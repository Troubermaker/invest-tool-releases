<script setup>
/**
 * 交易日志 view —— 实战账本（vs 候选池的 paper trading）。
 *
 * 功能：
 *   - 展示所有 trades（持仓中 / 已平仓）
 *   - 顶部统计：本月真实胜率 / 平均盈亏 / 对比预期
 *   - 行内操作：平仓 / 编辑止盈止损 / 删除
 *   - 仓位建议提示（按星级 + 当前持仓状态）
 */
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api/client'
import { pushSuccess, pushError } from '../composables/useNotifications'
import { confirmDialog } from '../composables/useConfirm'
import { openStockChart } from '../composables/useStockChart'

const trades = ref([])
const summary = ref(null)
const loading = ref(false)
const filterStatus = ref('all')   // 'all' | 'open' | 'closed'
const filterSource = ref(null)    // null | 'main_breakout' | ...

// 周维度趋势：跟 filter 解耦，独立拉一份近 ~10 周的已平仓样本
const trendTrades = ref([])
const trendWeeks = 8

const SOURCE_DISPLAY = {
    main_breakout:    { label: '主升突破', cls: 'bg-[#cffafe] text-[#155e75] border-[#a5f3fc]' },
    breakout_eve:     { label: '突破前夜', cls: 'bg-[#fef3c7] text-[#92400e] border-[#fde68a]' },
    dragon_return:    { label: '龙回头',   cls: 'bg-[#fee2e2] text-[#991b1b] border-[#fecaca]' },
    limit_up_relay:   { label: '连板游资', cls: 'bg-[#ffedd5] text-[#9a3412] border-[#fed7aa]' },
    manual:           { label: '手动',     cls: 'bg-[#f3f4f6] text-[#475569] border-[#e5e7eb]' },
}

const EXIT_REASON_DISPLAY = {
    stop_loss:    { label: '止损', cls: 'text-[#7f1d1d]' },
    take_profit:  { label: '止盈', cls: 'text-[#15803d]' },
    time_out:     { label: '时间', cls: 'text-[#666]'    },
    manual:       { label: '手动', cls: 'text-[#475569]' },
    invalid:      { label: '作废', cls: 'text-[#7f1d1d]' },
}

async function loadTrades() {
    loading.value = true
    try {
        const [resTrades, resSummary, resTrend] = await Promise.all([
            api.listTradeJournal(filterStatus.value, filterSource.value, 500),
            api.tradeJournalSummary(30),
            api.listTradeJournal('closed', null, 500),
        ])
        if (resTrades?.ok) trades.value = resTrades.data || []
        if (resSummary?.ok) summary.value = resSummary.data
        if (resTrend?.ok) trendTrades.value = resTrend.data || []
        // 给持仓中的票注实时行情 → 计算实时盈亏率
        await injectLivePnl()
    } catch (e) {
        pushError(`加载失败: ${e}`)
    } finally {
        loading.value = false
    }
}

async function injectLivePnl() {
    const openTrades = trades.value.filter(t => !t.exit_at)
    if (!openTrades.length) return
    try {
        const codes = [...new Set(openTrades.map(t => t.code))]
        const r = await api.getBatchQuotes(codes)
        const qMap = (r?.ok && r.data) || {}
        for (const t of openTrades) {
            const q = qMap[t.code]
            if (q?.price && t.entry_price > 0) {
                t.currentPrice = q.price
                t.pnlLive = ((q.price - t.entry_price) / t.entry_price) * 100
            }
        }
        // 触发响应式更新
        trades.value = [...trades.value]
    } catch (e) {
        console.warn('[journal] 实时行情拉取失败，持仓中显示静态价', e)
    }
}

// ============ 周维度趋势计算 ============
// ISO week key: 'YYYY-Www'，跟 holdout 周次一致
function isoWeekKey(d) {
    const dt = new Date(d.valueOf())
    dt.setHours(0, 0, 0, 0)
    // ISO weeks 用 Thursday 决定年份
    dt.setDate(dt.getDate() + 3 - ((dt.getDay() + 6) % 7))
    const week1 = new Date(dt.getFullYear(), 0, 4)
    const wn = 1 + Math.round(((dt - week1) / 86400000 - 3 + ((week1.getDay() + 6) % 7)) / 7)
    return `${dt.getFullYear()}-W${String(wn).padStart(2, '0')}`
}

