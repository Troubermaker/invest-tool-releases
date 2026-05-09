<script setup>
/**
 * 候选池 View —— 找发车 / 找候选扫出来后用户 ⭐ 收藏的票，持续追踪买点。
 *
 * 跟 Watchlist 区别：
 *   - 自带「入选时的突破点位 / 黄金买点」snapshot，永远不变
 *   - 当前价 vs snapshot 自动算状态标签：
 *       等待中 / 临门一脚 / 已突破 / 进入买点区 / 已失效
 *   - 没有分组概念（候选池就是一个池）
 *   - 行情每 3 秒刷新，跟自选 / 持仓节奏一致
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import Sortable from 'sortablejs'
import { api } from '../api/client'
import { useSmartRefresh } from '../composables/useSmartRefresh'
import { openStockChart } from '../composables/useStockChart'
import { openAddToWatchlist } from '../composables/useAddToWatchlist'
import { pushSuccess, pushError } from '../composables/useNotifications'
import { confirmDialog } from '../composables/useConfirm'
import { useCandidateRefresh } from '../composables/useCandidateRefresh'
// 全市场扫描器已迁出到 Quant.vue（量化选股 view）
import {
    QUOTE_INTERVAL_ACTIVE,
    QUOTE_INTERVAL_HIDDEN,
} from '../config/refreshIntervals'

// ---------------- 数据 ----------------
const picks = ref([])           // [{id, code, name, saved_at, stage, save_price, ...}]
const quotes = ref({})          // {code: {price, changePct, ...}}
const loading = ref(false)

// 顶部 source tab：'all' | 'candidate'（找候选 Stage 1/2）| 'signal'（找发车 Stage 3）
const sourceTab = ref('all')

// 状态过滤 chip：跟来源 tab 独立，可叠加
const filter = ref('all')

// 把 source 字段归类成 'candidate' / 'signal' / 'other'。
// 兼容字段缺失或自定义来源时按 stage 兜底。
function categorizeSource(pick) {
    const src = pick?.source || ''
    if (src.includes('发车')) return 'signal'
    if (src.includes('候选')) return 'candidate'
    // 兜底：stage 3 = 发车，stage 1/2 = 候选
    if (pick?.stage === 3) return 'signal'
    return 'candidate'
}

// ---------------- 加载 ----------------
async function loadPicks() {
    loading.value = true
    try {
        const res = await api.listCandidatePicks()
        if (res.ok) picks.value = res.data || []
        else pushError(res.error || '加载候选池失败')
    } catch (e) {
        pushError(`加载候选池失败：${e}`)
    } finally {
        loading.value = false
    }
}

async function refreshQuotes() {
    if (!picks.value.length) return
    const codes = picks.value.map(p => p.code)
    try {
        const res = await api.getBatchQuotes(codes)
        if (res.ok && res.data) quotes.value = res.data
    } catch (e) { /* 静默 —— 失败不影响候选池 snapshot 展示 */ }
}

useSmartRefresh(refreshQuotes, {
    baseInterval: QUOTE_INTERVAL_ACTIVE,
    hiddenInterval: QUOTE_INTERVAL_HIDDEN,
    immediate: false,
})

// ---------------- 状态计算（lazy，不存 DB） ----------------
//
// 候选 vs 发车的状态推断逻辑不同：
//   - 候选：Stage 1/2 蓄势中等突破，关心"能不能发车"
//   - 发车：Stage 3 已突破，关心"突破是否被验证、是否回踩到买点"
// 共用 failed / buy_zone 两个状态码，其余各家各行。

// 状态色减色策略：默认灰底单色；关键"可介入"状态用红强调；"已失效"用删除线
// 不同状态主要靠文字标签 (临门一脚/已突破/进入买点等) 区分，不依赖独立色块
const STATE_CSS = {
    waiting:   'bg-[#f3f4f6] text-[#666]',                                  // 中性
    almost:    'bg-[#f3f4f6] text-[#dc2626] font-bold',                      // 临门一脚 - 红字提示
    launched:  'bg-[#fee2e2] text-[#b91c1c] font-bold',                      // 已突破 - 红底突出
    breakdown: 'bg-[#f3f4f6] text-[#854d0e] font-bold',                      // 假突破回落 - 棕字警示
    buy_zone:  'bg-[#fee2e2] text-[#b91c1c] font-bold',                      // 进入买点区 - 红底（同 launched）
    strong:    'bg-[#f3f4f6] text-[#dc2626]',                                // 强势上涨 - 红字
    failed:    'bg-[#f3f4f6] text-[#999] line-through decoration-[1px]',     // 已失效 - 灰删除
    unknown:   'bg-[#f3f4f6] text-[#bbb]',
}

