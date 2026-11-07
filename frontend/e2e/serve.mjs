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
import { ROOT, findPython, tieLifetime } from './python.mjs'

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

const python = findPython(env)

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
tieLifetime(server, 'uvicorn')
