import { test, expect } from '@playwright/test'
import type { Page, APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Stepping through images with ← / → in the detail view must stay inside the
// filters the gallery was showing. The nav context the gallery writes carries its
// whole `filterParams` memo; when the arrows walk off the end of a page the detail
// view refetches the *next* page with those filters. It used to send only the
// caption filter, so crossing the boundary inside a subfolder landed in the middle
// of the unfiltered dataset — and Back then returned to page 2 of the subfolder,
// making the escape read like a display glitch.
//
// The interleaved uploads are what make these tests discriminate: filtered page 2
// and unfiltered page 2 hold different images, so a build that drops the filter
// lands somewhere provably wrong rather than merely somewhere else.

/** Seed the gallery's persisted state before the app boots. Clicking the row
 *  instead would not be safely awaitable: with `keepPreviousData` the tile count
 *  is 2 before and after the filter lands. */
async function seedGalleryState(
  page: Page,
  datasetId: string,
  state: { activeSubfolder: string; sortIdx: number; page?: number },
) {
  await page.addInitScript(
    ([dsId, s]) => {
      localStorage.setItem('gallery-page-size', '2')
      localStorage.setItem(
        `gallery-state-${dsId}`,
        JSON.stringify({
          page: (s as { page?: number }).page ?? 1,
          sortIdx: (s as { sortIdx: number }).sortIdx,
          captionedFilter: null,
          scrollTop: 0,
          activeSubfolder: (s as { activeSubfolder: string }).activeSubfolder,
        }),
      )
    },
    [datasetId, state] as const,
  )
}

/** filename → id, the way gallery-subfolders.spec.ts resolves one. */
async function idsByFilename(
  request: APIRequestContext,
  datasetId: string,
): Promise<Record<string, string>> {
  const list = await (
    await request.get('/api/v1/images/', { params: { dataset_id: datasetId, limit: '100' } })
  ).json()
  return Object.fromEntries(
    (list as { id: string; filename: string }[]).map((i) => [i.filename, i.id]),
  )
}

// Page size 2, name-ascending (SORT_OPTIONS[4]), 3 images in `alpha` and 2 in
// `beta` interleaved between them:
//
//   |        | filtered (alpha) | unfiltered   |
//   | page 1 | 01_a, 02_a       | 01_a, 02_a   |
//   | page 2 | 05_a             | 03_b, 04_b   |
test('arrow nav across a page boundary stays inside the subfolder filter', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `nav-filter-${Date.now()}`)
  for (const [name, sub] of [
    ['01_a.png', 'alpha'],
    ['02_a.png', 'alpha'],
    ['03_b.png', 'beta'],
    ['04_b.png', 'beta'],
    ['05_a.png', 'alpha'],
  ] as const) {
    await uploadViaApi(request, ds.id, name, sub)
  }
  const ids = await idsByFilename(request, ds.id)

  await seedGalleryState(page, ds.id, { activeSubfolder: 'alpha', sortIdx: 4 })
  await page.goto(`/datasets/${ds.id}/gallery`)
  // Non-vacuous precondition: the filter is actually applied, and there is a
  // second filtered page to cross into.
  await expect(page.getByText('Page 1 of 2 · 3 images')).toBeVisible()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  // Open the *first* image of the page and step to the second with the key
  // handler, so the within-page arrow path stays covered.
  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['01_a.png']}$`))
  // The URL changes on click, but the document-level key handler is registered by
  // an effect — press before the detail view has mounted and the key is dropped.
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.keyboard.press('ArrowRight')
  await expect(page).toHaveURL(new RegExp(`/image/${ids['02_a.png']}$`))

  // The boundary. `atEnd` is true in the broken build too (ids.length === limit
  // either way), so the prefetch fires and the assertion below is real.
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['05_a.png']}$`))

  // Second, independent signal: filtered page 2 holds one image, so Next is now
  // permanently disabled. The broken build would offer 04_b.png here.
  await expect(page.getByTitle('Next image (→)')).toBeDisabled()
  // The counter proves the context was replaced with the *filtered* page 2.
  await expect(page.getByText('1 / 1')).toBeVisible()
  await expect(page.getByText('p.2')).toBeVisible()

  // The only assertion pinning the stored *shape*, so a refactor that drops
  // `filters` or `limit` fails here rather than silently widening the prefetch.
  const stored = await page.evaluate(
    (k) => JSON.parse(sessionStorage.getItem(k)!),
    `gallery-nav-${ds.id}`,
  )
  expect(stored.page).toBe(2)
  expect(stored.ids).toHaveLength(1)
  expect(stored.limit).toBe(2)
  expect(stored.filters.subfolder).toBe('alpha')

  // `goTo`'s `gallery-state` patch, at a filtered boundary: Back lands on page 2
  // of alpha, which holds exactly one tile.
  await page.getByRole('button', { name: 'Back' }).click()
  await expect(page.getByText('Page 2 of 2 · 3 images')).toBeVisible()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
})

// The dataset root is a filter too — `""`, not "no subfolder filter". A
// `subfolder || undefined` normalization anywhere in `utils/galleryNav.ts` would
// pass the test above and silently break this one.
test('arrow nav across a page boundary stays at the dataset root', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `nav-root-${Date.now()}`)
  for (const [name, sub] of [
    ['01_r.png', ''],
    ['02_r.png', ''],
    ['03_s.png', 'sub'],
    ['04_s.png', 'sub'],
    ['05_r.png', ''],
  ] as const) {
    await uploadViaApi(request, ds.id, name, sub)
  }
  const ids = await idsByFilename(request, ds.id)

  await seedGalleryState(page, ds.id, { activeSubfolder: '', sortIdx: 4 })
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 2 · 3 images')).toBeVisible()

  await page.getByTestId('gallery-tile').nth(1).click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['02_r.png']}$`))

  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['05_r.png']}$`))
  await expect(page.getByTitle('Next image (→)')).toBeDisabled()

  const stored = await page.evaluate(
    (k) => JSON.parse(sessionStorage.getItem(k)!),
    `gallery-nav-${ds.id}`,
  )
  expect(stored.filters.subfolder).toBe('')
})

