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

// The throwaway DB `serve.sh` created for this run. Scores are written by the
// scorers alone (cv2/torch, which CI does not have) and no route sets one, so the
// only way to stage "a score exactly on a bucket edge" is to write the column.
// `serve.sh` puts the DB at `${TMP}/e2e.db` and every dataset under
// `${TMP}/data/datasets/`, so a dataset's own `folder_path` locates it — and the
// tmpdir check refuses anything that is not that throwaway DB.
function e2eDbPath(datasetFolder: string): string {
  const db = path.resolve(datasetFolder, '..', '..', '..', 'e2e.db')
  if (!fs.existsSync(db)) throw new Error(`no e2e DB where serve.sh puts it: ${db}`)
  const tmp = fs.realpathSync(os.tmpdir())
  if (!fs.realpathSync(db).startsWith(tmp + path.sep)) {
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
  execFileSync('python3', ['-I', '-c', py, db, JSON.stringify(rows)])
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
})
