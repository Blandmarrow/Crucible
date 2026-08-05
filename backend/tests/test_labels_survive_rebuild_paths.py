"""Labels survive every path that copies, duplicates, snapshots or restores an image.

`test_video_lineage_mirrors.py` cannot give this coverage: it walks the *columns*
of `Image` against the columns of `VersionImageState`, and a label is not a column
— it is a row in a join table. So a join-table attachment is exactly the shape of
thing that guard is blind to, and the rebuild paths fail as silently for it as
they did for `source_video_id`.

The structural guards come first. They cover the four rebuild paths **they name**
— they are a hardcoded list, not a discovery of every path in the codebase, so a
genuinely new rebuild path is still a review question rather than a test failure.
What they do catch is the label handling being deleted from one of the four, or
from one *branch* of one: `duplicate_dataset` has two, and a whole-function
"calls something label-shaped" check would let Step 2B rot while Step 2A held it
up.

The behavioural round-trips follow, including the two that are properties of
*not* writing code: `batch_move_dataset` needs no label handling at all
(`image_labels` deliberately carries no `dataset_id`, and a move UPDATEs
`Image.dataset_id` in place without changing `Image.id`), and a restore into a
different dataset needs no id remapping (the vocabulary is global).
"""
import ast
import inspect
import textwrap

from sqlalchemy import select

from backend.models import Dataset, ImageLabel
from backend.models.detection import Detection
from backend.models.versioning import VersionImageState
from backend.routers import detection as detection_router
from backend.routers import images as images_router
from backend.routers import lut as lut_router
from backend.routers import upscaling as upscaling_router
from backend.services import dataset_service, version_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image, wait_for_job

LABEL_HELPERS = ("copy_labels", "set_labels", "labels_by_image", "live_label_ids")


def _label_constructs(func) -> list[str]:
    """Every label-shaped construct `func`'s source calls, in source order.

    Read out of the AST rather than by running the path, so these guards fail on
    a machine that cannot decode a video. `ImageLabel` counts because
    `duplicate_dataset`'s snapshot branch builds the join rows itself, and a
    `label_ids=` kwarg counts because that is how `create_snapshot` fills the
    mirror.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: list[str] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        if name in LABEL_HELPERS or name == "ImageLabel":
            found.append(name)
        for kw in n.keywords:
            if kw.arg == "label_ids":
                found.append("label_ids=")
    return found


# Site → the construct that carries labels *there*. Two rows for
# `duplicate_dataset` on purpose: Step 2A copies from the live rows via
# `copy_labels`, Step 2B rebuilds the join rows off the snapshot mirror with
# `ImageLabel(...)`, and each must be named so neither can be deleted behind the
# other.
REBUILD_SITES = {
    "duplicate_dataset (Step 2A, on-disk)": (dataset_service.duplicate_dataset, "copy_labels"),
    "duplicate_dataset (Step 2B, snapshot)": (dataset_service.duplicate_dataset, "ImageLabel"),
    "batch_copy_dataset": (images_router.batch_copy_dataset, "copy_labels"),
    "create_snapshot": (version_service.create_snapshot, "label_ids="),
    "restore_snapshot": (version_service.restore_snapshot, "set_labels"),
}


def test_every_rebuild_path_carries_labels():
    """The structural guard over the four paths named above. Label handling
    deleted from any one of them — or from either branch of `duplicate_dataset` —
    fails here rather than in someone's dataset."""
    for site, (func, construct) in REBUILD_SITES.items():
        assert construct in _label_constructs(func), (
            f"{site} no longer carries labels: {func.__name__} has no {construct}"
        )


# The same-dataset derivative sites, and how many labelled copies each makes.
# `crop` counts twice: the synchronous crop-only tail and the nested
# `_run_crop_upscale` job, which lives inside its source. These are the five sites
# `test_video_lineage_mirrors._score_carriers` excludes from the *score* guard —
# a derivative's pixels are not the scored pixels — and the exclusion was prose
# there with no assertion either way about labels. A label is a fact about the
# picture, so it travels where a score must not.
def _derivative_sites() -> list[tuple[str, object, int]]:
    return [
        ("images.crop", images_router.crop, 2),
        ("lut.run_lut", lut_router.run_lut, 1),
        ("upscaling.run_upscale", upscaling_router.run_upscale, 1),
        ("detection.crop_to_detection", detection_router.crop_to_detection, 1),
    ]


