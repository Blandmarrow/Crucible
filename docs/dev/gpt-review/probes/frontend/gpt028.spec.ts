import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'
import * as os_ from 'os'
const SHOT = `${os_.tmpdir()}/gpt-review-probes`

test('GPT-028: cross-dataset delete from pane A leaves pane B stale', async ({ page, request }) => {
  const a = await createDatasetViaApi(request, `f028-A-${Date.now()}`)
  const b = await createDatasetViaApi(request, `f028-B-${Date.now()}`)
  await uploadViaApi(request, a.id, 'a1.png')
  await uploadViaApi(request, b.id, 'b1.png')
  const imgId = async (ds: string) => (await (await request.get('/api/v1/images/', { params: { dataset_id: ds, limit: '10' } })).json()).map((i: any) => i.id)
  const [aId] = await imgId(a.id); const [bId] = await imgId(b.id)

  await page.goto(`/datasets/${a.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
  await page.getByRole('button', { name: 'Enter split view' }).click()
  await page.getByRole('button', { name: 'Split horizontal (side by side)' }).click()
  const panes = page.getByTestId('pane-leaf')
  await expect(panes).toHaveCount(2)
  const left = panes.nth(0), right = panes.nth(1)
  await right.locator('select').nth(1).selectOption(b.id)
  await expect(right.getByTestId('gallery-tile')).toHaveCount(1)
  await expect(left.getByTestId('gallery-tile')).toHaveCount(1)

  await left.getByTestId('select-all-btn').click()
  await right.getByTestId('select-all-btn').click()
  await expect(left.getByText('2 selected')).toBeVisible()
  await left.getByRole('button', { name: 'Delete' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete All' }).click()
  await expect(page.getByText('Deleted 2 images')).toBeVisible()
  await page.waitForTimeout(1500)

  // API state: both gone
  const sa = (await request.get(`/api/v1/images/${aId}`)).status()
  const sb = (await request.get(`/api/v1/images/${bId}`)).status()
  console.log('GPT028 API status A img:', sa, ' B img:', sb)
  console.log('GPT028 API counts A:', (await (await request.get('/api/v1/images/count', { params: { dataset_id: a.id } })).json()), ' B:', (await (await request.get('/api/v1/images/count', { params: { dataset_id: b.id } })).json()))
  const leftTiles = await left.getByTestId('gallery-tile').count()
  const rightTiles = await right.getByTestId('gallery-tile').count()
  const rightText = (await right.innerText()).match(/\d+ image[s]?/g)
  console.log('GPT028 UI tiles left:', leftTiles, ' right:', rightTiles, ' right counts text:', rightText)
  await page.screenshot({ path: `${SHOT}/gpt028-split-after-delete.png` })
  expect(sa).toBe(404); expect(sb).toBe(404)
  expect(leftTiles).toBe(0)
  expect(rightTiles).toBe(1) // stale card still shown = claim reproduced
  // does it self-heal in a few seconds?
  await page.waitForTimeout(5000)
  console.log('GPT028 right tiles after +5s:', await right.getByTestId('gallery-tile').count())
})
