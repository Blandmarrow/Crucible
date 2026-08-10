import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// `POST /captioning/run` and `/pipeline` 422 any model id whose prefix reaches no
// captioning backend, and a pipeline is one request — so a stale step 3 stops step 1
// from running and the error names no step. The page has to refuse the Run itself.
//
// Pure client state: no GPU, no job, no model download. The gate is decided before
// anything is sent, so seeding the persisted workflow blob is the whole setup.
//
// `loadPersisted` shallow-merges at the top level only, so a seeded `additionalSteps`
// entry has to be a COMPLETE StepConfig — `delimiterParts` is `.join()`ed and
// `wd14Threshold` is `.toFixed(2)`ed during render, and a partial object throws.
function step(model: string) {
  return {
    id: 'seeded',
    model,
    style: 'detailed',
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

test('a pipeline step holding a non-captioning model blocks the run and names itself', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `run-gate-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  // `sam3` is a detector: it is in the model registry, so it could be selected here
  // before the picker was filtered, and it survives in anyone's saved workflow.
  await page.addInitScript(
    ({ key, blob }) => localStorage.setItem(key, blob),
    {
      key: 'captioning-workflow-config',
      blob: JSON.stringify({ selectedModel: 'florence2_large', additionalSteps: [step('sam3')] }),
    },
  )

  await page.goto(`/datasets/${ds.id}/captioning`)

  const run = page.getByRole('button', { name: /Run Pipeline/ })
  await expect(run).toBeDisabled()
  await expect(run).toHaveAttribute('title', /Step 2.*sam3/)
  await expect(page.getByText(/“sam3” is not a captioning model/)).toBeVisible()
})

// The negative control, and the reason it is not optional: `modelType` returns null
// for `wd14:` (it has no STYLE_LABELS vocabulary) despite wd14 being one of the two
// most-used backends. Gating on that instead of `captionBackend` disables Run for
// every wd14 and openai_compat selection — and without this assertion the suite
// passes an always-disabled implementation.
test('a wd14 tagger leaves the run enabled', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `run-gate-ok-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  await page.addInitScript(
    ({ key, blob }) => localStorage.setItem(key, blob),
    {
      key: 'captioning-workflow-config',
      blob: JSON.stringify({ selectedModel: 'wd14:eva02_large', additionalSteps: [] }),
    },
  )

  await page.goto(`/datasets/${ds.id}/captioning`)

  const run = page.getByRole('button', { name: 'Run captioning' })
  await expect(run).toBeEnabled()
  await expect(page.getByText(/is not a captioning model/)).toHaveCount(0)
})
