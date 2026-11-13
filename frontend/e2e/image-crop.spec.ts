import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The detail page's crop box (GitHub issue #91). The old cropper was pan-and-zoom
// and fell back to a 4:3 window when no aspect was set, so "Free" could not
// resize the box. The box is now one you drag, resize and type into, and these
// journeys pin what a user does with it. The plain crop needs no torch, so Save
// runs for real against the e2e backend.

// The tool's toolbar overflows at the default 1280px.
test.use({ viewport: { width: 1920, height: 1000 } })

/** A 400x200 image: non-square, so Free, 1:1 and 4:3 all give different boxes.
 *  The 32x32 seed is stretched by a batch resize, the one edit with no ML. */
async function seedWideImage(request: APIRequestContext) {
  const ds = await createDatasetViaApi(request, `e2e-crop-${Date.now()}-${Math.floor(Math.random() * 1e6)}`)
  await uploadViaApi(request, ds.id, 'wide.png')
  const listing = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const rows = Array.isArray(listing) ? listing : listing.images
  const img = rows.find((r: { filename: string }) => r.filename === 'wide.png')
  expect(img, JSON.stringify(rows)).toBeTruthy()

  const resize = await request.post('/api/v1/images/batch/resize', {
    data: { image_ids: [img.id], width: 400, height: 200, maintain_ar: false },
  })
  expect(resize.status(), await resize.text()).toBe(200)
  const jobId = (await resize.json()).job_id
  await expect
    .poll(async () => (await (await request.get(`/api/v1/jobs/${jobId}`)).json()).status, { timeout: 30_000 })
    .toBe('completed')
  // Asserted from the payload, so the tests cannot pass against a 32x32 image.
  const after = await (await request.get(`/api/v1/images/${img.id}`)).json()
  expect([after.width, after.height]).toEqual([400, 200])
  return { ds, id: img.id as string }
}

/** The four box fields, by their aria-labels. */
const fields = (page: Page) => ({
  x: page.getByRole('spinbutton', { name: 'Crop x', exact: true }),
  y: page.getByRole('spinbutton', { name: 'Crop y', exact: true }),
  w: page.getByRole('spinbutton', { name: 'Crop w', exact: true }),
  h: page.getByRole('spinbutton', { name: 'Crop h', exact: true }),
})

/** Replace a field's value and blur, which commits a draft the clamp would change. */
async function setField(field: ReturnType<Page['getByRole']>, value: number) {
  await field.fill('')
  await field.pressSequentially(String(value))
  await field.blur()
}

async function openCrop(page: Page, dsId: string, imageId: string) {
  await page.goto(`/datasets/${dsId}/image/${imageId}`)
  await page.getByRole('button', { name: 'Crop', exact: true }).click()
  // Anchored on the box being seeded from the loaded <img>: until then the
  // fields are disabled and read 0.
  await expect(page.getByText('of 400×200')).toBeVisible()
}

test('Free resizes the box on both axes independently', async ({ page, request }) => {
  const { ds, id } = await seedWideImage(request)
  await openCrop(page, ds.id, id)
  const f = fields(page)

  // Free opens on the whole image, not a 4:3 window.
  await expect(f.x).toHaveValue('0')
  await expect(f.y).toHaveValue('0')
  await expect(f.w).toHaveValue('400')
  await expect(f.h).toHaveValue('200')

  // A hidden 4:3 lock would drag the other side along with each of these.
  await setField(f.w, 150)
  await setField(f.h, 170)
  await expect(f.w).toHaveValue('150')
  await expect(f.h).toHaveValue('170')

  // Drag the south-east corner straight up: only the height may change. This is
  // the issue's actual complaint, done with the mouse rather than the fields.
  const handle = page.locator('.ReactCrop__drag-handle.ord-se')
  const box = (await handle.boundingBox())!
  const cx = box.x + box.width / 2
  const cy = box.y + box.height / 2
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  await page.mouse.move(cx, cy - 20, { steps: 5 })
  await page.mouse.move(cx, cy - 40, { steps: 5 })
  await page.mouse.up()

  await expect(f.w).toHaveValue('150')
  await expect.poll(async () => Number(await f.h.inputValue())).toBeLessThan(170)
})

