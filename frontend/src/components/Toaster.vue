<script setup>
/**
 * 全局 toast 容器。挂在 App.vue 顶层即可。
 *
 * 视觉：右下角堆叠（新的在下，跟微信、Slack 一致），白底 + 左侧彩色条。
 * 不再附加冗余 icon —— message 自己 carry 语义 emoji（🔔 / ⚠ / ✓）即可，
 * Toaster 只负责"颜色条 + 阴影 + 关闭按钮 + 动画"。
 *
 * 色阶：
 *   - error   → 红条 (#dc2626)，警示
 *   - warn    → 琥珀 (#f59e0b)，提醒（价格警报走这一档）
 *   - info    → 蓝   (#3b82f6)，普通通知
 *   - success → 绿   (#16a34a)，操作成功
 */
import { notifications, dismiss } from '../composables/useNotifications'

const ACCENT = {
    error:   { bar: '#dc2626', text: '#7f1d1d', bg: '#fef2f2' },
    warn:    { bar: '#f59e0b', text: '#78350f', bg: '#fffbeb' },
    info:    { bar: '#3b82f6', text: '#1e3a8a', bg: '#eff6ff' },
    success: { bar: '#16a34a', text: '#14532d', bg: '#f0fdf4' },
}
const DEFAULT_ACCENT = { bar: '#94a3b8', text: '#334155', bg: '#fff' }

function styleFor(kind) {
    const a = ACCENT[kind] || DEFAULT_ACCENT
    return {
        borderLeft: `4px solid ${a.bar}`,
        background: a.bg,
        color: a.text,
    }
}
</script>

<template>
    <TransitionGroup name="toast" tag="div"
                     class="fixed bottom-[42px] right-[16px] z-[400]
                            flex flex-col gap-[8px]
                            max-w-[380px] pointer-events-none">
        <div v-for="n in notifications" :key="n.id"
             :style="styleFor(n.kind)"
             class="rounded-[6px] shadow-[0_6px_20px_rgba(0,0,0,0.12),_0_2px_4px_rgba(0,0,0,0.06)]
                    pl-[14px] pr-[12px] py-[10px]
                    text-[13px] leading-[1.5]
                    flex items-start gap-[10px]
                    pointer-events-auto
                    border border-r border-y border-[#e5e7eb]/60">
            <span class="flex-1 break-words font-medium">{{ n.msg }}</span>
            <button @click="dismiss(n.id)"
                    class="opacity-40 hover:opacity-100 transition shrink-0
                           text-[14px] leading-[1] mt-[1px]
                           hover:bg-black/5 rounded w-[18px] h-[18px] flex items-center justify-center"
                    title="关闭">
                ✕
            </button>
        </div>
    </TransitionGroup>
</template>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all 0.30s cubic-bezier(0.22, 1, 0.36, 1); }
.toast-enter-from { opacity: 0; transform: translateX(40px) scale(0.95); }
.toast-leave-to   { opacity: 0; transform: translateX(40px) scale(0.95); }
.toast-leave-active { position: absolute; right: 0; }
</style>
