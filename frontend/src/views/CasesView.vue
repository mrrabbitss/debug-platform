<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import type { CaseItem, Principal } from '../types'

const router = useRouter()
const cases = ref<CaseItem[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const principal = ref<Principal | null>(null)
const authError = ref('')
const form = reactive({
  title: '', device_type: 'GW', device_model: '', firmware_version: '', issue_time: '',
  description: '', reproduction_steps: '', topology: ''
})
const canCreate = computed(() => principal.value !== null && principal.value.role !== 'VIEWER')

function errorMessage(error: any, fallback: string) {
  return error?.response?.data?.detail || error?.message || fallback
}

async function loadIdentity() {
  try {
    principal.value = (await api.get('/system/me')).data
    authError.value = ''
  } catch (error: any) {
    principal.value = null
    authError.value = errorMessage(error, '需要先配置访问凭据')
  }
}

async function loadCases() {
  if (!principal.value) return
  loading.value = true
  try {
    cases.value = (await api.get('/cases')).data
    authError.value = ''
  } catch (error: any) {
    authError.value = errorMessage(error, '案例读取失败')
  }
  finally { loading.value = false }
}

async function createCase() {
  if (!form.title.trim()) return ElMessage.warning('请填写问题标题')
  try {
    const { data } = await api.post('/cases', form)
    dialogVisible.value = false
    ElMessage.success('案例已创建')
    router.push(`/cases/${data.id}`)
  } catch (error: any) {
    ElMessage.error(errorMessage(error, '案例创建失败'))
  }
}

onMounted(async () => {
  await loadIdentity()
  await loadCases()
})
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">故障案例</h1>
      <el-tag v-if="principal" effect="plain">{{ principal.role }}</el-tag>
      <el-button type="primary" :disabled="!canCreate" @click="dialogVisible = true">新建案例</el-button>
      <el-button @click="loadCases">刷新</el-button>
    </div>
    <el-alert v-if="authError" type="warning" :closable="false" :title="authError" style="margin-bottom:14px" />
    <div v-if="authError" class="toolbar"><el-button type="primary" @click="router.push('/security')">打开安全与审计并配置凭据</el-button></div>
    <el-alert v-else-if="principal?.role === 'VIEWER'" type="info" :closable="false" title="当前账号为只读角色，可以查看获授权案例，但不能新建案例。" style="margin-bottom:14px" />
    <el-card>
      <el-table :data="cases" v-loading="loading" @row-dblclick="(row: CaseItem) => router.push(`/cases/${row.id}`)">
        <el-table-column prop="id" label="案例编号" width="210" />
        <el-table-column prop="title" label="问题标题" min-width="220" />
        <el-table-column prop="device_type" label="设备" width="80" />
        <el-table-column prop="device_model" label="型号" width="130" />
        <el-table-column prop="firmware_version" label="固件版本" width="130" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="scope"><el-tag>{{ scope.row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="severity" label="级别" width="90" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="100">
          <template #default="scope"><el-button link type="primary" @click="router.push(`/cases/${scope.row.id}`)">查看</el-button></template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" title="创建 GW/AP 故障案例" width="680px">
      <el-form label-width="110px">
        <el-form-item label="问题标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="设备类型"><el-radio-group v-model="form.device_type"><el-radio-button value="GW">GW</el-radio-button><el-radio-button value="AP">AP</el-radio-button><el-radio-button value="OTHER">其他</el-radio-button></el-radio-group></el-form-item>
        <el-form-item label="设备型号"><el-input v-model="form.device_model" /></el-form-item>
        <el-form-item label="固件版本"><el-input v-model="form.firmware_version" /></el-form-item>
        <el-form-item label="问题时间"><el-input v-model="form.issue_time" placeholder="例如 2026-07-20 10:32:00" /></el-form-item>
        <el-form-item label="组网环境"><el-input v-model="form.topology" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="问题现象"><el-input v-model="form.description" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="复现步骤"><el-input v-model="form.reproduction_steps" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" @click="createCase">创建</el-button></template>
    </el-dialog>
  </div>
</template>
