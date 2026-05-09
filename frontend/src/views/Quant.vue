<script setup>
/**
 * 量化选股 View —— 全市场扫描发现工具集合。
 *
 * 跟「候选池」的区别：
 *   - 候选池：用户已收藏的票，做持续追踪（snapshot 永久不变）
 *   - 量化选股：扫描器跑出的临时结果，用户挑选后通过 ☆ 收藏进候选池
 *
 * 三个扫描器同居一个 view（顶部 tab 切换）：
 *   1. 近期发车   useRecentMarketScan   detectThreeStageLaunch + backtestStock
 *   2. 龙回头     useDragonReturnScan   detectDragonReturn
 *   3. 连板游资   useLimitUpRelayScan   detectLimitUpRelay (Ptrade 1:1)
 */
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client'
import { openStockChart } from '../composables/useStockChart'
import { openAddToWatchlist, openAddToWatchlistBatch } from '../composables/useAddToWatchlist'
import { pushSuccess, pushError } from '../composables/useNotifications'
import { confirmDialog } from '../composables/useConfirm'
import { useRecentMarketScan } from '../composables/useRecentMarketScan'
import { useDragonReturnScan } from '../composables/useDragonReturnScan'
import { useLimitUpRelayScan } from '../composables/useLimitUpRelayScan'
import { useKlineDownloader } from '../composables/useKlineDownloader'

// ---------------- 顶部 tab：3 个扫描器 ----------------
const scanTab = ref('recent_market')   // 'recent_market' | 'dragon_return' | 'limit_up_relay'

// ---------------- 候选池 / 自选 codes（用于"已收/已自"徽章 + isFullyAdded）----------------
const candidatePoolCodes = ref(new Set())
const watchlistCodes = ref(new Set())

async function loadCandidatePoolCodes() {
    try {
        const res = await api.listCandidatePicks()
        if (res?.ok && Array.isArray(res.data)) {
            candidatePoolCodes.value = new Set(res.data.map(p => p.code))
        }
    } catch { /* 静默 */ }
}

async function loadWatchlistCodes() {
    try {
        const res = await api.getAllWatchlistStocks()
        if (res?.ok && Array.isArray(res.data)) {
            watchlistCodes.value = new Set(res.data.map(s => s.code))
        }
    } catch { /* 静默 */ }
}

function isInCandidatePool(code) { return candidatePoolCodes.value.has(code) }
function isInWatchlist(code)     { return watchlistCodes.value.has(code) }
function isFullyAdded(code)      { return isInCandidatePool(code) && isInWatchlist(code) }

// ---------------- 工具函数 ----------------
function fmtPct(v, d = 2) {
    if (v == null) return '—'
    return (v >= 0 ? '+' : '') + v.toFixed(d) + '%'
}
function colorOf(v) {
    if (v == null) return 'text-[#aaa]'
    if (v > 0) return 'text-[#dc2626]'
    if (v < 0) return 'text-[#059669]'
    return 'text-[#666]'
}
function fmtMarketDate(t) {
    if (!t) return '—'
    if (typeof t === 'string') return t.slice(0, 10).replaceAll('-', '/')
    if (typeof t === 'object' && t.year) {
        const mm = String(t.month).padStart(2, '0')
        const dd = String(t.day).padStart(2, '0')
        return `${t.year}/${mm}/${dd}`
    }
    return '—'
}
function gradeChipCls(g) {
    return ({
        B: 'bg-[#dc2626] text-white',
        S: 'bg-[#7c3aed] text-white',
        A: 'bg-[#e5e7eb] text-[#666]',
        C: 'bg-[#e5e7eb] text-[#666]',
    }[g] || 'bg-[#cbd5e1] text-[#475569]')
}

// ============================================================
// 近期发车（全市场扫描）
// ============================================================
const market = useRecentMarketScan()
const downloader = useKlineDownloader()

const cacheStatus = ref(null)
const cacheStatusChecking = ref(false)
const A_SHARE_PFX = ['600', '601', '603', '605', '000', '001', '003', '002']

async function refreshCacheStatus() {
    if (cacheStatusChecking.value) return
    cacheStatusChecking.value = true
    try {
        const codeRes = await api.listAllAShareCodes()
        if (!codeRes?.ok || !Array.isArray(codeRes.data)) return
        const codes = codeRes.data
            .filter(s => A_SHARE_PFX.some(p => s.code.startsWith(p)))
            .map(s => s.code)
        const checkRes = await api.bulkCheckKlineFreshness(codes, '日K')
        if (checkRes?.ok && checkRes.data) {
            cacheStatus.value = {
                target_date:    checkRes.data.target_date,
                today:          checkRes.data.today,
                total:          checkRes.data.total,
                cachedCount:    (checkRes.data.cached     || []).length,
                missingCount:   (checkRes.data.missing    || []).length,
                partialCount:   (checkRes.data.partial    || []).length,
                newStockCount:  (checkRes.data.new_stocks || []).length,
            }
        }
    } catch (e) {
        console.warn('[cache-status] 拉取失败', e)
    } finally {
        cacheStatusChecking.value = false
    }
}

const shouldPromptDownload = computed(() => {
    if (!cacheStatus.value) return false
    const s = cacheStatus.value
    if (!s.missingCount && !s.partialCount) return false
    const now = new Date()
    const wd = now.getDay()
    if (wd === 0 || wd === 6) return false
    const m = now.getHours() * 60 + now.getMinutes()
    if (m < 15 * 60 + 30) return false
    return true
})

const cacheStatusHint = computed(() => {
    if (!cacheStatus.value) return ''
    const s = cacheStatus.value
    const targetFmt = fmtMarketDate(s.target_date)
    const validTotal = s.total - (s.newStockCount || 0)
    const head = `${s.cachedCount}/${validTotal} 已是 ${targetFmt}`
    if (!s.missingCount && !s.partialCount) return head + (s.newStockCount ? ` · 跳过新股 ${s.newStockCount}` : '')
    let msg = head
    if (s.missingCount)  msg += ` · 缺 ${s.missingCount}`
    if (s.partialCount)  msg += ` · 不完整 ${s.partialCount}`
    if (s.newStockCount) msg += ` · 跳过新股 ${s.newStockCount}`
    return msg
})

