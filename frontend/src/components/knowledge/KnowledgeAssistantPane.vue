<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useKnowledgeAssistant } from '../../composables/useKnowledgeAssistant'
import type { AssistantOperation } from '../../types/workbench'
import AssistantSourceDialog from './AssistantSourceDialog.vue'
const props = defineProps<{ categories: { id: string; name: string }[]; roles: Record<string, string> }>()
const emit = defineEmits<{ published: [] }>()
const assistant = useKnowledgeAssistant(() => emit('published'))
const { history, session, selectedFiles, message, mode, consent, busy, loading, error, historyError,
  running, changes, coverage, complete, canConfirm, loadHistory, open, refresh, startNew, selectFiles,
  send, confirm, control, setConsent } = assistant
const operation = ref<AssistantOperation | null>(null), sourcePath = ref('')
const actionName = (value: string) => ({ create:'新增', merge:'合并', replace:'替换', link:'关联', skip:'跳过 / 保留' } as Record<string, string>)[value] || value
const categoryName = (id: string) => props.categories.find(item => item.id === id)?.name || id
const contentKindName = (value?: string) => value === 'SKILL' ? '诊断 Skill' : value === 'KNOWLEDGE' ? '普通知识' : '类型待核对'
const statusName = (value: string) => ({ READING:'正在阅读与整理', REVIEW:'等待核对', APPROVED:'已审批，等待发布', BUILDING:'已审批，正在构建索引', PUBLISHED:'已发布', PUBLISH_FAILED:'已审批，发布失败', FAILED:'整理失败', CANCELLED:'已取消', PAUSED:'已暂停' } as Record<string, string>)[value] || value
watch(() => session.value?.version, () => { operation.value = null })
watch(() => session.value?.id, () => { sourcePath.value = ''; mode.value = 'auto' })
onMounted(loadHistory)
</script>

