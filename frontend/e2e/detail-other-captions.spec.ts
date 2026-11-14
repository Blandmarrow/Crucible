import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// #45: the detail page's "Other captions" section lists how other images in the same
// dataset were captioned. Read-only, and "Add missing tags" only touches the unsaved draft.

const BOX = 'Natural language description'

const CAPTIONS: Record<string, string> = {
  '01_a.png': 'red scarf, snow, forest',      // the image under test
  '02_b.png': 'red scarf, snow, winter',      // shares 2 tags
  '03_c.png': 'snow, cabin, smoke',           // shares 1 tag
  '04_d.png': 'blue sea, boat',               // shares none
}

async function seed(request: APIRequestContext) {
  const ds = await createDatasetViaApi(request, `e2e-other-captions-${Date.now()}`)
  for (const name of Object.keys(CAPTIONS)) await uploadViaApi(request, ds.id, name)
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id: Record<string, string> = Object.fromEntries(
    list.map((i: { filename: string; id: string }) => [i.filename, i.id]),
  )
  for (const [file, text] of Object.entries(CAPTIONS)) {
    const r = await request.put(`/api/v1/captions/image/${id[file]}`, { data: { caption_text: text, caption_style: 'natural' } })
    expect(r.ok()).toBeTruthy()
  }
  const addBox = async (image_id: string, label: string) => {
    const r = await request.post('/api/v1/detection/manual', {
      data: { image_id, bbox: [0.1, 0.1, 0.5, 0.5], label, refine_with_sam: false },
    })
    expect(r.ok()).toBeTruthy()
  }
  await addBox(id['01_a.png'], 'cat')
  await addBox(id['03_c.png'], 'cat')
  await addBox(id['02_b.png'], 'dog')
  return { ds, a: id['01_a.png'], b: id['02_b.png'], c: id['03_c.png'] }
}

const storedCaption = async (request: APIRequestContext, id: string) =>
  (await (await request.get(`/api/v1/captions/image/${id}`)).json()).caption_text as string

test('Other captions: tag ranking, add missing tags, objects, looks alike, and it stays open', async ({ page, request }) => {
  const { ds, a } = await seed(request)
  await page.goto(`/datasets/${ds.id}/image/${a}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(CAPTIONS['01_a.png'])

  const rows = page.getByTestId('other-captions-row')
  await page.getByTestId('other-captions-toggle').click()

  // Shared tags: B (2 shared) before C (1 shared); D shares nothing and is left out.
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toContainText('02_b.png')
  await expect(rows.nth(0)).toContainText('2 shared')
  await expect(rows.nth(1)).toContainText('03_c.png')
  await expect(rows.nth(1)).toContainText('1 shared')

  // Add missing tags changes the box and leaves the stored caption alone.
  const add = rows.nth(0).getByTestId('other-captions-add-missing')
  await expect(add).toHaveText(/Add 1 tag$/)
  await add.click()
  await expect(box).toHaveValue('red scarf, snow, forest, winter')
  expect(await storedCaption(request, a)).toBe(CAPTIONS['01_a.png'])
  // The tag is in the box now, so the button goes away.
  await expect(rows.nth(0).getByTestId('other-captions-add-missing')).toHaveCount(0)

  // Objects: only C shares the "cat" label.
  await page.getByTestId('other-captions-mode-objects').click()
  await expect(page.getByTestId('other-captions-mode-objects')).toHaveAttribute('aria-pressed', 'true')
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText('03_c.png')

  // Looks alike: e2e has no CLIP vectors, so the section says so.
  await page.getByTestId('other-captions-mode-clip').click()
  await expect(page.getByTestId('other-captions-hint')).toContainText('No CLIP vector yet')
  await expect(rows).toHaveCount(0)

  // Open and mode both survive a reload.
  await page.reload()
  await expect(page.getByTestId('other-captions-mode-clip')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('other-captions-hint')).toContainText('No CLIP vector yet')
})
