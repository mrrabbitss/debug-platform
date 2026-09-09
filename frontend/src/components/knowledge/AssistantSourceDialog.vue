<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api/client'
import { failure } from '../../composables/useWorkbench'
const props = defineProps<{ sessionId: string; path: string }>()
const emit = defineEmits<{ close: [] }>()
const loading = ref(false), error = ref('')
const page = ref<{ content: string; start: number; end: number; total_characters: number; next_cursor: number | null } | null>(null)
const cursors = ref<number[]>([]), current = ref(0)
let epoch = 0
async function read(cursor = 0) {
  const token = ++epoch
  loading.value = true; error.value = ''
  try {
    const { data } = await api.get(`/workbench/assistant/${props.sessionId}/source`, { params: { path: props.path, cursor } })
    if (token === epoch) { page.value = data; current.value = cursor }
  } catch (cause) { if (token === epoch) error.value = failure(cause) }
  finally { if (token === epoch) loading.value = false }
}
function next() { if (page.value?.next_cursor != null) { cursors.value.push(current.value); void read(page.value.next_cursor) } }
function previous() { void read(cursors.value.pop() || 0) }
watch(() => props.path, path => { ++epoch; page.value = null; cursors.value = []; if (path) void read() }, { immediate: true })
</script>
<template>
  <el-dialog :model-value="!!path" @close="emit('close')" :title="path" width="850px">
    <el-alert v-if="error" :title="error" type="error" :closable="false"><el-button link @click="read(current)">重试读取</el-button></el-alert>
    <div v-loading="loading"><p v-if="page" class="field-hint">第 {{ page.start + 1 }}–{{ page.end }} 字符 / 全文 {{ page.total_characters }} 字符</p><pre class="document-text">{{ page?.content }}</pre></div>
    <template #footer><el-button :disabled="!cursors.length||loading" @click="previous">上一段</el-button><el-button :disabled="page?.next_cursor==null||loading" @click="next">继续阅读</el-button><el-button @click="emit('close')">关闭</el-button></template>
  </el-dialog>
</template>
