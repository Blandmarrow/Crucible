import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createDatasetViaApi, setComfyUrlViaApi } from './helpers'

// The automated run (M6 §4) on the Runs page, against the fake ComfyUI that
// e2e/serve-comfy.mjs serves: a hand-written blueprint is filled, generated and
// exported in one press. Score and caption stay off — e2e is torch-free.

const WORKFLOW = {
  '4': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'sdxl.safetensors' } },
  '6': { class_type: 'CLIPTextEncode', inputs: { text: 'template prompt', clip: ['4', 1] } },
  '8': { class_type: 'EmptyImage', inputs: { width: 64, height: 64, batch_size: 1, color: 0 } },
  '9': { class_type: 'SaveImage', inputs: { images: ['8', 0] } },
}

const SPEC = {
  axes: {
    character: [
      { id: 'k', label: 'Iron Knight', descriptor: 'an iron knight' },
      { id: 'w', label: 'Salt Witch', descriptor: 'a salt witch' },
    ],
  },
  variations: { camera: ['low angle', 'wide shot'] },
  scenarios: [{ name: 'solo', combine: ['character'], count: 1, path: 'knights/{character}', template: '{character}, {camera}' }],
}

test.beforeEach(async ({ request }) => {
  await setComfyUrlViaApi(request)
  // The start form is remembered on the server (M6 §6), shared by every test here.
  await request.delete('/api/v1/app-data/run-form')
})

test.afterEach(async ({ page, request }) => {
  // Leaving the page flushes the form's last save, so the delete after it wins.
  await page.goto('about:blank')
  await request.delete('/api/v1/app-data/run-form')
  await setComfyUrlViaApi(request, '')
})

