# Labels

A small, global, controlled vocabulary of short strings attached to images — a
**second facet** of organisation alongside the subfolder tree. The subfolder tree
is a single-parent hierarchy, so it can express *what an image is of* but not, at
the same time, *what it is useful for*: images that would make good
special-effects training data are scattered across subject folders, and pulling
them out means either duplicating the folder structure (`fx/explosions`,
`fx/smoke`, …) or losing them.

Labels are deliberately **not tags**. The tags system was removed in `2e3c8c9`
(migration `a8c3e1f2b9d0`) because it overlapped with captions; labels never
touch `Image.caption_text`, are never written into a caption sidecar, and are
never exported as caption tokens. That constraint is enforced by
`backend/tests/test_export_label_filter.py`, not just documented.

Read `docs/labels.md` for the user-facing description of the same feature.

## The naming rule

`label` was already taken twice before this feature existed — `Detection.label`
(a detected class, exposed as `detection_label`/`mask_labels`) and
`BackgroundJob.label` — and every export request body already carries
`label: str | None` as the job's display name. So **no bare `labels` field
appears on any request body or query param.** The filters are `label_filter`,
`label_match` and `label_missing`; the assign body uses `add`/`remove`; the
models are `Label`/`ImageLabel`; the router prefix is `/labels`.

## Schema

`backend/models/label.py` holds both tables.

| `Label` ("labels") | |
|---|---|
| `id` | `String(36)` PK, uuid4 |
| `name` | `String(64)` NOT NULL, unique |
| `color` | `String(16)` NOT NULL, `#6b7280` |
| `hotkey` | `String(1)` NULL, unique |
| `sort_order` | `Integer` NOT NULL, indexed |
| `created_at` | `DateTime` NOT NULL |

| `ImageLabel` ("image_labels") | |
|---|---|
| `id` | `Integer` PK autoincrement (the child-row convention, cf. `models/detection.py`) |
| `image_id` | FK `images.id` ondelete CASCADE, indexed |
| `label_id` | FK `labels.id` ondelete CASCADE, indexed |
| `created_at` | `DateTime` NOT NULL |
| | `UniqueConstraint("image_id", "label_id", name="uq_image_label")` |

Three decisions carry their reasons in the model's own comments, and are worth
repeating because each is the kind of thing a later change would undo:

- **The `Label` PK is a uuid, not an integer.** The id is persisted in three
  places outside its own table — the `VersionImageState` snapshot mirror, the
  gallery's `gallery-state-${datasetId}` blob, and export presets. SQLite's
  `INTEGER PRIMARY KEY` recycles `max(rowid)+1`, so deleting the last label and
  creating another would silently reuse its id and a restore would reattach the
  wrong concept.
- **`image_labels` has no `dataset_id`, deliberately.** It is derivable via
  `images.dataset_id`, and its absence is *why* `batch_move_dataset` needs zero
  changes: that path UPDATEs `Image.dataset_id` in place and never changes
  `Image.id`, so every join row follows for free. Denormalizing it for faster
  per-dataset counts would silently break every cross-dataset move.
- **There is no `Image.labels` relationship.** `Image.source_meta` is the
  standing lesson — a lazy load on an async session raises `MissingGreenlet`
  only on the live path, never in a helper-level unit test. Every read is an
  explicit `select(ImageLabel...)` through `services/label_service.py`.

Case-insensitive name uniqueness is enforced in the router
(`func.lower(Label.name) == body.name.lower()` → 409). The column's `unique=True`
backstops an **exact-case** duplicate only — SQLite's default collation is
case-sensitive — so the case-insensitive backstop is a second, *functional* index,
`Index("uq_labels_name_lower", func.lower(name), unique=True)`. A functional index
rather than `COLLATE NOCASE` on the column, which would also change every ORDER BY
and comparison on `name`. `hotkey` is normalized with `.lower()` and must match
`^[a-z0-9]$`, else 400; `color` must match `^#[0-9a-fA-F]{3,8}$` (`_clean_color`),
because `String(16)` is not enforced by SQLite and the value is interpolated into
inline CSS on every dot and chip — an unchecked `url(http://…)` is a remote fetch per card.

