#!/usr/bin/env python3
"""Scripted analyst-chat trials against a real model (M12 phase 2e).

Runs a few fixed conversations about one synthetic dataset through the real app,
in-process against a throwaway database (the backend tests' `api_env`), and reports
how a model copes with what the analyst asks of it: finishing a reply inside its
Max tokens, naming real findings and no invented ones, and writing a readable
`crucible-notes` block when asked. The result decides the "minimum model we
recommend" line in docs/analyst.md. See docs/dev/analyst/trials.md.

Nothing touches the real database or datasets folder. The dataset is synthetic:
made-up captions and plain coloured squares, so nothing private reaches a remote
provider. The API key is read from the environment or the dev database and never
printed.

Run from the repo root with the venv active:

    python scripts/analyst_trials.py --base-url http://localhost:1234/v1 --model qwen3-8b
    python scripts/analyst_trials.py --from-db Gemini --pause 6     # a provider saved in the dev DB
    python scripts/analyst_trials.py --from-db Strata --out trials-strata.md

`--from-db NAME` reads the provider's address, key and default model from
`dataset_manager.db` (read-only); `--model` overrides the model, and
`--api-key-env VAR` takes the key from an environment variable instead.

Each run starts with the tool-call probe (M12 phase 3b, docs/dev/analyst/tools.md), two
short calls, and reports its verdict; `--probe-only` stops there. The `tools` conversation
asks for a lookup outright, so it fails for a model that did not pass the probe: it is
offered no tools.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SHARE_ALL = {c: True for c in ("numbers", "identity", "notes", "tags", "captions", "file_names")}
FINDING_REF = re.compile(r"\[\[finding:([a-z0-9_]+)\]\]")

GOAL = "An SDXL character LoRA of Pip, a red fox mascot, for illustrated children's books."
CAPTIONS = [
    "pip, red fox, sitting, forest, smiling, children's book illustration",
    "pip, red fox, running, meadow, flowers, children's book illustration",
    "pip, red fox, reading a book, armchair, cozy room",
    "red fox",
    "fox",
    "pip, red fox, waving, village square, sunny day, children's book illustration",
    "",
    "",
]

# (name, message, what to check). Each conversation starts a fresh chat.
CONVERSATIONS = [
    ("rank", "What should I fix first in this dataset, and why? Keep it short.", "refs"),
    ("note-decision",
     "We agree: drop every image under 768px, since the LoRA trains at 1024. "
     "Please record that as a decision in the notes.", "notes-add"),
    ("note-remove",
     "The open item about re-tagging the forest images is done. Please remove it from the notes.",
     "notes-remove"),
    # The one conversation that needs a tool: the captions of one finding's images are not
    # in the context (the context's sample is the whole dataset's), so the model must look.
    ("tools",
     "Use your tools to read the captions of the images in [[finding:low_resolution]], then tell "
     "me in two sentences what you notice about those captions.", "tools"),
]


@dataclass
class Result:
    name: str
    status: str = ""
    error: str = ""
    seconds: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    estimated: bool = False
    refs_valid: int = 0
    refs_invented: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    # The reply's tool calls as "name" or "name (error)".
    tools: list[str] = field(default_factory=list)
    passed: bool = False
    reply: str = ""


def provider_from_db(name: str) -> dict:
    db = REPO / "dataset_manager.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute(
        "select base_url, api_key, default_model, max_tokens from openai_providers where name = ?", (name,)
    ).fetchone()
    if row is None:
        sys.exit(f"No provider named {name!r} in the dev database.")
    return {"base_url": row[0], "api_key": row[1] or "", "model": row[2] or "", "max_tokens": row[3]}


async def trial(cfg: dict, pause: float, probe_only: bool = False) -> tuple[dict, list[Result]]:
    from backend.tests.conftest import API, api_env, png_bytes, upload_image
    from backend.tests.test_chat_send import _events

    results: list[Result] = []
    with tempfile.TemporaryDirectory() as tmp:
        async with api_env(Path(tmp)) as env:
            ds = await env.create_dataset("trial-pip")
            r = await env.client.patch(f"{API}/datasets/{ds['id']}", json={"goal": GOAL, "target_resolution": 1024})
            assert r.status_code == 200, r.text
            for i, caption in enumerate(CAPTIONS):
                row = await upload_image(env, ds["id"], name=f"pip_{i:02d}.png", data=png_bytes((200, 60 + i * 20, 30)))
                if caption:
                    r = await env.client.put(f"{API}/captions/image/{row['id']}", json={"caption_text": caption, "caption_style": "tags"})
                    assert r.status_code == 200, r.text
            for section, text in (("preference", "The trigger word pip comes first in every caption."),
                                  ("open_item", "Re-tag the forest images with the season."),
                                  ("open_item", "Find ten more images of Pip at night.")):
                r = await env.client.post(f"{API}/datasets/{ds['id']}/notes", json={"section": section, "text": text})
                assert r.status_code == 201, r.text

            body = {"name": "trial", "base_url": cfg["base_url"], "api_key": cfg["api_key"],
                    "default_model": cfg["model"], "max_tokens": cfg["max_tokens"], "timeout_s": 600}
            r = await env.client.post(f"{API}/providers/", json=body)
            assert r.status_code == 201, r.text
            provider_id = r.json()["id"]
            t0 = time.monotonic()
            r = await env.client.post(f"{API}/providers/{provider_id}/probe-tools", json={})
            probe = r.json() if r.status_code == 200 else {"ok": None, "detail": f"no verdict ({r.status_code}): {r.json().get('detail')}"}
            probe["seconds"] = time.monotonic() - t0
            if probe_only:
                return probe, results
            known = {f["id"] for f in (await env.client.get(f"{API}/datasets/{ds['id']}/health")).json()["findings"]}

            for n, (name, message, check) in enumerate(CONVERSATIONS):
                if n and pause:
                    await asyncio.sleep(pause)
                res = Result(name)
                r = await env.client.post(f"{API}/datasets/{ds['id']}/chats", json={"provider_id": provider_id})
                chat_id = r.json()["id"]
                # Everything is shared, whatever the provider's defaults: the dataset is
                # synthetic, and the trial measures the model, not the privacy settings.
                # A billed provider's default (numbers only) hides the notes and the goal,
                # so "remove open_item 1" could not even be resolved.
                r = await env.client.patch(f"{API}/chats/{chat_id}", json={"privacy": SHARE_ALL})
                assert r.status_code == 200, r.text
                t0 = time.monotonic()
                r = await env.client.post(f"{API}/chats/{chat_id}/messages", json={"content": message})
                res.seconds = time.monotonic() - t0
                if r.status_code != 200:
                    res.status, res.error = f"refused {r.status_code}", r.text[:200]
                    results.append(res)
                    continue
                final = _events(r.text)[-1][1]
                res.status = final["status"] + (f" ({final['error_kind']})" if final.get("error_kind") else "")
                res.error = (final.get("error_detail") or "")[:200]
                res.tokens_in, res.tokens_out = final.get("tokens_in"), final.get("tokens_out")
                res.estimated = bool(final.get("tokens_estimated"))
                res.reply = final.get("content") or ""
                refs = FINDING_REF.findall(res.reply)
                res.refs_valid = sum(1 for x in refs if x in known)
                res.refs_invented = sorted({x for x in refs if x not in known})
                res.proposals = [f"{p['op']} {p['section']}" for p in final.get("proposals") or []]
                calls = final.get("tool_calls") or []
                res.tools = [c["name"] + ("" if c["status"] == "ok" else " (error)") for c in calls]
                ok = final["status"] == "complete" and not final.get("error_kind")
                if check == "refs":
                    res.passed = ok and res.refs_valid > 0 and not res.refs_invented
                elif check == "notes-add":
                    res.passed = ok and "add decision" in res.proposals
                elif check == "tools":
                    # Finished, at least one call that ran, and no invented finding.
                    res.passed = (ok and any(c["status"] == "ok" for c in calls)
                                  and not res.refs_invented)
                else:
                    res.passed = ok and "remove open_item" in res.proposals
                results.append(res)
    return probe, results


def report(cfg: dict, probe: dict, results: list[Result]) -> str:
    verdict = {True: "passed", False: "failed", None: "no verdict"}[probe.get("ok")]
    lines = [
        f"# Analyst trial: `{cfg['model'] or '(provider default)'}`",
        "",
        f"Provider: {cfg['base_url']} · Max tokens {cfg['max_tokens']}",
        "",
        f"Tool-call probe: **{verdict}** in {probe['seconds']:.1f} s. {probe.get('detail', '')}",
        "",
        "| Conversation | Passed | Status | Seconds | Tokens in / out | Valid refs | Invented refs | Proposals | Tool calls |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        tokens = f"{r.tokens_in} / {r.tokens_out}{' (est.)' if r.estimated else ''}" if r.tokens_in is not None else "—"
        lines.append(
            f"| {r.name} | {'yes' if r.passed else 'no'} | {r.status} | {r.seconds:.1f} | {tokens} | "
            f"{r.refs_valid} | {', '.join(r.refs_invented) or '—'} | {', '.join(r.proposals) or '—'} | "
            f"{', '.join(r.tools) or '—'} |"
        )
    lines.append("")
    for r in results:
        lines += [f"## {r.name}", ""]
        if r.error:
            lines += [f"Error: {r.error}", ""]
        lines += ["```text", r.reply.strip() or "(no text)", "```", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-db", metavar="NAME", help="a provider saved in the dev database, by name")
    ap.add_argument("--base-url")
    ap.add_argument("--model", help="the model; overrides the provider's default")
    ap.add_argument("--api-key-env", metavar="VAR", help="read the API key from this environment variable")
    ap.add_argument("--max-tokens", type=int, help="the reply budget (default: the provider's, else 4096)")
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between conversations, for a rate-limited tier")
    ap.add_argument("--out", type=Path, help="also write the report here")
    ap.add_argument("--probe-only", action="store_true", help="run the tool-call probe and stop")
    args = ap.parse_args()

    cfg = provider_from_db(args.from_db) if args.from_db else {"base_url": "", "api_key": "", "model": "", "max_tokens": None}
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.model:
        cfg["model"] = args.model
    if args.api_key_env:
        cfg["api_key"] = os.environ.get(args.api_key_env, "")
    cfg["max_tokens"] = args.max_tokens or cfg["max_tokens"] or 4096
    if not cfg["base_url"]:
        sys.exit("Give --from-db or --base-url.")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the report's · and — on a Windows console
    os.chdir(REPO / "backend")  # as check_migrations.py does: some migrations import backend.*
    probe, results = asyncio.run(trial(cfg, args.pause, args.probe_only))
    text = report(cfg, probe, results)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"probe": probe.get("ok"), "passed": sum(r.passed for r in results), "of": len(results)}))


if __name__ == "__main__":
    main()
