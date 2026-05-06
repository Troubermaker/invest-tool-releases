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
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api/client'
import { useSmartRefresh } from '../composables/useSmartRefresh'
import { openStockChart } from '../composables/useStockChart'
import { openAddToWatchlist } from '../composables/useAddToWatchlist'
import { pushSuccess, pushError } from '../composables/useNotifications'
import {
    QUOTE_INTERVAL_ACTIVE,
    QUOTE_INTERVAL_HIDDEN,
} from '../config/refreshIntervals'

// ---------------- 数据 ----------------
const picks = ref([])           // [{id, code, name, saved_at, stage, save_price, ...}]
const quotes = ref({})          // {code: {price, changePct, ...}}
const loading = ref(false)

// 顶部来源 tab：'all' | 'candidate'（找候选 Stage 1/2）| 'signal'（找发车 Stage 3）
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

const STATE_CSS = {
    waiting:   'bg-[#f3f4f6] text-[#475569]',
    almost:    'bg-[#fed7aa] text-[#9a3412] font-bold',
    launched:  'bg-[#fee2e2] text-[#b91c1c] font-bold',
    breakdown: 'bg-[#fef3c7] text-[#854d0e] font-bold',
    buy_zone:  'bg-[#dbeafe] text-[#1e40af] font-bold',
    strong:    'bg-[#fce7f3] text-[#9d174d] font-bold',
    failed:    'bg-[#e5e7eb] text-[#475569] line-through decoration-[1px]',
    unknown:   'bg-[#f3f4f6] text-[#999]',
}

function inferState(pick, currentPrice, category) {
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
        }
    })
})

// 排序优先级 —— 候选 / 发车下分别有意义的次序
//   候选：临门一脚 → 已突破 → 进入买点 → 等待中 → 已失效
//   发车：进入买点 → 假突破回落（要警惕）→ 突破在途 → 强势 → 已失效
const STATE_RANK = {
    almost: 0, breakdown: 1, buy_zone: 2, launched: 3, strong: 4,
    waiting: 5, failed: 6, unknown: 7,
}

// 先按 sourceTab 过滤，再按 filter 状态过滤
const tabFilteredPicks = computed(() => {
    if (sourceTab.value === 'all') return enrichedPicks.value
    return enrichedPicks.value.filter(p => p.category === sourceTab.value)
})

