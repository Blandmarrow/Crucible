import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// `Image.scores_stale`, end to end through the two screens that surface it.
//
// The in-place rewrite is driven with a batch resize, which is the one such path
// with no ML dependency at all — the crop/LUT/upscale/re-extract siblings need
// torch or cv2, and the point here is the badge, not the pixels.
//
// What this cannot reach is the *clear*: `POST /quality/score` imports the
// aesthetic scorer at job start, so it needs torch, which the e2e runner does
// not have. That half lives in `backend/tests/test_scores_stale.py`, driven with
// stubbed scorers.
test('an in-place edit marks the image stale in the gallery and warns at export', async ({
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

  // Nothing is stale before the edit — otherwise the badge below would prove
  // nothing about the resize.
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

  // Gallery: one card wears the badge, the other does not.
  await page.goto(`/datasets/${ds.id}/gallery`)
  const badge = page.getByTitle(/Scores are stale/)
  await expect(badge).toHaveCount(1)

  // Detail page: the flag row carries it, and the row's own render condition has
  // to include it — this image has no quality flags at all, so a condition that
  // forgot it would render nothing.
  await page.goto(`/datasets/${ds.id}/image/${edited.id}`)
  await expect(page.getByText('Scores stale', { exact: true })).toBeVisible()

  // Export: the advisory warning, and the numbers behind it.
  const after = await (await request.get(`/api/v1/export/preview/${ds.id}`)).json()
  expect(after.stale_scores_count).toBe(1)
  expect(after.stale_scores_will_export).toBe(1)

  await page.goto(`/datasets/${ds.id}/export`)
  await expect(
    page.getByText('after being scored', { exact: false }),
  ).toBeVisible()
})
