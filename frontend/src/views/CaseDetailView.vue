<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import CaseChatPanel from '../components/diagnosis/CaseChatPanel.vue'
import DiagnosticPlanningPanel from '../components/diagnosis/DiagnosticPlanningPanel.vue'
import LogBrowserPanel from '../components/diagnosis/LogBrowserPanel.vue'
import LogTriagePanel from '../components/diagnosis/LogTriagePanel.vue'
import PlanningTracePanel from '../components/diagnosis/PlanningTracePanel.vue'
import CaseOptionsPanel from '../components/diagnosis/CaseOptionsPanel.vue'
import LibrarySubmissionDialog from '../components/knowledge/LibrarySubmissionDialog.vue'
import { useWorkbench, failure } from '../composables/useWorkbench'
import type {
  Analysis,
  Artifact,
  CaseItem,
  CaseMember,
  CasePermission,
  Job,
  Principal,
  UserDirectoryEntry
} from '../types'

const route = useRoute()
const caseId = String(route.params.id)
const { config, caseCategories, loadConfig, categoryName } = useWorkbench()
const loading = ref(true), loadError = ref(''), submittingLibrary = ref(false), actionBusy = ref(false), consentBusy = ref(false)
let disposed = false
const caseInfo = ref<CaseItem | null>(null)
const artifacts = ref<Artifact[]>([])
const analyses = ref<Analysis[]>([])
const repositories = ref<any[]>([])
const symbols = ref<any[]>([])
const eventStats = ref<{total:number, filtered_total:number, level_counts:Record<string,number>, module_counts:Record<string,number>}>({
  total: 0, filtered_total: 0, level_counts: {}, module_counts: {}
})
const activeTab = ref('overview')
const debugFile = ref<File | null>(null)
const debugSourceDeviceType = ref<'GW' | 'AP' | 'UNKNOWN'>('UNKNOWN')
const debugSourceDeviceRole = ref<'PRIMARY' | 'SECONDARY' | 'UNKNOWN'>('UNKNOWN')
const debugFileInput = ref<HTMLInputElement | null>(null)
const repoFile = ref<File | null>(null)
const currentJob = ref<Job | null>(null)
const jobTimer = ref<number | null>(null)
const diagnosis = ref<any>({})
const reportHtml = ref('')
const reportPreviewAnalysisId = ref('')
const logBrowser = ref<{
  loadManifest: (artifactId: string) => Promise<void>
  openSource: (payload: { artifactId: string, sourceFile: string, line: number }) => Promise<void>
} | null>(null)
const symbolSearch = ref('')
const principal = ref<Principal | null>(null)
const caseAccess = ref<{case_id:string, role:string, permission:CasePermission | null} | null>(null)
const caseMembers = ref<CaseMember[]>([])
const memberDirectory = ref<UserDirectoryEntry[]>([])
const memberForm = reactive({ user_id: '', permission: 'VIEWER' as 'EDITOR' | 'VIEWER' })

const latestAnalysis = computed(() => analyses.value.find(item => item.status === 'COMPLETED'))
const latestAnalysisWithTrace = computed(() => analyses.value.find(item => item.agent_run_id))
const canEditCase = computed(() => {
  if (!principal.value || principal.value.role === 'VIEWER') return false
  return ['OWNER', 'EDITOR', 'SHARED'].includes(caseAccess.value?.permission || '')
})
const canManageMembers = computed(() => (
  principal.value?.role === 'ADMIN' || caseAccess.value?.permission === 'OWNER'
))
const availableMemberUsers = computed(() => {
  const assigned = new Set(caseMembers.value.map(item => item.user_id))
  return memberDirectory.value.filter(item => item.id !== caseInfo.value?.owner_id && !assigned.has(item.id))
})
const canSubmitResult = computed(() => ['ADMIN', 'EXPERT'].includes(principal.value?.role || '') || (principal.value?.role === 'ENGINEER' && caseAccess.value?.permission === 'OWNER'))
const jobRunning = computed(() => !!currentJob.value && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(currentJob.value.status))
const synthesisStatus = computed(() => diagnosis.value.synthesis_status || null)
const synthesisFailure = computed(() => synthesisStatus.value?.failure || null)
const isDemoSnapshot = computed(() => Boolean(
  diagnosis.value?.demo_snapshot
  || diagnosis.value?.synthesis_status?.mode === 'DEMO_VALIDATED_SNAPSHOT'
))
const analysisEvidence = computed<Record<string, any>>(() => {
  if (!latestAnalysis.value) return {}
  try {
    const items = JSON.parse(latestAnalysis.value.evidence_json || '[]')
    return Object.fromEntries(
      (Array.isArray(items) ? items : [])
        .filter(item => item && item.evidence_id)
        .map(item => [String(item.evidence_id), item])
    )
  } catch {
    return {}
  }
})

