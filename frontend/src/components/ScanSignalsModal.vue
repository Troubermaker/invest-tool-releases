<script setup>
/**
 * 三维启动「找发车」扫描器（fresh Stage 3 突破）。
 *
 * 用法：从自选页传入 stocks（[{code, name}]），点开按钮即开始扫描。
 * 仅列出 isFresh + currentStage===3 的票（最近 30 根内已突破，可追入）。
 * 按"距黄金买点距离"排序：越接近 0 越靠前。
 */
import { ref, computed, watch, onUnmounted, onMounted } from 'vue'
import { detectThreeStageLaunch, gradeFreshSignal, hasWeeklyTrendConfirmation } from '../composables/useTechIndicators'
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
    stocks: { type: Array,   default: () => [] },  // [{code, name}]
    title:  { type: String,  default: '三维启动「找发车」' },
})
const emit = defineEmits(['close'])

const scanner = useStockScanner()
const { scanning, scanned, total, currentCode, results, errors, skipped, progressPct } = scanner

// 大盘环境（共享缓存，5min TTL）
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

// 评级筛选 — 排序顺序按实战推荐度（数据驱动）：B 最佳，A 次之，C 保守，S 警示
// 全市场 7 天回测胜率：B 49% / A 40% / C 62%（保守胜率高但 max 仅 +52%）/ S 0%（形态完美=已透支）
// 默认 minGrade='C'：显示 B+A+C，过滤掉 S 透支警示档
const minGrade = ref('C')   // 'B' | 'A' | 'C' | 'S'
const GRADE_ORDER = { B: 0, A: 1, C: 2, S: 3 }
const GRADE_FILTER_ORDER = ['B', 'A', 'C', 'S']   // 表头 chip 显示顺序（最推荐 → 最警示）

// 突破时效窗口：只看最近 N 根内的突破。30 天前的"突破"再追买等于追高，
// 默认 15（主升浪起爆区 + 第一波回踩）。chip 切换是纯客户端过滤，不重扫。
const freshWindow = ref(15)
const FRESH_OPTIONS = [5, 10, 15, 30]

// 排序：周共振优先 → grade 推荐度 → score → 突破新鲜度
// 过滤：grade 满足下限 + 突破距今 ≤ freshWindow
//
// Phase 4 数据驱动：周共振票胜率 51% vs 无共振 43%（+8pp），grade 是次要维度。
// 双键排序让用户先看到"周共振 + B 级"这种最强组合，无共振票自然沉底。
const sortedResults = computed(() => {
    const minRank = GRADE_ORDER[minGrade.value] ?? 3
    const wRank = (w) => w === true ? 0 : (w === false ? 2 : 1)   // ✓ 优先 / 未知中 / ✗ 沉底
    return results.value
        .filter(r => (GRADE_ORDER[r.grade?.grade] ?? 3) <= minRank)
        .filter(r => {
            const bars = r.event?.barsAgoFromS3
            return bars == null || bars <= freshWindow.value
        })
        .sort((a, b) => {
            // 第一键：周共振（✓ → 未知 → ✗）
            const aw = wRank(a.weeklyConfirmed ?? a.grade?.weeklyConfirmed)
            const bw = wRank(b.weeklyConfirmed ?? b.grade?.weeklyConfirmed)
            if (aw !== bw) return aw - bw
            // 第二键：grade 推荐度（B → A → C → S）
            const ar = GRADE_ORDER[a.grade?.grade] ?? 3
            const br = GRADE_ORDER[b.grade?.grade] ?? 3
            if (ar !== br) return ar - br
            // 第三键：score 降序
            const as = a.grade?.score ?? 0, bs = b.grade?.score ?? 0
            if (as !== bs) return bs - as
            // 第四键：突破新鲜度（barsAgoFromS3 升序）
            const ab = a.event?.barsAgoFromS3 ?? 999
            const bb = b.event?.barsAgoFromS3 ?? 999
            return ab - bb
        })
})

function gradeStyle(g) {
    return ({
        S: { cls: 'bg-[#dc2626] text-white', label: 'S' },
        A: { cls: 'bg-[#ea580c] text-white', label: 'A' },
        B: { cls: 'bg-[#f59e0b] text-white', label: 'B' },
        C: { cls: 'bg-[#94a3b8] text-white', label: 'C' },
    }[g] || { cls: 'bg-[#cbd5e1] text-[#475569]', label: '?' })
}

