import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// "Select all" means every image the filters match, not the page on screen — the
// page is reachable from the caret menu beside it. The journey under test is the
// one click: five images across three pages, all five selected.
test('select all selects every matching image, not just the page', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `select-all-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png', 'e.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  // Two per page, so three pages exist without seeding a hundred files.
  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  // The count query drives the pagination row: a real total and a real page
  // count, not the "a full page means there is probably another" heuristic.
  await expect(page.getByText('Page 1 of 3 · 5 images')).toBeVisible()

  // Nothing is selected yet, so no offer.
  await expect(page.getByTestId('select-all-matching')).toHaveCount(0)

  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('5 selected')).toBeVisible()
  await expect(page.getByTestId('select-all-matching')).toContainText('All 5 matching images selected')

  // The grid still shows one page; the selection is the whole filtered set.
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  // "Deselect all" is the same button's other face, and it empties this dataset.
  await expect(page.getByTestId('select-all-btn')).toContainText('Deselect all')
  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('5 selected')).toHaveCount(0)
  await expect(page.getByTestId('select-all-matching')).toHaveCount(0)
})

// The regression this pair of behaviours exists for: every bulk select is
// additive, so a page selected on page 1 is still selected after paging to 2,
// selecting there, and coming back. `selectAll` used to build a fresh Set and
// the first page's ids were gone with no way to get them back.
test('a page selection survives paging away and back', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `select-all-additive-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 2 · 4 images')).toBeVisible()

  const selectThisPage = async () => {
    await page.getByTestId('select-all-menu-btn').click()
    await page.getByTestId('select-all-menu').getByRole('button', { name: /page/ }).click()
  }

  await selectThisPage()
  await expect(page.getByText('2 selected')).toBeVisible()

  await page.getByRole('button', { name: 'Next →' }).click()
  await expect(page.getByText('Page 2 of 2 · 4 images')).toBeVisible()
  await selectThisPage()
  await expect(page.getByText('4 selected')).toBeVisible()

  // Back to page 1: both tiles still carry the selection.
  await page.getByRole('button', { name: '← Previous' }).click()
  await expect(page.getByText('Page 1 of 2 · 4 images')).toBeVisible()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)
  await expect(page.locator('[data-testid="gallery-tile"][data-selected="true"]')).toHaveCount(2)
  await expect(page.getByText('4 selected')).toBeVisible()
})

// The button describes whatever the filters currently mean, not the dataset —
// the whole point of routing it through the same params the grid uses.
test('select all counts the active filter, not the dataset', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `select-all-filtered-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png', 'e.png']) {
    await uploadViaApi(request, ds.id, n)
  }
  for (const n of ['x.png', 'y.png', 'z.png']) {
    await uploadViaApi(request, ds.id, n, 'keep')
  }

  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 4 · 8 images')).toBeVisible()

  // Narrow to the subfolder: 3 images, so 2 pages and a smaller selection.
  await page.getByRole('button', { name: /^keep/ }).click()
  await expect(page.getByText('Page 1 of 2 · 3 images')).toBeVisible()

  await page.getByTestId('select-all-menu-btn').click()
  await expect(page.getByTestId('select-all-menu')).toContainText('All 3 matching filters')
  await page.getByTestId('select-all-menu').getByRole('button', { name: /matching filters/ }).click()
  await expect(page.getByText('3 selected')).toBeVisible()
})

// Narrowing the filters does not clear the selection, and bulk selects are
// additive, so the selection can be a strict *superset* of what the filters now
// match. The row asserts set identity and approximates it by cardinality — with
// `>=` a superset passed, and the row claimed "All 3 matching images selected"
// over a selection of 8.
test('a selection that outgrows the narrowed filters offers rather than claims', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `select-all-superset-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png', 'e.png']) {
    await uploadViaApi(request, ds.id, n)
  }
  for (const n of ['x.png', 'y.png', 'z.png']) {
    await uploadViaApi(request, ds.id, n, 'keep')
  }

  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 4 · 8 images')).toBeVisible()

  // Take the whole dataset: 8 selected, 8 matching.
  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('8 selected')).toBeVisible()

  // Narrow to the 3 in `keep`. The selection still holds 8, so the visible page
  // is fully selected and the row renders either way — only its branch differs.
  await page.getByRole('button', { name: /^keep/ }).click()
  await expect(page.getByText('Page 1 of 2 · 3 images')).toBeVisible()

  const offer = page.getByTestId('select-all-matching')
  await expect(offer).toContainText('All 2 on this page selected')
  await expect(offer.getByRole('button', { name: 'Select all 3 matching filters' })).toBeVisible()
  await expect(offer).not.toContainText('All 3 matching images selected')
})
