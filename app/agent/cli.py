"""Headless runner: python -m app.agent.cli "request" [--attach FILE] [--domain D] [--run-id ID] [--jsonl]"""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
from pathlib import Path


async def _main(a) -> int:
    from app.agent import run_request
    atts = [{"id": f"att_{i + 1}", "name": Path(p).name, "path": str(Path(p).resolve()),
             "media_type": mimetypes.guess_type(p)[0]} for i, p in enumerate(a.attach or [])]
    rc = 1
    async for ev in run_request(a.request, atts, domain=a.domain, run_id=a.run_id):
        if a.jsonl:
            print(json.dumps(ev, ensure_ascii=False, default=str))
        else:
            d = ev["data"]
            brief = {k: d[k] for k in ("step_id", "status", "summary", "reason", "from", "to", "question", "message")
                     if k in d}
            print(f"{ev['seq']:>4} {ev['type']:<10} {ev['node']:<9} {json.dumps(brief, ensure_ascii=False)[:160]}")
        if ev["type"] == "result":
            rc = 0
            if not a.jsonl:
                print("\nNARRATIVE:", ev["data"]["state"]["narrative"]["en"])
        if ev["type"] == "clarify":
            rc = 0
    return rc


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("request")
    ap.add_argument("--attach", action="append")
    ap.add_argument("--domain")
    ap.add_argument("--run-id")
    ap.add_argument("--jsonl", action="store_true")
    return asyncio.run(_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
