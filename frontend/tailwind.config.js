/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      // 「摸鱼模式」断点 —— 跟 src/composables/useViewport.js 的 COMPACT_BREAKPOINT 保持一致
      // 用法：`compact:hidden`（≥800 隐藏）/ `compact:flex-row`（≥800 行排）等
      // 修改这里时，记得同步改 useViewport.js
      screens: {
        compact: '800px',
      },
    },
  },
  plugins: [],
}
