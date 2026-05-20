/**
 * 三维启动回测引擎（Phase 1 Step 1.1）
 *
 * 核心问题：在历史 K 线上跑 detectThreeStageLaunch，对每个 Stage 3 突破模拟
 * "次日开盘买入 + 30 天固定平仓 / 跌破止损先生效"，输出 trades 列表 + 统计。
 *
 * 关键设计决策（与用户 2026-05-07 对齐）：
 *   - 不每天重跑 detector：detector 是 deterministic 的，一次跑出所有 events，
 *     每个 event 的 s3Idx 就是历史上"那天会被扫到"的精确时点。
 *   - 入场：s3Idx + 1 当天开盘价（"信号当晚收盘后扫到，次日开盘买入"）
 *   - 离场：进入持有期后，逐根检查收盘价 < stopLossPrice → 立刻止损出；
 *           否则持有满 holdDays 根，按当天收盘平仓。
 *   - 评级（gradeFreshSignal）：把 klines 截到 s3Idx + 1，让评级用的是
 *     "突破当天"的状态，而不是数据末尾的状态。否则评级泄漏未来信息。
 *   - marketEnv：MVP 不传（envScore = 中性 50），后续再扩展按日期查询指数状态。
 */
import { detectThreeStageLaunch, gradeFreshSignal, detectRallyExhaustion, hasWeeklyTrendConfirmation, volumeRatio, detectStretchedRally } from './useTechIndicators'

// ============ Phase 2 ML 特征提取（dumpFeatures 模式下使用）============
// 每个 S3 触发抽出一条特征向量 + 多 horizon 标签，喂给 LightGBM / SHAP。
// 设计原则：
//   - 全部用 s3Idx 当下能算到的信息，绝不泄漏未来
//   - 维度尽量解耦：detector 内部、价格上下文、量能、形态、日历分别建栏
//   - null 安全：缺数据返回 null，不强行填 0（LightGBM 原生支持 NaN）
function _sma(klines, idx, period) {
    if (idx < period - 1) return null
    let sum = 0
    for (let i = idx - period + 1; i <= idx; i++) sum += +klines[i].close
    return sum / period
}
function _avgVol(klines, idx, period) {
    if (idx < period) return null
    let sum = 0, n = 0
    for (let i = idx - period; i < idx; i++) {
        const v = +klines[i].vol
        if (v > 0) { sum += v; n++ }
    }
    return n ? sum / n : null
}
function _atr(klines, idx, period = 20) {
    if (idx < period) return null
    let sum = 0
    for (let i = idx - period + 1; i <= idx; i++) {
        const h = +klines[i].high, l = +klines[i].low
        const pc = i > 0 ? +klines[i - 1].close : +klines[i].open
        const tr = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc))
        sum += tr
    }
    return sum / period
}
function _countLimitUps(klines, fromIdx, toIdx) {
    // 粗略：单日涨幅 >= 9.5% 视为涨停（不区分主板 10% / 创业板 20%）
    let count = 0, lastIdx = -1
    for (let i = fromIdx; i <= toIdx && i < klines.length; i++) {
        if (i === 0) continue
        const prevClose = +klines[i - 1].close
        const curClose  = +klines[i].close
        if (prevClose > 0 && (curClose - prevClose) / prevClose >= 0.095) {
            count++
            lastIdx = i
        }
    }
    return { count, lastIdx }
}

/**
 * 抽取 S3 突破当下的特征向量（ML 训练用）。
 *
 * 32 维特征 + 6 维标签。所有特征基于 evt.s3Idx 当根及之前的数据，无未来泄漏。
 */
