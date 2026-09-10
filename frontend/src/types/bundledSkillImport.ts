import type { Job } from './index'

export type BundledSkillDisposition = 'ADD' | 'EXISTS' | 'CONFLICT'

export interface BundledSkillManifestEntry {
  path: string
  role: string
  bytes: number
  source_sha256: string
  adaptation_diff: string
  disposition: BundledSkillDisposition
  conflicts?: string[]
}

export interface BundledSkillPreview {
  operation_id: string
  source_sha256: string
  preview_hash: string
  can_confirm: boolean
  manifest: BundledSkillManifestEntry[]
  counts: Record<string, number>
  message?: string
  preserved: string[]
}

export interface BundledSkillOperation {
  operation_id: string
  status: string
  message?: string
  [key: string]: unknown
}

export interface BundledSkillOperationState {
  operation: BundledSkillOperation
  job: Job | null
}
