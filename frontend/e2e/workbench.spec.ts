import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'

const screenshotRoot = resolve(import.meta.dirname, '../../artifacts/validation/workbench-20260909')
const categories = [{ id: 'network', name: '组网问题' }, { id: 'connection', name: '连接问题' }, { id: 'unknown', name: '未知' }, { id: 'general', name: '通用知识' }]
const roles = { log_analysis: '日志分析', diagnosis: '综合诊断', fault_tree: '故障树', report_template: '报告格式', prior_knowledge: '先验知识' }
const stamp = '2026-09-09T00:00:00Z'
const models = [{ id: 'MODEL-A', name: '标准诊断模型', active: true }, { id: 'MODEL-B', name: '深入诊断模型', active: false }]
const documents = [
  { id: 'DOC-SKILL', title: 'GW / AP 组网日志分析 Skill', version: 3, status: 'ACTIVE', categories: ['network'], role: 'log_analysis', source_paths: ['network/SKILL.md'], content: '# 日志分析\n\n先识别设备来源，再核对连接与心跳证据。\n\n[详细方法](references/checks.md)' },
  { id: 'DOC-CONNECTION', title: '连接超时与重试诊断', version: 2, status: 'ACTIVE', categories: ['connection'], role: 'diagnosis', content: '# 连接诊断\n逐条核对超时与成功重连，记录反证。' },
  { id: 'DOC-TEMPLATE', title: '团队组网报告格式', version: 4, status: 'ACTIVE', categories: ['network'], role: 'report_template', content: '# 组网总览\n# 异常设备时间轨迹\n# 逐 AP 分析与因果链\n# 结论与后续' },
  { id: 'builtin-network-report', title: '组网问题报告格式', version: 2, status: 'ACTIVE', categories: ['network'], role: 'report_template', content: '# 内置组网报告 v2' },
  { id: 'DOC-DRAFT', title: '管理员未发布草稿', version: 1, status: 'DRAFT', categories: ['network'], role: 'fault_tree', content: '# 草稿，仅管理员可见' }
]
const makeCase = (id: string, title: string, category = 'network', consent = true) => ({
  id, title, problem_category: category, chat_profile_id: 'MODEL-B', owner_id: 'USER-1', device_type: 'GW',
  device_model: 'Synthetic GW', firmware_version: 'synthetic-1.0', description: '合成场景：AP 断开后恢复连接，需核对设备日志中的支持证据与反证。',
  status: 'DRAFT', severity: 'MEDIUM', model_egress_approved: consent, created_at: stamp, updated_at: stamp
})
const makeAnalysis = (caseId: string) => ({
  id: 'ANL-COMPLETE', case_id: caseId, status: 'COMPLETED', created_at: stamp, evidence_json: '[]',
  result_json: JSON.stringify({ summary: '合成诊断：请核对上联连接的恢复情况。', hypotheses: [], recommended_actions: [], confirmed_facts: [] })
})
const makeSession = (paths = ['synthetic-skill/SKILL.md', 'synthetic-skill/references/checks.md']) => ({
  id: 'ASSIST-1', title: '组网 Skill 整理 · 合成资料', version: 7, status: 'REVIEW', created_at: stamp,
  files: paths.map(path => ({ path })), model_egress_approved: true, mode: 'edit', review_digest: 'a'.repeat(64),
  messages: [{ role: 'user', content: '完整阅读 Skill 与依赖，保留原有步骤，再补充连接恢复的反证。' }],
  coverage: Object.fromEntries([...paths, 'knowledge/DOC-SKILL'].map(path => [path, { read: 2, total: 2, complete: true }])),
  answer: '全部资料与目标文档已读完。建议合并日志分析方法，并新增连接检查指南。',
  bundle_manifest: [{ path: paths[0], document_id: 'DOC-SKILL', references: [{ reference: 'references/checks.md', path: paths[1] }] }],
  plan: [
    { operation_id: 'OP-1', action: 'merge', title: 'GW / AP 组网日志分析 Skill', categories: ['network'], role: 'log_analysis', source_paths: [paths[0]], sources: [{ path: paths[0], start: 0, end: 45, sha256: 'a'.repeat(64) }], target_id: 'DOC-SKILL', expected_version: 3, expected_lock: 5, before: '# 日志分析\n等待 10 秒。', after: '# 日志分析\n等待 20 秒，核对恢复证据。', diff: '--- 原文\n+++ 拟发布版本\n@@ -1,2 +1,2 @@\n # 日志分析\n-等待 10 秒。\n+等待 20 秒，核对恢复证据。', reason: '保留已有的扫描流程，补充连接恢复的核对步骤。' },
    { operation_id: 'OP-2', action: 'create', title: '连接恢复检查指南', categories: ['connection'], role: 'prior_knowledge', source_paths: [paths[1]], sources: [{ path: paths[1], start: 0, end: 30, sha256: 'b'.repeat(64) }], before: '', after: '# 连接恢复\n成功重连作为反证。', diff: '--- 原文\n+++ 拟发布版本\n+# 连接恢复\n+成功重连作为反证。', reason: '为连接问题独立保存检查指南，并保留 Skill 相对引用。' }
  ],
  job: { id: 'JOB-ASSIST', kind: 'assistant_plan', status: 'COMPLETED', progress: 100, message: '已完整读取全部资料', result_json: '{}' }
})

