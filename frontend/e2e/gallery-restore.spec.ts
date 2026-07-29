import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadVideoViaApi, uploadViaApi } from './helpers'

// Leaving the gallery for the detail view and coming back must land on the page
// the user was on, not page 1.
test('gallery returns to the page it was left on', async ({ page, request }) => {
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
  // Past the debounce window that the mount effects run on.
  await page.waitForTimeout(1000)
  await expect(page.getByText('Page 2')).toBeVisible()

  await page.getByTestId('gallery-tile').first().click()
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()

  await expect(page.getByText('Page 2')).toBeVisible()
  await page.waitForTimeout(1000)
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