async function startScan() {
    if (scanning.value) return
    savedCodes.value = new Set()        // 每次扫描独立 session
    selectedCodes.value = new Set()
    // 等大盘环境拿到再开扫，这样每只票评级都能用上 envScore
    const envSnapshot = await refreshMarketEnv().catch(() => null)
    // 始终用最大窗口（30）扫描拿到最广的 fresh 集合；UI 用 freshWindow 客户端过滤
    await scanner.scan(props.stocks, async (stock, klines) => {
        const events = detectThreeStageLaunch(klines, { freshWithinBars: 30 })
        const fresh = events.filter(e => e.isFresh && e.currentStage === 3)
        if (fresh.length === 0) return null
        const evt = fresh.sort((a, b) => (b.s3Idx || 0) - (a.s3Idx || 0))[0]
        const lastClose = +klines[klines.length - 1].close

        // Phase 4：拉周 K 判周线趋势确认（走缓存，命中后毫秒级）
        let weeklyConfirmed = null
        try {
            const wRes = await api.getStockKlineViaTdxCached(stock.code, '周K')
            if (wRes?.ok && Array.isArray(wRes.data) && wRes.data.length >= 25) {
                weeklyConfirmed = hasWeeklyTrendConfirmation(wRes.data)
            }
        } catch { /* 周K失败不阻塞，weeklyConfirmed 留 null（不参与降级）*/ }

        // distancePct 给表格"距回踩"列展示用（评级内部自取现价计算）
        const distancePct = evt.goldenBuyPrice
            ? (lastClose - evt.goldenBuyPrice) / evt.goldenBuyPrice * 100
            : null
        const grade = gradeFreshSignal(klines, evt, envSnapshot || marketEnv.value, weeklyConfirmed)
        return {
            code: stock.code,
            name: stock.name || stock.code,
            event: evt,
            lastPrice: lastClose,
            distancePct,
            weeklyConfirmed,
            grade,
        }
    })
}

function openInDrawer(r) {
    const list = sortedResults.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(r.code, r.name, list)
}

// ---------------- 候选池收藏 ----------------
// 找发车扫出的是 Stage 3（已突破）的票，存进候选池后状态会被推断为「已突破」
// 用户在候选池就能持续追踪：是涨上去 / 假突破回落 / 还是回踩黄金买点
const savedCodes = ref(new Set())

