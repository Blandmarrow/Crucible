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

This file is the vocabulary itself: the two tables, `services/label_service.py`, the
`/labels` router and `POST /labels/assign`. Where an assignment shows up elsewhere — the
gallery and export filters, the snapshot mirror and restore, cross-dataset copies and
same-dataset derivatives — is `docs/dev/label-consumers.md`; the Settings tab, detail
panel, hotkeys and bulk modal are `docs/dev/labels-ui.md`. Read `docs/labels.md` for the
user-facing description of the same feature.

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
| `copy_labels(db, id_map)` | `batch_copy_dataset`, `duplicate_dataset` (on-disk branch), and the five same-dataset derivative sites (`docs/dev/label-consumers.md` § Versioning and cross-dataset hooks) |
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
| `frontend/e2e/labels.spec.ts` | the end-to-end journey, the hotkey double-tap ordering, and the caption-textarea guard |
