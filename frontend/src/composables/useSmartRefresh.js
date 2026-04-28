/**
 * 可见性 + 市场会话感知的定时轮询。
 *
 * 三层降速 / 暂停策略：
 *   1. 窗口可见性
 *      - 'active'      窗口可见，正常刷新（baseInterval）
 *      - 'hidden'      窗口隐藏 / 最小化 < 5 分钟，6× 间隔
 *      - 'deep-hidden' 隐藏 ≥ 5 分钟，18× 间隔
 *   2. 市场会话
 *      - 盘外（盘前 / 午休 / 盘后 / 周末）→ 自动暂停（数据已固定缓存，重复抓没意义）
 *        9:30 准点开盘自动恢复；周末永久暂停到周一 9:30
 *      - 不依赖 backend 节假日数据；周末检测足够大部分时候，节假日只是会多刷几次（无害）
 *   3. 用户偏好（每模块独立持久化）
 *      - 用户在 RefreshCountdown 选 0 = 暂停；选具体秒数 = 自定义周期
 *
 * 想 24x7 刷新（如快讯）→ 传 ignoreMarketHours: true 跳过市场会话检查
 *
 * 用法：
 *   const { secondsUntilNext, currentInterval, setRefreshInterval, refresh, marketSession } =
 *       useSmartRefresh(api.getMarketData, {
 *           baseInterval: 15_000,
 *           prefKey: 'refresh.market_data',
 *           onData: (d) => { marketIndices.value = d.indices },
 *       })
 */
import { onMounted, onUnmounted, ref, watch, computed } from 'vue'
import { api } from '../api/client'
import { pushError } from './useNotifications'
import { loadSnapshot, saveSnapshot } from './useSnapshot'

const HIDDEN_DEEP_MS = 5 * 60 * 1000  // 隐藏超过 5 分钟算 deep-hidden

// ---------------- 模块级全局状态 ----------------
const isHidden    = ref(typeof document !== 'undefined' ? document.hidden : false)
const hiddenSince = ref(isHidden.value ? Date.now() : null)
// 每秒推一次时间戳：1) 驱动 deep-hidden 判定 2) 驱动倒计时 UI 3) 驱动市场会话切换
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

// ---------------- 市场会话（A 股交易时段判定）----------------
// 'morning'      9:30 - 11:30 上半场
// 'lunch'        11:30 - 13:00 午休
// 'afternoon'    13:00 - 15:00 下半场
// 'after-close'  15:00 - 24:00 盘后
// 'before-open'  0:00 - 9:30   盘前
// 'weekend'      周六/周日全天
// 'holiday'      工作日里的法定节假日（如清明 / 国庆 / 春节调休等）
//
// 节假日靠 backend 的 chinese_calendar 给的交易日清单（getTradingDays），
// 首次启动时异步拉一次，挂在 tradingDaysSet。在 set 加载完成前，
// 仅靠周末判断作为兜底，节假日会被暂时误判为开盘日（无危害——backend 缓存挡上游）。

// 后端交易日集合（'YYYY-MM-DD'）。null=尚未加载；Set=已加载（即使是空 Set）
const tradingDaysSet = ref(null)

async function _loadTradingDays() {
    if (tradingDaysSet.value !== null) return  // 只加载一次
    try {
        const res = await api.getTradingDays()
        tradingDaysSet.value = (res.ok && Array.isArray(res.data))
            ? new Set(res.data)
            : new Set()
    } catch (e) {
        console.warn('加载交易日清单失败，节假日判断将退化为周末-only:', e)
        tradingDaysSet.value = new Set()
    }
}

function _localYmd(d) {
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
}

