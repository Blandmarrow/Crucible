import { test, expect, type APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The rating keys are rebindable and live on the server, in the same
// `threshold_settings` row as everything else. `rating.spec.ts` presses the
// DEFAULT digits, and the e2e database is shared by one worker — so every test
// here puts the defaults back in a `finally`, whatever happens in between.
const DEFAULT_KEYS = {
  rating_key_keep: '4',
  rating_key_probably: '3',
  rating_key_probably_not: '2',
  rating_key_cut: '1',
  rating_key_clear: '0',
}

async function restoreRatingKeys(request: APIRequestContext) {
  const r = await request.patch('/api/v1/settings/thresholds', { data: DEFAULT_KEYS })
  expect(r.status(), await r.text()).toBe(200)
}

// A label hotkey and a rating key on the same digit: the label wins on the
// detail page. Refusing the pairing instead would make the order the two were
// bound in decide the outcome, so it is allowed — and this pins who wins.
// The load-bearing check is the network one: the label chip appearing proves the
// label ran, but only an empty `bulk-rating` list proves the rating did not.
test('a label bound to a rating digit wins it on the detail page', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `rating-keys-label-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  const [{ id: imgId }] = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()

  const name = `rk-${Date.now()}`
  const created = await request.post('/api/v1/labels/', { data: { name, hotkey: '2' } })
  expect(created.status(), await created.text()).toBe(201)
  const label = await created.json()

  try {
    await page.goto(`/datasets/${ds.id}/image/${imgId}`)
    const labelBlock = page.getByRole('group', { name: 'Labels' })
    await expect(labelBlock.getByText('None')).toBeVisible()
    // The key handlers are registered by an effect; press only once the page is up.
    await expect(page.getByRole('button', { name: 'Back' })).toBeVisible()

    const writes: string[] = []
    page.on('request', (r) => { if (r.url().includes('bulk-rating')) writes.push(r.url()) })

    await page.keyboard.press('2')
    await expect(labelBlock.getByText(name)).toBeVisible()

    // No event to wait for when the correct behaviour is that nothing is sent.
    await page.waitForTimeout(500)
    expect(writes).toEqual([])
    await expect(page.getByTestId('rate-2')).toHaveAttribute('data-active', 'false')
    expect((await (await request.get(`/api/v1/images/${imgId}`)).json()).aesthetic_rating).toBeNull()
  } finally {
    await request.delete(`/api/v1/labels/${label.id}`)
  }
})

// Rebind Keep from the UI that exists for it, then prove both halves in the
// gallery: the new key rates, and the old digit is genuinely free rather than
// still rating as well.
test('a rebound Keep key rates in the gallery and the old digit no longer does', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `rating-keys-rebind-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')

  try {
    await page.goto('/settings')
    await page.getByRole('button', { name: 'Gallery', exact: true }).click()
    await page.getByRole('button', { name: 'Keep key 4' }).click()
    await page.keyboard.press('k')
    await expect(page.getByText('Rating key saved')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Keep key k' })).toBeVisible()

    await page.goto(`/datasets/${ds.id}/gallery`)
    await expect(page.getByTestId('gallery-tile')).toHaveCount(1)
    await page.getByTestId('select-a.png').click()
    await expect(page.getByText('1 selected')).toBeVisible()

    const writes: string[] = []
    page.on('request', (r) => { if (r.url().includes('bulk-rating')) writes.push(r.url()) })

    // The old key first, so a late write from it would be caught by the check
    // after the new key's own write has landed.
    await page.keyboard.press('4')
    await page.waitForTimeout(500)
    expect(writes).toEqual([])
    await expect(page.getByTestId('rating-badge')).toHaveCount(0)

    await page.keyboard.press('k')
    await expect(page.locator('[data-testid="rating-badge"][data-rating="4"]')).toHaveCount(1)
    expect(writes).toHaveLength(1)
  } finally {
    await restoreRatingKeys(request)
  }
})
