import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { addMenu, createDatasetViaApi, setComfyUrlViaApi } from './helpers'

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
  await addMenu(page, 'Paste prompts…')
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

test('the gallery card opens generation info the list no longer carries', async ({ page, request }) => {
  // The gallery list says only whether an image has generation metadata; the
  // modal fetches the JSON from the image's own endpoint (M9 stage 11).
  const ds = await createDatasetViaApi(request, `e2e-comfy-genmeta-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'genmeta plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await pastePrompts(page, ['a lighthouse at dusk'])
  await page.getByRole('button', { name: 'Run pending (1)' }).click()
  await expect(page.getByTestId('comfy-rows-table').getByText('completed', { exact: true }))
    .toHaveCount(1, { timeout: 30_000 })

  const [item] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(item.has_generation_metadata).toBe(true)
  expect(item).not.toHaveProperty('generation_metadata')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('gallery-tile').first().getByTitle('View generation info').click()
  // The metadata panel's prompt paragraph: the card's caption line is a div.
  await expect(page.locator('p', { hasText: /^a lighthouse at dusk$/ })).toBeVisible()
  await expect(page.locator('.badge-info', { hasText: /^ComfyUI$/ })).toBeVisible()
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

test('Reset failed clears the failed dot beside the plan in the rail', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-faildot-${Date.now()}`)
  await createPlanViaApi(request, ds.id, 'dot plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await pastePrompts(page, [`this one breaks ${FAIL_MARKER}`])
  await page.getByRole('button', { name: 'Run pending (1)' }).click()

  const dot = page.getByTestId('comfy-plan-rail').getByRole('img', { name: '1 failed' })
  await expect(dot).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Reset failed (1)' }).click()
  await expect(page.getByTestId('comfy-rows-table').getByText('pending', { exact: true })).toHaveCount(1)
  await expect(dot).toHaveCount(0)
})

test('while a run is live, the rail Delete and the bar Delete are off, and come back after', async ({ page, request }) => {
  // The fake answers one poll a second per row, so eight rows keep the run live for
  // several seconds: long enough to open both menus and read them.
  test.setTimeout(120_000)
  const ds = await createDatasetViaApi(request, `e2e-comfy-livedel-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'live plan')
  const added = await request.post(`/api/v1/comfy/plans/${plan.id}/rows/bulk`, {
    data: { lines: Array.from({ length: 8 }, (_, i) => `live row ${i}`) },
  })
  expect(added.status(), await added.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(rowsTable.locator('textarea')).toHaveCount(8)
  await rowsTable.locator('input.checkbox').nth(8).check()
  const barDelete = page.getByTestId('comfy-selection-bar').getByRole('button', { name: 'Delete', exact: true })
  await expect(barDelete).not.toHaveAttribute('aria-disabled', 'true')

  await page.getByRole('button', { name: 'Run pending (8)' }).click()
  await expect(barDelete).toHaveAttribute('aria-disabled', 'true')
  await expect(barDelete).toHaveAttribute('title', /run or a job/)

  await page.getByTestId('comfy-plan-rail').getByRole('button', { name: 'Plan actions for live plan' }).click()
  const railDelete = page.getByRole('menuitem', { name: 'Delete…' })
  await expect(railDelete).toHaveAttribute('aria-disabled', 'true')
  await expect(railDelete).toHaveAttribute('title', /Wait for this plan/)
  await page.keyboard.press('Escape')

  // The server agrees with the page.
  const refused = await request.delete(`/api/v1/comfy/plans/${plan.id}`)
  expect(refused.status()).toBe(409)

  // Once the run ends both come back.
  await expect(page.getByRole('button', { name: 'Run pending (0)' })).toBeVisible({ timeout: 90_000 })
  await expect(barDelete).not.toHaveAttribute('aria-disabled', 'true')
  await page.getByTestId('comfy-plan-rail').getByRole('button', { name: 'Plan actions for live plan' }).click()
  await expect(page.getByRole('menuitem', { name: 'Delete…' })).not.toHaveAttribute('aria-disabled', 'true')
})

// The user's ruling, 2026-11-07: a menu whose entries are all disabled (a folder's while
// its plan runs) still takes focus, so the arrows move and each entry's reason is reachable.
test('a folder menu that is all disabled during a run is still navigable from the keyboard', async ({ page, request }) => {
  test.setTimeout(120_000)
  const ds = await createDatasetViaApi(request, `e2e-comfy-menukeys-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'menu keys plan')
  const added = await request.post(`/api/v1/comfy/plans/${plan.id}/rows/bulk`, {
    data: { lines: Array.from({ length: 8 }, (_, i) => `fen row ${i}`), subfolder: 'fen' },
  })
  expect(added.status(), await added.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Run pending (8)' }).click()
  const more = page.getByTestId('comfy-folder-tree').getByRole('button', { name: 'Folder actions for fen' })
  // Wait for the lock, so the menu opens with every entry disabled.
  await more.click()
  await expect(page.getByRole('menuitem', { name: 'Rename…' })).toHaveAttribute('aria-disabled', 'true')
  await page.keyboard.press('Escape')
  await expect(more).toBeFocused()

  await page.keyboard.press('Enter')
  const items = page.getByRole('menuitem')
  await expect(items.first()).toBeFocused()
  await expect(items.first()).toHaveAttribute('aria-disabled', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(items.nth(1)).toBeFocused()
  await page.keyboard.press('End')
  await expect(items.last()).toBeFocused()
  // Enter on a disabled entry does nothing: the menu stays open.
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menu')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(more).toBeFocused()
})

// M6 §8d: Run selected moved out of the sticky run bar into the selection bar.
test('Run selected in the selection bar runs only the ticked rows', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-runsel-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'run selected plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await pastePrompts(page, ['the first prompt', 'the second prompt', 'the third prompt'])
  await expect(page.getByTestId('comfy-head').getByRole('button', { name: /^Run selected/ })).toHaveCount(0)

  // Header checkbox first, then one per row in run order.
  const rowsTable = page.getByTestId('comfy-rows-table')
  await rowsTable.locator('input.checkbox').nth(2).check()
  const bar = page.getByTestId('comfy-selection-bar')
  await expect(bar).toContainText('1 selected')
  await bar.getByRole('button', { name: 'Run selected' }).click()

  await expect(rowsTable.getByText('completed', { exact: true })).toHaveCount(1, { timeout: 30_000 })
  await expect(page.getByRole('button', { name: 'Run pending (2)' })).toBeEnabled()
  const rows: Row[] = await (await request.get(`/api/v1/comfy/plans/${plan.id}/rows`)).json()
  expect(rows.map((r) => r.status)).toEqual(['pending', 'completed', 'pending'])
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

  // §8a layout. The run bar sits in the sticky header on this tab too, without Run
  // selected, whose selection is only visible on Rows.
  await expect(page.getByTestId('comfy-head').getByRole('button', { name: /^Run pending/ })).toBeVisible()
  await expect(page.getByTestId('comfy-head').getByRole('button', { name: /^Run selected/ })).toHaveCount(0)
  // A folder's sample prompts open under it on demand.
  const leaves = page.getByTestId('blueprint-leaves')
  await expect(leaves.getByTestId('blueprint-leaf-samples')).toHaveCount(0)
  await leaves.getByRole('button', { name: 'knights/iron_knight' }).click()
  await expect(leaves.getByTestId('blueprint-leaf-samples')).toContainText('an iron knight')
  // A section collapses (hidden, its add button still there) and stays collapsed per plan.
  const jump = page.getByTestId('blueprint-jump')
  await page.getByRole('button', { name: /^Axes/, expanded: true }).click()
  await expect(axis).toBeHidden()
  await expect(page.getByRole('button', { name: '+ Add axis' })).toBeVisible()
  await page.getByRole('button', { name: 'Rows', exact: true }).click()
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()
  // Wait for the form itself: while it loads, the axis is trivially hidden.
  await expect(page.getByRole('button', { name: /^Axes/, expanded: false })).toBeVisible()
  await expect(page.getByTestId('axis-character')).toBeHidden()
  await expect(page.getByRole('button', { name: /^Variations/, expanded: true })).toBeVisible()
  await jump.getByRole('button', { name: 'Expand all' }).click()
  await expect(page.getByTestId('axis-character')).toBeVisible()
  // The jump bar scrolls a section in, below the sticky header, which stays at the top.
  await jump.getByRole('button', { name: 'Scenarios · 1' }).click()
  await expect(page.getByTestId('scenario-0')).toBeInViewport()
  // Scrolled to the bottom, the page is its own scroller and the header is pinned to its top.
  const pinned = await page.getByTestId('comfy-head').evaluate((head) => {
    const scroller = head.parentElement!
    scroller.scrollTop = scroller.scrollHeight
    return {
      scrolled: scroller.scrollTop > 0,
      gap: Math.abs(head.getBoundingClientRect().top - scroller.getBoundingClientRect().top),
    }
  })
  expect(pinned.scrolled).toBe(true)
  expect(pinned.gap).toBeLessThan(1)

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

  // Yield is kept images per completed row (M6 §5): that folder kept 1 of 2 rows,
  // so its one missing image takes two new rows.
  await expect
    .poll(async () => ((await (await request.get(rowsUrl)).json()) as LeafRow[]).map((r) => r.status), { timeout: 30_000 })
    .toEqual(['completed', 'completed', 'completed', 'completed', 'completed', 'completed'])
  const images = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(images).toHaveLength(5)
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

test('a waiting draft can be reviewed, with its expansion, before Use it', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-draft-review-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'draft review plan')
  const draft = {
    axes: {
      character: [
        { id: 'iron', label: 'Iron Knight', descriptor: 'a knight in dented iron plate' },
        { id: 'salt', label: 'Salt Witch', descriptor: 'a witch in a salt-crusted cloak' },
      ],
    },
    variations: { shot: ['low angle', 'wide shot'] },
    scenarios: [{
      name: 'alone', combine: [{ axis: 'character', take: 1 }], mode: 'full', count: 1,
      path: '{character}', template: '{character}, {shot}', rewrite: false,
    }],
  }
  // No LLM in e2e, so the outline job's one output, the saved draft, is added to the
  // blueprint GET. Everything after it is real: the preview of the draft, Use it's PUT
  // and its DELETE of the draft, after which the GET stops carrying one.
  let discarded = false
  await page.route(`**/api/v1/comfy/plans/${plan.id}/blueprint/draft`, async (route) => {
    if (route.request().method() === 'DELETE') discarded = true
    await route.continue()
  })
  await page.route(`**/api/v1/comfy/plans/${plan.id}/blueprint`, async (route) => {
    if (route.request().method() !== 'GET' || discarded) return route.continue()
    const response = await route.fetch()
    // `value` is null until the plan's first blueprint save.
    const value = (await response.json()).value ?? {
      spec: { axes: {}, variations: {}, scenarios: [] }, seed: 7, brief: null, updated_at: new Date().toISOString(),
    }
    await route.fulfill({ response, json: { value: { ...value, draft } } })
  })

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()
  await expect(page.getByTestId('blueprint-draft-banner')).toBeVisible()
  await page.getByTestId('blueprint-draft-review').click()

  const review = page.getByTestId('draft-review')
  await expect(review).toBeVisible()
  await expect(review.getByText('Iron Knight')).toBeVisible()
  await expect(review.getByText('{character}, {shot}')).toBeVisible()
  await expect(page.getByTestId('draft-review-totals')).toContainText('2 folders · 2 rows', { timeout: 15_000 })
  // The review is read-only: the saved spec is still empty.
  const before = (await (await request.get(`/api/v1/comfy/plans/${plan.id}/blueprint`)).json()).value
  expect(Object.keys(before?.spec.axes ?? {})).toHaveLength(0)

  await page.getByTestId('draft-review-use').click()
  await expect(page.getByTestId('outline-modal')).toHaveCount(0)
  await expect(page.getByTestId('blueprint-draft-banner')).toHaveCount(0)
  await expect(page.getByTestId('axis-character')).toBeVisible()
  const after = (await (await request.get(`/api/v1/comfy/plans/${plan.id}/blueprint`)).json()).value
  expect(after.spec.axes.character.map((e: { label: string }) => e.label)).toEqual(['Iron Knight', 'Salt Witch'])
  expect(after.draft).toBeNull()
})

test('Use it on a draft shows the draft pieces for a variation the spec already has', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-draft-pieces-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'draft pieces plan')
  const put = await request.put(`/api/v1/comfy/plans/${plan.id}/blueprint`, {
    data: { spec: { axes: {}, variations: { shot: ['old angle'] }, scenarios: [] }, seed: 7 },
  })
  expect(put.status(), await put.text()).toBe(200)
  const draft = { axes: {}, variations: { shot: ['low angle', 'wide shot'] }, scenarios: [] }
  let discarded = false
  await page.route(`**/api/v1/comfy/plans/${plan.id}/blueprint/draft`, async (route) => {
    if (route.request().method() === 'DELETE') discarded = true
    await route.continue()
  })
  await page.route(`**/api/v1/comfy/plans/${plan.id}/blueprint`, async (route) => {
    if (route.request().method() !== 'GET' || discarded) return route.continue()
    const response = await route.fetch()
    const value = (await response.json()).value
    await route.fulfill({ response, json: { value: { ...value, draft } } })
  })

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()
  await expect(page.getByLabel('shot pieces')).toHaveValue('old angle')
  await page.getByTestId('blueprint-draft-review').click()
  await page.getByTestId('draft-review-use').click()
  await expect(page.getByTestId('blueprint-draft-banner')).toHaveCount(0)
  // The textarea follows the new spec instead of keeping the old text.
  await expect(page.getByLabel('shot pieces')).toHaveValue('low angle\nwide shot')
  // The next keystroke must not save the old pieces over the draft.
  await page.getByLabel('shot pieces').press('End')
  await page.getByLabel('shot pieces').type('!')
  await expect
    .poll(async () => (await (await request.get(`/api/v1/comfy/plans/${plan.id}/blueprint`)).json()).value.spec.variations.shot)
    .toEqual(['low angle', 'wide shot!'])
})

