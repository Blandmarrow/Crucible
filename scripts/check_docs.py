#!/usr/bin/env python3
"""Documentation drift check (docs-architecture reference implementation, v3.1).

Adopted 2026-10-29, replacing this repo's own checker (decision 1 in
docs/dev/decisions.md). Only the CONFIG block is Crucible's, plus one line in
check_budgets() that names MAINTENANCE_RULES instead of a hardcoded path; keep the rest in
step with the reference copy rather than patching it here.

Dependency-free: Python 3.9+ standard library only, so CI needs no install step.
Shelling out to `git` (for ignore rules) goes through the standard library too.

    python scripts/check_docs.py              # check the repo this script lives in
    python scripts/check_docs.py --root PATH  # check another tree
    python scripts/check_docs.py --self-test  # prove the check fails on seeded errors

Edit only the CONFIG block below; everything after it is generic.

Governed docs: CLAUDE.md, README.md, docs/*.md (end-user), everything under docs/dev/
(recursive) and under each EXTRA_TREES root (recursive).

Severities: FAIL = exit 1. WARN = advisory, exit 0 unless a FAIL fires. NOTE =
informational (a file nearing its budget, so a split is planned rather than forced).

Core checks:
  1. Markdown links (FAIL): every relative link target must exist, resolved against
     the linking file's own directory (standard markdown, no lenient fallbacks). A
     missing target that git ignores (build output, local data) is WARN: it is
     legitimately absent from a clone.
  2. Anchors (FAIL): every `file.md#anchor` / `#anchor` must match a heading (ATX or
     setext), slugged the way GitHub slugs headings (with -1/-2 dedupe), or an explicit
     <a id="..."> / <a name="...">. Link titles, <angle> destinations, ?query strings,
     %-encoding and reference definitions (`[r]: path.md`) are understood.
  3. Inline-code paths (FAIL, or WARN if INLINE_PATHS_FAIL is False): an inline-code
     token containing `/` and ending in a FILE_EXTS extension must exist under one of
     SOURCE_ROOTS or the linking file's directory. Skipped: tokens with spaces or
     placeholder/code characters (<>*?{}()=|$ and quotes), absolute/home paths,
     tokens with `:`, SKIP_TOKENS, and paths from extra_known_paths(). A missing path
     git ignores is WARN.
  4. `@`-path footgun (FAIL): `@docs/` or `@CLAUDE` outside inline code. Docs that
     explain the rule quote it in backticks; those inert mentions are allowed.
  5. Word budgets (WARN over, NOTE near): see the constants. A WARN prints the file's
     per-section breakdown so the seam is visible; CLAUDE.md's breakdown is compared
     with CLAUDE_SECTION_TARGETS. GENERATED_DOCS and TRANSIENT_DIRS are exempt.
  6. Coverage (WARN): each list of files is enforced exactly once:
       docs/dev/*.md          <-> a row in the CLAUDE.md Documentation Map (a link or
                                  an inline-code path), minus NON_TOPIC_DEV_DOCS
       EXTRA_TREES root/*.md  <-> its index file; CLAUDE.md must link each index
       SPLIT_INDEXES dir/*.md <-> a link in that folder's index file
       docs/*.md              <-> a link in README.md (if USER_DOCS)
     A registered folder that doesn't exist yet is fine.

Optional checks (ENABLE_* flags, off until a project opts in):
  - MAP_WORDS: a Map row's trailing `~words` cell within MAP_WORDS_TOLERANCE /
    MAP_WORDS_FLOOR of the file's real count.
  - PARAGRAPH_CAP: no paragraph or single list item over MAX_BLOCK_WORDS words.
  - LINK_LABELS (FAIL): a link whose label starts with `x.md` must open x.md.
  - PM_STATUS_SYNC: each PM detail file's Status matches its index row (PM-000 skipped).
  - TREE_STATUS (Module A): each tree doc's `Status:` word matches its index row.
  - ROADMAP_DRIFT (Module A): the roadmap's readiness snapshot vs each doc's live status.
  - PENDING_SPLITS (Module B): over-budget files have a recorded seam; entries for
    files under SPLIT_TARGET_FRACTION of budget are stale.
  - CITED_VALUES (Module E, FAIL): a numeric link label pointing into
    VALUE_REGISTRY must equal that entry's default; fill in registry_defaults().

Blind spots: a passing run does NOT mean the docs are right.
  - It verifies that referenced things exist, not that what the docs say about them is
    true. A stale description of an existing file passes. Doc audits cover that
    (the doc-maintenance skill, .claude/skills/doc-maintenance/SKILL.md).
  - It cannot notice that a user-visible change has no end-user docs at all, or that a
    finding, decision or incident never got written down. Only the same-change rule
    and the maintenance rules cover that.
  - Inline-code tokens without a `/` or a known extension (bare names, directories)
    are not checked at all.
  - Code comments and READMEs outside the governed set are not scanned. A split must
    repoint links in them by hand.
  - A `§ Section` pointer in prose is not a link, so it is never checked.
  - Links are parsed line by line: a link whose text or destination wraps onto the next
    line is not seen, and neither are HTML <a href> links.
  - When git can't answer whether a path is ignored, a minimal .gitignore read decides
    (negations and nested .gitignore files are not understood).

Why words, not lines: the budgets bound how much context a file costs, and that cost
is tokens. A line budget is a bad proxy. It can be met by writing longer lines (one
project's CLAUDE.md sat at exactly 200 lines across eleven commits while its word
count grew 21%), and tables and one-paragraph-per-line prose hide their size from it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

# ============================== CONFIG =======================================
# Paths are repo-relative, with forward slashes.

ARCH_VERSION = "3.1"  # docs-architecture version this repo follows (see decision 1)

CLAUDE_MD = "CLAUDE.md"
README_MD = "README.md"
DEV_DIR = "docs/dev"
USER_DOCS_DIR = "docs"   # flat docs/*.md, the end-user docs
USER_DOCS = True         # False when the project has no user-facing surface (no Phase 6)
# Where the maintenance rules live, named in the budget WARN. Crucible keeps them in a
# project skill (the reference default is docs/dev/doc-maintenance.md).
MAINTENANCE_RULES = ".claude/skills/doc-maintenance/SKILL.md"

# Word budgets. Raising one requires updating its comment with the date and reason,
# and considering a split first. The remedy for an over-budget file is a SPLIT, never
# compression: squeezing accurate prose into denser prose is how facts end up stacked
# four clauses deep, which is what MAX_BLOCK_WORDS exists to catch. Record the seam in
# PENDING_SPLITS; the next session that appends to the file executes it.
#
# These are Crucible's own budgets, kept when the reference checker was adopted
# (2026-10-29) rather than lowered to the reference defaults (2500 / 3000 / 2500 / 3000):
# with 17 splits already queued, lower budgets would only lengthen the queue.
# Revisit once the queue is cleared.
#
# CLAUDE.md is loaded into every conversation, so it stays the tightest budget per word
# of value; the Documentation Map grows by one row per topic file by design.
MAX_CLAUDE_WORDS = 5400
NOTE_CLAUDE_WORDS = 5400   # = MAX: no NOTE tier (2026-10-29); the queue does that job
# Topic files load on demand: one subsystem per file.
MAX_TOPIC_WORDS = 3500
NOTE_TOPIC_WORDS = 3500    # = MAX: no NOTE tier (2026-10-29)
# End-user pages. docs/*.md was once exempt, and docs/features.md grew to 4,700 words
# of duplicated prose unflagged; it is now an index, and this budget keeps it one.
MAX_USER_WORDS = 2500
# The landing page. Kept at the user-page budget (the reference allows 3000): README's
# overflow is a second feature index (see PENDING_SPLITS), not landing-page content.
MAX_README_WORDS = 2500

# Per-section targets for CLAUDE.md (heading prefix -> words; "(intro)" is the text
# before the first ## heading). Reported as NOTEs only. Empty since 2026-10-29: the
# reference targets assume a 2,500-word CLAUDE.md, so today they would only restate the
# queued CLAUDE.md split. Set them once that split has landed.
CLAUDE_SECTION_TARGETS: dict[str, int] = {}

# Extra roots that inline-code paths may be abbreviated from (e.g. "src", "backend").
# The repo root and the linking file's folder are always tried; add to this list.
# The docs abbreviate backend paths (`routers/captioning.py`) and frontend paths
# (`constants/storage.ts`).
SOURCE_ROOTS = ["backend", "frontend", "frontend/src"]

# Additional knowledge trees (Module A): tree root -> its index file.
EXTRA_TREES: dict[str, str] = {}

# Index + detail splits: detail folder -> index file that must link every file in it.
SPLIT_INDEXES = {
    "docs/dev/postmortems": "docs/dev/postmortems.md",
    "docs/dev/decisions": "docs/dev/decisions.md",
    "docs/dev/roadmap-archive": "docs/dev/roadmap.md",
}

# docs/dev/*.md files that are not topic files and need no Documentation Map row
# (e.g. a work queue such as pending-splits.md, Module B).
NON_TOPIC_DEV_DOCS: set[str] = {"pending-splits.md"}

# Generated docs (Module E), exempt from budgets.
GENERATED_DOCS: set[str] = set()

# Transient handoff folders (Module C), exempt from budgets. Links are still checked.
# Each one's README states its lifecycle: durable rationale moves into a topic file,
# then the folder is deleted.
TRANSIENT_DIRS: set[str] = {"docs/dev/gpt-review", "docs/dev/rating-plan"}

# Inline-code tokens that look like paths but are illustrative, not files.
# Upstream source paths a postmortem cites (sam3 in PM-004, CPython's PC/ in PM-021):
# they name files in someone else's tree, not this one.
SKIP_TOKENS: set[str] = {
    "sam3/model/edt.py", "sam3/perflib/nms.py", "perflib/connected_components.py",
    "PC/errmap.h",
}

# Extensions that mark an inline-code token as a concrete file reference.
FILE_EXTS = (
    ".md", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".yml", ".yaml", ".toml",
    ".txt", ".sh", ".ps1", ".bat", ".cfg", ".ini", ".html", ".css", ".c", ".h", ".cpp",
    ".hpp", ".rs", ".go", ".java", ".kt", ".cs", ".gd", ".rb", ".sql",
)
# No .webp: the repo tracks no .webp file, so every `.webp` token in the docs names a
# dataset's runtime thumbnail or poster, which a clone never has.

# Missing inline-code paths: FAIL (True) or WARN (False, if illustrative paths are common).
INLINE_PATHS_FAIL = True

# Optional checks.
ENABLE_MAP_WORDS = True
MAP_WORDS_TOLERANCE = 0.05   # proportional, so a 3k-word file isn't flagged for a small edit
MAP_WORDS_FLOOR = 50         # floored, so the smallest file isn't flagged for one sentence

ENABLE_PARAGRAPH_CAP = True
MAX_BLOCK_WORDS = 250        # a paragraph or bullet past this should be a list

ENABLE_LINK_LABELS = True     # catches labels a split leaves pointing at the old file
ENABLE_PM_STATUS_SYNC = True
ENABLE_TREE_STATUS = False   # Module A
TREE_STATUS_WORDS = ["Decided", "Leaning", "Exploring", "Deferred"]
ENABLE_ROADMAP_DRIFT = False  # Module A: roadmap readiness snapshot vs tree doc status
ROADMAP_SNAPSHOT_HEADING = "readiness snapshot"

ENABLE_PENDING_SPLITS = True  # Module B
PENDING_SPLITS = "docs/dev/pending-splits.md"
SPLIT_TARGET_FRACTION = 0.6  # a split leaves both halves under this share of the budget

ENABLE_CITED_VALUES = False  # Module E
VALUE_REGISTRY = "docs/dev/values.md"


def registry_defaults(text: str) -> dict[str, float]:
    """Module E hook: parse VALUE_REGISTRY's text into {anchor id: default value}.

    The default below reads rows shaped `| <a id="ID"></a>... | DEFAULT | ...`.
    Adjust it to the generated table's real shape.
    """
    out: dict[str, float] = {}
    for line in text.splitlines():
        m = re.match(r'^\|\s*<a id="([^"]+)"></a>[^|]*\|\s*([-+]?\d+(?:\.\d+)?)\s*\|', line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def extra_known_paths(root: Path) -> set[str]:
    """Hook: repo-relative paths that count as existing although they aren't on disk.

    Module D example: files a patch series creates in a pinned dependency, found by
    scanning the patches for `new file mode`. The default returns nothing.
    """
    return set()

# ============================ END CONFIG =====================================

ROOT = Path(__file__).resolve().parent.parent

INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
LINK_OPEN_RE = re.compile(r"\[((?:[^\]\[]|\[[^\]]*\])*)\]\(")
REF_DEF_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
HTML_ANCHOR_RE = re.compile(r"<a\b[^>]*?\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.I)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(r"^([-*+]|\d+\.)\s")
PM_STATUS_RE = re.compile(r"\b(LIVE|MITIGATED|STRUCTURAL)\b")
LABEL_FILE_RE = re.compile(r"^`?([\w./-]+\.md)`?(?=$|[\s#§:])")
CITED_NUMBER_RE = re.compile(r"^[×x]?\s*([+\-−]?\d+(?:\.\d+)?)\s*(%?)$")
FOOTGUN_RE = re.compile(r"@docs/|@CLAUDE")
PATH_JUNK = set(" *?<>{}()=|$\"'")

failures: list[str] = []
warnings: list[str] = []
notes: list[str] = []
_text_cache: dict[Path, str] = {}


def fail(msg: str) -> None:
    failures.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


def p(relpath: str) -> Path:
    return (ROOT / relpath).resolve()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def read(path: Path) -> str:
    path = path.resolve()
    if path not in _text_cache:
        _text_cache[path] = path.read_text(encoding="utf-8", errors="replace")
    return _text_cache[path]


def word_count(path: Path) -> int:
    return len(read(path).split())


# --- gitignore ---------------------------------------------------------------

_ignore_cache: dict[Path, bool] = {}


def _fallback_ignored(path: Path) -> bool:
    """Minimal .gitignore match, used only when git can't answer."""
    gi = ROOT / ".gitignore"
    if not gi.exists():
        return False
    relp, name = rel(path), path.name
    for raw in read(gi).splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        pat = line.strip("/")
        if pat in (relp, name) or relp.startswith(pat + "/"):
            return True
        if pat.startswith("*.") and name.endswith(pat[1:]):
            return True
    return False


