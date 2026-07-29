import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadVideoViaApi, uploadViaApi } from './helpers'

// The frame-extraction surface, GPU-free: the video strip and its selection, the
// two-step modal, the controls each step owns, and one journey that actually
// runs an extraction end to end.
//
// **Extract really is clicked**, in `an extraction runs end to end…` only. The
// earlier reading — that a real run needs scenedetect, which CI lacks — was
// wrong: without scenedetect `detect_shots` falls back to `_uniform_shots`,
// which is pure arithmetic over the clip's span and needs nothing but opencv.
// `mp4Buffer()` is 2 s, so that fallback yields a single window and the whole
// run is over in about a second. Nothing below asserts a shot *count*: with
// scenedetect installed (a dev machine after `manage.sh update`) the detector
// runs for real and may find more, and pinning the number would be pinning the
// fallback rather than the feature.
//
// CI runs with `capabilities: { shot_detection: false, deinterlace: false }`
// (only opencv is installed), and a dev machine after `manage.sh update` runs
// with both true. Every assertion below therefore checks that a control is
// *present*, never that it is enabled — the disabled-deinterlace and
// fixed-interval-sampling branches are real behaviour in one of those two worlds.
test('a video can be driven through the extraction modal', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-${Date.now()}`)
  const video = await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  // One ordinary image too, so the strip is demonstrably separate from the grid.
  await uploadViaApi(request, ds.id, 'still.png')

  await page.goto(`/datasets/${ds.id}/gallery`)

  // The strip: header, count, and a checkbox per card.
  await expect(page.getByRole('button', { name: /^Videos/ })).toBeVisible()
  const checkbox = page.getByRole('checkbox', { name: 'Select clip.mp4' })
  await expect(checkbox).toBeVisible()
  await expect(checkbox).toHaveAttribute('aria-checked', 'false')

  // Selecting reveals the batch action row without navigating away.
  await checkbox.click()
  await expect(checkbox).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByText('1 selected')).toBeVisible()
  await page.getByRole('button', { name: 'Clear' }).click()
  await expect(page.getByText('1 selected')).toHaveCount(0)

  // Into the detail view via the card body.
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await expect(page.getByRole('heading', { name: 'Video Info' })).toBeVisible()

  // Step 1. The probe is a request, not a job — wait for it rather than for a
  // spinner to disappear.
  const probe = page.waitForResponse(
    (res) => res.url().includes(`/api/v1/videos/${video.id}/probe`) && res.request().method() === 'POST',
  )
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })
  await expect(dialog).toBeVisible()
  expect((await probe).status()).toBe(200)

  // The filmstrip, the crop rect's keyboard path, the deinterlace toggle and the
  // trim track — the four things step 1 exists to offer.
  await expect(dialog.getByTestId('probe-filmstrip').getByRole('button')).not.toHaveCount(0)
  for (const field of ['x', 'y', 'w', 'h']) {
    await expect(dialog.getByRole('spinbutton', { name: `Crop ${field}` })).toBeVisible()
  }
  await expect(dialog.getByRole('button', { name: 'Use detected' })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Clear crop' })).toBeVisible()
  await expect(dialog.getByRole('checkbox', { name: /Deinterlace/ })).toBeVisible()
  await expect(dialog.getByTestId('trim-bar')).toBeVisible()

  // Step 2 — the three re-extraction modes, with New subfolder the default.
  await dialog.getByRole('button', { name: 'Next' }).click()
  const mode = (name: RegExp) => dialog.getByRole('radio', { name })
  await expect(mode(/^New subfolder/)).toBeChecked()
  await expect(mode(/^Add to/)).toBeVisible()
  await expect(mode(/^Replace/)).toBeVisible()
  // Nothing has been extracted yet, so Replace says so instead of naming a count.
  await expect(dialog.getByText('Replace (nothing to replace yet)')).toBeVisible()
  await expect(dialog.getByRole('spinbutton', { name: /Frames per shot/ })).toBeVisible()

  // Close without submitting — the expensive button is never pressed.
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toHaveCount(0)

  // No frames were produced, so the history panel stays hidden entirely.
  await expect(page.getByRole('heading', { name: 'Extracted frames' })).toHaveCount(0)
})

// The two modes resolve a subfolder differently — `new_subfolder` steps whatever
// name it is given through `_step_subfolder`, so offering an existing folder
// there would silently produce `{name}_2`. The control has to differ, and this is
// the only automated check that it does.
test('existing subfolders are offered in Add mode only', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-sub-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  // A subfolder that actually holds an image, or the dropdown lists nothing in
  // either mode and the assertion below passes for the wrong reason.
  await uploadViaApi(request, ds.id, 'still.png', 'existing')

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })
  await dialog.getByRole('button', { name: 'Next' }).click()

  const options = () => dialog.getByTestId('extract-subfolder').locator('option').allTextContents()

  // New subfolder (the default): Automatic + Name it…, nothing else.
  expect(await options()).toHaveLength(2)
  expect((await options()).join(' | ')).toContain('a new subfolder named after the video')

  await dialog.getByRole('radio', { name: /^Add to/ }).check()
  const added = await options()
  expect(added).toHaveLength(3)
  // The label changes too: an empty subfolder means something different here.
  expect(added.join(' | ')).toContain("this video's previous subfolder")
  expect(added.join(' | ')).toContain('existing')

  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

// Extraction needs no probe — only step 1's previews do — so a video whose probe
// fails must still be extractable. It was not: `Next` was gated on the probe.
test('a failed probe still reaches step 2, and says what is missing', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-noprobe-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'clip.mp4')

  await page.route('**/api/v1/videos/*/probe', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"probe failed"}' }),
  )

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })

  await expect(dialog.getByText(/could not be sampled/)).toBeVisible()
  const next = dialog.getByRole('button', { name: 'Next' })
  await expect(next).toBeEnabled()
  await next.click()
  await expect(dialog.getByRole('radio', { name: /^New subfolder/ })).toBeChecked()

  await dialog.getByRole('button', { name: 'Cancel' }).click()
})

// Pass 2 — the gallery entry point. The button is deliberately ungated: the
// selection store holds ids only and a selection can span pages and datasets, so
// the *preview endpoint* is what says what will actually run. CI has no
// scenedetect, so no lineage-carrying frame can exist here — which makes this the
// honest test of the accounting, and the reason it is never submitted.
test('the re-extract form reports what it can and cannot do', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-reextract-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'still.png')

  await page.goto(`/datasets/${ds.id}/gallery`)
  // The gallery checkbox is an overlay div on the card, not an <input>.
  await page.getByTestId('select-still.png').click()
  await expect(page.getByText('1 selected')).toBeVisible()

  const preview = page.waitForResponse(
    (res) => res.url().includes('/api/v1/videos/reextract/preview') && res.request().method() === 'POST',
  )
  const opener = page.getByRole('button', { name: 'Re-extract' })
  await opener.click()
  // A real dialog, not a bare overlay: `ReextractFramesModal` spreads
  // `useModalBehavior`, so this is `role="dialog"` and Escape closes it.
  const modal = page.getByRole('dialog', { name: 'Re-extract at full resolution' })
  await expect(modal).toBeVisible()
  expect((await preview).status()).toBe(200)

  // The accounting, straight from the endpoint that would do the work.
  await expect(modal.getByText('0 frames from 0 videos will be re-extracted')).toBeVisible()
  await expect(modal.getByText('1 skipped (not extracted from a video)')).toBeVisible()

  // The controls, and the note that pass 2 does not re-score.
  await expect(modal.getByRole('radio', { name: 'JPEG' })).toBeChecked()
  await expect(modal.getByRole('radio', { name: /PNG/ })).toBeVisible()
  await expect(modal.getByRole('spinbutton')).toHaveAttribute('placeholder', 'native')
  await expect(modal.getByText(/Quality scores were measured on the triage frames/)).toBeVisible()

  // Long edge is bounded client-side. `min="64"` on the input enforces nothing on
  // a typed value, so without this `30` reached the API and came back a raw 422.
  await modal.getByRole('spinbutton').fill('30')
  await expect(modal.getByText(/whole number between 64 and 16384/)).toBeVisible()
  await modal.getByRole('spinbutton').fill('')
  await expect(modal.getByText(/whole number between 64 and 16384/)).toHaveCount(0)

  // Nothing is eligible, so the expensive button is not even offered.
  await expect(modal.getByRole('button', { name: 'Re-extract' })).toBeDisabled()

  // Escape closes and focus returns to the button that opened it.
  await page.keyboard.press('Escape')
  await expect(modal).toHaveCount(0)
  await expect(opener).toBeFocused()
})

// The one journey that runs a real extraction. Four surfaces are folded into it
// rather than split across four specs, because none of them can exist without
// one: the progress block, the extraction-history panel, the `?source_video_id=`
// gallery deep link, and the frame's lineage line. Four specs would be four
// extractions for the coverage of one.
test('an extraction runs end to end and its frame appears with lineage', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-run-${Date.now()}`)
  const video = await uploadVideoViaApi(request, ds.id, 'clip.mp4')

  // The dataset-card video badge — two lines here rather than a spec of its own.
  // Scoped to this dataset's card: the suite shares one DB, so earlier specs
  // leave datasets carrying videos of their own.
  await page.goto('/datasets')
  await expect(
    page.getByTestId(`dataset-card-${ds.id}`).getByText('1 video', { exact: true }),
  ).toBeVisible()

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByRole('button', { name: 'clip.mp4' }).first().click()
  await page.getByRole('button', { name: 'Extract frames' }).click()
  const dialog = page.getByRole('dialog', { name: 'Extract frames' })
  await dialog.getByRole('button', { name: 'Next' }).click()
  await dialog.getByRole('button', { name: 'Extract from 1 video' }).click()

  // `extract-running` appears as soon as the 200 lands. The settled text is the
  // legal wait target for "terminal": `useVideoExtractJobs` filters terminal
  // statuses out, so the row falls back to this label the moment the job stops
  // being live, and it persists until the modal closes.
  await expect(dialog.getByTestId('extract-running')).toBeVisible()
  await expect(dialog.getByText('Finished or no longer reporting')).toBeVisible({ timeout: 30_000 })

  // That label renders for a *failed* job too, so the outcome is cross-checked
  // through the API rather than inferred from the page.
  const summary = await (await request.get(`/api/v1/videos/${video.id}/frames-summary`)).json()
  expect(summary.total).toBeGreaterThan(0)
  // `limit` is explicit: `GET /images/` defaults to 50, so a fixture that ever
  // yields more frames would silently truncate the list and fail this length
  // check for a reason that has nothing to do with extraction.
  const frames = await (
    await request.get('/api/v1/images/', {
      params: { dataset_id: ds.id, source_video_id: video.id, limit: 500 },
    })
  ).json()
  expect(frames).toHaveLength(summary.total)

  // Closing invalidates `video-frames`, so the history panel appears — hidden
  // entirely until an extraction exists, which is why it has never been covered.
  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('heading', { name: 'Extracted frames' })).toBeVisible()
  const showAll = page.getByRole('button', { name: `Show all ${summary.total} frames` })
  await expect(showAll).toBeVisible()

  // The lineage deep link: `?source_video_id=` lands on a filtered gallery.
  await showAll.click()
  await expect(page).toHaveURL(new RegExp(`source_video_id=${video.id}`))
  const tiles = page.getByTestId(/^select-/)
  await expect(tiles).toHaveCount(summary.total)

  // And from a frame back to its source — the line only a frame renders.
  await page.getByRole('button', { name: frames[0].filename }).first().click()
  const lineage = page.getByText('From', { exact: true })
  await expect(lineage).toBeVisible()
  await expect(page.getByRole('button', { name: 'clip.mp4' }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'all frames' })).toBeVisible()
})

