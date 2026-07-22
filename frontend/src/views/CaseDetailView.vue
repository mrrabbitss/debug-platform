<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { Analysis, Artifact, CaseItem, Job, LogEvent } from '../types'

const route = useRoute()
const caseId = String(route.params.id)
const caseInfo = ref<CaseItem | null>(null)
const artifacts = ref<Artifact[]>([])
const events = ref<LogEvent[]>([])
const analyses = ref<Analysis[]>([])
const repositories = ref<any[]>([])
const symbols = ref<any[]>([])
const activeTab = ref('overview')
const debugFile = ref<File | null>(null)
const debugFileInput = ref<HTMLInputElement | null>(null)
const repoFile = ref<File | null>(null)
const currentJob = ref<Job | null>(null)
const jobTimer = ref<number | null>(null)
const eventFilter = reactive({ level: '', module: '', search: '' })
const diagnosis = ref<any>({})
const reportHtml = ref('')
const chatQuestion = ref('')
const chatMessages = ref<{role:string, content:string, citations?:any[]}[]>([])
const selectedEvent = ref<LogEvent | null>(null)
const fileManifest = ref<any>({})
const rawLog = ref('')
const selectedArtifact = ref<string>('')
const selectedLogPath = ref('')
const symbolSearch = ref('')

const latestAnalysis = computed(() => analyses.value.find(item => item.status === 'COMPLETED'))
const criticalCount = computed(() => events.value.filter(e => e.level === 'CRITICAL').length)
const errorCount = computed(() => events.value.filter(e => e.level === 'ERROR').length)
const modules = computed(() => [...new Set(events.value.map(e => e.module))].sort())

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
    await loadReportPreview(latestAnalysis.value.id)
  }
  await loadEvents()
}

async function loadEvents() {
  const params: any = { limit: 1000 }
  if (eventFilter.level) params.level = eventFilter.level
  if (eventFilter.module) params.module = eventFilter.module
  if (eventFilter.search) params.search = eventFilter.search
  events.value = (await api.get(`/cases/${caseId}/events`, { params })).data
}

async function uploadDebug() {
  if (!debugFile.value) return ElMessage.warning('请选择 collectDebuginfo 或日志文件')
  try {
    const selectedName = debugFile.value.name
    const data = new FormData()
    data.append('file', debugFile.value)
    data.append('kind', 'debug_log')
    const artifact = (await api.post(`/cases/${caseId}/artifacts`, data)).data
    artifacts.value.unshift(artifact)
    const parseJob = (await api.post(`/cases/${caseId}/artifacts/${artifact.id}/parse`)).data
    debugFile.value = null
    if (debugFileInput.value) debugFileInput.value.value = ''
    const normalized = artifact.original_name !== selectedName
    ElMessage.success(normalized ? `无后缀文件已按 ${artifact.original_name} 上传，正在解析` : '上传完成，正在按内容识别并解析日志')
    watchJob(parseJob)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志上传或解析启动失败')
  }
}

function selectDebugFile(event: Event) {
  debugFile.value = (event.target as HTMLInputElement).files?.[0] || null
}

async function parseArtifact(artifactId: string) {
  const { data } = await api.post(`/cases/${caseId}/artifacts/${artifactId}/parse`)
  watchJob(data)
}

async function analyze() {
  const { data } = await api.post(`/cases/${caseId}/analyses`)
  watchJob(data)
}

function watchJob(job: Job) {
  currentJob.value = job
  if (jobTimer.value) window.clearInterval(jobTimer.value)
  jobTimer.value = window.setInterval(async () => {
    const { data } = await api.get(`/jobs/${job.id}`)
    currentJob.value = data
    if (['COMPLETED', 'FAILED'].includes(data.status)) {
      if (jobTimer.value) window.clearInterval(jobTimer.value)
      jobTimer.value = null
      data.status === 'COMPLETED' ? ElMessage.success('任务执行完成') : ElMessage.error(data.error_message || '任务失败')
      await loadAll()
    }
  }, 1200)
}

async function loadManifest(artifactId: string) {
  selectedArtifact.value = artifactId
  fileManifest.value = (await api.get(`/artifacts/${artifactId}/files`)).data
  activeTab.value = 'logs'
}

