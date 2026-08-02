import { test, expect } from '@playwright/test'

// Change a quality threshold, save, reload, and confirm it persisted server-side.
// getByLabel works here because the Quality Thresholds fields carry real label/input
// associations — as do the API Keys rows below, since SecretField adopted the same
// `useId` + `htmlFor` pattern.
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

  // The row is a labelled group, so scoping to it needs no DOM walk — which matters
  // because all three rows carry a "Save" button with the same accessible name.
  const row = page.getByRole('group', { name: 'Gelbooru user ID' })
  await row.getByLabel('Gelbooru user ID').fill('e2e-user-9876')
  await row.getByRole('button', { name: 'Save', exact: true }).click()

  await expect(row.getByText(/Saved here/)).toBeVisible()
  await expect(row.getByText('*********9876')).toBeVisible()

  await row.getByRole('button', { name: 'Clear' }).click()
  await expect(row.getByText(/Saved here/)).toBeHidden()
})
