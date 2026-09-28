<script setup lang="ts">
/** 右侧聚合卡："今日影响你自选" —— 命中标的列表（✦标的 + 方向徽章 + 条数 + 最新标题） */
import type { WatchlistDigest } from '../../api/news'
import DirectionBadge from './DirectionBadge.vue'

defineProps<{ digests: WatchlistDigest[] }>()
</script>

<template>
  <div class="digest-card sira-card">
    <div class="head">
      <div class="title">今日影响你自选</div>
      <div class="sub">命中 {{ digests.length }} 个标的</div>
    </div>

    <div v-if="!digests.length" class="empty">
      今日资讯暂未命中你的自选标的
    </div>

    <div v-for="d in digests" :key="d.code || d.name" class="row">
      <div class="row-main">
        <span class="hit-chip"><span class="star">✦</span>{{ d.name }}</span>
        <DirectionBadge :direction="d.direction" />
        <span class="count">{{ d.count }} 条</span>
      </div>
      <div class="latest" :title="d.latest_title">{{ d.latest_title }}</div>
    </div>

    <div class="foot">✦ 标记为你的自选标的 · 点击左侧资讯查看完整影响分析</div>
  </div>
</template>

<style scoped>
.digest-card { padding: 18px 20px; }

.head { margin-bottom: 12px; }
.title { font-size: 15px; font-weight: 600; color: var(--sira-ink); }
.sub { font-size: 12px; color: var(--sira-mute); margin-top: 2px; }

.empty {
  font-size: 13px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: var(--sira-radius-sm);
  padding: 18px 12px;
  text-align: center;
}

.row {
  padding: 10px 0;
  border-bottom: 1px solid #eef1ea;
}
.row:last-of-type { border-bottom: none; }

.row-main { display: flex; align-items: center; gap: 8px; min-width: 0; }
.hit-chip {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 9999px;
  background: var(--sira-primary-pale);
  color: var(--sira-ink-deep);
  white-space: nowrap;
}
.star { margin-right: 2px; }
.count { margin-left: auto; font-size: 12px; color: var(--sira-mute); white-space: nowrap; }

.latest {
  margin-top: 6px;
  font-size: 12px;
  color: var(--sira-mute);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.foot {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #eef1ea;
  font-size: 12px;
  color: var(--sira-mute);
  line-height: 1.6;
}
</style>
