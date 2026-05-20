/**
 * 连板游资（Ptrade 1:1 迁移）— 全市场扫描 composable。
 *
 * 来源策略：极速优化版游资连板策略。
 * 核心：今日触板股 + 三种形态（A 连板接力 / B 反包确认 / C 摸板试盘）+ 妖股基因（近 5 日 ≥2 板 + 接近 20 日新高）。
 *
 * 板块过滤：跟原策略一致，仅主板（60/00 开头），排除科创板（688）和北交所（4/8/920）。
 * detector 内部用 code + name 识别 ST 和涨停幅度（10% / 20% / 5%）。
 *
 * 跟 useDragonReturnScan / useRecentMarketScan 同套节流框架（useStockScanner）。
 * 模块级单例 state，跨 tab 切换不重扫；重启 app 后状态失。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { useStockScanner } from './useStockScanner'
import { detectLimitUpRelay, getStrategyOverrides } from './useTechIndicators'
import { useMarketEnv } from './useMarketEnv'

// ---------------- 板块过滤：仅主板，跟 Ptrade 原策略对齐 ----------------
// startsWith('60') | '00' 且不是 '688' / '4' / '8' / '920'
const MAIN_BOARD_PREFIXES = ['600', '601', '603', '605', '000', '001', '002', '003']

// ---------------- 共享 state ----------------
const scanning   = ref(false)
const lastScanAt = ref(null)
const scanned    = ref(0)
const total      = ref(0)
const currentCode = ref('')
const errors     = ref([])
const results    = ref([])
const lastError  = ref(null)

let _activeScanner = null

const _cachedKlineFetch = (code, timeframe = '日K') =>
    api.getStockKlineViaTdxCached(code, timeframe)

const progressPct = computed(() => {
    if (!total.value) return 0
    return Math.round(scanned.value / total.value * 100)
})

// ---------------- 排序 ----------------
// sortKey === 'default' 时跟 Ptrade 原策略对齐：height 降序 → boards5 降序
// 其它 key 取 row[key] 数值排序，null 永远沉底
const sortKey = ref('default')
const sortDir = ref('desc')   // 'asc' | 'desc'

const SORTABLE_KEYS = new Set(['default', 'height', 'boards5', 'lastClose', 'pctChange'])

function setSort(key) {
    if (!SORTABLE_KEYS.has(key)) return
    if (sortKey.value === key) {
        // 同列再点切换方向；再点回退到 default
        if (sortDir.value === 'desc') {
            sortDir.value = 'asc'
        } else {
            sortKey.value = 'default'
            sortDir.value = 'desc'
        }
    } else {
        sortKey.value = key
        sortDir.value = 'desc'    // 新列默认降序（金融场景一般想看"最大/最近"）
    }
}

const sortedResults = computed(() => {
    const arr = [...results.value]
    if (sortKey.value === 'default') {
        return arr.sort((a, b) => {
            if (b.height !== a.height) return b.height - a.height
            return b.boards5 - a.boards5
        })
    }
    const k = sortKey.value
    const dirSign = sortDir.value === 'desc' ? -1 : 1
    return arr.sort((a, b) => {
        const av = a[k], bv = b[k]
        // null/undefined 沉底，不分方向
        if (av == null && bv == null) return 0
        if (av == null) return 1
        if (bv == null) return -1
        return (av - bv) * dirSign
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

    // Week 1 Day 2：弱势 / 破位市 游资策略停用
    const me = useMarketEnv()
    await me.refresh(false)
    const regime = me.currentRegime.value
    const overrides = getStrategyOverrides('limitUpRelay', regime)
    if (overrides.disabled) {
        lastError.value = `大盘 ${me.env.value?.regimeLabel || '弱势'}，连板游资策略已自动暂停（赚钱效应消失，连板易炸）`
        return null
    }

    // targetCodes 模式：只扫指定的 code 子集（用于"刷新当前列表"场景）
    // 仍然走板块过滤，subset 中不符合主板规则的票会被滤掉
    let stocks
    if (Array.isArray(targetCodes) && targetCodes.length) {
        stocks = targetCodes
            .filter(s => s?.code && MAIN_BOARD_PREFIXES.some(p => s.code.startsWith(p)))
            .map(s => ({ code: s.code, name: s.name || '' }))
        if (!stocks.length) {
            lastError.value = '指定的 code 子集中没有主板票'
            return null
        }
    } else {
        const codeRes = await api.listAllAShareCodes()
        if (!codeRes?.ok || !Array.isArray(codeRes.data) || !codeRes.data.length) {
            lastError.value = codeRes?.error || '全市场代码列表拉取失败'
            return null
        }
        // 主板过滤（含 ST 也保留 — detector 内部按 5% 算涨停）
        stocks = codeRes.data
            .filter(s => MAIN_BOARD_PREFIXES.some(p => s.code.startsWith(p)))
            .map(s => ({ code: s.code, name: s.name || '' }))
        if (!stocks.length) {
            lastError.value = '主板过滤后无可扫描的票'
            return null
        }
    }

    const scanner = useStockScanner({
        excludeST: true,                       // ST 直接剔除（Ptrade 原版逻辑）
        minBars: 60,                           // 上市 < 60 个交易日的次新跳过
        fetchFn: _cachedKlineFetch,
        batchSize: 10,
        adaptiveGapThresholdMs: 250,
        // detectLimitUpRelay 最深回看 20 根（妖股基因近 20 日 HHV）
        verifyContinuity: { lookbackBars: 20, maxGapDays: 12 },
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
            const r = detectLimitUpRelay(klines, { code: stock.code, name: stock.name })
            if (!r) return null
            // 信号最后一根 K 的交易日（YYYY-MM-DD），加自选时 added_at 用它。
            // 日 K 的 time 已是 'YYYY-MM-DD' 字符串，无需 new Date 转换。
            const lastK = klines[klines.length - 1]
            const lastDate = lastK?.time ? String(lastK.time).slice(0, 10) : null
            collected.push({
                code: stock.code,
                name: stock.name || stock.code,
                lastDate,
                ...r,
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
        } catch (e) { console.warn('[limit-up-relay] 板块强度拉取失败', e) }

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

export function useLimitUpRelayScan() {
    return {
        scanning,
        scanned,
        total,
        currentCode,
        errors,
        results,
        sortedResults,
        sortKey,
        sortDir,
        setSort,
        lastScanAt,
        lastError,
        progressPct,
        isStale,
        scan,
        cancel,
        reset,
    }
}
