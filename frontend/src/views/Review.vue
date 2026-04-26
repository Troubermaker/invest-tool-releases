<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api/client'

// ---------------- 日期 ----------------
function ymd(d) {
    const p = (n) => n.toString().padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
function todayStr() {
    return ymd(new Date())
}

// 交易日集合（从后端拉，自动调整非交易日选择用）
const tradingDaySet = ref(new Set())

async function loadTradingDays() {
    const res = await api.getTradingDays()
    if (res.ok && Array.isArray(res.data)) {
        tradingDaySet.value = new Set(res.data)
    }
}

// 找最近的一个交易日（先往前找 14 天，再往后找 14 天）
function findNearestTradingDay(dateStr) {
    if (tradingDaySet.value.size === 0) return dateStr  // 集合空时不调整
    const [y, m, d] = dateStr.split('-').map(Number)
    const t = new Date(y, m - 1, d)
    // 往前找
    for (let i = 0; i < 14; i++) {
        const ds = ymd(t)
        if (tradingDaySet.value.has(ds)) return ds
        t.setDate(t.getDate() - 1)
    }
    // 往后找（往回到原日 + 1 再前进 14 天）
    t.setDate(t.getDate() + 15)
    for (let i = 0; i < 14; i++) {
        const ds = ymd(t)
        if (tradingDaySet.value.has(ds)) return ds
        t.setDate(t.getDate() + 1)
    }
    return dateStr
}

// 找最近一个"已收盘"的交易日，用作复盘默认值
// 复盘视角：看的是"完整一天"的数据。未到 15:00 当日 session 没结束，
// 哪怕今天是交易日也要回退到昨天往前找。
function lastTradingDay() {
    const d = new Date()
    if (d.getHours() < 15) {
        d.setDate(d.getDate() - 1)  // 当日未收盘 → 跳到昨天
    }
    if (tradingDaySet.value.size > 0) {
        for (let i = 0; i < 14; i++) {
            if (tradingDaySet.value.has(ymd(d))) return ymd(d)
            d.setDate(d.getDate() - 1)
        }
    }
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1)
    return ymd(d)
}

// 默认值：先用今天，等 loadTradingDays 完成再修正
const selectedDate = ref(ymd(new Date()))
const adjustNotice = ref('')
let _adjustTimer = null

// ---------------- Sub-tab 子模块 ----------------
// 在复盘页面里横向切换不同的复盘视角
// 新增子 tab 只要在这里加一项 + 模板里加 v-if 分支就行
const SUB_TABS = [
    { id: 'sector',   name: '板块复盘' },
    { id: 'limitup',  name: '涨停池' },
    { id: 'breakout', name: '炸板 / 跌停' },
    { id: 'notes',    name: '复盘笔记' },
]
const activeTab = ref('sector')

// ---------------- 主数据状态 ----------------
const hotSectors = ref([])
const selectedSector = ref(null)
const sectorStocks = ref([])
const sectorStocksLoading = ref(false)
const stockFilter = ref('')
const ladderTiers = ref([])
const loadingMain = ref(false)

const sectorLimitUpCount = computed(() =>
    sectorStocks.value.filter(s => s.isLimitUp).length
)
const filteredStocks = computed(() => {
    const q = stockFilter.value.trim().toLowerCase()
    if (!q) return sectorStocks.value
    return sectorStocks.value.filter(s =>
        (s.name && s.name.toLowerCase().includes(q)) || (s.code && s.code.includes(q))
    )
})
const ladderTotalCount = computed(() =>
    ladderTiers.value.reduce((sum, t) => sum + (t.number || 0), 0)
)

// 连板档位色阶（与 Market.vue 一致）
function tierStyle(height) {
    if (height >= 8) return { main: '#991b1b', tint: 'rgba(153, 27, 27, 0.10)' }
    if (height >= 6) return { main: '#dc2626', tint: 'rgba(220, 38, 38, 0.09)' }
    if (height >= 4) return { main: '#ea580c', tint: 'rgba(234, 88, 12, 0.09)' }
    if (height >= 3) return { main: '#f59e0b', tint: 'rgba(245, 158, 11, 0.08)' }
    return { main: '#78716c', tint: 'rgba(120, 113, 108, 0.05)' }
}