test('a run fills the blueprint, generates and exports in one press', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-run-${Date.now()}`)
  let r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id, name: 'run plan', workflow_json: WORKFLOW,
      pinned_params: [{ node_id: '6', input: 'text', alias: 'prompt', is_prompt: true }],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  const plan = await r.json()
  r = await request.put(`/api/v1/comfy/plans/${plan.id}/blueprint`, { data: { spec: SPEC, seed: 3 } })
  expect(r.status(), await r.text()).toBe(200)

  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'crucible-e2e-run-'))

  await page.goto(`/datasets/${ds.id}/runs`)
  await expect(page.getByText('This plan has a blueprint', { exact: false })).toBeVisible()
  await page.getByRole('checkbox', { name: 'Score', exact: true }).uncheck()
  await page.getByRole('checkbox', { name: 'Export', exact: true }).check()
  // "Fill from the Export page" copies what that page saved in this browser.
  await page.evaluate((dir) => {
    localStorage.setItem('export-workflow-config', JSON.stringify({ format: 'plain', outputDir: dir }))
  }, outDir)
  await page.getByRole('button', { name: 'Fill from the Export page' }).click()
  await expect(page.getByPlaceholder('Output folder (absolute path)')).toHaveValue(outDir)
  await expect(page.getByRole('button', { name: 'plain folder' })).toHaveClass(/primary/)
  await page.getByPlaceholder('Run name (optional)').fill('e2e run')
  await page.getByRole('button', { name: 'Start run' }).click()

  // The run moves to Past runs once its last stage is done.
  const history = page.locator('.panel', { hasText: 'Past runs' })
  await expect(history.getByText('completed', { exact: true })).toBeVisible({ timeout: 60_000 })
  await history.getByRole('button', { name: '▸' }).click()
  await expect(history.getByText('2 rows generated')).toBeVisible()

  const runs = await (await request.get('/api/v1/runs/', { params: { dataset_id: ds.id } })).json()
  expect(runs[0].status).toBe('completed')
  expect(runs[0].stages.map((s: { status: string }) => s.status)).toEqual(['done', 'done', 'done', 'done', 'done'])

  const images = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  expect(images.map((i: { subfolder: string }) => i.subfolder).sort()).toEqual(['knights/iron_knight', 'knights/salt_witch'])
  expect(fs.readdirSync(path.join(outDir, 'images')).filter((f) => f.endsWith('.png'))).toHaveLength(2)

  // "Use these settings" (M6 §6) fills the start form from the past run's recipe.
  await page.getByRole('checkbox', { name: 'Export', exact: true }).uncheck()
  await history.getByRole('button', { name: 'Use these settings' }).click()
  await expect(page.getByRole('checkbox', { name: 'Export', exact: true })).toBeChecked()
  await expect(page.getByPlaceholder('Output folder (absolute path)')).toHaveValue(outDir)
  await expect(page.getByRole('checkbox', { name: 'Score', exact: true })).not.toBeChecked()
  fs.rmSync(outDir, { recursive: true, force: true })
})

// The recipe library (M6 §6): a form saved as a recipe and loaded back sends the same
// recipe — the round trip through every lossy converter — and its export file
// carries neither this machine's export folder nor its provider ids.
test('a recipe saves, loads back to the same run, and exports without local paths', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-recipe-${Date.now()}`)
  let r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id, name: 'recipe plan', workflow_json: { ...WORKFLOW, '3': { class_type: 'KSampler', inputs: { seed: 1, steps: 4 } } },
      pinned_params: [
        { node_id: '6', input: 'text', alias: 'prompt', is_prompt: true },
        { node_id: '3', input: 'seed', alias: 'seed', is_prompt: false, per_row: false, value: null, int_mode: 'random' },
      ],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  const plan = await r.json()
  r = await request.put(`/api/v1/comfy/plans/${plan.id}/blueprint`, { data: { spec: SPEC, seed: 3 } })
  expect(r.status(), await r.text()).toBe(200)
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'crucible-e2e-recipe-'))
  const recipeName = `e2e recipe ${Date.now()}`

  await page.goto(`/datasets/${ds.id}/runs`)
  await expect(page.getByText('This plan has a blueprint', { exact: false })).toBeVisible()
  // Away from the defaults in every section the form converts.
  await page.getByRole('checkbox', { name: 'Snapshot the dataset' }).check()
  await page.getByRole('checkbox', { name: 'Gate', exact: true }).check()
  await page.getByPlaceholder('Base folder (optional)').fill('gen/base')
  await page.locator('select').filter({ hasText: 'LAION' }).selectOption('v2_5')
  await page.getByRole('checkbox', { name: 'Repeat until every folder is full' }).check()
  await page.getByLabel('Most passes').fill('3')
  await page.getByRole('checkbox', { name: 'Export', exact: true }).check()
  await page.getByPlaceholder('Output folder (absolute path)').fill(outDir)
  await page.getByRole('button', { name: 'Custom', exact: true }).click()
  await page.getByPlaceholder('e.g. 1536').fill('1536')

  const dryBody = async () => {
    const req = page.waitForRequest((q) => q.url().includes('/runs/dry-run'))
    await page.getByRole('button', { name: 'Dry run' }).click()
    return (await req).postDataJSON().recipe
  }
  const before = await dryBody()
  expect(before.stages).toContain('snapshot')
  expect(before.export.resize_to).toBe(1536)

  await page.getByLabel('Recipe name').fill(recipeName)
  await page.getByRole('button', { name: 'Save as recipe' }).click()
  await expect(page.getByText(`Saved the recipe "${recipeName}"`)).toBeVisible()

  // Change the form, then load the recipe back: the same recipe goes out.
  await page.getByRole('checkbox', { name: 'Snapshot the dataset' }).uncheck()
  await page.getByPlaceholder('Base folder (optional)').fill('elsewhere')
  await page.getByRole('checkbox', { name: 'Repeat until every folder is full' }).uncheck()
  await page.getByLabel('Saved recipes').selectOption({ label: recipeName })
  await page.getByRole('button', { name: 'Load', exact: true }).click()
  await expect(page.getByText(`Filled the form from the recipe "${recipeName}"`)).toBeVisible()
  expect(await dryBody()).toEqual(before)

  // Export: no export folder, no provider id.
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  const file = await (await download).path()
  const doc = JSON.parse(fs.readFileSync(file!, 'utf8'))
  expect(doc.format).toBe('crucible.run-recipe')
  expect(JSON.stringify(doc)).not.toContain(outDir.replace(/\\/g, '\\\\'))
  expect(doc.recipe.export.output_dir).toBe('')

  // Import it back: saved under a numbered name, loaded, and the folder asked for again.
  await page.getByTestId('recipe-import-input').setInputFiles({
    name: 'r.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(doc)),
  })
  await expect(page.getByText(`Imported the recipe "${recipeName} (2)"`)).toBeVisible()
  await expect(page.getByText('Choose an export folder', { exact: false }).first()).toBeVisible()
  await expect(page.getByPlaceholder('Output folder (absolute path)')).toHaveValue('')
  fs.rmSync(outDir, { recursive: true, force: true })
})

