<script setup>
/**
 * F10 简版：个股基本面快照卡片。
 * 数据由 services/f10_service.py 提供（push2 quote + CompanySurvey）。
 */
import { ref, watch, onMounted } from 'vue'
import { api } from '../api/client'

const props = defineProps({
    code: { type: String, required: true },
    name: { type: String, default: '' },
})

const data    = ref(null)
const loading = ref(false)
const errMsg  = ref('')
const profileExpanded = ref(false)

async function load() {
    if (!props.code) return
    loading.value = true
    errMsg.value = ''
    try {
        const res = await api.getStockF10(props.code)
        if (!res.ok) {
            errMsg.value = res.error || 'F10 数据加载失败'
            data.value = null
        } else {
            data.value = res.data || null
        }
    } finally {
        loading.value = false
    }
}

watch(() => props.code, load)
onMounted(load)

// ============ 工具 ============
function fmt(v, decimals = 2, suffix = '') {
    if (v == null || v === '' || (typeof v === 'number' && !isFinite(v))) return '—'
    const n = +v
    if (!isFinite(n)) return String(v)
    return n.toFixed(decimals) + suffix
}
function fmtYi(v) {
    if (v == null) return '—'
    const n = +v
    if (!isFinite(n)) return '—'
    if (n >= 10000) return (n / 10000).toFixed(2) + ' 万亿'
    return n.toFixed(2) + ' 亿'
}
</script>

