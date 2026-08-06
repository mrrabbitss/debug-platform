import type { AxiosProgressEvent } from 'axios'

import type {
  KnowledgeCategory,
  KnowledgeCurationRevision,
  KnowledgeCurationSession,
  ModelProfile
} from '../types'
import { api } from './client'

export interface KnowledgeCurationCreateInput {
  files: File[]
  relativePaths: string[]
  title_hint: string
  category_id: string
  device_type: string
  device_model: string
  firmware_range: string
  module: string
  trust_level: string
  confidentiality: string
  model_profile_id: string
  consent_model_egress: boolean
}

export interface KnowledgeCurationJobResponse {
  session: KnowledgeCurationSession
  job: { id: string; status: string }
}

export interface KnowledgeCurationPreview {
  text: string
  has_more: boolean
  start_line: number
  line_count: number
}

export async function loadCurationOptions(): Promise<{
  models: ModelProfile[]
  categories: KnowledgeCategory[]
}> {
  const [modelResponse, categoryResponse] = await Promise.all([
    api.get<ModelProfile[]>('/system/models', { params: { task_type: 'chat' } }),
    api.get<KnowledgeCategory[]>('/knowledge/categories')
  ])
  return { models: modelResponse.data, categories: categoryResponse.data }
}

export async function listKnowledgeCurations(): Promise<KnowledgeCurationSession[]> {
  return (await api.get<KnowledgeCurationSession[]>('/knowledge-curations')).data
}

export async function getKnowledgeCuration(sessionId: string): Promise<KnowledgeCurationSession> {
  return (await api.get<KnowledgeCurationSession>(`/knowledge-curations/${sessionId}`)).data
}

export async function createKnowledgeCuration(
  input: KnowledgeCurationCreateInput,
  onUploadProgress?: (event: AxiosProgressEvent) => void
): Promise<KnowledgeCurationJobResponse> {
  const data = new FormData()
  input.files.forEach(file => data.append('files', file, file.name))
  data.append('relative_paths_json', JSON.stringify(input.relativePaths))
  Object.entries(input).forEach(([key, value]) => {
    if (key === 'files' || key === 'relativePaths') return
    if (typeof value === 'boolean') data.append(key, String(value))
    else if (value) data.append(key, value)
  })
  return (await api.post<KnowledgeCurationJobResponse>('/knowledge-curations', data, {
    timeout: 30 * 60 * 1000,
    onUploadProgress
  })).data
}

export async function saveKnowledgeCurationDraft(
  sessionId: string,
  payload: {
    title: string
    markdown: string
    change_summary: string
    expected_draft_version: number
  }
): Promise<KnowledgeCurationSession> {
  return (await api.patch<KnowledgeCurationSession>(
    `/knowledge-curations/${sessionId}/draft`,
    payload
  )).data
}

export async function refineKnowledgeCuration(
  sessionId: string,
  instruction: string,
  expectedDraftVersion: number
): Promise<KnowledgeCurationSession> {
  return (await api.post<KnowledgeCurationSession>(
    `/knowledge-curations/${sessionId}/chat`,
    { instruction, expected_draft_version: expectedDraftVersion },
    { timeout: 5 * 60 * 1000 }
  )).data
}

export async function restoreKnowledgeCurationRevision(
  sessionId: string,
  revision: KnowledgeCurationRevision,
  expectedDraftVersion: number
): Promise<KnowledgeCurationSession> {
  return (await api.post<KnowledgeCurationSession>(
    `/knowledge-curations/${sessionId}/revisions/${revision.version}/restore`,
    { expected_draft_version: expectedDraftVersion }
  )).data
}

export async function confirmKnowledgeCuration(
  sessionId: string,
  expectedDraftVersion: number
): Promise<{ session: KnowledgeCurationSession }> {
  return (await api.post<{ session: KnowledgeCurationSession }>(
    `/knowledge-curations/${sessionId}/confirm`,
    { expected_draft_version: expectedDraftVersion }
  )).data
}

export async function retryKnowledgeCuration(
  sessionId: string
): Promise<KnowledgeCurationJobResponse> {
  return (await api.post<KnowledgeCurationJobResponse>(
    `/knowledge-curations/${sessionId}/retry`,
    { consent_model_egress: true }
  )).data
}

export async function deleteKnowledgeCuration(sessionId: string): Promise<void> {
  await api.delete(`/knowledge-curations/${sessionId}`)
}

export async function previewKnowledgeCurationSource(
  sessionId: string,
  sourceId: string,
  startLine: number
): Promise<KnowledgeCurationPreview> {
  return (await api.get<KnowledgeCurationPreview>(
    `/knowledge-curations/${sessionId}/sources/${sourceId}/preview`,
    { params: { start_line: startLine, line_count: 500 } }
  )).data
}
