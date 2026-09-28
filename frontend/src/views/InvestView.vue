<script setup lang="ts">
/** 屏5 · 定投与复盘：定投计划 CRUD / 暂停恢复 / 立即执行一期 + 复盘统计与纪律检查。 */
import { onMounted, ref } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElButton, ElIcon, ElMessage, ElMessageBox } from 'element-plus'
import InvestPlanDialog from '../components/invest/InvestPlanDialog.vue'
import InvestPlanDetailDialog from '../components/invest/InvestPlanDetailDialog.vue'
import { MARKET_LABELS, fmtPct, trendClass } from '../api/market'
import { fmtMoney } from '../api/sim'
import {
  FREQUENCY_LABELS,
  TAKE_PROFIT_LABELS,
  extractInvestError,
  fmtQty,
  investApi,
  type InvestPlan,
  type InvestReview,
} from '../api/invest'

const plans = ref<InvestPlan[]>([])
const review = ref<InvestReview | null>(null)
const loading = ref(true)
/** 正在「立即执行一期」的计划 id */
const executingId = ref<number | null>(null)

/* 新建 / 编辑对话框 */
const planDialogVisible = ref(false)
const editingPlan = ref<InvestPlan | null>(null)

/* 详情对话框 */
const detailVisible = ref(false)
const detailPlanId = ref<number | null>(null)
const detailDialog = ref<InstanceType<typeof InvestPlanDetailDialog> | null>(null)

async function refresh() {
  try {
    const [p, r] = await Promise.all([investApi.plans(), investApi.review()])
    plans.value = p
    review.value = r
  } catch {
    ElMessage.error('定投数据加载失败，请确认后端服务已启动')
  }
}

onMounted(async () => {
  await refresh()
  loading.value = false
})

function openCreate() {
  editingPlan.value = null
  planDialogVisible.value = true
}

function openEdit(p: InvestPlan) {
  editingPlan.value = p
  planDialogVisible.value = true
}

function openDetail(p: InvestPlan) {
  detailPlanId.value = p.id
  detailVisible.value = true
}

async function toggleStatus(p: InvestPlan) {
  const next = p.status === 'active' ? 'paused' : 'active'
  try {
    await investApi.update(p.id, { status: next })
    ElMessage.success(next === 'paused' ? `已暂停：${p.name}` : `已恢复扣款：${p.name}`)
    await refresh()
  } catch (e) {
    ElMessage.error(extractInvestError(e))
  }
}

