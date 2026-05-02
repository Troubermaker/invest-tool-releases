/**
 * 通用股票扫描器 composable。
 *
 * 各类扫描场景（fresh 突破 / 蓄势候选 / 涨停后空中加油 / ...）共享同一套节流批拉框架：
 *   - 批量 3 只 + 抖动间隔（避开数据源反爬限频）
 *   - 进度计数 + 当前 code 实时显示
 *   - 可取消（关闭 modal / 切换分组等）
 *   - 失败 code 收集（数据不足 / 接口异常）
 *
 * 调用方传入一个 processor 回调，对每只股票的日 K 做处理，返回 result 或 null：
 *
 *   const scanner = useStockScanner()
 *   await scanner.scan(stocks, async (stock, klines) => {
 *       // 自定义检测逻辑
 *       return shouldKeep ? { ...someFields } : null
 *   })
 *   scanner.results.value  // [...过滤后的结果]
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'

export function useStockScanner(opts = {}) {
    const {
        batchSize    = 3,
        minGapMs     = 600,
        maxGapMs     = 1100,
        timeframe    = '日K',
        minBars      = 100,    // 数据条数不足直接归为 errors
        excludeST    = false,  // ST 不强制排除（部分 ST 也有合法形态信号）
        excludeBJ    = true,   // 北交所（4xxx/8xxx/920xxx）pytdx 不支持 + 用户不关注，默认排除
    } = opts

    const scanning   = ref(false)
    const cancelled  = ref(false)
    const scanned    = ref(0)
    const total      = ref(0)
    const currentCode = ref('')
    const results    = ref([])
    const errors     = ref([])
    const skipped    = ref(0)    // ST 等开扫前就过滤掉的数量
    const startedAt  = ref(0)

    let _token = 0

    const progressPct = computed(() => {
        if (!total.value) return 0
        return Math.round(scanned.value / total.value * 100)
    })

    /**
     * 开始扫描。stocks: [{code, name}]，processor: async (stock, klines) => result|null
     * 返回 Promise，扫完 / 取消时 resolve。
     */
    async function scan(stocks, processor) {
        if (scanning.value) return
        if (!stocks || !stocks.length) {
            results.value = []
            errors.value = []
            total.value = 0
            skipped.value = 0
            return
        }
        // 开扫前剔除 ST 类（按股名）+ 北交所（按代码前缀）
        const ST_RE = /^\*?S?ST/i
        const BJ_RE = /^(4|8|920)/    // 4xxx/8xxx/920xxx 北交所
        const filtered = stocks.filter(s => {
            if (excludeST && ST_RE.test((s.name || '').trim())) return false
            if (excludeBJ && BJ_RE.test((s.code || '').trim())) return false
            return true
        })
        const skippedCount = stocks.length - filtered.length

        const myToken = ++_token
        scanning.value  = true
        cancelled.value = false
        scanned.value   = 0
        total.value     = filtered.length
        currentCode.value = ''
        results.value   = []
        errors.value    = []
        skipped.value   = skippedCount
        startedAt.value = Date.now()

        for (let i = 0; i < filtered.length; i += batchSize) {
            if (myToken !== _token || cancelled.value) break
            const batch = filtered.slice(i, i + batchSize)
            await Promise.all(batch.map(async (s) => {
                if (myToken !== _token || cancelled.value) return
                currentCode.value = s.code
                try {
                    // admin-only 扫描器走 TDX（pytdx 私有协议，无反爬）；
                    // 端点内部 TDX 失败时会自动 fallback 到普通 kline_service
                    const res = await api.getStockKlineViaTdx(s.code, timeframe)
                    if (myToken !== _token || cancelled.value) return
                    if (!res.ok || !Array.isArray(res.data) || res.data.length < minBars) {
                        errors.value.push({ code: s.code, name: s.name, reason: '数据不足' })
                        return
                    }
                    const result = await processor(s, res.data)
                    if (myToken !== _token || cancelled.value) return
                    if (result) results.value.push(result)
                } catch (e) {
                    errors.value.push({ code: s.code, name: s.name, reason: String(e) })
                } finally {
                    scanned.value++
                }
            }))
            // 批间抖动间隔（最后一批不睡）
            if (i + batchSize < filtered.length && !cancelled.value && myToken === _token) {
                const gap = minGapMs + Math.random() * (maxGapMs - minGapMs)
                await new Promise(r => setTimeout(r, gap))
            }
        }

        if (myToken === _token) {
            scanning.value = false
            currentCode.value = ''
        }
    }

    function cancel() {
        cancelled.value = true
        scanning.value = false
        _token++  // 顺手让任何在途回调失效
    }

    function reset() {
        results.value = []
        errors.value = []
        scanned.value = 0
        total.value = 0
        skipped.value = 0
        currentCode.value = ''
    }

    return {
        // state
        scanning, cancelled, scanned, total, currentCode, results, errors, skipped, progressPct, startedAt,
        // actions
        scan, cancel, reset,
    }
}
