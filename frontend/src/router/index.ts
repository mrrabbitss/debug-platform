import { createRouter, createWebHistory } from 'vue-router'
import { api } from '../api/client'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    { path: '/', redirect: '/cases' },
    { path: '/cases', component: () => import('../views/CasesView.vue'), meta: { title: '故障定位' } },
    { path: '/cases/:id', component: () => import('../views/CaseDetailView.vue'), meta: { title: '案例详情' } },
    { path: '/knowledge', component: () => import('../views/KnowledgeWorkbenchView.vue'), meta: { title: '知识库' } },
    { path: '/settings', component: () => import('../views/WorkbenchSettingsView.vue'), meta: { title: '系统设置' } },
    { path: '/knowledge-management', component: () => import('../views/KnowledgeManagementView.vue'), meta: { title: '知识库管理', requiresKnowledgeManager: true } },
    { path: '/admin/knowledge', alias: '/knowledge/manage', redirect: '/knowledge-management?tab=maintenance', meta: { requiresKnowledgeManager: true } },
    { path: '/admin/curation', alias: '/knowledge/curation', component: () => import('../views/KnowledgeCurationView.vue'), meta: { title: 'AI 案例提炼', requiresContributor: true } },
    { path: '/admin/cognitive-search', alias: '/cognitive-search', component: () => import('../views/CognitiveSearchView.vue'), meta: { title: '检索检查', requiresKnowledgeManager: true } },
    { path: '/admin/agent-runs', alias: '/agent-runs', component: () => import('../views/AgentRunsView.vue'), meta: { title: '运行轨迹', requiresKnowledgeManager: true } },
    { path: '/admin/quality-governance', alias: '/quality-governance', component: () => import('../views/QualityGovernanceView.vue'), meta: { title: '质量与治理', requiresKnowledgeManager: true } },
    { path: '/admin/models', component: () => import('../views/SettingsView.vue'), meta: { title: '模型与索引', requiresAdmin: true } },
    { path: '/admin/security', alias: '/security', component: () => import('../views/SecurityView.vue'), meta: { title: '安全与审计', requiresExpert: true } },
    { path: '/:pathMatch(.*)*', redirect: '/cases' }
  ]
})

router.beforeEach(async to => {
  if (!to.meta.requiresAdmin && !to.meta.requiresKnowledgeManager && !to.meta.requiresExpert && !to.meta.requiresContributor) return true
  try {
    const { data } = await api.get('/workbench/bootstrap')
    const role = data.principal?.role
    if (role === 'ADMIN') return true
    if (!to.meta.requiresAdmin && role === 'EXPERT') return true
    if (to.meta.requiresContributor && role === 'ENGINEER') return true
    return { path: '/settings', query: { notice: to.meta.requiresAdmin ? 'admin-required' : 'role-required' }, replace: true }
  } catch {
    return { path: '/settings', query: { notice: 'access-unavailable' }, replace: true }
  }
})
router.afterEach(to => { document.title = `${to.meta.title || '工作台'} · GW/AP Debug` })
export default router
