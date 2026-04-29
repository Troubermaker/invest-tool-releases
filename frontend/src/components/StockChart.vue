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
import { computeAll, pickCloses, supportResistance, supportResistanceCluster, detectBoxes, detectPlatforms, detectTrendlines, detectZigzag, detectChannel, detectFibonacci, detectPatterns, detectMainTrends, detectMainRallyStart, detectGaps, clusterResonance, computeMainWaveScores, computeAccumulationScores, volumeRatio } from '../composables/useTechIndicators'

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
// 简化后的指标 chip 体系（13 个）：
//   - MA 合成 1 个，开启时显示 MA5/10/20（主流配置），不再单独 chip
//   - 压/支 自动包含简单 + 聚类两种识别（合并 SR_K）
//   - 箱体 自动识别平台并换色（合并 PLAT）
//   - 通道 默认包含上下趋势线（合并 TREND）
//   - 删除 主升/主跌（信息融入 主升启动 + 波段）
const allIndicators = [
    { id: 'MA',   label: '均线' },
    { id: 'BOLL', label: 'BOLL' },
    { id: 'SR',   label: '压/支' },
    { id: 'BOX',  label: '箱体' },
    { id: 'CHAN', label: '通道' },
    { id: 'ZZ',   label: '波段' },
    { id: 'FIB',  label: '斐波那契' },
    { id: 'PAT',  label: '形态' },
    { id: 'RALLY', label: '🚀 主升启动' },
    { id: 'GAP',   label: '缺口' },
    { id: 'RESO', label: '共振' },
    { id: 'SIG',  label: '📢 信号' },
    { id: 'MACD', label: 'MACD' },
    { id: 'KDJ',  label: 'KDJ'  },
    { id: 'SCORE', label: '📊 启动分' },
]
const activeIndicators = ref(['MA', 'MACD', 'KDJ'])

const klines = ref([])
const loading = ref(false)
const errMsg = ref('')

// ============ DOM refs + chart instances ============
const mainEl = ref(null)
const macdEl = ref(null)
const kdjEl  = ref(null)
const scoreEl = ref(null)
let mainChart = null, macdChart = null, kdjChart = null, scoreChart = null
let mainSeries = null
let volumeSeries = null
const overlaySeriesMap = new Map()  // name → seriesObj
let macdSeries = null  // {dif, dea, hist}
let kdjSeries  = null  // {k, d, j}
let scoreSeries = null  // 评分柱状 series

// ============ Hover 状态 ============
// hoverIdx：当前十字线对应的索引（-1 表示无 hover，副图 legend 此时回落到最后一根）
// hoverSide：浮动卡片贴左 / 贴右（避免遮挡 cursor 当前 bar）
const hoverIdx = ref(-1)
const hoverSide = ref('left')
let _lastComputed = null   // { mainOverlays, subPanes }
let _timeToIdx = new Map() // 时间 → klines 索引
let _patternByIdx = new Map() // idx → { label, fullName, signal } —— hover 时取完整形态名
let _signalByIdx = new Map()  // idx → [signal, signal, ...] —— hover 时显示完整信号详情
let _scoreByIdx = new Map()   // idx → { score, level, reasons, vr } —— 启动分（确认型）
let _accuByIdx = new Map()    // idx → { score, level, reasons, vr } —— 潜伏分（埋伏型，跟启动分互补）
let _rallyByIdx = new Map()   // breakoutIdx → rally 事件 —— hover 时取历史启动的交易计划

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
    const pattern = _patternByIdx.get(idx) || null
    const signalList = _signalByIdx.get(idx) || null
    const score = _scoreByIdx.get(idx) || null
    const accu = _accuByIdx.get(idx) || null
    const vr = volumeRatio(klines.value, idx, 5)
    const rally = _rallyByIdx.get(idx) || null
    return { k, chg, pct, avg, overlays, subs, pattern, signalList, score, accu, vr, rally }
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

