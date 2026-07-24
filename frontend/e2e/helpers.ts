// Selector policy: prefer getByRole / getByLabel / getByText. Reach for a
// data-testid only when a control is genuinely ambiguous by role/text; any new
// testid goes into frontend/src in the SAME commit as the spec that needs it
// (expected total ≤3 — the gallery thumbnail tile and the dataset card).
//
// These helpers cover SETUP that is not itself under test (creating a dataset,
// seeding an image) via the API, so a UI journey starts from a known state
// without re-driving unrelated screens.
import type { APIRequestContext } from '@playwright/test'
import { expect } from '@playwright/test'

// A real 32×32 PNG, inlined as base64 so no binary fixture lives in git.
const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAK0lEQVR4nO3NQQEAAATAQKTRP4VYSvC7BdjldMdn9XoHAAAAAAAAAAAAhy3SYgFYbQ5O/gAAAABJRU5ErkJggg=='

export function pngBuffer(): Buffer {
  return Buffer.from(PNG_BASE64, 'base64')
}

/** Create a dataset through the API and return its row. */
export async function createDatasetViaApi(
  request: APIRequestContext,
  name: string,
): Promise<{ id: string; name: string }> {
  const r = await request.post('/api/v1/datasets/', { data: { name } })
  expect(r.status(), await r.text()).toBe(201)
  return r.json()
}

/** Upload one PNG into a dataset through the API and return the added filename. */
export async function uploadViaApi(
  request: APIRequestContext,
  datasetId: string,
  name = 'seed.png',
): Promise<string> {
  const r = await request.post('/api/v1/images/upload', {
    params: { dataset_id: datasetId },
    multipart: {
      files: { name, mimeType: 'image/png', buffer: pngBuffer() },
    },
  })
  expect(r.status(), await r.text()).toBe(201)
  return (await r.json()).files[0]
}
