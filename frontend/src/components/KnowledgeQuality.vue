<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type { KnowledgeDocument } from '../types'
const props = defineProps<{ document: KnowledgeDocument }>()
const emit = defineEmits<{ changed: [] }>()
const visible = ref(false)
const busy = ref(false)
interface Quality {
  content_sha256: string
  compiler: { covered_characters: number; characters: number; complete: boolean; unclosed_code_fence: boolean }
  patterns: { text: string; meaning: string; line_start: number }[]
  fault_tree_nodes: { label: string; description: string; line_start: number }[]
  findings: { title: string; kind: string; similarity: number }[]
  comparison_truncated: boolean
  comparison_count: number
}
const quality = ref<Quality | null>(null)
async function open() {
  busy.value = true
  try {
    quality.value = (await api.get(`/knowledge/${props.document.id}/quality`)).data
    visible.value = true
  } catch { ElMessage.error('无法读取知识解析结果，请检查权限或正文格式') }
  finally { busy.value = false }
}
async function adopt() {
  if (!quality.value) return
  try { await ElMessageBox.confirm('确认这是人工核实过的历史归因或整理资料？系统将记录本次确认，设为高置信度并构建发布版本。', '采用人工核实资料') }
  catch { return }
  busy.value = true
  try {
    await api.post(`/knowledge/${props.document.id}/adopt-human-verified`, {
      human_verified: true, content_sha256: quality.value.content_sha256,
      expected_lock_version: props.document.lock_version
    })
    ElMessage.success('高置信度资料发布任务已提交，可在任务中心查看')
    visible.value = false
    emit('changed')
  } catch { ElMessage.error('发布失败：请确认管理员权限，并刷新已变化的资料') }
  finally { busy.value = false }
}
</script>
<template>
  <el-button link :loading="busy" @click="open">解析与质量</el-button>
  <el-dialog v-model="visible" title="知识解析与质量" width="80%" append-to-body>
    <template v-if="quality">
      <p>全文覆盖 {{ quality.compiler.covered_characters }} / {{ quality.compiler.characters }} 字符；提取 {{ quality.patterns.length }} 个日志模式、{{ quality.fault_tree_nodes.length }} 个故障树检查点。请对照正文核实提取结果。</p>
      <el-alert v-if="quality.compiler.unclosed_code_fence" title="正文存在未闭合代码块，请检查结构" type="warning" :closable="false" />
      <el-tabs>
        <el-tab-pane label="日志模式"><el-table :data="quality.patterns"><el-table-column prop="text" label="Pattern" /><el-table-column prop="meaning" label="含义" /><el-table-column prop="line_start" label="源行" width="80" /></el-table></el-tab-pane>
        <el-tab-pane label="故障树检查"><el-table :data="quality.fault_tree_nodes"><el-table-column prop="label" label="检查点" /><el-table-column prop="description" label="判断内容" /><el-table-column prop="line_start" label="源行" width="80" /></el-table></el-tab-pane>
        <el-tab-pane label="重复与冲突提示">
          <p>已比较 {{ quality.comparison_count }} 篇可访问资料{{ quality.comparison_truncated ? '，超过本次比较上限' : '' }}。相似度、数字和否定词差异仅用于提示，需结合型号、固件和适用条件人工判断。</p>
          <el-table :data="quality.findings"><el-table-column prop="title" label="相关资料" /><el-table-column prop="kind" label="提示" /><el-table-column prop="similarity" label="相似度" /></el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
    <template #footer><el-button v-if="document.can_attest_history && ['DRAFT', 'REJECTED'].includes(document.review_status)" type="primary" :loading="busy" @click="adopt">确认为人工核实资料并发布</el-button></template>
  </el-dialog>
</template>
