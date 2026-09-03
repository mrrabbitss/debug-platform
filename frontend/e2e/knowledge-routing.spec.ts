import { expect, test, type APIRequestContext } from '@playwright/test'


const backendUrl = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:18080'
const fakeModelUrl = process.env.E2E_FAKE_MODEL_URL || 'http://127.0.0.1:18081'
const fakeApiCredential = process.env.E2E_FAKE_API_CREDENTIAL
const apiUrl = `${backendUrl}/api/v1`

if (!fakeApiCredential) throw new Error('E2E fake API credential was not initialized')


async function ensureRoutingModel(request: APIRequestContext): Promise<string> {
  const listed = await request.get(`${apiUrl}/system/models`, {
    params: { task_type: 'chat' }
  })
  expect(listed.ok(), await listed.text()).toBeTruthy()
  const existing = (await listed.json()).find((item: any) => item.name === 'Knowledge Routing E2E Chat')
  if (existing) {
    const activated = await request.post(`${apiUrl}/system/models/${existing.id}/activate`)
    expect(activated.ok(), await activated.text()).toBeTruthy()
    return existing.id
  }
  const created = await request.post(`${apiUrl}/system/models`, {
    data: {
      name: 'Knowledge Routing E2E Chat',
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


test('classifies multiple Markdown files into different governed DRAFT categories', async ({ page, request }) => {
  const consoleErrors: string[] = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', error => consoleErrors.push(`pageerror: ${error.message}`))

  await ensureRoutingModel(request)
  const unique = Date.now().toString(36)
  const faultTreeTitle = `E2E AP offline fault tree ${unique}`
  const protocolTitle = `E2E GW AP protocol rule ${unique}`

  await page.goto('/knowledge')
  await page.getByTestId('knowledge-routing-open').click()
  await expect(page.getByTestId('knowledge-routing-submit')).toBeEnabled()
  await page.getByTestId('knowledge-routing-files').setInputFiles([
    {
      name: `ap-offline-fault-tree-${unique}.md`,
      mimeType: 'text/markdown',
      buffer: Buffer.from([
        `# ${faultTreeTitle}`,
        '',
        '## 故障树',
        '',
        'AP 频繁离线时，按 root cause branch 检查供电、回程链路和心跳。',
        '每个分支都需要支持证据、反证和下一步。'
      ].join('\n'))
    },
    {
      name: `gw-ap-protocol-rule-${unique}.markdown`,
      mimeType: 'text/markdown',
      buffer: Buffer.from([
        `# ${protocolTitle}`,
        '',
        '## 协议诊断规则',
        '',
        '这是 GW 与 AP 的 WLAN protocol 规则，用于核对 CAPWAP keepalive 和重连状态机。'
      ].join('\n'))
    }
  ])
  await page.getByTestId('knowledge-routing-egress-consent').click()
  await page.getByTestId('knowledge-routing-submit').click()

  const results = page.getByTestId('knowledge-routing-results')
  await expect(results.locator('.el-table__row')).toHaveCount(2)
  await expect(results).toContainText('故障树', { timeout: 90_000 })
  await expect(results).toContainText('协议诊断规则', { timeout: 90_000 })
  await expect(results.getByText('草稿')).toHaveCount(2)

  const listed = await request.get(`${apiUrl}/knowledge`, { params: { limit: 1000 } })
  expect(listed.ok(), await listed.text()).toBeTruthy()
  const documents = await listed.json()
  const faultTree = documents.find((item: any) => item.title === faultTreeTitle)
  const protocol = documents.find((item: any) => item.title === protocolTitle)
  expect(faultTree).toMatchObject({
    review_status: 'DRAFT',
    active: false,
    source_type: 'fault_tree'
  })
  expect(faultTree.metadata.knowledge_routing.category_code).toBe('history.fault_trees')
  expect(protocol).toMatchObject({
    review_status: 'DRAFT',
    active: false,
    source_type: 'protocol_rule'
  })
  expect(protocol.metadata.knowledge_routing.category_code).toBe('diagnosis.protocol_rules')
  expect(faultTree.category_id).not.toBe(protocol.category_id)
  expect(consoleErrors).toEqual([])
})
