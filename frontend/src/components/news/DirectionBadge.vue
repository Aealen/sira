<script setup lang="ts">
/** 方向徽章：利好=淡红底深红字 / 利空=淡绿底深绿字 / 中性=灰底（红涨绿跌） */
import { computed } from 'vue'
import { DIRECTION_LABELS, directionClass, type NewsDirection } from '../../api/news'

const props = defineProps<{ direction: NewsDirection }>()

const cls = computed(() => directionClass(props.direction))
const label = computed(() => DIRECTION_LABELS[props.direction] ?? props.direction)
</script>

<template>
  <span class="dir-badge" :class="cls">{{ label }}</span>
</template>

<style scoped>
.dir-badge {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 9999px;
  white-space: nowrap;
}
/* 利好：淡红底深红字 */
.is-positive { background: var(--sira-up-pale); color: var(--sira-up-deep); }
/* 利空：淡绿底深绿字 */
.is-negative { background: var(--sira-down-pale); color: var(--sira-down-deep); }
/* 中性：灰 */
.is-neutral { background: var(--sira-canvas-soft); color: var(--sira-body); }
</style>
