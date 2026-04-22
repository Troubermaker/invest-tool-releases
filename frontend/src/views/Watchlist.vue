<script setup>
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import draggable from 'vuedraggable'
import { api } from '../api/client'

// ---------------- 数据状态 ----------------
const groups = ref([])
const selectedGroupId = ref(null)
const stocks = ref([])
const loading = ref(false)

// 新建分组（tab 栏）
const showNewGroupInput = ref(false)
const newGroupName = ref('')

// 双击 tab inline 重命名
const renamingGroupId = ref(null)
const renamingGroupName = ref('')
const renameInputRef = ref(null)

// 添加股票
const showAddStockInput = ref(false)
const newStock = ref({ code: '', name: '', price: '' })

// 搜索
const searchQuery = ref('')

// 编辑股票弹窗
const editingStock = ref(null)

// 管理分组 modal
const showManageModal = ref(false)
const managingGroups = ref([])               // draggable 的本地副本
const managingEditingId = ref(null)          // modal 里正在改名的 group id
const managingEditingName = ref('')
const managingNewGroupName = ref('')
const managingEditInputRef = ref(null)

// ---------------- 计算 ----------------
const selectedGroup = computed(() =>
    groups.value.find(g => g.id === selectedGroupId.value) || null
)

const filteredStocks = computed(() => {
    const q = searchQuery.value.trim().toLowerCase()
    if (!q) return stocks.value
    return stocks.value.filter(s =>
        (s.name && s.name.toLowerCase().includes(q)) || (s.code && s.code.includes(q))
    )
})

// ---------------- 工具函数 ----------------
function marketPrefix(code) {
    if (!code) return ''
    if (code.startsWith('6')) return 'SH'
    if (code.startsWith('300') || code.startsWith('301')) return 'SZ'
    if (/^00[0-3]/.test(code)) return 'SZ'
    if (/^(4|8|9|920)/.test(code)) return 'BJ'
    return ''
}

function daysSinceAdded(addedAt) {
    if (!addedAt) return null
    const iso = typeof addedAt === 'string' ? addedAt.replace(' ', 'T') : addedAt
    const added = new Date(iso)
    if (isNaN(added.getTime())) return null
    const days = Math.floor((Date.now() - added.getTime()) / 86400000)
    return Math.max(days, 0)
}

function formatAddedDate(addedAt) {
    if (!addedAt) return '—'
    return addedAt.slice(5, 10).replace('-', '/')
}

// ---------------- 分组加载 ----------------
async function loadGroups() {
    const res = await api.getWatchlistGroups()
    if (res.ok) {
        groups.value = res.data || []
        if (selectedGroupId.value != null &&
            !groups.value.find(g => g.id === selectedGroupId.value)) {
            selectedGroupId.value = groups.value[0]?.id ?? null
        }
        if (selectedGroupId.value == null && groups.value.length > 0) {
            selectedGroupId.value = groups.value[0].id
        }
    }
}

async function selectGroup(groupId) {
    if (selectedGroupId.value === groupId) return
    selectedGroupId.value = groupId
    searchQuery.value = ''
    await loadStocks()
}

async function loadStocks() {
    if (selectedGroupId.value == null) { stocks.value = []; return }
    loading.value = true
    try {
        const res = await api.getWatchlistStocks(selectedGroupId.value)
        if (res.ok) stocks.value = res.data || []
    } finally {
        loading.value = false
    }
}

// ---------------- Tab 栏：新建 + 快速重命名 ----------------
async function handleCreateGroup() {
    const name = newGroupName.value.trim()
    if (!name) return
    const res = await api.createWatchlistGroup(name)
    if (res.ok) {
        newGroupName.value = ''
        showNewGroupInput.value = false
        await loadGroups()
        if (res.data?.id) await selectGroup(res.data.id)
    }
}

async function startRenameGroup(group) {
    renamingGroupId.value = group.id
    renamingGroupName.value = group.name
    await nextTick()
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
}

async function saveRenameGroup() {
    const newName = renamingGroupName.value.trim()
    const gid = renamingGroupId.value
    if (!gid) return
    const orig = groups.value.find(g => g.id === gid)
    if (!newName || newName === orig?.name) {
        cancelRenameGroup()
        return
    }
    const res = await api.renameWatchlistGroup(gid, newName)
    if (res.ok) await loadGroups()
    cancelRenameGroup()
}

