import { test, expect } from '@playwright/test'
import type { APIRequestContext, Locator, Page } from '@playwright/test'
import { createDatasetViaApi } from './helpers'

// M6 §8c: the drags themselves. Every one has a button route covered in comfy.spec.ts and
// comfy-library.spec.ts; these check the gestures. dnd-kit listens to pointer events and
// starts a drag only after 8px of travel, so `dragTo` moves in steps, not one jump.

const WORKFLOW = {
  '6': { class_type: 'CLIPTextEncode', inputs: { text: 'template prompt' } },
  '9': { class_type: 'SaveImage', inputs: {} },
}

async function createPlan(request: APIRequestContext, datasetId: string, name: string) {
  const r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: datasetId, name, workflow_json: WORKFLOW, output_node_ids: ['9'],
      pinned_params: [{ node_id: '6', input: 'text', alias: 'prompt', is_prompt: true }],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  return r.json()
}

async function addRows(request: APIRequestContext, planId: string, lines: string[]) {
  const r = await request.post(`/api/v1/comfy/plans/${planId}/rows/bulk`, { data: { lines } })
  expect(r.status(), await r.text()).toBe(200)
}

async function rowsOf(request: APIRequestContext, planId: string) {
  return (await request.get(`/api/v1/comfy/plans/${planId}/rows`)).json()
}

/** Press on `source`, travel past dnd-kit's 8px activation, and release over `target`,
 *  at `dy` from its centre, or `fromTop` px below its top edge. Both must be on screen:
 *  the browser sends no pointer events past the viewport. */
async function dragTo(page: Page, source: Locator, target: Locator, opts: { dy?: number; fromTop?: number } = {}) {
  await target.scrollIntoViewIfNeeded()
  await source.scrollIntoViewIfNeeded()
  const s = (await source.boundingBox())!
  await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2)
  await page.mouse.down()
  await page.mouse.move(s.x + s.width / 2 + 12, s.y + s.height / 2 + 12, { steps: 4 })
  const t = (await target.boundingBox())!
  const y = opts.fromTop !== undefined ? t.y + opts.fromTop : t.y + t.height / 2 + (opts.dy ?? 0)
  await page.mouse.move(t.x + t.width / 2, y, { steps: 12 })
  await page.mouse.move(t.x + t.width / 2, y + 1, { steps: 2 })
  await page.mouse.up()
}

test('a row dragged onto a folder in the tree is filed there', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-dnd-folder-${Date.now()}`)
  const plan = await createPlan(request, ds.id, 'dnd plan')
  await addRows(request, plan.id, ['a knight in a fen', 'a monk on a ridge'])
  const made = await request.post(`/api/v1/comfy/plans/${plan.id}/folders`, { data: { path: 'fen' } })
  expect(made.status(), await made.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(rowsTable.locator('textarea')).toHaveCount(2)
  const grip = rowsTable.getByRole('button', { name: 'Row actions' }).first()
  await dragTo(page, grip, page.getByTestId('comfy-folder-tree').getByRole('button', { name: /^fen/ }))

  await expect
    .poll(async () => (await rowsOf(request, plan.id))
      .map((r: { subfolder: string; values: { prompt: string } }) => [r.values.prompt, r.subfolder]))
    .toEqual([['a monk on a ridge', ''], ['a knight in a fen', 'fen']])
  // A drag that only crossed 8px still opened no menu.
  await expect(page.getByRole('menu')).toHaveCount(0)
})

test('a row dragged below another lands after it', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-dnd-order-${Date.now()}`)
  const plan = await createPlan(request, ds.id, 'order plan')
  await addRows(request, plan.id, ['first', 'second', 'third'])

  await page.goto(`/datasets/${ds.id}/comfy`)
  const rowsTable = page.getByTestId('comfy-rows-table')
  await expect(rowsTable.locator('textarea')).toHaveCount(3)
  const grips = rowsTable.getByRole('button', { name: 'Row actions' })
  // Onto the lower half of the last row's grip cell: after it.
  await dragTo(page, grips.first(), grips.nth(2), { dy: 6 })

  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { values: { prompt: string } }) => r.values.prompt))
    .toEqual(['second', 'third', 'first'])
})

