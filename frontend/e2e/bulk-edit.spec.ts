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

// An invalid regex used to come back as a 200 `{affected: 0}`: the toolbar's
// modal closed, the selection was cleared and the toast said "No captions
// matched", the same response a valid pattern that matched nothing gets
// (GPT-031). It is a 400 now, shown with the server's reason, and the modal, the
// pattern and the selection all stay so the typo can be fixed in place.
test('an invalid bulk-caption regex is reported and keeps the modal and selection', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-bulk-regex-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  const [img] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const put = await request.put(`/api/v1/captions/image/${img.id}`, { data: { caption_text: 'a red square' } })
  expect(put.status()).toBe(200)

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Edit Captions — 1 Image' })
  await dialog.getByRole('button', { name: 'Find & Replace' }).click()
  const find = dialog.getByPlaceholder('Text to find', { exact: false })
  await find.fill('(')
  await dialog.getByPlaceholder('Replacement text', { exact: false }).fill('X')
  await dialog.getByLabel('Use regular expression').check()

  const resp = page.waitForResponse((r) => r.url().includes('/bulk-edit'))
  await dialog.getByRole('button', { name: 'Apply' }).click()
  expect((await resp).status()).toBe(400)

  await expect(page.getByText(/^Invalid regex: /)).toBeVisible()
  await expect(page.getByText('No captions matched — nothing was changed')).toHaveCount(0)
  await expect(dialog).toBeVisible()
  await expect(find).toHaveValue('(')
  await expect(page.getByText('1 selected', { exact: true })).toBeVisible()
  const cap = (await (await request.get(`/api/v1/captions/image/${img.id}`)).json()).caption_text
  expect(cap).toBe('a red square')

  // The control: a valid pattern that matches nothing still reads as "no match".
  await find.fill('zzz+')
  await dialog.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByText('No captions matched — nothing was changed')).toBeVisible()
})
