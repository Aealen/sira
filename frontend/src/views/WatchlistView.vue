<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Delete, Plus, Search, TrendCharts } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { LineChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import {
  MARKET_LABELS,
  marketApi,
  watchlistApi,
  fmtPct,
  trendClass,
  type Bar,
  type IndexQuote,
  type SearchItem,
  type WatchItem,
} from '../api/market'

use([GridComponent, TooltipComponent, LineChart, CanvasRenderer])

const REFRESH_MS = 8000
const INDEX_REFRESH_MS = 60000

const router = useRouter()

const items = ref<WatchItem[]>([])
const loading = ref(false)
const keyword = ref('')
const searchResults = ref<SearchItem[]>([])
const searching = ref(false)
const searchInputEl = ref<HTMLInputElement | null>(null)
/** 市场概览条：主要指数实时行情 */
const indices = ref<IndexQuote[]>([])
/** 展开行：`${market}/${code}` -> K线数据 */
const expandedKey = ref('')
const expandedBars = ref<Bar[]>([])

const grouped = computed(() => {
  const map = new Map<string, WatchItem[]>()
  for (const it of items.value) {
    const list = map.get(it.market) ?? []
    list.push(it)
    map.set(it.market, list)
  }
  return [...map.entries()]
})

let searchTimer: ReturnType<typeof setTimeout> | undefined

async function refresh() {
  try {
    items.value = await watchlistApi.list()
  } catch {
    /* 行情源故障时保留上一次数据，不打断页面 */
  }
}

async function refreshIndices() {
  try {
    const data = await marketApi.indices()
    if (data.length) indices.value = data
  } catch {
    /* 概览失败保留上次数据 */
  }
}

function onKeywordInput() {
  clearTimeout(searchTimer)
  const q = keyword.value.trim()
  if (!q) {
    searchResults.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      searchResults.value = await marketApi.search(q, 8)
    } catch {
      searchResults.value = []
    } finally {
      searching.value = false
    }
  }, 400)
}

async function addItem(s: SearchItem) {
  try {
    await watchlistApi.add({ market: s.market, code: s.code, name: s.name })
    keyword.value = ''
    searchResults.value = []
    ElMessage.success(`已添加自选：${s.name}`)
    await refresh()
  } catch (e) {
    ElMessage.error('添加失败')
  }
}

async function removeItem(market: string, code: string, name: string) {
  try {
    await ElMessageBox.confirm(
      `确定将 ${name}(${code}) 移出自选？不影响模拟盘持仓。`,
      '移出自选',
      {
        confirmButtonText: '移出',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return // 用户取消，不执行删除
  }
  try {
    await watchlistApi.remove(market, code)
    ElMessage.info(`已移出：${name}`)
    await refresh()
  } catch {
    ElMessage.error('移出失败')
  }
}

/** 点击行情行 → 跳转屏2 分析页 */
function goAnalysis(it: WatchItem) {
  router.push({ path: '/analysis', query: { market: it.market, code: it.code } })
}

async function toggleExpand(it: WatchItem) {
  const key = `${it.market}/${it.code}`
  if (expandedKey.value === key) {
    expandedKey.value = ''
    return
  }
  expandedKey.value = key
  expandedBars.value = []
  try {
    const k = await marketApi.kline(it.market, it.code, 120)
    expandedBars.value = k.bars
  } catch {
    /* K线失败时仅收起 */
  }
}

const lineOption = computed(() => ({
  grid: { left: 8, right: 8, top: 8, bottom: 20 },
  xAxis: { type: 'category' as const, data: expandedBars.value.map(b => b.date.slice(5)), axisLabel: { fontSize: 10, color: '#868685' } },
  yAxis: { type: 'value' as const, scale: true, axisLabel: { fontSize: 10, color: '#868685' }, splitLine: { lineStyle: { color: 'var(--sira-canvas-soft)' } } },
  tooltip: { trigger: 'axis' as const },
  series: [{
    type: 'line' as const,
    data: expandedBars.value.map(b => b.close),
    showSymbol: false,
    lineStyle: { width: 2, color: '#0e0f0c' },
    areaStyle: { color: 'rgba(159, 232, 112, 0.25)' },
  }],
}))

let refreshTimer: ReturnType<typeof setInterval> | undefined
let indexTimer: ReturnType<typeof setInterval> | undefined

onMounted(async () => {
  loading.value = true
  await refresh()
  loading.value = false
  refreshTimer = setInterval(refresh, REFRESH_MS)
  refreshIndices()
  indexTimer = setInterval(refreshIndices, INDEX_REFRESH_MS)
})

onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  clearInterval(indexTimer)
})
</script>

