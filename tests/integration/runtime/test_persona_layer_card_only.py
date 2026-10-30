"""人设层只由人物卡片构建：收尾写下的看法与偏好只作观察私有证据，不进人设层；快照里的人设层就是卡片人设层；
LifeAgent 按进度层在最后取进度层。"""

import json
import logging

import pytest
from agentsociety2.agent.base import ReactLoopRequest

from tests.integration.runtime.current_contract_fixtures import ScriptedModel, environment, person, profile
from tests.life.conftest import advance_until_ready, conclude_request, context, do_at
from tests.life.environment_runtime import act
from worldmock.agents.prompt import life_prompt
from worldmock.agents.prompt.life_persona import PROMPT_SNAPSHOT_FILE, persona_layer
from worldmock.agents.prompt.life_prompt import user_layer
from worldmock.agents.prompt.narration import render_layer_json

#: 收尾写下的两条看法：对纸巾的偏好与对母亲（卡片上的人物）的关系看法。
_VIEWS = [[{"type": "preference", "about": "纸巾", "text": "喜欢柔软的纸巾。", "importance": 0.5, "keywords": ["纸巾"]},
           {"type": "relationship", "about": "母亲", "text": "她总惦记我吃饭。", "importance": 0.5,
            "keywords": ["母亲"]}]]


def _prepared(engine, label: str, workspace=None):
    """以此刻的真实观察准备一次生活决策，返回观察、准备好的 LifeAgent 与这次的循环请求。"""
    decision = context(engine, label)
    observed = engine.observe(decision)
    agent = person(environment(engine), decision, ScriptedModel([]))
    agent._workspace_root = workspace
    request = ReactLoopRequest(0, decision.observed_at,
                               [{"turn": 0, "action": "observe", "ok": True, "data": observed}])
    agent.prepare_react_loop(request)
    return observed, agent, request


def test_persona_layer_is_the_card_and_unchanged_by_conclusion_views(engine_factory):
    """收尾前后两次准备的人设层逐字节相同，就是卡片人设层；看法只在 persona_views 里，不进 user 消息的人设层；
    已知人物是卡片关系名与已有关系看法的人物。"""
    engine = engine_factory()
    _, started = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    _, before, _ = _prepared(engine, "before-views")
    name = engine.context.state.activities[started.activity_id].name
    _, concluded = act(engine, "conclude-views", conclude_request(name, views=_VIEWS), "conclude_activity")
    assert concluded.status == "completed"
    observed, after, request = _prepared(engine, "after-views")
    assert observed["persona_views"]["relationships"] == [{"person": "母亲", "view": "她总惦记我吃饭。"}]
    assert [row["preference"] for row in observed["persona_views"]["preferences"]] == ["喜欢柔软的纸巾。"]
    first = render_layer_json(user_layer(before._life_budget.layers, "人设层"))
    second = render_layer_json(user_layer(after._life_budget.layers, "人设层"))
    assert first == second == render_layer_json(persona_layer(profile(), "林舟"))
    user = json.loads(after.build_react_messages(request)[1]["content"])
    persona_text = render_layer_json(user_layer(user, "人设层"))
    assert "喜欢柔软的纸巾" not in persona_text and "她总惦记我吃饭" not in persona_text
    assert after._life_names["known_persons"] == frozenset({"母亲", "老周"})


def test_prompt_snapshot_holds_the_card_persona_layer(engine_factory, tmp_path):
    """有工作区时，一次生活决策保存的只读问答快照里的人设层就是卡片人设层。"""
    engine = engine_factory()
    _prepared(engine, "snapshot", tmp_path)
    snapshot = json.loads((tmp_path / PROMPT_SNAPSHOT_FILE).read_text(encoding="utf-8"))
    assert snapshot["persona"] == persona_layer(profile(), "林舟")


def test_progress_layer_must_be_the_last_user_layer(engine_factory, monkeypatch, caplog):
    """层数组不以进度层收尾时，LifeAgent 在任何模型调用之前以英文错误停止，错误日志写出最后一层的层名。"""
    from custom.agents import life_agent

    monkeypatch.setattr(life_agent, "life_user_layers",
                        lambda data, persona: list(reversed(life_prompt.life_user_layers(data, persona))))
    engine = engine_factory()
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError,
                                                       match="^Progress layer must be the last user layer$"):
        _prepared(engine, "reversed")
    assert "User layers end with 人设层 instead of 进度层" in caplog.text
