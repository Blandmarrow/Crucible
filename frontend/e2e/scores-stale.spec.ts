import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// `Image.scores_stale` on the journey that is by far the commonest: upload,
// edit in place, export — with no scoring anywhere.
//
// The bit says the ten `*_score` columns and the `quality_flags` derived from
// them were measured against pixels that no longer exist. An image that has
// never been scored carries no such measurement, so it must stay clean: the
// gallery badge, the detail chip and the export warning ("edited in place after
// being scored") were all appearing on rows with no scores and no flags.
//
// **The positive case — an edit on a *scored* image — is deliberately absent,
// and cannot be restored here.** No HTTP surface can write a score: there is no
// `PATCH /images/{id}` and no score field on any input schema, so only the
// copy/restore paths ever carry pre-existing scores; and `POST /quality/score`
// imports the aesthetic scorer at job start, which needs torch, which the e2e
// runner does not have. Both halves of the positive journey live in
// `backend/tests/test_scores_stale.py`, driven with stubbed scorers.
//
// The in-place rewrite is driven with a batch resize, the one such path with no
// ML dependency at all — its crop/LUT/upscale/re-extract siblings need torch or
// cv2.
test('an in-place edit of a never-scored image leaves it clean everywhere', async ({
  page,
  request,
}) => {
  const ds = await createDatasetViaApi(request, `e2e-stale-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'edited.png')
  await uploadViaApi(request, ds.id, 'untouched.png')

  const listing = await (
    await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })
  ).json()
  const rows = Array.isArray(listing) ? listing : listing.images
  const edited = rows.find((r: { filename: string }) => r.filename === 'edited.png')
  expect(edited, JSON.stringify(rows)).toBeTruthy()

  const before = await (await request.get(`/api/v1/export/preview/${ds.id}`)).json()
  expect(before.stale_scores_count).toBe(0)

  const resize = await request.post('/api/v1/images/batch/resize', {
    data: { image_ids: [edited.id], width: 16, height: 16, maintain_ar: false },
  })
  expect(resize.status(), await resize.text()).toBe(200)
  const jobId = (await resize.json()).job_id
  await expect
    .poll(
      async () => (await (await request.get(`/api/v1/jobs/${jobId}`)).json()).status,
      { timeout: 30_000 },
    )
    .toBe('completed')

  // The resize really happened — asserted from the payload, so this spec cannot
  // pass by doing nothing and then finding nothing stale.
  const after = await (await request.get(`/api/v1/images/${edited.id}`)).json()
  expect(after.width).toBe(16)
  expect(after.scores_stale).toBe(false)

  // Gallery: no card wears the badge. Anchored on the tiles being rendered
  // first, or an absence assertion would pass against a blank page.
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)
  await expect(page.getByTitle(/Scores are stale/)).toHaveCount(0)

  // Detail page: no chip either — again anchored on the page having rendered.
  await page.goto(`/datasets/${ds.id}/image/${edited.id}`)
  await expect(
    page.getByPlaceholder('Natural language description', { exact: false }),
  ).toBeVisible()
  await expect(page.getByText('Scores stale', { exact: true })).toHaveCount(0)

  // Export: nothing counted, and no advisory warning rendered.
  const preview = await (await request.get(`/api/v1/export/preview/${ds.id}`)).json()
  expect(preview.stale_scores_count).toBe(0)
  expect(preview.stale_scores_will_export).toBe(0)

  // Anchored on the *preview payload* having rendered, not merely the page: the
  // sibling unlicensed warning comes from the same fetch, so waiting for the
  // button alone would let the stale assertion pass before the data arrives.
  await page.goto(`/datasets/${ds.id}/export`)
  await expect(page.getByText('have no license recorded', { exact: false })).toBeVisible()
  await expect(page.getByText('after being scored', { exact: false })).toHaveCount(0)
})