Migrations: `c2a8f6b3d417_add_labels.py`, then
`a4d7e2f9c188_add_case_insensitive_label_name_index.py` for the functional index
(raw `op.execute`, since `op.create_index` renders through a column list and
cannot express `lower(name)`; `check_migrations.py` reports no drift, only a
SQLAlchemy warning that it cannot reflect an expression index). Both FKs are
declared as **table-level** `sa.ForeignKeyConstraint(...)` inside `create_table`;
column-level inline FKs break SQLAlchemy's SQLite reflection and cause permanent
`scripts/check_migrations.py` drift.

## The mirror column

`VersionImageState.label_ids` is a NOT NULL JSON list with a `'[]'`
`server_default`. Its rules, in the model's comment and pinned by
`test_labels_survive_rebuild_paths.py`:

- **Ids, not names.** A *rename* means "same concept, new spelling" — with ids a
  restore reattaches the concept under its current name for free, where names
  would name a label that no longer exists. A *delete* is a deliberate removal,
  and with ids a restore honestly drops the assignment rather than resurrecting
  it. Storing both is worse than either: a rename would flip the blob for every
  image at once and produce a full-dataset "modified" diff for a vocabulary edit
  that changed no image.
- **NOT NULL with `'[]'`**, not nullable — a NULL/`[]` split would make every
  pre-migration row differ from every post-migration row.
- **Stored sorted.** The diff compares with `!=`, so an unsorted list would
  report a reorder as a change.
- **No FK**, matching `image_id`/`source_video_id` on the same model: a snapshot
  must survive the deletion of what it names.

## `services/label_service.py`

CRUD stays inline in the router (following `routers/providers.py`, the closest
precedent for a small global vocabulary table). Everything with a second call
site lives in the service, so the gallery filter and the export filter cannot
drift into meaning different things.

| Helper | Used by |
|---|---|
| `label_filter_clause(label_ids, match, missing)` | `_apply_image_filters`, `_run_export_loop`, `preview_export` |
| `labels_by_image(db, image_ids)` | `list_images`, `get_image`, `create_snapshot`, `copy_labels` |
| `live_label_ids(db, candidate_ids)` | `restore_snapshot`, `duplicate_dataset` (snapshot branch) |
| `copy_labels(db, id_map)` | `batch_copy_dataset`, `duplicate_dataset` (on-disk branch), and the five same-dataset derivative sites below |
| `set_labels(db, wanted)` | `restore_snapshot` |

`ROWS_PER_STATEMENT = 8_000` is the chunk size, and it is a **row** count
(images × labels), not an image count: three bind parameters per row against
SQLite's 32,766 ceiling, and a 20,000-image request carrying three labels is
60,000 rows.

## API surface

