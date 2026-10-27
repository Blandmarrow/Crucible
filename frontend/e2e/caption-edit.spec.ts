import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

const BOX = 'Natural language description'

// Edit a caption in the image detail view, save, reload, and confirm it persisted.
test('edit a caption, save, and it survives a reload', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-caption-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'cap.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  // A plain click on a tile navigates to the image detail page.
  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(/\/image\//)

  const box = page.getByPlaceholder(BOX, { exact: false })
  await box.fill('a small red square')
  // Save is disabled until the caption is dirty (the fill above makes it dirty).
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByPlaceholder(BOX, { exact: false }))
    .toHaveValue('a small red square')
})

// ---- GPT-027: the caption draft belongs to the image it was typed on ----------
// Arrowing to a neighbour whose `["caption", id]` query is already cached changes
// `imageId` and `captionData` in one render; the editor used to carry the unsaved
// draft onto it, and Save then wrote it to the wrong image. See
// docs/dev/image-detail.md § ImageDetailPage caption panel.

/** Two images, `01_a.png` and `02_b.png`, captioned ORIG-A / ORIG-B. */
async function seed(request: APIRequestContext, variant: string) {
  const ds = await createDatasetViaApi(request, `e2e-caption-${variant}-${Date.now()}`)
  await uploadViaApi(request, ds.id, '01_a.png')
  await uploadViaApi(request, ds.id, '02_b.png')
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id: Record<string, string> = Object.fromEntries(
    list.map((i: { filename: string; id: string }) => [i.filename, i.id]),
  )
  for (const [file, text] of [['01_a.png', 'ORIG-A'], ['02_b.png', 'ORIG-B']]) {
    const r = await request.put(`/api/v1/captions/image/${id[file]}`, { data: { caption_text: text, caption_style: 'natural' } })
    expect(r.ok()).toBeTruthy()
  }
  return { ds, a: id['01_a.png'], b: id['02_b.png'] }
}

const storedCaption = async (request: APIRequestContext, id: string) =>
  (await (await request.get(`/api/v1/captions/image/${id}`)).json()).caption_text as string

/** Open the gallery sorted by filename (sortIdx 4) and click into the first tile (A). */
async function openFirst(page: Page, datasetId: string, a: string) {
  await page.addInitScript(
    (d) => localStorage.setItem(`gallery-state-${d}`, JSON.stringify({ page: 1, sortIdx: 4, captionedFilter: null, scrollTop: 0, activeSubfolder: '' })),
    datasetId,
  )
  await page.goto(`/datasets/${datasetId}/gallery`)
  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(new RegExp(`/image/${a}$`))
}

async function step(page: Page, dir: 'next' | 'prev', to: string) {
  await page.getByTitle(dir === 'next' ? 'Next image (→)' : 'Previous image (←)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${to}$`))
}

/** Visit A, B, then A again, so B's caption (and image) queries are warm. */
async function warmUp(page: Page, a: string, b: string) {
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue('ORIG-A')
  await step(page, 'next', b)
  await expect(box).toHaveValue('ORIG-B')
  await step(page, 'prev', a)
  await expect(box).toHaveValue('ORIG-A')
}

test('an unsaved draft does not follow the arrows onto a cached neighbour', async ({ page, request }) => {
  const { ds, a, b } = await seed(request, 'warm')
  await openFirst(page, ds.id, a)
  await warmUp(page, a, b)
  const box = page.getByPlaceholder(BOX, { exact: false })
  const save = page.getByRole('button', { name: 'Save', exact: true })

  await box.fill('DRAFT-FOR-A')
  await expect(save).toBeEnabled()
  await step(page, 'next', b)
  await expect(box).toHaveValue('ORIG-B')
  await expect(save).toBeDisabled()

  await box.press('End')
  await box.pressSequentially(' +edit')
  await save.click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()
  expect(await storedCaption(request, a)).toBe('ORIG-A')
  expect(await storedCaption(request, b)).toBe('ORIG-B +edit')
  await expect(box).toHaveValue('ORIG-B +edit')
  await expect(save).toBeDisabled()
})

test('control: an uncached neighbour shows its own caption too', async ({ page, request }) => {
  const { ds, a, b } = await seed(request, 'cold')
  await openFirst(page, ds.id, a)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue('ORIG-A')
  await box.fill('DRAFT-FOR-A')
  await step(page, 'next', b)
  await expect(box).toHaveValue('ORIG-B')
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
})

test('a save that lands after navigating away writes its own image and spares the new draft', async ({ page, request }) => {
  const { ds, a, b } = await seed(request, 'late')
  // Hold A's PUT until the user has moved on to B and started typing there.
  let release!: () => void
  const gate = new Promise<void>((r) => { release = r })
  await page.route(`**/api/v1/captions/image/${a}`, async (route) => {
    if (route.request().method() === 'PUT') await gate
    await route.continue()
  })

  await openFirst(page, ds.id, a)
  await warmUp(page, a, b)
  const box = page.getByPlaceholder(BOX, { exact: false })
  const save = page.getByRole('button', { name: 'Save', exact: true })

  await box.fill('SAVED-ON-A')
  const putA = page.waitForResponse((r) => r.url().endsWith(`/captions/image/${a}`) && r.request().method() === 'PUT')
  await save.click()
  await step(page, 'next', b)
  await expect(box).toHaveValue('ORIG-B')
  await box.fill('TYPING-ON-B')

  release()
  await putA
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()
  // A's completion must neither clear B's draft nor refresh B's caption over it.
  await expect(box).toHaveValue('TYPING-ON-B')
  await expect(save).toBeEnabled()
  expect(await storedCaption(request, a)).toBe('SAVED-ON-A')
  expect(await storedCaption(request, b)).toBe('ORIG-B')

  // B's draft is discarded on leaving it (unchanged behaviour); A shows what was saved.
  await step(page, 'prev', a)
  await expect(box).toHaveValue('SAVED-ON-A')
  await expect(save).toBeDisabled()
})
