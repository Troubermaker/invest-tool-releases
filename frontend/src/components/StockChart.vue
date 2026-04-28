<script setup>
/**
 * 个股 K 线 + 技术指标图。
 *
 * 三大区：
 *   - Header：股票名 + 代码 + 现价 + 关闭按钮
 *   - Toolbar：时间周期切换（分时/5日/日K/周K/月K/年K）+ 指标 chip 多选
 *   - Charts：主图（蜡烛 + MA/BOLL overlay + 量柱）+ 可选副图 MACD / KDJ
 *
 * 多 chart 实例之间用 timeScale 监听互相同步缩放/平移，体感跟同花顺一致。
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api/client'
import { computeAll, pickCloses } from '../composables/useTechIndicators'

const props = defineProps({
    code:   { type: String, required: true },
    name:   { type: String, default: '' },
})
const emit = defineEmits(['close'])

// ============ State ============
const TIMEFRAMES = ['分时', '5日', '日K', '周K', '月K', '年K']
const timeframe = ref('日K')

// 默认视窗 bar 数（打开 / 切周期时只显示最近这么多根，更多需手动缩放）
const DEFAULT_VISIBLE_BARS = {
    '日K': 100,   // ~5 个月
    '周K': 52,    // ~1 年
    '月K': 36,    // ~3 年
    '年K': 20,
}

// 指标多选（默认开 MA 三档 + MACD）
const allIndicators = [
    { id: 'MA5',  label: 'MA5'  },
    { id: 'MA10', label: 'MA10' },
    { id: 'MA20', label: 'MA20' },
    { id: 'MA60', label: 'MA60' },
    { id: 'BOLL', label: 'BOLL' },
    { id: 'MACD', label: 'MACD' },
    { id: 'KDJ',  label: 'KDJ'  },
]
const activeIndicators = ref(['MA5', 'MA10', 'MA20', 'MACD'])

const klines = ref([])
const loading = ref(false)
const errMsg = ref('')

// ============ DOM refs + chart instances ============
const mainEl = ref(null)
const macdEl = ref(null)
const kdjEl  = ref(null)
let mainChart = null, macdChart = null, kdjChart = null
let mainSeries = null
let volumeSeries = null
const overlaySeriesMap = new Map()  // name → seriesObj
let macdSeries = null  // {dif, dea, hist}
let kdjSeries  = null  // {k, d, j}

// ============ Hover tooltip ============
const tooltip = ref({ visible: false, idx: -1, side: 'left' })  // side: 卡片贴向哪一侧
let _lastComputed = null   // { mainOverlays, subPanes }
let _timeToIdx = new Map() // 时间 → klines 索引

// 当前 hover 数据（按索引取，避免在 crosshair 回调里反复算）
const tooltipData = computed(() => {
    if (!tooltip.value.visible) return null
    const idx = tooltip.value.idx
    if (idx < 0 || idx >= klines.value.length) return null
    const k = klines.value[idx]
    const prev = idx > 0 ? klines.value[idx - 1] : null

    let chg = 0, pct = 0
    if (k.chg != null) { chg = +k.chg; pct = +k.pct || 0 }
    else if (prev) {
        const prevClose = +(prev.close ?? prev.value ?? 0)
        const cur = +(k.close ?? k.value ?? 0)
        chg = cur - prevClose
        pct = prevClose ? (chg / prevClose) * 100 : 0
    }

    // 计算分时均价（hover 实时累加到当前 idx）
    let avg = null
    if (isLineMode.value) {
        let cumPV = 0, cumV = 0
        for (let i = 0; i <= idx; i++) {
            const p = +(klines.value[i].value ?? klines.value[i].close)
            const v = +(klines.value[i].vol || 0)
            cumPV += p * v; cumV += v
        }
        avg = cumV > 0 ? cumPV / cumV : +(k.value ?? k.close)
    }

    const overlays = []
    const subs = []
    if (_lastComputed) {
        for (const ovl of _lastComputed.mainOverlays) {
            const v = ovl.values?.[idx]
            if (v != null) overlays.push({ name: ovl.name, color: ovl.color, value: v })
        }
        for (const pane of _lastComputed.subPanes) {
            for (const line of pane.lines) {
                const v = line.values?.[idx]
                if (v != null) subs.push({ pane: pane.name, name: line.name, color: line.color, value: v })
            }
            if (pane.histogram) {
                const v = pane.histogram.values?.[idx]
                if (v != null) subs.push({ pane: pane.name, name: 'HIST', color: '#94a3b8', value: v })
            }
        }
    }
    return { k, chg, pct, avg, overlays, subs }
})

function formatHoverTime(time) {
    if (typeof time === 'string') return time
    const d = new Date(time * 1000)
    const pad = n => String(n).padStart(2, '0')
    if (timeframe.value === '分时') return `${pad(d.getHours())}:${pad(d.getMinutes())}`
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 单位格式化：跟 Market.vue 指数 chart 对齐
function formatAmtText(amt) {
    if (amt == null || amt === 0) return '-'
    if (amt >= 10000) return (amt / 10000).toFixed(2) + '万亿'
    return amt.toFixed(2) + '亿'
}
function formatVol(vol) {
    if (vol == null) return '-'
    if (vol >= 10000) return (vol / 10000).toFixed(2) + '亿手'
    return vol.toFixed(2) + '万手'
}

let _resizeObs = null
let _syncing = false  // 防止递归同步

// ============ 数据加载 ============
async function loadData() {
    loading.value = true
    errMsg.value = ''
    try {
        const res = await api.getStockKline(props.code, timeframe.value)
        if (!res.ok) {
            errMsg.value = res.error || '数据获取失败'
            klines.value = []
        } else {
            klines.value = Array.isArray(res.data) ? res.data : []
            if (!klines.value.length) errMsg.value = '该周期无数据'
        }
    } finally {
        loading.value = false
        await nextTick()
        renderAll()
    }
}

// ============ 切换状态 ============
const isLineMode = computed(() => timeframe.value === '分时' || timeframe.value === '5日')
const showMacd = computed(() => activeIndicators.value.includes('MACD'))
const showKdj  = computed(() => activeIndicators.value.includes('KDJ'))

// 主图高度：根据副图数量动态算（每个副图 110px）
const mainChartHeight = computed(() => {
    const subN = (showMacd.value ? 1 : 0) + (showKdj.value ? 1 : 0)
    if (subN === 0) return '100%'
    if (subN === 1) return 'calc(100% - 110px)'
    return 'calc(100% - 220px)'
})

function toggleIndicator(id) {
    const idx = activeIndicators.value.indexOf(id)
    if (idx >= 0) activeIndicators.value.splice(idx, 1)
    else          activeIndicators.value.push(id)
}

// ============ 当前价格信息（header）============
const summary = computed(() => {
    if (!klines.value.length) return null
    const last = klines.value[klines.value.length - 1]
    const prev = klines.value.length > 1 ? klines.value[klines.value.length - 2] : null
    const price = +(last.close ?? last.value ?? 0)
    let change = 0, pct = 0
    if (last.chg != null) {
        change = +last.chg
        pct = +last.pct || 0
    } else if (prev) {
        const prevClose = +(prev.close ?? prev.value ?? 0)
        change = price - prevClose
        pct = prevClose ? (change / prevClose) * 100 : 0
    }
    return { price, change, pct }
})

// ============ 渲染主图 ============
async function ensureChart() {
    if (mainChart || !mainEl.value) return
    const { createChart } = await import('lightweight-charts')

    const baseOpts = {
        layout: {
            background: { color: '#fff' },
            textColor: '#475569',
            fontSize: 11,
        },
        grid: {
            vertLines: { color: '#f1f5f9' },
            horzLines: { color: '#f1f5f9' },
        },
        timeScale: {
            borderColor: '#e2e8f0',
            timeVisible: true,
            secondsVisible: false,
            fixRightEdge: true,   // 锁死右边沿：禁止拖到最新 bar 之后留白（金融软件惯例）
            rightOffset: 4,       // 给最新 bar 右侧留 4 根 bar 宽度的呼吸空间
        },
        rightPriceScale: { borderColor: '#e2e8f0' },
        crosshair: { mode: 1 /* magnet */ },
    }

    mainChart = createChart(mainEl.value, baseOpts)

    // ResizeObserver 让所有 chart 跟随容器尺寸变化
    _resizeObs = new ResizeObserver(() => {
        if (mainChart && mainEl.value) {
            mainChart.applyOptions({ width: mainEl.value.clientWidth, height: mainEl.value.clientHeight })
        }
        if (macdChart && macdEl.value) {
            macdChart.applyOptions({ width: macdEl.value.clientWidth, height: macdEl.value.clientHeight })
        }
        if (kdjChart && kdjEl.value) {
            kdjChart.applyOptions({ width: kdjEl.value.clientWidth, height: kdjEl.value.clientHeight })
        }
    })
    _resizeObs.observe(mainEl.value)

    // 主图 timeScale 变化时同步副图
    mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (_syncing || !range) return
        _syncing = true
        if (macdChart) macdChart.timeScale().setVisibleLogicalRange(range)
        if (kdjChart)  kdjChart.timeScale().setVisibleLogicalRange(range)
        _syncing = false
    })

    // 十字线 hover → 更新 tooltip（cursor 在左半屏则卡片贴右，反之贴左，避免遮挡当前 bar）
    mainChart.subscribeCrosshairMove((param) => {
        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
            if (tooltip.value.visible) tooltip.value = { visible: false, idx: -1, side: 'left' }
            return
        }
        const idx = _timeToIdx.get(param.time)
        if (idx == null) {
            if (tooltip.value.visible) tooltip.value = { visible: false, idx: -1, side: 'left' }
            return
        }
        const w = mainEl.value?.clientWidth || 0
        const side = w > 0 && param.point.x < w / 2 ? 'right' : 'left'
        if (tooltip.value.idx !== idx || !tooltip.value.visible || tooltip.value.side !== side) {
            tooltip.value = { visible: true, idx, side }
        }
    })
}

