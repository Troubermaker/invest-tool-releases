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
