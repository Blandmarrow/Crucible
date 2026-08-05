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

// The sidebar tree's open branches are `expandedPaths`, and GalleryPage unmounts on
// every trip to the detail view — so they have to ride in the persisted blob or the
// tree comes back fully collapsed.
test('gallery keeps its subfolder tree expanded across a round trip', async ({ page, request }) => {
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 6)
  const ds = await createDatasetViaApi(request, `sf-expand-${Date.now()}`)
  // Both levels: `list_subfolders` derives rows from the images (plus declared paths),
  // so an upload to the leaf alone leaves `alpha` with no row to expand.
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')
  await uploadViaApi(request, ds.id, 'b.png', 'alpha/inner')

  await page.goto(`/datasets/${ds.id}/gallery`)
  // The child starts hidden: nothing has selected it, so no ancestor is open.
  await expect(page.getByTitle('alpha', { exact: true })).toBeVisible()
  await expect(page.getByTitle('alpha/inner', { exact: true })).toHaveCount(0)

  await page.getByRole('button', { name: 'Expand alpha' }).click()
  await expect(page.getByTitle('alpha/inner', { exact: true })).toBeVisible()

  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  await page.getByTestId('gallery-tile').first().click()
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()

  await expect(page.getByTitle('alpha/inner', { exact: true })).toBeVisible()
})

// The other half: a folder selected before the tree was drawn — restored from the blob
// here, a `?subfolder=` deep link in the wild — must have its ancestors opened for it,
// or the row that is highlighted as active is not on screen at all. This is the half of
// the activeSubfolder effect that stays unconditional: the ancestors go in on every run,
// including a restore, while the selected path itself only goes in on a real change (see
// the round-trip collapse test below).
test('a restored subfolder selection opens the branch containing it', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `sf-ancestors-${Date.now()}`)
  // Both levels: `list_subfolders` derives rows from the images (plus declared paths),
  // so an upload to the leaf alone leaves `alpha` with no row to expand.
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')
  await uploadViaApi(request, ds.id, 'b.png', 'alpha/inner')

  // Deliberately no `expandedPaths`: the selection alone has to reveal the row.
  await page.addInitScript(
    (key) => {
      localStorage.setItem(
        key,
        JSON.stringify({ page: 1, sortIdx: 0, captionedFilter: null, scrollTop: 0, activeSubfolder: 'alpha/inner' }),
      )
    },
    `gallery-state-${ds.id}`,
  )

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTitle('alpha/inner', { exact: true })).toBeVisible()
})

