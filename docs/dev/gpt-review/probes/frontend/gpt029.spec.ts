import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'
import * as os_ from 'os'
const SHOT = `${os_.tmpdir()}/gpt-review-probes`

async function probe(page: any, label: string, overlay: any, opener: any) {
  const out: Record<string, unknown> = {}
  out.dialogRoleCount = await page.getByRole('dialog').count()
  const focusBefore = await page.evaluate(() => document.activeElement?.tagName)
  // Tab 40 times; does focus ever land outside the overlay?
  let escaped = false
  for (let i = 0; i < 40; i++) {
    await page.keyboard.press('Tab')
    const inside = await overlay.evaluate((el: Element) => el.contains(document.activeElement))
    if (!inside) { escaped = true; break }
  }
  out.tabEscapedOverlay = escaped
  out.activeAfterTabEscape = await page.evaluate(() => document.activeElement?.tagName)
  await page.keyboard.press('Escape')
  out.stillOpenAfterEscape = await overlay.isVisible()
  out.focusBefore = focusBefore
  console.log(`GPT029 ${label}:`, JSON.stringify(out))
  return out
}

test('GPT-029: New Dataset overlay lacks dialog semantics', async ({ page, request }) => {
  await createDatasetViaApi(request, `f029-${Date.now()}`)
  await page.goto('/datasets')
  const open = page.getByRole('button', { name: 'New dataset', exact: false }).first()
  await open.click()
  const overlay = page.locator('.dialog-bg').filter({ hasText: 'New Dataset' })
  await expect(overlay).toBeVisible()
  await page.screenshot({ path: `${SHOT}/gpt029-new-dataset.png` })
  const labelAssoc = await page.getByLabel('Name', { exact: true }).count()
  console.log('GPT029 New Dataset getByLabel("Name") count:', labelAssoc)
  const o = await probe(page, 'NewDataset', overlay, open)
  expect(o.dialogRoleCount).toBe(0)
  expect(o.stillOpenAfterEscape).toBe(true)
  expect(o.tabEscapedOverlay).toBe(true)
  expect(labelAssoc).toBe(0)
  // cancel returns focus?
  await overlay.getByRole('button', { name: 'Cancel' }).click()
  await expect(overlay).toHaveCount(0)
  const active = await page.evaluate(() => document.activeElement?.tagName)
  console.log('GPT029 New Dataset focus after cancel:', active, ' opener focused:', await open.evaluate((e: Element) => e === document.activeElement))
})

test('GPT-029: Edit Captions overlay lacks dialog semantics', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `f029e-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'x.png')
  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByTestId('select-all-btn').click()
  const open = page.getByRole('button', { name: 'Edit', exact: true })
  await open.click()
  const overlay = page.locator('div.fixed.inset-0').filter({ hasText: 'Edit Captions' })
  await expect(overlay).toBeVisible()
  await page.screenshot({ path: `${SHOT}/gpt029-edit-captions.png` })
  const o = await probe(page, 'EditCaptions', overlay, open)
  expect(o.dialogRoleCount).toBe(0)
  expect(o.stillOpenAfterEscape).toBe(true)
  expect(o.tabEscapedOverlay).toBe(true)
})

test('GPT-029 control: Import folder modal (uses hook) has role=dialog and closes on Escape', async ({ page, request }) => {
  await createDatasetViaApi(request, `f029c-${Date.now()}`)
  await page.goto('/datasets')
  await page.getByRole('button', { name: 'Import folder' }).filter({ hasText: 'Import folder' }).click()
  const d = page.getByRole('dialog', { name: 'Import from folder' })
  await expect(d).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(d).toHaveCount(0)
})
