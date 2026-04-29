/**
 * 全局"添加到自选"流程的状态。
 * 任意位置调 openAddToWatchlist(code, name, price) → 弹出分组选择 modal。
 * AddToWatchlistModal 监听这个 state，挂在 App.vue 一次。
 */
import { ref } from 'vue'

export const addToWatchlistState = ref({
    visible: false,
    code: '',
    name: '',
    price: null,
})

export function openAddToWatchlist(code, name = '', price = null) {
    if (!code) return
    addToWatchlistState.value = {
        visible: true,
        code,
        name,
        price,
    }
}

export function closeAddToWatchlist() {
    addToWatchlistState.value.visible = false
}
