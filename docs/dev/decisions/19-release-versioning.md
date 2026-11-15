# Decision 19: Releases use three-number semantic versions, tagged vX.Y.Z

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-15.
- **Status:** **Decided** (2026-11-15, by the user), while planning v1.4.0.
- **Decision:**
  - **Semantic versioning with exactly three numbers**, MAJOR.MINOR.PATCH, read for an app
    rather than a library:
    - **PATCH** (1.4.0 → 1.4.1): fixes only. Nothing new for the user to learn.
    - **MINOR** (1.4 → 1.5): new features, and updating works the normal way. A migration
      that runs on its own is still a minor bump, even when it changes stored data.
    - **MAJOR** (1 → 2): updating needs a manual step beyond `manage update`, such as a
      new Python version, a changed setting, a removed feature, or "update to an earlier
      version first".
    - No fourth number and no leading zeros. A fix to a fix is the next patch.
  - **Tags are `vX.Y.Z`, titled "Crucible X.Y.Z".** The `Version-*` tags up to
    `Version-1.3.0` stay as they are.
  - **Release when users gain something**, not on a timer: a minor at the end of a
    milestone or tier, and a patch soon after a fix users would notice.
  - **The notes are written for users**, in the shape of the 1.3.0 notes, with an **Update
    notes** section first whenever the user must act or should expect a change on update.
    Docs, refactors and test-only PRs are left out.
  - **The version lives in one place**, `backend/version.py`. The health route reports it
    and Settings shows it with a link to that release's notes. `frontend/package.json`
    stays `0.0.0`, since it is private and a second copy would drift.
  - The `release` skill (`.claude/skills/release/SKILL.md`) is the procedure.
- **Rationale:**
  - Before this, releases were hard to read as a series: four-part numbers (`1.0.5.31`,
    `1.1.01`) that tools sort wrongly, a patch release carrying features (1.2.2, 19
    merges, notes of one line), ten releases in one week of May, a release of 47 merges,
    and two tags with no release. The app also reported `1.0.0` whatever it was, so a bug
    report could not say which version it came from.
  - The owner worried the major number would climb fast, since much of the work is large.
    Under these rules it does not: size is not the test, a manual step is. No release up
    to 1.3.0 would have needed a major bump.
- **Examples:**
  - The CLIP QuickGELU migration (decision 17) clears stored vectors on first start. It
    is a **minor** bump with an Update notes line telling users to re-run scoring.
  - Removing M5's one-time browser import strands only users who jump from 1.2.1 or older.
    It is a **minor** bump with "update to 1.3 or later first" in the Update notes, unless
    other changes in the same release make the step unavoidable.
- **Rejected:**
  - Calendar versioning (`2026.11.0`): no judgement call on each bump, and the number shows
    an install's age, but it cannot warn "read before updating". Crucible's migrations
    sometimes need that warning.
  - Keeping the `Version-` prefix: it matches the history, but `v` is what GitHub's tooling
    and most readers expect, and v1.4.0 is a clean point to switch.
  - Rewriting or deleting the old tags: they are public, and others may point at them.
- **Revisit when:** a release needs a manual step that a major bump would make too much of,
  or the owner wants the number to carry something it cannot today.
