/**
 * 刷新间隔集中配置 —— 开发期调参的"唯一真相源"。
 *
 * 改这里一处，自选 / 持仓 / 全局 K 线抽屉的轮询节奏全部跟着变。
 * 不暴露给最终用户，避免有人手贱设 1s 把 EM 的 IP 拉黑。
 *
 * 单位：毫秒。值越小越实时，但也越容易被 EM 反爬识别。
 */

// 自选 + 持仓 + 候选池的实时行情刷新（前台、盘中含集合竞价）。
// EM push2delay.ulist.np 单次批量请求。推荐区间：2_000 – 10_000；< 2s 容易触发风控。
//
// ⚠️ 跟后端 services/quote_service.py 的 QUOTE_TTL_TRADING_SEC 强相关：
//    后端 TTL（秒）≤ 这里（毫秒）/ 1000，否则前端 poll 命中老缓存，集合竞价行情滞后。
//    两者一致最好（每次 poll 都重新走 EM）。改一处必须同步改另一处。
export const QUOTE_INTERVAL_ACTIVE = 3_000

// 窗口最小化 / 切到其他 tab 时，行情刷新降到这个间隔（含 deep-hidden）。
// 后台不该高频打接口；推荐 30_000 – 120_000
export const QUOTE_INTERVAL_HIDDEN = 60_000

// 自选 + 持仓的迷你分时图（sparkline）刷新（前台、盘中）。
// 分时数据变化慢，无须高频；隐藏态走 useSmartRefresh 默认倍率（6× / 18×）
export const SPARKLINE_INTERVAL_ACTIVE = 60_000