async function ensureSubChart(slot /* 'macd' | 'kdj' */) {
    const el = slot === 'macd' ? macdEl.value : kdjEl.value
    if (!el) return null
    let chart = slot === 'macd' ? macdChart : kdjChart
    if (chart) return chart

    const { createChart } = await import('lightweight-charts')
    const opts = {
        layout: { background: { color: '#fff' }, textColor: '#475569', fontSize: 10 },
        grid: { vertLines: { color: '#f5f5f5' }, horzLines: { color: '#f5f5f5' } },
        timeScale: {
            borderColor: '#e2e8f0',
            timeVisible: true,
            secondsVisible: false,
            visible: false,         // 副图隐藏 x 轴，靠主图驱动
            fixRightEdge: true,
            rightOffset: 4,
        },
        rightPriceScale: { borderColor: '#e2e8f0' },
        crosshair: { mode: 1 },
        height: el.clientHeight,
        width: el.clientWidth,
    }
    chart = createChart(el, opts)
    if (_resizeObs) _resizeObs.observe(el)

    // 副图也允许独立缩放，但同步回主图
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (_syncing || !range || !mainChart) return
        _syncing = true
        mainChart.timeScale().setVisibleLogicalRange(range)
        _syncing = false
    })

    if (slot === 'macd') macdChart = chart
    else                 kdjChart = chart
    return chart
}

