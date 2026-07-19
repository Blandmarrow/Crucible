# Postmortem index

This file indexes past incidents as one-line rows so code reviews and bug investigations
can check new code against failure classes we have already been burned by. Each real
incident gets a detail file under `docs/dev/postmortems/` (copy
`docs/dev/postmortems/PM-000-template.md` to `PM-NNN-short-slug.md`, next free number).

**Usage note**: treat LIVE and MITIGATED entries as an active checklist for their code
class. STRUCTURAL entries are kept for history only — a refactor made that class of bug
impossible — and should not drive review attention. Keep the Symptom column greppable:
phrase it with the words someone would actually search for when the bug resurfaces.

| ID | Symptom | Root-cause category | Status | Detail |
|---|---|---|---|---|
| PM-001 | Restore failed: UNIQUE constraint failed: images.dataset_id, images.filename (filename swaps/renumber chains, deleted-name reuse) | Batch write permuting values under a unique constraint without staged updates (SQLite checks constraints per statement) | MITIGATED | `docs/dev/postmortems/PM-001-restore-filename-collision.md` |
| PM-002 | Pre-restore/checkout auto-snapshot appeared on "main" and moved main's head while working on another branch | Auto-created record resolved context from a hardcoded name instead of current state | MITIGATED | `docs/dev/postmortems/PM-002-auto-snapshot-wrong-branch.md` |
| PM-003 | Restore crashed with PK IntegrityError / snapshot couldn't materialize an image after batch move to another dataset | Scope-removal path (move) bypassed the versioning deletion hook keyed on DB deletes only | MITIGATED | `docs/dev/postmortems/PM-003-move-bypassed-versioning-hook.md` |
| PM-004 | Setup printed "CUDA-enabled PyTorch installed" but `torch.cuda.is_available()` stayed False (`2.12.1+cpu`); ML silently ran on CPU; SAM3 `ModuleNotFoundError: No module named 'triton'` on Windows | Capability claimed from a package manager's exit code; `pip install "pkg>=X"` is a no-op when a wrong-variant build already satisfies the specifier | MITIGATED | `docs/dev/postmortems/PM-004-silent-cpu-torch-fallback.md` |
