import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadVideoViaApi, uploadViaApi } from './helpers'

// Deleting videos from the gallery strip — the button, the Delete key, and the
// precedence rule that keeps the key from meaning two things at once.
//
// Before this existed a video could only be deleted from its own detail page,
// one at a time. The strip keeps its selection local (mixing video ids into the
// image-typed `selectionStore` would corrupt SelectionToolbar's cross-dataset
// breakdown), which is exactly why the two Delete bindings have to agree on who
// wins — see docs/dev/video-ui.md.

test('videos are deleted from the strip by button and by the Delete key', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-vdel-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'first.mp4')
  await uploadVideoViaApi(request, ds.id, 'second.mp4')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByRole('button', { name: /^Videos/ })).toBeVisible()

  // ── The button path ──
  await page.getByRole('checkbox', { name: 'Select first.mp4' }).click()
  await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()

  const confirm = page.getByRole('dialog', { name: 'Delete 1 video?' })
  await expect(confirm).toBeVisible()
  // The Phase 0 contract is stated, because it is not what a user expects.
  await expect(confirm.getByText(/Extracted frames keep their files/)).toBeVisible()
  await confirm.getByRole('button', { name: 'Delete' }).click()

  await expect(page.getByRole('checkbox', { name: 'Select first.mp4' })).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: 'Select second.mp4' })).toBeVisible()
  // Nothing stays selected once the delete lands.
  await expect(page.getByText('1 selected')).toHaveCount(0)

  // ── The Delete-key path ──
  await page.getByRole('checkbox', { name: 'Select second.mp4' }).click()
  await expect(page.getByText('1 selected')).toBeVisible()
  await page.keyboard.press('Delete')
  const confirm2 = page.getByRole('dialog', { name: 'Delete 1 video?' })
  await expect(confirm2).toBeVisible()
  await confirm2.getByRole('button', { name: 'Delete' }).click()

  // The last video goes and the strip disappears with it — an image-only dataset
  // looks exactly as it did before the strip existed.
  await expect(page.getByRole('button', { name: /^Videos/ })).toHaveCount(0)
})

test('Delete keeps its image meaning while images are also selected', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-vdel-prec-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  await uploadViaApi(request, ds.id, 'still.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByRole('button', { name: /^Videos/ })).toBeVisible()

  // Both kinds selected. The two selections live in separate stores, which is
  // the whole reason this rule has to be written down.
  const videoCheckbox = page.getByRole('checkbox', { name: 'Select clip.mp4' })
  await videoCheckbox.click()
  await expect(videoCheckbox).toHaveAttribute('aria-checked', 'true')
  await page.getByTestId('select-still.png').click()
  // Two "1 selected" labels — the strip's and SelectionToolbar's. That both are
  // on screen at once *is* the state under test.
  await expect(page.getByText('1 selected', { exact: true })).toHaveCount(2)

  await page.keyboard.press('Delete')

  // Exactly one confirm, and it is the image one — the strip stands down.
  await expect(page.getByRole('dialog', { name: 'Delete 1 video?' })).toHaveCount(0)
  const imageConfirm = page.getByRole('dialog', { name: 'Delete 1 Images' })
  await expect(imageConfirm).toBeVisible()
  await imageConfirm.getByRole('button', { name: 'Cancel' }).click()

  // And the video is untouched by any of it.
  await expect(videoCheckbox).toHaveAttribute('aria-checked', 'true')
})