function inferState(pick, currentPrice, category) {
    // Phase 5：detector 判定的形态状态优先（detector 综合考虑 MA / 量能 / exhaustion，
    // 比单纯价格-snapshot 比较更准确）。invalid / exhausted 直接覆盖。
    if (pick.formation_state === 'invalid') {
        return { code: 'failed', label: '形态作废', cls: STATE_CSS.failed }
    }
    if (pick.formation_state === 'exhausted') {
        return { code: 'breakdown', label: '衰竭警示', cls: STATE_CSS.breakdown }
    }

    if (currentPrice == null) return { code: 'unknown', label: '—', cls: STATE_CSS.unknown }
    const { break_level: brk, golden_price: gold, s1_lower: low } = pick

    // 共用：跌破 s1_lower 直接归"已失效"（蓄势结构 / 突破有效性都破坏）
    if (low != null && currentPrice < low) {
        return { code: 'failed', label: '已失效', cls: STATE_CSS.failed }
    }

    if (category === 'signal') {
        // 发车（Stage 3 已突破）—— 关心"突破是否仍有效 + 是否到回踩买点"
        // 假突破回落：当前价跌回 break_level 下方
        if (brk != null && currentPrice < brk) {
            return { code: 'breakdown', label: '假突破回落', cls: STATE_CSS.breakdown }
        }
        // 黄金回踩买点
        if (gold != null && Math.abs((currentPrice - gold) / gold * 100) < 2) {
            return { code: 'buy_zone', label: '进入买点区', cls: STATE_CSS.buy_zone }
        }
        // 强势上涨：突破后 > 10%（已经接近止盈区，过热）
        if (brk != null && (currentPrice - brk) / brk * 100 > 10) {
            return { code: 'strong', label: '强势上涨', cls: STATE_CSS.strong }
        }
        return { code: 'launched', label: '突破在途', cls: STATE_CSS.launched }
    }

    // 候选（Stage 1/2 蓄势）—— 关心"能不能发车"
    if (brk != null && currentPrice >= brk) {
        return { code: 'launched', label: '已突破', cls: STATE_CSS.launched }
    }
    if (brk != null && (brk - currentPrice) / brk * 100 < 1.5) {
        return { code: 'almost', label: '临门一脚', cls: STATE_CSS.almost }
    }
    if (gold != null && Math.abs((currentPrice - gold) / gold * 100) < 2) {
        return { code: 'buy_zone', label: '进入买点区', cls: STATE_CSS.buy_zone }
    }
    return { code: 'waiting', label: '等待中', cls: STATE_CSS.waiting }
}

// 来源 chip（行内紧凑显示）
function sourceChip(category) {
    if (category === 'signal') return { text: '发车', cls: 'bg-[#fee2e2] text-[#b91c1c] border-[#fecaca]' }
    return { text: '候选', cls: 'bg-[#fef3c7] text-[#92400e] border-[#fde68a]' }
}

const enrichedPicks = computed(() => {
    return picks.value.map(p => {
        const q = quotes.value[p.code]
        const cur = q?.price ?? null
        const category = categorizeSource(p)
        const state = inferState(p, cur, category)
        const distBreak = (p.break_level && cur)
            ? (cur - p.break_level) / p.break_level * 100
            : null
        const distGold = (p.golden_price && cur)
            ? (cur - p.golden_price) / p.golden_price * 100
            : null
        // 自入选以来涨幅（相对 save_price）
        const sinceSavePct = (p.save_price && cur)
            ? (cur - p.save_price) / p.save_price * 100
            : null
        return {
            ...p,
            category,
            currentPrice: cur,
            changePct: q?.changePct ?? null,
            state,
            distBreak,
            distGold,
            sinceSavePct,
            // Phase 5：detector 字段（DB 里来）
            peakGain: p.peak_gain_since_save,
            formationState: p.formation_state,
            lastRefreshedAt: p.last_refreshed_at,
            // Phase 6：二次买点
            secondaryEntryAt:    p.secondary_entry_at,
            secondaryEntryPrice: p.secondary_entry_price,
            // Phase 7：突破日（仅 stage 3 / rally / exhausted / invalid 有值）
            breakoutAt: p.breakout_at,
        }
    })
})

// ---------------- 列顺序自定义（saved picks 表中部 12 列可拖）----------------
// ---------------- 来源标签：识别每只票来自哪个扫描器 ----------------
// 跟 saveXxxToCandidatePool 里写的 source 字符串保持一致
// 用于：1) 在表格"来源"列显示彩色标签  2) 工具栏来源筛选
const SOURCE_DISPLAY_LIST = [
    { key: 'three_stage', label: '三维启动', match: src => /候选/.test(src),
      cls: 'bg-[#fef3c7] text-[#92400e] border-[#fde68a]' },
    { key: 'recent',      label: '近期发车', match: src => /发车/.test(src),
      cls: 'bg-[#cffafe] text-[#155e75] border-[#a5f3fc]' },
    { key: 'dragon',      label: '龙回头',   match: src => src === '龙回头',
      cls: 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca]' },
    { key: 'relay',       label: '连板游资', match: src => src === '连板游资',
      cls: 'bg-[#ffedd5] text-[#9a3412] border-[#fed7aa]' },
]
// 一只票可能被多个扫描器命中 → source 用 '+' 拼接（add_pick 合并）
// 返回所有命中的展示项数组（顺序保留：source 字段里出现的顺序）
function categorizeSourceLabels(pick) {
    const src = pick?.source || ''
    const parts = src.split('+').map(s => s.trim()).filter(Boolean)
    const result = []
    const seen = new Set()
    for (const part of parts) {
        const matched = SOURCE_DISPLAY_LIST.find(it => it.match(part))
        const item = matched || { key: 'other', label: '其它', cls: 'bg-[#f3f4f6] text-[#666] border-[#e5e7eb]' }
        if (!seen.has(item.key)) {
            result.push(item)
            seen.add(item.key)
        }
    }
    return result.length ? result : [{ key: 'other', label: '其它', cls: 'bg-[#f3f4f6] text-[#666] border-[#e5e7eb]' }]
}

