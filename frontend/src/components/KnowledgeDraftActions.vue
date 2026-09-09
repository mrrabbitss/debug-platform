<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { KnowledgeDocument, KnowledgeProposal } from '../types'
const props = defineProps<{ document: KnowledgeDocument }>()
const emit = defineEmits<{ changed: [] }>()
const busy = ref(false)
const preview = ref(false)
const publishedContent = ref('')
const proposedContent = ref('')
const selected = ref<KnowledgeProposal | null>(null)
const proposals = computed(() => Array.from(new Map([
  ...(props.document.pending_draft ? [props.document.pending_draft] : []),
  ...(props.document.review_drafts || [])
].map(draft => [draft.id, draft])).values()))
async function inspect(draft: KnowledgeProposal) {
  try {
    const { data } = await api.get<KnowledgeDocument>(`/knowledge/${props.document.id}`)
    const current = [data.pending_draft, ...(data.review_drafts || [])].find(item => item?.id === draft.id)
    if (!current?.snapshot) throw new Error('Proposal unavailable')
    selected.value = current
    publishedContent.value = data.content || ''
    proposedContent.value = current.snapshot.content || ''
    preview.value = true
  } catch { ElMessage.error('草稿已变化或无权读取，请刷新') }
}
async function review(action: string, draft: KnowledgeProposal) {
  if (action === 'APPROVE' && (!preview.value || selected.value?.id !== draft.id)) {
    await inspect(draft)
    return
  }
  try {
    if (action === 'APPROVE') await ElMessageBox.confirm(
      '确认已比较线上正文与修订？发布成功后所有人的新诊断使用新版，失败保留原版。', '确认发布', { type: 'warning' })
  } catch { return }
  busy.value = true
  try {
    const { data } = await api.post(`/knowledge/${props.document.id}/draft/review`, {
      draft_id: draft.id, action, expected_version: draft.version, comment: '网页知识草稿审核'
    })
    ElMessage.success(data.job ? '发布任务已提交，可在任务中心查看；线上旧版继续可用' : '草稿状态已更新')
    preview.value = false
    emit('changed')
  } catch { ElMessage.error('操作失败，请刷新后检查版本和权限') }
  finally { busy.value = false }
}
</script>
<template>
  <span v-for="draft in proposals" :key="draft.id">
    <el-tag type="warning">{{ draft.id === document.pending_draft?.id ? '我的修订' : '待审修订' }} · {{ draft.status }}</el-tag>
    <el-button link @click="inspect(draft)">预览</el-button>
    <el-button v-if="draft.id === document.pending_draft?.id && ['DRAFT', 'REJECTED', 'FAILED'].includes(draft.status)" link :disabled="busy" @click="review('SUBMIT', draft)">提交草稿</el-button>
    <el-button v-if="document.can_publish && draft.status === 'IN_REVIEW'" link type="success" :disabled="busy" @click="review('APPROVE', draft)">发布草稿</el-button>
    <el-button v-if="document.can_publish && draft.status === 'IN_REVIEW'" link :disabled="busy" @click="review('REJECT', draft)">退回草稿</el-button>
    <el-button v-if="draft.id === document.pending_draft?.id && draft.status !== 'BUILDING'" link :disabled="busy" @click="review('ARCHIVE', draft)">撤销草稿</el-button>
  </span>
  <el-dialog v-model="preview" title="比较知识修订" width="85%" append-to-body>
    <p>比较当前发布正文与拟发布修订。专家或管理员批准后，新诊断使用发布成功的版本。</p>
    <el-row :gutter="20">
      <el-col :span="12"><strong>线上正文</strong><pre class="proposal-text">{{ publishedContent }}</pre></el-col>
      <el-col :span="12"><strong>修订正文</strong><pre class="proposal-text">{{ proposedContent }}</pre></el-col>
    </el-row>
    <template #footer><el-button v-if="document.can_publish && selected?.status === 'IN_REVIEW'" type="primary" :loading="busy" @click="selected && review('APPROVE', selected)">确认发布此修订</el-button></template>
  </el-dialog>
</template>
<style scoped>
.proposal-text { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 55vh; overflow: auto; }
</style>
