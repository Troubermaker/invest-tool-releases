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

  /** K 线 / 分时。timeframe: '分时'|'5日'|'日K'|'周K'|'月K'|'年K' */
  getKline: (name, timeframe) => call('get_kline', name, timeframe),

  /** 板块联动个股。plateId: KPL 板块代码；date='YYYY-MM-DD' 切历史 */
  getSectorStocks: (plateId, date = null) => call('get_sector_stocks', plateId, date),

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

  /** 股票搜索（A 股，支持代码/中文名/拼音）*/
  searchStocks: (query, limit) => call('search_stocks', query, limit ?? 20),
  /** 从分组移除股票 */
  removeWatchlistStock: (groupId, code) => call('remove_watchlist_stock', groupId, code),
  /** 重排分组内股票，参数 [code1, code2, ...] */
  reorderWatchlistStocks: (groupId, orderedCodes) => call('reorder_watchlist_stocks', groupId, orderedCodes),

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
