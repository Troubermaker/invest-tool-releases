<script setup>
/**
 * 激活门禁。未激活前展示这个全屏页面，激活成功 emit('activated')。
 *
 * 体验细节：
 *   - 输入框自动按 4-4-4-4 格式插入横线（用户体验）
 *   - 提交按 Enter 触发
 *   - 错误信息红字提示，3 秒后自动消失
 *   - 失败超过 3 次给出"是否联系作者"软提示（不锁死，无服务器无法做硬限制）
 */
import { ref, computed, onMounted, nextTick } from 'vue'
import { api } from '../api/client'

const emit = defineEmits(['activated'])

const codeInput = ref('')
const inputRef  = ref(null)
const submitting = ref(false)
const error      = ref('')
const failCount  = ref(0)
let _errTimer = null

const formattedCode = computed({
    get: () => codeInput.value,
    set: (v) => {
        // 去掉所有非字母数字，自动按 4 字符插横线
        const raw = String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 16)
        const parts = []
        for (let i = 0; i < raw.length; i += 4) parts.push(raw.slice(i, i + 4))
        codeInput.value = parts.join('-')
    }
})

const canSubmit = computed(() => {
    const raw = codeInput.value.replace(/-/g, '')
    return raw.length === 16 && !submitting.value
})

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
        const res = await api.activateLicense(codeInput.value)
        if (res.ok && res.data === true) {
            emit('activated')
            return
        }
        failCount.value += 1
        showError('激活码无效，请检查后重试')
    } catch (e) {
        showError('激活失败：' + (e?.message || e))
    } finally {
        submitting.value = false
    }
}

onMounted(async () => {
    await nextTick()
    inputRef.value?.focus()
})
</script>

<template>
    <div class="fixed inset-0 z-[1000] bg-gradient-to-br from-[#fafafa] to-[#f0f0f0] flex items-center justify-center px-[24px]">
        <div class="w-[420px] bg-white rounded-[12px] shadow-[0_8px_40px_rgba(0,0,0,0.08)] border border-[#eee] p-[32px]">
            <!-- Logo / 标题 -->
            <div class="flex items-center gap-[10px] mb-[6px]">
                <div class="w-[32px] h-[32px] rounded-[8px] bg-gradient-to-br from-[#dc2626] to-[#991b1b] flex items-center justify-center text-white font-bold text-[14px] shadow-sm">
                    投
                </div>
                <h1 class="text-[18px] font-bold text-[#111] tracking-wide">A 股投资工具</h1>
            </div>
            <p class="text-[12px] text-[#888] mb-[24px] leading-relaxed">
                使用前请输入激活码。激活后所有功能解锁；未激活时无法访问任何数据接口。
            </p>

            <!-- 输入框 -->
            <label class="block text-[11px] font-semibold text-[#666] tracking-wide uppercase mb-[8px]">
                激活码
            </label>
            <input ref="inputRef"
                   v-model="formattedCode"
                   @keydown.enter="submit"
                   type="text"
                   placeholder="XXXX-XXXX-XXXX-XXXX"
                   maxlength="19"
                   autocomplete="off"
                   spellcheck="false"
                   class="w-full px-[14px] py-[10px] text-[14px] font-mono tracking-[0.08em] text-[#111] bg-[#fafafa] border-[1.5px] border-[#e5e5e5] rounded-[6px] outline-none focus:border-[#dc2626] focus:bg-white transition placeholder:text-[#ccc] tabular-nums uppercase">

            <!-- 错误提示 -->
            <div class="h-[20px] mt-[8px]">
                <p v-if="error" class="text-[12px] text-[#dc2626] font-medium animate-pulse">
                    ⚠ {{ error }}
                </p>
            </div>

            <!-- 失败提示（≥3 次给出帮助）-->
            <p v-if="failCount >= 3" class="text-[11px] text-[#888] mt-[2px] mb-[12px] leading-relaxed">
                多次激活失败？请确认激活码完整复制（无遗漏字符），或联系作者获取新的激活码。
            </p>

            <!-- 激活按钮 -->
            <button @click="submit"
                    :disabled="!canSubmit"
                    class="w-full mt-[12px] py-[10px] rounded-[6px] text-[14px] font-bold transition shadow-sm"
                    :class="canSubmit
                        ? 'bg-[#dc2626] text-white hover:bg-[#b91c1c] active:scale-[0.99]'
                        : 'bg-[#f5f5f5] text-[#bbb] cursor-not-allowed'">
                {{ submitting ? '激活中...' : '激活' }}
            </button>

            <!-- Footer -->
            <div class="mt-[20px] pt-[16px] border-t border-[#f0f0f0] text-[11px] text-[#aaa] leading-relaxed">
                <p>· 激活码请联系作者获取（仅限本机使用）</p>
                <p>· 激活成功后无需重复输入</p>
            </div>
        </div>
    </div>
</template>
