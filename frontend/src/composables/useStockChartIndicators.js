/**
 * K 线 drawer 的指标 chip 选择 — 跨股票/跨会话持久化。
 *
 * 之前 activeIndicators 是 StockChart 组件内的 ref，drawer 用 :key="code" 重挂载
 * 切换股票时会被重置回默认。把它提到模块级 + localStorage 持久化，做到：
 *   - 切换股票：同一组 chip 仍然亮着
 *   - 关闭 drawer 重新打开：恢复用户上次的选择
 *   - 重启应用：localStorage 恢复
 *   - 用户取消勾选：立即同步保存
 */
import { ref, watch } from 'vue'

const STORAGE_KEY = 'stock_chart_indicators_v1'
const BASIC_IDS = new Set(['MA', 'BOLL', 'MACD', 'KDJ'])

function loadStored() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY)
        if (raw) {
            const arr = JSON.parse(raw)
            if (Array.isArray(arr) && arr.every(x => typeof x === 'string')) {
                return arr
            }
        }
    } catch (e) { /* localStorage 不可用就走默认 */ }
    return null
}

function saveStored(val) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
    } catch (e) { /* 静默：不影响功能 */ }
}

// 模块级单例：所有 StockChart 实例共享同一个 ref
// 默认值：管理员能看 TRIPLE，普通用户只 MA/MACD/KDJ
const DEFAULT_INDICATORS = ['MA', 'MACD', 'KDJ', 'TRIPLE']
const _stored = loadStored()
const activeIndicators = ref(_stored || DEFAULT_INDICATORS)

// 自动持久化（深监听数组内部变化）
watch(activeIndicators, (val) => saveStored(val), { deep: true })

export function useStockChartIndicators() {
    /** 角色降级（管理员退出）时调用：剔除超出权限的高级 chip */
    function clampToBasic() {
        activeIndicators.value = activeIndicators.value.filter(id => BASIC_IDS.has(id))
    }
    return { activeIndicators, clampToBasic }
}
