import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { createDatasetViaApi } from './helpers'

// M6 §8c: the prompt library as a side panel on the ComfyUI page. Every journey uses
// buttons only (the drag-and-drop stage has its own spec). The library is global, so
// each test seeds categories under a name that no other test shares.

const WORKFLOW = {
  '6': { class_type: 'CLIPTextEncode', inputs: { text: 'template prompt' } },
  '9': { class_type: 'SaveImage', inputs: {} },
}

interface LibraryRow { id: string; category: string; text: string }

async function createPlanViaApi(request: APIRequestContext, datasetId: string, name: string) {
  const r = await request.post('/api/v1/comfy/plans', {
    data: {
      dataset_id: datasetId,
      name,
      workflow_json: WORKFLOW,
      pinned_params: [{ node_id: '6', input: 'text', alias: 'prompt', is_prompt: true }],
      output_node_ids: ['9'],
    },
  })
  expect(r.status(), await r.text()).toBe(200)
  return r.json()
}

async function addRowsViaApi(request: APIRequestContext, planId: string, lines: string[], subfolder = '') {
  const r = await request.post(`/api/v1/comfy/plans/${planId}/rows/bulk`, { data: { lines, subfolder } })
  expect(r.status(), await r.text()).toBe(200)
}

async function rowsOf(request: APIRequestContext, planId: string) {
  return (await request.get(`/api/v1/comfy/plans/${planId}/rows`)).json()
}

async function seedLibrary(request: APIRequestContext, category: string, prompts: string[]) {
  const r = await request.post('/api/v1/comfy/library', { data: { category, prompts } })
  expect(r.status(), await r.text()).toBe(200)
}

async function libraryOf(request: APIRequestContext, prefix: string): Promise<LibraryRow[]> {
  const all: { prompts: LibraryRow[] } = await (await request.get('/api/v1/comfy/library')).json()
  return all.prompts.filter((p) => p.category.startsWith(prefix))
}

async function openLibrary(page: Page, datasetId: string) {
  await page.goto(`/datasets/${datasetId}/comfy`)
  const toggle = page.getByRole('button', { name: 'Library', exact: true })
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  const panel = page.getByTestId('comfy-library-panel')
  await expect(panel).toBeVisible()
  return panel
}

