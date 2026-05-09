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

// ---------------- 量比（A 股短线核心指标）----------------
// vol_ratio = 当日成交量 / 前 lookback 日均量
export function volumeRatio(klines, idx, lookback = 5) {
    if (!klines || idx < 1 || !klines[idx]) return null
    const start = Math.max(0, idx - lookback)
    let sum = 0, count = 0
    for (let i = start; i < idx; i++) {
        const v = +klines[i].vol
        if (v > 0) { sum += v; count++ }
    }
    if (count === 0) return null
    const avg = sum / count
    const cur = +klines[idx].vol
    return avg > 0 ? cur / avg : null
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

    // 均线：'MA' chip 展开为 MA5/10/20（默认主流配置）；
    //       同时兼容旧式 'MA5'/'MA10'/...（手工指定单条）
    const MA_COLORS = { 5: '#1f77b4', 10: '#ff7f0e', 20: '#9467bd', 30: '#2ca02c', 60: '#dc2626' }
    const periodsToShow = new Set()
    if (activeIndicators.includes('MA')) [5, 10, 20, 30, 60].forEach(p => periodsToShow.add(p))
    for (const ind of activeIndicators) {
        const m = /^MA(\d+)$/.exec(ind)
        if (m) periodsToShow.add(+m[1])
    }
    for (const p of [...periodsToShow].sort((a, b) => a - b)) {
        mainOverlays.push({
            name: `MA${p}`,
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
        // 朋友的质量评分：触碰次数 + 量能递减 bonus + 收盘价居中度
        const span = end - i + 1
        const range = (upper - lower) / ((upper + lower) / 2)
        let upperTouches = 0, lowerTouches = 0
        let inBox = 0
        for (let k = i; k <= end; k++) {
            const h = +klines[k].high, l = +klines[k].low, c = +klines[k].close
            if (h > upper * 0.997) upperTouches++
            if (l < lower * 1.003) lowerTouches++
            if (c >= lower * 0.998 && c <= upper * 1.002) inBox++
        }
        // 量能递减检查（前半 vs 后半）
        let firstHalfVol = 0, firstHalfCount = 0
        let secondHalfVol = 0, secondHalfCount = 0
        const mid = i + Math.floor(span / 2)
        for (let k = i; k < mid; k++) {
            const v = +klines[k].vol
            if (v > 0) { firstHalfVol += v; firstHalfCount++ }
        }
        for (let k = mid; k <= end; k++) {
            const v = +klines[k].vol
            if (v > 0) { secondHalfVol += v; secondHalfCount++ }
        }
        const volDeclining = firstHalfCount > 0 && secondHalfCount > 0
            && (secondHalfVol / secondHalfCount) < (firstHalfVol / firstHalfCount)

        // quality 判定：满足"长 + 窄 + 量能递减 + 双轨触碰"则升级为 platform
        const inBoxRatio = span > 0 ? inBox / span : 0
        const isPlatform = span >= 20
            && range <= 0.10
            && inBoxRatio >= 0.7
            && upperTouches >= 2
            && lowerTouches >= 2
            && volDeclining

        // 评分（朋友的公式）
        let score = (upperTouches + lowerTouches) * 2
                  + (1 - range / rangePctThreshold) * 10
                  + span * 0.5
        if (volDeclining) score *= 1.3

        boxes.push({
            startIdx: i,
            endIdx:   end,
            startTime: klines[i].time,
            endTime:   klines[end].time,
            upper, lower,
            quality: isPlatform ? 'platform' : 'box',
            score,
            upperTouches, lowerTouches,
            volDeclining,
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
// ---------------- 稳健主升 / 稳健主跌（持续推进式）—— 已弃用 ----------------
// 历史保留代码（不再被任何 chip 调用）；如需重新启用，参考 git 历史 checkout 对应的 chip 注册
function _unused_detectSteadyRally(klines, opts = {}) {
    const {
        minBars = 30,
        minTotalGainPct = 0.10,    // 累计涨/跌幅 ≥ 10%
        ma5CompliancePct = 0.6,    // 沿 MA5 爬升 —— close 站上 MA5 的占比 ≥ 60%
        ma20BreakDeepPct = 0.02,   // close < MA20 × (1-2%) 视为"有效跌破" MA20
    } = opts
    if (!klines || klines.length < minBars + 20) return null
    const n = klines.length
    const lastIdx = n - 1
    const closes = klines.map(k => +k.close)
    const ma5Arr  = ma(closes, 5)
    const ma20Arr = ma(closes, 20)
    if (ma20Arr[lastIdx] == null) return null

    // === 稳健主升：找最近一次"有效跌破 MA20"的 bar，从其后开始算 rally ===
    let upStartIdx = -1
    for (let i = lastIdx; i >= 1; i--) {
        if (ma20Arr[i] == null) break
        if (closes[i] < ma20Arr[i] * (1 - ma20BreakDeepPct)) {
            upStartIdx = i + 1
            break
        }
    }
    if (upStartIdx < 0) upStartIdx = 1

    const upSpan = lastIdx - upStartIdx
    if (upSpan >= minBars) {
        const startPrice = +klines[upStartIdx].close
        const endPrice   = +klines[lastIdx].close
        const gainPct    = startPrice > 0 ? (endPrice - startPrice) / startPrice : 0
        if (gainPct >= minTotalGainPct) {
            // 沿 MA5 爬升：close > MA5 的占比
            let aboveMA5 = 0, count = 0
            for (let i = upStartIdx; i <= lastIdx; i++) {
                if (ma5Arr[i] == null) continue
                count++
                if (closes[i] > ma5Arr[i]) aboveMA5++
            }
            const compliance = count > 0 ? aboveMA5 / count : 0
            // MA20 趋势向上（首尾比较）
            const ma20Start = ma20Arr[upStartIdx], ma20End = ma20Arr[lastIdx]
            if (compliance >= ma5CompliancePct && ma20End > ma20Start) {
                return {
                    direction: 'up',
                    startIdx: upStartIdx, endIdx: lastIdx,
                    startTime: klines[upStartIdx].time,
                    endTime: klines[lastIdx].time,
                    startPrice, endPrice,
                    gainPct: gainPct * 100,
                    ma5CompliancePct: compliance * 100,
                    barCount: upSpan + 1,
                }
            }
        }
    }

    // === 稳健主跌：找最近一次"有效突破 MA20 向上"的 bar，从其后开始算 ===
    let downStartIdx = -1
    for (let i = lastIdx; i >= 1; i--) {
        if (ma20Arr[i] == null) break
        if (closes[i] > ma20Arr[i] * (1 + ma20BreakDeepPct)) {
            downStartIdx = i + 1
            break
        }
    }
    if (downStartIdx < 0) downStartIdx = 1

    const downSpan = lastIdx - downStartIdx
    if (downSpan >= minBars) {
        const startPrice = +klines[downStartIdx].close
        const endPrice   = +klines[lastIdx].close
        const dropPct    = startPrice > 0 ? (startPrice - endPrice) / startPrice : 0
        if (dropPct >= minTotalGainPct) {
            let belowMA5 = 0, count = 0
            for (let i = downStartIdx; i <= lastIdx; i++) {
                if (ma5Arr[i] == null) continue
                count++
                if (closes[i] < ma5Arr[i]) belowMA5++
            }
            const compliance = count > 0 ? belowMA5 / count : 0
            const ma20Start = ma20Arr[downStartIdx], ma20End = ma20Arr[lastIdx]
            if (compliance >= ma5CompliancePct && ma20End < ma20Start) {
                return {
                    direction: 'down',
                    startIdx: downStartIdx, endIdx: lastIdx,
                    startTime: klines[downStartIdx].time,
                    endTime: klines[lastIdx].time,
                    startPrice, endPrice,
                    gainPct: dropPct * 100,
                    ma5CompliancePct: compliance * 100,
                    barCount: downSpan + 1,
                }
            }
        }
    }
    return null
}

// ---------------- 主升浪 / 主跌浪 启动识别（横盘后突破，最强信号）----------------
/**
 * 识别"长期横盘 → 趋势反转启动"这类稀缺形态。条件：
 *   ① 找到一段长横盘（≥30 根 K 线、振幅 ≤10%）
 *   ② 横盘后向上突破上沿（主升启动）或向下跌破下沿（主跌启动）
 *   ③ 突破后的运动有显著幅度（≥8%）
 *   ④ 突破发生在最近 N 根之内（默认 30），且当前价仍在新趋势中
 *
 * @returns [{ direction: 'up'|'down', consolidationStartTime, consolidationEndTime,
 *            consolidationUpper, consolidationLower, consolidationBarCount,
 *            breakoutIdx, breakoutTime, breakoutPrice, rallyPct, barsAgo }]
 */
export function detectMainRallyStart(klines, opts = {}) {
    const {
        consolidationMinBars = 30,
        consolidationMaxRangePct = 0.15,  // 0.10 → 0.15：更宽容
        rallyMinPctAfter = 0.08,
        freshWithinBars = 30,
        historicalWithinBars = 150,
        closeInRangeRatio = 0.80,     // 至少 80% 收盘价在中位数 ±15% 内（容忍少量插针）
    } = opts
    if (!klines || klines.length < consolidationMinBars + 5) return []
    const n = klines.length
    const lastIdx = n - 1
    const lastClose = +klines[lastIdx].close
    const out = []

    // 宽容横盘检测：基于收盘价中位数 + 占比，容忍单根插针/跳空
    // 步长 = 5 根（性能优化，不每根都试），找尽可能长的横盘段
    const boxes = []
    const startScan = Math.max(0, n - 500)
    let i = startScan
    while (i <= n - consolidationMinBars) {
        let bestEnd = -1, bestUpper = 0, bestLower = 0
        for (let e = i + consolidationMinBars; e < n && e - i < 200; e += 5) {
            const closes = []
            for (let k = i; k <= e; k++) closes.push(+klines[k].close)
            const sorted = [...closes].sort((a, b) => a - b)
            const median = sorted[Math.floor(sorted.length / 2)]
            if (median <= 0) break
            let inRange = 0
            for (const c of closes) {
                if (Math.abs(c - median) / median <= consolidationMaxRangePct) inRange++
            }
            if (inRange / closes.length >= closeInRangeRatio) {
                bestEnd = e
                // upper/lower 用 95/5 百分位忽略极端插针
                const highs = []
                const lows  = []
                for (let k = i; k <= e; k++) { highs.push(+klines[k].high); lows.push(+klines[k].low) }
                highs.sort((a, b) => a - b)
                lows.sort((a, b) => a - b)
                bestUpper = highs[Math.floor(highs.length * 0.95)]
                bestLower = lows [Math.floor(lows.length  * 0.05)]
            } else if (bestEnd > 0) {
                break  // 已找到合格段，当前 e 失格 → 停止扩展，保留最长有效
            }
        }
        if (bestEnd > 0) {
            boxes.push({
                startIdx: i, endIdx: bestEnd,
                startTime: klines[i].time, endTime: klines[bestEnd].time,
                upper: bestUpper, lower: bestLower,
            })
            i = bestEnd + 1
        } else {
            i++
        }
    }

    for (const box of boxes) {
        if (box.endIdx >= lastIdx) continue

        // 找突破点（向上/向下，取先发生的）
        let upBreak = null, downBreak = null
        for (let i = box.endIdx + 1; i <= lastIdx; i++) {
            const close = +klines[i].close
            if (upBreak == null && close > box.upper) upBreak = i
            if (downBreak == null && close < box.lower) downBreak = i
            if (upBreak != null || downBreak != null) break
        }

        const buildEntry = (direction, breakIdx, refLevel, peakOrTrough) => {
            const isUp = direction === 'up'
            const pct = isUp ? (peakOrTrough - refLevel) / refLevel
                             : (refLevel - peakOrTrough) / refLevel
            if (pct < rallyMinPctAfter) return null
            const barsAgo = lastIdx - breakIdx
            const stillInTrend = isUp ? lastClose >= box.upper * 0.97
                                      : lastClose <= box.lower * 1.03
            const isFresh = barsAgo <= freshWithinBars && stillInTrend
            // 交易计划（基于横盘高度做 Fib 1.618/2.618 目标 + 反向 ×0.97 止损）
            const breakoutPrice = +klines[breakIdx].close
            const boxHeight = box.upper - box.lower
            let plan = null
            if (boxHeight > 0) {
                if (isUp) {
                    const stopLoss = box.lower * 0.97
                    const target1  = breakoutPrice + boxHeight * 1.618
                    const target2  = breakoutPrice + boxHeight * 2.618
                    const risk     = breakoutPrice - stopLoss
                    const reward1  = target1 - breakoutPrice
                    plan = {
                        entryPrice: +breakoutPrice.toFixed(2),
                        stopLoss:   +stopLoss.toFixed(2),
                        stopLossPct: ((stopLoss - breakoutPrice) / breakoutPrice * 100),
                        target1:    +target1.toFixed(2),
                        target1Pct: ((target1 - breakoutPrice) / breakoutPrice * 100),
                        target2:    +target2.toFixed(2),
                        target2Pct: ((target2 - breakoutPrice) / breakoutPrice * 100),
                        riskRewardRatio: risk > 0 ? +(reward1 / risk).toFixed(1) : null,
                    }
                } else {
                    const stopLoss = box.upper * 1.03
                    const target1  = breakoutPrice - boxHeight * 1.618
                    const target2  = breakoutPrice - boxHeight * 2.618
                    const risk     = stopLoss - breakoutPrice
                    const reward1  = breakoutPrice - target1
                    plan = {
                        entryPrice: +breakoutPrice.toFixed(2),
                        stopLoss:   +stopLoss.toFixed(2),
                        stopLossPct: ((stopLoss - breakoutPrice) / breakoutPrice * 100),
                        target1:    +target1.toFixed(2),
                        target1Pct: ((target1 - breakoutPrice) / breakoutPrice * 100),
                        target2:    +target2.toFixed(2),
                        target2Pct: ((target2 - breakoutPrice) / breakoutPrice * 100),
                        riskRewardRatio: risk > 0 ? +(reward1 / risk).toFixed(1) : null,
                    }
                }
            }
            return {
                direction, isFresh,
                consolidationStartIdx: box.startIdx,
                consolidationEndIdx:   box.endIdx,
                consolidationStartTime: box.startTime,
                consolidationEndTime:   box.endTime,
                consolidationUpper:    box.upper,
                consolidationLower:    box.lower,
                consolidationBarCount: box.endIdx - box.startIdx + 1,
                breakoutIdx:   breakIdx,
                breakoutTime:  klines[breakIdx].time,
                breakoutPrice,
                rallyPct:      pct * 100,
                barsAgo,
                plan,
            }
        }

        if (upBreak != null) {
            let peak = 0
            for (let i = upBreak; i <= lastIdx; i++) {
                const h = +klines[i].high
                if (h > peak) peak = h
            }
            const e = buildEntry('up', upBreak, box.upper, peak)
            if (e) out.push(e)
        }
        if (downBreak != null) {
            let trough = Infinity
            for (let i = downBreak; i <= lastIdx; i++) {
                const l = +klines[i].low
                if (l < trough) trough = l
            }
            const e = buildEntry('down', downBreak, box.lower, trough)
            if (e) out.push(e)
        }
    }
    // 时间倒序 + 过滤太老的历史事件
    return out
        .filter(e => e.barsAgo <= historicalWithinBars)
        .sort((a, b) => b.breakoutIdx - a.breakoutIdx)
}

// ---------------- 主升浪综合评分（0-100，5 维度加权）----------------
/**
 * 对每根 K 线计算"主升浪概率"评分。基于 5 个维度（共最高 ~115 分，截断到 100）：
 *   - 形态 (0-25)：突破创高 + 大阳线 + 实体饱满
 *   - 量能 (0-25)：量比放大 + 量价齐升 - 缩量涨警告
 *   - 均线 (0-25)：多头排列 + 金叉 + 发散
 *   - MACD (0-25)：零轴上 + 金叉 + 红柱放大
 *   - 共振 (0-15)：当日价格附近的多源共振区
 *
 * @param klines K 线数组
 * @param ctx    可选的预计算上下文 { ma5Arr, ma10Arr, ma20Arr, difArr, deaArr, histArr, resonancesByIdx }
 *               未提供则内部计算（每次 hover 重算性能差，建议预算好传入）
 * @returns Map: idx → { score, level, reasons }
 */
export function computeMainWaveScores(klines, ctx = {}) {
    if (!klines || klines.length < 25) return new Map()
    const closes = klines.map(k => +k.close)
    const ma5Arr  = ctx.ma5Arr  || ma(closes, 5)
    const ma10Arr = ctx.ma10Arr || ma(closes, 10)
    const ma20Arr = ctx.ma20Arr || ma(closes, 20)
    const macdRes = (ctx.difArr && ctx.deaArr && ctx.histArr)
        ? { dif: ctx.difArr, dea: ctx.deaArr, hist: ctx.histArr }
        : macd(closes)
    const { dif: difArr, dea: deaArr, hist: histArr } = macdRes
    const resByIdx = ctx.resonancesByIdx || null

    const out = new Map()
    for (let i = 25; i < klines.length; i++) {
        const k = klines[i]
        const open = +k.open, close = +k.close, high = +k.high, low = +k.low
        const prevClose = +klines[i - 1].close
        if (!(prevClose > 0)) continue
        const change = (close - prevClose) / prevClose
        const reasons = []
        let score = 0

        // === 形态 (0-25) ===
        let recentHigh = 0
        for (let j = Math.max(0, i - 20); j < i; j++) {
            if (+klines[j].high > recentHigh) recentHigh = +klines[j].high
        }
        if (close > recentHigh * 1.01) { score += 10; reasons.push('突破创高') }
        else if (close > recentHigh * 0.99) score += 3

        if (change > 0.05) { score += 8; reasons.push(`大阳${(change * 100).toFixed(1)}%`) }
        else if (change > 0.03) score += 4

        const range = high - low, body = Math.abs(close - open)
        if (range > 0 && body / range > 0.7) { score += 7; reasons.push('实体饱满') }

        // === 量能 (0-25) ===
        const vr = volumeRatio(klines, i, 5)
        if (vr != null) {
            if (vr > 2.5) { score += 15; reasons.push(`巨量${vr.toFixed(1)}×`) }
            else if (vr > 1.8) { score += 10; reasons.push(`放量${vr.toFixed(1)}×`) }
            else if (vr > 1.2) score += 5

            if (change > 0 && vr > 1.5) { score += 5; reasons.push('量价齐升') }
            else if (change > 0.03 && vr < 0.8) { score -= 8; reasons.push('缩量涨⚠') }
        }

        // === 均线 (0-25) ===
        const m5 = ma5Arr[i], m10 = ma10Arr[i], m20 = ma20Arr[i]
        if (m5 != null && m10 != null && m20 != null) {
            if (m5 > m10 && m10 > m20) { score += 10; reasons.push('多头排列') }
            else if (close > m5 && m5 > m10) score += 5
            const pm5 = ma5Arr[i - 1], pm10 = ma10Arr[i - 1]
            if (pm5 != null && pm10 != null && m5 > m10 && pm5 <= pm10) {
                score += 10; reasons.push('均线金叉')
            }
        }

        // === MACD (0-25) ===
        const dif = difArr[i], dea = deaArr[i]
        if (dif != null && dea != null) {
            if (dif > 0 && dea > 0) { score += 8; reasons.push('MACD零轴上') }
            const pDif = difArr[i - 1], pDea = deaArr[i - 1]
            if (pDif != null && pDea != null && dif > dea && pDif <= pDea && dif > 0) {
                score += 12; reasons.push('零轴上金叉')
            }
            const h = histArr[i], hp = histArr[i - 1]
            if (h != null && hp != null && h > hp && h > 0) {
                score += 5; reasons.push('红柱放大')
            }
        }

        // === 共振 (0-15) ===
        if (resByIdx) {
            const r = resByIdx.get(i)
            if (r) { score += Math.min(15, r.count * 5); reasons.push(`共振×${r.count}`) }
        }

        score = Math.max(0, Math.min(100, score))
        let level
        if (score >= 70) level = '强买'
        else if (score >= 50) level = '关注'
        else if (score >= 30) level = '中性'
        else level = '观望'

        out.set(i, { score, level, reasons, vr })
    }
    return out
}

// ---------------- 潜伏分（识别"何时埋伏"，跟主升评分（启动分）互补）----------------
/**
 * 潜伏分高 = 适合潜伏布局，跟主升评分（确认型滞后指标）反向。
 * 5 维度（共最高 ~100，截断到 100）：
 *   - 位置 (0-30)：紧贴 MA20 + 共振支撑区 + 趋势完好（在 MA60 上）
 *   - 量能 (0-25)：缩量、连续缩量；放量爆发 → 扣分
 *   - 超卖反转 (0-25)：KDJ J 超卖 / J 反转向上 / K < 30
 *   - 动能反转 (0-15)：MACD 零轴下绿柱转红 / 绿柱缩短
 *   - 形态加分 (0-15)：下影长、实体收缩
 *   - 扣分项：涨过 MA20 5% / 破 MA60 / 量比 > 2
 */
export function computeAccumulationScores(klines, ctx = {}) {
    if (!klines || klines.length < 25) return new Map()
    const closes = klines.map(k => +k.close)
    const ma20Arr = ctx.ma20Arr || ma(closes, 20)
    const ma60Arr = ctx.ma60Arr || ma(closes, 60)
    const macdRes = (ctx.difArr && ctx.histArr)
        ? { dif: ctx.difArr, hist: ctx.histArr }
        : macd(closes)
    const kdjRes = (ctx.kArr && ctx.jArr)
        ? { k: ctx.kArr, j: ctx.jArr }
        : kdj(klines)
    const { hist: histArr } = macdRes
    const { k: kArr, j: jArr } = kdjRes
    const resByIdx = ctx.resonancesByIdx || null

    const out = new Map()
    for (let i = 25; i < klines.length; i++) {
        const cb = klines[i]
        const open = +cb.open, close = +cb.close, high = +cb.high, low = +cb.low
        const reasons = []
        let score = 0

        // === 横盘特征 (0-30) —— 潜伏的核心信号 ===
        // 近 20 根 K 线波动率（核心：跌不下去也涨不上去 = 潜伏）
        let win20High = 0, win20Low = Infinity
        for (let j = Math.max(0, i - 19); i >= 0 && j <= i; j++) {
            const h = +klines[j].high, l = +klines[j].low
            if (h > win20High) win20High = h
            if (l < win20Low)  win20Low = l
        }
        const vol20 = win20Low > 0 ? (win20High - win20Low) / win20Low : 1
        if (vol20 <= 0.10)      { score += 20; reasons.push('窄幅横盘') }
        else if (vol20 <= 0.15) { score += 12; reasons.push('震荡中') }
        else if (vol20 <= 0.20) { score += 6 }

        // 近 30 根没创新低（不在下跌通道里）
        const lookback30 = Math.min(30, i)
        let madeNewLow = false
        const recentLow = +klines[i].low
        const earlyLowsStart = Math.max(0, i - lookback30)
        for (let j = earlyLowsStart; j < i; j++) {
            if (+klines[j].low > recentLow * 1.02) {  // 当前低 < 早期低 = 在创新低
                madeNewLow = true; break
            }
        }
        if (!madeNewLow && lookback30 >= 20) { score += 10; reasons.push('未创新低') }

        // === 量能维度 (0-25) ===
        const vr = volumeRatio(klines, i, 5)
        if (vr != null) {
            if (vr < 0.7)      { score += 12; reasons.push(`缩量${vr.toFixed(1)}×`) }
            else if (vr < 0.9) { score += 8 }
            else if (vr < 1.1) { score += 4 }
        }
        // 5 日均量 vs 20 日均量（更宏观的缩量趋势）
        let sum5 = 0, n5 = 0, sum20 = 0, n20 = 0
        for (let j = Math.max(0, i - 4); j <= i; j++) {
            const v = +klines[j].vol; if (v > 0) { sum5 += v; n5++ }
        }
        for (let j = Math.max(0, i - 19); j <= i; j++) {
            const v = +klines[j].vol; if (v > 0) { sum20 += v; n20++ }
        }
        if (n5 >= 3 && n20 >= 10) {
            const ratio = (sum5 / n5) / (sum20 / n20)
            if (ratio < 0.7)      { score += 13; reasons.push('5日均量持续萎缩') }
            else if (ratio < 0.85){ score += 8;  reasons.push('5日均量小缩') }
        }

        // === 位置健康 (0-20) ===
        const m20 = ma20Arr[i], m60 = ma60Arr[i]
        if (m20 != null) {
            const dist = (close - m20) / m20
            if (Math.abs(dist) <= 0.05) { score += 10; reasons.push('紧贴MA20') }
            else if (Math.abs(dist) <= 0.10) { score += 5 }
        }
        if (m60 != null && close > m60) { score += 5; reasons.push('守住MA60') }
        if (resByIdx) {
            const r = resByIdx.get(i)
            if (r && r.price <= close * 1.01) {
                score += Math.min(10, r.count * 4)
                reasons.push(`支撑共振×${r.count}`)
            }
        }

        // === 反转征兆 (0-15) ===
        const j = jArr[i], k = kArr[i]
        if (j != null && j < 30) { score += 5; reasons.push('J<30') }
        const jPrev = jArr[i - 1]
        if (j != null && jPrev != null && k != null && k < 50 && j > jPrev) {
            score += 8; reasons.push('KDJ反转↑')
        }
        const histV = histArr[i], histP = histArr[i - 1]
        if (histV != null && histP != null) {
            if (histV >= 0 && histP < 0) { score += 7; reasons.push('MACD转红') }
            else if (histV < 0 && histV > histP) { score += 3; reasons.push('绿柱缩') }
        }

        // === 形态加分 (0-10) ===
        const range = high - low, body = Math.abs(close - open)
        const lowerShadow = Math.min(open, close) - low
        if (range > 0 && body > 0) {
            if (lowerShadow > body * 2 && body / range < 0.3) {
                score += 5; reasons.push('下影长')
            }
            if (body / range < 0.3 && open > 0 && range / open < 0.025) {
                score += 5; reasons.push('实体收缩')
            }
        }

        // === 扣分项（仅明显已脱离潜伏期）===
        if (m20 != null && close > m20 * 1.08) { score -= 8;  reasons.push('涨过MA20⚠') }
        if (m60 != null && close < m60 * 0.95) { score -= 10; reasons.push('破MA60⚠') }
        if (vr != null && vr > 2)              { score -= 12; reasons.push('已爆发⚠') }

        score = Math.max(0, Math.min(100, score))
        let level
        if (score >= 70) level = '深度潜伏'
        else if (score >= 55) level = '潜伏中'
        else if (score >= 40) level = '可关注'
        else if (score >= 25) level = '观望'
        else level = '不适合'

        out.set(i, { score, level, reasons, vr })
    }
    return out
}

// ---------------- 缺口（跳空）识别 ----------------
/**
 * 自动识别 K 线之间的跳空缺口：
 *   - 向上缺口：当根 low > 前根 high
 *   - 向下缺口：当根 high < 前根 low
 *
 * 同时判定是否被回补（subsequent bar 价格穿越缺口区间）。
 *
 * @param klines
 * @param opts:
 *   minSizePct  最小缺口幅度（默认 0.005 = 0.5%；避免过小的缺口）
 *   recencyBars 只扫最近 N 根
 * @returns [{ direction, idx, time, prevTime, upper, lower, gapPct, filled, filledIdx, filledTime }]
 */
export function detectGaps(klines, opts = {}) {
    const {
        minSizePct = 0.005,
        recencyBars = 250,
    } = opts
    if (!klines || klines.length < 2) return []
    const n = klines.length
    const startIdx = Math.max(1, n - recencyBars)
    const out = []

    for (let i = startIdx; i < n; i++) {
        const prev = klines[i - 1], cur = klines[i]
        const prevHigh = +prev.high, prevLow = +prev.low
        const curHigh = +cur.high, curLow = +cur.low

        // 向上缺口
        if (curLow > prevHigh) {
            const gapPct = prevHigh > 0 ? (curLow - prevHigh) / prevHigh : 0
            if (gapPct >= minSizePct) {
                let filledIdx = null
                for (let j = i + 1; j < n; j++) {
                    if (+klines[j].low <= prevHigh) { filledIdx = j; break }
                }
                out.push({
                    direction: 'up',
                    idx: i, time: cur.time, prevTime: prev.time,
                    upper: curLow,    // 缺口顶（当根 low）
                    lower: prevHigh,  // 缺口底（前根 high）
                    gapPct: gapPct * 100,
                    filled: filledIdx !== null,
                    filledIdx,
                    filledTime: filledIdx !== null ? klines[filledIdx].time : null,
                })
            }
        }

        // 向下缺口
        if (curHigh < prevLow) {
            const gapPct = prevLow > 0 ? (prevLow - curHigh) / prevLow : 0
            if (gapPct >= minSizePct) {
                let filledIdx = null
                for (let j = i + 1; j < n; j++) {
                    if (+klines[j].high >= prevLow) { filledIdx = j; break }
                }
                out.push({
                    direction: 'down',
                    idx: i, time: cur.time, prevTime: prev.time,
                    upper: prevLow,   // 缺口顶（前根 low）
                    lower: curHigh,   // 缺口底（当根 high）
                    gapPct: gapPct * 100,
                    filled: filledIdx !== null,
                    filledIdx,
                    filledTime: filledIdx !== null ? klines[filledIdx].time : null,
                })
            }
        }
    }
    return out
}

// ---------------- 找发车信号 S/A/B/C 强度评级 ----------------
/**
 * 给一只 fresh Stage 3 的票打分并定级（S/A/B/C）。
 *
 * 评分四维度（满分 100）：
 *   - 形态 (0-30)：距黄金买点远近 + Stage 3 实体饱满度
 *   - 量能 (0-25)：突破当日量比合理 + 预突破温和放量铺垫
 *   - 均线 (0-25)：MA60 加分项；MA5>10>20>60 强势多头给满分
 *   - 大盘 (0-20)：大盘环境 score 线性映射
 *
 * 分级：
 *   - ≥ 85: S 级（黄金买点附近 + 量能 + 均线 + 大盘 多重共振）
 *   - 70-84: A 级（主力意图明显，可重仓）
 *   - 55-69: B 级（信号成立但有瑕疵）
 *   - < 55:  C 级（观望，参考即可）
 *
 * Phase 3 双锚点 formScore：
 *   全市场回测显示评级倒挂（A 46% < B 51% < C 53%）。根因：旧版 formScore 只奖励
 *   "距黄金买点近"，但黄金买点近 = 突破后股价没飞起来 = 弱势票。真正强势的突破
 *   不会回踩到黄金买点，反而是从突破价 +0~5% 一路上涨。
 *
 *   新版同时计算两个锚点的位置分，取较高者 — 体现"任一好买点都满意"：
 *     form_golden: 距回踩买点（goldenBuyPrice = min(试盘 K 中点, 蓄势上沿)）的偏离
 *     form_chase:  距追涨买点（chasePrice = breakoutPrice × 1.01）的偏离
 *
 *   现价从 klines 末尾取（live 调用 = 当下；回测调用 = klines 已截断到 s3Idx+1
 *   即突破当天）。这样不再需要 caller 传 distancePct。
 *
 * @param klines  日 K
 * @param event   detectThreeStageLaunch 返回的单条事件（必须 currentStage === 3）
 * @param marketEnv  computeMarketEnvScore 返回值（可选，没有就给中性 50）
 * @param weeklyConfirmed  Phase 4：可选 boolean，true=周线趋势确认，false=未确认 → grade 降一档
 *                         全市场回测：周共振票胜率 51% vs 无共振 43%（+8pp 显著差异）
 * @returns { grade: 'S'|'A'|'B'|'C', score, breakdown, reasons, weeklyConfirmed, gradeRaw }
 */
export function gradeFreshSignal(klines, event, marketEnv, weeklyConfirmed = null) {
    if (!klines || !event || event.currentStage !== 3) return null
    const closes = klines.map(k => +k.close)
    const ma5Arr  = ma(closes, 5)
    const ma10Arr = ma(closes, 10)
    const ma20Arr = ma(closes, 20)
    const ma60Arr = ma(closes, 60)
    const lastIdx = closes.length - 1
    const reasons = []

    // === Phase 3 v2 评级框架（5 维 100 分）===
    // 全市场回测发现旧版评级与胜率倒挂（S 级表现最差）。根因：旧版多个维度都奖励
    // "突破当天 strong"（量能爆 / 实体大 / MA 强势），而"突破当天 strong"= 当天涨过头
    // = 后续 mean reversion → 评级越高反而 30 天后跌得越多。
    //
    // 新版重分配：降权"当天爆发"维度，加重"持续累积"维度（预突破吸筹 / 长期均线 / 蓄势紧度）。
    //
    //   形态分 (0-25): 位置 20 + 实体 5         （位置降 2、实体降 3）
    //   量能分 (0-25): 突破当天 10 + 预突破 15  （当天爆发降权，累积升权）
    //   均线分 (0-20): 短期 10 + 长期 10        （短期降权，长期升权）
    //   蓄势分 (0-10): 时长越长越紧 = 越好  ★ 新增
    //   大盘分 (0-20): 不变
    //   ─────────────────────────────────────
    //   合计 100

    // === 形态分 (0-25) ===
    // 位置（0-20）：健康介入区 = [回踩买点, 追涨买点 = 突破价×1.01]
    let formScore = 0
    const lastClose = closes[lastIdx]
    const goldenBuy = event.goldenBuyPrice ?? null
    const chasePrice = event.breakoutPrice != null ? event.breakoutPrice * 1.01 : null

    let positionScore = 0
    let positionLabel = null
    if (goldenBuy != null && chasePrice != null && goldenBuy < chasePrice) {
        if (lastClose >= goldenBuy && lastClose <= chasePrice) {
            positionScore = 20; positionLabel = '健康介入区'
        } else if (lastClose < goldenBuy) {
            const drop = (goldenBuy - lastClose) / goldenBuy
            if      (drop <= 0.03) { positionScore = 12; positionLabel = '浅跌破回踩' }
            else if (drop <= 0.10) { positionScore = 5;  positionLabel = '深跌破回踩' }
            else                   { positionScore = 1;  positionLabel = '远跌破回踩' }
        } else {
            const overChase = (lastClose - chasePrice) / chasePrice
            if      (overChase <= 0.05) { positionScore = 14; positionLabel = '小幅追涨' }
            else if (overChase <= 0.10) { positionScore = 7;  positionLabel = '追涨偏高' }
            else if (overChase <= 0.20) { positionScore = 2;  positionLabel = '已追高' }
            else                        { positionScore = 0;  positionLabel = '远超追涨(过热)' }
        }
    }
    formScore += positionScore
    if (positionLabel) reasons.push(positionLabel)

    // 实体饱满度（0-5，原 8 → 5 降权）
    if (event.s3Idx >= 0) {
        const k = klines[event.s3Idx]
        const range = +k.high - +k.low
        const body  = Math.abs(+k.close - +k.open)
        if (range > 0) {
            const ratio = body / range
            if      (ratio >= 0.75) { formScore += 5; reasons.push('实体饱满') }
            else if (ratio >= 0.55) { formScore += 3 }
            else                    { formScore += 1 }
        }
    }
    formScore = Math.min(25, formScore)

    // === 量能分 (0-25) — 突破当天降权 + 预突破累积升权 ===
    let volScore = 0
    // 突破当天量比（0-10，原 15 → 10 降权）
    if (event.s3Idx >= 0) {
        const vr = volumeRatio(klines, event.s3Idx, 5)
        if (vr != null) {
            if      (vr >= 1.5 && vr <= 2.5) { volScore += 10; reasons.push(`突破量比 ${vr.toFixed(1)}× 健康`) }
            else if (vr > 2.5  && vr <= 3.5) { volScore += 5;  reasons.push(`突破量比 ${vr.toFixed(1)}× 偏高`) }
            else if (vr >= 1.3)              { volScore += 7 }
        }
    }
    // 预突破温和放量铺垫（0-15，原 10 → 15 升权）— 主力暗中吸筹的真信号
    const preBars = event.preBreakoutVolBars || 0
    if      (preBars >= 3) { volScore += 15; reasons.push(`突破前 ${preBars} 根温和放量(主力吸筹)`) }
    else if (preBars >= 2) { volScore += 10; reasons.push(`突破前 ${preBars} 根温和放量`) }
    else if (preBars >= 1) { volScore += 5 }
    volScore = Math.min(25, volScore)

    // === 均线分 (0-20) — 短期降权 + 长期升权 ===
    let maScore = 0
    const m5  = ma5Arr [lastIdx]
    const m10 = ma10Arr[lastIdx]
    const m20 = ma20Arr[lastIdx]
    const m60 = ma60Arr[lastIdx]
    const close = closes[lastIdx]
    // 短期 (0-10)
    if (m5 != null && m20 != null && close > m20 && m5 > m20) {
        maScore += 5
        if (m10 != null && m5 > m10 && m10 > m20) { maScore += 5; reasons.push('MA5>10>20 标准多头') }
    }
    // 长期 (0-10) — MA60 作为中长线趋势锚（升权，原 7 → 10）
    if (m60 != null && m20 != null && m20 > m60) {
        maScore += 5; reasons.push('MA20 > MA60')
        if (m5 != null && m10 != null && m5 > m10 && m10 > m20) {
            maScore += 5
            // 升级标签
            const idx = reasons.indexOf('MA5>10>20 标准多头')
            if (idx >= 0) reasons[idx] = 'MA5>10>20>60 强势多头'
            else reasons.push('MA20>MA60 长期多头')
        }
    }
    maScore = Math.min(20, maScore)

    // === 蓄势分 (0-10) ★ 新增 — 持续性信号：蓄势越长越紧 = 突破后能量越足 ===
    let consolidationScore = 0
    if (event.s1StartIdx != null && event.s1EndIdx != null) {
        const bars = event.s1EndIdx - event.s1StartIdx + 1
        if      (bars >= 80) { consolidationScore += 6; reasons.push(`蓄势 ${bars} 根(超长)`) }
        else if (bars >= 50) { consolidationScore += 4; reasons.push(`蓄势 ${bars} 根(长)`) }
        else if (bars >= 30) { consolidationScore += 2 }

        // 振幅紧度（dynMaxRange 越小越紧，奖励紧蓄势）
        if (event.dynMaxRange != null) {
            if      (event.dynMaxRange <= 0.10) { consolidationScore += 4; reasons.push('低波动紧蓄势') }
            else if (event.dynMaxRange <= 0.15) { consolidationScore += 2 }
        }
    }
    consolidationScore = Math.min(10, consolidationScore)

    // === 大盘分 (0-20) ===
    const envScore01 = marketEnv && typeof marketEnv.score === 'number'
        ? marketEnv.score / 100
        : 0.5
    const envScore = Math.round(envScore01 * 20)
    if (marketEnv) reasons.push(`大盘${marketEnv.level}`)

    // === 总分 + 评级 ===
    const score = formScore + volScore + maScore + consolidationScore + envScore
    let grade
    if      (score >= 85) grade = 'S'
    else if (score >= 70) grade = 'A'
    else if (score >= 55) grade = 'B'
    else                  grade = 'C'

    // Phase 4：weeklyConfirmed 作为独立维度输出，不再绑进 grade
    // 实证：降级反而让 grade 信号变扁平（B/C 接近），grade + weeklyConfirmed 独立排序更清晰
    if      (weeklyConfirmed === true)  reasons.push('✓ 周线趋势确认')
    else if (weeklyConfirmed === false) reasons.push('✗ 周线未确认')

    return {
        grade,
        score,
        weeklyConfirmed,               // null / true / false（独立维度）
        breakdown: {
            form: formScore,           // 0-25
            vol: volScore,             // 0-25
            ma: maScore,               // 0-20
            consolidation: consolidationScore,  // 0-10  ★ Phase 3 v2 新增
            env: envScore,             // 0-20
        },
        reasons,
    }
}

// ---------------- 大盘环境评分（沪深300 / 上证指数 等指数日K）----------------
/**
 * 基于指数 K 线给"大盘环境"打分（0-100），用于给"找发车"信号置信度做折损。
 * 大盘破位时即使个股形态完美，胜率也会大幅打折，所以信号要降级。
 *
 * 评分维度：
 *   - close vs MA20         ±15 分
 *   - close vs MA60         ±20 分
 *   - MA20 近 5 根斜率       ±10 分
 *   - MA60 近 10 根斜率      ±5  分
 *
 * @returns { score, level, reasons, close, ma20, ma60 } 或 null（数据不足）
 */
export function computeMarketEnvScore(klines) {
    if (!klines || klines.length < 60) return null
    const closes = klines.map(k => +k.close)
    const ma20Arr = ma(closes, 20)
    const ma60Arr = ma(closes, 60)
    const last = closes.length - 1
    const close = closes[last]
    const m20 = ma20Arr[last]
    const m60 = ma60Arr[last]
    if (m20 == null || m60 == null) return null

    let score = 50        // 中性基线
    const reasons = []

    // close vs MA20 (±15)
    if (close > m20 * 1.02)      { score += 15; reasons.push('指数收盘 > MA20') }
    else if (close < m20 * 0.98) { score -= 15; reasons.push('指数收盘 < MA20') }

    // close vs MA60 (±20) — 中长期方向，权重高于 MA20
    if (close > m60 * 1.02)      { score += 20; reasons.push('指数收盘 > MA60') }
    else if (close < m60 * 0.98) { score -= 20; reasons.push('指数收盘 < MA60') }

    // MA20 近 5 根斜率 (±10)
    const m20Prev5 = last >= 5 ? ma20Arr[last - 5] : null
    if (m20Prev5 != null) {
        if (m20 > m20Prev5 * 1.005)      { score += 10; reasons.push('MA20 上行') }
        else if (m20 < m20Prev5 * 0.995) { score -= 10; reasons.push('MA20 下行') }
    }

    // MA60 近 10 根斜率 (±5)
    const m60Prev10 = last >= 10 ? ma60Arr[last - 10] : null
    if (m60Prev10 != null) {
        if (m60 > m60Prev10 * 1.005)      { score += 5; reasons.push('MA60 上行') }
        else if (m60 < m60Prev10 * 0.995) { score -= 5; reasons.push('MA60 下行') }
    }

    score = Math.max(0, Math.min(100, score))

    let level
    if      (score >= 75) level = '强势'
    else if (score >= 55) level = '良好'
    else if (score >= 40) level = '震荡'
    else if (score >= 25) level = '弱势'
    else                  level = '破位'

    return { score, level, reasons, close, ma20: m20, ma60: m60 }
}

// ---------------- 🎯 三维启动（蓄势→试盘→突破 完整序列）----------------
/**
 * 三阶段三维度框架完整识别。比 detectMainRallyStart 严格得多 —— 必须按
 * 蓄势→试盘→突破时间顺序触发，每阶段满足 K 线 + MA + 量能多重条件。
 *
 * Stage 1 蓄势：长横盘（≥30 根）+ MA 粘合 + 地量 + 振幅收窄
 * Stage 2 试盘：长上影/下影 + ≥2.5 倍量
 * Stage 3 突破：放量阳线突破前高/箱顶 + MA 多头排列 + 量在合理区间
 *
 * @returns [{ s1Start/End, s1Upper/Lower, s2Idx, s3Idx, currentStage, isFresh, ... }]
 */
export function detectThreeStageLaunch(klines, opts = {}) {
    const {
        // Stage 1
        s1MinBars         = 20,      // 20-120 统一阈值
        s1MaxBars         = 120,     // 80 → 120（给"超长横盘后启动"机会，A 股不少票横半年才动）
        s1MaxRangePct     = 0.15,
        s1InRangeRatio    = 0.80,
        s1MaxMaSpreadPct  = 0.07,    // MA5/10/20 间距 < 7%
        s1ShrinkRatio     = 0.85,    // 后半振幅 / 前半振幅 < 0.85（轻度收窄即可）
        // 趋势位置过滤（防阴跌假蓄势）
        trendLookback     = 250,
        trendMinRatio     = 1.30,
        // Stage 2
        s2WithinBars      = 25,      // 15 → 25（蓄势结束到试盘可以更慢一点）
        s2VolRatioMin     = 2.5,
        s2ShadowRatio     = 1.5,
        // Stage 3
        s3WithinBars      = 30,      // 15 → 30（试盘后慢启动型常需 20-40 根才突破）
        s3VolRatioMin     = 1.3,
        s3VolRatioMax     = 3.5,
        s3BodyRatioMin    = 0.55,
        // 新鲜度
        freshWithinBars   = 30,
    } = opts

    if (!klines || klines.length < 100) return []
    const n = klines.length
    const closes = klines.map(k => +k.close)
    const ma5Arr  = ma(closes, 5)
    const ma10Arr = ma(closes, 10)
    const ma20Arr = ma(closes, 20)

    const events = []
    let i = Math.max(0, n - 400)

    while (i <= n - s1MinBars - 5) {
        // === 股性振幅探测（蓄势开始前的 60 根日均涨跌幅）===
        // 蓄势区本身波动小，不能在蓄势内测；用蓄势前的窗口反映"该股的天然股性"
        // 高股性 → 蓄势可允许更宽，低股性 → 蓄势必须更窄
        let dynMaxRange = s1MaxRangePct
        {
            const hvEnd   = Math.max(0, i - 1)
            const hvStart = Math.max(1, hvEnd - 60 + 1)
            let sumAbs = 0, cnt = 0
            for (let k = hvStart; k <= hvEnd; k++) {
                const prev = +klines[k - 1].close
                if (prev > 0) {
                    sumAbs += Math.abs(+klines[k].close - prev) / prev
                    cnt++
                }
            }
            if (cnt >= 30) {
                const avgDailyMove = sumAbs / cnt
                if      (avgDailyMove > 0.030) dynMaxRange = 0.20  // 高波动（题材/小盘）
                else if (avgDailyMove < 0.015) dynMaxRange = 0.10  // 低波动（蓝筹/大盘）
                else                           dynMaxRange = 0.15  // 中等
            }
        }

        // === Stage 1 蓄势检测：找尽可能长的合格段 ===
        let s1End = -1
        for (let e = i + s1MinBars; e < n && e - i <= s1MaxBars; e += 5) {
            // 收盘价中位数 ±dynMaxRange 占比 ≥ 80%
            const slice = closes.slice(i, e + 1)
            const sorted = [...slice].sort((a, b) => a - b)
            const median = sorted[Math.floor(sorted.length / 2)]
            if (!(median > 0)) break
            let inRange = 0
            for (const c of slice) {
                if (Math.abs(c - median) / median <= dynMaxRange) inRange++
            }
            if (inRange / slice.length < s1InRangeRatio) {
                if (s1End > 0) break
                continue
            }
            // 振幅收窄（后半 / 前半）
            const mid = Math.floor((i + e) / 2)
            let fMax = 0, fMin = Infinity, sMax = 0, sMin = Infinity
            for (let k = i; k < mid; k++) {
                const h = +klines[k].high, l = +klines[k].low
                if (h > fMax) fMax = h; if (l < fMin) fMin = l
            }
            for (let k = mid; k <= e; k++) {
                const h = +klines[k].high, l = +klines[k].low
                if (h > sMax) sMax = h; if (l < sMin) sMin = l
            }
            const fRange = fMin > 0 ? (fMax - fMin) / fMin : 0
            const sRange = sMin > 0 ? (sMax - sMin) / sMin : 0
            if (fRange <= 0 || sRange / fRange > s1ShrinkRatio) {
                if (s1End > 0) break
                continue
            }
            // MA 粘合（段末附近）
            const m5 = ma5Arr[e], m10 = ma10Arr[e], m20 = ma20Arr[e]
            if (m5 != null && m10 != null && m20 != null) {
                const maMax = Math.max(m5, m10, m20)
                const maMin = Math.min(m5, m10, m20)
                if ((maMax - maMin) / median > s1MaxMaSpreadPct) {
                    if (s1End > 0) break
                    continue
                }
            }
            s1End = e
        }
        if (s1End < 0) { i++; continue }

        // 蓄势上下沿（百分位过滤极端）
        const highs = [], lows = []
        for (let k = i; k <= s1End; k++) {
            highs.push(+klines[k].high); lows.push(+klines[k].low)
        }
        highs.sort((a, b) => a - b)
        lows.sort((a, b) => a - b)
        const s1Upper = highs[Math.floor(highs.length * 0.95)]
        const s1Lower = lows [Math.floor(lows.length  * 0.05)]

        // === 趋势位置过滤 ===
        // 真蓄势：前面有过一段上涨，蓄势区位置不会贴在历史最低
        // 假蓄势：长期阴跌后走平，蓄势区本身就是地板 — 大概率反弹乏力，要过滤
        // 检查：蓄势均价 ≥ 近 trendLookback 根最低价 × trendMinRatio
        {
            const lookbackStart = Math.max(0, s1End - (trendLookback - 1))
            let historicalLow = Infinity
            for (let k = lookbackStart; k <= s1End; k++) {
                const l = +klines[k].low
                if (l < historicalLow) historicalLow = l
            }
            const s1AvgPrice = (s1Upper + s1Lower) / 2
            if (historicalLow > 0 && s1AvgPrice < historicalLow * trendMinRatio) {
                i = s1End + 5
                continue
            }
        }

        // === Stage 2 试盘检测 ===
        let s2Idx = -1, s2Type = null
        for (let t = s1End + 1; t < n && t - s1End <= s2WithinBars; t++) {
            const k = klines[t]
            const open = +k.open, close = +k.close, high = +k.high, low = +k.low
            const body = Math.abs(close - open)
            const upper = high - Math.max(open, close)
            const lower = Math.min(open, close) - low
            const vr = volumeRatio(klines, t, 5)
            if (vr == null || body <= 0) continue
            // 长上影 + 大量（射击之星型试盘）
            if (upper > body * s2ShadowRatio && vr >= s2VolRatioMin) {
                s2Idx = t; s2Type = 'upperShadow'; break
            }
            // 长下影 + 温和量 + 收阳（金针探底型试盘）
            if (lower > body * s2ShadowRatio && vr >= 1.3 && close > open) {
                s2Idx = t; s2Type = 'lowerShadow'; break
            }
        }
        if (s2Idx < 0) { i = s1End + 5; continue }

        // === 试盘有效性确认 ===
        // 真试盘：主力试探后股价企稳；假试盘：试完就破位
        // 规则：试盘 K 之后 s2WithinBars 根内（或截止到当前），任何收盘 < s2Low × 0.99 → 整段作废
        const s2Low = +klines[s2Idx].low
        const s2ConfirmEnd = Math.min(n - 1, s2Idx + s2WithinBars)
        let s2Broken = false
        for (let t = s2Idx + 1; t <= s2ConfirmEnd; t++) {
            if (+klines[t].close < s2Low * 0.99) { s2Broken = true; break }
        }
        if (s2Broken) { i = s1End + 5; continue }

        // === Stage 3 突破检测 ===
        let s3Idx = -1
        const s2High = +klines[s2Idx].high
        const breakLevel = Math.max(s2High, s1Upper)
        for (let t = s2Idx + 1; t < n && t - s2Idx <= s3WithinBars; t++) {
            const k = klines[t]
            const open = +k.open, close = +k.close, high = +k.high, low = +k.low
            const body = Math.abs(close - open)
            const range = high - low
            const vr = volumeRatio(klines, t, 5)
            if (vr == null) continue
            // 收盘突破 + 量在合理区间 + 实体饱满 + MA 多头排列
            if (close > breakLevel
                && vr >= s3VolRatioMin && vr <= s3VolRatioMax
                && range > 0 && body / range >= s3BodyRatioMin
                && close > open) {
                // MA 排列检查：放宽为"close > MA20 且 MA5 > MA20"（不强求 MA5>MA10>MA20）
                // 长横盘后第一根突破，MA 还在缠绕，严格三层排列经常 miss
                const m5 = ma5Arr[t], m20 = ma20Arr[t]
                if (m5 != null && m20 != null && close > m20 && m5 > m20) {
                    s3Idx = t; break
                }
            }
        }

        // 构建 event（即使没 Stage 3 也保留：处于"试盘后等待启动"状态）
        const lastIdx = n - 1
        const currentStage = s3Idx >= 0 ? 3 : (s2Idx >= 0 ? 2 : 1)
        const barsAgoFromS3 = s3Idx >= 0 ? lastIdx - s3Idx : null
        const isFresh = currentStage === 3 && barsAgoFromS3 <= freshWithinBars

        // === 预突破信号（突破前 5 根温和放量计数）===
        // 量比 ∈ [1.3, 2.0]：主力悄悄进场的足迹（不是爆量诱多，是"试探性吸筹"）
        // 候选阶段（Stage 2）：预突破计数高 → 突破临近，提升候选权重
        // 发车阶段（Stage 3）：预突破计数高 → 突破有伏笔，是真启动而非偶然冲量
        const probeEndIdx = s3Idx >= 0 ? s3Idx - 1 : lastIdx
        const probeStartIdx = Math.max(s2Idx + 1, probeEndIdx - 4)
        let preBreakoutVolBars = 0
        for (let t = probeStartIdx; t <= probeEndIdx && t >= 0 && t <= lastIdx; t++) {
            const vr = volumeRatio(klines, t, 5)
            if (vr != null && vr >= 1.3 && vr <= 2.0) preBreakoutVolBars++
        }
        // —— 三档介入价位 + 止损价位 ——
        const s2K = s2Idx >= 0 ? klines[s2Idx] : null
        const s2Mid = s2K ? (+s2K.high + +s2K.low) / 2 : null  // 试盘 K 线半分位
        // 黄金买点 = 试盘 K 线半分位 与 蓄势上沿 取较低（更稳健的回踩位置）
        const goldenBuyPrice = s2Mid != null ? Math.min(s2Mid, s1Upper) : s1Upper
        const breakoutPrice  = s3Idx >= 0 ? +klines[s3Idx].close : null
        const stopLossPrice  = s1Lower * 0.97   // 蓄势下沿 × 0.97 = 关键支撑止损
        events.push({
            s1StartIdx:  i,
            s1EndIdx:    s1End,
            s1StartTime: klines[i].time,
            s1EndTime:   klines[s1End].time,
            s1Upper, s1Lower,
            s2Idx, s2Type,
            s2Low,
            s2Time:      s2K ? s2K.time : null,
            s2Price:     s2K ? +s2K.close : null,
            s3Idx,
            s3Time:      s3Idx >= 0 ? klines[s3Idx].time : null,
            s3Price:     s3Idx >= 0 ? +klines[s3Idx].close : null,
            currentStage,
            isFresh,
            barsAgoFromS3,
            // 介入价位
            goldenBuyPrice,
            breakoutPrice,
            // 止损价位
            stopLossPrice,
            // 预突破信号（0~5 根温和放量计数）
            preBreakoutVolBars,
            // 振幅自适应实际使用值（debug / UI 展示）
            dynMaxRange,
        })
        i = s1End + 5
    }
    return events
}

// ---------------- 🎯 二次买点检测（Phase 6 第三档买点）----------------
/**
 * 突破后第二次入场机会：回踩不破 + 反包阳线。
 *
 * 触发流程（最干净的一种形态，不含走四方 / 单阳不破 / 涨停回踩等口语形态）：
 *   1. Stage 3 突破 K 之后扫描 N 根
 *   2. 找第一根"回踩K"：close < 突破K收盘（出现回调）
 *      且 close ≥ goldenBuyPrice × 0.97（没跌破回踩位 = 形态没破）
 *   3. 回踩K 之后找"反包K"：阳线 + close > 前一根 close + 实体饱满 ≥ 0.5
 *   4. 新鲜度过滤：反包K必须在最近 freshWithinBars 根内（否则信号已过期，没参考意义）
 *
 * 实战意义：用户错过 Stage 3 fresh 窗口、票仍处于 rally 状态时，反包是第二次介入信号。
 *
 * @param klines  日 K 线
 * @param event   detectThreeStageLaunch 返回的 Stage 3 event
 * @param opts:
 *   scanWindow         突破后扫描根数（默认 30）
 *   pullbackToleranceRatio  跌破 goldenBuyPrice 的容忍比例（默认 0.97 = 允许跌破 3%）
 *   reboundBodyMin     反包K实体饱满度下限（默认 0.5）
 *   freshWithinBars    反包K距今上限（默认 10 根，约 2 周）— 过期信号无介入意义
 * @returns { idx, time, pullbackIdx, pullbackTime, pullbackLow, reboundClose, barsAgo } 或 null
 */
export function detectSecondaryEntry(klines, event, opts = {}) {
    const {
        scanWindow             = 30,
        pullbackToleranceRatio = 0.97,
        reboundBodyMin         = 0.5,
        freshWithinBars        = 10,
    } = opts

    if (!event || event.s3Idx == null || event.currentStage !== 3) return null
    if (!Array.isArray(klines)) return null

    const breakoutClose = +klines[event.s3Idx]?.close
    if (!(breakoutClose > 0)) return null
    const goldenBuy = event.goldenBuyPrice ?? null
    const start = event.s3Idx + 1
    const end = Math.min(klines.length - 1, start + scanWindow - 1)
    if (start > end) return null

    // 找第一根"回踩K" — 突破后第一次 close < breakoutClose
    let pullbackIdx = -1
    for (let t = start; t <= end; t++) {
        if (+klines[t].close < breakoutClose) {
            pullbackIdx = t
            break
        }
    }
    if (pullbackIdx < 0) return null   // 突破后一路没回踩 → 没二次买点（也不需要，一路涨）

    // 检查回踩没破回踩位（形态没破坏）
    const pullbackClose = +klines[pullbackIdx].close
    if (goldenBuy != null && pullbackClose < goldenBuy * pullbackToleranceRatio) return null

    // 找反包K — 回踩后第一根满足条件的 K
    for (let t = pullbackIdx + 1; t <= end; t++) {
        const k     = klines[t]
        const close = +k.close
        const open  = +k.open
        const high  = +k.high
        const low   = +k.low
        const body  = Math.abs(close - open)
        const range = high - low
        const prevClose = +klines[t - 1].close

        if (close > open                                  // 阳线
            && close > prevClose                          // 收盘超过前一根 close
            && range > 0 && body / range >= reboundBodyMin
        ) {
            // 新鲜度过滤：反包K距今超过 freshWithinBars → 信号已过期没参考意义
            const lastIdx = klines.length - 1
            const barsAgo = lastIdx - t
            if (barsAgo > freshWithinBars) return null

            return {
                idx:          t,
                time:         k.time,
                pullbackIdx,
                pullbackTime: klines[pullbackIdx].time,
                pullbackLow:  pullbackClose,
                reboundClose: close,
                barsAgo,
            }
        }
    }

    return null
}

// ---------------- 📅 周线共振确认（Phase 4 多周期过滤）----------------
/**
 * 判断周 K 是否处于趋势确认状态。日线突破 + 周线确认 = 真趋势变化（不是日线 noise）。
 *
 * 设计依据：
 *   - A 股散户主导、日线 noise 多；周线突破才是机构 / 真实资金流入信号
 *   - 周线趋势确认 = (close > 周MA20) + (周MA20 上行) + (周MA5 > 周MA20)
 *   - 需要至少 25 周数据（约半年）才能算 MA20
 *
 * @param weeklyKlines  周 K 线数组
 * @returns boolean — true 表示周线层面也处于多头趋势
 */
export function hasWeeklyTrendConfirmation(weeklyKlines) {
    if (!Array.isArray(weeklyKlines) || weeklyKlines.length < 25) return false

    const closes = weeklyKlines.map(k => +k.close)
    const ma5Arr  = ma(closes, 5)
    const ma20Arr = ma(closes, 20)
    const last = closes.length - 1
    const lastClose = closes[last]
    const m5  = ma5Arr[last]
    const m20 = ma20Arr[last]
    if (m20 == null || m5 == null) return false

    // 4 周前的 MA20 — 用来判断 MA20 是否上行
    const m20Prev4 = last >= 4 ? ma20Arr[last - 4] : null
    if (m20Prev4 == null) return false

    // 三个条件必须都满足才算"周线确认"
    const aboveMA20  = lastClose > m20
    const ma20Rising = m20 > m20Prev4 * 1.005   // MA20 上行 ≥ 0.5%（4周）
    const ma5AboveMA20 = m5 > m20

    return aboveMA20 && ma20Rising && ma5AboveMA20
}

// ---------------- 🚪 主升结束信号（Phase 2 离场 detector）----------------
/**
 * 给定一个 Stage 3 突破 event，扫描其之后的 K 线找退场信号。
 *
 * 三档信号（按严重程度递增）：
 *   - 'reduce'：减仓警示（爆量长上影 + 量比 ≥ 3.5）—— 不立即清仓，提示风险
 *   - 'exit'  ：清仓信号（跌破 MA10）—— 趋势走破
 *   - 'invalid'：形态作废（跌破突破K收盘 / 跌破蓄势下沿×0.97）—— 立即全清
 *
 * 设计依据 — 全市场回测（4743 只 × 1 年）数据：
 *   - 旧版用静态 stopLossPrice = s1Lower×0.97 作止损 → 触发率仅 1.28%（形同虚设）
 *   - 平均收益 5.52% / 中位收益仅 0.68% → 长尾驱动，大量信号 30 天后基本不动
 *   - 急需"主升走破"动态信号代替静态止损位
 *
 * 命中遵循"先发生先生效"：'invalid' / 'exit' 一旦触发立即返回（已无意义继续扫）
 * 'reduce' 不返回，继续扫，最终返回所有命中
 *
 * @param klines  日K线
 * @param event   detectThreeStageLaunch 返回的 Stage 3 event（必须有 s3Idx, breakoutPrice, s1Lower）
 * @param opts:
 *   recencyBars     检查范围 = breakoutIdx 之后多少根（默认 60，覆盖典型主升周期）
 *   protectBars     突破后保护期（前 N 根不查 MA10 / 突破价跌破，避免噪音）
 *   volClimaxRatio  爆量阈值（量比 ≥ 多少算爆量）
 *   shadowRatio     长上影阈值（上影 / 实体）
 * @returns [{ idx, time, level: 'reduce'|'exit'|'invalid', reason }, ...]
 */
export function detectRallyExhaustion(klines, event, opts = {}) {
    const {
        recencyBars     = 60,
        protectBars     = 3,
        volClimaxRatio  = 3.5,
        shadowRatio     = 1.5,
    } = opts

    if (!klines || !event || event.s3Idx == null || event.s3Idx < 0) return []
    const n = klines.length
    const start = event.s3Idx + 1
    if (start >= n) return []

    const closes = klines.map(k => +k.close)
    // Phase 2 锁定 Option A 基线：MA10。Option B 试过 MA20 反而胜率从 41% 跌到 34% —
    // 数据证明 MA10 的"假信号"其实多为真信号，给它机会反弹反而亏更多。
    const ma10Arr = ma(closes, 10)

    const out = []
    const end = Math.min(n - 1, start + recencyBars - 1)
    const bp = event.breakoutPrice
    const sl = event.s1Lower != null ? event.s1Lower * 0.97 : null

    for (let t = start; t <= end; t++) {
        const k = klines[t]
        const close = +k.close
        const open  = +k.open
        const high  = +k.high
        const body  = Math.abs(close - open)
        const upperShadow = high - Math.max(open, close)

        // === Level 'invalid'：形态作废（仅跌破蓄势下沿这种真正破位）===
        if (sl != null && close < sl) {
            out.push({ idx: t, time: k.time, level: 'invalid', reason: '跌破蓄势下沿×0.97' })
            return out
        }

        // === Level 'exit'：趋势走破（突破后保护期外才查）===
        // Phase 2.2 调整：把"跌破突破K收盘"从 invalid 降级为 exit，加 0.97 缓冲。
        // 旧版（0 缓冲 + invalid）在主板 3001 只回测里 invalid 率 65%，过度激进，
        // 砍掉大量"突破后小幅回踩 -2% 又涨回去"的健康形态。3% 缓冲 + 降级为 exit
        // 与 MA10 跌破并列，让"跌破突破K"和"跌破MA10"成为同等级的趋势走破信号。
        if (bp != null && close < bp * 0.97 && t >= start + protectBars) {
            out.push({ idx: t, time: k.time, level: 'exit', reason: '跌破突破K收盘×0.97' })
            return out
        }
        if (ma10Arr[t] != null && close < ma10Arr[t] && t >= start + protectBars) {
            out.push({ idx: t, time: k.time, level: 'exit', reason: '跌破MA10' })
            return out
        }

        // === Level 'reduce'：爆量长上影警示（不返回，继续扫）===
        if (body > 0 && upperShadow > body * shadowRatio) {
            const vr = volumeRatio(klines, t, 5)
            if (vr != null && vr >= volClimaxRatio) {
                out.push({
                    idx: t, time: k.time, level: 'reduce',
                    reason: `爆量长上影(VR=${vr.toFixed(1)})`,
                })
            }
        }
    }
    return out
}

// ---------------- 主升 / 主跌段识别（独立趋势段染色）----------------
/**
 * 识别价格走势中的"主升段"和"主跌段"——zigzag 相邻 swing 之间的大幅单向波段。
 * 用于"不开任何指标"的纯净视图下，依然能看清哪段是主升、哪段是主跌。
 *
 * @param klines
 * @param opts:
 *   pivotWindow   zigzag 检测窗口
 *   recencyBars   扫描范围
 *   minRangePct   最低涨跌幅（默认 15%，太小不算主升/主跌）
 *   minBars       最少持续根数
 * @returns [{ type: 'up'|'down', startIdx, endIdx, startTime, endTime, startPrice, endPrice, rangePct, barCount }]
 */
export function detectMainTrends(klines, opts = {}) {
    const {
        pivotWindow = 10,
        recencyBars = 300,
        minRangePct = 0.15,
        minBars = 10,
    } = opts
    if (!klines || klines.length < pivotWindow * 2 + 1) return []
    const zz = detectZigzag(klines, { pivotWindow, recencyBars })
    if (zz.length < 2) return []

    const trends = []
    for (let i = 0; i < zz.length - 1; i++) {
        const a = zz[i], b = zz[i + 1]
        const span = b.idx - a.idx
        if (span < minBars) continue
        const isUp  = b.price > a.price
        const range = Math.abs(b.price - a.price) / Math.min(a.price, b.price)
        if (range < minRangePct) continue
        trends.push({
            type:       isUp ? 'up' : 'down',
            startIdx:   a.idx,
            endIdx:     b.idx,
            startTime:  a.time,
            endTime:    b.time,
            startPrice: a.price,
            endPrice:   b.price,
            rangePct:   range * 100,
            barCount:   span,
        })
    }
    return trends
}

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

// ---------------- 聚类版支撑/压力（高级模式）----------------
/**
 * 跟简单版（近 N 根高/低）互补：用滑窗 swing + 价格聚类找"被反复试探"的关键档位。
 * 触及越多次、越近期 → 越强。
 *
 * @param klines
 * @param opts:
 *   pivotWindow       swing 检测窗口（左右各 N 根）
 *   clusterTolerance  价差 ≤ 此比例归并为同档
 *   minTouches        最少触及次数
 *   topK              支撑/压力各取 Top N
 *   recencyHalfLife   时间衰减半衰期（多少根 K 线前权重减半）
 *   maxDistancePct    离现价超过此比例剔除
 * @returns { supports: [{price, touches, score}], resistances: [...] }
 */
export function supportResistanceCluster(klines, opts = {}) {
    const {
        pivotWindow = 5,
        clusterTolerance = 0.015,
        minTouches = 2,
        topK = 3,
        recencyHalfLife = 30,
        maxDistancePct = 0.25,
        volumeConfirm = true,    // 量能确认：拐点附近均量 ≥ 全局均量 × 0.8 才算有效
        volumeRatio = 0.8,
    } = opts
    if (!klines || klines.length < pivotWindow * 2 + 1) {
        return { supports: [], resistances: [] }
    }
    const n = klines.length

    // 全局均量（用于量能确认）
    let globalAvgVol = 0
    if (volumeConfirm) {
        let sum = 0, count = 0
        for (const k of klines) {
            const v = +k.vol
            if (v > 0) { sum += v; count++ }
        }
        globalAvgVol = count > 0 ? sum / count : 0
    }
    function passVolume(idx) {
        if (!volumeConfirm || globalAvgVol === 0) return true
        let sum = 0, count = 0
        for (let i = Math.max(0, idx - 2); i < Math.min(n, idx + 3); i++) {
            const v = +klines[i].vol
            if (v > 0) { sum += v; count++ }
        }
        if (count === 0) return true
        return sum / count >= globalAvgVol * volumeRatio
    }

    // 1. 找 swing 高/低（带量能确认）
    let highs = []
    let lows  = []
    for (let i = pivotWindow; i < n - pivotWindow; i++) {
        const h = +klines[i].high, l = +klines[i].low
        let isHigh = true, isLow = true
        for (let j = 1; j <= pivotWindow; j++) {
            if (h <= +klines[i - j].high || h <= +klines[i + j].high) isHigh = false
            if (l >= +klines[i - j].low  || l >= +klines[i + j].low)  isLow  = false
            if (!isHigh && !isLow) break
        }
        if (isHigh) highs.push({ idx: i, price: h })
        if (isLow)  lows.push({ idx: i, price: l })
    }
    // 量能过滤；如果过滤完全为空，回退到原始（避免完全没结果）
    if (volumeConfirm) {
        const fHighs = highs.filter(p => passVolume(p.idx))
        const fLows  = lows.filter(p => passVolume(p.idx))
        if (fHighs.length >= minTouches) highs = fHighs
        if (fLows.length  >= minTouches) lows  = fLows
    }

    // 2. 价格聚类
    function cluster(pivots) {
        if (!pivots.length) return []
        const sorted = [...pivots].sort((a, b) => a.price - b.price)
        const clusters = []
        let cur = { prices: [sorted[0].price], idxs: [sorted[0].idx] }
        for (let i = 1; i < sorted.length; i++) {
            const ref = cur.prices.reduce((a, b) => a + b, 0) / cur.prices.length
            if (ref > 0 && Math.abs(sorted[i].price - ref) / ref <= clusterTolerance) {
                cur.prices.push(sorted[i].price)
                cur.idxs.push(sorted[i].idx)
            } else {
                clusters.push(cur)
                cur = { prices: [sorted[i].price], idxs: [sorted[i].idx] }
            }
        }
        clusters.push(cur)
        const last = n - 1
        return clusters
            .filter(c => c.prices.length >= minTouches)
            .map(c => {
                const meanPrice = c.prices.reduce((a, b) => a + b, 0) / c.prices.length
                const newest   = Math.max(...c.idxs)
                const recency  = Math.pow(0.5, (last - newest) / recencyHalfLife)
                return {
                    price: meanPrice,
                    touches: c.prices.length,
                    score: c.prices.length * recency,
                }
            })
            .sort((a, b) => b.score - a.score)
    }

    const lastClose = +klines[n - 1].close
    const nearEnough = c => Math.abs(c.price - lastClose) / lastClose <= maxDistancePct

    return {
        resistances: cluster(highs).filter(c => c.price >= lastClose && nearEnough(c)).slice(0, topK),
        supports:    cluster(lows ).filter(c => c.price <= lastClose && nearEnough(c)).slice(0, topK),
    }
}

// ---------------- K 线形态识别（5 种短线敏感形态）----------------
/**
 * 识别短线最敏感的 5 种形态：
 *   - 锤子线 hammer        ：底部反转信号，下影 > 实体×2.2、上影 < 实体×0.4
 *   - 射击之星 shootingStar ：顶部反转信号，上影 > 实体×2.2、下影 < 实体×0.4
 *   - 看涨吞没 bullishEngulfing：前阴后阳，后实体完全包裹前实体
 *   - 看跌吞没 bearishEngulfing：前阳后阴，后实体完全包裹前实体
 *   - 十字星 doji           ：犹豫 / 反转预警，实体 < 振幅×8%
 *
 * @param klines
 * @param opts:
 *   recencyBars  只扫最近 N 根（默认 200）
 * @returns [{ idx, time, type, signal: 'bullish'|'bearish'|'neutral', label }]
 */
export function detectPatterns(klines, opts = {}) {
    const { recencyBars = 200 } = opts
    if (!klines || klines.length < 2) return []
    const n = klines.length
    const startIdx = Math.max(1, n - recencyBars)
    const out = []

    for (let i = startIdx; i < n; i++) {
        const k = klines[i]
        const open  = +k.open
        const close = +k.close
        const high  = +k.high
        const low   = +k.low
        const body  = Math.abs(close - open)
        const upperShadow = high - Math.max(open, close)
        const lowerShadow = Math.min(open, close) - low
        const range = high - low
        if (range <= 0) continue

        // —— 锤子线 / 射击之星（单根 K 线）——
        if (body > 0) {
            if (lowerShadow > body * 2.2 && upperShadow < body * 0.4) {
                out.push({ idx: i, time: k.time, type: 'hammer', signal: 'bullish', label: '锤', fullName: '锤子线' })
                continue
            }
            if (upperShadow > body * 2.2 && lowerShadow < body * 0.4) {
                out.push({ idx: i, time: k.time, type: 'shootingStar', signal: 'bearish', label: '星', fullName: '射击之星' })
                continue
            }
        }

        // —— 十字星（单根 K 线）——
        if (body < range * 0.08) {
            out.push({ idx: i, time: k.time, type: 'doji', signal: 'neutral', label: '十', fullName: '十字星' })
            continue
        }

        // —— 吞没形态（前后两根）——
        if (i > 0) {
            const prev = klines[i - 1]
            const pOpen  = +prev.open
            const pClose = +prev.close
            const pBody  = Math.abs(pClose - pOpen)
            if (pBody > 0 && body > pBody) {
                const prevBull = pClose > pOpen
                const curBull  = close > open

                // 看涨吞没：前阴后阳 + 后实体包裹前实体
                if (!prevBull && curBull && open <= pClose && close >= pOpen) {
                    out.push({ idx: i, time: k.time, type: 'bullishEngulfing', signal: 'bullish', label: '吞', fullName: '看涨吞没' })
                    continue
                }
                // 看跌吞没：前阳后阴 + 后实体包裹前实体
                if (prevBull && !curBull && open >= pClose && close <= pOpen) {
                    out.push({ idx: i, time: k.time, type: 'bearishEngulfing', signal: 'bearish', label: '吞', fullName: '看跌吞没' })
                    continue
                }
            }
        }
    }

    return out
}

// ---------------- 斐波那契回撤（自动识别主波段 + 7 条经典回撤位）----------------
/**
 * 找最近最大的 zigzag 段作为锚点，画 0% / 23.6% / 38.2% / 50% / 61.8% / 78.6% / 100% 七条回撤线。
 * 短线核心用法：上涨回撤到 38.2% / 50% / 61.8% 是经典低吸点。
 *
 * @param klines
 * @param opts:
 *   recencyBars     扫描范围
 *   pivotWindow     zigzag 检测窗口
 *   minRangePct     主波段振幅至少多少（默认 10%，太小没回撤价值）
 * @returns null 或 { direction, startIdx, endIdx, startTime, endTime, high, low, levels: [{ratio, price, label}] }
 */
export function detectFibonacci(klines, opts = {}) {
    const { recencyBars = 250, pivotWindow = 10, minRangePct = 0.10 } = opts
    if (!klines || klines.length < pivotWindow * 2 + 1) return null

    const zz = detectZigzag(klines, { pivotWindow, recencyBars })
    if (zz.length < 2) return null

    // 找相邻 swing 中振幅最大的一段
    let best = null
    let bestRange = 0
    for (let i = 0; i < zz.length - 1; i++) {
        const a = zz[i], b = zz[i + 1]
        const range = Math.abs(b.price - a.price) / Math.min(a.price, b.price)
        if (range > bestRange) {
            bestRange = range
            best = { from: a, to: b }
        }
    }
    if (!best || bestRange < minRangePct) return null

    const isUp = best.to.price > best.from.price
    const high = Math.max(best.from.price, best.to.price)
    const low  = Math.min(best.from.price, best.to.price)
    const range = high - low

    // 7 个经典回撤比例
    const ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    const levels = ratios.map(r => ({
        ratio: r,
        price: high - r * range,                 // 0% = 高、100% = 低
        label: r === 0 ? '0%' : r === 1 ? '100%' : `${(r * 100).toFixed(1)}%`,
        isKey: r === 0 || r === 0.5 || r === 1,  // 0/50/100 视为关键档位
    }))

    return {
        direction: isUp ? 'up' : 'down',
        startIdx:  best.from.idx,
        endIdx:    best.to.idx,
        startTime: best.from.time,
        endTime:   best.to.time,
        high, low,
        levels,
    }
}

// ---------------- 共振检测（多源价位聚类）----------------
/**
 * 把多个独立指标产生的"关键价位"聚到一起，找出多源信号汇聚的"共振区"。
 *
 * @param levels    [{ price, source }] 各类指标产生的关键价位（同一价位可来自多个 source）
 * @param tolerancePct  聚类容差（价差 / 中位 ≤ 此比例归并为同一区，默认 1.2%）
 * @param minSources    至少多少个不同来源才算共振（默认 2）
 * @returns [{ price, sources: [...], count, distancePct }] 按 count 倒序
 *           （distancePct 需要外面传 currentPrice 才能算，这里返回不带 distance 版本）
 */
export function clusterResonance(levels, tolerancePct = 0.012, minSources = 2) {
    if (!levels?.length) return []
    const sorted = [...levels].sort((a, b) => a.price - b.price)
    const clusters = []
    let cur = { prices: [sorted[0].price], sources: [sorted[0].source] }
    for (let i = 1; i < sorted.length; i++) {
        const ref = cur.prices.reduce((a, b) => a + b, 0) / cur.prices.length
        if (ref > 0 && Math.abs(sorted[i].price - ref) / ref <= tolerancePct) {
            cur.prices.push(sorted[i].price)
            cur.sources.push(sorted[i].source)
        } else {
            clusters.push(cur)
            cur = { prices: [sorted[i].price], sources: [sorted[i].source] }
        }
    }
    clusters.push(cur)

    return clusters
        .map(c => {
            const uniqueSources = [...new Set(c.sources)]
            return {
                price: c.prices.reduce((a, b) => a + b, 0) / c.prices.length,
                sources: uniqueSources,
                count: uniqueSources.length,
            }
        })
        .filter(c => c.count >= minSources)
        .sort((a, b) => b.count - a.count)
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
        recencyBars = 150,    // 200 → 150：更聚焦近期趋势，避免老波段污染
        maxPivots = 4,         // 5 → 4：用更少的最近 swing，更响应当下
        minPivots = 3,
        maxDisplayBars = 100,  // 通道显示最多回看 N 根，超过的部分裁剪掉（避免视觉上"飞出去"）
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

    // 通道几何 startIdx 是用于拟合的最早 swing；
    // 但显示时不让通道往左延伸超过 maxDisplayBars 根（防止数据有结构性突变时通道飞出价格区间）
    const fitStartIdx = Math.min(lows[0].idx, highs[0].idx)
    const endIdx      = klines.length - 1
    const displayStartIdx = Math.max(fitStartIdx, endIdx - maxDisplayBars)
    return {
        // 平行通道：lowSlope === highSlope === slope
        lowSlope: slope, lowIntercept,
        highSlope: slope, highIntercept,
        startIdx: displayStartIdx, endIdx,
        startTime: klines[displayStartIdx].time,
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
        // 新增：角度过滤（朋友的算法）
        minSlopePct = 0.0003,    // |斜率| / 起点价 至少 0.03%（避免太平）
        maxSlopePct = 0.05,      // 至多 5%（避免太陡的伪线）
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
            // 角度过滤：剔除太平 / 太陡的无意义线
            const slopePct = p1.price > 0 ? Math.abs(slope) / p1.price : 0
            if (slopePct < minSlopePct || slopePct > maxSlopePct) continue
            const isHighLine = p1.type === 'high'

            let touches = 2, broken = false
            const deviations = []
            for (let i = p1.idx + 1; i < n; i++) {
                if (i === p2.idx) continue
                const lineY = p1.price + slope * (i - p1.idx)
                if (lineY <= 0) continue
                if (isHighLine) {
                    const h = +klines[i].high
                    if (h > lineY * (1 + touchTolerancePct)) { broken = true; break }
                    const dev = Math.abs(h - lineY) / lineY
                    if (dev <= touchTolerancePct) { touches++; deviations.push(dev) }
                } else {
                    const l = +klines[i].low
                    if (l < lineY * (1 - touchTolerancePct)) { broken = true; break }
                    const dev = Math.abs(l - lineY) / lineY
                    if (dev <= touchTolerancePct) { touches++; deviations.push(dev) }
                }
            }

            if (!broken && touches >= touchMinCount) {
                // 朋友的评分公式：touches² × span / (avgDeviation + ε) —— 平方权重 + 偏差惩罚
                const avgDev = deviations.length > 0
                    ? deviations.reduce((a, b) => a + b, 0) / deviations.length
                    : 0
                const score = (touches * touches) * span / (avgDev + 0.0001)
                candidates.push({
                    type: p1.type,
                    startIdx: p1.idx, endIdx: p2.idx,
                    startTime: klines[p1.idx].time, endTime: klines[p2.idx].time,
                    startPrice: p1.price, endPrice: p2.price,
                    touches, span, score,
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


// ============== 龙回头检测器 ==============
//
// 真龙回头特征：短促拉升 + 快速回踩，整个周期 10~30 天就完事。
//
// Phase 1 加固（基于外审反馈）：
//   • 连板硬约束（cluster 内必须存在连续 2 板，过滤"零散涨停伪龙"）
//   • 量能基期改用回踩末期均量（不再被涨停日基期污染）
//   • 启动 K 形态约束（实体占比 50% 或涨幅 4%，过滤上影线诱多）
//   • 上市 ≥ 60 日（次新逻辑性质不同，先剔除避免噪声）
//
// 三段式逻辑：
//   1. 龙（短促拉升 + 连板锁筹）：clusterWindow 根内堆 ≥ minLimitUps 个涨停 + 至少出现一次连板
//   2. 回头（迫切型）：高点 ≤ peakMaxBarsAgo 根前 + 回踩 [pullbackMin, pullbackMax]% + 近 N 根振幅收敛
//   3. 启动：近 signalLookback 根至少一根 K，满足 量比≥signalVolRatio (vs 回踩末期均量) + 实体或涨幅约束
//
// 涨停阈值 close/prev_close ≥ 1.095（主板 10% / 创业科创 20% 都覆盖）。
//
// @param {Object} klines  日 K 数组
// @param {Object} opts    可调参数
// @returns {Object|null}
export function detectDragonReturn(klines, opts = {}) {
    const {
        clusterWindow      = 15,    // 涨停聚集窗口：15 根内 ≥3 涨停
        minLimitUps        = 3,     // 涨停板下限
        limitUpThreshold   = 1.095, // close/prev_close 阈值（主板 10% / 创业 20% 全覆盖）
        peakMaxBarsAgo     = 15,    // 峰值 ≤ N 根前
        pullbackMin        = 15,    // 回踩 ≥ 15%
        pullbackMax        = 30,    // 回踩 ≤ 30%（再深就是死龙）
        stabilizeBars      = 5,     // 近 N 根振幅收敛
        stabilizeRangePct  = 10,    // 5 根范围振幅 ≤ 10%
        // ↓ Phase 1 加固 + 后续松动调整（兼顾质量与命中率）
        signalLookback     = 5,     // 近 N 根找启动 K（3→5：覆盖整周信号）
        signalVolRatio     = 1.7,   // 启动 K 量比 vs 回踩末期均量（2.0→1.7：包容温和启动）
        pullbackEndBars    = 5,     // 启动 K 之前 N 根作为"回踩末期地量"基期
        ignitionMinBodyRatio = 0.5, // 启动 K 实体占 K 线全长比例下限
        ignitionMinPct     = 4,     // 或涨幅 ≥ 4%（与上面 OR 关系）
        consecutiveWindow  = 3,     // 连板软化：3 天内含 2 个涨停即认（含"涨停-横-涨停"型）
        minIpoBars         = 60,    // 上市 < N 根的次新跳过（数据本身就不足）
    } = opts

    // 数据长度：至少要装下 search 窗口 + 稳定区 + IPO 过滤
    const minLen = Math.max(minIpoBars, peakMaxBarsAgo + clusterWindow + stabilizeBars + 5)
    if (!Array.isArray(klines) || klines.length < minLen) return null

    const n = klines.length
    const lastIdx = n - 1

    // 1. 找涨停聚集：在最近 (peakMaxBarsAgo + clusterWindow) 根范围内，
    //    寻找任一 clusterWindow 根滑动窗口里含 ≥ minLimitUps 个涨停
    const searchStart = Math.max(1, lastIdx - (peakMaxBarsAgo + clusterWindow) + 1)
    const limitUpIndices = []
    for (let t = searchStart; t <= lastIdx; t++) {
        const prev = +klines[t - 1].close
        const close = +klines[t].close
        if (prev > 0 && close / prev >= limitUpThreshold) {
            limitUpIndices.push(t)
        }
    }
    if (limitUpIndices.length < minLimitUps) return null

    // 滑动窗口找最早的合格 cluster
    let clusterStart = -1, clusterCount = 0, clusterIndices = []
    for (let i = 0; i <= limitUpIndices.length - minLimitUps; i++) {
        const span = limitUpIndices[i + minLimitUps - 1] - limitUpIndices[i] + 1
        if (span <= clusterWindow) {
            clusterStart = limitUpIndices[i]
            clusterIndices = limitUpIndices.filter(
                idx => idx >= clusterStart && idx <= clusterStart + clusterWindow - 1,
            )
            clusterCount = clusterIndices.length
            break
        }
    }
    if (clusterStart < 0) return null

    // 1.1 连板软化约束：cluster 内必须存在 consecutiveWindow 天内含 ≥ 2 涨停的紧密簇
    // 涵盖：连板（间隔 1）/ 涨停-横盘-涨停（间隔 2-3）
    // 过滤掉：涨停-跌停-涨停-横盘 5 天-涨停 这类零散反复票
    const hasTightCluster = clusterIndices.some(
        (idx, i) => i > 0 && (idx - clusterIndices[i - 1]) < consecutiveWindow,
    )
    if (!hasTightCluster) return null

    // 2. 高点：从 cluster 开始到现在的最高 high
    let peakIdx = clusterStart
    let peakHigh = +klines[clusterStart].high
    for (let t = clusterStart; t <= lastIdx; t++) {
        const h = +klines[t].high
        if (h > peakHigh) { peakHigh = h; peakIdx = t }
    }
    if (!(peakHigh > 0)) return null

    // 高点必须近 peakMaxBarsAgo 根（迫切型回头）
    const peakBarsAgo = lastIdx - peakIdx
    if (peakBarsAgo > peakMaxBarsAgo) return null

    // 3. 回踩幅度（峰 → 现价）
    const lastClose = +klines[lastIdx].close
    const pullbackPct = (peakHigh - lastClose) / peakHigh * 100
    if (pullbackPct < pullbackMin || pullbackPct > pullbackMax) return null

    // 4. 近 stabilizeBars 根振幅收敛（回头企稳）
    const stabStart = lastIdx - stabilizeBars + 1
    if (stabStart <= peakIdx) return null     // 还没回完，峰还在企稳窗口里
    let stabHigh = 0, stabLow = Infinity
    for (let t = stabStart; t <= lastIdx; t++) {
        const h = +klines[t].high, l = +klines[t].low
        if (h > stabHigh) stabHigh = h
        if (l < stabLow) stabLow = l
    }
    const stabRangePct = stabLow > 0 ? (stabHigh - stabLow) / stabLow * 100 : 999
    if (stabRangePct > stabilizeRangePct) return null

    // 5. 启动信号：近 signalLookback 根找一根满足
    //    a) 收阳
    //    b) 量比 ≥ signalVolRatio （基期 = 启动 K 之前 pullbackEndBars 根均量，地量基期）
    //    c) 实体 ≥ 50% 全长 OR 涨幅 ≥ 4%（剔除上影线诱多）
    const ignStart = lastIdx - signalLookback + 1
    let ignitionIdx = -1
    let ignitionVr = null
    for (let t = ignStart; t <= lastIdx; t++) {
        const open = +klines[t].open
        const close = +klines[t].close
        const high = +klines[t].high
        const low = +klines[t].low
        if (close <= open) continue                 // 必须收阳

        // 量比基期：启动 K 之前 pullbackEndBars 根均量（地量基期，不被涨停日污染）
        const baseStart = Math.max(0, t - pullbackEndBars)
        if (baseStart >= t) continue
        let volSum = 0, volCnt = 0
        for (let k = baseStart; k < t; k++) {
            const v = +klines[k].vol
            if (Number.isFinite(v) && v > 0) { volSum += v; volCnt++ }
        }
        if (volCnt === 0) continue
        const baseVol = volSum / volCnt
        const ignVol = +klines[t].vol
        if (!(baseVol > 0) || !(ignVol > 0)) continue
        const realVr = ignVol / baseVol
        if (realVr < signalVolRatio) continue       // 量能不足

        // K 线形态：实体占比 ≥ 50% 或 涨幅 ≥ 4%
        const body = Math.abs(close - open)
        const range = high - low
        const bodyRatio = range > 0 ? body / range : 0
        const prevClose = +klines[t - 1].close
        const pct = prevClose > 0 ? (close - prevClose) / prevClose * 100 : 0
        if (bodyRatio < ignitionMinBodyRatio && pct < ignitionMinPct) continue

        ignitionIdx = t
        ignitionVr  = realVr
        break
    }
    if (ignitionIdx < 0) return null

    return {
        // 龙（cluster 内涨停数 — 不是全 60 天的）
        limitUpCount:     clusterCount,
        clusterStartIdx:  clusterStart,
        clusterStartTime: klines[clusterStart].time,
        // 高点
        peakIdx,
        peakBarsAgo,
        peakTime:         klines[peakIdx].time,
        peakHigh,
        // 回头
        pullbackPct,
        stabRangePct,
        // 启动
        ignitionIdx,
        ignitionTime:     klines[ignitionIdx].time,
        ignitionVr,
        // 现状
        lastClose,
        // 推荐买价（启动 K 收盘）
        suggestedEntry:   +klines[ignitionIdx].close,
        // 止损：回踩区低点 × 0.97
        stopLoss:         stabLow * 0.97,
    }
}


// ============== 连板游资策略（Ptrade 1:1 迁移）==============
//
// 来源：Ptrade 量化平台「极速优化版游资连板策略」
// 核心：今日触板股 + 三种形态（连板接力 / 反包确认 / 摸板试盘）+ 妖股基因
//
// 涨停判定：close >= high_limit - 0.01 - EPSILON 且 volume > 0（排除停牌假板）
// 涨停幅度：主板 10% / 创业科创 20% / ST 5%（按 code 段 + 名称识别）
//
// @param {Array}   klines  日 K 数组（至少 30 根）
// @param {Object}  opts
//   @param {String}  code    股票代码（决定主板/创业/科创涨停幅度）
//   @param {String}  name    股票名（识别 ST：含 'ST'/'*ST' 即 ST 股）
// @returns {Object|null}  匹配则返回 { mode, height, boards5, ... }，否则 null
export function detectLimitUpRelay(klines, opts = {}) {
    const { code = '', name = '' } = opts

    if (!Array.isArray(klines) || klines.length < 30) return null
    const n = klines.length
    const lastIdx = n - 1
    const EPSILON = 1e-6

    // ---------------- 涨停幅度判定 ----------------
    const isST = /\*?ST/i.test((name || '').replace(/\s+/g, ''))
    let limitPct
    if (isST) limitPct = 1.05
    else if (/^(30|688)/.test(code)) limitPct = 1.20
    else limitPct = 1.10

    function calcLimit(prevClose) {
        // 跟 A 股实际规则对齐：四舍五入到 2 位小数
        return Math.round(prevClose * limitPct * 100) / 100
    }

    // ---------------- 涨停矩阵（含成交量约束）----------------
    // 每根 K 是否涨停：close >= limit - 0.01 - EPSILON 且 volume > 0
    // 停牌日 vol = 0 → 即使 close 看起来 == limit 也不算涨停
    const isZt = new Array(n).fill(false)
    for (let t = 1; t < n; t++) {
        const prevClose = +klines[t - 1].close
        const close = +klines[t].close
        const vol = +klines[t].vol
        if (!(prevClose > 0) || !(close > 0)) continue
        const limit = calcLimit(prevClose)
        if (close >= limit - 0.01 - EPSILON && vol > 0) {
            isZt[t] = true
        }
    }

    // ---------------- 关键价格 ----------------
    const c0 = +klines[lastIdx].close       // 今日收盘
    const c1 = +klines[lastIdx - 1].close    // 昨日收盘
    const c2 = +klines[lastIdx - 2].close    // 前日收盘
    const h0 = +klines[lastIdx].high         // 今日最高
    const v0 = +klines[lastIdx].vol          // 今日成交量

    if (!(c0 > 0) || !(v0 > 0)) return null  // 今日停牌或数据异常 → 跳过

    const limit0 = calcLimit(c1)             // 今日涨停价（基于昨日收盘）
    const isAlive = v0 > 0
    const isZt0 = isZt[lastIdx]
    const isZt1 = isZt[lastIdx - 1]

    // ---------------- 三种形态识别 ----------------
    // A) 连板接力：今天涨停 + 昨天涨停
    const modeA = isZt0 && isZt1
    // B) 反包确认：今天涨停 + 昨天未涨停 + 昨日相对前日跌幅 < 8%（c1/c2 > 0.92）
    const modeB = isZt0 && !isZt1 && c2 > 0 && (c1 / c2) > 0.92 - EPSILON
    // C) 摸板试盘：今天 high 触涨停但收盘未封 + 收涨 + 上影线 < 5%
    const touchZt0 = h0 >= limit0 - 0.01 - EPSILON
    const upperShadow = c0 > 0 ? (h0 - c0) / c0 : 999
    const modeC = touchZt0 && !isZt0 && c0 > c1 && upperShadow < 0.05

    const formationMatched = modeA || modeB || modeC
    if (!formationMatched) return null

    // ---------------- 妖股基因 ----------------
    // 基因 1：近 5 个有效交易日（vol > 0）至少 2 个涨停
    //         跨停牌按"有效交易日"算，停牌日不进 5 日窗口
    let realZt = []   // 有效交易日的涨停状态序列
    for (let t = 0; t < n; t++) {
        const v = +klines[t].vol
        if (Number.isFinite(v) && v > 0) realZt.push(isZt[t])
    }
    const recent5 = realZt.slice(-5)
    const recent5Zt = recent5.filter(Boolean).length
    const hotGene = recent5Zt >= 2

    if (!hotGene) return null

    // 基因 2：今日 close >= 近 20 日 high 最大值 × 0.95
    const lookback20Start = Math.max(0, lastIdx - 19)
    let hhv20 = 0
    for (let t = lookback20Start; t <= lastIdx; t++) {
        const h = +klines[t].high
        if (Number.isFinite(h) && h > hhv20) hhv20 = h
    }
    const newHigh = c0 >= hhv20 * 0.95 - EPSILON

    if (!newHigh) return null

    // ---------------- 跨停牌连板高度 + 5 天板数 ----------------
    // height: 从最新有效交易日往前数，连续涨停的有效日数（停牌日不算断）
    let height = 0
    if (realZt.length > 0 && realZt[realZt.length - 1]) {
        for (let i = realZt.length - 1; i >= 0; i--) {
            if (realZt[i]) height++
            else break
        }
    }
    const boards5 = recent5Zt   // 跟基因 1 复用，省一次遍历

    // ---------------- 形态标签 ----------------
    const modes = []
    if (modeA) modes.push('A')
    if (modeB) modes.push('B')
    if (modeC) modes.push('C')

    let stat
    if (height > 1) stat = `${height}连板`
    else if (height === 1) stat = '首板'
    else stat = '断板'

    const reasonMap = { A: '连板接力', B: '反包确认', C: '摸板试盘' }
    const reason = modes.map(m => reasonMap[m]).join('+')

    return {
        modes,           // ['A', 'B', ...]
        reason,          // '连板接力+反包确认' 等
        stat,            // '3连板' / '首板' / '断板'
        height,          // 连板高度
        boards5,         // 近 5 有效交易日板数
        isST,
        // 价格状态
        lastClose:   c0,
        limitPrice:  limit0,
        hhv20,
        // 形态标记（给 UI 排序/过滤）
        modeA, modeB, modeC,
    }
}
