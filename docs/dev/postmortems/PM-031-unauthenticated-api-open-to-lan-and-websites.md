# PM-031: Unauthenticated API open to the LAN and to any website in the browser

### Symptom

Found by external review (GPT-007), not by an incident. Both launchers started uvicorn with
`--host 0.0.0.0`, so every device on the user's network could drive the whole API — list,
download, rewrite and delete datasets, read files through any path bug (PM-015 was one), and
`POST /api/v1/shutdown`. Binding loopback alone would not have closed it: any web page open in
the user's browser could still `fetch("http://localhost:8000/api/v1/shutdown", {method: "POST"})`.
CORS blocked reading the response, never the request running. And a page using DNS rebinding
(`evil.example` re-pointed at 127.0.0.1) is *same-origin* to the browser, so it could read every
response too.

### Root cause

Three assumptions stacked. "Local app" was read as "nobody else can reach it", while the bind
address said the opposite. CORS was treated as access control, but it only governs what a
cross-origin page may *read*; a simple POST is sent and executed before CORS is consulted. And
nothing checked the `Host` header, which is the only signal that separates a rebound hostname
from a genuine local request, since the browser considers both same-origin.

### Generalizable rule

For any server with no authentication: (1) flag a default bind to `0.0.0.0`/`::`, since
exposure must be an explicit, warned opt-in; (2) flag any state-changing route reachable
without an Origin check, and do not accept CORS configuration as the answer to CSRF; (3) flag
the absence of a Host allowlist, because loopback binding does not stop DNS rebinding. New
middleware that must see every request has to be pure ASGI and outermost, or SSE and the SPA
catch-all slip past it.

### Why it wasn't caught the first time

Every request-level test drove the app with `base_url="http://test"` and no browser headers,
a shape no browser produces. The threat model was "the user's own machine" and never "the
user's own *browser*", which is where the hostile code runs. The launchers' bind address
lived in shell scripts no test exercises.

### Fix

`backend/request_guard.py` (pure ASGI, outermost) refuses a `Host` that is not an IP literal,
`localhost`, this machine's hostname or a `CRUCIBLE_ALLOWED_HOSTS` entry, and refuses unsafe
methods carrying a foreign `Origin`, `Origin: null`, or `Sec-Fetch-Site: cross-site/same-site`.
The launchers bind `127.0.0.1` by default through `scripts/bind_host.py`; `--lan` or
`CRUCIBLE_HOST` opts in with a printed warning. Tests now use `http://localhost`, and
`backend/tests/test_request_guard_http.py` covers each rule, including a shutdown from a
foreign origin that must not reach `os.kill`. See `docs/dev/backend-infrastructure.md`
§ Request guard and `docs/dev/environment-setup.md` § Bind address and `--lan`.

Still open by design: a LAN opt-in has no authentication. A per-install token was deferred.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-10-28.
