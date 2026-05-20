<script setup>
/**
 * Hub 控件组合（segmented 分段控件 + 今日按钮）
 *
 * 给 MarketHub / QuantHub 共用，通过 slot 注入到各内容 view 的 tab bar 右侧。
 * 父组件传入：
 *   - tabs:        [{ key, label, icon, title }]  当前 hub 的 sub-tab 列表
 *   - activeKey:   当前选中的 sub-tab key
 *   - showToday:   是否显示今日按钮（默认 true）
 * 触发：
 *   - @select(key)  用户点击 sub-tab
 */
import { computed } from 'vue'
import { useDashboard } from '../composables/useDashboard'
import { useDailyAutoScan } from '../composables/useDailyAutoScan'

const props = defineProps({
    tabs:      { type: Array,  required: true },
    activeKey: { type: String, required: true },
    showToday: { type: Boolean, default: true },
})
const emit = defineEmits(['select'])

const dashboard = useDashboard()
const autoScan = useDailyAutoScan()
const todayStarCount = computed(() => autoScan.todayResult.value?.star_count || 0)
const todayStar4Count = computed(() => autoScan.todayResult.value?.star4_count || 0)
</script>

<template>
    <!-- 分段控件 segmented pill -->
    <div class="inline-flex bg-white border border-[#e5e7eb] rounded-md overflow-hidden">
        <button v-for="(t, i) in tabs" :key="t.key"
                @click="emit('select', t.key)"
                :title="t.title"
                :class="['flex items-center gap-[5px] px-[10px] py-[5px] text-[12px] transition select-none whitespace-nowrap',
                        i > 0 ? 'border-l border-[#e5e7eb]' : '',
                        activeKey === t.key
                          ? 'bg-[#dc2626] text-white font-medium'
                          : 'bg-white text-[#475569] hover:bg-[#fafafa] hover:text-[#111]']">
            <svg v-if="t.icon" class="w-[14px] h-[14px]" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" :d="t.icon" />
            </svg>
            <span>{{ t.label }}</span>
        </button>
    </div>

    <!-- 今日仪表盘触发按钮 -->
    <button v-if="showToday"
            @click="dashboard.toggle()"
            :class="['relative flex items-center gap-[5px] px-[10px] py-[5px] rounded-md text-[12px] font-medium transition border whitespace-nowrap',
                    dashboard.isOpen.value
                      ? 'bg-[#dc2626] text-white border-[#dc2626]'
                      : todayStarCount > 0
                        ? 'bg-white text-[#dc2626] border-[#dc2626] hover:bg-[#fff5f5]'
                        : 'bg-white text-[#475569] border-[#e5e7eb] hover:text-[#dc2626] hover:border-[#dc2626]']"
            :title="todayStarCount > 0 ? `今日 ${todayStarCount} 只 ⭐⭐⭐+ 信号（含 ⭐⭐⭐⭐ ${todayStar4Count}）· Ctrl+J` : '今日仪表盘 (Ctrl+J)'">
        <svg class="w-[14px] h-[14px]" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0M3.124 7.5A8.969 8.969 0 015.292 3m13.416 0a8.969 8.969 0 012.168 4.5" />
        </svg>
        <span>今日</span>
        <span v-if="todayStarCount > 0"
              :class="['absolute -top-[6px] -right-[6px] min-w-[18px] h-[18px] px-[4px] rounded-full flex items-center justify-center text-[10px] font-bold border-2 border-white tabular-nums bg-[#dc2626] text-white',
                      todayStar4Count > 0 ? 'animate-pulse' : '']">
            {{ todayStarCount }}
        </span>
    </button>
</template>
