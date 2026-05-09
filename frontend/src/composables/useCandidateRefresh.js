/**
 * Phase 5：候选池刷新 composable —— 拉每只票日 K，跑 detector，算 peak_gain，
 * 批量写回后端 candidate_picks 的追踪字段。
 *
 * 设计决策：
 *   - 前端跑 detector（不在后端重写一份），单一逻辑来源避免漂移
 *   - 走 SQLite 持久化缓存（getStockKlineViaTdxCached），命中后毫秒级
 *   - "节流"靠后端 last_refreshed_at — 上层调用者决定是否触发
 *
 * 用法：
 *   const { refreshing, refresh } = useCandidateRefresh()
 *   await refresh(picks)   // picks = api.listCandidatePicks() 返回的数组
 */
import { ref } from 'vue'
import { api } from '../api/client'
import {
    detectThreeStageLaunch,
    detectRallyExhaustion,
    detectSecondaryEntry,
} from './useTechIndicators'


// ---------------- formation_state 计算 ----------------
//
// 6 档枚举（跟后端 _VALID_FORMATION_STATES 对齐）：
//   consolidating / tested / breakout / rally / exhausted / invalid / unknown
//
// 优先级：invalid > exhausted > breakout/rally > tested > consolidating

// 突破"过期"阈值：S3 突破后超过 N 根仍未启新蓄势，detector 不再当作有效信号
// （否则 344 天前的老突破也会被持续标记为 rally + 占用 breakout_at 列）
const MAX_RALLY_BARS = 40
// 形态作废"过期"阈值：跌破蓄势下沿后超过 N 根仍未走出新形态 → 视为终结，剔除
const MAX_INVALID_BARS = 5

/**
 * 一次跑完所有 detector，返回组合结果（避免外层重复调 detectThreeStageLaunch）。
 * @returns { state, latestEvent, expired }
 *   state ∈ formation_state 6 档 + 'unknown'
 *   expired = true 仅当"S3 远古过期"，外层据此从候选池剔除（区别于"数据不足/无事件"的 unknown）
 */
function _analyzeKlines(klines) {
    if (!Array.isArray(klines) || klines.length < 60) {
        return { state: 'unknown', latestEvent: null, expired: false }
    }
    const events = detectThreeStageLaunch(klines, { freshWithinBars: 30 })
    if (!events.length) return { state: 'unknown', latestEvent: null, expired: false }

    const latestEvent = events.sort((a, b) => (b.s1EndIdx ?? 0) - (a.s1EndIdx ?? 0))[0]

    if (latestEvent.currentStage === 1) return { state: 'consolidating', latestEvent, expired: false }
    if (latestEvent.currentStage === 2) return { state: 'tested',         latestEvent, expired: false }

    if (latestEvent.currentStage === 3) {
        const barsAgo = latestEvent.barsAgoFromS3 ?? 999

        // 过期：S3 太久没新结构 → 标记 expired，让外层从候选池剔除
        if (barsAgo > MAX_RALLY_BARS) {
            return { state: 'unknown', latestEvent: null, expired: true }
        }

        const exhaustion = detectRallyExhaustion(klines, latestEvent)
        const firstHard = exhaustion.find(e => e.level === 'invalid' || e.level === 'exit')
        if (firstHard?.level === 'invalid') {
            // 作废 > MAX_INVALID_BARS 根仍无新形态 → 标记 expired 让外层剔除
            const barsSinceInvalid = (klines.length - 1) - (firstHard.idx ?? 0)
            const expired = barsSinceInvalid > MAX_INVALID_BARS
            return { state: 'invalid', latestEvent, expired }
        }
        if (firstHard?.level === 'exit')    return { state: 'exhausted', latestEvent, expired: false }
        if (exhaustion.some(e => e.level === 'reduce')) return { state: 'exhausted', latestEvent, expired: false }

        return { state: barsAgo <= 5 ? 'breakout' : 'rally', latestEvent, expired: false }
    }
    return { state: 'unknown', latestEvent: null, expired: false }
}


// ---------------- peak_gain_since_save 计算 ----------------
// 从 saved_at 之后所有 K 线的 high 中找最大值，对比 save_price 得最大涨幅

