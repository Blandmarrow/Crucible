import { test, expect } from '@playwright/test'

// Create a dataset entirely through the UI, then delete it through the UI.
// Uses a unique name so the single shared backend stays independent across specs.
test('create and delete a dataset via the UI', async ({ page }) => {
  const name = `e2e-datasets-${Date.now()}`

  await page.goto('/datasets')
  await page.getByRole('button', { name: 'New Dataset' }).click()

  // The create modal: the Name input has no label association — anchor by placeholder.
  await page.getByPlaceholder('my_dataset').fill(name)
  await page.getByRole('button', { name: 'Create', exact: true }).click()

  // The new dataset appears as a card whose name is a heading.
  const card = page.getByRole('heading', { name })
  await expect(card).toBeVisible()

  // Delete: hover reveals the card's action buttons, then confirm. The trash
  // icon's title="Delete" collides with the confirm button's name, so scope the
  // confirm click to the modal overlay.
  await card.hover()
  await page.getByTitle('Delete').first().click()
  const dialog = page.locator('div.fixed.inset-0.z-50')
  await expect(dialog.getByText('Delete dataset')).toBeVisible()
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click()

  await expect(page.getByRole('heading', { name })).toHaveCount(0)
})
