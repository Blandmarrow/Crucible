import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { PERSIST_DEBOUNCE_MS } from '../src/constants/storage'
import { addMenu, createDatasetViaApi } from './helpers'

const INSTRUCTIONS_PLACEHOLDER = /danbooru-style tag prompts/

// ComfyUI journeys that need no ComfyUI server: plan creation and building the
// prompt queue. The "Run" controls are left alone here; the journeys that press
// Run are comfy-run.spec.ts, against the fake ComfyUI that serve-comfy.mjs runs.
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
  await addMenu(page, 'Generate prompts…')
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

    await addMenu(page, 'Generate prompts…')
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
    await addMenu(page, 'Generate prompts…')
    await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveCount(0)

    await page.unroute('**/gen-settings')
    await expect.poll(async () => (await savedGenSettings(request, plan.id))?.instructions, { timeout: 15_000 }).toBe('must not be lost')
    await page.getByRole('button', { name: 'Retry', exact: true }).click()
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
    await addMenu(page, 'Generate prompts…')
    await expect(page.getByRole('button', { name: 'Retry', exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByPlaceholder(INSTRUCTIONS_PLACEHOLDER)).toHaveCount(0)

    await page.unroute('**/gen-settings')
    await page.getByRole('button', { name: 'Retry', exact: true }).click()
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

  await page.getByTestId('comfy-plan-rail').getByRole('button', { name: 'New plan' }).click()
  await page.getByPlaceholder('Plan name').fill('smoke plan')
  await page.getByRole('button', { name: 'Create', exact: true }).click()

  // Creating a plan selects it and jumps to Workflow & Pins — a fresh plan has
  // no workflow, so that is the only useful next step.
  await expect(page.getByRole('heading', { name: 'Workflow template' })).toBeVisible()
  await expect(page.getByText('No plans yet.', { exact: false })).toHaveCount(0)
  await expect(page.getByTestId('comfy-plan-name')).toHaveText('smoke plan')
})

test('pasted prompts become queue rows', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-rows-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'paste plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  // The single plan is auto-selected and the Rows section is the default.
  await expect(page.getByText('No rows yet', { exact: false })).toBeVisible()

  await addMenu(page, 'Paste prompts…')
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toBeVisible()
  await page
    .getByPlaceholder('a cat sitting on a windowsill', { exact: false })
    .fill('a cat on a windowsill\na dog on a beach')
  await page.getByRole('button', { name: 'Add 2 rows' }).click()

  // The modal closes and both prompts land in editable row cells. Scoped to the
  // rows table on purpose: the prompt cells must be the *only* textareas in it, so
  // this fails if the single-line Folder cell is ever built as a textarea.
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)
  const rowsTable = page.getByTestId('comfy-rows-table')
  const promptCells = rowsTable.locator('textarea')
  await expect(promptCells).toHaveCount(2)
  await expect(promptCells.nth(0)).toHaveValue('a cat on a windowsill')
  await expect(promptCells.nth(1)).toHaveValue('a dog on a beach')
  // The rail lists the plan with its row count.
  await expect(page.getByTestId('comfy-plan-rail').getByRole('button', { name: /^paste plan/ })).toContainText('2')

  // Cross-check through the API: the rows carry the pinned alias, not just text
  // rendered client-side.
  const plans = await (await request.get('/api/v1/comfy/plans', { params: { dataset_id: ds.id } })).json()
  const rows = await (await request.get(`/api/v1/comfy/plans/${plans[0].id}/rows`)).json()
  expect(rows.map((r: { values: Record<string, string> }) => r.values.prompt)).toEqual([
    'a cat on a windowsill',
    'a dog on a beach',
  ])
})