function disposeSubChart(slot) {
    if (slot === 'macd' && macdChart) {
        macdChart.remove(); macdChart = null; macdSeries = null
    }
    if (slot === 'kdj' && kdjChart) {
        kdjChart.remove(); kdjChart = null; kdjSeries = null
    }
}

// ============ 把数据/指标真正画到图上 ============
async function renderAll() {
    await ensureChart()
    if (!mainChart) return

    // 清掉旧 series，避免叠加
    if (mainSeries)   { mainChart.removeSeries(mainSeries);   mainSeries = null }
    if (volumeSeries) { mainChart.removeSeries(volumeSeries); volumeSeries = null }
    for (const s of overlaySeriesMap.values()) {
        try { mainChart.removeSeries(s) } catch {}
    }
    overlaySeriesMap.clear()

    if (!klines.value.length) return

    // —— 主序列：分时/5日 走线，否则蜡烛 —— //
    if (isLineMode.value) {
        // 现价线（红）
        mainSeries = mainChart.addLineSeries({
            color: '#dc2626', lineWidth: 2,
            priceScaleId: 'right',
            title: '现价',
        })
        mainSeries.setData(klines.value.map(k => ({
            time: k.time,
            value: +(k.value ?? k.close),
        })))

        // 均价线（黄）：累计 Σ(价×量)/Σ(量)，5 日模式跨天连续
        const avgSeries = mainChart.addLineSeries({
            color: '#f59e0b', lineWidth: 1,
            priceScaleId: 'right',
            priceLineVisible: false, lastValueVisible: true,
            crosshairMarkerVisible: false,
            title: '均价',
        })
        let cumPV = 0, cumV = 0
        const avgData = []
        for (const k of klines.value) {
            const p = +(k.value ?? k.close)
            const v = +(k.vol || 0)
            cumPV += p * v
            cumV  += v
            avgData.push({ time: k.time, value: cumV > 0 ? cumPV / cumV : p })
        }
        avgSeries.setData(avgData)
        overlaySeriesMap.set('avg', avgSeries)
    } else {
        mainSeries = mainChart.addCandlestickSeries({
            upColor: '#dc2626', downColor: '#16a34a',
            borderUpColor: '#dc2626', borderDownColor: '#16a34a',
            wickUpColor: '#dc2626', wickDownColor: '#16a34a',
        })
        mainSeries.setData(klines.value.map(k => ({
            time: k.time,
            open: +k.open, high: +k.high, low: +k.low, close: +k.close,
        })))
    }

    // —— 量柱（不在分时模式下挂）—— //
    if (!isLineMode.value) {
        volumeSeries = mainChart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: 'volume',
            color: '#94a3b8',
        })
        mainChart.priceScale('volume').applyOptions({
            scaleMargins: { top: 0.78, bottom: 0 },
        })
        volumeSeries.setData(klines.value.map(k => ({
            time: k.time,
            value: +(k.vol || 0),
            color: (+k.close >= +k.open) ? 'rgba(220,38,38,0.45)' : 'rgba(22,163,74,0.45)',
        })))
    }

    // 重建 time → idx 索引（hover 用）
    _timeToIdx = new Map()
    klines.value.forEach((k, i) => _timeToIdx.set(k.time, i))

    // —— 计算并叠加指标 —— //
    const isCandleMode = !isLineMode.value
    const indicators = isCandleMode ? activeIndicators.value : []  // 分时/5日 不画 MA/BOLL/MACD/KDJ
    const { mainOverlays, subPanes } = computeAll(klines.value, indicators)
    _lastComputed = { mainOverlays, subPanes }

    // 主图 overlay（MA + BOLL）
    for (const ovl of mainOverlays) {
        const series = mainChart.addLineSeries({
            color: ovl.color, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false,
            crosshairMarkerVisible: false,
        })
        series.setData(klines.value.map((k, i) => ({
            time: k.time,
            value: ovl.values[i] == null ? undefined : ovl.values[i],
        })).filter(p => p.value !== undefined))
        overlaySeriesMap.set(ovl.name, series)
    }

    // —— 副图：MACD / KDJ —— //
    await renderSubPane('macd', subPanes.find(p => p.name === 'MACD'))
    await renderSubPane('kdj',  subPanes.find(p => p.name === 'KDJ'))

    // —— 默认视窗：蜡烛模式只显示最近 N 根，剩下的用户自己向左拖/缩放查看 —— //
    if (!isLineMode.value) {
        const defaultBars = DEFAULT_VISIBLE_BARS[timeframe.value]
        const total = klines.value.length
        if (defaultBars && total > defaultBars) {
            // 设置主图视窗 → 通过 subscribeVisibleLogicalRangeChange 自动同步到副图
            mainChart.timeScale().setVisibleLogicalRange({
                from: total - defaultBars,
                to:   total - 0.5,  // 右侧留半根 bar 余量
            })
        } else {
            mainChart.timeScale().fitContent()
        }
    }
}

