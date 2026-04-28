/**
 * 技术指标计算（纯 JS，零依赖）。
 *
 * 输入：K 线数组 [{time, open, high, low, close, vol, ...}, ...]
 * 输出：跟输入对齐的指标值数组（前 N 根因不足窗口而为 null，绘图时跳过即可）
 *
 * 提供：
 *   ma(closes, period)         → 简单移动平均
 *   ema(closes, period)        → 指数移动平均
 *   macd(closes, 12, 26, 9)    → {dif, dea, hist}
 *   kdj(klines, 9, 3, 3)       → {k, d, j}
 *   boll(closes, 20, 2)        → {upper, middle, lower}
 *
 * 跟同花顺 / 通达信 默认参数一致。
 */

// ---------------- 工具：从 kline 数组取 close 数组 ----------------
export function pickCloses(klines) {
    return klines.map(k => +k.close)
}

// ---------------- MA (Simple Moving Average) ----------------
export function ma(values, period) {
    const out = new Array(values.length).fill(null)
    if (period < 1) return out
    let sum = 0
    for (let i = 0; i < values.length; i++) {
        sum += values[i]
        if (i >= period) sum -= values[i - period]
        if (i >= period - 1) out[i] = sum / period
    }
    return out
}

// ---------------- EMA (Exponential Moving Average) ----------------
export function ema(values, period) {
    const out = new Array(values.length).fill(null)
    if (!values.length || period < 1) return out
    const k = 2 / (period + 1)
    out[0] = values[0]
    for (let i = 1; i < values.length; i++) {
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    }
    return out
}

// ---------------- MACD (12, 26, 9) ----------------
// DIF = EMA(close, 12) - EMA(close, 26)
// DEA = EMA(DIF, 9)
// HIST = (DIF - DEA) * 2
export function macd(closes, fast = 12, slow = 26, signal = 9) {
    const efast = ema(closes, fast)
    const eslow = ema(closes, slow)
    const dif = closes.map((_, i) => efast[i] - eslow[i])
    const dea = ema(dif, signal)
    const hist = dif.map((v, i) => (v - dea[i]) * 2)
    return { dif, dea, hist }
}

// ---------------- KDJ (9, 3, 3) ----------------
// RSV = (close - lowest_low_n) / (highest_high_n - lowest_low_n) * 100
// K = SMA(RSV, m1, 1), D = SMA(K, m2, 1), J = 3K - 2D
// 通达信 SMA(X, N, M) = (上一日 SMA * (N-M) + 当日 X * M) / N
function smaTdx(values, n, m) {
    const out = new Array(values.length).fill(null)
    let prev = null
    for (let i = 0; i < values.length; i++) {
        const v = values[i]
        if (v == null) { out[i] = prev; continue }
        if (prev == null) {
            prev = v   // 第一个值用自身初始化
        } else {
            prev = (prev * (n - m) + v * m) / n
        }
        out[i] = prev
    }
    return out
}

export function kdj(klines, n = 9, m1 = 3, m2 = 3) {
    const len = klines.length
    const rsv = new Array(len).fill(null)
    for (let i = 0; i < len; i++) {
        if (i < n - 1) continue
        let lo = Infinity, hi = -Infinity
        for (let j = i - n + 1; j <= i; j++) {
            if (+klines[j].low  < lo) lo = +klines[j].low
            if (+klines[j].high > hi) hi = +klines[j].high
        }
        const close = +klines[i].close
        rsv[i] = hi === lo ? 50 : (close - lo) / (hi - lo) * 100
    }
    const k = smaTdx(rsv, m1, 1)
    const d = smaTdx(k, m2, 1)
    const j = k.map((kv, i) => (kv == null || d[i] == null) ? null : 3 * kv - 2 * d[i])
    return { k, d, j }
}

// ---------------- BOLL (20, 2) ----------------
// MID = MA(close, 20)
// STD = stddev(close, 20)
// UP  = MID + 2 * STD
// DN  = MID - 2 * STD
export function boll(closes, period = 20, mult = 2) {
    const middle = ma(closes, period)
    const upper = new Array(closes.length).fill(null)
    const lower = new Array(closes.length).fill(null)
    for (let i = period - 1; i < closes.length; i++) {
        let sum2 = 0
        for (let j = i - period + 1; j <= i; j++) {
            const diff = closes[j] - middle[i]
            sum2 += diff * diff
        }
        const std = Math.sqrt(sum2 / period)
        upper[i] = middle[i] + mult * std
        lower[i] = middle[i] - mult * std
    }
    return { upper, middle, lower }
}

