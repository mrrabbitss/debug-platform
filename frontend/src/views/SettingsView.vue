<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type {
  Job,
  ModelDownloadCatalog,
  ModelDownloadItem,
  ModelDownloadJob,
  ModelMode,
  ModelProfile,
  ModelTask
} from '../types'

type ThinkingMode = 'inherit' | 'enabled' | 'disabled'

const profiles = ref<ModelProfile[]>([])
const retrieval = ref<any>({})
const activeTask = ref<ModelTask>('chat')
const dialogVisible = ref(false)
const editingId = ref<string | null>(null)
const saving = ref(false)
const testingId = ref('')
const reindexing = ref(false)
const apiKey = ref(localStorage.getItem('gw_ap_api_key') || '')
const startingDownloadId = ref('')
const watchedDownloadJobs = new Set<string>()
const modelDownloads = ref<ModelDownloadCatalog>({
  download_root: '',
  mirrors: [],
  models: [],
  jobs: [],
  runtime_installed: false
})
const downloadForm = reactive({
  mirror_base: 'https://hf-mirror.com',
  revision: 'main',
  proxy_url: ''
})
const defaultEmbeddingPath = 'models/embedding/bge-base-zh-v1.5'
const defaultRerankerPath = 'models/reranker/Qwen3-Reranker-0.6B'
const defaultEmbeddingInstruction = '为这个句子生成表示以用于检索相关文章：'
const defaultRerankerInstruction = 'Given a network troubleshooting query, retrieve passages that help diagnose and solve it.'

const form = reactive({
  name: '',
  task_type: 'chat' as ModelTask,
  mode: 'api' as ModelMode,
  provider: 'openai_compatible',
  model_name: '',
  base_url: '',
  api_key: '',
  clear_api_key: false,
  proxy_url: '',
  clear_proxy_url: false,
  enabled: true,
  temperature: 0.1,
  thinking_mode: 'inherit' as ThinkingMode,
  max_tokens: 0,
  context_window_tokens: 0,
  context_reserved_output_tokens: 0,
  timeout_seconds: 300,
  max_retries: 2,
  dimension: undefined as number | undefined,
  batch_size: 16,
  device: 'cpu',
  candidate_count: 30,
  query_instruction: defaultEmbeddingInstruction,
  instruction: defaultRerankerInstruction
})

const taskProfiles = computed(() => profiles.value.filter(item => item.task_type === activeTask.value))
const dialogTitle = computed(() => editingId.value ? '修改模型配置' : '添加模型配置')
const editingProfile = computed(() => profiles.value.find(item => item.id === editingId.value))

const taskLabels: Record<ModelTask, string> = {
  chat: '诊断大模型',
  embedding: 'Embedding 模型',
  reranker: 'Reranker 模型'
}

const providerLabels: Record<string, string> = {
  mock: '规则引擎 / Mock',
  openai_compatible: 'OpenAI-Compatible API',
  hashing: '内置字符向量',
  sentence_transformers: '高级：进程内 Sentence Transformers',
  disabled: '不使用 Reranker',
  qwen_rerank_api: 'Qwen Rerank API'
}

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}

async function load() {
  const [modelResponse, retrievalResponse, downloadResponse] = await Promise.all([
    api.get('/system/models'),
    api.get('/system/retrieval'),
    api.get<ModelDownloadCatalog>('/system/model-downloads')
  ])
  profiles.value = modelResponse.data
  retrieval.value = retrievalResponse.data
  applyDownloadCatalog(downloadResponse.data)
}

function applyDownloadCatalog(payload: ModelDownloadCatalog) {
  modelDownloads.value = payload
  if (!payload.mirrors.includes(downloadForm.mirror_base)) {
    downloadForm.mirror_base = payload.mirrors[0] || ''
  }
  resumeDownloadPolling()
}

