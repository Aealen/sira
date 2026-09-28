<script setup lang="ts">
/** 定投计划详情对话框：执行历史列表 + 收益走势迷你图（累计投入 vs 市值双线）。 */
import { computed, ref, watch } from 'vue'
import { ElDialog, ElMessage, ElTable, ElTableColumn } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import { MARKET_LABELS, fmtPct, trendClass } from '../../api/market'
import { fmtMoney } from '../../api/sim'
import { FREQUENCY_LABELS, fmtQty, investApi, type InvestPlanDetail } from '../../api/invest'

use([GridComponent, LegendComponent, TooltipComponent, LineChart, CanvasRenderer])

const props = defineProps<{ modelValue: boolean; planId: number | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const detail = ref<InvestPlanDetail | null>(null)
const loading = ref(false)

watch(visible, (v) => {
  if (v && props.planId != null) reload()
})

async function reload() {
  if (props.planId == null) return
  loading.value = true
  try {
    detail.value = await investApi.detail(props.planId)
  } catch {
    ElMessage.error('计划详情加载失败，请稍后重试')
    detail.value = null
  } finally {
    loading.value = false
  }
}

defineExpose({ reload })

/** 价格小数位：ETF / 场外基金保留 3 位 */
const priceDigits = computed(() => {
  const m = detail.value?.market
  return m === 'etf' || m === 'fund' ? 3 : 2
})

const qtyUnit = computed(() => (detail.value?.market === 'fund' ? '份' : '股'))

/** 双线图数据：按执行时点累计投入 与 累计份额×当期价格（市值近似） */
const curve = computed(() => {
  let invested = 0
  let quantity = 0
  return (detail.value?.executions ?? []).map((e) => {
    invested += e.amount
    quantity += e.quantity
    return { date: e.exec_date.slice(0, 10), invested, value: quantity * e.price }
  })
})

const chartOption = computed(() => ({
  grid: { left: 8, right: 8, top: 28, bottom: 20 },
  legend: { top: 0, right: 0, itemWidth: 14, textStyle: { fontSize: 11, color: '#868685' } },
  xAxis: {
    type: 'category' as const,
    data: curve.value.map(p => p.date.slice(5)),
    axisLabel: { fontSize: 10, color: '#868685' },
  },
  yAxis: {
    type: 'value' as const,
    scale: true,
    axisLabel: { fontSize: 10, color: '#868685' },
    splitLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } },
  },
  tooltip: {
    trigger: 'axis' as const,
    valueFormatter: (v: number) => fmtMoney(v),
  },
  series: [
    {
      name: '累计投入',
      type: 'line' as const,
      data: curve.value.map(p => Math.round(p.invested * 100) / 100),
      showSymbol: false,
      step: 'end' as const,
      lineStyle: { width: 1.5, color: '#868685', type: 'dashed' },
      itemStyle: { color: '#868685' },
    },
    {
      name: '市值',
      type: 'line' as const,
      data: curve.value.map(p => Math.round(p.value * 100) / 100),
      showSymbol: false,
      lineStyle: { width: 2, color: '#0e0f0c' },
      itemStyle: { color: '#0e0f0c' },
      areaStyle: { color: 'rgba(159, 232, 112, 0.25)' },
    },
  ],
}))
</script>

