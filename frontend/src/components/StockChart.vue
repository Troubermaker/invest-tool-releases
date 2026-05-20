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
import { computeAll, pickCloses, supportResistance, supportResistanceCluster, detectBoxes, detectPlatforms, detectTrendlines, detectZigzag, detectChannel, detectFibonacci, detectPatterns, detectMainTrends, detectMainRallyStart, detectThreeStageLaunch, detectStretchedRally, detectGaps, clusterResonance, computeMainWaveScores, computeAccumulationScores, volumeRatio } from '../composables/useTechIndicators'

const props = defineProps({
    code:   { type: String, required: true },
    name:   { type: String, default: '' },
})
const emit = defineEmits(['close'])

// ============ State ============
const TIMEFRAMES = ['分时', '日K', '5日', '周K', '月K', '年K']

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
// 基础指标（普通用户可见）：MA / BOLL / MACD / KDJ
// 高级指标（仅管理员）：复杂识别类（压支 / 箱体 / 通道 / 波段 / 斐波 / 形态 /
//                                  主升启动 / 三维启动 / 缺口 / 共振 / 信号 / 启动分）
const BASIC_INDICATORS = [
    { id: 'MA',   label: '均线' },
    { id: 'BOLL', label: 'BOLL' },
    { id: 'MACD', label: 'MACD' },
    { id: 'KDJ',  label: 'KDJ'  },
    { id: 'SCORE', label: '📊 启动分' },
]
const ADVANCED_INDICATORS = [
    { id: 'SR',   label: '压/支' },
    { id: 'BOX',  label: '箱体' },
    { id: 'CHAN', label: '通道' },
    { id: 'ZZ',   label: '波段' },
    { id: 'FIB',  label: '斐波那契' },
    { id: 'PAT',  label: '形态' },
    { id: 'RALLY', label: '🚀 主升启动' },
    { id: 'TRIPLE', label: '🎯 三维启动' },
    { id: 'GAP',   label: '缺口' },
    { id: 'RESO', label: '共振' },
    { id: 'SIG',  label: '📢 信号' },
    { id: 'SCORE', label: '📊 启动分' },
]
import { useUserRole } from '../composables/useUserRole'
const { isAdmin } = useUserRole()
// 显示顺序按原次序：MA / BOLL / 高级一组 / MACD / KDJ / 启动分
const allIndicators = computed(() => {
    if (isAdmin.value) {
        return [
            { id: 'MA',   label: '均线' },
            { id: 'BOLL', label: 'BOLL' },
            { id: 'SR',   label: '压/支' },
            { id: 'BOX',  label: '箱体' },
            { id: 'CHAN', label: '通道' },
            { id: 'ZZ',   label: '波段' },
            { id: 'FIB',  label: '斐波那契' },
            { id: 'PAT',  label: '形态' },
            { id: 'RALLY', label: '🚀 主升启动' },
            { id: 'TRIPLE', label: '🎯 三维启动' },
            { id: 'STRETCH', label: '🚀 横盘跳空' },
            { id: 'GAP',   label: '缺口' },
            { id: 'RESO', label: '共振' },
            { id: 'SIG',  label: '📢 信号' },
            { id: 'MACD', label: 'MACD' },
            { id: 'KDJ',  label: 'KDJ'  },
            { id: 'SCORE', label: '📊 启动分' },
        ]
    }
    return BASIC_INDICATORS
})
// 指标选择跨股票/跨会话持久化（模块级 ref + localStorage）
// 切换股票时不会重置，用户勾选/取消后立即持久化
import { useStockChartIndicators } from '../composables/useStockChartIndicators'
const { activeIndicators, clampToBasic, activeTimeframe: timeframe } = useStockChartIndicators()

// 角色变化时清掉超出权限范围的 chip（避免普通用户看到被禁用的复杂指标残留状态）
watch(isAdmin, (v) => {
    if (!v) clampToBasic()
})

const klines = ref([])
const loading = ref(false)
const errMsg = ref('')
const quote = ref(null)   // EM 行情快照（preClose / amplitude / changePct 等准确字段，比 K 线反推可靠）

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

// 三维启动 fresh banner 是否显示（左上角占据 ~250px 高度区域）
const hasFreshTripleBanner = computed(() =>
    activeIndicators.value?.includes?.('TRIPLE') &&
    triplesPx.value?.some?.(t => t.isFresh),
)

// hover 卡片位置（避开鼠标 + 避开左上角 banner）
//   - 鼠标在 K 左半 → 卡片右上（远离鼠标）
//   - 鼠标在 K 右半 + 无 banner → 卡片左上（远离鼠标）
//   - 鼠标在 K 右半 + 有 banner → 卡片贴左但下移到 banner 下方（远离鼠标 + 不被 banner 遮）
const hoverCardStyle = computed(() => {
    if (hoverSide.value === 'right') {
        // 鼠标在左半 → 卡片贴右
        return { right: '10px', top: '6px' }
    }
    // hoverSide === 'left'：鼠标在右半 → 卡片贴左
    if (hasFreshTripleBanner.value) {
        return { left: '10px', top: '250px' }
    }
    return { left: '10px', top: '6px' }
})
let _lastComputed = null   // { mainOverlays, subPanes }
let _timeToIdx = new Map() // 时间 → klines 索引
let _patternByIdx = new Map() // idx → { label, fullName, signal } —— hover 时取完整形态名
let _signalByIdx = new Map()  // idx → [signal, signal, ...] —— hover 时显示完整信号详情
let _scoreByIdx = new Map()   // idx → { score, level, reasons, vr } —— 启动分（确认型）
let _accuByIdx = new Map()    // idx → { score, level, reasons, vr } —— 潜伏分（埋伏型，跟启动分互补）
let _rallyByIdx = new Map()   // breakoutIdx → rally 事件 —— hover 时取历史启动的交易计划
let _baseMarkers = []         // 缓存形态/信号 markers，动态叠加可视区域极值

const displayIdx = computed(() => {
    if (!klines.value.length) return -1
    return hoverIdx.value >= 0 ? hoverIdx.value : klines.value.length - 1
})

