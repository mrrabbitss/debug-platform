import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/cases' },
    { path: '/cases', component: () => import('../views/CasesView.vue') },
    { path: '/cases/:id', component: () => import('../views/CaseDetailView.vue') },
    { path: '/knowledge', component: () => import('../views/KnowledgeView.vue') },
    { path: '/settings', component: () => import('../views/SettingsView.vue') },
    { path: '/security', component: () => import('../views/SecurityView.vue') }
  ]
})
