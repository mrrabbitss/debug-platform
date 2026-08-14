<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../../api/client'
import type { Artifact } from '../../types'

type SourceTarget = { artifactId: string, sourceFile: string, line: number }
type ManifestEntry = { path: string, line_count?: number, size?: number }

const props = defineProps<{ artifacts: Artifact[] }>()

const fileManifest = ref<Record<string, any>>({})
const rawLog = ref('')
const selectedArtifact = ref('')
const selectedLogPath = ref('')
const rawStartLine = ref(1)
const rawReturnedLines = ref(0)
const rawTotalLines = ref(0)
const rawHasMore = ref(false)
const rawEncoding = ref('')
const rawJumpLine = ref(1)
const targetLine = ref<number | null>(null)
const rawViewport = ref<HTMLElement | null>(null)
const rawSearchQuery = ref('')
const rawSearchMatches = ref<{line_number:number, text:string}[]>([])
const rawSearchNextLine = ref<number | null>(null)
const rawSearchScannedTo = ref(0)
const rawSearching = ref(false)

const manifestEntries = computed<ManifestEntry[]>(() => (
  Array.isArray(fileManifest.value.manifest) ? fileManifest.value.manifest : []
))
const parsedArtifacts = computed(() => props.artifacts.filter(item => item.status === 'PARSED'))
const visibleLines = computed(() => {
  if (!rawLog.value) return []
  const lines = rawLog.value.split(/\r?\n/)
  const limit = rawReturnedLines.value > 0 ? rawReturnedLines.value : lines.length
  return lines.slice(0, limit).map((text, index) => ({
    number: rawStartLine.value + index,
    text,
  }))
})

function normalizedPath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\.\/+/, '').replace(/\/{2,}/g, '/').toLowerCase()
}

function resolveManifestPath(requestedPath: string): string | null {
  const requested = normalizedPath(requestedPath)
  const entries = manifestEntries.value.filter(item => typeof item.path === 'string' && item.path)
  const exact = entries.find(item => normalizedPath(item.path) === requested)
  if (exact) return exact.path

  const suffixMatches = entries.filter(item => {
    const candidate = normalizedPath(item.path)
    return candidate.endsWith(`/${requested}`) || requested.endsWith(`/${candidate}`)
  })
  if (suffixMatches.length === 1) return suffixMatches[0].path

  const basename = requested.split('/').pop()
  const basenameMatches = entries.filter(item => normalizedPath(item.path).split('/').pop() === basename)
  return basenameMatches.length === 1 ? basenameMatches[0].path : null
}

function resetRawSearch() {
  rawSearchMatches.value = []
  rawSearchNextLine.value = null
  rawSearchScannedTo.value = 0
}

async function loadManifest(artifactId: string) {
  if (!artifactId) return
  selectedArtifact.value = artifactId
  fileManifest.value = (await api.get(`/artifacts/${artifactId}/files`)).data
  selectedLogPath.value = ''
  rawLog.value = ''
  targetLine.value = null
  resetRawSearch()
}

async function scrollToTarget() {
  await nextTick()
  if (!targetLine.value || !rawViewport.value) return
  const element = rawViewport.value.querySelector<HTMLElement>(`[data-line-number="${targetLine.value}"]`)
  element?.scrollIntoView({ block: 'center' })
}

async function loadRawLog(path: string, startLine = 1, highlightLine: number | null = null) {
  if (!selectedArtifact.value) return
  if (selectedLogPath.value !== path) resetRawSearch()
  selectedLogPath.value = path
  targetLine.value = highlightLine
  try {
    const response = await api.get(`/artifacts/${selectedArtifact.value}/content`, {
      params: { path, start_line: Math.max(1, startLine), line_count: 1000 }
    })
    rawLog.value = response.data
    rawStartLine.value = Number(response.headers['x-start-line'] || startLine)
    rawReturnedLines.value = Number(response.headers['x-returned-lines'] || 0)
    rawTotalLines.value = Number(response.headers['x-total-lines'] || 0)
    rawHasMore.value = String(response.headers['x-has-more'] || 'false') === 'true'
    rawEncoding.value = String(response.headers['x-text-encoding'] || '')
    rawJumpLine.value = highlightLine || rawStartLine.value
    await scrollToTarget()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '原始日志加载失败')
  }
}

async function selectManifestFile(path: string) {
  await loadRawLog(path)
}

async function openSource(payload: SourceTarget) {
  try {
    await loadManifest(payload.artifactId)
    const canonicalPath = resolveManifestPath(payload.sourceFile)
    if (!canonicalPath) {
      ElMessage.error(`无法在该日志包中唯一定位文件：${payload.sourceFile}`)
      return
    }
    const line = Math.max(1, Number(payload.line || 1))
    await loadRawLog(canonicalPath, Math.max(1, line - 20), line)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '日志来源跳转失败')
  }
}

async function searchRawLog(continueFromLast = false) {
  const query = rawSearchQuery.value.trim()
  if (!selectedArtifact.value || !selectedLogPath.value) return ElMessage.warning('请先选择日志文件')
  if (!query) return ElMessage.warning('请输入原始日志关键词')
  rawSearching.value = true
  try {
    const startLine = continueFromLast ? (rawSearchNextLine.value || 1) : 1
    const { data } = await api.get(`/artifacts/${selectedArtifact.value}/search`, {
      params: { path: selectedLogPath.value, query, start_line: startLine, limit: 100 }
    })
    rawSearchMatches.value = data.matches || []
    rawSearchNextLine.value = data.next_start_line
    rawSearchScannedTo.value = Number(data.scanned_to_line || 0)
    if (!rawSearchMatches.value.length) {
      ElMessage.info(data.has_more ? '当前扫描范围没有匹配，可继续搜索' : '没有找到匹配内容')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '原始日志搜索失败')
  } finally {
    rawSearching.value = false
  }
}

