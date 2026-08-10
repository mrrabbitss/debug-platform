<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { Job, ModelMode, ModelProfile, ModelTask } from '../types'

const profiles = ref<ModelProfile[]>([])
const retrieval = ref<any>({})
const agentRuntime = ref<any>({})
const localModels = ref<any[]>([])
const scanningModels = ref(false)
const localModelActionId = ref('')
const activeTask = ref<ModelTask>('chat')
const dialogVisible = ref(false)
const editingId = ref<string | null>(null)
const saving = ref(false)
const testingId = ref('')
const reindexing = ref(false)
const apiKey = ref(localStorage.getItem('gw_ap_api_key') || '')
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
  enabled: true,
  temperature: 0.1,
  timeout_seconds: 120,
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

const taskLabels: Record<ModelTask, string> = {
  chat: '诊断大模型',
  embedding: 'Embedding 模型',
  reranker: 'Reranker 模型'
}

const providerLabels: Record<string, string> = {
  mock: '规则引擎 / Mock',
  openai_compatible: 'OpenAI-Compatible API',
  hashing: '内置字符向量',
  sentence_transformers: '本地 Sentence Transformers',
  disabled: '不使用 Reranker',
  qwen_rerank_api: 'Qwen Rerank API',
  transformers_local: '本地 Transformers Chat',
  transformers_sequence_classifier: '本地 Transformers Sequence Classifier'
}

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}

async function load() {
  const [modelResponse, retrievalResponse, localResponse, agentRuntimeResponse] = await Promise.all([
    api.get('/system/models'),
    api.get('/system/retrieval'),
    api.get('/system/local-models'),
    api.get('/system/agent-runtime')
  ])
  profiles.value = modelResponse.data
  retrieval.value = retrievalResponse.data
  localModels.value = localResponse.data
  agentRuntime.value = agentRuntimeResponse.data
}

function providerFor(task: ModelTask, mode: ModelMode) {
  if (task === 'chat') return mode === 'builtin' ? 'mock' : mode === 'local' ? 'transformers_local' : 'openai_compatible'
  if (task === 'embedding') {
    return mode === 'builtin' ? 'hashing' : mode === 'local' ? 'sentence_transformers' : 'openai_compatible'
  }
  return mode === 'builtin' ? 'disabled' : mode === 'local' ? 'sentence_transformers' : 'qwen_rerank_api'
}

function allowedModes(task: ModelTask): ModelMode[] {
  return ['builtin', 'local', 'api']
}

function updateProvider() {
  if (!allowedModes(form.task_type).includes(form.mode)) form.mode = 'api'
  form.provider = providerFor(form.task_type, form.mode)
  if (form.provider === 'hashing') form.model_name = 'hashing-char-384'
  if (form.provider === 'mock') form.model_name = 'rule-engine'
  if (form.provider === 'disabled') form.model_name = 'disabled'
  if (form.provider === 'qwen_rerank_api' && !form.model_name) form.model_name = 'qwen3-rerank'
}

