import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Run a "plain folder" export from the UI, watch the progress panel complete,
// then cross-check the produced files through the API.
test('run a plain export to completion', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-export-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  await uploadViaApi(request, ds.id, 'y.png')

  // The e2e backend serves a throwaway data dir; write the export under /tmp.
  const outDir = `/tmp/e2e-export-${Date.now()}`

  await page.goto(`/datasets/${ds.id}/export`)
  await page.getByRole('button', { name: 'plain folder' }).click()
  await page.getByPlaceholder('C:\\training', { exact: false }).fill(outDir)
  await page.getByRole('button', { name: 'Build export' }).click()

  await expect(page.getByText('Export complete', { exact: false })).toBeVisible({ timeout: 30_000 })

  // Cross-check via the preview endpoint: both images were exportable.
  const preview = await (await request.get(`/api/v1/export/preview/${ds.id}`)).json()
  expect(preview.will_export).toBe(2)
})
