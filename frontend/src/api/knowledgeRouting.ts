import type {
  Job,
  KnowledgeRoutingImportResponse,
  KnowledgeRoutingJobResult,
  ModelProfile
} from '../types'
import { api, NORMAL_REQUEST_TIMEOUT_MS, SYNCHRONOUS_AI_TIMEOUT_MS } from './client'

export interface ImportMarkdownKnowledgeInput {
  files: File[]
  relativePaths: string[]
  modelProfileId: string
  consentModelEgress: boolean
  trustLevel: 'LOW' | 'MEDIUM' | 'HIGH'
  confidentiality: 'PUBLIC' | 'INTERNAL' | 'RESTRICTED'
}

export async function loadKnowledgeRoutingModels(): Promise<ModelProfile[]> {
  return (await api.get<ModelProfile[]>('/system/models', {
    params: { task_type: 'chat' }
  })).data
}

export async function importMarkdownKnowledge(
  input: ImportMarkdownKnowledgeInput
): Promise<KnowledgeRoutingImportResponse> {
  const data = new FormData()
  input.files.forEach(file => data.append('files', file, file.name))
  data.append('relative_paths_json', JSON.stringify(input.relativePaths))
  data.append('reasoning_owner', 'platform_llm')
  data.append('model_profile_id', input.modelProfileId)
  data.append('consent_model_egress', String(input.consentModelEgress))
  data.append('trust_level', input.trustLevel)
  data.append('confidentiality', input.confidentiality)
  return (await api.post<KnowledgeRoutingImportResponse>(
    '/knowledge-routing/import',
    data,
    { timeout: NORMAL_REQUEST_TIMEOUT_MS }
  )).data
}

export async function waitForKnowledgeRoutingJob(
  initialJob: Job,
  onUpdate?: (job: Job) => void,
  signal?: AbortSignal,
  onPollError?: (error: unknown) => void
): Promise<{ job: Job; result: KnowledgeRoutingJobResult }> {
  const deadline = Date.now() + SYNCHRONOUS_AI_TIMEOUT_MS
  let job = initialJob
  onUpdate?.(job)
  while (!['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(job.status)) {
    if (signal?.aborted) throw new Error('已停止查看知识分类任务')
    if (Date.now() >= deadline) throw new Error('知识分类任务等待超时，请稍后刷新知识列表')
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, 750)
      signal?.addEventListener('abort', () => { window.clearTimeout(timer); reject(new Error('已停止查看知识分类任务')) }, { once: true })
    })
    try {
      job = (await api.get<Job>(`/jobs/${job.id}`, { signal })).data
    } catch (error) {
      if (signal?.aborted) throw error
      onPollError?.(error)
      continue
    }
    onUpdate?.(job)
  }
  if (job.status !== 'COMPLETED') {
    throw new Error(
      job.error_message ||
      job.dead_letter_reason ||
      `知识分类任务${job.status === 'CANCELLED' ? '已取消' : '失败'}`
    )
  }
  let result: KnowledgeRoutingJobResult
  try {
    result = JSON.parse(job.result_json || '{}') as KnowledgeRoutingJobResult
  } catch {
    throw new Error('知识分类任务返回了无法解析的结果')
  }
  if (!result.document_id || result.review_status !== 'DRAFT') {
    throw new Error('知识分类任务没有生成受治理的草稿')
  }
  return { job, result }
}
