"""Request-level tests for `luminance_score` — the parts that do not need pixels.

The formula itself is deliberately untested: it lives in `score_technical_sync`,
which imports cv2, and CI has no OpenCV. That matches the coverage shape of every
other technical score. What *is* testable from the outside is the wiring, and the
wiring is where a new score field actually breaks: a column that no filter
whitelist admits, a histogram key the frontend reads and the backend never sends,
or a `score-values` array that silently stays absent.
"""
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


def test_luminance_is_an_accepted_score_field(tmp_path):
    """`_ALLOWED_SCORE_FIELDS` gates both filter forms; an omission is a 400."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            await upload_image(env, ds["id"], "a.png", png_bytes())

            r = await env.client.get(
                f"{API}/images/",
                params={"dataset_id": ds["id"], "score_field": "luminance_score", "min_score": 0.5},
            )
            assert r.status_code == 200, r.text
            # Nothing has been scored, so the column is NULL and the range excludes it.
            assert r.json() == []

            # An unknown field still 400s — the whitelist was widened, not removed.
            r = await env.client.get(
                f"{API}/images/",
                params={"dataset_id": ds["id"], "score_field": "brightness", "min_score": 0.5},
            )
            assert r.status_code == 400, r.text

    run(scenario())


def test_luminance_is_honoured_in_the_score_filters_array(tmp_path):
    """The gallery's JSON form reads the same frozenset. An unlisted field is
    skipped silently there, so a miss would look like "the filter does nothing"."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())

            # Set the column directly: scoring it for real needs cv2.
            async with env.Session() as db:
                from backend.models import Image
                row = await db.get(Image, img["id"])
                row.luminance_score = 0.8
                await db.commit()

            async def ids(spec: str) -> list[str]:
                r = await env.client.get(
                    f"{API}/images/", params={"dataset_id": ds["id"], "score_filters": spec}
                )
                assert r.status_code == 200, r.text
                return [i["id"] for i in r.json()]

            assert await ids('[{"field":"luminance_score","min":0.5}]') == [img["id"]]
            assert await ids('[{"field":"luminance_score","max":0.5}]') == []

            # It also comes back on the list payload, for the gallery card.
            r = await env.client.get(f"{API}/images/", params={"dataset_id": ds["id"]})
            assert r.json()[0]["luminance_score"] == 0.8
            r = await env.client.get(f"{API}/images/{img['id']}")
            assert r.json()["luminance_score"] == 0.8

    run(scenario())


def test_stats_and_score_values_carry_luminance(tmp_path):
    """StatsPage reads `luminance_distribution` off /stats and the raw array off
    /score-values (it rebuckets client-side once the user edits the edges), so
    both have to exist even before anything is scored."""
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            img = await upload_image(env, ds["id"], "a.png", png_bytes())

            r = await env.client.get(f"{API}/datasets/{ds['id']}/stats")
            assert r.status_code == 200, r.text
            assert "luminance_distribution" in r.json()

            r = await env.client.get(f"{API}/datasets/{ds['id']}/score-values")
            assert r.status_code == 200, r.text
            assert r.json()["luminance_score"] == []

            async with env.Session() as db:
                from backend.models import Image
                row = await db.get(Image, img["id"])
                row.luminance_score = 0.05  # the darkest bucket
                await db.commit()

            r = await env.client.get(f"{API}/datasets/{ds['id']}/stats")
            # Edges are 0.15/0.3/0.5/0.7 — must match DEFAULT_EDGES.luminance
            # on StatsPage, or an edited histogram jumps on first edit.
            assert r.json()["luminance_distribution"] == {"<0.15": 1}

            r = await env.client.get(f"{API}/datasets/{ds['id']}/score-values")
            assert r.json()["luminance_score"] == [0.05]

    run(scenario())


def test_a_null_brightness_is_absent_from_the_histogram_and_the_filter(tmp_path):
    """The wiring V-35 depends on. An image the technical scorer could not read
    records NULL rather than 0.0, so it must be *absent* from the distribution
    rather than counted in the darkest bucket — the bucket it never belonged in
    when the failure path wrote zeros."""
    async def scenario():
        async with api_env(tmp_path) as env:
            from backend.models import Image

            ds = await env.create_dataset("d")
            scored = await upload_image(env, ds["id"], "a.png", png_bytes())
            unread = await upload_image(env, ds["id"], "b.png", png_bytes((90, 90, 90)))

            async with env.Session() as db:
                (await db.get(Image, scored["id"])).luminance_score = 0.05
                (await db.get(Image, unread["id"])).luminance_score = None
                await db.commit()

            r = await env.client.get(f"{API}/datasets/{ds['id']}/stats")
            assert r.json()["luminance_distribution"] == {"<0.15": 1}

            r = await env.client.get(f"{API}/datasets/{ds['id']}/score-values")
            assert r.json()["luminance_score"] == [0.05]

            # `score_coverage["technical"]` counts blur_score, so an unread file
            # reads as unscored rather than inflating coverage.
            async with env.Session() as db:
                (await db.get(Image, scored["id"])).blur_score = 120.0
                await db.commit()
            r = await env.client.get(f"{API}/datasets/{ds['id']}/stats")
            assert r.json()["score_coverage"]["technical"] == 1

            r = await env.client.get(
                f"{API}/images/",
                params={"dataset_id": ds["id"], "score_field": "luminance_score", "min_score": 0.0},
            )
            assert [i["id"] for i in r.json()] == [scored["id"]]

    run(scenario())
