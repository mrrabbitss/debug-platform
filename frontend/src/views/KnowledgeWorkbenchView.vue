<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { useWorkbench, failure } from '../composables/useWorkbench'
import type { KnowledgeEntry, LibraryEntry } from '../types/workbench'
import KnowledgeAssistantPane from '../components/knowledge/KnowledgeAssistantPane.vue'
import LibrarySubmissionDialog from '../components/knowledge/LibrarySubmissionDialog.vue'
import KnowledgeCurationView from './KnowledgeCurationView.vue'

const { config, isAdmin, canSubmit, loadConfig, categoryName } = useWorkbench()
const tab = ref('knowledge'), category = ref(''), role = ref(''), query = ref('')
const documents = ref<KnowledgeEntry[]>([]), library = ref<LibraryEntry[]>([])
const selectedDocument = ref<KnowledgeEntry | null>(null), selectedRecord = ref<LibraryEntry | null>(null)
const submission = ref(false), loading = ref(false), busy = ref(false), error = ref('')
const templateCategory = ref(''), showReport = ref(false)
const match = (text: string) => text.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())
const visible = computed(() => documents.value.filter(item => (isAdmin.value || item.status === 'ACTIVE') &&
  (!category.value || item.categories.includes(category.value)) && (!role.value || item.role === role.value) && match(item.title + item.content)))
const records = computed(() => library.value.filter(item => (isAdmin.value || item.status === 'CONFIRMED' || item.owner_id === config.value.principal.id) && match(item.title + item.content)))
const reports = computed(() => records.value.filter(item => item.status === 'CONFIRMED' && item.report_markdown))
const pendingCount = computed(() => library.value.filter(item => item.status === 'PENDING').length)
const stateName = (value: string) => ({ PENDING: '待管理员审核', CONFIRMED: '已确认', REJECTED: '已退回' } as Record<string, string>)[value] || value
const selectedTitle = computed(() => selectedDocument.value?.title || selectedRecord.value?.title || '')
async function load() {
  loading.value = true; error.value = ''
  try {
    const [knowledge, shared] = await Promise.all([api.get<KnowledgeEntry[]>('/workbench/knowledge'), api.get<LibraryEntry[]>('/workbench/library')])
    documents.value = knowledge.data; library.value = shared.data
  } catch (cause) { error.value = failure(cause) }
  finally { loading.value = false }
}
async function review(approve: boolean) {
  const item = selectedRecord.value
  if (!item || !isAdmin.value || busy.value || item.status !== 'PENDING') return
  busy.value = true
  try {
    await api.post(`/workbench/library/${item.id}/review`, { version: item.version, approve })
    selectedRecord.value = null; await load()
    ElMessage.success(approve ? '已确认，案例与报告现可供其他人查阅' : '已退回提交者')
  } catch (cause) { ElMessage.error(failure(cause)) }
  finally { busy.value = false }
}
async function addCategory() {
  if (!isAdmin.value) return
  try {
    const { value } = await ElMessageBox.prompt('输入问题类别名称', '新增问题类别', { inputValidator: value => !!value?.trim() && value.trim().length <= 60 || '请输入 1–60 字的类别名称' })
    await api.post('/workbench/categories', { name: value.trim() }); await loadConfig()
  } catch (cause) { if (cause !== 'cancel' && cause !== 'close') ElMessage.error(failure(cause)) }
}
function openDocument(item: KnowledgeEntry) {
  selectedDocument.value = item; selectedRecord.value = null
  templateCategory.value = item.categories.includes(category.value) ? category.value : item.categories[0] || ''
}
function openRecord(item: LibraryEntry, report = false) {
  selectedRecord.value = item; selectedDocument.value = null; showReport.value = report
}
async function setTemplate() {
  const item = selectedDocument.value
  if (!isAdmin.value || busy.value || !item || item.status !== 'ACTIVE' || item.role !== 'report_template' || !item.categories.includes(templateCategory.value)) return
  busy.value = true
  try {
    await api.put(`/workbench/templates/${templateCategory.value}`, { document_id: item.id, version: item.version })
    ElMessage.success(`已将《${item.title}》v${item.version} 设为${categoryName(templateCategory.value)}的默认模板`)
  } catch (cause: any) {
    ElMessage.error(failure(cause))
    if (cause?.response?.status === 409) { selectedDocument.value = null; await load() }
  } finally { busy.value = false }
}
onMounted(async () => {
  try { await loadConfig(); await load() } catch (cause) { error.value = failure(cause) }
})
</script>

