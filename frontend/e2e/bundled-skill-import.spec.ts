import { expect, test, type Page } from '@playwright/test'

const stamp = '2026-09-10T12:00:00Z'
const principal = { id: 'EXPERT-1', username: 'expert', display_name: '组网专家', role: 'EXPERT', type: 'user_token' }
const roles = { diagnosis: '综合诊断', fault_tree: '故障树', log_analysis: '日志分析', prior_knowledge: '先验知识', report_template: '报告格式' }

function queuedJob() {
  return { id: 'BUNDLED-JOB', kind: 'bundled_skill_import', status: 'QUEUED', progress: 0, message: '等待构建索引', result_json: '{}',
    attempt: 1, max_attempts: 3, available_at: stamp, timeout_seconds: 3600, resource_limits_json: '{}' }
}

async function mockBundledImport(page: Page, initial: 'PRESERVED' | 'FAILED' = 'PRESERVED') {
  const state = {
    calls: [] as Array<{ method: string; path: string; body: unknown }>, unexpected: [] as string[], errors: [] as string[],
    loseConfirmResponse: false,
    bundled: { status: initial, message: initial === 'FAILED' ? '旧初始化未完成。' : '旧知识已保留，可核对后导入内置组网 Skill。', folder: 'bundled-knowledge/hilink-diag',
      ...(initial === 'FAILED' ? { operation_id: 'installer-network-skill-v1' } : {}) },
    operation: initial === 'FAILED'
      ? { operation: { operation_id: 'installer-network-skill-v1', status: 'FAILED' }, job: { ...queuedJob(), status: 'FAILED', progress: 42, message: '旧初始化实际失败信息', error_message: '旧初始化实际失败信息' } }
      : null as any
  }
  page.on('pageerror', error => state.errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', ''), method = request.method()
    let body: unknown = undefined
    try { body = JSON.parse(request.postData() || '') } catch { /* GET has no JSON body. */ }
    state.calls.push({ method, path, body })
    const ok = (value: unknown) => route.fulfill({ json: value })
    if (path === '/system/auth-info') return ok({ simple_engineer_login: false })
    if (path === '/workbench/bootstrap') return ok({ principal, categories: [{ id: 'network', name: '组网问题' }], knowledge_roles: roles,
      models: [], preferences: {}, model_selection: { profile_id: null, error: null }, bundled_knowledge: state.bundled })
    if (path === '/workbench/knowledge' || path === '/knowledge-contributions') return ok([])
    if (path === '/workbench/bundled-skill/preview') return ok({ operation_id: (body as { operation_id: string }).operation_id,
      source_sha256: 'a'.repeat(64), preview_hash: 'b'.repeat(64), can_confirm: true, message: '可新增完整六文件组网包。',
      counts: { added_files: 6, retired_documents: 0 }, preserved: ['existing_knowledge', 'existing_skills', 'existing_templates', 'cases', 'reports'],
      manifest: ['SKILL.md', 'methodology.md', 'fault-tree.md', 'log-analysis.md', 'architecture.md', 'report-format.md'].map((path, index) => ({
        path, role: ['diagnosis', 'diagnosis', 'fault_tree', 'log_analysis', 'prior_knowledge', 'report_template'][index], bytes: 100 + index,
        source_sha256: String(index).repeat(64), adaptation_diff: index === 0 ? '-旧引用\n+平台证据接口' : '', disposition: 'ADD'
      })) })
    if (path === '/workbench/bundled-skill/confirm') {
      const operationId = (body as { operation_id: string }).operation_id
      state.operation = { operation: { operation_id: operationId, status: 'APPROVED', message: '人工审批已保存' }, job: queuedJob() }
      state.bundled = { status: 'APPROVED', message: '内置组网 Skill 已排队。', folder: 'bundled-knowledge/hilink-diag', operation_id: operationId }
      if (state.loseConfirmResponse) { state.loseConfirmResponse = false; return route.abort('failed') }
      return ok(state.operation)
    }
    if (path.startsWith('/workbench/bundled-skill/')) return ok(state.operation)
    if (path === '/jobs/BUNDLED-JOB/retry') { state.operation.job = { ...state.operation.job, status: 'QUEUED', message: '重试已排队' }; return ok(state.operation.job) }
    state.unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: `Unexpected synthetic route: ${method} ${path}` } })
  })
  return state
}

