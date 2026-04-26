<script setup>
import { onMounted, ref, nextTick, watch, computed } from 'vue'
import { api } from '../api/client'
import { useSmartRefresh } from '../composables/useSmartRefresh'

const emit = defineEmits(['openAI'])

const marketIndices = ref([])
const totalTurnover = ref(0)
const hotSectors = ref([])

// 成交额智能单位： 亿 / 万亿 自动切换
function formatAmt(amt) {
    if (amt == null || amt === 0) return '-';
    if (amt >= 10000) return (amt / 10000).toFixed(2) + '<span class="text-[11px] font-normal text-[#999] ml-[2px]">万亿</span>';
    return amt.toFixed(2) + '<span class="text-[11px] font-normal text-[#999] ml-[2px]">亿</span>';
}

function formatAmtText(amt) {
    if (amt == null || amt === 0) return '-';
    if (amt >= 10000) return (amt / 10000).toFixed(2) + '万亿';
    return amt.toFixed(2) + '亿';
}

const selectedSector = ref(null)
const sectorStocks = ref([])          // 当前选中板块的联动个股列表
const sectorStocksLoading = ref(false) // 拉取中标志
const stockFilter = ref('')            // 股票列表搜索关键字（名称或代码，模糊匹配）

// 当前板块的涨停家数（基于全量，不随筛选变化）
const sectorLimitUpCount = computed(() =>
    sectorStocks.value.filter(s => s.isLimitUp).length
)

// 经过搜索筛选后的股票列表
const filteredStocks = computed(() => {
    const q = stockFilter.value.trim().toLowerCase()
    if (!q) return sectorStocks.value
    return sectorStocks.value.filter(s =>
        (s.name && s.name.toLowerCase().includes(q)) ||
        (s.code && s.code.includes(q))
    )
})

// 连板天梯数据（按 height 降序分组）
const ladderTiers = ref([])
const ladderTotalCount = computed(() =>
    ladderTiers.value.reduce((sum, t) => sum + (t.number || 0), 0)
)

// 市场情绪数据（成交额 + 涨跌家数 + 涨停跌停）
const marketSentiment = ref(null)

// 较昨日"放量/缩量 XX亿 (±X.XX%)"格式化
const turnoverCompare = computed(() => {
    const t = marketSentiment.value?.turnover
    if (!t) return null
    const absYi = Math.abs(t.change) / 1e8
    return {
        label: t.isExpansion ? '↑ 放量' : '↓ 缩量',
        amount: absYi >= 10000 ? `${(absYi / 10000).toFixed(2)}万亿` : `${absYi.toFixed(0)}亿`,
        pct: `${t.changePct > 0 ? '+' : ''}${t.changePct.toFixed(2)}%`,
        colorClass: t.isExpansion ? 'text-[#dc2626]' : 'text-[#059669]',
    }
})

// 连板档位色阶：连板数越高 → 色温越"烫"（深红→正红→橙→琥珀→棕灰）
// 五段式让梯度清晰：避免相邻档位同色造成扁平感
function tierStyle(height) {
    if (height >= 8) return { main: '#991b1b', tint: 'rgba(153, 27, 27, 0.10)' } // 炽红 red-800
    if (height >= 6) return { main: '#dc2626', tint: 'rgba(220, 38, 38, 0.09)' } // 正红 red-600
    if (height >= 4) return { main: '#ea580c', tint: 'rgba(234, 88, 12, 0.09)' } // 橙 orange-600
    if (height >= 3) return { main: '#f59e0b', tint: 'rgba(245, 158, 11, 0.08)' } // 琥珀 amber-500
    return { main: '#78716c', tint: 'rgba(120, 113, 108, 0.05)' }                   // 棕灰 stone-500
}

// 拉取某板块的成分股
async function loadSectorStocks(plateId) {
    if (!plateId) {
        sectorStocks.value = []
        return
    }
    sectorStocksLoading.value = true
    try {
        const res = await api.getSectorStocks(plateId)
        if (res.ok) {
            sectorStocks.value = res.data || []
        } else {
            console.error('板块个股拉取失败:', res.error)
            sectorStocks.value = []
        }
    } finally {
        sectorStocksLoading.value = false
    }
}

const showIndexDrawer = ref(false)
const drawerIndexName = ref('')
const chartContainer = ref(null)
const tooltipRef = ref(null)
let chartInstance = null
let currentLineSeries = null
let currentCandleSeries = null
let currentMaSeries = []
let currentVolumeSeries = null
let klineDataMap = new Map() // time -> full data row cache

const timeframes = ['分时', '日K', '5日', '周K', '月K', '年K']
const activeTimeframe = ref('日K')

const maConfigs = [
    // 专业终端 MA 配色：降饱和 + 避开红绿（红绿留给 K 线主语义，也利于色弱辨识）
    { period: 5,  color: '#ca8a04' }, // 暗金 — 短期线最显眼
    { period: 10, color: '#0891b2' }, // 墨青
    { period: 20, color: '#7c3aed' }, // 沉紫
    { period: 30, color: '#be185d' }, // 暗玫
    { period: 60, color: '#64748b' }  // 中性灰 — 最长周期线弱化为"背景参考"
]

function calculateSMA(data, count) {
    const result = [];
    if (!data || data.length < count) return result;
    let sum = 0;
    for (let i = 0; i < count; i++) {
        sum += data[i].close;
    }
    result.push({ time: data[count - 1].time, value: sum / count });
    for (let i = count; i < data.length; i++) {
        sum = sum - data[i - count].close + data[i].close;
        result.push({ time: data[i].time, value: sum / count });
    }
    return result;
}

function handleSectorClick(sector) {
    selectedSector.value = sector
    stockFilter.value = ''  // 切板块时清空搜索
    loadSectorStocks(sector.code)
}