async function openRawSearchMatch(lineNumber: number) {
  await loadRawLog(selectedLogPath.value, Math.max(1, lineNumber - 20), lineNumber)
}

async function previousRawPage() {
  await loadRawLog(selectedLogPath.value, Math.max(1, rawStartLine.value - 1000))
}

async function nextRawPage() {
  await loadRawLog(selectedLogPath.value, rawStartLine.value + Math.max(rawReturnedLines.value, 1))
}

async function jumpToRawLine() {
  const maximum = rawTotalLines.value || Number.MAX_SAFE_INTEGER
  const line = Math.min(Math.max(1, rawJumpLine.value), maximum)
  await loadRawLog(selectedLogPath.value, Math.max(1, line - 20), line)
}

defineExpose({ loadManifest, openSource })
</script>

<template>
  <div class="toolbar">
    <el-select v-model="selectedArtifact" placeholder="选择已解析日志包" style="width:260px" @change="loadManifest">
      <el-option v-for="item in parsedArtifacts" :key="item.id" :label="item.original_name" :value="item.id" />
    </el-select>
    <span class="muted">解析文件数：{{ fileManifest.manifest_file_count || 0 }}</span>
    <span class="muted">解析器：{{ Object.keys(fileManifest.parser_counts || {}).join('、') || '暂无' }}</span>
  </div>
  <el-row :gutter="14">
    <el-col :span="7">
      <el-card header="文件目录" style="height:650px;overflow:auto">
        <div
          v-for="item in manifestEntries"
          :key="item.path"
          class="manifest-entry"
          :class="{ selected: item.path === selectedLogPath }"
          @click="selectManifestFile(item.path)"
        >
          <span class="mono">{{ item.path }}</span>
          <small class="muted">{{ item.line_count ? `${item.line_count} 行` : `${item.size || 0} B` }}</small>
        </div>
      </el-card>
    </el-col>
    <el-col :span="17">
      <el-card :header="selectedLogPath || '原始日志'" style="height:650px">
        <div v-if="selectedLogPath" class="toolbar" style="margin-bottom:8px">
          <el-button :disabled="rawStartLine <= 1" @click="previousRawPage">上一页</el-button>
          <el-button :disabled="!rawHasMore" @click="nextRawPage">下一页</el-button>
          <el-input-number v-model="rawJumpLine" :min="1" :max="rawTotalLines || undefined" controls-position="right" style="width:150px" />
          <el-button @click="jumpToRawLine">跳转</el-button>
          <span class="muted">第 {{ rawStartLine }}–{{ rawStartLine + Math.max(rawReturnedLines - 1, 0) }} 行 / {{ rawTotalLines || '未知总行数' }} · {{ rawEncoding }}</span>
        </div>
        <div v-if="selectedLogPath" class="toolbar" style="margin-bottom:8px">
          <el-input v-model="rawSearchQuery" clearable placeholder="搜索原始日志关键词" style="width:280px" @keyup.enter="searchRawLog(false)" />
          <el-button :loading="rawSearching" @click="searchRawLog(false)">搜索</el-button>
          <el-button v-if="rawSearchNextLine" :loading="rawSearching" @click="searchRawLog(true)">从第 {{ rawSearchNextLine }} 行继续</el-button>
          <span v-if="rawSearchScannedTo" class="muted">已扫描到第 {{ rawSearchScannedTo }} 行</span>
        </div>
        <div v-if="rawSearchMatches.length" class="search-results">
          <div v-for="match in rawSearchMatches" :key="match.line_number" class="mono search-result">
            <el-button link type="primary" @click="openRawSearchMatch(match.line_number)">第 {{ match.line_number }} 行</el-button>{{ match.text }}
          </div>
        </div>
        <div ref="rawViewport" class="raw-log-viewer" :class="{ compact: rawSearchMatches.length }">
          <div v-if="!visibleLines.length" class="empty-log">选择左侧文件查看，原始数据不会被 LLM 输出覆盖。</div>
          <div
            v-for="line in visibleLines"
            :key="line.number"
            :data-line-number="line.number"
            class="log-line"
            :class="{ target: line.number === targetLine }"
          >
            <span class="line-number">{{ line.number }}</span><span class="line-text">{{ line.text || ' ' }}</span>
          </div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.manifest-entry { padding: 5px 7px; cursor: pointer; border-radius: 4px; display: flex; gap: 6px; justify-content: space-between; }
.manifest-entry:hover, .manifest-entry.selected { background: #eff6ff; }
.search-results { max-height: 92px; overflow: auto; border: 1px solid #e5e7eb; padding: 4px 8px; margin-bottom: 8px; }
.search-result { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.raw-log-viewer { height: 460px; overflow: auto; background: #111827; color: #e5e7eb; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.raw-log-viewer.compact { height: 375px; }
.log-line { display: flex; min-height: 18px; line-height: 18px; }
.log-line.target { background: #854d0e; color: #fef3c7; outline: 1px solid #f59e0b; }
.line-number { flex: 0 0 72px; padding-right: 10px; color: #9ca3af; text-align: right; user-select: none; border-right: 1px solid #374151; }
.line-text { padding-left: 10px; white-space: pre; }
.empty-log { padding: 16px; color: #9ca3af; }
</style>
