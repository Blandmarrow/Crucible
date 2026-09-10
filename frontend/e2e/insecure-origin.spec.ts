import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Crucible is routinely opened on `http://0.0.0.0:8000` or over a LAN IP, and
// neither is a secure context — Chromium installs `crypto.randomUUID` and
// `navigator.clipboard` on HTTPS, `localhost` and `127.0.0.1` and nowhere else.
// Saving a caption preset and hitting "Copy Errors" both threw a synchronous
// TypeError there (issue #93; PM-024). Playwright serves `http://localhost:8199`
// — secure by fiat — so no gate here can observe the real origin and the missing
// APIs have to be faked.
//
// `delete navigator.clipboard` is the trap: it deletes a non-existent *own*
// property, returns `true`, and changes nothing. Both APIs live on a PROTOTYPE
// (`Navigator.prototype.clipboard` is an accessor, `Crypto.prototype.randomUUID`
// a method) and both are `configurable: true`. Deleting there is both what works
// and what a real insecure origin looks like: never installed.
//
// This fixture stays local rather than moving to `e2e/helpers.ts`, whose header
// scopes it to API-side setup.
async function stripSecureContextApis(page: Page) {
  await page.addInitScript(() => {
    delete (Navigator.prototype as unknown as Record<string, unknown>).clipboard
    delete (Crypto.prototype as unknown as Record<string, unknown>).randomUUID
  })
}

test('a caption preset saves with crypto.randomUUID gone', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `insecure-preset-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'a.png')
  await stripSecureContextApis(page)

  await page.goto(`/datasets/${ds.id}/captioning`)

  // The precondition, asserted rather than assumed: without it a fixture that
  // silently stopped working would leave two green tests proving nothing.
  expect(await page.evaluate(() => typeof (crypto as Crypto).randomUUID)).toBe('undefined')

  await page.getByRole('button', { name: '+ Save current as preset' }).click()
  await page.getByPlaceholder('Preset name…').fill('insecure-origin preset')
  await page.getByRole('button', { name: 'OK', exact: true }).click()

  // The user-visible outcome: the preset row is in the list. The old code threw
  // before `set()`, so nothing was appended.
  await expect(page.getByRole('button', { name: /insecure-origin preset/ })).toBeVisible()
  await expect(page.getByText('No presets saved yet.')).toHaveCount(0)
})

test('Copy Errors copies with navigator.clipboard gone', async ({ page }) => {
  await stripSecureContextApis(page)
  await page.goto('/datasets')
  expect(await page.evaluate(() => navigator.clipboard)).toBeUndefined()

  // Seed the console the way the app does — its own `error` listener — so the
  // panel opens with exactly one entry to copy.
  await page.evaluate(() => {
    window.dispatchEvent(new ErrorEvent('error', {
      message: 'seeded for the insecure-origin spec',
      filename: 'insecure-origin.spec.ts',
      lineno: 1,
      colno: 1,
    }))
  })
  await expect(page.getByText('Error Console (1)')).toBeVisible()

  const copy = page.getByRole('button', { name: 'Copy Errors' })
  await copy.click()

  // Two assertions, and the second is the regression: the old handler's TypeError
  // was itself caught by the console's `error` listener, so a broken Copy Errors
  // button files a report about itself and the count climbs to 2.
  await expect(page.getByRole('button', { name: 'Copied' })).toBeVisible()
  await expect(page.getByText('Error Console (1)')).toBeVisible()

  // Clipboard *contents* are deliberately not asserted: reading them needs
  // `clipboard-read` permission plus reaching around the very API this spec
  // removed, and is the flakiest thing available on Linux CI. "Copied" already
  // means `document.execCommand("copy")` returned true.
})
