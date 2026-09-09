<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../../api/client'
import { failure, type ProblemCategory } from '../../composables/useWorkbench'
import { contributionsApi, contributionStatus, type KnowledgeContribution } from '../../api/knowledgeContributions'
import type { KnowledgeEntry } from '../../types/workbench'
import type { KnowledgeDocument } from '../../types'
import ContributionDetail from './ContributionDetail.vue'
const props = defineProps<{ categories: ProblemCategory[]; roles: Record<string, string>; ownerId: string }>()
const emit = defineEmits<{ changed: []; organize: []; maintain: [] }>()
const entries = ref<KnowledgeEntry[]>([]), drafts = ref<KnowledgeContribution[]>([]), selectedCategory = ref(''), selected = ref<KnowledgeEntry | null>(null), draftId = ref('')
const busy = ref(false), loading = ref(false), error = ref(''), dialog = ref(false), formError = ref(''), target = ref<KnowledgeDocument | null>(null)
const form = reactive({ title: '', content: '', categories: [] as string[], role: 'diagnosis', paths: '', metadata: '{}' })
const visible = computed(() => entries.value.filter(item => item.content_kind === 'SKILL' && (!selectedCategory.value || item.categories.includes(selectedCategory.value))))
const category = computed(() => props.categories.find(item => item.id === selectedCategory.value))
async function load() {
  loading.value = true; error.value = ''
  try { const [documents, proposals] = await Promise.all([api.get<KnowledgeEntry[]>('/workbench/knowledge'), contributionsApi.list(true)]); entries.value = documents.data; drafts.value = proposals.filter(item => item.content_kind === 'SKILL' && item.status !== 'PUBLISHED') }
  catch(cause) { error.value = failure(cause) } finally { loading.value = false }
}
async function edit(item?: KnowledgeEntry) {
  if (busy.value) return
  formError.value = ''; target.value = null
  try {
    const doc = item ? (await api.get<KnowledgeDocument>(`/knowledge/${item.id}`)).data : null
    target.value = doc; const metadata = doc?.metadata || {}
    Object.assign(form, { title: doc?.title || '', content: doc?.content || '', categories: metadata.problem_categories || [selectedCategory.value || 'general'], role: metadata.knowledge_role || 'diagnosis', paths: (metadata.source_paths || []).join('\n'), metadata: JSON.stringify(metadata, null, 2) }); dialog.value = true
  } catch(cause) { error.value = failure(cause) }
}
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return
  if (file.size > 500000 || !/\.(md|markdown)$/i.test(file.name)) { formError.value = '请选择不超过 500 KB 的 UTF-8 Markdown'; return }
  try { form.content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()); if (!form.title) form.title = file.name; if (!form.paths) form.paths = file.name }
  catch { formError.value = '请将文件转换为 UTF-8 编码' }
}
async function save() {
  if (busy.value) return
  if (!form.title.trim() || !form.content.trim() || !form.categories.length) { formError.value = '请填写标题、正文和至少一个问题类别'; return }
  let metadata: Record<string, any>
  try { metadata = JSON.parse(form.metadata); if (!metadata || Array.isArray(metadata) || typeof metadata !== 'object') throw Error() }
  catch { formError.value = '扩展属性必须是 JSON 对象'; return }
  Object.assign(metadata, { content_kind: 'SKILL', problem_categories: [...form.categories], knowledge_role: form.role, source_paths: form.paths.split('\n').map(value => value.trim()).filter(Boolean) })
  busy.value = true; formError.value = ''
  try {
    const item = await contributionsApi.create({ operation: target.value ? 'UPDATE' : 'CREATE', content_kind: 'SKILL', ...(target.value ? { target_document_id: target.value.id } : { source_type: 'analysis_skill' }), title: form.title.trim(), content: form.content, metadata })
    draftId.value = item.id; selected.value = null; dialog.value = false; await load(); ElMessage.success('变更已保存，核对完整版本后可直接批准发布')
  } catch(cause) { formError.value = failure(cause) } finally { busy.value = false }
}
async function remove(item: KnowledgeEntry) {
  if (busy.value) return
  try { await ElMessageBox.confirm(`将《${item.title}》v${item.version} 从当前诊断 Skill 中删除？历史案例的引用保留。`, '核对删除范围', { type: 'warning', confirmButtonText: '准备删除版本' }) } catch { return }
  busy.value = true
  try { const proposal = await contributionsApi.create({ operation: 'DELETE', target_document_id: item.id }); draftId.value = proposal.id; selected.value = null; await load() }
  catch(cause) { error.value = failure(cause) } finally { busy.value = false }
}
async function categoryAction(action: 'create' | 'rename' | 'delete') {
  if (busy.value || (action !== 'create' && !category.value)) return
  const current = category.value
  let name = ''
  try {
    if (action === 'delete') await ElMessageBox.confirm(`停用“${current!.name}”后新案例不再显示此类别。有未迁移内容时服务器会拒绝操作，历史案例仍保留类别。`, '停用问题类别', { type: 'warning' })
    else { const result = await ElMessageBox.prompt('问题类别会同步显示在创建案例的选项中', action === 'create' ? '新增问题类别' : '修改类别名称', { inputValue: action === 'rename' ? current!.name : '', inputValidator: value => !!value?.trim() && value.trim().length <= 60 || '请输入 1–60 字的名称' }); name = result.value.trim() }
  } catch { return }
  busy.value = true; error.value = ''
  try {
    if (action === 'create') { const { data } = await api.post('/workbench/categories', { name }); selectedCategory.value = data.id }
    else if (action === 'rename') await api.patch(`/workbench/categories/${current!.id}`, { name, version: current!.version })
    else { await api.delete(`/workbench/categories/${current!.id}`, { data: { version: current!.version } }); selectedCategory.value = '' }
    emit('changed'); ElMessage.success('问题类别已更新')
  } catch(cause) { error.value = failure(cause) } finally { busy.value = false }
}
async function setTemplate(item: KnowledgeEntry) {
  if (busy.value) return
  const targetCategory = selectedCategory.value && item.categories.includes(selectedCategory.value) ? selectedCategory.value : item.categories[0]
  if (!targetCategory) return
  busy.value = true
  try { await api.put(`/workbench/templates/${targetCategory}`, { document_id: item.id, version: item.version }); ElMessage.success('已设为该类别默认报告模板') }
  catch(cause) { error.value = failure(cause) } finally { busy.value = false }
}
async function changed() { await load(); emit('changed') }
onMounted(load)
</script>
<template>
  <section aria-label="Skill 与问题类别管理">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <div class="toolbar"><el-button type="primary" :disabled="busy" @click="edit()">新增 Skill 文件</el-button><el-button @click="emit('organize')">导入完整 Skill 文件夹</el-button><el-button :disabled="busy" @click="categoryAction('create')">新增问题类别</el-button><el-button @click="load" :loading="loading">刷新</el-button></div>
    <div class="skill-workspace">
      <aside class="knowledge-navigation" aria-label="Skill 类别目录"><button :class="{active:!selectedCategory}" @click="selectedCategory=''">全部问题类别</button><button v-for="item in categories" :key="item.id" :class="{active:selectedCategory===item.id}" @click="selectedCategory=item.id">{{ item.name }}</button><div v-if="category" class="category-actions"><el-button link :disabled="busy" @click="categoryAction('rename')">修改名称</el-button><el-button link type="danger" :disabled="busy || ['general','unknown'].includes(category.id)" @click="categoryAction('delete')">停用类别</el-button></div><el-divider /><span class="navigation-caption">我的待发布修订</span><button v-for="item in drafts" :key="item.id" @click="draftId=item.id;selected=null">{{ item.candidate.title }}<small>{{ contributionStatus(item.status) }}</small></button></aside>
      <section v-loading="loading" class="skill-content">
        <p class="field-hint">总领 Skill、子文件及报告格式按同一问题类别管理。文件夹整理保留依赖；新增和修改只在明确发布后影响诊断。</p>
        <el-table :data="visible"><el-table-column label="Skill 文件" min-width="230"><template #default="{row}"><strong>{{ row.title }}</strong><p class="table-description">{{ row.source_paths?.join(' / ') || '独立文档' }}</p></template></el-table-column><el-table-column label="用途" width="120"><template #default="{row}">{{ roles[row.role] || row.role }}</template></el-table-column><el-table-column label="状态" width="120"><template #default="{row}">{{ row.status==='ACTIVE'?'已发布':'未发布' }} · v{{ row.version }}</template></el-table-column><el-table-column label="操作" width="180"><template #default="{row}"><el-button link type="primary" @click="selected=row as KnowledgeEntry;draftId=''">查看</el-button><el-button link :disabled="busy" @click="row.status==='ACTIVE' ? edit(row as KnowledgeEntry) : emit('maintain')">编辑</el-button><el-button link type="danger" :disabled="busy || row.status!=='ACTIVE'" @click="remove(row as KnowledgeEntry)">删除</el-button></template></el-table-column><template #empty><el-empty description="此类别尚无 Skill，可新增文件或导入完整文件夹" /></template></el-table>
        <el-card v-if="selected" shadow="never" class="section-card"><div class="toolbar"><h3>{{ selected.title }}</h3><el-button v-if="selected.role==='report_template' && selected.status==='ACTIVE'" :disabled="busy" @click="setTemplate(selected)">设为类别默认报告模板</el-button><el-button link @click="selected=null">收起</el-button></div><pre class="document-text">{{ selected.content }}</pre></el-card>
      </section>
    </div>
    <el-drawer :model-value="!!draftId" @close="draftId=''" title="核对 Skill 发布版本" size="min(1250px, 97vw)" :close-on-click-modal="false"><ContributionDetail v-if="draftId" :id="draftId" :owner-id="ownerId" :review="true" @changed="changed" @close="draftId=''" /></el-drawer>
    <el-dialog v-model="dialog" :title="target?'修改 Skill 文件':'新增 Skill 文件'" width="min(960px, 96vw)" :close-on-click-modal="false"><el-alert v-if="formError" :title="formError" type="error" :closable="false" class="inline-alert" /><el-form label-position="top"><el-form-item label="Skill 标题"><el-input v-model="form.title" aria-label="Skill 标题" /></el-form-item><div class="form-columns"><el-form-item label="问题类别"><el-select v-model="form.categories" multiple aria-label="Skill 问题类别"><el-option v-for="item in categories" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="文件作用"><el-select v-model="form.role" aria-label="Skill 文件作用"><el-option v-for="(name,key) in roles" :key="key" :label="name" :value="String(key)" /></el-select></el-form-item></div><el-form-item label="文件路径（每行一个，保留相对引用）"><el-input v-model="form.paths" aria-label="Skill 文件路径" type="textarea" :rows="2" /></el-form-item><label class="file-choice">读取 Markdown 文件<input type="file" accept=".md,.markdown" aria-label="读取 Skill 文件" @change="upload" /></label><el-form-item label="Markdown 正文"><el-input v-model="form.content" aria-label="Skill Markdown" type="textarea" :rows="17" /></el-form-item><el-collapse><el-collapse-item title="高级：完整扩展属性与依赖元数据" name="metadata"><p class="field-hint">目录调整后同时核对交叉引用和包清单。内容类型、问题类别、用途及文件路径以上方字段为准。</p><el-input v-model="form.metadata" aria-label="Skill 扩展属性" type="textarea" :rows="9" /></el-collapse-item></el-collapse></el-form><template #footer><el-button :disabled="busy" @click="dialog=false">取消</el-button><el-button type="primary" :loading="busy" @click="save">保存变更并核对</el-button></template></el-dialog>
  </section>
</template>
