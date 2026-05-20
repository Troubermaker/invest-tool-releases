<script setup>
/**
 * 今日仪表盘抽屉（右侧滑出，Ctrl+J 召唤 / sidebar "今日" 按钮触发）
 *
 * 聚合 5 个 block，无需切 tab 即可完成早盘评估：
 *   1. 🌡 大盘 regime（复用 useMarketEnv）
 *   2. ⭐⭐⭐+ 今日信号（复用 useDailyAutoScan.todayResult）
 *   3. 💼 持仓中 trades + 实时 PnL（api.listTradeJournal('open') + getBatchQuotes）
 *   4. ⚠ 待复盘（最近 3 天平仓但 notes 为空）
 *   5. 📊 本月真实 vs 预期（api.tradeJournalSummary(30)）
 *
 * 行内动作：📝 加日志（pre-fill modal）/ 👁 看 K 线（openStockChart）。
 * 数据在 props.open 变 true 时拉一次；header refresh 按钮强刷。
 */
import { ref, computed, watch } from 'vue'
import { api } from '../api/client'
import { useMarketEnv } from '../composables/useMarketEnv'
import { useDailyAutoScan } from '../composables/useDailyAutoScan'
import { openStockChart } from '../composables/useStockChart'
import { openAddToWatchlist, openAddToWatchlistBatch } from '../composables/useAddToWatchlist'
import AddTradeJournalModal from './AddTradeJournalModal.vue'

const props = defineProps({
    open: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'navigate'])

// ---- 数据源 ----
const marketEnv = useMarketEnv()
const autoScan = useDailyAutoScan()

const openTrades = ref([])         // 持仓中 trades（已注入 currentPrice / pnlLive）
const closedRecent = ref([])       // 最近 3 天平仓
const monthSummary = ref(null)     // tradeJournalSummary(30)
const loading = ref(false)
const lastRefreshAt = ref(null)

// ---- 加日志 modal ----
const journalModal = ref({ open: false, prefill: {} })

// ---- 今日信号批量选择 ----
const selectedSignalCodes = ref(new Set())
function toggleSelectSignal(code) {
    const s = new Set(selectedSignalCodes.value)
    if (s.has(code)) s.delete(code); else s.add(code)
    selectedSignalCodes.value = s
}
function toggleSelectAllSignals() {
    const all = todaySignals.value.map(x => x.code)
    if (selectedSignalCodes.value.size === all.length && all.length > 0) {
        selectedSignalCodes.value = new Set()
    } else {
        selectedSignalCodes.value = new Set(all)
    }
}
function batchAddSelectedToWatchlist() {
    const all = todaySignals.value
    const picked = all.filter(s => selectedSignalCodes.value.has(s.code))
    if (!picked.length) return
    openAddToWatchlistBatch(picked.map(s => ({
        code: s.code,
        name: s.name,
        price: s.entryPrice ?? null,
    })))
    selectedSignalCodes.value = new Set()
}
function addSingleToWatchlist(s, e) {
    e?.stopPropagation()
    openAddToWatchlist(s.code, s.name, s.entryPrice ?? null)
}

function _now() { return Date.now() }

async function refreshAll() {
    if (loading.value) return
    loading.value = true
    selectedSignalCodes.value = new Set()
    try {
        const [envRet, autoRet, openRet, closedRet, sumRet] = await Promise.allSettled([
            marketEnv.refresh(),
            autoScan.loadTodayResult(),
            api.listTradeJournal('open'),
            api.listTradeJournal('closed', null, 50),
            api.tradeJournalSummary(30),
        ])
        // 持仓
        const trades = (openRet.status === 'fulfilled' && openRet.value?.ok && Array.isArray(openRet.value.data))
            ? openRet.value.data : []
        // 拉一次行情注入 currentPrice
        if (trades.length) {
            try {
                const codes = trades.map(t => t.code)
                const qRes = await api.getBatchQuotes(codes)
                const qMap = (qRes?.ok && qRes.data) || {}
                for (const t of trades) {
                    const q = qMap[t.code]
                    if (q && q.price) {
                        t.currentPrice = q.price
                        t.pnlLive = ((q.price - t.entry_price) / t.entry_price) * 100
                    }
                }
            } catch (e) { /* 行情失败不阻塞 */ }
        }
        openTrades.value = trades

        // 待复盘：最近 3 天平仓且 notes 空
        const closed = (closedRet.status === 'fulfilled' && closedRet.value?.ok && Array.isArray(closedRet.value.data))
            ? closedRet.value.data : []
        const cutoff = Date.now() - 3 * 24 * 3600 * 1000
        closedRecent.value = closed.filter(t => {
            if (!t.exit_at) return false
            const ts = new Date(t.exit_at).getTime()
            if (isNaN(ts) || ts < cutoff) return false
            return !t.notes || !t.notes.trim()
        }).slice(0, 5)

        // 月度
        monthSummary.value = (sumRet.status === 'fulfilled' && sumRet.value?.ok) ? sumRet.value.data : null
        lastRefreshAt.value = _now()
    } finally {
        loading.value = false
    }
}

