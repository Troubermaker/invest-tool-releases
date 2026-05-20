/**
 * 突破前夜（全市场）扫描 —— Stage 1/2 蓄势态批量发现。
 *
 * 对照「主升突破」（useRecentMarketScan）抓 Stage 3 已突破，本 composable 抓"还没突破"的票：
 *   - Stage 1 蓄势中（横盘 + MA 粘合 + 地量）
 *   - Stage 2 试盘后（长上影/下影 + 放量，等突破信号）
 *
 * 用途：周末复盘建观察池，设价格预警在 break_level，让被动等候突破触发。
 *
 * 单例 state — 整个 app 共享一份结果。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { useStockScanner } from './useStockScanner'
import { detectThreeStageLaunch, getStrategyOverrides } from './useTechIndicators'
import { useMarketEnv } from './useMarketEnv'

const BOARD_PREFIXES = {
    sh_main: ['600', '601', '603', '605'],
    sz_main: ['000', '001', '003'],
    sme:     ['002'],
    star:    ['688'],
    gem:     ['300'],
}
const DEFAULT_BOARDS = ['sh_main', 'sz_main', 'sme']

// ---------------- 共享 state ----------------
const scanning   = ref(false)
const lastScanAt = ref(null)
const scanned    = ref(0)
const total      = ref(0)
const currentCode = ref('')
const errors     = ref([])
const results    = ref([])     // [{ code, name, event, lastPrice, distanceToBreakPct, distanceToGoldenPct, consolidationBars }]
const lastError  = ref(null)

let _activeScanner = null

const _cachedKlineFetch = (code, timeframe = '日K') =>
    api.getStockKlineViaTdxCached(code, timeframe)

const progressPct = computed(() => {
    if (!total.value) return 0
    return Math.round(scanned.value / total.value * 100)
})

// 默认排序：星级强排序 → Stage 优先 → 综合评分
const sortedResults = computed(() =>
    [...results.value].sort((a, b) => {
        // 一档：星级
        const aStar = a.starLevel ?? 0
        const bStar = b.starLevel ?? 0
        if (aStar !== bStar) return bStar - aStar
        // 二档：Stage 2 优先（更接近发车）
        if (a.event.currentStage !== b.event.currentStage) {
            return b.event.currentStage - a.event.currentStage
        }
        // 三档：综合评分（quality + MACD + dist proxy）
        const score = r => {
            const q = r.qualityScore ?? 0
            const m = r.macdImminence ?? 0
            const dist = r.distanceToBreakPct == null ? 5 : Math.abs(r.distanceToBreakPct)
            const proxy = Math.max(0, (5 - dist) / 5) * 100
            return q * 0.50 + m * 0.30 + proxy * 0.20
        }
        return score(b) - score(a)
    }),
)

// 同日 1 小时阈值（蓄势态变化慢，可以放宽）
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

    // Week 1 Day 2：regime 调阈（破位市反而是埋伏机会，不阻断）
    const me = useMarketEnv()
    await me.refresh(false)
    const regime = me.currentRegime.value
    const overrides = getStrategyOverrides('breakoutEve', regime)
    // 距突破位的允许范围（regime 自适应）
    const maxDistPct = overrides.maxDistanceToBreakPct ?? 5

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
        minBars: 250,
        fetchFn: _cachedKlineFetch,
        batchSize: 10,
        adaptiveGapThresholdMs: 250,
        verifyContinuity: { lookbackBars: 250, maxGapDays: 12 },
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
            const events = detectThreeStageLaunch(klines)
            // 只关心还没突破：Stage 1（蓄势中）/ Stage 2（试盘后）
            const candidates = events.filter(e => e.currentStage === 1 || e.currentStage === 2)
            if (candidates.length === 0) return null

            // 取最新的蓄势段（s1End 最大）
            const evt = candidates.sort((a, b) => (b.s1EndIdx || 0) - (a.s1EndIdx || 0))[0]
            const lastClose = +klines[klines.length - 1].close
            const breakLevel = evt.s1Upper
            const distanceToBreakPct = breakLevel
                ? (lastClose - breakLevel) / breakLevel * 100
                : null
            const consolidationBars = evt.s1EndIdx - evt.s1StartIdx + 1

            // 有效性过滤：
            // 1) 距突破 > maxDistPct% → 远古逃逸突破（regime 自适应：强势 8% / 默认 5% / 弱势 3%）
            // 2) 蓄势 > 80 根 → 长期卡住的票
            if (distanceToBreakPct != null && distanceToBreakPct > maxDistPct) return null
            if (consolidationBars > 80) return null

            const distanceToGoldenPct = evt.goldenBuyPrice
                ? (lastClose - evt.goldenBuyPrice) / evt.goldenBuyPrice * 100
                : null

            // 信号最后一根 K 的交易日（YYYY-MM-DD），加自选时 added_at 用它。
            // 日 K 的 time 已是 'YYYY-MM-DD' 字符串，无需 new Date 转换。
            const lastK = klines[klines.length - 1]
            const lastDate = lastK?.time ? String(lastK.time).slice(0, 10) : null

            const row = {
                code: stock.code,
                name: stock.name || stock.code,
                event: evt,
                lastPrice: lastClose,
                lastDate,
                distanceToBreakPct,
                distanceToGoldenPct,
                consolidationBars,
                // Week 3 新增：蓄势质量评分 + MACD 金叉预兆
                qualityScore: evt.consolidationQuality ?? null,
                qualityBreakdown: evt.consolidationBreakdown ?? null,
                macdImminence: evt.macdImminence ?? 0,
            }
            collected.push(row)
            return { code: stock.code }
        })
        // Week 1 Day 3-5：拉板块强度附加
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
        } catch (e) {
            console.warn('[breakout-eve] 板块强度拉取失败', e)
        }

        // P2 龙虎榜：按当前时点拉（突破前夜信号是"现在"的，不是历史）
        try {
            const codes = [...new Set(collected.map(r => r.code))]
            if (codes.length) {
                const today = new Date().toISOString().slice(0, 10)
                const r = await api.getBatchLhbFeatures(codes, today, 30)
                if (r?.ok && r.data) {
                    for (const row of collected) {
                        const lhb = r.data[row.code]
                        if (lhb) {
                            row.lhbInWindow  = lhb.lhb_in_window
                            row.lhbCount     = lhb.lhb_count
                            row.lhbNetBuySum = lhb.lhb_net_buy_sum
                        }
                    }
                }
            }
        } catch (e) {
            console.warn('[breakout-eve] LHB 拉取失败', e)
        }

        // Week-4：突破前夜星级计算（蓄势态 stage 1/2，没有 N+1 confirm，用 quality + MACD + sector + LHB）
        // ⭐⭐⭐⭐: quality ≥ 70 AND MACD ≥ 50 AND (sector ≥ 60 OR LHB)
        // ⭐⭐⭐:  quality ≥ 70 OR (MACD ≥ 50 AND sector ≥ 60)
        // ⭐⭐:   quality ≥ 50 + 距突破 ≤ 3%（临门一脚）
        // ⭐:    sector ≥ 70 / MACD ≥ 50 / LHB / quality ≥ 50
        for (const row of collected) {
            const q   = row.qualityScore ?? 0
            const m   = row.macdImminence ?? 0
            const sec = row.sectorScore ?? 0
            const lhb = row.lhbInWindow === 1
            const dist = row.distanceToBreakPct == null ? 99 : Math.abs(row.distanceToBreakPct)
            let star = 0
            if (q >= 70 && m >= 50 && (sec >= 60 || lhb))                 star = 4
            else if (q >= 70 || (m >= 50 && sec >= 60))                    star = 3
            else if (q >= 50 && dist <= 3)                                  star = 2
            else if (sec >= 70 || m >= 50 || lhb || q >= 50)                star = 1
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
        results:  results.value.length,
    }
}

function cancel() {
    if (_activeScanner?.scanning?.value) _activeScanner.cancel()
}

function reset() {
    results.value = []
    errors.value = []
    lastScanAt.value = null
    lastError.value = null
}

export function useBreakoutEveScan() {
    return {
        scanning, lastScanAt, scanned, total, currentCode, errors, results, lastError,
        sortedResults, progressPct, isStale,
        scan, cancel, reset,
    }
}
