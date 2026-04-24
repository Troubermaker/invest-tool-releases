<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'

// ---------------- 导出（原生"另存为"对话框，可选分区）----------------
const exporting = ref(false)
const exportMsg = ref('')
// 分区勾选（默认全部导出；分享给别人时可去掉"持仓"）
const exportSections = ref({ watchlist: true, portfolio: true, preferences: true })

async function handleExport() {
    const selected = Object.keys(exportSections.value).filter(k => exportSections.value[k])
    if (!selected.length) { exportMsg.value = '请至少选择一个分区'; return }
    exporting.value = true
    exportMsg.value = ''
    try {
        const res = await api.exportUserDataInteractive(selected)
        if (!res.ok) { exportMsg.value = '导出失败：' + (res.error || '未知错误'); return }
        if (res.data.cancelled) { exportMsg.value = ''; return }
        const c = res.data.counts || {}
        const parts = []
        if (c.watchlist_groups != null) parts.push(`${c.watchlist_groups} 个自选分组`)
        if (c.watchlist_stocks != null) parts.push(`${c.watchlist_stocks} 只自选股`)
        if (c.portfolio_accounts != null) parts.push(`${c.portfolio_accounts} 个持仓账户`)
        if (c.portfolio_positions != null) parts.push(`${c.portfolio_positions} 条持仓`)
        if (c.user_preferences != null) parts.push(`${c.user_preferences} 条偏好`)
        exportMsg.value = `已保存到 ${res.data.path}（${parts.join(' / ')}）`
    } finally {
        exporting.value = false
    }
}

// ---------------- 导入（原生"打开"对话框 → 预览 → 选模式 → 确认）----------------
const importPreview = ref(null)  // { path, counts, exportedAt, schemaVersion }
const importMode = ref('merge')  // 'merge' | 'replace'，默认合并（非破坏性）
const importMsg = ref('')
const importing = ref(false)

async function triggerFilePicker() {
    importMsg.value = ''
    const res = await api.pickBackupFile()
    if (!res.ok) { importMsg.value = '读取失败：' + (res.error || '未知错误'); return }
    if (res.data.cancelled) return
    importPreview.value = {
        path: res.data.path,
        schemaVersion: res.data.schema_version,
        exportedAt: res.data.exported_at,
        counts: {
            自选分组: res.data.counts?.watchlist_groups || 0,
            自选股: res.data.counts?.watchlist_stocks || 0,
            持仓账户: res.data.counts?.portfolio_accounts || 0,
            持仓: res.data.counts?.portfolio_positions || 0,
            用户偏好: res.data.counts?.user_preferences || 0,
        },
    }
}

function cancelImport() {
    importPreview.value = null
    importMsg.value = ''
}

async function confirmImport() {
    const isReplace = importMode.value === 'replace'
    const ok = await askConfirm({
        title: isReplace ? '确认覆盖导入' : '确认合并导入',
        message: isReplace
            ? '「替换模式」将清空当前设备所有自选和持仓数据，用备份文件完整替换。此操作不可恢复。'
            : '「合并模式」以名称/代码为键做 upsert：同键条目会被备份值覆盖（股数/成本价/自选价等），仅本地有的条目保留。用户偏好跳过。',
        confirmText: isReplace ? '确认覆盖' : '确认合并',
    })
    if (!ok) return
    importing.value = true
    importMsg.value = ''
    try {
        const res = await api.importBackupFile(importPreview.value.path, importMode.value)
        if (!res.ok) { importMsg.value = '导入失败：' + (res.error || '未知错误'); return }
        const ic = res.data?.imported || {}
        if (isReplace) {
            importMsg.value = `替换完成！写入 ${ic.watchlist_stocks || 0} 只自选股 / ${ic.portfolio_positions || 0} 条持仓。切回自选/持仓 tab 即可看到。`
        } else {
            const wg = ic.watchlist_groups || { added: 0, updated: 0 }
            const ws = ic.watchlist_stocks || { added: 0, updated: 0 }
            const pa = ic.portfolio_accounts || { added: 0, updated: 0 }
            const pp = ic.portfolio_positions || { added: 0, updated: 0 }
            importMsg.value =
                `合并完成！自选分组 新增 ${wg.added} / 已存在 ${wg.updated} · ` +
                `自选股 新增 ${ws.added} / 更新 ${ws.updated} · ` +
                `账户 新增 ${pa.added} / 已存在 ${pa.updated} · ` +
                `持仓 新增 ${pp.added} / 更新 ${pp.updated}`
        }
        importPreview.value = null
    } finally {
        importing.value = false
    }
}

