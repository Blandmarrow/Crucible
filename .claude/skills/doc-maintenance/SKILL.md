---
name: doc-maintenance
description: Documentation maintenance for this repo — update CLAUDE.md, docs/dev/ topic files, or user docs; run a doc audit; split an oversized doc; check doc drift; propose a project skill. Use when a change adds or alters a user-visible feature, or when asked for a doc audit.
---

# Documentation maintenance

The docs are split three ways. Put a new fact in exactly one of them:

| Where | What lives there | Audience |
|---|---|---|
| `CLAUDE.md` | Commands, the request/job data flow, key invariants, the Documentation Map | Always loaded into every conversation |
| `docs/dev/*.md` | One subsystem in depth | Loaded on demand via the Map |
| `docs/*.md` + `README.md` | How to *use* a feature | End users |

`docs/*.md` (flat) and `docs/dev/*.md` are different audiences with different content.
Do not confuse them, and never duplicate one into the other — cross-reference instead.

## Where does this fact go?

- **Narrow, subsystem-specific** → append to the relevant `docs/dev/` file under the
  best-fitting heading. If that makes the file's "Read this when..." hint incomplete,
  update the hint (keep trigger keywords front-loaded).
- **Cross-cutting** — a universal invariant or a pattern every module must follow goes in
  § Key invariants in `CLAUDE.md`; a new *shared utility* goes in
  `docs/dev/shared-utilities.md` instead, since the index is a lookup rather than something
  every conversation needs resident. Test for either: *"would I want this loaded even for a
  task in an unrelated subsystem?"* If no, it is subsystem-specific. Utility entries stay
  one line; detailed behaviour goes in the utility's docstring.
- **Procedural and recurring** → a skill, not a doc. See "Proposing a skill" below.
- **A ruling on direction** ("we will / won't do X, because…") → the decision log, and
  **what comes next and what blocks it** → the roadmap. See "Keeping records current".

## Keeping records current

Three records sit beside the topic files. Each is updated by the session that changes what
it records, not as a separate chore:

- **The roadmap (`docs/dev/roadmap.md`) is ticked in the same session** that lands the
  work, like the drift check. It owns order and dependencies only: link the topic file for
  the *what* and the decision for the *why*. When a milestone completes, expand the next one
  into tasks, and shrink the finished one to a one-line "Landed" entry linking a record in
  `docs/dev/roadmap-archive/` — archive eagerly, as soon as only follow-ups remain. Update
  the dated "Next session" block before you stop. A feature with its own design plan keeps
  it in a transient folder (`TRANSIENT_DIRS` in `scripts/check_docs.py`, like
  `docs/dev/rating-plan/`); the roadmap names it and never restates its stages.
