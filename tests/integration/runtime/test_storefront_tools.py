"""真实目录供给与严格公开请求使用当前目标模型。"""
import pytest
from pydantic import ValidationError
from tests.engine_fixture import engine_factory
from tests.life.conftest import MAIN_TOOL, context, main_arguments, side_arguments
from worldmock.state.interaction.requests import StartMainActivityRequest, StartSideActivityRequest
from worldmock.agents.prompt.prompt_schemas import validate_layer


def test_real_goods_targets_validate_against_the_production_tool_layer_schema(engine_factory):
    engine = engine_factory()
    observed = engine.observe(context(engine, "catalog"))
    validate_layer(observed["tool_layer"])
    assert any(row["kind"] == "product" for row in observed["tool_layer"]["targets"])
    assert all(row["kind"] in {"location", "app", "product", "activity"} for row in observed["tool_layer"]["targets"])


@pytest.mark.parametrize("retired", ["browse", "wait", "cook", "use_app", "take_transit", "send_message",
                                     "walk", "drive", "take_taxi", "take_bus"])
def test_retired_activity_actions_are_strictly_rejected(engine_factory, retired):
    """已删除的动作（连同四个出行动作名）不在两个开始工具的枚举里：请求模型直接拒绝；绕过请求模型构造出来的请求由环境
    用同一个模型复核，以 ACTION_REQUEST_INVALID 拒绝，明细指向 action 并带完整的允许值，世界不变。"""
    from tests.life.conftest import stage_day_plan
    from tests.life.environment_runtime import act
    with pytest.raises(ValidationError):
        StartMainActivityRequest.model_validate(main_arguments(retired, "家", "测试休息"))
    with pytest.raises(ValidationError):
        StartSideActivityRequest.model_validate(side_arguments(retired, "抖音"))
    engine = engine_factory()
    stage_day_plan(engine)
    bypassed = StartMainActivityRequest.model_construct(action=retired, target="家", detail="测试休息")
    _, result = act(engine, "retired", bypassed, MAIN_TOOL)
    assert result.status == "rejected" and result.error_code == "ACTION_REQUEST_INVALID"
    [item] = result.data["items"]
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "action"] and item["written"] == retired
    assert item["allowed_values"] == ["travel", "interact", "sleep"]
    assert engine.context.state.activities == {}
