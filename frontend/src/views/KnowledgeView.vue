<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import MarkdownKnowledgeRoutingDialog from '../components/knowledge/MarkdownKnowledgeRoutingDialog.vue'
import KnowledgeDraftActions from '../components/KnowledgeDraftActions.vue'
import KnowledgeQuality from '../components/KnowledgeQuality.vue'
import {
  knowledgeDeviceTypeLabel,
  knowledgeDeviceTypeOptions
} from '../constants/knowledge'
import type {
  Job,
  KnowledgeCategory,
  KnowledgeDocument,
  KnowledgeRevision
} from '../types'

const router = useRouter()

const documents = ref<KnowledgeDocument[]>([])
const categories = ref<KnowledgeCategory[]>([])
const selectedCategoryId = ref('')
const search = ref('')
const loading = ref(false)
const documentDialog = ref(false)
const uploadDialog = ref(false)
const knowledgeRoutingDialog = ref(false)
const categoryDialog = ref(false)
const revisionDialog = ref(false)
const editingDocumentId = ref<string | null>(null)
const editingLockVersion = ref<number | null>(null)
const editingDraftVersion = ref<number | null>(null)
const editingCategoryId = ref<string | null>(null)
const revisionDocument = ref<KnowledgeDocument | null>(null)
const revisions = ref<KnowledgeRevision[]>([])
const saving = ref(false)
const file = ref<File | null>(null)

const sourceTypes = [
  { label: '日志与错误码规则', value: 'log_rule' },
  { label: '诊断规则', value: 'diagnostic_rule' },
  { label: '协议诊断规则', value: 'protocol_rule' },
  { label: '安全诊断规则', value: 'security_rule' },
  { label: '结构化故障案例', value: 'fault_case' },
  { label: '故障树', value: 'fault_tree' },
  { label: '解决方案', value: 'solution' },
  { label: '历史故障/已知问题', value: 'historical_bug' },
  { label: '错误分析 Skill', value: 'analysis_skill' },
  { label: '提炼分析方法', value: 'analysis_method' },
  { label: '产品/协议文档', value: 'document' },
  { label: '测试规范', value: 'test_spec' }
]

const documentForm = reactive({
  title: '',
  category_id: '',
  source_type: 'document',
  device_type: '',
  device_model: '',
  firmware_range: '',
  module: '',
  trust_level: 'MEDIUM',
  confidentiality: 'INTERNAL',
  content: ''
})

const uploadForm = reactive({
  category_id: '',
  source_type: 'document',
  device_type: '',
  module: '',
  trust_level: 'MEDIUM'
})

const categoryForm = reactive({
  name: '',
  parent_id: '',
  description: '',
  sort_order: 0
})

const categoryMap = computed(() => new Map(categories.value.map(item => [item.id, item])))
const selectedCategory = computed(() => categoryMap.value.get(selectedCategoryId.value))
const categoryTree = computed(() => {
  const nodes = new Map<string, KnowledgeCategory>()
  categories.value.filter(item => item.active).forEach(item => nodes.set(item.id, { ...item, children: [] }))
  const roots: KnowledgeCategory[] = []
  nodes.forEach(item => {
    if (item.parent_id && nodes.has(item.parent_id)) nodes.get(item.parent_id)!.children!.push(item)
    else roots.push(item)
  })
  const aggregateCount = (item: KnowledgeCategory): number => {
    const childCount = (item.children || []).reduce((sum, child) => sum + aggregateCount(child), 0)
    item.document_count += childCount
    return item.document_count
  }
  roots.forEach(aggregateCount)
  return roots
})

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}

