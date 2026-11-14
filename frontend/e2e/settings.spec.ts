import { test, expect } from '@playwright/test'

// Change a quality threshold, save, reload, and confirm it persisted server-side.
// getByLabel works here because the Quality Thresholds fields carry real label/input
// associations — as do the API Keys rows below, since SecretField adopted the same
// `useId` + `htmlFor` pattern.
test('a quality threshold persists across reload', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'Quality Thresholds' }).click()

  const field = page.getByLabel('Blur threshold')
  await field.fill('137.5')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Thresholds saved')).toBeVisible()

  await page.reload()
  await page.getByRole('button', { name: 'Quality Thresholds' }).click()
  await expect(page.getByLabel('Blur threshold')).toHaveValue('137.5')
})

// Light mode (issue #19). The page background is asserted as well as the
// attribute, so a token block that stops matching `data-theme` fails here too.
const LIGHT_BG = 'rgb(213, 216, 215)' // --bg under [data-theme="light"]
const DARK_BG = 'rgb(7, 9, 11)'

async function openAppearance(page: import('@playwright/test').Page) {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'UI Behavior' }).click()
}

test('light mode applies at once and survives a reload', async ({ page }) => {
  await openAppearance(page)
  const html = page.locator('html')
  await expect(html).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('body')).toHaveCSS('background-color', DARK_BG)

  await page.getByRole('radio', { name: /^Light/ }).check()
  await expect(html).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('body')).toHaveCSS('background-color', LIGHT_BG)

  await page.reload()
  await expect(html).toHaveAttribute('data-theme', 'light')
  await expect(page.locator('body')).toHaveCSS('background-color', LIGHT_BG)
  await page.getByRole('button', { name: 'UI Behavior' }).click()
  await expect(page.getByRole('radio', { name: /^Light/ })).toBeChecked()
})

test('Match system follows the OS setting both ways', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await openAppearance(page)
  await page.getByRole('radio', { name: /^Match system/ }).check()
  const html = page.locator('html')
  await expect(html).toHaveAttribute('data-theme', 'dark')

  await page.emulateMedia({ colorScheme: 'light' })
  await expect(html).toHaveAttribute('data-theme', 'light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(html).toHaveAttribute('data-theme', 'dark')
})

// The inline script in index.html sets the theme before the app's module runs.
// main.tsx never sets data-theme itself, so without that script the attribute
// would be missing when the document finishes parsing — the dark flash.
test('a saved light theme is set before the page finishes parsing', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('crucible-theme', 'light')
    document.addEventListener('DOMContentLoaded', () => {
      ;(window as unknown as { __themeAtParse?: string }).__themeAtParse =
        document.documentElement.dataset.theme
    })
  })
  await page.goto('/')
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __themeAtParse?: string }).__themeAtParse))
    .toBe('light')
})

// Save a key on the API Keys tab, then clear it. Asserted on the presence and absence of
// "Saved here" rather than on "Not set": if the shell running the tests exports HF_TOKEN,
// the backend inherits it and the cleared state is "Inherited from .env", not "Not set".
test('an API key saves and clears', async ({ page }) => {
  await page.goto('/settings')
  await page.getByRole('button', { name: 'API Keys' }).click()

  // The row is a labelled group, so scoping to it needs no DOM walk — which matters
  // because all three rows carry a "Save" button with the same accessible name.
  const row = page.getByRole('group', { name: 'Gelbooru user ID' })
  await row.getByLabel('Gelbooru user ID').fill('e2e-user-9876')
  await row.getByRole('button', { name: 'Save', exact: true }).click()

  await expect(row.getByText(/Saved here/)).toBeVisible()
  await expect(row.getByText('*********9876')).toBeVisible()

  await row.getByRole('button', { name: 'Clear' }).click()
  await expect(row.getByText(/Saved here/)).toBeHidden()
})
