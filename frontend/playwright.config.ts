import { randomBytes } from 'node:crypto'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig, devices } from '@playwright/test'


const frontendRoot = resolve(import.meta.dirname)
const repositoryRoot = resolve(frontendRoot, '..')
const runtimeRoot = mkdtempSync(join(tmpdir(), 'gwap-playwright-'))
const backendPort = Number(process.env.E2E_BACKEND_PORT || 18080)
const fakeModelPort = Number(process.env.E2E_FAKE_MODEL_PORT || 18081)
const frontendPort = Number(process.env.E2E_FRONTEND_PORT || 15173)
const backendUrl = `http://127.0.0.1:${backendPort}`
const fakeModelUrl = `http://127.0.0.1:${fakeModelPort}`
const frontendUrl = `http://127.0.0.1:${frontendPort}`
const fakeApiCredential = `e2e-${randomBytes(18).toString('hex')}`
const modelSecretKey = `${randomBytes(32).toString('base64url')}=`
const browserChannel = process.env.E2E_BROWSER_CHANNEL
const python = process.env.E2E_PYTHON || (
  process.platform === 'win32'
    ? resolve(repositoryRoot, '.venv', 'Scripts', 'python.exe')
    : 'python'
)
const quote = (value: string) => process.platform === 'win32'
  ? `"${value}"`
  : `'${value.replaceAll("'", "'\\''")}'`
const normalizedRuntime = runtimeRoot.replaceAll('\\', '/')

process.env.E2E_BACKEND_URL = backendUrl
process.env.E2E_FAKE_MODEL_URL = fakeModelUrl
process.env.E2E_FRONTEND_URL = frontendUrl
process.env.E2E_FAKE_API_CREDENTIAL = fakeApiCredential

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  outputDir: resolve(repositoryRoot, 'artifacts', 'e2e', 'test-results'),
  reporter: [
    ['list'],
    ['junit', { outputFile: resolve(repositoryRoot, 'artifacts', 'e2e', 'playwright.xml') }],
    ['html', { outputFolder: resolve(repositoryRoot, 'artifacts', 'e2e', 'playwright-report'), open: 'never' }]
  ],
  use: {
    baseURL: frontendUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off'
  },
  projects: [
    {
      name: browserChannel || 'chromium',
      use: { ...devices['Desktop Chrome'], ...(browserChannel ? { channel: browserChannel } : {}) }
    }
  ],
  webServer: [
    {
      command: `${quote(python)} backend/tests/fake_openai_server.py --host 127.0.0.1 --port ${fakeModelPort}`,
      cwd: repositoryRoot,
      url: `${fakeModelUrl}/healthz`,
      timeout: 60_000,
      reuseExistingServer: false
    },
    {
      command: `${quote(python)} -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port ${backendPort}`,
      cwd: repositoryRoot,
      url: backendUrl,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        APP_ENV: 'test',
        AUTH_MODE: 'local',
        LLM_PROVIDER: 'mock',
        DATABASE_URL: `sqlite:///${normalizedRuntime}/e2e.db`,
        STORAGE_ROOT: `${normalizedRuntime}/storage`,
        MODEL_ENDPOINT_ALLOWLIST: '127.0.0.1,localhost',
        MODEL_SECRET_KEY: modelSecretKey,
        CORS_ORIGINS: frontendUrl
      }
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      cwd: frontendRoot,
      url: frontendUrl,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        VITE_BACKEND_PROXY: backendUrl
      }
    }
  ]
})