async function mockWorkbench(page: Page, role: 'ADMIN' | 'ENGINEER' | 'VIEWER' = 'ENGINEER', permission = 'OWNER') {
  const state = {
    role, permission, preference: 'MODEL-B', calls: [] as { path: string; method: string; data: any; raw: string }[],
    unexpected: [] as string[], errors: [] as string[],
    cases: [makeCase('CASE-1', 'AP 间歇离线，恢复后需核对上联证据'), makeCase('CASE-LEGACY', '历史案例 · 模型授权已关闭', 'connection', false), makeCase('CASE-UNKNOWN', '设备连接异常，类别待确认', 'unknown')],
    artifacts: [] as any[], analyses: [] as any[], session: makeSession() as any,
    publishPending: false, staleConfirm: false,
    library: [{ id: 'LIB-1', title: '已定位的合成组网案例', version: 2, status: 'PENDING', content: '已核对上联恢复日志，确认连接恢复后无再次超时；仅为合成案例。', problem_category: 'network', owner_id: 'USER-1', case_id: 'CASE-1', analysis_id: 'ANL-COMPLETE', report_markdown: '# 组网总览\n合成报告。\n# 异常设备时间轨迹\n已恢复。' }] as any[]
  }
  page.on('pageerror', error => state.errors.push(error.message))
  // A catch-all route rejects unexpected API calls; no business server or model exists.
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', ''), method = request.method(), raw = request.postDataBuffer()?.toString('utf8') || ''
    let data: any = null
    try { data = JSON.parse(raw) } catch { /* multipart bodies are checked separately. */ }
    state.calls.push({ path, method, data, raw })
    const ok = (json: any) => route.fulfill({ json })
    const fail = (status: number, detail: string) => route.fulfill({ status, json: { detail } })
    const principal = { id: 'USER-1', username: 'synthetic-engineer', display_name: role === 'ADMIN' ? '合成管理员' : '合成工程师', type: 'user_token', role }
    if (path === '/system/auth-info') return ok({ simple_engineer_login: false })
    if (path === '/system/me') return ok(principal)
    if (path === '/system/user-directory') return ok([])
    if (path === '/workbench/bootstrap') return ok({ principal, categories, knowledge_roles: roles, models, preferences: { chat_profile_id: state.preference } })
    if (path === '/workbench/preferences') { state.preference = data.chat_profile_id; return ok(data) }
    if (path === '/workbench/knowledge') return ok(documents)
    if (path === '/workbench/library' && method === 'GET') return ok(state.library)
    if (path === '/workbench/library' && method === 'POST') {
      const item = { ...data, id: 'LIB-NEW', version: 1, owner_id: 'USER-1', status: 'PENDING' }
      state.library.push(item); return ok(item)
    }
    if (/^\/workbench\/library\/[^/]+\/review$/.test(path)) {
      const item = state.library.find(item => path.includes(item.id)); item.status = data.approve ? 'CONFIRMED' : 'REJECTED'; return ok(item)
    }
    if (path === '/workbench/templates/network' && method === 'PUT') return ok({ category_id: 'network', ...data })
    if (path === '/cases' && method === 'GET') return ok(state.cases)
    if (path === '/cases' && method === 'POST') {
      const created = { ...makeCase('CASE-NEW', data.title), ...data }; state.cases.unshift(created); return ok(created)
    }
    if (/^\/cases\/[^/]+$/.test(path)) {
      const item = state.cases.find(item => item.id === path.split('/')[2])
      if (method === 'PATCH') Object.assign(item!, data)
      return ok(item)
    }
    if (path.endsWith('/access')) return ok({ case_id: path.split('/')[2], role, permission: state.permission })
    if (path.endsWith('/members') || path.endsWith('/repositories') || path.endsWith('/conversations') || path.endsWith('/analysis-revisions')) return ok([])
    if (path.endsWith('/events/stats')) return ok({ total: 12, filtered_total: 12, level_counts: { INFO: 12 }, module_counts: {} })
    if (path.endsWith('/artifacts')) {
      if (method === 'POST') { const item = { id: 'ART-1', case_id: path.split('/')[2], original_name: 'synthetic-log.txt', kind: 'debug_log', size_bytes: 120, status: 'UPLOADED', source_device_type: 'GW', source_device_role: 'PRIMARY', metadata_json: '{}' }; state.artifacts.push(item); return ok(item) }
      return ok(state.artifacts)
    }
    const job = { id: 'JOB-1', kind: 'parse_artifact', status: 'COMPLETED', progress: 100, message: '合成任务完成', result_json: '{}' }
    if (path.endsWith('/parse')) { state.artifacts[0].status = 'PARSED'; return ok(job) }
    if (path === '/jobs/JOB-1') return ok(job)
    if (path.endsWith('/analyses')) {
      if (method === 'POST') { state.analyses = [makeAnalysis(path.split('/')[2])]; return ok({ ...job, kind: 'analyze_case' }) }
      return ok(state.analyses)
    }
    if (path.endsWith('/report/preview')) return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="zh"><body><h1>合成诊断报告</h1><h2>组网总览</h2><h2>异常设备时间轨迹</h2><h2>逐 AP 分析与因果链</h2><h2>结论与后续</h2></body></html>' })
    if (path === '/workbench/assistant' && method === 'GET') return ok([state.session].map(({ id, title, version, status, created_at }) => ({ id, title, version, status, created_at })))
    if (path === '/workbench/assistant' && method === 'POST') {
      const pathsField = raw.match(/name="paths"\r\n\r\n([^\r]+)/)?.[1]
      const paths = pathsField ? JSON.parse(pathsField) : []
      state.session = makeSession(paths.length ? paths : undefined)
      if (/name="model_egress_approved"\r\n\r\nfalse/.test(raw)) { state.session.status = 'PAUSED'; state.session.model_egress_approved = false; state.session.plan = []; state.session.coverage = {}; delete state.session.job }
      return ok(state.session)
    }
    if (path === '/workbench/assistant/ASSIST-1' && method === 'GET') {
      if (state.publishPending) { state.publishPending = false; state.session.status = 'PUBLISHED'; state.session.version++ }
      return ok(state.session)
    }
    if (path.endsWith('/ASSIST-1/source')) return ok({ path: url.searchParams.get('path'), content: '# 合成来源\n完整读取的示例内容。', start: 0, end: 20, total_characters: 20, next_cursor: null })
    if (path.endsWith('/ASSIST-1/messages')) {
      state.session.messages.push({ role: 'user', content: data.message }); state.session.version++; state.session.status = 'REVIEW'
      state.session.plan[0].reason = '按你的要求保留原步骤，只补充恢复反证。'; return ok(state.session)
    }
    if (path.endsWith('/ASSIST-1/confirm')) {
      if (state.staleConfirm) { state.staleConfirm = false; state.session.version++; state.session.plan[0].expected_version = 4; return fail(409, '目标知识已变化，请重新核对差异') }
      state.session.status = 'BUILDING'; state.session.version++; state.publishPending = true; return ok(state.session)
    }
    if (path.endsWith('/ASSIST-1/pause') || path.endsWith('/ASSIST-1/cancel')) {
      state.session.version++; state.session.status = path.endsWith('/pause') ? 'PAUSED' : 'CANCELLED'; return ok(state.session)
    }
    if (path.endsWith('/ASSIST-1/retry')) { state.session.version++; state.session.status = 'READING'; return ok(state.session) }
    if (path.endsWith('/ASSIST-1/consent')) { state.session.version++; state.session.model_egress_approved = data.model_egress_approved; if (!data.model_egress_approved) state.session.status = 'PAUSED'; return ok(state.session) }
    state.unexpected.push(`${method} ${path}`)
    return fail(501, 'Unexpected synthetic test request')
  })
  return state
}
async function screenshot(page: Page, name: string) {
  mkdirSync(screenshotRoot, { recursive: true })
  await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: join(screenshotRoot, name), fullPage: !name.includes('diff'), animations: 'disabled' })
}
async function assistantPage(page: Page) {
  await page.goto('/knowledge')
  await page.getByRole('tab', { name: 'AI 整理助手', exact: true }).click()
}
function assertClean(state: Awaited<ReturnType<typeof mockWorkbench>>) {
  expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([])
}