function isAShareTradingHours(now = new Date()) {
    const wd = now.getDay()
    if (wd === 0 || wd === 6) return false
    const m = now.getHours() * 60 + now.getMinutes()
    if (m >= 9 * 60 + 30 && m <= 11 * 60 + 30) return true
    if (m >= 13 * 60     && m <= 15 * 60)      return true
    return false
}

async function downloadTodayKlines() {
    if (isAShareTradingHours()) {
        const ok = await confirmDialog({
            title:       '盘中下载提示',
            message:     'A 股交易时段（盘中）下载会拿到今日未收盘的不完整 K 线，\n通常建议 15:00 收盘后再来。\n\n仍要继续？',
            confirmText: '继续下载',
            danger:      true,
        })
        if (!ok) return
    }
    const codeRes = await api.listAllAShareCodes()
    if (!codeRes?.ok || !Array.isArray(codeRes.data) || !codeRes.data.length) {
        pushError('全市场代码列表拉取失败')
        return
    }
    const PFX = A_SHARE_PFX
    const codes = codeRes.data
        .filter(s => PFX.some(p => s.code.startsWith(p)))
        .map(s => s.code)
    const check = await downloader.checkFreshness(codes)
    if (!check) {
        pushError(downloader.error.value || '预检失败')
        return
    }
    const missingCnt = (check.missing || []).length
    const partialCnt = (check.partial || []).length
    const totalToFetch = missingCnt + partialCnt
    if (!totalToFetch) {
        pushSuccess(`全部已是最新（${check.target_date}）· 共 ${check.cached.length} 只`)
        downloader.reset()
        return
    }
    const newStockCnt = (check.new_stocks || []).length
    let detail = `已缓存 ${check.cached.length} 只`
    if (missingCnt)  detail += `\n缺失 ${missingCnt} 只`
    if (partialCnt)  detail += `\n不完整 ${partialCnt} 只（盘中下载残留，需补全）`
    if (newStockCnt) detail += `\n跳过新股 ${newStockCnt} 只（上市 < 60 个交易日）`
    const estMin = Math.ceil(totalToFetch * 0.85 / 60)
    const ok = await confirmDialog({
        title: '下载今日 K 线',
        message: `数据末尾 ${check.target_date}\n${detail}\n共需下载 ${totalToFetch} 只，约 ${estMin} 分钟。\n\n开始下载？`,
        confirmText: '开始下载',
    })
    if (!ok) {
        downloader.reset()
        return
    }
    const result = await downloader.download()
    if (result) {
        let msg = `下载完成 · 成功 ${result.downloaded} / ${result.total}`
        if (result.failed) msg += ` · 失败 ${result.failed}`
        pushSuccess(msg)
        refreshCacheStatus()
    }
}

const RECENT_DAYS_OPTIONS = [1, 3, 5, 7]
const recentDays = ref(3)

const recentMarketRows = computed(() => {
    return market.openTrades.value
        .filter(t => (t.holdBars ?? 0) <= recentDays.value)
        .sort((a, b) => {
            const aw = a.weeklyConfirmed === true ? 0 : a.weeklyConfirmed === false ? 2 : 1
            const bw = b.weeklyConfirmed === true ? 0 : b.weeklyConfirmed === false ? 2 : 1
            if (aw !== bw) return aw - bw
            const gRank = { B: 0, A: 1, C: 2, S: 3 }
            const ar = gRank[a.grade] ?? 4
            const br = gRank[b.grade] ?? 4
            if (ar !== br) return ar - br
            return (b.returnPct ?? -Infinity) - (a.returnPct ?? -Infinity)
        })
})

const lastScanHint = computed(() => {
    if (!market.lastScanAt.value) return '从未扫描'
    const ms = new Date(market.lastScanAt.value).getTime()
    const ageMin = Math.round((Date.now() - ms) / 60000)
    if (ageMin < 1) return '刚刚'
    if (ageMin < 60) return `${ageMin} 分钟前`
    return `${Math.round(ageMin / 60)} 小时前`
})

const marketSelectedCodes = ref(new Set())
const marketBatchProcessing = ref(false)

