import { api } from './client'

export interface ContributionCandidate {
  title: string
  content: string
  category_id?: string | null
  source_type?: string
  metadata?: Record<string, any>
}
export interface KnowledgeContribution {
  id: string
  owner_id: string
  operation: 'CREATE' | 'UPDATE' | 'DELETE'
  content_kind: 'KNOWLEDGE' | 'SKILL'
  target_document_id?: string | null
  source_curation_id?: string | null
  source_library_id?: string | null
  status: string
  version: number
  content_hash: string
  candidate: ContributionCandidate
  original: Partial<ContributionCandidate>
  diff: string
  messages?: { role: string; content: string }[]
  revisions?: { version: number; action: string; actor_id: string; comment: string; diff: string; created_at: string }[]
  publication_job_id?: string | null
  review_comment?: string
  error_message?: string
  reviewed_by?: string
  created_at: string
  updated_at: string
}
export type ContributionCreate = Partial<ContributionCandidate> & { operation?: 'CREATE' | 'UPDATE' | 'DELETE'; content_kind?: 'KNOWLEDGE' | 'SKILL'; target_document_id?: string; source_curation_id?: string }
const root = '/knowledge-contributions'
export const contributionsApi = {
  list: async (mine = false, status = '') => (await api.get<KnowledgeContribution[]>(root, { params: { mine, limit: 500, ...(status ? { status } : {}) } })).data,
  get: async (id: string) => (await api.get<KnowledgeContribution>(`${root}/${id}`)).data,
  create: async (input: ContributionCreate) => (await api.post<KnowledgeContribution>(root, input)).data,
  update: async (id: string, input: Record<string, unknown>, review: boolean) => (await api.patch<KnowledgeContribution>(`${root}/${id}${review ? '/review-draft' : ''}`, input)).data,
  submit: async (item: KnowledgeContribution) => (await api.post<KnowledgeContribution>(`${root}/${item.id}/submit`, { expected_version: item.version })).data,
  remove: async (item: KnowledgeContribution) => { await api.delete(`${root}/${item.id}`, { params: { expected_version: item.version } }) },
  chat: async (item: KnowledgeContribution, instruction: string, consent: boolean) => (await api.post<KnowledgeContribution>(`${root}/${item.id}/review-chat`, { expected_version: item.version, instruction, consent_model_egress: consent }, { timeout: 5 * 60 * 1000 })).data,
  review: async (item: KnowledgeContribution, action: 'APPROVE' | 'RETURN' | 'REJECT', comment: string) => (await api.post<KnowledgeContribution>(`${root}/${item.id}/review`, { expected_version: item.version, expected_content_hash: item.content_hash, action, comment })).data,
  retry: async (item: KnowledgeContribution) => { if (!item.publication_job_id) throw new Error('缺少发布任务，请刷新状态'); await api.post(`/jobs/${item.publication_job_id}/retry`); return (await api.get<KnowledgeContribution>(`${root}/${item.id}`)).data }
}
export const contributionStatus = (state: string) => ({ DRAFT: '我的草稿', SUBMITTED: '待审核', RETURNED: '退回修改', REJECTED: '已驳回', APPROVED: '已审批，待发布', PUBLISHING: '已审批，发布中', PUBLISHED: '已生效', FAILED: '已审批，发布失败' } as Record<string, string>)[state] || state