def test_every_same_dataset_derivative_carries_labels():
    """A crop/upscale/LUT/detection-crop copy keeps its parent's labels.

    Walked for the `copy_labels` **call**, not for a kwarg: four of the five sites
    pass the id map positionally, and `_ctor_kwargs`-style kwarg reading skips a
    `**spread` anyway, so a spread form would be invisible to it.
    """
    for name, func, expected in _derivative_sites():
        calls = [c for c in _label_constructs(func) if c == "copy_labels"]
        assert len(calls) == expected, (
            f"{name} makes {expected} same-dataset derivative(s) but calls "
            f"copy_labels {len(calls)} time(s) — a derivative must carry its "
            "parent's labels, exactly as it carries copy_provenance"
        )


def test_the_mirror_column_is_declared_and_diffed():
    cols = {c.key for c in VersionImageState.__table__.columns}
    assert "label_ids" in cols

    diff_cols = {c.key for c in version_service._DIFF_COLS}
    assert "label_ids" in diff_cols, "mirrored but not selected by the diff"
    assert "label_ids" in version_service._DIFF_COMPARE_FIELDS, "selected but never compared"
    # Not heavy: a handful of uuids, not a ComfyUI workflow.
    assert "label_ids" not in version_service._HEAVY_DIFF_FIELDS
    # NOT NULL with a `[]` default, so pre- and post-migration rows do not split
    # on NULL vs [].
    assert VersionImageState.__table__.c.label_ids.nullable is False


def test_image_labels_has_no_dataset_id():
    """Denormalizing `dataset_id` here is what would silently break every
    cross-dataset move — the move never changes `Image.id`, so the join rows
    follow for free only while they do not name a dataset of their own."""
    assert "dataset_id" not in {c.key for c in ImageLabel.__table__.columns}


# ── behavioural ──────────────────────────────────────────────────────────────


async def _label(env, name: str) -> str:
    r = await env.client.post(f"{API}/labels/", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _attach(env, image_ids, label_id):
    r = await env.client.post(
        f"{API}/labels/assign", json={"image_ids": list(image_ids), "add": [label_id]}
    )
    assert r.status_code == 200, r.text


async def _labels_of(env, image_id: str) -> list[str]:
    return sorted((await env.client.get(f"{API}/images/{image_id}")).json()["label_ids"])


# Snapshot and restore go through the service rather than the routes: both
# routes hand the work to the background job queue and answer `{job_id}`, so an
# HTTP round-trip here would only be testing the queue. The diff *is* driven over
# HTTP below, because its id→name resolution is what the route contributes.
async def _snapshot(env, dataset_id: str, name: str) -> str:
    async with env.Session() as db:
        version = await version_service.create_snapshot(db, dataset_id, name, "")
        return version.id


async def _restore(env, dataset_id: str, version_id: str, **body):
    async with env.Session() as db:
        return await version_service.restore_snapshot(
            db, dataset_id, version_id, pre_restore_snapshot=False, **body
        )


def test_snapshot_then_detach_then_restore_brings_it_back(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)

            version = await _snapshot(env, ds["id"], "v1")
            await env.client.post(f"{API}/labels/assign", json={"image_ids": [img], "remove": [fx]})
            assert await _labels_of(env, img) == []

            await _restore(env, ds["id"], version)
            assert await _labels_of(env, img) == [fx]

    run(scenario())


def test_restore_replaces_rather_than_merges(tmp_path):
    """A label added after the snapshot disappears, exactly as a caption edit does."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            reject = await _label(env, "reject")
            await _attach(env, [img], fx)

            version = await _snapshot(env, ds["id"], "v1")
            await _attach(env, [img], reject)
            assert await _labels_of(env, img) == sorted([fx, reject])

            await _restore(env, ds["id"], version)
            assert await _labels_of(env, img) == [fx]

    run(scenario())


def test_restoring_a_snapshot_naming_a_deleted_label_succeeds(tmp_path):
    """There is no FK behind `VersionImageState.label_ids` — a snapshot must
    survive the deletion of what it names, and the restore resolves against the
    live vocabulary rather than raising."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            keep = await _label(env, "keep")
            await _attach(env, [img], fx)
            await _attach(env, [img], keep)

            version = await _snapshot(env, ds["id"], "v1")
            assert (await env.client.delete(f"{API}/labels/{fx}")).status_code == 204

            await _restore(env, ds["id"], version)
            # The deleted concept is honestly dropped; the surviving one comes back.
            assert await _labels_of(env, img) == [keep]

    run(scenario())


def test_a_rename_between_snapshots_produces_no_diff(tmp_path):
    """Ids, not names: the concept did not change, so neither did the image."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)

            a = await _snapshot(env, ds["id"], "v1")
            await env.client.patch(f"{API}/labels/{fx}", json={"name": "effects"})
            b = await _snapshot(env, ds["id"], "v2")

            r = await env.client.get(
                f"{API}/datasets/{ds["id"]}/versions/diff", params={"v1": a, "v2": b}
            )
            assert r.status_code == 200, r.text
            assert r.json()["summary"]["modified"] == 0

    run(scenario())


def test_an_attach_between_snapshots_diffs_by_name(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")

            a = await _snapshot(env, ds["id"], "v1")
            await _attach(env, [img], fx)
            b = await _snapshot(env, ds["id"], "v2")

            diff = (await env.client.get(
                f"{API}/datasets/{ds["id"]}/versions/diff", params={"v1": a, "v2": b}
            )).json()
            assert diff["summary"]["modified"] == 1
            change = diff["modified"][0]["changes"]["label_ids"]
            # Names, not uuids, resolved server-side once per diff.
            assert change == {"from": "", "to": "fx"}

    run(scenario())


# The three cross-dataset round-trips run with `foreign_keys=True`. The harness
# defaults them OFF per connection, and `image_labels` has two real FKs — PM-016's
# exact blind spot: a copy writing a join row for an image id that does not exist
# would pass silently with the pragma off.
def test_copy_to_dataset_carries_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            img = (await upload_image(env, a["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)

            r = await env.client.post(
                f"{API}/images/batch/copy-dataset",
                json={"image_ids": [img], "target_dataset_id": b["id"], "target_subfolder": ""},
            )
            assert r.status_code == 200, r.text

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": b["id"]})).json()
            assert len(listing) == 1
            assert listing[0]["label_ids"] == [fx]
            # The original keeps its own.
            assert await _labels_of(env, img) == [fx]

    run(scenario())


def test_move_to_dataset_keeps_labels_with_no_code_in_that_path(tmp_path):
    """A property to assert, not to remember: `batch_move_dataset` has no label
    handling *because* `image_labels` names no dataset and the move never
    changes `Image.id`."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            img = (await upload_image(env, a["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)

            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [img], "target_dataset_id": b["id"], "target_subfolder": ""},
            )
            assert r.status_code == 200, r.text

            assert await _labels_of(env, img) == [fx]
            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": b["id"]})).json()
            assert [i["id"] for i in listing] == [img]

            # …and the source of that: no label code in the move path at all.
            assert _label_constructs(images_router.batch_move_dataset) == []

    run(scenario())


