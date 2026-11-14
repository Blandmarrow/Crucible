import { test, expect } from '@playwright/test'
import type { APIRequestContext, Locator, Page } from '@playwright/test'
import { SYNONYMS_ENABLED_KEY } from '../src/constants/storage'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// #52: the synonym card under the caption box. The dictionary is never downloaded:
// `/dictionary/*` is faked per test with page.route, so these journeys pin the UI
// (select a word → meanings, a swap edits only the draft in the word's style, Esc
// closes, off by default, an uninstalled dictionary offers the download).

const BOX = 'Natural language description'
const PROSE = 'Shiny silver raincoat on a wet street'
const TAGS = 'school_uniform, red_scarf'

async function seed(request: APIRequestContext) {
  const ds = await createDatasetViaApi(request, `e2e-synonyms-${Date.now()}`)
  await uploadViaApi(request, ds.id, '01_prose.png')
  await uploadViaApi(request, ds.id, '02_tags.png')
  const list = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id, limit: '10' } })).json()
  const id: Record<string, string> = Object.fromEntries(
    list.map((i: { filename: string; id: string }) => [i.filename, i.id]),
  )
  for (const [file, text] of [['01_prose.png', PROSE], ['02_tags.png', TAGS]]) {
    const r = await request.put(`/api/v1/captions/image/${id[file]}`, { data: { caption_text: text, caption_style: 'natural' } })
    expect(r.ok()).toBeTruthy()
  }
  return { ds, prose: id['01_prose.png'], tags: id['02_tags.png'] }
}

const storedCaption = async (request: APIRequestContext, id: string) =>
  (await (await request.get(`/api/v1/captions/image/${id}`)).json()).caption_text as string

const READY = {
  installed: true, state: 'ready', percent: 100, message: '', error: null,
  size_bytes: 22_000_000, edition: 'Open English WordNet 2025 (core edition)',
}
const MISSING = { ...READY, installed: false, state: 'missing', percent: -1, size_bytes: null }

const SENSES: Record<string, unknown[]> = {
  shiny: [
    { lemma: 'shiny', pos: 'a', definition: 'reflecting light', words: ['glossy', 'lustrous'] },
    { lemma: 'shiny', pos: 'a', definition: 'having a shiny surface', words: ['glazed'] },
  ],
  'red scarf': [{ lemma: 'red scarf', pos: 'n', definition: 'a scarf that is red', words: ['muffler', 'neck warmer'] }],
}

/** Fake the dictionary routes; return the words looked up and the install presses. */
async function fakeDictionary(page: Page, status: object = READY, delayMs = 0) {
  const looked: string[] = []
  const installs: number[] = []
  await page.route('**/api/v1/dictionary/status', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(status) }))
  await page.route('**/api/v1/dictionary/install', (route) => {
    installs.push(Date.now())
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ...MISSING, state: 'downloading', percent: 10, message: 'Downloading (1.0 / 9.5 MB)' }),
    })
  })
  await page.route('**/api/v1/dictionary/synonyms**', async (route) => {
    const word = new URL(route.request().url()).searchParams.get('word') ?? ''
    looked.push(word)
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs))
    const key = word.toLowerCase().replace(/_/g, ' ')
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ word: key, lemmas: SENSES[key] ? [key] : [], senses: SENSES[key] ?? [] }),
    })
  })
  return { looked, installs }
}

const enable = (page: Page) =>
  page.addInitScript(([k]) => localStorage.setItem(k, 'true'), [SYNONYMS_ENABLED_KEY])

/** Select caption[start, end) as a keyboard selection would end. React's `onSelect`
 *  follows mouseup and keyup, not a bare `setSelectionRange`, so the Shift release
 *  is what a user's Shift+arrow selection would deliver. */
async function select(box: Locator, start: number, end: number) {
  await box.evaluate((el: HTMLTextAreaElement, [s, e]) => {
    el.focus()
    el.setSelectionRange(s, e)
  }, [start, end])
  await box.press('Shift')
}