test('rows carry a per-row folder, typed in a cell or set in bulk', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-folder-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'folder plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await addMenu(page, 'Paste prompts…')
  await page
    .getByPlaceholder('a cat sitting on a windowsill', { exact: false })
    .fill('a knight in a fen\na knight on a ridge')
  await page.getByRole('button', { name: 'Add 2 rows' }).click()
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)

  const rowsTable = page.getByTestId('comfy-rows-table')
  const folderCells = rowsTable.locator('input[placeholder="(root)"]')
  await expect(folderCells).toHaveCount(2)

  // Typing in one cell commits on blur, and only touches that row. The table then
  // groups the rows by folder, (root) first, so the row moves under its own header.
  await folderCells.nth(0).fill('salt-fen')
  await folderCells.nth(0).blur()
  await expect(rowsTable.locator('[data-testid="comfy-group"][data-folder="salt-fen"]')).toBeVisible()

  const rowsUrl = `/api/v1/comfy/plans/${plan.id}/rows`
  await expect
    .poll(async () => (await (await request.get(rowsUrl)).json()).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['salt-fen', ''])

  // Bulk-set both rows over the top of it.
  await rowsTable.locator('input.checkbox').first().check()
  await expect(page.getByTestId('comfy-selection-bar')).toContainText('2 selected')
  await page.getByTestId('comfy-selection-bar').getByRole('button', { name: 'Set folder…' }).click()
  await expect(page.getByRole('heading', { name: 'Set folder' })).toBeVisible()
  await page.getByPlaceholder('e.g. salt-fen/iron-knight').fill('ridge/knight')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByRole('heading', { name: 'Set folder' })).toHaveCount(0)

  await expect
    .poll(async () => (await (await request.get(rowsUrl)).json()).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['ridge/knight', 'ridge/knight'])
})

// ── M6 §8b: the plan rail, the folder tree and the grouped table ──────────────────

async function addRowsViaApi(request: APIRequestContext, planId: string, lines: string[]) {
  const r = await request.post(`/api/v1/comfy/plans/${planId}/rows/bulk`, { data: { lines } })
  expect(r.status(), await r.text()).toBe(200)
}

async function rowsOf(request: APIRequestContext, planId: string) {
  return (await request.get(`/api/v1/comfy/plans/${planId}/rows`)).json()
}

test('a plan duplicates from the rail, rows reset to pending', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-dup-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'dup plan')
  await addRowsViaApi(request, plan.id, ['a fox in snow', 'a hare in snow'])

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rail = page.getByTestId('comfy-plan-rail')
  await rail.getByRole('button', { name: 'Plan actions for dup plan' }).click()
  await page.getByRole('menuitem', { name: 'Duplicate…' }).click()
  await expect(page.getByRole('heading', { name: 'Duplicate plan' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Name' })).toHaveValue('dup plan (copy)')
  await page.getByRole('button', { name: 'Duplicate', exact: true }).click()

  // The copy is selected, and its rows came with it.
  await expect(page.getByTestId('comfy-plan-name')).toHaveText('dup plan (copy)')
  await expect(page.getByTestId('comfy-rows-table').locator('textarea')).toHaveCount(2)
  const plans = await (await request.get('/api/v1/comfy/plans', { params: { dataset_id: ds.id } })).json()
  const copy = plans.find((p: { name: string }) => p.name === 'dup plan (copy)')
  const rows = await rowsOf(request, copy.id)
  expect(rows.map((r: { status: string }) => r.status)).toEqual(['pending', 'pending'])
})

test('folders are made, renamed and picked in the tree', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-tree-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'tree plan')
  await addRowsViaApi(request, plan.id, ['a knight in a fen', 'a monk on a ridge'])
  const ids = (await rowsOf(request, plan.id)).map((r: { id: string }) => r.id)
  const set = await request.post(`/api/v1/comfy/plans/${plan.id}/rows/set-subfolder`, {
    data: { subfolder: 'fen', row_ids: [ids[0]] },
  })
  expect(set.status(), await set.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  const tree = page.getByTestId('comfy-folder-tree')

  // A new, empty folder stays in the tree.
  await tree.getByRole('button', { name: 'New folder' }).click()
  await page.getByRole('textbox', { name: 'Folder name' }).fill('ridge')
  await page.getByRole('button', { name: 'Create', exact: true }).click()
  await expect(tree.getByRole('button', { name: /^ridge/ })).toBeVisible()

  // Rename "fen" from its menu: its row moves with it.
  await tree.getByRole('button', { name: 'Folder actions for fen' }).click()
  await page.getByRole('menuitem', { name: 'Rename…' }).click()
  await page.getByRole('textbox', { name: 'Folder name' }).fill('salt-fen')
  await page.getByRole('button', { name: 'Rename', exact: true }).click()
  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['salt-fen', ''])

  // Picking a folder narrows the table to it.
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(rowsTable.locator('textarea')).toHaveCount(2)
  await tree.getByRole('button', { name: /^salt-fen/ }).click()
  await expect(rowsTable.locator('textarea')).toHaveCount(1)
  await expect(rowsTable.locator('textarea').first()).toHaveValue('a knight in a fen')
  await tree.getByRole('button', { name: /^All rows/ }).click()
  await expect(rowsTable.locator('textarea')).toHaveCount(2)
})