// 成交量智能单位： 万手 / 亿手 自动切换
function formatVol(vol) {
    if (vol == null) return '-';
    if (vol >= 10000) return (vol / 10000).toFixed(2) + '亿手';
    return vol.toFixed(2) + '万手';
}

async function openIndexChart(indexName) {
    drawerIndexName.value = indexName
    activeTimeframe.value = '日K'
    showIndexDrawer.value = true

    if (chartInstance) {
        // Drawer already open — destroy and rebuild chart for new index
        chartInstance.remove()
        chartInstance = null
        currentLineSeries = null
        currentCandleSeries = null
        currentVolumeSeries = null
        currentMaSeries = []
    }

    await nextTick()
    setTimeout(() => {
        initChart()
    }, 50)
}

async function switchTimeframe(tf) {
    if (activeTimeframe.value === tf) return
    activeTimeframe.value = tf
    await renderChartData()
}

function closeIndexChart() {
    showIndexDrawer.value = false
    if (chartInstance) {
        chartInstance.remove()
        chartInstance = null
    }
    currentLineSeries = null
    currentCandleSeries = null
    currentVolumeSeries = null
    currentMaSeries = []
}

async function initChart() {
    if (!chartContainer.value) return;
    if (chartInstance) {
        chartInstance.remove();
        chartInstance = null;
    }

    const { createChart } = await import('lightweight-charts');
    
    chartInstance = createChart(chartContainer.value, {
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#6b7280' },
        grid: {
            vertLines: { color: '#f3f4f6', style: 3 }, horzLines: { color: '#f3f4f6', style: 3 },
        },
        rightPriceScale: { borderColor: '#e5e7eb' },
        timeScale: { 
            borderColor: '#e5e7eb',
            timeVisible: true,
            secondsVisible: false,
        },
    });

    const observer = new ResizeObserver(entries => {
        if (!chartInstance || entries.length === 0 || entries[0].target !== chartContainer.value) return;
        const newRect = entries[0].contentRect;
        chartInstance.applyOptions({ height: newRect.height, width: newRect.width });
    });
    observer.observe(chartContainer.value);
    
    chartInstance.applyOptions({ 
        height: chartContainer.value.clientHeight, 
        width: chartContainer.value.clientWidth 
    });

    // Crosshair tooltip tracking
    chartInstance.subscribeCrosshairMove(param => {
        if (
            param.point === undefined ||
            !param.time ||
            param.point.x < 0 ||
            param.point.x > chartContainer.value.clientWidth ||
            param.point.y < 0 ||
            param.point.y > chartContainer.value.clientHeight
        ) {
            if (tooltipRef.value) tooltipRef.value.style.display = 'none';
            return;
        }

        let tooltipContent = '';
        
        if (activeTimeframe.value === '分时') {
            const data = currentLineSeries ? param.seriesData.get(currentLineSeries) : null;
            if (data && data.value !== undefined) {
                const d = new Date(param.time * 1000);
                const timeStr = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
                const full = klineDataMap.get(param.time) || {};
                const clr = (full.chg ?? 0) >= 0 ? '#dc2626' : '#059669';
                tooltipContent = `<div class="font-bold mb-1 text-[#333] border-b border-[#eee] pb-1">${timeStr}</div>
                                  <div class="flex flex-col gap-y-1 mt-1 text-[#555] font-mono min-w-[130px]">
                                    <div class="flex justify-between gap-4"><span>最新价</span> <span class="font-bold" style="color:${clr}">${data.value.toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>涨跌额</span> <span class="font-bold" style="color:${clr}">${(full.chg ?? 0) > 0 ? '+' : ''}${(full.chg ?? 0).toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>涨跌幅</span> <span class="font-bold" style="color:${clr}">${(full.pct ?? 0) > 0 ? '+' : ''}${(full.pct ?? 0).toFixed(2)}%</span></div>
                                    <div class="flex justify-between gap-4"><span>成交额</span> <span>${formatAmtText(full.amt)}</span></div>
                                    <div class="flex justify-between gap-4"><span>成交量</span> <span>${formatVol(full.vol)}</span></div>
                                  </div>`;
            }
        } else if (activeTimeframe.value === '5日') {
            const data = currentLineSeries ? param.seriesData.get(currentLineSeries) : null;
            if (data && data.value !== undefined) {
                const full = klineDataMap.get(param.time) || {};
                const d = new Date(param.time * 1000);
                const dateStr = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
                const clr = (full.chg ?? 0) >= 0 ? '#dc2626' : '#059669';
                
                tooltipContent = `<div class="font-bold mb-1 text-[#333] border-b border-[#eee] pb-1">${dateStr}</div>
                                  <div class="flex flex-col gap-y-1 mt-1 text-[#555] font-mono min-w-[130px]">
                                    <div class="flex justify-between gap-4"><span>价格</span> <span class="font-bold" style="color:${clr}">${data.value.toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>涨跌</span> <span class="font-bold" style="color:${clr}">${(full.chg ?? 0) > 0 ? '+' : ''}${(full.chg ?? 0).toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>幅度</span> <span class="font-bold" style="color:${clr}">${(full.pct ?? 0) > 0 ? '+' : ''}${(full.pct ?? 0).toFixed(2)}%</span></div>
                                    <div class="flex justify-between gap-4"><span>成交额</span> <span>${formatAmtText(full.amt)}</span></div>
                                    <div class="flex justify-between gap-4"><span>成交量</span> <span>${formatVol(full.vol)}</span></div>
                                  </div>`;
            }
        } else {
            const data = currentCandleSeries ? param.seriesData.get(currentCandleSeries) : null;
            if (data && data.close !== undefined) {
                const full = klineDataMap.get(param.time) || {};
                let dateStr = typeof param.time === 'string' ? param.time : (() => {
                    const d = new Date(param.time * 1000);
                    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
                })();
                const isUp = data.close >= data.open;
                const color = isUp ? '#dc2626' : '#059669';
                const chgColor = (full.chg ?? 0) >= 0 ? '#dc2626' : '#059669';
                
                // Build MA HTML strand
                let maHTML = '';
                if (currentMaSeries.length) {
                    maHTML = '<div class="w-full h-[1px] bg-[#eee] my-1.5"></div>';
                    maConfigs.forEach((config, idx) => {
                        const s = currentMaSeries[idx];
                        if (s) {
                            const maData = param.seriesData.get(s);
                            if (maData && maData.value !== undefined) {
                                maHTML += `<div class="flex justify-between gap-4"><span>MA${config.period}</span><span style="color:${config.color}">${maData.value.toFixed(2)}</span></div>`;
                            }
                        }
                    });
                }

                tooltipContent = `<div class="font-bold mb-1 text-[#333] border-b border-[#eee] pb-1">${dateStr}</div>
                                  <div class="flex flex-col gap-y-1 mt-1 text-[#555] font-mono whitespace-nowrap min-w-[140px]">
                                    <div class="flex justify-between gap-4"><span>开盘</span> <span style="color:${color}">${data.open.toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>最高</span> <span style="color:${color}">${data.high.toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>最低</span> <span style="color:${color}">${data.low.toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>收盘</span> <span style="color:${color}">${data.close.toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>涨跌</span> <span style="color:${chgColor}">${(full.chg ?? 0) > 0 ? '+' : ''}${(full.chg ?? 0).toFixed(2)}</span></div>
                                    <div class="flex justify-between gap-4"><span>涨幅</span> <span style="color:${chgColor}">${(full.pct ?? 0) > 0 ? '+' : ''}${(full.pct ?? 0).toFixed(2)}%</span></div>
                                    <div class="flex justify-between gap-4"><span>振幅</span> <span style="color:${chgColor}">${(full.amp ?? 0).toFixed(2)}%</span></div>
                                    <div class="flex justify-between gap-4"><span>成交额</span> <span>${formatAmtText(full.amt)}</span></div>
                                    <div class="flex justify-between gap-4"><span>成交量</span> <span>${formatVol(full.vol)}</span></div>
                                    ${maHTML}
                                  </div>`;
            }
        }

        if (tooltipContent && tooltipRef.value) {
            tooltipRef.value.style.display = 'block';
            tooltipRef.value.innerHTML = tooltipContent;
            
            // Adjust position so it doesn't overflow to the right or bottom
            let left = param.point.x + 15;
            let top = param.point.y + 15;
            
            // Basic boundary safety
            if (left > chartContainer.value.clientWidth - 150) {
                left = param.point.x - 160;
            }
            if (top > chartContainer.value.clientHeight - 280) { // Height is much larger now due to vertical card
                top = param.point.y - 300;
            }

            tooltipRef.value.style.left = left + 'px';
            tooltipRef.value.style.top = top + 'px';
        } else if (tooltipRef.value) {
            tooltipRef.value.style.display = 'none';
        }
    });

    await renderChartData();
}

