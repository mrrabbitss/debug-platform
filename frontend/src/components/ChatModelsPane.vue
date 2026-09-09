<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { failure } from '../composables/useWorkbench'
import type { ModelProfile } from '../types'
const props = withDefaults(defineProps<{ canShare: boolean; canCreate?: boolean }>(), { canCreate: true })
const emit = defineEmits<{ changed: [] }>()
const models = ref<ModelProfile[]>([]), loading = ref(false), busy = ref(''), error = ref(''), formError = ref(''), dialog = ref(false)
const editing = ref<ModelProfile | null>(null)
const defaults = () => ({ name: '', model_name: '', base_url: '', api_key: '', clear_api_key: false, visibility: (props.canShare ? 'SHARED' : 'PRIVATE') as 'SHARED' | 'PRIVATE', enabled: true, proxy_url: '', clear_proxy_url: false, temperature: 0.1, max_tokens: 0, context_window_tokens: 0, context_reserved_output_tokens: 0, timeout_seconds: 300, max_retries: 2, thinking_mode: 'inherit' })
const form = reactive(defaults())
async function load() {
  loading.value = true; error.value = ''
  try { models.value = (await api.get<ModelProfile[]>('/system/models', { params: { task_type: 'chat' } })).data.filter(item => item.task_type === 'chat') }
  catch (cause) { error.value = failure(cause) }
  finally { loading.value = false }
}
function open(item?: ModelProfile) {
  if (!item && !props.canCreate) return
  if (item && !item.can_manage) return
  editing.value = item || null; formError.value = ''; Object.assign(form, defaults())
  if (item) Object.assign(form, { name: item.name, model_name: item.model_name, base_url: item.base_url || '', visibility: item.visibility || 'SHARED', enabled: item.enabled,
    ...Object.fromEntries(['temperature', 'max_tokens', 'context_window_tokens', 'context_reserved_output_tokens', 'timeout_seconds', 'max_retries', 'thinking_mode'].filter(key => item.config[key] != null).map(key => [key, item.config[key]])) })
  dialog.value = true
}
async function save() {
  if (busy.value) return
  if (!form.name.trim() || !form.model_name.trim()) { formError.value = '请填写配置名称与模型名称'; return }
  try { const url = new URL(form.base_url.trim()); if (!['http:', 'https:'].includes(url.protocol)) throw Error() }
  catch { formError.value = 'Base URL 需要是完整的 HTTP 或 HTTPS 地址'; return }
  busy.value = 'save'; formError.value = ''
  try {
    const payload: Record<string, unknown> = {
      name: form.name.trim(), model_name: form.model_name.trim(), mode: 'api', provider: 'openai_compatible',
      base_url: form.base_url.trim(), visibility: props.canShare ? form.visibility : 'PRIVATE', enabled: form.enabled,
      config: { ...editing.value?.config, temperature: form.temperature, thinking_mode: form.thinking_mode, timeout_seconds: form.timeout_seconds, max_retries: form.max_retries,
        max_tokens: form.max_tokens || null, context_window_tokens: form.context_window_tokens || null, context_reserved_output_tokens: form.context_reserved_output_tokens || null }
    }
    if (form.api_key) payload.api_key = form.api_key
    if (form.proxy_url.trim()) payload.proxy_url = form.proxy_url.trim()
    if (editing.value) { payload.clear_api_key = form.clear_api_key && !form.api_key; payload.clear_proxy_url = form.clear_proxy_url && !form.proxy_url.trim(); await api.patch(`/system/models/${editing.value.id}`, payload) }
    else { payload.task_type = 'chat'; await api.post('/system/models', payload) }
    form.api_key = ''; form.proxy_url = ''; dialog.value = false; await load(); emit('changed'); ElMessage.success('诊断模型已保存')
  } catch (cause) { formError.value = failure(cause) }
  finally { busy.value = '' }
}
async function action(item: ModelProfile, kind: 'test' | 'activate' | 'delete') {
  if (busy.value || (kind !== 'test' && !item.can_manage)) return
  if (kind === 'activate' && (!props.canShare || item.visibility !== 'SHARED')) return
  if (kind === 'delete') {
    try { await ElMessageBox.confirm(`删除“${item.name}”的诊断 API 配置？选择了它的用户需要重新选择模型。`, '删除诊断模型', { type: 'warning' }) }
    catch { return }
  }
  busy.value = `${kind}-${item.id}`; error.value = ''
  try {
    if (kind === 'delete') await api.delete(`/system/models/${item.id}`)
    else {
      const { data } = await api.post(`/system/models/${item.id}/${kind}`)
      if (kind === 'test' && (data.ok === false || data.success === false)) throw new Error(data.message || data.error || '连接测试未通过')
    }
    ElMessage.success(kind === 'test' ? '连接测试通过' : kind === 'activate' ? '共享默认模型已更新；个人选择保持优先' : '诊断模型已删除')
    if (kind !== 'test') { await load(); emit('changed') }
  } catch (cause) { error.value = failure(cause) }
  finally { busy.value = '' }
}
onMounted(load)
</script>
<template>
  <section aria-label="诊断 API 配置">
    <div class="collection-heading"><div><h2>诊断 API 配置</h2><p class="muted">{{ canShare ? '新模型默认全员可用，也可设为仅自己使用。' : '你添加的模型仅自己可见和使用。' }}API Key 由服务器保存，不向其他用户展示。</p></div><el-button v-if="canCreate" type="primary" @click="open()">添加诊断 API</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link @click="load">刷新</el-button></el-alert>
    <el-table :data="models" v-loading="loading">
      <el-table-column label="模型" min-width="190"><template #default="{row}"><strong>{{ row.name }}</strong><p class="table-description">{{ row.model_name }}</p></template></el-table-column>
      <el-table-column label="来源" width="150"><template #default="{row}"><el-tag effect="plain">{{ row.visibility === 'PRIVATE' ? '仅自己使用' : '全员共享' }}</el-tag><p v-if="row.owner_name" class="table-description">{{ row.owner_name }}</p></template></el-table-column>
      <el-table-column label="状态" width="140"><template #default="{row}"><el-tag v-if="row.is_active" type="success">共享默认</el-tag><span v-else>{{ row.enabled ? '可用' : '已停用' }}</span></template></el-table-column>
      <el-table-column label="操作" min-width="270"><template #default="{row}">
        <el-button link :disabled="!!busy || !row.enabled" @click="action(row as ModelProfile,'test')">测试连接</el-button>
        <el-button v-if="row.can_manage" link type="primary" :disabled="!!busy || row.provider!=='openai_compatible'" @click="open(row as ModelProfile)">编辑</el-button>
        <el-button v-if="canShare && row.can_manage && row.visibility==='SHARED' && !row.is_active" link type="primary" :disabled="!!busy || !row.enabled || row.provider==='mock'" @click="action(row as ModelProfile,'activate')">设为共享默认</el-button>
        <el-button v-if="row.can_manage" link type="danger" :disabled="!!busy" @click="action(row as ModelProfile,'delete')">删除</el-button>
      </template></el-table-column>
      <template #empty><el-empty description="暂无可见模型，可以添加自己的诊断 API" /></template>
    </el-table>
    <el-dialog v-model="dialog" :title="editing ? '编辑诊断 API' : '添加诊断 API'" width="650px" :close-on-click-modal="false" :show-close="!busy" @closed="form.api_key='';form.proxy_url=''">
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" class="inline-alert" />
      <el-form label-position="top" @submit.prevent="save">
        <div class="form-columns"><el-form-item label="配置名称" required><el-input v-model="form.name" aria-label="配置名称" placeholder="用于区分模型来源" /></el-form-item><el-form-item label="模型名称" required><el-input v-model="form.model_name" aria-label="模型名称" placeholder="API 提供的 model 名称" /></el-form-item></div>
        <el-form-item label="Base URL" required><el-input v-model="form.base_url" aria-label="Base URL" placeholder="https://api.example.com/v1" /><p class="field-hint">可直接填写内网或外网 HTTP(S) 地址，使用 OpenAI 兼容 Chat API。</p></el-form-item>
        <el-form-item label="API Key"><el-input v-model="form.api_key" aria-label="模型 API Key" type="password" show-password autocomplete="new-password" :placeholder="editing?.api_key_configured ? '已配置；留空保留，填写则替换' : '服务需要鉴权时填写'" /><el-checkbox v-if="editing?.api_key_configured" v-model="form.clear_api_key" :disabled="!!form.api_key">清除已保存的 Key</el-checkbox></el-form-item>
        <el-form-item v-if="canShare" label="使用范围"><el-radio-group v-model="form.visibility" aria-label="模型使用范围"><el-radio value="SHARED">全员共享</el-radio><el-radio value="PRIVATE" :disabled="editing?.is_active">仅自己使用</el-radio></el-radio-group><p v-if="editing?.is_active" class="field-hint">共享默认模型需先更换默认，才能改为私有。</p></el-form-item>
        <el-form-item label="可用状态"><el-switch v-model="form.enabled" active-text="启用" /></el-form-item>
        <el-collapse><el-collapse-item title="代理、推理及上下文参数" name="advanced">
          <el-form-item label="模型代理"><el-input v-model="form.proxy_url" type="password" show-password autocomplete="off" :placeholder="editing?.proxy_url_configured ? '已配置；留空保留' : '可选：仅此模型使用的 HTTP(S) 代理'" /><el-checkbox v-if="editing?.proxy_url_configured" v-model="form.clear_proxy_url">清除原代理</el-checkbox></el-form-item>
          <div class="form-columns"><el-form-item label="温度"><el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" /></el-form-item><el-form-item label="思考模式"><el-select v-model="form.thinking_mode"><el-option label="沿用模型默认" value="inherit" /><el-option label="开启" value="enabled" /><el-option label="关闭" value="disabled" /></el-select></el-form-item><el-form-item label="最大输出 tokens（0 为默认）"><el-input-number v-model="form.max_tokens" :min="0" /></el-form-item><el-form-item label="上下文窗口 tokens（0 为默认）"><el-input-number v-model="form.context_window_tokens" :min="0" /></el-form-item><el-form-item label="预留输出 tokens（0 为默认）"><el-input-number v-model="form.context_reserved_output_tokens" :min="0" /></el-form-item><el-form-item label="超时（秒）"><el-input-number v-model="form.timeout_seconds" :min="1" :max="3600" /></el-form-item><el-form-item label="重试次数"><el-input-number v-model="form.max_retries" :min="0" :max="10" /></el-form-item></div>
        </el-collapse-item></el-collapse>
      </el-form>
      <template #footer><el-button :disabled="!!busy" @click="dialog=false">取消</el-button><el-button type="primary" :loading="busy==='save'" @click="save">保存诊断 API</el-button></template>
    </el-dialog>
  </section>
</template>
