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
    detectStretchedRally,
} from './useTechIndicators'
import { extractTradeFeatures } from './useBacktest'


// ---------------- formation_state 计算 ----------------
//
// 8 档枚举（跟后端 _VALID_FORMATION_STATES 对齐）：
//   consolidating / tested / breakout / rally / exhausted / invalid / stretched / unknown
//
// 优先级（高到低）：invalid > exhausted > breakout/rally > tested > consolidating > stretched > unknown
// stretched 是「横盘跳空」识别 —— 跟三维启动互补，仅当三维启动没找到任何阶段时启用。

// 突破"过期"阈值：S3 突破后超过 N 根仍未启新蓄势，detector 不再当作有效信号
// （否则 344 天前的老突破也会被持续标记为 rally + 占用 breakout_at 列）
const MAX_RALLY_BARS = 40
// 形态作废"过期"阈值：跌破蓄势下沿后超过 N 根仍未走出新形态 → 视为终结，剔除
const MAX_INVALID_BARS = 5
// 新加入保护期：入选 < N 毫秒的票永远不自动剔除（给用户观察期）
// 避免"刚加入立刻被刷新剔除"的体验杀手
const NEW_PICK_GRACE_MS = 3 * 24 * 60 * 60 * 1000   // 3 天

/**
 * 一次跑完所有 detector，返回组合结果（避免外层重复调 detectThreeStageLaunch）。
 * @returns { state, latestEvent, expired }
 *   state ∈ formation_state 8 档（含 stretched）
 *   expired = true 仅当"S3 远古过期"，外层据此从候选池剔除（区别于"数据不足/无事件"的 unknown）
 */
