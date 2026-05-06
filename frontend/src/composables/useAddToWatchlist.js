/**
 * 全局"添加到自选"流程的状态。
 *
 * 支持两种模式：
 *   - single（默认）：openAddToWatchlist(code, name, price) → 单只票添加
 *   - batch：       openAddToWatchlistBatch(stocks) → 批量添加，stocks=[{code, name, price?}]
 *
 * 用户选定分组后，单条走 addWatchlistStock，批量走 importBatchAdd。
 * AddToWatchlistModal 监听这个 state，挂在 App.vue 一次。
 */
import { ref } from 'vue'

export const addToWatchlistState = ref({
    visible: false,
    mode:   'single',     // 'single' | 'batch'
    code:   '',
    name:   '',
    price:  null,
    stocks: [],           // batch 模式用：[{code, name, price?}]
})

export function openAddToWatchlist(code, name = '', price = null) {
    if (!code) return
    addToWatchlistState.value = {
        visible: true,
        mode:   'single',
        code, name, price,
        stocks: [],
    }
}

export function openAddToWatchlistBatch(stocks) {
    if (!Array.isArray(stocks) || !stocks.length) return
    // 去重 + 过滤无 code
    const seen = new Set()
    const cleaned = []
    for (const s of stocks) {
        const c = String(s?.code || '').trim()
        if (!c || seen.has(c)) continue
        seen.add(c)
        cleaned.push({ code: c, name: (s.name || '').trim(), price: s.price ?? null })
    }
    if (!cleaned.length) return
    addToWatchlistState.value = {
        visible: true,
        mode:   'batch',
        code:   '',
        name:   '',
        price:  null,
        stocks: cleaned,
    }
}

export function closeAddToWatchlist() {
    addToWatchlistState.value.visible = false
}