<template>
  <el-dialog v-model="visible" title="计划详情" width="720px">
    <div v-if="loading && !detail" class="loading-box">加载计划详情中…</div>
    <template v-else-if="detail">
      <!-- 计划摘要 -->
      <div class="summary">
        <div class="sum-target">
          <span class="market-tag">{{ MARKET_LABELS[detail.market] ?? detail.market }}</span>
          <span class="sum-name">{{ detail.name }}</span>
          <span class="sum-code">{{ detail.code }}</span>
        </div>
        <div class="sum-line">
          {{ FREQUENCY_LABELS[detail.frequency] }}{{ detail.frequency === 'monthly' ? ` ${detail.day_of_month} 日` : '' }}
          · {{ fmtMoney(detail.amount) }}/期
          · {{ detail.smart_dca ? '智能加减速' : '固定金额' }}
          <template v-if="detail.take_profit_mode !== 'none'">
            · 止盈：{{ detail.take_profit_mode === 'target_return' ? '收益率' : '估值分位' }} {{ detail.take_profit_value }}%
          </template>
        </div>
        <div class="sum-stats">
          <span>已投 <b>{{ detail.stats.executions }}</b> 期</span>
          <span>投入 <b>{{ fmtMoney(detail.stats.invested) }}</b></span>
          <span>市值 <b>{{ fmtMoney(detail.stats.market_value) }}</b></span>
          <span>收益率 <b :class="trendClass(detail.stats.pnl_pct)">{{ fmtPct(detail.stats.pnl_pct) }}</b></span>
          <span v-if="detail.last_exec_date" class="sum-last">上次扣款 {{ detail.last_exec_date.slice(0, 10) }}</span>
        </div>
      </div>

      <!-- 收益走势：累计投入 vs 市值 -->
      <div class="chart-box">
        <div class="block-head">收益走势</div>
        <VChart v-if="curve.length" :option="chartOption" autoresize style="height: 220px" />
        <div v-else class="chart-empty">暂无执行记录 · 在计划卡上点「立即执行一期」开始第一笔定投</div>
      </div>

      <!-- 执行历史 -->
      <div class="block-head">执行历史（{{ detail.executions.length }}）</div>
      <el-table v-if="detail.executions.length" :data="detail.executions" class="hist-table" max-height="300">
        <el-table-column label="日期" min-width="100">
          <template #default="{ row }">{{ row.exec_date.slice(0, 10) }}</template>
        </el-table-column>
        <el-table-column label="价格" min-width="90" align="right">
          <template #default="{ row }">¥{{ row.price.toFixed(priceDigits) }}</template>
        </el-table-column>
        <el-table-column label="数量" min-width="90" align="right">
          <template #default="{ row }">{{ fmtQty(row.quantity) }} {{ qtyUnit }}</template>
        </el-table-column>
        <el-table-column label="金额" min-width="110" align="right">
          <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="费用" min-width="90" align="right">
          <template #default="{ row }">{{ fmtMoney(row.fee) }}</template>
        </el-table-column>
      </el-table>
      <div v-else class="chart-empty">暂无执行记录 · 计划创建后尚未扣款，在计划卡上点「立即执行一期」开始第一笔定投</div>
    </template>
    <div v-else class="loading-box">计划详情为空</div>
  </el-dialog>
</template>

<style scoped>
:deep(.el-dialog) { border-radius: 20px; }

.loading-box, .chart-empty { text-align: center; color: var(--sira-mute); padding: 32px 0; }

.summary { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.sum-target { display: flex; align-items: center; gap: 10px; }
.market-tag { font-size: 12px; color: var(--sira-mute); background: var(--sira-canvas-soft); border-radius: 9999px; padding: 1px 8px; }
.sum-name { font-size: 17px; font-weight: 600; }
.sum-code { color: var(--sira-mute); font-size: 13px; }
.sum-line { font-size: 13px; color: var(--sira-body); }
.sum-stats { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--sira-body); font-variant-numeric: tabular-nums; }
.sum-stats b { color: var(--sira-ink); }
.sum-last { margin-left: auto; font-size: 12px; color: var(--sira-mute); }

.block-head { font-size: 14px; font-weight: 600; margin: 14px 0 8px; }

.chart-box { background: var(--sira-canvas-soft); border-radius: var(--sira-radius-sm); padding: 10px 12px 6px; }

:deep(.el-table) { --el-table-border-color: var(--sira-canvas-soft); --el-table-header-bg-color: transparent; }
:deep(.el-table th.el-table__cell) { color: var(--sira-mute); font-weight: 500; }
:deep(.el-table .el-table__cell) { padding: 7px 0; }
</style>