// ---------------- 数据加载 ----------------
async function loadAll() {
    if (!selectedDate.value) return
    loadingMain.value = true
    try {
        const [marketRes, ladderRes] = await Promise.all([
            api.getMarketData(selectedDate.value),
            api.getLimitUpLadder(selectedDate.value),
        ])
        if (marketRes.ok) {
            hotSectors.value = marketRes.data?.hotSectors || []
            if (hotSectors.value.length > 0) {
                selectedSector.value = hotSectors.value[0]
                loadSectorStocks(hotSectors.value[0].code)
            } else {
                selectedSector.value = null
                sectorStocks.value = []
            }
        }
        if (ladderRes.ok) {
            ladderTiers.value = ladderRes.data || []
        }
    } finally {
        loadingMain.value = false
    }
}

async function loadSectorStocks(plateId) {
    if (!plateId) { sectorStocks.value = []; return }
    sectorStocksLoading.value = true
    try {
        const res = await api.getSectorStocks(plateId, selectedDate.value)
        sectorStocks.value = res.ok ? (res.data || []) : []
    } finally {
        sectorStocksLoading.value = false
    }
}

function handleSectorClick(sector) {
    if (selectedSector.value?.code === sector.code) return
    selectedSector.value = sector
    stockFilter.value = ''
    loadSectorStocks(sector.code)
}

// 用户改日期 → 校验是不是交易日，不是就自动调整 + 提示
watch(selectedDate, (newDate, oldDate) => {
    if (!newDate) return
    // tradingDaySet 还没加载完时直接用，loaded 后会触发再次 watch
    if (tradingDaySet.value.size > 0 && !tradingDaySet.value.has(newDate)) {
        const adjusted = findNearestTradingDay(newDate)
        if (adjusted && adjusted !== newDate) {
            adjustNotice.value = `${newDate} 非交易日，已自动调整为 ${adjusted}`
            selectedDate.value = adjusted  // 触发新一轮 watch（adjusted 在 set 里，下次走正常分支）
            clearTimeout(_adjustTimer)
            _adjustTimer = setTimeout(() => { adjustNotice.value = '' }, 3500)
            return  // 等下一轮 watch 触发 loadAll
        }
    }
    adjustNotice.value = ''
    loadAll()
})

onMounted(async () => {
    await loadTradingDays()
    selectedDate.value = lastTradingDay()  // 校准为最近一个真正的交易日
    // watch 会自动触发 loadAll
})

// 步进按钮：在交易日列表里前后翻一个
function shiftToTradingDay(direction) {
    const days = [...tradingDaySet.value].sort()
    if (!days.length) return
    const idx = days.indexOf(selectedDate.value)
    if (idx === -1) {
        selectedDate.value = lastTradingDay()
        return
    }
    const next = direction < 0 ? days[idx - 1] : days[idx + 1]
    if (next) selectedDate.value = next
}
</script>