<template>
    <div class="flex flex-col h-full bg-white">
        <!-- Header -->
        <div class="h-[44px] px-[14px] border-b border-[#e5e7eb] flex items-center gap-[12px] shrink-0">
            <div class="flex items-baseline gap-[8px]">
                <span class="text-[15px] font-bold text-[#111]">{{ name || data?.name || code }}</span>
                <span class="text-[11px] text-[#94a3b8] font-mono tabular-nums">{{ code }}</span>
            </div>
            <span v-if="data?.industry" class="text-[11px] text-[#666] bg-[#f5f5f5] px-[6px] py-[1px] rounded-[3px]">
                {{ data.industry }}
            </span>
            <span v-if="data?.area" class="text-[11px] text-[#666] bg-[#f5f5f5] px-[6px] py-[1px] rounded-[3px]">
                {{ data.area }}
            </span>
            <button @click="load" :disabled="loading"
                    class="ml-auto text-[11px] text-[#666] hover:text-[#dc2626] disabled:opacity-50 px-[8px] py-[3px] rounded-[3px] transition">
                {{ loading ? '加载中...' : '↻ 刷新' }}
            </button>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto px-[16px] py-[12px]">
            <div v-if="loading && !data" class="h-full flex items-center justify-center text-[13px] text-[#999]">
                正在加载基本面数据...
            </div>
            <div v-else-if="errMsg && !data" class="h-full flex items-center justify-center text-[13px] text-[#999]">
                {{ errMsg }}
            </div>
            <div v-else-if="!data" class="h-full flex items-center justify-center text-[13px] text-[#999]">
                暂无数据
            </div>

            <template v-else>
                <!-- 公司全称 -->
                <div v-if="data.fullName" class="mb-[14px]">
                    <div class="text-[10px] text-[#999] mb-[2px]">公司全称</div>
                    <div class="text-[14px] font-semibold text-[#111]">{{ data.fullName }}</div>
                </div>

                <!-- 关键财务（4 列网格）-->
                <div class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">关键财务</div>
                    <div class="grid grid-cols-4 gap-[10px]">
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">市盈率(动)</div>
                            <div class="text-[14px] font-bold tabular-nums"
                                 :class="data.peDynamic == null ? 'text-[#bbb]' : (data.peDynamic < 0 ? 'text-[#16a34a]' : 'text-[#111]')">
                                {{ fmt(data.peDynamic) }}
                            </div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">市净率</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.pb) }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">ROE 加权</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.roe, 2, '%') }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">股息率</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.dividendYield, 2, '%') }}</div>
                        </div>
                    </div>
                </div>

                <!-- 市值 / 股本（4 列网格）-->
                <div class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">市值 / 股本</div>
                    <div class="grid grid-cols-4 gap-[10px]">
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">总市值</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmtYi(data.totalMarketCap) }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">流通市值</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmtYi(data.floatMarketCap) }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">总股本</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.totalShares, 2, ' 亿') }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">流通股本</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.floatShares, 2, ' 亿') }}</div>
                        </div>
                    </div>
                </div>

                <!-- 最新业绩（营收/净利润 + 同比；带 4 期趋势）-->
                <div v-if="data.revenue != null || data.netProfit != null" class="mb-[16px]">
                    <div class="flex items-baseline gap-[8px] mb-[6px]">
                        <span class="text-[10px] text-[#999] uppercase tracking-wider">最新业绩</span>
                        <span v-if="data.latestPeriod" class="text-[10px] text-[#888] tabular-nums">{{ data.latestPeriod }}</span>
                    </div>
                    <div class="grid grid-cols-2 gap-[10px]">
                        <!-- 营收 -->
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[12px] py-[10px]">
                            <div class="flex items-baseline justify-between mb-[4px]">
                                <span class="text-[10px] text-[#888]">营业收入</span>
                                <span v-if="data.revenueYoy != null"
                                      class="text-[10px] font-semibold tabular-nums"
                                      :class="data.revenueYoy >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                                    {{ data.revenueYoy >= 0 ? '+' : '' }}{{ data.revenueYoy.toFixed(2) }}%
                                </span>
                            </div>
                            <div class="text-[16px] font-bold tabular-nums text-[#111]">{{ fmtYi(data.revenue) }}</div>
                            <div v-if="data.revenueTrend?.length > 1" class="mt-[4px] flex gap-[3px]">
                                <span v-for="(p, i) in data.revenueTrend" :key="i"
                                      class="text-[9px] text-[#999] tabular-nums"
                                      :title="`${p.period}: ${fmtYi(p.value)}${p.yoy != null ? ' (' + (p.yoy >= 0 ? '+' : '') + p.yoy.toFixed(1) + '%)' : ''}`">
                                    <span class="inline-block w-[8px] h-[8px] rounded-sm"
                                          :style="{ background: p.yoy == null ? '#cbd5e1' : (p.yoy >= 0 ? '#fca5a5' : '#86efac') }"></span>
                                </span>
                            </div>
                        </div>
                        <!-- 净利润 -->
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[12px] py-[10px]">
                            <div class="flex items-baseline justify-between mb-[4px]">
                                <span class="text-[10px] text-[#888]">归母净利润</span>
                                <span v-if="data.netProfitYoy != null"
                                      class="text-[10px] font-semibold tabular-nums"
                                      :class="data.netProfitYoy >= 0 ? 'text-[#dc2626]' : 'text-[#16a34a]'">
                                    {{ data.netProfitYoy >= 0 ? '+' : '' }}{{ data.netProfitYoy.toFixed(2) }}%
                                </span>
                            </div>
                            <div class="text-[16px] font-bold tabular-nums"
                                 :class="data.netProfit != null && data.netProfit < 0 ? 'text-[#16a34a]' : 'text-[#111]'">
                                {{ fmtYi(data.netProfit) }}
                            </div>
                            <div v-if="data.netProfitTrend?.length > 1" class="mt-[4px] flex gap-[3px]">
                                <span v-for="(p, i) in data.netProfitTrend" :key="i"
                                      class="text-[9px] text-[#999] tabular-nums"
                                      :title="`${p.period}: ${fmtYi(p.value)}${p.yoy != null ? ' (' + (p.yoy >= 0 ? '+' : '') + p.yoy.toFixed(1) + '%)' : ''}`">
                                    <span class="inline-block w-[8px] h-[8px] rounded-sm"
                                          :style="{ background: p.yoy == null ? '#cbd5e1' : (p.yoy >= 0 ? '#fca5a5' : '#86efac') }"></span>
                                </span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 盈利能力（毛利率/净利率/资产负债率/EPS）-->
                <div v-if="data.grossMargin != null || data.netMargin != null || data.debtRatio != null || data.eps != null"
                     class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">盈利能力</div>
                    <div class="grid grid-cols-4 gap-[10px]">
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">毛利率</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.grossMargin, 2, '%') }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">净利率</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.netMargin, 2, '%') }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">资产负债率</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.debtRatio, 2, '%') }}</div>
                        </div>
                        <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[8px]">
                            <div class="text-[10px] text-[#888] mb-[2px]">每股收益</div>
                            <div class="text-[14px] font-bold tabular-nums text-[#111]">{{ fmt(data.eps, 2, ' 元') }}</div>
                        </div>
                    </div>
                </div>

                <!-- 十大流通股东 -->
                <div v-if="data.topShareholders?.length" class="mb-[16px]">
                    <div class="flex items-baseline gap-[8px] mb-[6px]">
                        <span class="text-[10px] text-[#999] uppercase tracking-wider">十大流通股东</span>
                        <span v-if="data.shareholdersDate" class="text-[10px] text-[#888] tabular-nums">{{ data.shareholdersDate }}</span>
                    </div>
                    <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] overflow-hidden">
                        <table class="w-full text-[11px] tabular-nums">
                            <thead class="bg-[#f5f5f5] text-[#666]">
                                <tr>
                                    <th class="text-left px-[10px] py-[5px] font-normal w-[28px]">#</th>
                                    <th class="text-left px-[10px] py-[5px] font-normal">股东名称</th>
                                    <th class="text-right px-[10px] py-[5px] font-normal">持股比例</th>
                                    <th class="text-right px-[10px] py-[5px] font-normal">持股数</th>
                                    <th class="text-right px-[10px] py-[5px] font-normal">变动</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(h, i) in data.topShareholders" :key="i"
                                    class="border-t border-[#f0f0f0]">
                                    <td class="px-[10px] py-[5px] text-[#888]">{{ i + 1 }}</td>
                                    <td class="px-[10px] py-[5px] text-[#111] truncate max-w-[260px]">{{ h.name || '—' }}</td>
                                    <td class="px-[10px] py-[5px] text-right font-semibold">{{ h.ratio != null ? h.ratio.toFixed(2) + '%' : '—' }}</td>
                                    <td class="px-[10px] py-[5px] text-right text-[#475569]">{{ h.shares != null ? h.shares.toFixed(2) + ' 亿股' : '—' }}</td>
                                    <td class="px-[10px] py-[5px] text-right text-[#475569]">{{ h.change || '—' }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- 股东户数趋势 -->
                <div v-if="data.shareholderCountTrend?.length" class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">股东户数（最近 {{ data.shareholderCountTrend.length }} 期）</div>
                    <div class="flex flex-wrap gap-[10px]">
                        <div v-for="(p, i) in data.shareholderCountTrend" :key="i"
                             class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[10px] py-[6px] flex-1 min-w-[110px]">
                            <div class="text-[10px] text-[#888]">{{ p.period || '—' }}</div>
                            <div class="text-[13px] font-bold tabular-nums text-[#111]">
                                {{ p.count != null ? p.count.toLocaleString() : '—' }}
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 分红送配（最近 5 次）-->
                <div v-if="data.dividends?.length" class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">分红送配</div>
                    <div class="bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] divide-y divide-[#f0f0f0]">
                        <div v-for="(d, i) in data.dividends" :key="i"
                             class="flex items-baseline gap-[10px] px-[12px] py-[6px] text-[11px]">
                            <span class="text-[#888] font-mono tabular-nums w-[80px] shrink-0">{{ d.year || '—' }}</span>
                            <span class="text-[#475569] flex-1 truncate">{{ d.scheme || '—' }}</span>
                            <span v-if="d.exDate" class="text-[#888] tabular-nums shrink-0">除权 {{ d.exDate }}</span>
                        </div>
                    </div>
                </div>

                <!-- 概念题材 -->
                <div v-if="data.concepts?.length" class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">概念题材</div>
                    <div class="flex flex-wrap gap-[6px]">
                        <span v-for="c in data.concepts" :key="c"
                              class="text-[11px] text-[#7c3aed] bg-[#f5f3ff] border border-[#ddd6fe] px-[8px] py-[2px] rounded-[3px] font-semibold">
                            {{ c }}
                        </span>
                    </div>
                </div>

                <!-- 上市日期 / 注册资本 / 法人 / 员工数 -->
                <div class="mb-[16px] flex flex-wrap items-baseline gap-x-[20px] gap-y-[4px] text-[11px] text-[#475569]">
                    <span v-if="data.listedDate"><span class="text-[#888] mr-[6px]">上市日期</span><span class="font-semibold tabular-nums">{{ data.listedDate }}</span></span>
                    <span v-if="data.regCapital != null"><span class="text-[#888] mr-[6px]">注册资本</span><span class="font-semibold tabular-nums">{{ fmtYi(data.regCapital) }}</span></span>
                    <span v-if="data.chairman"><span class="text-[#888] mr-[6px]">董事长</span><span class="font-semibold">{{ data.chairman }}</span></span>
                    <span v-if="data.employees"><span class="text-[#888] mr-[6px]">员工数</span><span class="font-semibold tabular-nums">{{ data.employees }}</span></span>
                </div>

                <!-- 主营业务 -->
                <div v-if="data.mainBusiness" class="mb-[16px]">
                    <div class="text-[10px] text-[#999] mb-[6px] uppercase tracking-wider">主营业务</div>
                    <div class="text-[12px] text-[#475569] leading-relaxed bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[12px] py-[10px]">
                        {{ data.mainBusiness }}
                    </div>
                </div>

                <!-- 公司简介（折叠/展开）-->
                <div v-if="data.profile" class="mb-[16px]">
                    <div class="flex items-center justify-between mb-[6px]">
                        <span class="text-[10px] text-[#999] uppercase tracking-wider">公司简介</span>
                        <button @click="profileExpanded = !profileExpanded"
                                class="text-[11px] text-[#dc2626] hover:underline">
                            {{ profileExpanded ? '收起' : '展开全文' }}
                        </button>
                    </div>
                    <div class="text-[12px] text-[#475569] leading-relaxed bg-[#fafafa] border border-[#f0f0f0] rounded-[5px] px-[12px] py-[10px]"
                         :class="profileExpanded ? '' : 'line-clamp-3'">
                        {{ data.profile }}
                    </div>
                </div>
            </template>
        </div>
    </div>
</template>

<style scoped>
.line-clamp-3 {
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
</style>
