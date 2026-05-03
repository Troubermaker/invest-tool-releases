/**
 * 联动外部行情软件（通达信 / 同花顺）的全局 composable。
 *
 * 提供：
 *   - status: 当前两个软件是否运行（每 30s 自动探测一次）
 *   - settings: 用户的"启用联动"偏好（持久化到 user_preferences）
 *   - jumpTo(target, code): 触发联动跳转
 *
 * UX 约定：仅在 Settings 里勾选了对应软件 + 软件运行中时，行/按钮才会显示 📡 联动按钮。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'
import { pushSuccess, pushError } from './useNotifications'

const PREF_TDX = 'external.tdx_enabled'
const PREF_THS = 'external.ths_enabled'

// 模块级状态（所有调用者共享）
const _status   = ref({ platform_supported: true, tdx: { running: false }, ths: { running: false } })
const _enableTdx = ref(false)
const _enableThs = ref(false)
const _loaded   = ref(false)
let _statusTimer = null

async function _loadPrefs() {
    const a = await api.getUserPreference(PREF_TDX, false)
    if (a.ok && typeof a.data === 'boolean') _enableTdx.value = a.data
    const b = await api.getUserPreference(PREF_THS, false)
    if (b.ok && typeof b.data === 'boolean') _enableThs.value = b.data
}

async function _refreshStatus() {
    try {
        const res = await api.externalAppStatus()
        if (res.ok && res.data) _status.value = res.data
    } catch (e) { /* 静默 */ }
}

/** 启动后台轮询：每 30s 探测一次外部软件运行状态 */
function _ensurePolling() {
    if (_statusTimer) return
    _refreshStatus()
    _statusTimer = setInterval(_refreshStatus, 30_000)
}

async function init() {
    if (_loaded.value) return
    await _loadPrefs()
    _ensurePolling()
    _loaded.value = true
}

async function setEnabled(target, value) {
    const key = target === 'tdx' ? PREF_TDX : PREF_THS
    const ref_ = target === 'tdx' ? _enableTdx : _enableThs
    ref_.value = !!value
    await api.setUserPreference(key, !!value)
}

async function jumpTo(target, code) {
    if (!code) return false
    try {
        const res = await api.externalJumpToStock(target, code)
        if (res.ok && res.data?.ok) {
            return true
        }
        const msg = res.data?.msg || res.error || '联动失败'
        pushError(`📡 ${target.toUpperCase()} 联动失败：${msg}`)
        return false
    } catch (e) {
        pushError(`📡 联动异常：${e}`)
        return false
    }
}

export function useExternalApp() {
    return {
        status: _status,
        enableTdx: _enableTdx,
        enableThs: _enableThs,
        // 派生：显示按钮的最终条件 = 用户开 + 软件运行中
        showTdxButton: computed(() => _enableTdx.value && _status.value.tdx?.running),
        showThsButton: computed(() => _enableThs.value && _status.value.ths?.running),
        init,
        setEnabled,
        jumpTo,
        refreshStatus: _refreshStatus,
    }
}
