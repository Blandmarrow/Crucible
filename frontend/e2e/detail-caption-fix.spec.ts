import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The detail page's "Fix current" mode (#105): the user's note and the current caption go
// to an LLM captioner through `POST /captioning/run`, with the backend filling
// `{previous_caption}` from the stored caption. No real LLM: the run request is captured
// and answered here, so what is pinned is what the page sends, and that an unsaved draft
// is saved first. The backend's substitution is pinned in
// backend/tests/test_previous_caption.py.

const BOX = 'Natural language description'

async function seed(request: APIRequestContext) {
  const ds = await createDatasetViaApi(request, `e2e-fix-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'fix.png')
  const [img] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '1' } })).json()
  const r = await request.put(`/api/v1/captions/image/${img.id}`, {
    data: { caption_text: 'a figure beside broken glass', caption_style: 'detailed' },
  })
  expect(r.ok()).toBeTruthy()
  return { ds, imageId: img.id as string }
}

test.describe('Fix current caption', () => {
  let providerId: string
  let providerName: string

  test.beforeEach(async ({ request }) => {
    // An OpenAI-compatible provider is a model that reads a prompt (`takesPrompt`). Its
    // URL goes nowhere: the run request never reaches the backend.
    providerName = `e2e-fix-provider-${Date.now()}`
    const r = await request.post('/api/v1/providers/', {
      data: { name: providerName, base_url: 'http://127.0.0.1:1/v1', default_model: 'vl' },
    })
    expect(r.status(), await r.text()).toBeLessThan(300)
    providerId = (await r.json()).id
  })

  test.afterEach(async ({ request }) => {
    await request.delete(`/api/v1/providers/${providerId}`)
  })

  test('saves the draft, then sends the note with the caption placeholder', async ({ page, request }) => {
    const { ds, imageId } = await seed(request)

    // The order the two writes reach the network in, and the run's body.
    const order: string[] = []
    page.on('request', (req) => {
      if (req.method() === 'PUT' && req.url().endsWith(`/captions/image/${imageId}`)) order.push('save')
      if (req.method() === 'POST' && req.url().endsWith('/captioning/run')) order.push('run')
    })
    let runBody: Record<string, unknown> | null = null
    await page.route('**/api/v1/captioning/run', async (route) => {
      runBody = route.request().postDataJSON()
      await route.fulfill({ json: { job_id: 'e2e-fix-job', total: 1 } })
    })

    await page.goto(`/datasets/${ds.id}/gallery`)
    await page.getByTestId('gallery-tile').first().click()
    await expect(page).toHaveURL(new RegExp(`/image/${imageId}$`))
    const box = page.getByPlaceholder(BOX, { exact: false })
    await expect(box).toHaveValue('a figure beside broken glass')

    await page.getByRole('button', { name: /Generate with AI/ }).click()
    await page.getByRole('button', { name: 'Fix current' }).click()
    const fix = page.getByRole('button', { name: 'Fix Caption' })
    // No model yet.
    await expect(fix).toBeDisabled()

    await page.getByText(providerName, { exact: true }).click()
    // A model, but no note.
    await expect(fix).toBeDisabled()
    await page.getByLabel("What's wrong with the caption?").fill('The glass is their reflection in a broken mirror.')
    await expect(fix).toBeEnabled()

    // An unsaved edit: the model must see this text, so Fix saves it first.
    await box.fill('a figure beside broken glass, indoors')
    await fix.click()

    await expect.poll(() => order).toEqual(['save', 'run'])
    const stored = await (await request.get(`/api/v1/captions/image/${imageId}`)).json()
    expect(stored.caption_text).toBe('a figure beside broken glass, indoors')

    expect(runBody).not.toBeNull()
    const body = runBody as unknown as Record<string, unknown>
    expect(body.image_ids).toEqual([imageId])
    expect(body.model).toBe(`openai_compat:${providerId}:vl`)
    expect(body.overwrite).toBe(true)
    expect(body.append_tags).toBe(false)
    expect(body.delimiter_mode).toBe('overwrite')
    expect(body.style).toBe('detailed')
    const prompt = body.custom_prompt as string
    // The caption itself is not inlined: the backend fills the placeholder from the
    // stored caption the save just wrote.
    expect(prompt).toContain('{previous_caption}')
    expect(prompt).toContain('The glass is their reflection in a broken mirror.')
    expect(prompt).not.toContain('beside broken glass')
  })

  test('a fix whose one image fails says so, and offers no undo', async ({ page, request }) => {
    // The real job this time: the provider's URL refuses the connection, so the job ends
    // `completed` with `failed_count: 1` — the shape a timeout or a refusal takes (PM-023).
    // How it fails depends on the machine (a refused connection here; an empty caption on
    // CI, whose e2e install has no `openai` package), so the toast expected is whatever
    // the job recorded: its `failure_summary`, or the page's fallback when it has none.
    const { ds, imageId } = await seed(request)
    await page.goto(`/datasets/${ds.id}/gallery`)
    await page.getByTestId('gallery-tile').first().click()
    await expect(page).toHaveURL(new RegExp(`/image/${imageId}$`))

    await page.getByRole('button', { name: /Generate with AI/ }).click()
    await page.getByRole('button', { name: 'Fix current' }).click()
    await page.getByText(providerName, { exact: true }).click()
    await page.getByLabel("What's wrong with the caption?").fill('it is a mirror')
    const started = page.waitForResponse((r) => r.url().endsWith('/captioning/run') && r.request().method() === 'POST')
    await page.getByRole('button', { name: 'Fix Caption' }).click()
    const jobId: string = (await (await started).json()).job_id

    let job: { status: string; result_data?: { failed_count?: number; failure_summary?: string | null } } = { status: '' }
    await expect.poll(async () => {
      job = await (await request.get(`/api/v1/jobs/${jobId}`)).json()
      return job.status
    }, { timeout: 30_000 }).toBe('completed')
    expect(job.result_data?.failed_count).toBe(1)

    const expected = job.result_data?.failure_summary || 'The fix failed'
    await expect(page.getByText(expected, { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('Caption fixed', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Undo fix' })).toHaveCount(0)
    await expect(page.getByPlaceholder(BOX, { exact: false })).toHaveValue('a figure beside broken glass')
    // The panel is free again once the job is over.
    await expect(page.getByRole('button', { name: 'Fix Caption' })).toBeEnabled()
  })

  test('with nothing to fix, the button stays off', async ({ page, request }) => {
    const { ds, imageId } = await seed(request)
    await page.goto(`/datasets/${ds.id}/gallery`)
    await page.getByTestId('gallery-tile').first().click()
    await expect(page).toHaveURL(new RegExp(`/image/${imageId}$`))

    await page.getByRole('button', { name: /Generate with AI/ }).click()
    await page.getByRole('button', { name: 'Fix current' }).click()
    await page.getByText(providerName, { exact: true }).click()
    await page.getByLabel("What's wrong with the caption?").fill('wrong colour')
    const fix = page.getByRole('button', { name: 'Fix Caption' })
    await expect(fix).toBeEnabled()

    await page.getByPlaceholder(BOX, { exact: false }).fill('')
    await expect(fix).toBeDisabled()
    await expect(page.getByText('There is no caption to fix yet.', { exact: false })).toBeVisible()
  })
})
