import { test, expect } from '@playwright/test'
import { createDatasetViaApi } from './helpers'

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
  await importDialog.getByPlaceholder('/home/user/images', { exact: false }).fill('/tmp')
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
