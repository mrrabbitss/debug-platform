<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type {
  Analysis,
  CaseItem,
  DomainGraphStatus,
  EvaluationCase,
  EvaluationDataset,
  EvaluationRun,
  Job
} from '../types'

const activeTab = ref('graph')
const loading = ref(false)
const graphStatus = ref<DomainGraphStatus>({
  status: 'NOT_BUILT',
  entities: 0,
  relations: 0,
  metadata: {}
})
const graphQuery = ref('')
const graphResult = ref<any>(null)

const datasets = ref<EvaluationDataset[]>([])
const selectedDatasetId = ref('')
const evaluationCases = ref<EvaluationCase[]>([])
const evaluationRuns = ref<EvaluationRun[]>([])
const evaluationCaseDialog = ref(false)
const evaluationCaseForm = reactive({
  case_id: '',
  query: '',
  expected_evidence_ids: '',
  expected_root_causes: '',
  modules: ['knowledge', 'domain_graph'] as string[],
  top_k: 10,
  max_hops: 2
})

const cases = ref<CaseItem[]>([])
const selectedFeedbackCaseId = ref('')
const analyses = ref<Analysis[]>([])
const feedbackRows = ref<any[]>([])
const feedbackForm = reactive({
  analysis_run_id: '',
  verdict: 'PARTIAL',
  root_cause_correct: undefined as boolean | undefined,
  evidence_correct: undefined as boolean | undefined,
  comment: '',
  root_cause: '',
  solution: '',
  evidence: ''
})

const selectedDataset = computed(() =>
  datasets.value.find(item => item.id === selectedDatasetId.value)
)
const latestEvaluationRun = computed(() => evaluationRuns.value[0])

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || '操作失败'
}