// ---------------- 老板键 ----------------
const bossKey = ref(null)
const recording = ref(false)
const capturedMods = ref([])   // ['ctrl' | 'alt' | 'shift']
const capturedKey = ref(null)  // 'b' / '`' / 'f2' 等
const bossKeyMsg = ref('')

async function loadBossKey() {
    const res = await api.getBossKey()
    if (res.ok) bossKey.value = res.data.hotkey
}

function startRecording() {
    recording.value = true
    capturedMods.value = []
    capturedKey.value = null
    bossKeyMsg.value = ''
    window.addEventListener('keydown', onCaptureKeyDown, true)
    window.addEventListener('keyup', onCaptureKeyUp, true)
}

function cancelRecording() {
    recording.value = false
    capturedMods.value = []
    capturedKey.value = null
    bossKeyMsg.value = ''
    window.removeEventListener('keydown', onCaptureKeyDown, true)
    window.removeEventListener('keyup', onCaptureKeyUp, true)
}

// 浏览器 e.key → keyboard 库的按键名
function normalizeKey(e) {
    const k = e.key.toLowerCase()
    if (k === ' ') return 'space'
    if (k === 'escape') return 'esc'
    if (k === 'arrowup') return 'up'
    if (k === 'arrowdown') return 'down'
    if (k === 'arrowleft') return 'left'
    if (k === 'arrowright') return 'right'
    return k
}

function onCaptureKeyDown(e) {
    e.preventDefault()
    e.stopPropagation()
    const k = normalizeKey(e)
    // 如果本身是修饰键，只更新修饰键列表
    if (['control', 'alt', 'shift', 'meta'].includes(k)) {
        const mod = k === 'control' ? 'ctrl' : (k === 'meta' ? null : k)
        if (mod && !capturedMods.value.includes(mod)) {
            capturedMods.value = [...capturedMods.value, mod]
        }
        return
    }
    // 非修饰键 → 作为"第 2 键"捕获
    capturedKey.value = k
    // 同时重新计算当前按下的修饰键（避免之前松开又按下的脏数据）
    const mods = []
    if (e.ctrlKey) mods.push('ctrl')
    if (e.altKey) mods.push('alt')
    if (e.shiftKey) mods.push('shift')
    capturedMods.value = mods
}

function onCaptureKeyUp() {
    // 松开时不做处理；保存按钮会用当前捕获的值校验
}

const capturedCombo = computed(() => {
    const mods = capturedMods.value
    const key = capturedKey.value
    if (!key) return mods.join(' + ') || ''
    return [...mods, key].join(' + ')
})

const capturedComboRaw = computed(() => {
    const mods = capturedMods.value
    const key = capturedKey.value
    if (!key) return ''
    return [...mods, key].join('+')
})

const isValidCombo = computed(() => {
    return capturedMods.value.length === 1 && capturedKey.value && !['ctrl', 'alt', 'shift'].includes(capturedKey.value)
})

const validationHint = computed(() => {
    if (!capturedKey.value) return '先按住 Ctrl / Alt / Shift 中的一个，再按一个字母或符号键'
    if (capturedMods.value.length === 0) return '缺少修饰键（Ctrl / Alt / Shift）'
    if (capturedMods.value.length > 1) return `只能 1 个修饰键，当前有 ${capturedMods.value.length} 个（请只按住一个）`
    return '这个组合可以用 ✓'
})

async function saveBossKey() {
    if (!isValidCombo.value) return
    const res = await api.setBossKey(capturedComboRaw.value)
    if (!res.ok) {
        bossKeyMsg.value = '保存失败：' + (res.error || '未知错误')
        return
    }
    bossKey.value = res.data.hotkey
    bossKeyMsg.value = `已更新：${formatDisplay(res.data.hotkey)}`
    cancelRecording()
}

function formatDisplay(h) {
    if (!h) return '未设置'
    return h.split('+').map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' + ')
}

onMounted(() => { loadBossKey() })
onUnmounted(() => {
    // 若用户在录制中直接离开页面，清理监听
    window.removeEventListener('keydown', onCaptureKeyDown, true)
    window.removeEventListener('keyup', onCaptureKeyUp, true)
})

