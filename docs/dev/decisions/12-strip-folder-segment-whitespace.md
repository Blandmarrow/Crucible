# Decision 12: Folder segments are stripped of surrounding whitespace

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-08.
- **Status:** **Decided** (2026-11-08, by the user).
- **Decision:** `utils._subfolder_parts`, which both `normalize_subfolder` and
  `join_subfolder` use, strips the whitespace at both ends of every segment of a gallery or
  ComfyUI folder path. Interior spaces are part of the name, so `dark knight` stays one
  folder, while `knight ` and `knight /  ` both become `knight`. Migration `b8d2f4a6c1e3`
  rewrites the rows stored before the rule. Detail: `docs/dev/database.md` § Folder paths
  are stored stripped (migration b8d2f4a6c1e3).
- **Rationale:**
  - A blueprint leaf (`services/blueprint_expand.py`) and a library category
    (`comfy_library.normalize_category`) already stripped their segments, so a hand-typed
    `knight ` folder was one the leaf `knight` never adopted or locked. Found in M6's §8
    review and left open for this ruling.
  - Every folder match is an exact compare, so the rule alone would have split stored data
    from new input. Stored rows are rewritten instead.
  - Gallery folders are labels in the database. Merging two that differ only by padding moves
    no file. Windows cannot even create a folder whose name ends in a space, so no one can
    rely on that spelling.
- **What the rewrite does on purpose:**
  - Two folders that differ only by padding merge into one.
  - Snapshot rows (`version_image_states.subfolder`) are rewritten too, or restoring an old
    snapshot would bring the padded folder back. A snapshot's folder names change, while its
    images and their placement do not.
  - A stored ` .. ` segment would strip to `..`, which every normalizing route refuses, so
    the rewrite drops `..` segments as `join_subfolder` does. That files those images where
    a run already filed them.
  - The database is copied to `backups/<stem>.pre-subfolder-strip.db` first, and the
    upgrade stops if that copy fails.
  - The Runs page's remembered start form (`threshold_settings.run_form`, a blob whose shape
    the frontend owns) is left as it is. A padded folder there only shows in the form, and
    starting the run strips it.
- **Rejected alternatives:**
  - **Leave stored rows alone and strip only new input.** A padded legacy folder could then
    never be reached by a typed name, and the gallery would list it next to its stripped twin.
  - **Strip only at the end of the whole path** (`str.strip()`). `knight /a` would still be
    a different folder from `knight/a`.
  - **A startup repair, as the datasets root's spelling has** (`services/root_spelling.py`).
    That repair runs at every start because it depends on the runtime root. This rule is
    database-only and runs once, which is what a migration is for.
- **How it's verified:** `backend/tests/test_join_subfolder.py` (the rule),
  `backend/tests/test_migration_strip_subfolder_segments.py` (the rewrite and its backup,
  with `upgrade()` driven against a real SQLite file) and
  `backend/tests/test_subfolder_segment_strip_http.py` (upload, folder import, folder
  delete and a saved recipe). Run by hand on 2026-11-08 with `alembic upgrade head` against
  a seeded throwaway database.
