import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../api/client'
import { failure } from './useWorkbench'
import type { AssistantSession, AssistantSummary } from '../types/workbench'

export function useKnowledgeAssistant(onPublished: () => void) {
  const history = ref<AssistantSummary[]>([]), session = ref<AssistantSession | null>(null)
  const selectedFiles = ref<File[]>([]), message = ref(''), mode = ref('auto'), consent = ref(true)
  const busy = ref(false), loading = ref(false), error = ref(''), historyError = ref('')
  const running = computed(() => ['READING', 'APPROVED', 'BUILDING'].includes(session.value?.status || ''))
  const changes = computed(() => session.value?.plan?.filter(item => item.action !== 'skip').length || 0)
  const coverage = computed(() => Object.entries(session.value?.coverage || {}))
  const complete = computed(() => coverage.value.length > 0 && coverage.value.every(([, item]) => item.complete)
    && (session.value?.files || []).every(file => session.value?.coverage[file.path]?.complete))
  const canConfirm = computed(() => session.value?.status === 'REVIEW' && session.value.mode !== 'answer'
    && session.value.model_egress_approved && changes.value > 0 && complete.value && !message.value.trim() && !busy.value && !loading.value)
  let timer: ReturnType<typeof setTimeout> | undefined
  let epoch = 0, disposed = false
  const published = new Set<string>()
  function stopPolling() { if (timer) clearTimeout(timer); timer = undefined }
  async function loadHistory() {
    historyError.value = ''
    try { history.value = (await api.get<AssistantSummary[]>('/workbench/assistant')).data }
    catch (cause) { historyError.value = failure(cause) }
  }
  function schedule(id: string, token: number) {
    stopPolling()
    if (!disposed && token === epoch && running.value) timer = setTimeout(() => void refresh(id, token), 2500)
  }
  function accept(value: AssistantSession) {
    session.value = value; consent.value = value.model_egress_approved
    const row = history.value.find(item => item.id === value.id)
    if (row) Object.assign(row, { title: value.title, status: value.status, version: value.version })
    if (value.status === 'PUBLISHED' && !published.has(value.id)) { published.add(value.id); onPublished() }
  }
  async function refresh(id = session.value?.id, token = epoch) {
    if (!id) return
    try {
      const { data } = await api.get<AssistantSession>(`/workbench/assistant/${id}`)
      if (disposed || token !== epoch) return
      accept(data); error.value = ''
    } catch (cause) { if (token === epoch && !disposed) error.value = failure(cause) }
    finally { schedule(id, token) }
  }
  async function open(id: string) {
    if (busy.value) return
    stopPolling(); const token = ++epoch
    session.value = null; loading.value = true; message.value = ''; selectedFiles.value = []; error.value = ''
    await refresh(id, token)
    if (token === epoch) loading.value = false
  }
  function startNew() {
    if (busy.value) return
    stopPolling(); ++epoch; session.value = null; selectedFiles.value = []; message.value = ''
    consent.value = true; mode.value = 'auto'; error.value = ''; loading.value = false
  }
  function selectFiles(event: Event) {
    const input = event.target as HTMLInputElement
    const files = Array.from(input.files || [])
    const paths = files.map(file => file.webkitRelativePath || file.name)
    const invalid = files.find(file => file.size > 1024 * 1024 || !/\.(md|markdown|txt|json|yaml|yml|py|c|h|cpp|ps1|sh|toml|ini|cfg|csv)$/i.test(file.name))
    if (files.length > 64 || files.reduce((size, file) => size + file.size, 0) > 8 * 1024 * 1024) error.value = '最多 64 个文件、总计 8 MiB。请精简目录后重新选择，文件不会被截断。'
    else if (invalid) error.value = `${invalid.name} 格式不支持或超过单文件 1 MiB 限制，请调整后重新选择。`
    else if (new Set(paths.map(path => path.toLocaleLowerCase())).size !== paths.length) error.value = '文件相对路径重复，请选择一个完整目录。'
    else { selectedFiles.value = files; error.value = ''; return }
    selectedFiles.value = []; input.value = ''
  }
  async function mutate(action: () => Promise<{ data: AssistantSession }>) {
    if (busy.value || loading.value) return
    busy.value = true; error.value = ''; stopPolling(); const token = ++epoch
    try {
      const { data } = await action()
      if (disposed || token !== epoch) return
      accept(data); await loadHistory(); schedule(data.id, token)
      return data
    } catch (cause: any) {
      const detail = failure(cause)
      // Never replay a stale approval. Refresh the plan for another deliberate review.
      if (cause?.response?.status === 409 && session.value) await refresh(session.value.id, token)
      if (token === epoch && !disposed) error.value = detail
    } finally { busy.value = false; if (session.value) schedule(session.value.id, token) }
  }
  async function send() {
    if (!message.value.trim() && (session.value || !selectedFiles.value.length)) { error.value = '请输入问题、修改要求，或选择资料。'; return }
    const result = await mutate(() => {
      if (session.value) return api.post(`/workbench/assistant/${session.value.id}/messages`, {
        version: session.value.version, message: message.value.trim(), mode: mode.value,
        model_egress_approved: consent.value
      })
      const data = new FormData()
      selectedFiles.value.forEach(file => data.append('files', file))
      data.append('paths', JSON.stringify(selectedFiles.value.map(file => file.webkitRelativePath || file.name)))
      data.append('message', message.value.trim() || '请完整阅读所有文件与依赖，按问题类别和用途整理，与已有知识比较，提出具体变更清单。')
      data.append('model_egress_approved', String(consent.value)); data.append('mode', mode.value)
      return api.post('/workbench/assistant', data)
    })
    if (result) { message.value = ''; selectedFiles.value = [] }
  }
  async function confirm() {
    if (!canConfirm.value || !session.value) return
    await mutate(() => api.post(`/workbench/assistant/${session.value!.id}/confirm`, {
      version: session.value!.version,
      ...(session.value!.review_digest ? { review_digest: session.value!.review_digest } : {})
    }))
  }
  async function control(action: 'pause' | 'cancel' | 'retry') {
    if (!session.value) return
    await mutate(() => api.post(`/workbench/assistant/${session.value!.id}/${action}`, { version: session.value!.version }))
  }
  async function setConsent(value: boolean) {
    if (!session.value) { consent.value = value; return }
    await mutate(() => api.patch(`/workbench/assistant/${session.value!.id}/consent`, {
      version: session.value!.version, model_egress_approved: value
    }))
  }
  onBeforeUnmount(() => { disposed = true; ++epoch; stopPolling() })
  return { history, session, selectedFiles, message, mode, consent, busy, loading, error, historyError,
    running, changes, coverage, complete, canConfirm, loadHistory, open, refresh, startNew, selectFiles, send, confirm, control, setConsent }
}
