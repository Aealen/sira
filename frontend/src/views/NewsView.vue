<script setup lang="ts">
/** 屏4 资讯：分类 pill 筛选 + 资讯流（影响链行）+ 右侧"今日影响你自选"聚合卡 + 阅读抽屉。
 * 右上"立即采集 / 立即分析"为调试入口：POST 后刷新并提示新增条数。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, MagicStick } from '@element-plus/icons-vue'
import { newsApi, type NewsItem, type WatchlistDigest } from '../api/news'
import NewsFeedCard from '../components/news/NewsFeedCard.vue'
import NewsDrawer from '../components/news/NewsDrawer.vue'
import WatchDigestCard from '../components/news/WatchDigestCard.vue'

/** 分类 pill：'' = 全部（默认 lime 高亮） */
const CATEGORIES = [
  { value: '', label: '全部' },
  { value: 'macro', label: '宏观' },
  { value: 'industry', label: '行业' },
  { value: 'announcement', label: '公司公告' },
  { value: 'report', label: '研报' },
]

const PAGE_SIZE = 20

const activeCategory = ref('')
const items = ref<NewsItem[]>([])
const total = ref(0)
const listLoading = ref(false)

const digests = ref<WatchlistDigest[]>([])

const selected = ref<NewsItem | null>(null)
const detailLoading = ref(false)

const collecting = ref(false)
const analyzing = ref(false)

/** 列表中是否仍有"影响分析中"的条目（两段式管道：快讯先行 → 分析异步就绪） */
const hasPending = computed(() => items.value.some(it => it.analysis_status === 'pending'))

async function loadList(append = false, limit = PAGE_SIZE) {
  listLoading.value = true
  try {
    const offset = append ? items.value.length : 0
    const r = await newsApi.list({
      category: activeCategory.value || undefined,
      limit: append ? PAGE_SIZE : limit,
      offset,
    })
    total.value = r.total
    items.value = append ? [...items.value, ...r.items] : r.items
  } catch {
    ElMessage.error('资讯加载失败')
  } finally {
    listLoading.value = false
  }
}

async function loadDigest() {
  try {
    digests.value = await newsApi.watchlistDigest()
  } catch {
    digests.value = []
  }
}

function selectCategory(value: string) {
  if (activeCategory.value === value) return
  activeCategory.value = value
  loadList()
}

/** 点击条目 -> 打开阅读抽屉并拉取详情（先用列表数据占位，避免闪空） */
async function openNews(item: NewsItem) {
  selected.value = item
  detailLoading.value = true
  try {
    selected.value = await newsApi.detail(item.id)
  } catch {
    /* 详情失败时保留列表已有数据 */
  } finally {
    detailLoading.value = false
  }
}

/** 调试入口：立即采集 */
async function onCollect() {
  collecting.value = true
  try {
    const r = await newsApi.collect()
    ElMessage.success(`采集完成，新增 ${r.added} 条`)
    await Promise.all([loadList(), loadDigest()])
  } catch {
    ElMessage.error('采集失败')
  } finally {
    collecting.value = false
  }
}

/** 调试入口：立即分析（若抽屉打开则同步刷新抽屉内容） */
async function onAnalyze() {
  analyzing.value = true
  try {
    const r = await newsApi.analyze()
    ElMessage.success(`分析完成，处理 ${r.analyzed} 条`)
    await refreshAfterAnalyze()
  } catch {
    ElMessage.error('分析失败')
  } finally {
    analyzing.value = false
  }
}

/** 分析出结果后的刷新：保留已加载条数；抽屉打开时同步刷新抽屉内容 */
async function refreshAfterAnalyze() {
  const keep = Math.max(items.value.length, PAGE_SIZE)
  await Promise.all([loadList(false, keep), loadDigest()])
  if (selected.value) {
    try {
      selected.value = await newsApi.detail(selected.value.id)
    } catch {
      /* 刷新抽屉失败时保留原内容 */
    }
  }
}

// ---------------------------------------------------------------------------
// 分析中态自动轮询：存在 pending 条目时每 20s 静默触发一次 analyze，
// 有新分析结果（analyzed > 0）才刷新列表；全部 ready 后停止；页面不可见时暂停。
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 20_000
let pollTimer: ReturnType<typeof setInterval> | null = null

function startPollTimer() {
  if (pollTimer != null) return
  pollTimer = setInterval(() => void pollAnalyze(), POLL_INTERVAL_MS)
}

function stopPollTimer() {
  if (pollTimer == null) return
  clearInterval(pollTimer)
  pollTimer = null
}

