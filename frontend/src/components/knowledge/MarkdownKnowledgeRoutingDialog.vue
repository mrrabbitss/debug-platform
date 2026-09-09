<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  importMarkdownKnowledge,
  loadKnowledgeRoutingModels,
  waitForKnowledgeRoutingJob
} from '../../api/knowledgeRouting'
import { api } from '../../api/client'
import type { Job, KnowledgeRoutingJobResult, ModelProfile } from '../../types'
import ModelTaskProgress from '../common/ModelTaskProgress.vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  completed: [documentIds: string[]]
}>()

interface RoutingRow {
  relativePath: string
  documentId: string
  status: string
  message: string
  job?: Job
  result?: KnowledgeRoutingJobResult
  error?: string
}

const models = ref<ModelProfile[]>([])
const files = ref<File[]>([])
const rows = ref<RoutingRow[]>([])
const loadingModels = ref(false)
const submitting = ref(false)
let batchAbortController: AbortController | undefined
let disposed = false
const form = reactive({
  modelProfileId: '',
  consentModelEgress: false,
  trustLevel: 'MEDIUM' as 'LOW' | 'MEDIUM' | 'HIGH',
  confidentiality: 'INTERNAL' as 'PUBLIC' | 'INTERNAL' | 'RESTRICTED'
})

const eligibleModels = computed(() => models.value.filter(
  model => model.enabled && model.task_type === 'chat' && model.provider !== 'mock'
))
const selectedModel = computed(() => eligibleModels.value.find(
  model => model.id === form.modelProfileId
))
const requiresEgressConsent = computed(() => selectedModel.value?.mode === 'api')

function errorText(error: any) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map(item => item?.msg || String(item)).join('；')
  }
  if (detail) return JSON.stringify(detail)
  return error?.message || (typeof error === 'string' ? error : '操作失败')
}

function reset() {
  models.value = []
  files.value = []
  rows.value = []
  form.modelProfileId = ''
  form.consentModelEgress = false
  form.trustLevel = 'MEDIUM'
  form.confidentiality = 'INTERNAL'
}

async function loadModels() {
  loadingModels.value = true
  try {
    models.value = await loadKnowledgeRoutingModels()
    form.modelProfileId = eligibleModels.value.find(model => model.is_active)?.id
      || eligibleModels.value[0]?.id
      || ''
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loadingModels.value = false
  }
}

function selectFiles(event: Event) {
  const input = event.target as HTMLInputElement
  files.value = Array.from(input.files || [])
}

function selectModel() {
  form.consentModelEgress = false
}

function statusLabel(status: string) {
  return {
    QUEUED: '等待中',
    PENDING: '等待中',
    RUNNING: '分析中',
    COMPLETED: '已完成',
    FAILED: '失败',
    CANCELLED: '已取消',
    DEAD_LETTER: '失败'
  }[status] || status
}

