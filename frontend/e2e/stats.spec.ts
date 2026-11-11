import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The Analytics (Stats) page renders its always-present panels for a dataset.
test('stats page renders its summary panels', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-stats-${Date.now()}`)
  await uploadViaApi(request, ds.id, 's.png')

  await page.goto(`/datasets/${ds.id}/stats`)
  // The route is /stats but the page heading reads "Analytics".
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible()
  await expect(page.getByText('Total images', { exact: true })).toBeVisible()
})

// The throwaway DB `serve.mjs` created for this run. Scores are written by the
// scorers alone (cv2/torch, which CI does not have) and no route sets one, so the
// only way to stage "a score exactly on a bucket edge" is to write the column.
// `serve.mjs` puts the DB at `${TMP}/e2e.db` and every dataset under
// `${TMP}/data/datasets/`, so a dataset's own `folder_path` locates it — and the
// tmpdir check refuses anything that is not that throwaway DB.
function e2eDbPath(datasetFolder: string): string {
  const db = path.resolve(datasetFolder, '..', '..', '..', 'e2e.db')
  if (!fs.existsSync(db)) throw new Error(`no e2e DB where serve.mjs puts it: ${db}`)
  // `.native` on both sides: it expands a Windows 8.3 temp path (`RUNNER~1`),
  // which the backend's own `resolve()` has already expanded in folder_path.
  const tmp = fs.realpathSync.native(os.tmpdir())
  if (!fs.realpathSync.native(db).startsWith(tmp + path.sep)) {
    throw new Error(`refusing to write a DB outside the temp dir: ${db}`)
  }
  return db
}

/** `{image id: {column: value}}` straight into the e2e DB. */
function setColumns(db: string, rows: Record<string, Record<string, number | null>>) {
  const py = `import sqlite3, sys, json
c = sqlite3.connect(sys.argv[1])
for iid, cols in json.loads(sys.argv[2]).items():
    for col, v in cols.items():
        c.execute(f'update images set "{col}" = ? where id = ?', (v, iid))
c.commit()`
  // Any Python will do (sqlite3 is stdlib); Windows installs name it `python`.
  const python = process.platform === 'win32' ? 'python' : 'python3'
  execFileSync(python, ['-I', '-c', py, db, JSON.stringify(rows)])
}

/** Click the bar titled `bar` and return the panel's "N images" line. */
async function openBar(page: Page, bar: string, panelTitle: string) {
  const el = page.getByTitle(bar, { exact: true })
  await el.scrollIntoViewIfNeeded()
  await el.click({ force: true })
  const heading = page.getByRole('heading', { name: panelTitle, exact: true })
  await expect(heading).toBeVisible()
  return heading.locator('xpath=..').locator('p')
}

// GPT-018: the stats buckets are half-open, and a bar used to send its upper edge
// as the inclusive `max_score`, so a value sitting on that edge was counted by one
// bar and listed by two. Each bar below is chosen so the old bound would have
// listed more images than the bar says. `backend/tests/test_stats_click_through_edges.py`
// covers every builder from the API side.
test('a histogram bar lists exactly the images it counts', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-stats-edges-${Date.now()}`)
  const names = ['n.png', 'a02.png', 'a04.png', 'a06.png', 'a10.png']
  for (const n of names) await uploadViaApi(request, ds.id, n)
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id: Record<string, string> = Object.fromEntries(list.map((i: { filename: string; id: string }) => [i.filename, i.id]))

  setColumns(e2eDbPath(ds.folder_path), {
    // Saturation 0.2/0.4/0.6 sit on SAT_EDGES; 0.4 is the top of "0.2–0.4".
    // Style similarity 1.0 is folded into "0.9–1.0", whose upper edge must stay open.
    // 104857 bytes is the last whole byte below 0.1 MB (104857.6); the old
    // `round()` edge of 104858 also listed every other image under "<0.1 MB".
    [id['n.png']]:   { saturation_score: null, style_similarity_score: null, file_size_bytes: 104857 },
    [id['a02.png']]: { saturation_score: 0.2,  style_similarity_score: 0.5,  file_size_bytes: 104858 },
    [id['a04.png']]: { saturation_score: 0.4,  style_similarity_score: 0.8,  file_size_bytes: 104858 },
    [id['a06.png']]: { saturation_score: 0.6,  style_similarity_score: 0.9,  file_size_bytes: 104858 },
    [id['a10.png']]: { saturation_score: 1.0,  style_similarity_score: 1.0,  file_size_bytes: 104858 },
  })

  await page.goto(`/datasets/${ds.id}/stats`)

  const sat = await openBar(page, '0.2–0.4: 1', 'Saturation')
  await expect(sat).toHaveText(/^1 image( ·|$)/)
  await page.keyboard.press('Escape')

  // "0.8–0.9" holds 0.8 alone; an inclusive 0.9 would have pulled in a06 too.
  const ssMid = await openBar(page, '0.8–0.9: 1', 'Style similarity')
  await expect(ssMid).toHaveText(/^1 image( ·|$)/)
  await page.keyboard.press('Escape')

  const ssTop = await openBar(page, '0.9–1.0: 2', 'Style similarity')
  await expect(ssTop).toHaveText(/^2 images( ·|$)/)
  await page.keyboard.press('Escape')

  const size = await openBar(page, '<0.1 MB: 1', 'File size')
  await expect(size).toHaveText(/^1 image( ·|$)/)
  await page.keyboard.press('Escape')

  // Every bar past the first used to be missing: the client's labels lacked the
  // server's " MB" suffix, and fsEntries drops a label the server did not send.
  const sizeNext = await openBar(page, '0.1–0.5 MB: 4', 'File size')
  await expect(sizeNext).toHaveText(/^4 images( ·|$)/)
})

