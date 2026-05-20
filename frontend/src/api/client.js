/**
 * 前端 API 客户端。
 *
 * 所有后端调用统一从这里走：
 * - 自动等待 pywebview 桥接就绪，组件不用再自己判断 window.pywebview 存不存在
 * - 接收后端 { ok, data, error, code } 信封并透传
 * - 桥接层异常也标准化成同一种信封格式，前端错误处理只有一种分支
 *
 * 新增后端接口的标准流程：
 *   1. 后端 api.py 加 @api_endpoint 方法
 *   2. 本文件 api object 里加一行包装器
 *   3. 组件里 `const res = await api.xxx(); if (res.ok) {...}` 即可使用
 */

// ---------------- 基础设施 ---------------- //

let _readyPromise = null

/** 返回一个 Promise，在 pywebview 桥接就绪后 resolve。可反复调用，不会重复监听。 */
function waitForPywebview() {
  if (_readyPromise) return _readyPromise
  _readyPromise = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) {
      resolve()
      return
    }
    window.addEventListener('pywebviewready', () => resolve(), { once: true })
  })
  return _readyPromise
}

/**
 * 调用某个后端方法。返回统一信封 { ok, data, error, code }。
 *
 * - ok=true  → 业务成功，data 为 service 返回值
 * - ok=false → 任何失败（方法不存在 / 桥接异常 / 后端抛错 / 数据源超时）
 */
export async function call(method, ...args) {
  await waitForPywebview()

  const fn = window.pywebview && window.pywebview.api && window.pywebview.api[method]
  if (typeof fn !== 'function') {
    return { ok: false, error: `后端方法不存在: ${method}`, code: 'NO_METHOD' }
  }

  try {
    const res = await fn(...args)
    // 后端标准信封
    if (res && typeof res === 'object' && 'ok' in res) {
      return res
    }
    // 兜底：老接口或例外情况返回了裸数据
    return { ok: true, data: res }
  } catch (e) {
    return { ok: false, error: String(e), code: 'BRIDGE_ERROR' }
  }
}

/** `pywebview` 当前是否已就绪（同步探测，用于首屏渲染时的 SSR 兜底判断）。 */
export function isPywebviewReady() {
  return !!(window.pywebview && window.pywebview.api)
}


// ---------------- 业务接口声明 ---------------- //
//
// 所有后端可调方法集中在此。新增接口只需加一行。
// 命名约定：后端 snake_case → 前端 camelCase。

