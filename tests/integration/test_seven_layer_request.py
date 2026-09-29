"""七层请求与单一行动阶段的宿主验收（docs/dev/SPEC-persona-prompt.md 第一章「请求组成」、第二章「单一行动阶段」）。

宿主部分：system 为逐字节不变的系统层 JSON，user 为固定层序的六层 JSON 数组，原生工具整次运行不变、只写用途、没有
逐轮枚举；没有记忆查询的决策恰好一次模型请求，没有自述请求。引擎部分（真实 LifeEngine 与 SQLite）：decision_started
与首个行动请求同批提交且排在前，data 为闭集；没有模型证据时兜底写入；action_requested.data.self_note 取回复正文；
观察事件不存进度层快照。
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import _MEMORY_TOOL_MODELS, LifeAgent
from custom.agents.life_prompt import (LIFE_SYSTEM_LAYER, TOOL_DESCRIPTIONS, life_user_layers, system_message,
                                       user_message)
from tests.integration.test_life_agent_protocol import (
    _Environment,
    _GroundedCorrectionDispatcher,
    _MEMORY_TOOL_NAMES,
    _ModelResponse,
    _TASK,
    _WAIT,
    _WAIT_CHOICE,
    _WORK,
    _context,
    _observations,
    _wire_agent,
    _with_activities,
)
from tests.life.conftest import context, engine_factory as _engine_factory, stage_day_plan, start_request  # noqa: F401
from worldmock.life import prompt_capacity
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PENDING
from worldmock.life.models import REQUEST_MODELS, deepseek_strict_schema
from worldmock.life.narration import render_layer_json

#: 人物档案中的两位场景联系人（称谓与关系）；亲密度与渠道不进提示。
_CONTACTS = [{"contact_id": "contact-wang", "salutation": "王磊", "relation": "同事", "closeness": 0.8,
              "channels": ["wechat"]},
             {"contact_id": "contact-mom", "salutation": "妈妈", "relation": "母亲", "closeness": 0.9,
              "channels": ["phone"]}]
_LAYER_ORDER = ["工具层", "人设层", "记忆层", "待办层", "环境层", "进度层"]
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求合同。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)


def _agent(dispatcher, max_turns: int = 4) -> tuple[LifeAgent, _Environment]:
    """装配带场景联系人与人物卡片文本、没有结构化卡片的生活人物（人设层不读卡片文本）。"""
    agent, environment = LifeAgent(), _Environment()
    _wire_agent(agent, environment, dispatcher, max_turns, memory=True)
    agent._profile = {"id": 7, "name": "Alice", "contacts": copy.deepcopy(_CONTACTS),
                      "persona_card": "你是Alice，在张江做工程师。", "background_story": "在上海长大。"}
    return agent, environment


async def _run(agent: LifeAgent, observations: list[dict]) -> str:
    """执行一次生活决策。"""
    return await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, observations))


def _second_observation() -> list[dict]:
    """同一人物另一时刻的观察：一项进行中的工作、一项刚结束的等待，进度层有一条记录。"""
    observations = _observations()
    data = _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING))
    data["progress_layer"]["events"] = [{"occurred_at": "2026-01-04T23:45:00+00:00", "type": "tool_call",
                                         "tool": "start_activity", "arguments": _WAIT_CHOICE, "self_note": "歇会儿。",
                                         "result": {"occurred_at": "2026-01-04T23:45:00+00:00",
                                                    "status": "in_progress", "error_code": None,
                                                    "message": "Activity started", "details": {}}}]
    data["progress_meta"] = [{"protected": False, "lines": []}]
    return observations


@pytest.mark.asyncio
async def test_system_layer_is_byte_identical_across_turns_and_decisions() -> None:
    """同一决策的多个回合、不同决策之间，system 都是同一份紧凑系统层 JSON；原生工具逐字节相同。"""
    first = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("memory", "memory_recent", '{"limit":1}'),
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    second = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "conclude_activity", json.dumps(
            {"task": [_WAIT], "summary": ["等完了。"], "memories": [[]]}, ensure_ascii=False))])
    agent, _ = _agent(first)
    await _run(agent, _observations())
    agent._dispatcher = second
    await _run(agent, _second_observation())

    requests = first.requests + second.requests
    assert len(requests) == 3
    assert {item["messages"][0]["content"] for item in requests} == {render_layer_json(LIFE_SYSTEM_LAYER)}
    assert json.loads(requests[0]["messages"][0]["content"]) == LIFE_SYSTEM_LAYER
    assert len({json.dumps(item["tools"], ensure_ascii=False) for item in requests}) == 1
    assert first.requests[0]["messages"][1] == first.requests[1]["messages"][1]
    assert first.requests[0]["messages"][1] != second.requests[0]["messages"][1]


def _contract_system_layers() -> list[dict]:
    """接口合同「请求组成」逐字登记的系统层全文：生活决策在前，只读问答在后。"""
    text = (_REPO_ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(\{\n  \"layer\": \"系统层\",.*?\n\})\n```", text, flags=re.DOTALL)
    return [json.loads(block) for block in blocks]


def _spec_life_system_layer() -> tuple[str, list[str]]:
    """本规格第一章「系统层」定稿的生活决策 role 与逐条 rules。"""
    text = (_REPO_ROOT / "docs" / "dev" / "SPEC-persona-prompt.md").read_text(encoding="utf-8")
    section = text.split("\n### 系统层\n", 1)[1].split("\n### 工具层\n", 1)[0]
    role = re.search(r"^`role`：(.+)$", section, flags=re.MULTILINE)
    assert role is not None
    return role.group(1), re.findall(r"^\d+\. (.+)$", section, flags=re.MULTILINE)


@pytest.mark.asyncio
async def test_system_layers_are_the_contract_registration_verbatim() -> None:
    """系统层文案与接口合同「请求组成」的登记逐字相同（docs/dev/SPEC-persona-prompt.md 验收台账「系统层与工具」）：真实生活
    决策请求的 system、只读问答的 system 各是合同登记全文的紧凑序列化，键依次为 layer、role、rules；生活决策的 role 与
    十八条 rules 也与本规格第一章定稿逐字相同（docs/dev/SPEC-dayrun-fidelity.md 2.2：新第 14 条，第 15、16 条改写，
    第 4、12、18 条补句）。"""
    life, readonly = _contract_system_layers()
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    [request] = dispatcher.requests
    assert request["messages"][0]["content"] == render_layer_json(life)
    assert system_message(True)["content"] == render_layer_json(readonly)
    assert list(life) == list(readonly) == ["layer", "role", "rules"]
    role, rules = _spec_life_system_layer()
    assert (life["role"], life["rules"]) == (role, rules) and len(rules) == 18


@pytest.mark.asyncio
async def test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it() -> None:
    """系统层第 14 条（docs/dev/SPEC-dayrun-fidelity.md 2.2、2.3，裁决 D6、S3、S11）：真实生活决策请求的系统层在第 13 条之后
    写作息默认规则——平常照作息，今日计划写明今天与平常不同的安排时按计划，身体的感觉到更强的一档才改作息；饥饿、困意两条
    顺延为第 15、16 条，按档位与饭点吃、照就寝时间睡；第 4 条补「消息只核验对方说过」，第 18 条补「联系人不会来到身边」
    （7.3）。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    rules = json.loads(dispatcher.requests[0]["messages"][0]["content"])["rules"]
    assert len(rules) == 18 and rules == LIFE_SYSTEM_LAYER["rules"]
    assert rules[12].startswith("obtain 与 consume") and rules[13].startswith("人设层的作息和习惯是你平常的过法")
    for sentence in ("到了平常的钟点、在平常的地方，就照平常做", "今日计划写明今天与平常不同的安排时，按计划",
                     "身体的感觉没到更强的一档，就不为它改作息", "人设层没写作息，或者今天不在平常的环境里，就按身体的感觉来"):
        assert sentence in rules[13]
    assert rules[14].startswith("你会饿") and "每个饭点的正餐只吃一次" in rules[14] and "趁早" not in rules[14]
    assert rules[15].startswith("晚上照人设层作息里的就寝时间去睡") and "困了就去睡" not in rules[15]
    assert rules[3].endswith("消息只核验了对方这样说过，说已经做了、正在赶来，都不等于发生了。")
    assert rules[17].endswith("联系人只能发消息或通话，不会来到你身边。")
    assert "最接近的候选，不会替你改" in rules[11]