test('the page remembers the plan and tab per dataset', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-remember-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'first plan')
  await createPlanViaApi(request, ds.id, 'second plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByTestId('comfy-plan-rail').getByRole('button', { name: /^second plan/ }).click()
  await expect(page.getByTestId('comfy-plan-name')).toHaveText('second plan')
  await page.getByRole('button', { name: 'Workflow & Pins' }).click()
  await expect(page.getByRole('heading', { name: 'Workflow template' })).toBeVisible()

  await page.reload()
  await expect(page.getByTestId('comfy-plan-name')).toHaveText('second plan')
  await expect(page.getByRole('heading', { name: 'Workflow template' })).toBeVisible()
})

test('Set folder asks before moving rows the blueprint counts', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-linked-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'bp plan')
  const spec = {
    axes: { character: [{ id: 'c0', label: 'Knight', descriptor: 'a knight' }] },
    variations: {},
    scenarios: [{ name: 'solo', combine: ['character'], count: 1, path: '{character}', template: '{character}' }],
  }
  const pv = await (await request.post(`/api/v1/comfy/plans/${plan.id}/blueprint/preview`, {
    data: { spec, seed: 5 },
  })).json()
  const applied = await request.post(`/api/v1/comfy/plans/${plan.id}/blueprint/apply`, {
    data: { spec, seed: 5, confirm_total: pv.rows_to_add },
  })
  expect(applied.status(), await applied.text()).toBe(200)
  await addRowsViaApi(request, plan.id, ['a hand-made prompt'])
  const before = await rowsOf(request, plan.id)
  const linked = before.find((r: { blueprint_linked: boolean }) => r.blueprint_linked)
  expect(linked).toBeTruthy()

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rowsTable = page.getByTestId('comfy-rows-table')
  await rowsTable.locator('input.checkbox').first().check()
  await expect(page.getByTestId('comfy-selection-bar')).toContainText('2 selected')
  await page.getByTestId('comfy-selection-bar').getByRole('button', { name: 'Set folder…' }).click()
  await page.getByPlaceholder('e.g. salt-fen/iron-knight').fill('elsewhere')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTestId('comfy-linked-confirm')).toContainText('1 of these 2 rows came from the blueprint')
  await page.getByRole('button', { name: 'Move hand-made rows only' }).click()
  await expect(page.getByRole('heading', { name: 'Set folder' })).toHaveCount(0)

  // The blueprint's row stayed in its folder and stayed linked; the hand-made one moved.
  await expect
    .poll(async () => {
      const rows = await rowsOf(request, plan.id)
      return rows.map((r: { subfolder: string; blueprint_linked: boolean }) => [r.blueprint_linked, r.subfolder])
    })
    .toEqual(before.map((r: { subfolder: string; blueprint_linked: boolean }) =>
      [r.blueprint_linked, r.blueprint_linked ? r.subfolder : 'elsewhere']))
})

// ── M6 §8c: the button routes for copying rows to a plan and reordering them ─────

