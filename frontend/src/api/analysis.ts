/** 标的分析 API 封装（对接后端 /api/analysis 与 /api/profile）。
 *  后端联调未就绪（404/网络错误）时自动回退到契约 mock 数据，页面可先行完成 UI 验证。 */
import axios from 'axios'
import type { Bar } from './market'

const http = axios.create({ baseURL: '/api', timeout: 30000 })

export interface AnalysisIndicators {
  ann_volatility: number
  max_drawdown: number
  max_drawdown_start: string
  max_drawdown_end: string
  sharpe: number
  sortino: number
  daily_mean: number
  daily_std: number
  worst_daily: number
}

export interface AnalysisValuation {
  pe: number
  pb: number
  pe_percentile: number
  pb_percentile: number
  window_years: number
}

export interface RefPosition {
  tolerance: number
  hist_max_drawdown: number
  suggested_max_pct: number
}

export interface AnalysisResult {
  market: string
  code: string
  name: string
  bars: Bar[]
  indicators: AnalysisIndicators
  valuation: AnalysisValuation | null
  ref_position: RefPosition
}

export interface RiskProfile {
  max_drawdown_tolerance: number
  horizon: string
  target_return: number
  attitude: string
}

/** 投资期限与风险态度的可选项（后端枚举值联调时可再对齐） */
export const HORIZON_OPTIONS = [
  { value: 'short', label: '短期（1年内）' },
  { value: 'mid', label: '中期（1-3年）' },
  { value: 'long', label: '长期（3年以上）' },
] as const

export const ATTITUDE_OPTIONS = [
  { value: 'conservative', label: '保守' },
  { value: 'balanced', label: '稳健' },
  { value: 'aggressive', label: '进取' },
] as const

/** 简单移动平均：窗口不满时为 null（ECharts 中表现为缺口） */
export function calcMA(closes: number[], window: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= window) sum -= closes[i - window]
    out.push(i >= window - 1 ? sum / window : null)
  }
  return out
}

/** 最大回撤展示：兼容负值/幅度两种口径，统一渲染为 -xx.xx% */
export const fmtDrawdown = (v: number) => `${v > 0 ? '-' : ''}${Math.abs(v).toFixed(2)}%`

/** mock 是否生效（analysis 接口取不到时置 true，页面据此显示“演示数据”标记） */
let mockActive = false
export const isMockActive = () => mockActive

let mockProfile: RiskProfile = { max_drawdown_tolerance: 15, horizon: 'mid', target_return: 8, attitude: 'balanced' }

export const analysisApi = {
  get: async (market: string, code: string, days = 365): Promise<AnalysisResult> => {
    try {
      const r = await http.get<AnalysisResult>(`/analysis/${market}/${code}`, { params: { days } })
      mockActive = false
      return r.data
    } catch {
      mockActive = true
      return buildMockAnalysis(market, code, days)
    }
  },
}

export const profileApi = {
  get: async (): Promise<RiskProfile> => {
    try {
      const r = await http.get<RiskProfile>('/profile')
      return r.data
    } catch {
      return { ...mockProfile }
    }
  },
  update: async (p: RiskProfile): Promise<RiskProfile> => {
    try {
      const r = await http.put<RiskProfile>('/profile', p)
      return r.data
    } catch {
      mockProfile = { ...p }
      return { ...mockProfile }
    }
  },
}

/* ---------------- 契约 mock（仅后端未就绪时使用，确定性随机保证同标的数据稳定） ---------------- */

const MAX_MOCK_BARS = 780
const KNOWN_NAMES: Record<string, string> = { 'etf/510300': '沪深300ETF' }
const BASE_PRICE: Record<string, number> = { etf: 3.9, a_stock: 24, fund: 1.62, hk: 58, us: 172 }
const MOCK_VOL: Record<string, number> = { etf: 0.012, a_stock: 0.018, fund: 0.009, hk: 0.016, us: 0.014 }
const MOCK_VOL_BASE: Record<string, number> = { etf: 4.2e6, a_stock: 2.4e7, fund: 0, hk: 9e6, us: 5e6 }

