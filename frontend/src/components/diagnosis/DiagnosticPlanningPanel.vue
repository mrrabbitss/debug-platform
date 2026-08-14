<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  planning?: Record<string, any>
}>()

const methodCatalog = computed(() => props.planning?.method_usage || props.planning?.method_catalog || [])
const toolCalls = computed(() => props.planning?.tool_calls || [])
const faultTreeCoverage = computed(() => props.planning?.fault_tree_coverage || {})
const faultTreeItems = computed(() => faultTreeCoverage.value.items || [])
const methodTitles = computed<Record<string, string>>(() => Object.fromEntries(
  methodCatalog.value.map((item: any) => [String(item.id), String(item.title || item.id)])
))

function methodLabel(documentId: string): string {
  return methodTitles.value[documentId] ? `${methodTitles.value[documentId]}（${documentId}）` : documentId
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
</script>

<template>
  <section v-if="planning" data-testid="diagnostic-planning-details">
    <h3 class="section-title">故障树规划与排查</h3>
    <el-alert
      v-if="planning.planner_failure || planning.planner_mode === 'deterministic_fallback'"
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
      :title="`LLM 规划已通过 · ${planning.agent_mode || '受控规划'} · ${planning.rounds.length}/20 轮`"
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
    <el-collapse style="margin-bottom:12px">
      <el-collapse-item v-if="faultTreeItems.length" title="故障树逐节点结论" name="fault-tree-coverage">
        <el-table :data="faultTreeItems" size="small" max-height="460">
          <el-table-column prop="label" label="节点" min-width="150" />
          <el-table-column prop="category" label="类型" width="130" />
          <el-table-column prop="section" label="章节" min-width="220" show-overflow-tooltip />
          <el-table-column label="结论" width="120">
            <template #default="scope"><el-tag :type="coverageStatusType(scope.row.status)" size="small">{{ coverageStatusLabel(scope.row.status) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="已检索" width="90"><template #default="scope">{{ scope.row.attempted ? '是' : '否' }}</template></el-table-column>
          <el-table-column prop="rationale" label="判断依据" min-width="320" show-overflow-tooltip />
          <el-table-column prop="next_action" label="下一步" min-width="280" show-overflow-tooltip />
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
          <el-table-column prop="id" label="文档 ID" min-width="210" show-overflow-tooltip />
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
          <el-table-column prop="rationale" label="调用原因" min-width="280" show-overflow-tooltip />
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
        </template>
        <el-table :data="round.method_assessments || []" size="small">
          <el-table-column label="方法文档" min-width="300"><template #default="scope">{{ methodLabel(scope.row.method_document_id) }}</template></el-table-column>
          <el-table-column prop="relevance" label="相关性" width="170" />
          <el-table-column prop="rationale" label="判断理由" min-width="320" />
        </el-table>
        <h4>按方法执行的检查</h4>
        <el-table :data="round.checks || []" size="small">
          <el-table-column label="来源方法" min-width="300"><template #default="scope">{{ methodLabel(scope.row.method_document_id) }}</template></el-table-column>
          <el-table-column prop="description" label="检查" min-width="300" />
          <el-table-column prop="evidence_needed" label="所需证据" min-width="280" />
          <el-table-column prop="completion_rule" label="完成条件" min-width="260" />
        </el-table>
        <h4>实际调度的认知检索</h4>
        <el-table :data="round.search_queries || []" size="small">
          <el-table-column prop="query" label="查询" min-width="320" />
          <el-table-column label="来源方法" min-width="220">
            <template #default="scope">{{ (scope.row.method_document_ids || []).map(methodLabel).join('、') }}</template>
          </el-table-column>
          <el-table-column prop="rationale" label="目的" min-width="280" />
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.section-title { margin: 20px 0 10px; }
h4 { margin: 16px 0 8px; }
</style>
