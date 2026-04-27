<script setup>
/**
 * 刷新倒计时小徽章（可点击）。配合 useSmartRefresh 使用。
 *
 * 视觉：圆环进度 + 中央秒数。圆环按 (currentInterval - seconds) / currentInterval 推进。
 * - seconds=null（首次还没拉到）→ 显示一个柔和的脉冲点
 * - seconds=0（马上要刷）→ 临时切到红色加粗
 * - seconds=-1（暂停模式）→ 显示"⏸"
 *
 * 交互：点击圆环 → 弹出菜单选择间隔（10/15/30/60/120s/暂停/立即刷新）
 *
 * 用法：
 *   <RefreshCountdown
 *       :seconds="ladderCountdown"
 *       :current-interval="ladderInterval"
 *       @pick="setLadderInterval"
 *       @refresh-now="refreshLadder"
 *   />
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
    // 距离下次刷新的秒数；null=尚未首次刷新；-1=暂停
    seconds:         { type: Number, default: null },
    // 当前生效的周期（秒），用来算圆环占比 + popover 高亮
    currentInterval: { type: Number, default: 30 },
    // 可选间隔（秒）
    options:         { type: Array, default: () => [3, 5, 10, 15, 30, 60, 120] },
})
const emit = defineEmits(['pick', 'refresh-now'])

const open = ref(false)
const rootEl = ref(null)

const isPaused   = computed(() => props.seconds === -1 || props.currentInterval <= 0)
const isReady    = computed(() => !isPaused.value && props.seconds !== null)
const isImminent = computed(() => isReady.value && props.seconds === 0)

const CIRC = 2 * Math.PI * 7   // r=7 时的周长
const dashOffset = computed(() => {
    if (!isReady.value || props.currentInterval <= 0) return CIRC
    const ratio = Math.max(0, Math.min(1, props.seconds / props.currentInterval))
    return CIRC * ratio
})

function fmtOpt(s) {
    if (s < 60) return s + ' 秒'
    if (s % 60 === 0) return (s / 60) + ' 分钟'
    return s + ' 秒'
}

function pick(s) {
    emit('pick', s)
    open.value = false
}
function refreshNow() {
    emit('refresh-now')
    open.value = false
}

// 点 popover 外面关闭
function onDocClick(e) {
    if (!open.value) return
    if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))
</script>

<template>
    <div ref="rootEl" class="relative inline-flex">
        <button @click.stop="open = !open"
                type="button"
                class="inline-flex items-center gap-[3px] text-[10px] tabular-nums leading-none cursor-pointer hover:text-[#dc2626] transition px-[2px]"
                :class="isImminent ? 'text-[#dc2626] font-bold' : (isPaused ? 'text-[#94a3b8]' : 'text-[#999]')"
                :title="isPaused
                    ? '已暂停（点击设置）'
                    : (seconds == null ? '加载中（点击设置）' : `下次刷新 ${seconds}s / 周期 ${currentInterval}s（点击设置）`)">
            <svg class="w-[14px] h-[14px]" viewBox="0 0 18 18">
                <circle cx="9" cy="9" r="7" fill="none" stroke="#eee" stroke-width="2"/>
                <circle v-if="isReady"
                        cx="9" cy="9" r="7" fill="none"
                        :stroke="isImminent ? '#dc2626' : '#94a3b8'"
                        stroke-width="2" stroke-linecap="round"
                        transform="rotate(-90 9 9)"
                        :stroke-dasharray="CIRC"
                        :stroke-dashoffset="CIRC - dashOffset"/>
                <!-- 暂停：方括号双竖线 -->
                <g v-else-if="isPaused" fill="#94a3b8">
                    <rect x="6.5" y="5.5" width="2" height="7" rx="0.5"/>
                    <rect x="9.5" y="5.5" width="2" height="7" rx="0.5"/>
                </g>
                <!-- 加载脉冲 -->
                <circle v-else cx="9" cy="9" r="2" fill="#cbd5e1">
                    <animate attributeName="r" from="2" to="4" dur="1.2s" repeatCount="indefinite"/>
                    <animate attributeName="opacity" from="1" to="0.2" dur="1.2s" repeatCount="indefinite"/>
                </circle>
            </svg>
            <span v-if="isPaused">暂停</span>
            <span v-else-if="isReady">{{ seconds }}s</span>
        </button>

        <!-- popover：选择间隔 -->
        <div v-if="open"
             class="absolute right-0 top-full mt-[6px] z-[100] bg-white border border-[#e5e5e5] rounded-[6px] shadow-[0_4px_16px_rgba(0,0,0,0.12)] py-[4px] min-w-[148px] text-[#333]"
             @click.stop>
            <div class="px-[12px] py-[5px] text-[10px] text-[#999] tracking-wide border-b border-[#f5f5f5] uppercase">
                刷新间隔
            </div>
            <button v-for="opt in options" :key="opt"
                    @click="pick(opt)"
                    type="button"
                    class="w-full text-left px-[12px] py-[6px] text-[12px] hover:bg-[#fff5f5] transition flex justify-between items-center"
                    :class="opt === currentInterval && !isPaused ? 'text-[#dc2626] font-semibold bg-[#fff5f5]' : ''">
                <span>{{ fmtOpt(opt) }}</span>
                <span v-if="opt === currentInterval && !isPaused" class="text-[#dc2626]">✓</span>
            </button>
            <div class="border-t border-[#f5f5f5] mt-[2px]">
                <button @click="pick(0)"
                        type="button"
                        class="w-full text-left px-[12px] py-[6px] text-[12px] hover:bg-[#f5f5f5] transition flex justify-between items-center"
                        :class="isPaused ? 'text-[#dc2626] font-semibold' : 'text-[#666]'">
                    <span>暂停</span>
                    <span v-if="isPaused" class="text-[#dc2626]">✓</span>
                </button>
                <button @click="refreshNow"
                        type="button"
                        class="w-full text-left px-[12px] py-[6px] text-[12px] text-[#dc2626] hover:bg-[#fff5f5] transition">
                    立即刷新一次
                </button>
            </div>
        </div>
    </div>
</template>
