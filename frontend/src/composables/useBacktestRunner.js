/**
 * UI 触发版回测运行器 —— 给 Backtest.vue 的"新建回测"表单用，不依赖 window.bt（开发态/生产态都能跑）。
 *
 * 跟 main.js 里 dev-only 的 _runBatch 思路一致，但精简：
 *   - 只支持主升突破 (threeStage) detector（最主流，覆盖 90% 场景）
 *   - 不做 dumpFeatures / mlFilter / stretched 等高级开关（这些留控制台用户）
 *   - 暴露 reactive refs 供 UI 显示进度条 + 取消按钮
 *
 * 落库时把 equityCurve（资金曲线）注入 summary，详情面板能直接画。
 */
import { ref, computed } from 'vue'
import { api } from '../api/client'
import { useStockScanner } from './useStockScanner'
import { backtestStock, aggregateTrades } from './useBacktest'
import { useMarketEnv } from './useMarketEnv'

// 板块代码前缀映射（跟 main.js 同步）
const BOARD_PREFIXES = {
    sh_main: ['600', '601', '603', '605'],
    sz_main: ['000', '001', '003'],
    sme:     ['002'],
    star:    ['688'],
    gem:     ['300', '301'],
}

// 资金曲线（trades → 按平仓日累计净值 + 最大回撤）
function computeEquityCurve(trades) {
    if (!Array.isArray(trades) || !trades.length) return []
    const toDate = (t) => {
        const sec = +t.exitTime || +t.entryTime
        if (!sec) return null
        return new Date(sec * 1000).toISOString().slice(0, 10)
    }
    const byDate = new Map()
    for (const t of trades) {
        if (t.truncated) continue
        const d = toDate(t)
        if (!d) continue
        const ret = +t.returnPct
        if (!Number.isFinite(ret)) continue
        const cell = byDate.get(d) || { date: d, daily: 0, count: 0 }
        cell.daily += ret
        cell.count++
        byDate.set(d, cell)
    }
    const points = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date))
    let cum = 0, peak = 0
    for (const p of points) {
        cum += p.daily
        if (cum > peak) peak = cum
        p.cum = +cum.toFixed(4)
        p.drawdown = +(cum - peak).toFixed(4)
    }
    return points
}

