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
