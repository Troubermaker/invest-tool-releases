<script setup>
/**
 * 三维启动「找候选」扫描器（Stage 1 蓄势中 / Stage 2 试盘后等突破）。
 *
 * 用法：从板块联动等"热门股池"传入 stocks，挑出还没启动但形态符合的候选股，
 * 一键加入自选监控，等到真正突破时再让自选扫描器告警。
 *
 * 排序：
 *   1. Stage 2（试盘已成 + 等突破）优先
 *   2. 同阶段内按"距突破点距离"升序（距离突破点越近的越临门一脚）
 */
import { ref, computed, watch, onUnmounted, onMounted } from 'vue'
import { detectThreeStageLaunch } from '../composables/useTechIndicators'
import { openStockChart } from '../composables/useStockChart'
import { openAddToWatchlist, openAddToWatchlistBatch } from '../composables/useAddToWatchlist'
import { useStockScanner } from '../composables/useStockScanner'
import { useMarketEnv } from '../composables/useMarketEnv'
import { api } from '../api/client'
import { pushSuccess, pushError } from '../composables/useNotifications'
import { useDraggable } from '../composables/useDraggable'

// 拖动支持：用户可拖动 modal 标题栏到任意位置；不点 ✕ 不会关闭
const modalRef = ref(null)
const { style: draggableStyle, onMouseDown: onHeaderMouseDown, reset: resetDragPos } = useDraggable(modalRef)

const props = defineProps({
    open:   { type: Boolean, default: false },
    stocks: { type: Array,   default: () => [] },
    title:  { type: String,  default: '三维启动「找候选」' },
    subtitle: { type: String, default: '蓄势中 / 试盘后未突破，加入自选监控' },
})
const emit = defineEmits(['close'])

const scanner = useStockScanner()
const { scanning, scanned, total, currentCode, results, errors, skipped, progressPct } = scanner

// 大盘环境（共享缓存）
const { env: marketEnv, refresh: refreshMarketEnv } = useMarketEnv()
function envColor(level) {
    return ({
        强势: 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca]',
        良好: 'bg-[#fef3c7] text-[#92400e] border-[#fde68a]',
        震荡: 'bg-[#f3f4f6] text-[#475569] border-[#e5e7eb]',
        弱势: 'bg-[#dcfce7] text-[#166534] border-[#bbf7d0]',
        破位: 'bg-[#d1fae5] text-[#065f46] border-[#a7f3d0]',
    }[level] || 'bg-[#f3f4f6] text-[#475569]')
}

// 阶段过滤：默认两个都开
const showStage1 = ref(true)
const showStage2 = ref(true)

const filteredResults = computed(() => {
    return results.value.filter(r => {
        if (r.event.currentStage === 1 && !showStage1.value) return false
        if (r.event.currentStage === 2 && !showStage2.value) return false
        return true
    })
})

const sortedResults = computed(() => {
    return [...filteredResults.value].sort((a, b) => {
        // Stage 2 排前面（更接近发车）
        if (a.event.currentStage !== b.event.currentStage) {
            return b.event.currentStage - a.event.currentStage
        }
        // 同阶段：按距突破点距离升序（越近越好）
        const ad = a.distanceToBreakPct == null ? Infinity : Math.abs(a.distanceToBreakPct)
        const bd = b.distanceToBreakPct == null ? Infinity : Math.abs(b.distanceToBreakPct)
        return ad - bd
    })
})

