<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ModelTaskProgress from '../common/ModelTaskProgress.vue'
import { confirmBundledSkill, getBundledSkillOperation, previewBundledSkill, retryBundledSkillJob } from '../../api/bundledSkillImport'
import { failure } from '../../composables/useWorkbench'
import type { BundledSkillManifestEntry, BundledSkillOperationState, BundledSkillPreview } from '../../types/bundledSkillImport'

const props = defineProps<{
  modelValue: boolean
  operationId?: string
  roles: Record<string, string>
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  changed: []
}>()

const preview = ref<BundledSkillPreview | null>(null)
const state = ref<BundledSkillOperationState | null>(null)
const currentOperationId = ref('')
const busy = ref(false)
const reviewed = ref(false)
const embeddingApproved = ref(true)
const error = ref('')
const pollError = ref('')
const completedOperationId = ref('')
const pendingOperationCheck = ref(false)
let pollTimer: number | undefined
let disposed = false

const activeJob = computed(() => state.value?.job || null)
const jobIsActive = computed(() => ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value?.status || ''))
const jobFailed = computed(() => ['FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(activeJob.value?.status || ''))
const failureMessage = computed(() => {
  if (!jobFailed.value) return ''
  return activeJob.value?.error_message || activeJob.value?.dead_letter_reason || activeJob.value?.message || state.value?.operation.message || '导入任务未完成，请刷新或重试。'
})
const manifestHasFreshSixFileBundle = computed(() => Boolean(preview.value) && preview.value!.manifest.length === 6 && preview.value!.manifest.every(item => item.disposition === 'ADD'))
const canConfirm = computed(() => Boolean(preview.value?.can_confirm && manifestHasFreshSixFileBundle.value && reviewed.value && embeddingApproved.value && !busy.value))
const stateLabel = computed(() => ({
  APPROVED: '已审批，等待导入', BUILDING: '正在导入', PUBLISHED: '已发布', FAILED: '导入失败，现有知识已保留', CANCELLED: '导入已取消'
} as Record<string, string>)[state.value?.operation.status || ''] || state.value?.operation.status || '')
const previewSummary = computed(() => {
  if (!preview.value) return ''
  const counts = preview.value.manifest.reduce((summary, item) => {
    summary[item.disposition]++
    return summary
  }, { ADD: 0, EXISTS: 0, CONFLICT: 0 })
  return `本次核对 ${preview.value.manifest.length} 份文件：可新增 ${counts.ADD} 份，已有 ${counts.EXISTS} 份，冲突 ${counts.CONFLICT} 份。`
})
const preservedSummary = computed(() => {
  const labels: Record<string, string> = {
    existing_knowledge: '现有共享知识', existing_skills: '现有 Skill', drafts: '知识草稿',
    global_memories: '全局经验', existing_templates: '已设置的报告模板', cases: '案例', reports: '报告'
  }
  const values = [...new Set((preview.value?.preserved || []).map(item => labels[item]).filter(Boolean))]
  return values.length ? `保留范围：${values.join('、')}。` : '保留范围：现有共享知识、Skill、案例、报告和已设置的报告模板。'
})

function newOperationId() {
  return `bundled-skill-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

function clearPoll() {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

function close() {
  if (!busy.value) emit('update:modelValue', false)
}

function isCompleted() {
  return state.value?.operation.status === 'PUBLISHED' || activeJob.value?.status === 'COMPLETED'
}

function schedulePoll() {
  clearPoll()
  if (!disposed && jobIsActive.value && currentOperationId.value) {
    pollTimer = window.setTimeout(() => void refresh(), 1200)
  }
}

function announceCompletion() {
  if (!isCompleted() || !currentOperationId.value || completedOperationId.value === currentOperationId.value) return
  completedOperationId.value = currentOperationId.value
  emit('changed')
}

async function refresh() {
  if (busy.value || !currentOperationId.value) return
  busy.value = true
  pollError.value = ''
  const checkingUncertainConfirmation = pendingOperationCheck.value
  try {
    state.value = await getBundledSkillOperation(currentOperationId.value)
    currentOperationId.value = state.value.operation.operation_id || currentOperationId.value
    preview.value = null
    reviewed.value = false
    pendingOperationCheck.value = false
    if (checkingUncertainConfirmation) emit('changed')
    announceCompletion()
  } catch (cause) {
    pollError.value = failure(cause)
  } finally {
    busy.value = false
    schedulePoll()
  }
}

async function inspect() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  pollError.value = ''
  reviewed.value = false
  state.value = null
  if (!currentOperationId.value || props.operationId === currentOperationId.value) currentOperationId.value = newOperationId()
  try {
    preview.value = await previewBundledSkill(currentOperationId.value)
    currentOperationId.value = preview.value.operation_id
  } catch (cause) {
    error.value = failure(cause)
  } finally {
    busy.value = false
  }
}

async function confirm() {
  if (!preview.value || !canConfirm.value) return
  busy.value = true
  error.value = ''
  const exact = preview.value
  try {
    state.value = await confirmBundledSkill(exact, embeddingApproved.value)
    currentOperationId.value = state.value.operation.operation_id || exact.operation_id
    preview.value = null
    reviewed.value = false
    pendingOperationCheck.value = false
    emit('changed')
  } catch (cause) {
    preview.value = null
    reviewed.value = false
    pendingOperationCheck.value = true
    error.value = `${failure(cause)}；已保留本次操作，正在读取服务器状态以避免重复提交。`
  } finally {
    busy.value = false
    if (pendingOperationCheck.value) void refresh()
    else schedulePoll()
  }
}

async function retry() {
  if (busy.value || !activeJob.value?.id) return
  busy.value = true
  error.value = ''
  pollError.value = ''
  try {
    state.value = { ...state.value!, job: await retryBundledSkillJob(activeJob.value.id) }
  } catch (cause) {
    error.value = failure(cause)
  } finally {
    busy.value = false
    schedulePoll()
  }
}

function dispositionLabel(value: BundledSkillManifestEntry['disposition']) {
  return ({ ADD: '新增', EXISTS: '已存在，阻止导入', CONFLICT: '冲突，阻止导入' } as Record<string, string>)[value]
}

function dispositionType(value: BundledSkillManifestEntry['disposition']) {
  return ({ ADD: 'success', EXISTS: 'warning', CONFLICT: 'danger' } as Record<string, 'success' | 'warning' | 'danger'>)[value]
}

watch(() => props.operationId, value => {
  if (!value || value === currentOperationId.value) return
  currentOperationId.value = value
  preview.value = null
  reviewed.value = false
  if (props.modelValue) void refresh()
}, { immediate: true })

watch(() => props.modelValue, open => {
  if (!open) return clearPoll()
  if (props.operationId) {
    currentOperationId.value = props.operationId
    void refresh()
  } else if (pendingOperationCheck.value && currentOperationId.value) {
    void refresh()
  }
})

onBeforeUnmount(() => { disposed = true; clearPoll() })
</script>

<template>
  <el-dialog :model-value="modelValue" title="导入内置组网 Skill" width="min(1080px, 96vw)" :close-on-click-modal="false" @update:model-value="value => value || close()">
    <p class="muted">请核对内置组网 Skill 的完整清单和差异，再决定是否导入。</p>
    <p class="field-hint">仅当完整六文件包全部缺失时才可确认导入。已有同名文件、冲突文件或已删除的原文件都会阻止确认；不会覆盖现有内容，也不会恢复已删除内容。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <el-alert v-if="pollError" :title="pollError" type="warning" :closable="false" class="inline-alert"><el-button link :disabled="busy" @click="refresh">重新刷新状态</el-button></el-alert>

    <template v-if="preview">
      <el-alert :title="preview.message || '请核对完整六文件清单后决定是否导入。'" :type="preview.can_confirm ? 'info' : 'warning'" :closable="false" class="inline-alert" />
      <el-descriptions :column="1" border class="section-card">
        <el-descriptions-item label="操作编号"><span class="preserve-lines">{{ preview.operation_id }}</span></el-descriptions-item>
        <el-descriptions-item label="源文件 SHA-256"><span class="preserve-lines">{{ preview.source_sha256 }}</span></el-descriptions-item>
        <el-descriptions-item label="清单汇总">{{ previewSummary }}</el-descriptions-item>
      </el-descriptions>
      <h3>完整六文件清单</h3>
      <el-table :data="preview.manifest" border>
        <el-table-column label="路径" min-width="270"><template #default="{ row }"><span class="preserve-lines">{{ row.path }}</span></template></el-table-column>
        <el-table-column label="用途" min-width="130"><template #default="{ row }">{{ roles[row.role] || row.role }}</template></el-table-column>
        <el-table-column label="大小" width="100"><template #default="{ row }">{{ row.bytes }} bytes</template></el-table-column>
        <el-table-column label="处理" min-width="160"><template #default="{ row }"><el-tag :type="dispositionType(row.disposition)">{{ dispositionLabel(row.disposition) }}</el-tag><p v-if="row.conflicts?.length" class="field-hint preserve-lines">{{ row.conflicts.join('；') }}</p></template></el-table-column>
      </el-table>
      <section v-for="item in preview.manifest.filter(entry => entry.adaptation_diff)" :key="`${item.path}-diff`" class="revision-entry">
        <strong>{{ item.path === 'SKILL.md' ? '根 Skill 平台适配差异' : `${item.path} 的适配差异` }}</strong>
        <pre class="document-text">{{ item.adaptation_diff }}</pre>
      </section>
      <p v-if="!preview.manifest.some(item => item.adaptation_diff)" class="field-hint">六份内置文件均无需平台适配。</p>
      <p class="field-hint">{{ preservedSummary }} 现有共享知识、Skill、案例、报告和已设置报告模板都不会被清空或替换。</p>
      <div class="review-actions">
        <el-checkbox v-model="reviewed" :disabled="busy">我已核对完整六文件清单、处理结果和全部适配差异</el-checkbox>
        <el-checkbox v-model="embeddingApproved" :disabled="busy">允许使用当前 Embedding 模型重建索引（保留的共享知识和新增组网 Skill 将一并进入新索引）</el-checkbox>
        <el-button type="primary" :disabled="!canConfirm" :loading="busy" @click="confirm">确认导入内置组网 Skill</el-button>
      </div>
    </template>

    <template v-else-if="state">
      <el-alert :title="`${stateLabel} · ${failureMessage || activeJob?.message || state.operation.message || '审批与任务已保存'}`" :type="jobFailed ? 'error' : isCompleted() ? 'success' : 'info'" :closable="false" class="inline-alert" />
      <ModelTaskProgress v-if="activeJob" :job="activeJob" test-id="bundled-skill-import-progress">
        <template #actions>
          <el-button :disabled="busy" @click="refresh">刷新状态</el-button>
          <el-button v-if="jobFailed" type="primary" :disabled="busy" @click="retry">重试导入任务</el-button>
          <el-button :disabled="busy" @click="inspect">重新核对导入清单</el-button>
        </template>
      </ModelTaskProgress>
      <div v-else class="toolbar"><el-button :disabled="busy" @click="refresh">刷新状态</el-button><el-button :disabled="busy" @click="inspect">重新核对导入清单</el-button></div>
      <el-alert v-if="failureMessage" :title="failureMessage" type="error" :closable="false" class="inline-alert" />
    </template>

    <template v-else>
      <el-button type="primary" :loading="busy" @click="inspect">预览内置组网 Skill 导入</el-button>
    </template>
    <template #footer><el-button :disabled="busy" @click="close">关闭</el-button></template>
  </el-dialog>
</template>
