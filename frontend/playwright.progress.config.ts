import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e', testMatch: 'model-progress.spec.ts', workers: 1, retries: 0,
  timeout: 40_000, expect: { timeout: 10_000 },
  outputDir: '../artifacts/validation/model-progress-20260910/browser-results',
  reporter: [['list'], ['json', { outputFile: '../artifacts/validation/model-progress-20260910/browser-results.json' }]],
  use: { baseURL: 'http://127.0.0.1:16841', headless: true, viewport: { width: 1360, height: 1000 }, screenshot: 'only-on-failure' },
  webServer: { command: 'npm run dev -- --host 127.0.0.1 --port 16841 --strictPort',
    url: 'http://127.0.0.1:16841', reuseExistingServer: false, timeout: 30_000 }
})
