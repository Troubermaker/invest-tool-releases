<script setup>
/**
 * 加交易日志 modal —— 从候选池 / 选股扫描器 一键加日志。
 *
 * 行为：
 *   1. 自动 prefill：code/name/star_level/signal_source/signal_metadata
 *   2. 用户填：entry_price（默认当前价 / break_level）+ position_pct（默认 suggest_position 返回）
 *   3. 自动算：target_price = entry × 1.10  / stop_loss = s1_lower × 0.97（或自定义）
 *   4. 提交 → api.addTradeJournal → 关闭 modal + toast
 *
 * Props:
 *   open: Boolean              控制显示
 *   prefill: Object            { code, name, signalSource, starLevel, suggestedEntry, breakLevel, s1Lower, signalMetadata }
 * Emits:
 *   close, saved
 */
import { ref, computed, watch } from 'vue'
import { api } from '../api/client'
import { pushSuccess, pushError } from '../composables/useNotifications'

const props = defineProps({
    open: { type: Boolean, default: false },
    prefill: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'saved'])

// ---- 表单状态 ----
const entryPrice = ref('')
const positionPct = ref('')   // 仓位 % of 总资金
const targetPrice = ref('')   // 默认 entry × 1.10
const stopLoss = ref('')      // 默认 s1Lower × 0.97
const notes = ref('')
const saving = ref(false)
const positionHint = ref('')  // suggest_position 返回的建议
const totalOpenCount = ref(0) // 当前持仓数（用于 suggest_position）

// 监听 prefill 变化（modal 打开时）→ 重新填充
watch(() => props.open, async (isOpen) => {
    if (!isOpen) return
    // 默认值
    entryPrice.value  = String(props.prefill.suggestedEntry ?? props.prefill.breakLevel ?? '')
    targetPrice.value = ''
    stopLoss.value    = String(((props.prefill.s1Lower ?? 0) * 0.97 || '').toFixed(2))
    notes.value       = ''
    positionHint.value = '加载中...'

    // 当前持仓数（用于 suggest）+ suggest_position
    try {
        const tradesRes = await api.listTradeJournal('open')
        const openTrades = (tradesRes?.ok && Array.isArray(tradesRes.data)) ? tradesRes.data : []
        totalOpenCount.value = openTrades.length

        const sectorOpenPct = 0    // TODO: 后续接板块累计仓位
        const res = await api.suggestTradePosition(props.prefill.starLevel ?? 0, openTrades.length, sectorOpenPct)
        if (res?.ok && res.data) {
            const d = res.data
            if (d.suggested_pct != null) {
                positionPct.value = String(d.suggested_pct)
                positionHint.value = d.reason || ''
            } else {
                positionHint.value = d.reason || '系统不建议买入此星级'
                positionPct.value = ''
            }
        }
    } catch (e) {
        positionHint.value = '仓位建议接口异常，请手动填写'
    }
})

// 自动算 target_price (entry × 1.10) 当 entry 变化时
watch(entryPrice, (v) => {
    const f = parseFloat(v)
    if (!targetPrice.value && f > 0) {
        targetPrice.value = (f * 1.10).toFixed(2)
    }
})

const canSubmit = computed(() => {
    return !saving.value &&
           parseFloat(entryPrice.value) > 0 &&
           parseFloat(positionPct.value) > 0
})

async function submit() {
    if (!canSubmit.value) return
    saving.value = true
    try {
        const payload = {
            code:            props.prefill.code,
            name:            props.prefill.name,
            signal_source:   props.prefill.signalSource || 'manual',
            star_level:      props.prefill.starLevel ?? 0,
            signal_metadata: props.prefill.signalMetadata || {},
            entry_price:     parseFloat(entryPrice.value),
            position_pct:    parseFloat(positionPct.value),
            target_price:    parseFloat(targetPrice.value) || null,
            stop_loss:       parseFloat(stopLoss.value) || null,
            notes:           notes.value || '',
        }
        const res = await api.addTradeJournal(payload)
        if (res?.ok) {
            pushSuccess(`已记入交易日志：${props.prefill.name} (${props.prefill.code})`)
            emit('saved', res.data)
            emit('close')
        } else {
            pushError(`加日志失败: ${res?.error || '未知'}`)
        }
    } catch (e) {
        pushError(`加日志失败: ${e}`)
    } finally {
        saving.value = false
    }
}

function onCancel() {
    if (saving.value) return
    emit('close')
}

const starLabel = computed(() => {
    const lv = props.prefill.starLevel ?? 0
    if (lv === 4) return '⭐⭐⭐⭐'
    if (lv === 3) return '⭐⭐⭐'
    if (lv === 2) return '⭐⭐'
    if (lv === 1) return '⭐'
    return '—'
})
</script>

