// Playwright webServer command: a fake OpenAI-compatible LLM on a real port, for the
// specs that point a provider at it (fake-llm.spec.ts). CI has no LLM server; the
// e2e backend is a separate process, so the in-process transport the pytest suite
// uses cannot reach it. Same handler, served over a socket — see
// backend/tests/fake_llm.py for what it models and the [[fake:…]] markers.
import { spawn } from 'node:child_process'
import { ROOT, findPython, tieLifetime } from './python.mjs'

const env = { ...process.env }
const python = findPython(env)
const server = spawn(
  python,
  ['-m', 'backend.tests.fake_llm', '--port', process.env.E2E_LLM_PORT ?? '8197'],
  { cwd: ROOT, env, stdio: 'inherit' },
)
tieLifetime(server, 'the fake LLM')
