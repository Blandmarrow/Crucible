"""Row-layout experiment for M9 stage 7: where should the big columns live?

`bench_scaling.py` times the app as it is. This script answers a question the app cannot
be asked yet: what would the Stats statements cost if the three embedding blobs, and
`generation_metadata`, did not sit in the middle of every `images` row? It takes a seeded
file that `bench_scaling.py --keep-db` (or `--reuse-db`) left behind, builds copies of it
in other layouts, and replays the same statements on each.

Run from the repo root with the venv active:

    python -m backend.scripts.bench_split_layout \
        --db .claude/local/bench/work/bench-photo-100000-nb0-alembic.db \
        --cold --json .claude/local/bench/stage7-photo-100k.json

What it does:

1. **Capture**, once, on the file as it is. The app runs against it the way
   `bench_scaling.py` runs it, and every statement the two Stats endpoints and a few
   gallery pages issue is kept with its real parameters (`<db>.stage7-stmts.json`).
   Capture needs the app's models, so it only works on the blobs-in-row file. The
   Stats endpoints are captured dataset-wide and again scoped to one subfolder (30% of
   the rows, and 20 rows), since a subfolder scope has an index of its own. An index
   hint the app itself puts on a statement is taken off at capture and listed.
   Style scoring's keyset pages are compiled from the construct `_score_paginated`
   builds (`backend/routers/quality.py`), since capturing them would mean running a
   scoring job; `bench_scaling.bench_similarity`'s one-statement load is kept beside
   them, marked legacy, because stage 6b reported it.
2. **Layouts**, each a copy beside the source (`<db stem>.<layout>.db`):

   - ``row``: the source file itself. It reproduces `bench_scaling.py`'s numbers.
   - ``row_vacuum``: the same rows after a `VACUUM`. The control: every other copy is
     vacuumed, which also rebuilds each index compact, so a layout is compared with this
     and not with ``row``.
   - ``blobs_last``: no side table. `images` is rebuilt with the four big columns at the
     end of the row, so no reader changes.
   - ``split3``: the three embedding blobs move to `image_embeddings`, filled in the
     images table's rowid order. That is the file straight after a migration, and the
     best case for a statement that joins the two.
   - ``split3_shuffled``: the same, filled in id order, which is random against rowid.
     The worst case: a file whose embeddings were all written by later scoring runs.
   - ``split4``: `generation_metadata` moves too, to `image_generation_metadata`.

   A build runs with the journal off, so its step times are a floor for what a real
   migration would cost, not an estimate of it. Every layout is built before any is
   measured, and a run that built one waits (`--settle`) before it measures: a build
   writes and deletes tens of GB, and an SSD still tidying up after that slows the cold
   reads of whichever layout comes next.
3. **Replay** on raw `sqlite3`, through `bench_scaling.replay`. The Stats statements run
   under three index choices: SQLite's own, `INDEXED BY ix_images_dataset_id` (rowid
   order within the dataset) and `NOT INDEXED` (the whole table in rowid order). The
   gallery and similarity statements run under SQLite's own choice only.
   A statement that names a moved column joins the side table when it has a known
   side-table form (the embedding count, the similarity reads, a gallery page's rows);
   any other is skipped and listed.
4. **Check.** Before a statement is timed it is run once and its rows are counted and
   checksummed, and each layout's big columns are checksummed whole, through the joins.
   Every layout and index choice must give the same answer; a mismatch is reported and
   the exit code is 1.

What it cannot tell you: the seed's rows are nearly all one size, so ``blobs_last`` keeps
every small column on the leaf page here. Real rows vary (captions, metadata), and some
would push their small columns onto the first overflow page. Read its numbers as a best
case. And a photo-profile file says little about moving `generation_metadata` (400 bytes
a row); only a ComfyUI-profile file does.

`--cold` evicts the file before every replayed statement, as `bench_scaling.py --cold`
does. Keep result files out of the repo: the JSON records the machine it ran on.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.scripts import bench_scaling as bench

EMBEDDING_COLUMNS = ("clip_embedding", "dino_embedding", "dino_layer_embeddings")
METADATA_COLUMN = "generation_metadata"
BIG_COLUMNS = (*EMBEDDING_COLUMNS, METADATA_COLUMN)

LAYOUTS = ("row", "row_vacuum", "blobs_last", "split3", "split3_shuffled", "split4")
# The columns a layout takes out of `images`.
MOVED = {
    "row": (), "row_vacuum": (), "blobs_last": (),
    "split3": EMBEDDING_COLUMNS, "split3_shuffled": EMBEDDING_COLUMNS, "split4": BIG_COLUMNS,
}
INDEX_CHOICES = {
    "default": None,
    "dataset_id": "INDEXED BY ix_images_dataset_id",
    "table_order": "NOT INDEXED",
}
# Bump when a layout's build changes, so an older copy is rebuilt and not reused.
LAYOUT_VERSION = 1

# Bump when the statement list changes, so an older capture is taken again.
STATEMENTS_VERSION = 3
# An index hint the app itself puts on a statement (decision 15 pins the dataset-wide
# Stats scans). It is taken off at capture, so "default" below stays SQLite's own choice
# and the forced choices do not stack a second hint on the first.
APP_HINT = re.compile(r" INDEXED BY \w+")

LEGACY_SIMILARITY_SQL = (
    "SELECT images.id, images.dino_embedding FROM images "
    "WHERE images.dataset_id = ? AND images.dino_embedding IS NOT NULL"
)
# The side table each moved column lands in. A statement says which of them it may join,
# and how (its `joins`): an inner join only asks the same question when the statement
# already requires the column to be non-NULL.
SIDE_TABLES = {
    "image_embeddings": EMBEDDING_COLUMNS,
    "image_generation_metadata": (METADATA_COLUMN,),
}
# Every row's big columns, wherever the layout keeps them: the whole-content check.
CONTENT = {
    "group": "content", "label": "big columns, every row",
    "sql": "SELECT images.id, " + ", ".join(f"images.{c}" for c in BIG_COLUMNS) + " FROM images",
    "params": [],
    "joins": {"image_embeddings": "LEFT JOIN", "image_generation_metadata": "LEFT JOIN"},
}
ROWS_LABEL = "page rows (100 by id)"
ROWS_CASE = "gallery aesthetic_score desc deep"

GALLERY_CASES = (
    "gallery luminance_score desc p1",   # a sort column after the blobs
    "gallery luminance_score desc deep",
    "gallery color_score desc deep",     # no index, before the blobs
    f"gallery aesthetic_score desc p1 subfolder={bench.SMALL_SUBFOLDER}",
    "gallery aesthetic_score desc deep",  # indexed: the control that should not move
)
STATS_CASES = ("stats cold", "score-values cold")
# The same two endpoints scoped to one subfolder: 30% of the dataset, and 20 rows. A
# subfolder scope has an index of its own (`ix_images_dataset_subfolder`), so whether the
# forced index helps there is a separate question from the dataset-wide one.
STATS_SUBFOLDERS = ("set_a", bench.SMALL_SUBFOLDER)
STATS_GROUPS = ("stats", "stats_subfolder")


# --------------------------------------------------------------------------- capture
def _stats_label(sql: str) -> str:
    s = bench._one_line(sql)
    if "max(images.updated_at)" in s:
        return "validator"
    if "json_extract" in s:
        return "flag sums"
    if "clip_embedding IS NOT NULL" in s:
        return "embedding count"
    if "GROUP BY images.aesthetic_model" in s:
        return "aesthetic_model group"
    if "nullif(images.license" in s:
        return "license group"
    if "count(images.aesthetic_score)" in s:
        return "coverage counts"
    if "images.caption_text" in s:
        return "row fetch (stats)" if "images.format" in s else "row fetch (score-values)"
    return "stats statement"


async def capture_statements(db_path: Path, datasets_dir: Path, n: int) -> list[dict]:
    """The statements to replay, each `{group, label, sql, params}`, with unique labels."""
    from backend.services import dataset_service

    wanted = {*STATS_CASES, *GALLERY_CASES}
    cases = [c for c in bench.build_cases({"gallery", "stats"}, n, [], []) if c["name"] in wanted]
    missing = wanted - {c["name"] for c in cases}
    if missing:
        raise SystemExit(f"bench_scaling no longer builds these cases: {sorted(missing)}")
    for sub in STATS_SUBFOLDERS:
        for ep in ("stats", "score-values"):
            cases.append({"name": f"{ep} cold subfolder={sub}", "group": "stats", "subfolder": sub,
                          "reqs": [(f"/api/v1/datasets/{bench.DATASET_ID}/{ep}", {"subfolder": sub})]})

    capture = bench.Capture()
    engine = bench.make_engine(db_path, capture, cold=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    out: list[dict] = []
    seen: set[tuple[str, tuple]] = set()
    labels: dict[str, int] = {}

    def add(group: str, label: str, sql: str, params: tuple, joins: dict | None = None) -> None:
        app_hint = APP_HINT.search(sql)
        sql = APP_HINT.sub("", sql)
        if (sql, params) in seen:
            return  # the validator opens both Stats endpoints
        seen.add((sql, params))
        labels[label] = labels.get(label, 0) + 1
        if labels[label] > 1:
            label = f"{label} #{labels[label]}"
        bad = [p for p in params if not isinstance(p, (str, int, float, type(None)))]
        if bad:
            raise SystemExit(f"{label}: a parameter that JSON cannot keep: {bad[0]!r}")
        out.append({"group": group, "label": label, "sql": sql, "params": list(params),
                    "joins": joins or {}, "app_hint": app_hint.group(0).strip() if app_hint else None})

    try:
        async with bench.app_client(Session, datasets_dir) as client:
            for case in cases:
                dataset_service._stats_cache.clear()
                capture.stmts = []
                status, _, _ = await bench._issue(client, case, capture)
                if status != 200:
                    raise SystemExit(f"{case['name']}: HTTP {status} during capture")
                images = [(s, p) for s, p in capture.stmts
                          if "FROM images" in s and s.lstrip().upper().startswith(("SELECT", "WITH"))]
                if case["group"] == "stats":
                    sub = case.get("subfolder")
                    for sql, params in images:
                        label = _stats_label(sql)
                        joins = {"image_embeddings": "JOIN"} if label == "embedding count" else None
                        if sub is None:
                            add("stats", label, sql, params, joins)
                        else:
                            add("stats_subfolder", f"{label} [{sub}]", sql, params, joins)
                    continue
                if not images:
                    raise SystemExit(f"{case['name']}: no statement on images was captured")
                # A gallery page picks its ids first; on an unindexed sort that statement
                # is the page's cost. It then loads those rows, the same work whatever the
                # sort, so one page's row load stands for all of them. It is the one
                # statement here that reads `generation_metadata` (for the
                # `has_generation_metadata` flag). A row with no metadata must still come
                # back, hence the outer join.
                add("gallery", case["name"].removeprefix("gallery "), *images[0])
                rows = [(s, p) for s, p in images[1:] if "images.id IN" in s]
                if rows and case["name"] == ROWS_CASE:
                    add("gallery", ROWS_LABEL, *rows[0], {"image_generation_metadata": "LEFT JOIN"})
    finally:
        dataset_service._stats_cache.clear()
        await engine.dispose()
    if not any(s["label"] == ROWS_LABEL for s in out):
        raise SystemExit("capture found no page-row statement; has list_images changed?")
    for label, sql, params in similarity_pages():
        add("similarity", label, sql, params, {"image_embeddings": "JOIN"})
    add("similarity", "legacy: whole set in one statement (bench_similarity)",
        LEGACY_SIMILARITY_SQL, (bench.DATASET_ID,), {"image_embeddings": "JOIN"})
    return out


def similarity_pages() -> list[tuple[str, str, tuple]]:
    """Style scoring's candidate reads, as `_score_paginated` issues them since M9 stage 5:
    a keyset page by id. Compiled from the same construct, so the text is the app's. Two
    column sets (the combined final-layer branch, and the 18 KB per-layer blob), each at
    the first page and at one from the middle of the id range."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite

    from backend.models.image import Image
    from backend.routers.quality import _STYLE_PAGE_SIZE

    out = []
    for name, cols in (("clip + dino", (Image.clip_embedding, Image.dino_embedding)),
                       ("layers", (Image.dino_layer_embeddings,))):
        for where, last_id in (("first", ""), ("middle", "8")):  # ids are UUIDs: hex text
            stmt = (
                select(Image.id, *cols)
                .where(Image.dataset_id == bench.DATASET_ID, *(c.isnot(None) for c in cols),
                       Image.id > last_id)
                .order_by(Image.id)
                .limit(_STYLE_PAGE_SIZE)
            )
            compiled = stmt.compile(dialect=sqlite.dialect())
            params = tuple(compiled.params[k] for k in compiled.positiontup)
            out.append((f"keyset page of {_STYLE_PAGE_SIZE}, {name}, {where}", str(compiled), params))
    return out