async function startScan() {
    if (scanning.value) return
    savedCodes.value = new Set()    // 每次扫描是独立 session，清空已收藏标记
    selectedCodes.value = new Set() // 同步清空多选
    refreshMarketEnv()
    await scanner.scan(props.stocks, async (stock, klines) => {
        const events = detectThreeStageLaunch(klines)
        // 只关心还没突破的：currentStage 1 / 2
        const candidates = events.filter(e => e.currentStage === 1 || e.currentStage === 2)
        if (candidates.length === 0) return null
        // 取 s1End 最大的（最新的蓄势段）
        const evt = candidates.sort((a, b) => (b.s1EndIdx || 0) - (a.s1EndIdx || 0))[0]
        const lastClose = +klines[klines.length - 1].close
        // 突破触发点：max(s2High, s1Upper) — Stage 2 的 s2High 是从 detector 拿不到，但 s1Upper 一定有
        // 简化：用 s1Upper 作为突破参考线
        const breakLevel = evt.s1Upper
        const distanceToBreakPct = breakLevel
            ? (lastClose - breakLevel) / breakLevel * 100
            : null
        // 蓄势已经持续了多少根
        const consolidationBars = evt.s1EndIdx - evt.s1StartIdx + 1

        // === 候选有效性过滤 ===
        // 1) 距突破 > 5%：detector S3 窗口（30 根）漏抓的"远古逃逸突破"，不是真候选
        // 2) 蓄势 > 80 根：先涨后跌再蓄势的长期横盘，多为"卡住的票"，不符合主升需求
        if (distanceToBreakPct != null && distanceToBreakPct > 5)  return null
        if (consolidationBars > 80) return null

        // 当前价距黄金买点（如果 Stage 2 已确立，黄金买点是有意义的回踩位）
        const distanceToGoldenPct = evt.goldenBuyPrice
            ? (lastClose - evt.goldenBuyPrice) / evt.goldenBuyPrice * 100
            : null
        return {
            code: stock.code,
            name: stock.name || stock.code,
            event: evt,
            lastPrice: lastClose,
            distanceToBreakPct,
            distanceToGoldenPct,
            consolidationBars,
        }
    })
}

function openInDrawer(r) {
    const list = sortedResults.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(r.code, r.name, list)
}

function addToWatch(r, e) {
    e?.stopPropagation()
    openAddToWatchlist(r.code, r.name, r.lastPrice)
}

// 已保存到候选池的 code 集合 —— 让按钮变金色 + 改成"已收藏"，避免重复点
const savedCodes = ref(new Set())

// ---------------- 批量选择 ----------------
const selectedCodes = ref(new Set())

function toggleRow(code, e) {
    e?.stopPropagation()
    const next = new Set(selectedCodes.value)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    selectedCodes.value = next
}

const allVisibleSelected = computed(() => {
    if (!sortedResults.value.length) return false
    return sortedResults.value.every(r => selectedCodes.value.has(r.code))
})

function toggleAll() {
    if (allVisibleSelected.value) {
        // 全已选 → 全清空
        const next = new Set(selectedCodes.value)
        for (const r of sortedResults.value) next.delete(r.code)
        selectedCodes.value = next
    } else {
        // 否则 → 全选当前可见
        const next = new Set(selectedCodes.value)
        for (const r of sortedResults.value) next.add(r.code)
        selectedCodes.value = next
    }
}

const batchProcessing = ref(false)

async function batchSaveToCandidatePool() {
    if (batchProcessing.value || !selectedCodes.value.size) return
    const targets = sortedResults.value.filter(
        r => selectedCodes.value.has(r.code) && !savedCodes.value.has(r.code),
    )
    if (!targets.length) {
        pushSuccess('选中的票都已经在候选池里了')
        return
    }
    batchProcessing.value = true
    let okCount = 0
    let failCount = 0
    try {
        // 串行调用 —— 后端是 SQLite 单写，串行避免锁竞争 + 控制 EM 节奏
        for (const r of targets) {
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
                    source:             '三维启动找候选',
                })
                if (res.ok) {
                    okCount++
                    savedCodes.value = new Set([...savedCodes.value, r.code])
                } else {
                    failCount++
                }
            } catch {
                failCount++
            }
        }
        let msg = `已批量收藏 ${okCount} 只到候选池`
        if (failCount) msg += `，${failCount} 只失败`
        pushSuccess(msg)
    } finally {
        batchProcessing.value = false
    }
}

function batchAddToWatchlist() {
    if (!selectedCodes.value.size) return
    const targets = sortedResults.value
        .filter(r => selectedCodes.value.has(r.code))
        .map(r => ({ code: r.code, name: r.name, price: r.lastPrice }))
    if (!targets.length) return
    // 弹起全局批量加自选 modal —— 选择目标分组后由 modal 调 importBatchAdd
    openAddToWatchlistBatch(targets)
}

async function saveToCandidatePool(r, e) {
    e?.stopPropagation()
    if (savedCodes.value.has(r.code)) return     // 已存过，幂等
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
            source:             '三维启动找候选',
        })
        if (res.ok) {
            savedCodes.value = new Set([...savedCodes.value, r.code])
            pushSuccess(`已收藏 ${r.name}（${r.code}）→ 候选池`)
        } else {
            pushError(res.error || '收藏失败')
        }
    } catch (err) {
        pushError(`收藏失败：${err}`)
    }
}


function fmtPrice(v) { return v == null ? '—' : (+v).toFixed(2) }
function fmtPct(v, digits = 2) {
    if (v == null) return '—'
    return (v >= 0 ? '+' : '') + v.toFixed(digits) + '%'
}

