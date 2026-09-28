<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElButton, ElDialog, ElInputNumber, ElOption, ElSelect } from 'element-plus'
import { MARKET_LABELS, marketApi, type SearchItem } from '../../api/market'
import {
  estimateFee,
  extractRejectDetail,
  fmtMoney,
  fmtTime,
  ruleBadgeText,
  simApi,
  type MarketRule,
  type PlaceOrderPayload,
  type Side,
  type SimPosition,
} from '../../api/sim'

const props = defineProps<{ positions: SimPosition[]; rules: Record<string, MarketRule> | null }>()
const emit = defineEmits<{ submitted: [] }>()

/** 下拉选项：持仓标的在前，远程搜索结果补充 */
interface Option {
  market: string
  code: string
  name: string
  price?: number
}

const selected = ref('')
const side = ref<Side>('buy')
const orderType = ref<'market' | 'limit'>('market')
const limitPrice = ref<number | null>(null)
const quantity = ref<number>(100)
/** 非持仓标的的现价（来自行情接口） */
const quotePrice = ref<number | null>(null)
const quoteFailed = ref(false)
const searching = ref(false)
const searchResults = ref<SearchItem[]>([])
const submitting = ref(false)

/* —— 第一步：确认弹层 / 第二步：回执弹层 —— */
const confirmVisible = ref(false)
const receiptVisible = ref(false)

/** 回执数据：成交（含费用明细）/ 挂单 / 拒单（422 detail） */
type Receipt =
  | {
      kind: 'filled'
      side: Side
      market: string
      code: string
      name: string
      price: number
      quantity: number
      amount: number
      fee: number
      commission: number
      stampTax: number
      time: string
    }
  | { kind: 'open'; side: Side; market: string; code: string; name: string; price: number; quantity: number; time: string }
  | { kind: 'rejected'; detail: string }

const receipt = ref<Receipt | null>(null)

const receiptTitle = computed(() =>
  receipt.value?.kind === 'filled' ? '成交回执' : receipt.value?.kind === 'open' ? '委托回执' : '下单失败',
)

let searchTimer: ReturnType<typeof setTimeout> | undefined

const options = computed<Option[]>(() => {
  const map = new Map<string, Option>()
  for (const p of props.positions) {
    map.set(`${p.market}/${p.code}`, { market: p.market, code: p.code, name: p.name, price: p.price })
  }
  for (const s of searchResults.value) {
    if (!map.has(`${s.market}/${s.code}`)) map.set(`${s.market}/${s.code}`, { market: s.market, code: s.code, name: s.name })
  }
  return [...map.values()]
})

const selectedOpt = computed(() => options.value.find(o => `${o.market}/${o.code}` === selected.value))
const heldPrice = computed(() => props.positions.find(p => `${p.market}/${p.code}` === selected.value)?.price ?? null)
/** 市价单参考价：优先持仓现价，其次行情接口价 */
const refPrice = computed(() => heldPrice.value ?? quotePrice.value)
const rule = computed(() => (selectedOpt.value ? props.rules?.[selectedOpt.value.market] : undefined))
const minLot = computed(() => {
  const lot = rule.value?.min_lot
  return lot && lot > 0 ? lot : 100
})

const effPrice = computed(() => (orderType.value === 'limit' ? limitPrice.value : refPrice.value))
const amount = computed(() => (effPrice.value != null && quantity.value > 0 ? effPrice.value * quantity.value : 0))
const fee = computed(() => estimateFee(side.value, amount.value))

const canSubmit = computed(
  () =>
    !!selectedOpt.value
    && Number.isFinite(quantity.value)
    && quantity.value > 0
    && (orderType.value === 'market' ? refPrice.value != null : (limitPrice.value ?? 0) > 0),
)

function minLotFor(market: string) {
  const lot = props.rules?.[market]?.min_lot
  return lot && lot > 0 ? lot : 100
}

function onRemoteSearch(q: string) {
  clearTimeout(searchTimer)
  const query = q.trim()
  if (!query) {
    searchResults.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      searchResults.value = await marketApi.search(query, 8)
    } catch {
      searchResults.value = []
    } finally {
      searching.value = false
    }
  }, 350)
}

