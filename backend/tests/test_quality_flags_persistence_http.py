"""Request-level regression tests for the copy-before-mutate JSON write pattern.

`Image.quality_flags` is a plain `JSON` column (no `MutableDict`), so an in-place
mutation of the loaded dict is invisible to SQLAlchemy's equality-based change
detection and the UPDATE is silently skipped. Both endpoints under test here defend
against that by copying the dict, editing the copy, then reassigning the attribute
(`quality.py:613-619`, `caption_service.py:19-32`). Revert either to an in-place
`.pop()`/`[...]=` and these tests must fail: the flag change would not persist.

The assertions always open a *fresh* `env.Session()` after the request returns —
`api_env` uses `expire_on_commit=False`, so an earlier session would serve the
stale identity-map object and mask exactly the bug under test.
"""
from pathlib import Path

from backend.models.image import Image
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _seed_flags(env, image_id: str, flags: dict) -> None:
    """Write quality_flags directly (no generic setter endpoint exists)."""
    async with env.Session() as db:
        img = await db.get(Image, image_id)
        img.quality_flags = flags
        await db.commit()


def test_duplicate_resolve_keep_clears_flags_persistently(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png")
            await _seed_flags(env, img["id"], {
                "is_duplicate": True,
                "duplicate_of": "other-id",
                "is_blurry": True,
            })

            r = await env.client.post(
                f"{API}/quality/duplicates/resolve",
                json={"keep_ids": [img["id"]], "delete_ids": []},
            )
            assert r.status_code == 204, r.text

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                # is_duplicate/duplicate_of popped, everything else preserved.
                assert row.quality_flags == {"is_blurry": True}
                # The kept image was never a delete target — its file survives.
                assert Path(row.file_path).exists()

    run(scenario())


def test_empty_caption_clears_ai_artifact_flag_persistently(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes((3, 3, 3)))

            # Give it a caption and the artifact flag, then blank the caption.
            r = await env.client.put(
                f"{API}/captions/image/{img['id']}",
                json={"caption_text": "some caption with cruft"},
            )
            assert r.status_code == 200, r.text
            await _seed_flags(env, img["id"], {"has_ai_artifacts": True, "is_noisy": True})

            r = await env.client.put(
                f"{API}/captions/image/{img['id']}",
                json={"caption_text": ""},
            )
            assert r.status_code == 200, r.text

            async with env.Session() as db:
                row = await db.get(Image, img["id"])
                assert row.quality_flags.get("has_ai_artifacts") is False
                # An unrelated flag is untouched.
                assert row.quality_flags.get("is_noisy") is True
                sidecar = Path(row.file_path).with_suffix(".txt")
                assert sidecar.read_text(encoding="utf-8") == ""

    run(scenario())
