<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useWorkbench, failure } from '../composables/useWorkbench'
import type { KnowledgeEntry, LibraryEntry } from '../types/workbench'
import LibrarySubmissionDialog from '../components/knowledge/LibrarySubmissionDialog.vue'
import KnowledgeContributionsPane from '../components/knowledge/KnowledgeContributionsPane.vue'
import KnowledgeCurationView from './KnowledgeCurationView.vue'
const { config, canManageKnowledge, canSubmit, loadConfig, categoryName } = useWorkbench()
const route = useRoute(), router = useRouter()
const tab = ref('wiki'), submissionTab = ref('drafts'), category = ref(''), role = ref(''), query = ref(''), error = ref('')
const documents = ref<KnowledgeEntry[]>([]), library = ref<LibraryEntry[]>([]), selectedDocument = ref<KnowledgeEntry | null>(null), selectedRecord = ref<LibraryEntry | null>(null)
const submission = ref(false), recordView = ref('conclusion'), loading = ref(false), ready = ref(false)
const contributions = ref<InstanceType<typeof KnowledgeContributionsPane> | null>(null)
const visible = computed(() => documents.value.filter(item => item.status === 'ACTIVE' && (tab.value === 'skills' ? item.content_kind === 'SKILL' : item.content_kind === 'KNOWLEDGE') && (!category.value || item.categories.includes(category.value)) && (!role.value || item.role === role.value) && `${item.title} ${item.content}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const records = computed(() => library.value.filter(item => (item.status === 'CONFIRMED' || item.owner_id === config.value.principal.id) && `${item.title} ${item.content} ${item.reviewed_conclusion || ''}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
const recordContent = computed(() => recordView.value === 'reviewed' ? selectedRecord.value?.reviewed_conclusion : recordView.value === 'report' ? selectedRecord.value?.report_markdown : selectedRecord.value?.content)
const stateName = (state: string) => ({ PENDING: '待专家 / 管理员审核', CONFIRMED: '已确认', REJECTED: '已退回' })[state as 'PENDING'|'CONFIRMED'|'REJECTED'] || state
function syncTab() { const requested = String(route.query.tab || 'wiki'); tab.value = ['wiki','library','skills','submissions'].includes(requested) && (requested !== 'submissions' || canSubmit.value) ? requested : 'wiki'; submissionTab.value = route.query.extract==='1' ? 'extract' : 'drafts' }
watch(() => route.query, syncTab)
async function load() {
  loading.value = true; error.value = ''
  try { const [knowledge, shared] = await Promise.all([api.get<KnowledgeEntry[]>('/workbench/knowledge'), api.get<LibraryEntry[]>('/workbench/library')]); documents.value = knowledge.data; library.value = shared.data }
  catch(cause) { error.value = failure(cause) } finally { loading.value = false }
}
async function propose(item?: KnowledgeEntry, operation: 'UPDATE' | 'DELETE' = 'UPDATE') {
  selectedDocument.value = null; tab.value = 'submissions'; submissionTab.value = 'drafts'
  await router.replace({ path: '/knowledge', query: { tab: 'submissions' } }); await nextTick(); contributions.value?.create(item, operation)
}
function openRecord(item: LibraryEntry, report = false) { selectedRecord.value = item; selectedDocument.value = null; recordView.value = item.reviewed_conclusion ? 'reviewed' : report ? 'report' : 'conclusion' }
async function extract() { await router.replace({ path: '/knowledge', query: { tab: 'submissions', extract: '1' } }) }
onMounted(async () => { try { await loadConfig(); ready.value = true; syncTab(); await load() } catch(cause) { error.value = failure(cause) } })
</script>
<template>
  <div class="workspace-page">
    <div class="workspace-heading"><div><span class="eyebrow">KNOWLEDGE</span><h1>知识库</h1><p class="muted">查阅团队知识与诊断方法，把本次案例沉淀为下一次经验。</p></div><div v-if="canSubmit" class="toolbar"><el-button @click="submission=true">提交已定位案例</el-button><el-button @click="extract">AI 案例提炼</el-button><el-button type="primary" @click="propose()">上传 Wiki Markdown</el-button></div></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link @click="load">重新加载</el-button></el-alert>
    <el-tabs v-if="ready" v-model="tab" @tab-change="name=>router.replace({path:'/knowledge',query:{tab:String(name)}})">
      <el-tab-pane label="知识 Wiki" name="wiki" /><el-tab-pane label="案例与报告" name="library" /><el-tab-pane label="诊断 Skill · 只读" name="skills" /><el-tab-pane v-if="canSubmit" label="我的提交" name="submissions" />
    </el-tabs>
    <div v-if="ready && ['wiki','skills'].includes(tab)" class="knowledge-workspace">
      <aside class="knowledge-navigation" aria-label="知识分类"><span class="navigation-caption">问题类别</span><button :class="{active:!category}" @click="category='';role=''">全部类别</button><div v-for="item in config.categories" :key="item.id"><button :class="{active:category===item.id}" :aria-expanded="category===item.id" @click="category=item.id;role=''">{{ item.name }}</button><div v-if="category===item.id && tab==='skills'" class="knowledge-subcategories"><button v-for="(name,key) in config.knowledge_roles" :key="key" :class="{active:role===key}" @click="role=String(key)">{{ name }}</button></div></div></aside>
      <section class="knowledge-content" v-loading="loading">
        <el-alert v-if="tab==='skills'" title="此处只读诊断时使用的 Skill。可以提出修改建议，由专家或管理员审核后生效。" type="info" :closable="false" class="inline-alert" />
        <div class="toolbar"><el-input v-model="query" clearable aria-label="搜索知识" placeholder="搜索标题或正文" class="search-input" /><el-button @click="load">刷新知识</el-button><el-button v-if="canManageKnowledge && tab==='skills'" type="primary" plain @click="router.push('/knowledge-management')">管理 Skill 与类别</el-button></div>
        <div class="collection-heading"><h2>{{ category ? categoryName(category) : tab==='skills'?'全部诊断 Skill':'全部知识 Wiki' }}<span v-if="role"> / {{ config.knowledge_roles[role] }}</span></h2><span class="muted">{{ visible.length }} 份已发布资料</span></div>
        <div v-if="visible.length" class="knowledge-cards"><button v-for="item in visible" :key="item.id" class="knowledge-card" @click="selectedDocument=item;selectedRecord=null"><div class="knowledge-card-meta"><el-tag size="small" effect="plain">{{ tab==='skills' ? config.knowledge_roles[item.role] || item.role : '知识 Wiki' }}</el-tag><span>v{{ item.version }}</span></div><h3>{{ item.title }}</h3><p>{{ item.content.replace(/[#`*>]/g,'').slice(0,120) }}</p><small>{{ item.categories.map(categoryName).join(' / ') }}</small></button></div>
        <el-empty v-else :description="query?'没有匹配内容，试试其他关键词':tab==='skills'?'此分类尚未发布 Skill':'此分类还没有共享知识'" />
      </section>
    </div>
    <section v-if="ready && tab==='library'">
      <div class="toolbar"><el-input v-model="query" aria-label="搜索案例与报告" clearable placeholder="搜索案例或定位结果" class="search-input" /><el-button @click="load">刷新案例</el-button></div><p class="field-hint">已确认的案例与报告向全员共享，自己的待审记录显示审核状态。</p>
      <el-table :data="records" v-loading="loading"><el-table-column prop="title" label="案例" min-width="220" /><el-table-column label="类别" width="150"><template #default="{row}">{{ categoryName(row.problem_category) }}</template></el-table-column><el-table-column label="状态" min-width="160"><template #default="{row}"><el-tag effect="plain">{{ stateName(row.status) }}</el-tag></template></el-table-column><el-table-column label="操作" width="200"><template #default="{row}"><el-button link type="primary" @click="openRecord(row as LibraryEntry)">查看案例</el-button><el-button v-if="row.report_markdown" link type="primary" @click="openRecord(row as LibraryEntry,true)">阅读报告</el-button></template></el-table-column><template #empty><el-empty description="暂无案例与报告" /></template></el-table>
    </section>
    <section v-if="ready && tab==='submissions' && canSubmit"><el-tabs v-model="submissionTab"><el-tab-pane label="草稿与审核进度" name="drafts" /><el-tab-pane label="AI 案例提炼" name="extract" /></el-tabs><KnowledgeContributionsPane v-if="submissionTab==='drafts'" ref="contributions" :owner-id="config.principal.id || ''" :categories="config.categories" :can-create="canSubmit" :initial-id="String(route.query.contribution || '')" @changed="load" /><KnowledgeCurationView v-else /></section>
    <el-drawer :model-value="!!selectedDocument || !!selectedRecord" @close="selectedDocument=null;selectedRecord=null" :title="selectedDocument?.title || selectedRecord?.title" size="min(900px, 95vw)">
      <template v-if="selectedDocument"><div class="toolbar"><el-tag>{{ selectedDocument.content_kind==='SKILL'?'只读 Skill':'共享知识' }}</el-tag><span>v{{ selectedDocument.version }} · {{ selectedDocument.categories.map(categoryName).join(' / ') }}</span></div><p v-if="selectedDocument.source_paths?.length" class="field-hint">{{ selectedDocument.source_paths.join('、') }}</p><pre class="document-text">{{ selectedDocument.content }}</pre><div v-if="canSubmit" class="review-actions"><el-button type="primary" plain @click="propose(selectedDocument)">提出修改建议</el-button><el-button v-if="selectedDocument.content_kind!=='SKILL'" type="danger" plain @click="propose(selectedDocument,'DELETE')">申请删除</el-button></div></template>
      <template v-if="selectedRecord"><div class="toolbar"><el-tag>{{ stateName(selectedRecord.status) }}</el-tag><el-radio-group v-model="recordView" aria-label="案例内容版本"><el-radio-button v-if="selectedRecord.reviewed_conclusion" value="reviewed">审核后共享内容</el-radio-button><el-radio-button value="conclusion">提交原稿</el-radio-button><el-radio-button v-if="selectedRecord.report_markdown" value="report">原始诊断报告</el-radio-button></el-radio-group></div><p class="field-hint">{{ recordView==='reviewed'?'以下为专家或管理员修订并批准的共享版本。':'以下为提交时保存的原始内容，审核后的共享版本可能已有修正。' }}</p><pre class="document-text" data-testid="library-record-content">{{ recordContent }}</pre></template>
    </el-drawer>
    <LibrarySubmissionDialog v-if="canSubmit" v-model="submission" :categories="config.categories" @submitted="load();tab='library'" />
  </div>
</template>