test('Copy to plan keeps each row folder, and Move takes them out', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-copy-${Date.now()}`)
  const source = await createPlanViaApi(request, ds.id, 'source plan')
  const target = await createPlanViaApi(request, ds.id, 'target plan')
  await addRowsViaApi(request, source.id, ['a knight in a fen', 'a monk on a ridge'])
  const ids = (await rowsOf(request, source.id)).map((r: { id: string }) => r.id)
  const set = await request.post(`/api/v1/comfy/plans/${source.id}/rows/set-subfolder`, {
    data: { subfolder: 'knights', row_ids: [ids[0]] },
  })
  expect(set.status(), await set.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByTestId('comfy-plan-rail').getByRole('button', { name: /^source plan/ }).click()
  const rowsTable = page.getByTestId('comfy-rows-table')
  await rowsTable.locator('input.checkbox').first().check()
  await page.getByTestId('comfy-selection-bar').getByRole('button', { name: 'Copy to plan…' }).click()
  await expect(page.getByRole('combobox', { name: 'Target plan' })).toHaveValue(target.id)
  await page.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect
    .poll(async () => (await rowsOf(request, target.id))
      .map((r: { subfolder: string; values: { prompt: string } }) => [r.values.prompt, r.subfolder]))
    .toEqual([['a knight in a fen', 'knights'], ['a monk on a ridge', '']])
  expect(await rowsOf(request, source.id)).toHaveLength(2)

  // A move empties the source of those rows.
  await rowsTable.locator('input.checkbox').first().check()
  await page.getByTestId('comfy-selection-bar').getByRole('button', { name: 'Copy to plan…' }).click()
  await page.getByRole('radio', { name: /^Move/ }).check()
  await page.getByRole('button', { name: 'Move', exact: true }).click()
  await expect.poll(async () => (await rowsOf(request, source.id)).length).toBe(0)
  expect(await rowsOf(request, target.id)).toHaveLength(4)
})

test('the row menu moves a row within its folder', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-order-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'order plan')
  await addRowsViaApi(request, plan.id, ['first', 'second', 'third'])

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(rowsTable.locator('textarea')).toHaveCount(3)
  await rowsTable.getByRole('button', { name: 'Row actions' }).nth(2).click()
  // The last row cannot go further down.
  await expect(page.getByRole('menuitem', { name: 'Move down' })).toHaveAttribute('aria-disabled', 'true')
  await page.getByRole('menuitem', { name: 'Move to top of folder' }).click()
  const prompts = async () => (await rowsOf(request, plan.id)).map((r: { values: { prompt: string } }) => r.values.prompt)
  await expect.poll(prompts).toEqual(['third', 'first', 'second'])

  await rowsTable.getByRole('button', { name: 'Row actions' }).nth(1).click()
  await page.getByRole('menuitem', { name: 'Move down' }).click()
  await expect.poll(prompts).toEqual(['third', 'second', 'first'])
})

test('the library panel stays open across a reload', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-libopen-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'lib plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  const toggle = page.getByRole('button', { name: 'Library', exact: true })
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await toggle.click()
  await expect(page.getByTestId('comfy-library-panel')).toBeVisible()
  await page.waitForTimeout(PERSIST_DEBOUNCE_MS + 100)
  await page.reload()
  await expect(page.getByTestId('comfy-library-panel')).toBeVisible()
  await page.getByRole('button', { name: 'Close the library' }).click()
  await expect(page.getByTestId('comfy-library-panel')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Library', exact: true })).toHaveAttribute('aria-pressed', 'false')
})

// ── M6 §8d: the Add menu, the selection bar, the paste dialog on ModalShell ──────

test('the Add menu says why an entry is off, and Escape hands focus back', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-addmenu-${Date.now()}`)
  // A pin that is not marked as the prompt: Add row works, the prompt entries do not.
  const r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id,
      name: 'no prompt pin',
      workflow_json: WORKFLOW,
      pinned_params: [{ node_id: '6', input: 'text', alias: 'text', is_prompt: false }],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  const trigger = page.getByTestId('comfy-add-menu')
  await trigger.click()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  const paste = page.getByRole('menuitem', { name: 'Paste prompts…' })
  await expect(paste).toHaveAttribute('aria-disabled', 'true')
  await expect(paste).toHaveAttribute('title', 'Mark a pinned parameter as the prompt first')
  await expect(page.getByRole('menuitem', { name: 'Add row' })).not.toHaveAttribute('aria-disabled', 'true')
  // A click on a disabled entry does nothing and leaves the menu open. Forced, since
  // Playwright refuses to click an aria-disabled element; a mouse does not.
  await paste.click({ force: true })
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)
  await expect(page.getByRole('menu')).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('the paste dialog is a real dialog, and Escape keeps its draft', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-pastedlg-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'paste dialog plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await addMenu(page, 'Paste prompts…')
  const dialog = page.getByRole('dialog', { name: 'Paste prompts' })
  await expect(dialog).toBeVisible()
  const box = dialog.getByPlaceholder('a cat sitting on a windowsill', { exact: false })
  await expect(box).toBeFocused()
  await box.fill('a half-written prompt')

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByTestId('comfy-add-menu')).toBeFocused()
  await addMenu(page, 'Paste prompts…')
  await expect(box).toHaveValue('a half-written prompt')
})