export function extractTradeFeatures(klines, evt, weeklyConfirmed, grade) {
    const s3Idx = evt.s3Idx
    const breakK = klines[s3Idx]
    const close = +breakK.close
    const open  = +breakK.open
    const high  = +breakK.high
    const low   = +breakK.low

    // === MA 距离 ===
    const ma20  = _sma(klines, s3Idx, 20)
    const ma50  = _sma(klines, s3Idx, 50)
    const ma250 = _sma(klines, s3Idx, 250)

    // === 量能 ===
    const v5  = _avgVol(klines, s3Idx, 5)
    const v20 = _avgVol(klines, s3Idx, 20)
    const v60 = _avgVol(klines, s3Idx, 60)
    const s2Vol = evt.s2Idx >= 0 ? volumeRatio(klines, evt.s2Idx, 5) : null
    const s3Vol = volumeRatio(klines, s3Idx, 5)

    // === 波动率 ===
    const atr20 = _atr(klines, s3Idx, 20)

    // === 形态 ===
    const range = high - low
    const body  = Math.abs(close - open)
    const upperShadow = high - Math.max(open, close)
    const lowerShadow = Math.min(open, close) - low

    // === 阶段时序 ===
    const s1Length     = evt.s1EndIdx - evt.s1StartIdx + 1
    const s2BarsFromS1 = evt.s2Idx >= 0 ? evt.s2Idx - evt.s1EndIdx : -1
    const s3BarsFromS2 = evt.s2Idx >= 0 && s3Idx >= 0 ? s3Idx - evt.s2Idx : -1

    // === S1 振幅与位置 ===
    const s1Amplitude = evt.s1Upper && evt.s1Lower
        ? (evt.s1Upper - evt.s1Lower) / evt.s1Lower * 100
        : null
    const breakoutVsS1Upper = evt.s1Upper ? (close - evt.s1Upper) / evt.s1Upper * 100 : null

    // === 涨停历史 ===
    const limitUpWindow = _countLimitUps(klines, Math.max(0, s3Idx - 30), s3Idx)
    const daysFromLastLU = limitUpWindow.lastIdx >= 0 ? s3Idx - limitUpWindow.lastIdx : -1

    // === 日历 ===
    let dow = -1, month = -1
    if (evt.s3Time) {
        const d = new Date(evt.s3Time.replace(/-/g, '/'))
        if (!isNaN(d)) { dow = d.getDay(); month = d.getMonth() + 1 }
    }

    return {
        // ---- 标识 ----
        s3Time: evt.s3Time,
        signalSource: evt.signalSource || 'threeStage',   // Phase 3：多 detector 标识
        sectorStrength: null,    // Week 1 Day 3-5：scanner 外部注入；extractTradeFeatures 不直接拉
        marketRegime:   null,    // Week 1 Day 1：scanner 外部注入；'strong'|'good'|'neutral'|'weak'|'breakdown'
        // P2 龙虎榜（scanner 外部注入；ML 训练时按 s3Time 时点查询，无未来泄漏）
        lhbInWindow:        null,    // 0/1 30 天窗口内是否上龙虎榜
        lhbCount:           null,    // 30 天上榜次数
        lhbNetBuySum:       null,    // 30 天累计净买额（元，正=机构净买）
        daysSinceLastLhb:   null,    // 距上次上榜天数
        breakoutConfirm: evt.breakoutConfirm ?? null,    // Week 2 Day 1：'strong'|'medium'|'fail'|'pending'
        // Week 2 Day 2：主力潜伏特征（4 维）
        volAcceleration:    evt.volAcceleration    ?? null,
        upperShadowDensity: evt.upperShadowDensity ?? null,
        closeStability:     evt.closeStability     ?? null,
        resistanceTests:    evt.resistanceTests    ?? null,
        // ---- detector 内部 ----
        s1Length,
        s1Amplitude,
        s2BarsFromS1,
        s3BarsFromS2,
        s2Type: evt.s2Type,        // 'upperShadow' | 'lowerShadow'
        s2VolRatio: s2Vol,
        s3VolRatio: s3Vol,
        preBreakoutVolBars: evt.preBreakoutVolBars ?? null,
        breakoutVsS1Upper,
        // ---- 评级 ----
        gradeLetter: grade?.grade ?? null,
        gradeScore:  grade?.score ?? null,
        // ---- 周线 ----
        weeklyConfirmed: weeklyConfirmed === true ? 1 : weeklyConfirmed === false ? 0 : null,
        // ---- 价格上下文（MA 距离 %）----
        ma20Dist:  ma20  ? (close - ma20)  / ma20  * 100 : null,
        ma50Dist:  ma50  ? (close - ma50)  / ma50  * 100 : null,
        ma250Dist: ma250 ? (close - ma250) / ma250 * 100 : null,
        // ---- 量能 ----
        vol5dMean:  v5,
        vol20dMean: v20,
        vol60dMean: v60,
        volRatio5v20:  v5 && v20 ? v5 / v20  : null,
        volRatio5v60:  v5 && v60 ? v5 / v60  : null,
        // ---- 波动率 ----
        atr20Pct: atr20 && close > 0 ? atr20 / close * 100 : null,
        // ---- 突破 K 形态（百分比，去价格量纲）----
        breakoutBodyPct:        range > 0 ? body        / range * 100 : null,
        breakoutUpperShadowPct: range > 0 ? upperShadow / range * 100 : null,
        breakoutLowerShadowPct: range > 0 ? lowerShadow / range * 100 : null,
        breakoutChangePct:      open  > 0 ? (close - open) / open * 100 : null,
        // ---- 涨停背景（近 30 日）----
        limitUps30d: limitUpWindow.count,
        daysFromLastLimitUp: daysFromLastLU,
        // ---- 日历 ----
        dayOfWeek: dow,
        month:     month,
    }
}

