import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The whole label journey in one pass, because every step depends on the one
// before it: the vocabulary has to exist before anything can be applied, and the
// filter has nothing to narrow until something has been.
//
// Labels are a *second* axis of organisation alongside subfolders, so the thing
// worth proving end to end is that the same label reached three surfaces from
// one bulk apply — the toolbar filter, the card dots, and the detail-view hotkey.
test('create a label, bulk-apply it, filter by it, then toggle it off with the hotkey', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `labels-${Date.now()}`)
  for (const n of ['a.png', 'b.png']) {
    await uploadViaApi(request, ds.id, n)
  }

  // ── Settings → Labels: create one, with a hotkey.
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Labels', exact: true }).click()

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

  await page.getByRole('button', { name: 'Labels', exact: true }).click()
  const modal = page.getByRole('dialog', { name: 'Edit labels' })
  await expect(modal).toBeVisible()
  await modal.getByRole('group', { name: 'Labels to add' }).getByRole('checkbox', { name }).check()
  await modal.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByText(/Labels updated on 2 images/)).toBeVisible()

  // ── The dots appear on the cards.
  await expect(page.getByLabel(`Labels: ${name}`).first()).toBeVisible()

  // ── Take it back off b.png, so the filter below has something to narrow.
  // Without this every assertion in this block is 2, which is what the grid shows
  // unfiltered — the filter could be doing nothing and the test would pass.
  // The apply above clears the selection, so this starts from nothing.
  await page.getByTestId('select-b.png').click()
  await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: 'Labels', exact: true }).click()
  await expect(modal).toBeVisible()
  await modal.getByRole('group', { name: 'Labels to remove' }).getByRole('checkbox', { name }).check()
  await modal.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByText(/Labels updated on 1 image/)).toBeVisible()

  // ── The toolbar filter narrows the grid: 2 → 1, and "Unlabelled" is its
  // complement. The vocabulary lives behind a dropdown rather than a chip per
  // label, so every locator here is scoped to the open panel — "Unlabelled" in
  // particular was page-level while it was a bare toolbar toggle.
  const labelTrigger = page.getByRole('button', { name: 'Filter by label' })
  await labelTrigger.click()
  const panel = page.getByRole('group', { name: 'Label filters' })
  await expect(panel.getByRole('checkbox', { name: new RegExp(name) })).toBeVisible()

  await panel.getByRole('button', { name: 'Unlabelled' }).click()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)

  await panel.getByRole('button', { name: 'Unlabelled' }).click()
  await panel.getByRole('checkbox', { name: new RegExp(name) }).check()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)

  // Escape closes the panel without disturbing the page behind it, and the
  // collapsed trigger still says what is being filtered — the whole reason the
  // trigger summarises its own state.
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(labelTrigger).toContainText(name)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)

  // Reset clears it, and the grid comes back unfiltered.
  await page.getByRole('button', { name: 'Reset filters' }).click()
  await expect(page.getByText('Gallery filters reset')).toBeVisible()
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)

  // ── Detail view: the hotkey toggles the chip off. Toggle semantics are the
  // point — a mistyped key is undone with the same key.
  // The labelled card specifically — only a.png still carries it after the
  // removal above, and which of the two sorts first is not this test's business.
  await page.getByTestId('gallery-tile')
    .filter({ has: page.getByLabel(`Labels: ${name}`) })
    .click()
  const labelBlock = page.getByRole('group', { name: 'Labels' })
  await expect(labelBlock.getByText(name)).toBeVisible()

  await page.keyboard.press('f')
  await expect(labelBlock.getByText(name)).toHaveCount(0)
  await expect(labelBlock.getByText('None')).toBeVisible()

  await page.keyboard.press('f')
  await expect(labelBlock.getByText(name)).toBeVisible()

  // ── The same block's picker is the mouse path to the same assign, and it
  // stays open across a toggle so several labels can be attached in one visit.
  // `.click()`, not `.check()`: these boxes are driven by the server's answer,
  // so the tick only lands once the assign round-trips — `check()` asserts the
  // state flipped the instant it clicked and fails on the intermediate frame.
  await page.getByRole('button', { name: 'Add or remove labels' }).click()
  const vocab = page.getByRole('group', { name: 'Label vocabulary' })
  await vocab.getByRole('checkbox', { name }).click()
  await expect(labelBlock.getByText('None')).toBeVisible()
  await expect(vocab).toBeVisible()

  await vocab.getByRole('checkbox', { name }).click()
  await expect(vocab.getByRole('checkbox', { name })).toBeChecked()

  await page.keyboard.press('Escape')
  await expect(vocab).toHaveCount(0)
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
  await page.getByRole('button', { name: 'Labels', exact: true }).click()
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
