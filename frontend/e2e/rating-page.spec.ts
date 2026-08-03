import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The Aesthetic Rating page — the two measurements that decide whether a learned
// aesthetic head is worth building, rendered against a real corpus.
//
// **This is the one page in the suite with no dataset scope.** It pools ratings
// across every dataset by design, so the whole shared e2e database is its corpus
// and every other spec's images are in it. Absolute counts are therefore not
// assertable: this spec asserts *deltas* against a baseline it takes first (which
// is what proves the write path), and then asserts the rendered tiles against the
// API's own post-write values (which is what proves the page).
//
// **No scorer is ever run**, as in `quality.spec.ts`: the scorers import
// cv2/torch, which CI does not have. That is not a compromise — a rated but
// unscored corpus is a genuine day-one state, and its empty state is one of the
// things the page has to get right.
//
// The claim the spec exists for is the refusal: one re-rating produces one
// comparable pair, far below the floor, and the page must say so in words rather
// than render "0%" or "100%" from a sample of one.
test('the rating page reports its corpus and withholds an unearned ceiling', async ({
  page,
  request,
}) => {
  const summary = async () => {
    const r = await request.get('/api/v1/rating/summary')
    expect(r.status(), await r.text()).toBe(200)
    return r.json()
  }

  const before = await summary()

  const ds = await createDatasetViaApi(request, `rating-page-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')

  // `uploadViaApi` returns the stored filename, not a row, so the id comes from
  // the select-all endpoint — the one that states the whole filtered set.
  const idsRes = await request.get('/api/v1/images/ids', {
    params: { dataset_id: ds.id, sort: 'filename', order: 'asc' },
  })
  expect(idsRes.status(), await idsRes.text()).toBe(200)
  const { ids } = await idsRes.json()
  expect(ids).toHaveLength(2)

  const rate = async (rating: number) => {
    const r = await request.post('/api/v1/images/bulk-rating', {
      data: { dataset_id: ds.id, image_ids: [ids[0]], rating },
    })
    expect(r.status(), await r.text()).toBe(200)
  }
  // Rated, then re-rated with a *different* value: one image, two events, so
  // exactly one comparable pair appears and it is a disagreement.
  await rate(4)
  await rate(2)

  const after = await summary()

  // ── The write path, as deltas ──
  expect(after.total).toBe(before.total + 2)
  expect(after.rated).toBe(before.rated + 1)
  expect(after.unrated).toBe(before.unrated + 1)
  expect(after.events.total).toBe(before.events.total + 2)
  expect(after.events.images_with_repeats).toBe(before.events.images_with_repeats + 1)
  expect(after.self_agreement.pairs).toBe(before.self_agreement.pairs + 1)
  expect(after.self_agreement.agreements).toBe(before.self_agreement.agreements)

  // The floor branch is asserted below, so fail loudly here if the shared corpus
  // ever grows past it rather than silently testing the other branch.
  expect(after.self_agreement.pairs).toBeLessThan(10)

  await page.goto('/rating')
  await expect(page.getByRole('heading', { name: 'Aesthetic Rating' })).toBeVisible()

  // ── Tiles, against the API's own values ──
  // By test id, not by label text: three of the four tile labels contain the
  // substring "Rated", so a text filter is ambiguous.
  const tile = (id: string) => page.getByTestId(`tile-${id}`).locator('.sv')
  const ratedPct = Math.round((after.rated / after.total) * 100)
  await expect(tile('rated')).toContainText(
    `${ratedPct}% of ${after.total.toLocaleString('en-US')}`,
  )
  await expect(tile('unrated')).toHaveText(after.unrated.toLocaleString('en-US'))
  await expect(tile('rerated')).toHaveText(
    after.events.images_with_repeats.toLocaleString('en-US'),
  )
  // Below the floor, so the tile shows an em dash rather than a number.
  await expect(page.getByTestId('self-agreement-value')).toHaveText('—')

  // ── Distribution ──
  const dist = page.locator('section').filter({ hasText: 'Rating distribution' })
  // `toLocaleString`, like the page: the corpus here is the whole shared e2e
  // database, so a raw number starts failing the moment it passes 999 and reads
  // as a page bug rather than a spec one.
  await expect(dist).toContainText(
    `${after.rated.toLocaleString('en-US')} rated, ${after.unrated.toLocaleString('en-US')} unrated`,
  )
  // Every tier renders even at zero, so the four bars need no defaulting.
  for (const label of ['Keep', 'Probably', 'Probably not', 'Cut']) {
    await expect(dist.getByText(label, { exact: true })).toBeVisible()
  }

  // ── Self-agreement: the refusal, and the caveat that is always present ──
  const ceiling = page.locator('section').filter({ hasText: 'Your own ceiling' })
  await expect(ceiling).toContainText('Not enough re-ratings yet')
  await expect(ceiling).toContainText(
    `${after.self_agreement.pairs.toLocaleString('en-US')} comparable pair`,
  )
  await expect(ceiling).toContainText('not a blind re-show')
  // A ceiling from one pair is noise wearing a number; no percentage is offered.
  await expect(ceiling.getByText('%')).toHaveCount(0)

  // ── Scorer agreement: the empty state, since nothing in the suite is scored ──
  const scorerRes = await request.get('/api/v1/rating/scorer-agreement')
  expect(scorerRes.status(), await scorerRes.text()).toBe(200)
  const scorerBody = await scorerRes.json()
  // Absolute counts, not deltas — the one place this spec can use them. They hold
  // only because no spec can run a scorer (torch is absent in CI) and no endpoint
  // writes `aesthetic_score`, so the scored population of the shared corpus is
  // permanently empty. Same footing as the `toBeLessThan(10)` tripwire above: if
  // that ever changes, these fail loudly rather than drifting.
  expect(scorerBody.scored_and_rated).toBe(0)
  expect(scorerBody.models).toEqual([])

  const scorer = page
    .locator('section')
    .filter({ hasText: 'Does an existing scorer already know your taste?' })
  await expect(scorer).toContainText('No image is both rated and scored yet')
  // It names what is missing rather than just saying "no data".
  await expect(scorer).toContainText(
    `${scorerBody.rated_unscored.toLocaleString('en-US')} rated image`,
  )
})