// 距突破点：负=还在下面（未突破，正常）；正=刚刚突破或假突破回落
function breakDistanceLabel(p) {
    if (p == null) return { text: '—', cls: 'text-[#999]', icon: '' }
    if (p > 0)              return { text: fmtPct(p), cls: 'text-[#f59e0b] font-semibold', icon: '⚠' }   // 已超过突破线，可能马上发车或假突破
    if (p >= -1.5)          return { text: fmtPct(p), cls: 'text-[#dc2626] font-bold', icon: '◆' }       // 临门一脚（-1.5% 内）
    if (p >= -5)            return { text: fmtPct(p), cls: 'text-[#dc2626] font-semibold', icon: '▲' }   // 接近突破
    return                  { text: fmtPct(p), cls: 'text-[#475569]', icon: '·' }                          // 还在中下沿
}

function stageBadge(stage) {
    if (stage === 1) return { text: '蓄势', cls: 'bg-[#fef3c7] text-[#92400e]', icon: '🌱' }
    if (stage === 2) return { text: '试盘', cls: 'bg-[#fee2e2] text-[#991b1b]', icon: '🔥' }
    return { text: '?', cls: 'bg-[#f3f4f6] text-[#666]', icon: '' }
}

watch(() => props.open, (v) => {
    if (v) {
        resetDragPos()      // 每次打开都回到屏幕中央
        startScan()
    } else {
        scanner.cancel()
    }
})

function onKeydown(e) {
    if (e.key === 'Escape' && props.open) emit('close')
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => {
    window.removeEventListener('keydown', onKeydown)
    scanner.cancel()
})
</script>

