<script setup>
/**
 * 回测历史 View —— Phase 1 Day 4
 *
 * 展示 bt.runAll / bt.gridSearch / bt.runV0 / bt.runExitExperiment 跑完后存盘的历史 run。
 *
 * 用途：
 *   - 看不同时间点 / 不同参数下的 detector 表现对比
 *   - 看 grid search 的 Top combos
 *   - 给历史 run 加备注（"用这次的参数当默认"等）
 *
 * 注：回测通过 DevTools console 跑（dev-only，仅 admin 看得到本 view）；
 * 此 view 只展示结果，不发起新回测。
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api/client'
import { pushSuccess, pushError } from '../composables/useNotifications'
import { confirmDialog } from '../composables/useConfirm'
import { useBacktestRunner } from '../composables/useBacktestRunner'

const runs = ref([])
const loading = ref(false)
const selectedRun = ref(null)
const selectedDetail = ref(null)

// 新建回测面板
const runner = useBacktestRunner()
const formExpanded = ref(false)
const form = ref({
    boards: ['sh_main', 'sz_main', 'sme'],
    holdDays: 7,
    trainMl:  false,           // 顺手训练 ML 模型（相当于原"智能重训"）
    mlLabel:  't7Profitable',  // 仅 trainMl=true 时用
})
const BOARD_OPTIONS = [
    { key: 'sh_main', label: '沪市主板', desc: '600/601/603/605' },
    { key: 'sz_main', label: '深市主板', desc: '000/001/003' },
    { key: 'sme',     label: '中小板',   desc: '002（已并入深市）' },
    { key: 'gem',     label: '创业板',   desc: '300/301（±20%）' },
    { key: 'star',    label: '科创板',   desc: '688（±20%）' },
]
function toggleBoard(key) {
    const idx = form.value.boards.indexOf(key)
    if (idx >= 0) form.value.boards.splice(idx, 1)
    else form.value.boards.push(key)
}
async function startNewBacktest() {
    if (!form.value.boards.length) {
        pushError('请至少选一个板块')
        return
    }
    if (!form.value.holdDays || form.value.holdDays < 1) {
        pushError('持有天数必须 ≥ 1')
        return
    }

    // 勾了"顺手训 ML" → 走完整的 backtest + dataset 落盘 + 训练 + 激活流程
    if (form.value.trainMl) {
        // 同步 smartRetrain 状态并触发（复用已有的 startSmartRetrain 实现）
        smartRetrain.value.boards = [...form.value.boards]
        smartRetrain.value.holdDays = form.value.holdDays
        smartRetrain.value.label = form.value.mlLabel
        formExpanded.value = false
        await startSmartRetrain()
        return
    }

    // 否则只跑 backtest（不动 ML）
    const res = await runner.run({
        boards:   form.value.boards,
        holdDays: form.value.holdDays,
    })
    if (res.ok) {
        pushSuccess(`回测完成，已保存 run #${res.runId}`)
        formExpanded.value = false
        await loadRuns()
        if (res.runId) selectRun(res.runId)
    } else {
        pushError(`回测失败: ${res.error || '未知'}`)
    }
}

// ---- C2: 多选（对比 + 批量删除）----
const compareSelected = ref([])    // run ids
const compareDetails = ref([])     // 拉到的对比数据
const compareMode = ref(false)     // true: 显示对比表 / false: 显示单 run 详情
const compareLoading = ref(false)
const batchDeleting = ref(false)   // 批量删除进行中

function toggleCompare(runId, e) {
    e?.stopPropagation()
    const idx = compareSelected.value.indexOf(runId)
    if (idx >= 0) compareSelected.value.splice(idx, 1)
    else if (compareSelected.value.length < 4) compareSelected.value.push(runId)
    else pushError('最多对比 4 个 run')
}
function clearCompare() {
    compareSelected.value = []
    compareMode.value = false
    compareDetails.value = []
}
async function startCompare() {
    if (compareSelected.value.length < 2) {
        pushError('至少选 2 个 run')
        return
    }
    compareLoading.value = true
    compareMode.value = true
    selectedDetail.value = null
    selectedRun.value = null
    try {
        const detailRes = await Promise.all(compareSelected.value.map(id => api.getBacktestRun(id)))
        compareDetails.value = detailRes.map((r, i) => r?.ok ? r.data : { id: compareSelected.value[i], error: r?.error || '加载失败' })
    } catch (e) {
        pushError(`对比加载失败: ${e}`)
    } finally {
        compareLoading.value = false
    }
}

// 对比表 metric 行抽取（baseline = 第一个）
function getMetric(run, path) {
    if (!run || !run.summary) return null
    const segs = path.split('.')
    let cur = run.summary
    for (const s of segs) {
        cur = cur?.[s]
        if (cur == null) return null
    }
    return cur
}
const compareMetrics = computed(() => {
    if (!compareMode.value || compareDetails.value.length < 2) return []
    const rows = [
        { label: '样本数',              hint: '本次回测扫描到的股票数量', path: 'sample_size',           topLevel: true, kind: 'count' },
        { label: '整体胜率',            hint: '所有 trade 的赢面占比（盈利 trade / 总 trade）', path: 'overall.winRate', kind: 'rate' },
        { label: '整体平均收益',        hint: '所有 trade 的平均收益率', path: 'overall.avgReturn',   kind: 'pct' },
        { label: '⭐⭐⭐ 评级 胜率',       hint: 'gradeFreshSignal 评为 strong（最强）的 trade 胜率', path: 'byGrade.strong.winRate', kind: 'rate' },
        { label: '⭐⭐ 评级 胜率',         hint: 'gradeFreshSignal 评为 medium（中等）的 trade 胜率', path: 'byGrade.medium.winRate', kind: 'rate' },
        { label: '⭐ 评级 胜率',           hint: 'gradeFreshSignal 评为 fail（弱）的 trade 胜率', path: 'byGrade.fail.winRate',   kind: 'rate' },
        { label: 'N+1 强确认 胜率',     hint: '突破次日（N+1）爆量收阳收高的 strong 子集胜率', path: 'byBreakoutConfirm.strong.winRate', kind: 'rate' },
        { label: 'N+1 中确认 胜率',     hint: '突破次日（N+1）小阳/平稳 medium 子集胜率',     path: 'byBreakoutConfirm.medium.winRate', kind: 'rate' },
        { label: '周线确认 胜率',       hint: '同时满足周线趋势（周 MA20 上行）的 trade 胜率', path: 'byWeekly.confirmed.winRate', kind: 'rate' },
    ]
    return rows.map(r => {
        const vals = compareDetails.value.map(d => {
            if (r.topLevel) return d?.[r.path] ?? null
            return getMetric(d, r.path)
        })
        return { ...r, vals }
    })
})

function fmtMetric(v, kind) {
    if (v == null || !Number.isFinite(v)) return '—'
    if (kind === 'rate')  return (v * 100).toFixed(1) + '%'
    if (kind === 'pct')   return v.toFixed(2) + '%'
    if (kind === 'count') return String(v)
    return String(v)
}
// vs baseline 差值，2pp 阈值；返回 { diff, dir, cls, sym }
function diffVsBaseline(v, base, kind) {
    if (v == null || base == null || !Number.isFinite(v) || !Number.isFinite(base)) {
        return { diff: null, cls: '', sym: '' }
    }
    if (kind === 'count') {
        const d = v - base
        return { diff: d, cls: d === 0 ? 'text-[#94a3b8]' : '', sym: '' }
    }
    const isRate = kind === 'rate'
    const d = isRate ? (v - base) * 100 : (v - base)
    const threshold = 2.0   // 2pp / 2%
    const abs = Math.abs(d)
    let cls = 'text-[#64748b]'
    let sym = '→'
    if (d >= threshold) {       // 进步
        cls = 'text-[#15803d] font-bold'
        sym = '↑'
    } else if (d <= -threshold) {  // 退步
        cls = 'text-[#dc2626] font-bold'
        sym = '↓'
    } else if (abs >= 0.5) {
        sym = d > 0 ? '↗' : '↘'
    }
    const sign = d >= 0 ? '+' : ''
    const txt = `${sign}${d.toFixed(1)}${isRate ? 'pp' : '%'}`
    return { diff: d, cls, sym, txt }
}

async function loadRuns() {
    loading.value = true
    try {
        const res = await api.listBacktestRuns(50)
        if (res.ok) runs.value = res.data || []
        else pushError(res.error || '加载回测历史失败')
    } catch (e) {
        pushError(`加载失败：${e}`)
    } finally {
        loading.value = false
    }
}

async function selectRun(runId) {
    // 选单条 run 时退出对比模式
    compareMode.value = false
    selectedRun.value = runId
    selectedDetail.value = null
    try {
        const res = await api.getBacktestRun(runId)
        if (res.ok) selectedDetail.value = res.data
    } catch (e) {
        pushError(`详情加载失败：${e}`)
    }
}

async function deleteRun(runId) {
    // 查这个 run 有没有关联文件
    const target = runs.value.find(r => r.id === runId) || selectedDetail.value
    const hasFiles = !!(target?.produced_dataset || target?.produced_model)

    // 第 1 步：确认要删
    const ok = await confirmDialog({
        title: '删除回测记录',
        message: hasFiles
            ? `Run #${runId} 关联了：` +
              (target.produced_dataset ? `\n· 数据集 ${target.produced_dataset}` : '') +
              (target.produced_model ? `\n· 模型 ${target.produced_model.split(/[\\/]/).pop()}` : '') +
              `\n\n点"删除"会先问你是否清理这些文件。`
            : `确定删除 run #${runId} 吗？`,
        confirmText: '删除',
        danger: true,
    })
    if (!ok) return

    // 第 2 步：若关联了文件，再问是否一并清理
    let deleteFiles = false
    if (hasFiles) {
        deleteFiles = await confirmDialog({
            title: '一并删除关联文件？',
            message:
                '记录已确定删除。是否同时清理关联的 dataset / model 文件？' +
                '\n\n· 点"是" → 删 DB + 文件（激活中的模型会自动跳过）' +
                '\n· 点"否" → 仅删 DB 记录，文件保留可复用',
            confirmText: '是，一并删除',
            cancelText:  '否，保留文件',
            danger: true,
        })
    }

    try {
        const res = await api.deleteBacktestRun(runId, deleteFiles)
        const payload = res?.data || res
        if (res.ok && payload.ok) {
            const n = (payload.deleted_files || []).length
            if (deleteFiles && n > 0) pushSuccess(`已删除记录 + ${n} 个文件`)
            else if (deleteFiles && payload.file_errors?.length) pushError(`记录已删，但文件清理失败: ${payload.file_errors.join('; ')}`)
            else pushSuccess('已删除')
            if (selectedRun.value === runId) {
                selectedRun.value = null
                selectedDetail.value = null
            }
            await loadRuns()
            await loadMlHealth()
        }
    } catch (e) { pushError(String(e)) }
}

async function deleteSelectedRuns() {
    const ids = [...compareSelected.value]
    if (!ids.length) return

    // 看看选中的 run 里有几个带关联文件
    const targets = ids.map(id => runs.value.find(r => r.id === id)).filter(Boolean)
    const withFiles = targets.filter(t => t.produced_dataset || t.produced_model)

    // 第 1 步：确认
    const ok = await confirmDialog({
        title: `批量删除 ${ids.length} 条回测`,
        message:
            `将删除 ${ids.length} 条回测记录：\n` +
            targets.slice(0, 6).map(t => `· #${t.id} ${t.run_type} ${fmtTime(t.run_at)?.slice(5, 16)}`).join('\n') +
            (targets.length > 6 ? `\n... 还有 ${targets.length - 6} 条` : '') +
            (withFiles.length ? `\n\n其中 ${withFiles.length} 条带关联文件（数据集 / 模型）` : ''),
        confirmText: '继续',
        danger: true,
    })
    if (!ok) return

    // 第 2 步：如果有关联文件，问是否一并清理
    let deleteFiles = false
    if (withFiles.length) {
        deleteFiles = await confirmDialog({
            title: '一并删除关联文件？',
            message:
                `${withFiles.length} 条 run 有关联文件。\n\n` +
                '· 点"是" → 一并清理（激活中的模型会自动跳过）\n' +
                '· 点"否" → 仅删 DB 记录，文件保留',
            confirmText: '是，一并删除',
            cancelText:  '否，保留文件',
            danger: true,
        })
    }

    // 第 3 步：批量删除（循环调单个 endpoint，简单可靠）
    batchDeleting.value = true
    let okCount = 0, failCount = 0, filesCount = 0
    const errors = []
    try {
        for (const id of ids) {
            try {
                const res = await api.deleteBacktestRun(id, deleteFiles)
                const payload = res?.data || res
                if (res.ok && payload.ok) {
                    okCount++
                    filesCount += (payload.deleted_files || []).length
                    if (payload.file_errors?.length) errors.push(...payload.file_errors)
                } else {
                    failCount++
                }
            } catch (e) {
                failCount++
                errors.push(`#${id}: ${e}`)
            }
        }
        // 清空选择 + 刷新
        compareSelected.value = []
        if (ids.includes(selectedRun.value)) {
            selectedRun.value = null
            selectedDetail.value = null
        }
        await loadRuns()
        await loadMlHealth()

        const msg = `已删 ${okCount} 条`
            + (filesCount ? ` + ${filesCount} 个文件` : '')
            + (failCount ? `（${failCount} 条失败）` : '')
        if (failCount) pushError(msg + ' · ' + errors.slice(0, 2).join('; '))
        else pushSuccess(msg)
    } finally {
        batchDeleting.value = false
    }
}

async function updateNote(runId, currentNotes) {
    const newNotes = prompt('备注（标记调参意图 / 验证结论等）：', currentNotes || '')
    if (newNotes == null) return  // 取消
    try {
        await api.updateBacktestNotes(runId, newNotes)
        await loadRuns()
        if (selectedRun.value === runId) await selectRun(runId)
        pushSuccess('备注已更新')
    } catch (e) { pushError(String(e)) }
}

function fmtTime(iso) {
    if (!iso) return '—'
    try {
        const d = new Date(iso)
        const pad = n => String(n).padStart(2, '0')
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    } catch { return iso.slice(0, 16) }
}

function fmtPct(v, digits = 2) {
    if (v == null || !Number.isFinite(v)) return '—'
    return (v >= 0 ? '+' : '') + (v * 100).toFixed(digits) + '%'
}

// runType 显示标签 + 颜色
const RUN_TYPE_LABEL = {
    runAll:           { text: 'runAll',         cls: 'bg-[#dbeafe] text-[#1e40af]' },
    gridSearch:       { text: 'gridSearch',     cls: 'bg-[#fef3c7] text-[#92400e]' },
    runV0:            { text: 'runV0',          cls: 'bg-[#cffafe] text-[#155e75]' },
    runExitExperiment:{ text: 'exitExp',        cls: 'bg-[#fee2e2] text-[#991b1b]' },
}

// 英文 metric / grade / source key 的中文显示映射，让非技术用户也看得懂
const METRIC_LABEL_CN = {
    count:        '笔数',
    winRate:      '胜率',
    timeRate:     '持有到期占比',
    exitRate:     '跌破 MA10 离场占比',
    invalidRate:  '跌破蓄势位离场占比',
    avgReturn:    '平均收益',
    medianReturn: '收益中位数',
    maxReturn:    '最高单笔收益',
    minReturn:    '最低单笔收益',
    p25:          '收益 25 分位',
    p75:          '收益 75 分位',
    avgHold:      '平均持仓天数',
}
const GRADE_LABEL_CN = {
    strong:  '⭐⭐⭐ 强',
    medium:  '⭐⭐ 中',
    fail:    '⭐ 弱',
    pending: '◇ 待确认',
    S:       'S 级（最强）',
    A:       'A 级',
    B:       'B 级',
    C:       'C 级（最弱）',
    confirmed:   '已确认',
    unconfirmed: '未确认',
    unknown:     '未知',
    time:        '持有到期',
    exit:        '跌破 MA10',
    invalid:     '跌破蓄势位',
}
const SIGNAL_SOURCE_CN = {
    threeStage:    '主升突破',
    breakoutEve:   '突破前夜',
    dragonReturn:  '龙回头',
    limitUpRelay:  '连板游资',
    stretched:     '拉伸过度',
    manual:        '手动',
}
function cnMetric(key)  { return METRIC_LABEL_CN[key] || key }
function cnGrade(key)   { return GRADE_LABEL_CN[key]  || key }
function cnSource(key)  { return SIGNAL_SOURCE_CN[key] || key }

// 解析 summary 里的 overall，方便列表中显示
function overallStats(run) {
    const s = run?.summary
    if (!s) return null
    // runAll/runV0 是 { overall: {...} }
    if (s.overall) return s.overall
    // gridSearch 没有 overall，看 sortedByWin[0]
    if (s.sortedByWin && s.sortedByWin.length) return null  // grid 看 top_combos
    return null
}

// ============ P2: ML 健康面板 ============
const mlPanelOpen = ref(false)   // ML 健康面板默认折叠 —— 主流程不依赖 ML，按需展开
// 用户展开面板时重新渲染 IC 图（v-if 切换会导致 ref DOM 重挂载）
watch(mlPanelOpen, (open) => {
    if (open) nextTick(() => renderIcChart())
})
const mlHealth = ref(null)
const mlHealthLoading = ref(false)
const icTrend = ref([])         // 完整 IC 历史（最多 12 个）
const modelsWithIc = ref([])    // 模型对比矩阵：每个模型 vs 最新 dataset 的 IC
const datasetsAvail = ref([])   // 可用 dataset 列表
const selectedDatasetForCompare = ref(null)   // 模型对比所基于的 dataset（默认最新）

// 推荐模型 + 是否全部在 in-sample 状态
const recommendedModel = computed(() => modelsWithIc.value.find(m => m.recommended))
const allInSample = computed(() => modelsWithIc.value.length > 0 &&
    modelsWithIc.value.every(m => m.verdict === 'in_sample' || m.error))

// 范围切片 —— 不同回测范围分开对比
const compareScope = ref({
    labelField:   't7Return',    // 标签 = 持有期视角
    signalSource: null,           // 信号源 = detector
    boards:       null,           // 板块（list 或 null）
})
const SIGNAL_SOURCE_OPTIONS = [
    { key: null,            label: '全部' },
    { key: 'threeStage',    label: '主升突破' },
    { key: 'breakoutEve',   label: '突破前夜' },
    { key: 'dragonReturn',  label: '龙回头' },
    { key: 'limitUpRelay',  label: '连板游资' },
    { key: 'stretched',     label: 'stretched' },
]
const LABEL_OPTIONS = [
    { key: 't3Return',  label: 't+3 (短)' },
    { key: 't7Return',  label: 't+7 (中)' },
    { key: 't14Return', label: 't+14 (长)' },
    { key: 't21Return', label: 't+21 (超长)' },
]
const BOARD_OPTIONS_FOR_SCOPE = [
    { key: 'sh_main', label: '沪主板' },
    { key: 'sz_main', label: '深主板' },
    { key: 'sme',     label: '中小板' },
    { key: 'gem',     label: '创业板' },
    { key: 'star',    label: '科创板' },
]
function toggleScopeBoard(key) {
    const arr = compareScope.value.boards || []
    const idx = arr.indexOf(key)
    if (idx >= 0) arr.splice(idx, 1)
    else arr.push(key)
    compareScope.value.boards = arr.length ? [...arr] : null
}

async function loadMlHealth() {
    mlHealthLoading.value = true
    try {
        const [overviewR, trendR, modelsR, datasetsR] = await Promise.all([
            api.mlHealthOverview(),
            api.mlHealthIcTrend(12),
            api.mlHealthListModelsWithIc(
                selectedDatasetForCompare.value, 10,
                compareScope.value.labelField,
                compareScope.value.signalSource,
                compareScope.value.boards,
            ),
            api.mlHealthListDatasets(),
        ])
        if (overviewR?.ok) mlHealth.value = overviewR.data
        if (trendR?.ok) icTrend.value = Array.isArray(trendR.data) ? trendR.data : []
        if (modelsR?.ok) modelsWithIc.value = Array.isArray(modelsR.data) ? modelsR.data : []
        if (datasetsR?.ok) datasetsAvail.value = Array.isArray(datasetsR.data) ? datasetsR.data : []
        nextTick(renderIcChart)
    } catch (e) { /* 静默 */ }
    finally { mlHealthLoading.value = false }
}

