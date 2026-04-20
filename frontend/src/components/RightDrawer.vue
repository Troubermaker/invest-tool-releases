<script setup>
import { ref } from 'vue'

const props = defineProps(['isOpen'])
const emit = defineEmits(['close'])

const messages = ref([
  { role: 'ai', text: '您好！我是你的 AI 投研助手。需要我为您整理今天的龙虎榜或复盘数据吗？' }
])
const input = ref('')

function send() {
  if(!input.value.trim()) return;
  messages.value.push({ role: 'user', text: input.value })
  const userText = input.value
  input.value = ''
  
  if (window.pywebview && window.pywebview.api) {
      messages.value.push({ role: 'ai', text: '正在拼命扫描市场数据并思考中...' })
      window.pywebview.api.analyze_market_query(userText).then(reply => {
          messages.value.pop()
          messages.value.push({ role: 'ai', text: reply })
      }).catch(e => {
          messages.value.pop()
          messages.value.push({ role: 'ai', text: '调用出错: ' + e })
      })
  } else {
      setTimeout(() => {
        messages.value.push({ role: 'ai', text: `关于"${userText}"，本地无桌面引擎。` })
      }, 600)
  }
}
</script>

<template>
  <div 
    class="fixed top-0 right-0 h-full w-[360px] bg-white border-l border-gray-200 shadow-[0_0_40px_rgba(0,0,0,0.1)] transition-transform duration-300 z-50 flex flex-col"
    :class="isOpen ? 'translate-x-0' : 'translate-x-full'"
  >
    <!-- Header -->
    <div class="h-14 border-b border-gray-100 flex items-center justify-between px-4 bg-gray-50/80">
      <div class="flex items-center gap-2">
        <div class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
        <span class="font-bold text-sm text-gray-800">AI 智能复盘助手</span>
      </div>
      <button @click="emit('close')" class="text-gray-400 hover:text-gray-600 bg-white p-1 rounded-md shadow-sm border border-gray-100">
        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
      </button>
    </div>

    <!-- Chat Area -->
    <div class="flex-1 p-4 overflow-y-auto flex flex-col gap-4 bg-gray-50/30">
      <div v-for="(msg, i) in messages" :key="i" :class="['max-w-[85%] rounded-lg p-3 text-sm shadow-sm', msg.role === 'ai' ? 'bg-white border border-gray-100 text-gray-700 self-start rounded-tl-none font-medium' : 'bg-red-50 border border-red-100 text-red-800 self-end rounded-tr-none']">
        {{ msg.text }}
      </div>
    </div>

    <!-- Input Area -->
    <div class="p-3 border-t border-gray-100 bg-white">
      <div class="flex gap-2">
        <input v-model="input" @keyup.enter="send" type="text" placeholder="输入你想复盘的板块或代码..." class="flex-1 bg-gray-50 border border-gray-200 rounded-md px-3 py-2 text-sm text-gray-800 focus:outline-none focus:border-red-400 focus:bg-white transition-colors placeholder:text-gray-400">
        <button @click="send" class="bg-red-500 hover:bg-red-600 text-white px-3 py-2 rounded-md transition-colors shadow-sm">
          <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" /></svg>
        </button>
      </div>
    </div>
  </div>
  
  <!-- Backdrop -->
  <div v-if="isOpen" @click="emit('close')" class="fixed inset-0 bg-gray-900/10 z-40 hidden md:block border-r-[360px] border-transparent backdrop-blur-sm transition-all"></div>
</template>
