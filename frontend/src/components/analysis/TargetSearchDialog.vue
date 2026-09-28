<script setup lang="ts">
/** 标的分析·换标的搜索弹窗（复用 marketApi.search，风格对齐自选页搜索）。 */
import { computed, ref, watch } from 'vue'
import { ElDialog, ElIcon } from 'element-plus'
import { Right, Search } from '@element-plus/icons-vue'
import { MARKET_LABELS, marketApi, type SearchItem } from '../../api/market'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/icon/style/css'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'select', item: SearchItem): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const keyword = ref('')
const results = ref<SearchItem[]>([])
const searching = ref(false)

/** 每次打开清空上次搜索 */
watch(visible, (v) => {
  if (v) {
    keyword.value = ''
    results.value = []
  }
})

let timer: ReturnType<typeof setTimeout> | undefined

function onInput() {
  clearTimeout(timer)
  const q = keyword.value.trim()
  if (!q) {
    results.value = []
    return
  }
  timer = setTimeout(async () => {
    searching.value = true
    try {
      results.value = await marketApi.search(q, 8)
    } catch {
      results.value = []
    } finally {
      searching.value = false
    }
  }, 400)
}

function pick(item: SearchItem) {
  visible.value = false
  emit('select', item)
}
</script>

<template>
  <el-dialog v-model="visible" title="切换分析标的" width="480px">
    <div class="search-box">
      <el-icon :size="18" color="#868685"><Search /></el-icon>
      <input
        v-model="keyword"
        class="search-input"
        placeholder="搜索代码 / 名称 · A股 · ETF · 场外基金 · 港股 · 美股"
        autofocus
        @input="onInput"
      >
      <div v-if="searching" class="hint">搜索中…</div>
      <div v-else-if="keyword.trim() && !results.length" class="hint">未找到匹配标的</div>
    </div>

    <div class="result-list">
      <div v-for="s in results" :key="s.market + s.code" class="result-row" @click="pick(s)">
        <span class="market-tag">{{ MARKET_LABELS[s.market] ?? s.market }}</span>
        <span class="name">{{ s.name }}</span>
        <span class="code">{{ s.code }}</span>
        <el-icon class="go"><Right /></el-icon>
      </div>
      <div v-if="!results.length && !keyword.trim()" class="empty-hint">输入代码或名称开始搜索</div>
    </div>
  </el-dialog>
</template>

<style scoped>
:deep(.el-dialog) {
  border-radius: 20px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: var(--sira-canvas-soft);
  border-radius: var(--sira-radius-input);
}

.search-input {
  flex: 1;
  border: none;
  outline: none;
  font-size: 14px;
  color: var(--sira-ink);
  background: transparent;
}

.hint {
  font-size: 12px;
  color: var(--sira-mute);
}

.result-list {
  max-height: 320px;
  overflow-y: auto;
  margin-top: 8px;
}

.result-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--sira-radius-sm);
  cursor: pointer;
}

.result-row:hover {
  background: var(--sira-canvas-soft);
}

.market-tag {
  font-size: 12px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: 9999px;
  padding: 1px 8px;
  white-space: nowrap;
}

.name {
  font-weight: 500;
}

.code {
  color: var(--sira-mute);
  font-size: 13px;
  margin-left: auto;
}

.go {
  color: var(--sira-body);
}

.empty-hint {
  text-align: center;
  color: var(--sira-mute);
  padding: 28px 0;
  font-size: 13px;
}
</style>
