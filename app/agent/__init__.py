"""NilaSaatchi agent core. Public contract: app/agent/CONTRACT.md.

    from app.agent import run_request, resume_run, get_run_state, load_output, json_schemas, CONTRACT_VERSION
"""
from app.agent.state import CONTRACT_VERSION, json_schemas


async def run_request(*a, **kw):
    from app.agent.api import run_request as _r
    async for ev in _r(*a, **kw):
        yield ev


async def resume_run(*a, **kw):
    from app.agent.api import resume_run as _r
    async for ev in _r(*a, **kw):
        yield ev


def get_run_state(run_id: str):
    from app.agent.api import get_run_state as _g
    return _g(run_id)


def load_output(ref: str) -> dict:
    from app.agent.store import load_output as _l
    return _l(ref)


__all__ = ["CONTRACT_VERSION", "get_run_state", "json_schemas", "load_output", "resume_run", "run_request"]
