import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const captures = resolve(import.meta.dirname, '../../artifacts/validation/model-progress-20260910')
const principal = { id: 'progress-owner', username: 'progress', display_name: '进度验证', role: 'ENGINEER', type: 'user_token' }
const profile = { id: 'MODEL-progress', name: '合成慢响应模型', task_type: 'chat', mode: 'api', provider: 'openai_compatible',
  model_name: 'synthetic', base_url: 'https://model.invalid/v1', enabled: true, is_active: false, visibility: 'PRIVATE',
  owner_id: principal.id, can_manage: true, can_use: true, config: { timeout_seconds: 900 } }

async function fixture(page: Page, kind: 'analyze_case' | 'test_chat_model_connection') {
  const stamp = new Date().toISOString()
  const state = { submitted: 0, reads: 0, offline: false, unexpected: [] as string[], errors: [] as string[], job: {
    id: 'JOB-progress', kind, status: 'RUNNING', progress: 35, message: '正在完整阅读第 2/6 份 Skill，第 3 段',
    result_json: '{}', error_message: '', attempt: 1, max_attempts: 1, created_at: stamp, started_at: stamp,
    heartbeat_at: stamp, timeout_seconds: 7200,
    progress_detail: { stage: kind === 'analyze_case' ? '完整阅读 Skill' : '测试模型响应', stage_index: kind === 'analyze_case' ? 3 : 2,
      stage_count: kind === 'analyze_case' ? 6 : 4, completed_percent: 35, completed_units: 3, total_units: 9, unit: '段',
      waiting_for_model: true, stage_started_at: stamp, model_started_at: new Date(Date.now() - 120_000).toISOString(), updated_at: stamp }
  } }
  page.on('pageerror', error => state.errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', ''), method = request.method()
    const ok = (body: unknown) => route.fulfill({ json: body })
    if (path === '/system/auth-info') return ok({ simple_engineer_login: false })
    if (path === '/system/me') return ok(principal)
    if (path === '/workbench/bootstrap') return ok({ principal, preferences: { chat_profile_id: profile.id },
      model_selection: { profile_id: profile.id }, models: [{ ...profile, active: false }],
      categories: [{ id: 'network', name: '组网问题' }, { id: 'unknown', name: '未知' }], knowledge_roles: {} })
    if (path === '/system/models') return ok([profile])
    if (path === '/system/user-directory' || path.endsWith('/members')) return ok([])
    if (path === '/system/models/MODEL-progress/test-jobs' || (path.endsWith('/analyses') && method === 'POST')) {
      state.submitted++; return ok(state.job)
    }
    if (path === '/jobs/JOB-progress') {
      state.reads++
      if (state.offline) return route.abort('failed')
      state.job.heartbeat_at = new Date().toISOString()
      return ok(state.job)
    }
    if (path === '/jobs/JOB-progress/cancel') { state.job.status = 'CANCEL_REQUESTED'; return ok(state.job) }
    if (path === '/cases/CASE-progress') return ok({ id: 'CASE-progress', title: '合成案例 · 慢模型进度', owner_id: principal.id,
      problem_category: 'network', device_type: 'GW', device_model: 'Synthetic', description: '合成数据，检查阶段与断网显示',
      model_egress_approved: true, status: 'PARSED', created_at: stamp, updated_at: stamp })
    if (path.endsWith('/access')) return ok({ case_id: 'CASE-progress', role: 'ENGINEER', permission: 'OWNER' })
    if (path.endsWith('/artifacts')) return ok([{ id: 'ART-progress', kind: 'debug_log', original_name: 'synthetic.txt', status: 'PARSED',
      case_id: 'CASE-progress', metadata_json: '{}', size_bytes: 120, source_device_type: 'GW', source_device_role: 'PRIMARY' }])
    if (path.endsWith('/analyses') || path.endsWith('/repositories') || path.endsWith('/conversations') || path.endsWith('/analysis-revisions')) return ok([])
    if (path.endsWith('/events/stats')) return ok({ total: 10, filtered_total: 10, level_counts: {}, module_counts: {} })
    state.unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'Unexpected synthetic route' } })
  })
  return state
}