#: 系统层第 15 条用「」点名的饥饿档与第 16 条列举的困意档（docs/dev/SPEC-dayrun-fidelity.md 2.2：档位名与 felt.py 的
#: 分级文本逐字一致；仲裁档饥饿取「很饿」、困意取「很困」）。
_RULE_HUNGER_BANDS = ["有点想吃", "饿", "很饿"]
_RULE_SLEEPINESS_BANDS = ["有点乏", "困", "很困", "撑不住"]


def _felt_vocabulary() -> tuple[set[str], set[str]]:
    """felt.py 分级表里的饥饿档与困意档文本（含基档与只由能量决定的第六档），测试不自带词表。"""
    from worldmock.life import felt

    hunger = {name for _, name in felt.APPETITE_BANDS} | {felt.APETITE_BASE_LEVEL, felt.APPETITE_STARVING_LEVEL}
    sleepiness = {name for _, name in felt.SLEEPINESS_BANDS} | {felt.SLEEPINESS_BASE_LEVEL}
    return hunger, sleepiness


def _rule_band_mentions(rules: list[str]) -> tuple[list[str], list[str]]:
    """规则点名的档：第 15 条的「」引语；第 16 条「X、Y是睡前的常态」与「X或Y了可以提前」两处列举。"""
    hunger = re.findall(r"「([^」]+)」", rules[14])
    usual = re.search(r"：([^：；]+)是睡前的常态", rules[15])
    early = re.search(r"；([^；]+)了可以提前", rules[15])
    assert usual is not None and early is not None
    return hunger, usual.group(1).split("、") + early.group(1).split("或")


