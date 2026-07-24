import { defineConfig, devices } from '@playwright/test'

// E2E smoke suite. One worker: the specs share a single backend + SQLite DB, so
// they must not run concurrently; each journey uses uniquely-named datasets to
// stay independent. Port 8199 (not :8000) avoids colliding with a running dev
// session, and same-origin serving sidesteps the backend's hardcoded CORS.
const PORT = process.env.E2E_PORT ?? '8199'
const baseURL = `http://localhost:${PORT}`

export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'bash e2e/serve.sh',
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    // Piped output turns an opaque "webServer timeout" into a readable traceback
    // (e.g. a missing backend dep surfaces here, not as a bare timeout).
    stdout: 'pipe',
    stderr: 'pipe',
    env: { E2E_PORT: PORT },
  },
})
