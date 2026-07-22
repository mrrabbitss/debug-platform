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
  created_at: string
  updated_at: string
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
  result_json: string
  evidence_json: string
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
  category_id?: string
  category_name?: string
  chunk_count: number
  metadata: Record<string, any>
  content?: string
  created_at: string
  updated_at: string
}