async function removePlan(p: InvestPlan) {
  try {
    await ElMessageBox.confirm(
      `确认删除「${p.name}（${p.code}）」定投计划？已投入 ${fmtMoney(p.stats.invested)} 的执行历史将一并清除，不可恢复。`,
      '删除计划',
      { confirmButtonText: '确认删除', cancelButtonText: '再想想', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await investApi.remove(p.id)
    ElMessage.info(`已删除计划：${p.name}`)
    if (detailPlanId.value === p.id) detailVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error(extractInvestError(e))
  }
}

async function executeOne(p: InvestPlan) {
  if (executingId.value != null) return
  executingId.value = p.id
  try {
    const ex = await investApi.executeNow(p.id)
    const digits = p.market === 'etf' || p.market === 'fund' ? 3 : 2
    const unit = p.market === 'fund' ? '份' : '股'
    ElMessage.success(`已执行 1 期：${p.name} ${fmtQty(ex.quantity)} ${unit} @ ¥${ex.price.toFixed(digits)} · ${fmtMoney(ex.amount)}`)
    await refresh()
    // 详情开着时同步刷新执行历史
    if (detailVisible.value && detailPlanId.value === p.id) detailDialog.value?.reload()
  } catch (e) {
    ElMessage.error(extractInvestError(e))
  } finally {
    executingId.value = null
  }
}

/** 计划摘要行：频率 / 扣款日 / 金额 / 智能加减速 / 止盈 */
function summaryText(p: InvestPlan): string {
  const parts = [
    `${FREQUENCY_LABELS[p.frequency] ?? p.frequency}${p.frequency === 'monthly' ? ` ${p.day_of_month} 日` : ''}`,
    `${fmtMoney(p.amount)}/期`,
    p.smart_dca ? '智能加减速' : '固定金额',
  ]
  if (p.take_profit_mode !== 'none') {
    parts.push(`止盈 ${TAKE_PROFIT_LABELS[p.take_profit_mode]} ${p.take_profit_value}%`)
  }
  return parts.join(' · ')
}
</script>

<template>
  <div class="invest">
    <!-- 页头 -->
    <div class="page-head">
      <div>
        <div class="page-title">定投计划</div>
        <div class="page-sub">定期定额 · 智能加减速 · 复盘纪律检查</div>
      </div>
      <el-button type="primary" round size="large" @click="openCreate">
        <el-icon class="btn-plus"><Plus /></el-icon>
        新建定投计划
      </el-button>
    </div>

    <div class="main-grid">
      <!-- 左列：计划卡列表 -->
      <div class="col-left">
        <!-- 空状态 -->
        <div v-if="!loading && !plans.length" class="sira-card empty">
          <div class="empty-title">还没有定投计划 · 从一份纪律开始</div>
          <div class="empty-desc">定投的意义不在于择时，而在于纪律：定期定额买入看好的标的，用时间平滑成本</div>
          <el-button type="primary" round @click="openCreate">+ 新建定投计划</el-button>
          <div class="empty-caption">支持 A股 · ETF · 场外基金 · 港股 · 美股</div>
        </div>

        <div v-for="p in plans" :key="p.id" class="sira-card plan-card" @click="openDetail(p)">
          <div class="plan-top">
            <div class="target-chip">
              <span class="market-tag">{{ MARKET_LABELS[p.market] ?? p.market }}</span>
              <span class="t-name">{{ p.name }}</span>
              <span class="t-code">{{ p.code }}</span>
            </div>
            <span class="st-chip" :class="p.status === 'active' ? 'st-active' : 'st-paused'">
              {{ p.status === 'active' ? '扣款中' : '已暂停' }}
            </span>
          </div>

          <div class="plan-summary">{{ summaryText(p) }}</div>

          <div class="plan-stats">
            <span class="stat"><b>{{ p.stats.executions }}</b> 期</span>
            <span class="stat">投入 <b>{{ fmtMoney(p.stats.invested) }}</b></span>
            <span class="stat">市值 <b>{{ fmtMoney(p.stats.market_value) }}</b></span>
            <span class="stat">
              收益率 <b :class="trendClass(p.stats.pnl_pct)">{{ fmtPct(p.stats.pnl_pct) }}</b>
            </span>
            <span v-if="p.last_exec_date" class="last-exec">上次扣款 {{ p.last_exec_date.slice(0, 10) }}</span>
          </div>

          <div class="plan-ops" @click.stop>
            <button class="link" @click="openDetail(p)">详情</button>
            <button class="link" @click="openEdit(p)">编辑</button>
            <button class="link" @click="toggleStatus(p)">{{ p.status === 'active' ? '暂停' : '恢复' }}</button>
            <button class="exec-btn" :disabled="executingId != null" @click="executeOne(p)">
              {{ executingId === p.id ? '执行中…' : '立即执行一期' }}
            </button>
            <button class="link danger" @click="removePlan(p)">删除</button>
          </div>
        </div>
      </div>

      <!-- 右列：复盘统计 + 纪律检查 -->
      <div class="col-right">
        <div class="sira-card">
          <div class="card-head">复盘统计</div>
          <div class="review-stat">
            <div class="rs-label">总投入</div>
            <div class="rs-value">{{ review ? fmtMoney(review.total_invested) : '—' }}</div>
          </div>
          <div class="review-stat">
            <div class="rs-label">总市值</div>
            <div class="rs-value">{{ review ? fmtMoney(review.total_market_value) : '—' }}</div>
          </div>
          <div class="review-stat">
            <div class="rs-label">总收益率</div>
            <div class="rs-value" :class="review ? trendClass(review.total_pnl_pct) : ''">
              {{ review ? fmtPct(review.total_pnl_pct) : '—' }}
            </div>
          </div>
        </div>

        <div class="sira-card">
          <div class="card-head">纪律检查</div>
          <div v-if="review?.discipline.length" class="disc-list">
            <div v-for="(d, i) in review.discipline" :key="i" class="disc-item">
              <span class="disc-mark" :class="d.passed ? 'ok' : 'warn'">{{ d.passed ? '✓' : '✗' }}</span>
              <div>
                <div class="disc-check">{{ d.check }}</div>
                <div class="disc-detail">{{ d.detail }}</div>
              </div>
            </div>
          </div>
          <div v-else class="disc-empty">暂无检查项</div>
        </div>
      </div>
    </div>

    <div class="foot-caption">定投为模拟执行，不构成投资建议 · 学习用途</div>

    <!-- 新建 / 编辑对话框 -->
    <InvestPlanDialog v-model="planDialogVisible" :plan="editingPlan" @saved="refresh" />
    <!-- 详情对话框 -->
    <InvestPlanDetailDialog ref="detailDialog" v-model="detailVisible" :plan-id="detailPlanId" />
  </div>
</template>

<style scoped>
.invest { max-width: 1280px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }

/* 页头 */
.page-head { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.page-title { font-size: 20px; font-weight: 700; }
.page-sub { font-size: 13px; color: var(--sira-mute); margin-top: 4px; }
.btn-plus { margin-right: 6px; }

/* 主体两栏 */
.main-grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 14px; align-items: start; }
.col-left { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.col-right { position: sticky; top: 0; display: flex; flex-direction: column; gap: 14px; }

.card-head { font-size: 16px; font-weight: 600; margin-bottom: 12px; }

/* 空状态 */
.empty { text-align: center; padding: 56px 24px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.empty-title { font-size: 16px; font-weight: 600; }
.empty-desc { color: var(--sira-body); }
.empty-caption { font-size: 12px; color: var(--sira-mute); }

/* 计划卡 */
.plan-card { display: flex; flex-direction: column; gap: 10px; cursor: pointer; }
.plan-card:hover { outline: 1px solid var(--sira-canvas-soft); }

.plan-top { display: flex; align-items: center; gap: 10px; }
.target-chip { display: inline-flex; align-items: center; gap: 10px; background: var(--sira-canvas-soft); border-radius: 9999px; padding: 5px 14px; }
.market-tag { font-size: 12px; color: var(--sira-mute); background: var(--sira-canvas); border-radius: 9999px; padding: 1px 8px; white-space: nowrap; }
.t-name { font-weight: 600; }
.t-code { color: var(--sira-mute); font-size: 13px; }

/* 状态徽章：扣款中淡绿 / 已暂停灰 */
.st-chip { margin-left: auto; font-size: 12px; border-radius: 9999px; padding: 2px 10px; white-space: nowrap; }
.st-active { background: var(--sira-down-pale); color: var(--sira-down-deep); }
.st-paused { background: var(--sira-canvas-soft); color: var(--sira-mute); }

.plan-summary { font-size: 13px; color: var(--sira-body); }

.plan-stats { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 20px; font-size: 13px; color: var(--sira-body); font-variant-numeric: tabular-nums; }
.plan-stats b { font-size: 15px; color: var(--sira-ink); font-weight: 600; }
.plan-stats b.up { color: var(--sira-up); }
.plan-stats b.down { color: var(--sira-down); }
.last-exec { margin-left: auto; font-size: 12px; color: var(--sira-mute); }

.plan-ops { display: flex; align-items: center; gap: 16px; border-top: 1px solid var(--sira-canvas-soft); padding-top: 10px; }
.link { border: none; background: none; color: var(--sira-body); font-size: 13px; cursor: pointer; padding: 0; }
.link:hover { color: var(--sira-ink); font-weight: 600; }
.link.danger { margin-left: auto; color: var(--sira-up); }
.link.danger:hover { color: var(--sira-up-deep); }

.exec-btn {
  border: 1px solid var(--sira-primary); background: var(--sira-canvas); color: var(--sira-ink);
  border-radius: 9999px; padding: 4px 14px; font-size: 13px; font-weight: 600; cursor: pointer;
  transition: background 0.15s;
}
.exec-btn:hover:not(:disabled) { background: var(--sira-primary); }
.exec-btn:disabled { opacity: 0.5; cursor: default; }

/* 复盘统计 */
.review-stat { display: flex; justify-content: space-between; align-items: baseline; padding: 8px 0; border-bottom: 1px solid var(--sira-canvas-soft); }
.review-stat:last-child { border-bottom: none; }
.rs-label { font-size: 13px; color: var(--sira-mute); }
.rs-value { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
.rs-value.up { color: var(--sira-up); }
.rs-value.down { color: var(--sira-down); }

/* 纪律检查：✓ 绿勾 / ✗ 警示黄 */
.disc-list { display: flex; flex-direction: column; gap: 12px; }
.disc-item { display: flex; gap: 10px; align-items: flex-start; }
.disc-mark {
  flex: none; width: 20px; height: 20px; border-radius: 9999px;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700; margin-top: 1px;
}
.disc-mark.ok { background: var(--sira-down-pale); color: var(--sira-down-deep); }
.disc-mark.warn { background: #faf0d7; color: #b07c00; }
.disc-check { font-size: 13px; font-weight: 500; }
.disc-detail { font-size: 12px; color: var(--sira-mute); margin-top: 2px; }
.disc-empty { color: var(--sira-mute); font-size: 13px; padding: 8px 0; }

.foot-caption { text-align: center; font-size: 12px; color: var(--sira-mute); padding: 4px 0 12px; }

/* 窄屏：右列落到下方 */
@media (max-width: 1100px) {
  .main-grid { grid-template-columns: 1fr; }
  .col-right { position: static; }
}
</style>
