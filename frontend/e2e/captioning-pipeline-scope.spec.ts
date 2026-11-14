import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { createDatasetViaApi, resetCaptioningWorkflowViaApi, uploadViaApi } from './helpers'

// PM-038: a pipeline started with "Uncaptioned only" recaptioned every image. The route
// now reads one request-level `overwrite`, and the page must send it the way its
// single-step `/run` does — `scope !== "uncaptioned"`. The case that matters most is
// Selected: step 1 used to carry `scope === "all"`, which read as-is would have skipped
// selected images that already had a caption. The route's own filter is pinned in
// backend/tests/test_pipeline_scope.py; here only the request is checked, so the
// pipeline is captured and answered without running a job.

// A COMPLETE StepConfig: the page shallow-merges the saved workflow at the top level
// only, and renders fields of each step (see captioning-run-gate.spec.ts).
function step(model: string) {
  return {
    id: 'seeded',
    model,
    style: 'tags',
    customPrompt: '',
    wd14Threshold: 0.35,
    providerModelInput: '',
    delimiterMode: 'overwrite',
    delimiterParts: [',', ' '],
    usePreviousCaption: false,
    stripRefusals: true,
    stripThinking: false,
    stripUnderscores: false,
    stripHedges: false,
    dedupeTags: false,
    targetWidth: null,
    targetHeight: null,
  }
}

const MODEL = 'wd14:eva02_large'

async function seedWorkflow(request: APIRequestContext, scope: string) {
  const r = await request.put('/api/v1/app-data/captioning-workflow', {
    data: { value: { selectedModel: MODEL, scope, additionalSteps: [step(MODEL)] } },
  })
  expect(r.status(), await r.text()).toBe(200)
}

/** Capture the pipeline request and answer "nothing to do", so no job runs. */
async function capturePipeline(page: Page): Promise<() => Record<string, unknown> | null> {
  let body: Record<string, unknown> | null = null
  await page.route('**/api/v1/captioning/pipeline', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { job_id: null, message: 'No images to caption' } })
  })
  return () => body
}

async function runPipeline(page: Page) {
  await page.getByRole('button', { name: /Run Pipeline/ }).click()
  await expect(page.getByText('No images to caption', { exact: true })).toBeVisible()
}

test.afterEach(async ({ request }) => {
  await resetCaptioningWorkflowViaApi(request)
})

for (const [scope, expected] of [['uncaptioned', false], ['all', true]] as const) {
  test(`a pipeline on the ${scope} scope sends overwrite: ${expected}`, async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `pipeline-scope-${scope}-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'a.png')
    await seedWorkflow(request, scope)
    const sent = await capturePipeline(page)

    await page.goto(`/datasets/${ds.id}/captioning`)
    await runPipeline(page)

    expect(sent()?.overwrite).toBe(expected)
    // The old per-step copy is gone, so nothing can read it again.
    for (const s of sent()?.steps as Record<string, unknown>[]) expect(s).not.toHaveProperty('overwrite')
  })
}

test('a pipeline on the selected scope recaptions the selection', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `pipeline-scope-selected-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')
  const [img] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  // A captioned image, the kind the old `scope === "all"` would have dropped. The request
  // is answered here, so it only documents the case; the backend's side of it is
  // `test_overwrite_true_recaptions_captioned_ids` in backend/tests/test_pipeline_scope.py.
  const r = await request.put(`/api/v1/captions/image/${img.id}`, { data: { caption_text: 'already captioned', caption_style: 'tags' } })
  expect(r.ok()).toBeTruthy()
  await seedWorkflow(request, 'selected')
  const sent = await capturePipeline(page)

  // The selection lives in memory, so it is made in the gallery and carried to the
  // Captioning page by in-app navigation rather than a fresh load.
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('select-all-btn').click()
  await expect(page.getByText('2 selected')).toBeVisible()
  await page.getByRole('link', { name: 'Captioning', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/datasets/${ds.id}/captioning$`))

  await runPipeline(page)

  expect(sent()?.overwrite).toBe(true)
  expect(sent()?.image_ids).toHaveLength(2)
})

// The scope is saved on the server but the selection is not, so a fresh load leaves
// Selected pointing at nothing. The page used to send `image_ids: []`, which `/run` and
// `/pipeline` widen to the whole dataset with overwrite on. Run (and the Florence-2 detect
// run, which shares the check) must be blocked instead, and only by the scope.
test('the selected scope with nothing selected blocks Run', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `scope-empty-selection-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  const r = await request.put('/api/v1/app-data/captioning-workflow', {
    data: { value: { selectedModel: 'florence2_large', scope: 'selected' } },
  })
  expect(r.status(), await r.text()).toBe(200)
  let posted = false
  await page.route('**/api/v1/captioning/run', async (route) => {
    posted = true
    await route.fulfill({ json: { job_id: null, message: 'No images to caption' } })
  })

  await page.goto(`/datasets/${ds.id}/captioning`)

  const run = page.getByRole('button', { name: 'Run captioning' })
  await expect(run).toBeDisabled()
  await expect(run).toHaveAttribute('title', /Nothing selected in this dataset/)
  await expect(page.getByTestId('scope-blocked-note')).toBeVisible()
  const detect = page.getByRole('button', { name: 'Run Detection' })
  await expect(detect).toBeDisabled()
  await expect(detect).toHaveAttribute('title', /Nothing selected in this dataset/)

  // Another scope lifts the block, so it came from the scope alone.
  await page.getByRole('radio', { name: /Uncaptioned only/ }).check()
  await expect(run).toBeEnabled()
  await expect(page.getByTestId('scope-blocked-note')).toHaveCount(0)
  await expect(detect).not.toHaveAttribute('title', /Nothing selected/)
  expect(posted).toBe(false)
})
