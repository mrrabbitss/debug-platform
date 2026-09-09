<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { useWorkbench, failure } from '../composables/useWorkbench'
import SettingsView from './SettingsView.vue'
import SecurityView from './SecurityView.vue'
import { useRoute, useRouter } from 'vue-router'
const { config, isAdmin, configLoading, loadConfig } = useWorkbench()
const route = useRoute(), router = useRouter()
const tab = ref('personal'), error = ref(''), saving = ref(false), loaded = ref(false)
const selectedModel = ref<string | null>(null)
function syncTab() { const requested = String(route.query.tab || 'personal'); tab.value = isAdmin.value && ['models','security','advanced'].includes(requested) ? requested : 'personal' }
watch(() => route.query.tab, syncTab)
async function load() {
  error.value = ''
  try { await loadConfig(); selectedModel.value = config.value.preferences.chat_profile_id || null; loaded.value = true; syncTab() }
  catch(cause) { error.value = failure(cause) }
}
async function save() {
  if (saving.value || !loaded.value) return
  saving.value = true; error.value = ''
  try { await api.put('/workbench/preferences', { chat_profile_id: selectedModel.value || null }); ElMessage.success('已保存，将用于你的新案例') }
  catch(cause) { error.value = failure(cause) }
  finally { saving.value = false }
}
onMounted(load)
</script>
<template>
  <div class="workspace-page"><div class="workspace-heading"><div><span class="eyebrow">PREFERENCES</span><h1>系统设置</h1><p class="muted">选择适合自己的诊断模型，管理员在此维护平台。</p></div></div>
    <el-alert v-if="route.query.notice" :title="route.query.notice==='admin-required' ? '此管理页面仅限管理员访问，已返回个人设置。' : '暂时无法验证管理权限，请检查连接后重试。'" type="warning" :closable="false" class="inline-alert" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link @click="load">重新加载</el-button></el-alert>
    <el-tabs v-model="tab" @tab-change="name => router.replace({path:'/settings',query:{tab:String(name)}})">
      <el-tab-pane label="个人设置" name="personal"><el-card shadow="never" class="settings-card" v-loading="configLoading">
        <h2>个人诊断模型</h2><p class="muted">从管理员预设模型中选择，创建案例时仍可调整。</p>
        <div class="toolbar"><el-select v-model="selectedModel" aria-label="个人诊断模型" clearable placeholder="跟随系统默认" style="width:320px"><el-option v-for="model in config.models" :key="model.id" :label="model.name + (model.active ? ' · 系统默认' : '')" :value="model.id" /></el-select><el-button type="primary" :disabled="!loaded" :loading="saving" @click="save">保存偏好</el-button></div>
        <p v-if="loaded&&!config.models.length" class="field-hint">暂无已启用的 Chat 模型，请联系管理员预设模型。</p>
        <p v-if="selectedModel&&!config.models.some(item=>item.id===selectedModel)" class="field-hint">原模型已停用，请重新选择或清空以跟随系统默认。</p>
        <el-divider /><dl class="profile-details"><dt>当前用户</dt><dd>{{ config.principal.username || config.principal.display_name || '—' }}</dd><dt>身份</dt><dd>{{ isAdmin ? '管理员' : config.principal.role==='VIEWER' ? '只读用户' : '工程师' }}</dd></dl>
      </el-card></el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="模型与索引" name="models" lazy><SettingsView v-if="tab==='models'" /></el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="安全与审计" name="security" lazy><SecurityView v-if="tab==='security'" /></el-tab-pane>
      <el-tab-pane v-if="isAdmin" label="管理员入口" name="advanced" lazy><div class="admin-tools">
        <router-link to="/admin/knowledge" class="admin-tool"><h3>知识管理</h3><p>文档审核、版本恢复与发布管理</p></router-link>
        <router-link to="/admin/curation" class="admin-tool"><h3>AI 案例提炼</h3><p>导入资料、整理证据与提炼经验</p></router-link>
        <router-link to="/admin/agent-runs" class="admin-tool"><h3>运行轨迹</h3><p>检查诊断任务与执行记录</p></router-link>
        <router-link to="/admin/quality-governance" class="admin-tool"><h3>质量与治理</h3><p>知识质量和记忆审核</p></router-link>
        <router-link to="/admin/cognitive-search" class="admin-tool"><h3>检索检查</h3><p>查看知识与代码检索结果</p></router-link>
      </div></el-tab-pane>
    </el-tabs>
  </div>
</template>
