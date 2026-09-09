import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Job } from '../types'
import {
  clearTaskJobBookmark,
  readTaskJobBookmark,
  saveTaskJobBookmark,
  shouldClearTaskJobBookmark
} from './taskJobBookmark'

interface PatchSuggestionTaskOptions {
  caseId: string
  principalId: () => string | undefined
  canSuggest: () => boolean
}

export function usePatchSuggestionTask({ caseId, principalId, canSuggest }: PatchSuggestionTaskOptions) {
  let disposed = false
  const patchSuggestionJob = ref<Job | null>(null)
  const patchSuggestion = ref('')
  const patchSuggestionError = ref('')
  const patchTimer = ref<number | null>(null)
  let patchEpoch = 0

  async function suggestPatch(symbolId: string) {
    if (!canSuggest()) {
      ElMessage.warning('请先确认编辑权限并在案例概览开启模型出站授权')
      return
    }
    patchSuggestion.value = ''
    patchSuggestionError.value = ''
    try {
      const { data } = await api.post<Job>(`/cases/${caseId}/patch-suggestion-jobs`, { symbol_id: symbolId })
      patchSuggestionJob.value = data
      watchPatchSuggestionJob(data)
    } catch (error: any) {
      patchSuggestionError.value = error?.response?.data?.detail || error?.message || '候选补丁任务提交失败'
    }
  }

  function watchPatchSuggestionJob(job: Job) {
    const token = ++patchEpoch
    patchSuggestionJob.value = job
    saveTaskJobBookmark(principalId(), `case:${caseId}:patch-suggestion`, job.id)
    if (patchTimer.value) window.clearTimeout(patchTimer.value)
    const poll = async () => {
      try {
        const { data } = await api.get<Job>(`/jobs/${job.id}`)
        if (disposed || token !== patchEpoch) return
        patchSuggestionJob.value = data
        patchSuggestionError.value = ''
        if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) {
          patchTimer.value = window.setTimeout(() => void poll(), 1200)
          return
        }
        patchTimer.value = null
        if (data.status === 'COMPLETED') {
          let result: { patch?: string; message?: string } = {}
          try { result = JSON.parse(data.result_json || '{}') } catch { /* Preserve the server result as unavailable instead of inventing a patch. */ }
          patchSuggestion.value = result.patch || ''
          if (patchSuggestion.value) ElMessage.success('候选补丁已生成，请人工复制并审查；系统没有修改源码')
          else ElMessage.info(result.message || '未生成候选补丁，需要人工审查')
        } else if (data.status === 'CANCELLED') ElMessage.warning('候选补丁任务已取消')
        else patchSuggestionError.value = data.error_message || '候选补丁任务失败'
      } catch (error: any) {
        if (disposed || token !== patchEpoch) return
        if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(principalId(), `case:${caseId}:patch-suggestion`)
        patchSuggestionError.value = error?.response?.data?.detail || error?.message || '任务状态暂时无法刷新，页面保留最近一次确认的进度。'
        if (!disposed && patchSuggestionJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(patchSuggestionJob.value.status)) {
          patchTimer.value = window.setTimeout(() => void poll(), 2500)
        }
      }
    }
    void poll()
  }

  async function restorePatchSuggestionJob() {
    const scope = `case:${caseId}:patch-suggestion`
    const jobId = readTaskJobBookmark(principalId(), scope)
    if (!jobId) return
    const token = patchEpoch
    try {
      const { data } = await api.get<Job>(`/jobs/${jobId}`)
      if (disposed || token !== patchEpoch) return
      patchSuggestionJob.value = data
      if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) watchPatchSuggestionJob(data)
      else if (data.status === 'COMPLETED') {
        try { patchSuggestion.value = JSON.parse(data.result_json || '{}')?.patch || '' } catch { patchSuggestion.value = '' }
      }
    } catch (error) {
      if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(principalId(), scope)
    }
  }

  async function copyPatchSuggestion() {
    if (!patchSuggestion.value) return
    try { await navigator.clipboard.writeText(patchSuggestion.value); ElMessage.success('候选补丁已复制；请在外部审查后自行应用') }
    catch { ElMessage.warning('浏览器未允许写入剪贴板，请手工复制候选内容') }
  }

  async function cancelPatchSuggestion() {
    if (!patchSuggestionJob.value) return
    try { const { data } = await api.post<Job>(`/jobs/${patchSuggestionJob.value.id}/cancel`); watchPatchSuggestionJob(data) }
    catch (error: any) { patchSuggestionError.value = error?.response?.data?.detail || error?.message || '取消请求失败' }
  }

  async function retryPatchSuggestion() {
    if (!patchSuggestionJob.value) return
    try { const { data } = await api.post<Job>(`/jobs/${patchSuggestionJob.value.id}/retry`); patchSuggestionError.value = ''; watchPatchSuggestionJob(data) }
    catch (error: any) { patchSuggestionError.value = error?.response?.data?.detail || error?.message || '重试任务创建失败' }
  }

  function dismissPatchSuggestion() {
    ++patchEpoch
    if (patchTimer.value) window.clearTimeout(patchTimer.value)
    clearTaskJobBookmark(principalId(), `case:${caseId}:patch-suggestion`)
    patchSuggestionJob.value = null
    patchSuggestion.value = ''
    patchSuggestionError.value = ''
  }

  onBeforeUnmount(() => {
    disposed = true
    if (patchTimer.value) window.clearTimeout(patchTimer.value)
  })

  return {
    patchSuggestionJob,
    patchSuggestion,
    patchSuggestionError,
    suggestPatch,
    restorePatchSuggestionJob,
    copyPatchSuggestion,
    cancelPatchSuggestion,
    retryPatchSuggestion,
    dismissPatchSuggestion
  }
}