// 固定列：checkbox / 股票（左）+ 操作（右）
// 可拖列：状态 / 形态 / 来源 / 现价 / ... / 突破日
const SAVED_COLUMNS_META = {
    state:        { label: '状态',     width: '100px', align: 'text-center' },
    formation:    { label: '形态',     width: '80px',  align: 'text-center', title: 'detector 判定的形态状态（刷新追踪后填充）' },
    source:       { label: '来源',     width: '110px', align: 'text-center', title: '入选时所用的扫描器（多 detector 命中时叠多标签）' },
    currentPrice: { label: '现价',     width: '80px',  align: 'text-right' },
    changePct:    { label: '今日%',    width: '80px',  align: 'text-right' },
    savePrice:    { label: '入选价',   width: '90px',  align: 'text-right' },
    breakLevel:   { label: '突破点位', width: '100px', align: 'text-right' },
    distBreak:    { label: '距突破',   width: '90px',  align: 'text-right' },
    goldenPrice:  { label: '黄金买点', width: '100px', align: 'text-right' },
    distGold:     { label: '距黄金',   width: '90px',  align: 'text-right' },
    sinceSavePct: { label: '入选以来', width: '90px',  align: 'text-right' },
    savedAt:      { label: '入选时间', width: '90px',  align: 'text-center' },
    breakoutAt:   { label: '突破日',   width: '90px',  align: 'text-center', title: '突破日 = detector 找到的最新 stage 3 K 线日期（仅突破后的票有值）' },
}
const SAVED_DEFAULT_COLUMN_ORDER = Object.keys(SAVED_COLUMNS_META)
const savedColumnOrder = ref([...SAVED_DEFAULT_COLUMN_ORDER])

async function loadSavedColumnOrder() {
    const res = await api.getUserPreference('candidate_pool_column_order')
    if (res.ok && Array.isArray(res.data) && res.data.length) {
        // 校验：只保留有效 key，再把新增列追加到末尾（兼容版本升级）
        const validSet = new Set(SAVED_DEFAULT_COLUMN_ORDER)
        const saved = res.data.filter(k => validSet.has(k))
        const missing = SAVED_DEFAULT_COLUMN_ORDER.filter(k => !saved.includes(k))
        savedColumnOrder.value = [...saved, ...missing]
    }
}
async function saveSavedColumnOrder() {
    await api.setUserPreference('candidate_pool_column_order', savedColumnOrder.value)
}

let savedHeaderSortable = null
function initSavedHeaderSortable() {
    const row = document.querySelector('.candidate-pool-header-row')
    if (!row) return
    if (savedHeaderSortable) savedHeaderSortable.destroy()
    savedHeaderSortable = Sortable.create(row, {
        animation: 200,
        filter: '.col-fixed',
        preventOnFilter: false,
        draggable: '.col-draggable',
        ghostClass: 'drag-ghost-col',
        onEnd: async () => {
            const ths = row.querySelectorAll('th.col-draggable')
            savedColumnOrder.value = Array.from(ths).map(th => th.dataset.colKey)
            await saveSavedColumnOrder()
        },
    })
}

// 计算"距突破 N 天"（用 K 线日期跟今天比，简化为日历天）
function daysSinceBreakout(breakoutAt) {
    if (!breakoutAt) return null
    const dStr = typeof breakoutAt === 'string' ? breakoutAt.slice(0, 10) : null
    if (!dStr) return null
    const breakDate = new Date(dStr)
    if (Number.isNaN(breakDate.getTime())) return null
    const today = new Date()
    const diffMs = today.getTime() - breakDate.getTime()
    return Math.max(0, Math.floor(diffMs / 86400000))
}

// 排序优先级 —— 候选 / 发车下分别有意义的次序
//   候选：临门一脚 → 已突破 → 进入买点 → 等待中 → 已失效
//   发车：进入买点 → 假突破回落（要警惕）→ 突破在途 → 强势 → 已失效
const STATE_RANK = {
    almost: 0, breakdown: 1, buy_zone: 2, launched: 3, strong: 4,
    waiting: 5, failed: 6, unknown: 7,
}

// 来源筛选（独立于 sourceTab）：'all' | 'three_stage' | 'recent' | 'dragon' | 'relay' | 'other'
const sourceFilter = ref('all')

// 先按 sourceTab 过滤 → 再按来源筛选 → 再按状态筛选
const tabFilteredPicks = computed(() => {
    if (sourceTab.value === 'all') return enrichedPicks.value
    return enrichedPicks.value.filter(p => p.category === sourceTab.value)
})

const sourceFilteredPicks = computed(() => {
    if (sourceFilter.value === 'all') return tabFilteredPicks.value
    // 多源命中：任一标签匹配即算命中
    return tabFilteredPicks.value.filter(
        p => categorizeSourceLabels(p).some(l => l.key === sourceFilter.value),
    )
})