/**
 * 单股回测：对每个 Stage 3 突破模拟"次日开盘买 + 30 天平仓 / 跌破止损先生效"。
 *
 * @param {Array}  klines   日 K 线数组，至少 100 根
 * @param {Object} opts
 * @param {number} opts.holdDays      固定持有根数，默认 7（数据驱动：7/30/60 三方对比下
 *                                    7 天胜率最高，C 级 62% / B 级 49% / A 级 40%；持有越久
 *                                    mean reversion 风险越大，30/60 天几乎一致且胜率偏低）
 * @param {Object} opts.detectOpts    透传给 detectThreeStageLaunch
 * @param {Object} opts.marketEnv     可选；不传则评级里 envScore = 中性 50
 * @param {Array}  opts.weeklyKlines  可选；传入则给每笔 trade 打 weeklyConfirmed 字段
 *                                    （Phase 4：评估周线确认对胜率的影响）
 * @returns {{ events, trades }}
 */
export function backtestStock(klines, opts = {}) {
    const {
        holdDays      = 7,
        detectOpts    = {},
        marketEnv     = null,
        weeklyKlines  = null,
        // Phase 1 Day 2 实验 B：MA10 出场触发模式
        // 'strict' | 'buffered' | 'confirmed' | 'momentum'（见 detectRallyExhaustion）
        exitMode      = 'strict',
        // Phase 2 ML：true 时给每笔 trade 附 features + 多 horizon 标签
        dumpFeatures  = false,
    } = opts

    // 周线确认 — 全 trade 共享（周线状态是当前快照，跟单 trade 时点关系不大）
    // 注意：严格说回测应该用突破当周的状态，但周线变化慢，近似用最新状态可接受
    const weeklyConfirmed = weeklyKlines ? hasWeeklyTrendConfirmation(weeklyKlines) : null

    if (!Array.isArray(klines) || klines.length < 100) {
        return { events: [], trades: [] }
    }

    // Week 2 Day 3：把 weeklyKlines 传给 detector，让 weeklyMA20Required 能用上
    const events = detectThreeStageLaunch(klines, { ...detectOpts, weeklyKlines })
    const stage3Events = events.filter(e => e.currentStage === 3 && e.s3Idx >= 0)

    const trades = []
    for (const evt of stage3Events) {
        const entryIdx = evt.s3Idx + 1
        // 突破在数据最后一根 / 倒数第二根：没法次日开盘买（数据缺失）→ 跳过
        if (entryIdx >= klines.length) continue

        const entryBar = klines[entryIdx]
        const entryPrice = +entryBar.open
        if (!(entryPrice > 0)) continue   // 开盘价异常（停牌等）→ 跳过

        const stopLoss = evt.stopLossPrice

        // 持有期最后一根的索引（不超出数据末尾）
        const maxExitIdx = Math.min(klines.length - 1, entryIdx + holdDays - 1)

        // === Phase 2 新出场逻辑：detectRallyExhaustion ===
        // 旧版用静态 close < stopLossPrice，全市场触发率仅 1.28% 形同虚设。
        // 新版用三档动态信号：'invalid'（跌破蓄势下沿/突破K收盘）+ 'exit'（跌破MA10）+ 'reduce'（爆量长上影，不出场只记录）
        const exhaustion = detectRallyExhaustion(klines, evt, { recencyBars: holdDays, exitMode })
        const firstExit = exhaustion.find(e =>
            (e.level === 'invalid' || e.level === 'exit') && e.idx <= maxExitIdx,
        )

        let exitIdx    = maxExitIdx
        let exitReason = 'time'
        if (firstExit) {
            exitIdx    = firstExit.idx
            exitReason = firstExit.level   // 'invalid' | 'exit'
        }

        // reduce 警示出现在出场前的次数（数据丰富，不影响出场）
        const reduceWarnings = exhaustion.filter(e => e.level === 'reduce' && e.idx <= exitIdx).length

        // 持有期未走完（数据末尾截断）→ 标记 truncated 便于统计层剔除
        const holdBars  = exitIdx - entryIdx + 1
        const truncated = exitReason === 'time' && holdBars < holdDays

        const exitBar   = klines[exitIdx]
        const exitPrice = +exitBar.close
        const returnPct = (exitPrice - entryPrice) / entryPrice * 100

        // 评级：用突破当天截断的 klines，避免泄漏未来信息。
        // Phase 3：双锚点 formScore 在 gradeFreshSignal 内部从 klines 末尾自取现价计算。
        // Phase 4：传 weeklyConfirmed，未确认会自动降一档
        const klinesAtBreakout = klines.slice(0, evt.s3Idx + 1)
        const grade = gradeFreshSignal(klinesAtBreakout, evt, marketEnv, weeklyConfirmed)

        // ===== Phase 2 ML：可选特征 + 多 horizon 标签 =====
        let features = null
        let mlLabels = null
        if (dumpFeatures) {
            features = extractTradeFeatures(klines, evt, weeklyConfirmed, grade)
            // 多 horizon 标签：T+3 / T+7 / T+14 / T+21 收盘价收益（开盘价入场）
            // 比 exitReason 衍生的 returnPct 更"干净"：不被止损动态干扰，纯价格信号
            const horizonReturn = (h) => {
                const idx = Math.min(klines.length - 1, entryIdx + h - 1)
                if (idx <= entryIdx) return null
                const p = +klines[idx].close
                return p > 0 ? (p - entryPrice) / entryPrice * 100 : null
            }
            // 最大未来回报（hold 期内峰值，用于学"最佳止盈点"）
            let peakRet = -Infinity
            for (let k = entryIdx; k <= Math.min(klines.length - 1, entryIdx + 21); k++) {
                const r = (+klines[k].high - entryPrice) / entryPrice * 100
                if (r > peakRet) peakRet = r
            }
            mlLabels = {
                t3Return:  horizonReturn(3),
                t7Return:  horizonReturn(7),
                t14Return: horizonReturn(14),
                t21Return: horizonReturn(21),
                peakReturn21d: isFinite(peakRet) ? peakRet : null,
                exitReason,           // 同 trade.exitReason，但放进 labels 方便拆开
            }
        }

        trades.push({
            // 信号信息
            s3Idx:           evt.s3Idx,
            s3Time:          evt.s3Time,
            breakoutPrice:   evt.breakoutPrice,
            stopLossPrice:   stopLoss,
            goldenBuyPrice:  evt.goldenBuyPrice,
            breakoutConfirm: evt.breakoutConfirm ?? null,   // Week 2 Day 1：N+1 确认状态
            // 入场
            entryIdx,
            entryTime:       entryBar.time,
            entryPrice,
            // 出场
            exitIdx,
            exitTime:        exitBar.time,
            exitPrice,
            exitReason,      // 'time' | 'exit' | 'invalid'
            holdBars,
            truncated,       // true 表示持有期未跑完，统计时建议剔除
            // 收益
            returnPct,
            // Phase 2：reduce 警示数（爆量长上影出现次数，未触发出场只记录）
            reduceWarnings,
            // Phase 4：周线趋势确认（独立维度，评估对胜率的影响）
            weeklyConfirmed,
            // 评级（基于突破当天的状态）
            grade:     grade?.grade ?? null,
            score:     grade?.score ?? null,
            breakdown: grade?.breakdown ?? null,
            // Phase 2 ML：dumpFeatures 时附带
            features,
            mlLabels,
        })
    }

    return { events, trades }
}


