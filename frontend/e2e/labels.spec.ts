import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The whole label journey in one pass, because every step depends on the one
// before it: the vocabulary has to exist before anything can be applied, and the
// filter has nothing to narrow until something has been.
//
// Labels are a *second* axis of organisation alongside subfolders, so the thing
// worth proving end to end is that the same label reached three surfaces from
// one bulk apply — the chip filter, the card dots, and the detail-view hotkey.
test('create a label, bulk-apply it, filter by it, then toggle it off with the hotkey', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `labels-${Date.now()}`)
  for (const n of ['a.png', 'b.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  // ── Settings → Labels: create one, with a hotkey.
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Labels' }).click()

  const name = `fx-${Date.now()}`
  await page.getByLabel('New label name').fill(name)
  await page.getByRole('button', { name: 'Add label' }).click()
  await expect(page.getByText('Label created')).toBeVisible()

  // The hotkey charset is [a-z0-9] and nothing else, which is what keeps it
  // from ever colliding with Escape/Space/Arrow/Delete in the detail view.
  await page.getByRole('button', { name: 'Set hotkey' }).click()
  await page.keyboard.press('f')
  await expect(page.getByRole('button', { name: 'Hotkey f' })).toBeVisible()

  // ── Gallery: select everything and apply it.
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('2 selected')).toBeVisible()

  await page.getByRole('button', { name: 'Labels' }).click()
  const modal = page.getByRole('dialog', { name: 'Edit labels' })
  await expect(modal).toBeVisible()
  await modal.getByRole('group', { name: 'Labels to add' }).getByRole('button', { name }).click()
  await modal.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByText(/Labels updated on 2 images/)).toBeVisible()

  // ── The dots appear on the cards.
  await expect(page.getByLabel(`Labels: ${name}`).first()).toBeVisible()

  // ── The chip filter narrows the grid. Only one image is detached first, so
  // "narrows" means something: 2 → 1.
  const chips = page.getByRole('group', { name: 'Label filters' })
  await expect(chips.getByRole('button', { name: new RegExp(name) })).toBeVisible()

  await page.getByRole('button', { name: 'Unlabelled' }).click()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(0)

  await page.getByRole('button', { name: 'Unlabelled' }).click()
  await chips.getByRole('button', { name: new RegExp(name) }).click()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  // Reset clears it, and the grid comes back unfiltered.
  await page.getByRole('button', { name: 'Reset filters' }).click()
  await expect(page.getByText('Gallery filters reset')).toBeVisible()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  // ── Detail view: the hotkey toggles the chip off. Toggle semantics are the
  // point — a mistyped key is undone with the same key.
  await page.getByTestId('gallery-tile').first().click()
  const labelBlock = page.getByRole('group', { name: 'Labels' })
  await expect(labelBlock.getByText(name)).toBeVisible()

  await page.keyboard.press('f')
  await expect(labelBlock.getByText(name)).toHaveCount(0)
  await expect(labelBlock.getByText('None')).toBeVisible()

  await page.keyboard.press('f')
  await expect(labelBlock.getByText(name)).toBeVisible()
})

// The guard that makes the hotkeys usable at all: the caption editor is a
// <textarea> on this page, so without `isTextEntryTarget` typing a caption would
// label the image. Worth its own journey because it is the failure that would
// only show up once someone actually used the feature.
test('typing a bound key into the caption box does not label the image', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `labels-caption-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  const name = `fx-${Date.now()}`
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Labels' }).click()
  await page.getByLabel('New label name').fill(name)
  await page.getByRole('button', { name: 'Add label' }).click()
  await page.getByRole('button', { name: 'Set hotkey' }).click()
  await page.keyboard.press('f')
  await expect(page.getByRole('button', { name: 'Hotkey f' })).toBeVisible()

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('gallery-tile').first().click()

  const labelBlock = page.getByRole('group', { name: 'Labels' })
  await expect(labelBlock.getByText('None')).toBeVisible()

  await page.getByPlaceholder('Natural language description', { exact: false }).fill('a fluffy fox')
  // Still unlabelled: the "f"s went into the caption, not into an assign.
  await expect(labelBlock.getByText('None')).toBeVisible()
})
