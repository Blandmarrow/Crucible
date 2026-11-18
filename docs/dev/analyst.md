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
| [chat.md](analyst/chat.md) | The chat (phase 2): the `chats`, `chat_messages` and `llm_usage` tables, the token budget settings, privacy defaults and overrides, `build_context` and its categories, the preview route |
| [sending.md](analyst/sending.md) | Sending a message: the order of a send, the streamed call and its two time limits, usage before the reply, the SSE events and resume, budgets, cancel and the sweeps, the provider limit shared with captioning |

## Where things live

| Editing… | Read |
|---|---|
| `backend/services/health_service.py`, `backend/routers/health.py`, `backend/schemas/health.py` (`Finding`, `FilterSpec`, `HealthReport`) | `docs/dev/analyst/health.md` |
| `backend/services/version_service.py` (`create_snapshot`'s `findings_summary`) | `docs/dev/analyst/health.md` § The delta against the last snapshot; + `docs/dev/versioning.md` for the rest |
| `backend/models/notes.py`, `backend/services/notes_service.py`, `backend/routers/notes.py` | `docs/dev/analyst/notes.md` |
| `backend/models/dataset.py` (`goal`, `target_resolution`, `health_summary`), `backend/routers/datasets.py` (`_apply_goal_fields`) | `docs/dev/analyst/notes.md` § The goal fields; + `docs/dev/dataset-operations.md` for the rest of the routes |
| `frontend/src/pages/HealthPage.tsx`, `frontend/src/components/health/`, `frontend/src/api/health.ts`, `frontend/src/api/notes.ts` | `docs/dev/analyst/ui.md` |
| `frontend/src/components/datasets/GoalFields.tsx`, `HealthBadge.tsx` | `docs/dev/analyst/ui.md`; + `docs/dev/dataset-operations.md` for the dialogs, `docs/dev/datasets-page.md` for the card |
| `backend/models/chat.py`, `backend/schemas/chat.py`, `backend/routers/chats.py`, `backend/services/analyst_context.py`, `openai_providers.analyst_privacy` | `docs/dev/analyst/chat.md`; + `docs/dev/llm-providers.md` for the rest of the provider |
| `backend/services/chat_runner.py`, `backend/ml/chat_stream.py`, `backend/services/llm_limiter.py` | `docs/dev/analyst/sending.md`; + `docs/dev/captioning/jobs.md` for the caption side of the limiter |
| `frontend/src/components/common/FilteredImagesModal.tsx` | `docs/dev/statistics-panels.md`; + `docs/dev/analyst/ui.md` for the Health page's use |

## The rule a later phase must not break

Crucible finds the problems; the LLM explains them. A finding is computed here, tested here,
and useful with no LLM at all: a later phase may rank, explain or act on findings, but never
invent one. Phase 2 adds the second rule, a CLAUDE.md key invariant: everything an LLM sees
about a dataset is built by `analyst_context.build_context`, and its privacy filter is applied
there and nowhere else (`analyst/chat.md` § `build_context`: the one choke point).
