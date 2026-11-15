"""The app's version string: its shape, and that the health route reports it.

`backend/version.py` is the one place the version lives (decision 19), and the Settings page
reads it from `GET /api/v1/health` to show it and build the release-notes link. The `release`
skill tags GitHub as `v` + this value, so a fourth number or a leading zero would make a tag
that sorts wrongly — the problem decision 19 exists to end.
"""
import re

from backend.tests.conftest import API, api_env, run
from backend.version import __version__


def test_version_is_three_plain_numbers():
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", __version__), __version__


def test_health_reports_the_version(tmp_path):
    async def scenario():
        async with api_env(tmp_path) as env:
            r = await env.client.get(f"{API}/health")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "ok"
            assert body["version"] == __version__

    run(scenario())
