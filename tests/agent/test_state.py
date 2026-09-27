import json

import pytest

from app.agent.nodes.planner import validate_plan
from app.agent.state import SCHEMA_DIR, ExecutionPlan, RunState, WorkspaceSpec, json_schemas
from app.domains import get_pack


def test_exported_schemas_are_current():
    for name, schema in json_schemas().items():
        p = SCHEMA_DIR / f"{name}.json"
        assert p.exists(), f"run `uv run python -m app.agent.state` ({name} missing)"
        assert json.loads(p.read_text()) == schema, f"{name}.json is stale: re-export"


def test_public_dict_excludes_private():
    st = RunState(run_id="r", request="q", private={"pseudo": {"reverse": {"⟨OWNER_1⟩": "Arumugam"}}})
    d = st.public_dict()
    assert "private" not in d and "Arumugam" not in json.dumps(d, ensure_ascii=False)


def test_minimal_workspace_valid():
    WorkspaceSpec(title="t", domain="d", run_id="r", created_at="2026-09-27T00:00:00Z")


REG = get_pack("land_acquisition").registry()


def _plan(steps):
    return ExecutionPlan.model_validate({"goal": "g", "steps": steps, "outputs": []})


def test_plan_validation_ok_and_ref_adds_dependency():
    plan, errs = validate_plan(_plan([
        {"id": "s1", "tool": "sql_query", "args": {"question": "q"}},
        {"id": "s2", "tool": "satellite_timeseries", "args": {"parcel_uids": "$s1.data.rows"}}]), REG)
    assert not errs and plan.steps[1].depends_on == ["s1"]


@pytest.mark.parametrize("steps,needle", [
    ([{"id": "s1", "tool": "no_such_tool", "args": {}}], "unknown tool"),
    ([{"id": "s1", "tool": "spatial_query", "args": {"distance_m": -3}}], "spatial_query"),
    ([{"id": "s1", "tool": "sql_query", "args": {"question": "a"}, "depends_on": ["s2"]},
      {"id": "s2", "tool": "sql_query", "args": {"question": "b"}, "depends_on": ["s1"]}], "cycle"),
    ([{"id": "s1", "llm_task": "write_poem", "args": {}}], "llm_task"),
    ([{"id": "s1", "tool": "compose_workspace", "args": {}}], "unknown tool"),
])
def test_plan_validation_errors(steps, needle):
    plan, errs = validate_plan(_plan(steps), REG)
    assert plan is None and any(needle in e for e in errs), errs


def test_plan_max_12_steps():
    with pytest.raises(Exception):
        _plan([{"id": f"s{i}", "tool": "sql_query", "args": {"question": "q"}} for i in range(13)])


def test_reference_resolver_is_tolerant_and_generic():
    from app.agent.nodes.execute import _resolve
    o = {"s1": {"data": {"findings": [{"finding_id": 5, "parcel_uid": "A|1"}, {"finding_id": 6, "parcel_uid": "A|2"}],
                         "total_matching": 2}}}
    assert _resolve("$s1.data.findings.0.finding_id", o) == 5
    assert _resolve("$s1.data.0.finding_id", o) == 5             # main-list key omitted
    assert _resolve("$s1.data[1].parcel_uid", o) == "A|2"         # bracket index
    assert _resolve("$s1.data.parcel_uids", o) == ["A|1", "A|2"]  # column pluck
    assert _resolve("${s1.data.total_matching}", o) == 2
    assert _resolve("$s1.data.nope", o) is None
