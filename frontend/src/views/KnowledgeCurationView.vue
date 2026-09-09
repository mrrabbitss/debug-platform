<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { useKnowledgeCurationTask } from '../composables/useKnowledgeCurationTask'
import CurationSourcePreviewDialog from '../components/curation/CurationSourcePreviewDialog.vue'
import ChatModelSelect from '../components/ChatModelSelect.vue'
import ModelTaskProgress from '../components/common/ModelTaskProgress.vue'
import { contributionsApi } from '../api/knowledgeContributions'
import { useKnowledgeCurationPresentation } from '../composables/useKnowledgeCurationPresentation'
import { knowledgeDeviceTypeOptions } from '../constants/knowledge'
import {
  confirmKnowledgeCuration,
  createKnowledgeCuration,
  deleteKnowledgeCuration,
  listKnowledgeCurations,
  loadCurationOptions,
  previewKnowledgeCurationSource,
  refineKnowledgeCuration,
  restoreKnowledgeCurationRevision,
  retryKnowledgeCuration,
  saveKnowledgeCurationDraft
} from '../api/knowledgeCuration'
import type {
  KnowledgeCategory,
  KnowledgeCurationRevision,
  KnowledgeCurationSession,
  KnowledgeCurationSource,
  ModelProfile
} from '../types'

type FolderFile = File & { webkitRelativePath?: string }

const router = useRouter()
const {
  extractionMethodLabel,
  formatBytes,
  skipReasonLabel,
  sourceRoleLabel,
  statusLabel,
  statusType
} = useKnowledgeCurationPresentation()
const sessions = ref<KnowledgeCurationSession[]>([])
const current = ref<KnowledgeCurationSession | null>(null)
const modelProfiles = ref<ModelProfile[]>([])
const categories = ref<KnowledgeCategory[]>([])
const loading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const createDialog = ref(false)
const previewDialog = ref(false)
const selectedFiles = ref<FolderFile[]>([])
const draftEditor = ref('')
const draftTitle = ref('')
const chatInstruction = ref('')
const sourcePreview = ref('')
const sourcePreviewTitle = ref('')
const previewSourceItem = ref<KnowledgeCurationSource | null>(null)
const previewStartLine = ref(1)
const previewHasMore = ref(false)
const activeTab = ref('draft')
const principalId = ref('')
const {
  activeJob,
  jobPollError,
  isWorking,
  progressPhase,
  loadSession,
  trackCurationJob,
  cancelModelTask,
  retryModelTask,
  dismissModelTask,
  resetModelTask
} = useKnowledgeCurationTask({
  current,
  principalId,
  loading,
  draftEditor,
  draftTitle,
  loadSessions,
  errorText
})

const createForm = reactive({
  title_hint: '',
  category_id: '',
  device_type: '',
  device_model: '',
  firmware_range: '',
  module: '',
  trust_level: 'MEDIUM',
  confidentiality: 'RESTRICTED',
  model_profile_id: '',
  consent_model_egress: true
})

const eligibleModels = computed(() => modelProfiles.value.filter(
  item => item.task_type === 'chat' && item.enabled && item.provider !== 'mock'
))
const folderName = computed(() => {
  const path = selectedFiles.value[0]?.webkitRelativePath || ''
  return path.split('/')[0] || '所选文件'
})
const selectedBytes = computed(() => selectedFiles.value.reduce((sum, item) => sum + item.size, 0))
const draftDirty = computed(() => Boolean(
  current.value && (
    draftEditor.value !== (current.value.draft_markdown || '')
    || draftTitle.value !== current.value.draft_title
  )
))
function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}

async function loadModelsAndCategories() {
  const [options, meResponse] = await Promise.all([loadCurationOptions(), api.get<{ id: string }>('/system/me')])
  modelProfiles.value = options.models
  categories.value = options.categories
  principalId.value = meResponse.data.id
}

