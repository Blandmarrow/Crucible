"""Request-level tests for `PATCH /datasets/{id}/subfolders` — subfolder re-path.

Gallery subfolders are virtual (`Image.subfolder` plus `Dataset.declared_subfolders`),
so renaming `a/b` -> `a/c` and re-nesting `a` -> `z/a` are the *same* subtree prefix
rewrite and share one endpoint. This is also the first coverage this neighbourhood has
had at all, so it covers the guard table as well as the happy paths.

Two branches here are the ones nothing else can catch:

- **Filenames must not change.** A re-path is a label change; the images stay put on
  disk with the stems they already had. That assertion is what a future copy of
  `POST /images/batch/move-subfolder`'s `rename_on_move` would break.
- **`declared_subfolders` is rewritten by reassignment.** SQLAlchemy compares JSON
  columns by equality, so an in-place edit is a silently skipped UPDATE — and the
  failure is narrow: images re-path correctly and only *empty* declared folders are
  lost. Every other test in this file passes with that bug present.
"""
from backend.models import Dataset
from backend.services.dataset_busy import busy
from backend.tests.conftest import API, api_env, run, upload_image


async def _paths(env, ds_id: str) -> set[str]:
    r = await env.client.get(f"{API}/datasets/{ds_id}/subfolders")
    assert r.status_code == 200, r.text
    return {s["path"] for s in r.json()}


async def _by_subfolder(env, ds_id: str) -> dict[str, set[str]]:
    """{subfolder: {filename, …}} for every image in the dataset."""
    rows = (await env.client.get(f"{API}/images/", params={"dataset_id": ds_id, "limit": 500})).json()
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["subfolder"], set()).add(row["filename"])
    return out


async def _repath(env, ds_id: str, path: str, new_path: str):
    return await env.client.patch(
        f"{API}/datasets/{ds_id}/subfolders", json={"path": path, "new_path": new_path}
    )


