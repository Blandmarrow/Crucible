import { test, expect, type APIRequestContext } from '@playwright/test'
import { createDatasetViaApi, uploadViaApi } from './helpers'

// The analyst's tools (M12 phase 3) against the fake LLM (backend/tests/fake_llm.py,
// served by e2e/serve-llm.mjs). Settings → Analyst checks the provider's model for tool
// support (the fake calls the probe's tool itself); a chat then offers the tools,
// `[[fake:tool]]` calls the first one, and the reply shows what it looked up, opening to
// the exact text sent. "What gets sent" names the tools, and a privacy box narrows them.

const LLM = `http://127.0.0.1:${process.env.E2E_LLM_PORT ?? '8197'}`

type Recorded = { path: string; json: { tools?: { function: { name: string } }[]; tool_choice?: string; messages?: unknown[] } | null }

async function fakeProvider(request: APIRequestContext, name: string): Promise<string> {
  const r = await request.post('/api/v1/providers/', {
    data: { name, base_url: `${LLM}/v1`, default_model: 'fake-chat' },
  })
  expect(r.status(), await r.text()).toBeLessThan(300)
  return (await r.json()).id
}

test('analyst tools: a checked model looks things up, and the reply shows what it sent', async ({ page, request }) => {
  const name = `e2e-tools-${Date.now()}`
  const providerId = await fakeProvider(request, name)
  try {
    const ds = await createDatasetViaApi(request, name)
    await uploadViaApi(request, ds.id, 't1.png')

    // Settings → Analyst: check the provider's default model.
    await page.goto('/settings')
    await page.getByRole('button', { name: 'Analyst', exact: true }).click()
    const tools = page.getByTestId(`analyst-tools-${providerId}`)
    await expect(tools).toContainText('not checked')
    await tools.getByRole('button', { name: 'Check tool support' }).click()
    await expect(tools).toContainText('supported')
    await expect(tools).toContainText('Passed: the model called the tool and used its answer.')
    const all: { id: string; tools_probe: Record<string, { ok: boolean }> }[] =
      await (await request.get('/api/v1/providers/')).json()
    expect(all.find((p) => p.id === providerId)?.tools_probe['fake-chat']?.ok).toBe(true)

    // A chat on it is offered the tools; the fake calls the first, list_snapshots.
    await page.goto(`/datasets/${ds.id}/chat`)
    await page.getByRole('button', { name: 'Start a chat' }).click()
    await page.getByLabel('Provider').selectOption({ label: name })
    await expect(page.getByLabel('Provider')).toHaveValue(providerId)
    const tag = `e2e-tools-${Date.now()}`
    const box = page.getByLabel('Message')
    await box.fill(`[[fake:tool]] What snapshots are there? ${tag}`)
    await box.press('Enter')

    const reply = page.getByTestId('chat-reply').last()
    await expect(reply).toHaveAttribute('data-status', 'complete')
    await expect(reply).toContainText('The tool returned: Result of list_snapshots')
    const call = reply.getByTestId('chat-tool-call')
    await expect(call).toHaveCount(1)
    await expect(call).toHaveAttribute('data-tool', 'list_snapshots')
    await expect(call).toHaveAttribute('data-status', 'ok')
    await expect(call.locator('summary')).toHaveText('Listed the snapshots')
    // Open, it shows exactly what the result sent the model.
    await call.locator('summary').click()
    await expect(call.locator('pre')).toContainText('Result of list_snapshots')
    await expect(call.locator('pre')).toContainText('No snapshots yet.')

    // It went out with the tools, then once more with the tool's answer.
    const recorded: Recorded[] = await (await request.get(`${LLM}/_fake/requests`)).json()
    const ours = recorded.filter((q) => JSON.stringify(q.json ?? {}).includes(tag))
    expect(ours.length).toBe(2)
    expect(ours[0].json?.tool_choice).toBe('auto')
    expect(ours[0].json?.tools?.map((t) => t.function.name)).toContain('list_snapshots')
    expect(JSON.stringify(ours[1].json?.messages)).toContain('"role":"tool"')

    // "What gets sent" names the tools; turning Numbers off takes the tools that read it.
    await page.getByRole('button', { name: 'What gets sent' }).click()
    const dialog = page.getByRole('dialog', { name: 'What gets sent' })
    await expect(dialog.getByTestId('context-tools')).toContainText('list_snapshots')
    await dialog.getByRole('checkbox', { name: /^Numbers/ }).uncheck()
    await expect(dialog.getByTestId('context-tools')).not.toContainText('list_snapshots')
    await expect(dialog.getByTestId('context-tools')).toContainText('sample_captions')
    await dialog.getByRole('button', { name: 'Follow the provider\'s defaults again' }).click()
    await expect(dialog.getByTestId('context-tools')).toContainText('list_snapshots')
    await dialog.getByRole('button', { name: 'Close' }).click()

    // The stored reply carries its call, so a reload shows it too.
    await page.reload()
    await expect(page.getByTestId('chat-reply').last().getByTestId('chat-tool-call')).toHaveAttribute('data-tool', 'list_snapshots')
  } finally {
    await request.delete(`/api/v1/providers/${providerId}`)
  }
})
