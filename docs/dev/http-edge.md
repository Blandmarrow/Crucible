# HTTP edge: request guard, SPA serving, exception handlers & server control

This file covers what wraps every request rather than any one route: the Host/Origin request guard (the app's only middleware), production frontend serving and the SPA catch-all's containment gate, the app-level exception handlers, and the server control endpoints (shutdown, health, restart) with the manage-script restart loop behind them. The job queue those endpoints stop, SSE progress, job cancellation and the CI workflows are in `docs/dev/backend-infrastructure.md`. The bind address, `--lan` and the startup splash are in `docs/dev/environment-setup.md`; the database and its startup backup are in `docs/dev/database.md`.

### Request guard: Host and Origin

`backend/request_guard.py::RequestGuardMiddleware` is the app's only middleware, and therefore the first thing every HTTP request and WebSocket handshake meets: the API, the SSE streams and the SPA catch-all alike. It exists because the API is unauthenticated (GPT-007): loopback binding (see `docs/dev/environment-setup.md` § Bind address and `--lan`) keeps other machines out but not other *websites* in the user's browser.

- **Host (DNS rebinding), every method.** A rebound `evil.example` is same-origin to the browser, so only the `Host` header gives it away. Allowed: any IP literal, `localhost` and `*.localhost`, `socket.gethostname()` (plus its first label and `<name>.local`), and the comma-separated `CRUCIBLE_ALLOWED_HOSTS` setting. A missing `Host` passes. No DNS lookups ever. The configured set is re-parsed only when the setting's string changes, so a test that monkeypatches it is honoured.
- **Origin (CSRF), POST/PUT/PATCH/DELETE only.** CORS stops a foreign page *reading* a response, never the request itself running — a cross-site `fetch` could POST `/api/v1/shutdown`. An `Origin` passes iff its host and port equal the request `Host`'s (scheme-agnostic; no port, `:80` and `:443` compare equal). There is no origin allowlist. `Origin: null` is refused. With no `Origin`, the request is refused only when `Sec-Fetch-Site` is `cross-site` or `same-site` (ports are not part of a site, so another localhost port is "same-site"). No browser headers at all — curl, httpx, Playwright's `request` fixture — passes.

Both refusals are a 403 JSON `{"detail": …}` naming the host or origin. A WebSocket handshake gets both checks, Origin included whatever the method, and is refused by closing it (code 1008) before `websocket.accept`, which the server answers as a 403. CORS never covered WebSockets, so the first WS route would otherwise be open to every website.

**No CORS, on purpose.** Every page is served from the origin it calls. That includes the Vite dev server: its proxy forwards the browser's own `Host` (`changeOrigin: false` in `frontend/vite.config.ts`), so a dev page's `Origin` and `Host` agree on `:5173`, locally and from a `dev --lan` client alike. The app once ran `CORSMiddleware` allowing `http://localhost:5173` with credentials, and the guard briefly shared that list. That trusted *whatever* listens on `:5173`, by default any other project's Vite dev server, to send changes and read every response. Do not reintroduce either; a legitimate cross-origin consumer would need a deliberate design, not an allowlist entry.

**Pure ASGI, never `BaseHTTPMiddleware`.** The latter runs the downstream app in a separate task and pipes the body through a memory stream, which is known to interfere with streaming responses, and the progress bar's SSE stream lives for the whole session (`docs/dev/backend-infrastructure.md` § SSE progress). The guard only reads `scope["headers"]` and either forwards `receive`/`send` untouched or answers itself. Request-level tests must therefore use an allowed host: `conftest.api_env` and the three files that build their own client use `base_url="http://localhost"`. `backend/tests/test_request_guard_http.py` covers the rules, including a shutdown/restart from a foreign origin that must not reach a (patched) `os.kill`.

### Frontend serving (production)

`backend/main.py` serves the built React app after all API routers are registered. Two parts:

1. `app.mount("/assets", StaticFiles(directory=frontend_dist/"assets"))` — serves JS/CSS/fonts at their exact paths with correct content-type headers.
2. `@app.get("/{full_path:path}")` catch-all — for any unmatched path, serves the file directly if it exists in `frontend/dist/` (favicon, manifest, etc.), otherwise returns `index.html` so React Router handles client-side navigation.

**Do not replace this with a bare `StaticFiles(html=True)` mount.** Starlette's `html=True` only falls back to `index.html` for directory-style paths (`/`); it returns 404 for deep URLs like `/datasets/abc123` on hard refresh. The catch-all route is required for SPA routing to work correctly.

**The catch-all is containment-gated, and the order matters** (PM-015). `full_path` is raw client input reaching a `FileResponse`, so the handler resolves the candidate and requires `is_relative_to(frontend_dist)` **before** `is_file()`; anything outside falls through to `index.html`, which is the right answer for a path naming nothing in dist anyway. Two vectors made this an arbitrary-file read until 2026-07-30, and both are easy to talk yourself out of:

- **`%2e%2e`.** The usual reasoning is "clients normalize `../` away" — they do, which is why this survived so long, but percent-encoded dots survive that normalization and Starlette decodes the path parameter *after* routing. The handler receives real `..` segments.
- **A leading slash.** `Path.__truediv__` **discards the left operand** when the right is absolute, so `frontend_dist / "/etc/passwd"` *is* `/etc/passwd`. Sent as `//etc/passwd`, it needs no dots at all.

`backend/tests/test_spa_fallback_containment.py` pins both. Note it drives the second one through a hand-built ASGI scope rather than the httpx client: httpx parses `//foo` as a URL *authority* and never sends it as a path, so an httpx-based test for that vector passes against the unguarded handler and proves nothing. uvicorn applies no such reinterpretation. The block lives in `main.mount_spa(app, dist)`, which `main.py` calls only when `frontend/dist` exists; the tests call it against a throwaway `dist` under `tmp_path`, so they run on every pytest job with no frontend build. They used to skip whenever `dist` was absent, which meant every CI run, and the raw-scope helper's `receive()` hung Starlette ≥1.8's `FileResponse` disconnect listener the moment they were enabled. Keep the helper's blocking `receive()`, its `spec_version` and its timeout. See PM-028.

### App-level exception handlers

Three `@app.exception_handler` registrations sit in `backend/main.py` between the middleware registrations and the router includes. Both are app-level for the same reason: the exposure they close belongs to a *class* of route, not to one route, and a per-route defence is a rule every future route has to remember.

| Exception | Answer | Why here |
|---|---|---|
| `utils.FileInUseError` | 409 with the message as `detail` | Every site adopting `utils.unlink_retrying`/`rename_retrying` inherits the translation, including the `filesystem.py` routes that are not converted yet. 409 not 500 — the request was well-formed and a later retry can work. See CLAUDE.md § *A file the app is still serving...* and `docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md` |
| `model_manager.ModelInUseError` | 409 with the message as `detail` | `model_manager.unload` is reachable from several routes (`DELETE /captioning/model/{id}/unload`, among others), and a model a running job holds a lease on must answer "retry when the job finishes" from every one of them rather than 500 — or, before leases, break the job. See `docs/dev/model-residency.md` § Leases |
| `RequestValidationError` | 422 with `input` and `ctx` stripped from every error entry | FastAPI's default handler returns pydantic's entries verbatim, and pydantic puts the value it *refused* in `input` — so a rejected secret came back in the response body |

**The 422 redaction.** `PATCH /settings/secrets` with an over-long `hf_token` echoed the submitted token, directly contradicting the property `docs/dev/settings.md` § API Keys states and `test_settings_secrets.py` asserts. `POST`/`PATCH /providers` had the same shape via `OpenAIProviderCreate/Update.api_key`. Stripping `input` (and `ctx`, which carries the same value for some error types) makes "the plaintext appears in no response" a property of the app rather than of the paths someone remembered to check.

Nothing is lost by the strip: pydantic renders a `value_error` as `"Value error, "` + the `ValueError`'s own text and a constraint failure as `"String should have at most 500 characters"`, so `msg` already restates what `ctx` held, and `loc` still names the field — which is all `frontend/src/utils/apiError.ts` reads (it dispatches on the *shape* of `detail`, never on the status). `jsonable_encoder` is required and matches FastAPI's own default: `loc` arrives as a tuple, and a couple of entries are hand-built outside pydantic. The one cost is that a test passing `r.text` as a pytest failure message no longer prints the rejected value.

Scope is rejected *values* only. A secret reaching a 500 traceback or a log line is a different exposure and neither handler touches it.

### Server control endpoints

Three endpoints are registered directly in `backend/main.py` (not via a router), immediately before the frontend-serving block:

| Endpoint | Behaviour |
|---|---|
| `POST /api/v1/shutdown` | Touches `.shutdown` sentinel, then spawns a daemon thread that sends `SIGTERM` to the current process, triggering uvicorn's graceful shutdown. The sentinel lets the manage script distinguish a clean shutdown from a crash. |
| `GET /api/v1/health` | Returns `{"status": "ok", "start_time": <float>}`. `start_time` is `time.time()` captured once at module load (`_START_TIME`). Used by the frontend to detect when a restarted server is a *new* process. |
| `POST /api/v1/restart` | Removes `.shutdown` (if present), creates `.restart` sentinel file (`_RESTART_SENTINEL = Path(__file__).parent.parent / ".restart"`), then sends `SIGTERM`. The manage-script restart loop detects the sentinel after uvicorn exits and re-launches the server. |

Both `SIGTERM`s end in `main.py`'s lifespan, which awaits `job_queue.stop()` before uvicorn exits, so a running job gets its grace first: see `docs/dev/backend-infrastructure.md` § Shutdown: stopping the job queue.

**Restart loop** — `Cmd-Start`/`cmd_start` in `manage.ps1`/`manage.sh` wrap the uvicorn call in a `while` loop. After uvicorn exits they check for `.restart`; if present they delete it and restart, otherwise they break. `Cmd-Dev`/`cmd_dev` have the same loop (bash uses a background subshell so the frontend Vite server can run concurrently). Stale `.restart` and `.shutdown` files left by a crash are deleted at the top of each start before the loop begins. The uvicorn call is written as `python -m uvicorn ... || true` in `manage.sh` so that `set -euo pipefail` does not abort the loop on a non-zero exit before the sentinel check runs.

**Terminal auto-close on shutdown** — after the restart loop exits (no `.restart` sentinel), `Cmd-Start` in `manage.ps1` checks for `.shutdown`. If found, it deletes the file and calls `exit 0`. `Crucible.bat` only runs `pause` when the PowerShell exit code is non-zero, so a clean shutdown closes the terminal window automatically while a crash keeps it open so the user can read the error output.

**Frontend restart flow** (`TopBar.tsx` `handleRestart`): (1) fetch `/api/v1/health` to record the current `start_time`; (2) POST `/api/v1/restart`; (3) poll `/api/v1/health` with `{ cache: "no-store" }` every second until the response carries a *different* `start_time` (confirming a new process, not the dying old one); (4) `window.location.reload()`. While this runs, a full-screen overlay shows the animated `CrucibleMark`. Both the restart and shutdown buttons are disabled while `restarting || shuttingDown`.

**The restart poll has no deadline, deliberately.** It previously gave up after 60 s and reset the UI "so the user can retry". That silently strands the page: a cold start importing torch can exceed 60 s, and a server not running under the restart loop (see above) needs a manual relaunch — in both cases the server comes back *after* the poll has stopped, so the page never reloads and looks hung. Polling now continues indefinitely; after `RESTART_SLOW_MS` (25 s) the overlay adds an explanation and a manual "Reload now" button rather than giving up. `restarting` is never reset to `false` — every exit from this state is a page load. Do not reintroduce a cutoff.
