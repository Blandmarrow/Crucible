import { test, expect } from '@playwright/test'

// Change a quality threshold, save, reload, and confirm it persisted server-side.
// The Quality Thresholds fields are the one place in the app with real
// label/input associations, so getByLabel is reliable here.
test('a quality threshold persists across reload', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Quality Thresholds' }).click()

  const field = page.getByLabel('Blur threshold')
  await field.fill('137.5')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Thresholds saved')).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'Quality Thresholds' }).click()
  await expect(page.getByLabel('Blur threshold')).toHaveValue('137.5')
})

// Save a key on the API Keys tab, then clear it. Asserted on the presence and absence of
// "Saved here" rather than on "Not set": if the shell running the tests exports HF_TOKEN,
// the backend inherits it and the cleared state is "Inherited from .env", not "Not set".
test('an API key saves and clears', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'API Keys' }).click()

  const row = page.locator('div').filter({ hasText: /^Gelbooru user ID$/ }).first().locator('..')
  await row.getByPlaceholder('Leave blank to keep the current value').fill('e2e-user-9876')
  await row.getByRole('button', { name: 'Save', exact: true }).click()

  await expect(row.getByText(/Saved here/)).toBeVisible()
  await expect(row.getByText('*********9876')).toBeVisible()

  await row.getByRole('button', { name: 'Clear' }).click()
  await expect(row.getByText(/Saved here/)).toBeHidden()
})
