/**
 * 可见性感知的定时轮询。
 *
 * 设计思路：app 每次只挂载一个页面（v-if 切换时其它页面 unmount），
 * 所以当前可见的页面就是"用户正在盯的页面"，自然应该保持基础刷新节奏。
 * 只需要处理"窗口切到后台 / 最小化"这一种降速场景 —— 用户看不见就没必要抓接口。
 *
 * 三态：
 *   - 'active'      窗口可见，正常刷新（baseInterval）
 *   - 'hidden'      窗口隐藏 / 最小化不足 5 分钟，6 倍间隔（用户可能很快回来）
 *   - 'deep-hidden' 隐藏超过 5 分钟，18 倍间隔（接近暂停）
 *
 * 间隔可被用户偏好覆盖：传入 prefKey，组合就会从 user_preferences 读取秒数（0 = 暂停）。
 *
 * 用法：
 *   const { secondsUntilNext, currentInterval, setRefreshInterval, refresh } =
 *       useSmartRefresh(api.getMarketData, {
 *           baseInterval: 15_000,
 *           prefKey: 'refresh.market_data',
 *           onData: (d) => { marketIndices.value = d.indices },
 *       })
 */
import { onMounted, onUnmounted, ref, watch, computed } from 'vue'
import { api } from '../api/client'

const HIDDEN_DEEP_MS = 5 * 60 * 1000  // 隐藏超过 5 分钟算 deep-hidden

// ---------------- 模块级全局状态 ----------------
const isHidden    = ref(typeof document !== 'undefined' ? document.hidden : false)
const hiddenSince = ref(isHidden.value ? Date.now() : null)
// 每秒推一次时间戳：1) 驱动 deep-hidden 判定 2) 驱动各模块倒计时 UI
const now = ref(Date.now())

export const refreshMode = computed(() => {
    if (!isHidden.value) return 'active'
    const hidMs = hiddenSince.value ? (now.value - hiddenSince.value) : 0
    return hidMs >= HIDDEN_DEEP_MS ? 'deep-hidden' : 'hidden'
})

const MODE_MULTIPLIER = {
    active:       1,
    hidden:       6,
    'deep-hidden': 18,
}

// ---------------- 全局监听器只挂一次 ----------------
let globalListenersInstalled = false
let globalTickerStarted = false

function installGlobalListeners() {
    if (globalListenersInstalled || typeof window === 'undefined') return
    globalListenersInstalled = true
    document.addEventListener('visibilitychange', () => {
        const hidden = document.hidden
        isHidden.value = hidden
        hiddenSince.value = hidden ? Date.now() : null
    })
}

function startGlobalTicker() {
    if (globalTickerStarted || typeof window === 'undefined') return
    globalTickerStarted = true
    setInterval(() => { now.value = Date.now() }, 1000)
}


// ---------------- 核心 API ----------------
/**
 * @param {Function} fn API 调用函数，可以返回 {ok, data, error} 信封或 void
 * @param {Object} opts
 *   - baseInterval: 基础间隔 ms（active 模式下真实使用）
 *   - prefKey:      可选，user_preferences key；值是秒数（0 = 暂停）
 *   - onData:       成功回调（信封模式）
 *   - onError:      失败回调（信封模式）
 *   - immediate:    挂载时立即拉一次，默认 true
 */
export function useSmartRefresh(fn, {
    baseInterval = 30_000,
    prefKey = null,
    onData = null,
    onError = null,
    immediate = true,
} = {}) {
    installGlobalListeners()
    startGlobalTicker()

    let timer = null
    const lastRefreshAt = ref(0)
    // 当前生效的间隔（毫秒）。受 prefKey 覆盖；未配置时用 baseInterval。0 = 暂停。
    const currentIntervalMs = ref(baseInterval)

    async function run() {
        const res = await fn()
        if (res && typeof res === 'object' && 'ok' in res) {
            if (res.ok) onData && onData(res.data)
            else onError && onError(res.error, res.code)
        }
        lastRefreshAt.value = Date.now()
    }

    function reschedule() {
        if (timer) { clearInterval(timer); timer = null }
        const intervalMs = currentIntervalMs.value
        if (intervalMs <= 0) return  // 暂停模式：不安排定时器
        const multiplier = MODE_MULTIPLIER[refreshMode.value] || 1
        timer = setInterval(run, intervalMs * multiplier)
    }

    /**
     * 改变刷新间隔（秒）。0 表示暂停。
     * 持久化到 user_preferences（如果传了 prefKey），并立即重排定时器。
     */
    async function setRefreshInterval(seconds) {
        const ms = seconds === 0 ? 0 : seconds * 1000
        currentIntervalMs.value = ms
        if (prefKey) {
            try { await api.setUserPreference(prefKey, seconds) } catch (e) { console.warn(e) }
        }
        reschedule()
    }

    // 距离下次刷新的秒数。null = 还没首次拉取；-1 = 暂停模式
    const secondsUntilNext = computed(() => {
        if (currentIntervalMs.value <= 0) return -1
        if (!lastRefreshAt.value) return null
        const interval = currentIntervalMs.value * (MODE_MULTIPLIER[refreshMode.value] || 1)
        const remainingMs = (lastRefreshAt.value + interval) - now.value
        return Math.max(0, Math.ceil(remainingMs / 1000))
    })

    // 当前周期（秒），UI 用来给倒计时圆环算占比、给 popover 高亮选中项
    const currentInterval = computed(() => Math.round(currentIntervalMs.value / 1000))

    onMounted(async () => {
        if (prefKey) {
            try {
                const res = await api.getUserPreference(prefKey)
                if (res.ok && res.data != null) {
                    const sec = Number(res.data)
                    if (!Number.isNaN(sec) && sec >= 0) {
                        currentIntervalMs.value = sec * 1000
                    }
                }
            } catch (e) { console.warn(e) }
        }
        if (immediate) run()
        reschedule()
    })

    const stopWatch = watch(refreshMode, () => reschedule())

    onUnmounted(() => {
        if (timer) clearInterval(timer)
        stopWatch()
    })

    return {
        refresh: run,
        mode: refreshMode,
        lastRefreshAt,
        secondsUntilNext,
        currentInterval,
        setRefreshInterval,
    }
}