test('a library prompt dragged onto the table becomes a row', async ({ page, request }) => {
  const stamp = `dnd${Date.now()}`
  const ds = await createDatasetViaApi(request, `e2e-dnd-lib-${stamp}`)
  const plan = await createPlan(request, ds.id, 'lib plan')
  await addRows(request, plan.id, ['already here'])
  const saved = await request.post('/api/v1/comfy/library', {
    data: { category: `${stamp}/scenes`, prompts: [`a salt fen at dusk ${stamp}`] },
  })
  expect(saved.status(), await saved.text()).toBe(200)

  try {
    await page.goto(`/datasets/${ds.id}/comfy`)
    await page.getByRole('button', { name: 'Library', exact: true }).click()
    const panel = page.getByTestId('comfy-library-panel')
    await panel.getByRole('searchbox', { name: 'Search the library' }).fill(stamp)
    const card = panel.getByTestId('comfy-lib-prompt').filter({ hasText: stamp })
    await dragTo(page, card.getByRole('button', { name: 'Drag prompt' }), page.getByTestId('comfy-rows-body'), { fromTop: 80 })

    await expect
      .poll(async () => (await rowsOf(request, plan.id)).map((r: { values: { prompt: string } }) => r.values.prompt))
      .toEqual(['already here', `a salt fen at dusk ${stamp}`])
  } finally {
    await request.post('/api/v1/comfy/library/categories/delete', { data: { path: stamp } })
  }
})

test('.txt files dropped from the desktop become rows in the folder in view', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-dnd-txt-${Date.now()}`)
  const plan = await createPlan(request, ds.id, 'txt plan')
  await addRows(request, plan.id, ['already here'])
  const set = await request.post(`/api/v1/comfy/plans/${plan.id}/folders`, { data: { path: 'imports' } })
  expect(set.status(), await set.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByTestId('comfy-folder-tree').getByRole('button', { name: /^imports/ }).click()
  // A native file drop, as a desktop file manager sends it: a DataTransfer holding Files.
  const dt = await page.evaluateHandle(() => {
    const d = new DataTransfer()
    d.items.add(new File(['a fox\nin snow'], 'fox.txt', { type: 'text/plain' }))
    d.items.add(new File(['ignored'], 'notes.md', { type: 'text/markdown' }))
    return d
  })
  const body = page.getByTestId('comfy-rows-body')
  await body.dispatchEvent('dragenter', { dataTransfer: dt })
  await body.dispatchEvent('dragover', { dataTransfer: dt })
  await body.dispatchEvent('drop', { dataTransfer: dt })

  await expect
    .poll(async () => (await rowsOf(request, plan.id))
      .map((r: { subfolder: string; values: { prompt: string } }) => [r.values.prompt, r.subfolder]))
    .toEqual([['already here', ''], ['a fox in snow', 'imports']])
})

// ── Regressions from the §8c review ──────────────────────────────────────────────

test('Shift held before the drag starts makes a drop on a plan a move', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-dnd-shift-${Date.now()}`)
  const source = await createPlan(request, ds.id, 'a source')
  const target = await createPlan(request, ds.id, 'b target')
  await addRows(request, source.id, ['moving row'])

  await page.goto(`/datasets/${ds.id}/comfy`)
  await page.getByTestId('comfy-plan-rail').getByRole('button', { name: /^a source/ }).click()
  const grip = page.getByTestId('comfy-rows-table').getByRole('button', { name: 'Row actions' }).first()
  await page.keyboard.down('Shift')
  await dragTo(page, grip, page.getByTestId('comfy-plan-rail').getByRole('button', { name: /^b target/ }))
  await page.keyboard.up('Shift')

  await expect.poll(async () => (await rowsOf(request, target.id)).length).toBe(1)
  await expect.poll(async () => (await rowsOf(request, source.id)).length).toBe(0)
})

test('rows dragged out of the folder in view leave the selection', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-dnd-sel-${Date.now()}`)
  const plan = await createPlan(request, ds.id, 'sel plan')
  await addRows(request, plan.id, ['one', 'two'])
  for (const path of ['src', 'dst']) {
    const r = await request.post(`/api/v1/comfy/plans/${plan.id}/folders`, { data: { path } })
    expect(r.status(), await r.text()).toBe(200)
  }
  const ids = (await rowsOf(request, plan.id)).map((r: { id: string }) => r.id)
  await request.post(`/api/v1/comfy/plans/${plan.id}/rows/set-subfolder`, { data: { subfolder: 'src', row_ids: ids } })

  await page.goto(`/datasets/${ds.id}/comfy`)
  const tree = page.getByTestId('comfy-folder-tree')
  await tree.getByRole('button', { name: /^src/ }).click()
  const rowsTable = page.getByTestId('comfy-rows-table')
  await rowsTable.locator('input.checkbox').first().check()
  await expect(page.getByTestId('comfy-selection-bar')).toContainText('2 selected')
  await dragTo(page, rowsTable.getByRole('button', { name: 'Row actions' }).first(), tree.getByRole('button', { name: /^dst/ }))

  await expect
    .poll(async () => (await rowsOf(request, plan.id)).map((r: { subfolder: string }) => r.subfolder))
    .toEqual(['dst', 'dst'])
  await expect(page.getByTestId('comfy-selection-bar')).toContainText('Tick rows to move')
})