async function renderChartData() {
    if (!chartInstance) return;
    
    // Clear existing series safely
    const seriesToRemove = [currentLineSeries, currentCandleSeries, currentVolumeSeries, ...currentMaSeries].filter(Boolean);
    seriesToRemove.forEach(s => { try { chartInstance.removeSeries(s); } catch(e){} });
    currentLineSeries = null;
    currentCandleSeries = null;
    currentVolumeSeries = null;
    currentMaSeries = [];

    let klineData = [];
    const res = await api.getKline(drawerIndexName.value, activeTimeframe.value);
    if (res.ok) {
        klineData = res.data || [];
    } else {
        console.error("K线接口返回错误:", res.error);
    }

    // Build lookup map: time → full row (so tooltip can read vol, amt, chg, pct, amp)
    klineDataMap = new Map();
    if (klineData && klineData.length) {
        klineData.forEach(d => klineDataMap.set(d.time, d));
    }

    if (activeTimeframe.value === '分时' || activeTimeframe.value === '5日') {
        currentLineSeries = chartInstance.addAreaSeries({
            lineColor: '#2563eb', 
            topColor: 'rgba(37, 99, 235, 0.4)',
            bottomColor: 'rgba(37, 99, 235, 0.0)',
            lineWidth: 2,
        });
        if(klineData && klineData.length) currentLineSeries.setData(klineData);
    } else {
        currentCandleSeries = chartInstance.addCandlestickSeries({
            // 阳线（涨/红）空心：极淡红底 + 红色边框 —— 保留色彩暗示，又兼顾色弱辨识
            upColor: 'rgba(220, 38, 38, 0.05)',
            borderUpColor: '#dc2626',
            wickUpColor: '#dc2626',
            // 阴线（跌/绿）实体：整根实心绿色
            downColor: '#059669',
            borderDownColor: '#059669',
            wickDownColor: '#059669',
            borderVisible: true,
        });
        if(klineData && klineData.length) {
            currentCandleSeries.setData(klineData);

            // Draw MA lines
            maConfigs.forEach(config => {
                const smaData = calculateSMA(klineData, config.period);
                if (smaData.length > 0) {
                    const series = chartInstance.addLineSeries({
                        color: config.color,
                        lineWidth: 1.5,
                        crosshairMarkerVisible: false,
                        lastValueVisible: false,
                        priceLineVisible: false,
                    });
                    series.setData(smaData);
                    currentMaSeries.push(series);
                } else {
                    currentMaSeries.push(null); // Keep length matching maConfigs
                }
            });
        }
    }
    
    // Separate Main chart from Bottom Volume Chart visually
    chartInstance.priceScale('right').applyOptions({
        scaleMargins: {
            top: 0.05, // 5% margin from top
            bottom: 0.25, // Leave bottom 25% empty
        },
    });
    
    // Build Volume Histogram
    if (klineData && klineData.length) {
        currentVolumeSeries = chartInstance.addHistogramSeries({
            color: '#26a69a',
            priceFormat: { type: 'volume' },
            priceScaleId: 'volumeScale', // New completely isolated scale
        });
        
        // Define isolated pane layout for volume
        chartInstance.priceScale('volumeScale').applyOptions({
            scaleMargins: {
                top: 0.80, // Volume sits strictly at the bottom 20%
                bottom: 0,
            },
        });

        const volData = klineData.map(d => {
            const isRed = d.open !== undefined ? (d.close >= d.open) : (d.chg >= 0);
            return {
                time: d.time,
                value: d.vol || 0,
                // 上涨日红柱低不透明度（视觉"轻"）、下跌日绿柱高不透明度（视觉"重"）—— 与 K 线空心/实心逻辑一致，色弱也能靠明暗区分
                color: isRed ? 'rgba(220, 38, 38, 0.45)' : 'rgba(5, 150, 105, 0.8)'
            };
        });
        currentVolumeSeries.setData(volData);
    }
    
    chartInstance.timeScale().fitContent();
}