<template>
  <div class="watchlist">
    <!-- 市场概览条：主要指数（接口返回空时整条隐藏） -->
    <div v-if="indices.length" class="sira-card index-bar">
      <div v-for="idx in indices" :key="idx.code" class="index-cell">
        <div class="index-name">{{ idx.name }}</div>
        <div class="index-body">
          <span class="index-price">{{ idx.price.toFixed(2) }}</span>
          <span class="index-pct" :class="trendClass(idx.change_pct)">{{ fmtPct(idx.change_pct) }}</span>
        </div>
      </div>
    </div>

    <!-- 搜索卡 -->
    <div class="sira-card search-card">
      <el-icon :size="18" color="#868685"><Search /></el-icon>
      <input
        ref="searchInputEl"
        v-model="keyword"
        class="search-input"
        placeholder="搜索代码 / 名称 · A股 · ETF · 场外基金 · 港股 · 美股"
        @input="onKeywordInput"
      >
      <div v-if="searching" class="search-hint">搜索中…</div>
      <div v-else-if="keyword.trim() && !searchResults.length" class="search-hint">未找到匹配标的</div>
    </div>

    <!-- 搜索结果 -->
    <div v-if="searchResults.length" class="sira-card result-card">
      <div
        v-for="s in searchResults"
        :key="s.market + s.code"
        class="result-row"
        @click="addItem(s)"
      >
        <span class="market-tag">{{ MARKET_LABELS[s.market] ?? s.market }}</span>
        <span class="result-name">{{ s.name }}</span>
        <span class="result-code">{{ s.code }}</span>
        <el-icon class="add-icon"><Plus /></el-icon>
      </div>
    </div>

    <!-- 空状态（对应原型空态集·自选） -->
    <div v-if="!loading && !items.length" class="sira-card empty">
      <div class="empty-title">还没有自选标的</div>
      <div class="empty-desc">搜索代码或名称，覆盖 A股 · ETF · 场外基金 · 港股 · 美股</div>
      <el-button type="primary" round @click="searchInputEl?.focus()">搜索添加第一个标的 →</el-button>
      <div class="empty-caption">或使用上方搜索框</div>
    </div>

    <!-- 自选列表（按市场分组） -->
    <div v-for="[market, list] in grouped" :key="market" class="sira-card group-card">
      <div class="group-head">{{ MARKET_LABELS[market] ?? market }} · {{ list.length }} 只</div>
      <div v-for="it in list" :key="it.code">
        <div class="row" @click="goAnalysis(it)">
          <div class="row-name">
            <div class="name">{{ it.name }}</div>
            <div class="code">{{ it.code }}</div>
          </div>
          <div class="row-price">
            <div class="price">{{ it.market === 'fund' ? `净值 ${it.price}` : it.price }}</div>
            <div class="pct" :class="trendClass(it.change_pct)">{{ fmtPct(it.change_pct) }}</div>
          </div>
          <el-tag v-if="it.stale" size="small" type="info" effect="plain">延迟</el-tag>
          <div class="row-time">{{ it.time || '—' }}</div>
          <el-icon class="row-chart" title="展开迷你K线" @click.stop="toggleExpand(it)"><TrendCharts /></el-icon>
          <el-icon class="row-del" title="移出自选" @click.stop="removeItem(it.market, it.code, it.name)"><Delete /></el-icon>
        </div>
        <div v-if="expandedKey === `${it.market}/${it.code}`" class="mini-chart">
          <VChart v-if="expandedBars.length" :option="lineOption" autoresize style="height: 160px" />
          <div v-else class="chart-loading">加载 K 线中…</div>
        </div>
      </div>
    </div>

    <div class="foot-caption">
      免费数据源约 3 秒延迟 · {{ REFRESH_MS / 1000 }} 秒自动轮询 · 学习用途，非投资建议
    </div>
  </div>
</template>

<style scoped>
.watchlist { max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }

.index-bar { display: flex; align-items: stretch; gap: 10px; padding: 12px 20px; }
.index-cell { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; padding: 0 6px; }
.index-cell + .index-cell { border-left: 1px solid var(--sira-canvas-soft); }
.index-name { font-size: 12px; color: var(--sira-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.index-body { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.index-price { font-size: 16px; font-weight: 600; }
.index-pct { font-size: 13px; font-weight: 600; }

.search-card { display: flex; align-items: center; gap: 10px; padding: 14px 20px; }
.search-input { flex: 1; border: none; outline: none; font-size: 14px; color: var(--sira-ink); background: transparent; }
.search-hint { font-size: 12px; color: var(--sira-mute); }

.result-card { padding: 8px; }
.result-row { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: var(--sira-radius-sm); cursor: pointer; }
.result-row:hover { background: var(--sira-canvas-soft); }
.market-tag { font-size: 12px; color: var(--sira-mute); background: var(--sira-canvas-soft); border-radius: 9999px; padding: 1px 8px; }
.result-name { font-weight: 500; }
.result-code { color: var(--sira-mute); font-size: 13px; margin-left: auto; }
.add-icon { color: var(--sira-body); }

.empty { text-align: center; padding: 56px 24px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.empty-title { font-size: 16px; font-weight: 600; }
.empty-desc { color: var(--sira-body); }
.empty-caption { font-size: 12px; color: var(--sira-mute); }

.group-card { padding: 16px 20px; }
.group-head { font-size: 12px; color: var(--sira-mute); margin-bottom: 6px; }
.row { display: flex; align-items: center; gap: 14px; padding: 10px 8px; border-radius: var(--sira-radius-sm); cursor: pointer; }
.row:hover { background: var(--sira-canvas-soft); }
.row-name { min-width: 170px; }
.name { font-weight: 500; }
.code { font-size: 12px; color: var(--sira-mute); }
.row-price { margin-left: auto; text-align: right; min-width: 110px; }
.price { font-size: 16px; font-weight: 600; }
.pct { font-size: 13px; font-weight: 600; }
.row-time { font-size: 12px; color: var(--sira-mute); min-width: 70px; text-align: right; }
.row-del { color: var(--sira-mute); cursor: pointer; }
.row-del:hover { color: var(--sira-up); }
.row-chart { color: var(--sira-mute); cursor: pointer; }
.row-chart:hover { color: var(--sira-ink); }

.mini-chart { padding: 8px 12px; background: var(--sira-canvas-soft); border-radius: var(--sira-radius-sm); margin: 4px 0 8px; }
.chart-loading { text-align: center; color: var(--sira-mute); padding: 40px 0; }

.foot-caption { text-align: center; font-size: 12px; color: var(--sira-mute); padding: 4px 0 12px; }
</style>
