import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Renaming and re-nesting a gallery subfolder, both through the row's right-click
// menu. The drag gesture that also re-nests is deliberately **not** covered here: an
// 8 px PointerSensor activationConstraint needs a multi-step mouse.move dance, there
// is no existing drag spec to copy the idiom from, and a flaky one would cost more
// than it catches. It is verified by hand instead — see docs/dev/gallery-dnd.md.

test('rename a subfolder from its context menu', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `sf-rename-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')

  await page.goto(`/datasets/${ds.id}/gallery`)
  const row = page.getByTitle('alpha', { exact: true })
  await expect(row).toBeVisible()

  await row.click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Rename…' }).click()
  // The input seeds from the folder's own name and selects it, so typing replaces.
  await page.locator('.subfolder-row input').fill('beta')
  await page.locator('.subfolder-row input').press('Enter')

  await expect(page.getByTitle('beta', { exact: true })).toBeVisible()
  await expect(page.getByTitle('alpha', { exact: true })).toHaveCount(0)

  // The label change reached the images, and nothing on disk was renamed.
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(list).toHaveLength(1)
  expect(list[0].subfolder).toBe('beta')
  expect(list[0].filename).toBe('a.png')
})

test('move a subfolder under another from its context menu', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `sf-move-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png', 'alpha')
  await uploadViaApi(request, ds.id, 'b.png', 'beta')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTitle('alpha', { exact: true })).toBeVisible()

  await page.getByTitle('alpha', { exact: true }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Move to…' }).click()

  const dialog = page.getByRole('dialog', { name: 'Move subfolder alpha' })
  await dialog.getByRole('button', { name: 'beta' }).click()
  await expect(dialog.getByText('beta/alpha')).toBeVisible()
  await dialog.getByRole('button', { name: 'Move', exact: true }).click()

  // The destination's ancestors are added to `expandedPaths`, so the moved folder is
  // visible where it landed rather than hidden inside a collapsed parent.
  await expect(page.getByTitle('beta/alpha', { exact: true })).toBeVisible()

  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(list.find((i: { filename: string }) => i.filename === 'a.png').subfolder).toBe('beta/alpha')
})
