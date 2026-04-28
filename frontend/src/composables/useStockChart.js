/**
 * 全局 K 线 drawer 状态。
 *
 * 任何地方（自选 / 持仓 / 涨跌池表格行的双击）都可调 openStockChart(code, name)
 * 让主界面底部滑出 K 线 drawer。再次调用换股；调 closeStockChart() 收起。
 *
 * 状态是模块级单例，App.vue 里挂一个 <StockChartDrawer /> 监听就行。
 */
import { ref } from 'vue'

export const stockChartState = ref({
    visible: false,
    code:    '',
    name:    '',
})

export function openStockChart(code, name = '') {
    if (!code) return
    stockChartState.value = { visible: true, code: String(code), name: String(name || '') }
}

export function closeStockChart() {
    stockChartState.value = { ...stockChartState.value, visible: false }
}
