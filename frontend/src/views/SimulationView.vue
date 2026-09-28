<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElMessage, ElMessageBox, ElTable, ElTableColumn } from 'element-plus'
import 'element-plus/dist/index.css'
import SimOrderPanel from '../components/sim/SimOrderPanel.vue'
import { MARKET_LABELS, trendClass } from '../api/market'
import {
  fmtMoney,
  fmtSignedMoney,
  fmtTime,
  simApi,
  type SimAccount,
  type SimOpenOrder,
  type SimPosition,
  type SimRules,
  type SimTrade,
} from '../api/sim'

const REFRESH_MS = 8000

/** 页内 tab：持仓视图 ↔ 成交记录（不新增路由） */
const tab = ref<'main' | 'trades'>('main')

const account = ref<SimAccount | null>(null)
const rules = ref<SimRules | null>(null)
const loading = ref(true)
const allTrades = ref<SimTrade[]>([])
const tradesLoading = ref(false)
const mockMode = ref(false)

const orderPanel = ref<InstanceType<typeof SimOrderPanel> | null>(null)

const router = useRouter()

const marketValue = computed(() => account.value?.positions.reduce((s, p) => s + p.market_value, 0) ?? 0)

const tradeSummary = computed(() => {
  const filled = allTrades.value.filter(t => t.status === 'filled')
  return {
    count: filled.length,
    buyAmt: filled.filter(t => t.side === 'buy').reduce((s, t) => s + t.amount, 0),
    sellAmt: filled.filter(t => t.side === 'sell').reduce((s, t) => s + t.amount, 0),
    fee: filled.reduce((s, t) => s + t.fee, 0),
  }
})

/** 交易状态徽章 */
const STATUS_META: Record<string, { label: string; cls: string }> = {
  filled: { label: '已成交', cls: 'st-filled' },
  open: { label: '挂单中', cls: 'st-open' },
  pending: { label: '待成交', cls: 'st-open' },
  partial: { label: '部分成交', cls: 'st-open' },
  cancelled: { label: '已撤单', cls: 'st-mute' },
  rejected: { label: '已拒单', cls: 'st-reject' },
}
const statusOf = (s: string) => STATUS_META[s] ?? { label: s, cls: 'st-mute' }
const sideText = (s: string) => (s === 'buy' ? '买入' : '卖出')

async function refresh() {
  try {
    account.value = await simApi.account()
    mockMode.value = simApi.isMock()
  } catch {
    /* 行情源故障时保留上一次数据，不打断页面 */
  }
}

async function loadTrades() {
  tradesLoading.value = true
  try {
    allTrades.value = await simApi.trades(100)
  } catch {
    ElMessage.error('成交记录加载失败，请稍后重试')
  } finally {
    tradesLoading.value = false
  }
}

function switchTab(t: 'main' | 'trades') {
  tab.value = t
  if (t === 'trades') loadTrades()
}

/* —— 持仓操作：预填下单面板 —— */
function prefillBuy(p: SimPosition) {
  tab.value = 'main'
  orderPanel.value?.prefill({ market: p.market, code: p.code, name: p.name, price: p.price, side: 'buy' })
}
function prefillSell(p: SimPosition) {
  tab.value = 'main'
  orderPanel.value?.prefill({
    market: p.market,
    code: p.code,
    name: p.name,
    price: p.price,
    side: 'sell',
    quantity: p.available_qty > 0 ? p.available_qty : undefined,
  })
}

