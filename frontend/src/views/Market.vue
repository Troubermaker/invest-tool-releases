<script setup>
import { onMounted, onUnmounted, ref, nextTick, watch } from 'vue'

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

// Dummy stocks for a selected sector
const sectorStocksMap = {
    '算力': [
        { code: '600845', name: '宝信软件', price: '49.12', change: '+5.45%', changeVal: '+2.54', turnover: '8.54亿', up: true },
        { code: '300308', name: '中际旭创', price: '158.55', change: '+4.12%', changeVal: '+6.27', turnover: '35.1亿', up: true },
        { code: '300394', name: '天孚通信', price: '143.10', change: '+3.55%', changeVal: '+4.91', turnover: '15.2亿', up: true },
        { code: '000977', name: '浪潮信息', price: '38.56', change: '-1.23%', changeVal: '-0.48', turnover: '22.1亿', up: false },
    ],
    '锂电池': [
        { code: '300750', name: '宁德时代', price: '185.33', change: '+2.10%', changeVal: '+3.81', turnover: '65.3亿', up: true },
        { code: '002074', name: '国轩高科', price: '21.50', change: '+1.52%', changeVal: '+0.32', turnover: '11.5亿', up: true },
    ]
}

const selectedSector = ref(null) 

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
    if (window.pywebview && window.pywebview.api) {
        try {
            klineData = await window.pywebview.api.get_kline(drawerIndexName.value, activeTimeframe.value);
        } catch(e) {
            console.error("API error fetching klines", e);
        }
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

onMounted(() => {
    const loadData = async () => {
        if (window.pywebview && window.pywebview.api) {
            try {
                const res = await window.pywebview.api.get_market_data()
                marketIndices.value = res.indices
                totalTurnover.value = res.total_turnover
                hotSectors.value = res.hotSectors
                // Default select the first hot sector to fill right side automatically
                if (hotSectors.value.length > 0 && !selectedSector.value) {
                    selectedSector.value = hotSectors.value[0]
                }
            } catch (e) {
                console.error("获取数据失败:", e)
            }
        }
    }

    if (window.pywebview && window.pywebview.api) {
        loadData() 
    } else {
        window.addEventListener('pywebviewready', loadData) 
    }

    // 每 60 秒自动刷新一次行情数据
    const refreshInterval = setInterval(loadData, 60000)
    onUnmounted(() => {
        clearInterval(refreshInterval)
    })
    
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
                    <span class="text-[#222] text-[18px] font-bold leading-none" v-html="formatAmt(totalTurnover)"></span>
                    <span class="text-[10px] text-[#059669] font-medium leading-none tracking-wide mt-[8px]">↓ 缩量 745亿 <span class="opacity-70 ml-[2px]">(-3%)</span></span>
                </div>
                <!-- Right: Market breadth -->
                <div class="flex-[5] basis-0 min-w-0 flex flex-col justify-center">
                    <div class="flex justify-between items-center text-[10px] leading-none">
                        <span class="text-[#dc2626] font-medium">涨 4205</span>
                        <span class="text-[#888]">平 133</span>
                        <span class="text-[#059669] font-medium">跌 1072</span>
                    </div>
                    <div class="w-full h-[5px] rounded-full bg-[#f0f0f0] flex mt-[4px] overflow-hidden">
                        <div class="bg-[#dc2626] h-full" style="width: 80%"></div>
                        <div class="bg-transparent h-full" style="width: 2%"></div>
                        <div class="bg-[#059669] h-full" style="width: 18%"></div>
                    </div>
                    <div class="flex justify-between mt-[4px] text-[10px] leading-none">
                        <span class="text-[#dc2626] bg-[#fff0f0] px-1 py-[1px] rounded-sm">涨停 88</span>
                        <span class="text-[#059669] bg-[#f0fff0] px-1 py-[1px] rounded-sm">跌停 9</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- MAIN CONTENT AREA -->
    <div class="flex-1 flex overflow-hidden w-full bg-white z-0">
      
      <!-- Left Column: 精选板块 -->
      <div class="w-[280px] bg-white border-r border-[#e5e5e5] flex flex-col flex-shrink-0 z-0 relative">
        <div class="h-[44px] bg-[#fff5f5] text-[#dc2626] border-b border-[#ffe5e5] flex items-center justify-between px-[16px] font-semibold text-[14px]">
          <span>精选联动板块区</span>
        </div>
        
        <div class="flex-1 overflow-y-auto w-full custom-scrollbar">
            <div 
              v-for="item in hotSectors" 
              :key="item.name" 
              @click="handleSectorClick(item)"
              class="flex items-center justify-between py-[12px] px-[16px] border-b border-[#f5f5f5] hover:bg-[#fffafa] cursor-pointer group transition duration-200"
              :class="selectedSector?.name === item.name ? 'bg-[#fff0f0] border-l-2 border-l-[#dc2626] pr-[16px] pl-[14px]' : 'border-l-2 border-l-transparent'"
            >
              <div class="flex items-center gap-[10px] overflow-hidden max-w-[65%]">
                 <div class="w-[20px] h-[20px] flex-shrink-0 rounded-full flex items-center justify-center text-[11px] font-bold" 
                     :class="item.rank <= 3 ? 'bg-[#dc2626] text-white shadow-sm' : 'bg-[#f0f0f0] text-[#777]'">
                  {{ item.rank }}
                </div>
                <div class="flex flex-col min-w-0 leading-tight">
                  <span class="text-[14px] font-bold text-[#222] truncate block" :title="item.name">{{ item.name }}</span>
                  <span class="text-[11px] text-[#999] truncate block mt-[2px] font-mono">{{ item.code }}</span>
                </div>
              </div>
              <div class="flex flex-col items-end leading-tight">
                <span class="text-[14px] font-bold text-[#dc2626] block">{{ item.change }}</span>
                <span class="text-[11px] text-[#dc2626]/70 font-medium block mt-[2px]">{{ item.inflow }}</span>
              </div>
            </div>
        </div>
      </div>

      <!-- Right Column: Sector Stock List Workspace ALWAYS ON -->
      <div class="flex-1 bg-white relative overflow-hidden flex flex-col pt-[1px]">
        <template v-if="selectedSector">
            <div class="h-[43px] px-[20px] border-b border-[#f0f0f0] flex justify-between items-center bg-white shrink-0">
                <div class="flex items-center gap-3">
                    <h2 class="text-[15px] font-bold text-[#111] tracking-wide">{{ selectedSector.name }} <span class="text-[12px] font-normal text-[#888] ml-1">板块成分股</span></h2>
                    <div class="text-[12px] text-[#dc2626] font-bold bg-[#fff5f5] px-2 py-0.5 rounded-[4px] border border-[#ffe5e5]">领涨: {{ selectedSector.change }}</div>
                </div>
                <div class="flex gap-[10px] items-center">
                    <input type="text" placeholder="键盘精灵搜索..." class="bg-[#f9fafb] border border-[#e5e5e5] rounded-[4px] px-[10px] py-[4px] text-[12px] outline-none focus:border-[#ff6b6b] focus:bg-white w-[180px] transition placeholder:text-[#ccc]">
                    <button @click="emit('openAI')" class="px-[12px] py-[4px] bg-[#fff5f5] text-[#dc2626] border border-[#ffe5e5] text-[12px] font-bold rounded-[4px] hover:bg-[#dc2626] hover:text-white transition">AI 分析该题材</button>
                </div>
            </div>
            
            <div class="flex-1 w-full bg-white overflow-auto custom-scrollbar relative">
                <table class="w-full text-left border-collapse whitespace-nowrap min-w-max">
                    <thead class="sticky top-0 bg-[#fafafa] shadow-[0_1px_0_#eeeeee] z-10 text-[12px] text-[#888]">
                        <tr>
                            <th class="px-[20px] py-[10px] font-normal w-[80px]">代码</th>
                            <th class="px-[20px] py-[10px] font-normal w-[120px]">名称</th>
                            <th class="px-[20px] py-[10px] font-normal text-right">最新价</th>
                            <th class="px-[20px] py-[10px] font-normal text-right">涨跌幅</th>
                            <th class="px-[20px] py-[10px] font-normal text-right">涨跌额</th>
                            <th class="px-[20px] py-[10px] font-normal text-right">成交额</th>
                            <th class="px-[20px] py-[10px] font-normal text-center">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr 
                            v-for="stock in (sectorStocksMap[selectedSector.name] || [])" 
                            :key="stock.code"
                            class="border-b border-[#f5f5f5] hover:bg-[#f2f8fc] transition-colors group cursor-default"
                        >
                            <td class="px-[20px] py-[12px] text-[13px] text-[#666] font-mono">{{ stock.code }}</td>
                            <td class="px-[20px] py-[12px] text-[14px] font-bold text-[#111]">{{ stock.name }}</td>
                            <td class="px-[20px] py-[12px] text-[15px] font-bold text-right" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.price }}</td>
                            <td class="px-[20px] py-[12px] text-[14px] font-bold text-right" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.change }}</td>
                            <td class="px-[20px] py-[12px] text-[13px] text-right" :class="stock.up ? 'text-[#dc2626]' : 'text-[#059669]'">{{ stock.changeVal }}</td>
                            <td class="px-[20px] py-[12px] text-[13px] text-[#555] font-medium text-right">{{ stock.turnover }}</td>
                            <td class="px-[20px] py-[12px] text-[12px] text-center w-[120px]">
                                <div class="opacity-0 group-hover:opacity-100 flex justify-center gap-3 transition">
                                    <button class="text-[#3b82f6] hover:text-[#2563eb] font-medium border border-transparent hover:border-[#bfdbfe] px-2 py-0.5 rounded">详情</button>
                                    <button class="text-[#dc2626] hover:text-[#c23616] font-medium border border-transparent hover:border-[#ffcccc] px-2 py-0.5 rounded">+ 自选</button>
                                </div>
                            </td>
                        </tr>
                        <tr v-if="!sectorStocksMap[selectedSector.name] || sectorStocksMap[selectedSector.name].length === 0">
                            <td colspan="7" class="px-[20px] py-[80px] text-center text-[#aaa] text-[13px]">
                                暂无该板块成分股数据，开发环境下只接入了局部 Mock 数据...
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
