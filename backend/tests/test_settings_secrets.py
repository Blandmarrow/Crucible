"""Request-level tests for GET/PATCH /settings/secrets and the HF_TOKEN projection.

**Environment hygiene is this module's own responsibility.** `conftest.api_env` restores
only `settings.datasets_dir` by hand and does no env save/restore at all, so anything here
that writes `os.environ["HF_TOKEN"]` or `settings.hf_token` without `monkeypatch` leaks into
every later test in the session — including ones that load real models. Every case below
goes through `monkeypatch.setenv`/`delenv`/`setattr`.

The three secrets resolve DB-first: a non-empty column wins, otherwise the value pydantic
read from `.env`/the OS environment at import (recorded on the `settings` singleton, which
nothing may ever assign to outside a test). `""` is the not-set sentinel in both stores, so
"clear the override" and "no row at all" are the same state.
"""
import os

from backend.config import settings
from backend.schemas import mask_secret
from backend.tests.conftest import API, api_env, run


def test_no_row_reports_the_env_chain(tmp_path, monkeypatch):
    """With nothing saved, every secret reports the .env value — or `unset` when blank."""
    monkeypatch.setattr(settings, "hf_token", "hf_envtokenABCD")
    monkeypatch.setattr(settings, "gelbooru_api_key", "")
    monkeypatch.setattr(settings, "gelbooru_user_id", "")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/settings/secrets")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["hf_token"]["source"] == "env"
            assert body["hf_token"]["masked"] == "***********ABCD"
            assert body["gelbooru_api_key"] == {"masked": "", "source": "unset"}
            assert body["gelbooru_user_id"] == {"masked": "", "source": "unset"}

    run(scenario())


def test_saving_masks_the_value_and_reports_db_source(tmp_path, monkeypatch):
    """The plaintext must appear in neither the PATCH response nor the following GET."""
    monkeypatch.setattr(settings, "hf_token", "")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    secret = "hf_supersecretvalue9876"

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(f"{API}/settings/secrets", json={"hf_token": secret})
            assert r.status_code == 200, r.text
            assert secret not in r.text
            assert r.json()["hf_token"] == {"masked": mask_secret(secret), "source": "db"}

            r = await env.client.get(f"{API}/settings/secrets")
            assert r.status_code == 200, r.text
            assert secret not in r.text
            assert r.json()["hf_token"]["source"] == "db"

    run(scenario())


def test_saving_assigns_the_environment_variable(tmp_path, monkeypatch):
    """Assignment, not setdefault — this case fails if anyone reintroduces setdefault.

    The eight HuggingFace loaders that pass no `token=` read the ambient variable, so a
    saved token that does not land here reaches exactly one loader.
    """
    monkeypatch.setattr(settings, "hf_token", "")
    monkeypatch.setenv("HF_TOKEN", "stale-value-from-earlier")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(f"{API}/settings/secrets", json={"hf_token": "hf_fresh1234"})
            assert r.status_code == 200, r.text
            assert os.environ["HF_TOKEN"] == "hf_fresh1234"

    run(scenario())


