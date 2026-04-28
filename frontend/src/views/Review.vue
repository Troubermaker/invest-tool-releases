<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import Sortable from 'sortablejs'
import { api } from '../api/client'
import { openStockChart } from '../composables/useStockChart'

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

// ---------------- Sub-tab 子模块（可拖拽重排序）----------------
const ALL_SUB_TABS = [
    { id: 'sector', name: '板块复盘' },
    { id: 'pools',  name: '涨跌池复盘' },
]
const subTabs = ref([...ALL_SUB_TABS])
const activeTab = ref('sector')

const tabBarRef = ref(null)
let _subTabSortable = null
const TAB_ORDER_PREF_KEY = 'review_subtab_order'

async function loadSubTabOrder() {
    const res = await api.getUserPreference(TAB_ORDER_PREF_KEY)
    if (res.ok && Array.isArray(res.data) && res.data.length) {
        const idMap = new Map(ALL_SUB_TABS.map(t => [t.id, t]))
        const ordered = res.data.filter(id => idMap.has(id)).map(id => idMap.get(id))
        const missing = ALL_SUB_TABS.filter(t => !res.data.includes(t.id))
        subTabs.value = [...ordered, ...missing]
    }
}
async function saveSubTabOrder() {
    await api.setUserPreference(TAB_ORDER_PREF_KEY, subTabs.value.map(t => t.id))
}
function initSubTabSortable() {
    if (!tabBarRef.value) return
    if (_subTabSortable) _subTabSortable.destroy()
    _subTabSortable = Sortable.create(tabBarRef.value, {
        animation: 200,
        delay: 150,
        delayOnTouchOnly: true,
        ghostClass: 'tab-drag-ghost',
        chosenClass: 'tab-drag-chosen',
        onEnd: () => {
            const tabs = tabBarRef.value.querySelectorAll('[data-tab-id]')
            const idMap = new Map(subTabs.value.map(t => [t.id, t]))
            subTabs.value = Array.from(tabs).map(el => idMap.get(el.dataset.tabId))
            saveSubTabOrder()
        },
    })
}

// ---------------- 主数据状态 ----------------
const hotSectors = ref([])
const selectedSector = ref(null)
const sectorStocks = ref([])
const sectorStocksLoading = ref(false)
const stockFilter = ref('')
// 后端返全量；ladderIncludeSt 决定前端是否过滤掉 ST/*ST/SST/次新
const ladderTiersRaw = ref([])
const ladderIncludeSt = ref(false)
const LADDER_ST_PREF_KEY = 'ladder.include_st'   // 跟主页共用一个偏好
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