function hashCode(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function mulberry32(seed: number): () => number {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 生成以今天结尾的 n 个工作日（YYYY-MM-DD，升序） */
function mockDates(n: number): string[] {
  const out: string[] = []
  const d = new Date()
  while (out.length < n) {
    const day = d.getDay()
    if (day !== 0 && day !== 6) out.push(d.toISOString().slice(0, 10))
    d.setDate(d.getDate() - 1)
  }
  return out.reverse()
}

const round = (v: number, d: number) => Math.round(v * 10 ** d) / 10 ** d

/** 生成 MAX_MOCK_BARS 根 K 线；不同 days 区间取其尾部，保证区间之间数据一致 */
function buildMockSeries(market: string, code: string): Bar[] {
  const rnd = mulberry32(hashCode(`${market}/${code}`))
  const gauss = () => {
    const u = Math.max(rnd(), 1e-9)
    const v = rnd()
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
  }
  const dates = mockDates(MAX_MOCK_BARS)
  const vol = MOCK_VOL[market] ?? 0.015
  const digits = (BASE_PRICE[market] ?? 10) < 10 ? 3 : 2
  const volBase = MOCK_VOL_BASE[market] ?? 5e6
  const isFund = market === 'fund'
  let price = BASE_PRICE[market] ?? 10
  const bars: Bar[] = []
  for (let i = 0; i < MAX_MOCK_BARS; i++) {
    // 分段漂移制造出可感知的回撤区间
    const drift = 0.0004 * Math.sin(i / 55) - 0.0001
    const r = drift + vol * gauss()
    const prev = price
    price = price * (1 + r)
    const changePct = i === 0 ? 0 : (price / prev - 1) * 100
    if (isFund) {
      const nav = round(price, 4)
      bars.push({ date: dates[i], open: nav, high: nav, low: nav, close: nav, volume: 0, change_pct: round(changePct, 2) })
      continue
    }
    const open = round(prev * (1 + r * 0.3), digits)
    const close = round(price, digits)
    const high = Math.max(round(Math.max(open, close) * (1 + rnd() * vol * 0.5), digits), open, close)
    const low = Math.min(round(Math.min(open, close) * (1 - rnd() * vol * 0.5), digits), open, close)
    const volume = Math.round(volBase * (0.4 + rnd()) * (1 + 3 * Math.abs(r) * 100))
    bars.push({ date: dates[i], open, high, low, close, volume, change_pct: round(changePct, 2) })
  }
  return bars
}

function computeIndicators(bars: Bar[]): AnalysisIndicators {
  const rets: number[] = []
  for (let i = 1; i < bars.length; i++) rets.push((bars[i].close / bars[i - 1].close - 1) * 100)
  const n = rets.length || 1
  const mean = rets.reduce((a, b) => a + b, 0) / n
  const std = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / n)
  let peak = bars[0]?.close ?? 0
  let peakIdx = 0
  let mdd = 0
  let start = bars[0]?.date ?? ''
  let end = bars[0]?.date ?? ''
  for (let i = 1; i < bars.length; i++) {
    const c = bars[i].close
    if (c > peak) {
      peak = c
      peakIdx = i
    }
    const dd = (c / peak - 1) * 100
    if (dd < mdd) {
      mdd = dd
      start = bars[peakIdx].date
      end = bars[i].date
    }
  }
  const annVol = std * Math.sqrt(252)
  const annRet = mean * 252
  const rf = 2
  const downside = Math.sqrt(rets.reduce((a, r) => a + Math.min(r, 0) ** 2, 0) / n) * Math.sqrt(252)
  const r2 = (v: number) => Math.round(v * 100) / 100
  return {
    ann_volatility: r2(annVol),
    max_drawdown: r2(mdd),
    max_drawdown_start: start,
    max_drawdown_end: end,
    sharpe: r2(annVol > 0 ? (annRet - rf) / annVol : 0),
    sortino: r2(downside > 0 ? (annRet - rf) / downside : 0),
    daily_mean: r2(mean),
    daily_std: r2(std),
    worst_daily: r2(Math.min(0, ...rets)),
  }
}

function buildMockAnalysis(market: string, code: string, days: number): AnalysisResult {
  const full = buildMockSeries(market, code)
  const n = Math.min(full.length, Math.max(20, Math.round((days * 5) / 7)))
  const bars = full.slice(-n)
  const indicators = computeIndicators(bars)
  const rnd = mulberry32(hashCode(`${market}/${code}/valuation`))
  const valuation =
    market === 'a_stock'
      ? {
          pe: round(9 + rnd() * 28, 2),
          pb: round(0.8 + rnd() * 3.4, 2),
          pe_percentile: round(5 + rnd() * 90, 1),
          pb_percentile: round(5 + rnd() * 90, 1),
          window_years: 10,
        }
      : null
  const mddAbs = Math.abs(indicators.max_drawdown) || 1
  const ref_position = {
    tolerance: mockProfile.max_drawdown_tolerance,
    hist_max_drawdown: round(mddAbs, 2),
    suggested_max_pct: Math.min(100, round((mockProfile.max_drawdown_tolerance / mddAbs) * 100, 1)),
  }
  return {
    market,
    code,
    name: KNOWN_NAMES[`${market}/${code}`] ?? code,
    bars,
    indicators,
    valuation,
    ref_position,
  }
}