// ============ 主升/主跌段背景色块 ============
const mainTrends = ref([])     // [{ type, startTime, endTime, rangePct, barCount, ... }]
const mainTrendsPx = ref([])   // 像素坐标 [{ x, width, type, label }]
function updateMainTrendsPixels() {
    if (!mainChart || !mainTrends.value.length) {
        if (mainTrendsPx.value.length) mainTrendsPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const out = []
    for (const t of mainTrends.value) {
        const x1 = ts.timeToCoordinate(t.startTime)
        const x2 = ts.timeToCoordinate(t.endTime)
        if (x1 == null || x2 == null) continue
        out.push({
            key: `${t.type}-${t.startIdx}-${t.endIdx}`,
            type: t.type,
            x: Math.min(x1, x2),
            width: Math.max(2, Math.abs(x2 - x1)),
            label: `${t.type === 'up' ? '↑ +' : '↓ '}${t.rangePct.toFixed(1)}% / ${t.barCount}根`,
        })
    }
    mainTrendsPx.value = out
}

// ============ 缺口（跳空）overlay ============
const gaps = ref([])      // [{ direction, time, prevTime, upper, lower, gapPct, filled, filledTime }]
const gapsPx = ref([])    // 像素坐标
function updateGapsPixels() {
    if (!mainChart || !mainSeries || !gaps.value.length || !klines.value.length) {
        if (gapsPx.value.length) gapsPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const lastTime = klines.value[klines.value.length - 1].time
    const out = []
    for (const g of gaps.value) {
        // 缺口产生位置 → 最新 K 线（不延伸到右侧留白区，避免缺口框跑出 K 线区域）
        const x1 = ts.timeToCoordinate(g.prevTime)
        const xLast = ts.timeToCoordinate(lastTime)
        const y1 = mainSeries.priceToCoordinate(g.upper)
        const y2 = mainSeries.priceToCoordinate(g.lower)
        if (x1 == null || xLast == null || y1 == null || y2 == null) continue
        // 已补缺口右端到回补 bar；未补缺口右端到最新 K 线
        let xEnd
        if (g.filled && g.filledTime) {
            const xf = ts.timeToCoordinate(g.filledTime)
            xEnd = xf != null ? xf : xLast
        } else {
            xEnd = xLast
        }
        out.push({
            key: `gap-${g.direction}-${g.time}`,
            direction: g.direction,
            filled: g.filled,
            x: Math.min(x1, xEnd),
            y: Math.min(y1, y2),
            width: Math.max(2, Math.abs(xEnd - x1)),
            height: Math.max(3, Math.abs(y2 - y1)),     // 至少 3px 高，小缺口也能看见
            gapPct: g.gapPct,
            // label 文字位置
            labelX: Math.min(x1, xEnd) + 4,
            labelY: Math.min(y1, y2) - 2,
        })
    }
    gapsPx.value = out
}

// ============ 主升 / 主跌 启动信号（横盘后突破）============
const rallyStarts = ref([])     // [{ direction, consolidation*, breakout*, rallyPct, barsAgo }]
const rallyStartsPx = ref([])   // 像素坐标 + 渲染信息
function updateRallyStartsPixels() {
    if (!mainChart || !mainSeries || !rallyStarts.value.length) {
        if (rallyStartsPx.value.length) rallyStartsPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const out = []
    for (const r of rallyStarts.value) {
        const cx1 = ts.timeToCoordinate(r.consolidationStartTime)
        const cx2 = ts.timeToCoordinate(r.consolidationEndTime)
        const cy1 = mainSeries.priceToCoordinate(r.consolidationUpper)
        const cy2 = mainSeries.priceToCoordinate(r.consolidationLower)
        const bx  = ts.timeToCoordinate(r.breakoutTime)
        if ([cx1, cx2, cy1, cy2, bx].some(v => v == null)) continue
        out.push({
            key: `rally-${r.direction}-${r.breakoutIdx}`,
            direction: r.direction,
            isFresh: r.isFresh,
            cx: Math.min(cx1, cx2),
            cy: Math.min(cy1, cy2),
            cw: Math.max(2, Math.abs(cx2 - cx1)),
            ch: Math.max(2, Math.abs(cy2 - cy1)),
            bx,
            barsAgo: r.barsAgo,
            label: r.direction === 'up' ? '🚀 主升启动' : '⚠ 主跌启动',
            detail: `${r.consolidationBarCount}根横盘后${r.direction === 'up' ? '突破' : '破位'}，已${r.direction === 'up' ? '涨' : '跌'} ${r.rallyPct.toFixed(1)}% · ${r.barsAgo}根前`,
            plan: r.plan,
        })
    }
    rallyStartsPx.value = out
}

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

// ============ 共振区（多源价位聚类，最高优先级的实战工具）============
// resonances: [{ price, sources, count, distancePct }] 按 count 倒序
const resonances = ref([])

// ============ 实时信号（平台突破 / 趋势反转 / 形态+共振）============
// signals: [{ direction: 'up'|'down', label, detail, idx, barsAgo }]
const signals = ref([])

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
    updateMainTrendsPixels()
    updateRallyStartsPixels()
    updateGapsPixels()
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
const showMacd  = computed(() => activeIndicators.value.includes('MACD'))
const showKdj   = computed(() => activeIndicators.value.includes('KDJ'))
const showScore = computed(() => activeIndicators.value.includes('SCORE') && !isLineMode.value)

// 主图高度：根据副图数量动态算（每个副图 110px）
const mainChartHeight = computed(() => {
    const subN = (showMacd.value ? 1 : 0) + (showKdj.value ? 1 : 0) + (showScore.value ? 1 : 0)
    if (subN === 0) return '100%'
    if (subN === 1) return 'calc(100% - 110px)'
    if (subN === 2) return 'calc(100% - 220px)'
    return 'calc(100% - 330px)'
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
            fixLeftEdge:  true,   // 锁左边沿：禁止往历史数据之前拖
            // 不用 fixRightEdge —— 它会吃掉 rightOffset 留白；右边沿改在 subscribeVisibleLogicalRangeChange 里手动 clamp
            rightOffset:  3,      // 最新 bar 右侧留约 18-24px 呼吸空间
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
        if (scoreChart && scoreEl.value) {
            scoreChart.applyOptions({ width: scoreEl.value.clientWidth, height: scoreEl.value.clientHeight })
        }
        updateAllOverlays()
    })
    _resizeObs.observe(mainEl.value)

    // 主图 timeScale 变化：① 手动 clamp 右边沿（替代 fixRightEdge，保留 rightOffset 留白）
    //                  ② 同步副图 ③ 重算 overlay 像素坐标
    mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (_syncing || !range) return
        // —— 手动 clamp ——
        const total = klines.value.length
        const RIGHT_PADDING_BARS = 3
        if (total > 0 && range.to > total - 1 + RIGHT_PADDING_BARS) {
            _syncing = true
            const span = range.to - range.from
            const newTo = total - 1 + RIGHT_PADDING_BARS
            mainChart.timeScale().setVisibleLogicalRange({ from: newTo - span, to: newTo })
            _syncing = false
            return
        }
        _syncing = true
        if (macdChart)  macdChart.timeScale().setVisibleLogicalRange(range)
        if (kdjChart)   kdjChart.timeScale().setVisibleLogicalRange(range)
        if (scoreChart) scoreChart.timeScale().setVisibleLogicalRange(range)
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
            if (macdChart  && macdSeries?.dif) macdChart.setCrosshairPosition(0, time, macdSeries.dif)
            if (kdjChart   && kdjSeries?.k)    kdjChart.setCrosshairPosition(0, time, kdjSeries.k)
            if (scoreChart && scoreSeries)     scoreChart.setCrosshairPosition(0, time, scoreSeries)
        } else {
            if (macdChart)  macdChart.clearCrosshairPosition()
            if (kdjChart)   kdjChart.clearCrosshairPosition()
            if (scoreChart) scoreChart.clearCrosshairPosition()
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
            // 同步到所有非自身的副图
            const others = { macd: macdChart, kdj: kdjChart, score: scoreChart }
            const otherSeries = { macd: macdSeries?.dif, kdj: kdjSeries?.k, score: scoreSeries }
            for (const k of ['macd', 'kdj', 'score']) {
                if (k !== slot && others[k] && otherSeries[k]) {
                    others[k].setCrosshairPosition(0, time, otherSeries[k])
                }
            }
        } else {
            if (mainChart) mainChart.clearCrosshairPosition()
            const others = { macd: macdChart, kdj: kdjChart, score: scoreChart }
            for (const k of ['macd', 'kdj', 'score']) {
                if (k !== slot && others[k]) others[k].clearCrosshairPosition()
            }
        }
    } finally {
        _crosshairSyncing = false
    }
}

async function ensureSubChart(slot /* 'macd' | 'kdj' | 'score' */) {
    const elMap = { macd: macdEl, kdj: kdjEl, score: scoreEl }
    const el = elMap[slot]?.value
    if (!el) return null
    let chart = slot === 'macd' ? macdChart : slot === 'kdj' ? kdjChart : scoreChart
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
            fixLeftEdge:  true,
            rightOffset:  3,
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

    if (slot === 'macd')      macdChart = chart
    else if (slot === 'kdj')  kdjChart = chart
    else                      scoreChart = chart
    return chart
}

