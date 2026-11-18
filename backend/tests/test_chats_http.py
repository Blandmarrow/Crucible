"""The chat routes, their cascades, the budget settings and the provider privacy
override (M12 phase 2a)."""
from sqlalchemy import select

from backend.models import Chat, ChatMessage, LlmUsage
from backend.tests.conftest import API, api_env, run


async def _provider(env, name="p", base_url="http://localhost:9/v1", **extra) -> dict:
    r = await env.client.post(f"{API}/providers/", json={"name": name, "base_url": base_url, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_chat_crud_and_preview(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("c")
            prov = await _provider(env)
            base = f"{API}/datasets/{ds['id']}/chats"

            r = await env.client.post(base, json={"title": "  First  ", "provider_id": prov["id"], "model": "m"})
            assert r.status_code == 201, r.text
            chat = r.json()
            assert (chat["title"], chat["provider_id"], chat["model"], chat["privacy"]) == ("First", prov["id"], "m", None)
            assert [c["id"] for c in (await env.client.get(base)).json()] == [chat["id"]]

            detail = (await env.client.get(f"{API}/chats/{chat['id']}")).json()
            assert detail["messages"] == [] and detail["message_count"] == 0

            r = await env.client.post(f"{API}/chats/{chat['id']}/preview-context", json={"draft": "Hi"})
            assert r.status_code == 200, r.text
            preview = r.json()
            assert [m["role"] for m in preview["messages"]] == ["system", "user"]
            # A localhost provider with `billed` unset is free: everything but images.
            assert preview["manifest"]["privacy_source"] == "provider"
            assert preview["privacy"]["captions"] is True and preview["privacy"]["images"] is False

            r = await env.client.patch(f"{API}/chats/{chat['id']}", json={"privacy": {"captions": False}})
            assert r.status_code == 200 and r.json()["privacy"] == {"captions": False}
            preview = (await env.client.post(f"{API}/chats/{chat['id']}/preview-context", json={})).json()
            assert preview["manifest"]["privacy_source"] == "chat"
            assert preview["privacy"]["captions"] is False and preview["privacy"]["notes"] is True

            # Explicit null clears the override; images and unknown names are refused.
            r = await env.client.patch(f"{API}/chats/{chat['id']}", json={"privacy": None})
            assert r.json()["privacy"] is None
            for bad in ({"images": True}, {"pixels": True}):
                r = await env.client.patch(f"{API}/chats/{chat['id']}", json={"privacy": bad})
                assert r.status_code == 422, bad

            r = await env.client.patch(f"{API}/chats/{chat['id']}", json={"title": "Renamed"})
            assert r.json()["title"] == "Renamed"

            assert (await env.client.delete(f"{API}/chats/{chat['id']}")).status_code == 204
            assert (await env.client.get(f"{API}/chats/{chat['id']}")).status_code == 404
            assert (await env.client.get(f"{API}/datasets/nope/chats")).status_code == 404
            r = await env.client.post(base, json={"provider_id": "nope"})
            assert r.status_code == 404

    run(scenario())


def test_provider_change_clears_the_chat_override(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("c")
            local = await _provider(env, "local")
            paid = await _provider(env, "paid", base_url="https://api.example.com/v1")
            chat = (await env.client.post(
                f"{API}/datasets/{ds['id']}/chats", json={"provider_id": local["id"]}
            )).json()
            await env.client.patch(f"{API}/chats/{chat['id']}", json={"privacy": {"captions": True}})

            r = await env.client.patch(f"{API}/chats/{chat['id']}", json={"provider_id": paid["id"]})
            assert r.status_code == 200 and r.json()["privacy"] is None
            preview = (await env.client.post(f"{API}/chats/{chat['id']}/preview-context", json={})).json()
            assert {c for c, v in preview["privacy"].items() if v} == {"numbers"}

            # Setting the same provider again is not a change.
            await env.client.patch(f"{API}/chats/{chat['id']}", json={"privacy": {"tags": True}})
            r = await env.client.patch(f"{API}/chats/{chat['id']}", json={"provider_id": paid["id"]})
            assert r.json()["privacy"] == {"tags": True}

            # A form that resends its toggles with a provider change is refused, and
            # nothing in the request is applied.
            r = await env.client.patch(f"{API}/chats/{chat['id']}", json={
                "title": "x", "provider_id": local["id"], "privacy": {"captions": True},
            })
            assert r.status_code == 422
            got = (await env.client.get(f"{API}/chats/{chat['id']}")).json()
            assert (got["provider_id"], got["title"], got["privacy"]) == (paid["id"], "", {"tags": True})

    run(scenario())


def test_editing_a_provider_clears_its_chats_overrides(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("c")
            prov = await _provider(env, "local")
            base = f"{API}/datasets/{ds['id']}/chats"
            chat = (await env.client.post(base, json={"provider_id": prov["id"]})).json()
            url = f"{API}/chats/{chat['id']}"

            async def privacy() -> dict | None:
                return (await env.client.get(url)).json()["privacy"]

            # A rename, a model or an override of its own: the chat's toggles stay.
            await env.client.patch(url, json={"privacy": {"captions": True}})
            await env.client.patch(f"{API}/providers/{prov['id']}", json={"name": "renamed", "max_tokens": 99})
            await env.client.patch(f"{API}/providers/{prov['id']}", json={"analyst_privacy": {"tags": True}})
            assert await privacy() == {"captions": True}

            # Ticking `billed` changes what the server is: the toggles go.
            await env.client.patch(f"{API}/providers/{prov['id']}", json={"billed": True})
            assert await privacy() is None

            # So does pointing it at another server.
            await env.client.patch(url, json={"privacy": {"captions": True}})
            await env.client.patch(f"{API}/providers/{prov['id']}", json={"base_url": "http://localhost:10/v1"})
            assert await privacy() is None

            # An empty override is no override.
            r = await env.client.patch(f"{API}/providers/{prov['id']}", json={"analyst_privacy": {}})
            assert r.json()["analyst_privacy"] is None
            r = await env.client.post(f"{API}/providers/", json={
                "name": "empty", "base_url": "http://localhost:11/v1", "analyst_privacy": {},
            })
            assert r.json()["analyst_privacy"] is None

    run(scenario())


def test_cascades_dataset_provider_and_usage(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            ds = await env.create_dataset("c")
            prov = await _provider(env)
            keep = (await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={"provider_id": prov["id"]})).json()
            gone = (await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={})).json()
            async with env.Session() as db:
                msg = ChatMessage(chat_id=gone["id"], seq=0, role="user", content="x")
                db.add(msg)
                await db.flush()
                db.add(LlmUsage(day="2026-11-18", chat_id=gone["id"], message_id=msg.id,
                                provider_id=prov["id"], tokens_in=10, tokens_out=5, billed=True))
                await db.commit()

            # Deleting a chat takes its messages, never its usage (the daily budget).
            await env.client.delete(f"{API}/chats/{gone['id']}")
            async with env.Session() as db:
                assert (await db.execute(select(ChatMessage))).first() is None
                usage = (await db.execute(select(LlmUsage))).scalar_one()
                assert (usage.chat_id, usage.message_id, usage.tokens_in) == (None, None, 10)

            # Deleting the provider keeps the chat, unassigned.
            assert (await env.client.delete(f"{API}/providers/{prov['id']}")).status_code == 204
            assert (await env.client.get(f"{API}/chats/{keep['id']}")).json()["provider_id"] is None
            async with env.Session() as db:
                assert (await db.execute(select(LlmUsage))).scalar_one().provider_id is None

            # Deleting the dataset takes its chats.
            assert (await env.client.delete(f"{API}/datasets/{ds['id']}")).status_code in (200, 204)
            async with env.Session() as db:
                assert (await db.execute(select(Chat))).first() is None

    run(scenario())


def test_budget_settings(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            got = (await env.client.get(f"{API}/settings/thresholds")).json()
            assert (got["analyst_chat_budget_tokens"], got["analyst_daily_budget_tokens"]) == (200_000, 1_000_000)
            r = await env.client.patch(f"{API}/settings/thresholds", json={"analyst_chat_budget_tokens": 50_000})
            assert r.status_code == 200 and r.json()["analyst_chat_budget_tokens"] == 50_000
            r = await env.client.patch(f"{API}/settings/thresholds", json={"analyst_daily_budget_tokens": 10})
            assert r.status_code == 422

    run(scenario())


def test_provider_privacy_defaults_override_and_reset(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            local = await _provider(env, "local")
            assert local["analyst_privacy"] is None
            assert all(v for c, v in local["analyst_privacy_effective"].items() if c != "images")

            # Marking it billed flips the defaults with nothing stored.
            r = await env.client.patch(f"{API}/providers/{local['id']}", json={"billed": True})
            eff = r.json()["analyst_privacy_effective"]
            assert {c for c, v in eff.items() if v} == {"numbers"}

            r = await env.client.patch(f"{API}/providers/{local['id']}", json={"analyst_privacy": {"tags": True}})
            assert r.json()["analyst_privacy"] == {"tags": True}
            assert {c for c, v in r.json()["analyst_privacy_effective"].items() if v} == {"numbers", "tags"}

            r = await env.client.patch(f"{API}/providers/{local['id']}", json={"analyst_privacy_reset": True})
            assert r.json()["analyst_privacy"] is None

            r = await env.client.patch(f"{API}/providers/{local['id']}", json={"analyst_privacy": {"images": True}})
            assert r.status_code == 422
            remote = await _provider(env, "remote", base_url="https://api.example.com/v1",
                                     analyst_privacy={"captions": True})
            assert {c for c, v in remote["analyst_privacy_effective"].items() if v} == {"numbers", "captions"}

    run(scenario())
