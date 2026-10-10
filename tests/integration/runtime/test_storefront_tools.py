"""真实目录供给与严格公开请求使用当前目标模型。"""
import pytest
from pydantic import ValidationError
from tests.engine_fixture import engine_factory
from tests.life.conftest import context
from worldmock.life.models import StartActivityRequest
from worldmock.life.prompt_schemas import validate_layer


def test_real_goods_targets_validate_against_the_production_tool_layer_schema(engine_factory):
    engine = engine_factory()
    observed = engine.observe(context(engine, "catalog"))
    validate_layer(observed["tool_layer"])
    assert any(row["match"]["kind"] == "product" for row in observed["world_query_bindings"])
    assert all(row["kind"] in {"location", "app", "product", "activity"} for row in observed["tool_layer"]["targets"])


@pytest.mark.parametrize("retired", ["browse", "wait", "cook", "use_app", "take_transit", "send_message"])
def test_retired_activity_actions_are_strictly_rejected(engine_factory, retired):
    from tests.life.conftest import stage_day_plan
    from tests.life.environment_runtime import act
    engine = engine_factory()
    stage_day_plan(engine)
    _, result = act(engine, "retired", StartActivityRequest(items=[{"action": retired, "target": "家"}]))
    assert result.status == "rejected" and result.error_code == "ACTION_REQUEST_INVALID"
    assert engine.context.state.activities == {}
