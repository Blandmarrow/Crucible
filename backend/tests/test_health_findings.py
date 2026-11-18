"""The health report's findings (M12 phase 1): each fires, stays quiet, and honours its
threshold.

The SQL-backed findings go through `GET /datasets/{id}/health` against seeded rows. The
ones whose inputs are awkward to seed at scale (aspect-ratio spread, tag frequencies)
are tested on `_build_findings` / `_aggregate_captions` directly with synthetic input.

`test_every_filter_spec_selects_exactly_its_count` is the contract the Health page's
"Show images" rests on: a finding's `filter_spec`, sent to `GET /images/count`, must
match the finding's own count.
"""
from types import SimpleNamespace

from backend.models import Dataset, Image
from backend.schemas.health import FilterSpec
from backend.schemas.image import ImageFilterParams
from backend.services import health_service
from backend.services.health_service import (
    CLIP_TOKEN_LIMIT,
    DEFAULT_TARGET_SHORT_SIDE,
    SMALL_DATASET,
    _aggregate_captions,
    _build_findings,
)
from backend.services.threshold_service import DEFAULTS
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image

LONG_CAPTION = " ".join(f"word{i}" for i in range(120))


async def _dataset(env, n: int, *, goal: str = "a test goal", **fields) -> tuple[str, list[str]]:
    ds = await env.create_dataset(f"health-{n}-{goal[:8]}")
    if goal or fields:
        r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": goal, **fields})
        assert r.status_code == 200, r.text
    ids = [
        (await upload_image(env, ds["id"], f"img{i}.png", png_bytes((i * 7 % 255, i * 13 % 255, 40))))["id"]
        for i in range(n)
    ]
    return ds["id"], ids


async def _patch(env, ids: list[str], **values) -> None:
    async with env.Session() as db:
        for image_id in ids:
            row = await db.get(Image, image_id)
            for k, v in values.items():
                setattr(row, k, v)
        await db.commit()


async def _report(env, ds_id: str) -> dict:
    r = await env.client.get(f"{API}/datasets/{ds_id}/health")
    assert r.status_code == 200, r.text
    return r.json()


async def _findings(env, ds_id: str) -> dict[str, dict]:
    return {f["id"]: f for f in (await _report(env, ds_id))["findings"]}


def test_filter_spec_fields_exist_on_image_filter_params():
    """A FilterSpec is sent to `GET /images/` as is, so every field must be a real
    filter there; an unknown one would be silently ignored and show every image."""
    assert set(FilterSpec.model_fields) <= set(ImageFilterParams.model_fields)


