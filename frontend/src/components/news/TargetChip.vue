<script setup lang="ts">
/** 影响对象 chip：行业/指数灰底；hit_watchlist 命中自选的用 lime 淡底 + ✦ 前缀 */
import type { ImpactTarget } from '../../api/news'

defineProps<{ target: ImpactTarget }>()
</script>

<template>
  <span class="t-chip" :class="{ hit: target.hit_watchlist }">
    <span v-if="target.hit_watchlist" class="star">✦</span>{{ target.name }}
  </span>
</template>

<style scoped>
.t-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  font-size: 12px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 9999px;
  background: var(--sira-canvas-soft);
  color: var(--sira-body);
  white-space: nowrap;
}
.t-chip.hit {
  background: var(--sira-primary-pale);
  color: var(--sira-ink-deep);
  font-weight: 600;
}
.star { margin-right: 1px; }
</style>