watch(selected, async k => {
  limitPrice.value = null
  quotePrice.value = null
  quoteFailed.value = false
  const opt = options.value.find(o => `${o.market}/${o.code}` === k)
  if (!opt) return
  quantity.value = minLotFor(opt.market)
  if (heldPrice.value == null) {
    // 非持仓标的：从行情接口取现价（市价单预估与 mock 撮合都依赖它）
    try {
      const q = await marketApi.quote(opt.market, opt.code)
      if (`${opt.market}/${opt.code}` === selected.value) {
        quotePrice.value = q.price
        simApi.seedQuote(opt.market, opt.code, opt.name, q.price)
      }
    } catch {
      if (`${opt.market}/${opt.code}` === selected.value) quoteFailed.value = true
    }
  }
})

/** 持仓表“加仓/减仓”入口：预填方向与标的 */
function prefill(p: { market: string; code: string; name: string; price?: number; side: Side; quantity?: number }) {
  selected.value = `${p.market}/${p.code}`
  side.value = p.side
  orderType.value = 'market'
  quotePrice.value = p.price ?? null
  quantity.value = p.quantity && p.quantity > 0 ? p.quantity : minLotFor(p.market)
}
defineExpose({ prefill })

/** 清仓后标的从持仓消失且不在搜索结果中时，重置选择 */
watch(
  () => props.positions,
  () => {
    if (!selected.value) return
    const known =
      !!selectedOpt.value || searchResults.value.some(s => `${s.market}/${s.code}` === selected.value)
    if (!known) selected.value = ''
  },
)

/** 第一步：弹出确认层，不直接下单 */
function openConfirm() {
  if (!canSubmit.value || submitting.value) return
  confirmVisible.value = true
}

