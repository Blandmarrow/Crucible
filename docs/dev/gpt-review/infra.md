# Network exposure: the deferred half of GPT-007

The rest of GPT-007 is fixed (PM-031). `backend/request_guard.py` refuses a rebound `Host` and a
cross-site state-changing request, and the launchers bind `127.0.0.1` unless `--lan` (or
`CRUCIBLE_HOST`) opts in; see `docs/dev/http-edge.md` § Request guard and
`docs/dev/environment-setup.md`. Conventions are in `docs/dev/gpt-review/README.md`.

## GPT-007 step 3 — a shared token for LAN mode

**Status:** DEFERRED by decision. Build it only if Crucible is used from other devices on
networks that are not fully trusted.

**What is still open.** In LAN mode the API is still unauthenticated. The guard stops *websites*
from driving it, but any device that can reach the port can call every route directly. That
includes the filesystem router, which accepts arbitrary absolute paths, and
`/api/v1/shutdown`/`/restart`. The launcher's LAN warning says so.

**Fix notes.**
- `setup` generates a secret into `.env`, for example `CRUCIBLE_TOKEN`.
- The launcher prints a one-time `?token=` link, and the server exchanges it for an HttpOnly
  `SameSite=Strict` cookie.
- Use a cookie rather than a header: `<img src>`, `<video>` and `EventSource` (SSE) cannot send
  custom headers, but they do send cookies.
- Loopback requests are exempt, so local use is unchanged.
- Enforce it in `request_guard.py`, which already sees every request, so there is one gate rather
  than a dependency on each router.
