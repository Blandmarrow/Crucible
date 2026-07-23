# PM-005: helper's tuple return grew a field; unrelated caller crashed after writing files

### Symptom

Every ComfyUI generation run died on its first output image. `POST /comfy/run` accepted
the request and returned a `job_id`; the job then failed with

```
ValueError: too many values to unpack (expected 2)
```

The image and its thumbnail were already on disk by the time it raised, and the error
matched neither handler in the row loop (`except asyncio.CancelledError` /
`except (ComfyRowError, httpx.HTTPError, OSError)`), so per-row cleanup never ran: the
files leaked and the `ComfyRow` stayed committed as `"running"` forever. The whole test
suite was green (205 passed).

### Root cause

`dataset_service._register_file_sync` was widened from `(info, gen_meta)` to
`(info, gen_meta, provenance)` for the source-and-license feature, and its rescan caller
was updated. `routers/comfy.py` had a second caller — a thin `_write_and_register`
wrapper — that still declared `-> tuple[dict, dict | None]` and unpacked two values. A
positional tuple return has no name to grep for and no type checker running in CI, so the
annotation lied and nothing objected.

Two independent failures compounded it:

1. the unpack raised *after* the side effects (file write, thumbnail generation), and
2. the row loop caught a hand-picked exception list rather than `Exception`, so an
   unanticipated error type skipped the cleanup that existed precisely for this case.

### Generalizable rule

- **Flag any multi-value positional return that has more than one caller.** If a function
  returns a tuple of three or more values, or is likely to grow one, make it a
  `NamedTuple` (or dataclass) so callers use attribute access and a new field cannot
  silently break an existing unpack. When reviewing a diff that *widens* a return value,
  grep for every caller by name — the updated one is not evidence the others were found.
- **Flag a narrow `except (A, B, C)` around a block that has already written files or
  rows.** Cleanup handlers must catch `Exception`; the whole point of the handler is the
  error you did not anticipate. A curated exception list is only appropriate when
  *different* errors need *different* handling and an unhandled one is genuinely fatal.
- **Effects after the last thing that can raise.** Where practical, do the work that can
  fail before the work that leaves state behind.

### Why it wasn't caught the first time

There were no HTTP-level tests at all — no `conftest.py`, no test client anywhere in
`backend/tests/`. Every test called services and helpers directly, so no test ever ran a
router body, and a crash on the first line of the import block was invisible. That is the
structural reason a hard crash shipped with a green suite; a service-level test of
`_register_file_sync` would have passed too, because the helper was correct.

The type annotation on the wrapper was wrong, but `npm run build` type-checks only the
frontend and no Python type checker runs in CI, so the annotation was documentation.

### Fix

- `_register_file_sync` returns a `RegisteredFile` NamedTuple; both call sites use
  attribute access.
- The row-level handler in `run_plan` catches `Exception`, with `asyncio.CancelledError`
  still handled separately and re-raised.
- `backend/tests/conftest.py` adds an ASGI-client fixture (`api_env`) driving the real
  app over `httpx.ASGITransport` against a temp DB, and
  `backend/tests/test_provenance_http.py::test_comfy_run_imports_images_through_the_real_router`
  runs a whole plan through `POST /comfy/run` with a stubbed `ComfyClient`, asserting the
  row reaches `completed` and no orphan files remain.

### Status & date

MITIGATED — the NamedTuple removes this instance and the HTTP harness closes the class,
but nothing prevents a *new* positional tuple return from being introduced.
Last reviewed for staleness: 2026-07-22.