function evidenceLabel(evidenceId: string): string {
  const item = analysisEvidence.value[evidenceId] || {}
  const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {}
  const sourceFile = item.source_file || item.file_path || metadata.source_file || metadata.file_path
  const lineStart = Number(item.line_start || metadata.line_start || metadata.line_number || 0)
  const lineEnd = Number(item.line_end || metadata.line_end || 0)
  if (sourceFile && lineStart > 0) {
    return lineEnd > 0 && lineEnd !== lineStart
      ? `${sourceFile} - 第 ${lineStart}-${lineEnd} 行`
      : `${sourceFile} - 第 ${lineStart} 行`
  }
  if (sourceFile) return String(sourceFile)
  const derivedLocations = [metadata.udn_location, metadata.mac_location]
    .filter(location => location && typeof location === 'object')
    .map(location => {
      const file = String(location.source_file || '')
      const line = Number(location.line || location.line_start || 0)
      return file && line > 0 ? `${file} - 第 ${line} 行` : file
    })
    .filter(Boolean)
  if (derivedLocations.length) return [...new Set(derivedLocations)].join('、')
  if (item.title) return `《${item.title}》`
  if (item.source_type === 'analysis') return '综合诊断结果'
  return '证据位置未记录'
}

function evidenceLabels(evidenceIds?: string[]): string {
  const labels = [...new Set((evidenceIds || []).map(evidenceLabel))]
  return labels.join('、') || '未关联到可定位证据'
}

const analysisEvidenceLabels = computed<Record<string, string>>(() => Object.fromEntries(
  Object.keys(analysisEvidence.value).map(evidenceId => [evidenceId, evidenceLabel(evidenceId)])
))

function displayDiagnosisText(value: unknown): string {
  let rendered = String(value ?? '')
  for (const [evidenceId, label] of Object.entries(analysisEvidenceLabels.value)) {
    rendered = rendered.split(evidenceId).join(label)
  }
  return rendered.replace(
    /\b(?:EVT|LEM|LEH|LDE|DOC|KCHUNK|LOCALDOC|SYM|COMMIT|MEM|ANL|AREV)-[A-Za-z0-9_.:-]+\b/g,
    '证据位置未记录'
  )
}

const displayedActions = computed(() => (diagnosis.value.recommended_actions || []).map((item: any) => ({
  ...item,
  action: displayDiagnosisText(item.action),
  reason: displayDiagnosisText(item.reason),
  expected_result: displayDiagnosisText(item.expected_result)
})))

async function loadAll() {
  const [caseRes, artifactRes, analysisRes, repoRes] = await Promise.all([
    api.get(`/cases/${caseId}`), api.get(`/cases/${caseId}/artifacts`),
    api.get(`/cases/${caseId}/analyses`), api.get(`/cases/${caseId}/repositories`)
  ])
  caseInfo.value = caseRes.data
  artifacts.value = artifactRes.data
  analyses.value = analysisRes.data
  repositories.value = repoRes.data
  if (latestAnalysis.value) {
    diagnosis.value = JSON.parse(latestAnalysis.value.result_json || '{}')
    if (activeTab.value === 'report') await loadReportPreview(latestAnalysis.value.id)
  } else {
    reportHtml.value = ''
    reportPreviewAnalysisId.value = ''
  }
  await loadAccessContext()
  try { eventStats.value = (await api.get(`/cases/${caseId}/events/stats`)).data } catch { /* Summary counts are optional. */ }
}

async function loadAccessContext() {
  try {
    const [identityResponse, accessResponse] = await Promise.all([
      api.get('/system/me'),
      api.get(`/cases/${caseId}/access`)
    ])
    principal.value = identityResponse.data
    caseAccess.value = accessResponse.data
    if (canManageMembers.value) {
      const [directoryResponse, membersResponse] = await Promise.all([
        api.get('/system/user-directory'),
        api.get(`/cases/${caseId}/members`)
      ])
      memberDirectory.value = directoryResponse.data
      caseMembers.value = membersResponse.data
    } else {
      memberDirectory.value = []
      caseMembers.value = []
    }
  } catch {
    principal.value = null
    caseAccess.value = null
    memberDirectory.value = []
    caseMembers.value = []
  }
}