test('the selection bar holds its place, so the table never moves', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-selbar-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'selection bar plan')
  await addRowsViaApi(request, plan.id, ['a knight in a fen', 'a monk on a ridge'])

  await page.goto(`/datasets/${ds.id}/comfy`)
  const bar = page.getByTestId('comfy-selection-bar')
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(bar).toContainText('Tick rows to move, copy, run or delete them.')
  await expect(rowsTable.locator('textarea')).toHaveCount(2)
  // Measured against the toolbar above the bar, not the viewport, so a scroll that
  // check() makes to reach the box cannot pass for the bar moving the table.
  const filter = page.getByLabel('Filter prompts')
  const gap = async () => (await rowsTable.boundingBox())!.y - (await filter.boundingBox())!.y
  const before = await gap()

  await rowsTable.locator('input.checkbox').nth(1).check()
  await expect(bar).toContainText('1 selected')
  expect(await gap()).toBe(before)

  await bar.getByRole('button', { name: 'Clear' }).click()
  await expect(bar).toContainText('Tick rows to move')
  await expect(rowsTable.locator('input.checkbox:checked')).toHaveCount(0)
})

test('Import .txt and Edit prompts work from the Add menu', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-addimport-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'import plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  // The picker must open from inside the menu entry's click: a file input clicked
  // any later has lost the user activation and the browser shows nothing.
  const chooser = page.waitForEvent('filechooser')
  await addMenu(page, 'Import .txt…')
  await (await chooser).setFiles([
    { name: 'one.txt', mimeType: 'text/plain', buffer: Buffer.from('a knight\nin a fen') },
    { name: 'two.txt', mimeType: 'text/plain', buffer: Buffer.from('a monk on a ridge') },
  ])
  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { values: { prompt: string } }) => r.values.prompt).sort())
    .toEqual(['a knight in a fen', 'a monk on a ridge'])

  await addMenu(page, 'Edit prompts…')
  await expect(page.getByRole('heading', { name: 'Edit prompts' })).toBeVisible()
})

// ── M6 §8 review fixes: the button routes behind the drags, and the keyboard ─────

async function libraryTexts(request: APIRequestContext, categoryPrefix: string) {
  const all: { prompts: { category: string; text: string }[] } = await (await request.get('/api/v1/comfy/library')).json()
  return all.prompts.filter((p) => p.category.startsWith(categoryPrefix)).map((p) => [p.category, p.text])
}

test('the selection bar saves the ticked rows to a library category', async ({ page, request }) => {
  const stamp = `savelib${Date.now()}`
  const ds = await createDatasetViaApi(request, `e2e-comfy-savelib-${stamp}`)
  const plan = await createPlanViaApi(request, ds.id, 'save plan')
  await addRowsViaApi(request, plan.id, [`a knight ${stamp}`, `a monk ${stamp}`])

  try {
    await page.goto(`/datasets/${ds.id}/comfy`)
    const rowsTable = page.getByTestId('comfy-rows-table')
    await expect(rowsTable.locator('textarea')).toHaveCount(2)
    await rowsTable.locator('input.checkbox').nth(1).check()
    await page.getByTestId('comfy-selection-bar').getByRole('button', { name: 'Save to library…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Save to library' })
    await dialog.getByRole('combobox').fill(`${stamp}/studio`)
    await dialog.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(dialog).toHaveCount(0)

    // Only the ticked row went over.
    await expect.poll(() => libraryTexts(request, stamp)).toEqual([[`${stamp}/studio`, `a knight ${stamp}`]])
  } finally {
    await request.post('/api/v1/comfy/library/categories/delete', { data: { path: stamp } })
  }
})

test('the tree menu moves a folder under another, and its rows go with it', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-treemove-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'tree move plan')
  await addRowsViaApi(request, plan.id, ['a knight in a fen', 'a monk on a ridge'])
  const ids = (await rowsOf(request, plan.id)).map((r: { id: string }) => r.id)
  for (const [folder, id] of [['inner', ids[0]], ['outer', ids[1]]]) {
    const r = await request.post(`/api/v1/comfy/plans/${plan.id}/rows/set-subfolder`, { data: { subfolder: folder, row_ids: [id] } })
    expect(r.status(), await r.text()).toBe(200)
  }

  await page.goto(`/datasets/${ds.id}/comfy`)
  const tree = page.getByTestId('comfy-folder-tree')
  await tree.getByRole('button', { name: 'Folder actions for inner' }).click()
  await page.getByRole('menuitem', { name: 'Move to…' }).click()
  const dialog = page.getByRole('dialog', { name: /Move subfolder inner/ })
  await dialog.getByTitle('outer', { exact: true }).click()
  await expect(dialog.getByText('outer/inner')).toBeVisible()
  await dialog.getByRole('button', { name: 'Move', exact: true }).click()

  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['outer/inner', 'outer'])
})

