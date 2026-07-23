---
name: doc-maintenance
description: Documentation maintenance for this repo — update CLAUDE.md, docs/dev/ topic files, or user docs; run a doc audit; split an oversized doc; check doc drift; propose a project skill. Use when a change adds or alters a user-visible feature, or when asked for a doc audit.
---

# Documentation maintenance

The docs are split three ways. Put a new fact in exactly one of them:

| Where | What lives there | Audience |
|---|---|---|
| `CLAUDE.md` | Commands, the request/job data flow, shared utilities, key invariants, the Documentation Map | Always loaded into every conversation |
| `docs/dev/*.md` | One subsystem in depth | Loaded on demand via the Map |
| `docs/*.md` + `README.md` | How to *use* a feature | End users |

`docs/*.md` (flat) and `docs/dev/*.md` are different audiences with different content.
Do not confuse them, and never duplicate one into the other — cross-reference instead.

## Where does this fact go?

- **Narrow, subsystem-specific** → append to the relevant `docs/dev/` file under the
  best-fitting heading. If that makes the file's "Read this when..." hint incomplete,
  update the hint (keep trigger keywords front-loaded).
- **Cross-cutting** (a new shared utility, a universal invariant, a pattern every module
  must follow) → Key invariants or Shared utilities in `CLAUDE.md`. Test: *"would I want
  this loaded even for a task in an unrelated subsystem?"* If no, it is subsystem-specific.
  Utility entries stay one line there; detailed behaviour goes in the utility's docstring.
- **Procedural and recurring** → a skill, not a doc. See "Proposing a skill" below.

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

`scripts/check_docs.py` warns when a file exceeds its word budget (`TOPIC_MAX_WORDS = 3500`
for `docs/dev/`, `USER_MAX_WORDS = 2500` for `docs/`, `CLAUDE_MAX_WORDS = 4000`) or when any
single paragraph exceeds `MAX_BLOCK_WORDS` (250). Read that script's module docstring for
why the budgets count words rather than lines — the short version is that a line budget is
satisfiable by writing longer lines, and that is how facts end up stacked four clauses deep
where the next editor cannot see them.

When a file trips the budget:

1. **Split along subsystem lines, not by size.** An `and` in a filename is a warning sign.
   Look for a section that is really a different subsystem — it may even be misfiled.
2. **Name the new file after the user doc it mirrors** where one exists
   (`docs/dev/statistics.md` ↔ `docs/statistics.md`). That convention is repo-wide.
3. **Give it a `# Title`, a one-paragraph intro naming what it covers, and pointers** to the
   sibling files a reader will need next.
4. **Update every inbound reference.** They are inline-code paths (`` `docs/dev/x.md` ``),
   and a stale one is a check FAIL. Grep for the old filename before and after.
5. **Add a Documentation Map row** in `CLAUDE.md`: contents, keyword-front-loaded trigger,
   approximate word count. A missing row is a WARN, and the Map is how the file gets found.
6. **Fix pointers that were intra-file and are now cross-file** — a `§ Section` reference to
   a section that moved. The check cannot see these; grep `§` in the files you touched.

Do not append a new feature to the least-bad existing file. If a topic file has become a
dumping ground of unrelated facts, propose splitting it in that session rather than
continuing to append.

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
5. Run `python scripts/check_docs.py` and fix what it reports.

When asked for a "doc audit" outside a branch context, do the same against the whole file:
diff each topic file's claims against the code it describes and propose corrections.

## Conventions

- **Plain relative paths only.** Never write `@docs/...` or `@CLAUDE` — the `@path` syntax
  recursively auto-loads the target into every conversation. This is a check FAIL.
- **Cross-reference, don't duplicate.** Documenting something in file A that depends on
  something in file B gets a one-line pointer ("see `docs/dev/versioning.md` for the
  copy-on-write mechanism"), not a copy.
- **Refresh the Map's word counts** when you substantially edit a file.

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