const visiblePicks = computed(() => {
    let arr = sourceFilteredPicks.value
    if (filter.value !== 'all') {
        arr = arr.filter(p => p.state.code === filter.value)
    }
    return [...arr].sort((a, b) => {
        const ra = STATE_RANK[a.state.code] ?? 9
        const rb = STATE_RANK[b.state.code] ?? 9
        if (ra !== rb) return ra - rb
        const da = a.distBreak == null ? -Infinity : a.distBreak
        const db = b.distBreak == null ? -Infinity : b.distBreak
        return db - da
    })
})

// 来源筛选 chip 计数（基于 tabFilteredPicks，跟当前 sourceTab 联动）
// 多源命中的票会在每个对应来源 key 下都计 1（计数总和会 > 总票数，符合直觉）
const sourceFilterCounts = computed(() => {
    const c = { all: tabFilteredPicks.value.length, three_stage: 0, recent: 0, dragon: 0, relay: 0, other: 0 }
    for (const p of tabFilteredPicks.value) {
        for (const lbl of categorizeSourceLabels(p)) {
            if (lbl.key in c) c[lbl.key]++
        }
    }
    return c
})

// 仅显示当前 tab 下数量 > 0 的来源 chip
const visibleSourceChips = computed(() => {
    const all = [
        { key: 'all',         label: '全部来源' },
        { key: 'three_stage', label: '三维启动' },
        { key: 'recent',      label: '近期发车' },
        { key: 'dragon',      label: '龙回头'   },
        { key: 'relay',       label: '连板游资' },
        { key: 'other',       label: '其它'     },
    ]
    return all.filter(x => x.key === 'all' || (sourceFilterCounts.value[x.key] ?? 0) > 0)
})

// 来源 tab 计数
const sourceCounts = computed(() => {
    let candidate = 0, signal = 0
    for (const p of enrichedPicks.value) {
        if (p.category === 'signal') signal++
        else candidate++
    }
    return { all: enrichedPicks.value.length, candidate, signal }
})

// 状态过滤 chip 计数（仅在当前来源 tab 内统计）
const stateCounts = computed(() => {
    const c = {
        all: tabFilteredPicks.value.length,
        almost: 0, launched: 0, breakdown: 0, buy_zone: 0,
        waiting: 0, strong: 0, failed: 0,
    }
    for (const p of tabFilteredPicks.value) {
        if (p.state.code in c) c[p.state.code]++
    }
    return c
})

// 切换来源 tab 时重置状态过滤 + 来源筛选（不同 tab 语义不同，留着会困惑）
watch(sourceTab, () => {
    filter.value = 'all'
    sourceFilter.value = 'all'
})

// 当前 tab 下应展示哪些状态过滤 chip（避免出现 0 数量的"无效选项"）
const visibleStateChips = computed(() => {
    const all = [
        { key: 'all',       label: '全部' },
        { key: 'almost',    label: '临门一脚' },
        { key: 'launched',  label: sourceTab.value === 'signal' ? '突破在途' : '已突破' },
        { key: 'breakdown', label: '假突破回落' },
        { key: 'buy_zone',  label: '进入买点' },
        { key: 'strong',    label: '强势上涨' },
        { key: 'waiting',   label: '等待中' },
        { key: 'failed',    label: '已失效' },
    ]
    if (sourceTab.value === 'signal') {
        return all.filter(o => ['all', 'launched', 'breakdown', 'buy_zone', 'strong', 'failed'].includes(o.key))
    }
    if (sourceTab.value === 'candidate') {
        return all.filter(o => ['all', 'almost', 'launched', 'buy_zone', 'waiting', 'failed'].includes(o.key))
    }
    return all
})

// ---------------- 操作 ----------------
async function removePick(code) {
    const ok = await confirmDialog({
        title:       '确认移除',
        message:     `从候选池移除 ${code}？`,
        confirmText: '移除',
        danger:      true,
    })
    if (!ok) return
    try {
        const res = await api.removeCandidatePick(code)
        if (res.ok) {
            picks.value = picks.value.filter(p => p.code !== code)
            selectedCodes.value.delete(code)   // 同步清除多选状态
            pushSuccess(`已移除 ${code}`)
        } else {
            pushError(res.error || '移除失败')
        }
    } catch (e) {
        pushError(`移除失败：${e}`)
    }
}

function viewChart(p) {
    const list = visiblePicks.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(p.code, p.name, list)
}

function transferToWatchlist(p, e) {
    e?.stopPropagation()
    openAddToWatchlist(p.code, p.name, p.currentPrice ?? p.save_price)
}

// ---------------- 批量选择 + 操作 ----------------
const selectedCodes = ref(new Set())

