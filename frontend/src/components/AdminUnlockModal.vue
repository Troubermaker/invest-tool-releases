<script setup>
/**
 * 管理员密码解锁弹窗。
 * 通过隐藏快捷键 Ctrl+Shift+A 唤起（在 App.vue 监听）。
 * 解锁后开放：找候选 / 找发车 / 三维启动等复杂指标 + pytdx 数据源。
 *
 * 也支持已是管理员状态下点这个 modal "退出管理员模式"。
 */
import { ref, watch, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useUserRole } from '../composables/useUserRole'

const props = defineProps({
    open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const { isAdmin, unlock, disable } = useUserRole()

const password   = ref('')
const submitting = ref(false)
const error      = ref('')
const inputRef   = ref(null)
let _errTimer = null

const canSubmit = computed(() => password.value.trim().length > 0 && !submitting.value)

function showError(msg) {
    error.value = msg
    if (_errTimer) clearTimeout(_errTimer)
    _errTimer = setTimeout(() => { error.value = '' }, 3000)
}

async function submit() {
    if (!canSubmit.value) return
    submitting.value = true
    error.value = ''
    try {
        const ok = await unlock(password.value)
        if (ok) {
            password.value = ''
            emit('close')
        } else {
            showError('密码错误')
        }
    } finally {
        submitting.value = false
    }
}

async function handleDisable() {
    await disable()
    emit('close')
}

watch(() => props.open, async (v) => {
    if (v) {
        password.value = ''
        error.value = ''
        await nextTick()
        inputRef.value?.focus()
    }
})

function onKeydown(e) {
    if (e.key === 'Escape' && props.open) emit('close')
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
    <Transition name="fade">
        <div v-if="open"
             @click="$emit('close')"
             class="fixed inset-0 bg-black/35 z-[800] flex items-center justify-center px-[24px]">
            <div @click.stop
                 class="w-[400px] bg-white rounded-[10px] shadow-[0_10px_40px_rgba(0,0,0,0.15)] border border-[#eee] p-[24px]">
                <!-- 头部 -->
                <div class="flex items-center gap-[10px] mb-[6px]">
                    <div class="w-[28px] h-[28px] rounded-[6px] bg-gradient-to-br from-[#dc2626] to-[#7c2d12]
                                flex items-center justify-center text-white font-bold text-[12px] shadow-sm">
                        ⚙
                    </div>
                    <h2 class="text-[15px] font-bold text-[#111]">
                        {{ isAdmin ? '管理员模式' : '解锁管理员' }}
                    </h2>
                </div>
                <p class="text-[11px] text-[#888] mb-[16px] leading-relaxed">
                    <template v-if="isAdmin">
                        当前已开启管理员模式，可使用找候选 / 找发车 / 复杂指标 / pytdx 数据源等功能。
                    </template>
                    <template v-else>
                        输入管理员密码以开启高级功能（找候选 / 找发车扫描器、复杂技术指标、pytdx 数据源）。
                    </template>
                </p>

                <!-- 已是管理员：显示退出按钮 -->
                <div v-if="isAdmin" class="flex gap-[8px]">
                    <button @click="handleDisable"
                            class="flex-1 py-[8px] rounded-[6px] text-[13px] font-bold text-white bg-[#dc2626]
                                   hover:bg-[#b91c1c] active:scale-[0.99] transition shadow-sm">
                        退出管理员
                    </button>
                    <button @click="$emit('close')"
                            class="flex-1 py-[8px] rounded-[6px] text-[13px] text-[#666] border border-[#e5e5e5]
                                   hover:bg-[#fafafa] active:scale-[0.99] transition">
                        保持
                    </button>
                </div>

                <!-- 未解锁：密码输入 -->
                <template v-else>
                    <input ref="inputRef"
                           v-model="password"
                           @keydown.enter="submit"
                           type="password"
                           placeholder="管理员密码"
                           autocomplete="off"
                           spellcheck="false"
                           class="w-full px-[12px] py-[8px] text-[13px] font-mono text-[#111] bg-[#fafafa]
                                  border-[1.5px] border-[#e5e5e5] rounded-[6px] outline-none
                                  focus:border-[#dc2626] focus:bg-white transition placeholder:text-[#ccc]">
                    <div class="h-[18px] mt-[6px]">
                        <p v-if="error" class="text-[11px] text-[#dc2626] font-medium">⚠ {{ error }}</p>
                    </div>
                    <div class="flex gap-[8px] mt-[6px]">
                        <button @click="submit"
                                :disabled="!canSubmit"
                                class="flex-1 py-[8px] rounded-[6px] text-[13px] font-bold transition shadow-sm"
                                :class="canSubmit
                                    ? 'bg-[#dc2626] text-white hover:bg-[#b91c1c] active:scale-[0.99]'
                                    : 'bg-[#f5f5f5] text-[#bbb] cursor-not-allowed'">
                            {{ submitting ? '校验中...' : '解锁' }}
                        </button>
                        <button @click="$emit('close')"
                                class="flex-1 py-[8px] rounded-[6px] text-[13px] text-[#666] border border-[#e5e5e5]
                                       hover:bg-[#fafafa] active:scale-[0.99] transition">
                            取消
                        </button>
                    </div>
                    <p class="text-[10px] text-[#bbb] mt-[10px] leading-relaxed">
                        💡 提示：随时可通过 <span class="font-mono text-[#888]">Ctrl + Shift + A</span> 唤起本窗口
                    </p>
                </template>
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.16s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
</style>