function lastNWeekKeys(n) {
    const keys = []
    const today = new Date()
    for (let i = n - 1; i >= 0; i--) {
        const d = new Date(today)
        d.setDate(d.getDate() - i * 7)
        keys.push(isoWeekKey(d))
    }
    // 去重保序（边界周可能重复）
    return [...new Set(keys)]
}

const weeklyTrend = computed(() => {
    const weeks = lastNWeekKeys(trendWeeks)
    const closed = trendTrades.value.filter(t => t.exit_at && t.pnl_pct != null)
    // 按周分桶
    const bucketBy = (filterFn) => {
        const map = {}
        for (const k of weeks) map[k] = { wins: 0, total: 0 }
        for (const t of closed) {
            if (!filterFn(t)) continue
            const k = isoWeekKey(new Date(t.exit_at))
            if (!map[k]) continue
            map[k].total++
            if (t.pnl_pct > 0) map[k].wins++
        }
        return weeks.map(k => map[k].total ? map[k].wins / map[k].total : null)
    }
    return {
        weeks,
        overall:        bucketBy(() => true),
        star3plus:      bucketBy(t => (t.star_level ?? 0) >= 3),
        star4:          bucketBy(t => (t.star_level ?? 0) >= 4),
        main_breakout:  bucketBy(t => t.signal_source === 'main_breakout'),
        // 周样本量（同 overall total）用于 hover
        counts:         (() => {
            const cm = {}
            for (const k of weeks) cm[k] = 0
            for (const t of closed) {
                const k = isoWeekKey(new Date(t.exit_at))
                if (cm[k] != null) cm[k]++
            }
            return weeks.map(k => cm[k])
        })(),
    }
})

const hasTrendData = computed(() => weeklyTrend.value.counts.some(c => c > 0))

// ============ ECharts 渲染 ============
const trendChartRef = ref(null)
let _trendChart = null

function renderTrendChart() {
    if (!trendChartRef.value) return
    if (!_trendChart) {
        _trendChart = echarts.init(trendChartRef.value)
    }
    const wt = weeklyTrend.value
    const fmtSeries = (arr) => arr.map(v => v == null ? null : +(v * 100).toFixed(1))
    _trendChart.setOption({
        animation: false,
        grid:    { left: 38, right: 14, top: 28, bottom: 36 },
        tooltip: {
            trigger: 'axis',
            backgroundColor: '#fff',
            borderColor: '#e5e7eb',
            textStyle: { color: '#1f2937', fontSize: 11 },
            formatter: (params) => {
                if (!params?.length) return ''
                const idx = params[0].dataIndex
                const cnt = wt.counts[idx]
                let s = `<div style="font-weight:bold">${wt.weeks[idx]}</div>`
                s += `<div style="color:#64748b;font-size:10px">样本: ${cnt}</div>`
                for (const p of params) {
                    const v = p.value
                    s += `<div>${p.marker} ${p.seriesName}: <b>${v == null ? '—' : v + '%'}</b></div>`
                }
                return s
            },
        },
        legend: {
            top: 0, left: 0, itemGap: 10, itemWidth: 12, itemHeight: 8,
            textStyle: { fontSize: 10, color: '#475569' },
        },
        xAxis: {
            type: 'category', data: wt.weeks,
            axisLabel: { fontSize: 9, color: '#94a3b8', formatter: (v) => v.slice(5) },
            axisLine:  { lineStyle: { color: '#e5e7eb' } },
            axisTick:  { show: false },
        },
        yAxis: {
            type: 'value', min: 0, max: 100,
            axisLabel: { fontSize: 9, color: '#94a3b8', formatter: '{value}%' },
            splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' } },
        },
        series: [
            {
                name: '整体', type: 'line', smooth: true, connectNulls: true,
                data: fmtSeries(wt.overall),
                lineStyle: { color: '#94a3b8', width: 1.5 },
                itemStyle: { color: '#94a3b8' },
                symbol: 'circle', symbolSize: 5,
            },
            {
                name: '⭐⭐⭐+', type: 'line', smooth: true, connectNulls: true,
                data: fmtSeries(wt.star3plus),
                lineStyle: { color: '#dc2626', width: 2 },
                itemStyle: { color: '#dc2626' },
                symbol: 'circle', symbolSize: 6,
            },
            {
                name: '⭐⭐⭐⭐', type: 'line', smooth: true, connectNulls: true,
                data: fmtSeries(wt.star4),
                lineStyle: { color: '#7c2d12', width: 2 },
                itemStyle: { color: '#7c2d12' },
                symbol: 'rect', symbolSize: 6,
            },
            {
                name: '主升突破', type: 'line', smooth: true, connectNulls: true,
                data: fmtSeries(wt.main_breakout),
                lineStyle: { color: '#0369a1', width: 1.5, type: 'dashed' },
                itemStyle: { color: '#0369a1' },
                symbol: 'triangle', symbolSize: 6,
            },
            // 预期 76% 参考线（mark line on overall）
            {
                name: '预期 76%', type: 'line', data: wt.weeks.map(() => 76),
                lineStyle: { color: '#fbbf24', width: 1, type: 'dotted' },
                itemStyle: { color: '#fbbf24' },
                symbol: 'none', silent: true,
            },
        ],
    })
}

