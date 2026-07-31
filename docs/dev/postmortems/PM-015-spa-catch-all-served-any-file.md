# PM-015: the SPA catch-all served any file on the host

### Symptom

On a production server (`manage.sh start` / `manage.ps1 start` — anything with
`frontend/dist/` built), a plain `GET` retrieved arbitrary files from the host:

```
GET /%2e%2e/%2e%2e/CLAUDE.md                    → 200, the repo's CLAUDE.md
GET /%2e%2e/%2e%2e/dataset_manager.db           → 200, the entire 70 MB database
GET /%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd     → 200, "root:x:0:0:root:/root:/bin/bash…"
GET //etc/passwd                                → 200, same (raw path, no dots)
```

Nothing distinguished these from ordinary traffic: every response is a 200 that
looks like a static-asset hit, and there is no log line, no job row, and no
DB write. Found by re-reading a closed code review's "outside this branch's
scope" section, which had recorded it as a second unguarded `FileResponse` and
left it there. It was never fixed because it was never filed as a defect.

Reachable by anyone who can open a TCP connection to the server. The app ships no
authentication and binds `0.0.0.0`, so on a shared network that is everyone; a
leaked `dataset_manager.db` carries every dataset path, caption and provenance
record. Dev mode (`manage.sh dev`) is unaffected — Vite serves the frontend
there, `frontend/dist/` is absent, and `main.py` registers the route only when it
exists.

### Root cause

`spa_fallback` built a path from a raw client-supplied URL segment and served it
with no containment check:

```python
candidate = frontend_dist / full_path
if candidate.is_file():
    return FileResponse(str(candidate), headers=_NO_CACHE)
```

Two independent escapes, and the reasoning that hides each is the interesting
part — neither is a missing `..` check in the naive sense:

1. **Percent-encoded dots.** The intuition "a real client never sends `../`" is
   *correct* and irrelevant. Clients normalize literal `../` out of the path
   before the request leaves — which is exactly why casual testing with curl or a
   browser shows nothing wrong — but `%2e%2e` is not a dot segment at
   normalization time. It survives, and Starlette percent-decodes the
   `{full_path:path}` parameter *after* routing, so the handler receives real
   `..` segments that no client ever "sent".

2. **The absolute-path join.** `Path.__truediv__` discards its left operand
   entirely when the right operand is absolute: `Path("/a/dist") / "/etc/passwd"`
   is `Path("/etc/passwd")`, not `/a/dist/etc/passwd`. A request for
   `//etc/passwd` yields `full_path == "/etc/passwd"` and escapes with no dot
   segments involved at all. Every reviewer who checks for `..` and stops has
   missed this one.

### Generalizable rule

**Every `FileResponse` needs a containment gate, and the gate is decided by where
the path came from, not by which tree it points into.** The existing invariant is
phrased around *stored* paths and `settings.datasets_dir`, so a handler building
a path from a URL segment into `frontend/dist` read as a different kind of code
and got audited by nobody. When reviewing, ask of each `FileResponse`: *what is
the untrusted component of this path, and what proves the result stayed inside
its intended root?* — then require `resolve()` + `is_relative_to(root)` **before**
`is_file()`, so the existence check never runs on an escaped path.

Two specific red flags this incident supplies, both applicable to code that has
no `..` in sight:

- **`base / user_input` is unsafe when `user_input` may be absolute.** Pathlib
  silently discards `base`. Reject a leading separator, or gate the result.
- **"The client normalizes it" is never a guard.** Normalization happens before
  the request is sent, percent-decoding happens after routing, and the server
  sees the result of both. Test with encoded input, not typed input.

### Why it wasn't caught the first time

Three gaps, in increasing order of how much they should worry us:

- **`main.py` is outside the routers.** Every path-traversal sweep this repo has
  run — PM-011, PM-014, the V-83 batch — enumerated *routers*, and the guards
  they added live in `backend/utils.py` for routers to import. `main.py`
  registers three endpoints and the frontend block directly and was in none of
  those enumerations. This is PM-014's lesson exactly (an audit scoped by router
  missed an endpoint outside `routers/`), recurring one directory up.

- **A code review found it and filed it as scope.** It was recorded under "Noted,
  outside this branch's scope" as *"a second unguarded `FileResponse`, same
  exposure class as the intentionally-unguarded `/filesystem` router"* — accurate,
  and then never converted into a row anyone would act on. The comparison did the
  damage: `/filesystem` is unguarded *by design* because the File Browser needs
  arbitrary paths, and being filed beside it made this read as an accepted risk
  rather than a defect. **An out-of-scope finding needs a home outside the
  document that found it**, or it dies with that document.

- **No test could have caught it.** `spa_fallback` had no test at all, and the
  obvious one would have missed anyway: an httpx-based request for `//etc/passwd`
  passes against the *unguarded* handler, because httpx parses `//foo` as a URL
  authority and never sends it as a path. That was written and observed to pass
  during this fix before the discrepancy was chased down. A security test that
  cannot fail against the vulnerable code is worse than no test — see the Fix
  below for the shape that actually bites.

### Fix

Fixed 2026-07-30 in `backend/main.py`: resolve the candidate, require
`resolved.is_relative_to(frontend_dist.resolve())` and only then `is_file()`;
everything else falls through to `index.html`. `index.html` and the resolved dist
root are hoisted to module scope so the guard costs no per-request work. The
`OSError` arm covers a `resolve()` that fails on a malformed name.

Tests: `backend/tests/test_spa_fallback_containment.py` — four cases, one per
vector plus the two that keep the guard honest (a real file inside dist is still
served; an unknown route still reaches the SPA). Bite proven for both vectors by
restoring the pre-fix two lines and watching them fail. The absolute-path case
drives a hand-built ASGI scope with `scope["path"]` set verbatim, which is what
uvicorn produces from a raw `GET //etc/passwd HTTP/1.1`; the module skips when
`frontend/dist` is absent.

### Status & date

MITIGATED — 2026-07-30.
Last reviewed for staleness: 2026-07-30.