function toggleRow(code, e) {
    e?.stopPropagation()
    const next = new Set(selectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    selectedCodes.value = next
}

const allVisibleSelected = computed(() => {
    if (!visiblePicks.value.length) return false
    return visiblePicks.value.every(p => selectedCodes.value.has(p.code))
})

function toggleAll() {
    if (allVisibleSelected.value) {
        // 全已选 → 仅清空当前可见的（保留过滤掉的票的勾选状态，避免切 tab 后丢选择）
        const next = new Set(selectedCodes.value)
        for (const p of visiblePicks.value) next.delete(p.code)
        selectedCodes.value = next
    } else {
        const next = new Set(selectedCodes.value)
        for (const p of visiblePicks.value) next.add(p.code)
        selectedCodes.value = next
    }
}

const batchProcessing = ref(false)

async function batchRemove() {
    if (batchProcessing.value || !selectedCodes.value.size) return
    const codes = [...selectedCodes.value]
    const ok = await confirmDialog({
        title:       '批量移除',
        message:     `从候选池移除 ${codes.length} 只选中的票？\n此操作不可撤销。`,
        confirmText: `移除 ${codes.length} 只`,
        danger:      true,
    })
    if (!ok) return
    batchProcessing.value = true
    let okCount = 0, failCount = 0
    try {
        // 串行：避免撞 SQLite 单写锁
        for (const code of codes) {
            try {
                const res = await api.removeCandidatePick(code)
                if (res.ok) {
                    okCount++
                    picks.value = picks.value.filter(p => p.code !== code)
                } else {
                    failCount++
                }
            } catch {
                failCount++
            }
        }
        selectedCodes.value = new Set()
        let msg = `已批量移除 ${okCount} 只`
        if (failCount) msg += `，${failCount} 只失败`
        pushSuccess(msg)
    } finally {
        batchProcessing.value = false
    }
}

// ---------------- 工具 ----------------
function fmtPrice(v) { return v == null ? '—' : v.toFixed(2) }
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
function fmtDate(s) {
    if (!s) return '—'
    // saved_at 是 ISO 字符串 '2026-05-06T12:34:56.789'
    return s.slice(0, 10)
}
function marketPrefix(code) {
    if (!code) return ''
    return code.startsWith('6') ? 'SH' : 'SZ'
}

// ---------------- Phase 5：动态追踪刷新 ----------------
const { refreshing: tracking, lastSession: trackingSession, refresh: doRefreshTracking } = useCandidateRefresh()

// 上次刷新摘要 — 取所有 picks 里最新的 last_refreshed_at（没刷过 = null）
const oldestRefresh = computed(() => {
    if (!picks.value.length) return null
    let oldest = null
    let hasNull = false
    for (const p of picks.value) {
        if (!p.last_refreshed_at) { hasNull = true; continue }
        const ms = new Date(p.last_refreshed_at).getTime()
        if (Number.isNaN(ms)) continue
        if (oldest == null || ms < oldest) oldest = ms
    }
    return { oldestMs: oldest, hasNull }
})

const refreshHint = computed(() => {
    const r = oldestRefresh.value
    if (!r || r.oldestMs == null) {
        return r?.hasNull ? '从未刷新追踪' : ''
    }
    const ageMin = Math.round((Date.now() - r.oldestMs) / 60000)
    if (r.hasNull) return `部分未刷新 · 最早 ${ageMin}min 前`
    if (ageMin < 1)  return '刚刚刷新'
    if (ageMin < 60) return `${ageMin} 分钟前刷新`
    return `${Math.round(ageMin / 60)} 小时前刷新`
})

async function refreshTracking() {
    if (tracking.value) return
    if (!picks.value.length) return
    const session = await doRefreshTracking(picks.value)
    if (session) {
        await loadPicks()   // 重新拉取以获得新的 peak_gain / formation_state / last_refreshed_at
        let msg = `追踪刷新完成 · 更新 ${session.updated} / ${session.total}`
        if (session.removed) msg += ` · 剔除过期 ${session.removed}`
        if (session.errors)  msg += ` · ${session.errors} 失败`
        pushSuccess(msg)
    }
}

// formation_state：减色策略 — 主红/灰两色，靠 icon shape 区分阶段（色弱友好双编码）
// breakout/rally 用红强调（"该追入"信号），exhausted/invalid 用警示样式，其他灰
const FORMATION_DISPLAY = {
    consolidating: { text: '蓄势中',   icon: '○', cls: 'text-[#94a3b8]' },
    tested:        { text: '试盘后',   icon: '◐', cls: 'text-[#666]' },
    breakout:      { text: '刚突破',   icon: '●', cls: 'text-[#dc2626] font-semibold' },
    rally:         { text: '主升中',   icon: '▲', cls: 'text-[#dc2626]' },
    exhausted:     { text: '衰竭警示', icon: '⚠', cls: 'text-[#666] font-semibold' },
    invalid:       { text: '形态作废', icon: '✗', cls: 'text-[#999] line-through' },
    unknown:       { text: '未识别',   icon: '—', cls: 'text-[#cbd5e1]' },
}


onMounted(async () => {
    await loadPicks()
    // 自动触发：有 picks 且 (从没刷新过 OR 最早一只 ≥1 小时前)
    if (picks.value.length) {
        const r = oldestRefresh.value
        const stale = !r || r.hasNull || (r.oldestMs && (Date.now() - r.oldestMs) > 60 * 60 * 1000)
        if (stale) {
            // 不 await，让用户先看到 picks 列表
            refreshTracking()
        }
    }
    // 列顺序：先拉用户偏好，再等 DOM 渲染挂 Sortable
    await loadSavedColumnOrder()
    await nextTick()
    initSavedHeaderSortable()
})

onBeforeUnmount(() => {
    if (savedHeaderSortable) {
        savedHeaderSortable.destroy()
        savedHeaderSortable = null
    }
})

// 切换 source tab 时重新挂 Sortable（thead 通常不重建，但保险起见）
watch(sourceTab, async () => {
    await nextTick()
    initSavedHeaderSortable()
})

// 列表变了立刻拉一次 quotes（不等下一个 tick）
watch(() => picks.value.length, () => {
    if (picks.value.length) refreshQuotes()
})
</script>

<template>
    <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">
        <!-- Tab 栏 — 跟 Watchlist 统一：单红色下划线 + 计数胶囊，无 emoji，无多色 -->
        <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0 px-[12px] gap-[2px]">
            <button v-for="t in [
                { key: 'all',           label: '全部',     count: sourceCounts.all },
                { key: 'candidate',     label: '候选',     count: sourceCounts.candidate },
                { key: 'signal',        label: '发车',     count: sourceCounts.signal },
            ]" :key="t.key"
                 @click="sourceTab = t.key"
                 :title="t.title || ''"
                 class="flex items-center gap-[6px] px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0"
                 :class="sourceTab === t.key
                    ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                    : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'">
                <span>{{ t.label }}</span>
                <span v-if="t.count != null"
                      class="text-[10px] font-bold tabular-nums px-[5px] py-[1px] rounded-full"
                      :class="sourceTab === t.key ? 'bg-[#dc2626] text-white' : 'bg-[#e5e7eb] text-[#666]'">
                    {{ t.count }}
                </span>
            </button>
        </div>

        <!-- 状态过滤 chips（左）+ 上次刷新 hint + 按钮（右），合并为一行 44px -->
        <div
             class="h-[44px] flex items-center px-[14px] border-b border-[#e5e7eb] bg-white shrink-0 gap-[8px]">
            <!-- 左侧：状态 + 来源过滤 chips（横向滚动避免挤压按钮）-->
            <div class="flex items-center gap-[8px] overflow-x-auto custom-scrollbar text-[12px] min-w-0 flex-1">
                <button v-for="opt in visibleStateChips" :key="opt.key"
                     @click="filter = opt.key"
                     class="px-[10px] py-[3px] rounded-[12px] border transition-all shrink-0"
                     :class="filter === opt.key
                        ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                        : 'bg-[#f3f4f6] text-[#666] border-transparent hover:bg-[#e5e7eb]'">
                    {{ opt.label }}
                    <span class="ml-[4px] text-[10px] opacity-70">{{ stateCounts[opt.key] ?? 0 }}</span>
                </button>
                <!-- 视觉分隔 -->
                <span class="w-[1px] h-[18px] bg-[#e5e7eb] shrink-0"></span>
                <!-- 来源筛选 chips -->
                <button v-for="opt in visibleSourceChips" :key="`src-${opt.key}`"
                     @click="sourceFilter = opt.key"
                     class="px-[10px] py-[3px] rounded-[12px] border transition-all shrink-0"
                     :class="sourceFilter === opt.key
                        ? 'border-[#854d0e] text-[#854d0e] font-bold bg-[#fef3c7]'
                        : 'bg-[#f3f4f6] text-[#666] border-transparent hover:bg-[#e5e7eb]'">
                    {{ opt.label }}
                    <span class="ml-[4px] text-[10px] opacity-70">{{ sourceFilterCounts[opt.key] ?? 0 }}</span>
                </button>
            </div>
            <!-- 右侧：上次刷新 hint + 按钮 -->
            <span class="text-[10px] text-[#94a3b8] tabular-nums shrink-0">
                {{ refreshHint }}
            </span>
            <button @click="refreshTracking"
                    :disabled="tracking || !picks.length"
                    title="跑 detector 重新评估每只候选的形态状态 + 计算关注后最大涨幅"
                    class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#dc2626]/40
                           text-[#dc2626] bg-white hover:bg-[#fff5f5] hover:border-[#dc2626]
                           disabled:opacity-40 disabled:cursor-not-allowed transition shrink-0">
                {{ tracking ? '刷新中...' : '刷新追踪' }}
            </button>
            <button @click="loadPicks"
                    class="text-[12px] px-[10px] py-[4px] rounded-[4px] border border-[#94a3b8]/40
                           text-[#666] bg-white hover:bg-[#f3f4f6] hover:border-[#94a3b8]
                           transition shrink-0">
                刷新列表
            </button>
        </div>


        <!-- 批量操作工具栏（选中 ≥1 只时高亮）-->
        <div v-if="visiblePicks.length"
             class="px-[16px] py-[6px] border-b border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[10px] text-[11px]">
            <span class="text-[#666]">
                已选
                <span class="font-bold tabular-nums" :class="selectedCodes.size ? 'text-[#dc2626]' : 'text-[#999]'">
                    {{ selectedCodes.size }}
                </span>
                / {{ visiblePicks.length }}
            </span>
            <button @click="batchRemove"
                    :disabled="!selectedCodes.size || batchProcessing"
                    class="px-[10px] py-[3px] rounded-[4px] border transition
                           disabled:opacity-50 disabled:cursor-not-allowed"
                    :class="selectedCodes.size && !batchProcessing
                        ? 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca] hover:bg-[#fecaca] font-semibold'
                        : 'bg-white text-[#999] border-[#e5e7eb]'">
                🗑 批量移除
            </button>
            <span v-if="batchProcessing" class="text-[#dc2626] font-semibold animate-pulse">处理中...</span>
        </div>


        <!-- 表格 -->
        <div class="flex-1 overflow-auto custom-scrollbar">

            <!-- ============ 已收藏（saved picks）表格 ============ -->
            <div v-if="loading" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</div>
            <div v-else-if="!enrichedPicks.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                候选池还是空的 —— 去「选股」模块（量化扫描器）里点 ⭐ 收藏，票就会进来
            </div>
            <div v-else-if="!visiblePicks.length" class="py-[60px] text-center text-[#aaa] text-[13px]">
                当前过滤条件下没有匹配的票
            </div>

            <table v-else class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                    <tr class="candidate-pool-header-row">
                        <!-- 固定列：checkbox -->
                        <th class="col-fixed px-[8px] py-[8px] font-normal text-center w-[32px]">
                            <input type="checkbox"
                                   :checked="allVisibleSelected"
                                   @change="toggleAll"
                                   :title="allVisibleSelected ? '取消全选' : '全选当前可见'"
                                   class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                        </th>
                        <!-- 固定列：股票 -->
                        <th class="col-fixed px-[12px] py-[8px] font-normal w-[140px]">股票</th>
                        <!-- 中部 12 列可拖拽（按 savedColumnOrder 排）-->
                        <th v-for="key in savedColumnOrder" :key="key"
                            :data-col-key="key"
                            :title="SAVED_COLUMNS_META[key].title || '拖拽调整列顺序'"
                            class="col-draggable px-[8px] py-[8px] font-normal cursor-move select-none"
                            :class="SAVED_COLUMNS_META[key].align"
                            :style="{ width: SAVED_COLUMNS_META[key].width }">
                            {{ SAVED_COLUMNS_META[key].label }}
                        </th>
                        <!-- 固定列：操作 -->
                        <th class="col-fixed px-[8px] py-[8px] font-normal text-center w-[100px]">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="p in visiblePicks" :key="p.code"
                        @dblclick="viewChart(p)"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                        :class="selectedCodes.has(p.code) ? 'bg-[#fff5f5]' : ''"
                        title="双击看 K 线">

                        <!-- 固定列：多选 checkbox -->
                        <td class="px-[8px] py-[8px] text-center align-middle" @click.stop>
                            <input type="checkbox"
                                   :checked="selectedCodes.has(p.code)"
                                   @change="toggleRow(p.code, $event)"
                                   class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                        </td>

                        <!-- 固定列：股票名 + 代码 + Stage -->
                        <td class="px-[12px] py-[8px] align-middle">
                            <div class="text-[13px] font-bold text-[#111] leading-tight truncate">
                                {{ p.name || '—' }}
                            </div>
                            <div class="text-[10px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums flex items-center gap-[4px]">
                                <span>{{ marketPrefix(p.code) }}{{ p.code }}</span>
                                <span class="px-[4px] rounded-[2px] text-[9px] font-semibold"
                                      :class="p.stage === 3 ? 'bg-[#dc2626] text-white'
                                            : p.stage === 2 ? 'bg-[#fef3c7] text-[#b45309]'
                                                            : 'bg-[#f1f5f9] text-[#64748b]'">
                                    {{ p.stage === 3 ? 'S3 突破' : (p.stage === 2 ? 'S2 试盘' : 'S1 蓄势') }}
                                </span>
                            </div>
                        </td>

                        <!-- 中部 12 列：按 savedColumnOrder 顺序渲染（可拖拽重排）-->
                        <td v-for="key in savedColumnOrder" :key="key"
                            class="px-[8px] py-[8px] align-middle"
                            :class="SAVED_COLUMNS_META[key].align">

                            <!-- 状态 -->
                            <span v-if="key === 'state'"
                                  class="inline-block px-[8px] py-[2px] rounded-[3px] text-[11px]"
                                  :class="p.state.cls">
                                {{ p.state.label }}
                            </span>

                            <!-- 来源（扫描器标签）— 多源命中时叠多个 chip -->
                            <template v-else-if="key === 'source'">
                                <div class="flex flex-wrap items-center justify-center gap-[2px]"
                                     :title="`原始 source 字段：${p.source || '—'}`">
                                    <span v-for="lbl in categorizeSourceLabels(p)" :key="lbl.key"
                                          class="inline-block px-[5px] py-[1px] rounded-[3px] text-[10px] font-semibold border whitespace-nowrap"
                                          :class="lbl.cls">
                                        {{ lbl.label }}
                                    </span>
                                </div>
                            </template>

                            <!-- 形态（含二次买点小标）-->
                            <template v-else-if="key === 'formation'">
                                <div v-if="p.formationState"
                                     :class="['text-[11px] tabular-nums', FORMATION_DISPLAY[p.formationState]?.cls]"
                                     :title="FORMATION_DISPLAY[p.formationState]?.text + '（detector 判定）'">
                                    <span class="mr-[2px]">{{ FORMATION_DISPLAY[p.formationState]?.icon }}</span>
                                    {{ FORMATION_DISPLAY[p.formationState]?.text }}
                                </div>
                                <div v-else class="text-[#cbd5e1] text-[11px]" title="未刷新追踪">—</div>
                                <div v-if="p.secondaryEntryAt"
                                     class="text-[9px] font-bold text-white bg-[#dc2626] rounded-[2px] px-[4px] mt-[2px] inline-block"
                                     :title="`二次买点 · ${p.secondaryEntryAt?.slice?.(0, 10) || ''} 反包收盘 ${p.secondaryEntryPrice?.toFixed?.(2) || ''}`">
                                    二买
                                </div>
                            </template>

                            <!-- 现价 -->
                            <span v-else-if="key === 'currentPrice'"
                                  class="font-bold text-[13px] tabular-nums"
                                  :class="colorOf(p.changePct)">
                                {{ fmtPrice(p.currentPrice) }}
                            </span>

                            <!-- 今日 % -->
                            <template v-else-if="key === 'changePct'">
                                <span v-if="p.changePct != null"
                                      class="text-[12px] font-semibold tabular-nums"
                                      :class="colorOf(p.changePct)">
                                    {{ p.changePct > 0 ? '▲' : p.changePct < 0 ? '▼' : '◆' }}
                                    {{ fmtPct(p.changePct) }}
                                </span>
                                <span v-else class="text-[#ccc] text-[12px]">—</span>
                            </template>

                            <!-- 入选价 -->
                            <span v-else-if="key === 'savePrice'" class="text-[#666] text-[12px] tabular-nums">
                                {{ fmtPrice(p.save_price) }}
                            </span>

                            <!-- 突破点位 -->
                            <span v-else-if="key === 'breakLevel'" class="text-[#666] text-[12px] tabular-nums">
                                {{ fmtPrice(p.break_level) }}
                            </span>

                            <!-- 距突破 % -->
                            <span v-else-if="key === 'distBreak'"
                                  class="text-[12px] tabular-nums"
                                  :class="colorOf(p.distBreak)">
                                {{ fmtPct(p.distBreak) }}
                            </span>

                            <!-- 黄金买点 -->
                            <span v-else-if="key === 'goldenPrice'" class="text-[#666] text-[12px] tabular-nums">
                                {{ fmtPrice(p.golden_price) }}
                            </span>

                            <!-- 距黄金 % -->
                            <span v-else-if="key === 'distGold'"
                                  class="text-[12px] tabular-nums"
                                  :class="colorOf(p.distGold)">
                                {{ fmtPct(p.distGold) }}
                            </span>

                            <!-- 入选以来涨跌幅 + 历史最高涨幅 -->
                            <template v-else-if="key === 'sinceSavePct'">
                                <div v-if="p.sinceSavePct != null"
                                     class="text-[12px] font-semibold tabular-nums"
                                     :class="colorOf(p.sinceSavePct)">
                                    {{ p.sinceSavePct > 0 ? '▲' : p.sinceSavePct < 0 ? '▼' : '◆' }}
                                    {{ fmtPct(p.sinceSavePct) }}
                                </div>
                                <span v-else class="text-[#ccc] text-[12px]">—</span>
                                <div v-if="p.peakGain != null && p.peakGain > 0.5"
                                     class="text-[9px] text-[#dc2626]/70 font-normal mt-[1px]"
                                     :title="`关注后最高涨过 +${p.peakGain.toFixed(2)}%`">
                                    峰 +{{ p.peakGain.toFixed(1) }}%
                                </div>
                            </template>

                            <!-- 入选时间 -->
                            <span v-else-if="key === 'savedAt'" class="text-[11px] text-[#888] tabular-nums">
                                {{ fmtDate(p.saved_at) }}
                            </span>

                            <!-- 突破日 -->
                            <template v-else-if="key === 'breakoutAt'">
                                <span :title="p.breakoutAt ? `突破日 ${fmtDate(p.breakoutAt)}` : '尚未突破 / 未刷新'"
                                      class="inline-block">
                                    <template v-if="p.breakoutAt">
                                        <div class="text-[11px] tabular-nums text-[#dc2626] font-semibold">{{ fmtDate(p.breakoutAt).slice(5) }}</div>
                                        <div class="text-[9px] text-[#94a3b8] mt-[1px]">
                                            {{ daysSinceBreakout(p.breakoutAt) ?? '—' }}d 前
                                        </div>
                                    </template>
                                    <span v-else class="text-[#cbd5e1] text-[11px]">—</span>
                                </span>
                            </template>
                        </td>

                        <!-- 固定列：操作 -->
                        <td class="px-[8px] py-[8px] text-center" @click.stop>
                            <button @click.stop="transferToWatchlist(p, $event)"
                                    title="加入自选 / 持仓"
                                    class="text-[11px] text-[#2563eb] hover:underline mr-[8px]">
                                +自选
                            </button>
                            <button @click.stop="removePick(p.code)"
                                    title="从候选池移除"
                                    class="text-[11px] text-[#dc2626] hover:underline">
                                移除
                            </button>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
</template>
