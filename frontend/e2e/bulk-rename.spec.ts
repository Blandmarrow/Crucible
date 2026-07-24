import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Bulk rename from the Bulk Edit "Rename" tab, scoped to the whole dataset (no
// gallery selection). The renumbered stems are cross-checked through the API.
test('bulk rename the dataset', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-rename-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'one.png')
  await uploadViaApi(request, ds.id, 'two.png')

  await page.goto(`/datasets/${ds.id}/bulk-edit`)
  await page.getByRole('button', { name: 'Rename', exact: true }).click()

  await page.getByPlaceholder('e.g. portrait', { exact: false }).fill('portrait')
  await page.getByRole('button', { name: 'Rename Images' }).click()
  // Persistent result badge, not the auto-dismissing toast.
  await expect(page.getByText('2 renamed')).toBeVisible()

  // Collision-free renumbering gives the first file the bare stem and the rest a
  // numeric suffix — so "portrait.png" and "portrait_001.png".
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(list).toHaveLength(2)
  for (const img of list) {
    expect(img.filename).toMatch(/^portrait(_\d+)?\.png$/)
  }
})
