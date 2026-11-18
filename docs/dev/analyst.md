# Analyst — the dataset health report, notes and goal

M12, the dataset analyst ([decision 21](decisions/21-dataset-analyst.md)): Crucible works out
the facts about one dataset itself, and an LLM (phase 2 onward) explains them and suggests
what to do. This file is the family's entry point; the tables below route from a code path to
the file in `docs/dev/analyst/` that owns it. The design and the phases still to build are in
[analyst-plan/README.md](analyst-plan/README.md). The `GET /images/` filters a finding links
through are `docs/dev/image-filters.md`; the Stats cache and index pin the report reuses are
`docs/dev/statistics.md`; the snapshot row it records on is `docs/dev/versioning/storage.md`. How a user reads the page is `docs/analyst.md`.

## The family

| File | Covers |
|---|---|
| [health.md](analyst/health.md) | `health_service.py`: the findings catalogue and its constants, `FilterSpec`, the cache and its key, the card-badge write-back, dismissals and resurfacing, the snapshot delta |
| [notes.md](analyst/notes.md) | Dataset and app-wide notes: the three tables, soft delete and history, the size caps, a dismissal as a note, the `/notes` and `/app-notes` routes, the goal fields on `Dataset` |
| [ui.md](analyst/ui.md) | The frontend: `HealthPage`, the notes editor, the dialogs' goal fields, the card badge, their query keys and routing |

## Where things live

| Editing… | Read |
|---|---|
| `backend/services/health_service.py`, `backend/routers/health.py`, `backend/schemas/health.py` (`Finding`, `FilterSpec`, `HealthReport`) | `docs/dev/analyst/health.md` |
| `backend/services/version_service.py` (`create_snapshot`'s `findings_summary`) | `docs/dev/analyst/health.md` § The delta against the last snapshot; + `docs/dev/versioning.md` for the rest |
| `backend/models/notes.py`, `backend/services/notes_service.py`, `backend/routers/notes.py` | `docs/dev/analyst/notes.md` |
| `backend/models/dataset.py` (`goal`, `target_resolution`, `health_summary`), `backend/routers/datasets.py` (`_apply_goal_fields`) | `docs/dev/analyst/notes.md` § The goal fields; + `docs/dev/dataset-operations.md` for the rest of the routes |
| `frontend/src/pages/HealthPage.tsx`, `frontend/src/components/health/`, `frontend/src/api/health.ts`, `frontend/src/api/notes.ts` | `docs/dev/analyst/ui.md` |
| `frontend/src/components/datasets/GoalFields.tsx`, `HealthBadge.tsx` | `docs/dev/analyst/ui.md`; + `docs/dev/dataset-operations.md` for the dialogs, `docs/dev/datasets-page.md` for the card |
| `frontend/src/components/common/FilteredImagesModal.tsx` | `docs/dev/statistics-panels.md`; + `docs/dev/analyst/ui.md` for the Health page's use |

## The rule a later phase must not break

Crucible finds the problems; the LLM explains them. A finding is computed here, tested here,
and useful with no LLM at all: a later phase may rank, explain or act on findings, but never
invent one. Phase 2 adds the second rule, that everything an LLM sees goes through one
`build_context` and its privacy filter; it gets a CLAUDE.md key invariant when it lands.