// 前端过滤后的天梯：勾选 ST 时返原数据；未勾时按股名前缀过滤
const ladderTiers = computed(() => {
    if (ladderIncludeSt.value) return ladderTiersRaw.value
    return ladderTiersRaw.value
        .map(tier => ({
            ...tier,
            stocks: tier.stocks.filter(s => !/^\*?S?ST/i.test(s.name || '')),
        }))
        .map(tier => ({ ...tier, number: tier.stocks.length }))
        .filter(tier => tier.stocks.length > 0)
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

// ---------------- 涨跌池复盘（5 池：连板/涨停/炸板/冲刺/跌停）----------------
const POOL_DEFS = [
    { key: 'continuous', label: '连板池' },
    { key: 'limitUp',    label: '涨停池' },
    { key: 'broken',     label: '炸板池' },
    { key: 'sprint',     label: '冲刺涨停' },
    { key: 'limitDown',  label: '跌停池' },
]
const poolsLoaded = ref({})
const poolsLoading = ref({})
const activePoolKey = ref('continuous')
const poolFilter = ref('')

const activePool = computed(() => poolsLoaded.value[activePoolKey.value] || null)
const activePoolLoading = computed(() => !!poolsLoading.value[activePoolKey.value])

const filteredPoolStocks = computed(() => {
    if (!activePool.value) return []
    const q = poolFilter.value.trim().toLowerCase()
    if (!q) return activePool.value.stocks
    return activePool.value.stocks.filter(s =>
        (s.code || '').toLowerCase().includes(q) ||
        (s.name || '').toLowerCase().includes(q)
    )
})

const colHas = computed(() => {
    const stocks = activePool.value?.stocks || []
    const has = (getter) => stocks.some(s => {
        const v = getter(s)
        return v !== null && v !== undefined && v !== ''
    })
    return {
        price:            has(s => s.price),
        turnover:         has(s => s.turnoverRate),
        circulationValue: has(s => s.circulationValue),
        reason:           has(s => s.reason),
        limitType:        has(s => s.limitType),
        highDays:         has(s => s.highDays),
        firstLimitTime:   has(s => s.firstLimitTime),
        brokenTime:       has(s => s.brokenTime || s.lastLimitTime),
        orderAmount:      has(s => s.orderAmount),
        openNum:          has(s => s.openNum != null),
    }
})

async function loadPool(key, force = false) {
    if (!force && poolsLoaded.value[key]) return
    if (poolsLoading.value[key]) return
    poolsLoading.value = { ...poolsLoading.value, [key]: true }
    try {
        const res = await api.getLimitPool(key, selectedDate.value)
        if (res.ok && res.data) {
            poolsLoaded.value = { ...poolsLoaded.value, [key]: res.data }
        }
    } finally {
        poolsLoading.value = { ...poolsLoading.value, [key]: false }
    }
}

let _poolsSequentialActive = false
async function loadPoolsSequential() {
    if (_poolsSequentialActive) return
    _poolsSequentialActive = true
    try {
        for (const def of POOL_DEFS) {
            await loadPool(def.key)
        }
    } finally {
        _poolsSequentialActive = false
    }
}

// 切到涨跌池 tab → 启动顺次加载
watch(activeTab, (v) => {
    if (v === 'pools') loadPoolsSequential()
})
// 点击池子按钮 → 立即拉它（如果还没轮到）
watch(activePoolKey, (key) => { loadPool(key); poolFilter.value = '' })

function poolColor(key) { return key === 'limitDown' ? '#059669' : '#dc2626' }
function poolArrow(key) { return key === 'limitDown' ? '▼' : '▲' }
function fmtMoney(v) {
    if (v == null) return '—'
    if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
    if (v >= 1e4) return (v / 1e4).toFixed(2) + '万'
    return v.toFixed(0)
}
function fmtPct(v) {
    if (v == null) return '—'
    return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}
function concepts(reason) {
    return reason ? reason.split('+').map(c => c.trim()).filter(Boolean) : []
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
            ladderTiersRaw.value = ladderRes.data || []
        }
    } finally {
        loadingMain.value = false
    }
}

// 切换"显示 ST"只持久化偏好；ladderTiers 是 computed，自动重算
watch(ladderIncludeSt, async (v) => {
    await api.setUserPreference(LADDER_ST_PREF_KEY, v)
})

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
    // 涨跌池缓存按 date 隔离 → 改日期就清掉，下次进入或切按钮会按新日期重拉
    poolsLoaded.value = {}
    if (activeTab.value === 'pools') loadPoolsSequential()
})

onMounted(async () => {
    await loadSubTabOrder()
    await nextTick()
    initSubTabSortable()
    await loadTradingDays()
    // 加载 ST 显示偏好（跟主页共用一个 key）
    const stPref = await api.getUserPreference(LADDER_ST_PREF_KEY, false)
    if (stPref.ok && typeof stPref.data === 'boolean') ladderIncludeSt.value = stPref.data
    selectedDate.value = lastTradingDay()  // 校准为最近一个真正的交易日
    // watch 会自动触发 loadAll
})

