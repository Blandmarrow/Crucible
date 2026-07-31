// Selector policy: prefer getByRole / getByLabel / getByText. Reach for a
// data-testid only when a control is genuinely ambiguous by role/text; any new
// testid goes into frontend/src in the SAME commit as the spec that needs it.
// There are 14 today, and the shape of the exceptions is the policy: tiles
// addressed by id (`select-*`, `dataset-card-*`, `gallery-tile`) and the
// extraction modal's drag handles (`crop-handle-*`, `trim-*`), which have no
// accessible name to match on.
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

// A real 2-second 128x96 mp4 (25 fps, mp4v), inlined the same way and for the
// same reason as PNG_BASE64 — no binary fixture in git. Generated once with
// cv2.VideoWriter, matching backend/tests/conftest.py::mp4_bytes; `mp4v` is the
// fourcc that the opencv wheel can actually write (`avc1` needs an h264 encoder
// it does not ship). A moving block gives the probe something to sample that is
// not a run of identical frames.
const MP4_BASE64 =
  'AAAAHGZ0eXBpc29tAAACAGlzb21pc28ybXA0MQAAAAhmcmVlAAAIx21kYXQAAAGzABAHAAABthBgcYKhtyRtt/G238bbfxtt/G23' +
  '8bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxgbVt/KClB4CDbB4T+1B4iAzB8z/XBSMHgINsHhP7UHiIDMHzP9d/G238bbfxtt' +
  '/G238bbfxtt/IClB4GDbB4b+1B4qAzB87/XYPAwbYPDf2oPFQGYPnf64KR/CCiDApAYFIDwMDGDgPg8F/tgwGwYDYMoB4SAZBuB8' +
  'DcBVg8N/2l45HIKcFQoLBFUnG38bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/' +
  'G238bbfvAAABtlDwM//+vg8B/Eg8B+zg8H/Jg8B/mg8RARg8XAWg+BAPg8B/Eg8B+zg8H/Jg8B/mg8RARg8XAWg+BAPuH/t4PAf4' +
  'oPAf2oPAf5YPA/3oPAf5YNQYEEGHgMCgBgUIMoBgUQIYNoHgbADBIAOBhHA8JSlSCgHoM17FKjua9w////0AAAG2UWAz//7h7h/7' +
  'eFb////vAAABtlHwM//+4e4f+3gtH////QAAAbZSYDP//uHuH/t51////38AAAG2UvAz//7h7h/77////gAAAbZTYDP//uHhjBvN' +
  'uT9w9wf///4AAAG2U/Az//7h9w/7h9w////vAAABtlRgM//+4TuH/DGCobclw////gAAAbZU8DP//3D3D/33///+AAABtlVgM///' +
  'cPcP/ff///4AAAG2VfAz//9w9w/99////gAAAbMAEAcAAAG2FmBxgqG3JG238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt' +
  '/G238bbfxtt/G238bA2rfzsFKDwEG2Dwn9qDxEBmD5n+u2DwEG2Dwn9qDxEBmD5n+v8YKRt/G238bbfxtt/G238bbfw2ClB4GDbB' +
  '4b+1B4qAzB87/XYKT5GDwMG2Dw39qDxUBmD53+uFEGBSAwKQHgYGMHAfB4L/bBgNgwGwZQDwkAyDcD4G4CrB4b/tLxyOQU4KhQWC' +
  'KpON/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt+8AAAG2VvAz//9w9w/9' +
  '9////gAAAbZXYDP//3Dwxg3m3J+4e4T///4AAAG2V/Az//9w+4f9w99////fAAABtlhgM///cPuH/cPff///3wAAAbZY8DP//7h7' +
  'h/77///+AAABtllgM///uHuH/vv///4AAAG2WfAz//+4e4f++////gAAAbZaYDP//7h7h/77///+AAABtlrwM///uHuH/vv///4A' +
  'AAG2W2Az//+4e4f++////gAAAbZb8DP//7h9w/7h77///98AAAGzABAHAAABthxgcYKhtyRtt/G238bbfxtt/G238bbfxtt/G238' +
  'bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxgbVt/KClB4CDbB4T+1B4iAzB8z/XBSMHgINsHhP7UHiIDMHzP9d/G238bbfxtt/' +
  'G238bbfxtt/IClB4GDbB4b+1B4qAzB87/XYPAwbYPDf2oPFQGYPnf64KR/CCiDApAYFIDwMDGDgPg8F/tgwGwYDYMoB4SAZBuB8D' +
  'cBVg8N/2l45HIKcFQoLBFUnG38bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfvAAAB' +
  'tmg4Gf//7h7h/77///9/AAABtlDgM///3D3D/33///4AAAG2UXAz///cPcP/ff///gAAAbZR4DP//9w9w/99///+AAABtlJwM///' +
  '3D3D/33///4AAAG2UuAz///cPDGDebcn7h7hP//+AAABtlNwM///3D7h/3D33///3wAAAbZT4DP//9w+4f9w99///98AAAG2VHAz' +
  '///uHuH/vv///gAAAbZU4DP//+4e4f++///+AAABtlVwM///7h7h/77///4AAAGzABBHAAABthXgcYKhtyRtt/G238bbfxtt/G23' +
  '8bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/GwNq387BSg8BBtg8J/ag8RAZg+Z/rtg8BBtg8J/ag8RAZg+Z' +
  '/r/GCkbfxtt/G238bbfxtt/G238NgpQeBg2weG/tQeKgMwfO/12Ck+Rg8DBtg8N/ag8VAZg+d/rhRBgUgMCkB4GBjBwHweC/2wYD' +
  'YMBsGUA8JAMg3A+BuAqweG/7S8cjkFOCoUFgiqTjfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/' +
  'G238bbfvAAABtlZwM///7h7h/77///4AAAG2VuAz///uHhjBvNuT9w9wn//+AAABtldwM///7h9w/7h77///3wAAAbZX4DP//+4f' +
  'cP+4e+///98AAAG2WHAz///3D3D/33///gAAAbZY4DP///cPcP/ff//+AAABtllwM///9w9w/99///4AAAG2WeAz///3D3D/33//' +
  '/gAAAbZacDP///cPcP/ff//+AAABtlrgM///9w9w/99///4AAAG2W3Az///3D7h/3D33///fAAABswAQRwAAAbYb4HGCobckbbfx' +
  'tt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238YG1bfygpQeAg2weE/tQeIgMwfM/' +
  '1wUjB4CDbB4T+1B4iAzB8z/Xfxtt/G238bbfxtt/G238bbfyApQeBg2weG/tQeKgMwfO/12DwMG2Dw39qDxUBmD53+uCkfwgogwK' +
  'QGBSA8DAxg4D4PBf7YMBsGA2DKAeEgGQbgfA3AVYPDf9peORyCnBUKCwRVJxt/G238bbfxtt/G238bbfxtt/G238bbfxtt/G238b' +
  'bfxtt/G238bbfxtt/G237wAAAbZccDP///uHuH/vv//+AAAEHW1vb3YAAABsbXZoZAAAAAAAAAAAAAAAAAAAA+gAAAfQAAEAAAEA' +
  'AAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIA' +
  'AANHdHJhawAAAFx0a2hkAAAAAwAAAAAAAAAAAAAAAQAAAAAAAAfQAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAA' +
  'AAAAAAAAAAAAAAAAQAAAAACAAAAAYAAAAAAAJGVkdHMAAAAcZWxzdAAAAAAAAAABAAAH0AAAAAAAAQAAAAACv21kaWEAAAAgbWRo' +
  'ZAAAAAAAAAAAAAAAAAAAMgAAAGQAVcQAAAAAAC1oZGxyAAAAAAAAAAB2aWRlAAAAAAAAAAAAAAAAVmlkZW9IYW5kbGVyAAAAAmpt' +
  'aW5mAAAAFHZtaGQAAAABAAAAAAAAAAAAAAAkZGluZgAAABxkcmVmAAAAAAAAAAEAAAAMdXJsIAAAAAEAAAIqc3RibAAAANpzdHNk' +
  'AAAAAAAAAAEAAADKbXA0dgAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAACAAGAASAAAAEgAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAA' +
  'AAAAAAAAAAAAAAAAAAAAABj//wAAAGBlc2RzAAAAAAOAgIBPAAEABICAgEEgEQAAAAAJYAAAACL8BYCAgC8AAAGwAQAAAbWJEwAA' +
  'AQAAAAEgAMSNiADNBAQMFGMAAAGyTGF2YzYyLjI4LjEwMQaAgIABAgAAABRidHJ0AAAAAAAJYAAAACL8AAAAGHN0dHMAAAAAAAAA' +
  'AQAAADIAAAIAAAAAJHN0c3MAAAAAAAAABQAAAAEAAAANAAAAGQAAACUAAAAxAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAAyAAAAAQAA' +
  'ANxzdHN6AAAAAAAAAAAAAAAyAAABBgAAAIMAAAATAAAAEwAAABMAAAARAAAAFgAAABMAAAAWAAAAEQAAABEAAAARAAABBgAAABEA' +
  'AAAWAAAAEwAAABMAAAARAAAAEQAAABEAAAARAAAAEQAAABEAAAATAAABBgAAABIAAAARAAAAEQAAABEAAAARAAAAFgAAABMAAAAT' +
  'AAAAEQAAABEAAAARAAABBgAAABEAAAAWAAAAEwAAABMAAAARAAAAEQAAABEAAAARAAAAEQAAABEAAAATAAABBgAAABEAAAAUc3Rj' +
  'bwAAAAAAAAABAAAALAAAAGJ1ZHRhAAAAWm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALWlsc3QA' +
  'AAAlqXRvbwAAAB1kYXRhAAAAAQAAAABMYXZmNjIuMTIuMTAx'

