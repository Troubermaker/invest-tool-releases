<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import draggable from 'vuedraggable'
import { api } from '../api/client'
import { useSmartRefresh } from '../composables/useSmartRefresh'
import { openStockChart } from '../composables/useStockChart'
import { useExternalApp } from '../composables/useExternalApp'
const ext = useExternalApp()

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

// 列排序：3 态循环（desc → asc → 清空），可排："当日盈亏" / "持有盈亏" / "年初至今"
// sortKey: 'dailyProfit' | 'totalProfit' | 'ytdPct' | null
const sortKey = ref(null)
const sortOrder = ref('desc')

// 实时行情
const quotes = ref({})
// 分时 sparkline 数据（code → { preClose, prices, avgPrices }）
const sparklines = ref({})

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

// ---------------- 加减仓模拟弹窗 ----------------
// { position, mode: 'buy'|'sell', price: string, shares: string }
const simulating = ref(null)
const simFormError = ref('')

// 交易费率（持久化在 user_preferences['trade_fees']）
// 默认值参考用户提供的标准；用户可在模拟弹窗里改并保存
const DEFAULT_FEES = {
    commission: 0.0000869,  // 佣金率（双向）
    transfer:   0.0000096,  // 过户费率（双向）
    stamp:      0.000498,   // 印花税率（仅卖出）
}
const tradeFees = ref({ ...DEFAULT_FEES })
const showFeesEditor = ref(false)
const feesSaveStatus = ref('')

async function loadTradeFees() {
    const res = await api.getUserPreference('trade_fees')
    if (res.ok && res.data && typeof res.data === 'object') {
        tradeFees.value = { ...DEFAULT_FEES, ...res.data }
    }
}
async function saveTradeFees() {
    const res = await api.setUserPreference('trade_fees', tradeFees.value)
    feesSaveStatus.value = res.ok ? '✓ 已保存' : '保存失败'
    setTimeout(() => { feesSaveStatus.value = '' }, 1500)
}
function resetFeesToDefault() {
    tradeFees.value = { ...DEFAULT_FEES }
}

// 计算单次操作的手续费明细
function _computeFee(amount, mode) {
    const commission = amount * tradeFees.value.commission
    const transfer   = amount * tradeFees.value.transfer
    const stamp      = mode === 'sell' ? amount * tradeFees.value.stamp : 0
    return { commission, transfer, stamp, total: commission + transfer + stamp }
}

function _defaultPriceFor(code, fallback) {
    const q = quotes.value[code]
    if (q?.price != null) return q.price.toFixed(2)
    return fallback != null ? fallback.toFixed(2) : ''
}

function startSimulate(p) {
    simulating.value = {
        position: p,                        // 原始 DB 持仓（只读引用，用于对比）
        simShares: p.shares,                // 模拟态持股（随每步变化）
        simCost: p.cost_price,              // 模拟态成本价（摊薄）
        steps: [],                          // 已规划步骤列表（步骤详情含 mode/price/shares/date）
        mode: 'buy',                        // 当前正在编辑的步骤
        price: _defaultPriceFor(p.code, p.cost_price),
        shares: '',
        date: todayStr(),                   // 当前步骤的交易日期，用于 T+1 校验
    }
    simFormError.value = ''
    simDragOffset.value = { x: 0, y: 0 }
}

// 计算给定日期当天最多可卖股数（T+1 约束）
// 原始持股 + 该日期之前所有买入 - 之前所有卖出 - 同日已计划的卖出
// 同日的买入不计入可卖（A 股 T+1 规则）
function _availableToSellOn(date) {
    const s = simulating.value
    if (!s) return 0
    let entering = s.position.shares
    let sameDateSells = 0
    for (const step of s.steps) {
        if (step.date < date) {
            entering += (step.mode === 'buy' ? step.shares : -step.shares)
        } else if (step.date === date) {
            if (step.mode === 'sell') sameDateSells += step.shares
            // 当日买入不进入可卖池
        }
    }
    return entering - sameDateSells
}

// 累计现金流（已扣费）：加仓 = -(操作金额+费用)，减仓 = +(操作金额-费用)
const simCashFlow = computed(() => {
    const s = simulating.value
    if (!s) return 0
    return s.steps.reduce((sum, step) => {
        const amount = step.shares * step.price
        const fee = step.feeTotal || 0
        return sum + (step.mode === 'sell' ? (amount - fee) : -(amount + fee))
    }, 0)
})

// 累计交易费用（所有步骤的手续费总和）
const simTotalFees = computed(() => {
    const s = simulating.value
    if (!s) return 0
    return s.steps.reduce((sum, step) => sum + (step.feeTotal || 0), 0)
})

// 本方案增量 = 现金流（已扣费）+ (新持股 - 原持股) × 现价
const simNetGain = computed(() => {
    const s = simulating.value
    if (!s) return null
    const q = quotes.value[s.position.code]
    if (!q?.price) return null
    const shareDelta = s.simShares - s.position.shares
    return simCashFlow.value + shareDelta * q.price
})

// 做T 收益：把所有 sell 与 buy 步按数量配对（取较小总量），算差价收益
// shares 不变的纯T 场景下 = 本方案增量；混合场景下 = T 部分独立收益
const simRoundTrip = computed(() => {
    const s = simulating.value
    if (!s || s.steps.length < 2) return null
    let buyShares = 0, buyAmount = 0, sellShares = 0, sellAmount = 0
    for (const step of s.steps) {
        if (step.mode === 'buy') {
            buyShares += step.shares
            buyAmount += step.shares * step.price
        } else {
            sellShares += step.shares
            sellAmount += step.shares * step.price
        }
    }
    // 必须既有买又有卖才构成"做T"
    if (buyShares === 0 || sellShares === 0) return null
    const matched = Math.min(buyShares, sellShares)
    const avgBuy  = buyAmount / buyShares
    const avgSell = sellAmount / sellShares
    return {
        shares: matched,
        avgBuy,
        avgSell,
        profit: matched * (avgSell - avgBuy),
    }
})