export const marketSession = computed(() => {
    // 用 now（每秒 tick）让计算具备响应性，跨过 9:30/11:30/13:00/15:00 时自动切状态
    const d = new Date(now.value)
    const day = d.getDay()
    if (day === 0 || day === 6) return 'weekend'

    // 节假日检测（仅当 set 已加载且非空时生效）
    const set = tradingDaysSet.value
    if (set && set.size > 0 && !set.has(_localYmd(d))) {
        return 'holiday'
    }

    const m = d.getHours() * 60 + d.getMinutes()
    if (m < 9 * 60 + 30)         return 'before-open'
    if (m < 11 * 60 + 30)        return 'morning'
    if (m < 13 * 60)             return 'lunch'
    if (m <= 15 * 60)            return 'afternoon'
    return 'after-close'
})

export const isMarketOpen = computed(() =>
    marketSession.value === 'morning' || marketSession.value === 'afternoon'
)

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
        // 关键：窗口从后台回前台时，立即把 now 拉到真实时间。
        // 否则若 setInterval 在后台被节流（Chrome / WebView2 都会），
        // 倒计时会停在切走前的数字，回前台后看着像"卡住"。
        now.value = Date.now()
    })
    // 窗口聚焦时也强制对齐——比 visibilitychange 更敏感
    window.addEventListener('focus', () => { now.value = Date.now() })
}

function startGlobalTicker() {
    if (globalTickerStarted || typeof window === 'undefined') return
    globalTickerStarted = true
    // 用自调度的 setTimeout 而非 setInterval：
    // 1) setInterval 后台被节流后，前台时大量积压触发，可能挤爆 main 线程
    // 2) setTimeout 链能自纠正（每次实际执行时间都对齐到 Date.now()）
    function tick() {
        now.value = Date.now()
        setTimeout(tick, 1000)
    }
    setTimeout(tick, 1000)
}

let tradingDaysLoadStarted = false
function ensureTradingDaysLoaded() {
    if (tradingDaysLoadStarted) return
    tradingDaysLoadStarted = true
    _loadTradingDays()  // 火种异步触发，不阻塞 useSmartRefresh
}


// ---------------- 核心 API ----------------
/**
 * @param {Function} fn API 调用函数，可以返回 {ok, data, error} 信封或 void
 * @param {Object} opts
 *   - baseInterval:        基础间隔 ms
 *   - prefKey:             可选，user_preferences key；值是秒数（0 = 暂停）
 *   - ignoreMarketHours:   true = 24x7 刷新（用于快讯等盘外也变的数据）；默认 false
 *   - snapshotKey:         可选，localStorage 快照 key —— mount 时先用上次的数据
 *                           瞬时渲染，再后台拉新替换。冷启动体感"瞬开"。
 *   - onData:              成功回调（信封模式）
 *   - onError:             失败回调（信封模式）
 *   - immediate:           挂载时立即拉一次，默认 true
 */
