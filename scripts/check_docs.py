#!/usr/bin/env python3
"""Drift check for the documentation: dev docs (CLAUDE.md + docs/dev/*.md) and
user docs (README.md + docs/*.md).

Dependency-free (stdlib only). Run from anywhere:

    python scripts/check_docs.py

Checks (FAIL sets a non-zero exit code; WARN does not):
  1. FAIL  broken repo-path references in the docs (dev + user)
  2. FAIL  a markdown link to a .md file is missing, or its #anchor matches no heading
  3. FAIL  the `@docs/` / `@CLAUDE` auto-load footgun appears outside inline code
  4. WARN  any doc over its word budget (see word_limit())
  5. WARN  an over-budget doc has no seam recorded in docs/dev/pending-splits.md
  6. WARN  any single paragraph/bullet over MAX_BLOCK_WORDS
  7. WARN  a docs/dev/*.md file has no Documentation Map row, or vice versa
  8. WARN  a Documentation Map row's hand-written word count has drifted from the file

The word-budget warning prints a remedy and a per-section breakdown rather than a bare
number, because a bare number reads as a target to shrink and the cheapest way to shrink
it is to compress accurate prose — which is the very failure MAX_BLOCK_WORDS exists to
catch (see below). The remedy for an over-budget file is a SPLIT. Check 5 is what makes
that enforceable: the seam has to be written down where the next session can find it,
since the expensive half of a split is choosing the seam and that judgement is at its
best in the session that just worked the file, while *executing* the split is at its
worst there. So the two halves are deliberately separated in time — record the seam at
the end of the session that trips the budget, execute it at the start of the next
session that would append to the file. `docs/dev/pending-splits.md` is the handoff.

Why the heuristics are conservative: the docs reference many paths relative to
`backend/` (e.g. `routers/captioning.py`) and plenty of non-paths that superficially
look pathish (API routes like `stream/all/events`, globs, code fragments). To avoid
false positives we (a) ignore fenced ``` code blocks entirely, (b) only treat a token
as a checkable path when it contains `/` and ends in a known file extension or `/`,
and (c) resolve it against the repo root *and* `backend/`/`frontend/` before flagging.

Two file sets, deliberately different: the Documentation Map check is dev-only
(`docs/*.md` has no Map), while path, anchor and size checks cover both. Size
budgets are per-file — see `word_limit()`. `docs/*.md` was previously exempt, and
`docs/features.md` grew to 4,700 words unflagged as a result; it is now an index
that points at one doc per topic, and the budget is what keeps it that way.

Why the budgets count WORDS and not lines: these budgets exist to bound how much
context a file costs when it is read, and that cost is tokens. A line budget is
only a proxy for it, and a bad one — under the old 250-line cap `docs/dev/comfyui.md`
sat at 100% of budget holding 2,491 words while `docs/dev/backend-infrastructure.md`
read as one-third full holding 3,367, so the check pointed at the wrong files. Worse,
the cap was satisfiable by writing longer lines: CLAUDE.md sat at exactly 200 lines
across eleven consecutive commits while its word count grew 21%, which is how facts
ended up stacked four clauses deep in a single sentence where the next editor could
not see them.

MAX_BLOCK_WORDS is the guard against that stacking, and it deliberately measures
paragraphs rather than lines. Words-per-line would only measure hard-wrapping style:
the same paragraph scores ~12 wrapped at 80 columns and ~300 on one long line, with
identical rendered output and identical readability. A paragraph or bullet that runs
past MAX_BLOCK_WORDS should be a list, whatever its wrapping.

What this CANNOT do is notice that a shipped feature has no user docs at all —
the gap that motivated adding user docs here in the first place. Only the
CLAUDE.md maintenance rules prevent that.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- Configuration ---------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

# Word budgets. See the module docstring for why these are words and not lines.
# CLAUDE_MAX_WORDS was raised from 4000 to express a ceiling of roughly 250 lines,
# which is the form the limit was requested in. It is applied in words because that
# is the only unit this script can enforce meaningfully — a line budget is satisfied
# by writing longer lines. The conversion used CLAUDE.md's own density at the time,
# 186 lines to 3994 words, i.e. ~21.5 words per line.
CLAUDE_MAX_WORDS = 5400   # always loaded into every conversation — still the tightest budget
TOPIC_MAX_WORDS = 3500    # docs/dev/*.md — one subsystem per file
USER_MAX_WORDS = 2500     # docs/*.md — end-user docs
README_MAX_WORDS = 2500   # the landing page

# A single paragraph or bullet longer than this should be a list.
MAX_BLOCK_WORDS = 250

# The split handoff queue: one entry per over-budget file, naming the seam. See the
# module docstring for why the seam is recorded separately from the split being done.
PENDING_SPLITS = Path("docs/dev/pending-splits.md")

# docs/dev/*.md files that are not topic files and so are exempt from the Documentation
# Map. The Map indexes subsystems; a work queue is not one, and giving it a row would
# advertise it as something to read when a task touches a subsystem.
NON_TOPIC_DEV_DOCS = {PENDING_SPLITS.name}

# A split should leave both halves with real headroom. Two 3,400-word files bought
# nothing; if the natural seam cannot get under this, it is the wrong seam.
SPLIT_TARGET_FRACTION = 0.6

# How far the Documentation Map's hand-written `Words` column may drift from the
# file it describes. Proportional, so a 3k-word file is not flagged for a 60-word
# edit; floored, so the smallest file is not flagged for sentence-level churn.
# The tolerance is taken against the *claimed* value — that is the number under audit.
MAP_WORDS_TOLERANCE = 0.05
MAP_WORDS_FLOOR = 50

# Bases a path token may be relative to. The docs reference source files relative to
# `backend/` (e.g. `routers/captioning.py`) and `frontend/src/` (e.g. `api/foo.ts`).
RESOLVE_BASES = [
    REPO_ROOT,
    REPO_ROOT / "backend",
    REPO_ROOT / "frontend",
    REPO_ROOT / "frontend" / "src",
]

# Extensions that mark a token as a concrete file reference worth existence-checking.
PATH_EXTS = (
    ".py", ".md", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml",
    ".ps1", ".sh", ".bat", ".txt", ".webp", ".css", ".html", ".cfg", ".ini", ".toml",
)

# Tokens that clear every heuristic but are known non-paths / build artifacts.
# Populate if a validation run surfaces a genuine false positive.
SKIP_TOKENS: set[str] = set()

# Characters whose presence means "this is a code fragment / route, not a path".
_DISALLOWED = set("()=<>{}\"'|:@ ")

_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_MAP_ROW = re.compile(r"^\s*\|.*?docs/dev/([\w-]+\.md)")
# A sibling of _MAP_ROW, not an extra group on it: _MAP_ROW is deliberately lenient
# and check_map_sync depends on that. This one anchors the count to the row's LAST
# cell rather than a column index, so it survives the Map gaining or losing a column.
_MAP_WORDS_ROW = re.compile(r"^\s*\|\s*`docs/dev/([\w-]+\.md)`\s*\|.*\|\s*~?([\d,]+)\s*\|\s*$")
_FOOTGUN = re.compile(r"@docs/|@CLAUDE")
_LIST_ITEM = re.compile(r"^([-*+]|\d+\.)\s")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# GitHub's slugger: lowercase, drop everything but alphanumerics/spaces/hyphens,
# then spaces -> hyphens. "Datasets & Gallery" -> "datasets--gallery".
_SLUG_STRIP = re.compile(r"[^\w\- ]")


# --- Helpers ---------------------------------------------------------------

def dev_doc_files() -> list[Path]:
    """CLAUDE.md + docs/dev/*.md — the always-loaded file and its topic files."""
    files = [REPO_ROOT / "CLAUDE.md"]
    files += sorted((REPO_ROOT / "docs" / "dev").glob("*.md"))
    return [f for f in files if f.exists()]


def user_doc_files() -> list[Path]:
    """README.md + docs/*.md — end-user docs. Non-recursive, so docs/dev/ is not
    double-counted (it has its own set and its own checks)."""
    files = [REPO_ROOT / "README.md"]
    files += sorted((REPO_ROOT / "docs").glob("*.md"))
    return [f for f in files if f.exists()]


def split_fragment(tok: str) -> tuple[str, str]:
    """`docs/features.md#logs` -> ("docs/features.md", "logs"). Without this the
    extension test below rejects every anchor link, silently skipping it."""
    path, sep, frag = tok.partition("#")
    return path, frag if sep else ""


def heading_slugs(path: Path) -> set[str]:
    """GitHub-style anchor slugs for a markdown file's ATX headings. Skips fenced
    blocks so a `# comment` inside a code sample is not mistaken for a heading."""
    slugs: set[str] = set()
    for _, line, in_fence in iter_lines_outside_fences(path.read_text(encoding="utf-8")):
        if in_fence:
            continue
        m = _HEADING.match(line)
        if not m:
            continue
        text = _INLINE_CODE.sub(r"\1", m.group(2))          # `code` -> code
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](url) -> text
        text = re.sub(r"[*_]+", "", text)                     # **bold** -> bold
        slugs.add(_SLUG_STRIP.sub("", text.lower()).replace(" ", "-"))
    return slugs


def iter_lines_outside_fences(text: str):
    """Yield (lineno, line, in_fence) — in_fence True for lines inside ``` blocks."""
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            yield i, line, True  # the fence marker line itself is not content
            continue
        yield i, line, in_fence


def looks_like_path(tok: str) -> bool:
    if not tok or "/" not in tok:
        return False
    if tok.startswith(("http://", "https://", "mailto:")):
        return False
    if any(c in _DISALLOWED for c in tok):
        return False
    if tok in SKIP_TOKENS:
        return False
    # Only existence-check concrete file references. Bare directory tokens (`images/`,
    # `data/datasets/foo/`) are almost always illustrative prose, not repo paths, and
    # flagging them just trains readers to ignore the check.
    return tok.endswith(PATH_EXTS)


def path_exists(tok: str) -> bool:
    for base in RESOLVE_BASES:
        if "*" in tok:
            try:
                if next(base.glob(tok), None) is not None:
                    return True
            except (ValueError, OSError):
                continue
        elif (base / tok).exists():
            return True
    return False


# --- Checks ----------------------------------------------------------------

def check_broken_paths(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(REPO_ROOT)
        for lineno, line, in_fence in iter_lines_outside_fences(text):
            if in_fence:
                continue
            candidates = _INLINE_CODE.findall(line) + _MD_LINK.findall(line)
            for tok in candidates:
                tok, _ = split_fragment(tok.strip())
                if looks_like_path(tok) and not path_exists(tok):
                    errors.append(f"{rel}:{lineno}: references missing path `{tok}`")
    return errors


def check_md_links(files: list[Path]) -> list[str]:
    """Markdown links to other markdown files, and their #anchors.

    Targets resolve relative to the linking file's own directory (standard markdown),
    NOT via RESOLVE_BASES: that backend/-relative heuristic exists for inline-code
    path references, and applying it here would "resolve" links no reader can follow.

    This deliberately does not defer missing targets to check_broken_paths, which
    only considers tokens containing `/` — a conservative rule that is right for
    ambiguous inline code but would skip a plainly broken `[x](versioning.md)`.
    A markdown link target is unambiguous: it must resolve. Scoped to `.md` targets
    so prose that merely looks like a link (`](a|b|rc)`) stays out of it.
    """
    errors: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(REPO_ROOT)
        for lineno, line, in_fence in iter_lines_outside_fences(text):
            if in_fence:
                continue
            for raw in _MD_LINK.findall(line):
                tok = raw.strip()
                if tok.startswith(("http://", "https://", "mailto:")):
                    continue
                target, frag = split_fragment(tok)
                if target:
                    if not target.endswith(".md"):
                        continue  # check_broken_paths' job
                    dest = (f.parent / target).resolve()
                    if not dest.exists():
                        errors.append(f"{rel}:{lineno}: link target `{target}` not found")
                        continue
                elif frag:
                    dest = f  # same-file anchor
                else:
                    continue
                if frag and frag.lower() not in heading_slugs(dest):
                    where = "this file" if not target else target
                    errors.append(f"{rel}:{lineno}: anchor `#{frag}` not found in {where}")
    return errors


def word_limit(f: Path) -> int:
    """Per-file word budget. Both always-loaded landing files (CLAUDE.md, README.md)
    get their own; everything else is a topic file on the standard budget."""
    if f.name == "CLAUDE.md":
        return CLAUDE_MAX_WORDS
    if f.name == "README.md":
        return README_MAX_WORDS
    if f.parent.name == "dev":
        return TOPIC_MAX_WORDS
    return USER_MAX_WORDS


def section_words(path: Path) -> list[tuple[str, int]]:
    """(heading, word count) per top-level section — the raw material for choosing a seam.

    Splits on the *shallowest heading level the file actually uses below its `# Title`*,
    rather than assuming `##`: the repo is not consistent here (`docs/dev/versioning.md`
    is organised with `###`, `docs/dev/export.md` has no sub-headings at all), and
    hardcoding a level reports those files as one undifferentiated block, which is
    exactly the case where a seam is hardest to see and the breakdown is needed most.

    Words before the first heading are attributed to "(intro)". Fenced code counts —
    it costs context like anything else. Returns [] when the file has no sub-headings;
    the caller says so rather than printing a single meaningless row.
    """
    text = path.read_text(encoding="utf-8")
    levels = [
        len(m.group(1))
        for _, line, in_fence in iter_lines_outside_fences(text)
        if not in_fence and (m := _HEADING.match(line)) and len(m.group(1)) >= 2
    ]
    if not levels:
        return []
    split_at = min(levels)

    sections: list[tuple[str, int]] = []
    heading = "(intro)"
    words = 0
    for _, line, in_fence in iter_lines_outside_fences(text):
        m = None if in_fence else _HEADING.match(line)
        if m and len(m.group(1)) == split_at:
            if words:
                sections.append((heading, words))
            heading = _INLINE_CODE.sub(r"\1", m.group(2)).strip()
            words = 0
            continue
        words += len(line.split())
    if words:
        sections.append((heading, words))
    return sections


def oversized(files: list[Path]) -> list[tuple[Path, int, int]]:
    """(file, actual words, budget) for every file over its budget."""
    out = []
    for f in files:
        n = len(f.read_text(encoding="utf-8").split())
        limit = word_limit(f)
        if n > limit:
            out.append((f, n, limit))
    return out


def check_sizes(files: list[Path]) -> list[str]:
    """Report over-budget files with the remedy and a seam-picking breakdown.

    Deliberately verbose, and deliberately not a bare number: this fires rarely, and
    when it does it is the exact moment someone decides between splitting the file and
    compressing its prose. Naming the remedy here is cheaper than hoping the reader
    goes and looks it up.
    """
    warnings: list[str] = []
    for f, n, limit in oversized(files):
        target = int(limit * SPLIT_TARGET_FRACTION)
        lines = [
            f"{f.relative_to(REPO_ROOT)}: {n} words (> {limit})",
            "  SPLIT this file — do NOT compress the prose to fit. Compressing is how "
            "facts end up stacked several clauses deep; see the module docstring.",
            f"  Aim for each half under ~{target} words. Record the seam in "
            f"`{PENDING_SPLITS}`, then execute it at the START of the next session "
            "that appends here — never at the end of this one.",
        ]
        sections = section_words(f)
        if sections:
            lines.append("  Sections:")
            for heading, words in sorted(sections, key=lambda s: -s[1]):
                lines.append(f"    {words:>5}  {heading}")
        else:
            lines.append("  No sub-headings — read the file to find the seam, and give "
                         "each half a heading structure while you are in there.")
        warnings.append("\n      ".join(lines))
    return warnings


def check_pending_splits(files: list[Path]) -> list[str]:
    """Every over-budget file needs a recorded seam, and every recorded seam a file
    that still needs it.

    The second direction matters as much as the first: an entry left behind after a
    split (or after the content shrank for another reason) is a standing instruction to
    do work that is already done, which is how a queue stops being trusted.

    The two directions deliberately use different thresholds, leaving three bands. Over
    budget with no entry: record one. Under SPLIT_TARGET_FRACTION with an entry: the
    split evidently happened (that is the fraction a split targets), so the entry is
    stale. In between: entry recorded, split pending, silence. Keying staleness off the
    budget instead would call an entry for a file sitting *at* 100% stale and invite
    someone to delete it — the one entry most obviously about to be needed.
    """
    warnings: list[str] = []
    pending = REPO_ROOT / PENDING_SPLITS
    text = pending.read_text(encoding="utf-8") if pending.exists() else ""
    over = {str(f.relative_to(REPO_ROOT)) for f, _, _ in oversized(files)}
    settled = {
        str(f.relative_to(REPO_ROOT))
        for f in files
        if len(f.read_text(encoding="utf-8").split()) < word_limit(f) * SPLIT_TARGET_FRACTION
    }

    for rel in sorted(over):
        if rel not in text:
            warnings.append(
                f"{rel}: over budget with no seam recorded in {PENDING_SPLITS} "
                "(add one; do not trim the file instead)"
            )

    for lineno, line, in_fence in iter_lines_outside_fences(text):
        if in_fence:
            continue
        m = _HEADING.match(line)
        if not m or len(m.group(1)) != 2:
            continue
        named = _INLINE_CODE.sub(r"\1", m.group(2)).strip()
        # Only bare path headings are entries. Two things fall out of this, both wanted:
        # the file's own prose sections use `##` too and would otherwise read as a stale
        # queue, and a `## docs/dev/x.md (structural)` heading — an under-budget file
        # recorded as a dumping ground — is exempt from the staleness sweep while its
        # path still satisfies the has-an-entry check above, which matches on substring.
        if not (named.endswith(".md") and "/" in named):
            continue
        if named in settled:
            warnings.append(
                f"{PENDING_SPLITS}:{lineno}: entry for `{named}`, now under "
                f"{SPLIT_TARGET_FRACTION:.0%} of budget — delete it if the split is done"
            )
    return warnings


def text_blocks(path: Path) -> list[tuple[int, int]]:
    """(line number, word count) for each prose block — a paragraph or a single list
    item. Fenced code, table rows and headings are not prose and are skipped; a table
    row is one cell per column, not a sentence anyone reads straight through."""
    blocks: list[tuple[int, int]] = []
    cur: list[str] = []
    start = 0
    for lineno, line, in_fence in iter_lines_outside_fences(path.read_text(encoding="utf-8")):
        if in_fence:
            if cur:  # a code block ends the paragraph before it; don't join across it
                blocks.append((start, len(" ".join(cur).split())))
                cur = []
            continue
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("#"):
            if cur:
                blocks.append((start, len(" ".join(cur).split())))
                cur = []
            continue
        if _LIST_ITEM.match(s) and cur:      # a new list item starts a new block
            blocks.append((start, len(" ".join(cur).split())))
            cur = []
        if not cur:
            start = lineno
        cur.append(s)
    if cur:
        blocks.append((start, len(" ".join(cur).split())))
    return blocks


def check_blocks(files: list[Path]) -> list[str]:
    warnings: list[str] = []
    for f in files:
        rel = f.relative_to(REPO_ROOT)
        for lineno, words in text_blocks(f):
            if words > MAX_BLOCK_WORDS:
                warnings.append(f"{rel}:{lineno}: {words}-word paragraph (> {MAX_BLOCK_WORDS})")
    return warnings


def check_footgun(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(REPO_ROOT)
        for lineno, line, in_fence in iter_lines_outside_fences(text):
            if in_fence:
                continue
            stripped = _INLINE_CODE.sub("", line)  # drop inline-code spans
            if _FOOTGUN.search(stripped):
                errors.append(
                    f"{rel}:{lineno}: `@docs/`/`@CLAUDE` auto-load path outside inline code"
                )
    return errors


def check_map_sync() -> list[str]:
    claude = REPO_ROOT / "CLAUDE.md"
    dev_dir = REPO_ROOT / "docs" / "dev"
    warnings: list[str] = []
    if not claude.exists() or not dev_dir.exists():
        return warnings
    rows = set()
    for line in claude.read_text(encoding="utf-8").splitlines():
        m = _MAP_ROW.match(line)
        if m:
            rows.add(m.group(1))
    on_disk = {p.name for p in dev_dir.glob("*.md")} - NON_TOPIC_DEV_DOCS
    for name in sorted(on_disk - rows):
        warnings.append(f"docs/dev/{name}: file has no Documentation Map row")
    for name in sorted(rows - on_disk):
        warnings.append(f"docs/dev/{name}: Map row has no matching file")
    return warnings


def check_map_words() -> list[str]:
    """The Documentation Map's `Words` column is hand-written, so it rots silently —
    it had drifted by up to 455 words before this check existed. Counting is identical
    to check_sizes (whitespace split) so the two can never disagree.

    Rows whose file is missing are check_map_sync's problem, not this one. A row that
    matches the lenient _MAP_ROW but not _MAP_WORDS_ROW is reported too: dropping the
    cell would otherwise defeat the check, which is the exact drift being closed.
    """
    claude = REPO_ROOT / "CLAUDE.md"
    dev_dir = REPO_ROOT / "docs" / "dev"
    warnings: list[str] = []
    if not claude.exists() or not dev_dir.exists():
        return warnings
    for line in claude.read_text(encoding="utf-8").splitlines():
        loose = _MAP_ROW.match(line)
        if not loose:
            continue
        name = loose.group(1)
        if not (dev_dir / name).exists():
            continue  # check_map_sync reports this; don't double-report
        m = _MAP_WORDS_ROW.match(line)
        if not m:
            warnings.append(f"docs/dev/{name}: Map row has no trailing word-count cell")
            continue
        claimed = int(m.group(2).replace(",", ""))
        actual = len((dev_dir / name).read_text(encoding="utf-8").split())
        if abs(actual - claimed) > max(MAP_WORDS_FLOOR, claimed * MAP_WORDS_TOLERANCE):
            warnings.append(
                f"docs/dev/{name}: Map claims ~{claimed} words, file has {actual} "
                f"(use ~{round(actual / 5) * 5})"
            )
    return warnings


# --- Main ------------------------------------------------------------------

def _report(title: str, items: list[str], is_error: bool) -> None:
    tag = "FAIL" if is_error else "WARN"
    if items:
        print(f"[{tag}] {title}:")
        for it in items:
            print(f"    - {it}")
    else:
        print(f"[ ok ] {title}")


def main() -> int:
    dev = dev_doc_files()
    user = user_doc_files()
    every = dev + user

    broken = check_broken_paths(every)
    links = check_md_links(every)
    footgun = check_footgun(dev)      # user docs are not auto-loaded into context
    sizes = check_sizes(every)        # dev + user; see word_limit() for per-file budgets
    pending = check_pending_splits(every)
    long_blocks = check_blocks(every)
    map_sync = check_map_sync()       # docs/*.md has no Documentation Map
    map_words = check_map_words()     # the Map's hand-written word counts

    print("Documentation drift check\n" + "=" * 26)
    print(f"       {len(dev)} dev file(s), {len(user)} user file(s)\n")
    _report("broken path references", broken, is_error=True)
    _report("markdown links & anchors", links, is_error=True)
    _report("@-path auto-load footgun", footgun, is_error=True)
    _report("word budgets", sizes, is_error=False)
    _report("recorded split seams", pending, is_error=False)
    _report("paragraph length", long_blocks, is_error=False)
    _report("Documentation Map <-> files sync", map_sync, is_error=False)
    _report("Documentation Map word counts", map_words, is_error=False)

    errors = broken + links + footgun
    warns = (len(sizes) + len(pending) + len(long_blocks)
             + len(map_sync) + len(map_words))
    print()
    print("RESULT:", "FAIL" if errors else "ok",
          f"({len(errors)} error(s), {warns} warning(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
