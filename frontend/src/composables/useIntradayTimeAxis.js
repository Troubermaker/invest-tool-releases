/**
 * 分时图（intraday）X 轴等距划分共享逻辑 —— StockChart / Market 等多处复用。
 *
 * 核心思路：lightweight-charts 按 bar 逻辑索引等距排列（不按 time 值），
 * 所以要让"9:35 时 11 个点只占左侧 5%、午休 11:30/13:00 在中点对接、未到的时间留白"，
 * 必须始终铺满 240 个 bar，未到的时间用 whitespace（仅 time 无 value）占位。
 *
 * 同时把每个 bar 的 time 从真实秒映射成"合成秒"= dayStart + tradingMinute * 60，
 * 这样 chart 内部完全按 trading minute 等距，hover / 轴 label 反算回墙钟 HH:MM 显示。
 *
 * 用法：
 *   import { useIntradayTimeAxis } from '@/composables/useIntradayTimeAxis'
 *   const it = useIntradayTimeAxis()
 *   const filled = it.fillFullDay(klines)        // 转换 + 填充 240 bar
 *   it.setIntradayVisibleRange(chart)             // 锁定 0-239 logical range
 *   chart.applyOptions({ timeScale: { tickMarkFormatter: it.tickMarkFormatter } })
 */

const TRADING_MINUTES_PER_DAY = 240

/**
 * 当日 9:30 的真实 epoch 秒。
 */
function computeDayStartSec(realSec) {
    const d = new Date(realSec * 1000)
    d.setHours(9, 30, 0, 0)
    return Math.floor(d.getTime() / 1000)
}

/**
 * 真实秒 → 距 9:30 的 trading minute（0-239）。
 * 11:30 收盘归 119，午休 11:31-12:59 钳到 119，13:00 → 120，15:00 → 239。
 */
function realSecToTradingMinute(realSec, dayStart) {
    const wallMin = Math.round((realSec - dayStart) / 60)
    if (wallMin <= 0)   return 0
    if (wallMin <= 119) return wallMin
    if (wallMin <= 209) return 119      // 11:30 + 午休
    if (wallMin <= 329) return 120 + (wallMin - 210)
    return 239
}

/**
 * 合成秒 → 显示用的墙钟 HH:MM。
 * 边界 trading minute 119 / 239 特判（11:30 / 15:00 而非 11:29 / 14:59）。
 */
function syntheticSecToWallHHMM(syntheticSec, dayStart) {
    const tm = Math.round((syntheticSec - dayStart) / 60)
    const pad = n => String(n).padStart(2, '0')
    if (tm === 119) return '11:30'
    if (tm === 239) return '15:00'
    if (tm < 120) {
        const total = 9 * 60 + 30 + tm
        return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`
    }
    const total = 13 * 60 + (tm - 120)
    return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`
}

export function useIntradayTimeAxis() {
    // 单实例 ref：记录当前 dayStart 以便 formatter 反算
    let _dayStart = 0

    /**
     * 把分钟 K 列表转换成 240 bar 的合成时间序列（含 whitespace 占位）。
     * 输入要求：每根 K 至少有 time（epoch 秒）。
     *
     * 返回：长度 = 240 的数组，每个元素：
     *   - 真实数据：{ ...原 K, _realTime: 原 time, time: 合成秒 }
     *   - 占位：{ time: 合成秒, _isWhitespace: true }
     */
    function fillFullDay(klines) {
        if (!Array.isArray(klines) || !klines.length) return []
        _dayStart = computeDayStartSec(klines[0].time)
        // 用 trading minute 去重（11:29 + 11:30 会撞到 119，后到的覆盖）
        const dedup = new Map()
        for (const k of klines) {
            const tm = realSecToTradingMinute(k.time, _dayStart)
            dedup.set(tm, { ...k, _realTime: k.time, time: _dayStart + tm * 60 })
        }
        const fullDay = []
        for (let m = 0; m < TRADING_MINUTES_PER_DAY; m++) {
            if (dedup.has(m)) {
                fullDay.push(dedup.get(m))
            } else {
                fullDay.push({ time: _dayStart + m * 60, _isWhitespace: true })
            }
        }
        return fullDay
    }

    function getDayStart() { return _dayStart }

    /**
     * 锁定 chart 的 visible logical range 到 0-239，让 240 bar 均分全宽。
     */
    function setIntradayVisibleRange(chart) {
        if (!chart) return
        chart.timeScale().setVisibleLogicalRange({ from: 0, to: TRADING_MINUTES_PER_DAY - 1 })
    }

    /**
     * tickMarkFormatter —— 合成秒 → 墙钟 HH:MM，其他模式（time 不是数字 / dayStart 未设）按默认。
     * 用法：chart.applyOptions({ timeScale: { tickMarkFormatter: it.tickMarkFormatter } })
     */
    function tickMarkFormatter(time, tickMarkType) {
        if (typeof time !== 'number') return String(time ?? '')
        if (_dayStart) {
            return syntheticSecToWallHHMM(time, _dayStart)
        }
        // 默认 fallback：让 caller 自行处理
        const d = new Date(time * 1000)
        const pad = n => String(n).padStart(2, '0')
        if (tickMarkType === 3) return `${pad(d.getHours())}:${pad(d.getMinutes())}`
        return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    }

    /**
     * 把合成秒转回真实墙钟（用于 hover 显示）。
     * 优先用 bar 上的 _realTime（更精确），找不到就 fallback 反算。
     */
    function formatHoverTime(syntheticSec, klines) {
        const pad = n => String(n).padStart(2, '0')
        if (klines) {
            const k = klines.find(x => x.time === syntheticSec)
            if (k && !k._isWhitespace && k._realTime) {
                const rd = new Date(k._realTime * 1000)
                return `${pad(rd.getHours())}:${pad(rd.getMinutes())}`
            }
        }
        return _dayStart ? syntheticSecToWallHHMM(syntheticSec, _dayStart) : ''
    }

    return {
        fillFullDay,
        getDayStart,
        setIntradayVisibleRange,
        tickMarkFormatter,
        formatHoverTime,
        TRADING_MINUTES_PER_DAY,
    }
}
