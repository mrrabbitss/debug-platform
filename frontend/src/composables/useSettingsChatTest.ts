import { onBeforeUnmount, ref } from 'vue'
import type { Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Job } from '../types'
import {
  clearTaskJobBookmark,
  readTaskJobBookmark,
  saveTaskJobBookmark,
  shouldClearTaskJobBookmark
} from './taskJobBookmark'

interface SettingsChatTestOptions {
  principalId: Ref<string>
  errorText: (error: unknown) => string
}

export function useSettingsChatTest({ principalId, errorText }: SettingsChatTestOptions) {
  const activeChatTestJob = ref<Job | null>(null)
  const chatTestPollError = ref('')
  let chatTestTimer: number | undefined
  let disposed = false, chatTestEpoch = 0

  function chatTestScope() { return 'system:chat-model-test' }
  function trackChatTest(job: Job) {
    const token = ++chatTestEpoch
    activeChatTestJob.value = job
    saveTaskJobBookmark(principalId.value, chatTestScope(), job.id)
    scheduleChatTestPoll(job.id, token)
  }
  function scheduleChatTestPoll(jobId: string, token = chatTestEpoch) {
    if (chatTestTimer !== undefined) window.clearTimeout(chatTestTimer)
    chatTestTimer = window.setTimeout(() => void pollChatTest(jobId, token), 1200)
  }

  async function pollChatTest(jobId: string, token = chatTestEpoch) {
    try {
      const { data } = await api.get<Job>(`/jobs/${jobId}`)
      if (disposed || token !== chatTestEpoch) return
      activeChatTestJob.value = data
      chatTestPollError.value = ''
      if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) return scheduleChatTestPoll(jobId, token)
      if (data.status === 'COMPLETED') {
        try {
          const test = JSON.parse(data.result_json || '{}')?.test
          if (!test || test.ok !== true) throw new Error('诊断模型测试结果未通过')
          ElMessage.success(test.proxy_url_configured ? '诊断模型连接正常（已使用配置代理）' : '诊断模型连接正常')
        } catch (error: any) { chatTestPollError.value = error?.message || '诊断模型测试结果无法读取' }
      }
      else if (data.status === 'CANCELLED') ElMessage.warning('诊断模型测试已取消')
      else ElMessage.error(data.error_message || '诊断模型测试失败')
    } catch (error) {
      if (disposed || token !== chatTestEpoch) return
      chatTestPollError.value = errorText(error) || '任务状态暂时无法刷新，页面保留最近一次确认的进度。'
      if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(principalId.value, chatTestScope())
      if (!shouldClearTaskJobBookmark(error) && activeChatTestJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeChatTestJob.value.status)) scheduleChatTestPoll(jobId, token)
    }
  }

  async function cancelChatTest() {
    if (!activeChatTestJob.value) return
    try { const { data } = await api.post<Job>(`/jobs/${activeChatTestJob.value.id}/cancel`); activeChatTestJob.value = data; scheduleChatTestPoll(data.id) }
    catch (error) { chatTestPollError.value = errorText(error) }
  }

  async function retryChatTest() {
    if (!activeChatTestJob.value) return
    try { const { data } = await api.post<Job>(`/jobs/${activeChatTestJob.value.id}/retry`); chatTestPollError.value = ''; trackChatTest(data) }
    catch (error) { chatTestPollError.value = errorText(error) }
  }
  async function restoreChatTest() {
    const token = chatTestEpoch, jobId = readTaskJobBookmark(principalId.value, chatTestScope())
    if (!jobId) return
    try {
      const { data } = await api.get<Job>(`/jobs/${jobId}`)
      if (disposed || token !== chatTestEpoch) return
      activeChatTestJob.value = data
      if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) trackChatTest(data)
    } catch (error) {
      if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(principalId.value, chatTestScope())
    }
  }
  function dismissChatTest() {
    ++chatTestEpoch
    if (chatTestTimer !== undefined) window.clearTimeout(chatTestTimer)
    clearTaskJobBookmark(principalId.value, chatTestScope())
    activeChatTestJob.value = null
    chatTestPollError.value = ''
  }

  onBeforeUnmount(() => {
    disposed = true
    ++chatTestEpoch
    if (chatTestTimer !== undefined) window.clearTimeout(chatTestTimer)
  })

  return {
    activeChatTestJob,
    chatTestPollError,
    trackChatTest,
    restoreChatTest,
    cancelChatTest,
    retryChatTest,
    dismissChatTest
  }
}
