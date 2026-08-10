import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'


const repositoryRoot = resolve(import.meta.dirname, '..', '..')
const corpusRoot = resolve(repositoryRoot, 'sample_data', 'golden_incident')
const backendUrl = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:18080'
const fakeModelUrl = process.env.E2E_FAKE_MODEL_URL || 'http://127.0.0.1:18081'
const fakeApiCredential = process.env.E2E_FAKE_API_CREDENTIAL
const apiUrl = `${backendUrl}/api/v1`

if (!fakeApiCredential) throw new Error('E2E fake API credential was not initialized')


async function ensureGoldenModel(request: APIRequestContext): Promise<string> {
  const listed = await request.get(`${apiUrl}/system/models`, {
    params: { task_type: 'chat' }
  })
  expect(listed.ok()).toBeTruthy()
  const existing = (await listed.json()).find((item: any) => item.name === 'Golden E2E Chat')
  if (existing) return existing.id
  const created = await request.post(`${apiUrl}/system/models`, {
    data: {
      name: 'Golden E2E Chat',
      task_type: 'chat',
      mode: 'api',
      provider: 'openai_compatible',
      model_name: 'golden-curation',
      base_url: `${fakeModelUrl}/v1`,
      api_key: fakeApiCredential,
      config: { temperature: 0, timeout_seconds: 10, max_retries: 0 },
      enabled: true
    }
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const profile = await created.json()
  const activated = await request.post(`${apiUrl}/system/models/${profile.id}/activate`)
  expect(activated.ok(), await activated.text()).toBeTruthy()
  return profile.id
}


async function waitForStatus(page: Page, status: string): Promise<void> {
  await expect(page.getByTestId('curation-status')).toHaveAttribute('data-status', status, {
    timeout: 60_000
  })
}


test('uploads, previews, curates, corrects and confirms a DRAFT with no console errors', async ({ page, request }) => {
  const consoleErrors: string[] = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', error => consoleErrors.push(`pageerror: ${error.message}`))

  await ensureGoldenModel(request)
  await page.goto('/knowledge/curation')
  await expect(page.getByTestId('curation-open-create')).toBeEnabled()
  await page.getByTestId('curation-open-create').click()

  await page.getByTestId('curation-folder-input').setInputFiles(resolve(corpusRoot, 'case'))
  await page.getByTestId('curation-egress-consent').click()
  await page.getByTestId('curation-submit').click()
  await waitForStatus(page, 'REVIEWING')

  await page.locator('#tab-sources').click()
  const sessionListResponse = await request.get(`${apiUrl}/knowledge-curations`)
  expect(sessionListResponse.ok()).toBeTruthy()
  const currentSession = (await sessionListResponse.json())[0]
  const sessionDetailResponse = await request.get(`${apiUrl}/knowledge-curations/${currentSession.id}`)
  expect(sessionDetailResponse.ok()).toBeTruthy()
  const sessionDetail = await sessionDetailResponse.json()
  const sourceRefByName = new Map<string, string>(
    sessionDetail.sources.map((source: any) => [source.relative_path.split('/').at(-1), source.source_ref])
  )
  const previews = [
    ['error.txt', 'AUTH_TIMEOUT'],
    ['analysis.html', 'shared-key mismatch'],
    ['solution.docx', 'Back up the current WLAN configuration'],
    ['validation.pdf', 'Authentication succeeds']
  ] as const
  for (const [filename, expectedText] of previews) {
    const sourceRef = sourceRefByName.get(filename)
    expect(sourceRef, `missing uploaded source ${filename}`).toBeTruthy()
    await page.getByTestId(`source-preview-${sourceRef}`).click()
    await expect(page.getByTestId('curation-source-preview')).toContainText(expectedText)
    await expect(page.getByTestId('curation-source-preview')).not.toContainText('INVISIBLE_PROMPT_INJECTION')
    await page.keyboard.press('Escape')
  }

  await page.locator('#tab-draft').click()
  const editor = page.getByTestId('curation-draft-editor')
  await expect(editor).toHaveValue(/Synthetic AP authentication timeout case/)
  const validationRef = sourceRefByName.get('validation.pdf')
  expect(validationRef).toBeTruthy()
  await expect(editor).toHaveValue(new RegExp(`\\[${validationRef}:L1-L3\\]`))

  await page.getByTestId('curation-chat-instruction').fill(
    'E2E_CORRECTION_ADD_PEER_REVIEW: require a peer-review evidence-boundary note.'
  )
  await page.getByTestId('curation-send-correction').click()
  await expect(editor).toHaveValue(/A peer reviewer must confirm the evidence boundary/)

  await page.getByTestId('curation-confirm').click()
  await page.locator('.el-message-box__btns .el-button--primary').click()
  await waitForStatus(page, 'CONFIRMED')

  const sessionsResponse = await request.get(`${apiUrl}/knowledge-curations`)
  expect(sessionsResponse.ok()).toBeTruthy()
  const sessions = await sessionsResponse.json()
  const session = sessions.find((item: any) => item.id === currentSession.id)
  expect(session?.knowledge_document_id).toBeTruthy()
  const knowledgeResponse = await request.get(`${apiUrl}/knowledge/${session.knowledge_document_id}`)
  expect(knowledgeResponse.ok(), await knowledgeResponse.text()).toBeTruthy()
  const knowledge = await knowledgeResponse.json()
  expect(knowledge.review_status).toBe('DRAFT')
  expect(knowledge.active).toBe(false)

  await page.goto('/agent-runs')
  await expect(page.getByTestId('agent-runs-table')).toContainText('knowledge_curation')
  await expect(page.getByTestId('agent-runs-table')).toContainText('HUMAN_CONFIRMED_DRAFT')

  expect(consoleErrors).toEqual([])
  expect(readFileSync(resolve(corpusRoot, 'case', 'analysis.html'), 'utf8')).toContain(
    "throw new Error('HTML scripts must never execute')"
  )
})


test('configures and clears an encrypted Chat proxy without exposing credentials', async ({ page, request }) => {
  const consoleErrors: string[] = []
  page.on('console', message => {
    if (message.type() === 'error' || message.type() === 'warning') {
      consoleErrors.push(`${message.type()}: ${message.text()}`)
    }
  })
  page.on('pageerror', error => consoleErrors.push(`pageerror: ${error.message}`))

  const created = await request.post(`${apiUrl}/system/models`, {
    data: {
      name: 'Proxy Settings E2E Chat',
      task_type: 'chat',
      mode: 'api',
      provider: 'openai_compatible',
      model_name: 'proxy-settings-e2e',
      base_url: `${fakeModelUrl}/v1`,
      api_key: fakeApiCredential,
      config: { temperature: 0, timeout_seconds: 10, max_retries: 0 },
      enabled: true
    }
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  const profile = await created.json()

  try {
    await page.goto('/settings')
    let row = page.locator('.el-table__row').filter({ hasText: 'Proxy Settings E2E Chat' })
    await expect(row).toContainText('直连')
    await row.getByRole('button', { name: '修改' }).click()
    await page.getByTestId('model-proxy-url').fill(
      'http://proxy-user:proxy-secret@proxy.example.com:8080'
    )
    await page.getByTestId('model-profile-save').click()

    row = page.locator('.el-table__row').filter({ hasText: 'Proxy Settings E2E Chat' })
    await expect(row).toContainText('http://proxy.example.com:8080')
    await expect(page.locator('body')).not.toContainText('proxy-user')
    await expect(page.locator('body')).not.toContainText('proxy-secret')

    let listed = await request.get(`${apiUrl}/system/models`, {
      params: { task_type: 'chat' }
    })
    let saved = (await listed.json()).find((item: any) => item.id === profile.id)
    expect(saved.proxy_url_configured).toBe(true)
    expect(saved.proxy_url_hint).toBe('http://proxy.example.com:8080')
    expect(JSON.stringify(saved)).not.toContain('proxy-secret')

    await row.getByRole('button', { name: '修改' }).click()
    await page.getByTestId('clear-model-proxy').click()
    await page.getByTestId('model-profile-save').click()
    row = page.locator('.el-table__row').filter({ hasText: 'Proxy Settings E2E Chat' })
    await expect(row).toContainText('直连')

    listed = await request.get(`${apiUrl}/system/models`, {
      params: { task_type: 'chat' }
    })
    saved = (await listed.json()).find((item: any) => item.id === profile.id)
    expect(saved.proxy_url_configured).toBe(false)
    expect(consoleErrors).toEqual([])
  } finally {
    await request.delete(`${apiUrl}/system/models/${profile.id}`)
  }
})
