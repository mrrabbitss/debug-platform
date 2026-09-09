<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { contributionsApi, contributionStatus, type KnowledgeContribution } from '../../api/knowledgeContributions'
import { failure, type ProblemCategory } from '../../composables/useWorkbench'
import type { KnowledgeEntry } from '../../types/workbench'
import ContributionDetail from './ContributionDetail.vue'
const props = defineProps<{ ownerId: string; categories: ProblemCategory[]; review?: boolean; canCreate?: boolean; initialId?: string }>()
const emit = defineEmits<{ changed: [] }>()
const entries = ref<KnowledgeContribution[]>([]), selectedId = ref(''), loading = ref(false), error = ref(''), query = ref(''), filter = ref('')
const dialog = ref(false), busy = ref(false), formError = ref(''), target = ref<KnowledgeEntry | null>(null)
const form = reactive({ title: '', content: '', category: 'unknown', operation: 'CREATE' as 'CREATE' | 'UPDATE' | 'DELETE' })
const visible = computed(() => entries.value.filter(item => (props.review ? item.status !== 'DRAFT' : item.owner_id === props.ownerId) && (!filter.value || item.status === filter.value) && `${item.candidate.title} ${item.candidate.content}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase())))
async function load() {
  loading.value = true; error.value = ''
  try { entries.value = await contributionsApi.list(!props.review, filter.value) } catch (cause) { error.value = failure(cause) } finally { loading.value = false }
}
function create(item?: KnowledgeEntry, operation: 'UPDATE' | 'DELETE' = 'UPDATE') {
  if (!props.canCreate) return
  target.value = item || null; formError.value = ''
  Object.assign(form, { title: item?.title || '', content: item?.content || '', category: item?.categories[0] || 'unknown', operation: item ? operation : 'CREATE' }); dialog.value = true
}
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (!/\.(md|markdown)$/i.test(file.name) || file.size > 500000) { formError.value = '请选择不超过 500 KB 的 UTF-8 Markdown 文件'; return }
  try { form.content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()); if (!form.title.trim()) form.title = file.name.replace(/\.(md|markdown)$/i, ''); formError.value = '' }
  catch { formError.value = '文件不是有效的 UTF-8 文本，请转换编码后重新上传' }
}
async function saveNew() {
  if (busy.value || !props.canCreate) return
  if (!form.title.trim() || !form.content.trim()) { formError.value = '请填写标题与 Markdown 内容'; return }
  busy.value = true; formError.value = ''
  try {
    const value = await contributionsApi.create(target.value ? { operation: form.operation, target_document_id: target.value.id, ...(form.operation === 'DELETE' ? {} : { title: form.title.trim(), content: form.content }) } : {
      operation: 'CREATE', content_kind: 'KNOWLEDGE', title: form.title.trim(), content: form.content, source_type: 'document', metadata: { problem_categories: [form.category], knowledge_role: 'prior_knowledge' }
    })
    dialog.value = false; selectedId.value = value.id; await load(); emit('changed'); ElMessage.success('已保存为自己的草稿，提交审核后才会共享')
  } catch (cause) { formError.value = failure(cause) } finally { busy.value = false }
}
async function changed() { await load(); emit('changed') }
defineExpose({ create, reload: load })
watch(filter, load)
watch(() => props.initialId, value => { if (value) selectedId.value = value }, { immediate: true })
onMounted(load)
</script>
<template>
  <section :aria-label="review ? '知识审批队列' : '我的知识提交'">
    <div class="toolbar"><el-input v-model="query" clearable :aria-label="review?'搜索审批':'搜索我的提交'" placeholder="搜索标题或正文" class="search-input" /><el-select v-model="filter" aria-label="提交状态" clearable placeholder="全部状态" style="width:180px"><el-option v-for="state in ['DRAFT','SUBMITTED','RETURNED','REJECTED','APPROVED','PUBLISHING','PUBLISHED','FAILED']" :key="state" :value="state" :label="contributionStatus(state)" /></el-select><el-button @click="load" :loading="loading">刷新提交</el-button><el-button v-if="canCreate && !review" type="primary" @click="create()">新增 / 上传 Wiki</el-button></div>
    <p class="field-hint">{{ review ? '审核案例结论、知识与 Skill 变更建议；可以先对话修正，再批准精确版本。' : '自己的草稿可以直接修改和删除；共享内容及 Skill 的修改申请经过专家或管理员审核后生效。' }}</p>
    <p v-if="entries.length===500" class="field-hint">当前显示最近 500 条，请按状态缩小范围。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <el-table :data="visible" v-loading="loading"><el-table-column label="提交内容" min-width="220"><template #default="{row}"><strong>{{ row.candidate.title }}</strong><p class="table-description">{{ row.source_library_id ? '案例结论' : row.source_curation_id ? 'AI 案例提炼' : row.content_kind==='SKILL'?'Skill 建议':'知识 Wiki' }} · {{ {CREATE:'新增',UPDATE:'修改',DELETE:'申请删除'}[row.operation as 'CREATE'|'UPDATE'|'DELETE'] }}</p></template></el-table-column><el-table-column label="状态" width="180"><template #default="{row}"><el-tag :type="row.status==='PUBLISHED'?'success':row.status==='FAILED'?'danger':'info'">{{ contributionStatus(row.status) }}</el-tag></template></el-table-column><el-table-column v-if="review" prop="owner_id" label="提交人" min-width="130" show-overflow-tooltip /><el-table-column label="版本" width="90"><template #default="{row}">v{{ row.version }}</template></el-table-column><el-table-column label="操作" width="130"><template #default="{row}"><el-button link type="primary" @click="selectedId=row.id">{{ review && row.status==='SUBMITTED'?'核对并审核':'打开详情' }}</el-button></template></el-table-column><template #empty><el-empty :description="error?'暂时无法加载，请刷新':'暂无符合条件的提交'" /></template></el-table>
    <el-drawer :model-value="!!selectedId" @close="selectedId=''" title="知识提交与版本审核" size="min(1250px, 97vw)" :close-on-click-modal="false"><ContributionDetail v-if="selectedId" :key="selectedId" :id="selectedId" :owner-id="ownerId" :review="review" @changed="changed" @close="selectedId=''" /></el-drawer>
    <el-dialog v-model="dialog" :title="form.operation==='CREATE'?'新增知识草稿':form.operation==='DELETE'?'申请删除共享内容':'提出修改建议'" width="780px" :close-on-click-modal="false">
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" class="inline-alert" />
      <p v-if="target" class="field-hint">基于《{{ target.title }}》v{{ target.version }} 提出建议；原共享内容将在审核发布成功前保持可用。</p>
      <el-form label-position="top"><el-form-item label="标题"><el-input v-model="form.title" aria-label="新知识标题" :readonly="form.operation==='DELETE'" /></el-form-item>
        <el-form-item v-if="!target" label="问题类别"><el-select v-model="form.category" aria-label="知识问题类别"><el-option v-for="category in categories" :key="category.id" :label="category.name" :value="category.id" /></el-select></el-form-item>
        <label v-if="form.operation!=='DELETE'" class="file-choice">从 Markdown 文件读取<input type="file" accept=".md,.markdown" aria-label="上传 Wiki Markdown" @change="upload" /></label>
        <el-form-item label="Markdown"><el-input v-model="form.content" aria-label="新知识 Markdown" type="textarea" :rows="15" :readonly="form.operation==='DELETE'" /></el-form-item>
      </el-form><template #footer><el-button :disabled="busy" @click="dialog=false">取消</el-button><el-button type="primary" :loading="busy" @click="saveNew">保存我的草稿</el-button></template>
    </el-dialog>
  </section>
</template>