async function waitForJob(initialJob: Job): Promise<Job> {
  let job = initialJob
  while (!['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(job.status)) {
    await new Promise(resolve => window.setTimeout(resolve, 1000))
    job = (await api.get(`/jobs/${job.id}`)).data
  }
  if (job.status !== 'COMPLETED') {
    throw new Error(job.error_message || `知识导入任务${job.status === 'CANCELLED' ? '已取消' : '失败'}`)
  }
  return job
}

function sourceTypeLabel(value: string) {
  return sourceTypes.find(item => item.value === value)?.label || value
}

function reviewStatusLabel(value: KnowledgeDocument['review_status']) {
  return {
    DRAFT: '草稿',
    IN_REVIEW: '待审核',
    ACTIVE: '已发布',
    REJECTED: '已驳回',
    ARCHIVED: '已归档'
  }[value] || value
}

function reviewStatusType(value: KnowledgeDocument['review_status']) {
  if (value === 'ACTIVE') return 'success'
  if (value === 'IN_REVIEW') return 'warning'
  if (value === 'REJECTED') return 'danger'
  return 'info'
}

function sourceTypeForCategory(categoryId: string) {
  const code = categoryMap.value.get(categoryId)?.code || ''
  if (code === 'history.cases') return 'fault_case'
  if (code === 'history.fault_trees') return 'fault_tree'
  if (code === 'history.solutions') return 'solution'
  if (code === 'history.known_issues') return 'historical_bug'
  if (code === 'methods.analysis_skills') return 'analysis_skill'
  if (code === 'methods.extracted') return 'analysis_method'
  if (code === 'diagnosis.log_rules') return 'log_rule'
  if (code === 'diagnosis.protocol_rules') return 'protocol_rule'
  if (code === 'diagnosis.security_rules') return 'security_rule'
  if (code.startsWith('diagnosis.')) return 'diagnostic_rule'
  if (code === 'reference.test_specs') return 'test_spec'
  return 'document'
}

async function loadCategories() {
  categories.value = (await api.get('/knowledge/categories')).data
}

async function loadDocuments() {
  loading.value = true
  try {
    const params: Record<string, any> = { limit: 1000 }
    if (selectedCategoryId.value) params.category_id = selectedCategoryId.value
    if (search.value.trim()) params.search = search.value.trim()
    documents.value = (await api.get('/knowledge', { params })).data
  } finally {
    loading.value = false
  }
}

async function load() {
  try {
    await Promise.all([loadCategories(), loadDocuments()])
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

function selectAll() {
  selectedCategoryId.value = ''
  loadDocuments()
}

function selectCategory(category: KnowledgeCategory) {
  selectedCategoryId.value = category.id
  loadDocuments()
}

function resetDocumentForm() {
  editingDocumentId.value = null
  editingLockVersion.value = null
  editingDraftVersion.value = null
  documentForm.title = ''
  documentForm.category_id = selectedCategoryId.value
  documentForm.source_type = sourceTypeForCategory(selectedCategoryId.value)
  documentForm.device_type = ''
  documentForm.device_model = ''
  documentForm.firmware_range = ''
  documentForm.module = ''
  documentForm.trust_level = 'MEDIUM'
  documentForm.confidentiality = 'INTERNAL'
  documentForm.content = ''
}

function openCreateDocument() {
  resetDocumentForm()
  documentDialog.value = true
}

async function openFaultCaseTemplate() {
  try {
    const template = (await api.get('/knowledge/templates/fault-case')).data
    resetDocumentForm()
    documentForm.title = '新故障案例'
    documentForm.source_type = template.source_type
    documentForm.content = template.content
    const category = categories.value.find(item => item.code === 'history.cases')
    documentForm.category_id = category?.id || ''
    documentDialog.value = true
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

function canExtractMethod(document: KnowledgeDocument) {
  return ['fault_case', 'fault_tree', 'historical_bug', 'analysis_skill'].includes(
    document.source_type
  )
}

async function extractMethod(document: KnowledgeDocument) {
  if (document.metadata?.derived_analysis_method_id) {
    try {
      await ElMessageBox.confirm(
        '该来源已经存在派生分析方法。重新提炼会用当前来源覆盖派生方法正文，包括其中的人工修改。是否继续？',
        '重新提炼分析方法',
        { type: 'warning', confirmButtonText: '重新提炼', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
  }
  saving.value = true
  try {
    const result = (await api.post(`/knowledge/${document.id}/extract-method`)).data
    ElMessage.success(
      result.created ? '已提炼并建立可复用分析方法' : '已根据最新内容更新分析方法'
    )
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function openEditDocument(document: KnowledgeDocument) {
  try {
    const published: KnowledgeDocument = (await api.get(`/knowledge/${document.id}`)).data
    const detail = { ...published, ...published.pending_draft?.snapshot }
    editingDraftVersion.value = published.pending_draft?.version || null
    editingDocumentId.value = detail.id
    documentForm.title = detail.title
    documentForm.category_id = detail.category_id || ''
    documentForm.source_type = detail.source_type
    documentForm.device_type = detail.device_type || ''
    documentForm.device_model = detail.device_model || ''
    documentForm.firmware_range = detail.firmware_range || ''
    documentForm.module = detail.module || ''
    documentForm.trust_level = detail.trust_level
    documentForm.confidentiality = detail.confidentiality
    documentForm.content = detail.content || ''
    editingLockVersion.value = detail.lock_version
    documentDialog.value = true
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function saveDocument() {
  if (!documentForm.title.trim() || !documentForm.content.trim()) {
    return ElMessage.warning('标题和内容不能为空')
  }
  saving.value = true
  try {
    const payload = {
      ...documentForm,
      category_id: documentForm.category_id || null,
      device_type: documentForm.device_type || null,
      device_model: documentForm.device_model || null,
      firmware_range: documentForm.firmware_range || null,
      module: documentForm.module || null,
      ...(editingDocumentId.value
        ? { expected_lock_version: editingLockVersion.value, expected_draft_version: editingDraftVersion.value }
        : {})
    }
    if (editingDocumentId.value) await api.patch(`/knowledge/${editingDocumentId.value}`, payload)
    else await api.post('/knowledge', payload)
    ElMessage.success(
      editingDocumentId.value
        ? '已保存草稿，请审核发布；已有发布版本继续可用'
        : '知识草稿已新增并建立索引，请审核后发布'
    )
    documentDialog.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

function openUpload() {
  file.value = null
  uploadForm.category_id = selectedCategoryId.value
  uploadForm.source_type = sourceTypeForCategory(selectedCategoryId.value)
  uploadForm.device_type = ''
  uploadForm.module = ''
  uploadForm.trust_level = 'MEDIUM'
  uploadDialog.value = true
}

async function onKnowledgeRoutingCompleted() {
  await load()
}

async function upload() {
  if (!file.value) return ElMessage.warning('请选择文档')
  saving.value = true
  try {
    const data = new FormData()
    data.append('file', file.value)
    Object.entries(uploadForm).forEach(([key, value]) => value && data.append(key, value))
    const result = (
      await api.post(
        '/knowledge/upload',
        data,
        { timeout: 15 * 60 * 1000 }
      )
    ).data
    ElMessage.info('文档已上传，正在后台切分并建立索引')
    await waitForJob(result.job)
    ElMessage.success('文档已切分并建立草稿，请审核后发布')
    uploadDialog.value = false
    file.value = null
    await load()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function removeDocument(document: KnowledgeDocument) {
  try {
    await ElMessageBox.confirm(`确认删除“${document.title}”？`, '删除知识', { type: 'warning' })
    const { data } = await api.delete(`/knowledge/${document.id}`)
    if (data.publication_pending) {
      ElMessage.info('删除审批已保存，发布成功后才从共享知识移除')
      if (data.contribution?.id) {
        await router.push({ path: '/knowledge-management', query: { tab: 'review', contribution: data.contribution.id } })
        return
      }
    } else ElMessage.success('知识已归档，历史引用保留')
    await load()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

async function reviewAction(
  document: KnowledgeDocument,
  action: 'submit' | 'approve' | 'reject' | 'archive'
) {
  const labels = {
    submit: '提交审核',
    approve: '审核通过并发布',
    reject: '驳回',
    archive: '归档'
  }
  try {
    await ElMessageBox.confirm(
      `确认对“${document.title}”执行“${labels[action]}”？`,
      labels[action],
      { type: action === 'reject' || action === 'archive' ? 'warning' : 'info' }
    )
    const response = await api.post(`/knowledge/${document.id}/review/${action}`, {
      expected_lock_version: document.lock_version
    })
    ElMessage.success(response.data.publication_pending ? '发布构建任务已提交，请在任务中心查看结果' : `${labels[action]}成功`)
    await loadDocuments()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

async function openRevisions(document: KnowledgeDocument) {
  try {
    revisionDocument.value = document
    revisions.value = (
      await api.get(`/knowledge/${document.id}/revisions`)
    ).data
    revisionDialog.value = true
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function rollbackRevision(revision: KnowledgeRevision) {
  const document = revisionDocument.value
  if (!document) return
  try {
    await ElMessageBox.confirm(
      `将“${document.title}”恢复为 v${revision.version} 的内容？系统会生成一个新的草稿版本，不会覆盖历史。`,
      '恢复历史版本',
      { type: 'warning' }
    )
    await api.post(
      `/knowledge/${document.id}/revisions/${revision.version}/rollback`,
      { expected_lock_version: document.lock_version, expected_draft_version: document.pending_draft?.version }
    )
    ElMessage.success('已从历史版本生成新草稿')
    revisionDialog.value = false
    await loadDocuments()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

function openCreateCategory(asChild: boolean) {
  editingCategoryId.value = null
  categoryForm.name = ''
  categoryForm.parent_id = asChild ? selectedCategoryId.value : ''
  categoryForm.description = ''
  categoryForm.sort_order = 0
  categoryDialog.value = true
}

function openEditCategory() {
  const category = selectedCategory.value
  if (!category) return
  editingCategoryId.value = category.id
  categoryForm.name = category.name
  categoryForm.parent_id = category.parent_id || ''
  categoryForm.description = category.description
  categoryForm.sort_order = category.sort_order
  categoryDialog.value = true
}

async function saveCategory() {
  if (!categoryForm.name.trim()) return ElMessage.warning('分类名称不能为空')
  saving.value = true
  try {
    const payload = { ...categoryForm, parent_id: categoryForm.parent_id || null }
    if (editingCategoryId.value) await api.patch(`/knowledge/categories/${editingCategoryId.value}`, payload)
    else await api.post('/knowledge/categories', payload)
    ElMessage.success('知识分类已保存')
    categoryDialog.value = false
    await loadCategories()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    saving.value = false
  }
}

async function removeCategory() {
  const category = selectedCategory.value
  if (!category || category.system) return
  try {
    await ElMessageBox.confirm(`确认删除分类“${category.name}”？`, '删除分类', { type: 'warning' })
    await api.delete(`/knowledge/categories/${category.id}`)
    selectedCategoryId.value = ''
    ElMessage.success('分类已删除')
    await load()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">分层知识库</h1>
      <el-button type="primary" @click="openCreateDocument">新增知识</el-button>
      <el-button type="success" plain @click="openFaultCaseTemplate">故障案例模板</el-button>
      <el-button type="warning" plain @click="router.push('/knowledge/curation')">AI 文件夹提炼</el-button>
      <el-button data-testid="knowledge-routing-open" type="primary" plain @click="knowledgeRoutingDialog=true">AI 智能导入 MD</el-button>
      <el-button @click="openUpload">上传文件</el-button>
      <el-button @click="load">刷新</el-button>
    </div>

    <div class="knowledge-layout">
      <el-card class="category-panel">
        <template #header>
          <div class="category-header"><span>知识分类</span><el-button link type="primary" @click="openCreateCategory(false)">新增根分类</el-button></div>
        </template>
        <div class="all-category" :class="{ selected: !selectedCategoryId }" @click="selectAll">全部知识</div>
        <el-tree
          :data="categoryTree"
          node-key="id"
          default-expand-all
          highlight-current
          :expand-on-click-node="false"
          @node-click="selectCategory"
        >
          <template #default="{ data }">
            <span class="tree-node"><span>{{ data.name }}</span><span class="muted">{{ data.document_count }}</span></span>
          </template>
        </el-tree>
        <el-divider />
        <div class="category-actions">
          <el-button size="small" :disabled="!selectedCategory" @click="openCreateCategory(true)">添加子分类</el-button>
          <el-button size="small" :disabled="!selectedCategory" @click="openEditCategory">修改分类</el-button>
          <el-button size="small" type="danger" :disabled="!selectedCategory || selectedCategory.system" @click="removeCategory">删除</el-button>
        </div>
      </el-card>

      <el-card>
        <div class="toolbar">
          <strong>{{ selectedCategory?.name || '全部知识' }}</strong>
          <span v-if="selectedCategory?.description" class="muted">{{ selectedCategory.description }}</span>
          <el-input v-model="search" clearable placeholder="搜索标题或内容" style="width:280px;margin-left:auto" @keyup.enter="loadDocuments" @clear="loadDocuments" />
          <el-button @click="loadDocuments">搜索</el-button>
        </div>
        <el-table v-loading="loading" :data="documents" stripe>
          <el-table-column prop="title" label="标题" min-width="240" show-overflow-tooltip />
          <el-table-column label="分类" width="150"><template #default="scope">{{ scope.row.category_name || '未分类' }}</template></el-table-column>
          <el-table-column label="知识类型" width="150"><template #default="scope">{{ sourceTypeLabel(scope.row.source_type) }}</template></el-table-column>
          <el-table-column label="设备" width="80"><template #default="scope">{{ knowledgeDeviceTypeLabel(scope.row.device_type) }}</template></el-table-column>
          <el-table-column prop="module" label="模块" width="100" />
          <el-table-column label="可信级别" width="100"><template #default="scope"><el-tag :type="scope.row.trust_level === 'HIGH' ? 'success' : scope.row.trust_level === 'LOW' ? 'warning' : 'info'">{{ scope.row.trust_level }}</el-tag></template></el-table-column>
          <el-table-column label="审核状态" width="105">
            <template #default="scope">
              <el-tag :type="reviewStatusType(scope.row.review_status)">
                {{ reviewStatusLabel(scope.row.review_status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="版本" width="80">
            <template #default="scope">v{{ scope.row.version }}</template>
          </el-table-column>
          <el-table-column label="结构完整度" width="120">
            <template #default="scope">
              <template v-if="scope.row.metadata?.markdown_structure">
                <el-tooltip
                  :content="scope.row.metadata.markdown_structure.complete ? '必需章节齐全' : `缺少：${scope.row.metadata.markdown_structure.missing_sections.join('、')}`"
                >
                  <el-tag :type="scope.row.metadata.markdown_structure.complete ? 'success' : 'warning'">
                    {{ Math.round(scope.row.metadata.markdown_structure.completeness * 100) }}%
                  </el-tag>
                </el-tooltip>
              </template>
              <el-tooltip
                v-else-if="scope.row.metadata?.derivation_status"
                content="来源已更新，但派生方法曾被人工修改；请复核后决定是否重新提炼。"
              >
                <el-tag type="warning">需复核</el-tag>
              </el-tooltip>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="索引" width="100"><template #default="scope"><el-tooltip v-if="scope.row.metadata?.embedding_error" :content="scope.row.metadata.embedding_error"><el-tag type="danger">向量失败</el-tag></el-tooltip><el-tag v-else type="success">{{ scope.row.chunk_count }} 分块</el-tag></template></el-table-column>
          <el-table-column label="操作" width="420" fixed="right">
            <template #default="scope">
              <el-button link type="primary" @click="openEditDocument(scope.row as KnowledgeDocument)">修改</el-button>
              <KnowledgeDraftActions :document="scope.row as KnowledgeDocument" @changed="load" />
              <KnowledgeQuality :document="scope.row as KnowledgeDocument" @changed="load" />
              <el-button
                v-if="['DRAFT', 'REJECTED'].includes(scope.row.review_status)"
                link
                type="warning"
                @click="reviewAction(scope.row as KnowledgeDocument, 'submit')"
              >
                提交审核
              </el-button>
              <el-button
                v-if="scope.row.can_publish && scope.row.review_status === 'IN_REVIEW'"
                link
                type="success"
                @click="reviewAction(scope.row as KnowledgeDocument, 'approve')"
              >
                发布
              </el-button>
              <el-button
                v-if="scope.row.can_publish && scope.row.review_status === 'IN_REVIEW'"
                link
                type="danger"
                @click="reviewAction(scope.row as KnowledgeDocument, 'reject')"
              >
                驳回
              </el-button>
              <el-button
                v-if="canExtractMethod(scope.row as KnowledgeDocument)"
                link
                type="success"
                :loading="saving"
                @click="extractMethod(scope.row as KnowledgeDocument)"
              >
                提炼方法
              </el-button>
              <el-button link @click="openRevisions(scope.row as KnowledgeDocument)">版本</el-button>
              <el-button
                v-if="scope.row.review_status === 'ACTIVE'"
                link
                type="warning"
                @click="reviewAction(scope.row as KnowledgeDocument, 'archive')"
              >
                归档
              </el-button>
              <el-button link type="danger" @click="removeDocument(scope.row as KnowledgeDocument)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>

    <el-dialog v-model="documentDialog" :title="editingDocumentId ? '修改知识内容' : '新增知识内容'" width="800px" destroy-on-close>
      <el-alert
        title="保存会生成草稿；只有经过审核并发布的版本才会参与诊断检索。"
        type="info"
        :closable="false"
        style="margin-bottom:16px"
      />
      <el-form label-width="110px">
        <el-form-item label="标题"><el-input v-model="documentForm.title" /></el-form-item>
        <el-form-item label="所属分类"><el-tree-select v-model="documentForm.category_id" :data="categoryTree" node-key="id" :props="{ label: 'name', children: 'children' }" check-strictly clearable style="width:100%" /></el-form-item>
        <el-form-item label="知识类型"><el-select v-model="documentForm.source_type" style="width:100%"><el-option v-for="item in sourceTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <div class="form-grid">
          <el-form-item label="设备类型"><el-select v-model="documentForm.device_type" clearable><el-option v-for="item in knowledgeDeviceTypeOptions" :key="item.value" :label="item.label" :value="item.value"/></el-select></el-form-item>
          <el-form-item label="模块"><el-input v-model="documentForm.module" placeholder="WLAN/WAN/PON/OMCI" /></el-form-item>
          <el-form-item label="设备型号"><el-input v-model="documentForm.device_model" /></el-form-item>
          <el-form-item label="固件范围"><el-input v-model="documentForm.firmware_range" /></el-form-item>
          <el-form-item label="可信级别"><el-select v-model="documentForm.trust_level"><el-option label="高" value="HIGH"/><el-option label="中" value="MEDIUM"/><el-option label="低" value="LOW"/></el-select></el-form-item>
          <el-form-item label="可见级别"><el-select v-model="documentForm.confidentiality"><el-option label="内部" value="INTERNAL"/><el-option label="受限" value="RESTRICTED"/><el-option label="公开" value="PUBLIC"/></el-select></el-form-item>
        </div>
        <el-form-item label="正文"><el-input v-model="documentForm.content" type="textarea" :rows="16" placeholder="支持 Markdown。故障树可按“现象 → 检查 → 分支 → 根因 → 解决方案”的结构编写。" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="documentDialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveDocument">保存并重建索引</el-button></template>
    </el-dialog>

    <el-dialog v-model="revisionDialog" title="知识版本历史" width="760px">
      <el-alert
        title="恢复历史版本会创建一个新的草稿版本，已有版本和审核记录不会被覆盖。"
        type="info"
        :closable="false"
        style="margin-bottom:16px"
      />
      <el-table :data="revisions" stripe>
        <el-table-column label="版本" width="80"><template #default="scope">v{{ scope.row.version }}</template></el-table-column>
        <el-table-column prop="change_summary" label="变更说明" min-width="220" />
        <el-table-column prop="created_by" label="操作者" width="140" />
        <el-table-column prop="created_at" label="时间" width="190" />
        <el-table-column label="内容哈希" min-width="170"><template #default="scope"><span class="mono">{{ scope.row.content_hash.slice(0, 16) }}</span></template></el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="scope">
            <el-button
              link
              type="warning"
              :disabled="scope.row.version === revisionDocument?.version"
              @click="rollbackRevision(scope.row as KnowledgeRevision)"
            >
              恢复
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="uploadDialog" title="上传知识文件" width="600px">
      <el-form label-width="100px">
        <el-form-item label="文件"><input type="file" accept=".txt,.md,.log,.json" @change="(event:any) => file = event.target.files?.[0] || null" /></el-form-item>
        <el-form-item label="所属分类"><el-tree-select v-model="uploadForm.category_id" :data="categoryTree" node-key="id" :props="{ label: 'name', children: 'children' }" check-strictly clearable style="width:100%" /></el-form-item>
        <el-form-item label="知识类型"><el-select v-model="uploadForm.source_type" style="width:100%"><el-option v-for="item in sourceTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="设备类型"><el-select v-model="uploadForm.device_type" clearable><el-option v-for="item in knowledgeDeviceTypeOptions" :key="item.value" :label="item.label" :value="item.value"/></el-select></el-form-item>
        <el-form-item label="模块"><el-input v-model="uploadForm.module" placeholder="WLAN/WAN/PON/OMCI" /></el-form-item>
        <el-form-item label="可信级别"><el-select v-model="uploadForm.trust_level"><el-option label="高" value="HIGH"/><el-option label="中" value="MEDIUM"/><el-option label="低" value="LOW"/></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="uploadDialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="upload">上传并索引</el-button></template>
    </el-dialog>

    <el-dialog v-model="categoryDialog" :title="editingCategoryId ? '修改知识分类' : '新增知识分类'" width="520px">
      <el-form label-width="100px">
        <el-form-item label="分类名称"><el-input v-model="categoryForm.name" /></el-form-item>
        <el-form-item label="上级分类"><el-tree-select v-model="categoryForm.parent_id" :data="categoryTree" node-key="id" :props="{ label: 'name', children: 'children' }" check-strictly clearable style="width:100%" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="categoryForm.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="排序"><el-input-number v-model="categoryForm.sort_order" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="categoryDialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveCategory">保存</el-button></template>
    </el-dialog>

    <MarkdownKnowledgeRoutingDialog
      v-model="knowledgeRoutingDialog"
      @completed="onKnowledgeRoutingCompleted"
    />
  </div>
</template>

<style scoped>
.knowledge-layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 16px; align-items: start; }
.category-panel { min-height: 560px; }
.category-header, .tree-node { display: flex; align-items: center; justify-content: space-between; width: 100%; }
.all-category { padding: 8px 10px; margin-bottom: 4px; border-radius: 4px; cursor: pointer; }
.all-category:hover, .all-category.selected { background: #ecf5ff; color: #409eff; }
.category-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; column-gap: 14px; }
@media (max-width: 1000px) { .knowledge-layout { grid-template-columns: 1fr; } .category-panel { min-height: auto; } }
</style>