<template>
  <div class="assistant-layout">
    <aside class="assistant-history" aria-label="整理会话">
      <el-button :disabled="busy" @click="startNew" class="full-width">＋ 新建整理会话</el-button>
      <div class="collection-heading"><span class="field-hint">最近会话</span><el-button link :disabled="busy" @click="loadHistory">刷新列表</el-button></div>
      <el-alert v-if="historyError" :title="historyError" type="error" :closable="false" />
      <p v-else-if="!history.length" class="field-hint">会话会保存在服务器，可随时回来接着整理。</p>
      <button v-for="item in history" :key="item.id" class="history-item" :class="{active:session?.id===item.id}" :aria-pressed="session?.id===item.id" :disabled="busy" @click="open(item.id)">{{ item.title }}<small>{{ statusName(item.status) }} · {{ new Date(item.created_at).toLocaleDateString() }}</small></button>
    </aside>
    <section class="assistant-main" aria-label="知识整理助手">
      <el-alert v-if="error" :title="error" type="error" :closable="false" class="inline-alert"><el-button v-if="session" link :disabled="busy" @click="refresh()">刷新当前会话</el-button></el-alert>
      <el-skeleton v-if="loading" :rows="6" animated />
      <template v-else>
        <div v-if="!session" class="assistant-welcome"><h2>查找知识，或整理一份 Skill</h2><p class="muted">选择整个文件夹，阅读全部资料与依赖，核对具体改动后发布。</p>
          <div class="folder-picker"><label class="file-choice">选择文件夹<input type="file" aria-label="选择 Skill 文件夹" webkitdirectory multiple :disabled="busy" @change="selectFiles" /></label><label class="file-choice">选择文件<input type="file" aria-label="选择知识文件" multiple accept=".md,.markdown,.txt,.json,.yaml,.yml,.py,.c,.h,.cpp,.ps1,.sh,.toml,.ini,.cfg,.csv" :disabled="busy" @change="selectFiles" /></label></div>
          <p class="field-hint">支持 UTF-8 文本与脚本；最多 64 个文件，每个 1 MiB，总计 8 MiB。保留目录相对路径。</p>
          <details v-if="selectedFiles.length" class="technical-details" open><summary>已选择 {{ selectedFiles.length }} 个文件</summary><ul class="path-list"><li v-for="file in selectedFiles" :key="file.webkitRelativePath || file.name">{{ file.webkitRelativePath || file.name }}</li></ul></details>
        </div>
        <template v-if="session">
          <div class="workspace-heading"><h3>{{ session.title }}</h3><el-tag :type="session.status==='PUBLISHED'?'success':session.status==='FAILED'?'danger':'info'">{{ statusName(session.status) }}</el-tag></div>
          <div class="toolbar">
            <el-button :disabled="busy" @click="refresh()">刷新进度</el-button>
            <template v-if="running"><el-button :disabled="busy" @click="control('pause')">暂停</el-button><el-button :disabled="busy" type="warning" plain @click="control('cancel')">取消本次任务</el-button></template>
            <el-button v-if="['FAILED','CANCELLED','PAUSED'].includes(session.status)" :disabled="busy||!consent" type="primary" plain @click="control('retry')">{{ session.status==='FAILED'?'重试整理':'继续整理' }}</el-button>
            <el-button v-if="session.status==='PUBLISH_FAILED'" :disabled="busy || !!message.trim()" type="primary" @click="control('retry')">重试已审批的发布</el-button>
          </div>
          <el-alert v-if="session.error || session.job?.error_message" type="warning" :closable="false" :title="session.error || session.job?.error_message || ''" class="inline-alert" />
          <el-alert v-if="session.status==='PAUSED'" type="info" :closable="false" title="会话与阅读进度已保存。开启模型授权后，点击继续整理。" class="inline-alert" />
          <el-alert v-if="session.status==='PUBLISH_FAILED'" type="warning" :closable="false" title="精确版本的审批已保留，重试会恢复同一次发布，无需再次确认。发送内容修改要求后，需要核对新的变更清单。" class="inline-alert" />
          <p v-if="running" class="field-hint">{{ session.job?.message || '任务已保存，正在准备处理资料' }}</p>
          <el-progress v-if="running" :percentage="Math.max(0,Math.min(100,session.job?.progress || 0))" />
          <div v-for="(item,index) in session.messages" :key="index" class="conversation-message" :class="{assistant:item.role==='assistant'}"><span class="message-author">{{ item.role==='user'?'你的要求':'整理助手' }}</span>{{ item.content }}</div>
          <div v-if="session.answer" class="conversation-message assistant"><span class="message-author">整理助手</span>{{ session.answer }}</div>
          <div v-if="coverage.length" class="coverage-summary"><el-tag :type="complete?'success':'warning'">{{ complete?'全文已读完':'全文阅读未完成' }}</el-tag><span>{{ coverage.filter(([,item])=>item.complete).length }} / {{ coverage.length }} 份资料已完整读取</span></div>
          <details v-if="coverage.length" class="technical-details" open><summary>全文阅读与来源</summary><ul class="path-list"><li v-for="([path,item]) in coverage" :key="path"><el-button link @click="sourcePath=path">{{ path }}</el-button>：{{ item.read }} / {{ item.total }} 段 · {{ item.complete?'已读完':'尚未读完' }}</li></ul></details>
          <details v-if="session.bundle_manifest?.length" class="technical-details"><summary>文件依赖与关联</summary><ul class="path-list"><li v-for="(item,index) in session.bundle_manifest" :key="index"><strong>{{ item.path }}</strong><ul v-if="item.references?.length"><li v-for="ref in item.references" :key="ref.reference">{{ ref.reference }} → {{ ref.external?'外部引用':ref.path }}</li></ul><span v-else> · 无文件依赖</span></li></ul></details>
          <template v-if="session.plan?.length">
            <div class="collection-heading"><h2>拟执行的变更</h2><span class="muted">{{ session.plan.length }} 项操作 · 按顺序执行</span></div>
            <el-table :data="session.plan" class="assistant-plan">
              <el-table-column type="index" label="#" width="45" />
              <el-table-column label="文档与目标版本" min-width="190"><template #default="{row}"><strong>{{ row.title || row.source_paths?.[0] || '来源资料' }}</strong><div class="operation-target">{{ row.target_id ? '已有文档 · v'+row.expected_version : row.action==='skip'?'保留来源':'新文档' }}</div><div class="field-hint">{{ row.source_paths?.join('、') }}</div></template></el-table-column>
              <el-table-column label="类型 / 分类 / 用途" min-width="170"><template #default="{row}"><el-tag size="small" effect="plain">{{ contentKindName(row.content_kind) }}</el-tag><div>{{ row.categories.map(categoryName).join('、') }}</div><div class="field-hint">{{ roles[row.role] }}</div></template></el-table-column>
              <el-table-column label="操作" width="100"><template #default="{row}"><el-tag effect="plain" size="small">{{ actionName(row.action) }}</el-tag></template></el-table-column>
              <el-table-column prop="reason" label="理由" min-width="180" />
              <el-table-column width="100"><template #default="{row}"><el-button link type="primary" @click="operation=row as AssistantOperation">查看差异</el-button></template></el-table-column>
            </el-table>
          </template>
          <div v-if="session.status==='REVIEW' && changes" class="confirm-panel"><p>将执行 {{ changes }} 项变更。确认即发布审批，索引构建成功后向所有用户生效。</p><p v-if="!complete">还有资料未完整读取，暂不能发布。</p><p v-if="message.trim()">当前输入尚未发送，请先发送纠偏要求，或清空输入后核对方案。</p><el-button type="primary" :disabled="!canConfirm" :loading="busy" @click="confirm">确认并发布 {{ changes }} 项变更</el-button></div>
          <el-alert v-if="session.status==='PUBLISHED'" title="知识已发布，新诊断将使用更新后的版本。" type="success" :closable="false" class="inline-alert" />
          <el-alert v-else-if="session.status==='REVIEW'&&!changes" title="本轮没有需要发布的改动，可以继续提问或调整要求。" type="info" :closable="false" />
        </template>
        <div v-if="session?.status!=='PUBLISHED'" class="assistant-composer">
          <div class="toolbar"><el-radio-group v-model="mode" aria-label="整理方式" size="small" :disabled="busy"><el-radio-button value="auto">自动判断</el-radio-button><el-radio-button value="answer">查找问答</el-radio-button><el-radio-button value="edit">修改知识</el-radio-button></el-radio-group></div>
          <el-input v-model="message" aria-label="知识助手要求" type="textarea" :rows="4" :maxlength="12000" :disabled="busy" placeholder="例如：这份属于连接问题；只替换第三节，保留旧故障树，新增一个分支。也可以直接查找或修改已有知识。" />
          <div class="consent-panel"><el-switch :model-value="consent" aria-label="知识助手模型授权" :disabled="busy" @change="value=>setConsent(value===true)" active-text="允许模型分析" /><p>允许将资料和知识发送到系统预设模型。关闭会暂停模型处理，已保存的会话可以稍后继续。</p></div>
          <el-button type="primary" :loading="busy" @click="send">{{ !session ? (consent?'开始整理':'保存会话，暂不分析') : running ? '更新要求并重新整理' : '发送纠偏或问题' }}</el-button>
        </div>
      </template>
    </section>
    <el-dialog :model-value="!!operation" @close="operation=null" title="核对变更" width="1050px">
      <template v-if="operation">
        <div class="operation-detail-header"><el-tag>{{ actionName(operation.action) }}</el-tag><strong>{{ operation.title }}</strong><span>{{ operation.target_id ? '目标当前版本 v'+operation.expected_version : '无已有目标' }}</span></div>
        <p>{{ operation.reason }}</p><p class="field-hint">归位：{{ contentKindName(operation.content_kind) }} · {{ operation.categories.map(categoryName).join(' / ') }} · {{ roles[operation.role] }}</p>
        <details class="technical-details"><summary>精确目标与来源章节</summary><p class="field-hint">{{ operation.target_id || '新增文档' }}<span v-if="operation.target_id"> · 文档 v{{ operation.expected_version }} · 锁版本 {{ operation.expected_lock }}</span></p><ul class="path-list"><li v-for="(source,index) in operation.sources" :key="index">{{ source.path }} · 字符 {{ source.start + 1 }}–{{ source.end }}</li></ul></details>
        <el-tabs><el-tab-pane label="差异"><pre class="document-text diff-text"><span v-for="(line,index) in (operation.diff||'正文不变；请核对分类、用途和关联。').split('\n')" :key="index" :class="['diff-line',{'diff-add':line.startsWith('+'),'diff-remove':line.startsWith('-')} ]">{{ line }}</span></pre></el-tab-pane><el-tab-pane label="拟发布全文"><pre class="document-text">{{ operation.after || '本操作不产生新正文' }}</pre></el-tab-pane><el-tab-pane label="原文"><pre class="document-text">{{ operation.before || '新增文档，无已有正文' }}</pre></el-tab-pane></el-tabs>
      </template>
      <template #footer><el-button @click="operation=null">返回变更清单</el-button></template>
    </el-dialog>
    <AssistantSourceDialog v-if="session&&sourcePath" :session-id="session.id" :path="sourcePath" @close="sourcePath=''" />
  </div>
</template>
