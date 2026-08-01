import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// "Select all" can only ever mean the page it sits next to, so the gallery
// offers the rest separately. The journey under test is the two-step one: select
// the page, take the offer, end up with every matching image selected — with the
// real total in the label both times.
test('select all matching filters selects beyond the current page', async ({ page, request }) => {
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

  await page.getByRole('button', { name: 'Select all' }).click()
  await expect(page.getByText('2 selected')).toBeVisible()

  const offer = page.getByTestId('select-all-matching')
  await expect(offer).toContainText('All 2 on this page selected')
  await page.getByRole('button', { name: 'Select all 5 matching filters' }).click()

  await expect(page.getByText('5 selected')).toBeVisible()
  await expect(offer).toContainText('All 5 matching images selected')

  // The grid still shows one page; the selection is the whole filtered set.
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  await offer.getByRole('button', { name: 'Clear selection' }).click()
  await expect(page.getByText('5 selected')).toHaveCount(0)
  await expect(page.getByTestId('select-all-matching')).toHaveCount(0)
})

// The offer describes whatever the filters currently mean, not the dataset — the
// whole point of routing it through the same params the grid uses.
test('the offer counts the active filter, not the dataset', async ({ page, request }) => {
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

  // Narrow to the subfolder: 3 images, so 2 pages and a different offer.
  await page.getByRole('button', { name: /^keep/ }).click()
  await expect(page.getByText('Page 1 of 2 · 3 images')).toBeVisible()

  await page.getByRole('button', { name: 'Select all' }).click()
  await page.getByRole('button', { name: 'Select all 3 matching filters' }).click()
  await expect(page.getByText('3 selected')).toBeVisible()
})

// Narrowing the filters does not clear the selection, so the selection can become
// a strict *superset* of what the filters now match. The row asserts set identity
// and approximates it by cardinality — with `>=` a superset passed, and the row
// claimed "All 3 matching images selected" over a selection of 8.
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

  // Take the offer across the whole dataset: 8 selected, 8 matching.
  await page.getByRole('button', { name: 'Select all' }).click()
  await page.getByRole('button', { name: 'Select all 8 matching filters' }).click()
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
