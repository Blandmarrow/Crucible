import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Edit a caption in the image detail view, save, reload, and confirm it persisted.
test('edit a caption, save, and it survives a reload', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-caption-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'cap.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  // A plain click on a tile navigates to the image detail page.
  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(/\/image\//)

  const box = page.getByPlaceholder('Natural language description', { exact: false })
  await box.fill('a small red square')
  // Save is disabled until the caption is dirty (the fill above makes it dirty).
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByPlaceholder('Natural language description', { exact: false }))
    .toHaveValue('a small red square')
})
