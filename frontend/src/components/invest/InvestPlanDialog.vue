<script setup lang="ts">
/** 新建 / 编辑定投计划对话框。
 *
 * 标的搜索复用 marketApi.search（风格对齐自选页搜索）；
 * 频率与止盈方式为 pill 单选，扣款日 1-28，金额单位元；
 * 智能加减速 switch（低估多买 · 高估少买）。
 */
import { computed, ref, watch } from 'vue'
import { ElButton, ElDialog, ElIcon, ElInputNumber, ElMessage, ElSwitch } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { MARKET_LABELS, marketApi, type SearchItem } from '../../api/market'
import {
  FREQUENCY_LABELS,
  TAKE_PROFIT_LABELS,
  extractInvestError,
  investApi,
  type Frequency,
  type InvestPlan,
  type InvestPlanPayload,
  type TakeProfitMode,
} from '../../api/invest'

const props = defineProps<{ modelValue: boolean; plan: InvestPlan | null }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

/** 编辑模式：传入已有计划；新建：plan = null */
const editing = computed(() => !!props.plan)

/* ---------------- 表单状态 ---------------- */

const target = ref<SearchItem | null>(null)
const keyword = ref('')
const results = ref<SearchItem[]>([])
const searching = ref(false)
const frequency = ref<Frequency>('monthly')
const dayOfMonth = ref(10)
const amount = ref(500)
const smartDca = ref(false)
const tpMode = ref<TakeProfitMode>('none')
const tpValue = ref<number | null>(20)
const submitting = ref(false)

/** 每次打开按模式重置表单 */
watch(visible, (v) => {
  if (!v) return
  const p = props.plan
  if (p) {
    target.value = { market: p.market, code: p.code, name: p.name }
    frequency.value = p.frequency
    dayOfMonth.value = p.day_of_month
    amount.value = p.amount
    smartDca.value = p.smart_dca
    tpMode.value = p.take_profit_mode
    tpValue.value = p.take_profit_value ?? 20
  } else {
    target.value = null
    keyword.value = ''
    results.value = []
    frequency.value = 'monthly'
    dayOfMonth.value = 10
    amount.value = 500
    smartDca.value = false
    tpMode.value = 'none'
    tpValue.value = 20
  }
})

let searchTimer: ReturnType<typeof setTimeout> | undefined

function onKeywordInput() {
  clearTimeout(searchTimer)
  const q = keyword.value.trim()
  if (!q) {
    results.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    searching.value = true
    try {
      results.value = await marketApi.search(q, 8)
    } catch {
      results.value = []
    } finally {
      searching.value = false
    }
  }, 400)
}

function pickTarget(s: SearchItem) {
  target.value = s
  keyword.value = ''
  results.value = []
}

function clearTarget() {
  target.value = null
}

const FREQUENCIES: Frequency[] = ['monthly', 'biweekly', 'weekly']
const TP_MODES: TakeProfitMode[] = ['none', 'target_return', 'valuation']

const canSubmit = computed(
  () =>
    !!target.value
    && Number.isFinite(amount.value)
    && amount.value > 0
    && Number.isFinite(dayOfMonth.value)
    && dayOfMonth.value >= 1
    && dayOfMonth.value <= 28
    && (tpMode.value === 'none' || (tpValue.value ?? 0) > 0)
    && !submitting.value,
)