test.describe('the prompt library side panel', () => {
  let stamp: string
  let prefix: string

  test.beforeEach(() => {
    stamp = `${Date.now()}${Math.floor(Math.random() * 1000)}`
    prefix = `portraits-${stamp}`
  })

  test.afterEach(async ({ request }) => {
    const left = await libraryOf(request, prefix)
    if (left.length > 0) {
      await request.post('/api/v1/comfy/library/delete', { data: { ids: left.map((p) => p.id) } })
    }
  })

  test('the tree nests categories with subtree counts, and collapsing hides the children', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-tree-${stamp}`)
    await createPlanViaApi(request, ds.id, 'tree plan')
    await seedLibrary(request, `${prefix}/studio`, ['studio light portrait'])
    await seedLibrary(request, `${prefix}/outdoor`, ['outdoor window portrait'])

    const panel = await openLibrary(page, ds.id)
    const cat = (path: string) => panel.locator(`[data-testid="comfy-lib-category"][data-category="${path}"]`)

    await expect(cat(prefix)).toBeVisible()
    await expect(cat(prefix).locator('.comfy-rail-count')).toHaveText('2')
    await expect(cat(`${prefix}/studio`)).toBeVisible()
    await expect(cat(`${prefix}/outdoor`)).toBeVisible()

    await panel.getByRole('button', { name: `Collapse ${prefix}`, exact: true }).click()
    await expect(cat(`${prefix}/studio`)).toHaveCount(0)
    await expect(cat(`${prefix}/outdoor`)).toHaveCount(0)

    await panel.getByRole('button', { name: `Expand ${prefix}`, exact: true }).click()
    await expect(cat(`${prefix}/studio`)).toBeVisible()
  })

  test('a prompt edits in place, and an edit to a duplicate shows the error and changes nothing', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-edit-${stamp}`)
    await createPlanViaApi(request, ds.id, 'edit plan')
    await seedLibrary(request, `${prefix}/studio`, ['first prompt', 'second prompt'])
    const seeded = await libraryOf(request, prefix)
    const first = seeded.find((p) => p.text === 'first prompt')!
    const second = seeded.find((p) => p.text === 'second prompt')!

    const panel = await openLibrary(page, ds.id)
    const card = (id: string) => panel.locator(`[data-testid="comfy-lib-prompt"][data-prompt-id="${id}"]`)

    // A duplicate of its neighbour in the same category is refused.
    await card(second.id).getByRole('button', { name: 'Edit prompt' }).click()
    await card(second.id).getByRole('textbox', { name: 'Prompt text' }).fill('first prompt')
    await card(second.id).getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText(/already|duplicate/i).first()).toBeVisible()
    await expect(card(second.id).getByRole('textbox', { name: 'Prompt text' })).toBeVisible()
    expect((await libraryOf(request, prefix)).map((p) => p.text).sort()).toEqual(['first prompt', 'second prompt'])

    // Escape drops the edit; a fresh text saves with Enter.
    await card(second.id).getByRole('textbox', { name: 'Prompt text' }).press('Escape')
    await expect(card(second.id).getByText('second prompt')).toBeVisible()
    await card(first.id).getByRole('button', { name: 'Edit prompt' }).click()
    const box = card(first.id).getByRole('textbox', { name: 'Prompt text' })
    await box.fill('first prompt, edited')
    await box.press('Enter')
    await expect(card(first.id).getByText('first prompt, edited')).toBeVisible()
    await expect
      .poll(async () => (await libraryOf(request, prefix)).map((p) => p.text).sort())
      .toEqual(['first prompt, edited', 'second prompt'])
  })

  test('selected prompts are added to the plan in the folder chosen in the select', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-add-${stamp}`)
    const plan = await createPlanViaApi(request, ds.id, 'add plan')
    const folderRes = await request.post(`/api/v1/comfy/plans/${plan.id}/folders`, { data: { path: 'knights' } })
    expect(folderRes.status(), await folderRes.text()).toBe(200)
    await seedLibrary(request, `${prefix}/studio`, [`a knight in a fen ${stamp}`, `a monk on a ridge ${stamp}`])

    const panel = await openLibrary(page, ds.id)
    // The library is global: count only the cards this test seeded.
    const cards = panel.getByTestId('comfy-lib-prompt').filter({ hasText: stamp })
    await expect(cards).toHaveCount(2)
    await cards.nth(0).getByRole('checkbox', { name: 'Select prompt' }).check()
    await cards.nth(1).getByRole('checkbox', { name: 'Select prompt' }).check()

    await panel.getByRole('combobox', { name: 'Add to folder' }).selectOption('knights')
    await panel.getByRole('button', { name: 'Add 2 to plan' }).click()

    await expect
      .poll(async () => (await rowsOf(request, plan.id)).map((r: { subfolder: string }) => r.subfolder))
      .toEqual(['knights', 'knights'])
    await expect(panel.getByRole('button', { name: 'Add 2 to plan' })).toHaveCount(0)
  })

  test('renaming a category into an existing one merges them', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-rename-${stamp}`)
    await createPlanViaApi(request, ds.id, 'rename plan')
    await seedLibrary(request, `${prefix}/studio`, ['studio light', 'shared text'])
    await seedLibrary(request, `${prefix}/outdoor`, ['window light', 'shared text'])

    const panel = await openLibrary(page, ds.id)
    await panel.getByRole('button', { name: `Category actions for ${prefix}/outdoor` }).click()
    await page.getByRole('menuitem', { name: 'Rename…' }).click()
    await page.getByRole('textbox', { name: 'Category name' }).fill('studio')
    await page.getByRole('button', { name: 'Rename', exact: true }).click()

    await expect
      .poll(async () => {
        const rows = await libraryOf(request, prefix)
        return {
          categories: [...new Set(rows.map((p) => p.category))],
          texts: rows.map((p) => p.text).sort(),
        }
      })
      .toEqual({ categories: [`${prefix}/studio`], texts: ['shared text', 'studio light', 'window light'] })
    await expect(page.getByText(/merged/).first()).toBeVisible()
  })
})

