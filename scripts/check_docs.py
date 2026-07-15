#!/usr/bin/env python3
"""Drift check for the documentation: dev docs (CLAUDE.md + docs/dev/*.md) and
user docs (README.md + docs/*.md).

Dependency-free (stdlib only). Run from anywhere:

    python scripts/check_docs.py

Checks (FAIL sets a non-zero exit code; WARN does not):
  1. FAIL  broken repo-path references in the docs (dev + user)
  2. FAIL  a markdown link to a .md file is missing, or its #anchor matches no heading
  3. FAIL  the `@docs/` / `@CLAUDE` auto-load footgun appears outside inline code
  4. WARN  CLAUDE.md / docs/dev/*.md over their size thresholds
  5. WARN  a docs/dev/*.md file has no Documentation Map row, or vice versa

Why the heuristics are conservative: the docs reference many paths relative to
`backend/` (e.g. `routers/captioning.py`) and plenty of non-paths that superficially
look pathish (API routes like `stream/all/events`, globs, code fragments). To avoid
false positives we (a) ignore fenced ``` code blocks entirely, (b) only treat a token
as a checkable path when it contains `/` and ends in a known file extension or `/`,
and (c) resolve it against the repo root *and* `backend/`/`frontend/` before flagging.

Two file sets, deliberately different: size and Documentation Map checks are
dev-only (`docs/*.md` has neither a Map nor a line budget), while path and anchor
checks cover both. What this CANNOT do is notice that a shipped feature has no
user docs at all — the gap that motivated adding user docs here in the first place.
Only the CLAUDE.md maintenance rules prevent that.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- Configuration ---------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

CLAUDE_MAX_LINES = 200   # raised from 150: the full maintenance block is intentionally long
TOPIC_MAX_LINES = 250

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


def check_sizes(files: list[Path]) -> list[str]:
    warnings: list[str] = []
    for f in files:
        n = len(f.read_text(encoding="utf-8").splitlines())
        limit = CLAUDE_MAX_LINES if f.name == "CLAUDE.md" else TOPIC_MAX_LINES
        if n > limit:
            warnings.append(f"{f.relative_to(REPO_ROOT)}: {n} lines (> {limit})")
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
    sizes = check_sizes(dev)          # docs/*.md has no line budget
    map_sync = check_map_sync()       # docs/*.md has no Documentation Map

    print("Documentation drift check\n" + "=" * 26)
    print(f"       {len(dev)} dev file(s), {len(user)} user file(s)\n")
    _report("broken path references", broken, is_error=True)
    _report("markdown links & anchors", links, is_error=True)
    _report("@-path auto-load footgun", footgun, is_error=True)
    _report("file sizes", sizes, is_error=False)
    _report("Documentation Map <-> files sync", map_sync, is_error=False)

    errors = broken + links + footgun
    print()
    print("RESULT:", "FAIL" if errors else "ok",
          f"({len(errors)} error(s), {len(sizes) + len(map_sync)} warning(s))")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
