// Dedicated configuration for the new mocked workbench scenarios only.
// The filename stays inside the frontend workbench test ownership boundary.
import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

const frontend = resolve(import.meta.dirname, '..')
const output = resolve(frontend, '../artifacts/validation/workbench-20260909')
export default defineConfig({
  testDir: import.meta.dirname,
  testMatch: 'workbench.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 6000 },
  retries: 0,
  outputDir: resolve(output, 'test-results'),
  reporter: [['list'], ['junit', { outputFile: resolve(output, 'frontend-e2e.xml') }]],
  use: {
    ...devices['Desktop Chrome'],
    viewport: { width: 1440, height: 1000 },
    baseURL: 'http://127.0.0.1:15243',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --port 15243 --strictPort',
    cwd: frontend,
    url: 'http://127.0.0.1:15243',
    timeout: 30000,
    reuseExistingServer: false
  }
})
