<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import draggable from 'vuedraggable'
import Sortable from 'sortablejs'
import { api } from '../api/client'
import { useSmartRefresh } from '../composables/useSmartRefresh'
import { openStockChart } from '../composables/useStockChart'

// ---------------- 数据状态 ----------------
const groups = ref([])
const selectedGroupId = ref(null)
const stocks = ref([])
const loading = ref(false)

// 新建分组（tab 栏）
const showNewGroupInput = ref(false)
const newGroupName = ref('')

// 双击 tab inline 重命名
const renamingGroupId = ref(null)
const renamingGroupName = ref('')
const renameInputRef = ref(null)

// 添加股票：搜索式
const addQuery = ref('')
const addResults = ref([])
const addDropdownOpen = ref(false)
const addSearching = ref(false)
const addBoxRef = ref(null)
let addDebounceTimer = null

// 搜索
const searchQuery = ref('')

// 排序：3 态循环（desc → asc → 默认原序）
// sortKey 支持: 'changePct' | 'speedPct' | 'profit' | null
const sortKey = ref(null)
const sortOrder = ref('desc')

// ============ 列 Schema（拖拽排序用）============
// 每列定义：宽度、对齐、字号/字重（完整 Tailwind class 字符串，便于 JIT 扫描）、颜色策略、可否排序
// sizeCls/weightCls/staticColorCls 必须写完整 class 字符串，不能动态拼接，否则 Tailwind JIT 生成不了 CSS
const COLUMN_META = {
    price:        { label: '最新价',    width: 80,  align: 'right',  sizeCls: 'text-[13px]', weightCls: 'font-bold',    colorBy: 'changePct', sortable: false },
    changePct:    { label: '涨幅',      width: 70,  align: 'right',  sizeCls: 'text-[13px]', weightCls: 'font-bold',    colorBy: 'changePct', sortable: true },
    speedPct:     { label: '涨速',      width: 70,  align: 'right',  sizeCls: 'text-[12px]', weightCls: 'font-medium',  colorBy: 'speedPct',  sortable: true },
    amplitude:    { label: '振幅',      width: 70,  align: 'right',  sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#475569]' },
    turnoverRate: { label: '换手率',    width: 70,  align: 'right',  sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#475569]' },
    amount:       { label: '成交额',    width: 90,  align: 'right',  sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#475569]' },
    marketCap:    { label: '市值',      width: 80,  align: 'right',  sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#475569]' },
    volRatio:     { label: '量比',      width: 60,  align: 'right',  sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#475569]' },
    industry:     { label: '所属板块',  width: 68,  align: 'left',   sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#475569]', truncate: true },
    addedPrice:   { label: '自选价',    width: 70,  align: 'right',  sizeCls: 'text-[13px]', weightCls: '',             staticColorCls: 'text-[#555555]' },
    profit:       { label: '自选收益',  width: 80,  align: 'right',  sizeCls: 'text-[13px]', weightCls: 'font-bold',    colorBy: 'profit',    sortable: true },
    addedAt:      { label: '自选时间',  width: 95,  align: 'center', sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#666666]' },
    days:         { label: '自选天数',  width: 70,  align: 'right',  sizeCls: 'text-[12px]', weightCls: '',             staticColorCls: 'text-[#666666]' },
}
const DEFAULT_COLUMN_ORDER = Object.keys(COLUMN_META)
const columnOrder = ref([...DEFAULT_COLUMN_ORDER])

// 编辑股票弹窗
const editingStock = ref(null)

// 通用确认弹窗：askConfirm({title, message, confirmText}) → Promise<boolean>
const confirmState = ref({ show: false, title: '', message: '', confirmText: '确定', _resolve: null })
function askConfirm({ title = '确认操作', message = '', confirmText = '确定' } = {}) {
    return new Promise(resolve => {
        confirmState.value = { show: true, title, message, confirmText, _resolve: resolve }
    })
}
function confirmOk() {
    const r = confirmState.value._resolve
    confirmState.value.show = false
    r?.(true)
}
function confirmCancel() {
    const r = confirmState.value._resolve
    confirmState.value.show = false
    r?.(false)
}

// 实时行情（code → quote）
const quotes = ref({})

// 分时 sparkline 数据（code → { preClose, prices }）
const sparklines = ref({})

// 判断某 code 是否已在当前分组（供搜索结果显示"已添加"状态）
function isInCurrentGroup(code) {
    return stocks.value.some(s => s.code === code)
}

// ---------------- 实时行情格式化 ----------------
function fmtPrice(v) {
    return v == null ? '—' : v.toFixed(2)
}
function fmtPercent(v, digits = 2) {
    if (v == null) return '—'
    return (v >= 0 ? '+' : '') + v.toFixed(digits) + '%'
}
function fmtVolume(lots) {
    if (lots == null || lots === 0) return '—'
    if (lots >= 1e8) return (lots / 1e8).toFixed(2) + '亿手'
    if (lots >= 1e4) return (lots / 1e4).toFixed(2) + '万手'
    return lots.toFixed(0) + '手'
}
function fmtMarketCap(yuan) {
    if (yuan == null || yuan === 0) return '—'
    if (yuan >= 1e12) return (yuan / 1e12).toFixed(2) + '万亿'
    if (yuan >= 1e8) return (yuan / 1e8).toFixed(0) + '亿'
    return (yuan / 1e4).toFixed(0) + '万'
}
// 成交额：比市值多 1 位小数，因为成交额数值跨度大且需要更高精度
function fmtAmount(yuan) {
    if (yuan == null || yuan === 0) return '—'
    if (yuan >= 1e12) return (yuan / 1e12).toFixed(2) + '万亿'
    if (yuan >= 1e8) return (yuan / 1e8).toFixed(2) + '亿'
    if (yuan >= 1e4) return (yuan / 1e4).toFixed(0) + '万'
    return yuan.toFixed(0)
}
function fmtRatio(v) {
    return v == null ? '—' : v.toFixed(2)
}

// 上下色：v>0 红, v<0 绿, 0或空 灰
function colorClassOf(v) {
    if (v == null) return 'text-[#aaa]'
    if (v > 0) return 'text-[#dc2626]'
    if (v < 0) return 'text-[#059669]'
    return 'text-[#666]'
}

// 自选收益 %（当前价 vs 自选价）
function computeProfit(stock) {
    const q = quotes.value[stock.code]
    if (!q?.price || !stock.added_price) return null
    return (q.price - stock.added_price) / stock.added_price * 100
}

// 批量拉当前分组股票的实时行情
async function refreshQuotes() {
    if (!stocks.value.length) { quotes.value = {}; return }
    const codes = stocks.value.map(s => s.code)
    const res = await api.getBatchQuotes(codes)
    if (res.ok) quotes.value = res.data || {}
}

// 批量拉 sparkline —— 渐进式加载（小批 + jitter 间隔）
// 反爬策略：模拟"用户慢慢浏览"，避免一次性并发 N 个请求被识别为机器扫表
let _sparklineLoadToken = 0  // 切换分组时废弃未完成的上一轮加载
async function refreshSparklines() {
    if (!stocks.value.length) { sparklines.value = {}; return }
    const myToken = ++_sparklineLoadToken
    const codes = stocks.value.map(s => s.code)

    const BATCH_SIZE = 3                     // 每批最多 3 只
    const MIN_GAP_MS = 700, MAX_GAP_MS = 1300 // 批间隔 jitter 700-1300ms

    // 合并进现有数据，避免清空造成已有图先消失再出现的闪烁
    const merged = { ...sparklines.value }

    for (let i = 0; i < codes.length; i += BATCH_SIZE) {
        if (myToken !== _sparklineLoadToken) return  // 已被新一轮抢占，本轮弃
        const batch = codes.slice(i, i + BATCH_SIZE)
        const res = await api.getBatchSparklines(batch)
        if (myToken !== _sparklineLoadToken) return
        if (res.ok && res.data) {
            Object.assign(merged, res.data)
            // 增量更新视图 → 用户看到"逐批出现"的自然感
            sparklines.value = { ...merged }
        }
        // 不是最后一批就 jitter sleep
        if (i + BATCH_SIZE < codes.length) {
            const gap = MIN_GAP_MS + Math.random() * (MAX_GAP_MS - MIN_GAP_MS)
            await new Promise(r => setTimeout(r, gap))
        }
    }
}

// ---------- sparkline SVG 计算 ----------
const SPARK_W = 140
const SPARK_H = 56

/**
 * 计算 sparkline 的 Y 轴范围（含价格线、均价线、昨收基线）。
 * 策略：用实际 min-max（非对称）+ 8% padding，让数据撑满画布；
 * 同时把昨收 baseline 包含在内，保证基线一定可见。
 * 平盘日给最小 0.5% 显示幅度，避免完全直线。
 */
function computeYRange(sp) {
    if (!sp || sp.preClose == null) return null
    const baseline = sp.preClose
    let yMin = baseline, yMax = baseline
    for (const p of (sp.prices || [])) {
        if (p < yMin) yMin = p
        if (p > yMax) yMax = p
    }
    for (const p of (sp.avgPrices || [])) {
        if (p < yMin) yMin = p
        if (p > yMax) yMax = p
    }
    // 平盘最小幅度保护：避免完全直线
    const minRange = baseline * 0.005
    if (yMax - yMin < minRange) {
        const center = (yMin + yMax) / 2
        yMin = center - minRange / 2
        yMax = center + minRange / 2
    }
    // Padding：上下各留 8%，避免高低点贴边
    const padding = (yMax - yMin) * 0.08
    return { yMin: yMin - padding, yMax: yMax + padding }
}

function yToSvg(value, range) {
    return SPARK_H - ((value - range.yMin) / (range.yMax - range.yMin)) * SPARK_H
}

function sparkPath(sp, key = 'prices') {
    if (!sp || !sp[key] || !sp[key].length) return ''
    const range = computeYRange(sp)
    if (!range) return ''
    const arr = sp[key]
    const n = arr.length
    let path = ''
    for (let i = 0; i < n; i++) {
        const x = n > 1 ? (i / (n - 1)) * SPARK_W : SPARK_W / 2
        const y = yToSvg(arr[i], range)
        path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' '
    }
    return path
}

/** 昨收基线在 SVG 中的 Y 坐标（动态，因为 Y 轴范围不再以 baseline 为中心）*/
function baselineY(sp) {
    if (!sp || sp.preClose == null) return SPARK_H / 2
    const range = computeYRange(sp)
    return range ? yToSvg(sp.preClose, range) : SPARK_H / 2
}

/**
 * 生成"价格线下方填充区"的 SVG path：
 * 从价格线终点垂直降到画布底，再水平回到起点底，闭合。
 * 配合 linearGradient 实现"山脉图"渐变填充效果。
 */
function sparkAreaPath(sp) {
    if (!sp || !sp.prices || !sp.prices.length) return ''
    const range = computeYRange(sp)
    if (!range) return ''
    const arr = sp.prices
    const n = arr.length
    let path = ''
    for (let i = 0; i < n; i++) {
        const x = n > 1 ? (i / (n - 1)) * SPARK_W : SPARK_W / 2
        const y = yToSvg(arr[i], range)
        path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' '
    }
    // 闭合到画布底部
    const lastX = n > 1 ? SPARK_W : SPARK_W / 2
    path += `L${lastX.toFixed(1)},${SPARK_H} L0,${SPARK_H} Z`
    return path
}

// 颜色：末价 > 昨收 → 红；< → 绿；= → 灰
function sparkColor(sp) {
    if (!sp || !sp.prices || !sp.prices.length || sp.preClose == null) return '#cbd5e1'
    const last = sp.prices[sp.prices.length - 1]
    if (last > sp.preClose) return '#dc2626'
    if (last < sp.preClose) return '#059669'
    return '#94a3b8'
}

// 末端三角标记（色弱友好：红/绿看不清时靠形状判断）
// ▲ 涨 / ▼ 跌 / ◆ 平
function endMarkerPath(sp) {
    if (!sp || !sp.prices || !sp.prices.length || sp.preClose == null) return ''
    const range = computeYRange(sp)
    if (!range) return ''
    const baseline = sp.preClose
    const last = sp.prices[sp.prices.length - 1]
    const x = SPARK_W
    const y = yToSvg(last, range)
    if (last > baseline) {
        // ▲ 向上三角
        return `M${x - 2.8},${y + 2.2} L${x},${y - 2.8} L${x + 2.8},${y + 2.2} Z`
    }
    if (last < baseline) {
        // ▼ 向下三角
        return `M${x - 2.8},${y - 2.2} L${x},${y + 2.8} L${x + 2.8},${y - 2.2} Z`
    }
    // ◆ 平盘菱形
    return `M${x},${y - 2.2} L${x + 2.2},${y} L${x},${y + 2.2} L${x - 2.2},${y} Z`
}

// 管理分组 modal
const showManageModal = ref(false)
const managingGroups = ref([])               // draggable 的本地副本
const managingEditingId = ref(null)          // modal 里正在改名的 group id
const managingEditingName = ref('')
const managingNewGroupName = ref('')
const managingEditInputRef = ref(null)

// ---------------- 计算 ----------------
const selectedGroup = computed(() =>
    groups.value.find(g => g.id === selectedGroupId.value) || null
)

const filteredStocks = computed(() => {
    // ① 关键字过滤
    const q = searchQuery.value.trim().toLowerCase()
    let list = q
        ? stocks.value.filter(s =>
            (s.name && s.name.toLowerCase().includes(q)) || (s.code && s.code.includes(q))
          )
        : stocks.value

    // ② 按选中列排序（未排序时保持原顺序）
    if (sortKey.value) {
        const key = sortKey.value
        const getVal = (stock) => {
            if (key === 'profit') return computeProfit(stock)
            const quote = quotes.value[stock.code]
            return quote ? quote[key] : null
        }
        const dir = sortOrder.value === 'asc' ? 1 : -1
        list = [...list].sort((a, b) => {
            const av = getVal(a), bv = getVal(b)
            // 空值永远排最后（不论升序降序），避免"排序后顶部都是 —"
            if (av == null && bv == null) return 0
            if (av == null) return 1
            if (bv == null) return -1
            return (av - bv) * dir
        })
    }
    return list
})

// 表头点击：3 态循环 desc → asc → 取消
function handleSort(key) {
    if (sortKey.value === key) {
        if (sortOrder.value === 'desc') {
            sortOrder.value = 'asc'
        } else {
            sortKey.value = null
            sortOrder.value = 'desc'
        }
    } else {
        sortKey.value = key
        sortOrder.value = 'desc'
    }
}

// 当前列的排序方向（'desc' / 'asc' / null 表示未排序）
function sortDirFor(key) {
    return sortKey.value === key ? sortOrder.value : null
}

// ============ 列渲染 ============
const ALIGN_CLASS  = { right: 'text-right', center: 'text-center', left: 'text-left' }

/** 根据列 key 返回单元格内容（text）、额外 class、title 等 */
function getCellRender(key, stock) {
    const q = quotes.value[stock.code] || {}
    const def = COLUMN_META[key]
    let text = '—', extraClass = '', title = ''
    switch (key) {
        case 'price':        text = fmtPrice(q.price); break
        case 'changePct':    text = fmtPercent(q.changePct); break
        case 'speedPct':     text = fmtPercent(q.speedPct); break
        case 'amplitude':    text = fmtPercent(q.amplitude); break
        case 'turnoverRate': text = fmtPercent(q.turnoverRate); break
        case 'amount':       text = fmtAmount(q.amount); break
        case 'marketCap':    text = fmtMarketCap(q.marketCap); break
        case 'volRatio':     text = fmtRatio(q.volRatio); break
        case 'industry':     text = q.industry || '—'; title = q.industry || ''; extraClass = 'truncate max-w-[100px]'; break
        case 'addedPrice':   text = stock.added_price != null ? stock.added_price.toFixed(2) : '—'; break
        case 'profit':       text = fmtPercent(computeProfit(stock)); break
        case 'addedAt':      text = formatAddedDate(stock.added_at); break
        case 'days': {
            const d = daysSinceAdded(stock.added_at)
            if (d == null) text = '—'
            else if (d === 0) { text = '今日'; extraClass = 'text-[#dc2626] font-semibold' }
            else text = d + '天'
            break
        }
    }
    // 动态颜色（从 quote 字段取值决定红绿）
    let colorClass = ''
    if (def.colorBy === 'profit') {
        colorClass = colorClassOf(computeProfit(stock))
    } else if (def.colorBy) {
        colorClass = colorClassOf(q[def.colorBy])
    }
    // 静态颜色（仅当没有动态颜色时生效）
    if (!colorClass && def.staticColorCls) {
        colorClass = def.staticColorCls
    }
    return { text, extraClass, title, colorClass }
}

function getCellClasses(key, stock) {
    const def = COLUMN_META[key]
    const r = getCellRender(key, stock)
    return [
        'px-[10px]', 'py-[8px]',
        'tabular-nums',
        def.sizeCls,
        def.weightCls,
        ALIGN_CLASS[def.align],
        r.colorClass,
        r.extraClass,
    ].filter(Boolean).join(' ')
}

// ---------------- 工具函数 ----------------
function marketPrefix(code) {
    if (!code) return ''
    if (code.startsWith('6')) return 'SH'
    if (code.startsWith('300') || code.startsWith('301')) return 'SZ'
    if (/^00[0-3]/.test(code)) return 'SZ'
    if (/^(4|8|9|920)/.test(code)) return 'BJ'
    return ''
}

function daysSinceAdded(addedAt) {
    if (!addedAt) return null
    const iso = typeof addedAt === 'string' ? addedAt.replace(' ', 'T') : addedAt
    const added = new Date(iso)
    if (isNaN(added.getTime())) return null
    const days = Math.floor((Date.now() - added.getTime()) / 86400000)
    return Math.max(days, 0)
}

function formatAddedDate(addedAt) {
    if (!addedAt) return '—'
    // 支持 'YYYY-MM-DD HH:mm:ss' 或 'YYYY-MM-DD'，统一取前 10 位转成 YYYY/MM/DD
    return addedAt.slice(0, 10).replaceAll('-', '/')
}

// ---------------- 分组加载 ----------------
async function loadGroups() {
    const res = await api.getWatchlistGroups()
    if (res.ok) {
        groups.value = res.data || []
        if (selectedGroupId.value != null &&
            !groups.value.find(g => g.id === selectedGroupId.value)) {
            selectedGroupId.value = groups.value[0]?.id ?? null
        }
        if (selectedGroupId.value == null && groups.value.length > 0) {
            selectedGroupId.value = groups.value[0].id
        }
    }
}

async function selectGroup(groupId) {
    if (selectedGroupId.value === groupId) return
    selectedGroupId.value = groupId
    searchQuery.value = ''
    scrollSelectedTabIntoView()
    await loadStocks()
}

// 让当前选中的 tab 平滑滚动进视口（分组多时，切换/新建后自动跟过去）
async function scrollSelectedTabIntoView() {
    await nextTick()
    const gid = selectedGroupId.value
    if (gid == null) return
    const el = document.querySelector(`[data-group-id="${gid}"]`)
    if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' })
    }
}

async function loadStocks() {
    if (selectedGroupId.value == null) { stocks.value = []; return }
    loading.value = true
    try {
        const res = await api.getWatchlistStocks(selectedGroupId.value)
        if (res.ok) stocks.value = res.data || []
    } finally {
        loading.value = false
    }
}

// ---------------- Tab 栏：新建 + 快速重命名 ----------------
async function handleCreateGroup() {
    const name = newGroupName.value.trim()
    if (!name) return
    const res = await api.createWatchlistGroup(name)
    if (res.ok) {
        newGroupName.value = ''
        showNewGroupInput.value = false
        await loadGroups()
        if (res.data?.id) {
            await selectGroup(res.data.id)
            // 新建的分组在列表末尾，滚动到视口（selectGroup 已触发，但再显式确保一次）
            scrollSelectedTabIntoView()
        }
    }
}

async function startRenameGroup(group) {
    renamingGroupId.value = group.id
    renamingGroupName.value = group.name
    await nextTick()
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
}

async function saveRenameGroup() {
    const newName = renamingGroupName.value.trim()
    const gid = renamingGroupId.value
    if (!gid) return
    const orig = groups.value.find(g => g.id === gid)
    if (!newName || newName === orig?.name) {
        cancelRenameGroup()
        return
    }
    const res = await api.renameWatchlistGroup(gid, newName)
    if (res.ok) await loadGroups()
    cancelRenameGroup()
}

function cancelRenameGroup() {
    renamingGroupId.value = null
    renamingGroupName.value = ''
}

// ---------------- 管理分组 Modal ----------------
async function openManageModal() {
    // 深拷贝 groups 给 modal 用，避免拖拽直接改主 state
    managingGroups.value = JSON.parse(JSON.stringify(groups.value))
    showManageModal.value = true
}

function closeManageModal() {
    showManageModal.value = false
    managingEditingId.value = null
    managingEditingName.value = ''
    managingNewGroupName.value = ''
}

// 拖拽结束后保存新顺序到后端
async function onManageDragEnd() {
    const orderedIds = managingGroups.value.map(g => g.id)
    const res = await api.reorderWatchlistGroups(orderedIds)
    if (res.ok) await loadGroups()
}

// Modal 内重命名
async function startManageEdit(group) {
    managingEditingId.value = group.id
    managingEditingName.value = group.name
    await nextTick()
    managingEditInputRef.value?.focus()
    managingEditInputRef.value?.select()
}

async function saveManageEdit() {
    const newName = managingEditingName.value.trim()
    const gid = managingEditingId.value
    if (!gid) return
    const orig = managingGroups.value.find(g => g.id === gid)
    if (!newName || newName === orig?.name) {
        cancelManageEdit()
        return
    }
    const res = await api.renameWatchlistGroup(gid, newName)
    if (res.ok) {
        // 同步更新本地 state 和主 groups
        if (orig) orig.name = newName
        await loadGroups()
    }
    cancelManageEdit()
}

function cancelManageEdit() {
    managingEditingId.value = null
    managingEditingName.value = ''
}

// Modal 内删除
async function manageDeleteGroup(group) {
    const ok = await askConfirm({
        title: '删除分组',
        message: `删除分组「${group.name}」将会一并移除组内的所有股票，此操作不可恢复。`,
        confirmText: '删除分组',
    })
    if (!ok) return
    const res = await api.deleteWatchlistGroup(group.id)
    if (res.ok) {
        managingGroups.value = managingGroups.value.filter(g => g.id !== group.id)
        await loadGroups()
        await loadStocks()
    }
}

// Modal 内新建
async function manageCreateGroup() {
    const name = managingNewGroupName.value.trim()
    if (!name) return
    const res = await api.createWatchlistGroup(name)
    if (res.ok) {
        managingNewGroupName.value = ''
        await loadGroups()
        // 同步到 modal 本地副本
        managingGroups.value = JSON.parse(JSON.stringify(groups.value))
    }
}

// ---------------- 股票搜索 & 添加 ----------------
function onAddInput() {
    addDropdownOpen.value = true
    if (addDebounceTimer) clearTimeout(addDebounceTimer)
    const q = addQuery.value.trim()
    if (!q) { addResults.value = []; addSearching.value = false; return }
    addSearching.value = true
    addDebounceTimer = setTimeout(doSearch, 250)
}

async function doSearch() {
    const q = addQuery.value.trim()
    if (!q) { addResults.value = []; addSearching.value = false; return }
    const res = await api.searchStocks(q, 15)
    // 防止过期响应覆盖最新查询
    if (q !== addQuery.value.trim()) return
    addResults.value = res.ok ? (res.data || []) : []
    addSearching.value = false
}

async function handleAddFromSearch(stock) {
    if (selectedGroupId.value == null) return
    if (isInCurrentGroup(stock.code)) return
    const res = await api.addWatchlistStock(
        selectedGroupId.value, stock.code, stock.name, null, ''
    )
    if (res.ok) {
        await Promise.all([loadGroups(), loadStocks()])
        // 不清搜索，让用户可以继续加；只更新列表以触发"已添加"状态
    }
}

function closeAddDropdown() {
    addDropdownOpen.value = false
}
function clearAddSearch() {
    addQuery.value = ''
    addResults.value = []
    addDropdownOpen.value = false
    addSearching.value = false
}

async function handleRemoveStock(stock) {
    const ok = await askConfirm({
        title: '移除自选股',
        message: `将 ${stock.name || stock.code} 从当前分组移除？`,
        confirmText: '移除',
    })
    if (!ok) return
    const res = await api.removeWatchlistStock(selectedGroupId.value, stock.code)
    if (res.ok) await Promise.all([loadGroups(), loadStocks()])
}

function startEdit(stock) {
    // added_at 可能是 ISO 或空 → 截取 YYYY-MM-DD 给 <input type="date"> 用
    let addedAtDate = ''
    if (stock.added_at) {
        addedAtDate = stock.added_at.slice(0, 10)  // 取日期部分
    }
    editingStock.value = {
        code: stock.code,
        name: stock.name || '',
        added_price: stock.added_price ?? '',
        added_at: addedAtDate,
        remark: stock.remark || '',
    }
}

async function saveEdit() {
    const e = editingStock.value
    if (!e) return
    const price = parseFloat(e.added_price)
    // added_at: 'YYYY-MM-DD' 转 'YYYY-MM-DD 00:00:00' 免得时间字段丢
    const addedAt = e.added_at ? `${e.added_at} 09:30:00` : null
    const res = await api.updateWatchlistStock(selectedGroupId.value, e.code, {
        name: e.name,
        addedPrice: isNaN(price) ? null : price,
        remark: e.remark,
        addedAt: addedAt,
    })
    if (res.ok) {
        editingStock.value = null
        await loadStocks()
    }
}

function cancelEdit() {
    editingStock.value = null
}

// ============ 价格警报 ============
const alertModalStock = ref(null)        // 当前打开警报设置弹窗的 stock
const alertModalAbove = ref('')
const alertModalBelow = ref('')
const alertModalSaving = ref(false)

function hasAlert(stock) {
    return (stock.alert_above != null && stock.alert_above > 0) ||
           (stock.alert_below != null && stock.alert_below > 0)
}

function alertTooltip(stock) {
    if (!hasAlert(stock)) return '设置价格警报'
    const parts = []
    if (stock.alert_above != null) parts.push(`≥ ${Number(stock.alert_above).toFixed(2)}`)
    if (stock.alert_below != null) parts.push(`≤ ${Number(stock.alert_below).toFixed(2)}`)
    return `已设警报：${parts.join(' / ')}（点击修改）`
}

function openAlertModal(stock) {
    alertModalStock.value = stock
    // 默认填当前已设值，没设则用现价 ±5%
    const curPrice = parseFloat(quotes.value[stock.code]?.price || stock.added_price || 0) || 0
    alertModalAbove.value = stock.alert_above != null
        ? String(stock.alert_above)
        : (curPrice ? (curPrice * 1.05).toFixed(2) : '')
    alertModalBelow.value = stock.alert_below != null
        ? String(stock.alert_below)
        : (curPrice ? (curPrice * 0.95).toFixed(2) : '')
}

function closeAlertModal() {
    alertModalStock.value = null
    alertModalAbove.value = ''
    alertModalBelow.value = ''
}

async function saveAlert() {
    if (!alertModalStock.value) return
    alertModalSaving.value = true
    try {
        const above = parseFloat(alertModalAbove.value) || null
        const below = parseFloat(alertModalBelow.value) || null
        const code = alertModalStock.value.code
        const res = await api.setStockAlert(code, above, below)
        if (!res.ok) {
            console.warn('设警报失败', res)
            return
        }
        // 本地更新（避免等下次刷新才看到 🔔 图标变化）
        // 找到该 code 在 stocks 列表里的所有副本，更新 alert 字段
        for (const s of stocks.value) {
            if (s.code === code) {
                s.alert_above = above
                s.alert_below = below
            }
        }
        closeAlertModal()
    } finally {
        alertModalSaving.value = false
    }
}

async function clearAlertFromModal() {
    if (!alertModalStock.value) return
    const code = alertModalStock.value.code
    await api.clearStockAlert(code)
    for (const s of stocks.value) {
        if (s.code === code) {
            s.alert_above = null
            s.alert_below = null
        }
    }
    closeAlertModal()
}

// 点击搜索框外部时自动关闭下拉
function onDocumentClick(e) {
    if (addBoxRef.value && !addBoxRef.value.contains(e.target)) {
        addDropdownOpen.value = false
    }
}

// 股票列表一变就刷一次行情 + 分时（切分组、加/减股票都触发）
watch(stocks, () => {
    refreshQuotes()
    refreshSparklines()
}, { deep: false })

// 智能刷新：行情基础 10s；分时基础 60s。
// 窗口隐藏 / 5 分钟内活跃度低时自动降频，盯盘模式下强制保持节奏。
useSmartRefresh(refreshQuotes,     { baseInterval: 10_000, immediate: false })
useSmartRefresh(refreshSparklines, { baseInterval: 60_000, immediate: false })


// ============ 列顺序持久化 ============
async function loadColumnOrder() {
    const res = await api.getUserPreference('watchlist_column_order')
    if (res.ok && Array.isArray(res.data) && res.data.length) {
        // 校验：只保留有效 key，再把新增列追加到末尾
        const validSet = new Set(DEFAULT_COLUMN_ORDER)
        const saved = res.data.filter(k => validSet.has(k))
        const missing = DEFAULT_COLUMN_ORDER.filter(k => !saved.includes(k))
        columnOrder.value = [...saved, ...missing]
    }
}

async function saveColumnOrder() {
    await api.setUserPreference('watchlist_column_order', columnOrder.value)
}

// 挂 Sortable 到表头行（只对带 .col-draggable class 的 th 生效）
let headerSortable = null
function initHeaderSortable() {
    const row = document.querySelector('.watchlist-header-row')
    if (!row) return
    if (headerSortable) headerSortable.destroy()
    headerSortable = Sortable.create(row, {
        animation: 200,
        filter: '.col-fixed',         // 固定列不可拖
        preventOnFilter: false,       // 点击固定列不阻止（允许排序点击）
        draggable: '.col-draggable',  // 仅拖拽可拖列
        ghostClass: 'drag-ghost-col',
        onEnd: async () => {
            // 读 DOM 实际顺序，更新 Vue state 并存盘
            const ths = row.querySelectorAll('th.col-draggable')
            columnOrder.value = Array.from(ths).map(th => th.dataset.colKey)
            await saveColumnOrder()
        },
    })
}

onMounted(async () => {
    document.addEventListener('click', onDocumentClick)
    await loadColumnOrder()
    await loadGroups()
    await loadStocks()
    await nextTick()
    initHeaderSortable()
})

onUnmounted(() => {
    document.removeEventListener('click', onDocumentClick)
    if (headerSortable) { headerSortable.destroy(); headerSortable = null }
})
</script>

<template>
  <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

    <!-- ============ Tab 栏 ============ -->
    <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0">
        <!-- 左侧：可横向滚动的 tabs 区（含 + 新建）-->
        <div class="flex-1 flex items-center gap-[2px] px-[12px] overflow-x-auto custom-scrollbar min-w-0">
        <div v-for="g in groups" :key="g.id"
             :data-group-id="g.id"
             class="flex items-center gap-[6px] px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0"
             :class="selectedGroupId === g.id
                ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'"
             @click="selectGroup(g.id)"
             @dblclick="startRenameGroup(g)"
             title="双击重命名">
            <span v-if="renamingGroupId !== g.id">{{ g.name }}</span>
            <input v-else ref="renameInputRef"
                   v-model="renamingGroupName"
                   @keyup.enter="saveRenameGroup"
                   @keyup.esc="cancelRenameGroup"
                   @blur="saveRenameGroup"
                   @click.stop
                   class="text-[13px] font-bold text-[#dc2626] bg-white border border-[#dc2626] rounded-[3px] px-[6px] py-0 w-[90px] outline-none">

            <span v-if="renamingGroupId !== g.id"
                  class="text-[10px] font-bold tabular-nums px-[5px] py-[1px] rounded-full"
                  :class="selectedGroupId === g.id ? 'bg-[#dc2626] text-white' : 'bg-[#e5e7eb] text-[#666]'">
                {{ g.count }}
            </span>
        </div>

        <!-- + 新建（在滚动区内，跟在分组后面）-->
        <div v-if="!showNewGroupInput"
             @click="showNewGroupInput = true"
             class="shrink-0 px-[10px] py-[8px] text-[13px] text-[#999] hover:text-[#dc2626] cursor-pointer transition whitespace-nowrap">
            + 新建
        </div>
        <div v-else class="shrink-0 flex items-center gap-[4px] px-[8px]">
            <input v-model="newGroupName"
                   @keyup.enter="handleCreateGroup"
                   @keyup.esc="showNewGroupInput = false; newGroupName = ''"
                   placeholder="分组名"
                   class="text-[12px] w-[100px] px-[8px] py-[4px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]"
                   autofocus>
            <button @click="handleCreateGroup"
                    class="text-[11px] font-bold text-white bg-[#dc2626] px-[8px] py-[4px] rounded-[4px] hover:bg-[#991b1b]">确定</button>
            <button @click="showNewGroupInput = false; newGroupName = ''"
                    class="text-[11px] text-[#999] hover:text-[#111] px-[6px]">×</button>
        </div>
        </div>
        <!-- /左侧可滚动 tabs 区 -->

        <!-- 右侧：固定「编辑分组」入口（带左边框分隔，永远可见）-->
        <div v-if="groups.length" class="shrink-0 border-l border-[#e5e5e5] pl-[4px] pr-[8px]">
            <button @click="openManageModal"
                    class="flex items-center gap-[4px] px-[10px] py-[6px] text-[12px] text-[#666] hover:text-[#dc2626] hover:bg-white rounded-[4px] transition"
                    title="编辑分组：排序、重命名、删除">
                <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                </svg>
                <span>编辑分组</span>
            </button>
        </div>
    </div>

    <!-- ============ 空态：还没有分组 ============ -->
    <div v-if="!groups.length" class="flex-1 flex flex-col items-center justify-center text-[#ccc] text-[14px] gap-[12px]">
        <svg class="w-[48px] h-[48px] text-[#e5e7eb]" viewBox="0 0 20 20" fill="currentColor">
            <path d="M11.48 3.499a.562.562 0 011.04 0l2.125 5.111a.563.563 0 00.475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 00-.182.557l1.285 5.385a.562.562 0 01-.84.61l-4.725-2.885a.562.562 0 00-.586 0l-4.725 2.885a.562.562 0 01-.84-.61l1.285-5.386a.562.562 0 00-.182-.557l-4.204-3.602a.562.562 0 01.321-.988l5.518-.442a.563.563 0 00.475-.345L11.48 3.5z"/>
        </svg>
        <div>还没有自选分组</div>
        <div class="text-[12px]">点击顶部 <span class="text-[#dc2626] font-semibold">+ 新建</span> 创建第一个分组</div>
    </div>

    <!-- ============ 工具栏 + 表格 ============ -->
    <template v-else>
        <div class="h-[44px] flex items-center justify-end px-[14px] border-b border-[#f0f0f0] bg-white shrink-0 gap-[10px]">
            <div class="flex items-center gap-[8px] shrink-0">
                <div class="relative">
                    <!-- leading 搜索图标 -->
                    <svg class="absolute left-[8px] top-1/2 -translate-y-1/2 w-[12px] h-[12px] text-[#94a3b8] pointer-events-none"
                         viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" />
                    </svg>
                    <input v-model="searchQuery"
                           type="text"
                           placeholder="筛选当前列表"
                           class="text-[12px] w-[170px] pl-[26px] pr-[24px] py-[4px] bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] focus:bg-white transition placeholder:text-[#bbb]">
                    <button v-if="searchQuery" @click="searchQuery = ''"
                            class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-[#aaa] hover:text-[#666] flex items-center justify-center">
                        <svg class="w-[9px] h-[9px]" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                        </svg>
                    </button>
                </div>
                <!-- 搜索并添加股票（支持代码/中文名/拼音）-->
                <div ref="addBoxRef" class="relative">
                    <div class="flex items-center relative">
                        <!-- leading 搜索图标（红主题：与红色边框呼应）-->
                        <svg class="absolute left-[8px] top-1/2 -translate-y-1/2 w-[12px] h-[12px] text-[#dc2626]/70 pointer-events-none"
                             viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" />
                        </svg>
                        <input v-model="addQuery"
                               @input="onAddInput"
                               @focus="addDropdownOpen = true; if (addQuery.trim() && !addResults.length) onAddInput()"
                               @keyup.esc="clearAddSearch"
                               type="text"
                               placeholder="搜索代码/名称/拼音 加入自选"
                               class="text-[12px] w-[230px] pl-[26px] pr-[24px] py-[4px] bg-white border border-[#dc2626]/40 rounded-[4px] outline-none focus:border-[#dc2626] focus:shadow-[0_0_0_2px_rgba(220,38,38,0.08)] transition placeholder:text-[#bbb]">
                        <button v-if="addQuery" @click="clearAddSearch"
                                class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-[#aaa] hover:text-[#666] flex items-center justify-center">
                            <svg class="w-[9px] h-[9px]" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                            </svg>
                        </button>
                    </div>
                    <!-- 搜索结果 dropdown -->
                    <div v-if="addDropdownOpen && addQuery.trim()"
                         class="absolute top-full right-0 mt-[4px] w-[320px] bg-white shadow-[0_4px_20px_rgba(0,0,0,0.12)] border border-[#e5e5e5] rounded-[6px] z-30 max-h-[360px] overflow-auto custom-scrollbar">
                        <div v-if="addSearching" class="py-[16px] text-center text-[12px] text-[#aaa]">搜索中...</div>
                        <div v-else-if="!addResults.length" class="py-[16px] text-center text-[12px] text-[#aaa]">
                            未找到 "{{ addQuery }}" 相关的 A 股
                        </div>
                        <div v-else>
                            <div v-for="s in addResults" :key="s.code"
                                 class="flex items-center justify-between px-[10px] py-[8px] hover:bg-[#fff5f5] border-b border-[#f5f5f5] last:border-b-0 transition-colors">
                                <div class="min-w-0 flex-1">
                                    <div class="flex items-center gap-[6px]">
                                        <span class="text-[13px] font-bold text-[#111]">{{ s.name }}</span>
                                        <span class="text-[10px] text-[#dc2626] bg-[#fff0f0] px-[4px] py-[1px] rounded-sm">{{ s.marketType }}</span>
                                    </div>
                                    <div class="text-[11px] text-[#999] font-mono mt-[1px] tabular-nums">
                                        {{ s.marketPrefix }}{{ s.code }}
                                        <span v-if="s.pinyin" class="ml-[6px] text-[#bbb]">{{ s.pinyin }}</span>
                                    </div>
                                </div>
                                <button v-if="isInCurrentGroup(s.code)"
                                        disabled
                                        class="text-[11px] text-[#aaa] bg-[#f5f5f5] px-[10px] py-[4px] rounded-[4px] shrink-0 cursor-not-allowed">已添加</button>
                                <button v-else
                                        @click="handleAddFromSearch(s)"
                                        class="text-[11px] font-bold text-white bg-[#dc2626] px-[10px] py-[4px] rounded-[4px] hover:bg-[#991b1b] shrink-0 transition">+ 添加</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 股票表格 -->
        <div class="flex-1 overflow-auto custom-scrollbar bg-white">
            <table class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[12px] text-[#888] z-10">
                    <tr class="watchlist-header-row">
                        <!-- 固定列：股票名称（始终最左）-->
                        <th class="col-fixed px-[12px] py-[10px] font-normal w-[120px]">股票名称</th>
                        <!-- 固定列：今日走势（紧随名称）-->
                        <th class="col-fixed px-[8px] py-[10px] font-normal text-center w-[160px]">今日走势</th>
                        <!-- 可拖拽列：13 列数据列 -->
                        <th v-for="key in columnOrder" :key="key"
                            :data-col-key="key"
                            class="col-draggable px-[10px] py-[10px] font-normal cursor-move select-none"
                            :class="[ALIGN_CLASS[COLUMN_META[key].align], COLUMN_META[key].sortable ? 'hover:text-[#dc2626] transition group' : '']"
                            :style="{ width: COLUMN_META[key].width + 'px' }"
                            @click="COLUMN_META[key].sortable ? handleSort(key) : null">
                            {{ COLUMN_META[key].label }}
                            <span v-if="COLUMN_META[key].sortable"
                                  class="ml-[2px] text-[9px] align-middle tabular-nums"
                                  :class="sortDirFor(key) ? 'text-[#dc2626]' : 'text-[#ccc] opacity-0 group-hover:opacity-100'">
                                {{ sortDirFor(key) === 'asc' ? '▲' : '▼' }}
                            </span>
                        </th>
                        <!-- 固定列：操作（始终最右）-->
                        <th class="col-fixed px-[10px] py-[10px] font-normal text-center w-[80px]">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-if="loading && !stocks.length">
                        <td colspan="16" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</td>
                    </tr>
                    <tr v-else-if="!stocks.length">
                        <td colspan="16" class="py-[80px] text-center text-[#aaa] text-[13px]">
                            分组「{{ selectedGroup?.name }}」还没有股票，点击右上角
                            <span class="text-[#dc2626] font-semibold">+ 添加股票</span>
                        </td>
                    </tr>
                    <tr v-else-if="!filteredStocks.length">
                        <td colspan="16" class="py-[60px] text-center text-[#aaa] text-[13px]">
                            未匹配到"{{ searchQuery }}"，
                            <button @click="searchQuery = ''" class="text-[#dc2626] hover:underline">清空搜索</button>
                        </td>
                    </tr>

                    <tr v-for="stock in filteredStocks" :key="stock.code"
                        @dblclick="openStockChart(stock.code, stock.name || quotes[stock.code]?.name, filteredStocks)"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] transition-colors group cursor-pointer"
                        title="双击查看 K 线 · 左侧列表可切换">

                        <td class="px-[12px] py-[8px] align-middle">
                            <div class="text-[14px] font-bold text-[#111] leading-tight truncate">{{ stock.name || quotes[stock.code]?.name || '—' }}</div>
                            <div class="text-[11px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums">
                                {{ marketPrefix(stock.code) }}{{ stock.code }}
                            </div>
                        </td>

                        <!-- 今日走势：迷你分时 SVG -->
                        <td class="px-[8px] py-[6px] align-middle">
                            <div class="flex items-center justify-center h-[56px]">
                                <svg v-if="sparklines[stock.code]?.prices?.length"
                                     viewBox="0 0 140 56" preserveAspectRatio="none"
                                     class="w-[140px] h-[56px]"
                                     style="overflow: visible">
                                    <!-- 渐变定义：上至 18% 不透明，下至 0%（山脉淡出效果）-->
                                    <defs>
                                        <linearGradient :id="'spark-fill-' + stock.code" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="0%" :stop-color="sparkColor(sparklines[stock.code])" stop-opacity="0.20"/>
                                            <stop offset="100%" :stop-color="sparkColor(sparklines[stock.code])" stop-opacity="0"/>
                                        </linearGradient>
                                    </defs>
                                    <!-- 渐变填充区（价格线下方淡红/淡绿的"山脉"）-->
                                    <path :d="sparkAreaPath(sparklines[stock.code])"
                                          :fill="'url(#spark-fill-' + stock.code + ')'"
                                          stroke="none"/>
                                    <!-- 昨收基线：较灰虚线（Y 动态，因为 Y 轴不再以基线为中心）-->
                                    <line x1="0" :y1="baselineY(sparklines[stock.code])"
                                          x2="140" :y2="baselineY(sparklines[stock.code])"
                                          stroke="#94a3b8" stroke-width="0.6" stroke-dasharray="2.5,2.5"
                                          opacity="0.7"
                                          vector-effect="non-scaling-stroke"/>
                                    <!-- 均价线：暗金黄实线 -->
                                    <path :d="sparkPath(sparklines[stock.code], 'avgPrices')"
                                          stroke="#ca8a04" stroke-width="1.2" fill="none"
                                          stroke-linejoin="round" stroke-linecap="round"
                                          vector-effect="non-scaling-stroke"/>
                                    <!-- 分时价格曲线：红/绿 实线 -->
                                    <path :d="sparkPath(sparklines[stock.code])"
                                          :stroke="sparkColor(sparklines[stock.code])"
                                          stroke-width="1.5" fill="none"
                                          stroke-linejoin="round" stroke-linecap="round"
                                          vector-effect="non-scaling-stroke"/>
                                    <!-- 末端三角：色弱辅助（形状编码涨跌方向）-->
                                    <path :d="endMarkerPath(sparklines[stock.code])"
                                          :fill="sparkColor(sparklines[stock.code])"
                                          stroke="white" stroke-width="0.5"
                                          vector-effect="non-scaling-stroke"/>
                                </svg>
                                <span v-else class="text-[11px] text-[#ccc]">—</span>
                            </div>
                        </td>

                        <!-- 数据列：按 columnOrder 顺序渲染（可拖拽重排）-->
                        <td v-for="key in columnOrder" :key="key"
                            :class="getCellClasses(key, stock)"
                            :title="getCellRender(key, stock).title || null">
                            {{ getCellRender(key, stock).text }}
                        </td>

                        <td class="px-[10px] py-[8px] text-center">
                            <div class="flex items-center justify-center gap-[8px]">
                                <!-- 警报：已设时常驻显示，未设时 hover 才显 -->
                                <button @click="openAlertModal(stock)"
                                        class="transition"
                                        :class="hasAlert(stock)
                                            ? 'text-[#dc2626]'
                                            : 'text-[#666] opacity-0 group-hover:opacity-100 hover:text-[#dc2626]'"
                                        :title="alertTooltip(stock)">
                                    <svg v-if="hasAlert(stock)" class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z"/>
                                    </svg>
                                    <svg v-else class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
                                        <path stroke-linecap="round" stroke-linejoin="round"
                                              d="M14.857 17.082A23.848 23.848 0 0018 16.5c-1.094-1.39-1.5-3.236-1.5-5V8a5.5 5.5 0 00-11 0v3.5c0 1.764-.406 3.61-1.5 5a23.848 23.848 0 003.143.582m7.857 0a24.297 24.297 0 01-7.857 0m7.857 0a3 3 0 11-7.857 0"/>
                                    </svg>
                                </button>
                                <button @click="startEdit(stock)"
                                        class="text-[#666] hover:text-[#2563eb] transition opacity-0 group-hover:opacity-100"
                                        title="编辑">
                                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                                    </svg>
                                </button>
                                <button @click="handleRemoveStock(stock)"
                                        class="text-[#666] hover:text-[#dc2626] transition opacity-0 group-hover:opacity-100"
                                        title="移除">
                                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/>
                                    </svg>
                                </button>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </template>

    <!-- ============ 编辑股票弹窗 ============ -->
    <div v-if="editingStock" class="fixed inset-0 bg-black/20 z-50 flex items-center justify-center"
         @click.self="cancelEdit">
        <div class="bg-white rounded-[6px] p-[20px] w-[360px] shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <div class="text-[14px] font-bold text-[#111] mb-[16px]">编辑股票 {{ editingStock.code }}</div>
            <div class="flex flex-col gap-[12px]">
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">名称</span>
                    <input v-model="editingStock.name"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                </label>
                <div class="flex gap-[10px]">
                    <label class="flex flex-col gap-[4px] flex-1">
                        <span class="text-[12px] text-[#666]">自选价</span>
                        <input v-model="editingStock.added_price" type="number" step="0.01" placeholder="如 1680.00"
                               class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                    </label>
                    <label class="flex flex-col gap-[4px] flex-1">
                        <span class="text-[12px] text-[#666]">自选日期</span>
                        <input v-model="editingStock.added_at" type="date"
                               class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                    </label>
                </div>
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">备注（可选）</span>
                    <textarea v-model="editingStock.remark" rows="2" placeholder="自选逻辑、仓位计划..."
                              class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] resize-none"></textarea>
                </label>
            </div>
            <div class="flex justify-end gap-[8px] mt-[16px]">
                <button @click="cancelEdit"
                        class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-[#f5f5f5]">取消</button>
                <button @click="saveEdit"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b]">保存</button>
            </div>
        </div>
    </div>

    <!-- ============ 价格警报弹窗 ============ -->
    <div v-if="alertModalStock" class="fixed inset-0 bg-black/30 z-50 flex items-center justify-center"
         @click.self="closeAlertModal">
        <div class="bg-white rounded-[10px] w-[380px] shadow-[0_10px_40px_rgba(0,0,0,0.20)] overflow-hidden">
            <div class="px-[20px] py-[14px] border-b border-[#f0f0f0]">
                <div class="text-[14px] font-bold text-[#111] flex items-center gap-[8px]">
                    <span>🔔</span>
                    <span>价格警报 · {{ alertModalStock.name || alertModalStock.code }}</span>
                </div>
                <div class="text-[11px] text-[#888] mt-[2px]">
                    现价 {{ quotes[alertModalStock.code]?.price ? Number(quotes[alertModalStock.code].price).toFixed(2) : '—' }}
                    · 触发后桌面通知 + 应用内提醒 · 同一警报 1 小时内不重复
                </div>
            </div>
            <div class="px-[20px] py-[16px] flex flex-col gap-[12px]">
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#dc2626] font-semibold">▲ 上涨警报：现价 ≥</span>
                    <input v-model="alertModalAbove" type="number" step="0.01"
                           placeholder="留空 = 不设上涨警报"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                </label>
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#059669] font-semibold">▼ 下跌警报：现价 ≤</span>
                    <input v-model="alertModalBelow" type="number" step="0.01"
                           placeholder="留空 = 不设下跌警报"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#059669] tabular-nums">
                </label>
            </div>
            <div class="flex items-center justify-end gap-[8px] px-[20px] py-[14px] bg-[#fafafa] border-t border-[#f0f0f0]">
                <button v-if="hasAlert(alertModalStock)"
                        @click="clearAlertFromModal"
                        class="text-[12px] mr-auto text-[#666] hover:text-[#dc2626] hover:underline">
                    清除警报
                </button>
                <button @click="closeAlertModal"
                        class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-white">
                    取消
                </button>
                <button @click="saveAlert" :disabled="alertModalSaving"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b] disabled:opacity-50">
                    {{ alertModalSaving ? '保存中...' : '保存' }}
                </button>
            </div>
        </div>
    </div>

    <!-- ============ 管理分组 Modal ============ -->
    <div v-if="showManageModal" class="fixed inset-0 bg-black/25 z-50 flex items-center justify-center"
         @click.self="closeManageModal">
        <div class="bg-white rounded-[8px] w-[420px] max-h-[80vh] flex flex-col shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <!-- Modal 头 -->
            <div class="px-[20px] py-[14px] border-b border-[#f0f0f0] flex items-center justify-between shrink-0">
                <div>
                    <div class="text-[14px] font-bold text-[#111]">编辑分组</div>
                    <div class="text-[11px] text-[#999] mt-[2px]">拖拽 ⇅ 调整顺序，点击铅笔重命名</div>
                </div>
                <button @click="closeManageModal"
                        class="text-[#999] hover:text-[#111] w-[24px] h-[24px] flex items-center justify-center rounded hover:bg-[#f5f5f5]">
                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                    </svg>
                </button>
            </div>

            <!-- 分组列表（可拖拽）-->
            <div class="flex-1 overflow-auto custom-scrollbar px-[12px] py-[8px]">
                <draggable v-if="managingGroups.length"
                           v-model="managingGroups"
                           item-key="id"
                           handle=".drag-handle"
                           ghost-class="drag-ghost"
                           @end="onManageDragEnd"
                           class="flex flex-col gap-[2px]">
                    <template #item="{ element: g }">
                        <div class="flex items-center gap-[10px] px-[8px] py-[8px] rounded-[4px] hover:bg-[#fafafa] transition">
                            <!-- 拖拽手柄 -->
                            <div class="drag-handle cursor-grab active:cursor-grabbing text-[#bbb] hover:text-[#666] transition shrink-0">
                                <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                    <circle cx="6" cy="5" r="1.5"/><circle cx="6" cy="10" r="1.5"/><circle cx="6" cy="15" r="1.5"/>
                                    <circle cx="14" cy="5" r="1.5"/><circle cx="14" cy="10" r="1.5"/><circle cx="14" cy="15" r="1.5"/>
                                </svg>
                            </div>

                            <!-- 名称（查看态 or 编辑态）-->
                            <div class="flex-1 min-w-0">
                                <div v-if="managingEditingId !== g.id" class="flex items-center gap-[8px]">
                                    <span class="text-[13px] font-semibold text-[#111] truncate">{{ g.name }}</span>
                                </div>
                                <input v-else ref="managingEditInputRef"
                                       v-model="managingEditingName"
                                       @keyup.enter="saveManageEdit"
                                       @keyup.esc="cancelManageEdit"
                                       @blur="saveManageEdit"
                                       class="text-[13px] font-semibold text-[#dc2626] bg-white border border-[#dc2626] rounded-[3px] px-[6px] py-[3px] w-full outline-none">
                            </div>

                            <!-- 动作按钮 -->
                            <div v-if="managingEditingId !== g.id" class="flex items-center gap-[4px] shrink-0">
                                <button @click="startManageEdit(g)"
                                        class="w-[26px] h-[26px] text-[#666] hover:text-[#2563eb] hover:bg-[#eff6ff] rounded flex items-center justify-center transition"
                                        title="重命名">
                                    <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                                    </svg>
                                </button>
                                <button @click="manageDeleteGroup(g)"
                                        class="w-[26px] h-[26px] text-[#666] hover:text-[#dc2626] hover:bg-[#fff0f0] rounded flex items-center justify-center transition"
                                        title="删除">
                                    <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                    </template>
                </draggable>
                <div v-else class="py-[40px] text-center text-[#ccc] text-[13px]">
                    点击下方 + 新建分组 开始
                </div>
            </div>

            <!-- 新建分组（底部）-->
            <div class="px-[20px] py-[14px] border-t border-[#f0f0f0] flex items-center gap-[8px] shrink-0">
                <input v-model="managingNewGroupName"
                       @keyup.enter="manageCreateGroup"
                       placeholder="新分组名称"
                       class="flex-1 text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                <button @click="manageCreateGroup"
                        :disabled="!managingNewGroupName.trim()"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                    + 新建
                </button>
                <button @click="closeManageModal"
                        class="text-[12px] text-[#666] border border-[#e5e5e5] px-[14px] py-[6px] rounded-[4px] hover:bg-[#f5f5f5] transition">
                    完成
                </button>
            </div>
        </div>
    </div>

    <!-- ============ 通用确认弹窗（删除等危险操作）============ -->
    <div v-if="confirmState.show"
         class="fixed inset-0 bg-black/25 z-[60] flex items-center justify-center"
         @click.self="confirmCancel"
         @keyup.esc="confirmCancel">
        <div class="bg-white rounded-[8px] w-[360px] shadow-[0_10px_40px_rgba(0,0,0,0.18)] overflow-hidden">
            <!-- 头部：警告图标 + 标题 -->
            <div class="flex items-start gap-[12px] px-[20px] pt-[20px] pb-[4px]">
                <div class="w-[36px] h-[36px] rounded-full bg-[#fef2f2] flex items-center justify-center shrink-0">
                    <!-- 警告三角（色弱友好：形状+色双编码）-->
                    <svg class="w-[18px] h-[18px] text-[#dc2626]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l6.518 11.59c.75 1.335-.213 2.98-1.742 2.98H3.48c-1.53 0-2.493-1.645-1.743-2.98L8.257 3.1zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                    </svg>
                </div>
                <div class="flex-1 min-w-0 pt-[4px]">
                    <div class="text-[15px] font-bold text-[#111] leading-tight">{{ confirmState.title }}</div>
                </div>
            </div>

            <!-- 正文 -->
            <div class="px-[20px] pl-[68px] py-[10px] text-[13px] text-[#555] leading-relaxed">
                {{ confirmState.message }}
            </div>

            <!-- 按钮区 -->
            <div class="flex justify-end gap-[8px] px-[20px] py-[14px] bg-[#fafafa] border-t border-[#f0f0f0]">
                <button @click="confirmCancel"
                        class="text-[12px] px-[16px] py-[7px] text-[#444] border border-[#d4d4d4] rounded-[4px] hover:bg-white hover:border-[#999] transition">
                    取消
                </button>
                <button @click="confirmOk"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[16px] py-[7px] rounded-[4px] hover:bg-[#991b1b] shadow-sm transition">
                    {{ confirmState.confirmText }}
                </button>
            </div>
        </div>
    </div>

  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }

/* 拖拽占位的视觉样式：拖起时原位显示一条淡红色轮廓 */
.drag-ghost {
    opacity: 0.4;
    background: #fff0f0 !important;
    border: 1px dashed #dc2626;
}

/* 列表头拖拽时的占位 th 样式 */
.drag-ghost-col {
    opacity: 0.4;
    background: #fff0f0 !important;
    color: #dc2626 !important;
}
</style>