// ---------------- 一站式：根据用户选中的指标，计算并返回所有需要的序列 ----------------
// activeIndicators 形如 ['MA5', 'MA10', 'MA20', 'MACD', 'KDJ', 'BOLL']
// 返回 { mainOverlays: [{name, values, color}, ...], subPanes: [{name, panes}, ...] }
//   - mainOverlays: 画在主图上的线（MA / BOLL）
//   - subPanes:     画在副图上的指标组（MACD / KDJ）
export function computeAll(klines, activeIndicators) {
    const closes = pickCloses(klines)
    const mainOverlays = []
    const subPanes = []

    // MA：MA5/10/20/30/60
    const MA_COLORS = { 5: '#1f77b4', 10: '#ff7f0e', 20: '#9467bd', 30: '#2ca02c', 60: '#dc2626' }
    for (const ind of activeIndicators) {
        const m = /^MA(\d+)$/.exec(ind)
        if (!m) continue
        const p = +m[1]
        mainOverlays.push({
            name: ind,
            values: ma(closes, p),
            color: MA_COLORS[p] || '#888',
        })
    }

    // BOLL
    if (activeIndicators.includes('BOLL')) {
        const { upper, middle, lower } = boll(closes, 20, 2)
        mainOverlays.push({ name: 'BOLL UP',  values: upper,  color: '#a16207' })
        mainOverlays.push({ name: 'BOLL MID', values: middle, color: '#525252' })
        mainOverlays.push({ name: 'BOLL DN',  values: lower,  color: '#16a34a' })
    }

    // MACD
    if (activeIndicators.includes('MACD')) {
        const { dif, dea, hist } = macd(closes)
        subPanes.push({
            name: 'MACD',
            lines: [
                { name: 'DIF', values: dif, color: '#dc2626' },
                { name: 'DEA', values: dea, color: '#2563eb' },
            ],
            histogram: { values: hist, name: 'HIST' },
        })
    }

    // KDJ
    if (activeIndicators.includes('KDJ')) {
        const { k, d, j } = kdj(klines)
        subPanes.push({
            name: 'KDJ',
            lines: [
                { name: 'K', values: k, color: '#dc2626' },
                { name: 'D', values: d, color: '#2563eb' },
                { name: 'J', values: j, color: '#a855f7' },
            ],
        })
    }

    return { mainOverlays, subPanes }
}

// ---------------- 简单支撑 / 压力位（同花顺/通达信"一键标注"模式 + 防插针）----------------
/**
 * 近 window 根 K 线的最高价 = 压力，最低价 = 支撑，各一根线。
 * 防"插针"：候选高/低必须有 ±N 根内的相邻 bar 在容差内"认可"它，
 *           否则视为孤立 spike，跳到次优候选。
 *
 * 推荐 window：日K=60、周K=26、月K=12、年K=5
 *
 * @param klines  K 线数组（需要 high/low/close）
 * @param opts:
 *   window              回看 N 根
 *   spikeNeighborRange  ±N 根内寻找认可邻居
 *   spikeTolerance      邻居价格在此比例内即视为认可（默认 1.5%）
 * @returns { supports: [{price}], resistances: [{price}] }
 */
export function supportResistance(klines, opts = {}) {
    const {
        window = 60,
        spikeNeighborRange = 2,
        spikeTolerance = 0.015,
    } = opts
    if (!klines || !klines.length) return { supports: [], resistances: [] }

    const n = klines.length
    const start = Math.max(0, n - window)

    // 在 [start, n-1] 区间按"求高/求低"顺序找第一个被相邻 K 线认可的候选
    function pickEndorsed(isHigh) {
        const arr = []
        for (let i = start; i < n; i++) {
            arr.push({ idx: i, price: isHigh ? +klines[i].high : +klines[i].low })
        }
        arr.sort((a, b) => isHigh ? b.price - a.price : a.price - b.price)
        for (const cand of arr) {
            if (!(cand.price > 0)) continue
            for (let d = 1; d <= spikeNeighborRange; d++) {
                for (const j of [cand.idx - d, cand.idx + d]) {
                    if (j < start || j >= n) continue
                    const p = isHigh ? +klines[j].high : +klines[j].low
                    if (Math.abs(p - cand.price) / cand.price <= spikeTolerance) return cand
                }
            }
        }
        // 极端兜底（窗口里都是孤点）：返回绝对极值
        return arr[0] || null
    }

    const lastClose = +klines[n - 1].close
    const r = pickEndorsed(true)
    const s = pickEndorsed(false)

    return {
        resistances: r && r.price > lastClose ? [{ price: r.price }] : [],
        supports:    s && s.price < lastClose ? [{ price: s.price }] : [],
    }
}

