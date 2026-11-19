import { test, expect } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// Note proposals in the analyst chat (M12 phase 2d), against the fake LLM: "Ask it to
// note this" sends the narrow follow-up, and the fake's `notes` reply shows its block as
// suggestions to accept, undo and reject, never as text. See
// docs/dev/analyst/proposals.md.

const LLM = `http://127.0.0.1:${process.env.E2E_LLM_PORT ?? '8197'}`

test('analyst chat: suggested notes are accepted, undone and rejected', async ({ page, request }) => {
  const name = `e2e-chat-notes-${Date.now()}`
  const r = await request.post('/api/v1/providers/', {
    data: { name, base_url: `${LLM}/v1`, default_model: 'fake-chat' },
  })
  expect(r.status(), await r.text()).toBeLessThan(300)
  const providerId: string = (await r.json()).id
  try {
    const ds = await createDatasetViaApi(request, name)
    await uploadViaApi(request, ds.id, 'n1.png')
    const created = await request.post(`/api/v1/datasets/${ds.id}/chats`, { data: { provider_id: providerId } })
    expect(created.status(), await created.text()).toBe(201)
    const notes = async () =>
      (await (await request.get(`/api/v1/datasets/${ds.id}/notes`)).json()).items as { section: string; text: string }[]

    await page.goto(`/datasets/${ds.id}/chat`)
    const box = page.getByLabel('Message')

    // A plain reply offers to ask for notes; that sends the narrow follow-up.
    await box.fill('What matters most?')
    await box.press('Enter')
    await expect(page.getByTestId('chat-reply').last()).toHaveAttribute('data-status', 'complete')
    await page.getByRole('button', { name: 'Ask it to note this' }).click()
    await expect(page.getByTestId('chat-user-message').last()).toContainText('crucible-notes block')
    await expect(page.getByTestId('chat-reply').last()).toHaveAttribute('data-status', 'complete')

    // The fake's notes reply: its block shows as two suggestions, never as text.
    await box.fill('[[fake:notes]] Note what we agreed')
    await box.press('Enter')
    const reply = page.getByTestId('chat-reply').last()
    await expect(reply).toHaveAttribute('data-status', 'complete')
    await expect(reply).not.toContainText('crucible-notes')
    const suggestions = reply.getByTestId('proposal')
    await expect(suggestions).toHaveCount(2)
    await expect(suggestions.nth(0)).toContainText('Dropped images under 768px')
    await expect(page.getByRole('button', { name: 'Ask it to note this' })).toHaveCount(0)

    await suggestions.nth(0).getByRole('button', { name: 'Accept' }).click()
    await expect(suggestions.nth(0)).toHaveAttribute('data-status', 'accepted')
    await expect.poll(async () => (await notes()).map((n) => n.text)).toContain('Dropped images under 768px; the LoRA trains at 1024.')

    await suggestions.nth(0).getByRole('button', { name: 'Undo' }).click()
    await expect(suggestions.nth(0)).toHaveAttribute('data-status', 'pending')
    await expect.poll(async () => (await notes()).length).toBe(0)

    await suggestions.nth(1).getByRole('button', { name: 'Reject' }).click()
    await expect(suggestions.nth(1)).toHaveAttribute('data-status', 'rejected')
    expect((await notes()).length).toBe(0)

    // Accepted again, the note is on the Health page with the others.
    await suggestions.nth(0).getByRole('button', { name: 'Accept' }).click()
    await expect(suggestions.nth(0)).toHaveAttribute('data-status', 'accepted')
    await page.goto(`/datasets/${ds.id}/health`)
    await expect(page.getByRole('region', { name: 'Decisions' }).getByTestId('note-item')).toContainText('Dropped images under 768px')
  } finally {
    await request.delete(`/api/v1/providers/${providerId}`)
  }
})
