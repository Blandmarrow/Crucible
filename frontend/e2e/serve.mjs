// Playwright webServer command: serve the built SPA + API against a throwaway
// SQLite DB and data dir, so E2E never touches the live repo-root DB or ./data.
//
// Node rather than a shell script because Node is the one runtime guaranteed
// wherever Playwright runs — Windows often has no bash. Plain ESM `.mjs`, outside
// e2e/tsconfig.json's `**/*.ts`, so it never enters `npm run typecheck:e2e`.
import { spawn, spawnSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const isWin = process.platform === 'win32'

// `realpathSync.native` expands what `os.tmpdir()` may abbreviate — the
// `RUNNER~1` 8.3 form on Windows, `/var` -> `/private/var` on macOS — so the
// backend (which resolves its paths) and stats.spec.ts (which compares against
// the temp dir) see one spelling of it.
const TMP = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), 'crucible-e2e-')))
// SQLAlchemy wants forward slashes: `sqlite+aiosqlite:////tmp/x/e2e.db` on POSIX
// (abs path => 4 slashes total) and `sqlite+aiosqlite:///C:/.../e2e.db` on Windows.
const toPosix = (p) => p.split(path.sep).join('/')
const env = {
  ...process.env,
  DATABASE_URL: `sqlite+aiosqlite:///${toPosix(path.join(TMP, 'e2e.db'))}`,
  DATA_DIR: path.join(TMP, 'data'),
  DATASETS_DIR: path.join(TMP, 'data', 'datasets'), // does NOT derive from DATA_DIR
}
// stats.spec.ts finds the DB from a dataset's folder_path (three levels up), to
// seed scores no route writes; moving either path breaks it loudly, not silently.

// Works both locally (the repo venv) and in CI (setup-python + pip, no venv, so
// `python` on PATH). The venv is put first on PATH too, as `activate` would, so
// anything the backend spawns by name resolves inside it.
const venvBin = path.join(ROOT, 'venv', isWin ? 'Scripts' : 'bin')
const venvPython = path.join(venvBin, isWin ? 'python.exe' : 'python')
let python = 'python'
if (fs.existsSync(venvPython)) {
  python = venvPython
  env.VIRTUAL_ENV = path.join(ROOT, 'venv')
  // Windows env keys are case-insensitive but a spread object's are not: reuse
  // whichever spelling of PATH is already there rather than adding a second.
  const pathKey = Object.keys(env).find((k) => k.toUpperCase() === 'PATH') ?? 'PATH'
  env[pathKey] = [venvBin, env[pathKey]].filter(Boolean).join(path.delimiter)
}

if (!fs.existsSync(path.join(ROOT, 'frontend', 'dist', 'index.html'))) {
  console.error('frontend/dist missing — run npm run build')
  process.exit(1)
}

// init_db creates NO tables — the schema comes only from alembic, which must run
// from backend/ (see scripts/check_migrations.py on why).
const migrate = spawnSync(python, ['-m', 'alembic', 'upgrade', 'head'], {
  cwd: path.join(ROOT, 'backend'),
  env,
  stdio: 'inherit',
})
if (migrate.error) {
  console.error(`could not run ${python}: ${migrate.error.message}`)
  process.exit(1)
}
if (migrate.status !== 0) process.exit(migrate.status ?? 1)

// No --host: uvicorn's default, 127.0.0.1.
const server = spawn(
  python,
  ['-m', 'uvicorn', 'backend.main:app', '--port', process.env.E2E_PORT ?? '8199'],
  { cwd: ROOT, env, stdio: 'inherit' },
)
server.on('error', (err) => {
  console.error(`could not start uvicorn: ${err.message}`)
  process.exit(1)
})

// Playwright stops the webServer by killing its whole process tree (a process-
// group SIGKILL on POSIX, `taskkill /T` on Windows), which reaches uvicorn
// directly. A signal sent to this process alone — `kill <pid>`, or Ctrl-C from a
// manual run — is forwarded, so uvicorn never outlives its launcher.
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => {
    if (server.exitCode === null && server.signalCode === null) server.kill(sig)
  })
}
server.on('exit', (code, signal) => {
  if (signal) {
    // Die of the same signal, so the parent sees how the server ended.
    process.removeAllListeners(signal)
    process.kill(process.pid, signal)
    return
  }
  process.exit(code ?? 1)
})
