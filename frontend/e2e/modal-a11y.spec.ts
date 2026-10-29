import { test, expect, type Locator, type Page } from '@playwright/test'
import * as os from 'node:os'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Keyboard behavior added by hooks/useModalBehavior: Escape closes, focus lands
// inside the panel, and a stacked picker closes only itself.
test('Escape closes a modal and a stacked picker closes only itself', async ({ page, request }) => {
  await createDatasetViaApi(request, `e2e-modal-${Date.now()}`)

  await page.goto('/datasets')
  // Each dataset card carries an "Import folder" icon button too — take the
  // page toolbar's text button.
  await page.getByRole('button', { name: 'Import folder' }).filter({ hasText: 'Import folder' }).click()

  const importDialog = page.getByRole('dialog', { name: 'Import from folder' })
  await expect(importDialog).toBeVisible()
  // The path field declares autoFocus; the hook must respect it, not steal focus.
  await expect(importDialog.getByPlaceholder('/home/user/images', { exact: false })).toBeFocused()

  // The folder picker stacks over it. Escape closes the picker only, and focus
  // goes back to the button that opened it.
  await importDialog.getByPlaceholder('/home/user/images', { exact: false }).fill(os.tmpdir())
  const browse = importDialog.getByRole('button', { name: 'Browse…' }).first()
  await browse.click()
  const picker = page.getByRole('dialog', { name: 'Select a folder to import' })
  await expect(picker).toBeVisible()

  // The picker's inline "New folder" row owns its own Escape, and unmounting its
  // input drops focus to <body> — the next Escape must still close the picker.
  await picker.getByRole('button', { name: 'New folder' }).click()
  await expect(picker.getByPlaceholder('Folder name')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(picker.getByPlaceholder('Folder name')).toHaveCount(0)
  await expect(picker).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(picker).toHaveCount(0)
  await expect(importDialog).toBeVisible()
  await expect(browse).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(importDialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Import folder' }).filter({ hasText: 'Import folder' })).toBeFocused()
})

// A destructive confirm cancels on Escape and never on a backdrop click.
test('Escape cancels a destructive confirm; the backdrop does not', async ({ page, request }) => {
  const name = `e2e-modal-confirm-${Date.now()}`
  await createDatasetViaApi(request, name)

  await page.goto('/datasets')
  const card = page.getByRole('heading', { name })
  await card.hover()
  await page.getByTitle('Delete').first().click()

  const confirm = page.getByRole('dialog', { name: 'Delete dataset' })
  await expect(confirm).toBeVisible()

  // Backdrop click: destructive confirms deliberately ignore it.
  await page.mouse.click(5, 5)
  await expect(confirm).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(confirm).toHaveCount(0)
  await expect(page.getByRole('heading', { name })).toBeVisible()
})

// The bulk labels modal joins the shared guard: it uses `useModalBehavior` like
// every other one, so Escape closes it and focus starts inside the panel.
test('Escape closes the bulk labels modal', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-modal-labels-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('select-all-btn').click()
  await page.getByRole('button', { name: 'Labels' }).click()

  const dialog = page.getByRole('dialog', { name: 'Edit labels' })
  await expect(dialog).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

/** Press `key` `presses` times and assert focus never leaves `dialog`. */
async function expectTabTrapped(page: Page, dialog: Locator, presses = 30, key = 'Tab') {
  for (let i = 0; i < presses; i++) {
    await page.keyboard.press(key)
    const inside = await dialog.evaluate((el) => el.contains(document.activeElement))
    expect(inside, `focus left the dialog after ${i + 1} × ${key}`).toBe(true)
  }
}

// The five DatasetsPage dialogs were bare `.dialog-bg` overlays with no hook: no
// dialog role, Escape did nothing, Tab walked out into the page behind, and
// Cancel dropped focus on <body> (GPT-029). They now render through ModalShell.
test('New Dataset is a named dialog that traps focus, closes on Escape and restores focus', async ({ page, request }) => {
  await createDatasetViaApi(request, `e2e-modal-new-${Date.now()}`)
  await page.goto('/datasets')
  const opener = page.getByRole('button', { name: 'New dataset', exact: true })
  await opener.click()

  const dialog = page.getByRole('dialog', { name: 'New Dataset' })
  await expect(dialog).toBeVisible()
  // Labels are bound to their inputs, so the fields are reachable by name.
  await expect(dialog.getByLabel('Name', { exact: true })).toBeFocused()
  await expect(dialog.getByLabel('Description')).toBeVisible()
  await expect(dialog.getByLabel('Category')).toBeVisible()

  await expectTabTrapped(page, dialog)
  await expectTabTrapped(page, dialog, 10, 'Shift+Tab')

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()

  // Cancel restores focus too, and Escape and Cancel both discard the draft.
  await opener.click()
  await dialog.getByLabel('Name', { exact: true }).fill('draft')
  await page.keyboard.press('Escape')
  await opener.click()
  await expect(dialog.getByLabel('Name', { exact: true })).toHaveValue('')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()
})

// The one DatasetsPage dialog that always closed on a backdrop click keeps doing so.
test('New Category closes on Escape and on a backdrop click', async ({ page, request }) => {
  // The category rail (which holds the opener) needs two sections to render.
  const stamp = Date.now()
  for (const category of [`e2e-cat-a-${stamp}`, `e2e-cat-b-${stamp}`]) {
    const r = await request.post('/api/v1/datasets/', { data: { name: `${category}-ds`, category } })
    expect(r.status(), await r.text()).toBe(201)
  }
  await page.goto('/datasets')
  const opener = page.getByTitle('New category')
  await opener.click()
  const dialog = page.getByRole('dialog', { name: 'New Category' })
  await expect(dialog.getByLabel('Category name')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()

  await opener.click()
  await expect(dialog).toBeVisible()
  await page.mouse.click(5, 5)
  await expect(dialog).toHaveCount(0)
})

// The ten SelectionToolbar overlays were bare too. Edit Captions stands for them.
test('Edit Captions is a named dialog that traps focus, closes on Escape and restores focus', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-modal-edit-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('1 selected')).toBeVisible()

  const opener = page.getByRole('button', { name: 'Edit', exact: true })
  await opener.click()
  const dialog = page.getByRole('dialog', { name: 'Edit Captions — 1 Image' })
  await expect(dialog).toBeVisible()

  await expectTabTrapped(page, dialog)

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()
  // Dismissing the dialog is not a reason to drop the selection it acted on.
  await expect(page.getByText('1 selected')).toBeVisible()

  await opener.click()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()
})