// ---------------- 箱体高亮（震荡区识别）----------------
/**
 * 滑窗找连续震荡区：(高-低)/中位 ≤ 阈值视为箱体，贪心扩展直到破位。
 *
 * @param klines  K 线数组（需要 high/low）
 * @param opts:
 *   minBars            最少多少根 K 才算一个箱体（短于此忽略）
 *   maxBars            最大扩展长度（避免一个箱体吃掉半年数据）
 *   rangePctThreshold  (高-低)/中位 阈值，超过即破位
 *   recencyBars        只扫最近 N 根，老箱体忽略
 * @returns [{ startIdx, endIdx, startTime, endTime, upper, lower }, ...]
 */
export function detectBoxes(klines, opts = {}) {
    const {
        minBars = 8,                 // 8 根起步（原 12 太严）
        maxBars = 80,
        rangePctThreshold = 0.12,    // (高-低)/中位 ≤ 12%（原 8% 多数 A 股都不符合）
        recencyBars = 250,           // 扫近 250 根，覆盖默认视窗（100 根）外的近期箱体
    } = opts
    if (!klines || klines.length < minBars) return []
    const n = klines.length
    const boxes = []

    let i = Math.max(0, n - recencyBars)
    while (i <= n - minBars) {
        let upper = -Infinity
        let lower = Infinity
        for (let j = i; j < i + minBars; j++) {
            const h = +klines[j].high, l = +klines[j].low
            if (h > upper) upper = h
            if (l < lower) lower = l
        }
        const mean0 = (upper + lower) / 2
        if (mean0 <= 0 || (upper - lower) / mean0 > rangePctThreshold) {
            i++
            continue
        }
        // 贪心扩展到 maxBars 或破位
        let end = i + minBars - 1
        for (let j = i + minBars; j < n && j < i + maxBars; j++) {
            const newU = Math.max(upper, +klines[j].high)
            const newL = Math.min(lower, +klines[j].low)
            const newM = (newU + newL) / 2
            if (newM <= 0 || (newU - newL) / newM > rangePctThreshold) break
            upper = newU; lower = newL; end = j
        }
        boxes.push({
            startIdx: i,
            endIdx:   end,
            startTime: klines[i].time,
            endTime:   klines[end].time,
            upper, lower,
        })
        i = end + 1   // 不允许重叠
    }
    return boxes
}

// ---------------- 平台（加强版箱体：长度更长 + 量能缩量确认）----------------
/**
 * 平台 = 箱体 + 时间足够长 + 平台内均量 < 平台前 priorBars 的均量 × volumeShrinkRatio。
 * 表达"价格横盘 + 资金观望/吸筹中"，比纯箱体更有价值。
 *
 * @param klines
 * @param opts:
 *   minBars            起步最少根数（比 detectBoxes 严，默认 20）
 *   maxBars            最大长度
 *   rangePctThreshold  区间宽度阈值（比 detectBoxes 严，默认 10%）
 *   recencyBars        扫描范围
 *   volumeShrinkRatio  平台内均量 / 之前均量 ≤ 此比例才算缩量（默认 0.85）
 *   priorBars          用平台之前多少根 K 的均量做对照
 * @returns 跟 detectBoxes 相同结构
 */
// ---------------- Zigzag 波段折线 ----------------
/**
 * 把价格走势压缩成"高→低→高→低"的折线骨架，揭示波段结构。
 *
 * 算法：① 大窗口（默认 ±10）过滤小噪声后找 swing 高/低
 *      ② 高低交替化 —— 连续同类型 swing 只保留最极端的那个
 *      ③ 按时间顺序输出折线节点
 *
 * @param klines
 * @param opts:
 *   pivotWindow   左右各 N 根才算 swing（默认 10，越大波段越粗）
 *   recencyBars   只看最近 N 根
 * @returns [{ idx, time, price, type }] —— 按 idx 升序、type 严格高低交替
 */