function resetForm(task: ModelTask) {
  editingId.value = null
  form.name = ''
  form.task_type = task
  form.mode = task === 'chat' ? 'api' : 'local'
  form.model_name = ''
  form.base_url = ''
  form.api_key = ''
  form.clear_api_key = false
  form.enabled = true
  form.temperature = 0.1
  form.timeout_seconds = 120
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
  form.enabled = profile.enabled
  form.temperature = Number(profile.config.temperature ?? 0.1)
  form.timeout_seconds = Number(profile.config.timeout_seconds ?? 120)
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
    return { temperature: form.temperature, timeout_seconds: form.timeout_seconds, max_retries: form.max_retries, device: form.mode === 'local' ? form.device : undefined, max_new_tokens: form.mode === 'local' ? 512 : undefined }
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
    if (editingId.value) {
      payload.clear_api_key = form.clear_api_key
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
      ElMessage.success('模型连接正常')
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

async function scanLocalModels() {
  scanningModels.value = true
  try {
    const { data } = await api.post('/system/local-models/scan?use_llm=true')
    localModels.value = data.models
    ElMessage.success(`扫描完成：发现 ${data.count} 个本地模型候选`)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    scanningModels.value = false
  }
}

async function validateLocalModel(model: any) {
  localModelActionId.value = model.id
  try {
    const { data } = await api.post(`/system/local-models/${model.id}/validate`, { device: 'cpu' })
    ElMessage.success(data.validation_status === 'VALIDATED' ? '本地模型真实加载验证通过' : `验证状态：${data.validation_status}`)
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    localModelActionId.value = ''
  }
}

async function activateLocalModel(model: any) {
  if (model.validation_status !== 'VALIDATED') return ElMessage.warning('请先完成真实加载验证')
  localModelActionId.value = model.id
  try {
    const { data } = await api.post(`/system/local-models/${model.id}/activate`, { device: 'cpu', force: false })
    ElMessage.success(`已激活 ${model.name}`)
    await load()
    if (data.requires_reindex) ElMessage.warning('Embedding 已切换，请重建知识库向量索引')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    localModelActionId.value = ''
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
      <template #title>模型密钥由后端加密保存，页面不会回显完整 API Key。切换诊断模型和 Reranker 立即生效；切换 Embedding 后需要重建向量索引。</template>
    </el-alert>

    <el-card style="margin-bottom:16px">
      <template #header>Agent Runtime</template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="Agent Mode">
          <el-tag :type="agentRuntime.agent_mode === 'external' ? 'success' : 'info'">{{ agentRuntime.agent_mode || 'loading' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Optional Web">{{ agentRuntime.frontend_available ? '已构建' : '未构建' }}</el-descriptions-item>
        <el-descriptions-item label="Runtime" :span="2">{{ agentRuntime.reasoning_contract || '加载中' }}</el-descriptions-item>
      </el-descriptions>
      <p v-if="agentRuntime.agent_mode === 'external'" class="muted" style="margin-top:12px">
        External Agent Mode 下平台不会再调用第二个 Chat LLM 完成诊断 synthesis / case chat / patch；Claude Code 或 OpenCode 使用 Evidence Bundle 完成最终推理和源码修改。Embedding/Reranker 仍属于检索层。
      </p>
    </el-card>

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

    <el-card style="margin-top:16px">
      <template #header>本地模型自动发现</template>
      <div class="toolbar">
        <el-button type="primary" :loading="scanningModels" @click="scanLocalModels">扫描 models / MODEL_ROOTS</el-button>
        <span class="muted">不依赖旧下载脚本。先读安全元数据确定性分类；低置信度时才允许 Chat LLM 复核元数据，模型权重不会发送给 LLM。</span>
      </div>
      <el-table :data="localModels" stripe style="margin-top:12px">
        <el-table-column prop="name" label="模型" min-width="180" />
        <el-table-column prop="task_type" label="用途" width="100" />
        <el-table-column prop="loader" label="Loader" min-width="210" show-overflow-tooltip />
        <el-table-column label="识别" width="150"><template #default="scope">{{ scope.row.classification_source }} / {{ Math.round(Number(scope.row.confidence || 0) * 100) }}%</template></el-table-column>
        <el-table-column prop="validation_status" label="真实验证" width="120" />
        <el-table-column prop="path" label="目录" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作" width="170" fixed="right"><template #default="scope">
          <el-button link :loading="localModelActionId === scope.row.id" @click="validateLocalModel(scope.row)">验证</el-button>
          <el-button link type="primary" :disabled="scope.row.validation_status !== 'VALIDATED'" @click="activateLocalModel(scope.row)">激活</el-button>
        </template></el-table-column>
      </el-table>
      <p class="muted" style="margin-top:12px">
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
        <el-form-item label="适配器"><el-input :model-value="providerLabels[form.provider] || form.provider" disabled /></el-form-item>
        <el-form-item label="模型名/本地路径"><el-input v-model="form.model_name" :disabled="form.mode === 'builtin'" placeholder="模型名称或本地模型目录" /></el-form-item>
        <template v-if="form.mode === 'api'">
          <el-alert type="warning" :closable="false" style="margin-bottom:16px" title="API 模式会把当前用途所需的数据发送到该端点：诊断证据、知识分块或检索候选。请仅使用公司批准的模型服务。" />
          <el-form-item label="Base URL"><el-input v-model="form.base_url" placeholder="Embedding/Chat 填到 /v1；Qwen Reranker 可填到 /compatible-api/v1" /></el-form-item>
          <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password :placeholder="editingId ? '留空则保留原密钥' : '仅发送并保存在后端'" /></el-form-item>
          <el-form-item v-if="editingId" label="清除原密钥"><el-switch v-model="form.clear_api_key" /></el-form-item>
        </template>
        <template v-if="form.task_type === 'chat'">
          <el-form-item v-if="form.mode === 'local'" label="运行设备"><el-select v-model="form.device"><el-option label="CPU" value="cpu"/><el-option label="CUDA" value="cuda"/></el-select></el-form-item>
          <el-form-item label="Temperature"><el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" /></el-form-item>
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
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveProfile">保存</el-button></template>
    </el-dialog>
  </div>
</template>
