<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import { failure } from '../../composables/useWorkbench'
import type { CaseItem } from '../../types'

const props = defineProps<{
  modelValue: boolean
  categories: { id: string; name: string }[]
  sourceCase?: CaseItem | null
  analysisId?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; submitted: [] }>()
const form = reactive({ title: '', problem_category: 'unknown', content: '' })
const busy = ref(false), error = ref(''), savedRecordId = ref('')
watch(() => props.modelValue, open => {
  if (!open) return
  form.title = props.sourceCase?.title || ''
  form.problem_category = props.sourceCase?.problem_category || 'unknown'
  form.content = ''
  error.value = ''
  savedRecordId.value = ''
})
async function importText(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (file.size > 2 * 1024 * 1024) { error.value = '请使用不超过 2 MiB 的 Markdown 或文本文件'; return }
  try {
    const content = await file.text()
    if (content.length > 500000) throw new Error('案例内容不能超过 50 万字')
    form.content = content
    if (!form.title) form.title = file.name.replace(/\.[^.]+$/, '').slice(0, 255)
    error.value = ''
  } catch (cause) { error.value = failure(cause) }
}
async function submit() {
  if (busy.value) return
  if (form.title.trim().length < 2 || form.content.trim().length < 5) {
    error.value = '请填写至少两个字的标题，以及完整的定位结果和验证说明'
    return
  }
  busy.value = true; error.value = ''
  try {
    let submittedForReview = false
    if (!savedRecordId.value) {
      const { data } = await api.post('/workbench/library', {
        ...form, title: form.title.trim(), content: form.content.trim(),
        ...(props.sourceCase ? { case_id: props.sourceCase.id } : {}),
        ...(props.sourceCase && props.analysisId ? { analysis_id: props.analysisId } : {})
      })
      savedRecordId.value = data.id
      submittedForReview = !!data.contribution_id
    }
    if (!submittedForReview) await api.post(`/knowledge-contributions/from-library/${encodeURIComponent(savedRecordId.value)}`)
    emit('update:modelValue', false); emit('submitted')
    ElMessage.success('已提交专家或管理员审核，可在知识库的案例与报告中查看状态')
  } catch (cause) { error.value = savedRecordId.value ? `案例已保存，接入审批队列未完成：${failure(cause)}。可重试接入，或在知识库管理的待审记录中继续。` : failure(cause) }
  finally { busy.value = false }
}
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="value => !busy && emit('update:modelValue', value)"
    :title="analysisId ? '提交案例与诊断报告' : '提交已定位案例'" width="660px"
    :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
    <p class="muted">填写已确认的根因、证据和验证结果，专家或管理员审核通过后进入共享知识库。</p>
    <el-alert v-if="analysisId" title="已关联本案例完成的诊断报告，提交时保存该版本的报告。" type="info" :closable="false" class="inline-alert" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <el-form label-position="top" :disabled="busy || !!savedRecordId" @submit.prevent="submit">
      <el-form-item label="案例标题" required><el-input v-model="form.title" aria-label="提交案例标题" maxlength="255" /></el-form-item>
      <el-form-item label="问题类别"><el-select v-model="form.problem_category" aria-label="提交问题类别"><el-option v-for="item in categories.filter(item => item.id !== 'general')" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
      <el-form-item label="导入 Markdown 或文本"><input type="file" accept=".md,.markdown,.txt" aria-label="导入案例文本" :disabled="busy || !!savedRecordId" @change="importText" /></el-form-item>
      <el-form-item label="定位结果与验证" required><el-input v-model="form.content" aria-label="定位结果与验证" type="textarea" :rows="9" :maxlength="500000" placeholder="问题现象、关键证据、已确认根因、修复措施和验证结果" /></el-form-item>
    </el-form>
    <template #footer><el-button :disabled="busy" @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :loading="busy" @click="submit">{{ savedRecordId?'重试接入审批队列':'提交专家 / 管理员审核' }}</el-button></template>
  </el-dialog>
</template>
