<script setup>
/**
 * 量化中心 —— 候选 / 日志 / 选股 / 回测 4 个子 view 整合。
 *
 * Tab 顺序按点击频率从高到低排：
 *   候选（盯池子，最高频）→ 日志（盯持仓/平仓，次高频）→ 选股（每日 1 次）→ 回测（周/月复盘）
 * Fitts 法则：常用 tab 离默认位置最近。候选既是首位也是默认激活 tab，肌肉记忆顺。
 *
 * 方案 G 实现：去掉独立 hub bar，hub segmented + 今日按钮 通过 `tabBarRight` slot
 * 注入到子 view 的 tab bar 右侧，单行解决双层 tab。
 */
import { ref, watch } from 'vue'
import Quant from './Quant.vue'
import CandidatePool from './CandidatePool.vue'
import Backtest from './Backtest.vue'
import Journal from './Journal.vue'
import HubControls from '../components/HubControls.vue'

const props = defineProps({
    subTab: { type: String, default: 'candidates' },
})
const emit = defineEmits(['update:subTab'])

const activeTab = ref(props.subTab || 'candidates')

watch(() => props.subTab, (v) => {
    if (v && v !== activeTab.value) activeTab.value = v
})

watch(activeTab, (v) => {
    emit('update:subTab', v)
})

// 顺序 = 候选 → 日志 → 选股 → 回测（高频在左，低频在右）
const TABS = [
    {
        key: 'candidates', label: '候选', title: '扫描器选中后 ⭐ 收藏的票，自动追踪买点',
        icon: 'M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0z',
    },
    {
        key: 'journal', label: '日志', title: '实战交易记录 + 真实胜率 + 周趋势（量化的实盘验证）',
        icon: 'M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10',
    },
    {
        key: 'scanner', label: '选股', title: '5 个扫描器：全部信号 / 主升突破 / 突破前夜 / 龙回头 / 连板游资',
        icon: 'm21 21-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607Z',
    },
    {
        key: 'backtest', label: '回测', title: '历史 runs + ML 模型健康 + IC 趋势',
        icon: 'M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.402 1.402c-1.232 1.232-.65 3.318 1.067 3.611A48.309 48.309 0 0012 21c2.773 0 5.491-.235 8.135-.687 1.718-.293 2.3-2.379 1.067-3.61L19.8 15.3z',
    },
]
</script>

<template>
    <div class="flex flex-col h-full bg-[#f9fafb] overflow-hidden">
        <div class="flex-1 min-h-0 overflow-hidden">
            <KeepAlive>
                <Quant v-if="activeTab === 'scanner'" key="scanner">
                    <template #tabBarRight>
                        <HubControls :tabs="TABS" :activeKey="activeTab" @select="activeTab = $event" />
                    </template>
                </Quant>
                <CandidatePool v-else-if="activeTab === 'candidates'" key="candidates">
                    <template #tabBarRight>
                        <HubControls :tabs="TABS" :activeKey="activeTab" @select="activeTab = $event" />
                    </template>
                </CandidatePool>
                <Backtest v-else-if="activeTab === 'backtest'" key="backtest">
                    <template #tabBarRight>
                        <HubControls :tabs="TABS" :activeKey="activeTab" @select="activeTab = $event" />
                    </template>
                </Backtest>
                <Journal v-else-if="activeTab === 'journal'" key="journal">
                    <template #tabBarRight>
                        <HubControls :tabs="TABS" :activeKey="activeTab" @select="activeTab = $event" />
                    </template>
                </Journal>
            </KeepAlive>
        </div>
    </div>
</template>
