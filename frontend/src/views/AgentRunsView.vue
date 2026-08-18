<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { AgentRun } from '../types'

const runs = ref<AgentRun[]>([])
const selected = ref<AgentRun | null>(null)
const loading = ref(false)
const replaying = ref(false)
const drawerVisible = ref(false)
const filters = reactive({ operation: '', status: '' })

const score = computed(() => {
  const value = selected.value?.score?.score
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
})

function tokenUsage(run: AgentRun) {
  const input = Number(run.usage.input_tokens || 0) || (run.events || [])
    .reduce((sum, event) => sum + Number(event.input_tokens || 0), 0)
  const output = Number(run.usage.output_tokens || 0) || (run.events || [])
    .reduce((sum, event) => sum + Number(event.output_tokens || 0), 0)
  return {
    input,
    output,
    total: Number(run.usage.total_tokens || 0) || input + output
  }
}

function errorMessage(error: any, fallback: string) {
  return error?.response?.data?.detail || error?.message || fallback
}

function formatTime(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function shortHash(value?: string) {
  return value ? `${value.slice(0, 12)}…` : '—'
}

async function loadRuns() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: 200 }
    if (filters.operation.trim()) params.operation = filters.operation.trim()
    if (filters.status) params.status = filters.status
    runs.value = (await api.get('/agent-runs', { params })).data
  } catch (error: any) {
    ElMessage.error(errorMessage(error, '读取运行轨迹失败'))
  } finally {
    loading.value = false
  }
}

async function openRun(run: AgentRun) {
  try {
    selected.value = (await api.get(`/agent-runs/${run.run_id}`)).data
    drawerVisible.value = true
  } catch (error: any) {
    ElMessage.error(errorMessage(error, '读取轨迹详情失败'))
  }
}

async function replay() {
  if (!selected.value?.replay_supported) return
  try {
    await ElMessageBox.confirm(
      '将使用脱敏参数重新执行只读检索，不写入在线记忆或知识库。是否继续？',
      '脱敏重放',
      { type: 'warning' }
    )
    replaying.value = true
    const result = (await api.post(`/agent-runs/${selected.value.run_id}/replay`)).data
    ElMessage.success(`重放完成：${result.stop_reason || 'COMPLETED'}`)
    await loadRuns()
    const replayed = runs.value.find(item => item.run_id === result.run_id)
    if (replayed) await openRun(replayed)
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error, '轨迹重放失败'))
  } finally {
    replaying.value = false
  }
}

onMounted(loadRuns)
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">Agent 运行轨迹</h1>
      <el-input v-model="filters.operation" clearable placeholder="操作，例如 agentic_search" style="width:250px" />
      <el-select v-model="filters.status" clearable placeholder="状态" style="width:150px">
        <el-option label="COMPLETED" value="COMPLETED" />
        <el-option label="FAILED" value="FAILED" />
      </el-select>
      <el-button type="primary" data-testid="agent-runs-refresh" @click="loadRuns">查询</el-button>
    </div>

    <el-alert
      type="info"
      :closable="false"
      title="这里只展示摘要哈希、可读证据位置、模型用量和脱敏配置；不会保存或回显未经处理的公司日志正文。"
      style="margin-bottom:16px"
    />

    <el-card v-loading="loading">
      <el-table :data="runs" row-key="run_id" data-testid="agent-runs-table">
        <el-table-column label="时间" width="180"><template #default="scope">{{ formatTime(scope.row.created_at) }}</template></el-table-column>
        <el-table-column prop="operation" label="操作" min-width="170" />
        <el-table-column prop="execution_mode" label="模式" width="130" />
        <el-table-column prop="status" label="状态" width="120" />
        <el-table-column prop="stop_reason" label="停止原因" min-width="190" />
        <el-table-column prop="approval_status" label="审批" min-width="190" />
        <el-table-column label="耗时" width="100"><template #default="scope">{{ scope.row.duration_ms }} ms</template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="scope"><el-button link type="primary" :data-testid="`agent-run-open-${scope.row.run_id}`" @click="openRun(scope.row as AgentRun)">查看</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drawerVisible" size="72%" title="运行轨迹详情">
      <template v-if="selected">
        <div class="toolbar">
          <el-tag>{{ selected.operation }}</el-tag>
          <el-tag type="success">{{ selected.status }}</el-tag>
          <el-tag type="warning">{{ selected.stop_reason }}</el-tag>
          <el-button
            type="primary"
            :disabled="!selected.replay_supported"
            :loading="replaying"
            data-testid="agent-run-replay"
            @click="replay"
          >脱敏只读重放</el-button>
        </div>
        <el-descriptions :column="3" border style="margin-bottom:16px">
          <el-descriptions-item label="run_id">{{ selected.run_id }}</el-descriptions-item>
          <el-descriptions-item label="case_id">{{ selected.case_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="评分">{{ score }}</el-descriptions-item>
          <el-descriptions-item label="模型">{{ selected.model_name || '确定性执行器' }}</el-descriptions-item>
          <el-descriptions-item label="Prompt 版本">{{ selected.prompt_version || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Tokens">{{ tokenUsage(selected).total }}（输入 {{ tokenUsage(selected).input }} / 输出 {{ tokenUsage(selected).output }}）</el-descriptions-item>
          <el-descriptions-item label="输入摘要哈希"><span class="mono">{{ shortHash(selected.input_summary_hash) }}</span></el-descriptions-item>
          <el-descriptions-item label="输出摘要哈希"><span class="mono">{{ shortHash(selected.output_summary_hash) }}</span></el-descriptions-item>
          <el-descriptions-item label="证据数">{{ selected.evidence_ids.length }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="selected.events || []" row-key="id" data-testid="agent-run-events">
          <el-table-column prop="sequence" label="#" width="55" />
          <el-table-column prop="stage" label="阶段" min-width="170" />
          <el-table-column prop="tool_name" label="工具" min-width="160" />
          <el-table-column prop="status" label="状态" width="110" />
          <el-table-column prop="duration_ms" label="耗时(ms)" width="100" />
          <el-table-column label="Tokens" width="150"><template #default="scope">{{ scope.row.input_tokens }}/{{ scope.row.output_tokens }}</template></el-table-column>
          <el-table-column prop="retry_count" label="重试" width="70" />
          <el-table-column label="证据位置" min-width="240"><template #default="scope">{{ scope.row.evidence_labels?.join('、') || '—' }}</template></el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>
