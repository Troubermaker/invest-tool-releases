<script setup>
/**
 * 自选股批量导入 modal — 通达信/同花顺式的"无脑文本导入"。
 *
 * 粘贴/拖拽 .txt 文件 → 正则提取 6 位代码 + 反查名字 → 列表预览 → 选目标分组 → 一键导入。
 * 已存在的 code 静默跳过，不报错。
 */
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'
import { pushSuccess, pushError, pushWarn } from '../composables/useNotifications'

const props = defineProps({
    open:    { type: Boolean, default: false },
    groups:  { type: Array,   default: () => [] },     // 现有分组列表 [{id, name, ...}]
    defaultGroupId: { type: Number, default: null },   // 当前选中的分组 id（默认目标）
})
const emit = defineEmits(['close', 'imported'])

// ---------------- 文本输入 ----------------
const inputText = ref('')
const parsing = ref(false)
const parsedStocks = ref([])    // [{code, name, source, _selected}]
const textareaRef = ref(null)

async function parseText() {
    const text = inputText.value.trim()
    if (!text) {
        pushWarn('请先粘贴或输入要识别的文本')
        return
    }
    parsing.value = true
    try {
        const res = await api.importParseText(text)
        if (res.ok && Array.isArray(res.data)) {
            parsedStocks.value = res.data.map(s => ({ ...s, _selected: true }))
            if (parsedStocks.value.length === 0) {
                pushWarn('没识别到任何 A 股代码')
            }
        } else {
            pushError(res.error || '解析失败')
        }
    } finally {
        parsing.value = false
    }
}

// 拖拽 .txt 文件到文本框
async function handleTextFileDrop(e) {
    e.preventDefault()
    const file = e.dataTransfer?.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.txt') && !file.type.startsWith('text/')) {
        pushWarn('只支持 .txt 文本文件')
        return
    }
    const text = await file.text()
    inputText.value = text
    await parseText()
}

// ---------------- 选择目标分组 ----------------
const targetGroupId = ref(null)
watch(() => props.open, (v) => {
    if (v) {
        targetGroupId.value = props.defaultGroupId ?? props.groups?.[0]?.id ?? null
        nextTick(() => textareaRef.value?.focus())
    } else {
        // 关闭时清状态
        inputText.value = ''
        parsedStocks.value = []
    }
})

// ---------------- 选中数量 / 全选 ----------------
const selectedCount = computed(() => parsedStocks.value.filter(s => s._selected).length)
const allSelected = computed(() =>
    parsedStocks.value.length > 0 && parsedStocks.value.every(s => s._selected)
)
function toggleAll() {
    const next = !allSelected.value
    for (const s of parsedStocks.value) s._selected = next
}

