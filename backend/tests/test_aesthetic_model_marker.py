"""`Image.aesthetic_model` — which model produced each stored `aesthetic_score`.

The column exists because two consumers act **destructively** on that score:
`aesthetic_min` omits images at export, and *Keep best* in duplicate resolution
deletes them. LAION and Aesthetic Predictor V2.5 both emit 1–10 and that is the
only thing their scales share, so the moment a second producer became selectable
a dataset could hold two of them in one column with nothing on screen saying so.

Everything here is torch-free, so it runs in CI: the marker is an ordinary
string column, the router writes it beside the score it already writes, and the
one thing that genuinely needs a GPU — V2.5 inference — is verified by hand
(the same class as every other scorer, which is why `quality.spec.ts` never
clicks *Run scoring*).

The invariant under test throughout is the one migration a5e1b7c3d9f0's backfill
buys:

    aesthetic_score IS NOT NULL  <=>  aesthetic_model IS NOT NULL

Five consumers lean on it rather than each re-deriving a three-way rule, and the
last test here pins the arithmetic that follows from it.
"""

import sys
import types

import pytest
from sqlalchemy import select

from backend.models.image import Image
from backend.models.versioning import VersionImageState
from backend.routers import quality as quality_router
from backend.services import version_service
from backend.tests.conftest import (
    API,
    api_env,
    png_bytes,
    run,
    upload_image,
    wait_for_job,
)
from backend.utils import score_columns

@pytest.fixture(autouse=True)
def _no_real_scorers(monkeypatch):
    """Stub the aesthetic scorer and both loaders for every test in this module.

    Autouse rather than per-test, because the hazard is not local: `POST
    /quality/score` enqueues onto a **process-global** job queue, so a job
    created by one test can be picked up while a later test's event loop is
    running. Left real, that pulls a multi-GB CLIP download into a suite that is
    supposed to be torch-free — it hangs on a dev box with torch installed and
    fails the import outright on CI, in both cases far from the test that
    queued it.

    Most of what is under test is the *request* contract (which marker reaches
    `BackgroundJob.config`, which rows the selection query picks) and the read
    surfaces, all of which are decided before any model loads. The one thing
    that is not is the **dispatch**: which of the two loaders actually ran, and
    which handle shape reached the shared loop. So the stubs keep a call log and
    the fixture returns it — an autouse fixture can still be requested by name,
    so only the test that cares takes the parameter. Entries are name-tagged
    tuples, the `test_booru_http.py::_fakes` idiom:

        ("load", "laion" | "v2_5", entry)   — which loader ran, and what it gave back
        ("batch", model, handle)            — what `score_images_batch` received

    The two loaders return **distinct** `_Entry` instances so the handle at the
    batch call identifies its origin, which is what pins the two shapes at
    `quality.py:203`/`:208` (`handle = entry` for V2.5, `handle = entry.model`
    for LAION). Swapping those crashes every real V2.5 run on the first image,
    and V2.5 inference is manual-verify-only, so nothing else would catch it.
    """
    from backend.ml.model_manager import model_manager

    calls: list[tuple] = []

    aesthetic_mod = types.ModuleType("backend.ml.aesthetic_scorer")

    async def score_images_batch(paths, model_handle, job_id=None, model="laion"):
        calls.append(("batch", model, model_handle))
        return [7.0] * len(paths)

    async def score_images_watermark(paths, model_handle, job_id=None, watermark_threshold=0.5):
        return []

    async def extract_clip_embeddings_batch(paths, model_handle, job_id=None):
        return []

    aesthetic_mod.score_images_batch = score_images_batch
    aesthetic_mod.score_images_watermark = score_images_watermark
    aesthetic_mod.extract_clip_embeddings_batch = extract_clip_embeddings_batch
    monkeypatch.setitem(sys.modules, "backend.ml.aesthetic_scorer", aesthetic_mod)

    class _Entry:
        def __init__(self):
            self.model = object()
            self.processor = object()

    laion_entry, v2_5_entry = _Entry(), _Entry()

    def _loader(tag, entry):
        async def _load(*a, **kw):
            calls.append(("load", tag, entry))
            return entry
        return _load

    monkeypatch.setattr(model_manager, "load_aesthetic", _loader("laion", laion_entry))
    monkeypatch.setattr(model_manager, "load_aesthetic_v2_5", _loader("v2_5", v2_5_entry))
    return calls