# --------------------------------------------------------------------------- layouts
def split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on the commas outside parentheses and quotes."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    for i, ch in enumerate(body):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i].strip())
            start = i + 1
    parts.append(body[start:].strip())
    return [p for p in parts if p]


def reordered_ddl(create_sql: str, columns: list[str], last: tuple[str, ...], new_name: str) -> str:
    """`create_sql` for a table named `new_name`, with the `last` columns moved to the end
    of the column list. Table constraints stay after every column, where SQLite wants them
    (a column that `ALTER TABLE ADD COLUMN` appended sits after them in the stored text).
    """
    body = create_sql[create_sql.index("(") + 1:create_sql.rindex(")")]
    defs: dict[str, str] = {}
    constraints: list[str] = []
    for part in split_top_level(body):
        name = part.split(None, 1)[0].strip("\"`[]")
        if name in columns and name not in defs:
            defs[name] = part
        else:
            constraints.append(part)
    if set(defs) != set(columns):
        raise SystemExit(f"could not parse the images DDL: columns {sorted(set(columns) ^ set(defs))}")
    order = [c for c in columns if c not in last] + [c for c in columns if c in last]
    return f'CREATE TABLE "{new_name}" (\n\t' + ", \n\t".join([*(defs[c] for c in order), *constraints]) + "\n)"