test('a preset locks the box to its shape', async ({ page, request }) => {
  const { ds, id } = await seedWideImage(request)
  await openCrop(page, ds.id, id)
  const f = fields(page)

  await page.getByLabel('Crop aspect ratio').selectOption('1:1')
  // The largest centred square of a 400x200 image.
  await expect(f.w).toHaveValue('200')
  await expect(f.h).toHaveValue('200')

  // With the shape locked, one side drives the other.
  await setField(f.w, 120)
  await expect(f.w).toHaveValue('120')
  await expect(f.h).toHaveValue('120')
})

test('Save sends the box and lands on the cropped image', async ({ page, request }) => {
  const { ds, id } = await seedWideImage(request)
  await openCrop(page, ds.id, id)
  const f = fields(page)

  // Size first: x and y are capped by the room left beside the box.
  await setField(f.w, 150)
  await setField(f.h, 100)
  await setField(f.x, 10)
  await setField(f.y, 20)
  await expect(f.x).toHaveValue('10')
  await expect(f.y).toHaveValue('20')
  await expect(f.w).toHaveValue('150')
  await expect(f.h).toHaveValue('100')

  const posted = page.waitForRequest(
    (r) => r.method() === 'POST' && r.url().endsWith(`/api/v1/images/${id}/crop`),
  )
  await page.getByRole('button', { name: 'Save Crop' }).click()
  const body = (await posted).postDataJSON()
  // The box the user sees is the box the backend crops.
  expect(body).toMatchObject({ x: 10, y: 20, width: 150, height: 100 })

  await page.waitForURL((u) => /\/image\/[^/]+$/.test(u.pathname) && !u.pathname.endsWith(`/image/${id}`))
  const newId = new URL(page.url()).pathname.split('/').at(-1)!
  expect(newId).not.toBe(id)

  // The file really is the cropped size, not just the request.
  const made = await (await request.get(`/api/v1/images/${newId}`)).json()
  expect([made.width, made.height]).toEqual([150, 100])
})

// The box nudges itself with the arrow keys, and the page turns with them. The
// library calls preventDefault on the box's own keydown but does not stop it,
// so the page's window listener used to turn the page as well: the box moved,
// then the tool reopened on the previous image with the box reset.
test('an arrow key on the box nudges it and stays on the image', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-crop-keys-${Date.now()}-${Math.floor(Math.random() * 1e6)}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')
  // Opened from the gallery, so the page has a neighbour to turn to.
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('gallery-tile').nth(1).click()
  await expect(page.getByTitle('Previous image (←)')).toBeEnabled()
  const here = page.url()

  await page.getByRole('button', { name: 'Crop', exact: true }).click()
  await expect(page.getByText('of 32×32')).toBeVisible()
  const f = fields(page)
  await setField(f.w, 16)
  await setField(f.x, 8)
  await expect(f.x).toHaveValue('8')

  await page.locator('.ReactCrop__crop-selection').focus()
  await page.keyboard.press('ArrowLeft')
  await expect.poll(async () => Number(await f.x.inputValue())).toBeLessThan(8)
  expect(page.url()).toBe(here)
})

// The Resize-to fields change the box's shape once per keystroke. Reshaping
// each step from the last shrank the box to a sliver, because the in-between
// shapes (H "1", "10") are extreme and fitting them cuts the area. Every step
// now starts from the box as it was when the field got focus.
test('typing a Resize-to size keeps the box, only reshaped', async ({ page, request }) => {
  const { ds, id } = await seedWideImage(request)
  await openCrop(page, ds.id, id)
  const f = fields(page)
  // A 200x200 box: area 40,000.
  await setField(f.w, 200)
  await setField(f.h, 200)
  await setField(f.x, 100)
  await expect(f.x).toHaveValue('100')

  await page.getByLabel('Resize crop to width').pressSequentially('400')
  await page.getByLabel('Resize crop to height').pressSequentially('100')
  // 4:1 with the same area is 400x100, which just fits the 400x200 image.
  await expect(f.w).toHaveValue('400')
  await expect(f.h).toHaveValue('100')

  // Emptying the field drops the lock (back to Free), which puts back the box
  // from when H got focus, not a squashed leftover.
  await page.getByLabel('Resize crop to height').fill('')
  await expect(f.x).toHaveValue('100')
  await expect(f.w).toHaveValue('200')
  await expect(f.h).toHaveValue('200')
})
