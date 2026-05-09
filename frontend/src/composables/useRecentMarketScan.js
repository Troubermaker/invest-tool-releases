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

async function scan() {
    if (scanning.value) return null

    // 1. 拉全市场 A 股代码列表
    const codeRes = await api.listAllAShareCodes()
    if (!codeRes?.ok || !Array.isArray(codeRes.data) || !codeRes.data.length) {
        lastError.value = codeRes?.error || '全市场代码列表拉取失败'
        return null
    }

    // 2. 板块过滤（沪深主板 + 中小板，排除科创/创业）
    const allowedPrefixes = DEFAULT_BOARDS.flatMap(b => BOARD_PREFIXES[b] || [])
    const stocks = codeRes.data
        .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
        .map(s => ({ code: s.code, name: s.name }))

    if (!stocks.length) {
        lastError.value = '板块过滤后无可扫描的票'
        return null
    }

    // 3. 用 useStockScanner（带后端缓存 + 自适应节流）跑批量
    const scanner = useStockScanner({
        excludeST: true,
        minBars: 250,                          // ≈1 年（过滤次新股）
        fetchFn: _cachedKlineFetch,
        batchSize: 10,
        adaptiveGapThresholdMs: 250,           // 缓存命中场景跳过 gap
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
            // 顺手拉周 K（走缓存）— 算 weeklyConfirmed
            let weeklyKlines = null
            try {
                const wRes = await _cachedKlineFetch(stock.code, '周K')
                if (wRes?.ok && Array.isArray(wRes.data) && wRes.data.length >= 25) {
                    weeklyKlines = wRes.data
                }
            } catch { /* 周K 失败不阻塞 */ }

            const { trades: stockTrades } = backtestStock(klines, { weeklyKlines })
            if (!stockTrades.length) return null
            for (const t of stockTrades) {
                collected.push({ code: stock.code, name: stock.name || stock.code, ...t })
            }
            return { code: stock.code, count: stockTrades.length }
        })
        trades.value = collected
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
