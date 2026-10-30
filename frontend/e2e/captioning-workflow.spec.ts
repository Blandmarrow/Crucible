import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { PERSIST_DEBOUNCE_MS } from '../src/constants/storage'
import { createDatasetViaApi, resetCaptioningWorkflowViaApi, uploadViaApi } from './helpers'

// The Captioning page's remembered setup lives on the server. The server outlives a
// browser context, so every test starts from and leaves behind "never saved".

const PROMPT_FIELD = 'Leave blank to use the style preset prompt…'
const LEGACY_KEY = 'captioning-workflow-config'

const savedWorkflow = async (request: APIRequestContext) =>
  (await (await request.get('/api/v1/app-data/captioning-workflow')).json()).value

async function captioningDataset(request: APIRequestContext, name: string) {
  const ds = await createDatasetViaApi(request, `${name}-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  return ds.id as string
}

async function seedStorage(page: Page, key: string, value: string) {
  await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [key, value])
}

test.describe('the remembered captioning setup', () => {
  test.beforeEach(async ({ request }) => {
    await resetCaptioningWorkflowViaApi(request)
  })

  test.afterEach(async ({ request }) => {
    await resetCaptioningWorkflowViaApi(request)
  })

  test('a prompt typed in one browser appears in another with empty storage', async ({ page, browser, request }) => {
    const datasetId = await captioningDataset(request, 'wf-share')
    await page.goto(`/datasets/${datasetId}/captioning`)
    await page.getByPlaceholder(PROMPT_FIELD).fill('describe the lighting')
    await expect.poll(async () => (await savedWorkflow(request))?.customPrompt).toBe('describe the lighting')

    const context = await browser.newContext()
    const other = await context.newPage()
    await other.goto(`/datasets/${datasetId}/captioning`)
    await expect(other.getByPlaceholder(PROMPT_FIELD)).toHaveValue('describe the lighting')
    await context.close()
  })

  test('leaving straight after typing still saves', async ({ page, request }) => {
    const datasetId = await captioningDataset(request, 'wf-flush')
    await page.goto(`/datasets/${datasetId}/captioning`)
    await page.getByPlaceholder(PROMPT_FIELD).fill('typed then left')
    await page.goto('/settings')
    await expect.poll(async () => (await savedWorkflow(request))?.customPrompt).toBe('typed then left')
  })

  test('opening the page without editing saves nothing', async ({ page, request }) => {
    const datasetId = await captioningDataset(request, 'wf-null')
    await page.goto(`/datasets/${datasetId}/captioning`)
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toBeVisible()
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
    await page.goto('/settings')
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS)

    // NULL is what lets Settings' caption defaults apply, and a real copy still arrive.
    expect(await savedWorkflow(request)).toBeNull()
  })

  test('a failed load offers Retry and never shows the page on defaults', async ({ page, request }) => {
    const datasetId = await captioningDataset(request, 'wf-retry')
    const put = await request.put('/api/v1/app-data/captioning-workflow', { data: { value: { customPrompt: 'keep me' } } })
    expect(put.status()).toBe(200)

    await page.route('**/app-data/captioning-workflow', (route) => route.fulfill({ status: 500, body: '{}' }))
    await page.goto(`/datasets/${datasetId}/captioning`)
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveCount(0)

    await page.unroute('**/app-data/captioning-workflow')
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveValue('keep me')
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
    expect((await savedWorkflow(request)).customPrompt).toBe('keep me')
  })

  test('Reset on the page clears the saved prompt', async ({ page, request }) => {
    const datasetId = await captioningDataset(request, 'wf-reset')
    await request.put('/api/v1/app-data/captioning-workflow', { data: { value: { customPrompt: 'old prompt' } } })

    await page.goto(`/datasets/${datasetId}/captioning`)
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveValue('old prompt')
    await page.getByRole('button', { name: 'Reset to defaults' }).click()
    await page.getByRole('button', { name: 'Reset', exact: true }).click()
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveValue('')

    // The DELETE goes first and the page's own save of its reset state follows, in order,
    // so what must hold is that the old prompt doesn't come back.
    await expect.poll(async () => (await savedWorkflow(request))?.customPrompt ?? '').toBe('')
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
    expect((await savedWorkflow(request))?.customPrompt ?? '').toBe('')
  })

  test('Reset in Settings clears the saved setup', async ({ page, request }) => {
    await request.put('/api/v1/app-data/captioning-workflow', { data: { value: { customPrompt: 'old prompt' } } })
    await page.goto('/settings')
    await page.getByRole('button', { name: 'Captioning', exact: true }).click()
    await page.getByRole('button', { name: 'Reset remembered Captioning configuration' }).click()
    await expect.poll(() => savedWorkflow(request)).toBeNull()
  })

  test('an old setup held by this browser arrives on the server', async ({ page, browser, request }) => {
    const datasetId = await captioningDataset(request, 'wf-legacy')
    await seedStorage(page, LEGACY_KEY, JSON.stringify({ customPrompt: 'from the old browser', additionalSteps: [] }))

    await page.goto(`/datasets/${datasetId}/captioning`)
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveValue('from the old browser')

    const context = await browser.newContext()
    const other = await context.newPage()
    await other.goto(`/datasets/${datasetId}/captioning`)
    await expect(other.getByPlaceholder(PROMPT_FIELD)).toHaveValue('from the old browser')
    await context.close()
  })

  test('an untouched default copy is not uploaded', async ({ page, request }) => {
    const datasetId = await captioningDataset(request, 'wf-default')
    await seedStorage(page, LEGACY_KEY, JSON.stringify({ style: 'detailed', customPrompt: '' }))

    await page.goto(`/datasets/${datasetId}/captioning`)
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toBeVisible()
    expect(await savedWorkflow(request)).toBeNull()
  })

  test('a differing copy asks, and choosing it updates a page already open', async ({ page, request }) => {
    const datasetId = await captioningDataset(request, 'wf-conflict')
    await request.put('/api/v1/app-data/captioning-workflow', { data: { value: { customPrompt: 'already in the app' } } })
    await seedStorage(page, LEGACY_KEY, JSON.stringify({ customPrompt: 'held by this browser', additionalSteps: [] }))

    await page.goto(`/datasets/${datasetId}/captioning`)
    const dialog = page.getByRole('dialog', { name: 'Different saved copies' })
    await expect(dialog.getByText('Prompt: already in the app')).toBeVisible()
    await expect(dialog.getByText('Prompt: held by this browser')).toBeVisible()
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveValue('already in the app')

    await dialog.getByRole('button', { name: "Use this browser's copy" }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.getByPlaceholder(PROMPT_FIELD)).toHaveValue('held by this browser')
    expect((await savedWorkflow(request)).customPrompt).toBe('held by this browser')

    await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
    expect((await savedWorkflow(request)).customPrompt).toBe('held by this browser')
  })
})
