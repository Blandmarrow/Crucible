import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import * as os from 'node:os'
import * as path from 'node:path'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Run a "plain folder" export from the UI, watch the progress panel complete,
// then cross-check the produced files through the API.
test('run a plain export to completion', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-export-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  await uploadViaApi(request, ds.id, 'y.png')

  // The e2e backend serves a throwaway data dir; write the export under the temp dir.
  const outDir = path.join(os.tmpdir(), `e2e-export-${Date.now()}`)

  await page.goto(`/datasets/${ds.id}/export`)
  await page.getByRole('button', { name: 'plain folder' }).click()
  await page.getByPlaceholder('C:\\training', { exact: false }).fill(outDir)
  await page.getByRole('button', { name: 'Build export' }).click()

  await expect(page.getByText('Export complete', { exact: false })).toBeVisible({ timeout: 30_000 })

  // Cross-check via the preview endpoint: both images were exportable.
  const preview = await (await request.get(`/api/v1/export/preview/${ds.id}`)).json()
  expect(preview.will_export).toBe(2)
})

// Regression: the Export page used to send `label_missing: false` — a plain
// boolean, not omitted — into the export POST body, where `false` means "only
// images that DO carry a label" rather than "no label filter". The preview
// stripped falsy values, so it promised the full count while the export wrote
// zero files for every unlabelled image. Assert on the files on disk, not on the
// preview: the preview is the half that was already right.
test('an export with no label filter writes every unlabelled image', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-export-nolabels-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')

  const outDir = path.join(os.tmpdir(), `e2e-export-nolabels-${Date.now()}`)

  await page.goto(`/datasets/${ds.id}/export`)
  await page.getByRole('button', { name: 'plain folder' }).click()
  await page.getByPlaceholder('C:\\training', { exact: false }).fill(outDir)
  // "Has caption" is on by default and these uploads have none, so leaving it
  // checked would write zero files for a reason that has nothing to do with
  // labels — and this test's whole assertion is a file count.
  await page.locator('label').filter({ hasText: 'Has caption' }).getByRole('checkbox').uncheck()
  await page.getByRole('button', { name: 'Build export' }).click()

  await expect(page.getByText('Export complete', { exact: false })).toBeVisible({ timeout: 30_000 })

  const listing = await request.get('/api/v1/filesystem/list', { params: { path: path.join(outDir, 'images') } })
  expect(listing.status(), await listing.text()).toBe(200)
  const files = (await listing.json()).entries.filter((e: { type: string }) => e.type === 'file')
  expect(files).toHaveLength(2)
})

