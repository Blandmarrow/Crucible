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

// Regression: the Export page used to send `label_missing: false` — a plain
// boolean, not omitted — into the export POST body, where `false` means "only
// images that DO carry a label" rather than "no label filter". The preview
// stripped falsy values, so it promised the full count while the export wrote
// zero files for every unlabelled image. Assert on the files on disk, not on the
// preview: the preview is the half that was already right.
test('an export with no label filter writes every unlabelled image', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-export-nolabels-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')

  const outDir = `/tmp/e2e-export-nolabels-${Date.now()}`

  await page.goto(`/datasets/${ds.id}/export`)
  await page.getByRole('button', { name: 'plain folder' }).click()
  await page.getByPlaceholder('C:\\training', { exact: false }).fill(outDir)
  // "Has caption" is on by default and these uploads have none, so leaving it
  // checked would write zero files for a reason that has nothing to do with
  // labels — and this test's whole assertion is a file count.
  await page.locator('label').filter({ hasText: 'Has caption' }).getByRole('checkbox').uncheck()
  await page.getByRole('button', { name: 'Build export' }).click()

  await expect(page.getByText('Export complete', { exact: false })).toBeVisible({ timeout: 30_000 })

  const listing = await request.get('/api/v1/filesystem/list', { params: { path: `${outDir}/images` } })
  expect(listing.status(), await listing.text()).toBe(200)
  const files = (await listing.json()).entries.filter((e: { type: string }) => e.type === 'file')
  expect(files).toHaveLength(2)
})
