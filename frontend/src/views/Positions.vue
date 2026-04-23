<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import draggable from 'vuedraggable'
import { api } from '../api/client'

// ---------------- 账户 / 持仓状态 ----------------
const SUMMARY_ID = 'summary'  // 选中汇总 tab 时的哨兵值（真实账户 id 都是数字）

const accounts = ref([])
const selectedAccountId = ref(null)
const positions = ref([])
const loading = ref(false)

const isSummary = computed(() => selectedAccountId.value === SUMMARY_ID)

// 新建账户（tab 栏内联）
const showNewAccountInput = ref(false)
const newAccountName = ref('')

// 双击 tab 重命名
const renamingAccountId = ref(null)
const renamingAccountName = ref('')
const renameInputRef = ref(null)

// 搜索过滤当前列表
const searchQuery = ref('')

// 列排序：3 态循环（desc → asc → 清空），仅"当日盈亏"/"持有盈亏"可排
// sortKey: 'dailyProfit' | 'totalProfit' | null
const sortKey = ref(null)
const sortOrder = ref('desc')

// 实时行情
const quotes = ref({})
let quoteRefreshTimer = null

// ---------------- 添加持仓弹窗 ----------------
const showAddModal = ref(false)
const addForm = ref({ code: '', name: '', shares: '', costPrice: '', addedAt: '', remark: '' })
const addQuery = ref('')
const addResults = ref([])
const addSearching = ref(false)
let addDebounceTimer = null
const addFormError = ref('')

// ---------------- 编辑持仓弹窗 ----------------
const editingPosition = ref(null)
const editFormError = ref('')

// ---------------- 管理账户 modal ----------------
const showManageModal = ref(false)
const managingAccounts = ref([])
const managingEditingId = ref(null)
const managingEditingName = ref('')
const managingNewAccountName = ref('')
const managingEditInputRef = ref(null)

// ---------------- 通用确认弹窗 ----------------
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

// ---------------- 格式化辅助 ----------------
function fmtPrice(v) { return v == null ? '—' : v.toFixed(2) }
function fmtPercent(v, d = 2) { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d) + '%' }
// 持仓股数：原始数值，千分位，不简写
function fmtShares(n) {
    if (n == null) return '—'
    return n.toLocaleString()
}
// 金额（纯数值，如市值）：千分位，小数点后 2 位
function fmtAmount(v) {
    if (v == null) return '—'
    return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
// 带符号金额（盈亏类）：正数补 '+' 前缀，色弱用户靠符号辨识方向
function fmtSignedAmount(v) {
    if (v == null) return '—'
    const body = Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    if (v > 0) return '+' + body
    if (v < 0) return '-' + body
    return '0.00'
}
function colorClassOf(v) {
    if (v == null) return 'text-[#aaa]'
    if (v > 0) return 'text-[#dc2626]'
    if (v < 0) return 'text-[#059669]'
    return 'text-[#666]'
}
function formatAddedDate(s) {
    if (!s) return '—'
    return s.slice(0, 10).replaceAll('-', '/')
}
function marketPrefix(code) {
    if (!code) return ''
    if (code.startsWith('6')) return 'SH'
    if (code.startsWith('300') || code.startsWith('301')) return 'SZ'
    if (/^00[0-3]/.test(code)) return 'SZ'
    if (/^(4|8|9|920)/.test(code)) return 'BJ'
    return ''
}

// ---------------- 每行盈亏计算 ----------------
function marketValue(p) {
    const q = quotes.value[p.code]
    if (!q?.price || p.shares == null) return null
    return q.price * p.shares
}
function costValue(p) {
    if (p.cost_price == null || p.shares == null) return null
    return p.cost_price * p.shares
}
// 持有盈亏（累计）
function profitAmount(p) {
    const mv = marketValue(p), cv = costValue(p)
    if (mv == null || cv == null) return null
    return mv - cv
}
function profitPct(p) {
    const q = quotes.value[p.code]
    if (!q?.price || !p.cost_price) return null
    return (q.price - p.cost_price) / p.cost_price * 100
}
// 当日盈亏：(现价 - 昨收) × 持股 = 东财 f4 (changeVal) × 持股
function dailyProfitAmount(p) {
    const q = quotes.value[p.code]
    if (q?.changeVal == null || p.shares == null) return null
    return q.changeVal * p.shares
}
// 当日盈亏比就是该股票的日涨跌幅（与持仓成本无关）
function dailyProfitPct(p) {
    return quotes.value[p.code]?.changePct ?? null
}
// 仓位占比：该持仓市值 / 总市值（仅汇总视图有意义）
function positionWeight(p) {
    const mv = marketValue(p)
    const total = summaryStats.value.totalMV
    if (mv == null || !total) return null
    return mv / total * 100
}

// ---------------- 计算：筛选 ----------------
const selectedAccount = computed(() =>
    accounts.value.find(a => a.id === selectedAccountId.value) || null
)
const filteredPositions = computed(() => {
    // ① 关键字过滤
    const q = searchQuery.value.trim().toLowerCase()
    let list = q
        ? positions.value.filter(p =>
            (p.name && p.name.toLowerCase().includes(q)) || (p.code && p.code.includes(q))
          )
        : positions.value

    // ② 按盈亏金额排序（未排序时保持原顺序）
    if (sortKey.value) {
        const getVal = sortKey.value === 'dailyProfit' ? dailyProfitAmount : profitAmount
        const dir = sortOrder.value === 'asc' ? 1 : -1
        list = [...list].sort((a, b) => {
            const av = getVal(a), bv = getVal(b)
            if (av == null && bv == null) return 0
            if (av == null) return 1
            if (bv == null) return -1
            return (av - bv) * dir
        })
    }
    return list
})

// 当前可见持仓的涨跌统计（基于 filteredPositions + 实时行情）
// null 行情不计入，0 计入"平"
const tradingStats = computed(() => {
    let up = 0, down = 0, flat = 0
    for (const p of filteredPositions.value) {
        const pct = quotes.value[p.code]?.changePct
        if (pct == null) continue
        if (pct > 0) up++
        else if (pct < 0) down++
        else flat++
    }
    return { up, down, flat }
})

// 表头点击：3 态循环
function handleSort(key) {
    if (sortKey.value === key) {
        if (sortOrder.value === 'desc') sortOrder.value = 'asc'
        else { sortKey.value = null; sortOrder.value = 'desc' }
    } else {
        sortKey.value = key
        sortOrder.value = 'desc'
    }
}
function sortDirFor(key) {
    return sortKey.value === key ? sortOrder.value : null
}

// ---------------- 汇总统计（仅汇总 tab 用）----------------
// 依赖 positions（汇总时是合并后的列表）+ quotes（实时行情）
const summaryStats = computed(() => {
    let totalMV = 0, totalCV = 0, totalDailyPL = 0, totalPrevMV = 0
    let hasQuote = false
    for (const p of positions.value) {
        const cv = costValue(p)
        if (cv != null) totalCV += cv
        const mv = marketValue(p)
        if (mv != null) { totalMV += mv; hasQuote = true }
        const dp = dailyProfitAmount(p)
        if (dp != null) totalDailyPL += dp
        // 昨日市值（用于计算当日盈亏%）
        const q = quotes.value[p.code]
        if (q?.prevClose && p.shares != null) totalPrevMV += q.prevClose * p.shares
    }
    const totalProfit = hasQuote ? totalMV - totalCV : null
    const totalProfitPct = (hasQuote && totalCV > 0) ? (totalMV - totalCV) / totalCV * 100 : null
    const dailyPLPct = totalPrevMV > 0 ? totalDailyPL / totalPrevMV * 100 : null
    return {
        totalMV: hasQuote ? totalMV : null,
        totalCV: totalCV || null,
        totalProfit, totalProfitPct,
        totalDailyPL: hasQuote ? totalDailyPL : null,
        dailyPLPct,
    }
})

// ---------------- 数据加载 ----------------
async function loadAccounts() {
    const res = await api.getPortfolioAccounts()
    if (!res.ok) return
    accounts.value = res.data || []
    // 无账户时清空选中
    if (!accounts.value.length) { selectedAccountId.value = null; return }
    // 汇总 tab 始终有效
    if (selectedAccountId.value === SUMMARY_ID) return
    // 当前选中的账户仍存在 → 保持
    if (typeof selectedAccountId.value === 'number' &&
        accounts.value.find(a => a.id === selectedAccountId.value)) return
    // 否则默认进入汇总视图
    selectedAccountId.value = SUMMARY_ID
}

async function loadPositions() {
    if (selectedAccountId.value == null) { positions.value = []; return }
    loading.value = true
    try {
        const res = isSummary.value
            ? await api.getPortfolioMerged()
            : await api.getPortfolioPositions(selectedAccountId.value)
        if (res.ok) positions.value = res.data || []
    } finally {
        loading.value = false
    }
}

async function refreshQuotes() {
    if (!positions.value.length) { quotes.value = {}; return }
    const codes = positions.value.map(p => p.code)
    const res = await api.getBatchQuotes(codes)
    if (res.ok) quotes.value = res.data || {}
}

async function selectAccount(accountId) {
    if (selectedAccountId.value === accountId) return
    selectedAccountId.value = accountId
    searchQuery.value = ''
    scrollSelectedTabIntoView()
    await loadPositions()
}

async function scrollSelectedTabIntoView() {
    await nextTick()
    const aid = selectedAccountId.value
    if (aid == null) return
    const el = document.querySelector(`[data-account-id="${aid}"]`)
    if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' })
    }
}