// 把当前输入作为一步"提交"到模拟态（不写 DB），继续叠下一步
function commitStep() {
    const res = simulationResult.value
    const s = simulating.value
    if (!s || !res || res.error) return
    s.steps.push({
        mode: s.mode,
        price: parseFloat(s.price),
        shares: parseInt(s.shares, 10),
        date: s.date,
        realizedDelta: res.mode === 'sell' ? res.realizedProfit : 0,
        costDelta: res.costDelta,
        feeTotal: res.fees.total,             // 这步的总费用
        feeBreakdown: { ...res.fees },        // 明细（佣金 / 过户 / 印花）
    })
    s.simShares = res.newShares
    s.simCost = res.newCost
    s.price = _defaultPriceFor(s.position.code, s.simCost)
    s.shares = ''
    simFormError.value = ''
}

// 撤销最后一步：从头重算（与 simulationResult 同公式："费用减去"口径）
function undoLastStep() {
    const s = simulating.value
    if (!s || !s.steps.length) return
    s.steps.pop()
    s.simShares = s.position.shares
    s.simCost = s.position.cost_price
    for (const step of s.steps) {
        const amount = step.shares * step.price
        const fee = step.feeTotal || 0
        if (step.mode === 'buy') {
            const newShares = s.simShares + step.shares
            s.simCost = (s.simShares * s.simCost + amount) / newShares  // 加仓不计费用进基础
            s.simShares = newShares
        } else {
            const newShares = s.simShares - step.shares
            s.simCost = newShares > 0
                ? (s.simShares * s.simCost - amount - fee) / newShares  // 减仓减去费用
                : 0
            s.simShares = newShares
        }
    }
}

// 清空所有步骤，回到原始持仓
function resetSimulation() {
    const s = simulating.value
    if (!s) return
    s.simShares = s.position.shares
    s.simCost = s.position.cost_price
    s.steps = []
    s.mode = 'buy'
    s.price = _defaultPriceFor(s.position.code, s.position.cost_price)
    s.shares = ''
    s.date = todayStr()
    simFormError.value = ''
}

// ---------------- 模拟弹窗：可拖动 + 防误关 ----------------
const simDragOffset = ref({ x: 0, y: 0 })
let _simDragStart = null  // { mouseX, mouseY, offsetX, offsetY }

function onSimDragStart(e) {
    if (e.button !== 0) return  // 仅左键
    e.preventDefault()
    _simDragStart = {
        mouseX: e.clientX,
        mouseY: e.clientY,
        offsetX: simDragOffset.value.x,
        offsetY: simDragOffset.value.y,
    }
    window.addEventListener('mousemove', onSimDragMove)
    window.addEventListener('mouseup', onSimDragEnd)
}
function onSimDragMove(e) {
    if (!_simDragStart) return
    simDragOffset.value = {
        x: _simDragStart.offsetX + (e.clientX - _simDragStart.mouseX),
        y: _simDragStart.offsetY + (e.clientY - _simDragStart.mouseY),
    }
}
function onSimDragEnd() {
    _simDragStart = null
    window.removeEventListener('mousemove', onSimDragMove)
    window.removeEventListener('mouseup', onSimDragEnd)
}

// 遮罩点击关闭：仅当 mousedown 和 click 都发生在遮罩上时才关，
// 避免"输入框里按下 → 拖出到遮罩上松开"被误判为点击遮罩
let _simOverlayMouseDown = false
function onSimOverlayMouseDown(e) {
    _simOverlayMouseDown = (e.target === e.currentTarget)
}
function onSimOverlayClick(e) {
    if (_simOverlayMouseDown && e.target === e.currentTarget) {
        cancelSimulate()
    }
    _simOverlayMouseDown = false
}

function cancelSimulate() {
    simulating.value = null
    simFormError.value = ''
}

// 实时计算下一步的结果（叠加在当前模拟态 simShares/simCost 上）
const simulationResult = computed(() => {
    const s = simulating.value
    if (!s) return null
    const curShares = s.simShares
    const curCost = s.simCost
    const opPrice = parseFloat(s.price)
    const opShares = parseInt(s.shares, 10)
    if (isNaN(opPrice) || opPrice <= 0) return null
    if (isNaN(opShares) || opShares <= 0 || opShares % 100 !== 0) return null

    const amount = opShares * opPrice              // 操作金额（不含费）
    const fees = _computeFee(amount, s.mode)        // 手续费明细

    if (s.mode === 'buy') {
        // 加仓：摊薄成本只算"投入本金"，费用作为沉没成本不进基础
        const newShares = curShares + opShares
        const newCost = (curShares * curCost + amount) / newShares
        return {
            mode: 'buy',
            newShares,
            newCost,
            costDelta: newCost - curCost,
            fees,
            cashUsed: amount + fees.total,  // 实际现金流出（含费）
        }
    }
    // 卖出：先做 T+1 校验，再做总持仓校验
    if (opShares > curShares) {
        return { error: `卖出数量 ${opShares.toLocaleString()} 超过当前持有 ${curShares.toLocaleString()}` }
    }
    const availableT1 = _availableToSellOn(s.date)
    if (opShares > availableT1) {
        return {
            error: `T+1 限制：${s.date} 当日最多可卖 ${availableT1.toLocaleString()} 股 ` +
                   `（当日买入的部分要等下个交易日才能卖）`
        }
    }
    const newShares = curShares - opShares
    // 已实现盈亏 = (卖出收入 - 费用) - 卖出股的成本（真实落袋损益）
    //           = opShares×(opPrice-curCost) - fees
    const realizedProfit = amount - fees.total - opShares * curCost
    const realizedPct = curCost > 0 ? (realizedProfit / (opShares * curCost)) * 100 : null
    // 摊薄新成本（费用作沉没成本，不进基础）：
    //   newCost = (curShares × curCost − 操作金额 − 费用) / 剩余股
    // 含义：剩余股的成本基础 = "本金净未回收"，费用是已确认的损失独立于成本基础
    const newCost = newShares > 0
        ? (curShares * curCost - amount - fees.total) / newShares
        : 0
    return {
        mode: 'sell',
        newShares,
        newCost,
        costDelta: newCost - curCost,
        fees,
        realizedProfit,
        realizedPct,
        cashGained: amount - fees.total,  // 实际现金流入（扣费）
        isFullClose: newShares === 0,
    }
})

