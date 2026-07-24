"""Request-level regression tests for bulk caption find/replace/regex.

Both `/captions/dataset/{id}/find-replace` and `.../bulk-edit` rewrite the `.txt`
sidecar alongside the DB caption. The failure classes pinned here:

- a literal find-replace must touch only matching captions (DB + sidecar), never
  the rest;
- a regex `remove` collapses whitespace and reports skipped rows;
- an **invalid** regex is swallowed by design (`caption_service.py:200-203`
  returns 0) → 200 + no-op, not 400;
- a regex **timeout** raises builtin `TimeoutError`, which the per-item
  `except regex_error` does NOT catch, so it escapes before any sidecar write →
  408 with every caption and sidecar byte-identical.

The timeout tests set `_REGEX_TIMEOUT = 0.0`, which makes `regex_sub_deadline`
raise on its pre-check deadline before touching the engine — deterministic and
CI-safe (no reliance on a catastrophic pattern actually running slowly).
"""
from pathlib import Path

from backend.models.image import Image
from backend.services import caption_service
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


async def _caption(env, image_id: str, text: str) -> None:
    r = await env.client.put(
        f"{API}/captions/image/{image_id}", json={"caption_text": text}
    )
    assert r.status_code == 200, r.text


async def _db_caption(env, image_id: str) -> str:
    async with env.Session() as db:
        return (await db.get(Image, image_id)).caption_text


def _sidecar(env, image_row: dict, dataset: dict) -> Path:
    return Path(dataset["folder_path"]) / "images" / (Path(image_row["filename"]).stem + ".txt")


def test_find_replace_literal_updates_db_and_sidecars_selectively(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            await _caption(env, a["id"], "a red hat")
            await _caption(env, b["id"], "green shoes")

            r = await env.client.post(
                f"{API}/captions/dataset/{ds['id']}/find-replace",
                json={"find": "red", "replace": "blue", "use_regex": False},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"updated": 1}

            assert await _db_caption(env, a["id"]) == "a blue hat"
            assert await _db_caption(env, b["id"]) == "green shoes"
            assert _sidecar(env, a, ds).read_text(encoding="utf-8") == "a blue hat"
            assert _sidecar(env, b, ds).read_text(encoding="utf-8") == "green shoes"

    run(scenario())


def test_bulk_edit_regex_remove_normalizes_and_skips(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            await _caption(env, a["id"], "foo  bar baz")
            await _caption(env, b["id"], "nothing here")

            r = await env.client.post(
                f"{API}/captions/dataset/{ds['id']}/bulk-edit",
                json={"operation": "remove", "text": "ba[rz]", "use_regex": True},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"affected": 1, "skipped": 1}

            assert await _db_caption(env, a["id"]) == "foo"
            assert await _db_caption(env, b["id"]) == "nothing here"
            assert _sidecar(env, a, ds).read_text(encoding="utf-8") == "foo"
            assert _sidecar(env, b, ds).read_text(encoding="utf-8") == "nothing here"

    run(scenario())


def test_find_replace_invalid_regex_is_noop(tmp_path):
    """A compile error is swallowed (`caption_service.py:200-203`), not a 400."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png")
            await _caption(env, a["id"], "unchanged caption")

            r = await env.client.post(
                f"{API}/captions/dataset/{ds['id']}/find-replace",
                json={"find": "(", "replace": "x", "use_regex": True},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"updated": 0}

            assert await _db_caption(env, a["id"]) == "unchanged caption"
            assert _sidecar(env, a, ds).read_text(encoding="utf-8") == "unchanged caption"

    run(scenario())


def test_find_replace_regex_timeout_408_leaves_captions_untouched(tmp_path, monkeypatch):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            b = await upload_image(env, ds["id"], "b.png", png_bytes((2, 2, 2)))
            await _caption(env, a["id"], "a" * 20_000 + "b")
            await _caption(env, b["id"], "safe caption")

            before_a = _sidecar(env, a, ds).read_bytes()
            before_b = _sidecar(env, b, ds).read_bytes()

            monkeypatch.setattr(caption_service, "_REGEX_TIMEOUT", 0.0)
            r = await env.client.post(
                f"{API}/captions/dataset/{ds['id']}/find-replace",
                json={"find": "(a+)+$", "replace": "x", "use_regex": True},
            )
            assert r.status_code == 408, r.text

            assert await _db_caption(env, a["id"]) == "a" * 20_000 + "b"
            assert await _db_caption(env, b["id"]) == "safe caption"
            assert _sidecar(env, a, ds).read_bytes() == before_a
            assert _sidecar(env, b, ds).read_bytes() == before_b

    run(scenario())


def test_bulk_edit_regex_timeout_408(tmp_path, monkeypatch):
    """Same timeout guarantee via the separate `/bulk-edit` code path
    (`caption_service.py:104-143`)."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            a = await upload_image(env, ds["id"], "a.png", png_bytes((1, 1, 1)))
            await _caption(env, a["id"], "a" * 20_000 + "b")
            before_a = _sidecar(env, a, ds).read_bytes()

            monkeypatch.setattr(caption_service, "_REGEX_TIMEOUT", 0.0)
            r = await env.client.post(
                f"{API}/captions/dataset/{ds['id']}/bulk-edit",
                json={"operation": "find_replace", "text": "(a+)+$",
                      "replacement": "x", "use_regex": True},
            )
            assert r.status_code == 408, r.text

            assert await _db_caption(env, a["id"]) == "a" * 20_000 + "b"
            assert _sidecar(env, a, ds).read_bytes() == before_a

    run(scenario())
