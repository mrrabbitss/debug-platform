<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import type {
  Artifact,
  LogEvidenceBucket,
  LogEvidenceItem,
  LogEvidencePage,
  LogTriageRun
} from '../../types'
import PlanningTracePanel from './PlanningTracePanel.vue'

const props = defineProps<{
  caseId: string
  artifacts: Artifact[]
  canEdit: boolean
  modelEgressApproved: boolean
}>()

const emit = defineEmits<{
  openSource: [payload: { artifactId: string, sourceFile: string, line: number }]
}>()

const selectedArtifactId = ref('')
const triage = ref<LogTriageRun | null>(null)
const loading = ref(false)
const submitting = ref(false)
const activeBucket = ref<LogEvidenceBucket>('LLM_RELEVANT')
const pages = reactive<Record<LogEvidenceBucket, LogEvidencePage>>({
  LLM_RELEVANT: { triage_run_id: '', bucket: 'LLM_RELEVANT', total: 0, offset: 0, limit: 100, items: [] },
  METHOD_REQUIRED: { triage_run_id: '', bucket: 'METHOD_REQUIRED', total: 0, offset: 0, limit: 100, items: [] },
  OTHER: { triage_run_id: '', bucket: 'OTHER', total: 0, offset: 0, limit: 100, items: [] }
})
let timer: number | null = null

const parsedArtifacts = computed(() => props.artifacts.filter(item => item.status === 'PARSED'))
const methodDocuments = computed(() => triage.value?.method_coverage?.documents || [])
const planHypotheses = computed(() => triage.value?.plan?.hypotheses || [])
const screeningSteps = computed(() => triage.value?.plan?.screening_steps || [])

function bucketLabel(bucket: LogEvidenceBucket): string {
  const labels: Record<LogEvidenceBucket, string> = {
    LLM_RELEVANT: '① LLM 判断相关',
    METHOD_REQUIRED: '② 方法文档强制检查',
    OTHER: '③ 其他日志事件'
  }
  return labels[bucket]
}

function schedule() {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => void loadTriage(), 1400)
}

async function loadTriage() {
  if (!selectedArtifactId.value) {
    triage.value = null
    return
  }
  loading.value = !triage.value
  try {
    const { data } = await api.get<LogTriageRun | null>(`/cases/${props.caseId}/log-triage`, {
      params: { artifact_id: selectedArtifactId.value }
    })
    triage.value = data
    if (data?.status === 'COMPLETED') {
      await Promise.all((['LLM_RELEVANT', 'METHOD_REQUIRED', 'OTHER'] as LogEvidenceBucket[])
        .map(bucket => loadEvidence(bucket, 1)))
    } else if (data && ['QUEUED', 'RUNNING'].includes(data.status)) {
      schedule()
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志规划状态加载失败')
  } finally {
    loading.value = false
  }
}

async function startTriage() {
  if (!selectedArtifactId.value) return ElMessage.warning('请选择已解析日志')
  if (!props.modelEgressApproved) return ElMessage.warning('请先在案例概览确认模型出站授权')
  submitting.value = true
  try {
    await api.post(`/cases/${props.caseId}/artifacts/${selectedArtifactId.value}/triage`)
    ElMessage.success('LLM 日志规划已进入后台任务')
    await loadTriage()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志规划启动失败')
  } finally {
    submitting.value = false
  }
}

async function loadEvidence(bucket: LogEvidenceBucket, page: number) {
  if (!triage.value || triage.value.status !== 'COMPLETED') return
  const target = pages[bucket]
  const offset = (page - 1) * target.limit
  const { data } = await api.get<LogEvidencePage>(
    `/cases/${props.caseId}/log-triage/${triage.value.id}/evidence`,
    { params: { bucket, offset, limit: target.limit } }
  )
  pages[bucket] = data
}

function currentPage(bucket: LogEvidenceBucket): number {
  return Math.floor(pages[bucket].offset / pages[bucket].limit) + 1
}

function openItem(item: LogEvidenceItem) {
  emit('openSource', {
    artifactId: selectedArtifactId.value,
    sourceFile: item.source_file,
    line: item.line_start
  })
}

watch(parsedArtifacts, (items) => {
  if (!items.some(item => item.id === selectedArtifactId.value)) {
    selectedArtifactId.value = items[0]?.id || ''
  }
}, { immediate: true })

watch(selectedArtifactId, () => {
  triage.value = null
  void loadTriage()
})

onMounted(() => void loadTriage())
onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})
</script>

