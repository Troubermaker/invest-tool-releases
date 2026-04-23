<script setup>
import { ref } from 'vue'
import { api } from '../api/client'

// ---------------- 导出（原生"另存为"对话框）----------------
const exporting = ref(false)
const exportMsg = ref('')

async function handleExport() {
    exporting.value = true
    exportMsg.value = ''
    try {
        const res = await api.exportUserDataInteractive()
        if (!res.ok) { exportMsg.value = '导出失败：' + (res.error || '未知错误'); return }
        if (res.data.cancelled) { exportMsg.value = ''; return }
        const c = res.data.counts || {}
        const parts = [
            `${c.watchlist_groups || 0} 个自选分组`,
            `${c.watchlist_stocks || 0} 只自选股`,
            `${c.portfolio_accounts || 0} 个持仓账户`,
            `${c.portfolio_positions || 0} 条持仓`,
        ].join(' / ')
        exportMsg.value = `已保存到 ${res.data.path}（${parts}）`
    } finally {
        exporting.value = false
    }
}

// ---------------- 导入（原生"打开"对话框 → 预览 → 确认）----------------
const importPreview = ref(null)  // { path, counts, exportedAt, schemaVersion }
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
    const ok = await askConfirm({
        title: '确认导入备份',
        message: '将清空当前设备所有自选和持仓数据，用备份文件替换。此操作不可恢复，建议先导出当前数据作为备份。',
        confirmText: '确认导入',
    })
    if (!ok) return
    importing.value = true
    importMsg.value = ''
    try {
        const res = await api.importBackupFile(importPreview.value.path, 'replace')
        if (!res.ok) { importMsg.value = '导入失败：' + (res.error || '未知错误'); return }
        const ic = res.data?.imported || {}
        importMsg.value = `导入成功！写入 ${ic.watchlist_stocks || 0} 只自选股 / ${ic.portfolio_positions || 0} 条持仓。切回自选/持仓 tab 即可看到。`
        importPreview.value = null
    } finally {
        importing.value = false
    }
}

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
                        <div class="text-[12px] text-[#888] mt-[2px]">生成 <code class="text-[11px] bg-[#f5f5f5] px-[5px] py-[1px] rounded">invest_data_backup_*.json</code>，含：自选分组/自选股、持仓账户/持仓、用户偏好。不含市场行情缓存。</div>
                    </div>
                    <button @click="handleExport" :disabled="exporting"
                            class="shrink-0 text-[12px] font-bold text-white bg-[#dc2626] px-[16px] py-[7px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                        {{ exporting ? '导出中...' : '导出为 JSON' }}
                    </button>
                </div>
                <div v-if="exportMsg" class="text-[12px] text-[#059669] mt-[10px] bg-[#f0fdf4] border border-[#dcfce7] px-[10px] py-[6px] rounded-[4px]">
                    ✓ {{ exportMsg }}
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
                    <div class="flex justify-end gap-[8px] mt-[14px]">
                        <button @click="cancelImport"
                                class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-white transition">
                            取消
                        </button>
                        <button @click="confirmImport" :disabled="importing"
                                class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                            {{ importing ? '导入中...' : '确认导入（覆盖现有数据）' }}
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
