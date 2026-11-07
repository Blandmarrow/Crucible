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

## 3. App-stored prompts

**Decided** (2026-10-30, by the user): user-written LLM text and empty category names move from
browser storage to the database, with a one-time import that keeps the old keys for one release;
per-browser preferences stay in the browser. Full record:
[decisions/03-app-stored-prompts.md](decisions/03-app-stored-prompts.md).

## 5. M7 goes ahead without M4

**Decided** (2026-11-02, by the user): M7 no longer waits for M4 to merge, because the other
unmerged branches may never land; whichever of the two lands second resolves their six shared
files. Number 4 is held by the unmerged M4 branch. Full record:
[decisions/05-m7-goes-ahead-without-m4.md](decisions/05-m7-goes-ahead-without-m4.md).

## 6. Layer-7 default, and no new style model

**Decided** (2026-11-03, by the user): M7 ports the DINOv2 layer-7 style default next, and
none of Kaloscope, CSD or PE-Core is adopted for now, since none beat it across media. Full
record:
[decisions/06-layer-7-default-and-no-new-style-model.md](decisions/06-layer-7-default-and-no-new-style-model.md).

## 7. M6 is built whole on one branch

**Decided** (2026-11-03, by the user): every M6 stage is built on `feat/m6-comfy-automation`
and nothing merges until the whole build is done; numbers 7 to 10 were held on that branch until it merged (PR #110, 2026-11-07).
Full record:
[decisions/07-m6-built-whole-on-one-branch.md](decisions/07-m6-built-whole-on-one-branch.md).

## 8. Structured generation builds prompts from templates and files by folder

**Decided** (2026-11-03, by the user): one outline call returns axes, descriptors and variation
pieces, and code builds every prompt from a template; a per-leaf LLM rewrite is optional. Leaves
are filed by folder, not labels, and Top up comes before the unattended loop. Full record:
[decisions/08-templates-first-filed-by-folder.md](decisions/08-templates-first-filed-by-folder.md).

## 9. Automated runs step forward on job completion

**Decided** (2026-11-03, by the user): a `Run` row is advanced by `advance_run()` each time one
of its child jobs finishes, instead of a parent job on a second queue that polls; stages call
service functions, so M8 stages 2 and 3 land first (corrected 2026-11-04: they did, and §4
still builds the score, caption and export start functions itself). Full record:
[decisions/09-runs-step-forward-on-job-completion.md](decisions/09-runs-step-forward-on-job-completion.md).

## 10. Unload a local Strata server while ComfyUI generates

**Decided** (2026-11-06, by the user) that Crucible frees a local Strata LLM server's GPU
before ComfyUI generates, so captioning and prompt writing can run on a 16 GB card beside
ComfyUI; **Leaning** on the mechanism: an HTTP unload behind an opt-in setting, and no load
step, since Strata reloads on the next request. Full record:
[decisions/10-unload-strata-while-comfyui-generates.md](decisions/10-unload-strata-while-comfyui-generates.md).
