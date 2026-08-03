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
