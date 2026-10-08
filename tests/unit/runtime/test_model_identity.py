"""@brief 模型运行身份的字段与完整性规则。"""

import copy

import pytest

from worldmock.errors import IntegrityError


def test_model_identity_freezes_complete_strict_endpoint_and_request() -> None:
    """运行身份记录完整节点 URL 和唯一 strict 请求参数。"""
    from worldmock.run_setup import _model_identity, stage_model_identity

    identity = _model_identity().model_dump(mode="json")

    assert identity["base_url"] == "https://api.deepseek.com/beta"
    assert identity["provider"] == "openai_compatible"
    assert identity["requested_model"] == "deepseek-flash"
    assert identity["tool_choice"] == "auto"
    assert identity["thinking"] == "enabled"
    assert identity["max_retries"] == 0
    assert "base_url_host" not in identity



def test_model_identity_top_level_tracks_life_agent_action_constants() -> None:
    """顶层 thinking/tool_choice 与 B2 导出的行动阶段常量同源。"""
    from worldmock.run_setup import _model_identity, stage_model_identity

    from custom.agents.life_agent import ACTION_THINKING, ACTION_TOOL_CHOICE

    identity = _model_identity()

    assert identity.thinking == ACTION_THINKING["thinking"]["type"]
    assert identity.tool_choice == ACTION_TOOL_CHOICE



def test_stage_model_identity_shape_matches_life_agent_constants() -> None:
    """阶段身份只有行动与工作验收两个阶段，行动参数与 LifeAgent 常量单一事实源一致。"""
    from worldmock.run_setup import _model_identity, stage_model_identity

    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        ACTION_MAX_TOKENS,
    )

    identity = stage_model_identity()
    stages = identity["stages"]

    assert set(stages) == {"action"}
    assert stages["action"] == {"thinking": ACTION_THINKING["thinking"]["type"],
                                "tool_choice": ACTION_TOOL_CHOICE, "max_tokens": ACTION_MAX_TOKENS,
                                "same_second_continuation": True}
    assert stage_model_identity(False)["stages"]["action"]["same_second_continuation"] is False
    assert identity["base_url"] == "https://api.deepseek.com/beta"
    assert identity["max_retries"] == 0 and identity["strict"] is True
    assert identity["reasoning_effort"] is None



def test_stage_model_identity_is_frozen_and_tamper_rejected() -> None:
    """阶段身份运行中不变更；任何篡改被一致性校验拒绝。"""
    from worldmock.run_setup import _model_identity, stage_model_identity
    from worldmock.run_setup import validate_stage_model_identity

    identity = stage_model_identity()

    assert stage_model_identity() == identity
    validate_stage_model_identity(identity)

    tampered = copy.deepcopy(identity)
    tampered["stages"]["action"]["max_tokens"] = 4000
    with pytest.raises(IntegrityError, match="stage action"):
        validate_stage_model_identity(tampered)

    wrong_endpoint = copy.deepcopy(identity)
    wrong_endpoint["base_url"] = "https://elsewhere.example/v1"
    with pytest.raises(IntegrityError, match="base_url"):
        validate_stage_model_identity(wrong_endpoint)

    reasoning = copy.deepcopy(identity)
    reasoning["reasoning_effort"] = "high"
    with pytest.raises(IntegrityError, match="reasoning_effort"):
        validate_stage_model_identity(reasoning)
