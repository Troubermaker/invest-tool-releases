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

// ---------------- 工具：K 线 time 字段 → JS Date ----------------
// 兼容 'YYYY-MM-DD' / 'YYYY-MM-DD HH:mm:ss' 字符串、{year,month,day} 对象、毫秒/秒时间戳
export function parseKlineDate(t) {
    if (!t) return null
    if (typeof t === 'string' && t.length >= 10) {
        const y = +t.slice(0, 4), m = +t.slice(5, 7), d = +t.slice(8, 10)
        if (Number.isFinite(y) && Number.isFinite(m) && Number.isFinite(d)) {
            return new Date(y, m - 1, d)
        }
        return null
    }
    if (typeof t === 'object' && t.year) {
        return new Date(+t.year, (+t.month || 1) - 1, +t.day || 1)
    }
    if (typeof t === 'number') {
        const ms = t > 1e12 ? t : t * 1000
        const d = new Date(ms)
        return Number.isNaN(d.getTime()) ? null : d
    }
    return null
}

// ---------------- 工具：两根 K 线相隔的日历日数 ----------------
// 用于「停牌后又涨停"伪装"成连板」之类的误判防御 ——
// detector 算"相邻"时不要用 idx 差（i+1 在数组里相邻但日历上可能差一个月），
// 改用日历日差判定，含周末跨越 / 节假日跨越的真实间隔。
// 解析失败返 null。
export function calendarDaysBetween(klines, idxA, idxB) {
    if (!klines || idxA < 0 || idxB < 0 || idxA >= klines.length || idxB >= klines.length) return null
    const dA = parseKlineDate(klines[idxA]?.time)
    const dB = parseKlineDate(klines[idxB]?.time)
    if (!dA || !dB) return null
    return Math.round(Math.abs(dB - dA) / (24 * 60 * 60 * 1000))
}