// 模拟态下的"浮动盈亏"：(现价 − 当前摊薄成本) × 模拟持股
// 配合"已实现"一起看可以知道"如果此刻按现价卖光，总共赚/亏多少"
const simUnrealized = computed(() => {
    const s = simulating.value
    if (!s || s.simShares === 0) return null
    const q = quotes.value[s.position.code]
    if (!q?.price) return null
    const amount = s.simShares * (q.price - s.simCost)
    // 摊薄成本可能为负（罕见但合法），此时百分比无物理意义，只返回金额
    const pct = s.simCost > 0 ? (q.price - s.simCost) / s.simCost * 100 : null
    return { amount, pct }
})

// 执行按钮的文案：根据是否清仓 / 有无多步 动态变化
const executeButtonLabel = computed(() => {
    const s = simulating.value
    if (!s) return '执行'
    const hasSteps = s.steps.length > 0
    // 加上当前未提交的这步，预判最终 shares
    const pendingFinalShares = simulationResult.value && !simulationResult.value.error
        ? simulationResult.value.newShares
        : s.simShares
    if (pendingFinalShares === 0) return '执行（清仓）'
    if (hasSteps) return `执行 ${s.steps.length + (simulationResult.value && !simulationResult.value.error ? 1 : 0)} 步到持仓`
    if (simulationResult.value?.mode === 'buy') return '执行加仓'
    if (simulationResult.value?.mode === 'sell') return '执行减仓'
    return '执行'
})