// 指数 + 板块榜：基础 15s
useSmartRefresh(api.getMarketData, {
    baseInterval: 15_000,
    onData: (data) => {
        marketIndices.value = data.indices
        totalTurnover.value = data.total_turnover
        hotSectors.value = data.hotSectors
        if (hotSectors.value.length > 0 && !selectedSector.value) {
            selectedSector.value = hotSectors.value[0]
            loadSectorStocks(hotSectors.value[0].code)
        }
    },
    onError: (err) => console.error("获取行情数据失败:", err),
})

// 连板天梯：基础 30s
useSmartRefresh(api.getLimitUpLadder, {
    baseInterval: 30_000,
    onData: (data) => { ladderTiers.value = data || [] },
    onError: (err) => console.error("连板天梯拉取失败:", err),
})

// 市场情绪：基础 15s
useSmartRefresh(api.getMarketSentiment, {
    baseInterval: 15_000,
    onData: (data) => { marketSentiment.value = data },
    onError: (err) => console.error("市场情绪拉取失败:", err),
})

onMounted(() => {
    // Fallback dummy
    setTimeout(() => {
        if (marketIndices.value.length === 0) {
            marketIndices.value = [
              { name: '上证指数', price: '4055.55', change: '+0.70%', value: '+28.34', up: true },
              { name: '深证成指', price: '14796.33', change: '+2.05%', value: '+297.88', up: true },
              { name: '创业板指', price: '3626.27', change: '+3.17%', value: '+111.31', up: true },
              { name: '科创50', price: '1422.23', change: '+1.13%', value: '+15.91', up: true },
              { name: '北证50', price: '1340.82', change: '+1.34%', value: '+17.74', up: true },
              { name: '沪深300', price: '4736.61', change: '+1.10%', value: '+51.36', up: true },
              { name: '中证500', price: '8180.08', change: '+1.69%', value: '+136.00', up: true },
              { name: '中证1000', price: '8232.98', change: '+1.56%', value: '+126.82', up: true },
            ]
            hotSectors.value = [
              { rank: 1, name: '算力', change: '+2.58%', inflow: '+115.19亿', code: '880123' },
              { rank: 2, name: '锂电池', change: '+1.96%', inflow: '+91.27亿', code: '880124' },
              { rank: 3, name: '人工智能', change: '+1.80%', inflow: '+88.19亿', code: '880144' },
              { rank: 4, name: '大金融', change: '+1.20%', inflow: '+64.20亿', code: '880111' },
            ]
            if (!selectedSector.value) {
                selectedSector.value = hotSectors.value[0]
            }
        }
    }, 1500)
})
</script>