// ---------------- 新建 + 重命名 Tab ----------------
async function handleCreateAccount() {
    const name = newAccountName.value.trim()
    if (!name) return
    const res = await api.createPortfolioAccount(name)
    if (res.ok) {
        newAccountName.value = ''
        showNewAccountInput.value = false
        await loadAccounts()
        if (res.data?.id) {
            await selectAccount(res.data.id)
            scrollSelectedTabIntoView()
        }
    }
}

async function startRenameAccount(account) {
    renamingAccountId.value = account.id
    renamingAccountName.value = account.name
    await nextTick()
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
}

async function saveRenameAccount() {
    const newName = renamingAccountName.value.trim()
    const aid = renamingAccountId.value
    if (!aid) return
    const orig = accounts.value.find(a => a.id === aid)
    if (!newName || newName === orig?.name) { cancelRenameAccount(); return }
    const res = await api.renamePortfolioAccount(aid, newName)
    if (res.ok) await loadAccounts()
    cancelRenameAccount()
}

function cancelRenameAccount() {
    renamingAccountId.value = null
    renamingAccountName.value = ''
}

// ---------------- 管理账户 Modal ----------------
async function openManageModal() {
    managingAccounts.value = JSON.parse(JSON.stringify(accounts.value))
    showManageModal.value = true
}

function closeManageModal() {
    showManageModal.value = false
    managingEditingId.value = null
    managingEditingName.value = ''
    managingNewAccountName.value = ''
}

async function onManageDragEnd() {
    const orderedIds = managingAccounts.value.map(a => a.id)
    const res = await api.reorderPortfolioAccounts(orderedIds)
    if (res.ok) await loadAccounts()
}

async function startManageEdit(account) {
    managingEditingId.value = account.id
    managingEditingName.value = account.name
    await nextTick()
    managingEditInputRef.value?.focus()
    managingEditInputRef.value?.select()
}

