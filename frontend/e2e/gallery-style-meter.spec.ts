import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The gallery style-match meter, covered at the two points a test can actually
// reach without a GPU.
//
// **No scoring journey here, deliberately.** Writing `style_similarity_score`
// needs CLIP or DINOv2 embeddings, and `frontend/e2e/serve.sh` has torch on a dev
// machine and not in CI — a spec that scored for real would pass locally and fail
// on the runner. The scoring half is pinned from the backend instead
// (`backend/tests/test_style_similarity_run_http.py`,
// `backend/tests/test_style_distribution_http.py`), which drives the real POST
// with seeded float16 blobs.
//
// What is left is worth having: the meter must be **invisible** on a dataset that
// has never been style-scored — that is the state almost every dataset is in, and
// a meter appearing there (at zero width, say) would be a visible regression on
// every gallery — and the preference must survive a reload.

test('no meter appears on a dataset that has never been style-scored', async ({
  page,
  request,
}) => {
  const ds = await createDatasetViaApi(request, `e2e-style-meter-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  // Anchored on the tiles first: an absence assertion against a blank page passes
  // for the wrong reason.
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)
  await expect(page.getByTestId('style-meter')).toHaveCount(0)
})

test('the gallery style meter preference persists across a reload', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Gallery' }).click()

  const toggle = page.getByTestId('style-meter-toggle').getByRole('checkbox')
  // On by default — the absent localStorage key must mean on.
  await expect(toggle).toBeChecked()

  await toggle.uncheck()
  await expect(page.getByText('Preference saved')).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'Gallery' }).click()
  await expect(page.getByTestId('style-meter-toggle').getByRole('checkbox')).not.toBeChecked()

  // Restore, so the default state is what a following spec sees.
  await page.getByTestId('style-meter-toggle').getByRole('checkbox').check()
  await expect(page.getByTestId('style-meter-toggle').getByRole('checkbox')).toBeChecked()
})
