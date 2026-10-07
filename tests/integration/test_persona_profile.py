
from tests.data_paths import ENVIRONMENT_ROOT
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.life.scenario import LifeScenario
from worldmock.life.world_setup import load_scenario
from worldmock.run_setup import _agent_specs


SCENARIO = ENVIRONMENT_ROOT / 'scenarios/single-person.json'
TRAITS = ('trait_source_kind',)


def test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer():
    """@brief 真实画像输入经宿主人设层与提示组装保留散文，不依赖其他用例的旧世界观察。"""
    from custom.agents.life_persona import persona_layer
    from custom.agents.life_prompt import life_user_layers, system_message
    from tests.profile.conftest import make_card
    from worldmock.profile.textgen import render_card

    scenario, _ = load_scenario(SCENARIO)
    card = make_card()
    specification = _agent_specs(scenario, persona_compiler=lambda agent: card)[0]
    profile = specification["profile"]
    person = scenario.agents[0]
    persona = persona_layer(profile, person.name, {"relationships": [], "preferences": []})
    layer_names = (("tool_layer", "工具层"), ("memory_layer", "记忆层"), ("todo_layer", "待办层"),
                   ("environment_layer", "环境层"), ("progress_layer", "进度层"))
    observation = {key: {"layer": name} for key, name in layer_names}
    user = json.dumps(life_user_layers(observation, persona), ensure_ascii=False)
    system = system_message(False)["content"]
    assert persona["identity"] == {"name": person.name, "age": 32, "occupation": "产品经理",
                                   "living_arrangement": "与伴侣合租两居室"}
    assert [(row["person"], row["role"]) for row in persona["relationships"]] == [
        ("母亲", "家人"), ("老周", "老友"), *((contact.salutation, contact.relation) for contact in person.contacts)]
    assert all(row["closeness"] is None and row["channels"] == [] for row in persona["relationships"][2:])
    assert "你不是AI助手" not in system and person.name not in system
    assert render_card(card) not in user and card.situation.busy_this_week not in user
    assert persona["tastes"] == card.tastes.model_dump(mode="json")
    for stale in ('"profile"', '"trait_source_kind"', '"persona_card"', '"persona_card_struct"', '"situation"',
                  '"taste_weights"', '"attention_prior"'):
        assert stale not in user
    assert person.trait_source_kind == "experiment_assumption"


@pytest.mark.parametrize('field', TRAITS)
def test_persona_traits_cannot_be_silently_omitted(field):
    value = json.loads(SCENARIO.read_text(encoding='utf-8'))
    del value['agents'][0][field]
    with pytest.raises(ValidationError, match=field):
        LifeScenario.model_validate(value)


@pytest.mark.parametrize('field', ('occupation', 'routine_preference',
                                   'consumption_preference', 'risk_preference'))
def test_deleted_template_fields_stay_out_of_documents(field):
    """docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」：四个模板字段删除；文档中再次出现即被封闭模型拒绝。"""
    value = json.loads(SCENARIO.read_text(encoding='utf-8'))
    value['agents'][0][field] = 'legacy'
    with pytest.raises(ValidationError):
        LifeScenario.model_validate(value)


def test_initial_agent_state_and_profile_do_not_keep_numeric_tastes():
    """@brief 真实初态与画像输入均无口味权重；严格模型也拒绝重新注入旧字段。"""
    from tests.profile.conftest import make_card
    from worldmock.life.models import AgentState
    from worldmock.life.world_setup import _initial_agent

    scenario, _ = load_scenario(SCENARIO)
    config = scenario.agents[0]
    state = _initial_agent(config, scenario, "agent:1", int(scenario.start_at.timestamp()))
    profile = _agent_specs(scenario, persona_compiler=lambda agent: make_card())[0]["profile"]
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
