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
  /** 首页概览：指数 + 全市场成交额 + 热门板块 */
  getMarketData: () => call('get_market_data'),

  /** K 线 / 分时。timeframe: '分时'|'5日'|'日K'|'周K'|'月K'|'年K' */
  getKline: (name, timeframe) => call('get_kline', name, timeframe),

  /** 板块联动个股。plateId: KPL 板块代码如 '801001' */
  getSectorStocks: (plateId) => call('get_sector_stocks', plateId),

  /** AI 复盘问答 */
  analyzeMarketQuery: (query) => call('analyze_market_query', query),

  /** 强制清缓存并重新拉取概览 */
  refreshCache: () => call('refresh_cache'),

  /** 后端心跳探测 */
  getSystemStatus: () => call('get_system_status'),
}