test('engineer sees three navigation items, published knowledge, personal model preferences and guarded admin routes', async ({ page }) => {
  const state = await mockWorkbench(page)
  await page.goto('/settings')
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('menuitem')).toHaveCount(3)
  await expect(page.getByRole('heading', { name: '个人诊断模型' })).toBeVisible()
  await expect(page.getByRole('tab', { name: '模型与索引' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: '安全与审计' })).toHaveCount(0)
  await screenshot(page, 'engineer-settings.png')
  await page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '个人诊断模型' }) }).click()
  await page.getByRole('option', { name: '标准诊断模型 · 系统默认' }).click()
  await page.getByRole('button', { name: '保存偏好' }).click()
  await expect.poll(() => state.preference).toBe('MODEL-A')
  for (const path of ['/admin/models', '/admin/security', '/knowledge/manage', '/knowledge/curation', '/cognitive-search', '/agent-runs']) {
    await page.goto(path)
    await expect(page).toHaveURL(/\/settings\?notice=admin-required/)
    await expect(page.getByRole('heading', { name: '个人诊断模型' })).toBeVisible()
  }
  await page.goto('/knowledge')
  await expect(page.getByRole('tab', { name: 'AI 整理助手' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'AI 案例提炼' })).toHaveCount(0)
  await expect(page.getByText('管理员未发布草稿', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '组网问题', exact: true }).click()
  await page.getByRole('button', { name: '日志分析', exact: true }).click()
  await expect(page.locator('.knowledge-card')).toHaveCount(1)
  await page.locator('.knowledge-card').click()
  await expect(page.locator('.el-drawer')).toContainText('network/SKILL.md')
  await expect(page.locator('.document-text')).toContainText('[详细方法](references/checks.md)')
  expect(state.calls.filter(call => call.path.startsWith('/workbench/assistant') || call.path.startsWith('/system/models'))).toHaveLength(0)
  assertClean(state)
})

