/** 资讯 API 封装（对接后端 /api/news）。
 *
 * 后端路由并行开发中：真 API 请求失败（404 / 网络错误等）时自动降级到内置 mock，
 * 字段与契约完全一致；后端就绪后无需改动即可无缝切换。
 *
 * 方向语义（红涨绿跌惯例，务必别做反）：
 *   利好 positive = 淡红底 + 深红字（--sira-up-pale / --sira-up-deep）
 *   利空 negative = 淡绿底 + 深绿字（--sira-down-pale / --sira-down-deep）
 *   中性 neutral  = 灰底灰字
 */
import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 30000 })

export type NewsDirection = 'positive' | 'negative' | 'neutral'
export type NewsAnalysisStatus = 'pending' | 'done' | 'failed'

export interface ImpactTarget {
  /** stock / industry / index / macro … */
  type: string
  name: string
  code: string
  /** 是否命中用户自选（chip 用 ✦ + lime 淡底强调） */
  hit_watchlist: boolean
}

export interface NewsImpact {
  direction: NewsDirection
  /** '强' | '中' | '弱'，兼容后端输出 1-5 数值 */
  strength: string | number
  /** 短期 / 中期 / 长期 */
  horizon: string
  /** 传导逻辑一句话 */
  logic: string
  /** 原文引句 */
  quote: string
  /** 归纳事实 */
  fact: string
  /** 置信度，0-1 小数或 0-100 */
  confidence: number
  targets: ImpactTarget[]
}

export interface NewsItem {
  id: number
  source: string
  /** macro / industry / announcement / report */
  category: string
  title: string
  summary: string
  /** ISO 时间字符串 */
  published_at: string
  analysis_status: NewsAnalysisStatus
  impacts: NewsImpact[]
  /** 详情字段：原文链接（阅读抽屉"查看原文"用） */
  url?: string
}

export interface NewsListResp {
  total: number
  items: NewsItem[]
}

export interface CollectResp { added: number }
export interface AnalyzeResp { analyzed: number }

export interface WatchlistDigest {
  code: string
  name: string
  direction: NewsDirection
  count: number
  latest_title: string
}

export interface NewsListParams {
  /** 空串 = 全部 */
  category?: string
  q?: string
  limit?: number
  offset?: number
}

export const CATEGORY_LABELS: Record<string, string> = {
  macro: '宏观',
  industry: '行业',
  announcement: '公司公告',
  report: '研报',
}

export const TARGET_TYPE_LABELS: Record<string, string> = {
  stock: '个股',
  industry: '行业',
  index: '指数',
  macro: '宏观',
}

export const DIRECTION_LABELS: Record<NewsDirection, string> = {
  positive: '利好',
  negative: '利空',
  neutral: '中性',
}

/** 徽章配色 class：利好淡红、利空淡绿、中性灰（红涨绿跌） */
export const directionClass = (d: NewsDirection): string =>
  d === 'positive' ? 'is-positive' : d === 'negative' ? 'is-negative' : 'is-neutral'

export const fmtStrength = (s: string | number): string =>
  typeof s === 'number' ? (s >= 4 ? '强' : s >= 3 ? '中' : '弱') : s

export const fmtConfidence = (c: number): string => `${Math.round(c > 1 ? c : c * 100)}%`

/** 展示时间：今天显示 HH:mm，更早显示 MM-DD HH:mm */
export function fmtNewsTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const now = new Date()
  return d.toDateString() === now.toDateString() ? hm : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
}

// ---------------------------------------------------------------------------
// 内置 mock（后端 /api/news 未就绪时的降级数据，契约字段一致）
// ---------------------------------------------------------------------------

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()

interface MockSeed {
  item: NewsItem
  /** mock"立即分析"时注入的影响分析结果 */
  analyzeImpacts?: NewsImpact[]
}

