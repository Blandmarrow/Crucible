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
  await expect(scorer('Aesthetic score')).toBeChecked()
  await expect(scorer('Technical · OpenCV')).toBeChecked()
  await expect(scorer('NSFW detection · Marqo')).not.toBeChecked()

  // The aesthetic model picker: a per-run choice with a sticky default. It is a
  // sub-row rather than a control inside the checkbox's `<label>`, which is what
  // keeps `scorer('Aesthetic score')` above unambiguous.
  const model = page.getByLabel('Aesthetic model')
  await expect(model).toHaveValue('laion')
  await model.selectOption('v2_5')

  // The per-layer DINOv2 option is conditional on DINOv2 itself being selected.
  await expect(page.getByText('DINOv2 per-layer embeds')).toHaveCount(0)
  await scorer('DINOv2 embeddings').check()
  await expect(page.getByText('DINOv2 per-layer embeds')).toBeVisible()

  // Subfolder scope. Matched by its content, not by DOM order: `.first()`
  // survived only by coincidence, and the page now renders a second select.
  const scope = page.locator('select').filter({ hasText: 'All subfolders' })
  await expect(scope).toContainText('sub (1)')
  await scope.selectOption('sub')
  await expect(scope).toHaveValue('sub')

  // The picker is sticky: it rides the same global QUALITY_WORKFLOW blob as the
  // scoring toggles, so a reload keeps it…
  await page.reload()
  await expect(page.getByLabel('Aesthetic model')).toHaveValue('v2_5')

  // …and *Reset to defaults* puts it back to LAION.
  await page.getByRole('button', { name: 'Reset to defaults' }).click()
  await expect(page.getByLabel('Aesthetic model')).toHaveValue('laion')
})

// The style-similarity panel's prerequisite: it reads embeddings a *scoring* run
// writes, and every mode answers 400 when the column it reads is empty. This
// dataset has none, which makes the whole check deterministic and GPU-free — and
// it is the cheapest end-to-end proof of GET /quality/embedding-coverage.
test('style similarity defaults to layer 9 and refuses to run without embeddings', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-style-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  const coverage = page.waitForResponse(
    (res) => res.url().includes('/api/v1/quality/embedding-coverage/') && res.request().method() === 'GET',
  )

  await page.goto(`/datasets/${ds.id}/quality`)
  // Collapsed by default, and the query is `enabled`-gated on it being open.
  await page.getByRole('button', { name: 'Expand style similarity' }).click()
  expect((await coverage).status()).toBe(200)

  // The layer select only renders for dino/combined, so pick a mode first —
  // which also exercises the mode -> layer effect.
  await page.getByRole('button', { name: 'CLIP + DINOv2', exact: true }).click()

  // Layer 9, not "Layer 12"/final. The final embedding is a separate option
  // because it is a different vector, not a relabelling of layer 12.
  const layer = page.locator('select').filter({ hasText: 'Final embedding' })
  await expect(layer).toHaveValue('9')

  // Nothing in this dataset has CLIP or per-layer DINOv2 embeddings, so the run
  // would be a guaranteed 400 — the page says so instead of letting it fail.
  await expect(page.getByTestId('style-embedding-warning')).toBeVisible()
  const score = page.getByRole('button', { name: /Score similarity/ })
  await expect(score).toBeDisabled()
  await expect(score).toHaveAttribute('title', /would fail/)
})
