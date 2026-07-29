import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Leaving the gallery for the detail view and coming back must land on the page
// the user was on, not page 1.
test('gallery returns to the page it was left on', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `restore-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  // Two images per page, so page 2 exists without seeding a hundred files.
  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  await page.getByRole('button', { name: 'Next →' }).click()
  await expect(page.getByText('Page 2')).toBeVisible()
  // Past the debounce window that the mount effects run on.
  await page.waitForTimeout(1000)
  await expect(page.getByText('Page 2')).toBeVisible()

  await page.getByTestId('gallery-tile').first().click()
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()

  await expect(page.getByText('Page 2')).toBeVisible()
  await page.waitForTimeout(1000)
  await expect(page.getByText('Page 2')).toBeVisible()
})
