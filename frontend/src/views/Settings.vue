<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'
import { useExternalApp } from '../composables/useExternalApp'

// 联动外部行情软件（通达信 / 同花顺）
const ext = useExternalApp()
async function handleToggleExt(target, val) {
    await ext.setEnabled(target, val)
}

// ---------------- 关于 / 系统 ----------------
const appVersion = ref('')
const dataDir = ref('')
const checkingUpdate = ref(false)
const updateCheckMsg = ref('')      // 检查后的反馈
let _updateCheckMsgTimer = null

// 桌面通知开关（默认 false，仅应用内 toast）
const desktopNotifyEnabled = ref(false)
const DESKTOP_NOTIFY_PREF_KEY = 'alerts.desktop_notification'

// 关闭按钮行为：勾选 = 最小化到托盘，不勾选 = 直接退出
const minimizeToTrayEnabled = ref(true)
const MIN_TO_TRAY_PREF_KEY = 'app.minimize_to_tray_on_close'

async function loadAppInfo() {
    const res = await api.getAppVersion()
    if (res.ok && res.data) {
        appVersion.value = res.data.version
        dataDir.value = res.data.data_dir
    }
    // 顺便加载通知偏好
    const pref = await api.getUserPreference(DESKTOP_NOTIFY_PREF_KEY, false)
    if (pref.ok && typeof pref.data === 'boolean') {
        desktopNotifyEnabled.value = pref.data
    }
    // 加载关闭行为偏好（默认 true：最小化到托盘）
    const trayPref = await api.getUserPreference(MIN_TO_TRAY_PREF_KEY, true)
    if (trayPref.ok && typeof trayPref.data === 'boolean') {
        minimizeToTrayEnabled.value = trayPref.data
    }
}

async function handleToggleMinimizeToTray() {
    await api.setUserPreference(MIN_TO_TRAY_PREF_KEY, minimizeToTrayEnabled.value)
}

async function handleToggleDesktopNotify() {
    // v-model 已经更新了 desktopNotifyEnabled.value，这里只持久化
    await api.setUserPreference(DESKTOP_NOTIFY_PREF_KEY, desktopNotifyEnabled.value)
}

async function handleOpenDataDir() {
    await api.openDataDirectory()
}

// ---- 切换数据目录 ----
const changeDirBusy = ref(false)
const changeDirNotice = ref('')        // 操作结果展示（红/绿）
const showRestartModal = ref(false)
let _changeDirNoticeTimer = null

async function handleChangeDataDir() {
    changeDirBusy.value = true
    try {
        const r1 = await api.pickDataDirectory()
        if (!r1.ok || r1.data?.cancelled) return

        const newPath = r1.data.path
        const ok = await askConfirm({
            title: '切换数据目录',
            message: `确认要把数据目录改为：\n\n${newPath}\n\n` +
                     `当前数据将自动迁移过去（自选 / 持仓 / 激活码 / 偏好）。\n` +
                     `应用需要重启才能生效。`,
            confirmText: '确认切换',
        })
        if (!ok) return

        const r2 = await api.changeDataDirectory(newPath, true)
        if (!r2.ok) {
            showNotice('切换失败：' + (r2.error || ''), 'error')
            return
        }
        // 成功 → 提示重启
        dataDir.value = r2.data.new_path
        showRestartModal.value = true
    } finally {
        changeDirBusy.value = false
    }
}

async function handleResetDataDir() {
    const ok = await askConfirm({
        title: '恢复默认数据目录',
        message: '将把数据目录恢复为 %APPDATA%\\InvestTool\\。\n' +
                 '当前自定义目录里的数据不会被自动搬回——如需保留请先手动备份。\n' +
                 '应用需要重启。',
        confirmText: '确认恢复',
    })
    if (!ok) return
    const r = await api.resetDataDirectory()
    if (r.ok) {
        showRestartModal.value = true
    }
}

async function handleRestartNow() {
    showRestartModal.value = false
    await api.restartApp()
    // 进程会在 200ms 后退出。用户需手动双击 exe 重启。
}