<template>
  <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

    <!-- Tab 栏：跟自选/持仓 tab 风格一致 -->
    <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0">
        <!-- 左侧 sub-tab -->
        <div class="flex-1 flex items-center gap-[2px] px-[12px] overflow-x-auto custom-scrollbar min-w-0">
            <div v-for="t in SUB_TABS" :key="t.id"
                 @click="activeTab = t.id"
                 class="px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0"
                 :class="activeTab === t.id
                    ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                    : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'">
                {{ t.name }}
            </div>
        </div>

        <!-- 右侧：日期控件 + 自动调整提示 -->
        <div class="shrink-0 border-l border-[#e5e5e5] pl-[10px] pr-[12px] flex items-center gap-[6px]">
            <span v-if="adjustNotice"
                  class="text-[11px] text-[#dc2626] bg-[#fff0f0] border border-[#fecaca] px-[8px] py-[2px] rounded-[4px] animate-pulse">
                {{ adjustNotice }}
            </span>
            <button @click="shiftToTradingDay(-1)"
                    class="w-[22px] h-[22px] flex items-center justify-center text-[#666] hover:text-[#dc2626] hover:bg-white rounded transition"
                    title="上一交易日">‹</button>
            <input v-model="selectedDate"
                   type="date"
                   :max="todayStr()"
                   class="text-[12px] px-[8px] py-[3px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums bg-white">
            <button @click="shiftToTradingDay(1)"
                    class="w-[22px] h-[22px] flex items-center justify-center text-[#666] hover:text-[#dc2626] hover:bg-white rounded transition"
                    title="下一交易日">›</button>
            <button @click="selectedDate = lastTradingDay()"
                    class="text-[11px] text-[#666] hover:text-[#dc2626] transition px-[4px]"
                    title="跳到最近一个交易日">
                最近
            </button>
        </div>
    </div>

    <!-- ============ 板块复盘（主 sub-tab）============ -->
    <div v-if="activeTab === 'sector'" class="flex-1 flex overflow-hidden w-full bg-white">

      <!-- 左：精选板块榜 -->
      <div class="w-[220px] bg-white border-r border-[#e5e5e5] flex flex-col flex-shrink-0">
        <div class="h-[44px] bg-[#fff5f5] text-[#dc2626] border-b border-[#ffe5e5] flex items-center justify-between px-[12px] font-semibold text-[13px]">
          <span>精选板块</span>
          <span class="text-[11px] text-[#dc2626]/70 font-normal">{{ hotSectors.length }} 个</span>
        </div>
        <div class="flex-1 overflow-y-auto custom-scrollbar">
            <div v-if="loadingMain && !hotSectors.length" class="text-[12px] text-[#ccc] text-center py-[60px]">
                加载中...
            </div>
            <div v-else-if="!hotSectors.length" class="text-[12px] text-[#ccc] text-center py-[60px]">
                {{ selectedDate }} 无板块数据<br>
                <span class="text-[11px]">（可能不是交易日）</span>
            </div>
            <div v-for="item in hotSectors" :key="item.name"
                 @click="handleSectorClick(item)"
                 class="flex items-center justify-between py-[10px] px-[10px] border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition"
                 :class="selectedSector?.code === item.code ? 'bg-[#fff0f0] border-l-2 border-l-[#dc2626] pr-[10px] pl-[8px]' : 'border-l-2 border-l-transparent'">
              <div class="flex items-center gap-[10px] flex-1 min-w-0">
                <div class="w-[22px] h-[22px] shrink-0 rounded-[4px] flex items-center justify-center text-[11px] font-bold"
                     :class="item.rank <= 3 ? 'bg-[#dc2626] text-white' : 'bg-[#f0f0f0] text-[#777]'">
                  {{ item.rank }}
                </div>
                <div class="flex flex-col min-w-0">
                  <span class="text-[14px] font-bold text-[#222] truncate" :title="item.name">{{ item.name }}</span>
                  <span v-if="item.strength" class="text-[11px] text-[#999] mt-[3px] tabular-nums">
                    强度 {{ item.strength.toLocaleString() }}
                  </span>
                </div>
              </div>
              <div class="flex flex-col items-end shrink-0 ml-2">
                <span class="text-[13px] font-bold" :class="item.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ item.change }}</span>
                <span class="text-[11px] mt-[3px]" :class="(item.inflow || '').startsWith('-') ? 'text-[#059669]/80' : 'text-[#dc2626]/75'">{{ item.inflow }}</span>
              </div>
            </div>
        </div>
      </div>

      <!-- 中：板块联动股 -->
      <div class="flex-1 bg-white relative overflow-hidden flex flex-col">
        <template v-if="selectedSector">
            <div class="h-[43px] px-[14px] border-b border-[#f0f0f0] flex justify-between items-center bg-white shrink-0">
                <div class="flex items-center gap-2 min-w-0">
                    <h2 class="text-[14px] font-bold text-[#111] truncate">{{ selectedSector.name }}</h2>
                    <div class="text-[11px] text-[#dc2626] font-bold bg-[#fff5f5] px-[6px] py-[1px] rounded-[4px] border border-[#ffe5e5] shrink-0">
                        领涨 {{ selectedSector.change }}
                    </div>
                    <div v-if="sectorLimitUpCount > 0"
                         class="text-[11px] font-bold bg-[#dc2626] text-white px-[6px] py-[1px] rounded-[4px] shrink-0">
                        涨停 {{ sectorLimitUpCount }}
                    </div>
                </div>
                <div class="relative">
                    <input v-model="stockFilter" type="text" placeholder="筛选 名称 / 代码..."
                           class="bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] pl-[10px] pr-[26px] py-[4px] text-[12px] outline-none focus:border-[#dc2626] focus:bg-white w-[180px] transition placeholder:text-[#ccc]">
                    <button v-if="stockFilter" @click="stockFilter = ''"
                            class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[16px] h-[16px] flex items-center justify-center rounded-full text-[#aaa] hover:bg-[#f0f0f0]">
                        <svg class="w-[10px] h-[10px]" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                        </svg>
                    </button>
                </div>
            </div>

            <div class="flex-1 overflow-auto custom-scrollbar">
                <table class="w-full text-left border-collapse whitespace-nowrap min-w-max">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] z-10 text-[12px] text-[#888]">
                        <tr>
                            <th class="px-[12px] py-[10px] font-normal w-[70px]">代码</th>
                            <th class="px-[12px] py-[10px] font-normal w-[220px]">名称</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">收盘价</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">涨幅</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">成交额</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">主力买</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">主力卖</th>
                            <th class="px-[12px] py-[10px] font-normal text-right">主力净额</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="stock in filteredStocks" :key="stock.code"
                            class="border-b border-[#f5f5f5] hover:bg-[#f2f8fc] transition-colors group">
                            <td class="px-[12px] py-[10px] text-[12px] text-[#666] font-mono align-top">{{ stock.code }}</td>
                            <td class="px-[12px] py-[10px] align-top">
                                <div class="flex items-center gap-[6px] flex-wrap">
                                    <svg v-if="stock.isLimitUp" class="w-[14px] h-[14px] shrink-0 text-[#ea580c]" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M12.395 2.553a1 1 0 00-1.45-.385c-.345.23-.614.558-.822.88-.214.33-.403.713-.57 1.116-.334.804-.614 1.768-.84 2.734a31.365 31.365 0 00-.613 3.58 2.64 2.64 0 01-.945-1.067c-.328-.68-.398-1.534-.398-2.654A1 1 0 005.05 6.05 6.981 6.981 0 003 11a7 7 0 1011.95-4.95c-.592-.591-.98-.985-1.348-1.467-.363-.476-.724-1.063-1.207-2.03zM12.12 15.12A3 3 0 017 13s.879.5 2.5.5c0-1 .5-4 1.25-4.5.5 1 .786 1.293 1.371 1.879A2.99 2.99 0 0113 13a2.99 2.99 0 01-.879 2.121z" clip-rule="evenodd" />
                                    </svg>
                                    <span class="text-[14px] font-bold text-[#111]">{{ stock.name }}</span>
                                    <span v-if="stock.leader"
                                          class="text-[10px] font-bold px-[6px] py-[1px] rounded-[3px] text-white"
                                          :class="stock.leader === '破板' ? 'bg-[#94a3b8]' : 'bg-[#dc2626]'">
                                        {{ stock.leader }}
                                    </span>
                                    <span v-if="stock.streak"
                                          class="text-[10px] font-semibold px-[6px] py-[1px] rounded-[3px] bg-[#fff0f0] text-[#dc2626] border border-[#fecaca]">
                                        {{ stock.streak }}
                                    </span>
                                </div>
                                <div v-if="stock.mainForce || stock.themesAll" class="mt-[4px] flex items-center gap-[4px] truncate" :title="stock.themesAll">
                                    <span v-if="stock.mainForce" class="shrink-0 text-[10px] font-semibold px-[5px] py-[1px] rounded-[3px]"
                                          :class="stock.mainForce === '游资' ? 'bg-[#f3e8ff] text-[#7c3aed]' : 'bg-[#dbeafe] text-[#1d4ed8]'">
                                        {{ stock.mainForce }}
                                    </span>
                                    <span v-if="stock.themesAll" class="text-[11px] text-[#999] truncate">{{ stock.themesAll }}</span>
                                </div>
                            </td>
                            <td class="px-[10px] py-[10px] text-[14px] font-bold text-right align-top tabular-nums" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.price }}</td>
                            <td class="px-[10px] py-[10px] text-[13px] font-bold text-right align-top tabular-nums" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.change }}</td>
                            <td class="px-[10px] py-[10px] text-[12px] text-[#475569] text-right align-top tabular-nums">{{ stock.turnover }}</td>
                            <td class="px-[10px] py-[10px] text-[12px] text-[#475569] text-right align-top tabular-nums">{{ stock.mainBuy }}</td>
                            <td class="px-[10px] py-[10px] text-[12px] text-[#475569] text-right align-top tabular-nums">{{ stock.mainSell }}</td>
                            <td class="px-[12px] py-[10px] text-[13px] font-bold text-right align-top tabular-nums" :class="stock.mainNetUp ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.mainNet }}</td>
                        </tr>
                        <tr v-if="sectorStocksLoading && !sectorStocks.length">
                            <td colspan="8" class="px-[20px] py-[60px] text-center text-[#aaa] text-[13px]">
                                正在拉取 {{ selectedSector.name }} 板块联动股...
                            </td>
                        </tr>
                        <tr v-else-if="!sectorStocks.length">
                            <td colspan="8" class="px-[20px] py-[80px] text-center text-[#aaa] text-[13px]">
                                {{ selectedDate }} 该板块无成分股数据
                            </td>
                        </tr>
                        <tr v-else-if="!filteredStocks.length">
                            <td colspan="8" class="px-[20px] py-[60px] text-center text-[#aaa] text-[13px]">
                                未匹配到"{{ stockFilter }}"，
                                <button @click="stockFilter = ''" class="text-[#dc2626] hover:underline">清空筛选</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </template>
        <div v-else class="flex-1 flex items-center justify-center text-[#ccc] text-[14px]">
            {{ loadingMain ? '加载板块数据中...' : `${selectedDate} 无可用板块` }}
        </div>
      </div>

      <!-- 右：连板天梯 -->
      <div class="w-[340px] bg-white border-l border-[#e5e5e5] flex flex-col flex-shrink-0">
        <div class="h-[44px] bg-[#fff5f5] text-[#dc2626] border-b border-[#ffe5e5] flex items-center justify-between px-[14px] shrink-0">
          <span class="font-semibold text-[13px]">连板天梯</span>
          <span class="text-[11px] text-[#dc2626]/70">{{ ladderTotalCount }} 只</span>
        </div>
        <div class="flex-1 overflow-y-auto custom-scrollbar">
            <div v-if="!ladderTiers.length && !loadingMain" class="flex items-center justify-center h-full text-[#ccc] text-[12px]">
                {{ selectedDate }} 无连板数据
            </div>
            <div v-else-if="loadingMain" class="flex items-center justify-center h-full text-[#ccc] text-[12px]">
                加载中...
            </div>
            <div v-for="tier in ladderTiers" :key="tier.height" class="border-b border-[#f5f5f5]">
                <div class="px-[14px] py-[8px] flex items-center justify-between sticky top-0 z-[1]"
                     :style="{ background: tierStyle(tier.height).tint }">
                    <span class="text-[13px] font-bold" :style="{ color: tierStyle(tier.height).main }">
                        {{ tier.label }}
                    </span>
                    <span class="text-[11px] font-medium" :style="{ color: tierStyle(tier.height).main }">
                        {{ tier.stocks.length }} 只
                    </span>
                </div>
                <div v-for="stock in tier.stocks" :key="stock.code"
                     class="px-[14px] py-[8px] hover:bg-[#fafafa] transition">
                    <div class="flex items-center justify-between">
                        <span class="text-[13px] font-bold text-[#111]">{{ stock.name }}</span>
                        <span class="text-[10px] text-[#999] font-mono tabular-nums">{{ stock.code }}</span>
                    </div>
                    <div v-if="stock.concepts && stock.concepts.length"
                         class="mt-[3px] flex flex-wrap gap-[3px]"
                         :title="stock.reasonAll">
                        <span v-for="(c, i) in stock.concepts.slice(0, 3)" :key="i"
                              class="text-[10px] text-[#dc2626] bg-[#fff5f5] px-[5px] py-[1px] rounded-sm">
                            {{ c }}
                        </span>
                        <span v-if="stock.concepts.length > 3" class="text-[10px] text-[#999]">
                            +{{ stock.concepts.length - 3 }}
                        </span>
                    </div>
                </div>
            </div>
        </div>
      </div>

    </div>
    <!-- /板块复盘 -->

    <!-- ============ 占位 sub-tab ============ -->
    <div v-else class="flex-1 overflow-auto custom-scrollbar bg-white px-[24px] py-[24px]">
        <div class="max-w-[640px] mx-auto bg-[#fafafa] border border-[#eeeeee] rounded-[8px] p-[24px]">
            <!-- 涨停池 -->
            <template v-if="activeTab === 'limitup'">
                <div class="flex items-baseline gap-[10px]">
                    <div class="text-[15px] font-bold text-[#111]">涨停池</div>
                    <span class="text-[10px] font-normal text-[#dc2626] bg-[#fff0f0] border border-[#fecaca] px-[6px] py-[1px] rounded-sm">规划中</span>
                </div>
                <div class="text-[12px] text-[#888] mt-[4px]">{{ selectedDate }} 当日所有涨停股</div>
                <div class="text-[12px] text-[#666] leading-relaxed mt-[16px] space-y-[8px]">
                    <div>计划展示：</div>
                    <ul class="list-disc list-inside text-[#888] space-y-[4px] ml-[4px]">
                        <li>当日全部涨停股按封板时间排序</li>
                        <li>每只股的连板数（首板 / 几板）+ 题材标签</li>
                        <li>封单金额 + 涨停时间 + 是否炸板回封</li>
                        <li>主力性质（机构 / 游资）+ 龙头排名</li>
                    </ul>
                </div>
            </template>

            <!-- 炸板 / 跌停 -->
            <template v-else-if="activeTab === 'breakout'">
                <div class="flex items-baseline gap-[10px]">
                    <div class="text-[15px] font-bold text-[#111]">炸板 / 跌停</div>
                    <span class="text-[10px] font-normal text-[#dc2626] bg-[#fff0f0] border border-[#fecaca] px-[6px] py-[1px] rounded-sm">规划中</span>
                </div>
                <div class="text-[12px] text-[#888] mt-[4px]">当日盘中开板及跌停股表现</div>
                <div class="text-[12px] text-[#666] leading-relaxed mt-[16px] space-y-[8px]">
                    <div>计划展示：</div>
                    <ul class="list-disc list-inside text-[#888] space-y-[4px] ml-[4px]">
                        <li>炸板股列表 + 是否回封 + 最终涨幅</li>
                        <li>跌停股按封单大小排序</li>
                        <li>当日"涨跌停比"市场强弱指标</li>
                    </ul>
                </div>
            </template>

            <!-- 复盘笔记 -->
            <template v-else-if="activeTab === 'notes'">
                <div class="flex items-baseline gap-[10px]">
                    <div class="text-[15px] font-bold text-[#111]">复盘笔记</div>
                    <span class="text-[10px] font-normal text-[#dc2626] bg-[#fff0f0] border border-[#fecaca] px-[6px] py-[1px] rounded-sm">规划中</span>
                </div>
                <div class="text-[12px] text-[#888] mt-[4px]">每个交易日的心得记录</div>
                <div class="text-[12px] text-[#666] leading-relaxed mt-[16px] space-y-[8px]">
                    <div>计划展示：</div>
                    <ul class="list-disc list-inside text-[#888] space-y-[4px] ml-[4px]">
                        <li>按交易日存档的 Markdown 编辑器</li>
                        <li>关联当日操作 / 关注股票 / 当日做错的事</li>
                        <li>关键词搜索过往笔记</li>
                        <li>导出/导入跟着备份系统走</li>
                    </ul>
                </div>
            </template>
        </div>
    </div>

  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }

</style>
