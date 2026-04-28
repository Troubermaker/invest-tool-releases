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
import { computeAll, pickCloses, supportResistance, detectBoxes, detectPlatforms, detectTrendlines, detectZigzag, detectChannel } from '../composables/useTechIndicators'

const props = defineProps({
    code:   { type: String, required: true },
    name:   { type: String, default: '' },
})
const emit = defineEmits(['close'])

// ============ State ============
const TIMEFRAMES = ['分时', '日K', '5日', '周K', '月K', '年K']
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
    { id: 'SR',   label: '压/支' },
    { id: 'BOX',  label: '箱体' },
    { id: 'PLAT', label: '平台' },
    { id: 'TREND', label: '趋势线' },
    { id: 'ZZ',   label: '波段' },
    { id: 'CHAN', label: '通道' },
    { id: 'MACD', label: 'MACD' },
    { id: 'KDJ',  label: 'KDJ'  },
]
const activeIndicators = ref(['MA5', 'MA10', 'MA20', 'MACD', 'KDJ'])

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

// ============ Hover 状态 ============
// hoverIdx：当前十字线对应的索引（-1 表示无 hover，副图 legend 此时回落到最后一根）
// hoverSide：浮动卡片贴左 / 贴右（避免遮挡 cursor 当前 bar）
const hoverIdx = ref(-1)
const hoverSide = ref('left')
let _lastComputed = null   // { mainOverlays, subPanes }
let _timeToIdx = new Map() // 时间 → klines 索引

const displayIdx = computed(() => {
    if (!klines.value.length) return -1
    return hoverIdx.value >= 0 ? hoverIdx.value : klines.value.length - 1
})

