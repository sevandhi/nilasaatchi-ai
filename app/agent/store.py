"""Run store for step outputs (kept out of RunState so checkpoints stay small). ref = run://<run_id>/<step_id>."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.agent.state import REPO_ROOT


def runs_dir() -> Path:
    base = os.environ.get("AGENT_DATA_DIR") or os.environ.get("ROUTER_DATA_DIR") or str(REPO_ROOT / "data")
    return Path(base) / "runs"


def save_output(run_id: str, step_id: str, payload: Any) -> str:
    p = runs_dir() / run_id / "steps" / f"{step_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    return f"run://{run_id}/{step_id}"


def load_output(ref: str) -> dict:
    if not ref.startswith("run://"):
        raise ValueError(f"bad output ref {ref!r}")
    run_id, step_id = ref[len("run://"):].split("/", 1)
    p = runs_dir() / run_id / "steps" / f"{step_id}.json"
    return json.loads(p.read_text(encoding="utf-8"))
