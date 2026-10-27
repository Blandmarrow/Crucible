import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// A batch delete deletes files first and keeps the row of any file another
// program holds open (GPT-008), answering `{deleted, in_use, failed}`. The lock
// itself is Windows-only, so the response is stubbed: what is under test is that
// the toolbar reports the kept images instead of claiming every one was deleted.
test('a batch delete that keeps locked images says so', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `delete-kept-${Date.now()}`)
  for (const n of ['a.png', 'b.png']) await uploadViaApi(request, ds.id, n)

  await page.route('**/api/v1/images/batch/delete', (route) =>
    route.fulfill({ json: { deleted: 1, in_use: 1, failed: 0 } }),
  )
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('2 selected')).toBeVisible()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await page.getByRole('button', { name: 'Delete All' }).click()

  await expect(page.getByText('Deleted 1 image', { exact: true })).toBeVisible()
  await expect(page.getByText('1 image not deleted: open in another program. Close it and try again.')).toBeVisible()
})