/** 资讯流初始 6 条：LPR 中性 / 白酒利好茅台✦ / 宁德利好 / 美联储利空纳指✦ / 港股估值中性 / 隆基 pending */
const initialSeeds: MockSeed[] = [
  {
    item: {
      id: 1,
      source: '中国汽车动力电池产业创新联盟',
      category: 'industry',
      title: '8月新能源汽车产销同比增逾三成，动力电池装机量创单月历史新高',
      summary: '8月我国动力电池装车量47.0GWh，同比+35.3%、环比+7.1%；宁德时代装机市占率45.2%，环比提升1.8个百分点。',
      published_at: minutesAgo(12),
      analysis_status: 'done',
      url: 'https://www.catm.com.cn',
      impacts: [{
        direction: 'positive',
        strength: '强',
        horizon: '中期',
        quote: '8月我国动力电池装车量47.0GWh，同比增长35.3%；宁德时代装机市占率45.2%，环比+1.8pct。',
        fact: '新能源车产销高增带动动力电池装机创单月新高，龙头份额环比回升',
        logic: '装机高增叠加份额提升，出货量与产能利用率上行、盈利弹性释放，利好宁德时代',
        confidence: 0.88,
        targets: [
          { type: 'stock', name: '宁德时代', code: '300750', hit_watchlist: true },
          { type: 'industry', name: '动力电池', code: 'BK1033', hit_watchlist: false },
        ],
      }],
    },
  },
  {
    item: {
      id: 2,
      source: '财联社',
      category: 'industry',
      title: '双节白酒动销环比改善约两成，飞天茅台批价企稳回升至2480元',
      summary: '渠道调研显示高端白酒终端动销环比改善约20%，飞天茅台散瓶批价周环比上涨1.6%，经销商回款进度符合预期。',
      published_at: minutesAgo(40),
      analysis_status: 'done',
      url: 'https://www.cls.cn',
      impacts: [{
        direction: 'positive',
        strength: '中',
        horizon: '中期',
        quote: '本周飞天茅台散瓶批价2480元，周环比+1.6%；双节终端动销环比改善约20%。',
        fact: '高端白酒需求边际回暖，龙头批价止跌回升',
        logic: '批价企稳改善渠道利润与经销商信心，白酒板块盈利预期上修，利好龙头贵州茅台',
        confidence: 0.82,
        targets: [
          { type: 'stock', name: '贵州茅台', code: '600519', hit_watchlist: true },
          { type: 'industry', name: '白酒', code: 'BK0477', hit_watchlist: false },
        ],
      }],
    },
  },
  {
    item: {
      id: 3,
      source: '上交所公告',
      category: 'announcement',
      title: '隆基绿能：拟发行GDR募集不超50亿元，70%投向高效电池产能建设',
      summary: '公司拟发行全球存托凭证并在瑞士证券交易所上市，募集资金约70%用于高效电池产能建设项目，其余补充流动资金。',
      published_at: minutesAgo(65),
      analysis_status: 'pending',
      impacts: [],
    },
    analyzeImpacts: [{
      direction: 'positive',
      strength: '中',
      horizon: '长期',
      quote: '本次发行拟募集资金不超过50亿元，其中约70%将用于高效电池产能建设项目。',
      fact: '募资主要投向高效电池产能，强化技术代际领先',
      logic: '产能升级巩固龙头地位，短期虽有摊薄压力，综合影响偏正面',
      confidence: 0.71,
      targets: [
        { type: 'stock', name: '隆基绿能', code: '601012', hit_watchlist: false },
        { type: 'industry', name: '光伏设备', code: 'BK1015', hit_watchlist: false },
      ],
    }],
  },
  {
    item: {
      id: 4,
      source: '华尔街见闻',
      category: 'macro',
      title: '美联储票委密集放鹰，11月降息概率定价由78%降至54%，科技股承压',
      summary: '多位FOMC票委表态通胀黏性超预期，市场对11月降息概率的定价大幅回落，10年期美债利率反弹至4.1%，纳指期货跌0.8%。',
      published_at: minutesAgo(180),
      analysis_status: 'done',
      url: 'https://www.wallstreetcn.com',
      impacts: [{
        direction: 'negative',
        strength: '中',
        horizon: '短期',
        quote: '通胀黏性使得进一步宽松的门槛提高，年内或仅余一次降息空间。',
        fact: '降息预期降温推升美债利率，高估值成长股估值承压',
        logic: '贴现率上行直接压制长久期科技股估值，纳斯达克指数短期承压',
        confidence: 0.79,
        targets: [
          { type: 'index', name: '纳斯达克指数', code: 'NDX', hit_watchlist: true },
          { type: 'stock', name: '苹果', code: 'AAPL', hit_watchlist: false },
        ],
      }],
    },
  },
  {
    item: {
      id: 5,
      source: '中国人民银行',
      category: 'macro',
      title: '9月LPR报价出炉：1年期3.35%、5年期以上3.85%，连续四个月维持不变',
      summary: '本月LPR继续按兵不动，市场此前预期的下调落空；业内人士认为四季度仍有降息窗口，宽松节奏后移而非终止。',
      published_at: minutesAgo(300),
      analysis_status: 'done',
      url: 'https://www.pbc.gov.cn',
      impacts: [{
        direction: 'neutral',
        strength: '弱',
        horizon: '短期',
        quote: '2026年9月LPR为：1年期3.35%，5年期以上3.85%，均与上期持平。',
        fact: 'LPR连续四个月维持不变，宽松节奏低于市场预期',
        logic: '政策利率按兵不动，流动性边际变化有限，对风险资产影响偏中性',
        confidence: 0.9,
        targets: [
          { type: 'macro', name: '流动性', code: 'LPR', hit_watchlist: false },
        ],
      }],
    },
  },
  {
    item: {
      id: 6,
      source: '中信证券研究',
      category: 'report',
      title: '研报：恒指前瞻PE处近十年18%分位，港股性价比凸显但催化待确认',
      summary: '当前恒生指数前瞻市盈率9.2倍，处于近十年18%分位；AH溢价指数148，南向资金连续六周净流入，等待盈利与流动性共振。',
      published_at: minutesAgo(1560),
      analysis_status: 'done',
      url: 'https://www.citics.com',
      impacts: [{
        direction: 'neutral',
        strength: '中',
        horizon: '长期',
        quote: '恒指前瞻PE 9.2倍（近十年18%分位），风险溢价仍处历史高位。',
        fact: '港股估值处于历史低位区间，但盈利上修与增量资金仍待确认',
        logic: '低估值提供安全边际，方向取决于盈利与流动性共振，短期维持中性判断',
        confidence: 0.75,
        targets: [
          { type: 'index', name: '恒生指数', code: 'HSI', hit_watchlist: false },
        ],
      }],
    },
  },
]