function showNotice(msg, kind = 'info') {
    changeDirNotice.value = `[${kind}] ${msg}`
    if (_changeDirNoticeTimer) clearTimeout(_changeDirNoticeTimer)
    _changeDirNoticeTimer = setTimeout(() => { changeDirNotice.value = '' }, 6000)
}

async function handleCheckUpdate() {
    checkingUpdate.value = true
    updateCheckMsg.value = ''
    if (_updateCheckMsgTimer) clearTimeout(_updateCheckMsgTimer)

    // 让 UpdateBanner 自己拉一次（如果有更新会自动展开模态框）
    window.dispatchEvent(new CustomEvent('invest-tool:trigger-update-check'))

    // 同步显示 Settings 页内的反馈
    const res = await api.checkUpdate()
    checkingUpdate.value = false
    if (!res.ok || !res.data) {
        updateCheckMsg.value = '检查失败，请检查网络'
    } else if (!res.data.configured) {
        updateCheckMsg.value = '更新功能未配置（联系开发者）'
    } else if (res.data.error) {
        updateCheckMsg.value = '检查失败：' + res.data.error
    } else if (res.data.has_update) {
        updateCheckMsg.value = `🔔 v${res.data.latest_version} 可用（顶部已弹出详情）`
    } else {
        updateCheckMsg.value = `✅ 当前已是最新版本（v${res.data.current_version}）`
    }
    _updateCheckMsgTimer = setTimeout(() => { updateCheckMsg.value = '' }, 8000)
}

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

