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
  await expect(dialog.getByRole('spinbutton', { name: /Frames per shot|^$/ }).first()).toBeVisible()

  // Close without submitting — the expensive button is never pressed.
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)

  // No frames were produced, so the history panel stays hidden entirely.
  await expect(page.getByRole('heading', { name: 'Extracted frames' })).toHaveCount(0)
})
