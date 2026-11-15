---
name: release
description: Cut a Crucible release on GitHub — pick the version number (semantic, three numbers), draft user-facing release notes from the PRs merged since the last tag, bump backend/version.py, tag vX.Y.Z and publish. Use when asked to release, ship a version, tag, bump the version or write release notes.
---

# Releasing Crucible

The rules this follows are decision 19 (`docs/dev/decisions/19-release-versioning.md`):
three-number semantic versions, tags `vX.Y.Z`, notes written for users. Read it if a bump
is not obvious. A release is public and outward-facing: **nothing is tagged or published
until the user has approved both the number and the notes** (step 6).

Run from the repo root in Git Bash.

## 1. Preflight

```bash
git fetch -q origin && git status -sb | head -1     # on main, clean, "## main...origin/main" with no ahead/behind
gh run list --branch main --limit 3                 # the newest run on main is green
```

Stop and report if `main` is dirty, ahead or behind, or its latest CI run failed. The
Windows backend job runs only on `main` (decision 11), so this is where its result counts.

## 2. Smoke test

Run the `qa-smoke` skill and quote each stage's result line. Stop on a failure.

## 3. Gather what changed

```bash
last=$(git describe --tags --abbrev=0 origin/main)  # the previous release tag
git log --oneline --merges "$last"..origin/main     # merged PRs
git log --oneline --no-merges --first-parent "$last"..origin/main   # direct commits to main
```

For each PR, read its title and the top of its body with
`gh pr view <n> --json title,body`. Sort each into **user-facing feature**, **faster**,
**user-facing fix**, or **leave out** (docs, roadmap, refactors, tests, CI). A direct commit
gets the same treatment; it has no PR number, so name it by what it fixed.

Then look for anything a user must act on or should expect on update: a new Alembic
migration under `backend/alembic/versions/` (read its docstring; does it clear or relabel
data?), a changed setting or env var, a new prerequisite in `manage.ps1`/`manage.sh`, an
outside service that changed (a host, an API).

## 4. Pick the number

From the current `backend/version.py`, by decision 19:

- any manual step beyond `manage update` → **MAJOR**
- otherwise any new feature → **MINOR**
- otherwise → **PATCH**

State the choice and its reason in one line.

## 5. Draft the notes

Write them to the scratchpad as `release-notes-vX.Y.Z.md`, in this shape. Omit an empty
section, and omit **Update notes** entirely when there is nothing to say.

```markdown
## Update notes
- **<What to do or expect.>** <Why, in one or two sentences, with the exact page and button names.> (#PR)

## What's new
- **<Feature name as the UI shows it>**: <what it does and where to find it>. (#PR)

## Faster
- <What got faster, for whom.> (#PR)

## Fixes
- <What was wrong, from the user's side, and what happens now.> (#PR)

**Full changelog**: https://github.com/Blandmarrow/Crucible/compare/<last tag>...vX.Y.Z
```

Write for users, not maintainers: short sentences, UI names rather than code names, one line
per change. Check every page, button and setting name in Update notes against the user docs
in `docs/` or the code, since a wrong button name in an instruction is worse than none.

## 6. Stop for approval

Show the user the number, the one-line reason and the full notes. Wait for their OK or
edits. Do not continue without it.

## 7. Bump the version

Skip this step if a PR already merged since the last tag set `backend/version.py` to the
new number.

```bash
git checkout -b release/vX.Y.Z
# edit backend/version.py: __version__ = "X.Y.Z"
# add the release to docs/dev/roadmap.md § Landed
python -m pytest backend/tests/test_version.py -q
```

Commit (in PowerShell, pass the message through a file, `git commit -F <file>`), push, and
open a PR. Wait for its checks by polling, since `gh pr checks --watch` can exit before the
checks finish:

```bash
for i in $(seq 1 40); do out=$(gh pr checks <n> 2>&1)
  echo "$out" | awk -F'\t' '$2=="pending"{f=1} END{exit !f}' || break; sleep 30; done
gh pr checks <n>
```

Match the tab-separated status column with `awk`: Git Bash's `grep -P` fails here, and the
loop would then end at once.

Merge once every blocking check passes, then `git checkout main && git pull -q`.

## 8. Publish

```bash
sha=$(git rev-parse origin/main)
git show "$sha":backend/version.py | grep __version__   # must say X.Y.Z
gh release create vX.Y.Z --target "$sha" --title "Crucible X.Y.Z" --notes-file <scratchpad>/release-notes-vX.Y.Z.md
```

## 9. Verify and report

```bash
gh release view vX.Y.Z --json tagName,isLatest,url
git fetch -q --tags && git show vX.Y.Z:backend/version.py | grep __version__
```

Report the release URL, that it is marked Latest, and that the tag's `backend/version.py`
matches the tag. If anything differs, say so; do not delete or move a published tag
without asking the user.