def test_felt_bands_named_by_the_rules_are_the_felt_band_texts() -> None:
    """档位名与 felt.py 分级文本逐字一致（docs/dev/SPEC-dayrun-fidelity.md 2.2 验收）：词表取自 felt.py 的分级表；第 15 条点名的
    饥饿档是饥饿分级表的前三档，第 16 条依次列出困意分级表的全部四个非基档；把规则里的档改成分级表没有的说法会被抓住。"""
    from worldmock.life import felt

    hunger_vocabulary, sleepiness_vocabulary = _felt_vocabulary()
    rules = LIFE_SYSTEM_LAYER["rules"]
    hunger, sleepiness = _rule_band_mentions(rules)
    assert (hunger, sleepiness) == (_RULE_HUNGER_BANDS, _RULE_SLEEPINESS_BANDS)
    assert set(hunger) <= hunger_vocabulary and set(sleepiness) <= sleepiness_vocabulary
    assert hunger == [name for _, name in felt.APPETITE_BANDS][:3]
    assert sleepiness == [name for _, name in felt.SLEEPINESS_BANDS]
    poisoned = list(rules)
    poisoned[14] = poisoned[14].replace("「很饿」", "「非常饿」")
    poisoned[15] = poisoned[15].replace("撑不住了", "熬不住了")
    stale_hunger, stale_sleepiness = _rule_band_mentions(poisoned)
    assert not set(stale_hunger) <= hunger_vocabulary and not set(stale_sleepiness) <= sleepiness_vocabulary


