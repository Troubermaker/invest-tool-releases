/**
 * 近期发车（全市场）扫描 — 候选池"发现工具"。
 *
 * 跟 bt.runAll() 走同一套逻辑（useBacktest.backtestStock + useStockScanner 节流框架），
 * 但产出聚焦在"近期突破还在飞"的 trades（truncated === true）。
 *
 * 单例 state — 整个 app 共享一份扫描结果。切换候选池 tab 来回不会重扫。
 * 重启 app 后 state 失，下次进 tab 触发自动重新扫描。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { useStockScanner } from './useStockScanner'
import { backtestStock } from './useBacktest'
import { useMarketEnv } from './useMarketEnv'
import { getStrategyOverrides } from './useTechIndicators'

// ---------------- 板块过滤（跟 bt.runAll 默认值一致）----------------
const BOARD_PREFIXES = {
    sh_main: ['600', '601', '603', '605'],
    sz_main: ['000', '001', '003'],
    sme:     ['002'],
    star:    ['688'],
    gem:     ['300'],
}
const DEFAULT_BOARDS = ['sh_main', 'sz_main', 'sme']

// ---------------- 共享 state（模块级单例）----------------
const scanning   = ref(false)
const lastScanAt = ref(null)        // ISO 时间戳
const scanned    = ref(0)
const total      = ref(0)
const currentCode = ref('')
const errors     = ref([])
const trades     = ref([])          // 全部 trades，UI 再按时段过滤
const lastError  = ref(null)        // 整体扫描失败时的错误信息

let _activeScanner = null

// 后端持久化缓存的 fetch 函数（命中后毫秒级，跨重启留存）
const _cachedKlineFetch = (code, timeframe = '日K') =>
    api.getStockKlineViaTdxCached(code, timeframe)

const progressPct = computed(() => {
    if (!total.value) return 0
    return Math.round(scanned.value / total.value * 100)
})

// trades 里 "近期还在飞" 的 = truncated（持有期未跑完，因为数据末尾就是今天）
const openTrades = computed(() => trades.value.filter(t => t.truncated))

// 扫描数据的 K 线末尾日期 — 跟"今天"对比就知道数据是不是新鲜
// 任何 truncated trade 的 exitTime = klines[lastIdx].time = 数据末尾
// 没 truncated 就找全部 trades 里 exitTime 最大的（fallback）
const dataEndDate = computed(() => {
    const open = openTrades.value
    if (open.length && open[0].exitTime) return open[0].exitTime
    let max = null
    for (const t of trades.value) {
        if (!t.exitTime) continue
        if (!max || String(t.exitTime) > String(max)) max = t.exitTime
    }
    return max
})

// 是否 stale：
//   1. 跨日（lastScanAt 不在今天，半夜过 12 点也算）→ 必扫
//   2. 同日 1 小时未扫 → 重扫（避免反复扫但确保刷新合理）
const STALE_MS = 60 * 60 * 1000
const isStale = computed(() => {
    if (!lastScanAt.value) return true
    const last = new Date(lastScanAt.value)
    if (Number.isNaN(last.getTime())) return true
    const now = new Date()
    // 跨日 = stale（数据末尾不再是今天，"持有 1d"会误指更早信号）
    if (last.toDateString() !== now.toDateString()) return true
    // 同日内 1 小时阈值
    return now.getTime() - last.getTime() > STALE_MS
})

// ---------------- 扫描逻辑 ----------------

async function scan(targetCodes = null) {
    if (scanning.value) return null

    // Week 1 Day 2：先拉 regime，决定是否阻断 + 调整 detector 阈值
    const me = useMarketEnv()
    await me.refresh(false)
    const regime = me.currentRegime.value
    const overrides = getStrategyOverrides('threeStage', regime)
    if (overrides.disabled) {
        lastError.value = `大盘 ${me.env.value?.regimeLabel || '破位'}，主升突破策略已自动暂停（破位市追突破胜率 <30%）`
        return null
    }

    const allowedPrefixes = DEFAULT_BOARDS.flatMap(b => BOARD_PREFIXES[b] || [])
    let stocks
    if (Array.isArray(targetCodes) && targetCodes.length) {
        // targetCodes 模式：只扫指定的 code 子集（"刷新当前列表"用）
        stocks = targetCodes
            .filter(s => s?.code && allowedPrefixes.some(p => s.code.startsWith(p)))
            .map(s => ({ code: s.code, name: s.name || '' }))
        if (!stocks.length) {
            lastError.value = '指定的 code 子集中没有符合板块的票'
            return null
        }
    } else {
        // 1. 拉全市场 A 股代码列表
        const codeRes = await api.listAllAShareCodes()
        if (!codeRes?.ok || !Array.isArray(codeRes.data) || !codeRes.data.length) {
            lastError.value = codeRes?.error || '全市场代码列表拉取失败'
            return null
        }
        // 2. 板块过滤（沪深主板 + 中小板，排除科创/创业）
        stocks = codeRes.data
            .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
            .map(s => ({ code: s.code, name: s.name }))
        if (!stocks.length) {
            lastError.value = '板块过滤后无可扫描的票'
            return null
        }
    }

    // 3. 用 useStockScanner（带后端缓存 + 自适应节流）跑批量
    const scanner = useStockScanner({
        excludeST: true,
        minBars: 250,                          // ≈1 年（过滤次新股）
        fetchFn: _cachedKlineFetch,
        batchSize: 10,
        adaptiveGapThresholdMs: 250,           // 缓存命中场景跳过 gap
        // detectThreeStageLaunch 内部 trendLookback=250，最深回看到第 250 根
        // 这 250 根里有停牌空洞 → 蓄势均价/历史最低判定失真 → 必须 skip
        verifyContinuity: { lookbackBars: 250, maxGapDays: 12 },
    })
    _activeScanner = scanner
    scanning.value = true
    lastError.value = null
    trades.value = []
    errors.value = []
    scanned.value = 0
    total.value = stocks.length
    currentCode.value = ''

    // 实时透传 scanner 状态（非响应式 ref 桥接，简单 watcher）
    const stateBridge = setInterval(() => {
        scanned.value     = scanner.scanned.value
        currentCode.value = scanner.currentCode.value
        errors.value      = [...scanner.errors.value]
    }, 500)

    try {
        const collected = []
        await scanner.scan(stocks, async (stock, klines) => {
            // 周K 跳过：跟"下载今日 K"按钮的覆盖范围（日K only）严格对齐，避免冷拉 TDX
            // 代价：weeklyConfirmed 字段全 null，但实测在 SHAP top 20 外，对 ML 增益微弱
            // 想恢复周K 信号：跟下载器一起加周K 预热（未来 phase）
            const weeklyKlines = null

            // dumpFeatures=true 让 trades 自带 features，供下面 ML 批量预测
            // 透传 regime 覆盖参数到 detectOpts
            const detectOpts = { ...overrides }
            delete detectOpts.disabled  // 清理 disabled flag（不是 detector 参数）
            const { trades: stockTrades } = backtestStock(klines, { weeklyKlines, dumpFeatures: true, detectOpts })
            if (!stockTrades.length) return null
            // 信号扫描所基于的最后一根 K 的交易日（YYYY-MM-DD），加自选时 added_at 用它。
            // 日 K 的 time 已是 'YYYY-MM-DD' 字符串，无需 new Date 转换。
            const lastK = klines[klines.length - 1]
            const lastDate = lastK?.time ? String(lastK.time).slice(0, 10) : null
            for (const t of stockTrades) {
                collected.push({ code: stock.code, name: stock.name || stock.code, lastDate, ...t })
            }
            return { code: stock.code, count: stockTrades.length }
        })
        trades.value = collected
        lastScanAt.value = new Date().toISOString()

        // Week 1 Day 3-5：批量拉板块强度，附 sectorStrength 到 trades
        try {
            const codes = [...new Set(collected.map(t => t.code))]
            if (codes.length) {
                const r = await api.getBatchStockSectorStrengths(codes)
                if (r?.ok && r.data) {
                    for (const t of collected) {
                        const info = r.data[t.code]
                        if (info) {
                            t.sectorName  = info.best_sector_name
                            t.sectorScore = info.best_sector_score
                        }
                    }
                    // 同步注入到 features（ML 特征用）
                    for (const t of collected) {
                        if (t.features) {
                            t.features.sectorStrength = t.sectorScore ?? null
                            t.features.marketRegime   = regime ?? null
                        }
                    }
                    // mutate 原对象绕过 ref 的 Proxy，强制重新赋值触发 Vue 响应式
                    trades.value = [...collected]
                }
            }
        } catch (e) {
            console.warn('[recent-market] 板块强度拉取失败（不影响主流程）', e)
        }

        // P2 龙虎榜：按 s3Time 分组批量拉，附 LHB 到 trades + features
        try {
            const groups = {}
            for (const t of collected) {
                if (!t.s3Time) continue
                const dateKey = String(t.s3Time).slice(0, 10)
                groups[dateKey] = groups[dateKey] || new Set()
                groups[dateKey].add(t.code)
            }
            for (const [dateKey, codeSet] of Object.entries(groups)) {
                const r = await api.getBatchLhbFeatures([...codeSet], dateKey, 30)
                if (r?.ok && r.data) {
                    for (const t of collected) {
                        if (String(t.s3Time).slice(0, 10) !== dateKey) continue
                        const lhb = r.data[t.code]
                        if (lhb) {
                            t.lhbInWindow      = lhb.lhb_in_window
                            t.lhbCount         = lhb.lhb_count
                            t.lhbNetBuySum     = lhb.lhb_net_buy_sum
                            t.daysSinceLastLhb = lhb.days_since_last_lhb
                            if (t.features) {
                                t.features.lhbInWindow      = lhb.lhb_in_window
                                t.features.lhbCount         = lhb.lhb_count
                                t.features.lhbNetBuySum     = lhb.lhb_net_buy_sum
                                t.features.daysSinceLastLhb = lhb.days_since_last_lhb
                            }
                        }
                    }
                }
            }
            trades.value = [...collected]   // 触发 Vue 响应式（绕过 Proxy 的 mutation 不会自动通知）
        } catch (e) {
            console.warn('[recent-market] LHB 拉取失败（不影响主流程）', e)
        }

        // Phase 3 Step 2：批量 ML 预测，附 mlScore 到每个 trade（仅 truncated 即"还在飞"的）
        try {
            const openOnes = collected.filter(t => t.truncated && t.features)
            if (openOnes.length) {
                const r = await api.mlPredictScores(openOnes.map(t => t.features))
                if (r?.ok && Array.isArray(r.data)) {
                    for (let i = 0; i < openOnes.length; i++) {
                        const s = r.data[i]
                        if (typeof s === 'number') openOnes[i].mlScore = s
                    }
                    trades.value = [...collected]   // 触发响应式
                }
            }
        } catch (e) {
            console.warn('[recent-market] ML predict 失败（trades 无 mlScore，UI 降级）', e)
        }
    } catch (e) {
        lastError.value = String(e)
    } finally {
        clearInterval(stateBridge)
        scanning.value = false
        currentCode.value = ''
        _activeScanner = null
    }

    return {
        total:    stocks.length,
        scanned:  scanned.value,
        trades:   trades.value.length,
        openTrades: openTrades.value.length,
    }
}

function cancel() {
    if (_activeScanner?.scanning?.value) {
        _activeScanner.cancel()
    }
}

function reset() {
    trades.value = []
    errors.value = []
    lastScanAt.value = null
    lastError.value = null
}

// ---------------- 导出 ----------------

export function useRecentMarketScan() {
    return {
        // state（响应式）
        scanning,
        scanned,
        total,
        currentCode,
        errors,
        trades,
        openTrades,
        dataEndDate,
        lastScanAt,
        lastError,
        progressPct,
        isStale,
        // actions
        scan,
        cancel,
        reset,
    }
}