async function renderSubPane(slot, paneData) {
    if (!paneData) {
        disposeSubChart(slot)
        return
    }
    const chart = await ensureSubChart(slot)
    if (!chart) return

    // 清旧
    const refMap = slot === 'macd' ? macdSeries : kdjSeries
    if (refMap) {
        for (const s of Object.values(refMap)) {
            try { chart.removeSeries(s) } catch {}
        }
    }
    const newRefs = {}

    // 副图 hist（MACD 才有）
    if (paneData.histogram) {
        const histSeries = chart.addHistogramSeries({
            priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
            color: '#94a3b8',
        })
        histSeries.setData(klines.value.map((k, i) => ({
            time: k.time,
            value: paneData.histogram.values[i] ?? 0,
            color: (paneData.histogram.values[i] ?? 0) >= 0
                ? 'rgba(220,38,38,0.55)' : 'rgba(22,163,74,0.55)',
        })))
        newRefs.hist = histSeries
    }

    // 副图线条（DIF/DEA 或 KDJ 三条）
    for (const line of paneData.lines) {
        const series = chart.addLineSeries({
            color: line.color, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: true,
            crosshairMarkerVisible: false,
        })
        series.setData(klines.value.map((k, i) => ({
            time: k.time,
            value: line.values[i] == null ? undefined : line.values[i],
        })).filter(p => p.value !== undefined))
        newRefs[line.name.toLowerCase()] = series
    }

    if (slot === 'macd') macdSeries = newRefs
    else                 kdjSeries  = newRefs
}

