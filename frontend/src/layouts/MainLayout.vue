<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Calendar,
  Coin,
  DataAnalysis,
  Reading,
  TrendCharts,
  Wallet,
} from '@element-plus/icons-vue'
import { trendClass } from '../api/market'
import { fmtMoney, fmtSignedMoney, simApi, type SimAccount } from '../api/sim'

const route = useRoute()
const router = useRouter()

/** 导航六项：一期五屏 + 量化预研（二期，灰显不可点，对应原型导航第六项） */
const navItems = [
  { key: 'watchlist', label: '自选行情', icon: TrendCharts, enabled: true },
  { key: 'analysis', label: '标的分析', icon: Coin, enabled: true },
  { key: 'simulation', label: '模拟盘', icon: Wallet, enabled: true },
  { key: 'news', label: '资讯', icon: Reading, enabled: true },
  { key: 'invest', label: '定投复盘', icon: Calendar, enabled: true },
  { key: 'quant', label: '量化预研', icon: DataAnalysis, enabled: false },
]

const activeKey = computed(() => String(route.name ?? ''))

/* —— 底部账户摘要：挂载即拉取 + 15s 轮询；失败显示 — —— */
const POLL_MS = 15_000
const account = ref<SimAccount | null>(null)
let pollTimer: ReturnType<typeof setInterval> | undefined
let polling = false

async function refreshAccount() {
  if (polling) return // 上一次请求未返回时跳过本轮，避免堆积
  polling = true
  try {
    account.value = await simApi.account()
  } catch {
    account.value = null // 接口失败显示 —
  } finally {
    polling = false
  }
}

onMounted(() => {
  refreshAccount()
  pollTimer = setInterval(refreshAccount, POLL_MS)
})

onUnmounted(() => clearInterval(pollTimer))
</script>

<template>
  <div class="layout">
    <aside class="nav">
      <div class="logo">
        <span class="logo-dot" />
        <span class="logo-text">SIRA</span>
      </div>
      <nav class="nav-list">
        <div
          v-for="item in navItems"
          :key="item.key"
          class="nav-item"
          :class="{ active: activeKey === item.key, disabled: !item.enabled }"
          @click="item.enabled && router.push({ name: item.key })"
        >
          <el-icon :size="18"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
          <span v-if="!item.enabled" class="nav-badge">二期</span>
        </div>
      </nav>
      <div class="account">
        <div class="account-label">模拟盘账户</div>
        <div class="account-value">{{ account ? fmtMoney(account.total_asset) : '—' }}</div>
        <div class="account-pnl">
          <span class="account-pnl-label">盈亏</span>
          <span v-if="account" :class="trendClass(account.total_pnl)">{{ fmtSignedMoney(account.total_pnl) }}</span>
          <span v-else>—</span>
        </div>
      </div>
    </aside>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
}

.nav {
  width: 220px;
  flex-shrink: 0;
  background: var(--sira-canvas);
  display: flex;
  flex-direction: column;
  padding: 20px 12px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 12px 20px;
}

.logo-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--sira-primary);
}

.logo-text {
  font-size: 20px;
  font-weight: 900;
  letter-spacing: 2px;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--sira-radius-sm);
  color: var(--sira-body);
  cursor: pointer;
  font-weight: 500;
}

.nav-item:hover:not(.disabled) {
  background: var(--sira-canvas-soft);
}

.nav-item.active {
  background: var(--sira-primary-pale);
  color: var(--sira-ink-deep);
  font-weight: 600;
}

.nav-item.disabled {
  color: var(--sira-mute);
  cursor: not-allowed;
}

.nav-badge {
  margin-left: auto;
  font-size: 12px;
  color: var(--sira-mute);
  background: var(--sira-canvas-soft);
  border-radius: 9999px;
  padding: 1px 8px;
}

.account {
  margin-top: auto;
  background: var(--sira-canvas-soft);
  border-radius: var(--sira-radius-sm);
  padding: 10px 12px;
}

.account-label {
  font-size: 12px;
  color: var(--sira-mute);
}

.account-value {
  font-size: 16px;
  font-weight: 600;
  color: var(--sira-body);
  font-variant-numeric: tabular-nums;
}

.account-pnl {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-top: 2px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.account-pnl-label {
  color: var(--sira-mute);
}

.account-pnl .up { color: var(--sira-up); font-weight: 600; }
.account-pnl .down { color: var(--sira-down-deep); font-weight: 600; }

.main {
  flex: 1;
  overflow-y: auto;
  background: var(--sira-canvas-soft);
  padding: 20px 24px;
}
</style>
