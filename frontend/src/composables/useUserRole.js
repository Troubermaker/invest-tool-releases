/**
 * 用户角色（普通 / 管理员）全局状态。
 *
 * - 模块级 ref，App.vue 启动后调一次 refresh()，全局组件直接读 isAdmin
 * - 解锁管理员：unlockAdmin(password) → 后端校验 → 更新本地状态
 * - 退出管理员：disableAdmin()
 *
 * 普通用户：基础 K 线 + MA/BOLL/MACD/KDJ + 自选 + 持仓 + 行情页
 * 管理员：以上全部 + 复杂指标（三维启动 / 主升 / 缺口 / 共振 等）+ 找候选 / 找发车
 */
import { ref } from 'vue'
import { api } from '../api/client'
import { pushSuccess, pushError } from './useNotifications'

const _isAdmin = ref(false)
const _loaded  = ref(false)

async function refresh() {
    try {
        const res = await api.isAdmin()
        if (res.ok) _isAdmin.value = !!res.data
    } catch (e) { /* 静默：未激活时也会走到这，不影响体验 */ }
    finally {
        _loaded.value = true
    }
}

async function unlock(password) {
    const res = await api.unlockAdmin(password)
    if (res.ok && res.data === true) {
        _isAdmin.value = true
        pushSuccess('已开启管理员模式')
        return true
    }
    pushError(res.error || '密码错误')
    return false
}

async function disable() {
    const res = await api.disableAdmin()
    if (res.ok) {
        _isAdmin.value = false
        pushSuccess('已退出管理员模式')
    }
}

export function useUserRole() {
    return {
        isAdmin: _isAdmin,
        loaded: _loaded,
        refresh,
        unlock,
        disable,
    }
}
