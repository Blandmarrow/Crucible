import { test, expect } from '@playwright/test'
import { createDatasetViaApi, resetCategoriesViaApi } from './helpers'

test.describe('empty categories', () => {
  // The server outlives a browser context, and one left behind changes this page's
  // layout for every later spec.
  test.beforeEach(async ({ request }) => {
    await resetCategoriesViaApi(request)
  })

  test.afterEach(async ({ request }) => {
    await resetCategoriesViaApi(request)
  })

  const declared = async (request: import('@playwright/test').APIRequestContext) =>
    (await request.get('/api/v1/app-data/categories')).json()

  test('one made in a browser appears in another with empty storage', async ({ page, browser, request }) => {
    await createDatasetViaApi(request, `e2e-cat-share-${Date.now()}`)
    await page.goto('/datasets')
    await page.getByRole('button', { name: 'New category' }).first().click()
    const dialog = page.getByRole('dialog', { name: 'New Category' })
    await dialog.getByLabel('Category name').fill('planned/later')
    await dialog.getByRole('button', { name: 'Create', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.getByText('planned/later').first()).toBeVisible()
    expect(await declared(request)).toEqual(['planned/later'])

    const context = await browser.newContext()
    const other = await context.newPage()
    await other.goto('/datasets')
    await expect(other.getByText('planned/later').first()).toBeVisible()
    await context.close()
  })

  test('renaming and removing one stick', async ({ page, request }) => {
    await createDatasetViaApi(request, `e2e-cat-edit-${Date.now()}`)
    await request.post('/api/v1/app-data/categories', { data: { name: 'before' } })

    await page.goto('/datasets')
    await expect(page.getByText('before').first()).toBeVisible()
    await page.getByTitle('Rename category').click({ force: true })
    await page.locator(':focus').fill('after')
    await page.getByTitle('Save', { exact: true }).click({ force: true })
    await expect.poll(() => declared(request)).toEqual(['after'])

    await page.reload()
    await expect(page.getByText('after').first()).toBeVisible()
    await page.getByTitle('Delete category').click({ force: true })
    await page.getByRole('button', { name: 'Remove', exact: true }).click()
    await expect.poll(() => declared(request)).toEqual([])

    await page.reload()
    await expect(page.getByRole('button', { name: 'New Dataset' })).toBeVisible()
    await expect(page.getByTitle('Rename category')).toHaveCount(0)
  })

  test('those held by two browsers are merged, and a removed one stays removed', async ({ page, browser, request }) => {
    await createDatasetViaApi(request, `e2e-cat-legacy-${Date.now()}`)
    const KEY = 'crucible-declared-categories'
    await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [KEY, JSON.stringify(['shared', 'first only'])])
    await page.goto('/datasets')
    await expect.poll(() => declared(request)).toEqual(['first only', 'shared'])

    const context = await browser.newContext()
    const second = await context.newPage()
    await second.addInitScript(([k, v]) => localStorage.setItem(k, v), [KEY, JSON.stringify(['shared', 'second only'])])
    await second.goto('/datasets')
    await expect.poll(() => declared(request)).toEqual(['first only', 'second only', 'shared'])
    await context.close()

    // The marker stops a re-import, so reloading can't bring this back.
    await request.delete('/api/v1/app-data/categories', { params: { name: 'first only' } })
    await page.reload()
    await expect(page.getByText('shared').first()).toBeVisible()
    expect(await declared(request)).toEqual(['second only', 'shared'])
  })

  test('an empty list held by a browser uploads nothing', async ({ page, request }) => {
    const importRequests: string[] = []
    page.on('request', (r) => { if (r.url().includes('/legacy-import')) importRequests.push(r.url()) })
    await page.addInitScript(([k, v]) => localStorage.setItem(k, v), ['crucible-declared-categories', '[]'])
    await page.goto('/datasets')
    // The marker is written only once the import has run, so this can't check too early.
    await expect.poll(() => page.evaluate(() => localStorage.getItem('crucible-legacy-import-v1'))).toContain('categories')
    expect(importRequests).toEqual([])
    expect(await declared(request)).toEqual([])
  })

  test('a failed load says so rather than looking like none', async ({ page, request }) => {
    await createDatasetViaApi(request, `e2e-cat-fail-${Date.now()}`)
    await page.route('**/api/v1/app-data/categories', (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html>' }))
    await page.goto('/datasets')
    await expect(page.getByText(/Couldn't load your empty categories/)).toBeVisible({ timeout: 15_000 })
  })

  test('renaming a category that has datasets moves them and declares nothing', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-cat-carry-${Date.now()}`)
    const patch = await request.patch(`/api/v1/datasets/${ds.id}`, { data: { category: 'with data' } })
    expect(patch.status(), await patch.text()).toBe(200)

    await page.goto('/datasets')
    await expect(page.getByText('with data').first()).toBeVisible()
    await page.getByTitle('Rename category').click({ force: true })
    await page.locator(':focus').fill('renamed')
    await page.getByTitle('Save', { exact: true }).click({ force: true })

    await expect(page.getByTitle('Save', { exact: true })).toHaveCount(0)
    await expect.poll(async () => (await (await request.get(`/api/v1/datasets/${ds.id}`)).json()).category).toBe('renamed')
    expect(await declared(request)).toEqual([])
    await request.patch(`/api/v1/datasets/${ds.id}`, { data: { category: '' } })
  })
})

// Create a dataset entirely through the UI, then delete it through the UI.
// Uses a unique name so the single shared backend stays independent across specs.
test('create and delete a dataset via the UI', async ({ page }) => {
  const name = `e2e-datasets-${Date.now()}`

  await page.goto('/datasets')
  await page.getByRole('button', { name: 'New Dataset' }).click()

  // The create modal is a named dialog, and its Name label is bound to the input.
  const create = page.getByRole('dialog', { name: 'New Dataset' })
  await create.getByLabel('Name', { exact: true }).fill(name)
  await create.getByRole('button', { name: 'Create', exact: true }).click()

  // The new dataset appears as a card whose name is a heading.
  const card = page.getByRole('heading', { name })
  await expect(card).toBeVisible()

  // Delete: hover reveals the card's action buttons, then confirm. The trash
  // icon's title="Delete" collides with the confirm button's name, so scope the
  // confirm click to the dialog.
  await card.hover()
  await page.getByTitle('Delete').first().click()
  const dialog = page.getByRole('dialog', { name: 'Delete dataset' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click()

  await expect(page.getByRole('heading', { name })).toHaveCount(0)
})
