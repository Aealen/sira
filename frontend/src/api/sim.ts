/** 模拟盘 API 封装（对接后端 /api/sim）。
 *
 * 后端 /api/sim 并行开发中：首次调用会探测 /api/sim/rules，
 * 未就绪（404 / 连接失败）时自动降级为内置 mock 引擎（演示数据），
 * 后端就绪后无需改码，刷新页面即自动切换真实接口。
 */
import axios from 'axios'
import { MARKET_LABELS } from './market'

const http = axios.create({ baseURL: '/api/sim', timeout: 30000 })

/* ---------------- 类型（与后端契约一致） ---------------- */

export type Side = 'buy' | 'sell'
export type OrderType = 'market' | 'limit'

export interface SimPosition {
  market: string
  code: string
  name: string
  /** 总持仓量 */
  quantity: number
  /** 可卖量（T+1 市场当日买入部分不可卖） */
  available_qty: number
  avg_cost: number
  price: number
  market_value: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
}

export interface SimOpenOrder {
  id: number
  market: string
  code: string
  name: string
  side: Side
  order_type: OrderType
  price: number
  quantity: number
  created_at: string
}

export interface SimTrade {
  id: number
  market: string
  code: string
  name: string
  side: Side
  price: number
  quantity: number
  amount: number
  fee: number
  /** 后端返回的费用明细（下单回执展示用；mock 模式无此字段，调用方估算兜底） */
  commission?: number
  stamp_tax?: number
  created_at: string
  status: string
}

export interface SimAccount {
  cash: number
  total_asset: number
  total_pnl: number
  positions: SimPosition[]
  open_orders: SimOpenOrder[]
  recent_trades: SimTrade[]
}

export interface PlaceOrderPayload {
  market: string
  code: string
  side: Side
  order_type: OrderType
  price?: number
  quantity: number
}

export interface SimOrderResult {
  status: 'filled' | 'open'
  trade?: SimTrade
}

export interface MarketRule {
  /** 交收制度：1 = T+1（当日买入不可卖），0 = T+0 */
  t_plus: number
  /** 涨跌幅限制（0.1 = ±10%），0 表示无限制 */
  price_limit_pct: number
  /** 最小申报单位（A股/ETF 为 100 股） */
  min_lot: number
}

export type SimRules = Record<string, MarketRule>

/* ---------------- 展示工具 ---------------- */