export function useBacktestRunner() {
    const running        = ref(false)
    const scanned        = ref(0)
    const total          = ref(0)
    const currentCode    = ref('')
    const errorCount     = ref(0)
    const lastResult     = ref(null)   // { trades, stats, runId }
    const lastError      = ref(null)

    const progressPct = computed(() => total.value > 0 ? Math.floor(scanned.value / total.value * 100) : 0)

    let _scanner = null

    function cancel() {
        if (_scanner) _scanner.cancel()
    }

    /**
     * 启动一次回测。
     * @param opts {
     *   boards: string[], holdDays: number, excludeST: boolean,
     *   dumpFeatures: boolean,        // true 时附 features+mlLabels，落 NDJSON 给 ML 训练用
     *   dumpName: string,             // dumpFeatures=true 时的文件名后缀
     * }
     * @returns { ok: boolean, runId?: number, datasetFilename?: string, error?: string }
     */
    async function run(opts = {}) {
        if (running.value) {
            return { ok: false, error: '已有回测在跑，请先取消' }
        }
        running.value = true
        scanned.value = 0
        total.value = 0
        currentCode.value = ''
        errorCount.value = 0
        lastError.value = null

        const {
            boards       = ['sh_main', 'sz_main', 'sme'],
            holdDays     = 7,
            excludeST    = true,
            minBars      = 250,
            dumpFeatures = false,
            dumpName     = null,
        } = opts

        try {
            // 1) 拉全市场代码 + 板块过滤
            const codesRes = await api.listAllAShareCodes()
            if (!codesRes?.ok || !Array.isArray(codesRes.data)) {
                throw new Error('代码列表拉取失败')
            }
            const allowedPrefixes = boards.flatMap(b => BOARD_PREFIXES[b] || [])
            if (!allowedPrefixes.length) throw new Error('板块参数无效')

            const stocks = codesRes.data
                .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
                .map(s => ({ code: s.code, name: s.name }))

            if (!stocks.length) throw new Error('过滤后没有符合的股票')

            // 2) 用 useStockScanner 做并发抓取 + 节流
            _scanner = useStockScanner({
                excludeST,
                minBars,
                batchSize: 10,
                adaptiveGapThresholdMs: 250,
                // 复用带后端缓存的 fetch（命中缓存毫秒级）
                fetchFn: (code, tf) => api.getStockKlineViaTdxCached(code, tf, 800),
            })
            total.value = stocks.length

            // 进度同步：scanner 内部 ref → 我们的外部 ref
            const allTrades = []
            const progressTimer = setInterval(() => {
                if (!_scanner) return
                scanned.value = _scanner.scanned.value
                currentCode.value = _scanner.currentCode.value || ''
            }, 250)

            try {
                await _scanner.scan(stocks, async (stock, klines) => {
                    // 顺手拉周 K（提供 weeklyConfirmed 维度）
                    let weeklyKlines = null
                    try {
                        const wRes = await api.getStockKlineViaTdxCached(stock.code, '周K', 200)
                        if (wRes?.ok && Array.isArray(wRes.data) && wRes.data.length >= 25) {
                            weeklyKlines = wRes.data
                        }
                    } catch { /* 周K 失败不阻塞 */ }

                    // dumpFeatures=true 时让 backtestStock 给每个 trade 附 features + mlLabels
                    const { trades } = backtestStock(klines, { holdDays, weeklyKlines, dumpFeatures })
                    if (!trades.length) return null
                    for (const t of trades) {
                        allTrades.push({ code: stock.code, name: stock.name || stock.code, ...t })
                    }
                    return { code: stock.code, count: trades.length }
                })
            } finally {
                clearInterval(progressTimer)
                scanned.value = _scanner.scanned.value
                errorCount.value = _scanner.errors.value.length
            }

            if (_scanner.cancelled?.value) {
                return { ok: false, error: '已取消' }
            }

            // 3) 注入大盘 regime
            try {
                const me = useMarketEnv()
                await me.refresh(false)
                const regime = me.currentRegime.value
                for (const t of allTrades) {
                    if (t.features) t.features.marketRegime = regime ?? null
                }
            } catch (e) { /* 不阻塞 */ }

            // 4) 聚合统计 + 资金曲线
            const stats = aggregateTrades(allTrades, { holdDays })
            stats.equityCurve = computeEquityCurve(allTrades)

            // 5) dumpFeatures=true 时先落 ML dataset NDJSON（再保 run，方便把 dataset 字段塞 run 里）
            let datasetFilename = null
            if (dumpFeatures) {
                try {
                    const rows = allTrades
                        .filter(t => t.features && t.mlLabels)
                        .map(t => ({
                            code: t.code,
                            name: t.name,
                            ...t.features,
                            ...t.mlLabels,
                        }))
                    if (rows.length) {
                        const dsName = dumpName || `ui_${Date.now()}`
                        const dsRes = await api.saveMLDataset(rows, dsName)
                        if (dsRes?.ok) datasetFilename = dsRes.data?.filename || null
                    }
                } catch (e) {
                    console.warn('保存 ML dataset 失败', e)
                }
            }

            // 6) 落库 backtest_runs，把关联 dataset 一并记下（智能重训后续 update produced_model）
            let runId = null
            try {
                const saveRes = await api.saveBacktestRun({
                    run_type:    'runAll',
                    sample_size: scanned.value,
                    hold_days:   holdDays,
                    boards,
                    detector_opts: {},
                    summary:     stats,
                    notes:       '',
                    produced_dataset: datasetFilename,
                })
                if (saveRes?.ok) runId = saveRes.data
            } catch (e) {
                console.warn('保存回测结果失败', e)
            }

            lastResult.value = { trades: allTrades, stats, runId, datasetFilename }
            return { ok: true, runId, datasetFilename }
        } catch (e) {
            lastError.value = String(e?.message || e)
            return { ok: false, error: lastError.value }
        } finally {
            running.value = false
            _scanner = null
        }
    }

    return {
        // state
        running, scanned, total, currentCode, errorCount, progressPct,
        lastResult, lastError,
        // actions
        run, cancel,
    }
}
