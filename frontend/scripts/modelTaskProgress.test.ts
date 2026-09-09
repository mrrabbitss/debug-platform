import assert from 'node:assert/strict'
import type { Job } from '../src/types/index.ts'
import { modelTaskProgress } from '../src/components/common/modelTaskProgress.ts'
import { clearTaskJobBookmark, readTaskJobBookmark, saveTaskJobBookmark, shouldClearTaskJobBookmark, taskJobBookmarkKey } from '../src/composables/taskJobBookmark.ts'

const storage = new Map<string, string>()
const memoryStorage = { getItem: (key: string) => storage.get(key) || null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) }
const bookmarkKey = taskJobBookmarkKey('user-a', 'case:case-a:diagnosis')!
saveTaskJobBookmark('user-a', 'case:case-a:diagnosis', 'job-a', memoryStorage)
assert.equal(readTaskJobBookmark('user-a', 'case:case-a:diagnosis', memoryStorage), 'job-a')
assert.equal(readTaskJobBookmark('user-b', 'case:case-a:diagnosis', memoryStorage), undefined)
assert.equal(storage.has(bookmarkKey), true)
clearTaskJobBookmark('user-a', 'case:case-a:diagnosis', memoryStorage)
assert.equal(storage.has(bookmarkKey), false)
assert.equal(shouldClearTaskJobBookmark({ response: { status: 404 } }), true)
assert.equal(shouldClearTaskJobBookmark({ response: { status: 500 } }), false)

const timestamps = {
  created_at: '2026-09-10T00:00:00Z',
  started_at: '2026-09-10T00:01:00Z',
  heartbeat_at: '2026-09-10T00:02:00Z'
}

function job(partial: Partial<Job>): Job {
  return {
    id: 'job-1', kind: 'chat', status: 'RUNNING', progress: 0, message: '', result_json: '{}',
    attempt: 1, max_attempts: 3, available_at: timestamps.created_at, timeout_seconds: 900,
    resource_limits_json: '{}', ...timestamps, ...partial
  }
}

const waiting = modelTaskProgress(job({
  progress: 35,
  message: '第 2 份资料第 4 段读取中',
  progress_detail: {
    stage: '完整阅读 Skill', stage_index: 2, stage_count: 5, completed_percent: 35,
    completed_units: 4, total_units: 12, unit: '段', waiting_for_model: true,
    stage_started_at: timestamps.started_at, model_started_at: timestamps.started_at, updated_at: timestamps.heartbeat_at
  }
}))!
assert.equal(waiting.stage, '完整阅读 Skill · 等待模型响应')
assert.match(waiting.detail, /第 2 份资料第 4 段读取中/)
assert.match(waiting.remaining, /约剩余 65%（按步骤估算，非时间预测）/)
assert.equal(waiting.heartbeatAt, timestamps.heartbeat_at)

const cancelling = modelTaskProgress(job({ status: 'CANCEL_REQUESTED', progress: 42, progress_detail: {
  stage: '生成诊断报告', completed_percent: 42, waiting_for_model: true, stage_started_at: timestamps.started_at, updated_at: timestamps.heartbeat_at
} }))!
assert.match(cancelling.stage, /生成诊断报告 · 正在取消/)
assert.match(cancelling.remaining, /等待当前步骤结束/)

const paused = modelTaskProgress(job({ status: 'COMPLETED', progress: 54, result_json: '{"paused":true}', progress_detail: {
  stage: '完整阅读 Skill', completed_percent: 54, waiting_for_model: false, stage_started_at: timestamps.started_at, updated_at: timestamps.heartbeat_at
} }))!
assert.equal(paused.percentage, 54)
assert.equal(paused.stage, '完整阅读 Skill · 已暂停')
assert.match(paused.remaining, /暂停中/)

const failed = modelTaskProgress(job({ status: 'FAILED', progress: 48, progress_detail: {
  stage: '提取诊断证据', completed_percent: 48, completed_units: 8, unit: '行', waiting_for_model: false, stage_started_at: timestamps.started_at, updated_at: timestamps.heartbeat_at
} }))!
assert.equal(failed.stage, '提取诊断证据 · 未完成')
assert.match(failed.detail, /已处理 8 行/)
assert.equal(failed.percentage, 48)

const retry = modelTaskProgress(job({ status: 'QUEUED', attempt: 3 }))!
assert.match(retry.detail, /第 2 次重试/)

console.log('modelTaskProgress presentation tests passed')
