/**
 * 全局 K 线 drawer 状态。
 *
 * 任何地方（自选 / 持仓 / 涨跌池表格行的双击）都可调 openStockChart(code, name, stockList?)
 * 让主界面底部滑出 K 线 drawer。再次调用换股；调 closeStockChart() 收起。
 *
 * stockList 可选，传入后 drawer 左侧会显示该列表，点击任意条目切换 K 线 —— 适合
 * 从一个表格连续浏览多只票的场景。
 *
 * 状态是模块级单例，App.vue 里挂一个 <StockChartDrawer /> 监听就行。
 */
import { ref } from 'vue'

export const stockChartState = ref({
    visible:   false,
    code:      '',
    name:      '',
    stockList: [],   // [{ code, name }, ...] 可选侧栏列表
})

export function openStockChart(code, name = '', stockList = null) {
    if (!code) return
    // 标准化列表：只保留 code/name；去重（按 code）；加进 list 末端的当前股（如果列表里没有）
    const normalizedList = []
    const seen = new Set()
    if (Array.isArray(stockList)) {
        for (const s of stockList) {
            if (!s || !s.code) continue
            const c = String(s.code)
            if (seen.has(c)) continue
            seen.add(c)
            normalizedList.push({ code: c, name: String(s.name || '') })
        }
    }
    if (!seen.has(String(code))) {
        normalizedList.push({ code: String(code), name: String(name || '') })
    }
    stockChartState.value = {
        visible:   true,
        code:      String(code),
        name:      String(name || ''),
        stockList: normalizedList,
    }
}

// 在已打开的 drawer 内切换当前股票（不动 stockList，保留侧栏）
export function switchToStock(code, name = '') {
    if (!code) return
    stockChartState.value = {
        ...stockChartState.value,
        code: String(code),
        name: String(name || ''),
    }
}

export function closeStockChart() {
    stockChartState.value = { ...stockChartState.value, visible: false }
}