<template>
    <Teleport to="body">
        <div v-if="open"
             class="fixed inset-0 z-[200] flex items-center justify-center bg-black/30"
             @click.self="onCancel">
            <div class="bg-white rounded-lg shadow-2xl w-[460px] max-w-[92vw] overflow-hidden">
                <!-- 标题栏 -->
                <div class="px-[18px] py-[12px] border-b border-[#e5e7eb] flex items-center justify-between bg-gradient-to-r from-[#fef2f2] to-white">
                    <div class="flex items-center gap-[10px]">
                        <span class="text-[15px] font-bold text-[#111]">📝 加交易日志</span>
                        <span class="text-[12px] text-[#dc2626] font-bold">{{ starLabel }}</span>
                    </div>
                    <button @click="onCancel" :disabled="saving"
                            class="text-[#999] hover:text-[#dc2626] text-[18px] leading-none px-[4px]"
                            :class="saving ? 'opacity-30 cursor-not-allowed' : ''">×</button>
                </div>

                <!-- 信号摘要 -->
                <div class="px-[18px] py-[10px] bg-[#fafafa] border-b border-[#f0f0f0] text-[12px]">
                    <div class="flex items-center gap-[10px]">
                        <span class="font-bold text-[14px]">{{ prefill.name || '—' }}</span>
                        <span class="text-[#999] font-mono">{{ prefill.code }}</span>
                        <span class="ml-auto text-[10px] text-[#666]">{{ prefill.signalSource }}</span>
                    </div>
                </div>

                <!-- 表单 -->
                <div class="px-[18px] py-[14px] space-y-[12px]">
                    <!-- 入场价 -->
                    <div>
                        <label class="text-[11px] text-[#666] block mb-[3px]">入场价 *</label>
                        <input v-model="entryPrice" type="number" step="0.01"
                               class="w-full px-[10px] py-[6px] border border-[#e5e7eb] rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626]"
                               placeholder="例：12.34">
                    </div>

                    <!-- 仓位 % -->
                    <div>
                        <label class="text-[11px] text-[#666] flex items-center justify-between mb-[3px]">
                            <span>仓位 % *</span>
                            <span v-if="positionHint" class="text-[10px] text-[#94a3b8] italic">{{ positionHint }}</span>
                        </label>
                        <input v-model="positionPct" type="number" step="0.5"
                               class="w-full px-[10px] py-[6px] border border-[#e5e7eb] rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626]"
                               placeholder="例：15">
                    </div>

                    <!-- 止盈 / 止损 -->
                    <div class="grid grid-cols-2 gap-[10px]">
                        <div>
                            <label class="text-[11px] text-[#666] block mb-[3px]">目标止盈（默认 +10%）</label>
                            <input v-model="targetPrice" type="number" step="0.01"
                                   class="w-full px-[10px] py-[6px] border border-[#e5e7eb] rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626]"
                                   placeholder="留空 = 不设">
                        </div>
                        <div>
                            <label class="text-[11px] text-[#666] block mb-[3px]">止损（s1Lower × 0.97）</label>
                            <input v-model="stopLoss" type="number" step="0.01"
                                   class="w-full px-[10px] py-[6px] border border-[#e5e7eb] rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626]"
                                   placeholder="留空 = 不设">
                        </div>
                    </div>

                    <!-- 备注 -->
                    <div>
                        <label class="text-[11px] text-[#666] block mb-[3px]">备注（可选）</label>
                        <textarea v-model="notes" rows="2"
                                  class="w-full px-[10px] py-[6px] border border-[#e5e7eb] rounded text-[12px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626] resize-none"
                                  placeholder="例：盘中突破后买入，主力放量"></textarea>
                    </div>
                </div>

                <!-- 底部按钮 -->
                <div class="px-[18px] py-[12px] bg-[#fafafa] border-t border-[#e5e7eb] flex items-center justify-end gap-[8px]">
                    <button @click="onCancel" :disabled="saving"
                            class="text-[12px] px-[12px] py-[6px] border border-[#e5e7eb] rounded hover:bg-white text-[#666]"
                            :class="saving ? 'opacity-30 cursor-not-allowed' : ''">
                        取消
                    </button>
                    <button @click="submit" :disabled="!canSubmit"
                            class="text-[12px] px-[14px] py-[6px] rounded font-semibold bg-[#dc2626] text-white hover:bg-[#b91c1c]"
                            :class="!canSubmit ? 'opacity-40 cursor-not-allowed' : ''">
                        {{ saving ? '保存中...' : '✓ 加入日志' }}
                    </button>
                </div>
            </div>
        </div>
    </Teleport>
</template>
