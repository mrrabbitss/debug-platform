<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Job } from '../../types'
import { elapsedLabel, jobTimestampMilliseconds, modelTaskProgress, type ModelTaskPresentationPhase } from './modelTaskProgress'

const props = withDefaults(defineProps<{
  job?: Partial<Job> | null
  phase?: ModelTaskPresentationPhase
  compact?: boolean
  testId?: string
}>(), { job: null, compact: false, testId: 'model-task-progress' })

const now = ref(Date.now())
let timer: number | undefined
const presentation = computed(() => modelTaskProgress(props.job, props.phase))
const needsClock = computed(() => Boolean(presentation.value?.waitingForModel && presentation.value.waitingSince))

function stopClock() { if (timer !== undefined) window.clearInterval(timer); timer = undefined }
function updateClock() {
  stopClock()
  if (needsClock.value) timer = window.setInterval(() => { now.value = Date.now() }, 1000)
}
watch(needsClock, updateClock, { immediate: true })
onMounted(updateClock)
onBeforeUnmount(stopClock)
</script>

<template>
  <section v-if="presentation" class="model-task-progress" :class="{ compact }" :data-testid="testId" aria-live="polite">
    <div class="model-task-progress__heading">
      <strong>{{ presentation.stage }}</strong>
      <span class="model-task-progress__percentage">约完成 {{ presentation.percentage }}%</span>
    </div>
    <el-progress :percentage="presentation.percentage" :status="presentation.state === 'active' ? undefined : presentation.state" :stroke-width="compact ? 6 : 8" :show-text="false" />
    <div class="model-task-progress__detail">{{ presentation.detail }}</div>
    <div class="model-task-progress__meta">
      <span>{{ presentation.remaining }}</span>
      <span v-if="presentation.waitingForModel && presentation.waitingSince"> · 已等待 {{ elapsedLabel(presentation.waitingSince, now) }}</span>
      <span v-if="presentation.heartbeatAt"> · 最近心跳 {{ new Date(jobTimestampMilliseconds(presentation.heartbeatAt)).toLocaleString() }}</span>
      <span v-else-if="presentation.lastUpdated"> · 最近状态更新 {{ new Date(jobTimestampMilliseconds(presentation.lastUpdated)).toLocaleString() }}</span>
    </div>
    <div v-if="$slots.actions" class="model-task-progress__actions"><slot name="actions" /></div>
  </section>
</template>

<style scoped>
.model-task-progress { margin: 12px 0; border: 1px solid #dcdfe6; border-radius: 6px; padding: 12px; background: #fafcff; }
.model-task-progress__heading { display:flex; justify-content:space-between; gap:12px; align-items:center; margin-bottom:8px; }
.model-task-progress__percentage { color:#606266; font-size:13px; white-space:nowrap; }
.model-task-progress__detail { margin-top:7px; color:#303133; font-size:13px; }
.model-task-progress__meta { margin-top:4px; color:#909399; font-size:12px; }
.model-task-progress__actions { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap; }
.model-task-progress.compact { margin:0; padding:8px; min-width:230px; }
.model-task-progress.compact .model-task-progress__detail { margin-top:5px; }
</style>
