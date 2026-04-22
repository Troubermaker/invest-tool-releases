<script setup>
import { ref } from 'vue'

const emit = defineEmits(['navigate'])
const props = defineProps(['currentTab'])

const navItems = [
  { id: 'market', name: '行情', icon: 'M3 13.122c-1.01.218-1.5 1.488-.8 2.302l1.378 1.603a5.5 5.5 0 007.828.18l2.96-2.613a2.25 2.25 0 013.161.025l2.678 2.502c.706.66 1.838.2 1.95-1.042l.375-4.167a5.5 5.5 0 00-6.17-5.961l-2.02.302a2.25 2.25 0 01-2.47-1.84L11.5 3a5.5 5.5 0 00-10.228 1.93l-.442 4.195c-.116 1.099.645 2.052 1.737 2.155l.433.04z' },
  { id: 'watchlist', name: '自选', icon: 'M11.48 3.499a.562.562 0 011.04 0l2.125 5.111a.563.563 0 00.475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 00-.182.557l1.285 5.385a.562.562 0 01-.84.61l-4.725-2.885a.562.562 0 00-.586 0L6.982 20.54a.562.562 0 01-.84-.61l1.285-5.386a.562.562 0 00-.182-.557l-4.204-3.602a.562.562 0 01.321-.988l5.518-.442a.563.563 0 00.475-.345L11.48 3.5z' },
  { id: 'positions', name: '持仓', icon: 'M10.5 6a7.5 7.5 0 107.5 7.5h-7.5V6z M13.5 10.5H21A7.5 7.5 0 0013.5 3v7.5z' },
  { id: 'review', name: '复盘', icon: 'M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z' }
]

const settingsItem = { id: 'settings', name: '设置', icon: 'M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z M12 15a3 3 0 100-6 3 3 0 000 6z' }
</script>

<template>
  <div class="h-full w-[64px] bg-[#fafafa] border-r border-[#e5e5e5] flex flex-col items-center py-4 z-10 shrink-0">
    <!-- Top Account/Logo Icon -->
    <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-[#ff6b6b] to-[#ee5253] text-white flex items-center justify-center font-bold mb-6 shadow-[0_2px_10px_rgba(238,82,83,0.3)] cursor-pointer">
      T
    </div>

    <!-- Main Navigation Items -->
    <div class="flex-1 flex flex-col gap-4 w-full">
      <button 
        v-for="item in navItems" 
        :key="item.id"
        @click="emit('navigate', item.id)"
        class="flex flex-col items-center w-full group relative py-2"
      >
        <div :class="['flex flex-col items-center transition-colors', currentTab === item.id ? 'text-[#dc2626]' : 'text-[#7f8fa6] hover:text-[#dc2626]']">
          <!-- Active Indicator line on left -->
          <div v-if="currentTab === item.id" class="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-full bg-[#dc2626]"></div>
          
          <svg class="w-[22px] h-[22px] mb-1.5 mx-auto" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" :d="item.icon" />
          </svg>
          <span class="text-[12px] font-medium block leading-none tracking-wider" :class="currentTab === item.id ? 'text-[#dc2626]' : 'text-[#7f8fa6] group-hover:text-[#dc2626]'">{{ item.name }}</span>
        </div>
      </button>
    </div>

    <!-- Settings at Bottom -->
    <div class="mt-auto w-full">
      <button 
        @click="emit('navigate', settingsItem.id)"
        class="flex flex-col items-center w-full group relative py-2 mb-2"
      >
        <div :class="['flex flex-col items-center transition-colors', currentTab === settingsItem.id ? 'text-[#dc2626]' : 'text-[#7f8fa6] hover:text-[#dc2626]']">
          <svg class="w-[22px] h-[22px] mb-1.5 mx-auto" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" :d="settingsItem.icon" />
          </svg>
          <span class="text-[12px] font-medium block leading-none tracking-wider">{{ settingsItem.name }}</span>
        </div>
      </button>
    </div>
  </div>
</template>