// ============ Phase 3 Step 1：stretched 历史滑窗扫描（方案 B 性能修复版）============
// 性能瓶颈历程：
//   v1: 固定 asOfIdx 步进 (step=25)  → 20 次调用/股 × 中等成本 = 偏慢但稳定
//   v2: recencyBars=9999 + 递减游标   → 死股票全量扫历史 800 根 = 1 只/秒 ❌
//   v3 (本版): bounded recencyBars + miss 早终止 + 事件上限 → 5-8 只/秒 ✓
//
// 算法（滑窗 + 递减 + 早终止）：
//   每次调 detect 扫 SCAN_WINDOW (=80) 根
//   命中 → cursor 跳到事件之前
//   未命中 → cursor 跳到下一窗口（重叠 30 根避免漏边界事件）
//   连续 MAX_MISSES (=2) 个窗口都没事件 → 此股放弃（多数死股没意义继续扫）
//   事件数达 MAX_EVENTS_PER_STOCK (=3) 也停止（训练样本够了）
//
// 性能：每股 max 5 calls × ~150ms = 750ms；平均 ~400ms
// 单线程 JS 串行，3000 股 ≈ 3-5 分钟
export function backtestStretchedHistorical(klines, opts = {}) {
    const { holdDays = 7, dumpFeatures = false } = opts
    if (!Array.isArray(klines) || klines.length < 280) return { events: [], trades: [] }

    const SCAN_WINDOW = 80          // 每次 detector 调用扫的 K 线根数（recencyBars）
    const NO_HIT_STEP = 50          // 当前窗口无事件时 cursor 后退步长（=80-30 重叠）
    const MAX_MISSES = 2            // 连续 N 个窗口未命中 → 放弃此股
    const MAX_EVENTS_PER_STOCK = 3  // 训练用：每股 3 个样本足够，多了边际收益低

    const events = []
    const n = klines.length
    let cursor = n - 22
    let consecutiveMisses = 0

    while (cursor >= 250 && events.length < MAX_EVENTS_PER_STOCK && consecutiveMisses < MAX_MISSES) {
        const evt = detectStretchedRally(klines, {
            asOfIdx: cursor,
            recencyBars: SCAN_WINDOW,
            requireMA250Uptrend: true,
            strictBreakoutVol: false,
            consolidationStep: 15,     // 5 → 15：cLen 步长×3，损失边际 cLen 精度换 3x 速度
        })
        if (evt && evt.breakIdx != null && evt.breakIdx < cursor) {
            events.push(evt)
            consecutiveMisses = 0
            cursor = evt.breakIdx - 1
        } else {
            consecutiveMisses++
            cursor -= NO_HIT_STEP
        }
    }

    const trades = []
    for (const evt of events) {
        const breakIdx = evt.breakIdx
        const entryIdx = breakIdx + 1
        if (entryIdx >= n - 1) continue
        const entryBar = klines[entryIdx]
        const entryPrice = +entryBar.open
        if (!(entryPrice > 0)) continue

        // 固定持有期出场（stretched 没接 detectRallyExhaustion，简单退出避免接入复杂度）
        const exitIdx = Math.min(n - 1, entryIdx + holdDays - 1)
        const exitBar = klines[exitIdx]
        const exitPrice = +exitBar.close
        const returnPct = (exitPrice - entryPrice) / entryPrice * 100
        const holdBars = exitIdx - entryIdx + 1
        const truncated = holdBars < holdDays

        // 适配成 threeStage 格式让 extractTradeFeatures 能复用
        // stretched 没 s2，相关字段填 -1 / null（_avgVol/_atr 等数值特征不受影响）
        const adaptedEvt = {
            s3Idx: breakIdx,
            s3Time: evt.breakTime,
            s2Idx: -1,
            s2Type: null,
            s1StartIdx: evt.cStartIdx,
            s1EndIdx:   evt.cEndIdx,
            s1Upper:    evt.cUpper,
            s1Lower:    evt.cLower,
            preBreakoutVolBars: null,
            signalSource: 'stretched',
        }

        let features = null
        let mlLabels = null
        if (dumpFeatures) {
            features = extractTradeFeatures(klines, adaptedEvt, null, null)
            const horizonReturn = (h) => {
                const idx = Math.min(n - 1, entryIdx + h - 1)
                if (idx <= entryIdx) return null
                const p = +klines[idx].close
                return p > 0 ? (p - entryPrice) / entryPrice * 100 : null
            }
            let peakRet = -Infinity
            for (let k = entryIdx; k <= Math.min(n - 1, entryIdx + 21); k++) {
                const r = (+klines[k].high - entryPrice) / entryPrice * 100
                if (r > peakRet) peakRet = r
            }
            mlLabels = {
                t3Return:  horizonReturn(3),
                t7Return:  horizonReturn(7),
                t14Return: horizonReturn(14),
                t21Return: horizonReturn(21),
                peakReturn21d: isFinite(peakRet) ? peakRet : null,
                exitReason: 'time',
            }
        }

        trades.push({
            s3Idx: breakIdx,
            s3Time: evt.breakTime,
            breakoutPrice: evt.breakClose,
            stopLossPrice: evt.stopLossPrice,
            goldenBuyPrice: evt.cUpper,
            entryIdx, entryTime: entryBar.time, entryPrice,
            exitIdx,  exitTime:  exitBar.time,  exitPrice,
            exitReason: 'time',
            holdBars, truncated, returnPct,
            reduceWarnings: 0,
            weeklyConfirmed: null,
            grade: null, score: null, breakdown: null,
            features, mlLabels,
        })
    }
    return { events, trades }
}