// The dry run (M6 §6): the same checks the start runs, and the first pass's rows,
// with nothing written — no run, and the plan still has no rows.
test('a dry run reports the first pass and writes nothing', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-dry-${Date.now()}`)
  let r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id, name: 'dry plan', workflow_json: WORKFLOW,
      pinned_params: [{ node_id: '6', input: 'text', alias: 'prompt', is_prompt: true }],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  const plan = await r.json()
  r = await request.put(`/api/v1/comfy/plans/${plan.id}/blueprint`, { data: { spec: SPEC, seed: 3 } })
  expect(r.status(), await r.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/runs`)
  await expect(page.getByText('This plan has a blueprint', { exact: false })).toBeVisible()
  await page.getByRole('checkbox', { name: 'Score', exact: true }).uncheck()
  await page.getByRole('button', { name: 'Dry run' }).click()

  const report = page.getByTestId('dry-run-report')
  await expect(report.getByText('Ready to start')).toBeVisible()
  await expect(report.getByText('First pass: 2 rows to generate', { exact: false })).toBeVisible()
  await expect(report.getByText('knights/iron_knight: 0 of 1 kept', { exact: false })).toBeVisible()
  // Changing the form marks the report stale.
  await page.getByPlaceholder('Run name (optional)').fill('changed')
  await expect(report.getByText('The form has changed since', { exact: false })).toBeVisible()

  const runs = await (await request.get('/api/v1/runs/', { params: { dataset_id: ds.id } })).json()
  expect(runs).toEqual([])
  const rows = await (await request.get(`/api/v1/comfy/plans/${plan.id}/rows`)).json()
  expect(rows).toHaveLength(0)
})