async function addCaseMember() {
  if (!memberForm.user_id) return ElMessage.warning('请选择用户')
  try {
    await api.put(`/cases/${caseId}/members/${memberForm.user_id}`, {
      permission: memberForm.permission
    })
    memberForm.user_id = ''
    ElMessage.success('案例成员已添加')
    await loadAccessContext()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '添加成员失败')
  }
}

async function updateCaseMember(member: CaseMember, permission: 'EDITOR' | 'VIEWER') {
  try {
    await api.put(`/cases/${caseId}/members/${member.user_id}`, { permission })
    ElMessage.success('成员权限已更新')
    await loadAccessContext()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '更新权限失败')
    await loadAccessContext()
  }
}

async function removeCaseMember(member: CaseMember) {
  try {
    await ElMessageBox.confirm(`确认移除 ${member.display_name || member.username}？`, '移除案例成员', {
      type: 'warning'
    })
    await api.delete(`/cases/${caseId}/members/${member.user_id}`)
    ElMessage.success('案例成员已移除')
    await loadAccessContext()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(error?.response?.data?.detail || error?.message || '移除成员失败')
  }
}

async function initialize() {
  loading.value = true; loadError.value = ''
  try { await Promise.all([loadConfig(), loadAll()]) }
  catch (cause) { loadError.value = failure(cause) }
  finally { loading.value = false }
}

async function uploadDebug() {
  if (actionBusy.value || !canEditCase.value) return
  if (!debugFile.value) return ElMessage.warning('请选择 collectDebuginfo 或日志文件')
  actionBusy.value = true
  try {
    const selectedName = debugFile.value.name
    const data = new FormData()
    data.append('file', debugFile.value)
    data.append('kind', 'debug_log')
    data.append('source_device_type', debugSourceDeviceType.value)
    data.append('source_device_role', debugSourceDeviceRole.value)
    const artifact = (await api.post(`/cases/${caseId}/artifacts`, data)).data
    artifacts.value.unshift(artifact)
    const parseJob = (await api.post(`/cases/${caseId}/artifacts/${artifact.id}/parse`)).data
    debugFile.value = null
    debugSourceDeviceType.value = 'UNKNOWN'
    debugSourceDeviceRole.value = 'UNKNOWN'
    if (debugFileInput.value) debugFileInput.value.value = ''
    const normalized = artifact.original_name !== selectedName
    ElMessage.success(normalized ? `无后缀文件已按 ${artifact.original_name} 上传，正在解析` : '上传完成，正在按内容识别并解析日志')
    watchJob(parseJob)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志上传或解析启动失败')
  } finally { actionBusy.value = false }
}

function selectDebugFile(event: Event) {
  debugFile.value = (event.target as HTMLInputElement).files?.[0] || null
}

async function parseArtifact(artifactId: string) {
  const { data } = await api.post(`/cases/${caseId}/artifacts/${artifactId}/parse`)
  watchJob(data)
}

async function deleteArtifact(artifact: Artifact) {
  try {
    await ElMessageBox.confirm(`确认删除“${artifact.original_name}”及其解析结果？`, '删除日志', { type: 'warning' })
    const { data } = await api.delete(`/cases/${caseId}/artifacts/${artifact.id}`)
    if (data.storage_cleanup_errors?.length) {
      ElMessage.warning('数据库记录已删除，但部分文件清理失败，请检查后端日志')
    } else {
      ElMessage.success('日志及解析结果已删除')
    }
    await loadAll()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(error?.response?.data?.detail || error?.message || '删除失败')
  }
}

async function analyze() {
  if (actionBusy.value || jobRunning.value || !canEditCase.value) return
  actionBusy.value = true
  try {
    const { data } = await api.post(`/cases/${caseId}/analyses`)
    watchJob(data)
    activeTab.value = 'diagnosis'
    await loadAll()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '综合诊断启动失败')
  } finally { actionBusy.value = false }
}

async function updateModelEgress(value: boolean) {
  if (!caseInfo.value || consentBusy.value || !canEditCase.value) return
  consentBusy.value = true
  try {
    const { data } = await api.patch(`/cases/${caseId}`, { model_egress_approved: value })
    caseInfo.value = data
    ElMessage.success(value ? '已授权模型读取问题描述与脱敏证据' : '已关闭模型出站授权')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '模型授权更新失败')
  } finally { consentBusy.value = false }
}

