import { computed, onBeforeUnmount, ref } from 'vue'
import type { Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { getKnowledgeCuration } from '../api/knowledgeCuration'
import type { Job, KnowledgeCurationSession } from '../types'
import {
  clearTaskJobBookmark,
  readTaskJobBookmark,
  saveTaskJobBookmark,
  shouldClearTaskJobBookmark
} from './taskJobBookmark'

interface KnowledgeCurationTaskOptions {
  current: Ref<KnowledgeCurationSession | null>
  principalId: Ref<string>
  loading: Ref<boolean>
  draftEditor: Ref<string>
  draftTitle: Ref<string>
  loadSessions: (preferredId?: string, isCurrent?: () => boolean) => Promise<void>
  errorText: (error: unknown) => string
}

export function useKnowledgeCurationTask({
  current, principalId, loading, draftEditor, draftTitle, loadSessions, errorText
}: KnowledgeCurationTaskOptions) {
  const activeJob = ref<Job | null>(null)
  const jobPollError = ref('')
  let activeJobSessionId: string | undefined
  let pollTimer: number | undefined
  let disposed = false, jobEpoch = 0, sessionEpoch = 0

  const isWorking = computed(() => ['QUEUED', 'EXTRACTING', 'CONFIRMING'].includes(current.value?.status || '')
    || ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value?.status || ''))
  const progressPhase = computed(() => {
    if (current.value?.status === 'REVIEWING' && activeJob.value?.status === 'COMPLETED') return 'awaiting_human' as const
    if (current.value?.status === 'FAILED') return 'failed' as const
    if (current.value?.status === 'CANCELLED') return 'cancelled' as const
    return undefined
  })

  function clearPoll() {
    if (pollTimer !== undefined) window.clearTimeout(pollTimer)
    pollTimer = undefined
  }

  function schedulePoll(token = jobEpoch, sessionId = current.value?.id) {
    if (disposed || token !== jobEpoch || current.value?.id !== sessionId) return
    clearPoll()
    if (!sessionId || !current.value || (!['QUEUED', 'EXTRACTING', 'CONFIRMING'].includes(current.value.status)
      && !['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value?.status || ''))) return
    pollTimer = window.setTimeout(async () => {
      const isCurrent = () => !disposed && token === jobEpoch && current.value?.id === sessionId
      if (!isCurrent()) return
      try {
        await loadSession(sessionId)
        if (!isCurrent()) return
        await loadSessions(sessionId, isCurrent)
      } finally {
        schedulePoll(token, sessionId)
      }
    }, 1500)
  }

  function curationJobScope(sessionId = current.value?.id) { return `knowledge-curation:${sessionId || 'unknown'}:model` }
  function trackCurationJob(job: Job, sessionId = current.value?.id) {
    if (!sessionId) return
    const token = ++jobEpoch
    activeJobSessionId = sessionId
    activeJob.value = job
    saveTaskJobBookmark(principalId.value, curationJobScope(sessionId), job.id)
    schedulePoll(token, sessionId)
  }

  async function refreshActiveJob(session: KnowledgeCurationSession, token = sessionEpoch) {
    const scope = curationJobScope(session.id)
    const jobId = (activeJobSessionId === session.id ? activeJob.value?.id : undefined)
      || readTaskJobBookmark(principalId.value, scope)
      || session.job_id
    const requestJobEpoch = jobEpoch
    if (!jobId) return
    const isCurrent = () => !disposed && token === sessionEpoch
      && requestJobEpoch === jobEpoch && current.value?.id === session.id
    try {
      const { data } = await api.get<Job>(`/jobs/${jobId}`)
      if (!isCurrent()) return
      if (data.status === 'COMPLETED') {
        let completedDraftVersion = 0
        try {
          completedDraftVersion = Number(JSON.parse(data.result_json || '{}')?.draft_version)
        } catch { /* Legacy jobs may not contain a draft version. */ }
        if (Number.isFinite(completedDraftVersion) && completedDraftVersion > session.draft_version) {
          // The job may have committed a newer draft after the first session read.
          const latest = await getKnowledgeCuration(session.id)
          if (!isCurrent()) return
          current.value = latest
          draftEditor.value = latest.draft_markdown || ''
          draftTitle.value = latest.draft_title || latest.title_hint
        }
      }
      // Keep the previous task state until its completed draft has been read.
      activeJobSessionId = session.id
      activeJob.value = data
      jobPollError.value = ''
    } catch (error) {
      if (!isCurrent()) return
      // Do not clear an earlier confirmed job when a poll is interrupted.
      jobPollError.value = errorText(error) || '任务状态暂时无法刷新，页面保留最近一次确认的进度。'
      if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(principalId.value, curationJobScope(session.id))
    }
  }

  async function loadSession(sessionId: string) {
    if (current.value?.id !== sessionId) resetModelTask()
    const token = ++sessionEpoch
    loading.value = true
    try {
      const detail = await getKnowledgeCuration(sessionId)
      if (disposed || token !== sessionEpoch) return
      current.value = detail
      draftEditor.value = detail.draft_markdown || ''
      draftTitle.value = detail.draft_title || detail.title_hint
      await refreshActiveJob(detail, token)
      if (disposed || token !== sessionEpoch) return
      void restoreCurationJob(detail.id)
    } catch (error) {
      if (!disposed && token === sessionEpoch) ElMessage.error(errorText(error))
    } finally {
      if (!disposed && token === sessionEpoch) loading.value = false
    }
    if (!disposed && token === sessionEpoch) schedulePoll()
  }

  async function cancelModelTask() {
    if (!activeJob.value) return
    try { const { data } = await api.post<Job>(`/jobs/${activeJob.value.id}/cancel`); activeJob.value = data; schedulePoll() }
    catch (error) { jobPollError.value = errorText(error) }
  }

  async function retryModelTask() {
    if (!activeJob.value) return
    try { const { data } = await api.post<Job>(`/jobs/${activeJob.value.id}/retry`); jobPollError.value = ''; trackCurationJob(data) }
    catch (error) { jobPollError.value = errorText(error) }
  }

  function dismissModelTask() {
    const sessionId = current.value?.id
    resetModelTask()
    clearTaskJobBookmark(principalId.value, curationJobScope(sessionId))
  }

  async function restoreCurationJob(sessionId: string) {
    const token = jobEpoch, jobId = readTaskJobBookmark(principalId.value, curationJobScope(sessionId))
    if (!jobId) return
    if (activeJobSessionId === sessionId && activeJob.value?.id === jobId) return
    try {
      const { data } = await api.get<Job>(`/jobs/${jobId}`)
      if (disposed || token !== jobEpoch || current.value?.id !== sessionId) return
      activeJobSessionId = sessionId
      activeJob.value = data
      if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) trackCurationJob(data, sessionId)
    } catch (error) {
      if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(principalId.value, curationJobScope(sessionId))
    }
  }

  function resetModelTask() {
    clearPoll()
    ++jobEpoch
    ++sessionEpoch
    activeJobSessionId = undefined
    activeJob.value = null
    jobPollError.value = ''
    loading.value = false
  }

  onBeforeUnmount(() => {
    disposed = true
    ++jobEpoch
    ++sessionEpoch
    clearPoll()
  })

  return {
    activeJob,
    jobPollError,
    isWorking,
    progressPhase,
    loadSession,
    trackCurationJob,
    cancelModelTask,
    retryModelTask,
    dismissModelTask,
    resetModelTask
  }
}
