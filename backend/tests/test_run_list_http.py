"""`GET /runs/`: per dataset, or across datasets with an optional status.

The TopBar's paused-run chip lists every dataset's paused runs, so `dataset_id` is
optional and `status` narrows the list.
"""
from backend.models import Run
from backend.tests.conftest import API, api_env, run


def test_list_runs_across_datasets_and_by_status(tmp_path):
    async def go():
        async with api_env(tmp_path) as env:
            a = await env.create_dataset("a")
            b = await env.create_dataset("b")
            async with env.Session() as db:
                for ds, status in ((a, "paused"), (a, "completed"), (b, "paused"), (b, "running")):
                    db.add(Run(dataset_id=ds["id"], name=f"{ds['name']}-{status}", status=status,
                               recipe={}, stages=[], context={}))
                await db.commit()

            r = await env.client.get(f"{API}/runs/", params={"dataset_id": a["id"]})
            assert r.status_code == 200
            assert sorted(x["name"] for x in r.json()) == ["a-completed", "a-paused"]

            r = await env.client.get(f"{API}/runs/")
            assert len(r.json()) == 4

            r = await env.client.get(f"{API}/runs/", params={"status": "paused"})
            assert sorted(x["name"] for x in r.json()) == ["a-paused", "b-paused"]

            r = await env.client.get(f"{API}/runs/", params={"status": "paused", "dataset_id": b["id"]})
            assert [x["name"] for x in r.json()] == ["b-paused"]

    run(go())
