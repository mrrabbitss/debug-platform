<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { CaseItem } from '../types'

const route = useRoute()
const cases = ref<CaseItem[]>([])
const selectedCaseId = ref('')
const repositories = ref<any[]>([])
const selectedRepositoryId = ref('')
const query = ref('')
const autoPlan = ref(true)
const selectedModules = ref<string[]>([
  'knowledge',
  'domain_graph',
  'memory',
  'code',
  'commit'
])
const topK = ref(12)
const maxHops = ref(2)
const loading = ref(false)
const result = ref<any | null>(null)
const memories = ref<any[]>([])
const memoryType = ref('')
const memorySearch = ref('')
const graphQuery = ref('')
const graphResult = ref<any | null>(null)
const graphLoading = ref(false)

const selectedCase = computed(() => cases.value.find(item => item.id === selectedCaseId.value))
const finalResults = computed(() => result.value?.results || [])
const explainablePaths = computed(() => result.value?.paths || [])
const plan = computed(() => result.value?.plan || null)

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}

function scoreOf(item: any) {
  const value = item.reranker_score ?? item.combined_score ?? item.source_score ?? 0
  return Number(value).toFixed(4)
}

function sourceLabel(value: string) {
  if (value === 'code_symbol') return '代码符号'
  if (value === 'commit') return 'Commit'
  if (value.startsWith('memory_')) return `记忆/${value.replace('memory_', '').toUpperCase()}`
  if (value === 'analysis_method') return '分析方法'
  if (value === 'fault_case') return '故障案例'
  return value
}

function pathTypeLabel(value: string) {
  if (value === 'code_graph') return '代码关系路径'
  if (value === 'query_commit_file') return 'Query → Commit → 文件'
  if (value === 'query_commit_file_code') return 'Query → Commit → 文件 → 符号'
  return value
}

function pathSummary(item: any) {
  if (item.path_type === 'code_graph') {
    const relations = (item.edges || [])
      .map((edge: any) => edge.relation_type)
      .filter(Boolean)
      .join(' → ')
    return `${item.from} → ${relations || '关系'} → ${item.to}`
  }
  const commit = item.commit_hash ? String(item.commit_hash).slice(0, 12) : item.commit_id
  if (item.path_type === 'query_commit_file_code') {
    return `当前 Query → ${commit} → ${item.file_path} → ${item.symbol_id}`
  }
  return `当前 Query → ${commit} → ${item.file_path}${item.change_type ? ` (${item.change_type})` : ''}`
}

async function loadCases() {
  cases.value = (await api.get('/cases')).data
  const requestedCaseId = String(route.query.case || '')
  if (
    !selectedCaseId.value
    && requestedCaseId
    && cases.value.some(item => item.id === requestedCaseId)
  ) {
    selectedCaseId.value = requestedCaseId
  } else if (!selectedCaseId.value && cases.value.length) {
    selectedCaseId.value = cases.value[0].id
  }
  await changeCase()
}

async function changeCase() {
  result.value = null
  graphResult.value = null
  selectedRepositoryId.value = ''
  if (!selectedCaseId.value) {
    repositories.value = []
    memories.value = []
    return
  }
  const [repositoryResponse] = await Promise.all([
    api.get(`/cases/${selectedCaseId.value}/repositories`),
    loadMemories()
  ])
  repositories.value = repositoryResponse.data
  selectedRepositoryId.value = repositories.value[0]?.id || ''
}