def _columns(con: sqlite3.Connection, table: str = "images") -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _transform_blobs_last(con: sqlite3.Connection) -> None:
    columns = _columns(con)
    create_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'images'").fetchone()[0]
    indexes = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'images' AND sql IS NOT NULL")]
    con.execute(reordered_ddl(create_sql, columns, BIG_COLUMNS, "images_bench_new"))
    cols = ", ".join(columns)
    # The rowid is copied, not reassigned: every index entry ends with it, and the
    # gallery's tie-break and the "dataset_id" index choice both walk in its order.
    con.execute(f"INSERT INTO images_bench_new (rowid, {cols}) SELECT rowid, {cols} FROM images ORDER BY rowid")
    con.execute("DROP TABLE images")  # foreign keys are off here, so nothing cascades
    con.execute("ALTER TABLE images_bench_new RENAME TO images")
    for sql in indexes:
        con.execute(sql)


def _transform_split(con: sqlite3.Connection, with_metadata: bool, order: str = "rowid") -> None:
    id_type = next(r[2] for r in con.execute("PRAGMA table_info(images)") if r[1] == "id")
    blobs = ", ".join(EMBEDDING_COLUMNS)
    con.execute(
        f"CREATE TABLE image_embeddings (\n\timage_id {id_type} NOT NULL, \n\t"
        + ", \n\t".join(f"{c} BLOB" for c in EMBEDDING_COLUMNS)
        + ", \n\tPRIMARY KEY (image_id), \n\tFOREIGN KEY(image_id) REFERENCES images (id) ON DELETE CASCADE\n)"
    )
    # `rowid`: the side table's own rowid order follows the images table's, as it would
    # straight after a migration. `id`: random against it, as after later scoring runs.
    con.execute(
        f"INSERT INTO image_embeddings (image_id, {blobs}) SELECT id, {blobs} FROM images WHERE "
        + " OR ".join(f"{c} IS NOT NULL" for c in EMBEDDING_COLUMNS) + f" ORDER BY {order}"
    )
    if with_metadata:
        con.execute(
            f"CREATE TABLE image_generation_metadata (\n\timage_id {id_type} NOT NULL, \n\t"
            f"{METADATA_COLUMN} JSON, \n\tPRIMARY KEY (image_id), \n\t"
            "FOREIGN KEY(image_id) REFERENCES images (id) ON DELETE CASCADE\n)"
        )
        con.execute(
            f"INSERT INTO image_generation_metadata (image_id, {METADATA_COLUMN}) "
            f"SELECT id, {METADATA_COLUMN} FROM images WHERE {METADATA_COLUMN} IS NOT NULL ORDER BY rowid"
        )
    for col in (BIG_COLUMNS if with_metadata else EMBEDDING_COLUMNS):
        con.execute(f"ALTER TABLE images DROP COLUMN {col}")