# ---------------------------------------------------------------------------
# Structural — the column's place in the schema and in the two derived universes
# ---------------------------------------------------------------------------


def test_the_marker_is_nullable_on_both_tables():
    """NULL is load-bearing: it is what "unscored" reads as. A `default=""` — the
    shape `captioned_by` uses — would recreate exactly the scored-but-unknown
    ambiguity the backfill removed."""
    for model in (Image, VersionImageState):
        col = model.__table__.c["aesthetic_model"]
        assert col.nullable, f"{model.__name__}.aesthetic_model must hold 'unscored'"
        assert col.default is None and col.server_default is None


def test_the_marker_is_diffed_as_well_as_mirrored():
    """The mirror itself is forced by `test_video_lineage_mirrors.py`. What that
    guard cannot say is which side of `_DIFF_COLS`' immutable-lineage carve-out
    this falls on: a re-score with a different model flips this column, so it is
    mutable state and belongs in the diff — same answer as `scores_stale`."""
    selected = {c.key for c in version_service._DIFF_COLS}
    assert "aesthetic_model" in selected
    assert "aesthetic_model" in version_service._DIFF_COMPARE_FIELDS


def test_the_marker_is_not_a_score_column():
    """Both universes it must stay out of, pinned together.

    `score_columns(Image)` drives `record_in_place`'s decision to set
    `scores_stale`, and `_JOB_SCORE_COLUMNS` drives the predicate that clears it.
    Both are `*_score`-suffix-derived and correctly exclude a string marker.
    Adding it to either — or to the `refreshed` set the router builds — is inert
    today and a live bug the day anyone re-derives one of them.
    """
    assert "aesthetic_model" not in score_columns(Image)
    assert "aesthetic_model" not in quality_router._JOB_SCORE_COLUMNS


# ---------------------------------------------------------------------------
# The request contract
# ---------------------------------------------------------------------------


def test_the_chosen_model_lands_verbatim_in_the_job_config(tmp_path):
    """`body.model_dump()` carries it onto `BackgroundJob.config`, which is where
    a user later asks which model made these numbers. Read straight back off the
    job row rather than waiting for the job body, which would need a GPU."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"],
                "run_aesthetic": True, "run_technical": False,
                "aesthetic_model": "v2_5",
            })
            assert r.status_code == 200, r.text
            job_id = r.json()["job_id"]
            # Awaited before returning, per the enqueue-and-return hang the
            # `api_env` teardown documents.
            await wait_for_job(env, job_id, timeout=60)

            jobs = (await env.client.get(f"{API}/jobs/?limit=50")).json()
            job = next(j for j in jobs if j["id"] == job_id)
            assert job["config"]["aesthetic_model"] == "v2_5"
            # The auto-label names it too — job history is a durable record of
            # which model produced a dataset's numbers.
            assert "V2.5" in job["label"]

    run(scenario())


def test_the_default_is_laion_and_says_so_in_the_label(tmp_path):
    """The default keeps every existing client byte-identical, and an unmarked
    label still means LAION rather than "unknown"."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": False,
            })
            assert r.status_code == 200, r.text
            await wait_for_job(env, r.json()["job_id"], timeout=60)
            jobs = (await env.client.get(f"{API}/jobs/?limit=50")).json()
            job = next(j for j in jobs if j["id"] == r.json()["job_id"])
            assert job["config"]["aesthetic_model"] == "laion"
            assert "V2.5" not in job["label"]

    run(scenario())


