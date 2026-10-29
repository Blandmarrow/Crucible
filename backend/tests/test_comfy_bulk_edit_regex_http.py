"""`POST /comfy/plans/{id}/rows/bulk-edit` answers an invalid regex with a 400.

This route already did — it is the pattern the caption bulk-edit routes now follow
(GPT-031, `test_caption_bulk_http.py`) — but nothing pinned it. The two surfaces
mirror each other ("mirrors caption bulk-edit semantics"), so a regression on
either side is the same bug: a pattern that never ran reported as "nothing matched".
"""
from backend.tests.conftest import API, api_env, run

PROMPT_ALIAS = "prompt"
WORKFLOW = {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "template prompt"}},
    "9": {"class_type": "SaveImage", "inputs": {}},
}


async def _plan_with_row(env, prompt: str) -> tuple[str, str]:
    ds = await env.create_dataset("comfy-regex-ds")
    r = await env.client.post(f"{API}/comfy/plans", json={
        "dataset_id": ds["id"],
        "name": "regex-plan",
        "workflow_json": WORKFLOW,
        "pinned_params": [
            {"node_id": "6", "input": "text", "alias": PROMPT_ALIAS, "is_prompt": True},
        ],
        "output_node_ids": ["9"],
    })
    assert r.status_code == 200, r.text
    plan_id = r.json()["id"]
    r = await env.client.post(f"{API}/comfy/plans/{plan_id}/rows", json={"values": {PROMPT_ALIAS: prompt}})
    assert r.status_code == 200, r.text
    return plan_id, r.json()["id"]


async def _prompt(env, plan_id: str, row_id: str) -> str:
    r = await env.client.get(f"{API}/comfy/plans/{plan_id}/rows")
    assert r.status_code == 200, r.text
    return next(row for row in r.json() if row["id"] == row_id)["values"][PROMPT_ALIAS]


def test_invalid_regex_is_a_400_and_leaves_the_rows_alone(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id, row_id = await _plan_with_row(env, "a red square")

            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/bulk-edit",
                json={"operation": "find_replace", "text": "(", "replacement": "x", "use_regex": True},
            )
            assert r.status_code == 400, r.text
            assert r.json()["detail"].startswith("Invalid regex: ")
            assert await _prompt(env, plan_id, row_id) == "a red square"

            # The control: a valid pattern that matches nothing is a 200 no-op.
            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/bulk-edit",
                json={"operation": "find_replace", "text": "zzz+", "replacement": "x", "use_regex": True},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"affected": 0, "skipped": 1}

    run(scenario())


def test_invalid_replacement_template_is_a_400_not_a_500(tmp_path):
    """`\\9` compiles with the pattern and fails only on a match, inside the
    executor. That `regex_error` used to escape the route as a 500."""
    async def scenario():
        async with api_env(tmp_path) as env:
            plan_id, row_id = await _plan_with_row(env, "a red square")

            r = await env.client.post(
                f"{API}/comfy/plans/{plan_id}/rows/bulk-edit",
                json={"operation": "find_replace", "text": "(red)", "replacement": r"\9", "use_regex": True},
            )
            assert r.status_code == 400, r.text
            assert r.json()["detail"].startswith("Invalid regex: ")
            assert await _prompt(env, plan_id, row_id) == "a red square"

    run(scenario())
