<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api/client'
import { contributionsApi, contributionStatus, type KnowledgeContribution } from '../../api/knowledgeContributions'
import { failure } from '../../composables/useWorkbench'
import { clearTaskJobBookmark, readTaskJobBookmark, saveTaskJobBookmark, shouldClearTaskJobBookmark } from '../../composables/taskJobBookmark'
import type { Job } from '../../types'
import ModelTaskProgress from '../common/ModelTaskProgress.vue'
const props = defineProps<{ id: string; ownerId: string; review?: boolean }>()
const emit = defineEmits<{ changed: []; close: [] }>()
const item = ref<KnowledgeContribution | null>(null), busy = ref(false), loading = ref(false), error = ref('')
const title = ref(''), content = ref(''), comment = ref(''), instruction = ref(''), consent = ref(true), checked = ref(false), contentTab = ref('content')
let epoch = 0, timer: number | undefined, disposed = false
const activeReviewJob = ref<Job | null>(null), reviewPollError = ref('')
let reviewJobTimer: number | undefined
let reviewJobEpoch = 0
const ownDraft = computed(() => !!item.value && item.value.owner_id === props.ownerId && ['DRAFT', 'RETURNED', 'REJECTED'].includes(item.value.status))
const reviewing = computed(() => !!props.review && item.value?.status === 'SUBMITTED')
const editable = computed(() => ownDraft.value || reviewing.value)
const dirty = computed(() => !!item.value && (title.value !== item.value.candidate.title || content.value !== item.value.candidate.content))
const unsent = computed(() => !!instruction.value.trim())
const reviewJobActive = computed(() => ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeReviewJob.value?.status || ''))
function apply(value: KnowledgeContribution) {
  if (disposed) return
  item.value = value; title.value = value.candidate.title; content.value = value.candidate.content; checked.value = false
  window.clearTimeout(timer)
  if (['APPROVED', 'PUBLISHING'].includes(value.status)) timer = window.setTimeout(() => load(), 2000)
}
async function load() {
  const requestEpoch = ++epoch, id = props.id; loading.value = !item.value; error.value = ''
  try { const value = await contributionsApi.get(id); if (requestEpoch === epoch && props.id === id) apply(value) }
  catch (cause) { if (requestEpoch === epoch) error.value = failure(cause) }
  finally { if (requestEpoch === epoch) loading.value = false }
}
async function refresh() {
  if (dirty.value || unsent.value) {
    try { await ElMessageBox.confirm('刷新将丢弃当前未保存的编辑和未发送的要求。', '重新读取服务器版本', { type: 'warning' }) } catch { return }
  }
  instruction.value = ''; await load()
}
async function perform(operation: () => Promise<KnowledgeContribution>, message: string) {
  if (busy.value) return
  const id = props.id, requestEpoch = ++epoch; busy.value = true; error.value = ''; window.clearTimeout(timer)
  try {
    const value = await operation()
    if (requestEpoch !== epoch || id !== props.id) return
    apply(value); emit('changed'); ElMessage.success(message)
  } catch (cause) { if (requestEpoch === epoch) { checked.value = false; error.value = failure(cause) } }
  finally { if (requestEpoch === epoch) busy.value = false }
}
async function save() {
  if (!item.value || !editable.value) return
  if (!title.value.trim() || !content.value.trim()) { error.value = '标题和 Markdown 内容不能为空'; return }
  const current = item.value
  await perform(() => contributionsApi.update(current.id, { expected_version: current.version, ...(current.operation === 'DELETE' ? {} : { title: title.value.trim(), content: content.value }), comment: comment.value }, reviewing.value), '修改已保存，原稿与差异已保留')
}
async function chat() {
  if (!item.value || !reviewing.value || reviewJobActive.value || item.value.operation === 'DELETE' || dirty.value || !consent.value || !instruction.value.trim()) return
  const current = item.value, text = instruction.value.trim()
  busy.value = true; reviewPollError.value = ''
  try {
    const job = await contributionsApi.reviewChatJob(current, text, consent.value)
    trackReviewJob(job)
    instruction.value = ''
    ElMessage.info('AI 修正任务已进入后台队列')
  } catch (cause) { reviewPollError.value = failure(cause) }
  finally { busy.value = false }
}