function cancelRenameGroup() {
    renamingGroupId.value = null
    renamingGroupName.value = ''
}

// ---------------- 管理分组 Modal ----------------
async function openManageModal() {
    // 深拷贝 groups 给 modal 用，避免拖拽直接改主 state
    managingGroups.value = JSON.parse(JSON.stringify(groups.value))
    showManageModal.value = true
}

function closeManageModal() {
    showManageModal.value = false
    managingEditingId.value = null
    managingEditingName.value = ''
    managingNewGroupName.value = ''
}

// 拖拽结束后保存新顺序到后端
async function onManageDragEnd() {
    const orderedIds = managingGroups.value.map(g => g.id)
    const res = await api.reorderWatchlistGroups(orderedIds)
    if (res.ok) await loadGroups()
}

// Modal 内重命名
async function startManageEdit(group) {
    managingEditingId.value = group.id
    managingEditingName.value = group.name
    await nextTick()
    managingEditInputRef.value?.focus()
    managingEditInputRef.value?.select()
}

async function saveManageEdit() {
    const newName = managingEditingName.value.trim()
    const gid = managingEditingId.value
    if (!gid) return
    const orig = managingGroups.value.find(g => g.id === gid)
    if (!newName || newName === orig?.name) {
        cancelManageEdit()
        return
    }
    const res = await api.renameWatchlistGroup(gid, newName)
    if (res.ok) {
        // 同步更新本地 state 和主 groups
        if (orig) orig.name = newName
        await loadGroups()
    }
    cancelManageEdit()
}

function cancelManageEdit() {
    managingEditingId.value = null
    managingEditingName.value = ''
}

// Modal 内删除
async function manageDeleteGroup(group) {
    if (!confirm(`确定删除分组「${group.name}」？组内的所有股票会一并移除。`)) return
    const res = await api.deleteWatchlistGroup(group.id)
    if (res.ok) {
        managingGroups.value = managingGroups.value.filter(g => g.id !== group.id)
        await loadGroups()
        await loadStocks()
    }
}

// Modal 内新建
async function manageCreateGroup() {
    const name = managingNewGroupName.value.trim()
    if (!name) return
    const res = await api.createWatchlistGroup(name)
    if (res.ok) {
        managingNewGroupName.value = ''
        await loadGroups()
        // 同步到 modal 本地副本
        managingGroups.value = JSON.parse(JSON.stringify(groups.value))
    }
}

// ---------------- 股票操作 ----------------
async function handleAddStock() {
    const code = newStock.value.code.trim()
    if (!code || selectedGroupId.value == null) return
    const price = parseFloat(newStock.value.price)
    const res = await api.addWatchlistStock(
        selectedGroupId.value,
        code,
        newStock.value.name.trim(),
        isNaN(price) ? null : price,
        ''
    )
    if (res.ok) {
        newStock.value = { code: '', name: '', price: '' }
        showAddStockInput.value = false
        await Promise.all([loadGroups(), loadStocks()])
    }
}

async function handleRemoveStock(stock) {
    if (!confirm(`从当前分组移除 ${stock.name || stock.code}?`)) return
    const res = await api.removeWatchlistStock(selectedGroupId.value, stock.code)
    if (res.ok) await Promise.all([loadGroups(), loadStocks()])
}

function startEdit(stock) {
    editingStock.value = {
        code: stock.code,
        name: stock.name || '',
        added_price: stock.added_price ?? '',
        remark: stock.remark || '',
    }
}

async function saveEdit() {
    const e = editingStock.value
    if (!e) return
    const price = parseFloat(e.added_price)
    const res = await api.updateWatchlistStock(selectedGroupId.value, e.code, {
        name: e.name,
        addedPrice: isNaN(price) ? null : price,
        remark: e.remark,
    })
    if (res.ok) {
        editingStock.value = null
        await loadStocks()
    }
}

function cancelEdit() {
    editingStock.value = null
}

onMounted(async () => {
    await loadGroups()
    await loadStocks()
})
</script>

