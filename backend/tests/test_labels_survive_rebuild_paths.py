"""Labels survive every path that copies, duplicates, snapshots or restores an image.

`test_video_lineage_mirrors.py` cannot give this coverage: it walks the *columns*
of `Image` against the columns of `VersionImageState`, and a label is not a column
— it is a row in a join table. So a join-table attachment is exactly the shape of
thing that guard is blind to, and the rebuild paths fail as silently for it as
they did for `source_video_id`.

The structural guards come first, because they are the ones that fail for the
*next* rebuild path somebody adds. The behavioural round-trips follow, including
the two that are properties of *not* writing code: `batch_move_dataset` needs no
label handling at all (`image_labels` deliberately carries no `dataset_id`, and
a move UPDATEs `Image.dataset_id` in place without changing `Image.id`), and a
restore into a different dataset needs no id remapping (the vocabulary is
global).
"""
import ast
import inspect
import textwrap

from sqlalchemy import select

from backend.models import Dataset, ImageLabel
from backend.models.versioning import VersionImageState
from backend.routers import images as images_router
from backend.services import dataset_service, version_service
from backend.tests.conftest import API, api_env, run, upload_image

LABEL_HELPERS = ("copy_labels", "set_labels", "labels_by_image")


def _calls_a_label_helper(func) -> bool:
    """True if `func`'s source calls one of `label_service`'s helpers, or carries
    a `label_ids=` kwarg / `ImageLabel(...)` construction of its own.

    Read out of the AST rather than by running the path, so this fails for the
    next rebuild path even on a machine that cannot decode a video.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name in LABEL_HELPERS or name == "ImageLabel":
                return True
            for kw in n.keywords:
                if kw.arg == "label_ids":
                    return True
    return False


def test_every_rebuild_path_carries_labels():
    """The structural guard. A new path that rebuilds images without touching
    labels fails here rather than in someone's dataset."""
    for func in (
        dataset_service.duplicate_dataset,
        images_router.batch_copy_dataset,
        version_service.create_snapshot,
        version_service.restore_snapshot,
    ):
        assert _calls_a_label_helper(func), f"{func.__name__} does not carry labels"


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


def test_copy_to_dataset_carries_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
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
            assert not _calls_a_label_helper(images_router.batch_move_dataset)

    run(scenario())


def test_duplicate_from_disk_carries_labels(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
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
        async with api_env(tmp_path) as env:
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
    """Pass 0b can mint a fresh uuid for a re-created image, so the restore keys
    on `p.img.id` rather than `p.state.image_id` — writing the snapshot's id
    would label nothing at all."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = (await upload_image(env, ds["id"], "a.png"))["id"]
            fx = await _label(env, "fx")
            await _attach(env, [img], fx)
            version = await _snapshot(env, ds["id"], "v1")

            assert (await env.client.delete(f"{API}/images/{img}")).status_code == 204
            await _restore(env, ds["id"], version)

            listing = (await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})).json()
            assert len(listing) == 1
            assert listing[0]["label_ids"] == [fx], listing[0]

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
