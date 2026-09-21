"""通过真实人物、LifeEnv、SQLite 与到达事件验证交互菜单端到端。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.test_decision_context_roundtrip import _decide
from tests.life.conftest import advance_until_ready, context, stage_day_plan
from tests.life.test_interaction_menu_runtime import _engine, _restrict, _travel_to_office
from custom.envs.life_env import LifeEnv
from worldmock.errors import IntegrityError


@pytest.mark.asyncio
async def test_real_person_requests_use_current_location_menu_and_next_round_results(tmp_path):
    """真实模型请求随到达后的冻结观察更新，允许动作经共同结算后才成为结果。"""
    engine = _engine(tmp_path, lambda document: _restrict(document, tools=("send_message",), location="office"))
    try:
        stage_day_plan(engine)
        environment = LifeEnv()
        environment.engine = engine
        first = context(engine, "home-person")
        engine.begin_decision_round([first])
        home, home_requests = await _decide(environment, first)
        home_tools = {item["function"]["name"] for item in home_requests[1]["tools"]}
        assert "send_message" in home_tools
        assert home["location_id"] == "home"
        assert not any(item.agent_id == 1 and item.status.value == "active"
                       for item in engine.state.activities.values())
        assert engine.commit_decision_round(first.round_id)[0].error_code is None
        assert advance_until_ready(engine)
        _travel_to_office(engine)
        assert advance_until_ready(engine)
        second = context(engine, "office-person")
        engine.begin_decision_round([second])
        office, office_requests = await _decide(environment, second)
        office_tools = {item["function"]["name"] for item in office_requests[1]["tools"]}
        assert office["location_id"] == "office"
        assert "send_message" not in office_tools
        assert "send_message" not in office["interaction_menu"]["allowed_tools"]
        assert office["facts"]["recent_action_results"]
        assert engine.commit_decision_round(second.round_id)[0].error_code is None
        observations = engine.store.read_agent_events("observation", 1)
        person_observations = [item for item in observations
                               if item.decision_id in {first.decision_id, second.decision_id}]
        assert len(person_observations) == 2
        assert [item.data["interaction_menu"] for item in person_observations] == [
            home["interaction_menu"], office["interaction_menu"]]
    finally:
        engine.close()


@pytest.mark.parametrize("person_menu", [False, True])
def test_resume_rejects_menu_changes_before_copying_a_branch(tmp_path, person_menu):
    """真实恢复入口在发布任何子分支工件之前拒绝地点或人物菜单变化。"""
    from tests.integration.test_host_branch_coverage import _parent_store
    from tests.life.test_scheduling import _document, _write_scenario
    from worldmock.runtime import ResumeRequest, _prepare_resume

    parent, checkpoint = _parent_store(tmp_path)
    parent.close()
    document = _document("white-collar-12-weeks.json")
    _restrict(document, tools=("purchase",), agent=0 if person_menu else None)
    changed = _write_scenario(document, tmp_path)
    destination = tmp_path / "child"
    with pytest.raises(IntegrityError, match="Checkpoint scenario identity differs"):
        _prepare_resume(ResumeRequest(Path.cwd(), changed, tmp_path / "parent",
                                      checkpoint.checkpoint_id, destination))
    assert not destination.exists()


def test_supply_fact_prompt_and_tools_reject_old_model_identity():
    """供给差异事实解释与工具 Schema 均冻结身份，旧提示或工具合同不能恢复。"""
    from custom.agents.life_agent import life_prompt_sha256, life_tool_schema_sha256
    from worldmock.runtime import (
        _model_identity, _require_runtime_identity, runtime_code_sha256, two_stage_model_identity,
    )

    assert life_prompt_sha256() == "dc43b5418e28e61709e48af2fad251189c518e2f39e6bf8df295339558451b65"
    old_prompt = "59f22d2f8aece81545169f2234446420ca8a0f5e4454e03b3f432816cea1ba5a"
    old_schema = "dd2c1490f5ad9306e95fe23828665b9f0338164cdeb2477fb009d3073a9b639e"
    assert life_tool_schema_sha256() != old_schema
    root = Path.cwd()
    manifest = {"model_identity": _model_identity().model_dump(mode="json"),
                "two_stage_model_identity": two_stage_model_identity(),
                "code_sha256": runtime_code_sha256(root)}
    _require_runtime_identity(manifest, root)
    for field, old_value in (("tool_schema_sha256", old_schema), ("prompt_sha256", old_prompt)):
        outdated = {**manifest, "model_identity": {**manifest["model_identity"], field: old_value}}
        with pytest.raises(IntegrityError, match="model identity differs"):
            _require_runtime_identity(outdated, root)