async function saveManageEdit() {
    const newName = managingEditingName.value.trim()
    const aid = managingEditingId.value
    if (!aid) return
    const orig = managingAccounts.value.find(a => a.id === aid)
    if (!newName || newName === orig?.name) { cancelManageEdit(); return }
    const res = await api.renamePortfolioAccount(aid, newName)
    if (res.ok) {
        if (orig) orig.name = newName
        await loadAccounts()
    }
    cancelManageEdit()
}

function cancelManageEdit() {
    managingEditingId.value = null
    managingEditingName.value = ''
}

async function manageDeleteAccount(account) {
    const ok = await askConfirm({
        title: '删除账户',
        message: `删除账户「${account.name}」将一并移除该账户下的所有持仓记录，此操作不可恢复。`,
        confirmText: '删除账户',
    })
    if (!ok) return
    const res = await api.deletePortfolioAccount(account.id)
    if (res.ok) {
        managingAccounts.value = managingAccounts.value.filter(a => a.id !== account.id)
        await loadAccounts()
        await loadPositions()
    }
}

async function manageCreateAccount() {
    const name = managingNewAccountName.value.trim()
    if (!name) return
    const res = await api.createPortfolioAccount(name)
    if (res.ok) {
        managingNewAccountName.value = ''
        await loadAccounts()
        managingAccounts.value = JSON.parse(JSON.stringify(accounts.value))
    }
}

// ---------------- 添加持仓 ----------------
function openAddModal() {
    // 汇总视图禁止新增（没有目标账户）
    if (selectedAccountId.value == null || isSummary.value) return
    addForm.value = { code: '', name: '', shares: '', costPrice: '', addedAt: todayStr(), remark: '' }
    addQuery.value = ''
    addResults.value = []
    addFormError.value = ''
    showAddModal.value = true
}

