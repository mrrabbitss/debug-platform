<script setup lang="ts">
defineProps<{
  modelValue: boolean
  title: string
  text: string
  startLine: number
  hasMore: boolean
  hasSource: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  navigate: [startLine: number]
}>()
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="900px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <pre data-testid="curation-source-preview" class="source-preview">{{ text }}</pre>
    <template #footer>
      <el-button
        :disabled="startLine <= 1 || !hasSource"
        @click="emit('navigate', Math.max(1, startLine - 500))"
      >上一段</el-button>
      <el-button
        :disabled="!hasSource || !hasMore"
        @click="emit('navigate', startLine + 500)"
      >下一段</el-button>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.source-preview {
  min-height: 520px;
  max-height: 65vh;
  overflow: auto;
  padding: 14px;
  background: #111827;
  color: #d1fae5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
