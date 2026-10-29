"""`POST /images/batch/move-subfolder` refuses a selection that spans datasets.

The gallery selection crosses split-view panes, so the toolbar's *Move to* can be
handed ids from two datasets. The route took `rows[0]`'s dataset for everything —
its images folder, its filename set and its busy guard — so with `rename_on_move`
the other dataset's files were renamed **into the first dataset's folder** while
their rows kept their own `dataset_id`: a row in B naming a file inside A, which
B's rescan, export and delete would all then get wrong. Which dataset "won" was
whichever row the query happened to return first.

A subfolder is a per-dataset label, so a mixed request has no single meaning. The
route now answers 400 before touching anything, and the toolbar sends one request
per dataset (see `docs/dev/frontend-core.md`).
"""
from pathlib import Path

import pytest

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


def _images_dir(ds: dict) -> Path:
    return Path(ds["folder_path"]) / "images"


async def _row(env, image_id: str) -> Image:
    async with env.Session() as db:
        return await db.get(Image, image_id)


@pytest.mark.parametrize("rename_on_move", [True, False])
def test_mixed_dataset_move_is_refused_and_touches_nothing(tmp_path, rename_on_move):
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("mix-a")
            b = await env.create_dataset("mix-b")
            ia = await upload_image(env, a["id"], "x.png", png_bytes((1, 2, 3)))
            ib = await upload_image(env, b["id"], "y.png", png_bytes((4, 5, 6)))
            before = {i: (await _row(env, i)).file_path for i in (ia["id"], ib["id"])}

            r = await env.client.post(
                f"{API}/images/batch/move-subfolder",
                json={"image_ids": [ia["id"], ib["id"]], "subfolder": "sub",
                      "rename_on_move": rename_on_move},
            )
            assert r.status_code == 400, r.text
            assert "more than one dataset" in r.json()["detail"]

            # Nothing moved: each file is still in its own dataset's folder under
            # its own name, and each row still says so.
            assert sorted(p.name for p in _images_dir(a).iterdir()) == ["x.png"]
            assert sorted(p.name for p in _images_dir(b).iterdir()) == ["y.png"]
            for img_id, path in before.items():
                row = await _row(env, img_id)
                assert row.file_path == path
                assert row.subfolder == ""

    run(scenario())


def test_single_dataset_move_still_renames_in_its_own_folder(tmp_path):
    """The control: one dataset's ids, split per dataset as the toolbar now sends
    them, each land in their own folder."""
    async def scenario():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("solo-a")
            b = await env.create_dataset("solo-b")
            ia = await upload_image(env, a["id"], "x.png", png_bytes((1, 2, 3)))
            ib = await upload_image(env, b["id"], "y.png", png_bytes((4, 5, 6)))

            for ds, img in ((a, ia), (b, ib)):
                r = await env.client.post(
                    f"{API}/images/batch/move-subfolder",
                    json={"image_ids": [img["id"]], "subfolder": "sub", "rename_on_move": True},
                )
                assert r.status_code == 200, r.text
                assert r.json() == {"moved": 1, "failed": 0, "subfolder": "sub"}
                row = await _row(env, img["id"])
                assert row.dataset_id == ds["id"]
                assert row.subfolder == "sub"
                assert Path(row.file_path).parent == _images_dir(ds)
                assert Path(row.file_path).is_file()

    run(scenario())
