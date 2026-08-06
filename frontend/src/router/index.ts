import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/cases' },
    { path: '/cases', component: () => import('../views/CasesView.vue') },
    { path: '/cases/:id', component: () => import('../views/CaseDetailView.vue') },
    { path: '/knowledge', component: () => import('../views/KnowledgeView.vue') },
    { path: '/knowledge/curation', component: () => import('../views/KnowledgeCurationView.vue') },
    { path: '/cognitive-search', component: () => import('../views/CognitiveSearchView.vue') },
    { path: '/agent-runs', component: () => import('../views/AgentRunsView.vue') },
    { path: '/quality-governance', component: () => import('../views/QualityGovernanceView.vue') },
    { path: '/settings', component: () => import('../views/SettingsView.vue') },
    { path: '/security', component: () => import('../views/SecurityView.vue') }
  ]
})
