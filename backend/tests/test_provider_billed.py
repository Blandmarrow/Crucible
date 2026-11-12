"""The per-provider `billed` setting: NULL follows the URL, an explicit value wins."""
from types import SimpleNamespace

import pytest

from backend.schemas.openai_provider import OpenAIProviderOut, provider_is_billed
from backend.tests.conftest import API, api_env, run


def _row(url, billed=None):
    return SimpleNamespace(base_url=url, billed=billed)


@pytest.mark.parametrize("url,expected", [
    ("http://localhost:1234/v1", False),
    ("http://127.0.0.1:1234/v1", False),
    ("http://192.168.1.20:1234/v1", True),
    ("https://api.openai.com/v1", True),
])
def test_null_follows_is_remote(url, expected):
    assert provider_is_billed(_row(url)) is expected


def test_explicit_wins():
    assert provider_is_billed(_row("http://192.168.1.20:1234/v1", False)) is False
    assert provider_is_billed(_row("http://localhost:1234/v1", True)) is True


def test_billed_http_round_trip(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.post(f"{API}/providers/", json={
                "name": "lan", "base_url": "http://192.168.1.20:1234/v1"})
            assert r.status_code == 201, r.text
            body = r.json()
            pid = body["id"]
            assert body["billed"] is None
            assert body["is_remote"] is True and body["is_billed"] is True

            r = await env.client.patch(f"{API}/providers/{pid}", json={"billed": False})
            assert r.json()["billed"] is False and r.json()["is_billed"] is False
            # An unrelated PATCH leaves it alone.
            r = await env.client.patch(f"{API}/providers/{pid}", json={"default_model": "m"})
            assert r.json()["billed"] is False
            listed = [p for p in (await env.client.get(f"{API}/providers/")).json() if p["id"] == pid]
            assert listed[0]["billed"] is False and listed[0]["is_billed"] is False

            r = await env.client.post(f"{API}/providers/", json={
                "name": "paid-local", "base_url": "http://localhost:1/v1", "billed": True})
            assert r.status_code == 201, r.text
            assert r.json()["billed"] is True and r.json()["is_billed"] is True
            assert r.json()["is_remote"] is False

    run(scenario())


def test_out_carries_both_fields():
    out = OpenAIProviderOut.from_orm_row(SimpleNamespace(
        id="i", name="n", base_url="http://localhost:1/v1", api_key="", default_model="",
        max_image_px=1024, max_tokens=1, timeout_s=10, billed=True, created_at=__import__("datetime").datetime(2026, 1, 1)))
    assert out.billed is True and out.is_billed is True and out.is_remote is False


def test_out_carries_max_concurrent():
    row = SimpleNamespace(
        id="i", name="n", base_url="http://localhost:1/v1", api_key="", default_model="",
        max_image_px=1024, max_tokens=1, timeout_s=10, max_concurrent=4, billed=None,
        created_at=__import__("datetime").datetime(2026, 1, 1))
    assert OpenAIProviderOut.from_orm_row(row).max_concurrent == 4
    del row.max_concurrent                      # a row-like object without the column
    assert OpenAIProviderOut.from_orm_row(row).max_concurrent == 1