function _analyzeKlines(klines) {
    if (!Array.isArray(klines) || klines.length < 60) {
        return { state: 'unknown', latestEvent: null, expired: false }
    }
    const events = detectThreeStageLaunch(klines, { freshWithinBars: 30 })

    // 三维启动没找到任何事件 → 试横盘跳空兜底
    if (!events.length) {
        return _tryStretchedFallback(klines)
    }

    const latestEvent = events.sort((a, b) => (b.s1EndIdx ?? 0) - (a.s1EndIdx ?? 0))[0]

    if (latestEvent.currentStage === 1) {
        // 三维只到 S1 蓄势，但横盘跳空可能已经突破 —— 优先用 stretched 信号
        const stretchedFallback = _tryStretchedFallback(klines)
        if (stretchedFallback.state === 'stretched') return stretchedFallback
        return { state: 'consolidating', latestEvent, expired: false }
    }
    if (latestEvent.currentStage === 2) {
        // 三维到 S2 试盘但没确认 S3，stretched 可能识别为已突破
        const stretchedFallback = _tryStretchedFallback(klines)
        if (stretchedFallback.state === 'stretched') return stretchedFallback
        return { state: 'tested', latestEvent, expired: false }
    }

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

/**
 * 横盘跳空兜底识别。
 * 三维启动 detector 因为要求 S2 试盘环节漏掉的「横盘 → 直接跳空」型，由这个识别。
 * 命中后返 'stretched' state，latestEvent 兼容 S3 字段结构（s3Idx / s3Time / breakoutPrice 等）。
 */
function _tryStretchedFallback(klines) {
    const stretched = detectStretchedRally(klines)
    if (stretched) return { state: 'stretched', latestEvent: stretched, expired: false }
    return { state: 'unknown', latestEvent: null, expired: false }
}

// 诊断模式：在浏览器 console 打印每只票的 detector 检测过程
// 打开方法：浏览器 console 跑 `window.__CANDIDATE_REFRESH_DEBUG__ = true`
// 关掉方法：刷新页面 或 `window.__CANDIDATE_REFRESH_DEBUG__ = false`
function _debugLog(code, name, klines, state) {
    if (typeof window === 'undefined' || !window.__CANDIDATE_REFRESH_DEBUG__) return
    const events = detectThreeStageLaunch(klines, { freshWithinBars: 30 })
    const stretched = detectStretchedRally(klines, { debug: true })   // debug=true 返卡点
    const hit = stretched && !stretched._missed
    console.groupCollapsed(`[refresh] ${code} ${name || ''} → state=${state}`)
    console.log(`  三维启动事件数：${events.length}`)
    if (events.length) {
        const latest = events.sort((a, b) => (b.s1EndIdx ?? 0) - (a.s1EndIdx ?? 0))[0]
        console.log(`  最新事件：currentStage=${latest.currentStage}, S1=[${latest.s1StartIdx}-${latest.s1EndIdx}], `
                  + `S2=${latest.s2Idx}, S3=${latest.s3Idx}, barsAgoFromS3=${latest.barsAgoFromS3}`)
    }
    console.log(`  detectStretchedRally：${hit ? '✅ 命中' : '❌ 未命中'}`)
    if (hit) {
        const prevClose = stretched.breakIdx > 0 ? +klines[stretched.breakIdx - 1].close : null
        const pct = prevClose > 0 ? ((stretched.breakClose - prevClose) / prevClose * 100).toFixed(2) : '?'
        console.log(`    横盘 ${stretched.consolidationBars} 根 [${stretched.cLower.toFixed(2)} ~ ${stretched.cUpper.toFixed(2)}]`)
        console.log(`    突破 K 时间=${stretched.breakTime} close=${stretched.breakClose.toFixed(2)} 涨幅=${pct}% 量比×${stretched.volRatio.toFixed(2)}`)
    } else if (stretched?._missed) {
        const detail = stretched.lastReason.detail
            ? ' ' + JSON.stringify(stretched.lastReason.detail)
            : ''
        console.log(`    卡点：${stretched.lastReason.stage}${detail}`)
    }
    console.groupEnd()
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

        // Phase 3 Step 2：ML 预测批次累积（仅对 stage 3 / stretched 突破票）
        // 每只票算完 detector 后，把 features 攒到这里，最后 1 次 batch 调 mlPredictScores
        const mlPending = []   // { code, features }
        // P2 LHB 批次累积：{ code, s3Time } 用于按日分组批量查
        const lhbPending = [] // { code, s3Time, latestEventBreakoutConfirm }

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
                    _debugLog(p.code, p.name, klines, state)

                    // 突破 / 横盘跳空 状态 → 攒到 ML 预测批 + LHB 批
                    if (latestEvent && (state === 'breakout' || state === 'rally' || state === 'stretched')) {
                        try {
                            // stretched 来源时强制标识；threeStage 默认
                            const evtForFeat = state === 'stretched'
                                ? { ...latestEvent, signalSource: 'stretched',
                                    s3Idx: latestEvent.s3Idx ?? latestEvent.breakIdx,
                                    s3Time: latestEvent.s3Time ?? latestEvent.breakTime,
                                    s1StartIdx: latestEvent.s1StartIdx ?? latestEvent.cStartIdx,
                                    s1EndIdx:   latestEvent.s1EndIdx   ?? latestEvent.cEndIdx,
                                    s1Upper:    latestEvent.s1Upper    ?? latestEvent.cUpper,
                                    s1Lower:    latestEvent.s1Lower    ?? latestEvent.cLower,
                                    s2Idx: -1, s2Type: null,
                                  }
                                : { ...latestEvent, signalSource: 'threeStage' }
                            const features = extractTradeFeatures(klines, evtForFeat, null, null)
                            if (features) mlPending.push({ code: p.code, features })
                            // 攒 LHB 查询（用 s3Time 防未来泄漏）
                            const s3Time = latestEvent.s3Time || latestEvent.breakTime
                            if (s3Time) {
                                lhbPending.push({
                                    code: p.code,
                                    s3Time: String(s3Time).slice(0, 10),
                                    breakoutConfirm: latestEvent.breakoutConfirm ?? null,
                                })
                            }
                        } catch (e) { /* 特征提取失败不阻塞 refresh */ }
                    }

                    // 远古突破直接剔除（不走 update，跳到下一只）
                    // 但：入选 < NEW_PICK_GRACE_MS（3 天）的票豁免，让用户有观察期
                    if (expired) {
                        const savedAt = p.saved_at ? new Date(p.saved_at).getTime() : 0
                        const isNewPick = savedAt > 0 && (Date.now() - savedAt) < NEW_PICK_GRACE_MS
                        if (isNewPick) {
                            // 新加入的票即使 detector 判过期，也保留（标 formation_state，不剔除）
                            updates.push({
                                code: p.code,
                                formation_state: 'unknown',
                                peak_gain_since_save: _peakGainSinceSave(klines, p.saved_at, p.save_price),
                                secondary_entry_at: '',
                                secondary_entry_price: null,
                                breakout_at: '',
                                ml_score: 'clear', breakout_confirm: 'clear',
                                lhb_in_window: 'clear', lhb_count: 'clear',
                                lhb_net_buy: 'clear', star_level: 'clear',
                            })
                            continue
                        }
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

                    // 非突破态 → 所有衍生信号 'clear'（防止老分数残留）
                    // 突破态 → 占位 'pending'，下面 batch 后填入
                    const isBreakState = state === 'breakout' || state === 'rally' || state === 'stretched'
                    const ml_score = isBreakState ? 'pending' : 'clear'
                    const breakout_confirm = isBreakState ? (latestEvent?.breakoutConfirm ?? 'clear') : 'clear'
                    // LHB 字段 + star_level 也用占位
                    const lhb_in_window = isBreakState ? 'pending' : 'clear'
                    const lhb_count     = isBreakState ? 'pending' : 'clear'
                    const lhb_net_buy   = isBreakState ? 'pending' : 'clear'
                    const star_level    = isBreakState ? 'pending' : 'clear'

                    updates.push({
                        code: p.code,
                        formation_state: state,
                        peak_gain_since_save,
                        secondary_entry_at,
                        secondary_entry_price,
                        breakout_at,
                        ml_score,
                        breakout_confirm,
                        lhb_in_window,
                        lhb_count,
                        lhb_net_buy,
                        star_level,
                    })
                } catch (e) {
                    errors++
                    console.warn(`[candidate-refresh] ${p.code} 失败`, e)
                }
            }

            // Phase 3 Step 2：批量预测 ML
            const mlScores = {}
            if (mlPending.length) {
                try {
                    const r = await api.mlPredictScores(mlPending.map(x => x.features))
                    if (r?.ok && Array.isArray(r.data)) {
                        for (let i = 0; i < mlPending.length; i++) {
                            const s = r.data[i]
                            if (typeof s === 'number') mlScores[mlPending[i].code] = s
                        }
                    }
                } catch (e) {
                    console.warn('[candidate-refresh] ML predict 失败（降级无 score）', e)
                }
            }

            // P2 LHB：按 s3Time 分组 batch 拉
            const lhbMap = {}   // { code: { lhb_in_window, lhb_count, lhb_net_buy_sum, days_since_last_lhb } }
            if (lhbPending.length) {
                try {
                    const byDate = {}
                    for (const e of lhbPending) {
                        byDate[e.s3Time] = byDate[e.s3Time] || new Set()
                        byDate[e.s3Time].add(e.code)
                    }
                    for (const [dateKey, codeSet] of Object.entries(byDate)) {
                        const r = await api.getBatchLhbFeatures([...codeSet], dateKey, 30)
                        if (r?.ok && r.data) {
                            for (const [code, info] of Object.entries(r.data)) {
                                if (info) lhbMap[code] = info
                            }
                        }
                    }
                } catch (e) {
                    console.warn('[candidate-refresh] LHB 拉取失败（降级）', e)
                }
            }

            // star_level 计算：跟 Quant.vue getTradeStarLevel 同一套逻辑
            const computeStarLevel = (p, breakoutConfirm, sectorScore, lhbInWindow, mlScore) => {
                const confirmGood = breakoutConfirm === 'strong' || breakoutConfirm === 'medium'
                const hasLhb = lhbInWindow === 1
                if (confirmGood && hasLhb) return 4
                if (confirmGood || hasLhb) return 3
                if (breakoutConfirm === 'medium') return 2
                if (breakoutConfirm === 'strong') return 1
                if (typeof sectorScore === 'number' && sectorScore >= 70) return 1
                if (typeof mlScore === 'number' && mlScore >= 0.40) return 1
                return 0
            }

            // 回填 'pending' 字段 + 算 star_level
            for (const u of updates) {
                if (u.ml_score === 'pending') {
                    u.ml_score = (u.code in mlScores) ? mlScores[u.code] : 'clear'
                }
                const lhb = lhbMap[u.code]
                if (u.lhb_in_window === 'pending') {
                    u.lhb_in_window = lhb ? (lhb.lhb_in_window || 0) : 'clear'
                }
                if (u.lhb_count === 'pending') {
                    u.lhb_count = lhb ? (lhb.lhb_count || 0) : 'clear'
                }
                if (u.lhb_net_buy === 'pending') {
                    u.lhb_net_buy = lhb ? (lhb.lhb_net_buy_sum || 0) : 'clear'
                }
                if (u.star_level === 'pending') {
                    // 同步拉一次 sectorStrength（候选池单次量小，直接逐个）
                    let sectorScore = null
                    // 不再单独 RPC 拉 sector（性能考量），仅用 LHB+confirm+ML
                    const star = computeStarLevel(
                        u,
                        u.breakout_confirm !== 'clear' ? u.breakout_confirm : null,
                        sectorScore,
                        lhb ? lhb.lhb_in_window : null,
                        typeof u.ml_score === 'number' ? u.ml_score : null,
                    )
                    u.star_level = star
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
                mlScores,         // Phase 3 Step 2：{ code: score (0-1) } 供 UI 渲染 ⭐
            }
            return lastSession.value
        } finally {
            refreshing.value = false
        }
    }

    return { refreshing, lastSession, refresh }
}
