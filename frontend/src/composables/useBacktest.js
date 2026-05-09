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
import { detectThreeStageLaunch, gradeFreshSignal, detectRallyExhaustion, hasWeeklyTrendConfirmation } from './useTechIndicators'

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
    } = opts

    // 周线确认 — 全 trade 共享（周线状态是当前快照，跟单 trade 时点关系不大）
    // 注意：严格说回测应该用突破当周的状态，但周线变化慢，近似用最新状态可接受
    const weeklyConfirmed = weeklyKlines ? hasWeeklyTrendConfirmation(weeklyKlines) : null

    if (!Array.isArray(klines) || klines.length < 100) {
        return { events: [], trades: [] }
    }

    const events = detectThreeStageLaunch(klines, detectOpts)
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
        const exhaustion = detectRallyExhaustion(klines, evt, { recencyBars: holdDays })
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

        trades.push({
            // 信号信息
            s3Idx:           evt.s3Idx,
            s3Time:          evt.s3Time,
            breakoutPrice:   evt.breakoutPrice,
            stopLossPrice:   stopLoss,
            goldenBuyPrice:  evt.goldenBuyPrice,
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

    return {
        overall,
        byGrade,
        byWeeklyConfirmed,
        totalTrades:     trades.length,
        validTrades:     valid.length,
        truncatedTrades: trades.length - valid.length,
    }
}
