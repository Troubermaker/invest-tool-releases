<script setup>
/**
 * 全局 toast 容器。挂在 App.vue 顶层即可。
 *
 * 视觉：右下角堆叠，新的在上。每条独立 ✕ 关闭。
 * - error → 红
 * - warn  → 橙
 * - info  → 蓝
 * - success → 绿
 */
import { notifications, dismiss } from '../composables/useNotifications'

function bgClass(kind) {
    return {
        error:   'bg-[#fef2f2] border-[#fecaca] text-[#991b1b]',
        warn:    'bg-[#fffbeb] border-[#fde68a] text-[#92400e]',
        info:    'bg-[#eff6ff] border-[#bfdbfe] text-[#1e40af]',
        success: 'bg-[#f0fdf4] border-[#bbf7d0] text-[#166534]',
    }[kind] || 'bg-white border-[#e5e5e5] text-[#333]'
}

function icon(kind) {
    return { error: '⚠', warn: '⚠', info: 'ⓘ', success: '✓' }[kind] || ''
}
</script>

<template>
    <TransitionGroup name="toast" tag="div"
                     class="fixed bottom-[42px] right-[16px] z-[400]
                            flex flex-col-reverse gap-[8px]
                            max-w-[360px] pointer-events-none">
        <div v-for="n in notifications" :key="n.id"
             class="border rounded-[6px] shadow-[0_4px_16px_rgba(0,0,0,0.10)] px-[12px] py-[8px] text-[12px] leading-relaxed flex items-start gap-[8px] pointer-events-auto"
             :class="bgClass(n.kind)">
            <span class="font-bold text-[14px] leading-tight">{{ icon(n.kind) }}</span>
            <span class="flex-1 break-words">{{ n.msg }}</span>
            <button @click="dismiss(n.id)"
                    class="opacity-50 hover:opacity-100 transition shrink-0 text-[14px] leading-tight"
                    title="关闭">
                ✕
            </button>
        </div>
    </TransitionGroup>
</template>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all 0.25s ease; }
.toast-enter-from { opacity: 0; transform: translateX(20px); }
.toast-leave-to   { opacity: 0; transform: translateX(20px); }
</style>