// Deleting from the detail view has to leave ← / → working. A delete shifts
// server-side paging by one, so the handler re-fetches the current page instead of
// splicing the id out of the stored list, and the shared invalidation helper
// *resets* the boundary-prefetch cache so the arrows cannot step onto a row the
// server no longer has.

/** Delete whatever image the detail view is showing, via the Delete key and the
 *  confirm dialog. Returns once the toast confirms the request finished — the
 *  navigation that follows is what each test then asserts on. */
async function deleteCurrentImage(page: Page) {
  await page.keyboard.press('Delete')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText('Image deleted')).toBeVisible()
}

/** `n` root images named `01_x.png` … , page size 2, name-ascending. */
async function seedNumbered(request: APIRequestContext, name: string, n: number) {
  const ds = await createDatasetViaApi(request, `${name}-${Date.now()}`)
  for (let i = 1; i <= n; i++) {
    await uploadViaApi(request, ds.id, `0${i}_x.png`)
  }
  return { ds, ids: await idsByFilename(request, ds.id) }
}

// Page size 2 over 01…05: page 1 = 01,02 | page 2 = 03,04 | page 3 = 05.
//
// Fails without the cache reset: opening 02 prefetches page 2 as [03, 04] and
// caches it for 60 s under a key nothing else in the app touches. Deleting 02
// shifts page 1 to [01, 03] — so → from 03 reads the stale entry, finds 03 as the
// first row of "page 2", and steps onto the image already on screen.
test('deleting at a page boundary does not re-serve the pre-delete page', async ({ page, request }) => {
  const { ds, ids } = await seedNumbered(request, 'nav-del-boundary', 5)

  await seedGalleryState(page, ds.id, { activeSubfolder: '', sortIdx: 4 })
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 3 · 5 images')).toBeVisible()

  // The last image of page 1: `atEnd`, so the boundary prefetch runs and the stale
  // entry the broken build serves actually exists by the time we delete.
  await page.getByTestId('gallery-tile').nth(1).click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['02_x.png']}$`))
  await expect(page.getByTitle('Next image (→)')).toBeEnabled()

  await deleteCurrentImage(page)

  // 03 slid up into the deleted slot — still the second of two on page 1.
  await expect(page).toHaveURL(new RegExp(`/image/${ids['03_x.png']}$`))
  await expect(page.getByText('2 / 2')).toBeVisible()

  // …and the row after it is 04, not the 03 the stale page-2 entry starts with.
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['04_x.png']}$`))
})

// Fails without the refresh: splicing leaves `ids.length === limit - 1`, so `atEnd`
// (a strict `=== limit`) is false forever and → dies at the end of the page — the
// exact workflow the arrows exist for, arrowing through a folder deleting the bad
// ones.
test('deleting mid-page keeps Next working to the end of the page and across it', async ({ page, request }) => {
  const { ds, ids } = await seedNumbered(request, 'nav-del-midpage', 6)

  await seedGalleryState(page, ds.id, { activeSubfolder: '', sortIdx: 4 })
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 3 · 6 images')).toBeVisible()

  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['01_x.png']}$`))
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()

  await deleteCurrentImage(page)

  // Page 1 is now [02, 03] — a *full* page, which is what keeps → alive below.
  await expect(page).toHaveURL(new RegExp(`/image/${ids['02_x.png']}$`))
  await expect(page.getByText('1 / 2')).toBeVisible()

  // Clicked, not keyed: the button is disabled until the boundary prefetch lands,
  // so the click waits where a keypress would simply be dropped.
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['03_x.png']}$`))
  // The end of the refreshed page: one more → must cross into page 2.
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['04_x.png']}$`))
  await expect(page.getByText('p.2')).toBeVisible()
})

// Deleting the only image of the last page: the refreshed page is empty, so the
// context has to step *back* — id list and `page` together, or it describes an
// empty page and both arrows die.
test('deleting the only image of the last page falls back to the previous page', async ({ page, request }) => {
  const { ds, ids } = await seedNumbered(request, 'nav-del-lastpage', 5)

  await seedGalleryState(page, ds.id, { activeSubfolder: '', sortIdx: 4, page: 3 })
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 3 of 3 · 5 images')).toBeVisible()

  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['05_x.png']}$`))
  // `atStart`, so the previous page is prefetched — the fallback reads it.
  await expect(page.getByTitle('Previous image (←)')).toBeEnabled()

  await deleteCurrentImage(page)

  await expect(page).toHaveURL(new RegExp(`/image/${ids['04_x.png']}$`))
  await expect(page.getByText('2 / 2')).toBeVisible()
  await expect(page.getByText('p.2')).toBeVisible()

  // Both arrows still describe page 2, and Next is disabled only because the page
  // the delete emptied really is gone.
  await page.getByTitle('Previous image (←)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['03_x.png']}$`))
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${ids['04_x.png']}$`))
  await expect(page.getByTitle('Next image (→)')).toBeDisabled()

  // The `gallery-state` patch moved with the context, so Back lands on page 2.
  await page.getByRole('button', { name: 'Back' }).click()
  await expect(page.getByText('Page 2 of 2 · 4 images')).toBeVisible()
})