@pytest.mark.asyncio
async def test_user_content_is_one_json_array_in_the_fixed_layer_order() -> None:
    """user 是六个层对象组成的一个 JSON 数组：环境五层原样取自观察，人设层由宿主构建；没有场景、事实与身份前言。"""
    observations = _second_observation()
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, observations)

    [request] = dispatcher.requests
    assert [item["role"] for item in request["messages"]] == ["system", "user"]
    user = request["messages"][1]["content"]
    layers = json.loads(user)
    assert [layer["layer"] for layer in layers] == _LAYER_ORDER
    data = observations[0]["data"]
    assert layers == life_user_layers(data, layers[1])
    assert request["messages"][1] == user_message(layers)
    assert all(list(layer)[0] == "layer" for layer in layers)
    text = request["messages"][0]["content"] + user
    for retired in ('"scene"', '"facts"', "你不是AI助手", "怎样过一天", "事实清单", "Authoritative", "budget",
                    "剩余回合", "你刚才心里想的是", "在上海长大", "在张江做工程师"):
        assert retired not in text


def test_no_card_persona_layer_keeps_the_name_and_scene_contacts() -> None:
    """没有结构化卡片的人设层：只填姓名，其余身份字段、作息、口味、应对为 null，习惯为空；关系逐项来自场景联系人（亲密度 null、
    渠道为空），看法与偏好取自环境下发的 persona_views。"""
    observations = _observations()
    data = observations[0]["data"]
    data["persona_views"] = {"relationships": [{"person": "王磊", "view": "做事靠谱"},
                                               {"person": "王磊", "view": "最近很忙"}],
                             "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": "2026-01-04"}]}
    agent, _ = _agent(_GroundedCorrectionDispatcher(responses=[]))
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    persona = agent._life_budget.layers[1]

    assert persona == {
        "layer": "人设层",
        "identity": {"name": "Alice", "age": None, "occupation": None, "living_arrangement": None},
        "rhythm": None, "habits": [], "tastes": None, "coping": None,
        "relationships": [
            {"person": "王磊", "role": "同事", "closeness": None, "channels": [], "view": "最近很忙"},
            {"person": "妈妈", "role": "母亲", "closeness": None, "channels": [], "view": None}],
        "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": "2026-01-04"}],
    }


@pytest.mark.asyncio
async def test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn() -> None:
    """原生工具：八个人物工具（REQUEST_MODELS 顺序）加三个记忆查询，description 只写用途，没有逐轮枚举；
    只剩最后一个回合时去掉记忆查询，其余字节不变。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("memory", "memory_recent", '{"limit":1}'),
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher, max_turns=2)
    await _run(agent, _second_observation())

    first, last = (item["tools"] for item in dispatcher.requests)
    assert [item["function"]["name"] for item in first] == [*REQUEST_MODELS, "memory_recent", "memory_search",
                                                            "memory_time_range"]
    assert last == first[:8]
    assert all(item["function"]["description"] == TOOL_DESCRIPTIONS[item["function"]["name"]] for item in first)
    models = {**REQUEST_MODELS, **_MEMORY_TOOL_MODELS}
    assert all(item["function"]["parameters"] == deepseek_strict_schema(models[item["function"]["name"]])
               for item in first)
    for name in ("targets", "actions", "ways"):
        assert "enum" not in first[0]["function"]["parameters"]["properties"][name]["items"]
    assert _MEMORY_TOOL_NAMES.isdisjoint(item["function"]["name"] for item in last)


@pytest.mark.asyncio
async def test_a_decision_without_memory_queries_sends_exactly_one_request_and_no_prelude() -> None:
    """没有记忆查询的决策恰好一次模型请求，参数符合行动行（思考开启、auto、8000）；登记后不追加 tool 消息。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "submit_work", json.dumps(
            {"task_name": _TASK, "deliverable": {"summary": "要点。", "decisions": ["推进。"],
                                                 "action_items": ["复盘。"]}}, ensure_ascii=False))])
    agent, environment = _agent(dispatcher)
    assert await _run(agent, _observations()) == "submit_work registered"

    [request] = dispatcher.requests
    assert [item.phase for item in environment.evidence] == ["request", "response"]
    assert (request["tool_choice"], request["max_tokens"], request["extra_body"]) == (
        "auto", 8000, {"thinking": {"type": "enabled"}})
    assert "temperature" not in request and len(environment.calls) == 1
    assert [item["role"] for item in agent._react_messages] == ["system", "user", "assistant"]