async function waitForJob(initialJob: Job): Promise<Job> {
  let job = initialJob
  while (!['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(job.status)) {
    await new Promise(resolve => window.setTimeout(resolve, 1000))
    job = (await api.get(`/jobs/${job.id}`)).data
  }
  if (job.status !== 'COMPLETED') {
    throw new Error(job.error_message || `后台任务${job.status}`)
  }
  return job
}

async function loadGraphStatus() {
  graphStatus.value = (await api.get('/knowledge/graph/status')).data
}

async function rebuildGraph() {
  loading.value = true
  try {
    const job: Job = (await api.post('/knowledge/graph/rebuild')).data
    ElMessage.info('领域知识图谱正在后台旁路构建')
    await waitForJob(job)
    await loadGraphStatus()
    ElMessage.success('领域知识图谱已原子切换到新版本')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function searchGraph() {
  if (!graphQuery.value.trim()) return ElMessage.warning('请输入检索内容')
  loading.value = true
  try {
    graphResult.value = (
      await api.post('/knowledge/graph/search', {
        query: graphQuery.value,
        top_k: 20,
        max_hops: 2
      })
    ).data
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function loadDatasets() {
  datasets.value = (await api.get('/evaluation/datasets')).data
  if (!selectedDatasetId.value && datasets.value.length) {
    selectedDatasetId.value = datasets.value[0].id
  }
  await loadSelectedDataset()
}

async function loadSelectedDataset() {
  if (!selectedDatasetId.value) {
    evaluationCases.value = []
    evaluationRuns.value = []
    return
  }
  const [caseResponse, runResponse] = await Promise.all([
    api.get(`/evaluation/datasets/${selectedDatasetId.value}/cases`),
    api.get(`/evaluation/datasets/${selectedDatasetId.value}/runs`)
  ])
  evaluationCases.value = caseResponse.data
  evaluationRuns.value = runResponse.data
}

async function createDataset() {
  try {
    const { value } = await ElMessageBox.prompt(
      '输入评测集名称。评测集用于固定 query、预期证据和预期根因。',
      '新建检索评测集',
      { inputPattern: /\S{2,}/, inputErrorMessage: '名称至少 2 个字符' }
    )
    const created = (
      await api.post('/evaluation/datasets', { name: value, description: '' })
    ).data
    selectedDatasetId.value = created.id
    await loadDatasets()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

function openEvaluationCase() {
  evaluationCaseForm.case_id = cases.value[0]?.id || ''
  evaluationCaseForm.query = ''
  evaluationCaseForm.expected_evidence_ids = ''
  evaluationCaseForm.expected_root_causes = ''
  evaluationCaseForm.modules = ['knowledge', 'domain_graph']
  evaluationCaseForm.top_k = 10
  evaluationCaseForm.max_hops = 2
  evaluationCaseDialog.value = true
}

function lines(value: string) {
  return value
    .split(/\r?\n|,/)
    .map(item => item.trim())
    .filter(Boolean)
}

async function saveEvaluationCase() {
  if (!selectedDatasetId.value) return
  if (!evaluationCaseForm.case_id || !evaluationCaseForm.query.trim()) {
    return ElMessage.warning('请选择案例并填写 query')
  }
  if (!evaluationCaseForm.modules.length) {
    return ElMessage.warning('至少选择一个检索模块')
  }
  loading.value = true
  try {
    await api.post(
      `/evaluation/datasets/${selectedDatasetId.value}/cases`,
      {
        case_id: evaluationCaseForm.case_id,
        query: evaluationCaseForm.query,
        expected_evidence_ids: lines(
          evaluationCaseForm.expected_evidence_ids
        ),
        expected_root_causes: lines(
          evaluationCaseForm.expected_root_causes
        ),
        modules: evaluationCaseForm.modules,
        top_k: evaluationCaseForm.top_k,
        max_hops: evaluationCaseForm.max_hops
      }
    )
    evaluationCaseDialog.value = false
    await loadDatasets()
    ElMessage.success('评测用例已保存')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function removeEvaluationCase(item: EvaluationCase) {
  try {
    await ElMessageBox.confirm('确认删除该评测用例？', '删除评测用例', {
      type: 'warning'
    })
    await api.delete(
      `/evaluation/datasets/${item.dataset_id}/cases/${item.id}`
    )
    await loadDatasets()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorText(error))
  }
}

async function runEvaluation() {
  if (!selectedDatasetId.value) return
  loading.value = true
  try {
    const response = (
      await api.post(
        `/evaluation/datasets/${selectedDatasetId.value}/runs`
      )
    ).data
    ElMessage.info('评测正在后台运行；本次运行不会写入或强化记忆')
    await waitForJob(response.job)
    await loadSelectedDataset()
    ElMessage.success('检索评测完成')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function loadFeedbackCase() {
  if (!selectedFeedbackCaseId.value) return
  const [analysisResponse, feedbackResponse] = await Promise.all([
    api.get(`/cases/${selectedFeedbackCaseId.value}/analyses`),
    api.get(`/cases/${selectedFeedbackCaseId.value}/diagnosis-feedback`)
  ])
  analyses.value = analysisResponse.data
  feedbackRows.value = feedbackResponse.data
  feedbackForm.analysis_run_id =
    analyses.value.find(item => item.status === 'COMPLETED')?.id || ''
}

async function submitFeedback() {
  if (!selectedFeedbackCaseId.value || !feedbackForm.analysis_run_id) {
    return ElMessage.warning('请选择案例和已完成的诊断运行')
  }
  loading.value = true
  try {
    await api.post(
      `/cases/${selectedFeedbackCaseId.value}/diagnosis-feedback`,
      {
        analysis_run_id: feedbackForm.analysis_run_id,
        verdict: feedbackForm.verdict,
        root_cause_correct: feedbackForm.root_cause_correct,
        evidence_correct: feedbackForm.evidence_correct,
        comment: feedbackForm.comment,
        corrections: {
          root_cause: feedbackForm.root_cause,
          solution: feedbackForm.solution,
          evidence: feedbackForm.evidence
        }
      }
    )
    feedbackForm.comment = ''
    feedbackForm.root_cause = ''
    feedbackForm.solution = ''
    feedbackForm.evidence = ''
    await loadFeedbackCase()
    ElMessage.success('反馈已提交，审核前不会进入知识库或记忆')
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

async function reviewFeedback(item: any, action: 'APPROVE' | 'REJECT') {
  try {
    await api.post(
      `/cases/${item.case_id}/diagnosis-feedback/${item.id}/review`,
      { action }
    )
    await loadFeedbackCase()
    ElMessage.success(action === 'APPROVE' ? '反馈已审核通过' : '反馈已驳回')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function incorporateFeedback(item: any) {
  try {
    await api.post(
      `/cases/${item.case_id}/diagnosis-feedback/${item.id}/incorporate`
    )
    await loadFeedbackCase()
    ElMessage.success('已生成知识草稿，仍需在知识库中审核发布')
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function load() {
  loading.value = true
  try {
    cases.value = (await api.get('/cases')).data
    selectedFeedbackCaseId.value = cases.value[0]?.id || ''
    await Promise.all([
      loadGraphStatus(),
      loadDatasets().catch(() => undefined),
      loadFeedbackCase()
    ])
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">质量与知识治理</h1>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="领域图谱 / GraphRAG" name="graph">
        <el-card>
          <div class="metric-grid">
            <div class="metric-box"><span>图谱状态</span><strong>{{ graphStatus.status }}</strong></div>
            <el-statistic title="实体" :value="graphStatus.entities" />
            <el-statistic title="关系" :value="graphStatus.relations" />
            <div class="metric-box"><span>活动 Generation</span><strong class="mono">{{ graphStatus.active_generation_id || '—' }}</strong></div>
          </div>
          <el-alert
            v-if="graphStatus.status === 'STALE'"
            title="知识已变更，当前图谱仍可查询，但应重建后再用于正式评测。"
            type="warning"
            :closable="false"
            style="margin:16px 0"
          />
          <div class="toolbar" style="margin-top:16px">
            <el-button type="primary" @click="rebuildGraph">原子重建图谱</el-button>
            <el-input
              v-model="graphQuery"
              placeholder="输入症状、错误码、模块或解决方案"
              style="max-width:520px;margin-left:auto"
              @keyup.enter="searchGraph"
            />
            <el-button @click="searchGraph">GraphRAG 检索</el-button>
          </div>
        </el-card>

        <div v-if="graphResult" class="graph-results">
          <el-card>
            <template #header>命中证据</template>
            <el-table :data="graphResult.documents" stripe>
              <el-table-column prop="title" label="标题" min-width="240" />
              <el-table-column prop="source_type" label="类型" width="130" />
              <el-table-column label="证据来源" min-width="180"><template #default="scope">{{ scope.row.metadata?.source_file || scope.row.metadata?.file_path || scope.row.title || '知识库文档' }}</template></el-table-column>
              <el-table-column prop="source_score" label="图谱分数" width="110" />
              <el-table-column label="路径数" width="90"><template #default="scope">{{ scope.row.paths?.length || 0 }}</template></el-table-column>
            </el-table>
          </el-card>
          <el-card>
            <template #header>实体与关系</template>
            <div class="split-grid">
              <el-table :data="graphResult.nodes" max-height="360">
                <el-table-column prop="entity_type" label="实体类型" width="140" />
                <el-table-column prop="name" label="名称" min-width="220" show-overflow-tooltip />
                <el-table-column prop="score" label="分数" width="90" />
              </el-table>
              <el-table :data="graphResult.edges" max-height="360">
                <el-table-column prop="relation_type" label="关系" width="180" />
                <el-table-column prop="source" label="起点" min-width="150" show-overflow-tooltip />
                <el-table-column prop="target" label="终点" min-width="150" show-overflow-tooltip />
              </el-table>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="检索评测" name="evaluation">
        <el-alert
          title="评测运行固定模型配置快照，并显式关闭记忆写入。未填写预期证据或根因的指标会显示为空，不会被当作 0 分。"
          type="info"
          :closable="false"
          style="margin-bottom:16px"
        />
        <el-card>
          <div class="toolbar">
            <el-select
              v-model="selectedDatasetId"
              placeholder="选择评测集"
              style="width:320px"
              @change="loadSelectedDataset"
            >
              <el-option
                v-for="item in datasets"
                :key="item.id"
                :label="`${item.name}（${item.case_count}）`"
                :value="item.id"
              />
            </el-select>
            <el-button @click="createDataset">新建评测集</el-button>
            <el-button
              :disabled="!selectedDataset"
              @click="openEvaluationCase"
            >
              添加用例
            </el-button>
            <el-button
              type="primary"
              :disabled="!evaluationCases.length"
              @click="runEvaluation"
            >
              运行评测
            </el-button>
          </div>
          <el-table :data="evaluationCases" stripe style="margin-top:16px">
            <el-table-column prop="query" label="Query" min-width="260" />
            <el-table-column prop="case_id" label="案例 ID" min-width="170" />
            <el-table-column label="预期证据" width="100"><template #default="scope">{{ scope.row.expected_evidence_ids.length }}</template></el-table-column>
            <el-table-column label="模块" min-width="200"><template #default="scope">{{ scope.row.modules.join(' / ') }}</template></el-table-column>
            <el-table-column prop="top_k" label="Top-K" width="80" />
            <el-table-column label="操作" width="90"><template #default="scope"><el-button link type="danger" @click="removeEvaluationCase(scope.row as EvaluationCase)">删除</el-button></template></el-table-column>
          </el-table>
        </el-card>

        <el-card v-if="latestEvaluationRun" style="margin-top:16px">
          <template #header>最近一次评测：{{ latestEvaluationRun.status }}</template>
          <div class="metric-grid">
            <div class="metric-box"><span>Recall@K</span><strong>{{ latestEvaluationRun.metrics.recall_at_k ?? '—' }}</strong></div>
            <div class="metric-box"><span>Precision@K</span><strong>{{ latestEvaluationRun.metrics.precision_at_k ?? '—' }}</strong></div>
            <div class="metric-box"><span>MRR</span><strong>{{ latestEvaluationRun.metrics.mrr ?? '—' }}</strong></div>
            <div class="metric-box"><span>NDCG@K</span><strong>{{ latestEvaluationRun.metrics.ndcg_at_k ?? '—' }}</strong></div>
            <div class="metric-box"><span>Root Cause Top-K</span><strong>{{ latestEvaluationRun.metrics.root_cause_top_k ?? '—' }}</strong></div>
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane label="人工诊断反馈" name="feedback">
        <el-alert
          title="提交反馈只会进入待审核区；管理员审核通过后也不会自动学习，只有“生成知识草稿”才会建立待二次审核的文档。"
          type="warning"
          :closable="false"
          style="margin-bottom:16px"
        />
        <el-card>
          <el-form label-width="120px">
            <el-form-item label="案例">
              <el-select
                v-model="selectedFeedbackCaseId"
                filterable
                style="width:100%"
                @change="loadFeedbackCase"
              >
                <el-option v-for="item in cases" :key="item.id" :label="item.title" :value="item.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="诊断运行">
              <el-select v-model="feedbackForm.analysis_run_id" style="width:100%">
                <el-option
                  v-for="item in analyses"
                  :key="item.id"
                  :disabled="item.status !== 'COMPLETED'"
                  :label="`${item.id} / ${item.model} / ${item.status}`"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <div class="form-grid">
              <el-form-item label="总体结论">
                <el-select v-model="feedbackForm.verdict">
                  <el-option label="正确" value="CORRECT" />
                  <el-option label="部分正确" value="PARTIAL" />
                  <el-option label="错误" value="INCORRECT" />
                </el-select>
              </el-form-item>
              <el-form-item label="根因正确">
                <el-select v-model="feedbackForm.root_cause_correct" clearable>
                  <el-option label="是" :value="true" />
                  <el-option label="否" :value="false" />
                </el-select>
              </el-form-item>
              <el-form-item label="证据正确">
                <el-select v-model="feedbackForm.evidence_correct" clearable>
                  <el-option label="是" :value="true" />
                  <el-option label="否" :value="false" />
                </el-select>
              </el-form-item>
            </div>
            <el-form-item label="反馈说明"><el-input v-model="feedbackForm.comment" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="确认根因"><el-input v-model="feedbackForm.root_cause" type="textarea" :rows="2" /></el-form-item>
            <el-form-item label="确认方案"><el-input v-model="feedbackForm.solution" type="textarea" :rows="2" /></el-form-item>
            <el-form-item label="确认依据"><el-input v-model="feedbackForm.evidence" type="textarea" :rows="2" /></el-form-item>
            <el-form-item><el-button type="primary" @click="submitFeedback">提交待审核反馈</el-button></el-form-item>
          </el-form>
        </el-card>

        <el-card style="margin-top:16px">
          <template #header>反馈审核队列</template>
          <el-table :data="feedbackRows" stripe>
            <el-table-column prop="verdict" label="结论" width="110" />
            <el-table-column prop="comment" label="反馈" min-width="260" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="130" />
            <el-table-column prop="submitted_by" label="提交人" width="130" />
            <el-table-column label="操作" width="260">
              <template #default="scope">
                <el-button v-if="scope.row.status === 'SUBMITTED'" link type="success" @click="reviewFeedback(scope.row, 'APPROVE')">通过</el-button>
                <el-button v-if="scope.row.status === 'SUBMITTED'" link type="danger" @click="reviewFeedback(scope.row, 'REJECT')">驳回</el-button>
                <el-button v-if="scope.row.status === 'APPROVED'" link type="primary" @click="incorporateFeedback(scope.row)">生成知识草稿</el-button>
                <span v-if="scope.row.incorporated_document_id" class="mono">{{ scope.row.incorporated_document_id }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="evaluationCaseDialog" title="添加检索评测用例" width="700px">
      <el-form label-width="130px">
        <el-form-item label="案例">
          <el-select v-model="evaluationCaseForm.case_id" filterable style="width:100%">
            <el-option v-for="item in cases" :key="item.id" :label="item.title" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="Query"><el-input v-model="evaluationCaseForm.query" type="textarea" :rows="3" /></el-form-item>
        <el-collapse style="margin-bottom:12px">
          <el-collapse-item title="高级：证据级评测约束" name="expected-evidence">
            <el-form-item label="内部定位键">
              <el-input
                v-model="evaluationCaseForm.expected_evidence_ids"
                type="textarea"
                :rows="3"
                placeholder="仅供评测维护者录入稳定定位键；操作界面和评测结果不会展示这些内部编号"
              />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
        <el-form-item label="预期根因"><el-input v-model="evaluationCaseForm.expected_root_causes" type="textarea" :rows="3" placeholder="每行一个可接受根因短语" /></el-form-item>
        <el-form-item label="检索模块">
          <el-checkbox-group v-model="evaluationCaseForm.modules">
            <el-checkbox value="knowledge">知识</el-checkbox>
            <el-checkbox value="domain_graph">领域图谱</el-checkbox>
            <el-checkbox value="code">代码图谱</el-checkbox>
            <el-checkbox value="commit">Commit</el-checkbox>
            <el-checkbox value="memory">记忆</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="Top-K"><el-input-number v-model="evaluationCaseForm.top_k" :min="1" :max="50" /></el-form-item>
          <el-form-item label="最大跳数"><el-input-number v-model="evaluationCaseForm.max_hops" :min="0" :max="3" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="evaluationCaseDialog=false">取消</el-button>
        <el-button type="primary" @click="saveEvaluationCase">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; }
.metric-box { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid #ebeef5; border-radius: 6px; }
.metric-box span { color: #909399; font-size: 13px; }
.metric-box strong { font-size: 20px; overflow-wrap: anywhere; }
.graph-results { display: grid; gap: 16px; margin-top: 16px; }
.split-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
@media (max-width: 1000px) {
  .split-grid, .form-grid { grid-template-columns: 1fr; }
}
</style>
