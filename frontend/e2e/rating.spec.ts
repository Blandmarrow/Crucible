import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The keep/cut rating, end to end through the surface that actually matters: the
// keyboard. Rating is meant to be a triage pass — select, glance, press — so a
// journey that clicked a modal would not be testing the thing anyone uses.
//
// Three claims, in one journey because they are one workflow: the key writes the
// rating, the badge shows it, and the filter chips narrow the grid to it. The
// pagination string is the filter proof, the way `gallery-select-all.spec.ts`
// uses it — it is the one place the *whole* filtered count is stated, so it
// cannot be satisfied by the page happening to hold the right tiles.
test('rating from the keyboard shows a badge and narrows the filter', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `rating-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  // One per page. The pagination row only renders when more than one page
  // exists, and it is the assertion that proves the *whole filtered set* moved
  // rather than the tiles that happen to be on screen — so the page size is
  // chosen to keep that row present on both sides of the filter.
  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '1'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 4 · 4 images')).toBeVisible()
  // Nothing is rated, so no badge and the whole dataset is untriaged.
  await expect(page.getByTestId('rating-badge')).toHaveCount(0)
  await expect(page.getByTestId('unrated-count')).toContainText('4 unrated')

  // Rate the visible tile Keep, then the next one, so the filtered set spans
  // two pages of its own.
  const rateVisible = async () => {
    // Scoped inside the tile: `select-all-btn` shares the `select-` prefix.
    await page.getByTestId('gallery-tile').locator('[data-testid^="select-"]').first().click()
    await expect(page.getByText('1 selected')).toBeVisible()
    await page.keyboard.press('4')
    await expect(page.locator('[data-testid="rating-badge"][data-rating="4"]')).toHaveCount(1)
    await page.getByTestId('select-all-btn').click()   // deselect, so the next press is scoped
  }
  await rateVisible()
  await expect(page.getByTestId('unrated-count')).toContainText('3 unrated')
  await page.getByRole('button', { name: 'Next →' }).click()
  await expect(page.getByText('Page 2 of 4 · 4 images')).toBeVisible()
  await rateVisible()
  await expect(page.getByTestId('unrated-count')).toContainText('2 unrated')

  // The chip filters the whole result set, not the page.
  await page.getByTestId('rating-chip-4').click()
  await expect(page.getByText('Page 1 of 2 · 2 images')).toBeVisible()

  // "Keep or unrated" is the shape a single `rating_filter` param exists for —
  // two independent params could only AND, and would match nothing here.
  await page.getByTestId('rating-chip-0').click()
  await expect(page.getByText('Page 1 of 4 · 4 images')).toBeVisible()

  // Unrated alone is the complement of Keep, over the same four images.
  await page.getByTestId('rating-chip-4').click()
  await expect(page.getByText('Page 1 of 2 · 2 images')).toBeVisible()

  // Clearing brings the whole dataset back.
  await page.getByRole('button', { name: 'Clear', exact: true }).click()
  await expect(page.getByText('Page 1 of 4 · 4 images')).toBeVisible()
})

// `0` clears, as in Lightroom — and it is the reason the scale runs 4-is-best
// rather than 1-is-best, so it is worth pinning that it actually works.
test('0 clears the rating on the selection', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `rating-clear-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)

  await page.getByTestId('select-a.png').click()
  await page.keyboard.press('1')
  await expect(page.locator('[data-testid="rating-badge"][data-rating="1"]')).toHaveCount(1)

  await page.keyboard.press('0')
  await expect(page.getByTestId('rating-badge')).toHaveCount(0)
  await expect(page.getByTestId('unrated-count')).toContainText('1 unrated')
})

// The guard that makes a window-level keydown safe at all: typing a digit into a
// text field must not rate the selection. Without it, searching for "4k" would
// silently mark everything selected as Keep.
test('typing a digit in the search box does not rate the selection', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `rating-guard-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
  await page.getByTestId('select-a.png').click()
  await expect(page.getByText('1 selected')).toBeVisible()

  await page.getByPlaceholder(/search/i).first().fill('4')
  // Give the rating request a chance to have happened, then assert it did not.
  await expect(page.getByTestId('unrated-count')).toContainText('1 unrated')
  await expect(page.getByTestId('rating-badge')).toHaveCount(0)
})