export function useSmartRefresh(fn, {
    baseInterval = 30_000,
    prefKey = null,
    ignoreMarketHours = false,
    snapshotKey = null,
    onData = null,
    onError = null,
    immediate = true,
} = {}) {
    installGlobalListeners()
    startGlobalTicker()
    ensureTradingDaysLoaded()

    let timer = null
    const lastRefreshAt = ref(0)
    // 当前生效的间隔（毫秒）。受 prefKey 覆盖；未配置时用 baseInterval。0 = 暂停。
    const currentIntervalMs = ref(baseInterval)

    async function run() {
        // try/finally 保证 lastRefreshAt 一定更新——
        // 即便 fn() 抛异常，倒计时也不会卡在 0 永不归位
        try {
            const res = await fn()
            if (res && typeof res === 'object' && 'ok' in res) {
                if (res.ok) {
                    onData && onData(res.data)
                    // 写快照供下次冷启动用
                    if (snapshotKey) saveSnapshot(snapshotKey, res.data)
                } else {
                    onError && onError(res.error, res.code)
                    _maybeToastError(res.error, res.code)
                }
            }
        } catch (e) {
            console.warn('[useSmartRefresh] run failed:', e)
            if (onError) onError(String(e), 'EXCEPTION')
            _maybeToastError(String(e), 'EXCEPTION')
        } finally {
            lastRefreshAt.value = Date.now()
        }
    }

    function _maybeToastError(msg, code) {
        // 静默吃掉的错误码：
        //   NOT_ACTIVATED → 用户没激活，激活页会处理，没必要弹
        //   NO_METHOD     → 后端方法名错（开发阶段），用户场景不该出现
        if (code === 'NOT_ACTIVATED' || code === 'NO_METHOD') return
        // 用 prefKey 作 dedupKey；同模块的同类错 30s 内只弹一次
        pushError(`数据获取失败：${msg || '未知错误'}`, {
            dedupKey: prefKey || `unknown_${code || 'err'}`,
        })
    }

    function shouldAutoRefresh() {
        // 用户暂停
        if (currentIntervalMs.value <= 0) return false
        // 盘外暂停（除非显式 opt-out）
        if (!ignoreMarketHours && !isMarketOpen.value) return false
        return true
    }

    function reschedule() {
        if (timer) { clearInterval(timer); timer = null }
        if (!shouldAutoRefresh()) return
        const multiplier = MODE_MULTIPLIER[refreshMode.value] || 1
        timer = setInterval(run, currentIntervalMs.value * multiplier)
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

    // 距离下次刷新的秒数。
    //   null = 还没首次拉取
    //   -1   = 用户主动暂停
    //   -2   = 盘外自动暂停（数据已固定缓存，无意义抓取）
    const secondsUntilNext = computed(() => {
        if (currentIntervalMs.value <= 0) return -1
        if (!ignoreMarketHours && !isMarketOpen.value) return -2
        if (!lastRefreshAt.value) return null
        const interval = currentIntervalMs.value * (MODE_MULTIPLIER[refreshMode.value] || 1)
        const remainingMs = (lastRefreshAt.value + interval) - now.value
        return Math.max(0, Math.ceil(remainingMs / 1000))
    })

    // 当前周期（秒），UI 用来给倒计时圆环算占比、给 popover 高亮选中项
    const currentInterval = computed(() => Math.round(currentIntervalMs.value / 1000))

    onMounted(async () => {
        // 1. 同步 hydrate 快照 —— 让用户在网络请求返回前先看到上次的数据
        //    这一步必须在 await 任何东西之前做，否则会有空白闪烁
        if (snapshotKey && onData) {
            const cached = loadSnapshot(snapshotKey)
            if (cached) {
                onData(cached.data)
                lastRefreshAt.value = cached.savedAt   // 倒计时基线 = 快照保存时间
            }
        }

        // 2. 异步加载用户的刷新间隔偏好
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

        // 3. 首次拉真实数据（用快照后立刻发起，~100-500ms 内替换）
        if (immediate) run()
        reschedule()
    })

    // 窗口可见性 / 市场会话 / 用户偏好 任一变化都重排定时器
    const stopWatch1 = watch(refreshMode, () => reschedule())
    const stopWatch2 = watch(isMarketOpen, () => reschedule())

    // 看门狗：如果距离上次刷新已经超过预期间隔的 2 倍，说明 setInterval
    // 可能被浏览器后台节流"丢"了 —— 主动触发一次 run() 自愈
    const watchdog = setInterval(() => {
        if (!shouldAutoRefresh()) return
        if (!lastRefreshAt.value) return
        const expected = currentIntervalMs.value * (MODE_MULTIPLIER[refreshMode.value] || 1)
        const elapsed = Date.now() - lastRefreshAt.value
        if (elapsed > expected * 2) {
            // 久未刷新，强制跑一次 + 重排定时器
            run()
            reschedule()
        }
    }, 10_000)  // 每 10s 自查一次，开销可忽略

    onUnmounted(() => {
        if (timer) clearInterval(timer)
        clearInterval(watchdog)
        stopWatch1()
        stopWatch2()
    })

    return {
        refresh: run,
        mode: refreshMode,
        marketSession,
        lastRefreshAt,
        secondsUntilNext,
        currentInterval,
        setRefreshInterval,
    }
}
