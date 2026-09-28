<script setup lang="ts">
/** 资讯阅读抽屉（右侧 480px）：完整标题 / 来源时间 / 摘要 / 影响分析卡
 * —— 信息点列表（竖线+引号原文+归纳事实）、影响对象矩阵、传导链纵向步骤条；底部"查看原文"外链。 */
import { computed } from 'vue'
import {
  CATEGORY_LABELS,
  TARGET_TYPE_LABELS,
  fmtConfidence,
  fmtNewsTime,
  fmtStrength,
  type NewsDirection,
  type NewsItem,
} from '../../api/news'
import DirectionBadge from './DirectionBadge.vue'

const props = defineProps<{
  /** 当前资讯（null 关闭）；父组件负责请求详情并传入 */
  news: NewsItem | null
  loading?: boolean
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const opened = computed(() => !!props.news)

const isPending = computed(() => !props.news || props.news.analysis_status === 'pending' || props.news.impacts.length === 0)

const categoryLabel = computed(() => (props.news ? CATEGORY_LABELS[props.news.category] ?? props.news.category : ''))

/** 影响对象矩阵行：impacts × targets 展开（同一对象在不同影响下方向/强度不同则各占一行） */
const matrixRows = computed(() => {
  const rows: { name: string; hit: boolean; typeLabel: string; direction: NewsDirection; strength: string }[] = []
  if (!props.news) return rows
  for (const im of props.news.impacts) {
    for (const t of im.targets) {
      rows.push({
        name: t.name,
        hit: t.hit_watchlist,
        typeLabel: TARGET_TYPE_LABELS[t.type] ?? t.type,
        direction: im.direction,
        strength: fmtStrength(im.strength),
      })
    }
  }
  return rows
})
</script>

<template>
  <el-drawer
    :model-value="opened"
    size="480px"
    direction="rtl"
    :with-header="false"
    append-to-body
    class="news-read-drawer"
    @close="emit('close')"
  >
    <div v-if="loading" class="state-box">加载详情中…</div>

    <template v-else-if="news">
      <!-- 两段式管道：该条仍在分析中时，抽屉顶部提示条 -->
      <el-alert
        v-if="isPending"
        class="pending-tip"
        type="info"
        :closable="false"
        show-icon
        title="影响分析中…"
        description="AI 正在解析这条快讯对相关标的的影响，分析就绪后此处自动更新"
      />

      <div class="caption">
        <span class="src">{{ news.source }}</span>
        <span class="dot">·</span>
        <span>{{ fmtNewsTime(news.published_at) }}</span>
        <span class="cat">{{ categoryLabel }}</span>
      </div>

      <h3 class="d-title">{{ news.title }}</h3>
      <p class="d-summary">{{ news.summary }}</p>

      <!-- 影响分析卡 -->
      <div v-if="!isPending" class="analysis-card">
        <!-- 信息点列表：竖线 + 引号原文 + 归纳事实 -->
        <div class="sec-title">信息点</div>
        <div v-for="(im, i) in news.impacts" :key="`q-${i}`" class="quote-block">
          <p class="quote">“{{ im.quote }}”</p>
          <p class="fact">{{ im.fact }}</p>
        </div>

        <!-- 影响对象矩阵 -->
        <div class="sec-title">影响对象</div>
        <table class="matrix">
          <thead>
            <tr><th>对象</th><th>类型</th><th>方向</th><th>强度</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in matrixRows" :key="i">
              <td>
                <span class="m-name" :class="{ hit: row.hit }">
                  <span v-if="row.hit" class="star">✦</span>{{ row.name }}
                </span>
              </td>
              <td>{{ row.typeLabel }}</td>
              <td><DirectionBadge :direction="row.direction" /></td>
              <td>{{ row.strength }}</td>
            </tr>
          </tbody>
        </table>

        <!-- 传导链：事实 → 逻辑 → 方向结论（纵向步骤条） -->
        <div class="sec-title">传导链</div>
        <div v-for="(im, i) in news.impacts" :key="`c-${i}`" class="chain">
          <div class="chain-step">
            <span class="node" />
            <div class="step-body">
              <div class="step-label">事实</div>
              <div class="step-text">{{ im.fact }}</div>
            </div>
          </div>
          <div class="chain-line" />
          <div class="chain-step">
            <span class="node" />
            <div class="step-body">
              <div class="step-label">逻辑</div>
              <div class="step-text">{{ im.logic }}</div>
            </div>
          </div>
          <div class="chain-line" />
          <div class="chain-step">
            <span class="node node-end" />
            <div class="step-body">
              <div class="step-label">结论</div>
              <div class="step-conclusion">
                <DirectionBadge :direction="im.direction" />
                <span class="conclusion-meta">
                  {{ im.horizon }} · 强度 {{ fmtStrength(im.strength) }} · 置信 {{ fmtConfidence(im.confidence) }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <a
        v-if="news.url"
        class="origin-link"
        :href="news.url"
        target="_blank"
        rel="noopener noreferrer"
      >查看原文 →</a>
    </template>
  </el-drawer>
</template>

<style scoped>
.state-box { padding: 60px 0; text-align: center; color: var(--sira-mute); }

.caption {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sira-mute);
}
.src { color: var(--sira-body); }
.cat {
  margin-left: auto;
  font-size: 12px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: 9999px;
  padding: 2px 8px;
  white-space: nowrap;
}

.d-title {
  margin: 10px 0 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--sira-ink);
}
.d-summary {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sira-body);
}