// ── M9 stage 4: the raw `score-values` lists are fetched on demand ────────────
// The default histograms, the means and the CSV all come from `/stats`. The raw
// lists (one entry per image) load only for a panel with custom edges, or one
// whose edge editor is open.

/** Five images: saturation NULL/0.2/0.4/0.6/1.0, and two aesthetic scores. */
async function stageScores(request: Parameters<typeof createDatasetViaApi>[0], name: string) {
  const ds = await createDatasetViaApi(request, `${name}-${Date.now()}`)
  const names = ['n.png', 'a02.png', 'a04.png', 'a06.png', 'a10.png']
  for (const n of names) await uploadViaApi(request, ds.id, n)
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id: Record<string, string> = Object.fromEntries(list.map((i: { filename: string; id: string }) => [i.filename, i.id]))
  setColumns(e2eDbPath(ds.folder_path), {
    [id['n.png']]:   { saturation_score: null, aesthetic_score: null },
    [id['a02.png']]: { saturation_score: 0.2,  aesthetic_score: 5 },
    [id['a04.png']]: { saturation_score: 0.4,  aesthetic_score: 6 },
    [id['a06.png']]: { saturation_score: 0.6,  aesthetic_score: null },
    [id['a10.png']]: { saturation_score: 1.0,  aesthetic_score: null },
  })
  return ds
}

/** Every `score-values` request the page makes from here on. */
function watchRawRequests(page: Page): string[] {
  const seen: string[] = []
  page.on('request', (r) => { if (r.url().includes('/score-values')) seen.push(r.url()) })
  return seen
}

const saturationPanel = (page: Page) => page.locator('.panel', { hasText: 'Saturation' })
const meanAestheticCard = (page: Page) => page.locator('.stat-card', { hasText: 'Mean aesthetic' }).locator('.sv')

