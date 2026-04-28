<script setup>
/**
 * 底部滑出的 K 线 drawer。挂在 App.vue 一次，全局可见。
 * 由 useStockChart.openStockChart(code, name) 触发显示。
 */
import { computed } from 'vue'
import StockChart from './StockChart.vue'
import { stockChartState, closeStockChart } from '../composables/useStockChart'

const visible = computed(() => stockChartState.value.visible)
const code    = computed(() => stockChartState.value.code)
const name    = computed(() => stockChartState.value.name)

// ESC 关闭
function onKeydown(e) {
    if (e.key === 'Escape' && visible.value) closeStockChart()
}
import { onMounted, onUnmounted } from 'vue'
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
             style="height: 78vh;">
            <!-- 顶部抓手条（视觉） -->
            <div class="h-[6px] flex items-center justify-center shrink-0">
                <div class="w-[40px] h-[3px] rounded-full bg-[#cbd5e1]"></div>
            </div>

            <!-- 真正的 chart（独立组件，每次开新股都重挂载）-->
            <StockChart v-if="code"
                        :key="code"
                        :code="code"
                        :name="name"
                        @close="closeStockChart"
                        class="flex-1 min-h-0" />
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