async function submit() {
  const t = target.value
  if (!t || !canSubmit.value) return
  submitting.value = true
  const payload: InvestPlanPayload = {
    market: t.market,
    code: t.code,
    name: t.name,
    frequency: frequency.value,
    day_of_month: Math.round(dayOfMonth.value),
    amount: amount.value,
    smart_dca: smartDca.value,
    take_profit_mode: tpMode.value,
    take_profit_value: tpMode.value === 'none' ? null : tpValue.value,
  }
  try {
    if (editing.value) {
      const { market: _m, code: _c, name: _n, ...patch } = payload
      void _m; void _c; void _n
      await investApi.update(props.plan!.id, patch)
      ElMessage.success(`已保存修改：${t.name}`)
    } else {
      await investApi.create(payload)
      ElMessage.success(`定投计划已创建：${t.name}`)
    }
    visible.value = false
    emit('saved')
  } catch (e) {
    ElMessage.error(extractInvestError(e))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="editing ? '编辑定投计划' : '新建定投计划'" width="540px">
    <div class="form">
      <!-- 定投标的：搜索选择（编辑模式锁定） -->
      <div class="field">
        <label>定投标的</label>
        <div v-if="target" class="target-chip">
          <span class="market-tag">{{ MARKET_LABELS[target.market] ?? target.market }}</span>
          <span class="t-name">{{ target.name }}</span>
          <span class="t-code">{{ target.code }}</span>
          <button v-if="!editing" class="t-change" @click="clearTarget">更换</button>
        </div>
        <template v-else>
          <div class="search-box">
            <el-icon :size="18" color="#868685"><Search /></el-icon>
            <input
              v-model="keyword"
              class="search-input"
              placeholder="搜索代码 / 名称 · A股 · ETF · 场外基金 · 港股 · 美股"
              @input="onKeywordInput"
            >
            <div v-if="searching" class="hint">搜索中…</div>
          </div>
          <div v-if="keyword.trim() && !results.length && !searching" class="hint pad">未找到匹配标的</div>
          <div v-else-if="!keyword.trim()" class="hint pad">输入代码或名称，选择真实标的</div>
          <div v-if="results.length" class="result-list">
            <div v-for="s in results" :key="s.market + s.code" class="result-row" @click="pickTarget(s)">
              <span class="market-tag">{{ MARKET_LABELS[s.market] ?? s.market }}</span>
              <span class="t-name">{{ s.name }}</span>
              <span class="t-code">{{ s.code }}</span>
            </div>
          </div>
        </template>
      </div>

      <!-- 频率 pill -->
      <div class="field">
        <label>定投频率</label>
        <div class="pill-group">
          <button
            v-for="f in FREQUENCIES"
            :key="f"
            type="button"
            :class="{ active: frequency === f }"
            @click="frequency = f"
          >{{ FREQUENCY_LABELS[f] }}</button>
        </div>
      </div>

      <!-- 扣款日 + 每期金额 -->
      <div class="row2">
        <div class="field">
          <label>扣款日</label>
          <el-input-number
            v-model="dayOfMonth"
            :min="1"
            :max="28"
            :step="1"
            step-strictly
            :controls="false"
            placeholder="1-28"
            style="width: 100%"
          />
          <div class="hint">1 - 28 · 每两周 / 每周计划以该日为起点循环</div>
        </div>
        <div class="field">
          <label>每期金额（元）</label>
          <el-input-number
            v-model="amount"
            :min="1"
            :step="100"
            :controls="false"
            placeholder="如 500"
            style="width: 100%"
          />
          <div class="hint">每期扣款金额，单位元</div>
        </div>
      </div>

      <!-- 智能加减速 -->
      <div class="switch-row">
        <div>
          <div class="sw-label">智能加减速</div>
          <div class="sw-caption">低估多买 · 高估少买</div>
        </div>
        <el-switch v-model="smartDca" />
      </div>

      <!-- 止盈方式 -->
      <div class="field">
        <label>止盈方式</label>
        <div class="pill-group">
          <button
            v-for="m in TP_MODES"
            :key="m"
            type="button"
            :class="{ active: tpMode === m }"
            @click="tpMode = m"
          >{{ TAKE_PROFIT_LABELS[m] }}</button>
        </div>
        <div v-if="tpMode !== 'none'" class="tp-row">
          <el-input-number
            v-model="tpValue"
            :min="0.01"
            :max="tpMode === 'valuation' ? 100 : 1000"
            :step="tpMode === 'valuation' ? 5 : 1"
            :controls="false"
            style="width: 140px"
          />
          <span class="tp-unit">{{ tpMode === 'target_return' ? '% 收益率' : '% PE 分位' }}</span>
        </div>
        <div v-if="tpMode === 'target_return'" class="hint">如 20 表示累计收益率达 +20% 时提示止盈</div>
        <div v-else-if="tpMode === 'valuation'" class="hint">如 80 表示估值分位高于 80%（高估区）时提示止盈</div>
      </div>
    </div>

    <template #footer>
      <el-button round @click="visible = false">取消</el-button>
      <el-button type="primary" round :disabled="!canSubmit" :loading="submitting" @click="submit">
        {{ editing ? '保存修改' : '创建计划' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
:deep(.el-dialog) { border-radius: 20px; }

.form { display: flex; flex-direction: column; gap: 16px; }

.field { display: flex; flex-direction: column; gap: 6px; }
.field > label { font-size: 12px; color: var(--sira-mute); }

.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

/* 标的 chip */
.target-chip {
  display: inline-flex; align-items: center; gap: 10px; align-self: flex-start;
  background: var(--sira-canvas-soft); border-radius: 9999px; padding: 6px 14px;
}
.market-tag { font-size: 12px; color: var(--sira-mute); background: var(--sira-canvas); border-radius: 9999px; padding: 1px 8px; white-space: nowrap; }
.t-name { font-weight: 500; }
.t-code { color: var(--sira-mute); font-size: 13px; }
.t-change { border: none; background: none; color: var(--sira-body); font-size: 12px; cursor: pointer; padding: 0; text-decoration: underline; }
.t-change:hover { color: var(--sira-ink); }

/* 搜索框（对齐自选页） */
.search-box {
  display: flex; align-items: center; gap: 10px; padding: 12px 16px;
  background: var(--sira-canvas-soft); border-radius: var(--sira-radius-input);
}
.search-input { flex: 1; border: none; outline: none; font-size: 14px; color: var(--sira-ink); background: transparent; }
.hint { font-size: 12px; color: var(--sira-mute); }
.hint.pad { padding: 4px 2px; }
.result-list { max-height: 220px; overflow-y: auto; }
.result-row {
  display: flex; align-items: center; gap: 12px; padding: 10px 12px;
  border-radius: var(--sira-radius-sm); cursor: pointer;
}
.result-row:hover { background: var(--sira-canvas-soft); }

/* pill 单选（对齐模拟页 tab 风格） */
.pill-group { display: inline-flex; gap: 4px; background: var(--sira-canvas-soft); border-radius: 9999px; padding: 4px; align-self: flex-start; }
.pill-group button {
  border: none; background: transparent; border-radius: 9999px; padding: 7px 18px;
  font-size: 13px; color: var(--sira-body); cursor: pointer;
}
.pill-group button.active {
  background: var(--sira-canvas); color: var(--sira-ink); font-weight: 600;
  box-shadow: 0 1px 3px rgba(14, 15, 12, 0.12);
}

/* 智能加减速 */
.switch-row {
  display: flex; justify-content: space-between; align-items: center; gap: 16px;
  background: var(--sira-canvas-soft); border-radius: var(--sira-radius-input); padding: 10px 14px;
}
.sw-label { font-weight: 500; }
.sw-caption { font-size: 12px; color: var(--sira-mute); margin-top: 2px; }

/* 止盈数值 */
.tp-row { display: flex; align-items: center; gap: 10px; }
.tp-unit { font-size: 13px; color: var(--sira-body); }
</style>
