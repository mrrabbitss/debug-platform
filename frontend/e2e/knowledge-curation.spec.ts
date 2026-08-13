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
  if (existing) {
    const activated = await request.post(`${apiUrl}/system/models/${existing.id}/activate`)
    expect(activated.ok(), await activated.text()).toBeTruthy()
    return existing.id
  }
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


async function waitForJob(request: APIRequestContext, jobId: string): Promise<any> {
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    const response = await request.get(`${apiUrl}/jobs/${jobId}`)
    expect(response.ok(), await response.text()).toBeTruthy()
    const job = await response.json()
    if (['COMPLETED', 'FAILED', 'CANCELLED', 'DEAD_LETTER'].includes(job.status)) {
      expect(job.status, job.error_message || job.dead_letter_reason || JSON.stringify(job)).toBe('COMPLETED')
      return job
    }
    await new Promise(resolvePromise => setTimeout(resolvePromise, 250))
  }
  throw new Error(`Job ${jobId} did not reach a terminal state`)
}


async function publishSyntheticDiagnosticMethod(request: APIRequestContext): Promise<string> {
  const created = await request.post(`${apiUrl}/knowledge`, {
    data: {
      title: 'Synthetic E2E authentication screening method',
      source_type: 'analysis_skill',
      device_type: 'AP',
      module: 'AUTH',
      trust_level: 'HIGH',
      confidentiality: 'INTERNAL',
      content: [
        '# Synthetic authentication screening method',
        '',
        '## 必查日志关键词',
        '',
        '- `authentication failed`',
        '- `DHCP discover timeout`',
        '- `get WLANConfiguration!`',
        '',
        '## 排查步骤',
        '',
        '1. Compare the authentication configuration with the approved baseline.',
        '2. Record supporting and contradicting evidence IDs.'
      ].join('\n')
    }
  })
  expect(created.ok(), await created.text()).toBeTruthy()
  let document = await created.json()
  const submitted = await request.post(`${apiUrl}/knowledge/${document.id}/review/submit`, {
    data: { expected_lock_version: document.lock_version, comment: 'Synthetic E2E review' }
  })
  expect(submitted.ok(), await submitted.text()).toBeTruthy()
  document = await submitted.json()
  const approved = await request.post(`${apiUrl}/knowledge/${document.id}/review/approve`, {
    data: { expected_lock_version: document.lock_version, comment: 'Synthetic E2E approval' }
  })
  expect(approved.ok(), await approved.text()).toBeTruthy()
  expect((await approved.json()).review_status).toBe('ACTIVE')
  return document.id
}


async function waitForCompletedTriage(
  request: APIRequestContext,
  caseId: string,
  artifactId: string
): Promise<any> {
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    const response = await request.get(`${apiUrl}/cases/${caseId}/log-triage`, {
      params: { artifact_id: artifactId }
    })
    expect(response.ok(), await response.text()).toBeTruthy()
    const triage = await response.json()
    if (triage?.status === 'COMPLETED') return triage
    if (triage && ['FAILED', 'CANCELLED'].includes(triage.status)) {
      throw new Error(triage.error_message || `Log triage ended as ${triage.status}`)
    }
    await new Promise(resolvePromise => setTimeout(resolvePromise, 250))
  }
  throw new Error('Automatic log triage did not complete')
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