test('a plain stats load and its CSV export ask for no raw score lists', async ({ page, request }) => {
  const ds = await stageScores(request, 'e2e-stats-plain')
  const raw = watchRawRequests(page)

  await page.goto(`/datasets/${ds.id}/stats`)

  // The mean is the server's, over the two scored images.
  await expect(meanAestheticCard(page)).toHaveText('5.50')
  await expect(page.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()
  // The pencil does not wait on the lists it would fetch.
  await expect(saturationPanel(page).getByTitle('Edit bucket edges')).toBeVisible()

  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export Stats CSV' }).click()
  const csv = fs.readFileSync(await (await downloading).path(), 'utf8')
  expect(csv).toContain('mean_aesthetic_score,5.5000\n')
  expect(csv).toContain('mean_saturation_score,0.5500\n')
  // Nothing scored blur: an empty cell, not a zero.
  expect(csv).toContain('mean_blur_score,\n')

  // A request the page was going to make on load has had every chance by now:
  // the panels are up and a download has gone through since.
  await page.waitForTimeout(300)
  expect(raw).toEqual([])
})

test('editing a histogram fetches the raw lists, and a saved edge fetches them on load', async ({ page, request }) => {
  const ds = await stageScores(request, 'e2e-stats-custom')
  await page.goto(`/datasets/${ds.id}/stats`)
  const sat = saturationPanel(page)
  await expect(page.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()

  // Opening the editor is what asks for the lists.
  const asked = page.waitForRequest((r) => r.url().includes('/score-values'))
  await sat.getByTitle('Edit bucket edges').click()
  await asked

  await sat.getByPlaceholder('e.g. 4, 6, 8').fill('0.5')
  await sat.getByRole('button', { name: 'Apply' }).click()
  await expect(sat.getByText('custom', { exact: true })).toBeVisible()
  await expect(sat.getByTitle('< 0.5: 2', { exact: true })).toBeVisible()
  await expect(sat.getByTitle('0.5+: 2', { exact: true })).toBeVisible()
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toHaveCount(0)

  // A custom bar still lists exactly what it counts.
  const low = await openBar(page, '< 0.5: 2', 'Saturation')
  await expect(low).toHaveText(/^2 images( ·|$)/)
  await page.keyboard.press('Escape')

  // The edge is saved, so the next load asks for the lists straight away.
  const askedOnLoad = page.waitForRequest((r) => r.url().includes('/score-values'))
  await page.reload()
  await askedOnLoad
  await expect(sat.getByText('custom', { exact: true })).toBeVisible()
  await expect(sat.getByTitle('< 0.5: 2', { exact: true })).toBeVisible()

  // Reset goes back to the server's buckets, and stops asking.
  await sat.getByTitle('Edit bucket edges').click()
  await sat.getByRole('button', { name: 'Reset' }).click()
  await expect(sat.getByText('custom', { exact: true })).toHaveCount(0)
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()

  const raw = watchRawRequests(page)
  await page.reload()
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()
  await page.waitForTimeout(300)
  expect(raw).toEqual([])
})

// A panel with saved edges and no raw list must say so. Drawing the server's
// default buckets under a "custom" badge would be a wrong chart with no sign of it.
test('a custom histogram reports a failed raw-list load instead of default bars', async ({ page, request }) => {
  const ds = await stageScores(request, 'e2e-stats-rawfail')
  await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [`stats-hist-edges-saturation-${ds.id}`, '0.5'])
  await page.route(/\/score-values/, (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }))

  await page.goto(`/datasets/${ds.id}/stats`)
  const sat = saturationPanel(page)

  await expect(sat.getByText('Custom buckets unavailable — failed to load score values.')).toBeVisible({ timeout: 15_000 })
  await expect(sat.getByText('custom', { exact: true })).toBeVisible()
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toHaveCount(0)
  // The rest of the page does not depend on the lists.
  await expect(meanAestheticCard(page)).toHaveText('5.50')

  // An open editor with no list says so before Apply, not after.
  const color = page.locator('.panel', { hasText: 'Color richness' })
  await color.getByTitle('Edit bucket edges').click()
  await expect(color.getByText('Failed to load score values — custom buckets cannot be drawn yet.')).toBeVisible({ timeout: 15_000 })
})

// One mounted panel used to be carried across a dataset switch, and wrote the
// old dataset's edges under the new dataset's key before re-reading them. It
// needs the destination's stats already cached, or the page unmounts while they
// load and hides the fault. Navigation is client-side: a full load remounts.
test("a dataset's custom edges do not leak to the next dataset viewed", async ({ page, request }) => {
  const plain = await stageScores(request, 'e2e-stats-leak-plain')
  const custom = await stageScores(request, 'e2e-stats-leak-custom')
  const visit = async (id: string) => {
    await page.evaluate((url) => {
      window.history.pushState({}, '', url)
      window.dispatchEvent(new PopStateEvent('popstate'))
    }, `/datasets/${id}/stats`)
  }
  const sat = saturationPanel(page)

  await page.goto(`/datasets/${plain.id}/stats`)
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()
  await page.evaluate(([k, v]) => localStorage.setItem(k, v), [`stats-hist-edges-saturation-${custom.id}`, '0.5'])

  await visit(custom.id)
  await expect(sat.getByText('custom', { exact: true })).toBeVisible()
  await expect(sat.getByTitle('< 0.5: 2', { exact: true })).toBeVisible()

  // Back to the first dataset, whose stats are still cached.
  const raw = watchRawRequests(page)
  await visit(plain.id)
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()
  await expect(sat.getByText('custom', { exact: true })).toHaveCount(0)
  // Nor does the dataset left behind make this one fetch its raw lists.
  await page.waitForTimeout(300)
  expect(raw).toEqual([])
  expect(await page.evaluate((k) => localStorage.getItem(k), `stats-hist-edges-saturation-${plain.id}`)).toBeNull()
  expect(await page.evaluate((k) => localStorage.getItem(k), `stats-hist-edges-saturation-${custom.id}`)).toBe('0.5')
})

