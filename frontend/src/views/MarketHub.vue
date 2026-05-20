<script setup>
/**
 * 行情中心 —— 实时行情（Market）+ 历史复盘（Review）合二为一。
 *
 * 方案 G 实现：去掉独立 hub bar，hub segmented + 今日按钮 通过 `tabBarRight` slot
 * 注入到子 view 的 tab bar 右侧，单行解决双层 tab。
 */
import { ref, watch } from 'vue'
import Market from './Market.vue'
import Review from './Review.vue'
import HubControls from '../components/HubControls.vue'
import { useUserRole } from '../composables/useUserRole'

const props = defineProps({
    subTab: { type: String, default: 'live' },
})
const emit = defineEmits(['openAI', 'update:subTab'])

const { isAdmin } = useUserRole()
const activeTab = ref(props.subTab || 'live')

watch(() => props.subTab, (v) => {
    if (v && v !== activeTab.value) activeTab.value = v
})

watch(activeTab, (v) => {
    emit('update:subTab', v)
})

const TABS = [
    {
        key: 'live', label: '实时', title: '当下盘面：大盘 / 板块 / 涨跌池 / 涨停板',
        icon: 'M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z',
    },
    {
        key: 'review', label: '复盘', title: '某历史交易日的盘面快照 + 复盘标记',
        icon: 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',
    },
]
</script>

<template>
    <div class="flex flex-col h-full bg-[#f9fafb] overflow-hidden">
        <div class="flex-1 min-h-0 overflow-hidden">
            <KeepAlive>
                <Market v-if="activeTab === 'live'" key="live" @openAI="emit('openAI')">
                    <template #tabBarRight>
                        <HubControls :tabs="TABS" :activeKey="activeTab" :showToday="isAdmin" @select="activeTab = $event" />
                    </template>
                </Market>
                <Review v-else-if="activeTab === 'review'" key="review">
                    <template #tabBarRight>
                        <HubControls :tabs="TABS" :activeKey="activeTab" :showToday="isAdmin" @select="activeTab = $event" />
                    </template>
                </Review>
            </KeepAlive>
        </div>
    </div>
</template>
