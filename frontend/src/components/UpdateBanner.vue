<script setup>
/**
 * 在线更新组件。
 *
 * 状态机：
 *   idle        → 启动时静默检查 → 没新版就不显示任何东西
 *   available   → 顶部弹通知条 "🔔 v0.x.y 可用 [查看详情]"
 *   modal       → 用户点详情 → 弹模态框，显示发布说明 + "立即更新" / "稍后"
 *   downloading → 下载中 → 进度条 + 暂停/取消按钮
 *   ready       → 下载完成 → "重启应用以更新？" 模态
 *   applying    → 用户点重启 → 调 applyUpdate（不会返回，进程退出）
 *   error       → 任何环节失败 → 错误提示 + 重试按钮
 *
 * 启动检测：组件 mount 后 5s 静默检查（避免跟首屏其他网络请求挤）。
 * 用户主动："Settings → 检查更新" 可直接调 doCheck()，需要把组件 ref 暴露出去
 *           （后续可以做，目前先支持启动自动检查）。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'

const phase = ref('idle')
const updateInfo = ref(null)         // check_update 返回的 dict
const progress = ref({ downloaded_bytes: 0, total_bytes: 0 })
const errorMsg = ref('')
const showModal = ref(false)
const showReadyModal = ref(false)

let _pollTimer = null

// ---- 进度计算 ----
const pct = computed(() => {
    const t = progress.value.total_bytes || 0
    const d = progress.value.downloaded_bytes || 0
    if (!t) return 0
    return Math.min(100, Math.round((d / t) * 100))
})

function fmtMB(bytes) {
    if (!bytes) return '0 MB'
    return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

// ---- 检测 ----
async function doCheck() {
    try {
        const res = await api.checkUpdate()
        if (res.ok && res.data) {
            updateInfo.value = res.data
            if (res.data.has_update) {
                phase.value = 'available'
            }
        }
    } catch (e) {
        console.warn('[update] check failed:', e)
    }
}

// ---- 用户操作 ----
function viewDetails() {
    showModal.value = true
}

async function confirmDownload() {
    showModal.value = false
    phase.value = 'downloading'
    errorMsg.value = ''
    progress.value = {
        downloaded_bytes: 0,
        total_bytes: updateInfo.value.size_bytes || 0,
    }
    const res = await api.startUpdateDownload(
        updateInfo.value.download_url,
        updateInfo.value.sha256,
        updateInfo.value.size_bytes,
    )
    if (!res.ok || !res.data?.started) {
        phase.value = 'error'
        errorMsg.value = '下载启动失败：' + (res.error || '后端拒绝')
        return
    }
    startPolling()
}

function startPolling() {
    stopPolling()
    _pollTimer = setInterval(async () => {
        const res = await api.getUpdateProgress()
        if (!res.ok) return
        const p = res.data
        progress.value = {
            downloaded_bytes: p.downloaded_bytes || 0,
            total_bytes: p.total_bytes || progress.value.total_bytes,
        }
        if (p.phase === 'ready') {
            stopPolling()
            phase.value = 'ready'
            showReadyModal.value = true
        } else if (p.phase === 'error') {
            stopPolling()
            phase.value = 'error'
            errorMsg.value = p.error || '下载失败'
        }
    }, 500)
}

function stopPolling() {
    if (_pollTimer) {
        clearInterval(_pollTimer)
        _pollTimer = null
    }
}

async function cancelDownload() {
    stopPolling()
    await api.cancelUpdateDownload()
    phase.value = 'available'
    progress.value = { downloaded_bytes: 0, total_bytes: 0 }
}

async function applyNow() {
    showReadyModal.value = false
    phase.value = 'applying'
    // 调 applyUpdate 后端会在 200ms 后让进程退出；正常情况下这个调用永远不返回
    const res = await api.applyUpdate()
    if (res.ok && res.data?.ok) {
        // 等待进程退出 + updater 启动；UI 这里基本看不到
        return
    }
    // 走到这里说明 apply 失败
    phase.value = 'error'
    errorMsg.value = res.data?.error || res.error || '应用更新失败'
}

function retry() {
    phase.value = updateInfo.value?.has_update ? 'available' : 'idle'
    errorMsg.value = ''
}

function dismiss() {
    // "稍后再说"——隐藏通知条，本次启动不再打扰
    phase.value = 'idle'
    showModal.value = false
    showReadyModal.value = false
}

// 用户从 Settings 页面"立即检查"按钮触发的强制检查
async function externalTriggerCheck() {
    await doCheck()
    // 检查完如果有更新，强行展开模态（即使之前被用户 ✕ 关掉过）
    if (phase.value === 'available') {
        showModal.value = true
    } else {
        // 没更新，让 Settings 页知道
        window.dispatchEvent(new CustomEvent('invest-tool:update-check-result', {
            detail: { hasUpdate: false, info: updateInfo.value }
        }))
    }
}

onMounted(() => {
    // 5 秒后静默检查（让首屏其他请求先跑）
    setTimeout(doCheck, 5000)
    window.addEventListener('invest-tool:trigger-update-check', externalTriggerCheck)
})

onUnmounted(() => {
    stopPolling()
    window.removeEventListener('invest-tool:trigger-update-check', externalTriggerCheck)
})

defineExpose({ doCheck })
</script>

<template>
    <!-- 顶部通知条：非阻塞，可关闭 -->
    <div v-if="phase === 'available'"
         class="fixed top-0 left-0 right-0 z-[200] bg-gradient-to-r from-[#fff5f5] to-[#fef2f2] border-b border-[#fecaca] px-[16px] py-[8px] flex items-center justify-between gap-[12px] shadow-sm">
        <div class="flex items-center gap-[10px] text-[13px]">
            <span class="text-[16px]">🔔</span>
            <span class="text-[#dc2626] font-semibold">新版本可用</span>
            <span class="text-[#888]">v{{ updateInfo.current_version }} → v{{ updateInfo.latest_version }}</span>
            <span v-if="updateInfo.release_date" class="text-[11px] text-[#aaa]">（{{ updateInfo.release_date }} 发布）</span>
        </div>
        <div class="flex items-center gap-[8px]">
            <button @click="viewDetails"
                    class="text-[12px] font-semibold text-white bg-[#dc2626] hover:bg-[#b91c1c] px-[12px] py-[5px] rounded-[4px] transition">
                查看详情
            </button>
            <button v-if="!updateInfo.force_update"
                    @click="dismiss"
                    class="text-[11px] text-[#888] hover:text-[#dc2626] px-[6px] transition"
                    title="本次启动不再提示">
                ✕
            </button>
        </div>
    </div>

    <!-- 下载进度 浮动条 -->
    <div v-if="phase === 'downloading'"
         class="fixed top-0 left-0 right-0 z-[200] bg-white border-b border-[#e5e5e5] px-[16px] py-[8px] shadow-sm">
        <div class="flex items-center justify-between gap-[12px]">
            <div class="flex items-center gap-[10px] text-[13px]">
                <svg class="w-[14px] h-[14px] animate-spin text-[#dc2626]" viewBox="0 0 24 24" fill="none">
                    <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="60" stroke-dashoffset="20" stroke-linecap="round"/>
                </svg>
                <span class="font-semibold text-[#111]">下载中</span>
                <span class="text-[#888] tabular-nums">{{ fmtMB(progress.downloaded_bytes) }} / {{ fmtMB(progress.total_bytes) }} ({{ pct }}%)</span>
            </div>
            <button @click="cancelDownload"
                    class="text-[11px] text-[#888] hover:text-[#dc2626] px-[8px] transition">
                取消
            </button>
        </div>
        <div class="mt-[6px] h-[3px] bg-[#f0f0f0] rounded-full overflow-hidden">
            <div class="h-full bg-gradient-to-r from-[#dc2626] to-[#ef4444] transition-[width] duration-300"
                 :style="{ width: pct + '%' }"></div>
        </div>
    </div>

    <!-- 错误条 -->
    <div v-if="phase === 'error'"
         class="fixed top-0 left-0 right-0 z-[200] bg-[#fef2f2] border-b border-[#fecaca] px-[16px] py-[8px] flex items-center justify-between gap-[12px]">
        <div class="flex items-center gap-[10px] text-[13px]">
            <span class="text-[#dc2626] font-semibold">⚠ 更新失败</span>
            <span class="text-[#666] truncate max-w-[600px]" :title="errorMsg">{{ errorMsg }}</span>
        </div>
        <div class="flex items-center gap-[8px]">
            <button @click="retry"
                    class="text-[12px] font-semibold text-[#dc2626] border border-[#dc2626] hover:bg-[#fff5f5] px-[10px] py-[3px] rounded-[4px] transition">
                重试
            </button>
            <button @click="dismiss"
                    class="text-[11px] text-[#888] hover:text-[#dc2626] px-[6px] transition">✕</button>
        </div>
    </div>

    <!-- "查看详情"模态框 -->
    <Transition name="fade">
        <div v-if="showModal"
             @click.self="showModal = false"
             class="fixed inset-0 z-[300] bg-black/40 flex items-center justify-center px-[24px]">
            <div class="bg-white rounded-[10px] shadow-2xl max-w-[480px] w-full max-h-[80vh] overflow-hidden flex flex-col">
                <div class="px-[24px] pt-[20px] pb-[12px] border-b border-[#f0f0f0]">
                    <div class="flex items-baseline gap-[10px]">
                        <h3 class="text-[16px] font-bold text-[#111]">新版本 v{{ updateInfo.latest_version }}</h3>
                        <span class="text-[11px] text-[#888]">{{ updateInfo.release_date }}</span>
                    </div>
                    <p class="text-[12px] text-[#888] mt-[2px]">
                        当前版本 v{{ updateInfo.current_version }} · 大小 {{ fmtMB(updateInfo.size_bytes) }}
                    </p>
                </div>

                <!-- 发布说明 -->
                <div class="px-[24px] py-[16px] flex-1 overflow-auto custom-scrollbar">
                    <div class="text-[11px] text-[#999] uppercase tracking-wide mb-[8px]">本版变更</div>
                    <pre class="text-[13px] text-[#444] leading-relaxed whitespace-pre-wrap font-sans">{{ updateInfo.release_notes || '（无）' }}</pre>
                </div>

                <!-- 强更提示 -->
                <div v-if="updateInfo.force_update"
                     class="mx-[24px] mb-[12px] px-[12px] py-[8px] bg-[#fff5f5] border border-[#fecaca] rounded-[6px] text-[12px] text-[#dc2626]">
                    ⚠ 本次为兼容性升级，建议立即更新以确保功能正常使用。
                </div>

                <!-- 操作 -->
                <div class="px-[24px] py-[14px] bg-[#fafafa] border-t border-[#f0f0f0] flex items-center justify-end gap-[10px]">
                    <button v-if="!updateInfo.force_update"
                            @click="dismiss"
                            class="text-[13px] text-[#666] hover:text-[#111] px-[14px] py-[7px] transition">
                        稍后再说
                    </button>
                    <button @click="confirmDownload"
                            class="text-[13px] font-bold text-white bg-[#dc2626] hover:bg-[#b91c1c] px-[18px] py-[7px] rounded-[5px] transition shadow-sm">
                        立即下载更新
                    </button>
                </div>
            </div>
        </div>
    </Transition>

    <!-- "下载完成 → 重启"模态框 -->
    <Transition name="fade">
        <div v-if="showReadyModal"
             class="fixed inset-0 z-[300] bg-black/40 flex items-center justify-center px-[24px]">
            <div class="bg-white rounded-[10px] shadow-2xl max-w-[400px] w-full p-[24px]">
                <div class="flex items-baseline gap-[10px] mb-[10px]">
                    <span class="text-[20px]">✅</span>
                    <h3 class="text-[16px] font-bold text-[#111]">下载完成</h3>
                </div>
                <p class="text-[13px] text-[#444] leading-relaxed">
                    新版本 v{{ updateInfo.latest_version }} 已下载并校验完成。<br>
                    点击"立即重启"应用更新；你的自选 / 持仓 / 激活码 全部保留。
                </p>

                <div class="mt-[20px] flex items-center justify-end gap-[10px]">
                    <button @click="showReadyModal = false"
                            class="text-[13px] text-[#666] hover:text-[#111] px-[14px] py-[7px] transition">
                        下次启动应用
                    </button>
                    <button @click="applyNow"
                            class="text-[13px] font-bold text-white bg-[#dc2626] hover:bg-[#b91c1c] px-[18px] py-[7px] rounded-[5px] transition shadow-sm">
                        立即重启
                    </button>
                </div>
            </div>
        </div>
    </Transition>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 4px; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