def _action_request() -> dict:
    """一个行动请求的 wire 形状（没有 purpose、带 start_activity、思考开启）。"""
    return {"model": "deepseek-flash", "messages": [{"role": "system", "content": "系统层"}],
            "tools": [{"type": "function", "function": {"name": "start_activity"}}],
            "extra_body": {"thinking": {"type": "enabled"}}}


def _reply(content: str | None) -> dict:
    """一次模型响应：回复正文与一个世界请求工具调用。"""
    return {"choices": [{"message": {"content": content, "tool_calls": [
        {"id": "call", "type": "function", "function": {"name": "start_activity", "arguments": "{}"}}]}}]}


def _batches(engine, monkeypatch) -> list[list[str]]:
    """记下每次提交的事件类型批次。"""
    batches: list[list[str]] = []
    original = engine.store.commit

    def commit(state, drafts):
        batches.append([draft.event_type for draft in drafts])
        return original(state, drafts)

    monkeypatch.setattr(engine.store, "commit", commit)
    return batches


def _opened(engine, round_id: str):
    """开启单人决策轮并观察。"""
    stage_day_plan(engine)
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    return decision


def _events(engine, event_type: str) -> list:
    """全部某类事件。"""
    return [event for event in engine.store.read_events(engine.store.last_event_seq) if event.event_type == event_type]


def test_decision_started_is_committed_with_and_before_the_first_action_request(_engine_factory, monkeypatch):
    """首个行动请求与 decision_started 同一批提交、decision_started 在前，data 为闭集；同一决策后续请求与登记不再写。"""
    engine = _engine_factory()
    decision = _opened(engine, "first-action")
    batches = _batches(engine, monkeypatch)
    for index, (phase, payload) in enumerate((("request", None), ("response", _reply("")), ("request", None))):
        call_id = f"{decision.decision_id}:model:{index // 2 + 1:04d}:attempt:1"
        engine.record_model_exchange(ModelCallEvidence(decision, call_id, phase, 1, _action_request(),
                                                       response=payload))
    engine.submit_action(DirectToolCall(decision, "action-first", "start_activity", start_request(
        **_WAIT_CHOICE)))

    assert batches == [["decision_started", "model_request"], ["model_response"], ["model_request"],
                       ["action_requested"]]
    [start] = _events(engine, "decision_started")
    first_request = _events(engine, "model_request")[0]
    assert (start.event_seq + 1, start.state_version_after) == (first_request.event_seq,
                                                                 first_request.state_version_after)
    assert start.data == {"round_id": "first-action", "round_order": 0, "world_state_version": decision.world_state_version,
                          "wake_reasons": start.data["wake_reasons"]}
    assert start.decision_id == decision.decision_id and start.occurred_at == first_request.occurred_at


@pytest.mark.parametrize("request_shape", [
    {**_action_request(), "purpose": "work_review"},
    {**_action_request(), "tools": [{"type": "function", "function": {"name": "finish"}}]},
    {**_action_request(), "extra_body": {"thinking": {"type": "disabled"}}},
])
def test_non_action_requests_leave_the_start_to_the_engine_driven_fallback(_engine_factory, monkeypatch,
                                                                           request_shape):
    """工作验收、只读问答形状与思考关闭的请求都不带出 decision_started；没有行动请求时首个动作登记兜底写入。"""
    engine = _engine_factory()
    decision = _opened(engine, "fallback")
    batches = _batches(engine, monkeypatch)
    engine.record_model_exchange(ModelCallEvidence(decision, f"{decision.decision_id}:model:0001:attempt:1",
                                                   "request", 1, request_shape))
    engine.submit_action(DirectToolCall(decision, "action-fallback", "start_activity",
                                        start_request(**_WAIT_CHOICE)))

    assert batches == [["model_request"], ["decision_started", "action_requested"]]