// The remembered form (M6 §6): what was left on the form is there after a reload, and
// a remembered plan id this dataset no longer has falls back to its first plan.
test('the start form is remembered, and a stale plan falls back', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-remember-${Date.now()}`)
  const r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id, name: 'kept plan', workflow_json: WORKFLOW,
      pinned_params: [{ node_id: '6', input: 'text', alias: 'prompt', is_prompt: true }],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  // A plan id from before: gone, so the form must not send it.
  await request.put('/api/v1/app-data/run-form', {
    data: { value: { v: 1, form: { stages: ['generate'] }, byDataset: { [ds.id]: { planId: 'gone', subfolder: 'old' } } } },
  })

  await page.goto(`/datasets/${ds.id}/runs`)
  await expect(page.locator('select').filter({ hasText: 'kept plan' })).toHaveValue((await r.json()).id)
  await expect(page.getByPlaceholder('Base folder (optional)')).toHaveValue('old')
  await expect(page.getByRole('checkbox', { name: 'Fill the blueprint' })).not.toBeChecked()

  const saved = page.waitForResponse((res) => res.url().includes('/app-data/run-form') && res.request().method() === 'PUT')
  await page.getByRole('checkbox', { name: 'Snapshot the dataset' }).check()
  await page.getByPlaceholder('Base folder (optional)').fill('kept/folder')
  await saved
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Snapshot the dataset' })).toBeChecked()
  await expect(page.getByPlaceholder('Base folder (optional)')).toHaveValue('kept/folder')
})

// The goal loop (M6 §5) from the form: with Score and the gate off nothing is
// rejected, so the first pass fills every folder and the run says so. The gate's
// own logic is the backend's to test (test_run_loop.py), which can score images.
test('a repeating run stops after the pass that fills every folder', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-loop-${Date.now()}`)
  const workflow = { ...WORKFLOW, '3': { class_type: 'KSampler', inputs: { seed: 1, steps: 4 } } }
  let r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id, name: 'loop plan', workflow_json: workflow,
      pinned_params: [
        { node_id: '6', input: 'text', alias: 'prompt', is_prompt: true },
        // A repeating run refuses a seed that is not random.
        { node_id: '3', input: 'seed', alias: 'seed', is_prompt: false, per_row: false, value: null, int_mode: 'random' },
      ],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  const plan = await r.json()
  r = await request.put(`/api/v1/comfy/plans/${plan.id}/blueprint`, { data: { spec: SPEC, seed: 3 } })
  expect(r.status(), await r.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/runs`)
  await expect(page.getByText('This plan has a blueprint', { exact: false })).toBeVisible()
  await page.getByRole('checkbox', { name: 'Score', exact: true }).uncheck()
  // The snapshot (M6 §6) is a head stage: once, before the first pass. Versioning is
  // off on a fresh install, so it is skipped with a note and the run goes on.
  await page.getByRole('checkbox', { name: 'Snapshot the dataset' }).check()
  await page.getByRole('checkbox', { name: 'Repeat until every folder is full' }).check()
  await expect(page.getByLabel('Most passes')).toHaveValue('5')
  await page.getByPlaceholder('Run name (optional)').fill('e2e loop')
  await page.getByRole('button', { name: 'Start run' }).click()

  const history = page.locator('.panel', { hasText: 'Past runs' })
  await expect(history.getByText('completed', { exact: true })).toBeVisible({ timeout: 60_000 })
  await history.getByRole('button', { name: '▸' }).click()
  await expect(history.getByText('Before the first pass', { exact: true })).toBeVisible()
  await expect(history.getByText('Versioning is off', { exact: false })).toBeVisible()
  await expect(history.getByText('Pass 1', { exact: true })).toBeVisible()
  await expect(history.getByText('Every folder is full (2).')).toBeVisible()

  const runs = await (await request.get('/api/v1/runs/', { params: { dataset_id: ds.id } })).json()
  expect(runs[0].outcome).toEqual({ outcome: 'met', reason: null })
  expect(runs[0].stages.map((s: { key: string }) => s.key)).toEqual(
    ['snapshot', 'prompts', 'handoff_to_comfy', 'generate', 'handoff_to_local', 'next_pass'],
  )
  expect(runs[0].stages[0]).toMatchObject({ status: 'skipped', pass_no: null })
})

// Carrying on past a cap (M6 §6): an image cap of 1 stops a two-folder loop after one
// image; raising it from the past run's dialog adds a pass that fills the other folder.
test('a run stopped by its image cap carries on once the cap is raised', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extend-${Date.now()}`)
  const workflow = { ...WORKFLOW, '3': { class_type: 'KSampler', inputs: { seed: 1, steps: 4 } } }
  let r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: ds.id, name: 'extend plan', workflow_json: workflow,
      pinned_params: [
        { node_id: '6', input: 'text', alias: 'prompt', is_prompt: true },
        { node_id: '3', input: 'seed', alias: 'seed', is_prompt: false, per_row: false, value: null, int_mode: 'random' },
      ],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  const plan = await r.json()
  r = await request.put(`/api/v1/comfy/plans/${plan.id}/blueprint`, { data: { spec: SPEC, seed: 3 } })
  expect(r.status(), await r.text()).toBe(200)
  r = await request.post('/api/v1/runs/', {
    data: {
      dataset_id: ds.id, plan_id: plan.id, name: 'capped',
      recipe: {
        stages: ['prompts', 'generate'], score: { run_aesthetic: false, aesthetic_model: 'laion' },
        generate: { subfolder: '', set_caption: true }, caption: null, export: null,
        loop: { max_passes: 5, max_images: 1, max_minutes: 60, max_paid_calls: null },
      },
    },
  })
  expect(r.status(), await r.text()).toBe(200)

  await page.goto(`/datasets/${ds.id}/runs`)
  const history = page.locator('.panel', { hasText: 'Past runs' })
  await history.getByRole('button', { name: 'Raise caps and continue' }).click({ timeout: 60_000 })
  const dialog = page.getByRole('dialog', { name: 'Raise the caps and carry on' })
  await expect(dialog.getByText('(stopped the run)')).toBeVisible()
  await dialog.getByLabel('Most images').fill('2')
  await dialog.getByLabel('Most images').blur()
  await dialog.getByRole('button', { name: 'Carry on' }).click()
  await expect(page.getByText('The run carries on')).toBeVisible()

  // The past run already read "completed" before it carried on, so wait on the outcome.
  const listRuns = async () => (await request.get('/api/v1/runs/', { params: { dataset_id: ds.id } })).json()
  await expect.poll(async () => (await listRuns())[0].outcome?.outcome, { timeout: 60_000 }).toBe('met')
  const runs = await listRuns()
  expect(runs[0].status).toBe('completed')
  expect(runs[0].outcome).toEqual({ outcome: 'met', reason: null })
  expect(runs[0].recipe.loop.max_images).toBe(2)
  expect(runs[0].can_extend).toBe(false)
})