export function detectZigzag(klines, opts = {}) {
    const { pivotWindow = 10, recencyBars = 250 } = opts
    if (!klines || klines.length < pivotWindow * 2 + 1) return []
    const n = klines.length
    const startScan = Math.max(pivotWindow, n - recencyBars)

    const raw = []
    for (let i = startScan; i < n - pivotWindow; i++) {
        const h = +klines[i].high, l = +klines[i].low
        let isHigh = true, isLow = true
        for (let j = 1; j <= pivotWindow; j++) {
            if (h <= +klines[i - j].high || h <= +klines[i + j].high) isHigh = false
            if (l >= +klines[i - j].low  || l >= +klines[i + j].low)  isLow  = false
            if (!isHigh && !isLow) break
        }
        if (isHigh) raw.push({ idx: i, type: 'high', price: h, time: klines[i].time })
        if (isLow)  raw.push({ idx: i, type: 'low',  price: l, time: klines[i].time })
    }
    if (raw.length < 2) return []
    raw.sort((a, b) => a.idx - b.idx || (a.type === 'high' ? -1 : 1))

    // 高低交替化：连续同类型只保留更极端的那个
    const out = [raw[0]]
    for (let i = 1; i < raw.length; i++) {
        const prev = out[out.length - 1]
        const cur  = raw[i]
        if (cur.type === prev.type) {
            const more = (cur.type === 'high' && cur.price > prev.price)
                      || (cur.type === 'low'  && cur.price < prev.price)
            if (more) out[out.length - 1] = cur
        } else {
            out.push(cur)
        }
    }
    return out
}

// ---------------- 趋势通道（Raff 平行通道：包络法）----------------
/**
 * 主流"趋势通道"做法：先确定通道斜率，再用同斜率做上下平行线包住所有 swing 点。
 *
 * 算法：① 取最近的 zigzag swing 高/低
 *      ② 分别 LR 拟合得到两条 slope，取均值作为通道统一斜率（更稳定）
 *      ③ 上轨截距 = max(swing_high.price - slope × idx) 让所有高点在线下
 *      ④ 下轨截距 = min(swing_low.price  - slope × idx) 让所有低点在线上
 *      → 上下轨严格平行，跟手画"趋势线 + 平行复制"一致
 *
 * @returns null 或 { lowSlope, lowIntercept, highSlope, highIntercept, startIdx, endIdx, startTime, endTime }
 *           （lowSlope 和 highSlope 始终相等 — 平行）
 */
export function detectChannel(klines, opts = {}) {
    const {
        pivotWindow = 10,
        recencyBars = 200,
        maxPivots = 5,
        minPivots = 3,
    } = opts
    const zz = detectZigzag(klines, { pivotWindow, recencyBars })
    if (!zz.length) return null

    const lows  = zz.filter(p => p.type === 'low').slice(-maxPivots)
    const highs = zz.filter(p => p.type === 'high').slice(-maxPivots)
    if (lows.length < minPivots || highs.length < minPivots) return null

    function fitSlope(pts) {
        const n = pts.length
        const meanX = pts.reduce((s, p) => s + p.idx, 0) / n
        const meanY = pts.reduce((s, p) => s + p.price, 0) / n
        let num = 0, den = 0
        for (const p of pts) {
            num += (p.idx - meanX) * (p.price - meanY)
            den += (p.idx - meanX) ** 2
        }
        return den === 0 ? null : num / den
    }

    const lowSlope  = fitSlope(lows)
    const highSlope = fitSlope(highs)
    if (lowSlope == null || highSlope == null) return null

    // 通道统一斜率：上下两组 swing 各自拟合的 slope 取均值
    const slope = (lowSlope + highSlope) / 2

    // 包络：上轨过最高 swing 高点；下轨过最低 swing 低点
    let highIntercept = -Infinity
    for (const h of highs) {
        const b = h.price - slope * h.idx
        if (b > highIntercept) highIntercept = b
    }
    let lowIntercept = Infinity
    for (const l of lows) {
        const b = l.price - slope * l.idx
        if (b < lowIntercept) lowIntercept = b
    }
    if (!isFinite(highIntercept) || !isFinite(lowIntercept)) return null

    const startIdx = Math.min(lows[0].idx, highs[0].idx)
    const endIdx   = klines.length - 1
    return {
        // 平行通道：lowSlope === highSlope === slope
        lowSlope: slope, lowIntercept,
        highSlope: slope, highIntercept,
        startIdx, endIdx,
        startTime: klines[startIdx].time,
        endTime:   klines[endIdx].time,
    }
}

// ---------------- 趋势线（自动识别有效斜线）----------------
/**
 * 算法：① 滑窗找 swing 高/低 ② 同类型 swing 两两连线生成候选 ③ 校验：从起点到现在
 *      没有任何 bar 突破/跌破该线 ④ 按"触及数 × 跨度"打分各取 Top K。
 *
 * 上升趋势线（low-low）= 支撑线；下降趋势线（high-high）= 压力线。
 *
 * @param klines
 * @param opts:
 *   pivotWindow         swing 检测窗口（左右各 N 根）
 *   minSpanBars         两端最少跨多少根 K 才算有意义
 *   recencyBars         扫描范围
 *   topK                每个类型取 Top K
 *   touchTolerancePct   ±此比例内算"触及"
 *   touchMinCount       至少触及次数（含两端点）
 * @returns [{ type, startIdx, endIdx, startTime, endTime, startPrice, endPrice, touches, span, score }]
 */
