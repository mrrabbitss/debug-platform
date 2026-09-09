<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const ready = ref(false)
const enabled = ref(false)
const signedIn = ref(false)
const busy = ref(false)
const administrator = ref(false)
const code = ref('')
const adminToken = ref('')
const identity = ref('')
const error = ref('')
const apiBase = import.meta.env.VITE_API_BASE || '/api/v1'

async function request(path: string, method = 'GET', body?: unknown, token?: string) {
  const response = await fetch(`${apiBase}${path}`, {
    method, headers: { 'Content-Type': 'application/json', ...(token ? { 'X-API-Key': token } : {}) },
    body: body ? JSON.stringify(body) : undefined, cache: 'no-store'
  })
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '登录失败，请检查识别码')
  return value
}

async function acceptToken(token: string) {
  const me = await request('/system/me', 'GET', undefined, token)
  localStorage.setItem('gw_ap_api_key', token)
  identity.value = me.display_name || me.username || '管理员'
  signedIn.value = true
  error.value = ''
}

async function login() {
  error.value = ''
  if (!administrator.value && !/^[a-z][0-9]{8}$/.test(code.value)) {
    error.value = '请输入一位小写字母加八位数字，例如 a12345678'
    return
  }
  busy.value = true
  try {
    const token = administrator.value ? adminToken.value.trim() :
      (await request('/auth/engineer-login', 'POST', { personal_code: code.value })).token
    await acceptToken(token)
    adminToken.value = ''
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '无法连接服务器' }
  finally { busy.value = false }
}

function signOut() {
  localStorage.removeItem('gw_ap_api_key')
  signedIn.value = false
  identity.value = ''
  code.value = ''
}
function expired() { if (enabled.value) signOut() }

async function initialize() {
  busy.value = true
  error.value = ''
  try {
    const info = await request('/system/auth-info')
    enabled.value = Boolean(info.simple_engineer_login)
    if (enabled.value) {
      const ticket = new URLSearchParams(location.hash.slice(1)).get('gwap-login')
      if (ticket) {
        history.replaceState(null, '', location.pathname + location.search)
        await acceptToken((await request('/auth/browser-login', 'POST', { ticket })).token)
      } else {
        const saved = localStorage.getItem('gw_ap_api_key')
        if (saved) {
          try { await acceptToken(saved) } catch { signOut() }
        }
      }
    }
    ready.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '无法连接服务器'
    if (enabled.value) ready.value = true
  } finally { busy.value = false }
}
onMounted(() => { window.addEventListener('gwap-auth-expired', expired); void initialize() })
onUnmounted(() => window.removeEventListener('gwap-auth-expired', expired))
</script>

<template>
  <template v-if="ready && (!enabled || signedIn)">
    <div v-if="enabled" class="identity-bar">{{ identity }} <button @click="signOut">切换账号</button></div>
    <slot />
  </template>
  <main v-else class="login-page">
    <form class="login-card" @submit.prevent="login">
      <h1>GW/AP 诊断平台</h1>
      <p>{{ administrator ? '管理员登录。初始令牌只在首次安装时生成；如已过期，请停止服务器后在本机运行“恢复管理员访问”。' : '输入个人识别码即可进入，首次使用自动开通工程师账号。' }}</p>
      <template v-if="ready">
        <label v-if="!administrator">个人识别码
          <input v-model="code" maxlength="9" placeholder="例如 a12345678" autocomplete="off" autocapitalize="none" spellcheck="false" autofocus />
        </label>
        <label v-else>管理员令牌<input v-model="adminToken" type="password" autocomplete="off" /></label>
        <p v-if="!administrator" class="hint">一位小写字母＋八位数字。请始终使用自己的识别码。</p>
      </template>
      <p v-if="error" class="error">{{ error }}</p>
      <button v-if="ready" class="submit" type="submit" :disabled="busy">{{ busy ? '正在登录…' : '进入平台' }}</button>
      <button v-else class="submit" type="button" :disabled="busy" @click="initialize">{{ busy ? '正在连接…' : '重新连接' }}</button>
      <button v-if="ready" class="switch" type="button" @click="administrator = !administrator; error = ''">{{ administrator ? '返回工程师登录' : '管理员入口' }}</button>
    </form>
  </main>
</template>

<style scoped>
.login-page{min-height:100vh;display:grid;place-items:center;background:#f3f6fa;padding:24px;box-sizing:border-box}
.login-card{width:min(100%,400px);padding:32px;border-radius:16px;background:#fff;box-shadow:0 10px 40px #20304012}
h1{font-size:24px;color:#203044;margin:0 0 18px}p{line-height:1.7;color:#526173}label{display:block;margin-top:24px;font-size:14px}
input{box-sizing:border-box;width:100%;margin-top:10px;padding:12px;border:1px solid #cdd6e1;border-radius:8px;font-size:18px}
.hint{font-size:12px}.error{color:#b42318;font-size:14px}button{cursor:pointer}.submit{width:100%;padding:12px;border:0;border-radius:8px;background:#245dd8;color:white;font-size:16px}
.submit:disabled{opacity:.6}.switch{display:block;margin:18px auto 0;border:0;background:none;color:#526173}.identity-bar{position:fixed;right:24px;bottom:12px;z-index:1000;background:white;padding:8px 12px;border:1px solid #dbe3eb;border-radius:8px;font-size:12px}.identity-bar button{margin-left:12px;border:0;background:none;color:#245dd8}
</style>