// ============ 重训 + 切换模型 ============
const retrainState = ref({
    open:     false,       // 表单展开
    running:  false,       // 训练中
    label:    't7Profitable',
    selectedDatasets: [],  // 选中的数据集（空 = 全部）
    elapsed:  0,           // 耗时计时（秒）
    result:   null,        // 完成后的结果
})
const RETRAIN_LABELS = [
    { key: 't7Profitable',  desc: '7 日内是否盈利（分类，默认 / 最稳）' },
    { key: 't14Profitable', desc: '14 日内是否盈利（中线）' },
    { key: 't3Profitable',  desc: '3 日内是否盈利（短线，样本少）' },
    { key: 't7Return',      desc: '7 日收益率（回归）' },
]

function toggleRetrainDataset(filename) {
    const arr = retrainState.value.selectedDatasets
    const idx = arr.indexOf(filename)
    if (idx >= 0) arr.splice(idx, 1)
    else arr.push(filename)
}
async function startRetrain() {
    if (retrainState.value.running) return
    const s = retrainState.value
    s.running = true
    s.elapsed = 0
    s.result = null
    const timerId = setInterval(() => { s.elapsed++ }, 1000)
    try {
        const datasetFiles = s.selectedDatasets.length ? [...s.selectedDatasets] : null
        const res = await api.mlRetrain(datasetFiles, s.label)
        s.result = res?.data || res
        if (res?.ok && res.data?.ok) {
            pushSuccess(`训练完成，平均 AUC ${(res.data.avg_auc ?? 0).toFixed(3)}，已激活`)
            // 重训成功 → 重新加载健康数据
            await loadMlHealth()
            s.open = false
        } else {
            const err = res?.data?.error || res?.error || '未知错误'
            pushError(`训练失败: ${err}`)
        }
    } catch (e) {
        pushError(`训练异常: ${e}`)
    } finally {
        clearInterval(timerId)
        s.running = false
    }
}
// ============ 🔮 智能重训：一键全流程（采数 → 训练 → 激活）============
const smartRetrain = ref({
    running:  false,
    phase:    null,    // 'collecting' | 'training' | 'done'
    progress: '',
    elapsed:  0,
    boards:   ['sh_main', 'sz_main', 'sme'],
    holdDays: 7,
    label:    't7Profitable',
    open:     false,
    result:   null,
})

async function startSmartRetrain() {
    if (smartRetrain.value.running || runner.running.value || retrainState.value.running) {
        pushError('已有任务在跑，先等完成或取消')
        return
    }
    const s = smartRetrain.value
    s.running = true
    s.phase = 'collecting'
    s.progress = ''
    s.elapsed = 0
    s.result = null
    const tickTimer = setInterval(() => { s.elapsed++ }, 1000)
    try {
        // Phase 1：采新数据（backtest + dumpFeatures，落新 dataset，run 已带 produced_dataset）
        const dsName = `smart_${Date.now().toString().slice(-6)}`
        const collectRes = await runner.run({
            boards: s.boards,
            holdDays: s.holdDays,
            dumpFeatures: true,
            dumpName: dsName,
        })
        if (!collectRes.ok) {
            throw new Error('采数失败: ' + (collectRes.error || '未知'))
        }
        if (!collectRes.datasetFilename) {
            throw new Error('采数完成但没生成 dataset（可能 trades 太少）')
        }
        s.progress = `✓ 已采集新数据: ${collectRes.datasetFilename}`

        // Phase 2：用刚生成的 dataset 训练新模型
        s.phase = 'training'
        const trainRes = await api.mlRetrain([collectRes.datasetFilename], s.label)
        if (!trainRes?.ok || !trainRes.data?.ok) {
            throw new Error('训练失败: ' + (trainRes?.data?.error || trainRes?.error || '未知'))
        }
        s.result = trainRes.data
        s.progress = `✓ 训练完成 · AUC=${(trainRes.data.avg_auc ?? 0).toFixed(3)} · 已激活新模型`
        s.phase = 'done'

        // 回填 produced_model 到 run 行，方便日后"删 run 时一并清掉模型文件"
        if (collectRes.runId && trainRes.data.model_path) {
            try {
                await api.updateBacktestRunArtifacts(collectRes.runId, null, trainRes.data.model_path)
            } catch (e) { /* 不阻塞 */ }
        }

        // Phase 3：刷新数据
        await loadMlHealth()
        await loadRuns()
        pushSuccess(`🎉 智能重训完成！平均 AUC ${(trainRes.data.avg_auc ?? 0).toFixed(3)}`)
        s.open = false
    } catch (e) {
        pushError(String(e?.message || e))
    } finally {
        clearInterval(tickTimer)
        s.running = false
        s.phase = null
    }
}

// 批量删除 ML 模型
const selectedModels = ref([])   // 选中要删的 model paths
const batchDeletingModels = ref(false)

function toggleModelSelection(path) {
    const idx = selectedModels.value.indexOf(path)
    if (idx >= 0) selectedModels.value.splice(idx, 1)
    else selectedModels.value.push(path)
}
async function deleteSelectedModels() {
    const paths = [...selectedModels.value]
    if (!paths.length) return

    const targets = paths.map(p => modelsWithIc.value.find(m => m.path === p)).filter(Boolean)
    // 过滤掉激活的（如果用户错选了）
    const active = targets.filter(m => m.is_active)
    const deletable = targets.filter(m => !m.is_active)
    if (!deletable.length) {
        pushError('选中的全是激活中的模型，请先切换到其他模型再删')
        return
    }

    const ok = await confirmDialog({
        title: `批量删除 ${deletable.length} 个模型`,
        message:
            `将删除 ${deletable.length} 个模型文件（+ 各自的 sidecar JSON）：\n` +
            deletable.slice(0, 8).map(m => `· ${m.filename}`).join('\n') +
            (deletable.length > 8 ? `\n... 还有 ${deletable.length - 8} 个` : '') +
            (active.length ? `\n\n（其中 ${active.length} 个为激活中，会自动跳过）` : ''),
        confirmText: '🗑 全部删除',
        danger: true,
    })
    if (!ok) return

    batchDeletingModels.value = true
    let okCount = 0, failCount = 0
    try {
        for (const m of deletable) {
            try {
                const res = await api.mlDeleteModel(m.path)
                if (res?.ok && res.data?.ok) okCount++
                else failCount++
            } catch { failCount++ }
        }
        selectedModels.value = []
        await loadMlHealth()
        if (failCount) pushError(`已删 ${okCount} 个 · 失败 ${failCount} 个`)
        else pushSuccess(`已删除 ${okCount} 个模型`)
    } finally {
        batchDeletingModels.value = false
    }
}

