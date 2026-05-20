/**
 * 今日仪表盘抽屉的全局开关 —— 单例 composable，避免 sidebar/hub 之间 prop drilling。
 *
 * 任何组件都可以 import { useDashboard } 然后 toggle()/open()/close()，
 * App.vue 里的 <DashboardDrawer> 监听这同一个 ref。
 *
 * 触发入口：
 *   - 顶部 Hub 的 "📊 今日" 按钮（行情中心 / 量化中心 右上角）
 *   - 全局快捷键 Ctrl+J
 */
import { ref } from 'vue'

const isOpen = ref(false)

export function useDashboard() {
    return {
        isOpen,
        toggle: () => { isOpen.value = !isOpen.value },
        open:   () => { isOpen.value = true },
        close:  () => { isOpen.value = false },
    }
}
