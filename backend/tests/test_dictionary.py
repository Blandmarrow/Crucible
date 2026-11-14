"""The offline synonym dictionary (#52): pinned download, conversion, lookup, routes.

A tiny archive in the real OEWN JSON shape stands in for the 10 MB one, served
through `downloads._open` (which conftest's `_no_real_download` otherwise refuses).
The real archive's shape was checked on 2026-11-14 — see `docs/dev/dictionary.md`.
"""

import asyncio
import hashlib
import io
import json
import threading
import zipfile

import pytest

from backend import downloads
from backend.config import settings
from backend.services import dictionary_service as d
from backend.tests.conftest import API, api_env, run

LEXFILES = {
    "noun.animal.json": {
        "01-n": {"members": ["goose"], "definition": ["a web-footed bird"], "partOfSpeech": "n"},
        "02-n": {"members": ["goose", "fool", "bozo"], "definition": ["a silly person"], "partOfSpeech": "n"},
        "03-n": {"members": ["cat", "true cat"], "definition": ["feline mammal"], "partOfSpeech": "n"},
        "04-n": {"members": ["CAT", "computed tomography"], "definition": ["a scan"], "partOfSpeech": "n"},
        "05-n": {"members": ["ice cream", "icecream"], "definition": ["frozen dessert"], "partOfSpeech": "n"},
        "06-n": {"members": ["woman", "adult female"], "definition": ["an adult female person"], "partOfSpeech": "n"},
    },
    "adj.all.json": {
        "10-a": {
            "members": ["shiny", "glazed"], "definition": ["having a shiny surface"],
            "partOfSpeech": "a", "similar": ["11-s"],
        },
        "11-s": {
            "members": ["glossy", "lustrous"], "definition": ["reflecting light"],
            "partOfSpeech": "s", "similar": ["10-a"],
        },
    },
    "verb.motion.json": {
        "20-v": {"members": ["run", "scat"], "definition": ["flee"], "partOfSpeech": "v"},
        "21-v": {"members": ["walk", "stroll"], "definition": ["go on foot"], "partOfSpeech": "v"},
    },
}
ENTRIES = {
    "entries-c.json": {
        "cat": {"n": {"sense": [{"id": "cat%1", "synset": "03-n"}]}},
        "CAT": {"n": {"sense": [{"id": "cat%2", "synset": "04-n"}]}},
    },
    "entries-g.json": {
        "goose": {"n": {"form": ["geese"], "sense": [
            {"id": "goose%1", "synset": "01-n"}, {"id": "goose%2", "synset": "02-n"},
        ]}},
        "glossy": {"a": {"sense": [{"id": "glossy%1", "synset": "11-s"}]}},
    },
    "entries-i.json": {"ice cream": {"n": {"sense": [{"id": "ice_cream%1", "synset": "05-n"}]}}},
    "entries-r.json": {"run": {"v": {"form": ["ran"], "sense": [{"id": "run%1", "synset": "20-v"}]}}},
    "entries-s.json": {"shiny": {"a": {"sense": [{"id": "shiny%1", "synset": "10-a"}]}}},
    "entries-w.json": {
        "walk": {"v": {"sense": [{"id": "walk%1", "synset": "21-v"}]}},
        "woman": {"n": {"sense": [{"id": "woman%1", "synset": "06-n"}]}},
    },
}


