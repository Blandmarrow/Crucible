import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadVideoViaApi, uploadViaApi } from './helpers'
// The app's own debounce window, not a copy of it: this spec waits out the
// gallery's persist timer, so a change to that timer must move the wait with it.
// `npm run typecheck:e2e` is what keeps this cross-tree import compiling.
import { PERSIST_DEBOUNCE_MS } from '../src/constants/storage'

// Leaving the gallery for the detail view and coming back must land on the page
// the user was on, not page 1.
test('gallery returns to the page it was left on', async ({ page, request }) => {
  // The two waits below are derived from the debounce; the per-test budget has to
  // be too, or raising the window trips Playwright's 30 s default instead of the
  // assertion and the spec fails for the wrong reason.
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 6)
  const ds = await createDatasetViaApi(request, `restore-${Date.now()}`)
  for (const n of ['a.png', 'b.png', 'c.png', 'd.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  // Two images per page, so page 2 exists without seeding a hundred files.
  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  await page.getByRole('button', { name: 'Next →' }).click()
  await expect(page.getByText('Page 2')).toBeVisible()
  // Wait out the debounced persist (3× the window is the margin this spec has
  // always used), then check both halves: the write landed, and the mount
  // effects that run inside that window did not reset the page behind it.
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  expect(
    await page.evaluate(
      (k) => JSON.parse(localStorage.getItem(k)!).page,
      `gallery-state-${ds.id}`,
    ),
  ).toBe(2)
  await expect(page.getByText('Page 2')).toBeVisible()

  await page.getByTestId('gallery-tile').first().click()
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()

  await expect(page.getByText('Page 2')).toBeVisible()
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  await expect(page.getByText('Page 2')).toBeVisible()
})

// A subfolder deep link (the extraction-history panel's "N frames" row) must clear
// a lineage filter restored from the previous session, the mirror of what the
// `?source_video_id=` link does to a restored subfolder. Without it the two filters
// intersect and the panel's own link opens an empty grid.
test('subfolder deep link clears a restored lineage filter', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `lineage-link-${Date.now()}`)
  // A real video row, so the gallery's stale-id guard does not clear the filter for
  // us and let the test pass without the fix.
  const video = await uploadVideoViaApi(request, ds.id)
  await uploadViaApi(request, ds.id, 'filed.png', 'shots')

  await page.addInitScript(
    ([key, id]) => {
      localStorage.setItem(
        key,
        JSON.stringify({ page: 1, sortIdx: 0, captionedFilter: null, scrollTop: 0, frameVideoId: id }),
      )
    },
    [`gallery-state-${ds.id}`, video.id] as const,
  )

  await page.goto(`/datasets/${ds.id}/gallery?subfolder=shots`)

  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
  await expect(page.getByText('No images found. Upload or adjust filters.')).toHaveCount(0)
  await expect(page.getByLabel('Filter by source video')).toHaveValue('')
})