// Re-attachment after a reload, fully stubbed — no job is ever started. The hook
// re-seeds `jobStore` from a persisted id and matches everything after that by
// `video_id`, and that seeding is exactly what a reload destroys and what no
// other test can reach: a real job finishes far too fast to reload into.
test('a running extraction is re-attached after a reload', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-extract-reattach-${Date.now()}`)
  const video = await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  const jobId = 'stub-job-running'

  const jobRow = (status: string) => ({
    id: jobId, job_type: 'video_extract', label: 'Extract - clip.mp4',
    status, dataset_id: ds.id, total_items: 3, done_items: 1,
    error_msg: null, result_data: {}, config: {},
    created_at: new Date().toISOString(), started_at: new Date().toISOString(),
    finished_at: null,
  })

  // `videoExtractJobKey(videoId)`, holding `persistentState`'s envelope.
  await page.addInitScript(
    ([key, id]) => localStorage.setItem(key, JSON.stringify({ jobId: id })),
    [`video-extract-job-${video.id}`, jobId],
  )
  await page.route(`**/api/v1/jobs/${jobId}`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(jobRow('running')) }),
  )

  await page.goto(`/datasets/${ds.id}/video/${video.id}`)

  // The synthetic event the recovery effect writes carries no phase and no
  // message, so `extractPhaseLabel` falls through to its default.
  await expect(page.getByText('Starting…')).toBeVisible()
  // Only true if the hook matched the seeded job to *this* video by `video_id`.
  await expect(page.getByRole('button', { name: 'Extract frames' }))
    .toHaveAttribute('title', 'Show the running extraction')

  // The twin: a persisted id whose job has since gone terminal shows no bar, and
  // the key is cleared so the lookup is not repeated on every future mount.
  await page.unroute(`**/api/v1/jobs/${jobId}`)
  await page.route(`**/api/v1/jobs/${jobId}`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(jobRow('completed')) }),
  )
  await page.reload()

  await expect(page.getByRole('button', { name: 'Extract frames' }))
    .toHaveAttribute('title', 'Turn this video into frames')
  await expect(page.getByText('Starting…')).toHaveCount(0)
  await expect
    .poll(() => page.evaluate((key) => localStorage.getItem(key), `video-extract-job-${video.id}`))
    .toBe(JSON.stringify({ jobId: null }))
})

// Rename and delete from the detail page. Neither has any coverage, and both are
// destructive enough that "the button exists" is not the interesting claim — the
// row afterwards is.
test('a video can be renamed and deleted from its detail page', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-video-crud-${Date.now()}`)
  const video = await uploadVideoViaApi(request, ds.id, 'clip.mp4')

  await page.goto(`/datasets/${ds.id}/video/${video.id}`)
  await page.getByTitle('Rename (the extension is kept)').click()

  // Enter, not the Save icon: the input has its own Enter handler, so the
  // unlabelled icon button never needs a selector of its own.
  const patch = page.waitForResponse(
    (res) => res.url().includes(`/videos/${video.id}/rename`) && res.request().method() === 'PATCH',
  )
  await page.getByRole('textbox').fill('renamed')
  await page.keyboard.press('Enter')
  expect((await patch).status()).toBe(200)

  // The info grid, not the toast — toasts auto-dismiss, and the grid is what a
  // user reads afterwards. `.nth(1)` because the page header carries the name too;
  // both updating is the point, so a `.first()` here would pass on a stale grid.
  await expect(page.getByText('renamed.mp4')).toHaveCount(2)
  await expect(page.getByText('renamed.mp4').nth(1)).toBeVisible()

  await page.getByRole('button', { name: 'Delete video' }).click()
  const confirm = page.getByRole('dialog', { name: 'Delete video?' })
  await expect(confirm).toBeVisible()
  await expect(confirm.getByText(/Extracted frames are not deleted/)).toBeVisible()
  await confirm.getByRole('button', { name: 'Delete' }).click()

  // A one-video dataset, so the post-delete navigation takes the deterministic
  // `paneGo` branch back to the gallery rather than stepping to a sibling.
  await expect(page).toHaveURL(new RegExp(`/datasets/${ds.id}/gallery`))
  await expect(page.getByRole('button', { name: 'renamed.mp4' })).toHaveCount(0)
})

