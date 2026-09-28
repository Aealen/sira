<script setup lang="ts">
/** 屏2·标的分析：蜡烛图主图（红涨绿跌）+ 风险指标 + 估值分位 + 参考仓位。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton, ElIcon, ElTag } from 'element-plus'
import { Position, Switch } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/tag/style/css'
import { MARKET_LABELS, fmtPct, marketApi, trendClass, type Quote, type SearchItem } from '../api/market'

/** 指标类数值为小数口径（0.172 = 17.2%），展示时统一 ×100（区别于行情 API 的百分数值） */
const fmtRatioPct = (v: number) => `${v > 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
const fmtRatioPct1 = (v: number) => `${(v * 100).toFixed(1)}%`
const fmtDrawdownRatio = (v: number) => `-${(Math.abs(v) * 100).toFixed(1)}%`
import {
  analysisApi,
  calcMA,
  isMockActive,
  type AnalysisResult,
} from '../api/analysis'
import TargetSearchDialog from '../components/analysis/TargetSearchDialog.vue'
import RiskProfileDialog from '../components/analysis/RiskProfileDialog.vue'

use([GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, CandlestickChart, LineChart, BarChart, CanvasRenderer])

/* ---------------- 标的与区间 ---------------- */

const RANGES = [
  { key: '1m', label: '1月', days: 30 },
  { key: '3m', label: '3月', days: 90 },
  { key: '6m', label: '6月', days: 180 },
  { key: '1y', label: '1年', days: 365 },
  { key: '3y', label: '3年', days: 1095 },
] as const
type RangeKey = (typeof RANGES)[number]['key']

const rangeKey = ref<RangeKey>('1y')
const rangeDays = computed(() => RANGES.find((r) => r.key === rangeKey.value)!.days)

const route = useRoute()
const router = useRouter()

/** 当前标的（默认沪深300ETF 510300），支持 ?market=&code= 切换 */
const market = ref('etf')
const code = ref('510300')
const name = ref('')

function applyRouteQuery() {
  const m = String(route.query.market ?? '').trim()
  const c = String(route.query.code ?? '').trim()
  if (m && c && (m !== market.value || c !== code.value)) {
    market.value = m
    code.value = c
    name.value = ''
  }
}
applyRouteQuery()
watch(() => route.query, applyRouteQuery)

/* ---------------- 数据加载 ---------------- */

const result = ref<AnalysisResult | null>(null)
const loading = ref(false)
const mockOn = ref(false)

async function load() {
  loading.value = true
  try {
    result.value = await analysisApi.get(market.value, code.value, rangeDays.value)
    mockOn.value = isMockActive()
    if (result.value.name && result.value.name !== code.value) name.value = result.value.name
  } finally {
    loading.value = false
  }
}
watch([market, code, rangeDays], load, { immediate: true })

/* ---------------- 实时报价（当日价格，8s 轮询） ---------------- */

const quote = ref<Quote | null>(null)
let quoteTimer: ReturnType<typeof setInterval> | undefined

async function loadQuote() {
  try {
    quote.value = await marketApi.quote(market.value, code.value)
  } catch {
    /* 保留上一次报价 */
  }
}

watch([market, code], () => {
  quote.value = null
  void loadQuote()
}, { immediate: true })

onMounted(() => {
  quoteTimer = setInterval(loadQuote, 8000)
})
onBeforeUnmount(() => clearInterval(quoteTimer))

const searchVisible = ref(false)
const profileVisible = ref(false)

function onSelectTarget(s: SearchItem) {
  if (s.market !== market.value || s.code !== code.value) {
    market.value = s.market
    code.value = s.code
    name.value = s.name
    router.replace({ query: { ...route.query, market: s.market, code: s.code } })
  }
}

function goSimulation() {
  router.push({ path: '/simulation', query: { market: market.value, code: code.value } })
}

/* ---------------- 图表 ---------------- */

const bars = computed(() => result.value?.bars ?? [])
/** 场外基金（open=close 的净值 bar）自动切净值折线 */
const isNavLine = computed(() => bars.value.length > 0 && bars.value.every((b) => b.open === b.close))

/* ---------------- 当日分时视图（仅 A股/ETF 支持） ---------------- */

type ChartMode = 'day' | 'intraday'
const supportsIntraday = computed(() => ['a_stock', 'etf'].includes(market.value))
/** 默认当日分时（支持的市場），日K可切换；不支持的市场强制日K */
const chartMode = ref<ChartMode>('intraday')
const intradayBars = ref<{ time: string; price: number; avg?: number; volume: number }[]>([])
const intradayPrevClose = ref<number | null>(null)
let intradayTimer: ReturnType<typeof setInterval> | undefined

async function loadIntraday() {
  try {
    const d = await marketApi.intraday(market.value, code.value)
    intradayBars.value = d.bars
    intradayPrevClose.value = d.prev_close
  } catch {
    /* 保留上一次分时 */
  }
}

watch(supportsIntraday, (ok) => {
  if (!ok) chartMode.value = 'day'
}, { immediate: true })

watch(chartMode, (m) => {
  clearInterval(intradayTimer)
  intradayTimer = undefined
  if (m === 'intraday') {
    intradayBars.value = []
    void loadIntraday()
    intradayTimer = setInterval(loadIntraday, 8000)
  }
}, { immediate: true })
watch([market, code], () => {
  if (chartMode.value === 'intraday') {
    intradayBars.value = []
    void loadIntraday()
  }
})
onBeforeUnmount(() => clearInterval(intradayTimer))

const intradayOption = computed(() => {
  const prev = intradayPrevClose.value
  const bs = intradayBars.value
  const prices = bs.map((b) => b.price)
  const avgs = bs.map((b) => b.avg ?? b.price)
  // 主流理财 App 分时惯例：蓝价格线 + 橙均价线 + 灰昨收虚线（红绿留给K线涨跌）
  const PRICE_COLOR = '#2f6fd8'
  const AVG_COLOR = '#f5a623'
  return {
    grid: { left: 8, right: 8, top: 12, bottom: 24 },
    xAxis: { type: 'category' as const, data: bs.map((b) => b.time), axisLabel: { fontSize: 10, color: '#909399' } },
    yAxis: { type: 'value' as const, scale: true, axisLabel: { fontSize: 10, color: '#909399' }, splitLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } } },
    tooltip: {
      trigger: 'axis' as const,
      formatter: (ps: Array<{ dataIndex: number }>) => {
        const i = ps[0]?.dataIndex ?? 0
        const b = bs[i]
        if (!b) return ''
        const pct = prev ? ((b.price - prev) / prev * 100).toFixed(2) : '—'
        return `<div style="font-size:12px;line-height:1.8"><b>${b.time}</b><br>价格 <b style="color:${PRICE_COLOR}">${b.price}</b>（${pct}%）<br>均价 <span style="color:${AVG_COLOR}">${b.avg ?? '—'}</span></div>`
      },
    },
    series: [
      {
        name: '价格',
        type: 'line' as const,
        data: prices,
        showSymbol: false,
        lineStyle: { width: 1.5, color: PRICE_COLOR },
        areaStyle: { color: PRICE_COLOR, opacity: 0.07 },
        markLine: prev
          ? { symbol: 'none', silent: true, lineStyle: { type: 'dashed', color: '#909399', width: 1 }, label: { formatter: `昨收 ${prev}`, fontSize: 10, color: '#909399' }, data: [{ yAxis: prev }] }
          : undefined,
      },
      {
        name: '均价',
        type: 'line' as const,
        data: avgs,
        showSymbol: false,
        lineStyle: { width: 1, color: AVG_COLOR },
      },
    ],
  }
})

const dates = computed(() => bars.value.map((b) => b.date.slice(5)))
const closes = computed(() => bars.value.map((b) => b.close))
const ma20 = computed(() => calcMA(closes.value, 20))
const ma60 = computed(() => calcMA(closes.value, 60))

const UP = '#d03238'
const DOWN = '#2ead4b'
const MA20_COLOR = '#b86700'
const MA60_COLOR = '#38c8ff'

const fmtPrice = (v: number) => (v >= 5 ? v.toFixed(2) : v.toFixed(3))
const fmtVolume = (v: number) =>
  v >= 1e8 ? `${(v / 1e8).toFixed(2)}亿` : v >= 1e4 ? `${(v / 1e4).toFixed(1)}万` : String(Math.round(v))

interface TipParam {
  dataIndex: number
  seriesName?: string
}

function tooltipFormatter(params: unknown): string {
  const list = (Array.isArray(params) ? params : [params]) as TipParam[]
  if (!list.length || !bars.value.length) return ''
  const i = Math.min(list[0].dataIndex, bars.value.length - 1)
  const b = bars.value[i]
  const color = b.change_pct > 0 ? 'var(--sira-up)' : b.change_pct < 0 ? 'var(--sira-down)' : 'inherit'
  const lines: string[] = [`<b>${b.date}</b>`]
  if (isNavLine.value) {
    lines.push(`净值 <b style="color:${color}">${b.close.toFixed(4)}</b>`)
  } else {
    lines.push(
      `开 ${fmtPrice(b.open)}　收 <b style="color:${color}">${fmtPrice(b.close)}</b>`,
      `高 ${fmtPrice(b.high)}　低 ${fmtPrice(b.low)}`,
    )
  }
  lines.push(`涨跌幅 <b style="color:${color}">${fmtPct(b.change_pct)}</b>`)
  if (b.volume > 0) lines.push(`成交量 ${fmtVolume(b.volume)}`)
  const m20 = ma20.value[i]
  const m60 = ma60.value[i]
  if (m20 != null) lines.push(`MA20 <span style="color:${MA20_COLOR}">${fmtPrice(m20)}</span>`)
  if (m60 != null) lines.push(`MA60 <span style="color:${MA60_COLOR}">${fmtPrice(m60)}</span>`)
  return `<div style="font-size:12px;line-height:1.8">${lines.join('<br>')}</div>`
}

const legendOpt = {
  top: 4,
  right: 8,
  itemWidth: 14,
  itemHeight: 2,
  textStyle: { fontSize: 10, color: '#868685' },
  data: ['MA20', 'MA60'],
}

const chartOption = computed(() => {
  if (chartMode.value === 'intraday') return intradayOption.value
  if (isNavLine.value) {
    return {
      animation: false,
      legend: legendOpt,
      tooltip: { trigger: 'axis' as const, formatter: tooltipFormatter },
      grid: { left: 10, right: 16, top: 26, bottom: 26 },
      xAxis: {
        type: 'category' as const,
        data: dates.value,
        boundaryGap: false,
        axisLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } },
        axisTick: { show: false },
        axisLabel: { fontSize: 10, color: '#868685' },
      },
      yAxis: {
        type: 'value' as const,
        scale: true,
        splitLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } },
        axisLabel: { fontSize: 10, color: '#868685' },
      },
      series: [
        {
          name: '净值',
          type: 'line' as const,
          data: closes.value,
          showSymbol: false,
          lineStyle: { width: 2, color: '#0e0f0c' },
          areaStyle: { color: 'rgba(159, 232, 112, 0.25)' },
        },
        {
          name: 'MA20',
          type: 'line' as const,
          data: ma20.value,
          showSymbol: false,
          lineStyle: { width: 1.5, color: MA20_COLOR },
          itemStyle: { color: MA20_COLOR },
        },
        {
          name: 'MA60',
          type: 'line' as const,
          data: ma60.value,
          showSymbol: false,
          lineStyle: { width: 1.5, color: MA60_COLOR },
          itemStyle: { color: MA60_COLOR },
        },
      ],
    }
  }
  return {
    animation: false,
    legend: legendOpt,
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'cross' as const, label: { backgroundColor: '#454745' } },
      formatter: tooltipFormatter,
    },
    grid: [
      { left: 10, right: 16, top: 26, height: '56%' },
      { left: 10, right: 16, top: '74%', height: '17%' },
    ],
    xAxis: [
      {
        type: 'category' as const,
        data: dates.value,
        gridIndex: 0,
        axisLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      {
        type: 'category' as const,
        data: dates.value,
        gridIndex: 1,
        axisLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } },
        axisTick: { show: false },
        axisLabel: { fontSize: 10, color: '#868685' },
      },
    ],
    yAxis: [
      {
        type: 'value' as const,
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } },
        axisLabel: { fontSize: 10, color: '#868685' },
      },
      {
        type: 'value' as const,
        gridIndex: 1,
        splitNumber: 2,
        splitLine: { show: false },
        axisLabel: { fontSize: 10, color: '#868685', formatter: (v: number) => fmtVolume(v) },
      },
    ],
    series: [
      {
        name: '价格',
        type: 'candlestick' as const,
        data: bars.value.map((b) => [b.open, b.close, b.low, b.high]),
        // 红涨绿跌：阳线 UP / 阴线 DOWN
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      },
      {
        name: 'MA20',
        type: 'line' as const,
        data: ma20.value,
        showSymbol: false,
        lineStyle: { width: 1.5, color: MA20_COLOR },
        itemStyle: { color: MA20_COLOR },
      },
      {
        name: 'MA60',
        type: 'line' as const,
        data: ma60.value,
        showSymbol: false,
        lineStyle: { width: 1.5, color: MA60_COLOR },
        itemStyle: { color: MA60_COLOR },
      },
      {
        name: '成交量',
        type: 'bar' as const,
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: bars.value.map((b) => ({
          value: b.volume,
          itemStyle: { color: b.change_pct >= 0 ? 'rgba(208, 50, 56, 0.55)' : 'rgba(46, 173, 75, 0.55)' },
        })),
      },
    ],
  }
})

/* ---------------- 右列卡片 ---------------- */

const ind = computed(() => result.value?.indicators ?? null)
const valuation = computed(() => result.value?.valuation ?? null)
const refPos = computed(() => result.value?.ref_position ?? null)

const valuationRows = computed(() =>
  valuation.value
    ? [
        { key: 'pe', label: 'PE · 市盈率', value: valuation.value.pe.toFixed(1), pct: valuation.value.pe_percentile },
        { key: 'pb', label: 'PB · 市净率', value: valuation.value.pb.toFixed(2), pct: valuation.value.pb_percentile },
      ]
    : [],
)

const dash = (v: number | undefined | null, fmt: (v: number) => string) =>
  v == null || Number.isNaN(v) ? '—' : fmt(v)
</script>

<template>
  <div class="analysis">
    <!-- 顶部标的切换条 -->
    <div class="sira-card topbar">
      <span class="market-tag">{{ MARKET_LABELS[market] ?? market }}</span>
      <div class="chip">
        <span class="chip-name">{{ name || code }}</span>
        <span class="chip-code">{{ code }}</span>
      </div>
      <el-tag v-if="mockOn" class="mock-tag" size="small" effect="plain" title="后端 /api/analysis 未就绪，当前展示契约 mock 数据">
        演示数据
      </el-tag>
      <el-button size="small" round @click="searchVisible = true">
        <el-icon style="margin-right: 4px"><Switch /></el-icon>换标的
      </el-button>
      <div class="flex-space" />
      <el-button type="primary" round @click="goSimulation">
        模拟盘下单<el-icon style="margin-left: 4px"><Position /></el-icon>
      </el-button>
    </div>

    <!-- 实时报价条：当日价格与盘口摘要 -->
    <div class="sira-card quote-bar">
      <template v-if="quote">
        <span class="q-price" :class="trendClass(quote.change_pct)">{{ quote.price }}</span>
        <span class="q-pct" :class="trendClass(quote.change_pct)">
          {{ quote.change > 0 ? '+' : '' }}{{ quote.change }} · {{ fmtPct(quote.change_pct) }}
        </span>
        <el-tag v-if="quote.stale" size="small" type="info" effect="plain">延迟</el-tag>
        <span class="q-fields">
          今开 {{ quote.open || '—' }} · 最高 {{ quote.high || '—' }} · 最低 {{ quote.low || '—' }} · 昨收 {{ quote.prev_close || '—' }}
        </span>
        <div class="flex-space" />
        <span class="q-time">{{ quote.time || '—' }}</span>
      </template>
      <template v-else>
        <span class="q-loading">报价加载中…</span>
      </template>
    </div>

    <div class="grid">
      <!-- 主图：蜡烛图 + MA + 成交量（场外基金自动切净值折线） -->
      <div class="sira-card chart-card">
        <div class="chart-head">
          <div class="chart-title">
            {{ chartMode === 'intraday' ? '当日分时' : isNavLine ? '净值走势' : '价格走势' }}
            <span class="sub">{{ name || code }} · {{ MARKET_LABELS[market] ?? market }}</span>
          </div>
          <div class="view-tabs" v-if="supportsIntraday">
            <button type="button" class="pill" :class="{ active: chartMode === 'intraday' }" @click="chartMode = 'intraday'">分时</button>
            <button type="button" class="pill" :class="{ active: chartMode === 'day' }" @click="chartMode = 'day'">日K</button>
          </div>
          <div class="flex-space" />
          <div class="pills" v-if="chartMode === 'day'">
            <button
              v-for="r in RANGES"
              :key="r.key"
              type="button"
              class="pill"
              :class="{ active: rangeKey === r.key }"
              @click="rangeKey = r.key"
            >
              {{ r.label }}
            </button>
          </div>
        </div>
        <VChart v-if="chartMode === 'intraday' && intradayBars.length" class="chart" :option="intradayOption" autoresize />
        <div v-else-if="chartMode === 'intraday'" class="chart-placeholder">分时数据暂不可用（交易日 9:30–15:00 有数据）</div>
        <VChart v-else-if="bars.length" class="chart" :option="chartOption" autoresize />
        <div v-else-if="loading" class="chart-placeholder">加载行情数据中…</div>
        <div v-else class="chart-placeholder">暂无行情数据</div>
        <div v-if="isNavLine && bars.length && chartMode === 'day'" class="chart-note">场外基金按单位净值展示</div>
      </div>

      <!-- 右列 -->
      <div class="side">
        <!-- 风险指标 -->
        <div class="sira-card side-card">
          <div class="card-title">风险指标<span class="sub">区间内日频计算</span></div>
          <div class="ind-grid">
            <div class="ind">
              <div class="ind-label">年化波动率</div>
              <div class="ind-value">{{ dash(ind?.ann_volatility, fmtRatioPct1) }}</div>
            </div>
            <div class="ind">
              <div class="ind-label">最大回撤</div>
              <div class="ind-value up">{{ dash(ind?.max_drawdown, fmtDrawdownRatio) }}</div>
              <div class="ind-sub">{{ ind ? `${ind.max_drawdown_start} ~ ${ind.max_drawdown_end}` : '—' }}</div>
            </div>
            <div class="ind">
              <div class="ind-label">夏普比率</div>
              <div class="ind-value">{{ dash(ind?.sharpe, (v) => v.toFixed(2)) }}</div>
            </div>
            <div class="ind">
              <div class="ind-label">索提诺比率</div>
              <div class="ind-value">{{ dash(ind?.sortino, (v) => v.toFixed(2)) }}</div>
            </div>
          </div>
          <div class="divider" />
          <div class="daily-row">
            <div class="daily">
              <span class="d-label">日均收益</span>
              <span class="d-value" :class="ind ? trendClass(ind.daily_mean) : ''">
                {{ dash(ind?.daily_mean, fmtRatioPct) }}
              </span>
            </div>
            <div class="daily">
              <span class="d-label">日收益标准差</span>
              <span class="d-value">{{ dash(ind?.daily_std, fmtRatioPct) }}</span>
            </div>
            <div class="daily">
              <span class="d-label">最差单日</span>
              <span class="d-value" :class="ind ? trendClass(ind.worst_daily) : ''">
                {{ dash(ind?.worst_daily, fmtRatioPct) }}
              </span>
            </div>
          </div>
        </div>

        <!-- 估值分位 -->
        <div class="sira-card side-card">
          <div class="card-title">
            估值分位<span class="sub" v-if="valuation">近 {{ valuation.window_years }} 年</span>
          </div>
          <template v-if="valuation">
            <div v-for="row in valuationRows" :key="row.key" class="val-row">
              <div class="val-head">
                <span class="val-label">{{ row.label }}</span>
                <span class="val-value">{{ row.value }}<span class="val-pct"> · {{ row.pct.toFixed(1) }}% 分位</span></span>
              </div>
              <div class="pbar">
                <div class="pbar-track">
                  <div class="pbar-fill" :style="{ width: `${Math.min(Math.max(row.pct, 0), 100)}%` }" />
                  <div class="pbar-pin" :style="{ left: `${Math.min(Math.max(row.pct, 0), 100)}%` }" />
                </div>
                <div class="pbar-scale"><span>0</span><span>50</span><span>100</span></div>
              </div>
            </div>
          </template>
          <div v-else class="val-empty">估值数据暂不可用（仅支持 A 股个股）</div>
        </div>

        <!-- 参考仓位 -->
        <div class="sira-card side-card ref-card">
          <div class="ref-label">参考仓位上限</div>
          <div class="ref-value">
            {{ dash(refPos?.suggested_max_pct, (v) => v.toFixed(1)) }}<span class="ref-unit">%</span>
          </div>
          <div class="ref-formula">
            可承受回撤 {{ dash(refPos?.tolerance, (v) => `${(v * 100).toFixed(0)}%`) }} ÷ 历史最大回撤
            {{ dash(refPos?.hist_max_drawdown, (v) => `${(Math.abs(v) * 100).toFixed(1)}%`) }}
          </div>
          <button type="button" class="ref-link" @click="profileVisible = true">调整风险偏好设置 →</button>
        </div>
      </div>
    </div>

    <TargetSearchDialog v-model="searchVisible" @select="onSelectTarget" />
    <RiskProfileDialog v-model="profileVisible" @saved="load" />
  </div>
</template>

<style scoped>
.analysis {
  max-width: 1180px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 顶部标的切换条 */

/* 实时报价条 */
.quote-bar {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 12px 20px;
}
.q-price {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.q-pct {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.q-fields {
  color: var(--sira-body);
  font-size: 13px;
}
.q-time {
  color: var(--sira-mute);
  font-size: 12px;
}
.q-loading {
  color: var(--sira-mute);
  font-size: 13px;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
}

.market-tag {
  font-size: 12px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: 9999px;
  padding: 2px 10px;
  white-space: nowrap;
}

.chip {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.chip-name {
  font-size: 17px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-code {
  color: var(--sira-mute);
  font-size: 13px;
}

.mock-tag {
  flex-shrink: 0;
}

.flex-space {
  flex: 1;
}

/* 主体两栏 */
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 14px;
  align-items: start;
}

@media (max-width: 980px) {
  .grid {
    grid-template-columns: 1fr;
  }
}

/* 主图卡 */
.chart-card {
  padding: 16px 20px 12px;
}

.chart-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.view-tabs { display: flex; gap: 4px; background: var(--sira-canvas-soft); border-radius: 6px; padding: 3px; }

.chart-title {
  font-size: 15px;
  font-weight: 600;
}

.sub {
  font-size: 12px;
  color: var(--sira-mute);
  font-weight: 400;
  margin-left: 8px;
}

.pills {
  display: flex;
  gap: 6px;
}

.pill {
  border: 1px solid var(--sira-canvas-soft);
  background: var(--sira-canvas);
  color: var(--sira-body);
  border-radius: 9999px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}

.pill.active {
  background: var(--sira-ink);
  border-color: var(--sira-ink);
  color: #fff;
  font-weight: 500;
}

.chart {
  height: 440px;
}

.chart-placeholder {
  height: 440px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sira-mute);
}

.chart-note {
  font-size: 12px;
  color: var(--sira-mute);
  text-align: right;
  padding-top: 4px;
}

/* 右列 */
.side {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.side-card {
  padding: 18px 20px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 14px;
}

/* 指标卡 */
.ind-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 10px;
}

.ind-label {
  font-size: 12px;
  color: var(--sira-mute);
}

.ind-value {
  font-size: 20px;
  font-weight: 700;
  margin-top: 2px;
}

.ind-value.up {
  color: var(--sira-up);
}

.ind-sub {
  font-size: 11px;
  color: var(--sira-mute);
  margin-top: 2px;
}

.divider {
  height: 1px;
  background: var(--sira-canvas-soft);
  margin: 14px 0 12px;
}

.daily-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.daily {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.d-label {
  font-size: 11px;
  color: var(--sira-mute);
  white-space: nowrap;
}

.d-value {
  font-size: 14px;
  font-weight: 600;
}

/* 估值卡 */
.val-row {
  margin-bottom: 16px;
}

.val-row:last-child {
  margin-bottom: 2px;
}

.val-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}

.val-label {
  font-size: 13px;
  color: var(--sira-body);
}

.val-value {
  font-size: 15px;
  font-weight: 700;
}

.val-pct {
  font-size: 12px;
  color: var(--sira-mute);
  font-weight: 400;
}

.pbar-track {
  position: relative;
  height: 8px;
  border-radius: 9999px;
  background: var(--sira-canvas-soft);
}

.pbar-fill {
  height: 100%;
  border-radius: 9999px;
  background: linear-gradient(90deg, var(--sira-primary-pale), var(--sira-primary));
}

.pbar-pin {
  position: absolute;
  top: -4px;
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: var(--sira-ink);
  transform: translateX(-50%);
}

.pbar-scale {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--sira-mute);
  margin-top: 4px;
}

.val-empty {
  color: var(--sira-mute);
  font-size: 13px;
  padding: 10px 0 14px;
  background: var(--sira-canvas-soft);
  border-radius: var(--sira-radius-sm);
  text-align: center;
}

/* 参考仓位卡（淡绿底） */
.ref-card {
  background: var(--sira-primary-pale);
}

.ref-label {
  font-size: 13px;
  color: var(--sira-ink-deep);
}

.ref-value {
  font-size: 34px;
  font-weight: 800;
  color: var(--sira-ink-deep);
  margin: 4px 0 2px;
}

.ref-unit {
  font-size: 18px;
  font-weight: 600;
  margin-left: 2px;
}

.ref-formula {
  font-size: 12px;
  color: var(--sira-body);
}

.ref-link {
  border: none;
  background: none;
  padding: 0;
  margin-top: 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--sira-ink-deep);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}

.ref-link:hover {
  opacity: 0.75;
}
</style>