function onTrendResize() { _trendChart?.resize() }

watch(weeklyTrend, () => {
    if (hasTrendData.value) nextTick(renderTrendChart)
}, { deep: true })

// ============ 编辑 modal 状态 ============
const editModal = ref({ open: false, trade: null, entryPrice: '', targetPrice: '', stopLoss: '', positionPct: '', notes: '' })

function openEditModal(t) {
    editModal.value = {
        open: true,
        trade: t,
        entryPrice:  t.entry_price  != null ? String(t.entry_price)  : '',
        targetPrice: t.target_price != null ? String(t.target_price) : '',
        stopLoss:    t.stop_loss    != null ? String(t.stop_loss)    : '',
        positionPct: t.position_pct != null ? String(t.position_pct) : '',
        notes:       t.notes || '',
    }
}

async function submitEdit() {
    const m = editModal.value
    if (!m.trade?.id) return
    const ep = m.entryPrice.trim()  === '' ? null : parseFloat(m.entryPrice)
    const tp = m.targetPrice.trim() === '' ? null : parseFloat(m.targetPrice)
    const sl = m.stopLoss.trim()    === '' ? null : parseFloat(m.stopLoss)
    const pp = m.positionPct.trim() === '' ? null : parseFloat(m.positionPct)
    if (ep != null && !(ep > 0)) { pushError('入场价无效'); return }
    if (tp != null && !(tp > 0)) { pushError('止盈价无效'); return }
    if (sl != null && !(sl > 0)) { pushError('止损价无效'); return }
    if (pp != null && !(pp >= 0)) { pushError('仓位 % 无效'); return }
    try {
        const res = await api.updateTradeJournal({
            trade_id:     m.trade.id,
            entry_price:  ep,
            target_price: tp,
            stop_loss:    sl,
            position_pct: pp,
            notes:        m.notes,
        })
        if (res?.ok) {
            pushSuccess('已更新')
            editModal.value.open = false
            await loadTrades()
        } else {
            pushError(`更新失败: ${res?.error || '未知'}`)
        }
    } catch (e) {
        pushError(`更新异常: ${e}`)
    }
}

// ============ 平仓 modal 状态 ============
const closeModal = ref({ open: false, trade: null, exitPrice: '', exitReason: 'manual', notes: '' })

function openCloseModal(t) {
    closeModal.value = {
        open: true,
        trade: t,
        exitPrice: '',
        exitReason: 'manual',
        notes: '',
    }
}

async function submitClose() {
    const m = closeModal.value
    const ep = parseFloat(m.exitPrice)
    if (!(ep > 0)) { pushError('请输入卖出价'); return }
    try {
        const res = await api.closeTradeJournal({
            trade_id:    m.trade.id,
            exit_price:  ep,
            exit_reason: m.exitReason,
            notes:       m.notes,
        })
        if (res?.ok) {
            pushSuccess(`已平仓 ${m.trade.name}`)
            closeModal.value.open = false
            await loadTrades()
        } else {
            pushError('平仓失败')
        }
    } catch (e) {
        pushError(`平仓异常: ${e}`)
    }
}

async function deleteTrade(t) {
    const ok = await confirmDialog({
        title: '删除日志',
        message: `删除 ${t.name} (${t.code}) 的交易记录？此操作不可撤销。`,
        confirmText: '删除',
        danger: true,
    })
    if (!ok) return
    try {
        const res = await api.deleteTradeJournal(t.id)
        if (res?.ok) {
            pushSuccess('已删除')
            await loadTrades()
        }
    } catch (e) { pushError(`删除失败: ${e}`) }
}

// ============ 统计卡片 ============
const overallSummary = computed(() => summary.value?.overall || null)
const expectedWinRate = 0.76   // ⭐⭐⭐ 预期胜率

