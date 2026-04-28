/**
 * 首屏快照（localStorage 同步缓存）。
 *
 * 目的：app 冷启动时，让 useSmartRefresh 在等待真实接口返回之前，
 *       先把"上次成功拉到的数据"瞬时渲染出来——用户体验从"开屏 5 秒空白
 *       → 慢慢冒数据"变成"开屏立刻看到（旧）数据 → 100-500ms 内被新数据替换"。
 *
 * 设计：
 *   - 同步读写 localStorage（不需要 await，比 IndexedDB 简单）
 *   - 24h TTL：跨日的数据不复用（避免明显陈旧）
 *   - JSON 序列化兜底：localStorage 满了 / 数据无法序列化都静默失败，
 *     不影响主流程（其他模块照常用网络拉新）
 *
 * 用法（结合 useSmartRefresh）：
 *   useSmartRefresh(api.getX, {
 *       snapshotKey: 'market_data',  // 唯一 key
 *       onData: (data) => { state.value = data },
 *   })
 *
 * 直接用：
 *   import { loadSnapshot, saveSnapshot } from './useSnapshot'
 *   const cached = loadSnapshot('my_key')
 *   if (cached) state.value = cached.data
 *   saveSnapshot('my_key', currentData)
 */

const PREFIX = 'invest_tool:snapshot:'
const MAX_AGE_MS = 24 * 60 * 60 * 1000  // 24 小时

export function loadSnapshot(key) {
    if (!key || typeof window === 'undefined') return null
    try {
        const raw = localStorage.getItem(PREFIX + key)
        if (!raw) return null
        const obj = JSON.parse(raw)
        if (!obj || !obj.t || obj.d === undefined || obj.d === null) return null
        if (Date.now() - obj.t > MAX_AGE_MS) return null
        return { data: obj.d, savedAt: obj.t }
    } catch {
        return null
    }
}

export function saveSnapshot(key, data) {
    if (!key || typeof window === 'undefined') return
    if (data === undefined || data === null) return
    try {
        localStorage.setItem(PREFIX + key, JSON.stringify({ t: Date.now(), d: data }))
    } catch {
        // QuotaExceededError / 序列化错误 / 隐私模式都走这里——静默吃掉
    }
}

export function clearSnapshot(key) {
    if (!key || typeof window === 'undefined') return
    try { localStorage.removeItem(PREFIX + key) } catch {}
}
