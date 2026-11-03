import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, setComfyUrlViaApi } from './helpers'

// ComfyUI journeys that press Run. The backend talks to the fake ComfyUI that
// e2e/serve-comfy.mjs serves beside it (backend/tests/fake_comfy.py): every
// SaveImage node yields a real PNG carrying the submitted graph, and a marker in
// the prompt text picks a failure. comfy.spec.ts holds the journeys that need no
// server at all.
const FAIL_MARKER = '[[fake:fail]]'
const FAIL_MESSAGE = /fake out-of-memory/

const WORKFLOW = {
  '4': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'sdxl.safetensors' } },
  '6': { class_type: 'CLIPTextEncode', inputs: { text: 'template prompt', clip: ['4', 1] } },
  '8': { class_type: 'EmptyImage', inputs: { width: 64, height: 64, batch_size: 1, color: 0 } },
  '9': { class_type: 'SaveImage', inputs: { images: ['8', 0] } },
}

type Row = { id: string; status: string; subfolder: string; image_ids: string[]; error_msg: string | null }

async function createPlanViaApi(request: APIRequestContext, datasetId: string, name: string) {
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

async function pastePrompts(page: import('@playwright/test').Page, prompts: string[]) {
  await page.getByRole('button', { name: 'Paste prompts…' }).click()
  await page.getByPlaceholder('a cat sitting on a windowsill', { exact: false }).fill(prompts.join('\n'))
  await page.getByRole('button', { name: `Add ${prompts.length} rows` }).click()
  await expect(page.getByRole('heading', { name: 'Paste prompts' })).toHaveCount(0)
}

test.beforeEach(async ({ request }) => {
  await setComfyUrlViaApi(request)
})

test.afterEach(async ({ request }) => {
  await setComfyUrlViaApi(request, '')
})

test('a run files each row under its folder and imports the image', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-run-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'run plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await pastePrompts(page, ['a knight, close up', 'a knight, wide shot'])

  const rowsTable = page.getByTestId('comfy-rows-table')
  const folderCells = rowsTable.locator('input[placeholder="(root)"]')
  await folderCells.nth(0).fill('knight/close')
  await folderCells.nth(0).blur()
  await folderCells.nth(1).fill('knight/wide')
  await folderCells.nth(1).blur()
  const rowsUrl = `/api/v1/comfy/plans/${plan.id}/rows`
  await expect
    .poll(async () => ((await (await request.get(rowsUrl)).json()) as Row[]).map((r) => r.subfolder))
    .toEqual(['knight/close', 'knight/wide'])

  await page.getByRole('button', { name: 'Run pending (2)' }).click()
  await expect(rowsTable.getByText('completed', { exact: true })).toHaveCount(2, { timeout: 30_000 })
  await expect(page.getByRole('button', { name: 'Run pending (0)' })).toBeVisible()

  // Both outputs landed in the dataset under their own folders.
  const rows: Row[] = await (await request.get(rowsUrl)).json()
  const images = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(images.map((i: { subfolder: string }) => i.subfolder).sort()).toEqual(['knight/close', 'knight/wide'])
  expect(images.map((i: { id: string }) => i.id).sort()).toEqual(rows.flatMap((r) => r.image_ids).sort())

  // The imported PNG carried the graph the run submitted: the detail page shows
  // ComfyUI generation metadata with this row's prompt, not the plan's template.
  // Scoped to the metadata panel's badge and prompt paragraph: "ComfyUI" is also
  // the provenance source name, and the prompt is also the caption.
  await rowsTable.getByRole('button', { name: 'View' }).first().click()
  await expect(page.locator('.badge-info', { hasText: /^ComfyUI$/ })).toBeVisible()
  await expect(page.locator('p', { hasText: /^a knight, close up$/ })).toBeVisible()
})

test('a failed row shows ComfyUI\'s error and can be reset', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-fail-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'fail plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await pastePrompts(page, ['this one works', `this one breaks ${FAIL_MARKER}`])

  const rowsTable = page.getByTestId('comfy-rows-table')
  await page.getByRole('button', { name: 'Run pending (2)' }).click()
  await expect(rowsTable.getByText('completed', { exact: true })).toHaveCount(1, { timeout: 30_000 })
  const failed = rowsTable.getByText('failed', { exact: true })
  await expect(failed).toHaveCount(1)
  await expect(failed).toHaveAttribute('title', FAIL_MESSAGE)

  await page.getByRole('button', { name: 'Reset failed (1)' }).click()
  await expect(rowsTable.getByText('pending', { exact: true })).toHaveCount(1)
  const rows: Row[] = await (await request.get(`/api/v1/comfy/plans/${plan.id}/rows`)).json()
  expect(rows.map((r) => r.status)).toEqual(['completed', 'pending'])
  const images = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(images).toHaveLength(1)
})

