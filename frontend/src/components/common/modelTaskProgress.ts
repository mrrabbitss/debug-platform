import type { Job, JobProgressDetail } from '../../types'

export type ModelTaskPresentationPhase = 'paused' | 'awaiting_human' | 'published' | 'failed' | 'cancelled'

export interface ModelTaskProgressView {
  percentage: number
  stage: string
  detail: string
  remaining: string
  state: 'success' | 'warning' | 'exception' | 'active'
  waitingForModel: boolean
  waitingSince?: string
  heartbeatAt?: string
  lastUpdated?: string
  terminal: boolean
}

const terminal = new Set(['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'])

function number(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function text(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const valueTrimmed = value.trim()
  if (!valueTrimmed) return undefined
  // Backend stages are Chinese but may legitimately contain Skill, API or LLM.
  // Only suppress a raw English-only legacy message that cannot be presented to users.
  if (!/[\u3400-\u9fff]/.test(valueTrimmed) && !/\b(?:skill|api|llm)\b/i.test(valueTrimmed)) return undefined
  return valueTrimmed
}

function percentage(job: Partial<Job>, detail?: Partial<JobProgressDetail>, cap = 99): number {
  const candidate = number(detail?.completed_percent) ?? number(job.progress) ?? 0
  return Math.max(0, Math.min(cap, Math.round(candidate)))
}

function countDetail(detail?: Partial<JobProgressDetail>): string | undefined {
  const completed = number(detail?.completed_units)
  const total = number(detail?.total_units)
  if (completed === undefined) return undefined
  const unit = text(detail?.unit)
  if (total === undefined || total < 0) return `已处理 ${completed}${unit ? ` ${unit}` : ''}`
  return `${completed} / ${total}${unit ? ` ${unit}` : ''}`
}

function stageDetail(detail?: Partial<JobProgressDetail>): string | undefined {
  const index = number(detail?.stage_index)
  const count = number(detail?.stage_count)
  const round = number(detail?.round_number)
  const parts: string[] = []
  if (index !== undefined && count !== undefined && count > 0) parts.push(`第 ${index} / ${count} 步`)
  if (round !== undefined && round > 0) parts.push(`第 ${round} 轮`)
  const counted = countDetail(detail)
  if (counted) parts.push(counted)
  return parts.join(' · ') || undefined
}

function pausedResult(job: Partial<Job>): boolean {
  if (job.status === 'PAUSED') return true
  try { return JSON.parse(job.result_json || '{}')?.paused === true } catch { return false }
}

function currentStage(job: Partial<Job>, detail?: Partial<JobProgressDetail>, fallback = '正在处理'): string {
  return text(detail?.stage) || text(job.message) || fallback
}

function remaining(percent: number): string {
  return `约剩余 ${Math.max(0, 100 - percent)}%（按步骤估算，非时间预测）`
}

function mergeDetails(...values: Array<string | undefined>): string | undefined {
  const unique = [...new Set(values.map(value => value?.trim()).filter((value): value is string => Boolean(value)))]
  return unique.join(' · ') || undefined
}

function stageWithState(stage: string, state: '未完成' | '已取消' | '已暂停'): string {
  return stage.includes(state) ? stage : `${stage} · ${state}`
}

export function modelTaskProgress(
  job?: Partial<Job> | null,
  phase?: ModelTaskPresentationPhase
): ModelTaskProgressView | null {
  if (!job) return null
  const detail = job.progress_detail
  const isFailed = phase === 'failed' || ['FAILED', 'DEAD_LETTER'].includes(job.status || '')
  const isCancelled = phase === 'cancelled' || job.status === 'CANCELLED'
  const isPaused = phase === 'paused' || pausedResult(job)
  const isPublished = phase === 'published'
  const awaitingHuman = phase === 'awaiting_human'
  const baseDetail = stageDetail(detail)
  const activityDetail = mergeDetails(baseDetail, text(job.message))
  const heartbeatAt = job.heartbeat_at
  const lastUpdated = detail?.updated_at || job.started_at

  if (isPublished) return {
    percentage: 100, stage: '已发布并生效', detail: '已完成全部处理', remaining: '无需继续处理',
    state: 'success', waitingForModel: false, terminal: true
  }
  if (isPaused) return {
    percentage: percentage(job, detail), stage: stageWithState(currentStage(job, detail, '任务'), '已暂停'), detail: activityDetail || '进度已保存，可继续处理',
    remaining: '暂停中，等待继续', state: 'warning', waitingForModel: false,
    heartbeatAt, lastUpdated, terminal: false
  }
  if (awaitingHuman) return {
    percentage: 100, stage: '本轮 AI 整理已完成', detail: '待人工核对，尚未发布', remaining: '等待人工确认',
    state: 'warning', waitingForModel: false, heartbeatAt, lastUpdated: job.completed_at || lastUpdated, terminal: false
  }
  if (isFailed || isCancelled) return {
    percentage: percentage(job, detail), stage: stageWithState(currentStage(job, detail, '任务'), isCancelled ? '已取消' : '未完成'),
    detail: activityDetail || (isCancelled ? '已安全停止，未将未完成步骤计为完成' : '可查看原因后重试'),
    remaining: isCancelled ? '已停止' : '等待重试', state: isFailed ? 'exception' : 'warning',
    waitingForModel: false, heartbeatAt, lastUpdated: job.completed_at || lastUpdated, terminal: true
  }
  if (job.status === 'COMPLETED') return {
    percentage: 100, stage: currentStage(job, detail, '任务已完成'), detail: activityDetail || '结果已持久保存',
    remaining: '无需继续处理', state: 'success', waitingForModel: false,
    heartbeatAt, lastUpdated: job.completed_at || lastUpdated, terminal: true
  }
  if (job.status === 'QUEUED') return {
    percentage: 0, stage: currentStage(job, detail, '已排队等待处理'), detail: activityDetail || `尚未开始模型调用${(job.attempt || 1) > 1 ? ` · 第 ${(job.attempt || 1) - 1} 次重试` : ''}`,
    remaining: '等待任务开始', state: 'active', waitingForModel: false,
    heartbeatAt, lastUpdated: job.created_at || lastUpdated, terminal: false
  }
  if (job.status === 'CANCEL_REQUESTED') {
    const currentPercent = percentage(job, detail)
    return {
      percentage: currentPercent, stage: `${currentStage(job, detail)} · 正在取消`, detail: activityDetail || '正在等待本次请求安全结束',
      remaining: '取消请求已保存，等待当前步骤结束', state: 'warning', waitingForModel: false,
      heartbeatAt, lastUpdated, terminal: false
    }
  }
  const waitingForModel = Boolean(detail?.waiting_for_model)
  const currentPercent = percentage(job, detail)
  const stage = waitingForModel ? `${currentStage(job, detail)} · 等待模型响应` : currentStage(job, detail)
  return {
    percentage: currentPercent, stage,
    detail: waitingForModel ? mergeDetails(activityDetail, '模型响应返回后将继续下一步')! : activityDetail || '正在执行已确认步骤',
    remaining: remaining(currentPercent), state: 'active', waitingForModel,
    waitingSince: detail?.model_started_at || detail?.stage_started_at,
    heartbeatAt, lastUpdated, terminal: terminal.has(job.status || '')
  }
}

export function jobTimestampMilliseconds(value: string): number {
  // SQLite reloads UTC database timestamps without an offset. Structured
  // progress timestamps already carry +00:00; preserve explicit offsets.
  return Date.parse(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`)
}

export function elapsedLabel(startedAt?: string, now = Date.now()): string {
  if (!startedAt) return ''
  const started = jobTimestampMilliseconds(startedAt)
  if (!Number.isFinite(started)) return ''
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return minutes < 60 ? `${minutes} 分 ${remainder} 秒` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`
}
