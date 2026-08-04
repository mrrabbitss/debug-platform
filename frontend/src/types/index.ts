export interface CaseItem {
  id: string
  title: string
  device_type: string
  device_model?: string
  firmware_version?: string
  topology?: string
  description: string
  reproduction_steps?: string
  issue_time?: string
  status: string
  severity: string
  owner_id?: string
  created_at: string
  updated_at: string
}

export type UserRole = 'ADMIN' | 'ENGINEER' | 'VIEWER'
export type CasePermission = 'OWNER' | 'EDITOR' | 'VIEWER' | 'SHARED'

export interface Principal {
  id: string
  username?: string
  display_name?: string
  role: UserRole
  type: 'local' | 'api_key' | 'user_token' | 'anonymous'
  token_id?: string
}

export interface UserAccount {
  id: string
  username: string
  display_name: string
  role: UserRole
  active: boolean
  created_at: string
  updated_at: string
}

export interface UserDirectoryEntry {
  id: string
  username: string
  display_name: string
  role: UserRole
}

export interface AccessTokenInfo {
  id: string
  user_id: string
  name: string
  token_hint: string
  expires_at?: string
  last_used_at?: string
  revoked_at?: string
  created_at: string
}

export interface CaseMember {
  id: string
  case_id: string
  user_id: string
  username: string
  display_name: string
  permission: 'EDITOR' | 'VIEWER'
}

export interface AuditEvent {
  id: string
  actor_id?: string
  actor_type: string
  action: string
  resource_type?: string
  resource_id?: string
  case_id?: string
  outcome: string
  ip_address?: string
  details: Record<string, unknown>
  created_at: string
}

export interface Artifact {
  id: string
  case_id: string
  kind: string
  original_name: string
  sha256: string
  size_bytes: number
  status: string
  metadata_json: string
  created_at: string
}

export interface Job {
  id: string
  kind: string
  status: string
  progress: number
  message: string
  result_json: string
  error_message?: string
}

export interface LogEvent {
  id: string
  artifact_id: string
  source_file: string
  line_start: number
  line_end: number
  timestamp_raw?: string
  timestamp_normalized?: string
  level: string
  module: string
  component: string
  event_code: string
  message: string
  raw_text: string
  entities: Record<string, string>
  confidence: number
}

export interface Analysis {
  id: string
  case_id: string
  status: string
  provider: string
  model: string
  model_profile_id?: string
  model_config_json: string
  prompt_version: string
  result_json: string
  evidence_json: string
  error_message?: string
  created_at: string
}

export type ModelTask = 'chat' | 'embedding' | 'reranker'
export type ModelMode = 'builtin' | 'local' | 'api'

export interface ModelProfile {
  id: string
  name: string
  task_type: ModelTask
  mode: ModelMode
  provider: string
  model_name: string
  base_url?: string
  api_key_configured: boolean
  api_key_hint?: string
  config: Record<string, any>
  enabled: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface KnowledgeCategory {
  id: string
  name: string
  code: string
  parent_id?: string
  description: string
  sort_order: number
  system: boolean
  active: boolean
  document_count: number
  created_at: string
  updated_at: string
  children?: KnowledgeCategory[]
}

export interface KnowledgeDocument {
  id: string
  title: string
  source_type: string
  device_type?: string
  device_model?: string
  firmware_range?: string
  module?: string
  trust_level: string
  confidentiality: string
  active: boolean
  review_status: 'DRAFT' | 'IN_REVIEW' | 'ACTIVE' | 'REJECTED' | 'ARCHIVED'
  version: number
  lock_version: number
  reviewed_by?: string
  reviewed_at?: string
  review_comment?: string
  published_at?: string
  category_id?: string
  category_name?: string
  chunk_count: number
  metadata: Record<string, any>
  content?: string
  created_at: string
  updated_at: string
}

export interface KnowledgeRevision {
  id: string
  document_id: string
  version: number
  content_hash: string
  change_summary: string
  created_by?: string
  created_at: string
  snapshot: Record<string, any>
}

export interface KnowledgeCurationSource {
  id: string
  source_ref: string
  relative_path: string
  extraction_method?: 'plain_text' | 'html_visible_text' | 'docx_paragraphs_tables' | 'pdf_text_layer'
  extraction_truncated: boolean
  page_count?: number
  sha256: string
  size_bytes: number
  media_type?: string
  text_encoding?: string
  line_count?: number
  source_role: 'log' | 'error' | 'analysis' | 'solution' | 'context'
  included: boolean
  skip_reason?: string
  created_at: string
}

export interface KnowledgeCurationMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  citations: string[]
  draft_version?: number
  model_profile_id?: string
  created_by?: string
  created_at: string
}

export interface KnowledgeCurationRevision {
  id: string
  version: number
  content_hash: string
  change_summary: string
  validation: Record<string, any>
  source_message_id?: string
  created_by?: string
  created_at: string
}

export interface KnowledgeCurationSession {
  id: string
  status: 'QUEUED' | 'EXTRACTING' | 'REVIEWING' | 'FAILED' | 'CANCELLED' | 'CONFIRMING' | 'CONFIRMED'
  title_hint: string
  category_id?: string
  device_type?: string
  device_model?: string
  firmware_range?: string
  module?: string
  trust_level: string
  confidentiality: string
  model_profile_id?: string
  model_snapshot: Record<string, any>
  source_manifest: Record<string, any>
  source_count: number
  sources?: KnowledgeCurationSource[]
  draft_title: string
  draft_markdown?: string
  draft_version: number
  validation: Record<string, any>
  open_questions: string[]
  messages?: KnowledgeCurationMessage[]
  revisions?: KnowledgeCurationRevision[]
  knowledge_document_id?: string
  job_id?: string
  error_message?: string
  created_by?: string
  created_at: string
  updated_at: string
  confirmed_at?: string
}

export interface DomainGraphStatus {
  status: 'NOT_BUILT' | 'BUILDING' | 'READY' | 'STALE' | 'FAILED'
  active_generation_id?: string
  building_generation_id?: string
  entities: number
  relations: number
  metadata: Record<string, any>
  error?: string
}

export interface EvaluationDataset {
  id: string
  name: string
  description: string
  active: boolean
  created_by?: string
  case_count: number
  created_at: string
  updated_at: string
}

export interface EvaluationCase {
  id: string
  dataset_id: string
  case_id: string
  query: string
  expected_evidence_ids: string[]
  expected_root_causes: string[]
  modules: Array<'knowledge' | 'domain_graph' | 'code' | 'commit' | 'memory'>
  top_k: number
  max_hops: number
  metadata: Record<string, any>
  created_at: string
  updated_at: string
}

export interface EvaluationRun {
  id: string
  dataset_id: string
  job_id?: string
  status: string
  config: Record<string, any>
  metrics: Record<string, number | null>
  results: Array<Record<string, any>>
  error_message?: string
  created_at: string
  started_at?: string
  completed_at?: string
}
