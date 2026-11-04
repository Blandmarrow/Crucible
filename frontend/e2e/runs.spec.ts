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
})

test.afterEach(async ({ request }) => {
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
  fs.rmSync(outDir, { recursive: true, force: true })
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
  await page.getByRole('checkbox', { name: 'Repeat until every folder is full' }).check()
  await expect(page.getByLabel('Most passes')).toHaveValue('5')
  await page.getByPlaceholder('Run name (optional)').fill('e2e loop')
  await page.getByRole('button', { name: 'Start run' }).click()

  const history = page.locator('.panel', { hasText: 'Past runs' })
  await expect(history.getByText('completed', { exact: true })).toBeVisible({ timeout: 60_000 })
  await history.getByRole('button', { name: '▸' }).click()
  await expect(history.getByText('Pass 1', { exact: true })).toBeVisible()
  await expect(history.getByText('Every folder is full (2).')).toBeVisible()

  const runs = await (await request.get('/api/v1/runs/', { params: { dataset_id: ds.id } })).json()
  expect(runs[0].outcome).toEqual({ outcome: 'met', reason: null })
  expect(runs[0].stages.map((s: { key: string }) => s.key)).toEqual(
    ['prompts', 'handoff_to_comfy', 'generate', 'handoff_to_local', 'next_pass'],
  )
})