.pending-tip { margin-bottom: 14px; }

.analysis-card {
  margin-top: 18px;
  background: var(--sira-canvas-soft);
  border-radius: var(--sira-radius-input);
  padding: 14px 16px 16px;
}

.sec-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--sira-mute);
  letter-spacing: 1px;
  margin: 6px 0 10px;
}
.sec-title:not(:first-child) { margin-top: 18px; }

/* 信息点：竖线 + 引号 + 事实 */
.quote-block {
  position: relative;
  padding: 2px 0 2px 12px;
  margin-bottom: 12px;
}
.quote-block::before {
  content: '';
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 3px;
  border-radius: 2px;
  background: var(--sira-primary);
}
.quote {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sira-ink);
}
.fact {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--sira-body);
}

/* 影响对象矩阵 */
.matrix {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.matrix th {
  text-align: left;
  font-weight: 500;
  color: var(--sira-mute);
  padding: 4px 6px 6px;
  border-bottom: 1px solid #d6dbd0;
}
.matrix td {
  padding: 8px 6px;
  border-bottom: 1px solid #dfe5da;
  color: var(--sira-body);
  vertical-align: middle;
}
.matrix tr:last-child td { border-bottom: none; }
.m-name { color: var(--sira-ink); font-weight: 500; }
.m-name.hit { color: var(--sira-ink-deep); font-weight: 600; background: var(--sira-primary-pale); border-radius: 9999px; padding: 2px 8px; }
.star { margin-right: 1px; }

/* 传导链步骤条 */
.chain { padding: 2px 0 6px; }
.chain-step { display: flex; gap: 10px; align-items: flex-start; }
.node {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--sira-mute);
}
.node-end { background: var(--sira-primary); }
.chain-line {
  width: 2px;
  height: 16px;
  margin-left: 3px;
  background: #cfd6c9;
  border-radius: 1px;
}
.step-label { font-size: 11px; color: var(--sira-mute); }
.step-text { font-size: 13px; line-height: 1.6; color: var(--sira-ink); margin-top: 2px; }
.step-conclusion { display: flex; align-items: center; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
.conclusion-meta { font-size: 12px; color: var(--sira-mute); }

.origin-link {
  display: block;
  text-align: center;
  margin-top: 18px;
  padding: 10px 0;
  border-radius: 9999px;
  background: var(--sira-primary);
  color: var(--sira-ink-deep);
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;
}
.origin-link:hover { background: #cdffad; }
</style>
