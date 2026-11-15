import { test, expect, type Locator, type Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The Compare page (#130, docs/dev/gallery/compare.md): reached from the selection
// toolbar, linked zoom across panes, an id list kept in the URL, and — in split
// view — opened in the neighbouring pane so the gallery stays put.

/** The drawn width of a pane's full-size image, once it has been placed. */
async function drawnWidth(pane: Locator): Promise<number> {
  return pane.locator('img.cmp-full').evaluate((img) => parseFloat((img as HTMLImageElement).style.width) || 0)
}

async function seedThree(request: import('@playwright/test').APIRequestContext, prefix: string) {
  const ds = await createDatasetViaApi(request, `${prefix}-${Date.now()}`)
  const names = [
    await uploadViaApi(request, ds.id, 'a.png'),
    await uploadViaApi(request, ds.id, 'b.png'),
    await uploadViaApi(request, ds.id, 'c.png'),
  ]
  return { ds, names }
}

async function selectAllIn(scope: Page | Locator, count: number) {
  await expect(scope.getByTestId('gallery-tile')).toHaveCount(count)
  await scope.getByTestId('select-all-btn').click()
  await expect(scope.getByText(`${count} selected`)).toBeVisible()
}

test('Compare opens the selection side by side with linked zoom', async ({ page, request }) => {
  const { ds, names } = await seedThree(request, 'compare')
  await page.goto(`/datasets/${ds.id}/gallery`)
  await selectAllIn(page, 3)
  await page.getByRole('button', { name: 'Compare', exact: true }).click()

  await expect(page).toHaveURL(/\/compare\?ids=/)
  const panes = page.getByTestId('compare-pane')
  await expect(panes).toHaveCount(3)
  for (let i = 0; i < 3; i++) await expect.poll(() => drawnWidth(panes.nth(i))).toBeGreaterThan(0)
  const before = await Promise.all([0, 1, 2].map((i) => drawnWidth(panes.nth(i))))

  // Zoom over the first pane only; every pane follows.
  const box = await panes.nth(0).boundingBox()
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.wheel(0, -400)
  for (let i = 0; i < 3; i++) {
    await expect.poll(() => drawnWidth(panes.nth(i))).toBeGreaterThan(before[i] * 1.5)
  }
  const readout = page.locator('.cmp-zoom-read')
  await expect(readout).toHaveText(/^×\d/)
  await page.getByRole('button', { name: 'Fit', exact: true }).click()
  await expect(readout).toHaveText('Fit')
  for (let i = 0; i < 3; i++) await expect.poll(() => drawnWidth(panes.nth(i))).toBeCloseTo(before[i], 0)

  // Remove one: two panes, and the URL follows, so a reload shows the same two.
  await page.getByRole('button', { name: `Remove ${names[2]} from the comparison` }).click()
  await expect(panes).toHaveCount(2)
  await expect.poll(() => new URL(page.url()).searchParams.get('ids')!.split(',').length).toBe(2)
  await page.reload()
  await expect(panes).toHaveCount(2)

  // Two is the floor.
  await page.getByRole('button', { name: `Remove ${names[1]} from the comparison` }).click()
  await expect(page.getByText('A comparison needs two images')).toBeVisible()
  await expect(panes).toHaveCount(2)
})

/** How far into image B its swipe clip starts, in CSS pixels. */
async function clipLeft(b: Locator): Promise<number> {
  return b.evaluate((img) => {
    const m = /inset\(0px 0px 0px ([\d.]+)px\)/.exec((img as HTMLImageElement).style.clipPath)
    return m ? parseFloat(m[1]) : -1
  })
}

test('A/B shows B beside a divider, while a key is held, or as a difference', async ({ page, request }) => {
  const { ds } = await seedThree(request, 'compare-ab')
  const rows = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  await page.goto(`/compare?ids=${rows.map((r: { id: string }) => r.id).join(',')}`)
  await page.getByRole('button', { name: 'A/B', exact: true }).click()

  const frame = page.getByTestId('compare-ab')
  const b = page.getByTestId('compare-ab-b')
  await expect.poll(() => b.evaluate((img) => parseFloat((img as HTMLImageElement).style.width) || 0)).toBeGreaterThan(0)

  // Swipe: the divider's arrow keys move where B's clip starts — and, zoomed in,
  // only that: the frame's own arrow keys pan, and must not also fire.
  await frame.focus()
  await page.keyboard.press('+')
  await page.keyboard.press('+')
  await expect(page.locator('.cmp-zoom-read')).toHaveText(/^×/)
  const a = page.getByTestId('compare-ab-a')
  const panBefore = await a.evaluate((img) => (img as HTMLImageElement).style.transform)
  await expect.poll(() => clipLeft(b)).toBeGreaterThan(0)
  const before = await clipLeft(b)
  const divider = page.getByRole('slider', { name: 'Divider between A and B' })
  await divider.focus()
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => clipLeft(b)).toBeGreaterThan(before)
  expect(await a.evaluate((img) => (img as HTMLImageElement).style.transform)).toBe(panBefore)
  await page.getByRole('button', { name: 'Fit', exact: true }).click()

  // Flip: B shows only while Space is held — not when Space is pressing a button.
  await page.getByRole('button', { name: 'Flip', exact: true }).click()
  const showing = page.getByTestId('compare-ab-showing')
  await expect(showing).toHaveText(/^A · /)
  await page.keyboard.down('Space') // focus is still on the Flip button
  await expect(showing).toHaveText(/^A · /)
  await page.keyboard.up('Space')
  await frame.focus()
  await page.keyboard.down('Space')
  await expect(showing).toHaveText(/^B · /)
  await expect(b).toBeVisible()
  await page.keyboard.up('Space')
  await expect(showing).toHaveText(/^A · /)
  await expect(b).toBeHidden()

  // Difference: B blends over A, and the mode and its view survive a reload.
  await page.getByRole('button', { name: 'Difference', exact: true }).click()
  await expect(b).toHaveCSS('mix-blend-mode', 'difference')
  await page.reload()
  await expect(page.getByTestId('compare-ab-b')).toHaveCSS('mix-blend-mode', 'difference')
  await expect(page.getByRole('button', { name: 'A/B', exact: true })).toHaveAttribute('aria-pressed', 'true')
})

test('more than four open as a grid; ticked ones compare side by side; both exports download', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `compare-grid-${Date.now()}`)
  for (const n of ['a', 'b', 'c', 'd', 'e']) await uploadViaApi(request, ds.id, `${n}.png`)
  await page.goto(`/datasets/${ds.id}/gallery`)
  await selectAllIn(page, 5)
  await page.getByRole('button', { name: 'Compare', exact: true }).click()

  // Five is more than Side by side shows, so the set opens as a grid.
  const grid = page.getByTestId('compare-grid')
  await expect(grid.getByRole('button')).toHaveCount(5)
  await expect(page.getByRole('button', { name: 'Grid', exact: true })).toHaveAttribute('aria-pressed', 'true')

  // The whole set as one sheet: the server draws it, the browser saves it.
  await page.getByRole('button', { name: 'Export sheet' }).click()
  const dialog = page.getByRole('dialog', { name: 'Export sheet' })
  const [sheet] = await Promise.all([
    page.waitForEvent('download'),
    dialog.getByRole('button', { name: 'Download' }).click(),
  ])
  expect(sheet.suggestedFilename()).toMatch(/-sheet\.png$/)
  expect((await import('node:fs')).statSync(await sheet.path()).size).toBeGreaterThan(0)
  await expect(dialog).toBeHidden()

  // Tick two, compare them side by side, and come back to all five.
  await grid.getByRole('button').nth(0).click()
  await grid.getByRole('button').nth(2).click()
  await page.getByRole('button', { name: 'Compare ticked (2)' }).click()
  const panes = page.getByTestId('compare-pane')
  await expect(panes).toHaveCount(2)
  await expect.poll(() => new URL(page.url()).searchParams.get('ids')!.split(',').length).toBe(2)

  // Export current view: the shown panes, each cropped to the region on screen.
  for (let i = 0; i < 2; i++) await expect.poll(() => drawnWidth(panes.nth(i))).toBeGreaterThan(0)
  await page.getByRole('button', { name: 'Export current view' }).click()
  const view = page.getByRole('dialog', { name: 'Export current view' })
  await view.getByRole('radio', { name: 'JPEG (smaller)' }).check()
  const [crop] = await Promise.all([
    page.waitForEvent('download'),
    view.getByRole('button', { name: 'Download' }).click(),
  ])
  expect(crop.suggestedFilename()).toMatch(/-sheet\.jpg$/)

  await page.getByRole('button', { name: 'Back to all 5' }).click()
  await expect(grid.getByRole('button')).toHaveCount(5)
})