def _layout_meta(src: Path, layout: str) -> dict:
    return {"layout": layout, "version": LAYOUT_VERSION, "source": src.name,
            "source_bytes": src.stat().st_size, "source_seed": bench.read_meta(src)}


def _read_layout_meta(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    try:
        with contextlib.closing(sqlite3.connect(db_path)) as con:
            row = con.execute("SELECT value FROM bench_meta WHERE key = 'layout'").fetchone()
        return json.loads(row[0]) if row else None
    except sqlite3.Error:
        return None


def layout_path(src: Path, layout: str) -> Path:
    return src if layout == "row" else src.with_name(f"{src.stem}.{layout}.db")


def build_layout(src: Path, layout: str, rebuild: bool = False) -> tuple[Path, dict]:
    """The file for `layout`, built from `src` unless a matching copy is already there.
    Returns its path and how the build went (`{}` for ``row``, `{"reused": True}` for a
    copy found)."""
    dest = layout_path(src, layout)
    bench.check_scratch_path(dest)
    if layout == "row":
        return dest, {}
    meta = _layout_meta(src, layout)
    if not rebuild and _read_layout_meta(dest) == meta:
        print(f"    {layout}: reusing {dest.name}", flush=True)
        return dest, {"reused": True}

    from backend.utils import InsufficientDiskSpaceError, require_free_space

    try:  # blobs_last holds two copies of `images` in its working file, plus the result
        require_free_space(src.parent, int(src.stat().st_size * 3.2))
    except InsufficientDiskSpaceError as exc:
        raise SystemExit(str(exc)) from exc

    work = dest.with_name(dest.name + ".building")
    out = dest.with_name(dest.name + ".vacuumed")
    for p in (*bench._db_files(dest), *bench._db_files(work), *bench._db_files(out)):
        p.unlink(missing_ok=True)
    steps: dict[str, float] = {}

    def timed(name: str, fn) -> None:
        t0 = time.perf_counter()
        fn()
        steps[name] = round(time.perf_counter() - t0, 2)
        print(f"    {layout}: {name} {steps[name]:.1f}s", flush=True)

    try:
        with contextlib.closing(sqlite3.connect(src)) as con:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # the copy takes the main file only
            rows, lo, hi = con.execute("SELECT count(*), min(rowid), max(rowid) FROM images").fetchone()
        timed("copy_s", lambda: shutil.copyfile(src, work))
        # isolation_level=None: no implicit transaction, so the pragmas and VACUUM apply.
        con = sqlite3.connect(work, isolation_level=None)
        try:
            con.execute("PRAGMA journal_mode=OFF")  # a scratch copy: a crash means a rebuild
            con.execute("PRAGMA synchronous=OFF")
            con.execute("PRAGMA foreign_keys=OFF")
            if layout == "blobs_last":
                timed("rebuild_s", lambda: _transform_blobs_last(con))
            elif layout in ("split3", "split3_shuffled", "split4"):
                timed("split_s", lambda: _transform_split(
                    con, layout == "split4", "id" if layout == "split3_shuffled" else "rowid"))
            elif layout != "row_vacuum":
                raise SystemExit(f"unknown layout {layout!r}")
            con.execute("INSERT OR REPLACE INTO bench_meta VALUES ('layout', ?)", (json.dumps(meta),))
            timed("vacuum_s", lambda: con.execute("VACUUM INTO ?", (str(out),)))
        finally:
            con.close()
        with contextlib.closing(sqlite3.connect(out, isolation_level=None)) as con:
            con.execute("PRAGMA journal_mode=WAL")  # what a real install's file is in
            after = con.execute("SELECT count(*), min(rowid), max(rowid) FROM images").fetchone()
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if tuple(after) != (rows, lo, hi):
            raise SystemExit(f"{layout}: images went from {(rows, lo, hi)} to {tuple(after)} (rows, rowid range)")
        for p in bench._db_files(out)[1:]:
            p.unlink(missing_ok=True)
        os.replace(out, dest)
    finally:
        for p in (*bench._db_files(work), *bench._db_files(out)):
            p.unlink(missing_ok=True)
    return dest, steps


def layout_info(db_path: Path, dbstat: bool) -> dict:
    """Where the big columns sit, and how many rows share a leaf page of `images`."""
    with contextlib.closing(sqlite3.connect(db_path)) as con:
        cols = _columns(con)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        rows = con.execute("SELECT count(*) FROM images").fetchone()[0]
        info: dict = {
            "db_bytes": db_path.stat().st_size,
            "page_size": con.execute("PRAGMA page_size").fetchone()[0],
            "columns": len(cols),
            "big_columns_at": {c: cols.index(c) + 1 for c in BIG_COLUMNS if c in cols},
            "after_big": cols[max(cols.index(c) for c in BIG_COLUMNS if c in cols) + 1:]
            if any(c in cols for c in BIG_COLUMNS) else [],
            "side_tables": sorted(tables & {"image_embeddings", "image_generation_metadata"}),
            "rows": rows,
        }
        if dbstat:
            try:  # dbstat reads every page of the table, and not every build has it
                pages = dict(con.execute(
                    "SELECT pagetype, count(*) FROM dbstat WHERE name = 'images' GROUP BY pagetype"))
                info["images_pages"] = pages
                if pages.get("leaf"):
                    info["rows_per_leaf"] = round(rows / pages["leaf"], 2)
            except sqlite3.Error as exc:
                info["dbstat"] = f"unavailable: {exc}"
    return info


# --------------------------------------------------------------------------- statements
def _names(sql: str, cols: tuple[str, ...]) -> list[str]:
    return [c for c in cols if re.search(rf"\b{c}\b", sql)]


def statement_for(stmt: dict, layout: str, hint: str | None) -> tuple[str | None, str | None]:
    """The SQL to run for `stmt` on `layout` under an index `hint`, and a note on what was
    changed. `(None, why)` when the statement cannot be run there."""
    sql = stmt["sql"]
    notes: list[str] = []
    named = _names(sql, MOVED[layout])
    joins = stmt.get("joins") or {}
    # Last table first: each join goes in directly after `FROM images`, so the first
    # table's ends up in front, and an index hint (below) in front of both.
    for table, cols in reversed(SIDE_TABLES.items()):
        here = [c for c in named if c in cols]
        if not here:
            continue
        if table not in joins:
            return None, f"names {', '.join(here)}, which this layout moves; no rewrite"
        for col in here:
            sql = re.sub(rf"\bimages\.{col}\b", f"{table}.{col}", sql)
        if any(re.search(rf"(?<!{table}\.)\b{col}\b", sql) for col in here):
            return None, f"names {', '.join(here)} without the table name; no rewrite"
        if sql.count("FROM images") != 1:
            return None, "not one FROM images to join from"
        sql = sql.replace(
            "FROM images", f"FROM images {joins[table]} {table} ON {table}.image_id = images.id", 1)
        notes.insert(0, f"{joins[table].lower()}s {table}")
    if hint:
        if sql.count("FROM images") != 1:
            return None, "not one FROM images to hint"
        sql = sql.replace("FROM images", f"FROM images {hint}", 1)
    return sql, "; ".join(notes) or None


def _checksum(con: sqlite3.Connection, sql: str, params: tuple) -> tuple[int, int]:
    """Row count and an order-free checksum of a statement's rows. Order-free because two
    index choices return the same rows in different orders when nothing sorts them."""
    rows = 0
    acc = 0
    for row in con.execute(sql, params):
        rows += 1
        data = b"\x1f".join(v if isinstance(v, bytes) else repr(v).encode() for v in row)
        acc = (acc + zlib.crc32(data)) & 0xFFFFFFFFFFFFFFFF
    return rows, acc


def measure_layout(db_path: Path, layout: str, stmts: list[dict], reps: int,
                   cold_reps: int, evictions: bench.Evictions | None) -> list[dict]:
    results: list[dict] = []
    for index_name, hint in INDEX_CHOICES.items():
        batch: list[tuple[dict, str, tuple]] = []
        con = sqlite3.connect(db_path)
        try:
            con.execute("PRAGMA synchronous=NORMAL")
            for stmt in stmts:
                if hint and stmt["group"] not in STATS_GROUPS:
                    continue  # forcing an index under a sort would measure something else
                entry = {"group": stmt["group"], "label": stmt["label"], "index": index_name}
                results.append(entry)
                sql, note = statement_for(stmt, layout, hint)
                if sql is None:
                    entry.update(status="skipped", note=note)
                    continue
                params = tuple(stmt["params"])
                entry.update(sql=bench._one_line(sql), note=note)
                try:
                    # Also the first, untimed call that fills the cache for the warm pass.
                    entry["rows"], entry["checksum"] = _checksum(con, sql, params)
                except sqlite3.OperationalError as exc:
                    # The one error an index hint may honestly cause. Anything else is a
                    # broken rewrite: it must stop the run, not drop out of the check.
                    if "no query solution" not in str(exc):
                        raise SystemExit(f"{layout}/{index_name}, {stmt['label']}: {exc}\n{sql}") from exc
                    entry.update(status=f"n/a: {exc}")
                    continue
                entry["status"] = "ok"
                batch.append((entry, sql, params))
        finally:
            con.close()  # an open handle would keep a cold run's eviction from working
        pairs = [(sql, params) for _, sql, params in batch]
        warm = bench.replay(db_path, pairs, reps, None)
        if len(warm) != len(batch):
            raise SystemExit(f"{layout}/{index_name}: replay merged two statements")
        for (entry, _, _), w in zip(batch, warm):
            entry.update(warm_ms=w["replay_ms"], plan=w["plan"], temp_btree=w["temp_btree"])
        if evictions is not None:
            for (entry, _, _), c in zip(batch, bench.replay(db_path, pairs, cold_reps, evictions)):
                entry["cold_ms"] = c["replay_ms"]
        done = sum(e.get("warm_ms") or 0 for e, _, _ in batch)
        print(f"    {layout}/{index_name}: {len(batch)} statements, {done:,.0f} ms warm", flush=True)
    return results


def cross_check(layouts: dict) -> list[str]:
    """Every statement must return the same rows on every layout and under every index."""
    first: dict[tuple[str, str], tuple[str, tuple[int, int]]] = {}
    problems: list[str] = []
    for layout, data in layouts.items():
        for r in (*([data["content"]] if data.get("content") else []), *data.get("results", [])):
            if r["status"] != "ok":
                continue
            key = (r["group"], r["label"])
            answer = (r["rows"], r["checksum"])
            where = f"{layout}/{r['index']}"
            if key not in first:
                first[key] = (where, answer)
            elif first[key][1] != answer:
                problems.append(f"{r['label']}: {where} gave {answer}, {first[key][0]} gave {first[key][1]}")
    return problems


# --------------------------------------------------------------------------- driver
async def run(args) -> dict:
    src = Path(args.db).resolve()
    bench.check_scratch_path(src)
    if not src.exists():
        raise SystemExit(f"{src} does not exist; seed one with bench_scaling.py --keep-db")
    layouts = [x.strip() for x in args.layouts.split(",") if x.strip()]
    unknown = set(layouts) - set(LAYOUTS)
    if unknown:
        raise SystemExit(f"--layouts: unknown layout(s) {sorted(unknown)}")
    if args.cold and not bench.cold_cache_supported(bench.ALEMBIC_INI):
        raise SystemExit("--cold: this platform cannot evict a file from the OS cache")
    # Only a file this harness seeded. The path guard knows this checkout's database and
    # no other, and what follows writes to the file (`drop_analysis`) and beside it.
    seed = bench.read_meta(src)
    if not seed or "n" not in seed:
        raise SystemExit(f"{src.name} was not seeded by bench_scaling.py (no bench_meta seed row)")
    n = seed["n"]
    with contextlib.closing(sqlite3.connect(src)) as con:
        missing = [c for c in BIG_COLUMNS if c not in _columns(con)]
    if missing:
        raise SystemExit(f"{src.name} has no {', '.join(missing)}: it is not a blobs-in-row file")

    stmts_path = Path(args.statements) if args.statements else src.with_name(src.stem + ".stage7-stmts.json")
    kept = json.loads(stmts_path.read_text()) if stmts_path.exists() and not args.recapture else {}
    if isinstance(kept, dict) and kept.get("version") == STATEMENTS_VERSION:
        stmts = kept["statements"]
        print(f"statements: {len(stmts)} from {stmts_path.name}", flush=True)
    else:
        bench.drop_analysis(src)  # the app never analyzes; a leftover would change the plans
        stmts = await capture_statements(src, (src.parent / "datasets" / src.stem).resolve(), n)
        stmts_path.write_text(json.dumps({"version": STATEMENTS_VERSION, "statements": stmts}, indent=1))
        print(f"statements: captured {len(stmts)} -> {stmts_path.name}", flush=True)

    report = {
        "started": datetime.now().isoformat(timespec="seconds"), "machine": bench.machine_info(),
        "source": src.name, "seed": seed, "n": n, "cold": bool(args.cold),
        "reps": args.reps, "cold_reps": args.cold_reps, "statements": len(stmts), "layouts": {},
        # What the app pins today, so a reader can tell "default" from "as the app runs it".
        "app_hints": {s["label"]: s["app_hint"] for s in stmts if s.get("app_hint")},
    }
    out = Path(args.json) if args.json else None
    print(f"# Layout experiment on {src.name} (N={n:,}), SQLite {sqlite3.sqlite_version}\n", flush=True)

    # Every build first, then every measurement: see the module docstring.
    paths: dict[str, Path] = {}
    built = False
    for layout in layouts:
        print(f"--- build {layout} ---", flush=True)
        paths[layout], build = build_layout(src, layout, args.rebuild)
        built = built or bool(build) and not build.get("reused")
        bench.drop_analysis(paths[layout])
        data = {"file": paths[layout].name, "build": build, **layout_info(paths[layout], args.dbstat)}
        content_sql, _ = statement_for(CONTENT, layout, None)
        with contextlib.closing(sqlite3.connect(paths[layout])) as con:
            rows, checksum = _checksum(con, content_sql, ())
        data["content"] = {**{k: CONTENT[k] for k in ("group", "label")}, "index": "default",
                           "status": "ok", "rows": rows, "checksum": checksum}
        report["layouts"][layout] = data
    report["mismatches"] = cross_check(report["layouts"])
    if report["mismatches"] or args.build_only:
        if out:
            write_json(report, out)
        return report  # layouts that hold different data have no timings worth taking

    if built and args.settle > 0:
        print(f"settling for {args.settle:.0f}s after the builds", flush=True)
        await asyncio.sleep(args.settle)
    report["settled_s"] = args.settle if built else None
    evictions = bench.Evictions() if args.cold else None
    for layout in layouts:
        print(f"--- measure {layout} ---", flush=True)
        report["layouts"][layout]["results"] = measure_layout(
            paths[layout], layout, stmts, args.reps, args.cold_reps, evictions)
        if evictions is not None:
            report["evictions"] = {"tried": evictions.tried, "failed": evictions.failed}
        if out:  # after every layout: an hours-long run keeps what it has finished
            write_json(report, out)
    report["mismatches"] = cross_check(report["layouts"])
    if out:
        write_json(report, out)
    return report


def write_json(report: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))


