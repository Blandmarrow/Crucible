# M1 — Drift check upgrade

**Status: archive (landed 2026-10-29).** *Record for the M1 line in the
[roadmap](../roadmap.md#landed). Why the work was done: [decision 1](../decisions.md#1-documentation-architecture).*

Gate: the drift check covers every doc under `docs/dev/`, proves itself in CI, and runs on
every PR.

- [x] `scripts/check_docs.py` replaced by the docs-architecture v3.1 reference checker, with
      Crucible's budgets and settings in its CONFIG block. `docs/dev/` is scanned recursively,
      so the postmortems and the transient folders are link-checked for the first time;
      link labels and postmortem status sync are new checks.
- [x] `.github/workflows/check-docs.yml` runs on every PR with no path filter, and runs
      `--self-test` first.
- [x] The rating-feature plan moved from docs/dev/roadmap/roadmap.md to
      [rating-plan/README.md](../rating-plan/README.md), registered in `TRANSIENT_DIRS` with
      `docs/dev/gpt-review/`.
- [x] This roadmap and the [decision log](../decisions.md) created; the `doc-maintenance`
      skill carries the rules for keeping both current.