test('a new set picked from inside the old one opens as a new set, not a removal', async ({ page, request }) => {
  // In split view the Compare pane is not remounted between presses, so the page
  // must tell its own edits from a new selection — even one that is a subset.
  const ds = await createDatasetViaApi(request, `compare-subset-${Date.now()}`)
  const names: string[] = []
  for (const n of ['a', 'b', 'c', 'd', 'e']) names.push(await uploadViaApi(request, ds.id, `${n}.png`))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(5)
  await page.getByRole('button', { name: 'Enter split view' }).click()
  await page.getByRole('button', { name: 'Split horizontal (side by side)' }).click()
  const left = page.getByTestId('pane-leaf').nth(0)
  const right = page.getByTestId('pane-leaf').nth(1)

  await selectAllIn(left, 5)
  await left.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(right.getByTestId('compare-grid').getByRole('button')).toHaveCount(5)

  await left.getByTestId('select-all-btn').click() // Deselect all
  for (const n of names.slice(0, 3)) await left.getByTestId(`select-${n}`).click()
  await expect(left.getByText('3 selected')).toBeVisible()
  await left.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(right.getByTestId('compare-pane')).toHaveCount(3)
  await expect(right.getByTestId('compare-grid')).toHaveCount(0)
})

test('a pane can label its image, and delete it', async ({ page, request }) => {
  const { ds, names } = await seedThree(request, 'compare-act')
  const labelName = `keep-${Date.now()}`
  const lab = await request.post('/api/v1/labels/', { data: { name: labelName } })
  expect(lab.status(), await lab.text()).toBe(201)
  // The label vocabulary is app-wide and the e2e server is shared, so the label
  // goes again even if this test fails: labels.spec.ts expects its own to be the
  // only one with a Set hotkey button.
  const labelId = (await lab.json()).id
  try {
  await page.goto(`/datasets/${ds.id}/gallery`)
  await selectAllIn(page, 3)
  await page.getByRole('button', { name: 'Compare', exact: true }).click()
  const panes = page.getByTestId('compare-pane')
  await expect(panes).toHaveCount(3)

  // Labels: a dialog (a pane clips popovers), written through the detail page's queue.
  await page.getByRole('button', { name: `Labels for ${names[0]}` }).click()
  const dialog = page.getByRole('dialog', { name: `Labels for ${names[0]}` })
  await dialog.getByRole('checkbox', { name: labelName }).check()
  await expect(dialog.getByRole('checkbox', { name: labelName })).toBeChecked()
  await dialog.getByRole('button', { name: 'Done' }).click()
  await expect(page.locator('.cmp-label', { hasText: labelName })).toHaveCount(1)

  // Delete: confirmed, then the image and its file are gone and the pane with them.
  const rows = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const victim = rows.find((r: { filename: string }) => r.filename === names[2]).id
  await page.getByRole('button', { name: `Delete ${names[2]}` }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(page.getByText(`Deleted ${names[2]}`)).toBeVisible()
  await expect(panes).toHaveCount(2)
  expect((await request.get(`/api/v1/images/${victim}`)).status()).toBe(404)
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)
  } finally {
    await request.delete(`/api/v1/labels/${labelId}`)
  }
})

