# Decision 15: The big columns stay in the row; the Stats scans pin their index

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-11.
- **Status:** **Decided** (2026-11-11, by the user), both halves: no side table now, with a
  trigger to reopen it, and the dataset-wide Stats scans pinned to `ix_images_dataset_id`.
- **Decision:** M9 stage 7's question, answered from its own measurement
  ([scale-plan/README.md § Stage 7](../scale-plan/README.md#stage-7--move-the-embeddings-into-their-own-table-only-if-stage-6-says-so);
  the tables are the stage 7 addendum of `backend/scripts/scaling_bottlenecks_report.md`).
  - **The layout is left alone.** The three embedding blobs and `generation_metadata` stay
    in the `images` row. Nothing is built: no `image_embeddings` table, no second file, no
    rebuild with the big columns last. `backend/scripts/bench_split_layout.py` and its
    smoke test stay, so the measurement can be run again.
  - **Reopen it when a dataset nears 50,000 photo-shaped images or 20,000 ComfyUI-shaped
    ones**, the sizes where the cost starts to be felt. Reopening is a new entry that
    supersedes this one, and it starts by running the script again.
  - **The dataset-wide Stats scans are pinned** with `INDEXED BY ix_images_dataset_id`:
    the two row fetches, the coverage counts, the `aesthetic_model` group, the flag sums,
    the embedding count and the license group. **The cache validator is not pinned**: it
    reads `ix_images_dataset_updated_at` as a covering index and is slower under any other.
    **A subfolder-scoped Stats call is timed before it is pinned**, and pinned only if the
    forced index wins there too, because `ix_images_dataset_subfolder` may serve it better.
  - **The pin is documented wherever a later editor would look** (the user's condition):
    at the pin, at the index's declaration in the model, in `docs/dev/database.md` and
    `docs/dev/statistics.md`, and in the plan guard test.
  - **A plan guard test covers the Stats statements**, pinned or not (already the user's
    call on 2026-11-11, before the measurement).
  - **A copy carries what it carries today.** A cross-dataset copy, a duplicated dataset
    and every derivative get no embeddings, and the next quality run fills them in; a move
    keeps them, because the row keeps its id.
- **Rationale:**
  - **The split pays only at sizes nobody has.** With the embeddings in a side table the
    Stats scans were 15–31× faster cold and 1.2–2.8× faster warm at 1M photo rows. The
    owner's datasets are under 2,000 images, where every one of these statements already
    costs milliseconds warm. This is the reasoning that shrank stage 4
    ([decision 13](13-scale-work-first.md), the 2026-11-10 amendment).
  - **The half that helps the data in use is the risky half.** On ComfyUI-shaped rows the
    larger blob is `generation_metadata` (about 55 KB a row). Moving the embeddings alone
    took a cold row fetch from 6.3 s to 4.7 s at 20k; moving both took it to 0.16 s. But
    `generation_metadata` is written by seven constructors, mirrored on `VersionImageState`
    and carried by snapshot and restore, so moving it touches the paths that fail silently.
  - **The split has a cost of its own.** A statement that needs a blob then joins the side
    table: the embedding count was 2.6× slower warm, a style similarity page 2.3×.
  - **Waiting costs little.** The scale plan said to migrate "while databases are still
    small, since it rewrites every row". Moving the embeddings out of 1M rows took 142 s
    with the journal off: minutes at that size, seconds at any size in use. The argument
    for doing it early mostly falls away.
  - **The pin is the cheap win, and it is there at every size measured.** It needs no
    schema change, no new index and no extra space: `ix_images_dataset_id` has existed
    since the first migration. On today's layout it took the cold row fetch from 95 s to
    14 s at 1M, from 13.7 s to 4.4 s at 100k, and from 6.3 s to 3.5 s on ComfyUI 20k, and
    every pinned statement was faster or the same warm. It reads a dataset's rows in table
    order, where the index SQLite picks since stage 11 (`ix_images_dataset_updated_at`)
    reads them in an order scattered across the file.
  - **The pin's risk is a developer's, not a user's.** A hint that names a missing index
    is an error, not a slowdown. A user's database gets its schema only from the
    migrations, so the index can go missing only if a later change drops or renames it,
    most plausibly as "redundant": nine other indexes start with `dataset_id`. The guard
    test and the migration drift check both stand in front of that, and the comments say
    why the index must stay.
- **Rejected alternatives:**
  - **The embeddings in a side table now.** Small and contained (one write site), but it
    is the half that helps photo-shaped data, at a size nobody has, and it slows the
    embedding count and style scoring's reads.
  - **Both moved, in two changes.** The larger win on ComfyUI-shaped rows, and the widest
    change of the four, for the reason above.
  - **The big columns last in the row.** No reader changes, and the warm cost of reading
    past the blobs goes (2.1 s to 0.9 s for the `aesthetic_model` group at 1M). But cold,
    every scan still reads a page a row (51 s against 3.8 s for the split), the seed's
    even row sizes flatter it, and every column a later migration adds lands after the
    blobs again.
  - **A second, attached file for the blobs**, to keep regenerable data out of the five
    rotating backups. Not weighed further once the split was parked. SQLite cannot enforce
    a foreign key across files, so it would need a cleanup sweep; that stands as the
    objection when the split is reopened.
  - **No pin, a guard test only.** The test would show a plan change in CI, but the plan
    it guards would be the slower one.
  - **`NOT INDEXED` instead of a named index.** As fast as the pin on the bench files,
    which hold one dataset; in an install with several it scans every dataset's rows.
- **How it's verified:** the experiment's own cross-check (every layout and index choice
  returned the same rows in all eight runs), two machines with different disks and SQLite
  versions agreeing on the order of the layouts, and the row layout reproducing stage 6b's
  figures. The pin itself is verified when it is built: the plan guard test asserts the
  pinned index in each statement's plan, which also proves the hint reached the SQL.
- **Amendments:**
  - **2026-11-11, the subfolder-scoped call is not pinned.** Timed as the decision asked,
    on the kept bench files (the "subfolder" section of the report's stage 7 addendum).
    SQLite already reads a subfolder-scoped Stats statement through
    `ix_images_dataset_subfolder`, which lists that subfolder's rows in table order, and
    the forced index lost on every statement: at 100,000 rows, 512 ms against 278 ms warm
    for the row scan of a subfolder holding 30% of the rows, and 455 ms against under a
    millisecond for a 20-row subfolder (5.1 s against 19 ms cold). `_stats_scan`
    (`backend/services/dataset_service.py`) pins the dataset-wide form only, and
    `backend/tests/test_stats_plan.py` asserts both halves.
  - **2026-11-11, how the pin is built.** SQLAlchemy's SQLite dialect drops a table hint
    without a word (checked on 2.1.6), so `backend/database.py` implements the compiler's
    `get_from_hint_text` and `indexed_by` wraps `Select.with_hint` for it. The guard test
    reads the compiled SQL, the plan, and every statement the two Stats endpoints really
    send.