# --------------------------------------------------------------------------- reporting
def _ms(result: dict | None, field: str) -> str:
    if result is None:
        return "-"
    if result["status"] != "ok":
        return "skipped" if result["status"] == "skipped" else "n/a"
    return bench._f(result.get(field))


def print_markdown(report: dict) -> None:
    layouts = report["layouts"]
    names = list(layouts)
    print(f"\n## {report['source']}, N={report['n']:,}\n")
    print("| Layout | File GB | Big columns at | Columns | Rows per leaf page | Build s |")
    print("|---|---|---|---|---|---|")
    for name, d in layouts.items():
        at = ", ".join(str(v) for v in d["big_columns_at"].values()) or "moved out"
        build = ", ".join(f"{k[:-2]} {v:,.0f}" for k, v in d["build"].items() if k.endswith("_s"))
        build = build or ("reused" if d["build"].get("reused") else "-")
        print(f"| {name} | {d['db_bytes'] / 2**30:.2f} | {at} | {d['columns']} | "
              f"{bench._f(d.get('rows_per_leaf'), 2)} | {build} |")
    ev = report.get("evictions")
    if ev:
        print(f"\nCold cache: {ev['tried'] - ev['failed']} of {ev['tried']} evictions succeeded.")

    if any("results" not in d for d in layouts.values()):
        print("\nNothing was timed: " + ("the layouts hold different data.\n" if report["mismatches"]
                                         else "a build-only run.\n"))
        for line in report["mismatches"]:
            print(f"- {line}")
        return

    by_key: dict[tuple, dict[str, dict]] = {}
    for name, d in layouts.items():
        for r in d["results"]:
            by_key.setdefault((r["group"], r["label"], r["index"]), {})[name] = r
    passes = [("warm_ms", f"warm, best of {report['reps']}")]
    if report["cold"]:
        passes.append(("cold_ms", f"cold, best of {report['cold_reps']}"))
    for group in (*STATS_GROUPS, "gallery", "similarity"):
        keys = [k for k in by_key if k[0] == group]
        if not keys:
            continue
        for field, title in passes:
            print(f"\n### {group}, {title}, ms\n")
            print("| Statement | Index | " + " | ".join(names) + " |")
            print("|---|---|" + "---|" * len(names))
            for key in keys:
                cells = " | ".join(_ms(by_key[key].get(name), field) for name in names)
                print(f"| {key[1]} | {key[2]} | {cells} |")

    print("\n### Plans under SQLite's own choice\n")
    print("| Statement | Layout | Plan |")
    print("|---|---|---|")
    for key, per_layout in by_key.items():
        if key[2] != "default":
            continue
        for name, r in per_layout.items():
            plan = "; ".join(r.get("plan") or [r.get("note") or r["status"]]).replace("|", "\\|")
            print(f"| {key[1]} | {name} | `{plan}` |")

    if report.get("app_hints"):
        print("\n### Hinted by the app itself\n")
        print("These statements carry an index hint in the app. It was taken off for the "
              "\"default\" rows above, which are SQLite's own choice.\n")
        for label, hint in report["app_hints"].items():
            print(f"- {label}: `{hint}`")

    skipped = sorted({(r["label"], name, r.get("note") or r["status"])
                      for name, d in layouts.items() for r in d["results"] if r["status"] != "ok"})
    if skipped:
        print("\n### Not run\n")
        for label, name, why in skipped:
            print(f"- {label} on {name}: {why}")
    print()
    if report["mismatches"]:
        print("### MISMATCHES: these timings do not compare\n")
        for line in report["mismatches"]:
            print(f"- {line}")
    else:
        print("Every statement returned the same rows on every layout and under every index.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Row-layout experiment (M9 stage 7)")
    ap.add_argument("--db", required=True, help="a seeded file kept by bench_scaling.py")
    ap.add_argument("--layouts", default=",".join(LAYOUTS),
                    help=f"comma-separated layouts (default {','.join(LAYOUTS)})")
    ap.add_argument("--reps", type=int, default=3, help="timed warm repeats; the best is reported")
    ap.add_argument("--cold", action="store_true",
                    help="also time each statement with the file evicted from the OS cache")
    ap.add_argument("--cold-reps", type=int, default=1, help="timed cold repeats (default 1)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild every layout copy, even a matching one")
    ap.add_argument("--build-only", action="store_true",
                    help="build and check the layout copies, and time nothing")
    ap.add_argument("--settle", type=float, default=300.0,
                    help="seconds to wait between the builds and the first measurement, when "
                         "this run built a copy (default 300; a run that reused every copy does not wait)")
    ap.add_argument("--statements", help="where the captured statements are kept "
                                         "(default <db stem>.stage7-stmts.json beside the file)")
    ap.add_argument("--recapture", action="store_true", help="capture the statements again")
    ap.add_argument("--no-dbstat", dest="dbstat", action="store_false",
                    help="skip the rows-per-leaf-page count (it reads the whole images table)")
    ap.add_argument("--json", help="write every result to this JSON file")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report = asyncio.run(run(args))
    print_markdown(report)
    if args.json:
        print(f"results written to {args.json}")
    if report["mismatches"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
