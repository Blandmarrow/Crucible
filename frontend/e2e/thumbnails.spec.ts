import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Rebuild thumbnails from the Bulk Edit "Thumbnails" tab, scoped to the whole
// dataset. The only automated check on the UI wiring — there are no frontend
// unit tests — so the assertion is the one the user actually feels:
// `updated_at` has to advance, because `imagesApi.thumbnailUrlVersioned` builds
// its cache-buster from it and a repair that does not move it leaves the browser
// serving the stale tile.
test('rebuild thumbnails from Bulk Edit', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-thumbs-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'one.png')

  const listUrl = '/api/v1/images/'
  const before = await (await request.get(listUrl, { params: { dataset_id: ds.id } })).json()
  expect(before).toHaveLength(1)

  await page.goto(`/datasets/${ds.id}/bulk-edit`)
  await page.getByRole('button', { name: 'Thumbnails', exact: true }).click()
  await page.getByRole('button', { name: 'Rebuild Thumbnails' }).click()

  await expect(page.getByText('Rebuilt 1 thumbnail')).toBeVisible()

  const after = await (await request.get(listUrl, { params: { dataset_id: ds.id } })).json()
  expect(Date.parse(after[0].updated_at)).toBeGreaterThan(Date.parse(before[0].updated_at))
})
