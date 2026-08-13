<script setup lang="ts">
defineProps<{
  planning?: Record<string, any>
}>()
</script>

<template>
  <section v-if="planning?.rounds?.length" data-testid="diagnostic-planning-details">
    <h3 class="section-title">故障树规划与排查</h3>
    <el-alert
      v-if="planning.planner_mode === 'deterministic_fallback'"
      type="warning"
      :closable="false"
      :title="`LLM 规划已回退：${planning.stop_reason || '校验失败'}`"
      style="margin-bottom:10px"
    />
    <el-collapse>
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
        </template>
        <el-table :data="round.method_assessments || []" size="small">
          <el-table-column prop="method_document_id" label="方法文档" min-width="210" />
          <el-table-column prop="relevance" label="相关性" width="170" />
          <el-table-column prop="rationale" label="判断理由" min-width="320" />
        </el-table>
        <h4>按方法执行的检查</h4>
        <el-table :data="round.checks || []" size="small">
          <el-table-column prop="method_document_id" label="来源方法" min-width="210" />
          <el-table-column prop="description" label="检查" min-width="300" />
          <el-table-column prop="evidence_needed" label="所需证据" min-width="280" />
          <el-table-column prop="completion_rule" label="完成条件" min-width="260" />
        </el-table>
        <h4>实际调度的认知检索</h4>
        <el-table :data="round.search_queries || []" size="small">
          <el-table-column prop="query" label="查询" min-width="320" />
          <el-table-column label="来源方法" min-width="220">
            <template #default="scope">{{ scope.row.method_document_ids?.join('、') }}</template>
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
