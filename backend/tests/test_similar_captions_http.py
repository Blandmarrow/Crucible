"""Request-level tests for `GET /images/{id}/similar-captions` (M11 #45).

Rows are seeded straight through the session: captions, `captioned_at`, CLIP
vectors and detections, none of which needs a model.
"""
from datetime import datetime, timedelta

import numpy as np

from backend.models.detection import Detection
from backend.models.image import Image
from backend.services.similar_captions_service import parse_caption_terms
from backend.tests.conftest import API, api_env, png_bytes, run, upload_image


def _vec(*head: float) -> bytes:
    v = np.zeros(768, np.float32)
    v[: len(head)] = head
    v /= np.linalg.norm(v)
    return v.astype(np.float16).tobytes()


async def _img(env, ds_id, name, caption="", **cols) -> str:
    n = sum(name.encode()) * 7 % 200
    img = await upload_image(env, ds_id, name, png_bytes((n, (n * 3) % 255, 40)))
    async with env.Session() as db:
        row = await db.get(Image, img["id"])
        row.caption_text = caption
        for k, v in cols.items():
            setattr(row, k, v)
        await db.commit()
    return img["id"]


async def _detect(env, image_id, *labels):
    async with env.Session() as db:
        for label in labels:
            db.add(Detection(
                image_id=image_id, label=label, bbox=[0.1, 0.1, 0.5, 0.5],
                score=0.9, model="t", task="detect",
            ))
        await db.commit()


async def _get(env, image_id, by, **params):
    return await env.client.get(
        f"{API}/images/{image_id}/similar-captions", params={"by": by, **params}
    )


def _ids(body):
    return [i["id"] for i in body["items"]]


def test_parse_caption_terms_tags_and_prose():
    kind, terms = parse_caption_terms("Red_Scarf,  SNOW , red scarf, a   big  hat")
    assert kind == "tags"
    assert terms == ["red scarf", "snow", "a big hat"]
    kind, terms = parse_caption_terms("The dog runs in the park with the dog")
    assert kind == "prose"
    assert terms == ["dog", "runs", "park"]
    assert parse_caption_terms("")[1] == []
    # Long comma parts are prose, not tags.
    kind, _ = parse_caption_terms("a very long first clause here, another long clause goes here")
    assert kind == "prose"


