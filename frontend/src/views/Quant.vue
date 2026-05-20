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
import { useBreakoutEveScan } from '../composables/useBreakoutEveScan'
import { useKlineDownloader } from '../composables/useKlineDownloader'
import { useMarketEnv } from '../composables/useMarketEnv'
import { useDailyAutoScan } from '../composables/useDailyAutoScan'
import AddTradeJournalModal from '../components/AddTradeJournalModal.vue'
import RowActionMenu from '../components/RowActionMenu.vue'
import KCacheBadge from '../components/KCacheBadge.vue'

// 单例：早盘自动扫描结果（用于 all_signals 在用户未跑扫描器时也能展示当日 ⭐⭐⭐+）
const autoScan = useDailyAutoScan()

// ---------------- 顶部 tab：3 个扫描器 ----------------
const scanTab = ref('all_signals')   // 'all_signals' | 'recent_market' | 'breakout_eve' | 'dragon_return' | 'limit_up_relay'

// ---------------- 候选池 / 自选 / 日志中 codes（用于行内徽章 + isFullyAdded）----------------
const candidatePoolCodes = ref(new Set())
const watchlistCodes = ref(new Set())
const journalingCodes = ref(new Set())   // 在交易日志且 status=open（持仓中）

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

async function loadJournalingCodes() {
    try {
        const res = await api.listTradeJournal('open', null, 500)
        if (res?.ok && Array.isArray(res.data)) {
            journalingCodes.value = new Set(res.data.map(t => t.code))
        }
    } catch { /* 静默 */ }
}

function isInCandidatePool(code) { return candidatePoolCodes.value.has(code) }
function isInWatchlist(code)     { return watchlistCodes.value.has(code) }
function isInJournal(code)       { return journalingCodes.value.has(code) }
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

// Phase 3 Step 2：ML 阈值
const ML_STAR_THRESHOLD = 0.40

// 星级体系（基于 week3_lhb_v1 真 holdout + 跨段 + LHB 组合验证）：
//
// ⭐⭐⭐⭐  confirm in [strong,medium] AND lhbInWindow   极稀缺（月 1-2），LHB 主力 + N+1 站稳
// ⭐⭐⭐   confirm in [strong,medium] OR lhbInWindow    holdout 74.4% (n=43)，月 7-12 个
// ⭐⭐    confirm='medium' (单独)                       holdout 63.6%，4 段稳定 62-91%
// ⭐      confirm='strong' / sectorStrength ≥ 70 / mlScore ≥ 0.40   辅助参考
//
// 核心反直觉：
// 1. medium > strong（散户追高后劲不足，平开起步更稳）
// 2. LHB 是"假突破"救赎规则（confirm=fail + LHB → 75% win）
// 3. ML AUC 0.55 无法捕到 LHB 稀疏信号，只能靠规则层

// 格式化龙虎榜净买额（元 → 万）。空/无效 → '?'，避免 toFixed NaN
function fmtLhbNet(v) {
    if (v == null || !Number.isFinite(+v)) return '?'
    return (+v / 1e4).toFixed(0) + '万'
}

function getTradeStarLevel(t) {
    const confirmGood = t.breakoutConfirm === 'strong' || t.breakoutConfirm === 'medium'
    const hasLhb = t.lhbInWindow === 1 || (typeof t.features?.lhbInWindow === 'number' && t.features.lhbInWindow === 1)
    // ⭐⭐⭐⭐: 双重确认（confirm + LHB），最强但稀缺
    if (confirmGood && hasLhb) return 4
    // ⭐⭐⭐: 单维强信号（OR 关系）
    if (confirmGood || hasLhb) return 3
    // ⭐⭐: 仍有边际价值
    if (t.breakoutConfirm === 'medium') return 2
    // ⭐: 辅助参考
    if (t.breakoutConfirm === 'strong') return 1
    if (typeof t.sectorScore === 'number' && t.sectorScore >= 70) return 1
    if (typeof t.mlScore === 'number' && t.mlScore >= ML_STAR_THRESHOLD) return 1
    return 0
}
function isMLStarTrade(t) { return getTradeStarLevel(t) >= 1 }
function isDoubleStarTrade(t) { return getTradeStarLevel(t) >= 2 }
function isTripleStarTrade(t) { return getTradeStarLevel(t) >= 3 }

