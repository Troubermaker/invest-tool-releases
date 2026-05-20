<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import RightDrawer from './components/RightDrawer.vue'
import DashboardDrawer from './components/DashboardDrawer.vue'
import ActivationGate from './components/ActivationGate.vue'
import UpdateBanner from './components/UpdateBanner.vue'
import Toaster from './components/Toaster.vue'
import StockChartDrawer from './components/StockChartDrawer.vue'
import AddToWatchlistModal from './components/AddToWatchlistModal.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import AdminUnlockModal from './components/AdminUnlockModal.vue'
import MarketHub from './views/MarketHub.vue'
import Watchlist from './views/Watchlist.vue'
import Positions from './views/Positions.vue'
import QuantHub from './views/QuantHub.vue'
import Settings from './views/Settings.vue'
import { api } from './api/client'
import { pushWarn } from './composables/useNotifications'
import { useUserRole } from './composables/useUserRole'
import { useDailyAutoScan } from './composables/useDailyAutoScan'
import { useDashboard } from './composables/useDashboard'

const currentTab = ref('market')
const hubSubTabs = ref({ market: undefined, quant: undefined })
const isAIDrawerOpen = ref(false)
const dashboard = useDashboard()       // 今日仪表盘开关（共享单例，hub 顶部按钮 + Ctrl+J 都改它）

// 激活门禁状态：null=检查中（首屏空白片刻），false=未激活（显示 Gate），true=已激活
const isActivated = ref(null)

// 管理员模式 + 隐藏解锁 modal
const { refresh: refreshUserRole, isAdmin } = useUserRole()
const showAdminModal = ref(false)

// 管理员专属 tab —— 跟 Sidebar.vue 的 ALL_NAV_ITEMS[].adminOnly 必须保持一致
const ADMIN_ONLY_TABS = new Set(['quant'])

// 退出管理员模式时，如果当前在管理员专属 tab，自动退回到行情首页
// （否则 Sidebar 已隐藏入口但视图还在渲染，造成"我看到的内容点不到"的诡异感）
watch(isAdmin, (admin) => {
    if (!admin && ADMIN_ONLY_TABS.has(currentTab.value)) {
        currentTab.value = 'market'
    }
})

onMounted(async () => {
    const res = await api.isActivated()
    isActivated.value = !!(res.ok && res.data === true)
})

// 已激活后拉一次 admin 状态（未激活时不调，避免门禁拦截）
watch(isActivated, (v) => {
    if (v === true) {
        refreshUserRole()
    }
}, { immediate: true })

// 激活通过 + admin → 启动每日自动扫描守护
const autoScan = useDailyAutoScan()
watch([isActivated, isAdmin], ([act, adm]) => {
    if (act === true && adm === true) {
        autoScan.start()
    } else {
        autoScan.stop()
    }
}, { immediate: true })

// 隐藏快捷键 Ctrl+Shift+A：唤起管理员解锁 modal
// Ctrl+J：召唤 / 关闭 今日仪表盘抽屉（管理员才有意义，但门禁外也允许 toggle）
// Esc：关闭仪表盘抽屉（如已打开）
function onGlobalKeydown(e) {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'A' || e.key === 'a')) {
        e.preventDefault()
        if (isActivated.value === true) {
            showAdminModal.value = true
        }
        return
    }
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'j' || e.key === 'J')) {
        if (isActivated.value === true && isAdmin.value) {
            e.preventDefault()
            dashboard.toggle()
        }
        return
    }
    if (e.key === 'Escape' && dashboard.isOpen.value) {
        dashboard.close()
    }
}
onMounted(() => window.addEventListener('keydown', onGlobalKeydown))
onUnmounted(() => window.removeEventListener('keydown', onGlobalKeydown))

function handleNavigate(tabId, subTab = undefined) {
    currentTab.value = tabId
    if (subTab !== undefined) {
        hubSubTabs.value[tabId] = subTab
    }
}

// ============ 价格警报轮询（激活后每 10s 一次）============
let _alertPollTimer = null
const ALERT_POLL_INTERVAL = 10_000

async function pollAlerts() {
    try {
        const res = await api.getPendingAlerts(20)
        if (!res.ok || !res.data?.length) return
        const ids = []
        for (const a of res.data) {
            ids.push(a.id)
            const dir = a.alert_type === 'above' ? '↑ 上涨' : '↓ 下跌'
            const name = a.name || a.code
            // dedupKey 用警报 id，每条独立 —— 避免前端 30s 去重把后端真触发的多条吞掉
            pushWarn(
                `🔔 ${name} ${dir}至 ${Number(a.triggered_price).toFixed(2)}（阈值 ${Number(a.threshold).toFixed(2)}）`,
                { dedupKey: `alert_id_${a.id}`, ttlMs: 8000 }
            )
        }
        if (ids.length) await api.ackAlerts(ids)
    } catch (e) {
        console.warn('[alerts] pollAlerts 异常:', e)
    }
}

