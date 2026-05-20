/**
 * 每日 09:25+ 自动扫描 + 桌面推送（P1.1）
 *
 * 触发逻辑：
 *   1. App 启动 5 秒后：检查今日是否已扫，未扫且时间已过 09:25 → 立刻扫
 *   2. 每分钟轮询：到 09:25 之后第一次满足条件 → 触发
 *   3. 用户在「设置」可禁用 / 改时间（持久化在 user_preferences）
 *
 * 扫完会：
 *   - 过滤 ⭐⭐⭐+ 信号
 *   - 调 saveAutoScanResult 持久化
 *   - 调 sendDesktopNotification 推 toast
 *   - 触发 reactive ref，UI banner 可显示今日命中数
 *
 * 单例模块（不要重复 mount）。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { useRecentMarketScan } from './useRecentMarketScan'

// ---------------- 配置 ----------------
const DEFAULT_HOUR = 9
const DEFAULT_MIN = 25
const STAR_PUSH_THRESHOLD = 3   // ⭐⭐⭐+ 才推送

// ---------------- state（模块单例）----------------
const enabled = ref(true)            // 总开关
const triggerHour = ref(DEFAULT_HOUR)
const triggerMin = ref(DEFAULT_MIN)
const lastAutoRunDate = ref(null)    // 'YYYY-MM-DD' 今日已跑标记
const todayResult = ref(null)        // 今日扫描结果（持久化 + 反射）
const lastError = ref(null)

let _checkTimer = null
let _running = false   // 正在跑标记，防并发

// ---------------- 工具 ----------------
function _today() {
    return new Date().toISOString().slice(0, 10)
}

function _isMarketDay() {
    const day = new Date().getDay()
    return day >= 1 && day <= 5   // 周一到周五
}

function _isPastTriggerTime() {
    const now = new Date()
    const m = now.getHours() * 60 + now.getMinutes()
    return m >= (triggerHour.value * 60 + triggerMin.value)
}

function _isBeforeMarketClose() {
    // 当日 15:00 前还有意义（开盘前 / 盘中扫到的 stage 3 突破依然有效）
    const now = new Date()
    const m = now.getHours() * 60 + now.getMinutes()
    return m < 15 * 60 + 5   // 15:05 之前
}

function shouldRunNow() {
    if (!enabled.value) return false
    if (_running) return false
    if (lastAutoRunDate.value === _today()) return false
    if (!_isMarketDay()) return false
    if (!_isPastTriggerTime()) return false
    if (!_isBeforeMarketClose()) return false
    return true
}

// ---------------- 星级判定（跟 Quant.vue 一致）----------------
function _getStarLevel(t) {
    const confirmGood = t.breakoutConfirm === 'strong' || t.breakoutConfirm === 'medium'
    const hasLhb = t.lhbInWindow === 1
    if (confirmGood && hasLhb) return 4
    if (confirmGood || hasLhb) return 3
    if (t.breakoutConfirm === 'medium') return 2
    if (t.breakoutConfirm === 'strong') return 1
    if (typeof t.sectorScore === 'number' && t.sectorScore >= 70) return 1
    if (typeof t.mlScore === 'number' && t.mlScore >= 0.40) return 1
    return 0
}

// ---------------- 核心扫描 ----------------
async function performAutoScan() {
    if (_running) return null
    _running = true
    lastError.value = null
    try {
        const market = useRecentMarketScan()
        // 复用现有扫描器（同 UI 行为）
        await market.scan()

        const trades = market.trades.value || []
        // 仅近 5 天突破（"早盘扫"主要看近期信号）
        const open = trades.filter(t => t.truncated && (t.holdBars ?? 99) <= 5)
        // 给每个 trade 计算星级（runRecentMarketScan 已注入 sector/LHB）
        for (const t of open) t._star = _getStarLevel(t)
        const star3Plus = open.filter(t => t._star >= STAR_PUSH_THRESHOLD)
        const star4 = star3Plus.filter(t => t._star >= 4)

        // 持久化（前 20 个）
        const topCodes = star3Plus.slice(0, 20).map(t => ({
            code:            t.code,
            name:            t.name,
            starLevel:       t._star,
            breakoutConfirm: t.breakoutConfirm || null,
            sectorScore:     t.sectorScore ?? null,
            lhbInWindow:     t.lhbInWindow ?? 0,
            lhbCount:        t.lhbCount ?? 0,
            mlScore:         t.mlScore ?? null,
            entryPrice:      t.entryPrice ?? null,
            s3Time:          t.s3Time ?? null,
        }))
        try {
            await api.saveAutoScanResult({
                scan_date:     _today(),
                star_count:    star3Plus.length,
                star4_count:   star4.length,
                top_codes:     topCodes,
                total_scanned: market.scanned.value || 0,
                notes:         `auto-trigger@${triggerHour.value}:${String(triggerMin.value).padStart(2,'0')}`,
            })
        } catch (e) { /* 失败不阻塞 */ }

        // 桌面通知
        if (star3Plus.length) {
            try {
                const top3 = star3Plus.slice(0, 3).map(t => t.name).join(' / ')
                await api.sendDesktopNotification(
                    `📈 今日 ${star3Plus.length} 只 ⭐⭐⭐+ 信号`,
                    `${star4.length ? '⭐⭐⭐⭐ ' + star4.length + ' 只 · ' : ''}Top: ${top3}`,
                    12,
                )
            } catch (e) { /* 通知失败不阻塞 */ }
        } else {
            // 也通知一下"没信号"，让用户知道扫过了
            try {
                await api.sendDesktopNotification('📊 今日扫描完成', '暂无 ⭐⭐⭐+ 信号，可静默观望', 8)
            } catch (e) {}
        }

        lastAutoRunDate.value = _today()
        await loadTodayResult()
        return { star3Plus: star3Plus.length, star4: star4.length }
    } catch (e) {
        lastError.value = String(e)
        console.warn('[auto-scan] 扫描失败:', e)
        return null
    } finally {
        _running = false
    }
}

// ---------------- 启动 / 停止 ----------------
async function loadTodayResult() {
    try {
        const r = await api.getTodayAutoScan()
        if (r?.ok) {
            todayResult.value = r.data || null
            if (todayResult.value?.scan_date) {
                lastAutoRunDate.value = todayResult.value.scan_date
            }
        }
    } catch (e) { /* 静默 */ }
}

function start() {
    // 启动时先拉一次"今日结果"，避免重复跑
    loadTodayResult()
    if (_checkTimer) clearInterval(_checkTimer)
    // 每 60s 检查一次
    _checkTimer = setInterval(() => {
        if (shouldRunNow()) performAutoScan().catch(() => {})
    }, 60_000)
    // 启动 5s 后立即检查（catch up：app 在 09:25 之后才打开）
    setTimeout(() => {
        if (shouldRunNow()) performAutoScan().catch(() => {})
    }, 5000)
}

function stop() {
    if (_checkTimer) {
        clearInterval(_checkTimer)
        _checkTimer = null
    }
}

// 手动触发（设置页 / banner 上的"立即扫描"按钮）
async function triggerNow() {
    lastAutoRunDate.value = null   // 解锁今日已跑标记
    return performAutoScan()
}

// ---------------- 暴露 ----------------
const hasResult = computed(() => !!todayResult.value)

export function useDailyAutoScan() {
    return {
        enabled, triggerHour, triggerMin,
        todayResult, hasResult, lastError, lastAutoRunDate,
        start, stop, triggerNow, loadTodayResult,
    }
}
