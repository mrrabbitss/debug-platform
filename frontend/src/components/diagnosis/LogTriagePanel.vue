<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import type {
  Artifact,
  LogEvidenceBucket,
  LogEvidenceHit,
  LogEvidenceHitPage,
  LogEvidenceItem,
  LogEvidencePage,
  LogTriageRun,
  Job
} from '../../types'
import PlanningTracePanel from './PlanningTracePanel.vue'
import ModelTaskProgress from '../common/ModelTaskProgress.vue'
import { clearTaskJobBookmark, readTaskJobBookmark, saveTaskJobBookmark, shouldClearTaskJobBookmark } from '../../composables/taskJobBookmark'

const props = defineProps<{
  caseId: string
  artifacts: Artifact[]
  canEdit: boolean
  modelEgressApproved: boolean
  principalId?: string
}>()

const emit = defineEmits<{
  openSource: [payload: { artifactId: string, sourceFile: string, line: number }]
}>()

const selectedArtifactId = ref('')
const triage = ref<LogTriageRun | null>(null)
const activeJob = ref<Job | null>(null)
const loading = ref(false)
const submitting = ref(false)
const pollError = ref('')
const activeBucket = ref<LogEvidenceBucket>('LLM_RELEVANT')
const pages = reactive<Record<LogEvidenceBucket, LogEvidencePage>>({
  LLM_RELEVANT: { triage_run_id: '', bucket: 'LLM_RELEVANT', total: 0, offset: 0, limit: 100, items: [] },
  METHOD_REQUIRED: { triage_run_id: '', bucket: 'METHOD_REQUIRED', total: 0, offset: 0, limit: 100, items: [] },
  OTHER: { triage_run_id: '', bucket: 'OTHER', total: 0, offset: 0, limit: 100, items: [] }
})
const occurrencePages = reactive<Record<string, LogEvidenceHitPage>>({})
const occurrenceLoading = reactive<Record<string, boolean>>({})
let triageTimer: number | undefined
let jobTimer: number | undefined
let epoch = 0
let disposed = false

const parsedArtifacts = computed(() => props.artifacts.filter(item => item.status === 'PARSED'))
const methodDocuments = computed(() => triage.value?.method_coverage?.documents || [])
const methodUsage = computed(() => triage.value?.summary?.method_usage || methodDocuments.value)
const plannerFailure = computed(() => triage.value?.summary?.planner_failure || null)
const toolCalls = computed(() => triage.value?.plan?.tool_calls || [])
const planHypotheses = computed(() => triage.value?.plan?.hypotheses || [])
const screeningSteps = computed(() => triage.value?.plan?.screening_steps || [])
const isDemoSnapshot = computed(() => Boolean(
  triage.value?.summary?.demo_snapshot || triage.value?.plan?.demo_snapshot
))
const selectedPatternCount = computed(() => Number(
  triage.value?.summary?.selected_pattern_count
  ?? triage.value?.plan?.selected_pattern_ids?.length
  ?? 0
))
const additionalKeywordCount = computed(() => Number(
  triage.value?.summary?.additional_keyword_count
  ?? triage.value?.plan?.additional_keywords?.length
  ?? 0
))
const selectedPatternCandidateCount = computed(() => Number(
  triage.value?.plan?.selected_pattern_candidate_count
  ?? selectedPatternCount.value
))
const selectionWasTruncated = computed(() => Boolean(
  triage.value?.plan?.selected_pattern_selection_truncated
  || triage.value?.plan?.additional_keyword_selection_truncated
))

function bucketLabel(bucket: LogEvidenceBucket): string {
  const labels: Record<LogEvidenceBucket, string> = {
    LLM_RELEVANT: '① LLM 判断相关',
    METHOD_REQUIRED: '② 方法文档强制检查',
    OTHER: '③ 其他日志事件'
  }
  return labels[bucket]
}

function triageScope(artifactId = selectedArtifactId.value) {
  return `case:${props.caseId}:log-triage:${artifactId}`
}

function scheduleTriage(token = epoch) {
  if (triageTimer !== undefined) window.clearTimeout(triageTimer)
  triageTimer = window.setTimeout(() => void loadTriage(token), 1400)
}