def test_missing_dataset_is_404(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            assert (await env.client.get(f"{API}/datasets/nope/health")).status_code == 404

    run(scenario())


def test_empty_dataset_reports_only_the_goal(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _ = await _dataset(env, 0, goal="")
            assert set(await _findings(env, ds_id)) == {"no_goal"}
            await env.client.patch(f"{API}/datasets/{ds_id}", json={"goal": "style LoRA"})
            report = await _report(env, ds_id)
            assert report["findings"] == []
            assert report["summary"] == {"error": 0, "warn": 0, "info": 0}

    run(scenario())


def test_fresh_upload_findings_and_their_quiet_side(tmp_path):
    """Uploaded, untouched images: uncaptioned, unscored, unlicensed, small, and below
    the default target. Then fix each and see it go."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 3)
            f = await _findings(env, ds_id)
            assert f["uncaptioned"]["count"] == 3 and f["uncaptioned"]["severity"] == "warn"
            assert f["unscored_aesthetic"]["count"] == 3
            assert f["unscored_technical"]["count"] == 3
            assert f["no_license"]["count"] == 3
            assert f["small_dataset"]["count"] == 3
            assert f["small_dataset"]["numbers"]["min_images"] == SMALL_DATASET
            low = f["low_resolution"]
            assert low["count"] == 3
            assert low["numbers"]["target_short_side"] == DEFAULT_TARGET_SHORT_SIDE
            assert low["numbers"]["target_from"] == "default"
            assert low["numbers"]["smallest_short_side"] == 16
            assert "no_goal" not in f

            await _patch(env, ids, caption_text="a red fox standing in deep snow at dusk",
                         aesthetic_score=5.0, blur_score=200.0, width=1200, height=1600)
            r = await env.client.patch(f"{API}/datasets/{ds_id}", json={"license": "cc0-1.0"})
            assert r.status_code == 200, r.text
            f = await _findings(env, ds_id)
            for gone in ("uncaptioned", "unscored_aesthetic", "unscored_technical", "no_license", "low_resolution"):
                assert gone not in f, gone

    run(scenario())


def test_target_resolution_is_honoured_and_clearable(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 2, target_resolution=512)
            await _patch(env, ids[:1], width=600, height=800)
            f = await _findings(env, ds_id)
            assert f["low_resolution"]["count"] == 1
            assert f["low_resolution"]["numbers"]["target_from"] == "dataset"
            # An explicit null clears it back to the default, where both are below.
            r = await env.client.patch(f"{API}/datasets/{ds_id}", json={"target_resolution": None})
            assert r.json()["target_resolution"] is None
            assert (await _findings(env, ds_id))["low_resolution"]["count"] == 2
            # Out of range is refused, not stored.
            r = await env.client.patch(f"{API}/datasets/{ds_id}", json={"target_resolution": 16})
            assert r.status_code == 422
            # Leaving it out leaves it alone.
            await env.client.patch(f"{API}/datasets/{ds_id}", json={"target_resolution": 700})
            r = await env.client.patch(f"{API}/datasets/{ds_id}", json={"description": "x"})
            assert r.json()["target_resolution"] == 700

    run(scenario())


def test_caption_length_findings(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 3)
            await _patch(env, ids[:1], caption_text="fox")
            await _patch(env, ids[1:2], caption_text=LONG_CAPTION)
            await _patch(env, ids[2:], caption_text="a red fox standing in deep snow at dusk")
            f = await _findings(env, ds_id)
            assert f["short_captions"]["count"] == 1
            assert f["long_captions"]["count"] == 1
            assert f["long_captions"]["numbers"]["token_limit"] == CLIP_TOKEN_LIMIT

    run(scenario())


def test_quality_flags_carry_the_setting_they_were_made_with(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 3)
            await _patch(env, ids[:2], quality_flags={"is_blurry": True})
            await _patch(env, ids[2:], quality_flags={"has_watermark": True})
            f = await _findings(env, ds_id)
            assert f["blurry"]["count"] == 2
            assert f["blurry"]["numbers"]["blur_threshold"] == DEFAULTS["blur_threshold"]
            assert f["watermarked"]["count"] == 1
            assert "noisy" not in f

            r = await env.client.patch(f"{API}/settings/thresholds", json={"blur_threshold": 55.0})
            assert r.status_code == 200, r.text
            assert (await _findings(env, ds_id))["blurry"]["numbers"]["blur_threshold"] == 55.0

    run(scenario())


def test_duplicate_groups_count_roots_not_copies(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 5)
            await _patch(env, ids[1:3], quality_flags={"is_duplicate": True, "duplicate_of": ids[0]})
            await _patch(env, ids[4:], quality_flags={"is_duplicate": True, "duplicate_of": ids[3]})
            dup = (await _findings(env, ds_id))["duplicates"]
            assert dup["unit"] == "groups"
            assert dup["count"] == 2
            assert dup["numbers"]["duplicate_copies"] == 3

    run(scenario())


def test_stale_scores(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 2)
            assert "stale_scores" not in await _findings(env, ds_id)
            await _patch(env, ids[:1], scores_stale=True)
            assert (await _findings(env, ds_id))["stale_scores"]["count"] == 1

    run(scenario())


def test_every_filter_spec_selects_exactly_its_count(tmp_path):
    """A finding's "Show images" must show the images it counted, no more, no fewer."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 6, target_resolution=64)
            await _patch(env, ids, width=100, height=100)
            await _patch(env, ids[:1], caption_text="fox", width=10, height=300)
            await _patch(env, ids[1:2], caption_text=LONG_CAPTION, scores_stale=True)
            await _patch(env, ids[2:3], quality_flags={"is_blurry": True}, aesthetic_score=4.0)
            await _patch(env, ids[3:4], quality_flags={"is_duplicate": True, "duplicate_of": ids[2]})
            # Whitespace only: the `captioned` filter calls this captioned, so the
            # count must too.
            await _patch(env, ids[4:5], caption_text="   ")
            report = await _report(env, ds_id)
            checked = 0
            for f in report["findings"]:
                if f["filter_spec"] is None or f["unit"] != "images":
                    continue
                params = {k: v for k, v in f["filter_spec"].items() if v is not None}
                r = await env.client.get(f"{API}/images/count", params={"dataset_id": ds_id, **params})
                assert r.status_code == 200, r.text
                assert r.json()["count"] == f["count"], f["id"]
                checked += 1
            assert checked >= 6

    run(scenario())


def test_videos_get_no_findings(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, _ = await _dataset(env, 0)
            async with env.Session() as db:
                ds = await db.get(Dataset, ds_id)
                ds.video_count = 3
                await db.commit()
            assert (await _report(env, ds_id))["findings"] == []

    run(scenario())


# --- Pure builders: synthetic input --------------------------------------------


def _counts(**kw):
    base = dict(below_target=0, min_short_side=None, stale=0, dup_groups=0,
                short_captions=0, no_aesthetic=0, no_technical=0, uncaptioned=0, unlicensed=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _build(stats: dict, captions: dict | None = None, **counts):
    ds = SimpleNamespace(goal="x", target_resolution=None)
    thresholds = SimpleNamespace(**DEFAULTS)
    caps = captions or _aggregate_captions([])
    findings = _build_findings(ds, {"image_count": 100, "captioned_count": 100, **stats},
                               _counts(**counts), caps, thresholds, DEFAULT_TARGET_SHORT_SIDE, None)
    return {f.id: f for f in findings}


def test_aspect_ratio_spread():
    spread = _build({"aspect_ratio_fine": {"1:1": 94, "16:9": 3, "21:9+": 3}})
    f = spread["aspect_ratio_spread"]
    assert f.count == 6
    assert f.numbers["sparse_buckets"] == {"16:9": 3, "21:9+": 3}
    assert f.filter_spec is None
    # Two healthy buckets, no stragglers.
    assert "aspect_ratio_spread" not in _build({"aspect_ratio_fine": {"1:1": 50, "3:4": 50}})
    # Too few stragglers to matter.
    assert "aspect_ratio_spread" not in _build({"aspect_ratio_fine": {"1:1": 98, "16:9": 2}})


def test_tag_findings():
    caps = (
        [f"zxc, fox, snow, tag{i}" for i in range(30)]
        + ["zxc, fox, forest" for _ in range(5)]
        + ["A photograph of a fox walking through the snow at dusk."] * 2
    )
    agg = _aggregate_captions(caps)
    assert agg["tag_like"] == 35 and agg["prose"] == 2
    # A tie, broken by the tag: the same answer under every hash seed.
    assert [e["tag"] for e in agg["everywhere"]] == ["fox", "zxc"]
    f = _build({}, agg)
    assert f["tag_everywhere"].count == 35 and f["tag_everywhere"].total == 35
    # `search` is a substring match, so no filter beats one that over-selects.
    assert f["tag_everywhere"].filter_spec is None
    assert f["tags_once"].unit == "tags" and f["tags_once"].count == 30
    # 2 prose of 37 is under the mixed threshold.
    assert "mixed_caption_styles" not in f


def test_caption_pass_is_independent_of_input_and_set_order():
    """`freq.update(set)` counts in hash-seed order; ties must not depend on it."""
    caps = ["b, a, c", "c, a, b", "a, b, c"] * 3 + ["d, e"]  # 9 of 10 carry a, b and c
    forward, backward = _aggregate_captions(caps), _aggregate_captions(caps[::-1])
    assert forward == backward
    assert [e["tag"] for e in forward["everywhere"]] == ["a", "b", "c"]
    assert _aggregate_captions(["   ", "\t\n"])["prose"] == 0


def test_mixed_caption_styles():
    caps = ["fox, snow, dusk"] * 8 + ["A fox in the snow at dusk, looking back."] * 4
    f = _build({}, _aggregate_captions(caps))
    mixed = f["mixed_caption_styles"]
    assert mixed.count == 4 and mixed.total == 12
    assert "mixed_caption_styles" not in _build({}, _aggregate_captions(["fox, snow, dusk"] * 12))


def test_findings_sort_by_severity():
    f = list(_build({"captioned_count": 50, "license_breakdown": {"": 10}}).values())
    severities = [x.severity for x in f]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warn": 1, "info": 2}[s])


def test_constants_are_named_in_module():
    # The user doc lists these by name; renaming one must be a deliberate doc change.
    for name in ("CLIP_TOKEN_LIMIT", "SHORT_CAPTION_TOKENS", "DEFAULT_TARGET_SHORT_SIDE",
                 "SMALL_DATASET", "TAG_EVERYWHERE_FRACTION", "TAGS_ONCE_MIN_DISTINCT",
                 "MIXED_CAPTIONS_FRACTION", "AR_SPARSE_BUCKET_FRACTION", "AR_SPARSE_MIN_IMAGES",
                 "STYLE_OUTLIER_IQR_K"):
        assert hasattr(health_service, name), name


def test_no_license_survives_the_breakdown_cap(tmp_path):
    """The Stats licence breakdown keeps 20 licences and folds the rest; the
    unlicensed count must not be one of the folded."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds_id, ids = await _dataset(env, 23)
            for i, image_id in enumerate(ids[:22]):
                await _patch(env, [image_id], license=f"other:licence {i:02d}")
            await _patch(env, ids[:2], license="other:licence 00")
            f = await _findings(env, ds_id)
            assert f["no_license"]["count"] == 1
            r = await env.client.get(f"{API}/images/count", params={"dataset_id": ds_id, "license_missing": "true"})
            assert r.json()["count"] == 1

    run(scenario())