onMounted(() => { loadBossKey(); loadAppInfo() })
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
    <div class="px-[24px] py-[12px] border-b border-[#e5e5e5] bg-white shrink-0">
        <div class="text-[16px] font-bold text-[#111]">设置</div>
    </div>

    <div class="flex-1 px-[16px] py-[14px] space-y-[12px]">

        <!-- ============ 关于 / 系统 ============ -->
        <div class="bg-white border border-[#eeeeee] rounded-[6px] shadow-[0_1px_3px_rgba(0,0,0,0.02)]">
            <div class="px-[14px] py-[8px] border-b border-[#f0f0f0]">
                <div class="text-[13px] font-bold text-[#111]">关于 / 系统</div>
            </div>
            <!-- 一行铺开：版本 / 数据目录 / 操作按钮组 -->
            <div class="px-[14px] py-[10px] flex items-center gap-[14px] flex-wrap">

                <!-- 版本 + 检查更新 -->
                <div class="flex items-center gap-[8px] shrink-0">
                    <span class="text-[11px] text-[#666]">版本</span>
                    <span class="text-[13px] font-mono font-semibold text-[#111] tabular-nums">v{{ appVersion || '...' }}</span>
                    <button @click="handleCheckUpdate"
                            :disabled="checkingUpdate"
                            class="text-[11px] font-semibold text-[#dc2626] border border-[#fecaca] hover:bg-[#fff5f5] disabled:opacity-50 disabled:cursor-wait px-[8px] py-[2px] rounded-[3px] transition">
                        {{ checkingUpdate ? '检查中' : '检查更新' }}
                    </button>
                </div>

                <!-- 竖线分隔 -->
                <div class="w-px h-[18px] bg-[#e5e7eb] shrink-0"></div>

                <!-- 数据目录占满中间 -->
                <div class="flex items-center gap-[8px] flex-1 min-w-[280px]">
                    <span class="text-[11px] text-[#666] shrink-0">数据目录</span>
                    <code class="text-[11px] font-mono text-[#475569] bg-[#f8fafc] border border-[#e5e7eb] px-[6px] py-[1px] rounded-[3px] truncate flex-1 min-w-0"
                          :title="dataDir">{{ dataDir || '...' }}</code>
                </div>

                <!-- 数据目录 4 个按钮 -->
                <div class="flex items-center gap-[5px] shrink-0">
                    <button @click="handleOpenDataDir"
                            class="text-[11px] font-semibold text-[#2563eb] border border-[#bfdbfe] hover:bg-[#eff6ff] px-[8px] py-[2px] rounded-[3px] transition">
                        打开
                    </button>
                    <button @click="handleChangeDataDir"
                            :disabled="changeDirBusy"
                            class="text-[11px] font-semibold text-[#dc2626] border border-[#fecaca] hover:bg-[#fff5f5] disabled:opacity-50 px-[8px] py-[2px] rounded-[3px] transition">
                        {{ changeDirBusy ? '处理中' : '更改' }}
                    </button>
                    <a class="text-[11px] text-[#2563eb] cursor-pointer hover:underline px-[4px]"
                       @click="handleResetDataDir">恢复默认</a>
                </div>
            </div>

            <!-- 通知偏好行 -->
            <div class="px-[14px] pb-[10px] -mt-[4px] flex items-center gap-[14px] flex-wrap">
                <span class="text-[11px] text-[#666] shrink-0">价格警报</span>
                <label class="flex items-center gap-[6px] cursor-pointer select-none">
                    <input type="checkbox"
                           v-model="desktopNotifyEnabled"
                           @change="handleToggleDesktopNotify"
                           class="accent-[#dc2626] cursor-pointer">
                    <span class="text-[11px] text-[#333]">桌面通知（Windows Toast）</span>
                </label>
                <span class="text-[10px] text-[#aaa]">
                    应用内永远开启（右下角弹 toast）；桌面 Toast 默认关，避免抢焦点
                </span>
            </div>

            <!-- 关闭按钮行为 -->
            <div class="px-[14px] pb-[10px] -mt-[4px] flex items-center gap-[14px] flex-wrap">
                <span class="text-[11px] text-[#666] shrink-0">关闭按钮</span>
                <label class="flex items-center gap-[6px] cursor-pointer select-none">
                    <input type="checkbox"
                           v-model="minimizeToTrayEnabled"
                           @change="handleToggleMinimizeToTray"
                           class="accent-[#dc2626] cursor-pointer">
                    <span class="text-[11px] text-[#333]">点 X 最小化到托盘（不勾选则直接退出）</span>
                </label>
                <span class="text-[10px] text-[#aaa]">
                    托盘菜单的「退出」永远是真退出，不受此开关影响
                </span>
            </div>

            <!-- 联动外部行情软件 -->
            <div class="px-[14px] pb-[10px] -mt-[4px] flex items-center gap-[14px] flex-wrap">
                <span class="text-[11px] text-[#666] shrink-0">联动外部</span>

                <!-- 通达信 -->
                <label class="flex items-center gap-[6px] cursor-pointer select-none">
                    <input type="checkbox"
                           :checked="ext.enableTdx.value"
                           @change="handleToggleExt('tdx', $event.target.checked)"
                           class="accent-[#dc2626] cursor-pointer">
                    <span class="text-[11px] text-[#333]">📡 通达信</span>
                    <span v-if="ext.enableTdx.value" class="text-[10px]"
                          :class="ext.status.value.tdx?.running ? 'text-[#16a34a]' : 'text-[#dc2626]'">
                        {{ ext.status.value.tdx?.running ? '✓ 已检测到' : '⚠ 未运行' }}
                    </span>
                </label>

                <!-- 同花顺 -->
                <label class="flex items-center gap-[6px] cursor-pointer select-none">
                    <input type="checkbox"
                           :checked="ext.enableThs.value"
                           @change="handleToggleExt('ths', $event.target.checked)"
                           class="accent-[#dc2626] cursor-pointer">
                    <span class="text-[11px] text-[#333]">📡 同花顺</span>
                    <span v-if="ext.enableThs.value" class="text-[10px]"
                          :class="ext.status.value.ths?.running ? 'text-[#16a34a]' : 'text-[#dc2626]'">
                        {{ ext.status.value.ths?.running ? '✓ 已检测到' : '⚠ 未运行' }}
                    </span>
                </label>

                <button @click="ext.refreshStatus()"
                        class="text-[10px] text-[#2563eb] hover:underline cursor-pointer">
                    重新检测
                </button>

                <span class="text-[10px] text-[#aaa]">
                    勾选后，自选/持仓行 hover 会出「📡」按钮，点击可在外部软件跳转该股
                </span>
            </div>

            <!-- 反馈消息：检查更新结果（如有）-->
            <div v-if="updateCheckMsg"
                 class="px-[14px] pb-[8px] -mt-[4px] text-[11px]"
                 :class="updateCheckMsg.includes('失败') || updateCheckMsg.includes('未配置') ? 'text-[#dc2626]' : 'text-[#059669]'">
                {{ updateCheckMsg }}
            </div>
        </div>

        <!-- ============ 老板键（一行铺开）============ -->
        <div class="bg-white border border-[#eeeeee] rounded-[6px] shadow-[0_1px_3px_rgba(0,0,0,0.02)]">
            <div class="px-[14px] py-[8px] border-b border-[#f0f0f0]">
                <div class="text-[13px] font-bold text-[#111]">老板键</div>
            </div>
            <div class="px-[14px] py-[10px]">
                <!-- 查看态：当前快捷键 + 修改按钮 + 说明 inline -->
                <div v-if="!recording" class="flex items-center gap-[12px] flex-wrap">
                    <span class="text-[11px] text-[#666]">当前</span>
                    <span class="text-[15px] font-bold text-[#111] tabular-nums">{{ formatDisplay(bossKey) }}</span>
                    <button @click="startRecording"
                            class="text-[11px] font-semibold text-[#444] bg-white border border-[#d4d4d4] px-[10px] py-[2px] rounded-[3px] hover:bg-[#f5f5f5] hover:border-[#999] transition">
                        修改
                    </button>
                    <span class="text-[10px] text-[#999] flex-1 min-w-[200px]">
                        一键隐藏 / 恢复窗口（Alt+Tab 也消失）；2 键组合（修饰键 + 普通键）。推荐 Ctrl+`、Alt+Z 避开浏览器常用键。
                    </span>
                </div>

                <!-- 录制态 -->
                <div v-else class="bg-[#fafafa] border border-dashed border-[#fbbf24] rounded-[4px] px-[10px] py-[8px] flex items-center gap-[10px] flex-wrap">
                    <span class="text-[11px] text-[#92400e] shrink-0">请按下新的快捷键组合 ⌨</span>
                    <div class="text-[14px] font-bold text-[#111] tabular-nums tracking-wider px-[10px] py-[3px] bg-white rounded-[3px] border border-[#eeeeee] min-w-[120px] text-center">
                        {{ capturedCombo || '等待按键...' }}
                    </div>
                    <div class="text-[10px] flex-1 min-w-[120px]"
                         :class="isValidCombo ? 'text-[#059669]' : 'text-[#888]'">
                        {{ validationHint }}
                    </div>
                    <div class="flex gap-[6px] shrink-0">
                        <button @click="cancelRecording"
                                class="text-[11px] px-[10px] py-[3px] text-[#666] border border-[#e5e5e5] rounded-[3px] hover:bg-white">
                            取消
                        </button>
                        <button @click="saveBossKey" :disabled="!isValidCombo"
                                class="text-[11px] font-bold text-white bg-[#dc2626] px-[10px] py-[3px] rounded-[3px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                            保存
                        </button>
                    </div>
                </div>

                <div v-if="bossKeyMsg && !recording"
                     class="text-[11px] mt-[6px] px-[8px] py-[3px] rounded-[3px]"
                     :class="bossKeyMsg.includes('失败')
                         ? 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'
                         : 'text-[#059669] bg-[#f0fdf4] border border-[#dcfce7]'">
                    {{ bossKeyMsg }}
                </div>
            </div>
        </div>

        <!-- ============ 数据备份（占满下方）============ -->
        <div class="bg-white border border-[#eeeeee] rounded-[6px] shadow-[0_1px_3px_rgba(0,0,0,0.02)]">
            <div class="px-[14px] py-[8px] border-b border-[#f0f0f0]">
                <div class="flex items-baseline gap-[10px]">
                    <div class="text-[13px] font-bold text-[#111]">数据备份</div>
                    <div class="text-[11px] text-[#999]">导出 JSON → 在另一台电脑导入；可云盘 / 私有 git 版本化</div>
                </div>
            </div>

            <!-- 导出块 -->
            <div class="px-[14px] py-[10px] border-b border-[#f5f5f5]">
                <div class="flex items-center gap-[12px] flex-wrap">
                    <div class="text-[12px] font-semibold text-[#111] shrink-0">导出</div>
                    <!-- 分区勾选 inline -->
                    <label class="flex items-center gap-[4px] cursor-pointer select-none text-[11px]">
                        <input type="checkbox" v-model="exportSections.watchlist" class="accent-[#dc2626]">
                        <span class="text-[#333]">自选</span>
                    </label>
                    <label class="flex items-center gap-[4px] cursor-pointer select-none text-[11px]">
                        <input type="checkbox" v-model="exportSections.portfolio" class="accent-[#dc2626]">
                        <span class="text-[#333]">持仓</span>
                    </label>
                    <label class="flex items-center gap-[4px] cursor-pointer select-none text-[11px]">
                        <input type="checkbox" v-model="exportSections.preferences" class="accent-[#dc2626]">
                        <span class="text-[#666]">偏好</span>
                    </label>
                    <span class="text-[10px] text-[#999]">分享自选时记得去掉"持仓"</span>
                    <button @click="handleExport" :disabled="exporting"
                            class="ml-auto shrink-0 text-[11px] font-bold text-white bg-[#dc2626] px-[12px] py-[5px] rounded-[3px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                        {{ exporting ? '导出中...' : '导出为 JSON' }}
                    </button>
                </div>
                <div v-if="exportMsg"
                     class="text-[11px] mt-[8px] px-[8px] py-[4px] rounded-[3px]"
                     :class="exportMsg.startsWith('请') || exportMsg.startsWith('导出失败')
                         ? 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'
                         : 'text-[#059669] bg-[#f0fdf4] border border-[#dcfce7]'">
                    {{ exportMsg }}
                </div>
            </div>

            <!-- 导入块 -->
            <div class="px-[14px] py-[10px]">
                <div class="flex items-center gap-[12px] flex-wrap">
                    <div class="text-[12px] font-semibold text-[#111] shrink-0">导入</div>
                    <span class="text-[11px] text-[#888]">选择之前导出的 JSON；预览后可选合并 / 覆盖</span>
                    <button @click="triggerFilePicker"
                            class="ml-auto shrink-0 text-[11px] font-bold text-[#444] bg-white border border-[#d4d4d4] px-[12px] py-[5px] rounded-[3px] hover:bg-[#f5f5f5] hover:border-[#999] transition">
                        选择备份文件...
                    </button>
                </div>

                <!-- 导入预览 -->
                <div v-if="importPreview" class="mt-[10px] bg-[#fafafa] border border-[#eeeeee] rounded-[4px] p-[10px]">
                    <div class="flex items-center justify-between text-[11px]">
                        <div class="font-semibold text-[#111]">备份文件预览</div>
                        <div class="text-[#999]">{{ importPreview.exportedAt || '—' }} · schema v{{ importPreview.schemaVersion }}</div>
                    </div>
                    <div class="grid grid-cols-5 gap-[6px] mt-[8px]">
                        <div v-for="(v, k) in importPreview.counts" :key="k"
                             class="bg-white border border-[#eeeeee] rounded-[3px] px-[8px] py-[5px]">
                            <div class="text-[10px] text-[#999]">{{ k }}</div>
                            <div class="text-[14px] font-bold text-[#111] tabular-nums mt-[1px]">{{ v }}</div>
                        </div>
                    </div>

                    <!-- 模式选择（紧凑横向）-->
                    <div class="mt-[10px] border-t border-[#eeeeee] pt-[8px]">
                        <div class="text-[11px] font-semibold text-[#111] mb-[5px]">导入方式</div>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-[6px]">
                            <label class="flex items-start gap-[6px] px-[8px] py-[6px] rounded-[3px] cursor-pointer transition"
                                   :class="importMode === 'merge' ? 'bg-white border border-[#dc2626]' : 'border border-transparent hover:bg-white/60'">
                                <input type="radio" value="merge" v-model="importMode" class="mt-[2px] accent-[#dc2626]">
                                <div class="flex-1">
                                    <div class="text-[11px] font-semibold text-[#111]">合并（增量）<span class="ml-[4px] text-[9px] font-normal text-[#dc2626]">推荐</span></div>
                                    <div class="text-[10px] text-[#888] leading-relaxed">同键覆盖、仅本地有的保留。适合两机都编辑过同步</div>
                                </div>
                            </label>
                            <label class="flex items-start gap-[6px] px-[8px] py-[6px] rounded-[3px] cursor-pointer transition"
                                   :class="importMode === 'replace' ? 'bg-white border border-[#dc2626]' : 'border border-transparent hover:bg-white/60'">
                                <input type="radio" value="replace" v-model="importMode" class="mt-[2px] accent-[#dc2626]">
                                <div class="flex-1">
                                    <div class="text-[11px] font-semibold text-[#111]">替换（覆盖）</div>
                                    <div class="text-[10px] text-[#888] leading-relaxed">清空再写入，本地独有会丢失。适合首次迁移 / 备份权威</div>
                                </div>
                            </label>
                        </div>
                    </div>

                    <div class="flex justify-end gap-[6px] mt-[10px]">
                        <button @click="cancelImport"
                                class="text-[11px] px-[12px] py-[5px] text-[#666] border border-[#e5e5e5] rounded-[3px] hover:bg-white transition">
                            取消
                        </button>
                        <button @click="confirmImport" :disabled="importing"
                                class="text-[11px] font-bold text-white bg-[#dc2626] px-[12px] py-[5px] rounded-[3px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                            {{ importing ? '导入中...' : (importMode === 'merge' ? '确认合并导入' : '确认覆盖导入') }}
                        </button>
                    </div>
                </div>

                <div v-if="importMsg"
                     class="text-[11px] mt-[8px] px-[8px] py-[4px] rounded-[3px]"
                     :class="importMsg.includes('成功') ? 'text-[#059669] bg-[#f0fdf4] border border-[#dcfce7]' : 'text-[#dc2626] bg-[#fef2f2] border border-[#fecaca]'">
                    {{ importMsg }}
                </div>
            </div>
        </div>

    </div>

    <!-- ============ 重启提示弹窗 ============ -->
    <div v-if="showRestartModal"
         class="fixed inset-0 bg-black/30 z-[80] flex items-center justify-center"
         @click.self="showRestartModal = false">
        <div class="bg-white rounded-[10px] w-[380px] shadow-[0_10px_40px_rgba(0,0,0,0.20)] overflow-hidden">
            <div class="px-[24px] pt-[20px] pb-[14px] flex items-baseline gap-[10px]">
                <span class="text-[18px]">🔄</span>
                <div class="text-[15px] font-bold text-[#111]">数据目录已切换</div>
            </div>
            <div class="px-[24px] pb-[14px] text-[13px] text-[#555] leading-relaxed">
                配置已保存到 <code class="text-[12px] bg-[#f5f5f5] px-[4px] py-[1px] rounded">config.json</code>，
                <strong>需要重启应用才能生效</strong>。<br>
                数据已自动迁移到新位置（如选择了"迁移"）。
            </div>
            <div class="flex justify-end gap-[8px] px-[20px] py-[14px] bg-[#fafafa] border-t border-[#f0f0f0]">
                <button @click="showRestartModal = false"
                        class="text-[12px] px-[16px] py-[7px] text-[#444] border border-[#d4d4d4] rounded-[4px] hover:bg-white transition">
                    稍后手动重启
                </button>
                <button @click="handleRestartNow"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[16px] py-[7px] rounded-[4px] hover:bg-[#991b1b] shadow-sm transition">
                    立即关闭（请手动启动）
                </button>
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