`backend/routers/labels.py`, prefix `/labels`. Collection routes are declared
**above** `/{label_id}`, or FastAPI matches `counts`/`assign`/`reorder` as a
label id.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/labels/` | `list[LabelOut]` ordered by `sort_order, name`; `usage_count` from one `GROUP BY` |
| `GET` | `/labels/counts?dataset_id=` | `{counts: {label_id: int}}` for the gallery picker's per-label counts, scoped through `images.dataset_id` |
| `POST` | `/labels/assign` | The single attach/detach endpoint — below |
| `POST` | `/labels/reorder` | `{ordered_ids}` → `sort_order = index`; 400 unless the id set is the whole vocabulary |
| `POST` | `/labels/` | 201; 409 on duplicate name (case-insensitive) or hotkey; 400 on bad charset, blank name or non-hex colour |
| `PATCH` | `/labels/{id}` | `model_dump(exclude_unset=True)` |
| `DELETE` | `/labels/{id}` | 204; `image_labels` rows go via the DB cascade |

`_assert_name_free`/`_assert_hotkey_free` read then write, so two concurrent
creates can both pass. `create_label` and `update_label` therefore commit through
`_commit_unique`, which turns the `IntegrityError` a lost race raises into the
same 409 the pre-check gives — no router in the repo catches `IntegrityError` and
`main.py` registers no handler, so the loser would otherwise get a 500. An
app-level handler would change behaviour repo-wide and is deliberately not what
this does.

The PATCH deviates from `routers/providers.py`, which uses `exclude_none=True`:
`hotkey` has to be clearable with an explicit `{"hotkey": null}`, which
`exclude_none` would silently drop. The deviation carries a comment at the call
site and a regression test.

`DELETE /labels/{id}` relies on the **database** cascade, not an ORM one — there
is no relationship to cascade through. `test_label_cascade_fk.py` opens
`api_env(tmp_path, foreign_keys=True)` for that reason: the harness defaults FKs
OFF, so without the opt-in every assertion there would pass vacuously.

### `POST /labels/assign`

```python
class LabelAssignRequest(BaseModel):
    image_ids: list[str]
    add: list[str] = []       # label ids
    remove: list[str] = []    # label ids
```

Returns `{images, added, removed}`. One endpoint serves the detail panel (1
image), a hotkey (1 image) and the gallery toolbar (up to `SELECT_ALL_ID_CAP`) —
two endpoints would be two idempotency stories.

Validation, in order: 400 on empty `image_ids`; 400 if `add` and `remove` are
both empty; 400 if `len(image_ids) > SELECT_ALL_ID_CAP` (read off
`routers/images.py` at request time, so it is the same bound `/images/ids` hands
out and the toolbar can never build a body this refuses); 400 if `add ∩ remove`;
400 naming any unknown **label** id, because FK enforcement is on in the app and
an unvalidated bad id would surface as an `IntegrityError` → 500. Unknown
**image** ids are skipped silently and `images` reports what matched — a
selection is a client-side set that can go stale, and `useSelectionStore` spans
datasets.

`add` and `remove` are then **de-duped**, and that is execution-critical rather
than cosmetic: the chunk size below is `ROWS_PER_STATEMENT // len(add)`, so a body
repeating one id 20,000 times floors it to one image and builds a single INSERT
carrying 60,000 binds — past the 32,766 a stock Windows `sqlite3.dll` enforces.
After the unknown-id 400 both lists are bounded by the vocabulary.

The resolved live image ids then go through `images_router.guard_batch_datasets`
(public for exactly this second caller), so every dataset the selection touches
must be idle — the guard `batch_resize` and `batch_crop` already have. Restore's
Pass 2c is an authoritative replace, so an assign landing mid-restore would be
discarded without a word; this answers the usual 409 instead.

Execution: existence-filter the image ids, `delete(ImageLabel)` for the removes,
then a SQLite `insert(...).on_conflict_do_nothing(index_elements=["image_id",
"label_id"])`. Idempotency comes from the unique constraint rather than a
read-then-write (which races), and `rowcount` gives an honest "newly added" for
the toast. There is no `BackgroundJob`: this is milliseconds, and must not grow
SSE plumbing.

### Reads on existing payloads

There is no new read endpoint. `ImageOut.label_ids` and `ImageListItem.label_ids`
are filled by the router:

- `GET /images/{id}` — one `labels_by_image` call, the same shape as the
  `detections` attach two lines above it.
- `GET /images/` — one query for the whole page, bucketed into a dict, the
  pattern the effective-license stamp already uses. `ImageListParams.limit` is
  capped at 500, so the `IN` needs no chunking.
- `PATCH /images/{id}/provenance` — the third `ImageOut` producer, filled the same
  way and for the same reason it re-attaches `detections`: the client seeds its
  per-image cache from this response, and `label_ids` is required on the TS type.

