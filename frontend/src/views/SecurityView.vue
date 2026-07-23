<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import type {
  AccessTokenInfo,
  AuditEvent,
  Principal,
  UserAccount,
  UserRole
} from '../types'

interface AuthInfo {
  mode: 'local' | 'api_key' | 'rbac'
  token_header: string
  legacy_admin_enabled: boolean
}

const authInfo = ref<AuthInfo | null>(null)
const credential = ref(localStorage.getItem('gw_ap_api_key') || '')
const principal = ref<Principal | null>(null)
const identityError = ref('')
const loading = ref(false)
const users = ref<UserAccount[]>([])
const auditEvents = ref<AuditEvent[]>([])
const systemStatus = ref<any>(null)
const userDialogVisible = ref(false)
const tokenDialogVisible = ref(false)
const revealedDialogVisible = ref(false)
const selectedUser = ref<UserAccount | null>(null)
const userTokens = ref<AccessTokenInfo[]>([])
const revealedToken = ref('')
const revealedFor = ref('')
const auditFilter = reactive({ action: '', outcome: '' })
const userForm = reactive({
  username: '',
  display_name: '',
  role: 'VIEWER' as UserRole,
  issue_token: true,
  token_name: 'initial',
  token_expires_days: 90
})
const tokenForm = reactive({ name: 'replacement', expires_days: 90 })

const isAdmin = computed(() => principal.value?.role === 'ADMIN')

function errorMessage(error: any, fallback: string) {
  return error?.response?.data?.detail || error?.message || fallback
}

async function loadAuthInfo() {
  authInfo.value = (await api.get('/system/auth-info')).data
}

async function loadIdentity(showSuccess = false) {
  identityError.value = ''
  loading.value = true
  try {
    principal.value = (await api.get('/system/me')).data
    if (isAdmin.value) await Promise.all([loadUsers(), loadAudit(), loadSystemStatus()])
    else {
      users.value = []
      auditEvents.value = []
      systemStatus.value = null
    }
    if (showSuccess) ElMessage.success('凭据验证成功')
  } catch (error: any) {
    principal.value = null
    users.value = []
    auditEvents.value = []
    systemStatus.value = null
    identityError.value = errorMessage(error, '凭据验证失败')
    if (showSuccess) ElMessage.error(identityError.value)
  } finally {
    loading.value = false
  }
}

async function saveCredential() {
  const value = credential.value.trim()
  if (value) localStorage.setItem('gw_ap_api_key', value)
  else localStorage.removeItem('gw_ap_api_key')
  await loadIdentity(true)
}

async function clearCredential() {
  credential.value = ''
  localStorage.removeItem('gw_ap_api_key')
  await loadIdentity(false)
  ElMessage.success('浏览器中保存的凭据已清除')
}

async function loadUsers() {
  users.value = (await api.get('/system/users')).data
}

async function createUser() {
  if (!userForm.username.trim() || !userForm.display_name.trim()) {
    return ElMessage.warning('请填写用户名和显示名称')
  }
  try {
    const { data } = await api.post('/system/users', userForm)
    userDialogVisible.value = false
    await loadUsers()
    if (data.raw_token) showRevealedToken(data.raw_token, data.user.username)
    else ElMessage.success('用户已创建')
    Object.assign(userForm, {
      username: '', display_name: '', role: 'VIEWER', issue_token: true,
      token_name: 'initial', token_expires_days: 90
    })
  } catch (error: any) {
    ElMessage.error(errorMessage(error, '创建用户失败'))
  }
}

async function updateUser(user: UserAccount, changes: Partial<Pick<UserAccount, 'role' | 'active'>>) {
  try {
    await api.patch(`/system/users/${user.id}`, changes)
    ElMessage.success('用户已更新')
    await loadUsers()
  } catch (error: any) {
    ElMessage.error(errorMessage(error, '更新用户失败'))
    await loadUsers()
  }
}

async function openTokens(user: UserAccount) {
  selectedUser.value = user
  tokenDialogVisible.value = true
  await loadTokens()
}

async function loadTokens() {
  if (!selectedUser.value) return
  userTokens.value = (await api.get(`/system/users/${selectedUser.value.id}/tokens`)).data
}

async function issueToken() {
  if (!selectedUser.value) return
  try {
    const { data } = await api.post(
      `/system/users/${selectedUser.value.id}/tokens`,
      tokenForm
    )
    await loadTokens()
    showRevealedToken(data.raw_token, selectedUser.value.username)
  } catch (error: any) {
    ElMessage.error(errorMessage(error, '签发令牌失败'))
  }
}

async function revokeToken(token: AccessTokenInfo) {
  if (!selectedUser.value) return
  try {
    await ElMessageBox.confirm(`确认撤销令牌 ${token.token_hint}？`, '撤销访问令牌', {
      type: 'warning'
    })
    await api.delete(`/system/users/${selectedUser.value.id}/tokens/${token.id}`)
    ElMessage.success('令牌已撤销')
    await loadTokens()
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error, '撤销令牌失败'))
  }
}

