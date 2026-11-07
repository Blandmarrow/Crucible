"""Path-prefix tests in SQL go through the `utils` helpers (PM-034).

SQLite's `LIKE` ignores ASCII case, and a column's `.startswith()` compiles to `LIKE`.
So `Image.subfolder.like("knight/%")` also matched `Knight/a`: a gallery rename, a
folder delete, the subfolder filter (and select-all's bulk ops through it) and the
file browser's `/delete` on Linux all reached the wrong rows.

Two helpers, because two kinds of value:

- **Folder labels** (`Image.subfolder`, ComfyUI folders and categories) are
  case-sensitive everywhere: `sql_has_prefix` / `sql_subtree`, always exact.
- **Full stored paths** (`file_path`, `thumbnail_path`, `poster_path`,
  `folder_path`) match the way the filesystem matches: `sql_has_path_prefix`,
  case-blind under `PATHS_FOLD_CASE` (Windows, macOS), exact on Linux. Exact on
  Windows was the review's catch — the stored root's spelling drifts between
  launches (`test_filesystem_root_case.py`).

The first half of this file pins the helpers on a real SQLite; the second is an AST
guard. **What the guard cannot see**: a column reached through a local name
(`col = getattr(model, "file_path")`) and then matched with a bare
`col.startswith(p)` or `sql_has_prefix(col, p)`. It catches the same site when it
passes `autoescape=` (the old spelling), and it bans the exact helper from
`routers/filesystem.py` outright, since every prefix there is a full path.
"""
import ast
from pathlib import Path

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select

from backend import utils
from backend.utils import sql_has_path_prefix, sql_has_prefix, sql_subtree

BACKEND = Path(__file__).resolve().parent.parent

FULL_PATH_COLUMNS = {"file_path", "thumbnail_path", "poster_path", "folder_path"}
# Columns holding a path or a `/`-separated folder label.
PATH_COLUMNS = FULL_PATH_COLUMNS | {"subfolder", "category"}
# `startswith`/`endswith`/`contains` all compile to LIKE on a column.
LIKE_METHODS = {"like", "ilike", "startswith", "endswith", "contains", "istartswith", "iendswith", "icontains"}
EXACT_HELPERS = {"sql_has_prefix", "sql_subtree"}
# Every prefix in this module is a full stored path, so the exact helpers never belong.
FULL_PATH_ONLY_MODULES = {"routers/filesystem.py"}


def _match(rows: list[str], clause_for) -> set[str]:
    engine = create_engine("sqlite://")
    t = Table("t", MetaData(), Column("p", String))
    t.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(t), [{"p": r} for r in rows])
        return {r[0] for r in conn.execute(select(t.c.p).where(clause_for(t.c.p)))}


def test_subtree_is_case_sensitive_and_has_no_wildcards():
    rows = ["knight", "knight/b", "knight/b/c", "Knight", "Knight/a", "knightx", "a_b/c", "axb/c", "a%b/c"]
    assert _match(rows, lambda c: sql_subtree(c, "knight")) == {"knight", "knight/b", "knight/b/c"}
    assert _match(rows, lambda c: sql_has_prefix(c, "knight/")) == {"knight/b", "knight/b/c"}
    assert _match(rows, lambda c: sql_has_prefix(c, "a_b/")) == {"a_b/c"}
    assert _match(rows, lambda c: sql_has_prefix(c, "a%b/")) == {"a%b/c"}


def test_a_non_ascii_prefix_lines_up():
    # Python's len and SQLite's substr must count the same units.
    rows = ["château/a", "château", "chateau/a", "Château/a"]
    assert _match(rows, lambda c: sql_subtree(c, "château")) == {"château/a", "château"}


@pytest.mark.parametrize("fold, want", [
    (True, {"/r/my_ds/images/a.png", "/R/MY_DS/images/b.png"}),
    (False, {"/r/my_ds/images/a.png"}),
])
def test_a_full_path_prefix_folds_case_only_where_the_filesystem_does(monkeypatch, fold, want):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", fold)
    rows = ["/r/my_ds/images/a.png", "/R/MY_DS/images/b.png", "/r/myxds/images/c.png", "/r/my_ds/imagesx/d.png"]
    # `_` stays literal in both modes: `my_ds` never matches `myxds`.
    assert _match(rows, lambda c: sql_has_path_prefix(c, "/r/my_ds/images/")) == want


def _offending_calls(tree: ast.AST, module: str) -> list[int]:
    """Line numbers of path-prefix SQL written by hand, or with the wrong helper."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # `X.<path col>.<like method>(…)`. A Python string has `startswith` too, but no
        # backend code calls it on one of these attributes; bind a local first if needed.
        if (
            isinstance(func, ast.Attribute)
            and func.attr in LIKE_METHODS
            and isinstance(func.value, ast.Attribute)
            and func.value.attr in PATH_COLUMNS
        ):
            hits.append(node.lineno)
            continue
        # `autoescape=` belongs to a column's LIKE operators only; `utils` owns the one
        # sanctioned use, inside `sql_has_path_prefix`.
        if module != "utils.py" and any(k.arg == "autoescape" for k in node.keywords):
            hits.append(node.lineno)
            continue
        # An exact helper applied to a full stored path, or anywhere in a module whose
        # prefixes are all full paths.
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in EXACT_HELPERS:
            first = node.args[0] if node.args else None
            on_full_path = isinstance(first, ast.Attribute) and first.attr in FULL_PATH_COLUMNS
            if on_full_path or module in FULL_PATH_ONLY_MODULES:
                hits.append(node.lineno)
    return hits


def test_no_backend_module_writes_a_path_prefix_by_hand():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in {"tests", "alembic"}:
            continue
        module = rel.as_posix()
        for line in _offending_calls(ast.parse(path.read_text(encoding="utf-8")), module):
            offenders.append(f"{module}:{line}")
    assert not offenders, (
        "Path prefixes go through utils.sql_has_prefix / sql_subtree (folder labels) or "
        "sql_has_path_prefix (full stored paths); see PM-034: " + ", ".join(offenders)
    )


def test_the_guard_can_see_each_shape():
    src = "\n".join([
        'Image.subfolder.like("a/%")',                     # 1
        'model.file_path.startswith(p, autoescape=True)',  # 2
        'Dataset.folder_path.startswith(p)',               # 3
        'row.category.ilike(x)',                           # 4
        'img.file_path.startswith(p)',                     # 5 (no backend code does this)
        'col.startswith(p, autoescape=True)',              # 6 the getattr shape, old spelling
        'sql_has_prefix(Image.file_path, p)',              # 7 exact helper on a full path
        'Image.filename.like(x)',                          # not a path column
        'col.startswith(p)',                               # invisible: a local name
        'sql_subtree(Image.subfolder, p)',                 # the right helper
        'sql_has_path_prefix(model.file_path, p)',         # the right helper
    ])
    assert _offending_calls(ast.parse(src), "services/x.py") == [1, 2, 3, 4, 5, 6, 7]
    # In the file browser, the exact helper is refused even through a local name.
    assert _offending_calls(ast.parse("sql_has_prefix(col, p)"), "routers/filesystem.py") == [1]
    assert _offending_calls(ast.parse("col.startswith(p, autoescape=True)"), "utils.py") == []
