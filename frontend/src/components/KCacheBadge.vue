<script setup>
/**
 * K 线缓存状态 + 下载按钮 —— 小尺寸 inline 徽章，挂在策略说明卡片的右侧空白处。
 *
 * 因为 cacheStatus / downloader 等状态散在 Quant.vue 的 setup 里，没单独抽 composable，
 * 这里通过 props 接收，调用方传透。父组件触发下载逻辑则通过 @download emit。
 */
defineProps({
    cacheStatus:         { type: Object,  default: null },
    cacheStatusChecking: { type: Boolean, default: false },
    cacheStatusHint:     { type: String,  default: '' },
    shouldPromptDownload:{ type: Boolean, default: false },
    downloaderPhase:     { type: String,  default: 'idle' },
    downloadedCount:     { type: Number,  default: 0 },
    failedCount:         { type: Number,  default: 0 },
    totalToDownload:     { type: Number,  default: 0 },
    scanning:            { type: Boolean, default: false },
})
const emit = defineEmits(['download', 'cancel'])
</script>

<template>
    <div class="shrink-0 flex items-center gap-[6px] text-[10px]">
        <span class="text-[#94a3b8]">📦 K 线</span>
        <span v-if="cacheStatus"
              class="tabular-nums"
              :class="shouldPromptDownload
                ? 'text-[#dc2626] font-semibold'
                : (cacheStatus.missingCount > 0 ? 'text-[#854d0e]' : 'text-[#059669]')"
              :title="shouldPromptDownload
                ? `已收盘 + 有 ${cacheStatus.missingCount} 只缺今日数据，建议下载`
                : (cacheStatus.missingCount > 0
                    ? `${cacheStatus.missingCount} 只缺数据（盘前/盘中正常）`
                    : `全部已是最新（${cacheStatus.target_date}）`)">
            {{ cacheStatusHint }}
        </span>
        <span v-else-if="cacheStatusChecking" class="text-[#94a3b8]">检查中</span>
        <span v-else class="text-[#94a3b8]">未检测</span>

        <button v-if="downloaderPhase === 'idle' || downloaderPhase === 'done' || downloaderPhase === 'error'"
                @click="emit('download')"
                :disabled="scanning"
                class="text-[10px] px-[7px] py-[1px] rounded-[3px] border border-[#0369a1]/40
                       text-[#0369a1] bg-white hover:bg-[#eff6ff] hover:border-[#0369a1]
                       disabled:opacity-40 disabled:cursor-not-allowed transition">
            下载今日 K
        </button>
        <span v-else-if="downloaderPhase === 'checking'" class="text-[#0369a1] animate-pulse">预检中</span>
        <span v-else-if="downloaderPhase === 'downloading'" class="text-[#0369a1] tabular-nums">
            下载 {{ downloadedCount + failedCount }} / {{ totalToDownload }}
            <button @click="emit('cancel')" class="ml-[4px] text-[#94a3b8] hover:text-[#dc2626]">取消</button>
        </span>
    </div>
</template>