/** 第二步：用户在确认层点“确认”后才真正调用 placeOrder */
async function confirmSubmit() {
  const opt = selectedOpt.value
  if (!opt || !canSubmit.value || submitting.value) return
  submitting.value = true
  try {
    const payload: PlaceOrderPayload = {
      market: opt.market,
      code: opt.code,
      side: side.value,
      order_type: orderType.value,
      quantity: quantity.value,
    }
    if (orderType.value === 'limit') payload.price = Number(limitPrice.value)
    const res = await simApi.placeOrder(payload)
    confirmVisible.value = false
    if (res.trade) {
      const t = res.trade
      const est = estimateFee(t.side, t.amount)
      receipt.value = {
        kind: 'filled',
        side: t.side,
        market: t.market,
        code: t.code,
        name: t.name,
        price: t.price,
        quantity: t.quantity,
        amount: t.amount,
        fee: t.fee,
        commission: t.commission ?? est.commission,
        stampTax: t.stamp_tax ?? est.stamp,
        time: t.created_at,
      }
    } else {
      receipt.value = {
        kind: 'open',
        side: side.value,
        market: opt.market,
        code: opt.code,
        name: opt.name,
        price: orderType.value === 'limit' ? Number(limitPrice.value) : (refPrice.value ?? 0),
        quantity: quantity.value,
        time: new Date().toISOString(),
      }
    }
    receiptVisible.value = true
    quantity.value = minLot.value
    limitPrice.value = null
    emit('submitted')
  } catch (e) {
    confirmVisible.value = false
    receipt.value = { kind: 'rejected', detail: extractRejectDetail(e) }
    receiptVisible.value = true
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="sira-card order-panel">
    <div class="panel-title">模拟下单</div>

    <el-select
      v-model="selected"
      filterable
      remote
      clearable
      :remote-method="onRemoteSearch"
      :loading="searching"
      placeholder="搜索代码 / 名称，或选持仓标的"
      size="large"
      style="width: 100%"
    >
      <el-option
        v-for="o in options"
        :key="o.market + o.code"
        :value="o.market + '/' + o.code"
        :label="o.name + ' ' + o.code"
      >
        <span>{{ o.name }}</span>
        <span class="opt-meta">{{ o.code }}<template v-if="o.price"> · 现价 {{ o.price }}</template></span>
      </el-option>
    </el-select>

    <!-- 方向 toggle：买入红描边 / 卖出绿 -->
    <div class="side-toggle">
      <button type="button" class="side-btn buy" :class="{ active: side === 'buy' }" @click="side = 'buy'">买入</button>
      <button type="button" class="side-btn sell" :class="{ active: side === 'sell' }" @click="side = 'sell'">卖出</button>
    </div>

    <!-- 委托类型 pill：市价 / 限价 -->
    <div class="type-toggle">
      <button type="button" :class="{ active: orderType === 'market' }" @click="orderType = 'market'">市价</button>
      <button type="button" :class="{ active: orderType === 'limit' }" @click="orderType = 'limit'">限价</button>
    </div>

    <div v-if="orderType === 'limit'" class="field">
      <label>委托价格</label>
      <el-input-number v-model="limitPrice" :min="0.01" :step="0.01" :controls="false" placeholder="限价" style="width: 100%" />
      <div v-if="refPrice != null" class="field-hint">现价参考 ¥{{ refPrice.toFixed(2) }}</div>
    </div>

    <div class="field">
      <label>数量（股）<template v-if="minLot > 1">· {{ minLot }} 的整数倍</template></label>
      <el-input-number v-model="quantity" :min="minLot" :step="minLot" step-strictly style="width: 100%" />
    </div>

    <!-- 市场规则徽章（来自 GET /api/sim/rules） -->
    <div v-if="selectedOpt" class="rule-chip">{{ ruleBadgeText(selectedOpt.market, rule) }}</div>
    <div v-if="orderType === 'market' && quoteFailed" class="field-hint warn">现价获取失败，可改用限价委托</div>

    <!-- 预估 -->
    <div class="calc">
      <div class="calc-row"><span>预估金额</span><b>{{ amount > 0 ? fmtMoney(amount) : '—' }}</b></div>
      <div class="calc-row"><span>预估费用</span><b>{{ amount > 0 ? fmtMoney(fee.total) : '—' }}</b></div>
      <div class="calc-sub">佣金 万2.5（最低¥5）· 印花税 万5（仅卖出）</div>
    </div>

    <el-button
      type="primary"
      round
      size="large"
      class="submit-btn"
      :disabled="!canSubmit"
      :loading="submitting"
      @click="openConfirm"
    >
      确认下单
    </el-button>
    <div class="panel-foot">模拟盘 · 学习用途，非投资建议</div>

    <!-- ============ 第一步：下单确认弹层 ============ -->
    <el-dialog
      v-model="confirmVisible"
      title="确认下单"
      width="380px"
      append-to-body
      align-center
      :close-on-click-modal="!submitting"
      :show-close="!submitting"
    >
      <div class="dlg">
        <!-- 订单摘要 -->
        <div class="sum-row">
          <span class="sum-label">标的</span>
          <span class="sum-val">
            <b>{{ selectedOpt?.name }}</b>
            <span class="sum-sub">{{ selectedOpt?.code }} · {{ MARKET_LABELS[selectedOpt?.market ?? ''] ?? selectedOpt?.market }}</span>
          </span>
        </div>
        <div class="sum-row">
          <span class="sum-label">方向</span>
          <span class="side-badge" :class="side === 'buy' ? 'is-buy' : 'is-sell'">{{ side === 'buy' ? '买入' : '卖出' }}</span>
        </div>
        <div class="sum-row">
          <span class="sum-label">委托类型</span>
          <span class="sum-val">{{ orderType === 'market' ? '市价' : '限价' }}</span>
        </div>
        <div class="sum-row">
          <span class="sum-label">价格</span>
          <span class="sum-val">
            <template v-if="orderType === 'limit'">¥{{ Number(limitPrice).toFixed(2) }}</template>
            <template v-else>{{ refPrice != null ? `¥${refPrice.toFixed(2)}` : '按市场价撮合' }}<span class="sum-sub">（参考现价）</span></template>
          </span>
        </div>
        <div class="sum-row">
          <span class="sum-label">数量</span>
          <span class="sum-val">{{ quantity }} 股</span>
        </div>

        <!-- 预估金额大字 -->
        <div class="amount-hero">
          <div class="amount-hero-label">预估金额</div>
          <div class="amount-hero-value">{{ fmtMoney(amount) }}</div>
        </div>

        <!-- 费用明细（前端预估口径，与后端费率一致） -->
        <div class="fee-row"><span>佣金（万2.5，最低 ¥5）</span><b>{{ fmtMoney(fee.commission) }}</b></div>
        <div class="fee-row"><span>印花税（万5，仅卖出）</span><b>{{ fmtMoney(fee.stamp) }}</b></div>

        <div class="risk-caption">模拟盘 · 不涉及真实资金</div>
      </div>
      <template #footer>
        <el-button round :disabled="submitting" @click="confirmVisible = false">取消</el-button>
        <el-button type="primary" round :loading="submitting" @click="confirmSubmit">
          {{ side === 'buy' ? '确认买入' : '确认卖出' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ============ 第二步：回执弹层（成交 / 挂单 / 拒单） ============ -->
    <el-dialog v-model="receiptVisible" :title="receiptTitle" width="380px" append-to-body align-center>
      <div v-if="receipt" class="dlg">
        <span class="rc-chip" :class="`rc-${receipt.kind}`">
          {{ receipt.kind === 'filled' ? '已成交' : receipt.kind === 'open' ? '已挂单' : '已拒单' }}
        </span>

        <template v-if="receipt.kind === 'rejected'">
          <div class="rc-reject">{{ receipt.detail }}</div>
        </template>

        <template v-else>
          <div class="sum-row">
            <span class="sum-label">标的</span>
            <span class="sum-val">
              <b>{{ receipt.name }}</b>
              <span class="sum-sub">{{ receipt.code }} · {{ MARKET_LABELS[receipt.market] ?? receipt.market }}</span>
            </span>
          </div>
          <div class="sum-row">
            <span class="sum-label">方向</span>
            <span class="side-badge" :class="receipt.side === 'buy' ? 'is-buy' : 'is-sell'">{{ receipt.side === 'buy' ? '买入' : '卖出' }}</span>
          </div>
          <div class="sum-row">
            <span class="sum-label">{{ receipt.kind === 'filled' ? '成交价' : '委托价' }}</span>
            <span class="sum-val">¥{{ receipt.price.toFixed(2) }}</span>
          </div>
          <div class="sum-row">
            <span class="sum-label">数量</span>
            <span class="sum-val">{{ receipt.quantity }} 股</span>
          </div>

          <template v-if="receipt.kind === 'filled'">
            <div class="sum-row">
              <span class="sum-label">成交额</span>
              <span class="sum-val"><b>{{ fmtMoney(receipt.amount) }}</b></span>
            </div>
            <div class="rc-fee-box">
              <div class="fee-row"><span>佣金</span><b>{{ fmtMoney(receipt.commission) }}</b></div>
              <div class="fee-row"><span>印花税</span><b>{{ fmtMoney(receipt.stampTax) }}</b></div>
              <div class="fee-row total"><span>费用合计</span><b>{{ fmtMoney(receipt.fee) }}</b></div>
            </div>
          </template>

          <div v-else class="rc-open-hint">限价委托未触及成交价，已进入挂单列表，可在持仓页撤单</div>
          <div class="rc-time">{{ receipt.kind === 'filled' ? '成交' : '委托' }}时间 {{ fmtTime(receipt.time) }}</div>
        </template>
      </div>
      <template #footer>
        <el-button type="primary" round @click="receiptVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.order-panel { display: flex; flex-direction: column; gap: 12px; }
.panel-title { font-size: 16px; font-weight: 600; }
.panel-foot { text-align: center; font-size: 12px; color: var(--sira-mute); }

.opt-meta { float: right; font-size: 12px; color: var(--sira-mute); }

/* 方向 toggle：买入红描边 / 卖出绿描边（红涨绿跌体系） */
.side-toggle { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.side-btn {
  border: 1.5px solid var(--sira-canvas-soft);
  background: var(--sira-canvas);
  border-radius: var(--sira-radius-input);
  padding: 10px 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--sira-body);
  cursor: pointer;
  transition: all 0.15s;
}
.side-btn.buy.active { border-color: var(--sira-up); color: var(--sira-up); background: var(--sira-up-pale); }
.side-btn.sell.active { border-color: var(--sira-down); color: var(--sira-down-deep); background: var(--sira-down-pale); }

/* 委托类型 pill：中性 */
.type-toggle { display: inline-flex; background: var(--sira-canvas-soft); border-radius: 9999px; padding: 3px; align-self: flex-start; }
.type-toggle button {
  border: none; background: transparent; border-radius: 9999px; padding: 5px 16px;
  font-size: 13px; color: var(--sira-body); cursor: pointer;
}
.type-toggle button.active { background: var(--sira-canvas); color: var(--sira-ink); font-weight: 600; box-shadow: 0 1px 3px rgba(14, 15, 12, 0.12); }

.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 12px; color: var(--sira-mute); }
.field-hint { font-size: 12px; color: var(--sira-mute); }
.field-hint.warn { color: var(--sira-up); }

.rule-chip {
  align-self: flex-start;
  font-size: 12px; color: var(--sira-body);
  background: var(--sira-canvas-soft);
  border-radius: 9999px; padding: 3px 10px;
}

.calc { background: var(--sira-canvas-soft); border-radius: var(--sira-radius-input); padding: 10px 14px; display: flex; flex-direction: column; gap: 4px; }
.calc-row { display: flex; justify-content: space-between; font-size: 13px; color: var(--sira-body); }
.calc-row b { color: var(--sira-ink); font-variant-numeric: tabular-nums; }
.calc-sub { font-size: 11px; color: var(--sira-mute); }

.submit-btn { width: 100%; font-weight: 700; }

/* ============ 弹层内布局（确认层 / 回执共用） ============ */
.dlg { display: flex; flex-direction: column; gap: 8px; }

.sum-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; min-height: 22px; }
.sum-label { color: var(--sira-mute); }
.sum-val { color: var(--sira-ink); text-align: right; }
.sum-val b { font-weight: 600; font-variant-numeric: tabular-nums; }
.sum-sub { font-size: 12px; color: var(--sira-mute); margin-left: 6px; }

.side-badge {
  display: inline-block; font-size: 13px; font-weight: 600;
  border-radius: 9999px; padding: 2px 14px;
}
.side-badge.is-buy { background: var(--sira-up-pale); color: var(--sira-up); }
.side-badge.is-sell { background: var(--sira-down-pale); color: var(--sira-down-deep); }

/* 预估金额大字 */
.amount-hero {
  margin: 6px 0 2px; padding: 14px;
  background: var(--sira-canvas-soft); border-radius: var(--sira-radius-sm);
  text-align: center;
}
.amount-hero-label { font-size: 12px; color: var(--sira-mute); margin-bottom: 4px; }
.amount-hero-value { font-size: 28px; font-weight: 800; font-variant-numeric: tabular-nums; }

.fee-row { display: flex; justify-content: space-between; font-size: 13px; color: var(--sira-body); padding: 2px 0; }
.fee-row b { color: var(--sira-ink); font-variant-numeric: tabular-nums; }
.fee-row.total { border-top: 1px dashed var(--sira-canvas-soft); margin-top: 2px; padding-top: 6px; }
.fee-row.total span, .fee-row.total b { font-weight: 600; }

.risk-caption { margin-top: 8px; text-align: center; font-size: 12px; color: var(--sira-mute); }

/* 回执状态徽章 */
.rc-chip { align-self: flex-start; font-size: 12px; border-radius: 9999px; padding: 3px 12px; margin-bottom: 4px; }
.rc-filled { background: var(--sira-primary-pale); color: var(--sira-ink-deep); }
.rc-open { background: var(--sira-canvas-soft); color: var(--sira-body); }
.rc-rejected { background: var(--sira-up-pale); color: var(--sira-up); }

/* 回执费用明细块 */
.rc-fee-box { background: var(--sira-canvas-soft); border-radius: var(--sira-radius-sm); padding: 8px 12px; margin-top: 4px; }

.rc-open-hint { font-size: 12px; color: var(--sira-body); background: var(--sira-canvas-soft); border-radius: var(--sira-radius-sm); padding: 8px 12px; line-height: 1.6; }
.rc-time { font-size: 12px; color: var(--sira-mute); text-align: right; margin-top: 4px; }

/* 拒单原因：红色失败态 */
.rc-reject {
  color: var(--sira-up-deep); background: var(--sira-up-pale);
  border-radius: var(--sira-radius-sm); padding: 12px 14px;
  font-size: 13px; line-height: 1.7;
}
</style>
