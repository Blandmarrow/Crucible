"""The health report's cache, its card-badge write-back, and the delta against the
current branch head's snapshot (M12 phase 1)."""
from unittest.mock import patch

from sqlalchemy import select

from backend.models import Dataset, DatasetVersion, Image
from backend.services import dataset_service, health_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job


async def _seeded(env, n: int = 2) -> tuple[str, list[str]]:
    ds = await env.create_dataset("cache")
    await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": "style LoRA"})
    ids = [(await upload_image(env, ds["id"], f"{i}.png", png_bytes((i * 40, 10, 10))))["id"] for i in range(n)]
    return ds["id"], ids


async def _health(env, ds_id: str) -> dict:
    r = await env.client.get(f"{API}/datasets/{ds_id}/health")
    assert r.status_code == 200, r.text
    return r.json()


async def _snapshot(env, ds_id: str, name: str) -> None:
    r = await env.client.post(f"{API}/datasets/{ds_id}/versions", json={"name": name})
    assert r.status_code in (200, 201, 202), r.text
    if "job_id" in r.json():
        job = await wait_for_job(env, r.json()["job_id"], timeout=60)
        assert job["status"] == "completed", job


def _counting_builds():
    """Patch `_build_report` to count how often the report is really built."""
    real = health_service._build_report
    calls = {"n": 0}

    async def counted(*a, **kw):
        calls["n"] += 1
        return await real(*a, **kw)

    return calls, patch.object(health_service, "_build_report", counted)


def test_cache_hits_until_an_image_or_a_dismissal_changes(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _seeded(env)
            dataset_service._stats_cache.clear()
            calls, patcher = _counting_builds()
            with patcher:
                await _health(env, ds_id)
                await _health(env, ds_id)
                assert calls["n"] == 1, "the second read must come from the cache"

                async with env.Session() as db:
                    (await db.get(Image, ids[0])).caption_text = "a fox"
                    await db.commit()
                report = await _health(env, ds_id)
                assert calls["n"] == 2, "an image edit must invalidate the report"
                assert {f["id"] for f in report["findings"]} >= {"uncaptioned"}
                assert next(f for f in report["findings"] if f["id"] == "uncaptioned")["count"] == 1

                r = await env.client.post(f"{API}/datasets/{ds_id}/health/dismiss",
                                          json={"finding_id": "small_dataset", "reason": "a test set"})
                assert r.status_code == 201, r.text
                report = await _health(env, ds_id)
                assert calls["n"] == 3, "a dismissal must invalidate the report"
                assert "small_dataset" in {f["id"] for f in report["dismissed"]}

                # Removing the dismissal brings it back.
                await env.client.delete(f"{API}/datasets/{ds_id}/notes/{r.json()['id']}")
                report = await _health(env, ds_id)
                assert calls["n"] == 4
                assert "small_dataset" in {f["id"] for f in report["findings"]}

    run(scenario())


def test_card_summary_is_written_without_touching_updated_at(tmp_path):
    """`updated_at` is part of the Stats and health validators: a write-back that bumped
    it would evict both caches on every read."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _ = await _seeded(env)
            async with env.Session() as db:
                before = (await db.get(Dataset, ds_id)).updated_at
            report = await _health(env, ds_id)
            async with env.Session() as db:
                ds = await db.get(Dataset, ds_id)
                assert ds.updated_at == before
                assert {k: ds.health_summary[k] for k in ("error", "warn", "info")} == report["summary"]
                assert ds.health_summary["computed_at"]

            calls, patcher = _counting_builds()
            with patcher:
                await _health(env, ds_id)
            assert calls["n"] == 0, "the write-back evicted the cache"

            listed = (await env.client.get(f"{API}/datasets/")).json()
            assert next(d for d in listed if d["id"] == ds_id)["health_summary"]["warn"] == report["summary"]["warn"]

    run(scenario())


def test_snapshot_delta_against_the_branch_head(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _seeded(env, 3)
            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})

            assert (await _health(env, ds_id))["baseline"] is None

            async with env.Session() as db:
                (await db.get(Image, ids[0])).caption_text = "a fox in the snow at dusk"
                await db.commit()
            await _snapshot(env, ds_id, "v1")
            async with env.Session() as db:
                v = (await db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id))).scalar_one()
                summary = {i["id"]: i["count"] for i in v.findings_summary}
            assert summary["uncaptioned"] == 2

            async with env.Session() as db:
                for image_id in ids:
                    (await db.get(Image, image_id)).caption_text = "a fox in the snow at dusk"
                (await db.get(Image, ids[1])).scores_stale = True
                await db.commit()
            report = await _health(env, ds_id)
            assert report["baseline"]["version_name"] == "v1"
            assert report["baseline"]["missing"] is False
            stale = next(f for f in report["findings"] if f["id"] == "stale_scores")
            assert stale["was"] == 0
            unscored = next(f for f in report["findings"] if f["id"] == "unscored_aesthetic")
            assert unscored["was"] == 3
            assert {r["id"]: r["was"] for r in report["resolved"]}["uncaptioned"] == 2

    run(scenario())


def test_old_snapshot_reads_as_no_baseline_and_a_failing_report_never_fails_a_snapshot(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _ = await _seeded(env)
            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})

            async def boom(*a, **kw):
                raise RuntimeError("report failed")

            with patch.object(health_service, "summary_for_snapshot", boom):
                await _snapshot(env, ds_id, "v1")
            async with env.Session() as db:
                v = (await db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id))).scalar_one()
                assert v.findings_summary is None
            report = await _health(env, ds_id)
            assert report["baseline"]["version_name"] == "v1"
            assert report["baseline"]["missing"] is True
            assert all(f["was"] is None for f in report["findings"])
            assert report["resolved"] == []

    run(scenario())


def test_a_failed_badge_write_never_fails_the_report(tmp_path):
    """A write lock held elsewhere (a manual snapshot job) must not turn a built
    report into a 500: the write-back is best effort."""
    from sqlalchemy.exc import OperationalError

    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _ = await _seeded(env)

            async def locked(*a, **kw):
                raise OperationalError("UPDATE datasets", {}, Exception("database is locked"))

            with patch.object(health_service, "_write_summary", locked):
                report = await _health(env, ds_id)
            assert report["findings"]
            async with env.Session() as db:
                assert (await db.get(Dataset, ds_id)).health_summary is None

    run(scenario())