/** "立即采集"待入库池（初始均 pending，采集后进入资讯流） */
const collectPool: MockSeed[] = [
  {
    item: {
      id: 101,
      source: '宁德时代公告',
      category: 'announcement',
      title: '宁德时代发布第三代神行超充电池：充电5分钟，续航520公里',
      summary: '公司发布第三代神行超充电池，峰值充电倍率4C，官宣2027年量产装车，已获多家车企定点。',
      published_at: minutesAgo(3),
      analysis_status: 'pending',
      impacts: [],
    },
    analyzeImpacts: [{
      direction: 'positive',
      strength: '强',
      horizon: '长期',
      quote: '第三代神行超充电池峰值充电倍率达4C，充电5分钟可补能520公里，计划2027年量产装车。',
      fact: '快充技术代际领先并获多家车企定点',
      logic: '技术溢价强化产品竞争力，有望提升市占率与单位盈利，利好宁德时代',
      confidence: 0.81,
      targets: [
        { type: 'stock', name: '宁德时代', code: '300750', hit_watchlist: true },
        { type: 'industry', name: '动力电池', code: 'BK1033', hit_watchlist: false },
      ],
    }],
  },
  {
    item: {
      id: 102,
      source: '新华社',
      category: 'macro',
      title: '国常会部署一揽子增量政策，进一步提振资本市场信心',
      summary: '会议部署稳增长一揽子增量政策，提及统筹运用降准降息工具、大力引导中长期资金入市。',
      published_at: minutesAgo(6),
      analysis_status: 'pending',
      impacts: [],
    },
    analyzeImpacts: [{
      direction: 'positive',
      strength: '中',
      horizon: '短期',
      quote: '统筹运用降准和公开市场操作工具，保持流动性合理充裕；大力引导中长期资金入市。',
      fact: '政策定调宽松加码，明确引导中长期资金入市',
      logic: '流动性与风险偏好双重改善，权重指数估值中枢有望上移',
      confidence: 0.77,
      targets: [
        { type: 'index', name: '沪深300', code: '000300', hit_watchlist: false },
        { type: 'macro', name: '流动性', code: 'POLICY', hit_watchlist: false },
      ],
    }],
  },
]