async function executeSimulation() {
    const s = simulating.value
    if (!s) return
    simFormError.value = ''

    // 如果当前有未提交的有效输入，先把它 commit 进 steps 再执行
    if (simulationResult.value && !simulationResult.value.error) {
        commitStep()
    }

    if (s.steps.length === 0) {
        simFormError.value = '还没有任何操作步骤'
        return
    }

    const p = s.position
    let apiRes
    if (s.simShares === 0) {
        // 清仓
        apiRes = await api.removePortfolioPosition(selectedAccountId.value, p.code)
    } else if (s.simShares === p.shares && Math.abs(s.simCost - p.cost_price) < 1e-9) {
        // 净效应为 0（比如加仓再同价减仓）
        simulating.value = null
        return
    } else {
        apiRes = await api.updatePortfolioPosition(selectedAccountId.value, p.code, {
            shares: s.simShares,
            costPrice: s.simCost,
        })
    }

    if (!apiRes.ok) {
        simFormError.value = apiRes.error || '执行失败'
        return
    }
    simulating.value = null
    await Promise.all([loadAccounts(), loadPositions()])
}

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

    // ② 按选中维度排序（未排序时保持原顺序）
    if (sortKey.value) {
        let getVal
        if (sortKey.value === 'dailyProfit')      getVal = dailyProfitAmount
        else if (sortKey.value === 'totalProfit') getVal = profitAmount
        else if (sortKey.value === 'ytdPct')      getVal = (p) => quotes.value[p.code]?.ytdPct
        else                                       getVal = () => null
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

// 批量拉 sparkline —— 渐进式加载（小批 + jitter 间隔，反爬友好）
let _sparklineLoadToken = 0
async function refreshSparklines() {
    if (!positions.value.length) { sparklines.value = {}; return }
    const myToken = ++_sparklineLoadToken
    const codes = positions.value.map(p => p.code)
    const BATCH_SIZE = 3
    const MIN_GAP_MS = 700, MAX_GAP_MS = 1300
    const merged = { ...sparklines.value }
    for (let i = 0; i < codes.length; i += BATCH_SIZE) {
        if (myToken !== _sparklineLoadToken) return
        const batch = codes.slice(i, i + BATCH_SIZE)
        const res = await api.getBatchSparklines(batch)
        if (myToken !== _sparklineLoadToken) return
        if (res.ok && res.data) {
            Object.assign(merged, res.data)
            sparklines.value = { ...merged }
        }
        if (i + BATCH_SIZE < codes.length) {
            const gap = MIN_GAP_MS + Math.random() * (MAX_GAP_MS - MIN_GAP_MS)
            await new Promise(r => setTimeout(r, gap))
        }
    }
}

// ---------- sparkline SVG 计算（与 Watchlist 共用一套规则）----------
const SPARK_W = 140
const SPARK_H = 50

function computeYRange(sp) {
    if (!sp || sp.preClose == null) return null
    const baseline = sp.preClose
    let yMin = baseline, yMax = baseline
    for (const p of (sp.prices || [])) { if (p < yMin) yMin = p; if (p > yMax) yMax = p }
    for (const p of (sp.avgPrices || [])) { if (p < yMin) yMin = p; if (p > yMax) yMax = p }
    const minRange = baseline * 0.005
    if (yMax - yMin < minRange) {
        const center = (yMin + yMax) / 2
        yMin = center - minRange / 2
        yMax = center + minRange / 2
    }
    const padding = (yMax - yMin) * 0.08
    return { yMin: yMin - padding, yMax: yMax + padding }
}
function yToSvg(value, range) { return SPARK_H - ((value - range.yMin) / (range.yMax - range.yMin)) * SPARK_H }
function sparkPath(sp, key = 'prices') {
    if (!sp || !sp[key] || !sp[key].length) return ''
    const range = computeYRange(sp); if (!range) return ''
    const arr = sp[key]; const n = arr.length
    let path = ''
    for (let i = 0; i < n; i++) {
        const x = n > 1 ? (i / (n - 1)) * SPARK_W : SPARK_W / 2
        const y = yToSvg(arr[i], range)
        path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' '
    }
    return path
}
function baselineY(sp) {
    if (!sp || sp.preClose == null) return SPARK_H / 2
    const range = computeYRange(sp)
    return range ? yToSvg(sp.preClose, range) : SPARK_H / 2
}
function sparkAreaPath(sp) {
    if (!sp || !sp.prices || !sp.prices.length) return ''
    const range = computeYRange(sp); if (!range) return ''
    const arr = sp.prices; const n = arr.length
    let path = ''
    for (let i = 0; i < n; i++) {
        const x = n > 1 ? (i / (n - 1)) * SPARK_W : SPARK_W / 2
        const y = yToSvg(arr[i], range)
        path += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1) + ' '
    }
    const lastX = n > 1 ? SPARK_W : SPARK_W / 2
    path += `L${lastX.toFixed(1)},${SPARK_H} L0,${SPARK_H} Z`
    return path
}
function sparkColor(sp) {
    if (!sp || !sp.prices || !sp.prices.length || sp.preClose == null) return '#cbd5e1'
    const last = sp.prices[sp.prices.length - 1]
    if (last > sp.preClose) return '#dc2626'
    if (last < sp.preClose) return '#059669'
    return '#94a3b8'
}
function endMarkerPath(sp) {
    if (!sp || !sp.prices || !sp.prices.length || sp.preClose == null) return ''
    const range = computeYRange(sp); if (!range) return ''
    const baseline = sp.preClose
    const last = sp.prices[sp.prices.length - 1]
    const x = SPARK_W
    const y = yToSvg(last, range)
    if (last > baseline) return `M${x - 2.8},${y + 2.2} L${x},${y - 2.8} L${x + 2.8},${y + 2.2} Z`
    if (last < baseline) return `M${x - 2.8},${y - 2.2} L${x},${y + 2.8} L${x + 2.8},${y - 2.2} Z`
    return `M${x},${y - 2.2} L${x + 2.2},${y} L${x},${y + 2.2} L${x - 2.2},${y} Z`
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
watch(positions, () => {
    refreshQuotes()
    refreshSparklines()
}, { deep: false })

// 智能刷新：行情基础 10s，sparkline 60s（变化慢，且每只独立缓存）
useSmartRefresh(refreshQuotes,     { baseInterval: 10_000, immediate: false })
useSmartRefresh(refreshSparklines, { baseInterval: 60_000, immediate: false })

onMounted(async () => {
    await loadAccounts()
    await loadPositions()
    loadTradeFees()  // 加载用户的交易费率配置（异步，不阻塞）
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
                        <th class="px-[8px] py-[10px] font-normal text-center w-[150px]">今日走势</th>
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
                        <th class="px-[10px] py-[10px] font-normal text-right w-[80px] cursor-pointer select-none hover:text-[#dc2626] transition group"
                            @click="handleSort('ytdPct')">
                            <div class="leading-tight">
                                年初至今
                                <span class="ml-[2px] text-[9px] align-middle tabular-nums"
                                      :class="sortDirFor('ytdPct') ? 'text-[#dc2626]' : 'text-[#ccc] opacity-0 group-hover:opacity-100'">
                                    {{ sortDirFor('ytdPct') === 'asc' ? '▲' : '▼' }}
                                </span>
                            </div>
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
                        <td :colspan="isSummary ? 9 : 10" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</td>
                    </tr>
                    <tr v-else-if="!positions.length">
                        <td :colspan="isSummary ? 9 : 10" class="py-[80px] text-center text-[#aaa] text-[13px]">
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
                        <td :colspan="isSummary ? 9 : 10" class="py-[60px] text-center text-[#aaa] text-[13px]">
                            未匹配到"{{ searchQuery }}"，
                            <button @click="searchQuery = ''" class="text-[#dc2626] hover:underline">清空搜索</button>
                        </td>
                    </tr>

                    <tr v-for="p in filteredPositions" :key="p.code"
                        @dblclick="openStockChart(p.code, p.name, filteredPositions)"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] transition-colors group cursor-pointer"
                        title="双击查看 K 线 · 左侧列表可切换">

                        <!-- 股票名称 + 代码 -->
                        <td class="px-[12px] py-[8px] align-middle">
                            <div class="flex items-center gap-[4px] min-w-0">
                                <div class="text-[14px] font-bold text-[#111] leading-tight truncate flex-1">{{ p.name || quotes[p.code]?.name || '—' }}</div>
                                <button v-if="ext.showTdxButton.value"
                                        @click.stop="ext.jumpTo('tdx', p.code)"
                                        title="在通达信打开"
                                        class="shrink-0 text-[11px] font-semibold px-[8px] py-[3px] rounded-[4px]
                                               text-[#0891b2] bg-[#ecfeff] hover:bg-[#a5f3fc] hover:text-[#0e7490]
                                               active:scale-95 active:bg-[#67e8f9]
                                               border border-[#a5f3fc] transition-all duration-100 shadow-sm">
                                    📡 TDX
                                </button>
                                <button v-if="ext.showThsButton.value"
                                        @click.stop="ext.jumpTo('ths', p.code)"
                                        title="在同花顺打开"
                                        class="shrink-0 text-[11px] font-semibold px-[8px] py-[3px] rounded-[4px]
                                               text-[#7c3aed] bg-[#f5f3ff] hover:bg-[#ddd6fe] hover:text-[#5b21b6]
                                               active:scale-95 active:bg-[#c4b5fd]
                                               border border-[#ddd6fe] transition-all duration-100 shadow-sm">
                                    📡 THS
                                </button>
                            </div>
                            <div class="text-[11px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums">
                                {{ marketPrefix(p.code) }}{{ p.code }}
                            </div>
                        </td>

                        <!-- 今日走势：迷你分时 SVG -->
                        <td class="px-[8px] py-[6px] align-middle">
                            <div class="flex items-center justify-center h-[50px]">
                                <svg v-if="sparklines[p.code]?.prices?.length"
                                     viewBox="0 0 140 50" preserveAspectRatio="none"
                                     class="w-[140px] h-[50px]"
                                     style="overflow: visible">
                                    <defs>
                                        <linearGradient :id="'pos-spark-fill-' + p.code" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="0%" :stop-color="sparkColor(sparklines[p.code])" stop-opacity="0.20"/>
                                            <stop offset="100%" :stop-color="sparkColor(sparklines[p.code])" stop-opacity="0"/>
                                        </linearGradient>
                                    </defs>
                                    <path :d="sparkAreaPath(sparklines[p.code])"
                                          :fill="'url(#pos-spark-fill-' + p.code + ')'" stroke="none"/>
                                    <line x1="0" :y1="baselineY(sparklines[p.code])"
                                          x2="140" :y2="baselineY(sparklines[p.code])"
                                          stroke="#94a3b8" stroke-width="0.6" stroke-dasharray="2.5,2.5"
                                          opacity="0.7" vector-effect="non-scaling-stroke"/>
                                    <path :d="sparkPath(sparklines[p.code], 'avgPrices')"
                                          stroke="#ca8a04" stroke-width="1.2" fill="none"
                                          stroke-linejoin="round" stroke-linecap="round"
                                          vector-effect="non-scaling-stroke"/>
                                    <path :d="sparkPath(sparklines[p.code])"
                                          :stroke="sparkColor(sparklines[p.code])"
                                          stroke-width="1.5" fill="none"
                                          stroke-linejoin="round" stroke-linecap="round"
                                          vector-effect="non-scaling-stroke"/>
                                    <path :d="endMarkerPath(sparklines[p.code])"
                                          :fill="sparkColor(sparklines[p.code])"
                                          stroke="white" stroke-width="0.5"
                                          vector-effect="non-scaling-stroke"/>
                                </svg>
                                <span v-else class="text-[11px] text-[#ccc]">—</span>
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

                        <!-- 年初至今涨幅（基于股价本身，不涉及成本）-->
                        <td class="px-[10px] py-[6px] text-right tabular-nums align-middle">
                            <span class="text-[12px] font-medium"
                                  :class="colorClassOf(quotes[p.code]?.ytdPct)">
                                {{ fmtPercent(quotes[p.code]?.ytdPct) }}
                            </span>
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
                                <button @click="startSimulate(p)" class="text-[#666] hover:text-[#16a34a] transition" title="加减仓模拟">
                                    <!-- 计算器图标：加号 + 减号 -->
                                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2">
                                        <rect x="4" y="3" width="12" height="14" rx="1.5"/>
                                        <path stroke-linecap="round" d="M7 7h6M10 10V7"/>
                                        <path stroke-linecap="round" d="M7 13h6"/>
                                    </svg>
                                </button>
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

    <!-- ============ 加减仓模拟 Modal（可拖动）============ -->
    <div v-if="simulating" class="fixed inset-0 bg-black/20 z-50 flex items-center justify-center"
         @mousedown="onSimOverlayMouseDown"
         @click="onSimOverlayClick">
        <div class="bg-white rounded-[6px] w-[540px] shadow-[0_10px_40px_rgba(0,0,0,0.15)] overflow-hidden"
             :style="{ transform: `translate(${simDragOffset.x}px, ${simDragOffset.y}px)` }">
            <!-- 可拖拽的标题栏（按住这里移动弹窗）-->
            <div class="px-[20px] pt-[16px] pb-[8px] cursor-grab active:cursor-grabbing select-none bg-gradient-to-b from-[#fafafa] to-white border-b border-[#f0f0f0]"
                 @mousedown="onSimDragStart">
                <div class="flex items-center justify-between">
                    <div class="text-[14px] font-bold text-[#111]">
                        加减仓模拟 — {{ simulating.position.name || simulating.position.code }}
                    </div>
                    <svg class="w-[14px] h-[14px] text-[#bbb]" viewBox="0 0 20 20" fill="currentColor" title="可拖动">
                        <circle cx="7" cy="6" r="1.2"/><circle cx="7" cy="10" r="1.2"/><circle cx="7" cy="14" r="1.2"/>
                        <circle cx="13" cy="6" r="1.2"/><circle cx="13" cy="10" r="1.2"/><circle cx="13" cy="14" r="1.2"/>
                    </svg>
                </div>
                <div class="text-[11px] text-[#999] mt-[2px]">先试算，再决定是否更新持仓记录</div>
            </div>

            <div class="px-[20px] py-[16px]">

            <!-- 操作模式切换 -->
            <div class="flex gap-[4px] bg-[#f5f5f5] rounded-[4px] p-[3px] mb-[14px]">
                <button @click="simulating.mode = 'buy'"
                        class="flex-1 text-[12px] py-[6px] rounded-[3px] font-semibold transition"
                        :class="simulating.mode === 'buy' ? 'bg-white text-[#dc2626] shadow-sm' : 'text-[#666] hover:text-[#111]'">
                    + 加仓
                </button>
                <button @click="simulating.mode = 'sell'"
                        class="flex-1 text-[12px] py-[6px] rounded-[3px] font-semibold transition"
                        :class="simulating.mode === 'sell' ? 'bg-white text-[#059669] shadow-sm' : 'text-[#666] hover:text-[#111]'">
                    − 减仓
                </button>
            </div>

            <!-- 当前状态（模拟态）-->
            <div class="bg-[#fafafa] border border-[#eeeeee] rounded-[4px] px-[12px] py-[10px] mb-[10px]">
                <div class="flex items-center justify-between mb-[6px]">
                    <div class="text-[11px] text-[#888]">
                        {{ simulating.steps.length === 0 ? '当前持仓（原始）' : `模拟态（已走 ${simulating.steps.length} 步）` }}
                    </div>
                    <button v-if="simulating.steps.length > 0"
                            @click="resetSimulation"
                            class="text-[11px] text-[#999] hover:text-[#dc2626] transition">
                        重置到原始
                    </button>
                </div>
                <div class="grid grid-cols-5 gap-[6px] text-center">
                    <div>
                        <div class="text-[10px] text-[#999]">持股</div>
                        <div class="text-[13px] font-bold text-[#111] tabular-nums">
                            {{ simulating.simShares === 0 ? '清仓' : fmtShares(simulating.simShares) }}
                        </div>
                    </div>
                    <div>
                        <div class="text-[10px] text-[#999]">成本价</div>
                        <div class="text-[13px] font-bold text-[#111] tabular-nums">
                            {{ simulating.simShares === 0 ? '—' : simulating.simCost?.toFixed(3) }}
                        </div>
                    </div>
                    <div>
                        <div class="text-[10px] text-[#999]">现价</div>
                        <div class="text-[13px] font-bold tabular-nums"
                             :class="colorClassOf(quotes[simulating.position.code]?.changePct)">
                            {{ fmtPrice(quotes[simulating.position.code]?.price) }}
                        </div>
                    </div>
                    <!-- 浮动盈亏：按现价折算剩余持仓的未实现盈亏 -->
                    <div>
                        <div class="text-[10px] text-[#999]">浮动盈亏</div>
                        <div class="text-[13px] font-bold tabular-nums"
                             :class="colorClassOf(simUnrealized?.amount)">
                            {{ simUnrealized ? fmtSignedAmount(simUnrealized.amount) : '—' }}
                        </div>
                        <div v-if="simUnrealized && simUnrealized.pct != null"
                             class="text-[10px] tabular-nums"
                             :class="colorClassOf(simUnrealized.pct)">
                            {{ fmtPercent(simUnrealized.pct) }}
                        </div>
                    </div>
                    <div>
                        <div class="text-[10px] text-[#999]" title="执行本方案 vs 什么都不做，按现价折算的净收益（已扣费）">本方案增量</div>
                        <div class="text-[13px] font-bold tabular-nums"
                             :class="colorClassOf(simNetGain)">
                            {{ simNetGain == null ? '—' : fmtSignedAmount(simNetGain) }}
                        </div>
                        <div v-if="simulating.steps.length > 0" class="text-[10px] text-[#999] tabular-nums">
                            费用 ¥{{ simTotalFees.toFixed(2) }}
                        </div>
                    </div>
                </div>
            </div>

            <!-- 做T 收益（混合 加+减 时浮现）-->
            <div v-if="simRoundTrip"
                 class="mb-[10px] flex items-center justify-between px-[12px] py-[8px] bg-gradient-to-r from-[#fffbeb] to-[#fff] border border-[#fde68a] rounded-[4px]">
                <div class="flex items-center gap-[8px]">
                    <span class="text-[10px] font-bold text-[#92400e] bg-[#fde68a] px-[6px] py-[1px] rounded-sm">做 T</span>
                    <span class="text-[11px] text-[#888] tabular-nums">
                        匹配 <b class="text-[#111]">{{ simRoundTrip.shares.toLocaleString() }}</b> 股 ·
                        均卖 <b class="text-[#dc2626]">{{ simRoundTrip.avgSell.toFixed(3) }}</b> /
                        均买 <b class="text-[#059669]">{{ simRoundTrip.avgBuy.toFixed(3) }}</b>
                    </span>
                </div>
                <span class="text-[14px] font-bold tabular-nums" :class="colorClassOf(simRoundTrip.profit)">
                    {{ fmtSignedAmount(simRoundTrip.profit) }}
                </span>
            </div>

            <!-- 已规划步骤列表 -->
            <div v-if="simulating.steps.length > 0"
                 class="mb-[12px] max-h-[120px] overflow-auto custom-scrollbar border border-[#eeeeee] rounded-[4px]">
                <div class="px-[10px] py-[6px] bg-[#fafafa] text-[11px] text-[#888] border-b border-[#eeeeee] sticky top-0 flex items-center justify-between">
                    <span>已规划 {{ simulating.steps.length }} 步</span>
                    <button @click="undoLastStep"
                            class="text-[11px] text-[#999] hover:text-[#dc2626] transition">撤销最后一步</button>
                </div>
                <div v-for="(step, i) in simulating.steps" :key="i"
                     class="flex items-center justify-between px-[10px] py-[5px] text-[11px] tabular-nums border-b border-[#f5f5f5] last:border-b-0">
                    <span class="flex items-center gap-[6px] min-w-0">
                        <span class="text-[#999]">#{{ i + 1 }}</span>
                        <span class="text-[#999] tabular-nums">{{ step.date ? step.date.slice(5) : '—' }}</span>
                        <span class="font-semibold" :class="step.mode === 'buy' ? 'text-[#dc2626]' : 'text-[#059669]'">
                            {{ step.mode === 'buy' ? '加仓' : '减仓' }}
                        </span>
                        <span class="text-[#111] truncate">{{ step.shares.toLocaleString() }} @ {{ step.price.toFixed(3) }}</span>
                        <span v-if="step.feeTotal" class="text-[#aaa] text-[10px]" :title="`佣 ${step.feeBreakdown?.commission?.toFixed(2)} + 过户 ${step.feeBreakdown?.transfer?.toFixed(2)}${step.mode === 'sell' ? ' + 印花 ' + step.feeBreakdown?.stamp?.toFixed(2) : ''}`">
                            费 {{ step.feeTotal.toFixed(2) }}
                        </span>
                    </span>
                    <span v-if="step.mode === 'sell'" :class="colorClassOf(step.realizedDelta)" class="font-semibold shrink-0">
                        {{ fmtSignedAmount(step.realizedDelta) }}
                    </span>
                    <span v-else class="text-[#999] shrink-0">
                        成本 {{ (step.costDelta >= 0 ? '+' : '') + step.costDelta.toFixed(3) }}
                    </span>
                </div>
            </div>

            <!-- 操作输入 -->
            <div class="flex gap-[8px] mb-[14px]">
                <label class="flex flex-col gap-[4px] w-[140px] shrink-0">
                    <span class="text-[12px] text-[#666]">日期 (T+1 校验)</span>
                    <input v-model="simulating.date" type="date"
                           class="text-[13px] px-[8px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums w-full">
                </label>
                <label class="flex flex-col gap-[4px] flex-1 min-w-0">
                    <span class="text-[12px] text-[#666]">{{ simulating.mode === 'buy' ? '买入' : '卖出' }}价</span>
                    <input v-model="simulating.price" type="number" step="0.001"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums w-full">
                </label>
                <label class="flex flex-col gap-[4px] flex-1 min-w-0">
                    <span class="text-[12px] text-[#666]">{{ simulating.mode === 'buy' ? '买入' : '卖出' }}数量</span>
                    <input v-model="simulating.shares" type="number" step="100" min="100"
                           :max="simulating.mode === 'sell' ? simulating.position.shares : undefined"
                           placeholder="100 倍数"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums w-full">
                </label>
            </div>

            <!-- 操作后结果预览 -->
            <div v-if="simulationResult && simulationResult.error"
                 class="bg-[#fef2f2] border border-[#fecaca] text-[#dc2626] text-[12px] px-[12px] py-[8px] rounded-[4px] mb-[14px]">
                {{ simulationResult.error }}
            </div>

            <div v-else-if="simulationResult"
                 class="border rounded-[4px] px-[12px] py-[10px] mb-[14px]"
                 :class="simulationResult.mode === 'buy'
                    ? 'bg-[#fff0f0] border-[#fecaca]'
                    : 'bg-[#f0fdf4] border-[#bbf7d0]'">
                <div class="text-[11px] mb-[6px]"
                     :class="simulationResult.mode === 'buy' ? 'text-[#dc2626]' : 'text-[#059669]'">
                    这步操作后
                    <span v-if="simulationResult.isFullClose" class="ml-[4px] font-bold">（清仓）</span>
                </div>

                <!-- 加仓结果 -->
                <template v-if="simulationResult.mode === 'buy'">
                    <div class="grid grid-cols-3 gap-[8px] text-center">
                        <div>
                            <div class="text-[10px] text-[#999]">新持股</div>
                            <div class="text-[14px] font-bold text-[#111] tabular-nums">{{ fmtShares(simulationResult.newShares) }}</div>
                        </div>
                        <div>
                            <div class="text-[10px] text-[#999]">新成本价</div>
                            <div class="text-[14px] font-bold text-[#111] tabular-nums">{{ simulationResult.newCost.toFixed(3) }}</div>
                        </div>
                        <div>
                            <div class="text-[10px] text-[#999]">成本变动</div>
                            <div class="text-[13px] font-bold tabular-nums"
                                 :class="colorClassOf(simulationResult.costDelta)">
                                {{ (simulationResult.costDelta >= 0 ? '+' : '') + simulationResult.costDelta.toFixed(3) }}
                            </div>
                        </div>
                    </div>
                    <div class="text-[11px] text-[#888] mt-[8px] flex justify-between">
                        <span>
                            手续费 <span class="font-bold text-[#dc2626] tabular-nums">¥{{ simulationResult.fees.total.toFixed(2) }}</span>
                            <span class="text-[10px] text-[#aaa] ml-[4px]">
                                （佣 {{ simulationResult.fees.commission.toFixed(2) }} + 过户 {{ simulationResult.fees.transfer.toFixed(2) }}）
                            </span>
                        </span>
                        <span>
                            投入资金 <span class="font-bold text-[#111] tabular-nums">{{ fmtAmount(simulationResult.cashUsed) }}</span>
                        </span>
                    </div>
                </template>

                <!-- 减仓结果 -->
                <template v-else>
                    <div class="grid grid-cols-3 gap-[8px] text-center">
                        <div>
                            <div class="text-[10px] text-[#999]">剩余持股</div>
                            <div class="text-[14px] font-bold text-[#111] tabular-nums">
                                {{ simulationResult.isFullClose ? '已清仓' : fmtShares(simulationResult.newShares) }}
                            </div>
                        </div>
                        <div>
                            <div class="text-[10px] text-[#999]">剩余成本价</div>
                            <div class="text-[14px] font-bold text-[#111] tabular-nums">
                                {{ simulationResult.isFullClose ? '—' : simulationResult.newCost.toFixed(3) }}
                            </div>
                        </div>
                        <div>
                            <div class="text-[10px] text-[#999]">已实现盈亏</div>
                            <div class="text-[14px] font-bold tabular-nums"
                                 :class="colorClassOf(simulationResult.realizedProfit)">
                                {{ fmtSignedAmount(simulationResult.realizedProfit) }}
                            </div>
                        </div>
                    </div>
                    <div class="text-[11px] mt-[8px] flex flex-col gap-[3px]">
                        <div class="flex justify-between">
                            <span class="text-[#888]">
                                手续费 <span class="font-bold text-[#dc2626] tabular-nums">¥{{ simulationResult.fees.total.toFixed(2) }}</span>
                                <span class="text-[10px] text-[#aaa] ml-[4px]">
                                    （佣 {{ simulationResult.fees.commission.toFixed(2) }} + 过户 {{ simulationResult.fees.transfer.toFixed(2) }} + 印花 {{ simulationResult.fees.stamp.toFixed(2) }}）
                                </span>
                            </span>
                            <span class="text-[#888]">
                                回收资金 <span class="font-bold text-[#111] tabular-nums">{{ fmtAmount(simulationResult.cashGained) }}</span>
                            </span>
                        </div>
                        <div class="text-right">
                            <span :class="colorClassOf(simulationResult.realizedPct)" class="font-semibold tabular-nums">
                                收益率 {{ fmtPercent(simulationResult.realizedPct) }}（已扣费）
                            </span>
                        </div>
                    </div>
                </template>
            </div>

            <div v-else class="bg-[#fafafa] border border-dashed border-[#e5e5e5] text-[#aaa] text-[12px] px-[12px] py-[14px] rounded-[4px] mb-[14px] text-center">
                输入价格和数量后自动计算
            </div>

            <div v-if="simFormError" class="text-[12px] text-[#dc2626] mb-[10px]">{{ simFormError }}</div>

            <!-- 费率设置（折叠）-->
            <div class="mb-[12px] border-t border-[#f0f0f0] pt-[10px]">
                <div class="flex items-center justify-between text-[11px]">
                    <button @click="showFeesEditor = !showFeesEditor"
                            class="text-[#2563eb] hover:underline">
                        {{ showFeesEditor ? '▾' : '▸' }} 费率设置
                    </button>
                    <span class="text-[#999] text-[10px]" v-if="!showFeesEditor">
                        佣金 万{{ (tradeFees.commission * 10000).toFixed(3) }} ·
                        过户 万{{ (tradeFees.transfer * 10000).toFixed(3) }} ·
                        印花 万{{ (tradeFees.stamp * 10000).toFixed(3) }}
                    </span>
                    <span v-if="feesSaveStatus" class="text-[#059669] text-[10px]">{{ feesSaveStatus }}</span>
                </div>
                <div v-if="showFeesEditor" class="mt-[8px] bg-[#fafafa] border border-[#eeeeee] rounded p-[10px]">
                    <div class="grid grid-cols-3 gap-[8px]">
                        <label class="flex flex-col gap-[2px]">
                            <span class="text-[10px] text-[#666]">佣金率（双向）</span>
                            <input v-model.number="tradeFees.commission" type="number" step="0.0000001" min="0"
                                   class="text-[12px] px-[6px] py-[4px] border border-[#e5e5e5] rounded outline-none focus:border-[#dc2626] tabular-nums">
                        </label>
                        <label class="flex flex-col gap-[2px]">
                            <span class="text-[10px] text-[#666]">过户费率（双向）</span>
                            <input v-model.number="tradeFees.transfer" type="number" step="0.0000001" min="0"
                                   class="text-[12px] px-[6px] py-[4px] border border-[#e5e5e5] rounded outline-none focus:border-[#dc2626] tabular-nums">
                        </label>
                        <label class="flex flex-col gap-[2px]">
                            <span class="text-[10px] text-[#666]">印花税（仅卖出）</span>
                            <input v-model.number="tradeFees.stamp" type="number" step="0.0000001" min="0"
                                   class="text-[12px] px-[6px] py-[4px] border border-[#e5e5e5] rounded outline-none focus:border-[#dc2626] tabular-nums">
                        </label>
                    </div>
                    <div class="flex items-center justify-between mt-[8px]">
                        <div class="text-[10px] text-[#999]">
                            填小数。示例：0.0000869 = 万分之 0.869
                        </div>
                        <div class="flex gap-[6px]">
                            <button @click="resetFeesToDefault"
                                    class="text-[10px] text-[#999] hover:text-[#dc2626]">恢复默认</button>
                            <button @click="saveTradeFees"
                                    class="text-[10px] font-bold text-white bg-[#dc2626] px-[8px] py-[3px] rounded hover:bg-[#991b1b]">
                                保存
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <div class="flex items-center justify-end gap-[8px]">
                <button @click="cancelSimulate"
                        class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-[#f5f5f5]">
                    取消
                </button>
                <!-- 继续叠一步：把本步加入规划，不写 DB，清空输入准备下一步 -->
                <button @click="commitStep"
                        :disabled="!simulationResult || !!simulationResult?.error"
                        class="text-[12px] px-[14px] py-[6px] text-[#2563eb] border border-[#93c5fd] rounded-[4px] hover:bg-[#eff6ff] disabled:text-[#ccc] disabled:border-[#e5e5e5] disabled:cursor-not-allowed transition"
                        title="把这一步加入规划，继续叠加下一步（不写入持仓）">
                    ＋ 继续叠一步
                </button>
                <!-- 执行：把所有已规划步骤的净效果写入持仓 -->
                <button @click="executeSimulation"
                        :disabled="simulating.steps.length === 0 && (!simulationResult || !!simulationResult?.error)"
                        class="text-[12px] font-bold text-white px-[14px] py-[6px] rounded-[4px] transition disabled:bg-[#ccc] disabled:cursor-not-allowed"
                        :class="simulating.simShares === 0 || (simulationResult && simulationResult.isFullClose && simulating.steps.length === 0)
                            ? 'bg-[#059669] hover:bg-[#047857]'
                            : 'bg-[#dc2626] hover:bg-[#991b1b]'">
                    {{ executeButtonLabel }}
                </button>
            </div>
            </div>  <!-- /px-20 py-16 内容区 -->
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
