import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'
import * as os_ from 'os'
const SHOT = `${os_.tmpdir()}/gpt-review-probes`
const BOX = 'Natural language description'

async function seed(request: any, variant: string) {
  const ds = await createDatasetViaApi(request, `f027-${variant}-${Date.now()}`)
  await uploadViaApi(request, ds.id, '01_a.png')
  await uploadViaApi(request, ds.id, '02_b.png')
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id = Object.fromEntries(list.map((i: any) => [i.filename, i.id]))
  await request.put(`/api/v1/captions/image/${id['01_a.png']}`, { data: { caption_text: 'ORIG-A', caption_style: 'natural' } })
  await request.put(`/api/v1/captions/image/${id['02_b.png']}`, { data: { caption_text: 'ORIG-B', caption_style: 'natural' } })
  return { ds, id }
}
const cap = async (request: any, id: string) => (await (await request.get(`/api/v1/captions/image/${id}`)).json()).caption_text

test('GPT-027: draft on A leaks into cached B (warm A->B->A)', async ({ page, request }) => {
  const { ds, id } = await seed(request, 'warm')
  await page.addInitScript((d) => localStorage.setItem(`gallery-state-${d}`, JSON.stringify({ page: 1, sortIdx: 4, captionedFilter: null, scrollTop: 0, activeSubfolder: '' })), ds.id)
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(new RegExp(`/image/${id['01_a.png']}$`))
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue('ORIG-A')
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${id['02_b.png']}$`))
  await expect(box).toHaveValue('ORIG-B')
  await page.getByTitle('Previous image (←)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${id['01_a.png']}$`))
  await expect(box).toHaveValue('ORIG-A')
  // type unsaved draft on A
  await box.fill('DRAFT-FOR-A')
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${id['02_b.png']}$`))
  await page.waitForTimeout(1500)
  const shown = await box.inputValue()
  const saveDisabled = await page.getByRole('button', { name: 'Save', exact: true }).isDisabled()
  console.log('GPT027 warm: textarea on B =', JSON.stringify(shown), ' save disabled =', saveDisabled)
  await page.screenshot({ path: `${SHOT}/gpt027-warm-B-shows-draft.png` })
  expect(shown).toBe('DRAFT-FOR-A') // claim reproduced
  expect(saveDisabled).toBe(true)
  // edit + save on B
  await box.press('End'); await box.pressSequentially(' +edit')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()
  const a = await cap(request, id['01_a.png']); const b = await cap(request, id['02_b.png'])
  console.log('GPT027 warm API: A =', JSON.stringify(a), ' B =', JSON.stringify(b))
  expect(a).toBe('ORIG-A')
  expect(b).toBe('DRAFT-FOR-A +edit')
})

test('GPT-027 control: B not cached (A->B only) — draft does not leak', async ({ page, request }) => {
  const { ds, id } = await seed(request, 'cold')
  await page.addInitScript((d) => localStorage.setItem(`gallery-state-${d}`, JSON.stringify({ page: 1, sortIdx: 4, captionedFilter: null, scrollTop: 0, activeSubfolder: '' })), ds.id)
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('gallery-tile').first().click()
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue('ORIG-A')
  await box.fill('DRAFT-FOR-A')
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${id['02_b.png']}$`))
  await page.waitForTimeout(1500)
  const shown = await box.inputValue()
  console.log('GPT027 cold control: textarea on B =', JSON.stringify(shown))
  await page.screenshot({ path: `${SHOT}/gpt027-cold-control.png` })
  expect(shown).toBe('ORIG-B')
})
