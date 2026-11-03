// Playwright webServer command: a fake ComfyUI on a real port, for the specs
// that press Run (comfy-run.spec.ts). CI has no ComfyUI and no GPU; the e2e
// backend is a separate process, so the in-process MockTransport the pytest
// suite uses cannot reach it. Same handler, served over a socket — see
// backend/tests/fake_comfy.py for what it models.
import { spawn } from 'node:child_process'
import { ROOT, findPython, tieLifetime } from './python.mjs'

const env = { ...process.env }
const python = findPython(env)
const server = spawn(
  python,
  ['-m', 'backend.tests.fake_comfy', '--port', process.env.E2E_COMFY_PORT ?? '8198'],
  { cwd: ROOT, env, stdio: 'inherit' },
)
tieLifetime(server, 'the fake ComfyUI')
