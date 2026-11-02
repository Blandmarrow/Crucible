import { test, expect, type Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The detail page's one-image upscale and LUT runs: what the toast says when a
// run did nothing, and what the page does while a run is queued or after the
// user has moved on (cleanup plan, stage 1 follow-ups 4 and 6).
//
// No e2e run has a model, so the jobs are faked at the HTTP edge, as
// `jobs-reconcile.spec.ts` fakes its import: the run POST, the job row the page
// reads its outcome from, and the live stream. The real backend still serves the
// dataset, the images and `GET /jobs/?ids=`, which does not know the stub ids
// and so leaves them to the stream — the same as a job whose row was deleted.

// The open tool's controls overflow the toolbar under the right-hand panel at
// the default 1280px, and at 1700px once the gallery arrows are there too.
test.use({ viewport: { width: 1920, height: 1000 } })

type StubJob = {
  jobType: 'batch_upscale' | 'batch_lut'
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  resultData: Record<string, unknown>
  imageId?: string
}

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

/** Fake upscale and LUT models and jobs for `page`. The returned controller
 *  finishes a job, and can hold the job-row GET the page reads its outcome from. */
async function fakeEditJobs(page: Page, datasetId: string) {
  const jobs = new Map<string, StubJob>()
  const order: string[] = []
  let heldOutcome: Promise<void> | null = null

  await page.route('**/api/v1/upscaling/models', (route) =>
    route.fulfill(json([{ name: 'fake_2x', path: '/models/fake_2x.pth', scale: 2 }])))
  await page.route('**/api/v1/lut/models', (route) =>
    route.fulfill(json([{ name: 'warm', path: '/luts/warm.cube', format: 'cube' }])))

  for (const [path, jobType] of [['upscaling', 'batch_upscale'], ['lut', 'batch_lut']] as const) {
    await page.route(`**/api/v1/${path}/run`, (route) => {
      const id = `stub-edit-${order.length}`
      jobs.set(id, { jobType, status: 'pending', resultData: {} })
      order.push(id)
      return route.fulfill(json({ job_id: id, total: 1 }))
    })
  }

  await page.route('**/api/v1/jobs/stub-edit-*', async (route) => {
    if (heldOutcome) await heldOutcome
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!
    const job = jobs.get(id)!
    const now = new Date().toISOString()
    return route.fulfill(json({
      id, job_type: job.jobType, status: job.status, dataset_id: datasetId, label: null,
      total_items: 1, done_items: job.status === 'completed' ? 1 : 0, error_msg: null,
      result_data: job.resultData, config: {}, created_at: now, started_at: now, finished_at: now,
    }))
  })

  // Every connection replays each stub job's current state, then ends; `retry`
  // makes EventSource reopen quickly, so a status change lands within ~150 ms.
  await page.route('**/api/v1/jobs/stream/**', (route) => {
    const frames = [...jobs].map(([id, j]) => `data: ${JSON.stringify({
      type: 'progress', job_id: id, job_type: j.jobType, status: j.status, dataset_id: datasetId,
      done: j.status === 'completed' ? 1 : 0, total: 1, percent: j.status === 'completed' ? 100 : 0,
      ...(j.imageId ? { image_id: j.imageId } : {}),
    })}\n\n`)
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: `retry: 150\n\n${frames.join('')}` })
  })

  return {
    /** The id of the most recently started run. */
    latest: () => order.at(-1)!,
    finish(id: string, over: Partial<StubJob>) {
      jobs.set(id, { ...jobs.get(id)!, status: 'completed', ...over })
    },
    set(id: string, over: Partial<StubJob>) {
      jobs.set(id, { ...jobs.get(id)!, ...over })
    },
    /** Hold every job-row GET until the returned function is called. */
    holdOutcome(): () => void {
      let release!: () => void
      heldOutcome = new Promise<void>((r) => { release = r })
      return () => { heldOutcome = null; release() }
    },
  }
}

async function seedImages(request: Parameters<typeof uploadViaApi>[0], prefix: string, names: string[]) {
  const ds = await createDatasetViaApi(request, `${prefix}-${Date.now()}`)
  for (const name of names) await uploadViaApi(request, ds.id, name)
  const listing = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const rows: { id: string; filename: string }[] = Array.isArray(listing) ? listing : listing.images
  const ids = Object.fromEntries(rows.map((r) => [r.filename, r.id]))
  return { ds, ids }
}

async function openTool(page: Page, tool: 'Upscale' | 'LUT') {
  await page.getByRole('button', { name: tool, exact: true }).click()
  const placeholder = tool === 'Upscale' ? '— select model —' : '— select LUT —'
  await page
    .locator('select', { has: page.locator('option', { hasText: placeholder }) })
    .selectOption({ index: 1 })
}

/** The open tool's Run button, which shows a percentage while its job is live. */
const runButton = (page: Page) => page.getByRole('button', { name: /^(Run|\d+%)$/ })

async function startRun(page: Page) {
  const posted = page.waitForResponse((r) => /\/(upscaling|lut)\/run$/.test(r.url()))
  await runButton(page).click()
  await posted
}

