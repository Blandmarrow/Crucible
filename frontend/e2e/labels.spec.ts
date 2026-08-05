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
  // The *popover* placement autofocuses its search box — a panel that opened on
  // top of the page and closes on Escape can take the keyboard. Asserted so the
  // inline assertion further down cannot be "fixed" by dropping the autofocus
  // outright.
  await expect(panel.getByRole('textbox')).toBeFocused()

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

  // The inline placement must *not* autofocus its search box. The panel stays
  // open, and `isTextEntryTarget` reports any focused INPUT as text entry — so
  // an autofocused search box would silently disable this page's arrows, Space,
  // Delete and every label hotkey for as long as the picker is open.
  await expect(vocab.getByRole('textbox')).not.toBeFocused()
  // The proof that matters is the keyboard itself, not where focus sits.
  // Asserted *before* the first checkbox click below: a clicked checkbox is a
  // focused `<input>` too, and that hotkeys go dead until focus leaves it is a
  // known limitation of the shared `isTextEntryTarget` guard, not this panel's.
  // The chip's own remove button, not `getByText(name)`: with the panel open the
  // name is on screen twice, once as the chip and once as a row in the picker.
  const chip = labelBlock.getByRole('button', { name: `Remove label ${name}` })
  await page.keyboard.press('f')
  await expect(chip).toHaveCount(0)
  await page.keyboard.press('f')
  await expect(chip).toBeVisible()

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

// No dataset and no images: this is purely about the Settings row's colour
// popover. The row is keyed on the label id, so the swatch button survives a
// recolour — its Custom input is therefore a *draft* of `label.color` and must
// resync, or a bare focus-and-leave commits the colour the row had at mount and
// silently reverts the user's pick.
test('recolouring a label sticks, and its Custom input follows the new colour', async ({ page, request }) => {
  const name = `fx-${Date.now()}`
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Labels', exact: true }).click()
  await page.getByLabel('New label name').fill(name)
  await page.getByRole('button', { name: 'Add label' }).click()
  await expect(page.getByText('Label created')).toBeVisible()

  // Every locator below is scoped to this label's row: the create form above
  // carries its own palette and a "Custom colour" input whose accessible name is
  // a *substring* of the row's "Custom colour for {name}".
  const row = page.getByRole('listitem').filter({ hasText: name })
  const trigger = row.getByRole('button', { name: `Change ${name} colour` })

  await trigger.click()
  await row.getByRole('button', { name: 'Colour #22c55e' }).click()
  await expect(trigger).toHaveCSS('background-color', 'rgb(34, 197, 94)')

  // Reopen. This is the assertion that fails loudly on the unfixed build — the
  // draft still held the colour the row was created with — and it is timing-free.
  await trigger.click()
  const custom = row.getByLabel(`Custom colour for ${name}`)
  await expect(custom).toHaveValue('#22c55e')

  // Never `click()` a colour input: that opens the browser's own picker, which
  // Playwright cannot drive. Focus and leave it instead — the gesture that
  // reverted the label, since blur commits on any difference from `label.color`.
  await custom.focus()
  await custom.blur()
  await expect(trigger).toHaveCSS('background-color', 'rgb(34, 197, 94)')
  // That assertion is a negative and would pass on the frame before a revert
  // PATCH landed, so ask the server what the colour actually is.
  await page.waitForTimeout(400)
  const after = await (await request.get('/api/v1/labels/')).json()
  expect(after.find((l: { name: string }) => l.name === name).color).toBe('#22c55e')

  // Commits on blur, never on change: the OS picker fires `onChange` per frame
  // while dragged, and each one here would be its own PATCH. The drag storm
  // cannot be synthesised, but `fill()` is one `onChange` and locks the rule.
  let patches = 0
  page.on('request', (r) => {
    if (r.method() === 'PATCH' && r.url().includes('/api/v1/labels/')) patches += 1
  })
  await custom.fill('#eab308')
  expect(patches).toBe(0)
  await custom.blur()
  await expect(trigger).toHaveCSS('background-color', 'rgb(234, 179, 8)')
  expect(patches).toBe(1)
})