function watchJob(job: Job) {
  currentJob.value = job
  if (jobTimer.value) window.clearTimeout(jobTimer.value)
  const poll = async () => {
    try {
      const { data } = await api.get(`/jobs/${job.id}`)
      if (disposed) return
      currentJob.value = data
      if (['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(data.status)) {
        jobTimer.value = null
        if (data.status === 'COMPLETED') ElMessage.success('任务执行完成')
        else if (data.status === 'CANCELLED') ElMessage.warning('任务已取消')
        else ElMessage.error(data.error_message || '任务失败')
        await loadAll()
        return
      }
      jobTimer.value = window.setTimeout(() => void poll(), 1200)
    } catch (error: any) {
      jobTimer.value = null
      ElMessage.error(error?.response?.data?.detail || error?.message || '任务状态查询失败')
    }
  }
  void poll()
}

async function cancelCurrentJob() {
  if (!currentJob.value) return
  try {
    const { data } = await api.post(`/jobs/${currentJob.value.id}/cancel`)
    currentJob.value = data
    ElMessage.info(data.status === 'CANCELLED' ? '任务已取消' : '已请求取消，正在安全停止')
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '取消任务失败')
  }
}

async function retryCurrentJob() {
  if (!currentJob.value) return
  try {
    const { data } = await api.post(`/jobs/${currentJob.value.id}/retry`)
    ElMessage.info('已创建重试任务')
    watchJob(data)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '重试任务失败')
  }
}

async function openArtifactFiles(artifactId: string) {
  activeTab.value = 'logs'
  await nextTick()
  await logBrowser.value?.loadManifest(artifactId)
}

async function openLogSource(payload: { artifactId: string, sourceFile: string, line: number }) {
  activeTab.value = 'logs'
  await nextTick()
  await logBrowser.value?.openSource(payload)
}

async function openTriageSource(payload: { artifactId: string, sourceFile: string, line: number }) {
  await openLogSource(payload)
}

async function loadReportPreview(analysisId: string) {
  if (reportPreviewAnalysisId.value === analysisId && reportHtml.value) return
  reportHtml.value = (await api.get(`/cases/${caseId}/analyses/${analysisId}/report/preview`)).data
  reportPreviewAnalysisId.value = analysisId
}

async function handleTabChange(name: string | number) {
  if (String(name) !== 'report' || !latestAnalysis.value) return
  try {
    await loadReportPreview(latestAnalysis.value.id)
  } catch (error: any) {
    reportHtml.value = ''
    reportPreviewAnalysisId.value = ''
    ElMessage.error(error?.response?.data?.detail || error?.message || '诊断报告预览加载失败')
  }
}

async function exportReport(format: string) {
  if (!latestAnalysis.value) return ElMessage.warning('请先完成诊断分析')
  const created = (await api.post(`/cases/${caseId}/analyses/${latestAnalysis.value.id}/reports/${format}`)).data
  const response = await api.get(`/reports/${created.report_id}/download`, { responseType: 'blob' })
  const url = URL.createObjectURL(response.data)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `GW_AP_Diagnosis_${caseId}.${format}`
  anchor.click()
  URL.revokeObjectURL(url)
}

async function uploadRepo() {
  if (!repoFile.value) return ElMessage.warning('请选择代码仓库归档或 Git Bundle')
  try {
    const data = new FormData()
    data.append('file', repoFile.value)
    const result = (
      await api.post(
        `/cases/${caseId}/repositories`,
        data,
        { timeout: 15 * 60 * 1000 }
      )
    ).data
    repoFile.value = null
    repositories.value = (await api.get(`/cases/${caseId}/repositories`)).data
    ElMessage.info('仓库已上传，正在后台解压并校验')
    watchJob(result.job)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '仓库上传失败')
  }
}

async function indexRepo(repositoryId: string) {
  const { data } = await api.post(`/repositories/${repositoryId}/index`)
  watchJob(data)
}

async function loadSymbols(repositoryId: string) {
  symbols.value = (await api.get(`/repositories/${repositoryId}/symbols`, { params: { search: symbolSearch.value, limit: 500 } })).data
}

async function runStatic(repositoryId: string) {
  const { data } = await api.post(`/repositories/${repositoryId}/static-analysis`, { tools: ['cppcheck', 'clang-tidy'] })
  watchJob(data)
}

