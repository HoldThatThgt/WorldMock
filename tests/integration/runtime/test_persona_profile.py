
from tests.data_paths import ENVIRONMENT_ROOT
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.input_models import PersonaConfig
from tests.integration.runtime.current_contract_fixtures import inputs, profile as current_profile, compiled_persona
from worldmock.run_setup import creation_inputs


SCENARIO = ENVIRONMENT_ROOT
TRAITS = ('trait_source_kind',)


def test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer():
    """@brief 真实画像输入经宿主人设层与提示组装保留散文，不依赖其他用例的旧世界观察。"""
    from worldmock.agents.life_persona import persona_layer
    from worldmock.agents.life_prompt import life_user_layers, system_message
    from tests.profile.conftest import make_card
    from worldmock.profile.textgen import render_card

    frozen = inputs()
    card = make_card()
    specification = creation_inputs(frozen, {1: card})[0][0]
    profile = specification["profile"]
    person = frozen.persona
    persona = persona_layer(profile, person.name, {"relationships": [], "preferences": []})
    layer_names = (("tool_layer", "工具层"), ("memory_layer", "记忆层"), ("todo_layer", "待办层"),
                   ("environment_layer", "环境层"), ("progress_layer", "进度层"))
    observation = {key: {"layer": name} for key, name in layer_names}
    user = json.dumps(life_user_layers(observation, persona), ensure_ascii=False)
    system = system_message(False)["content"]
    assert persona["identity"] == {"name": person.name, "age": 32, "occupation": "产品经理",
                                   "living_arrangement": "与伴侣合租两居室"}
    assert [(row["person"], row["role"]) for row in persona["relationships"]] == [("母亲", "家人"), ("老周", "老友")]
    assert all(row["closeness"] is None and row["channels"] == [] for row in persona["relationships"][2:])
    assert "你不是AI助手" not in system and person.name not in system
    assert render_card(card) not in user and card.situation.busy_this_week not in user
    assert persona["tastes"] == card.tastes.model_dump(mode="json")
    for stale in ('"profile"', '"trait_source_kind"', '"persona_card"', '"persona_card_struct"', '"situation"',
                  '"taste_weights"', '"attention_prior"'):
        assert stale not in user


@pytest.mark.parametrize('field', ('occupation', 'routine_preference',
                                   'consumption_preference', 'risk_preference'))
def test_deleted_template_fields_stay_out_of_documents(field):
    """docs/design/06-agent.md「画像编译」：四个模板字段删除；文档中再次出现即被封闭模型拒绝。"""
    value = inputs().persona.model_dump(mode="json")
    value[field] = 'legacy'
    with pytest.raises(ValidationError):
        PersonaConfig.model_validate(value)


def test_initial_agent_state_and_profile_do_not_keep_numeric_tastes():
    """@brief 真实初态与画像输入均无口味权重；严格模型也拒绝重新注入旧字段。"""
    from tests.profile.conftest import make_card
    from worldmock.life.models import AgentState
    from worldmock.life.world_setup import _initial_agent

    frozen = inputs()
    compiled = compiled_persona(frozen)
    state = _initial_agent(frozen, compiled.world_dynamics, int(frozen.config.start_time.timestamp()))
    profile = current_profile()
    assert "taste_weights" not in state.model_dump(mode="json") and "taste_weights" not in profile
    assert "attention_prior" not in json.dumps(profile)
    with pytest.raises(ValidationError, match="taste_weights"):
        AgentState.model_validate({**state.model_dump(mode="python"), "taste_weights": {"content": 1.0}})


@pytest.mark.parametrize("profile", [{"taste_weights": {}}, {"taste_weights": {"content": 1.0},
                                                              "persona_card_struct": None}])
def test_restored_persona_profile_rejects_retired_taste_weights(profile):
    """@brief 恢复画像校验直接拒绝旧数值口味，即使没有结构化卡片也不保留。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    with pytest.raises(IntegrityError, match="retired taste weights"):
        _validate_persona_card_struct(profile)


@pytest.mark.parametrize("profile", [None, {}, {"persona_card_struct": None},
                                   {"persona_card_struct": {}}, {"persona_card_struct": []}])
def test_restore_requires_a_valid_frozen_persona_card(profile):
    """@brief 缺失、空值与损坏卡片都拒绝恢复，不用正文补齐卡片。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    with pytest.raises(IntegrityError, match="Restored persona card structure is invalid"):
        _validate_persona_card_struct(profile)


def test_creation_requires_a_compiled_persona_card():
    """@brief 新建人物在工作区写入之前拒绝缺失的画像编译结果。"""
    from worldmock.errors import IntegrityError

    frozen = inputs()
    with pytest.raises(IntegrityError, match="Compiled persona card is missing"):
        creation_inputs(frozen, {})


def test_valid_frozen_persona_card_can_be_restored_without_compilation(monkeypatch):
    """@brief 实际新建输入的卡片在恢复时验证原结构，不请求编译模型。"""
    from tests.profile.conftest import make_card
    from worldmock.runtime import _validate_persona_card_struct

    frozen = inputs()
    profile = current_profile()
    monkeypatch.setattr("worldmock.profile.compile.compile_persona",
                        lambda *args, **kwargs: pytest.fail("Restoring must not compile a persona"))
    _validate_persona_card_struct(profile, frozen.persona)
