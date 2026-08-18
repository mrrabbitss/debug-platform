<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import type { AnalysisRevision, ConversationMessage, Job } from '../../types'
import PlanningTracePanel from './PlanningTracePanel.vue'

const props = defineProps<{
  caseId: string
  canEdit: boolean
  modelEgressApproved: boolean
  latestAnalysisId: string
}>()
const emit = defineEmits<{ analysisUpdated: [] }>()

const messages = ref<ConversationMessage[]>([])
const question = ref('')
const intent = ref<'ANSWER' | 'REVISE_DIAGNOSIS'>('ANSWER')
const revisions = ref<AnalysisRevision[]>([])
const submitting = ref(false)
const activeJob = ref<Job | null>(null)
const activeRunId = ref('')
let timer: number | null = null

const waiting = computed(() => (
  submitting.value || Boolean(activeJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value.status))
))

async function loadMessages() {
  const [messagesResponse, revisionsResponse] = await Promise.all([
    api.get<ConversationMessage[]>(`/cases/${props.caseId}/conversations`),
    api.get<AnalysisRevision[]>(`/cases/${props.caseId}/analysis-revisions`)
  ])
  const data = messagesResponse.data
  messages.value = data
  revisions.value = revisionsResponse.data
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
  const [messagesResponse, revisionsResponse] = await Promise.all([
    api.get<ConversationMessage[]>(`/cases/${props.caseId}/conversations`),
    api.get<AnalysisRevision[]>(`/cases/${props.caseId}/analysis-revisions`)
  ])
  messages.value = messagesResponse.data
  revisions.value = revisionsResponse.data
}

async function send() {
  const content = question.value.trim()
  if (!content) return ElMessage.warning('请输入问题')
  if (!props.modelEgressApproved) return ElMessage.warning('请先在案例概览确认模型出站授权')
  submitting.value = true
  try {
    const { data } = await api.post(`/cases/${props.caseId}/chat`, {
      question: content,
      intent: intent.value,
      source_analysis_id: intent.value === 'REVISE_DIAGNOSIS' ? props.latestAnalysisId : undefined
    })
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

async function reviewRevision(revision: AnalysisRevision, action: 'apply' | 'reject') {
  try {
    await api.post(`/cases/${props.caseId}/analysis-revisions/${revision.id}/${action}`, { comment: '' })
    ElMessage.success(action === 'apply' ? '修订已确认，已生成新版综合诊断和报告数据' : '修订草稿已拒绝')
    await loadMessagesWithoutAutopoll()
    if (action === 'apply') emit('analysisUpdated')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '修订审核失败')
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
            引用：{{ message.citations.map(item => item.display_label || item.title || '证据位置未记录').filter(Boolean).join('、') }}
          </div>
        </div>
      </div>
    </div>
    <el-card v-if="revisions.length" shadow="never" class="revision-list" data-testid="analysis-revisions">
      <template #header>诊断与报告修订草稿</template>
      <el-collapse>
        <el-collapse-item v-for="revision in revisions" :key="revision.id" :name="revision.id">
          <template #title>
            <el-tag :type="revision.status === 'DRAFT' ? 'warning' : revision.status === 'APPLIED' ? 'success' : 'info'">{{ revision.status }}</el-tag>
            <span class="revision-title">{{ revision.change_summary || revision.instruction }}</span>
          </template>
          <el-alert type="info" :closable="false" title="该草稿同时修改综合诊断结构和由它生成的诊断报告；确认前不会覆盖现有版本。" />
          <h4>修订要求</h4><div class="message-content">{{ revision.instruction }}</div>
          <h4>新版摘要</h4><div class="message-content">{{ revision.proposed_result?.summary || '尚未生成' }}</div>
          <h4>根因候选</h4>
          <ul><li v-for="item in revision.proposed_result?.hypotheses || []" :key="item.rank">{{ item.rank }}. {{ item.title }}（{{ item.confidence_level }}）</li></ul>
          <div v-if="revision.status === 'DRAFT'" class="revision-actions">
            <el-button type="primary" :disabled="!canEdit" data-testid="apply-analysis-revision" @click="reviewRevision(revision, 'apply')">确认并生成新版</el-button>
            <el-button type="danger" :disabled="!canEdit" @click="reviewRevision(revision, 'reject')">拒绝草稿</el-button>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>
    <el-progress
      v-if="activeJob && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.status)"
      :percentage="activeJob.progress"
      :format="() => activeJob?.message || activeJob?.status || ''"
      style="margin:12px 0"
    />
    <div class="chat-input">
      <el-radio-group v-model="intent" :disabled="waiting">
        <el-radio-button value="ANSWER">证据问答</el-radio-button>
        <el-radio-button value="REVISE_DIAGNOSIS" :disabled="!latestAnalysisId">修订诊断与报告</el-radio-button>
      </el-radio-group>
      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        :disabled="!canEdit || waiting"
        :placeholder="intent === 'ANSWER' ? '例如：为什么认为这个根因成立？有哪些反证？' : '例如：把 AP 离线调整为第二根因，并补充 GW 上联异常的反证和验证步骤'"
        @keyup.ctrl.enter="send"
      />
      <el-button type="primary" :loading="submitting" :disabled="!canEdit || waiting" @click="send">发送到后台</el-button>
      <el-button v-if="activeJob && ['QUEUED', 'RUNNING'].includes(activeJob.status)" type="warning" @click="cancel">取消本轮</el-button>
    </div>
    <PlanningTracePanel
      v-if="activeRunId"
      :case-id="caseId"
      :run-id="activeRunId"
      :operation="intent === 'REVISE_DIAGNOSIS' ? 'diagnosis_revision' : 'case_chat'"
      :title="intent === 'REVISE_DIAGNOSIS' ? '本轮诊断修订轨迹' : '本轮问答轨迹'"
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
.chat-input { flex-wrap: wrap; }
.chat-input .el-textarea { flex: 1 1 520px; }
.revision-list { margin-top: 12px; }
.revision-title { margin-left: 8px; }
.revision-actions { margin-top: 12px; }
</style>