onUnmounted(() => {
    if (_subTabSortable) { _subTabSortable.destroy(); _subTabSortable = null }
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
        <!-- 左侧 sub-tab（可拖拽重排序）-->
        <div ref="tabBarRef"
             class="flex-1 flex items-center gap-[2px] px-[12px] overflow-x-auto custom-scrollbar min-w-0">
            <div v-for="t in subTabs" :key="t.id"
                 :data-tab-id="t.id"
                 @click="activeTab = t.id"
                 class="px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0 select-none"
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
                            @dblclick="openStockChart(stock.code, stock.name)"
                            :title="'双击查看 K 线'"
                            class="border-b border-[#f5f5f5] hover:bg-[#f2f8fc] transition-colors group cursor-pointer">
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

      <!-- 右：连板天梯（跟主页同款样式：左色条 + 渐变档位头 + 2 列栅格） -->
      <div class="w-[340px] bg-white border-l border-[#e5e5e5] flex flex-col flex-shrink-0 z-0 relative">
        <div class="h-[44px] bg-[#fff5f5] text-[#dc2626] border-b border-[#ffe5e5] flex items-center justify-between px-[14px] shrink-0">
          <div class="flex items-center gap-[6px]">
            <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M12.395 2.553a1 1 0 00-1.45-.385c-.345.23-.614.558-.822.88-.214.33-.403.713-.57 1.116-.334.804-.614 1.768-.84 2.734a31.365 31.365 0 00-.613 3.58 2.64 2.64 0 01-.945-1.067c-.328-.68-.398-1.534-.398-2.654A1 1 0 005.05 6.05 6.981 6.981 0 003 11a7 7 0 1011.95-4.95c-.592-.591-.98-.985-1.348-1.467-.363-.476-.724-1.063-1.207-2.03zM12.12 15.12A3 3 0 017 13s.879.5 2.5.5c0-1 .5-4 1.25-4.5.5 1 .786 1.293 1.371 1.879A2.99 2.99 0 0113 13a2.99 2.99 0 01-.879 2.121z" clip-rule="evenodd" />
            </svg>
            <span class="font-semibold text-[13px]">连板天梯</span>
          </div>
          <div class="flex items-center gap-[8px]">
            <!-- ST 开关：跟主页保持一致 -->
            <label class="flex items-center gap-[3px] text-[10px] text-[#dc2626]/70 font-medium cursor-pointer select-none hover:text-[#dc2626] transition"
                   title="勾选后显示 ST / *ST / 次新（涨跌停规则不同，默认排除）">
                <input type="checkbox"
                       v-model="ladderIncludeSt"
                       class="w-[11px] h-[11px] accent-[#dc2626] cursor-pointer"
                       />
                <span>ST</span>
            </label>
            <span class="text-[11px] text-[#dc2626]/70 font-medium">{{ ladderTotalCount }} 只</span>
          </div>
        </div>
        <div class="flex-1 overflow-y-auto custom-scrollbar">
            <!-- Empty / loading -->
            <div v-if="loadingMain && !ladderTiers.length" class="flex items-center justify-center h-full text-[#ccc] text-[12px]">
                加载天梯数据中...
            </div>
            <div v-else-if="!ladderTiers.length" class="flex items-center justify-center h-full text-[#ccc] text-[12px]">
                {{ selectedDate }} 无连板数据
            </div>

            <!-- Tier groups -->
            <div v-for="tier in ladderTiers" :key="tier.height"
                 class="relative">
                <!-- 左侧连续 accent 色条 -->
                <div class="absolute left-0 top-0 bottom-0 w-[3px]"
                     :style="{ background: tierStyle(tier.height).main }"></div>

                <!-- 档位头：sticky + 渐变背景 + 数字徽章 -->
                <div class="sticky top-0 z-[5] flex items-center justify-between pl-[14px] pr-[12px] py-[7px] border-b border-[#f0f0f0]"
                     :style="{ background: `linear-gradient(to right, ${tierStyle(tier.height).tint}, rgba(255,255,255,0.85) 85%)` }">
                    <div class="flex items-center gap-[8px]">
                        <div class="flex items-baseline gap-[2px] px-[8px] py-[2px] rounded-[4px] text-white shadow-[0_1px_2px_rgba(0,0,0,0.15)]"
                             :style="{ background: tierStyle(tier.height).main }">
                            <span class="text-[13px] font-bold tabular-nums leading-none">{{ tier.height }}</span>
                            <span class="text-[10px] font-semibold leading-none">板</span>
                        </div>
                        <span class="text-[11px] text-[#666] font-medium">{{ tier.number }} 只</span>
                    </div>
                </div>

                <!-- 股票行：2 列栅格 -->
                <div class="grid grid-cols-2">
                    <div v-for="(stock, idx) in tier.stocks" :key="stock.code"
                         @dblclick="openStockChart(stock.code, stock.name)"
                         :title="'双击查看 K 线'"
                         class="flex flex-col gap-[3px] pl-[14px] pr-[8px] py-[6px] cursor-pointer transition-colors min-w-0 border-b border-[#f5f5f5] hover:bg-[#fff5f5]"
                         :class="{ 'border-l border-[#f5f5f5]': idx % 2 === 1 }">
                        <div class="flex items-center gap-[8px] min-w-0">
                            <span class="text-[12px] text-[#666] font-mono tabular-nums shrink-0">{{ stock.code }}</span>
                            <span class="text-[14px] font-bold text-[#111] truncate">{{ stock.name }}</span>
                        </div>
                        <div v-if="stock.reasonAll"
                             class="text-[11px] text-[#94a3b8] leading-[1.4] truncate"
                             :title="stock.reasonAll">
                            {{ stock.reasonAll }}
                        </div>
                    </div>
                </div>
            </div>
        </div>
      </div>

    </div>
    <!-- /板块复盘 -->

    <!-- ============ 涨跌池复盘（5 池：连板/涨停/炸板/冲刺/跌停）============ -->
    <div v-else-if="activeTab === 'pools'" class="flex-1 flex flex-col overflow-hidden bg-white">
        <!-- 工具栏：5 池切换 + 名称/代码筛选 -->
        <div class="h-[44px] px-[14px] border-b border-[#f0f0f0] flex items-center justify-between bg-white shrink-0 gap-[12px]">
            <div class="flex bg-[#f5f5f5] rounded-[4px] p-[2px] shrink-0">
                <button v-for="p in POOL_DEFS" :key="p.key"
                        @click="activePoolKey = p.key"
                        class="text-[12px] px-[12px] py-[4px] rounded-[3px] font-semibold transition flex items-center gap-[4px]"
                        :class="activePoolKey === p.key
                                  ? 'bg-white shadow-sm'
                                  : 'text-[#666] hover:text-[#111]'"
                        :style="activePoolKey === p.key ? { color: poolColor(p.key) } : {}">
                    <span>{{ poolArrow(p.key) }}</span>
                    <span>{{ p.label }}</span>
                    <span v-if="poolsLoaded[p.key]" class="text-[11px] tabular-nums opacity-80">
                        {{ poolsLoaded[p.key].count }}
                    </span>
                </button>
            </div>

            <div class="flex items-center gap-[10px] shrink-0">
                <span v-if="activePool" class="text-[11px] text-[#999] tabular-nums">
                    <template v-if="poolFilter">{{ filteredPoolStocks.length }} / {{ activePool.count }}</template>
                    <template v-else>{{ activePool.count }} 只</template>
                </span>
                <span v-else-if="activePoolLoading" class="text-[11px] text-[#999]">加载中...</span>
                <div class="relative">
                    <input v-model="poolFilter"
                           type="text"
                           placeholder="筛选 名称 / 代码..."
                           class="bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] pl-[10px] pr-[26px] py-[4px] text-[12px] outline-none focus:border-[#ff6b6b] focus:bg-white w-[180px] transition placeholder:text-[#ccc]">
                    <button v-if="poolFilter"
                            @click="poolFilter = ''"
                            class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[16px] h-[16px] flex items-center justify-center rounded-full text-[#aaa] hover:text-[#666] hover:bg-[#f0f0f0] transition"
                            title="清空筛选">
                        <svg class="w-[10px] h-[10px]" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
                        </svg>
                    </button>
                </div>
            </div>
        </div>

        <!-- 明细表 -->
        <div class="flex-1 overflow-auto custom-scrollbar">
            <table class="w-full text-[12px]">
                <thead class="bg-[#fafafa] text-[#666] sticky top-0 z-[1]">
                    <tr class="border-b border-[#eee]">
                        <th class="text-left px-[14px] py-[8px] font-medium">股票</th>
                        <th v-if="colHas.price"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">现价</th>
                        <th class="text-right px-[10px] py-[8px] font-medium tabular-nums">涨跌幅</th>
                        <th v-if="colHas.turnover"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">换手</th>
                        <th v-if="activePool && activePool.key === 'continuous' && colHas.limitType"
                            class="text-center px-[10px] py-[8px] font-medium">涨停形态</th>
                        <th v-if="activePool && (activePool.key === 'limitUp' || activePool.key === 'continuous') && colHas.highDays"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">几天几板</th>
                        <th v-if="activePool && activePool.key === 'limitUp' && colHas.firstLimitTime"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">封板时间</th>
                        <th v-if="activePool && activePool.key === 'broken' && colHas.brokenTime"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">炸板时间</th>
                        <th v-if="activePool && (activePool.key === 'limitUp' || activePool.key === 'continuous') && colHas.orderAmount"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">封单额</th>
                        <th v-if="activePool && activePool.key === 'broken' && colHas.openNum"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">开板次数</th>
                        <th v-if="colHas.circulationValue"
                            class="text-right px-[10px] py-[8px] font-medium tabular-nums">流通市值</th>
                        <th v-if="colHas.reason"
                            class="text-left px-[12px] py-[8px] font-medium">题材</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-if="activePoolLoading && !activePool">
                        <td colspan="20" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</td>
                    </tr>
                    <tr v-else-if="!activePool || !activePool.stocks.length">
                        <td colspan="20" class="py-[60px] text-center text-[#aaa] text-[13px]">
                            {{ selectedDate }} {{ activePool ? '该池子无数据（可能非交易日）' : '点击上方按钮加载' }}
                        </td>
                    </tr>
                    <tr v-else-if="!filteredPoolStocks.length">
                        <td colspan="20" class="py-[60px] text-center text-[#aaa] text-[13px]">
                            未匹配到"{{ poolFilter }}"，
                            <button @click="poolFilter = ''" class="text-[#dc2626] hover:underline">清空筛选</button>
                        </td>
                    </tr>
                    <tr v-for="s in filteredPoolStocks" :key="s.code"
                        @dblclick="openStockChart(s.code, s.name)"
                        :title="'双击查看 K 线'"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] transition-colors cursor-pointer">
                        <td class="px-[14px] py-[8px]">
                            <div class="text-[14px] font-bold text-[#111] leading-tight truncate max-w-[140px]">{{ s.name }}</div>
                            <div class="text-[11px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums">{{ s.code }}</div>
                        </td>
                        <td v-if="colHas.price"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[13px] font-semibold text-[#111]">
                            {{ s.price != null ? s.price.toFixed(2) : '—' }}
                        </td>
                        <td class="px-[10px] py-[8px] text-right tabular-nums">
                            <span class="text-[13px] font-bold"
                                  :class="s.changePct == null
                                            ? 'text-[#999]'
                                            : (s.changePct >= 0 ? 'text-[#dc2626]' : 'text-[#059669]')">
                                {{ fmtPct(s.changePct) }}
                            </span>
                        </td>
                        <td v-if="colHas.turnover"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            {{ s.turnoverRate != null ? s.turnoverRate.toFixed(2) + '%' : '—' }}
                        </td>
                        <td v-if="activePool.key === 'continuous' && colHas.limitType"
                            class="px-[10px] py-[8px] text-center">
                            <span v-if="s.limitType"
                                  class="text-[11px] font-semibold px-[6px] py-[1px] rounded-sm bg-[#fff0f0] text-[#dc2626] border border-[#fecaca]">
                                {{ s.limitType }}
                            </span>
                            <span v-else class="text-[#bbb]">—</span>
                        </td>
                        <td v-if="(activePool.key === 'limitUp' || activePool.key === 'continuous') && colHas.highDays"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            <span v-if="s.highDays" class="font-semibold text-[#dc2626]">{{ s.highDays }}</span>
                            <span v-else class="text-[#bbb]">—</span>
                        </td>
                        <td v-if="activePool.key === 'limitUp' && colHas.firstLimitTime"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            {{ s.firstLimitTime || '—' }}
                        </td>
                        <td v-if="activePool.key === 'broken' && colHas.brokenTime"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            {{ s.brokenTime || s.lastLimitTime || '—' }}
                        </td>
                        <td v-if="(activePool.key === 'limitUp' || activePool.key === 'continuous') && colHas.orderAmount"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            {{ fmtMoney(s.orderAmount) }}
                        </td>
                        <td v-if="activePool.key === 'broken' && colHas.openNum"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            {{ s.openNum != null ? s.openNum : '—' }}
                        </td>
                        <td v-if="colHas.circulationValue"
                            class="px-[10px] py-[8px] text-right tabular-nums text-[12px] text-[#475569]">
                            {{ fmtMoney(s.circulationValue) }}
                        </td>
                        <td v-if="colHas.reason" class="px-[12px] py-[8px]">
                            <div class="flex flex-wrap gap-[4px] max-w-[260px]" :title="s.reason">
                                <span v-for="(c, i) in concepts(s.reason).slice(0, 4)" :key="i"
                                      class="text-[11px] text-[#475569] bg-[#f1f5f9] px-[6px] py-[1px] rounded-sm">
                                    {{ c }}
                                </span>
                                <span v-if="concepts(s.reason).length > 4"
                                      class="text-[11px] text-[#999] self-center">
                                    +{{ concepts(s.reason).length - 4 }}
                                </span>
                                <span v-if="!concepts(s.reason).length" class="text-[#bbb] text-[11px]">—</span>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>

  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }

/* sub-tab 拖拽反馈 */
.tab-drag-ghost {
    opacity: 0.4;
    background: #fff0f0 !important;
}
.tab-drag-chosen {
    cursor: grabbing !important;
}
</style>