test('visualizes LLM log planning, multi-round diagnosis and recoverable case chat', async ({ page, request }) => {
  const consoleErrors: string[] = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', error => consoleErrors.push(`pageerror: ${error.message}`))

  await ensureGoldenModel(request)
  await publishSyntheticDiagnosticMethod(request)
  const createdCase = await request.post(`${apiUrl}/cases`, {
    data: {
      title: 'Synthetic E2E authentication failure',
      device_type: 'AP',
      device_model: 'GOLDEN-AP-01',
      description: 'Clients fail authentication during the 4-way handshake.',
      reproduction_steps: 'Associate a synthetic client with a mismatched shared key.',
      model_egress_approved: true
    }
  })
  expect(createdCase.ok(), await createdCase.text()).toBeTruthy()
  const caseItem = await createdCase.json()

  const uploaded = await request.post(`${apiUrl}/cases/${caseItem.id}/artifacts`, {
    multipart: {
      kind: 'debug_log',
      file: {
        name: 'collectDebuginfo_e2e',
        mimeType: 'text/plain',
        buffer: readFileSync(resolve(corpusRoot, 'logs', 'collectDebuginfo_golden'))
      }
    }
  })
  expect(uploaded.ok(), await uploaded.text()).toBeTruthy()
  const artifact = await uploaded.json()
  expect(artifact.original_name).toBe('collectDebuginfo_e2e.txt')

  const parseResponse = await request.post(
    `${apiUrl}/cases/${caseItem.id}/artifacts/${artifact.id}/parse`
  )
  expect(parseResponse.ok(), await parseResponse.text()).toBeTruthy()
  await waitForJob(request, (await parseResponse.json()).id)
  const triage = await waitForCompletedTriage(request, caseItem.id, artifact.id)
  expect(triage.plan.planner_mode).toBe('llm')
  expect(triage.method_coverage.all_required_read).toBe(true)
  expect(triage.summary.raw_text_scan_completed).toBe(true)

  const methodEvidence = await request.get(
    `${apiUrl}/cases/${caseItem.id}/log-triage/${triage.id}/evidence`,
    { params: { bucket: 'METHOD_REQUIRED', limit: 100 } }
  )
  expect(methodEvidence.ok(), await methodEvidence.text()).toBeTruthy()
  expect((await methodEvidence.json()).items.some(
    (item: any) => item.message.includes('get WLANConfiguration!') && item.line_start === 5
  )).toBe(true)

  const analysisResponse = await request.post(`${apiUrl}/cases/${caseItem.id}/analyses`)
  expect(analysisResponse.ok(), await analysisResponse.text()).toBeTruthy()
  await waitForJob(request, (await analysisResponse.json()).id)
  const analysesResponse = await request.get(`${apiUrl}/cases/${caseItem.id}/analyses`)
  expect(analysesResponse.ok(), await analysesResponse.text()).toBeTruthy()
  const analysis = (await analysesResponse.json()).find((item: any) => item.status === 'COMPLETED')
  expect(analysis?.agent_run_id).toBeTruthy()
  const diagnosis = JSON.parse(analysis.result_json)
  expect(diagnosis.diagnostic_planning.rounds).toHaveLength(2)
  expect(diagnosis.diagnostic_planning.stop_reason).toBe('ENOUGH_EVIDENCE')

  await page.goto(`/cases/${caseItem.id}`)
  await page.getByRole('tab', { name: '智能日志筛查' }).click()
  await expect(page.getByTestId('log-triage-status')).toContainText('COMPLETED')
  await expect(page.getByRole('tab', { name: /① LLM 判断相关/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /② 方法文档强制检查/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: /③ 其他日志事件/ })).toBeVisible()
  await expect(page.getByRole('tabpanel', { name: /① LLM 判断相关/ })).toContainText(
    'authentication failed during 4-way handshake'
  )
  await page.getByRole('tab', { name: /② 方法文档强制检查/ }).click()
  await expect(page.getByRole('tabpanel', { name: /② 方法文档强制检查/ })).toContainText(
    'get WLANConfiguration!'
  )
  const triageTrace = page.getByTestId('planning-trace').filter({
    hasText: '日志 LLM Planning 轨迹'
  })
  await expect(triageTrace).toContainText(
    /Tokens：[1-9]\d*（输入 [1-9]\d* \/ 输出 [1-9]\d*）/
  )

  await page.getByRole('tab', { name: '综合诊断' }).click()
  const diagnosisTrace = page.getByTestId('planning-trace').filter({
    hasText: '综合诊断多轮 LLM Planning 轨迹'
  })
  await expect(diagnosisTrace).toContainText('llm_planning_round_1')
  await expect(diagnosisTrace).toContainText('llm_planning_round_2')
  await expect(diagnosisTrace).toContainText('第 2 轮 · ENOUGH_EVIDENCE')
  await expect(diagnosisTrace).toContainText(
    /Tokens：[1-9]\d*（输入 [1-9]\d* \/ 输出 [1-9]\d*）/
  )
  await expect(page.getByText('Synthetic evidence-constrained comprehensive diagnosis completed.')).toBeVisible()

  await page.getByRole('tab', { name: '交互问答' }).click()
  await page.getByPlaceholder(/为什么认为这个根因成立/).fill('What evidence supports the current hypothesis?')
  await page.getByRole('button', { name: '发送到后台' }).click()
  await expect(page.locator('[data-testid="case-chat-message"][data-role="assistant"]')).toContainText(
    'Synthetic asynchronous answer completed',
    { timeout: 30_000 }
  )
  await expect(page.getByTestId('planning-trace').filter({ hasText: '本轮问答轨迹' })).toContainText('COMPLETED')

  await page.locator('.el-radio-button').filter({ hasText: '修订诊断与报告' }).click()
  await page.getByPlaceholder(/把 AP 离线调整为第二根因/).fill(
    'Add explicit primary GW and secondary AP cross-device verification to the diagnosis and report.'
  )
  await page.getByRole('button', { name: '发送到后台' }).click()
  const revisionPanel = page.getByTestId('analysis-revisions')
  await expect(revisionPanel).toContainText('Added explicit GW/AP cross-device verification.', {
    timeout: 30_000
  })
  await revisionPanel.locator('.el-collapse-item__header').first().click()
  await expect(revisionPanel).toContainText('Synthetic human-requested GW/AP joint diagnosis revision.')
  await revisionPanel.getByTestId('apply-analysis-revision').click()
  await expect(revisionPanel).toContainText('APPLIED')

  await page.getByRole('tab', { name: '综合诊断' }).click()
  await expect(page.getByRole('tabpanel', { name: '综合诊断' }).getByText(
    'Synthetic human-requested GW/AP joint diagnosis revision.'
  )).toBeVisible()
  await page.getByRole('tab', { name: '诊断报告' }).click()
  const reportFrame = page.locator('iframe.report-frame')
  await expect(reportFrame).toBeVisible()
  await expect(reportFrame.contentFrame().getByText(
    'Synthetic human-requested GW/AP joint diagnosis revision.'
  )).toBeVisible()
  expect(consoleErrors).toEqual([])
})