def test_clearing_restores_the_env_value(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hf_token", "hf_fromdotenv")

    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.patch(f"{API}/settings/secrets", json={"hf_token": "hf_fromtheui"})
            assert os.environ["HF_TOKEN"] == "hf_fromtheui"

            r = await env.client.patch(f"{API}/settings/secrets", json={"hf_token": ""})
            assert r.status_code == 200, r.text
            assert r.json()["hf_token"] == {"masked": mask_secret("hf_fromdotenv"), "source": "env"}
            assert os.environ["HF_TOKEN"] == "hf_fromdotenv"

    run(scenario())


def test_clearing_with_no_env_value_removes_the_variable(tmp_path, monkeypatch):
    """Pins pop-not-`""`: popping is what re-exposes HUGGING_FACE_HUB_TOKEN and the
    on-disk `~/.cache/huggingface/token` to huggingface_hub's own lookup, so a cleared
    override has to leave process state indistinguishable from a fresh boot."""
    monkeypatch.setattr(settings, "hf_token", "")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.patch(f"{API}/settings/secrets", json={"hf_token": "hf_temporary"})
            assert os.environ["HF_TOKEN"] == "hf_temporary"

            r = await env.client.patch(f"{API}/settings/secrets", json={"hf_token": ""})
            assert r.status_code == 200, r.text
            assert r.json()["hf_token"] == {"masked": "", "source": "unset"}
            assert "HF_TOKEN" not in os.environ

    run(scenario())


def test_echoing_the_get_response_back_is_rejected(tmp_path, monkeypatch):
    """`PATCH(GET().json())` is a 422 — the whole reason SecretsOut nests.

    This holds ONLY because the read shape is `{masked, source}` objects while the write
    shape is plain strings. A future flattening to `hf_token: str | None` would make the
    same call a 200 that silently saves `****abcd` as the key, and this test is where that
    must fail.
    """
    monkeypatch.setattr(settings, "hf_token", "hf_something1234")

    async def scenario():
        async with api_env(tmp_path) as env:
            body = (await env.client.get(f"{API}/settings/secrets")).json()
            r = await env.client.patch(f"{API}/settings/secrets", json=body)
            assert r.status_code == 422, r.text

    run(scenario())


def test_null_leaves_a_secret_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "hf_token", "")
    monkeypatch.setattr(settings, "gelbooru_api_key", "")

    async def scenario():
        async with api_env(tmp_path) as env:
            await env.client.patch(f"{API}/settings/secrets", json={"hf_token": "hf_keepme12"})
            r = await env.client.patch(
                f"{API}/settings/secrets", json={"gelbooru_api_key": "gb_key9999", "hf_token": None}
            )
            assert r.status_code == 200, r.text
            assert r.json()["hf_token"] == {"masked": mask_secret("hf_keepme12"), "source": "db"}
            assert r.json()["gelbooru_api_key"]["source"] == "db"

    run(scenario())


def test_whitespace_only_clears_the_override(tmp_path, monkeypatch):
    """A pasted token carries a trailing newline, so the field strips — and a value that is
    nothing but whitespace strips to "", i.e. clear."""
    monkeypatch.setattr(settings, "gelbooru_user_id", "12345")

    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.patch(
                f"{API}/settings/secrets", json={"gelbooru_user_id": "  67890\n"}
            )
            assert r.json()["gelbooru_user_id"]["masked"] == mask_secret("67890")

            r = await env.client.patch(f"{API}/settings/secrets", json={"gelbooru_user_id": "   "})
            assert r.json()["gelbooru_user_id"]["source"] == "env"

    run(scenario())


def test_startup_seeding_reprojects_the_saved_token(tmp_path, monkeypatch):
    """The only coverage of the restart path.

    The lifespan does not run under `httpx.ASGITransport` (see conftest), so the seeding
    step `main.lifespan` performs after `init_db()` is called here directly against the
    test session factory. Without that step a token saved in the UI is silently forgotten
    on every restart until the user re-saves it.
    """
    monkeypatch.setattr(settings, "hf_token", "")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    async def scenario():
        from backend.services.secrets_service import sync_env_from_db

        async with api_env(tmp_path) as env:
            await env.client.patch(f"{API}/settings/secrets", json={"hf_token": "hf_persisted77"})

            # Simulate the process going away: the projection is gone, the row is not.
            os.environ.pop("HF_TOKEN", None)

            async with env.Session() as session:
                await sync_env_from_db(session)
            assert os.environ["HF_TOKEN"] == "hf_persisted77"

    run(scenario())
    monkeypatch.delenv("HF_TOKEN", raising=False)


def test_mask_secret_boundaries():
    """First coverage of a formula `OpenAIProviderOut.api_key_masked` also depends on.

    Four characters or fewer masks entirely — the tail is only revealed once there is
    something in front of it to hide.
    """
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("abcd") == "****"
    assert mask_secret("abcde") == "*bcde"
    assert mask_secret("0123456789") == "******6789"
