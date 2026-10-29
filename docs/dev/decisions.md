# Decision log

**Status: living.** Lightweight ADR-style records. This file is the **index**: one stub per
decision with its status and a one-line ruling, linking the **full record** in
[decisions/](decisions/).

**Status vocabulary:** **Decided** (committed; reopening needs a new superseding entry),
**Leaning** (preferred direction, safe to build on, with a real alternative still open),
**Exploring** (open), **Deferred** (parked), **Superseded by N**. Only the user (the human
maintainer) moves a decision to Decided. Record who decided and when:
"**Decided** (YYYY-MM-DD, by the user)".

**Link targets:** other docs link to the headings below. Heading text is stable by
contract; never reword one. To reopen a decision, add a new entry that supersedes it
rather than silently editing the old record.

**Record format** (one file per decision in decisions/, named NN-slug.md): title
`# Decision N: Title`; an italic back-link to this index; then bullets **Date**,
**Status**, **Decision**, **Rationale**, **Rejected alternative(s)**, optionally
**How it's verified**, **Amendments** (dated), and **Superseded because** when it is.

The log starts on 2026-10-29. Rulings made before it live where they were made — the
"why" in CLAUDE.md § Key invariants, the postmortems and the topic files — and are not
back-filled here; a decision that reopens one of them gets an entry then.

## 1. Documentation architecture

**Decided** (2026-10-29, by the user): the docs follow docs-architecture v3.1: an
always-loaded CLAUDE.md, on-demand topic files in `docs/dev/`, word budgets enforced by
`scripts/check_docs.py`, with Crucible's own budgets and the maintenance rules in the
`doc-maintenance` skill. Full record:
[decisions/01-documentation-architecture.md](decisions/01-documentation-architecture.md).

## 2. CLAUDE.md budget and hub routing

**Decided** (2026-10-29, by the user): `MAX_CLAUDE_WORDS` rises from 5,400 to 6,000, and
large subsystem families route through a hub doc with one Map row per family and their
detail files in a `SPLIT_INDEXES` folder, so the Map grows by family rather than by file;
gallery and image similarity/scoring are the first.
Amends decision 1. Full record:
[decisions/02-claude-md-budget-and-hub-routing.md](decisions/02-claude-md-budget-and-hub-routing.md).