def test_rename_carries_the_subtree_and_leaves_filenames_alone(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("repath-ds")
            for sf in ("a", "a/b", "a/b/c", "z"):
                await upload_image(env, ds["id"], name=f"{sf.replace('/', '_')}.png", subfolder=sf)
            before = await _by_subfolder(env, ds["id"])

            r = await _repath(env, ds["id"], "a", "people")
            assert r.status_code == 200, r.text
            assert r.json() == {"path": "people", "previous_path": "a", "images_updated": 3}

            assert await _paths(env, ds["id"]) == {"people", "people/b", "people/b/c", "z"}
            after = await _by_subfolder(env, ds["id"])
            # The control folder is untouched, and every image kept the stem it had.
            assert after["z"] == before["z"]
            assert after["people"] == before["a"]
            assert after["people/b"] == before["a/b"]
            assert after["people/b/c"] == before["a/b/c"]

    run(scenario())


def test_move_under_another_folder_and_back_to_top_level(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("repath-move")
            for sf in ("a", "a/b", "z"):
                await upload_image(env, ds["id"], name=f"{sf.replace('/', '_')}.png", subfolder=sf)

            r = await _repath(env, ds["id"], "a", "z/a")
            assert r.status_code == 200, r.text
            assert r.json()["images_updated"] == 2
            assert await _paths(env, ds["id"]) == {"z", "z/a", "z/a/b"}

            # Back out to the top level — the drag-onto-(root) path. The destination is
            # the bare basename, never "" (which is the root pseudo-folder).
            r = await _repath(env, ds["id"], "z/a", "a")
            assert r.status_code == 200, r.text
            assert await _paths(env, ds["id"]) == {"a", "a/b", "z"}

    run(scenario())


def test_declared_subfolders_are_rewritten(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("repath-declared")
            await upload_image(env, ds["id"], name="a.png", subfolder="a")
            # Declared but empty: it exists only in Dataset.declared_subfolders, so it
            # is invisible to the two image UPDATEs.
            r = await env.client.post(f"{API}/datasets/{ds['id']}/subfolders", json={"path": "a/b/empty"})
            assert r.status_code == 201, r.text

            r = await _repath(env, ds["id"], "a", "people")
            assert r.status_code == 200, r.text
            assert await _paths(env, ds["id"]) == {"people", "people/b", "people/b/empty"}

            async with env.Session() as session:
                row = await session.get(Dataset, ds["id"])
                declared = set(row.declared_subfolders or [])
            assert declared == {"people", "people/b", "people/b/empty"}

            # Moving the empty folder under a merely-declared parent must keep that
            # parent's ancestors declared, or it vanishes from the sidebar.
            r = await _repath(env, ds["id"], "people/b/empty", "people/b/moved")
            assert r.status_code == 200, r.text
            assert r.json()["images_updated"] == 0
            assert "people/b/moved" in await _paths(env, ds["id"])

    run(scenario())


def test_like_wildcards_in_a_path_are_escaped(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("repath-like")
            # `_` is a LIKE single-character wildcard, `%` matches anything. Unescaped,
            # re-pathing `a_b` would drag `axb`'s images along with it.
            for sf in ("a_b", "axb", "a%b", "azzzb"):
                await upload_image(env, ds["id"], name=f"{sf.replace('%', 'p')}.png", subfolder=sf)
                await upload_image(env, ds["id"], name=f"{sf.replace('%', 'p')}_child.png", subfolder=sf + "/c")

            r = await _repath(env, ds["id"], "a_b", "renamed")
            assert r.status_code == 200, r.text
            assert r.json()["images_updated"] == 2
            assert await _paths(env, ds["id"]) == {
                "renamed", "renamed/c", "axb", "axb/c", "a%b", "a%b/c", "azzzb", "azzzb/c",
            }

            r = await _repath(env, ds["id"], "a%b", "pct")
            assert r.status_code == 200, r.text
            assert r.json()["images_updated"] == 2
            assert await _paths(env, ds["id"]) == {
                "renamed", "renamed/c", "axb", "axb/c", "pct", "pct/c", "azzzb", "azzzb/c",
            }

    run(scenario())


def test_guards(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("repath-guards")
            for sf in ("a", "a/b", "z"):
                await upload_image(env, ds["id"], name=f"{sf.replace('/', '_')}.png", subfolder=sf)

            missing = await _repath(env, "does-not-exist", "a", "b")
            assert missing.status_code == 404
            assert missing.json()["detail"] == "Dataset not found"

            cases = [
                ("../evil", "b", 400, "'..'"),
                ("a", "../evil", 400, "'..'"),
                ("", "b", 400, "Subfolder path must not be empty"),
                # "" is the root pseudo-folder, and normalizes from "/" too.
                ("a", "", 400, "New subfolder path must not be empty"),
                ("a", "/", 400, "New subfolder path must not be empty"),
                ("a", "a", 400, "New path is the same as the current path"),
                ("a", "a/b/a", 400, "Cannot move a subfolder into itself"),
                ("nope", "b", 404, "Subfolder not found: nope"),
                ("a", "z", 409, 'A subfolder named "z" already exists'),
            ]
            for path, new_path, code, fragment in cases:
                r = await _repath(env, ds["id"], path, new_path)
                assert r.status_code == code, f"{path!r}->{new_path!r}: {r.status_code} {r.text}"
                assert fragment in r.json()["detail"], f"{path!r}->{new_path!r}: {r.text}"

            # Nothing moved.
            assert await _paths(env, ds["id"]) == {"a", "a/b", "z"}

    run(scenario())


def test_refused_while_the_dataset_is_busy(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("repath-busy")
            await upload_image(env, ds["id"], name="a.png", subfolder="a")
            # VersionImageState.subfolder means a snapshot restore rewrites this exact
            # column, so an interactive re-path landing mid-job would race it.
            with busy(ds["id"], "restoring snapshot"):
                r = await _repath(env, ds["id"], "a", "b")
            assert r.status_code == 409, r.text
            assert "busy" in r.json()["detail"]
            assert await _paths(env, ds["id"]) == {"a"}

    run(scenario())