test('viewer cannot create or submit cases, or enter knowledge administration', async ({ page }) => {
  const state = await mockWorkbench(page, 'VIEWER', 'VIEWER')
  await page.goto('/cases')
  await expect(page.getByRole('button', { name: '创建待定位案例' })).toBeDisabled()
  await page.goto('/knowledge')
  await expect(page.getByRole('button', { name: '提交已定位案例' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'AI 整理助手' })).toHaveCount(0)
  assertClean(state)
})

test('case flow uses category and model preset, parses synthetic logs, retains QA/report and submits completed own report', async ({ page }) => {
  const state = await mockWorkbench(page)
  await page.goto('/cases')
  await expect(page.locator('.case-title-link')).toHaveCount(3)
  await screenshot(page, 'case-home.png')
  await page.getByRole('button', { name: '创建待定位案例' }).click()
  const dialog = page.getByRole('dialog', { name: '创建待定位案例' })
  await expect(dialog.getByRole('switch', { name: '模型出站授权' })).toBeChecked()
  await expect(dialog.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '诊断模型', exact: true }) })).toContainText('深入诊断模型')
  await dialog.getByRole('textbox', { name: '问题标题' }).fill('合成 GW / AP 连接诊断')
  await dialog.getByText('连接问题', { exact: true }).click()
  await dialog.getByRole('textbox', { name: '问题现象' }).fill('合成日志出现一次断开，随后恢复连接，需要核对恢复证据。')
  await dialog.getByRole('button', { name: '创建并上传日志' }).click()
  await expect(page).toHaveURL(/\/cases\/CASE-NEW$/)
  expect(state.calls.find(call => call.path === '/cases' && call.method === 'POST')?.data).toMatchObject({ problem_category: 'connection', chat_profile_id: 'MODEL-B', model_egress_approved: true })
  await expect(page.getByRole('button', { name: '提交案例与报告' })).toBeDisabled()
  await expect(page.getByRole('tab', { name: /事件|时间线/ })).toHaveCount(0)
  await expect(page.locator('details').filter({ has: page.getByText('案例协作与成员权限', { exact: true }) })).not.toHaveAttribute('open')
  await page.getByLabel('选择问题日志', { exact: true }).setInputFiles({ name: 'synthetic-log.txt', mimeType: 'text/plain', buffer: Buffer.from('2026-09-09 INFO synthetic AP reconnected\n') })
  await page.getByRole('button', { name: '上传并解析' }).click()
  await expect(page.getByRole('cell', { name: 'PARSED', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '开始综合诊断' }).click()
  await expect(page.getByText('合成诊断：请核对上联连接的恢复情况。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '提交案例与报告' }).click()
  await page.getByRole('textbox', { name: '定位结果与验证' }).fill('已核对断开后的成功重连日志，并验证合成 AP 稳定运行。')
  await page.getByRole('button', { name: '提交管理员审核', exact: true }).click()
  await expect.poll(() => state.calls.filter(call => call.path === '/workbench/library' && call.method === 'POST').length).toBe(1)
  expect(state.calls.find(call => call.path === '/workbench/library' && call.method === 'POST')?.data).toMatchObject({ case_id: 'CASE-NEW', analysis_id: 'ANL-COMPLETE', problem_category: 'connection' })
  await page.getByRole('tab', { name: '诊断报告', exact: true }).click()
  await expect(page.frameLocator('iframe[title="诊断报告预览"]').getByRole('heading', { name: '合成诊断报告' })).toBeVisible()
  await page.getByRole('tab', { name: '交互问答', exact: true }).click()
  await expect(page.getByRole('button', { name: '发送到后台' })).toBeVisible()
  expect(state.calls.filter(call => /\/events$|\/timeline$/.test(call.path))).toHaveLength(0)
  assertClean(state)
})

test('historical opt-out stays false on load and case edits; an editor cannot submit the owner result', async ({ page }) => {
  const state = await mockWorkbench(page, 'ENGINEER', 'EDITOR')
  state.analyses = [makeAnalysis('CASE-LEGACY')]
  await page.goto('/cases/CASE-LEGACY')
  await expect(page.getByRole('switch', { name: '案例模型出站授权' })).not.toBeChecked()
  expect(state.calls.filter(call => call.method === 'PATCH')).toHaveLength(0)
  await expect(page.getByRole('button', { name: '提交案例与报告' })).toHaveCount(0)
  const modelSelect = page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '案例诊断模型' }) })
  await modelSelect.hover()
  await modelSelect.locator('.el-select__clear').click()
  await page.getByRole('textbox', { name: '案例问题现象' }).fill('继续整理历史案例的本地证据。')
  await page.getByRole('button', { name: '保存问题资料' }).click()
  await expect.poll(() => state.calls.filter(call => call.method === 'PATCH').length).toBe(1)
  expect(state.calls.find(call => call.method === 'PATCH')?.data).not.toHaveProperty('model_egress_approved')
  expect(state.calls.find(call => call.method === 'PATCH')?.data.chat_profile_id).toBeNull()
  await expect(page.getByRole('switch', { name: '案例模型出站授权' })).not.toBeChecked()
  assertClean(state)
})