const displayData = computed(() => {
    const idx = displayIdx.value
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
let _syncing = false  // 防止 timeScale 递归同步
let _crosshairSyncing = false  // 防止 crosshair 递归同步

// ============ 趋势线 overlay（SVG 斜线）============
const trendlines = ref([])     // 算法返回的趋势线（基于 idx/time/price 的逻辑数据）
const trendlinesPx = ref([])   // 转成像素坐标的渲染数据
function updateTrendlinesPixels() {
    if (!mainChart || !mainSeries || !trendlines.value.length || !klines.value.length) {
        if (trendlinesPx.value.length) trendlinesPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const lastIdx  = klines.value.length - 1
    const lastTime = klines.value[lastIdx].time
    const out = []
    for (const tl of trendlines.value) {
        // 起点：swing 锚点
        const x1 = ts.timeToCoordinate(tl.startTime)
        const y1 = mainSeries.priceToCoordinate(tl.startPrice)
        // 终点：用斜率把线延伸到最新 bar
        const slope = (tl.endPrice - tl.startPrice) / (tl.endIdx - tl.startIdx)
        const projectedPrice = tl.startPrice + slope * (lastIdx - tl.startIdx)
        const x2 = ts.timeToCoordinate(lastTime)
        const y2 = mainSeries.priceToCoordinate(projectedPrice)
        if (x1 == null || y1 == null || x2 == null || y2 == null) continue
        out.push({
            key: `${tl.type}-${tl.startIdx}-${tl.endIdx}`,
            type: tl.type,
            x1, y1, x2, y2,
            touches: tl.touches,
        })
    }
    trendlinesPx.value = out
}

// ============ Zigzag 波段折线 overlay ============
const zigzag = ref([])         // [{ idx, time, price, type }] 按时间排序
const zigzagPx = ref([])       // [{ x, y, type }] 像素坐标
function updateZigzagPixels() {
    if (!mainChart || !mainSeries || !zigzag.value.length) {
        if (zigzagPx.value.length) zigzagPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const out = []
    for (const p of zigzag.value) {
        const x = ts.timeToCoordinate(p.time)
        const y = mainSeries.priceToCoordinate(p.price)
        if (x == null || y == null) continue
        out.push({ x, y, type: p.type, price: p.price })
    }
    zigzagPx.value = out
}

// ============ 趋势通道 overlay ============
const channel = ref(null)        // { lowSlope, lowIntercept, highSlope, highIntercept, startIdx, endIdx, startTime, endTime }
const channelPx = ref(null)      // { polygon: 'x1,y1 x2,y2 ...', lowLine, highLine }
function updateChannelPixels() {
    if (!mainChart || !mainSeries || !channel.value) {
        if (channelPx.value) channelPx.value = null
        return
    }
    const c = channel.value
    const ts = mainChart.timeScale()
    const startTime = c.startTime
    const endTime   = c.endTime
    const x1 = ts.timeToCoordinate(startTime)
    const x2 = ts.timeToCoordinate(endTime)
    if (x1 == null || x2 == null) { channelPx.value = null; return }
    const lowYStart  = mainSeries.priceToCoordinate(c.lowSlope  * c.startIdx + c.lowIntercept)
    const lowYEnd    = mainSeries.priceToCoordinate(c.lowSlope  * c.endIdx   + c.lowIntercept)
    const highYStart = mainSeries.priceToCoordinate(c.highSlope * c.startIdx + c.highIntercept)
    const highYEnd   = mainSeries.priceToCoordinate(c.highSlope * c.endIdx   + c.highIntercept)
    if ([lowYStart, lowYEnd, highYStart, highYEnd].some(v => v == null)) { channelPx.value = null; return }
    channelPx.value = {
        // 多边形：左上 → 右上 → 右下 → 左下
        polygon: `${x1},${highYStart} ${x2},${highYEnd} ${x2},${lowYEnd} ${x1},${lowYStart}`,
        highLine: { x1, y1: highYStart, x2, y2: highYEnd },
        lowLine:  { x1, y1: lowYStart,  x2, y2: lowYEnd },
    }
}

// 统一刷新所有 overlay 的像素坐标
function updateAllOverlays() {
    updateRectOverlaysPixels()
    updateTrendlinesPixels()
    updateZigzagPixels()
    updateChannelPixels()
}

// ============ 矩形 overlay（箱体 / 平台 共用）============
const rectOverlays = ref([])      // [{ type: 'box'|'platform', startTime, endTime, upper, lower, ... }]
const rectOverlaysPx = ref([])    // 像素坐标
function updateRectOverlaysPixels() {
    if (!mainChart || !mainSeries || !rectOverlays.value.length) {
        if (rectOverlaysPx.value.length) rectOverlaysPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const out = []
    for (const b of rectOverlays.value) {
        const x1 = ts.timeToCoordinate(b.startTime)
        const x2 = ts.timeToCoordinate(b.endTime)
        const y1 = mainSeries.priceToCoordinate(b.upper)
        const y2 = mainSeries.priceToCoordinate(b.lower)
        if (x1 == null || x2 == null || y1 == null || y2 == null) continue
        out.push({
            key: `${b.type}-${b.startIdx}-${b.endIdx}`,
            type: b.type,
            upper: b.upper, lower: b.lower,
            left: Math.min(x1, x2),
            top:  Math.min(y1, y2),
            width:  Math.max(2, Math.abs(x2 - x1)),
            height: Math.max(2, Math.abs(y2 - y1)),
            pct: b.lower > 0 ? (b.upper / b.lower - 1) * 100 : 0,
            bars: (b.endIdx - b.startIdx + 1),
        })
    }
    rectOverlaysPx.value = out
}

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
            attributionLogo: false,
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
        rightPriceScale: { borderColor: '#e2e8f0', minimumWidth: 60 },  // 与副图统一刻度宽度，避免多图竖直线错位
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
        updateAllOverlays()
    })
    _resizeObs.observe(mainEl.value)

    // 主图 timeScale 变化时同步副图 + 重算箱体像素坐标
    mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (_syncing || !range) return
        _syncing = true
        if (macdChart) macdChart.timeScale().setVisibleLogicalRange(range)
        if (kdjChart)  kdjChart.timeScale().setVisibleLogicalRange(range)
        _syncing = false
        updateAllOverlays()
    })

    // 十字线 hover → 更新 hoverIdx（副图 legend 跟随）+ hoverSide（浮动卡片左右翻转）+ 竖直线同步到副图
    // 注意：副图 hover 通过 setCrosshairPosition 反向同步到主图时，param.point 可能 undefined/NaN，
    //      只用 param.time 判 hoverIdx，side 翻转走单独的有效 point 判断。
    mainChart.subscribeCrosshairMove((param) => {
        const idx = param.time ? _timeToIdx.get(param.time) : null
        if (idx == null) {
            if (hoverIdx.value !== -1) hoverIdx.value = -1
            syncCrosshairToSubs(null)
            return
        }
        if (hoverIdx.value !== idx) hoverIdx.value = idx

        // side 只在真实鼠标 hover 主图时才算（程序化触发的 point 不可信）
        if (param.point && param.point.x >= 0 && param.point.y >= 0) {
            const w = mainEl.value?.clientWidth || 0
            const side = w > 0 && param.point.x < w / 2 ? 'right' : 'left'
            if (hoverSide.value !== side) hoverSide.value = side
        }

        syncCrosshairToSubs(param.time)
    })
}