async function loadSessions(preferredId?: string, isCurrent?: () => boolean) {
  const items = await listKnowledgeCurations()
  if (isCurrent && !isCurrent()) return
  sessions.value = items
  const target = preferredId || current.value?.id || sessions.value[0]?.id
  if (target && (!current.value || current.value.id !== target)) await loadSession(target)
}

async function selectSession(session: KnowledgeCurationSession) {
  if (draftDirty.value) {
    try {
      await ElMessageBox.confirm('当前草稿有未保存修改，切换后会丢失。仍然切换？', '未保存修改', {
        type: 'warning'
      })
    } catch {
      return
    }
  }
  resetModelTask()
  await loadSession(session.id)
}

function resetCreateForm() {
  selectedFiles.value = []
  uploadProgress.value = 0
  createForm.title_hint = ''
  createForm.category_id = categories.value.find(item => item.code === 'history.cases')?.id || ''
  createForm.device_type = ''
  createForm.device_model = ''
  createForm.firmware_range = ''
  createForm.module = ''
  createForm.trust_level = 'MEDIUM'
  createForm.confidentiality = 'RESTRICTED'
  createForm.model_profile_id = ''
  createForm.consent_model_egress = true
}

function openCreate() {
  resetCreateForm()
  createDialog.value = true
}

function selectFolder(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFiles.value = Array.from(input.files || []) as FolderFile[]
}

async function createSession() {
  if (!selectedFiles.value.length) return ElMessage.warning('请选择包含案例材料的文件夹')
  if (!createForm.consent_model_egress) return ElMessage.warning('请确认脱敏证据可以发送到所选模型 API')
  uploading.value = true
  uploadProgress.value = 0
  try {
    const response = await createKnowledgeCuration({
      files: selectedFiles.value,
      relativePaths: selectedFiles.value.map(file => file.webkitRelativePath || file.name),
      ...createForm
    }, event => {
        if (event.total) uploadProgress.value = Math.round(event.loaded * 100 / event.total)
    })
    const session = response.session
    createDialog.value = false
    current.value = session
    trackCurationJob(response.job, session.id)
    jobPollError.value = ''
    draftEditor.value = ''
    draftTitle.value = session.title_hint
    ElMessage.success('文件夹已上传，正在后台调用大模型生成案例初稿')
    await loadSessions(session.id)
    await loadSession(session.id)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    uploading.value = false
  }
}

