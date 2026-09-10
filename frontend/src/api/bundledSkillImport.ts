import { api } from './client'
import type { Job } from '../types'
import type { BundledSkillOperationState, BundledSkillPreview } from '../types/bundledSkillImport'

const root = '/workbench/bundled-skill'

export function previewBundledSkill(operationId: string): Promise<BundledSkillPreview> {
  return api.post<BundledSkillPreview>(`${root}/preview`, { operation_id: operationId }).then(response => response.data)
}

export function confirmBundledSkill(preview: BundledSkillPreview, modelEgressApproved: boolean): Promise<BundledSkillOperationState> {
  return api.post<BundledSkillOperationState>(`${root}/confirm`, {
    operation_id: preview.operation_id,
    expected_source_sha256: preview.source_sha256,
    expected_preview_hash: preview.preview_hash,
    confirmed: true,
    model_egress_approved: modelEgressApproved
  }).then(response => response.data)
}

export function getBundledSkillOperation(operationId: string): Promise<BundledSkillOperationState> {
  return api.get<BundledSkillOperationState>(`${root}/${encodeURIComponent(operationId)}`).then(response => response.data)
}

export function retryBundledSkillJob(jobId: string): Promise<Job> {
  return api.post<Job>(`/jobs/${encodeURIComponent(jobId)}/retry`).then(response => response.data)
}
