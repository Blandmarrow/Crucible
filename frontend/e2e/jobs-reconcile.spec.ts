import { test, expect, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { createDatasetViaApi, pngBuffer } from './helpers'

// Job outcomes that cannot be missed (GPT-014, GPT-030).
//
// The SSE stream is live-only: a job that finishes while it is down never sends
// its terminal event to this page. `utils/jobReconcile.ts` catches up from the
// durable rows, and a folder import's outcome — success, failure or cancel — is
// reported by the always-mounted TopBar for all three entry points, each of which
// adopts the job on submit. Ported, inverted, from the gpt-review probe that
// showed the gallery stuck at 0 images after an SSE gap and a failed import
// reporting nothing but "Import started".

/** Record every distinct toast element and each text it showed, from page load.
 *  react-hot-toast reuses one element per toast id, so an element count is a
 *  toast count — a duplicate toast is a second element, an in-place update is
 *  not. */
async function recordToasts(page: Page) {
  await page.addInitScript(() => {
    const ids = new WeakMap<Element, number>()
    const seen = new WeakMap<Element, Set<string>>()
    const log: { el: number; text: string }[] = []
    let next = 0
    const scan = () => {
      document.querySelectorAll('[role="status"]').forEach((el) => {
        if (!ids.has(el)) ids.set(el, ++next)
        const text = (el.textContent ?? '').trim()
        const texts = seen.get(el) ?? new Set<string>()
        if (text && !texts.has(text)) {
          texts.add(text)
          log.push({ el: ids.get(el)!, text })
        }
        seen.set(el, texts)
      })
    }
    new MutationObserver(scan).observe(document, { subtree: true, childList: true, characterData: true })
    ;(window as unknown as { __toasts: typeof log }).__toasts = log
  })
}

async function toastCount(page: Page, pattern: RegExp): Promise<number> {
  const log = await page.evaluate(
    () => (window as unknown as { __toasts: { el: number; text: string }[] }).__toasts,
  )
  return new Set(log.filter((t) => pattern.test(t.text)).map((t) => t.el)).size
}

function stageFolder(name: string, count: number): string {
  const dir = path.join(os.tmpdir(), 'crucible-e2e-jobs-reconcile', `${name}-${Date.now()}`)
  fs.rmSync(dir, { recursive: true, force: true })
  fs.mkdirSync(dir, { recursive: true })
  for (let i = 0; i < count; i++) {
    fs.writeFileSync(path.join(dir, `img_${String(i).padStart(3, '0')}.png`), pngBuffer())
  }
  return dir
}

async function importViaGalleryModal(page: Page, folder: string) {
  await page.getByTitle('Import a folder of images into this dataset').click()
  const dialog = page.getByRole('dialog', { name: 'Import from folder' })
  await expect(dialog).toBeVisible()
  await dialog.getByPlaceholder('/home/user/images', { exact: false }).fill(folder)
  await dialog.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(dialog).toHaveCount(0)
}

/** A `background_jobs` row as `GET /jobs/{id}` returns it. */
function jobRow(over: Record<string, unknown>) {
  const now = new Date().toISOString()
  return {
    label: null, total_items: 0, done_items: 0, error_msg: null, result_data: {}, config: {},
    created_at: now, started_at: now, finished_at: null, ...over,
  }
}

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

test('an import that finishes while SSE is down still lands, without a reload', async ({ page, request }) => {
  test.setTimeout(120_000)
  const dir = stageFolder('sse-gap', 240)
  const ds = await createDatasetViaApi(request, `e2e-reconcile-gap-${Date.now()}`)
  await recordToasts(page)

  let blocked = true
  let streamsAfterUnblock = 0
  await page.route('**/api/v1/jobs/stream/**', async (route) => {
    if (blocked) return route.abort()
    streamsAfterUnblock++
    return route.continue()
  })

  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(0)
  await importViaGalleryModal(page, dir)
  await expect(page.getByText('Import submitted')).toBeVisible()

  // The backend finishes while the page cannot hear it.
  await expect.poll(async () => {
    const r = await request.get('/api/v1/images/count', { params: { dataset_id: ds.id } })
    return (await r.json()).count
  }, { timeout: 60_000 }).toBe(240)

  blocked = false
  await expect.poll(() => streamsAfterUnblock, { timeout: 15_000 }).toBeGreaterThan(0)

  // Reconciled from the durable row: tiles, the summary toast, an idle TopBar.
  await expect(page.getByTestId('gallery-tile').first()).toBeVisible({ timeout: 25_000 })
  await expect.poll(() => toastCount(page, /Imported 240 images/), { timeout: 25_000 }).toBe(1)
  await expect(page.getByText('Ready', { exact: true })).toBeVisible()
})

test('a terminal status from both SSE and the reconcile toasts exactly once', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-reconcile-once-${Date.now()}`)
  const jobId = `stub-import-${Date.now()}`
  const completed = jobRow({
    id: jobId, job_type: 'import', status: 'completed', dataset_id: ds.id,
    total_items: 7, result_data: { added: 7 }, finished_at: new Date().toISOString(),
  })
  await recordToasts(page)

  let submitted = false
  let streamsAfterSubmit = 0
  await page.route('**/api/v1/datasets/*/import', (route) => {
    submitted = true
    return route.fulfill(json({ job_id: jobId }))
  })
  // Adopt, the summary toast's own fetch, and the reconcile's `?ids=` list all
  // see the job already finished — the fast-finish-in-a-gap case.
  await page.route(`**/api/v1/jobs/${jobId}`, (route) => route.fulfill(json(completed)))
  await page.route(
    (url) => url.pathname === '/api/v1/jobs/' && url.searchParams.has('ids'),
    (route) => route.fulfill(json([completed])),
  )
  // ...and the live stream delivers the same terminal event again on every
  // reconnect (a fulfilled body ends, so EventSource reopens it).
  const frame = JSON.stringify({
    type: 'progress', job_id: jobId, job_type: 'import', label: null, status: 'completed',
    dataset_id: ds.id, done: 7, total: 7, percent: 100, message: 'Done.',
  })
  await page.route('**/api/v1/jobs/stream/all/events', (route) => {
    if (!submitted) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': idle\n\n' })
    streamsAfterSubmit++
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: `data: ${frame}\n\n` })
  })

  await page.goto(`/datasets/${ds.id}/gallery`)
  await importViaGalleryModal(page, path.join(os.tmpdir(), 'e2e-stubbed-import-source'))

  await expect(page.getByText('Imported 7 images')).toBeVisible()
  // Two more deliveries after the first toast: neither may add another.
  await expect.poll(() => streamsAfterSubmit, { timeout: 20_000 }).toBeGreaterThanOrEqual(2)
  expect(await toastCount(page, /Imported 7 images/)).toBe(1)
})

test('an import of a folder that does not exist reports the server error', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-reconcile-404-${Date.now()}`)
  await recordToasts(page)

  await page.goto(`/datasets/${ds.id}/gallery`)
  await importViaGalleryModal(page, path.join(os.tmpdir(), 'definitely', 'not', 'a', 'real', 'folder-e2e'))

  await expect(page.getByText('Import submitted')).toBeVisible()
  await expect(page.getByText(/Import failed: Folder not found/)).toBeVisible({ timeout: 20_000 })
  expect(await toastCount(page, /Import started/)).toBe(0)
  await expect(page.getByText('Ready', { exact: true })).toBeVisible()
})

