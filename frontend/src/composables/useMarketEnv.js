/**
 * 大盘环境拉取 + 缓存。
 *
 * 用沪深 300 日 K 计算"大盘评分"（强势/良好/震荡/弱势/破位 五档），
 * 给所有"找发车"扫描器作为前置上下文：大盘破位时哪怕个股形态完美也要降级，
 * 大盘强势时反过来给个股加分。
 *
 * 缓存：模块级单例，5 分钟 TTL。多个 modal 同时调用只发一次请求。
 */
import { ref } from 'vue'
import { api } from '../api/client'
import { computeMarketEnvScore } from './useTechIndicators'

// 模块级缓存（单例，所有调用方共享）
const envState = ref(null)            // { score, level, reasons, close, ma20, ma60, ts }
const loading  = ref(false)
const CACHE_TTL_MS = 5 * 60 * 1000    // 5 分钟
let _inflight = null                  // 并发请求合并

async function refresh(force = false) {
    const cached = envState.value
    if (!force && cached && Date.now() - cached.ts < CACHE_TTL_MS) {
        return cached
    }
    if (_inflight) return _inflight

    loading.value = true
    _inflight = (async () => {
        try {
            // 走 TDX（admin-only 端点）— 大盘环境只在管理员的扫描器里展示，
            // 所以这里始终用 TDX，绝不打腾讯/EM
            const res = await api.getIndexKlineViaTdx('沪深300', '日K', 200)
            if (res?.ok && Array.isArray(res.data) && res.data.length >= 60) {
                const env = computeMarketEnvScore(res.data)
                if (env) {
                    envState.value = { ...env, ts: Date.now() }
                    return envState.value
                }
            }
        } catch (e) { /* 静默：拉不到指数不影响扫描主流程 */ }
        finally {
            loading.value = false
            _inflight = null
        }
        return null
    })()
    return _inflight
}

export function useMarketEnv() {
    return { env: envState, loading, refresh }
}