export const api = {
  /** 首页概览。date='YYYY-MM-DD' 时返回历史数据（indices 会变成 null，前端需隐藏指数面板）*/
  getMarketData: (date = null) => call('get_market_data', date),

  /** 指数 K 线 / 分时。timeframe: '分时'|'5日'|'日K'|'周K'|'月K'|'年K' */
  getKline: (name, timeframe) => call('get_kline', name, timeframe),

  /** 个股 K 线 / 分时。code = 6 位股票代码 */
  getStockKline: (code, timeframe) => call('get_stock_kline', code, timeframe),
  /** 管理员专用：通达信源 K 线（批量扫描用，无反爬）。count 默认 800，超过自动分页 */
  getStockKlineViaTdx: (code, timeframe = '日K', count = 800) =>
    call('get_stock_kline_via_tdx', code, timeframe, count),
  /** 管理员专用：通达信源指数 K 线（沪深 300 / 上证指数 等）*/
  getIndexKlineViaTdx: (name, timeframe = '日K', count = 300) =>
    call('get_index_kline_via_tdx', name, timeframe, count),
  /** 管理员专用：TDX 连接是否可用 */
  tdxIsAvailable: () => call('tdx_is_available'),
  /** 管理员专用：全市场 A 股代码列表（约 5000 条），给批量回测 / 扫描用 */
  listAllAShareCodes: () => call('list_all_a_share_codes'),
  /** 管理员专用：带 SQLite 持久化缓存的 TDX K线（仅回测用，每日刷新，跨重启留存）*/
  getStockKlineViaTdxCached: (code, timeframe = '日K', count = 800) =>
    call('get_stock_kline_via_tdx_cached', code, timeframe, count),
  /** 管理员专用：K线缓存统计 */
  klineCacheStats: () => call('kline_cache_stats'),
  /** 管理员专用：清空 K线缓存表 */
  klineCacheClear: () => call('kline_cache_clear'),
  /** 管理员专用：批量预检缓存 freshness（cached / missing 分组），给"下载今日 K 线"用 */
  bulkCheckKlineFreshness: (codes, timeframe = '日K') =>
    call('bulk_check_kline_freshness', codes || [], timeframe),

  /** 板块联动个股。plateId: KPL 板块代码；date='YYYY-MM-DD' 切历史 */
  getSectorStocks: (plateId, date = null) => call('get_sector_stocks', plateId, date),

  /** 全量板块榜（默认 80 个），热力图用 */
  getAllSectors: (limit = 80) => call('get_all_sectors', limit),

  /** 连板天梯。date='YYYY-MM-DD' 切历史。返全量数据，ST 由前端按需过滤显示 */
  getLimitUpLadder: (date = null) => call('get_limit_up_ladder', date),

  /** 市场情绪。date 传了返回 null（THS 该接口无历史版）*/
  getMarketSentiment: (date = null) => call('get_market_sentiment', date),

  /** 同花顺热榜。period: 'hour' (1 小时) | 'day' (24 小时) */
  getThsHotList: (period = 'hour') => call('get_ths_hot_list', period),

  /** 快讯。source: 'ths' (同花顺) | 'em' (东方财富) */
  getFastNews: (source = 'ths') => call('get_fast_news', source),

  /** 涨跌池聚合：连板/涨停/炸板/冲刺涨停/跌停 5 个池子。date='YYYY-MM-DD' 切历史 */
  getLimitPools: (date = null) => call('get_limit_pools', date),
  /** 单池查询（按需加载）。poolKey: continuous|limitUp|broken|sprint|limitDown */
  getLimitPool: (poolKey, date = null) => call('get_limit_pool', poolKey, date),

  /** A 股交易日列表（'YYYY-MM-DD' 字符串数组），区间默认近两年到今天 */
  getTradingDays: (startDate = null, endDate = null) => call('get_trading_days', startDate, endDate),

  /** AI 复盘问答 */
  analyzeMarketQuery: (query) => call('analyze_market_query', query),

  /** 强制清缓存并重新拉取概览 */
  refreshCache: () => call('refresh_cache'),

  /** 后端心跳探测 */
  getSystemStatus: () => call('get_system_status'),

  // -------- 激活 / 授权（未激活时也能调用）--------
  /** 是否已激活。返回 boolean */
  isActivated: () => call('is_activated'),
  /** 提交激活码。成功返回 true，失败返回 false */
  activateLicense: (code) => call('activate_license', code),

  // -------- 管理员模式（基于已激活之上的权限分级）--------
  /** 是否处于管理员模式。返回 boolean */
  isAdmin: () => call('is_admin'),
  /** 输入管理员密码升级。成功返回 true */
  unlockAdmin: (password) => call('unlock_admin', password),
  /** 退出管理员模式 */
  disableAdmin: () => call('disable_admin'),

  // -------- 在线更新 --------
  /** 检查更新：联网拉 latest.json 比版本。返回 {has_update, latest_version, ...} */
  checkUpdate: () => call('check_update'),
  /** 启动后台下载。前端轮询 getUpdateProgress() 看进度 */
  startUpdateDownload: (downloadUrl, expectedSha256, totalBytes = 0) =>
    call('start_update_download', downloadUrl, expectedSha256, totalBytes),
  /** 轮询下载状态：{phase, downloaded_bytes, total_bytes, error} */
  getUpdateProgress: () => call('get_update_progress'),
  /** 取消下载 */
  cancelUpdateDownload: () => call('cancel_update_download'),
  /** 应用更新（写 updater.bat、退出进程）。成功的话本调用不会返回 */
  applyUpdate: () => call('apply_update'),
  /** 获取当前版本 + 数据库路径，给 Settings 显示用 */
  getAppVersion: () => call('get_app_version'),
  /** 打开数据目录（资源管理器） */
  openDataDirectory: () => call('open_data_directory'),
  /** 选择新的数据目录（原生文件夹选择对话框）。返回 {cancelled, path} */
  pickDataDirectory: () => call('pick_data_directory'),
  /** 切换数据目录。migrate=true 会把当前 invest_data.db 拷到新目录 */
  changeDataDirectory: (newPath, migrate = true) =>
    call('change_data_directory', newPath, migrate),
  /** 恢复默认数据目录（%APPDATA%\InvestTool\）*/
  resetDataDirectory: () => call('reset_data_directory'),
  /** 退出当前进程（用户手动重启） */
  restartApp: () => call('restart_app'),

  // -------- 自选股 --------
  /** 所有分组（按顺序）*/
  getWatchlistGroups: () => call('get_watchlist_groups'),
  /** 新建分组，返回 { id, name } */
  createWatchlistGroup: (name) => call('create_watchlist_group', name),
  /** 重命名分组 */
  renameWatchlistGroup: (groupId, newName) => call('rename_watchlist_group', groupId, newName),
  /** 删除分组（级联删除组内股票）*/
  deleteWatchlistGroup: (groupId) => call('delete_watchlist_group', groupId),
  /** 重排分组，参数 [id1, id2, ...] */
  reorderWatchlistGroups: (orderedIds) => call('reorder_watchlist_groups', orderedIds),

  /** 某分组下的股票列表 */
  getWatchlistStocks: (groupId) => call('get_watchlist_stocks', groupId),
  /** 全部自选（虚拟分组，去重合并）*/
  getAllWatchlistStocks: () => call('get_all_watchlist_stocks'),
  /** 添加股票到分组 */
  addWatchlistStock: (groupId, code, name, addedPrice, remark) =>
    call('add_watchlist_stock', groupId, code, name || '', addedPrice ?? null, remark || ''),
  /** 编辑股票信息（仅更新传入的字段）。updates 支持 { name, addedPrice, remark, addedAt } */
  updateWatchlistStock: (groupId, code, updates) =>
    call('update_watchlist_stock', groupId, code,
         updates.name ?? null, updates.addedPrice ?? null,
         updates.remark ?? null, updates.addedAt ?? null),

  /** 批量导入：解析文本，返回识别出的股票列表 */
  importParseText: (text) => call('import_parse_text', text || ''),
  /** 批量入库：把识别出的 [{code, name}] 批量加入指定分组 */
  importBatchAdd: (groupId, stocks) => call('import_batch_add', groupId, stocks || []),

  /** ---- 候选池（找发车 / 找候选 收藏后持续追踪买点）---- */
  /** 列出全部候选池条目（按入选时间倒序）*/
  listCandidatePicks: () => call('list_candidate_picks'),
  /** 把扫描结果存入候选池。payload 见 api.py 注释 */
  addCandidatePick: (payload) => call('add_candidate_pick', payload || {}),
  /** 从候选池删一只票 */
  removeCandidatePick: (code) => call('remove_candidate_pick', code),
  /** 改备注 */
  updateCandidateNote: (code, note) => call('update_candidate_note', code, note || ''),
  /** 清空整个候选池（危险）*/
  clearCandidatePicks: () => call('clear_candidate_picks'),
  /** Phase 5：更新单条候选池的追踪字段（peak_gain_since_save / formation_state）*/
  updateCandidateTracking: (payload) => call('update_candidate_tracking', payload || {}),
  /** Phase 5：批量更新追踪字段（候选池刷新一次性写回所有票）*/
  bulkUpdateCandidateTracking: (payloads) => call('bulk_update_candidate_tracking', payloads || []),

  /** ---- 回测持久化（Phase 1 Day 3）---- */
  saveBacktestRun:    (payload) => call('save_backtest_run', payload || {}),
  updateBacktestRunArtifacts: (runId, producedDataset, producedModel) =>
                                   call('update_backtest_run_artifacts', runId, producedDataset || null, producedModel || null),
  listBacktestRuns:   (limit)   => call('list_backtest_runs', limit ?? 50),
  getBacktestRun:     (runId)   => call('get_backtest_run', runId),
  updateBacktestNotes:(runId, notes) => call('update_backtest_notes', runId, notes || ''),
  deleteBacktestRun:  (runId, deleteFiles) => call('delete_backtest_run', runId, !!deleteFiles),

  /** ---- ML 训练数据落盘（Phase 2 Step 1）---- */
  saveMLDataset:      (rows, name) => call('save_ml_dataset', { rows, name }),
  listMLDatasets:     ()           => call('list_ml_datasets'),

  /** ---- ML 在线预测（Phase 3 Step 2）---- */
  mlPredictStatus:    ()           => call('ml_predict_status'),
  mlPredictScore:     (features)   => call('ml_predict_score', features),
  mlPredictScores:    (featuresList) => call('ml_predict_scores', featuresList),

  /** ---- 板块强度（Week 1 Day 3-5）---- */
  getSectorStrengths:           (force) => call('get_sector_strengths', !!force),
  getStockSectorStrength:       (code)  => call('get_stock_sector_strength', code),
  getBatchStockSectorStrengths: (codes) => call('get_batch_stock_sector_strengths', codes || []),

  /** ---- P2 龙虎榜 ---- */
  refreshLhbData:       (days, force)            => call('refresh_lhb_data', days ?? 90, !!force),
  getStockLhbHistory:   (code, lookbackDays)     => call('get_stock_lhb_history', code, lookbackDays ?? 90),
  getLhbFeatures:       (code, eventDate, win)   => call('get_lhb_features', code, eventDate || null, win ?? 30),
  getBatchLhbFeatures:  (codes, eventDate, win)  => call('get_batch_lhb_features', codes || [], eventDate || null, win ?? 30),
  lhbStats:             ()                       => call('lhb_stats'),

  /** ---- 每日自动扫描 + 桌面推送 ---- */
  saveAutoScanResult:   (payload)                => call('save_auto_scan_result', payload || {}),
  getTodayAutoScan:     ()                       => call('get_today_auto_scan'),
  getAutoScanByDate:    (date)                   => call('get_auto_scan_by_date', date),
  listRecentAutoScans:  (limit)                  => call('list_recent_auto_scans', limit ?? 30),
  sendDesktopNotification: (title, msg, timeout) => call('send_desktop_notification', title, msg, timeout ?? 10),

  /** ---- ML 健康监控 (P2) ---- */
  mlHealthOverview:     ()                       => call('ml_health_overview'),
  mlHealthCurrentModel: ()                       => call('ml_health_current_model'),
  mlHealthListModels:   ()                       => call('ml_health_list_models'),
  mlHealthListDatasets: ()                       => call('ml_health_list_datasets'),
  mlHealthComputeIc:    (filename, label)        => call('ml_health_compute_ic', filename, label || 't7Return'),
  mlHealthIcTrend:      (n)                      => call('ml_health_ic_trend', n ?? 5),
  /** 所有模型 × 最新（或指定）数据集 IC 矩阵，可按范围切片 */
  mlHealthListModelsWithIc: (dataset, maxModels, labelField, signalSource, boards) =>
    call('ml_health_list_models_with_ic',
         dataset || null,
         maxModels ?? 10,
         labelField || 't7Return',
         signalSource || null,
         boards || null),
  /** 切换激活模型（指定 .pkl 路径）*/
  mlSetActiveModel:     (path)                   => call('ml_set_active_model', path),
  /** 删除模型 .pkl + sidecar JSON（激活中的模型禁止删，先切走）*/
  mlDeleteModel:        (path)                   => call('ml_delete_model', path),
  /** 删除 dataset NDJSON */
  mlDeleteDataset:      (filename)               => call('ml_delete_dataset', filename),
  /** 用指定 dataset 重训模型（同步阻塞，30s-3min）*/
  mlRetrain:            (datasetFiles, label)    => call('ml_retrain', datasetFiles || null, label || 't7Profitable'),

  /** ---- P0 交易日志 + 仓位管理 ---- */
  addTradeJournal:        (payload) => call('add_trade_journal', payload || {}),
  closeTradeJournal:      (payload) => call('close_trade_journal', payload || {}),
  updateTradeJournal:     (payload) => call('update_trade_journal', payload || {}),
  deleteTradeJournal:     (tradeId) => call('delete_trade_journal', tradeId),
  listTradeJournal:       (status, signalSource, limit) =>
                            call('list_trade_journal', status || 'all', signalSource || null, limit || 200),
  tradeJournalSummary:    (periodDays) => call('trade_journal_summary', periodDays || 30),
  suggestTradePosition:   (starLevel, totalOpen, sectorOpenPct) =>
                            call('suggest_trade_position', starLevel || 0, totalOpen || 0, sectorOpenPct || 0),

  /** 股票搜索（A 股，支持代码/中文名/拼音）*/
  searchStocks: (query, limit) => call('search_stocks', query, limit ?? 20),
  /** 从分组移除股票 */
  removeWatchlistStock: (groupId, code) => call('remove_watchlist_stock', groupId, code),
  /** 批量移除：codes 数组 */
  removeWatchlistStocksBatch: (groupId, codes) =>
    call('remove_watchlist_stocks_batch', groupId, codes || []),
  /** 重排分组内股票，参数 [code1, code2, ...] */
  reorderWatchlistStocks: (groupId, orderedCodes) => call('reorder_watchlist_stocks', groupId, orderedCodes),

  // -------- 价格警报 --------
  /** 设警报。above/below 至少一个非空；都传 null 等于清除 */
  setStockAlert: (code, above = null, below = null) => call('set_stock_alert', code, above, below),
  /** 清警报（等同 setStockAlert(code, null, null)） */
  clearStockAlert: (code) => call('clear_stock_alert', code),
  /** 读警报阈值。返回 null 或 {alert_above, alert_below} */
  getStockAlert: (code) => call('get_stock_alert', code),
  /** 轮询：拿未展示过的触发记录 */
  getPendingAlerts: (limit = 20) => call('get_pending_alerts', limit),
  /** 标记 ack（前端展示后调）*/
  ackAlerts: (ids) => call('ack_alerts', ids || []),
  /** 立即检查警报（绕过 scheduler，用于测试）*/
  forceCheckAlerts: () => call('force_check_alerts'),

  // -------- 持仓 Portfolio --------
  /** 所有持仓账户（按顺序，含 count）*/
  getPortfolioAccounts: () => call('get_portfolio_accounts'),
  createPortfolioAccount: (name) => call('create_portfolio_account', name),
  renamePortfolioAccount: (accountId, newName) => call('rename_portfolio_account', accountId, newName),
  deletePortfolioAccount: (accountId) => call('delete_portfolio_account', accountId),
  reorderPortfolioAccounts: (orderedIds) => call('reorder_portfolio_accounts', orderedIds),

  /** 某账户下的持仓（不含行情）*/
  getPortfolioPositions: (accountId) => call('get_portfolio_positions', accountId),
  addPortfolioPosition: (accountId, code, name, shares, costPrice, remark) =>
    call('add_portfolio_position', accountId, code, name || '', shares, costPrice, remark || ''),
  /** 编辑持仓，updates 支持 { name, shares, costPrice, remark, addedAt } */
  updatePortfolioPosition: (accountId, code, updates) =>
    call('update_portfolio_position', accountId, code,
         updates.name ?? null, updates.shares ?? null,
         updates.costPrice ?? null, updates.remark ?? null, updates.addedAt ?? null),
  removePortfolioPosition: (accountId, code) => call('remove_portfolio_position', accountId, code),
  reorderPortfolioPositions: (accountId, orderedCodes) =>
    call('reorder_portfolio_positions', accountId, orderedCodes),
  /** 跨账户合并（汇总 tab 用）*/
  getPortfolioMerged: () => call('get_portfolio_merged'),

  // -------- 用户偏好 --------
  getUserPreference: (key, defaultVal) => call('get_user_preference', key, defaultVal ?? null),
  setUserPreference: (key, value) => call('set_user_preference', key, value),

  // -------- 老板键 --------
  getBossKey: () => call('get_boss_key'),
  setBossKey: (hotkey) => call('set_boss_key', hotkey),

  // -------- 数据备份（跨机器迁移）--------
  /** 弹原生"另存为"对话框并写入 JSON。sections: ['watchlist','portfolio','preferences'] 的子集，null=全部 */
  exportUserDataInteractive: (sections) => call('export_user_data_interactive', sections ?? null),
  /** 弹原生"打开"对话框，读取并预览备份文件，返回 {cancelled, path, counts, exported_at, schema_version} */
  pickBackupFile: () => call('pick_backup_file'),
  /** 按路径导入指定备份文件。mode 默认 'replace' */
  importBackupFile: (path, mode) => call('import_backup_file', path, mode ?? 'replace'),

  // -------- 批量行情 --------
  /** 给一批股票代码，返回 {code: {price, changePct, turnoverRate, volRatio, marketCap, industry, ...}} */
  getBatchQuotes: (codes) => call('get_batch_quotes', codes || []),

  /** 批量分时 sparkline 数据，返回 {code: {preClose, prices: [...]}} */
  getBatchSparklines: (codes) => call('get_batch_sparklines', codes || []),
}
