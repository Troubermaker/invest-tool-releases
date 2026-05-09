import { createApp } from 'vue'
import './style.css'
import App from './App.vue'

createApp(App).mount('#app')

// ============== 开发期回测调试入口（Phase 1 Step 1.2 + 1.3）==============
// 仅在 vite dev 模式下暴露 window.bt，方便在 DevTools console 里手测回测，
// 不污染 production bundle。
//
//   bt.run(code, opts)            单只票回测
//   bt.runMany(codes, opts)       批量回测一组指定代码
//   bt.runAll(opts)               全市场回测（约 5000 只，30 分钟级）
//   bt.cancel()                   取消正在跑的批量
if (import.meta.env.DEV) {
    Promise.all([
        import('./composables/useBacktest.js'),
        import('./composables/useStockScanner.js'),
        import('./api/client.js'),
    ]).then(([
        { backtestStock, aggregateTrades },
        { useStockScanner },
        { api },
    ]) => {
        // 把 api 暴露到 window，方便在 console 直接调（仅 dev 模式）
        window.api = api
        // 共享一个批量 scanner，避免并发 bt.runMany 撞车
        let _activeScanner = null

        // ============== K 线缓存（后端 SQLite 持久化）==============
        // 调用 api.getStockKlineViaTdxCached → 后端 services/kline_cache_service 走 SQLite。
        // 跨重启 / 跨刷新 / 跨控制台清空全部留存；跨日自动失效。
        // 数据存独立 kline_cache.db 文件（不污染 invest_data.db）。
        // 仅 bt.* 走缓存；线上扫描器（ScanSignals/ScanCandidates）继续拿最新数据。
        const _cachedKlineFetch = (code, timeframe = '日K') =>
            api.getStockKlineViaTdxCached(code, timeframe)

        // 板块代码前缀映射（A 股代码段官方分类）
        const BOARD_PREFIXES = {
            sh_main: ['600', '601', '603', '605'],   // 沪市主板
            sz_main: ['000', '001', '003'],           // 深市主板（不含中小板）
            sme:     ['002'],                         // 中小板（2021 并入深市主板，但市场行为仍可单独追踪）
            star:    ['688'],                         // 科创板（20% 涨跌停 + 50 万门槛）
            gem:     ['300'],                         // 创业板（20% 涨跌停）
        }

        function _printAggregate(allTrades) {
            const stats = aggregateTrades(allTrades)
            console.log(`[bt] === 聚合统计（共 ${stats.totalTrades} 笔，剔除 truncated 后有效 ${stats.validTrades} 笔）===`)
            console.log('[bt] overall:', stats.overall)
            console.log('[bt] byGrade:')
            console.table(stats.byGrade)
            // Phase 4: 周共振切片
            if (stats.byWeeklyConfirmed) {
                console.log('[bt] byWeeklyConfirmed (周共振 vs 未共振):')
                console.table(stats.byWeeklyConfirmed)
            }
            return stats
        }

        async function _runBatch(stocks, btOpts = {}, scanOpts = {}) {
            if (_activeScanner?.scanning?.value) {
                console.warn('[bt] 已有批量在跑，先 bt.cancel() 取消')
                return null
            }
            // 注入后端持久化缓存的拉取函数 → 命中缓存时毫秒级，跨重启留存
            // batchSize=10：缓存命中场景下并发 10 个 SQLite 读不会触发 TDX 反爬（命中根本不打 TDX）
            // adaptiveGapThresholdMs=250：批次 < 250ms 跳过 gap；cache miss 时批次会慢，自动恢复节流保护 TDX
            const scanner = useStockScanner({
                ...scanOpts,
                fetchFn: _cachedKlineFetch,
                batchSize: 10,
                adaptiveGapThresholdMs: 250,
            })
            _activeScanner = scanner

            // 拉取批量开始前的缓存命中数（用于结束时算"新增缓存"）
            const cacheStatsBefore = await api.klineCacheStats().catch(() => null)
            const todayRowsBefore = cacheStatsBefore?.ok ? (cacheStatsBefore.data?.today_rows ?? 0) : 0

            const allTrades = []
            const t0 = Date.now()
            // 进度报告（每 30 只打一次，避免刷屏）
            let lastReport = 0
            const reportTimer = setInterval(() => {
                if (!scanner.scanning.value) return
                const n = scanner.scanned.value
                if (n - lastReport >= 30 || (n > 0 && n === scanner.total.value)) {
                    lastReport = n
                    const pct = scanner.progressPct.value
                    const speed = (n / ((Date.now() - t0) / 1000)).toFixed(1)
                    console.log(`[bt] 进度 ${n}/${scanner.total.value} (${pct}%) · ${speed} 只/s · trades=${allTrades.length} · current=${scanner.currentCode.value}`)
                }
            }, 2000)

            try {
                await scanner.scan(stocks, async (stock, klines) => {
                    // Phase 4: 顺手拉一次周 K（走缓存，命中后毫秒级；首次会慢但只会一次）
                    let weeklyKlines = null
                    try {
                        const wRes = await _cachedKlineFetch(stock.code, '周K')
                        if (wRes?.ok && Array.isArray(wRes.data) && wRes.data.length >= 25) {
                            weeklyKlines = wRes.data
                        }
                    } catch { /* 周K失败不阻塞日K回测，weeklyConfirmed 留 null */ }

                    const { trades } = backtestStock(klines, { ...btOpts, weeklyKlines })
                    if (!trades.length) return null
                    for (const t of trades) {
                        allTrades.push({ code: stock.code, name: stock.name || stock.code, ...t })
                    }
                    return { code: stock.code, count: trades.length }
                })
            } finally {
                clearInterval(reportTimer)
            }

            const elapsed = ((Date.now() - t0) / 1000).toFixed(1)
            const cacheStatsAfter = await api.klineCacheStats().catch(() => null)
            const todayRowsAfter = cacheStatsAfter?.ok ? (cacheStatsAfter.data?.today_rows ?? 0) : 0
            const newCached = Math.max(0, todayRowsAfter - todayRowsBefore)
            const cacheMsg = newCached > 0
                ? `· 缓存新增 ${newCached} 只（今日合计 ${todayRowsAfter}，约 ${cacheStatsAfter?.data?.size_mb ?? '?'} MB）`
                : `· 全部命中缓存（今日 ${todayRowsAfter} 条）`
            console.log(`[bt] 批量完成：扫 ${scanner.scanned.value} 只 · 跳过 ${scanner.skipped.value} 北交所 · 失败 ${scanner.errors.value.length} 只 · 耗时 ${elapsed}s ${cacheMsg}`)
            const stats = _printAggregate(allTrades)
            const result = { trades: allTrades, stats, errors: scanner.errors.value }
            window.bt.lastResult = result    // 保存供后续切片分析（避免 await 没赋值丢失）
            return result
        }

        window.bt = {
            backtestStock,
            aggregateTrades,

            /** 单只票回测 */
            async run(code, opts = {}) {
                console.log(`[bt] fetching ${code}...`)
                const res = await api.getStockKlineViaTdx(code, '日K')
                if (!res?.ok || !Array.isArray(res.data) || res.data.length < 100) {
                    console.error('[bt] kline 数据不足', res)
                    return null
                }
                console.log(`[bt] got ${res.data.length} bars, running backtest...`)
                const { events, trades } = backtestStock(res.data, opts)
                console.log(`[bt] events: ${events.length}, stage3 trades: ${trades.length}`)
                if (trades.length) {
                    console.table(trades.map(t => ({
                        s3Time:    t.s3Time,
                        grade:     t.grade,
                        score:     t.score,
                        entry:     t.entryPrice?.toFixed?.(2),
                        exit:      t.exitPrice?.toFixed?.(2),
                        exitWhy:   t.exitReason,         // 'time' | 'exit' | 'invalid'
                        hold:      t.holdBars,
                        return:    t.returnPct?.toFixed?.(2) + '%',
                        reduceW:   t.reduceWarnings,    // 持有期间爆量长上影警示次数
                        truncated: t.truncated ? 'Y' : '',
                    })))
                }
                const stats = aggregateTrades(trades)
                console.log('[bt] stats:', stats)
                window.bt.lastResult = { events, trades, stats }
                return { events, trades, stats }
            },

            /**
             * 批量回测一组指定代码（用户显式选的票，默认不过滤 ST / 次新股）。
             *   await bt.runMany(['600519', '300750', '002757'])
             *   await bt.runMany(codes, { holdDays: 60, excludeST: true, minBars: 250 })
             */
            async runMany(codes, opts = {}) {
                if (!Array.isArray(codes) || !codes.length) {
                    console.error('[bt] runMany 需要传一个非空代码数组')
                    return null
                }
                const { excludeST = false, minBars = 100, ...btOpts } = opts
                const stocks = codes.map(c => ({ code: String(c).trim(), name: '' }))
                console.log(`[bt] 批量回测 ${stocks.length} 只票（每批 3 只 + 抖动间隔）`)
                return _runBatch(stocks, btOpts, { excludeST, minBars })
            },

            /**
             * 主板批量回测（默认沪深主板 + 中小板，排除 ST + 次新股 + 科创/创业）。
             *
             *   await bt.runAll()                       // 默认沪深主板（含 002）
             *   await bt.runAll({ boards: ['sh_main','sz_main'] })   // 严格老主板，不含 002
             *   await bt.runAll({ boards: ['sh_main','sz_main','sme','star','gem'] })  // 全 A 股
             *   await bt.runAll({ holdDays: 60 })
             *
             * 板块 key（BOARD_PREFIXES）：
             *   sh_main   沪市主板  600/601/603/605
             *   sz_main   深市主板  000/001/003（不含中小板）
             *   sme       中小板   002（2021 并入深市主板，独立分桶便于对比）
             *   star      科创板   688
             *   gem       创业板   300
             *
             * 默认 excludeST: true、minBars: 250（≈上市 1 年，自动过滤次新股）
             */
            async runAll(opts = {}) {
                const {
                    boards    = ['sh_main', 'sz_main', 'sme'],   // 沪深主板（含已合并的中小板）
                    excludeST = true,
                    minBars   = 250,
                    ...btOpts
                } = opts

                const allowedPrefixes = boards.flatMap(b => BOARD_PREFIXES[b] || [])
                if (!allowedPrefixes.length) {
                    console.error(`[bt] boards 参数无效 ${JSON.stringify(boards)}，可选：${Object.keys(BOARD_PREFIXES).join('/')}`)
                    return null
                }

                console.log('[bt] 拉取全市场 A 股代码列表...')
                const res = await api.listAllAShareCodes()
                if (!res?.ok || !Array.isArray(res.data) || !res.data.length) {
                    console.error('[bt] 全市场代码列表拉取失败', res)
                    return null
                }

                const all = res.data
                const filtered = all.filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
                const stocks = filtered.map(s => ({ code: s.code, name: s.name }))
                const estMin = Math.round(stocks.length * 0.85 / 60 / 3 * 1.1)
                console.log(
                    `[bt] 板块筛选 ${stocks.length}/${all.length}（板块: ${boards.join(',')}）` +
                    ` · ST 排除: ${excludeST}` +
                    ` · 最少 K 线: ${minBars} 根（≈${(minBars / 250).toFixed(1)} 年，过滤次新股）` +
                    ` · 预计 ${estMin} 分钟`,
                )
                return _runBatch(stocks, btOpts, { excludeST, minBars })
            },

            /** 取消正在跑的批量回测 */
            cancel() {
                if (_activeScanner?.scanning?.value) {
                    _activeScanner.cancel()
                    console.log('[bt] 已取消')
                } else {
                    console.log('[bt] 当前没有运行中的批量')
                }
            },

            /** 后端 SQLite 缓存统计：今日命中行数 / 历史残留 / 文件大小 */
            async cacheStats() {
                const res = await api.klineCacheStats()
                if (!res?.ok) {
                    console.error('[bt] 缓存统计失败', res)
                    return null
                }
                console.log('[bt] 缓存状态：', res.data)
                return res.data
            },

            /** 手动清空缓存（跨日会自动失效，平时不需要调）。立即 VACUUM 回收磁盘 */
            async clearCache() {
                const res = await api.klineCacheClear()
                if (res?.ok) console.log('[bt] 缓存已清空（kline_cache.db VACUUM 完成）')
                else         console.error('[bt] 清空失败', res)
            },
        }
        console.log('[bt] 回测调试入口已注册：bt.run / bt.runMany / bt.runAll / bt.cancel / bt.cacheStats / bt.clearCache')
    })
}
