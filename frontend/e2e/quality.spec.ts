import { test, expect } from '@playwright/test'
import { createDatasetViaApi, pngBuffer, uploadViaApi } from './helpers'

// The Quality page's GPU-free surface: its panels, the subfolder scope select,
// and the duplicates query. "Run scoring" is never clicked — the scorers import
// cv2/torch, which CI does not have, so a click here would fail the job body
// rather than the page.
test('quality page renders its panels and scopes to a subfolder', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-quality-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'root.png')
  // A second image in a subfolder: the scope select only renders once a
  // non-root subfolder exists.
  const r = await request.post('/api/v1/images/upload', {
    params: { dataset_id: ds.id, subfolder: 'sub' },
    multipart: { files: { name: 'nested.png', mimeType: 'image/png', buffer: pngBuffer() } },
  })
  expect(r.status(), await r.text()).toBe(201)

  // The duplicates query is fired on mount and has no visible surface when the
  // dataset is clean — assert the request itself resolved.
  const duplicates = page.waitForResponse(
    (res) => res.url().includes('/api/v1/quality/duplicates/') && res.request().method() === 'GET',
  )

  await page.goto(`/datasets/${ds.id}/quality`)
  await expect(page.getByRole('heading', { name: 'Score images' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Run quality analysis' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Style similarity' })).toBeVisible()

  expect((await duplicates).status()).toBe(200)
  // No duplicates were flagged, so the group panel stays absent.
  await expect(page.getByRole('heading', { name: 'Duplicate groups' })).toHaveCount(0)

  // Default scoring selection: aesthetic + technical on, the rest off.
  const scorer = (label: string) =>
    page.locator('label').filter({ hasText: label }).getByRole('checkbox')
  await expect(scorer('Aesthetic score · LAION')).toBeChecked()
  await expect(scorer('Technical · OpenCV')).toBeChecked()
  await expect(scorer('NSFW detection · Marqo')).not.toBeChecked()

  // The per-layer DINOv2 option is conditional on DINOv2 itself being selected.
  await expect(page.getByText('DINOv2 per-layer embeds')).toHaveCount(0)
  await scorer('DINOv2 embeddings').check()
  await expect(page.getByText('DINOv2 per-layer embeds')).toBeVisible()

  // Subfolder scope.
  const scope = page.locator('select').first()
  await expect(scope).toContainText('sub (1)')
  await scope.selectOption('sub')
  await expect(scope).toHaveValue('sub')
})