<template>
  <div class="flex flex-col h-full bg-[#fcfcfc] overflow-hidden">

    <!-- ============ Tab 栏 ============ -->
    <div class="h-[44px] bg-[#fafafa] border-b border-[#e5e5e5] flex items-center px-[12px] gap-[2px] shrink-0 overflow-x-auto custom-scrollbar">
        <div v-for="g in groups" :key="g.id"
             class="flex items-center gap-[6px] px-[14px] py-[8px] text-[13px] cursor-pointer transition-colors border-b-2 shrink-0"
             :class="selectedGroupId === g.id
                ? 'border-[#dc2626] text-[#dc2626] font-bold bg-white'
                : 'border-transparent text-[#666] hover:text-[#111] hover:bg-white/60'"
             @click="selectGroup(g.id)"
             @dblclick="startRenameGroup(g)"
             title="双击重命名">
            <span v-if="renamingGroupId !== g.id">{{ g.name }}</span>
            <input v-else ref="renameInputRef"
                   v-model="renamingGroupName"
                   @keyup.enter="saveRenameGroup"
                   @keyup.esc="cancelRenameGroup"
                   @blur="saveRenameGroup"
                   @click.stop
                   class="text-[13px] font-bold text-[#dc2626] bg-white border border-[#dc2626] rounded-[3px] px-[6px] py-0 w-[90px] outline-none">

            <span v-if="renamingGroupId !== g.id"
                  class="text-[10px] font-bold tabular-nums px-[5px] py-[1px] rounded-full"
                  :class="selectedGroupId === g.id ? 'bg-[#dc2626] text-white' : 'bg-[#e5e7eb] text-[#666]'">
                {{ g.count }}
            </span>
        </div>

        <!-- + 新建 -->
        <div v-if="!showNewGroupInput"
             @click="showNewGroupInput = true"
             class="px-[10px] py-[8px] text-[13px] text-[#999] hover:text-[#dc2626] cursor-pointer transition whitespace-nowrap">
            + 新建
        </div>
        <div v-else class="flex items-center gap-[4px] px-[8px]">
            <input v-model="newGroupName"
                   @keyup.enter="handleCreateGroup"
                   @keyup.esc="showNewGroupInput = false; newGroupName = ''"
                   placeholder="分组名"
                   class="text-[12px] w-[100px] px-[8px] py-[4px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]"
                   autofocus>
            <button @click="handleCreateGroup"
                    class="text-[11px] font-bold text-white bg-[#dc2626] px-[8px] py-[4px] rounded-[4px] hover:bg-[#991b1b]">确定</button>
            <button @click="showNewGroupInput = false; newGroupName = ''"
                    class="text-[11px] text-[#999] hover:text-[#111] px-[6px]">×</button>
        </div>

        <!-- 编辑分组 -->
        <div v-if="groups.length" class="ml-auto shrink-0">
            <button @click="openManageModal"
                    class="flex items-center gap-[4px] px-[10px] py-[6px] text-[12px] text-[#666] hover:text-[#dc2626] hover:bg-white rounded-[4px] transition"
                    title="编辑分组：排序、重命名、删除">
                <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                </svg>
                <span>编辑分组</span>
            </button>
        </div>
    </div>

    <!-- ============ 空态：还没有分组 ============ -->
    <div v-if="!groups.length" class="flex-1 flex flex-col items-center justify-center text-[#ccc] text-[14px] gap-[12px]">
        <svg class="w-[48px] h-[48px] text-[#e5e7eb]" viewBox="0 0 20 20" fill="currentColor">
            <path d="M11.48 3.499a.562.562 0 011.04 0l2.125 5.111a.563.563 0 00.475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 00-.182.557l1.285 5.385a.562.562 0 01-.84.61l-4.725-2.885a.562.562 0 00-.586 0l-4.725 2.885a.562.562 0 01-.84-.61l1.285-5.386a.562.562 0 00-.182-.557l-4.204-3.602a.562.562 0 01.321-.988l5.518-.442a.563.563 0 00.475-.345L11.48 3.5z"/>
        </svg>
        <div>还没有自选分组</div>
        <div class="text-[12px]">点击顶部 <span class="text-[#dc2626] font-semibold">+ 新建</span> 创建第一个分组</div>
    </div>

    <!-- ============ 工具栏 + 表格 ============ -->
    <template v-else>
        <div class="h-[44px] flex items-center justify-end px-[14px] border-b border-[#f0f0f0] bg-white shrink-0 gap-[10px]">
            <div class="flex items-center gap-[8px] shrink-0">
                <div class="relative">
                    <input v-model="searchQuery"
                           type="text"
                           placeholder="搜索代码/名称"
                           class="text-[12px] w-[160px] pl-[10px] pr-[24px] py-[4px] bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] focus:bg-white transition">
                    <button v-if="searchQuery" @click="searchQuery = ''"
                            class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-[#aaa] hover:text-[#666] flex items-center justify-center">
                        <svg class="w-[9px] h-[9px]" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                        </svg>
                    </button>
                </div>
                <div v-if="!showAddStockInput"
                     @click="showAddStockInput = true"
                     class="text-[12px] px-[10px] py-[4px] text-[#dc2626] border border-[#dc2626]/50 rounded-[4px] hover:bg-[#fff0f0] cursor-pointer transition whitespace-nowrap">
                    + 添加股票
                </div>
                <div v-else class="flex items-center gap-[4px]">
                    <input v-model="newStock.code" placeholder="代码"
                           class="text-[12px] w-[90px] px-[8px] py-[4px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] font-mono"
                           @keyup.enter="handleAddStock" autofocus>
                    <input v-model="newStock.name" placeholder="名称"
                           class="text-[12px] w-[90px] px-[8px] py-[4px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]"
                           @keyup.enter="handleAddStock">
                    <input v-model="newStock.price" placeholder="自选价"
                           class="text-[12px] w-[70px] px-[8px] py-[4px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums"
                           @keyup.enter="handleAddStock">
                    <button @click="handleAddStock"
                            class="text-[11px] font-bold text-white bg-[#dc2626] px-[8px] py-[4px] rounded-[4px] hover:bg-[#991b1b]">确定</button>
                    <button @click="showAddStockInput = false; newStock = { code:'', name:'', price:'' }"
                            class="text-[11px] text-[#999] hover:text-[#111] px-[6px]">×</button>
                </div>
            </div>
        </div>

        <!-- 股票表格 -->
        <div class="flex-1 overflow-auto custom-scrollbar bg-white">
            <table class="w-full text-left border-collapse whitespace-nowrap">
                <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] text-[12px] text-[#888] z-10">
                    <tr>
                        <th class="px-[14px] py-[10px] font-normal w-[160px]">股票名称</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[80px]">最新价</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[70px]">涨幅</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[70px]">换手率</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[90px]">成交量</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[80px]">市值</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[60px]">量比</th>
                        <th class="px-[8px] py-[10px] font-normal w-[100px]">所属板块</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[80px]">自选价</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[90px]">自选收益</th>
                        <th class="px-[8px] py-[10px] font-normal text-center w-[80px]">自选时间</th>
                        <th class="px-[8px] py-[10px] font-normal text-right w-[70px]">自选天数</th>
                        <th class="px-[8px] py-[10px] font-normal text-center w-[80px]">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-if="loading && !stocks.length">
                        <td colspan="13" class="py-[60px] text-center text-[#aaa] text-[13px]">加载中...</td>
                    </tr>
                    <tr v-else-if="!stocks.length">
                        <td colspan="13" class="py-[80px] text-center text-[#aaa] text-[13px]">
                            分组「{{ selectedGroup?.name }}」还没有股票，点击右上角
                            <span class="text-[#dc2626] font-semibold">+ 添加股票</span>
                        </td>
                    </tr>
                    <tr v-else-if="!filteredStocks.length">
                        <td colspan="13" class="py-[60px] text-center text-[#aaa] text-[13px]">
                            未匹配到"{{ searchQuery }}"，
                            <button @click="searchQuery = ''" class="text-[#dc2626] hover:underline">清空搜索</button>
                        </td>
                    </tr>

                    <tr v-for="stock in filteredStocks" :key="stock.code"
                        class="border-b border-[#f5f5f5] hover:bg-[#fffafa] transition-colors group">

                        <td class="px-[14px] py-[8px] align-middle">
                            <div class="text-[14px] font-bold text-[#111] leading-tight">{{ stock.name || '—' }}</div>
                            <div class="text-[11px] text-[#999] font-mono leading-tight mt-[2px] tabular-nums">
                                {{ marketPrefix(stock.code) }}{{ stock.code }}
                            </div>
                        </td>

                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>
                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>
                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>
                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>
                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>
                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>
                        <td class="px-[8px] py-[8px] text-[13px] text-[#aaa]">—</td>

                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#555] tabular-nums">
                            {{ stock.added_price != null ? stock.added_price.toFixed(2) : '—' }}
                        </td>

                        <td class="px-[8px] py-[8px] text-right text-[13px] text-[#aaa] tabular-nums">—</td>

                        <td class="px-[8px] py-[8px] text-center text-[12px] text-[#666] tabular-nums">
                            {{ formatAddedDate(stock.added_at) }}
                        </td>

                        <td class="px-[8px] py-[8px] text-right text-[12px] text-[#666] tabular-nums">
                            <template v-if="daysSinceAdded(stock.added_at) != null">
                                <span :class="daysSinceAdded(stock.added_at) === 0 ? 'text-[#dc2626] font-semibold' : ''">
                                    {{ daysSinceAdded(stock.added_at) === 0 ? '今日' : daysSinceAdded(stock.added_at) + '天' }}
                                </span>
                            </template>
                            <template v-else>—</template>
                        </td>

                        <td class="px-[8px] py-[8px] text-center">
                            <div class="flex items-center justify-center gap-[8px] opacity-0 group-hover:opacity-100 transition">
                                <button @click="startEdit(stock)" class="text-[#666] hover:text-[#2563eb] transition" title="编辑">
                                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                                    </svg>
                                </button>
                                <button @click="handleRemoveStock(stock)" class="text-[#666] hover:text-[#dc2626] transition" title="移除">
                                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/>
                                    </svg>
                                </button>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </template>

    <!-- ============ 编辑股票弹窗 ============ -->
    <div v-if="editingStock" class="fixed inset-0 bg-black/20 z-50 flex items-center justify-center"
         @click.self="cancelEdit">
        <div class="bg-white rounded-[6px] p-[20px] w-[360px] shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <div class="text-[14px] font-bold text-[#111] mb-[16px]">编辑股票 {{ editingStock.code }}</div>
            <div class="flex flex-col gap-[12px]">
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">名称</span>
                    <input v-model="editingStock.name"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                </label>
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">自选价（记录加入时成本价，用于算收益）</span>
                    <input v-model="editingStock.added_price" type="number" step="0.01" placeholder="如 1680.00"
                           class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] tabular-nums">
                </label>
                <label class="flex flex-col gap-[4px]">
                    <span class="text-[12px] text-[#666]">备注（可选）</span>
                    <textarea v-model="editingStock.remark" rows="2" placeholder="自选逻辑、仓位计划..."
                              class="text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626] resize-none"></textarea>
                </label>
            </div>
            <div class="flex justify-end gap-[8px] mt-[16px]">
                <button @click="cancelEdit"
                        class="text-[12px] px-[14px] py-[6px] text-[#666] border border-[#e5e5e5] rounded-[4px] hover:bg-[#f5f5f5]">取消</button>
                <button @click="saveEdit"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b]">保存</button>
            </div>
        </div>
    </div>

    <!-- ============ 管理分组 Modal ============ -->
    <div v-if="showManageModal" class="fixed inset-0 bg-black/25 z-50 flex items-center justify-center"
         @click.self="closeManageModal">
        <div class="bg-white rounded-[8px] w-[420px] max-h-[80vh] flex flex-col shadow-[0_10px_40px_rgba(0,0,0,0.15)]">
            <!-- Modal 头 -->
            <div class="px-[20px] py-[14px] border-b border-[#f0f0f0] flex items-center justify-between shrink-0">
                <div>
                    <div class="text-[14px] font-bold text-[#111]">编辑分组</div>
                    <div class="text-[11px] text-[#999] mt-[2px]">拖拽 ⇅ 调整顺序，点击铅笔重命名</div>
                </div>
                <button @click="closeManageModal"
                        class="text-[#999] hover:text-[#111] w-[24px] h-[24px] flex items-center justify-center rounded hover:bg-[#f5f5f5]">
                    <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                    </svg>
                </button>
            </div>

            <!-- 分组列表（可拖拽）-->
            <div class="flex-1 overflow-auto custom-scrollbar px-[12px] py-[8px]">
                <draggable v-if="managingGroups.length"
                           v-model="managingGroups"
                           item-key="id"
                           handle=".drag-handle"
                           ghost-class="drag-ghost"
                           @end="onManageDragEnd"
                           class="flex flex-col gap-[2px]">
                    <template #item="{ element: g }">
                        <div class="flex items-center gap-[10px] px-[8px] py-[8px] rounded-[4px] hover:bg-[#fafafa] transition">
                            <!-- 拖拽手柄 -->
                            <div class="drag-handle cursor-grab active:cursor-grabbing text-[#bbb] hover:text-[#666] transition shrink-0">
                                <svg class="w-[14px] h-[14px]" viewBox="0 0 20 20" fill="currentColor">
                                    <circle cx="6" cy="5" r="1.5"/><circle cx="6" cy="10" r="1.5"/><circle cx="6" cy="15" r="1.5"/>
                                    <circle cx="14" cy="5" r="1.5"/><circle cx="14" cy="10" r="1.5"/><circle cx="14" cy="15" r="1.5"/>
                                </svg>
                            </div>

                            <!-- 名称（查看态 or 编辑态）-->
                            <div class="flex-1 min-w-0">
                                <div v-if="managingEditingId !== g.id" class="flex items-center gap-[8px]">
                                    <span class="text-[13px] font-semibold text-[#111] truncate">{{ g.name }}</span>
                                </div>
                                <input v-else ref="managingEditInputRef"
                                       v-model="managingEditingName"
                                       @keyup.enter="saveManageEdit"
                                       @keyup.esc="cancelManageEdit"
                                       @blur="saveManageEdit"
                                       class="text-[13px] font-semibold text-[#dc2626] bg-white border border-[#dc2626] rounded-[3px] px-[6px] py-[3px] w-full outline-none">
                            </div>

                            <!-- 动作按钮 -->
                            <div v-if="managingEditingId !== g.id" class="flex items-center gap-[4px] shrink-0">
                                <button @click="startManageEdit(g)"
                                        class="w-[26px] h-[26px] text-[#666] hover:text-[#2563eb] hover:bg-[#eff6ff] rounded flex items-center justify-center transition"
                                        title="重命名">
                                    <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/>
                                    </svg>
                                </button>
                                <button @click="manageDeleteGroup(g)"
                                        class="w-[26px] h-[26px] text-[#666] hover:text-[#dc2626] hover:bg-[#fff0f0] rounded flex items-center justify-center transition"
                                        title="删除">
                                    <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                    </template>
                </draggable>
                <div v-else class="py-[40px] text-center text-[#ccc] text-[13px]">
                    点击下方 + 新建分组 开始
                </div>
            </div>

            <!-- 新建分组（底部）-->
            <div class="px-[20px] py-[14px] border-t border-[#f0f0f0] flex items-center gap-[8px] shrink-0">
                <input v-model="managingNewGroupName"
                       @keyup.enter="manageCreateGroup"
                       placeholder="新分组名称"
                       class="flex-1 text-[13px] px-[10px] py-[6px] border border-[#e5e5e5] rounded-[4px] outline-none focus:border-[#dc2626]">
                <button @click="manageCreateGroup"
                        :disabled="!managingNewGroupName.trim()"
                        class="text-[12px] font-bold text-white bg-[#dc2626] px-[14px] py-[6px] rounded-[4px] hover:bg-[#991b1b] disabled:bg-[#ccc] disabled:cursor-not-allowed transition">
                    + 新建
                </button>
                <button @click="closeManageModal"
                        class="text-[12px] text-[#666] border border-[#e5e5e5] px-[14px] py-[6px] rounded-[4px] hover:bg-[#f5f5f5] transition">
                    完成
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

/* 拖拽占位的视觉样式：拖起时原位显示一条淡红色轮廓 */
.drag-ghost {
    opacity: 0.4;
    background: #fff0f0 !important;
    border: 1px dashed #dc2626;
}
</style>