export const fmtMoney = (v: number) =>
  `¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

/** 盈亏金额（正数带 + 号） */
export const fmtSignedMoney = (v: number) => `${v > 0 ? '+' : ''}${fmtMoney(v)}`

export const fmtTime = (iso: string) => {
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 费用预估口径：佣金 万2.5（最低 ¥5，买卖均收）· 印花税 万5（仅卖出收） */
const COMMISSION_RATE = 0.00025
const COMMISSION_MIN = 5
const STAMP_TAX_RATE = 0.0005

export function estimateFee(side: Side, amount: number): { commission: number; stamp: number; total: number } {
  const commission = amount > 0 ? Math.max(amount * COMMISSION_RATE, COMMISSION_MIN) : 0
  const stamp = side === 'sell' && amount > 0 ? amount * STAMP_TAX_RATE : 0
  return { commission, stamp, total: commission + stamp }
}

/** 市场规则徽章文案，如 “A股 · T+1 · 涨跌幅±10%” */
export function ruleBadgeText(market: string, rule?: MarketRule): string {
  const label = MARKET_LABELS[market] ?? market
  if (!rule) return label
  const parts = [label, rule.t_plus === 1 ? 'T+1' : 'T+0']
  if (rule.price_limit_pct > 0) parts.push(`涨跌幅±${Math.round(rule.price_limit_pct * 100)}%`)
  return parts.join(' · ')
}

/** 从下单异常中提取后端 422 detail（中文拒单原因）；mock 拒单与真实接口结构一致 */
export function extractRejectDetail(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const d = e.response?.data as { detail?: string } | undefined
    if (d?.detail) return d.detail
    if (!e.response) return '后端连接失败，请确认服务已启动'
  }
  const d = e as { detail?: string } | null
  if (d && typeof d.detail === 'string') return d.detail
  return '下单失败，请稍后重试'
}

/* ---------------- Mock 引擎（/api/sim 未就绪时的演示数据） ---------------- */

const INITIAL_CASH = 1_000_000

const MOCK_RULES: SimRules = {
  a_stock: { t_plus: 1, price_limit_pct: 0.1, min_lot: 100 },
  etf: { t_plus: 0, price_limit_pct: 0.1, min_lot: 100 },
  fund: { t_plus: 0, price_limit_pct: 0, min_lot: 1 },
  hk: { t_plus: 0, price_limit_pct: 0, min_lot: 100 },
  us: { t_plus: 0, price_limit_pct: 0, min_lot: 1 },
}
const DEFAULT_RULE: MarketRule = { t_plus: 0, price_limit_pct: 0, min_lot: 1 }

interface MockPos {
  market: string
  code: string
  name: string
  quantity: number
  available_qty: number
  avg_cost: number
  price: number
}

interface MockState {
  cash: number
  positions: MockPos[]
  openOrders: SimOpenOrder[]
  trades: SimTrade[]
  nextId: number
  /** 非持仓标的的行情参考价 / 名称（由下单面板 seedQuote 同步） */
  extraPrices: Map<string, number>
  knownNames: Map<string, string>
}

const iso = (msAgo = 0) => new Date(Date.now() - msAgo).toISOString()
const key = (m: string, c: string) => `${m}/${c}`
const round = (v: number, d = 2) => Number(v.toFixed(d))

function freshMock(): MockState {
  // 初始现金 = 100 万 - 三笔建仓支出（金额+费用），保持账目自洽
  return {
    cash: 556_529.16,
    positions: [
      { market: 'a_stock', code: '600519', name: '贵州茅台', quantity: 200, available_qty: 200, avg_cost: 1650, price: 1720.5 },
      { market: 'etf', code: '510300', name: '沪深300ETF', quantity: 12000, available_qty: 12000, avg_cost: 3.58, price: 3.62 },
      { market: 'hk', code: '00700', name: '腾讯控股', quantity: 200, available_qty: 200, avg_cost: 352, price: 368.4 },
    ],
    openOrders: [
      { id: 1001, market: 'a_stock', code: '000001', name: '平安银行', side: 'buy', order_type: 'limit', price: 11.8, quantity: 1000, created_at: iso(3600e3) },
    ],
    trades: [
      { id: 3, market: 'hk', code: '00700', name: '腾讯控股', side: 'buy', price: 352, quantity: 200, amount: 70_400, fee: 17.6, created_at: iso(3 * 86400e3), status: 'filled' },
      { id: 2, market: 'etf', code: '510300', name: '沪深300ETF', side: 'buy', price: 3.58, quantity: 12000, amount: 42_960, fee: 10.74, created_at: iso(5 * 86400e3), status: 'filled' },
      { id: 1, market: 'a_stock', code: '600519', name: '贵州茅台', side: 'buy', price: 1650, quantity: 200, amount: 330_000, fee: 82.5, created_at: iso(9 * 86400e3), status: 'filled' },
    ],
    nextId: 1002,
    extraPrices: new Map([['a_stock/000001', 11.95]]),
    knownNames: new Map([['a_stock/000001', '平安银行']]),
  }
}

let mock: MockState = freshMock()

/** 抛出与后端 422 {detail} 同构的拒单错误 */
function refuse(detail: string): never {
  const e = new Error(detail) as Error & { detail: string }
  e.detail = detail
  throw e
}

function nameOf(market: string, code: string): string {
  return (
    mock.positions.find(p => p.market === market && p.code === code)?.name
    ?? mock.knownNames.get(key(market, code))
    ?? mock.openOrders.find(o => o.market === market && o.code === code)?.name
    ?? code
  )
}

function priceOfOrNone(market: string, code: string): number | null {
  const pos = mock.positions.find(p => p.market === market && p.code === code)
  if (pos) return pos.price
  return mock.extraPrices.get(key(market, code)) ?? null
}

function priceOf(market: string, code: string): number {
  return priceOfOrNone(market, code) ?? refuse('行情不可用：暂时拿不到该标的现价，请改用限价委托或稍后再试')
}

/** 行情小幅度随机游走，让轮询时的现价 / 浮动盈亏有演示效果 */
function tickPrices() {
  for (const p of mock.positions) {
    const digits = p.market === 'etf' ? 3 : 2
    p.price = round(p.price * (1 + (Math.random() - 0.5) * 0.006), digits)
  }
  for (const [k, v] of mock.extraPrices) {
    mock.extraPrices.set(k, round(v * (1 + (Math.random() - 0.5) * 0.01)))
  }
}

function mockOrder(p: PlaceOrderPayload): SimOrderResult {
  const rule = MOCK_RULES[p.market] ?? DEFAULT_RULE
  const qty = p.quantity
  if (!Number.isInteger(qty) || qty <= 0) refuse('委托数量必须为正整数')
  if (rule.min_lot > 1 && qty % rule.min_lot !== 0) {
    refuse(`${MARKET_LABELS[p.market] ?? p.market}委托数量需为 ${rule.min_lot} 股的整数倍`)
  }

  const cur = priceOf(p.market, p.code)
  let fillPrice: number
  let fill: boolean
  if (p.order_type === 'market') {
    fill = true
    fillPrice = cur
  } else {
    if (p.price === undefined || !(p.price > 0)) refuse('限价委托必须填写有效的委托价格')
    fillPrice = p.price
    // 限价触及对手价即成交：买单限价 >= 现价 / 卖单限价 <= 现价
    fill = p.side === 'buy' ? p.price >= cur : p.price <= cur
  }

  if (!fill) {
    mock.openOrders.push({
      id: mock.nextId++,
      market: p.market,
      code: p.code,
      name: nameOf(p.market, p.code),
      side: p.side,
      order_type: 'limit',
      price: fillPrice,
      quantity: qty,
      created_at: iso(),
    })
    return { status: 'open' }
  }

  const amount = round(fillPrice * qty)
  const { total: fee } = estimateFee(p.side, amount)

  if (p.side === 'buy') {
    const need = round(amount + fee)
    if (mock.cash < need) {
      refuse(`可用资金不足：本次需 ${fmtMoney(need)}（含费用 ${fmtMoney(fee)}），当前可用 ${fmtMoney(mock.cash)}`)
    }
    mock.cash = round(mock.cash - need)
    const pos = mock.positions.find(x => x.market === p.market && x.code === p.code)
    if (pos) {
      pos.avg_cost = round((pos.avg_cost * pos.quantity + amount) / (pos.quantity + qty))
      pos.quantity += qty
      if (rule.t_plus === 0) pos.available_qty += qty // T+1 市场当日买入不可卖
    } else {
      mock.positions.push({
        market: p.market,
        code: p.code,
        name: nameOf(p.market, p.code),
        quantity: qty,
        available_qty: rule.t_plus === 0 ? qty : 0,
        avg_cost: fillPrice,
        price: fillPrice,
      })
    }
  } else {
    const pos = mock.positions.find(x => x.market === p.market && x.code === p.code)
    if (!pos) refuse(`无 ${nameOf(p.market, p.code)} 持仓，无法卖出`)
    if (pos.available_qty < qty) {
      refuse(`可卖数量不足：当前可卖 ${pos.available_qty} 股（T+1 当日买入部分不可卖）`)
    }
    pos.quantity -= qty
    pos.available_qty -= qty
    if (pos.quantity <= 0) mock.positions = mock.positions.filter(x => x !== pos)
    mock.cash = round(mock.cash + amount - fee)
  }

  const trade: SimTrade = {
    id: mock.nextId++,
    market: p.market,
    code: p.code,
    name: nameOf(p.market, p.code),
    side: p.side,
    price: fillPrice,
    quantity: qty,
    amount,
    fee,
    created_at: iso(),
    status: 'filled',
  }
  mock.trades.unshift(trade)
  return { status: 'filled', trade }
}

/** 挂单撮合：轮询时若限价触及现价则模拟成交（资金/可卖不足自动转撤单） */
function matchOpenOrders() {
  const remain: SimOpenOrder[] = []
  for (const o of mock.openOrders) {
    const cur = priceOfOrNone(o.market, o.code)
    const hit = cur !== null && (o.side === 'buy' ? cur <= o.price : cur >= o.price)
    if (!hit) {
      remain.push(o)
      continue
    }
    try {
      mockOrder({ market: o.market, code: o.code, side: o.side, order_type: 'limit', price: o.price, quantity: o.quantity })
    } catch {
      mock.trades.unshift({
        id: mock.nextId++,
        market: o.market,
        code: o.code,
        name: o.name,
        side: o.side,
        price: o.price,
        quantity: o.quantity,
        amount: 0,
        fee: 0,
        created_at: iso(),
        status: 'cancelled',
      })
    }
  }
  mock.openOrders = remain
}

function mockAccount(): SimAccount {
  tickPrices()
  matchOpenOrders()
  const positions: SimPosition[] = mock.positions.map(p => ({
    market: p.market,
    code: p.code,
    name: p.name,
    quantity: p.quantity,
    available_qty: p.available_qty,
    avg_cost: p.avg_cost,
    price: p.price,
    market_value: round(p.price * p.quantity),
    unrealized_pnl: round((p.price - p.avg_cost) * p.quantity),
    unrealized_pnl_pct: round((p.price / p.avg_cost - 1) * 100),
  }))
  const marketValueSum = round(positions.reduce((s, p) => s + p.market_value, 0))
  const totalAsset = round(mock.cash + marketValueSum)
  return {
    cash: round(mock.cash),
    total_asset: totalAsset,
    total_pnl: round(totalAsset - INITIAL_CASH),
    positions,
    open_orders: [...mock.openOrders],
    recent_trades: mock.trades.slice(0, 8),
  }
}

/* ---------------- 模式探测：真实后端优先，失败自动降级 mock ---------------- */

let mockMode: boolean | null = null
let probing: Promise<boolean> | null = null

function detectMode(): Promise<boolean> {
  if (mockMode !== null) return Promise.resolve(mockMode)
  const p: Promise<boolean> = (probing ??= http
    .get('/rules', { timeout: 3000 })
    .then(() => { mockMode = false })
    .catch(() => { mockMode = true })
    .then(() => {
      probing = null
      return mockMode as boolean
    }))
  return p
}

/** 后端成交流水的费用字段为 total_fee（含四项明细），下单回执的成交对象同源——归一到前端 fee 口径 */
function normalizeTrade(t: SimTrade & { total_fee?: number }): SimTrade {
  return { ...t, fee: t.fee ?? t.total_fee ?? 0 }
}

export const simApi = {
  /** 当前是否运行在 mock 演示数据模式（/api/sim 未就绪） */
  isMock: () => mockMode === true,

  async account(): Promise<SimAccount> {
    if (await detectMode()) return mockAccount()
    const d = (await http.get<SimAccount>('/account')).data
    d.recent_trades = d.recent_trades.map(normalizeTrade)
    return d
  },

  async rules(): Promise<SimRules> {
    if (await detectMode()) return MOCK_RULES
    return (await http.get<SimRules>('/rules')).data
  },

  /** 下单：201 成交/挂单；422 {detail} 为中文拒单原因（走异常） */
  async placeOrder(p: PlaceOrderPayload): Promise<SimOrderResult> {
    if (await detectMode()) return mockOrder(p)
    const d = (await http.post<SimOrderResult>('/order', p)).data
    if (d.trade) d.trade = normalizeTrade(d.trade)
    return d
  },

  async cancelOrder(id: number): Promise<void> {
    if (await detectMode()) {
      mock.openOrders = mock.openOrders.filter(o => o.id !== id)
      return
    }
    await http.delete(`/order/${id}`)
  },

  async trades(limit = 100): Promise<SimTrade[]> {
    if (await detectMode()) return mock.trades.slice(0, limit)
    return ((await http.get<SimTrade[]>('/trades', { params: { limit } })).data).map(normalizeTrade)
  },

  async reset(): Promise<void> {
    if (await detectMode()) {
      mock = freshMock()
      return
    }
    await http.post('/reset', { confirm: true })
  },

  /** mock 模式下同步面板拉到的行情参考价（真实模式忽略，由后端撮合） */
  seedQuote(market: string, code: string, name: string, price: number) {
    if (mockMode) {
      if (!mock.positions.some(p => p.market === market && p.code === code)) {
        mock.extraPrices.set(key(market, code), price)
      }
      mock.knownNames.set(key(market, code), name)
    }
  },
}
