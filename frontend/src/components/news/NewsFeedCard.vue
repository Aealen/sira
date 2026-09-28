<script setup lang="ts">
/** 资讯流卡片：来源时间 caption / 标题 / 摘要一行 / 影响链行（方向徽章 + 对象 chips + 传导逻辑）。
 * analysis_status=pending 时以灰底"影响分析中…"徽章代替影响链。 */
import { computed } from 'vue'
import { CATEGORY_LABELS, fmtNewsTime, type ImpactTarget, type NewsDirection, type NewsItem } from '../../api/news'
import DirectionBadge from './DirectionBadge.vue'
import TargetChip from './TargetChip.vue'

const props = defineProps<{ item: NewsItem }>()

const emit = defineEmits<{ (e: 'open', item: NewsItem): void }>()

const isPending = computed(() => props.item.analysis_status === 'pending' || props.item.impacts.length === 0)

/** 主方向：取首条影响（流卡仅展示单一主导方向，明细见阅读抽屉） */
const mainDirection = computed<NewsDirection | null>(() => props.item.impacts[0]?.direction ?? null)

const logicText = computed(() => props.item.impacts[0]?.logic ?? '')

/** 汇总去重全部影响对象，命中自选的排前 */
const targets = computed<ImpactTarget[]>(() => {
  const seen = new Set<string>()
  const list: ImpactTarget[] = []
  for (const im of props.item.impacts) {
    for (const t of im.targets) {
      const key = `${t.type}/${t.code}/${t.name}`
      if (seen.has(key)) continue
      seen.add(key)
      list.push(t)
    }
  }
  return list.sort((a, b) => Number(b.hit_watchlist) - Number(a.hit_watchlist))
})

const categoryLabel = computed(() => CATEGORY_LABELS[props.item.category] ?? props.item.category)
</script>

<template>
  <article class="news-card sira-card" @click="emit('open', item)">
    <div class="caption">
      <span class="src">{{ item.source }}</span>
      <span class="dot">·</span>
      <span>{{ fmtNewsTime(item.published_at) }}</span>
      <span class="cat">{{ categoryLabel }}</span>
    </div>
    <div class="title">{{ item.title }}</div>
    <div class="summary">{{ item.summary }}</div>

    <div v-if="isPending" class="pending-badge">影响分析中…</div>
    <div v-else class="impact-row">
      <DirectionBadge v-if="mainDirection" :direction="mainDirection" />
      <TargetChip v-for="t in targets" :key="t.type + t.code + t.name" :target="t" />
      <span class="logic" :title="logicText">{{ logicText }}</span>
    </div>
  </article>
</template>

<style scoped>
.news-card {
  cursor: pointer;
  transition: background 0.15s;
  padding: 18px 22px;
}
.news-card:hover { background: #fbfdf9; }

.caption {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sira-mute);
}
.src { color: var(--sira-body); }
.dot { color: var(--sira-mute); }
.cat {
  margin-left: auto;
  font-size: 12px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: 9999px;
  padding: 2px 8px;
  white-space: nowrap;
}

.title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
  margin-top: 8px;
  color: var(--sira-ink);
}

.summary {
  margin-top: 6px;
  font-size: 13px;
  color: var(--sira-body);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-badge {
  display: inline-flex;
  align-items: center;
  margin-top: 10px;
  font-size: 12px;
  line-height: 1;
  padding: 4px 10px;
  border-radius: 9999px;
  background: var(--sira-canvas-soft);
  color: var(--sira-mute);
}

.impact-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  min-width: 0;
}
.logic {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--sira-mute);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