<template>
  <div v-loading="loading" data-testid="log-triage-panel">
    <el-alert
      v-if="!modelEgressApproved"
      type="warning"
      :closable="false"
      title="当前案例未授权向模型发送问题描述与方法文档；请先在案例概览开启授权。日志正文始终只在本地扫描。"
      style="margin-bottom:12px"
    />
    <div class="triage-toolbar">
      <el-select v-model="selectedArtifactId" placeholder="选择已解析日志" style="width:320px">
        <el-option v-for="item in parsedArtifacts" :key="item.id" :label="item.original_name" :value="item.id" />
      </el-select>
      <el-button type="primary" :disabled="!canEdit || !selectedArtifactId" :loading="submitting" @click="startTriage">
        {{ triage ? '重新执行 LLM 规划' : '启动 LLM 日志规划' }}
      </el-button>
      <el-tag v-if="triage" data-testid="log-triage-status" :type="triage.status === 'COMPLETED' ? 'success' : triage.status === 'FAILED' ? 'danger' : 'primary'">{{ triage.status }}</el-tag>
      <span v-if="triage" class="muted">{{ triage.model_name || '确定性回退' }} · {{ triage.plan?.planner_mode || '等待规划' }}</span>
    </div>

    <el-empty v-if="!selectedArtifactId" description="暂无已解析日志" />
    <el-empty v-else-if="!triage" description="尚未执行日志规划" />
    <template v-else>
      <el-alert v-if="triage.error_message" type="error" :closable="false" :title="triage.error_message" style="margin:12px 0" />
      <el-row :gutter="14" style="margin:14px 0">
        <el-col :span="8"><el-card shadow="never"><div class="muted">强制阅读方法</div><strong>{{ triage.method_coverage?.document_count || 0 }}</strong><div class="muted">规则 {{ triage.method_coverage?.compiled_pattern_count || 0 }}</div></el-card></el-col>
        <el-col :span="8"><el-card shadow="never"><div class="muted">实际命中事件</div><strong>{{ triage.summary?.matched_events || 0 }}</strong><div class="muted">总事件 {{ triage.summary?.total_events || 0 }}</div></el-card></el-col>
        <el-col :span="8"><el-card shadow="never"><div class="muted">其他事件</div><strong>{{ triage.summary?.other_events || 0 }}</strong><div class="muted">不会冒充关键证据</div></el-card></el-col>
      </el-row>

      <el-collapse style="margin-bottom:14px">
        <el-collapse-item title="已读取的方法文档与 LLM 规划摘要" name="methods">
          <el-table :data="methodDocuments" size="small" max-height="260">
            <el-table-column prop="title" label="文档" min-width="240" />
            <el-table-column prop="role" label="角色" width="190" />
            <el-table-column prop="version" label="版本" width="80" />
            <el-table-column prop="id" label="证据 ID" min-width="210" show-overflow-tooltip />
          </el-table>
          <h4>假设</h4><ul><li v-for="item in planHypotheses" :key="item">{{ item }}</li></ul>
          <h4>筛查步骤</h4><ol><li v-for="item in screeningSteps" :key="item">{{ item }}</li></ol>
        </el-collapse-item>
      </el-collapse>

      <PlanningTracePanel
        :case-id="caseId"
        :run-id="triage.agent_run_id"
        operation="log_triage_planning"
        title="日志 LLM Planning 轨迹"
        style="margin-bottom:14px"
      />

      <el-tabs v-if="triage.status === 'COMPLETED'" v-model="activeBucket" type="border-card">
        <el-tab-pane
          v-for="bucket in (['LLM_RELEVANT', 'METHOD_REQUIRED', 'OTHER'] as LogEvidenceBucket[])"
          :key="bucket"
          :name="bucket"
          :label="`${bucketLabel(bucket)} (${pages[bucket].total})`"
        >
          <el-table :data="pages[bucket].items" height="520" @row-click="openItem">
            <el-table-column prop="relevance_score" label="相关度" width="90" />
            <el-table-column prop="occurrence_count" label="次数" width="80" />
            <el-table-column prop="pattern_text" label="命中规则" min-width="210" show-overflow-tooltip />
            <el-table-column prop="message" label="日志证据" min-width="420" show-overflow-tooltip />
            <el-table-column prop="source_file" label="文件" min-width="180" show-overflow-tooltip />
            <el-table-column prop="line_start" label="首行" width="90" />
            <el-table-column prop="reason" label="排序理由" min-width="230" show-overflow-tooltip />
          </el-table>
          <el-pagination
            :current-page="currentPage(bucket)"
            :page-size="pages[bucket].limit"
            :total="pages[bucket].total"
            layout="total, prev, pager, next, jumper"
            style="margin-top:12px;justify-content:flex-end"
            @current-change="(page:number) => loadEvidence(bucket, page)"
          />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<style scoped>
.triage-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.muted { color: #64748b; font-size: 12px; }
strong { font-size: 24px; }
</style>
