import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { PERSIST_DEBOUNCE_MS } from '../src/constants/storage'
import { createDatasetViaApi } from './helpers'

const INSTRUCTIONS_PLACEHOLDER = /danbooru-style tag prompts/

// ComfyUI journeys that need no ComfyUI server: plan creation and building the
// prompt queue. CI has no ComfyUI (and no torch), so a run is never started —
// the "Run" controls are deliberately left alone here; the cancel/import side of
// a run is covered request-level by backend/tests/test_comfy_cancel_stats.py.
const WORKFLOW = {
  '6': { class_type: 'CLIPTextEncode', inputs: { text: 'template prompt' } },
  '9': { class_type: 'SaveImage', inputs: {} },
}

/** A plan with a prompt pin, created through the API — setup, not under test. */
async function createPlanViaApi(
  request: import('@playwright/test').APIRequestContext,
  datasetId: string,
  name: string,
) {
  const r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: datasetId,
      name,
      workflow_json: WORKFLOW,
      pinned_params: [{ node_id: '6', input: 'text', alias: 'prompt', is_prompt: true }],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  return r.json()
}

/** The modal shows its form only once a provider exists. */
async function createProviderViaApi(request: APIRequestContext): Promise<string> {
  const r = await request.post('/api/v1/providers/', {
    data: { name: `e2e-provider-${Date.now()}`, base_url: 'http://127.0.0.1:1/v1' },
  })
  expect(r.status(), await r.text()).toBeLessThan(300)
  return (await r.json()).id
}

async function savedGenSettings(request: APIRequestContext, planId: string) {
  return (await (await request.get(`/api/v1/comfy/plans/${planId}/gen-settings`)).json()).value
}

async function openGenerateModal(page: Page, datasetId: string) {
  await page.goto(`/datasets/${datasetId}/comfy`)
  await page.getByRole('button', { name: 'Generate prompts…' }).click()
  await expect(page.getByRole('heading', { name: 'Generate prompts' })).toBeVisible()
}

