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
  5. WARN  any single paragraph/bullet over MAX_BLOCK_WORDS
  6. WARN  a docs/dev/*.md file has no Documentation Map row, or vice versa

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


def check_sizes(files: list[Path]) -> list[str]:
    warnings: list[str] = []
    for f in files:
        n = len(f.read_text(encoding="utf-8").split())
        limit = word_limit(f)
        if n > limit:
            warnings.append(f"{f.relative_to(REPO_ROOT)}: {n} words (> {limit})")
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
    on_disk = {p.name for p in dev_dir.glob("*.md")}
    for name in sorted(on_disk - rows):
        warnings.append(f"docs/dev/{name}: file has no Documentation Map row")
    for name in sorted(rows - on_disk):
        warnings.append(f"docs/dev/{name}: Map row has no matching file")
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
    long_blocks = check_blocks(every)
    map_sync = check_map_sync()       # docs/*.md has no Documentation Map

    print("Documentation drift check\n" + "=" * 26)
    print(f"       {len(dev)} dev file(s), {len(user)} user file(s)\n")
    _report("broken path references", broken, is_error=True)
    _report("markdown links & anchors", links, is_error=True)
    _report("@-path auto-load footgun", footgun, is_error=True)
    _report("word budgets", sizes, is_error=False)
    _report("paragraph length", long_blocks, is_error=False)
    _report("Documentation Map <-> files sync", map_sync, is_error=False)

    errors = broken + links + footgun
    warns = len(sizes) + len(long_blocks) + len(map_sync)
    print()
    print("RESULT:", "FAIL" if errors else "ok",
          f"({len(errors)} error(s), {warns} warning(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
