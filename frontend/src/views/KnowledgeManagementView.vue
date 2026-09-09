<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import { useWorkbench, failure } from '../composables/useWorkbench'
import SkillManagementPane from '../components/knowledge/SkillManagementPane.vue'
import KnowledgeAssistantPane from '../components/knowledge/KnowledgeAssistantPane.vue'
import KnowledgeContributionsPane from '../components/knowledge/KnowledgeContributionsPane.vue'
import KnowledgeResetPane from '../components/knowledge/KnowledgeResetPane.vue'
import KnowledgeView from './KnowledgeView.vue'
import type { Job } from '../types'
import type { LibraryEntry } from '../types/workbench'
const { config, canManageKnowledge, loadConfig } = useWorkbench()
let bundleTimer: ReturnType<typeof setTimeout> | undefined
let bundleDisposed = false
function pollBundle() {
  if (bundleDisposed) return
  if (bundleTimer) clearTimeout(bundleTimer)
  bundleTimer = setTimeout(async () => {
    await refreshConfig()
    if (['PENDING','APPROVED','BUILDING'].includes(config.value.bundled_knowledge?.status || '')) pollBundle()
  }, 5000)
}
onUnmounted(() => { bundleDisposed = true; if (bundleTimer) clearTimeout(bundleTimer) })
const route = useRoute(), router = useRouter(), tab = ref('skills'), ready = ref(false), error = ref(''), indexBusy = ref(false), indexJob = ref<Job | null>(null)
const legacyCases = ref<LibraryEntry[]>([]), bridging = ref(''), reviews = ref<InstanceType<typeof KnowledgeContributionsPane> | null>(null)
function syncTab() { const value = String(route.query.tab || 'skills'); tab.value = ['skills','assistant','review','maintenance'].includes(value) ? value : 'skills' }
watch(() => route.query.tab, syncTab)
async function refreshConfig() { try { await loadConfig() } catch(cause) { error.value = failure(cause) } }
async function loadLegacyCases() { try { legacyCases.value = (await api.get<LibraryEntry[]>('/workbench/library')).data.filter(item => item.status === 'PENDING') } catch(cause) { error.value = failure(cause) } }
async function reviewCase(item: LibraryEntry) {
  if (bridging.value) return
  bridging.value = item.id; error.value = ''
  try { await api.post(`/knowledge-contributions/from-library/${item.id}`); await reviews.value?.reload() }
  catch(cause) { error.value = failure(cause) } finally { bridging.value = '' }
}
async function reindex() {
  if (indexBusy.value) return
  indexBusy.value = true; error.value = ''
  try { indexJob.value = (await api.post<Job>('/knowledge/reindex')).data }
  catch(cause) { error.value = failure(cause) } finally { indexBusy.value = false }
}
async function refreshIndex() { if (indexJob.value) try { indexJob.value = (await api.get<Job>(`/jobs/${indexJob.value.id}`)).data } catch(cause) { error.value = failure(cause) } }
watch(tab, value => { if (value === 'review' && canManageKnowledge.value) void loadLegacyCases() })
onMounted(async () => { try { await loadConfig(); ready.value = true; syncTab(); if (['PENDING','APPROVED','BUILDING'].includes(config.value.bundled_knowledge?.status || '')) pollBundle(); if (tab.value==='review') await loadLegacyCases() } catch(cause) { error.value = failure(cause) } })
</script>
<template>
  <div class="workspace-page">
    <div class="workspace-heading"><div><span class="eyebrow">KNOWLEDGE MANAGEMENT</span><h1>知识库管理</h1><p class="muted">管理必读 Skill、整理资料并审核团队贡献。发布版本完整保留，可追溯到原稿。</p></div><el-tag effect="plain">专家与管理员</el-tag></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button link @click="refreshConfig">重新加载</el-button></el-alert>
    <template v-if="ready && canManageKnowledge">
      <el-alert v-if="config.bundled_knowledge && config.bundled_knowledge.status !== 'NOT_BUNDLED'" :title="config.bundled_knowledge.message" :type="config.bundled_knowledge.status === 'PUBLISHED' ? 'success' : config.bundled_knowledge.status === 'FAILED' ? 'warning' : 'info'" :closable="false" class="inline-alert">
        <span v-if="config.bundled_knowledge.status !== 'PUBLISHED'" class="field-hint">随包文件夹：{{ config.bundled_knowledge.folder }}<template v-if="config.bundled_knowledge.operation_id"> · 操作：{{ config.bundled_knowledge.operation_id }}</template></span>
        <el-button link @click="refreshConfig">刷新状态</el-button>
      </el-alert>
      <el-tabs v-model="tab" @tab-change="name=>router.replace({path:'/knowledge-management',query:{tab:String(name)}})"><el-tab-pane label="Skill 与故障类别" name="skills" /><el-tab-pane label="AI 整理助手" name="assistant" /><el-tab-pane label="审批中心" name="review" /><el-tab-pane label="知识维护" name="maintenance" /></el-tabs>
      <SkillManagementPane v-if="tab==='skills'" :key="config.bundled_knowledge?.status" :categories="config.categories" :roles="config.knowledge_roles" :owner-id="config.principal.id || ''" @changed="refreshConfig" @organize="tab='assistant'" @maintain="tab='maintenance'" />
      <KnowledgeAssistantPane v-else-if="tab==='assistant'" :categories="config.categories" :roles="config.knowledge_roles" @published="refreshConfig" />
      <section v-else-if="tab==='review'"><KnowledgeContributionsPane ref="reviews" :owner-id="config.principal.id || ''" :categories="config.categories" :review="true" :initial-id="String(route.query.contribution || '')" @changed="loadLegacyCases" /><el-collapse v-if="legacyCases.length" class="section-card"><el-collapse-item :title="`案例与报告待审记录（${legacyCases.length}）`" name="legacy"><p class="field-hint">已进入审批队列的记录可继续从上方打开；旧版本的待审案例可以在此接入同一多轮审核流程。</p><div v-for="item in legacyCases" :key="item.id" class="collection-heading"><span>{{ item.title }}</span><el-button :loading="bridging===item.id" :disabled="!!bridging" @click="reviewCase(item)">纳入审批队列</el-button></div></el-collapse-item></el-collapse></section>
      <section v-else>
        <el-collapse><el-collapse-item title="检索索引与高级检查" name="tools"><div class="toolbar"><el-button :loading="indexBusy" @click="reindex">重建知识索引</el-button><router-link to="/admin/cognitive-search">检索检查</router-link><router-link to="/admin/quality-governance">质量与治理</router-link><router-link to="/admin/agent-runs">运行轨迹</router-link></div><p class="field-hint">新索引准备完成后切换版本；Embedding、Reranker 与 GGUF 全局模型配置由管理员在系统设置中维护。</p><el-alert v-if="indexJob" :title="`${indexJob.status} · ${indexJob.message || '重建任务已保存'}`" :type="indexJob.status==='FAILED'?'error':'info'" :closable="false"><el-button link @click="refreshIndex">刷新进度</el-button></el-alert></el-collapse-item></el-collapse>
        <KnowledgeView />
        <el-collapse class="section-card"><el-collapse-item title="高级维护：一次性知识重置与组网包导入" name="reset"><KnowledgeResetPane :roles="config.knowledge_roles" /></el-collapse-item></el-collapse>
      </section>
    </template>
  </div>
</template>
