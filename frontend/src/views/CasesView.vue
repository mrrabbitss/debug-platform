<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useWorkbench, failure } from '../composables/useWorkbench'
import type { CaseItem } from '../types'
const router = useRouter()
const { config, canSubmit, caseCategories, loadConfig, categoryName } = useWorkbench()
const cases = ref<CaseItem[]>([])
const loading = ref(false), saving = ref(false), dialog = ref(false), query = ref(''), category = ref(''), advanced = ref(false)
const error = ref(''), formError = ref('')
const defaults = () => ({ title: '', problem_category: 'unknown', description: '', device_type: 'GW', chat_profile_id: null as string | null,
  model_egress_approved: true, device_model: '', firmware_version: '', topology: '', reproduction_steps: '', issue_time: '' })
const form = reactive(defaults())
const visible = computed(() => cases.value.filter(item => (!category.value || (item.problem_category || 'unknown') === category.value) && (!query.value.trim() || `${item.title} ${item.description}`.toLocaleLowerCase().includes(query.value.trim().toLocaleLowerCase()))))
const statusName = (value: string) => ({ DRAFT:'待处理',QUEUED:'等待诊断',RUNNING:'诊断中',ANALYZING:'诊断中',COMPLETED:'已生成诊断',ANALYZED:'已生成诊断',FAILED:'处理失败',RESOLVED:'已定位' } as Record<string, string>)[value] || value
const inProgress = computed(() => cases.value.filter(item => ['RUNNING','ANALYZING','QUEUED'].includes(item.status)).length)
async function load() {
  loading.value = true; error.value = ''
  try { await loadConfig(); cases.value = (await api.get('/cases')).data }
  catch(cause) { error.value = failure(cause) }
  finally { loading.value = false }
}
function create() {
  Object.assign(form, defaults())
  const preferred = config.value.preferences.chat_profile_id
  form.chat_profile_id = config.value.models.some(item => item.id === preferred) ? preferred || null : null
  formError.value = ''; advanced.value = false; dialog.value = true
}
async function save() {
  if (saving.value || !canSubmit.value) return
  if (form.title.trim().length < 2) { formError.value = '请填写至少两个字的问题标题'; return }
  saving.value = true; formError.value = ''
  try { const {data} = await api.post('/cases', { ...form, title: form.title.trim(), chat_profile_id: form.chat_profile_id || null }); dialog.value = false; await router.push(`/cases/${data.id}`) }
  catch(cause) { formError.value = failure(cause) }
  finally { saving.value = false }
}
onMounted(load)
</script>
<template>
  <div class="workspace-page">
    <div class="workspace-heading"><div><span class="eyebrow">DIAGNOSIS</span><h1>故障定位</h1><p class="muted">记录问题、收集证据，让诊断有据可循。</p></div><el-button type="primary" size="large" :disabled="!canSubmit || loading" @click="create">创建待定位案例</el-button></div>
    <div class="summary-strip"><div><strong>{{ cases.length }}</strong><span>全部案例</span></div><div><strong>{{ inProgress }}</strong><span>处理中</span></div><p class="muted">从上传 GW / AP 日志开始，逐步完善问题资料。</p></div>
    <div class="toolbar"><el-input v-model="query" aria-label="搜索问题" placeholder="搜索问题标题或现象" clearable class="search-input" /><el-select v-model="category" aria-label="筛选问题类别" clearable placeholder="全部问题类别" style="width:180px"><el-option v-for="item in caseCategories" :key="item.id" :label="item.name" :value="item.id" /></el-select><el-button :loading="loading" @click="load">刷新</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="inline-alert" />
    <el-card shadow="never"><el-table :data="visible" v-loading="loading" @row-click="(row:CaseItem) => router.push(`/cases/${row.id}`)" class="clickable-table">
      <el-table-column label="问题" min-width="250"><template #default="{row}"><router-link class="case-title-link" :to="`/cases/${row.id}`" @click.stop>{{ row.title }}</router-link><p class="table-description">{{ row.description || '待补充问题现象' }}</p></template></el-table-column>
      <el-table-column label="问题类别" width="130"><template #default="{row}">{{ categoryName(row.problem_category) }}</template></el-table-column>
      <el-table-column label="状态" width="150"><template #default="{row}"><el-tag effect="plain" :type="row.status==='FAILED'?'danger':'info'">{{ statusName(row.status) }}</el-tag></template></el-table-column><el-table-column prop="device_model" label="设备型号" width="130" />
      <el-table-column label="创建时间" width="180"><template #default="{row}">{{ new Date(row.created_at).toLocaleString() }}</template></el-table-column>
      <template #empty><el-empty :description="error ? '案例暂时无法加载，请刷新重试' : query || category ? '没有匹配的案例，试试其他条件' : '还没有案例，从创建一个问题开始'" /></template>
    </el-table></el-card>
    <el-dialog v-model="dialog" title="创建待定位案例" width="640px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving"><el-form label-position="top" @submit.prevent="save">
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" class="inline-alert" />
      <el-form-item label="问题标题" required><el-input v-model="form.title" aria-label="问题标题" maxlength="255" placeholder="用一句话描述问题" /></el-form-item>
      <el-form-item label="问题类别"><el-radio-group v-model="form.problem_category" aria-label="问题类别"><el-radio-button v-for="item in caseCategories" :key="item.id" :value="item.id">{{ item.name }}</el-radio-button></el-radio-group></el-form-item>
      <p v-if="form.problem_category==='unknown'" class="field-hint">不确定类别时选择“未知”，诊断会结合多类知识查找线索。</p>
      <el-form-item label="问题现象"><el-input v-model="form.description" aria-label="问题现象" type="textarea" :rows="3" placeholder="发生了什么，影响哪些设备，何时开始" /></el-form-item>
      <el-form-item label="诊断模型"><el-select v-model="form.chat_profile_id" aria-label="诊断模型" clearable placeholder="跟随系统默认"><el-option v-for="model in config.models" :key="model.id" :label="model.name + (model.active ? ' · 系统默认' : '')" :value="model.id" /></el-select></el-form-item>
      <p v-if="!config.models.length" class="field-hint">管理员尚未预设 Chat 模型。可以先创建案例和上传日志。</p>
      <div class="consent-panel"><el-switch v-model="form.model_egress_approved" aria-label="模型出站授权" active-text="允许模型分析" /><p>允许将问题资料、知识和脱敏证据发送到所选模型。可关闭，稍后在案例中调整。</p></div>
      <el-button text @click="advanced=!advanced">{{ advanced ? '收起补充信息' : '补充设备、拓扑等信息' }}</el-button>
      <div v-if="advanced" class="supplementary-fields"><el-form-item label="设备"><el-radio-group v-model="form.device_type"><el-radio value="GW">GW</el-radio><el-radio value="AP">AP</el-radio><el-radio value="OTHER">其他</el-radio></el-radio-group></el-form-item>
        <el-form-item label="型号"><el-input v-model="form.device_model" /></el-form-item><el-form-item label="固件版本"><el-input v-model="form.firmware_version" /></el-form-item>
        <el-form-item label="拓扑"><el-input v-model="form.topology" type="textarea" /></el-form-item><el-form-item label="复现步骤"><el-input v-model="form.reproduction_steps" type="textarea" /></el-form-item>
      </div>
    </el-form><template #footer><el-button :disabled="saving" @click="dialog=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">创建并上传日志</el-button></template></el-dialog>
  </div>
</template>
