import { test, expect } from '@playwright/test'
import type { Browser, Page } from '@playwright/test'
import { createDatasetViaApi, resetPresetsViaApi, uploadViaApi } from './helpers'

// What a browser held before this data moved to the server has to arrive there, and
// the point of moving it is that a browser with nothing in its own storage then sees
// it. So every journey here checks from a SECOND context with empty storage, because a
// passing check in the seeding context could be reading its own localStorage.

const LEGACY_PRESETS_KEY = 'caption-prompt-presets'
const MARKER_KEY = 'crucible-legacy-import-v1'

function legacyPresets(...names: string[]): string {
  return JSON.stringify({
    state: {
      presets: names.map((name, i) => ({ id: `old-${i}`, name, model: 'florence2_large', style: 'detailed', prompt: `${name} text` })),
    },
    version: 0,
  })
}

async function seedStorage(page: Page, key: string, value: string) {
  await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [key, value])
}

async function freshCaptioningPage(browser: Browser, datasetId: string): Promise<Page> {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto(`/datasets/${datasetId}/captioning`)
  return page
}

test.describe('copying saved presets out of the browser', () => {
  test.beforeEach(async ({ request }) => {
    await resetPresetsViaApi(request)
  })

  test.afterEach(async ({ request }) => {
    await resetPresetsViaApi(request)
  })

  test('a preset held by one browser appears in another with empty storage', async ({ page, browser, request }) => {
    const ds = await createDatasetViaApi(request, `legacy-presets-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')
    await seedStorage(page, LEGACY_PRESETS_KEY, legacyPresets('from the old browser'))

    await page.goto(`/datasets/${ds.id}/captioning`)
    await expect(page.getByRole('button', { name: /from the old browser/ })).toBeVisible()

    const other = await freshCaptioningPage(browser, ds.id)
    await expect(other.getByRole('button', { name: /from the old browser/ })).toBeVisible()
    await other.context().close()
  })

  test('reloading does not import twice, and the old key is kept for rollback', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `legacy-once-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')
    await seedStorage(page, LEGACY_PRESETS_KEY, legacyPresets('only once'))

    await page.goto(`/datasets/${ds.id}/captioning`)
    const preset = page.getByRole('button', { name: /only once/ })
    await expect(preset).toBeVisible()

    // A marker, and not the dedupe, has to be what stops this: delete the preset and
    // reload, and a browser that re-imported would bring it straight back.
    await page.getByTitle('Delete preset').click()
    await expect(preset).toHaveCount(0)
    await page.reload()
    await expect(page.getByText('No presets saved yet.')).toBeVisible()

    const stored = await page.evaluate((k) => localStorage.getItem(k), LEGACY_PRESETS_KEY)
    expect(stored).toContain('only once')
    const marker = await page.evaluate((k) => localStorage.getItem(k), MARKER_KEY)
    expect(JSON.parse(marker!).done).toContain('presets')
  })

  test('a failed import leaves the section unmarked and the next load retries', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `legacy-retry-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')
    await seedStorage(page, LEGACY_PRESETS_KEY, legacyPresets('after a retry'))

    await page.route('**/api/v1/app-data/legacy-import', (route) => route.fulfill({ status: 500, body: '{}' }))
    await page.goto(`/datasets/${ds.id}/captioning`)
    await expect(page.getByText(/Couldn't copy your saved data/)).toBeVisible()
    expect(await page.evaluate((k) => localStorage.getItem(k), MARKER_KEY)).toBeNull()

    await page.unroute('**/api/v1/app-data/legacy-import')
    await page.reload()
    await expect(page.getByRole('button', { name: /after a retry/ })).toBeVisible()
  })

  test('a preset the server refuses is named, and the rest still arrive', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `legacy-reject-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')
    await seedStorage(page, LEGACY_PRESETS_KEY, legacyPresets('a good one', 'x'.repeat(300)))

    await page.goto(`/datasets/${ds.id}/captioning`)
    await expect(page.getByRole('button', { name: /a good one/ })).toBeVisible()
    await expect(page.getByText(/could not be copied to the app.*xxxx…/)).toBeVisible()

    await expect.poll(() => page.evaluate((k) => localStorage.getItem(k), MARKER_KEY)).toContain('rejected')
    const marker = JSON.parse((await page.evaluate((k) => localStorage.getItem(k), MARKER_KEY))!)
    expect(marker.rejected).toHaveLength(1)
  })

  test('a list that fails to load does not read as having no presets', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `legacy-list-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')

    // The SPA's 200 HTML, as a server older than the frontend answers. It must not crash.
    await page.route('**/api/v1/app-data/presets', (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html>' }))
    await page.goto(`/datasets/${ds.id}/captioning`)

    await expect(page.getByText("Couldn't load presets.")).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('No presets saved yet.')).toHaveCount(0)
  })

  test('two browsers holding the same preset end up with one copy', async ({ page, browser, request }) => {
    const ds = await createDatasetViaApi(request, `legacy-merge-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')
    await seedStorage(page, LEGACY_PRESETS_KEY, legacyPresets('shared', 'only in first'))
    await page.goto(`/datasets/${ds.id}/captioning`)
    await expect(page.getByRole('button', { name: /only in first/ })).toBeVisible()

    const context = await browser.newContext()
    const second = await context.newPage()
    await seedStorage(second, LEGACY_PRESETS_KEY, legacyPresets('shared', 'only in second'))
    await second.goto(`/datasets/${ds.id}/captioning`)

    await expect(second.getByRole('button', { name: /only in second/ })).toBeVisible()
    await expect(second.getByRole('button', { name: /only in first/ })).toBeVisible()
    await expect(second.getByRole('button', { name: /shared/ })).toHaveCount(1)
    await context.close()
  })
})
