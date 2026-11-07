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

Full stored paths also have an *equality* twin, `sql_path_equals` (and the Python
`path_str_equals` / `path_str_has_prefix`), for the file arms of the file browser:
a bare `model.file_path == str(p)` misses a row whose spelling differs only in case.

The first half of this file pins the helpers on a real SQLite; the second is an AST
guard. **What the guard cannot see**: a column reached through a local name
(`col = getattr(model, "file_path")`) and then matched with a bare
`col.startswith(p)`, `sql_has_prefix(col, p)` or `col == str(p)`. It catches the
same site when it passes `autoescape=` (the old spelling), and it bans the exact
helper from `routers/filesystem.py` outright, since every prefix there is a full
path. In that module it also bans `==` / `!=` on a column read off a *class* or the
conventional `model` loop variable (`Image.file_path`, `model.file_path`); a plain
Python read off an ORM instance (`img.file_path == x`, lower-case name) is not
flagged, since it compares an already-loaded value, not a query.

Shapes a review found that the guard does **not** see, so a reviewer must: a loop
variable other than `model` (`m.file_path == x`), a column fetched by name
(`getattr(model, "file_path") == x`, the `getattr` shape above, and
`Image.__table__.c.file_path == x`). It does flag `filter_by(file_path=...)` and
`.in_([...])` on a full-path column in `routers/filesystem.py`: both are exact
matches, and the router has no legitimate use of either.
"""
import ast
from pathlib import Path

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select

from backend import utils
from backend.utils import (
    path_str_equals,
    path_str_has_prefix,
    sql_has_path_prefix,
    sql_has_prefix,
    sql_path_equals,
    sql_subtree,
)

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


@pytest.mark.parametrize("fold, want", [
    (True, {"C:/a_b/x", "c:/A_B/X"}),
    (False, {"C:/a_b/x"}),
])
def test_path_equals_folds_case_only_where_the_filesystem_does(monkeypatch, fold, want):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", fold)
    rows = ["C:/a_b/x", "C:/axb/x", "C:/a%b/x", "c:/A_B/X", "C:/a_b/x/y", "C:/a_b/xx"]
    # `_` is a literal, not a one-character wildcard, and no row below the path matches.
    assert _match(rows, lambda c: sql_path_equals(c, "C:/a_b/x")) == want


@pytest.mark.parametrize("fold", [True, False])
def test_path_equals_treats_percent_and_slash_literally(monkeypatch, fold):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", fold)
    rows = ["C:/a%b/x", "C:/aXXb/x", "C:/ab/x", "C:/a/b/x", "C://a%b/x"]
    assert _match(rows, lambda c: sql_path_equals(c, "C:/a%b/x")) == {"C:/a%b/x"}
    # The escape character itself must not leak: a path holding `//` matches only itself.
    assert _match(rows, lambda c: sql_path_equals(c, "C://a%b/x")) == {"C://a%b/x"}


def test_path_equals_is_ascii_only_under_the_flag(monkeypatch):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", True)
    rows = ["/r/é", "/r/É", "/r/e"]
    assert _match(rows, lambda c: sql_path_equals(c, "/r/é")) == {"/r/é"}


@pytest.mark.parametrize("fold, a, b, want", [
    (True, "C:/Foo/a_b", "c:/foo/A_B", True),
    (True, "C:/Foo/a_b", "c:/foo/AxB", False),
    (True, "é", "É", False),          # ASCII only, to agree with SQLite's LIKE
    (True, "é", "é", True),
    (False, "C:/Foo", "c:/foo", False),
    (False, "C:/Foo", "C:/Foo", True),
])
def test_path_str_equals(monkeypatch, fold, a, b, want):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", fold)
    assert path_str_equals(a, b) is want


@pytest.mark.parametrize("fold, s, prefix, want", [
    (True, "C:/Root/ds/images/a.png", "c:/root/DS/", True),
    (True, "C:/Root/ds/images/a.png", "c:/root/other/", False),
    (True, "C:/Ro", "C:/Root/", False),     # shorter than the prefix
    (True, "/r/é/x", "/r/É/", False),
    (False, "C:/Root/ds/a.png", "c:/root/ds/", False),
    (False, "C:/Root/ds/a.png", "C:/Root/ds/", True),
])
def test_path_str_has_prefix(monkeypatch, fold, s, prefix, want):
    monkeypatch.setattr(utils, "PATHS_FOLD_CASE", fold)
    assert path_str_has_prefix(s, prefix) is want


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


def _offending_compares(tree: ast.AST, module: str) -> list[int]:
    """Line numbers of `==` / `!=` against a full-path column in a full-path-only module.

    Only a column read off a class (`Image.file_path`, an upper-case first letter) or
    the conventional loop variable `model` is a query term. A lower-case name
    (`img.file_path == x`, `row.folder_path != y`) is a Python read of an ORM
    instance: an already-loaded value, legitimately exact, so it is not flagged.
    """
    if module not in FULL_PATH_ONLY_MODULES:
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        for side in [node.left, *node.comparators]:
            if (
                isinstance(side, ast.Attribute)
                and side.attr in FULL_PATH_COLUMNS
                and isinstance(side.value, ast.Name)
                and (side.value.id[:1].isupper() or side.value.id == "model")
            ):
                hits.append(node.lineno)
                break
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        func = node.func
        # `.filter_by(file_path=...)`: an exact match spelled as a keyword.
        if func.attr == "filter_by" and any(k.arg in FULL_PATH_COLUMNS for k in node.keywords):
            hits.append(node.lineno)
        # `X.file_path.in_([...])`: an exact membership test.
        elif (
            func.attr == "in_"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr in FULL_PATH_COLUMNS
            and isinstance(func.value.value, ast.Name)
            and (func.value.value.id[:1].isupper() or func.value.value.id == "model")
        ):
            hits.append(node.lineno)
    return sorted(hits)


def test_no_backend_module_writes_a_path_prefix_by_hand():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in {"tests", "alembic"}:
            continue
        module = rel.as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line in _offending_calls(tree, module) + _offending_compares(tree, module):
            offenders.append(f"{module}:{line}")
    assert not offenders, (
        "Path prefixes go through utils.sql_has_prefix / sql_subtree (folder labels) or "
        "sql_has_path_prefix / sql_path_equals (full stored paths); see PM-034: " + ", ".join(offenders)
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


def test_the_guard_sees_an_exact_match_on_a_path_column_in_the_file_browser():
    src = "\n".join([
        'Image.file_path == str(p)',                # 1
        'model.file_path == str(src)',               # 2
        'str(p) == Dataset.folder_path',             # 3 either side
        'Dataset.folder_path != x',                  # 4
        'img.file_path == other',                    # a Python read of an instance
        'Image.filename == name',                    # not a path column
        'a < Image.file_path',                       # not an equality
        'q.filter_by(file_path=x)',                  # 5
        'Image.thumbnail_path.in_(paths)',           # 6
        'Image.id.in_(ids)',                         # not a path column
        'q.filter_by(dataset_id=x)',                 # not a path column
        'sql_path_equals(model.file_path, str(p))',  # the right helper
    ])
    assert _offending_compares(ast.parse(src), "routers/filesystem.py") == [1, 2, 3, 4, 8, 9]
    assert _offending_compares(ast.parse(src), "services/x.py") == []
