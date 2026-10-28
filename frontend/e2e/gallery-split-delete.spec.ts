import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// A selection spans split-view panes (`selectionStore.datasetByImageId`), and the
// batch endpoints act on every id they are given — so a toolbar action fired from
// one pane changes the other pane's dataset too. Invalidating only the pane that
// started it left the other one showing tiles for images that were gone (GPT-028).
// These are the inverted assertions of the GPT-028 probe: the *other* pane empties.

async function imageIds(request: APIRequestContext, datasetId: string): Promise<string[]> {
  const r = await request.get('/api/v1/images/', { params: { dataset_id: datasetId, limit: '10' } })
  return (await r.json()).map((i: { id: string }) => i.id)
}

/** Open A in the left pane and B in the right, with one image each, both selected. */
async function splitWithBothSelected(page: Page, aId: string, bId: string) {
  await page.goto(`/datasets/${aId}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
  await page.getByRole('button', { name: 'Enter split view' }).click()
  await page.getByRole('button', { name: 'Split horizontal (side by side)' }).click()
  const panes = page.getByTestId('pane-leaf')
  await expect(panes).toHaveCount(2)
  const left = panes.nth(0)
  const right = panes.nth(1)
  await right.locator('select').nth(1).selectOption(bId)
  await expect(right.getByTestId('gallery-tile')).toHaveCount(1)
  await expect(left.getByTestId('gallery-tile')).toHaveCount(1)

  await left.getByTestId('select-all-btn').click()
  await right.getByTestId('select-all-btn').click()
  await expect(left.getByText('2 selected')).toBeVisible()
  return { left, right }
}

test('a cross-dataset delete from one pane empties the other pane too', async ({ page, request }) => {
  const a = await createDatasetViaApi(request, `split-del-A-${Date.now()}`)
  const b = await createDatasetViaApi(request, `split-del-B-${Date.now()}`)
  await uploadViaApi(request, a.id, 'a1.png')
  await uploadViaApi(request, b.id, 'b1.png')
  const [aImg] = await imageIds(request, a.id)
  const [bImg] = await imageIds(request, b.id)

  const { left, right } = await splitWithBothSelected(page, a.id, b.id)
  await left.getByRole('button', { name: 'Delete', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete All' }).click()
  await expect(page.getByText('Deleted 2 images')).toBeVisible()

  expect((await request.get(`/api/v1/images/${aImg}`)).status()).toBe(404)
  expect((await request.get(`/api/v1/images/${bImg}`)).status()).toBe(404)
  await expect(left.getByTestId('gallery-tile')).toHaveCount(0)
  // The probe saw 1 here, still there 5 s later: B's pane was never invalidated.
  await expect(right.getByTestId('gallery-tile')).toHaveCount(0)
})

test('a cross-dataset move to a third dataset empties both source panes', async ({ page, request }) => {
  const stamp = Date.now()
  const a = await createDatasetViaApi(request, `split-mv-A-${stamp}`)
  const b = await createDatasetViaApi(request, `split-mv-B-${stamp}`)
  const c = await createDatasetViaApi(request, `split-mv-C-${stamp}`)
  await uploadViaApi(request, a.id, 'a1.png')
  await uploadViaApi(request, b.id, 'b1.png')

  const { left, right } = await splitWithBothSelected(page, a.id, b.id)
  await left.getByRole('button', { name: 'Move to Dataset' }).click()
  const dialog = page.getByRole('dialog', { name: 'Move images to dataset' })
  await dialog.getByRole('button', { name: new RegExp(`split-mv-C-${stamp}`) }).click()
  await dialog.getByRole('button', { name: 'Move', exact: true }).click()
  await expect(page.getByText('Moved 2 images to dataset')).toBeVisible()

  expect(await imageIds(request, c.id)).toHaveLength(2)
  await expect(left.getByTestId('gallery-tile')).toHaveCount(0)
  await expect(right.getByTestId('gallery-tile')).toHaveCount(0)
})

// `POST /images/batch/move-subfolder` used to take rows[0]'s dataset for every
// row, renaming the other dataset's files into the first one's folder. It now
// refuses a mixed request, so the toolbar sends one per dataset.
test('a cross-dataset move to a subfolder moves each image within its own dataset', async ({ page, request }) => {
  const stamp = Date.now()
  const a = await createDatasetViaApi(request, `split-sf-A-${stamp}`)
  const b = await createDatasetViaApi(request, `split-sf-B-${stamp}`)
  await uploadViaApi(request, a.id, 'a1.png')
  await uploadViaApi(request, b.id, 'b1.png')

  const { left } = await splitWithBothSelected(page, a.id, b.id)
  await left.getByRole('button', { name: 'Move to', exact: true }).click()
  await page.getByPlaceholder(/characters\/poses/).fill('sorted')
  await page.getByRole('button', { name: 'Move', exact: true }).click()
  await expect(page.getByText('Moved 2 images to "sorted"')).toBeVisible()

  for (const ds of [a, b]) {
    const rows = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
    expect(rows).toHaveLength(1)
    expect(rows[0].subfolder).toBe('sorted')
    expect(rows[0].dataset_id).toBe(ds.id)
  }
})
