<script setup>
/**
 * 底部滑出的 K 线 drawer。挂在 App.vue 一次，全局可见。
 *
 * - 由 useStockChart.openStockChart(code, name, stockList?) 触发显示
 * - 如果传了 stockList（≥2 项），左侧显示该列表，点击切换 K 线
 * - 单只票（无列表）则隐藏左侧栏，K 线全宽
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import StockChart from './StockChart.vue'
import { stockChartState, closeStockChart, switchToStock } from '../composables/useStockChart'
import { api } from '../api/client'

const visible   = computed(() => stockChartState.value.visible)
const code      = computed(() => stockChartState.value.code)
const name      = computed(() => stockChartState.value.name)
const stockList = computed(() => stockChartState.value.stockList || [])
const showSidebar = computed(() => stockList.value.length >= 2)

// 侧栏行情：拉一次 → drawer 关闭前不再刷（避免频繁打 EM）
const quotes = ref({})  // { code: { price, changePct } }
async function fetchQuotes() {
    if (!showSidebar.value) return
    const codes = stockList.value.map(s => s.code)
    if (!codes.length) return
    try {
        const res = await api.getBatchQuotes(codes)
        if (res.ok && res.data) quotes.value = res.data
    } catch (e) { /* 静默 */ }
}
watch(visible, (v) => {
    if (v) fetchQuotes()
    else quotes.value = {}
})
watch(() => stockList.value.length, () => {
    if (visible.value) fetchQuotes()
})

function onKeydown(e) {
    if (e.key === 'Escape' && visible.value) closeStockChart()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
    <!-- 半透明背景遮罩（可点击关闭）-->
    <Transition name="fade">
        <div v-if="visible"
             @click="closeStockChart"
             class="fixed inset-0 bg-black/15 z-[250]"></div>
    </Transition>

    <!-- 底部滑出的 drawer -->
    <Transition name="slide-up">
        <div v-if="visible"
             class="fixed left-0 right-0 bottom-0 z-[260]
                    bg-white rounded-t-[10px] shadow-[0_-10px_40px_rgba(0,0,0,0.18)]
                    overflow-hidden flex flex-col"
             style="height: 92vh;">
            <!-- 顶部抓手条（视觉） -->
            <div class="h-[6px] flex items-center justify-center shrink-0">
                <div class="w-[40px] h-[3px] rounded-full bg-[#cbd5e1]"></div>
            </div>

            <!-- 主体：左侧股票列表（可选）+ 右侧 K 线 -->
            <div class="flex-1 flex min-h-0 overflow-hidden">
                <!-- 左侧股票列表（仅 stockList ≥ 2 项时显示）-->
                <aside v-if="showSidebar"
                       class="w-[180px] shrink-0 border-r border-[#e5e7eb] bg-[#fafafa] flex flex-col">
                    <div class="px-[10px] py-[6px] text-[10px] text-[#888] border-b border-[#f0f0f0] shrink-0
                                flex items-center justify-between">
                        <span>列表 {{ stockList.length }} 只</span>
                       
                    </div>
                    <div class="flex-1 overflow-y-auto">
                        <button v-for="s in stockList" :key="s.code"
                                @click="switchToStock(s.code, s.name)"
                                class="w-full text-left px-[10px] py-[7px] border-b border-[#f0f0f0]
                                       flex flex-col gap-[1px] transition-colors"
                                :class="s.code === code
                                    ? 'bg-[#fff5f5] border-l-[3px] border-l-[#dc2626]'
                                    : 'hover:bg-[#f5f5f5] border-l-[3px] border-l-transparent'">
                            <!-- 第 1 行：名字 + 价格 -->
                            <div class="flex items-baseline justify-between gap-[6px]">
                                <span class="text-[12px] font-semibold truncate flex-1"
                                      :class="s.code === code ? 'text-[#dc2626]' : 'text-[#111]'">
                                    {{ s.name || s.code }}
                                </span>
                                <span v-if="quotes[s.code]?.price != null"
                                      class="text-[12px] font-bold tabular-nums shrink-0"
                                      :class="(quotes[s.code]?.changePct ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#059669]'">
                                    {{ quotes[s.code].price.toFixed(2) }}
                                </span>
                            </div>
                            <!-- 第 2 行：代码 + 涨跌幅 -->
                            <div class="flex items-baseline justify-between gap-[6px]">
                                <span class="text-[10px] text-[#999] font-mono tabular-nums">{{ s.code }}</span>
                                <span v-if="quotes[s.code]?.changePct != null"
                                      class="text-[10px] font-semibold tabular-nums shrink-0"
                                      :class="quotes[s.code].changePct >= 0 ? 'text-[#dc2626]' : 'text-[#059669]'">
                                    {{ quotes[s.code].changePct >= 0 ? '+' : '' }}{{ quotes[s.code].changePct.toFixed(2) }}%
                                </span>
                            </div>
                        </button>
                    </div>
                </aside>

                <!-- 右侧 K 线（每次切股都重挂载，保证状态干净）-->
                <StockChart v-if="code"
                            :key="code"
                            :code="code"
                            :name="name"
                            @close="closeStockChart"
                            class="flex-1 min-h-0 min-w-0" />
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.20s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

.slide-up-enter-active, .slide-up-leave-active {
    transition: transform 0.30s cubic-bezier(0.22, 1, 0.36, 1);
}
.slide-up-enter-from, .slide-up-leave-to {
    transform: translateY(100%);
}
</style>
