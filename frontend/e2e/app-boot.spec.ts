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

// The running version sits under the app's name on every page, linked to its
// release notes, and Settings shows the same one (decision 19).
test('the sidebar and Settings show the version the server reports', async ({ page, request }) => {
  const { version } = await (await request.get('/api/v1/health')).json()
  expect(version).toMatch(/^\d+\.\d+\.\d+$/)
  const notes = `https://github.com/Blandmarrow/Crucible/releases/tag/v${version}`

  await page.goto('/datasets')
  const link = page.getByTestId('sidebar-version')
  await expect(link).toHaveText(`v${version}`)
  await expect(link).toHaveAttribute('href', notes)

  await page.getByRole('link', { name: 'Settings' }).click()
  await expect(page.getByText(`Crucible ${version}`)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Release notes' })).toHaveAttribute('href', notes)
})
