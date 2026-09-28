/** 定投与复盘 API 封装（对接后端 /api/invest，真数据）。 */
import axios from 'axios'

const http = axios.create({ baseURL: '/api/invest', timeout: 30000 })

/* ---------------- 类型（与后端契约一致） ---------------- */

export type Frequency = 'monthly' | 'biweekly' | 'weekly'
export type TakeProfitMode = 'none' | 'target_return' | 'valuation'
export type PlanStatus = 'active' | 'paused'

export const FREQUENCY_LABELS: Record<Frequency, string> = {
  monthly: '每月',
  biweekly: '每两周',
  weekly: '每周',
}

export const TAKE_PROFIT_LABELS: Record<TakeProfitMode, string> = {
  none: '不设止盈',
  target_return: '目标收益率',
  valuation: '估值分位',
}

export interface InvestPlanStats {
  /** 累计投入金额（元） */
  invested: number
  /** 已执行期数 */
  executions: number
  /** 当前持仓市值（元） */
  market_value: number
  /** 收益率（百分数，正为盈利） */
  pnl_pct: number
}

export interface InvestPlan {
  id: number
  market: string
  code: string
  name: string
  frequency: Frequency
  /** 扣款日（1-28） */
  day_of_month: number
  /** 每期金额（元） */
  amount: number
  /** 智能加减速：低估多买 · 高估少买 */
  smart_dca: boolean
  take_profit_mode: TakeProfitMode
  /** 止盈阈值（目标收益率 % 或估值分位 %），mode=none 时为 null */
  take_profit_value: number | null
  status: PlanStatus
  last_exec_date: string | null
  stats: InvestPlanStats
}

export interface InvestPlanPayload {
  market: string
  code: string
  name: string
  frequency: Frequency
  day_of_month: number
  amount: number
  smart_dca: boolean
  take_profit_mode: TakeProfitMode
  take_profit_value: number | null
}

/** PATCH 支持部分字段 + status 切换（暂停/恢复） */
export type InvestPlanPatch = Partial<InvestPlanPayload> & { status?: PlanStatus }

export interface InvestExecution {
  exec_date: string
  price: number
  quantity: number
  amount: number
  fee: number
}

export interface InvestPlanDetail extends InvestPlan {
  executions: InvestExecution[]
}

export interface InvestDisciplineItem {
  check: string
  passed: boolean
  detail: string
}

export interface InvestReview {
  total_invested: number
  total_market_value: number
  /** 总收益率（百分数，正为盈利） */
  total_pnl_pct: number
  discipline: InvestDisciplineItem[]
}

/** POST /plans/{id}/execute-now 的返回 */
export interface ExecuteNowResult {
  ok: boolean
  plan_id: number
  message: string
  execution_id: number
  price: number
  quantity: number
  amount: number
  fee: number
}

/** 从请求异常中提取后端 422 detail（中文原因） */
export function extractInvestError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const d = e.response?.data as { detail?: string } | undefined
    if (d?.detail && typeof d.detail === 'string') return d.detail
    if (!e.response) return '后端连接失败，请确认服务已启动'
  }
  return '操作失败，请稍后重试'
}

/** 份额数量展示：整数不带小数，小数保留 2 位（场外基金常见小数份额） */
export const fmtQty = (q: number) => (Number.isInteger(q) ? String(q) : q.toFixed(2))

export const investApi = {
  /** 进行中的计划列表（含累计统计） */
  plans: () => http.get<InvestPlan[]>('/plans').then(r => r.data),

  create: (p: InvestPlanPayload) => http.post<InvestPlan>('/plans', p).then(r => r.data),

  /** 部分更新 / 暂停恢复（status: active | paused） */
  update: (id: number, patch: InvestPlanPatch) =>
    http.patch<InvestPlan>(`/plans/${id}`, patch).then(r => r.data),

  remove: (id: number) => http.delete(`/plans/${id}`).then(r => r.data),

  /** 计划详情（含执行历史） */
  detail: (id: number) => http.get<InvestPlanDetail>(`/plans/${id}`).then(r => r.data),

  /** 立即执行一期 */
  executeNow: (id: number) =>
    http.post<ExecuteNowResult>(`/plans/${id}/execute-now`).then(r => r.data),

  /** 复盘统计（总投入/总市值/总收益率 + 纪律检查） */
  review: () => http.get<InvestReview>('/review').then(r => r.data),
}