test('a scenario can opt in to the LLM rewrite: Top up waits for it, and the dialog shows the estimate', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-comfy-rewrite-${Date.now()}`)
  const plan = await createPlanViaApi(request, ds.id, 'rewrite plan')

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByRole('button', { name: 'Blueprint', exact: true }).click()

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

  await page.getByRole('button', { name: '+ Add scenario' }).click()
  const scenario = page.getByTestId('scenario-0')
  await scenario.getByRole('button', { name: '+ Combine an axis' }).click()
  await scenario.getByLabel('Images per folder').fill('2')
  await scenario.getByLabel('Folder path').fill('knights/{character}')
  await scenario.getByLabel('Prompt template').fill('{character}, standing')

  // Nothing to rewrite until the scenario opts in.
  const totals = page.getByTestId('blueprint-totals')
  await expect(totals).toHaveText('2 folders · 4 rows to add · 4 images short', { timeout: 15_000 })
  await scenario.getByLabel('Rewrite into prose (LLM)').check()
  await expect(totals).toContainText(/4 to rewrite · ≤ \d+ LLM calls/, { timeout: 15_000 })

  // Before Apply no row exists, so the button is disabled with a reason.
  const rewrite = page.getByTestId('blueprint-rewrite')
  await expect(rewrite).toBeDisabled()
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  const rowsUrl = `/api/v1/comfy/plans/${plan.id}/rows`
  await expect.poll(async () => ((await (await request.get(rowsUrl)).json()) as Row[]).length).toBe(4)
  await expect(rewrite).toBeEnabled({ timeout: 15_000 })
  await expect(rewrite).toHaveText('Rewrite 4 prompts')

  // Top up refuses to run while template prompts wait: it says so and starts nothing.
  await page.getByTestId('blueprint-topup').click()
  await expect(page.getByTestId('blueprint-awaiting-rewrite')).toContainText('4 rows wait for the rewrite')
  const rows: Row[] = await (await request.get(rowsUrl)).json()
  expect(rows.map((r) => r.status)).toEqual(['pending', 'pending', 'pending', 'pending'])

  // No LLM is called: the dialog shows the estimate, or says none is configured.
  await rewrite.click()
  const modal = page.getByTestId('rewrite-modal')
  await expect(modal).toBeVisible()
  await expect(modal.getByText(/No LLM providers|4 prompts in 2 folders/).first()).toBeVisible({ timeout: 15_000 })
})
