# Workspace tools: hardware stats, file browser, Logs & Booru lookup

This file covers the smaller workspace surfaces: the sidebar hardware meters and their `/system` endpoints, the file browser page and its `/filesystem` router, the Logs page (job history + JS error console), and the Booru tag lookup page.

## System hardware stats

Router: `backend/routers/system.py`, two endpoints both mounted at `/api/v1/system`.

**`GET /system/gpu`** returns `{ name, used_mb, total_mb, utilization_pct }` by trying three external sources in priority order: (1) `nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits` for NVIDIA GPUs; (2) `rocm-smi --showmeminfo vram --csv` for AMD ROCm (ROCm 6.x CSV: `device,VRAM Total Memory (B),VRAM Total Used Memory (B)` — GPU name falls back to device ID e.g. `card0`); (3) `torch.mps.current_allocated_memory()` / `torch.mps.driver_allocated_memory()` for Apple Silicon (name = `"Apple Silicon (MPS)"`, `utilization_pct = null` since unified memory has no fixed GPU partition). Returns `{ name: null }` when all three fail. **Do not revert to `torch.cuda.memory_allocated()` or `torch.cuda.mem_get_info()` here** — both are per-process CUDA context reads that miss VRAM allocated by other processes (e.g. Ollama).

The MPS probe is the one link in that chain that imports torch, and it runs on exactly the machines where the first two failed. A cold `import torch` costs ~14 s and holds the GIL, so **`_mps_stats` returns before the import unless `sys.platform == "darwin"`, and on macOS defers to `_mps_stats_sync` in an executor** — inline, it froze the whole event loop (every concurrent request, not just this one) on the first sidebar poll after each start. Never call `_mps_stats_sync` directly. `backend/tests/test_system_http.py` covers the probe order, the null row and that guard (a `sys.modules["torch"]` landmine raising a `BaseException`, so the probe's own `except Exception` cannot mask a regression).

**`GET /system/cpu-ram`** returns `{ cpu_pct, ram_used_mb, ram_total_mb }` via `psutil` (`psutil>=5.9` in `requirements.txt`). Both `psutil.cpu_percent(interval=0.1)` and `psutil.virtual_memory()` are run together inside `asyncio.get_running_loop().run_in_executor()` to avoid blocking the event loop (both calls perform blocking I/O — `/proc/meminfo` on Linux, `GetPerformanceInfo` on Windows). Wrapped in `try/except`; returns `{ cpu_pct: 0.0, ram_used_mb: 0, ram_total_mb: 0 }` on any failure.

**Frontend**: The Sidebar footer (`frontend/src/components/layout/Sidebar.tsx`) renders three stacked hardware meters — CPU, RAM, and GPU — using a shared `MeterRow` helper component (defined in the same file). CPU and RAM are driven by `useCpuRamStats` (`frontend/src/hooks/useCpuRamStats.ts`); GPU is driven by `useGpuStats` (`frontend/src/hooks/useGpuStats.ts`). Both hooks poll every 5 s via TanStack Query with `retry: false`. In-loop SSE progress emitters (captioning, detection) use `_device.memory_reserved_mb()` from `backend/ml/device.py` — subprocess overhead is unacceptable inside the per-image inference loop, and those emitters only cover PyTorch-loaded models anyway.

## File browser

Router: `backend/routers/filesystem.py`, prefix `/api/v1/filesystem`, registered in `main.py`.

| Endpoint | Purpose |
|---|---|
| `GET /roots` | Windows drive roots (`C:\`, `D:\`, …) |
| `GET /list?path=` | Directory listing — dirs first, then files, both alphabetical; `media_kind` per entry (`"image"`, `"video"` or null), replacing the older `is_image` boolean |
| `GET /preview?path=` | Serve any file in `MEDIA_EXTENSIONS` directly (`FileResponse`); videos get `video_mime` as the content type, and the `FileResponse` supplies the Range/206 a `<video>` needs to seek |
| `GET /image-meta?path=` | `{width, height, format, file_size_bytes, generation_metadata}` — reads file without touching DB. Image-only on purpose: a video container carries no EXIF or generation parameters |
| `POST /move` | Move file/dir; syncs the `Image` or `Video` row's `file_path`/`filename`, and 409s rather than re-homing a registered file — or a folder holding one (below) |
| `POST /rename` | Rename in place; syncs `file_path`/`filename` on whichever of `(Image, Video)` matches |
| `POST /delete` | Delete file or directory (recursive); deletes from the filesystem first, then removes the matching `Image` **and** `Video` records — so a failed FS deletion leaves DB records intact |
| `POST /mkdir` | Create directory |

**`POST /move` has two branches, chosen from `src` *before* `shutil.move` runs** — `src` no longer exists afterwards, so a late `is_file()`/`is_dir()` answers False for both and the whole sync becomes dead code (PM-011).

- **File branch.** Looks up the `Image` or `Video` row whose `file_path` is the source — also before the move, so the 409 below lands with the filesystem untouched, matching the collision guards' convention. If a row exists and the destination is not inside that row's own dataset, the move is **refused with a 409**: re-homing is `batch_move_dataset`'s job (provenance materialization, stats refresh, NULLing `Image.source_video_id`, moving the poster/thumbnail), and none of it happens here. The condition is `new_ds is None or new_ds.id != row.dataset_id` — deliberately broader than "another dataset", because a registered file moved out of the datasets tree entirely is equally broken: `utils.safe_dataset_path` then 403s every request for its bytes. Files with no DB row still move anywhere. Otherwise only `file_path`/`filename` are rewritten.
- **Directory branch.** Loads the rows under the moved folder first — before `shutil.move`, for the same reason the file branch does — and applies the same containment guard: if any `Image`/`Video` row has `file_path` under the old prefix, the destination must resolve to *the* dataset those rows belong to, or the move is **refused with a 409**. The predicate is set equality (`{row.dataset_id for …} != {new_ds.id}`) rather than "some dataset was found": a moved tree can span two datasets, and then no single destination is the same dataset for all of them, so `!=` refuses a move that would re-home half the rows and strand the rest. Without this, `POST /move {src: "{ds}/images", dst_dir: "/outside"}` returned 200 and left every row's `file_path` outside `settings.datasets_dir`, where `utils.safe_dataset_path` 403s each one's bytes — a blank gallery with nothing in the DB saying why.

  Permitted moves then prefix-rewrite `file_path` for every matched row, plus that model's derived path (`Image.thumbnail_path`, `Video.poster_path`) **when and only when it also starts with the old prefix**. A poster lives under `videos/`, so it travels with the folder and its stored path must follow; an image's thumbnails live in `{ds}/thumbnails/`, beside `images/` rather than under it, so moving `images/` alone leaves them valid and an unconditional rewrite would point every row at a file that was never there. No `refresh_stats` on either branch: it recomputes counts and sizes only — never a path, never `dataset_id` — and the guard makes every permitted move a same-dataset one, so none of those numbers can change.

**No UI caller.** `filesystemApi.move` exists in `frontend/src/api/filesystem.ts` and nothing calls it — there is no move menu item, drag/drop or destination picker. The endpoint is API-only; the wrapper is scaffolding for a move UI that has not been built.

Still open, recorded rather than fixed: `Image.subfolder` travels unchanged, so a file moved between subfolders of its own dataset keeps the old sidebar grouping. Moving a *dataset's own folder* is now refused whenever it holds any row, so only an **empty** dataset's folder still moves, leaving `Dataset.folder_path` stale. The other hole the guard does not close is a **derived-artifact directory**: `{ds}/thumbnails` and `{ds}/videos/thumbnails` hold no `file_path`, only `thumbnail_path`/`poster_path` values, so no row matches the prefix, the move returns 200 and every derived path is stranded.

**DB sync**: `_find_dataset_for_path(db, file_path)` checks if `file_path` is inside any dataset's `folder_path` and returns the dataset — a `LIKE` pre-filter over `Dataset.folder_path` (longest first) with `relative_to` as the authoritative check. Both branches' guards use it; rename and delete match on the exact path and need no dataset lookup.

**Path safety**: `sanitize_abs_path()` (from `backend/utils.py`) rejects null bytes and requires an absolute path. No further sandbox — this is a local desktop app with intentional full-filesystem access.

**Name collisions** are 409s, checked before the filesystem is touched: `POST /move` onto an existing name at the destination, `POST /rename` onto an existing sibling, `POST /mkdir` where the name already exists (as a directory *or* a file — the guard is `exists()`, not `is_dir()`). `backend/tests/test_conflict_paths_http.py` asserts each one leaves both sides on disk untouched.

Frontend page: `FileBrowserPage.tsx`, route `/file-browser`, sidebar nav item "File Browser". Three-panel layout (`200px | 1fr | 280px`): left = drive roots + quick-access links, middle = breadcrumb + file list + context menu (rename/delete/import), right = image preview + `<GenerationMetadata>` panel.

API client: `frontend/src/api/filesystem.ts` — thin wrappers over all endpoints; `previewUrl(path)` returns a URL string for use in `<img src>`.

## Logs page

Frontend page: `frontend/src/pages/LogsPage.tsx`, route `/logs`, sidebar nav item "Logs" (with a red badge showing the unread error count when `errorConsoleStore.errors.length > 0`).

Two tabs rendered with the standard `.tabs` / `.tab` CSS classes:

**History tab** (`HistoryTab` component):
- Fetches `GET /api/v1/jobs/?limit=200` via TanStack Query (`queryKey: ["jobs"]`, `staleTime: 10_000`). The `limit` param was made configurable in `backend/routers/jobs.py` (`Query(50, ge=1, le=500)`) — `LogsPage` passes 200; existing callers (none pass `limit`) keep the former 50-record default.
- Client-side filter input searches across `label`, `job_type`, and `dataset_id` fields.
- Each row: `StatusBadge` (pending `--fg-mute` / running `--accent` / completed `--good` / failed `--bad` / cancelled `--fg-dim`), label (falls back to `job_type`), dataset ID chip (first 8 chars), relative timestamp (`Xm ago`) with absolute on `title`, duration (`finished_at − started_at`), and `done/total` counter when `total_items > 0`. Failed jobs show `error_msg` below the row in `--bad`.
- **Refresh** button re-invokes `refetch()`.

**Errors tab** (`ErrorsTab` component):
- Reads from `errorConsoleStore` (same data as the `ErrorConsole` overlay). See `docs/dev/frontend-core.md` § Error console for store details.
- Toolbar: error count summary, **Copy Errors** (calls `formatErrorsForCopy` → `navigator.clipboard.writeText`), **Clear** (calls `clearErrors()`).
- Each entry: timestamp, type badge (`error` / `rejection` / `render`), message, source file/line/col, collapsible stack trace `<details>`.
- Empty state: "No JS errors captured this session."

The Errors tab button in the tab bar shows a red pill badge when `errorCount > 0` — the same count drives the sidebar `NavItem` `tail` prop (with `tailColor="var(--bad)"`).

## Booru tag lookup page

A read-only tag-name/post-count lookup against external image boards. Nothing is persisted — it's a reference tool for finding correct booru tag spellings and gauging tag popularity while captioning.

**Router**: `backend/routers/booru.py`, prefix `/booru`. Two endpoints, no service-layer DB access:
- `GET /booru/search` — `q` (required, `min_length=1`), `source` (`safebooru` | `gelbooru`, regex-validated, default `safebooru`), `limit` (`1..100`, default 20). Dispatches to the matching service function.
- `POST /booru/autocomplete` — `AutocompleteRequest { prefix, source="safebooru", limit=10 }`. Same dispatch; intended for type-ahead (the current `BooruPage` doesn't wire it up — search is submit-driven).

`backend/tests/test_booru_http.py` pins the dispatch, the validation 422s and the gelbooru credential forwarding. It patches `search_safebooru`/`search_gelbooru` **on the router module** (the names the router imported), which also sidesteps the service's process-wide TTL cache — patching `booru_service` instead would let one test's rows leak into another's and would still make real HTTP calls on a cache miss.

**Service**: `backend/services/booru_service.py`. `search_safebooru(query, limit)` hits Danbooru's safe host `https://safebooru.donmai.us/tags.json` (`search[name_matches]=*query*`, ordered by count); `search_gelbooru(query, limit, api_key, user_id)` hits `https://gelbooru.com/index.php` (`s=tag` API). Gelbooru credentials come from `settings.gelbooru_api_key` / `settings.gelbooru_user_id` (env vars `GELBOORU_API_KEY` / `GELBOORU_USER_ID` via `config.py`); they're optional — omitted when blank, so anonymous requests still work but may be rate-limited. Both functions normalize results to `{tag, count, category, source}` dicts, mapping the numeric category id to a name (`0` general, `1` artist, `3` copyright, `4` character, `5` meta) via `_safebooru_category` / `_gelbooru_category`. Guardrails: an in-module 5-minute TTL cache (`_cache`, keyed `{source}:{query}:{limit}`, evicts expired entries on read), an `asyncio.Semaphore(2)` capping concurrent outbound requests, a 10-second per-request timeout, a 0.5s politeness delay before each Gelbooru call, and a blanket `except Exception: return []` so a booru outage never surfaces as a 500.

**Frontend**: `frontend/src/pages/BooruPage.tsx`, route `/booru` (`App.tsx`, also in `PageRenderer`/`PaneHeader` for split-view), sidebar nav "Booru Browser". API wrapper `frontend/src/api/booru.ts` (`booruApi.search` / `booruApi.autocomplete`), result type `BooruTag`. Search is submit-driven: the text input and Source/Limit selects update local state, and `handleSearch` (Enter or the Search button) copies `query` into a separate `search` state that is the actual query trigger — `useQuery({ queryKey: ["booru-search", search, source, limit], enabled: search.length > 0 })`. TanStack Query's default cache makes repeat searches instant (backed additionally by the backend 5-minute cache; the footer notes "Results cached for 5 minutes"). The `SOURCES` list includes `danbooru`/`e621` marked `supported: false` — selecting one and searching shows a toast ("… is not yet supported") and skips the request. Results render as a table (tag in category color, category `badge`, post count, and a **Copy** button that writes the tag to `navigator.clipboard` and toasts). Limit choices are 20/50/100 (default 50 in the UI).
