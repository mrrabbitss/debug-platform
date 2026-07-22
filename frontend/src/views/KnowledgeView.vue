<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { KnowledgeCategory, KnowledgeDocument } from '../types'

const documents = ref<KnowledgeDocument[]>([])
const categories = ref<KnowledgeCategory[]>([])
const selectedCategoryId = ref('')
const search = ref('')
const loading = ref(false)
const documentDialog = ref(false)
const uploadDialog = ref(false)
const categoryDialog = ref(false)
const editingDocumentId = ref<string | null>(null)
const editingCategoryId = ref<string | null>(null)
const saving = ref(false)
const file = ref<File | null>(null)

const sourceTypes = [
  { label: '日志与错误码规则', value: 'log_rule' },
  { label: '诊断规则', value: 'diagnostic_rule' },
  { label: '协议诊断规则', value: 'protocol_rule' },
  { label: '安全诊断规则', value: 'security_rule' },
  { label: '故障树', value: 'fault_tree' },
  { label: '解决方案', value: 'solution' },
  { label: '历史故障/已知问题', value: 'historical_bug' },
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
  content: '',
  active: true
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

function sourceTypeLabel(value: string) {
  return sourceTypes.find(item => item.value === value)?.label || value
}

function sourceTypeForCategory(categoryId: string) {
  const code = categoryMap.value.get(categoryId)?.code || ''
  if (code === 'history.fault_trees') return 'fault_tree'
  if (code === 'history.solutions') return 'solution'
  if (code === 'history.known_issues') return 'historical_bug'
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
  documentForm.active = true
}

function openCreateDocument() {
  resetDocumentForm()
  documentDialog.value = true
}

async function openEditDocument(document: KnowledgeDocument) {
  try {
    const detail: KnowledgeDocument = (await api.get(`/knowledge/${document.id}`)).data
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
    documentForm.active = detail.active
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
      module: documentForm.module || null
    }
    if (editingDocumentId.value) await api.patch(`/knowledge/${editingDocumentId.value}`, payload)
    else await api.post('/knowledge', payload)
    ElMessage.success(editingDocumentId.value ? '知识内容已修改并重建索引' : '知识内容已新增并建立索引')
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

async function upload() {
  if (!file.value) return ElMessage.warning('请选择文档')
  saving.value = true
  try {
    const data = new FormData()
    data.append('file', file.value)
    Object.entries(uploadForm).forEach(([key, value]) => value && data.append(key, value))
    await api.post('/knowledge/upload', data)
    ElMessage.success('文档已切分并建立索引')
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
    await api.delete(`/knowledge/${document.id}`)
    ElMessage.success('知识已删除')
    await load()
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
          <el-table-column prop="device_type" label="设备" width="80" />
          <el-table-column prop="module" label="模块" width="100" />
          <el-table-column label="可信级别" width="100"><template #default="scope"><el-tag :type="scope.row.trust_level === 'HIGH' ? 'success' : scope.row.trust_level === 'LOW' ? 'warning' : 'info'">{{ scope.row.trust_level }}</el-tag></template></el-table-column>
          <el-table-column label="索引" width="100"><template #default="scope"><el-tooltip v-if="scope.row.metadata?.embedding_error" :content="scope.row.metadata.embedding_error"><el-tag type="danger">向量失败</el-tag></el-tooltip><el-tag v-else type="success">{{ scope.row.chunk_count }} 分块</el-tag></template></el-table-column>
          <el-table-column label="操作" width="130" fixed="right"><template #default="scope"><el-button link type="primary" @click="openEditDocument(scope.row as KnowledgeDocument)">修改</el-button><el-button link type="danger" @click="removeDocument(scope.row as KnowledgeDocument)">删除</el-button></template></el-table-column>
        </el-table>
      </el-card>
    </div>

    <el-dialog v-model="documentDialog" :title="editingDocumentId ? '修改知识内容' : '新增知识内容'" width="800px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="标题"><el-input v-model="documentForm.title" /></el-form-item>
        <el-form-item label="所属分类"><el-tree-select v-model="documentForm.category_id" :data="categoryTree" node-key="id" :props="{ label: 'name', children: 'children' }" check-strictly clearable style="width:100%" /></el-form-item>
        <el-form-item label="知识类型"><el-select v-model="documentForm.source_type" style="width:100%"><el-option v-for="item in sourceTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <div class="form-grid">
          <el-form-item label="设备类型"><el-select v-model="documentForm.device_type" clearable><el-option label="GW" value="GW"/><el-option label="AP" value="AP"/><el-option label="其他" value="OTHER"/></el-select></el-form-item>
          <el-form-item label="模块"><el-input v-model="documentForm.module" placeholder="WLAN/WAN/PON/OMCI" /></el-form-item>
          <el-form-item label="设备型号"><el-input v-model="documentForm.device_model" /></el-form-item>
          <el-form-item label="固件范围"><el-input v-model="documentForm.firmware_range" /></el-form-item>
          <el-form-item label="可信级别"><el-select v-model="documentForm.trust_level"><el-option label="高" value="HIGH"/><el-option label="中" value="MEDIUM"/><el-option label="低" value="LOW"/></el-select></el-form-item>
          <el-form-item label="可见级别"><el-select v-model="documentForm.confidentiality"><el-option label="内部" value="INTERNAL"/><el-option label="受限" value="RESTRICTED"/><el-option label="公开" value="PUBLIC"/></el-select></el-form-item>
        </div>
        <el-form-item label="正文"><el-input v-model="documentForm.content" type="textarea" :rows="16" placeholder="支持 Markdown。故障树可按“现象 → 检查 → 分支 → 根因 → 解决方案”的结构编写。" /></el-form-item>
        <el-form-item label="参与检索"><el-switch v-model="documentForm.active" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="documentDialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveDocument">保存并重建索引</el-button></template>
    </el-dialog>

    <el-dialog v-model="uploadDialog" title="上传知识文件" width="600px">
      <el-form label-width="100px">
        <el-form-item label="文件"><input type="file" accept=".txt,.md,.log,.json" @change="(event:any) => file = event.target.files?.[0] || null" /></el-form-item>
        <el-form-item label="所属分类"><el-tree-select v-model="uploadForm.category_id" :data="categoryTree" node-key="id" :props="{ label: 'name', children: 'children' }" check-strictly clearable style="width:100%" /></el-form-item>
        <el-form-item label="知识类型"><el-select v-model="uploadForm.source_type" style="width:100%"><el-option v-for="item in sourceTypes" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
        <el-form-item label="设备类型"><el-select v-model="uploadForm.device_type" clearable><el-option label="GW" value="GW"/><el-option label="AP" value="AP"/></el-select></el-form-item>
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