// open 变 true 时拉数据
watch(() => props.open, (v) => {
    if (v) refreshAll()
})

// ---- 派生 ----
const regimeInfo = computed(() => {
    const e = marketEnv.env.value
    if (!e) return null
    return {
        regime:   e.regime || 'neutral',
        label:    e.regimeLabel || '中性',
        score:    e.regimeScore ?? e.score ?? 50,
        breakdown: e.breakdown || null,
    }
})

// regime 颜色（用户色弱：颜色 + emoji shape 双重编码）
function regimeStyle(regime) {
    switch (regime) {
        case 'strong':    return { cls: 'bg-[#fef2f2] text-[#991b1b] border-[#fecaca]', icon: '⬆⬆' }
        case 'good':      return { cls: 'bg-[#fffbeb] text-[#b45309] border-[#fde68a]', icon: '⬆' }
        case 'neutral':   return { cls: 'bg-[#f1f5f9] text-[#475569] border-[#e2e8f0]', icon: '→' }
        case 'weak':      return { cls: 'bg-[#eff6ff] text-[#1e40af] border-[#bfdbfe]', icon: '⬇' }
        case 'breakdown': return { cls: 'bg-[#1e3a8a] text-white border-[#1e3a8a]', icon: '⬇⬇' }
        default:          return { cls: 'bg-[#f1f5f9] text-[#475569] border-[#e2e8f0]', icon: '→' }
    }
}

const todaySignals = computed(() => {
    const r = autoScan.todayResult.value
    if (!r || !Array.isArray(r.top_codes)) return []
    // ⭐⭐⭐+ only
    return r.top_codes.filter(c => (c.starLevel ?? 0) >= 3).slice(0, 8)
})

const todayStar4 = computed(() => todaySignals.value.filter(s => (s.starLevel ?? 0) >= 4).length)

const openTradesStat = computed(() => {
    const ts = openTrades.value
    if (!ts.length) return null
    const withPnl = ts.filter(t => typeof t.pnlLive === 'number')
    const avgPnl = withPnl.length ? withPnl.reduce((a, b) => a + b.pnlLive, 0) / withPnl.length : null
    const positive = withPnl.filter(t => t.pnlLive > 0).length
    return { count: ts.length, avgPnl, positive, negative: withPnl.length - positive }
})

const monthKpi = computed(() => {
    const s = monthSummary.value
    if (!s || !s.overall) return null
    const overall = s.overall
    // 加权期望：把已平仓的 by_star count 与 expected_win_rate 加权
    let expWins = 0, expTotal = 0
    for (const k of ['star_4', 'star_3', 'star_2', 'star_1', 'star_0']) {
        const cell = s.by_star?.[k]
        if (cell && cell.count) {
            expTotal += cell.count
            expWins += cell.count * (s.expected_win_rate?.[k] ?? 0.5)
        }
    }
    const expected = expTotal ? expWins / expTotal : null
    return {
        total:    overall.count,
        winRate:  overall.win_rate,
        avgPnl:   overall.avg_pnl,
        expected,
        diff:     expected != null ? overall.win_rate - expected : null,
    }
})

// ---- 行内动作 ----
const SRC_LABEL = {
    main_breakout:   '主升突破',
    breakout_eve:    '突破前夜',
    dragon_return:   '龙回头',
    limit_up_relay:  '连板游资',
    manual:          '手动',
}

