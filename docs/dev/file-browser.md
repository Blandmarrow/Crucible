# File browser — the `/filesystem` router and its page

The File Browser is the app's window onto the whole filesystem: it lists and previews any
folder, and its three mutations — move, rename and delete — keep the database in step when
they touch a file a dataset has registered. This file is the family's entry point: the
tables below route from a code path to whichever file in `docs/dev/file-browser/` owns it.
The path guards those routes share (`canonical_abs_path`, `within_datasets_dir`) are indexed
in `docs/dev/shared-utilities.md`, the dataset folder layout they defend is
`docs/dev/dataset-operations.md`, and the Windows file-lock class the preview panel works
around is `docs/dev/postmortems/PM-021-served-file-handle-blocked-windows-delete.md`. The
other workspace surfaces — hardware meters, Logs and Booru — are `docs/dev/workspace.md`.
The user doc is `docs/file-browser.md`.

## The family

| File | Covers |
|---|---|
| [listing.md](file-browser/listing.md) | The endpoint table, the listing side (`/roots`, `/list`, `/preview`, `/image-meta`) and `/mkdir`, the name-collision 409s, path safety and `canonical_abs_path`, the router's holes still open, and `FileBrowserPage` with its `released` preview prop |
| [move.md](file-browser/move.md) | `POST /move`: the file and directory branches, the canonical-media-folder 409, the derived-artifact guard, DB sync and `_find_dataset_for_path` |
| [rename-delete.md](file-browser/rename-delete.md) | `POST /rename`, with its thumbnail-stem 409 and the guards C/D/A/B over `_DATASET_LAYOUT_DIRS`, and `POST /delete`, with its versioning-hook ordering and `_add_orphan` |

## Where things live

Each row names the file that **owns** that code. A `+` clause is a second genuine owner,
not a see-also, and says which part of the code it owns.

| Editing… | Read |
|---|---|
| `backend/routers/filesystem.py` (`/roots`, `/list`, `/preview`, `/image-meta`, `/mkdir`, the shared path handling) | `docs/dev/file-browser/listing.md` |
| `backend/routers/filesystem.py` (`move`, `_find_dataset_for_path`) | `docs/dev/file-browser/move.md` |
| `backend/routers/filesystem.py` (`rename`, `delete`, `_add_orphan`) | `docs/dev/file-browser/rename-delete.md` |
| `backend/utils.py::canonical_abs_path` | `docs/dev/file-browser/listing.md` § Path safety; + `docs/dev/shared-utilities.md` for the index entry |
| `frontend/src/pages/FileBrowserPage.tsx`, `frontend/src/api/filesystem.ts` | `docs/dev/file-browser/listing.md` § Frontend |
| `backend/tests/test_conflict_paths_http.py`, `test_filesystem_path_canonical_http.py` | `docs/dev/file-browser/listing.md` |
| `backend/tests/test_filesystem_rename_http.py`, `test_filesystem_delete_http.py` | `docs/dev/file-browser/rename-delete.md` |