/** 可变 mock 状态：新 -> 旧 */
let mockItems: NewsItem[] = initialSeeds.map(s => s.item)
const analyzeTemplates = new Map<number, NewsImpact[]>()
for (const s of [...initialSeeds, ...collectPool]) {
  if (s.analyzeImpacts) analyzeTemplates.set(s.item.id, s.analyzeImpacts)
}

function mockList(params: NewsListParams): NewsListResp {
  const { category, q, limit = 20, offset = 0 } = params
  const kw = q?.trim().toLowerCase()
  const filtered = mockItems.filter(it => {
    if (category && it.category !== category) return false
    if (kw && !(`${it.title}${it.summary}${it.source}`.toLowerCase().includes(kw))) return false
    return true
  })
  return { total: filtered.length, items: filtered.slice(offset, offset + limit) }
}

function mockCollect(): CollectResp {
  const seed = collectPool.shift()
  if (!seed) return { added: 0 }
  mockItems = [seed.item, ...mockItems]
  return { added: 1 }
}

function mockAnalyze(): AnalyzeResp {
  let analyzed = 0
  mockItems = mockItems.map(it => {
    if (it.analysis_status !== 'pending') return it
    const impacts = analyzeTemplates.get(it.id)
    analyzed++
    return { ...it, analysis_status: 'done' as const, impacts: impacts ?? it.impacts }
  })
  return { analyzed }
}

function mockDigest(): WatchlistDigest[] {
  const map = new Map<string, WatchlistDigest>()
  for (const it of mockItems) {
    for (const im of it.impacts) {
      for (const t of im.targets) {
        if (!t.hit_watchlist) continue
        const key = t.code || t.name
        if (!map.has(key)) {
          map.set(key, { code: t.code, name: t.name, direction: im.direction, count: 1, latest_title: it.title })
        } else {
          map.get(key)!.count++
        }
      }
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count)
}

// ---------------------------------------------------------------------------
// API：真接口优先，失败自动降级 mock
// ---------------------------------------------------------------------------

export const newsApi = {
  list: async (params: NewsListParams = {}): Promise<NewsListResp> => {
    try {
      return await http.get<NewsListResp>('/news', { params }).then(r => r.data)
    } catch {
      return mockList(params)
    }
  },
  detail: async (id: number): Promise<NewsItem> => {
    try {
      return await http.get<NewsItem>(`/news/${id}`).then(r => r.data)
    } catch {
      const it = mockItems.find(i => i.id === id)
      if (!it) throw new Error(`news ${id} not found`)
      return it
    }
  },
  collect: async (): Promise<CollectResp> => {
    try {
      return await http.post<CollectResp>('/news/collect').then(r => r.data)
    } catch {
      return mockCollect()
    }
  },
  analyze: async (): Promise<AnalyzeResp> => {
    try {
      return await http.post<AnalyzeResp>('/news/analyze').then(r => r.data)
    } catch {
      return mockAnalyze()
    }
  },
  watchlistDigest: async (): Promise<WatchlistDigest[]> => {
    try {
      return await http.get<WatchlistDigest[]>('/news/watchlist-digest').then(r => r.data)
    } catch {
      return mockDigest()
    }
  },
}
