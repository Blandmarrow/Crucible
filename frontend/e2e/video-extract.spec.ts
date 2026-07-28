import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadVideoViaApi, uploadViaApi } from './helpers'

// The frame-extraction surface, GPU-free: the video strip and its selection, the
// two-step modal, and the controls each step owns. **Extract is never clicked** —
// the job decodes the whole file and needs scenedetect, which CI does not have,
// so a click here would fail the job body rather than the page. Same convention
// as quality.spec.ts.
//
// CI runs with `capabilities: { shot_detection: false, deinterlace: false }`
// (only opencv is installed), and a dev machine after `manage.sh update` runs
// with both true. Every assertion below therefore checks that a control is
// *present*, never that it is enabled — the disabled-deinterlace and
// fixed-interval-sampling branches are real behaviour in one of those two worlds.
test('a video can be driven through the extraction modal', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-${Date.now()}`)
  const video = await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  // One ordinary image too, so the strip is demonstrably separate from the grid.
  await uploadViaApi(request, ds.id, 'still.png')

  await page.goto(`/datasets/${ds.id}/gallery`)

  // The strip: header, count, and a checkbox per card.
  await expect(page.getByRole('button', { name: /^Videos/ })).toBeVisible()
  const checkbox = page.getByRole('checkbox', { name: 'Select clip.mp4' })
  await expect(checkbox).toBeVisible()
  await expect(checkbox).toHaveAttribute('aria-checked', 'false')

  // Selecting reveals the batch action row without navigating away.
  await checkbox.click()
  await expect(checkbox).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: 'Clear' }).click()
  await expect(page.getByText('1 selected')).toHaveCount(0)

  // Into the detail view via the card body.
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await expect(page.getByRole('heading', { name: 'Video Info' })).toBeVisible()

  // Step 1. The probe is a request, not a job — wait for it rather than for a
  // spinner to disappear.
  const probe = page.waitForResponse(
    (res) => res.url().includes(`/api/v1/videos/${video.id}/probe`) && res.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })
  await expect(dialog).toBeVisible()
  expect((await probe).status()).toBe(200)

  // The filmstrip, the crop rect's keyboard path, the deinterlace toggle and the
  // trim track — the four things step 1 exists to offer.
  await expect(dialog.getByTestId('probe-filmstrip').getByRole('button')).not.toHaveCount(0)
  for (const field of ['x', 'y', 'w', 'h']) {
    await expect(dialog.getByRole('spinbutton', { name: `Crop ${field}` })).toBeVisible()
  }
  await expect(dialog.getByRole('button', { name: 'Use detected' })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Clear crop' })).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: /Deinterlace/ })).toBeVisible()
  await expect(dialog.getByTestId('trim-bar')).toBeVisible()

  // Step 2 — the three re-extraction modes, with New subfolder the default.
  await dialog.getByRole('button', { name: 'Next' }).click()
  const mode = (name: RegExp) => dialog.getByRole('radio', { name })
  await expect(mode(/^New subfolder/)).toBeChecked()
  await expect(mode(/^Add to/)).toBeVisible()
  await expect(mode(/^Replace/)).toBeVisible()
  // Nothing has been extracted yet, so Replace says so instead of naming a count.
  await expect(dialog.getByText('Replace (nothing to replace yet)')).toBeVisible()
  await expect(dialog.getByRole('spinbutton', { name: /Frames per shot/ })).toBeVisible()

  // Close without submitting — the expensive button is never pressed.
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)

  // No frames were produced, so the history panel stays hidden entirely.
  await expect(page.getByRole('heading', { name: 'Extracted frames' })).toHaveCount(0)
})

// The two modes resolve a subfolder differently — `new_subfolder` steps whatever
// name it is given through `_step_subfolder`, so offering an existing folder
// there would silently produce `{name}_2`. The control has to differ, and this is
// the only automated check that it does.
test('existing subfolders are offered in Add mode only', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-sub-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  // A subfolder that actually holds an image, or the dropdown lists nothing in
  // either mode and the assertion below passes for the wrong reason.
  await uploadViaApi(request, ds.id, 'still.png', 'existing')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })
  await dialog.getByRole('button', { name: 'Next' }).click()

  const options = () => dialog.getByTestId('extract-subfolder').locator('option').allTextContents()

  // New subfolder (the default): Automatic + Name it…, nothing else.
  expect(await options()).toHaveLength(2)
  expect((await options()).join(' | ')).toContain('a new subfolder named after the video')

  await dialog.getByRole('radio', { name: /^Add to/ }).check()
  const added = await options()
  expect(added).toHaveLength(3)
  // The label changes too: an empty subfolder means something different here.
  expect(added.join(' | ')).toContain("this video's previous subfolder")
  expect(added.join(' | ')).toContain('existing')

  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

// Extraction needs no probe — only step 1's previews do — so a video whose probe
// fails must still be extractable. It was not: `Next` was gated on the probe.
test('a failed probe still reaches step 2, and says what is missing', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-noprobe-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'clip.mp4')

  await page.route('**/api/v1/videos/*/probe', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"probe failed"}' }),
  )

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })

  await expect(dialog.getByText(/could not be sampled/)).toBeVisible()
  const next = dialog.getByRole('button', { name: 'Next' })
  await expect(next).toBeEnabled()
  await next.click()
  await expect(dialog.getByRole('radio', { name: /^New subfolder/ })).toBeChecked()

  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

// Pass 2 — the gallery entry point. The button is deliberately ungated: the
// selection store holds ids only and a selection can span pages and datasets, so
// the *preview endpoint* is what says what will actually run. CI has no
// scenedetect, so no lineage-carrying frame can exist here — which makes this the
// honest test of the accounting, and the reason it is never submitted.
test('the re-extract form reports what it can and cannot do', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-reextract-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'still.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  // The gallery checkbox is an overlay div on the card, not an <input>.
  await page.getByTestId('select-still.png').click()
  await expect(page.getByText('1 selected')).toBeVisible()

  const preview = page.waitForResponse(
    (res) => res.url().includes('/api/v1/videos/reextract/preview') && res.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Re-extract' }).click()
  const modal = page.locator('.card', { hasText: 'Re-extract at Full Resolution' })
  await expect(modal).toBeVisible()
  expect((await preview).status()).toBe(200)

  // The accounting, straight from the endpoint that would do the work.
  await expect(modal.getByText('0 frames from 0 videos will be re-extracted')).toBeVisible()
  await expect(modal.getByText('1 skipped (not extracted from a video)')).toBeVisible()

  // The controls, and the note that pass 2 does not re-score.
  await expect(modal.getByRole('radio', { name: 'JPEG' })).toBeChecked()
  await expect(modal.getByRole('radio', { name: /PNG/ })).toBeVisible()
  await expect(modal.getByRole('spinbutton')).toHaveAttribute('placeholder', 'native')
  await expect(modal.getByText(/Quality scores were measured on the triage frames/)).toBeVisible()

  // Nothing is eligible, so the expensive button is not even offered.
  await expect(modal.getByRole('button', { name: 'Re-extract' })).toBeDisabled()
  await modal.getByRole('button', { name: 'Cancel' }).click()
  await expect(modal).toHaveCount(0)
})