test('Chat test exposes remaining work, survives refresh and does not advance offline', async ({ page }) => {
  const state = await fixture(page, 'test_chat_model_connection')
  await page.goto('/settings')
  await page.getByRole('button', { name: '测试连接', exact: true }).click()
  const panel = page.locator('.model-task-progress').first()
  await expect(panel).toContainText('测试模型响应')
  await expect(panel).toContainText('约剩余 65%')
  await expect(panel).toContainText('已等待')
  await page.reload()
  await expect(panel).toContainText('约完成 35%')
  expect(state.submitted).toBe(1)
  state.offline = true
  await expect(page.getByText(/状态暂时无法刷新|无法连接服务器/).first()).toBeVisible()
  await expect(panel).toContainText('约完成 35%')
  state.offline = false
  state.job.status = 'COMPLETED'; state.job.progress = 100
  state.job.result_json = JSON.stringify({ profile_id: profile.id, test: { ok: true, proxy_url_configured: false } })
  await expect(panel).toContainText('100%')
  expect(state.submitted).toBe(1)
  expect(state.errors).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('Diagnosis shows the Skill phase and count, then keeps the failure milestone', async ({ page }) => {
  const state = await fixture(page, 'analyze_case')
  await page.goto('/cases/CASE-progress')
  await page.getByRole('button', { name: '开始综合诊断', exact: true }).click()
  const panel = page.locator('.model-task-progress').first()
  await expect(panel).toContainText('完整阅读 Skill')
  await expect(panel).toContainText('第 3 / 6 步')
  await expect(panel).toContainText('3 / 9 段')
  await expect(panel).toContainText('第 2/6 份 Skill')
  await expect(panel).toContainText('约剩余 65%')
  mkdirSync(captures, { recursive: true })
  await panel.screenshot({ path: resolve(captures, 'diagnosis-model-wait.png') })
  await page.reload()
  await expect(panel).toContainText('完整阅读 Skill')
  expect(state.submitted).toBe(1)
  state.job.status = 'FAILED'; state.job.error_message = '合成模型响应中断；进度保留'
  await expect(panel).toContainText(/未完成|等待重试/)
  await expect(panel).toContainText('35%')
  await expect(panel).not.toContainText('100%')
  await panel.screenshot({ path: resolve(captures, 'diagnosis-interrupted.png') })
  expect(state.errors).toEqual([])
  expect(state.unexpected).toEqual([])
})

test('Curation correction follows its own job after refresh and unlocks the next turn', async ({ page }) => {
  const base = await fixture(page, 'analyze_case')
  const session = { id: 'CUR-progress', created_by: principal.id, status: 'REVIEWING',
    title_hint: '合成提炼进度', draft_title: '合成提炼进度', draft_markdown: '第一版合成草稿',
    draft_version: 1, job_id: 'CURATION-initial', source_count: 1, sources: [], messages: [], revisions: [],
    validation: { confirmable: true, citation_count: 1, structure: { completeness: 1 }, warnings: [] } }
  const initial = { ...base.job, id: 'CURATION-initial', kind: 'curate_knowledge_folder', status: 'COMPLETED', progress: 100 }
  const correction = { ...base.job, id: 'CURATION-correction', kind: 'refine_knowledge_curation',
    progress_detail: { ...base.job.progress_detail, stage: '生成修订草稿' } }
  let submitted = 0, correctionReads = 0
  await page.route('**/api/v1/knowledge/categories', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/knowledge-curations**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/chat-jobs')) { submitted++; return route.fulfill({ json: correction }) }
    return route.fulfill({ json: path.endsWith('/knowledge-curations') ? [session] : session })
  })
  await page.route('**/api/v1/jobs/CURATION-*', route => {
    if (route.request().url().endsWith('CURATION-correction')) {
      correctionReads++
      // Finish between GET(session) and GET(job), as a real atomic worker can.
      if (correction.status === 'COMPLETED') {
        session.draft_version = 2; session.draft_markdown = '第二版合成草稿'
      }
      return route.fulfill({ json: correction })
    }
    return route.fulfill({ json: initial })
  })
  await page.goto('/knowledge/curation')
  const send = page.getByTestId('curation-send-correction')
  await page.getByTestId('curation-chat-instruction').fill('请核对第二条证据再修正草稿')
  await send.click()
  const panel = page.getByTestId('knowledge-curation-model-progress')
  await expect(panel).toContainText('生成修订草稿')
  await expect(send).toBeDisabled()
  await page.reload()
  await expect(panel).toContainText('生成修订草稿')
  await expect.poll(() => correctionReads).toBeGreaterThanOrEqual(2)
  correction.status = 'COMPLETED'; correction.progress = 100
  correction.result_json = JSON.stringify({ session_id: session.id, draft_version: 2 })
  await expect(panel).toContainText('本轮 AI 整理已完成')
  await expect(page.getByTestId('curation-draft-editor')).toHaveValue('第二版合成草稿')
  await expect(send).toBeEnabled()
  expect(submitted).toBe(1)
  expect(base.errors).toEqual([])
  expect(base.unexpected).toEqual([])
})