const displayData = computed(() => {
    let idx = displayIdx.value
    if (idx < 0 || idx >= klines.value.length) return null
    // 分时模式：hover 落在 whitespace（未到的时间）时，回退到最后一根真实 K
    while (idx >= 0 && klines.value[idx]?._isWhitespace) idx--
    if (idx < 0) return null
    const k = klines.value[idx]
    // 同样跳过 whitespace 找前一根真实 K
    let pi = idx - 1
    while (pi >= 0 && klines.value[pi]?._isWhitespace) pi--
    const prev = pi >= 0 ? klines.value[pi] : null

    let chg = 0, pct = 0
    if (k.chg != null) { chg = +k.chg; pct = +k.pct || 0 }
    else if (prev) {
        const prevClose = +(prev.close ?? prev.value ?? 0)
        const cur = +(k.close ?? k.value ?? 0)
        chg = cur - prevClose
        pct = prevClose ? (chg / prevClose) * 100 : 0
    }

    // 计算分时均价（hover 实时累加到当前 idx）—— 分时模式跳过 whitespace 占位 + 一字板特判
    let avg = null
    if (isLineMode.value) {
        // 一字板（所有真实 K 价格一致）→ 均价 = 价格
        const realSoFar = []
        for (let i = 0; i <= idx; i++) {
            if (!klines.value[i]?._isWhitespace) realSoFar.push(klines.value[i])
        }
        const firstP = realSoFar.length ? +(realSoFar[0].value ?? realSoFar[0].close) : null
        const flat = firstP != null && realSoFar.every(x =>
            Math.abs(+(x.value ?? x.close) - firstP) < 0.001
        )
        if (flat) {
            avg = firstP
        } else {
            let cumPV = 0, cumV = 0
            for (let i = 0; i <= idx; i++) {
                if (klines.value[i]?._isWhitespace) continue
                const p = +(klines.value[i].value ?? klines.value[i].close)
                if (!Number.isFinite(p)) continue
                const v = +(klines.value[i].vol || 0)
                cumPV += p * v; cumV += v
            }
            avg = cumV > 0 ? cumPV / cumV : +(k.value ?? k.close)
        }
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

// ============ 分时 X 轴等距划分（trading-minute synthetic time）============
// 专业分时图把午休 11:30-13:00 压缩成一根分界线，上午 0-50%、下午 50-100% 等距。
// 实现方式：把每根分钟 K 的 time 转换成"合成秒"= dayStart + tradingMinute * 60，
//   tradingMinute 跳过午休（9:30=0, 11:29=119, 13:00=120, 14:59=239）。
// chart 内部完全用合成时间定位 + 锁定范围 [dayStart, dayStart + 240*60]，
// 显示时（轴 label / hover）再反算回真实墙上时间 HH:MM。
let _minuteDayStart = 0   // 当日 9:30 的真实 epoch 秒（仅 分时 模式使用）

function computeDayStartSec(realSec) {
    const d = new Date(realSec * 1000)
    d.setHours(9, 30, 0, 0)
    return Math.floor(d.getTime() / 1000)
}
function realSecToTradingMinute(realSec, dayStart) {
    const wallMin = Math.round((realSec - dayStart) / 60)   // 距 9:30 的墙钟分钟数
    if (wallMin <= 0)   return 0
    if (wallMin <= 119) return wallMin                       // 上午 9:30-11:29 → 0..119
    if (wallMin <= 209) return 119                           // 11:30 + 午休 → 钳到上午末（11:30 数据点占 119）
    if (wallMin <= 329) return 120 + (wallMin - 210)        // 下午 13:00-14:59 → 120..239
    return 239                                               // 15:00 及之后
}
function realToSyntheticSec(realSec, dayStart) {
    return dayStart + realSecToTradingMinute(realSec, dayStart) * 60
}
// 反向：合成秒 → 显示用的墙钟 HH:MM。
// 注意 trading minute 119 实际持有的是 11:30 收盘 K（_parse_trading_minute 把 wallMin 120 clamp 到 119），
// 同理 trading 239 持有 15:00 K。这两个边界要特判，否则会显示成 "11:29" / "14:59"。
function syntheticSecToWallHHMM(syntheticSec, dayStart) {
    const tm = Math.round((syntheticSec - dayStart) / 60)   // trading-minute 0-239
    const pad = n => String(n).padStart(2, '0')
    if (tm === 119) return '11:30'   // 上午收盘边界
    if (tm === 239) return '15:00'   // 下午收盘边界
    if (tm < 120) {
        const total = 9 * 60 + 30 + tm
        return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`
    }
    const total = 13 * 60 + (tm - 120)
    return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`
}

function formatHoverTime(time) {
    if (typeof time === 'string') return time
    const pad = n => String(n).padStart(2, '0')
    if (timeframe.value === '分时' && _minuteDayStart) {
        // 优先用 _realTime（数据带的真实墙钟）；找不到再用 synthetic 反算
        const k = klines.value.find(x => x.time === time)
        if (k && !k._isWhitespace && k._realTime) {
            const rd = new Date(k._realTime * 1000)
            return `${pad(rd.getHours())}:${pad(rd.getMinutes())}`
        }
        return syntheticSecToWallHHMM(time, _minuteDayStart)
    }
    const d = new Date(time * 1000)
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

// ============ 🎯 三维启动 overlay（蓄势→试盘→突破 完整序列）============
const triples = ref([])     // [{ s1Start/End, s1Upper/Lower, s2Idx, s3Idx, currentStage, isFresh, ... }]
const triplesPx = ref([])   // 像素坐标
function updateTriplesPixels() {
    if (!mainChart || !mainSeries || !triples.value.length || !klines.value.length) {
        if (triplesPx.value.length) triplesPx.value = []
        return
    }
    const ts = mainChart.timeScale()
    const out = []
    for (const t of triples.value) {
        // 蓄势期矩形
        const x1 = ts.timeToCoordinate(t.s1StartTime)
        const x2 = ts.timeToCoordinate(t.s1EndTime)
        const yU = mainSeries.priceToCoordinate(t.s1Upper)
        const yL = mainSeries.priceToCoordinate(t.s1Lower)
        if (x1 == null || x2 == null || yU == null || yL == null) continue
        // 试盘 + 突破点 x 坐标
        const xs2 = t.s2Time ? ts.timeToCoordinate(t.s2Time) : null
        const xs3 = t.s3Time ? ts.timeToCoordinate(t.s3Time) : null
        const ys2 = t.s2Price != null ? mainSeries.priceToCoordinate(t.s2Price) : null
        const ys3 = t.s3Price != null ? mainSeries.priceToCoordinate(t.s3Price) : null
        // 用 K 线 high 价格定位勋章（在 K 线最高点上方放 marker，更精准）
        const s2K = (t.s2Idx >= 0 && t.s2Idx < klines.value.length) ? klines.value[t.s2Idx] : null
        const s3K = (t.s3Idx >= 0 && t.s3Idx < klines.value.length) ? klines.value[t.s3Idx] : null
        const ys2High = s2K ? mainSeries.priceToCoordinate(+s2K.high) : null
        const ys3High = s3K ? mainSeries.priceToCoordinate(+s3K.high) : null
        out.push({
            key: `triple-${t.s1StartIdx}`,
            stage: t.currentStage,
            isFresh: t.isFresh,
            cx: Math.min(x1, x2),
            cy: Math.min(yU, yL),
            cw: Math.max(2, Math.abs(x2 - x1)),
            ch: Math.max(2, Math.abs(yU - yL)),
            s2x: xs2, s2y: ys2, s2yHigh: ys2High, s2Type: t.s2Type,
            s3x: xs3, s3y: ys3, s3yHigh: ys3High,
            barsAgoFromS3: t.barsAgoFromS3,
            // 介入价位 / 风控价位（用于 banner 显示交易计划）
            goldenBuyPrice: t.goldenBuyPrice,
            breakoutPrice:  t.breakoutPrice,
            stopLossPrice:  t.stopLossPrice,
            maAddOnPrice:   t.maAddOnPrice,
            maReduceLine:   t.maReduceLine,
            timeStopWarning: t.timeStopWarning,
        })
    }
    triplesPx.value = out
}

// ============ 🚀 横盘跳空 overlay（detectStretchedRally）============
// 跟三维启动并列：长横盘期 + 直接跳空突破（无 S2 试盘环节）
// 显示：青色矩形（横盘期，类似 S1 框）+ 红色 🚀 勋章（突破 K 顶部）
const stretched = ref(null)         // 单个 event 或 null
const stretchedPx = ref(null)       // 像素坐标 或 null
function updateStretchedPixels() {
    if (!mainChart || !mainSeries || !stretched.value || !klines.value.length) {
        if (stretchedPx.value) stretchedPx.value = null
        return
    }
    const s = stretched.value
    const ts = mainChart.timeScale()
    // 横盘期矩形
    const x1 = ts.timeToCoordinate(s.cStartTime)
    const x2 = ts.timeToCoordinate(s.cEndTime)
    const yU = mainSeries.priceToCoordinate(s.cUpper)
    const yL = mainSeries.priceToCoordinate(s.cLower)
    if (x1 == null || x2 == null || yU == null || yL == null) {
        stretchedPx.value = null
        return
    }
    // 突破 K 勋章定位（在突破 K 的 high 上方）
    const xBreak = ts.timeToCoordinate(s.breakTime)
    const breakK = (s.breakIdx >= 0 && s.breakIdx < klines.value.length) ? klines.value[s.breakIdx] : null
    const yBreakHigh = breakK ? mainSeries.priceToCoordinate(+breakK.high) : null
    stretchedPx.value = {
        cx: Math.min(x1, x2),
        cy: Math.min(yU, yL),
        cw: Math.max(2, Math.abs(x2 - x1)),
        ch: Math.max(2, Math.abs(yU - yL)),
        xBreak,
        yBreakHigh,
        consolidationBars: s.consolidationBars,
        liftOffBars: s.liftOffBars,
        breakPct: ((s.breakClose / (klines.value[s.breakIdx - 1]?.close || s.breakClose) - 1) * 100).toFixed(2),
        volRatio: s.volRatio,
        barsAgoFromBreak: s.barsAgoFromBreak,
    }
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
    updateTriplesPixels()
    updateStretchedPixels()
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
    // 行情快照跟 K 线并行拉；header stats 的 preClose / amplitude / changePct 依赖它
    fetchQuote()
    try {
        // 管理员 → 全 timeframe 走 pytdx（分时 / 5 日 改用 1 分钟 K 实现，更可靠）
        // 普通用户 → 全部走 kline_service
        const res = isAdmin.value
            ? await api.getStockKlineViaTdx(props.code, timeframe.value)
            : await api.getStockKline(props.code, timeframe.value)
        if (!res.ok) {
            errMsg.value = res.error || '数据获取失败'
            klines.value = []
        } else {
            klines.value = Array.isArray(res.data) ? res.data : []
            if (!klines.value.length) errMsg.value = '该周期无数据'
            // 分时模式：lightweight-charts 按 bar 逻辑索引定位（不是按 time 值），
            // 11 个点会被均分到整个宽度。要让 11 个点只占左侧 5%，必须始终铺满 240 bars，
            // 未到的时间用 whitespace（仅 time 无 value）占位 —— 这样 X 轴自然就是 9:30 → 15:00 等距划分。
            // 同时午休 11:30-13:00 由于本身就没有数据，对应那段全是 whitespace（视觉上是空白带）
            if (timeframe.value === '分时' && klines.value.length) {
                _minuteDayStart = computeDayStartSec(klines.value[0].time)
                // 1) 用 Map 把真实 K 按合成 minute 索引收集（11:29/11:30 互覆盖）
                const dedup = new Map()
                for (const k of klines.value) {
                    const tm = realSecToTradingMinute(k.time, _minuteDayStart)
                    dedup.set(tm, { ...k, _realTime: k.time, time: _minuteDayStart + tm * 60 })
                }
                // 2) 铺满 240 个 bar；trading minute 120-... 中无数据的为 whitespace 占位
                const fullDay = []
                for (let m = 0; m < 240; m++) {
                    if (dedup.has(m)) {
                        fullDay.push(dedup.get(m))
                    } else {
                        fullDay.push({ time: _minuteDayStart + m * 60, _isWhitespace: true })
                    }
                }
                klines.value = fullDay
            } else {
                _minuteDayStart = 0
            }
        }
    } finally {
        loading.value = false
        await nextTick()
        renderAll()
    }
}

// ============ 切换状态 ============
const isLineMode = computed(() => timeframe.value === '分时' || timeframe.value === '5日')
// 分时 / 5 日 模式下不显示 MACD / KDJ / 评分副图 —— 这些指标都是日线级别才有意义，分钟分时图上无价值
const showMacd  = computed(() => activeIndicators.value.includes('MACD') && !isLineMode.value)
const showKdj   = computed(() => activeIndicators.value.includes('KDJ') && !isLineMode.value)
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
    // 优先用 EM 行情快照（盘中实时，比 K 线最后一根精确到 10s 内）
    const q = quote.value
    if (q && Number.isFinite(+q.price) && +q.price > 0) {
        return {
            price:  +q.price,
            change: Number.isFinite(+q.changeVal)  ? +q.changeVal  : 0,
            pct:    Number.isFinite(+q.changePct) ? +q.changePct : 0,
        }
    }
    // 兜底：从 K 线最后一根真实 K 反推
    if (!klines.value.length) return null
    let lastIdx = klines.value.length - 1
    while (lastIdx >= 0 && klines.value[lastIdx]?._isWhitespace) lastIdx--
    if (lastIdx < 0) return null
    const last = klines.value[lastIdx]
    let prevIdx = lastIdx - 1
    while (prevIdx >= 0 && klines.value[prevIdx]?._isWhitespace) prevIdx--
    const prev = prevIdx >= 0 ? klines.value[prevIdx] : null
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

// 涨跌停 / 昨收 / 振幅 —— 顶部 header 用
// 数据源优先级：EM 行情快照 (quote.value) > K 线反推
// EM 直接返：prevClose / amplitude / changePct，准确且实时
const stockMeta = computed(() => {
    // —— 数据源 1：EM 行情快照 ——
    const q = quote.value
    let preClose = null
    let amplitude = null
    if (q && Number.isFinite(+q.prevClose) && +q.prevClose > 0) {
        preClose = +q.prevClose
        if (Number.isFinite(+q.amplitude)) amplitude = +q.amplitude
    }

    // —— 数据源 2：K 线反推（quote 没拿到时兜底）——
    if (preClose == null && klines.value.length) {
        const realKs = klines.value.filter(k => !k._isWhitespace)
        // 优先用 chg 字段（分时数据有）
        for (const k of realKs) {
            if (k.chg != null) {
                const v = +(k.value ?? k.close)
                if (Number.isFinite(v)) {
                    preClose = v - +k.chg
                    break
                }
            }
        }
        // 兜底用倒数第二根 K 的 close（日K/周K 没有 chg 但有完整 OHLC）
        if (preClose == null && klines.value.length >= 2) {
            const prev = klines.value[klines.value.length - 2]
            if (prev && prev.close != null) preClose = +prev.close
        }
    }

    if (preClose == null || !Number.isFinite(preClose) || preClose <= 0) return null

    // —— 振幅兜底：从 K 线 value 范围算 ——
    if (amplitude == null && klines.value.length) {
        const realKs = klines.value.filter(k => !k._isWhitespace)
        let hi = -Infinity, lo = Infinity
        for (const k of realKs) {
            const v = +(k.value ?? k.close)
            if (Number.isFinite(v)) {
                if (v > hi) hi = v
                if (v < lo) lo = v
            }
            if (k.high != null) { const h = +k.high; if (Number.isFinite(h) && h > hi) hi = h }
            if (k.low  != null) { const l = +k.low;  if (Number.isFinite(l) && l < lo) lo = l  }
        }
        amplitude = (hi !== -Infinity && lo !== Infinity) ? (hi - lo) / preClose * 100 : 0
    }

    // —— 涨跌停百分比 by 代码前缀 + ST 名称识别 ——
    const c = String(props.code || '').trim()
    const nm = String(props.name || q?.name || '')
    let pctLimit = 0.10                                                 // 主板（沪/深 600/000/001/002/003）
    if (c.startsWith('688') || c.startsWith('689')) pctLimit = 0.20      // 科创板
    else if (c.startsWith('300') || c.startsWith('301')) pctLimit = 0.20 // 创业板
    else if (c.startsWith('4') || c.startsWith('8') || c.startsWith('92')) pctLimit = 0.30  // 北交所
    if (/^\*?ST/i.test(nm)) pctLimit = 0.05                              // ST 板块（主板 ±5%）

    // A 股涨跌停一律保留 2 位小数
    const limitUp   = Math.round(preClose * (1 + pctLimit) * 100) / 100
    const limitDown = Math.round(preClose * (1 - pctLimit) * 100) / 100

    return { preClose, limitUp, limitDown, amplitude: amplitude || 0 }
})

// 拉一次 EM 行情快照 —— 给 header stats（preClose / amplitude / 涨跌幅 等）用
async function fetchQuote() {
    if (!props.code) {
        quote.value = null
        return
    }
    try {
        const res = await api.getBatchQuotes([props.code])
        if (res?.ok && res.data && res.data[props.code]) {
            quote.value = res.data[props.code]
        } else {
            quote.value = null
        }
    } catch (e) {
        quote.value = null
    }
}

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
            // 分时模式：time 是合成秒，需要反算回墙钟 HH:MM 才能显示对的轴 label。
            // 其他模式：必须返回有效字符串（否则 lightweight-charts 显示空 label）
            tickMarkFormatter: (time, tickMarkType) => {
                if (typeof time !== 'number') return String(time ?? '')
                if (timeframe.value === '分时' && _minuteDayStart) {
                    return syntheticSecToWallHHMM(time, _minuteDayStart)
                }
                // 其他模式：手写默认 label（跟 lightweight-charts 默认相近）
                const d = new Date(time * 1000)
                const pad = n => String(n).padStart(2, '0')
                // tickMarkType: 0=Year 1=Month 2=DayOfMonth 3=Time 4=TimeWithSeconds
                if (tickMarkType === 0) return String(d.getFullYear())
                if (tickMarkType === 1) return `${d.getMonth() + 1}月`
                if (tickMarkType === 2) return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
                if (tickMarkType === 3) return `${pad(d.getHours())}:${pad(d.getMinutes())}`
                if (tickMarkType === 4) return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
                return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
            },
        },
        rightPriceScale: {
            borderColor: '#e2e8f0',
            minimumWidth: 60,                             // 与副图统一刻度宽度，避免多图竖直线错位
            // 主刻度底部留 25% 给量柱区（跟 Market.vue 指数分时图同款配置），
            // 否则量柱跟价格线叠在一起视觉很糊
            scaleMargins: { top: 0.05, bottom: 0.25 },
        },
        crosshair: {
            mode: 1, // magnet
            // hover 时各 series 的价格 label（显示 "现价 152.50" / "均价 151.32" 的小方框）会贴在右价格轴上，
            // 跟分时图的线交叠很难看。数据已在顶部状态条显示，这里关掉光标的水平 label。
            horzLine: { labelVisible: false },
        },
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
        // —— 手动 clamp —— 分时模式跳过：X 轴锁定到完整交易日，允许 range.to 远超当前 bar 数
        const total = klines.value.length
        const RIGHT_PADDING_BARS = 3
        if (timeframe.value !== '分时' && total > 0 && range.to > total - 1 + RIGHT_PADDING_BARS) {
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
        updateVisibleMinMaxMarkers(range)
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

// ============ 在 K 线视窗内动态标记最高/最低点 ============
function updateVisibleMinMaxMarkers(range) {
    if (!mainSeries || !klines.value.length) return
    const tsRange = range || (mainChart ? mainChart.timeScale().getVisibleLogicalRange() : null)
    if (!tsRange) return
    
    // Y 轴可能会有一点超出，向外扩 1 根确保极值在视口边缘也能识别
    const fromIdx = Math.max(0, Math.floor(tsRange.from) - 1)
    const toIdx = Math.min(klines.value.length - 1, Math.ceil(tsRange.to) + 1)
    if (fromIdx > toIdx) {
        mainSeries.setMarkers(_baseMarkers)
        return
    }

    let maxVal = -Infinity, minVal = Infinity
    let maxIdx = -1, minIdx = -1
    
    for (let i = fromIdx; i <= toIdx; i++) {
        const k = klines.value[i]
        if (!k || k._isWhitespace) continue
        const high = +(k.high ?? k.value ?? k.close)
        const low = +(k.low ?? k.value ?? k.close)
        
        if (Number.isFinite(high) && high > maxVal) { maxVal = high; maxIdx = i }
        if (Number.isFinite(low) && low < minVal) { minVal = low; minIdx = i }
    }
    
    const minMaxMarkers = []
    
    // 动态文本对齐：如果极值点非常靠近左/右边缘，LightweightCharts 的 marker 文本可能会被视口截断。
    // 我们通过给靠近边缘的文本增加空格占位符，把文字向图表中间推。
    const formatEdgeText = (val, idx) => {
        let text = Number(val).toFixed(2)
        // tsRange.from / to 是逻辑坐标，如果是分时图，可能 0 或 239
        if (idx - tsRange.from < 10) return '       ' + text // 靠近左侧，文字向右推
        if (tsRange.to - idx < 10) return text + '       '   // 靠近右侧，文字向左推
        return text
    }

    if (maxIdx !== -1 && maxVal !== -Infinity) {
        minMaxMarkers.push({
            time: klines.value[maxIdx].time,
            position: 'aboveBar',
            color: '#dc2626',
            shape: 'arrowDown',
            text: formatEdgeText(maxVal, maxIdx),
        })
    }
    if (minIdx !== -1 && minIdx !== maxIdx && minVal !== Infinity) {
        minMaxMarkers.push({
            time: klines.value[minIdx].time,
            position: 'belowBar',
            color: '#16a34a',
            shape: 'arrowUp',
            text: formatEdgeText(minVal, minIdx),
        })
    }
    
    // lightweight-charts 要求 marker 必须按时间排序
    const allMarkers = [..._baseMarkers, ...minMaxMarkers].sort((a, b) => {
        if (typeof a.time === 'string' && typeof b.time === 'string') {
            return a.time.localeCompare(b.time)
        }
        return a.time - b.time
    })
    try {
        mainSeries.setMarkers(allMarkers)
    } catch (e) {
        console.warn('setMarkers failed:', e)
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
        // 现价线（专业分时图惯例用深色，跟均价的橙色形成对比；非红绿对比，色弱友好）
        // lineWidth=1 跟均价等宽，一字板时两线 100% 重合不出现亚像素错位
        // 不设 title / 关掉 lastValueVisible / priceLineVisible —— 避免右轴出现 "现价" 标签遮挡线
        // 系列名称已在 toolbar 用色块图例显示了
        mainSeries = mainChart.addLineSeries({
            color: '#1e293b', lineWidth: 1,
            priceScaleId: 'right',
            lastValueVisible: false,
            priceLineVisible: false,
        })
        // 分时模式下 klines 可能含 whitespace 占位（仅 time 无 value），需返回 {time} 走 whitespace data
        mainSeries.setData(klines.value.map(k => k._isWhitespace
            ? { time: k.time }
            : { time: k.time, value: +(k.value ?? k.close) }
        ))

        // —— 昨收水平参考线（专业分时图必备）—— //
        // 从第一根真实 K 反推：preClose = value - chg
        if (timeframe.value === '分时') {
            const firstReal = klines.value.find(k => !k._isWhitespace)
            if (firstReal && firstReal.chg != null) {
                const preClose = +(firstReal.value ?? firstReal.close) - +firstReal.chg
                if (preClose > 0) {
                    mainSeries.createPriceLine({
                        price: preClose,
                        color: '#94a3b8',
                        lineWidth: 1,
                        lineStyle: 2,           // dashed
                        axisLabelVisible: true,
                        title: '昨收',
                    })
                }
            }
        }

        // 均价线（黄）：累计 Σ(价×量)/Σ(量)，5 日模式跨天连续；分时 whitespace 段也走 whitespace
        // 同样不设 title，避免右轴出现 "均价" 标签遮挡线（toolbar 已有色块图例区分）
        const avgSeries = mainChart.addLineSeries({
            color: '#f59e0b', lineWidth: 1,
            priceScaleId: 'right',
            priceLineVisible: false, lastValueVisible: false,
            crosshairMarkerVisible: false,
        })
        // 一字板检测：所有真实 K 的 close 完全一致（涨停 / 跌停 一字板）→ 均价 = 该价格，
        // 跟现价完美重合，避免 VWAP 在 vol=0 段落用 fallback `p` 时跟实际有微小数值差
        const realKs = klines.value.filter(k => !k._isWhitespace)
        const firstP = realKs.length ? +(realKs[0].value ?? realKs[0].close) : null
        const isFlatLine = firstP != null && realKs.every(k =>
            Math.abs(+(k.value ?? k.close) - firstP) < 0.001
        )

        let cumPV = 0, cumV = 0
        const avgData = []
        for (const k of klines.value) {
            if (k._isWhitespace) {
                avgData.push({ time: k.time })   // 未到时间，不画
                continue
            }
            if (isFlatLine) {
                // 一字板：均价直接等于价格
                avgData.push({ time: k.time, value: firstP })
                continue
            }
            const p = +(k.value ?? k.close)
            if (!Number.isFinite(p)) {
                avgData.push({ time: k.time })   // 异常数据当 whitespace 处理，避免污染 cum
                continue
            }
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
        // 量柱区独占底部 20%（跟主刻度的 bottom 25% 之间留 5% gap 做视觉分隔）
        scaleMargins: { top: 0.80, bottom: 0 },
    })
    volumeSeries.setData(klines.value.map(k => {
        if (k._isWhitespace) return { time: k.time }   // 分时 whitespace 占位，量柱也留空
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

    // —— 🎯 三维启动（蓄势→试盘→突破 完整序列）—— //
    if (isCandleMode && activeIndicators.value.includes('TRIPLE')) {
        triples.value = detectThreeStageLaunch(klines.value)
        // 把当前 MA 值附到每个 event 上（用作"加仓点"和"减仓线"提示）
        const lastIdx = klines.value.length - 1
        const ma5LastVal  = _lastComputed?.mainOverlays?.find(o => o.name === 'MA5')?.values?.[lastIdx]
        const ma10LastVal = _lastComputed?.mainOverlays?.find(o => o.name === 'MA10')?.values?.[lastIdx]
        const ma20LastVal = _lastComputed?.mainOverlays?.find(o => o.name === 'MA20')?.values?.[lastIdx]
        const lastClose = +klines.value[lastIdx].close
        for (const t of triples.value) {
            t.maAddOnPrice = ma5LastVal != null ? +ma5LastVal.toFixed(2) : null  // 加仓点：当前 MA5
            t.maAddOnPrice2 = ma10LastVal != null ? +ma10LastVal.toFixed(2) : null  // 备用 MA10
            t.maReduceLine = ma20LastVal != null ? +ma20LastVal.toFixed(2) : null  // 减仓线：MA20
            // 时间止损警告：突破后 5 日内未脱离成本（当前价 < 突破价 × 1.02）
            if (t.s3Idx >= 0 && t.barsAgoFromS3 >= 5 && t.breakoutPrice
                && lastClose < t.breakoutPrice * 1.02) {
                t.timeStopWarning = true
            } else {
                t.timeStopWarning = false
            }
        }
    } else {
        triples.value = []
    }

    // —— 🚀 横盘跳空 detectStretchedRally —— //
    if (isCandleMode && activeIndicators.value.includes('STRETCH')) {
        stretched.value = detectStretchedRally(klines.value)
    } else {
        stretched.value = null
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

    // —— K 线形态识别 + 信号 markers —— 累加到 _baseMarkers，最后统一 setMarkers —— //
    // 形态识别总是跑（O(N) 开销小），_patternByIdx 始终填充给状态条 / hover tooltip 用；
    // markers 只在 PAT chip 打开时画到 K 线上
    _baseMarkers = []
    _patternByIdx.clear()
    if (isCandleMode) {
        const patterns = detectPatterns(klines.value)
        for (const p of patterns) _patternByIdx.set(p.idx, p)
        if (activeIndicators.value.includes('PAT')) {
            for (const p of patterns) {
                if (p.signal === 'bullish') {
                    _baseMarkers.push({ time: p.time, position: 'belowBar', color: '#2563eb', shape: 'arrowUp',   text: p.label, size: 1 })
                } else if (p.signal === 'bearish') {
                    _baseMarkers.push({ time: p.time, position: 'aboveBar', color: '#ea580c', shape: 'arrowDown', text: p.label, size: 1 })
                } else {
                    _baseMarkers.push({ time: p.time, position: 'aboveBar', color: '#94a3b8', shape: 'square',    text: p.label, size: 1 })
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
        _baseMarkers.push({
            time,
            position: isUp ? 'belowBar' : 'aboveBar',
            color:    isUp ? '#dc2626' : '#16a34a',
            shape:    'circle',
            text:     '!',
            size:     1.6,
        })
    }
    
    // 初始化时合并当前视窗的最值 markers
    updateVisibleMinMaxMarkers()

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
        if (timeframe.value === '分时' && _minuteDayStart) {
            // 分时图：240 bar 完整一天（含 whitespace 占位），锁定 logical range 0-239
            // 这样 lightweight-charts 把 240 个 bar 均分到全宽，开盘 N 分钟时数据只占 N/240
            mainChart.timeScale().setVisibleLogicalRange({ from: 0, to: 239 })
        } else if (isLineMode.value) {
            // 5 日模式：跨天点数不一，沿用 fitContent
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

            <!-- 涨跌停 / 昨收 / 振幅 —— 专业分时图标准头部 stats -->
            <div v-if="stockMeta" class="flex items-baseline gap-[10px] text-[11px] tabular-nums text-[#666] pl-[6px] border-l border-[#e5e7eb]">
                <span class="flex items-baseline gap-[3px]">
                    <span class="text-[#94a3b8]">昨收</span>
                    <span class="font-semibold text-[#475569]">{{ stockMeta.preClose.toFixed(2) }}</span>
                </span>
                <span class="flex items-baseline gap-[3px]" title="今日涨停价">
                    <span class="text-[#dc2626]">▲ 涨停</span>
                    <span class="font-semibold text-[#dc2626]">{{ stockMeta.limitUp.toFixed(2) }}</span>
                </span>
                <span class="flex items-baseline gap-[3px]" title="今日跌停价">
                    <span class="text-[#059669]">▼ 跌停</span>
                    <span class="font-semibold text-[#059669]">{{ stockMeta.limitDown.toFixed(2) }}</span>
                </span>
                <span class="flex items-baseline gap-[3px]" title="(最高 - 最低) / 昨收">
                    <span class="text-[#94a3b8]">振幅</span>
                    <span class="font-semibold text-[#475569]">{{ stockMeta.amplitude.toFixed(2) }}%</span>
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

        <!-- 状态条：常驻显示当前/hover 行的关键数据 -->
        <!-- 分时模式专用：所有 hover 数据拼成顶部一行（不再用浮动方框）-->
        <div v-if="displayData && isLineMode"
             class="h-[26px] px-[12px] bg-[#fafafa] border-b border-[#f0f0f0] shrink-0
                    flex items-center gap-[14px] text-[11px] tabular-nums font-mono whitespace-nowrap overflow-x-auto">
            <span class="text-[#475569] font-semibold">{{ formatHoverTime(displayData.k.time) }}</span>
            <span class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">现价</span>
                <span class="font-bold"
                      :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    {{ (+(displayData.k.value ?? displayData.k.close)).toFixed(2) }}
                </span>
            </span>
            <span v-if="displayData.avg != null" class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">均价</span>
                <span class="font-bold text-[#f59e0b]">{{ displayData.avg.toFixed(2) }}</span>
            </span>
            <span class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">涨跌</span>
                <span class="font-bold"
                      :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    {{ displayData.chg >= 0 ? '+' : '' }}{{ displayData.chg.toFixed(2) }}
                </span>
                <span class="text-[10px]"
                      :class="displayData.chg >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                    ({{ displayData.chg >= 0 ? '+' : '' }}{{ displayData.pct.toFixed(2) }}%)
                </span>
            </span>
            <span class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">成交额</span>
                <span class="font-semibold text-[#475569]">{{ formatAmtText(displayData.k.amt) }}</span>
            </span>
            <span class="flex items-baseline gap-[3px]">
                <span class="text-[#888]">成交量</span>
                <span class="font-semibold text-[#475569]">{{ formatVol(displayData.k.vol) }}</span>
            </span>
        </div>

        <!-- 蜡烛模式 状态条：保留原有的量比 + 主升评分等 -->
        <div v-else-if="displayData && !isLineMode"
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

                <!-- 🎯 三维启动 overlay：勋章 marker 版 — K 线上方贴小圆徽章 -->
                <svg v-if="triplesPx.length"
                     class="absolute inset-0 pointer-events-none z-[6]"
                     style="width: 100%; height: 100%;">
                    <g v-for="t in triplesPx" :key="t.key">
                        <!-- 蓄势期矩形（保留：表示一段区间，不是一个点）-->
                        <rect :x="t.cx" :y="t.cy" :width="t.cw" :height="t.ch"
                              fill="rgba(6, 182, 212, 0.06)"
                              stroke="rgba(6, 182, 212, 0.45)"
                              stroke-width="1" stroke-dasharray="3 2" />
                        <text :x="t.cx + 4" :y="t.cy + t.ch - 4"
                              fill="#0e7490" font-size="9" font-weight="600"
                              font-family="ui-monospace, monospace" opacity="0.75">
                            蓄势
                        </text>
                        <!-- 试盘勋章：青色 = 跟蓄势区间同色系 = "还在准备阶段" -->
                        <g v-if="t.s2x != null && t.s2yHigh != null">
                            <circle :cx="t.s2x" :cy="t.s2yHigh - 14" r="9"
                                    fill="#0891b2" stroke="white" stroke-width="1.5"
                                    style="filter: drop-shadow(0 1px 2px rgba(0,0,0,0.15))"/>
                            <text :x="t.s2x" :y="t.s2yHigh - 14"
                                  fill="white" font-size="11" font-weight="700"
                                  text-anchor="middle" dominant-baseline="central"
                                  font-family="ui-monospace, monospace">
                                试
                            </text>
                        </g>
                        <!-- 突破勋章：红色 = "正式行动信号"，比试稍大更醒目 -->
                        <g v-if="t.s3x != null && t.s3yHigh != null">
                            <circle :cx="t.s3x" :cy="t.s3yHigh - 16" r="10"
                                    fill="#dc2626" stroke="white" stroke-width="1.5"
                                    style="filter: drop-shadow(0 1px 2px rgba(0,0,0,0.18))"/>
                            <text :x="t.s3x" :y="t.s3yHigh - 16"
                                  fill="white" font-size="12" font-weight="700"
                                  text-anchor="middle" dominant-baseline="central"
                                  font-family="ui-monospace, monospace">
                                启
                            </text>
                        </g>
                    </g>
                </svg>

                <!-- 🚀 横盘跳空 overlay：跟三维启动同套视觉语言，但用红色 🚀 标突破 K -->
                <svg v-if="stretchedPx"
                     class="absolute inset-0 pointer-events-none z-[6]"
                     style="width: 100%; height: 100%;">
                    <!-- 横盘期矩形（跟三维 S1 同色系）-->
                    <rect :x="stretchedPx.cx" :y="stretchedPx.cy"
                          :width="stretchedPx.cw" :height="stretchedPx.ch"
                          fill="rgba(6, 182, 212, 0.06)"
                          stroke="rgba(6, 182, 212, 0.45)"
                          stroke-width="1" stroke-dasharray="3 2" />
                    <text :x="stretchedPx.cx + 4" :y="stretchedPx.cy + stretchedPx.ch - 4"
                          fill="#0e7490" font-size="9" font-weight="600"
                          font-family="ui-monospace, monospace" opacity="0.75">
                        横盘 {{ stretchedPx.consolidationBars }}根
                    </text>
                    <!-- 突破 K 勋章：红底 🚀 -->
                    <g v-if="stretchedPx.xBreak != null && stretchedPx.yBreakHigh != null">
                        <circle :cx="stretchedPx.xBreak" :cy="stretchedPx.yBreakHigh - 16" r="11"
                                fill="#dc2626" stroke="white" stroke-width="1.5"
                                style="filter: drop-shadow(0 1px 2px rgba(0,0,0,0.18))"/>
                        <text :x="stretchedPx.xBreak" :y="stretchedPx.yBreakHigh - 16"
                              fill="white" font-size="13" font-weight="700"
                              text-anchor="middle" dominant-baseline="central">
                            🚀
                        </text>
                    </g>
                </svg>

                <!-- 横盘跳空顶部 banner（最近 5 根内才显示）-->
                <div v-if="stretchedPx && stretchedPx.barsAgoFromBreak <= 5"
                     class="absolute top-[5px] left-[5px] z-[12] pointer-events-none
                            text-[12px] font-bold tabular-nums leading-[1.4]
                            shadow-[0_2px_12px_rgba(220,38,38,0.20)] rounded-[5px]
                            px-[12px] py-[6px] flex flex-col gap-[2px] min-w-[260px] max-w-[300px]
                            bg-gradient-to-r from-[#fef2f2] to-[#fee2e2]
                            border-2 border-[#dc2626] text-[#dc2626]">
                    <span class="text-[14px]">🚀 横盘跳空</span>
                    <span class="text-[10px] font-normal opacity-90">
                        横盘 {{ stretchedPx.consolidationBars }}根 ·
                        突破 {{ stretchedPx.barsAgoFromBreak }}根前 ·
                        涨幅 +{{ stretchedPx.breakPct }}% ·
                        量比 {{ stretchedPx.volRatio.toFixed(2) }}×
                    </span>
                </div>

                <!-- 三维启动顶部 banner（仅 fresh 时）—— 完整交易计划 -->
                <!-- 位置：左上角（避让主升启动 banner 的居中位置）-->
                <div v-if="triplesPx.find(t => t.isFresh)"
                     class="absolute top-[5px] left-[5px] z-[13] pointer-events-none
                            text-[12px] font-bold tabular-nums leading-[1.4]
                            shadow-[0_2px_12px_rgba(220,38,38,0.20)] rounded-[5px]
                            px-[12px] py-[6px] flex flex-col gap-[2px] min-w-[280px] max-w-[320px]
                            bg-gradient-to-r from-[#fef2f2] to-[#fee2e2]
                            border-2 border-[#dc2626] text-[#dc2626]">
                    <span class="text-[14px]">🎯 三维启动确认</span>
                    <span class="text-[10px] font-normal opacity-90">
                        蓄势+试盘+突破 · {{ triplesPx.find(t => t.isFresh).barsAgoFromS3 }}根前
                    </span>
                    <!-- 三档介入点 + 风控线 -->
                    <div class="mt-[3px] pt-[3px] border-t border-current/30 w-full text-[10px] font-mono leading-[1.5]">
                        <div class="flex justify-between gap-2">
                            <span class="text-[#0e7490]">💎 黄金买点</span>
                            <span>{{ triplesPx.find(t => t.isFresh).goldenBuyPrice?.toFixed(2) }}</span>
                            <span class="text-[#666] font-normal text-[9px]">回踩支撑</span>
                        </div>
                        <div v-if="triplesPx.find(t => t.isFresh).breakoutPrice" class="flex justify-between gap-2">
                            <span>🎯 追涨买点</span>
                            <span>{{ triplesPx.find(t => t.isFresh).breakoutPrice.toFixed(2) }}</span>
                            <span class="text-[#666] font-normal text-[9px]">突破日</span>
                        </div>
                        <div v-if="triplesPx.find(t => t.isFresh).maAddOnPrice" class="flex justify-between gap-2">
                            <span class="text-[#2563eb]">➕ 加仓点</span>
                            <span>{{ triplesPx.find(t => t.isFresh).maAddOnPrice.toFixed(2) }}</span>
                            <span class="text-[#666] font-normal text-[9px]">回踩 MA5</span>
                        </div>
                        <div v-if="triplesPx.find(t => t.isFresh).maReduceLine" class="flex justify-between gap-2">
                            <span class="text-[#ea580c]">⚠ 减仓线</span>
                            <span>{{ triplesPx.find(t => t.isFresh).maReduceLine.toFixed(2) }}</span>
                            <span class="text-[#666] font-normal text-[9px]">跌破 MA20 减仓</span>
                        </div>
                        <div v-if="triplesPx.find(t => t.isFresh).stopLossPrice" class="flex justify-between gap-2">
                            <span class="text-[#16a34a]">⛔ 止损</span>
                            <span>{{ triplesPx.find(t => t.isFresh).stopLossPrice.toFixed(2) }}</span>
                            <span class="text-[#666] font-normal text-[9px]">蓄势下沿×0.97</span>
                        </div>
                        <div v-if="triplesPx.find(t => t.isFresh).timeStopWarning"
                             class="mt-[2px] pt-[2px] border-t border-current/20 text-center text-[10px] font-bold">
                            ⏰ 时间止损警告：突破后 5 日未脱离成本，警惕假突破
                        </div>
                    </div>
                </div>

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

                <!-- hover 卡片 —— 分时模式已挪到顶部行内显示，浮动卡只用于蜡烛模式 -->
                <div v-if="hoverIdx >= 0 && displayData && !isLineMode"
                     class="absolute z-[14] pointer-events-none
                            bg-white/95 backdrop-blur-[2px] border border-[#e5e7eb] rounded-[5px]
                            shadow-[0_2px_8px_rgba(0,0,0,0.08)]
                            px-[10px] py-[7px] text-[11px] tabular-nums font-mono leading-[1.5] min-w-[170px]"
                     :style="hoverCardStyle">
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