/** 静默单次轮询：不弹任何提示，失败等下一轮 */
async function pollAnalyze() {
  if (document.visibilityState !== 'visible') return
  if (analyzing.value || listLoading.value || !hasPending.value) return
  try {
    const r = await newsApi.analyze()
    if (r.analyzed > 0) await refreshAfterAnalyze()
  } catch {
    /* 静默失败，等待下一轮 */
  }
}

/** pending 出现/消失时启停定时器 */
watch(hasPending, pending => {
  if (pending && document.visibilityState === 'visible') startPollTimer()
  else stopPollTimer()
})

/** 页面不可见暂停轮询，重新可见时若有 pending 则恢复 */
function onVisibilityChange() {
  if (document.visibilityState === 'hidden') stopPollTimer()
  else if (hasPending.value) startPollTimer()
}

onMounted(() => {
  loadList()
  loadDigest()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onBeforeUnmount(() => {
  stopPollTimer()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div class="news-page">
    <!-- 顶栏：分类 pill + 调试入口 -->
    <div class="sira-card top-bar">
      <div class="pills">
        <button
          v-for="c in CATEGORIES"
          :key="c.value"
          class="pill"
          :class="{ active: activeCategory === c.value }"
          type="button"
          @click="selectCategory(c.value)"
        >{{ c.label }}</button>
      </div>
      <div class="debug-actions">
        <el-button size="small" round :loading="collecting" @click="onCollect">
          <el-icon class="btn-icon"><Download /></el-icon>立即采集
        </el-button>
        <el-button size="small" round :loading="analyzing" @click="onAnalyze">
          <el-icon class="btn-icon"><MagicStick /></el-icon>立即分析
        </el-button>
      </div>
    </div>

    <div class="main">
      <!-- 左：资讯流 -->
      <div class="feed">
        <div v-if="listLoading && !items.length" class="sira-card state-card">加载资讯中…</div>

        <!-- 空态：全部分类下一条资讯都没有 = 采集管道未启动 -->
        <div v-else-if="!items.length && !activeCategory" class="sira-card news-empty">
          <div class="empty-title">暂无资讯 · 采集管道未启动</div>
          <div class="empty-desc">资讯由 AI 自动采集并做影响分析，点下方按钮拉取最新快讯</div>
          <el-button type="primary" round :loading="collecting" @click="onCollect">
            <el-icon class="btn-icon"><Download /></el-icon>立即采集
          </el-button>
        </div>

        <template v-else>
          <NewsFeedCard v-for="it in items" :key="it.id" :item="it" @open="openNews" />
          <div v-if="!items.length" class="sira-card state-card">该分类下暂无资讯</div>
        </template>

        <el-button
          v-if="items.length < total"
          class="more-btn"
          round
          plain
          :loading="listLoading"
          @click="loadList(true)"
        >加载更多 · {{ items.length }}/{{ total }}</el-button>

        <div class="foot-caption">资讯由 AI 自动采集与影响分析 · 学习用途，非投资建议</div>
      </div>

      <!-- 右：今日影响你自选 -->
      <aside class="side">
        <WatchDigestCard :digests="digests" />
      </aside>
    </div>

    <!-- 阅读抽屉 -->
    <NewsDrawer :news="selected" :loading="detailLoading" @close="selected = null" />
  </div>
</template>

<style scoped>
.news-page {
  max-width: 1080px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* 顶栏 */
.top-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  flex-wrap: wrap;
}
.pills { display: flex; gap: 8px; flex-wrap: wrap; flex: 1; min-width: 0; }
.pill {
  border: none;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  line-height: 1;
  padding: 7px 14px;
  border-radius: 9999px;
  background: transparent;
  color: var(--sira-body);
  transition: background 0.15s;
}
.pill:hover { background: var(--sira-canvas-soft); }
/* 选中：lime 底深字 */
.pill.active {
  background: var(--sira-primary);
  color: var(--sira-ink-deep);
  font-weight: 600;
}
.debug-actions { display: flex; gap: 8px; margin-left: auto; }
.btn-icon { margin-right: 4px; }

/* 主区：左流右卡 */
.main {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.feed {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.side {
  width: 280px;
  flex-shrink: 0;
  position: sticky;
  top: 0;
}
.state-card { text-align: center; color: var(--sira-mute); padding: 56px 24px; }

/* 资讯空态：采集管道未启动 */
.news-empty {
  text-align: center;
  padding: 56px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.empty-title { font-size: 16px; font-weight: 600; color: var(--sira-ink); }
.empty-desc { font-size: 13px; color: var(--sira-body); }

.more-btn { align-self: center; }

.foot-caption {
  text-align: center;
  font-size: 12px;
  color: var(--sira-mute);
  padding: 4px 0 12px;
}

/* 窄屏：聚合卡移到资讯流下方 */
@media (max-width: 920px) {
  .main { flex-direction: column; }
  .side { width: 100%; position: static; }
}
</style>