async function saveToCandidatePool(r, e) {
    e?.stopPropagation()
    if (savedCodes.value.has(r.code)) return
    try {
        const res = await api.addCandidatePick({
            code:               r.code,
            name:               r.name,
            stage:              r.event?.currentStage ?? 3,
            save_price:         r.lastPrice,
            break_level:        r.event?.s1Upper ?? null,
            golden_price:       r.event?.goldenBuyPrice ?? null,
            s1_lower:           r.event?.s1Lower ?? null,
            consolidation_bars: null,
            source:             '三维启动找发车',
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

function addToWatch(r, e) {
    e?.stopPropagation()
    openAddToWatchlist(r.code, r.name, r.lastPrice)
}

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
        const next = new Set(selectedCodes.value)
        for (const r of sortedResults.value) next.delete(r.code)
        selectedCodes.value = next
    } else {
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
        // 串行：避免撞 SQLite 单写锁 + 控制 EM 节奏
        for (const r of targets) {
            try {
                const res = await api.addCandidatePick({
                    code:               r.code,
                    name:               r.name,
                    stage:              r.event?.currentStage ?? 3,
                    save_price:         r.lastPrice,
                    break_level:        r.event?.s1Upper ?? null,
                    golden_price:       r.event?.goldenBuyPrice ?? null,
                    s1_lower:           r.event?.s1Lower ?? null,
                    consolidation_bars: null,
                    source:             '三维启动找发车',
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
    openAddToWatchlistBatch(targets)
}

function fmtPrice(v) { return v == null ? '—' : (+v).toFixed(2) }
function fmtPct(v, digits = 2) {
    if (v == null) return '—'
    return (v >= 0 ? '+' : '') + v.toFixed(digits) + '%'
}
function fmtDate(t) {
    if (!t) return '—'
    if (typeof t === 'string') return t.slice(0, 10).replaceAll('-', '/')
    if (typeof t === 'number') {
        const ms = t > 1e12 ? t : t * 1000
        const d = new Date(ms)
        if (!isNaN(d.getTime())) {
            const yyyy = d.getFullYear()
            const mm = String(d.getMonth() + 1).padStart(2, '0')
            const dd = String(d.getDate()).padStart(2, '0')
            return `${yyyy}/${mm}/${dd}`
        }
    }
    if (typeof t === 'object' && t.year) {
        const mm = String(t.month).padStart(2, '0')
        const dd = String(t.day).padStart(2, '0')
        return `${t.year}/${mm}/${dd}`
    }
    return '—'
}
function distanceLabel(p) {
    if (p == null) return { text: '—', cls: 'text-[#999]', icon: '' }
    if (Math.abs(p) <= 1.5) return { text: fmtPct(p), cls: 'text-[#dc2626] font-bold', icon: '◆' }
    if (p < 0)              return { text: fmtPct(p), cls: 'text-[#059669] font-semibold', icon: '▼' }
    if (p <= 5)             return { text: fmtPct(p), cls: 'text-[#f59e0b] font-semibold', icon: '▲' }
    return                  { text: fmtPct(p), cls: 'text-[#9ca3af]', icon: '▲' }
}

// 追涨买点 = 突破 K 收盘 ×1.01（突破当天介入的合理价）
// 距追涨 = (现价 - 追涨买点) / 追涨买点 × 100%
// 这才是"现在追不追高"的真实指标 — 距黄金买点是"等回踩才有意义的理想位"
function chasePrice(r) {
    const bp = r.event?.breakoutPrice
    return bp != null ? bp * 1.01 : null
}
function distanceToChasePct(r) {
    const cp = chasePrice(r)
    if (cp == null || r.lastPrice == null) return null
    return (r.lastPrice - cp) / cp * 100
}
// 形状双编码避开红绿语义：◎ 完美 / ▼ 折扣 / ▲ 可追 / △ 慎追 / ⚠ 追高
function chaseLabel(p) {
    if (p == null) return { text: '—', cls: 'text-[#999]', icon: '' }
    if (p < -1)             return { text: fmtPct(p), cls: 'text-[#0369a1] font-semibold', icon: '▼' }   // 还在追涨买点之下
    if (p <= 1)             return { text: fmtPct(p), cls: 'text-[#dc2626] font-bold',     icon: '◎' }   // 完美追涨区
    if (p <= 5)             return { text: fmtPct(p), cls: 'text-[#ea580c] font-semibold', icon: '▲' }   // 可追
    if (p <= 10)            return { text: fmtPct(p), cls: 'text-[#94a3b8]',                icon: '△' }   // 慎追
    return                  { text: fmtPct(p), cls: 'text-[#cbd5e1]',                icon: '⚠' }          // 已涨过 >10% = 真追高
}
// 距今天数形状编码：● 起爆区 / ◐ 第一波 / ○ 中段 / △ 偏老
function barsAgoLabel(bars) {
    if (bars == null) return { icon: '', cls: 'text-[#94a3b8]' }
    if (bars <= 5)  return { icon: '●', cls: 'text-[#dc2626] font-semibold' }
    if (bars <= 10) return { icon: '◐', cls: 'text-[#ea580c] font-semibold' }
    if (bars <= 15) return { icon: '○', cls: 'text-[#94a3b8]' }
    return                 { icon: '△', cls: 'text-[#cbd5e1]' }
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
                        w-[760px] max-w-[92vw] max-h-[82vh] overflow-hidden flex flex-col">
                <!-- Header（可拖动；点其上的按钮 / 输入元素不触发拖动）-->
                <div @mousedown="onHeaderMouseDown"
                     class="px-[16px] py-[12px] border-b border-[#f0f0f0] flex items-center gap-[10px] cursor-move select-none">
                    <span class="text-[14px] font-bold text-[#111]">{{ title }}</span>
                    <span class="text-[11px] text-[#94a3b8]">已突破 0~{{ freshWindow }} 根，可追入</span>
                    <!-- 大盘环境 chip -->
                    <span v-if="marketEnv"
                          :class="['text-[10px] font-semibold px-[6px] py-[1px] rounded-[3px] border tabular-nums', envColor(marketEnv.level)]"
                          :title="(marketEnv.reasons || []).join(' · ')">
                        大盘 {{ marketEnv.level }} {{ marketEnv.score }}
                    </span>
                    <!-- 评级过滤 chip — 按推荐度排序（B 最优 → S 警示）-->
                    <div v-if="!scanning && results.length > 0"
                         class="flex items-center gap-[2px] ml-[4px]"
                         title="评级是&quot;形态强度+市场定价度&quot;复合指标。回测显示 B 级胜率最高（49%），S 级形态完美但已透支（避免追入）。点击展开覆盖范围">
                        <button v-for="g in GRADE_FILTER_ORDER" :key="g"
                                @click="minGrade = g"
                                :class="['text-[10px] font-bold w-[20px] h-[18px] rounded-[3px] transition',
                                         minGrade === g
                                             ? gradeStyle(g).cls + ' shadow'
                                             : 'bg-[#f3f4f6] text-[#94a3b8] hover:bg-[#e5e7eb]']"
                                :title="`覆盖到 ${g} 级（${({B:'仅推荐档',A:'推荐+强势',C:'推荐+强势+保守',S:'全部含 S 警示'})[g]}）`">
                            {{ g }}+
                        </button>
                    </div>
                    <!-- 突破时效窗口 chip：≤N 天内的突破才算"可追入"。30 天前的突破再买 = 追高 -->
                    <div v-if="!scanning && results.length > 0"
                         class="flex items-center gap-[2px] ml-[2px]"
                         title="突破时效：只看最近 N 根内的突破">
                        <button v-for="n in FRESH_OPTIONS" :key="n"
                                @click="freshWindow = n"
                                :class="['text-[10px] font-semibold h-[18px] px-[5px] rounded-[3px] transition tabular-nums',
                                         freshWindow === n
                                             ? 'bg-[#1e293b] text-white shadow'
                                             : 'bg-[#f3f4f6] text-[#94a3b8] hover:bg-[#e5e7eb]']"
                                :title="`只显示 ≤${n} 天前突破的票`">
                            ≤{{ n }}d
                        </button>
                    </div>
                    <span v-if="!scanning && results.length > 0"
                          class="ml-auto text-[12px] text-[#dc2626] font-semibold tabular-nums">
                        命中 {{ sortedResults.length }} / {{ results.length }} / 共 {{ total }}
                    </span>
                    <span v-else-if="!scanning && total > 0"
                          class="ml-auto text-[12px] text-[#94a3b8]">
                        无命中（{{ total }} 只）
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
                    <div v-else-if="!scanning && !results.length"
                         class="py-[60px] text-center">
                        <div class="text-[28px] mb-[8px]">🌫️</div>
                        <div class="text-[13px] text-[#666]">当前列表暂无 fresh 三维启动信号</div>
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
                                <th class="px-[12px] py-[8px] font-normal w-[60px]">评级</th>
                                <th class="px-[8px]  py-[8px] font-normal text-center w-[36px]"
                                    title="周线趋势确认：close > 周MA20 + 周MA20 上行 + 周MA5 > 周MA20。回测周共振票胜率高 8 个百分点">周</th>
                                <th class="px-[12px] py-[8px] font-normal w-[140px]">股票</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[70px]">现价</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[80px]"
                                    title="回踩买点 = min(试盘 K 中点, 蓄势上沿)。突破后回调到此价位站住 = 稳健加仓位">回踩买点</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[85px]"
                                    title="现价距回踩买点。等回踩才有意义；正常突破强势票常不回到此位">距回踩</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[75px]"
                                    title="突破 K 的收盘价">突破价</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[85px]"
                                    title="距追涨买点（突破价 ×1.01）。这才是&quot;现在追不追高&quot;的真实指标">距追涨</th>
                                <th class="px-[8px]  py-[8px] font-normal text-center w-[80px]"
                                    title="突破距今天数 + 形状编码：● ≤5 起爆 / ◐ ≤10 / ○ ≤15 / △ 偏老">距今</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[65px]">止损</th>
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
                                    <div v-if="r.grade"
                                         class="flex items-center gap-[6px]"
                                         :title="(r.grade.reasons || []).join(' · ')">
                                        <span :class="['inline-flex items-center justify-center w-[24px] h-[20px] rounded-[3px] text-[11px] font-bold',
                                                       gradeStyle(r.grade.grade).cls]">
                                            {{ r.grade.grade }}
                                        </span>
                                        <span class="text-[10px] text-[#94a3b8] tabular-nums">{{ r.grade.score }}</span>
                                    </div>
                                </td>
                                <td class="px-[8px] py-[8px] text-center"
                                    :title="r.weeklyConfirmed === true ? '周线趋势确认（胜率高 8pp）'
                                          : r.weeklyConfirmed === false ? '周线未确认 → grade 已自动降一档'
                                          : '周K数据不足'">
                                    <span v-if="r.weeklyConfirmed === true"  class="text-[#dc2626] font-bold text-[13px]">✓</span>
                                    <span v-else-if="r.weeklyConfirmed === false" class="text-[#94a3b8] text-[13px]">✗</span>
                                    <span v-else class="text-[#cbd5e1]">—</span>
                                </td>
                                <td class="px-[12px] py-[8px]">
                                    <div class="flex flex-col">
                                        <span class="text-[13px] font-semibold text-[#111] truncate">{{ r.name }}</span>
                                        <span class="text-[10px] text-[#999] font-mono tabular-nums">{{ r.code }}</span>
                                    </div>
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums font-semibold text-[#111]">
                                    {{ fmtPrice(r.lastPrice) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#dc2626] font-semibold">
                                    {{ fmtPrice(r.event.goldenBuyPrice) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums">
                                    <span :class="distanceLabel(r.distancePct).cls">
                                        <span class="mr-[2px]">{{ distanceLabel(r.distancePct).icon }}</span>
                                        {{ distanceLabel(r.distancePct).text }}
                                    </span>
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#475569]"
                                    :title="`追涨买点 ${fmtPrice(chasePrice(r))}（突破价 ×1.01）`">
                                    {{ fmtPrice(r.event.breakoutPrice) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums">
                                    <span :class="chaseLabel(distanceToChasePct(r)).cls">
                                        <span class="mr-[2px]">{{ chaseLabel(distanceToChasePct(r)).icon }}</span>
                                        {{ chaseLabel(distanceToChasePct(r)).text }}
                                    </span>
                                </td>
                                <td class="px-[8px] py-[8px] text-center tabular-nums"
                                    :class="barsAgoLabel(r.event.barsAgoFromS3).cls"
                                    :title="`突破日 ${fmtDate(r.event.s3Time)}`">
                                    <span class="mr-[2px]">{{ barsAgoLabel(r.event.barsAgoFromS3).icon }}</span>
                                    {{ r.event.barsAgoFromS3 }}根
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#059669]">
                                    {{ fmtPrice(r.event.stopLossPrice) }}
                                </td>
                                <!-- 操作：★ 收藏候选池 + + 自选 -->
                                <td class="px-[8px] py-[8px] text-center" @click.stop>
                                    <div class="flex items-center justify-center gap-[4px]">
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
                                                title="加入自选"
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

                <!-- Footer：图例（按实战推荐度排序，全市场 7 天回测验证）-->
                <div v-if="results.length > 0"
                     class="px-[16px] py-[8px] border-t border-[#f0f0f0] bg-[#fafafa] text-[10px] text-[#94a3b8]">
                    <div class="flex flex-wrap items-center gap-[10px]">
                        <span class="text-[#666] font-semibold">评级（推荐度排序）:</span>
                        <span class="flex items-center gap-[3px]"
                              title="55-69 分；回测胜率 ~49%、平均收益 +3.2%；形态有瑕疵但市场未透支">
                            <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#f59e0b] text-white font-bold">⭐ B</span>
                            稳健推荐（胜率最高）
                        </span>
                        <span class="flex items-center gap-[3px]"
                              title="70-84 分；回测胜率 ~40%、max +99%、min -25%；潜在大牛股需严控止损">
                            <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#ea580c] text-white font-bold">↗ A</span>
                            强势·高方差
                        </span>
                        <span class="flex items-center gap-[3px]"
                              title="<55 分；回测胜率 62% 但 max 仅 +52%；适合极保守仓位">
                            <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#94a3b8] text-white font-bold">◐ C</span>
                            保守慢涨
                        </span>
                        <span class="flex items-center gap-[3px]"
                              title="≥85 分；回测胜率 0% 平均 -5%；形态完美 = 已充分定价 = mean reversion 风险高，避免追入">
                            <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#dc2626] text-white font-bold">⚠ S</span>
                            形态完美·透支警示
                        </span>
                    </div>
                    <div class="mt-[5px] text-[#bbb]">
                        距追涨 ◎完美 ▲可追 △慎追 ⚠追高 · 距今 ●起爆 ◐第一波 ○中段 · 周 ✓共振(优先排序，胜率+8pp) ✗未共振(沉底) · 点击行打开 K 线
                    </div>
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
