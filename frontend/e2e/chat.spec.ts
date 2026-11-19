import { test, expect, type APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The analyst chat (M12 phase 2c) against the fake LLM (backend/tests/fake_llm.py,
// served by e2e/serve-llm.mjs): a reply streams in and gets its related findings, a
// stalled reply is stopped with its text kept, and "What gets sent" changes the
// chat's privacy. A second test drives Settings → Analyst.

const LLM = `http://127.0.0.1:${process.env.E2E_LLM_PORT ?? '8197'}`
const FAKE_REPLY = 'A plain test image, described by the fake LLM server.'

type Recorded = { path: string; json: { stream?: boolean; messages?: { content: unknown }[] } | null }

async function fakeProvider(request: APIRequestContext, name: string): Promise<string> {
  const r = await request.post('/api/v1/providers/', {
    data: { name, base_url: `${LLM}/v1`, default_model: 'fake-chat' },
  })
  expect(r.status(), await r.text()).toBeLessThan(300)
  return (await r.json()).id
}

test('analyst chat: a reply streams, a stalled one is stopped, privacy is the chat\'s own', async ({ page, request }) => {
  const name = `e2e-chat-${Date.now()}`
  const providerId = await fakeProvider(request, name)
  try {
    const ds = await createDatasetViaApi(request, name)
    await uploadViaApi(request, ds.id, 'c1.png')
    await uploadViaApi(request, ds.id, 'c2.png')

    await page.goto(`/datasets/${ds.id}/chat`)
    await page.getByRole('button', { name: 'Start a chat' }).click()
    // Other specs may leave providers behind: pick this one by name.
    await page.getByLabel('Provider').selectOption({ label: name })
    await expect(page.getByLabel('Provider')).toHaveValue(providerId)

    // A plain reply, streamed and stored. The box clears once the server accepts it.
    const tag = `e2e-${Date.now()}`
    const box = page.getByLabel('Message')
    await box.fill(`What should I fix first? ${tag}`)
    await box.press('Enter')
    await expect(box).toHaveValue('')
    await expect(page.getByTestId('chat-user-message').last()).toContainText(tag)
    const reply = page.getByTestId('chat-reply').last()
    await expect(reply).toContainText(FAKE_REPLY)
    await expect(reply).toHaveAttribute('data-status', 'complete')
    // Crucible's own strip under the reply: cards built from the report, not the model.
    await expect(reply.getByRole('list', { name: 'Related findings' }).getByRole('listitem').first()).toBeVisible()
    // The first message named the chat.
    await expect(page.getByLabel('Chat title')).toHaveValue(new RegExp(tag))

    // It went out as a stream, with the message in it.
    const recorded: Recorded[] = await (await request.get(`${LLM}/_fake/requests`)).json()
    const ours = recorded.filter((q) => JSON.stringify(q.json ?? {}).includes(tag))
    expect(ours.length).toBeGreaterThanOrEqual(1)
    expect(ours[0].json?.stream).toBe(true)

    // A reply that stalls after its first pieces: Stop keeps what arrived.
    await box.fill('[[fake:stall]] and keep going')
    await box.press('Enter')
    const stalled = page.getByTestId('chat-reply').last()
    await expect(stalled).toHaveAttribute('data-status', 'writing')
    await expect(stalled).toContainText('A plain')
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await expect(stalled.getByTestId('chat-stopped')).toBeVisible()
    await expect(stalled).toHaveAttribute('data-status', 'cancelled')
    await expect(stalled).toContainText('A plain')
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible()

    // A reload mid-reply picks the stream back up: the server kept the reply going.
    await box.fill('[[fake:stall]] once more')
    await box.press('Enter')
    await expect(page.getByTestId('chat-reply').last()).toContainText('A plain')
    await page.reload()
    const resumed = page.getByTestId('chat-reply').last()
    await expect(resumed).toHaveAttribute('data-status', 'writing')
    await expect(resumed).toContainText('A plain')
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await expect(resumed).toHaveAttribute('data-status', 'cancelled')
    await expect(resumed).toContainText('A plain')

    // "What gets sent": the exact context, and the chat's own privacy choice.
    await page.getByRole('button', { name: 'What gets sent' }).click()
    const dialog = page.getByRole('dialog', { name: 'What gets sent' })
    await expect(dialog.getByTestId('context-preview')).toContainText('System message and context')
    await expect(dialog).toContainText('follows the provider')
    const captions = dialog.getByRole('checkbox', { name: /^Captions/ })
    await expect(captions).toBeChecked() // a free provider gets everything
    await captions.uncheck()
    await expect(dialog).toContainText('This chat has its own choice')
    await expect(captions).not.toBeChecked()
    await dialog.getByRole('button', { name: 'Follow the provider\'s defaults again' }).click()
    await expect(dialog).toContainText('follows the provider')
    await expect(captions).toBeChecked()
    // No tools yet: the chat's model was never checked. Checking it here offers them.
    await expect(dialog.getByTestId('context-tools')).toContainText('No tools')
    await expect(dialog.getByTestId('tool-check-verdict')).toHaveText('not checked')
    await dialog.getByRole('button', { name: 'Check tool support' }).click()
    await expect(dialog.getByTestId('context-tools')).toContainText('list_snapshots')
    await dialog.getByRole('button', { name: 'Close' }).click()

    // The stored chat agrees with the page.
    const chats = await (await request.get(`/api/v1/datasets/${ds.id}/chats`)).json()
    expect(chats).toHaveLength(1)
    expect(chats[0].message_count).toBe(6)
    expect(chats[0].privacy).toBeNull()
  } finally {
    await request.delete(`/api/v1/providers/${providerId}`)
  }
})

test('settings → analyst: budgets and a provider\'s privacy', async ({ page, request }) => {
  const name = `e2e-analyst-settings-${Date.now()}`
  const providerId = await fakeProvider(request, name)
  try {
    await page.goto('/settings')
    await page.getByRole('button', { name: 'Analyst', exact: true }).click()

    const row = page.getByTestId(`analyst-privacy-${providerId}`)
    await expect(row).toContainText('The free defaults')
    await row.getByRole('checkbox', { name: 'Captions' }).uncheck()
    await expect(row).toContainText('Your choice')
    // The page shows the choice at once; the save lands a moment later.
    const stored = async () => {
      const all: { id: string; analyst_privacy: Record<string, boolean> | null }[] =
        await (await request.get('/api/v1/providers/')).json()
      return all.find((x) => x.id === providerId)?.analyst_privacy ?? null
    }
    await expect.poll(async () => (await stored())?.captions).toBe(false)
    await expect(row.getByRole('button', { name: 'Back to the defaults' })).toBeEnabled()

    await row.getByRole('button', { name: 'Back to the defaults' }).click()
    await expect(row).toContainText('The free defaults')
    await expect(row.getByRole('checkbox', { name: 'Captions' })).toBeChecked()
    await expect.poll(stored).toBeNull()

    const perChat = page.getByLabel('Per chat (tokens)')
    await perChat.fill('5000')
    await perChat.blur()
    await page.getByRole('button', { name: 'Save budgets' }).click()
    await expect(page.getByText('Budgets saved')).toBeVisible()
    const t = await (await request.get('/api/v1/settings/thresholds')).json()
    expect(t.analyst_chat_budget_tokens).toBe(5000)
  } finally {
    await request.patch('/api/v1/settings/thresholds', { data: { analyst_chat_budget_tokens: 200_000 } })
    await request.delete(`/api/v1/providers/${providerId}`)
  }
})
