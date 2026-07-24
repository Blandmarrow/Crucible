import { test, expect } from '@playwright/test'
import { createDatasetViaApi, pngBuffer } from './helpers'

// Upload an image through the gallery UI and see its tile render. The dataset is
// created via API (that path is covered by datasets.spec) so this focuses on the
// upload → thumbnail journey.
test('upload an image and see its tile', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-upload-${Date.now()}`)

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByText('No images found.', { exact: false })).toBeVisible()

  // The file input is display:none behind an "Upload" label; setInputFiles works on it.
  await page.locator('input[type="file"]').first().setInputFiles({
    name: 'shot.png',
    mimeType: 'image/png',
    buffer: pngBuffer(),
  })

  await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
  await expect(page.getByRole('img', { name: 'shot.png' })).toBeVisible()
})
