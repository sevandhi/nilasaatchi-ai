"""LangGraph StateGraph over RunState. Control flow is generic: no node branches on a tool or domain name.

intake -> planner -> dispatch -> execute -> (recover -> dispatch ...) -> verify -> critic -> judge
       -> (REROUTE: recover -> dispatch ... max 2) -> present -> END
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.critic import critic
from app.agent.nodes.execute import dispatch, execute
from app.agent.nodes.intake import intake
from app.agent.nodes.judge import judge
from app.agent.nodes.planner import planner
from app.agent.nodes.present import present
from app.agent.nodes.recover import recover
from app.agent.nodes.verify import verify
from app.agent.state import RunState

RECURSION_LIMIT = 80


def _route(*allowed: str):
    def f(state: RunState) -> str:
        n = state.next_node or "end"
        return n if n in allowed else "end"
    return f


def build_graph() -> StateGraph:
    g = StateGraph(RunState)
    for name, fn in (("intake", intake), ("planner", planner), ("dispatch", dispatch), ("execute", execute),
                     ("recover", recover), ("verify", verify), ("critic", critic), ("judge", judge),
                     ("present", present)):
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _route("planner", "end"), {"planner": "planner", "end": END})
    g.add_conditional_edges("planner", _route("dispatch", "end"), {"dispatch": "dispatch", "end": END})
    g.add_edge("dispatch", "execute")
    g.add_conditional_edges("execute", _route("recover", "verify"), {"recover": "recover", "verify": "verify",
                                                                      "end": END})
    g.add_conditional_edges("recover", _route("dispatch", "verify"), {"dispatch": "dispatch", "verify": "verify",
                                                                      "end": END})
    g.add_edge("verify", "critic")
    g.add_edge("critic", "judge")
    g.add_conditional_edges("judge", _route("recover", "present"), {"recover": "recover", "present": "present",
                                                                    "end": END})
    g.add_edge("present", END)
    return g


_MEMORY = None
_PG_READY = False


def _memory_saver():
    global _MEMORY
    if _MEMORY is None:
        from langgraph.checkpoint.memory import InMemorySaver
        _MEMORY = InMemorySaver()
    return _MEMORY


def checkpoint_dsn() -> str | None:
    if os.environ.get("AGENT_CHECKPOINTER", "").lower() == "memory":
        return None
    return os.environ.get("CHECKPOINT_DSN") or os.environ.get("DATABASE_URL")


@asynccontextmanager
async def checkpointer():
    """Postgres checkpointer (thread_id = run_id); in-memory when disabled or the DB is unreachable."""
    global _PG_READY
    dsn = checkpoint_dsn()
    if dsn:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
                if not _PG_READY:
                    await saver.setup()
                    _PG_READY = True
                yield saver, "postgres"
                return
        except Exception as e:
            if "GeneratorExit" in type(e).__name__:
                raise
            import logging
            logging.getLogger("app.agent").warning("postgres checkpointer unavailable: %s", e)
    yield _memory_saver(), "memory"


def mermaid() -> str:
    return build_graph().compile().get_graph().draw_mermaid()