/**
 * 把多只票的 trades 聚合成统计指标。
 *
 * @param {Array} trades - 多次 backtestStock 的 trades 拼接
 * @param {Object} opts
 * @param {boolean} opts.excludeTruncated  默认 true，剔除持有期未跑完的 trade（避免末端偏差）
 * @returns {Object} 总体 + 按 grade 分桶的统计
 */
export function aggregateTrades(trades, opts = {}) {
    const { excludeTruncated = true } = opts
    const valid = excludeTruncated ? trades.filter(t => !t.truncated) : trades

    const summarize = (arr) => {
        if (!arr.length) return { count: 0 }
        const returns = arr.map(t => t.returnPct)
        const wins    = arr.filter(t => t.returnPct > 0).length
        // Phase 2：出场原因分布（time/exit/invalid 三档）
        const timeOut    = arr.filter(t => t.exitReason === 'time').length
        const exitOut    = arr.filter(t => t.exitReason === 'exit').length
        const invalidOut = arr.filter(t => t.exitReason === 'invalid').length
        returns.sort((a, b) => a - b)
        const sum     = returns.reduce((s, x) => s + x, 0)
        const median  = returns[Math.floor(returns.length / 2)]
        return {
            count:     arr.length,
            winRate:   wins / arr.length,
            // 三档出场比例（取代旧 stopRate）
            timeRate:    timeOut    / arr.length,
            exitRate:    exitOut    / arr.length,
            invalidRate: invalidOut / arr.length,
            avgReturn: sum / arr.length,
            medianReturn: median,
            maxReturn: returns[returns.length - 1],
            minReturn: returns[0],
            p25:       returns[Math.floor(returns.length * 0.25)],
            p75:       returns[Math.floor(returns.length * 0.75)],
        }
    }

    const overall = summarize(valid)
    const byGrade = {}
    for (const g of ['S', 'A', 'B', 'C']) {
        byGrade[g] = summarize(valid.filter(t => t.grade === g))
    }

    // Phase 4: 周共振切片 — 周线确认的 vs 未确认的
    const byWeeklyConfirmed = {
        confirmed:   summarize(valid.filter(t => t.weeklyConfirmed === true)),
        unconfirmed: summarize(valid.filter(t => t.weeklyConfirmed === false)),
        unknown:     summarize(valid.filter(t => t.weeklyConfirmed == null)),
    }

    // === Phase 1 Day 1 切片分析：找最强的"组合维度" ===

    // grade × weekly 交叉表 —— 12 个 cell（4 grade × 3 weekly），定位最强组合
    // 行索引：'B|confirmed', 'B|unconfirmed', 'A|confirmed', ...
    const byGradeWeekly = {}
    for (const g of ['B', 'A', 'C', 'S']) {
        for (const w of ['confirmed', 'unconfirmed', 'unknown']) {
            const wMatch = (t) =>
                w === 'confirmed'   ? t.weeklyConfirmed === true :
                w === 'unconfirmed' ? t.weeklyConfirmed === false :
                                       t.weeklyConfirmed == null
            const sliced = valid.filter(t => t.grade === g && wMatch(t))
            if (sliced.length > 0) {
                byGradeWeekly[`${g}|${w}`] = summarize(sliced)
            }
        }
    }

    // exit reason 切片 —— time（持满）vs exit（跌破 MA10）vs invalid（跌破蓄势下沿）
    // 实战意义：跌破 MA10 离场的票后续是否还有反弹 / 跌破蓄势下沿的票是否最差
    const byExitReason = {
        time:    summarize(valid.filter(t => t.exitReason === 'time')),
        exit:    summarize(valid.filter(t => t.exitReason === 'exit')),
        invalid: summarize(valid.filter(t => t.exitReason === 'invalid')),
    }

    // 持有期分桶
    const byHoldDuration = {
        '1-3':  summarize(valid.filter(t => t.holdBars >= 1 && t.holdBars <= 3)),
        '4-7':  summarize(valid.filter(t => t.holdBars >= 4 && t.holdBars <= 7)),
        '8+':   summarize(valid.filter(t => t.holdBars >= 8)),
    }

    // P2.7：N+1 突破确认切片（Week 2 Day 1 引入的最强 alpha 维度）
    const byBreakoutConfirm = {
        strong:  summarize(valid.filter(t => t.breakoutConfirm === 'strong')),
        medium:  summarize(valid.filter(t => t.breakoutConfirm === 'medium')),
        fail:    summarize(valid.filter(t => t.breakoutConfirm === 'fail')),
        pending: summarize(valid.filter(t => t.breakoutConfirm === 'pending')),
    }

    // P2.7：信号源切片（threeStage / stretched / dragonReturn）
    const bySignalSource = {}
    const sources = [...new Set(valid.map(t => t.features?.signalSource || 'threeStage').filter(Boolean))]
    for (const src of sources) {
        bySignalSource[src] = summarize(valid.filter(t => (t.features?.signalSource || 'threeStage') === src))
    }

    return {
        overall,
        byGrade,
        byWeeklyConfirmed,
        byGradeWeekly,
        byExitReason,
        byHoldDuration,
        // P2.7 新增切片
        byBreakoutConfirm,
        bySignalSource,
        totalTrades:     trades.length,
        validTrades:     valid.length,
        truncatedTrades: trades.length - valid.length,
    }
}