test('admin folder upload preserves paths, corrections fence the plan, one exact approval publishes and the durable session reopens', async ({ page }) => {
  const state = await mockWorkbench(page, 'ADMIN')
  const folder = mkdtempSync(join(tmpdir(), 'workbench-fixture-'))
  try {
    mkdirSync(join(folder, 'references'))
    writeFileSync(join(folder, 'SKILL.md'), '# 合成 Skill\n读取 [检查指南](references/checks.md)。\n')
    writeFileSync(join(folder, 'references', 'checks.md'), '# 检查指南\n成功重连作为反证。\n')
    await assistantPage(page)
    await page.getByLabel('选择 Skill 文件夹', { exact: true }).setInputFiles(folder)
    await expect(page.getByText(`${basename(folder)}/references/checks.md`, { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '开始整理', exact: true }).click()
    await expect(page.getByText('全文已读完', { exact: true })).toBeVisible()
    const creation = state.calls.find(call => call.path === '/workbench/assistant' && call.method === 'POST')!
    expect(creation.raw).toContain(`${basename(folder)}/SKILL.md`)
    expect(creation.raw).toContain(`${basename(folder)}/references/checks.md`)
    expect(creation.raw).toMatch(/name="model_egress_approved"\r\n\r\ntrue/)
    await page.getByRole('textbox', { name: '知识助手要求' }).fill('保留原步骤，只补充恢复反证。')
    await expect(page.getByRole('button', { name: '确认并发布 2 项变更' })).toBeDisabled()
    await page.getByRole('button', { name: '发送纠偏或问题' }).click()
    await expect(page.getByRole('cell', { name: '按你的要求保留原步骤，只补充恢复反证。', exact: true })).toBeVisible()
    expect(state.calls.find(call => call.path.endsWith('/messages'))?.data).toMatchObject({ version: 7, message: '保留原步骤，只补充恢复反证。' })
    await screenshot(page, 'admin-assistant.png')
    await page.getByRole('button', { name: '查看差异', exact: true }).first().click()
    const diff = page.getByRole('dialog', { name: '核对变更' })
    await expect(diff).toContainText('目标当前版本 v3')
    await expect(diff.locator('.diff-remove')).toContainText(['--- 原文', '-等待 10 秒。'])
    await screenshot(page, 'admin-assistant-diff.png')
    await diff.getByRole('button', { name: '返回变更清单' }).click()
    await page.getByRole('button', { name: '确认并发布 2 项变更' }).click()
    await expect(page.getByText('知识已发布，新诊断将使用更新后的版本。', { exact: true })).toBeVisible()
    const approvals = state.calls.filter(call => call.path.endsWith('/confirm'))
    expect(approvals).toHaveLength(1)
    expect(approvals[0].data).toEqual({ version: 8, review_digest: 'a'.repeat(64) })
    await page.reload()
    await page.getByRole('tab', { name: 'AI 整理助手', exact: true }).click()
    await page.locator('.history-item').first().click()
    await expect(page.getByText('知识已发布，新诊断将使用更新后的版本。', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '确认并发布 2 项变更' })).toHaveCount(0)
    assertClean(state)
  } finally {
    // Only remove the exact synthetic fixture returned by mkdtemp.
    expect(dirname(resolve(folder))).toBe(resolve(tmpdir()))
    expect(basename(folder)).toMatch(/^workbench-fixture-/)
    rmSync(folder, { recursive: true, force: true })
  }
})

