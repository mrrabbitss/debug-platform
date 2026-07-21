<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
const model = ref<any>({})
const apiKey = ref(localStorage.getItem('gw_ap_api_key') || '')
async function load() { model.value = (await api.get('/system/model')).data }
async function test() { const { data } = await api.post('/system/model/test'); data.ok ? ElMessage.success('模型连接正常') : ElMessage.warning(data.response) }
function saveKey() { localStorage.setItem('gw_ap_api_key', apiKey.value); ElMessage.success('前端 API Key 已保存') }
onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">系统设置</h1>
    <el-card style="max-width:760px">
      <template #header>模型网关</template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="Provider">{{ model.provider }}</el-descriptions-item>
        <el-descriptions-item label="Model">{{ model.model }}</el-descriptions-item>
        <el-descriptions-item label="Base URL">{{ model.base_url_configured ? '已配置' : '未配置' }}</el-descriptions-item>
        <el-descriptions-item label="API Key">{{ model.api_key_configured ? '已配置' : '未配置' }}</el-descriptions-item>
      </el-descriptions>
      <p class="muted">Qwen、GLM 及公司内部模型统一通过 OpenAI-Compatible Base URL、API Key 和模型名配置。密钥只保存在后端环境变量中。</p>
      <el-button type="primary" @click="test">测试模型连接</el-button>
    </el-card>
    <el-card style="max-width:760px;margin-top:16px">
      <template #header>后端 API 鉴权</template>
      <el-input v-model="apiKey" type="password" show-password placeholder="后端未配置 API_KEY 时可留空" />
      <el-button style="margin-top:12px" @click="saveKey">保存到浏览器</el-button>
    </el-card>
  </div>
</template>