function scheduleJob(jobId: string, token = epoch) {
  if (jobTimer !== undefined) window.clearTimeout(jobTimer)
  jobTimer = window.setTimeout(() => void pollJob(jobId, token), 1400)
}

function trackTriageJob(job: Job, artifactId = selectedArtifactId.value, token?: number) {
  const effectiveToken = token ?? ++epoch
  activeJob.value = job
  saveTaskJobBookmark(props.principalId, triageScope(artifactId), job.id)
  pollError.value = ''
  scheduleJob(job.id, effectiveToken)
}

async function pollJob(jobId: string, token = epoch) {
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`)
    if (disposed || token !== epoch) return
    activeJob.value = data
    pollError.value = ''
    if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) {
      scheduleJob(jobId, token)
      return
    }
    await loadTriage(token)
  } catch (error: any) {
    if (disposed || token !== epoch) return
    // Preserve the last server-confirmed progress until the user can refresh or connectivity returns.
    pollError.value = error?.response?.data?.detail || error?.message || '任务状态暂时无法刷新，页面保留最近一次确认的进度。'
    if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(props.principalId, triageScope())
    if (!shouldClearTaskJobBookmark(error) && activeJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value.status)) scheduleJob(jobId, token)
  }
}

async function loadTriage(token = epoch) {
  const artifactId = selectedArtifactId.value
  if (!artifactId) {
    triage.value = null
    return
  }
  loading.value = !triage.value
  try {
    const { data } = await api.get<LogTriageRun | null>(`/cases/${props.caseId}/log-triage`, {
      params: { artifact_id: artifactId }
    })
    if (disposed || token !== epoch || artifactId !== selectedArtifactId.value) return
    if (triage.value?.id !== data?.id) {
      for (const key of Object.keys(occurrencePages)) delete occurrencePages[key]
    }
    triage.value = data
    if (data?.status === 'COMPLETED') {
      await Promise.all((['LLM_RELEVANT', 'METHOD_REQUIRED', 'OTHER'] as LogEvidenceBucket[])
        .map(bucket => loadEvidence(bucket, 1)))
    } else if (data && ['QUEUED', 'RUNNING'].includes(data.status)) {
      if (!activeJob.value || !['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(activeJob.value.status)) scheduleTriage(token)
    }
  } catch (error: any) {
    if (disposed || token !== epoch || artifactId !== selectedArtifactId.value) return
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志规划状态加载失败')
  } finally {
    if (!disposed && token === epoch && artifactId === selectedArtifactId.value) loading.value = false
  }
}

async function startTriage() {
  if (!selectedArtifactId.value) return ElMessage.warning('请选择已解析日志')
  if (!props.modelEgressApproved) return ElMessage.warning('请先在案例概览确认模型出站授权')
  const artifactId = selectedArtifactId.value
  const token = ++epoch
  submitting.value = true
  try {
    const { data } = await api.post<{ job: Job }>(`/cases/${props.caseId}/artifacts/${artifactId}/triage`)
    if (disposed || token !== epoch || artifactId !== selectedArtifactId.value) return
    trackTriageJob(data.job, artifactId, token)
    ElMessage.success('LLM 日志规划已进入后台任务')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志规划启动失败')
  } finally {
    submitting.value = false
  }
}

async function restoreTriageJob(token = epoch) {
  const artifactId = selectedArtifactId.value
  const jobId = readTaskJobBookmark(props.principalId, triageScope(artifactId))
  if (!artifactId || !jobId) return
  try {
    const { data } = await api.get<Job>(`/jobs/${jobId}`)
    if (disposed || token !== epoch || artifactId !== selectedArtifactId.value) return
    activeJob.value = data
    if (['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(data.status)) trackTriageJob(data, artifactId, token)
  } catch (error) {
    if (shouldClearTaskJobBookmark(error)) clearTaskJobBookmark(props.principalId, triageScope(artifactId))
  }
}

async function cancelTriage() {
  if (!activeJob.value) return
  const artifactId = selectedArtifactId.value
  const jobId = activeJob.value.id
  const token = epoch
  try {
    const { data } = await api.post<Job>(`/jobs/${jobId}/cancel`)
    if (disposed || token !== epoch || artifactId !== selectedArtifactId.value) return
    trackTriageJob(data, artifactId)
  } catch (error: any) { pollError.value = error?.response?.data?.detail || error?.message || '取消请求失败' }
}

async function retryTriage() {
  if (!activeJob.value) return
  const artifactId = selectedArtifactId.value
  const jobId = activeJob.value.id
  const token = epoch
  try {
    const { data } = await api.post<Job>(`/jobs/${jobId}/retry`)
    if (disposed || token !== epoch || artifactId !== selectedArtifactId.value) return
    trackTriageJob(data, artifactId)
  } catch (error: any) { pollError.value = error?.response?.data?.detail || error?.message || '重试任务创建失败' }
}

function dismissTriageJob() {
  ++epoch
  if (jobTimer !== undefined) window.clearTimeout(jobTimer)
  if (triageTimer !== undefined) window.clearTimeout(triageTimer)
  clearTaskJobBookmark(props.principalId, triageScope())
  activeJob.value = null
  pollError.value = ''
}

async function loadEvidence(bucket: LogEvidenceBucket, page: number) {
  if (!triage.value || triage.value.status !== 'COMPLETED') return
  const target = pages[bucket]
  const offset = (page - 1) * target.limit
  const { data } = await api.get<LogEvidencePage>(
    `/cases/${props.caseId}/log-triage/${triage.value.id}/evidence`,
    { params: { bucket, offset, limit: target.limit } }
  )
  pages[bucket] = data
}

function currentPage(bucket: LogEvidenceBucket): number {
  return Math.floor(pages[bucket].offset / pages[bucket].limit) + 1
}

function openItem(item: LogEvidenceItem) {
  emit('openSource', {
    artifactId: selectedArtifactId.value,
    sourceFile: item.source_file,
    line: item.line_start
  })
}

function openOccurrence(item: LogEvidenceHit) {
  emit('openSource', {
    artifactId: selectedArtifactId.value,
    sourceFile: item.source_file,
    line: item.line_start
  })
}

function methodSource(item: LogEvidenceItem): string {
  const source = item.metadata?.method_source || {}
  return [source.document_title, source.heading].filter(Boolean).join(' / ')
    || (item.bucket === 'LLM_RELEVANT' ? 'LLM 规划' : '诊断 Skill')
}

async function loadOccurrences(item: LogEvidenceItem, page = 1) {
  if (!triage.value || item.bucket === 'OTHER') return
  const limit = occurrencePages[item.id]?.limit || 100
  occurrenceLoading[item.id] = true
  try {
    const { data } = await api.get<LogEvidenceHitPage>(
      `/cases/${props.caseId}/log-triage/${triage.value.id}/evidence/${item.id}/occurrences`,
      { params: { offset: (page - 1) * limit, limit } }
    )
    occurrencePages[item.id] = data
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '命中位置加载失败')
  } finally {
    occurrenceLoading[item.id] = false
  }
}

function occurrenceCurrentPage(matchId: string): number {
  const page = occurrencePages[matchId]
  return page ? Math.floor(page.offset / page.limit) + 1 : 1
}

function handleExpandChange(
  item: LogEvidenceItem,
  expandedRows: LogEvidenceItem[] | boolean
) {
  const expanded = typeof expandedRows === 'boolean'
    ? expandedRows
    : expandedRows.some(row => row.id === item.id)
  if (expanded && !occurrencePages[item.id]) {
    void loadOccurrences(item)
  }
}

watch(parsedArtifacts, (items) => {
  if (!items.some(item => item.id === selectedArtifactId.value)) {
    selectedArtifactId.value = items[0]?.id || ''
  }
}, { immediate: true })

watch(selectedArtifactId, () => {
  ++epoch
  if (triageTimer !== undefined) window.clearTimeout(triageTimer)
  if (jobTimer !== undefined) window.clearTimeout(jobTimer)
  triage.value = null
  activeJob.value = null
  pollError.value = ''
  const token = epoch
  void loadTriage(token)
  void restoreTriageJob(token)
})

watch(() => props.principalId, () => { if (selectedArtifactId.value) void restoreTriageJob(epoch) })
onMounted(() => { void loadTriage(epoch); void restoreTriageJob(epoch) })
onBeforeUnmount(() => {
  disposed = true
  ++epoch
  if (triageTimer !== undefined) window.clearTimeout(triageTimer)
  if (jobTimer !== undefined) window.clearTimeout(jobTimer)
})
</script>

<template>
  <div v-loading="loading" data-testid="log-triage-panel">
    <el-alert
      v-if="!modelEgressApproved && !isDemoSnapshot"
      type="warning"
      :closable="false"
      title="当前案例未授权向模型发送问题描述与方法文档；请先在案例概览开启授权。日志正文始终只在本地扫描。"
      style="margin-bottom:12px"
    />
    <div class="triage-toolbar">
      <el-select v-model="selectedArtifactId" placeholder="选择已解析日志" style="width:320px">
        <el-option v-for="item in parsedArtifacts" :key="item.id" :label="item.original_name" :value="item.id" />
      </el-select>
      <el-button type="primary" :disabled="!canEdit || !selectedArtifactId || ['QUEUED','RUNNING','CANCEL_REQUESTED'].includes(activeJob?.status || '')" :loading="submitting" @click="startTriage">
        {{ isDemoSnapshot ? '使用当前模型重新执行 LLM 规划' : triage ? '重新执行 LLM 规划' : '启动 LLM 日志规划' }}
      </el-button>
      <el-tag v-if="triage" data-testid="log-triage-status" :type="triage.status === 'COMPLETED' ? 'success' : triage.status === 'FAILED' ? 'danger' : 'primary'">{{ triage.status }}</el-tag>
      <span v-if="triage" class="muted">{{ triage.model_name || '确定性回退' }} · {{ triage.plan?.planner_mode || '等待规划' }}</span>
      <el-button v-if="activeJob && ['QUEUED','RUNNING'].includes(activeJob.status)" type="warning" link @click="cancelTriage">取消规划</el-button>
      <el-button v-if="activeJob && ['FAILED','CANCELLED','DEAD_LETTER'].includes(activeJob.status)" type="primary" link @click="retryTriage">重试规划</el-button>
    </div>

    <ModelTaskProgress v-if="activeJob" :job="activeJob" compact test-id="log-triage-model-progress">
      <template #actions><el-button v-if="['COMPLETED','FAILED','CANCELLED','DEAD_LETTER'].includes(activeJob.status)" size="small" @click="dismissTriageJob">关闭记录</el-button></template>
    </ModelTaskProgress>
    <el-alert v-if="pollError" type="warning" :title="pollError" :closable="false" style="margin:12px 0" />

    <el-empty v-if="!selectedArtifactId" description="暂无已解析日志" />
    <el-empty v-else-if="!triage" description="尚未执行日志规划" />
    <template v-else>
      <el-alert v-if="triage.error_message" type="error" :closable="false" :title="triage.error_message" style="margin:12px 0" />
      <el-alert
        v-if="isDemoSnapshot"
        type="info"
        :closable="false"
        data-testid="demo-triage-snapshot"
        title="真实 GLM-5.2 历史筛查结果 · 导入时未再次调用模型"
        description="模型选择的 Pattern、补充关键词、方法阅读状态和真实 Token/耗时来自历史成功运行；命中已在当前合成日志上重新定位。每组默认折叠，展开后所有位置都可跳转原始行。"
        style="margin:12px 0"
      />
      <el-alert
        v-else-if="triage.summary?.planner_fallback"
        type="warning"
        :closable="false"
        :title="`LLM 规划未通过，已完成确定性回退${plannerFailure?.code ? `（${plannerFailure.code}）` : ''}`"
        :description="`${plannerFailure?.message || triage.summary?.planner_error_type || '模型请求或结构校验失败'}${plannerFailure?.upstream_error_type ? `；上游错误：${plannerFailure.upstream_error_type}` : ''}${plannerFailure?.field_path ? `；字段：${plannerFailure.field_path}` : ''}${triage.summary?.planner_thinking_mode ? `；Thinking：${triage.summary.planner_thinking_mode}` : ''}${triage.summary?.planner_finish_reason ? `；模型停止原因：${triage.summary.planner_finish_reason}` : ''}`"
        style="margin:12px 0"
      />
      <el-alert
        v-else-if="triage.status === 'COMPLETED'"
        type="success"
        :closable="false"
        :title="`LLM 日志规划已通过校验 · ${triage.plan?.agent_mode || '受控规划'}`"
        :description="`${triage.summary?.planner_thinking_mode ? `本阶段 Thinking：${triage.summary.planner_thinking_mode}；` : ''}${triage.summary?.planner_finish_reason ? `模型停止原因：${triage.summary.planner_finish_reason}` : '全部方法规则已由本地扫描器完成检查。'}`"
        style="margin:12px 0"
      />
      <el-alert
        v-if="selectionWasTruncated"
        type="info"
        :closable="false"
        :title="`模型返回 ${selectedPatternCandidateCount} 条候选规则；系统按相关度保留前 ${selectedPatternCount} 条，其余规则仍在“方法文档强制检查”中扫描。`"
        style="margin:12px 0"
      />
      <el-alert
        v-if="triage.status === 'COMPLETED' && triage.summary?.exact_hit_count === undefined"
        type="info"
        :closable="false"
        title="这是升级前生成的筛查结果；请点击“重新执行 LLM 规划”以生成全部逐行命中位置。"
        style="margin:12px 0"
      />
      <el-row :gutter="14" style="margin:14px 0">
        <el-col :span="6"><el-card shadow="never"><div class="muted">强制阅读方法</div><strong>{{ triage.method_coverage?.document_count || 0 }}</strong><div class="muted">规则 {{ triage.method_coverage?.compiled_pattern_count || 0 }}</div></el-card></el-col>
        <el-col :span="6"><el-card shadow="never"><div class="muted">LLM 选择规则</div><strong>{{ selectedPatternCount }}</strong><div class="muted">已规划候选，不等于实际命中 · 补充关键词 {{ additionalKeywordCount }}</div></el-card></el-col>
        <el-col :span="6"><el-card shadow="never"><div class="muted">实际命中事件</div><strong>{{ triage.summary?.matched_events || 0 }}</strong><div class="muted">总事件 {{ triage.summary?.total_events || 0 }}</div></el-card></el-col>
        <el-col :span="6"><el-card shadow="never"><div class="muted">其他事件</div><strong>{{ triage.summary?.other_events || 0 }}</strong><div class="muted">不会冒充关键证据</div></el-card></el-col>
      </el-row>

      <el-collapse style="margin-bottom:14px">
        <el-collapse-item title="调用的文档、方法与 LLM 规划摘要" name="methods">
          <el-table :data="methodUsage" size="small" max-height="320">
            <el-table-column prop="title" label="文档" min-width="240" />
            <el-table-column prop="source_type" label="类型" width="150" />
            <el-table-column prop="device_type" label="设备" width="80" />
            <el-table-column prop="version" label="版本" width="80" />
            <el-table-column prop="read_status" label="读取状态" width="170" />
            <el-table-column prop="selected_pattern_count" label="LLM 选择" width="100" />
            <el-table-column prop="matched_pattern_count" label="命中规则" width="100" />
            <el-table-column prop="occurrence_count" label="命中次数" width="100" />
          </el-table>
          <h4>只读工具调用</h4>
          <el-table :data="toolCalls" size="small" max-height="240">
            <el-table-column prop="round" label="轮次" width="70" />
            <el-table-column prop="tool_name" label="工具" min-width="210" />
            <el-table-column prop="invoked_by" label="调度方" width="170" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column prop="returned" label="返回" width="90" />
          </el-table>
          <h4>假设</h4><ul><li v-for="item in planHypotheses" :key="item">{{ item }}</li></ul>
          <h4>筛查步骤</h4><ol><li v-for="item in screeningSteps" :key="item">{{ item }}</li></ol>
        </el-collapse-item>
      </el-collapse>

      <details class="technical-details"><summary>技术轨迹与执行记录</summary>
    <PlanningTracePanel
        :case-id="caseId"
        :run-id="triage.agent_run_id"
        operation="log_triage_planning"
        :title="isDemoSnapshot ? '真实 GLM-5.2 历史日志筛查轨迹（脱敏快照）' : '日志 LLM Planning 轨迹'"
        style="margin-bottom:14px"
      />
    </details>

      <el-tabs v-if="triage.status === 'COMPLETED'" v-model="activeBucket" type="border-card">
        <el-tab-pane
          v-for="bucket in (['LLM_RELEVANT', 'METHOD_REQUIRED', 'OTHER'] as LogEvidenceBucket[])"
          :key="bucket"
          :name="bucket"
          :label="`${bucketLabel(bucket)} (${pages[bucket].total})`"
        >
          <el-table
            :data="pages[bucket].items"
            height="520"
            row-key="id"
            @expand-change="handleExpandChange"
          >
            <el-table-column v-if="bucket !== 'OTHER'" type="expand" width="48">
              <template #default="scope">
                <div class="occurrence-panel" v-loading="occurrenceLoading[scope.row.id]">
                  <div class="occurrence-heading">
                    全部命中位置（{{ occurrencePages[scope.row.id]?.total ?? scope.row.occurrence_count }}）
                  </div>
                  <el-table :data="occurrencePages[scope.row.id]?.items || []" size="small" max-height="330">
                    <el-table-column prop="source_file" label="文件" min-width="190" show-overflow-tooltip />
                    <el-table-column prop="line_start" label="行号" width="90" />
                    <el-table-column prop="timestamp" label="时间" width="185" show-overflow-tooltip />
                    <el-table-column prop="message" label="日志内容" min-width="430" show-overflow-tooltip />
                    <el-table-column label="操作" width="100">
                      <template #default="hitScope">
                        <el-button link type="primary" @click.stop="openOccurrence(hitScope.row as LogEvidenceHit)">跳转</el-button>
                      </template>
                    </el-table-column>
                  </el-table>
                  <el-pagination
                    v-if="(occurrencePages[scope.row.id]?.total || 0) > (occurrencePages[scope.row.id]?.limit || 100)"
                    :current-page="occurrenceCurrentPage(scope.row.id)"
                    :page-size="occurrencePages[scope.row.id]?.limit || 100"
                    :total="occurrencePages[scope.row.id]?.total || 0"
                    layout="total, prev, pager, next, jumper"
                    style="margin-top:10px;justify-content:flex-end"
                    @current-change="(page:number) => loadOccurrences(scope.row as LogEvidenceItem, page)"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="relevance_score" label="相关度" width="90" />
            <el-table-column prop="occurrence_count" label="次数" width="80" />
            <el-table-column prop="pattern_text" label="命中规则" min-width="210" show-overflow-tooltip />
            <el-table-column prop="meaning" label="Skill 含义" min-width="260" show-overflow-tooltip />
            <el-table-column prop="message" label="日志证据" min-width="420" show-overflow-tooltip />
            <el-table-column prop="source_file" label="文件" min-width="180" show-overflow-tooltip />
            <el-table-column prop="line_start" :label="bucket === 'OTHER' ? '行号' : '首行'" width="90" />
            <el-table-column v-if="bucket !== 'OTHER'" label="Skill 来源" min-width="230" show-overflow-tooltip><template #default="scope">{{ methodSource(scope.row as LogEvidenceItem) }}</template></el-table-column>
            <el-table-column prop="reason" label="排序理由" min-width="230" show-overflow-tooltip />
            <el-table-column label="操作" width="110">
              <template #default="scope"><el-button link type="primary" @click.stop="openItem(scope.row as LogEvidenceItem)">{{ bucket === 'OTHER' ? '跳转' : '首个位置' }}</el-button></template>
            </el-table-column>
          </el-table>
          <el-pagination
            :current-page="currentPage(bucket)"
            :page-size="pages[bucket].limit"
            :total="pages[bucket].total"
            layout="total, prev, pager, next, jumper"
            style="margin-top:12px;justify-content:flex-end"
            @current-change="(page:number) => loadEvidence(bucket, page)"
          />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<style scoped>
.triage-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.muted { color: #64748b; font-size: 12px; }
.occurrence-panel { padding: 10px 18px 16px; background: #f8fafc; }
.occurrence-heading { color: #334155; font-weight: 600; margin-bottom: 8px; }
strong { font-size: 24px; }
</style>
