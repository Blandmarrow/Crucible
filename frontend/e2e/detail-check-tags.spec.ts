import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// #50: "Check tags on Danbooru" on the detail page's caption panel. Danbooru is never
// reached: `/booru/check` is faked per test with page.route, so these journeys pin the UI
// (press-only, problems first, Replace edits only the draft, an outage reads as one).

const BOX = 'Natural language description'
const TAGS = 'yellow hair, schol uniform, cat_ears, standing up'
const PROSE = 'A woman in a red coat walks down a wet street at night, neon signs reflected in puddles.'

async function seed(request: APIRequestContext) {
  const ds = await createDatasetViaApi(request, `e2e-check-tags-${Date.now()}`)
  await uploadViaApi(request, ds.id, '01_tags.png')
  await uploadViaApi(request, ds.id, '02_prose.png')
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id: Record<string, string> = Object.fromEntries(
    list.map((i: { filename: string; id: string }) => [i.filename, i.id]),
  )
  for (const [file, text] of [['01_tags.png', TAGS], ['02_prose.png', PROSE]]) {
    const r = await request.put(`/api/v1/captions/image/${id[file]}`, { data: { caption_text: text, caption_style: 'natural' } })
    expect(r.ok()).toBeTruthy()
  }
  return { ds, tags: id['01_tags.png'], prose: id['02_prose.png'] }
}

const storedCaption = async (request: APIRequestContext, id: string) =>
  (await (await request.get(`/api/v1/captions/image/${id}`)).json()).caption_text as string

const RESULT = {
  suggestions_complete: true,
  results: [
    { input: 'yellow hair', name: 'yellow_hair', status: 'alias', count: 0, category: 'general', suggestion: { name: 'blonde_hair', count: 1600000 } },
    { input: 'schol uniform', name: 'schol_uniform', status: 'missing', count: 0, category: null, suggestion: { name: 'school_uniform', count: 1087767 } },
    { input: 'cat_ears', name: 'cat_ears', status: 'ok', count: 420512, category: 'general', suggestion: null },
    { input: 'standing up', name: 'standing_up', status: 'rare', count: 78, category: 'general', suggestion: { name: 'standing', count: 1900000 } },
  ],
}

/** Fake the check route; return the list of tag lists it received. */
async function fakeCheck(page: Page, reply: { status: number; body: unknown }) {
  const sent: string[][] = []
  await page.route('**/api/v1/booru/check', async (route) => {
    sent.push(route.request().postDataJSON().tags)
    await route.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify(reply.body) })
  })
  return sent
}

test('Check tags: press-only, problems first, Replace edits the draft in the caption style', async ({ page, request }) => {
  const { ds, tags } = await seed(request)
  const sent = await fakeCheck(page, { status: 200, body: RESULT })
  await page.goto(`/datasets/${ds.id}/image/${tags}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(TAGS)

  // Nothing leaves the machine until the button is pressed.
  const run = page.getByTestId('check-tags-run')
  await expect(run).toBeVisible()
  expect(sent).toEqual([])
  await run.click()
  await expect(page.getByTestId('check-tags-summary')).toHaveText('1 of 4 tags look fine · 3 to review')
  expect(sent).toEqual([['yellow hair', 'schol uniform', 'cat_ears', 'standing up']])

  const rows = page.getByTestId('check-tags-row')
  await expect(rows).toHaveCount(3) // the fine tag is folded away
  await expect(rows.nth(0)).toHaveAttribute('data-status', 'alias')
  await expect(rows.nth(1)).toHaveAttribute('data-status', 'rare')
  await expect(rows.nth(2)).toHaveAttribute('data-status', 'missing')
  await expect(rows.nth(2)).toContainText('not found')

  // Replace swaps one tag, in the caption's space style, and saves nothing.
  await rows.nth(0).getByTestId('check-tags-replace').click()
  await expect(box).toHaveValue('blonde hair, schol uniform, cat_ears, standing up')
  expect(await storedCaption(request, tags)).toBe(TAGS)
  // That tag is gone from the box, so its Replace can no longer apply.
  await expect(rows.nth(0).getByTestId('check-tags-replace')).toBeDisabled()

  await rows.nth(1).getByTestId('check-tags-replace').click()
  await expect(box).toHaveValue('blonde hair, schol uniform, cat_ears, standing')
  expect(await storedCaption(request, tags)).toBe(TAGS)

  // The fine tags unfold on request.
  await page.getByRole('button', { name: 'Show the 1 fine tag' }).click()
  await expect(rows).toHaveCount(4)
  await expect(rows.nth(3)).toHaveAttribute('data-status', 'ok')
})

test('Check tags: hidden for a prose caption', async ({ page, request }) => {
  const { ds, prose } = await seed(request)
  await page.goto(`/datasets/${ds.id}/image/${prose}`)
  await expect(page.getByPlaceholder(BOX, { exact: false })).toHaveValue(PROSE)
  await expect(page.getByTestId('other-captions-toggle')).toBeVisible() // the panel has rendered
  await expect(page.getByTestId('check-tags-run')).toHaveCount(0)
})

test('Check tags: an unreachable Danbooru reads as an outage, not as unknown tags', async ({ page, request }) => {
  const { ds, tags } = await seed(request)
  await fakeCheck(page, { status: 502, body: { detail: 'Could not reach Danbooru (HTTP 403)' } })
  await page.goto(`/datasets/${ds.id}/image/${tags}`)
  await page.getByTestId('check-tags-run').click()
  await expect(page.getByTestId('check-tags-error')).toHaveText('Could not reach Danbooru (HTTP 403). Nothing was marked.')
  await expect(page.getByTestId('check-tags-row')).toHaveCount(0)
  await expect(page.getByTestId('check-tags-summary')).toHaveCount(0)
})