def make_zip(extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in {**LEXFILES, **ENTRIES, "frames.json": {}, **(extra or {})}.items():
            zf.writestr(name, json.dumps(body))
    return buf.getvalue()


class _Resp:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serve(monkeypatch, data: bytes, *, pin: str | None = None) -> list[str]:
    """Serve `data` for every download and pin the dictionary to it (or to `pin`)."""
    calls: list[str] = []

    def _open(url, timeout):
        calls.append(url)
        return _Resp(data)

    monkeypatch.setattr(downloads, "_open", _open)
    monkeypatch.setattr(d, "ZIP_SHA256", pin or hashlib.sha256(data).hexdigest())
    return calls


@pytest.fixture(autouse=True)
def fresh_dictionary(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(d, "_progress", d._Progress())
    monkeypatch.setattr(d, "_task", None)
    monkeypatch.setattr(d, "_stop", threading.Event())


async def _install_and_wait(client) -> dict:
    r = await client.post(f"{API}/dictionary/install")
    assert r.status_code == 200
    assert r.json()["state"] in ("downloading", "converting", "ready")
    if d._task is not None:
        await asyncio.wait({d._task})
    return (await client.get(f"{API}/dictionary/status")).json()


# ── install ───────────────────────────────────────────────────────────────────


def test_install_downloads_converts_and_cleans_up(tmp_path, monkeypatch):
    calls = serve(monkeypatch, make_zip())

    async def scenario():
        async with api_env(tmp_path) as env:
            before = (await env.client.get(f"{API}/dictionary/status")).json()
            assert before["state"] == "missing" and before["installed"] is False
            status = await _install_and_wait(env.client)
            assert status["state"] == "ready" and status["installed"] is True
            assert status["size_bytes"] > 0
            # A second press while ready sends nothing.
            again = await env.client.post(f"{API}/dictionary/install")
            assert again.json()["state"] == "ready"

    run(scenario())
    assert calls == [d.ZIP_URL]
    folder = d.dictionary_dir()
    assert sorted(p.name for p in folder.iterdir()) == [
        "LICENSE.md", "NOTICE.txt", "WNDB_License.txt", d.DB_NAME,
    ]
    assert "Princeton University" in (folder / "WNDB_License.txt").read_text(encoding="utf-8")


def test_a_hash_mismatch_is_refused_and_leaves_nothing(tmp_path, monkeypatch):
    serve(monkeypatch, make_zip(), pin="0" * 64)

    async def scenario():
        async with api_env(tmp_path) as env:
            return await _install_and_wait(env.client)

    status = run(scenario())
    assert status["state"] == "failed" and status["installed"] is False
    assert "not the expected file" in status["error"]
    assert not any(d.dictionary_dir().iterdir())


def test_an_unexpected_archive_member_is_refused(tmp_path, monkeypatch):
    serve(monkeypatch, make_zip({"../evil.json": {}}))

    async def scenario():
        async with api_env(tmp_path) as env:
            return await _install_and_wait(env.client)

    status = run(scenario())
    assert status["state"] == "failed"
    assert "unexpected file" in status["error"]
    assert not d.db_path().exists()


def test_without_a_network_the_install_fails_visibly(tmp_path):
    # conftest's `_no_real_download` refuses the connection, as an offline machine would.
    async def scenario():
        async with api_env(tmp_path) as env:
            return await _install_and_wait(env.client)

    status = run(scenario())
    assert status["state"] == "failed"
    assert status["error"].startswith("Could not download the dictionary")


def test_sweep_removes_what_an_unfinished_install_left(tmp_path):
    folder = d.dictionary_dir()
    folder.mkdir(parents=True)
    leftovers = [downloads.part_path(folder / d.ZIP_NAME), folder / d.ZIP_NAME, folder / (d.DB_NAME + ".tmp")]
    for p in leftovers:
        p.write_bytes(b"half")
    d.sweep_leftovers()
    assert not any(p.exists() for p in leftovers)


# ── lookup ────────────────────────────────────────────────────────────────────


@pytest.fixture
def built(tmp_path):
    zip_path = tmp_path / "oewn.zip"
    zip_path.write_bytes(make_zip())
    d.build(zip_path, d.dictionary_dir())


def _senses(word):
    return [(s["lemma"], s["pos"], s["words"]) for s in d.synonyms(word)["senses"]]


def test_irregular_forms_come_from_the_entries_form_lists(built):
    # The bird sense has no other word, so it is dropped; the fool sense stays.
    assert d.synonyms("Geese")["lemmas"] == ["goose"]
    assert _senses("geese") == [("goose", "n", ["fool", "bozo"])]
    assert _senses("ran") == [("run", "v", ["scat"])]


def test_regular_forms_go_through_morphy_rules(built):
    assert _senses("walked") == [("walk", "v", ["stroll"])]
    assert _senses("women") == [("woman", "n", ["adult female"])]
    assert _senses("ice creams") == [("ice cream", "n", ["icecream"])]


def test_the_lowercase_entry_ranks_first_unless_another_spelling_was_typed(built):
    # `cat` and `CAT` share the key `cat`; each ranks its senses from 0.
    assert _senses("cats") == [
        ("cat", "n", ["true cat"]),
        ("cat", "n", ["computed tomography"]),
    ]
    # Selected as written in "CAT scan", the scan comes first.
    assert _senses("CAT") == [
        ("cat", "n", ["computed tomography"]),
        ("cat", "n", ["true cat"]),
    ]


def test_a_combining_mark_is_part_of_a_letter():
    # `İ` lowercases to `i` + U+0307; the client's \p{L}\p{M} rule lets it through too.
    assert d.normalize("İstanbul") == "i̇stanbul"


def test_an_adjective_gets_its_similar_meanings_words(built):
    assert _senses("shiny") == [("shiny", "a", ["glazed", "glossy", "lustrous"])]
    # A satellite points back at its head.
    assert _senses("glossy") == [("glossy", "a", ["lustrous", "shiny", "glazed"])]


def test_underscores_and_case_are_normalised(built):
    assert _senses("ICE_CREAM") == [("ice cream", "n", ["icecream"])]
    assert d.synonyms("unknownword") == {"word": "unknownword", "lemmas": [], "senses": []}


@pytest.mark.parametrize("bad", ["", "   ", "123", "a b c d e", "x" * 41, "<b>"])
def test_normalize_refuses_what_is_not_a_word(bad):
    with pytest.raises(ValueError):
        d.normalize(bad)


# ── routes ────────────────────────────────────────────────────────────────────


def test_synonyms_route_codes(tmp_path, monkeypatch):
    serve(monkeypatch, make_zip())

    async def scenario():
        async with api_env(tmp_path) as env:
            c = env.client
            r = await c.get(f"{API}/dictionary/synonyms", params={"word": "shiny"})
            assert r.status_code == 409 and "not installed" in r.json()["detail"]
            await _install_and_wait(c)
            r = await c.get(f"{API}/dictionary/synonyms", params={"word": "Shiny"})
            assert r.status_code == 200
            assert r.json()["senses"][0]["words"] == ["glazed", "glossy", "lustrous"]
            r = await c.get(f"{API}/dictionary/synonyms", params={"word": "one two three four five"})
            assert r.status_code == 422

    run(scenario())


def test_remove_deletes_the_folder_and_refuses_while_installing(tmp_path, monkeypatch):
    serve(monkeypatch, make_zip())

    async def scenario():
        async with api_env(tmp_path) as env:
            c = env.client
            await _install_and_wait(c)
            r = await c.delete(f"{API}/dictionary")
            assert r.status_code == 200 and r.json()["state"] == "missing"
            assert not d.dictionary_dir().exists()

            blocker = asyncio.get_running_loop().create_future()
            d._task = asyncio.ensure_future(blocker)
            try:
                r = await c.delete(f"{API}/dictionary")
                assert r.status_code == 409
            finally:
                blocker.cancel()

            # And an install refuses while a removal runs.
            d._removing = True
            try:
                r = await c.post(f"{API}/dictionary/install")
                assert r.status_code == 409 and "being removed" in r.json()["detail"]
            finally:
                d._removing = False

    run(scenario())


def test_a_locked_database_blocks_the_remove_before_the_notices_go(tmp_path, monkeypatch):
    """Windows: a lookup holding the file open. The database is unlinked first, so a
    refusal leaves the install whole, licence notices included (PM-021)."""
    from backend import utils

    serve(monkeypatch, make_zip())

    async def locked(path, **_kw):
        raise utils.FileInUseError(f"'{path.name}' is in use")

    async def scenario():
        async with api_env(tmp_path) as env:
            await _install_and_wait(env.client)
            monkeypatch.setattr(utils, "unlink_retrying", locked)
            r = await env.client.delete(f"{API}/dictionary")
            assert r.status_code == 409
            return (await env.client.get(f"{API}/dictionary/status")).json()

    status = run(scenario())
    assert status["installed"] is True
    assert (d.dictionary_dir() / "LICENSE.md").exists()
    assert (d.dictionary_dir() / "WNDB_License.txt").exists()


class _SlowResp(_Resp):
    """A download that would take minutes, so shutdown always catches it mid-way."""

    def read(self, n=-1):
        import time
        time.sleep(0.02)
        return self._buf.read(1024)


def test_shutdown_stops_the_worker_thread_and_cleans_up(monkeypatch):
    data = make_zip() * 50  # never verified: the stop comes first
    monkeypatch.setattr(downloads, "_open", lambda url, timeout: _SlowResp(data))

    async def scenario():
        d.start_install()
        task = d._task
        await asyncio.sleep(0.2)
        assert d.installing()
        await d.shutdown(grace=5)
        # The thread stopped itself: no cancel, so no "exception never retrieved".
        assert task.done() and not task.cancelled() and task.exception() is None

    run(scenario())
    assert d.status()["state"] == "missing"
    assert not any(d.dictionary_dir().iterdir())


# ── the shared download helper ────────────────────────────────────────────────


def test_download_verified_writes_only_a_matching_file(tmp_path, monkeypatch):
    data = b"pinned bytes" * 1000
    monkeypatch.setattr(downloads, "_open", lambda url, timeout: _Resp(data))
    dest = tmp_path / "f.bin"
    seen = []
    downloads.download_verified(
        "https://x/f", dest, hashlib.sha256(data).hexdigest(),
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert dest.read_bytes() == data
    assert seen[-1] == (len(data), len(data))
    assert not downloads.part_path(dest).exists()

    other = tmp_path / "g.bin"
    with pytest.raises(downloads.DownloadHashMismatch):
        downloads.download_verified("https://x/g", other, "0" * 64)
    assert not other.exists() and not downloads.part_path(other).exists()

    with pytest.raises(downloads.DownloadCancelled):
        downloads.download_verified("https://x/g", other, "0" * 64, should_stop=lambda: True)
    assert not downloads.part_path(other).exists()


def test_cached_verified_deletes_a_file_with_the_wrong_hash(tmp_path):
    f = tmp_path / "w.bin"
    f.write_bytes(b"good")
    assert downloads.cached_verified(f, hashlib.sha256(b"good").hexdigest())
    assert not downloads.cached_verified(f, "0" * 64)
    assert not f.exists()
