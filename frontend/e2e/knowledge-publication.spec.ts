import { expect, test } from '@playwright/test'

const api = `${process.env.E2E_BACKEND_URL || 'http://127.0.0.1:18080'}/api/v1`

test('editing published knowledge keeps it online until reviewed atomic publication', async ({ page, request }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const title = `Publication E2E ${Date.now()}`
  const created = await request.post(`${api}/knowledge`, { data: {
    title, content: '# Synthetic logs\nOLD_AUTH_TIMEOUT', source_type: 'document'
  } })
  expect(created.ok(), await created.text()).toBeTruthy()
  let document = await created.json()
  for (const action of ['submit', 'approve']) {
    const reviewed = await request.post(`${api}/knowledge/${document.id}/review/${action}`, {
      data: { expected_lock_version: document.lock_version, comment: 'Synthetic browser test' }
    })
    expect(reviewed.ok(), await reviewed.text()).toBeTruthy()
    document = await reviewed.json()
  }
  await page.goto('/knowledge')
  const row = page.locator('.el-table__row').filter({ hasText: title }).first()
  await row.getByRole('button', { name: '修改', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '修改知识内容' })
  await dialog.getByLabel('正文', { exact: true }).fill('# Synthetic logs\nNEW_POWER_FAILURE')
  await dialog.getByRole('button', { name: '保存并重建索引' }).click()
  await expect(dialog).not.toBeVisible()
  const pending = await (await request.get(`${api}/knowledge/${document.id}`)).json()
  expect(pending.active).toBe(true)
  expect(pending.content).toContain('OLD_AUTH_TIMEOUT')
  expect(pending.pending_draft.snapshot.content).toContain('NEW_POWER_FAILURE')
  await row.getByRole('button', { name: '提交草稿', exact: true }).click()
  await row.getByRole('button', { name: '发布草稿', exact: true }).click()
  await page.getByRole('button', { name: '确认发布此修订', exact: true }).click()
  await page.getByRole('button', { name: 'OK', exact: true }).click()
  await expect.poll(async () => {
    const current = await (await request.get(`${api}/knowledge/${document.id}`)).json()
    return current.version
  }, { timeout: 60_000 }).toBe(2)
  const released = await (await request.get(`${api}/knowledge/${document.id}`)).json()
  expect(released.content).toContain('NEW_POWER_FAILURE')
  expect(released.pending_draft).toBeNull()
  const manifests = await (await request.get(`${api}/knowledge/${document.id}/publications`)).json()
  expect(manifests[0].document_version).toBe(2)
  expect(manifests[0].graph_generation_id).toBeTruthy()
  expect(errors).toEqual([])
})

test('human verified historical material has a visible compilation preview and publishes', async ({ page, request }) => {
  const title = `Human verified E2E ${Date.now()}`
  const created = await request.post(`${api}/knowledge`, { data: {
    title, content: '# Logs\n`HUMAN_CONFIRMED_POWER_FAULT` means power failure.\n## Verification\nCheck stable uptime.', source_type: 'analysis_skill'
  } })
  expect(created.ok()).toBeTruthy()
  const document = await created.json()
  await page.goto('/knowledge')
  const row = page.locator('.el-table__row').filter({ hasText: title }).first()
  await row.getByRole('button', { name: '解析与质量', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '知识解析与质量' })
  await expect(dialog).toContainText('HUMAN_CONFIRMED_POWER_FAULT')
  await dialog.getByRole('button', { name: '确认为人工核实资料并发布' }).click()
  await page.getByRole('button', { name: 'OK', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect.poll(async () => {
    const current = await (await request.get(`${api}/knowledge/${document.id}`)).json()
    return current.active && current.trust_level === 'HIGH'
  }, { timeout: 60_000 }).toBe(true)
})
