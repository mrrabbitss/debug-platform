import { expect, test } from '@playwright/test'


test('imports and presents the recorded successful GLM-5.2 AP offline demo', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', error => consoleErrors.push(error.message))

  await page.goto('/cases')
  const importButton = page.getByTestId('import-ap-offline-demo')
  await expect(importButton).toBeVisible()
  await importButton.click()
  await expect(page).toHaveURL(/\/cases\/CASE-DEMO-AP-OFFLINE-/)
  await expect(page.getByTestId('demo-case-banner')).toBeVisible()
  await expect(page.getByTestId('demo-case-banner')).toContainText('真实 GLM-5.2 历史成功结果')
  await expect(page.getByTestId('demo-case-banner')).toContainText('导入快照时不会再次出站')

  await page.getByRole('tab', { name: '智能日志筛查' }).click()
  const triagePanel = page.getByTestId('log-triage-panel')
  await expect(triagePanel.getByTestId('demo-triage-snapshot')).toBeVisible()
  await expect(triagePanel.getByTestId('demo-triage-snapshot')).toContainText('每组默认折叠')
  await expect(triagePanel.getByRole('tab', { name: /① LLM 判断相关 \([1-9]/ })).toBeVisible()
  await expect(triagePanel.getByRole('tab', { name: /② 方法文档强制检查 \([1-9]/ })).toBeVisible()
  await expect(triagePanel.getByRole('tab', { name: /③ 其他日志事件 \([1-9]/ })).toBeVisible()
  await expect(triagePanel.getByText('glm-5.2', { exact: false }).first()).toBeVisible()

  await triagePanel.getByRole('button', { name: '首个位置' }).first().click()
  await expect(page.getByRole('tab', { name: '日志浏览' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.log-line.target')).toBeVisible()
  await expect(page.locator('.log-line.target')).toContainText(/udm|advertisements|listen port|curTime|CtrlPointVerify|Status=\[0\]/i)

  await page.getByRole('tab', { name: '综合诊断' }).click()
  await expect(page.getByTestId('demo-diagnosis-snapshot')).toBeVisible()
  await expect(page.getByTestId('demo-planning-snapshot')).toBeVisible()
  await page.getByText('故障树逐节点结论', { exact: true }).click()
  await expect(page.getByText('场景2', { exact: true })).toBeVisible()
  await expect(page.getByText('场景1', { exact: true })).toBeVisible()
  await expect(page.getByText(/AP 侧 UDM 进程\/协议栈异常/).first()).toBeVisible()

  await page.getByRole('tab', { name: '诊断报告' }).click()
  const report = page.locator('iframe[title="诊断报告预览"]')
  await expect(report).toBeVisible()
  await expect(report.contentFrame().getByText(/GW_collectDebuginfo_demo\.txt - 第/).first()).toBeVisible()
  await expect(report.contentFrame().getByText(/AP_collectDebuginfo_demo\.txt - 第/).first()).toBeVisible()

  expect(consoleErrors).toEqual([])
})
