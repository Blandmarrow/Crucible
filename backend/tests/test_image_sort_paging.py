"""OFFSET paging under every gallery sort must neither repeat nor skip an image.

Every row below shares one `created_at`, and the score, `sort_order` and lineage
columns are drawn from a tiny pool (with NULLs), so nearly every sort is mostly
ties. `_apply_image_ordering` ends each ordering with `rowid` in the sort's own
direction so the order is total; without a unique last key SQLite returns tied
rows in scan order and a paged walk can show one image twice and another never.

`rowid` rather than `created_at, id` because every index entry already ends
with it, so the tie-breaker costs an indexed sort nothing (PM-036; the plan
itself is guarded in `test_image_sort_plan.py`).
"""
from datetime import datetime
from itertools import groupby

import pytest
from sqlalchemy import text

from backend.models import Image
from backend.routers.images import _ALLOWED_SCORE_FIELDS, _ALLOWED_SORT_FIELDS
from backend.tests.conftest import API, api_env, run

SAME_TIME = datetime(2026, 1, 1, 12, 0, 0)
N = 25


def _seed_rows(dataset_id: str, score_cols: list[str]) -> list[Image]:
    rows = []
    for i in range(N):
        kw = {c: (None, 1.0, 2.0)[(i + j) % 3] for j, c in enumerate(score_cols)}
        rows.append(Image(
            dataset_id=dataset_id,
            # (dataset_id, filename) is unique, so this sort is the one without ties.
            filename=f"img_{i % 4}_{i:03d}.png",
            original_filename=f"img_{i}.png",
            file_path=f"/tmp/{dataset_id}/img_{i:03d}.png",
            created_at=SAME_TIME,
            updated_at=SAME_TIME,
            width=100 * (i % 3),
            height=100,
            file_size_bytes=1000 * (i % 2),
            caption_token_count=i % 3,
            sort_order=None if i % 3 == 0 else i % 2,
            source_timestamp_ms=None if i % 2 else (i % 3) * 100,
            source_shot_index=None if i % 4 else i % 2,
            **kw,
        ))
    return rows


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_paging_is_disjoint_complete_and_tie_ordered_by_rowid(tmp_path, order):
    score_cols = sorted(_ALLOWED_SCORE_FIELDS | {"nsfw_score"})

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            async with env.Session() as db:
                rows = _seed_rows(ds["id"], score_cols)
                db.add_all(rows)
                await db.commit()
                seeded = {r.id: r for r in rows}
                # Read back, not assumed from the seeding order: a unit of work
                # is free to insert a batch in any order it likes.
                rowid = {i: n for i, n in await db.execute(text("SELECT id, rowid FROM images"))}

            for sort in sorted(_ALLOWED_SORT_FIELDS) + ["no_such_field"]:
                params = {"dataset_id": ds["id"], "sort": sort, "order": order}
                paged: list[str] = []
                page = 1
                while True:
                    r = await env.client.get(
                        f"{API}/images/", params={**params, "page": page, "limit": 4})
                    assert r.status_code == 200, r.text
                    batch = [i["id"] for i in r.json()]
                    paged.extend(batch)
                    if len(batch) < 4:
                        break
                    page += 1
                label = f"{sort}/{order}"
                assert len(paged) == len(set(paged)), f"{label}: repeated an image"
                assert set(paged) == set(seeded), f"{label}: skipped an image"

                r = await env.client.get(f"{API}/images/ids", params=params)
                assert r.status_code == 200, r.text
                body = r.json()
                ids = body["ids"] if isinstance(body, dict) else body
                assert ids == paged, f"{label}: /ids differs from the paged order"

                # Within a run of equal sort values the rowids must run in the
                # sort's own direction — the direction is what keeps a
                # descending sort on its index. Custom order ignores `order`
                # and is always ascending. An unknown name is coerced to
                # created_at, where the whole list is one run.
                col = sort if sort in _ALLOWED_SORT_FIELDS else "created_at"
                descending = order == "desc" and col != "sort_order"
                for _, grp in groupby(paged, key=lambda i: getattr(seeded[i], col)):
                    tied = [rowid[i] for i in grp]
                    assert tied == sorted(tied, reverse=descending), (
                        f"{label}: tied rows not in rowid order")

    run(scenario())