// ============ Watchers ============
watch(() => [props.code, timeframe.value], loadData)
watch(activeIndicators, renderAll, { deep: true })

// ============ Lifecycle ============
onMounted(() => loadData())
onUnmounted(() => {
    if (_resizeObs) { _resizeObs.disconnect(); _resizeObs = null }
    if (mainChart) { mainChart.remove(); mainChart = null }
    if (macdChart) { macdChart.remove(); macdChart = null }
    if (kdjChart)  { kdjChart.remove();  kdjChart  = null }
})
</script>

<template>
    <div class="flex flex-col h-full bg-white">
        <!-- Header -->
        <div class="h-[44px] px-[14px] border-b border-[#e5e7eb] flex items-center gap-[12px] shrink-0">
            <div class="flex items-baseline gap-[8px]">
                <span class="text-[15px] font-bold text-[#111]">{{ name || code }}</span>
                <span class="text-[11px] text-[#94a3b8] font-mono tabular-nums">{{ code }}</span>
            </div>
            <div v-if="summary" class="flex items-baseline gap-[6px] tabular-nums">
                <span class="text-[16px] font-bold"
                      :class="summary.change >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    {{ summary.price.toFixed(2) }}
                </span>
                <span class="text-[12px] font-semibold"
                      :class="summary.change >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    {{ summary.change >= 0 ? '▲ +' : '▼ ' }}{{ summary.change.toFixed(2) }}
                    ({{ summary.pct >= 0 ? '+' : '' }}{{ summary.pct.toFixed(2) }}%)
                </span>
            </div>
            <div class="ml-auto flex items-center gap-[6px]">
                <button @click="loadData" :disabled="loading"
                        class="text-[11px] text-[#666] hover:text-[#dc2626] disabled:opacity-50 px-[8px] py-[3px] rounded-[3px] transition">
                    {{ loading ? '加载中...' : '↻ 刷新' }}
                </button>
                <button @click="$emit('close')"
                        class="text-[14px] text-[#888] hover:text-[#dc2626] hover:bg-[#fff5f5] w-[24px] h-[24px] rounded transition flex items-center justify-center"
                        title="关闭">
                    ✕
                </button>
            </div>
        </div>

        <!-- Toolbar -->
        <div class="h-[36px] px-[14px] border-b border-[#f0f0f0] flex items-center gap-[12px] shrink-0 bg-[#fafafa]">
            <!-- 时间周期 -->
            <div class="flex bg-white rounded-[4px] p-[2px] border border-[#e5e7eb]">
                <button v-for="tf in TIMEFRAMES" :key="tf"
                        @click="timeframe = tf"
                        class="text-[11px] px-[10px] py-[3px] rounded-[3px] font-semibold transition"
                        :class="timeframe === tf ? 'bg-[#dc2626] text-white' : 'text-[#666] hover:text-[#111]'">
                    {{ tf }}
                </button>
            </div>

            <!-- 指标 chips（仅蜡烛模式有意义）-->
            <div v-if="!isLineMode" class="flex items-center gap-[3px]">
                <span class="text-[10px] text-[#999] mr-[2px]">指标</span>
                <button v-for="ind in allIndicators" :key="ind.id"
                        @click="toggleIndicator(ind.id)"
                        class="text-[10px] px-[7px] py-[2px] rounded-[3px] border transition"
                        :class="activeIndicators.includes(ind.id)
                            ? 'bg-[#fff5f5] border-[#fecaca] text-[#dc2626] font-semibold'
                            : 'bg-white border-[#e5e7eb] text-[#888] hover:border-[#bbb]'">
                    {{ ind.label }}
                </button>
            </div>
            <div v-else class="flex items-center gap-[10px] text-[10px]">
                <span class="flex items-center gap-[4px] text-[#666]">
                    <span class="inline-block w-[10px] h-[2px] bg-[#dc2626]"></span>现价
                </span>
                <span class="flex items-center gap-[4px] text-[#666]">
                    <span class="inline-block w-[10px] h-[2px] bg-[#f59e0b]"></span>均价
                </span>
            </div>
        </div>

        <!-- Charts area -->
        <div class="flex-1 flex flex-col overflow-hidden relative">
            <div v-if="errMsg && !klines.length"
                 class="absolute inset-0 flex items-center justify-center text-[13px] text-[#999]">
                {{ errMsg }}
            </div>

            <!-- 主图 + 浮动 tooltip -->
            <div class="relative bg-white" :style="{ height: mainChartHeight }">
                <div ref="mainEl" class="w-full h-full"></div>

                <!-- Hover tooltip 卡片（贴左上 / 右上自动切换，永远在 cursor 反侧避免遮挡）-->
                <div v-if="tooltipData"
                     class="absolute top-[6px] z-[10] pointer-events-none
                            bg-white/95 backdrop-blur-[2px] border border-[#e5e7eb] rounded-[5px]
                            shadow-[0_2px_8px_rgba(0,0,0,0.08)]
                            px-[10px] py-[7px] text-[11px] tabular-nums font-mono leading-[1.5] min-w-[170px]"
                     :style="tooltip.side === 'right' ? { right: '10px' } : { left: '10px' }">
                    <!-- 时间标题 -->
                    <div class="font-bold text-[#111] mb-[4px] pb-[3px] border-b border-[#f1f5f9]">
                        {{ formatHoverTime(tooltipData.k.time) }}
                    </div>

                    <!-- 蜡烛模式：开/高/低/收 + 涨跌 + 振幅 + 量额 -->
                    <template v-if="!isLineMode">
                        <div class="flex flex-col gap-y-[2px] text-[#475569]">
                            <div class="flex justify-between gap-3"><span>开盘</span>
                                <span :class="tooltipData.k.close >= tooltipData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+tooltipData.k.open).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>最高</span>
                                <span :class="tooltipData.k.close >= tooltipData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+tooltipData.k.high).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>最低</span>
                                <span :class="tooltipData.k.close >= tooltipData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+tooltipData.k.low).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>收盘</span>
                                <span class="font-semibold" :class="tooltipData.k.close >= tooltipData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+tooltipData.k.close).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨跌</span>
                                <span :class="tooltipData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ tooltipData.chg >= 0 ? '+' : '' }}{{ tooltipData.chg.toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨幅</span>
                                <span :class="tooltipData.pct >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ tooltipData.pct >= 0 ? '+' : '' }}{{ tooltipData.pct.toFixed(2) }}%</span>
                            </div>
                            <div v-if="tooltipData.k.amp != null" class="flex justify-between gap-3"><span>振幅</span>
                                <span class="text-[#111]">{{ (+tooltipData.k.amp).toFixed(2) }}%</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交额</span>
                                <span class="text-[#475569]">{{ formatAmtText(tooltipData.k.amt) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交量</span>
                                <span class="text-[#475569]">{{ formatVol(tooltipData.k.vol) }}</span>
                            </div>
                        </div>

                        <!-- 主图叠加（MA / BOLL）-->
                        <div v-if="tooltipData.overlays.length"
                             class="mt-[4px] pt-[4px] border-t border-[#f1f5f9] flex flex-col gap-y-[2px]">
                            <div v-for="ovl in tooltipData.overlays" :key="ovl.name"
                                 class="flex justify-between gap-3">
                                <span :style="{ color: ovl.color }">{{ ovl.name }}</span>
                                <span :style="{ color: ovl.color }" class="font-semibold">{{ ovl.value.toFixed(2) }}</span>
                            </div>
                        </div>

                        <!-- 副图（MACD / KDJ）-->
                        <div v-if="tooltipData.subs.length"
                             class="mt-[4px] pt-[4px] border-t border-[#f1f5f9] flex flex-col gap-y-[2px]">
                            <div v-for="s in tooltipData.subs" :key="s.pane + s.name"
                                 class="flex justify-between gap-3">
                                <span :style="{ color: s.color }">{{ s.pane }}.{{ s.name }}</span>
                                <span :style="{ color: s.color }" class="font-semibold">{{ s.value.toFixed(s.pane === 'MACD' ? 3 : 2) }}</span>
                            </div>
                        </div>
                    </template>

                    <!-- 分时/5日 模式 -->
                    <template v-else>
                        <div class="flex flex-col gap-y-[2px] text-[#475569]">
                            <div class="flex justify-between gap-3"><span>现价</span>
                                <span class="font-semibold" :class="tooltipData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+(tooltipData.k.value ?? tooltipData.k.close)).toFixed(2) }}</span>
                            </div>
                            <div v-if="tooltipData.avg != null" class="flex justify-between gap-3"><span>均价</span>
                                <span class="font-semibold text-[#f59e0b]">{{ tooltipData.avg.toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨跌</span>
                                <span :class="tooltipData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ tooltipData.chg >= 0 ? '+' : '' }}{{ tooltipData.chg.toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨幅</span>
                                <span :class="tooltipData.pct >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ tooltipData.pct >= 0 ? '+' : '' }}{{ tooltipData.pct.toFixed(2) }}%</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交额</span>
                                <span class="text-[#475569]">{{ formatAmtText(tooltipData.k.amt) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交量</span>
                                <span class="text-[#475569]">{{ formatVol(tooltipData.k.vol) }}</span>
                            </div>
                        </div>
                    </template>
                </div>
            </div>

            <!-- MACD 副图 -->
            <div v-if="showMacd" class="border-t border-[#e5e7eb] relative">
                <span class="absolute top-[4px] left-[8px] text-[10px] text-[#666] font-semibold pointer-events-none z-[1]">
                    MACD (12,26,9)
                </span>
                <div ref="macdEl" class="w-full" style="height: 110px"></div>
            </div>

            <!-- KDJ 副图 -->
            <div v-if="showKdj" class="border-t border-[#e5e7eb] relative">
                <span class="absolute top-[4px] left-[8px] text-[10px] text-[#666] font-semibold pointer-events-none z-[1]">
                    KDJ (9,3,3)
                </span>
                <div ref="kdjEl" class="w-full" style="height: 110px"></div>
            </div>
        </div>
    </div>
</template>

