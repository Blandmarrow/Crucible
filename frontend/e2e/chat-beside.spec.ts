import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// "Open the analyst beside" (M12 phase 2e): the chat opens in a pane next to the page,
// stays there while the other pane follows the sidebar, is focused rather than stacked
// when asked for again, and comes back with the layout after a reload, still on the
// chat that pane was showing. See docs/dev/analyst/chat-ui.md § Open beside.

test('the analyst opens beside the page and survives a reload', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-beside-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'b1.png')
  for (const title of ['First chat', 'Second chat']) {
    const r = await request.post(`/api/v1/datasets/${ds.id}/chats`, { data: { title } })
    expect(r.status(), await r.text()).toBe(201)
  }

  await page.goto(`/datasets/${ds.id}/gallery`)
  await page.getByRole('button', { name: 'Open the analyst beside this page' }).click()
  const panes = page.getByTestId('pane-leaf')
  await expect(panes).toHaveCount(2)
  const chatPane = panes.nth(1)
  await expect(chatPane.getByRole('complementary', { name: 'Chats' })).toBeVisible()

  // This pane picks its own chat.
  await chatPane.getByRole('button', { name: /^First chat/ }).click()
  await expect(chatPane.getByLabel('Chat title')).toHaveValue('First chat')

  // The left pane follows the sidebar; the chat stays.
  await page.getByRole('link', { name: 'Stats' }).click()
  // Each pane header's first select is its page.
  await expect(panes.nth(0).getByRole('combobox').first()).toHaveValue('stats')
  await expect(chatPane.getByRole('combobox').first()).toHaveValue('chat')
  await expect(chatPane.getByLabel('Chat title')).toHaveValue('First chat')

  // Asked for again, the open chat is focused, not stacked.
  await page.getByRole('button', { name: 'Open the analyst beside this page' }).click()
  await expect(panes).toHaveCount(2)
  await expect(chatPane).toHaveAttribute('data-active', 'true')

  // A reload brings the layout back, on the chat this pane was showing.
  await page.waitForTimeout(400) // the layout is saved a moment after the last change
  await page.reload()
  await expect(panes).toHaveCount(2)
  await expect(panes.nth(1).getByLabel('Chat title')).toHaveValue('First chat')
})

test('the Health page asks the analyst beside itself', async ({ page, request }) => {
  const ds = await createDatasetViaApi(request, `e2e-beside-health-${Date.now()}`)
  await uploadViaApi(request, ds.id, 'h1.png')

  await page.goto(`/datasets/${ds.id}/health`)
  await page.getByRole('button', { name: 'Ask the analyst' }).click()
  const panes = page.getByTestId('pane-leaf')
  await expect(panes).toHaveCount(2)
  await expect(panes.nth(0).getByRole('heading', { name: 'Health', exact: true })).toBeVisible()
  await expect(panes.nth(1).getByRole('complementary', { name: 'Chats' })).toBeVisible()
})