const visiblePicks = computed(() => {
    let arr = tabFilteredPicks.value
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

// 切换来源 tab 时重置状态过滤（不同 tab 状态语义不同，留着会困惑）
watch(sourceTab, () => { filter.value = 'all' })

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
    if (!confirm(`确认从候选池移除 ${code}？`)) return
    try {
        const res = await api.removeCandidatePick(code)
        if (res.ok) {
            picks.value = picks.value.filter(p => p.code !== code)
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

onMounted(() => {
    loadPicks()
})

// 列表变了立刻拉一次 quotes（不等下一个 tick）
watch(() => picks.value.length, () => {
    if (picks.value.length) refreshQuotes()
})
</script>

<template>
    <div class="flex flex-col h-full bg-white">
        <!-- Header -->
        <div class="px-[16px] py-[12px] border-b border-[#e5e7eb] bg-[#fafafa] flex items-center gap-[12px]">
            <span class="text-[15px] font-bold text-[#111]">候选池</span>
            <span class="text-[11px] text-[#888]">扫描器收藏的待发车股，按状态自动归类，盘中实时追踪买点</span>
            <button @click="loadPicks"
                    class="ml-auto text-[11px] text-[#2563eb] hover:underline">刷新列表</button>
        </div>

        <!-- 来源 Tab：候选（Stage 1/2 蓄势）vs 发车（Stage 3 已突破）-->
        <div class="px-[16px] pt-[8px] bg-white flex items-center gap-[4px] text-[12px]">
            <button v-for="t in [
                { key: 'all',       label: '全部', cls: 'text-[#475569]' },
                { key: 'candidate', label: '候选', cls: 'text-[#92400e]' },
                { key: 'signal',    label: '发车', cls: 'text-[#b91c1c]' },
            ]" :key="t.key"
                 @click="sourceTab = t.key"
                 class="px-[14px] py-[6px] rounded-t-[6px] border-b-2 transition-all font-semibold"
                 :class="sourceTab === t.key
                    ? 'border-[#dc2626] ' + t.cls + ' bg-[#fff5f5]'
                    : 'border-transparent text-[#94a3b8] hover:text-[#475569]'">
                {{ t.label }}
                <span class="ml-[4px] text-[10px] tabular-nums opacity-70">{{ sourceCounts[t.key] ?? 0 }}</span>
            </button>
        </div>

        <!-- 状态过滤 chips（按来源 tab 动态变化）-->
        <div class="px-[16px] py-[8px] border-b border-[#e5e7eb] bg-white flex items-center gap-[8px] flex-wrap text-[12px]">
            <button v-for="opt in visibleStateChips" :key="opt.key"
                 @click="filter = opt.key"
                 class="px-[10px] py-[3px] rounded-[12px] border transition-all"
                 :class="filter === opt.key
                    ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                    : (STATE_CSS[opt.key] || 'bg-[#f3f4f6] text-[#475569]') + ' border-transparent hover:opacity-80'">
                {{ opt.label }}
                <span class="ml-[4px] text-[10px] opacity-70">{{ stateCounts[opt.key] ?? 0 }}</span>
            </button>
        </div>

        <!-- 表格 -->
        <div class="flex-1 overflow-auto custom-scrollbar">
            <div v-if="loading" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</div>
            <div v-else-if="!enrichedPicks.length" class="py-[80px] text-center text-[#aaa] text-[13px]">
                候选池还是空的 —— 在扫描器（市场 → 板块联动 → 找候选）里点 ⭐ 收藏，就会出现在这里
            </div>
            <div v-else-if="!visiblePicks.length" class="py-[60px] text-center text-[#aaa] text-[13px]">
                当前过滤条件下没有匹配的票
            </div>

            <table v-else class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                    <tr>
                        <th class="px-[12px] py-[8px] font-normal w-[140px]">股票</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[100px]">状态</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">现价</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">今日%</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[90px]">入选价</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[100px]">突破点位</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[90px]">距突破</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[100px]">黄金买点</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[90px]">距黄金</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[90px]">入选以来</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[90px]">入选时间</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[100px]">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="p in visiblePicks" :key="p.code"
                        @dblclick="viewChart(p)"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                        title="双击看 K 线">

                        <!-- 股票名 + 代码 + 来源标签 + Stage -->
                        <td class="px-[12px] py-[8px] align-middle">
                            <div class="flex items-center gap-[6px]">
                                <div class="text-[13px] font-bold text-[#111] leading-tight truncate flex-1">
                                    {{ p.name || '—' }}
                                </div>
                                <!-- 来源标签：候选 / 发车 —— 跟 stage 是相关但独立维度 -->
                                <span class="shrink-0 px-[5px] py-[1px] text-[9px] font-semibold rounded-[2px] border"
                                      :class="sourceChip(p.category).cls">
                                    {{ sourceChip(p.category).text }}
                                </span>
                            </div>
                            <div class="text-[10px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums flex items-center gap-[4px]">
                                <span>{{ marketPrefix(p.code) }}{{ p.code }}</span>
                                <span class="px-[4px] rounded-[2px] text-[#fff] text-[9px] font-semibold"
                                      :class="p.stage === 3 ? 'bg-[#b91c1c]' : (p.stage === 2 ? 'bg-[#dc2626]' : 'bg-[#f59e0b]')">
                                    {{ p.stage === 3 ? 'S3 突破' : (p.stage === 2 ? 'S2 试盘' : 'S1 蓄势') }}
                                </span>
                            </div>
                        </td>

                        <!-- 状态标签 -->
                        <td class="px-[8px] py-[8px] align-middle text-center">
                            <span class="inline-block px-[8px] py-[2px] rounded-[3px] text-[11px]"
                                  :class="p.state.cls">
                                {{ p.state.label }}
                            </span>
                        </td>

                        <!-- 现价 -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums font-bold text-[13px]"
                            :class="colorOf(p.changePct)">
                            {{ fmtPrice(p.currentPrice) }}
                        </td>

                        <!-- 今日 % -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[12px] font-semibold"
                            :class="colorOf(p.changePct)">
                            <span v-if="p.changePct != null">
                                {{ p.changePct > 0 ? '▲' : p.changePct < 0 ? '▼' : '◆' }}
                                {{ fmtPct(p.changePct) }}
                            </span>
                            <span v-else class="text-[#ccc]">—</span>
                        </td>

                        <!-- 入选价 -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666] text-[12px]">
                            {{ fmtPrice(p.save_price) }}
                        </td>

                        <!-- 突破点位 -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666] text-[12px]">
                            {{ fmtPrice(p.break_level) }}
                        </td>

                        <!-- 距突破 % -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[12px]"
                            :class="colorOf(p.distBreak)">
                            {{ fmtPct(p.distBreak) }}
                        </td>

                        <!-- 黄金买点 -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[#666] text-[12px]">
                            {{ fmtPrice(p.golden_price) }}
                        </td>

                        <!-- 距黄金 % -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[12px]"
                            :class="colorOf(p.distGold)">
                            {{ fmtPct(p.distGold) }}
                        </td>

                        <!-- 入选以来涨跌幅 -->
                        <td class="px-[8px] py-[8px] text-right tabular-nums text-[12px] font-semibold"
                            :class="colorOf(p.sinceSavePct)">
                            <span v-if="p.sinceSavePct != null">
                                {{ p.sinceSavePct > 0 ? '▲' : p.sinceSavePct < 0 ? '▼' : '◆' }}
                                {{ fmtPct(p.sinceSavePct) }}
                            </span>
                            <span v-else class="text-[#ccc]">—</span>
                        </td>

                        <!-- 入选时间 -->
                        <td class="px-[8px] py-[8px] text-center text-[11px] text-[#888] tabular-nums">
                            {{ fmtDate(p.saved_at) }}
                        </td>

                        <!-- 操作 -->
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
