/**
 * 大盘环境拉取 + 缓存（Week 1 Day 1 升级版 —— 多源 regime 评分）。
 *
 * 原版只用沪深 300 K 线，新版加入：
 *   1. 沪深300 日 K（趋势）
 *   2. 创业板指 日 K（小盘股风险偏好）
 *   3. 市场情绪（涨停 / 跌停 / 上涨家数）
 *
 * 输出 regime 5 档：strong / good / neutral / weak / breakdown。
 * 给所有"找发车"扫描器作为前置过滤 + 阈值适配。
 *
 * 缓存：模块级单例，3 分钟 TTL（市场情绪 15s 一变，但 regime 用 3 分钟够了）。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { computeMarketEnvScore, computeMarketRegime } from './useTechIndicators'

// 模块级缓存（单例，所有调用方共享）
const envState = ref(null)
const loading  = ref(false)
const CACHE_TTL_MS = 3 * 60 * 1000    // 3 分钟
let _inflight = null

async function refresh(force = false) {
    const cached = envState.value
    if (!force && cached && Date.now() - cached.ts < CACHE_TTL_MS) {
        return cached
    }
    if (_inflight) return _inflight

    loading.value = true
    _inflight = (async () => {
        try {
            // 并行拉三个数据源 —— sentiment 失败不影响主流程
            const [csiRes, gemRes, sentRes] = await Promise.allSettled([
                api.getIndexKlineViaTdx('沪深300', '日K', 200),
                api.getIndexKlineViaTdx('创业板指', '日K', 200),
                api.getMarketSentiment(),
            ])

            const csi300 = (csiRes.status === 'fulfilled' && csiRes.value?.ok) ? csiRes.value.data : null
            const gem    = (gemRes.status === 'fulfilled' && gemRes.value?.ok) ? gemRes.value.data : null
            const sent   = (sentRes.status === 'fulfilled' && sentRes.value?.ok) ? sentRes.value.data : null
            const breadth = sent?.breadth || null

            if (!csi300 || csi300.length < 60) {
                console.warn('[market-env] 沪深 300 K 线不足，regime 无法计算')
                return null
            }

            const regime = computeMarketRegime({
                csi300Klines: csi300,
                gemKlines:    gem,
                sentiment:    breadth,
            })
            if (!regime) return null

            // 兼容旧 env shape（score/level/reasons），添加新 regime 字段
            const legacy = computeMarketEnvScore(csi300)
            envState.value = {
                // ---- 旧字段（兼容现有调用）----
                score:   legacy?.score,
                level:   legacy?.level,
                reasons: regime.reasons,
                close:   legacy?.close,
                ma20:    legacy?.ma20,
                ma60:    legacy?.ma60,
                // ---- 新字段 ----
                regime:    regime.regime,        // 'strong'|'good'|'neutral'|'weak'|'breakdown'
                regimeLabel: regime.label,       // 中文
                regimeScore: regime.score,       // 0-100
                breakdown:   regime.breakdown,   // 子分量明细
                hasMultiSource: !!(gem && breadth),
                ts: Date.now(),
            }
            return envState.value
        } catch (e) {
            console.warn('[market-env] refresh failed:', e)
            return null
        } finally {
            loading.value = false
            _inflight = null
        }
    })()
    return _inflight
}

// 给 UI / 策略快速判断当前 regime 用
const isBreakdown   = computed(() => envState.value?.regime === 'breakdown')
const isStrong      = computed(() => envState.value?.regime === 'strong')
const isWeak        = computed(() => envState.value?.regime === 'weak' || envState.value?.regime === 'breakdown')
const currentRegime = computed(() => envState.value?.regime || null)

export function useMarketEnv() {
    return { env: envState, loading, refresh, isBreakdown, isStrong, isWeak, currentRegime }
}
