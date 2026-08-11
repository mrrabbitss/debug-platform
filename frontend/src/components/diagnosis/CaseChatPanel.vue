<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import type { ConversationMessage, Job } from '../../types'
import PlanningTracePanel from './PlanningTracePanel.vue'

const props = defineProps<{
  caseId: string
  canEdit: boolean
  modelEgressApproved: boolean
}>()

const messages = ref<ConversationMessage[]>([])
const question = ref('')
const submitting = ref(false)
const activeJob = ref<Job | null>(null)
const activeRunId = ref('')
let timer: number | null = null

const waiting = computed(() => (
  submitting.value || Boolean(activeJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value.status))
))

async function loadMessages() {
  const { data } = await api.get<ConversationMessage[]>(`/cases/${props.caseId}/conversations`)
  messages.value = data
  const pending = [...data].reverse().find(item => (
    item.role === 'user' && ['QUEUED', 'RUNNING'].includes(item.status) && item.job_id
  ))
  if (pending?.job_id && !activeJob.value) {
    activeRunId.value = pending.agent_run_id || ''
    void pollJob(pending.job_id)
  }
}

function schedule(jobId: string) {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => void pollJob(jobId), 1400)
}

async function pollJob(jobId: string) {
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`)
    activeJob.value = data
    await loadMessagesWithoutAutopoll()
    if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) {
      schedule(jobId)
      return
    }
    if (data.status === 'COMPLETED') ElMessage.success('模型回答已完成')
    else if (data.status === 'CANCELLED') ElMessage.warning('本轮问答已取消')
    else ElMessage.error(data.error_message || '模型回答失败')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '问答任务状态查询失败')
  }
}

async function loadMessagesWithoutAutopoll() {
  const { data } = await api.get<ConversationMessage[]>(`/cases/${props.caseId}/conversations`)
  messages.value = data
}

async function send() {
  const content = question.value.trim()
  if (!content) return ElMessage.warning('请输入问题')
  if (!props.modelEgressApproved) return ElMessage.warning('请先在案例概览确认模型出站授权')
  submitting.value = true
  try {
    const { data } = await api.post(`/cases/${props.caseId}/chat`, { question: content })
    question.value = ''
    activeJob.value = data.job
    activeRunId.value = data.agent_run_id
    await loadMessagesWithoutAutopoll()
    schedule(data.job.id)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '问答提交失败')
  } finally {
    submitting.value = false
  }
}

async function cancel() {
  if (!activeJob.value) return
  try {
    const { data } = await api.post<Job>(`/jobs/${activeJob.value.id}/cancel`)
    activeJob.value = data
    schedule(data.id)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '取消失败')
  }
}

onMounted(() => void loadMessages())
onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})
</script>

<template>
  <div>
    <el-alert
      v-if="!modelEgressApproved"
      type="warning"
      :closable="false"
      title="当前案例未授权模型出站，交互问答不会发送。请先在案例概览开启授权。"
      style="margin-bottom:12px"
    />
    <div class="chat-history">
      <el-empty v-if="!messages.length" description="尚无问答记录" />
      <div
        v-for="message in messages"
        :key="message.id"
        :class="['message-row', message.role]"
        data-testid="case-chat-message"
        :data-role="message.role"
      >
        <div class="message-bubble">
          <div class="message-content">{{ message.content }}</div>
          <div class="message-meta">
            {{ message.status }}
            <span v-if="message.error_message"> · {{ message.error_message }}</span>
          </div>
          <div v-if="message.citations?.length" class="message-meta">
            引用：{{ message.citations.map(item => item.evidence_id).filter(Boolean).join('、') }}
          </div>
        </div>
      </div>
    </div>
    <el-progress
      v-if="activeJob && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.status)"
      :percentage="activeJob.progress"
      :format="() => activeJob?.message || activeJob?.status || ''"
      style="margin:12px 0"
    />
    <div class="chat-input">
      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        :disabled="!canEdit || waiting"
        placeholder="例如：为什么认为这个根因成立？有哪些反证？下一步需要补充什么证据？"
        @keyup.ctrl.enter="send"
      />
      <el-button type="primary" :loading="submitting" :disabled="!canEdit || waiting" @click="send">发送到后台</el-button>
      <el-button v-if="activeJob && ['QUEUED', 'RUNNING'].includes(activeJob.status)" type="warning" @click="cancel">取消本轮</el-button>
    </div>
    <PlanningTracePanel
      v-if="activeRunId"
      :case-id="caseId"
      :run-id="activeRunId"
      operation="case_chat"
      title="本轮问答轨迹"
      style="margin-top:14px"
    />
  </div>
</template>

<style scoped>
.chat-history { height: 470px; overflow: auto; border: 1px solid #e5e7eb; padding: 16px; background: #fff; }
.message-row { display: flex; margin-bottom: 14px; }
.message-row.user { justify-content: flex-end; }
.message-bubble { max-width: 82%; border-radius: 8px; padding: 10px 14px; background: #f3f4f6; }
.message-row.user .message-bubble { background: #dbeafe; }
.message-content { white-space: pre-wrap; word-break: break-word; }
.message-meta { color: #64748b; font-size: 12px; margin-top: 5px; }
.chat-input { display: flex; gap: 10px; align-items: flex-start; margin-top: 12px; }
</style>
