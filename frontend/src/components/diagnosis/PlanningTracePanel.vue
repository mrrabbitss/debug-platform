<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../../api/client'
import type { AgentRun, AgentTraceEvent } from '../../types'

const props = withDefaults(defineProps<{
  caseId: string
  runId?: string
  operation?: string
  title?: string
}>(), {
  runId: '',
  operation: '',
  title: 'LLM 规划运行轨迹'
})

const run = ref<AgentRun | null>(null)
const loading = ref(false)
const errorMessage = ref('')
let timer: number | null = null

const terminal = computed(() => (
  !run.value || ['COMPLETED', 'FAILED', 'CANCELLED', 'FALLBACK'].includes(run.value.status)
))
const displayedInputTokens = computed(() => {
  const direct = Number(run.value?.usage.input_tokens || 0)
  if (direct > 0) return direct
  return (run.value?.events || []).reduce((sum, event) => sum + Number(event.input_tokens || 0), 0)
})
const displayedOutputTokens = computed(() => {
  const direct = Number(run.value?.usage.output_tokens || 0)
  if (direct > 0) return direct
  return (run.value?.events || []).reduce((sum, event) => sum + Number(event.output_tokens || 0), 0)
})
const displayedTotalTokens = computed(() => {
  const direct = Number(run.value?.usage.total_tokens || 0)
  return direct > 0 ? direct : displayedInputTokens.value + displayedOutputTokens.value
})

function eventDescription(event: AgentTraceEvent): string {
  const metadata = event.metadata || {}
  if (event.stage === 'read_method_document') {
    return `${metadata.title || metadata.document_id || '方法文档'} · v${metadata.version || '?'}`
  }
  if (event.stage.startsWith('llm_planning_round_')) {
    return `第 ${metadata.round || '?'} 轮 · ${metadata.stop_reason || '继续分析'}${event.retry_count ? ` · 结构纠错 ${event.retry_count} 次` : ''}`
  }
  if (event.stage === 'execute_planned_search') {
    return `按 LLM 规划执行认知检索${metadata.document_id ? ` · 方法 ${metadata.document_id}` : ''}`
  }
  if (event.stage === 'rank_log_evidence') return '完成三层日志证据排序'
  return String(metadata.reason || metadata.planner_mode || event.tool_name || event.stage)
}

async function resolveRunId(): Promise<string> {
  if (props.runId) return props.runId
  const { data } = await api.get<AgentRun[]>(`/cases/${props.caseId}/agent-runs`, {
    params: { operation: props.operation || undefined, limit: 1 }
  })
  return data[0]?.run_id || ''
}

async function loadTrace() {
  loading.value = !run.value
  errorMessage.value = ''
  try {
    const target = await resolveRunId()
    if (!target) {
      run.value = null
      return
    }
    const { data } = await api.get<AgentRun>(`/cases/${props.caseId}/agent-runs/${target}`)
    run.value = data
    if (!terminal.value) schedule()
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '轨迹加载失败'
  } finally {
    loading.value = false
  }
}

function schedule() {
  if (timer) window.clearTimeout(timer)
  timer = window.setTimeout(() => void loadTrace(), 1400)
}

watch(() => [props.runId, props.operation], () => {
  run.value = null
  void loadTrace()
})

onMounted(() => void loadTrace())
onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})
</script>

<template>
  <el-card
    shadow="never"
    class="planning-trace"
    :header="title"
    v-loading="loading"
    data-testid="planning-trace"
    :data-operation="operation"
  >
    <el-alert v-if="errorMessage" type="error" :closable="false" :title="errorMessage" />
    <el-empty v-else-if="!run" description="尚未创建规划轨迹" :image-size="72" />
    <template v-else>
      <div class="trace-summary">
        <el-tag :type="run.status === 'FAILED' ? 'danger' : run.status === 'COMPLETED' ? 'success' : 'primary'">{{ run.status }}</el-tag>
        <span>停止原因：{{ run.stop_reason }}</span>
        <span>模型：{{ run.model_name || '确定性回退' }}</span>
        <span>Tokens：{{ displayedTotalTokens }}（输入 {{ displayedInputTokens }} / 输出 {{ displayedOutputTokens }}）</span>
        <span>耗时：{{ run.duration_ms || 0 }} ms</span>
        <el-button size="small" @click="loadTrace">刷新</el-button>
      </div>
      <el-timeline v-if="run.events?.length" class="trace-timeline">
        <el-timeline-item
          v-for="event in run.events"
          :key="event.id"
          :timestamp="`${event.sequence}. ${event.stage}`"
          :type="event.status === 'FAILED' ? 'danger' : event.status === 'RUNNING' ? 'primary' : 'success'"
          placement="top"
        >
          <div class="trace-event">
            <div><strong>{{ event.tool_name || event.stage }}</strong> · {{ event.status }}</div>
            <div class="muted">{{ eventDescription(event) }}</div>
            <div class="muted">耗时 {{ event.duration_ms }} ms · 输入/输出 {{ event.input_tokens }}/{{ event.output_tokens }} tokens · 证据 {{ event.evidence_ids.length }}</div>
          </div>
        </el-timeline-item>
      </el-timeline>
    </template>
  </el-card>
</template>

<style scoped>
.trace-summary { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; margin-bottom: 16px; }
.trace-timeline { max-height: 520px; overflow: auto; padding: 6px 14px; }
.trace-event { border: 1px solid #e5e7eb; border-radius: 6px; padding: 9px 12px; background: #fff; }
.muted { color: #64748b; font-size: 12px; margin-top: 4px; }
</style>