// 激活通过 → 开启轮询；未激活 → 不跑（数据访问被门禁拦着）
watch(isActivated, (v) => {
    if (v === true) {
        pollAlerts()  // 立即跑一次捕获积压
        if (_alertPollTimer) clearInterval(_alertPollTimer)
        _alertPollTimer = setInterval(pollAlerts, ALERT_POLL_INTERVAL)
    } else if (_alertPollTimer) {
        clearInterval(_alertPollTimer)
        _alertPollTimer = null
    }
}, { immediate: true })

onUnmounted(() => {
    if (_alertPollTimer) clearInterval(_alertPollTimer)
})
</script>

<template>
  <!-- 加载中：纯白屏（避免内容闪现）-->
  <div v-if="isActivated === null" class="h-screen w-full bg-[#fafafa]"></div>

  <!-- 未激活：全屏激活页，所有视图都不挂载（useSmartRefresh 不会启动）-->
  <ActivationGate v-else-if="!isActivated" @activated="isActivated = true" />

  <!-- 已激活：正常主界面（含底部免责声明 22px）-->
  <div v-else class="flex h-screen w-full bg-[#f9fafb] text-gray-800 overflow-hidden font-sans pb-[22px]">

    <!-- 在线更新通知（顶部，启动时静默检查 Gitee 上的 latest.json） -->
    <UpdateBanner />

    <!-- Left Sidebar -->
    <Sidebar :currentTab="currentTab"
             @navigate="handleNavigate"/>

    <!-- Main Content Area -->
    <main class="flex-1 relative flex flex-col min-w-0">
        <!-- 注：原顶部 ⭐⭐⭐+ 信号 banner 已撤掉 —— 太占常驻空间。
             今日信号现在挂在 hub 顶栏 "📊 今日" 按钮的红色徽章上，点开抽屉看完整列表 -->

        <!-- Render Active View（5 个顶层 tab + dashboard drawer + settings）-->
        <KeepAlive>
            <MarketHub v-if="currentTab === 'market'"
                       v-model:subTab="hubSubTabs.market"
                       @openAI="isAIDrawerOpen = true"/>
            <Watchlist v-else-if="currentTab === 'watchlist'" />
            <Positions v-else-if="currentTab === 'positions'" />
            <QuantHub v-else-if="currentTab === 'quant'"
                      v-model:subTab="hubSubTabs.quant" />
            <Settings v-else-if="currentTab === 'settings'" />

            <!-- Placeholders for other tabs for now -->
            <div v-else class="flex items-center justify-center h-full text-gray-400 flex-col bg-white m-4 rounded-xl border border-gray-200">
                <svg class="w-16 h-16 mb-4 text-red-200" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" /></svg>
                <h2 class="text-2xl font-semibold text-gray-500 capitalize mb-2">{{ currentTab }} 模块</h2>
                <p class="text-sm font-medium">界面数据接入中 (Development in progress)</p>
                <button @click="handleNavigate('market', 'live')" class="mt-4 px-4 py-2 border border-red-500 text-red-500 rounded-lg hover:bg-red-50 font-bold transition text-sm">返回行情首页</button>
            </div>
        </KeepAlive>
    </main>

    <!-- Right Drawer for AI -->
    <RightDrawer :isOpen="isAIDrawerOpen" @close="isAIDrawerOpen = false"/>

    <!-- 今日仪表盘抽屉（Ctrl+J 召唤 / hub 顶栏 "今日" 按钮 / Esc 关闭）-->
    <DashboardDrawer v-if="isAdmin"
        :open="dashboard.isOpen.value"
        @close="dashboard.close()"
        @navigate="(t, sub) => { handleNavigate(t, sub); dashboard.close() }"/>

    <!-- 全局 toast 通知（接口失败 / 重要事件） -->
    <Toaster />

    <!-- 全局 K 线 drawer（任意页双击股票行触发）-->
    <StockChartDrawer />

    <!-- 全局"添加到自选" modal（任意页股票行点 + 触发）-->
    <AddToWatchlistModal />

    <!-- 全局确认框（confirmDialog() 触发，统一替代浏览器原生 confirm）-->
    <ConfirmModal />

    <!-- 管理员解锁 modal（Ctrl+Shift+A 唤起）-->
    <AdminUnlockModal :open="showAdminModal" @close="showAdminModal = false" />

    <!-- 底部固定免责声明（22px，常驻不可关）-->
    <div class="fixed bottom-0 left-0 right-0 h-[22px] z-[150]
                bg-[#fafafa] border-t border-[#e5e7eb]
                flex items-center justify-center px-[12px]
                text-[10px] text-[#888] tracking-wide select-none pointer-events-none">
        <span>
            ⚠ 此软件仅供个人复盘参考；不构成投资建议，实际交易请以交易所原始数据为准。
        </span>
    </div>

  </div>
</template>

<style>
/* Ensure the body takes full height and hides scrollbars overall */
body {
    margin: 0;
    overflow: hidden;
}
/* Custom thin scrollbar for data panels */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #e5e7eb; /* gray-200 */
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: #d1d5db; /* gray-300 */
}
</style>
