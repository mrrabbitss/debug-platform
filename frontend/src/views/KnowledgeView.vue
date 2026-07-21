<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'

const documents = ref<any[]>([])
const dialogVisible = ref(false)
const file = ref<File | null>(null)
const form = reactive({ source_type: 'document', device_type: '', module: '', trust_level: 'MEDIUM' })

async function load() { documents.value = (await api.get('/knowledge')).data }
async function upload() {
  if (!file.value) return ElMessage.warning('请选择文档')
  const data = new FormData()
  data.append('file', file.value)
  Object.entries(form).forEach(([key, value]) => value && data.append(key, value))
  await api.post('/knowledge/upload', data)
  ElMessage.success('文档已切分并建立索引')
  dialogVisible.value = false
  file.value = null
  await load()
}
async function remove(id: string) { await api.delete(`/knowledge/${id}`); await load() }
onMounted(load)
</script>

<template>
  <div>
    <div class="toolbar"><h1 class="page-title" style="margin-right:auto">多源知识库</h1><el-button type="primary" @click="dialogVisible=true">上传知识</el-button><el-button @click="load">刷新</el-button></div>
    <el-card>
      <el-table :data="documents">
        <el-table-column prop="title" label="标题" min-width="260" />
        <el-table-column prop="source_type" label="来源" width="140" />
        <el-table-column prop="device_type" label="设备" width="90" />
        <el-table-column prop="module" label="模块" width="110" />
        <el-table-column prop="trust_level" label="可信级别" width="110" />
        <el-table-column prop="created_at" label="创建时间" width="190" />
        <el-table-column label="操作" width="80"><template #default="scope"><el-button link type="danger" @click="remove(scope.row.id)">删除</el-button></template></el-table-column>
      </el-table>
    </el-card>
    <el-dialog v-model="dialogVisible" title="上传协议、产品文档或历史案例" width="560px">
      <el-form label-width="100px">
        <el-form-item label="文件"><input type="file" accept=".txt,.md,.log,.json" @change="(e:any) => file = e.target.files?.[0] || null" /></el-form-item>
        <el-form-item label="来源类型"><el-select v-model="form.source_type"><el-option label="产品/协议文档" value="document"/><el-option label="历史故障" value="historical_bug"/><el-option label="测试规范" value="test_spec"/><el-option label="错误码规则" value="log_rule"/></el-select></el-form-item>
        <el-form-item label="设备类型"><el-select v-model="form.device_type" clearable><el-option label="GW" value="GW"/><el-option label="AP" value="AP"/></el-select></el-form-item>
        <el-form-item label="模块"><el-input v-model="form.module" placeholder="WLAN/WAN/PON/OMCI..." /></el-form-item>
        <el-form-item label="可信级别"><el-select v-model="form.trust_level"><el-option label="高" value="HIGH"/><el-option label="中" value="MEDIUM"/><el-option label="低" value="LOW"/></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" @click="upload">上传并索引</el-button></template>
    </el-dialog>
  </div>
</template>