// ---------------- 执行导入 ----------------
const importing = ref(false)
async function doImport() {
    if (!targetGroupId.value) {
        pushWarn('请选择目标分组')
        return
    }
    const picked = parsedStocks.value.filter(s => s._selected).map(s => ({
        code: s.code, name: s.name,
    }))
    if (!picked.length) {
        pushWarn('请勾选要导入的股票')
        return
    }
    importing.value = true
    try {
        const res = await api.importBatchAdd(targetGroupId.value, picked)
        if (res.ok && res.data) {
            const { added, skipped_existing, failed } = res.data
            const parts = []
            if (added > 0) parts.push(`新增 ${added}`)
            if (skipped_existing > 0) parts.push(`跳过 ${skipped_existing}（已存在）`)
            if (failed > 0) parts.push(`失败 ${failed}`)
            pushSuccess('导入完成：' + parts.join('，'))
            emit('imported', res.data)
            emit('close')
        } else {
            pushError(res.error || '导入失败')
        }
    } finally {
        importing.value = false
    }
}

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
             class="fixed inset-0 bg-black/35 z-[450] flex items-center justify-center px-[24px]">
            <div @click.stop
                 class="w-[680px] max-w-[92vw] max-h-[88vh] bg-white rounded-[10px]
                        shadow-[0_10px_40px_rgba(0,0,0,0.15)] border border-[#eee]
                        flex flex-col overflow-hidden">
                <!-- Header -->
                <div class="px-[16px] py-[12px] border-b border-[#f0f0f0] flex items-center gap-[10px] shrink-0">
                    <span class="text-[14px] font-bold text-[#111]">📥 批量导入自选</span>
                    <span class="text-[11px] text-[#94a3b8]">从文本自动识别 A 股代码</span>
                    <button @click="$emit('close')"
                            class="ml-auto text-[14px] text-[#888] hover:text-[#dc2626] w-[22px] h-[22px] rounded flex items-center justify-center">
                        ✕
                    </button>
                </div>

                <!-- Body -->
                <div class="flex-1 overflow-y-auto custom-scrollbar p-[16px]">
                    <div class="text-[11px] text-[#888] mb-[6px]">
                        粘贴任意含股票代码的文本（聊天记录、券商列表、新闻片段都行），
                        或直接拖拽 .txt 文件到下方文本框
                    </div>
                    <textarea ref="textareaRef"
                              v-model="inputText"
                              @drop="handleTextFileDrop"
                              @dragover.prevent
                              spellcheck="false"
                              rows="6"
                              placeholder="例如：今天关注 贵州茅台600519、宁德时代(300750)、海康威视002415..."
                              class="w-full px-[10px] py-[8px] text-[12px] font-mono text-[#111]
                                     bg-[#fafafa] border-[1.5px] border-[#e5e5e5] rounded-[6px]
                                     outline-none focus:border-[#dc2626] focus:bg-white transition
                                     placeholder:text-[#bbb] resize-y"></textarea>
                    <button @click="parseText"
                            :disabled="parsing"
                            class="mt-[8px] px-[14px] py-[5px] rounded-[5px] text-[12px] font-bold
                                   bg-[#dc2626] text-white hover:bg-[#b91c1c]
                                   disabled:bg-[#cbd5e1] disabled:cursor-not-allowed transition">
                        {{ parsing ? '识别中...' : '识别股票' }}
                    </button>

                    <!-- 解析结果列表 -->
                    <div v-if="parsedStocks.length"
                         class="mt-[14px] border-t border-[#f0f0f0] pt-[10px]">
                        <div class="flex items-center gap-[8px] mb-[6px]">
                            <label class="flex items-center gap-[4px] text-[11px] text-[#475569] cursor-pointer select-none">
                                <input type="checkbox"
                                       :checked="allSelected"
                                       @change="toggleAll"
                                       class="w-[12px] h-[12px] accent-[#dc2626]">
                                <span>全选 ({{ selectedCount }} / {{ parsedStocks.length }})</span>
                            </label>
                            <div class="ml-auto flex items-center gap-[4px] text-[11px] text-[#475569]">
                                <span>导入到分组：</span>
                                <select v-model="targetGroupId"
                                        class="text-[11px] px-[6px] py-[2px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                                    <option v-for="g in groups" :key="g.id" :value="g.id">
                                        {{ g.name }} ({{ g.count || 0 }})
                                    </option>
                                </select>
                            </div>
                        </div>
                        <div class="border border-[#f0f0f0] rounded-[5px] divide-y divide-[#f5f5f5] max-h-[260px] overflow-y-auto">
                            <label v-for="(s, idx) in parsedStocks" :key="idx"
                                   class="flex items-center gap-[8px] px-[10px] py-[6px] cursor-pointer hover:bg-[#fafafa]">
                                <input type="checkbox" v-model="s._selected" class="w-[12px] h-[12px] accent-[#dc2626]">
                                <span class="text-[11px] text-[#999] font-mono tabular-nums w-[60px]">{{ s.code }}</span>
                                <span class="text-[12px] text-[#111] flex-1 truncate">{{ s.name || '—' }}</span>
                                <span class="text-[10px] px-[5px] py-[1px] rounded-[3px]"
                                      :class="s.source === 'name'
                                          ? 'bg-[#fef3c7] text-[#92400e]'
                                          : 'bg-[#dbeafe] text-[#1d4ed8]'">
                                    {{ s.source === 'name' ? '名字匹配' : '代码匹配' }}
                                </span>
                            </label>
                        </div>
                    </div>
                </div>

                <!-- Footer -->
                <div class="px-[16px] py-[10px] border-t border-[#f0f0f0] bg-[#fafafa] flex items-center gap-[8px] shrink-0">
                    <span v-if="parsedStocks.length" class="text-[11px] text-[#666]">
                        将导入 <span class="font-bold text-[#dc2626] tabular-nums">{{ selectedCount }}</span> 只到
                        「{{ groups.find(g => g.id === targetGroupId)?.name || '?' }}」
                    </span>
                    <button @click="$emit('close')"
                            class="ml-auto px-[14px] py-[6px] rounded-[5px] text-[12px] text-[#666]
                                   border border-[#e5e5e5] hover:bg-white transition">
                        取消
                    </button>
                    <button @click="doImport"
                            :disabled="importing || !selectedCount || !targetGroupId"
                            class="px-[16px] py-[6px] rounded-[5px] text-[12px] font-bold
                                   bg-[#dc2626] text-white hover:bg-[#b91c1c] shadow-sm
                                   disabled:bg-[#cbd5e1] disabled:cursor-not-allowed transition">
                        {{ importing ? '导入中...' : '确认导入' }}
                    </button>
                </div>
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 3px; }
</style>