test.describe('saved Generate prompts settings', () => {
  let providerId: string

  test.beforeEach(async ({ request }) => {
    providerId = await createProviderViaApi(request)
  })

  test.afterEach(async ({ request }) => {
    await request.delete(`/api/v1/providers/${providerId}`)
  })

  test('instructions typed in one browser appear in another with empty storage', async ({ page, browser, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-share-${Date.now()}`)
    const plan = await createPlanViaApi(request, ds.id, 'share plan')

    await openGenerateModal(page, ds.id)
    await page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER).fill('always tag the weather')
    await expect.poll(async () => (await savedGenSettings(request, plan.id))?.instructions).toBe('always tag the weather')

    const context = await browser.newContext()
    const other = await context.newPage()
    await openGenerateModal(other, ds.id)
    await expect(other.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveValue('always tag the weather')
    await context.close()
  })

  test('closing straight after typing still saves, and reopening waits for that save', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-flush-${Date.now()}`)
    await createPlanViaApi(request, ds.id, 'flush plan')

    // Held long enough that a reopen reads the server before this lands, unless it waits.
    await page.route('**/gen-settings', async (route) => {
      if (route.request().method() === 'PUT') await new Promise((resolve) => setTimeout(resolve, 1000))
      await route.continue()
    })

    await openGenerateModal(page, ds.id)
    await page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER).fill('typed then closed')
    await page.getByRole('button', { name: '×' }).click()
    await expect(page.getByRole('heading', { name: 'Generate prompts' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Generate prompts…' }).click()
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveValue('typed then closed')
  })

  test('a save queued behind a slow one still lands when the page goes away', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-queued-${Date.now()}`)
    const plan = await createPlanViaApi(request, ds.id, 'queued plan')

    let held = false
    await page.route('**/gen-settings', async (route) => {
      if (route.request().method() === 'PUT' && !held) {
        held = true
        await new Promise((resolve) => setTimeout(resolve, 1500))
      }
      await route.continue()
    })

    await openGenerateModal(page, ds.id)
    const field = page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)
    await field.fill('first')
    await expect.poll(() => held).toBe(true)
    await field.fill('second')
    // Past the debounce, so this edit has left the hook and sits queued in the writer,
    // where the hook's own flush can't see it.
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 2)
    await page.goto('/settings')

    await expect.poll(async () => (await savedGenSettings(request, plan.id))?.instructions).toBe('second')
  })

  test('a failing save keeps retrying, and meanwhile a reopen refuses to show the old value', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-failing-${Date.now()}`)
    const plan = await createPlanViaApi(request, ds.id, 'failing plan')

    await openGenerateModal(page, ds.id)
    await page.route('**/gen-settings', (route) =>
      route.request().method() === 'PUT' ? route.fulfill({ status: 503, body: '{}' }) : route.continue())
    await page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER).fill('must not be lost')
    await expect(page.getByText(/Couldn't save your changes to the app/)).toBeVisible({ timeout: 15_000 })

    await page.getByRole('button', { name: '×' }).click()
    await page.getByRole('button', { name: 'Generate prompts…' }).click()
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveCount(0)

    await page.unroute('**/gen-settings')
    await expect.poll(async () => (await savedGenSettings(request, plan.id))?.instructions, { timeout: 15_000 }).toBe('must not be lost')
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveValue('must not be lost')
  })

  test('opening the modal without editing saves nothing', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-null-${Date.now()}`)
    const plan = await createPlanViaApi(request, ds.id, 'untouched plan')

    await openGenerateModal(page, ds.id)
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toBeVisible()
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS * 3)
    await page.getByRole('button', { name: '×' }).click()
    await page.waitForTimeout(PERSIST_DEBOUNCE_MS)

    // NULL is what lets a real copy still arrive from another browser's first import.
    expect(await savedGenSettings(request, plan.id)).toBeNull()
  })

  test('a failed load offers Retry and never shows an empty form', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-retry-${Date.now()}`)
    const plan = await createPlanViaApi(request, ds.id, 'retry plan')
    const put = await request.put(`/api/v1/comfy/plans/${plan.id}/gen-settings`, { data: { instructions: 'keep me' } })
    expect(put.status()).toBe(200)

    await page.route('**/gen-settings', (route) => route.fulfill({ status: 500, body: '{}' }))
    await page.goto(`/datasets/${ds.id}/comfy`)
    await page.getByRole('button', { name: 'Generate prompts…' }).click()
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveCount(0)

    await page.unroute('**/gen-settings')
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveValue('keep me')
  })

  test('saving as you type leaves the workflow editor mounted', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-gen-stamp-${Date.now()}`)
    const plan = await createPlanViaApi(request, ds.id, 'stamp plan')

    await openGenerateModal(page, ds.id)
    await page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER).fill('no bump')
    await expect.poll(async () => (await savedGenSettings(request, plan.id))?.instructions).toBe('no bump')

    const after = await (await request.get(`/api/v1/comfy/plans/${plan.id}`)).json()
    expect(after.updated_at).toBe(plan.updated_at)
  })
})

test('comfy page shows its empty state and creates a plan', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-empty-${Date.now()}`)

  await page.goto(`/datasets/${ds.id}/comfy`)
  await expect(page.getByRole('heading', { name: 'ComfyUI generation' })).toBeVisible()
  await expect(page.getByText('No plans yet.', { exact: false })).toBeVisible()

  await page.getByRole('button', { name: '+ New plan' }).click()
  await page.getByPlaceholder('Plan name').fill('smoke plan')
  await page.getByRole('button', { name: 'Create', exact: true }).click()

  // Creating a plan selects it and jumps to Workflow & Pins — a fresh plan has
  // no workflow, so that is the only useful next step.
  await expect(page.getByRole('heading', { name: 'Workflow template' })).toBeVisible()
  await expect(page.getByText('No plans yet.', { exact: false })).toHaveCount(0)
  await expect(page.locator('select').first()).toContainText('smoke plan (0 rows)')
})

test('pasted prompts become queue rows', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-rows-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'paste plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  // The single plan is auto-selected and the Rows section is the default.
  await expect(page.getByText('No rows yet', { exact: false })).toBeVisible()

  await page.getByRole('button', { name: 'Paste prompts…' }).click()
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toBeVisible()
  await page
    .getByPlaceholder('a cat sitting on a windowsill', { exact: false })
    .fill('a cat on a windowsill\na dog on a beach')
  await page.getByRole('button', { name: 'Add 2 rows' }).click()

  // The modal closes and both prompts land in editable row cells (the rows table
  // owns every textarea on the page once the paste modal is gone).
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)
  const promptCells = page.locator('textarea')
  await expect(promptCells).toHaveCount(2)
  await expect(promptCells.nth(0)).toHaveValue('a cat on a windowsill')
  await expect(promptCells.nth(1)).toHaveValue('a dog on a beach')
  await expect(page.locator('select').first()).toContainText('paste plan (2 rows)')

  // Cross-check through the API: the rows carry the pinned alias, not just text
  // rendered client-side.
  const plans = await (await request.get('/api/v1/comfy/plans', { params: { dataset_id: ds.id } })).json()
  const rows = await (await request.get(`/api/v1/comfy/plans/${plans[0].id}/rows`)).json()
  expect(rows.map((r: { values: Record<string, string> }) => r.values.prompt)).toEqual([
    'a cat on a windowsill',
    'a dog on a beach',
  ])
})
