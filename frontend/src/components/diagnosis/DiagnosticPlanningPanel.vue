<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  planning?: Record<string, any>
  evidenceLabels?: Record<string, string>
}>()

const methodCatalog = computed(() => props.planning?.method_usage || props.planning?.method_catalog || [])
const toolCalls = computed(() => props.planning?.tool_calls || [])
const faultTreeCoverage = computed(() => props.planning?.fault_tree_coverage || {})
const faultTreeItems = computed(() => faultTreeCoverage.value.items || [])
const agentBudget = computed(() => props.planning?.budget || {})
const budgetLimits = computed(() => agentBudget.value.limits || {})
const budgetUsage = computed(() => agentBudget.value.usage || {})
const contextGovernance = computed(() => props.planning?.context_governance || {})
const plannerRepairs = computed(() => (props.planning?.rounds || []).flatMap(
  (round: any) => round.planner_repairs || []
))
const contextSections = computed(() => Object.entries(contextGovernance.value.sections || {}).map(([name, value]: [string, any]) => ({
  name,
  ...value
})))
const methodTitles = computed<Record<string, string>>(() => Object.fromEntries(
  methodCatalog.value.map((item: any) => [String(item.id), String(item.title || '诊断方法')])
))

function methodLabel(documentId: string): string {
  return methodTitles.value[documentId] || '诊断方法'
}

function displayText(value: unknown): string {
  let rendered = String(value ?? '')
  for (const [evidenceId, label] of Object.entries(props.evidenceLabels || {})) {
    rendered = rendered.split(evidenceId).join(label)
  }
  return rendered.replace(
    /\b(?:EVT|LEM|LEH|LDE|DOC|KCHUNK|LOCALDOC|SYM|COMMIT|MEM|ANL|AREV)-[A-Za-z0-9_.:-]+\b/g,
    '证据位置未记录'
  )
}

function evidenceLabel(evidenceId: unknown): string {
  return props.evidenceLabels?.[String(evidenceId)] || '证据位置未记录'
}

function repairLabel(code: unknown): string {
  return ({
    MISSING_METHOD_ASSESSMENTS_ADDED: '补齐逐文档相关性判断',
    SYMPTOM_METHOD_RELEVANCE_UPGRADED: '保留与现象重叠的故障树',
    UNSUPPORTED_CONCLUSIONS_DOWNGRADED: '无证据结论降级为证据不足',
    UNKNOWN_FAULT_TREE_ITEMS_REMOVED: '移除未知故障树节点',
    EXECUTABLE_CHECKS_ADDED: '补齐可执行检查',
    READ_ONLY_EVIDENCE_BINDINGS_ADDED: '补齐只读证据检索'
  } as Record<string, string>)[String(code || '')] || String(code || '安全规范化')
}

function repairSummary(repairs: any[]): string {
  return (repairs || []).map(
    (item: any) => `${repairLabel(item.code)} × ${item.count || 1}`
  ).join('；')
}

function coverageStatusLabel(status: string): string {
  return ({
    SUPPORTED: '证据支持',
    EXCLUDED: '已排除',
    INSUFFICIENT_EVIDENCE: '证据不足',
    PENDING: '待排查'
  } as Record<string, string>)[status] || status
}

function coverageStatusType(status: string): 'success' | 'info' | 'warning' | 'danger' {
  if (status === 'SUPPORTED') return 'success'
  if (status === 'EXCLUDED') return 'info'
  if (status === 'INSUFFICIENT_EVIDENCE') return 'warning'
  return 'danger'
}

function integer(value: unknown): string {
  return Number(value || 0).toLocaleString('zh-CN')
}

function budgetStopLabel(reason: unknown): string {
  return ({
    TOKEN_BUDGET: '累计 Token 达到上限',
    TIME_BUDGET: '运行时间达到上限',
    TOOL_CALL_BUDGET: '工具调用达到上限',
    NO_PROGRESS: '连续多轮没有新进展'
  } as Record<string, string>)[String(reason || '')] || String(reason || '未触发')
}
</script>