async function runSearch() {
  if (!selectedCaseId.value) return ElMessage.warning('请先选择故障案例')
  if (query.value.trim().length < 2) return ElMessage.warning('请输入至少两个字符的检索问题')
  if (!autoPlan.value && !selectedModules.value.length) {
    return ElMessage.warning('手动规划时请至少选择一个检索模块')
  }
  loading.value = true
  try {
    const payload: Record<string, any> = {
      query: query.value.trim(),
      top_k: topK.value,
      max_hops: maxHops.value
    }
    if (!autoPlan.value) payload.modules = selectedModules.value
    result.value = (await api.post(
      `/cases/${selectedCaseId.value}/agentic-search`,
      payload
    )).data
    await loadMemories()
    ElMessage.success(`检索完成，返回 ${result.value?.results?.length || 0} 条证据`)
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function loadMemories() {
  if (!selectedCaseId.value) return
  try {
    const params: Record<string, any> = { limit: 300 }
    if (memoryType.value) params.memory_type = memoryType.value
    if (memorySearch.value.trim()) params.search = memorySearch.value.trim()
    memories.value = (await api.get(
      `/cases/${selectedCaseId.value}/memories`,
      { params }
    )).data
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function loadGraph(kind: 'code' | 'commit') {
  if (!selectedRepositoryId.value) return ElMessage.warning('当前案例没有代码仓库')
  graphLoading.value = true
  try {
    if (kind === 'code') {
      if (graphQuery.value.trim()) {
        graphResult.value = (await api.get(
          `/repositories/${selectedRepositoryId.value}/graph/search`,
          {
            params: {
              query: graphQuery.value.trim(),
              max_hops: maxHops.value,
              limit: 30
            }
          }
        )).data
      } else {
        graphResult.value = (await api.get(
          `/repositories/${selectedRepositoryId.value}/graph`,
          { params: { limit: 300 } }
        )).data
      }
    } else {
      graphResult.value = (await api.get(
        `/repositories/${selectedRepositoryId.value}/commit-graph`,
        {
          params: {
            query: graphQuery.value.trim() || undefined,
            limit: 100
          }
        }
      )).data
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    graphLoading.value = false
  }
}

onMounted(async () => {
  try {
    await loadCases()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
})
</script>

<template>
  <div>
    <div class="toolbar">
      <div>
        <h1 class="page-title">认知检索</h1>
        <div class="muted">动态编排知识库、代码图谱、Commit 图谱和三类记忆</div>
      </div>
      <el-select
        v-model="selectedCaseId"
        placeholder="选择故障案例"
        filterable
        style="width:320px;margin-left:auto"
        @change="changeCase"
      >
        <el-option v-for="item in cases" :key="item.id" :label="item.title" :value="item.id" />
      </el-select>
    </div>

    <el-tabs>
      <el-tab-pane label="Agentic Search">
        <el-card>
          <el-form label-width="110px">
            <el-form-item label="当前案例">
              <span>{{ selectedCase?.title || '未选择' }}</span>
            </el-form-item>
            <el-form-item label="检索问题">
              <el-input
                v-model="query"
                type="textarea"
                :rows="4"
                placeholder="例如：这个 WLAN 认证回归可能由哪个 Commit 引入，相关调用链和以前的处理经验是什么？"
                @keyup.ctrl.enter="runSearch"
              />
            </el-form-item>
            <el-form-item label="自动规划">
              <el-switch v-model="autoPlan" />
              <span class="muted" style="margin-left:12px">根据代码、历史、回归、步骤等意图动态选择模块</span>
            </el-form-item>
            <el-form-item v-if="!autoPlan" label="检索模块">
              <el-checkbox-group v-model="selectedModules">
                <el-checkbox value="knowledge">知识库</el-checkbox>
                <el-checkbox value="domain_graph">领域图谱</el-checkbox>
                <el-checkbox value="code">代码图谱</el-checkbox>
                <el-checkbox value="commit">Commit 图谱</el-checkbox>
                <el-checkbox value="memory">记忆</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <div class="form-row">
              <el-form-item label="返回数量">
                <el-input-number v-model="topK" :min="1" :max="50" />
              </el-form-item>
              <el-form-item label="图谱跳数">
                <el-input-number v-model="maxHops" :min="0" :max="3" />
              </el-form-item>
            </div>
            <el-form-item>
              <el-button type="primary" :loading="loading" @click="runSearch">
                执行多跳检索
              </el-button>
              <span class="muted" style="margin-left:12px">Ctrl + Enter 快速执行</span>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card v-if="plan" style="margin-top:16px">
          <template #header><strong>动态执行计划</strong></template>
          <div class="tag-row">
            <el-tag v-for="module in plan.selected_modules" :key="module">{{ module }}</el-tag>
            <el-tag v-for="algorithm in plan.algorithms" :key="algorithm" type="info">{{ algorithm }}</el-tag>
          </div>
          <ul>
            <li v-for="item in plan.rationale" :key="item">{{ item }}</li>
          </ul>
          <el-table :data="result.trace" size="small">
            <el-table-column prop="stage" label="阶段" width="160" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column prop="candidate_count" label="候选数" width="100" />
            <el-table-column prop="duration_ms" label="耗时(ms)" width="110" />
            <el-table-column label="错误/说明" min-width="260" show-overflow-tooltip>
              <template #default="scope">{{ scope.row.error || scope.row.reason || '' }}</template>
            </el-table-column>
          </el-table>
        </el-card>

        <div v-loading="loading" class="result-list">
          <el-card v-for="item in finalResults" :key="`${item.source_type}:${item.evidence_id}`">
            <div class="result-title">
              <el-tag size="small">{{ sourceLabel(item.source_type) }}</el-tag>
              <strong>{{ item.title }}</strong>
              <span class="score">score {{ scoreOf(item) }}</span>
            </div>
            <pre class="result-content">{{ item.content }}</pre>
            <div class="tag-row">
              <el-tag v-for="module in item.modules" :key="module" size="small" type="info">
                {{ module }}
              </el-tag>
              <el-tag v-if="item.metadata?.file_path" size="small" type="success">
                {{ item.metadata.file_path }}:{{ item.metadata.line_start }}
              </el-tag>
              <el-tag v-if="item.metadata?.commit_hash" size="small" type="warning">
                {{ item.metadata.commit_hash.slice(0, 12) }}
              </el-tag>
              <el-tag v-if="item.paths?.length" size="small" type="danger">
                {{ item.paths.length }} 条可解释路径
              </el-tag>
            </div>
          </el-card>
          <el-empty v-if="result && !finalResults.length" description="没有检索到证据" />
        </div>

        <el-card v-if="explainablePaths.length" style="margin-top:16px">
          <template #header><strong>可解释多跳路径</strong></template>
          <el-table :data="explainablePaths" max-height="420" stripe>
            <el-table-column label="路径类型" width="210">
              <template #default="scope">{{ pathTypeLabel(scope.row.path_type) }}</template>
            </el-table-column>
            <el-table-column label="完整路径" min-width="520" show-overflow-tooltip>
              <template #default="scope">{{ pathSummary(scope.row) }}</template>
            </el-table-column>
            <el-table-column prop="repository_id" label="代码仓" width="180" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="代码与 Commit 图谱">
        <el-card>
          <div class="toolbar">
            <el-select v-model="selectedRepositoryId" placeholder="选择代码仓" style="width:300px">
              <el-option
                v-for="item in repositories"
                :key="item.id"
                :label="`${item.name} / ${item.graph_status} / ${item.commit_graph_status}`"
                :value="item.id"
              />
            </el-select>
            <el-input
              v-model="graphQuery"
              clearable
              placeholder="函数、文件、错误或 Commit 意图"
              style="width:360px"
            />
            <el-button type="primary" :loading="graphLoading" @click="loadGraph('code')">
              查询代码图谱
            </el-button>
            <el-button :loading="graphLoading" @click="loadGraph('commit')">
              查询 Commit 图谱
            </el-button>
          </div>
          <el-alert
            v-if="selectedRepositoryId && repositories.find(item => item.id === selectedRepositoryId)?.commit_graph_status === 'UNAVAILABLE'"
            type="warning"
            :closable="false"
            title="该归档没有 Git 历史。请上传 git bundle create repo.bundle --all 生成的 Bundle。"
            style="margin:12px 0"
          />
          <template v-if="graphResult">
            <div class="tag-row" style="margin:12px 0">
              <el-tag v-for="(value,key) in graphResult.stats" :key="key" type="info">
                {{ key }}: {{ typeof value === 'object' ? JSON.stringify(value) : value }}
              </el-tag>
            </div>
            <el-table :data="graphResult.nodes || []" max-height="420" stripe>
              <el-table-column prop="node_type" label="节点类型" width="110" />
              <el-table-column prop="kind" label="符号类型" width="110" />
              <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
              <el-table-column prop="subject" label="Commit 意图" min-width="220" show-overflow-tooltip />
              <el-table-column prop="file_path" label="文件" min-width="260" show-overflow-tooltip />
              <el-table-column prop="hop" label="跳数" width="70" />
            </el-table>
            <el-table :data="graphResult.edges || []" max-height="360" stripe style="margin-top:16px">
              <el-table-column prop="relation_type" label="代码关系" width="130" />
              <el-table-column prop="edge_type" label="Commit 关系" width="130" />
              <el-table-column prop="source" label="起点" min-width="190" show-overflow-tooltip />
              <el-table-column prop="target" label="终点" min-width="190" show-overflow-tooltip />
              <el-table-column prop="target_name" label="目标名称" min-width="160" />
              <el-table-column prop="confidence" label="置信度" width="90" />
            </el-table>
          </template>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="任务记忆">
        <el-card>
          <div class="toolbar">
            <el-select v-model="memoryType" clearable placeholder="全部记忆类型" style="width:180px" @change="loadMemories">
              <el-option label="情景记忆" value="EPISODIC" />
              <el-option label="程序记忆" value="PROCEDURAL" />
              <el-option label="失败记忆" value="FAILURE" />
            </el-select>
            <el-input
              v-model="memorySearch"
              clearable
              placeholder="搜索经验、步骤或失败原因"
              style="width:320px"
              @keyup.enter="loadMemories"
              @clear="loadMemories"
            />
            <el-button @click="loadMemories">搜索记忆</el-button>
          </div>
          <el-table :data="memories" stripe>
            <el-table-column prop="memory_type" label="类型" width="120" />
            <el-table-column prop="title" label="标题" min-width="230" show-overflow-tooltip />
            <el-table-column prop="outcome" label="结果" width="100" />
            <el-table-column prop="confidence" label="置信度" width="90" />
            <el-table-column prop="occurrence_count" label="出现" width="70" />
            <el-table-column prop="reuse_count" label="复用" width="70" />
            <el-table-column prop="content" label="内容" min-width="360" show-overflow-tooltip />
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.form-row { display: grid; grid-template-columns: 260px 260px; gap: 20px; }
.tag-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.result-list { display: grid; gap: 14px; margin-top: 16px; min-height: 80px; }
.result-title { display: flex; align-items: center; gap: 10px; }
.score { margin-left: auto; color: #64748b; font-variant-numeric: tabular-nums; }
.result-content {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 260px;
  overflow: auto;
  padding: 12px;
  border-radius: 6px;
  background: #f8fafc;
  color: #334155;
  font-family: inherit;
  line-height: 1.55;
}
@media (max-width: 900px) {
  .form-row { grid-template-columns: 1fr; }
}
</style>