// 批量删除 ML datasets
const selectedDatasets = ref([])
const batchDeletingDatasets = ref(false)

function toggleDatasetSelection(filename) {
    const idx = selectedDatasets.value.indexOf(filename)
    if (idx >= 0) selectedDatasets.value.splice(idx, 1)
    else selectedDatasets.value.push(filename)
}
async function deleteSelectedDatasets() {
    const names = [...selectedDatasets.value]
    if (!names.length) return

    const ok = await confirmDialog({
        title: `批量删除 ${names.length} 个数据集`,
        message:
            `将永久删除以下 NDJSON 文件：\n` +
            names.slice(0, 8).map(n => `· ${n}`).join('\n') +
            (names.length > 8 ? `\n... 还有 ${names.length - 8} 个` : '') +
            `\n\n依赖这些数据集的模型 IC 评估会失效。`,
        confirmText: '🗑 全部删除',
        danger: true,
    })
    if (!ok) return

    batchDeletingDatasets.value = true
    let okCount = 0, failCount = 0
    try {
        for (const n of names) {
            try {
                const res = await api.mlDeleteDataset(n)
                if (res?.ok && res.data?.ok) okCount++
                else failCount++
            } catch { failCount++ }
        }
        selectedDatasets.value = []
        await loadMlHealth()
        if (failCount) pushError(`已删 ${okCount} 个 · 失败 ${failCount} 个`)
        else pushSuccess(`已删除 ${okCount} 个数据集`)
    } finally {
        batchDeletingDatasets.value = false
    }
}

async function deleteModelFile(m) {
    if (m.is_active) {
        pushError('此模型正在激活，先切换到其他模型再删除')
        return
    }
    const ok = await confirmDialog({
        title: '删除模型',
        message: `确认删除 ${m.filename}？\n会同时删除 sidecar JSON，不可恢复。`,
        confirmText: '删除',
        danger: true,
    })
    if (!ok) return
    try {
        const res = await api.mlDeleteModel(m.path)
        if (res?.ok && res.data?.ok) {
            pushSuccess(`已删除 ${m.filename}（含 sidecar）`)
            await loadMlHealth()
        } else {
            pushError(`删除失败: ${res?.data?.error || '未知'}`)
        }
    } catch (e) { pushError(String(e)) }
}

async function deleteDatasetFile(d) {
    const ok = await confirmDialog({
        title: '删除数据集',
        message: `确认删除 ${d.filename}？\n该文件被删除后，依赖它的模型 IC 评估会失效。`,
        confirmText: '删除',
        danger: true,
    })
    if (!ok) return
    try {
        const res = await api.mlDeleteDataset(d.filename)
        if (res?.ok && res.data?.ok) {
            pushSuccess(`已删除 ${d.filename}`)
            await loadMlHealth()
        } else {
            pushError(`删除失败: ${res?.data?.error || '未知'}`)
        }
    } catch (e) { pushError(String(e)) }
}

async function activateModel(path) {
    const ok = await confirmDialog({
        title: '切换激活模型',
        message: `切换到 ${path.split(/[\\/]/).pop()}？\n切换后所有 mlScore 计算都用此模型。`,
        confirmText: '切换',
    })
    if (!ok) return
    try {
        const res = await api.mlSetActiveModel(path)
        if (res?.ok && res.data?.ok) {
            pushSuccess('已切换激活模型')
            await loadMlHealth()
        } else {
            pushError(`切换失败: ${res?.data?.error || '未知'}`)
        }
    } catch (e) { pushError(String(e)) }
}

// ============ C3: IC 历史折线图 ============
const icChartRef = ref(null)
let _icChart = null

const hasIcTrend = computed(() => {
    // 至少 2 个有效 IC 才画
    return icTrend.value.filter(x => x.ic != null || x.ic_threestage != null).length >= 2
})

function renderIcChart() {
    if (!icChartRef.value || !hasIcTrend.value) return
    if (!_icChart) _icChart = echarts.init(icChartRef.value)
    // 按 age_days 升序（最老在左）
    const sorted = [...icTrend.value].sort((a, b) => (b.dataset_age_days ?? 0) - (a.dataset_age_days ?? 0))
    const labels = sorted.map(x => {
        const fn = (x.dataset_filename || '').replace(/^dataset_/, '').replace(/\.parquet$/, '')
        return fn.length > 14 ? fn.slice(-14) : fn
    })
    const icAll = sorted.map(x => x.ic != null ? +x.ic.toFixed(3) : null)
    const icThree = sorted.map(x => x.ic_threestage != null ? +x.ic_threestage.toFixed(3) : null)
    const verdicts = sorted.map(x => x.verdict)
    _icChart.setOption({
        animation: false,
        grid: { left: 38, right: 14, top: 28, bottom: 36 },
        tooltip: {
            trigger: 'axis',
            backgroundColor: '#fff',
            borderColor: '#e5e7eb',
            textStyle: { color: '#1f2937', fontSize: 11 },
            formatter: (params) => {
                if (!params?.length) return ''
                const i = params[0].dataIndex
                const d = sorted[i]
                let s = `<div style="font-weight:bold">${labels[i]}</div>`
                s += `<div style="color:#64748b;font-size:10px">${d.dataset_age_days} 天前 · n=${d.n ?? '—'} · ${d.verdict || '—'}</div>`
                for (const p of params) {
                    s += `<div>${p.marker} ${p.seriesName}: <b>${p.value ?? '—'}</b></div>`
                }
                return s
            },
        },
        legend: {
            top: 0, left: 0, itemGap: 10, itemWidth: 12, itemHeight: 8,
            textStyle: { fontSize: 10, color: '#475569' },
        },
        xAxis: {
            type: 'category', data: labels,
            axisLabel: { fontSize: 9, color: '#94a3b8', rotate: 30 },
            axisLine:  { lineStyle: { color: '#e5e7eb' } },
            axisTick:  { show: false },
        },
        yAxis: {
            type: 'value',
            axisLabel: { fontSize: 9, color: '#94a3b8' },
            splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' } },
        },
        series: [
            {
                name: 'IC 全样本', type: 'line', smooth: false, connectNulls: true,
                data: icAll,
                lineStyle: { color: '#dc2626', width: 2 },
                itemStyle: { color: '#dc2626' },
                symbol: 'circle', symbolSize: 6,
                markLine: {
                    silent: true, symbol: 'none',
                    label: { fontSize: 9, color: '#16a34a' },
                    lineStyle: { type: 'dashed', color: '#16a34a' },
                    data: [{ yAxis: 0.05, name: '健康 0.05' }],
                },
            },
            {
                name: 'IC threeStage', type: 'line', smooth: false, connectNulls: true,
                data: icThree,
                lineStyle: { color: '#0369a1', width: 1.5, type: 'dashed' },
                itemStyle: { color: '#0369a1' },
                symbol: 'rect', symbolSize: 6,
            },
            {
                name: '衰减阈值 0.02', type: 'line', data: labels.map(() => 0.02),
                lineStyle: { color: '#fbbf24', width: 1, type: 'dotted' },
                itemStyle: { color: '#fbbf24' },
                symbol: 'none', silent: true,
            },
            // 训练集（in_sample）标记 —— 用散点叠加
            {
                name: '⊘ 训练集（不计）', type: 'scatter',
                data: icAll.map((v, i) => verdicts[i] === 'in_sample' && v != null ? v : null),
                itemStyle: { color: '#94a3b8' },
                symbol: 'diamond', symbolSize: 10,
                tooltip: { show: false },
            },
        ],
    })
}

function onIcResize() { _icChart?.resize() }

// ============ 资金曲线（详情面板用）============
const equityChartRef = ref(null)
let _equityChart = null

// 4 主指标卡 + by-star 简化卡 用的派生数据
const keyStats = computed(() => {
    const o = selectedDetail.value?.summary?.overall
    const curve = selectedDetail.value?.summary?.equityCurve || []
    if (!o) return null
    // 最大回撤：从 equityCurve 取 min(drawdown)；老 run 没 curve 时返 null
    let maxDD = null
    if (curve.length) {
        let m = 0
        for (const p of curve) if ((p.drawdown ?? 0) < m) m = p.drawdown
        maxDD = m
    }
    // 终值（累计净值最后一根）
    const finalCum = curve.length ? (curve[curve.length - 1].cum ?? 0) : null
    return {
        total:    o.count ?? 0,
        winRate:  o.winRate ?? null,           // 0-1
        avgReturn: o.avgReturn ?? null,        // %
        maxDrawdown: maxDD,                    // %（负数）
        finalCum,                              // %
    }
})

// 按 N+1 突破确认强度拆分：实际胜率 vs 预期（用 byBreakoutConfirm，不是 byGrade）
// byGrade 是 S/A/B/C 评分；byBreakoutConfirm 是 strong/medium/fail/pending（专业语义清晰）
const byStarRows = computed(() => {
    const bc = selectedDetail.value?.summary?.byBreakoutConfirm
    if (!bc) return []
    // 预期胜率：跟 trade_journal_service 的 expected_win_rate 同步
    const expected = {
        strong:  0.74,
        medium:  0.76,
        fail:    0.45,
        pending: null,
    }
    const labels = {
        strong:  { label: '⭐⭐⭐ N+1 strong', cls: 'text-[#dc2626]' },
        medium:  { label: '⭐⭐⭐ N+1 medium', cls: 'text-[#dc2626]' },
        fail:    { label: '⭐ N+1 fail',      cls: 'text-[#94a3b8]' },
        pending: { label: '◇ N+1 pending',   cls: 'text-[#94a3b8]' },
    }
    return Object.entries(bc).map(([k, s]) => ({
        key:      k,
        label:    labels[k]?.label || k,
        cls:      labels[k]?.cls   || 'text-[#475569]',
        count:    s.count ?? 0,
        winRate:  s.winRate ?? null,
        avgRet:   s.avgReturn ?? 0,
        expected: expected[k] ?? null,
        gap:      (s.winRate != null && expected[k] != null) ? s.winRate - expected[k] : null,
    })).filter(r => r.count > 0)
})

function renderEquityChart() {
    const curve = selectedDetail.value?.summary?.equityCurve
    if (!equityChartRef.value || !curve || !curve.length) return
    // 如果 chart 实例还绑在旧的 DOM 上（切换 run 时 div 可能重新挂载），先 dispose
    if (_equityChart && _equityChart.getDom?.() !== equityChartRef.value) {
        _equityChart.dispose()
        _equityChart = null
    }
    if (!_equityChart) _equityChart = echarts.init(equityChartRef.value)
    const labels = curve.map(p => p.date)
    const cumData = curve.map(p => p.cum)
    const ddData  = curve.map(p => p.drawdown)
    const finalCum = curve[curve.length - 1].cum
    _equityChart.setOption({
        animation: false,
        grid: { left: 50, right: 14, top: 30, bottom: 40 },
        tooltip: {
            trigger: 'axis',
            backgroundColor: '#fff',
            borderColor: '#e5e7eb',
            textStyle: { color: '#1f2937', fontSize: 11 },
            formatter: (params) => {
                if (!params?.length) return ''
                const idx = params[0].dataIndex
                const p = curve[idx]
                let s = `<div style="font-weight:bold">${p.date}</div>`
                s += `<div style="color:#64748b;font-size:10px">当日 ${p.count} 笔平仓</div>`
                s += `<div>累计净值: <b>${p.cum >= 0 ? '+' : ''}${p.cum.toFixed(2)}%</b></div>`
                s += `<div>回撤: <span style="color:#1e40af">${p.drawdown.toFixed(2)}%</span></div>`
                s += `<div>当日盈亏: ${p.daily >= 0 ? '+' : ''}${p.daily.toFixed(2)}%</div>`
                return s
            },
        },
        legend: {
            top: 0, left: 0, itemGap: 16, itemWidth: 14, itemHeight: 8,
            textStyle: { fontSize: 10, color: '#475569' },
        },
        xAxis: {
            type: 'category', data: labels,
            axisLabel: { fontSize: 9, color: '#94a3b8', rotate: 30 },
            axisLine:  { lineStyle: { color: '#e5e7eb' } },
            axisTick:  { show: false },
            boundaryGap: false,
        },
        yAxis: [
            {
                type: 'value', name: '累计净值 %',
                nameTextStyle: { fontSize: 9, color: '#94a3b8' },
                axisLabel: { fontSize: 9, color: '#94a3b8', formatter: v => v.toFixed(0) + '%' },
                splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' } },
            },
            {
                type: 'value', name: '回撤 %',
                nameTextStyle: { fontSize: 9, color: '#94a3b8' },
                axisLabel: { fontSize: 9, color: '#1e40af', formatter: v => v.toFixed(0) + '%' },
                splitLine: { show: false },
                position: 'right',
                max: 0,
            },
        ],
        series: [
            {
                name: '累计净值', type: 'line', smooth: false,
                data: cumData,
                lineStyle: { color: finalCum >= 0 ? '#dc2626' : '#059669', width: 2 },
                itemStyle: { color: finalCum >= 0 ? '#dc2626' : '#059669' },
                areaStyle: {
                    color: finalCum >= 0
                        ? { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
                            { offset: 0, color: 'rgba(220,38,38,0.15)' },
                            { offset: 1, color: 'rgba(220,38,38,0)' },
                          ] }
                        : { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
                            { offset: 0, color: 'rgba(5,150,105,0.15)' },
                            { offset: 1, color: 'rgba(5,150,105,0)' },
                          ] },
                },
                symbol: 'none',
                markLine: {
                    silent: true, symbol: 'none',
                    label: { fontSize: 9, color: '#94a3b8', position: 'end' },
                    lineStyle: { type: 'dashed', color: '#cbd5e1' },
                    data: [{ yAxis: 0, name: '0%' }],
                },
            },
            {
                name: '回撤', type: 'line', smooth: false,
                yAxisIndex: 1,
                data: ddData,
                lineStyle: { color: '#1e40af', width: 1, type: 'dotted' },
                itemStyle: { color: '#1e40af' },
                areaStyle: { color: 'rgba(30,64,175,0.06)' },
                symbol: 'none',
            },
        ],
    })
}
function onEquityResize() { _equityChart?.resize() }