test('a duplicate group opened from the Quality page keeps one and deletes the rest', async ({ page, request }) => {
  // The group is staged, not scanned: a scoring run imports torch (the aesthetic
  // scorer is imported whatever the boxes say), and CI's e2e has none. The
  // Quality page's group list and the duplicate flags on Compare's rows are
  // answered as a Technical scan would write them; the resolve itself is real.
  const { ds } = await seedThree(request, 'compare-dups')
  const rows: { id: string; filename: string; updated_at: string }[] =
    await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const root = rows[0].id
  await page.route(`**/api/v1/quality/duplicates/${ds.id}`, (route) => route.fulfill({
    json: {
      groups: [rows.map((r, i) => ({
        id: r.id, filename: r.filename, aesthetic_score: null, aesthetic_model: null,
        updated_at: r.updated_at, created_at: r.updated_at, kept: i === 0,
        source_video_id: null, source_timestamp_ms: null, source_shot_index: null, source_video_name: null,
      }))],
    },
  }))
  await page.route('**/api/v1/images/by-ids', async (route) => {
    const res = await route.fetch()
    const items = await res.json()
    for (const it of items) {
      if (it.id !== root) it.quality_flags = { ...it.quality_flags, is_duplicate: true, duplicate_of: root }
    }
    await route.fulfill({ response: res, json: items })
  })

  await page.goto(`/datasets/${ds.id}/quality`)
  await expect(page.getByRole('heading', { name: 'Duplicate groups' })).toBeVisible()
  await page.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(page).toHaveURL(/from=dups/)
  const keep = page.getByRole('button', { name: 'Keep this one' })
  await expect(keep).toHaveCount(3)

  // Keep this one only while the comparison is the whole group: a move keeps it,
  // a removal takes it away (resolving part of a group would strand the rest).
  await page.getByRole('button', { name: /^Move .* left$/ }).first().click()
  await expect(keep).toHaveCount(3)
  await page.getByRole('button', { name: /^Remove .* from the comparison$/ }).last().click()
  await expect(page.getByTestId('compare-pane')).toHaveCount(2)
  await expect(keep).toHaveCount(0)

  await page.goto(`/datasets/${ds.id}/quality`)
  await page.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(keep).toHaveCount(3)
  // The second pane's image is the one to keep, so the survivor is not just
  // whichever the scan happened to keep.
  const chosen = (await page.getByTestId('compare-pane').nth(1).locator('..').locator('.cmp-name').textContent())!
  await keep.nth(1).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete 2' }).click()
  await expect(page.getByText(`Kept ${chosen} and deleted 2 duplicates.`, { exact: true })).toBeVisible()
  const left = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  expect(left).toHaveLength(1)
  expect(left[0].filename).toBe(chosen)
})

