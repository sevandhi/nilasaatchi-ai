"""CLI: python -m app.ledger verify [--run RUN_ID] [--json]   |   python -m app.ledger show --run RUN_ID"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    from dotenv import load_dotenv

    from .store import REPO_ROOT, get_ledger
    load_dotenv(REPO_ROOT / ".env", override=False)
    ap = argparse.ArgumentParser(prog="python -m app.ledger")
    ap.add_argument("cmd", choices=["verify", "show", "runs"])
    ap.add_argument("--run", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    led = get_ledger()
    if a.cmd == "runs":
        for r in led.runs():
            print(r)
        return 0
    if a.cmd == "show":
        for e in led.entries(a.run):
            print(json.dumps({"seq": e.seq, "node": e.node, "type": e.event_type, "hash": e.hash[:16]}))
        return 0
    rep = led.verify(a.run)
    if a.json:
        print(json.dumps({**rep.as_dict(), "backend": led.backend}))
    else:
        scope = f"run {a.run}" if a.run else f"{rep.runs_checked} run(s)"
        if rep.ok:
            print(f"ledger OK ({led.backend}): {scope}, {rep.entries_checked} entries, chain intact")
        else:
            fb = rep.first_bad
            print(f"ledger TAMPERED ({led.backend}): run {fb['run_id']} first bad seq={fb['seq']}: {fb['reason']}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