test('a one-image run that did nothing says which skip it was', async ({ page, request }) => {
  const { ds, ids } = await seedImages(request, 'e2e-edit-why', ['a.png'])
  const jobs = await fakeEditJobs(page, ds.id)
  await page.goto(`/datasets/${ds.id}/image/${ids['a.png']}`)
  await openTool(page, 'Upscale')

  const cases: [Record<string, number>, string][] = [
    [{ skipped: 1, missing: 1 }, 'Upscaling skipped — the image was deleted before the job ran'],
    [{ skipped: 1, missing: 0 }, 'Upscaling skipped — a file with the new name already exists'],
    [{ skipped: 0, missing: 0 }, 'Upscaling skipped — nothing was changed'],
  ]
  for (const [counts, toast] of cases) {
    await startRun(page)
    jobs.finish(jobs.latest(), { resultData: { processed: 0, failed: 0, ...counts } })
    await expect(page.getByText(toast)).toBeVisible()
    // Nothing processed leaves the tool open and Run free for another try.
    await expect(runButton(page)).toHaveText('Run')
    await expect(runButton(page)).toBeEnabled()
  }

  // The LUT twin reads the same counts.
  await openTool(page, 'LUT')
  await startRun(page)
  jobs.finish(jobs.latest(), { resultData: { processed: 0, failed: 0, skipped: 1, missing: 1 } })
  await expect(page.getByText('LUT skipped — the image was deleted before the job ran')).toBeVisible()
})

test('Run is held while the job is queued, and freed when it is cancelled', async ({ page, request }) => {
  const { ds, ids } = await seedImages(request, 'e2e-edit-queued', ['a.png'])
  const jobs = await fakeEditJobs(page, ds.id)
  await page.goto(`/datasets/${ds.id}/image/${ids['a.png']}`)
  await openTool(page, 'Upscale')

  await startRun(page)
  // `pending`, as a job queued behind another stays: a second click would start
  // a second run on the same image.
  await expect(runButton(page)).toHaveText('0%')
  await expect(runButton(page)).toBeDisabled()

  jobs.set(jobs.latest(), { status: 'cancelled' })
  await expect(runButton(page)).toHaveText('Run')
  await expect(runButton(page)).toBeEnabled()
})

test('an outcome that lands after the user moved to another image does not drag them back', async ({
  page,
  request,
}) => {
  const { ds, ids } = await seedImages(request, 'e2e-edit-moved', ['a.png', 'b.png'])
  const jobs = await fakeEditJobs(page, ds.id)
  // Opened from the gallery, which is what gives the detail page its arrows.
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(2)
  await page.getByTestId('gallery-tile').first().click()
  await expect(page).toHaveURL(/\/image\//)
  const startedOn = page.url()
  const other = startedOn.endsWith(ids['a.png']) ? ids['b.png'] : ids['a.png']
  await openTool(page, 'Upscale')

  // A copy-mode run whose result would open the new image, with its outcome
  // GET held so the user can move on first.
  await startRun(page)
  const release = jobs.holdOutcome()
  const outcomeAsked = page.waitForRequest((r) => r.url().includes(`/jobs/${jobs.latest()}`))
  jobs.finish(jobs.latest(), { resultData: { processed: 1, failed: 0, skipped: 0, missing: 0 }, imageId: 'stub-new-image' })
  await outcomeAsked

  // The first tile has no previous image, so Next leads to the other one.
  await page.getByTitle('Next image (→)').click()
  await expect(page).toHaveURL(new RegExp(`/image/${other}$`))
  const movedTo = page.url()

  // Released while the move may still be rendering: the outcome must read where
  // the user asked to go, not the image this render still shows.
  release()
  await expect(page.getByText('Upscaling complete', { exact: true })).toBeVisible()
  expect(page.url()).toBe(movedTo)
  await expect(page.getByText(/Image not found/)).toHaveCount(0)
  await expect(page.getByPlaceholder('Natural language description', { exact: false })).toBeVisible()
})

test('an outcome that lands after a second run started leaves that run alone', async ({ page, request }) => {
  const { ds, ids } = await seedImages(request, 'e2e-edit-rerun', ['a.png'])
  const jobs = await fakeEditJobs(page, ds.id)
  const here = `/datasets/${ds.id}/image/${ids['a.png']}`
  await page.goto(here)
  await openTool(page, 'Upscale')

  await startRun(page)
  const first = jobs.latest()
  const release = jobs.holdOutcome()
  const outcomeAsked = page.waitForRequest((r) => r.url().includes(`/jobs/${first}`))
  jobs.finish(first, { resultData: { processed: 1, failed: 0, skipped: 0, missing: 0 }, imageId: 'stub-new-image' })
  await outcomeAsked

  // The id is cleared while that GET is out, so Run is live: a second run starts.
  await startRun(page)
  await expect(runButton(page)).toHaveText('0%')

  release()
  await expect(page.getByText('Upscaling complete', { exact: true })).toBeVisible()
  // The first run's outcome neither closed the tool under the second run nor
  // opened its own result.
  await expect(runButton(page)).toHaveText('0%')
  await expect(page).toHaveURL(new RegExp(`${here}$`))
})