function toggleMarketRow(code, e) {
    e?.stopPropagation()
    if (isFullyAdded(code)) return
    const next = new Set(marketSelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    marketSelectedCodes.value = next
}

const allMarketSelectableSelected = computed(() => {
    const selectable = recentMarketRows.value.filter(r => !isFullyAdded(r.code))
    if (!selectable.length) return false
    return selectable.every(r => marketSelectedCodes.value.has(r.code))
})

function toggleAllMarket() {
    const selectable = recentMarketRows.value.filter(r => !isFullyAdded(r.code))
    if (allMarketSelectableSelected.value) {
        const next = new Set(marketSelectedCodes.value)
        for (const r of selectable) next.delete(r.code)
        marketSelectedCodes.value = next
    } else {
        const next = new Set(marketSelectedCodes.value)
        for (const r of selectable) next.add(r.code)
        marketSelectedCodes.value = next
    }
}

async function batchSaveMarketToCandidatePool() {
    if (marketBatchProcessing.value || !marketSelectedCodes.value.size) return
    const targets = recentMarketRows.value.filter(
        r => marketSelectedCodes.value.has(r.code) && !isInCandidatePool(r.code),
    )
    if (!targets.length) {
        pushSuccess('选中的票都已在候选池里了')
        return
    }
    marketBatchProcessing.value = true
    let okCount = 0, failCount = 0
    try {
        for (const r of targets) {
            try {
                const res = await api.addCandidatePick({
                    code: r.code, name: r.name, stage: 3,
                    save_price: r.entryPrice ?? r.exitPrice,
                    break_level: r.breakoutPrice,
                    golden_price: r.goldenBuyPrice,
                    s1_lower: null, consolidation_bars: null,
                    source: '近期发车追踪',
                })
                if (res.ok) okCount++; else failCount++
            } catch { failCount++ }
        }
        marketSelectedCodes.value = new Set()
        let msg = `已批量收藏 ${okCount} 只到候选池`
        if (failCount) msg += `，${failCount} 只失败`
        pushSuccess(msg)
        await loadCandidatePoolCodes()
    } finally {
        marketBatchProcessing.value = false
    }
}

function batchAddMarketToWatchlist() {
    if (!marketSelectedCodes.value.size) return
    const targets = recentMarketRows.value
        .filter(r => marketSelectedCodes.value.has(r.code) && !isInWatchlist(r.code))
        .map(r => ({ code: r.code, name: r.name, price: r.entryPrice ?? r.exitPrice }))
    if (!targets.length) {
        pushSuccess('选中的票都已在自选了')
        return
    }
    openAddToWatchlistBatch(targets)
    setTimeout(loadWatchlistCodes, 1500)
}

function addMarketRowToWatchlist(row, e) {
    e?.stopPropagation()
    if (isInWatchlist(row.code)) return
    openAddToWatchlist(row.code, row.name, row.entryPrice ?? row.exitPrice)
    setTimeout(loadWatchlistCodes, 1500)
}

async function saveMarketRowToCandidatePool(row, e) {
    e?.stopPropagation()
    if (isInCandidatePool(row.code)) return
    try {
        const res = await api.addCandidatePick({
            code: row.code, name: row.name, stage: 3,
            save_price: row.entryPrice ?? row.exitPrice,
            break_level: row.breakoutPrice,
            golden_price: row.goldenBuyPrice,
            s1_lower: null, consolidation_bars: null,
            source: '近期发车追踪',
        })
        if (res.ok) {
            pushSuccess(`已收藏 ${row.name}（${row.code}）→ 候选池`)
            await loadCandidatePoolCodes()
        } else {
            pushError(res.error || '收藏失败')
        }
    } catch (err) {
        pushError(`收藏失败：${err}`)
    }
}

function viewMarketChart(row) {
    const list = recentMarketRows.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(row.code, row.name, list)
}

// ============================================================
// 龙回头（全市场扫描）
// ============================================================
const dragon = useDragonReturnScan()
const dragonSelectedCodes = ref(new Set())
const dragonBatchProcessing = ref(false)

const dragonLastScanHint = computed(() => {
    if (!dragon.lastScanAt.value) return '从未扫描'
    const ms = new Date(dragon.lastScanAt.value).getTime()
    const ageMin = Math.round((Date.now() - ms) / 60000)
    if (ageMin < 1) return '刚刚'
    if (ageMin < 60) return `${ageMin} 分钟前`
    return `${Math.round(ageMin / 60)} 小时前`
})

function toggleDragonRow(code, e) {
    e?.stopPropagation()
    if (isFullyAdded(code)) return
    const next = new Set(dragonSelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    dragonSelectedCodes.value = next
}

const allDragonSelectableSelected = computed(() => {
    const selectable = dragon.sortedResults.value.filter(r => !isFullyAdded(r.code))
    if (!selectable.length) return false
    return selectable.every(r => dragonSelectedCodes.value.has(r.code))
})

function toggleAllDragon() {
    const selectable = dragon.sortedResults.value.filter(r => !isFullyAdded(r.code))
    const next = new Set(dragonSelectedCodes.value)
    if (allDragonSelectableSelected.value) {
        for (const r of selectable) next.delete(r.code)
    } else {
        for (const r of selectable) next.add(r.code)
    }
    dragonSelectedCodes.value = next
}

function viewDragonChart(row) {
    const list = dragon.sortedResults.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(row.code, row.name, list)
}

async function saveDragonRowToCandidatePool(row, e) {
    e?.stopPropagation()
    if (isInCandidatePool(row.code)) return
    try {
        const res = await api.addCandidatePick({
            code: row.code, name: row.name, stage: 3,
            save_price: row.suggestedEntry ?? row.lastClose,
            break_level: row.peakHigh,
            golden_price: row.suggestedEntry,
            s1_lower: row.stopLoss,
            consolidation_bars: null,
            source: '龙回头',
        })
        if (res.ok) {
            pushSuccess(`已收藏 ${row.name}（${row.code}）→ 候选池`)
            await loadCandidatePoolCodes()
        } else {
            pushError(res.error || '收藏失败')
        }
    } catch (err) {
        pushError(`收藏失败：${err}`)
    }
}

function addDragonRowToWatchlist(row, e) {
    e?.stopPropagation()
    if (isInWatchlist(row.code)) return
    openAddToWatchlist(row.code, row.name, row.suggestedEntry ?? row.lastClose)
    setTimeout(loadWatchlistCodes, 1500)
}

async function batchSaveDragonToCandidatePool() {
    if (dragonBatchProcessing.value || !dragonSelectedCodes.value.size) return
    const targets = dragon.sortedResults.value.filter(
        r => dragonSelectedCodes.value.has(r.code) && !isInCandidatePool(r.code),
    )
    if (!targets.length) {
        pushSuccess('选中的票都已经在候选池里了')
        return
    }
    dragonBatchProcessing.value = true
    let success = 0, fail = 0
    for (const r of targets) {
        try {
            const res = await api.addCandidatePick({
                code: r.code, name: r.name, stage: 3,
                save_price: r.suggestedEntry ?? r.lastClose,
                break_level: r.peakHigh,
                golden_price: r.suggestedEntry,
                s1_lower: r.stopLoss,
                consolidation_bars: null,
                source: '龙回头',
            })
            if (res.ok) success++; else fail++
        } catch { fail++ }
    }
    dragonBatchProcessing.value = false
    dragonSelectedCodes.value = new Set()
    await loadCandidatePoolCodes()
    pushSuccess(`已收藏 ${success} 只到候选池` + (fail ? `（${fail} 失败）` : ''))
}

async function batchAddDragonToWatchlist() {
    if (!dragonSelectedCodes.value.size) return
    const targets = dragon.sortedResults.value.filter(
        r => dragonSelectedCodes.value.has(r.code) && !isInWatchlist(r.code),
    )
    if (!targets.length) {
        pushSuccess('选中的票都已经在自选里了')
        return
    }
    for (const r of targets) {
        openAddToWatchlist(r.code, r.name, r.suggestedEntry ?? r.lastClose)
        await new Promise(res => setTimeout(res, 100))
    }
    setTimeout(loadWatchlistCodes, 2000)
}

// ============================================================
// 连板游资（全市场扫描，Ptrade 1:1）
// ============================================================
const relay = useLimitUpRelayScan()
const relaySelectedCodes = ref(new Set())
const relayBatchProcessing = ref(false)

const relayLastScanHint = computed(() => {
    if (!relay.lastScanAt.value) return '从未扫描'
    const ms = new Date(relay.lastScanAt.value).getTime()
    const ageMin = Math.round((Date.now() - ms) / 60000)
    if (ageMin < 1) return '刚刚'
    if (ageMin < 60) return `${ageMin} 分钟前`
    return `${Math.round(ageMin / 60)} 小时前`
})

function toggleRelayRow(code, e) {
    e?.stopPropagation()
    if (isFullyAdded(code)) return
    const next = new Set(relaySelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    relaySelectedCodes.value = next
}

const allRelaySelectableSelected = computed(() => {
    const selectable = relay.sortedResults.value.filter(r => !isFullyAdded(r.code))
    if (!selectable.length) return false
    return selectable.every(r => relaySelectedCodes.value.has(r.code))
})

function toggleAllRelay() {
    const selectable = relay.sortedResults.value.filter(r => !isFullyAdded(r.code))
    const next = new Set(relaySelectedCodes.value)
    if (allRelaySelectableSelected.value) {
        for (const r of selectable) next.delete(r.code)
    } else {
        for (const r of selectable) next.add(r.code)
    }
    relaySelectedCodes.value = next
}

function viewRelayChart(row) {
    const list = relay.sortedResults.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(row.code, row.name, list)
}

async function saveRelayRowToCandidatePool(row, e) {
    e?.stopPropagation()
    if (isInCandidatePool(row.code)) return
    try {
        const res = await api.addCandidatePick({
            code: row.code, name: row.name, stage: 3,
            save_price: row.lastClose,
            break_level: row.limitPrice,
            golden_price: row.lastClose,
            s1_lower: null, consolidation_bars: null,
            source: '连板游资',
        })
        if (res.ok) {
            pushSuccess(`已收藏 ${row.name}（${row.code}）→ 候选池`)
            await loadCandidatePoolCodes()
        } else {
            pushError(res.error || '收藏失败')
        }
    } catch (err) {
        pushError(`收藏失败：${err}`)
    }
}

function addRelayRowToWatchlist(row, e) {
    e?.stopPropagation()
    if (isInWatchlist(row.code)) return
    openAddToWatchlist(row.code, row.name, row.lastClose)
    setTimeout(loadWatchlistCodes, 1500)
}

async function batchSaveRelayToCandidatePool() {
    if (relayBatchProcessing.value || !relaySelectedCodes.value.size) return
    const targets = relay.sortedResults.value.filter(
        r => relaySelectedCodes.value.has(r.code) && !isInCandidatePool(r.code),
    )
    if (!targets.length) {
        pushSuccess('选中的票都已经在候选池里了')
        return
    }
    relayBatchProcessing.value = true
    let success = 0, fail = 0
    for (const r of targets) {
        try {
            const res = await api.addCandidatePick({
                code: r.code, name: r.name, stage: 3,
                save_price: r.lastClose,
                break_level: r.limitPrice,
                golden_price: r.lastClose,
                s1_lower: null, consolidation_bars: null,
                source: '连板游资',
            })
            if (res.ok) success++; else fail++
        } catch { fail++ }
    }
    relayBatchProcessing.value = false
    relaySelectedCodes.value = new Set()
    await loadCandidatePoolCodes()
    pushSuccess(`已收藏 ${success} 只到候选池` + (fail ? `（${fail} 失败）` : ''))
}

async function batchAddRelayToWatchlist() {
    if (!relaySelectedCodes.value.size) return
    const targets = relay.sortedResults.value.filter(
        r => relaySelectedCodes.value.has(r.code) && !isInWatchlist(r.code),
    )
    if (!targets.length) {
        pushSuccess('选中的票都已经在自选里了')
        return
    }
    for (const r of targets) {
        openAddToWatchlist(r.code, r.name, r.lastClose)
        await new Promise(res => setTimeout(res, 100))
    }
    setTimeout(loadWatchlistCodes, 2000)
}

// ============================================================
// onMounted
// ============================================================
onMounted(async () => {
    await Promise.all([loadCandidatePoolCodes(), loadWatchlistCodes()])
    refreshCacheStatus()
})
</script>

<template>
    <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

        <!-- ========== 顶部 tab 栏 ========== -->
        <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0 px-[12px] gap-[2px]">
            <button v-for="t in [
                { key: 'recent_market', label: '近期发车',
                  title: '全市场近 N 天 detector 找到的 stage 3 突破，按推荐度排序' },
                { key: 'dragon_return', label: '龙回头',
                  title: '15 根内 ≥3 涨停（含 3 天内 2 涨停簇）+ 回踩 15-30% + 近 5 根振幅 ≤10% + 启动信号' },
                { key: 'limit_up_relay', label: '连板游资',
                  title: 'Ptrade 1:1 — 仅主板 + 中小板，剔除 ST/科创/创业/次新（< 60 日）。今日触板 + 三种形态 + 妖股基因' },
            ]" :key="t.key"
                 @click="scanTab = t.key"
                 :title="t.title || ''"
                 class="flex items-center gap-[6px] px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0"
                 :class="scanTab === t.key
                    ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                    : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'">
                <span>{{ t.label }}</span>
            </button>
        </div>

        <!-- ========== 近期发车工具栏 ========== -->
        <div v-if="scanTab === 'recent_market'"
             class="h-[44px] flex items-center px-[14px] border-b border-[#e5e7eb] bg-white shrink-0 gap-[8px]">
            <span class="text-[12px] text-[#666] shrink-0">突破时段</span>
            <div class="flex items-center gap-[3px] shrink-0">
                <button v-for="d in RECENT_DAYS_OPTIONS" :key="d"
                        @click="recentDays = d"
                        :class="['text-[10px] font-semibold h-[22px] px-[8px] rounded-[3px] transition tabular-nums',
                                 recentDays === d
                                    ? 'bg-[#1e293b] text-white'
                                    : 'bg-[#f3f4f6] text-[#666] hover:bg-[#e5e7eb]']">
                    ≤{{ d }}d
                </button>
            </div>
            <span v-if="cacheStatus"
                  class="text-[10px] tabular-nums truncate min-w-0 ml-[8px]"
                  :class="shouldPromptDownload
                    ? 'text-[#dc2626] font-semibold'
                    : (cacheStatus.missingCount > 0 ? 'text-[#854d0e]' : 'text-[#059669]')"
                  :title="shouldPromptDownload
                    ? `已收盘 + 有 ${cacheStatus.missingCount} 只缺今日数据，建议点&quot;下载今日 K&quot;`
                    : (cacheStatus.missingCount > 0
                        ? `${cacheStatus.missingCount} 只缺数据（盘前/盘中正常）`
                        : `全部已是最新（${cacheStatus.target_date}）`)">
                {{ cacheStatusHint }}
            </span>
            <span v-else-if="cacheStatusChecking" class="text-[10px] text-[#94a3b8] ml-[8px]">检查中...</span>
            <span v-if="market.lastScanAt.value"
                  class="text-[10px] text-[#94a3b8] tabular-nums shrink-0"
                  :title="`上次扫描 ${lastScanHint}`">
                · 扫于 {{ lastScanHint }}
            </span>
            <div class="ml-auto flex items-center gap-[8px] shrink-0">
                <button v-if="downloader.phase.value === 'idle' || downloader.phase.value === 'done' || downloader.phase.value === 'error'"
                        @click="downloadTodayKlines"
                        :disabled="market.scanning.value"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#0369a1]/40
                               text-[#0369a1] bg-white hover:bg-[#eff6ff] hover:border-[#0369a1]
                               disabled:opacity-40 disabled:cursor-not-allowed transition shrink-0">
                    下载今日 K
                </button>
                <span v-else-if="downloader.phase.value === 'checking'"
                      class="text-[12px] text-[#0369a1] animate-pulse shrink-0">预检中...</span>
                <span v-else-if="downloader.phase.value === 'downloading'"
                      class="text-[12px] text-[#0369a1] tabular-nums shrink-0">
                    下载 {{ downloader.downloaded.value + downloader.failed.value }} / {{ downloader.totalToDownload.value }}
                    <button @click="downloader.cancel()" class="ml-[6px] text-[#94a3b8] hover:text-[#dc2626]">取消</button>
                </span>
                <button v-if="!market.scanning.value"
                        @click="market.scan()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0">
                    重新扫描
                </button>
                <button v-else
                        @click="market.cancel()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#854d0e]/40
                               text-[#854d0e] bg-[#fef3c7] hover:bg-[#fde68a] transition shrink-0">
                    取消（{{ market.scanned.value }}/{{ market.total.value }}）
                </button>
            </div>
        </div>

        <!-- 近期发车进度条 -->
        <div v-if="scanTab === 'recent_market' && market.scanning.value"
             class="px-[16px] py-[8px] border-b border-[#f0f0f0] bg-[#fafafa]">
            <div class="flex items-center justify-between text-[11px] mb-[5px]">
                <span class="text-[#475569]">
                    扫描中 <span class="font-bold tabular-nums text-[#dc2626]">{{ market.scanned.value }}</span>
                    / {{ market.total.value }}
                    <span v-if="market.currentCode.value" class="ml-[6px] text-[#94a3b8] font-mono">{{ market.currentCode.value }}</span>
                </span>
                <span class="text-[#94a3b8]">{{ market.progressPct.value }}%</span>
            </div>
            <div class="h-[3px] bg-[#e5e7eb] rounded-full overflow-hidden">
                <div class="h-full bg-[#dc2626] transition-all duration-200"
                     :style="{ width: market.progressPct.value + '%' }"></div>
            </div>
        </div>

        <!-- 下载进度条 -->
        <div v-if="scanTab === 'recent_market' && downloader.phase.value === 'downloading'"
             class="px-[16px] py-[8px] border-b border-[#f0f0f0] bg-[#eff6ff]">
            <div class="flex items-center justify-between text-[11px] mb-[5px]">
                <span class="text-[#0369a1]">
                    📥 下载今日 K 线
                    <span class="font-bold tabular-nums">{{ downloader.downloaded.value }}</span>
                    + 失败 {{ downloader.failed.value }}
                    / {{ downloader.totalToDownload.value }}
                    <span v-if="downloader.currentCode.value" class="ml-[6px] text-[#94a3b8] font-mono">{{ downloader.currentCode.value }}</span>
                </span>
                <span class="text-[#94a3b8]">{{ downloader.downloadProgressPct.value }}%</span>
            </div>
            <div class="h-[3px] bg-[#e5e7eb] rounded-full overflow-hidden">
                <div class="h-full bg-[#0369a1] transition-all duration-200"
                     :style="{ width: downloader.downloadProgressPct.value + '%' }"></div>
            </div>
        </div>

        <!-- 近期发车批量工具栏 -->
        <div v-if="scanTab === 'recent_market' && recentMarketRows.length"
             class="px-[16px] py-[6px] border-b border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[10px] text-[11px]">
            <span class="text-[#666]">
                已选
                <span class="font-bold tabular-nums" :class="marketSelectedCodes.size ? 'text-[#dc2626]' : 'text-[#999]'">
                    {{ marketSelectedCodes.size }}
                </span>
                / {{ recentMarketRows.length }}
            </span>
            <button @click="batchSaveMarketToCandidatePool"
                    :disabled="!marketSelectedCodes.size || marketBatchProcessing"
                    class="px-[10px] py-[3px] rounded-[4px] border transition disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="marketSelectedCodes.size && !marketBatchProcessing
                        ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] hover:bg-[#fde68a] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                ★ 批量收藏到候选池
            </button>
            <button @click="batchAddMarketToWatchlist"
                    :disabled="!marketSelectedCodes.size"
                    class="px-[10px] py-[3px] rounded-[4px] border transition disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="marketSelectedCodes.size
                        ? 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca] hover:bg-[#fecaca] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                + 批量加自选
            </button>
            <span v-if="marketBatchProcessing" class="text-[#dc2626] font-semibold animate-pulse">处理中...</span>
        </div>

        <!-- ========== 龙回头工具栏 ========== -->
        <div v-if="scanTab === 'dragon_return'"
             class="h-[44px] flex items-center px-[14px] border-b border-[#e5e7eb] bg-white shrink-0 gap-[8px]">
            <span class="text-[12px] text-[#666] shrink-0">
                龙回头
                <span v-if="dragon.results.value.length" class="text-[#dc2626] font-bold tabular-nums">{{ dragon.results.value.length }}</span>
                <span v-else class="text-[#94a3b8]">—</span>
                只
            </span>
            <span class="text-[10px] text-[#94a3b8] shrink-0">
                · 15 根 ≥3 涨停（含紧密簇）· 回踩 15-30% · 近 5 根启动 K 量比 ≥1.7× 地量
            </span>
            <span v-if="dragon.lastScanAt.value" class="text-[10px] text-[#94a3b8] tabular-nums shrink-0 ml-[8px]">
                · 扫于 {{ dragonLastScanHint }}
            </span>
            <div class="ml-auto flex items-center gap-[8px] shrink-0">
                <button v-if="!dragon.scanning.value"
                        @click="dragon.scan()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0">
                    重新扫描
                </button>
                <button v-else
                        @click="dragon.cancel()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#854d0e]/40
                               text-[#854d0e] bg-[#fef3c7] hover:bg-[#fde68a] transition shrink-0">
                    取消（{{ dragon.scanned.value }}/{{ dragon.total.value }}）
                </button>
            </div>
        </div>

        <!-- 龙回头进度条 -->
        <div v-if="scanTab === 'dragon_return' && dragon.scanning.value"
             class="px-[16px] py-[8px] border-b border-[#f0f0f0] bg-[#fafafa]">
            <div class="flex items-center justify-between text-[11px] mb-[5px]">
                <span class="text-[#475569]">
                    扫描中 <span class="font-bold tabular-nums text-[#dc2626]">{{ dragon.scanned.value }}</span>
                    / {{ dragon.total.value }}
                    <span v-if="dragon.currentCode.value" class="ml-[6px] text-[#94a3b8] font-mono">{{ dragon.currentCode.value }}</span>
                </span>
                <span class="text-[#94a3b8]">{{ dragon.progressPct.value }}%</span>
            </div>
            <div class="h-[3px] bg-[#e5e7eb] rounded-full overflow-hidden">
                <div class="h-full bg-[#dc2626] transition-all duration-200"
                     :style="{ width: dragon.progressPct.value + '%' }"></div>
            </div>
        </div>

        <!-- 龙回头批量工具栏 -->
        <div v-if="scanTab === 'dragon_return' && dragon.sortedResults.value.length"
             class="px-[16px] py-[6px] border-b border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[10px] text-[11px]">
            <span class="text-[#666]">
                已选
                <span class="font-bold tabular-nums" :class="dragonSelectedCodes.size ? 'text-[#dc2626]' : 'text-[#999]'">
                    {{ dragonSelectedCodes.size }}
                </span>
                / {{ dragon.sortedResults.value.length }}
            </span>
            <button @click="batchSaveDragonToCandidatePool"
                    :disabled="!dragonSelectedCodes.size || dragonBatchProcessing"
                    class="px-[10px] py-[3px] rounded-[4px] border transition disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="dragonSelectedCodes.size && !dragonBatchProcessing
                        ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] hover:bg-[#fde68a] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                ★ 批量收藏到候选池
            </button>
            <button @click="batchAddDragonToWatchlist"
                    :disabled="!dragonSelectedCodes.size"
                    class="px-[10px] py-[3px] rounded-[4px] border transition disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="dragonSelectedCodes.size
                        ? 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca] hover:bg-[#fecaca] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                + 批量加自选
            </button>
            <span v-if="dragonBatchProcessing" class="text-[#dc2626] font-semibold animate-pulse">处理中...</span>
        </div>

        <!-- ========== 连板游资工具栏 ========== -->
        <div v-if="scanTab === 'limit_up_relay'"
             class="h-[44px] flex items-center px-[14px] border-b border-[#e5e7eb] bg-white shrink-0 gap-[8px]">
            <span class="text-[12px] text-[#666] shrink-0">
                连板游资
                <span v-if="relay.results.value.length" class="text-[#dc2626] font-bold tabular-nums">{{ relay.results.value.length }}</span>
                <span v-else class="text-[#94a3b8]">—</span>
                只
            </span>
            <span class="text-[10px] text-[#94a3b8] shrink-0">
                · 主板+中小板 · 剔除 ST/科创/创业/次新 · 今日触板 + 三形态 + 妖股基因
            </span>
            <span v-if="relay.lastScanAt.value" class="text-[10px] text-[#94a3b8] tabular-nums shrink-0 ml-[8px]">
                · 扫于 {{ relayLastScanHint }}
            </span>
            <div class="ml-auto flex items-center gap-[8px] shrink-0">
                <button v-if="!relay.scanning.value"
                        @click="relay.scan()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0">
                    重新扫描
                </button>
                <button v-else
                        @click="relay.cancel()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#854d0e]/40
                               text-[#854d0e] bg-[#fef3c7] hover:bg-[#fde68a] transition shrink-0">
                    取消（{{ relay.scanned.value }}/{{ relay.total.value }}）
                </button>
            </div>
        </div>

        <!-- 连板游资进度条 -->
        <div v-if="scanTab === 'limit_up_relay' && relay.scanning.value"
             class="px-[16px] py-[8px] border-b border-[#f0f0f0] bg-[#fafafa]">
            <div class="flex items-center justify-between text-[11px] mb-[5px]">
                <span class="text-[#475569]">
                    扫描中 <span class="font-bold tabular-nums text-[#dc2626]">{{ relay.scanned.value }}</span>
                    / {{ relay.total.value }}
                    <span v-if="relay.currentCode.value" class="ml-[6px] text-[#94a3b8] font-mono">{{ relay.currentCode.value }}</span>
                </span>
                <span class="text-[#94a3b8]">{{ relay.progressPct.value }}%</span>
            </div>
            <div class="h-[3px] bg-[#e5e7eb] rounded-full overflow-hidden">
                <div class="h-full bg-[#dc2626] transition-all duration-200"
                     :style="{ width: relay.progressPct.value + '%' }"></div>
            </div>
        </div>

        <!-- 连板游资批量工具栏 -->
        <div v-if="scanTab === 'limit_up_relay' && relay.sortedResults.value.length"
             class="px-[16px] py-[6px] border-b border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[10px] text-[11px]">
            <span class="text-[#666]">
                已选
                <span class="font-bold tabular-nums" :class="relaySelectedCodes.size ? 'text-[#dc2626]' : 'text-[#999]'">
                    {{ relaySelectedCodes.size }}
                </span>
                / {{ relay.sortedResults.value.length }}
            </span>
            <button @click="batchSaveRelayToCandidatePool"
                    :disabled="!relaySelectedCodes.size || relayBatchProcessing"
                    class="px-[10px] py-[3px] rounded-[4px] border transition disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="relaySelectedCodes.size && !relayBatchProcessing
                        ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] hover:bg-[#fde68a] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                ★ 批量收藏到候选池
            </button>
            <button @click="batchAddRelayToWatchlist"
                    :disabled="!relaySelectedCodes.size"
                    class="px-[10px] py-[3px] rounded-[4px] border transition disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="relaySelectedCodes.size
                        ? 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca] hover:bg-[#fecaca] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                + 批量加自选
            </button>
            <span v-if="relayBatchProcessing" class="text-[#dc2626] font-semibold animate-pulse">处理中...</span>
        </div>

        <!-- ========== 表格区 ========== -->
        <div class="flex-1 overflow-auto custom-scrollbar">

            <!-- 近期发车表格 -->
            <div v-if="scanTab === 'recent_market'">
                <div v-if="market.scanning.value && !market.trades.value.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    全市场扫描中... 缓存命中约 60 秒，全冷启动约 25 分钟
                </div>
                <div v-else-if="market.lastError.value" class="py-[80px] text-center text-[#dc2626] text-[12px]">
                    扫描失败: {{ market.lastError.value }}
                </div>
                <div v-else-if="!market.trades.value.length && !market.scanning.value" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    还没扫过 — 点击右上方"重新扫描"开始
                </div>
                <div v-else-if="!recentMarketRows.length" class="py-[60px] text-center text-[#aaa] text-[13px]">
                    最近 {{ recentDays }} 天没找到 stage 3 突破。试试调宽时段（≤5d / ≤7d）
                </div>
                <table v-else class="w-full text-left border-collapse whitespace-nowrap text-[12px]">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                        <tr>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[32px]">
                                <input type="checkbox" :checked="allMarketSelectableSelected" @change="toggleAllMarket"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                            </th>
                            <th class="px-[12px] py-[8px] font-normal w-[60px]">评级</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[36px]" title="周线趋势确认">周</th>
                            <th class="px-[12px] py-[8px] font-normal w-[160px]">股票</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[80px]">突破日</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[60px]">持有</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">入场价</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">当前价</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">收益</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[140px]">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in recentMarketRows" :key="r.code"
                            @dblclick="viewMarketChart(r)"
                            class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                            :class="marketSelectedCodes.has(r.code) ? 'bg-[#fff5f5]' : ''"
                            title="双击看 K 线">
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <input type="checkbox" :checked="marketSelectedCodes.has(r.code)" :disabled="isFullyAdded(r.code)"
                                       @change="toggleMarketRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[6px]">
                                    <span :class="['inline-flex items-center justify-center w-[24px] h-[20px] rounded-[3px] text-[11px] font-bold', gradeChipCls(r.grade)]">{{ r.grade }}</span>
                                    <span class="text-[10px] text-[#94a3b8] tabular-nums">{{ r.score }}</span>
                                </div>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.weeklyConfirmed === true"  class="text-[#dc2626] font-bold text-[13px]">✓</span>
                                <span v-else-if="r.weeklyConfirmed === false" class="text-[#94a3b8] text-[13px]">✗</span>
                                <span v-else class="text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                </div>
                                <div class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</div>
                            </td>
                            <td class="px-[8px] py-[8px] text-center text-[11px] text-[#666] tabular-nums">{{ fmtMarketDate(r.s3Time) }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.holdBars ?? '—' }}d</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.entryPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-bold text-[13px]" :class="colorOf(r.returnPct)">{{ r.exitPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-semibold" :class="colorOf(r.returnPct)">
                                <span v-if="r.returnPct != null">
                                    {{ r.returnPct > 0 ? '▲' : r.returnPct < 0 ? '▼' : '◆' }} {{ fmtPct(r.returnPct) }}
                                </span>
                                <span v-else>—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <div class="flex items-center justify-center gap-[4px]">
                                    <button @click.stop="saveMarketRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)"
                                            class="text-[11px] px-[6px] py-[3px] rounded-[3px] transition border"
                                            :class="isInCandidatePool(r.code)
                                                ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] cursor-default'
                                                : 'bg-white text-[#f59e0b] border-[#fde68a] hover:bg-[#fffbeb]'">
                                        {{ isInCandidatePool(r.code) ? '★' : '☆' }}
                                    </button>
                                    <button @click.stop="addMarketRowToWatchlist(r, $event)" :disabled="isInWatchlist(r.code)"
                                            class="text-[10px] px-[6px] py-[3px] rounded-[3px] transition border font-semibold"
                                            :class="isInWatchlist(r.code)
                                                ? 'bg-[#e5e7eb] text-[#666] border-[#e5e7eb] cursor-default'
                                                : 'bg-white text-[#dc2626] border-[#fecaca] hover:bg-[#fff5f5]'">
                                        {{ isInWatchlist(r.code) ? '已自选' : '+ 自选' }}
                                    </button>
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- 龙回头表格 -->
            <div v-if="scanTab === 'dragon_return'">
                <div v-if="dragon.scanning.value && !dragon.results.value.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    全市场扫描中...
                </div>
                <div v-else-if="dragon.lastError.value" class="py-[80px] text-center text-[#dc2626] text-[12px]">
                    扫描失败: {{ dragon.lastError.value }}
                </div>
                <div v-else-if="!dragon.results.value.length && !dragon.scanning.value" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    还没扫过 — 点击右上方"重新扫描"开始
                </div>
                <table v-else class="w-full text-left border-collapse whitespace-nowrap text-[12px]">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                        <tr>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[32px]">
                                <input type="checkbox" :checked="allDragonSelectableSelected" @change="toggleAllDragon"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                            </th>
                            <th class="px-[12px] py-[8px] font-normal w-[180px]">股票</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]">涨停</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">高点</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">回踩</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[90px]">启动日</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[60px]">量比</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">现价</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">止损</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[140px]">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in dragon.sortedResults.value" :key="r.code"
                            @dblclick="viewDragonChart(r)"
                            class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                            :class="dragonSelectedCodes.has(r.code) ? 'bg-[#fff5f5]' : ''">
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <input type="checkbox" :checked="dragonSelectedCodes.has(r.code)" :disabled="isFullyAdded(r.code)"
                                       @change="toggleDragonRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                </div>
                                <div class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</div>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span class="inline-flex items-center justify-center min-w-[22px] h-[18px] rounded-[3px] bg-[#fee2e2] text-[#991b1b] text-[11px] font-bold tabular-nums px-[5px]">{{ r.limitUpCount }}</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.peakHigh?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-semibold text-[#854d0e]">-{{ r.pullbackPct?.toFixed?.(1) }}%</td>
                            <td class="px-[8px] py-[8px] text-center text-[11px] text-[#666] tabular-nums">{{ fmtMarketDate(r.ignitionTime) }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#dc2626] font-semibold">{{ r.ignitionVr?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-bold text-[13px] text-[#111]">{{ r.lastClose?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[10px] text-[#94a3b8]">{{ r.stopLoss?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <div class="flex items-center justify-center gap-[4px]">
                                    <button @click.stop="saveDragonRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)"
                                            class="text-[11px] px-[6px] py-[3px] rounded-[3px] transition border"
                                            :class="isInCandidatePool(r.code)
                                                ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] cursor-default'
                                                : 'bg-white text-[#f59e0b] border-[#fde68a] hover:bg-[#fffbeb]'">
                                        {{ isInCandidatePool(r.code) ? '★' : '☆' }}
                                    </button>
                                    <button @click.stop="addDragonRowToWatchlist(r, $event)" :disabled="isInWatchlist(r.code)"
                                            class="text-[10px] px-[6px] py-[3px] rounded-[3px] transition border font-semibold"
                                            :class="isInWatchlist(r.code)
                                                ? 'bg-[#e5e7eb] text-[#666] border-[#e5e7eb] cursor-default'
                                                : 'bg-white text-[#dc2626] border-[#fecaca] hover:bg-[#fff5f5]'">
                                        {{ isInWatchlist(r.code) ? '已自选' : '+ 自选' }}
                                    </button>
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- 连板游资表格 -->
            <div v-if="scanTab === 'limit_up_relay'">
                <div v-if="relay.scanning.value && !relay.results.value.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    全市场扫描中...
                </div>
                <div v-else-if="relay.lastError.value" class="py-[80px] text-center text-[#dc2626] text-[12px]">
                    扫描失败: {{ relay.lastError.value }}
                </div>
                <div v-else-if="!relay.results.value.length && !relay.scanning.value" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    还没扫过 — 点击右上方"重新扫描"开始
                </div>
                <table v-else class="w-full text-left border-collapse whitespace-nowrap text-[12px]">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                        <tr>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[32px]">
                                <input type="checkbox" :checked="allRelaySelectableSelected" @change="toggleAllRelay"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                            </th>
                            <th class="px-[12px] py-[8px] font-normal w-[180px]">股票</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[120px]">形态</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[80px]">连板</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]">5天板</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">现价</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">涨停价</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[140px]">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in relay.sortedResults.value" :key="r.code"
                            @dblclick="viewRelayChart(r)"
                            class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                            :class="relaySelectedCodes.has(r.code) ? 'bg-[#fff5f5]' : ''">
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <input type="checkbox" :checked="relaySelectedCodes.has(r.code)" :disabled="isFullyAdded(r.code)"
                                       @change="toggleRelayRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <span v-if="r.isST" class="shrink-0 text-[9px] text-[#dc2626] bg-[#fee2e2] px-[3px] rounded-[2px]">ST</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                </div>
                                <div class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</div>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span class="text-[10px] text-[#dc2626] font-semibold">{{ r.reason }}</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span class="inline-flex items-center justify-center min-w-[32px] h-[18px] rounded-[3px] bg-[#fee2e2] text-[#991b1b] text-[11px] font-bold tabular-nums px-[5px]">{{ r.stat }}</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center tabular-nums text-[#854d0e] font-semibold">{{ r.boards5 }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-bold text-[13px] text-[#111]">{{ r.lastClose?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[10px] text-[#94a3b8]">{{ r.limitPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <div class="flex items-center justify-center gap-[4px]">
                                    <button @click.stop="saveRelayRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)"
                                            class="text-[11px] px-[6px] py-[3px] rounded-[3px] transition border"
                                            :class="isInCandidatePool(r.code)
                                                ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] cursor-default'
                                                : 'bg-white text-[#f59e0b] border-[#fde68a] hover:bg-[#fffbeb]'">
                                        {{ isInCandidatePool(r.code) ? '★' : '☆' }}
                                    </button>
                                    <button @click.stop="addRelayRowToWatchlist(r, $event)" :disabled="isInWatchlist(r.code)"
                                            class="text-[10px] px-[6px] py-[3px] rounded-[3px] transition border font-semibold"
                                            :class="isInWatchlist(r.code)
                                                ? 'bg-[#e5e7eb] text-[#666] border-[#e5e7eb] cursor-default'
                                                : 'bg-white text-[#dc2626] border-[#fecaca] hover:bg-[#fff5f5]'">
                                        {{ isInWatchlist(r.code) ? '已自选' : '+ 自选' }}
                                    </button>
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

        </div>
    </div>
</template>
