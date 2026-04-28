/**
 * 全局通知（toast）系统。
 *
 * 用途：
 *   - useSmartRefresh 的 onError 上报网络异常
 *   - 任何场景需要短暂提示用户某事件
 *
 * 设计：
 *   - 模块级 ref，所有组件共享一个 toast 队列
 *   - 同 dedupKey 的通知 30s 内只显示一次（避免被同一个错误反复轰炸）
 *   - 自动消失（3-5s 视严重度而定）
 *   - 用户可点 ✕ 提前关闭
 *
 * 用法：
 *   import { pushError, pushInfo } from '@/composables/useNotifications'
 *   pushError('行情接口失败', { dedupKey: 'market_data' })
 */
import { ref } from 'vue'

let _seq = 0
export const notifications = ref([])  // [{id, kind, msg, dedupKey, createdAt}]

const _lastShownAt = new Map()  // dedupKey → timestamp（防止短时间内同错重复弹）
const DEDUP_WINDOW_MS = 30_000

function _push(kind, msg, opts = {}) {
    const dedupKey = opts.dedupKey || null
    const now = Date.now()

    // 去重检查
    if (dedupKey) {
        const lastAt = _lastShownAt.get(dedupKey) || 0
        if (now - lastAt < DEDUP_WINDOW_MS) return  // 静默吃掉
        _lastShownAt.set(dedupKey, now)
    }

    const id = ++_seq
    const ttlMs = opts.ttlMs ?? (kind === 'error' ? 5000 : 3000)
    notifications.value.push({ id, kind, msg, dedupKey, createdAt: now })

    // 自动消失
    setTimeout(() => dismiss(id), ttlMs)
}

export function pushInfo(msg, opts) { _push('info', msg, opts) }
export function pushSuccess(msg, opts) { _push('success', msg, opts) }
export function pushWarn(msg, opts) { _push('warn', msg, opts) }
export function pushError(msg, opts) { _push('error', msg, opts) }

export function dismiss(id) {
    const i = notifications.value.findIndex(n => n.id === id)
    if (i >= 0) notifications.value.splice(i, 1)
}

export function clearAll() {
    notifications.value = []
    _lastShownAt.clear()
}