// ---------------- 通用确认弹窗 ----------------
const confirmState = ref({ show: false, title: '', message: '', confirmText: '确定', _resolve: null })
function askConfirm({ title = '确认操作', message = '', confirmText = '确定' } = {}) {
    return new Promise(resolve => {
        confirmState.value = { show: true, title, message, confirmText, _resolve: resolve }
    })
}
function confirmOk() {
    const r = confirmState.value._resolve
    confirmState.value.show = false
    r?.(true)
}
function confirmCancel() {
    const r = confirmState.value._resolve
    confirmState.value.show = false
    r?.(false)
}
</script>

<template>
  <div class="flex flex-col h-full bg-[#fcfcfc] overflow-auto custom-scrollbar">

    <!-- 页头 -->
    <div class="px-[24px] py-[20px] border-b border-[#e5e5e5] bg-white shrink-0">
        <div class="text-[18px] font-bold text-[#111]">设置</div>
        <div class="text-[12px] text-[#888] mt-[2px]">数据备份、偏好配置等</div>
    </div>

    <div class="flex-1 px-[24px] py-[20px]">

        <!-- ============ 数据备份 ============ -->
        <div class="max-w-[760px] bg-white border border-[#eeeeee] rounded-[8px] shadow-[0_1px_3px_rgba(0,0,0,0.02)]">
            <div class="px-[20px] py-[14px] border-b border-[#f0f0f0]">
                <div class="text-[14px] font-bold text-[#111]">数据备份</div>
                <div class="text-[12px] text-[#999] mt-[2px]">
                    导出当前电脑的所有自选股和持仓记录为 JSON 文件，在另一台电脑「导入」即可恢复。
                    文件可放在云盘 / 私有 git 仓库里版本化管理。
                </div>
            </div>

            <!-- 导出块 -->
            <div class="px-[20px] py-[16px] border-b border-[#f5f5f5]">
                <div class="flex items-start gap-[16px]">
                    <div class="flex-1">
                        <div class="text-[13px] font-semibold text-[#111]">导出数据</div>
                        <div class="text-[12px] text-[#888] mt-[2px]">勾选要导出的分区 —— 比如<b class="text-[#dc2626]">分享自选给朋友时去掉"持仓"</b>。不含市场行情缓存。</div>
                    </div>
                    <button @click="handleExport" :disabled="exporting"
                            class="shrink-0 text-[12px] font-bold text-white bg-[#dc2626] px-[16px] py-[7px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                        {{ exporting ? '导出中...' : '导出为 JSON' }}
                    </button>
                </div>
                <!-- 分区勾选 -->
                <div class="flex items-center gap-[16px] mt-[10px] text-[12px]">
                    <label class="flex items-center gap-[6px] cursor-pointer select-none">
                        <input type="checkbox" v-model="exportSections.watchlist" class="accent-[#dc2626]">
                        <span class="text-[#333]">自选（分组 + 股票）</span>
                    </label>
                    <label class="flex items-center gap-[6px] cursor-pointer select-none">
                        <input type="checkbox" v-model="exportSections.portfolio" class="accent-[#dc2626]">
                        <span class="text-[#333]">持仓（账户 + 持仓记录）</span>
                    </label>
                    <label class="flex items-center gap-[6px] cursor-pointer select-none">
                        <input type="checkbox" v-model="exportSections.preferences" class="accent-[#dc2626]">
                        <span class="text-[#666]">用户偏好（列顺序等）</span>
                    </label>
                </div>
                <div v-if="exportMsg"
                     class="text-[12px] mt-[10px] px-[10px] py-[6px] rounded-[4px]"
                     :class="exportMsg.startsWith('请') || exportMsg.startsWith('导出失败')
                         ? 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'
                         : 'text-[#059669] bg-[#f0fdf4] border border-[#dcfce7]'">
                    {{ exportMsg }}
                </div>
            </div>

            <!-- 导入块 -->
            <div class="px-[20px] py-[16px]">
                <div class="flex items-start gap-[16px]">
                    <div class="flex-1">
                        <div class="text-[13px] font-semibold text-[#111]">导入数据</div>
                        <div class="text-[12px] text-[#888] mt-[2px]">选择之前导出的 JSON 文件。<span class="text-[#dc2626]">将清空当前所有自选和持仓，用文件内容替换</span>，请先导出当前数据作为备份。</div>
                    </div>
                    <button @click="triggerFilePicker"
                            class="shrink-0 text-[12px] font-bold text-[#444] bg-white border border-[#d4d4d4] px-[16px] py-[7px] rounded-[4px] hover:bg-[#f5f5f5] hover:border-[#999] transition">
                        选择备份文件...
                    </button>
                </div>

                <!-- 导入预览 -->
                <div v-if="importPreview" class="mt-[14px] bg-[#fafafa] border border-[#eeeeee] rounded-[6px] p-[14px]">
                    <div class="flex items-center justify-between">
                        <div>
                            <div class="text-[12px] font-semibold text-[#111]">备份文件预览</div>
                            <div class="text-[11px] text-[#999] mt-[2px]">
                                生成时间：{{ importPreview.exportedAt || '—' }} / schema v{{ importPreview.schemaVersion }}
                            </div>
                        </div>
                    </div>
                    <div class="grid grid-cols-5 gap-[8px] mt-[12px]">
                        <div v-for="(v, k) in importPreview.counts" :key="k"
                             class="bg-white border border-[#eeeeee] rounded-[4px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#999]">{{ k }}</div>
                            <div class="text-[15px] font-bold text-[#111] tabular-nums mt-[2px]">{{ v }}</div>
                        </div>
                    </div>

                    <!-- 模式选择 -->
                    <div class="mt-[14px] border-t border-[#eeeeee] pt-[12px]">
                        <div class="text-[12px] font-semibold text-[#111] mb-[8px]">导入方式</div>
                        <label class="flex items-start gap-[8px] px-[10px] py-[8px] rounded-[4px] cursor-pointer transition"
                               :class="importMode === 'merge' ? 'bg-white border border-[#dc2626]' : 'border border-transparent hover:bg-white/60'">
                            <input type="radio" value="merge" v-model="importMode" class="mt-[3px] accent-[#dc2626]">
                            <div class="flex-1">
                                <div class="text-[12px] font-semibold text-[#111]">合并（增量更新）<span class="ml-[6px] text-[10px] font-normal text-[#dc2626]">推荐</span></div>
                                <div class="text-[11px] text-[#888] mt-[1px] leading-relaxed">
                                    按「分组名/账户名/代码」匹配：<b>同键条目用备份值覆盖</b>（自选价、持股、成本价、备注等），<b>仅本地有的条目原样保留</b>。适合两台电脑都编辑过、希望同步增量改动的场景。
                                </div>
                            </div>
                        </label>
                        <label class="flex items-start gap-[8px] px-[10px] py-[8px] rounded-[4px] cursor-pointer transition mt-[4px]"
                               :class="importMode === 'replace' ? 'bg-white border border-[#dc2626]' : 'border border-transparent hover:bg-white/60'">
                            <input type="radio" value="replace" v-model="importMode" class="mt-[3px] accent-[#dc2626]">
                            <div class="flex-1">
                                <div class="text-[12px] font-semibold text-[#111]">替换（清空后全量写入）</div>
                                <div class="text-[11px] text-[#888] mt-[1px] leading-relaxed">
                                    先清空本地数据再用备份完整覆盖，本地独有的条目会丢失。适合<b>全新电脑首次迁移</b>或者<b>想以备份为权威版本</b>的场景。
                                </div>
                            </div>
                        </label>
                    </div>

                    <div class="flex justify-end gap-[8px] mt-[14px]">
                        <button @click="cancelImport"
                                class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-white transition">
                            取消
                        </button>
                        <button @click="confirmImport" :disabled="importing"
                                class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                            {{ importing ? '导入中...' : (importMode === 'merge' ? '确认合并导入' : '确认覆盖导入') }}
                        </button>
                    </div>
                </div>

                <div v-if="importMsg"
                     class="text-[12px] mt-[10px] px-[10px] py-[6px] rounded-[4px]"
                     :class="importMsg.includes('成功') ? 'text-[#059669] bg-[#f0fdf4] border border-[#dcfce7]' : 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'">
                    {{ importMsg }}
                </div>
            </div>
        </div>

        <!-- 提示：手工拷库路径 -->
        <div class="max-w-[760px] mt-[16px] bg-[#fffbeb] border border-[#fde68a] rounded-[6px] px-[14px] py-[10px] text-[12px] text-[#92400e]">
            💡 应急方案：也可以直接把 <code class="bg-white px-[5px] py-[1px] rounded text-[11px]">invest_data.db</code> 拷贝到另一台电脑的相同路径，但不建议用云盘自动同步（SQLite 锁冲突风险）。
        </div>

        <!-- ============ 老板键 ============ -->
        <div class="max-w-[760px] mt-[20px] bg-white border border-[#eeeeee] rounded-[8px] shadow-[0_1px_3px_rgba(0,0,0,0.02)]">
            <div class="px-[20px] py-[14px] border-b border-[#f0f0f0]">
                <div class="text-[14px] font-bold text-[#111]">老板键</div>
                <div class="text-[12px] text-[#999] mt-[2px]">
                    一键隐藏 / 恢复窗口。窗口从任务栏、Alt+Tab 彻底消失，再按同组合恢复。
                    <b>只允许 2 键组合（1 个修饰键 + 1 个普通键）</b>。
                </div>
            </div>

            <div class="px-[20px] py-[16px]">
                <!-- 查看态 -->
                <div v-if="!recording" class="flex items-center justify-between">
                    <div>
                        <div class="text-[12px] text-[#888]">当前快捷键</div>
                        <div class="text-[16px] font-bold text-[#111] mt-[4px] tabular-nums">
                            {{ formatDisplay(bossKey) }}
                        </div>
                    </div>
                    <button @click="startRecording"
                            class="shrink-0 text-[12px] font-bold text-[#444] bg-white border border-[#d4d4d4] px-[16px] py-[7px] rounded-[4px] hover:bg-[#f5f5f5] hover:border-[#999] transition">
                        修改快捷键
                    </button>
                </div>

                <!-- 录制态 -->
                <div v-else
                     class="bg-[#fafafa] border border-dashed border-[#fbbf24] rounded-[6px] px-[14px] py-[14px]">
                    <div class="text-[12px] text-[#92400e] mb-[8px]">请按下新的快捷键组合 ⌨</div>
                    <div class="text-[20px] font-bold text-[#111] tabular-nums tracking-wider py-[10px] text-center bg-white rounded-[4px] border border-[#eeeeee]">
                        {{ capturedCombo || '等待按键...' }}
                    </div>
                    <div class="text-[11px] mt-[8px]"
                         :class="isValidCombo ? 'text-[#059669]' : 'text-[#888]'">
                        {{ validationHint }}
                    </div>
                    <div class="flex justify-end gap-[8px] mt-[12px]">
                        <button @click="cancelRecording"
                                class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-white">
                            取消
                        </button>
                        <button @click="saveBossKey" :disabled="!isValidCombo"
                                class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                            保存
                        </button>
                    </div>
                </div>

                <div v-if="bossKeyMsg && !recording"
                     class="text-[12px] mt-[10px] px-[10px] py-[6px] rounded-[4px]"
                     :class="bossKeyMsg.includes('失败')
                         ? 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'
                         : 'text-[#059669] bg-[#f0fdf4] border border-[#dcfce7]'">
                    {{ bossKeyMsg }}
                </div>

                <div class="mt-[12px] text-[11px] text-[#999]">
                    推荐组合：Ctrl + `（反引号）、Alt + Z、Ctrl + B 等。避免与浏览器 / 系统常用快捷键冲突。
                </div>
            </div>
        </div>

    </div>

    <!-- ============ 通用确认弹窗 ============ -->
    <div v-if="confirmState.show"
         class="fixed inset-0 bg-black/25 z-[60] flex items-center justify-center"
         @click.self="confirmCancel">
        <div class="bg-white rounded-[8px] w-[400px] shadow-[0_10px_40px_rgba(0,0,0,0.18)] overflow-hidden">
            <div class="flex items-start gap-[12px] px-[20px] pt-[20px] pb-[4px]">
                <div class="w-[36px] h-[36px] rounded-full bg-[#fef2f2] flex items-center justify-center shrink-0">
                    <svg class="w-[18px] h-[18px] text-[#dc2626]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l6.518 11.59c.75 1.335-.213 2.98-1.742 2.98H3.48c-1.53 0-2.493-1.645-1.743-2.98L8.257 3.1zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                    </svg>
                </div>
                <div class="flex-1 min-w-0 pt-[4px]">
                    <div class="text-[15px] font-bold text-[#111] leading-tight">{{ confirmState.title }}</div>
                </div>
            </div>
            <div class="px-[20px] pl-[68px] py-[10px] text-[13px] text-[#555] leading-relaxed">
                {{ confirmState.message }}
            </div>
            <div class="flex justify-end gap-[8px] px-[20px] py-[14px] bg-[#fafafa] border-t border-[#f0f0f0]">
                <button @click="confirmCancel"
                        class="text-[12px] px-[16px] py-[7px] text-[#444] border border-[#d4d4d4] rounded-[4px] hover:bg-white hover:border-[#999] transition">
                    取消
                </button>
                <button @click="confirmOk"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[16px] py-[7px] rounded-[4px] hover:bg-[#991b1b] shadow-sm transition">
                    {{ confirmState.confirmText }}
                </button>
            </div>
        </div>
    </div>

  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #cbd5e1; }
</style>