function openAddJournalForSignal(s, e) {
    e?.stopPropagation()
    journalModal.value = {
        open: true,
        prefill: {
            code:           s.code,
            name:           s.name,
            signalSource:   'main_breakout',   // 今日信号都来自 useRecentMarketScan
            starLevel:      s.starLevel ?? 0,
            suggestedEntry: s.entryPrice ?? null,
            breakLevel:     s.entryPrice ?? null,
            signalMetadata: {
                breakoutConfirm: s.breakoutConfirm,
                sectorScore:     s.sectorScore,
                lhbInWindow:     s.lhbInWindow,
                lhbCount:        s.lhbCount,
                mlScore:         s.mlScore,
                s3Time:          s.s3Time,
            },
        },
    }
}

function viewChart(code, name, listSrc) {
    const list = (listSrc || []).map(x => ({ code: x.code, name: x.name }))
    openStockChart(code, name, list)
}

function jumpQuant() {
    emit('navigate', 'quant', 'scanner')
    emit('close')
}

function jumpJournal() {
    emit('navigate', 'quant', 'journal')
    emit('close')
}

// ---- 格式化 ----
function fmtPct(v, signed = false) {
    if (v == null || isNaN(v)) return '—'
    const s = (signed && v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%'
    return s
}
function fmtPctDirect(v, signed = false) {
    if (v == null || isNaN(v)) return '—'
    return (signed && v > 0 ? '+' : '') + v.toFixed(2) + '%'
}
function fmtPrice(v) {
    if (v == null || isNaN(v)) return '—'
    return Number(v).toFixed(2)
}
function fmtDateShort(s) {
    if (!s) return ''
    return s.slice(5, 10).replace('-', '/')
}

function starText(n) { return '⭐'.repeat(Math.max(0, Math.min(4, n || 0))) }
</script>

<template>
    <!-- 半透明遮罩（轻度，不阻挡操作）-->
    <div v-if="open" @click="emit('close')"
         class="fixed inset-0 bg-gray-900/5 z-[55] backdrop-blur-[1px] transition-opacity"></div>

    <!-- 抽屉本体 -->
    <div
        class="fixed top-0 right-0 h-full w-[400px] bg-white border-l border-gray-200 shadow-[0_0_40px_rgba(0,0,0,0.12)] transition-transform duration-300 z-[60] flex flex-col"
        :class="open ? 'translate-x-0' : 'translate-x-full'"
    >
        <!-- Header -->
        <div class="h-[44px] border-b border-[#e5e7eb] flex items-center justify-between px-[14px] bg-[#fafafa] shrink-0">
            <div class="flex items-center gap-2">
                <svg class="w-[18px] h-[18px] text-[#dc2626]" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0M3.124 7.5A8.969 8.969 0 015.292 3m13.416 0a8.969 8.969 0 012.168 4.5" />
                </svg>
                <span class="font-bold text-[13px] text-[#111]">今日仪表盘</span>
                <span v-if="lastRefreshAt" class="text-[10px] text-[#94a3b8]">
                    {{ new Date(lastRefreshAt).toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 5) }} 刷新
                </span>
            </div>
            <div class="flex items-center gap-1">
                <button @click="refreshAll" :disabled="loading"
                        class="px-[7px] py-[3px] rounded text-[11px] text-[#666] hover:bg-white border border-transparent hover:border-[#e5e7eb] transition disabled:opacity-50"
                        title="刷新">
                    <span :class="loading ? 'animate-spin inline-block' : ''">↻</span>
                </button>
                <button @click="emit('close')"
                        class="text-[#666] hover:text-[#111] bg-white p-1 rounded-md border border-[#e5e7eb]"
                        title="关闭 (Esc)">
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
            </div>
        </div>

        <!-- Scrollable Content -->
        <div class="flex-1 overflow-y-auto bg-[#fafafa]">

            <!-- Block 1: Regime -->
            <section class="bg-white border-b border-gray-100">
                <div class="px-3 py-2 flex items-center justify-between">
                    <div class="flex items-center gap-1.5">
                        <span class="text-[12px]">🌡</span>
                        <span class="text-[12px] font-bold text-[#374151]">大盘 regime</span>
                    </div>
                </div>
                <div v-if="regimeInfo" class="px-3 pb-3">
                    <div :class="['flex items-center justify-between px-2.5 py-2 rounded border', regimeStyle(regimeInfo.regime).cls]">
                        <div class="flex items-center gap-2">
                            <span class="text-[14px] font-bold">{{ regimeStyle(regimeInfo.regime).icon }}</span>
                            <span class="text-[13px] font-bold">{{ regimeInfo.label }}</span>
                        </div>
                        <span class="text-[18px] font-bold tabular-nums">{{ Math.round(regimeInfo.score) }}</span>
                    </div>
                    <div v-if="regimeInfo.breakdown" class="mt-1.5 grid grid-cols-4 gap-1 text-[10px] text-[#64748b]">
                        <div class="px-1 py-1 bg-[#f8fafc] rounded text-center" title="沪深 300 指数趋势">
                            <div class="text-[#94a3b8]">趋势</div>
                            <div class="font-bold text-[#475569] tabular-nums">
                                {{ Math.round(regimeInfo.breakdown.index_trend?.score ?? 0) }}<span class="text-[8px] text-[#cbd5e1]">/{{ regimeInfo.breakdown.index_trend?.max ?? 50 }}</span>
                            </div>
                        </div>
                        <div class="px-1 py-1 bg-[#f8fafc] rounded text-center" title="涨跌停 / 涨家数 情绪宽度">
                            <div class="text-[#94a3b8]">宽度</div>
                            <div class="font-bold text-[#475569] tabular-nums">
                                {{ Math.round(regimeInfo.breakdown.market_breadth?.score ?? 0) }}<span class="text-[8px] text-[#cbd5e1]">/{{ regimeInfo.breakdown.market_breadth?.max ?? 20 }}</span>
                            </div>
                        </div>
                        <div class="px-1 py-1 bg-[#f8fafc] rounded text-center" title="创业板 vs 沪深 300 相对强度">
                            <div class="text-[#94a3b8]">创业板</div>
                            <div class="font-bold text-[#475569] tabular-nums">
                                {{ Math.round(regimeInfo.breakdown.gem_relative?.score ?? 0) }}<span class="text-[8px] text-[#cbd5e1]">/{{ regimeInfo.breakdown.gem_relative?.max ?? 15 }}</span>
                            </div>
                        </div>
                        <div class="px-1 py-1 bg-[#f8fafc] rounded text-center" title="量能 / 缩量阴跌识别">
                            <div class="text-[#94a3b8]">量能</div>
                            <div class="font-bold text-[#475569] tabular-nums">
                                {{ Math.round(regimeInfo.breakdown.volume?.score ?? 0) }}<span class="text-[8px] text-[#cbd5e1]">/{{ regimeInfo.breakdown.volume?.max ?? 15 }}</span>
                            </div>
                        </div>
                    </div>
                </div>
                <div v-else class="px-3 pb-3 text-[11px] text-[#94a3b8]">
                    regime 数据加载中…
                </div>
            </section>

            <!-- Block 2: 今日 ⭐⭐⭐+ 信号 -->
            <section class="bg-white border-b border-gray-100 mt-2">
                <div class="px-3 py-2 flex items-center justify-between">
                    <div class="flex items-center gap-1.5">
                        <span class="text-[12px]">⭐⭐⭐+</span>
                        <span class="text-[12px] font-bold text-[#374151]">今日信号</span>
                        <span v-if="todaySignals.length" class="text-[10px] text-[#dc2626] font-bold">
                            {{ todaySignals.length }} 只<span v-if="todayStar4 > 0"> · ⭐⭐⭐⭐ {{ todayStar4 }}</span>
                        </span>
                    </div>
                    <button @click="jumpQuant" class="text-[10px] text-[#7c2d12] hover:underline">查看全部 →</button>
                </div>
                <div v-if="todaySignals.length === 0" class="px-3 pb-3 text-[11px] text-[#94a3b8]">
                    暂无 ⭐⭐⭐+ 信号（或今日尚未扫描）
                </div>
                <div v-else class="px-1.5 pb-2">
                    <!-- 批量操作 bar -->
                    <div class="flex items-center justify-between px-2 py-1.5 mb-1 bg-gradient-to-r from-[#fef2f2] to-[#fffbeb] rounded border border-[#fecaca]">
                        <label class="flex items-center gap-1.5 text-[11px] text-[#7c2d12] cursor-pointer select-none font-medium">
                            <input type="checkbox"
                                   :checked="selectedSignalCodes.size === todaySignals.length && todaySignals.length > 0"
                                   :indeterminate.prop="selectedSignalCodes.size > 0 && selectedSignalCodes.size < todaySignals.length"
                                   @change="toggleSelectAllSignals"
                                   class="w-3.5 h-3.5 accent-[#dc2626]" />
                            <span v-if="selectedSignalCodes.size === 0">勾选下方批量加自选</span>
                            <span v-else>已选 <span class="text-[#dc2626] font-bold">{{ selectedSignalCodes.size }}</span> / {{ todaySignals.length }}</span>
                        </label>
                        <button @click="batchAddSelectedToWatchlist"
                                :disabled="selectedSignalCodes.size === 0"
                                class="px-2.5 py-1 text-[11px] rounded font-bold transition"
                                :class="selectedSignalCodes.size > 0
                                    ? 'bg-[#dc2626] text-white hover:bg-[#b91c1c]'
                                    : 'bg-[#fde8e8] text-[#dc2626] opacity-60 cursor-not-allowed border border-[#fecaca]'">
                            ＋ 批量加自选<span v-if="selectedSignalCodes.size > 0"> ({{ selectedSignalCodes.size }})</span>
                        </button>
                    </div>
                    <div v-for="s in todaySignals" :key="s.code"
                         class="flex items-center gap-1.5 px-1.5 py-1.5 rounded hover:bg-[#fef2f2] group"
                         :class="selectedSignalCodes.has(s.code) ? 'bg-[#fef2f2]' : ''">
                        <input type="checkbox"
                               :checked="selectedSignalCodes.has(s.code)"
                               @change="toggleSelectSignal(s.code)"
                               class="w-3 h-3 shrink-0 accent-[#dc2626]" />
                        <span class="text-[11px] w-[58px] shrink-0 tabular-nums" :class="(s.starLevel ?? 0) >= 4 ? 'text-[#dc2626] font-bold' : 'text-[#b45309]'">
                            {{ starText(s.starLevel) }}
                        </span>
                        <div class="flex-1 min-w-0">
                            <div class="text-[12px] font-medium text-[#1f2937] truncate">{{ s.name }}</div>
                            <div class="text-[10px] text-[#9ca3af] tabular-nums">
                                {{ s.code }}
                                <span v-if="s.entryPrice"> · {{ fmtPrice(s.entryPrice) }}</span>
                                <span v-if="s.lhbInWindow === 1" class="text-[#dc2626] font-bold ml-1">龙</span>
                                <span v-if="s.breakoutConfirm === 'strong'" class="ml-1 text-[#7c2d12]">强确</span>
                                <span v-else-if="s.breakoutConfirm === 'medium'" class="ml-1 text-[#b45309]">中确</span>
                            </div>
                        </div>
                        <!-- 统一的分段控件：淡灰边框 + 中间分隔线，hover 时按钮单独高亮 -->
                        <!-- 图标来源：Heroicons outline，跟 QuantHub tab 用的一致 -->
                        <div class="opacity-0 group-hover:opacity-100 transition flex items-center shrink-0 rounded-md border border-[#e5e7eb] bg-white overflow-hidden divide-x divide-[#f1f5f9] shadow-sm">
                            <button @click="openAddJournalForSignal(s, $event)"
                                    class="w-[26px] h-[22px] flex items-center justify-center text-[#64748b] hover:bg-[#fef2f2] hover:text-[#dc2626] transition"
                                    title="加交易日志">
                                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
                                </svg>
                            </button>
                            <button @click="addSingleToWatchlist(s, $event)"
                                    class="w-[26px] h-[22px] flex items-center justify-center text-[#64748b] hover:bg-[#fffbeb] hover:text-[#b45309] transition"
                                    title="加入自选">
                                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0z" />
                                </svg>
                            </button>
                            <button @click="viewChart(s.code, s.name, todaySignals)"
                                    class="w-[26px] h-[22px] flex items-center justify-center text-[#64748b] hover:bg-[#f1f5f9] hover:text-[#111] transition"
                                    title="看 K 线">
                                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
            </section>

            <!-- Block 3: 持仓中 -->
            <section class="bg-white border-b border-gray-100 mt-2">
                <div class="px-3 py-2 flex items-center justify-between">
                    <div class="flex items-center gap-1.5">
                        <span class="text-[12px]">💼</span>
                        <span class="text-[12px] font-bold text-[#374151]">持仓中</span>
                        <span v-if="openTradesStat" class="text-[10px] font-bold tabular-nums"
                              :class="(openTradesStat.avgPnl ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#1e40af]'">
                            {{ openTradesStat.count }} 只
                            <span v-if="openTradesStat.avgPnl != null">
                                · 均 {{ fmtPctDirect(openTradesStat.avgPnl, true) }}
                                ({{ openTradesStat.positive }}↑/{{ openTradesStat.negative }}↓)
                            </span>
                        </span>
                    </div>
                    <button @click="jumpJournal" class="text-[10px] text-[#7c2d12] hover:underline">日志 →</button>
                </div>
                <div v-if="openTrades.length === 0" class="px-3 pb-3 text-[11px] text-[#94a3b8]">
                    暂无持仓
                </div>
                <div v-else class="px-1.5 pb-2">
                    <div v-for="t in openTrades" :key="t.id"
                         class="flex items-center gap-1.5 px-1.5 py-1.5 rounded hover:bg-[#f8fafc] group">
                        <span class="text-[11px] w-[58px] shrink-0 tabular-nums text-[#b45309]">
                            {{ starText(t.star_level) }}
                        </span>
                        <div class="flex-1 min-w-0">
                            <div class="text-[12px] font-medium text-[#1f2937] truncate">{{ t.name || t.code }}</div>
                            <div class="text-[10px] text-[#9ca3af] tabular-nums">
                                入 {{ fmtPrice(t.entry_price) }}
                                <span v-if="t.currentPrice"> → {{ fmtPrice(t.currentPrice) }}</span>
                                <span v-if="t.position_pct"> · {{ t.position_pct }}%</span>
                            </div>
                        </div>
                        <div v-if="typeof t.pnlLive === 'number'"
                             class="text-[12px] font-bold tabular-nums shrink-0 px-1"
                             :class="t.pnlLive >= 0 ? 'text-[#dc2626]' : 'text-[#1e40af]'">
                            <span class="mr-0.5">{{ t.pnlLive >= 0 ? '↑' : '↓' }}</span>
                            {{ fmtPctDirect(t.pnlLive, true) }}
                        </div>
                        <!-- 单按钮风格跟今日信号的分段控件保持一致 -->
                        <div class="opacity-0 group-hover:opacity-100 transition flex items-center shrink-0 rounded-md border border-[#e5e7eb] bg-white overflow-hidden shadow-sm">
                            <button @click="viewChart(t.code, t.name, openTrades)"
                                    class="w-[26px] h-[22px] flex items-center justify-center text-[#64748b] hover:bg-[#f1f5f9] hover:text-[#111] transition"
                                    title="看 K 线">
                                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
            </section>

            <!-- Block 4: 待复盘 -->
            <section v-if="closedRecent.length" class="bg-white border-b border-gray-100 mt-2">
                <div class="px-3 py-2 flex items-center justify-between">
                    <div class="flex items-center gap-1.5">
                        <span class="text-[12px]">⚠</span>
                        <span class="text-[12px] font-bold text-[#92400e]">待复盘</span>
                        <span class="text-[10px] text-[#92400e] font-bold">{{ closedRecent.length }} 只</span>
                    </div>
                    <button @click="jumpJournal" class="text-[10px] text-[#7c2d12] hover:underline">去补 →</button>
                </div>
                <div class="px-1.5 pb-2">
                    <div v-for="t in closedRecent" :key="t.id"
                         class="flex items-center gap-1.5 px-1.5 py-1.5 rounded hover:bg-[#fef3c7] group">
                        <span class="text-[10px] text-[#92400e] w-[44px] shrink-0">{{ fmtDateShort(t.exit_at) }}</span>
                        <div class="flex-1 min-w-0">
                            <div class="text-[12px] font-medium text-[#1f2937] truncate">{{ t.name || t.code }}</div>
                            <div class="text-[10px] text-[#9ca3af]">{{ SRC_LABEL[t.signal_source] || t.signal_source }} · 未填复盘</div>
                        </div>
                        <span class="text-[12px] font-bold tabular-nums shrink-0"
                              :class="(t.pnl_pct ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#1e40af]'">
                            <span class="mr-0.5">{{ (t.pnl_pct ?? 0) >= 0 ? '↑' : '↓' }}</span>
                            {{ fmtPctDirect(t.pnl_pct, true) }}
                        </span>
                    </div>
                </div>
            </section>

            <!-- Block 5: 月度 KPI -->
            <section class="bg-white border-b border-gray-100 mt-2 mb-2">
                <div class="px-3 py-2 flex items-center justify-between">
                    <div class="flex items-center gap-1.5">
                        <span class="text-[12px]">📊</span>
                        <span class="text-[12px] font-bold text-[#374151]">本月真实 vs 预期</span>
                    </div>
                </div>
                <div v-if="monthKpi" class="px-3 pb-3">
                    <div class="grid grid-cols-2 gap-2">
                        <div class="px-2 py-1.5 bg-[#f8fafc] rounded">
                            <div class="text-[9px] text-[#94a3b8]">已平仓</div>
                            <div class="text-[14px] font-bold text-[#1f2937] tabular-nums">{{ monthKpi.total }} 笔</div>
                        </div>
                        <div class="px-2 py-1.5 bg-[#f8fafc] rounded">
                            <div class="text-[9px] text-[#94a3b8]">均盈亏</div>
                            <div class="text-[14px] font-bold tabular-nums"
                                 :class="(monthKpi.avgPnl ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#1e40af]'">
                                <span class="text-[10px] mr-0.5">{{ (monthKpi.avgPnl ?? 0) >= 0 ? '↑' : '↓' }}</span>
                                {{ fmtPctDirect(monthKpi.avgPnl, true) }}
                            </div>
                        </div>
                    </div>
                    <div class="mt-2 px-2 py-2 rounded border"
                         :class="(monthKpi.diff ?? 0) >= -0.02 ? 'bg-[#fef2f2] border-[#fecaca]' : 'bg-[#eff6ff] border-[#bfdbfe]'">
                        <div class="flex items-center justify-between text-[11px]">
                            <span class="text-[#475569]">实测胜率</span>
                            <span class="font-bold tabular-nums text-[#1f2937]">{{ fmtPct(monthKpi.winRate) }}</span>
                        </div>
                        <div class="flex items-center justify-between text-[11px] mt-0.5">
                            <span class="text-[#475569]">期望胜率</span>
                            <span class="tabular-nums text-[#64748b]">{{ fmtPct(monthKpi.expected) }}</span>
                        </div>
                        <div v-if="monthKpi.diff != null" class="flex items-center justify-between text-[11px] mt-1 pt-1 border-t border-dashed"
                             :class="monthKpi.diff >= -0.02 ? 'border-[#fecaca]' : 'border-[#bfdbfe]'">
                            <span class="text-[#475569]">偏离</span>
                            <span class="font-bold tabular-nums"
                                  :class="monthKpi.diff >= 0 ? 'text-[#dc2626]' : monthKpi.diff >= -0.02 ? 'text-[#b45309]' : 'text-[#1e40af]'">
                                <span class="mr-0.5">{{ monthKpi.diff >= 0 ? '↑' : '↓' }}</span>
                                {{ (monthKpi.diff * 100 >= 0 ? '+' : '') + (monthKpi.diff * 100).toFixed(1) }} pp
                            </span>
                        </div>
                    </div>
                </div>
                <div v-else class="px-3 pb-3 text-[11px] text-[#94a3b8]">
                    近 30 天暂无已平仓交易
                </div>
            </section>

            <!-- Footer hint -->
            <div class="px-3 py-2 text-center text-[10px] text-[#94a3b8]">
                Ctrl+J 召唤 · Esc 关闭
            </div>
        </div>
    </div>

    <!-- 加交易日志 modal -->
    <AddTradeJournalModal
        :open="journalModal.open"
        :prefill="journalModal.prefill"
        @close="journalModal.open = false"
        @saved="journalModal.open = false; refreshAll()"
    />
</template>
