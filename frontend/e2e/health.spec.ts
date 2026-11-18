import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The Health page (M12 phase 1): open the report, dismiss a finding with a reason,
// see it move to Dismissed and into the notes, then open another finding's images.
test('health report: dismiss a finding and open another one\'s images', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-health-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'h1.png')
  await uploadViaApi(request, ds.id, 'h2.png')

  await page.goto(`/datasets/${ds.id}/health`)
  await expect(page.getByRole('heading', { name: 'Health', exact: true })).toBeVisible()

  // Fresh uploads have no captions, and no goal is set.
  const uncaptioned = page.getByTestId('finding-uncaptioned')
  await expect(uncaptioned).toBeVisible()
  await expect(uncaptioned).toContainText('2 of 2 images')
  await expect(page.getByTestId('finding-no_goal')).toBeVisible()

  await uncaptioned.getByRole('button', { name: 'Dismiss…' }).click()
  const dialog = page.getByRole('dialog', { name: /Dismiss/ })
  await dialog.getByLabel('Reason').fill('captions come from the next run')
  await dialog.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(dialog).toHaveCount(0)

  await expect(page.getByTestId('finding-uncaptioned')).toHaveCount(0)
  const dismissed = page.getByTestId('dismissed')
  await dismissed.locator('summary').click()
  await expect(dismissed).toContainText('captions come from the next run')
  // The dismissal is a preference note.
  const preferences = page.getByRole('region', { name: 'Preferences' })
  await expect(preferences.getByTestId('note-item')).toContainText('captions come from the next run')

  // Another finding's images, in the modal. The uploads are 16 px, under the default target.
  const lowRes = page.getByTestId('finding-low_resolution')
  await lowRes.getByRole('button', { name: 'Show images' }).click()
  const modalTitle = page.getByRole('heading', { level: 3, name: /below the target resolution/ })
  await expect(modalTitle).toBeVisible()
  // The modal's own count line, under its title (the row above says "2 of 2 images").
  await expect(modalTitle.locator('xpath=..')).toContainText('2 images')
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(2)

  // Restoring the dismissal brings the finding back.
  await page.keyboard.press('Escape')
  await dismissed.getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByTestId('finding-uncaptioned')).toBeVisible()
})

// The Health sidebar entry and the badge's source: a report built for a dataset
// stores its counts, and the Datasets card shows the worst level.
test('a built report puts a health badge on the dataset card', async ({ page, request }) => {
  const name = `e2e-health-badge-${Date.now()}`
  const ds = await createDatasetViaApi(request, name)
  await uploadViaApi(request, ds.id, 'b.png')
  // Building the report (here through the API) writes the card's summary.
  const report = await (await request.get(`/api/v1/datasets/${ds.id}/health`)).json()
  expect(report.summary.warn).toBeGreaterThan(0)

  await page.goto('/datasets')
  const card = page.getByTestId(`dataset-card-${ds.id}`)
  await expect(card.getByTestId('health-badge')).toContainText('warning')
})