def test_tags_idf_ranking_and_exclusions(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            other = await env.create_dataset("other")
            q = await _img(env, ds["id"], "q.png", "girl, snow, red scarf")
            common_only = await _img(env, ds["id"], "a.png", "girl, hat, tree")
            rare = await _img(env, ds["id"], "b.png", "boy, Red_Scarf, cat")
            await _img(env, ds["id"], "c.png", "girl, sky, moon")
            await _img(env, ds["id"], "u.png", "")  # uncaptioned
            await _img(env, other["id"], "o.png", "girl, snow, red scarf")  # other dataset

            r = await _get(env, q, "tags")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["by"] == "tags" and body["available"] is True and body["reason"] is None
            # `girl` is on 3 of 4 candidates; `red scarf` on 1: rare outranks common.
            assert _ids(body)[0] == rare
            assert body["items"][0]["shared"] == ["Red_Scarf"]
            assert common_only in _ids(body)
            assert len(body["items"]) == 3  # a, b, c: not q, not uncaptioned, not other
            assert q not in _ids(body)
            assert body["items"][0]["score"] > body["items"][1]["score"]

            r = await _get(env, q, "tags", limit=1)
            assert len(r.json()["items"]) == 1

    run(scenario())


def test_tags_without_a_caption_is_unavailable(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            q = await _img(env, ds["id"], "q.png", "")
            body = (await _get(env, q, "tags")).json()
            assert body["available"] is False
            assert body["reason"] == "This image has no caption yet"
            assert body["items"] == []

    run(scenario())


def test_prose_fallback_ranks_by_shared_words_and_ignores_stopwords(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            q = await _img(env, ds["id"], "q.png", "A tall woman walks along the beach at sunset")
            two = await _img(env, ds["id"], "t.png", "Woman on a beach, golden light")
            one = await _img(env, ds["id"], "o.png", "A woman in a kitchen")
            await _img(env, ds["id"], "n.png", "The and with that this their")  # stopwords only

            body = (await _get(env, q, "tags")).json()
            assert _ids(body) == [two, one]
            assert set(body["items"][0]["shared"]) == {"woman", "beach"}

    run(scenario())


def test_objects_ranking_and_unavailable(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            q = await _img(env, ds["id"], "q.png", "x")
            both = await _img(env, ds["id"], "b.png", "y")
            one = await _img(env, ds["id"], "o.png", "z")
            uncaptioned = await _img(env, ds["id"], "u.png", "")
            await _img(env, ds["id"], "n.png", "w")  # no detections

            r = await _get(env, q, "objects")
            body = r.json()
            assert body["available"] is False
            assert body["reason"] == "No detections on this image"

            await _detect(env, q, "person", "dog")
            await _detect(env, both, "person", "dog", "person")
            await _detect(env, one, "person", "car")
            await _detect(env, uncaptioned, "person", "dog")

            body = (await _get(env, q, "objects")).json()
            assert body["available"] is True
            assert _ids(body) == [both, one]
            assert body["items"][0]["shared"] == ["dog", "person"]
            assert body["items"][1]["shared"] == ["person"]

    run(scenario())


def test_clip_ranks_by_cosine_and_skips_missing(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            other = await env.create_dataset("o")
            q = await _img(env, ds["id"], "q.png", "x", clip_embedding=_vec(1, 0))
            near = await _img(env, ds["id"], "n.png", "y", clip_embedding=_vec(1, 0.1))
            mid = await _img(env, ds["id"], "m.png", "z", clip_embedding=_vec(1, 1))
            far = await _img(env, ds["id"], "f.png", "w", clip_embedding=_vec(0, 1))
            await _img(env, ds["id"], "nv.png", "v")  # no vector
            await _img(env, ds["id"], "un.png", "", clip_embedding=_vec(1, 0))  # uncaptioned
            await _img(env, other["id"], "ot.png", "o", clip_embedding=_vec(1, 0))

            body = (await _get(env, q, "clip")).json()
            assert body["available"] is True
            assert _ids(body) == [near, mid, far]
            assert body["items"][0]["score"] > 0.99
            assert all(i["shared"] == [] for i in body["items"])

            # A query without a vector is unavailable.
            nv = await _img(env, ds["id"], "nq.png", "x")
            body = (await _get(env, nv, "clip")).json()
            assert body["available"] is False
            assert body["reason"] == "No CLIP vector yet. Run Style embeddings · CLIP on the Quality page."

    run(scenario())


def test_recent_order_and_null_captioned_at(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            now = datetime(2026, 1, 1, 12, 0, 0)
            q = await _img(env, ds["id"], "q.png", "x", captioned_at=now)
            old = await _img(env, ds["id"], "o.png", "a", captioned_at=now - timedelta(days=2))
            new = await _img(env, ds["id"], "n.png", "b", captioned_at=now - timedelta(hours=1))
            await _img(env, ds["id"], "nu.png", "c")  # captioned_at NULL

            body = (await _get(env, q, "recent")).json()
            assert _ids(body) == [new, old]
            assert body["items"][0]["captioned_at"] is not None
            assert body["items"][0]["captioned_at"] > body["items"][1]["captioned_at"]

    run(scenario())


def test_unknown_image_404_and_bad_params_422(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            q = await _img(env, ds["id"], "q.png", "x")
            assert (await _get(env, "nope", "tags")).status_code == 404
            assert (await _get(env, q, "bogus")).status_code == 422
            assert (await _get(env, q, "tags", limit=0)).status_code == 422
            assert (await _get(env, q, "tags", limit=25)).status_code == 422
            assert (await _get(env, q, "tags", limit=24)).status_code == 200

    run(scenario())


def test_hot_loop_agrees_with_the_full_parse():
    """`_shared_terms` (the per-row fast path) must find exactly the shared terms the
    full parse would, or the chip counts and highlights disagree with the ranking."""
    from backend.services.similar_captions_service import _shared_terms, _terms_with_spelling

    query = "red scarf, snow, long hair, 1girl"
    kind, terms = parse_caption_terms(query)
    qs = frozenset(terms)
    for cand in (
        "Red_Scarf, SNOW ,  long   hair, boots",
        "red\tscarf, 1girl,\nsnow",
        "long_hair,1girl",
        "nothing, in, common",
        "",
    ):
        full = {t for t in _terms_with_spelling(cand, kind) if t in qs}
        fast, size = _shared_terms(cand, kind, qs)
        assert fast == full, cand
        assert size == len(cand.split(","))

    kind, terms = parse_caption_terms("Женщина гуляет по пляжу на закате")
    assert kind == "prose" and "женщина" in terms
    fast, _ = _shared_terms("Женщина в шляпе", kind, frozenset(terms))
    assert fast == {"женщина"}


def test_caption_with_no_matchable_words_says_so(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            q = await _img(env, ds["id"], "q.png", "ok so")
            body = (await _get(env, q, "tags")).json()
            assert body["available"] is False
            assert body["reason"] == "No words to match in this caption"

    run(scenario())


def test_a_long_caption_does_not_win_on_length(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            q = await _img(env, ds["id"], "q.png", "red scarf, snow, forest")
            short = await _img(env, ds["id"], "s.png", "red scarf, snow")
            filler = ", ".join(f"tag{i}" for i in range(60))
            long_ = await _img(env, ds["id"], "l.png", f"red scarf, snow, forest, {filler}")
            body = (await _get(env, q, "tags")).json()
            # The long one shares one more tag, but 63 tags against 2.
            assert _ids(body) == [short, long_]

    run(scenario())


def test_recent_and_objects_stay_in_their_dataset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("d")
            other = await env.create_dataset("other")
            now = datetime(2026, 11, 14, 12, 0, 0)
            q = await _img(env, ds["id"], "q.png", "a, b", captioned_at=now)
            mine = await _img(env, ds["id"], "m.png", "c, d", captioned_at=now - timedelta(hours=1))
            far = await _img(env, other["id"], "f.png", "c, d", captioned_at=now + timedelta(hours=1))
            await _detect(env, q, "cat")
            await _detect(env, mine, "cat")
            await _detect(env, far, "cat")
            assert _ids((await _get(env, q, "recent")).json()) == [mine]
            assert _ids((await _get(env, q, "objects")).json()) == [mine]

    run(scenario())