const winRateGap = computed(() => {
    if (!overallSummary.value) return null
    return overallSummary.value.win_rate - expectedWinRate
})

function fmtPct(v) {
    if (v == null) return '—'
    const sign = v > 0 ? '+' : ''
    return `${sign}${(v).toFixed(2)}%`
}
function fmtPctRate(v) {
    return v == null ? '—' : `${(v * 100).toFixed(1)}%`
}
function fmtDate(s) { return s ? s.slice(0, 10) : '—' }
function fmtPrice(v) { return v != null ? v.toFixed(2) : '—' }

function star(n) {
    if (!n) return ''
    return '⭐'.repeat(Math.min(n, 4))
}

// 按 source / star 拆分（已含预期对比）
const byStarRows = computed(() => {
    if (!summary.value?.by_star) return []
    const expected = summary.value.expected_win_rate || {}
    return [4, 3, 2, 1, 0].map(lv => {
        const k = `star_${lv}`
        const s = summary.value.by_star[k]
        if (!s) return null
        const exp = expected[k] ?? null
        return {
            level: lv,
            label: lv > 0 ? '⭐'.repeat(lv) : '无星',
            count: s.count,
            win_rate: s.win_rate,
            avg_pnl: s.avg_pnl,
            median_pnl: s.median_pnl,
            avg_hold: s.avg_hold,
            expected: exp,
            gap: exp != null ? s.win_rate - exp : null,
        }
    }).filter(Boolean)
})

const bySourceRows = computed(() => {
    if (!summary.value?.by_source) return []
    const labels = {
        main_breakout: '主升突破',
        breakout_eve:  '突破前夜',
        dragon_return: '龙回头',
        limit_up_relay:'连板游资',
        manual:        '手动',
    }
    return Object.entries(summary.value.by_source).map(([key, s]) => ({
        key,
        label: labels[key] || key,
        count: s.count,
        win_rate: s.win_rate,
        avg_pnl: s.avg_pnl,
        median_pnl: s.median_pnl,
        avg_hold: s.avg_hold,
    })).sort((a, b) => b.count - a.count)
})

function openChartForRow(t) {
    openStockChart(t.code, t.name, trades.value.map(x => ({ code: x.code, name: x.name })))
}

onMounted(() => {
    loadTrades()
    window.addEventListener('resize', onTrendResize)
})
onBeforeUnmount(() => {
    window.removeEventListener('resize', onTrendResize)
    _trendChart?.dispose()
    _trendChart = null
})
</script>