// 主图 ↔ 副图 竖直十字线同步
function syncCrosshairToSubs(time) {
    if (_crosshairSyncing) return
    _crosshairSyncing = true
    try {
        if (time != null) {
            if (macdChart && macdSeries?.dif) macdChart.setCrosshairPosition(0, time, macdSeries.dif)
            if (kdjChart  && kdjSeries?.k)    kdjChart.setCrosshairPosition(0, time, kdjSeries.k)
        } else {
            if (macdChart) macdChart.clearCrosshairPosition()
            if (kdjChart)  kdjChart.clearCrosshairPosition()
        }
    } finally {
        _crosshairSyncing = false
    }
}

function syncCrosshairToMainAndOther(slot, time) {
    if (_crosshairSyncing) return
    _crosshairSyncing = true
    try {
        if (time != null) {
            if (mainChart && mainSeries) mainChart.setCrosshairPosition(0, time, mainSeries)
            if (slot === 'macd' && kdjChart && kdjSeries?.k) kdjChart.setCrosshairPosition(0, time, kdjSeries.k)
            if (slot === 'kdj'  && macdChart && macdSeries?.dif) macdChart.setCrosshairPosition(0, time, macdSeries.dif)
        } else {
            if (mainChart) mainChart.clearCrosshairPosition()
            if (slot === 'macd' && kdjChart)  kdjChart.clearCrosshairPosition()
            if (slot === 'kdj'  && macdChart) macdChart.clearCrosshairPosition()
        }
    } finally {
        _crosshairSyncing = false
    }
}