function disposeSubChart(slot) {
    if (slot === 'macd' && macdChart) {
        macdChart.remove(); macdChart = null; macdSeries = null
    }
    if (slot === 'kdj' && kdjChart) {
        kdjChart.remove(); kdjChart = null; kdjSeries = null
    }
    if (slot === 'score' && scoreChart) {
        scoreChart.remove(); scoreChart = null; scoreSeries = null
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
            // 阳线（红/涨）—— 空心：极淡红底 + 红色边框（视觉"轻"）
            upColor: 'rgba(220, 38, 38, 0.05)',
            borderUpColor: '#dc2626',
            wickUpColor: '#dc2626',
            // 阴线（绿/跌）—— 实心：整根实心绿（视觉"重"）
            downColor: '#059669',
            borderDownColor: '#059669',
            wickDownColor: '#059669',
            borderVisible: true,
        })
        mainSeries.setData(klines.value.map(k => ({
            time: k.time,
            open: +k.open, high: +k.high, low: +k.low, close: +k.close,
        })))
    }

    // —— 量柱（所有模式都显示：蜡烛按 close>=open 染色，分时/5日按 chg>=0 染色）—— //
    volumeSeries = mainChart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        color: '#94a3b8',
    })
    mainChart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.78, bottom: 0 },
    })
    volumeSeries.setData(klines.value.map(k => {
        // 颜色判定：蜡烛模式用 close vs open；分时/5日用 chg 字段（涨跌相对昨收）
        const isUp = isLineMode.value
            ? (+(k.chg ?? 0) >= 0)
            : (+k.close >= +k.open)
        return {
            time: k.time,
            value: +(k.vol || 0),
            // 上涨柱红半透明（视觉"轻"），下跌柱绿不透明（视觉"重"）
            color: isUp
                ? 'rgba(220, 38, 38, 0.45)'
                : 'rgba(5, 150, 105, 0.80)',
        }
    }))

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

    // —— 压力 / 支撑（合并简单 + 聚类）—— //
    // 同时跑两套算法：简单近 N 根高低 + 聚类被反复试探的档位
    // 简单档：细虚/点线；聚类强档：粗实线带触及次数 — 视觉区分一目了然
    let srLevels = []
    if (isCandleMode && activeIndicators.value.includes('SR')) {
        const SR_WINDOW = { '日K': 60, '周K': 26, '月K': 12, '年K': 5 }
        const window = SR_WINDOW[timeframe.value] || 60
        // 简单版（近 N 根高低）—— 保留 axis label，标题缩短为单字
        const { supports, resistances } = supportResistance(klines.value, { window })
        for (const r of resistances) {
            mainSeries.createPriceLine({
                price: r.price, color: '#dc2626', lineWidth: 1, lineStyle: 2,
                axisLabelVisible: true, title: '压',
            })
            srLevels.push({ price: r.price, source: '压力' })
        }
        for (const s of supports) {
            mainSeries.createPriceLine({
                price: s.price, color: '#16a34a', lineWidth: 1, lineStyle: 4,
                axisLabelVisible: true, title: '支',
            })
            srLevels.push({ price: s.price, source: '支撑' })
        }
        // 聚类强档 —— 关掉 axis label（避免占右轴宽度），线条 + 颜色 + 粗细本身已能识别
        const isClose = (a, b) => Math.abs(a - b) / Math.min(a, b) < 0.01
        const { supports: csups, resistances: cres } = supportResistanceCluster(klines.value)
        for (const r of cres) {
            if (resistances.some(x => isClose(x.price, r.price))) continue
            mainSeries.createPriceLine({
                price: r.price, color: '#dc2626', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: false, title: `强压×${r.touches}`,
            })
            srLevels.push({ price: r.price, source: `强压×${r.touches}` })
        }
        for (const s of csups) {
            if (supports.some(x => isClose(x.price, s.price))) continue
            mainSeries.createPriceLine({
                price: s.price, color: '#16a34a', lineWidth: 2, lineStyle: 0,
                axisLabelVisible: false, title: `强支×${s.touches}`,
            })
            srLevels.push({ price: s.price, source: `强支×${s.touches}` })
        }
    }

    // —— 箱体 / 平台（合并版：detectBoxes 自带 quality 字段，platform 自动升级样式）—— //
    const rects = []
    if (isCandleMode && activeIndicators.value.includes('BOX')) {
        for (const b of detectBoxes(klines.value)) {
            rects.push({ ...b, type: b.quality })   // quality === 'platform' | 'box'
        }
    }
    rectOverlays.value = rects

    // —— 趋势线（不作为独立 chip — 通道开启时自动显示其上下边线作为趋势线）—— //
    // 仍保留 trendlines.value 给共振/信号检测内部使用（下面的逻辑会调 detectTrendlines 直接算）
    trendlines.value = []

    // —— Zigzag 波段折线 —— //
    if (isCandleMode && activeIndicators.value.includes('ZZ')) {
        zigzag.value = detectZigzag(klines.value)
    } else {
        zigzag.value = []
    }

    // —— 🚀 主升 / 主跌 启动识别（长横盘后突破，最强信号）—— //
    _rallyByIdx.clear()
    if (isCandleMode && activeIndicators.value.includes('RALLY')) {
        rallyStarts.value = detectMainRallyStart(klines.value)
        for (const r of rallyStarts.value) _rallyByIdx.set(r.breakoutIdx, r)
    } else {
        rallyStarts.value = []
    }

    // —— 缺口（跳空）—— //
    if (isCandleMode && activeIndicators.value.includes('GAP')) {
        gaps.value = detectGaps(klines.value)
    } else {
        gaps.value = []
    }

