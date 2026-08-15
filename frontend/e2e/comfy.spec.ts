import { test, expect } from '@playwright/test'
import { createDatasetViaApi } from './helpers'

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

  // The modal closes and both prompts land in editable row cells. Scoped to the
  // rows table on purpose: the prompt cells must be the *only* textareas in it, so
  // this fails if the single-line Folder cell is ever built as a textarea.
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)
  const rowsTable = page.getByTestId('comfy-rows-table')
  const promptCells = rowsTable.locator('textarea')
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

test('rows carry a per-row folder, typed in a cell or set in bulk', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-folder-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'folder plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Paste prompts…' }).click()
  await page
    .getByPlaceholder('a cat sitting on a windowsill', { exact: false })
    .fill('a knight in a fen\na knight on a ridge')
  await page.getByRole('button', { name: 'Add 2 rows' }).click()
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)

  const rowsTable = page.getByTestId('comfy-rows-table')
  const folderCells = rowsTable.locator('input[placeholder="(root)"]')
  await expect(folderCells).toHaveCount(2)

  // Typing in one cell commits on blur, and only touches that row.
  await folderCells.nth(0).fill('salt-fen')
  await folderCells.nth(0).blur()
  await expect(folderCells.nth(0)).toHaveValue('salt-fen')

  const rowsUrl = `/api/v1/comfy/plans/${plan.id}/rows`
  await expect
    .poll(async () => (await (await request.get(rowsUrl)).json()).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['salt-fen', ''])

  // Bulk-set both rows over the top of it.
  await rowsTable.locator('input.checkbox').first().check()
  await page.getByRole('button', { name: 'Set folder (2)' }).click()
  await expect(page.getByRole('heading', { name: 'Set folder' })).toBeVisible()
  await page.getByPlaceholder('e.g. salt-fen/iron-knight').fill('ridge/knight')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByRole('heading', { name: 'Set folder' })).toHaveCount(0)

  await expect
    .poll(async () => (await (await request.get(rowsUrl)).json()).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['ridge/knight', 'ridge/knight'])
})
