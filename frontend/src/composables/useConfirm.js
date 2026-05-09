/**
 * 全局确认框 —— 替代浏览器原生 confirm()，跟应用整体 modal 风格一致。
 *
 * 用法：
 *   import { confirmDialog } from '@/composables/useConfirm'
 *   const ok = await confirmDialog({
 *       title: '确认移除',
 *       message: '此操作不可撤销，是否继续？',
 *       danger: true,            // 危险操作 → 主按钮变红
 *       confirmText: '移除',
 *       cancelText: '取消',
 *   })
 *   if (!ok) return
 *
 * 实现：单例 state + Promise，由全局挂载的 ConfirmModal.vue 监听并渲染。
 * 同时只能有一个 confirm 在等待响应；新调起会强制 reject 旧的（避免遗留 promise）。
 */
import { ref } from 'vue'

const state = ref({
    visible:     false,
    title:       '',
    message:     '',
    confirmText: '确认',
    cancelText:  '取消',
    danger:      false,
    _resolve:    null,
})

export function confirmDialog({
    title = '确认',
    message = '',
    confirmText = '确认',
    cancelText = '取消',
    danger = false,
} = {}) {
    // 已有未关闭的 confirm？先 reject 它（防止 promise 永远悬而未决）
    if (state.value.visible && state.value._resolve) {
        state.value._resolve(false)
    }
    return new Promise((resolve) => {
        state.value = {
            visible: true,
            title, message, confirmText, cancelText, danger,
            _resolve: resolve,
        }
    })
}

export function useConfirm() {
    return {
        state,
        accept() {
            const r = state.value._resolve
            state.value = { ...state.value, visible: false, _resolve: null }
            r?.(true)
        },
        reject() {
            const r = state.value._resolve
            state.value = { ...state.value, visible: false, _resolve: null }
            r?.(false)
        },
    }
}
