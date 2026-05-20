/**
 * 龙回头（全市场）扫描 — 候选池"发现工具"。
 *
 * 三段式判定（detectDragonReturn）：
 *   1. 龙   — 60 日内 ≥3 涨停
 *   2. 回头 — 高点回踩 30-50% + 近 5 根振幅收敛
 *   3. 启动 — 近 3 根至少一根量比≥1.3 收阳
 *
 * 跟 useRecentMarketScan 同套节流框架（useStockScanner + 后端缓存 fetch + 自适应 gap），
 * 单例 state — 整个 app 共享一份扫描结果。重启 app 后状态失，下次进 tab 触发自动扫描。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { useStockScanner } from './useStockScanner'
import { detectDragonReturn, getStrategyOverrides } from './useTechIndicators'
import { useMarketEnv } from './useMarketEnv'

// ---------------- 板块过滤（跟 useRecentMarketScan 默认值一致）----------------
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
const lastScanAt = ref(null)
const scanned    = ref(0)
const total      = ref(0)
const currentCode = ref('')
const errors     = ref([])
const results    = ref([])    // 全部命中的票
const lastError  = ref(null)

let _activeScanner = null

const _cachedKlineFetch = (code, timeframe = '日K') =>
    api.getStockKlineViaTdxCached(code, timeframe)

const progressPct = computed(() => {
    if (!total.value) return 0
    return Math.round(scanned.value / total.value * 100)
})

// 排序：星级强排序 → 综合评分
const sortedResults = computed(() => {
    return [...results.value].sort((a, b) => {
        const aStar = a.starLevel ?? 0
        const bStar = b.starLevel ?? 0
        if (aStar !== bStar) return bStar - aStar
        const score = r => {
            const pq  = r.pullbackQuality ?? 0
            const pb  = r.probeOk ? 100 : 0
            const ps  = r.positionOk ? 100 : 0
            const lu  = Math.min((r.limitUpCount ?? 0) * 20, 100)
            const sec = r.sectorScore ?? 0
            return pq * 0.40 + pb * 0.20 + ps * 0.15 + lu * 0.15 + sec * 0.10
        }
        return score(b) - score(a)
    })
})

const STALE_MS = 60 * 60 * 1000
const isStale = computed(() => {
    if (!lastScanAt.value) return true
    const last = new Date(lastScanAt.value)
    if (Number.isNaN(last.getTime())) return true
    const now = new Date()
    if (last.toDateString() !== now.toDateString()) return true
    return now.getTime() - last.getTime() > STALE_MS
})

// ---------------- 扫描逻辑 ----------------

async function scan(targetCodes = null) {
    if (scanning.value) return null

    // Week 1 Day 2：regime 阻断 + 调阈
    const me = useMarketEnv()
    await me.refresh(false)
    const regime = me.currentRegime.value
    const overrides = getStrategyOverrides('dragonReturn', regime)
    if (overrides.disabled) {
        lastError.value = `大盘 ${me.env.value?.regimeLabel || '破位'}，龙回头策略已自动暂停（破位市妖股复活率极低）`
        return null
    }

    const allowedPrefixes = DEFAULT_BOARDS.flatMap(b => BOARD_PREFIXES[b] || [])
    let stocks
    if (Array.isArray(targetCodes) && targetCodes.length) {
        stocks = targetCodes
            .filter(s => s?.code && allowedPrefixes.some(p => s.code.startsWith(p)))
            .map(s => ({ code: s.code, name: s.name || '' }))
        if (!stocks.length) {
            lastError.value = '指定的 code 子集中没有符合板块的票'
            return null
        }
    } else {
        const codeRes = await api.listAllAShareCodes()
        if (!codeRes?.ok || !Array.isArray(codeRes.data) || !codeRes.data.length) {
            lastError.value = codeRes?.error || '全市场代码列表拉取失败'
            return null
        }
        stocks = codeRes.data
            .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
            .map(s => ({ code: s.code, name: s.name }))
        if (!stocks.length) {
            lastError.value = '板块过滤后无可扫描的票'
            return null
        }
    }

    const scanner = useStockScanner({
        excludeST: true,
        minBars: 80,                          // 龙回头需要 60+ 蓄势 + 少量后续
        fetchFn: _cachedKlineFetch,
        batchSize: 10,
        adaptiveGapThresholdMs: 250,
        // detectDragonReturn 最深回看 30 根（peakMaxBarsAgo+clusterWindow）
        verifyContinuity: { lookbackBars: 30, maxGapDays: 12 },
    })
    _activeScanner = scanner
    scanning.value = true
    lastError.value = null
    results.value = []
    errors.value = []
    scanned.value = 0
    total.value = stocks.length
    currentCode.value = ''

    const stateBridge = setInterval(() => {
        scanned.value     = scanner.scanned.value
        currentCode.value = scanner.currentCode.value
        errors.value      = [...scanner.errors.value]
    }, 500)

    try {
        const collected = []
        await scanner.scan(stocks, async (stock, klines) => {
            // 透传 regime 调阈（signalVolRatio / pullbackMax 等）
            // requirePositionOk 默认开（弱势市强制；其他档由 regime 调）
            const detectOpts = { requirePositionOk: regime === 'weak', ...overrides }
            delete detectOpts.disabled
            const dr = detectDragonReturn(klines, detectOpts)
            if (!dr) return null
            // 信号成立时最后一根 K 的交易日（YYYY-MM-DD）。加自选时 added_at 用它，
            // 表示"基于该交易日 K 线发现的信号"，而不是用户点按钮的当下。
            // 注意：日 K 的 time 是 'YYYY-MM-DD' 字符串（kline_service.py 直传 CSV parts[0]），
            // 不是 epoch 秒。这里只取前 10 位即可，无需 new Date()。
            const lastK = klines[klines.length - 1]
            const lastDate = lastK?.time ? String(lastK.time).slice(0, 10) : null
            collected.push({
                code: stock.code,
                name: stock.name || stock.code,
                lastDate,
                ...dr,
            })
            return { code: stock.code }
        })
        // Week 1 Day 3-5：拉板块强度
        try {
            const codes = [...new Set(collected.map(r => r.code))]
            if (codes.length) {
                const r = await api.getBatchStockSectorStrengths(codes)
                if (r?.ok && r.data) {
                    for (const row of collected) {
                        const info = r.data[row.code]
                        if (info) {
                            row.sectorName  = info.best_sector_name
                            row.sectorScore = info.best_sector_score
                        }
                    }
                }
            }
        } catch (e) { console.warn('[dragon-return] 板块强度拉取失败', e) }

        // P2 龙虎榜：以 ignitionTime 为时点查（防未来泄漏）
        try {
            const groups = {}
            for (const r of collected) {
                const dateKey = r.ignitionTime ? String(r.ignitionTime).slice(0, 10) : new Date().toISOString().slice(0, 10)
                groups[dateKey] = groups[dateKey] || new Set()
                groups[dateKey].add(r.code)
            }
            for (const [dateKey, codeSet] of Object.entries(groups)) {
                const r = await api.getBatchLhbFeatures([...codeSet], dateKey, 30)
                if (r?.ok && r.data) {
                    for (const row of collected) {
                        const rowDate = row.ignitionTime ? String(row.ignitionTime).slice(0, 10) : new Date().toISOString().slice(0, 10)
                        if (rowDate !== dateKey) continue
                        const lhb = r.data[row.code]
                        if (lhb) {
                            row.lhbInWindow  = lhb.lhb_in_window
                            row.lhbCount     = lhb.lhb_count
                            row.lhbNetBuySum = lhb.lhb_net_buy_sum
                        }
                    }
                }
            }
        } catch (e) { console.warn('[dragon-return] LHB 拉取失败', e) }

        // 龙回头星级（已是 stage-3-like 信号，规则比突破前夜更严）：
        // ⭐⭐⭐⭐: positionOk + probeOk + pullbackQuality ≥ 70（黄金组合）
        // ⭐⭐⭐:  positionOk + (probeOk OR pullbackQuality ≥ 70)
        // ⭐⭐:   pullbackQuality ≥ 70 OR (probeOk AND sector ≥ 60)
        // ⭐:    positionOk OR sector ≥ 70 OR LHB
        for (const row of collected) {
            const pos = row.positionOk === true
            const probe = row.probeOk === true
            const pq = row.pullbackQuality ?? 0
            const sec = row.sectorScore ?? 0
            const lhb = row.lhbInWindow === 1
            let star = 0
            if (pos && probe && pq >= 70)                              star = 4
            else if (pos && (probe || pq >= 70))                       star = 3
            else if (pq >= 70 || (probe && sec >= 60))                 star = 2
            else if (pos || sec >= 70 || lhb)                          star = 1
            row.starLevel = star
        }

        results.value = collected
        lastScanAt.value = new Date().toISOString()
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
        hits:     results.value.length,
    }
}

function cancel() {
    if (_activeScanner?.scanning?.value) {
        _activeScanner.cancel()
    }
}

function reset() {
    results.value = []
    errors.value = []
    lastScanAt.value = null
    lastError.value = null
}

export function useDragonReturnScan() {
    return {
        scanning,
        scanned,
        total,
        currentCode,
        errors,
        results,
        sortedResults,
        lastScanAt,
        lastError,
        progressPct,
        isStale,
        scan,
        cancel,
        reset,
    }
}