def test_abort_flushes_the_pending_start_without_model_evidence(_engine_factory, monkeypatch):
    """引擎直驱中止：没有模型证据的决策在中止批次里补写 decision_started（闭集字段）。"""
    engine = _engine_factory()
    decision = _opened(engine, "aborted")
    batches = _batches(engine, monkeypatch)
    engine.abort_decision_round(decision.round_id, "injected failure")

    assert batches[0][0] == "decision_started"
    [start] = _events(engine, "decision_started")
    assert set(start.data) == {"round_id", "round_order", "world_state_version", "wake_reasons"}


@pytest.mark.parametrize(("contents", "note"), [
    (["  好，先去吃饭。 \n"], "好，先去吃饭。"),
    ([""], None),
    ([None], None),
    (["先查查昨天。", "   "], None),
    (["", "决定了。"], "决定了。"),
])
def test_self_note_is_the_latest_reply_body_of_the_decision(_engine_factory, contents, note):
    """self_note 取本决策最近一次模型响应的正文（去首尾空白），空或缺失为 null；只做记忆查询的回复正文不带入。"""
    engine = _engine_factory()
    decision = _opened(engine, "reply")
    for index, content in enumerate(contents, start=1):
        call_id = f"{decision.decision_id}:model:{index:04d}:attempt:1"
        engine.record_model_exchange(ModelCallEvidence(decision, call_id, "request", 1, _action_request()))
        engine.record_model_exchange(ModelCallEvidence(decision, call_id, "response", 1, _action_request(),
                                                       response=_reply(content)))
    engine.submit_action(DirectToolCall(decision, "action-reply", "start_activity",
                                        start_request(**_WAIT_CHOICE)))

    [requested] = _events(engine, "action_requested")
    assert "self_note" in requested.data and requested.data["self_note"] == note


def test_self_note_ignores_other_decisions_and_failed_calls(_engine_factory):
    """其他决策的回复正文与传输失败都不作为本次世界请求的 self_note；没有模型证据时为 null。"""
    engine = _engine_factory()
    decision = _opened(engine, "isolated")
    call_id = f"{decision.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(decision, call_id, "request", 1, _action_request()))
    engine.record_model_exchange(ModelCallEvidence(decision, call_id, "failure", 1, _action_request(),
                                                   error="TimeoutError()"))
    engine.submit_action(DirectToolCall(decision, "action-isolated", "start_activity",
                                        start_request(**_WAIT_CHOICE)))
    [requested] = _events(engine, "action_requested")
    assert requested.data["self_note"] is None


