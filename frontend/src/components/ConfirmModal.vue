<script setup>
/**
 * 全局确认框 —— 由 useConfirm.confirmDialog() 触发，App.vue 挂载一次即可。
 *
 * 视觉风格对齐 AddToWatchlistModal / AdminUnlockModal：白色圆角卡片、轻阴影、
 * 标题 + 消息 + 取消/确认按钮。danger 模式下确认按钮变红色（用于不可撤销操作）。
 *
 * 键盘：Esc = 取消，Enter = 确认（跟系统原生 confirm 习惯一致）。
 */
import { onMounted, onUnmounted } from 'vue'
import { useConfirm } from '../composables/useConfirm'

const { state, accept, reject } = useConfirm()

function onKeydown(e) {
    if (!state.value.visible) return
    if (e.key === 'Escape') { e.preventDefault(); reject() }
    else if (e.key === 'Enter') { e.preventDefault(); accept() }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
    <Transition name="fade">
        <!-- z-[500] 比所有其他 modal 都高，避免被覆盖 -->
        <div v-if="state.visible"
             @click.self="reject"
             class="fixed inset-0 bg-black/30 z-[500] flex items-center justify-center">
            <div @click.stop
                 class="bg-white rounded-[8px] shadow-[0_10px_40px_rgba(0,0,0,0.18)]
                        min-w-[320px] max-w-[440px] overflow-hidden flex flex-col">
                <div class="px-[18px] pt-[16px] pb-[10px]">
                    <div class="text-[14px] font-bold text-[#111] mb-[10px]">{{ state.title }}</div>
                    <div class="text-[12px] text-[#555] leading-relaxed whitespace-pre-line">{{ state.message }}</div>
                </div>
                <div class="px-[16px] pb-[14px] pt-[6px] flex gap-[8px] justify-end">
                    <button @click="reject"
                            class="px-[16px] py-[6px] rounded-[5px] text-[12px] text-[#555] border border-[#e5e5e5]
                                   hover:bg-[#fafafa] active:scale-[0.98] transition">
                        {{ state.cancelText }}
                    </button>
                    <button @click="accept"
                            class="px-[16px] py-[6px] rounded-[5px] text-[12px] font-bold text-white
                                   active:scale-[0.98] transition shadow-sm"
                            :class="state.danger
                                ? 'bg-[#dc2626] hover:bg-[#b91c1c]'
                                : 'bg-[#2563eb] hover:bg-[#1d4ed8]'">
                        {{ state.confirmText }}
                    </button>
                </div>
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
</style>
