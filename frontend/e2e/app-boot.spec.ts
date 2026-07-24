import { test, expect } from '@playwright/test'

// The SPA boots and the API is alive. If this fails, nothing else can pass —
// it isolates "server/build is broken" from "a journey is broken".
test('app boots and health is green', async ({ page, request }) => {
  const health = await request.get('/api/v1/health')
  expect(health.status()).toBe(200)
  expect((await health.json()).status).toBe('ok')

  await page.goto('/')
  // Redirects /  →  /datasets; the always-mounted sidebar link proves the shell rendered.
  await expect(page.getByRole('link', { name: 'Datasets' })).toBeVisible()
  await expect(page).toHaveURL(/\/datasets$/)
})
