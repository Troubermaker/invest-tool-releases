<script setup>
/**
 * 添加到自选 modal：
 *   1. 打开时拉取所有分组
 *   2. 用户点某个分组 → 调 addWatchlistStock(groupId, code, name, price)
 *   3. 成功 toast，关闭 modal
 *
 * 由 useAddToWatchlist.openAddToWatchlist(code, name, price) 触发。
 */
import { ref, watch, computed } from 'vue'
import { api } from '../api/client'
import { addToWatchlistState, closeAddToWatchlist } from '../composables/useAddToWatchlist'
import { pushSuccess, pushError } from '../composables/useNotifications'

const visible = computed(() => addToWatchlistState.value.visible)
const mode    = computed(() => addToWatchlistState.value.mode || 'single')
const code    = computed(() => addToWatchlistState.value.code)
const name    = computed(() => addToWatchlistState.value.name)
const price   = computed(() => addToWatchlistState.value.price)
const stocks  = computed(() => addToWatchlistState.value.stocks || [])
const isBatch = computed(() => mode.value === 'batch')

const groups = ref([])
const loading = ref(false)
const adding = ref(null)   // 正在添加的 groupId

watch(visible, async (v) => {
    if (v) {
        loading.value = true
        try {
            const res = await api.getWatchlistGroups()
            groups.value = res.ok ? (res.data || []) : []
        } finally {
            loading.value = false
        }
    }
})

async function pickGroup(g) {
    if (adding.value) return
    adding.value = g.id
    try {
        if (isBatch.value) {
            // 批量：调用 importBatchAdd（自动跳过已存在的 code）
            const payload = stocks.value.map(s => ({ code: s.code, name: s.name }))
            const res = await api.importBatchAdd(g.id, payload)
            if (res.ok) {
                const d = res.data || {}
                const added = d.added ?? 0
                const skipped = d.skipped_existing ?? 0
                const failed = d.failed ?? 0
                let msg = `已添加 ${added} 只到「${g.name}」`
                if (skipped) msg += `，${skipped} 只已存在跳过`
                if (failed)  msg += `，${failed} 只失败`
                pushSuccess(msg)
                closeAddToWatchlist()
            } else {
                pushError(res.error || '批量添加失败')
            }
        } else {
            const res = await api.addWatchlistStock(g.id, code.value, name.value, price.value, '')
            if (res.ok) {
                pushSuccess(`已添加 ${name.value || code.value} 到「${g.name}」`)
                closeAddToWatchlist()
            } else {
                pushError(res.error || '添加失败')
            }
        }
    } catch (e) {
        pushError('添加异常：' + e.message)
    } finally {
        adding.value = null
    }
}

function onKeydown(e) {
    if (e.key === 'Escape' && visible.value) closeAddToWatchlist()
}
import { onMounted, onUnmounted } from 'vue'
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
    <Transition name="fade">
        <div v-if="visible"
             @click="closeAddToWatchlist"
             class="fixed inset-0 bg-black/30 z-[400] flex items-center justify-center">
            <div @click.stop
                 class="bg-white rounded-[8px] shadow-xl min-w-[320px] max-w-[420px]
                        max-h-[70vh] overflow-hidden flex flex-col">
                <!-- Header -->
                <div class="px-[16px] py-[10px] border-b border-[#f0f0f0] flex items-baseline gap-[8px]">
                    <template v-if="isBatch">
                        <span class="text-[14px] font-bold text-[#111]">批量添加到自选</span>
                        <span class="text-[12px] text-[#dc2626] font-bold tabular-nums">{{ stocks.length }} 只</span>
                        <span class="text-[11px] text-[#888] truncate">
                            {{ stocks.slice(0, 3).map(s => s.name || s.code).join('、') }}{{ stocks.length > 3 ? ' 等' : '' }}
                        </span>
                    </template>
                    <template v-else>
                        <span class="text-[14px] font-bold text-[#111]">添加到自选</span>
                        <span class="text-[11px] text-[#94a3b8] font-mono tabular-nums">{{ code }}</span>
                        <span class="text-[12px] text-[#666] truncate">{{ name }}</span>
                    </template>
                    <button @click="closeAddToWatchlist"
                            class="ml-auto text-[14px] text-[#888] hover:text-[#dc2626] w-[22px] h-[22px] rounded transition flex items-center justify-center">
                        ✕
                    </button>
                </div>

                <!-- Body -->
                <div class="px-[12px] py-[10px] overflow-y-auto flex-1">
                    <div v-if="loading" class="text-center py-[24px] text-[12px] text-[#999]">
                        加载分组...
                    </div>
                    <div v-else-if="!groups.length" class="text-center py-[24px] text-[12px] text-[#999]">
                        还没有分组<br>
                        <span class="text-[10px] text-[#bbb]">请先在自选页创建分组</span>
                    </div>
                    <div v-else>
                        <div class="text-[10px] text-[#888] mb-[6px]">选择要添加到的分组：</div>
                        <div class="flex flex-col gap-[4px]">
                            <button v-for="g in groups" :key="g.id"
                                    @click="pickGroup(g)"
                                    :disabled="adding != null"
                                    class="text-left px-[12px] py-[8px] rounded-[5px] border border-[#e5e7eb]
                                           hover:bg-[#fff5f5] hover:border-[#fecaca] transition
                                           text-[13px] text-[#111] flex items-center gap-[8px]
                                           disabled:opacity-50 disabled:cursor-wait">
                                <span class="font-semibold flex-1">{{ g.name }}</span>
                                <span v-if="g.count != null" class="text-[10px] text-[#999] tabular-nums">
                                    {{ g.count }} 只
                                </span>
                                <span v-if="adding === g.id" class="text-[10px] text-[#dc2626]">添加中...</span>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.18s ease; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }
</style>