test('Back returns to the gallery, after edits to the set and from a fresh tab', async ({ page, request }) => {
  const { ds, names } = await seedThree(request, 'compare-back')
  const gallery = new RegExp(`/datasets/${ds.id}/gallery`)

  // Opened from the gallery: one step back, even after a pane was removed (the
  // removal replaces its history entry rather than adding one).
  await page.goto(`/datasets/${ds.id}/gallery`)
  await selectAllIn(page, 3)
  await page.getByRole('button', { name: 'Compare', exact: true }).click()
  await expect(page.getByTestId('compare-pane')).toHaveCount(3)
  await page.getByRole('button', { name: `Remove ${names[2]} from the comparison` }).click()
  await expect(page.getByTestId('compare-pane')).toHaveCount(2)
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(page).toHaveURL(gallery)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(3)

  // Opened straight from a URL, with nothing behind it: the first image's gallery.
  const rows = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const fresh = await page.context().newPage()
  await fresh.goto(`/compare?ids=${rows.map((r: { id: string }) => r.id).join(',')}`)
  await expect(fresh.getByTestId('compare-pane')).toHaveCount(3)
  await fresh.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(fresh).toHaveURL(gallery)
})

test('in split view, Compare opens in the neighbouring pane and the gallery stays', async ({ page, request }) => {
  const { ds } = await seedThree(request, 'compare-split')
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(3)
  await page.getByRole('button', { name: 'Enter split view' }).click()
  await page.getByRole('button', { name: 'Split horizontal (side by side)' }).click()
  const leaves = page.getByTestId('pane-leaf')
  await expect(leaves).toHaveCount(2)
  const left = leaves.nth(0)
  const right = leaves.nth(1)

  await selectAllIn(left, 3)
  await left.getByRole('button', { name: 'Compare', exact: true }).click()

  await expect(right.getByTestId('compare-pane')).toHaveCount(3)
  await expect(left.getByTestId('gallery-tile')).toHaveCount(3)
  // The right pane's selector names the page rather than falling back to its first option.
  await expect(right.locator('select').first()).toHaveValue('compare')
  // The opened pane is the active one, though the click landed in the left pane,
  // whose own click handler makes itself active as the event bubbles.
  await expect(right).toHaveAttribute('data-active', 'true')
  await expect(left).not.toHaveAttribute('data-active', 'true')

  // Open one image's detail view beside the comparison (in the left pane), edit
  // its caption there, and the comparison follows without a reload.
  await right.getByRole('button', { name: /^Open .* in the detail view$/ }).first().click()
  await expect(left.getByPlaceholder('Natural language description', { exact: false })).toBeVisible()
  await expect(left).toHaveAttribute('data-active', 'true')
  await expect(right.getByTestId('compare-pane')).toHaveCount(3)
  await left.getByPlaceholder('Natural language description', { exact: false }).fill('a caption written beside')
  await left.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible() // a toast, at the app root
  await expect(right.getByRole('button', { name: 'a caption written beside' })).toBeVisible()
})

