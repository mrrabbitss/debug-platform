import type { Job } from './index'

export interface KnowledgeEntry {
  id: string
  title: string
  version: number
  lock_version?: number
  status: string
  content: string
  categories: string[]
  role: string
  bundle_id?: string
  source_paths?: string[]
  legacy?: boolean
  content_kind?: 'KNOWLEDGE' | 'SKILL'
  owner_id?: string | null
  metadata?: Record<string, any>
}

export interface LibraryEntry {
  id: string
  title: string
  version: number
  status: 'PENDING' | 'CONFIRMED' | 'REJECTED'
  content: string
  problem_category: string
  owner_id: string
  case_id?: string
  analysis_id?: string
  report_markdown?: string
  reviewed_conclusion?: string
  contribution_id?: string
}

export interface AssistantOperation {
  operation_id: string
  content_kind?: 'SKILL' | 'KNOWLEDGE'
  action: 'create' | 'merge' | 'replace' | 'link' | 'skip'
  title: string
  categories: string[]
  role: string
  source_paths: string[]
  sources?: { path: string; start: number; end: number; sha256: string }[]
  reason: string
  target_id?: string
  expected_version?: number
  expected_lock?: number
  expected_sha256?: string
  before: string
  after: string
  diff: string
}

export interface AssistantSummary {
  id: string
  title: string
  version: number
  status: string
  created_at: string
}

export interface AssistantSession extends AssistantSummary {
  messages: { role: string; content: string }[]
  files: { path: string; sha256?: string }[]
  plan: AssistantOperation[]
  coverage: Record<string, { read: number; total: number; complete: boolean; sha256?: string }>
  answer?: string
  error?: string
  job?: Job
  job_id?: string
  mode?: string
  review_digest?: string
  model_egress_approved: boolean
  bundle_manifest?: { path: string; document_id: string; references: { reference: string; path?: string; external?: boolean }[] }[]
}