## Filters

`label_filter` / `label_match` / `label_missing` are appended to
`ImageFilterParams`, so `ImageIdsParams` and `ImageListParams` inherit them and
`/images/`, `/images/count` and `/images/ids` agree by construction — see
`docs/dev/image-filters.md`. `label_filter` is a JSON array in a string, the same
encoding as `license_filter`, parsed by `utils.parse_id_list_param`.

`label_match` is a plain `str`, **not** a `Literal`, so a bad value is a **400
from the shared filter validator** rather than a 422 from per-route query
parsing — the contract `test_bad_input_is_rejected_identically_by_all_three`
pins. On export it *is* a `Literal` and so gives a 422; that asymmetry is
deliberate and is the one `_parse_flags` already documents.

The SQL block sits after the detection-count block and before `score_filters`,
and uses **correlated `EXISTS` only, never a join**: `/count` runs the same
builder over `select(func.count(Image.id))`, so a join to `image_labels` would
count a two-label image twice and duplicate rows in `/images/`. The license block
stays the only one that mutates the FROM clause, and stays last.

- `label_missing is True` → `~exists`; `is False` → `exists`.
- `label_match="any"` (the default) → one `EXISTS` with `label_id.in_(ids)`.
- `label_match="all"` → one `EXISTS` **per id**, preferred over
  `GROUP BY … HAVING COUNT(...)`, which would need a join and a grouping this
  shared builder must not introduce. Each is index-backed on
  `ix_image_labels_image_id`, and nobody ticks fifty labels.

The 400s live in **`utils.validate_label_filter_params`**, not inline in
`_apply_image_filters`, because export calls the same function — see
`docs/dev/shared-utilities.md`. It raises for: a blank entry in `label_filter`
(the `license_filter` reasoning — silently dropping a blank narrows a mixed list
and voids an all-blank one, both silent lies); an invalid `label_match`;
`label_missing=true` combined with a non-empty `label_filter`, which is
unsatisfiable and indistinguishable from a broken filter; and **more than
`MAX_LABEL_FILTER_IDS` (100) ids**, since `match=all` builds one `EXISTS` per id
and ~1,000 of them overflow SQLite's `SQLITE_MAX_EXPR_DEPTH` into an uncaught 500.
Unparseable JSON is the fifth, and stays in `parse_id_list_param`.

`_apply_image_filters` calls the validator **unconditionally**, beside the
`score_field`/`quality_flag` checks rather than inside the label block:
`?label_match=either` with no filter at all is still bad input, and all three
endpoints must refuse it identically.

`label_filter_clause` additionally raises `ValueError` on a `match` that is
neither `"any"` nor `"all"`, instead of falling through to "any" — a direct
service call with a typo cannot then quietly answer the wrong question. HTTP
callers never reach it, having been 400'd upstream.

## Export

`_run_export_loop` and `preview_export` each take the three params and apply
`label_filter_clause` right after the `subfolders` where. Like `subfolders`, the
label filter **narrows the query** rather than appearing in the exclusion tally:
it defines which images the export is about, not an exclusion over a fixed
population. So `image_count` shrinks and no new counter is needed. The three
request bodies in `routers/export.py` and the preview `GET` carry them too; the
preview parses via `parse_id_list_param`.

**Export validates exactly what the gallery validates.** All four POST/GET
handlers call `utils.validate_label_filter_params` — the three POSTs in the
*request* path beside their `_normalize_mask_labels` normalizers, so a bad shape
is a 400 to the client rather than an already-enqueued job failing later.

