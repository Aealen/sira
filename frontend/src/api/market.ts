/** 行情与自选 API 封装（对接后端 /api/market 与 /api/watchlist）。 */
import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 30000 })

export interface SearchItem { market: string; code: string; name: string }

export interface Quote {
  market: string
  code: string
  name: string
  price: number
  prev_close: number
  change: number
  change_pct: number
  time: string
  stale: boolean
}

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  change_pct: number
}

export interface Kline { market: string; code: string; name: string; stale: boolean; bars: Bar[] }

export interface WatchItem {
  market: string
  code: string
  name: string
  price: number
  change_pct: number
  time: string
  stale: boolean
}

export interface IndexQuote {
  code: string
  name: string
  price: number
  change_pct: number
}

export interface IntradayBar { time: string; price: number; volume: number }
export interface Intraday { prev_close: number | null; bars: IntradayBar[] }

export const marketApi = {
  search: (q: string, limit = 10) =>
    http.get<SearchItem[]>('/market/search', { params: { q, limit } }).then(r => r.data),
  quote: (market: string, code: string) =>
    http.get<Quote>(`/market/quote/${market}/${code}`).then(r => r.data),
  kline: (market: string, code: string, days = 120) =>
    http.get<Kline>(`/market/kline/${market}/${code}`, { params: { days } }).then(r => r.data),
  indices: () => http.get<IndexQuote[]>('/market/indices').then(r => r.data),
  intraday: (market: string, code: string) =>
    http.get<Intraday>(`/market/intraday/${market}/${code}`).then(r => r.data),
}

export const watchlistApi = {
  list: () => http.get<WatchItem[]>('/watchlist').then(r => r.data),
  add: (item: { market: string; code: string; name: string }) =>
    http.post('/watchlist', item).then(r => r.data),
  remove: (market: string, code: string) =>
    http.delete(`/watchlist/${market}/${code}`).then(r => r.data),
}

export const MARKET_LABELS: Record<string, string> = {
  a_stock: 'A股', etf: '场内ETF', fund: '场外基金', hk: '港股', us: '美股',
}

export const fmtPct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`

export const trendClass = (v: number) => (v > 0 ? 'up' : v < 0 ? 'down' : '')
