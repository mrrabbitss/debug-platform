<script setup lang="ts">
import { computed } from 'vue'
import type { ProblemCategory } from '../../composables/useWorkbench'
const props = defineProps<{ category?: ProblemCategory }>()
const message = computed(() => {
  const item = props.category; if (!item) return ''
  if (item.skill_warning) return item.skill_warning
  const status = item.skill_status
  if (status && typeof status === 'object' && (status.warning || status.message)) return status.warning || status.message || ''
  const mode = String(typeof status === 'object' ? status.mode || status.status || '' : status || '').toUpperCase()
  if (['NONE','NO_SKILL','EVIDENCE_ONLY','MISSING'].includes(mode)) return '未使用任何 Skill：此类别与通用知识均无已发布 Skill，仍允许仅按日志证据诊断。'
  if (['GENERAL','COMMON','GENERAL_ONLY','FALLBACK'].includes(mode)) return '缺少专属 Skill：本次诊断使用通用 Skill，并依据日志证据分析。'
  if (item.has_skill === false || item.skill_count === 0) return item.has_general_skill === false ? '未使用任何 Skill：仍允许仅按日志证据诊断。' : item.has_general_skill === true ? '缺少专属 Skill，将使用通用 Skill。' : ''
  return ''
})
</script>
<template><el-alert v-if="message" :title="message" type="warning" show-icon :closable="false" class="inline-alert skill-status-notice" /></template>