test('Synonyms: a double-clicked word shows its meanings, and a swap keeps its case and saves nothing until Save', async ({ page, request }) => {
  const { ds, prose } = await seed(request)
  await enable(page)
  const { looked } = await fakeDictionary(page)
  await page.goto(`/datasets/${ds.id}/image/${prose}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(PROSE)

  // A real double-click on the first word (top-left of the box).
  await box.dblclick({ position: { x: 16, y: 14 } })
  const card = page.getByTestId('synonym-card')
  await expect(card).toBeVisible()
  await expect(card.getByTestId('synonym-sense')).toHaveCount(2)
  await expect(card).toContainText('reflecting light')
  expect(looked).toEqual(['Shiny'])

  const height = (await card.boundingBox())!.height
  const glossy = card.getByRole('button', { name: 'glossy' })
  await glossy.click()
  await expect(box).toHaveValue('Glossy silver raincoat on a wet street')
  // The card stays, on the same list and at the same size: closing it shrank the panel
  // and moved the caption under the pointer.
  await expect(glossy).toHaveAttribute('aria-pressed', 'true')
  expect((await card.boundingBox())!.height).toBe(height)
  expect(await storedCaption(request, prose)).toBe(PROSE)

  // A second swap replaces the word now in the box, not the old span ("Glossyy").
  await card.getByRole('button', { name: 'lustrous' }).click()
  await expect(box).toHaveValue('Lustrous silver raincoat on a wet street')
  // Put back restores the word first selected; then glossy again.
  await card.getByTestId('synonym-put-back').click()
  await expect(box).toHaveValue(PROSE)
  await expect(card.getByTestId('synonym-put-back')).toHaveCount(0)
  await glossy.click()
  await expect(box).toHaveValue('Glossy silver raincoat on a wet street')
  expect(looked).toEqual(['Shiny']) // the list was never looked up again

  // Focus stays in the box, caret after the new word: the next key edits the caption
  // instead of reaching the page's arrow navigation.
  await expect(box).toBeFocused()
  // (Set on the next animation frame, after React has written the value.)
  await expect.poll(() => box.evaluate((el: HTMLTextAreaElement) => el.selectionStart)).toBe('Glossy'.length)
  const url = page.url()
  await box.press('ArrowRight')
  expect(page.url()).toBe(url)

  // A word with no synonyms says so.
  const at = (await box.inputValue()).indexOf('street')
  await select(box, at, at + 'street'.length)
  await expect(page.getByTestId('synonym-none')).toHaveText('No synonyms for “street”.')

  // Esc in the box closes the card; the page's own Escape handling is not involved.
  await box.press('Escape')
  await expect(card).toHaveCount(0)

  // Save works with the card open: closing it must not move Save out from under the click.
  // (Collapse first: React's onSelect fires only when the selection changes, and Esc
  // left "street" selected.)
  await select(box, 0, 0)
  await select(box, at, at + 'street'.length)
  await expect(card).toBeVisible()
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect.poll(() => storedCaption(request, prose)).toBe('Glossy silver raincoat on a wet street')
})

test('Synonyms: the card holds its size between words, so a second double-click lands where aimed', async ({ page, request }) => {
  // A double-click's first press collapses the old selection. The card used to close
  // there and reopen on the second press; the panel shrank in between, its scroll
  // clamped, and the caption moved under the pointer.
  const { ds, prose } = await seed(request)
  await enable(page)
  await fakeDictionary(page, READY, 800)
  await page.goto(`/datasets/${ds.id}/image/${prose}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(PROSE)
  const card = page.getByTestId('synonym-card')
  const list = page.getByTestId('synonym-list')

  await select(box, 0, 5)
  await expect(list).toHaveAttribute('aria-busy', 'false')
  const height = (await card.boundingBox())!.height

  // A collapsed caret, as the first press of a double-click leaves: the card stays.
  await select(box, 8, 8)
  await expect(card).toBeVisible()
  expect((await card.boundingBox())!.height).toBe(height)

  // The next word: while it loads, the last list stays, dimmed, and its chips are off.
  await select(box, 6, 12)
  await expect(list).toHaveAttribute('aria-busy', 'true')
  expect((await card.boundingBox())!.height).toBe(height)
  await expect(card.getByTestId('synonym-word').first()).toBeDisabled()
  await expect(page.getByTestId('synonym-none')).toHaveText('No synonyms for “silver”.')

  // Typing closes it.
  await box.press('End')
  await box.press('s')
  await expect(card).toHaveCount(0)
})

test('Synonyms: a swap in a long caption leaves the panel scroll where it was', async ({ page, request }) => {
  // The caption box auto-sizes by setting its height to `auto` and measuring. That
  // collapsed it for a moment, the page laid out shorter, and a panel scrolled near the
  // bottom had its scroll clamped: the caption jumped on every swap. Typing runs the
  // same resize. Before the fix this read 573 → 291.
  const { ds, prose } = await seed(request)
  const long = `${PROSE}. ` + 'The rain keeps falling on the quiet town while lights flicker in the windows. '.repeat(12)
  const put = await request.put(`/api/v1/captions/image/${prose}`, { data: { caption_text: long, caption_style: 'natural' } })
  expect(put.ok()).toBeTruthy()
  await page.setViewportSize({ width: 1280, height: 560 })
  await enable(page)
  await fakeDictionary(page)
  await page.goto(`/datasets/${ds.id}/image/${prose}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(long)

  await select(box, 0, 5)
  const card = page.getByTestId('synonym-card')
  await expect(card.getByTestId('synonym-sense')).toHaveCount(2)

  /** The caption box's nearest scrolling ancestor: scrolled to its bottom, or read. */
  const panelScroll = (toBottom: boolean) =>
    box.evaluate((el, bottom) => {
      let p = el.parentElement
      while (p && !(p.scrollHeight > p.clientHeight && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement
      if (!p) return -1
      if (bottom) p.scrollTop = p.scrollHeight
      return p.scrollTop
    }, toBottom)
  const before = await panelScroll(true)
  expect(before).toBeGreaterThan(0) // the panel really scrolls at this size

  await card.getByRole('button', { name: 'glossy' }).click()
  await expect(box).toHaveValue(long.replace('Shiny', 'Glossy'))
  await page.evaluate(() => new Promise(requestAnimationFrame)) // past the caret's frame
  expect(await panelScroll(false)).toBe(before)
  // A second swap, and Put back, the same.
  await card.getByRole('button', { name: 'lustrous' }).click()
  await card.getByTestId('synonym-put-back').click()
  await expect(box).toHaveValue(long)
  await page.evaluate(() => new Promise(requestAnimationFrame))
  expect(await panelScroll(false)).toBe(before)
})

test('Synonyms: a tag keeps its underscores when swapped', async ({ page, request }) => {
  const { ds, tags } = await seed(request)
  await enable(page)
  const { looked } = await fakeDictionary(page)
  await page.goto(`/datasets/${ds.id}/image/${tags}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(TAGS)

  // The selection takes in the comma's space; it is trimmed off.
  await select(box, 15, 25)
  const card = page.getByTestId('synonym-card')
  await card.getByRole('button', { name: 'neck warmer' }).click()
  await expect(box).toHaveValue('school_uniform, neck_warmer')
  expect(looked).toEqual(['red_scarf'])
  expect(await storedCaption(request, tags)).toBe(TAGS)
})

test('Synonyms: off by default — no card and no lookup', async ({ page, request }) => {
  const { ds, prose } = await seed(request)
  const { looked } = await fakeDictionary(page)
  await page.goto(`/datasets/${ds.id}/image/${prose}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(PROSE)
  await select(box, 0, 5)
  await page.waitForTimeout(400) // past the card's 150 ms debounce
  await expect(page.getByTestId('synonym-card')).toHaveCount(0)
  expect(looked).toEqual([])
})

test('Synonyms: with no dictionary installed, the card and Settings both offer the download', async ({ page, request }) => {
  const { ds, prose } = await seed(request)
  await enable(page)
  const { looked, installs } = await fakeDictionary(page, MISSING)
  await page.goto(`/datasets/${ds.id}/image/${prose}`)
  const box = page.getByPlaceholder(BOX, { exact: false })
  await expect(box).toHaveValue(PROSE)
  await select(box, 0, 5)
  await expect(page.getByTestId('synonym-not-installed')).toBeVisible()
  expect(looked).toEqual([]) // no lookup against a missing dictionary
  await page.getByRole('button', { name: /Download it/ }).click()
  await expect.poll(() => installs.length).toBe(1)

  // Settings: switching the toggle off and on again starts the install too.
  await page.goto('/settings')
  await page.getByRole('button', { name: 'UI Behavior' }).click()
  const toggle = page.getByTestId('synonyms-toggle')
  await expect(toggle).toBeChecked()
  await toggle.uncheck()
  await toggle.check()
  await expect.poll(() => installs.length).toBe(2)
  expect(await page.evaluate((k) => localStorage.getItem(k), SYNONYMS_ENABLED_KEY)).toBe('true')
})
