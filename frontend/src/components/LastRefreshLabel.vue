<script setup>
/**
 * 显示"上次刷新于 X 秒/分钟前"的小标签。
 *
 * - 接 useSmartRefresh 返回的 lastRefreshAt 即可（一个 ms 时间戳 ref）
 * - 自动跟随全局 1s ticker 重算（不需要自己定时）
 * - 0 → "刚刚"；< 60s → "Xs"；< 60min → "X分前"；更久 → "X时前"
 *
 * 用法：
 *   <LastRefreshLabel :timestamp="ladderLastRefreshAt" />
 */
import { computed, ref, onMounted, onUnmounted } from 'vue'

const props = defineProps({
    timestamp: { type: Number, default: 0 },   // ms 时间戳；0 = 还没首次刷新
})

const now = ref(Date.now())
let _ticker = null
onMounted(() => {
    _ticker = setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => {
    if (_ticker) clearInterval(_ticker)
})

const label = computed(() => {
    if (!props.timestamp) return '—'
    const diffMs = now.value - props.timestamp
    if (diffMs < 0) return '刚刚'
    const sec = Math.floor(diffMs / 1000)
    if (sec < 3) return '刚刚'
    if (sec < 60) return `${sec}秒前`
    const min = Math.floor(sec / 60)
    if (min < 60) return `${min}分前`
    const hr = Math.floor(min / 60)
    if (hr < 24) return `${hr}时前`
    const day = Math.floor(hr / 24)
    return `${day}天前`
})

const tooltip = computed(() => {
    if (!props.timestamp) return '尚未刷新'
    const d = new Date(props.timestamp)
    return `上次刷新：${d.toLocaleString()}`
})
</script>

<template>
    <span class="text-[10px] text-[#999] tabular-nums select-none cursor-default" :title="tooltip">
        {{ label }}
    </span>
</template>