// —— 趋势通道（线性回归拟合上下轨）—— //
    if (isCandleMode && activeIndicators.value.includes('CHAN')) {
        channel.value = detectChannel(klines.value)
    } else {
        channel.value = null
    }

    // —— 斐波那契回撤（自动识别主波段，画 7 条回撤线）—— //
    // 关键档（0/50/100）保留 axis label，中间档关掉避免占右轴
    let fibData = null
    if (isCandleMode && activeIndicators.value.includes('FIB')) {
        fibData = detectFibonacci(klines.value)
        if (fibData) {
            for (const lv of fibData.levels) {
                mainSeries.createPriceLine({
                    price: lv.price,
                    color: lv.isKey ? '#ec4899' : '#f9a8d4',
                    lineWidth: 1,
                    lineStyle: lv.isKey ? 2 : 1,
                    axisLabelVisible: lv.isKey,            // 只关键档显示右轴价签
                    title: lv.label,                        // 短标 "38.2%" 而非 "Fib 38.2%"
                })
            }
        }
    }

    // —— K 线形态识别 + 信号 markers —— 累加到 _allMarkers，最后统一 setMarkers —— //
    // 形态识别总是跑（O(N) 开销小），_patternByIdx 始终填充给状态条 / hover tooltip 用；
    // markers 只在 PAT chip 打开时画到 K 线上
    const _allMarkers = []
    _patternByIdx.clear()
    if (isCandleMode) {
        const patterns = detectPatterns(klines.value)
        for (const p of patterns) _patternByIdx.set(p.idx, p)
        if (activeIndicators.value.includes('PAT')) {
            for (const p of patterns) {
                if (p.signal === 'bullish') {
                    _allMarkers.push({ time: p.time, position: 'belowBar', color: '#2563eb', shape: 'arrowUp',   text: p.label, size: 1 })
                } else if (p.signal === 'bearish') {
                    _allMarkers.push({ time: p.time, position: 'aboveBar', color: '#ea580c', shape: 'arrowDown', text: p.label, size: 1 })
                } else {
                    _allMarkers.push({ time: p.time, position: 'aboveBar', color: '#94a3b8', shape: 'square',    text: p.label, size: 1 })
                }
            }
        }
    }

    // —— 共振检测：收集所有活跃指标的"当前关键价位"，聚类找多源共振区 —— //
    if (isCandleMode && activeIndicators.value.includes('RESO')) {
        const lastIdx = klines.value.length - 1
        const lastClose = +klines.value[lastIdx].close
        const lvls = []
        // S/R
        lvls.push(...srLevels)
        // 通道（上下轨在最新 bar 的价格）
        if (channel.value) {
            const c = channel.value
            lvls.push({ price: c.highSlope * lastIdx + c.highIntercept, source: '通道上轨' })
            lvls.push({ price: c.lowSlope  * lastIdx + c.lowIntercept,  source: '通道下轨' })
        }
        // MA / BOLL（最新 bar 的值）
        if (_lastComputed) {
            for (const ovl of _lastComputed.mainOverlays) {
                const v = ovl.values?.[lastIdx]
                if (v != null) lvls.push({ price: v, source: ovl.name })
            }
        }
        // 箱体 / 平台 上下边
        for (const r of rectOverlays.value) {
            const label = r.type === 'platform' ? '平台' : '箱体'
            lvls.push({ price: r.upper, source: `${label}顶` })
            lvls.push({ price: r.lower, source: `${label}底` })
        }
        // 趋势线（外推到最新 bar 的价格）
        for (const tl of trendlines.value) {
            const slope = (tl.endPrice - tl.startPrice) / (tl.endIdx - tl.startIdx)
            const projected = tl.startPrice + slope * (lastIdx - tl.startIdx)
            lvls.push({ price: projected, source: tl.type === 'high' ? '下降趋势线' : '上升趋势线' })
        }
        // 斐波那契回撤（每个档位一个 source，关键档单标，其他档归类为 Fib）
        if (fibData) {
            for (const lv of fibData.levels) {
                if (lv.isKey || lv.ratio === 0.382 || lv.ratio === 0.618) {
                    // 0/38.2/50/61.8/100 当独立 source（这几个最强）
                    lvls.push({ price: lv.price, source: `Fib${lv.label}` })
                }
            }
        }
        // 聚类 + 加距现价百分比
        resonances.value = clusterResonance(lvls).map(r => ({
            ...r,
            distancePct: lastClose > 0 ? ((r.price / lastClose - 1) * 100) : 0,
        }))
    } else {
        resonances.value = []
    }

    // —— 实时信号检测：平台突破 / 趋势反转 / 形态在关键位 —— //
    // 全历史扫描，所有信号都画 marker；左下角面板只显最近 5 条做摘要
    let allSigs = []
    if (isCandleMode && activeIndicators.value.includes('SIG')) {
        const lastIdx = klines.value.length - 1

        // ① 平台突破 —— 扫所有平台，不限"最近"
        const platforms = detectPlatforms(klines.value)
        for (const p of platforms) {
            if (p.endIdx >= lastIdx) continue   // 还在进行中的不算
            for (let i = p.endIdx + 1; i <= lastIdx; i++) {
                const close = +klines.value[i].close
                if (close > p.upper) {
                    allSigs.push({
                        direction: 'up',
                        label: '📈 平台向上突破',
                        detail: `站上 ${p.upper.toFixed(2)} · 平台${p.endIdx - p.startIdx + 1}根缩量`,
                        idx: i, barsAgo: lastIdx - i,
                    })
                    break
                }
                if (close < p.lower) {
                    allSigs.push({
                        direction: 'down',
                        label: '📉 平台向下破位',
                        detail: `跌破 ${p.lower.toFixed(2)} · 平台${p.endIdx - p.startIdx + 1}根`,
                        idx: i, barsAgo: lastIdx - i,
                    })
                    break
                }
            }
        }

        // ② 趋势线突破/跌破 —— 扫整段确认后区间
        const tlines = detectTrendlines(klines.value)
        for (const tl of tlines) {
            const slope = (tl.endPrice - tl.startPrice) / (tl.endIdx - tl.startIdx)
            for (let i = tl.endIdx + 1; i <= lastIdx; i++) {
                const lineY = tl.startPrice + slope * (i - tl.startIdx)
                if (lineY <= 0) continue
                const close = +klines.value[i].close
                if (tl.type === 'low' && close < lineY * 0.992) {
                    allSigs.push({
                        direction: 'down',
                        label: '📉 上升趋势告破',
                        detail: `收盘 ${close.toFixed(2)} 跌破支撑线 ${lineY.toFixed(2)}`,
                        idx: i, barsAgo: lastIdx - i,
                    })
                    break
                }
                if (tl.type === 'high' && close > lineY * 1.008) {
                    allSigs.push({
                        direction: 'up',
                        label: '📈 下降趋势反转',
                        detail: `收盘 ${close.toFixed(2)} 突破压力线 ${lineY.toFixed(2)}`,
                        idx: i, barsAgo: lastIdx - i,
                    })
                    break
                }
            }
        }

        // ③ K 线形态在关键位 —— 扫所有形态（不限最近）
        // 不依赖 RESO chip —— 内部独立收集 key levels
        const keyLevels = []
        const SR_W = { '日K': 60, '周K': 26, '月K': 12, '年K': 5 }[timeframe.value] || 60
        const sr = supportResistance(klines.value, { window: SR_W })
        for (const x of sr.resistances) keyLevels.push({ price: x.price, source: '压力' })
        for (const x of sr.supports)    keyLevels.push({ price: x.price, source: '支撑' })
        const ch = detectChannel(klines.value)
        if (ch) {
            keyLevels.push({ price: ch.highSlope * lastIdx + ch.highIntercept, source: '通道上轨' })
            keyLevels.push({ price: ch.lowSlope  * lastIdx + ch.lowIntercept,  source: '通道下轨' })
        }
        if (_lastComputed) {
            for (const ovl of _lastComputed.mainOverlays) {
                const v = ovl.values?.[lastIdx]
                if (v != null) keyLevels.push({ price: v, source: ovl.name })
            }
        }
        const fib = detectFibonacci(klines.value)
        if (fib) {
            for (const lv of fib.levels) {
                if (lv.isKey || lv.ratio === 0.382 || lv.ratio === 0.618) {
                    keyLevels.push({ price: lv.price, source: `Fib${lv.label}` })
                }
            }
        }

        const patterns = detectPatterns(klines.value)
        // 全部形态（不限最近 N 根）—— 让历史的形态信号也可见
        for (const p of patterns) {
            if (p.signal === 'neutral') continue
            const refPrice = +klines.value[p.idx].close
            const nearby = keyLevels.filter(lv => Math.abs(lv.price - refPrice) / refPrice < 0.015)
            if (nearby.length === 0) continue
            const uniqSources = [...new Set(nearby.map(lv => lv.source))]
            const isMulti = uniqSources.length >= 2
            const tag = p.signal === 'bullish'
                ? (isMulti ? '主升' : '反转')
                : (isMulti ? '主跌' : '反转')
            allSigs.push({
                direction: p.signal === 'bullish' ? 'up' : 'down',
                label: `${p.signal === 'bullish' ? '📈' : '📉'} ${tag}信号：${p.fullName}`,
                detail: `${p.fullName} + ${uniqSources.slice(0, 3).join('/')}`,
                idx: p.idx, barsAgo: lastIdx - p.idx,
            })
        }

        // 按时间倒序，左下角面板只显最近 5 条做摘要
        signals.value = [...allSigs].sort((a, b) => b.idx - a.idx).slice(0, 5)
    } else {
        signals.value = []
    }

    // —— 把所有信号挂成 markers + 索引到 _signalByIdx（全历史，不止最近 5 条） —— //
    _signalByIdx.clear()
    for (const s of allSigs) {
        const arr = _signalByIdx.get(s.idx) || []
        arr.push(s)
        _signalByIdx.set(s.idx, arr)
    }
    // 同一根 K 线只画一次 marker（取该 bar 的第一条信号决定颜色/方向）
    for (const [idx, sigList] of _signalByIdx) {
        const s = sigList[0]
        const time = klines.value[idx].time
        const isUp = s.direction === 'up'
        _allMarkers.push({
            time,
            position: isUp ? 'belowBar' : 'aboveBar',
            color:    isUp ? '#dc2626' : '#16a34a',
            shape:    'circle',
            text:     '!',
            size:     1.6,
        })
    }
    // 统一一次性 setMarkers（PAT + SIG）
    mainSeries.setMarkers(_allMarkers)

    // —— 副图：MACD / KDJ —— //
    await renderSubPane('macd', subPanes.find(p => p.name === 'MACD'))
    await renderSubPane('kdj',  subPanes.find(p => p.name === 'KDJ'))

    // —— 默认视窗 + 拖拉缩放控制 —— //
    // 分时/5日：固定显示全部、禁拖拉
    if (isLineMode.value) {
        mainChart.applyOptions({ handleScroll: false, handleScale: false })
    } else {
        mainChart.applyOptions({ handleScroll: true, handleScale: true })
    }
    // 视窗设置延后到 rAF 里跟 equalizePriceScaleWidths 之后，
    // 避免 priceScale 宽度调整造成的 re-layout 把视窗 drift 掉（首次加载留白丢失的元凶）

    // —— 主升浪综合评分（用 _lastComputed 里已算好的 MA/MACD 复用，不重复计算）—— //
    _scoreByIdx.clear()
    if (isCandleMode) {
        const ma5O  = _lastComputed?.mainOverlays?.find(o => o.name === 'MA5')
        const ma10O = _lastComputed?.mainOverlays?.find(o => o.name === 'MA10')
        const ma20O = _lastComputed?.mainOverlays?.find(o => o.name === 'MA20')
        const macdPane = _lastComputed?.subPanes?.find(p => p.name === 'MACD')
        const difLine = macdPane?.lines?.find(l => l.name === 'DIF')
        const deaLine = macdPane?.lines?.find(l => l.name === 'DEA')
        // 共振按 idx 索引（用 hoverIdx 对应位置查询）—— 简化为最新一根的共振
        const resByIdx = new Map()
        if (resonances.value.length) {
            const lastIdx = klines.value.length - 1
            for (const r of resonances.value) resByIdx.set(lastIdx, r)
        }
        const scores = computeMainWaveScores(klines.value, {
            ma5Arr:  ma5O?.values,
            ma10Arr: ma10O?.values,
            ma20Arr: ma20O?.values,
            difArr:  difLine?.values,
            deaArr:  deaLine?.values,
            histArr: macdPane?.histogram?.values,
            resonancesByIdx: resByIdx,
        })
        for (const [idx, s] of scores) _scoreByIdx.set(idx, s)

        // 潜伏分（独立维度，跟启动分互补）—— 复用同样的 ctx 但需要 KDJ 数据
        _accuByIdx.clear()
        const kdjPane = _lastComputed?.subPanes?.find(p => p.name === 'KDJ')
        const kLine = kdjPane?.lines?.find(l => l.name === 'K')
        const jLine = kdjPane?.lines?.find(l => l.name === 'J')
        const ma60O = _lastComputed?.mainOverlays?.find(o => o.name === 'MA60')
        const accuScores = computeAccumulationScores(klines.value, {
            ma20Arr: ma20O?.values,
            ma60Arr: ma60O?.values,
            difArr:  difLine?.values,
            histArr: macdPane?.histogram?.values,
            kArr:    kLine?.values,
            jArr:    jLine?.values,
            resonancesByIdx: resByIdx,
        })
        for (const [idx, s] of accuScores) _accuByIdx.set(idx, s)
    }

    // —— 评分曲线副图（依赖 _scoreByIdx 已填充）—— //
    await renderScorePane(showScore.value)

    // 等浏览器把所有刻度文本布局完成后再对齐价格刻度宽度（保证三图竖直线连成一根）
    // 然后再设视窗（确保留白稳定）+ 算 overlay 像素坐标
    requestAnimationFrame(() => {
        equalizePriceScaleWidths()
        // 视窗设置：放在 equalize 之后避免 re-layout drift
        if (isLineMode.value) {
            mainChart.timeScale().fitContent()
        } else {
            const defaultBars = DEFAULT_VISIBLE_BARS[timeframe.value]
            const total = klines.value.length
            const RIGHT_PADDING_BARS = 3
            if (defaultBars && total > defaultBars) {
                mainChart.timeScale().setVisibleLogicalRange({
                    from: total - defaultBars,
                    to:   total - 1 + RIGHT_PADDING_BARS,
                })
            } else {
                mainChart.timeScale().fitContent()
            }
        }
        updateAllOverlays()
    })
}