def is_git_ignored(path: Path) -> bool:
    """True if ignore rules exclude `path`. If git can't answer, fall back to a minimal
    .gitignore read; on doubt, report (treat as not ignored)."""
    path = path.resolve()
    if path in _ignore_cache:
        return _ignore_cache[path]
    try:
        r = subprocess.run(["git", "check-ignore", "-q", "--", rel(path)],
                           cwd=str(ROOT), capture_output=True)
        ignored = r.returncode == 0 if r.returncode in (0, 1) else _fallback_ignored(path)
    except (OSError, subprocess.SubprocessError):
        ignored = _fallback_ignored(path)
    _ignore_cache[path] = ignored
    return ignored


# --- Markdown helpers --------------------------------------------------------

def opening_fence(line: str) -> str | None:
    """The fence marker if `line` opens a fenced block (CommonMark: a backtick fence's
    info string may not contain a backtick, so a one-line ```x``` is inline code)."""
    m = FENCE_RE.match(line)
    if not m:
        return None
    marker, info = m.group(1), m.group(2)
    if marker[0] == "`" and "`" in info:
        return None
    return marker


def _mask_code(line: str) -> str:
    """The line with inline-code spans blanked to spaces (same length, same offsets)."""
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


def content_lines(path: Path) -> list[tuple[int, str]]:
    """(line number, line) outside fenced code blocks and HTML comments. Comment
    markers inside inline code (`<!--`) don't count."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    in_comment = False
    for i, line in enumerate(read(path).splitlines(), start=1):
        if fence is not None:
            m = FENCE_RE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not m.group(2).strip():
                fence = None
            continue
        if not in_comment:
            marker = opening_fence(line)
            if marker:
                fence = marker
                continue
        masked = _mask_code(line)
        kept, pos = [], 0
        while pos < len(line):
            if in_comment:
                end = masked.find("-->", pos)
                if end < 0:
                    pos = len(line)
                else:
                    in_comment = False
                    pos = end + 3
            else:
                start = masked.find("<!--", pos)
                if start < 0:
                    kept.append(line[pos:])
                    pos = len(line)
                else:
                    kept.append(line[pos:start])
                    in_comment = True
                    pos = start + 4
        text = "".join(kept)
        if text.strip():
            out.append((i, text))
    return out


def strip_inline_code(line: str) -> str:
    return INLINE_CODE_RE.sub("", line)


def inline_tokens(line: str) -> list[str]:
    return [m.group(2).strip() for m in INLINE_CODE_RE.finditer(line)]


def _destination(s: str, i: int) -> str | None:
    """Parse a link destination starting at s[i] (just after `(`). Handles <...> and
    balanced parentheses; stops at whitespace (a title may follow)."""
    while i < len(s) and s[i] in " \t":
        i += 1
    if i < len(s) and s[i] == "<":
        end = s.find(">", i + 1)
        return s[i + 1:end] if end >= 0 else None
    depth, start = 0, i
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            i += 2
            continue
        if c in " \t":
            break
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                break
            depth -= 1
        i += 1
    return s[start:i]


def links(line: str) -> list[tuple[str, str]]:
    """(label, target) for inline links and reference definitions on a line, ignoring
    anything inside inline code. Titles ("t", 't', (t)) are dropped."""
    bare = _mask_code(line)
    out = []
    m = REF_DEF_RE.match(bare)
    if m:
        out.append((line[m.start(1):m.end(1)], m.group(2).strip("<>")))
    for m in LINK_OPEN_RE.finditer(bare):
        target = _destination(bare, m.end())
        if target is None:
            continue
        out.append((line[m.start(1):m.end(1)], target))
    return out


def split_target(target: str) -> tuple[str, str]:
    """(path, anchor) with any ?query dropped and both percent-decoded."""
    path_part, _, anchor = target.partition("#")
    path_part = path_part.split("?", 1)[0]
    return unquote(path_part), unquote(anchor)


def is_external(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) or target.startswith("//")


def slugify(heading: str) -> str:
    """GitHub-style heading slug."""
    s = heading.strip().lower()
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # [text](url) -> text
    s = s.replace("`", "").replace("*", "")
    s = re.sub(r"<[^>]+>", "", s)                    # inline HTML
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return re.sub(r"\s", "-", s.strip())


_slug_cache: dict[Path, set[str]] = {}


def anchors(path: Path) -> set[str]:
    path = path.resolve()
    if path in _slug_cache:
        return _slug_cache[path]
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    lines = content_lines(path)
    for k, (i, line) in enumerate(lines):
        m = HEADING_RE.match(line)
        text = m.group(2) if m else None
        if text is None and k + 1 < len(lines) and lines[k + 1][0] == i + 1 \
                and SETEXT_RE.match(lines[k + 1][1]) and line.strip() \
                and not re.match(r"^\s*([-*+>|]|\d+\.)", line):
            text = line.strip()  # setext heading: text underlined by === or ---
        if text:
            base = slugify(text)
            if base:
                n = counts.get(base, 0)
                slugs.add(base if n == 0 else f"{base}-{n}")
                counts[base] = n + 1
        for a in HTML_ANCHOR_RE.findall(line):
            slugs.add(a.lower())
    _slug_cache[path] = slugs
    return slugs


def sections(path: Path) -> list[tuple[str, int]]:
    """(heading, words) per section, split at the shallowest heading level below the
    title that the file uses. Words before it count as "(intro)". Fenced code counts:
    it costs context like anything else."""
    lines = read(path).splitlines()
    levels = [len(m.group(1)) for _, l in content_lines(path)
              if (m := HEADING_RE.match(l)) and len(m.group(1)) >= 2]
    if not levels:
        return []
    split_at = min(levels)
    heading_lines = {i for i, l in content_lines(path)
                     if (m := HEADING_RE.match(l)) and len(m.group(1)) == split_at}
    out: list[tuple[str, int]] = []
    current, words = "(intro)", 0
    for i, line in enumerate(lines, start=1):
        if i in heading_lines:
            out.append((current, words))
            current = INLINE_CODE_RE.sub(r"\2", HEADING_RE.match(line).group(2)).strip()
            words = 0
            continue
        words += len(line.split())
    out.append((current, words))
    return [(h, w) for h, w in out if w or h != "(intro)"]


# --- File sets ---------------------------------------------------------------

def under(path: Path, relroot: str) -> bool:
    root = p(relroot)
    return path.resolve() == root or root in path.resolve().parents


def governed_docs() -> list[Path]:
    docs: list[Path] = []
    for f in (p(CLAUDE_MD), p(README_MD)):
        if f.exists():
            docs.append(f)
    if USER_DOCS and p(USER_DOCS_DIR).is_dir():
        docs += sorted(p(USER_DOCS_DIR).glob("*.md"))
    for tree in [DEV_DIR, *EXTRA_TREES]:
        if p(tree).is_dir():
            docs += sorted(p(tree).rglob("*.md"))
    seen, out = set(), []
    for d in docs:
        if d.resolve() not in seen:
            seen.add(d.resolve())
            out.append(d)
    return out


def budget(path: Path) -> tuple[int, int] | None:
    """(cap, note threshold) for a file, or None if exempt."""
    r = rel(path)
    if r in GENERATED_DOCS or any(under(path, t) for t in TRANSIENT_DIRS):
        return None
    if r == CLAUDE_MD:
        return MAX_CLAUDE_WORDS, NOTE_CLAUDE_WORDS
    if r == README_MD:
        return MAX_README_WORDS, MAX_README_WORDS
    if under(path, DEV_DIR) or any(under(path, t) for t in EXTRA_TREES):
        return MAX_TOPIC_WORDS, NOTE_TOPIC_WORDS
    return MAX_USER_WORDS, MAX_USER_WORDS


# --- Core checks -------------------------------------------------------------

def check_links_and_anchors(docs: list[Path]) -> None:
    for doc in docs:
        for i, line in content_lines(doc):
            for _, target in links(line):
                if not target or is_external(target):
                    continue
                path_part, anchor = split_target(target)
                if path_part:
                    dest = (doc.parent / path_part).resolve()
                    if not dest.exists():
                        if is_git_ignored(dest):
                            warn(f"{rel(doc)}:{i}: link target is gitignored (absent from a clone) -> {target}")
                        else:
                            fail(f"{rel(doc)}:{i}: link target does not exist -> {target}")
                        continue
                else:
                    dest = doc
                if anchor and dest.is_file() and dest.suffix.lower() == ".md":
                    if anchor.lower() not in anchors(dest):
                        fail(f"{rel(doc)}:{i}: broken anchor -> {target}")


def check_inline_paths(docs: list[Path]) -> None:
    known = extra_known_paths(ROOT)
    report = fail if INLINE_PATHS_FAIL else warn
    for doc in docs:
        for i, line in content_lines(doc):
            for tok in inline_tokens(line):
                tok = tok.split("#", 1)[0].lstrip("@")  # a quoted `@docs/x.md` names x.md
                if (not tok or tok in SKIP_TOKENS or tok in known or "/" not in tok
                        or any(c in PATH_JUNK for c in tok) or ":" in tok
                        or tok.startswith(("/", "~")) or not tok.lower().endswith(FILE_EXTS)):
                    continue
                # The repo root is always a candidate; SOURCE_ROOTS adds abbreviation roots.
                candidates = [ROOT / tok] + [p(r) / tok for r in SOURCE_ROOTS] + [doc.parent / tok]
                if any(c.exists() for c in candidates):
                    continue
                if any(is_git_ignored(c) for c in candidates):
                    warn(f"{rel(doc)}:{i}: inline path is gitignored (absent from a clone) -> `{tok}`")
                    continue
                report(f"{rel(doc)}:{i}: inline-code path does not exist -> `{tok}`")


def check_footgun(docs: list[Path]) -> None:
    for doc in docs:
        for i, line in content_lines(doc):
            if FOOTGUN_RE.search(strip_inline_code(line)):
                fail(f"{rel(doc)}:{i}: @-path auto-load syntax outside inline code -> {line.strip()}")


def check_budgets(docs: list[Path]) -> None:
    for doc in docs:
        b = budget(doc)
        if b is None:
            continue
        cap, near = b
        n = word_count(doc)
        is_claude = rel(doc) == CLAUDE_MD
        if n > cap:
            secs = sections(doc)
            lines = [f"{rel(doc)}: {n} words, over the {cap}-word budget. Split it; never "
                     f"compress accurate prose ({MAINTENANCE_RULES})."]
            if secs:
                lines.append("    sections, largest first:")
                for h, w in sorted(secs, key=lambda s: -s[1]):
                    lines.append(f"    {w:>6}  {h}{_target_hint(h, w) if is_claude else ''}")
            else:
                lines.append("    no sub-headings: give the file ## headings so a seam can be named")
            warn("\n".join(lines))
        elif n > near:
            note(f"{rel(doc)}: {n} words, nearing the {cap}-word budget")
        if is_claude:
            for h, w in sections(doc):
                t = _target(h)
                if t is not None and w > t * 1.25:
                    note(f"{CLAUDE_MD}: section '{h}' is {w} words (target ~{t})")


def _target(heading: str) -> int | None:
    for prefix, t in CLAUDE_SECTION_TARGETS.items():
        if heading.lower().startswith(prefix.lower()):
            return t
    return None


def _target_hint(heading: str, words: int) -> str:
    t = _target(heading)
    return f"   (target ~{t})" if t is not None else ""


def _linked(index: Path) -> set[Path]:
    """Files an index links to, plus repo paths it names in inline code."""
    out: set[Path] = set()
    for _, line in content_lines(index):
        for _, target in links(line):
            part, _ = split_target(target)
            if part and not is_external(target):
                out.add((index.parent / part).resolve())
        for tok in inline_tokens(line):
            tok = tok.split("#", 1)[0]
            if tok.endswith(".md") and "/" in tok:
                out.add(p(tok))
    return out


def check_coverage() -> None:
    def require(files: list[Path], index: Path, what: str) -> None:
        if not files:
            return
        if not index.exists():
            warn(f"{rel(index)} is missing, so {what} coverage is not checked")
            return
        linked = _linked(index)
        for f in files:
            if f.resolve() != index.resolve() and f.resolve() not in linked:
                warn(f"{rel(f)} has no entry in {rel(index)} ({what})")

    claude = p(CLAUDE_MD)
    if p(DEV_DIR).is_dir():
        topics = [f for f in sorted(p(DEV_DIR).glob("*.md")) if f.name not in NON_TOPIC_DEV_DOCS]
        require(topics, claude, "Documentation Map")
    for tree, index in EXTRA_TREES.items():
        if p(tree).is_dir():
            require(sorted(p(tree).glob("*.md")), p(index), "tree index")
            require([p(index)], claude, "Documentation Map row for the tree index")
    for folder, index in SPLIT_INDEXES.items():
        if p(folder).is_dir():
            require(sorted(p(folder).glob("*.md")), p(index), "split index")
    if USER_DOCS and p(USER_DOCS_DIR).is_dir():
        require(sorted(p(USER_DOCS_DIR).glob("*.md")), p(README_MD), "README Docs table")


# --- Optional checks ---------------------------------------------------------

def check_map_words() -> None:
    claude = p(CLAUDE_MD)
    if not claude.exists():
        return
    for _, line in content_lines(claude):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        target = None
        for _, t in links(cells[0]):
            target = (claude.parent / split_target(t)[0]).resolve()
        for tok in inline_tokens(cells[0]):
            if tok.endswith(".md"):
                target = p(tok)
        if target is None or not target.is_file() or not under(target, DEV_DIR):
            continue
        m = re.match(r"^~?\s*([\d,]+)", cells[-1])
        if not m:
            warn(f"{CLAUDE_MD}: Map row for {rel(target)} has no trailing ~words cell")
            continue
        claimed = int(m.group(1).replace(",", ""))
        actual = word_count(target)
        if abs(actual - claimed) > max(MAP_WORDS_FLOOR, claimed * MAP_WORDS_TOLERANCE):
            warn(f"{CLAUDE_MD}: Map says ~{claimed} words for {rel(target)}, file has {actual} "
                 f"(use ~{round(actual / 50) * 50})")


def check_paragraphs(docs: list[Path]) -> None:
    for doc in docs:
        if budget(doc) is None:
            continue
        blocks: list[tuple[int, int]] = []
        cur: list[str] = []
        start = 0
        last = 0
        for i, line in content_lines(doc):
            s = line.strip()
            gap = i != last + 1
            last = i
            if gap or s.startswith(("|", "#")) or LIST_ITEM_RE.match(s):
                if cur:
                    blocks.append((start, len(" ".join(cur).split())))
                    cur = []
                if s.startswith(("|", "#")):
                    continue
            if not cur:
                start = i
            cur.append(s)
        if cur:
            blocks.append((start, len(" ".join(cur).split())))
        for i, n in blocks:
            if n > MAX_BLOCK_WORDS:
                warn(f"{rel(doc)}:{i}: {n}-word paragraph (over {MAX_BLOCK_WORDS}); make it a list")


def check_link_labels(docs: list[Path]) -> None:
    for doc in docs:
        for i, line in content_lines(doc):
            for label, target in links(line):
                m = LABEL_FILE_RE.match(label.strip())
                if not m or is_external(target):
                    continue
                part = split_target(target)[0]
                opened = Path(part).name if part else doc.name
                if Path(m.group(1)).name != opened:
                    fail(f"{rel(doc)}:{i}: link labelled {m.group(1)} opens {opened} ({target})")


def check_pm_status() -> None:
    index = p(f"{DEV_DIR}/postmortems.md")
    folder = p(f"{DEV_DIR}/postmortems")
    if not index.exists() or not folder.is_dir():
        return
    rows = [l for l in read(index).splitlines() if l.lstrip().startswith("|")]
    for f in sorted(folder.glob("PM-*.md")):
        if f.name.startswith("PM-000"):
            continue
        parts = read(f).split("### Status & date", 1)
        m = PM_STATUS_RE.search(parts[1]) if len(parts) == 2 else None
        if not m:
            warn(f"{rel(f)}: no LIVE/MITIGATED/STRUCTURAL under '### Status & date'")
            continue
        row = next((r for r in rows if f.name in r or f.stem in r), None)
        if row is None:
            continue  # coverage already warns
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        got = PM_STATUS_RE.search(cells[3]) if len(cells) > 3 else None
        if not got or got.group(1) != m.group(1):
            warn(f"{rel(f)}: status {m.group(1)} does not match its row in {rel(index)}")


def _status_word(text: str) -> str | None:
    for w in TREE_STATUS_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", text):
            return w
    return None


def check_tree_status() -> None:
    for tree, index in EXTRA_TREES.items():
        idx = p(index)
        if not idx.exists() or not p(tree).is_dir():
            continue
        rows = [l for l in read(idx).splitlines() if l.lstrip().startswith("|")]
        for f in sorted(p(tree).glob("*.md")):
            if f.resolve() == idx:
                continue
            head = "\n".join(read(f).splitlines()[:8])
            m = re.search(r"Status:\s*([^\n]+)", head)
            if not m:
                warn(f"{rel(f)}: no 'Status:' line near the top")
                continue
            row = next((r for r in rows if f.name in r), None)
            if row is None:
                continue
            mine = _status_word(m.group(1))
            theirs = _status_word(row.strip().strip("|").split("|")[-1])
            if mine and theirs and mine != theirs:
                warn(f"{rel(f)}: status {mine} differs from its index row ({theirs}) in {index}")


def check_roadmap_drift() -> None:
    """Module A: the roadmap's readiness snapshot records each referenced tree doc's
    status; WARN when a doc's live `Status:` has moved off it (the cue to re-tier the
    roadmap and re-stamp the row). The snapshot is a change detector, not an authority."""
    roadmap = p(f"{DEV_DIR}/roadmap.md")
    if not roadmap.exists():
        return
    mark = slugify(ROADMAP_SNAPSHOT_HEADING)
    in_section, seen = False, False
    for i, line in content_lines(roadmap):
        m = HEADING_RE.match(line)
        if m:
            in_section = mark in slugify(m.group(2))
            continue
        if not in_section or not line.lstrip().startswith("|") or "---" in line:
            continue
        recorded = _status_word(line)
        doc = next((split_target(t)[0] for _, t in links(line)
                    if split_target(t)[0].endswith(".md")), None)
        if not recorded or not doc:
            continue
        seen = True
        f = (roadmap.parent / doc).resolve()
        if not f.exists():
            continue  # the link check reports it
        head = "\n".join(read(f).splitlines()[:8])
        sm = re.search(r"Status:\s*([^\n]+)", head)
        actual = _status_word(sm.group(1)) if sm else None
        if actual and actual != recorded:
            warn(f"{rel(roadmap)}:{i}: {rel(f)} is now {actual} but the readiness snapshot "
                 f"says {recorded}; re-tier the roadmap and re-stamp the row")
    if not seen:
        warn(f"{rel(roadmap)}: no readiness-snapshot rows found under a heading containing "
             f"'{ROADMAP_SNAPSHOT_HEADING}' (the drift guard is inactive)")


def check_pending_splits(docs: list[Path]) -> None:
    queue = p(PENDING_SPLITS)
    text = read(queue) if queue.exists() else ""
    for doc in docs:
        b = budget(doc)
        if b and word_count(doc) > b[0] and rel(doc) not in text:
            warn(f"{rel(doc)}: over budget with no seam recorded in {PENDING_SPLITS} "
                 f"(record one; do not trim the prose instead)")
    if not text:
        return
    for i, line in content_lines(queue):
        m = HEADING_RE.match(line)
        if not m or len(m.group(1)) != 2:
            continue
        named = INLINE_CODE_RE.sub(r"\2", m.group(2)).strip()
        if not (named.endswith(".md") and "/" in named):
            continue  # prose sections and "(structural)" entries are not swept
        f = p(named)
        b = budget(f) if f.exists() else None
        if not f.exists():
            warn(f"{PENDING_SPLITS}:{i}: entry for {named}, which does not exist; delete it if the split is done")
        elif b and word_count(f) < b[0] * SPLIT_TARGET_FRACTION:
            warn(f"{PENDING_SPLITS}:{i}: entry for {named}, now under "
                 f"{SPLIT_TARGET_FRACTION:.0%} of budget; delete it if the split is done")


def check_cited_values(docs: list[Path]) -> None:
    reg = p(VALUE_REGISTRY)
    if not reg.exists():
        return
    defaults = registry_defaults(read(reg))
    for doc in docs:
        if doc.resolve() == reg:
            continue
        for i, line in content_lines(doc):
            for label, target in links(line):
                part, anchor = split_target(target)
                if not part or not anchor or (doc.parent / part).resolve() != reg:
                    continue
                m = CITED_NUMBER_RE.match(label.strip())
                if not m or anchor not in defaults:
                    continue
                cited = float(m.group(1).replace("−", "-"))
                expected = defaults[anchor] * (100.0 if m.group(2) == "%" else 1.0)
                if abs(cited - expected) > 1e-9 * max(1.0, abs(expected)):
                    fail(f"{rel(doc)}:{i}: cites {label.strip()} for {anchor}, "
                         f"but the registry default is {defaults[anchor]:g}")


# --- Main --------------------------------------------------------------------

def run() -> int:
    docs = governed_docs()
    if not docs:
        print("No documentation files found; nothing to check.")
        print("RESULT: ok (0 error(s), 0 warning(s), 0 note(s))")
        return 0
    check_links_and_anchors(docs)
    check_inline_paths(docs)
    check_footgun(docs)
    check_budgets(docs)
    check_coverage()
    if ENABLE_MAP_WORDS:
        check_map_words()
    if ENABLE_PARAGRAPH_CAP:
        check_paragraphs(docs)
    if ENABLE_LINK_LABELS:
        check_link_labels(docs)
    if ENABLE_PM_STATUS_SYNC:
        check_pm_status()
    if ENABLE_TREE_STATUS:
        check_tree_status()
    if ENABLE_ROADMAP_DRIFT:
        check_roadmap_drift()
    if ENABLE_PENDING_SPLITS:
        check_pending_splits(docs)
    if ENABLE_CITED_VALUES:
        check_cited_values(docs)

    for n in notes:
        print(f"NOTE  {n}")
    for w in warnings:
        print(f"WARN  {w}")
    for f in failures:
        print(f"FAIL  {f}")
    print(f"\nChecked {len(docs)} docs (docs-architecture v{ARCH_VERSION}).")
    print(f"RESULT: {'fail' if failures else 'ok'} ({len(failures)} error(s), "
          f"{len(warnings)} warning(s), {len(notes)} note(s))")
    return 1 if failures else 0


SELF_TEST_GOOD = {
    "CLAUDE.md": "# CLAUDE.md\n\nNever write `@docs/...` paths, e.g. `@docs/dev/topic.md`.\n\n"
                 "## Documentation Map\n\n"
                 "| File | Read this when... | ~words |\n|---|---|---|\n"
                 "| [docs/dev/topic.md](docs/dev/topic.md) | Testing - a topic | ~20 |\n",
    "docs/dev/topic.md": "# Topic\n\n**Status: living.** Scope.\n\n## Part one\n\n"
                         "See [the map](../../CLAUDE.md#documentation-map 'title') and `docs/dev/topic.md`.\n\n"
                         "Setext title\n------------\n\n"
                         "Links: [setext](#setext-title), [query](topic.md?plain=1#part-one), "
                         "[angle](<topic.md>), [ref][r].\n\n[r]: topic.md#part-one\n\n"
                         "Inline ```x``` stays inline, and `<!--` in code opens no comment.\n\n"
                         "<!-- a placeholder link [x](missing.md) inside a comment is ignored\n"
                         "     even across lines [y](missing2.md) -->\n\n"
                         "````\n```\n[fenced](also-missing.md) `docs/nope.md`\n```\n````\n",
}
SELF_TEST_BAD = {
    "docs/dev/topic.md": SELF_TEST_GOOD["docs/dev/topic.md"]
    + "\nBroken: [gone](gone.md), [anchor](topic.md#no-such-heading), `docs/dev/missing.md`, "
      "and a live @docs/topic.md reference.\n\n`<!--` then [gone2](gone2.md).\n\n[r2]: gone3.md\n",
}


def _self_test_expect() -> list[tuple[str, str]]:
    inline = "FAIL" if INLINE_PATHS_FAIL else "WARN"
    return [("FAIL", "-> gone.md"), ("FAIL", "broken anchor"),
            (inline, "inline-code path does not exist"), ("FAIL", "@-path auto-load"),
            ("FAIL", "-> gone2.md"), ("FAIL", "-> gone3.md")]


def self_test() -> int:
    """Run this script on a seeded tree: it must pass clean, then fail on each seeded error."""
    script = Path(__file__).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="check_docs_selftest_"))
    try:
        def write(files: dict[str, str]) -> None:
            for name, body in files.items():
                f = tmp / name
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(body, encoding="utf-8")

        def run_on() -> tuple[int, str]:
            r = subprocess.run([sys.executable, str(script), "--root", str(tmp)],
                               capture_output=True, text=True, encoding="utf-8")
            return r.returncode, r.stdout + r.stderr

        write(SELF_TEST_GOOD)
        code, out = run_on()
        if code != 0:
            print("SELF-TEST FAILED: the clean tree did not pass:\n" + out)
            return 1
        write(SELF_TEST_BAD)
        code, out = run_on()
        lines = out.splitlines()
        missing = [f"{sev} ...{text}" for sev, text in _self_test_expect()
                   if not any(l.startswith(sev + " ") and text in l for l in lines)]
        if code != 1 or missing:
            print(f"SELF-TEST FAILED: exit {code}, missing findings {missing}:\n" + out)
            return 1
        write(SELF_TEST_GOOD)
        code, out = run_on()
        if code != 0:
            print("SELF-TEST FAILED: the reverted tree did not pass:\n" + out)
            return 1
        print("SELF-TEST ok: the clean tree (titles, query strings, <angle> and reference "
              "links, setext headings, nested fences, multi-line comments) passes; broken "
              "links (inline, after a code `<!--`, reference definition), a broken anchor, a "
              "missing inline path and a live @-path are each reported at their severity "
              "(exit 1); the reverted tree passes.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    global ROOT
    ap = argparse.ArgumentParser(description="Documentation drift check.")
    ap.add_argument("--root", help="repository root (default: this script's parent's parent)")
    ap.add_argument("--self-test", action="store_true",
                    help="prove the check passes a clean tree and fails seeded errors")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.root:
        ROOT = Path(args.root).resolve()
    return run()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