<template>
    <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">
        <!-- ========== 顶部 hub bar（44px，与 选股/候选/回测 完全对齐，避免切 tab 时跳动）========== -->
        <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center shrink-0 px-[12px] gap-[10px]">
            <h2 class="text-[15px] font-bold text-[#111]">交易日志</h2>
            <span class="text-[11px] text-[#94a3b8]">最近 30 天</span>
            <button @click="loadTrades" class="ml-auto text-[11px] px-[8px] py-[3px] rounded border border-[#dc2626]/40 text-[#dc2626] hover:bg-[#fff5f5]">刷新</button>
            <!-- hub 控件注入位（QuantHub 提供 segmented + 今日按钮）—— 跟项目标准统一：左侧 border-l 分隔 -->
            <div class="shrink-0 flex items-center pl-[10px] pr-[14px] border-l border-[#e5e5e5] gap-[10px] h-full">
                <slot name="tabBarRight" />
            </div>
        </div>

        <!-- ========== 顶部 stats 卡片 ========== -->
        <div class="px-[16px] py-[12px] bg-white border-b border-[#e5e7eb] shrink-0">
            <div v-if="overallSummary" class="grid grid-cols-5 gap-[10px]">
                <div class="bg-[#fafafa] rounded p-[8px] border border-[#e5e7eb]">
                    <div class="text-[10px] text-[#94a3b8]">总笔数</div>
                    <div class="text-[18px] font-bold tabular-nums text-[#111]">{{ overallSummary.count }}</div>
                </div>
                <div class="bg-[#fafafa] rounded p-[8px] border border-[#e5e7eb]">
                    <div class="text-[10px] text-[#94a3b8]">实际胜率</div>
                    <div class="text-[18px] font-bold tabular-nums"
                         :class="overallSummary.win_rate >= 0.7 ? 'text-[#dc2626]' : overallSummary.win_rate >= 0.5 ? 'text-[#b45309]' : 'text-[#475569]'">
                        {{ fmtPctRate(overallSummary.win_rate) }}
                    </div>
                </div>
                <div class="bg-[#fafafa] rounded p-[8px] border border-[#e5e7eb]" :title="`⭐⭐⭐ 预期 76% · 你的实际 - 预期 = ${winRateGap != null ? (winRateGap*100).toFixed(1) + 'pp' : '—'}`">
                    <div class="text-[10px] text-[#94a3b8]">vs 预期 76%</div>
                    <div class="text-[18px] font-bold tabular-nums"
                         :class="winRateGap >= 0 ? 'text-[#dc2626]' : 'text-[#475569]'">
                        {{ winRateGap != null ? (winRateGap > 0 ? '+' : '') + (winRateGap * 100).toFixed(1) + 'pp' : '—' }}
                    </div>
                </div>
                <div class="bg-[#fafafa] rounded p-[8px] border border-[#e5e7eb]">
                    <div class="text-[10px] text-[#94a3b8]">平均盈亏</div>
                    <div class="text-[18px] font-bold tabular-nums"
                         :class="overallSummary.avg_pnl > 0 ? 'text-[#dc2626]' : 'text-[#475569]'">
                        {{ fmtPct(overallSummary.avg_pnl) }}
                    </div>
                </div>
                <div class="bg-[#fafafa] rounded p-[8px] border border-[#e5e7eb]">
                    <div class="text-[10px] text-[#94a3b8]">最大单笔</div>
                    <div class="text-[12px] tabular-nums">
                        <span class="text-[#dc2626] font-semibold">{{ fmtPct(overallSummary.max_win) }}</span>
                        <span class="text-[#94a3b8]"> / </span>
                        <span class="text-[#7f1d1d] font-semibold">{{ fmtPct(overallSummary.max_loss) }}</span>
                    </div>
                </div>
            </div>
            <div v-else class="text-[12px] text-[#94a3b8] py-[8px]">还没有平仓记录 — 平仓后这里显示真实胜率对比</div>

            <!-- 周维度胜率趋势（近 8 周）-->
            <div v-if="hasTrendData" class="mt-[12px] border border-[#e5e7eb] rounded p-[8px] bg-[#fafafa]">
                <div class="flex items-center justify-between mb-[4px]">
                    <div class="text-[11px] text-[#666] font-semibold">近 {{ trendWeeks }} 周胜率走势</div>
                    <div class="text-[10px] text-[#94a3b8]">虚线 = 预期 76% · 横轴 = ISO 周</div>
                </div>
                <div ref="trendChartRef" class="w-full h-[150px]"></div>
            </div>

            <!-- ⭐ 按星级 拆分（与预期对比）-->
            <div v-if="byStarRows.length" class="mt-[12px]">
                <div class="text-[11px] text-[#666] font-semibold mb-[5px]">按星级拆分（实际 vs 预期）</div>
                <table class="w-full text-[11px] border-collapse">
                    <thead class="bg-[#fafafa] text-[#94a3b8]">
                        <tr>
                            <th class="px-[8px] py-[5px] text-left font-normal w-[80px]">星级</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[60px]">笔数</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">实际胜率</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">预期</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">差值</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">平均盈亏</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">中位</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">持有天</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in byStarRows" :key="r.level" class="border-b border-[#f0f0f0]">
                            <td class="px-[8px] py-[5px] font-bold">{{ r.label }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums">{{ r.count }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums font-bold"
                                :class="r.win_rate >= 0.7 ? 'text-[#dc2626]' : r.win_rate >= 0.5 ? 'text-[#b45309]' : 'text-[#475569]'">
                                {{ fmtPctRate(r.win_rate) }}
                            </td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums text-[#94a3b8]">
                                {{ r.expected != null ? fmtPctRate(r.expected) : '—' }}
                            </td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums font-semibold"
                                :class="r.gap == null ? 'text-[#cbd5e1]' : r.gap >= 0 ? 'text-[#dc2626]' : 'text-[#475569]'">
                                {{ r.gap != null ? (r.gap > 0 ? '+' : '') + (r.gap * 100).toFixed(1) + 'pp' : '—' }}
                            </td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums"
                                :class="r.avg_pnl > 0 ? 'text-[#dc2626]' : 'text-[#475569]'">{{ fmtPct(r.avg_pnl) }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums text-[#666]">{{ fmtPct(r.median_pnl) }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums text-[#666]">{{ r.avg_hold?.toFixed?.(1) ?? '—' }}d</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- 按 signal_source 拆分 -->
            <div v-if="bySourceRows.length" class="mt-[10px]">
                <div class="text-[11px] text-[#666] font-semibold mb-[5px]">按信号源拆分</div>
                <table class="w-full text-[11px] border-collapse">
                    <thead class="bg-[#fafafa] text-[#94a3b8]">
                        <tr>
                            <th class="px-[8px] py-[5px] text-left font-normal w-[80px]">来源</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[60px]">笔数</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">胜率</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">平均盈亏</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">中位</th>
                            <th class="px-[8px] py-[5px] text-right font-normal w-[80px]">持有天</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="r in bySourceRows" :key="r.key" class="border-b border-[#f0f0f0]">
                            <td class="px-[8px] py-[5px]">
                                <span class="inline-flex items-center px-[5px] py-[1px] rounded border text-[10px] font-semibold"
                                      :class="SOURCE_DISPLAY[r.key]?.cls || 'bg-[#f3f4f6]'">{{ r.label }}</span>
                            </td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums">{{ r.count }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums font-bold"
                                :class="r.win_rate >= 0.65 ? 'text-[#dc2626]' : r.win_rate >= 0.5 ? 'text-[#b45309]' : 'text-[#475569]'">
                                {{ fmtPctRate(r.win_rate) }}
                            </td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums"
                                :class="r.avg_pnl > 0 ? 'text-[#dc2626]' : 'text-[#475569]'">{{ fmtPct(r.avg_pnl) }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums text-[#666]">{{ fmtPct(r.median_pnl) }}</td>
                            <td class="px-[8px] py-[5px] text-right tabular-nums text-[#666]">{{ r.avg_hold?.toFixed?.(1) ?? '—' }}d</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- ========== 过滤 + 列表 ========== -->
        <div class="px-[16px] py-[8px] bg-[#fafafa] border-b border-[#e5e7eb] flex items-center gap-[8px] text-[11px] shrink-0">
            <span class="text-[#666]">状态：</span>
            <button v-for="s in ['all','open','closed']" :key="s"
                    @click="filterStatus = s; loadTrades()"
                    :class="['px-[8px] py-[3px] rounded transition',
                            filterStatus === s ? 'bg-[#1e293b] text-white font-semibold' : 'bg-white border border-[#e5e7eb] text-[#666] hover:bg-[#f1f5f9]']">
                {{ s === 'all' ? '全部' : s === 'open' ? '持仓中' : '已平仓' }}
            </button>
            <span class="mx-[6px] text-[#cbd5e1]">|</span>
            <span class="text-[#666]">来源：</span>
            <button @click="filterSource = null; loadTrades()"
                    :class="['px-[8px] py-[3px] rounded transition',
                            !filterSource ? 'bg-[#1e293b] text-white font-semibold' : 'bg-white border border-[#e5e7eb] text-[#666]']">全部</button>
            <button v-for="(disp, src) in SOURCE_DISPLAY" :key="src"
                    @click="filterSource = src; loadTrades()"
                    :class="['px-[8px] py-[3px] rounded border transition',
                            filterSource === src ? 'border-[#1e293b] font-semibold' : disp.cls]">
                {{ disp.label }}
            </button>
        </div>

        <!-- ========== Trades 表格 ========== -->
        <div class="flex-1 overflow-auto custom-scrollbar">
            <div v-if="!trades.length && !loading" class="py-[100px] text-center text-[#aaa] text-[13px]">
                还没记录 —— 从「选股」或「候选池」一键加入交易日志，开始真实仓位追踪
            </div>
            <table v-else class="w-full text-left text-[12px] border-collapse whitespace-nowrap">
                <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eee] text-[11px] text-[#888] z-10">
                    <tr>
                        <th class="px-[12px] py-[8px] font-normal w-[180px]">股票</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[90px]">来源 / 星级</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[90px]">买入</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">入场价</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[60px]">仓位%</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">止损/止盈</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[90px]">卖出</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">出场价</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[80px]">收益</th>
                        <th class="px-[8px] py-[8px] font-normal text-right w-[60px]">持仓天</th>
                        <th class="px-[8px] py-[8px] font-normal text-center w-[180px]">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="t in trades" :key="t.id"
                        @dblclick="openChartForRow(t)"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer transition-colors"
                        title="双击看 K 线">
                        <td class="px-[12px] py-[6px]">
                            <div class="flex items-center gap-[4px]">
                                <span class="text-[13px] font-bold text-[#111] truncate">{{ t.name || '—' }}</span>
                                <span v-if="!t.exit_at" class="shrink-0 text-[9px] font-semibold px-[4px] rounded bg-[#fee2e2] text-[#991b1b]">持仓中</span>
                            </div>
                            <div class="text-[10px] text-[#999] font-mono">{{ t.code }}</div>
                        </td>
                        <td class="px-[8px] py-[6px] text-center">
                            <span :class="['inline-block px-[6px] py-[1px] rounded text-[10px] border', SOURCE_DISPLAY[t.signal_source]?.cls || 'bg-[#f3f4f6]']">
                                {{ SOURCE_DISPLAY[t.signal_source]?.label || t.signal_source }}
                            </span>
                            <div v-if="t.star_level" class="text-[10px] mt-[2px]">{{ star(t.star_level) }}</div>
                        </td>
                        <td class="px-[8px] py-[6px] text-center text-[11px] text-[#666] tabular-nums">{{ fmtDate(t.entry_at) }}</td>
                        <td class="px-[8px] py-[6px] text-right tabular-nums">{{ fmtPrice(t.entry_price) }}</td>
                        <td class="px-[8px] py-[6px] text-right tabular-nums text-[#666]">{{ t.position_pct != null ? t.position_pct.toFixed(0) + '%' : '—' }}</td>
                        <td class="px-[8px] py-[6px] text-right tabular-nums text-[10px] leading-tight">
                            <div class="text-[#7f1d1d]" v-if="t.stop_loss">⬇{{ fmtPrice(t.stop_loss) }}</div>
                            <div class="text-[#15803d]" v-if="t.target_price">⬆{{ fmtPrice(t.target_price) }}</div>
                        </td>
                        <td class="px-[8px] py-[6px] text-center text-[11px] text-[#666] tabular-nums">{{ fmtDate(t.exit_at) || '—' }}</td>
                        <td class="px-[8px] py-[6px] text-right tabular-nums">
                            <template v-if="t.exit_price != null">{{ fmtPrice(t.exit_price) }}</template>
                            <template v-else-if="t.currentPrice != null">
                                <span class="text-[#475569]">{{ fmtPrice(t.currentPrice) }}</span>
                                <div class="text-[9px] text-[#94a3b8]">实时</div>
                            </template>
                            <template v-else>—</template>
                        </td>
                        <td class="px-[8px] py-[6px] text-right tabular-nums font-bold"
                            :class="(t.pnl_pct ?? t.pnlLive) > 0 ? 'text-[#dc2626]' : (t.pnl_pct ?? t.pnlLive) < 0 ? 'text-[#7f1d1d]' : 'text-[#666]'">
                            <template v-if="t.pnl_pct != null">
                                <span>{{ t.pnl_pct > 0 ? '▲' : t.pnl_pct < 0 ? '▼' : '◆' }} {{ fmtPct(t.pnl_pct) }}</span>
                                <div v-if="t.exit_reason" :class="['text-[9px]', EXIT_REASON_DISPLAY[t.exit_reason]?.cls]">
                                    {{ EXIT_REASON_DISPLAY[t.exit_reason]?.label || t.exit_reason }}
                                </div>
                            </template>
                            <template v-else-if="t.pnlLive != null">
                                <span>{{ t.pnlLive > 0 ? '▲' : t.pnlLive < 0 ? '▼' : '◆' }} {{ fmtPct(t.pnlLive) }}</span>
                                <div class="text-[9px] text-[#94a3b8] font-normal">浮动</div>
                            </template>
                            <span v-else>—</span>
                        </td>
                        <td class="px-[8px] py-[6px] text-right tabular-nums text-[#666]">{{ t.hold_days != null ? t.hold_days + 'd' : '—' }}</td>
                        <td class="px-[8px] py-[6px] text-center" @click.stop>
                            <button v-if="!t.exit_at"
                                    @click.stop="openCloseModal(t)"
                                    class="text-[11px] px-[8px] py-[3px] rounded border border-[#0369a1]/40 text-[#0369a1] bg-white hover:bg-[#eff6ff]"
                                    title="平仓">
                                平仓
                            </button>
                            <button @click.stop="openEditModal(t)"
                                    class="text-[10px] px-[6px] py-[3px] rounded border border-[#b45309]/40 text-[#b45309] bg-white hover:bg-[#fffbeb] ml-[4px]"
                                    title="编辑入场价/止盈/止损/仓位/备注">
                                编辑
                            </button>
                            <button @click.stop="deleteTrade(t)"
                                    class="text-[10px] px-[6px] py-[3px] rounded border border-[#94a3b8] text-[#94a3b8] hover:bg-[#f1f5f9] ml-[4px]"
                                    title="删除">
                                删
                            </button>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>

        <!-- ========== 编辑 Modal ========== -->
        <div v-if="editModal.open"
             class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
             @click.self="editModal.open = false">
            <div class="bg-white rounded-lg p-[20px] w-[420px] shadow-2xl">
                <h3 class="text-[14px] font-bold mb-[4px]">编辑日志 {{ editModal.trade?.name }} ({{ editModal.trade?.code }})</h3>
                <div class="text-[11px] text-[#94a3b8] mb-[12px]">
                    <template v-if="editModal.trade?.exit_at">
                        ⚠ 已平仓 · 改入场价会自动重算收益率
                    </template>
                    <template v-else>持仓中 · 改入场价不影响星级/信号源</template>
                </div>
                <div class="grid grid-cols-2 gap-[10px] mb-[8px]">
                    <div>
                        <label class="text-[11px] text-[#666]">入场价</label>
                        <input v-model="editModal.entryPrice" type="number" step="0.01" placeholder="如 12.34"
                               class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626]">
                    </div>
                    <div>
                        <label class="text-[11px] text-[#666]">仓位 % (占总资金)</label>
                        <input v-model="editModal.positionPct" type="number" step="0.5" placeholder="如 15"
                               class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#0369a1]">
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-[10px] mb-[8px]">
                    <div>
                        <label class="text-[11px] text-[#666]">目标止盈</label>
                        <input v-model="editModal.targetPrice" type="number" step="0.01" placeholder="留空 = 清除"
                               class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#15803d]">
                    </div>
                    <div>
                        <label class="text-[11px] text-[#666]">止损</label>
                        <input v-model="editModal.stopLoss" type="number" step="0.01" placeholder="留空 = 清除"
                               class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[13px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#7f1d1d]">
                    </div>
                </div>
                <div class="mb-[12px]">
                    <label class="text-[11px] text-[#666]">备注</label>
                    <textarea v-model="editModal.notes" rows="2"
                              class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[12px] text-[#111] bg-white placeholder-[#cbd5e1] focus:outline-none focus:border-[#dc2626] resize-none"
                              placeholder="盘后复盘 / 调整原因等"></textarea>
                </div>
                <div class="flex justify-end gap-[8px]">
                    <button @click="editModal.open = false" class="px-[12px] py-[5px] text-[12px] border rounded hover:bg-[#f3f4f6]">取消</button>
                    <button @click="submitEdit" class="px-[12px] py-[5px] text-[12px] bg-[#b45309] text-white rounded hover:bg-[#92400e]">保存</button>
                </div>
            </div>
        </div>

        <!-- ========== 平仓 Modal ========== -->
        <div v-if="closeModal.open"
             class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
             @click.self="closeModal.open = false">
            <div class="bg-white rounded-lg p-[20px] w-[400px] shadow-2xl">
                <h3 class="text-[14px] font-bold mb-[12px]">平仓 {{ closeModal.trade?.name }} ({{ closeModal.trade?.code }})</h3>
                <div class="mb-[8px]">
                    <label class="text-[11px] text-[#666]">卖出价</label>
                    <input v-model="closeModal.exitPrice" type="number" step="0.01" placeholder="如 12.34"
                           class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[13px]">
                </div>
                <div class="mb-[8px]">
                    <label class="text-[11px] text-[#666]">原因</label>
                    <select v-model="closeModal.exitReason" class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[13px]">
                        <option value="take_profit">止盈</option>
                        <option value="stop_loss">止损</option>
                        <option value="time_out">持仓到期</option>
                        <option value="invalid">形态作废</option>
                        <option value="manual">手动 / 其他</option>
                    </select>
                </div>
                <div class="mb-[12px]">
                    <label class="text-[11px] text-[#666]">备注（可选）</label>
                    <input v-model="closeModal.notes" type="text" class="w-full mt-[2px] px-[8px] py-[5px] border rounded text-[12px]">
                </div>
                <div class="flex justify-end gap-[8px]">
                    <button @click="closeModal.open = false" class="px-[12px] py-[5px] text-[12px] border rounded hover:bg-[#f3f4f6]">取消</button>
                    <button @click="submitClose" class="px-[12px] py-[5px] text-[12px] bg-[#dc2626] text-white rounded hover:bg-[#b91c1c]">确认平仓</button>
                </div>
            </div>
        </div>
    </div>
</template>