The client half matters as much: `ExportPage` encodes the trio through a single
`labelParams()` helper used by **both** the export POST bodies and the preview
query, and it **omits every falsy value**. `label_missing: false` is not "no
filter" — in the documented three-endpoint contract it means *"only images that
carry a label"* — so sending the plain boolean default silently dropped every
unlabelled image from every export while the preview, which stripped falsy values,
promised the full count. The server behaviour is correct and deliberately
unchanged; two encodings of one thing was the bug. `frontend/e2e/export-plain.spec.ts`
guards it by asserting on the files on disk, not on the preview.

`_write_image` and the caption sidecar writer stay untouched — see the invariant
at the top of this file.

## Versioning and cross-dataset hooks

`create_snapshot` prefetches `labels_by_image` once for the dataset (a per-image
query inside the state loop would be N+1 over 20k images) and passes
`label_ids=labels_by_id.get(img.id, [])` — never `or None`; the column is NOT
NULL.

`restore_snapshot` writes labels at the **end of Pass 2c**, before the
`db.commit()` that is the DB→filesystem boundary. Four things about it:

- It keys on **`p.img.id`, not `p.state.image_id`** — Pass 0b can fork a fresh
  uuid or adopt a different row, and writing the snapshot's id would label an
  image in another dataset, or nothing at all.
- Plans with `p.img is None` (`skip_recreate`) are skipped.
- Ids are resolved through `live_label_ids`, with one aggregate warning for
  assignments naming a label deleted since the snapshot — there is no FK to
  catch it.
- `set_labels` is an **authoritative replace, not a merge**: a restore means "the
  dataset looked like this", so a label added after the snapshot disappears,
  exactly as a caption edit does. It deletes only for image ids in `wanted`, so
  `handle_extra_images="keep"` images keep theirs.

A restore targeting a *different* dataset needs no id remapping at all — the
direct payoff of the vocabulary being global.

The diff triple: `VersionImageState.label_ids` is in `_DIFF_COLS` and
`_DIFF_COMPARE_FIELDS`, and **not** in `_HEAVY_DIFF_FIELDS`. It is mutable state
(attach/detach) and therefore diffed, unlike the immutable lineage carve-out
beside it. Because the *comparison* runs on ids, a rename produces no diff at all
— the correct answer. Ids are resolved to names once per diff, after the modified
list is built, and joined to a comma-separated string server-side, so
`DiffModal` needs no change; an id with no row renders `(deleted)`.

Cross-dataset:

- **`batch_copy_dataset`** binds `new_img = Image(...)` into an `id_map` and
  calls `copy_labels` between the insert loop and the FS copies, preserving that
  function's deliberate stage-DB → copy-files → commit ordering.
- **`duplicate_dataset`**, Step 2A (on-disk) builds the same `image_id_map` and
  calls `copy_labels` after the loop — including after the cancellation `break`,
  so a cancelled duplicate keeps labels for whatever it did copy. Step 2B (from a
  snapshot) reads `state.label_ids` off the mirror and resolves against the live
  vocabulary exactly as restore does, in one query for the whole run.
- **`batch_move_dataset`: no change**, and that is a property asserted rather
  than remembered.

Labels travel on a copy while **detections do not** (there are no `Detection`
references in `dataset_service.py`). Not a bug — "this image is a reject" is a
fact about the image — but surprising enough that someone will otherwise "fix"
it.

**Same-dataset derivatives carry labels too**, for the same reason and by the
same rule as `copy_provenance`: a crop is the same picture at a different
framing, so a fact about the picture is still true of it. Five sites build a new
`Image` beside its parent and each calls `copy_labels`:

| Site | Shape |
|---|---|
| `routers/lut.py`, `routers/upscaling.py`, `routers/detection.py` (crop-to-detection) | Accumulate `derivative_ids[parent] = new_img.id` through the loop, drain with one `copy_labels` **after** the loop — so a cancelled run still labels what it did copy — before the trailing commit |
| `routers/images.py::crop`, sync copy-mode tail | The `id=str(uuid4())` is assigned explicitly (the `batch_copy_dataset` precedent) so the join rows can name the crop, and `copy_labels` rides the same transaction |
| `routers/images.py::crop`'s nested `_run_crop_upscale` | Runs in its own session with the parent ORM object gone, so `job_cfg` carries `parent_id`; `copy_labels` re-reads the parent's labels itself, and a `flush()` comes first because `Image.id` is a Python-side default |