// Clicking a folder opens it, not only the branch above it — the half the ancestors rule
// does not cover, and the one a user notices first.
test('clicking a subfolder reveals what is filed inside it', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `sf-open-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')
  await uploadViaApi(request, ds.id, 'b.png', 'alpha/inner')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTitle('alpha', { exact: true })).toBeVisible()
  await expect(page.getByTitle('alpha/inner', { exact: true })).toHaveCount(0)

  // The label, not the arrow: this is the selection doing the expanding.
  await page.getByTitle('alpha', { exact: true }).click()
  await expect(page.getByTitle('alpha/inner', { exact: true })).toBeVisible()

  // And collapsing the folder you are standing in has to stick — the effect fires on the
  // change of selection, not for as long as it is selected.
  await page.getByRole('button', { name: 'Collapse alpha' }).click()
  await expect(page.getByTitle('alpha/inner', { exact: true })).toHaveCount(0)
})

// …and it has to stick across a round trip too, which a dep array alone does not give
// you: an effect fires on **mount** whether or not its dep moved, and GalleryPage unmounts
// on every trip to the detail view, so the return trip used to re-add `activeSubfolder`
// and re-open the folder the user had just closed. The blob was right; the effect
// overrode it.
test('collapsing the folder you are standing in survives a round trip', async ({ page, request }) => {
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 6)
  const ds = await createDatasetViaApi(request, `sf-collapse-${Date.now()}`)
  // Three populated levels: `list_subfolders` derives rows from the images, and the test
  // needs a row *below* the collapsed one to watch disappear.
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')
  await uploadViaApi(request, ds.id, 'b.png', 'alpha/inner')
  await uploadViaApi(request, ds.id, 'c.png', 'alpha/inner/deep')

  // Deliberately no `?subfolder=`: routed Back is `navigate(-1)`, which restores the query
  // string, and a deep link is *meant* to open the folder it names.
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTitle('alpha', { exact: true }).click()
  await page.getByTitle('alpha/inner', { exact: true }).click()
  await expect(page.getByTitle('alpha/inner/deep', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Collapse alpha/inner' }).click()
  await expect(page.getByTitle('alpha/inner/deep', { exact: true })).toHaveCount(0)

  // The blob records the collapse — this half never broke, so assert it separately from
  // what the page does with it on the way back.
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  const saved = await page.evaluate(
    (k) => JSON.parse(localStorage.getItem(k)!) as { activeSubfolder: string; expandedPaths: string[] },
    `gallery-state-${ds.id}`,
  )
  expect(saved.activeSubfolder).toBe('alpha/inner')
  expect(saved.expandedPaths).toContain('alpha')
  expect(saved.expandedPaths).not.toContain('alpha/inner')

  await page.getByTestId('gallery-tile').first().click()
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()

  // Both halves at once: the ancestor reopened, so the active row is reachable…
  await expect(page.getByTitle('alpha/inner', { exact: true })).toBeVisible()
  // …and the collapse the user asked for held.
  await expect(page.getByTitle('alpha/inner/deep', { exact: true })).toHaveCount(0)
})

// A deep link is an arrival, not a restore, so it opens the folder it names even though a
// mount is exactly when the round-trip rule above says not to. The `seenSubfolder` seed is
// what tells the two apart; drop its `linkedSubfolder !== undefined` clause and this fails.
test('a subfolder deep link opens the folder it names', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `sf-link-open-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')
  await uploadViaApi(request, ds.id, 'b.png', 'alpha/inner')

  // Seeded closed, and already naming `alpha` — so a seed that took the blob's value would
  // see no change and leave the branch shut.
  await page.addInitScript(
    (key) => {
      localStorage.setItem(
        key,
        JSON.stringify({ page: 1, sortIdx: 0, captionedFilter: null, scrollTop: 0, activeSubfolder: 'alpha', expandedPaths: [] }),
      )
    },
    `gallery-state-${ds.id}`,
  )

  await page.goto(`/datasets/${ds.id}/gallery?subfolder=alpha`)
  await expect(page.getByTitle('alpha/inner', { exact: true })).toBeVisible()
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

// The other three gallery filters — the score chips, the search box and the detection
// label — used to be held in plain component state, so every trip to the detail view
// dropped them while `page` and `scrollTop`, sampled against the *filtered* list, came
// back. The three tests below cover the debounced write path, the unmount-flush path,
// and the restore-side validation, in that order.
//
// Constraint on all of them: e2e cannot give an image a non-null score (no HTTP write
// path, no torch in the e2e image), and NULL fails every comparison — so a score filter
// matches zero images here. Assert on the chip and the blob, never on grid contents.

test('a score-range chip survives a remount', async ({ page, request }) => {
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 6)
  const ds = await createDatasetViaApi(request, `score-restore-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)

  await page.getByRole('button', { name: 'Score filter' }).click()
  await page.getByPlaceholder('min', { exact: true }).fill('5')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTitle('Remove filter')).toHaveCount(1)

  // Adding a chip moves no *other* persisted field — `applyScoreFilter` calls
  // `resetPage()`, a no-op on `page` when it is already 1 — so this is the assertion
  // that the write was scheduled by `scoreFilters` being in the effect's dep array.
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  const stored = await page.evaluate(
    (k) => JSON.parse(localStorage.getItem(k)!).scoreFilters,
    `gallery-state-${ds.id}`,
  )
  // **Strings**, and an empty `max`. This is what catches anyone persisting
  // `scoreFiltersParam` instead of the state array: that form is numbers, and it has
  // already collapsed the ""-vs-0 distinction `scoreChipLabel` reads by truthiness.
  expect(stored).toEqual([{ field: 'aesthetic_score', min: '5', max: '' }])

  // A reload rather than a tile click: with the filter on there is no tile to click,
  // which is also why this is the case that covers the debounced write.
  await page.reload()
  await expect(page.getByTitle('Remove filter')).toHaveCount(1)
  await expect(page.getByText('No images found. Upload or adjust filters.')).toBeVisible()
})

// The reported gesture, the unmount-flush write path, and the PM-012 guard in one
// journey: a search that survives Back *and* keeps the page it was left on. The page
// number is the second-order half — it was computed against the filtered list, so
// restoring it without the filter lands on page N of a wider one.
test('a search survives Back without resetting the page', async ({ page, request }) => {
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 6)
  const ds = await createDatasetViaApi(request, `search-restore-${Date.now()}`)
  // The two decoys are what make the footer discriminate: 6 images / 3 pages
  // unfiltered against 4 / 2 filtered, so a lost filter cannot read as a pass.
  for (const n of ['keep-a.png', 'keep-b.png', 'keep-c.png', 'keep-d.png', 'other-e.png', 'other-f.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('Page 1 of 3 · 6 images')).toBeVisible()

  await page.getByPlaceholder('Search filename or caption…').fill('keep')
  await expect(page.getByText('Page 1 of 2 · 4 images')).toBeVisible()

  await page.getByRole('button', { name: 'Next →' }).click()
  await expect(page.getByText('Page 2 of 2 · 4 images')).toBeVisible()

  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  const saved = await page.evaluate(
    (k) => JSON.parse(localStorage.getItem(k)!) as { page: number; search: string },
    `gallery-state-${ds.id}`,
  )
  expect(saved.page).toBe(2)
  expect(saved.search).toBe('keep')

  await page.getByTestId('gallery-tile').first().click()
  await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()

  await expect(page.getByPlaceholder('Search filename or caption…')).toHaveValue('keep')
  await expect(page.getByText('Page 2 of 2 · 4 images')).toBeVisible()
  // The load-bearing assertion. PM-012's failure restores correctly and *then* throws
  // it away: the mount debounce fires 350 ms later, commits a `search` that disagrees
  // with the input, and resets the page — which the persist effect then writes back, so
  // the loss is permanent rather than a flicker. Seeding both halves of the pair is the
  // whole of the fix.
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  await expect(page.getByPlaceholder('Search filename or caption…')).toHaveValue('keep')
  await expect(page.getByText('Page 2 of 2 · 4 images')).toBeVisible()
})

// The blob outlives the build that wrote it, and a score entry naming a field this build
// no longer has is worse than an unknown license id: the backend `continue`s past it and
// its `except … pass` abandons the rest of the list, so the chip stays on screen applying
// nothing *and* the entries after it stop being applied while their chips stay lit.
// `sanitizeScoreFilters`' entire contract is that exactly the one good entry survives.
test('a malformed stored score filter is dropped', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `score-sanitize-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  await page.addInitScript(
    (key) => {
      localStorage.setItem(
        key,
        JSON.stringify({
          page: 1, sortIdx: 0, captionedFilter: null, scrollTop: 0,
          detectionLabel: 'cat',
          scoreFilters: [
            { field: 'aesthetic_score', min: '5', max: '' },   // the one keeper
            { field: 'no_such_score', min: '1', max: '' },     // field this build lacks
            { field: 'blur_score', min: 'abc', max: '' },      // bound that is not a number
            { field: 'noise_score', min: '', max: '' },        // no bound at all
            null,
            'nonsense',
          ],
        }),
      )
    },
    `gallery-state-${ds.id}`,
  )

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTitle('Remove filter')).toHaveCount(1)
  // Same seeding rule as `search`, so the detection label rides along here rather than
  // getting a journey of its own.
  await expect(page.getByPlaceholder('Objects: cat, dog…')).toHaveValue('cat')
})

