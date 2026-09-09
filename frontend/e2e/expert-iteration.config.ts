import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'
const frontend = resolve(import.meta.dirname, '..')
const output = resolve(frontend, 'node_modules/.cache/expert-iteration-20260909', process.env.EXPERT_TEST_RUN || '.')
export default defineConfig({
  testDir: import.meta.dirname, testMatch: 'expert-iteration.spec.ts', fullyParallel: false, workers: 1,
  timeout: 30000, expect: { timeout: 6000 }, retries: 0,
  outputDir: resolve(output, 'results'),
  reporter: [['list'], ['junit', { outputFile: resolve(output, 'frontend-e2e.xml') }]],
  use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 }, baseURL: 'http://127.0.0.1:15244', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: { command: 'npm run preview -- --port 15244 --strictPort', cwd: frontend, url: 'http://127.0.0.1:15244', timeout: 30000, reuseExistingServer: false }
})
