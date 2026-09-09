import { ref, computed } from 'vue'
import { api } from '../api/client'
import type { Principal } from '../types'

export interface WorkbenchConfig {
  categories: { id: string; name: string }[]
  knowledge_roles: Record<string, string>
  models: { id: string; name: string; active: boolean }[]
  preferences: { chat_profile_id?: string | null }
  principal: Partial<Principal>
}

export function useWorkbench() {
  const config = ref<WorkbenchConfig>({ categories: [], knowledge_roles: {}, models: [], preferences: {}, principal: {} })
  const configLoading = ref(false)
  const configError = ref('')
  const isAdmin = computed(() => config.value.principal.role === 'ADMIN')
  const canSubmit = computed(() => ['ADMIN', 'ENGINEER'].includes(config.value.principal.role || ''))
  const caseCategories = computed(() => config.value.categories.filter(item => item.id !== 'general'))
  async function loadConfig() {
    configLoading.value = true
    configError.value = ''
    try { config.value = (await api.get<WorkbenchConfig>('/workbench/bootstrap')).data }
    catch (error) { configError.value = failure(error); throw error }
    finally { configLoading.value = false }
  }
  function categoryName(id?: string) {
    return config.value.categories.find(item => item.id === id)?.name || ({ network: '组网问题', connection: '连接问题', unknown: '未知', general: '通用知识' } as Record<string, string>)[id || 'unknown'] || id
  }
  return { config, isAdmin, canSubmit, caseCategories, configLoading, configError, loadConfig, categoryName }
}

export function failure(error: any): string {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item.msg || '输入无效').join('；')
  if (error?.response?.status === 403) return '当前账号没有此操作权限'
  if (error?.response?.status === 409) return '内容已变化，请刷新后重新核对'
  if (error?.code === 'ECONNABORTED') return '请求超时，请刷新检查是否已完成，再决定重试'
  return error?.message === 'Network Error' ? '无法连接服务器，请检查连接后重试' : error?.message || '操作失败，请重试'
}
