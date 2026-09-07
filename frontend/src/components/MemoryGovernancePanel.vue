<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'

interface MemoryCandidate {
  id: string; case_id: string | null; title: string; content: string; memory_type: string
  review_status: string; scope: string; review_version: number; outcome: string
  evidence: unknown[]; expires_at: string | null; review_comment: string | null
}
const rows = ref<MemoryCandidate[]>([])
const state = ref('CANDIDATE')
const loading = ref(false)
const administrator = ref(false)
const statusLabels: Record<string, string> = { CANDIDATE: '待复核', PUBLISHED: '已发布', REJECTED: '已驳回', ARCHIVED: '已归档' }
const outcomeLabels: Record<string, string> = { UNKNOWN: '尚未验证实际结果', SUCCESS: '人工确认已解决', FAILED: '失败 / 未解决', PARTIAL: '任务不完整', RETRIEVED: '完成检索', NO_RESULT: '无检索结果' }

async function load() {
  if (!administrator.value) return
  loading.value = true
  try { rows.value = (await api.get('/memory-governance/candidates', { params: { review_status: state.value } })).data }
  catch (error: any) { ElMessage.error(error?.response?.data?.detail || '候选记忆加载失败') }
  finally { loading.value = false }
}

async function review(memoryId: string, action: 'PUBLISH' | 'REJECT' | 'ARCHIVE') {
  const item = rows.value.find(row => row.id === memoryId)
  if (!item) return
  const description = action === 'PUBLISH'
    ? '发布后，脱敏内容将在其他案例中复用，180 天后过期。请核对来源、证据和适用范围；发布不代表故障已解决。'
    : '请填写本次驳回或归档的原因；此内容将停止参与检索。'
  try {
    const { value } = await ElMessageBox.prompt(description, '人工复核候选经验', {
      inputType: 'textarea', inputValidator: value => !!value?.trim() || '请输入复核说明',
      confirmButtonText: action === 'PUBLISH' ? '确认全局发布' : '确认', cancelButtonText: '取消'
    })
    await api.post(`/memory-governance/candidates/${item.id}/review`, {
      action, expected_version: item.review_version, comment: value.trim(), expiry_days: 180
    })
    await load()
    ElMessage.success('复核结果已保存')
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error?.response?.data?.detail || '复核失败，请刷新后重试')
  }
}

onMounted(async () => {
  try { administrator.value = (await api.get('/system/me')).data.role === 'ADMIN'; await load() }
  catch { ElMessage.error('无法确认当前用户身份') }
})
</script>

<template>
  <el-alert title="模型输出先留在来源案例，不能自动成为全局经验。重复生成、被检索和高置信度都不等于真实有效；实际处理结果须另行确认。" type="info" :closable="false" />
  <p v-if="!administrator">此页面由管理员复核。工程师可以在来源案例查看候选记忆并提交实际处理反馈。</p>
  <div v-else v-loading="loading">
    <div class="toolbar" style="margin-top:16px">
      <el-select v-model="state" aria-label="候选记忆状态" style="width:180px" @change="load">
        <el-option v-for="(label, value) in statusLabels" :key="value" :label="label" :value="value" />
      </el-select>
      <el-button @click="load">刷新候选</el-button>
    </div>
    <el-table :data="rows" stripe empty-text="此状态下没有候选记忆">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding:16px">
            <pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{ row.content }}</pre>
            <p>来源案例：{{ row.case_id || '旧数据来源待补充，暂不可发布' }}</p>
            <p>证据：{{ JSON.stringify(row.evidence) }}</p>
            <p>复核说明：{{ row.review_comment || '尚未复核' }}</p>
            <p>到期时间：{{ row.expires_at || '未设置' }}</p>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="候选经验" min-width="260" />
      <el-table-column prop="memory_type" label="类型" width="135" />
      <el-table-column label="真实结果 / 任务状态" min-width="180"><template #default="{ row }">{{ outcomeLabels[row.outcome] || row.outcome }}</template></el-table-column>
      <el-table-column label="范围" width="100"><template #default="{ row }">{{ row.scope === 'GLOBAL' ? '全局' : '来源案例' }}</template></el-table-column>
      <el-table-column label="操作" width="235">
        <template #default="{ row }">
          <el-button v-if="row.review_status !== 'ARCHIVED' && row.memory_type === 'PROCEDURAL'" link type="primary" @click="review(row.id, 'PUBLISH')">复核发布</el-button>
          <el-button v-if="row.review_status === 'CANDIDATE'" link type="warning" @click="review(row.id, 'REJECT')">驳回</el-button>
          <el-button v-if="row.review_status !== 'ARCHIVED'" link type="danger" @click="review(row.id, 'ARCHIVE')">归档</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>
