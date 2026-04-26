<script setup>
import { ref } from 'vue'
import Sidebar from './components/Sidebar.vue'
import RightDrawer from './components/RightDrawer.vue'
import Market from './views/Market.vue'
import Watchlist from './views/Watchlist.vue'
import Positions from './views/Positions.vue'
import Review from './views/Review.vue'
import Settings from './views/Settings.vue'

const currentTab = ref('market')
const isAIDrawerOpen = ref(false)

function handleNavigate(tabId) {
    currentTab.value = tabId
}
</script>

<template>
  <div class="flex h-screen w-full bg-[#f9fafb] text-gray-800 overflow-hidden font-sans">
    
    <!-- Left Sidebar -->
    <Sidebar :currentTab="currentTab" @navigate="handleNavigate"/>

    <!-- Main Content Area -->
    <main class="flex-1 relative flex flex-col min-w-0">
        <!-- Render Active View -->
        <Market v-if="currentTab === 'market'" @openAI="isAIDrawerOpen = true"/>
        <Watchlist v-else-if="currentTab === 'watchlist'" />
        <Positions v-else-if="currentTab === 'positions'" />
        <Review v-else-if="currentTab === 'review'" />
        <Settings v-else-if="currentTab === 'settings'" />

        <!-- Placeholders for other tabs for now -->
        <div v-else class="flex items-center justify-center h-full text-gray-400 flex-col bg-white m-4 rounded-xl border border-gray-200">
            <svg class="w-16 h-16 mb-4 text-red-200" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" /></svg>
            <h2 class="text-2xl font-semibold text-gray-500 capitalize mb-2">{{ currentTab }} 模块</h2>
            <p class="text-sm font-medium">界面数据接入中 (Development in progress)</p>
            <button @click="currentTab = 'market'" class="mt-4 px-4 py-2 border border-red-500 text-red-500 rounded-lg hover:bg-red-50 font-bold transition text-sm">返回行情首页</button>
        </div>
    </main>

    <!-- Right Drawer for AI -->
    <RightDrawer :isOpen="isAIDrawerOpen" @close="isAIDrawerOpen = false"/>

  </div>
</template>

<style>
/* Ensure the body takes full height and hides scrollbars overall */
body {
    margin: 0;
    overflow: hidden;
}
/* Custom thin scrollbar for data panels */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #e5e7eb; /* gray-200 */
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: #d1d5db; /* gray-300 */
}
</style>
