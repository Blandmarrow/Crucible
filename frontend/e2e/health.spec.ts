import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'
import { GALLERY_DEFAULT_CAPTION_KEY } from '../src/constants/storage'

// The Health page (M12 phase 1): open the report, dismiss a finding with a reason,
// see it move to Dismissed and into the notes, then open another finding's images.
test('health report: dismiss a finding and open another one\'s images', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-health-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'h1.png')
  await uploadViaApi(request, ds.id, 'h2.png')

  await page.goto(`/datasets/${ds.id}/health`)
  await expect(page.getByRole('heading', { name: 'Health', exact: true })).toBeVisible()

  // Fresh uploads have no captions, and no goal is set.
  const uncaptioned = page.getByTestId('finding-uncaptioned')
  await expect(uncaptioned).toBeVisible()
  await expect(uncaptioned).toContainText('2 of 2 images')
  await expect(page.getByTestId('finding-no_goal')).toBeVisible()

  // Its accessible name carries the finding's title, so a screen reader can tell rows apart.
  await uncaptioned.getByRole('button', { name: 'Dismiss: Images with no caption' }).click()
  const dialog = page.getByRole('dialog', { name: /Dismiss/ })
  await dialog.getByLabel('Reason').fill('captions come from the next run')
  await dialog.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await expect(dialog).toHaveCount(0)

  await expect(page.getByTestId('finding-uncaptioned')).toHaveCount(0)
  const dismissed = page.getByTestId('dismissed')
  await dismissed.locator('summary').click()
  await expect(dismissed).toContainText('captions come from the next run')
  // The dismissal is a preference note.
  const preferences = page.getByRole('region', { name: 'Preferences' })
  await expect(preferences.getByTestId('note-item')).toContainText('captions come from the next run')

  // Another finding's images, in the modal. The uploads are 16 px, under the default target.
  const lowRes = page.getByTestId('finding-low_resolution')
  await lowRes.getByRole('button', { name: 'Show images' }).click()
  const modalTitle = page.getByRole('heading', { level: 3, name: /below the target resolution/ })
  await expect(modalTitle).toBeVisible()
  // The modal's own count line, under its title (the row above says "2 of 2 images").
  await expect(modalTitle.locator('xpath=..')).toContainText('2 images')
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(2)

  // Restoring the dismissal brings the finding back.
  await page.keyboard.press('Escape')
  await dismissed.getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByTestId('finding-uncaptioned')).toBeVisible()
})

// The Health sidebar entry and the badge's source: a report built for a dataset
// stores its counts, and the Datasets card shows the worst level.
test('a built report puts a health badge on the dataset card', async ({ page, request }) => {
  const name = `e2e-health-badge-${Date.now()}`
  const ds = await createDatasetViaApi(request, name)
  await uploadViaApi(request, ds.id, 'b.png')
  // Building the report (here through the API) writes the card's summary.
  const report = await (await request.get(`/api/v1/datasets/${ds.id}/health`)).json()
  expect(report.summary.warn).toBeGreaterThan(0)

  await page.goto('/datasets')
  const card = page.getByTestId(`dataset-card-${ds.id}`)
  await expect(card.getByTestId('health-badge')).toContainText('warning')
})