<template>
  <div class="flex flex-col h-full bg-[#fcfcfc] relative overflow-hidden">

    <!-- TOP HORIZONTAL STRIP: INDICES -->
    <div class="bg-[#fafafa] border-b border-[#e5e5e5] px-4 py-2 flex flex-wrap xl:flex-nowrap items-stretch w-full gap-4 z-10 shrink-0">
        <div class="flex-1 flex gap-[8px] items-stretch min-w-0">
            <!-- Modified to click open drawer -->
            <div
              v-for="idx in marketIndices"
              :key="idx.name"
              @click="openIndexChart(idx.name)"
              class="flex-1 basis-0 min-w-0 bg-white border border-[#e8e8e8] rounded-[4px] py-[4px] px-[8px] cursor-pointer hover:border-[#dc2626]/40 transition-[border-color] flex flex-col items-center justify-center relative overflow-hidden group"
            >
                <div class="absolute inset-0 bg-[#dc2626] opacity-0 group-hover:opacity-[0.02] transition-opacity"></div>
                <div class="text-[#888] text-[12px] font-medium text-center tracking-wide">{{ idx.name }}</div>
                <div class="font-bold text-center text-[16px] my-[2px] font-sans tracking-tight" :class="idx.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ idx.price }}</div>
                <div class="flex justify-center items-center gap-[6px] text-[11px] font-medium" :class="idx.up ? 'text-[#dc2626]' : 'text-[#059669]'">
                    <span>{{ idx.value }}</span>
                    <span>{{ idx.change }}</span>
                </div>
            </div>
        </div>

        <!-- Volume / Sentiment -->
        <div class="shrink-0 xl:pl-4 xl:border-l border-[#e5e5e5] basis-[320px] grow-[0.2] max-w-[440px] flex items-stretch">
            <div class="bg-white border border-[#e8e8e8] rounded-[4px] py-[4px] px-[10px] flex items-center gap-[10px] w-full h-full cursor-default">
                <!-- Left: Turnover value -->
                <div class="flex-[4] basis-0 min-w-0 flex flex-col justify-center border-r border-[#f0f0f0] pr-[12px]">
                    <span class="text-[#999] text-[11px] font-medium leading-none tracking-[0.12em] mb-[7px]">全市场成交额</span>
                    <span class="text-[#222] text-[18px] font-bold leading-none"
                          v-html="marketSentiment ? formatAmt(marketSentiment.turnover.today / 1e8) : '--'"></span>
                    <span v-if="turnoverCompare"
                          class="text-[10px] font-medium leading-none tracking-wide mt-[8px]"
                          :class="turnoverCompare.colorClass">
                        {{ turnoverCompare.label }} {{ turnoverCompare.amount }}
                        <span class="opacity-70 ml-[2px]">({{ turnoverCompare.pct }})</span>
                    </span>
                    <span v-else class="text-[10px] text-[#ccc] font-medium leading-none mt-[8px]">较昨日 --</span>
                </div>
                <!-- Right: Market breadth -->
                <div class="flex-[5] basis-0 min-w-0 flex flex-col justify-center">
                    <div class="flex justify-between items-center text-[10px] leading-none tabular-nums">
                        <span class="text-[#dc2626] font-medium">涨 {{ marketSentiment?.breadth?.up ?? '--' }}</span>
                        <span class="text-[#888]">平 {{ marketSentiment?.breadth?.flat ?? '--' }}</span>
                        <span class="text-[#059669] font-medium">跌 {{ marketSentiment?.breadth?.down ?? '--' }}</span>
                    </div>
                    <div class="w-full h-[5px] rounded-full bg-[#f0f0f0] flex mt-[4px] overflow-hidden">
                        <div class="bg-[#dc2626] h-full transition-[width] duration-500" :style="{ width: (marketSentiment?.breadth?.upPct ?? 0) + '%' }"></div>
                        <div class="bg-[#d1d5db] h-full transition-[width] duration-500" :style="{ width: (marketSentiment?.breadth?.flatPct ?? 0) + '%' }"></div>
                        <div class="bg-[#059669] h-full transition-[width] duration-500" :style="{ width: (marketSentiment?.breadth?.downPct ?? 0) + '%' }"></div>
                    </div>
                    <div class="flex justify-between mt-[4px] text-[10px] leading-none tabular-nums">
                        <span class="text-[#dc2626] bg-[#fff0f0] px-1 py-[1px] rounded-sm">涨停 {{ marketSentiment?.breadth?.limitUp ?? '--' }}</span>
                        <span class="text-[#059669] bg-[#f0fff0] px-1 py-[1px] rounded-sm">跌停 {{ marketSentiment?.breadth?.limitDown ?? '--' }}</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- MAIN CONTENT AREA -->
    <div class="flex-1 flex overflow-hidden w-full bg-white z-0">
      
      <!-- Left Column: 精选板块 -->
      <div class="w-[220px] bg-white border-r border-[#e5e5e5] flex flex-col flex-shrink-0 z-0 relative">
        <div class="h-[44px] bg-[#fff5f5] text-[#dc2626] border-b border-[#ffe5e5] flex items-center justify-between px-[12px] font-semibold text-[13px]">
          <span>精选板块</span>
        </div>

        <div class="flex-1 overflow-y-auto w-full custom-scrollbar">
            <div
              v-for="item in hotSectors"
              :key="item.name"
              @click="handleSectorClick(item)"
              class="flex items-center justify-between py-[10px] px-[10px] border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer group transition duration-200"
              :class="selectedSector?.name === item.name ? 'bg-[#fff0f0] border-l-2 border-l-[#dc2626] pr-[10px] pl-[8px]' : 'border-l-2 border-l-transparent'"
            >
              <div class="flex items-center gap-[10px] flex-1 min-w-0">
                <div class="w-[22px] h-[22px] shrink-0 rounded-[4px] flex items-center justify-center text-[11px] font-bold"
                     :class="item.rank <= 3 ? 'bg-[#dc2626] text-white' : 'bg-[#f0f0f0] text-[#777]'">
                  {{ item.rank }}
                </div>
                <div class="flex flex-col min-w-0 leading-tight">
                  <span class="text-[14px] font-bold text-[#222] truncate block" :title="item.name">{{ item.name }}</span>
                  <span v-if="item.strength" class="text-[11px] text-[#999] font-medium mt-[3px] flex items-center gap-[4px]" :title="'KPL 精选强度分'">
                    <svg class="w-[11px] h-[11px] text-[#dc2626]/55" viewBox="0 0 20 20" fill="currentColor">
                      <path fill-rule="evenodd" d="M12.395 2.553a1 1 0 00-1.45-.385c-.345.23-.614.558-.822.88-.214.33-.403.713-.57 1.116-.334.804-.614 1.768-.84 2.734a31.365 31.365 0 00-.613 3.58 2.64 2.64 0 01-.945-1.067c-.328-.68-.398-1.534-.398-2.654A1 1 0 005.05 6.05 6.981 6.981 0 003 11a7 7 0 1011.95-4.95c-.592-.591-.98-.985-1.348-1.467-.363-.476-.724-1.063-1.207-2.03zM12.12 15.12A3 3 0 017 13s.879.5 2.5.5c0-1 .5-4 1.25-4.5.5 1 .786 1.293 1.371 1.879A2.99 2.99 0 0113 13a2.99 2.99 0 01-.879 2.121z" clip-rule="evenodd" />
                    </svg>
                    <span>{{ item.strength.toLocaleString() }}</span>
                  </span>
                </div>
              </div>
              <div class="flex flex-col items-end leading-tight shrink-0 ml-2">
                <span class="text-[13px] font-bold" :class="item.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ item.change }}</span>
                <span class="text-[11px] font-medium mt-[3px]" :class="(item.inflow && item.inflow.startsWith('-')) ? 'text-[#059669]/80' : 'text-[#dc2626]/75'">{{ item.inflow }}</span>
              </div>
            </div>
        </div>
      </div>

      <!-- Right Column: Sector Stock List Workspace ALWAYS ON -->
      <div class="flex-1 bg-white relative overflow-hidden flex flex-col pt-[1px]">
        <template v-if="selectedSector">
            <div class="h-[43px] px-[14px] border-b border-[#f0f0f0] flex justify-between items-center bg-white shrink-0">
                <div class="flex items-center gap-2 min-w-0">
                    <h2 class="text-[14px] font-bold text-[#111] tracking-wide truncate">{{ selectedSector.name }}</h2>
                    <div class="text-[11px] text-[#dc2626] font-bold bg-[#fff5f5] px-[6px] py-[1px] rounded-[4px] border border-[#ffe5e5] shrink-0">领涨 {{ selectedSector.change }}</div>
                    <div v-if="sectorLimitUpCount > 0"
                         class="text-[11px] font-bold bg-[#dc2626] text-white px-[6px] py-[1px] rounded-[4px] shrink-0 shadow-[0_1px_2px_rgba(220,38,38,0.25)] tabular-nums">
                        涨停 {{ sectorLimitUpCount }}
                    </div>
                </div>
                <div class="flex gap-[8px] items-center shrink-0 relative">
                    <input v-model="stockFilter"
                           type="text"
                           placeholder="筛选 名称 / 代码..."
                           class="bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] pl-[10px] pr-[26px] py-[4px] text-[12px] outline-none focus:border-[#ff6b6b] focus:bg-white w-[180px] transition placeholder:text-[#ccc]">
                    <!-- 清空按钮：有输入时显示 -->
                    <button v-if="stockFilter"
                            @click="stockFilter = ''"
                            class="absolute right-[6px] top-1/2 -translate-y-1/2 w-[16px] h-[16px] flex items-center justify-center rounded-full text-[#aaa] hover:text-[#666] hover:bg-[#f0f0f0] transition"
                            title="清空筛选">
                        <svg class="w-[10px] h-[10px]" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
                        </svg>
                    </button>
                </div>
            </div>
            
            <div class="flex-1 w-full bg-white overflow-auto custom-scrollbar relative">
                <table class="w-full text-left border-collapse whitespace-nowrap min-w-max">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] z-10 text-[12px] text-[#888]">
                        <tr>
                            <th class="px-[12px] py-[10px] font-normal w-[70px]">代码</th>
                            <th class="px-[12px] py-[10px] font-normal w-[220px]">名称</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">最新价</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">涨幅</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">成交额</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">主力买</th>
                            <th class="px-[10px] py-[10px] font-normal text-right">主力卖</th>
                            <th class="px-[12px] py-[10px] font-normal text-right">主力净额</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="stock in filteredStocks"
                            :key="stock.code"
                            class="border-b border-[#f5f5f5] hover:bg-[#f2f8fc] transition-colors group cursor-default"
                        >
                            <td class="px-[12px] py-[10px] text-[12px] text-[#666] font-mono align-top">{{ stock.code }}</td>
                            <td class="px-[12px] py-[10px] align-top">
                                <div class="flex items-center gap-[6px] flex-wrap">
                                    <svg v-if="stock.isLimitUp"
                                         class="w-[14px] h-[14px] shrink-0 text-[#ea580c] drop-shadow-[0_0_3px_rgba(234,88,12,0.45)]"
                                         viewBox="0 0 20 20" fill="currentColor"
                                         :title="'已涨停（+' + stock.change + '）'">
                                        <path fill-rule="evenodd" d="M12.395 2.553a1 1 0 00-1.45-.385c-.345.23-.614.558-.822.88-.214.33-.403.713-.57 1.116-.334.804-.614 1.768-.84 2.734a31.365 31.365 0 00-.613 3.58 2.64 2.64 0 01-.945-1.067c-.328-.68-.398-1.534-.398-2.654A1 1 0 005.05 6.05 6.981 6.981 0 003 11a7 7 0 1011.95-4.95c-.592-.591-.98-.985-1.348-1.467-.363-.476-.724-1.063-1.207-2.03zM12.12 15.12A3 3 0 017 13s.879.5 2.5.5c0-1 .5-4 1.25-4.5.5 1 .786 1.293 1.371 1.879A2.99 2.99 0 0113 13a2.99 2.99 0 01-.879 2.121z" clip-rule="evenodd" />
                                    </svg>
                                    <span class="text-[14px] font-bold text-[#111]">{{ stock.name }}</span>
                                    <span v-if="stock.leader"
                                          class="text-[10px] font-bold px-[6px] py-[1px] rounded-[3px] text-white leading-[1.4] shadow-[0_1px_2px_rgba(220,38,38,0.25)]"
                                          :class="stock.leader === '破板' ? 'bg-[#94a3b8] !shadow-[0_1px_2px_rgba(100,116,139,0.25)]' : 'bg-[#dc2626]'">
                                        {{ stock.leader }}
                                    </span>
                                    <span v-if="stock.streak"
                                          class="text-[10px] font-semibold px-[6px] py-[1px] rounded-[3px] bg-[#fff0f0] text-[#dc2626] border border-[#fecaca] leading-[1.4]">
                                        {{ stock.streak }}
                                    </span>
                                </div>
                                <div v-if="stock.mainForce || stock.themesAll" class="mt-[4px] flex items-center gap-[4px] truncate leading-tight" :title="stock.themesAll">
                                    <span v-if="stock.mainForce"
                                          class="shrink-0 text-[10px] font-semibold px-[5px] py-[1px] rounded-[3px] leading-[1.4]"
                                          :class="stock.mainForce === '游资' ? 'bg-[#f3e8ff] text-[#7c3aed]' : 'bg-[#dbeafe] text-[#1d4ed8]'">
                                        {{ stock.mainForce }}
                                    </span>
                                    <span v-if="stock.themesAll" class="text-[11px] text-[#999] truncate">{{ stock.themesAll }}</span>
                                </div>
                            </td>
                            <td class="px-[10px] py-[10px] text-[14px] font-bold text-right align-top tabular-nums" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.price }}</td>
                            <td class="px-[10px] py-[10px] text-[13px] font-bold text-right align-top tabular-nums" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.change }}</td>
                            <td class="px-[10px] py-[10px] text-[12px] text-[#475569] font-medium text-right align-top tabular-nums">{{ stock.turnover }}</td>
                            <td class="px-[10px] py-[10px] text-[12px] text-[#475569] font-medium text-right align-top tabular-nums">{{ stock.mainBuy }}</td>
                            <td class="px-[10px] py-[10px] text-[12px] text-[#475569] font-medium text-right align-top tabular-nums">{{ stock.mainSell }}</td>
                            <td class="px-[12px] py-[10px] text-[13px] font-bold text-right align-top tabular-nums" :class="stock.mainNetUp ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.mainNet }}</td>
                        </tr>
                        <tr v-if="sectorStocksLoading && !sectorStocks.length">
                            <td colspan="8" class="px-[20px] py-[60px] text-center text-[#aaa] text-[13px]">
                                <span class="inline-flex items-center gap-2">
                                    <span class="w-[10px] h-[10px] rounded-full bg-[#dc2626]/50 animate-pulse"></span>
                                    正在拉取 {{ selectedSector.name }} 板块联动个股...
                                </span>
                            </td>
                        </tr>
                        <tr v-else-if="!sectorStocks.length">
                            <td colspan="8" class="px-[20px] py-[80px] text-center text-[#aaa] text-[13px]">
                                暂无该板块成分股数据
                            </td>
                        </tr>
                        <tr v-else-if="!filteredStocks.length">
                            <td colspan="8" class="px-[20px] py-[60px] text-center text-[#aaa] text-[13px]">
                                未匹配到"{{ stockFilter }}"，
                                <button @click="stockFilter = ''" class="text-[#dc2626] hover:underline">清空筛选</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </template>
        <!-- Failsafe if neither selected nor loaded yet -->
        <div v-else class="flex-1 flex items-center justify-center text-[#ccc] text-[14px]">
            板块数据加载中...
        </div>
      </div>

      <!-- Right Column: 连板天梯 -->
      <div class="w-[340px] bg-white border-l border-[#e5e5e5] flex flex-col flex-shrink-0 z-0 relative">
        <div class="h-[44px] bg-[#fff5f5] text-[#dc2626] border-b border-[#ffe5e5] flex items-center justify-between px-[14px] shrink-0">
          <div class="flex items-center gap-[6px]">
            <svg class="w-[13px] h-[13px]" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M12.395 2.553a1 1 0 00-1.45-.385c-.345.23-.614.558-.822.88-.214.33-.403.713-.57 1.116-.334.804-.614 1.768-.84 2.734a31.365 31.365 0 00-.613 3.58 2.64 2.64 0 01-.945-1.067c-.328-.68-.398-1.534-.398-2.654A1 1 0 005.05 6.05 6.981 6.981 0 003 11a7 7 0 1011.95-4.95c-.592-.591-.98-.985-1.348-1.467-.363-.476-.724-1.063-1.207-2.03zM12.12 15.12A3 3 0 017 13s.879.5 2.5.5c0-1 .5-4 1.25-4.5.5 1 .786 1.293 1.371 1.879A2.99 2.99 0 0113 13a2.99 2.99 0 01-.879 2.121z" clip-rule="evenodd" />
            </svg>
            <span class="font-semibold text-[13px]">连板天梯</span>
          </div>
          <span class="text-[11px] text-[#dc2626]/70 font-medium">{{ ladderTotalCount }} 只</span>
        </div>

        <div class="flex-1 overflow-y-auto custom-scrollbar">
            <!-- Empty / loading state -->
            <div v-if="!ladderTiers.length" class="flex items-center justify-center h-full text-[#ccc] text-[12px]">
                加载天梯数据中...
            </div>

            <!-- Tier groups, 最高板数在最上（天梯结构：顶部最强，向下递减）-->
            <div v-for="tier in ladderTiers" :key="tier.height"
                 class="relative">
                <!-- 左侧连续 accent 色条：视觉上"爬梯"的骨架 -->
                <div class="absolute left-0 top-0 bottom-0 w-[3px]"
                     :style="{ background: tierStyle(tier.height).main }"></div>

                <!-- 档位头（sticky 贴顶）: 档位徽章 + 背景色温 -->
                <div class="sticky top-0 z-[5] flex items-center justify-between pl-[14px] pr-[12px] py-[7px] border-b border-[#f0f0f0]"
                     :style="{ background: `linear-gradient(to right, ${tierStyle(tier.height).tint}, rgba(255,255,255,0.85) 85%)` }">
                    <div class="flex items-center gap-[8px]">
                        <!-- 档位徽章 —— 梯形感的圆角矩形 + 数字主视觉 -->
                        <div class="flex items-baseline gap-[2px] px-[8px] py-[2px] rounded-[4px] text-white shadow-[0_1px_2px_rgba(0,0,0,0.15)]"
                             :style="{ background: tierStyle(tier.height).main }">
                            <span class="text-[13px] font-bold tabular-nums leading-none">{{ tier.height }}</span>
                            <span class="text-[10px] font-semibold leading-none">板</span>
                        </div>
                        <span class="text-[11px] text-[#666] font-medium">{{ tier.number }} 只</span>
                    </div>
                </div>

                <!-- 股票行：2 列栅格，每格内 2 行（name / concept badge）-->
                <div class="grid grid-cols-2">
                    <div v-for="(stock, idx) in tier.stocks" :key="stock.code"
                         class="flex flex-col gap-[3px] pl-[14px] pr-[8px] py-[6px] cursor-default transition-colors min-w-0 border-b border-[#f5f5f5] hover:bg-[#fff5f5]"
                         :class="{ 'border-l border-[#f5f5f5]': idx % 2 === 1 }">
                        <!-- 第一行：代码 + 名字 -->
                        <div class="flex items-center gap-[8px] min-w-0">
                            <span class="text-[12px] text-[#666] font-mono tabular-nums shrink-0">{{ stock.code }}</span>
                            <span class="text-[14px] font-bold text-[#111] truncate">{{ stock.name }}</span>
                        </div>
                        <!-- 第二行：涨停池原始涨停原因（+号分隔灰色文字，作为辅助信息不抢主角戏份）-->
                        <div v-if="stock.reasonAll"
                             class="text-[11px] text-[#94a3b8] leading-[1.4] truncate"
                             :title="stock.reasonAll">
                            {{ stock.reasonAll }}
                        </div>
                    </div>
                </div>
            </div>
        </div>
      </div>
    </div>
    
    <!-- OVERLAY DRAWER FOR MARKET INDEX CHART -->
    <Transition name="drawer">
        <div v-if="showIndexDrawer" class="absolute bottom-0 left-0 right-0 h-[85%] bg-white shadow-[0_-10px_40px_rgba(0,0,0,0.15)] z-50 flex flex-col border-t border-[#e5e5e5] rounded-t-xl">
            <!-- Handle & Header -->
            <div class="h-[54px] px-[24px] border-b border-[#f0f0f0] flex justify-between items-center bg-[#fafafa] rounded-t-xl shrink-0 cursor-default">
                <div class="flex items-center gap-[16px]">
                    <div class="flex items-center gap-2">
                        <div class="w-1.5 h-4 rounded-full bg-[#dc2626]"></div>
                        <h2 class="text-[16px] font-bold text-[#111] tracking-wide">{{ drawerIndexName }}</h2>
                    </div>
                    
                    <!-- Timeframe Toggles -->
                    <div class="flex items-center bg-[#f0f0f0] rounded-[6px] p-[3px] ml-4">
                        <button 
                            v-for="tf in timeframes" 
                            :key="tf"
                            @click="switchTimeframe(tf)"
                            class="px-3 py-1 text-[12px] font-medium rounded-[4px] transition"
                            :class="activeTimeframe === tf ? 'bg-white text-[#111] shadow-[0_1px_2px_rgba(0,0,0,0.05)]' : 'text-[#777] hover:text-[#333]'"
                        >
                            {{ tf }}
                        </button>
                    </div>
                </div>
                
                <div class="flex gap-4 items-center">
                    <button @click="emit('openAI')" class="text-[12px] font-bold text-[#dc2626] hover:text-[#991b1b] flex items-center gap-1 transition">
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                        AI 解盘
                    </button>
                    <!-- Close button -->
                    <button @click="closeIndexChart" class="w-7 h-7 flex items-center justify-center rounded-full hover:bg-[#ffe5e5] text-[#999] hover:text-[#dc2626] transition ml-2">
                        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
            </div>
            
            <!-- Chart Container -->
            <div ref="chartContainer" class="flex-1 w-full bg-white relative overflow-hidden">
                <!-- MA Legend: only on candle timeframes (分时/5日 是折线，不含 MA) -->
                <div
                    v-if="activeTimeframe !== '分时' && activeTimeframe !== '5日'"
                    class="absolute top-[10px] left-[12px] flex items-center gap-[14px] bg-white/85 backdrop-blur-[2px] border border-[#e5e5e5] rounded-[4px] px-[10px] py-[4px] z-[5] pointer-events-none"
                >
                    <span
                        v-for="m in maConfigs"
                        :key="m.period"
                        class="text-[11px] font-semibold tracking-wide"
                        :style="{ color: m.color }"
                    >
                        MA{{ m.period }}
                    </span>
                </div>
                <div ref="tooltipRef" class="absolute leading-tight hidden pointer-events-none bg-white/95 backdrop-blur-[2px] border border-[#e5e5e5] text-[12px] p-2.5 z-[100] rounded-[6px] shadow-[0_4px_16px_rgba(0,0,0,0.12)]" style="display: none; left: 0; top: 0;"></div>
            </div>
        </div>
    </Transition>
    
    <!-- Dim Background Overlay behind the drawer -->
    <Transition name="fade">
        <div v-if="showIndexDrawer" @click="closeIndexChart" class="absolute inset-0 bg-black/10 z-40 backdrop-blur-[1px]"></div>
    </Transition>
  </div>
</template>

<style scoped>
.scrollbar-hide::-webkit-scrollbar {
    display: none;
}
.scrollbar-hide {
    -ms-overflow-style: none;
    scrollbar-width: none;
}
.custom-scrollbar::-webkit-scrollbar {
  width: 5px;
  height: 5px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #e2e8f0; 
  border-radius: 4px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #cbd5e1;
}

/* Drawer Transitions */
.drawer-enter-active,
.drawer-leave-active {
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.drawer-enter-from,
.drawer-leave-to {
  transform: translateY(100%);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