function statusType(status: string) {
  if (status === 'COMPLETED') return 'success'
  if (['FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(status)) return 'danger'
  if (status === 'RUNNING') return 'warning'
  return 'info'
}

function confidenceText(value?: number) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '—'
}

function close() {
  if (!submitting.value) emit('update:modelValue', false)
}

async function submit() {
  if (!files.value.length) return ElMessage.warning('请选择一个或多个 Markdown 文件')
  if (files.value.length > 20) return ElMessage.warning('每批最多导入 20 个 Markdown 文件')
  const unsupported = files.value.find(file => !/\.(md|markdown)$/i.test(file.name))
  if (unsupported) return ElMessage.warning(`不支持文件“${unsupported.name}”，仅允许 .md/.markdown`)
  if (!form.modelProfileId) return ElMessage.warning('请先在系统设置中配置可用的大模型 API')
  if (requiresEgressConsent.value && !form.consentModelEgress) {
    return ElMessage.warning('请确认脱敏、限长的 Markdown 摘要可以发送到所选模型 API')
  }
  const relativePaths = files.value.map(file => file.webkitRelativePath || file.name)
  if (new Set(relativePaths.map(path => path.toLocaleLowerCase())).size !== relativePaths.length) {
    return ElMessage.warning('同一批次中的 Markdown 文件名或相对路径不能重复')
  }

  submitting.value = true
  rows.value = []
  try {
    const response = await importMarkdownKnowledge({
      files: files.value,
      relativePaths,
      modelProfileId: form.modelProfileId,
      consentModelEgress: form.consentModelEgress,
      trustLevel: form.trustLevel,
      confidentiality: form.confidentiality
    })
    rows.value = response.items.map(item => ({
      relativePath: item.relative_path,
      documentId: item.document_id,
      status: item.job.status,
      message: item.job.message,
      job: item.job
    }))
    batchAbortController?.abort()
    batchAbortController = new AbortController()
    const signal = batchAbortController.signal

    const settled = await Promise.allSettled(response.items.map(async (item, index) => {
      const completed = await waitForKnowledgeRoutingJob(item.job, (job: Job) => {
        if (disposed || signal.aborted) return
        const row = rows.value[index]
        if (!row) return
        row.status = job.status
        row.message = job.message
        row.job = job
      }, signal, error => {
        if (!disposed && !signal.aborted) {
          const row = rows.value[index]
          if (row) row.error = `状态暂时无法刷新，保留最近一次确认的进度：${errorText(error)}`
        }
      })
      if (disposed || signal.aborted) return ''
      const row = rows.value[index]
      if (row) {
        row.status = completed.job.status
        row.message = completed.job.message
        row.result = completed.result
      }
      return completed.result.document_id
    }))
    if (disposed || signal.aborted) return
    const completedIds = settled.flatMap(item => item.status === 'fulfilled' && item.value ? [item.value] : [])
    settled.forEach((item, index) => {
      if (item.status !== 'rejected') return
      const row = rows.value[index]
      if (row) {
        row.status = 'FAILED'
        row.error = errorText(item.reason)
      }
    })
    if (completedIds.length) emit('completed', completedIds)
    if (completedIds.length === response.items.length) {
      ElMessage.success(`已自动分类并生成 ${completedIds.length} 条知识草稿，请审核后发布`)
    } else {
      ElMessage.warning(`已生成 ${completedIds.length} 条草稿，${response.items.length - completedIds.length} 条失败`)
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    if (!disposed) submitting.value = false
  }
}

async function cancelRow(index: number) {
  const row = rows.value[index]
  if (!row?.job) return
  try { const { data } = await api.post<Job>(`/jobs/${row.job!.id}/cancel`); row.job = data; row.status = data.status; row.message = data.message }
  catch (error) { row.error = errorText(error) }
}

async function retryRow(index: number) {
  const row = rows.value[index]
  if (!row?.job) return
  try {
    const { data } = await api.post<Job>(`/jobs/${row.job!.id}/retry`)
    row.job = data; row.status = data.status; row.message = data.message; row.error = undefined
    const completed = await waitForKnowledgeRoutingJob(data, job => { if (!disposed) { row.job = job; row.status = job.status; row.message = job.message } }, batchAbortController?.signal, error => {
      if (!disposed) row.error = `状态暂时无法刷新，保留最近一次确认的进度：${errorText(error)}`
    })
    if (disposed) return
    row.job = completed.job; row.status = completed.job.status; row.message = completed.job.message; row.result = completed.result
    emit('completed', [completed.result.document_id])
  }
  catch (error) { row.error = errorText(error) }
}

watch(() => props.modelValue, visible => {
  if (visible) {
    reset()
    void loadModels()
  }
})
onBeforeUnmount(() => { disposed = true; batchAbortController?.abort() })
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="AI 智能导入 Markdown 知识"
    width="880px"
    destroy-on-close
    :close-on-click-modal="!submitting"
    :close-on-press-escape="!submitting"
    :show-close="!submitting"
    @close="close"
  >
    <el-alert
      title="模型会逐文件读取脱敏、限长的 Markdown 摘要，从当前有效知识目录中选择最合适的叶子分类。导入结果始终是草稿，不会绕过人工审核发布。"
      type="info"
      :closable="false"
      style="margin-bottom:16px"
    />
    <el-form label-width="120px">
      <el-form-item label="Markdown 文件">
        <div>
          <input
            data-testid="knowledge-routing-files"
            type="file"
            accept=".md,.markdown,text/markdown"
            multiple
            :disabled="submitting"
            @change="selectFiles"
          />
          <div class="muted routing-hint">
            已选择 {{ files.length }} 个文件；每批 1–20 个，仅支持 .md/.markdown。
          </div>
        </div>
      </el-form-item>
      <el-form-item label="分类模型">
        <el-select
          v-model="form.modelProfileId"
          data-testid="knowledge-routing-model"
          :loading="loadingModels"
          :disabled="submitting"
          placeholder="选择启用的 Chat 模型"
          style="width:100%"
          @change="selectModel"
        >
          <el-option
            v-for="model in eligibleModels"
            :key="model.id"
            :label="`${model.name} · ${model.model_name}${model.is_active ? '（当前）' : ''}`"
            :value="model.id"
          />
        </el-select>
        <div v-if="!loadingModels && !eligibleModels.length" class="empty-model-hint">
          未找到可用的真实 Chat 模型，请先到“系统设置”配置并启用。
        </div>
      </el-form-item>
      <div class="form-grid">
        <el-form-item label="可信级别">
          <el-select v-model="form.trustLevel" :disabled="submitting">
            <el-option label="高" value="HIGH" />
            <el-option label="中" value="MEDIUM" />
            <el-option label="低" value="LOW" />
          </el-select>
        </el-form-item>
        <el-form-item label="可见级别">
          <el-select v-model="form.confidentiality" :disabled="submitting">
            <el-option label="内部" value="INTERNAL" />
            <el-option label="受限" value="RESTRICTED" />
            <el-option label="公开" value="PUBLIC" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item v-if="requiresEgressConsent" label="模型数据出站">
        <el-checkbox
          v-model="form.consentModelEgress"
          data-testid="knowledge-routing-egress-consent"
          :disabled="submitting"
        >
          我确认脱敏、限长的 Markdown 内容片段与标题大纲可以发送到所选模型 API；未脱敏原文不会发送
        </el-checkbox>
      </el-form-item>
    </el-form>

    <el-table
      v-if="rows.length"
      data-testid="knowledge-routing-results"
      :data="rows"
      stripe
      max-height="340"
    >
      <el-table-column prop="relativePath" label="文件" min-width="170" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模型进度" min-width="250">
        <template #default="scope"><ModelTaskProgress v-if="scope.row.job" :job="scope.row.job" compact :test-id="`knowledge-routing-progress-${scope.$index}`" /></template>
      </el-table-column>
      <el-table-column label="自动分类" min-width="210">
        <template #default="scope">
          <span :data-testid="`knowledge-routing-category-${scope.$index}`">
            {{ scope.row.result?.category_path || '—' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="90">
        <template #default="scope">{{ confidenceText(scope.row.result?.confidence) }}</template>
      </el-table-column>
      <el-table-column label="审核" width="80">
        <template #default="scope">
          <el-tag v-if="scope.row.result" type="info">草稿</el-tag>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="说明" min-width="220" show-overflow-tooltip>
        <template #default="scope">
          <span :class="{ 'error-text': scope.row.error }">
            {{ scope.row.error || scope.row.result?.rationale || scope.row.message || '—' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="控制" width="105">
        <template #default="{ row, $index }">
          <el-button v-if="row.job && ['QUEUED','RUNNING'].includes(row.job.status)" link type="warning" @click="cancelRow($index)">取消</el-button>
          <el-button v-if="row.job && ['FAILED','CANCELLED','DEAD_LETTER'].includes(row.job.status)" link type="primary" @click="retryRow($index)">重试</el-button>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button :disabled="submitting" @click="close">关闭</el-button>
      <el-button
        data-testid="knowledge-routing-submit"
        type="primary"
        :loading="submitting"
        :disabled="loadingModels || !eligibleModels.length"
        @click="submit"
      >
        上传、分类并生成草稿
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.routing-hint { margin-top: 6px; }
.empty-model-hint { margin-top: 6px; color: #e6a23c; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; column-gap: 14px; }
.error-text { color: #f56c6c; }
@media (max-width: 720px) { .form-grid { grid-template-columns: 1fr; } }
</style>
