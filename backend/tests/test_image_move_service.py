"""`services/image_move.move_to_subfolder` and the `gate_rejected` quality flag."""
import pytest
from fastapi import HTTPException

from backend.models.image import Image
from backend.routers.export import _parse_flags
from backend.services import image_move
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image
from backend.utils import ALLOWED_FLAG_KEYS, chunked


async def _row(env, image_id: str) -> Image:
    async with env.Session() as db:
        return await db.get(Image, image_id)


def test_empty_id_list_is_refused():
    async def scenario():
        with pytest.raises(ValueError, match="at least one image id"):
            await image_move.move_to_subfolder(None, [], "sub", rename_on_move=False)

    run(scenario())


def test_commit_false_with_rename_is_refused():
    async def scenario():
        with pytest.raises(ValueError, match="commit=False"):
            await image_move.move_to_subfolder(None, ["x"], "sub", rename_on_move=True, commit=False)

    run(scenario())


def test_unknown_ids_raise_404():
    async def scenario():
        async with api_env(__import__("pathlib").Path(__import__("tempfile").mkdtemp())) as env:
            async with env.Session() as db:
                with pytest.raises(HTTPException) as e:
                    await image_move.move_to_subfolder(db, ["nope"], "sub", rename_on_move=False)
                assert e.value.status_code == 404

    run(scenario())


def test_ids_are_chunked_and_all_move(tmp_path, monkeypatch):
    sizes: list[int] = []

    def tiny(seq, size=10_000):
        sizes.append(len(seq))
        return chunked(seq, 2)

    monkeypatch.setattr(image_move, "chunked", tiny)

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("chunky")
            ids = []
            for n in range(5):
                img = await upload_image(env, ds["id"], f"i{n}.png", png_bytes((n, 2, 3)))
                ids.append(img["id"])
            async with env.Session() as db:
                out = await image_move.move_to_subfolder(db, ids, "bin", rename_on_move=False)
            assert out == {"moved": 5, "failed": 0, "subfolder": "bin"}
            for i in ids:
                assert (await _row(env, i)).subfolder == "bin"

    run(scenario())
    assert len(sizes) >= 2  # the select and the UPDATE both went through chunked


def test_commit_false_stays_uncommitted_until_the_caller_commits(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("staged")
            img = await upload_image(env, ds["id"], "a.png", png_bytes((1, 2, 3)))
            async with env.Session() as db:
                out = await image_move.move_to_subfolder(
                    db, [img["id"]], "keep", rename_on_move=False, commit=False,
                )
                assert out["moved"] == 1
                # Another session cannot see the staged change yet.
                assert (await _row(env, img["id"])).subfolder == ""
                await db.commit()
            assert (await _row(env, img["id"])).subfolder == "keep"

    run(scenario())


def test_gate_rejected_is_an_allowed_flag_for_filter_and_export(tmp_path):
    assert "gate_rejected" in ALLOWED_FLAG_KEYS
    assert _parse_flags("has_watermark,gate_rejected") == ["has_watermark", "gate_rejected"]

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("gate")
            bad = await upload_image(env, ds["id"], "bad.png", png_bytes((1, 2, 3)))
            await upload_image(env, ds["id"], "ok.png", png_bytes((9, 8, 7)))
            async with env.Session() as db:
                row = await db.get(Image, bad["id"])
                flags = dict(row.quality_flags or {})
                flags["gate_rejected"] = True
                flags["gate_reasons"] = ["too blurry"]
                row.quality_flags = flags
                await db.commit()
            r = await env.client.get(
                f"{API}/images/", params={"dataset_id": ds["id"], "quality_flag": "gate_rejected"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            items = body["items"] if isinstance(body, dict) else body
            assert [i["id"] for i in items] == [bad["id"]]

    run(scenario())