def test_observe_stores_the_body_without_the_host_only_progress_keys(_engine_factory, monkeypatch):
    """观察返回体带进度层快照与私有元数据交给宿主；观察事件存其余全部键，不存这两个键（提交后挂上的观察身份与续接链
    同样不存）。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    original = engine._observation_data
    progress = {"layer": "进度层", "session_started_at": "2026-01-05T00:00:00+08:00", "events": []}

    def with_progress(agent):
        return {**original(agent), "progress_layer": progress, "progress_meta": [], "progress_event_ids": []}

    monkeypatch.setattr(engine, "_observation_data", with_progress)
    decision = context(engine, "observed")
    engine.begin_decision_round([decision])
    returned = engine.observe(decision)

    [stored] = _events(engine, "observation")
    assert returned["progress_layer"] == progress and returned["progress_meta"] == []
    assert returned["observation_event_id"] == stored.event_id
    assert not {"progress_layer", "progress_meta", "observation_event_id", "continuation"} & set(stored.data)
    assert stored.data["progress_event_ids"] == []
    assert set(returned) - set(stored.data) == {"progress_layer", "progress_meta", "observation_event_id",
                                                "continuation"}


def test_system_layers_persona_memory_and_tools_validate_against_the_layer_schemas() -> None:
    """两份系统层、没有卡片的人设层（含场景联系人与看法偏好）、空记忆层与每个原生工具定义都通过七层 Schema。"""
    from jsonschema import Draft202012Validator

    from custom.agents.life_persona import persona_layer
    from custom.agents.life_prompt import READONLY_SYSTEM_LAYER, empty_memory_layer
    from worldmock.life.prompt_schemas import TOOL_DEFINITION_SCHEMA, validate_layer

    views = {"relationships": [{"person": "王磊", "view": "做事靠谱"}],
             "preferences": [{"subject": "午饭", "preference": "爱吃面", "noted_on": "2026-01-04"}]}
    for layer in (LIFE_SYSTEM_LAYER, READONLY_SYSTEM_LAYER, empty_memory_layer(),
                  persona_layer({"contacts": _CONTACTS}, "Alice", views),
                  persona_layer({}, "Alice", {"relationships": [], "preferences": []})):
        validate_layer(layer)
    agent, _ = _agent(_GroundedCorrectionDispatcher(responses=[]))
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))
    validator = Draft202012Validator(TOOL_DEFINITION_SCHEMA)
    for tool in agent.build_react_tool_schemas(readonly=False):
        validator.validate(tool)


def test_recorded_prompt_fingerprint_is_recomputed_from_the_eighteen_rules_and_the_around_time_schema(
        monkeypatch) -> None:
    """prompt_sha256 只计算、不钉常量，随系统层与待办层 Schema 重钉（docs/dev/SPEC-dayrun-fidelity.md 2.2、8.2）：新运行的
    模型身份记录的提示指纹就是按现行十八条系统层与带 around_time 的待办层 Schema 算出的值；去掉新第 14 条回到十七条、
    或去掉待办层目标的 around_time，指纹都不同。"""
    from custom.agents import life_prompt
    from worldmock.life import prompt_schemas
    from worldmock.runtime import _model_identity

    current = life_prompt.life_prompt_sha256()
    assert _model_identity().prompt_sha256 == current
    rules = [rule for index, rule in enumerate(LIFE_SYSTEM_LAYER["rules"]) if index != 13]
    monkeypatch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", {**LIFE_SYSTEM_LAYER, "rules": rules})
    assert len(rules) == 17 and life_prompt.life_prompt_sha256() != current
    monkeypatch.undo()
    todo = copy.deepcopy(prompt_schemas.TODO_LAYER_SCHEMA)
    goal = todo["properties"]["day_plan"]["oneOf"][1]["properties"]["goals"]["items"]
    assert goal["properties"].pop("around_time") and "around_time" in goal["required"]
    goal["required"].remove("around_time")
    monkeypatch.setattr(prompt_schemas, "LAYER_SCHEMAS", {**prompt_schemas.LAYER_SCHEMAS, "待办层": todo})
    assert life_prompt.life_prompt_sha256() != current


def test_prompt_fingerprint_binds_the_layer_schemas(monkeypatch) -> None:
    """提示指纹绑定七层 Schema：任何一层的 Schema 改动都改变运行指纹。"""
    from custom.agents.life_prompt import life_prompt_sha256
    from worldmock.life import prompt_schemas

    original = life_prompt_sha256()
    changed = {**prompt_schemas.LAYER_SCHEMAS, "人设层": {**prompt_schemas.LAYER_SCHEMAS["人设层"], "title": "改"}}
    monkeypatch.setattr(prompt_schemas, "LAYER_SCHEMAS", changed)
    assert life_prompt_sha256() != original

