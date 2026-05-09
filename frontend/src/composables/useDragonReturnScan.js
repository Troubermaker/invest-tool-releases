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
import { detectDragonReturn } from './useTechIndicators'

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

// 默认排序：涨停数多的优先 → 启动信号靠后（更新鲜）
const sortedResults = computed(() => {
    return [...results.value].sort((a, b) => {
        const lc = (b.limitUpCount ?? 0) - (a.limitUpCount ?? 0)
        if (lc !== 0) return lc
        return (b.ignitionIdx ?? 0) - (a.ignitionIdx ?? 0)
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

async function scan() {
    if (scanning.value) return null

    const codeRes = await api.listAllAShareCodes()
    if (!codeRes?.ok || !Array.isArray(codeRes.data) || !codeRes.data.length) {
        lastError.value = codeRes?.error || '全市场代码列表拉取失败'
        return null
    }

    const allowedPrefixes = DEFAULT_BOARDS.flatMap(b => BOARD_PREFIXES[b] || [])
    const stocks = codeRes.data
        .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
        .map(s => ({ code: s.code, name: s.name }))

    if (!stocks.length) {
        lastError.value = '板块过滤后无可扫描的票'
        return null
    }

    const scanner = useStockScanner({
        excludeST: true,
        minBars: 80,                          // 龙回头需要 60+ 蓄势 + 少量后续
        fetchFn: _cachedKlineFetch,
        batchSize: 10,
        adaptiveGapThresholdMs: 250,
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
            const dr = detectDragonReturn(klines)
            if (!dr) return null
            collected.push({
                code: stock.code,
                name: stock.name || stock.code,
                ...dr,
            })
            return { code: stock.code }
        })
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
