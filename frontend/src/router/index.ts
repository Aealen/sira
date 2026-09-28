import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '../layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: MainLayout,
      redirect: '/watchlist',
      children: [
        { path: 'watchlist', name: 'watchlist', component: () => import('../views/WatchlistView.vue'), meta: { title: '自选行情' } },
        { path: 'analysis', name: 'analysis', component: () => import('../views/AnalysisView.vue'), meta: { title: '标的分析' } },
        { path: 'simulation', name: 'simulation', component: () => import('../views/SimulationView.vue'), meta: { title: '模拟盘' } },
        { path: 'news', name: 'news', component: () => import('../views/NewsView.vue'), meta: { title: '资讯' } },
        { path: 'invest', name: 'invest', component: () => import('../views/InvestView.vue'), meta: { title: '定投与复盘' } },
      ],
    },
  ],
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? 'SIRA')} · SIRA`
})

export default router