// The `?filter=` deep link (M12 phase 1c): a finding opens its images in the
// gallery. Its caption-token filter has no toolbar control, so it lands as a chip.
// One short and one long caption make the link discriminate: a gallery that dropped
// the filter would show both tiles.
test('Open in gallery applies the finding as a chip, once, and strips the link', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-health-link-${Date.now()}`)
  const short = await uploadViaApi(request, ds.id, 'short.png')
  const long = await uploadViaApi(request, ds.id, 'long.png')
  const listed = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const idOf = (name: string) => listed.find((i: { filename: string }) => i.filename === name).id
  await request.put(`/api/v1/captions/image/${idOf(short)}`, { data: { caption_text: 'fox' } })
  await request.put(`/api/v1/captions/image/${idOf(long)}`, {
    data: { caption_text: 'a red fox standing in deep snow beside a frozen river at dusk' },
  })

  await page.goto(`/datasets/${ds.id}/health`)
  const shortRow = page.getByTestId('finding-short_captions')
  await expect(shortRow).toContainText('1 of 2 images')
  await shortRow.getByRole('button', { name: 'Open in gallery: Very short captions' }).click()

  await expect(page).toHaveURL(new RegExp(`/datasets/${ds.id}/gallery$`))
  await expect(page.getByTestId('link-filter')).toContainText('Very short captions')
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(1)

  // Removing the chip shows the rest, and a reload does not bring the link back.
  // The remembered state is written on a debounce and a reload runs no unmount
  // flush, so wait until the blob no longer holds the chip before reloading.
  await page.getByRole('button', { name: 'Remove filter: Very short captions' }).click()
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(2)
  await expect.poll(() => page.evaluate((key) => {
    const raw = localStorage.getItem(key)
    return raw === null ? 'unwritten' : JSON.stringify(JSON.parse(raw).linkFilter ?? null)
  }, `gallery-state-${ds.id}`)).toBe('null')
  await page.reload()
  await expect(page.getByTestId('link-filter')).toHaveCount(0)
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(2)
})

// A link that fails validation is ignored whole, and still stripped.
test('a malformed ?filter= link changes nothing', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-health-badlink-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  await page.goto(`/datasets/${ds.id}/gallery?filter=not-a-link`)
  await expect(page).toHaveURL(new RegExp(`/datasets/${ds.id}/gallery$`))
  await expect(page.getByTestId('link-filter')).toHaveCount(0)
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(1)
})

// A finding's "any caption" survives a round trip to the detail view even when the
// Settings default caption filter says otherwise: a remembered "all" used to be
// stored as "never chosen", so the default replaced it on the way back and a
// captioned image dropped out of the finding's grid.
test('a link keeps its images across the detail view round trip', async ({ page, request }) => {
  await page.addInitScript((key) => localStorage.setItem(key, 'uncaptioned'), GALLERY_DEFAULT_CAPTION_KEY)
  const ds = await createDatasetViaApi(request, `e2e-health-trip-${Date.now()}`)
  const a = await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')
  const listed = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const aId = listed.find((i: { filename: string }) => i.filename === a).id
  await request.put(`/api/v1/captions/image/${aId}`, { data: { caption_text: 'a captioned image of a fox' } })

  // Both uploads are 16 px, under the default target, captioned or not.
  await page.goto(`/datasets/${ds.id}/health`)
  await page.getByTestId('finding-low_resolution')
    .getByRole('button', { name: /^Open in gallery: Images below the target resolution/ }).click()
  await expect(page.getByTestId('link-filter')).toContainText('below the target resolution')
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(2)

  await page.locator('img[src*="/thumbnail"]').first().click()
  await expect(page).toHaveURL(/\/image\//)
  await page.goBack()
  await expect(page.getByTestId('link-filter')).toContainText('below the target resolution')
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(2)
})

// A link whose keys a toolbar control can show lands in that control, with no chip.
test('a link the caption select can hold sets the select and shows no chip', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-health-ctl-${Date.now()}`)
  const a = await uploadViaApi(request, ds.id, 'a.png')
  await uploadViaApi(request, ds.id, 'b.png')
  const listed = await (await request.get('/api/v1/images/', { params: { dataset_id: ds.id } })).json()
  const aId = listed.find((i: { filename: string }) => i.filename === a).id
  await request.put(`/api/v1/captions/image/${aId}`, { data: { caption_text: 'a captioned image of a fox' } })

  await page.goto(`/datasets/${ds.id}/health`)
  await page.getByTestId('finding-uncaptioned')
    .getByRole('button', { name: 'Open in gallery: Images with no caption' }).click()
  await expect(page).toHaveURL(new RegExp(`/datasets/${ds.id}/gallery$`))
  await expect(page.getByTestId('link-filter')).toHaveCount(0)
  await expect(page.locator('img[src*="/thumbnail"]')).toHaveCount(1)
})