async function loadRawLog(path: string) {
  if (!selectedArtifact.value) return
  selectedLogPath.value = path
  rawLog.value = (await api.get(`/artifacts/${selectedArtifact.value}/content`, { params: { path, start_line: 1, line_count: 5000 } })).data
}

async function loadReportPreview(analysisId: string) {
  reportHtml.value = (await api.get(`/cases/${caseId}/analyses/${analysisId}/report/preview`)).data
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

async function ask() {
  const question = chatQuestion.value.trim()
  if (!question) return
  chatMessages.value.push({ role: 'user', content: question })
  chatQuestion.value = ''
  const { data } = await api.post(`/cases/${caseId}/chat`, { question })
  chatMessages.value.push({ role: 'assistant', content: data.answer, citations: data.citations })
}

async function uploadRepo() {
  if (!repoFile.value) return ElMessage.warning('请选择代码仓库压缩包')
  const data = new FormData()
  data.append('file', repoFile.value)
  const result = (await api.post(`/cases/${caseId}/repositories`, data)).data
  repoFile.value = null
  repositories.value = (await api.get(`/cases/${caseId}/repositories`)).data
  ElMessage.success(`代码仓库已上传，共 ${result.files} 个文件`)
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

onMounted(loadAll)
</script>

<template>
  <div v-if="caseInfo">
    <div class="toolbar">
      <div style="margin-right:auto">
        <h1 class="page-title" style="margin-bottom:4px">{{ caseInfo.title }}</h1>
        <span class="muted">{{ caseInfo.id }} · {{ caseInfo.device_type }} {{ caseInfo.device_model || '' }} · {{ caseInfo.firmware_version || '固件版本未填' }}</span>
      </div>
      <el-tag>{{ caseInfo.status }}</el-tag>
      <el-button type="primary" @click="analyze">开始综合诊断</el-button>
    </div>

    <el-alert v-if="currentJob" :closable="false" :type="currentJob.status === 'FAILED' ? 'error' : 'info'" style="margin-bottom:14px">
      <template #title>{{ currentJob.kind }}：{{ currentJob.message || currentJob.status }}</template>
      <el-progress :percentage="currentJob.progress" :status="currentJob.status === 'FAILED' ? 'exception' : undefined" />
      <pre v-if="currentJob.error_message" class="mono">{{ currentJob.error_message }}</pre>
    </el-alert>

    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="案例概览" name="overview">
        <div class="card-grid">
          <div class="stat-card"><div class="muted">关键事件</div><strong style="font-size:28px">{{ events.length }}</strong></div>
          <div class="stat-card"><div class="muted">严重事件</div><strong style="font-size:28px;color:#991b1b">{{ criticalCount }}</strong></div>
          <div class="stat-card"><div class="muted">错误事件</div><strong style="font-size:28px;color:#dc2626">{{ errorCount }}</strong></div>
          <div class="stat-card"><div class="muted">知识增强</div><strong style="font-size:28px">{{ diagnosis.retrieved_knowledge?.length || 0 }}</strong></div>
        </div>
        <h3 class="section-title">问题现象</h3><p>{{ caseInfo.description || '未填写' }}</p>
        <h3 class="section-title">上传 collectDebuginfo</h3>
        <div class="toolbar">
          <input ref="debugFileInput" type="file" @change="selectDebugFile"/>
          <el-button type="primary" @click="uploadDebug">上传并解析</el-button>
          <span class="muted">支持 ZIP/TAR/TGZ、常见日志和无后缀纯文本 collectDebuginfo；无后缀日志上传时会自动追加 .txt。</span>
        </div>
        <el-table :data="artifacts">
          <el-table-column prop="original_name" label="文件" min-width="260" />
          <el-table-column prop="kind" label="类型" width="130" />
          <el-table-column prop="size_bytes" label="大小(B)" width="120" />
          <el-table-column prop="status" label="状态" width="120" />
          <el-table-column label="操作" width="210">
            <template #default="scope">
              <el-button link type="primary" @click="parseArtifact(scope.row.id)">解析</el-button>
              <el-button link @click="loadManifest(scope.row.id)">文件树</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="日志浏览" name="logs">
        <div class="toolbar">
          <el-select v-model="selectedArtifact" placeholder="选择已解析日志包" style="width:260px" @change="loadManifest">
            <el-option v-for="item in artifacts.filter(a => a.status === 'PARSED')" :key="item.id" :label="item.original_name" :value="item.id" />
          </el-select>
          <span class="muted">解析文件数：{{ fileManifest.manifest_file_count || 0 }}</span>
          <span class="muted">解析器：{{ Object.keys(fileManifest.parser_counts || {}).join('、') || '暂无' }}</span>
        </div>
        <el-row :gutter="14">
          <el-col :span="7">
            <el-card header="文件目录" style="height:650px;overflow:auto">
              <div v-for="item in fileManifest.manifest || []" :key="item.path" style="padding:5px 0;cursor:pointer" @click="loadRawLog(item.path)">
                <span class="mono">{{ item.path }}</span> <small class="muted">{{ item.size }}</small>
              </div>
            </el-card>
          </el-col>
          <el-col :span="17">
            <el-card :header="selectedLogPath || '原始日志'" style="height:650px">
              <pre class="mono" style="height:570px;overflow:auto">{{ rawLog || '选择左侧文件查看，原始数据不会被 LLM 输出覆盖。' }}</pre>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <el-tab-pane label="事件与时间线" name="events">
        <div class="toolbar">
          <el-select v-model="eventFilter.level" clearable placeholder="级别" style="width:120px"><el-option v-for="x in ['CRITICAL','ERROR','WARN','NOTICE','INFO','DEBUG','TRACE']" :key="x" :label="x" :value="x" /></el-select>
          <el-select v-model="eventFilter.module" clearable placeholder="模块" style="width:130px"><el-option v-for="x in modules" :key="x" :label="x" :value="x" /></el-select>
          <el-input v-model="eventFilter.search" placeholder="错误码/关键词" style="width:260px" @keyup.enter="loadEvents" />
          <el-button @click="loadEvents">筛选</el-button>
        </div>
        <el-table :data="events" height="590" @row-click="(row:LogEvent) => selectedEvent = row">
          <el-table-column prop="timestamp_normalized" label="时间" width="190" />
          <el-table-column prop="level" label="级别" width="90"><template #default="scope"><span :class="`log-${scope.row.level.toLowerCase()}`">{{ scope.row.level }}</span></template></el-table-column>
          <el-table-column prop="module" label="模块" width="100" />
          <el-table-column prop="component" label="组件" width="120" />
          <el-table-column prop="event_code" label="事件码" width="190" />
          <el-table-column prop="message" label="日志内容" min-width="400" show-overflow-tooltip />
          <el-table-column prop="source_file" label="来源" width="210" show-overflow-tooltip />
        </el-table>
        <el-drawer v-model="selectedEvent" title="事件证据详情" size="52%">
          <div v-if="selectedEvent">
            <el-descriptions :column="2" border><el-descriptions-item label="证据编号">{{ selectedEvent.id }}</el-descriptions-item><el-descriptions-item label="可信度">{{ selectedEvent.confidence }}</el-descriptions-item><el-descriptions-item label="文件">{{ selectedEvent.source_file }}</el-descriptions-item><el-descriptions-item label="行号">{{ selectedEvent.line_start }}-{{ selectedEvent.line_end }}</el-descriptions-item></el-descriptions>
            <pre class="mono evidence-box">{{ selectedEvent.raw_text }}</pre>
          </div>
        </el-drawer>
      </el-tab-pane>

      <el-tab-pane label="综合诊断" name="diagnosis">
        <el-empty v-if="!latestAnalysis" description="请先完成日志解析并启动综合诊断" />
        <template v-else>
          <el-alert type="info" :closable="false" :title="diagnosis.summary || '诊断完成'" />
          <h3 class="section-title">已确认事实</h3>
          <div v-for="fact in diagnosis.confirmed_facts || []" :key="fact.statement" class="evidence-box">{{ fact.statement }}<div class="muted">证据：{{ fact.evidence_ids?.join('、') }}</div></div>
          <h3 class="section-title">根因候选</h3>
          <el-collapse>
            <el-collapse-item v-for="item in diagnosis.hypotheses || []" :key="item.rank" :name="item.rank">
              <template #title><strong>{{ item.rank }}. {{ item.title }}</strong>&nbsp;<el-tag size="small">{{ item.confidence_level }}</el-tag>&nbsp;<el-tag size="small" type="danger">{{ item.priority }}</el-tag></template>
              <p>{{ item.description }}</p><p class="muted">支持证据：{{ item.supporting_evidence?.join('、') }}；反证：{{ item.contradicting_evidence?.join('、') || '无明确反证' }}</p>
            </el-collapse-item>
          </el-collapse>
          <h3 class="section-title">建议排查步骤</h3>
          <el-table :data="diagnosis.recommended_actions || []"><el-table-column prop="priority" label="优先级" width="90"/><el-table-column prop="action" label="动作" min-width="240"/><el-table-column prop="reason" label="原因" min-width="280"/><el-table-column prop="expected_result" label="预期结果" min-width="240"/></el-table>
          <h3 class="section-title">缺失信息与限制</h3><ul><li v-for="item in [...(diagnosis.missing_information || []), ...(diagnosis.limitations || [])]" :key="item">{{ item }}</li></ul>
        </template>
      </el-tab-pane>

      <el-tab-pane label="交互问答" name="chat">
        <div style="height:540px;overflow:auto;border:1px solid #e5e7eb;padding:16px;background:#fff">
          <div v-for="(msg,index) in chatMessages" :key="index" :style="{textAlign:msg.role==='user'?'right':'left',marginBottom:'16px'}">
            <div :style="{display:'inline-block',maxWidth:'80%',padding:'10px 14px',borderRadius:'8px',background:msg.role==='user'?'#dbeafe':'#f3f4f6',textAlign:'left'}">{{ msg.content }}</div>
            <div v-if="msg.citations?.length" class="muted" style="font-size:12px">引用：{{ msg.citations.map(x => x.evidence_id).join('、') }}</div>
          </div>
        </div>
        <div class="toolbar" style="margin-top:12px"><el-input v-model="chatQuestion" type="textarea" :rows="2" placeholder="例如：为什么认为是 hostapd 问题？还缺少哪些证据？" @keyup.ctrl.enter="ask"/><el-button type="primary" @click="ask">发送</el-button></div>
      </el-tab-pane>

      <el-tab-pane label="代码仓库" name="code">
        <div class="toolbar"><input type="file" accept=".zip,.tar,.gz,.tgz" @change="(e:any) => repoFile = e.target.files?.[0] || null"/><el-button type="primary" @click="uploadRepo">上传代码仓库</el-button><span class="muted">支持 C/C++ 函数、宏、结构体索引；不会自动覆盖源码。</span></div>
        <el-table :data="repositories">
          <el-table-column prop="name" label="仓库" min-width="220"/><el-table-column prop="status" label="状态" width="120"/><el-table-column prop="commit_hash" label="Commit" width="160"/>
          <el-table-column label="操作" width="280"><template #default="scope"><el-button link type="primary" @click="indexRepo(scope.row.id)">建立索引</el-button><el-button link @click="loadSymbols(scope.row.id)">查看符号</el-button><el-button link type="warning" @click="runStatic(scope.row.id)">静态分析</el-button></template></el-table-column>
        </el-table>
        <div class="toolbar" style="margin-top:18px"><el-input v-model="symbolSearch" placeholder="函数名、宏名或文件路径" style="width:300px"/><el-button v-if="repositories[0]" @click="loadSymbols(repositories[0].id)">搜索符号</el-button></div>
        <el-table :data="symbols" height="450">
          <el-table-column prop="kind" label="类型" width="90"/><el-table-column prop="name" label="符号" width="210"/><el-table-column prop="file_path" label="文件" min-width="260"/><el-table-column prop="line_start" label="起始行" width="90"/><el-table-column prop="signature" label="签名" min-width="260" show-overflow-tooltip/>
          <el-table-column label="操作" width="120"><template #default="scope"><el-button link type="primary" @click="suggestPatch(scope.row.id)">候选补丁</el-button></template></el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="诊断报告" name="report">
        <div class="toolbar"><el-button type="primary" @click="exportReport('pdf')">导出 PDF</el-button><el-button @click="exportReport('docx')">导出 Word</el-button><el-button @click="exportReport('html')">导出 HTML</el-button></div>
        <iframe v-if="reportHtml" :srcdoc="reportHtml" style="width:100%;height:720px;border:1px solid #d1d5db;background:white" />
        <el-empty v-else description="暂无报告" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
