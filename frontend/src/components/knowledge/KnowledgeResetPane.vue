<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import { failure } from '../../composables/useWorkbench'
import type { Job } from '../../types'
interface ResetPreview {
  operation_id: string; source_sha256: string; preview_hash: string
  target: { data_root: string; database_path: string }
  counts: Record<string, number>
  manifest: { path: string; role: string; bytes: number; sha256: string; source_sha256: string; references: unknown[]; adaptation_diff: string }[]
  preserved: string[]
}
const props = defineProps<{ roles: Record<string, string> }>()
const operationId = ref(`reset-${Date.now()}-${Math.random().toString(16).slice(2,10)}`), dataRoot = ref(''), busy = ref(false), error = ref(''), checked = ref(false), consent = ref(true)
const preview = ref<ResetPreview | null>(null), state = ref<{operation: Record<string, any>;job: Job | null} | null>(null)
const savedOperation = ref(localStorage.getItem('gwap_last_knowledge_reset') || '')
const countLabels: Record<string,string> = { knowledge_and_skills:'当前知识与 Skill', currently_active:'其中已生效', skills:'其中 Skill', drafts:'未发布修订', global_memories:'全局经验', preserved_chunks:'保留的历史分块', preserved_publications:'保留的发布记录' }
const statusName = (value: string) => ({APPROVED:'已审批，等待导入',BUILDING:'正在导入并构建索引',PUBLISHED:'已发布',FAILED:'重置失败，旧版本与审批保留',CANCELLED:'已取消'} as Record<string,string>)[value] || value
watch([dataRoot, operationId],()=>{preview.value=null;checked.value=false})
async function inspect() {
  if (busy.value || !dataRoot.value.trim()) return
  busy.value=true;error.value='';checked.value=false;preview.value=null;state.value=null
  try { preview.value=(await api.post<ResetPreview>('/workbench/knowledge-reset/preview',{operation_id:operationId.value,data_root:dataRoot.value.trim()})).data }
  catch(cause){error.value=failure(cause)} finally{busy.value=false}
}
async function confirm() {
  if (busy.value || !preview.value || !checked.value || !consent.value) return
  const exact=preview.value;busy.value=true;error.value='';checked.value=false
  localStorage.setItem('gwap_last_knowledge_reset',exact.operation_id);savedOperation.value=exact.operation_id
  try { state.value=(await api.post('/workbench/knowledge-reset/confirm',{operation_id:exact.operation_id,data_root:exact.target.data_root,expected_source_sha256:exact.source_sha256,expected_preview_hash:exact.preview_hash,confirmed:true,model_egress_approved:consent.value},{timeout:180000})).data;preview.value=null }
  catch(cause){error.value=failure(cause)+'；若请求已发送，请先刷新此操作状态，避免重新创建操作。'} finally{busy.value=false}
}
async function refresh() {
  if (busy.value || !savedOperation.value) return
  busy.value=true;error.value=''
  try {state.value=(await api.get(`/workbench/knowledge-reset/${encodeURIComponent(savedOperation.value)}`)).data;if(state.value?.operation)preview.value=null}
  catch(cause){error.value=failure(cause)}finally{busy.value=false}
}
onMounted(()=>{if(savedOperation.value)void refresh()})
</script>
<template>
  <section aria-label="一次性知识重置">
    <p class="muted">清空当前知识和 Skill 的使用版本，再导入服务器预设的组网 Skill 包。案例、报告和必要历史引用保留。服务器先完成一致性备份，再保存审批与发布任务。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <el-form label-position="top"><div class="form-columns"><el-form-item label="本次操作编号"><el-input v-model="operationId" aria-label="重置操作编号" :disabled="busy" /></el-form-item><el-form-item label="服务器数据根目录"><el-input v-model="dataRoot" aria-label="服务器数据根目录" :disabled="busy" placeholder="填写该实例实际的数据根目录" /></el-form-item></div><p class="field-hint">导入源由服务器的 KNOWLEDGE_RESET_SOURCE_ZIP 预先指定。页面只核对已配置源，不提交新的 ZIP 路径。</p><el-button :disabled="!dataRoot.trim() || busy" @click="inspect">预览清理与导入清单</el-button></el-form>
    <template v-if="preview">
      <el-descriptions :column="1" border class="section-card"><el-descriptions-item label="数据根目录">{{ preview.target.data_root }}</el-descriptions-item><el-descriptions-item label="数据库">{{ preview.target.database_path }}</el-descriptions-item><el-descriptions-item label="源文件 SHA-256"><span class="preserve-lines">{{ preview.source_sha256 }}</span></el-descriptions-item><el-descriptions-item v-for="(value,key) in preview.counts" :key="key" :label="countLabels[key] || String(key)">{{ value }}</el-descriptions-item></el-descriptions>
      <h3>完整导入与适配清单 · {{ preview.manifest.length }} 个文件</h3><div v-for="item in preview.manifest" :key="item.path" class="revision-entry"><strong class="preserve-lines">{{ item.path }}</strong><p>{{ props.roles[item.role] || item.role }} · {{ item.bytes }} bytes</p><pre v-if="item.adaptation_diff" class="document-text">{{ item.adaptation_diff }}</pre><p v-else class="field-hint">原文不变。</p><details v-if="item.references?.length"><summary>文件引用</summary><pre class="document-text">{{ JSON.stringify(item.references,null,2) }}</pre></details></div>
      <div class="review-actions"><el-checkbox v-model="checked" :disabled="busy">已核对全部清理范围、完整文件清单和适配差异</el-checkbox><el-checkbox v-model="consent" :disabled="busy">允许使用当前 Embedding 模型构建新索引</el-checkbox><div class="toolbar"><el-button type="danger" :disabled="!checked || !consent || busy" :loading="busy" @click="confirm">确认备份并重置导入</el-button></div></div>
    </template>
    <div v-if="savedOperation" class="section-card"><div class="toolbar"><span class="preserve-lines">已保存操作：{{ savedOperation }}</span><el-button :disabled="busy" @click="refresh">刷新重置状态</el-button></div><el-alert v-if="state" :title="`${statusName(state.operation.status)} · ${state.job?.message || '审批与任务已保存'}`" :type="state.job?.status==='FAILED'?'error':state.job?.status==='COMPLETED'?'success':'info'" :closable="false" /><p v-if="state?.operation.backup" class="field-hint preserve-lines">备份：{{ state.operation.backup.path }} · SHA-256 {{ state.operation.backup.sha256 }}</p></div>
  </section>
</template>
