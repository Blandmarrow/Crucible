import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The Analytics (Stats) page renders its always-present panels for a dataset.
test('stats page renders its summary panels', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-stats-${Date.now()}`)
  await uploadViaApi(request, ds.id, 's.png')

  await page.goto(`/datasets/${ds.id}/stats`)
  // The route is /stats but the page heading reads "Analytics".
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible()
  await expect(page.getByText('Total images', { exact: true })).toBeVisible()
})
