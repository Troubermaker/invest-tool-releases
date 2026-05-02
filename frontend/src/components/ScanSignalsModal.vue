<script setup>
/**
 * 三维启动「找发车」扫描器（fresh Stage 3 突破）。
 *
 * 用法：从自选页传入 stocks（[{code, name}]），点开按钮即开始扫描。
 * 仅列出 isFresh + currentStage===3 的票（最近 30 根内已突破，可追入）。
 * 按"距黄金买点距离"排序：越接近 0 越靠前。
 */
import { ref, computed, watch, onUnmounted, onMounted } from 'vue'
import { detectThreeStageLaunch, gradeFreshSignal } from '../composables/useTechIndicators'
import { openStockChart } from '../composables/useStockChart'
import { useStockScanner } from '../composables/useStockScanner'
import { useMarketEnv } from '../composables/useMarketEnv'

const props = defineProps({
    open:     { type: Boolean, default: false },
    stocks:   { type: Array,   default: () => [] },  // [{code, name}]
    title:    { type: String,  default: '三维启动「找发车」' },
    subtitle: { type: String,  default: '已突破 0~30 根，可追入' },
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

// 评级筛选
const minGrade = ref('C')   // 'S' | 'A' | 'B' | 'C'
const GRADE_ORDER = { S: 0, A: 1, B: 2, C: 3 }

// 排序：按 grade 升序（S 最优）→ 同 grade 按 score 降序
const sortedResults = computed(() => {
    const minRank = GRADE_ORDER[minGrade.value] ?? 3
    return results.value
        .filter(r => (GRADE_ORDER[r.grade?.grade] ?? 3) <= minRank)
        .sort((a, b) => {
            const ar = GRADE_ORDER[a.grade?.grade] ?? 3
            const br = GRADE_ORDER[b.grade?.grade] ?? 3
            if (ar !== br) return ar - br
            const as = a.grade?.score ?? 0, bs = b.grade?.score ?? 0
            return bs - as
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
    // 等大盘环境拿到再开扫，这样每只票评级都能用上 envScore
    const envSnapshot = await refreshMarketEnv().catch(() => null)
    await scanner.scan(props.stocks, async (stock, klines) => {
        const events = detectThreeStageLaunch(klines)
        const fresh = events.filter(e => e.isFresh && e.currentStage === 3)
        if (fresh.length === 0) return null
        const evt = fresh.sort((a, b) => (b.s3Idx || 0) - (a.s3Idx || 0))[0]
        const lastClose = +klines[klines.length - 1].close
        const distancePct = evt.goldenBuyPrice
            ? (lastClose - evt.goldenBuyPrice) / evt.goldenBuyPrice * 100
            : null
        const grade = gradeFreshSignal(klines, evt, distancePct, envSnapshot || marketEnv.value)
        return {
            code: stock.code,
            name: stock.name || stock.code,
            event: evt,
            lastPrice: lastClose,
            distancePct,
            grade,
        }
    })
}

function openInDrawer(r) {
    const list = sortedResults.value.map(x => ({ code: x.code, name: x.name }))
    openStockChart(r.code, r.name, list)
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

watch(() => props.open, (v) => {
    if (v) startScan()
    else scanner.cancel()
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
        <div v-if="open"
             @click="$emit('close')"
             class="fixed inset-0 bg-black/30 z-[300] flex items-center justify-center">
            <div @click.stop
                 class="bg-white rounded-[10px] shadow-[0_10px_40px_rgba(0,0,0,0.18)]
                        w-[760px] max-w-[92vw] max-h-[82vh] overflow-hidden flex flex-col">
                <!-- Header -->
                <div class="px-[16px] py-[12px] border-b border-[#f0f0f0] flex items-center gap-[10px]">
                    <span class="text-[14px] font-bold text-[#111]">{{ title }}</span>
                    <span class="text-[11px] text-[#94a3b8]">{{ subtitle }}</span>
                    <!-- 大盘环境 chip -->
                    <span v-if="marketEnv"
                          :class="['text-[10px] font-semibold px-[6px] py-[1px] rounded-[3px] border tabular-nums', envColor(marketEnv.level)]"
                          :title="(marketEnv.reasons || []).join(' · ')">
                        大盘 {{ marketEnv.level }} {{ marketEnv.score }}
                    </span>
                    <!-- 评级过滤 chip -->
                    <div v-if="!scanning && results.length > 0"
                         class="flex items-center gap-[2px] ml-[4px]">
                        <button v-for="g in ['S','A','B','C']" :key="g"
                                @click="minGrade = g"
                                :class="['text-[10px] font-bold w-[20px] h-[18px] rounded-[3px] transition',
                                         minGrade === g
                                             ? gradeStyle(g).cls + ' shadow'
                                             : 'bg-[#f3f4f6] text-[#94a3b8] hover:bg-[#e5e7eb]']"
                                :title="`显示 ${g} 级及以上`">
                            {{ g }}+
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

                    <table v-else class="w-full text-left border-collapse text-[12px]">
                        <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[11px] text-[#888] z-10">
                            <tr>
                                <th class="px-[12px] py-[8px] font-normal w-[60px]">评级</th>
                                <th class="px-[12px] py-[8px] font-normal w-[140px]">股票</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[70px]">现价</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[80px]">黄金买点</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[90px]">距买点</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[80px]">突破价</th>
                                <th class="px-[8px]  py-[8px] font-normal text-center w-[90px]">突破日</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[60px]">距今</th>
                                <th class="px-[8px]  py-[8px] font-normal text-right w-[70px]">止损</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="r in sortedResults" :key="r.code"
                                @click="openInDrawer(r)"
                                class="border-b border-[#f5f5f5] hover:bg-[#fff5f5] cursor-pointer transition-colors">
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
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#475569]">
                                    {{ fmtPrice(r.event.breakoutPrice) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-center tabular-nums text-[#475569]">
                                    {{ fmtDate(r.event.s3Time) }}
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums"
                                    :class="r.event.barsAgoFromS3 <= 5 ? 'text-[#dc2626] font-semibold' : 'text-[#94a3b8]'">
                                    {{ r.event.barsAgoFromS3 }} 根
                                </td>
                                <td class="px-[8px] py-[8px] text-right tabular-nums text-[#059669]">
                                    {{ fmtPrice(r.event.stopLossPrice) }}
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <!-- Footer：图例 -->
                <div v-if="results.length > 0"
                     class="px-[16px] py-[8px] border-t border-[#f0f0f0] bg-[#fafafa] text-[10px] text-[#94a3b8]
                            flex flex-wrap items-center gap-[12px]">
                    <span class="flex items-center gap-[3px]">
                        <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#dc2626] text-white font-bold">S</span>
                        ≥85 多重共振，可重仓
                    </span>
                    <span class="flex items-center gap-[3px]">
                        <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#ea580c] text-white font-bold">A</span>
                        70-84 主力意图明显
                    </span>
                    <span class="flex items-center gap-[3px]">
                        <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#f59e0b] text-white font-bold">B</span>
                        55-69 信号成立有瑕疵
                    </span>
                    <span class="flex items-center gap-[3px]">
                        <span class="inline-block px-[5px] py-[1px] rounded-[2px] bg-[#94a3b8] text-white font-bold">C</span>
                        &lt;55 观望
                    </span>
                    <span class="ml-auto text-[#bbb]">悬停评级看详情 · 点击行打开 K 线</span>
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
