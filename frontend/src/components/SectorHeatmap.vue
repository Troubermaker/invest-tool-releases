<script setup>
/**
 * 板块热力图（基于 ECharts treemap）。
 *
 * 视觉：
 *   - 每个方块 = 一个板块
 *   - 大小：按"强度"（KPL 给的 strength 值，反映资金活跃度）
 *   - 颜色：按"涨跌幅"
 *       +5% 或以上 → 深红
 *       +0~5%      → 渐红
 *       0          → 灰
 *       0~-5%      → 渐绿
 *       -5% 或以下 → 深绿
 *   - 标签：板块名 + ▲/▼ + 涨跌幅（前置形状字符给色弱用户辅助识别）
 *   - 悬停：tooltip 显示完整信息（强度 / 资金净流入 / 涨跌幅）
 *   - 点击：emit('select-sector', sectorObj)，让父组件决定跳哪
 */
import { ref, onMounted, onUnmounted, watch, computed } from 'vue'
import * as echarts from 'echarts/core'
import { TreemapChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([TreemapChart, TooltipComponent, CanvasRenderer])

const props = defineProps({
    sectors: { type: Array, default: () => [] },   // [{rank,name,change,inflow,code,strength,up}]
    sizeBy:  { type: String, default: 'strength' }, // 'strength' | 'absChange' | 'absInflow'
})
const emit = defineEmits(['select-sector'])

const chartEl = ref(null)
let _chart = null
let _resizeObs = null


// ---- 颜色映射（红涨绿跌）----
function colorForChange(pct) {
    if (pct >=  5) return '#7f1d1d'   // red-900
    if (pct >=  3) return '#b91c1c'   // red-700
    if (pct >=  1) return '#dc2626'   // red-600
    if (pct >   0) return '#ef4444'   // red-500
    if (pct ===  0) return '#9ca3af'  // gray-400
    if (pct >  -1) return '#86efac'   // green-300
    if (pct > -3)  return '#22c55e'   // green-500
    if (pct > -5)  return '#15803d'   // green-700
    return '#064e3b'                  // green-900
}


// ---- 数据转换 ----
function changeFloat(s) {
    // s.change 形如 "+1.42%" 或 "-0.53%"
    return parseFloat((s.change || '0').replace('%', '')) || 0
}

function inflowAbsYi(s) {
    // s.inflow 形如 "+44.23亿" 或 "-1.02亿"
    return Math.abs(parseFloat((s.inflow || '0').replace(/[+亿]/g, '')) || 0)
}

const treemapData = computed(() => {
    return props.sectors.map(s => {
        const pct = changeFloat(s)
        let value
        if (props.sizeBy === 'absChange')      value = Math.max(0.1, Math.abs(pct))
        else if (props.sizeBy === 'absInflow') value = Math.max(0.1, inflowAbsYi(s))
        else                                    value = Math.max(1, s.strength || 1)
        const arrow = pct > 0 ? '▲' : (pct < 0 ? '▼' : '·')
        return {
            name: s.name,
            value,
            // 自定义字段，tooltip 用
            __pct: pct,
            __strength: s.strength,
            __inflow: s.inflow,
            __code: s.code,
            __label: `${s.name}\n${arrow} ${s.change}`,
            itemStyle: {
                color: colorForChange(pct),
                borderColor: '#fff',
                borderWidth: 1,
                gapWidth: 1,
            },
        }
    })
})


// ---- 渲染 ----
function buildOption() {
    return {
        animation: true,
        animationDurationUpdate: 300,
        tooltip: {
            confine: true,
            backgroundColor: 'rgba(255,255,255,0.96)',
            borderColor: '#e5e5e5',
            borderWidth: 1,
            textStyle: { color: '#111', fontSize: 12 },
            formatter: (info) => {
                const d = info.data || {}
                if (!d.__code) return ''
                const pctColor = d.__pct >= 0 ? '#dc2626' : '#059669'
                return `
                    <div style="font-weight:bold;font-size:13px;margin-bottom:4px;">${d.name}</div>
                    <div style="font-family:tabular-nums;color:${pctColor};font-weight:600;font-size:14px">${d.__pct >= 0 ? '▲' : '▼'} ${d.__pct.toFixed(2)}%</div>
                    <div style="color:#666;font-size:11px;margin-top:2px;">资金 ${d.__inflow}</div>
                    <div style="color:#666;font-size:11px">强度 ${d.__strength}</div>
                    <div style="color:#aaa;font-size:10px;margin-top:4px;">代码 ${d.__code} · 点击查看</div>
                `
            },
        },
        series: [{
            type: 'treemap',
            data: treemapData.value,
            roam: false,                // 不允许拖拽 / 缩放（嵌入页内别飞起来）
            nodeClick: false,           // 我们自己处理点击
            breadcrumb: { show: false },
            label: {
                show: true,
                formatter: (params) => params.data.__label || params.name,
                fontSize: 11,
                color: '#fff',
                fontWeight: 600,
                lineHeight: 14,
                textShadowColor: 'rgba(0,0,0,0.5)',
                textShadowBlur: 2,
            },
            // 单层不需要 levels 配置；保持简洁
            upperLabel: { show: false },
            itemStyle: {
                borderColor: '#fff',
                borderWidth: 1,
                gapWidth: 1,
            },
        }],
    }
}

function render() {
    if (!_chart) return
    _chart.setOption(buildOption(), { notMerge: true })
}


// ---- 生命周期 ----
onMounted(() => {
    if (!chartEl.value) return
    _chart = echarts.init(chartEl.value, null, { renderer: 'canvas' })
    render()
    _chart.on('click', (params) => {
        const d = params.data || {}
        if (d.__code) {
            emit('select-sector', {
                code: d.__code,
                name: d.name,
                change: d.__inflow ? `${d.__pct >= 0 ? '+' : ''}${d.__pct.toFixed(2)}%` : '',
                inflow: d.__inflow,
                strength: d.__strength,
                up: d.__pct >= 0,
            })
        }
    })
    // 容器尺寸变化时自动 resize
    _resizeObs = new ResizeObserver(() => _chart && _chart.resize())
    _resizeObs.observe(chartEl.value)
})

onUnmounted(() => {
    if (_resizeObs) { _resizeObs.disconnect(); _resizeObs = null }
    if (_chart) { _chart.dispose(); _chart = null }
})

watch(() => [props.sectors, props.sizeBy], render, { deep: true })
</script>

<template>
    <div ref="chartEl" class="w-full h-full"></div>
</template>