A *score* must not travel the same way — those pixels are not the scored pixels —
and `test_video_lineage_mirrors._score_carriers` names these same five sites as
its deliberate exclusion. The label guard is the inverse and is executable:
`test_labels_survive_rebuild_paths.py::test_every_same_dataset_derivative_carries_labels`
walks each function's AST for the `copy_labels` **call** (not a kwarg — four of
the five pass the map positionally).

## Frontend

| File | Contents |
|---|---|
| `src/api/labels.ts` | `labelsApi = { list, counts, create, update, remove, reorder, assign }` |
| `src/hooks/useLabels.ts` | `useQuery(["labels"], staleTime 5 min)` + memoized `byId`/`byHotkey`, and `isLoaded` (the query's `isSuccess`) for the bounds checks below |
| `src/components/settings/LabelsPanel.tsx` | The Settings tab body — the 20-colour palette, inline rename, hotkey capture, up/down reorder, delete via `ConfirmDialog` naming `usage_count` |
| `src/components/settings/HotkeyCaptureButton.tsx` | Captures the next keypress while focused |
| `src/components/common/LabelPicker.tsx` | `LabelCheckList` (searchable checkbox list + `footer` slot) and `LabelPicker` (the same behind a trigger). The one control behind all four picking surfaces — see `docs/dev/styling.md` § Anchored popovers, including why `placement` is not a style choice |
| `src/components/image/LabelsPanel.tsx` | Detail-page block: removable chips + an inline `LabelPicker`, one `assign` per tick |
| `src/components/gallery/LabelsBulkModal.tsx` | Dumb modal (form state only) with *Add* and *Remove* `LabelCheckList`s |
| `src/utils/keyboard.ts` | `isTextEntryTarget(e)`, adopted by both of `ImageDetailPage`'s pre-existing keydown effects |

The query key is a bare `["labels"]` with no dataset in it, because the
vocabulary is app-wide. Writers call `invalidateLabelScope(qc)` from
`constants/queryKeys.ts` rather than listing keys inline — there are four writers
(Settings, the detail panel, the hotkey, the toolbar), which is exactly the drift
that file exists to prevent. It takes **no `datasetId`** and invalidates every key
at its bare prefix, matching `invalidateProvenanceScope`: the bulk assign it
mostly serves labels a selection that can straddle datasets, so scoping to one
pane's id left other datasets' grids and card dots stale.

Both the gallery and the Export page bounds-check a label filter against the
vocabulary and drop ids whose label was deleted — the `licenseFilter` precedent.
Without it the grid silently shows zero images with nothing on screen explaining
why (the picker is hidden entirely while the vocabulary is empty, so there is not
even a control to open). In the gallery, dropping anything also resets paging.

The check is **derived during render**, gated on `useLabels().isLoaded` — the
`frameVideoId` guard in `GalleryPage` is the precedent, and `isLoaded` (the
query's `isSuccess`) is what separates "still fetching" from "the vocabulary is
genuinely empty", which `labels` alone collapses into `[]`. It is deliberately not
a mount-once `useRef` latch, which had three escapes: an empty vocabulary never
reconciled at all, a label deleted later in the session never reconciled either,
and the Export page's dataset-switch effect re-seeded the filter from a different
blob past a ref that had already fired. `handleResetToDefaults` there resets the
label trio alongside the other fourteen filters, or the debounced persist writes
it straight back into the blob it just cleared.

`GalleryPage` persistence needs **six** edits, because the filter blob is
hand-rolled there rather than using `useDebouncedPersist`: the inline type in
`loadSavedState`, the `useState` seed that reads it, `liveStateRef` (both lines),
the debounced write (the `JSON.stringify` literal **and** the dep array — a field
whose own change moves no other dep is otherwise only written by the unmount
flush, and a reload drops it), the unmount blob (the destructure **and** its
literal), and `handleResetFilters`. Neither write site merges with what is
stored, so a field present in one literal and missing from the other is deleted
on whichever path runs. See `docs/dev/persistence.md`.

**A debounced field is two of those seeds, not one.** `search` and
`detectionLabel` each commit out of a `*Input` draft through a 350 ms timer whose
only bail-out is `input === committed`, so a restore must seed **both halves**
from the one stored value, and every clear (Reset filters, the input's ×) must
clear both together. Seeding or clearing one half leaves the pair unequal, which
is exactly the state that makes the timer fire on mount and throw away the
restored page — PM-012, in `docs/dev/postmortems.md`. Persist only the committed
value; a persisted draft leaves the restore no correct choice.

### Hotkeys

`labelHotkeysEnabled` lives in `store/uiPrefsStore.ts`, not only in localStorage,
so toggling it in Settings re-renders an already-mounted detail pane in split
view.

**Conflict prevention is structural, not a blocklist.** The `[a-z0-9]` charset
cannot express Escape, Space, ArrowLeft/Right or Delete — the five keys
`ImageDetailPage` already binds — so no reserved-key set is needed and none can
go stale when a sixth binding is added. The instinct to add
`RESERVED = new Set([...])` is the version that rots. Cross-label collisions are
a server 409, pre-empted in the UI by naming which label owns the key.

The handler guards, all required: the pref is on; `paneCtx.paneId === activePaneId`;
no modal open; no modifier held; **`!e.repeat`** (a held key would fire dozens of
assigns — the guard the two existing effects do not need and this one does); and
`!isTextEntryTarget`, which is load-bearing because the caption editor is a
`<textarea>` on this page and typing "a" would otherwise label the image.
Semantics are **toggle**, so a mistyped key is undone with the same key.

`ImageDetailPage` mounts the detail panel with ``key={`labels-${image.id}`}`` —
**prefixed**, because `ProvenancePanel` is a sibling in the same children array
and already uses a bare `image.id`, and two siblings sharing one key is a
reconciliation error that renders the panel twice rather than warning about it.

## Non-goals

- **No caption interaction, ever.** This is what keeps labels from re-becoming
  the tags system.
- No Statistics panel for labels (add later, once the useful view is known).
- No labels on videos.
- No auto-labelling, no hierarchy, no per-dataset scoping.

## Tests

| File | Covers |
|---|---|
| `test_labels_crud_http.py` | sort_order, case-insensitive 409, hotkey 409/400, non-hex colour 400 on create *and* PATCH, rename detaches nothing, `{"hotkey": null}` clears, partial reorder 400, dataset-scoped counts |
| `test_label_assign_http.py` | double-assign idempotency, add+remove in one call, the five 400s, chunk-boundary crossing, a repeated-id `add` that would otherwise blow the bind ceiling, the busy-dataset 409 |
| `test_label_cascade_fk.py` | the four deletion paths, under `foreign_keys=True` |
| `test_label_filters_http.py` | any/all/missing, the row-multiplication regression, three-endpoint agreement, the 400 shapes and the `MAX_LABEL_FILTER_IDS` cap, dataset scoping |
| `test_labels_survive_rebuild_paths.py` | the rebuild-path guard over the four paths it names (and both of `duplicate_dataset`'s branches, checked separately), the derivative-site guard, plus snapshot/restore/copy/move/duplicate round-trips |
| `test_export_label_filter.py` | narrowing, preview-equals-export, the caption invariant, and the export **request** layer: the trio reaching the loop over the wire and the 400s/422 the endpoints answer |
| `frontend/e2e/labels.spec.ts` | the end-to-end journey, and the caption-textarea guard |
