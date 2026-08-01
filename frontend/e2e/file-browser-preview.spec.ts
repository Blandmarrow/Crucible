import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadVideoViaApi } from './helpers'

// The File Browser's video preview and the handle it holds.
//
// `/filesystem/preview` is a FileResponse, and a `<video preload="metadata">`
// never finishes consuming that body — so on Windows the open player is what
// blocks a rename of the very file it is showing (PM-021), and `filesystem.py`
// is not converted to the retrying helpers, so that surfaces as a 500 rather
// than a 409. The client half is the whole defence here: the element must be
// *unmounted* before the mutation goes out, which is what `released` does.
//
// Rename is the case that was missed: it is not one of the panel's modals, it
// fires straight from the inline input. POSIX cannot see the failure, so this
// spec pins the observable client-side behaviour instead.

test('opening the inline rename releases the video preview', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-fsprev-${Date.now()}`)
  await uploadVideoViaApi(request, ds.id, 'clip.mp4')
  const folder = ds.folder_path.split(/[\\/]/).pop()!

  await page.goto('/file-browser')
  // The browser opens on datasets_dir, so the dataset's own folder is a row here.
  // Single-click navigates into a directory; only files select.
  await page.getByText(folder, { exact: true }).click()
  await page.getByText('videos', { exact: true }).click()
  // `.first()` because the preview panel repeats the name once the row is
  // selected, and the list is rendered ahead of the panel.
  const row = page.getByText('clip.mp4', { exact: true }).first()
  await row.click()

  // Selected: the preview panel mounts a real player against /preview.
  const player = page.locator('video')
  await expect(player).toHaveCount(1)
  await expect(page.getByText('Preview paused')).toHaveCount(0)

  await row.click({ button: 'right' })
  await page.getByRole('button', { name: 'Rename' }).click()

  // The element is gone, not merely `src`-cleared: clearing `src` leaves it
  // attached, still holding the connection, and fires a spurious `error` event
  // that would paint a playback failure over a file that is fine.
  await expect(player).toHaveCount(0)
  await expect(page.getByText('Preview paused')).toBeVisible()
})