function _klineTimeMs(t) {
    if (t == null) return null
    if (typeof t === 'number') return t > 1e12 ? t : t * 1000
    if (typeof t === 'string') {
        const ms = new Date(t).getTime()
        return Number.isNaN(ms) ? null : ms
    }
    if (typeof t === 'object' && t.year) {
        return new Date(t.year, (t.month || 1) - 1, t.day || 1).getTime()
    }
    return null
}

function _peakGainSinceSave(klines, savedAtIso, savePrice) {
    if (!savedAtIso || !(savePrice > 0)) return null
    const savedMs = new Date(savedAtIso).getTime()
    if (Number.isNaN(savedMs)) return null
    let peak = 0
    for (const k of klines) {
        const t = _klineTimeMs(k.time)
        if (t == null || t < savedMs) continue
        const high = +k.high
        if (!Number.isFinite(high)) continue
        const gain = (high - savePrice) / savePrice * 100
        if (gain > peak) peak = gain
    }
    return peak
}


// ---------------- 主 composable ----------------

export function useCandidateRefresh() {
    const refreshing = ref(false)
    const lastSession = ref(null)   // { at, total, updated, errors, elapsedMs }

    /**
     * 跑刷新。返回 { at, total, updated, errors, elapsedMs } 或 null（已在跑/无 picks）
     *
     * @param picks api.listCandidatePicks() 返回的数组
     */
    async function refresh(picks) {
        if (refreshing.value) return null
        if (!Array.isArray(picks) || !picks.length) return null
        refreshing.value = true
        const t0 = Date.now()
        const updates = []
        const expiredCodes = []   // S3 过期（barsAgo > MAX_RALLY_BARS）→ 刷新时从候选池剔除
        let errors = 0

        try {
            for (const p of picks) {
                try {
                    const res = await api.getStockKlineViaTdxCached(p.code, '日K')
                    if (!res?.ok || !Array.isArray(res.data) || res.data.length < 60) {
                        errors++
                        continue
                    }
                    const klines = res.data
                    const { state, latestEvent, expired } = _analyzeKlines(klines)

                    // 远古突破直接剔除（不走 update，跳到下一只）
                    if (expired) {
                        expiredCodes.push({ code: p.code, name: p.name })
                        continue
                    }

                    const peak_gain_since_save = _peakGainSinceSave(klines, p.saved_at, p.save_price)

                    // Phase 6：仅 stage 3 + 形态未破时检测二次买点
                    let secondary_entry_at = ''     // '' = 显式清空（无二次买点 / 形态已破）
                    let secondary_entry_price = null
                    if (latestEvent
                        && latestEvent.currentStage === 3
                        && state !== 'invalid'      // 形态作废就没意义了
                    ) {
                        const sec = detectSecondaryEntry(klines, latestEvent)
                        if (sec) {
                            secondary_entry_at    = sec.time
                            secondary_entry_price = sec.reboundClose
                        }
                    }

                    // 突破日：detector 给出的 stage 3 K 时间（用于 UI 显示"距今 N 天"）
                    // 没到 stage 3 → '' 显式清空（避免老的 breakout_at 残留）
                    const breakout_at = (latestEvent && latestEvent.currentStage === 3 && latestEvent.s3Time)
                        ? latestEvent.s3Time
                        : ''

                    updates.push({
                        code: p.code,
                        formation_state: state,
                        peak_gain_since_save,
                        secondary_entry_at,
                        secondary_entry_price,
                        breakout_at,
                    })
                } catch (e) {
                    errors++
                    console.warn(`[candidate-refresh] ${p.code} 失败`, e)
                }
            }

            if (updates.length) {
                await api.bulkUpdateCandidateTracking(updates)
            }

            // 批量剔除过期票（顺序调用，单条失败不影响其它）
            let removed = 0
            for (const ex of expiredCodes) {
                try {
                    const r = await api.removeCandidatePick(ex.code)
                    if (r?.ok) removed++
                } catch (e) {
                    console.warn(`[candidate-refresh] 剔除过期 ${ex.code} 失败`, e)
                }
            }

            lastSession.value = {
                at:        new Date().toISOString(),
                total:     picks.length,
                updated:   updates.length,
                expired:   expiredCodes.length,
                removed,
                expiredList: expiredCodes,
                errors,
                elapsedMs: Date.now() - t0,
            }
            return lastSession.value
        } finally {
            refreshing.value = false
        }
    }

    return { refreshing, lastSession, refresh }
}