test('preserved bundle previews a translated six-file summary and confirms a single additive import', async ({ page }) => {
  const state = await mockBundledImport(page)
  await page.goto('/knowledge-management')
  await page.getByRole('button', { name: '导入内置组网 Skill', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入内置组网 Skill' })
  await expect(dialog.getByRole('textbox')).toHaveCount(0)
  await page.getByRole('button', { name: '预览内置组网 Skill 导入', exact: true }).click()
  await expect(dialog.getByText('本次核对 6 份文件：可新增 6 份，已有 0 份，冲突 0 份。', { exact: true })).toBeVisible()
  await expect(dialog.getByText('existing_knowledge', { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('added_files', { exact: true })).toHaveCount(0)
  await expect(dialog.getByText('平台证据接口', { exact: false })).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: '允许使用当前 Embedding 模型重建索引（保留的共享知识和新增组网 Skill 将一并进入新索引）' })).toBeChecked()
  await page.getByRole('checkbox', { name: '我已核对完整六文件清单、处理结果和全部适配差异' }).locator('..').click()
  await page.getByRole('button', { name: '确认导入内置组网 Skill', exact: true }).click()
  const confirm = state.calls.find(call => call.path === '/workbench/bundled-skill/confirm')!
  expect(confirm.body).toEqual({ operation_id: expect.any(String), expected_source_sha256: 'a'.repeat(64), expected_preview_hash: 'b'.repeat(64), confirmed: true, model_egress_approved: true })
  expect(Object.keys(confirm.body as Record<string, unknown>)).not.toContain('data_root')
  await expect(dialog.getByTestId('bundled-skill-import-progress')).toContainText('等待构建索引')
  expect(state.unexpected).toEqual([])
  expect(state.errors).toEqual([])
})

test('failed installer-default operation resumes through the new endpoint and retries its existing job', async ({ page }) => {
  const state = await mockBundledImport(page, 'FAILED')
  await page.goto('/knowledge-management')
  await page.getByRole('button', { name: '导入内置组网 Skill', exact: true }).click()
  await expect(page.getByTestId('bundled-skill-import-progress').getByText('旧初始化实际失败信息', { exact: true })).toBeVisible()
  expect(state.calls.some(call => call.method === 'GET' && call.path === '/workbench/bundled-skill/installer-network-skill-v1')).toBe(true)
  await page.getByRole('button', { name: '重试导入任务', exact: true }).click()
  expect(state.calls.some(call => call.method === 'POST' && call.path === '/jobs/BUNDLED-JOB/retry')).toBe(true)
  expect(state.unexpected).toEqual([])
  expect(state.errors).toEqual([])
})

test('lost confirm response refreshes the saved operation and reopening does not submit it again', async ({ page }) => {
  const state = await mockBundledImport(page)
  state.loseConfirmResponse = true
  await page.goto('/knowledge-management')
  await page.getByRole('button', { name: '导入内置组网 Skill', exact: true }).click()
  await page.getByRole('button', { name: '预览内置组网 Skill 导入', exact: true }).click()
  await page.getByRole('checkbox', { name: '我已核对完整六文件清单、处理结果和全部适配差异' }).locator('..').click()
  await page.getByRole('button', { name: '确认导入内置组网 Skill', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '导入内置组网 Skill' })
  await expect(dialog.getByTestId('bundled-skill-import-progress')).toContainText('等待构建索引')
  const operationId = (state.calls.find(call => call.path === '/workbench/bundled-skill/confirm')!.body as { operation_id: string }).operation_id
  expect(state.calls.some(call => call.method === 'GET' && call.path === `/workbench/bundled-skill/${operationId}`)).toBe(true)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '导入内置组网 Skill', exact: true }).click()
  await expect(dialog.getByTestId('bundled-skill-import-progress')).toBeVisible()
  expect(state.calls.filter(call => call.path === '/workbench/bundled-skill/confirm')).toHaveLength(1)
  expect(state.unexpected).toEqual([])
  expect(state.errors).toEqual([])
})