- **Decisions go in the log (`docs/dev/decisions.md`).** A new direction gets a stub there
  plus a `docs/dev/decisions/NN-slug.md` record (the format is in the index's intro). Only
  the user moves a decision to Decided, and the record says who and when. Never reword an
  existing heading — other docs link to it. To reopen a decision, add a superseding entry
  rather than editing the old record. Rulings made before the log started (2026-10-29) are
  not back-filled; one gets an entry when it is reopened.
- **Incidents become postmortems.** After debugging anything that shipped broken or failed
  in a surprising way, add `docs/dev/postmortems/PM-NNN-slug.md` (copy PM-000, next free
  number) and an index row. The index row's Status must match the detail file's
  `### Status & date` section: the check WARNs when they differ (`ENABLE_PM_STATUS_SYNC`).
- **New records carry a status line** (`**Status: living.**`, or `archive (landed DATE)` for
  a roadmap-archive record). Existing topic files do not, by decision — see
  `docs/dev/decisions/01-documentation-architecture.md`.

## User-facing features need user-facing docs

`docs/dev/` explains a subsystem to whoever maintains it. It never counts as documenting
the feature. When a change adds or alters something a user can see — a page, a sidebar
item, a settings tab, a setup step — update the user docs **in the same change**:

- A whole subsystem (its own page + settings) earns its own `docs/<topic>.md` plus a
  README Docs-table row. `docs/comfyui.md` is the model.
- A smaller capability is a section in the `docs/<topic>.md` that already covers its area.
- **`docs/features.md` is an index, not a container.** It gets a row pointing at the topic
  doc, never prose. It previously held ten subsystems and reached 4,700 words; the word
  budget now prevents that.
- README's Workflow chain, Prerequisites, and Docs table are part of the change when the
  feature affects them.

`scripts/check_docs.py` link-checks these files but **cannot tell that a feature is missing
from them**. That check is yours.

## Splitting an oversized doc

`scripts/check_docs.py` warns when a file exceeds its word budget (`MAX_TOPIC_WORDS = 3500`
for everything under `docs/dev/`, subfolders included; `MAX_USER_WORDS = 2500` for `docs/`
and `MAX_README_WORDS = 2500`; `MAX_CLAUDE_WORDS = 6000` — that one expresses a ~280-line
ceiling, converted at CLAUDE.md's own density of ~21.5 words per line, raised from 5,400 by
decision 2 in `docs/dev/decisions.md`) or when any single
paragraph exceeds `MAX_BLOCK_WORDS` (250). The folders in `TRANSIENT_DIRS` are exempt. The
budgets live in that script's CONFIG block, and its module docstring says why they count
words rather than lines — the short version is that a line budget is satisfiable by
writing longer lines, and that is how facts end up stacked four clauses deep where the next
editor cannot see them.

**The remedy for an over-budget file is a split. It is never compression.** Squeezing
accurate prose into denser prose is the failure the budgets exist to prevent, not a way to
satisfy them: CLAUDE.md once sat at exactly 200 lines across eleven consecutive commits
while its word count grew 21%. A budget warning is a WARN precisely because it is not a
fix-it-now item — it asks for a *structural* change, and structural changes do not belong
in the tail of a session that was about something else.

Deleting text is legitimate only when the text is **false**, **duplicated** in a sibling
file (replace it with a cross-reference), or **re-derivable from the code in one read** —
a function signature, an exhaustive parameter list. Rewriting a correct paragraph to be
shorter is not on that list, and neither is dropping the *why* behind an invariant: the
why is the part that stops the next editor from "simplifying" the rule away.

### When a file trips the budget

Do not split now, and do not trim. **Finish the task you were actually asked to do**, then
record the seam in `docs/dev/pending-splits.md` — see that file for the entry format. Two
or three sentences: which sections move, the new filename, why the seam is there, and
anything the next session cannot cheaply rediscover.

The two halves of a split want opposite conditions, which is the whole reason they are
separated in time:

- **Choosing the seam** wants the session that just worked the file. You know which section
  stopped belonging, and which one was misfiled to begin with. That knowledge is gone
  tomorrow, and it is cheap to write down now — it is recall, not analysis.
- **Executing the split** is an exhaustive multi-file sweep, and it wants a *fresh* context.
  Late in a long session it is the worst-served kind of work, and it is the one thing the
  checker cannot fully verify: broken paths and anchors are caught, a badly chosen seam
  never is.

So the split runs as the **first** action of the next session that would append to that
file — not the last action of this one. That ordering also puts it in front of the person
who most benefits from it, since they are about to add a fact to the file.

An over-budget file with no entry in `docs/dev/pending-splits.md` is its own WARN, so the
handoff cannot be silently skipped. Delete the entry in the commit that performs the split.

### Executing a split

1. **Split along subsystem lines, not by size.** An `and` in a filename is a warning sign.
   Look for a section that is really a different subsystem — it may even be misfiled. The
   over-budget warning prints a per-section word breakdown to make the candidates visible.
2. **Leave both halves real headroom** — aim for each under ~60% of budget
   (`SPLIT_TARGET_FRACTION`). Two files at 3,400 words have bought nothing and will both
   trip again on the next edit. If the natural seam cannot get there, it is the wrong seam.
3. **Name the new file after the user doc it mirrors** where one exists
   (`docs/dev/statistics.md` ↔ `docs/statistics.md`). That convention is repo-wide.
4. **Give it a `# Title`, a one-paragraph intro naming what it covers, and pointers** to the
   sibling files a reader will need next.
5. **Update every inbound reference.** They are inline-code paths (`` `docs/dev/x.md` ``),
   and a stale one is a check FAIL. Grep for the old filename before and after.
6. **Add a Documentation Map row** in `CLAUDE.md`: a keyword-front-loaded trigger, then
   ` — ` and only what the file holds that neither the trigger nor the file's own intro
   paragraph already names, then the approximate word count. The intro is the file's table
   of contents; a row that restates it costs every conversation those words. A missing row
   is a WARN, and the Map is how the file gets found. **Inside a hubbed family** (a hub doc
   whose detail folder is in `SPLIT_INDEXES`, per decision 2), the new file goes in that
   folder and gets a row in the hub's routing table instead; CLAUDE.md keeps one row per
   family.
7. **Fix pointers that were intra-file and are now cross-file** — a `§ Section` reference to
   a section that moved. The check cannot see these; grep `§` in the files you touched.
8. **Relabel links whose text names the old file.** A markdown link labelled `x.md` that now
   opens `y.md` is a check FAIL (`ENABLE_LINK_LABELS`); repointing the target alone leaves it.

Do not append a new feature to the least-bad existing file. The queue holds files that need
restructuring, not only files that are numerically over — an entry is welcome at any size.
Only a dumping ground sitting *below* the 60% floor needs the `(structural)` marker, which
stops the checker reading it as a split that already happened.

### Give a file headings before it needs a seam

A file organised with `**Bold**:` lead-ins instead of `##` headings cannot be split well:
the over-budget warning picks a seam by reporting per-section word counts, and with no
headings at all it can only say "read the file". **Five** topic files are in that state today.
A further thirteen have no `## ` heading but do use `###`, which is enough — `sections()`
splits on the shallowest level a file actually uses, so those still get a per-section
breakdown. So eighteen files want headings *added* and five want them *at all*; expect the
file you are editing to be one of the eighteen. Every file with a seam recorded in
`docs/dev/pending-splits.md` carried `##` headings when the seam was chosen — so a
heading-less file has neither structure nor a seam waiting for it.

So when editing a heading-less file, add the `##` structure while you are there — the bold
lead-ins already mark every boundary, so it is a cheap edit that turns a future split from
a judgement call into a mechanical one. This is a distinct remedy from splitting, and it is
the one that has to come first.

## The end-of-branch doc audit

Most doc drift is not stale-by-neglect; it is prose that was accurate when written and was
overtaken by a later commit **on the same branch**. One audit found 44 such discrepancies,
four of them self-contradictions between two paragraphs of the branch's own docs.

So before opening a PR:

1. `git diff main... --name-only` — list every doc the branch touched.
2. Re-read each one against the branch's **final** state, not the state at the commit that
   wrote it. Identifiers renamed mid-branch are the usual casualty.
3. Verify every claim you cannot see is still true — an endpoint's params, a constant's
   value, a component's name, whether a field still exists. Read the code; do not trust
   the prose that is already there.
4. Check the user docs for features the branch shipped that they never mention.
5. Run `python scripts/check_docs.py` and fix what it reports — it is also CI
   (`.github/workflows/check-docs.yml`), where only its FAIL checks (missing inline-code
   paths, markdown links and anchors, the `@docs/` footgun, a link labelled `x.md` that opens
   another file) block a PR; the rest are warnings. CI also runs `--self-test` first, which
   proves the checker still fails on seeded errors — run it too after editing the script.

When asked for a "doc audit" outside a branch context, do the same against the whole file:
diff each topic file's claims against the code it describes and propose corrections.

## Conventions

- **Plain relative paths only.** Never write `@docs/...` or `@CLAUDE` — the `@path` syntax
  recursively auto-loads the target into every conversation. This is a check FAIL.
- **Cross-reference, don't duplicate.** Documenting something in file A that depends on
  something in file B gets a one-line pointer ("see `docs/dev/versioning.md` for the
  copy-on-write mechanism"), not a copy.
- **Refresh the Map's word count** when you substantially edit a file. `check_map_words`
  WARNs when the hand-written `Words` cell drifts by more than `MAP_WORDS_TOLERANCE` (5%) or
  `MAP_WORDS_FLOOR` (50 words), whichever is larger, and again if a Map row loses the cell
  entirely. The warning prints a value to use, rounded to 50.

## Proposing a skill

Reference documentation stays in `docs/dev/` — never duplicate it into a skill. Propose a
skill only for **procedural** knowledge meeting all three:

1. It is a *workflow* (a sequence of steps/commands), not facts about the code.
2. It has recurred, or clearly will, across sessions.
3. It benefits from automatic triggering and/or a bundled script whose code shouldn't
   occupy context (only script *output* costs tokens).

**Never create a skill without approval.** Propose it in this exact format and wait:

> **Skill proposal:** `<name>` — <one sentence: what workflow it captures>.
> **Trigger description:** "<the frontmatter description, keyword-front-loaded>"
> **Bundles:** <scripts/templates, or "none">
> **Why a skill and not docs:** <one sentence>

If approved: keep SKILL.md focused on workflow steps, put reusable code in bundled scripts,
and keep the description short and keyword-rich (every skill's description is always loaded
and dilutes trigger matching for the others). If rejected, don't re-propose unless
circumstances change. Be conservative — a handful of high-value skills beats many marginal
ones.
