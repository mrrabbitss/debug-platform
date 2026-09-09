<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { useWorkbench, failure } from '../composables/useWorkbench'
import ChatModelsPane from '../components/ChatModelsPane.vue'
import ChatModelSelect from '../components/ChatModelSelect.vue'
import SettingsView from './SettingsView.vue'
import SecurityView from './SecurityView.vue'
import { useRoute, useRouter } from 'vue-router'
const { config, isAdmin, canManageKnowledge, canSubmit, roleLabel, configLoading, loadConfig } = useWorkbench()
const route = useRoute(), router = useRouter()
const tab = ref('personal'), error = ref(''), saving = ref(false), loaded = ref(false)
const selectedModel = ref<string | null>(null)
function syncTab() {
  const requested = String(route.query.tab || 'personal')
  tab.value = requested === 'security' && canManageKnowledge.value ? 'security' : ['retrieval', 'models'].includes(requested) && isAdmin.value ? 'retrieval' : 'personal'
}
watch(() => route.query.tab, syncTab)
async function load() {
  error.value = ''
  try { await loadConfig(); selectedModel.value = config.value.preferences.chat_profile_id || null; loaded.value = true; syncTab() }
  catch(cause) { loaded.value = false; error.value = failure(cause) }
}
async function refreshModels() { try { await loadConfig() } catch(cause) { error.value = failure(cause) } }
async function save() {
  if (saving.value || !loaded.value) return
  saving.value = true; error.value = ''
  try { await api.put('/workbench/preferences', { chat_profile_id: selectedModel.value || null }); await loadConfig(); ElMessage.success('已保存，将统一用于你的新诊断、问答和 AI 提炼') }
  catch(cause) { error.value = failure(cause) }
  finally { saving.value = false }
}
onMounted(load)
</script>
<template>
  <div class="workspace-page"><div class="workspace-heading"><div><span class="eyebrow">PREFERENCES</span><h1>系统设置</h1><p class="muted">选择统一使用的诊断模型，管理个人 API 配置。</p></div><el-tag effect="plain">{{ roleLabel }}</el-tag></div>
    <el-alert v-if="route.query.notice" :title="route.query.notice==='admin-required' ? '此配置仅限管理员访问，已返回个人设置。' : route.query.notice==='role-required' ? '当前角色无权访问该页面，已返回个人设置。' : '暂时无法验证权限，请检查连接后重试。'" type="warning" :closable="false" class="inline-alert" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link @click="load">重新加载</el-button></el-alert>
    <el-tabs v-model="tab" @tab-change="name => router.replace({path:'/settings',query:{tab:String(name)}})">
      <el-tab-pane label="诊断模型" name="personal">
        <el-card shadow="never" v-loading="configLoading" class="settings-card">
          <h2>我的统一模型选择</h2><p class="muted">个人选择优先；留空时跟随共享默认。新诊断、问答和 AI 提炼使用同一来源，运行中的任务保留启动时配置。</p>
          <div class="toolbar"><ChatModelSelect v-model="selectedModel" :models="config.models" label="个人诊断模型" placeholder="跟随共享默认" /><el-button type="primary" :disabled="!loaded" :loading="saving" @click="save">保存偏好</el-button></div>
          <p v-if="loaded&&!config.models.length" class="field-hint">暂无可用模型，可在下方添加自己的诊断 API。</p>
          <el-alert v-if="selectedModel&&!config.models.some(item=>item.id===selectedModel)" title="原模型已停用或不再可见，请重新选择；平台不会悄悄换用其他来源。" type="warning" :closable="false" />
          <el-alert v-else-if="config.model_selection?.error" :title="config.model_selection.error" type="warning" :closable="false" />
        </el-card>
        <el-card v-if="loaded" shadow="never" class="section-card"><ChatModelsPane :can-share="canManageKnowledge" :can-create="canSubmit" @changed="refreshModels" /></el-card>
      </el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="全局检索与 GGUF" name="retrieval" lazy><SettingsView v-if="tab==='retrieval'" :retrieval-only="true" /></el-tab-pane>
      <el-tab-pane v-if="canManageKnowledge" label="安全与审计" name="security" lazy><SecurityView v-if="tab==='security'" /></el-tab-pane>
    </el-tabs>
  </div>
</template>
