import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Find & Replace across a whole dataset from the Bulk Edit page. Uses the
// "All images in dataset" scope so no gallery selection (shift-click / dnd) is
// involved — that selection surface is the flakiest and is deliberately avoided.
test('bulk find & replace across the dataset', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-bulk-${Date.now()}`)
  const a = await uploadViaApi(request, ds.id, 'a.png')
  const b = await uploadViaApi(request, ds.id, 'b.png')

  // Seed captions via API (caption editing itself is covered by caption-edit.spec).
  for (const fn of [a, b]) {
    const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
    const img = list.find((i: { filename: string }) => i.filename === fn)
    const r = await request.put(`/api/v1/captions/image/${img.id}`, { data: { caption_text: 'cat, dog' } })
    expect(r.status()).toBe(200)
  }

  await page.goto(`/datasets/${ds.id}/bulk-edit`)
  // "Edit Captions" tab is the default. Choose the Find & Replace operation.
  await page.getByRole('button', { name: 'Find & Replace' }).click()
  await page.getByPlaceholder('Text to find', { exact: false }).fill('cat')
  await page.getByPlaceholder('Replacement text', { exact: false }).fill('fox')
  await page.getByRole('button', { name: 'Apply', exact: true }).click()

  // Assert the persistent result badge, not the auto-dismissing toast.
  await expect(page.getByText('2 updated')).toBeVisible()

  // Cross-check through the API: both captions were rewritten.
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  for (const img of list) {
    const cap = await (await request.get(`/api/v1/captions/image/${img.id}`)).json()
    expect(cap.caption_text).toBe('fox, dog')
  }
})