// Persisting the three turned a cosmetic omission in `handleResetFilters` into a visible
// one: it removes the blob, so a filter it forgets is re-written into a fresh blob by the
// debounced persist 350 ms later and Reset undoes itself on screen. The delayed half of
// this test is the one that fails in that case — and clearing only the *committed* half of
// a debounced pair fails it the same way, since the input's own timer re-commits.
test('Reset filters clears all three and they stay cleared', async ({ page, request }) => {
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 8)
  const ds = await createDatasetViaApi(request, `reset-filters-${Date.now()}`)
  for (const n of ['keep-a.png', 'other-b.png']) await uploadViaApi(request, ds.id, n)

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByPlaceholder('Search filename or caption…').fill('keep')
  await page.getByPlaceholder('Objects: cat, dog…').fill('cat')
  await page.getByRole('button', { name: 'Score filter' }).click()
  await page.getByPlaceholder('min', { exact: true }).fill('5')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTitle('Remove filter')).toHaveCount(1)
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)

  await page.getByRole('button', { name: 'Reset filters' }).click()
  await expect(page.getByPlaceholder('Search filename or caption…')).toHaveValue('')
  await expect(page.getByPlaceholder('Objects: cat, dog…')).toHaveValue('')
  await expect(page.getByTitle('Remove filter')).toHaveCount(0)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  await expect(page.getByPlaceholder('Search filename or caption…')).toHaveValue('')
  await expect(page.getByTitle('Remove filter')).toHaveCount(0)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)
  expect(
    await page.evaluate((k) => JSON.parse(localStorage.getItem(k)!), `gallery-state-${ds.id}`),
  ).toMatchObject({ search: '', detectionLabel: '', scoreFilters: [] })
})

// The × the search box gained alongside the persistence — the one-click way out of a
// filter that now survives a restart. It clears both halves of the pair synchronously,
// which is what makes the debounce stay quiet afterwards.
test('the search box × clears the query and returns to page 1', async ({ page, request }) => {
  test.setTimeout(30_000 + PERSIST_DEBOUNCE_MS * 6)
  const ds = await createDatasetViaApi(request, `search-clear-${Date.now()}`)
  for (const n of ['keep-a.png', 'keep-b.png', 'keep-c.png', 'keep-d.png', 'other-e.png', 'other-f.png']) {
    await uploadViaApi(request, ds.id, n)
  }
  await page.addInitScript(() => localStorage.setItem('gallery-page-size', '2'))
  await page.goto(`/datasets/${ds.id}/gallery`)

  await page.getByPlaceholder('Search filename or caption…').fill('keep')
  await expect(page.getByText('Page 1 of 2 · 4 images')).toBeVisible()
  await page.getByRole('button', { name: 'Next →' }).click()
  await expect(page.getByText('Page 2 of 2 · 4 images')).toBeVisible()

  // Scoped to the wrapper: the detection-label input's clear button carries the same title.
  await page.locator('.search-wrap').getByTitle('Clear').click()
  await expect(page.getByPlaceholder('Search filename or caption…')).toHaveValue('')
  await expect(page.getByText('Page 1 of 3 · 6 images')).toBeVisible()
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
  await expect(page.getByText('Page 1 of 3 · 6 images')).toBeVisible()
})