async function ensureSubChart(slot /* 'macd' | 'kdj' */) {
    const el = slot === 'macd' ? macdEl.value : kdjEl.value
    if (!el) return null
    let chart = slot === 'macd' ? macdChart : kdjChart
    if (chart) return chart

    const { createChart } = await import('lightweight-charts')
    const opts = {
        layout: { background: { color: '#fff' }, textColor: '#475569', fontSize: 10, attributionLogo: false },
        grid: { vertLines: { color: '#f5f5f5' }, horzLines: { color: '#f5f5f5' } },
        timeScale: {
            borderColor: '#e2e8f0',
            timeVisible: true,
            secondsVisible: false,
            visible: false,         // 副图隐藏 x 轴，靠主图驱动
            fixRightEdge: true,
            rightOffset: 4,
        },
        rightPriceScale: { borderColor: '#e2e8f0', minimumWidth: 60 },
        crosshair: {
            mode: 1,
            horzLine: { visible: false, labelVisible: false },  // 副图只保留竖直十字线
        },
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

    // hover 副图时：① 直接更新 hoverIdx（lightweight-charts 的 setCrosshairPosition 不会
    // 触发回调，所以不能依赖往主图的反向传播）② 把竖直线视觉同步到主图 + 另一个副图
    chart.subscribeCrosshairMove((param) => {
        const valid = param.time && param.point && param.point.x >= 0
        if (valid) {
            const idx = _timeToIdx.get(param.time)
            if (idx != null && hoverIdx.value !== idx) hoverIdx.value = idx
        } else {
            if (hoverIdx.value !== -1) hoverIdx.value = -1
        }
        syncCrosshairToMainAndOther(slot, valid ? param.time : null)
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

    // 主图 overlay（MA + BOLL）—— 预热期用 whitespace（只 time 不带 value），保持时间索引与主序列一致
    for (const ovl of mainOverlays) {
        const series = mainChart.addLineSeries({
            color: ovl.color, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false,
            crosshairMarkerVisible: false,
        })
        series.setData(klines.value.map((k, i) => {
            const v = ovl.values[i]
            return v == null ? { time: k.time } : { time: k.time, value: v }
        }))
        overlaySeriesMap.set(ovl.name, series)
    }

    // —— 简单压力/支撑位（同花顺/通达信模式：近 N 根高低，各一条）—— //
    // 红虚线 = 压力（dashed），绿点线 = 支撑（sparseDotted），线型差异化对色弱友好
    if (isCandleMode && activeIndicators.value.includes('SR')) {
        const SR_WINDOW = { '日K': 60, '周K': 26, '月K': 12, '年K': 5 }
        const window = SR_WINDOW[timeframe.value] || 60
        const { supports, resistances } = supportResistance(klines.value, { window })
        for (const r of resistances) {
            mainSeries.createPriceLine({
                price: r.price,
                color: '#dc2626',
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: `压·近${window}`,
            })
        }
        for (const s of supports) {
            mainSeries.createPriceLine({
                price: s.price,
                color: '#16a34a',
                lineWidth: 1,
                lineStyle: 4,
                axisLabelVisible: true,
                title: `支·近${window}`,
            })
        }
    }

    // —— 箱体 / 平台 识别（矩形高亮）—— //
    const rects = []
    if (isCandleMode && activeIndicators.value.includes('BOX')) {
        for (const b of detectBoxes(klines.value)) rects.push({ ...b, type: 'box' })
    }
    if (isCandleMode && activeIndicators.value.includes('PLAT')) {
        for (const p of detectPlatforms(klines.value)) rects.push({ ...p, type: 'platform' })
    }
    rectOverlays.value = rects

    // —— 趋势线识别（SVG 斜线）—— //
    if (isCandleMode && activeIndicators.value.includes('TREND')) {
        trendlines.value = detectTrendlines(klines.value)
    } else {
        trendlines.value = []
    }

    // —— Zigzag 波段折线 —— //
    if (isCandleMode && activeIndicators.value.includes('ZZ')) {
        zigzag.value = detectZigzag(klines.value)
    } else {
        zigzag.value = []
    }

    // —— 趋势通道（线性回归拟合上下轨）—— //
    if (isCandleMode && activeIndicators.value.includes('CHAN')) {
        channel.value = detectChannel(klines.value)
    } else {
        channel.value = null
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

    // 等浏览器把所有刻度文本布局完成后再对齐价格刻度宽度（保证三图竖直线连成一根）
    // 同时算箱体像素坐标（依赖最终的视窗 + 价格刻度尺寸）
    requestAnimationFrame(() => {
        equalizePriceScaleWidths()
        updateAllOverlays()
    })
}

// 三张 chart 价格刻度宽度对齐：取最大宽度回写为 minimumWidth，避免竖直十字线分段错位
function equalizePriceScaleWidths() {
    const charts = [mainChart, macdChart, kdjChart].filter(Boolean)
    if (charts.length < 2) return
    let maxW = 0
    for (const c of charts) {
        try {
            const w = c.priceScale('right').width()
            if (w > maxW) maxW = w
        } catch {}
    }
    if (maxW <= 0) return
    for (const c of charts) {
        try { c.priceScale('right').applyOptions({ minimumWidth: maxW }) } catch {}
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

    // 副图 hist（MACD 才有）—— 预热期用 whitespace；
    // 4 色配色：动能强化用深色、动能减弱用浅色（参考 TradingView MACD 默认配色，但保留 A 股红涨绿跌）
    //   v ≥ 0 且 ↑ → 深红（多头加强）        v ≥ 0 且 ↓ → 浅红（多头减弱）
    //   v <  0 且 ↓ → 深绿（空头加强）        v <  0 且 ↑ → 浅绿（空头减弱）
    if (paneData.histogram) {
        const histSeries = chart.addHistogramSeries({
            priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
            color: '#94a3b8',
        })
        const histVals = paneData.histogram.values
        histSeries.setData(klines.value.map((k, i) => {
            const v = histVals[i]
            if (v == null) return { time: k.time }
            const prev = i > 0 ? histVals[i - 1] : null
            // 动能加强 = 绝对值在变大（正向时 v ≥ prev，负向时 v ≤ prev）
            const strong = prev == null ? true : (v >= 0 ? v >= prev : v <= prev)
            const color = v >= 0
                ? (strong ? 'rgba(220, 38, 38, 0.90)' : 'rgba(220, 38, 38, 0.28)')
                : (strong ? 'rgba(22, 163, 74, 0.90)' : 'rgba(22, 163, 74, 0.28)')
            return { time: k.time, value: v, color }
        }))
        newRefs.hist = histSeries
    }

    // 副图线条（DIF/DEA 或 KDJ 三条）—— 预热期用 whitespace，保持时间索引与主图一致
    for (const line of paneData.lines) {
        const series = chart.addLineSeries({
            color: line.color, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: true,
            crosshairMarkerVisible: false,
        })
        series.setData(klines.value.map((k, i) => {
            const v = line.values[i]
            return v == null ? { time: k.time } : { time: k.time, value: v }
        }))
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

            <!-- 主图 + hover 浮动卡片（仅 hover 时出现；MACD/KDJ 已挪到各自副图 legend，这里不展示）-->
            <div class="relative bg-white" :style="{ height: mainChartHeight }">
                <div ref="mainEl" class="w-full h-full"></div>

                <!-- 趋势线 overlay（SVG 斜线；上升 = 蓝色实线、下降 = 橙色虚线，色弱友好的 hue + 线型双重区分）-->
                <svg v-if="trendlinesPx.length"
                     class="absolute inset-0 pointer-events-none z-[5]"
                     style="width: 100%; height: 100%;">
                    <line v-for="tl in trendlinesPx" :key="tl.key"
                          :x1="tl.x1" :y1="tl.y1" :x2="tl.x2" :y2="tl.y2"
                          :stroke="tl.type === 'high' ? '#ea580c' : '#2563eb'"
                          stroke-width="1.5"
                          :stroke-dasharray="tl.type === 'high' ? '5 3' : '0'" />
                </svg>

                <!-- Zigzag 波段折线 —— 分段着色：上行段（low→high）蓝色实线、下行段（high→low）橙色虚线 -->
                <svg v-if="zigzagPx.length >= 2"
                     class="absolute inset-0 pointer-events-none z-[5]"
                     style="width: 100%; height: 100%;">
                    <line v-for="i in zigzagPx.length - 1" :key="`seg-${i}`"
                          :x1="zigzagPx[i-1].x" :y1="zigzagPx[i-1].y"
                          :x2="zigzagPx[i].x"   :y2="zigzagPx[i].y"
                          :stroke="zigzagPx[i].type === 'high' ? '#2563eb' : '#ea580c'"
                          stroke-width="1.5"
                          :stroke-dasharray="zigzagPx[i].type === 'high' ? '0' : '5 3'" />
                    <circle v-for="(p, i) in zigzagPx" :key="`pt-${i}`"
                            :cx="p.x" :cy="p.y" r="2.8"
                            :fill="p.type === 'high' ? '#ea580c' : '#fff'"
                            :stroke="p.type === 'high' ? '#ea580c' : '#2563eb'"
                            stroke-width="1.2" />
                </svg>

                <!-- 趋势通道（半透明青色填充 + 上下两条边线，独立线性回归拟合）-->
                <svg v-if="channelPx"
                     class="absolute inset-0 pointer-events-none z-[5]"
                     style="width: 100%; height: 100%;">
                    <polygon :points="channelPx.polygon"
                             fill="rgba(6, 182, 212, 0.10)" stroke="none" />
                    <line :x1="channelPx.highLine.x1" :y1="channelPx.highLine.y1"
                          :x2="channelPx.highLine.x2" :y2="channelPx.highLine.y2"
                          stroke="#06b6d4" stroke-width="1.3" stroke-dasharray="0" />
                    <line :x1="channelPx.lowLine.x1" :y1="channelPx.lowLine.y1"
                          :x2="channelPx.lowLine.x2" :y2="channelPx.lowLine.y2"
                          stroke="#06b6d4" stroke-width="1.3" stroke-dasharray="0" />
                </svg>

                <!-- 箱体 / 平台 overlay（绝对定位 div，coords 由 timeToCoordinate / priceToCoordinate 算出）
                     箱体 = indigo（普通震荡），平台 = teal（缩量横盘，更有意义） -->
                <div v-for="b in rectOverlaysPx" :key="b.key"
                     class="absolute pointer-events-none z-[5]"
                     :style="b.type === 'platform' ? {
                        left:   b.left + 'px',
                        top:    b.top + 'px',
                        width:  b.width + 'px',
                        height: b.height + 'px',
                        background: 'rgba(13, 148, 136, 0.18)',
                        borderTop:    '2px solid rgba(13, 148, 136, 0.9)',
                        borderBottom: '2px solid rgba(13, 148, 136, 0.9)',
                     } : {
                        left:   b.left + 'px',
                        top:    b.top + 'px',
                        width:  b.width + 'px',
                        height: b.height + 'px',
                        background: 'rgba(99, 102, 241, 0.15)',
                        borderTop:    '1.5px dashed rgba(99, 102, 241, 0.85)',
                        borderBottom: '1.5px dashed rgba(99, 102, 241, 0.85)',
                     }">
                    <span v-if="b.type === 'platform'"
                          class="absolute top-[2px] left-[3px] text-[10px] text-[#0f766e] font-bold tabular-nums leading-none bg-white/95 px-[3px] py-[1px] rounded-[2px] shadow-sm">
                        平台 {{ b.bars }}根 · 振幅{{ b.pct.toFixed(1) }}%
                    </span>
                    <span v-else
                          class="absolute top-[2px] left-[3px] text-[10px] text-[#4338ca] font-bold tabular-nums leading-none bg-white/90 px-[3px] py-[1px] rounded-[2px] shadow-sm">
                        箱 {{ b.pct.toFixed(1) }}%
                    </span>
                </div>

                <div v-if="hoverIdx >= 0 && displayData"
                     class="absolute top-[6px] z-[10] pointer-events-none
                            bg-white/95 backdrop-blur-[2px] border border-[#e5e7eb] rounded-[5px]
                            shadow-[0_2px_8px_rgba(0,0,0,0.08)]
                            px-[10px] py-[7px] text-[11px] tabular-nums font-mono leading-[1.5] min-w-[170px]"
                     :style="hoverSide === 'right' ? { right: '10px' } : { left: '10px' }">
                    <div class="font-bold text-[#111] mb-[4px] pb-[3px] border-b border-[#f1f5f9]">
                        {{ formatHoverTime(displayData.k.time) }}
                    </div>

                    <!-- 蜡烛模式 -->
                    <template v-if="!isLineMode">
                        <div class="flex flex-col gap-y-[2px] text-[#475569]">
                            <div class="flex justify-between gap-3"><span>开盘</span>
                                <span :class="displayData.k.close >= displayData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+displayData.k.open).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>最高</span>
                                <span :class="displayData.k.close >= displayData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+displayData.k.high).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>最低</span>
                                <span :class="displayData.k.close >= displayData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+displayData.k.low).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>收盘</span>
                                <span class="font-semibold" :class="displayData.k.close >= displayData.k.open ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+displayData.k.close).toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨跌</span>
                                <span :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ displayData.chg >= 0 ? '+' : '' }}{{ displayData.chg.toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨幅</span>
                                <span :class="displayData.pct >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ displayData.pct >= 0 ? '+' : '' }}{{ displayData.pct.toFixed(2) }}%</span>
                            </div>
                            <div v-if="displayData.k.amp != null" class="flex justify-between gap-3"><span>振幅</span>
                                <span class="text-[#111]">{{ (+displayData.k.amp).toFixed(2) }}%</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交额</span>
                                <span class="text-[#475569]">{{ formatAmtText(displayData.k.amt) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交量</span>
                                <span class="text-[#475569]">{{ formatVol(displayData.k.vol) }}</span>
                            </div>
                        </div>

                        <!-- 主图叠加（MA / BOLL）—— MACD/KDJ 已挪到各自副图 legend，不在卡片中显示 -->
                        <div v-if="displayData.overlays.length"
                             class="mt-[4px] pt-[4px] border-t border-[#f1f5f9] flex flex-col gap-y-[2px]">
                            <div v-for="ovl in displayData.overlays" :key="ovl.name"
                                 class="flex justify-between gap-3">
                                <span :style="{ color: ovl.color }">{{ ovl.name }}</span>
                                <span :style="{ color: ovl.color }" class="font-semibold">{{ ovl.value.toFixed(2) }}</span>
                            </div>
                        </div>
                    </template>

                    <!-- 分时/5日 -->
                    <template v-else>
                        <div class="flex flex-col gap-y-[2px] text-[#475569]">
                            <div class="flex justify-between gap-3"><span>现价</span>
                                <span class="font-semibold" :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ (+(displayData.k.value ?? displayData.k.close)).toFixed(2) }}</span>
                            </div>
                            <div v-if="displayData.avg != null" class="flex justify-between gap-3"><span>均价</span>
                                <span class="font-semibold text-[#f59e0b]">{{ displayData.avg.toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨跌</span>
                                <span :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ displayData.chg >= 0 ? '+' : '' }}{{ displayData.chg.toFixed(2) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>涨幅</span>
                                <span :class="displayData.pct >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">{{ displayData.pct >= 0 ? '+' : '' }}{{ displayData.pct.toFixed(2) }}%</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交额</span>
                                <span class="text-[#475569]">{{ formatAmtText(displayData.k.amt) }}</span>
                            </div>
                            <div class="flex justify-between gap-3"><span>成交量</span>
                                <span class="text-[#475569]">{{ formatVol(displayData.k.vol) }}</span>
                            </div>
                        </div>
                    </template>
                </div>
            </div>

            <!-- MACD 副图 + 行内 legend -->
            <div v-if="showMacd" class="border-t border-[#e5e7eb] relative">
                <div class="absolute top-[3px] left-[10px] z-[10] pointer-events-none
                            text-[10px] tabular-nums font-mono leading-[1.4]
                            flex flex-wrap items-center gap-x-[8px]
                            bg-white/85 backdrop-blur-[1px] px-[4px] py-[1px] rounded-[3px]">
                    <span class="text-[#666] font-semibold">MACD (12, 26, 9)</span>
                    <template v-if="displayData">
                        <span v-for="s in displayData.subs.filter(x => x.pane === 'MACD')" :key="s.name"
                              :style="{ color: s.color }" class="font-semibold">
                            {{ s.name }} {{ s.value.toFixed(3) }}
                        </span>
                    </template>
                </div>
                <div ref="macdEl" class="w-full" style="height: 110px"></div>
            </div>

            <!-- KDJ 副图 + 行内 legend -->
            <div v-if="showKdj" class="border-t border-[#e5e7eb] relative">
                <div class="absolute top-[3px] left-[10px] z-[10] pointer-events-none
                            text-[10px] tabular-nums font-mono leading-[1.4]
                            flex flex-wrap items-center gap-x-[8px]
                            bg-white/85 backdrop-blur-[1px] px-[4px] py-[1px] rounded-[3px]">
                    <span class="text-[#666] font-semibold">KDJ (9, 3, 3)</span>
                    <template v-if="displayData">
                        <span v-for="s in displayData.subs.filter(x => x.pane === 'KDJ')" :key="s.name"
                              :style="{ color: s.color }" class="font-semibold">
                            {{ s.name }} {{ s.value.toFixed(2) }}
                        </span>
                    </template>
                </div>
                <div ref="kdjEl" class="w-full" style="height: 110px"></div>
            </div>
        </div>
    </div>
</template>

