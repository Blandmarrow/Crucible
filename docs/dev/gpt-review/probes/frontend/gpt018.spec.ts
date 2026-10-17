import { test, expect } from '@playwright/test'
import { execFileSync } from 'child_process'
import * as fs from 'fs'
import { createDatasetViaApi, uploadViaApi } from './helpers'
import * as os_ from 'os'
const F = `${os_.tmpdir()}/gpt-review-probes`

function e2eDbPath(): string {
  const pids = execFileSync('pgrep', ['-f', 'uvicorn backend.main:app --port 8199']).toString().trim().split('\n')
  let url = ''
  for (const pid of pids) {
    try {
      const env = fs.readFileSync(`/proc/${pid}/environ`).toString().split('\0')
      const e = env.find((x) => x.startsWith('DATABASE_URL='))
      if (e) { url = e.split('=').slice(1).join('='); break }
    } catch {}
  }
  const p = url.replace('sqlite+aiosqlite:///', '')
  if (!p.includes('/tmp') && !p.includes('tmp.')) throw new Error('refusing: not a throwaway DB ' + p)
  return p
}

test('GPT-018: histogram bar "0.2–0.4: 1" opens a panel listing 2 images', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `f018-${Date.now()}`)
  const names = ['n.png', 'a02.png', 'a04.png', 'a06.png', 'a10.png']
  for (const n of names) await uploadViaApi(request, ds.id, n)
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const byName = Object.fromEntries(list.map((i: any) => [i.filename, i.id]))
  const scores: Record<string, number | null> = { 'n.png': null, 'a02.png': 0.2, 'a04.png': 0.4, 'a06.png': 0.6, 'a10.png': 1.0 }
  const db = e2eDbPath()
  console.log('GPT018 throwaway DB:', db)
  const py = `import sqlite3,sys,json
c=sqlite3.connect(sys.argv[1]); d=json.loads(sys.argv[2])
for i,v in d.items(): c.execute('update images set saturation_score=? where id=?',(v,i))
c.commit()`
  execFileSync('python3', ['-I', '-c', py, db, JSON.stringify(Object.fromEntries(Object.entries(scores).map(([n, v]) => [byName[n], v])))])

  // API: server-side bucket vs list filter semantics
  const stats = await (await request.get(`/api/v1/datasets/${ds.id}/stats`)).json()
  console.log('GPT018 API saturation_distribution:', JSON.stringify(stats.saturation_distribution))
  const filt = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, score_field: 'saturation_score', min_score: '0.2', max_score: '0.4', limit: '50' } })).json()
  console.log('GPT018 API list min=0.2 max=0.4:', filt.length, filt.map((i: any) => i.filename))

  await page.goto(`/datasets/${ds.id}/stats`)
  const bar = page.getByTitle('0.2–0.4: 1')
  await bar.scrollIntoViewIfNeeded()
  await expect(bar).toBeVisible()
  await bar.click({ force: true })
  const heading = page.getByRole('heading', { name: /Saturation/ })
  await expect(heading).toBeVisible()
  const sub = await page.getByText(/\d+ images?( ·|$)/).first().innerText()
  console.log('GPT018 UI bar title: "0.2–0.4: 1"; panel says:', JSON.stringify(sub))
  await page.screenshot({ path: `${F}/gpt018-bucket-panel.png` })
  expect(stats.saturation_distribution['0.2–0.4']).toBe(1)
  expect(filt.length).toBe(2)
  expect(sub).toMatch(/^2 images/)
})