// ---------------- 工具：检测最近 N 根 K 线是否有"空洞" ----------------
// 用法：scanner 调用 detector 之前先校验，避免拿到含停牌空洞的数据导致误判
// 返回：null（无空洞）或 { idxFrom, idxTo, daysGap }（最近一处空洞详情）
//
// lookbackBars: 只检查最后 N 根（每个 detector 实际只用最近这么多）
// maxGapDays:   相邻两根 ≤ 此值算正常（默认 12，覆盖国庆春节 + 调休）
export function findRecentGap(klines, lookbackBars, maxGapDays = 12) {
    if (!klines || klines.length < 2) return null
    const start = Math.max(1, klines.length - lookbackBars)
    for (let i = start; i < klines.length; i++) {
        const gap = calendarDaysBetween(klines, i - 1, i)
        if (gap != null && gap > maxGapDays) {
            return { idxFrom: i - 1, idxTo: i, daysGap: gap }
        }
    }
    return null
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

// ---------------- 大盘环境多维度评分（Week 1 Day 1 升级版）----------------
/**
 * 多维度 regime 评分 —— 在原 computeMarketEnvScore 基础上叠加：
 *   1. 沪深300 / 上证指数 趋势分（原 computeMarketEnvScore 逻辑）
 *   2. 创业板指 相对强度（小盘股风险偏好）
 *   3. 市场宽度（涨停 / 跌停家数比、上涨家数占比）
 *   4. 量能验证（破位需要放量确认；缩量阴跌不算破位）
 *
 * 输入：
 *   csi300Klines: 沪深300 日 K（必须，60 根以上）
 *   gemKlines:    创业板指 日 K（可选，没有则 gem_relative=0）
 *   sentiment:    { limitUp, limitDown, up, down, flat } 当日涨跌分布（可选）
 *
 * 输出：
 *   {
 *     score,    // 0-100
 *     regime,   // 'strong' | 'good' | 'neutral' | 'weak' | 'breakdown'
 *     label,    // 中文标签
 *     breakdown: { index_trend, market_breadth, gem_relative, volume },  // 子分量
 *     reasons,  // 平铺所有理由
 *   }
 */
export function computeMarketRegime({ csi300Klines, gemKlines, sentiment } = {}) {
    if (!csi300Klines || csi300Klines.length < 60) return null

    const reasons = []
    const breakdown = {
        index_trend:    { score: 0, max: 50, reasons: [] },
        market_breadth: { score: 0, max: 20, reasons: [] },
        gem_relative:   { score: 0, max: 15, reasons: [] },
        volume:         { score: 0, max: 15, reasons: [] },
    }

    // === 1. 指数趋势 (50 分)：复用 computeMarketEnvScore 逻辑 ===
    const csi = computeMarketEnvScore(csi300Klines)
    if (csi) {
        // 把原 0-100 分映射到 0-50
        breakdown.index_trend.score = csi.score * 0.5
        breakdown.index_trend.reasons = csi.reasons
        reasons.push(...csi.reasons.map(r => `[趋势] ${r}`))
    }

    // === 2. 市场宽度 (20 分)：涨跌停比 + 上涨家数 ===
    if (sentiment) {
        const { limitUp = 0, limitDown = 0, up = 0, down = 0 } = sentiment
        let breadthScore = 10   // 中性
        // 涨停 vs 跌停（最重要的"情绪温度计"）
        const luLdRatio = limitDown > 0 ? limitUp / limitDown : (limitUp > 0 ? 10 : 1)
        if (limitUp >= 80 && limitDown <= 10) {
            breadthScore += 6
            breakdown.market_breadth.reasons.push(`涨停 ${limitUp} 跌停 ${limitDown}（赚钱效应强）`)
        } else if (limitUp >= 50 && luLdRatio >= 3) {
            breadthScore += 3
            breakdown.market_breadth.reasons.push(`涨停 ${limitUp} 跌停 ${limitDown}（赚钱效应）`)
        } else if (limitDown >= 30 && luLdRatio < 0.5) {
            breadthScore -= 6
            breakdown.market_breadth.reasons.push(`跌停 ${limitDown} 涨停 ${limitUp}（亏钱效应强）`)
        } else if (limitDown >= 15) {
            breadthScore -= 3
            breakdown.market_breadth.reasons.push(`跌停 ${limitDown}（亏钱效应）`)
        }
        // 上涨家数占比
        const totalMoving = up + down
        if (totalMoving > 100) {
            const upPct = up / totalMoving
            if (upPct >= 0.65)      { breadthScore += 4; breakdown.market_breadth.reasons.push(`${(upPct*100).toFixed(0)}% 个股上涨`) }
            else if (upPct >= 0.55) { breadthScore += 2 }
            else if (upPct <= 0.35) { breadthScore -= 4; breakdown.market_breadth.reasons.push(`仅 ${(upPct*100).toFixed(0)}% 个股上涨`) }
            else if (upPct <= 0.45) { breadthScore -= 2 }
        }
        breakdown.market_breadth.score = Math.max(0, Math.min(20, breadthScore))
        reasons.push(...breakdown.market_breadth.reasons.map(r => `[宽度] ${r}`))
    } else {
        breakdown.market_breadth.score = 10  // 无数据 → 中性
    }

    // === 3. 创业板相对强度 (15 分)：小盘风险偏好 ===
    if (gemKlines && gemKlines.length >= 20) {
        const gemCloses = gemKlines.map(k => +k.close)
        const csiCloses = csi300Klines.map(k => +k.close)
        // 近 20 日 GEM 涨跌幅 vs CSI300 涨跌幅
        const gemRet = (gemCloses[gemCloses.length - 1] - gemCloses[gemCloses.length - 20]) / gemCloses[gemCloses.length - 20]
        const csiRet = (csiCloses[csiCloses.length - 1] - csiCloses[csiCloses.length - 20]) / csiCloses[csiCloses.length - 20]
        const relStrength = gemRet - csiRet
        let gemScore = 7.5  // 中性
        if (relStrength > 0.03) {
            gemScore += 7.5
            breakdown.gem_relative.reasons.push(`创业板近 20 日跑赢沪深 300 +${(relStrength*100).toFixed(1)}%（风险偏好高）`)
        } else if (relStrength > 0.01) {
            gemScore += 3
        } else if (relStrength < -0.03) {
            gemScore -= 7.5
            breakdown.gem_relative.reasons.push(`创业板近 20 日跑输沪深 300 ${(relStrength*100).toFixed(1)}%（避险情绪）`)
        } else if (relStrength < -0.01) {
            gemScore -= 3
        }
        breakdown.gem_relative.score = Math.max(0, Math.min(15, gemScore))
        reasons.push(...breakdown.gem_relative.reasons.map(r => `[创业板] ${r}`))
    } else {
        breakdown.gem_relative.score = 7.5
    }

    // === 4. 量能 (15 分)：当日成交量 vs 20 日均，破位需要放量确认 ===
    const vols = csi300Klines.map(k => +k.vol || 0)
    const last = vols.length - 1
    if (last >= 20) {
        const curVol = vols[last]
        let avg20 = 0
        for (let i = last - 19; i <= last - 1; i++) avg20 += vols[i]
        avg20 /= 19
        const volRatio = avg20 > 0 ? curVol / avg20 : 1
        // 价格方向 × 量能：放量上涨加分 / 放量下跌减分 / 缩量上涨弱 / 缩量下跌中性
        const closes = csi300Klines.map(k => +k.close)
        const dayChange = closes[last] > 0 ? (closes[last] - closes[last - 1]) / closes[last - 1] : 0
        let volScore = 7.5
        if (dayChange > 0.005 && volRatio > 1.3) {
            volScore += 7.5
            breakdown.volume.reasons.push(`沪深 300 放量上涨（量比 ${volRatio.toFixed(2)}）`)
        } else if (dayChange < -0.005 && volRatio > 1.3) {
            volScore -= 7.5
            breakdown.volume.reasons.push(`沪深 300 放量下跌（量比 ${volRatio.toFixed(2)}，破位风险）`)
        } else if (dayChange > 0.005 && volRatio < 0.8) {
            volScore -= 3
            breakdown.volume.reasons.push(`沪深 300 缩量反弹（量比 ${volRatio.toFixed(2)}，乏力）`)
        }
        breakdown.volume.score = Math.max(0, Math.min(15, volScore))
        reasons.push(...breakdown.volume.reasons.map(r => `[量能] ${r}`))
    } else {
        breakdown.volume.score = 7.5
    }

    // === 汇总 ===
    const score = Math.round(
        breakdown.index_trend.score +
        breakdown.market_breadth.score +
        breakdown.gem_relative.score +
        breakdown.volume.score
    )

    // regime 分档（5 档）
    let regime, label
    if      (score >= 75) { regime = 'strong';     label = '强势' }
    else if (score >= 60) { regime = 'good';       label = '良好' }
    else if (score >= 40) { regime = 'neutral';    label = '震荡' }
    else if (score >= 25) { regime = 'weak';       label = '弱势' }
    else                  { regime = 'breakdown';  label = '破位' }

    return { score, regime, label, breakdown, reasons }
}

// ---------------- 各 detector 在不同 regime 下的阈值适配（Week 1 Day 2 用）----------------
/**
 * 给定 regime + 策略名，返回该策略在此 regime 下应使用的 detector 参数覆盖。
 *
 * 适配原则：
 *   strong（强势）：放宽 — 抓更多信号，反正大盘托底
 *   good：保持默认（已经是数据驱动调过的甜区参数）
 *   neutral：保持默认
 *   weak：收紧 — 只挑最优形态，提高胜率
 *   breakdown：阻断 — 三个进攻策略都不出信号，仅突破前夜可少量探仓
 */
export function getStrategyOverrides(strategy, regime) {
    if (!regime || regime === 'good' || regime === 'neutral') return {}

    const overrides = {}
    if (strategy === 'threeStage') {
        // 主升突破
        if (regime === 'strong') {
            overrides.trendMinRatio  = 1.3          // 1.50 → 1.30 放宽
            overrides.s2VolRatioMin  = 2.0          // 2.5 → 2.0 放宽
            overrides.s3VolRatioMax  = 7.0          // 6.0 → 7.0 容忍更暴量
        } else if (regime === 'weak') {
            overrides.trendMinRatio  = 1.7          // 1.50 → 1.70 收紧
            overrides.s2ShadowRatio  = 2.5          // 2.0 → 2.5 收紧（要更明显试盘）
            overrides.mlFilter       = 'lite'       // 弱势市场强制开 ML 过滤
            overrides.requireBreakoutConfirm = true // 弱势市强制 N+1 确认
            overrides.requireMA60Uptrend     = true // 弱势市必须中线趋势向上
            overrides.weeklyMA20Required     = true // 弱势市必须周线 MA20 向上
        } else if (regime === 'breakdown') {
            overrides.disabled       = true         // 大盘破位直接停用
        }
    } else if (strategy === 'breakoutEve') {
        // 突破前夜：弱势市场反而是埋伏机会（破位前低位筹码集中）
        if (regime === 'strong') {
            overrides.maxDistanceToBreakPct = 8     // 5% → 8% 放宽（强势市更多近端票）
        } else if (regime === 'weak') {
            overrides.maxDistanceToBreakPct = 3     // 5% → 3% 收紧（只挑最临门一脚的）
        }
        // breakdown 模式下突破前夜不停用 —— 反而是底部埋伏机会
    } else if (strategy === 'dragonReturn') {
        // 龙回头：需要市场情绪配合
        if (regime === 'strong') {
            overrides.signalVolRatio = 1.5          // 1.7 → 1.5 放宽
        } else if (regime === 'weak') {
            overrides.signalVolRatio = 2.0          // 1.7 → 2.0 收紧
            overrides.pullbackMax    = 25           // 30 → 25 收紧深回踩
        } else if (regime === 'breakdown') {
            overrides.disabled       = true
        }
    } else if (strategy === 'limitUpRelay') {
        // 连板游资：弱市赚钱效应消失，强市游资疯狂
        if (regime === 'weak' || regime === 'breakdown') {
            overrides.disabled       = true
        }
    }
    return overrides
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

// ---------------- 蓄势质量评分（Week 3 突破前夜深度优化）----------------
/**
 * 评估"蓄势态"的质量。优质蓄势 → 突破成功率高；劣质蓄势 → 突破多为假阳。
 *
 * 4 个维度（每个 0-25 分，合计 0-100）：
 *   amplitudeShrink: 后半段振幅 / 前半段振幅 < 0.85 → 收窄良好（25 分）
 *   volumeShrink:    后半段 5d 均量 / 前半段均量 < 0.7 → 量能萎缩（25 分）
 *   maAdhesion:      MA5/10/20 spread 持续收窄 → MA 粘合（25 分）
 *   probeFootprint:  突破前 10 天温和放量根数（2-4 根最优）→ 主力潜伏（25 分）
 */
function _computeConsolidationQuality(klines, s1Start, s1End) {
    if (s1End - s1Start < 15 || s1Start < 0 || s1End >= klines.length) {
        return { qualityScore: null, amplitudeShrink: null, volumeShrink: null, maAdhesion: null, probeFootprint: null }
    }
    const mid = Math.floor((s1Start + s1End) / 2)

    // 1) 振幅收窄
    let firstHi = -Infinity, firstLo = Infinity, secHi = -Infinity, secLo = Infinity
    for (let i = s1Start; i <= mid; i++) {
        const h = +klines[i].high, l = +klines[i].low
        if (h > firstHi) firstHi = h
        if (l < firstLo) firstLo = l
    }
    for (let i = mid + 1; i <= s1End; i++) {
        const h = +klines[i].high, l = +klines[i].low
        if (h > secHi) secHi = h
        if (l < secLo) secLo = l
    }
    const firstRange = firstHi - firstLo
    const secRange = secHi - secLo
    const ampRatio = firstRange > 0 ? secRange / firstRange : 1
    const amplitudeShrink = ampRatio < 0.85 ? 25 : ampRatio < 0.95 ? 15 : ampRatio < 1.05 ? 8 : 0

    // 2) 量能萎缩
    let firstVolSum = 0, firstVolCnt = 0, secVolSum = 0, secVolCnt = 0
    for (let i = s1Start; i <= mid; i++) {
        const v = +klines[i].vol
        if (v > 0) { firstVolSum += v; firstVolCnt++ }
    }
    for (let i = mid + 1; i <= s1End; i++) {
        const v = +klines[i].vol
        if (v > 0) { secVolSum += v; secVolCnt++ }
    }
    const fAvg = firstVolCnt > 0 ? firstVolSum / firstVolCnt : 0
    const sAvg = secVolCnt > 0 ? secVolSum / secVolCnt : 0
    const volRatio = fAvg > 0 ? sAvg / fAvg : 1
    const volumeShrink = volRatio < 0.7 ? 25 : volRatio < 0.85 ? 15 : volRatio < 1.0 ? 8 : 0

    // 3) MA 粘合（末端 5 天里 MA5/10/20 spread / median 的平均）
    const closes = klines.map(k => +k.close)
    const m5 = ma(closes, 5), m10 = ma(closes, 10), m20 = ma(closes, 20)
    let adhesionAccum = 0, adhesionCnt = 0
    for (let i = Math.max(s1End - 4, s1Start); i <= s1End; i++) {
        if (m5[i] == null || m10[i] == null || m20[i] == null) continue
        const median = (m5[i] + m10[i] + m20[i]) / 3
        const spread = Math.max(m5[i], m10[i], m20[i]) - Math.min(m5[i], m10[i], m20[i])
        if (median > 0) {
            adhesionAccum += spread / median
            adhesionCnt++
        }
    }
    const adhesion = adhesionCnt > 0 ? adhesionAccum / adhesionCnt : 1
    // spread/median 越小越粘合：< 2% = 25, < 4% = 15, < 6% = 8
    const maAdhesion = adhesion < 0.02 ? 25 : adhesion < 0.04 ? 15 : adhesion < 0.06 ? 8 : 0

    // 4) 突破前 10 天温和放量足迹（量比 1.3-2.0 的根数，2-4 根最优）
    let probeBars = 0
    for (let i = Math.max(s1End - 9, s1Start); i <= s1End; i++) {
        const vr = volumeRatio(klines, i, 5)
        if (vr != null && vr >= 1.3 && vr <= 2.0) probeBars++
    }
    const probeFootprint = probeBars >= 2 && probeBars <= 4 ? 25 : probeBars === 1 ? 15 : probeBars === 5 ? 12 : probeBars > 5 ? 5 : 0

    return {
        qualityScore: amplitudeShrink + volumeShrink + maAdhesion + probeFootprint,
        amplitudeShrink, volumeShrink, maAdhesion, probeFootprint,
    }
}

// ---------------- MACD 即将金叉预警（Week 3）----------------
/**
 * 蓄势末期 MACD 金叉前 1-5 根是最强埋伏信号。
 * 判定条件：
 *   - 当前 DIF < DEA（还没金叉）
 *   - DIF 上行 + DEA 上行 + (DIF - DEA) 在收敛
 *   - 红柱缩短或绿柱已转红初期
 * 返回 0-100 分（越接近金叉分越高）。
 */
function _computeMacdGoldenCrossImminence(closes, idx) {
    if (idx < 35) return 0
    const { dif, dea } = macd(closes)
    if (dif[idx] == null || dea[idx] == null) return 0
    // 已金叉 → 返 0（不再"即将"）
    if (dif[idx] > dea[idx]) return 0
    // 当前 gap
    const gap = dea[idx] - dif[idx]
    if (gap <= 0) return 0
    // 5 根前 gap
    if (dif[idx - 5] == null || dea[idx - 5] == null) return 0
    const gapPrev5 = dea[idx - 5] - dif[idx - 5]
    if (gapPrev5 <= gap) return 0  // gap 在扩大 → 远离金叉
    // gap 在收敛：缩多少？
    const shrinkRatio = gap / gapPrev5
    // shrinkRatio 越小越接近金叉
    if (shrinkRatio < 0.2) return 95   // 临门一脚
    if (shrinkRatio < 0.4) return 75
    if (shrinkRatio < 0.6) return 50
    if (shrinkRatio < 0.8) return 25
    return 10
}

// ---------------- 主力潜伏特征（Week 2 Day 2）----------------
/**
 * 突破前 N 天的"主力悄悄进场"留痕。
 * 这些信号比单纯 preBreakoutVolBars（计数 0-5）更精细，喂给 ML 期望显著提升 AUC。
 *
 * 返回 4 个连续型特征：
 *   volAcceleration:     突破前 5d 均量 / 前 6-20d 均量（>1.5 = 量能加速进场）
 *   upperShadowDensity:  突破前 10d 内 "上影≥2× 实体" 的根数（试盘密度）
 *   closeStability:      突破前 10d 收盘 std / mean（< 2% = 横盘锁筹）
 *   resistanceTests:     突破前 20d 冲击 s1Upper 但未站上 的次数（>=3 = 阻力坚实）
 */
function _computeLatentEntryFeatures(klines, s3Idx, s1Upper) {
    if (s3Idx < 21) {
        return { volAcceleration: null, upperShadowDensity: null, closeStability: null, resistanceTests: null }
    }
    const v5 = []
    const v6_20 = []
    for (let i = s3Idx - 5; i <= s3Idx - 1; i++)  v5.push(+klines[i].vol || 0)
    for (let i = s3Idx - 20; i <= s3Idx - 6; i++) v6_20.push(+klines[i].vol || 0)
    const mean = arr => arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : 0
    const std = (arr, m) => {
        if (!arr.length) return 0
        const sq = arr.reduce((s, x) => s + (x - m) * (x - m), 0) / arr.length
        return Math.sqrt(sq)
    }
    const v5Mean = mean(v5)
    const v6_20Mean = mean(v6_20)
    const volAcceleration = v6_20Mean > 0 ? v5Mean / v6_20Mean : null

    // upper shadow density：突破前 10 天
    let upperShadowDensity = 0
    for (let i = s3Idx - 10; i <= s3Idx - 1; i++) {
        if (i < 0) continue
        const k = klines[i]
        const open = +k.open, close = +k.close, high = +k.high
        const body = Math.abs(close - open)
        const upperShadow = high - Math.max(open, close)
        if (body > 0 && upperShadow >= body * 2) upperShadowDensity++
    }

    // close stability：突破前 10 天的收盘价 std / mean
    const closes10 = []
    for (let i = s3Idx - 10; i <= s3Idx - 1; i++) {
        if (i < 0) continue
        closes10.push(+klines[i].close || 0)
    }
    const cm = mean(closes10)
    const cs = std(closes10, cm)
    const closeStability = cm > 0 ? (cs / cm * 100) : null   // %，越小越锁筹

    // resistance tests：突破前 20 天里 high ≥ s1Upper 但 close < s1Upper 的次数
    let resistanceTests = 0
    if (s1Upper > 0) {
        for (let i = s3Idx - 20; i <= s3Idx - 1; i++) {
            if (i < 0) continue
            const k = klines[i]
            if (+k.high >= s1Upper * 0.995 && +k.close < s1Upper) resistanceTests++
        }
    }

    return {
        volAcceleration: volAcceleration != null ? +volAcceleration.toFixed(3) : null,
        upperShadowDensity,
        closeStability:   closeStability != null ? +closeStability.toFixed(3) : null,
        resistanceTests,
    }
}

// ---------------- N+1 突破确认 helper（Week 2 Day 1）----------------
/**
 * 突破 K 之后的 N+1 / N+2 行为决定突破真假。
 * 主力真出货 vs 真启动的核心差异：N+1 是否有承接、是否站稳、是否量能维持。
 *
 * 返回 'strong' | 'medium' | 'fail' | 'pending'。
 */
function _checkBreakoutConfirm(klines, s3Idx) {
    if (s3Idx + 1 >= klines.length) return 'pending'   // 突破在最末根，N+1 还没出现

    const s3K  = klines[s3Idx]
    const n1K  = klines[s3Idx + 1]
    const s3Open  = +s3K.open
    const s3Close = +s3K.close
    const s3High  = +s3K.high
    const s3Low   = +s3K.low
    const s3Mid   = (s3High + s3Low) / 2
    const s3Vol   = +s3K.vol || 0

    const n1Open  = +n1K.open
    const n1Close = +n1K.close
    const n1Low   = +n1K.low
    const n1Vol   = +n1K.vol || 0

    if (!(s3Close > 0) || !(n1Open > 0)) return 'pending'

    const gap = (n1Open - s3Close) / s3Close * 100
    const isRed   = n1Close > n1Open      // 收阳
    const isGreen = n1Close < n1Open      // 收阴

    // --- Fail 条件（最高优先级）---
    if (n1Low < s3Close * 0.97) return 'fail'                    // 盘中跌破突破 K -3%
    if (gap < -2) return 'fail'                                   // 大幅低开 ≥2%
    if (gap < 0 && isGreen && n1Close < s3Mid) return 'fail'     // 低开收阴破中点

    // --- Strong：高开 ≥1% + 收阳 + 不破中点 ---
    if (gap >= 1 && isRed && n1Low >= s3Mid) return 'strong'

    // --- Medium：平开~微高开 + 收阳 + 量能维持 ≥50% ---
    if (gap >= -0.5 && isRed && (s3Vol === 0 || n1Vol >= s3Vol * 0.5)) return 'medium'

    // 其它（含微低开收阴 / 收平）视为失败
    return 'fail'
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
        trendMinRatio     = 1.50,    // Phase 1 Day 2 grid search 结果：1.30 → 1.50
                                     // 192 组合 × 1000 只票回测显示 1.50 让 win% 从 44%→56%
        // Stage 2
        s2WithinBars      = 25,      // 15 → 25（蓄势结束到试盘可以更慢一点）
        s2VolRatioMin     = 2.5,
        s2ShadowRatio     = 2.0,     // Phase 1 Day 2 grid search 结果：1.5 → 2.0
                                     // 要求试盘 K 影线 ≥ 2 倍实体（更明显的射击之星 / 金针探底）
        // Stage 3
        s3WithinBars      = 30,      // 15 → 30（试盘后慢启动型常需 20-40 根才突破）
        s3VolRatioMin     = 1.3,
        s3VolRatioMax     = 6.0,     // 3.5 → 6.0：把爆量涨停（≈ 5-6x 量比）纳入识别范围
                                     // 旧值 3.5 会漏掉一字板 / T 字板这类强势启动 K
        s3BodyRatioMin    = 0.55,
        // 新鲜度
        freshWithinBars   = 30,
        // Phase 2 ML 衍生过滤（基于 378 样本 SHAP 分析，黑名单避开胜率<50%的危险区）
        // mlFilter=false / true / 'strict' / 'lite'
        //   'strict' (=true): 4 条规则 —— S2-S3 间隔 8-14 / S1 长度 41-66 / 下影 >16% / 涨幅 >10%
        //                     A/B 实测：胜率 +4.7pp, median +0.62pp, trades -70%（适合高确信清单）
        //   'lite': 3 条规则（去掉最弱的涨幅 >10%）—— 留更多 trades，胜率略低
        mlFilter          = false,
        // Week 2 Day 1：N+1 突破确认 —— 突破当日 +1/+2 根高开站稳才算"真突破"
        //   预期：胜率 +8-12pp，信号量 -30-40%（过滤大量假突破）
        //   strong / medium → 保留；fail → 抛弃；pending（数据不足，通常 = 突破在最末根）→ 保留（标 awaiting）
        requireBreakoutConfirm = false,
        // Week 2 Day 3：多周期共振过滤 —— 仅 Stage 3 应用
        //   requireMA60Uptrend = true：MA60 必须上行（突破当日 MA60 > 10 天前 MA60）
        //   weeklyKlines: 周 K（可选，由外部传入；不传则不做周线检查）
        //   weeklyMA20Required = true：周线 MA20 必须上行
        // 预期：胜率 +3-5pp，砍掉 20-30% 顶部反弹型假突破
        requireMA60Uptrend     = false,
        weeklyKlines           = null,
        weeklyMA20Required     = false,
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

        // === Phase 2 ML 黑名单过滤（仅对 Stage 3 已突破事件应用）===
        // 基于 378 样本 SHAP 分析得出的危险区规则；命中任一条直接抛弃事件
        if (mlFilter && s3Idx >= 0) {
            const s3K = klines[s3Idx]
            const s3Open = +s3K.open
            const s3Close = +s3K.close
            const s3High = +s3K.high
            const s3Low  = +s3K.low

            const s3BarsFromS2 = s3Idx - s2Idx
            const s1Length     = s1End - i + 1
            const range        = s3High - s3Low
            const lowerShadowPct = range > 0
                ? (Math.min(s3Open, s3Close) - s3Low) / range * 100
                : 0
            const breakoutChangePct = s3Open > 0
                ? (s3Close - s3Open) / s3Open * 100
                : 0

            // 三条核心规则（strict + lite 都启用）
            let inDanger =
                (s3BarsFromS2 >= 8 && s3BarsFromS2 <= 14) ||  // S2-S3 间隔死亡区
                (s1Length     >= 41 && s1Length     <= 66) || // 蓄势长度死亡区
                (lowerShadowPct > 16)                          // 突破K 下影线过长

            // strict 档额外加一条（lite 不加）
            if (!inDanger && mlFilter !== 'lite' && breakoutChangePct > 10) {
                inDanger = true   // 涨幅过猛（涨停冲高乏力）
            }
            if (inDanger) { i = s1End + 5; continue }
        }

        // === Week 2 Day 1：N+1 突破确认（仅 Stage 3 + 有 N+1 数据时启用）===
        let breakoutConfirm = null
        if (s3Idx >= 0) {
            breakoutConfirm = _checkBreakoutConfirm(klines, s3Idx)
            if (requireBreakoutConfirm && breakoutConfirm === 'fail') {
                i = s1End + 5; continue
            }
        }

        // === Week 2 Day 2：主力潜伏特征（仅 Stage 3 计算）===
        const latentEntry = s3Idx >= 0
            ? _computeLatentEntryFeatures(klines, s3Idx, s1Upper)
            : { volAcceleration: null, upperShadowDensity: null, closeStability: null, resistanceTests: null }

        // === Week 3：蓄势质量评分 + MACD 金叉预兆（突破前夜核心特征）===
        const consolidationQuality = _computeConsolidationQuality(klines, i, s1End)
        // MACD 金叉预兆：用 s1End（蓄势末端）作为索引，因为预兆是"即将突破"的信号
        const macdImminence = _computeMacdGoldenCrossImminence(closes, s1End)

        // === Week 2 Day 3：多周期共振过滤 ===
        // 日线 MA60 上行：中线趋势确认（避免日线漂亮但中线下行的反弹陷阱）
        if (requireMA60Uptrend && s3Idx >= 70) {
            // 算 MA60 当前 vs 10 天前
            let ma60Now = 0, ma60Then = 0
            for (let k = s3Idx - 59; k <= s3Idx; k++)         ma60Now  += +klines[k].close
            for (let k = s3Idx - 69; k <= s3Idx - 10; k++)    ma60Then += +klines[k].close
            ma60Now /= 60; ma60Then /= 60
            if (!(ma60Now > ma60Then * 1.002)) {   // 至少 +0.2% 才算上行
                i = s1End + 5; continue
            }
        }
        // 周线 MA20 上行（外部传入周 K）
        if (weeklyMA20Required && Array.isArray(weeklyKlines) && weeklyKlines.length >= 25) {
            const wcloses = weeklyKlines.map(k => +k.close)
            const wmaArr  = ma(wcloses, 20)
            const lastIdxW = wmaArr.length - 1
            if (lastIdxW >= 5 && wmaArr[lastIdxW] != null && wmaArr[lastIdxW - 4] != null) {
                if (!(wmaArr[lastIdxW] > wmaArr[lastIdxW - 4] * 1.002)) {
                    i = s1End + 5; continue
                }
            }
        }

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
            // Week 2 Day 1：N+1 突破确认结果（'strong'|'medium'|'fail'|'pending'|null）
            breakoutConfirm,
            // Week 2 Day 2：主力潜伏特征（4 维）
            ...latentEntry,
            // Week 3：蓄势质量评分（突破前夜的核心信号）+ MACD 金叉预兆
            consolidationQuality: consolidationQuality.qualityScore,
            consolidationBreakdown: consolidationQuality,
            macdImminence,
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
        // Phase 1 Day 2 实验 B: MA10 出场触发模式
        //   'strict'     默认 —— close < MA10 立即触发（原行为）
        //   'buffered'   close < MA10 × 0.98 才触发（2% 缓冲，过滤微跌噪音）
        //   'confirmed'  连续 2 天 close < MA10 才触发（避免单日插针误杀）
        //   'momentum'   close < MA10 且当天跌幅 > 2% 才触发（小阴线放过，大阴线出）
        exitMode        = 'strict',
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

    // MA10 出场判定 —— 按 exitMode 切换
    function _ma10Triggered(t, close) {
        const ma10 = ma10Arr[t]
        if (ma10 == null) return false
        if (exitMode === 'buffered') {
            return close < ma10 * 0.98
        }
        if (exitMode === 'confirmed') {
            // 当天 + 前一天都跌破 MA10
            const prevClose = +klines[t - 1]?.close
            const prevMa10  = ma10Arr[t - 1]
            return close < ma10 && prevClose < prevMa10
        }
        if (exitMode === 'momentum') {
            // 跌破 MA10 同时当天跌幅 > 2%（小阴放过，大阴出）
            const prevClose = +klines[t - 1]?.close
            const pct = prevClose > 0 ? (close - prevClose) / prevClose * 100 : 0
            return close < ma10 && pct < -2
        }
        // 'strict' 默认
        return close < ma10
    }

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
        if (_ma10Triggered(t, close) && t >= start + protectBars) {
            out.push({ idx: t, time: k.time, level: 'exit', reason: `跌破MA10(${exitMode})` })
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
        asOfIdx            = null,  // 回测滑窗用：把 klines[0..asOfIdx] 当作"截至当时"
        // Week 3 龙回头深度优化：
        // 1. 位置因子 —— 必须在 MA200 上方 + 距 60 日新高 < 30%（防"长跌中的反弹陷阱"）
        requirePositionOk  = false,
        // 2. 探底试盘 K —— 回踩末期出现"长下影 + 站稳" K 线（探底成功信号）
        requireProbeProbe  = false,
    } = opts

    if (!Array.isArray(klines)) return null
    const n = (asOfIdx != null && asOfIdx >= 0) ? Math.min(asOfIdx + 1, klines.length) : klines.length

    // 数据长度：至少要装下 search 窗口 + 稳定区 + IPO 过滤
    const minLen = Math.max(minIpoBars, peakMaxBarsAgo + clusterWindow + stabilizeBars + 5)
    if (n < minLen) return null

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

    // 1.1 连板软化约束：cluster 内必须存在"紧密簇" —— consecutiveWindow 个交易日内含 ≥2 涨停
    // 涵盖：连板（紧邻 1 天）/ 涨停-横盘-涨停（2-3 天内）
    // 过滤掉：涨停-跌停-涨停-横盘 5 天-涨停 这类零散反复票
    //
    // 用日历日差代替索引差，避免「停牌前涨停 + 停牌后涨停」被误判为紧密簇。
    // 阈值映射：consecutiveWindow=3 个交易日 ≈ 最多 7 日历日（含周末跨越）
    const tightClusterMaxDays = consecutiveWindow * 2 + 1   // 3 → 7 日历日
    const hasTightCluster = clusterIndices.some((idx, i) => {
        if (i === 0) return false
        const days = calendarDaysBetween(klines, clusterIndices[i - 1], idx)
        return days != null && days <= tightClusterMaxDays
    })
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

    // ===== Week 3 龙回头深度优化：位置因子 + 探底试盘 K + 回踩深度甜区 =====
    // 1. 位置因子：现价必须在 MA200 上方 + 距 60 日新高 < 30%
    let positionOk = null, ma200 = null, distFrom60dHigh = null
    if (ignitionIdx >= 200) {
        let sum200 = 0
        for (let k = ignitionIdx - 199; k <= ignitionIdx; k++) sum200 += +klines[k].close
        ma200 = sum200 / 200
        let hi60 = -Infinity
        for (let k = Math.max(0, ignitionIdx - 59); k <= ignitionIdx; k++) {
            if (+klines[k].high > hi60) hi60 = +klines[k].high
        }
        distFrom60dHigh = hi60 > 0 ? (hi60 - lastClose) / hi60 * 100 : null
        positionOk = (lastClose > ma200) && (distFrom60dHigh != null && distFrom60dHigh < 30)
        if (requirePositionOk && !positionOk) return null
    }

    // 2. 探底试盘 K：回踩末期出现"长下影 + 站稳"信号（探底成功）
    // 检查启动 K 前 5-10 天里：close < 前 5 天 mean BUT close > open（站稳）AND lower shadow >= 2× body
    let probeOk = false, probeIdx = -1
    for (let t = Math.max(0, ignitionIdx - 10); t < ignitionIdx; t++) {
        const k = klines[t]
        const open = +k.open, close = +k.close, high = +k.high, low = +k.low
        if (close <= open) continue   // 必须收阳
        const body = Math.abs(close - open)
        const lowerShadow = Math.min(open, close) - low
        if (body <= 0 || lowerShadow < body * 2) continue   // 长下影
        // 当日 low 应低于前 5 天 low 的均值（探底）
        let sumPrev5Low = 0, cnt = 0
        for (let k2 = Math.max(0, t - 5); k2 < t; k2++) {
            sumPrev5Low += +klines[k2].low; cnt++
        }
        const avgPrev5Low = cnt > 0 ? sumPrev5Low / cnt : 0
        if (low < avgPrev5Low) { probeOk = true; probeIdx = t; break }
    }
    if (requireProbeProbe && !probeOk) return null

    // 3. 回踩深度甜区评分（0-100，非线性）
    //    12-18%: 100（强势龙浅回踩，最优）
    //    18-25%: 70（标准回踩）
    //    25-30%: 40（深回踩，赔率高但成功率低）
    //    < 12% / > 30%: 已过滤（pullbackMin/Max 限制）
    let pullbackQuality = 0
    if (pullbackPct >= 12 && pullbackPct < 18) pullbackQuality = 100
    else if (pullbackPct >= 18 && pullbackPct < 25) pullbackQuality = 70
    else if (pullbackPct >= 25 && pullbackPct <= 30) pullbackQuality = 40
    else pullbackQuality = 25   // 在范围内但非甜区

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
        pullbackQuality,           // Week 3 新增：回踩深度甜区评分
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
        // Week 3 位置因子
        positionOk, ma200,
        distFrom60dHigh,
        // Week 3 探底试盘 K
        probeOk, probeIdx,
        probeTime:        probeIdx >= 0 ? klines[probeIdx].time : null,
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

    // 日历日相邻校验：防止「停牌-涨停-停牌-涨停」被误判为连板
    // TDX 不返停牌日 bar，所以数组里相邻两根可能日历相隔一个月
    const todayPrevDays = calendarDaysBetween(klines, lastIdx - 1, lastIdx)
    const isCalendarAdjacent = todayPrevDays != null && todayPrevDays <= 4
    const prevPrev2Days = calendarDaysBetween(klines, lastIdx - 2, lastIdx - 1)
    const isPrevCalendarAdjacent = prevPrev2Days != null && prevPrev2Days <= 4

    // ---------------- 三种形态识别 ----------------
    // A) 连板接力：今天涨停 + 昨天涨停 + 日历相邻
    const modeA = isZt0 && isZt1 && isCalendarAdjacent
    // B) 反包确认：今天涨停 + 昨天未涨停 + 昨日相对前日跌幅 < 8% + 昨天和前天日历相邻
    const modeB = isZt0 && !isZt1 && c2 > 0 && (c1 / c2) > 0.92 - EPSILON
                  && isCalendarAdjacent && isPrevCalendarAdjacent
    // C) 摸板试盘：今天 high 触涨停但收盘未封 + 收涨 + 上影线 < 5% + 日历相邻
    const touchZt0 = h0 >= limit0 - 0.01 - EPSILON
    const upperShadow = c0 > 0 ? (h0 - c0) / c0 : 999
    const modeC = touchZt0 && !isZt0 && c0 > c1 && upperShadow < 0.05 && isCalendarAdjacent

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

    // ---------------- 连板高度 + 5 天板数 ----------------
    // height: 从最新一根 K 往前数连续涨停，必须满足两个条件:
    //   a) 当前是涨停（isZt = true，含 vol > 0）
    //   b) 与上一根计入的涨停日历相邻（≤4 日，跨周末容忍）
    // 这样防止「停牌前涨停 + 停牌后涨停」被算成连板
    let height = 0
    if (isZt[lastIdx]) {
        height = 1
        let prevIdx = lastIdx
        for (let i = lastIdx - 1; i >= 0; i--) {
            if (!isZt[i]) break   // 非涨停 / 停牌（vol=0）→ 断
            const days = calendarDaysBetween(klines, i, prevIdx)
            if (days == null || days > 4) break   // 日历跳跃 > 4 天 = 停牌→恢复，不算连板
            height++
            prevIdx = i
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

    // 今日涨幅 = (今日收盘 - 昨日收盘) / 昨日收盘 × 100
    // 用来区分"实封涨停"（接近 limit%）vs"摸板回落"（pct 明显低于 limit）
    const pctChange = c1 > 0 ? (c0 - c1) / c1 * 100 : null

    return {
        modes,           // ['A', 'B', ...]
        reason,          // '连板接力+反包确认' 等
        stat,            // '3连板' / '首板' / '断板'
        height,          // 连板高度
        boards5,         // 近 5 有效交易日板数
        isST,
        // 价格状态
        lastClose:   c0,
        prevClose:   c1,
        limitPrice:  limit0,
        pctChange,       // 今日涨幅 %（已是百分数，非小数）
        hhv20,
        // 形态标记（给 UI 排序/过滤）
        modeA, modeB, modeC,
    }
}


// ============== 横盘跳空检测器（detectStretchedRally）==============
//
// 跟 detectThreeStageLaunch 互补 —— 专抓「长时间横盘 → 直接跳空突破」型。
// 共同点：都要求 S1 蓄势期。
// 差异：不要求 S2 试盘（detectThreeStageLaunch 要求长上影/长下影 K 作试盘验证）。
//
// 抓的典型形态：大唐发电那种"横盘 4 个月 → 直接放量拉涨停"，
// 三维启动会因"无试盘 K"而漏掉这种，本 detector 兜底。
//
// 判定（AND）：
//   1. 最近 recencyBars 根内有一根突破 K
//   2. 突破 K 之前有 ≥ consolidationMinBars 根的横盘期（振幅 ≤ 20%，75% 时间在区间内）
//   3. 突破 K 收盘 > 横盘上沿 × breakoutPriceMargin
//   4. 突破 K 量能 > 横盘期均量 × breakoutVolMultiplier
//
// @param {Array}  klines  日 K 数组
// @param {Object} opts    可调参数
// @returns {Object|null}  匹配则返回 event，否则 null
export function detectStretchedRally(klines, opts = {}) {
    const {
        consolidationMinBars     = 30,    // 横盘最少 30 根（≈ 6 周交易日）
        consolidationMaxBars     = 250,   // 横盘最长 250 根（≈ 1 年，比三维启动 120 更宽）
        consolidationMaxRangePct = 0.20,  // 横盘振幅 ≤ 20%（比三维启动 15% 更宽容）
        inRangeRatio             = 0.75,  // 75% 时间在范围内
        recencyBars              = 30,    // 突破 K 必须在最近 N 根内（不要老突破）
        breakoutPriceMargin      = 1.03,  // close > 横盘上沿 × 1.03 才算确认突破
        breakoutVolMultiplier    = 1.3,   // 量比 ≥ 1.3 vs 横盘均量（2.0→1.3：包容温和放量启动）
        breakoutMinPct           = 5.0,   // 单日涨幅 ≥ 5% 作兜底门槛（防止"温和放量微涨"假阳）
                                          // 量比 + 涨幅 都满足才算真突破 K
        maxLiftOffBars           = 7,     // 横盘期到突破 K 之间允许 N 根"预备爬升 K"
                                          // 大唐发电这种"前 4 月横盘 + 最近 3-5 天爬升 + 今天涨停"
                                          // 用这个允许 cEnd 往前推几根，匹配到真正的横盘段
        debug                    = false, // 返回未命中原因（仅诊断用）
        asOfIdx                  = null,  // 回测滑窗用：把 klines[0..asOfIdx] 当作"截至当时"
                                          // 避免外部 .slice() 大数组复制带来的性能开销
        consolidationStep        = 5,     // cLen 循环步长（回测可加大到 15 提速 3x）
        // ↓ 质量过滤器（默认关，回测验证用；开启后预期胜率提升 + trade 数减少）
        requireMA250Uptrend      = false, // 突破 K close 必须 > MA250（过滤长期下跌中的横盘）
        strictBreakoutVol        = false, // 启动量比 ≥ 2.5×（默认 1.3 太松，强化要求）
    } = opts
    // 调试用：记录最近一次"差点命中"的卡点原因
    const lastReason = { stage: 'no-loop', detail: null }

    if (!Array.isArray(klines)) return null
    const n = (asOfIdx != null && asOfIdx >= 0) ? Math.min(asOfIdx + 1, klines.length) : klines.length
    if (n < consolidationMinBars + 5) return null
    const lastIdx = n - 1
    const EPS = 1e-9

    // 从最近往前找突破 K（recencyBars 内）。一旦找到合格的，立即返回。
    for (let breakIdx = lastIdx; breakIdx >= Math.max(0, lastIdx - recencyBars + 1); breakIdx--) {
        if (breakIdx < consolidationMinBars) { lastReason.stage = 'no-room'; continue }

        const breakK = klines[breakIdx]
        const breakClose = +breakK.close
        const breakVol   = +breakK.vol
        if (!(breakClose > 0) || !(breakVol > 0)) { lastReason.stage = 'bad-data'; continue }

        // 双层循环：先固定 cEnd（横盘期结束位置），再变 cLen（横盘期长度）
        //   cEndOffset = 1 → cEnd = breakIdx - 1（横盘紧贴突破 K，传统语义）
        //   cEndOffset = 7 → cEnd = breakIdx - 7（允许中间 7 天预备爬升 K）
        // 优先小 offset（横盘越靠近突破越好），再优先大 cLen（横盘越长越有信心）
        let _hit = null
        for (let cEndOffset = 1; cEndOffset <= maxLiftOffBars && !_hit; cEndOffset++) {
        for (let cLen = consolidationMaxBars; cLen >= consolidationMinBars; cLen -= consolidationStep) {
            const cEnd   = breakIdx - cEndOffset
            const cStart = cEnd - cLen + 1
            if (cStart < 0 || cEnd < cStart) continue

            // 算横盘期数据 + 收集 close（只用收盘价做横盘判定，跟三维启动 S1 哲学一致）
            // —— 不再用 high/low，避免带长影线的少数日子把范围炸大
            const closes = []
            let volSum = 0, volCnt = 0
            for (let t = cStart; t <= cEnd; t++) {
                const v = +klines[t].vol, c = +klines[t].close
                if (Number.isFinite(c) && c > 0) closes.push(c)
                if (Number.isFinite(v) && v > 0) { volSum += v; volCnt++ }
            }
            if (closes.length < cLen * 0.7) { lastReason.stage = 'data-gap'; lastReason.detail = { cLen }; continue }

            // 用收盘价的 95/5 分位作横盘上下沿（filter 出极端日的 close 也已经够稳）
            // 跟三维启动 detectThreeStageLaunch 内部 closes-median 判定哲学一致
            const closesSorted = closes.slice().sort((a, b) => a - b)
            const cUpperPctile = closesSorted[Math.floor(closesSorted.length * 0.95)]
            const cLowerPctile = closesSorted[Math.floor(closesSorted.length * 0.05)]
            const median = closesSorted[Math.floor(closesSorted.length / 2)]
            const rangePct = (cUpperPctile - cLowerPctile) / median
            if (!(median > 0) || rangePct > consolidationMaxRangePct) {
                lastReason.stage = 'range-too-wide'
                lastReason.detail = { cLen, rangePct: +rangePct.toFixed(4), limit: consolidationMaxRangePct }
                continue
            }

            // 75% 时间收盘价在 ±半振幅 内（基于中位数，剔除极端波动）
            const half = consolidationMaxRangePct / 2
            let inRange = 0
            for (const c of closes) {
                if (Math.abs(c - median) / median <= half + EPS) inRange++
            }
            const ratio = inRange / closes.length
            if (ratio < inRangeRatio) {
                lastReason.stage = 'in-range-ratio-low'
                lastReason.detail = { cLen, ratio: +ratio.toFixed(3), need: inRangeRatio }
                continue
            }

            // 突破 K 价格校验（突破 95 分位上沿 × margin，不用 raw max 避免影线干扰）
            const upperBreak = cUpperPctile * breakoutPriceMargin
            if (breakClose < upperBreak) {
                lastReason.stage = 'price-not-above-margin'
                lastReason.detail = { cLen, breakClose, cUpper: +cUpperPctile.toFixed(2), need: +upperBreak.toFixed(2) }
                continue
            }

            // 防"老平台延续上涨"误判：
            // 横盘期结束 cEnd 之后到突破 K 之前，如果已经有 K 收盘 > upperBreak
            // 那就是早就破了，今天的"突破"是趋势延续，不是真·横盘跳空。
            let alreadyBrokenIdx = -1
            for (let t = cEnd + 1; t < breakIdx; t++) {
                const c = +klines[t].close
                if (Number.isFinite(c) && c > upperBreak) {
                    alreadyBrokenIdx = t
                    break
                }
            }
            if (alreadyBrokenIdx >= 0) {
                lastReason.stage = 'already-broken-earlier'
                lastReason.detail = { cLen, brokenAt: alreadyBrokenIdx, upperBreak: +upperBreak.toFixed(2) }
                continue
            }

            // 突破 K 量能校验
            const avgVol = volCnt > 0 ? volSum / volCnt : 0
            if (!(avgVol > 0)) { lastReason.stage = 'no-avg-vol'; continue }
            const volRatio = breakVol / avgVol
            const effectiveVolMin = strictBreakoutVol ? Math.max(breakoutVolMultiplier, 2.5) : breakoutVolMultiplier
            if (volRatio < effectiveVolMin) {
                lastReason.stage = 'vol-too-low'
                lastReason.detail = { cLen, volRatio: +volRatio.toFixed(2), need: effectiveVolMin }
                continue
            }

            // 突破 K 单日涨幅校验（兜底，防"温和放量微涨"假阳）
            const prevClose = breakIdx > 0 ? +klines[breakIdx - 1].close : null
            const breakPct = prevClose > 0 ? (breakClose - prevClose) / prevClose * 100 : 0
            if (breakPct < breakoutMinPct) {
                lastReason.stage = 'pct-too-low'
                lastReason.detail = { cLen, breakPct: +breakPct.toFixed(2), need: breakoutMinPct }
                continue
            }

            // MA250 长期趋势过滤（可选，过滤"长期下跌中的横盘"假信号）
            // 突破 K 收盘必须 > 突破日的 MA250（即处在长期上升通道里）
            if (requireMA250Uptrend && breakIdx >= 250) {
                let ma250Sum = 0
                for (let k = breakIdx - 249; k <= breakIdx; k++) {
                    ma250Sum += +klines[k].close
                }
                const ma250 = ma250Sum / 250
                if (breakClose < ma250) {
                    lastReason.stage = 'below-ma250'
                    lastReason.detail = { cLen, breakClose, ma250: +ma250.toFixed(2) }
                    continue
                }
            }

            // 全部条件通过 —— 命中（写到 _hit 后双层 break）
            _hit = {
                // 横盘期信息（用百分位上下沿，跟三维启动 s1Upper/s1Lower 同语义）
                cStartIdx:  cStart,
                cEndIdx:    cEnd,
                cStartTime: klines[cStart].time,
                cEndTime:   klines[cEnd].time,
                cUpper:     cUpperPctile,
                cLower:     cLowerPctile,
                consolidationBars: cLen,
                liftOffBars: cEndOffset - 1,   // 横盘期与突破 K 之间隔的预备爬升 K 数
                // 突破 K
                breakIdx,
                breakTime:  breakK.time,
                breakClose,
                breakVol,
                volRatio,
                // 距今几根
                barsAgoFromBreak: lastIdx - breakIdx,
                // 跟三维启动 event 兼容的字段（让上游 secondary_entry / breakout_at 复用）
                currentStage: 3,            // 概念上等价 S3 已突破
                s3Idx:        breakIdx,
                s3Time:       breakK.time,
                s1Upper:      cUpperPctile,
                s1Lower:      cLowerPctile,
                breakoutPrice: breakClose,
                stopLossPrice: cLowerPctile * 0.97,
            }
            break   // 跳出 cLen 循环
        }
        }   // 闭合 cEndOffset 循环
        if (_hit) return _hit   // 跳出 breakIdx 循环
    }

    // 未命中：debug 模式返回卡点原因（不影响生产代码 —— 没传 debug:true 仍然 return null）
    return debug ? { _missed: true, lastReason } : null
}