async function cancelOrder(o: SimOpenOrder) {
  try {
    await ElMessageBox.confirm(
      `确认撤单：${sideText(o.side)} ${o.name}（${o.code}）${o.quantity} 股 @ ¥${o.price.toFixed(2)}？`,
      '撤单确认',
      { confirmButtonText: '确认撤单', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await simApi.cancelOrder(o.id)
    ElMessage.success(`已撤单：${o.name}`)
    await refresh()
  } catch {
    ElMessage.error('撤单失败，请重试')
  }
}

async function resetAccount() {
  try {
    await ElMessageBox.confirm(
      '将清空全部持仓、挂单与交易记录，恢复初始资金 ¥1,000,000，不可恢复。确认重新开局？',
      '重新开局',
      { confirmButtonText: '确认重置', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await simApi.reset()
    allTrades.value = []
    ElMessage.success('已重新开局：初始资金 ¥1,000,000')
    await refresh()
  } catch {
    ElMessage.error('重置失败，请重试')
  }
}

let refreshTimer: ReturnType<typeof setInterval> | undefined

onMounted(async () => {
  loading.value = true
  await Promise.all([refresh(), simApi.rules().then(r => { rules.value = r }).catch(() => { /* 规则缺失时用默认 */ })])
  loading.value = false
  refreshTimer = setInterval(refresh, REFRESH_MS)
})

onBeforeUnmount(() => clearInterval(refreshTimer))
</script>

<template>
  <div class="sim">
    <!-- 页内 tab 切换：持仓视图 ↔ 成交记录 -->
    <div class="tab-bar">
      <div class="tabs">
        <button :class="{ on: tab === 'main' }" @click="switchTab('main')">持仓视图</button>
        <button :class="{ on: tab === 'trades' }" @click="switchTab('trades')">成交记录</button>
      </div>
      <span v-if="mockMode" class="mock-chip" title="后端 /api/sim 未就绪，当前为演示数据">演示数据 · /api/sim 未就绪</span>
    </div>

    <!-- ================= 持仓视图 ================= -->
    <template v-if="tab === 'main'">
      <!-- 账户总览四指标卡 -->
      <div class="stat-grid">
        <div class="sira-card stat">
          <div class="stat-label">总资产</div>
          <div class="stat-value">{{ account ? fmtMoney(account.total_asset) : '—' }}</div>
        </div>
        <div class="sira-card stat">
          <div class="stat-label">持仓市值</div>
          <div class="stat-value">{{ account ? fmtMoney(marketValue) : '—' }}</div>
        </div>
        <div class="sira-card stat">
          <div class="stat-label">可用资金</div>
          <div class="stat-value">{{ account ? fmtMoney(account.cash) : '—' }}</div>
        </div>
        <div class="sira-card stat">
          <div class="stat-label">累计盈亏</div>
          <div class="stat-value" :class="account ? trendClass(account.total_pnl) : ''">
            {{ account ? fmtSignedMoney(account.total_pnl) : '—' }}
          </div>
        </div>
      </div>

      <div class="main-grid">
        <div class="col-left">
          <!-- 持仓表 -->
          <div class="sira-card">
            <div class="card-head">持仓（{{ account?.positions.length ?? 0 }}）</div>
            <el-table v-if="account?.positions.length" :data="account.positions" class="pos-table">
              <el-table-column label="标的" min-width="150">
                <template #default="{ row }">
                  <div class="cell-name">{{ row.name }}</div>
                  <div class="cell-sub">{{ row.code }} · {{ MARKET_LABELS[row.market] ?? row.market }}</div>
                </template>
              </el-table-column>
              <el-table-column label="持仓 / 可卖" min-width="110" align="right">
                <template #default="{ row }">
                  <div>{{ row.quantity }}</div>
                  <div class="cell-sub" :class="{ warn: row.available_qty < row.quantity }">可卖 {{ row.available_qty }}</div>
                </template>
              </el-table-column>
              <el-table-column label="成本" min-width="80" align="right">
                <template #default="{ row }">{{ row.avg_cost.toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="现价" min-width="80" align="right">
                <template #default="{ row }">{{ row.price.toFixed(row.market === 'etf' ? 3 : 2) }}</template>
              </el-table-column>
              <el-table-column label="市值" min-width="110" align="right">
                <template #default="{ row }">{{ fmtMoney(row.market_value) }}</template>
              </el-table-column>
              <el-table-column label="浮动盈亏" min-width="130" align="right">
                <template #default="{ row }">
                  <div :class="trendClass(row.unrealized_pnl)">{{ fmtSignedMoney(row.unrealized_pnl) }}</div>
                  <div class="cell-sub" :class="trendClass(row.unrealized_pnl)">{{ row.unrealized_pnl_pct.toFixed(2) }}%</div>
                </template>
              </el-table-column>
              <el-table-column label="操作" min-width="140" align="center">
                <template #default="{ row }">
                  <el-button size="small" plain round class="op-buy" @click="prefillBuy(row as SimPosition)">加仓</el-button>
                  <el-button
                    size="small"
                    plain
                    round
                    class="op-sell"
                    :disabled="row.available_qty <= 0"
                    :title="row.available_qty <= 0 ? 'T+1 当日买入暂不可卖' : ''"
                    @click="prefillSell(row as SimPosition)"
                  >减仓</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-else class="empty-box">
              <template v-if="loading">加载账户数据中…</template>
              <template v-else>
                <div class="empty-title">空仓中 · 初始资金已就绪</div>
                <div class="empty-hint">先去自选行情挑选标的，回这里下第一笔模拟单</div>
                <el-button type="primary" plain round size="small" class="empty-btn" @click="router.push('/watchlist')">
                  去自选行情
                </el-button>
              </template>
            </div>
          </div>

          <!-- 挂单列表（有挂单时显示） -->
          <div v-if="account?.open_orders.length" class="sira-card">
            <div class="card-head">挂单（{{ account.open_orders.length }}）</div>
            <div class="grid-row order-head">
              <span>标的</span><span>方向</span><span>限价</span><span>数量</span><span>时间</span><span />
            </div>
            <div v-for="o in account.open_orders" :key="o.id" class="grid-row">
              <span>
                <span class="cell-name">{{ o.name }}</span>
                <span class="cell-sub">{{ o.code }}</span>
              </span>
              <span :class="o.side === 'buy' ? 'up' : 'down'">{{ sideText(o.side) }}</span>
              <span>¥{{ o.price.toFixed(2) }}</span>
              <span>{{ o.quantity }} 股</span>
              <span class="cell-sub">{{ fmtTime(o.created_at) }}</span>
              <el-button size="small" plain round @click="cancelOrder(o)">撤单</el-button>
            </div>
          </div>

          <!-- 最近交易 -->
          <div class="sira-card">
            <div class="card-head with-link">
              <span>最近交易</span>
              <button class="link" @click="switchTab('trades')">查看全部 →</button>
            </div>
            <template v-if="account?.recent_trades.length">
              <div class="grid-row trade-head">
                <span>时间</span><span>标的</span><span>方向</span><span>价格</span><span>数量</span><span>费用</span><span>状态</span>
              </div>
              <div v-for="t in account.recent_trades" :key="t.id" class="grid-row trade-row">
                <span class="cell-sub">{{ fmtTime(t.created_at) }}</span>
                <span>
                  <span class="cell-name">{{ t.name }}</span>
                  <span class="cell-sub">{{ t.code }}</span>
                </span>
                <span :class="t.side === 'buy' ? 'up' : 'down'">{{ sideText(t.side) }}</span>
                <span>¥{{ t.price.toFixed(2) }}</span>
                <span>{{ t.quantity }}</span>
                <span>{{ fmtMoney(t.fee) }}</span>
                <span class="st-chip" :class="statusOf(t.status).cls">{{ statusOf(t.status).label }}</span>
              </div>
            </template>
            <div v-else class="empty-box">暂无交易记录</div>
          </div>

          <!-- 危险区：重新开局 -->
          <div class="sira-card danger-card">
            <div class="danger-text">
              <div class="danger-title">重新开局</div>
              <div class="danger-desc">清空全部持仓、挂单与交易记录，恢复初始资金 ¥1,000,000，不可恢复</div>
            </div>
            <el-button type="danger" plain round @click="resetAccount">重新开局</el-button>
          </div>
        </div>

        <!-- 右侧下单面板 -->
        <div class="col-right">
          <SimOrderPanel
            ref="orderPanel"
            :positions="account?.positions ?? []"
            :rules="rules"
            @submitted="refresh"
          />
        </div>
      </div>
    </template>

    <!-- ================= 成交记录 ================= -->
    <template v-else>
      <div class="sira-card">
        <div class="card-head with-link">
          <span>成交记录（最近 100 笔）</span>
          <button class="link" :disabled="tradesLoading" @click="loadTrades">{{ tradesLoading ? '加载中…' : '刷新' }}</button>
        </div>
        <el-table v-if="allTrades.length" :data="allTrades" class="pos-table">
          <el-table-column label="时间" min-width="110">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="标的" min-width="150">
            <template #default="{ row }">
              <div class="cell-name">{{ row.name }}</div>
              <div class="cell-sub">{{ row.code }} · {{ MARKET_LABELS[row.market] ?? row.market }}</div>
            </template>
          </el-table-column>
          <el-table-column label="方向" min-width="70" align="center">
            <template #default="{ row }">
              <span :class="row.side === 'buy' ? 'up' : 'down'">{{ sideText(row.side) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="价格" min-width="90" align="right">
            <template #default="{ row }">¥{{ row.price.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="数量" min-width="90" align="right">
            <template #default="{ row }">{{ row.quantity }}</template>
          </el-table-column>
          <el-table-column label="金额" min-width="120" align="right">
            <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
          </el-table-column>
          <el-table-column label="费用" min-width="90" align="right">
            <template #default="{ row }">{{ fmtMoney(row.fee) }}</template>
          </el-table-column>
          <el-table-column label="状态" min-width="90" align="center">
            <template #default="{ row }">
              <span class="st-chip" :class="statusOf(row.status).cls">{{ statusOf(row.status).label }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div v-else class="empty-box">{{ tradesLoading ? '加载中…' : '暂无成交记录' }}</div>
        <div class="summary-bar">
          共 {{ tradeSummary.count }} 笔成交 · 买入 {{ fmtMoney(tradeSummary.buyAmt) }} · 卖出 {{ fmtMoney(tradeSummary.sellAmt) }} · 总费用 {{ fmtMoney(tradeSummary.fee) }}
        </div>
      </div>
    </template>

    <div class="foot-caption">模拟盘 · {{ REFRESH_MS / 1000 }} 秒自动轮询 · 学习用途，非投资建议</div>
  </div>
</template>

<style scoped>
.sim { max-width: 1280px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }

/* 页内 tab */
.tab-bar { display: flex; align-items: center; gap: 12px; }
.tabs { display: inline-flex; gap: 4px; background: var(--sira-canvas-soft); border-radius: 9999px; padding: 4px; }
.tabs button { border: none; background: transparent; border-radius: 9999px; padding: 7px 20px; font-size: 14px; color: var(--sira-body); cursor: pointer; }
.tabs button.on { background: var(--sira-canvas); color: var(--sira-ink); font-weight: 600; box-shadow: 0 1px 3px rgba(14, 15, 12, 0.12); }
.mock-chip { font-size: 12px; color: var(--sira-body); background: var(--sira-canvas); border: 1px dashed var(--sira-mute); border-radius: 9999px; padding: 3px 10px; }

/* 四指标卡 */
.stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.stat { padding: 16px 20px; }
.stat-label { font-size: 12px; color: var(--sira-mute); margin-bottom: 6px; }
.stat-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }

/* 主体两栏 */
.main-grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 14px; align-items: start; }
.col-left { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.col-right { position: sticky; top: 0; }

.card-head { font-size: 16px; font-weight: 600; margin-bottom: 10px; }
.card-head.with-link { display: flex; justify-content: space-between; align-items: center; }
.link { border: none; background: none; color: var(--sira-body); font-size: 13px; cursor: pointer; padding: 0; }
.link:hover { color: var(--sira-ink); font-weight: 600; }
.link:disabled { color: var(--sira-mute); cursor: default; }

/* 表格微调（对齐 Wise 风格） */
:deep(.el-table) { --el-table-border-color: var(--sira-canvas-soft); --el-table-header-bg-color: transparent; }
:deep(.el-table th.el-table__cell) { color: var(--sira-mute); font-weight: 500; }
:deep(.el-table .el-table__cell) { padding: 8px 0; }

.cell-name { font-weight: 500; }
.cell-sub { font-size: 12px; color: var(--sira-mute); }
.cell-sub.warn { color: var(--sira-up); }

/* 持仓操作按钮：加仓红 / 减仓绿（红涨绿跌） */
.op-buy { --el-button-text-color: var(--sira-up); --el-button-border-color: var(--sira-up-pale); --el-button-hover-text-color: var(--sira-up); --el-button-hover-border-color: var(--sira-up); --el-button-hover-bg-color: var(--sira-up-pale); }
.op-sell { --el-button-text-color: var(--sira-down-deep); --el-button-border-color: var(--sira-down-pale); --el-button-hover-text-color: var(--sira-down-deep); --el-button-hover-border-color: var(--sira-down); --el-button-hover-bg-color: var(--sira-down-pale); }

/* 挂单 / 最近交易列表行 */
.grid-row { display: grid; grid-template-columns: minmax(120px, 1.4fr) 52px 84px 84px 70px 76px; gap: 8px; align-items: center; padding: 10px 8px; border-bottom: 1px solid var(--sira-canvas-soft); }
.grid-row:last-child { border-bottom: none; }
.trade-head, .order-head { font-size: 12px; color: var(--sira-mute); border-bottom: 1px solid var(--sira-canvas-soft); padding: 6px 8px; }
.trade-row { grid-template-columns: 76px minmax(110px, 1.4fr) 44px 76px 64px 76px 72px; }

/* 状态徽章 */
.st-chip { font-size: 12px; border-radius: 9999px; padding: 2px 10px; display: inline-block; }
.st-filled { background: var(--sira-primary-pale); color: var(--sira-ink-deep); }
.st-open { background: var(--sira-canvas-soft); color: var(--sira-body); }
.st-mute { background: var(--sira-canvas-soft); color: var(--sira-mute); }
.st-reject { background: var(--sira-up-pale); color: var(--sira-up); }

/* 危险区 */
.danger-card { display: flex; justify-content: space-between; align-items: center; gap: 16px; background: var(--sira-up-pale); }
.danger-title { font-weight: 600; color: var(--sira-up-deep); margin-bottom: 4px; }
.danger-desc { font-size: 12px; color: var(--sira-body); }

/* 成交记录汇总条 */
.summary-bar { margin-top: 12px; background: var(--sira-canvas-soft); border-radius: var(--sira-radius-sm); padding: 10px 14px; font-size: 13px; color: var(--sira-body); font-variant-numeric: tabular-nums; }

.empty-box { text-align: center; color: var(--sira-mute); padding: 36px 0; }
.empty-title { font-size: 15px; font-weight: 600; color: var(--sira-body); }
.empty-hint { font-size: 12px; margin: 6px 0 14px; }
.empty-btn { margin-top: 2px; }
.foot-caption { text-align: center; font-size: 12px; color: var(--sira-mute); padding: 4px 0 12px; }

/* 窄屏：下单面板落到下方 */
@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .main-grid { grid-template-columns: 1fr; }
  .col-right { position: static; }
}
</style>
