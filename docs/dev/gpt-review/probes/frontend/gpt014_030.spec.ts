import { test, expect } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { createDatasetViaApi, pngBuffer } from './helpers'
import * as os_ from 'os'
const F = `${os_.tmpdir()}/gpt-review-probes`

async function importViaModal(page: any, folder: string) {
  await page.getByTitle('Import a folder of images into this dataset').click()
  const d = page.getByRole('dialog', { name: 'Import from folder' })
  await expect(d).toBeVisible()
  await d.getByPlaceholder('/home/user/images', { exact: false }).fill(folder)
  await d.getByRole('button', { name: 'Import', exact: true }).click()
}

test('GPT-014 (browser): SSE blocked during import; terminal event never reconciles after SSE returns', async ({ page, request }) => {
  test.setTimeout(120_000)
  const dir = path.join(F, 'import-src-240'); fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true })
  for (let i = 0; i < 240; i++) fs.writeFileSync(path.join(dir, `img_${String(i).padStart(3, '0')}.png`), pngBuffer())
  const ds = await createDatasetViaApi(request, `f014-${Date.now()}`)

  let sseBlocked = true; let sseRequestsAfterUnblock = 0; let abortedCount = 0
  await page.route('**/api/v1/jobs/stream/**', async (route: any) => {
    if (sseBlocked) { abortedCount++; await route.abort() } else { sseRequestsAfterUnblock++; await route.continue() }
  })
  await page.goto(`/datasets/${ds.id}/gallery`)
  await expect(page.getByTestId('gallery-tile')).toHaveCount(0)
  await importViaModal(page, dir)
  await expect(page.getByText('Import started')).toBeVisible()

  // wait for the backend to finish while the browser is blind
  await expect.poll(async () => (await (await request.get('/api/v1/images/count', { params: { dataset_id: ds.id } })).json()).count, { timeout: 60_000 }).toBe(240)
  const jobs = await (await request.get('/api/v1/jobs/', { params: { limit: '5' } })).json().catch(() => null)
  console.log('GPT014 backend jobs (head):', JSON.stringify(Array.isArray(jobs) ? jobs.slice(0, 1).map((j: any) => ({ type: j.job_type, status: j.status })) : jobs).slice(0, 300))
  console.log('GPT014 aborted SSE attempts while blocked:', abortedCount)

  sseBlocked = false
  await expect.poll(() => sseRequestsAfterUnblock, { timeout: 15_000 }).toBeGreaterThan(0)
  await page.waitForTimeout(6000)
  const uiText = await page.locator('body').innerText()
  const header = uiText.match(/\d+ image[s]?/g)
  const ready = uiText.includes('Ready')
  const tiles = await page.getByTestId('gallery-tile').count()
  console.log('GPT014 after SSE restored +6s: SSE reconnect requests =', sseRequestsAfterUnblock, ' image-count strings =', header, ' Ready =', ready, ' tiles =', tiles)
  await page.screenshot({ path: `${F}/gpt014-after-sse-restored.png` })
  expect(tiles).toBe(0) // stale = claim reproduced
  await page.reload()
  await expect(page.getByTestId('gallery-tile').first()).toBeVisible()
  console.log('GPT014 after reload: image-count strings =', (await page.locator('body').innerText()).match(/\d+ image[s]?/g))
})

test('GPT-014 control: SSE not blocked -> gallery reconciles by itself', async ({ page, request }) => {
  test.setTimeout(120_000)
  const dir = path.join(F, 'import-src-40'); fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true })
  for (let i = 0; i < 40; i++) fs.writeFileSync(path.join(dir, `img_${String(i).padStart(3, '0')}.png`), pngBuffer())
  const ds = await createDatasetViaApi(request, `f014c-${Date.now()}`)
  await page.goto(`/datasets/${ds.id}/gallery`)
  await importViaModal(page, dir)
  await expect(page.getByText(/40 images/).first()).toBeVisible({ timeout: 30_000 })
})

test('GPT-030: nonexistent folder import -> only "Import started" toast, no failure feedback', async ({ page, request }) => {
  test.setTimeout(60_000)
  const ds = await createDatasetViaApi(request, `f030-${Date.now()}`)
  const msgs: string[] = []
  await page.goto(`/datasets/${ds.id}/gallery`)
  await importViaModal(page, '/tmp/definitely/not/a/real/folder-f030')
  await expect(page.getByText('Import started')).toBeVisible()
  // observe backend job
  let job: any
  await expect.poll(async () => {
    const r = await request.get('/api/v1/jobs/', { params: { limit: '10' } })
    const list = r.ok() ? await r.json() : []
    job = (Array.isArray(list) ? list : list.jobs ?? []).find((j: any) => j.job_type === 'import' || /import/i.test(j.job_type ?? ''))
    return job?.status
  }, { timeout: 20_000 }).toBe('failed')
  console.log('GPT030 backend job:', JSON.stringify({ type: job.job_type, status: job.status, error: job.error_msg ?? job.error ?? job.message }))
  // collect every toast text for 8s
  const seen = new Set<string>()
  for (let i = 0; i < 16; i++) {
    const t = await page.locator('[role="status"], [data-sonner-toast], .go3958317564, div[class*="toast"]').allInnerTexts().catch(() => [])
    t.forEach((x: string) => seen.add(x.trim()))
    await page.waitForTimeout(500)
  }
  const body = await page.locator('body').innerText()
  console.log('GPT030 toasts seen:', JSON.stringify([...seen]))
  console.log('GPT030 body mentions "not found"/"failed":', /not found|failed/i.test(body), ' Ready:', body.includes('Ready'))
  await page.screenshot({ path: `${F}/gpt030-after-failed-import.png` })
  expect(/folder not found|import failed|failed/i.test(body)).toBe(false)
})
