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
  // The filename carries the digit being typed, so the tile survives its own
  // search: a name that filtered itself off screen would empty the grid, and
  // every on-screen assertion below would then hold for the innocent reason.
  await uploadViaApi(request, ds.id, 'a4.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
  await page.getByTestId('select-a4.png').click()
  await expect(page.getByText('1 selected')).toBeVisible()

  // The load-bearing assertion. A rating write is a request, and watching for it
  // is the only check that cannot be satisfied by the UI simply not having caught
  // up yet — `unrated-count` still reads its pre-write value for as long as the
  // write is in flight, so it passes on timing alone.
  const writes: string[] = []
  page.on('request', (r) => { if (r.url().includes('bulk-rating')) writes.push(r.url()) })

  // The debounced search that the typing itself triggers. It cannot come back
  // before the keydown that would have fired the write, which is what makes an
  // empty `writes` afterwards mean something.
  const searched = page.waitForResponse((r) => r.url().includes('/api/v1/images/') && r.url().includes('search=4'))
  // `pressSequentially`, never `fill` — `fill` sets the value directly and dispatches no
  // `keydown` at all, so the window listener never runs and the test passes identically
  // with the guard deleted.
  const box = page.getByPlaceholder(/search/i).first()
  await box.pressSequentially('4')
  // The digit reached the input rather than being swallowed: the rating handler
  // calls `preventDefault()`, so a guard that let it through eats the character
  // and the search box stays empty.
  await expect(box).toHaveValue('4')
  await searched

  expect(writes).toEqual([])
  await expect(page.getByTestId('rating-badge')).toHaveCount(0)
  await expect(page.getByTestId('unrated-count')).toContainText('1 unrated')
})

// The other half of that guard, on the page whose modal set is hand-written. The
// gallery asks the DOM for `[role="dialog"]`; `ImageDetailPage` cannot, because its
// detect overlay is a bare `fixed inset-0` div with no dialog role — so it reads a
// single `anyModalOpen`, and this test is what keeps that set from drifting again.
// Clicking the modal's own heading is the point: a field would be caught by the
// text-field guard instead, and prove nothing about the modal one.
test('keys do not rate or navigate behind the detect modal', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `rating-modal-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')
  const images = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()

  await page.goto(`/datasets/${ds.id}/image/${images[0].id}`)
  const url = page.url()

  const writes: string[] = []
  page.on('request', (r) => { if (r.url().includes('bulk-rating')) writes.push(r.url()) })

  await page.getByRole('button', { name: /Run Detection/ }).first().click()
  const modal = page.locator('div.fixed.inset-0 >> .card')
  await expect(modal).toBeVisible()
  await modal.getByRole('heading', { name: 'Run Detection' }).click()

  await page.keyboard.press('3')
  await page.keyboard.press('ArrowRight')
  await expect(modal).toBeVisible()
  // A negative about a request needs a moment to be worth asserting: there is no
  // event to wait for when the correct behaviour is that nothing is sent.
  await page.waitForTimeout(500)
  expect(writes).toEqual([])
  // The arrow keys are scoped to the same guard, and a second image exists for
  // them to have moved to — so an unchanged URL is a real assertion.
  expect(page.url()).toBe(url)

  // Closing it hands the keys back, which is what makes the assertions above a
  // statement about the modal rather than about the keys never working here.
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.keyboard.press('3')
  await expect.poll(() => writes.length).toBe(1)
})