function reviewJobScope() { return `knowledge-contribution:${props.id}:review-chat` }
function trackReviewJob(job: Job) {
  const token = ++reviewJobEpoch
  activeReviewJob.value = job
  saveTaskJobBookmark(props.ownerId, reviewJobScope(), job.id)
  scheduleReviewJob(job.id, token)
}
function scheduleReviewJob(jobId: string, token = reviewJobEpoch) {
  if (reviewJobTimer !== undefined) window.clearTimeout(reviewJobTimer)
  reviewJobTimer = window.setTimeout(() => void pollReviewJob(jobId, token), 1200)
}
async function pollReviewJob(jobId: string, token = reviewJobEpoch) {
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`)
    if (disposed || token !== reviewJobEpoch) return
    activeReviewJob.value = data; reviewPollError.value = ''
    if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) return scheduleReviewJob(jobId, token)
    if (data.status === 'COMPLETED') { ElMessage.success('AI 修订已保存，请核对新的差异'); await load(); emit('changed') }
    else if (data.status === 'CANCELLED') ElMessage.warning('AI 修正已取消')
    else reviewPollError.value = data.error_message || 'AI 修正失败'
  } catch (cause) {
    if (disposed || token !== reviewJobEpoch) return
    // Keep the last known job rather than converting a polling outage into a failed review.
    reviewPollError.value = failure(cause) || '任务状态暂时无法刷新，页面保留最近一次确认的进度。'
    if (shouldClearTaskJobBookmark(cause)) clearTaskJobBookmark(props.ownerId, reviewJobScope())
    if (!shouldClearTaskJobBookmark(cause) && activeReviewJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeReviewJob.value.status)) scheduleReviewJob(jobId, token)
  }
}
async function cancelReviewJob() {
  if (!activeReviewJob.value) return
  try {
    const { data } = await api.post<Job>(`/jobs/${activeReviewJob.value.id}/cancel`)
    activeReviewJob.value = data; scheduleReviewJob(data.id)
  } catch (cause) { reviewPollError.value = failure(cause) }
}
async function retryReviewJob() {
  if (!activeReviewJob.value) return
  try {
    const { data } = await api.post<Job>(`/jobs/${activeReviewJob.value.id}/retry`)
    reviewPollError.value = ''; trackReviewJob(data)
  } catch (cause) { reviewPollError.value = failure(cause) }
}
async function restoreReviewJob() {
  const id = props.id, token = reviewJobEpoch, scope = reviewJobScope()
  const jobId = readTaskJobBookmark(props.ownerId, scope)
  if (!jobId) return
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`)
    if (disposed || token !== reviewJobEpoch || props.id !== id) return
    activeReviewJob.value = data
    if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) trackReviewJob(data)
  } catch (cause) {
    if (shouldClearTaskJobBookmark(cause)) clearTaskJobBookmark(props.ownerId, scope)
  }
}
function dismissReviewJob() {
  ++reviewJobEpoch
  window.clearTimeout(reviewJobTimer)
  clearTaskJobBookmark(props.ownerId, reviewJobScope())
  activeReviewJob.value = null
  reviewPollError.value = ''
}
async function submit() {
  if (!item.value || !ownDraft.value || dirty.value || unsent.value) return
  const current = item.value; await perform(() => contributionsApi.submit(current), '已提交，专家或管理员审核后生效')
}
async function publishOwn() {
  if (!props.review || !item.value || !ownDraft.value || !checked.value || dirty.value || unsent.value) return
  const current = item.value
  await perform(async () => {
    const submitted = await contributionsApi.submit(current)
    // The submit response advances the version; approval binds its exact content hash.
    apply(submitted)
    return contributionsApi.review(submitted, 'APPROVE', comment.value)
  }, '审批已保存，正在准备发布')
}
async function review(action: 'APPROVE' | 'RETURN' | 'REJECT') {
  if (!item.value || !reviewing.value || dirty.value || unsent.value || (action === 'APPROVE' && !checked.value)) return
  const current = item.value
  await perform(() => contributionsApi.review(current, action, comment.value), action === 'APPROVE' ? '审批已保存，正在准备发布' : '审核意见已保存')
}
async function remove() {
  if (!item.value || !ownDraft.value || busy.value) return
  try { await ElMessageBox.confirm(`删除自己的草稿“${item.value.candidate.title}”？共享知识保持原状。`, '删除草稿', { type: 'warning' }) } catch { return }
  busy.value = true
  try { await contributionsApi.remove(item.value); emit('changed'); emit('close') }
  catch (cause) { error.value = failure(cause) }
  finally { busy.value = false }
}
async function retry() { const current = item.value; if (current && props.review && current.status === 'FAILED') await perform(() => contributionsApi.retry(current), '已恢复同一已审批版本的发布任务') }
watch(() => props.id, () => { item.value = null; comment.value = ''; instruction.value = ''; busy.value = false; activeReviewJob.value = null; reviewPollError.value = ''; ++reviewJobEpoch; window.clearTimeout(reviewJobTimer); void load(); void restoreReviewJob() }, { immediate: true })
onBeforeUnmount(() => { disposed = true; epoch++; ++reviewJobEpoch; window.clearTimeout(timer); window.clearTimeout(reviewJobTimer) })
</script>
<template>
  <section class="contribution-detail" v-loading="loading" aria-label="知识提交详情">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link :disabled="busy" @click="refresh">刷新并重新核对</el-button></el-alert>
    <template v-if="item">
      <div class="toolbar"><el-tag :type="item.status==='PUBLISHED'?'success':item.status==='FAILED'?'danger':'info'">{{ contributionStatus(item.status) }}</el-tag><span>{{ item.content_kind==='SKILL'?'Skill 修改建议':'普通知识' }} · v{{ item.version }}</span><el-tag v-if="item.operation==='DELETE'" type="danger">申请删除共享内容</el-tag><el-button :disabled="busy" @click="refresh">刷新状态</el-button></div>
      <details class="technical-details"><summary>分类、文件路径与完整属性</summary><div class="form-columns"><div><strong>原稿属性</strong><pre class="document-text">{{ JSON.stringify({source_type:item.original.source_type,category_id:item.original.category_id,metadata:item.original.metadata},null,2) }}</pre></div><div><strong>拟发布属性</strong><pre class="document-text">{{ JSON.stringify({source_type:item.candidate.source_type,category_id:item.candidate.category_id,metadata:item.candidate.metadata},null,2) }}</pre></div></div></details>
      <el-alert v-if="['APPROVED','PUBLISHING'].includes(item.status)" title="审批已持久保存，索引构建成功后生效。可以关闭页面，服务重启后会继续处理。" type="info" :closable="false" class="inline-alert" />
      <el-alert v-if="item.error_message" :title="item.error_message" type="warning" :closable="false" class="inline-alert" />
      <div class="review-workspace">
        <div class="review-document">
          <el-tabs v-model="contentTab"><el-tab-pane label="拟发布内容" name="content">
            <el-form label-position="top"><el-form-item label="知识标题"><el-input v-model="title" aria-label="提交标题" :readonly="!editable || item.operation==='DELETE'" :disabled="busy" /></el-form-item><el-form-item label="Markdown 正文"><el-input v-if="editable && item.operation!=='DELETE'" v-model="content" aria-label="提交 Markdown" type="textarea" :rows="18" :disabled="busy" /><pre v-else class="document-text">{{ content }}</pre></el-form-item></el-form>
          </el-tab-pane><el-tab-pane label="提交原稿" name="original"><pre class="document-text">{{ item.original.content || '此提交为新建文档，没有原始共享版本。' }}</pre></el-tab-pane><el-tab-pane label="修改差异" name="diff"><pre class="document-text diff-text">{{ item.diff || '正文尚无变化。类别等修改请同时核对属性。' }}</pre></el-tab-pane><el-tab-pane label="审核与版本" name="history"><div v-for="revision in item.revisions" :key="revision.version" class="revision-entry"><strong>v{{ revision.version }} · {{ revision.action }}</strong><p>{{ revision.actor_id }} · {{ new Date(revision.created_at).toLocaleString() }}</p><p>{{ revision.comment }}</p><details v-if="revision.diff"><summary>本次差异</summary><pre class="document-text">{{ revision.diff }}</pre></details></div></el-tab-pane></el-tabs>
          <div class="toolbar" v-if="editable"><el-button :disabled="busy || !dirty" @click="save">保存修改</el-button><span v-if="dirty" class="field-hint">有未保存修改，保存后才能对话或提交审核。</span></div>
        </div>
        <aside class="review-assistant" aria-label="审核 AI 修正">
          <h3>{{ reviewing ? 'AI 多轮修正' : '提炼与修正记录' }}</h3><p class="muted">使用你的统一模型选择。每次修订保存原稿、差异和对话，再核对最终版本。</p>
          <div v-for="(message,index) in item.messages" :key="index" class="conversation-message"><strong>{{ message.role==='user'?'人工要求':'AI 回复' }}</strong><p class="preserve-lines">{{ message.content }}</p></div>
          <ModelTaskProgress v-if="activeReviewJob" :job="activeReviewJob" compact test-id="contribution-review-model-progress">
            <template #actions><el-button v-if="reviewJobActive" size="small" type="warning" @click="cancelReviewJob">取消本轮</el-button><el-button v-if="['FAILED','CANCELLED','DEAD_LETTER'].includes(activeReviewJob.status)" size="small" type="primary" @click="retryReviewJob">重试本轮</el-button><el-button v-if="['COMPLETED','FAILED','CANCELLED','DEAD_LETTER'].includes(activeReviewJob.status)" size="small" @click="dismissReviewJob">关闭记录</el-button></template>
          </ModelTaskProgress>
          <el-alert v-if="reviewPollError" type="warning" :title="reviewPollError" :closable="false" class="inline-alert" />
          <template v-if="reviewing && item.operation!=='DELETE'"><el-input v-model="instruction" aria-label="AI 修正要求" type="textarea" :rows="4" :disabled="busy || reviewJobActive" placeholder="指出需要修改之处，可以继续追问和纠正" /><el-checkbox v-model="consent" :disabled="busy || reviewJobActive">允许发送至所选模型</el-checkbox><el-button type="primary" plain :loading="busy" :disabled="reviewJobActive || !consent || !instruction.trim() || dirty" @click="chat">发送修正要求</el-button></template>
          <el-form-item v-if="reviewing" label="审核意见"><el-input v-model="comment" aria-label="审核意见" type="textarea" :rows="3" :disabled="busy" /></el-form-item>
          <p v-if="item.review_comment" class="field-hint">审核意见：{{ item.review_comment }}</p>
        </aside>
      </div>
      <div v-if="reviewing" class="review-actions"><el-checkbox v-model="checked" :disabled="busy || dirty || unsent">已核对 v{{ item.version }} 的完整内容与修改差异</el-checkbox><div class="toolbar"><el-button type="primary" :disabled="!checked || dirty || unsent || busy" @click="review('APPROVE')">批准并发布</el-button><el-button :disabled="dirty || unsent || busy" @click="review('RETURN')">退回修改</el-button><el-button type="danger" plain :disabled="dirty || unsent || busy" @click="review('REJECT')">驳回</el-button></div></div>
      <div v-if="ownDraft" class="review-actions"><template v-if="props.review"><el-checkbox v-model="checked" :disabled="busy || dirty || unsent">已核对 v{{ item.version }} 的完整内容与修改差异</el-checkbox><el-button type="primary" :disabled="busy || dirty || unsent || !checked" @click="publishOwn">批准并发布此版本</el-button></template><el-button v-else type="primary" :disabled="busy || dirty || unsent" @click="submit">提交审核</el-button><el-button type="danger" plain :disabled="busy" @click="remove">删除我的草稿</el-button></div>
      <el-button v-if="props.review && item.status==='FAILED'" :loading="busy" @click="retry">重试已审批的发布</el-button>
    </template>
  </section>
</template>