test('a hand-written blueprint expands into rows with folders, and they run', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-blueprint-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'blueprint plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()

  // Axis: character with two entities.
  await page.getByRole('button', { name: '+ Add axis' }).click()
  await page.getByLabel('Axis name').fill('character')
  await page.getByLabel('Axis name').blur()
  const axis = page.getByTestId('axis-character')
  await axis.getByRole('button', { name: '+ Add entity' }).click()
  await axis.getByRole('button', { name: '+ Add entity' }).click()
  await axis.getByLabel('character entity label').nth(0).fill('Iron Knight')
  await axis.getByLabel('character entity descriptor').nth(0).fill('an iron knight')
  await axis.getByLabel('character entity label').nth(1).fill('Salt Witch')
  await axis.getByLabel('character entity descriptor').nth(1).fill('a salt witch')

  // Variation: camera with two pieces.
  await page.getByRole('button', { name: '+ Add variation' }).click()
  await page.getByLabel('Variation name').fill('camera')
  await page.getByLabel('Variation name').blur()
  await page.getByLabel('camera pieces').fill('low angle\nwide shot')

  // Scenario: one folder per character, two images each.
  await page.getByRole('button', { name: '+ Add scenario' }).click()
  const scenario = page.getByTestId('scenario-0')
  await scenario.getByRole('button', { name: '+ Combine an axis' }).click()
  await scenario.getByLabel('Images per folder').fill('2')
  // A blank path and template show the defaults the server would use.
  await expect(scenario.getByLabel('Folder path')).toHaveAttribute('placeholder', '{character}')
  await expect(scenario.getByLabel('Prompt template')).toHaveAttribute('placeholder', '{character}, {camera}')
  await scenario.getByLabel('Folder path').fill('knights/{character}')
  await scenario.getByLabel('Prompt template').fill('{character}, {camera}')

  await expect(page.getByTestId('blueprint-totals')).toHaveText('2 folders · 4 rows to add · 4 images short', { timeout: 15_000 })
  const apply = page.getByRole('button', { name: 'Apply', exact: true })
  await expect(apply).toBeEnabled()
  await apply.click()

  const rowsUrl = `/api/v1/comfy/plans/${plan.id}/rows`
  type LeafRow = Row & { leaf_id: string | null }
  await expect
    .poll(async () => ((await (await request.get(rowsUrl)).json()) as LeafRow[]).length)
    .toBe(4)
  const rows: LeafRow[] = await (await request.get(rowsUrl)).json()
  const bySub: Record<string, number> = {}
  for (const r of rows) {
    bySub[r.subfolder] = (bySub[r.subfolder] ?? 0) + 1
    expect(r.leaf_id).not.toBeNull()
  }
  expect(bySub).toEqual({ 'knights/iron_knight': 2, 'knights/salt_witch': 2 })

  // The rows are ordinary rows: they run.
  await page.getByRole('button', { name: 'Rows', exact: true }).click()
  await page.getByRole('button', { name: 'Run pending (4)' }).click()
  await expect(page.getByTestId('comfy-rows-table').getByText('completed', { exact: true })).toHaveCount(4, { timeout: 30_000 })

  // Top up: everything is full, so it is disabled.
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()
  await expect(page.getByTestId('blueprint-totals')).toContainText('0 images short', { timeout: 15_000 })
  const topUp = page.getByTestId('blueprint-topup')
  await expect(topUp).toBeDisabled()

  // Delete one generated image through the API; the folder is short again.
  const done: LeafRow[] = await (await request.get(rowsUrl)).json()
  const imageIds = done.flatMap((r) => r.image_ids)
  expect(imageIds).toHaveLength(4)
  const del = await request.delete(`/api/v1/images/${imageIds[0]}`)
  expect(del.status(), await del.text()).toBeLessThan(300)
  // Reopen the tab: the preview refetches on mount.
  await page.getByRole('button', { name: 'Rows', exact: true }).click()
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()
  await expect(page.getByTestId('blueprint-totals')).toContainText('1 image short', { timeout: 15_000 })
  await expect(topUp).toBeEnabled()
  await topUp.click()

  await expect
    .poll(async () => ((await (await request.get(rowsUrl)).json()) as LeafRow[]).map((r) => r.status), { timeout: 30_000 })
    .toEqual(['completed', 'completed', 'completed', 'completed', 'completed'])
  const images = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(images).toHaveLength(4)
})

test('the Blueprint tab opens the draft-from-a-description dialog', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-outline-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'outline plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()
  await page.getByTestId('outline-open').click()
  // No LLM is called: the dialog shows the provider picker, or says none is configured.
  await expect(page.getByTestId('outline-modal')).toBeVisible()
  await expect(page.getByTestId('outline-modal').getByText(/Provider|No LLM providers/).first()).toBeVisible()
  await expect(page.getByTestId('blueprint-draft-banner')).toHaveCount(0)
})