test.describe('limit to subfolders', () => {
  // By default one root image and two under `parent`, which holds nothing directly,
  // so `list_subfolders` returns no row for it: the page has to make that row up.
  const DEFAULT_SEED = [['root.png', ''], ['one.png', 'parent/one'], ['two.png', 'parent/two']]

  async function openWithFolderFilter(page: Page, request: APIRequestContext, seed = DEFAULT_SEED) {
    const ds = await createDatasetViaApi(request, `e2e-export-folders-${Date.now()}`)
    for (const [name, subfolder] of seed) await uploadViaApi(request, ds.id, name, subfolder)

    const outDir = path.join(os.tmpdir(), `e2e-export-folders-${Date.now()}`)
    await page.goto(`/datasets/${ds.id}/export`)
    await page.getByRole('button', { name: 'plain folder' }).click()
    await page.getByPlaceholder('C:\\training', { exact: false }).fill(outDir)
    // These uploads have no captions, and "Has caption" is on by default.
    await page.locator('label').filter({ hasText: 'Has caption' }).getByRole('checkbox').uncheck()
    await page.locator('label').filter({ hasText: 'Limit to subfolders' }).getByRole('checkbox').check()
    return outDir
  }

  const willExport = (page: Page) =>
    page.locator('.stat-card').filter({ hasText: 'Will export' }).locator('.sv')

  test('ticking a parent exports everything under it', async ({ page, request }) => {
    const outDir = await openWithFolderFilter(page, request)

    await page.getByRole('checkbox', { name: 'parent', exact: true }).check()
    await expect(page.getByRole('checkbox', { name: 'parent/one', exact: true })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: 'parent/two', exact: true })).toBeChecked()
    await expect(willExport(page)).toHaveText('2')
    // Nothing is filed in `parent` itself, so it has nothing to tick separately.
    await expect(page.getByRole('checkbox', { name: 'parent (this folder)' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Build export' }).click()
    await expect(page.getByText('Export complete', { exact: false })).toBeVisible({ timeout: 30_000 })

    const listing = await request.get('/api/v1/filesystem/list', { params: { path: path.join(outDir, 'images') } })
    expect(listing.status(), await listing.text()).toBe(200)
    const files = (await listing.json()).entries.filter((e: { type: string }) => e.type === 'file')
    expect(files.map((f: { name: string }) => f.name).sort()).toEqual(['one.png', 'two.png'])
  })

  test('unticking one child leaves the parent part-ticked', async ({ page, request }) => {
    await openWithFolderFilter(page, request)
    const parent = page.getByRole('checkbox', { name: 'parent', exact: true })

    await parent.check()
    await page.getByRole('checkbox', { name: 'parent/two', exact: true }).uncheck()

    await expect(parent).not.toBeChecked()
    expect(await parent.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(true)
    await expect(willExport(page)).toHaveText('1')

    // A click on a part-ticked row completes it rather than clearing it.
    await parent.click()
    await expect(parent).toBeChecked()
    expect(await parent.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(false)
    await expect(willExport(page)).toHaveText('2')
  })

  // Regression: a parent's one row stood for both its own images and its branch, so
  // two selections exporting different files looked identical on screen.
  test('a parent holding images has its own row for them', async ({ page, request }) => {
    await openWithFolderFilter(page, request, [
      ['own.png', 'parent'], ['one.png', 'parent/one'], ['two.png', 'parent/two'],
    ])
    const parent = page.getByRole('checkbox', { name: 'parent', exact: true })
    const own = page.getByRole('checkbox', { name: 'parent (this folder)' })

    await parent.check()
    await expect(own).toBeChecked()
    await expect(willExport(page)).toHaveText('3')

    // Leave its own images out and keep the whole branch below it.
    await own.uncheck()
    await expect(page.getByRole('checkbox', { name: 'parent/one', exact: true })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: 'parent/two', exact: true })).toBeChecked()
    expect(await parent.evaluate((el: HTMLInputElement) => el.indeterminate)).toBe(true)
    await expect(willExport(page)).toHaveText('2')

    // The other way round: only its own images.
    await parent.click()
    await parent.uncheck()
    await own.check()
    await expect(willExport(page)).toHaveText('1')
  })

  test('every missing parent gets a row, however deep', async ({ page, request }) => {
    await openWithFolderFilter(page, request, [['deep.png', 'top/middle/bottom']])

    await page.getByRole('checkbox', { name: 'top', exact: true }).check()
    await expect(page.getByRole('checkbox', { name: 'top/middle', exact: true })).toBeChecked()
    await expect(page.getByRole('checkbox', { name: 'top/middle/bottom', exact: true })).toBeChecked()
    await expect(willExport(page)).toHaveText('1')
  })

  // A saved folder since renamed or deleted has no row. It must not narrow the
  // export with no checkbox showing why.
  test('a saved selection naming a missing folder is ignored', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-export-stale-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'one.png', 'parent/one')
    await uploadViaApi(request, ds.id, 'two.png', 'parent/two')
    const openWithSaved = async (selectedSubfolders: string[]) => {
      await page.goto('/')
      await page.evaluate(([key, blob]) => localStorage.setItem(key, blob), [
        `export-filters-${ds.id}`,
        JSON.stringify({ filterCaptioned: false, subfolderFilterActive: true, selectedSubfolders }),
      ])
      await page.goto(`/datasets/${ds.id}/export`)
      await page.getByPlaceholder('C:\\training', { exact: false }).fill(path.join(os.tmpdir(), 'e2e-unused'))
    }

    await openWithSaved(['gone', 'parent/one'])
    await expect(page.getByRole('checkbox', { name: 'parent/one', exact: true })).toBeChecked()
    await expect(willExport(page)).toHaveText('1')
    await expect(page.getByRole('button', { name: 'Build export' })).toBeEnabled()

    await openWithSaved(['gone'])
    await expect(page.getByText('Tick at least one folder', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Build export' })).toBeDisabled()
    await expect(willExport(page)).toHaveText('0')
  })

  // Regression: with nothing ticked the preview promised the whole dataset while
  // the export wrote nothing, and the button stayed live.
  test('nothing ticked blocks the run and the preview says zero', async ({ page, request }) => {
    await openWithFolderFilter(page, request)

    await expect(page.getByRole('button', { name: 'Build export' })).toBeDisabled()
    await expect(page.getByText('Tick at least one folder', { exact: false })).toBeVisible()
    await expect(willExport(page)).toHaveText('0')

    await page.getByRole('checkbox', { name: 'parent/one', exact: true }).check()
    await expect(page.getByRole('button', { name: 'Build export' })).toBeEnabled()
    await expect(page.getByText('Tick at least one folder', { exact: false })).toHaveCount(0)
  })

  // Regression: the root folder's path is "", which the old comma-joined preview
  // param dropped, so this counted the whole dataset. A parent never ticks it.
  test('the root folder is counted alone', async ({ page, request }) => {
    await openWithFolderFilter(page, request)
    const root = page.getByRole('checkbox', { name: /^\(root\)/ })

    await root.check()
    await expect(willExport(page)).toHaveText('1')

    await page.getByRole('checkbox', { name: 'parent', exact: true }).check()
    await expect(root).toBeChecked()
    await expect(willExport(page)).toHaveText('3')

    await page.getByRole('checkbox', { name: 'parent', exact: true }).uncheck()
    await expect(root).toBeChecked()
    await expect(willExport(page)).toHaveText('1')
  })
})

// "Exclude rated: Cut" drops only the images someone marked Cut. An unrated image
// is never named by that filter, so it must still be written. Asserted on the
// files on disk, as above: the preview is the half that is easy to get right.
test('an export excluding Cut writes the unrated and drops the cut', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-export-rating-${Date.now()}`)
  for (const n of ['keep.png', 'cut.png', 'unrated.png']) await uploadViaApi(request, ds.id, n)
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const idOf = (filename: string) => list.find((i: { filename: string }) => i.filename === filename).id
  for (const [filename, rating] of [['keep.png', 4], ['cut.png', 1]] as const) {
    const r = await request.post('/api/v1/images/bulk-rating', {
      data: { dataset_id: ds.id, image_ids: [idOf(filename)], rating },
    })
    expect(r.status(), await r.text()).toBe(200)
  }

  const outDir = path.join(os.tmpdir(), `e2e-export-rating-${Date.now()}`)
  await page.goto(`/datasets/${ds.id}/export`)
  await page.getByRole('button', { name: 'plain folder' }).click()
  await page.getByPlaceholder('C:\\training', { exact: false }).fill(outDir)
  await page.locator('label').filter({ hasText: 'Has caption' }).getByRole('checkbox').uncheck()
  // Scoped by the row's own span: the "Rating ≥" select in the neighbouring label also lists a Cut option.
  await page.locator('label').filter({ has: page.locator('span', { hasText: /^1 — Cut$/ }) }).getByRole('checkbox').check()
  await expect(
    page.locator('.stat-card').filter({ hasText: 'Will export' }).locator('.sv'),
  ).toHaveText('2')

  await page.getByRole('button', { name: 'Build export' }).click()
  await expect(page.getByText('Export complete', { exact: false })).toBeVisible({ timeout: 30_000 })

  const listing = await request.get('/api/v1/filesystem/list', { params: { path: path.join(outDir, 'images') } })
  expect(listing.status(), await listing.text()).toBe(200)
  const files = (await listing.json()).entries.filter((e: { type: string }) => e.type === 'file')
  expect(files.map((f: { name: string }) => f.name).sort()).toEqual(['keep.png', 'unrated.png'])
})