<template>
    <Transition name="fade">
        <!-- 遮罩：不再点击关闭（用户要求只通过 ✕ 关），仅起视觉聚焦作用 -->
        <div v-if="open"
             class="fixed inset-0 bg-black/30 z-[300]">
            <!-- 弹窗本体：absolute 定位，初始 50%+translate 居中；拖动后 left/top 跟踪鼠标 -->
            <div ref="modalRef"
                 :style="draggableStyle"
                 class="absolute bg-white rounded-[10px] shadow-[0_10px_40px_rgba(0,0,0,0.18)]
                        w-[820px] max-w-[94vw] max-h-[82vh] overflow-hidden flex flex-col">
                <!-- Header（可拖动；点其上的按钮 / 输入元素不触发拖动）-->
                <div @mousedown="onHeaderMouseDown"
                     class="px-[16px] py-[12px] border-b border-[#f0f0f0] flex items-center gap-[10px] cursor-move select-none">
                    <span class="text-[14px] font-bold text-[#111]">{{ title }}</span>
                    <span class="text-[11px] text-[#94a3b8]">{{ subtitle }}</span>
                    <!-- 大盘环境 chip -->
                    <span v-if="marketEnv"
                          :class="['text-[10px] font-semibold px-[6px] py-[1px] rounded-[3px] border tabular-nums', envColor(marketEnv.level)]"
                          :title="(marketEnv.reasons || []).join(' · ')">
                        大盘 {{ marketEnv.level }} {{ marketEnv.score }}
                    </span>

                    <!-- 阶段过滤 -->
                    <div class="flex items-center gap-[6px] ml-[8px]">
                        <label class="flex items-center gap-[3px] text-[11px] cursor-pointer select-none"
                               :class="showStage1 ? 'text-[#92400e]' : 'text-[#bbb]'">
                            <input type="checkbox" v-model="showStage1" class="w-[11px] h-[11px] accent-[#f59e0b] cursor-pointer">
                            <span>🌱 蓄势</span>
                        </label>
                        <label class="flex items-center gap-[3px] text-[11px] cursor-pointer select-none"
                               :class="showStage2 ? 'text-[#991b1b]' : 'text-[#bbb]'">
                            <input type="checkbox" v-model="showStage2" class="w-[11px] h-[11px] accent-[#dc2626] cursor-pointer">
                            <span>🔥 试盘</span>
                        </label>
                    </div>

                    <span v-if="!scanning && results.length > 0"
                          class="ml-auto text-[12px] text-[#dc2626] font-semibold tabular-nums">
                        命中 {{ filteredResults.length }} / {{ results.length }} / 共 {{ total }}
                    </span>
                    <span v-else-if="!scanning && total > 0"
                          class="ml-auto text-[12px] text-[#94a3b8]">
                        无候选（{{ total }} 只）
                    </span>
                    <button v-if="!scanning"
                            @click="startScan"
                            :disabled="!props.stocks.length"
                            class="text-[11px] px-[10px] py-[3px] rounded-[4px] border border-[#dc2626]/30
                                   text-[#dc2626] hover:bg-[#fff5f5] disabled:opacity-40 disabled:cursor-not-allowed transition">
                        重新扫描
                    </button>
                    <button @click="$emit('close')"
                            class="text-[14px] text-[#888] hover:text-[#dc2626] w-[22px] h-[22px] rounded flex items-center justify-center">
                        ✕
                    </button>
                </div>

                <!-- Progress -->
                <div v-if="scanning"
                     class="px-[16px] py-[10px] border-b border-[#f0f0f0] bg-[#fafafa]">
                    <div class="flex items-center justify-between text-[12px] mb-[6px]">
                        <span class="text-[#475569]">
                            扫描中 <span class="font-bold tabular-nums text-[#dc2626]">{{ scanned }}</span>
                            / {{ total }}
                            <span v-if="currentCode" class="ml-[8px] text-[#94a3b8] font-mono">{{ currentCode }}</span>
                        </span>
                        <button @click="scanner.cancel()"
                                class="text-[11px] px-[8px] py-[2px] text-[#94a3b8] hover:text-[#dc2626] transition">
                            取消
                        </button>
                    </div>
                    <div class="h-[4px] bg-[#e5e7eb] rounded-full overflow-hidden">
                        <div class="h-full bg-[#dc2626] transition-all duration-200"
                             :style="{ width: progressPct + '%' }"></div>
                    </div>
                </div>

                <!-- Body -->
                <div class="flex-1 overflow-y-auto custom-scrollbar">
                    <div v-if="!scanning && !results.length && total === 0"
                         class="py-[60px] text-center text-[12px] text-[#999]">
                        点击「重新扫描」开始
                    </div>
                    <div v-else-if="!scanning && !sortedResults.length"
                         class="py-[60px] text-center">
                        <div class="text-[28px] mb-[8px]">🌫️</div>
                        <div class="text-[13px] text-[#666]">
                            <span v-if="!results.length">当前列表暂无三维启动候选</span>
                            <span v-else>当前过滤条件下无结果</span>
                        </div>
                        <div class="text-[11px] text-[#aaa] mt-[4px]">
                            扫描了 {{ total }} 只
                            <span v-if="skipped">，过滤 {{ skipped }} 只北交所</span>
                            <span v-if="errors.length">，{{ errors.length }} 只数据不足/失败</span>
                        </div>
                    </div>

                    <!-- 批量操作工具栏（选中 ≥1 只时高亮）-->
                    <div v-else-if="sortedResults.length"
                         class="px-[12px] py-[6px] border-b border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[10px] text-[11px]">
                        <span class="text-[#666]">
                            已选
                            <span class="font-bold tabular-nums" :class="selectedCodes.size ? 'text-[#dc2626]' : 'text-[#999]'">
                                {{ selectedCodes.size }}
                            </span>
                            / {{ sortedResults.length }}
                        </span>
                        <button @click="batchSaveToCandidatePool"
                                :disabled="!selectedCodes.size || batchProcessing"
                                class="px-[10px] py-[3px] rounded-[4px] border transition
                                       disabled:opacity-50 disabled:cursor-not-allowed"
                                :class="selectedCodes.size && !batchProcessing
                                    ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] hover:bg-[#fde68a] font-semibold'
                                    : 'bg-white text-[#999] border-[#e5e7eb]'">
                            ★ 批量收藏到候选池
                        </button>
                        <button @click="batchAddToWatchlist"
                                :disabled="!selectedCodes.size"
                                class="px-[10px] py-[3px] rounded-[4px] border transition
                                       disabled:opacity-50 disabled:cursor-not-allowed"
                                :class="selectedCodes.size
                                    ? 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca] hover:bg-[#fecaca] font-semibold'
                                    : 'bg-white text-[#999] border-[#e5e7eb]'">
                            + 批量加自选
                        </button>
                        <span v-if="batchProcessing" class="text-[#dc2626] font-semibold animate-pulse">处理中...</span>
                    </div>

                    <table v-if="sortedResults.length" class="w-full text-left border-collapse text-[12px]">
                        <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                            <tr>
                                <th class="px-[8px] py-[8px] font-normal text-center w-[32px]">
                                    <input type="checkbox"
                                           :checked="allVisibleSelected"
                                           @change="toggleAll"
                                           :title="allVisibleSelected ? '取消全选' : '全选当前结果'"
                                           class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                                </th>
                                <th class="px-[12px] py-[8px] font-normal w-[150px]">股票</th>
                                <th class="px-[8px]  py-[8px] font-normal text-center w-[60px]">阶段</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[70px]">现价</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[80px]">突破线</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[100px]">距突破</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[80px]">蓄势天数</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[80px]">止损</th>
                                <th class="px-[8px]  py-[8px] font-normal text-center w-[110px]">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="r in sortedResults" :key="r.code"
                                @click="openInDrawer(r)"
                                class="border-b border-[#f5f5f5] hover:bg-[#fff5f5] cursor-pointer transition-colors"
                                :class="selectedCodes.has(r.code) ? 'bg-[#fffaf0]' : ''">
                                <!-- 多选 checkbox -->
                                <td class="px-[8px] py-[8px] text-center" @click.stop>
                                    <input type="checkbox"
                                           :checked="selectedCodes.has(r.code)"
                                           @change="toggleRow(r.code, $event)"
                                           class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer">
                                </td>
                                <td class="px-[12px] py-[8px]">
                                    <div class="flex flex-col">
                                        <span class="text-[13px] font-semibold text-[#111] truncate">{{ r.name }}</span>
                                        <span class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</span>
                                    </div>
                                </td>
                                <td class="px-[8px] py-[8px] text-center">
                                    <span class="text-[10px] font-semibold px-[6px] py-[2px] rounded-[3px]"
                                          :class="stageBadge(r.event.currentStage).cls">
                                        {{ stageBadge(r.event.currentStage).icon }} {{ stageBadge(r.event.currentStage).text }}
                                    </span>
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums font-semibold text-[#111]">
                                    {{ fmtPrice(r.lastPrice) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#dc2626] font-semibold">
                                    {{ fmtPrice(r.event.s1Upper) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums">
                                    <span :class="breakDistanceLabel(r.distanceToBreakPct).cls">
                                        <span class="mr-[2px]">{{ breakDistanceLabel(r.distanceToBreakPct).icon }}</span>
                                        {{ breakDistanceLabel(r.distanceToBreakPct).text }}
                                    </span>
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#475569]">
                                    {{ r.consolidationBars }} 根
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#059669]">
                                    {{ fmtPrice(r.event.stopLossPrice) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-center" @click.stop>
                                    <div class="flex items-center justify-center gap-[4px]">
                                        <!-- ⭐ 收藏到候选池：保存当前 snapshot，到「候选池」tab 持续追踪 -->
                                        <button @click="saveToCandidatePool(r, $event)"
                                                :disabled="savedCodes.has(r.code)"
                                                :title="savedCodes.has(r.code) ? '已加入候选池' : '收藏到候选池，持续追踪买点'"
                                                class="text-[12px] px-[6px] py-[3px] rounded-[3px] transition shadow-sm border"
                                                :class="savedCodes.has(r.code)
                                                    ? 'bg-[#fef3c7] text-[#b45309] border-[#fde68a] cursor-default'
                                                    : 'bg-white text-[#f59e0b] border-[#fde68a] hover:bg-[#fffbeb]'">
                                            {{ savedCodes.has(r.code) ? '★' : '☆' }}
                                        </button>
                                        <button @click="addToWatch(r, $event)"
                                                title="加入自选 / 持仓"
                                                class="text-[10px] font-bold text-white bg-[#dc2626] px-[8px] py-[3px] rounded-[3px]
                                                       hover:bg-[#991b1b] transition shadow-sm">
                                            + 自选
                                        </button>
                                    </div>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <!-- Footer：图例 -->
                <div v-if="results.length > 0"
                     class="px-[16px] py-[8px] border-t border-[#f0f0f0] bg-[#fafafa] text-[10px] text-[#94a3b8]
                            flex flex-wrap items-center gap-[14px]">
                    <span><span class="text-[#dc2626] font-bold">◆</span> 临门一脚 (距突破 ≤1.5%)</span>
                    <span><span class="text-[#dc2626] font-semibold">▲</span> 接近突破 (1.5~5%)</span>
                    <span><span class="text-[#f59e0b]">⚠</span> 已超突破线（注意假突破）</span>
                    <span class="ml-auto text-[#bbb]">点击行打开 K 线 · + 自选 加入监控</span>
                </div>
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 3px; }
</style>