<template>
  <div class="workspace-page">
    <div class="workspace-heading"><div><span class="eyebrow">KNOWLEDGE</span><h1>知识库</h1><p class="muted">按问题查找方法，把确认过的经验留给下一次诊断。</p></div><el-button v-if="canSubmit" size="large" @click="submission=true">提交已定位案例</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link @click="load">重新加载</el-button></el-alert>
    <el-tabs v-model="tab" class="workbench-tabs">
      <el-tab-pane label="诊断知识" name="knowledge">
        <div class="knowledge-workspace">
          <aside class="knowledge-navigation" aria-label="知识分类">
            <span class="navigation-caption">问题类别</span>
            <button :class="{active:!category}" :aria-pressed="!category" @click="category='';role=''">全部知识</button>
            <div v-for="item in config.categories" :key="item.id">
              <button :class="{active:category===item.id}" :aria-expanded="category===item.id" @click="category=item.id;role=''">{{ item.name }}</button>
              <div v-if="category===item.id" class="knowledge-subcategories"><button v-for="(label,key) in config.knowledge_roles" :key="key" :class="{active:role===key}" :aria-pressed="role===key" @click="role=String(key)">{{ label }}</button></div>
            </div>
            <el-button v-if="isAdmin" text @click="addCategory">＋ 新增问题类别</el-button>
          </aside>
          <section class="knowledge-content" v-loading="loading">
            <div class="toolbar"><el-input v-model="query" aria-label="搜索知识" clearable placeholder="搜索标题或正文" class="search-input" /><el-button :loading="loading" @click="load">刷新</el-button><el-button v-if="isAdmin" type="primary" plain @click="tab='assistant'">上传 Skill / 对话维护</el-button></div>
            <div class="collection-heading"><h2>{{ category ? categoryName(category) : '全部知识' }}<span v-if="role"> / {{ config.knowledge_roles[role] }}</span></h2><span class="muted">{{ visible.length }} 份资料</span></div>
            <div v-if="visible.length" class="knowledge-cards"><button v-for="item in visible" :key="item.id" class="knowledge-card" @click="openDocument(item)">
              <div class="knowledge-card-meta"><el-tag size="small" effect="plain">{{ config.knowledge_roles[item.role] || item.role }}</el-tag><span>v{{ item.version }}</span></div>
              <h3>{{ item.title }}</h3><p>{{ item.content.replace(/[#`*>]/g,'').slice(0,120) }}</p>
              <small>{{ item.categories.map(categoryName).join(' / ') }} · {{ item.status==='ACTIVE'?'已发布':'待发布' }}{{ item.legacy?' · 历史资料待归类':'' }}</small>
            </button></div>
            <el-empty v-else :description="query ? '没有匹配的知识，试试其他关键词' : '此分类还没有知识'" />
            <p v-if="isAdmin" class="field-hint"><router-link to="/admin/knowledge">文档审核、版本与恢复管理 →</router-link></p>
          </section>
        </div>
      </el-tab-pane>
      <el-tab-pane name="cases"><template #label>已定位案例 <el-tag v-if="isAdmin&&pendingCount" size="small" type="warning">{{ pendingCount }} 待审</el-tag></template>
        <div class="toolbar"><el-input v-model="query" aria-label="搜索案例与报告" clearable placeholder="搜索案例或定位结果" class="search-input" /><el-button :loading="loading" @click="load">刷新</el-button></div>
        <p class="field-hint">已确认案例供所有人查阅。自己的提交可在这里查看审核状态。</p>
        <el-table :data="records" v-loading="loading"><el-table-column prop="title" label="案例" min-width="220" /><el-table-column label="类别" min-width="130"><template #default="{row}">{{ categoryName(row.problem_category) }}</template></el-table-column><el-table-column label="状态" min-width="150"><template #default="{row}"><el-tag :type="row.status==='PENDING'?'warning':row.status==='CONFIRMED'?'success':'info'" effect="plain">{{ stateName(row.status) }}</el-tag></template></el-table-column><el-table-column label="操作" width="140"><template #default="{row}"><el-button link type="primary" @click="openRecord(row as LibraryEntry)">{{ isAdmin&&row.status==='PENDING'?'查看并审核':'查看案例' }}</el-button></template></el-table-column><template #empty><el-empty description="暂无案例记录" /></template></el-table>
      </el-tab-pane>
      <el-tab-pane label="已确认报告" name="reports">
        <el-table :data="reports" v-loading="loading"><el-table-column prop="title" label="报告" min-width="220" /><el-table-column label="类别"><template #default="{row}">{{ categoryName(row.problem_category) }}</template></el-table-column><el-table-column label="操作" width="140"><template #default="{row}"><el-button link type="primary" @click="openRecord(row as LibraryEntry,true)">阅读报告</el-button></template></el-table-column><template #empty><el-empty description="审核通过的案例报告将在这里展示" /></template></el-table>
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="AI 整理助手" name="assistant" lazy><KnowledgeAssistantPane :categories="config.categories" :roles="config.knowledge_roles" @published="load" /></el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="AI 案例提炼" name="curation" lazy><KnowledgeCurationView v-if="tab==='curation'" /></el-tab-pane>
    </el-tabs>
    <el-drawer :model-value="!!selectedDocument||!!selectedRecord" @close="selectedDocument=null;selectedRecord=null" :title="selectedTitle" size="min(900px, 94vw)">
      <template v-if="selectedDocument">
        <div class="toolbar"><el-tag>{{ config.knowledge_roles[selectedDocument.role] }}</el-tag><span>v{{ selectedDocument.version }} · {{ selectedDocument.categories.map(categoryName).join(' / ') }}</span></div>
        <p v-if="selectedDocument.source_paths?.length" class="field-hint">来源：{{ selectedDocument.source_paths.join('、') }}</p>
        <div v-if="isAdmin&&selectedDocument.role==='report_template'&&selectedDocument.status==='ACTIVE'" class="template-choice">
          <p v-if="selectedDocument.id==='builtin-network-report'" class="field-hint">系统内置模板 v{{ selectedDocument.version }}。发布自定义报告格式后，可选择其作为类别默认模板。</p>
          <template v-else><p class="field-hint">为该类别的新诊断选用此报告格式，已开始的诊断保留原模板。</p><div class="toolbar"><el-select v-model="templateCategory" aria-label="默认模板类别"><el-option v-for="id in selectedDocument.categories" :key="id" :label="categoryName(id)" :value="id" /></el-select><el-button :loading="busy" type="primary" @click="setTemplate">设为该类别默认模板</el-button></div></template>
        </div>
        <pre class="document-text">{{ selectedDocument.content }}</pre>
      </template>
      <template v-if="selectedRecord">
        <div class="toolbar"><el-tag>{{ stateName(selectedRecord.status) }}</el-tag><span>{{ categoryName(selectedRecord.problem_category) }}</span><el-button v-if="selectedRecord.report_markdown" @click="showReport=!showReport">{{ showReport?'查看定位结果':'查看附带报告' }}</el-button></div>
        <pre class="document-text">{{ showReport ? selectedRecord.report_markdown : selectedRecord.content }}</pre>
        <div v-if="isAdmin&&selectedRecord.status==='PENDING'" class="review-actions"><p>核对定位结果及附带报告，确认后向所有用户共享。</p><el-button type="primary" :loading="busy" @click="review(true)">确认入库</el-button><el-button :disabled="busy" @click="review(false)">退回</el-button></div>
      </template>
    </el-drawer>
    <LibrarySubmissionDialog v-if="canSubmit" v-model="submission" :categories="config.categories" @submitted="load();tab='cases'" />
  </div>
</template>