def test_duplicate_from_disk_carries_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("src")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)

            async with env.Session() as db:
                clone = await dataset_service.duplicate_dataset(
                    db, await db.get(Dataset, ds["id"]), "clone", job_id=None,
                )
            listing = (await env.client.get(
                f"{API}/images/", params={"dataset_id": clone["dataset_id"]}
            )).json()
            assert len(listing) == 1
            assert listing[0]["label_ids"] == [fx]
            assert listing[0]["id"] != img

    run(scenario())


def test_duplicate_from_snapshot_carries_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("src")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)
            version = await _snapshot(env, ds["id"], "v1")

            # Detached after the snapshot: the clone must reflect the *snapshot*,
            # which is the whole point of duplicating from one.
            await env.client.post(f"{API}/labels/assign", json={"image_ids": [img], "remove": [fx]})

            async with env.Session() as db:
                clone = await dataset_service.duplicate_dataset(
                    db, await db.get(Dataset, ds["id"]), "clone",
                    job_id=None, source_version_id=version,
                )
            listing = (await env.client.get(
                f"{API}/images/", params={"dataset_id": clone["dataset_id"]}
            )).json()
            assert len(listing) == 1
            assert listing[0]["label_ids"] == [fx]

    run(scenario())


def test_a_forked_row_gets_the_label_on_the_fork(tmp_path):
    """Pass 0b mints a fresh uuid for a re-created image, so the restore keys on
    `p.img.id` rather than `p.state.image_id` — writing the snapshot's id would
    label an image in another dataset instead.

    Reaching the fork branch takes both halves and neither is incidental:
    `versioning_mode="manual"` is what makes `create_snapshot` record a
    `file_hash` (the branch bails to `skip_recreate` without one), and the image
    has to be **moved to another dataset** rather than deleted — a delete leaves
    the id free, so the restore re-creates the row under its original id and the
    fork never happens.
    """
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            r = await env.client.patch(
                f"{API}/settings/thresholds", json={"versioning_mode": "manual"}
            )
            assert r.status_code == 200, r.text

            src = await env.create_dataset("src")
            other = await env.create_dataset("other")
            img = (await upload_image(env, src["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)
            version = await _snapshot(env, src["id"], "v1")

            r = await env.client.post(
                f"{API}/images/batch/move-dataset",
                json={"image_ids": [img], "target_dataset_id": other["id"], "target_subfolder": ""},
            )
            assert r.status_code == 200, r.text

            await _restore(env, src["id"], version)

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": src["id"]})).json()
            assert len(listing) == 1
            assert listing[0]["id"] != img, "expected a forked row, not the snapshot's id"
            assert listing[0]["label_ids"] == [fx], listing[0]
            # The moved original keeps its own — the fork is a second row, and
            # `set_labels` only deletes for the ids the restore names.
            assert await _labels_of(env, img) == [fx]

    run(scenario())


def test_keep_mode_extras_keep_their_own_labels(tmp_path):
    """`set_labels` deletes only for image ids the restore actually names."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            first = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [first], fx)
            version = await _snapshot(env, ds["id"], "v1")

            extra = (await upload_image(env, ds["id"], "b.png"))["id"]
            reject = await _label(env, "reject")
            await _attach(env, [extra], reject)

            await _restore(env, ds["id"], version, handle_extra_images="keep")

            assert await _labels_of(env, first) == [fx]
            assert await _labels_of(env, extra) == [reject]

    run(scenario())


def test_a_same_dataset_crop_carries_the_parents_labels(tmp_path):
    """The behavioural half of `test_every_same_dataset_derivative_carries_labels`.

    A crop is the same picture at a different framing, so "this image is a reject"
    is still true of it — the same reasoning that makes it carry `copy_provenance`.
    The synchronous new-file crop is the one derivative site that runs in the
    request handler rather than a job.
    """
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes((10, 20, 30), (40, 20)))
            fx = await _label(env, "fx")
            await _attach(env, [img["id"]], fx)

            r = await env.client.post(f"{API}/images/{img['id']}/crop", json={
                "x": 5, "y": 5, "width": 20, "height": 10,
            })
            assert r.status_code == 200, r.text
            crop_id = r.json()["id"]
            assert crop_id != img["id"]
            assert await _labels_of(env, crop_id) == [fx]
            # …and the parent keeps its own.
            assert await _labels_of(env, img["id"]) == [fx]

    run(scenario())


def test_a_detection_crop_job_carries_the_parents_labels(tmp_path):
    """The job-path half. The three job sites (LUT, upscale, detection crop) share
    one shape — accumulate `parent id -> new id` through the loop, drain it with a
    single `copy_labels` before the trailing commit — and this is the one of the
    three that needs no ML model to run."""
    async def scenario():
        async with api_env(tmp_path, foreign_keys=True) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes((10, 20, 30), (40, 20)))
            fx = await _label(env, "fx")
            await _attach(env, [img["id"]], fx)
            async with env.Session() as db:
                db.add(Detection(
                    image_id=img["id"], label="face", bbox=[0.25, 0.25, 0.75, 0.75],
                    score=0.9, model="test", task="detect",
                ))
                await db.commit()

            r = await env.client.post(f"{API}/detection/crop", json={
                "dataset_id": ds["id"], "image_ids": [img["id"]],
            })
            assert r.status_code == 200, r.text
            job = await wait_for_job(env, r.json()["job_id"])
            assert job["status"] == "completed", job

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(listing) == 2, listing
            crop = next(i for i in listing if i["id"] != img["id"])
            assert crop["label_ids"] == [fx], crop

    run(scenario())


def test_the_snapshot_mirror_is_sorted_and_never_null(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            labelled = (await upload_image(env, ds["id"], "a.png"))["id"]
            bare = (await upload_image(env, ds["id"], "b.png"))["id"]
            ids = sorted([await _label(env, "b-label"), await _label(env, "a-label")])
            for lid in ids:
                await _attach(env, [labelled], lid)
            await _snapshot(env, ds["id"], "v1")

            async with env.Session() as db:
                states = (await db.execute(select(VersionImageState))).scalars().all()
                by_image = {s.image_id: s.label_ids for s in states}
                assert by_image[labelled] == ids, "stored unsorted — the diff would see a reorder"
                assert by_image[bare] == [], "an unlabelled image mirrors as [], never None"

    run(scenario())
