import { onMounted, onUnmounted } from 'vue'

/**
 * 给一个 API 调用函数加"挂载立即拉一次 + 定时自动刷新"的能力。
 *
 * 不同数据有不同刷新频率需求（指数 60s、K 线仅在切换时、板块榜 60s...），
 * 避免组件各自写一套 setInterval + onUnmounted 清理。
 *
 * 使用示例：
 *   const { refresh, loading, error } = useAutoRefresh(api.getMarketData, {
 *     interval: 60_000,
 *     onData: (data) => {
 *       marketIndices.value = data.indices
 *       hotSectors.value = data.hotSectors
 *     },
 *     onError: (err) => console.error('行情拉取失败:', err)
 *   })
 *
 * @param {Function} fn           返回 { ok, data, error, code } 的 API 调用函数
 * @param {Object}   options
 * @param {number}   options.interval    自动刷新间隔毫秒，默认 60_000。传 0 关闭定时
 * @param {Function} options.onData      成功回调 (data) => void
 * @param {Function} options.onError     失败回调 (errorMsg, errorCode) => void
 * @param {boolean}  options.immediate   挂载时是否立即拉一次，默认 true
 *
 * @returns {{ refresh: () => Promise<void> }}  refresh() 可手动触发一次刷新
 */
export function useAutoRefresh(fn, {
  interval = 60_000,
  onData,
  onError,
  immediate = true,
} = {}) {
  let timer = null

  async function run() {
    const res = await fn()
    if (res && res.ok) {
      onData && onData(res.data)
    } else {
      onError && onError(res && res.error, res && res.code)
    }
  }

  onMounted(() => {
    if (immediate) run()
    if (interval > 0) {
      timer = setInterval(run, interval)
    }
  })

  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { refresh: run }
}