/**
 * Make the app believe a quality job is running on `datasetId`, which is what
 * turns on the Stats page's 5 s polling. Only the "what is running now" list the
 * job store seeds itself from is answered here; the page's own requests all go
 * to the real server. A real scoring job needs cv2 and is over in milliseconds
 * on five tiny images, so it could never span a poll.
 */
async function fakeRunningJob(page: Page, datasetId: string) {
  const now = new Date().toISOString()
  await page.route(/\/api\/v1\/jobs\/\?[^#]*status=pending/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{
      id: 'stub-stats-poll', job_type: 'quality_score', label: 'stub scoring job', status: 'running',
      dataset_id: datasetId, total_items: 10, done_items: 1, error_msg: null, result_data: {},
      config: {}, created_at: now, started_at: now, finished_at: null, retryable: false,
    }]),
  }))
}

test('during a job the page polls /stats, and the raw lists only while a panel needs them', async ({ page, request }) => {
  test.setTimeout(90_000)
  const ds = await stageScores(request, 'e2e-stats-poll')
  await fakeRunningJob(page, ds.id)
  const raw = watchRawRequests(page)
  const isStats = (r: { url(): string }) => new URL(r.url()).pathname.endsWith(`/datasets/${ds.id}/stats`)
  const twoStatsPolls = async () => {
    await page.waitForRequest(isStats, { timeout: 20_000 })
    await page.waitForRequest(isStats, { timeout: 20_000 })
  }

  await page.goto(`/datasets/${ds.id}/stats`)
  const sat = saturationPanel(page)
  await expect(sat.getByTitle('0.2–0.4: 1', { exact: true })).toBeVisible()

  // The job is on: /stats is polled, the raw lists are not asked for at all.
  await twoStatsPolls()
  expect(raw).toEqual([])

  // An open editor needs them, so they load and then join the poll.
  await sat.getByTitle('Edit bucket edges').click()
  await expect.poll(() => raw.length, { timeout: 25_000 }).toBeGreaterThanOrEqual(2)

  // Closed without applying: nothing needs them, and their polling stops.
  await sat.getByPlaceholder('e.g. 4, 6, 8').press('Escape')
  await expect(sat.getByPlaceholder('e.g. 4, 6, 8')).toHaveCount(0)
  const settled = raw.length
  await twoStatsPolls()
  expect(raw.length).toBe(settled)
})

// A failed refetch keeps the previous list, so the bars stay on screen. Without
// a line saying so they look current while the panels beside them keep moving.
test('a custom histogram says when a refresh of the raw lists fails, and recovers', async ({ page, request }) => {
  test.setTimeout(90_000)
  const ds = await stageScores(request, 'e2e-stats-refetch-fail')
  await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [`stats-hist-edges-saturation-${ds.id}`, '0.5'])
  await fakeRunningJob(page, ds.id)
  let failing = false
  await page.route(/\/score-values/, (route) => failing
    ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
    : route.continue())

  await page.goto(`/datasets/${ds.id}/stats`)
  const sat = saturationPanel(page)
  const notice = sat.getByText('Failed to refresh score values — these buckets may be out of date.')
  await expect(sat.getByTitle('< 0.5: 2', { exact: true })).toBeVisible()
  await expect(notice).toHaveCount(0)

  failing = true
  await expect(notice).toBeVisible({ timeout: 30_000 })
  // The last good bars are still drawn, under the warning.
  await expect(sat.getByTitle('< 0.5: 2', { exact: true })).toBeVisible()

  failing = false
  await expect(notice).toHaveCount(0, { timeout: 30_000 })
})