def test_the_marker_decides_which_model_actually_runs(tmp_path, _no_real_scorers):
    """The one test that observes the *dispatch* rather than the request.

    Everything else here derives from `body.aesthetic_model`, so a wrong literal
    at the branch in `_run` — `"v25"` for `"v2_5"`, say — would load LAION,
    score with LAION and stamp `aesthetic_model = "v2_5"` beside it, with the
    whole module green. That is a LAION distribution handed to `rankForKeepBest`,
    which *deletes* images.

    The handle assertions are the second half: the two loaders return different
    entry shapes on purpose, and swapping them is a crash on the first image of
    every real V2.5 run that no torch-free test could otherwise see.
    """
    calls = _no_real_scorers

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            async def score(**extra) -> tuple[tuple, tuple]:
                calls.clear()
                r = await env.client.post(f"{API}/quality/score", json={
                    "dataset_id": ds["id"], "run_aesthetic": True,
                    "run_technical": False, **extra,
                })
                assert r.status_code == 200, r.text
                await wait_for_job(env, r.json()["job_id"], timeout=60)
                loads = [c for c in calls if c[0] == "load"]
                batches = [c for c in calls if c[0] == "batch"]
                assert len(loads) == 1, f"expected exactly one loader call, got {loads}"
                assert len(batches) == 1, f"expected exactly one batch call, got {batches}"
                return loads[0], batches[0]

            load, batch = await score(aesthetic_model="v2_5")
            assert load[1] == "v2_5", "V2.5 was requested and LAION was loaded"
            assert batch[1] == "v2_5", "the loop was told the other model"
            assert batch[2] is load[2], "V2.5 takes the plain entry (.model + .processor)"

            for request in ({}, {"aesthetic_model": "laion"}):
                load, batch = await score(**request)
                assert load[1] == "laion", f"{request or 'the default'} loaded V2.5"
                assert batch[1] == "laion"
                assert batch[2] is load[2].model, "LAION takes the three-tenant dict"

    run(scenario())


