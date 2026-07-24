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
