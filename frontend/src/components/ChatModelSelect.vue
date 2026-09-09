<script setup lang="ts">
import { computed } from 'vue'
import type { WorkbenchModel } from '../composables/useWorkbench'
const props = withDefaults(defineProps<{ modelValue: string | null; models: WorkbenchModel[]; label?: string; disabled?: boolean; placeholder?: string }>(), { label: '诊断模型', placeholder: '跟随个人选择 / 共享默认' })
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()
const groups = computed(() => [
  { name: '共享诊断模型', items: props.models.filter(item => item.visibility !== 'PRIVATE') },
  { name: '我的私有模型', items: props.models.filter(item => item.visibility === 'PRIVATE') }
].filter(group => group.items.length))
</script>
<template>
  <el-select :model-value="modelValue" @update:model-value="value => emit('update:modelValue', value || null)" :aria-label="label" :disabled="disabled" clearable :placeholder="placeholder" class="model-choice">
    <el-option-group v-for="group in groups" :key="group.name" :label="group.name">
      <el-option v-for="item in group.items" :key="item.id" :value="item.id" :label="item.name + (item.active ? ' · 共享默认' : '')" />
    </el-option-group>
  </el-select>
</template>
