<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import { failure, type ProblemCategory, type WorkbenchModel } from '../../composables/useWorkbench'
import SkillStatusNotice from './SkillStatusNotice.vue'
import type { CaseItem } from '../../types'
const props = defineProps<{
  caseInfo: CaseItem; canEdit: boolean
  categories: ProblemCategory[]
  models: WorkbenchModel[]
  selectedModelId?: string | null
  modelSelectionError?: string | null
}>()
const emit = defineEmits<{ updated: [value: CaseItem] }>()
const draft = reactive({ problem_category: 'unknown', chat_profile_id: null as string | null, description: '' })
const busy = ref(false), error = ref('')
watch(() => props.caseInfo, value => Object.assign(draft, { problem_category: value.problem_category || 'unknown', chat_profile_id: value.chat_profile_id || null, description: value.description }), { immediate: true })
async function save() {
  if (busy.value || !props.canEdit) return
  busy.value = true; error.value = ''
  try { emit('updated', (await api.patch(`/cases/${props.caseInfo.id}`, { problem_category: draft.problem_category, description: draft.description })).data); ElMessage.success('已保存，新的诊断将使用这些问题资料') }
  catch (cause) { error.value = failure(cause) }
  finally { busy.value = false }
}
</script>
<template>
  <el-form label-position="top" class="case-options" @submit.prevent="save">
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert" />
    <div class="form-columns">
      <el-form-item label="问题类别"><el-select v-model="draft.problem_category" aria-label="案例问题类别" :disabled="!canEdit || busy"><el-option v-for="item in categories" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
      <el-form-item label="统一诊断模型"><div><strong>{{ modelSelectionError ? '当前模型选择不可用' : models.find(item=>item.id===selectedModelId)?.name || '跟随个人选择 / 共享默认' }}</strong><p class="field-hint">新诊断与问答使用你的统一模型配置。<router-link to="/settings">调整模型来源</router-link></p></div></el-form-item>
    </div>
    <el-alert v-if="modelSelectionError" :title="modelSelectionError" type="warning" :closable="false" show-icon class="inline-alert" />
    <SkillStatusNotice :category="categories.find(item=>item.id===draft.problem_category)" />
    <el-form-item label="问题现象"><el-input v-model="draft.description" aria-label="案例问题现象" type="textarea" :rows="3" :disabled="!canEdit || busy" /></el-form-item>
    <div class="toolbar"><el-button v-if="canEdit" :loading="busy" @click="save">保存问题资料</el-button><span class="field-hint">运行中的诊断使用启动时固定的知识、模板与模型设置。</span></div>
  </el-form>
</template>