test('Load .json file puts a workflow in the editor, and a UI-format export is refused', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-loadjson-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'load json plan')
  const loaded = { '3': { class_type: 'KSampler', inputs: { seed: 1, steps: 20 } }, '9': { class_type: 'SaveImage', inputs: {} } }

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Workflow & Pins' }).click()
  const editor = page.getByPlaceholder(/Paste new JSON here/)

  const first = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Load .json file' }).click()
  await (await first).setFiles({ name: 'wf.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(loaded)) })
  await expect(editor).toHaveValue(JSON.stringify(loaded))
  await expect(page.getByTestId('comfy-workflow-drop').getByText('Import images from')).toBeVisible()

  const second = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: 'Load .json file' }).click()
  await (await second).setFiles({ name: 'ui.json', mimeType: 'application/json', buffer: Buffer.from('{"nodes": [], "links": []}') })
  await expect(page.getByText(/looks like a UI-format export/)).toBeVisible()
})

test('Set folder from a folder view drops the moved rows from the selection', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-setsel-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'set selection plan')
  await addRowsViaApi(request, plan.id, ['one', 'two'])
  const ids = (await rowsOf(request, plan.id)).map((r: { id: string }) => r.id)
  const set = await request.post(`/api/v1/comfy/plans/${plan.id}/rows/set-subfolder`, { data: { subfolder: 'src', row_ids: ids } })
  expect(set.status(), await set.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByTestId('comfy-folder-tree').getByRole('button', { name: /^src/ }).click()
  const rowsTable = page.getByTestId('comfy-rows-table')
  await rowsTable.locator('input.checkbox').first().check()
  const bar = page.getByTestId('comfy-selection-bar')
  await expect(bar).toContainText('2 selected')
  await bar.getByRole('button', { name: 'Set folder…' }).click()
  await page.getByPlaceholder('e.g. salt-fen/iron-knight').fill('dst')
  await page.getByRole('button', { name: 'Apply' }).click()

  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['dst', 'dst'])
  // The rows left the folder in view, so they left the selection too.
  await expect(bar).toContainText('Tick rows to move')
  await expect(rowsTable.locator('input.checkbox:checked')).toHaveCount(0)
})

test('a desktop file dropped outside any drop zone is refused, not opened', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-filedrop-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'file drop plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await expect(page.getByTestId('comfy-add-menu')).toBeVisible()
  const urlBefore = page.url()
  // The Add button sits in the toolbar, which no zone covers.
  const outcome = await page.getByTestId('comfy-add-menu').evaluate((el) => {
    const dt = new DataTransfer()
    dt.items.add(new File(['a fox in snow'], 'fox.txt', { type: 'text/plain' }))
    const fire = (type: string) => {
      const ev = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt })
      el.dispatchEvent(ev)
      return ev.defaultPrevented
    }
    return { over: fire('dragover'), dropEffect: dt.dropEffect, drop: fire('drop') }
  })
  expect(outcome).toEqual({ over: true, dropEffect: 'none', drop: true })
  expect(page.url()).toBe(urlBefore)
  await expect(page.getByTestId('comfy-plan-name')).toHaveText('file drop plan')
  expect(await rowsOf(request, plan.id)).toHaveLength(0)
})

test('a row is reordered from the keyboard through its grip menu', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-kbd-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'keyboard plan')
  await addRowsViaApi(request, plan.id, ['first', 'second', 'third'])

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(rowsTable.locator('textarea')).toHaveCount(3)
  const grip = rowsTable.getByRole('button', { name: 'Row actions' }).nth(1)
  await grip.focus()
  await page.keyboard.press('Enter')

  await expect(page.getByRole('menu')).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Move to top of folder' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Move up' })).toBeFocused()
  await page.keyboard.press('Enter')

  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { values: { prompt: string } }) => r.values.prompt))
    .toEqual(['second', 'first', 'third'])
  await expect(page.getByRole('menu')).toHaveCount(0)
})

test('a dialog opened from a rail menu hands focus back to the ⋯ button on Escape', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-focusback-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'focus plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  const trigger = page.getByTestId('comfy-plan-rail').getByRole('button', { name: 'Plan actions for focus plan' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem', { name: 'Rename…' })).toBeFocused()
  await page.getByRole('menuitem', { name: 'Duplicate…' }).click()
  await expect(page.getByRole('heading', { name: 'Duplicate plan' })).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(page.getByRole('heading', { name: 'Duplicate plan' })).toHaveCount(0)
  await expect(trigger).toBeFocused()
})