test('assistant supports pause, retry, cancel and persistent consent opt-out without implicit resume', async ({ page }) => {
  const state = await mockWorkbench(page, 'ADMIN')
  state.session.status = 'READING'; state.session.plan = []; state.session.coverage = {}
  await assistantPage(page)
  await page.locator('.history-item').first().click()
  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await expect(page.getByRole('button', { name: '继续整理', exact: true })).toBeEnabled()
  expect(state.calls.find(call => call.path.endsWith('/pause'))?.data).toEqual({ version: 7 })
  await page.getByRole('button', { name: '继续整理', exact: true }).click()
  await expect(page.getByRole('button', { name: '取消本次任务' })).toBeEnabled()
  await page.locator('.el-switch').filter({ has: page.getByRole('switch', { name: '知识助手模型授权' }) }).click()
  await expect(page.getByRole('button', { name: '继续整理', exact: true })).toBeDisabled()
  expect(state.calls.find(call => call.path.endsWith('/consent'))?.data).toEqual({ version: 9, model_egress_approved: false })
  await page.locator('.el-switch').filter({ has: page.getByRole('switch', { name: '知识助手模型授权' }) }).click()
  await expect(page.getByRole('button', { name: '继续整理', exact: true })).toBeEnabled()
  expect(state.calls.filter(call => call.path.endsWith('/retry'))).toHaveLength(1)
  await page.getByRole('button', { name: '继续整理', exact: true }).click()
  await page.getByRole('button', { name: '取消本次任务' }).click()
  await expect(page.getByRole('button', { name: '继续整理', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '新建整理会话' }).click()
  await page.locator('.el-switch').filter({ has: page.getByRole('switch', { name: '知识助手模型授权' }) }).click()
  await page.getByRole('textbox', { name: '知识助手要求' }).fill('先保存合成会话，稍后再分析。')
  await page.getByRole('button', { name: '保存会话，暂不分析' }).click()
  await expect(page.getByRole('button', { name: '继续整理', exact: true })).toBeDisabled()
  const saved = state.calls.find(call => call.path === '/workbench/assistant' && call.method === 'POST')!
  expect(saved.raw).toMatch(/name="model_egress_approved"\r\n\r\nfalse/)
  expect(state.calls.filter(call => call.path.endsWith('/confirm'))).toHaveLength(0)
  assertClean(state)
})

test('incomplete reading blocks approval and a stale target refreshes the plan without automatic reapproval', async ({ page }) => {
  const state = await mockWorkbench(page, 'ADMIN')
  state.session.coverage['knowledge/DOC-SKILL'].complete = false
  await assistantPage(page)
  await page.locator('.history-item').first().click()
  await expect(page.getByRole('button', { name: '确认并发布 2 项变更' })).toBeDisabled()
  state.session.coverage['knowledge/DOC-SKILL'].complete = true
  await page.getByRole('button', { name: '刷新进度' }).click()
  await expect(page.getByRole('button', { name: '确认并发布 2 项变更' })).toBeEnabled()
  state.staleConfirm = true
  await page.getByRole('button', { name: '确认并发布 2 项变更' }).click()
  await expect(page.getByText('目标知识已变化，请重新核对差异', { exact: true })).toBeVisible()
  await expect(page.getByText('已有文档 · v4', { exact: true })).toBeVisible()
  expect(state.calls.filter(call => call.path.endsWith('/confirm'))).toHaveLength(1)
  expect(state.session.status).toBe('REVIEW')
  assertClean(state)
})

test('admin reviews the submitted report and chooses an active category template with the exact version', async ({ page }) => {
  const state = await mockWorkbench(page, 'ADMIN')
  await page.goto('/knowledge')
  await page.getByRole('tab', { name: /已定位案例/ }).click()
  await page.getByRole('button', { name: '查看并审核' }).click()
  await expect(page.locator('.el-drawer')).toContainText('已核对上联恢复日志')
  await page.getByRole('button', { name: '查看附带报告' }).click()
  await expect(page.locator('.el-drawer')).toContainText('# 异常设备时间轨迹')
  await page.getByRole('button', { name: '确认入库', exact: true }).click()
  await expect.poll(() => state.library[0].status).toBe('CONFIRMED')
  expect(state.calls.find(call => call.path.endsWith('/review'))?.data).toEqual({ version: 2, approve: true })
  await page.getByRole('tab', { name: '诊断知识', exact: true }).click()
  await page.locator('.knowledge-card').filter({ has: page.getByRole('heading', { name: '团队组网报告格式' }) }).click()
  await page.getByRole('button', { name: '设为该类别默认模板' }).click()
  await expect.poll(() => state.calls.filter(call => call.path === '/workbench/templates/network').length).toBe(1)
  expect(state.calls.find(call => call.path === '/workbench/templates/network')?.data).toEqual({ document_id: 'DOC-TEMPLATE', version: 4 })
  assertClean(state)
})

test('small screen keeps navigation and knowledge controls inside the viewport', async ({ page }) => {
  const state = await mockWorkbench(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/cases')
  await expect(page.getByRole('heading', { name: '故障定位', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('menuitem')).toHaveCount(3)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('menuitem', { name: '知识库' }).click()
  await page.getByRole('button', { name: '连接问题', exact: true }).click()
  await page.getByRole('button', { name: '综合诊断', exact: true }).click()
  await expect(page.locator('.knowledge-card')).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  assertClean(state)
})