def test_an_unknown_model_is_refused(tmp_path):
    """A `Literal`, not a plain `str`: an unknown value has to be a 422 at the
    edge rather than an unknown marker written into the column, where nothing
    downstream could interpret it."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True,
                "aesthetic_model": "waifu-scorer-v3",
            })
            assert r.status_code == 422, r.text
            # …and the rejected value is not echoed back into the response.
            assert "waifu-scorer-v3" not in r.text

    run(scenario())


# ---------------------------------------------------------------------------
# Seeding helper — the marker written directly, the way a real run would
# ---------------------------------------------------------------------------


async def _score(env, image_id: str, score: float, marker: str) -> None:
    """Write the pair the router writes. Direct session writes, because driving
    the real scorers needs torch."""
    async with env.Session() as db:
        row = await db.get(Image, image_id)
        row.aesthetic_score = score
        row.aesthetic_model = marker
        await db.commit()


async def _flag_duplicate(env, dup_id: str, root_id: str) -> None:
    """What `_flag_duplicates` writes: the copies are flagged, the root is not."""
    async with env.Session() as db:
        row = await db.get(Image, dup_id)
        row.quality_flags = {"is_duplicate": True, "duplicate_of": root_id}
        await db.commit()


# ---------------------------------------------------------------------------
# The duplicates payload
# ---------------------------------------------------------------------------


def test_the_duplicates_payload_carries_the_marker_on_members_and_the_root(tmp_path):
    """The root is fetched by a *separate* query and prepended, and that path has
    regressed on its own before (see `get_duplicates`' docstring). A root with no
    marker would make a genuinely mixed group look uniform to the client-side
    guard, which is the one that keeps *Keep best* from deleting across two
    incomparable scales."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root = await upload_image(env, ds["id"], "root.png", png_bytes())
            dup = await upload_image(env, ds["id"], "dup.png", png_bytes((9, 9, 9)))
            await _score(env, root["id"], 6.2, "laion")
            await _score(env, dup["id"], 6.4, "v2_5")
            await _flag_duplicate(env, dup["id"], root["id"])

            r = await env.client.get(f"{API}/quality/duplicates/{ds['id']}")
            assert r.status_code == 200, r.text
            groups = r.json()["groups"]
            assert len(groups) == 1
            by_name = {m["filename"]: m for m in groups[0]}

            assert by_name["root.png"]["kept"] is True
            assert by_name["root.png"]["aesthetic_model"] == "laion"
            assert by_name["dup.png"]["kept"] is False
            assert by_name["dup.png"]["aesthetic_model"] == "v2_5"

    run(scenario())


# ---------------------------------------------------------------------------
# The export preview pair
# ---------------------------------------------------------------------------


def test_the_export_preview_reports_both_models_and_tracks_the_filters(tmp_path):
    """Whole-scope / will-export, the same split the unlicensed and stale-score
    advisories use — and dicts rather than a bool, because the useful sentence is
    "N by LAION, M by V2.5": the skew is what says whether one `aesthetic_min`
    threshold is over- or under-including."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("mix")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes((9, 9, 9)))
            await _score(env, a["id"], 8.0, "laion")
            await _score(env, b["id"], 3.0, "v2_5")

            async def preview(**params):
                r = await env.client.get(f"{API}/export/preview/{ds['id']}", params=params)
                assert r.status_code == 200, r.text
                return r.json()

            p = await preview()
            assert p["aesthetic_models"] == {"laion": 1, "v2_5": 1}
            assert p["aesthetic_models_will_export"] == {"laion": 1, "v2_5": 1}

            # A filter that drops the V2.5 row moves the will-export dict and
            # leaves the whole-scope one alone. Advisory only: no exclusion row
            # of its own, and `will_export` is decided by the filters, not by the
            # marker.
            p = await preview(aesthetic_min=5.0)
            assert p["aesthetic_models"] == {"laion": 1, "v2_5": 1}
            assert p["aesthetic_models_will_export"] == {"laion": 1}
            assert p["will_export"] == 1

    run(scenario())


def test_an_unscored_dataset_reports_empty_dicts(tmp_path):
    """The warning renders on `Object.keys(...).length > 1`, so the keys have to
    be present and empty rather than absent."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            p = (await env.client.get(f"{API}/export/preview/{ds['id']}")).json()
            assert p["aesthetic_models"] == {}
            assert p["aesthetic_models_will_export"] == {}

    run(scenario())


# ---------------------------------------------------------------------------
# Coverage — the two surfaces, and the arithmetic the backfill buys
# ---------------------------------------------------------------------------


def test_per_model_counts_sum_to_the_aesthetic_coverage_figure(tmp_path):
    """The invariant, asserted as arithmetic on both surfaces at once.

    If a row could be scored without a marker there would be a silent remainder
    here, and every "N by LAION, M by V2.5" sentence in the UI would quietly fail
    to add up to the coverage figure printed beside it.
    """
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            b = await upload_image(env, ds["id"], "b.png", png_bytes((9, 9, 9)))
            c = await upload_image(env, ds["id"], "c.png", png_bytes((3, 3, 3)))
            await _score(env, a["id"], 8.0, "laion")
            await _score(env, b["id"], 3.0, "v2_5")
            await _score(env, c["id"], 5.0, "v2_5")

            stats = (await env.client.get(f"{API}/datasets/{ds['id']}/stats")).json()
            cov = stats["score_coverage"]
            assert cov["aesthetic"] == 3
            assert cov["aesthetic_laion"] == 1
            assert cov["aesthetic_v2_5"] == 2
            per_model = {k: v for k, v in cov.items() if k.startswith("aesthetic_")}
            assert sum(per_model.values()) == cov["aesthetic"]

            # The Score page's own endpoint, which answers the same question in
            # its own subfolder scope.
            r = await env.client.get(f"{API}/quality/aesthetic-coverage/{ds['id']}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body == {"scored": 3, "unscored": 0, "by_model": {"laion": 1, "v2_5": 2}}
            assert sum(body["by_model"].values()) == body["scored"]

    run(scenario())


def test_the_coverage_endpoint_honours_the_subfolder_scope(tmp_path):
    """It exists *because* it takes the page's scope — that is the whole reason
    it is not a field on the dataset stats aggregation."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            root = await upload_image(env, ds["id"], "root.png", png_bytes())
            r = await env.client.post(
                f"{API}/images/upload",
                params={"dataset_id": ds["id"], "subfolder": "sub"},
                files=[("files", ("nested.png", png_bytes((9, 9, 9)), "image/png"))],
            )
            assert r.status_code == 201, r.text
            listing = (await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"]}
            )).json()
            nested_id = next(i for i in listing if i["filename"] == "nested.png")["id"]
            await _score(env, root["id"], 8.0, "laion")
            await _score(env, nested_id, 3.0, "v2_5")

            whole = (await env.client.get(f"{API}/quality/aesthetic-coverage/{ds['id']}")).json()
            assert whole["by_model"] == {"laion": 1, "v2_5": 1}

            scoped = (await env.client.get(
                f"{API}/quality/aesthetic-coverage/{ds['id']}", params={"subfolder": "sub"}
            )).json()
            assert scoped == {"scored": 1, "unscored": 0, "by_model": {"v2_5": 1}}

    run(scenario())


# ---------------------------------------------------------------------------
# The re-score offer's scoping flag
# ---------------------------------------------------------------------------


def test_only_mismatched_selects_rows_another_model_scored(tmp_path):
    """A scoping flag rather than an id round-trip, so the selection is atomic
    with the run. It deliberately excludes never-scored rows: plain *Run scoring*
    already covers those, and two buttons with two literal meanings beat one with
    a mode."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            laion = await upload_image(env, ds["id"], "laion.png", png_bytes())
            v25 = await upload_image(env, ds["id"], "v25.png", png_bytes((9, 9, 9)))
            await upload_image(env, ds["id"], "unscored.png", png_bytes((3, 3, 3)))
            await _score(env, laion["id"], 8.0, "laion")
            await _score(env, v25["id"], 3.0, "v2_5")

            async def rescore(model: str) -> int:
                r = await env.client.post(f"{API}/quality/score", json={
                    "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": False,
                    "aesthetic_model": model, "only_mismatched": True,
                })
                assert r.status_code == 200, r.text
                await wait_for_job(env, r.json()["job_id"], timeout=60)
                return r.json()["total"]

            async def markers() -> dict[str, str | None]:
                async with env.Session() as db:
                    return {
                        row.filename: row.aesthetic_model
                        for row in (await db.execute(select(Image))).scalars()
                    }

            # Targeting v2_5 selects the one LAION row — not the unscored one,
            # which plain *Run scoring* is for.
            assert await rescore("v2_5") == 1
            assert await markers() == {
                "laion.png": "v2_5", "v25.png": "v2_5", "unscored.png": None,
            }

            # Targeting LAION now finds *both*, which is the same rule read the
            # other way and the reason the count is a live query rather than a
            # number the client remembers. The round trip also proves the marker
            # is written by the run, not merely accepted by the request.
            assert await rescore("laion") == 2
            assert await markers() == {
                "laion.png": "laion", "v25.png": "laion", "unscored.png": None,
            }

    run(scenario())


def test_only_mismatched_on_a_uniform_dataset_matches_nothing(tmp_path):
    """`{"job_id": None}` rather than a job over zero images — the ordinary
    answer once a dataset has been brought onto one model, and what the button's
    own count already predicts."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes())
            await _score(env, a["id"], 8.0, "laion")

            r = await env.client.post(f"{API}/quality/score", json={
                "dataset_id": ds["id"], "run_aesthetic": True, "run_technical": False,
                "aesthetic_model": "laion", "only_mismatched": True,
            })
            assert r.status_code == 200, r.text
            assert r.json()["job_id"] is None

    run(scenario())


# ---------------------------------------------------------------------------
# The snapshot round-trip
# ---------------------------------------------------------------------------


def test_a_snapshot_carries_the_marker_beside_the_score(tmp_path):
    """The behavioural half of the mirror guard. Without it a restore writes a
    snapshot's LAION score onto a row whose marker still reads "v2_5" — the score
    and its terms would disagree, silently disarming a guard that gates image
    *deletion*."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())
            await _score(env, img["id"], 6.2, "laion")

            await env.client.patch(f"{API}/settings/thresholds", json={"versioning_mode": "manual"})
            r = await env.client.post(f"{API}/datasets/{ds['id']}/versions", json={"name": "v1"})
            assert r.status_code in (200, 201, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                state = (await db.execute(
                    select(VersionImageState).where(VersionImageState.image_id == img["id"])
                )).scalar_one()
                assert state.aesthetic_score == 6.2
                assert state.aesthetic_model == "laion"

            # A re-score with the other model, then a restore.
            await _score(env, img["id"], 3.0, "v2_5")
            version_id = (await env.client.get(f"{API}/datasets/{ds['id']}/versions")).json()[0]["id"]
            r = await env.client.post(
                f"{API}/datasets/{ds['id']}/versions/{version_id}/restore", json={}
            )
            assert r.status_code in (200, 202), r.text
            if "job_id" in r.json():
                await wait_for_job(env, r.json()["job_id"], timeout=60)

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                await db.refresh(row)
            assert row.aesthetic_score == 6.2
            assert row.aesthetic_model == "laion", "the score came back under the wrong terms"

    run(scenario())
