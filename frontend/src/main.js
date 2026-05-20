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
        import('./composables/useTechIndicators.js'),
        import('./api/client.js'),
        import('./composables/useMarketEnv.js'),
    ]).then(([
        { backtestStock, aggregateTrades, backtestStretchedHistorical },
        { useStockScanner },
        { detectStretchedRally, detectDragonReturn },
        { api },
        { useMarketEnv },
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

        // ============== Phase 0 V0 共享 helper ==============

        /**
         * 通用模拟交易：给定 signalIdx + stopLossPrice + holdDays，返回 trade 对象或 null。
         * 入场 = signalIdx + 1 开盘价；出场 = holdDays 到期或 close < stopLossPrice（先到先出）。
         */
        function _simulateGenericTrade(klines, signalIdx, stopLossPrice, holdDays) {
            const entryIdx = signalIdx + 1
            if (entryIdx >= klines.length) return null
            const entryPrice = +klines[entryIdx].open
            if (!(entryPrice > 0)) return null

            const maxExitIdx = Math.min(klines.length - 1, entryIdx + holdDays - 1)
            let exitIdx = maxExitIdx
            let exitReason = 'time'
            for (let t = entryIdx + 1; t <= maxExitIdx; t++) {
                const close = +klines[t].close
                if (stopLossPrice && Number.isFinite(close) && close < stopLossPrice) {
                    exitIdx = t
                    exitReason = 'stop'
                    break
                }
            }
            const exitPrice = +klines[exitIdx].close
            const returnPct = (exitPrice - entryPrice) / entryPrice * 100
            const holdBars = exitIdx - entryIdx + 1
            const truncated = exitReason === 'time' && holdBars < holdDays
            return {
                signalIdx, entryIdx, entryPrice, exitIdx, exitPrice,
                returnPct, exitReason, holdBars, truncated,
            }
        }

        /**
         * 历史滑窗扫描：用 stride 步长滑过 klines，每次跑 detector 拿"那时的信号"。
         * 用 Set 去重相同 signalIdx 的事件（同一信号在 stride 内会被多次返回）。
         *
         * 性能关键：用 detector 的 asOfIdx 参数代替 klines.slice() —— 避免每次滑窗
         * 都复制 800-bar 数组（之前 60 万次 slice 是 V0 卡 20 分钟的元凶）。
         *
         * @param {Array} klines 全部历史 K（共享同一份 reference）
         * @param {Function} detector 接受 (klines, opts) → 单 event 或 null
         * @param {Function} getKey 从 event 提取唯一 idx（如 e.breakIdx / e.ignitionIdx）
         * @param {Object} opts { stride, startAt, holdDays }
         */
        function _scanHistorically(klines, detector, getKey, opts = {}) {
            const { stride = 15, startAt = 100, holdDays = 7 } = opts
            const events = []
            const seen = new Set()
            const endAt = klines.length - holdDays - 1
            for (let i = startAt; i <= endAt; i += stride) {
                let e
                try { e = detector(klines, { asOfIdx: i }) } catch { continue }
                if (!e) continue
                const key = getKey(e)
                if (key == null || seen.has(key)) continue
                seen.add(key)
                events.push(e)
            }
            return events
        }

        function _summarizeV0(trades) {
            if (!trades.length) return { trades: 0 }
            const returns = trades.map(t => t.returnPct).sort((a, b) => a - b)
            const wins = trades.filter(t => t.returnPct > 0).length
            const sum = returns.reduce((s, x) => s + x, 0)
            const fmt = (v) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
            return {
                trades:  trades.length,
                'win%':  (wins / trades.length * 100).toFixed(1) + '%',
                avgRet:  fmt(sum / trades.length),
                median:  fmt(returns[Math.floor(returns.length / 2)]),
                p25:     fmt(returns[Math.floor(returns.length * 0.25)]),
                p75:     fmt(returns[Math.floor(returns.length * 0.75)]),
                maxRet:  fmt(returns[returns.length - 1]),
                minRet:  fmt(returns[0]),
            }
        }

        function _printAggregate(allTrades) {
            const stats = aggregateTrades(allTrades)
            console.log(`[bt] === 聚合统计（共 ${stats.totalTrades} 笔，剔除 truncated 后有效 ${stats.validTrades} 笔）===`)
            console.log('[bt] overall:', stats.overall)
            console.log('[bt] byGrade:')
            console.table(stats.byGrade)
            if (stats.byWeeklyConfirmed) {
                console.log('[bt] byWeeklyConfirmed (周共振 vs 未共振):')
                console.table(stats.byWeeklyConfirmed)
            }

            // === Phase 1 Day 1: 新增 3 张切片表 ===

            // grade × weekly 交叉表 —— 找最强的 4-cell 组合
            if (stats.byGradeWeekly && Object.keys(stats.byGradeWeekly).length) {
                console.log('[bt] === byGradeWeekly (grade × 周共振 交叉表，找最强组合) ===')
                console.table(stats.byGradeWeekly)
                // 自动标记 Top 3 胜率组合（trade 数 ≥ 30 才算稳定，否则统计不显著）
                const cellList = Object.entries(stats.byGradeWeekly)
                    .filter(([_k, v]) => v.count >= 30)
                    .map(([k, v]) => ({ combo: k, count: v.count, winRate: v.winRate, avgRet: v.avgReturn }))
                    .sort((a, b) => b.winRate - a.winRate)
                if (cellList.length) {
                    console.log('[bt] 🏆 grade×weekly Top 3 胜率组合（仅显示 trade≥30 的）:')
                    console.table(cellList.slice(0, 3).map(c => ({
                        combo:   c.combo,
                        trades:  c.count,
                        'win%':  (c.winRate * 100).toFixed(1) + '%',
                        avgRet:  (c.avgRet >= 0 ? '+' : '') + c.avgRet.toFixed(2) + '%',
                    })))
                }
            }

            // exit reason 切片
            if (stats.byExitReason) {
                console.log('[bt] === byExitReason (出场原因分组) ===')
                console.table(stats.byExitReason)
            }

            // 持有期分桶
            if (stats.byHoldDuration) {
                console.log('[bt] === byHoldDuration (持有期分桶) ===')
                console.table(stats.byHoldDuration)
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
                    // skipWeekly=true 时跳过 —— 周K 大批量 cache miss 会拖慢，weeklyConfirmed 留 null
                    let weeklyKlines = null
                    if (!btOpts.skipWeekly) {
                        try {
                            const wRes = await _cachedKlineFetch(stock.code, '周K')
                            if (wRes?.ok && Array.isArray(wRes.data) && wRes.data.length >= 25) {
                                weeklyKlines = wRes.data
                            }
                        } catch { /* 周K失败不阻塞日K回测，weeklyConfirmed 留 null */ }
                    }

                    // Phase 3：可选多 detector —— threeStage 默认开，stretched 通过 opts.detectors 传入开启
                    const useThreeStage = !btOpts.detectors || btOpts.detectors.includes('threeStage')
                    const useStretched  = btOpts.detectors && btOpts.detectors.includes('stretched')

                    const merged = []
                    if (useThreeStage) {
                        const { trades } = backtestStock(klines, { ...btOpts, weeklyKlines })
                        for (const t of trades) {
                            // 写入 features.signalSource（threeStage 时 extractTradeFeatures 已默认填）
                            merged.push(t)
                        }
                    }
                    if (useStretched && klines.length >= 280) {
                        const { trades: sTrades } = backtestStretchedHistorical(klines, btOpts)
                        for (const t of sTrades) {
                            // 标识到顶层 trade（features 内部 signalSource 已经写了）
                            t.exitReason = 'time'   // 显式
                            merged.push(t)
                        }
                    }
                    if (!merged.length) return null
                    for (const t of merged) {
                        allTrades.push({ code: stock.code, name: stock.name || stock.code, ...t })
                    }
                    return { code: stock.code, count: merged.length }
                })
            } finally {
                clearInterval(reportTimer)
            }

            // Week 1 Day 1-5 + Week 2 + P2：给 trades 注入 marketRegime + sectorStrength + LHB 特征
            try {
                // 1. 拉当前 regime
                const me = useMarketEnv()
                await me.refresh(false)
                const regime = me.currentRegime.value
                for (const t of allTrades) {
                    if (t.features) t.features.marketRegime = regime ?? null
                }
                // 2. 批量拉板块强度
                const codes = [...new Set(allTrades.map(t => t.code))]
                if (codes.length) {
                    const r = await api.getBatchStockSectorStrengths(codes)
                    if (r?.ok && r.data) {
                        for (const t of allTrades) {
                            const info = r.data[t.code]
                            if (info && typeof info.best_sector_score === 'number' && t.features) {
                                t.features.sectorStrength = info.best_sector_score
                            }
                        }
                    }
                }
                // 3. P2 龙虎榜特征：按每笔 trade 的 s3Time 时点单独查询（防未来泄漏）
                //    按 (code, s3Time) 分组，每组一次 batch 调用，避免逐笔 RPC
                const groups = {}   // { 's3Time YYYY-MM-DD': [code, code, ...] }
                for (const t of allTrades) {
                    if (!t.features || !t.s3Time) continue
                    const dateKey = String(t.s3Time).slice(0, 10)
                    groups[dateKey] = groups[dateKey] || new Set()
                    groups[dateKey].add(t.code)
                }
                for (const [dateKey, codeSet] of Object.entries(groups)) {
                    try {
                        const r = await api.getBatchLhbFeatures([...codeSet], dateKey, 30)
                        if (r?.ok && r.data) {
                            for (const t of allTrades) {
                                if (!t.features) continue
                                if (String(t.s3Time).slice(0, 10) !== dateKey) continue
                                const lhb = r.data[t.code]
                                if (lhb) {
                                    t.features.lhbInWindow      = lhb.lhb_in_window
                                    t.features.lhbCount         = lhb.lhb_count
                                    t.features.lhbNetBuySum     = lhb.lhb_net_buy_sum
                                    t.features.daysSinceLastLhb = lhb.days_since_last_lhb
                                }
                            }
                        }
                    } catch (e) { /* 单组失败不阻塞 */ }
                }
            } catch (e) {
                console.warn('[bt] 注入 regime/sector/LHB 失败（不影响主流程）', e)
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

            // —— 资金曲线（按 entry/exit 日累计 returnPct）—— Backtest.vue 详情面板展示用 ——
            // 等额单仓位假设：每笔 trade 仓位 = 1 单位，累计净值 = Σ returnPct
            // 最大回撤 = min(累计 - 历史 peak)
            stats.equityCurve = _computeEquityCurve(allTrades)

            const result = { trades: allTrades, stats, errors: scanner.errors.value }
            window.bt.lastResult = result    // 保存供后续切片分析（避免 await 没赋值丢失）

            // Phase 1 Day 3 自动存盘：summary 入库供 Backtest.vue 历史查看
            try {
                const saveRes = await api.saveBacktestRun({
                    run_type:    'runAll',
                    sample_size: scanner.scanned.value,
                    hold_days:   btOpts.holdDays ?? 7,
                    boards:      scanOpts.boards || null,
                    detector_opts: btOpts.detectOpts || {},
                    summary:     stats,
                    notes:       '',
                })
                if (saveRes?.ok) console.log(`[bt] 💾 已保存 run #${saveRes.data}（Backtest 模块可查）`)
            } catch (e) { console.warn('[bt] 保存 run 失败（不影响结果）', e) }

            return result
        }

        // 资金曲线计算（trades → 按交易日聚合 → 累计净值 / 最大回撤）
        // 用 exit_time（平仓日）入账，符合"trade 落地后才计 P&L"的会计逻辑
        function _computeEquityCurve(trades) {
            if (!Array.isArray(trades) || !trades.length) return []
            // 用 exitTime 入账；exitTime 是 unix 秒，转 YYYY-MM-DD
            const toDate = (t) => {
                const sec = +t.exitTime || +t.entryTime
                if (!sec) return null
                return new Date(sec * 1000).toISOString().slice(0, 10)
            }
            const byDate = new Map()
            for (const t of trades) {
                if (t.truncated) continue              // 持有期没跑完的不计入
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
                    mlFilter  = false,    // Phase 2 ML 黑名单快捷开关（透传到 detectOpts）
                                          // false / true / 'strict' / 'lite'
                    ...btOpts
                } = opts
                // 顶层 mlFilter → detectOpts.mlFilter（不覆盖用户显式传的 detectOpts）
                if (mlFilter) {
                    btOpts.detectOpts = { ...(btOpts.detectOpts || {}), mlFilter }
                }

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

            /**
             * Phase 2 ML：跑一次 runAll，但每笔 trade 附 features + 多 horizon 标签，
             * 跳完自动落盘到 data/ml_dataset/dataset_*.ndjson 供 LightGBM 训练。
             *
             *   await bt.runDump()                        // 默认沪深主板，holdDays 任意（标签是 T+3/7/14/21）
             *   await bt.runDump({ name: 'baseline_v1' }) // 自定义文件名后缀
             *   await bt.runDump({ boards: ['sh_main','sz_main','sme','star','gem'] })  // 全 A 股
             */
            async runDump(opts = {}) {
                // skipWeekly 默认 true：周K cache miss 时全市场补拉 ~30 分钟，weeklyConfirmed 留 null
                // boards 默认主板 + 中小板（跟"下载今日 K"按钮覆盖范围一致，避免缓存 miss）
                // 想跑 star/gem 显式传 boards: ['sh_main','sz_main','sme','star','gem']，但要先单独预热缓存
                // detectors：['threeStage', 'stretched'] —— 各自产 trades，由 signalSource 区分
                const {
                    name = null,
                    boards = ['sh_main','sz_main','sme'],
                    excludeST = true,
                    minBars = 250,
                    skipWeekly = true,
                    detectors = ['threeStage', 'stretched'],
                    ...btOpts
                } = opts

                const allowedPrefixes = boards.flatMap(b => BOARD_PREFIXES[b] || [])
                if (!allowedPrefixes.length) { console.error('[bt] boards 无效'); return null }

                console.log('[bt-dump] 拉取代码列表...')
                const res = await api.listAllAShareCodes()
                if (!res?.ok) { console.error('[bt-dump] 代码列表失败', res); return null }
                const stocks = res.data
                    .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
                    .map(s => ({ code: s.code, name: s.name }))
                console.log(`[bt-dump] ${stocks.length} 只票，dumpFeatures=true，跳完自动落 NDJSON`)

                // 复用 _runBatch，强制 dumpFeatures=true，并透传 skipWeekly 和 detectors
                const result = await _runBatch(stocks, { ...btOpts, dumpFeatures: true, skipWeekly, detectors }, { excludeST, minBars })
                if (!result?.trades?.length) { console.warn('[bt-dump] 无 trades'); return result }

                // 拼训练样本：[{code, name, features..., mlLabels...}, ...]
                const rows = result.trades
                    .filter(t => t.features && t.mlLabels)
                    .map(t => ({
                        code: t.code, name: t.name,
                        ...t.features,
                        ...t.mlLabels,
                    }))
                console.log(`[bt-dump] 准备落盘 ${rows.length} 条样本...`)
                try {
                    const saveRes = await api.saveMLDataset(rows, name)
                    if (saveRes?.ok) {
                        console.log(`[bt-dump] ✅ 已落盘: ${saveRes.data.filename} (${saveRes.data.rows} rows, ${(JSON.stringify(rows).length/1024).toFixed(0)}KB)`)
                        console.log(`[bt-dump]    路径: ${saveRes.data.path}`)
                        console.log(`[bt-dump]    下一步：python -m services.ml_signal_filter`)
                    } else {
                        console.error('[bt-dump] 落盘失败', saveRes)
                    }
                } catch (e) { console.error('[bt-dump] 落盘异常', e) }

                window.bt.lastDump = { rows, ...result }
                return result
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

            /**
             * Phase 0 V0：跨 detector 速验回测
             *
             *   await bt.runV0()                  // 默认沪深主板+中小，holdDays=7
             *   await bt.runV0({ holdDays: 14 })
             *
             * 三个 detector 并行跑：
             *   threeStage    用现有 backtestStock（多次历史 S3 突破）
             *   stretched     单只票 detect 一次（最近一个事件）
             *   dragonReturn  单只票 detect 一次（最近一个事件）
             *
             * 注意 stretched/dragon 是单事件 detector，每只票最多产生 1 笔 trade（vs
             * threeStage 多笔），样本量天然小一个量级。Phase 1 加历史滑窗才能精确对比。
             *
             * 输出 console.table 对比 win% / avgRet / 中位 / max / min。
             */
            /**
             * Phase 1 Day 2 实验 B：MA10 出场模式对比
             *
             *   await bt.runExitExperiment()                 // 默认 500 只抽样
             *   await bt.runExitExperiment({ maxStocks: 1000 })
             *
             * 4 种 exitMode 跑同一批股票，输出对比表：
             *   strict    | close < MA10 立即出（当前默认）
             *   buffered  | close < MA10 × 0.98 才出（2% 缓冲）
             *   confirmed | 连续 2 天跌破 MA10 才出
             *   momentum  | 跌破 MA10 且当天跌幅 > 2% 才出
             */
            async runExitExperiment(opts = {}) {
                const {
                    boards    = ['sh_main', 'sz_main', 'sme'],
                    holdDays  = 7,
                    excludeST = true,
                    minBars   = 250,
                    maxStocks = 500,
                } = opts

                if (_activeScanner?.scanning?.value) {
                    console.warn('[exitExp] 已有批量在跑，先 bt.cancel()')
                    return null
                }

                const codeRes = await api.listAllAShareCodes()
                if (!codeRes?.ok || !Array.isArray(codeRes.data)) {
                    console.error('[exitExp] 代码列表拉取失败', codeRes)
                    return null
                }
                const allowedPrefixes = boards.flatMap(b => BOARD_PREFIXES[b] || [])
                let stocks = codeRes.data
                    .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
                    .map(s => ({ code: s.code, name: s.name }))

                if (maxStocks && stocks.length > maxStocks) {
                    const shuffled = stocks.slice()
                    for (let i = shuffled.length - 1; i > 0; i--) {
                        const j = Math.floor(Math.random() * (i + 1))
                        ;[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
                    }
                    stocks = shuffled.slice(0, maxStocks)
                }

                console.log(`[exitExp] MA10 出场模式实验：${stocks.length} 只 × 4 模式，holdDays=${holdDays}`)

                const MODES = ['strict', 'buffered', 'confirmed', 'momentum']
                const tradesByMode = {}
                for (const m of MODES) tradesByMode[m] = []

                const scanner = useStockScanner({
                    excludeST, minBars,
                    fetchFn: _cachedKlineFetch,
                    batchSize: 10,
                    adaptiveGapThresholdMs: 250,
                })
                _activeScanner = scanner

                const t0 = Date.now()
                let lastReport = 0
                const reportTimer = setInterval(() => {
                    if (!scanner.scanning.value) return
                    const n = scanner.scanned.value
                    if (n - lastReport >= 50 || (n > 0 && n === scanner.total.value)) {
                        lastReport = n
                        const speed = (n / ((Date.now() - t0) / 1000)).toFixed(1)
                        console.log(`[exitExp] 进度 ${n}/${scanner.total.value} (${scanner.progressPct.value}%) · ${speed}/s · trades by mode: ${MODES.map(m => `${m}=${tradesByMode[m].length}`).join(' ')}`)
                    }
                }, 2000)

                try {
                    await scanner.scan(stocks, async (stock, klines) => {
                        // 关键优化：一份 klines 跑 4 次回测（不同 exitMode），避免 4 倍 IO
                        for (const mode of MODES) {
                            try {
                                const { trades } = backtestStock(klines, { holdDays, exitMode: mode })
                                for (const t of trades) {
                                    if (!t.truncated) tradesByMode[mode].push({ code: stock.code, ...t })
                                }
                            } catch { /* ignore */ }
                        }
                        return null
                    })
                } finally {
                    clearInterval(reportTimer)
                }

                const elapsed = ((Date.now() - t0) / 1000).toFixed(1)
                console.log(`[exitExp] 跑完 ${elapsed}s`)

                // 聚合 4 个 mode 的对比表
                const summary = {}
                for (const m of MODES) {
                    summary[m] = _summarizeV0(tradesByMode[m])
                }
                console.log(`[exitExp] === MA10 出场模式对比 (holdDays=${holdDays}, ${stocks.length} 只票) ===`)
                console.table(summary)

                // 每个 mode 的 timeRate（持满率）—— 这是核心指标，越高越说明 mode 让 trade 撑得住
                const exitRateTable = {}
                for (const m of MODES) {
                    const trades = tradesByMode[m]
                    if (!trades.length) { exitRateTable[m] = { count: 0 }; continue }
                    const time = trades.filter(t => t.exitReason === 'time').length
                    const exit = trades.filter(t => t.exitReason === 'exit').length
                    const invalid = trades.filter(t => t.exitReason === 'invalid').length
                    exitRateTable[m] = {
                        count: trades.length,
                        'time率': (time / trades.length * 100).toFixed(1) + '%',
                        'exit率': (exit / trades.length * 100).toFixed(1) + '%',
                        'invalid率': (invalid / trades.length * 100).toFixed(1) + '%',
                    }
                }
                console.log('[exitExp] === 出场原因分布（time率 越高 = mode 越宽松）===')
                console.table(exitRateTable)

                window.bt.lastExitExp = { tradesByMode, summary, exitRateTable }
                return { tradesByMode, summary, exitRateTable }
            },

            /**
             * Phase 1 Day 2 实验 A：threeStage detectOpts 参数网格搜索
             *
             *   await bt.gridSearch()                              // 精简：s2Vol × s3Vol = 16 组合
             *   await bt.gridSearch({ params: { ... } })           // 自定义网格
             *   await bt.gridSearch({ maxStocks: 1000 })           // 大样本
             *
             * 关键优化：每只票 K 线只拉一次，N 个参数组合在同一份 klines 上跑 N 次回测。
             *
             * 默认网格：s2VolRatioMin × s3VolRatioMax = 4 × 4 = 16 组合
             */
            async gridSearch(opts = {}) {
                const {
                    boards    = ['sh_main', 'sz_main', 'sme'],
                    holdDays  = 7,
                    excludeST = true,
                    minBars   = 250,
                    maxStocks = 500,
                    params    = {
                        s2VolRatioMin: [1.5, 2.0, 2.5, 3.0],   // 默认 2.5
                        s3VolRatioMax: [4.0, 5.0, 6.0, 7.0],   // 默认 6.0
                    },
                    minTradesForRank = 50,    // trade 数 < 此值的组合不参与 Top 排名（样本不足）
                } = opts

                if (_activeScanner?.scanning?.value) {
                    console.warn('[grid] 已有批量在跑，先 bt.cancel()')
                    return null
                }

                // 笛卡尔积展开所有组合
                const paramKeys = Object.keys(params)
                const combos = []
                function _expand(idx, current) {
                    if (idx === paramKeys.length) {
                        combos.push({ ...current })
                        return
                    }
                    const key = paramKeys[idx]
                    for (const val of params[key]) {
                        current[key] = val
                        _expand(idx + 1, current)
                    }
                }
                _expand(0, {})

                const codeRes = await api.listAllAShareCodes()
                if (!codeRes?.ok || !Array.isArray(codeRes.data)) {
                    console.error('[grid] 代码列表拉取失败', codeRes)
                    return null
                }
                const allowedPrefixes = boards.flatMap(b => BOARD_PREFIXES[b] || [])
                let stocks = codeRes.data
                    .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
                    .map(s => ({ code: s.code, name: s.name }))

                if (maxStocks && stocks.length > maxStocks) {
                    const shuffled = stocks.slice()
                    for (let i = shuffled.length - 1; i > 0; i--) {
                        const j = Math.floor(Math.random() * (i + 1))
                        ;[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
                    }
                    stocks = shuffled.slice(0, maxStocks)
                }

                console.log(`[grid] 参数网格搜索：${combos.length} 个组合 × ${stocks.length} 只票，holdDays=${holdDays}`)
                console.log(`[grid] 参数空间：`)
                for (const k of paramKeys) {
                    console.log(`  ${k}: [${params[k].join(', ')}]`)
                }

                const tradesByCombo = combos.map(() => [])

                const scanner = useStockScanner({
                    excludeST, minBars,
                    fetchFn: _cachedKlineFetch,
                    batchSize: 10,
                    adaptiveGapThresholdMs: 250,
                })
                _activeScanner = scanner

                const t0 = Date.now()
                let lastReport = 0
                const reportTimer = setInterval(() => {
                    if (!scanner.scanning.value) return
                    const n = scanner.scanned.value
                    if (n - lastReport >= 50 || (n > 0 && n === scanner.total.value)) {
                        lastReport = n
                        const speed = (n / ((Date.now() - t0) / 1000)).toFixed(1)
                        const totalTrades = tradesByCombo.reduce((s, arr) => s + arr.length, 0)
                        console.log(`[grid] 进度 ${n}/${scanner.total.value} (${scanner.progressPct.value}%) · ${speed}/s · 累计 ${totalTrades} trades`)
                    }
                }, 2000)

                try {
                    await scanner.scan(stocks, async (stock, klines) => {
                        // 同一份 klines 跑 N 个 detectOpts 组合（避免 N 倍 IO）
                        for (let i = 0; i < combos.length; i++) {
                            const detectOpts = combos[i]
                            try {
                                const { trades } = backtestStock(klines, { holdDays, detectOpts })
                                for (const t of trades) {
                                    if (!t.truncated) tradesByCombo[i].push({ code: stock.code, ...t })
                                }
                            } catch { /* ignore */ }
                        }
                        return null
                    })
                } finally {
                    clearInterval(reportTimer)
                }

                const elapsed = ((Date.now() - t0) / 1000).toFixed(1)
                console.log(`[grid] 跑完 ${elapsed}s`)

                // 聚合 + 按胜率排序
                const summary = combos.map((c, i) => {
                    const sum = _summarizeV0(tradesByCombo[i])
                    const comboLabel = paramKeys.map(k => `${k}=${c[k]}`).join(' / ')
                    return { combo: comboLabel, ...sum }
                })

                // 全部组合（按胜率降序）
                const sortedByWin = summary
                    .filter(s => s.trades >= minTradesForRank)
                    .map(s => ({
                        combo: s.combo,
                        trades: s.trades,
                        'win%': s['win%'],
                        avgRet: s.avgRet,
                        median: s.median,
                    }))
                    .sort((a, b) => parseFloat(b['win%']) - parseFloat(a['win%']))

                console.log(`[grid] === 全部 ${summary.length} 组合（trade ≥ ${minTradesForRank} 才参与排名）===`)
                console.table(sortedByWin)

                // Top 3
                console.log(`[grid] 🏆 Top 3 胜率组合：`)
                console.table(sortedByWin.slice(0, 3))

                // 找出当前生产默认配置在网格里的位置（精确匹配 4 参数）
                // 默认：s2VolRatioMin=2.5, s3VolRatioMax=6.0, trendMinRatio=1.5, s2ShadowRatio=2.0
                // 注意是用 indexOf 找子串而不是正则，避免 s2=2.5 误匹配 s2=2.55 这种
                function _matchExact(combo, key, val) {
                    return combo.includes(`${key}=${val}`) || combo.includes(`${key}=${val}.`)
                        ? combo.match(new RegExp(`${key.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}=([\\\\d.]+)`))?.[1] === String(val)
                        : false
                }
                // 简单粗暴版：直接构造期望字符串
                const expectedDefaultCombo = 's2VolRatioMin=2.5 / s3VolRatioMax=6 / trendMinRatio=1.5 / s2ShadowRatio=2'
                const defaultCombo = summary.find(s => s.combo === expectedDefaultCombo)
                // 万一格式不对（数值表示差异），fallback 用 includes 多关键字精确匹配
                const defaultComboFallback = defaultCombo || summary.find(s =>
                    s.combo.includes('s2VolRatioMin=2.5')
                    && s.combo.includes('s3VolRatioMax=6 ')   // 加空格防止匹配 6.5 / 60
                    && s.combo.includes('trendMinRatio=1.5')
                    && s.combo.includes('s2ShadowRatio=2')
                )
                const targetCombo = defaultComboFallback || defaultCombo
                if (targetCombo) {
                    // 找它在 sortedByWin 中的排名
                    const rank = sortedByWin.findIndex(s => s.combo === targetCombo.combo)
                    console.log(`[grid] 📍 当前生产默认 (s2=2.5/s3=6/trend=1.5/shadow=2) 排名 #${rank + 1}/${sortedByWin.length}:`)
                    console.table([{
                        combo: targetCombo.combo,
                        trades: targetCombo.trades,
                        'win%': targetCombo['win%'],
                        avgRet: targetCombo.avgRet,
                        median: targetCombo.median,
                    }])
                }

                window.bt.lastGridSearch = { combos, tradesByCombo, summary, sortedByWin }

                // Phase 1 Day 3 自动存盘：网格结果入库
                try {
                    const top10 = sortedByWin.slice(0, 10)
                    const saveRes = await api.saveBacktestRun({
                        run_type:      'gridSearch',
                        sample_size:   stocks.length,
                        hold_days:     holdDays,
                        boards:        boards,
                        detector_opts: { params },
                        summary:       { combos: summary, sortedByWin },
                        top_combos:    top10,
                        notes:         '',
                    })
                    if (saveRes?.ok) console.log(`[grid] 💾 已保存 run #${saveRes.data}（Backtest 模块可查）`)
                } catch (e) { console.warn('[grid] 保存 run 失败（不影响结果）', e) }

                return { combos, summary, sortedByWin }
            },

            async runV0(opts = {}) {
                const {
                    boards     = ['sh_main', 'sz_main', 'sme'],
                    holdDays   = 7,
                    excludeST  = true,
                    minBars    = 250,
                    maxStocks  = null,         // 抽样上限：传 500 = 随机抽 500 只，加速测试
                    fastMode   = true,         // 默认开：粗步长 + 大 stride（牺牲精度换速度）
                    // 质量过滤器（默认关）—— 开启后 stretched 信号数变少，胜率应该上升
                    stretchedFilters = {},     // { requireMA250Uptrend, strictBreakoutVol }
                } = opts

                if (_activeScanner?.scanning?.value) {
                    console.warn('[v0] 已有批量在跑，先 bt.cancel()')
                    return null
                }

                // 拉全市场代码 + 板块过滤
                const codeRes = await api.listAllAShareCodes()
                if (!codeRes?.ok || !Array.isArray(codeRes.data)) {
                    console.error('[v0] 代码列表拉取失败', codeRes)
                    return null
                }
                const allowedPrefixes = boards.flatMap(b => BOARD_PREFIXES[b] || [])
                let stocks = codeRes.data
                    .filter(s => allowedPrefixes.some(p => s.code.startsWith(p)))
                    .map(s => ({ code: s.code, name: s.name }))

                // 抽样（用 Fisher-Yates，洗牌后取前 N 只）
                if (maxStocks && stocks.length > maxStocks) {
                    const shuffled = stocks.slice()
                    for (let i = shuffled.length - 1; i > 0; i--) {
                        const j = Math.floor(Math.random() * (i + 1))
                        ;[shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]]
                    }
                    stocks = shuffled.slice(0, maxStocks)
                    console.log(`[v0] 随机抽样 ${stocks.length} 只`)
                }

                console.log(`[v0] 跨 detector 回测：${stocks.length} 只 × 3 detector，holdDays=${holdDays}, fastMode=${fastMode}`)

                const scanner = useStockScanner({
                    excludeST, minBars,
                    fetchFn: _cachedKlineFetch,
                    batchSize: 10,
                    adaptiveGapThresholdMs: 250,
                })
                _activeScanner = scanner

                const tradesByDetector = {
                    threeStage:   [],
                    stretched:    [],
                    dragonReturn: [],
                }
                const t0 = Date.now()
                let lastReport = 0
                const reportTimer = setInterval(() => {
                    if (!scanner.scanning.value) return
                    const n = scanner.scanned.value
                    if (n - lastReport >= 50 || (n > 0 && n === scanner.total.value)) {
                        lastReport = n
                        const speed = (n / ((Date.now() - t0) / 1000)).toFixed(1)
                        console.log(`[v0] 进度 ${n}/${scanner.total.value} (${scanner.progressPct.value}%) · ${speed}/s · 3stage=${tradesByDetector.threeStage.length} · stretched=${tradesByDetector.stretched.length} · dragon=${tradesByDetector.dragonReturn.length}`)
                    }
                }, 2000)

                try {
                    await scanner.scan(stocks, async (stock, klines) => {
                        // 1) threeStage —— 复用现有 backtestStock（detector 天然返回多笔历史 S3）
                        try {
                            const { trades } = backtestStock(klines, { holdDays })
                            for (const t of trades) {
                                if (!t.truncated) tradesByDetector.threeStage.push({ code: stock.code, ...t })
                            }
                        } catch { /* ignore */ }

                        // fastMode 加速参数：stride 加大 + consolidationStep 加大
                        const stretchedStride = fastMode ? 30 : 15
                        const dragonStride    = fastMode ? 15 : 5
                        const cStep           = fastMode ? 15 : 5

                        // 2) stretched —— 历史滑窗扫，传 consolidationStep 加速 detector 内部
                        // 可选注入质量过滤器（MA250 / strictVol 等），让用户对比 filtered vs raw
                        try {
                            const events = _scanHistorically(
                                klines,
                                (k, o) => detectStretchedRally(k, {
                                    ...o,
                                    consolidationStep: cStep,
                                    ...stretchedFilters,
                                }),
                                e => e.breakIdx,
                                { stride: stretchedStride, startAt: 100, holdDays },
                            )
                            for (const e of events) {
                                const trade = _simulateGenericTrade(klines, e.breakIdx, e.stopLossPrice, holdDays)
                                if (trade && !trade.truncated) {
                                    tradesByDetector.stretched.push({ code: stock.code, ...trade })
                                }
                            }
                        } catch { /* ignore */ }

                        // 3) dragonReturn
                        try {
                            const events = _scanHistorically(
                                klines, detectDragonReturn, e => e.ignitionIdx,
                                { stride: dragonStride, startAt: 100, holdDays },
                            )
                            for (const e of events) {
                                const trade = _simulateGenericTrade(klines, e.ignitionIdx, e.stopLoss, holdDays)
                                if (trade && !trade.truncated) {
                                    tradesByDetector.dragonReturn.push({ code: stock.code, ...trade })
                                }
                            }
                        } catch { /* ignore */ }

                        return null
                    })
                } finally {
                    clearInterval(reportTimer)
                }

                const elapsed = ((Date.now() - t0) / 1000).toFixed(1)
                console.log(`[v0] 跑完 ${elapsed}s`)

                // 聚合每个 detector 的统计
                const summary = {}
                for (const [name, trades] of Object.entries(tradesByDetector)) {
                    summary[name] = _summarizeV0(trades)
                }
                console.log(`[v0] === 跨 detector 对比 (holdDays=${holdDays}) ===`)
                console.table(summary)
                console.log('[v0] 三个 detector 都已用历史滑窗扫描，样本时段公平对齐。')

                window.bt.lastV0 = { tradesByDetector, summary }
                return { tradesByDetector, summary }
            },
        }
        console.log('[bt] 回测调试入口已注册：bt.run / bt.runMany / bt.runAll / bt.runV0 / bt.runExitExperiment / bt.gridSearch / bt.cancel / bt.cacheStats / bt.clearCache')
    })
}