// The `Include videos` toggle. Its backend behaviour is covered three times over
// in test_video_import_rescan_http.py; the only untested claim is the *wiring*,
// so the import POST is stubbed and the request body is the assertion. A real
// folder import here would drag in three interacting subsystems for one checkbox.
test('the import modal sends include_videos when the box is ticked', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-import-videos-${Date.now()}`)

  let body: Record<string, unknown> | null = null
  let target: string | null = null
  await page.route('**/api/v1/datasets/*/import', (route) => {
    body = route.request().postDataJSON()
    target = new URL(route.request().url()).pathname.split('/').at(-2) ?? null
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"job_id":"stub"}' })
  })

  await page.goto('/datasets')
  await page.getByTitle('Import folder').first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  // Picked explicitly: which card is first depends on the page's sort and on
  // every dataset the earlier specs left behind.
  await dialog.getByRole('combobox').first().selectOption(ds.id)

  const include = dialog.getByRole('checkbox', { name: 'Include videos' })
  await expect(dialog.getByText(/land flat/)).toHaveCount(0)
  await include.check()
  // The explanatory paragraph is the only visible consequence of ticking it.
  await expect(dialog.getByText(/land flat/)).toBeVisible()

  await dialog.getByRole('textbox').first().fill('/tmp/e2e-import-source')
  await dialog.getByRole('button', { name: 'Import' }).click()

  await expect.poll(() => body).not.toBeNull()
  expect(target).toBe(ds.id)
  expect(body!.include_videos).toBe(true)
})