export function detectTrendlines(klines, opts = {}) {
    const {
        pivotWindow = 5,
        minSpanBars = 15,
        recencyBars = 200,
        topK = 1,
        touchTolerancePct = 0.012,
        touchMinCount = 2,
    } = opts
    if (!klines || klines.length < minSpanBars + pivotWindow * 2) return []
    const n = klines.length
    const startScan = Math.max(pivotWindow, n - recencyBars)

    // 1. 找 swing 高/低
    const swings = []
    for (let i = startScan; i < n - pivotWindow; i++) {
        const h = +klines[i].high, l = +klines[i].low
        let isHigh = true, isLow = true
        for (let j = 1; j <= pivotWindow; j++) {
            if (h <= +klines[i - j].high || h <= +klines[i + j].high) isHigh = false
            if (l >= +klines[i - j].low  || l >= +klines[i + j].low)  isLow  = false
            if (!isHigh && !isLow) break
        }
        if (isHigh) swings.push({ idx: i, type: 'high', price: h })
        if (isLow)  swings.push({ idx: i, type: 'low',  price: l })
    }
    if (swings.length < 2) return []

    // 2. 同类型 swing 两两连线，校验是否被破
    const candidates = []
    for (let a = 0; a < swings.length; a++) {
        for (let b = a + 1; b < swings.length; b++) {
            if (swings[a].type !== swings[b].type) continue
            const p1 = swings[a], p2 = swings[b]
            const span = p2.idx - p1.idx
            if (span < minSpanBars) continue
            const slope = (p2.price - p1.price) / span
            const isHighLine = p1.type === 'high'

            let touches = 2, broken = false
            for (let i = p1.idx + 1; i < n; i++) {
                if (i === p2.idx) continue
                const lineY = p1.price + slope * (i - p1.idx)
                if (lineY <= 0) continue
                if (isHighLine) {
                    const h = +klines[i].high
                    if (h > lineY * (1 + touchTolerancePct)) { broken = true; break }
                    if (Math.abs(h - lineY) / lineY <= touchTolerancePct) touches++
                } else {
                    const l = +klines[i].low
                    if (l < lineY * (1 - touchTolerancePct)) { broken = true; break }
                    if (Math.abs(l - lineY) / lineY <= touchTolerancePct) touches++
                }
            }

            if (!broken && touches >= touchMinCount) {
                candidates.push({
                    type: p1.type,
                    startIdx: p1.idx, endIdx: p2.idx,
                    startTime: klines[p1.idx].time, endTime: klines[p2.idx].time,
                    startPrice: p1.price, endPrice: p2.price,
                    touches, span, score: touches * span,
                })
            }
        }
    }

    const ups   = candidates.filter(c => c.type === 'low' ).sort((a, b) => b.score - a.score).slice(0, topK)
    const downs = candidates.filter(c => c.type === 'high').sort((a, b) => b.score - a.score).slice(0, topK)
    return [...ups, ...downs]
}

export function detectPlatforms(klines, opts = {}) {
    const {
        minBars = 20,
        maxBars = 100,
        rangePctThreshold = 0.10,
        recencyBars = 250,
        volumeShrinkRatio = 0.85,
        priorBars = 30,
    } = opts
    if (!klines || !klines.length) return []

    const candidates = detectBoxes(klines, { minBars, maxBars, rangePctThreshold, recencyBars })

    return candidates.filter(box => {
        // 平台内均量
        let inSum = 0, inCount = 0
        for (let i = box.startIdx; i <= box.endIdx; i++) {
            const v = +klines[i].vol || 0
            if (v > 0) { inSum += v; inCount++ }
        }
        if (inCount === 0) return true   // 没有量数据：保留候选

        // 平台前均量
        const priorStart = Math.max(0, box.startIdx - priorBars)
        let priorSum = 0, priorCount = 0
        for (let i = priorStart; i < box.startIdx; i++) {
            const v = +klines[i].vol || 0
            if (v > 0) { priorSum += v; priorCount++ }
        }
        if (priorCount === 0) return true   // 平台之前无数据：保留

        const inAvg = inSum / inCount
        const priorAvg = priorSum / priorCount
        return inAvg < priorAvg * volumeShrinkRatio
    })
}
