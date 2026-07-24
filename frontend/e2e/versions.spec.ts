import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Create a snapshot from the Versions page and see it in the list. Versioning is
// off by default; enabling it is a settings concern, so it is turned on via the
// API here (not the thing under test) and the snapshot journey runs through the UI.
test('create a snapshot and see it listed', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-versions-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'v.png')
  const on = await request.patch('/api/v1/settings/thresholds', { data: { versioning_mode: 'manual' } })
  expect(on.status()).toBe(200)

  await page.goto(`/datasets/${ds.id}/versions`)
  await page.getByRole('button', { name: 'Create Snapshot', exact: false }).first().click()

  const snapName = `snap-${Date.now()}`
  await page.getByPlaceholder('e.g. Before quality scoring').fill(snapName)
  // The modal's submit button (also "Create Snapshot").
  await page.getByRole('button', { name: 'Create Snapshot', exact: true }).click()

  // Manual snapshots run as a background job; a version card (with its Restore
  // button) appears once it completes. Anchor on Restore — the snapshot name
  // text also appears transiently in the job-progress line, which would be
  // ambiguous. A fresh dataset has exactly one version afterwards.
  await expect(page.getByRole('button', { name: 'Restore' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(snapName).first()).toBeVisible()
})