const recentMarketRows = computed(() => {
    return market.openTrades.value
        .filter(t => (t.holdBars ?? 0) <= recentDays.value)
        .sort((a, b) => {
            // Week 4 修正：星级强排序（⭐⭐⭐ > ⭐⭐ > ⭐ > 0）
            const aStar = getTradeStarLevel(a), bStar = getTradeStarLevel(b)
            if (aStar !== bStar) return bStar - aStar
            // 综合分数排序：ML × 0.5 + sectorScore × 0.3 + weekly × 0.2
            const aHasMl = typeof a.mlScore === 'number'
            const bHasMl = typeof b.mlScore === 'number'
            const aSec = typeof a.sectorScore === 'number' ? a.sectorScore / 100 : null
            const bSec = typeof b.sectorScore === 'number' ? b.sectorScore / 100 : null
            // 综合评分：缺特征自动跳过该 component
            const composite = (ml, sec, wk) => {
                let score = 0, weight = 0
                if (ml != null)  { score += ml  * 0.5; weight += 0.5 }
                if (sec != null) { score += sec * 0.3; weight += 0.3 }
                if (wk  != null) { score += wk  * 0.2; weight += 0.2 }
                return weight > 0 ? score / weight : null
            }
            const aComp = composite(aHasMl ? a.mlScore : null, aSec, a.weeklyConfirmed === true ? 1 : a.weeklyConfirmed === false ? 0 : null)
            const bComp = composite(bHasMl ? b.mlScore : null, bSec, b.weeklyConfirmed === true ? 1 : b.weeklyConfirmed === false ? 0 : null)
            if (aComp != null && bComp != null && aComp !== bComp) return bComp - aComp
            // 一档：ML 分数（兜底用，正常 composite 已生效）
            if (aHasMl && !bHasMl) return -1
            if (!aHasMl && bHasMl) return 1
            if (aHasMl && bHasMl && a.mlScore !== b.mlScore) return b.mlScore - a.mlScore
            // 二档：周线确认
            const aw = a.weeklyConfirmed === true ? 0 : a.weeklyConfirmed === false ? 2 : 1
            const bw = b.weeklyConfirmed === true ? 0 : b.weeklyConfirmed === false ? 2 : 1
            if (aw !== bw) return aw - bw
            // 三档：评级
            const gRank = { B: 0, A: 1, C: 2, S: 3 }
            const ar = gRank[a.grade] ?? 4
            const br = gRank[b.grade] ?? 4
            if (ar !== br) return ar - br
            // 四档：当前 returnPct
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
    // 不拦截 isFullyAdded —— 后端候选池是 UPSERT，自选按目标分组单独 dedup，
    // 同一只票可同时属于不同分组（短线/长线/观察池），不应跨分组堵死选择
    const next = new Set(marketSelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    marketSelectedCodes.value = next
}

const allMarketSelectableSelected = computed(() => {
    const selectable = recentMarketRows.value
    if (!selectable.length) return false
    return selectable.every(r => marketSelectedCodes.value.has(r.code))
})

function toggleAllMarket() {
    const selectable = recentMarketRows.value
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
    // 不做 pre-filter —— 后端 add_pick 是 UPSERT，已存在自动合并 source
    // 避免 candidatePoolCodes 缓存陈旧导致的"假已存在"
    const allSelected = recentMarketRows.value.filter(r => marketSelectedCodes.value.has(r.code))
    if (!allSelected.length) return
    marketBatchProcessing.value = true
    let okCount = 0, failCount = 0
    const failedCodes = []
    try {
        for (const r of allSelected) {
            try {
                const res = await api.addCandidatePick({
                    code: r.code, name: r.name, stage: 3,
                    save_price: r.entryPrice ?? r.exitPrice,
                    break_level: r.breakoutPrice,
                    golden_price: r.goldenBuyPrice,
                    s1_lower: null, consolidation_bars: null,
                    source: '主升突破',
                })
                if (res.ok) okCount++
                else { failCount++; failedCodes.push(r.code) }
            } catch (e) {
                failCount++; failedCodes.push(r.code)
                console.warn(`[batch-add] ${r.code} 失败`, e)
            }
        }
        marketSelectedCodes.value = new Set()
        await loadCandidatePoolCodes()
        const msg = `已记入候选池 ${okCount} / ${allSelected.length} 只` + (failCount ? ` · ❌ 失败 ${failCount}` : '')
        if (failCount > 0) {
            console.warn('[batch-add] 失败的代码:', failedCodes)
            pushError(msg)
        } else {
            pushSuccess(msg)
        }
    } finally {
        marketBatchProcessing.value = false
    }
}

function batchAddMarketToWatchlist() {
    if (!marketSelectedCodes.value.size) return
    // 不跨分组过滤——自选分组之间相互隔离，同一只票可同时在多个分组里。
    // 后端 import_batch_add 会按目标分组单独去重。
    // added_at 用扫描数据最后一根 K 线的日期 + 当日 A 股收盘 15:00 CST，
    // 不用"用户点按钮的当下"——保留信号的真实数据日。
    const targets = recentMarketRows.value
        .filter(r => marketSelectedCodes.value.has(r.code))
        .map(r => ({
            code: r.code,
            name: r.name,
            price: r.entryPrice ?? r.exitPrice,
            added_at: r.lastDate ? `${r.lastDate}T15:00:00+08:00` : null,
        }))
    if (!targets.length) return
    openAddToWatchlistBatch(targets)
    setTimeout(loadWatchlistCodes, 1500)
}

function addMarketRowToWatchlist(row, e) {
    e?.stopPropagation()
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
            source: '主升突破',
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

// P0: 一键加交易日志 —— 通过 modal 让用户填实际入场价 + 仓位（避免静默瞎填）
function addToTradeJournal(row, e) {
    e?.stopPropagation()
    openAddJournalForRow(row, '主升突破')
}

function viewMarketChart(row) {
    const list = recentMarketRows.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(row.code, row.name, list)
}

// ============================================================
// 突破前夜（全市场扫 stage 1/2 蓄势态）
// ============================================================
const eve = useBreakoutEveScan()
const eveSelectedCodes = ref(new Set())
const eveBatchProcessing = ref(false)

const eveLastScanHint = computed(() => {
    if (!eve.lastScanAt.value) return '从未扫描'
    const ms = new Date(eve.lastScanAt.value).getTime()
    const ageMin = Math.round((Date.now() - ms) / 60000)
    if (ageMin < 1) return '刚刚'
    if (ageMin < 60) return `${ageMin} 分钟前`
    return `${Math.round(ageMin / 60)} 小时前`
})

function toggleEveRow(code, e) {
    e?.stopPropagation()
    const next = new Set(eveSelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    eveSelectedCodes.value = next
}

const allEveSelectableSelected = computed(() => {
    const sel = eve.sortedResults.value
    if (!sel.length) return false
    return sel.every(r => eveSelectedCodes.value.has(r.code))
})

function toggleAllEve() {
    const sel = eve.sortedResults.value
    if (allEveSelectableSelected.value) {
        const next = new Set(eveSelectedCodes.value)
        for (const r of sel) next.delete(r.code)
        eveSelectedCodes.value = next
    } else {
        const next = new Set(eveSelectedCodes.value)
        for (const r of sel) next.add(r.code)
        eveSelectedCodes.value = next
    }
}

function viewEveChart(r) {
    const list = eve.sortedResults.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(r.code, r.name, list)
}

async function saveEveRowToCandidatePool(r, e) {
    e?.stopPropagation()
    if (isInCandidatePool(r.code)) return
    try {
        const res = await api.addCandidatePick({
            code:               r.code,
            name:               r.name,
            stage:              r.event?.currentStage,
            save_price:         r.lastPrice,
            break_level:        r.event?.s1Upper ?? null,
            golden_price:       r.event?.goldenBuyPrice ?? null,
            s1_lower:           r.event?.s1Lower ?? null,
            consolidation_bars: r.consolidationBars ?? null,
            source:             '突破前夜',
        })
        if (res.ok) {
            candidatePoolCodes.value = new Set([...candidatePoolCodes.value, r.code])
            pushSuccess(`已收藏 ${r.name} 到候选池`)
        }
    } catch (e) { pushError('收藏失败') }
}

async function eveBatchSaveToCandidatePool() {
    if (!eveSelectedCodes.value.size) return
    eveBatchProcessing.value = true
    let okCount = 0, failCount = 0
    const failedCodes = []
    try {
        // 不做 pre-filter（后端 UPSERT 自动合并 source）
        const allSelected = eve.sortedResults.value.filter(r => eveSelectedCodes.value.has(r.code))
        if (!allSelected.length) return
        for (const r of allSelected) {
            try {
                const res = await api.addCandidatePick({
                    code:               r.code,
                    name:               r.name,
                    stage:              r.event?.currentStage,
                    save_price:         r.lastPrice,
                    break_level:        r.event?.s1Upper ?? null,
                    golden_price:       r.event?.goldenBuyPrice ?? null,
                    s1_lower:           r.event?.s1Lower ?? null,
                    consolidation_bars: r.consolidationBars ?? null,
                    source:             '突破前夜',
                })
                if (res.ok) {
                    okCount++
                    candidatePoolCodes.value = new Set([...candidatePoolCodes.value, r.code])
                } else { failCount++; failedCodes.push(r.code) }
            } catch (e) {
                failCount++; failedCodes.push(r.code)
                console.warn(`[batch-add] ${r.code} 失败`, e)
            }
        }
        const msg = `已记入候选池 ${okCount} / ${allSelected.length} 只` + (failCount ? ` · ❌ 失败 ${failCount}` : '')
        if (failCount > 0) {
            console.warn('[batch-add] 失败的代码:', failedCodes)
            pushError(msg)
        } else {
            pushSuccess(msg)
        }
    } finally {
        eveBatchProcessing.value = false
        eveSelectedCodes.value = new Set()
    }
}

function eveBatchAddToWatchlist() {
    if (!eveSelectedCodes.value.size) return
    // added_at = 信号最后一根 K 线日（15:00 CST），保留信号的真实数据日。
    const targets = eve.sortedResults.value
        .filter(r => eveSelectedCodes.value.has(r.code))
        .map(r => ({
            code: r.code,
            name: r.name,
            price: r.lastPrice,
            added_at: r.lastDate ? `${r.lastDate}T15:00:00+08:00` : null,
        }))
    if (!targets.length) return
    openAddToWatchlistBatch(targets)
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
    const next = new Set(dragonSelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    dragonSelectedCodes.value = next
}

const allDragonSelectableSelected = computed(() => {
    const selectable = dragon.sortedResults.value
    if (!selectable.length) return false
    return selectable.every(r => dragonSelectedCodes.value.has(r.code))
})

function toggleAllDragon() {
    const selectable = dragon.sortedResults.value
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
    openAddToWatchlist(row.code, row.name, row.suggestedEntry ?? row.lastClose)
    setTimeout(loadWatchlistCodes, 1500)
}

async function batchSaveDragonToCandidatePool() {
    if (dragonBatchProcessing.value || !dragonSelectedCodes.value.size) return
    // 不做 pre-filter（后端 UPSERT 自动合并 source）
    const allSelected = dragon.sortedResults.value.filter(r => dragonSelectedCodes.value.has(r.code))
    if (!allSelected.length) return
    dragonBatchProcessing.value = true
    let okCount = 0, failCount = 0
    const failedCodes = []
    for (const r of allSelected) {
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
            if (res.ok) okCount++
            else { failCount++; failedCodes.push(r.code) }
        } catch (e) {
            failCount++; failedCodes.push(r.code)
            console.warn(`[batch-add] ${r.code} 失败`, e)
        }
    }
    dragonBatchProcessing.value = false
    dragonSelectedCodes.value = new Set()
    await loadCandidatePoolCodes()
    const msg = `已记入候选池 ${okCount} / ${allSelected.length} 只` + (failCount ? ` · ❌ 失败 ${failCount}` : '')
    if (failCount > 0) {
        console.warn('[batch-add] 失败的代码:', failedCodes)
        pushError(msg)
    } else {
        pushSuccess(msg)
    }
}

async function batchAddDragonToWatchlist() {
    if (!dragonSelectedCodes.value.size) return
    // 不跨分组过滤——自选分组之间相互隔离，同一只票可同时在多个分组里。
    // 后端 import_batch_add 会按目标分组单独去重。
    // added_at = 信号最后一根 K 线日（15:00 CST），保留信号的真实数据日。
    const targets = dragon.sortedResults.value
        .filter(r => dragonSelectedCodes.value.has(r.code))
        .map(r => ({
            code: r.code,
            name: r.name,
            price: r.suggestedEntry ?? r.lastClose,
            added_at: r.lastDate ? `${r.lastDate}T15:00:00+08:00` : null,
        }))
    if (!targets.length) return
    openAddToWatchlistBatch(targets)
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
    const next = new Set(relaySelectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    relaySelectedCodes.value = next
}

const allRelaySelectableSelected = computed(() => {
    const selectable = relay.sortedResults.value
    if (!selectable.length) return false
    return selectable.every(r => relaySelectedCodes.value.has(r.code))
})

function toggleAllRelay() {
    const selectable = relay.sortedResults.value
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
    openAddToWatchlist(row.code, row.name, row.lastClose)
    setTimeout(loadWatchlistCodes, 1500)
}

async function batchSaveRelayToCandidatePool() {
    if (relayBatchProcessing.value || !relaySelectedCodes.value.size) return
    // 不做 pre-filter（后端 UPSERT 自动合并 source）
    const allSelected = relay.sortedResults.value.filter(r => relaySelectedCodes.value.has(r.code))
    if (!allSelected.length) return
    relayBatchProcessing.value = true
    let okCount = 0, failCount = 0
    const failedCodes = []
    for (const r of allSelected) {
        try {
            const res = await api.addCandidatePick({
                code: r.code, name: r.name, stage: 3,
                save_price: r.lastClose,
                break_level: r.limitPrice,
                golden_price: r.lastClose,
                s1_lower: null, consolidation_bars: null,
                source: '连板游资',
            })
            if (res.ok) okCount++
            else { failCount++; failedCodes.push(r.code) }
        } catch (e) {
            failCount++; failedCodes.push(r.code)
            console.warn(`[batch-add] ${r.code} 失败`, e)
        }
    }
    relayBatchProcessing.value = false
    relaySelectedCodes.value = new Set()
    await loadCandidatePoolCodes()
    const msg = `已记入候选池 ${okCount} / ${allSelected.length} 只` + (failCount ? ` · ❌ 失败 ${failCount}` : '')
    if (failCount > 0) {
        console.warn('[batch-add] 失败的代码:', failedCodes)
        pushError(msg)
    } else {
        pushSuccess(msg)
    }
}

async function batchAddRelayToWatchlist() {
    if (!relaySelectedCodes.value.size) return
    // 不跨分组过滤——自选分组之间相互隔离，同一只票可同时在多个分组里。
    // 后端 import_batch_add 会按目标分组单独去重。
    // added_at = 信号最后一根 K 线日（15:00 CST），保留信号的真实数据日。
    const targets = relay.sortedResults.value
        .filter(r => relaySelectedCodes.value.has(r.code))
        .map(r => ({
            code: r.code,
            name: r.name,
            price: r.lastClose,
            added_at: r.lastDate ? `${r.lastDate}T15:00:00+08:00` : null,
        }))
    if (!targets.length) return
    openAddToWatchlistBatch(targets)
    setTimeout(loadWatchlistCodes, 2000)
}

// ============================================================
// 大盘 Regime（Week 1 Day 1）—— 多源 5 档评分
// ============================================================
const { env: marketEnv, refresh: refreshMarketEnv, currentRegime, isBreakdown, isWeak, isStrong } = useMarketEnv()

const REGIME_DISPLAY = {
    strong:    { label: '强势市', icon: '🔥', cls: 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca]',
                 hint: '大盘强势 · 策略放宽阈值，多抓信号' },
    good:      { label: '良好市', icon: '✓',  cls: 'bg-[#dcfce7] text-[#166534] border-[#bbf7d0]',
                 hint: '大盘良好 · 默认参数运行' },
    neutral:   { label: '震荡市', icon: '—',  cls: 'bg-[#f3f4f6] text-[#475569] border-[#e5e7eb]',
                 hint: '大盘震荡 · 默认参数运行' },
    weak:      { label: '弱势市', icon: '⚠',  cls: 'bg-[#fef3c7] text-[#92400e] border-[#fde68a]',
                 hint: '大盘弱势 · 主升突破/龙回头收紧，突破前夜可埋伏' },
    breakdown: { label: '破位市', icon: '✗',  cls: 'bg-[#1e293b] text-white border-[#0f172a]',
                 hint: '大盘破位 · 主升突破/龙回头/连板游资 暂停' },
}
const regimeDisplay = computed(() => REGIME_DISPLAY[currentRegime.value] || null)

// ============ 加交易日志 modal ============
const journalModal = ref({ open: false, prefill: {} })
function openAddJournalForRow(r, sourceLabel) {
    const sourceKey = ({'主升突破':'main_breakout','突破前夜':'breakout_eve','龙回头':'dragon_return','连板游资':'limit_up_relay'})[sourceLabel] || 'manual'
    journalModal.value = {
        open: true,
        prefill: {
            code:           r.code,
            name:           r.name,
            signalSource:   sourceKey,
            starLevel:      typeof r.starLevel === 'number' ? r.starLevel : (typeof getTradeStarLevel === 'function' ? getTradeStarLevel(r) : 0),
            suggestedEntry: r.entryPrice ?? r.suggestedEntry ?? r.lastPrice ?? r.lastClose ?? null,
            breakLevel:     r.breakoutPrice ?? r.event?.s1Upper ?? r.peakHigh ?? null,
            s1Lower:        r.event?.s1Lower ?? r.stopLoss ?? null,
            signalMetadata: {
                breakoutConfirm: r.breakoutConfirm,
                sectorScore:     r.sectorScore,
                sectorName:      r.sectorName,
                mlScore:         r.mlScore,
                lhbInWindow:     r.lhbInWindow,
                lhbCount:        r.lhbCount,
                grade:           r.grade,
            },
        },
    }
}
function closeJournalModal() { journalModal.value.open = false }

// 当前 tab 是否被 regime 阻断（破位市 / 弱势市 → 部分策略停用）
// 跟 getStrategyOverrides 逻辑一一对应：threeStage/dragonReturn breakdown 停；limitUpRelay weak+breakdown 停
const strategyDisabledByRegime = computed(() => {
    const r = currentRegime.value
    if (!r) return null
    if (scanTab.value === 'recent_market'  && r === 'breakdown') return '主升突破在破位市暂停（破位追突破胜率 <30%）'
    if (scanTab.value === 'dragon_return'  && r === 'breakdown') return '龙回头在破位市暂停（妖股复活率极低）'
    if (scanTab.value === 'limit_up_relay' && (r === 'weak' || r === 'breakdown')) {
        return '连板游资在弱势/破位市暂停（赚钱效应消失，连板易炸）'
    }
    return null
})

// ============ B3：全部信号 聚合视图（4 个 source 合并到一个表）============
const allSignalsSourceFilter = ref('all')   // 'all' | 'main_breakout' | 'breakout_eve' | 'dragon_return' | 'limit_up_relay'

// 当 market.openTrades 还没扫过、但今天早盘 auto-scan 已落库时，用 today_result.top_codes 作 fallback
// 避免用户从 Dashboard "查看全部 →" 跳过来看到空表
const mainBreakoutFallback = computed(() => {
    const live = market.openTrades.value || []
    if (live.length) return null   // 实时数据优先
    const tr = autoScan.todayResult.value
    if (!tr || !Array.isArray(tr.top_codes) || !tr.top_codes.length) return null
    return tr.top_codes
})

const allSignals = computed(() => {
    const rows = []
    // 主升突破（实时优先 / fallback 用 today_result）
    if (mainBreakoutFallback.value) {
        for (const c of mainBreakoutFallback.value) {
            rows.push({
                sourceKey: 'main_breakout',
                sourceLabel: '主升突破',
                sourceCls:   'bg-[#cffafe] text-[#155e75] border-[#a5f3fc]',
                code: c.code, name: c.name,
                starLevel: c.starLevel ?? 0,
                currentPrice: c.entryPrice,
                entryPrice:   c.entryPrice,
                sectorName:   null, sectorScore: c.sectorScore,
                lhbInWindow:  c.lhbInWindow, lhbCount: c.lhbCount,
                mlScore:      c.mlScore,
                breakoutConfirm: c.breakoutConfirm,
                extra: { 突破日: () => fmtMarketDate(c.s3Time), 来源: () => '早盘扫描' },
                _fromAutoScan: true,
                _raw: c,
            })
        }
    } else {
        for (const t of (market.openTrades.value || [])) {
            if ((t.holdBars ?? 99) > recentDays.value) continue
            rows.push({
                sourceKey: 'main_breakout',
                sourceLabel: '主升突破',
                sourceCls:   'bg-[#cffafe] text-[#155e75] border-[#a5f3fc]',
                code: t.code, name: t.name,
                starLevel: getTradeStarLevel(t),
                currentPrice: t.exitPrice ?? t.entryPrice,
                entryPrice:   t.entryPrice,
                sectorName:   t.sectorName, sectorScore: t.sectorScore,
                lhbInWindow:  t.lhbInWindow, lhbCount: t.lhbCount, lhbNetBuySum: t.lhbNetBuySum,
                mlScore:      t.mlScore,
                breakoutConfirm: t.breakoutConfirm,
                extra: { 突破日: r => fmtMarketDate(t.s3Time), 收益: r => t.returnPct?.toFixed?.(2) + '%' },
                _raw: t,
            })
        }
    }
    // 突破前夜
    for (const r of (eve.sortedResults.value || [])) {
        rows.push({
            sourceKey: 'breakout_eve',
            sourceLabel: '突破前夜',
            sourceCls:   'bg-[#fef3c7] text-[#92400e] border-[#fde68a]',
            code: r.code, name: r.name,
            starLevel: r.starLevel ?? 0,
            currentPrice: r.lastPrice,
            entryPrice:   r.event?.s1Upper,
            sectorName:   r.sectorName, sectorScore: r.sectorScore,
            lhbInWindow:  r.lhbInWindow, lhbCount: r.lhbCount, lhbNetBuySum: r.lhbNetBuySum,
            extra: { 距突破: () => (r.distanceToBreakPct?.toFixed?.(2) ?? '—') + '%' },
            _raw: r,
        })
    }
    // 龙回头
    for (const r of (dragon.sortedResults.value || [])) {
        rows.push({
            sourceKey: 'dragon_return',
            sourceLabel: '龙回头',
            sourceCls:   'bg-[#fee2e2] text-[#991b1b] border-[#fecaca]',
            code: r.code, name: r.name,
            starLevel: r.starLevel ?? 0,
            currentPrice: r.lastClose,
            entryPrice:   r.suggestedEntry,
            sectorName:   r.sectorName, sectorScore: r.sectorScore,
            lhbInWindow:  r.lhbInWindow, lhbCount: r.lhbCount, lhbNetBuySum: r.lhbNetBuySum,
            extra: { 启动日: () => fmtMarketDate(r.ignitionTime), 回踩: () => '-' + r.pullbackPct?.toFixed?.(1) + '%' },
            _raw: r,
        })
    }
    // 连板游资
    for (const r of (relay.sortedResults.value || [])) {
        rows.push({
            sourceKey: 'limit_up_relay',
            sourceLabel: '连板游资',
            sourceCls:   'bg-[#ffedd5] text-[#9a3412] border-[#fed7aa]',
            code: r.code, name: r.name,
            starLevel: 0,   // 连板游资暂无星级体系
            currentPrice: r.lastClose,
            entryPrice:   r.limitPrice,
            sectorName:   r.sectorName, sectorScore: r.sectorScore,
            extra: { 形态: () => r.mode || '—', 连板: () => r.stat || '—' },
            _raw: r,
        })
    }
    // 过滤
    const filtered = allSignalsSourceFilter.value === 'all'
        ? rows
        : rows.filter(r => r.sourceKey === allSignalsSourceFilter.value)
    // 排序：星级 desc → sector desc
    return filtered.sort((a, b) => {
        if ((b.starLevel ?? 0) !== (a.starLevel ?? 0)) return (b.starLevel ?? 0) - (a.starLevel ?? 0)
        return (b.sectorScore ?? 0) - (a.sectorScore ?? 0)
    })
})

const allSignalsCounts = computed(() => {
    const counts = { all: 0, main_breakout: 0, breakout_eve: 0, dragon_return: 0, limit_up_relay: 0 }
    if (mainBreakoutFallback.value) {
        counts.main_breakout = mainBreakoutFallback.value.length
    } else {
        counts.main_breakout = (market.openTrades.value || []).filter(t => (t.holdBars ?? 99) <= recentDays.value).length
    }
    counts.breakout_eve   = (eve.sortedResults.value || []).length
    counts.dragon_return  = (dragon.sortedResults.value || []).length
    counts.limit_up_relay = (relay.sortedResults.value || []).length
    counts.all = counts.main_breakout + counts.breakout_eve + counts.dragon_return + counts.limit_up_relay
    return counts
})

function openAddJournalForAggRow(r, e) {
    e?.stopPropagation()
    const map = {
        main_breakout:  '主升突破',
        breakout_eve:   '突破前夜',
        dragon_return:  '龙回头',
        limit_up_relay: '连板游资',
    }
    openAddJournalForRow(r._raw, map[r.sourceKey] || '主升突破')
}

function viewAggChart(r) {
    const list = allSignals.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(r.code, r.name, list)
}

// ============================================================
// ============================================================
// 全部信号：刷新策略
// - "刷新当前"：只重扫 allSignals 表里已有的 code 子集（~30 秒，快）
// - "全市场扫"：4 个 source 各自扫整个 A 股市场（~8 分钟，慢）
// ============================================================
const allSignalsRefreshing = computed(() =>
    market.scanning.value || eve.scanning.value || dragon.scanning.value || relay.scanning.value
)

// 当前 allSignals 里的 unique {code, name} 列表
const allSignalsCodes = computed(() => {
    const seen = new Set()
    const out = []
    for (const r of allSignals.value) {
        if (!r?.code || seen.has(r.code)) continue
        seen.add(r.code)
        out.push({ code: r.code, name: r.name || '' })
    }
    return out
})

// 只扫当前列表里已有的 code（4 个 source 并行）
async function refreshAllSignalsSubset() {
    const codes = allSignalsCodes.value
    if (!codes.length) {
        pushError('当前列表为空，请先到各 tab 跑一次扫描')
        return
    }
    const tasks = []
    if (!market.scanning.value) tasks.push(market.scan(codes).catch(e => console.warn('[refresh-subset] 主升突破失败', e)))
    if (!eve.scanning.value)    tasks.push(eve.scan(codes).catch(e => console.warn('[refresh-subset] 突破前夜失败', e)))
    if (!dragon.scanning.value) tasks.push(dragon.scan(codes).catch(e => console.warn('[refresh-subset] 龙回头失败', e)))
    if (!relay.scanning.value)  tasks.push(relay.scan(codes).catch(e => console.warn('[refresh-subset] 连板游资失败', e)))
    if (!tasks.length) return
    await Promise.allSettled(tasks)
}

// 全市场扫（原行为）
async function refreshAllSignalsFullMarket() {
    const tasks = []
    if (!market.scanning.value) tasks.push(market.scan().catch(e => console.warn('[refresh-full] 主升突破失败', e)))
    if (!eve.scanning.value)    tasks.push(eve.scan().catch(e => console.warn('[refresh-full] 突破前夜失败', e)))
    if (!dragon.scanning.value) tasks.push(dragon.scan().catch(e => console.warn('[refresh-full] 龙回头失败', e)))
    if (!relay.scanning.value)  tasks.push(relay.scan().catch(e => console.warn('[refresh-full] 连板游资失败', e)))
    if (!tasks.length) return
    await Promise.allSettled(tasks)
}

// onMounted
// ============================================================
onMounted(async () => {
    await Promise.all([loadCandidatePoolCodes(), loadWatchlistCodes(), loadJournalingCodes(), refreshMarketEnv()])
    refreshCacheStatus()
    // 拉一次今日早盘扫描结果（用作 all_signals 在用户未扫之前的 fallback）
    autoScan.loadTodayResult()
})
</script>

<template>
    <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

        <!-- ========== 顶部 tab 栏 ========== -->
        <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0 px-[12px] gap-[2px]">
            <button v-for="t in [
                { key: 'all_signals', label: '📡 全部信号',
                  title: '聚合主升突破 / 突破前夜 / 龙回头 / 连板游资 已扫数据，按星级排序' },
                { key: 'recent_market', label: '主升突破',
                  title: '全市场近 N 天 detector 找到的 stage 3 突破，已启动主升浪的票' },
                { key: 'breakout_eve', label: '突破前夜',
                  title: '全市场 stage 1/2 蓄势态（蓄势中 + 试盘后等突破），距突破 ≤5%' },
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

            <!-- Week 1 Day 1：大盘 regime badge（右侧）-->
            <div class="ml-auto flex items-center gap-[8px] pr-[6px]">
                <button v-if="regimeDisplay"
                        :title="regimeDisplay.hint + (marketEnv?.regimeScore != null ? ` · 评分 ${marketEnv.regimeScore}/100` : '')"
                        @click="refreshMarketEnv(true)"
                        :class="['inline-flex items-center gap-[4px] px-[8px] py-[3px] rounded-[4px] border text-[11px] font-bold transition cursor-pointer hover:opacity-80', regimeDisplay.cls]">
                    <span class="text-[12px]">{{ regimeDisplay.icon }}</span>
                    <span>{{ regimeDisplay.label }}</span>
                    <span v-if="marketEnv?.regimeScore != null" class="text-[10px] opacity-70 tabular-nums">{{ marketEnv.regimeScore }}</span>
                </button>
                <span v-else class="text-[10px] text-[#94a3b8]">加载大盘环境...</span>
            </div>

            <!-- 最右侧：hub 控件注入位（QuantHub 提供 segmented + 今日按钮）-->
            <div class="shrink-0 flex items-center pl-[10px] pr-[14px] border-l border-[#e5e5e5] gap-[10px] h-full">
                <slot name="tabBarRight" />
            </div>
        </div>

        <!-- Week 1 Day 2：regime 阻断警示横幅 -->
        <div v-if="strategyDisabledByRegime"
             class="px-[14px] py-[6px] bg-[#1e293b] text-white text-[11px] font-semibold border-b border-[#0f172a] shrink-0 flex items-center gap-[8px]">
            <span class="text-[14px]">⚠</span>
            <span>{{ strategyDisabledByRegime }}</span>
            <span class="ml-auto text-[10px] opacity-70">右上角 regime 状态变好后自动恢复</span>
        </div>

        <!-- ========== 策略说明卡片（每个 tab 一个，告诉用户在选什么）========== -->
        <!-- 全部信号（聚合）-->
        <div v-if="scanTab === 'all_signals'"
             class="flex items-center gap-[12px] px-[14px] py-[8px] bg-gradient-to-r from-[#eff6ff] to-[#fff] border-b border-[#bae6fd] text-[11px] shrink-0">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-[8px]">
                    <span class="text-[12px] font-bold text-[#0369a1]">📡 全部信号</span>
                    <span class="text-[10px] text-[#666] bg-white border border-[#bae6fd] px-[6px] py-[1px] rounded">聚合 4 策略已扫数据</span>
                    <span class="text-[10px] text-[#94a3b8] truncate min-w-0">· 按星级排序 · 多源同票合并查看</span>
                </div>
                <div class="text-[11px] text-[#475569] leading-[18px] mt-[3px]">
                    <span class="font-semibold text-[#666]">用法：</span>
                    点「刷新全部」并行扫 4 个策略，或先到各 tab 单独跑
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">提示：</span>
                    ⭐⭐⭐⭐ 顶部 — 优先实盘；⭐⭐⭐ 次之；⭐⭐ / ⭐ 仅参考
                </div>
            </div>
            <!-- 刷新当前 —— 只重扫表里已有的 code 子集（快） -->
            <button @click="refreshAllSignalsSubset"
                    :disabled="allSignalsRefreshing || !allSignalsCodes.length"
                    :title="!allSignalsCodes.length ? '当前列表为空，先到各 tab 跑一次扫描'
                        : allSignalsRefreshing ? '正在扫描中...'
                        : `只重扫当前列表的 ${allSignalsCodes.length} 只票（~30 秒），不重扫全市场`"
                    class="text-[12px] px-[10px] py-[4px] rounded-[4px] border transition shrink-0 flex items-center gap-[5px]"
                    :class="allSignalsRefreshing
                        ? 'border-[#bae6fd] bg-[#eff6ff] text-[#0369a1] cursor-wait'
                        : !allSignalsCodes.length
                          ? 'border-[#e5e7eb] text-[#cbd5e1] bg-white cursor-not-allowed'
                          : 'border-[#0369a1] text-[#0369a1] bg-white hover:bg-[#eff6ff] font-semibold'">
                <span :class="allSignalsRefreshing ? 'animate-spin inline-block' : ''">↻</span>
                <span>{{ allSignalsRefreshing ? '扫描中...' : `刷新当前 (${allSignalsCodes.length})` }}</span>
            </button>
            <!-- 全市场重扫 —— 慢，独立次要按钮 -->
            <button @click="refreshAllSignalsFullMarket"
                    :disabled="allSignalsRefreshing"
                    title="并行重扫整个 A 股市场（每个 source ~5-8 分钟）—— 用于发现新信号"
                    class="text-[12px] px-[10px] py-[4px] rounded-[4px] border transition shrink-0 flex items-center gap-[5px]"
                    :class="allSignalsRefreshing
                        ? 'border-[#e5e7eb] bg-white text-[#cbd5e1] cursor-not-allowed'
                        : 'border-[#94a3b8]/40 text-[#475569] bg-white hover:bg-[#f1f5f9] hover:border-[#475569]'">
                <svg class="w-[12px] h-[12px]" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607Z" />
                </svg>
                <span>全市场扫</span>
            </button>
            <KCacheBadge
                :cacheStatus="cacheStatus" :cacheStatusChecking="cacheStatusChecking"
                :cacheStatusHint="cacheStatusHint" :shouldPromptDownload="shouldPromptDownload"
                :downloaderPhase="downloader.phase.value"
                :downloadedCount="downloader.downloaded.value" :failedCount="downloader.failed.value"
                :totalToDownload="downloader.totalToDownload.value"
                :scanning="market.scanning.value"
                @download="downloadTodayKlines" @cancel="downloader.cancel()" />
        </div>

        <!-- 主升突破 -->
        <div v-if="scanTab === 'recent_market'"
             class="flex items-center gap-[12px] px-[14px] py-[8px] bg-gradient-to-r from-[#fef2f2] to-[#fff] border-b border-[#fecaca] text-[11px] shrink-0">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-[8px] mb-[3px]">
                    <span class="text-[12px] font-bold text-[#dc2626]">📈 主升突破</span>
                    <span class="text-[10px] text-[#666] bg-white border border-[#fecaca] px-[6px] py-[1px] rounded">三维启动检测</span>
                    <span class="text-[10px] text-[#94a3b8] truncate min-w-0">· 已突破态 · 机器学习二次过滤</span>
                </div>
                <div class="text-[11px] text-[#475569] leading-[18px]">
                    <span class="font-semibold text-[#666]">形态：</span>
                    <span>蓄势 20-120 天 → 试盘（长影线 + 2.5 倍量）→ 突破（涨幅 ≥5% + 1.3-6 倍量）</span>
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">入场：</span>突破当日次日早盘
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">出场：</span>跌破 10 日均线 / 蓄势下沿
                </div>
                <div class="text-[10px] text-[#475569] leading-[16px] mt-[3px]">
                    <span class="font-semibold">⭐ 星级体系</span>（4 段实测）：
                    <span class="bg-[#dc2626] text-white px-[3px] rounded font-bold">⭐⭐⭐</span> N+1 medium 平均 <span class="text-[#dc2626] font-bold">76%</span> 跨段 62-91% ·
                    <span class="bg-[#dc2626] text-white opacity-80 px-[3px] rounded font-bold">⭐⭐</span> N+1 strong 平均 63% 跨段 40-91%（看 regime）·
                    <span class="bg-[#fde68a] text-[#92400e] px-[3px] rounded font-bold">⭐</span> sector/ML 辅助
                </div>
            </div>
            <KCacheBadge
                :cacheStatus="cacheStatus" :cacheStatusChecking="cacheStatusChecking"
                :cacheStatusHint="cacheStatusHint" :shouldPromptDownload="shouldPromptDownload"
                :downloaderPhase="downloader.phase.value"
                :downloadedCount="downloader.downloaded.value" :failedCount="downloader.failed.value"
                :totalToDownload="downloader.totalToDownload.value"
                :scanning="market.scanning.value"
                @download="downloadTodayKlines" @cancel="downloader.cancel()" />
        </div>

        <!-- 突破前夜 -->
        <div v-if="scanTab === 'breakout_eve'"
             class="flex items-center gap-[12px] px-[14px] py-[8px] bg-gradient-to-r from-[#fffbeb] to-[#fff] border-b border-[#fde68a] text-[11px] shrink-0">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-[8px] mb-[3px]">
                    <span class="text-[12px] font-bold text-[#b45309]">🌅 突破前夜</span>
                    <span class="text-[10px] text-[#666] bg-white border border-[#fde68a] px-[6px] py-[1px] rounded">三维启动检测（蓄势/试盘阶段）</span>
                    <span class="text-[10px] text-[#94a3b8] truncate min-w-0">· 蓄势埋伏 · 等待突破触发</span>
                </div>
                <div class="text-[11px] text-[#475569] leading-[18px]">
                    <span class="font-semibold text-[#666]">筛选：</span>
                    <span>仍处于蓄势中 / 试盘后未突破 · 距突破位 ≤5%（临门一脚）· 蓄势天数 ≤80 天（不要长期卡死的票）</span>
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">用法：</span>周末复盘建观察池，把突破位设成价格预警
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">优势：</span>建仓成本低 + 风险收益比 1:5-10
                </div>
            </div>
            <KCacheBadge
                :cacheStatus="cacheStatus" :cacheStatusChecking="cacheStatusChecking"
                :cacheStatusHint="cacheStatusHint" :shouldPromptDownload="shouldPromptDownload"
                :downloaderPhase="downloader.phase.value"
                :downloadedCount="downloader.downloaded.value" :failedCount="downloader.failed.value"
                :totalToDownload="downloader.totalToDownload.value"
                :scanning="market.scanning.value"
                @download="downloadTodayKlines" @cancel="downloader.cancel()" />
        </div>

        <!-- 龙回头 -->
        <div v-if="scanTab === 'dragon_return'"
             class="flex items-center gap-[12px] px-[14px] py-[8px] bg-gradient-to-r from-[#fff1f2] to-[#fff] border-b border-[#fecdd3] text-[11px] shrink-0">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-[8px] mb-[3px]">
                    <span class="text-[12px] font-bold text-[#9f1239]">🐉 龙回头</span>
                    <span class="text-[10px] text-[#666] bg-white border border-[#fecdd3] px-[6px] py-[1px] rounded">龙回头检测</span>
                    <span class="text-[10px] text-[#94a3b8] truncate min-w-0">· 妖股复活 · 深回踩反包</span>
                </div>
                <div class="text-[11px] text-[#475569] leading-[18px]">
                    <span class="font-semibold text-[#666]">形态：</span>
                    <span>15 天内 ≥3 个涨停（含 3 天内 2 板紧密簇）→ 高点回踩 15-30% → 5 天振幅收敛 ≤10% → 启动阳线（量比 ≥1.7 倍 + 实体 ≥50% 或涨幅 ≥4%）</span>
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">入场：</span>启动阳线收盘价
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">止损：</span>回踩低点下方约 3%
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">适合：</span>题材龙头二波抄底
                </div>
            </div>
            <KCacheBadge
                :cacheStatus="cacheStatus" :cacheStatusChecking="cacheStatusChecking"
                :cacheStatusHint="cacheStatusHint" :shouldPromptDownload="shouldPromptDownload"
                :downloaderPhase="downloader.phase.value"
                :downloadedCount="downloader.downloaded.value" :failedCount="downloader.failed.value"
                :totalToDownload="downloader.totalToDownload.value"
                :scanning="market.scanning.value"
                @download="downloadTodayKlines" @cancel="downloader.cancel()" />
        </div>

        <!-- 连板游资 -->
        <div v-if="scanTab === 'limit_up_relay'"
             class="flex items-center gap-[12px] px-[14px] py-[8px] bg-gradient-to-r from-[#fff7ed] to-[#fff] border-b border-[#fed7aa] text-[11px] shrink-0">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-[8px] mb-[3px]">
                    <span class="text-[12px] font-bold text-[#9a3412]">⚡ 连板游资</span>
                    <span class="text-[10px] text-[#666] bg-white border border-[#fed7aa] px-[6px] py-[1px] rounded">连板游资接力</span>
                    <span class="text-[10px] text-[#94a3b8] truncate min-w-0">· 涨停板梯队 · 游资抢筹</span>
                </div>
                <div class="text-[11px] text-[#475569] leading-[18px]">
                    <span class="font-semibold text-[#666]">条件：</span>
                    <span>今日触及涨停板 · 三种形态（连板接力 / 反包确认 / 摸板试盘）· 妖股基因（前 5 日板数加分）</span>
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">范围：</span>主板 + 中小板（排除 ST / 科创 / 创业 / 次新股不足 60 个交易日）
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="font-semibold text-[#666]">适合：</span>超短打板（持有 1-3 天）
                    <span class="mx-[6px] text-[#cbd5e1]">·</span>
                    <span class="text-[#dc2626] font-bold">⚠ 高风险高回报</span>
                </div>
            </div>
            <KCacheBadge
                :cacheStatus="cacheStatus" :cacheStatusChecking="cacheStatusChecking"
                :cacheStatusHint="cacheStatusHint" :shouldPromptDownload="shouldPromptDownload"
                :downloaderPhase="downloader.phase.value"
                :downloadedCount="downloader.downloaded.value" :failedCount="downloader.failed.value"
                :totalToDownload="downloader.totalToDownload.value"
                :scanning="market.scanning.value"
                @download="downloadTodayKlines" @cancel="downloader.cancel()" />
        </div>

        <!-- 下载进度条（所有扫描器都可见，因为下载是全局操作）-->
        <div v-if="downloader.phase.value === 'downloading'"
             class="px-[16px] py-[6px] border-b border-[#f0f0f0] bg-[#eff6ff]">
            <div class="flex items-center justify-between text-[11px] mb-[4px]">
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

        <!-- ========== 近期发车工具栏（仅 recent_market sub-tab）========== -->
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
            <span v-if="market.lastScanAt.value"
                  class="text-[10px] text-[#94a3b8] tabular-nums shrink-0 ml-[8px]"
                  :title="`上次扫描 ${lastScanHint}`">
                扫于 {{ lastScanHint }}
            </span>
            <div class="ml-auto flex items-center gap-[8px] shrink-0">
                <button v-if="!market.scanning.value"
                        @click="market.scan()"
                        :disabled="!!strategyDisabledByRegime"
                        :title="strategyDisabledByRegime || ''"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0
                               disabled:opacity-40 disabled:cursor-not-allowed">
                    {{ strategyDisabledByRegime ? '已暂停' : '重新扫描' }}
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

        <!-- ========== 突破前夜工具栏 ========== -->
        <div v-if="scanTab === 'breakout_eve'"
             class="h-[44px] flex items-center px-[14px] border-b border-[#e5e7eb] bg-white shrink-0 gap-[8px]">
            <span class="text-[12px] text-[#666] shrink-0">
                突破前夜
                <span v-if="eve.results.value.length" class="text-[#dc2626] font-bold tabular-nums">{{ eve.results.value.length }}</span>
                <span v-else class="text-[#94a3b8]">—</span>
                只
            </span>
            <span class="text-[10px] text-[#94a3b8] shrink-0">
                · Stage 1/2 蓄势态 · 距突破 ≤5% · 蓄势 ≤80 根
            </span>
            <span v-if="eve.lastScanAt.value" class="text-[10px] text-[#94a3b8] tabular-nums shrink-0 ml-[8px]">
                · 扫于 {{ eveLastScanHint }}
            </span>
            <div class="ml-auto flex items-center gap-[8px] shrink-0">
                <button v-if="!eve.scanning.value"
                        @click="eve.scan()"
                        :title="currentRegime === 'strong' ? '强势市：距突破≤8% 放宽' :
                                currentRegime === 'weak'   ? '弱势市：距突破≤3% 收紧（埋伏机会）' :
                                currentRegime === 'breakdown' ? '破位市：底部埋伏黄金期' : ''"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0">
                    重新扫描
                </button>
                <button v-else
                        @click="eve.cancel()"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#854d0e]/40
                               text-[#854d0e] bg-[#fef3c7] hover:bg-[#fde68a] transition shrink-0">
                    取消（{{ eve.scanned.value }}/{{ eve.total.value }}）
                </button>
            </div>
        </div>

        <!-- 突破前夜进度条 -->
        <div v-if="scanTab === 'breakout_eve' && eve.scanning.value"
             class="px-[16px] py-[8px] border-b border-[#f0f0f0] bg-[#fafafa]">
            <div class="flex items-center justify-between text-[11px] mb-[5px]">
                <span class="text-[#475569]">
                    扫描中 <span class="font-bold tabular-nums text-[#dc2626]">{{ eve.scanned.value }}</span>
                    / {{ eve.total.value }}
                    <span v-if="eve.currentCode.value" class="ml-[6px] text-[#94a3b8] font-mono">{{ eve.currentCode.value }}</span>
                </span>
                <span class="text-[#94a3b8]">{{ eve.progressPct.value }}%</span>
            </div>
            <div class="h-[3px] bg-[#e5e7eb] rounded-full overflow-hidden">
                <div class="h-full bg-[#dc2626] transition-all duration-200"
                     :style="{ width: eve.progressPct.value + '%' }"></div>
            </div>
        </div>

        <!-- 突破前夜批量工具栏 -->
        <div v-if="scanTab === 'breakout_eve' && eve.sortedResults.value.length"
             class="px-[16px] py-[6px] border-b border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[10px] text-[11px]">
            <span class="text-[#666]">
                已选
                <span class="font-bold tabular-nums" :class="eveSelectedCodes.size ? 'text-[#dc2626]' : 'text-[#999]'">{{ eveSelectedCodes.size }}</span>
                / {{ eve.sortedResults.value.length }}
            </span>
            <button @click="eveBatchSaveToCandidatePool" :disabled="!eveSelectedCodes.size || eveBatchProcessing"
                    class="text-[11px] px-[10px] py-[3px] rounded-[4px] border border-[#f59e0b]/40
                           text-[#f59e0b] bg-white hover:bg-[#fffbeb] hover:border-[#f59e0b]
                           disabled:opacity-40 disabled:cursor-not-allowed transition">
                ☆ 批量加候选池
            </button>
            <button @click="eveBatchAddToWatchlist" :disabled="!eveSelectedCodes.size || eveBatchProcessing"
                    class="text-[11px] px-[10px] py-[3px] rounded-[4px] border border-[#dc2626]/40
                           text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626]
                           disabled:opacity-40 disabled:cursor-not-allowed transition">
                + 批量加自选
            </button>
            <span v-if="eveBatchProcessing" class="text-[#dc2626] font-semibold animate-pulse">处理中...</span>
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
                        :disabled="!!strategyDisabledByRegime"
                        :title="strategyDisabledByRegime || ''"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0
                               disabled:opacity-40 disabled:cursor-not-allowed">
                    {{ strategyDisabledByRegime ? '已暂停' : '重新扫描' }}
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
                        :disabled="!!strategyDisabledByRegime"
                        :title="strategyDisabledByRegime || ''"
                        class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                               text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626] transition shrink-0
                               disabled:opacity-40 disabled:cursor-not-allowed">
                    {{ strategyDisabledByRegime ? '已暂停' : '重新扫描' }}
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

            <!-- ========== 全部信号 聚合 tab ========== -->
            <div v-if="scanTab === 'all_signals'">
                <!-- source filter chips -->
                <div class="px-[14px] py-[8px] border-b border-[#e5e7eb] bg-[#fafafa] flex items-center gap-[6px] sticky top-0 z-20">
                    <span class="text-[11px] text-[#666]">来源筛选：</span>
                    <button v-for="f in [
                        { k: 'all',             label: '全部',     cls: 'bg-[#1e293b] text-white' },
                        { k: 'main_breakout',   label: '主升突破', cls: 'bg-[#cffafe] text-[#155e75] border border-[#a5f3fc]' },
                        { k: 'breakout_eve',    label: '突破前夜', cls: 'bg-[#fef3c7] text-[#92400e] border border-[#fde68a]' },
                        { k: 'dragon_return',   label: '龙回头',   cls: 'bg-[#fee2e2] text-[#991b1b] border border-[#fecaca]' },
                        { k: 'limit_up_relay',  label: '连板游资', cls: 'bg-[#ffedd5] text-[#9a3412] border border-[#fed7aa]' },
                    ]" :key="f.k"
                            @click="allSignalsSourceFilter = f.k"
                            :class="['text-[11px] px-[8px] py-[3px] rounded transition cursor-pointer',
                                    allSignalsSourceFilter === f.k
                                        ? (f.k === 'all' ? f.cls + ' font-bold' : f.cls + ' font-bold ring-1 ring-offset-1 ring-[#dc2626]/40')
                                        : 'bg-white border border-[#e5e7eb] text-[#666] hover:bg-[#f1f5f9]']">
                        {{ f.label }} {{ allSignalsCounts[f.k] }}
                    </button>
                    <span class="ml-auto text-[10px] text-[#94a3b8]">⭐⭐⭐⭐ {{ allSignals.filter(r => r.starLevel >= 4).length }} · ⭐⭐⭐ {{ allSignals.filter(r => r.starLevel === 3).length }} · ⭐⭐ {{ allSignals.filter(r => r.starLevel === 2).length }} · ⭐ {{ allSignals.filter(r => r.starLevel === 1).length }}</span>
                </div>

                <div v-if="!allSignals.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    各个 tab 都没扫过数据。<br/>
                    <span class="text-[11px]">先去「主升突破」等 tab 点扫描，再回来这里聚合查看</span>
                </div>
                <table v-else class="w-full text-left border-collapse whitespace-nowrap text-[12px]">
                    <thead class="sticky top-[44px] bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                        <tr>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]">星级</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[80px]">来源</th>
                            <th class="px-[12px] py-[8px] font-normal w-[160px]">股票</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[80px]">板块</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]">龙虎</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">现价</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">入场参考</th>
                            <th class="px-[8px] py-[8px] font-normal text-left">额外</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in allSignals" :key="r.sourceKey + '-' + r.code"
                            @dblclick="viewAggChart(r)"
                            class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer"
                            title="双击看 K 线">
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.starLevel === 4"
                                      class="inline-flex items-center justify-center px-[4px] py-[1px] rounded text-[10px] font-bold bg-[#7c2d12] text-white">⭐⭐⭐⭐</span>
                                <span v-else-if="r.starLevel === 3"
                                      class="inline-flex items-center justify-center px-[5px] py-[1px] rounded text-[10px] font-bold bg-[#dc2626] text-white">⭐⭐⭐</span>
                                <span v-else-if="r.starLevel === 2"
                                      class="inline-flex items-center justify-center px-[5px] py-[1px] rounded text-[10px] font-bold bg-[#dc2626] text-white opacity-70">⭐⭐</span>
                                <span v-else-if="r.starLevel === 1"
                                      class="inline-flex items-center justify-center px-[5px] py-[1px] rounded text-[10px] font-bold bg-[#fde68a] text-[#92400e]">⭐</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span class="inline-flex items-center px-[5px] py-[1px] rounded text-[10px] font-semibold border" :class="r.sourceCls">{{ r.sourceLabel }}</span>
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded">已自</span>
                                    <span v-if="isInJournal(r.code)" class="shrink-0 text-[9px] font-bold text-white bg-[#15803d] px-[3px] rounded">📝持仓</span>
                                </div>
                                <div class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</div>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <div v-if="r.sectorName && typeof r.sectorScore === 'number'"
                                     :title="`${r.sectorName} · 强度 ${r.sectorScore.toFixed(1)}/100`"
                                     class="flex flex-col items-center leading-tight">
                                    <span class="text-[10px] truncate max-w-[72px]" :class="r.sectorScore >= 70 ? 'text-[#dc2626] font-semibold' : 'text-[#94a3b8]'">{{ r.sectorName }}</span>
                                    <span class="text-[9px] tabular-nums" :class="r.sectorScore >= 70 ? 'text-[#dc2626]' : 'text-[#94a3b8]'">{{ r.sectorScore.toFixed(0) }}</span>
                                </div>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.lhbInWindow === 1"
                                      :title="`30 天 ${r.lhbCount ?? '?'} 次上榜 · 净 ${fmtLhbNet(r.lhbNetBuySum)}`"
                                      class="inline-flex items-center px-[4px] py-[1px] rounded text-[10px] font-bold bg-[#7c2d12] text-white">🔥{{ r.lhbCount }}</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums">{{ r.currentPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.entryPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-[11px] text-[#475569]">
                                <span v-for="(fn, key) in r.extra" :key="key" class="mr-[8px]">
                                    <span class="text-[#94a3b8]">{{ key }}:</span> {{ fn() }}
                                </span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <RowActionMenu>
                                    <button @click="openAddJournalForAggRow(r, $event)">📝 加日志</button>
                                    <button @click="viewAggChart(r)">👁 看 K 线</button>
                                </RowActionMenu>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

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
                            <th class="px-[8px] py-[8px] font-normal text-center w-[52px]" title="星级（基于真实 holdout 验证 + LHB 数据）">星级</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]" title="龙虎榜：30 天上榜次数 + 净买（万元）· holdout 实测 +14pp 胜率">龙虎</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[80px]" title="所在最强板块名称 + 板块强度评分 0-100">板块</th>
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
                                <input type="checkbox" :checked="marketSelectedCodes.has(r.code)"
                                       @change="toggleMarketRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <!-- ⭐⭐⭐⭐ confirm + LHB 双重确认（最稀缺最强）-->
                                <span v-if="getTradeStarLevel(r) >= 4"
                                      title="⭐⭐⭐⭐ N+1 确认 + 龙虎榜（主力痕迹 + 站稳）· 月 1-2 个，最强信号"
                                      class="inline-flex items-center justify-center px-[4px] py-[1px] rounded-[3px] text-[10px] font-bold bg-[#7c2d12] text-white">
                                    ⭐⭐⭐⭐
                                </span>
                                <!-- ⭐⭐⭐ confirm OR LHB（holdout 74.4%）-->
                                <span v-else-if="getTradeStarLevel(r) >= 3"
                                      :title="`⭐⭐⭐ ${r.breakoutConfirm === 'strong' || r.breakoutConfirm === 'medium' ? 'N+1 确认' : ''}${r.lhbInWindow === 1 ? ' 龙虎榜' : ''}` + ' · holdout 74.4% (n=43)'"
                                      class="inline-flex items-center justify-center px-[5px] py-[1px] rounded-[3px] text-[10px] font-bold bg-[#dc2626] text-white">
                                    ⭐⭐⭐
                                </span>
                                <!-- ⭐⭐ medium 单独 -->
                                <span v-else-if="getTradeStarLevel(r) >= 2"
                                      title="⭐⭐ N+1 中等确认 · 4 段稳定 62-91%"
                                      class="inline-flex items-center justify-center px-[5px] py-[1px] rounded-[3px] text-[10px] font-bold bg-[#dc2626] text-white opacity-70">
                                    ⭐⭐
                                </span>
                                <span v-else-if="getTradeStarLevel(r) >= 1"
                                      :title="`⭐ ${r.breakoutConfirm === 'strong' ? 'strong confirm' : r.mlScore >= ML_STAR_THRESHOLD ? 'ML ' + (r.mlScore*100).toFixed(0) + '%' : 'sector ' + r.sectorScore?.toFixed?.(0)}`"
                                      class="inline-flex items-center justify-center px-[5px] py-[1px] rounded-[3px] text-[10px] font-bold bg-[#fde68a] text-[#92400e]">
                                    ⭐
                                </span>
                                <span v-else-if="typeof r.mlScore === 'number'"
                                      :title="`ML ${(r.mlScore*100).toFixed(0)}%`"
                                      class="text-[10px] text-[#94a3b8] tabular-nums">{{ (r.mlScore*100).toFixed(0) }}</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.lhbInWindow === 1"
                                      :title="`龙虎榜：30 天 ${r.lhbCount ?? '?'} 次上榜，净 ${fmtLhbNet(r.lhbNetBuySum)}${r.daysSinceLastLhb != null ? ' · 距上次 ' + r.daysSinceLastLhb + ' 天' : ''}`"
                                      :class="['inline-flex items-center justify-center px-[4px] py-[1px] rounded text-[10px] font-bold',
                                              r.lhbNetBuySum > 0 ? 'bg-[#7c2d12] text-white' : 'bg-[#fef3c7] text-[#92400e]']">
                                    🔥{{ r.lhbCount }}
                                </span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <div v-if="r.sectorName && typeof r.sectorScore === 'number'"
                                     :title="`所在最强板块: ${r.sectorName} · 强度 ${r.sectorScore.toFixed(1)}/100`"
                                     class="flex flex-col items-center leading-tight">
                                    <span class="text-[10px] truncate max-w-[72px]"
                                          :class="r.sectorScore >= 70 ? 'text-[#dc2626] font-semibold' : r.sectorScore >= 50 ? 'text-[#666]' : 'text-[#94a3b8]'">
                                        {{ r.sectorName }}
                                    </span>
                                    <span class="text-[9px] tabular-nums"
                                          :class="r.sectorScore >= 70 ? 'text-[#dc2626]' : 'text-[#94a3b8]'">
                                        {{ r.sectorScore.toFixed(0) }}
                                    </span>
                                </div>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
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
                                    <!-- Week 2 Day 1: N+1 突破确认徽章 -->
                                    <span v-if="r.breakoutConfirm === 'strong'"
                                          title="N+1 强确认：高开 ≥1% + 收阳 + 不破中点"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#dc2626] px-[4px] rounded-[2px]">✓✓</span>
                                    <span v-else-if="r.breakoutConfirm === 'medium'"
                                          title="N+1 中等确认：平开微高开 + 收阳 + 量能维持"
                                          class="shrink-0 text-[9px] font-bold text-[#b45309] bg-[#fef3c7] border border-[#fde68a] px-[4px] rounded-[2px]">✓</span>
                                    <span v-else-if="r.breakoutConfirm === 'pending'"
                                          title="N+1 待确认：突破日为最新一根，明日确认"
                                          class="shrink-0 text-[9px] font-bold text-[#475569] bg-[#f1f5f9] px-[4px] rounded-[2px]">…</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                    <span v-if="isInJournal(r.code)" title="已在交易日志（持仓中）" class="shrink-0 text-[9px] font-bold text-white bg-[#15803d] px-[3px] rounded-[2px]">📝持仓</span>
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
                                <RowActionMenu>
                                    <button @click="saveMarketRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)">
                                        {{ isInCandidatePool(r.code) ? '★ 已在候选池' : '☆ 加入候选池' }}
                                    </button>
                                    <button @click="addToTradeJournal(r, $event)">📝 加日志</button>
                                    <button @click="addMarketRowToWatchlist(r, $event)">
                                        + 加自选<span v-if="isInWatchlist(r.code)" class="text-[10px] text-[#94a3b8] ml-1">（已在某分组）</span>
                                    </button>
                                    <button @click="viewMarketChart(r)">👁 看 K 线</button>
                                </RowActionMenu>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- 突破前夜表格 -->
            <div v-if="scanTab === 'breakout_eve'">
                <div v-if="eve.scanning.value && !eve.results.value.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    全市场扫描中... 蓄势态识别约 5-10 分钟
                </div>
                <div v-else-if="eve.lastError.value" class="py-[80px] text-center text-[#dc2626] text-[12px]">
                    扫描失败: {{ eve.lastError.value }}
                </div>
                <div v-else-if="!eve.results.value.length && !eve.scanning.value" class="py-[80px] text-center text-[#aaa] text-[13px]">
                    还没扫过 — 点击右上方"重新扫描"开始<br/>
                    <span class="text-[11px]">建议周末复盘时跑一次，建埋伏观察池</span>
                </div>
                <table v-else class="w-full text-left border-collapse whitespace-nowrap text-[12px]">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                        <tr>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[32px]">
                                <input type="checkbox" :checked="allEveSelectableSelected" @change="toggleAllEve"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                            </th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[52px]">阶段</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]" title="蓄势质量 0-100（振幅收窄+量能萎缩+MA粘合+主力潜伏 4 维评分）">质量</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]" title="MACD 即将金叉评分 0-100（DIF/DEA 收敛 → 突破前兆）">MACD</th>
                            <th class="px-[12px] py-[8px] font-normal w-[160px]">股票</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">现价</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]" title="s1Upper 突破触发位">突破位</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]" title="(现价 - 突破位) / 突破位 × 100%">距突破</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]" title="蓄势已持续根数">蓄势天</th>
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]" title="s1Lower 蓄势下沿（止损位）">止损位</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[140px]">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in eve.sortedResults.value" :key="r.code"
                            @dblclick="viewEveChart(r)"
                            class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                            :class="eveSelectedCodes.has(r.code) ? 'bg-[#fff5f5]' : ''"
                            title="双击看 K 线">
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <input type="checkbox" :checked="eveSelectedCodes.has(r.code)"
                                       @change="toggleEveRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span :class="['inline-flex items-center justify-center px-[6px] py-[1px] rounded-[3px] text-[10px] font-bold',
                                              r.event.currentStage === 2
                                                  ? 'bg-[#fef3c7] text-[#b45309]'
                                                  : 'bg-[#f1f5f9] text-[#64748b]']"
                                      :title="r.event.currentStage === 2 ? 'Stage 2 试盘后等突破' : 'Stage 1 蓄势中'">
                                    S{{ r.event.currentStage }}
                                </span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="typeof r.qualityScore === 'number'"
                                      :title="`蓄势质量 ${r.qualityScore}/100：振幅${r.qualityBreakdown?.amplitudeShrink}+量能${r.qualityBreakdown?.volumeShrink}+MA粘合${r.qualityBreakdown?.maAdhesion}+潜伏${r.qualityBreakdown?.probeFootprint}`"
                                      :class="['text-[10px] font-bold tabular-nums',
                                              r.qualityScore >= 70 ? 'text-[#dc2626]' :
                                              r.qualityScore >= 50 ? 'text-[#b45309]' :
                                              'text-[#94a3b8]']">{{ r.qualityScore }}</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.macdImminence > 50"
                                      :title="`MACD 金叉前兆 ${r.macdImminence}/100（即将金叉）`"
                                      class="text-[10px] font-bold tabular-nums text-[#dc2626]">{{ r.macdImminence }}🔥</span>
                                <span v-else-if="r.macdImminence > 25"
                                      :title="`MACD ${r.macdImminence}/100（开始收敛）`"
                                      class="text-[10px] tabular-nums text-[#b45309]">{{ r.macdImminence }}</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">{{ r.macdImminence || '—' }}</span>
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <!-- 突破前夜 星级（基于 quality + MACD + sector + LHB）-->
                                    <span v-if="r.starLevel >= 4"
                                          title="⭐⭐⭐⭐ 蓄势 ≥70 + MACD 即将金叉 + 板块/LHB"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#7c2d12] px-[4px] rounded-[2px]">⭐⭐⭐⭐</span>
                                    <span v-else-if="r.starLevel === 3"
                                          title="⭐⭐⭐ 蓄势质量优秀 OR MACD+板块共振"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#dc2626] px-[4px] rounded-[2px]">⭐⭐⭐</span>
                                    <span v-else-if="r.starLevel === 2"
                                          title="⭐⭐ 蓄势 ≥50 + 距突破 ≤3%"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#dc2626] opacity-70 px-[4px] rounded-[2px]">⭐⭐</span>
                                    <span v-else-if="r.starLevel === 1"
                                          title="⭐ 单维信号"
                                          class="shrink-0 text-[9px] font-bold text-[#92400e] bg-[#fde68a] px-[4px] rounded-[2px]">⭐</span>
                                    <span v-if="r.lhbInWindow === 1"
                                          :title="`龙虎榜 ${r.lhbCount ?? '?'} 次 · 净 ${fmtLhbNet(r.lhbNetBuySum)}`"
                                          class="shrink-0 text-[9px] font-bold bg-[#7c2d12] text-white px-[3px] rounded-[2px]">🔥{{ r.lhbCount }}</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                    <span v-if="isInJournal(r.code)" title="已在交易日志（持仓中）" class="shrink-0 text-[9px] font-bold text-white bg-[#15803d] px-[3px] rounded-[2px]">📝持仓</span>
                                </div>
                                <div class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</div>
                            </td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums">{{ r.lastPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.event?.s1Upper?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-semibold"
                                :class="r.distanceToBreakPct == null ? 'text-[#999]'
                                    : Math.abs(r.distanceToBreakPct) < 1 ? 'text-[#dc2626]'
                                    : Math.abs(r.distanceToBreakPct) < 3 ? 'text-[#f59e0b]'
                                    : 'text-[#666]'">
                                {{ r.distanceToBreakPct != null ? r.distanceToBreakPct.toFixed(2) + '%' : '—' }}
                            </td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.consolidationBars ?? '—' }}d</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.event?.s1Lower?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <RowActionMenu>
                                    <button @click="saveEveRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)">
                                        {{ isInCandidatePool(r.code) ? '★ 已在候选池' : '☆ 加入候选池' }}
                                    </button>
                                    <button @click="openAddJournalForRow(r, '突破前夜')">📝 加日志</button>
                                    <button @click="viewEveChart(r)">👁 看 K 线</button>
                                </RowActionMenu>
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
                            <th class="px-[8px] py-[8px] font-normal text-right w-[80px]" title="深度甜区评分：12-18% 浅回踩=100，18-25%=70，25-30%=40">回踩</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]" title="位置因子：MA200 上方 + 距 60 日新高 < 30%（防长跌反弹陷阱）">位置</th>
                            <th class="px-[8px] py-[8px] font-normal text-center w-[60px]" title="探底试盘 K：回踩末期长下影 + 站稳，主力探底成功信号">探底</th>
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
                                <input type="checkbox" :checked="dragonSelectedCodes.has(r.code)"
                                       @change="toggleDragonRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <!-- 龙回头 星级（基于 position + probe + pullback + sector + LHB）-->
                                    <span v-if="r.starLevel >= 4"
                                          title="⭐⭐⭐⭐ 位置✓ + 探底✓ + 浅回踩（黄金组合）"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#7c2d12] px-[4px] rounded-[2px]">⭐⭐⭐⭐</span>
                                    <span v-else-if="r.starLevel === 3"
                                          title="⭐⭐⭐ 位置✓ + 探底✓ OR 浅回踩"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#dc2626] px-[4px] rounded-[2px]">⭐⭐⭐</span>
                                    <span v-else-if="r.starLevel === 2"
                                          title="⭐⭐ 浅回踩 OR 探底+板块"
                                          class="shrink-0 text-[9px] font-bold text-white bg-[#dc2626] opacity-70 px-[4px] rounded-[2px]">⭐⭐</span>
                                    <span v-else-if="r.starLevel === 1"
                                          title="⭐ 单维信号"
                                          class="shrink-0 text-[9px] font-bold text-[#92400e] bg-[#fde68a] px-[4px] rounded-[2px]">⭐</span>
                                    <span v-if="r.lhbInWindow === 1"
                                          :title="`龙虎榜 ${r.lhbCount ?? '?'} 次 · 净 ${fmtLhbNet(r.lhbNetBuySum)}`"
                                          class="shrink-0 text-[9px] font-bold bg-[#7c2d12] text-white px-[3px] rounded-[2px]">🔥{{ r.lhbCount }}</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                    <span v-if="isInJournal(r.code)" title="已在交易日志（持仓中）" class="shrink-0 text-[9px] font-bold text-white bg-[#15803d] px-[3px] rounded-[2px]">📝持仓</span>
                                </div>
                                <div class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</div>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span class="inline-flex items-center justify-center min-w-[22px] h-[18px] rounded-[3px] bg-[#fee2e2] text-[#991b1b] text-[11px] font-bold tabular-nums px-[5px]">{{ r.limitUpCount }}</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666]">{{ r.peakHigh?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-semibold"
                                :title="`回踩 ${r.pullbackPct?.toFixed?.(1)}% · 甜区评分 ${r.pullbackQuality}/100`"
                                :class="r.pullbackQuality >= 70 ? 'text-[#dc2626]' : r.pullbackQuality >= 40 ? 'text-[#854d0e]' : 'text-[#94a3b8]'">
                                -{{ r.pullbackPct?.toFixed?.(1) }}%
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.positionOk === true" title="MA200 上方 + 距 60 日新高 < 30%（位置好）"
                                      class="text-[12px] font-bold text-[#dc2626]">✓</span>
                                <span v-else-if="r.positionOk === false" :title="`距 60 日新高 ${r.distFrom60dHigh?.toFixed?.(0)}% · 位置不利`"
                                      class="text-[12px] text-[#94a3b8]">✗</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center">
                                <span v-if="r.probeOk" :title="`探底试盘 K @ ${r.probeTime}`"
                                      class="text-[11px] font-bold text-[#dc2626]">⛏</span>
                                <span v-else class="text-[10px] text-[#cbd5e1]">—</span>
                            </td>
                            <td class="px-[8px] py-[8px] text-center text-[11px] text-[#666] tabular-nums">{{ fmtMarketDate(r.ignitionTime) }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[#dc2626] font-semibold">{{ r.ignitionVr?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-bold text-[13px] text-[#111]">{{ r.lastClose?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[10px] text-[#94a3b8]">{{ r.stopLoss?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <RowActionMenu>
                                    <button @click="saveDragonRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)">
                                        {{ isInCandidatePool(r.code) ? '★ 已在候选池' : '☆ 加入候选池' }}
                                    </button>
                                    <button @click="openAddJournalForRow(r, '龙回头')">📝 加日志</button>
                                    <button @click="addDragonRowToWatchlist(r, $event)">
                                        + 加自选<span v-if="isInWatchlist(r.code)" class="text-[10px] text-[#94a3b8] ml-1">（已在某分组）</span>
                                    </button>
                                    <button @click="viewDragonChart(r)">👁 看 K 线</button>
                                </RowActionMenu>
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
                            <th @click="relay.setSort('height')"
                                class="px-[8px] py-[8px] font-normal text-center w-[80px] cursor-pointer select-none hover:text-[#dc2626] transition">
                                连板<span class="ml-[2px] text-[9px]">{{ relay.sortKey.value === 'height' ? (relay.sortDir.value === 'desc' ? '▼' : '▲') : '↕' }}</span>
                            </th>
                            <th @click="relay.setSort('boards5')"
                                class="px-[8px] py-[8px] font-normal text-center w-[60px] cursor-pointer select-none hover:text-[#dc2626] transition">
                                5天板<span class="ml-[2px] text-[9px]">{{ relay.sortKey.value === 'boards5' ? (relay.sortDir.value === 'desc' ? '▼' : '▲') : '↕' }}</span>
                            </th>
                            <th @click="relay.setSort('lastClose')"
                                class="px-[8px] py-[8px] font-normal text-right w-[80px] cursor-pointer select-none hover:text-[#dc2626] transition">
                                现价<span class="ml-[2px] text-[9px]">{{ relay.sortKey.value === 'lastClose' ? (relay.sortDir.value === 'desc' ? '▼' : '▲') : '↕' }}</span>
                            </th>
                            <th @click="relay.setSort('pctChange')"
                                class="px-[8px] py-[8px] font-normal text-right w-[80px] cursor-pointer select-none hover:text-[#dc2626] transition">
                                今日涨幅<span class="ml-[2px] text-[9px]">{{ relay.sortKey.value === 'pctChange' ? (relay.sortDir.value === 'desc' ? '▼' : '▲') : '↕' }}</span>
                            </th>
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
                                <input type="checkbox" :checked="relaySelectedCodes.has(r.code)"
                                       @change="toggleRelayRow(r.code, $event)"
                                       class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer disabled:cursor-not-allowed disabled:opacity-40">
                            </td>
                            <td class="px-[12px] py-[8px]">
                                <div class="flex items-center gap-[4px]">
                                    <span class="text-[13px] font-bold text-[#111] truncate">{{ r.name }}</span>
                                    <span v-if="r.isST" class="shrink-0 text-[9px] text-[#dc2626] bg-[#fee2e2] px-[3px] rounded-[2px]">ST</span>
                                    <span v-if="isInCandidatePool(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已收</span>
                                    <span v-if="isInWatchlist(r.code)" class="shrink-0 text-[9px] text-[#666] bg-[#e5e7eb] px-[3px] rounded-[2px]">已自</span>
                                    <span v-if="isInJournal(r.code)" title="已在交易日志（持仓中）" class="shrink-0 text-[9px] font-bold text-white bg-[#15803d] px-[3px] rounded-[2px]">📝持仓</span>
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
                            <td class="px-[8px] py-[8px] text-right tabular-nums font-bold text-[12px]"
                                :class="r.pctChange == null ? 'text-[#94a3b8]'
                                       : r.pctChange > 0 ? 'text-[#dc2626]'
                                       : r.pctChange < 0 ? 'text-[#1e40af]' : 'text-[#666]'">
                                <template v-if="r.pctChange != null">
                                    <span class="mr-[1px]">{{ r.pctChange > 0 ? '▲' : r.pctChange < 0 ? '▼' : '◆' }}</span>{{ (r.pctChange > 0 ? '+' : '') + r.pctChange.toFixed(2) + '%' }}
                                </template>
                                <template v-else>—</template>
                            </td>
                            <td class="px-[8px] py-[8px] text-right tabular-nums text-[10px] text-[#94a3b8]">{{ r.limitPrice?.toFixed?.(2) ?? '—' }}</td>
                            <td class="px-[8px] py-[8px] text-center" @click.stop>
                                <RowActionMenu>
                                    <button @click="saveRelayRowToCandidatePool(r, $event)" :disabled="isInCandidatePool(r.code)">
                                        {{ isInCandidatePool(r.code) ? '★ 已在候选池' : '☆ 加入候选池' }}
                                    </button>
                                    <button @click="openAddJournalForRow(r, '连板游资')">📝 加日志</button>
                                    <button @click="addRelayRowToWatchlist(r, $event)">
                                        + 加自选<span v-if="isInWatchlist(r.code)" class="text-[10px] text-[#94a3b8] ml-1">（已在某分组）</span>
                                    </button>
                                    <button @click="viewRelayChart(r)">👁 看 K 线</button>
                                </RowActionMenu>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

        </div>

        <!-- 加交易日志 modal -->
        <AddTradeJournalModal
            :open="journalModal.open"
            :prefill="journalModal.prefill"
            @close="closeJournalModal"
            @saved="(d) => { closeJournalModal(); loadJournalingCodes() }" />
    </div>
</template>