async function refreshModelDownloads(resume = true) {
  const { data } = await api.get<ModelDownloadCatalog>('/system/model-downloads')
  modelDownloads.value = data
  if (!data.mirrors.includes(downloadForm.mirror_base)) {
    downloadForm.mirror_base = data.mirrors[0] || ''
  }
  if (resume) resumeDownloadPolling()
}

function activeDownload(modelId: string) {
  return modelDownloads.value.jobs.find(job =>
    job.model_id === modelId
    && ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status)
  )
}

function mergeDownloadJob(job: ModelDownloadJob) {
  const index = modelDownloads.value.jobs.findIndex(item => item.id === job.id)
  if (index >= 0) modelDownloads.value.jobs[index] = job
  else modelDownloads.value.jobs.unshift(job)
}

function resumeDownloadPolling() {
  modelDownloads.value.jobs
    .filter(job => ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status))
    .forEach(job => { void watchDownloadJob(job) })
}

async function watchDownloadJob(initial: ModelDownloadJob) {
  if (watchedDownloadJobs.has(initial.id)) return
  watchedDownloadJobs.add(initial.id)
  let current = initial
  try {
    while (!['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(current.status)) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      const response = await api.get<Job>(`/jobs/${current.id}`)
      current = {
        ...response.data,
        model_id: initial.model_id,
        mirror_base: initial.mirror_base,
        revision: initial.revision,
        proxy_configured: initial.proxy_configured
      }
      mergeDownloadJob(current)
    }
    if (current.status === 'COMPLETED') ElMessage.success(`${initial.model_id} 权重下载完成`)
    else if (current.status !== 'CANCELLED') {
      ElMessage.error(current.error_message || current.message || '模型权重下载失败')
    }
    await refreshModelDownloads(false)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    watchedDownloadJobs.delete(initial.id)
  }
}

async function startModelDownload(model: ModelDownloadItem) {
  if (!downloadForm.mirror_base) return ElMessage.warning('请选择模型镜像')
  try {
    await ElMessageBox.confirm(
      `确认下载 ${model.display_name}？只下载权重文件，不会安装 Torch 或启动模型。`,
      '下载模型权重',
      { type: 'warning' }
    )
  } catch {
    return
  }
  startingDownloadId.value = model.model_id
  try {
    const { data } = await api.post<Job>('/system/model-downloads', {
      model_id: model.model_id,
      mirror_base: downloadForm.mirror_base,
      revision: downloadForm.revision.trim() || 'main',
      proxy_url: downloadForm.proxy_url.trim() || null
    })
    const job: ModelDownloadJob = {
      ...data,
      model_id: model.model_id,
      mirror_base: downloadForm.mirror_base,
      revision: downloadForm.revision.trim() || 'main',
      proxy_configured: !!downloadForm.proxy_url.trim()
    }
    downloadForm.proxy_url = ''
    mergeDownloadJob(job)
    ElMessage.success('模型权重下载任务已创建')
    void watchDownloadJob(job)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    startingDownloadId.value = ''
  }
}

async function cancelModelDownload(job: ModelDownloadJob) {
  try {
    const { data } = await api.post<Job>(`/system/model-downloads/${job.id}/cancel`)
    mergeDownloadJob({ ...job, ...data })
    ElMessage.success('已请求取消模型下载')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

function formatBytes(value: number) {
  if (!value) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / (1024 ** index)).toFixed(index ? 2 : 0)} ${units[index]}`
}

function downloadStatus(status: ModelDownloadItem['status']) {
  return {
    NOT_DOWNLOADED: '未下载',
    PARTIAL: '未完成',
    READY: '已完整下载'
  }[status]
}

function providerFor(task: ModelTask, mode: ModelMode) {
  if (task === 'chat') return mode === 'builtin' ? 'mock' : 'openai_compatible'
  if (task === 'embedding') {
    return mode === 'builtin' ? 'hashing' : mode === 'local' ? 'sentence_transformers' : 'openai_compatible'
  }
  return mode === 'builtin' ? 'disabled' : mode === 'local' ? 'sentence_transformers' : 'qwen_rerank_api'
}

function allowedModes(task: ModelTask): ModelMode[] {
  return task === 'chat' ? ['builtin', 'api'] : ['builtin', 'local', 'api']
}

function updateProvider() {
  if (!allowedModes(form.task_type).includes(form.mode)) form.mode = 'api'
  form.provider = providerFor(form.task_type, form.mode)
  if (form.provider === 'hashing') form.model_name = 'hashing-char-384'
  if (form.provider === 'mock') form.model_name = 'rule-engine'
  if (form.provider === 'disabled') form.model_name = 'disabled'
  if (form.provider === 'sentence_transformers' && form.task_type === 'embedding' && !form.model_name) {
    form.model_name = defaultEmbeddingPath
  }
  if (form.provider === 'sentence_transformers' && form.task_type === 'reranker' && !form.model_name) {
    form.model_name = defaultRerankerPath
  }
  if (form.provider === 'qwen_rerank_api' && !form.model_name) form.model_name = 'qwen3-rerank'
}

function resetForm(task: ModelTask) {
  editingId.value = null
  form.name = ''
  form.task_type = task
  form.mode = task === 'chat' ? 'api' : 'builtin'
  form.model_name = ''
  form.base_url = ''
  form.api_key = ''
  form.clear_api_key = false
  form.proxy_url = ''
  form.clear_proxy_url = false
  form.enabled = true
  form.temperature = 0.1
  form.thinking_mode = 'inherit'
  form.max_tokens = 0
  form.context_window_tokens = 0
  form.context_reserved_output_tokens = 0
  form.timeout_seconds = 300
  form.max_retries = 2
  form.dimension = undefined
  form.batch_size = 16
  form.device = 'cpu'
  form.candidate_count = 30
  form.query_instruction = defaultEmbeddingInstruction
  form.instruction = defaultRerankerInstruction
  updateProvider()
}

function changeTask() {
  form.model_name = ''
  updateProvider()
}

function openCreate() {
  resetForm(activeTask.value)
  dialogVisible.value = true
}

function openEdit(profile: ModelProfile) {
  editingId.value = profile.id
  form.name = profile.name
  form.task_type = profile.task_type
  form.mode = profile.mode
  form.provider = profile.provider
  form.model_name = profile.model_name
  form.base_url = profile.base_url || ''
  form.api_key = ''
  form.clear_api_key = false
  form.proxy_url = ''
  form.clear_proxy_url = false
  form.enabled = profile.enabled
  form.temperature = Number(profile.config.temperature ?? 0.1)
  const configuredThinking = String(profile.config.thinking_mode || '')
  form.thinking_mode = ['inherit', 'enabled', 'disabled'].includes(configuredThinking)
    ? configuredThinking as ThinkingMode
    : Object.prototype.hasOwnProperty.call(profile.config, 'thinking_enabled')
      ? (profile.config.thinking_enabled ? 'enabled' : 'disabled')
      : 'inherit'
  form.max_tokens = Number(profile.config.max_tokens ?? 0)
  form.context_window_tokens = Number(profile.config.context_window_tokens ?? 0)
  form.context_reserved_output_tokens = Number(profile.config.context_reserved_output_tokens ?? 0)
  form.timeout_seconds = Number(profile.config.timeout_seconds ?? 300)
  form.max_retries = Number(profile.config.max_retries ?? 2)
  form.dimension = profile.config.dimension ? Number(profile.config.dimension) : undefined
  form.batch_size = Number(profile.config.batch_size ?? 16)
  form.device = String(profile.config.device ?? 'cpu')
  form.candidate_count = Number(profile.config.candidate_count ?? 30)
  form.query_instruction = String(profile.config.query_instruction ?? defaultEmbeddingInstruction)
  form.instruction = String(profile.config.instruction ?? defaultRerankerInstruction)
  dialogVisible.value = true
}

function modelConfig() {
  if (form.task_type === 'chat') {
    return {
      temperature: form.temperature,
      thinking_mode: form.thinking_mode,
      max_tokens: form.max_tokens > 0 ? form.max_tokens : undefined,
      context_window_tokens: form.context_window_tokens > 0 ? form.context_window_tokens : undefined,
      context_reserved_output_tokens: form.context_reserved_output_tokens > 0 ? form.context_reserved_output_tokens : undefined,
      timeout_seconds: form.timeout_seconds,
      max_retries: form.max_retries
    }
  }
  if (form.task_type === 'embedding') {
    return {
      dimension: form.dimension || undefined,
      batch_size: form.batch_size,
      device: form.device,
      normalize: true,
      query_instruction: form.mode === 'local' ? form.query_instruction.trim() : undefined,
      timeout_seconds: form.timeout_seconds,
      max_retries: form.max_retries
    }
  }
  return {
    device: form.device,
    batch_size: form.batch_size,
    candidate_count: form.candidate_count,
    instruction: form.instruction,
    timeout_seconds: form.timeout_seconds
  }
}

async function saveProfile() {
  if (!form.name.trim()) return ElMessage.warning('请输入配置名称')
  saving.value = true
  try {
    const activeEmbeddingWasEdited = !!editingId.value
      && form.task_type === 'embedding'
      && profiles.value.some(item => item.id === editingId.value && item.is_active)
    const payload: any = {
      name: form.name.trim(),
      mode: form.mode,
      provider: form.provider,
      model_name: form.model_name.trim(),
      base_url: form.base_url.trim() || null,
      config: modelConfig(),
      enabled: form.enabled
    }
    if (form.api_key) payload.api_key = form.api_key
    if (form.task_type === 'chat' && form.mode === 'api' && form.proxy_url.trim()) {
      payload.proxy_url = form.proxy_url.trim()
    }
    if (editingId.value) {
      payload.clear_api_key = form.clear_api_key
      payload.clear_proxy_url = form.proxy_url.trim()
        ? false
        : (form.mode !== 'api' ? true : form.clear_proxy_url)
      await api.patch(`/system/models/${editingId.value}`, payload)
    } else {
      payload.task_type = form.task_type
      await api.post('/system/models', payload)
    }
    ElMessage.success('模型配置已保存')
    dialogVisible.value = false
    await load()
    if (activeEmbeddingWasEdited) {
      try {
        await ElMessageBox.confirm('当前 Embedding 配置已修改，旧向量已失效。是否现在重建知识库向量？', '重建向量索引', { type: 'warning' })
        await rebuildEmbeddings()
      } catch {
        ElMessage.info('可稍后点击“重建向量索引”')
      }
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function testProfile(profile: ModelProfile) {
  testingId.value = profile.id
  try {
    const { data } = await api.post(`/system/models/${profile.id}/test`)
    if (profile.task_type === 'embedding') {
      ElMessage.success(`连接正常，向量维度 ${data.dimension}`)
    } else if (profile.task_type === 'reranker') {
      ElMessage.success(data.disabled ? 'Reranker 已关闭' : 'Reranker 测试正常')
    } else {
      ElMessage.success(data.proxy_url_configured
        ? '模型连接正常（已使用配置代理，并跳过证书吊销检查）'
        : '模型连接正常（直连）')
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    testingId.value = ''
  }
}

async function pollJob(job: Job) {
  let current = job
  while (!['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(current.status)) {
    await new Promise(resolve => setTimeout(resolve, 1000))
    current = (await api.get(`/jobs/${job.id}`)).data
  }
  if (current.status !== 'COMPLETED') throw new Error(current.error_message || current.message || '任务未完成')
  return current
}

async function rebuildEmbeddings() {
  reindexing.value = true
  try {
    const { data } = await api.post('/knowledge/reindex')
    await pollJob(data)
    ElMessage.success('知识库向量索引重建完成')
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    reindexing.value = false
  }
}

async function activate(profile: ModelProfile) {
  try {
    const { data } = await api.post(`/system/models/${profile.id}/activate`)
    ElMessage.success(`已切换到 ${profile.name}`)
    await load()
    if (data.requires_reindex) {
      try {
        await ElMessageBox.confirm('Embedding 已切换。需要用新模型重建知识库向量，是否现在执行？', '重建向量索引', { type: 'warning' })
        await rebuildEmbeddings()
      } catch {
        ElMessage.info('可稍后在设置页点击“重建向量索引”')
      }
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function removeProfile(profile: ModelProfile) {
  try {
    await ElMessageBox.confirm(`确认删除“${profile.name}”？`, '删除模型配置', { type: 'warning' })
    await api.delete(`/system/models/${profile.id}`)
    ElMessage.success('模型配置已删除')
    await load()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

function saveKey() {
  localStorage.setItem('gw_ap_api_key', apiKey.value)
  ElMessage.success('前端 API Key 已保存到当前浏览器')
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">系统设置</h1>
      <el-button :loading="reindexing" @click="rebuildEmbeddings">重建向量索引</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-alert type="info" :closable="false" style="margin-bottom:16px">
      <template #title>模型密钥和含凭据的代理地址由后端加密保存，页面不会回显完整值。切换诊断模型和 Reranker 立即生效；切换 Embedding 后需要重建向量索引。</template>
    </el-alert>

    <el-card>
      <el-tabs v-model="activeTask">
        <el-tab-pane v-for="task in (['chat', 'embedding', 'reranker'] as ModelTask[])" :key="task" :label="taskLabels[task]" :name="task" />
      </el-tabs>
      <div class="toolbar">
        <el-button type="primary" @click="openCreate">添加 {{ taskLabels[activeTask] }}</el-button>
        <span v-if="activeTask === 'embedding'" class="muted">
          索引 {{ retrieval.embedding?.vector_count || 0 }} / {{ retrieval.embedding?.chunk_count || 0 }} 个知识分块
        </span>
      </div>
      <el-table :data="taskProfiles" stripe>
        <el-table-column label="状态" width="90">
          <template #default="scope"><el-tag v-if="scope.row.is_active" type="success">当前使用</el-tag><el-tag v-else type="info">备用</el-tag></template>
        </el-table-column>
        <el-table-column prop="name" label="配置名称" min-width="190" />
        <el-table-column label="运行方式" width="100"><template #default="scope">{{ { builtin: '内置', local: '本地', api: 'API' }[scope.row.mode as ModelMode] }}</template></el-table-column>
        <el-table-column label="适配器" min-width="180"><template #default="scope">{{ providerLabels[scope.row.provider] || scope.row.provider }}</template></el-table-column>
        <el-table-column prop="model_name" label="模型名/本地路径" min-width="220" show-overflow-tooltip />
        <el-table-column label="API Key" width="110"><template #default="scope">{{ scope.row.api_key_configured ? scope.row.api_key_hint || '已配置' : '—' }}</template></el-table-column>
        <el-table-column v-if="activeTask === 'chat'" label="模型代理" min-width="190" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.mode === 'api' && scope.row.proxy_url_configured ? scope.row.proxy_url_hint || '已配置' : '直连' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="scope">
            <el-button v-if="!scope.row.is_active" link type="primary" @click="activate(scope.row as ModelProfile)">切换使用</el-button>
            <el-button link :loading="testingId === scope.row.id" @click="testProfile(scope.row as ModelProfile)">测试</el-button>
            <el-button link @click="openEdit(scope.row as ModelProfile)">修改</el-button>
            <el-button v-if="!scope.row.is_active && !scope.row.config?.builtin" link type="danger" @click="removeProfile(scope.row as ModelProfile)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card data-testid="model-download-card" style="margin-top:16px">
      <template #header>本地模型权重下载</template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="该功能来自原 A.py 的文件清单、断点续传和进度逻辑；只下载权重到平台管理目录，不安装 Torch、Sentence Transformers，也不会在 FastAPI 进程中加载模型。"
        style="margin-bottom:16px"
      />
      <el-form inline>
        <el-form-item label="模型镜像">
          <el-select v-model="downloadForm.mirror_base" data-testid="model-download-mirror" style="width:260px">
            <el-option v-for="mirror in modelDownloads.mirrors" :key="mirror" :label="mirror" :value="mirror" />
          </el-select>
        </el-form-item>
        <el-form-item label="Revision">
          <el-input v-model="downloadForm.revision" data-testid="model-download-revision" style="width:160px" placeholder="main 或固定 revision" />
        </el-form-item>
        <el-form-item label="下载代理">
          <el-input
            v-model="downloadForm.proxy_url"
            data-testid="model-download-proxy"
            type="password"
            show-password
            autocomplete="off"
            style="width:320px"
            placeholder="留空直连，例如 http://proxy.corp:8080"
          />
        </el-form-item>
      </el-form>
      <div class="muted" style="margin-bottom:12px">
        下载根目录：<span class="mono">{{ modelDownloads.download_root }}</span>。Revision 会固定到镜像返回的 Commit；同模型任务串行执行，新 generation 完整校验后才切换，失败或取消继续使用上一版本。代理凭据只以密文保存在后台任务中，页面和任务结果不会回显；启用代理时跳过证书吊销检查，但仍验证证书链和主机名。
      </div>
      <el-table :data="modelDownloads.models" stripe>
        <el-table-column label="状态" width="120">
          <template #default="scope">
            <el-tag :type="scope.row.status === 'READY' ? 'success' : scope.row.status === 'PARTIAL' ? 'warning' : 'info'">
              {{ downloadStatus(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="模型" min-width="220" />
        <el-table-column prop="model_id" label="仓库 ID" min-width="250" show-overflow-tooltip />
        <el-table-column label="文件/大小" width="150">
          <template #default="scope">{{ scope.row.file_count }} / {{ formatBytes(scope.row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column prop="target_directory" label="当前版本目录" min-width="280" show-overflow-tooltip />
        <el-table-column label="下载进度" min-width="240">
          <template #default="scope">
            <template v-if="activeDownload(scope.row.model_id)">
              <el-progress :percentage="activeDownload(scope.row.model_id)?.progress || 0" :stroke-width="8" />
              <div class="muted">{{ activeDownload(scope.row.model_id)?.message }}</div>
            </template>
            <span v-else class="muted">{{ scope.row.completed_at ? `完成于 ${scope.row.completed_at}` : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="scope">
            <el-button
              type="primary"
              link
              :loading="startingDownloadId === scope.row.model_id"
              :disabled="!!activeDownload(scope.row.model_id)"
              @click="startModelDownload(scope.row as ModelDownloadItem)"
            >{{ scope.row.status === 'READY' ? '重新校验/下载' : '开始下载' }}</el-button>
            <el-button
              v-if="activeDownload(scope.row.model_id)"
              type="danger"
              link
              @click="cancelModelDownload(activeDownload(scope.row.model_id) as ModelDownloadJob)"
            >取消</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header>本地模型说明</template>
      <p class="muted">标准源码环境和 Win11 便携包仍不包含 Torch 或 Sentence Transformers。上方下载器只准备可供独立模型服务使用的 BGE/Qwen 权重；下载完成不等于平台已经安装推理能力。默认继续使用内置 Hashing Embedding并关闭 Reranker，需要这些权重时应由独立模型服务加载，再在模型网关中配置 API。标记为“高级”的本地配置只为既有、人工维护的源码环境保留。</p>
      <p class="muted">
        当前知识存储：{{ retrieval.knowledge_storage || '加载中' }}；
        方法派生关系 {{ retrieval.knowledge_graph?.derivations || 0 }}；
        代码符号/关系 {{ retrieval.code_graph?.symbols || 0 }}/{{ retrieval.code_graph?.relations || 0 }}；
        Commit {{ retrieval.commit_graph?.commits || 0 }}；
        任务记忆 {{ retrieval.memory?.items || 0 }}。
      </p>
    </el-card>

    <el-card style="margin-top:16px;max-width:760px">
      <template #header>后端 API 鉴权</template>
      <el-input v-model="apiKey" type="password" show-password placeholder="后端未配置 API_KEY 时可留空" />
      <el-button style="margin-top:12px" @click="saveKey">保存到浏览器</el-button>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="680px" destroy-on-close>
      <el-form label-width="130px">
        <el-form-item label="用途"><el-select v-model="form.task_type" :disabled="!!editingId" @change="changeTask"><el-option v-for="(label, value) in taskLabels" :key="value" :label="label" :value="value" /></el-select></el-form-item>
        <el-form-item label="配置名称"><el-input v-model="form.name" placeholder="例如：公司 Qwen Plus" /></el-form-item>
        <el-form-item label="运行方式"><el-radio-group v-model="form.mode" @change="updateProvider"><el-radio-button v-for="mode in allowedModes(form.task_type)" :key="mode" :value="mode">{{ { builtin: '内置', local: '本地', api: 'API' }[mode] }}</el-radio-button></el-radio-group></el-form-item>
        <el-alert v-if="form.mode === 'local'" type="warning" :closable="false" show-icon style="margin-bottom:16px" title="高级兼容模式：便携包不包含本地模型运行库；请优先使用独立模型服务的 API，避免把 Torch 安装进平台运行环境。" />
        <el-form-item label="适配器"><el-input :model-value="providerLabels[form.provider] || form.provider" disabled /></el-form-item>
        <el-form-item label="模型名/本地路径"><el-input v-model="form.model_name" :disabled="form.mode === 'builtin'" placeholder="模型名称或本地模型目录" /></el-form-item>
        <template v-if="form.mode === 'api'">
          <el-alert type="warning" :closable="false" style="margin-bottom:16px" title="API 模式会把当前用途所需的数据发送到该端点：诊断证据、知识分块或检索候选。请仅使用公司批准的模型服务。" />
          <el-form-item label="Base URL">
            <div style="width:100%">
              <el-input v-model="form.base_url" placeholder="Embedding/Chat 填到 /v1；Qwen Reranker 可填到 /compatible-api/v1" />
              <div class="muted" style="margin-top:6px">开发环境兼容 HTTP/HTTPS；私网地址启用 MODEL_ALLOW_PRIVATE_ENDPOINTS 后无需再配置 HTTP 白名单。</div>
            </div>
          </el-form-item>
          <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password :placeholder="editingId ? '留空则保留原密钥' : '仅发送并保存在后端'" /></el-form-item>
          <el-form-item v-if="editingId" label="清除原密钥"><el-switch v-model="form.clear_api_key" /></el-form-item>
          <template v-if="form.task_type === 'chat'">
            <el-form-item label="模型代理">
              <div style="width:100%">
                <el-input
                  v-model="form.proxy_url"
                  data-testid="model-proxy-url"
                  type="password"
                  show-password
                  autocomplete="off"
                  :placeholder="editingProfile?.proxy_url_configured ? '留空则保留原代理' : '留空直连，例如 http://proxy.corp:8080'"
                />
                <div class="muted" style="margin-top:6px">仅作用于此回答模型；可填写含认证信息的 HTTP/HTTPS 代理 URL，后端会加密保存。</div>
              </div>
            </el-form-item>
            <el-form-item v-if="editingId" label="清除原代理"><el-switch v-model="form.clear_proxy_url" data-testid="clear-model-proxy" /></el-form-item>
            <el-alert
              v-if="form.proxy_url || (editingProfile?.proxy_url_configured && !form.clear_proxy_url)"
              type="warning"
              :closable="false"
              style="margin-bottom:16px"
              title="代理启用时会强制跳过证书吊销检查，但仍严格验证证书链和目标主机名；不会关闭 TLS 验证。"
            />
          </template>
        </template>
        <template v-if="form.task_type === 'chat'">
          <el-form-item label="Temperature"><el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" /></el-form-item>
          <el-form-item label="Thinking">
            <div>
              <el-select v-model="form.thinking_mode" style="width:220px">
                <el-option label="跟随模型默认" value="inherit" />
                <el-option label="强制开启" value="enabled" />
                <el-option label="强制关闭" value="disabled" />
              </el-select>
              <div class="muted">GLM-5.1/5.2 选择“强制关闭”时会显式发送 thinking.type=disabled；智能日志筛查属于有界 JSON 提取，会始终关闭 Thinking，综合诊断仍采用此处设置。</div>
            </div>
          </el-form-item>
          <el-form-item label="最大输出 Tokens">
            <div>
              <el-input-number v-model="form.max_tokens" :min="0" :max="2000000" :step="1024" />
              <div class="muted">0 表示使用模型默认值；GLM-5.1/5.2 可填写 65536。</div>
            </div>
          </el-form-item>
          <el-form-item label="上下文窗口 Tokens">
            <div>
              <el-input-number v-model="form.context_window_tokens" :min="0" :max="10000000" :step="8192" />
              <div class="muted">0 使用后端默认 131072；应填写模型真实的输入+输出上下文窗口。</div>
            </div>
          </el-form-item>
          <el-form-item label="预留输出 Tokens">
            <div>
              <el-input-number v-model="form.context_reserved_output_tokens" :min="0" :max="2000000" :step="1024" />
              <div class="muted">0 优先沿用“最大输出 Tokens”；综合诊断会把剩余容量用于方法、日志证据和历史轨迹。</div>
            </div>
          </el-form-item>
        </template>
        <template v-if="form.task_type === 'embedding'">
          <el-form-item v-if="form.mode === 'api'" label="向量维度"><el-input-number v-model="form.dimension" :min="1" placeholder="留空使用模型默认值" /></el-form-item>
          <el-form-item v-if="form.mode === 'local'" label="运行设备"><el-select v-model="form.device"><el-option label="CPU" value="cpu"/><el-option label="CUDA" value="cuda"/></el-select></el-form-item>
          <el-form-item label="批量大小"><el-input-number v-model="form.batch_size" :min="1" :max="100" /></el-form-item>
          <el-form-item v-if="form.mode === 'local'" label="检索查询指令"><el-input v-model="form.query_instruction" type="textarea" :rows="2" /></el-form-item>
        </template>
        <template v-if="form.task_type === 'reranker'">
          <el-form-item v-if="form.mode === 'local'" label="运行设备"><el-select v-model="form.device"><el-option label="CPU" value="cpu"/><el-option label="CUDA" value="cuda"/></el-select></el-form-item>
          <el-form-item v-if="form.mode === 'local'" label="推理批量"><el-input-number v-model="form.batch_size" :min="1" :max="100" /></el-form-item>
          <el-form-item label="候选文档数"><el-input-number v-model="form.candidate_count" :min="5" :max="100" /></el-form-item>
          <el-form-item v-if="form.mode !== 'builtin'" label="排序指令"><el-input v-model="form.instruction" type="textarea" :rows="3" /></el-form-item>
        </template>
        <el-form-item v-if="form.mode === 'api'" label="超时秒数"><el-input-number v-model="form.timeout_seconds" :min="5" :max="600" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button data-testid="model-profile-save" type="primary" :loading="saving" @click="saveProfile">保存</el-button></template>
    </el-dialog>
  </div>
</template>
