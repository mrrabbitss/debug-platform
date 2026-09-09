<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import { failure } from '../../composables/useWorkbench'
import type { CaseItem } from '../../types'
const props = defineProps<{
  caseInfo: CaseItem; canEdit: boolean
  categories: { id: string; name: string }[]
  models: { id: string; name: string; active: boolean }[]
}>()
const emit = defineEmits<{ updated: [value: CaseItem] }>()
const draft = reactive({ problem_category: 'unknown', chat_profile_id: null as string | null, description: '' })
const busy = ref(false), error = ref('')
watch(() => props.caseInfo, value => Object.assign(draft, { problem_category: value.problem_category || 'unknown', chat_profile_id: value.chat_profile_id || null, description: value.description }), { immediate: true })
async function save() {
  if (busy.value || !props.canEdit) return
  busy.value = true; error.value = ''
  try { emit('updated', (await api.patch(`/cases/${props.caseInfo.id}`, { ...draft, chat_profile_id: draft.chat_profile_id || null })).data); ElMessage.success('已保存，新的诊断将使用这些设置') }
  catch (cause) { error.value = failure(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <el-form label-position="top" class="case-options" @submit.prevent="save">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <div class="form-columns">
      <el-form-item label="问题类别"><el-select v-model="draft.problem_category" aria-label="案例问题类别" :disabled="!canEdit || busy"><el-option v-for="item in categories" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
      <el-form-item label="诊断模型"><el-select v-model="draft.chat_profile_id" aria-label="案例诊断模型" clearable placeholder="跟随系统默认" :disabled="!canEdit || busy"><el-option v-for="item in models" :key="item.id" :label="item.name + (item.active ? ' · 系统默认' : '')" :value="item.id" /></el-select></el-form-item>
    </div>
    <el-form-item label="问题现象"><el-input v-model="draft.description" aria-label="案例问题现象" type="textarea" :rows="3" :disabled="!canEdit || busy" /></el-form-item>
    <div class="toolbar"><el-button v-if="canEdit" :loading="busy" @click="save">保存问题资料</el-button><span class="field-hint">运行中的诊断使用启动时固定的知识、模板与模型设置。</span></div>
  </el-form>
</template>