export function mp4Buffer(): Buffer {
  return Buffer.from(MP4_BASE64, 'base64')
}

/** Create a dataset through the API and return its row. */
export async function createDatasetViaApi(
  request: APIRequestContext,
  name: string,
  // `folder_path` is what the endpoint has always returned; it is declared here
  // so a spec can reach the dataset folder on disk — the only way to stage the
  // "files appeared outside the app" state that Rescan exists to pick up.
): Promise<{ id: string; name: string; folder_path: string }> {
  const r = await request.post('/api/v1/datasets/', { data: { name } })
  expect(r.status(), await r.text()).toBe(201)
  return r.json()
}

/** Upload one PNG into a dataset through the API and return the added filename. */
export async function uploadViaApi(
  request: APIRequestContext,
  datasetId: string,
  name = 'seed.png',
  subfolder = '',
): Promise<string> {
  const r = await request.post('/api/v1/images/upload', {
    params: { dataset_id: datasetId, subfolder },
    multipart: {
      files: { name, mimeType: 'image/png', buffer: pngBuffer() },
    },
  })
  expect(r.status(), await r.text()).toBe(201)
  return (await r.json()).files[0]
}

/** Upload one mp4 into a dataset and return its `Video` row.
 *
 * Videos come back under `videos`, not `files` — the same endpoint ingests both,
 * and cv2 is the gate: a build without opencv rejects the upload rather than
 * skipping it, which is why the e2e workflow installs opencv-python-headless.
 */
export async function uploadVideoViaApi(
  request: APIRequestContext,
  datasetId: string,
  name = 'clip.mp4',
): Promise<{ id: string; filename: string }> {
  const r = await request.post('/api/v1/images/upload', {
    params: { dataset_id: datasetId },
    multipart: {
      files: { name, mimeType: 'video/mp4', buffer: mp4Buffer() },
    },
  })
  expect(r.status(), await r.text()).toBe(201)
  const body = await r.json()
  expect(body.videos, JSON.stringify(body)).toContain(name)
  const listing = await (
    await request.get('/api/v1/videos/', { params: { dataset_id: datasetId } })
  ).json()
  return listing.find((v: { filename: string }) => v.filename === name)
}