test('a gallery import shows its summary toast exactly once', async ({ page, request }) => {
  const dir = stageFolder('gallery-once', 12)
  const ds = await createDatasetViaApi(request, `e2e-reconcile-gallery-${Date.now()}`)
  await recordToasts(page)

  await page.goto(`/datasets/${ds.id}/gallery`)
  await importViaGalleryModal(page, dir)

  await expect(page.getByText('Imported 12 images')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('gallery-tile')).toHaveCount(12)
  // Long enough for a second watcher, had one survived, to have fired.
  await page.waitForTimeout(3000)
  expect(await toastCount(page, /Imported 12 images/)).toBe(1)
})

test('a File Browser import toasts after the user has navigated away', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-reconcile-fb-${Date.now()}`)
  // Staged inside datasets_dir, which is where the File Browser opens.
  const name = `e2e-fb-source-${Date.now()}`
  const dir = path.join(path.dirname(ds.folder_path), name)
  fs.mkdirSync(dir, { recursive: true })
  for (let i = 0; i < 5; i++) fs.writeFileSync(path.join(dir, `fb_${i}.png`), pngBuffer())
  await recordToasts(page)

  await page.goto('/file-browser')
  await page.getByText(name, { exact: true }).click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Import into Dataset' }).click()
  await expect(page.getByRole('heading', { name: 'Import into Dataset' })).toBeVisible()
  await page.locator('select').last().selectOption(ds.id)
  await page.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(page.getByText('Import submitted')).toBeVisible()

  // Client-side navigation: the page that started the import is gone, the store
  // is not.
  await page.getByRole('link', { name: 'Datasets' }).first().click()
  await expect(page).toHaveURL(/\/datasets$/)

  await expect.poll(() => toastCount(page, /Imported 5 images/), { timeout: 30_000 }).toBe(1)
  fs.rmSync(dir, { recursive: true, force: true })
})

test('a rescan that fails or is cancelled says so and frees the button', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-reconcile-rescan-${Date.now()}`)
  const outcomes = [
    { status: 'failed', error_msg: 'Disk on fire' },
    { status: 'cancelled', error_msg: null },
  ]
  let calls = 0
  await page.route('**/api/v1/datasets/*/rescan', (route) => {
    const n = calls++
    return route.fulfill(json({ job_id: `stub-rescan-${n}` }))
  })
  // The job is already over when it is adopted — the stream never hears of it.
  await page.route('**/api/v1/jobs/stub-rescan-*', (route) => {
    const n = Number(new URL(route.request().url()).pathname.split('-').at(-1))
    return route.fulfill(json(jobRow({
      id: `stub-rescan-${n}`, job_type: 'rescan', dataset_id: ds.id,
      finished_at: new Date().toISOString(), ...outcomes[n],
    })))
  })

  await page.goto(`/datasets/${ds.id}/gallery`)
  const button = page.getByTitle(/Rescan folder from disk/)

  await button.click()
  await expect(page.getByText('Rescan failed: Disk on fire')).toBeVisible()
  await expect(button).toBeEnabled()
  await expect(button).toHaveText(/Rescan$/)

  await button.click()
  await expect(page.getByText('Rescan cancelled')).toBeVisible()
  await expect(button).toBeEnabled()
  expect(calls).toBe(2)
})