watch(selectedDetail, () => {
    nextTick(() => {
        renderEquityChart()
    })
})

const VERDICT_DISPLAY = {
    healthy:   { label: '✓ 健康',     cls: 'bg-[#dcfce7] text-[#166534]' },
    declining: { label: '⚠ 衰减中',   cls: 'bg-[#fef3c7] text-[#92400e]' },
    failed:    { label: '✗ 失效',     cls: 'bg-[#fee2e2] text-[#991b1b]' },
    in_sample: { label: '⊘ 训练集',   cls: 'bg-[#f1f5f9] text-[#475569]' },
    unknown:   { label: '? 未知',     cls: 'bg-[#f3f4f6] text-[#666]' },
}

onMounted(() => {
    loadRuns()
    loadMlHealth()
    window.addEventListener('resize', onIcResize)
    window.addEventListener('resize', onEquityResize)
})
onBeforeUnmount(() => {
    window.removeEventListener('resize', onIcResize)
    window.removeEventListener('resize', onEquityResize)
    _icChart?.dispose()
    _icChart = null
    _equityChart?.dispose()
    _equityChart = null
})
</script>

<template>
    <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

        <!-- ============ ML 模型健康面板（默认折叠 —— 主流程不依赖 ML）============ -->
        <!-- 收起状态：单行 status 摘要 + 展开按钮（高度对齐其它 view 的 44px hub bar）-->
        <div v-if="!mlPanelOpen"
             class="h-[44px] px-[16px] flex items-center gap-[10px] bg-[#fafafa] border-b border-[#e5e7eb] shrink-0 text-[11px]">
            <span class="text-[#666] font-semibold">🧠 ML</span>
            <span v-if="mlHealth?.current_model?.ready"
                  class="text-[10px] px-[5px] py-[1px] rounded bg-[#dcfce7] text-[#166534] font-semibold">
                ✓ 已激活
            </span>
            <span v-else class="text-[10px] px-[5px] py-[1px] rounded bg-[#fee2e2] text-[#991b1b] font-semibold">
                ✗ 未加载
            </span>
            <span v-if="mlHealth?.current_model?.path" class="text-[10px] text-[#666] font-mono truncate"
                  :title="mlHealth.current_model.path">
                {{ (mlHealth.current_model.path || '').split(/[\\/]/).pop() }}
            </span>
            <span v-if="mlHealth?.current_model?.age_days != null" class="text-[10px] text-[#94a3b8]">
                {{ mlHealth.current_model.age_days }} 天前训练
            </span>
            <span v-if="recommendedModel && recommendedModel.path !== mlHealth?.current_model?.path"
                  class="text-[10px] text-[#16a34a] font-semibold">
                💡 有更优模型可切换
            </span>
            <span v-else-if="allInSample"
                  class="text-[10px] text-[#92400e]">⚠ 评估不可信（点开看详情）</span>

            <button @click="mlPanelOpen = true"
                    class="ml-auto text-[10px] px-[8px] py-[2px] rounded border border-[#e5e7eb] text-[#666] hover:bg-white hover:border-[#94a3b8]">
                ⌄ 展开 ML 面板
            </button>
            <!-- hub 控件注入位（QuantHub segmented + 今日按钮）—— 与项目标准统一：左侧 border-l 分隔 -->
            <div class="shrink-0 flex items-center pl-[10px] pr-[14px] border-l border-[#e5e5e5] gap-[10px] h-full">
                <slot name="tabBarRight" />
            </div>
        </div>

        <!-- 展开状态：完整 ML 健康面板（两层 header：44px hub bar + 32px 模型信息条）-->
        <div v-else class="shrink-0 border-b border-[#fecaca] bg-gradient-to-r from-[#fef2f2] to-white">
            <!-- 第 1 行：44px hub bar —— 跟其他 view（候选/日志/选股）保持同一基准线 -->
            <div class="h-[44px] px-[12px] flex items-center gap-[10px] border-b border-[#fecaca]/60">
                <span class="text-[14px] font-bold text-[#111]">🧠 ML 模型健康</span>
                <span v-if="mlHealth?.current_model?.ready"
                      class="text-[10px] px-[6px] py-[1px] rounded bg-[#dcfce7] text-[#166534] font-semibold">
                    ✓ 已激活
                </span>
                <span v-else class="text-[10px] px-[6px] py-[1px] rounded bg-[#fee2e2] text-[#991b1b] font-semibold">
                    ✗ 未加载
                </span>
                <span v-if="smartRetrain.running"
                      class="text-[11px] px-[8px] py-[2px] rounded border border-[#16a34a] bg-[#dcfce7] text-[#166534] font-semibold animate-pulse">
                    🔮 {{ smartRetrain.phase === 'collecting' ? '采数据' : '训练' }} {{ smartRetrain.elapsed }}s
                </span>
                <button @click="mlPanelOpen = false"
                        class="ml-auto text-[11px] px-[10px] py-[3px] rounded border border-[#94a3b8] text-[#475569] bg-white hover:bg-[#fafafa] hover:border-[#1e293b] font-semibold transition">
                    ⌃ 收起面板
                </button>
                <!-- hub 控件注入位 —— 与项目标准统一：左侧 border-l 分隔 -->
                <div class="shrink-0 flex items-center pl-[10px] pr-[2px] border-l border-[#e5e5e5] gap-[10px] h-full">
                    <slot name="tabBarRight" />
                </div>
            </div>

            <!-- 第 2 行：32px 模型信息条 + 操作按钮 -->
            <div class="h-[32px] px-[12px] flex items-center gap-[10px] text-[11px]">
                <span v-if="mlHealth?.current_model?.path" class="text-[#666] font-mono truncate max-w-[280px]"
                      :title="mlHealth.current_model.path">
                    {{ (mlHealth.current_model.path || '').split(/[\\/]/).pop() }}
                </span>
                <span v-if="mlHealth?.current_model?.age_days != null" class="text-[#94a3b8]">
                    {{ mlHealth.current_model.age_days }} 天前 · {{ mlHealth.current_model.features?.length || 0 }} 维
                </span>
                <span class="ml-auto text-[10px] text-[#94a3b8]" v-if="!smartRetrain.running">
                    💡 想要新数据？在「新建回测」勾选"🔮 顺手训 ML"
                </span>
                <!-- 高级：手动选已有 dataset 重训 -->
                <button v-if="!retrainState.running && !smartRetrain.running"
                        @click="retrainState.open = !retrainState.open"
                        class="text-[10px] px-[8px] py-[2px] rounded border font-semibold transition"
                        :class="retrainState.open
                            ? 'border-[#dc2626] bg-[#fef2f2] text-[#dc2626]'
                            : 'border-[#dc2626]/40 text-[#dc2626] bg-white hover:bg-[#fff5f5]'"
                        title="不重跑回测，直接用历史 dataset 训新模型">
                    {{ retrainState.open ? '✕ 收起' : '🔄 仅训 ML（用旧数据）' }}
                </button>
                <button v-else-if="retrainState.running" disabled
                        class="text-[10px] px-[8px] py-[2px] rounded border border-[#e5e7eb] text-[#94a3b8]">
                    训练中 {{ retrainState.elapsed }}s
                </button>
                <button @click="loadMlHealth" :disabled="mlHealthLoading"
                        class="text-[10px] px-[8px] py-[2px] rounded border border-[#e5e7eb] hover:bg-white disabled:opacity-50">
                    {{ mlHealthLoading ? '加载' : '刷新' }}
                </button>
            </div>

            <!-- ============ ML 面板 body（带 padding）============ -->
            <div class="px-[16px] py-[10px]">

            <!-- 智能重训面板（展开）-->
            <div v-if="smartRetrain.open && !smartRetrain.running"
                 class="mb-[8px] p-[10px] border border-[#16a34a] bg-[#dcfce7] rounded text-[11px]">
                <div class="text-[#166534] font-semibold mb-[5px]">🔮 智能重训：一键完成 采新数据 → 训练 → 激活</div>
                <div class="text-[10px] text-[#166534] mb-[6px] leading-[1.5]">
                    流程：①跑一次回测扫全市场（生成"模型从没见过"的新 dataset）→ ②用此 dataset 训练新模型 → ③自动激活。
                    全程约 <b>10-15 分钟</b>（采数 ~8 分钟 + 训练 ~2 分钟），完成后 IC 数字就是真实 out-of-sample 表现。
                </div>
                <div class="flex items-center gap-[6px] mb-[6px]">
                    <span class="text-[#166534] font-semibold w-[50px]">板块</span>
                    <button v-for="b in BOARD_OPTIONS_FOR_SCOPE" :key="b.key"
                            @click="(smartRetrain.boards.includes(b.key) ? smartRetrain.boards.splice(smartRetrain.boards.indexOf(b.key), 1) : smartRetrain.boards.push(b.key))"
                            :class="['px-[8px] py-[2px] rounded text-[10px] border transition',
                                    smartRetrain.boards.includes(b.key)
                                      ? 'bg-[#16a34a] text-white border-[#16a34a]'
                                      : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#16a34a]/40']">
                        {{ b.label }}
                    </button>
                </div>
                <div class="flex items-center gap-[6px] mb-[6px]">
                    <span class="text-[#166534] font-semibold w-[50px]">持有</span>
                    <button v-for="d in [3, 7, 14, 21]" :key="d"
                            @click="smartRetrain.holdDays = d"
                            :class="['px-[8px] py-[2px] rounded text-[10px] border transition tabular-nums',
                                    smartRetrain.holdDays === d
                                      ? 'bg-[#1e293b] text-white border-[#1e293b]'
                                      : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#1e293b]/40']">
                        {{ d }} 天
                    </button>
                </div>
                <div class="flex items-center gap-[6px] mb-[6px]">
                    <span class="text-[#166534] font-semibold w-[50px]">标签</span>
                    <button v-for="l in RETRAIN_LABELS.filter(x => x.key.endsWith('Profitable'))" :key="l.key"
                            @click="smartRetrain.label = l.key"
                            :title="l.desc"
                            :class="['px-[8px] py-[2px] rounded text-[10px] border transition font-mono',
                                    smartRetrain.label === l.key
                                      ? 'bg-[#1e293b] text-white border-[#1e293b]'
                                      : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#1e293b]/40']">
                        {{ l.key }}
                    </button>
                </div>
                <div class="flex items-center pt-[6px] border-t border-[#86efac]">
                    <span class="text-[10px] text-[#94a3b8]">期间不要关页面（webview 刷新会丢进度指针）</span>
                    <button @click="startSmartRetrain"
                            class="ml-auto text-[11px] px-[14px] py-[5px] rounded bg-[#16a34a] text-white font-semibold hover:bg-[#15803d]">
                        ▶ 开始一键全流程
                    </button>
                </div>
            </div>

            <!-- 智能重训进行中 -->
            <div v-if="smartRetrain.running"
                 class="mb-[8px] p-[10px] border border-[#16a34a] bg-[#dcfce7] rounded text-[11px]">
                <div class="flex items-center gap-[8px] mb-[4px]">
                    <span class="animate-pulse text-[#166534] font-bold">🔮 智能重训进行中</span>
                    <span class="tabular-nums text-[#166534]">已用 {{ smartRetrain.elapsed }} 秒</span>
                    <span class="text-[10px] ml-auto"
                          :class="smartRetrain.phase === 'collecting' ? 'text-[#166534] font-bold' : 'text-[#94a3b8]'">
                        {{ smartRetrain.phase === 'collecting' ? '① 采集新数据' : '✓ 数据已采' }}
                    </span>
                    <span class="text-[10px]"
                          :class="smartRetrain.phase === 'training' ? 'text-[#166534] font-bold' : 'text-[#94a3b8]'">
                        {{ smartRetrain.phase === 'training' ? '② 训练模型中' : (smartRetrain.phase === 'done' ? '✓ 训练完成' : '— 训练待开始') }}
                    </span>
                </div>
                <div v-if="smartRetrain.phase === 'collecting'" class="text-[10px] text-[#166534]">
                    扫描 {{ runner.scanned.value }} / {{ runner.total.value }} ({{ runner.progressPct.value }}%)
                    <span v-if="runner.currentCode.value" class="font-mono text-[#94a3b8] ml-[6px]">{{ runner.currentCode.value }}</span>
                </div>
                <div v-if="smartRetrain.progress" class="text-[10px] text-[#166534] mt-[2px]">{{ smartRetrain.progress }}</div>
            </div>

            <!-- 高级重训面板（展开 / 折叠）-->
            <div v-if="retrainState.open && !retrainState.running"
                 class="mb-[8px] p-[10px] border border-[#fbbf24] bg-[#fffbeb] rounded text-[11px]">
                <div class="flex items-center gap-[8px] mb-[6px]">
                    <span class="text-[#92400e] font-semibold">📚 训练数据</span>
                    <span class="text-[10px] text-[#94a3b8]">
                        （单击 chip = 选择用于训练 · 勾 ☐ = 选择批量删除 · hover 出 🗑 单删）
                    </span>
                    <button v-if="selectedDatasets.length"
                            @click="deleteSelectedDatasets"
                            :disabled="batchDeletingDatasets"
                            class="ml-auto text-[10px] px-[10px] py-[2px] rounded bg-[#dc2626] text-white hover:bg-[#b91c1c] disabled:opacity-50">
                        {{ batchDeletingDatasets ? '删除中...' : `🗑 批量删 (${selectedDatasets.length})` }}
                    </button>
                </div>
                <div class="flex flex-wrap gap-[5px] mb-[8px]">
                    <div v-for="d in datasetsAvail" :key="d.filename"
                         class="inline-flex items-center rounded border overflow-hidden text-[10px] font-mono group"
                         :class="[
                            retrainState.selectedDatasets.includes(d.filename)
                              ? 'bg-[#92400e] text-white border-[#92400e]'
                              : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#92400e]/40',
                            selectedDatasets.includes(d.filename) ? 'ring-2 ring-[#dc2626] ring-offset-[1px]' : '',
                         ]">
                        <input type="checkbox"
                               :checked="selectedDatasets.includes(d.filename)"
                               @change="toggleDatasetSelection(d.filename)"
                               @click.stop
                               class="ml-[5px] w-[11px] h-[11px] accent-[#dc2626] cursor-pointer shrink-0"
                               title="勾选加入批量删除"/>
                        <button @click="toggleRetrainDataset(d.filename)"
                                :title="`${d.filename} (${d.age_days}d, ${d.size_kb}kb)`"
                                class="px-[8px] py-[2px]">
                            {{ d.filename.replace('dataset_', '').replace('.ndjson', '') }}
                            <span class="opacity-60">({{ d.age_days }}d)</span>
                        </button>
                        <button @click.stop="deleteDatasetFile(d)"
                                :class="['px-[5px] py-[2px] border-l opacity-0 group-hover:opacity-100 transition',
                                        retrainState.selectedDatasets.includes(d.filename)
                                          ? 'border-l-[#fbbf24] hover:bg-[#7c2d12]'
                                          : 'border-l-[#e5e7eb] hover:bg-[#fef2f2] hover:text-[#dc2626]']"
                                title="单独删除该文件">
                            🗑
                        </button>
                    </div>
                </div>
                <div class="flex items-center gap-[6px] mb-[8px]">
                    <span class="text-[#92400e] font-semibold w-[60px]">📌 标签</span>
                    <button v-for="l in RETRAIN_LABELS" :key="l.key"
                            @click="retrainState.label = l.key"
                            :title="l.desc"
                            :class="['px-[8px] py-[2px] rounded text-[10px] border transition font-mono',
                                    retrainState.label === l.key
                                      ? 'bg-[#1e293b] text-white border-[#1e293b]'
                                      : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#1e293b]/40']">
                        {{ l.key }}
                    </button>
                </div>
                <div class="flex items-center pt-[6px] border-t border-[#fde68a]">
                    <span class="text-[10px] text-[#94a3b8]">
                        LightGBM 5-fold walk-forward CV · 一般 30s-3min · 训完自动激活新模型
                    </span>
                    <button @click="startRetrain"
                            class="ml-auto text-[11px] px-[14px] py-[5px] rounded bg-[#dc2626] text-white font-semibold hover:bg-[#b91c1c]">
                        ▶ 开始训练
                    </button>
                </div>
            </div>

            <!-- 训练进行中 -->
            <div v-if="retrainState.running"
                 class="mb-[8px] p-[10px] border border-[#dc2626] bg-[#fef2f2] rounded text-[11px] flex items-center gap-[10px]">
                <span class="animate-pulse text-[#dc2626] font-bold">🔄 训练中...</span>
                <span class="tabular-nums text-[#666]">已用 {{ retrainState.elapsed }} 秒</span>
                <span class="text-[10px] text-[#94a3b8]">LightGBM 训练同步阻塞，请勿关闭页面</span>
            </div>

            <!-- 加载状态 -->
            <div v-if="mlHealthLoading && !mlHealth" class="text-[11px] text-[#94a3b8]">加载 ML 数据中...</div>

            <!-- ============ 模型对比矩阵 ============ -->
            <div v-else-if="modelsWithIc.length">
                <div class="flex items-center justify-between mb-[5px]">
                    <div class="text-[10px] text-[#666]">
                        🤖 <b>模型对比</b>（IC 越高说明该模型在 当前范围 内越有 alpha）
                    </div>
                </div>

                <!-- 推荐 / 全 in-sample 警告 -->
                <div v-if="recommendedModel"
                     class="mb-[6px] p-[8px] bg-[#dcfce7] border border-[#86efac] rounded text-[11px]">
                    <div class="flex items-center gap-[6px]">
                        <span class="text-[#166534] font-bold">🏆 推荐使用</span>
                        <span class="font-mono text-[10px] text-[#166534]">{{ recommendedModel.filename }}</span>
                        <button v-if="!recommendedModel.is_active"
                                @click="activateModel(recommendedModel.path)"
                                class="ml-auto text-[10px] px-[8px] py-[2px] rounded bg-[#16a34a] text-white font-semibold hover:bg-[#15803d]">
                            一键切换到此模型
                        </button>
                        <span v-else class="ml-auto text-[10px] text-[#166534] font-semibold">✓ 已经在用</span>
                    </div>
                    <div class="text-[10px] text-[#166534] mt-[2px] pl-[2px]">
                        {{ recommendedModel.recommend_reason }}
                    </div>
                </div>
                <div v-else-if="allInSample"
                     class="mb-[6px] px-[10px] py-[5px] bg-[#fef3c7] border border-[#fbbf24] rounded text-[10px] flex items-center gap-[6px]"
                     title="所有模型 IC > 0.3，开卷自评。解法：DevTools 跑 await bt.runDump({ name: 'fresh' }) → 在「数据集」切到新文件 → 看真实 out-of-sample IC">
                    <span class="text-[#92400e] font-bold shrink-0">⚠ 所有模型都是训练集评估</span>
                    <span class="text-[#92400e]">IC &gt; 0.3 = 开卷自评。需新数据：在「新建回测」勾「🔮 顺手训 ML」，或 DevTools 跑 <code class="bg-white px-[2px] py-[1px] rounded font-mono">bt.runDump()</code></span>
                </div>

                <!-- 范围切片 filter（不同回测范围分开比对）-->
                <div class="mb-[8px] px-[8px] py-[6px] bg-white border border-[#e5e7eb] rounded text-[10px] flex flex-wrap items-center gap-[10px]">
                    <span class="text-[#92400e] font-semibold">🎯 当前范围:</span>

                    <!-- dataset -->
                    <span class="flex items-center gap-[3px]">
                        <span class="text-[#94a3b8]">数据集</span>
                        <select v-model="selectedDatasetForCompare" @change="loadMlHealth"
                                class="px-[5px] py-[1px] rounded border border-[#e5e7eb] bg-white">
                            <option :value="null">最新</option>
                            <option v-for="d in datasetsAvail" :key="d.filename" :value="d.filename">
                                {{ d.filename.replace('dataset_', '').replace('.ndjson', '') }} ({{ d.age_days }}d)
                            </option>
                        </select>
                    </span>

                    <!-- label horizon -->
                    <span class="flex items-center gap-[3px]">
                        <span class="text-[#94a3b8]">持有期</span>
                        <select v-model="compareScope.labelField" @change="loadMlHealth"
                                class="px-[5px] py-[1px] rounded border border-[#e5e7eb] bg-white">
                            <option v-for="l in LABEL_OPTIONS" :key="l.key" :value="l.key">{{ l.label }}</option>
                        </select>
                    </span>

                    <!-- signal source -->
                    <span class="flex items-center gap-[3px]">
                        <span class="text-[#94a3b8]">检测器</span>
                        <select v-model="compareScope.signalSource" @change="loadMlHealth"
                                class="px-[5px] py-[1px] rounded border border-[#e5e7eb] bg-white">
                            <option v-for="s in SIGNAL_SOURCE_OPTIONS" :key="s.key || 'all'" :value="s.key">{{ s.label }}</option>
                        </select>
                    </span>

                    <!-- boards (multi) -->
                    <span class="flex items-center gap-[3px]">
                        <span class="text-[#94a3b8]">板块</span>
                        <button v-for="b in BOARD_OPTIONS_FOR_SCOPE" :key="b.key"
                                @click="toggleScopeBoard(b.key); loadMlHealth()"
                                :class="['px-[5px] py-[1px] rounded border transition',
                                        (compareScope.boards || []).includes(b.key)
                                          ? 'bg-[#dc2626] text-white border-[#dc2626]'
                                          : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#dc2626]/40']">
                            {{ b.label }}
                        </button>
                        <button v-if="(compareScope.boards || []).length"
                                @click="compareScope.boards = null; loadMlHealth()"
                                class="text-[#94a3b8] hover:text-[#dc2626] px-[3px]"
                                title="清空板块筛选">✕</button>
                    </span>
                </div>
                <!-- 批量删除模型工具栏（有选中时显示）-->
                <div v-if="selectedModels.length"
                     class="mb-[5px] px-[8px] py-[4px] bg-[#fef2f2] border border-[#fecaca] rounded text-[11px] flex items-center gap-[8px]">
                    <span class="text-[#dc2626] font-semibold">已选 {{ selectedModels.length }} 个模型</span>
                    <button @click="selectedModels = []"
                            class="text-[10px] text-[#94a3b8] hover:text-[#dc2626] underline">清空</button>
                    <button @click="deleteSelectedModels"
                            :disabled="batchDeletingModels"
                            class="ml-auto text-[10px] px-[10px] py-[2px] rounded bg-[#dc2626] text-white hover:bg-[#b91c1c] disabled:opacity-50">
                        {{ batchDeletingModels ? '删除中...' : `🗑 删除选中` }}
                    </button>
                </div>

                <table class="w-full text-[11px] border-collapse">
                    <thead class="text-[10px] text-[#94a3b8]">
                        <tr>
                            <th class="px-[4px] py-[3px] text-center font-normal w-[20px]">
                                <input type="checkbox"
                                       :checked="modelsWithIc.length > 0 && modelsWithIc.every(m => m.is_active || selectedModels.includes(m.path))"
                                       @change="selectedModels = $event.target.checked ? modelsWithIc.filter(m => !m.is_active).map(m => m.path) : []"
                                       :title="'全选可删模型（排除激活中的）'"
                                       class="w-[12px] h-[12px] accent-[#dc2626] cursor-pointer"/>
                            </th>
                            <th class="px-[6px] py-[3px] text-left font-normal w-[16px]"></th>
                            <th class="px-[6px] py-[3px] text-left font-normal">模型</th>
                            <th class="px-[6px] py-[3px] text-right font-normal w-[60px]">训练距今</th>
                            <th class="px-[6px] py-[3px] text-right font-normal w-[60px]">样本 n</th>
                            <th class="px-[6px] py-[3px] text-right font-normal w-[70px]">IC 全样本</th>
                            <th class="px-[6px] py-[3px] text-right font-normal w-[80px]">IC threeStage</th>
                            <th class="px-[6px] py-[3px] text-center font-normal w-[80px]">状态</th>
                            <th class="px-[6px] py-[3px] text-center font-normal w-[80px]">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="m in modelsWithIc" :key="m.path"
                            class="border-t border-[#fce7e7]"
                            :class="[
                                m.is_active ? 'bg-[#fef9c3]' : '',
                                m.recommended && !m.is_active ? 'bg-[#dcfce7]' : '',
                                selectedModels.includes(m.path) ? 'bg-[#fef2f2]' : '',
                            ]">
                            <td class="px-[4px] py-[3px] text-center">
                                <input v-if="!m.is_active" type="checkbox"
                                       :checked="selectedModels.includes(m.path)"
                                       @change="toggleModelSelection(m.path)"
                                       class="w-[12px] h-[12px] accent-[#dc2626] cursor-pointer"/>
                                <span v-else class="text-[9px] text-[#94a3b8]">—</span>
                            </td>
                            <td class="px-[6px] py-[3px] text-center">
                                <span v-if="m.is_active" class="text-[#dc2626] font-bold" title="当前激活">●</span>
                                <span v-else-if="m.recommended" class="text-[#16a34a] font-bold" title="推荐使用">🏆</span>
                            </td>
                            <td class="px-[6px] py-[3px] font-mono text-[10px] truncate" :title="m.path">
                                {{ m.filename }}
                                <span v-if="m.label" class="ml-[4px] text-[#94a3b8]">[{{ m.label }}]</span>
                                <span v-if="m.recommended" class="ml-[4px] text-[10px] px-[5px] py-[1px] rounded bg-[#16a34a] text-white font-bold">推荐</span>
                            </td>
                            <td class="px-[6px] py-[3px] text-right tabular-nums text-[#666]">
                                {{ m.age_days != null ? m.age_days + 'd' : '—' }}
                            </td>
                            <td class="px-[6px] py-[3px] text-right tabular-nums text-[#666]">{{ m.n ?? '—' }}</td>
                            <td class="px-[6px] py-[3px] text-right tabular-nums font-bold"
                                :class="m.is_in_sample ? 'text-[#94a3b8]'
                                       : m.ic >= 0.05 ? 'text-[#166534]'
                                       : m.ic >= 0.02 ? 'text-[#92400e]'
                                       : 'text-[#dc2626]'">
                                {{ m.ic != null ? m.ic.toFixed(3) : '—' }}
                            </td>
                            <td class="px-[6px] py-[3px] text-right tabular-nums font-bold"
                                :class="m.is_in_sample ? 'text-[#94a3b8]'
                                       : m.ic_threestage >= 0.05 ? 'text-[#166534]'
                                       : m.ic_threestage >= 0.02 ? 'text-[#92400e]'
                                       : 'text-[#dc2626]'">
                                {{ m.ic_threestage != null ? m.ic_threestage.toFixed(3) : '—' }}
                            </td>
                            <td class="px-[6px] py-[3px] text-center">
                                <span :class="['inline-block px-[6px] py-[1px] rounded text-[10px] font-semibold', VERDICT_DISPLAY[m.verdict]?.cls || 'bg-[#f3f4f6]']">
                                    {{ VERDICT_DISPLAY[m.verdict]?.label || (m.error ? '✗ 错误' : m.verdict) }}
                                </span>
                            </td>
                            <td class="px-[6px] py-[3px] text-center">
                                <div class="flex items-center justify-center gap-[4px]">
                                    <button v-if="!m.is_active"
                                            @click="activateModel(m.path)"
                                            class="text-[10px] px-[6px] py-[1px] rounded border border-[#dc2626]/40 text-[#dc2626] bg-white hover:bg-[#fef2f2]">
                                        切换
                                    </button>
                                    <span v-else class="text-[10px] text-[#94a3b8]">使用中</span>
                                    <button v-if="!m.is_active"
                                            @click="deleteModelFile(m)"
                                            title="删除模型文件 + sidecar"
                                            class="text-[10px] px-[5px] py-[1px] rounded border border-[#94a3b8] text-[#94a3b8] hover:border-[#dc2626] hover:text-[#dc2626] hover:bg-[#fef2f2]">
                                        🗑
                                    </button>
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <div class="mt-[5px] text-[10px] text-[#94a3b8] leading-[1.5]">
                    ✓ 健康 0.05≤IC&lt;0.3 · ⚠ 衰减 0.02-0.05 · ✗ 失效 &lt;0.02 · ⊘ 训练集（mtime 检测 或 IC&gt;0.3 异常高）
                    <br/>
                    <span class="text-[#94a3b8]">
                        💡 正常股票预测 out-of-sample IC 范围 0.05-0.15；超过 0.3 几乎一定是开卷评估，
                        看到全表都是这种数字就先跑 <code class="bg-white px-[2px] rounded">bt.runDump()</code> 生成新数据集再回来对比
                    </span>
                </div>

                <!-- IC 历史趋势折线图（当前激活模型对各 dataset）-->
                <div v-if="hasIcTrend" class="mt-[10px] border border-[#e5e7eb] rounded bg-[#fafafa] p-[8px]">
                    <div class="flex items-center justify-between mb-[4px]">
                        <div class="text-[11px] text-[#666] font-semibold">📉 当前激活模型的 IC 历史趋势（最近 {{ icTrend.length }} 个 dataset）</div>
                        <div class="text-[10px] text-[#94a3b8]">绿虚 = 健康 0.05 / 黄点 = 衰减 0.02 / ⊘ = 训练集</div>
                    </div>
                    <div ref="icChartRef" class="w-full h-[170px]"></div>
                </div>
            </div>
            <div v-else class="text-[11px] text-[#94a3b8]">暂无模型 / 数据集，先在 量化 → 选股 → 主升突破 跑一次 bt.runDump() 生成数据。</div>

            </div><!-- /ML 面板 body padding -->
        </div>

        <!-- ============ 主区：左 list + 右 detail ============ -->
        <div class="flex flex-1 overflow-hidden">

        <!-- ============ 左侧：历史 runs 列表 ============ -->
        <div class="w-[420px] border-r border-[#e5e7eb] flex flex-col shrink-0">
            <!-- 头部 -->
            <div class="h-[44px] flex items-center px-[14px] border-b border-[#e5e7eb] bg-[#fafafa]">
                <span class="text-[13px] font-bold text-[#111]">回测历史</span>
                <span class="ml-[8px] text-[11px] text-[#94a3b8]">{{ runs.length }} 条</span>
                <button v-if="!runner.running.value"
                        @click="formExpanded = !formExpanded"
                        class="ml-auto text-[11px] px-[10px] py-[3px] rounded-[3px] border font-semibold transition"
                        :class="formExpanded
                            ? 'border-[#dc2626] bg-[#fef2f2] text-[#dc2626]'
                            : 'border-[#dc2626]/40 text-[#dc2626] bg-white hover:bg-[#fff5f5]'">
                    {{ formExpanded ? '✕ 收起' : '🚀 新建回测' }}
                </button>
                <button v-else disabled
                        class="ml-auto text-[11px] px-[10px] py-[3px] rounded-[3px] border border-[#e5e7eb] text-[#94a3b8]">
                    回测进行中...
                </button>
                <button @click="loadRuns" :disabled="loading"
                        class="ml-[6px] text-[11px] px-[8px] py-[3px] rounded-[3px] border border-[#e5e7eb]
                               hover:bg-white disabled:opacity-50">
                    刷新
                </button>
            </div>

            <!-- ============ Part A: 新建回测面板（折叠 / 展开）============ -->
            <div v-if="formExpanded && !runner.running.value"
                 class="px-[14px] py-[10px] border-b border-[#e5e7eb] bg-[#fafafa] text-[11px]">
                <div class="mb-[8px]">
                    <div class="text-[#666] mb-[4px]">板块</div>
                    <div class="flex flex-wrap gap-[5px]">
                        <button v-for="b in BOARD_OPTIONS" :key="b.key"
                                @click="toggleBoard(b.key)"
                                :title="b.desc"
                                :class="['px-[8px] py-[3px] rounded border transition',
                                        form.boards.includes(b.key)
                                          ? 'bg-[#dc2626] text-white border-[#dc2626]'
                                          : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#dc2626]/40']">
                            {{ b.label }}
                        </button>
                    </div>
                </div>
                <div class="mb-[8px] flex items-center gap-[8px]">
                    <span class="text-[#666] w-[40px]">持有</span>
                    <button v-for="d in [3, 5, 7, 14, 30]" :key="d"
                            @click="form.holdDays = d"
                            :class="['px-[8px] py-[2px] rounded border tabular-nums transition',
                                    form.holdDays === d
                                      ? 'bg-[#1e293b] text-white border-[#1e293b]'
                                      : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#1e293b]/40']">
                        {{ d }}d
                    </button>
                </div>

                <!-- 顺手训 ML 复选框（合并了原"智能重训"入口）-->
                <div class="mb-[8px] p-[8px] rounded border transition"
                     :class="form.trainMl
                              ? 'border-[#16a34a] bg-[#dcfce7]'
                              : 'border-[#e5e7eb] bg-white'">
                    <label class="flex items-start gap-[8px] cursor-pointer">
                        <input type="checkbox" v-model="form.trainMl"
                               class="mt-[2px] w-[14px] h-[14px] accent-[#16a34a] cursor-pointer"/>
                        <span class="flex-1">
                            <span class="text-[#166534] font-semibold">🔮 顺手训练 ML 模型</span>
                            <span class="text-[10px] text-[#475569] ml-[4px]">（多 ~5 分钟，让 mlScore 在扫描器里变可信）</span>
                            <div v-if="form.trainMl" class="mt-[5px] flex items-center gap-[6px] flex-wrap">
                                <span class="text-[10px] text-[#166534]">标签:</span>
                                <button v-for="l in RETRAIN_LABELS.filter(x => x.key.endsWith('Profitable'))" :key="l.key"
                                        @click.stop="form.mlLabel = l.key"
                                        :title="l.desc"
                                        :class="['px-[7px] py-[1px] rounded text-[10px] border font-mono transition',
                                                form.mlLabel === l.key
                                                  ? 'bg-[#1e293b] text-white border-[#1e293b]'
                                                  : 'bg-white text-[#475569] border-[#e5e7eb] hover:border-[#1e293b]/40']">
                                    {{ l.key }}
                                </button>
                            </div>
                            <div v-if="form.trainMl" class="text-[10px] text-[#166534] mt-[4px] leading-[1.5]">
                                ✓ 跑完后会自动激活新模型，老 ML 模型保留可在 ML 健康面板对比 / 切换
                            </div>
                        </span>
                    </label>
                </div>

                <div class="flex items-center gap-[8px] pt-[6px] border-t border-[#e5e7eb]">
                    <span class="text-[10px] text-[#94a3b8]">
                        主升突破 detector ·
                        <span v-if="!form.trainMl">单次约 5-10 分钟（仅回测）</span>
                        <span v-else>单次约 10-15 分钟（回测 + 训练 ML）</span>
                    </span>
                    <button @click="startNewBacktest"
                            :class="['ml-auto text-[11px] px-[14px] py-[5px] rounded text-white font-semibold transition',
                                    form.trainMl
                                      ? 'bg-[#16a34a] hover:bg-[#15803d]'
                                      : 'bg-[#dc2626] hover:bg-[#b91c1c]']">
                        {{ form.trainMl ? '▶ 开始（回测 + 训 ML）' : '▶ 开始回测' }}
                    </button>
                </div>
            </div>

            <!-- 进度条（运行中显示）-->
            <div v-if="runner.running.value"
                 class="px-[14px] py-[10px] border-b border-[#e5e7eb] bg-[#fef2f2]">
                <div class="flex items-center justify-between text-[11px] mb-[5px]">
                    <span class="text-[#7c2d12] font-semibold">
                        🔄 扫描中
                        <span class="tabular-nums">{{ runner.scanned.value }} / {{ runner.total.value }}</span>
                        <span class="text-[10px] text-[#94a3b8] ml-[6px] font-mono">{{ runner.currentCode.value }}</span>
                    </span>
                    <button @click="runner.cancel()"
                            class="text-[10px] px-[8px] py-[2px] rounded border border-[#dc2626]/40 text-[#dc2626] hover:bg-white">
                        取消
                    </button>
                </div>
                <div class="h-[4px] bg-[#fecaca] rounded-full overflow-hidden">
                    <div class="h-full bg-[#dc2626] transition-all duration-200"
                         :style="{ width: runner.progressPct.value + '%' }"></div>
                </div>
                <div class="mt-[3px] text-[10px] text-[#94a3b8] tabular-nums">
                    {{ runner.progressPct.value }}%
                    <span v-if="runner.errorCount.value > 0" class="ml-[8px] text-[#dc2626]">· 失败 {{ runner.errorCount.value }} 只</span>
                </div>
            </div>

            <!-- 多选工具栏（对比 + 批量删除）-->
            <div class="h-[34px] flex items-center px-[10px] gap-[6px] border-b border-[#e5e7eb] bg-[#fafafa] text-[11px]">
                <span class="text-[#666]">已选</span>
                <span :class="['tabular-nums', compareSelected.length > 0 ? 'text-[#dc2626] font-bold' : 'text-[#94a3b8]']">
                    {{ compareSelected.length }}
                </span>
                <button v-if="compareSelected.length"
                        @click="clearCompare"
                        class="text-[10px] text-[#94a3b8] hover:text-[#dc2626] underline">清空</button>
                <button @click="startCompare"
                        :disabled="compareSelected.length < 2 || compareLoading"
                        class="ml-auto text-[11px] px-[10px] py-[3px] rounded border border-[#dc2626]/40 bg-white text-[#dc2626] hover:bg-[#fff5f5] disabled:opacity-40 disabled:cursor-not-allowed">
                    {{ compareLoading ? '加载...' : `📊 对比 (${compareSelected.length})` }}
                </button>
                <button @click="deleteSelectedRuns"
                        :disabled="compareSelected.length < 1 || batchDeleting"
                        class="text-[11px] px-[10px] py-[3px] rounded border border-[#dc2626] bg-white text-[#dc2626] hover:bg-[#fef2f2] disabled:opacity-40 disabled:cursor-not-allowed">
                    {{ batchDeleting ? '删除中...' : `🗑 删除 (${compareSelected.length})` }}
                </button>
            </div>

            <!-- 列表 -->
            <div class="flex-1 overflow-auto custom-scrollbar">
                <div v-if="loading" class="py-[60px] text-center text-[12px] text-[#94a3b8]">加载中...</div>
                <div v-else-if="!runs.length" class="py-[80px] px-[16px] text-center text-[12px] text-[#94a3b8]">
                    还没有回测记录。<br/>
                    在 DevTools console 跑 <code class="bg-[#f3f4f6] px-[4px] rounded">bt.runAll()</code> 或
                    <code class="bg-[#f3f4f6] px-[4px] rounded">bt.gridSearch()</code> 后会自动入库。
                </div>
                <div v-else>
                    <div v-for="r in runs" :key="r.id"
                         @click="selectRun(r.id)"
                         class="px-[12px] py-[10px] border-b border-[#f0f0f0] cursor-pointer transition-colors flex gap-[8px]"
                         :class="[
                             selectedRun === r.id ? 'bg-[#fff5f5]' : 'hover:bg-[#fafafa]',
                             compareSelected.includes(r.id) ? 'border-l-[3px] border-l-[#dc2626]' : '',
                         ]">
                        <!-- 对比 checkbox（C2）-->
                        <label class="flex items-start pt-[2px]" @click.stop>
                            <input type="checkbox"
                                   :checked="compareSelected.includes(r.id)"
                                   @change="toggleCompare(r.id, $event)"
                                   class="w-[13px] h-[13px] accent-[#dc2626] cursor-pointer"/>
                        </label>
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-[6px] text-[12px]">
                                <span class="font-bold text-[#111]">#{{ r.id }}</span>
                                <span :class="['px-[5px] py-[1px] rounded-[3px] text-[10px] font-semibold', RUN_TYPE_LABEL[r.run_type]?.cls || 'bg-[#e5e7eb] text-[#666]']">
                                    {{ RUN_TYPE_LABEL[r.run_type]?.text || r.run_type }}
                                </span>
                                <span class="text-[#94a3b8] ml-auto tabular-nums">{{ fmtTime(r.run_at) }}</span>
                            </div>
                            <div class="flex items-center gap-[6px] mt-[3px] text-[10px] text-[#666]">
                                <span>{{ r.sample_size }} 只</span>
                                <span>·</span>
                                <span>持有 {{ r.hold_days }} 天</span>
                                <span v-if="r.produced_dataset || r.produced_model"
                                      class="text-[#166534]"
                                      :title="(r.produced_dataset ? '数据集: ' + r.produced_dataset : '') + (r.produced_model ? '\n模型: ' + r.produced_model.split(/[\\/]/).pop() : '')">
                                    📎
                                </span>
                                <span v-if="r.notes" class="ml-auto text-[#dc2626] truncate" :title="r.notes">
                                    📝 {{ r.notes }}
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ============ 右侧：对比视图 / 单 run 详情 ============ -->
        <div class="flex-1 flex flex-col overflow-hidden min-w-0">

            <!-- C2: 对比视图 -->
            <div v-if="compareMode" class="flex-1 overflow-auto custom-scrollbar p-[16px]">
                <div class="flex items-center gap-[8px] mb-[12px]">
                    <span class="text-[14px] font-bold text-[#111]">📊 并排对比</span>
                    <span class="text-[11px] text-[#94a3b8]">基准 = #{{ compareDetails[0]?.id }} · 差值阈值 ±2pp</span>
                    <button @click="clearCompare" class="ml-auto text-[11px] px-[8px] py-[3px] rounded border border-[#e5e7eb] hover:bg-[#fafafa]">退出对比</button>
                </div>

                <div v-if="compareLoading" class="text-[12px] text-[#94a3b8]">加载对比数据...</div>
                <table v-else class="w-full text-[11px] border-collapse">
                    <thead class="sticky top-0 bg-white">
                        <tr class="border-b-2 border-[#e5e7eb]">
                            <th class="py-[6px] px-[8px] text-left text-[#666] font-normal w-[220px]">指标</th>
                            <th v-for="(d, i) in compareDetails" :key="d.id"
                                class="py-[6px] px-[8px] text-right font-bold"
                                :class="i === 0 ? 'text-[#dc2626] bg-[#fff5f5]' : 'text-[#111]'">
                                <div>#{{ d.id }}</div>
                                <div class="text-[9px] text-[#94a3b8] font-normal">{{ RUN_TYPE_LABEL[d.run_type]?.text || d.run_type }}</div>
                                <div class="text-[9px] text-[#94a3b8] font-normal">{{ fmtTime(d.run_at)?.slice(5, 16) }}</div>
                                <div v-if="i === 0" class="text-[8px] text-[#dc2626] font-bold mt-[2px]">基准</div>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="(row, ri) in compareMetrics" :key="ri"
                            class="border-b border-[#f0f0f0]"
                            :class="row.path === 'sample_size' || row.path === 'overall.winRate' ? 'bg-[#fafafa]' : ''">
                            <td class="py-[5px] px-[8px] text-[#475569] text-[11px]" :title="row.hint">
                                {{ row.label }}
                                <span v-if="row.hint" class="text-[#cbd5e1] text-[10px] cursor-help">ⓘ</span>
                            </td>
                            <td v-for="(v, i) in row.vals" :key="i"
                                class="py-[5px] px-[8px] text-right tabular-nums">
                                <div class="font-semibold text-[#111]">{{ fmtMetric(v, row.kind) }}</div>
                                <div v-if="i > 0 && row.vals[0] != null && v != null"
                                     class="text-[10px]"
                                     :class="diffVsBaseline(v, row.vals[0], row.kind).cls">
                                    <span class="mr-0.5">{{ diffVsBaseline(v, row.vals[0], row.kind).sym }}</span>
                                    {{ diffVsBaseline(v, row.vals[0], row.kind).txt }}
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>

                <div class="mt-[12px] text-[10px] text-[#94a3b8]">
                    ↑ 进步 ≥ +2pp · ↓ 退步 ≤ -2pp · ↗/↘ 轻微变化（&lt;2pp）· → 几乎相同
                </div>
                <div class="mt-[10px] p-[8px] bg-[#f8fafc] border border-[#e5e7eb] rounded text-[10px] text-[#475569]">
                    <strong>备注：</strong>
                    <span v-for="(d, i) in compareDetails" :key="d.id">
                        <span v-if="i > 0"> · </span>
                        #{{ d.id }} {{ d.notes || '—' }}
                    </span>
                </div>
            </div>

            <div v-else-if="!selectedDetail" class="flex-1 flex items-center justify-center text-[13px] text-[#94a3b8]">
                ← 从左侧列表选一条 run 查看详情<br/>
                <span class="text-[11px] mt-[6px] text-[#cbd5e1]">或勾选 2-4 条 → 对比按钮</span>
            </div>
            <div v-else class="flex-1 overflow-auto custom-scrollbar p-[16px]">
                <!-- run 头部信息 -->
                <div class="flex items-center gap-[8px] mb-[12px]">
                    <span class="text-[16px] font-bold text-[#111]">#{{ selectedDetail.id }}</span>
                    <span :class="['px-[6px] py-[2px] rounded-[3px] text-[11px] font-semibold', RUN_TYPE_LABEL[selectedDetail.run_type]?.cls || 'bg-[#e5e7eb] text-[#666]']">
                        {{ RUN_TYPE_LABEL[selectedDetail.run_type]?.text || selectedDetail.run_type }}
                    </span>
                    <span class="text-[12px] text-[#94a3b8]">{{ fmtTime(selectedDetail.run_at) }}</span>
                    <button @click="updateNote(selectedDetail.id, selectedDetail.notes)"
                            class="ml-auto text-[11px] px-[8px] py-[3px] rounded-[3px] border border-[#e5e7eb] hover:bg-[#fafafa]">
                        📝 备注
                    </button>
                    <button @click="deleteRun(selectedDetail.id)"
                            class="text-[11px] px-[8px] py-[3px] rounded-[3px] border border-[#fecaca] text-[#dc2626] hover:bg-[#fef2f2]">
                        删除
                    </button>
                </div>

                <!-- 基础信息 -->
                <div class="text-[12px] text-[#666] mb-[16px] flex flex-wrap gap-[16px]">
                    <span>样本：<b class="text-[#111]">{{ selectedDetail.sample_size }}</b> 只</span>
                    <span>持有期：<b class="text-[#111]">{{ selectedDetail.hold_days }}</b> 天</span>
                    <span v-if="selectedDetail.boards">板块：{{ selectedDetail.boards.join(' / ') }}</span>
                </div>

                <!-- 关联文件（智能重训的 run 才会有）-->
                <div v-if="selectedDetail.produced_dataset || selectedDetail.produced_model"
                     class="mb-[16px] p-[8px] bg-[#dcfce7] border border-[#86efac] rounded text-[11px]">
                    <div class="text-[#166534] font-semibold mb-[3px]">📎 本次回测产生的文件（删除 run 时可一并清理）</div>
                    <div v-if="selectedDetail.produced_dataset" class="text-[10px] text-[#166534]">
                        · 数据集 <code class="font-mono bg-white px-[3px] rounded">{{ selectedDetail.produced_dataset }}</code>
                    </div>
                    <div v-if="selectedDetail.produced_model" class="text-[10px] text-[#166534]">
                        · 模型 <code class="font-mono bg-white px-[3px] rounded">{{ selectedDetail.produced_model.split(/[\\/]/).pop() }}</code>
                    </div>
                </div>
                <div v-if="selectedDetail.notes" class="mb-[16px] p-[10px] bg-[#fef3c7] rounded-[4px] text-[12px] text-[#92400e]">
                    📝 {{ selectedDetail.notes }}
                </div>

                <!-- ====== Part C: 4 主指标 stat 卡片 ====== -->
                <div v-if="keyStats" class="grid grid-cols-4 gap-[10px] mb-[16px]">
                    <div class="bg-white border border-[#e5e7eb] rounded-[6px] p-[10px]">
                        <div class="text-[10px] text-[#94a3b8] mb-[3px]">总笔数</div>
                        <div class="text-[18px] font-bold tabular-nums text-[#111]">{{ keyStats.total }}</div>
                    </div>
                    <div class="bg-white border border-[#e5e7eb] rounded-[6px] p-[10px]">
                        <div class="text-[10px] text-[#94a3b8] mb-[3px]">胜率</div>
                        <div class="text-[18px] font-bold tabular-nums"
                             :class="keyStats.winRate == null ? 'text-[#cbd5e1]'
                                   : keyStats.winRate >= 0.7 ? 'text-[#dc2626]'
                                   : keyStats.winRate >= 0.5 ? 'text-[#b45309]'
                                   : 'text-[#1e40af]'">
                            {{ keyStats.winRate != null ? (keyStats.winRate * 100).toFixed(1) + '%' : '—' }}
                        </div>
                    </div>
                    <div class="bg-white border border-[#e5e7eb] rounded-[6px] p-[10px]">
                        <div class="text-[10px] text-[#94a3b8] mb-[3px]">平均收益</div>
                        <div class="text-[18px] font-bold tabular-nums"
                             :class="keyStats.avgReturn == null ? 'text-[#cbd5e1]'
                                   : keyStats.avgReturn > 0 ? 'text-[#dc2626]' : 'text-[#1e40af]'">
                            <span v-if="keyStats.avgReturn != null">
                                <span class="text-[12px]">{{ keyStats.avgReturn > 0 ? '▲ +' : '▼ ' }}</span>{{ keyStats.avgReturn.toFixed(2) }}%
                            </span>
                            <span v-else>—</span>
                        </div>
                    </div>
                    <div class="bg-white border border-[#e5e7eb] rounded-[6px] p-[10px]">
                        <div class="text-[10px] text-[#94a3b8] mb-[3px]">最大回撤</div>
                        <div class="text-[18px] font-bold tabular-nums"
                             :class="keyStats.maxDrawdown == null ? 'text-[#cbd5e1]' : 'text-[#1e40af]'">
                            <span v-if="keyStats.maxDrawdown != null">{{ keyStats.maxDrawdown.toFixed(2) }}%</span>
                            <span v-else class="text-[11px] text-[#cbd5e1]">老 run 无</span>
                        </div>
                    </div>
                </div>

                <!-- ====== Part B: 资金曲线（如果有 equityCurve）====== -->
                <div v-if="selectedDetail.summary?.equityCurve?.length"
                     class="mb-[16px] bg-white border border-[#e5e7eb] rounded-[6px] p-[10px]">
                    <div class="flex items-center justify-between mb-[4px]">
                        <div class="text-[12px] font-semibold text-[#111]">📈 资金曲线（等额单仓位 · 按平仓日累计）</div>
                        <div class="text-[10px] text-[#94a3b8] tabular-nums">
                            起 0% · 终
                            <span :class="(keyStats?.finalCum ?? 0) >= 0 ? 'text-[#dc2626] font-semibold' : 'text-[#1e40af] font-semibold'">
                                {{ (keyStats?.finalCum ?? 0) >= 0 ? '+' : '' }}{{ (keyStats?.finalCum ?? 0).toFixed(2) }}%
                            </span>
                            · 最大回撤 <span class="text-[#1e40af] font-semibold">{{ keyStats?.maxDrawdown?.toFixed?.(2) || 0 }}%</span>
                        </div>
                    </div>
                    <div ref="equityChartRef" class="w-full h-[200px]"></div>
                </div>
                <div v-else-if="overallStats(selectedDetail)"
                     class="mb-[16px] p-[10px] bg-[#fafafa] border border-dashed border-[#e5e7eb] rounded text-[11px] text-[#94a3b8]">
                    💡 此 run 没有资金曲线数据（创建于功能上线前）。重新发起一次回测就能看到。
                </div>

                <!-- ====== Part C: 按 突破次日确认 强度 拆分（实际胜率 vs 预期）====== -->
                <div v-if="byStarRows.length" class="mb-[16px] bg-white border border-[#e5e7eb] rounded-[6px] overflow-hidden">
                    <div class="px-[12px] py-[8px] bg-[#fafafa] border-b border-[#e5e7eb] flex items-baseline gap-[8px]">
                        <span class="text-[12px] font-bold text-[#111]">按 突破次日确认强度 拆分</span>
                        <span class="text-[10px] text-[#94a3b8]">— 看实际胜率跟历史预期的偏离</span>
                    </div>
                    <!-- 列头 -->
                    <div class="grid grid-cols-[160px_60px_1fr_90px_70px] gap-[10px] px-[12px] py-[5px] text-[10px] text-[#94a3b8] border-b border-[#f1f5f9]">
                        <span>评级</span>
                        <span class="text-right">笔数</span>
                        <span>实际胜率 vs 预期</span>
                        <span class="text-right">偏离</span>
                        <span class="text-right">平均收益</span>
                    </div>
                    <!-- 数据行 -->
                    <div v-for="r in byStarRows" :key="r.key"
                         class="grid grid-cols-[160px_60px_1fr_90px_70px] gap-[10px] items-center px-[12px] py-[7px] text-[11px] tabular-nums border-b border-[#f5f5f5] last:border-b-0 hover:bg-[#fafafa]">
                        <!-- 评级 -->
                        <span class="font-semibold" :class="r.cls">{{ r.label }}</span>
                        <!-- 笔数 -->
                        <span class="text-right text-[#475569]">{{ r.count }}</span>
                        <!-- 胜率 vs 预期 进度条（直观对比）-->
                        <div class="flex items-center gap-[8px]">
                            <div class="flex-1 h-[14px] bg-[#f1f5f9] rounded relative overflow-hidden">
                                <!-- 预期线（虚线）-->
                                <div v-if="r.expected != null"
                                     class="absolute top-0 bottom-0 border-l-2 border-dashed border-[#94a3b8] z-[1]"
                                     :style="{ left: (r.expected * 100) + '%' }"
                                     :title="`预期 ${(r.expected * 100).toFixed(0)}%`"></div>
                                <!-- 实际胜率条 -->
                                <div class="h-full transition-all"
                                     :class="r.winRate >= 0.7 ? 'bg-[#dc2626]'
                                           : r.winRate >= 0.5 ? 'bg-[#f59e0b]'
                                           : 'bg-[#1e40af]'"
                                     :style="{ width: (r.winRate * 100).toFixed(1) + '%' }"></div>
                            </div>
                            <span class="font-bold w-[44px] text-right"
                                  :class="r.winRate >= 0.7 ? 'text-[#dc2626]'
                                        : r.winRate >= 0.5 ? 'text-[#b45309]'
                                        : 'text-[#1e40af]'">
                                {{ (r.winRate * 100).toFixed(1) }}%
                            </span>
                            <span v-if="r.expected != null" class="text-[10px] text-[#94a3b8] w-[44px] text-right">
                                / {{ (r.expected * 100).toFixed(0) }}%
                            </span>
                        </div>
                        <!-- 偏离徽章 -->
                        <span v-if="r.gap != null"
                              class="justify-self-end text-[10px] px-[6px] py-[1px] rounded font-semibold whitespace-nowrap"
                              :class="r.gap >= 0
                                       ? 'bg-[#dcfce7] text-[#166534]'
                                       : (r.gap >= -0.03
                                          ? 'bg-[#f1f5f9] text-[#475569]'
                                          : 'bg-[#eff6ff] text-[#1e40af]')">
                            {{ r.gap >= 0 ? '↑ 超出' : (r.gap >= -0.03 ? '→ 持平' : '↓ 偏低') }}
                            {{ r.gap >= 0 ? '+' : '' }}{{ (r.gap * 100).toFixed(1) }}pp
                        </span>
                        <span v-else class="justify-self-end text-[10px] text-[#cbd5e1]">—</span>
                        <!-- 平均收益 -->
                        <span class="text-right font-semibold"
                              :class="r.avgRet > 0 ? 'text-[#dc2626]' : r.avgRet < 0 ? 'text-[#1e40af]' : 'text-[#94a3b8]'">
                            {{ r.avgRet > 0 ? '+' : '' }}{{ r.avgRet.toFixed(2) }}%
                        </span>
                    </div>
                    <!-- 说明脚注 -->
                    <div class="px-[12px] py-[5px] bg-[#fafafa] border-t border-[#e5e7eb] text-[10px] text-[#94a3b8] flex items-center gap-[10px]">
                        <span><span class="inline-block w-[8px] h-[2px] bg-[#94a3b8] align-middle mr-[2px]" style="border-top: 1px dashed #94a3b8"></span> 预期胜率参考线</span>
                        <span>·</span>
                        <span>实际 ≥ 预期 = 策略仍有效；偏低超 3pp = 该子集需重点观察</span>
                    </div>
                </div>

                <!-- ====== 旧版完整 stats 详情（折叠保留供查）====== -->
                <details class="mb-[16px]">
                    <summary class="text-[11px] text-[#94a3b8] cursor-pointer hover:text-[#475569]">
                        🔍 完整 stats 详表（overall / byGrade / byBreakoutConfirm / byWeekly）
                    </summary>
                    <div class="mt-[10px]">

                <!-- ===== runAll / runV0 类型：显示 overall + byGrade + byWeekly ===== -->
                <template v-if="overallStats(selectedDetail)">
                    <div class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">总体指标</h3>
                        <table class="w-full text-[12px] border-collapse">
                            <tbody>
                                <tr v-for="(v, k) in overallStats(selectedDetail)" :key="k"
                                    class="border-b border-[#f0f0f0]">
                                    <td class="py-[4px] text-[#666] w-[140px]">{{ cnMetric(k) }}</td>
                                    <td class="py-[4px] text-[#111] tabular-nums">
                                        {{ typeof v === 'number'
                                            ? (k.endsWith('Rate') || k === 'avgReturn' || k === 'medianReturn' || k === 'maxReturn' || k === 'minReturn' || k === 'p25' || k === 'p75')
                                                ? (v < 1 && v > -1 ? fmtPct(v) : v.toFixed(2))
                                                : v
                                            : v }}
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div v-if="selectedDetail.summary.byGrade" class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">按评级分组</h3>
                        <table class="w-full text-[11px] border-collapse">
                            <thead>
                                <tr class="border-b border-[#e5e7eb] bg-[#fafafa]">
                                    <th class="py-[5px] px-[6px] text-left text-[#666] font-normal">评级</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">笔数</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">胜率</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">平均收益</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(stats, g) in selectedDetail.summary.byGrade" :key="g"
                                    class="border-b border-[#f0f0f0]">
                                    <td class="py-[4px] px-[6px] font-bold">{{ cnGrade(g) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ stats.count }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ fmtPct(stats.winRate) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums"
                                        :class="(stats.avgReturn ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#059669]'">
                                        {{ (stats.avgReturn ?? 0).toFixed(2) }}%
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <!-- N+1 突破次日确认切片：当前最具预测力的维度 -->
                    <div v-if="selectedDetail.summary.byBreakoutConfirm" class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">🎯 突破次日确认（最具预测力）</h3>
                        <table class="w-full text-[11px] border-collapse">
                            <thead>
                                <tr class="border-b border-[#e5e7eb] bg-[#fafafa]">
                                    <th class="py-[5px] px-[6px] text-left text-[#666] font-normal">确认强度</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">笔数</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">胜率</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">平均收益</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">收益中位数</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(stats, k) in selectedDetail.summary.byBreakoutConfirm" :key="k"
                                    v-show="stats?.count"
                                    class="border-b border-[#f0f0f0]"
                                    :class="k === 'strong' ? 'bg-[#fee2e2]' : k === 'medium' ? 'bg-[#fef3c7]' : ''">
                                    <td class="py-[4px] px-[6px] font-bold">{{ cnGrade(k) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ stats.count }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums font-bold">{{ fmtPct(stats.winRate) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums"
                                        :class="(stats.avgReturn ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#059669]'">
                                        {{ (stats.avgReturn ?? 0).toFixed(2) }}%
                                    </td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ (stats.medianReturn ?? 0).toFixed(2) }}%</td>
                                </tr>
                            </tbody>
                        </table>
                        <div class="text-[10px] text-[#94a3b8] mt-[3px]">老版本回测此切片为空</div>
                    </div>

                    <!-- 信号源切片 -->
                    <div v-if="selectedDetail.summary.bySignalSource && Object.keys(selectedDetail.summary.bySignalSource).length" class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">📡 按信号源拆分</h3>
                        <table class="w-full text-[11px] border-collapse">
                            <thead>
                                <tr class="border-b border-[#e5e7eb] bg-[#fafafa]">
                                    <th class="py-[5px] px-[6px] text-left text-[#666] font-normal">信号源</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">笔数</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">胜率</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">平均收益</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">收益中位数</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(stats, src) in selectedDetail.summary.bySignalSource" :key="src"
                                    v-show="stats?.count"
                                    class="border-b border-[#f0f0f0]">
                                    <td class="py-[4px] px-[6px] font-bold">{{ cnSource(src) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ stats.count }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums font-bold">{{ fmtPct(stats.winRate) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums"
                                        :class="(stats.avgReturn ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#059669]'">
                                        {{ (stats.avgReturn ?? 0).toFixed(2) }}%
                                    </td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ (stats.medianReturn ?? 0).toFixed(2) }}%</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div v-if="selectedDetail.summary.byGradeWeekly" class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">评级 × 周线确认 交叉表</h3>
                        <table class="w-full text-[11px] border-collapse">
                            <thead>
                                <tr class="border-b border-[#e5e7eb] bg-[#fafafa]">
                                    <th class="py-[5px] px-[6px] text-left text-[#666] font-normal">组合（评级 | 周线）</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">笔数</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">胜率</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal">平均收益</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(stats, key) in selectedDetail.summary.byGradeWeekly" :key="key"
                                    class="border-b border-[#f0f0f0]">
                                    <td class="py-[4px] px-[6px] text-[10px]">
                                        {{ key.split('|').map(p => cnGrade(p)).join(' | ') }}
                                    </td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ stats.count }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ fmtPct(stats.winRate) }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums"
                                        :class="(stats.avgReturn ?? 0) >= 0 ? 'text-[#dc2626]' : 'text-[#059669]'">
                                        {{ (stats.avgReturn ?? 0).toFixed(2) }}%
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </template>

                <!-- 网格搜索：显示 Top 10 -->
                <template v-if="selectedDetail.top_combos">
                    <div class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">🏆 胜率 Top 10 参数组合</h3>
                        <table class="w-full text-[11px] border-collapse">
                            <thead>
                                <tr class="border-b border-[#e5e7eb] bg-[#fafafa]">
                                    <th class="py-[5px] px-[6px] text-left text-[#666] font-normal w-[40px]">排名</th>
                                    <th class="py-[5px] px-[6px] text-left text-[#666] font-normal">参数组合</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal w-[60px]">笔数</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal w-[60px]">胜率</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal w-[70px]">平均收益</th>
                                    <th class="py-[5px] px-[6px] text-right text-[#666] font-normal w-[70px]">中位数</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(c, i) in selectedDetail.top_combos" :key="i"
                                    class="border-b border-[#f0f0f0]"
                                    :class="i === 0 ? 'bg-[#fef3c7]' : ''">
                                    <td class="py-[4px] px-[6px] font-bold">{{ i + 1 }}</td>
                                    <td class="py-[4px] px-[6px] font-mono text-[10px]">{{ c.combo }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ c.trades }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums font-semibold">{{ c['win%'] }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ c.avgRet }}</td>
                                    <td class="py-[4px] px-[6px] text-right tabular-nums">{{ c.median }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    <div v-if="selectedDetail.detector_opts?.params" class="mb-[16px]">
                        <h3 class="text-[13px] font-bold text-[#111] mb-[6px]">网格定义</h3>
                        <table class="w-full text-[11px] border-collapse">
                            <tbody>
                                <tr v-for="(vals, k) in selectedDetail.detector_opts.params" :key="k"
                                    class="border-b border-[#f0f0f0]">
                                    <td class="py-[4px] px-[6px] text-[#666] font-mono">{{ k }}</td>
                                    <td class="py-[4px] px-[6px] font-mono">[{{ vals.join(', ') }}]</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </template>

                    </div><!-- /完整 stats 详表 折叠 -->
                </details>

                <!-- 折叠：原始 JSON（仅 debug 用）-->
                <details class="mt-[16px]">
                    <summary class="text-[11px] text-[#94a3b8] cursor-pointer">原始 JSON (debug)</summary>
                    <pre class="mt-[8px] p-[8px] bg-[#f9fafb] text-[10px] overflow-auto rounded-[3px] border border-[#e5e7eb] max-h-[400px]">{{ JSON.stringify(selectedDetail, null, 2) }}</pre>
                </details>
            </div>
        </div>

        </div><!-- /主区 -->
    </div>
</template>
