import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'
import * as os_ from 'os'
const F = `${os_.tmpdir()}/gpt-review-probes`

async function setup(request: any) {
  const ds = await createDatasetViaApi(request, `f031-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  const [img] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  await request.put(`/api/v1/captions/image/${img.id}`, { data: { caption_text: 'a red square', caption_style: 'natural' } })
  return { ds, img }
}
async function run(page: any, ds: any, pattern: string) {
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('select-all-btn').click()
  await page.getByRole('button', { name: 'Edit', exact: true }).click()
  const m = page.locator('div.fixed.inset-0').filter({ hasText: 'Edit Captions' })
  await m.getByRole('button', { name: 'Find & Replace' }).click()
  await m.getByPlaceholder('Text to find…').fill(pattern)
  await m.getByPlaceholder('Replacement text', { exact: false }).fill('X')
  await m.getByLabel('Use regular expression').check()
  const resp = page.waitForResponse((r: any) => r.url().includes('/bulk-edit'))
  await m.getByRole('button', { name: 'Apply' }).click()
  const r = await resp
  return { status: r.status(), body: await r.json(), m }
}

test('GPT-031: invalid regex "(" -> 200 {affected:0,skipped:1}, modal closes, "No captions matched" toast', async ({ page, request }) => {
  const { ds, img } = await setup(request)
  const { status, body, m } = await run(page, ds, '(')
  console.log('GPT031 API:', status, JSON.stringify(body))
  await expect(page.getByText('No captions matched — nothing was changed')).toBeVisible()
  await page.screenshot({ path: `${F}/gpt031-invalid-regex.png` })
  console.log('GPT031 modal still visible:', await m.isVisible(), ' selection toolbar text present:', await page.getByText('selected').count())
  expect(status).toBe(200); expect(body).toEqual({ affected: 0, skipped: 1 })
  await expect(m).toHaveCount(0)
  const cap = (await (await request.get(`/api/v1/captions/image/${img.id}`)).json()).caption_text
  expect(cap).toBe('a red square')
})

test('GPT-031 control: valid regex that matches nothing -> same response/toast (indistinguishable)', async ({ page, request }) => {
  const { ds } = await setup(request)
  const { status, body } = await run(page, ds, 'zzz+')
  console.log('GPT031 control API:', status, JSON.stringify(body))
  await expect(page.getByText('No captions matched — nothing was changed')).toBeVisible()
})