function showRevealedToken(token: string, username: string) {
  revealedToken.value = token
  revealedFor.value = username
  revealedDialogVisible.value = true
}

function clearRevealedToken() {
  revealedToken.value = ''
  revealedFor.value = ''
}

async function copyRevealedToken() {
  try {
    await navigator.clipboard.writeText(revealedToken.value)
    ElMessage.success('令牌已复制')
  } catch {
    ElMessage.warning('自动复制失败，请手动选中复制')
  }
}

async function loadAudit() {
  const params: Record<string, string | number> = { limit: 300 }
  if (auditFilter.action.trim()) params.action = auditFilter.action.trim()
  if (auditFilter.outcome) params.outcome = auditFilter.outcome
  auditEvents.value = (await api.get('/system/audit', { params })).data
}

async function loadSystemStatus() {
  systemStatus.value = (await api.get('/system/status')).data
}

function formatBytes(value?: number | null) {
  if (value === undefined || value === null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 2)} ${units[index]}`
}

function formatTime(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

onMounted(async () => {
  try {
    await loadAuthInfo()
    await loadIdentity(false)
  } catch (error: any) {
    identityError.value = errorMessage(error, '无法读取鉴权配置')
  }
})
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin-right:auto">安全与审计</h1>
      <el-tag v-if="authInfo" effect="plain">模式：{{ authInfo.mode }}</el-tag>
      <el-button @click="loadIdentity(false)">刷新身份</el-button>
    </div>

    <el-card style="margin-bottom:16px" v-loading="loading">
      <template #header>当前浏览器凭据</template>
      <el-alert
        v-if="authInfo?.mode === 'local'"
        type="info"
        :closable="false"
        title="当前是本机开发模式；切换到 rbac 后需要粘贴个人令牌。"
        style="margin-bottom:14px"
      />
      <el-alert
        v-if="identityError"
        type="warning"
        :closable="false"
        :title="identityError"
        style="margin-bottom:14px"
      />
      <div class="toolbar">
        <el-input
          v-model="credential"
          type="password"
          show-password
          autocomplete="off"
          placeholder="API Key 或 gwdp_ 开头的个人令牌"
          style="max-width:620px"
          @keyup.enter="saveCredential"
        />
        <el-button type="primary" @click="saveCredential">保存并验证</el-button>
        <el-button @click="clearCredential">清除</el-button>
      </div>
      <el-descriptions v-if="principal" :column="3" border>
        <el-descriptions-item label="身份">{{ principal.display_name || principal.username || principal.id }}</el-descriptions-item>
        <el-descriptions-item label="角色"><el-tag>{{ principal.role }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="凭据类型">{{ principal.type }}</el-descriptions-item>
      </el-descriptions>
      <p class="muted">凭据只保存在当前浏览器的 localStorage，并通过 {{ authInfo?.token_header || 'X-API-Key' }} 请求头发送；服务端只保存个人令牌的 SHA-256 摘要。</p>
    </el-card>

    <template v-if="isAdmin">
      <el-card v-if="systemStatus" style="margin-bottom:16px">
        <template #header>
          <div class="toolbar" style="margin-bottom:0">
            <span style="margin-right:auto">运行状态</span>
            <el-tag :type="systemStatus.status === 'ready' ? 'success' : 'danger'">{{ systemStatus.status }}</el-tag>
            <el-button @click="loadSystemStatus">刷新</el-button>
          </div>
        </template>
        <div class="card-grid">
          <div class="stat-card"><div class="muted">数据库</div><strong>{{ systemStatus.database?.dialect }}</strong><div>{{ systemStatus.checks?.database?.ok ? '连接正常' : '连接异常' }}</div></div>
          <div class="stat-card"><div class="muted">存储空间</div><strong>{{ formatBytes(systemStatus.storage?.free_bytes) }}</strong><div>已登记日志 {{ formatBytes(systemStatus.storage?.artifact_bytes) }}</div></div>
          <div class="stat-card"><div class="muted">后台任务</div><strong>{{ Object.values(systemStatus.jobs?.counts || {}).reduce((sum: number, value: any) => sum + Number(value), 0) }}</strong><div>{{ systemStatus.jobs?.configured_workers }} 个工作线程</div></div>
          <div class="stat-card"><div class="muted">数据对象</div><strong>{{ systemStatus.entities?.cases || 0 }} 个案例</strong><div>{{ systemStatus.entities?.artifacts || 0 }} 个日志，{{ systemStatus.entities?.knowledge_documents || 0 }} 篇知识</div></div>
        </div>
      </el-card>

      <el-card style="margin-bottom:16px">
        <template #header>
          <div class="toolbar" style="margin-bottom:0">
            <span style="margin-right:auto">用户与角色</span>
            <el-button type="primary" @click="userDialogVisible = true">新建用户</el-button>
            <el-button @click="loadUsers">刷新</el-button>
          </div>
        </template>
        <el-table :data="users">
          <el-table-column prop="username" label="用户名" min-width="150" />
          <el-table-column prop="display_name" label="显示名称" min-width="170" />
          <el-table-column label="角色" width="170">
            <template #default="scope">
              <el-select
                v-model="scope.row.role"
                :disabled="scope.row.id === principal?.id"
                @change="(value: UserRole) => updateUser(scope.row as UserAccount, { role: value })"
              >
                <el-option label="管理员" value="ADMIN" />
                <el-option label="工程师" value="ENGINEER" />
                <el-option label="只读用户" value="VIEWER" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="100">
            <template #default="scope">
              <el-switch
                v-model="scope.row.active"
                :disabled="scope.row.id === principal?.id"
                @change="(value: string | number | boolean) => updateUser(scope.row as UserAccount, { active: Boolean(value) })"
              />
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="190">
            <template #default="scope">{{ formatTime(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="scope"><el-button link type="primary" @click="openTokens(scope.row as UserAccount)">管理令牌</el-button></template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card>
        <template #header>
          <div class="toolbar" style="margin-bottom:0">
            <span style="margin-right:auto">审计记录</span>
            <el-input v-model="auditFilter.action" clearable placeholder="动作精确匹配" style="width:220px" />
            <el-select v-model="auditFilter.outcome" clearable placeholder="结果" style="width:130px">
              <el-option label="成功" value="SUCCESS" />
              <el-option label="拒绝" value="DENIED" />
              <el-option label="失败" value="FAILED" />
            </el-select>
            <el-button @click="loadAudit">筛选/刷新</el-button>
          </div>
        </template>
        <el-table :data="auditEvents" height="520">
          <el-table-column prop="created_at" label="时间" width="190">
            <template #default="scope">{{ formatTime(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="actor_id" label="操作者" width="180" show-overflow-tooltip />
          <el-table-column prop="action" label="动作" min-width="210" show-overflow-tooltip />
          <el-table-column prop="outcome" label="结果" width="100" />
          <el-table-column prop="resource_type" label="资源" width="120" />
          <el-table-column prop="resource_id" label="资源 ID" width="180" show-overflow-tooltip />
          <el-table-column label="脱敏详情" min-width="260" show-overflow-tooltip>
            <template #default="scope">{{ JSON.stringify(scope.row.details) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <el-empty v-else-if="principal" description="当前角色可查看身份，但用户和审计管理仅对管理员开放。" />

    <el-dialog v-model="userDialogVisible" title="新建用户" width="560px">
      <el-form label-width="110px">
        <el-form-item label="用户名"><el-input v-model="userForm.username" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="userForm.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role" class="full-width">
            <el-option label="管理员" value="ADMIN" />
            <el-option label="工程师" value="ENGINEER" />
            <el-option label="只读用户" value="VIEWER" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始令牌"><el-checkbox v-model="userForm.issue_token">立即签发</el-checkbox></el-form-item>
        <template v-if="userForm.issue_token">
          <el-form-item label="令牌名称"><el-input v-model="userForm.token_name" /></el-form-item>
          <el-form-item label="有效天数"><el-input-number v-model="userForm.token_expires_days" :min="1" :max="3650" /></el-form-item>
        </template>
      </el-form>
      <template #footer><el-button @click="userDialogVisible=false">取消</el-button><el-button type="primary" @click="createUser">创建</el-button></template>
    </el-dialog>

    <el-dialog v-model="tokenDialogVisible" :title="`管理令牌：${selectedUser?.username || ''}`" width="780px">
      <div class="toolbar">
        <el-input v-model="tokenForm.name" placeholder="令牌名称" style="width:220px" />
        <span>有效天数</span>
        <el-input-number v-model="tokenForm.expires_days" :min="1" :max="3650" />
        <el-button type="primary" @click="issueToken">签发新令牌</el-button>
      </div>
      <el-table :data="userTokens">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="token_hint" label="标识" width="150" />
        <el-table-column label="到期时间" width="180"><template #default="scope">{{ formatTime(scope.row.expires_at) }}</template></el-table-column>
        <el-table-column label="最近使用" width="180"><template #default="scope">{{ formatTime(scope.row.last_used_at) }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="scope.row.revoked_at ? 'info' : 'success'">{{ scope.row.revoked_at ? '已撤销' : '有效' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="90"><template #default="scope"><el-button v-if="!scope.row.revoked_at" link type="danger" :disabled="scope.row.id === principal?.token_id" @click="revokeToken(scope.row as AccessTokenInfo)">撤销</el-button></template></el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="revealedDialogVisible" title="一次性访问令牌" width="720px" :close-on-click-modal="false" @closed="clearRevealedToken">
      <el-alert type="warning" :closable="false" :title="`${revealedFor} 的令牌只显示这一次，关闭前请复制并安全保存。`" />
      <el-input v-model="revealedToken" readonly type="textarea" :rows="4" class="mono" style="margin-top:14px" />
      <template #footer><el-button type="primary" @click="copyRevealedToken">复制令牌</el-button><el-button @click="revealedDialogVisible=false">我已保存</el-button></template>
    </el-dialog>
  </div>
</template>