function todayStr() {
    const d = new Date()
    const p = (n) => n.toString().padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function closeAddModal() {
    showAddModal.value = false
}

function onAddSearchInput() {
    if (addDebounceTimer) clearTimeout(addDebounceTimer)
    const q = addQuery.value.trim()
    if (!q) { addResults.value = []; addSearching.value = false; return }
    addSearching.value = true
    addDebounceTimer = setTimeout(doAddSearch, 250)
}

async function doAddSearch() {
    const q = addQuery.value.trim()
    if (!q) { addResults.value = []; addSearching.value = false; return }
    const res = await api.searchStocks(q, 15)
    if (q !== addQuery.value.trim()) return
    addResults.value = res.ok ? (res.data || []) : []
    addSearching.value = false
}

function selectStockForAdd(stock) {
    addForm.value.code = stock.code
    addForm.value.name = stock.name
    addQuery.value = `${stock.name} ${stock.code}`
    addResults.value = []
}

function validateSharesInput(v) {
    const n = parseInt(v, 10)
    if (isNaN(n) || n <= 0) return '持股数量必须为正整数'
    if (n % 100 !== 0) return 'A 股必须为 100 股的整数倍'
    return null
}

async function submitAdd() {
    const f = addForm.value
    addFormError.value = ''
    if (!f.code) { addFormError.value = '请先搜索并选择股票'; return }
    const shareErr = validateSharesInput(f.shares)
    if (shareErr) { addFormError.value = shareErr; return }
    const price = parseFloat(f.costPrice)
    if (isNaN(price) || price <= 0) { addFormError.value = '成本价必须大于 0'; return }

    const res = await api.addPortfolioPosition(
        selectedAccountId.value, f.code, f.name, parseInt(f.shares, 10), price, f.remark
    )
    if (!res.ok) { addFormError.value = res.error || '添加失败'; return }
    // 如果用户指定了加仓日期，覆盖默认的 CURRENT_TIMESTAMP
    if (f.addedAt) {
        await api.updatePortfolioPosition(selectedAccountId.value, f.code,
            { addedAt: `${f.addedAt} 09:30:00` })
    }
    closeAddModal()
    await Promise.all([loadAccounts(), loadPositions()])
}

// ---------------- 编辑持仓 ----------------
function startEdit(position) {
    editingPosition.value = {
        code: position.code,
        name: position.name || '',
        shares: position.shares,
        costPrice: position.cost_price ?? '',
        addedAt: position.added_at ? position.added_at.slice(0, 10) : '',
        remark: position.remark || '',
    }
    editFormError.value = ''
}

function cancelEdit() {
    editingPosition.value = null
}

async function saveEdit() {
    const e = editingPosition.value
    if (!e) return
    editFormError.value = ''
    const shareErr = validateSharesInput(e.shares)
    if (shareErr) { editFormError.value = shareErr; return }
    const price = parseFloat(e.costPrice)
    if (isNaN(price) || price <= 0) { editFormError.value = '成本价必须大于 0'; return }

    const res = await api.updatePortfolioPosition(selectedAccountId.value, e.code, {
        name: e.name,
        shares: parseInt(e.shares, 10),
        costPrice: price,
        remark: e.remark,
        addedAt: e.addedAt ? `${e.addedAt} 09:30:00` : null,
    })
    if (!res.ok) { editFormError.value = res.error || '保存失败'; return }
    editingPosition.value = null
    await loadPositions()
}

async function handleRemovePosition(position) {
    const ok = await askConfirm({
        title: '删除持仓',
        message: `将 ${position.name || position.code} 从持仓中删除？此操作不可恢复。`,
        confirmText: '删除',
    })
    if (!ok) return
    const res = await api.removePortfolioPosition(selectedAccountId.value, position.code)
    if (res.ok) await Promise.all([loadAccounts(), loadPositions()])
}

// ---------------- 生命周期 ----------------
watch(positions, () => { refreshQuotes() }, { deep: false })

onMounted(async () => {
    await loadAccounts()
    await loadPositions()
    quoteRefreshTimer = setInterval(refreshQuotes, 30_000)
})

onUnmounted(() => {
    if (quoteRefreshTimer) clearInterval(quoteRefreshTimer)
})
</script>

<template>
  <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

    <!-- ============ Tab 栏：账户 ============ -->
    <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0">
        <div class="flex-1 flex items-center gap-[2px] px-[12px] overflow-x-auto custom-scrollbar min-w-0">
            <!-- 汇总虚拟 tab（跨账户聚合视图）-->
            <div :data-account-id="SUMMARY_ID"
                 class="shrink-0 flex items-center gap-[6px] px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2"
                 :class="isSummary
                    ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                    : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'"
                 @click="selectAccount(SUMMARY_ID)"
                 title="所有账户持仓聚合">
                <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z"/>
                </svg>
                <span>汇总</span>
            </div>
            <div class="shrink-0 w-px h-[18px] bg-[#e5e5e5] mx-[6px]"></div>

            <!-- 账户 tabs -->
            <div v-for="a in accounts" :key="a.id"
                 :data-account-id="a.id"
                 class="flex items-center gap-[6px] px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0"
                 :class="selectedAccountId === a.id
                    ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                    : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'"
                 @click="selectAccount(a.id)"
                 @dblclick="startRenameAccount(a)"
                 title="双击重命名">
                <span v-if="renamingAccountId !== a.id">{{ a.name }}</span>
                <input v-else ref="renameInputRef"
                       v-model="renamingAccountName"
                       @keyup.enter="saveRenameAccount"
                       @keyup.esc="cancelRenameAccount"
                       @blur="saveRenameAccount"
                       @click.stop
                       class="text-[13px] font-bold text-[#dc2626] bg-white border border-[#dc2626] rounded-[3px] px-[6px] py-0 w-[100px] outline-none">
                <span v-if="renamingAccountId !== a.id"
                      class="text-[10px] font-bold tabular-nums px-[5px] py-[1px] rounded-full"
                      :class="selectedAccountId === a.id ? 'bg-[#dc2626] text-white' : 'bg-[#e5e7eb] text-[#666]'">
                    {{ a.count }}
                </span>
            </div>

            <!-- + 新建账户 -->
            <div v-if="!showNewAccountInput"
                 @click="showNewAccountInput = true"
                 class="shrink-0 px-[10px] py-[8px] text-[13px] text-[#999] hover:text-[#dc2626] cursor-pointer transition whitespace-nowrap">
                + 新建账户
            </div>
            <div v-else class="shrink-0 flex items-center gap-[4px] px-[8px]">
                <input v-model="newAccountName"
                       @keyup.enter="handleCreateAccount"
                       @keyup.esc="showNewAccountInput = false; newAccountName = ''"
                       placeholder="账户名"
                       class="text-[12px] w-[110px] px-[8px] py-[4px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]"
                       autofocus>
                <button @click="handleCreateAccount"
                        class="text-[11px] font-bold text-white bg-[#dc2626] px-[8px] py-[4px] rounded-[4px] hover:bg-[#991b1b]">确定</button>
                <button @click="showNewAccountInput = false; newAccountName = ''"
                        class="text-[11px] text-[#999] hover:text-[#111] px-[6px]">×</button>
            </div>
        </div>

        <!-- 右侧：管理账户 -->
        <div v-if="accounts.length" class="shrink-0 border-l border-[#e5e5e5] pl-[4px] pr-[8px]">
            <button @click="openManageModal"
                    class="flex items-center gap-[4px] px-[10px] py-[6px] text-[12px] text-[#666] hover:text-[#dc2626] hover:bg-white rounded-[4px] transition"
                    title="管理账户：排序、重命名、删除">
                <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                </svg>
                <span>管理账户</span>
            </button>
        </div>
    </div>

    <!-- 空态：还没有账户 -->
    <div v-if="!accounts.length" class="flex-1 flex flex-col items-center justify-center text-[#ccc] text-[14px] gap-[12px]">
        <svg class="w-[48px] h-[48px] text-[#e5e7eb]" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10 2a1 1 0 00-1 1v1H6a2 2 0 00-2 2v2H3a1 1 0 100 2h1v2H3a1 1 0 100 2h1v2a2 2 0 002 2h8a2 2 0 002-2v-2h1a1 1 0 100-2h-1v-2h1a1 1 0 100-2h-1V6a2 2 0 00-2-2h-3V3a1 1 0 00-1-1h-0z"/>
        </svg>
        <div>还没有持仓账户</div>
        <div class="text-[12px]">点击顶部 <span class="text-[#dc2626] font-semibold">+ 新建账户</span> 创建第一个账户</div>
    </div>

    <!-- ============ 统计卡 + 工具栏 + 表格 ============ -->
    <template v-else>
        <!-- ============ 统计卡（紧贴 tab 栏，作为"头部总览"）============ -->
        <div v-if="positions.length"
             class="px-[14px] py-[12px] bg-white border-b border-[#f0f0f0] shrink-0">
            <div class="grid grid-cols-4 gap-[10px]">
                <!-- 总市值 -->
                <div class="bg-gradient-to-b from-[#fafafa] to-white border border-[#eeeeee] rounded-[6px] px-[14px] py-[10px]">
                    <div class="text-[11px] text-[#888]">总市值</div>
                    <div class="text-[18px] font-bold text-[#111] tabular-nums mt-[4px]">
                        {{ fmtAmount(summaryStats.totalMV) }}
                    </div>
                    <div class="text-[10px] text-[#bbb] mt-[2px]">
                        <template v-if="isSummary">{{ positions.length }} 只 / {{ accounts.length }} 账户</template>
                        <template v-else>{{ positions.length }} 只持仓</template>
                    </div>
                </div>
                <!-- 总成本 -->
                <div class="bg-gradient-to-b from-[#fafafa] to-white border border-[#eeeeee] rounded-[6px] px-[14px] py-[10px]">
                    <div class="text-[11px] text-[#888]">总成本</div>
                    <div class="text-[18px] font-bold text-[#111] tabular-nums mt-[4px]">
                        {{ fmtAmount(summaryStats.totalCV) }}
                    </div>
                    <div class="text-[10px] text-[#bbb] mt-[2px]">
                        <template v-if="isSummary">加权均价</template>
                        <template v-else>{{ selectedAccount?.name }}</template>
                    </div>
                </div>
                <!-- 当日盈亏 -->
                <div class="bg-gradient-to-b from-[#fafafa] to-white border border-[#eeeeee] rounded-[6px] px-[14px] py-[10px]">
                    <div class="text-[11px] text-[#888]">当日盈亏</div>
                    <div class="text-[18px] font-bold tabular-nums mt-[4px]"
                         :class="colorClassOf(summaryStats.totalDailyPL)">
                        {{ fmtSignedAmount(summaryStats.totalDailyPL) }}
                    </div>
                    <div class="text-[11px] font-medium tabular-nums mt-[2px]"
                         :class="colorClassOf(summaryStats.dailyPLPct)">
                        {{ fmtPercent(summaryStats.dailyPLPct) }}
                    </div>
                </div>
                <!-- 持有盈亏 -->
                <div class="bg-gradient-to-b from-[#fafafa] to-white border border-[#eeeeee] rounded-[6px] px-[14px] py-[10px]">
                    <div class="text-[11px] text-[#888]">持有盈亏</div>
                    <div class="text-[18px] font-bold tabular-nums mt-[4px]"
                         :class="colorClassOf(summaryStats.totalProfit)">
                        {{ fmtSignedAmount(summaryStats.totalProfit) }}
                    </div>
                    <div class="text-[11px] font-medium tabular-nums mt-[2px]"
                         :class="colorClassOf(summaryStats.totalProfitPct)">
                        {{ fmtPercent(summaryStats.totalProfitPct) }}
                    </div>
                </div>
            </div>
        </div>

        <!-- ============ 工具栏（紧贴表格，作为"表格操作区"）============ -->
        <div class="h-[44px] flex items-center px-[14px] border-b border-[#f0f0f0] bg-white shrink-0 gap-[10px]">
            <!-- 左侧：涨跌统计（当前可见持仓）-->
            <div v-if="positions.length" class="flex items-center gap-[14px] text-[12px] tabular-nums">
                <span class="font-semibold text-[#dc2626]">
                    上涨 <span class="mx-[1px]">{{ tradingStats.up }}</span> 个
                </span>
                <span v-if="tradingStats.flat > 0" class="text-[#999]">
                    持平 <span class="mx-[1px]">{{ tradingStats.flat }}</span> 个
                </span>
                <span class="font-semibold text-[#059669]">
                    下跌 <span class="mx-[1px]">{{ tradingStats.down }}</span> 个
                </span>
                <!-- 迷你比例条：红/灰/绿 堆叠显示涨跌占比 -->
                <div v-if="(tradingStats.up + tradingStats.down + tradingStats.flat) > 0"
                     class="flex h-[5px] w-[90px] rounded-full overflow-hidden bg-[#f0f0f0]">
                    <div class="bg-[#dc2626]"
                         :style="{ width: (tradingStats.up / (tradingStats.up + tradingStats.down + tradingStats.flat) * 100) + '%' }"></div>
                    <div class="bg-[#d4d4d8]"
                         :style="{ width: (tradingStats.flat / (tradingStats.up + tradingStats.down + tradingStats.flat) * 100) + '%' }"></div>
                    <div class="bg-[#059669]"
                         :style="{ width: (tradingStats.down / (tradingStats.up + tradingStats.down + tradingStats.flat) * 100) + '%' }"></div>
                </div>
            </div>

            <!-- 右侧：搜索 + 添加 -->
            <div class="flex items-center gap-[8px] shrink-0 ml-auto">
                <!-- 筛选输入 -->
                <div class="relative">
                    <svg class="absolute left-[8px] top-1/2 -translate-y-1/2 w-[12px] h-[12px] text-[#94a3b8] pointer-events-none"
                         viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd" />
                    </svg>
                    <input v-model="searchQuery"
                           type="text"
                           placeholder="筛选当前持仓"
                           class="text-[12px] w-[170px] pl-[26px] pr-[24px] py-[4px] bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] focus:bg-white transition placeholder:text-[#bbb]">
                    <button v-if="searchQuery" @click="searchQuery = ''"
                            class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-[#aaa] hover:text-[#666] flex items-center justify-center">
                        <svg class="w-[9px] h-[9px]" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                        </svg>
                    </button>
                </div>
                <!-- 添加持仓按钮（汇总 tab 下没有目标账户，隐藏）-->
                <button v-if="!isSummary"
                        @click="openAddModal"
                        class="flex items-center gap-[4px] text-[12px] font-bold text-white bg-[#dc2626] px-[12px] py-[5px] rounded-[4px] hover:bg-[#991b1b] transition">
                    <svg class="w-[11px] h-[11px]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clip-rule="evenodd"/>
                    </svg>
                    添加持仓
                </button>
            </div>
        </div>

        <!-- 持仓表格 -->
        <div class="flex-1 overflow-auto custom-scrollbar bg-white">
            <table class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[12px] text-[#888] z-10">
                    <tr>
                        <th class="px-[12px] py-[10px] font-normal w-[140px]">股票名称</th>
                        <th class="px-[10px] py-[10px] font-normal text-right w-[90px]">
                            <div class="leading-tight">现价</div>
                            <div class="text-[10px] text-[#bbb] font-normal leading-tight mt-[1px]">成本</div>
                        </th>
                        <th class="px-[10px] py-[10px] font-normal text-right w-[110px] cursor-pointer select-none hover:text-[#dc2626] transition group"
                            @click="handleSort('dailyProfit')">
                            <div class="leading-tight">
                                当日盈亏
                                <span class="ml-[2px] text-[9px] align-middle tabular-nums"
                                      :class="sortDirFor('dailyProfit') ? 'text-[#dc2626]' : 'text-[#ccc] opacity-0 group-hover:opacity-100'">
                                    {{ sortDirFor('dailyProfit') === 'asc' ? '▲' : '▼' }}
                                </span>
                            </div>
                            <div class="text-[10px] text-[#bbb] font-normal leading-tight mt-[1px]">当日盈亏%</div>
                        </th>
                        <th class="px-[10px] py-[10px] font-normal text-right w-[120px] cursor-pointer select-none hover:text-[#dc2626] transition group"
                            @click="handleSort('totalProfit')">
                            <div class="leading-tight">
                                持有盈亏
                                <span class="ml-[2px] text-[9px] align-middle tabular-nums"
                                      :class="sortDirFor('totalProfit') ? 'text-[#dc2626]' : 'text-[#ccc] opacity-0 group-hover:opacity-100'">
                                    {{ sortDirFor('totalProfit') === 'asc' ? '▲' : '▼' }}
                                </span>
                            </div>
                            <div class="text-[10px] text-[#bbb] font-normal leading-tight mt-[1px]">持有盈亏%</div>
                        </th>
                        <th class="px-[10px] py-[10px] font-normal text-right w-[75px]">持股</th>
                        <th class="px-[10px] py-[10px] font-normal text-right w-[110px]">
                            <div class="leading-tight">市值</div>
                            <div v-if="isSummary" class="text-[10px] text-[#bbb] font-normal leading-tight mt-[1px]">仓位占比</div>
                        </th>
                        <!-- 汇总视图显示"持有账户"，单账户视图显示"加仓日期" -->
                        <th v-if="isSummary" class="pl-[20px] pr-[10px] py-[10px] font-normal text-left w-[120px]">持有账户</th>
                        <th v-else class="px-[10px] py-[10px] font-normal text-center w-[90px]">加仓日期</th>
                        <!-- 操作列只在单账户视图下出现 -->
                        <th v-if="!isSummary" class="px-[10px] py-[10px] font-normal text-center w-[80px]">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-if="loading && !positions.length">
                        <td :colspan="isSummary ? 7 : 8" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</td>
                    </tr>
                    <tr v-else-if="!positions.length">
                        <td :colspan="isSummary ? 7 : 8" class="py-[80px] text-center text-[#aaa] text-[13px]">
                            <template v-if="isSummary">
                                还没有任何持仓，先切到账户 tab 添加持仓再来看汇总
                            </template>
                            <template v-else>
                                账户「{{ selectedAccount?.name }}」还没有持仓，点击右上角
                                <span class="text-[#dc2626] font-semibold">+ 添加持仓</span>
                            </template>
                        </td>
                    </tr>
                    <tr v-else-if="!filteredPositions.length">
                        <td :colspan="isSummary ? 7 : 8" class="py-[60px] text-center text-[#aaa] text-[13px]">
                            未匹配到"{{ searchQuery }}"，
                            <button @click="searchQuery = ''" class="text-[#dc2626] hover:underline">清空搜索</button>
                        </td>
                    </tr>

                    <tr v-for="p in filteredPositions" :key="p.code"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] transition-colors group">

                        <!-- 股票名称 + 代码 -->
                        <td class="px-[12px] py-[8px] align-middle">
                            <div class="text-[14px] font-bold text-[#111] leading-tight truncate">{{ p.name || quotes[p.code]?.name || '—' }}</div>
                            <div class="text-[11px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums">
                                {{ marketPrefix(p.code) }}{{ p.code }}
                            </div>
                        </td>

                        <!-- 现价 / 成本（上下两行）-->
                        <td class="px-[10px] py-[6px] text-right tabular-nums align-middle">
                            <div class="text-[13px] font-bold leading-tight"
                                 :class="colorClassOf(quotes[p.code]?.changePct)">
                                {{ fmtPrice(quotes[p.code]?.price) }}
                            </div>
                            <div class="text-[11px] text-[#999] leading-tight mt-[2px]">
                                {{ p.cost_price != null ? p.cost_price.toFixed(3) : '—' }}
                            </div>
                        </td>

                        <!-- 当日盈亏 / 当日盈亏%（±符号 + 颜色双编码）-->
                        <td class="px-[10px] py-[6px] text-right tabular-nums align-middle"
                            :class="colorClassOf(dailyProfitAmount(p))">
                            <div class="text-[13px] font-bold leading-tight">
                                {{ fmtSignedAmount(dailyProfitAmount(p)) }}
                            </div>
                            <div class="text-[11px] font-medium leading-tight mt-[2px] opacity-85">
                                {{ fmtPercent(dailyProfitPct(p)) }}
                            </div>
                        </td>

                        <!-- 持有盈亏 / 持有盈亏%（±符号 + 颜色双编码）-->
                        <td class="px-[10px] py-[6px] text-right tabular-nums align-middle"
                            :class="colorClassOf(profitAmount(p))">
                            <div class="text-[13px] font-bold leading-tight">
                                {{ fmtSignedAmount(profitAmount(p)) }}
                            </div>
                            <div class="text-[11px] font-medium leading-tight mt-[2px] opacity-85">
                                {{ fmtPercent(profitPct(p)) }}
                            </div>
                        </td>

                        <!-- 持股 -->
                        <td class="px-[10px] py-[8px] text-right text-[12px] text-[#475569] tabular-nums align-middle">
                            {{ fmtShares(p.shares) }}
                        </td>

                        <!-- 市值（汇总视图下方附仓位占比%）-->
                        <td class="px-[10px] py-[6px] text-right tabular-nums align-middle">
                            <div class="text-[12px] text-[#475569] leading-tight">
                                {{ fmtAmount(marketValue(p)) }}
                            </div>
                            <div v-if="isSummary" class="text-[11px] text-[#999] leading-tight mt-[2px]">
                                {{ positionWeight(p) != null ? positionWeight(p).toFixed(2) + '%' : '—' }}
                            </div>
                        </td>

                        <!-- 汇总视图：持有账户徽章 -->
                        <td v-if="isSummary" class="pl-[20px] pr-[10px] py-[8px] align-middle">
                            <div class="flex flex-wrap gap-[4px]">
                                <span v-for="(acc, idx) in (p.accounts || [])" :key="idx"
                                      class="text-[10px] text-[#dc2626] bg-[#fff0f0] border border-[#fde0e0] px-[6px] py-[1px] rounded-sm leading-[1.4]">
                                    {{ acc }}
                                </span>
                            </div>
                        </td>

                        <!-- 单账户视图：加仓日期 -->
                        <td v-else class="px-[10px] py-[8px] text-center text-[12px] text-[#666] tabular-nums align-middle">
                            {{ formatAddedDate(p.added_at) }}
                        </td>

                        <!-- 操作（仅单账户视图）-->
                        <td v-if="!isSummary" class="px-[10px] py-[8px] text-center">
                            <div class="flex items-center justify-center gap-[8px] opacity-0 group-hover:opacity-100 transition">
                                <button @click="startEdit(p)" class="text-[#666] hover:text-[#2563eb] transition" title="编辑">
                                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                                    </svg>
                                </button>
                                <button @click="handleRemovePosition(p)" class="text-[#666] hover:text-[#dc2626] transition" title="删除">
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

    <!-- ============ 添加持仓 Modal ============ -->
    <div v-if="showAddModal" class="fixed inset-0 bg-black/20 z-50 flex items-center justify-center"
         @click.self="closeAddModal">
        <div class="bg-white rounded-[6px] p-[20px] w-[420px] shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <div class="text-[14px] font-bold text-[#111] mb-[16px]">添加持仓 — {{ selectedAccount?.name }}</div>

            <div class="flex flex-col gap-[12px]">
                <!-- 股票搜索 -->
                <label class="flex flex-col gap-[4px] relative">
                    <span class="text-[12px] text-[#666]">股票（代码/名称/拼音）</span>
                    <input v-model="addQuery" @input="onAddSearchInput"
                           placeholder="如：600519 / 贵州茅台 / gzmt"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                    <!-- 搜索结果下拉 -->
                    <div v-if="addQuery.trim() && (addSearching || addResults.length)"
                         class="absolute top-full left-0 right-0 mt-[4px] bg-white border border-[#e5e5e5] rounded-[4px] shadow-[0_4px_16px_rgba(0,0,0,0.1)] max-h-[200px] overflow-auto custom-scrollbar z-10">
                        <div v-if="addSearching" class="py-[10px] text-center text-[12px] text-[#aaa]">搜索中...</div>
                        <div v-for="s in addResults" :key="s.code"
                             @click="selectStockForAdd(s)"
                             class="px-[10px] py-[6px] hover:bg-[#fff5f5] cursor-pointer border-b border-[#f5f5f5] last:border-b-0">
                            <div class="flex items-center gap-[6px]">
                                <span class="text-[13px] font-bold text-[#111]">{{ s.name }}</span>
                                <span class="text-[10px] text-[#dc2626] bg-[#fff0f0] px-[4px] py-[1px] rounded-sm">{{ s.marketType }}</span>
                            </div>
                            <div class="text-[11px] text-[#999] font-mono tabular-nums">{{ s.marketPrefix }}{{ s.code }}</div>
                        </div>
                    </div>
                    <!-- 已选中股票提示 -->
                    <div v-if="addForm.code" class="text-[11px] text-[#dc2626] mt-[2px]">
                        已选：{{ addForm.name }}（{{ addForm.code }}）
                    </div>
                </label>

                <div class="flex gap-[10px]">
                    <label class="flex flex-col gap-[4px] flex-1">
                        <span class="text-[12px] text-[#666]">持股数量（100 股的整数倍）</span>
                        <input v-model="addForm.shares" type="number" step="100" min="100" placeholder="如 1000"
                               class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                    </label>
                    <label class="flex flex-col gap-[4px] flex-1">
                        <span class="text-[12px] text-[#666]">成本价</span>
                        <input v-model="addForm.costPrice" type="number" step="0.001" placeholder="如 1680.000"
                               class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                    </label>
                </div>

                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">加仓日期</span>
                    <input v-model="addForm.addedAt" type="date"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                </label>

                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">备注（可选）</span>
                    <textarea v-model="addForm.remark" rows="2" placeholder="买入逻辑、仓位计划..."
                              class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] resize-none"></textarea>
                </label>

                <div v-if="addFormError" class="text-[12px] text-[#dc2626]">{{ addFormError }}</div>
            </div>

            <div class="flex justify-end gap-[8px] mt-[16px]">
                <button @click="closeAddModal"
                        class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-[#f5f5f5]">取消</button>
                <button @click="submitAdd"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b]">确认添加</button>
            </div>
        </div>
    </div>

    <!-- ============ 编辑持仓 Modal ============ -->
    <div v-if="editingPosition" class="fixed inset-0 bg-black/20 z-50 flex items-center justify-center"
         @click.self="cancelEdit">
        <div class="bg-white rounded-[6px] p-[20px] w-[400px] shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <div class="text-[14px] font-bold text-[#111] mb-[16px]">编辑持仓 — {{ editingPosition.name || editingPosition.code }}</div>
            <div class="flex flex-col gap-[12px]">
                <div class="flex gap-[10px]">
                    <label class="flex flex-col gap-[4px] flex-1">
                        <span class="text-[12px] text-[#666]">持股数量</span>
                        <input v-model="editingPosition.shares" type="number" step="100" min="100"
                               class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                    </label>
                    <label class="flex flex-col gap-[4px] flex-1">
                        <span class="text-[12px] text-[#666]">成本价</span>
                        <input v-model="editingPosition.costPrice" type="number" step="0.001"
                               class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                    </label>
                </div>
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">加仓日期</span>
                    <input v-model="editingPosition.addedAt" type="date"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                </label>
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">备注</span>
                    <textarea v-model="editingPosition.remark" rows="2"
                              class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] resize-none"></textarea>
                </label>
                <div v-if="editFormError" class="text-[12px] text-[#dc2626]">{{ editFormError }}</div>
            </div>
            <div class="flex justify-end gap-[8px] mt-[16px]">
                <button @click="cancelEdit"
                        class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-[#f5f5f5]">取消</button>
                <button @click="saveEdit"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b]">保存</button>
            </div>
        </div>
    </div>

    <!-- ============ 管理账户 Modal ============ -->
    <div v-if="showManageModal" class="fixed inset-0 bg-black/25 z-50 flex items-center justify-center"
         @click.self="closeManageModal">
        <div class="bg-white rounded-[8px] w-[420px] max-h-[80vh] flex flex-col shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <div class="px-[20px] py-[14px] border-b border-[#f0f0f0] flex items-center justify-between shrink-0">
                <div>
                    <div class="text-[14px] font-bold text-[#111]">管理账户</div>
                    <div class="text-[11px] text-[#999] mt-[2px]">拖拽 ⇅ 调整顺序，点击铅笔重命名</div>
                </div>
                <button @click="closeManageModal"
                        class="text-[#999] hover:text-[#111] w-[24px] h-[24px] flex items-center justify-center rounded hover:bg-[#f5f5f5]">
                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                    </svg>
                </button>
            </div>

            <div class="flex-1 overflow-auto custom-scrollbar px-[12px] py-[8px]">
                <draggable v-if="managingAccounts.length"
                           v-model="managingAccounts"
                           item-key="id"
                           handle=".drag-handle"
                           ghost-class="drag-ghost"
                           @end="onManageDragEnd"
                           class="flex flex-col gap-[2px]">
                    <template #item="{ element: a }">
                        <div class="flex items-center gap-[10px] px-[8px] py-[8px] rounded-[4px] hover:bg-[#fafafa] transition">
                            <div class="drag-handle cursor-grab active:cursor-grabbing text-[#bbb] hover:text-[#666] transition shrink-0">
                                <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                    <circle cx="6" cy="5" r="1.5"/><circle cx="6" cy="10" r="1.5"/><circle cx="6" cy="15" r="1.5"/>
                                    <circle cx="14" cy="5" r="1.5"/><circle cx="14" cy="10" r="1.5"/><circle cx="14" cy="15" r="1.5"/>
                                </svg>
                            </div>
                            <div class="flex-1 min-w-0">
                                <div v-if="managingEditingId !== a.id" class="flex items-center gap-[8px]">
                                    <span class="text-[13px] font-semibold text-[#111] truncate">{{ a.name }}</span>
                                    <span class="text-[10px] text-[#999] tabular-nums">{{ a.count }} 只</span>
                                </div>
                                <input v-else ref="managingEditInputRef"
                                       v-model="managingEditingName"
                                       @keyup.enter="saveManageEdit"
                                       @keyup.esc="cancelManageEdit"
                                       @blur="saveManageEdit"
                                       class="text-[13px] font-semibold text-[#dc2626] bg-white border border-[#dc2626] rounded-[3px] px-[6px] py-[3px] w-full outline-none">
                            </div>
                            <div v-if="managingEditingId !== a.id" class="flex items-center gap-[4px] shrink-0">
                                <button @click="startManageEdit(a)"
                                        class="w-[26px] h-[26px] text-[#666] hover:text-[#2563eb] hover:bg-[#eff6ff] rounded flex items-center justify-center transition"
                                        title="重命名">
                                    <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                                    </svg>
                                </button>
                                <button @click="manageDeleteAccount(a)"
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
                    点击下方 + 新建账户 开始
                </div>
            </div>

            <div class="px-[20px] py-[14px] border-t border-[#f0f0f0] flex items-center gap-[8px] shrink-0">
                <input v-model="managingNewAccountName"
                       @keyup.enter="manageCreateAccount"
                       placeholder="新账户名称"
                       class="flex-1 text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                <button @click="manageCreateAccount"
                        :disabled="!managingNewAccountName.trim()"
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

    <!-- ============ 通用确认弹窗 ============ -->
    <div v-if="confirmState.show"
         class="fixed inset-0 bg-black/25 z-[60] flex items-center justify-center"
         @click.self="confirmCancel">
        <div class="bg-white rounded-[8px] w-[360px] shadow-[0_10px_40px_rgba(0,0,0,0.18)] overflow-hidden">
            <div class="flex items-start gap-[12px] px-[20px] pt-[20px] pb-[4px]">
                <div class="w-[36px] h-[36px] rounded-full bg-[#fef2f2] flex items-center justify-center shrink-0">
                    <svg class="w-[18px] h-[18px] text-[#dc2626]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l6.518 11.59c.75 1.335-.213 2.98-1.742 2.98H3.48c-1.53 0-2.493-1.645-1.743-2.98L8.257 3.1zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                    </svg>
                </div>
                <div class="flex-1 min-w-0 pt-[4px]">
                    <div class="text-[15px] font-bold text-[#111] leading-tight">{{ confirmState.title }}</div>
                </div>
            </div>
            <div class="px-[20px] pl-[68px] py-[10px] text-[13px] text-[#555] leading-relaxed">
                {{ confirmState.message }}
            </div>
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

.drag-ghost {
    opacity: 0.4;
    background: #fff0f0 !important;
    border: 1px dashed #dc2626;
}
</style>
