/**
 * 通用「弹窗拖动」composable —— 给 modal 加抓拽搬位能力。
 *
 * 用法：
 *   const modalRef = ref(null)
 *   const { style, onMouseDown, reset } = useDraggable(modalRef)
 *
 *   <div :style="style" :class="...absolute..." ref="modalRef">
 *       <header @mousedown="onMouseDown" class="cursor-move select-none">...</header>
 *       ...
 *   </div>
 *
 *   // modal 重新打开时调 reset() 让位置回到屏幕中央
 *   watch(() => props.open, (v) => { if (v) reset() })
 *
 * 实现要点：
 *   - 初始 pos = { x: null, y: null } → style 用 50% + translate(-50%) 精确居中
 *   - 第一次拖动时把当前 viewport 坐标抓进 pos，之后用绝对 left/top 跟踪
 *   - 拖动只在 header 触发；点击 header 上的 button / input / label 时跳过（保留原生交互）
 *   - 用 window.innerWidth/Height 限位防止拖出视口
 */
import { ref, computed, onUnmounted } from 'vue'

const DRAG_MIN_VISIBLE_PX = 50   // 拖到边缘时，至少留 50px 在视口内（防止"丢失" modal）

export function useDraggable(modalRef) {
    const pos = ref({ x: null, y: null })
    const dragging = ref(false)
    const dragOffset = ref({ x: 0, y: 0 })

    const style = computed(() => {
        if (pos.value.x == null) {
            // 初始 / reset 后：CSS 精确居中（不依赖 flex 父容器，避免 modal 容器布局耦合）
            return {
                left: '50%',
                top: '50%',
                transform: 'translate(-50%, -50%)',
            }
        }
        return {
            left: pos.value.x + 'px',
            top: pos.value.y + 'px',
        }
    })

    function onMouseMove(e) {
        if (!dragging.value) return
        const x = e.clientX - dragOffset.value.x
        const y = e.clientY - dragOffset.value.y
        // 限位：至少留 DRAG_MIN_VISIBLE_PX 在视口内，防止用户拖出去找不回来
        const maxX = window.innerWidth - DRAG_MIN_VISIBLE_PX
        const maxY = window.innerHeight - DRAG_MIN_VISIBLE_PX
        const minX = -(modalRef.value?.offsetWidth ?? 200) + DRAG_MIN_VISIBLE_PX
        pos.value = {
            x: Math.min(Math.max(x, minX), maxX),
            y: Math.min(Math.max(y, 0), maxY),
        }
    }

    function onMouseUp() {
        dragging.value = false
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
    }

    function onMouseDown(e) {
        // header 上的 button / input / label / select / textarea / [contenteditable] 不触发拖动 ——
        // 否则用户点叉号关弹窗、点筛选 chip、改输入时会被误启动拖动
        if (e.target.closest('button, input, select, textarea, label, [contenteditable]')) return

        const el = modalRef.value
        if (!el) return

        // 第一次拖动：把当前居中位置（CSS 50%+translate）转换成 viewport 绝对坐标
        const rect = el.getBoundingClientRect()
        pos.value = { x: rect.left, y: rect.top }
        dragOffset.value = {
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
        }
        dragging.value = true
        document.addEventListener('mousemove', onMouseMove)
        document.addEventListener('mouseup', onMouseUp)
        e.preventDefault()
    }

    function reset() {
        pos.value = { x: null, y: null }
    }

    onUnmounted(() => {
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
    })

    return { style, onMouseDown, reset, dragging }
}
