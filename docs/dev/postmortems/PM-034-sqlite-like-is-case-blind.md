# PM-034: SQLite's LIKE matched another folder's subtree regardless of case

### Symptom

A folder's subtree reached rows in a folder whose name differed only in case:

- Gallery: renaming or moving subfolder `knight` also re-pathed `Knight/a`; deleting
  `knight` filed `Knight/a`'s images at the root.
- Gallery filter `subfolder=knight` listed `Knight/a`'s images. `GET /images/ids` shares
  the filter, so "select all matching" handed a bulk delete or move the other folder's
  images.
- ComfyUI (found first, in the M6 §8b review): deleting folder `knight` in a plan deleted
  `Knight/a`'s rows.
- File Browser, Linux only: `POST /filesystem/delete` on a hand-made `MY_DS/` folder beside
  dataset folder `my_ds/` deleted `my_ds`'s image and video rows, though `rmtree` never
  touched their files.

No error anywhere. The wrong rows were simply in the result.

### Root cause

SQLite's `LIKE` is case-insensitive for ASCII by default (`PRAGMA case_sensitive_like` is
off), and SQLAlchemy's column `.startswith()` / `.endswith()` / `.contains()` compile to
`LIKE`. Every subtree query was `col = :path OR col LIKE :path || '/%'`, so `knight/%`
matched `Knight/a`. The code had already been hardened once against LIKE's *other*
over-match, `_` as a wildcard (escaping, `autoescape=True`), and that fix looked complete:
it made the pattern literal in every way except case.

Gallery folders and ComfyUI folders keep their case (`normalize_subfolder` does not fold
it, and the collision checks are exact), so two folders differing only in case are
legitimate. File paths are another matter: they are case-sensitive on Linux, so the
file-browser case needed a case-sensitive filesystem to reach.

**The first fix overcorrected.** It made the file browser's full-path prefixes exact on
every platform, and the review caught it. Every stored path begins with
`settings.datasets_dir`, whose spelling on Windows follows the launch folder's typed case
(`config.py` derives it from `__file__`, and `manage.ps1` sets the working folder from
`$PSScriptRoot`, so `c:\crucible` and `C:\Crucible` are both possible) or a `.env` edit.
`canonical_abs_path` folds a request's case below the root, never the root itself (and on
macOS it folds nothing). So rows written under one launch's spelling missed the next
launch's exact prefix: renaming a dataset's own folder returned 200 instead of 409, and
deleting `{ds}/images` removed the bytes while the rows, and their versioning backup,
stayed. The old LIKE had been right there by accident.

### Generalizable rule

Flag any SQL `LIKE`, `ilike`, or column `.startswith()`/`.endswith()`/`.contains()` used to
mean "this path or below it", or any other exact prefix. In SQLite it ignores case, and
escaping `_`/`%` does not change that. Then ask which kind of value the column holds. A
**label** the app owns (a gallery folder, a ComfyUI category) is case-sensitive everywhere:
`utils.sql_has_prefix` / `utils.sql_subtree`, an exact `substr` compare. A **filesystem
path** must match the way the filesystem does, which differs by platform:
`utils.sql_has_path_prefix`. Flag an exact compare on a full stored path as readily as a
case-blind one on a label. A LIKE is acceptable only where an over-match is harmless by
construction, such as a pre-filter that a stricter check follows; say so in a comment.

### Why it wasn't caught the first time

The tests for these queries guarded the wildcard over-match (`a_b` vs `axb`) and never put
two case variants side by side. The §8b review fixed the ComfyUI copy and recorded the
gallery's as an open item rather than sweeping for the class, and each file-browser comment
explained `autoescape=True` as if it made the prefix exact. The first fix then treated
"case-sensitive" as a property of the query rather than of the filesystem, and no test
varied the root's spelling between the rows and the request.

### Fix

Branch `fix/case-blind-subtree`. `utils.sql_has_prefix` and `sql_subtree` serve
`dataset_service.repath_subfolder` and `delete_subfolder`, the `images.py` subfolder filter,
and the two ComfyUI clauses that already had the fix inline. `utils.sql_has_path_prefix`
serves the eight prefix tests in `routers/filesystem.py`: an escaped LIKE (the old
behaviour) when `PATHS_FOLD_CASE` is set — Windows and macOS — and exact on Linux.
Case-variant tests sit beside each site's existing tests; `backend/tests/test_filesystem_root_case.py`
pins the root-spelling drift with the flag forced on, so Linux CI runs it too; and
`backend/tests/test_sql_prefix_guard.py` is an AST guard, whose docstring says which
shapes it cannot see.

The deeper fix followed on `fix/root-spelling`. At startup the root's on-disk case is
worked out (`utils.on_disk_spelling`, which follows no link), and
`services/root_spelling.py` rewrites every stored row whose root is a case variant of it
and the same folder on disk (`os.path.samefile`), after an integrity-checked backup. The
setting adopts the new spelling only when that repair went ahead (`docs/dev/database.md`
§ The datasets root's one spelling). The file browser's exact matches (`/delete`'s and
`/rename`'s file arms, `/move`'s, guards C and D) now take `utils.sql_path_equals`, so a
case drift *below* the root cannot strand rows either. For a single file the case-blind
match is only a pre-filter: `_rows_naming` keeps a variant row only when it is the same
file, since on a case-sensitive directory under the flag it is another file's record. The
AST guard flags a bare `==` on a full-path column in `routers/filesystem.py`. The same
review found the orphan sweep comparing resolved paths case-sensitively on macOS, where
`resolve()` keeps the given case, so a dataset folder renamed by case could be
`rmtree`'d. It now compares folded under the flag.
Left on LIKE on purpose, and listed in `docs/dev/shared-utilities.md`: the filename
reservation scans and `filesystem._find_dataset_for_path`'s pre-filter.

### Status & date

MITIGATED — the helpers and the AST guard cover the known shapes; a match through a local
name, or against a column the guard does not list, could still reintroduce it.
Last reviewed for staleness: 2026-11-07.
