import { createRouter, createWebHistory } from 'vue-router'
import CasesView from '../views/CasesView.vue'
import CaseDetailView from '../views/CaseDetailView.vue'
import KnowledgeView from '../views/KnowledgeView.vue'
import SettingsView from '../views/SettingsView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/cases' },
    { path: '/cases', component: CasesView },
    { path: '/cases/:id', component: CaseDetailView },
    { path: '/knowledge', component: KnowledgeView },
    { path: '/settings', component: SettingsView }
  ]
})