// 三张 chart 价格刻度宽度对齐：取最大宽度回写为 minimumWidth，避免竖直十字线分段错位
function equalizePriceScaleWidths() {
    const charts = [mainChart, macdChart, kdjChart, scoreChart].filter(Boolean)
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
                : (strong ? 'rgba(5, 150, 105, 0.90)'  : 'rgba(5, 150, 105, 0.28)')
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

// 评分副图：单根柱状（按当日评分上色），加 70/50/30 三条参考线
async function renderScorePane(active) {
    if (!active) {
        disposeSubChart('score')
        return
    }
    const chart = await ensureSubChart('score')
    if (!chart) return

    if (scoreSeries) {
        try { chart.removeSeries(scoreSeries) } catch {}
    }
    scoreSeries = chart.addHistogramSeries({
        priceFormat: { type: 'price', precision: 0, minMove: 1 },
        color: '#94a3b8',
        priceLineVisible: false,
    })
    // 数据：所有 K 线都画（无评分的用 whitespace）；按等级染色
    scoreSeries.setData(klines.value.map((k, idx) => {
        const s = _scoreByIdx.get(idx)
        if (!s) return { time: k.time }
        const color = s.score >= 70 ? 'rgba(220, 38, 38, 0.90)'
                    : s.score >= 50 ? 'rgba(234, 88, 12, 0.85)'
                    : s.score >= 30 ? 'rgba(59, 130, 246, 0.75)'
                                    : 'rgba(148, 163, 184, 0.60)'
        return { time: k.time, value: s.score, color }
    }))
    // 参考线（70 强买 / 50 关注 / 30 中性）
    scoreSeries.createPriceLine({ price: 70, color: '#dc2626', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: true, title: '强买' })
    scoreSeries.createPriceLine({ price: 50, color: '#ea580c', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: true, title: '关注' })
    scoreSeries.createPriceLine({ price: 30, color: '#3b82f6', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: true, title: '中性' })
}

// ============ Watchers ============
watch(() => [props.code, timeframe.value], loadData)
watch(activeIndicators, renderAll, { deep: true })

// ============ Lifecycle ============
onMounted(() => loadData())
onUnmounted(() => {
    if (_resizeObs) { _resizeObs.disconnect(); _resizeObs = null }
    if (mainChart) { mainChart.remove(); mainChart = null }
    if (macdChart)  { macdChart.remove();  macdChart  = null }
    if (kdjChart)   { kdjChart.remove();   kdjChart   = null }
    if (scoreChart) { scoreChart.remove(); scoreChart = null }
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

        <!-- 状态条：常驻显示当前/hover 行的关键数据（量比 + 主升评分），不依赖 hover -->
        <div v-if="displayData && !isLineMode"
             class="h-[26px] px-[12px] bg-[#fafafa] border-b border-[#f0f0f0] shrink-0
                    flex items-center gap-[14px] text-[11px] tabular-nums font-mono whitespace-nowrap overflow-x-auto">
            <span class="text-[#888]">{{ formatHoverTime(displayData.k.time) }}</span>
            <span class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">收</span>
                <span class="font-bold"
                      :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    {{ (+displayData.k.close).toFixed(2) }}
                </span>
                <span class="text-[10px]"
                      :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    {{ displayData.chg >= 0 ? '+' : '' }}{{ displayData.pct.toFixed(2) }}%
                </span>
            </span>
            <span v-if="displayData.vr != null" class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">量比</span>
                <span class="font-bold"
                      :class="displayData.vr >= 2.5 ? 'text-[#dc2626]'
                            : displayData.vr >= 1.8 ? 'text-[#ea580c]'
                            : displayData.vr >= 1.2 ? 'text-[#f59e0b]'
                            : displayData.vr < 0.8  ? 'text-[#16a34a]'
                            : 'text-[#475569]'">
                    {{ displayData.vr.toFixed(2) }}×
                </span>
            </span>
            <span v-if="displayData.score" class="flex items-baseline gap-[4px] pl-[8px] border-l border-[#e5e7eb]">
                <span class="text-[#888]">📊 启动</span>
                <span class="font-bold text-[12px]"
                      :class="displayData.score.score >= 70 ? 'text-[#dc2626]'
                            : displayData.score.score >= 50 ? 'text-[#ea580c]'
                            : displayData.score.score >= 30 ? 'text-[#3b82f6]'
                            : 'text-[#94a3b8]'">
                    {{ displayData.score.score }}
                </span>
                <span class="text-[10px] font-semibold px-[4px] py-[1px] rounded-[3px]"
                      :class="displayData.score.score >= 70 ? 'bg-[#fee2e2] text-[#dc2626]'
                            : displayData.score.score >= 50 ? 'bg-[#ffedd5] text-[#ea580c]'
                            : displayData.score.score >= 30 ? 'bg-[#dbeafe] text-[#3b82f6]'
                            : 'bg-[#f1f5f9] text-[#94a3b8]'">
                    {{ displayData.score.level }}
                </span>
            </span>
            <span v-if="displayData.accu" class="flex items-baseline gap-[4px] pl-[6px]">
                <span class="text-[#888]">🌱 潜伏</span>
                <span class="font-bold text-[12px]"
                      :class="displayData.accu.score >= 70 ? 'text-[#16a34a]'
                            : displayData.accu.score >= 55 ? 'text-[#15803d]'
                            : displayData.accu.score >= 40 ? 'text-[#0891b2]'
                            : displayData.accu.score >= 25 ? 'text-[#64748b]'
                            : 'text-[#94a3b8]'">
                    {{ displayData.accu.score }}
                </span>
                <span class="text-[10px] font-semibold px-[4px] py-[1px] rounded-[3px]"
                      :class="displayData.accu.score >= 70 ? 'bg-[#dcfce7] text-[#16a34a]'
                            : displayData.accu.score >= 55 ? 'bg-[#bbf7d0] text-[#15803d]'
                            : displayData.accu.score >= 40 ? 'bg-[#cffafe] text-[#0891b2]'
                            : displayData.accu.score >= 25 ? 'bg-[#f1f5f9] text-[#64748b]'
                            : 'bg-[#f1f5f9] text-[#94a3b8]'">
                    {{ displayData.accu.level }}
                </span>
            </span>
            <span v-if="displayData.score?.reasons?.length"
                  class="text-[10px] text-[#666] truncate flex-1 min-w-0">
                {{ displayData.score.reasons.slice(0, 5).join(' · ') }}
            </span>
            <span v-if="displayData.pattern" class="text-[10px] text-[#94a3b8] shrink-0">
                · 形态: <span class="font-semibold"
                              :class="displayData.pattern.signal === 'bullish' ? 'text-[#2563eb]'
                                    : displayData.pattern.signal === 'bearish' ? 'text-[#ea580c]'
                                    : 'text-[#94a3b8]'">{{ displayData.pattern.fullName }}</span>
            </span>
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

                <!-- 缺口（跳空）overlay：横条状贯穿带 - 未补=鲜艳，已补=淡色虚框 -->
                <svg v-if="gapsPx.length"
                     class="absolute inset-0 pointer-events-none z-[3]"
                     style="width: 100%; height: 100%;">
                    <g v-for="g in gapsPx" :key="g.key">
                        <rect :x="g.x" :y="g.y" :width="g.width" :height="g.height"
                              :fill="g.direction === 'up'
                                ? (g.filled ? 'rgba(220, 38, 38, 0.06)' : 'rgba(220, 38, 38, 0.20)')
                                : (g.filled ? 'rgba(5, 150, 105, 0.06)' : 'rgba(5, 150, 105, 0.22)')"
                              :stroke="g.direction === 'up' ? '#dc2626' : '#059669'"
                              :stroke-width="g.filled ? 0.7 : 1.2"
                              :stroke-dasharray="g.filled ? '3 2' : '0'"
                              :opacity="g.filled ? 0.5 : 1" />
                        <text v-if="!g.filled"
                              :x="g.labelX" :y="g.labelY"
                              :fill="g.direction === 'up' ? '#dc2626' : '#059669'"
                              font-size="9" font-weight="bold" font-family="ui-monospace, monospace">
                            {{ g.direction === 'up' ? '↑' : '↓' }} 缺口 {{ g.gapPct.toFixed(1) }}%
                        </text>
                    </g>
                </svg>

                <!-- 🚀 主升 / 主跌 启动信号（fresh = 鲜艳醒目；historical = 灰色低调）-->
                <svg v-if="rallyStartsPx.length"
                     class="absolute inset-0 pointer-events-none z-[7]"
                     style="width: 100%; height: 100%;">
                    <g v-for="r in rallyStartsPx" :key="r.key">
                        <!-- 横盘期虚框（fresh = 实色 + 透明底；historical = 灰色虚框，比之前清晰）-->
                        <rect :x="r.cx" :y="r.cy" :width="r.cw" :height="r.ch"
                              :fill="r.isFresh
                                    ? (r.direction === 'up' ? 'rgba(220, 38, 38, 0.05)' : 'rgba(22, 163, 74, 0.05)')
                                    : 'rgba(148, 163, 184, 0.18)'"
                              :stroke="r.isFresh
                                     ? (r.direction === 'up' ? '#dc2626' : '#16a34a')
                                     : '#64748b'"
                              :stroke-width="r.isFresh ? 1.5 : 1.3"
                              stroke-dasharray="4 2"
                              :opacity="r.isFresh ? 1 : 0.85" />
                        <!-- 突破点竖虚线（fresh = 红/绿粗；historical = 深灰细）-->
                        <line :x1="r.bx" y1="0" :x2="r.bx" y2="100%"
                              :stroke="r.isFresh
                                     ? (r.direction === 'up' ? '#dc2626' : '#16a34a')
                                     : '#64748b'"
                              :stroke-width="r.isFresh ? 2.5 : 1.4"
                              stroke-dasharray="6 3"
                              :opacity="r.isFresh ? 0.85 : 0.7" />
                        <!-- 历史事件方向 + 时效小标签 -->
                        <text v-if="!r.isFresh"
                              :x="r.bx + 4" y="14"
                              fill="#475569"
                              font-size="11" font-weight="bold"
                              font-family="ui-monospace, monospace">
                            {{ r.direction === 'up' ? '↑' : '↓' }} {{ r.barsAgo }}前
                        </text>
                    </g>
                </svg>
                <!-- 顶部高亮 banner（仅在最新 fresh 事件时出现）+ 交易计划（止损/目标价）-->
                <div v-if="rallyStartsPx.find(r => r.isFresh)"
                     class="absolute top-[5px] left-1/2 -translate-x-1/2 z-[12] pointer-events-none
                            text-[12px] font-bold tabular-nums leading-[1.4]
                            shadow-[0_2px_12px_rgba(0,0,0,0.18)] rounded-[5px]
                            px-[14px] py-[6px] flex flex-col items-center gap-[2px] min-w-[280px]"
                     :class="rallyStartsPx.find(r => r.isFresh).direction === 'up'
                       ? 'bg-gradient-to-r from-[#fef2f2] to-[#fee2e2] border-2 border-[#dc2626] text-[#dc2626]'
                       : 'bg-gradient-to-r from-[#f0fdf4] to-[#dcfce7] border-2 border-[#16a34a] text-[#16a34a]'">
                    <span class="text-[14px]">{{ rallyStartsPx.find(r => r.isFresh).label }}</span>
                    <span class="text-[10px] font-normal opacity-90">{{ rallyStartsPx.find(r => r.isFresh).detail }}</span>
                    <!-- 交易计划：入场/止损/目标 -->
                    <div v-if="rallyStartsPx.find(r => r.isFresh).plan"
                         class="mt-[3px] pt-[3px] border-t border-current/30 w-full text-[10px] font-mono">
                        <div class="flex justify-between gap-2">
                            <span>💎 入场 {{ rallyStartsPx.find(r => r.isFresh).plan.entryPrice }}</span>
                            <span>止损 {{ rallyStartsPx.find(r => r.isFresh).plan.stopLoss }} ({{ rallyStartsPx.find(r => r.isFresh).plan.stopLossPct.toFixed(1) }}%)</span>
                        </div>
                        <div class="flex justify-between gap-2 mt-[1px]">
                            <span>🎯 目标₁ {{ rallyStartsPx.find(r => r.isFresh).plan.target1 }} ({{ rallyStartsPx.find(r => r.isFresh).plan.target1Pct >= 0 ? '+' : '' }}{{ rallyStartsPx.find(r => r.isFresh).plan.target1Pct.toFixed(1) }}%)</span>
                            <span>目标₂ {{ rallyStartsPx.find(r => r.isFresh).plan.target2 }} ({{ rallyStartsPx.find(r => r.isFresh).plan.target2Pct >= 0 ? '+' : '' }}{{ rallyStartsPx.find(r => r.isFresh).plan.target2Pct.toFixed(1) }}%)</span>
                        </div>
                        <div v-if="rallyStartsPx.find(r => r.isFresh).plan.riskRewardRatio"
                             class="text-center mt-[1px]">
                            盈亏比 {{ rallyStartsPx.find(r => r.isFresh).plan.riskRewardRatio }}:1
                        </div>
                    </div>
                </div>

<!-- 实时信号 mini 摘要（左下角）—— 总览，K 线上有红/绿 ! 圆点 marker，hover 看详情 -->
                <div v-if="activeIndicators.includes('SIG') && !isLineMode"
                     class="absolute bottom-[10px] left-[10px] z-[8] pointer-events-none
                            bg-white/95 backdrop-blur-[2px] border-[1.5px] rounded-[5px]
                            shadow-[0_2px_8px_rgba(0,0,0,0.10)]
                            px-[9px] py-[5px] text-[11px] tabular-nums leading-[1.4]"
                     :class="signals.length === 0 ? 'border-[#cbd5e1]'
                           : signals[0].direction === 'up' ? 'border-[#dc2626]/50' : 'border-[#16a34a]/50'">
                    <div v-if="signals.length === 0" class="text-[#888]">
                        📢 暂无活跃信号
                    </div>
                    <div v-else class="flex flex-col gap-[1px]">
                        <span class="font-bold"
                              :class="signals[0].direction === 'up' ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                            📢 {{ signals.length }} 条信号 · 最近 {{ signals[0].barsAgo === 0 ? '当前' : signals[0].barsAgo + '根前' }}
                        </span>
                        <span class="text-[10px] text-[#666]">
                            {{ signals[0].label.replace(/^[📈📉]\s*/, '') }}
                        </span>
                        <span class="text-[9px] text-[#999]">点 K 线上的 <span class="font-bold"
                              :class="signals[0].direction === 'up' ? 'text-[#dc2626]' : 'text-[#16a34a]'">!</span> 圆点查看详情</span>
                    </div>
                </div>

                <!-- 共振区面板（右上角常驻；列出多源信号汇聚的关键价位 + 距现价百分比）-->
                <div v-if="resonances.length"
                     class="absolute top-[6px] right-[10px] z-[8] pointer-events-none
                            bg-white/95 backdrop-blur-[2px] border-[1.5px] border-[#dc2626]/40 rounded-[5px]
                            shadow-[0_2px_8px_rgba(220,38,38,0.15)]
                            px-[9px] py-[6px] text-[11px] tabular-nums font-mono leading-[1.45] min-w-[180px] max-w-[260px]">
                    <div class="font-bold text-[#dc2626] mb-[4px] pb-[3px] border-b border-[#fee2e2] flex items-center gap-[4px]">
                        ⚡ 共振区
                        <span class="text-[10px] text-[#999] font-normal">{{ resonances.length }} 处</span>
                    </div>
                    <div v-for="(r, i) in resonances.slice(0, 6)" :key="i"
                         class="mb-[4px] last:mb-0">
                        <div class="flex items-baseline justify-between gap-[6px]">
                            <span class="text-[13px] font-bold tabular-nums"
                                  :class="r.distancePct >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                                {{ r.price.toFixed(2) }}
                            </span>
                            <span class="text-[10px] tabular-nums"
                                  :class="r.distancePct >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                                {{ r.distancePct >= 0 ? '+' : '' }}{{ r.distancePct.toFixed(2) }}%
                            </span>
                            <span class="text-[10px] text-[#dc2626] font-bold">×{{ r.count }}</span>
                        </div>
                        <div class="text-[10px] text-[#666] leading-tight truncate" :title="r.sources.join(' + ')">
                            {{ r.sources.join(' + ') }}
                        </div>
                    </div>
                </div>

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
                            <div v-if="displayData.vr != null" class="flex justify-between gap-3"><span>量比</span>
                                <span class="font-semibold"
                                      :class="displayData.vr >= 2.5 ? 'text-[#dc2626]'
                                            : displayData.vr >= 1.8 ? 'text-[#ea580c]'
                                            : displayData.vr >= 1.2 ? 'text-[#f59e0b]'
                                            : displayData.vr < 0.8  ? 'text-[#16a34a]'
                                            : 'text-[#475569]'">
                                    {{ displayData.vr.toFixed(2) }}×
                                </span>
                            </div>
                            <!-- K 线形态（如果当前 bar 命中形态）-->
                            <div v-if="displayData.pattern"
                                 class="flex justify-between gap-3 mt-[3px] pt-[3px] border-t border-[#f1f5f9]">
                                <span>形态</span>
                                <span class="font-semibold"
                                      :class="displayData.pattern.signal === 'bullish' ? 'text-[#2563eb]'
                                            : displayData.pattern.signal === 'bearish' ? 'text-[#ea580c]'
                                            : 'text-[#94a3b8]'">
                                    {{ displayData.pattern.signal === 'bullish' ? '↑ ' : displayData.pattern.signal === 'bearish' ? '↓ ' : '◇ ' }}{{ displayData.pattern.fullName }}
                                </span>
                            </div>
                            <!-- 实时信号（如果当前 bar 是信号触发位置）-->
                            <div v-if="displayData.signalList"
                                 class="mt-[3px] pt-[3px] border-t border-[#f1f5f9]">
                                <div v-for="(s, i) in displayData.signalList" :key="i"
                                     class="flex flex-col gap-[1px]" :class="i > 0 ? 'mt-[3px]' : ''">
                                    <span class="font-semibold"
                                          :class="s.direction === 'up' ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                                        {{ s.label }}
                                    </span>
                                    <span class="text-[10px] text-[#666] leading-tight">{{ s.detail }}</span>
                                </div>
                            </div>
                            <!-- 主升启动交易计划（hover 该启动的突破点 K 线时展示，历史/fresh 都看得到）-->
                            <div v-if="displayData.rally && displayData.rally.plan"
                                 class="mt-[3px] pt-[3px] border-t-[2px]"
                                 :class="displayData.rally.direction === 'up' ? 'border-[#dc2626]' : 'border-[#16a34a]'">
                                <div class="font-bold mb-[2px]"
                                     :class="displayData.rally.direction === 'up' ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                                    {{ displayData.rally.direction === 'up' ? '🚀 主升启动' : '⚠ 主跌启动' }}
                                    <span class="text-[9px] font-normal text-[#888]">
                                        ({{ displayData.rally.isFresh ? 'fresh' : displayData.rally.barsAgo + '根前' }})
                                    </span>
                                </div>
                                <div class="text-[10px] text-[#666] mb-[3px]">
                                    {{ displayData.rally.consolidationBarCount }}根横盘 · 已{{ displayData.rally.direction === 'up' ? '涨' : '跌' }} {{ displayData.rally.rallyPct.toFixed(1) }}%
                                </div>
                                <div class="flex flex-col gap-[1px] text-[10px] tabular-nums leading-[1.5]">
                                    <div class="flex justify-between gap-3">
                                        <span class="text-[#888]">💎 入场</span>
                                        <span class="font-semibold text-[#111]">{{ displayData.rally.plan.entryPrice }}</span>
                                    </div>
                                    <div class="flex justify-between gap-3">
                                        <span class="text-[#888]">止损</span>
                                        <span class="font-semibold text-[#16a34a]">
                                            {{ displayData.rally.plan.stopLoss }}
                                            ({{ displayData.rally.plan.stopLossPct.toFixed(1) }}%)
                                        </span>
                                    </div>
                                    <div class="flex justify-between gap-3">
                                        <span class="text-[#888]">🎯 目标₁</span>
                                        <span class="font-semibold text-[#dc2626]">
                                            {{ displayData.rally.plan.target1 }}
                                            ({{ displayData.rally.plan.target1Pct >= 0 ? '+' : '' }}{{ displayData.rally.plan.target1Pct.toFixed(1) }}%)
                                        </span>
                                    </div>
                                    <div class="flex justify-between gap-3">
                                        <span class="text-[#888]">目标₂</span>
                                        <span class="font-semibold text-[#dc2626]">
                                            {{ displayData.rally.plan.target2 }}
                                            ({{ displayData.rally.plan.target2Pct >= 0 ? '+' : '' }}{{ displayData.rally.plan.target2Pct.toFixed(1) }}%)
                                        </span>
                                    </div>
                                    <div v-if="displayData.rally.plan.riskRewardRatio"
                                         class="flex justify-between gap-3 pt-[2px] border-t border-[#f5f5f5]">
                                        <span class="text-[#888]">盈亏比</span>
                                        <span class="font-bold text-[#dc2626]">{{ displayData.rally.plan.riskRewardRatio }} : 1</span>
                                    </div>
                                </div>
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

            <!-- 评分副图 + 行内 legend -->
            <div v-if="showScore" class="border-t border-[#e5e7eb] relative">
                <div class="absolute top-[3px] left-[10px] z-[10] pointer-events-none
                            text-[10px] tabular-nums font-mono leading-[1.4]
                            flex flex-wrap items-center gap-x-[8px]
                            bg-white/85 backdrop-blur-[1px] px-[4px] py-[1px] rounded-[3px]">
                    <span class="text-[#666] font-semibold">📊 主升评分</span>
                    <template v-if="displayData?.score">
                        <span class="font-bold"
                              :class="displayData.score.score >= 70 ? 'text-[#dc2626]'
                                    : displayData.score.score >= 50 ? 'text-[#ea580c]'
                                    : displayData.score.score >= 30 ? 'text-[#3b82f6]'
                                    : 'text-[#94a3b8]'">
                            {{ displayData.score.score }}/100
                        </span>
                        <span class="text-[#666]">{{ displayData.score.level }}</span>
                    </template>
                </div>
                <div ref="scoreEl" class="w-full" style="height: 110px"></div>
            </div>
        </div>
    </div>
</template>