async function suggestPatch(symbolId: string) {
  const { data } = await api.post(`/cases/${caseId}/patch-suggestions`, { symbol_id: symbolId })
  if (data.patch) {
    await navigator.clipboard.writeText(data.patch)
    ElMessage.success('候选补丁已复制到剪贴板；系统未自动修改源码')
  } else {
    ElMessage.info(data.message || '需要人工审查')
  }
}

onMounted(initialize)
onBeforeUnmount(() => {
  disposed = true
  if (jobTimer.value) window.clearTimeout(jobTimer.value)
})
</script>

<template>
  <div v-if="loadError" class="state-panel" role="alert"><h2>案例暂时无法加载</h2><p>{{ loadError }}</p><el-button type="primary" @click="initialize">重新加载</el-button></div>
  <el-skeleton v-else-if="loading" :rows="8" animated />
  <div v-else-if="caseInfo" class="workspace-page case-detail">
    <router-link class="back-link" to="/cases">← 返回故障定位</router-link>
    <div class="toolbar">
      <div style="margin-right:auto">
        <h1 class="page-title" style="margin-bottom:4px">{{ caseInfo.title }}</h1>
        <span class="muted">{{ categoryName(caseInfo.problem_category) }} · {{ caseInfo.device_type }} {{ caseInfo.device_model || '' }} · {{ caseInfo.firmware_version || '固件版本未填' }}</span>
      </div>
      <el-button v-if="canSubmitResult" :disabled="!latestAnalysis" @click="submittingLibrary=true">提交案例与报告</el-button>
      <el-button type="primary" :disabled="!canEditCase || jobRunning || !artifacts.length" :loading="actionBusy" @click="analyze">开始综合诊断</el-button>
    </div>

    <el-alert v-if="caseAccess && !canEditCase" type="info" :closable="false" title="当前账号对这个案例只有只读权限。" style="margin-bottom:14px" />

    <el-alert
      v-if="isDemoSnapshot"
      type="info"
      :closable="false"
      show-icon
      data-testid="demo-case-banner"
      title="合成日志演示 · 真实 GLM-5.2 历史成功结果"
      description="两份日志、两侧 LLM 筛查、2 轮综合规划、真实 Token/耗时与文件行号证据均来自此前通过 wawapii.com 完成的 GLM-5.2 成功运行。导入快照时不会再次出站；凭据、原始 Prompt 和私有方法正文未随快照分发。"
      style="margin-bottom:14px"
    />

    <el-alert v-if="currentJob" :closable="false" :type="['FAILED', 'DEAD_LETTER'].includes(currentJob.status) ? 'error' : currentJob.status === 'CANCELLED' ? 'warning' : 'info'" style="margin-bottom:14px">
      <template #title>{{ currentJob.kind }}：{{ currentJob.message || currentJob.status }}</template>
      <el-progress :percentage="currentJob.progress" :status="['FAILED', 'DEAD_LETTER'].includes(currentJob.status) ? 'exception' : undefined" />
      <pre v-if="currentJob.error_message" class="mono">{{ currentJob.error_message }}</pre>
      <div class="toolbar" style="margin-top:8px">
        <el-button v-if="canEditCase && ['QUEUED', 'RUNNING'].includes(currentJob.status)" size="small" type="warning" @click="cancelCurrentJob">安全取消</el-button>
        <el-button v-if="canEditCase && ['FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(currentJob.status)" size="small" type="primary" @click="retryCurrentJob">重试</el-button>
      </div>
    </el-alert>

    <el-tabs v-model="activeTab" type="border-card" @tab-change="handleTabChange">
      <el-tab-pane label="案例概览" name="overview">
        <div class="case-journey" aria-label="定位流程"><span :class="{done:artifacts.length}">1 · 上传问题日志</span><span :class="{done:latestAnalysis}">2 · 诊断与核对</span><span>3 · 报告与知识沉淀</span></div>
        <CaseOptionsPanel :case-info="caseInfo" :can-edit="canEditCase" :categories="caseCategories" :models="config.models" :selected-model-id="config.model_selection?.profile_id" :model-selection-error="config.model_selection?.error" @updated="value => caseInfo=value" />
        <el-card shadow="never" style="margin:14px 0">
          <div class="toolbar">
            <div style="margin-right:auto">
              <strong>模型出站授权</strong>
              <div class="muted">开启后，系统可将问题描述、已发布方法文档和脱敏证据发送到当前 Chat 模型；原始日志正文仍由本机扫描。</div>
            </div>
            <el-switch
              :model-value="caseInfo.model_egress_approved"
              aria-label="案例模型出站授权" :loading="consentBusy"
              :disabled="!canEditCase || consentBusy"
              active-text="已授权"
              inactive-text="未授权"
              @change="(value:string | number | boolean) => updateModelEgress(value === true)"
            />
          </div>
        </el-card>
        <details v-if="canManageMembers" class="technical-details">
          <summary>案例协作与成员权限</summary>
          <el-card shadow="never" style="margin-bottom:16px">
            <div class="toolbar">
              <el-select v-model="memberForm.user_id" filterable placeholder="选择用户" style="width:260px">
                <el-option v-for="user in availableMemberUsers" :key="user.id" :label="`${user.display_name} (${user.username})`" :value="user.id" />
              </el-select>
              <el-select v-model="memberForm.permission" style="width:150px">
                <el-option label="可编辑" value="EDITOR" />
                <el-option label="只读" value="VIEWER" />
              </el-select>
              <el-button type="primary" :disabled="!memberForm.user_id" @click="addCaseMember">添加成员</el-button>
              <span class="muted">案例所有者和管理员可以管理成员；可编辑成员不能删除案例或管理成员。</span>
            </div>
            <el-table :data="caseMembers" empty-text="尚未添加成员">
              <el-table-column prop="username" label="用户名" min-width="160" />
              <el-table-column prop="display_name" label="显示名称" min-width="180" />
              <el-table-column label="案例权限" width="180">
                <template #default="scope">
                  <el-select :model-value="scope.row.permission" @change="(value: 'EDITOR' | 'VIEWER') => updateCaseMember(scope.row as CaseMember, value)">
                    <el-option label="可编辑" value="EDITOR" />
                    <el-option label="只读" value="VIEWER" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="100"><template #default="scope"><el-button link type="danger" @click="removeCaseMember(scope.row as CaseMember)">移除</el-button></template></el-table-column>
            </el-table>
          </el-card>
        </details>
        <h3 class="section-title">上传问题日志</h3>
        <div class="toolbar">
          <input ref="debugFileInput" type="file" aria-label="选择问题日志" :disabled="!canEditCase || actionBusy" @change="selectDebugFile"/>
          <el-select v-model="debugSourceDeviceType" style="width:145px" aria-label="日志来源设备">
            <el-option label="来源未知" value="UNKNOWN" />
            <el-option label="GW 日志" value="GW" />
            <el-option label="AP 日志" value="AP" />
          </el-select>
          <el-select v-model="debugSourceDeviceRole" style="width:155px" aria-label="日志来源角色">
            <el-option label="角色未知" value="UNKNOWN" />
            <el-option label="主设备" value="PRIMARY" />
            <el-option label="从设备" value="SECONDARY" />
          </el-select>
          <el-button type="primary" :disabled="!canEditCase || !debugFile || jobRunning" :loading="actionBusy" @click="uploadDebug">上传并解析</el-button>
          <span class="muted">支持 ZIP/TAR/TGZ、常见日志和无后缀纯文本 collectDebuginfo；无后缀日志上传时会自动追加 .txt。</span>
        </div>
        <el-table :data="artifacts">
          <el-table-column prop="original_name" label="文件" min-width="260" />
          <el-table-column prop="kind" label="类型" width="130" />
          <el-table-column label="组网来源" width="150">
            <template #default="scope">{{ scope.row.source_device_type }} / {{ scope.row.source_device_role }}</template>
          </el-table-column>
          <el-table-column prop="size_bytes" label="大小(B)" width="120" />
          <el-table-column prop="status" label="状态" width="120" />
          <el-table-column label="操作" width="250">
            <template #default="scope">
              <el-button link type="primary" :disabled="!canEditCase" @click="parseArtifact(scope.row.id)">解析</el-button>
              <el-button link @click="openArtifactFiles(scope.row.id)">文件树</el-button>
               <el-button link type="danger" :disabled="!canEditCase" @click="deleteArtifact(scope.row as Artifact)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="日志与筛查" name="logs" lazy>
        <LogTriagePanel v-if="activeTab==='logs'" :case-id="caseId" :artifacts="artifacts" :can-edit="canEditCase" :model-egress-approved="caseInfo.model_egress_approved" @open-source="openTriageSource" />
        <h3 class="section-title">原始日志与证据位置</h3>
        <LogBrowserPanel ref="logBrowser" :artifacts="artifacts" />
      </el-tab-pane>

      <el-tab-pane label="综合诊断" name="diagnosis">
        <el-alert v-if="diagnosis.diagnostic_planning?.method_coverage?.skill_status?.warning" :title="diagnosis.diagnostic_planning.method_coverage.skill_status.warning" type="warning" show-icon :closable="false" class="inline-alert" data-testid="diagnosis-skill-warning" />
        <details v-if="latestAnalysisWithTrace?.agent_run_id" class="technical-details"><summary>技术轨迹与执行记录</summary>
        <PlanningTracePanel
          v-if="latestAnalysisWithTrace?.agent_run_id"
          :case-id="caseId"
          :run-id="latestAnalysisWithTrace.agent_run_id"
          operation="comprehensive_diagnosis"
          :title="isDemoSnapshot ? '真实 GLM-5.2 历史诊断轨迹（脱敏快照）' : '综合诊断多轮 LLM Planning 轨迹'"
          style="margin-bottom:14px"
        />
        </details>
        <el-empty v-if="!latestAnalysis" description="请先完成日志解析并启动综合诊断" />
        <template v-else>
          <el-alert type="info" :closable="false" :title="displayDiagnosisText(diagnosis.summary || '诊断完成')" />
          <el-alert
            v-if="isDemoSnapshot"
            type="info"
            :closable="false"
            show-icon
            data-testid="demo-diagnosis-snapshot"
            title="这是此前真实 GLM-5.2 成功诊断的脱敏、证据重定位快照"
            description="模型结论、2 轮规划、工具轨迹、321,453 Token 与约 146 秒耗时均来自历史成功运行；当前导入过程未再次调用模型。结论已重绑定到当前合成日志的文件名与行号，私有方法正文和凭据未入包。"
            style="margin-top:10px"
          />
          <el-alert
            v-else-if="synthesisStatus"
            :type="synthesisStatus.accepted ? 'success' : synthesisStatus.mode === 'SKIPPED' ? 'info' : 'warning'"
            :closable="false"
            show-icon
            :title="synthesisStatus.accepted ? '最终大模型诊断已通过证据校验' : synthesisStatus.mode === 'SKIPPED' ? '本次未调用最终大模型诊断' : '最终大模型诊断未通过校验，保留确定性诊断结果'"
            :description="synthesisFailure ? `${synthesisFailure.code} · ${synthesisFailure.message}${synthesisFailure.field_path ? ` · 字段 ${synthesisFailure.field_path}` : ''}${synthesisStatus.finish_reason ? ` · 模型停止原因 ${synthesisStatus.finish_reason}` : ''}` : synthesisStatus.mode"
            style="margin-top:10px"
          />
          <details v-if="diagnosis.diagnostic_planning" class="technical-details"><summary>诊断方法与故障树覆盖</summary><DiagnosticPlanningPanel :planning="diagnosis.diagnostic_planning" :evidence-labels="analysisEvidenceLabels" /></details>
          <h3 class="section-title">根因候选</h3>
          <el-collapse>
            <el-collapse-item v-for="item in diagnosis.hypotheses || []" :key="item.rank" :name="item.rank">
              <template #title><strong>{{ item.rank }}. {{ displayDiagnosisText(item.title) }}</strong>&nbsp;<el-tag size="small">{{ item.confidence_level }}</el-tag>&nbsp;<el-tag size="small" type="danger">{{ item.priority }}</el-tag></template>
              <p>{{ displayDiagnosisText(item.description) }}</p><p class="muted">支持证据：{{ evidenceLabels(item.supporting_evidence) }}；反证：{{ item.contradicting_evidence?.length ? evidenceLabels(item.contradicting_evidence) : '无明确反证' }}</p>
            </el-collapse-item>
          </el-collapse>
          <h3 class="section-title">建议排查步骤</h3>
          <el-table :data="displayedActions"><el-table-column prop="priority" label="优先级" width="90"/><el-table-column prop="action" label="动作" min-width="240"/><el-table-column prop="reason" label="原因" min-width="280"/><el-table-column prop="expected_result" label="预期结果" min-width="240"/></el-table>
          <h3 class="section-title">缺失信息与限制</h3><ul><li v-for="item in [...(diagnosis.missing_information || []), ...(diagnosis.limitations || [])]" :key="item">{{ displayDiagnosisText(item) }}</li></ul>
          <h3 class="section-title">已确认事实</h3>
          <div v-for="fact in diagnosis.confirmed_facts || []" :key="fact.statement" class="evidence-box">{{ displayDiagnosisText(fact.statement) }}<div class="muted">证据：{{ evidenceLabels(fact.evidence_ids) }}</div></div>
        </template>
      </el-tab-pane>

      <el-tab-pane label="交互问答" name="chat" lazy>
        <CaseChatPanel
          :case-id="caseId"
          :can-edit="canEditCase"
          :model-egress-approved="caseInfo.model_egress_approved"
          :latest-analysis-id="latestAnalysis?.id || ''"
          @analysis-updated="loadAll"
        />
      </el-tab-pane>

      <el-tab-pane label="补充资料" name="code" lazy>
        <div class="toolbar">
          <input type="file" accept=".zip,.tar,.gz,.tgz,.bundle" :disabled="!canEditCase" @change="(e:any) => repoFile = e.target.files?.[0] || null"/>
          <el-button type="primary" :disabled="!canEditCase" @click="uploadRepo">上传代码仓库</el-button>
          <router-link v-if="['ADMIN','EXPERT'].includes(principal?.role || '')" :to="{ path: '/admin/cognitive-search', query: { case: caseId } }">
            <el-button>打开认知检索</el-button>
          </router-link>
        </div>
        <el-alert
          type="info"
          :closable="false"
          style="margin-bottom:12px"
          title="代码图谱支持 C/C++、Python、Java、JavaScript/TypeScript、Go；需要 Commit 追溯时，请在源码仓执行 git bundle create repository.bundle --all 后上传 .bundle。系统只建立索引，不会覆盖源码。"
        />
        <el-table :data="repositories">
          <el-table-column prop="name" label="仓库" min-width="190"/>
          <el-table-column prop="status" label="文件状态" width="110"/>
          <el-table-column prop="graph_status" label="代码图谱" width="120"/>
          <el-table-column prop="commit_graph_status" label="Commit 图谱" width="130"/>
          <el-table-column prop="branch" label="分支" width="120"/>
          <el-table-column prop="commit_hash" label="Commit" width="160" show-overflow-tooltip/>
          <el-table-column label="操作" width="280"><template #default="scope"><el-button link type="primary" :disabled="!canEditCase || !['UPLOADED', 'INDEXED', 'INDEX_FAILED'].includes(scope.row.status)" @click="indexRepo(scope.row.id)">建立索引</el-button><el-button link @click="loadSymbols(scope.row.id)">查看符号</el-button><el-button link type="warning" :disabled="!canEditCase || !['UPLOADED', 'INDEXED', 'INDEX_FAILED'].includes(scope.row.status)" @click="runStatic(scope.row.id)">静态分析</el-button></template></el-table-column>
        </el-table>
        <div class="toolbar" style="margin-top:18px"><el-input v-model="symbolSearch" placeholder="函数名、宏名或文件路径" style="width:300px"/><el-button v-if="repositories[0]" @click="loadSymbols(repositories[0].id)">搜索符号</el-button></div>
        <el-table :data="symbols" height="450">
          <el-table-column prop="kind" label="类型" width="90"/><el-table-column prop="name" label="符号" width="210"/><el-table-column prop="file_path" label="文件" min-width="260"/><el-table-column prop="line_start" label="起始行" width="90"/><el-table-column prop="signature" label="签名" min-width="260" show-overflow-tooltip/>
          <el-table-column label="操作" width="120"><template #default="scope"><el-button link type="primary" :disabled="!canEditCase" @click="suggestPatch(scope.row.id)">候选补丁</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="诊断报告" name="report">
        <div class="toolbar"><el-button type="primary" :disabled="!canEditCase || !latestAnalysis" @click="exportReport('pdf')">导出 PDF</el-button><el-button :disabled="!canEditCase || !latestAnalysis" @click="exportReport('docx')">导出 Word</el-button><el-button :disabled="!canEditCase || !latestAnalysis" @click="exportReport('html')">导出 HTML</el-button></div>
        <iframe v-if="activeTab === 'report' && reportHtml" class="report-frame" title="诊断报告预览" :srcdoc="reportHtml" sandbox="allow-scripts" style="width:100%;height:720px;border:1px solid #d1d5db;background:white" />
        <el-empty v-else description="暂无报告" />
      </el-tab-pane>
    </el-tabs>
    <LibrarySubmissionDialog v-if="canSubmitResult" v-model="submittingLibrary" :categories="config.categories" :source-case="caseInfo" :analysis-id="latestAnalysis?.id" />
  </div>
</template>