async function saveDraft() {
  if (!current.value) return
  if (!draftEditor.value.trim()) return ElMessage.warning('Markdown 草稿不能为空')
  saving.value = true
  try {
    const response = await saveKnowledgeCurationDraft(current.value.id, {
      title: draftTitle.value,
      markdown: draftEditor.value,
      change_summary: '工程师手工校正案例草稿',
      expected_draft_version: current.value.draft_version
    })
    current.value = response
    draftEditor.value = response.draft_markdown || ''
    draftTitle.value = response.draft_title
    ElMessage.success('人工修改已保存为新的草稿版本')
    await loadSessions(current.value!.id)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function sendCorrection() {
  if (isWorking.value || saving.value) return
  if (!current.value) return
  const instruction = chatInstruction.value.trim()
  if (!instruction) return ElMessage.warning('请输入要讨论或纠正的内容')
  if (draftDirty.value) return ElMessage.warning('请先保存右侧人工修改，再与模型继续对话')
  saving.value = true
  try {
    const response = await refineKnowledgeCuration(
      current.value.id,
      instruction,
      current.value.draft_version
    )
    trackCurationJob(response)
    jobPollError.value = ''
    chatInstruction.value = ''
    ElMessage.success('修正任务已进入后台队列')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function restoreRevision(revision: KnowledgeCurationRevision) {
  if (!current.value || revision.version === current.value.draft_version) return
  try {
    await ElMessageBox.confirm(
      `恢复 v${revision.version} 的正文？系统会创建新版本，不会覆盖现有历史。`,
      '恢复草稿版本',
      { type: 'warning' }
    )
    const response = await restoreKnowledgeCurationRevision(
      current.value.id,
      revision,
      current.value.draft_version
    )
    current.value = response
    draftEditor.value = response.draft_markdown || ''
    draftTitle.value = response.draft_title
    ElMessage.success('已从历史内容创建新的草稿版本')
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

async function confirmSession() {
  if (!current.value) return
  if (draftDirty.value) return ElMessage.warning('请先保存当前人工修改')
  if (!current.value.validation?.confirmable) return ElMessage.warning('当前草稿仍有结构或来源引用问题')
  try {
    await ElMessageBox.confirm(
      '确认 Markdown 与来源引用后，将提交给专家或管理员审核；审核发布成功后才参与诊断检索。',
      '提交提炼结果审核',
      { type: 'warning', confirmButtonText: '确认并提交审核', cancelButtonText: '继续检查' }
    )
    saving.value = true
    const response = await confirmKnowledgeCuration(
      current.value.id,
      current.value.draft_version
    )
    current.value = response.session
    if (response.contribution) {
      const contribution = response.contribution.status === 'DRAFT' ? await contributionsApi.submit(response.contribution) : response.contribution
      ElMessage.success('提炼结果已提交，可在“我的提交”查看审核进度')
      await router.push({ path: '/knowledge', query: { tab: 'submissions', contribution: contribution.id } })
    } else { ElMessage.success('已保存提炼结果，请在“我的提交”继续核对'); await loadSessions(current.value!.id) }
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function retrySession() {
  if (!current.value) return
  saving.value = true
  try {
    const response = await retryKnowledgeCuration(current.value.id)
    current.value = response.session
    trackCurationJob(response.job, response.session.id)
    jobPollError.value = ''
    ElMessage.success('已重新提交模型提炼任务')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function deleteSession() {
  if (!current.value) return
  try {
    await ElMessageBox.confirm(
      `确认删除提炼会话“${current.value.draft_title || current.value.title_hint || current.value.id}”及其本地来源副本？`,
      '删除提炼会话',
      { type: 'warning' }
    )
    await deleteKnowledgeCuration(current.value.id)
    resetModelTask()
    current.value = null
    draftEditor.value = ''
    await loadSessions()
    ElMessage.success('未入库的提炼会话和来源副本已删除')
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

async function openSourcePreview(source: KnowledgeCurationSource, startLine = 1) {
  if (!current.value) return
  previewSourceItem.value = source
  previewStartLine.value = startLine
  try {
    const response = await previewKnowledgeCurationSource(
      current.value.id,
      source.id,
      startLine
    )
    sourcePreview.value = response.text
    sourcePreviewTitle.value = `${source.source_ref} · ${source.relative_path} · L${startLine}`
    previewHasMore.value = Boolean(response.has_more)
    previewDialog.value = true
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function initialize() {
  loading.value = true
  try {
    await loadModelsAndCategories()
    await loadSessions()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

onMounted(initialize)
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">AI 案例提炼工作台</h1>
      <el-button @click="router.push('/knowledge?tab=submissions')">返回我的提交</el-button>
      <el-button data-testid="curation-open-create" type="primary" :disabled="!eligibleModels.length" @click="openCreate">选择文件夹并提炼</el-button>
      <el-button @click="loadSessions(current?.id)">刷新</el-button>
    </div>

    <el-alert
      v-if="!eligibleModels.length"
      title="尚未配置可用的大模型 API。请先在“系统设置 → 诊断大模型”中新增、测试并启用模型。"
      type="warning"
      :closable="false"
      style="margin-bottom:16px"
    />
    <el-alert
      title="原始文件只保存在本地存储；模型 API 仅接收脱敏、限长并带来源行号的证据摘要。模型内容不会自动入库，必须经过对话纠错和人工确认。"
      type="info"
      :closable="false"
      style="margin-bottom:16px"
    />

    <div class="curation-layout">
      <el-card class="session-panel">
        <template #header><strong>提炼会话</strong></template>
        <el-empty v-if="!sessions.length" description="还没有提炼会话" />
        <div
          v-for="session in sessions"
          :key="session.id"
          class="session-item"
          :class="{ selected: current?.id === session.id }"
          @click="selectSession(session)"
        >
          <div class="session-title">{{ session.draft_title || session.title_hint || '未命名案例' }}</div>
          <div class="session-meta">
            <el-tag size="small" :type="statusType(session.status)">{{ statusLabel(session.status) }}</el-tag>
            <span>v{{ session.draft_version }}</span>
            <span>{{ session.source_count }} 文件</span>
          </div>
        </div>
      </el-card>

      <el-card v-loading="loading" class="workspace-panel">
        <el-empty v-if="!current" description="选择或创建一个提炼会话" />
        <template v-else>
          <div class="workspace-header">
            <div>
              <h2>{{ current.draft_title || current.title_hint || current.id }}</h2>
              <div class="muted">
                {{ current.model_snapshot?.profile_name || current.model_snapshot?.model || '未知模型' }} ·
                {{ current.source_count }} 个来源 · 草稿 v{{ current.draft_version }}
              </div>
            </div>
            <el-tag data-testid="curation-status" :data-status="current.status" :type="statusType(current.status)">{{ statusLabel(current.status) }}</el-tag>
          </div>

          <el-alert
            v-if="isWorking"
            :title="current.status === 'CONFIRMING' ? '正在建立知识库草稿，请稍候。' : '后台正在读取来源并生成初稿，页面会自动刷新。'"
            type="info"
            show-icon
            :closable="false"
            style="margin-bottom:16px"
          />
          <ModelTaskProgress v-if="activeJob" :job="activeJob" :phase="progressPhase" test-id="knowledge-curation-model-progress">
            <template #actions>
              <el-button v-if="['QUEUED','RUNNING'].includes(activeJob.status)" size="small" type="warning" @click="cancelModelTask">取消本轮</el-button>
              <el-button v-if="['FAILED','CANCELLED','DEAD_LETTER'].includes(activeJob.status)" size="small" type="primary" @click="retryModelTask">重试本轮</el-button>
              <el-button v-if="['COMPLETED','FAILED','CANCELLED','DEAD_LETTER'].includes(activeJob.status)" size="small" @click="dismissModelTask">关闭记录</el-button>
            </template>
          </ModelTaskProgress>
          <el-alert v-if="jobPollError" type="warning" :title="jobPollError" :closable="false" style="margin-bottom:16px" />
          <el-alert
            v-if="current.status === 'FAILED'"
            :title="current.error_message || '模型提炼失败'"
            type="error"
            :closable="false"
            style="margin-bottom:16px"
          >
            <template #default><el-button size="small" :loading="saving" @click="retrySession">重新提炼</el-button></template>
          </el-alert>
          <el-alert
            v-if="current.status === 'CONFIRMED'"
            title="提炼结果已保存。请在“我的提交”查看对应草稿或审核进度；审核发布前不会共享。"
            type="success"
            :closable="false"
            style="margin-bottom:16px"
          >
            <template #default><el-button size="small" type="success" @click="router.push('/knowledge?tab=submissions')">查看我的提交</el-button></template>
          </el-alert>

          <el-tabs v-if="current.draft_version > 0" v-model="activeTab">
            <el-tab-pane label="草稿与对话纠错" name="draft">
              <div class="review-summary">
                <el-tag :type="current.validation?.confirmable ? 'success' : 'warning'">
                  {{ current.validation?.confirmable ? '满足人工确认条件' : '仍需校正' }}
                </el-tag>
                <span>来源引用 {{ current.validation?.citation_count || 0 }} 处</span>
                <span>结构完整度 {{ Math.round((current.validation?.structure?.completeness || 0) * 100) }}%</span>
                <el-tag v-if="draftDirty" type="danger">有未保存修改</el-tag>
              </div>
              <el-alert
                v-if="current.validation?.warnings?.length"
                :title="current.validation.warnings.join('；')"
                type="warning"
                :closable="false"
                style="margin-bottom:12px"
              />
              <el-alert
                v-if="current.open_questions?.length"
                :title="`待确认问题：${current.open_questions.join('；')}`"
                type="info"
                :closable="false"
                style="margin-bottom:12px"
              />

              <div class="editor-chat-grid">
                <section>
                  <div class="section-toolbar">
                    <strong>Markdown 草稿</strong>
                    <el-input v-model="draftTitle" size="small" style="width:320px" :disabled="current.status !== 'REVIEWING'" />
                    <el-button
                      type="primary"
                      :disabled="current.status !== 'REVIEWING' || !draftDirty"
                      :loading="saving"
                      @click="saveDraft"
                    >保存人工修改</el-button>
                  </div>
                  <el-input
                    data-testid="curation-draft-editor"
                    v-model="draftEditor"
                    type="textarea"
                    :rows="30"
                    resize="vertical"
                    :disabled="current.status !== 'REVIEWING'"
                    class="markdown-editor"
                  />
                </section>

                <section class="chat-panel">
                  <strong>与模型讨论并纠错</strong>
                  <div class="message-list">
                    <div v-for="message in current.messages || []" :key="message.id" class="message" :class="message.role">
                      <div class="message-role">{{ message.role === 'user' ? '工程师' : message.role === 'assistant' ? '模型' : '系统' }} · v{{ message.draft_version || '-' }}</div>
                      <div class="message-content">{{ message.content }}</div>
                    </div>
                  </div>
                  <el-input
                    data-testid="curation-chat-instruction"
                    v-model="chatInstruction"
                    type="textarea"
                    :rows="5"
                    :disabled="current.status !== 'REVIEWING'"
                    placeholder="例如：日志第 20 行只是现象，不是根因；请依据 SRC-0002 的分析结论修正错误定位，并保留证据引用。"
                    @keyup.ctrl.enter="sendCorrection"
                  />
                  <el-button
                    data-testid="curation-send-correction"
                    type="primary"
                    style="width:100%;margin-top:8px"
                    :disabled="current.status !== 'REVIEWING' || draftDirty || isWorking"
                    :loading="saving"
                    @click="sendCorrection"
                  >发送并生成下一版</el-button>
                </section>
              </div>

              <div class="confirm-bar">
                <el-button
                  data-testid="curation-confirm"
                  type="success"
                  size="large"
                  :disabled="current.status !== 'REVIEWING' || draftDirty || !current.validation?.confirmable"
                  :loading="saving"
                  @click="confirmSession"
                >确认并提交专家审核</el-button>
                <span class="muted">加入后仍需走“提交审核 → 发布”流程，发布前不会参与检索。</span>
              </div>
            </el-tab-pane>

            <el-tab-pane :label="`来源文件（${current.source_count}）`" name="sources">
              <el-table :data="current.sources || []" stripe>
                <el-table-column prop="source_ref" label="引用编号" width="115" />
                <el-table-column prop="relative_path" label="相对路径" min-width="300" show-overflow-tooltip />
                <el-table-column label="类型" width="100"><template #default="scope">{{ sourceRoleLabel(scope.row.source_role) }}</template></el-table-column>
                <el-table-column label="大小" width="100"><template #default="scope">{{ formatBytes(scope.row.size_bytes) }}</template></el-table-column>
                <el-table-column prop="line_count" label="行数" width="90" />
                <el-table-column label="正文提取" width="155">
                  <template #default="scope">
                    <el-tooltip v-if="!scope.row.included" :content="skipReasonLabel(scope.row.skip_reason)">
                      <el-tag type="warning">已跳过</el-tag>
                    </el-tooltip>
                    <el-tooltip v-else-if="scope.row.extraction_truncated" content="正文已达到安全提取上限；请核对提取预览">
                      <el-tag type="warning">{{ extractionMethodLabel(scope.row as KnowledgeCurationSource) }}</el-tag>
                    </el-tooltip>
                    <el-tag v-else type="success">{{ extractionMethodLabel(scope.row as KnowledgeCurationSource) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="90"><template #default="scope"><el-button :data-testid="`source-preview-${scope.row.source_ref}`" link type="primary" :disabled="!scope.row.line_count" @click="openSourcePreview(scope.row as KnowledgeCurationSource)">查看</el-button></template></el-table-column>
              </el-table>
            </el-tab-pane>

            <el-tab-pane :label="`草稿版本（${current.revisions?.length || 0}）`" name="revisions">
              <el-table :data="current.revisions || []" stripe>
                <el-table-column label="版本" width="80"><template #default="scope">v{{ scope.row.version }}</template></el-table-column>
                <el-table-column prop="change_summary" label="变更说明" min-width="260" />
                <el-table-column prop="created_by" label="操作者" width="150" />
                <el-table-column prop="created_at" label="时间" width="190" />
                <el-table-column label="内容哈希" width="180"><template #default="scope"><span class="mono">{{ scope.row.content_hash.slice(0, 16) }}</span></template></el-table-column>
                <el-table-column label="操作" width="90"><template #default="scope"><el-button link type="warning" :disabled="scope.row.version === current?.draft_version || current?.status !== 'REVIEWING'" @click="restoreRevision(scope.row as KnowledgeCurationRevision)">恢复</el-button></template></el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>

          <div v-if="current.status !== 'CONFIRMED' && !isWorking" class="danger-zone">
            <el-button type="danger" plain @click="deleteSession">删除未入库会话及来源副本</el-button>
          </div>
        </template>
      </el-card>
    </div>

    <el-dialog v-model="createDialog" title="从文件夹提炼故障案例" width="760px" destroy-on-close>
      <el-alert
        title="请选择同时包含日志、错误现象、人工分析和解决方案的目录。支持文本、无后缀文本、HTML/HTM、Word DOCX 和带文本层的 PDF；其他二进制文件会保留在本地但不发送给模型。"
        type="info"
        :closable="false"
        style="margin-bottom:16px"
      />
      <el-form label-width="120px">
        <el-form-item label="来源文件夹">
          <div>
            <input data-testid="curation-folder-input" type="file" webkitdirectory directory multiple @change="selectFolder" />
            <div v-if="selectedFiles.length" class="muted" style="margin-top:6px">
              {{ folderName }}：{{ selectedFiles.length }} 个文件，{{ formatBytes(selectedBytes) }}
            </div>
          </div>
        </el-form-item>
        <el-form-item label="标题提示"><el-input v-model="createForm.title_hint" placeholder="例如：AP 认证超时与共享密钥不一致" /></el-form-item>
        <el-form-item label="提炼模型">
          <ChatModelSelect :model-value="createForm.model_profile_id || null" @update:model-value="value=>createForm.model_profile_id=value || ''" :models="eligibleModels.map(item=>({...item,active:item.is_active}))" label="提炼模型" />
          <p class="field-hint">默认使用系统设置中的个人选择，未选择时跟随共享默认。</p>
        </el-form-item>
        <el-form-item label="知识分类">
          <el-select v-model="createForm.category_id" filterable style="width:100%">
            <el-option v-for="category in categories.filter(item => item.active)" :key="category.id" :label="category.name" :value="category.id" />
          </el-select>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="设备类型"><el-select v-model="createForm.device_type" clearable><el-option v-for="item in knowledgeDeviceTypeOptions" :key="item.value" :label="item.label" :value="item.value"/></el-select></el-form-item>
          <el-form-item label="模块"><el-input v-model="createForm.module" placeholder="WLAN/WAN/PON/OMCI" /></el-form-item>
          <el-form-item label="设备型号"><el-input v-model="createForm.device_model" /></el-form-item>
          <el-form-item label="固件范围"><el-input v-model="createForm.firmware_range" /></el-form-item>
          <el-form-item label="可信级别"><el-select v-model="createForm.trust_level"><el-option label="高" value="HIGH"/><el-option label="中" value="MEDIUM"/><el-option label="低" value="LOW"/></el-select></el-form-item>
          <el-form-item label="可见级别"><el-select v-model="createForm.confidentiality"><el-option label="受限" value="RESTRICTED"/><el-option label="内部" value="INTERNAL"/><el-option label="公开" value="PUBLIC"/></el-select></el-form-item>
        </div>
        <el-form-item label="模型数据出站">
          <el-checkbox data-testid="curation-egress-consent" v-model="createForm.consent_model_egress">
            我确认脱敏、限长的来源证据可以发送到所选模型 API；原始文件不会直接发送
          </el-checkbox>
        </el-form-item>
        <el-progress v-if="uploading" :percentage="uploadProgress" />
      </el-form>
      <template #footer>
        <el-button @click="createDialog=false">取消</el-button>
        <el-button data-testid="curation-submit" type="primary" :loading="uploading" @click="createSession">上传并开始提炼</el-button>
      </template>
    </el-dialog>

    <CurationSourcePreviewDialog
      v-model="previewDialog"
      :title="sourcePreviewTitle"
      :text="sourcePreview"
      :start-line="previewStartLine"
      :has-more="previewHasMore"
      :has-source="Boolean(previewSourceItem)"
      @navigate="line => previewSourceItem && openSourcePreview(previewSourceItem, line)"
    />
  </div>
</template>

<style scoped>
.curation-layout { display: grid; grid-template-columns: 310px minmax(0, 1fr); gap: 16px; align-items: start; }
.session-panel { min-height: 680px; }
.workspace-panel { min-height: 680px; }
.session-item { padding: 12px; margin-bottom: 8px; border: 1px solid #ebeef5; border-radius: 6px; cursor: pointer; }
.session-item:hover, .session-item.selected { border-color: #409eff; background: #ecf5ff; }
.session-title { font-weight: 600; margin-bottom: 8px; word-break: break-word; }
.session-meta { display: flex; align-items: center; gap: 8px; color: #909399; font-size: 12px; }
.workspace-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; }
.workspace-header h2 { margin: 0 0 6px; }
.review-summary { display: flex; gap: 14px; align-items: center; margin-bottom: 12px; }
.editor-chat-grid { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(340px, .75fr); gap: 16px; }
.section-toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; }
.section-toolbar strong { margin-right: auto; }
.markdown-editor :deep(textarea) { font-family: Consolas, 'Courier New', monospace; line-height: 1.55; }
.chat-panel { display: flex; flex-direction: column; min-height: 680px; }
.message-list { flex: 1; max-height: 545px; overflow: auto; margin: 10px 0; padding: 10px; background: #f5f7fa; border-radius: 6px; }
.message { margin-bottom: 12px; padding: 10px; border-radius: 6px; background: white; }
.message.user { margin-left: 35px; background: #ecf5ff; }
.message.assistant { margin-right: 35px; }
.message.system { color: #606266; border: 1px dashed #c0c4cc; }
.message-role { font-size: 12px; color: #909399; margin-bottom: 5px; }
.message-content { white-space: pre-wrap; word-break: break-word; }
.confirm-bar { display: flex; align-items: center; gap: 16px; margin-top: 18px; padding-top: 16px; border-top: 1px solid #ebeef5; }
.danger-zone { margin-top: 24px; padding-top: 16px; border-top: 1px dashed #f56c6c; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; column-gap: 14px; }
@media (max-width: 1200px) { .editor-chat-grid { grid-template-columns: 1fr; } .chat-panel { min-height: 500px; } }
@media (max-width: 900px) { .curation-layout { grid-template-columns: 1fr; } .session-panel { min-height: auto; } }
</style>