<template>
  <section v-if="planning" data-testid="diagnostic-planning-details">
    <h3 class="section-title">故障树规划与排查</h3>
    <el-alert
      v-if="planning.demo_snapshot"
      type="info"
      :closable="false"
      data-testid="demo-planning-snapshot"
      :title="`真实 GLM-5.2 历史规划快照 · ${planning.rounds?.length || 0} 轮 LLM Planning · ${integer(planning.budget?.usage?.total_tokens)} 规划 Token`"
      description="模型规划、只读工具调用和故障树结论来自历史成功运行；当前仅导入并重定位证据，没有再次调用模型，也没有复制私有方法正文。"
      style="margin-bottom:10px"
    />
    <el-alert
      v-else-if="planning.planner_failure || planning.planner_mode === 'deterministic_fallback'"
      type="warning"
      :closable="false"
      :title="`LLM 规划已回退：${planning.planner_failure?.code || planning.stop_reason || '校验失败'}`"
      :description="`${planning.planner_failure?.message || '当前结果保留确定性诊断底座'}${planning.planner_failure?.field_path ? `；字段：${planning.planner_failure.field_path}` : ''}${planning.planner_failure?.finish_reason ? `；模型停止原因：${planning.planner_failure.finish_reason}` : ''}`"
      style="margin-bottom:10px"
    />
    <el-alert
      v-else
      type="success"
      :closable="false"
      :title="`LLM 规划已通过 · ${planning.agent_mode || '受控规划'} · ${planning.rounds?.length || 0}/20 轮`"
      style="margin-bottom:10px"
    />
    <el-alert
      v-if="faultTreeItems.length"
      :type="faultTreeCoverage.complete ? 'success' : 'warning'"
      :closable="false"
      :title="`故障树逐节点覆盖：${faultTreeCoverage.concluded || 0}/${faultTreeCoverage.total || 0} 已有结论，${faultTreeCoverage.attempted || 0}/${faultTreeCoverage.total || 0} 已执行检索`"
      :description="faultTreeCoverage.complete ? '全部节点均已得到证据支持、排除或明确的证据不足结论。' : '覆盖未完成时 LLM 规划不会被标记为通过，也不会伪造已完成结论。'"
      style="margin-bottom:10px"
    />
    <el-alert
      v-if="faultTreeCoverage.fallback_applied"
      type="warning"
      :closable="false"
      title="模型规划未完整通过，已执行确定性方法证据扫描"
      :description="`故障树仍完成 ${faultTreeCoverage.concluded || 0}/${faultTreeCoverage.total || 0} 个终态；支持和排除只绑定实际日志证据，其他节点明确保留为证据不足。原因：${faultTreeCoverage.fallback_reason || planning.stop_reason || '未知'}`"
      style="margin-bottom:10px"
    />
    <el-alert
      v-else-if="faultTreeCoverage.resolution_source_counts?.DETERMINISTIC_METHOD_EVIDENCE"
      type="info"
      :closable="false"
      title="模型覆盖账本已与方法 Pattern 的本地证据扫描交叉核验"
      :description="`确定性核验节点：${faultTreeCoverage.resolution_source_counts.DETERMINISTIC_METHOD_EVIDENCE}；模型证据门禁节点：${faultTreeCoverage.resolution_source_counts.LLM_EVIDENCE_GATED || 0}。`"
      style="margin-bottom:10px"
    />
    <el-alert
      v-if="plannerRepairs.length"
      type="info"
      :closable="false"
      title="模型计划已完成安全规范化"
      :description="repairSummary(plannerRepairs)"
      style="margin-bottom:10px"
    />
    <el-collapse style="margin-bottom:12px">
      <el-collapse-item v-if="budgetLimits.max_rounds" title="Agent 预算与停止边界" name="agent-budget">
        <el-alert
          v-if="agentBudget.stop_reason"
          type="warning"
          :closable="false"
          :title="budgetStopLabel(agentBudget.stop_reason)"
          style="margin-bottom:10px"
        />
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="规划轮次">{{ integer(budgetUsage.rounds) }} / {{ integer(budgetLimits.max_rounds) }}</el-descriptions-item>
          <el-descriptions-item label="累计 Token">{{ integer(budgetUsage.total_tokens) }} / {{ integer(budgetLimits.max_total_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="只读工具调用">{{ integer(budgetUsage.tool_calls) }} / {{ integer(budgetLimits.max_total_tool_calls) }}</el-descriptions-item>
          <el-descriptions-item label="累计输入 Token">{{ integer(budgetUsage.input_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="累计输出 Token">{{ integer(budgetUsage.output_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="缓存输入 Token">{{ integer(budgetUsage.cached_input_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="工具输出估算">{{ integer(budgetUsage.tool_output_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="运行耗时">{{ (Number(budgetUsage.duration_ms || 0) / 1000).toFixed(1) }} 秒</el-descriptions-item>
          <el-descriptions-item label="连续无进展">{{ integer(budgetUsage.stagnant_rounds) }} / {{ integer(budgetLimits.max_stagnant_rounds) }} 轮</el-descriptions-item>
          <el-descriptions-item label="单轮工具上限">{{ integer(budgetLimits.max_tool_calls_per_round) }}</el-descriptions-item>
          <el-descriptions-item label="最少规划轮次">{{ integer(budgetLimits.min_rounds) }}</el-descriptions-item>
        </el-descriptions>
      </el-collapse-item>
      <el-collapse-item v-if="contextGovernance.context_window_tokens" title="上下文治理与压缩" name="context-governance">
        <el-descriptions :column="3" border size="small" style="margin-bottom:10px">
          <el-descriptions-item label="上下文窗口">{{ integer(contextGovernance.context_window_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="本轮输入预算">{{ integer(contextGovernance.input_budget_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="估算输入">{{ integer(contextGovernance.estimated_input_tokens) }}</el-descriptions-item>
          <el-descriptions-item label="累计实际输入">{{ integer(contextGovernance.actual_input_tokens_total) }}</el-descriptions-item>
          <el-descriptions-item label="压缩次数">{{ integer(contextGovernance.compaction_count) }}</el-descriptions-item>
          <el-descriptions-item label="临时读取句柄">{{ integer(contextGovernance.spill_handle_total) }}</el-descriptions-item>
        </el-descriptions>
        <el-alert type="info" :closable="false" title="临时句柄只用于本次运行继续读取被压缩正文，不会作为事实证据，也不会保存公司日志正文。" style="margin-bottom:10px" />
        <el-table :data="contextSections" size="small">
          <el-table-column prop="name" label="上下文分区" min-width="220" />
          <el-table-column label="原始估算"><template #default="scope">{{ integer(scope.row.original_tokens) }}</template></el-table-column>
          <el-table-column label="保留估算"><template #default="scope">{{ integer(scope.row.kept_tokens) }}</template></el-table-column>
          <el-table-column label="分区预算"><template #default="scope">{{ integer(scope.row.allocation_tokens) }}</template></el-table-column>
          <el-table-column label="省略条目"><template #default="scope">{{ integer(scope.row.omitted_items) }}</template></el-table-column>
        </el-table>
      </el-collapse-item>
      <el-collapse-item v-if="faultTreeItems.length" title="故障树逐节点结论" name="fault-tree-coverage">
        <el-table :data="faultTreeItems" size="small" max-height="460">
          <el-table-column prop="label" label="节点" min-width="150" />
          <el-table-column prop="category" label="类型" width="130" />
          <el-table-column prop="section" label="章节" min-width="220" show-overflow-tooltip />
          <el-table-column label="结论" width="120">
            <template #default="scope"><el-tag :type="coverageStatusType(scope.row.status)" size="small">{{ coverageStatusLabel(scope.row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="已检索" width="90"><template #default="scope">{{ scope.row.attempted ? '是' : '否' }}</template></el-table-column>
          <el-table-column label="判断依据" min-width="320" show-overflow-tooltip><template #default="scope">{{ displayText(scope.row.rationale) }}</template></el-table-column>
          <el-table-column label="实际证据" min-width="320" show-overflow-tooltip><template #default="scope">{{ (scope.row.evidence_ids || []).map(evidenceLabel).join('、') || '—' }}</template></el-table-column>
          <el-table-column label="下一步" min-width="280" show-overflow-tooltip><template #default="scope">{{ displayText(scope.row.next_action) }}</template></el-table-column>
          <el-table-column prop="line_start" label="方法行" width="90" />
        </el-table>
      </el-collapse-item>
      <el-collapse-item title="本次调用的文档与方法" name="method-usage">
        <el-table :data="methodCatalog" size="small" max-height="360">
          <el-table-column prop="title" label="文档" min-width="240" />
          <el-table-column prop="source_type" label="类型" width="150" />
          <el-table-column prop="device_type" label="设备" width="80" />
          <el-table-column prop="read_status" label="读取状态" width="180" />
          <el-table-column prop="relevance" label="相关性" width="170" />
          <el-table-column prop="check_count" label="检查" width="80" />
          <el-table-column prop="tool_call_count" label="工具调用" width="100" />
          <el-table-column prop="evidence_hit_count" label="证据命中" width="100" />
        </el-table>
      </el-collapse-item>
      <el-collapse-item title="原生只读工具调用" name="tool-calls">
        <el-table :data="toolCalls" size="small" max-height="360">
          <el-table-column prop="round" label="轮次" width="70" />
          <el-table-column prop="tool_name" label="工具" min-width="220" />
          <el-table-column prop="invoked_by" label="调度方" width="150" />
          <el-table-column label="关联方法" min-width="300" show-overflow-tooltip>
            <template #default="scope">{{ (scope.row.method_document_ids || []).map(methodLabel).join('、') || '—' }}</template>
          </el-table-column>
          <el-table-column label="调用原因" min-width="280" show-overflow-tooltip><template #default="scope">{{ displayText(scope.row.rationale) }}</template></el-table-column>
          <el-table-column label="故障树节点" width="110"><template #default="scope">{{ (scope.row.fault_tree_item_ids || []).length }}</template></el-table-column>
          <el-table-column prop="status" label="状态" width="130" />
          <el-table-column prop="returned" label="返回" width="80" />
        </el-table>
      </el-collapse-item>
    </el-collapse>
    <el-collapse>
      <el-empty v-if="!planning.rounds?.length" description="LLM 未产生通过校验的规划轮次；上方保留回退原因与强制读取的方法目录。" />
      <el-collapse-item
        v-for="round in planning.rounds"
        :key="round.round"
        :name="`planning-${round.round}`"
      >
        <template #title>
          <strong>第 {{ round.round }} 轮</strong>&nbsp;
          <el-tag size="small">方法 {{ round.method_assessments?.length || 0 }}</el-tag>&nbsp;
          <el-tag size="small" type="success">检查 {{ round.checks?.length || 0 }}</el-tag>&nbsp;
          <el-tag size="small" type="info">检索 {{ round.search_queries?.length || 0 }}</el-tag>
          &nbsp;<el-tag size="small" type="warning">节点结论 {{ round.fault_tree_assessments?.length || 0 }}</el-tag>
          &nbsp;<el-tag v-if="round.planner_repairs?.length" size="small" type="info">安全规范化 {{ round.planner_repairs.length }}</el-tag>
        </template>
        <el-alert
          v-if="round.planner_repairs?.length"
          type="info"
          :closable="false"
          :title="`本轮后端安全规范化：${repairSummary(round.planner_repairs)}`"
          style="margin-bottom:10px"
        />
        <el-table :data="round.method_assessments || []" size="small">
          <el-table-column label="方法文档" min-width="300"><template #default="scope">{{ methodLabel(scope.row.method_document_id) }}</template></el-table-column>
          <el-table-column prop="relevance" label="相关性" width="170" />
          <el-table-column label="判断理由" min-width="320"><template #default="scope">{{ displayText(scope.row.rationale) }}</template></el-table-column>
        </el-table>
        <h4>按方法执行的检查</h4>
        <el-table :data="round.checks || []" size="small">
          <el-table-column label="来源方法" min-width="300"><template #default="scope">{{ methodLabel(scope.row.method_document_id) }}</template></el-table-column>
          <el-table-column label="检查" min-width="300"><template #default="scope">{{ displayText(scope.row.description) }}</template></el-table-column>
          <el-table-column label="所需证据" min-width="280"><template #default="scope">{{ displayText(scope.row.evidence_needed) }}</template></el-table-column>
          <el-table-column label="完成条件" min-width="260"><template #default="scope">{{ displayText(scope.row.completion_rule) }}</template></el-table-column>
        </el-table>
        <h4>实际调度的认知检索</h4>
        <el-table :data="round.search_queries || []" size="small">
          <el-table-column label="查询" min-width="320"><template #default="scope">{{ displayText(scope.row.query) }}</template></el-table-column>
          <el-table-column label="来源方法" min-width="220">
            <template #default="scope">{{ (scope.row.method_document_ids || []).map(methodLabel).join('、') }}</template>
          </el-table-column>
          <el-table-column label="目的" min-width="280"><template #default="scope">{{ displayText(scope.row.rationale) }}</template></el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.section-title { margin: 20px 0 10px; }
h4 { margin: 16px 0 8px; }
</style>