test('the detail page compares an image with the selection or its neighbours', async ({ page, request, context }) => {
  const { ds } = await seedThree(request, 'compare-detail')
  const idInUrl = () => new URL(page.url()).pathname.split('/image/')[1]
  const compareIds = () => new URL(page.url()).searchParams.get('ids')!.split(',')
  const menuItem = (name: RegExp) => page.getByRole('menuitem', { name })

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(3)
  await page.getByTestId('gallery-tile').first().click()
  // The key handler registers in an effect: wait for the page before pressing keys.
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()

  // Select the first and third images from the detail page, then stand on the second.
  // Each step waits for the counter: a key pressed while the next image loads is lost.
  const at = (n: number) => expect(page.getByTitle(`Image ${n} of 3`)).toBeVisible()
  const order: string[] = []
  await at(1)
  order.push(idInUrl())
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.keyboard.press('ArrowRight')
  await at(2)
  order.push(idInUrl())
  await page.keyboard.press('ArrowRight')
  await at(3)
  order.push(idInUrl())
  await page.getByRole('button', { name: 'Select', exact: true }).click()
  await page.keyboard.press('ArrowLeft')
  await at(2)
  expect(idInUrl()).toBe(order[1])

  // With selection: this image first, then the selection in pick order.
  await page.getByTestId('detail-compare-menu').click()
  await menuItem(/^With selection \(2\)/).click()
  await expect(page).toHaveURL(/\/compare\?ids=/)
  expect(compareIds()).toEqual([order[1], order[0], order[2]])
  await expect(page.getByTestId('compare-pane')).toHaveCount(3)

  // Compare's own Back returns to the detail page; With neighbours is the arrow
  // keys' order.
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/image/${order[1]}$`))
  await page.getByTestId('detail-compare-menu').click()
  await menuItem(/^With neighbours/).click()
  await expect(page).toHaveURL(/\/compare\?ids=/)
  expect(compareIds()).toEqual(order)

  // A detail page opened in a fresh tab has no selection and no gallery order.
  const fresh = await context.newPage()
  await fresh.goto(`/datasets/${ds.id}/image/${order[1]}`)
  await expect(fresh.getByRole('button', { name: 'Back' })).toBeVisible()
  await fresh.getByTestId('detail-compare-menu').click()
  await expect(fresh.getByRole('menuitem', { name: /^With selection/ })).toHaveAttribute('aria-disabled', 'true')
  await expect(fresh.getByRole('menuitem', { name: /^With neighbours/ })).toHaveAttribute('aria-disabled', 'true')
  await fresh.close()
})