test.describe('library button routes', () => {
  let stamp: string
  let prefix: string

  test.beforeEach(() => {
    stamp = `${Date.now()}${Math.floor(Math.random() * 1000)}`
    prefix = `routes-${stamp}`
  })

  test.afterEach(async ({ request }) => {
    await request.post('/api/v1/comfy/library/categories/delete', { data: { path: prefix } })
  })

  const placed = async (request: APIRequestContext) =>
    (await libraryOf(request, prefix)).map((p) => `${p.category}|${p.text}`).sort()

  test('a ticked prompt moves to another category with Move to', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-pmove-${stamp}`)
    await createPlanViaApi(request, ds.id, 'pmove plan')
    await seedLibrary(request, `${prefix}/a`, [`stay ${stamp}`, `go ${stamp}`])

    const panel = await openLibrary(page, ds.id)
    await panel.getByTestId('comfy-lib-prompt').filter({ hasText: `go ${stamp}` }).getByRole('checkbox', { name: 'Select prompt' }).check()
    await panel.getByRole('button', { name: 'Move to…' }).click()
    await panel.getByLabel('Move to category').fill(`${prefix}/b`)
    await panel.getByRole('button', { name: 'Move', exact: true }).click()

    await expect.poll(() => placed(request)).toEqual([
      `${prefix}/a|stay ${stamp}`,
      `${prefix}/b|go ${stamp}`,
    ])
  })

  test('a category moves under another from its menu', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-cmove-${stamp}`)
    await createPlanViaApi(request, ds.id, 'cmove plan')
    await seedLibrary(request, `${prefix}/outdoor`, [`window light ${stamp}`])
    await seedLibrary(request, `${prefix}/studio`, [`softbox ${stamp}`])

    const panel = await openLibrary(page, ds.id)
    await panel.getByRole('button', { name: `Category actions for ${prefix}/outdoor` }).click()
    await page.getByRole('menuitem', { name: 'Move to…' }).click()
    const dialog = page.getByRole('dialog', { name: new RegExp(`Move subfolder ${prefix}/outdoor`) })
    await dialog.getByTitle(`${prefix}/studio`, { exact: true }).click()
    await dialog.getByRole('button', { name: 'Move', exact: true }).click()

    await expect.poll(() => placed(request)).toEqual([
      `${prefix}/studio/outdoor|window light ${stamp}`,
      `${prefix}/studio|softbox ${stamp}`,
    ])
  })

  test('deleting a category counts its whole subtree even under a search', async ({ page, request }) => {
    const ds = await createDatasetViaApi(request, `e2e-lib-cdel-${stamp}`)
    await createPlanViaApi(request, ds.id, 'cdel plan')
    await seedLibrary(request, `${prefix}/studio`, [`alpha ${stamp}`, `beta ${stamp}`, `gamma ${stamp}`])

    const panel = await openLibrary(page, ds.id)
    // The search leaves one of the three on show; the delete still takes all of them.
    await panel.getByRole('searchbox', { name: 'Search the library' }).fill(`alpha ${stamp}`)
    await expect(panel.getByTestId('comfy-lib-prompt')).toHaveCount(1)
    await panel.getByRole('button', { name: `Category actions for ${prefix}/studio` }).click()
    await page.getByRole('menuitem', { name: 'Delete…' }).click()
    await expect(page.getByText(/and its 3 prompts/)).toBeVisible()
    await page.getByRole('button', { name: 'Delete', exact: true }).click()

    await expect.poll(() => placed(request)).toEqual([])
  })
})

test.describe('the Other plans tab', () => {
  test('Copy here brings another plan\'s rows over with their folder', async ({ page, request }) => {
    const stamp = `${Date.now()}${Math.floor(Math.random() * 1000)}`
    const ds = await createDatasetViaApi(request, `e2e-lib-xplan-${stamp}`)
    const source = await createPlanViaApi(request, ds.id, 'a source plan')
    const target = await createPlanViaApi(request, ds.id, 'z target plan')
    await addRowsViaApi(request, source.id, ['a knight in a fen', 'a knight at a gate'], 'knights')

    await page.goto(`/datasets/${ds.id}/comfy`)
    // Show the target plan, whichever plan the page opened on.
    await page.getByTestId('comfy-plan-rail').getByRole('button', { name: /^z target plan/ }).click()
    await expect(page.getByTestId('comfy-plan-name')).toHaveText('z target plan')
    await page.getByRole('button', { name: 'Library', exact: true }).click()
    const panel = page.getByTestId('comfy-library-panel')
    await panel.getByRole('tab', { name: 'Other plans' }).click()

    await expect(panel.getByRole('combobox', { name: 'Plan' })).toContainText('a source plan')
    const rows = panel.getByTestId('comfy-xplan-row')
    await expect(rows).toHaveCount(2)
    await expect(rows.first()).toContainText('knights')

    await panel.getByRole('button', { name: 'Select all' }).click()
    await panel.getByRole('button', { name: 'Copy here (2)' }).click()

    await expect
      .poll(async () => (await rowsOf(request, target.id)).map((r: { subfolder: string }) => r.subfolder))
      .toEqual(['knights', 'knights'])
    // A copy leaves the source alone.
    expect(await rowsOf(request, source.id)).toHaveLength(2)
  })
})

test('a search that hides selected prompts leaves them out of the actions', async ({ page, request }) => {
  const stamp = `libsel${Date.now()}`
  const ds = await createDatasetViaApi(request, `e2e-lib-sel-${stamp}`)
  await createPlanViaApi(request, ds.id, 'sel plan')
  await request.post('/api/v1/comfy/library', { data: { category: `${stamp}/a`, prompts: [`alpha ${stamp}`] } })
  await request.post('/api/v1/comfy/library', { data: { category: `${stamp}/b`, prompts: [`beta ${stamp}`] } })
  try {
    await page.goto(`/datasets/${ds.id}/comfy`)
    await page.getByRole('button', { name: 'Library', exact: true }).click()
    const panel = page.getByTestId('comfy-library-panel')
    await panel.getByRole('searchbox', { name: 'Search the library' }).fill(stamp)
    for (const word of ['alpha', 'beta']) {
      await panel.getByTestId('comfy-lib-prompt').filter({ hasText: word }).getByRole('checkbox').check()
    }
    await expect(panel.getByRole('button', { name: 'Add 2 to plan' })).toBeVisible()
    await panel.getByRole('searchbox', { name: 'Search the library' }).fill(`alpha ${stamp}`)
    await expect(panel.getByRole('button', { name: 'Add 1 to plan' })).toBeVisible()
  } finally {
    await request.post('/api/v1/comfy/library/categories/delete', { data: { path: stamp } })
  }
})
