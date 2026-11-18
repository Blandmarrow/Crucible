import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The fake OpenAI-compatible LLM (backend/tests/fake_llm.py, served by
// e2e/serve-llm.mjs) end to end: a provider pointed at it lists its models, and a
// caption job through the real OpenAI SDK writes its reply. It is the analyst's (M12)
// test harness; this journey proves the wiring before any analyst spec depends on it.
// Without the `openai` package in the e2e install the caption would come back empty,
// so it also pins that CI installs it.

const LLM = `http://127.0.0.1:${process.env.E2E_LLM_PORT ?? '8197'}`
const FAKE_REPLY = 'A plain test image, described by the fake LLM server.'

type Recorded = { path: string; json: { messages?: { content: unknown }[] } | null }

test('a provider on the fake LLM lists its models and captions an image', async ({ page, request }) => {
  const r = await request.post('/api/v1/providers/', {
    data: { name: `e2e-fake-llm-${Date.now()}`, base_url: `${LLM}/v1`, default_model: 'fake-vision' },
  })
  expect(r.status(), await r.text()).toBeLessThan(300)
  const providerId: string = (await r.json()).id

  try {
    const models = await (await request.get(`/api/v1/providers/${providerId}/models`)).json()
    expect(models.models).toEqual(['fake-chat', 'fake-vision'])

    const ds = await createDatasetViaApi(request, `e2e-fake-llm-${Date.now()}`)
    await uploadViaApi(request, ds.id, 'llm.png')
    const [img] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '1' } })).json()

    // A unique prompt, to find this job's request in the fake's shared record.
    const prompt = `Describe this image. e2e-${Date.now()}`
    const run = await request.post('/api/v1/captioning/run', {
      data: {
        dataset_id: ds.id, image_ids: [img.id], model: `openai_compat:${providerId}:fake-vision`,
        custom_prompt: prompt, overwrite: true,
      },
    })
    expect(run.status(), await run.text()).toBe(200)
    const jobId: string = (await run.json()).job_id

    let job: { status: string; result_data?: Record<string, unknown> } = { status: '' }
    await expect.poll(async () => {
      job = await (await request.get(`/api/v1/jobs/${jobId}`)).json()
      return job.status
    }, { timeout: 30_000 }).toBe('completed')
    expect(job.result_data?.failed_count ?? 0, JSON.stringify(job.result_data)).toBe(0)

    // What the fake received: the prompt, and the image beside it.
    const recorded: Recorded[] = await (await request.get(`${LLM}/_fake/requests`)).json()
    const ours = recorded.filter((q) => JSON.stringify(q.json ?? {}).includes(prompt))
    expect(ours).toHaveLength(1)
    expect(ours[0].path).toBe('/v1/chat/completions')
    expect(JSON.stringify(ours[0].json?.messages)).toContain('data:image/jpeg;base64,')

    // And the caption is on the image's page.
    await page.goto(`/datasets/${ds.id}/gallery`)
    await page.getByTestId('gallery-tile').first().click()
    await expect(page).toHaveURL(new RegExp(`/image/${img.id}$`))
    await expect(page.getByPlaceholder('Natural language description', { exact: false })).toHaveValue(FAKE_REPLY)
  } finally {
    await request.delete(`/api/v1/providers/${providerId}`)
  }
})
