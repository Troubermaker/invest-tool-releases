<script setup>
/**
 * 通用行内操作下拉菜单
 *
 * 用法：父组件用 <slot> 填具体动作 button
 *
 *   <RowActionMenu>
 *     <button @click.stop="addJournal">📝 加日志</button>
 *     <button @click.stop="addWatch">+ 自选</button>
 *     <button @click.stop="viewChart">👁 看 K 线</button>
 *     <button @click.stop="remove" class="text-red">✕ 移除</button>
 *   </RowActionMenu>
 *
 * 行为：
 *   - 点 ⋯ 按钮 → 弹出下拉
 *   - 点 menu 外 / 触发任意 slot 按钮 → 自动关闭
 *   - 点滚动 / ESC → 关闭
 */
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'

const open = ref(false)
const triggerRef = ref(null)
const menuRef = ref(null)
const menuPos = ref({ top: 0, left: 0 })   // teleport 用绝对定位

function toggle(e) {
    e?.stopPropagation()
    if (open.value) {
        open.value = false
        return
    }
    // 计算菜单位置（按钮下方左对齐）
    const rect = triggerRef.value?.getBoundingClientRect()
    if (rect) {
        menuPos.value = {
            top:  rect.bottom + 2,
            left: rect.right - 140,    // 菜单宽 ~140px，右对齐按钮
        }
    }
    open.value = true
}

function close() {
    open.value = false
}

function onMenuClick(e) {
    // 用户点了 slot 里的 button → 自动关闭（让 button 的 @click 先触发，再关）
    nextTick(() => { open.value = false })
}

function onGlobalClick(e) {
    if (!open.value) return
    if (triggerRef.value?.contains(e.target)) return
    if (menuRef.value?.contains(e.target)) return
    open.value = false
}

function onKey(e) {
    if (e.key === 'Escape') open.value = false
}

onMounted(() => {
    document.addEventListener('click', onGlobalClick, true)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
})
onBeforeUnmount(() => {
    document.removeEventListener('click', onGlobalClick, true)
    document.removeEventListener('keydown', onKey)
    window.removeEventListener('scroll', close, true)
    window.removeEventListener('resize', close)
})
</script>

<template>
    <button ref="triggerRef" @click.stop="toggle"
            class="inline-flex items-center justify-center w-[24px] h-[20px] rounded text-[#666] hover:bg-[#f1f5f9] hover:text-[#dc2626] transition"
            title="更多操作">
        <span class="text-[14px] leading-none font-bold">⋯</span>
    </button>

    <Teleport to="body">
        <div v-if="open" ref="menuRef"
             @click="onMenuClick"
             :style="{ top: menuPos.top + 'px', left: menuPos.left + 'px' }"
             class="fixed z-[300] min-w-[140px] bg-white border border-[#e5e7eb] rounded-md shadow-md py-[3px]">
            <slot />
        </div>
    </Teleport>
</template>

<style scoped>
/* slot 里 button 默认样式 —— 父组件可覆盖 */
:slotted(button) {
    display: block;
    width: 100%;
    padding: 6px 10px;
    text-align: left;
    font-size: 12px;
    color: #475569;
    background: transparent;
    border: none;
    cursor: pointer;
    transition: background 0.1s;
}
:slotted(button:hover) {
    background: #fef2f2;
}
:slotted(button.text-red) {
    color: #dc2626;
}
:slotted(button:disabled) {
    opacity: 0.4;
    cursor: not-allowed;
}
</style>
