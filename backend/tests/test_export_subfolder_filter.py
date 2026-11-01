"""Limiting an export to subfolders: the preview and the export must agree.

The preview used to receive this list as comma-joined text, with blanks dropped and
an empty result read as "no filter". So it counted the whole dataset for a `(root)`
tick, lost `(root)` beside another folder, split a folder name holding a comma, and
promised the whole dataset when nothing was ticked and the export wrote nothing.

Every case therefore drives both halves over HTTP, and compares the preview's count
with the files a real export writes. The match stays **exact** on purpose: `a` does
not include `a/b`. Ticking a parent is the page's job, which sends each folder.
"""
import json

from backend.models import Image
from backend.tests.conftest import API, api_env, run, wait_for_job

FOLDERS = {"root.png": "", "b.png": "a/b", "c.png": "a/c", "comma.png": "with,comma"}


async def _seed(env, dataset_id: str) -> None:
    ds_dir = env.datasets_dir / dataset_id
    (ds_dir / "images").mkdir(parents=True, exist_ok=True)

    async with env.Session() as db:
        for i, (name, subfolder) in enumerate(FOLDERS.items()):
            fp = ds_dir / "images" / name
            fp.write_bytes(b"PNGDATA" + bytes([i]))
            db.add(Image(
                dataset_id=dataset_id,
                filename=name,
                original_filename=name,
                subfolder=subfolder,
                file_path=str(fp),
                thumbnail_path=str(ds_dir / "thumbnails" / f"{name}.webp"),
                caption_text="a caption",
            ))
        await db.commit()


def test_preview_and_export_agree_on_every_selection(tmp_path):
    cases = [
        ([""], {"root.png"}),
        (["", "a/b"], {"root.png", "b.png"}),
        (["a/b", "a/c"], {"b.png", "c.png"}),
        (["a"], set()),
        (["with,comma"], {"comma.png"}),
        ([], set()),
    ]

    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"])

            for i, (subfolders, expected) in enumerate(cases):
                r = await env.client.get(
                    f"{API}/export/preview/{ds['id']}",
                    params={"subfolders": json.dumps(subfolders)},
                )
                assert r.status_code == 200, r.text
                preview = r.json()
                assert preview["image_count"] == len(expected), subfolders
                assert preview["will_export"] == len(expected), subfolders

                out = tmp_path / f"out{i}"
                r = await env.client.post(f"{API}/export/plain", json={
                    "dataset_id": ds["id"], "output_dir": str(out), "subfolders": subfolders,
                })
                assert r.status_code == 200, r.text
                job = await wait_for_job(env, r.json()["job_id"])
                assert job["status"] == "completed", job
                assert job["result_data"]["exported"] == len(expected), subfolders
                images = out / "images"
                written = {p.name for p in images.iterdir()} if images.exists() else set()
                assert written == expected, subfolders

    run(scenario())


def test_no_subfolders_param_means_no_filter(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await _seed(env, ds["id"])

            r = await env.client.get(f"{API}/export/preview/{ds['id']}")
            assert r.status_code == 200, r.text
            assert r.json()["will_export"] == len(FOLDERS)

    run(scenario())


def test_a_malformed_subfolders_param_is_refused(tmp_path):
    """Including the old comma form: read as "no filter", it would show a count for
    the whole dataset rather than an error."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")

            for bad in ("a/b,a/c", '{"a": 1}', "[1]", '"a"'):
                r = await env.client.get(
                    f"{API}/export/preview/{ds['id']}", params={"subfolders": bad},
                )
                assert r.status_code == 400, f"{bad}: {r.status_code} {r.text}"

    run(scenario())
