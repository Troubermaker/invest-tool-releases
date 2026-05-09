/**
 * 通达信式"下载今日 K 线数据"工具。
 *
 * 流程：
 *   1. checkFreshness(codes)  后端批查每只票 K 线最后一根日期 → cached / missing 分组
 *   2. 用户看预检结果（"已缓存 X · 待下载 Y"），决定是否继续
 *   3. download()  对 missing 列表逐只调用带缓存的 TDX 拉取（自动写入 SQLite）
 *
 * 跟 useStockScanner 的区别：
 *   - 不跑 detector / processor，只下 K 线进缓存
 *   - 没有 fresh / stale window 概念，按 K 线 last_date 严格判断
 *   - 显式两阶段：预检 → 用户确认 → 下载（避免无脑触发 30 分钟下载）
 *
 * 模块级单例 state — 整个 app 共享一份下载进度。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'

// ---------------- 模块级 state ----------------

const phase = ref('idle')   // 'idle' | 'checking' | 'ready' | 'downloading' | 'done' | 'error'
const error = ref(null)

// 预检结果
const targetDate     = ref(null)
const cachedCodes    = ref([])      // 已缓存到 target_date 的 codes
const missingCodes   = ref([])      // 完全没缓存或缓存的 K 线 last < target_date
const partialCodes   = ref([])      // Phase 7：盘中下载的 partial today K（需补全）
const newStockCodes  = ref([])      // 新股（K 线 < 60 根），从下载队列排除

// 下载进度
const downloaded    = ref(0)
const failed        = ref(0)
const currentCode   = ref('')

let _cancelToken = 0

// 下载总数 = 缺失 + 不完整（两类都要重拉）
const totalToDownload = computed(() => missingCodes.value.length + partialCodes.value.length)
const downloadProgressPct = computed(() => {
    const t = totalToDownload.value
    if (!t) return 0
    return Math.round((downloaded.value + failed.value) / t * 100)
})

// ---------------- 操作 ----------------

/**
 * 阶段 1：预检 — 批量查询每只票缓存 freshness。
 * 完成后 phase 变 'ready'，等用户调 download() 触发下载。
 */
async function checkFreshness(codes) {
    if (phase.value === 'checking' || phase.value === 'downloading') return null
    const myToken = ++_cancelToken
    phase.value = 'checking'
    error.value = null
    cachedCodes.value = []
    missingCodes.value = []
    targetDate.value = null

    try {
        const res = await api.bulkCheckKlineFreshness(codes, '日K')
        if (myToken !== _cancelToken) return null
        if (!res?.ok || !res.data) {
            error.value = res?.error || '预检失败'
            phase.value = 'error'
            return null
        }
        targetDate.value    = res.data.target_date
        cachedCodes.value   = res.data.cached || []
        missingCodes.value  = res.data.missing || []
        partialCodes.value  = res.data.partial || []
        newStockCodes.value = res.data.new_stocks || []
        phase.value = 'ready'
        return res.data
    } catch (e) {
        error.value = String(e)
        phase.value = 'error'
        return null
    }
}

/**
 * K 线 time 字段抽出 'YYYY-MM-DD' 字符串。兼容字符串 / dict / 时间戳。
 * 跟 _normalize_kline_time_key 后端逻辑对齐。
 */
function _extractDateStr(t) {
    if (!t) return null
    if (typeof t === 'string') return t.length >= 10 ? t.slice(0, 10) : null
    if (typeof t === 'object' && t.year) {
        const m = String(t.month ?? 1).padStart(2, '0')
        const d = String(t.day   ?? 1).padStart(2, '0')
        return `${t.year}-${m}-${d}`
    }
    if (typeof t === 'number') {
        const ms = t > 1e12 ? t : t * 1000
        const d = new Date(ms)
        if (Number.isNaN(d.getTime())) return null
        return d.toISOString().slice(0, 10)
    }
    return null
}

/**
 * 阶段 2：下载 missing codes。
 * 节流参考 useStockScanner（batch 10 并发 + 250ms gap）。
 * 命中缓存的票（可能在预检和下载之间被其它流程填充）会毫秒级跳过。
 *
 * 成功判定：拿到的 K 线最后一根日期 ≥ targetDate。仅 data.length>0 不够 ——
 * TDX 拉失败时后端退回老缓存，老 K 线 length>0 但 last_date 没到 target。
 */
async function download(opts = {}) {
    if (phase.value !== 'ready') return null
    // 下载队列 = missing + partial（partial 需要重拉今日 K 覆盖盘中残留的不完整数据）
    const todo = [...missingCodes.value, ...partialCodes.value]
    if (!todo.length) {
        phase.value = 'done'
        return { downloaded: 0, failed: 0 }
    }
    const { batchSize = 10, gapMs = 250 } = opts
    const myToken = ++_cancelToken
    phase.value = 'downloading'
    downloaded.value = 0
    failed.value = 0
    currentCode.value = ''
    try {
        for (let i = 0; i < todo.length; i += batchSize) {
            if (myToken !== _cancelToken) break
            const batch = todo.slice(i, i + batchSize)
            const t0 = Date.now()
            await Promise.all(batch.map(async code => {
                if (myToken !== _cancelToken) return
                currentCode.value = code
                try {
                    const res = await api.getStockKlineViaTdxCached(code, '日K')
                    // 严格成功判定：last bar 日期必须 ≥ targetDate
                    // —— 防止后端拉失败时返回老缓存被误计入"成功"
                    const arr = Array.isArray(res?.data) ? res.data : null
                    const lastBar = arr?.length ? arr[arr.length - 1] : null
                    const lastDate = _extractDateStr(lastBar?.time)
                    const target = targetDate.value
                    const ok = res?.ok && lastDate && (!target || lastDate >= target)
                    if (ok) downloaded.value++
                    else failed.value++
                } catch {
                    failed.value++
                }
            }))
            // 自适应 gap：批次响应快（后端已经全命中缓存）就跳过等待
            if (i + batchSize < todo.length && myToken === _cancelToken) {
                const elapsed = Date.now() - t0
                if (elapsed >= 250) {
                    await new Promise(r => setTimeout(r, gapMs))
                }
            }
        }
        if (myToken === _cancelToken) {
            phase.value = 'done'
            currentCode.value = ''
        }
    } catch (e) {
        error.value = String(e)
        phase.value = 'error'
    }

    return {
        downloaded: downloaded.value,
        failed:     failed.value,
        total:      todo.length,
    }
}

function cancel() {
    _cancelToken++
    phase.value = 'idle'
    currentCode.value = ''
}

function reset() {
    _cancelToken++
    phase.value = 'idle'
    error.value = null
    targetDate.value = null
    cachedCodes.value = []
    missingCodes.value = []
    partialCodes.value = []
    newStockCodes.value = []
    downloaded.value = 0
    failed.value = 0
    currentCode.value = ''
}

// ---------------- 导出 ----------------

export function useKlineDownloader() {
    return {
        // state
        phase, error,
        targetDate, cachedCodes, missingCodes, partialCodes, newStockCodes,
        downloaded, failed, currentCode,
        totalToDownload, downloadProgressPct,
        // actions
        checkFreshness, download, cancel, reset,
    }
}
