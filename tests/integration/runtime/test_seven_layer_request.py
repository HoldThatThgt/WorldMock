"""七层请求与单一行动阶段的宿主验收（docs/design/06-agent.md 第一章「请求组成」、第二章「单一行动阶段」）。

宿主部分：system 为逐字节不变的系统层 JSON，user 为固定层序的六层 JSON 数组，原生工具整次运行不变、只写用途、没有
逐轮枚举；没有记忆查询的决策恰好一次模型请求，没有自述请求。引擎部分（真实 LifeEngine 与 SQLite）：decision_started
与首个行动请求同批提交且排在前，data 为闭集；没有模型证据时兜底写入；action_requested.data.self_note 取回复正文；
观察事件不存进度层快照。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ModelCallEvidence, ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import _MEMORY_TOOL_MODELS, LifeAgent
from worldmock.agents.life_prompt import (LIFE_SYSTEM_LAYER, READONLY_SYSTEM_LAYER, TOOL_DESCRIPTIONS, life_user_layers, system_message,
                                       user_message)
from tests.integration.runtime.test_life_agent_protocol import (
    _Environment,
    _ModelResponse,
    _WAIT,
    _WORK,
    _context,
    _wire_agent,
    _with_activities,
)
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan, start_request)  # noqa: F401
from tests.life.action_contract_world import contract_world
from tests.integration.runtime.test_life_agent_protocol import _layers
from worldmock.footprints.models import EventDraft
from worldmock.life.tool_groups import compact_render, expand_tool_layer
from worldmock.life.world_query import QueryWorldRequest
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
_REPO_ROOT = Path(__file__).resolve().parents[3]


_TASK = "办公"
_WAIT_CHOICE = {"items": [{"target": "自己", "way": "等待", "app": None, "to": None, "pay_app": None}],
                "answering": None}


def _observations(*progress_event_ids: str) -> list[dict]:
    """@brief 以真实目录工具层建立当前观察，其他层沿用通用空状态。"""
    _, facts = contract_world()
    layers = _layers()
    layers["tool_layer"] = facts.pop("tool_layer")
    layers["environment_layer"]["duty"] = None
    data = {**facts, **layers, "agent_id": 7, "current_time": int(_context().observed_at.timestamp()),
            "observation_event_id": "observation-current", "wake_reasons": [], "same_moment_stage": "open",
            "progress_event_ids": list(progress_event_ids), "presented_message_ids": [], "verified_experiences": [],
            "activity_bindings": [], "goal_bindings": [], "task_bindings": [], "errand_bindings": [],
            "errand_item_bindings": [], "active_activities": [], "paused_activities": [],
            "pending_conclusions": [], "interaction_menu": {"allowed_actions": sorted({
                row["canonical_action"] for item in facts["interaction_bindings"] for row in item["actions"]})}}
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


class _GroundedCorrectionDispatcher:
    """@brief 捕获真实宿主请求并按明示响应序列回复当前工具参数。"""

    model_name = "deepseek-flash"

    def __init__(self, responses=None):
        """@brief 默认序列先查询、收到一次错误纠正后提交完整等待请求。"""
        self.requests = []
        self.responses = list(responses) if responses is not None else [
            _ModelResponse("call-memory", "memory_recent", '{"limit":1}'),
            _ModelResponse("call-invalid", "play_game", '{"game_id":"game-visible"}'),
            _ModelResponse("call-action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))]

    async def call(self, **request):
        """@brief 记录实际 wire 请求并返回下一项响应。"""
        self.requests.append(request)
        return self.responses.pop(0)


def _stage(engine, moment=None, location=None):
    """@brief 测试时刻经真实 SQLite 提交，解除退休语料测试的辅助依赖。"""
    staged = engine.context.state.model_copy(deep=True)
    now = staged.current_time if moment is None else moment
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={"next_decision_at": now, "body_settled_at": now,
                                               "location_id": location or agent.location_id}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_staged"})])
    engine.context.state = staged


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
            {"items": [
                {"activity_name": _WAIT, "summary": "等完了。", "memories": [], "views": []}], "errands": []},
            ensure_ascii=False))])
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





@pytest.mark.asyncio
async def test_system_layers_match_the_actual_host_request() -> None:
    """@brief 真实行动请求与只读系统消息使用各自的生产提示，键序与规则数量明确。"""
    life, readonly = LIFE_SYSTEM_LAYER, READONLY_SYSTEM_LAYER
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    [request] = dispatcher.requests
    assert request["messages"][0]["content"] == render_layer_json(life)
    assert system_message(True)["content"] == render_layer_json(readonly)
    assert list(life) == list(readonly) == ["layer", "role", "rules"]
    assert life == LIFE_SYSTEM_LAYER and len(life["rules"]) == 18


@pytest.mark.asyncio
async def test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it() -> None:
    """系统层第 14 条（docs/design/06-agent.md、2.3，裁决 D6、S3、S11）：真实生活决策请求的系统层在第 13 条之后
    写作息默认规则——平常照作息，今日计划写明今天与平常不同的安排时按计划，身体的感觉到更强的一档才改作息；饥饿、困意两条
    顺延为第 15、16 条，按档位与饭点吃、照就寝时间睡；第 4 条补「消息只核验对方说过」，第 18 条补「联系人不会来到身边」
    （7.3）。四个分句之后是让「等待」成为例外规格插入的钟点一句（docs/design/04-simulation-engine.md）：
    作息与今日计划里的钟点、around_time 都只是大概，非准点不可的靠目标的提醒时刻；第 14 条以这一句再接上班安排一句结尾
    （docs/design/10-runtime.md 4.6，收窄裁决 S11，逐字不动）：环境层写了上班安排的日子，那是定下的安排
    而不是习惯，今日计划改不了它。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    rules = json.loads(dispatcher.requests[0]["messages"][0]["content"])["rules"]
    assert len(rules) == 18 and rules == LIFE_SYSTEM_LAYER["rules"]
    assert rules[12].startswith("start_activity 一项一个对象") and rules[13].startswith("人设层的作息和习惯是你平常的过法")
    for sentence in ("到了平常的钟点、在平常的地方，就照平常做", "今日计划写明今天与平常不同的安排时，按计划",
                     "身体的感觉没到更强的一档，就不为它改作息", "人设层没写作息，或者今天不在平常的环境里，就按身体的感觉来"):
        assert sentence in rules[13]
    assert rules[13].endswith("就按身体的感觉来。作息里的钟点，和今日计划目标文字里的钟点、around_time，都只是大概的时候，"
                              "到时不一定会请你决策：差不多到了就做，早一点晚一点都算照常，不为凑钟点干等；"
                              "和人约好的事宁早勿晚，非准点不可的要靠目标的提醒时刻。"
                              "环境层写了上班安排的日子，那是定下的安排，不是习惯：今日计划改不了它，"
                              "手上的工作交了也还在班上，写明的休息时段和工作要求去别处的外出除外。")
    assert rules[14].startswith("你会饿") and "每个饭点的正餐只吃一次" in rules[14] and "趁早" not in rules[14]
    assert rules[15].startswith("晚上照人设层作息里的就寝时间去睡") and "困了就去睡" not in rules[15]
    assert rules[3].endswith("消息只核验了对方这样说过，说已经做了、正在赶来，都不等于发生了。")
    assert "联系人只能发消息或通过工具层列出的社交动作远程交流，不会来到你身边" in rules[17]
    assert rules[17].endswith("具体聊天内容和进展由你播报，不会自动生成对方回复。")
    assert "最接近的候选，不会替你改" in rules[11]


#: 系统层第 16 条，随让「等待」成为例外规格改写（docs/design/04-simulation-engine.md）：仍专指当日目标的
#: 提醒，睡着时只有提醒或睡够了才会醒，与第 6 条（事务提醒不叫醒睡着的人）不相抵。
_RULE_16 = '晚上照人设层作息里的就寝时间去睡（没写就寝时间，就困了再睡）：有点乏、困是睡前的常态，还没到就寝时间就照常过晚上，到了就可以睡，不用撑到某个钟点；很困或撑不住了可以提前，精神还好也可以晚些。想在某个时刻起来，可以用 set_alarm 设置一次闹钟，也可以给今日计划的目标写上提醒时刻；睡着时只有提醒、闹钟或睡够了才会醒，早睡也就早醒。'
#: 旧第 6 条的延续句（docs/design/06-agent.md 逐字项）：它把 report_status 的 summary 也写成延续载体。
_RETIRED_SENTENCE = "需要延续的打算写进 report_status、pause_activity 或 conclude_activity 的 summary"



#: 规格 3.1 定稿的第 1 条里的环境层一句（阶段 2 的写法；docs/design/10-runtime.md 4.6 在阶段 3 原位改写它）。
#: 第 14 条开头的四个分句（前缀；其后依次是插入的钟点一句与上班安排一句）。
_RULE_14_BEFORE_WORKDAY = ("人设层的作息和习惯是你平常的过法：到了平常的钟点、在平常的地方，就照平常做；今日计划写明今天与"
                           "平常不同的安排时，按计划；身体的感觉没到更强的一档，就不为它改作息。人设层没写作息，或者今天"
                           "不在平常的环境里，就按身体的感觉来。")



@pytest.mark.asyncio
async def test_rules_1_and_14_state_the_duty_arrangement_verbatim() -> None:
    """@brief 真实请求同时说明当前工作安排和日常作息，日程不能改变工作安排。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    rules = json.loads(dispatcher.requests[0]["messages"][0]["content"])["rules"]
    life = LIFE_SYSTEM_LAYER
    clause = "环境层是此刻的身体感受、所在地点与今天的上班安排"
    rule_14 = life["rules"][13]
    assert clause == "环境层是此刻的身体感受、所在地点与今天的上班安排"
    assert len(rules) == 18 and clause in rules[0] and rules == life["rules"]
    assert rules[13] == rule_14 and rule_14.startswith(_RULE_14_BEFORE_WORKDAY)
    assert rule_14.removeprefix(_RULE_14_BEFORE_WORKDAY) == (
        "作息里的钟点，和今日计划目标文字里的钟点、around_time，都只是大概的时候，到时不一定会请你决策："
        "差不多到了就做，早一点晚一点都算照常，不为凑钟点干等；和人约好的事宁早勿晚，非准点不可的要靠目标的提醒时刻。"
        "环境层写了上班安排的日子，那是定下的安排，不是习惯：今日计划改不了它，手上的工作交了也还在班上，"
        "写明的休息时段和工作要求去别处的外出除外。")
    life = LIFE_SYSTEM_LAYER
    assert life["rules"] == rules == LIFE_SYSTEM_LAYER["rules"]
    assert [index for index, rule in enumerate(rules) if "上班安排" in rule] == [0, 13]


@pytest.mark.asyncio
async def test_rules_1_4_6_divide_errands_summaries_and_memories() -> None:
    """@brief 真实请求区分个人事务、活动自述与记忆，并说明事务提醒不唤醒睡眠。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    rules = json.loads(dispatcher.requests[0]["messages"][0]["content"])["rules"]
    life = LIFE_SYSTEM_LAYER
    assert len(rules) == 18 and rules == life["rules"]
    assert "待办层是暂停的活动、还没办完的个人事务与今日计划" in rules[0]
    assert "办没办成由环境按实际结算确认，自己说办完了不算" in rules[3] and "办成或取消的事务不再列在待办层" in rules[3]
    assert rules[3].endswith("消息只核验了对方这样说过，说已经做了、正在赶来，都不等于发生了。")
    for sentence in ("在 report_status、pause_activity 或 conclude_activity 的 errands 里记成个人事务",
                     "到提醒时刻只提醒一次，不会叫醒睡着的你",
                     "其他需要延续的打算写进 pause_activity 或 conclude_activity 的 summary",
                     "report_status 的 summary 只留到本段停留或路程结束", "想记住的事只记以后做决定用得上的新情况",
                     "人设层已有的作息和待办层已有的事务不再记"):
        assert sentence in rules[5]
    assert rules[15] == _RULE_16 and "睡着时只有提醒、闹钟或睡够了才会醒" in rules[15] and "事务" not in rules[15]
    assert [index for index, rule in enumerate(rules) if "待办" in rule] == [0, 3, 5, 11]


#: 让「等待」成为例外规格写给人物的新说法（docs/design/04-simulation-engine.md）：规则下标 → 只在这一条出现的子串。
_IDLE_FILLER_PHRASES = {
    6: ("到时也会请你决策，手上的活动照常进行", "等待只留给几分钟内就有事请你决策的时候"),
    13: ("到时不一定会请你决策", "不为凑钟点干等"),
    15: ("不用撑到某个钟点",),
}
#: 被这次改写取代的第 15、16 条旧读法：任何一条规则里都不再出现。
_RETIRED_IDLE_READINGS = ("等平常的饭点", "下单后要等送到", "不必因此提早上床")


async def _request_rules() -> list[str]:
    """一次真实生活决策请求（直接导入的 LifeAgent 经调度器发出）的系统层规则。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())
    return json.loads(dispatcher.requests[0]["messages"][0]["content"])["rules"]


@pytest.mark.asyncio
async def test_rules_7_14_16_say_when_decisions_come_and_that_clock_times_are_approximate() -> None:
    """系统层第 7、14、16 条的新说法（docs/design/04-simulation-engine.md）：真实生活决策请求里，第 7 条说
    自己下的单送到、提醒到点等事到时也会请人物决策而手上的活动照常进行，等待只留给几分钟内就有事请他决策的时候；第 14 条
    说作息与计划里的钟点到时不一定会请他决策、不为凑钟点干等；第 16 条说到了就寝时间就可以睡、不用撑到某个钟点。每个
    说法只出现在它那一条里，请求的规则与模块常量逐字相同。"""
    rules = await _request_rules()
    assert len(rules) == 18 and rules == LIFE_SYSTEM_LAYER["rules"]
    for index, phrases in _IDLE_FILLER_PHRASES.items():
        for phrase in phrases:
            assert [number for number, rule in enumerate(rules) if phrase in rule] == [index], phrase


@pytest.mark.asyncio
async def test_retired_meal_delivery_and_bedtime_readings_are_gone_from_the_system_layer() -> None:
    """@brief 真实请求说明当前用餐与就寝规则，不包含已删除的旧指引。"""
    rules = await _request_rules()
    life = LIFE_SYSTEM_LAYER
    for place in (rules, LIFE_SYSTEM_LAYER["rules"], life["rules"]):
        assert [phrase for phrase in _RETIRED_IDLE_READINGS for rule in place if phrase in rule] == []
    assert "正餐留到平常的饭点前后吃" in rules[14] and "下单的东西要送到才算吃上" in rules[14]
    assert "还没到就寝时间就照常过晚上" in rules[15]


#: 同时做两件事规格第 8 条的新说法（docs/design/04-simulation-engine.md）：只在第 8 条出现。
_PARALLEL_RULE_8_PHRASES = ("暂停的不算", "恢复暂停的活动要有空位", "环境只拦同一种的两件", "原来那件照常进行")
#: 被第 8 条改写取代的旧名额读法：任何一条规则里都不再出现。
_RETIRED_SLOT_READING = "进行中、暂停与刚结束的都算"


@pytest.mark.asyncio
async def test_rule_8_says_paused_activities_do_not_count_and_only_same_kinds_collide() -> None:
    """@brief 真实请求说明暂停不占活动名额，只有同类活动互斥。"""
    rules = await _request_rules()
    life = LIFE_SYSTEM_LAYER
    assert len(rules) == 18 and rules == LIFE_SYSTEM_LAYER["rules"] == life["rules"]
    for phrase in _PARALLEL_RULE_8_PHRASES:
        assert [number for number, rule in enumerate(rules) if phrase in rule] == [7], phrase
    for place in (rules, LIFE_SYSTEM_LAYER["rules"], life["rules"]):
        assert [rule for rule in place if _RETIRED_SLOT_READING in rule] == []


@pytest.mark.asyncio
async def test_system_request_describes_current_apps_work_and_receipts() -> None:
    """@brief 真实请求包含当前应用推送、工作计时与收货规则，删除的操作不再出现。"""
    rules = await _request_rules()
    life = LIFE_SYSTEM_LAYER
    for place in (rules, LIFE_SYSTEM_LAYER["rules"], life["rules"]):
        assert len(place) == 18
        for phrase, index in (("有 app 推送", 6), ("你只选动作和它列出的 app", 11), ("按实际做的时间计进度", 16)):
            assert [i for i, text in enumerate(place) if phrase in text] == [index]
            assert sum(text.count(phrase) for text in place) == 1
        retired = ("娱乐只选 app", "方式写打开", "按主题计工时", "门店或渠道", "工时达标不等于已经提交",
                   "开车时只能再听播客", "给予必须写他人", "自己写工作主题")
        assert [phrase for phrase in retired for text in place if phrase in text] == []


def test_viewer_fixture_system_layers_are_the_host_system_message() -> None:
    """查看器夹具 tests/fixtures/viewer-context.json 里行动请求与续接请求两处系统层就是宿主 system_message(False) 的输出，
    逐字相等（docs/design/02-exchange-engine.md）；夹具只记录这两种当前请求。"""
    fixture = json.loads((_REPO_ROOT / "tests" / "fixtures" / "viewer-context.json").read_text(encoding="utf-8"))
    expected = system_message(False)
    assert [fixture[kind]["messages"][0] for kind in ("action", "continuation")] == [expected, expected]
    assert set(fixture) == {"action", "continuation"}



def test_continuation_guidance_only_persists_paused_and_concluded_summaries() -> None:
    """@brief 宿主发出的指引只把暂停与收尾的自述列为后续延续内容。"""
    places = {"host system message": system_message(False)["content"]}
    assert [name for name, text in places.items() if _RETIRED_SENTENCE in text] == []
    current = "其他需要延续的打算写进 pause_activity 或 conclude_activity 的 summary"
    assert [name for name, text in places.items() if current not in text] == []


@pytest.mark.asyncio
async def test_the_system_layer_asks_for_no_particular_language() -> None:
    """不作语言要求（docs/design/06-agent.md，裁决 D18）：真实生活决策请求与只读问答的系统层都没有要人物用某种
    语言写的规则——role 与每条 rules 都不提中文、英文或语言；人物用英文写的收尾总结与记忆照常登记，不被纠错。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher)
    await _run(agent, _observations())

    for system in (json.loads(dispatcher.requests[0]["messages"][0]["content"]),
                   json.loads(system_message(True)["content"])):
        texts = [system["role"], *system["rules"]]
        assert [text for text in texts if re.search("中文|英文|汉语|语言|Chinese|English|language", text)] == []
    memory = {"type": "routine", "text": "I took a short break after lunch.", "importance": 0.2, "keywords": ["break"]}
    english = _GroundedCorrectionDispatcher(responses=[_ModelResponse("action", "conclude_activity", json.dumps(
        {"items": [{"activity_name": _WAIT, "summary": "Waited a while and rested.", "memories": [memory],
                    "views": []}], "errands": []}, ensure_ascii=False))])
    agent, environment = _agent(english)
    assert await _run(agent, _second_observation()) == "conclude_activity registered"
    assert len(english.requests) == 1
    [item] = environment.calls[0].request.items
    assert (item.summary, [entry.text for entry in item.memories]) == ("Waited a while and rested.", [memory["text"]])


#: 系统层第 15 条用「」点名的饥饿档与第 16 条列举的困意档（docs/design/06-agent.md：档位名与 felt.py 的
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
    """档位名与 felt.py 分级文本逐字一致（docs/design/06-agent.md 验收）：词表取自 felt.py 的分级表；第 15 条点名的
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
    assert layers == compact_render(life_user_layers(data, layers[1]))
    assert request["messages"][1] == user_message(life_user_layers(data, layers[1]))
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
    """原生工具：六个世界请求、两个世界查询与三个记忆查询，description 只写用途，没有逐轮枚举；
    只剩最后一个回合时去掉全部查询，六个世界请求的字节不变。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("memory", "memory_recent", '{"limit":1}'),
        _ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent, _ = _agent(dispatcher, max_turns=2)
    await _run(agent, _second_observation())

    first, last = (item["tools"] for item in dispatcher.requests)
    world_tools = ["start_activity", "send_message", "report_status", "pause_activity", "conclude_activity",
                   "revise_day_plan", "set_alarm"]
    queries = ["recall_task_summary", "query_world", "memory_recent", "memory_search", "memory_time_range"]
    assert [item["function"]["name"] for item in first] == [*world_tools, *queries]
    assert [item["function"]["name"] for item in last] == world_tools
    assert last == first[:len(world_tools)]
    assert all(item["function"]["description"] == TOOL_DESCRIPTIONS[item["function"]["name"]] for item in first)
    models = {**REQUEST_MODELS, **_MEMORY_TOOL_MODELS, "query_world": QueryWorldRequest}
    assert all(item["function"]["parameters"] == deepseek_strict_schema(models[item["function"]["name"]])
               for item in first)
    for name in ("target", "way"):
        assert "enum" not in first[0]["function"]["parameters"]["properties"]["items"]["items"]["properties"][name]
    assert set(queries).isdisjoint(item["function"]["name"] for item in last)


@pytest.mark.asyncio
async def test_a_decision_without_memory_queries_sends_exactly_one_request_and_no_prelude() -> None:
    """没有记忆查询的决策恰好一次模型请求，参数符合行动行（思考开启、auto、8000）；登记后不追加 tool 消息。"""
    work = {"items": [{"target": _TASK, "way": "进行", "app": None, "to": None, "pay_app": None}],
            "answering": None}
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("action", "start_activity", json.dumps(work, ensure_ascii=False))])
    agent, environment = _agent(dispatcher)
    assert await _run(agent, _observations()) == "start_activity registered"

    [request] = dispatcher.requests
    assert [item.phase for item in environment.evidence] == ["request", "response"]
    assert (request["tool_choice"], request["max_tokens"], request["extra_body"]) == (
        "auto", 8000, {"thinking": {"type": "enabled"}})
    assert "temperature" not in request and len(environment.calls) == 1
    assert environment.calls[0].tool_name == "start_activity"
    assert environment.calls[0].request.model_dump(mode="json", by_alias=True) == work
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
    original = engine.context.store.commit

    def commit(state, drafts):
        batches.append([draft.event_type for draft in drafts])
        return original(state, drafts)

    monkeypatch.setattr(engine.context.store, "commit", commit)
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
    return [event for event in engine.context.store.read_events(engine.context.store.last_event_seq) if event.event_type == event_type]


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
    original = engine.observations._observation_data
    progress = {"layer": "进度层", "session_started_at": "2026-01-05T00:00:00+08:00", "events": []}

    def with_progress(agent):
        return {**original(agent), "progress_layer": progress, "progress_meta": [], "progress_event_ids": []}

    monkeypatch.setattr(engine.observations, '_observation_data', with_progress)
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

    from worldmock.agents.life_persona import persona_layer
    from worldmock.agents.life_prompt import READONLY_SYSTEM_LAYER, empty_memory_layer
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
    """prompt_sha256 只计算、不钉常量，随系统层与待办层 Schema 重钉（docs/design/06-agent.md、8.2）：新运行的
    模型身份记录的提示指纹就是按现行十八条系统层与带 around_time 的待办层 Schema 算出的值；去掉新第 14 条回到十七条、
    或去掉待办层目标的 around_time，指纹都不同。"""
    from worldmock.agents import life_prompt
    from worldmock.life import prompt_schemas
    from worldmock.run_setup import _model_identity

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
    monkeypatch.setattr(life_prompt, "LAYER_SCHEMAS", {**prompt_schemas.LAYER_SCHEMAS, "待办层": todo})
    assert life_prompt.life_prompt_sha256() != current


def test_prompt_fingerprint_binds_the_layer_schemas(monkeypatch) -> None:
    """提示指纹绑定七层 Schema：任何一层的 Schema 改动都改变运行指纹。"""
    from worldmock.agents import life_prompt
    from worldmock.life import prompt_schemas

    original = life_prompt.life_prompt_sha256()
    changed = {**prompt_schemas.LAYER_SCHEMAS, "人设层": {**prompt_schemas.LAYER_SCHEMAS["人设层"], "title": "改"}}
    monkeypatch.setattr(life_prompt, "LAYER_SCHEMAS", changed)
    assert life_prompt.life_prompt_sha256() != original


@pytest.mark.parametrize("index,current,retired", [
    (6, "有 app 推送", "有工作可以开始做了"),
    (7, "工具层里类别相同的动作算一种", "看和听内容算一种"),
    (11, "环境定，你只选动作和它列出的 app", "人物自己点名内容"),
    (12, "target、way、app、to、pay_app", "target、way、from、to"),
    (16, "按实际做的时间计进度", "留意截止时间与已完成的有效工时"),
])
def test_each_resource_rule_change_moves_only_the_prompt_fingerprint(monkeypatch, index, current, retired) -> None:
    """五条规则的语义都绑定新运行提示指纹（docs/design/02-exchange-engine.md）：
    任一条换回旧读法时实际模型身份的提示指纹变化，工具指纹不变；所有指纹实时计算，不钉字面摘要。"""
    from worldmock.agents import life_prompt
    from custom.agents.life_agent import life_tool_schema_sha256
    from worldmock.run_setup import _model_identity

    prompt, tools = life_prompt.life_prompt_sha256(), life_tool_schema_sha256()
    identity = _model_identity()
    assert (identity.prompt_sha256, identity.tool_schema_sha256) == (prompt, tools)
    changed = copy.deepcopy(LIFE_SYSTEM_LAYER)
    assert changed["rules"][index].count(current) == 1
    changed["rules"][index] = changed["rules"][index].replace(current, retired)
    monkeypatch.setattr(life_prompt, "LIFE_SYSTEM_LAYER", changed)
    identity = _model_identity()
    assert identity.prompt_sha256 == life_prompt.life_prompt_sha256() != prompt
    assert identity.tool_schema_sha256 == life_tool_schema_sha256() == tools


def test_prompt_fingerprint_binds_the_callable_index_schema(monkeypatch) -> None:
    """工具层 Schema 登记可调用索引 callable_index（docs/design/04-simulation-engine.md）：它是工具层的必填键，形状为
    lifestyle、goods、query_categories 三个键；新运行的模型身份记录的提示指纹按带索引的 Schema 算出，去掉索引指纹就不同。"""
    from worldmock.agents import life_prompt
    from worldmock.life import prompt_schemas
    from worldmock.run_setup import _model_identity

    schema = prompt_schemas.TOOL_LAYER_SCHEMA
    assert "callable_index" in schema["required"]
    assert set(schema["properties"]["callable_index"]["properties"]) == {
        "goods", "navigation_apps", "payment_apps", "query_categories"}
    current = life_prompt.life_prompt_sha256()
    assert _model_identity().prompt_sha256 == current
    tools = copy.deepcopy(schema)
    tools["properties"].pop("callable_index")
    tools["required"].remove("callable_index")
    monkeypatch.setattr(life_prompt, "LAYER_SCHEMAS", {**prompt_schemas.LAYER_SCHEMAS, "工具层": tools})
    assert life_prompt.life_prompt_sha256() != current


@pytest.mark.asyncio
async def test_callable_index_rides_in_the_tool_layer_and_counts_toward_capacity(_engine_factory, monkeypatch,
                                                                                  caplog) -> None:
    """工具层的可调用索引（docs/design/04-simulation-engine.md）随真实观察原样进入 user 消息的工具层，计入整次请求的容量
    计数（第四章预算不变）：同一请求去掉索引后计数更小；把窗口定在两者之间时，宿主在发送前以容量故障停止——不删减索引、
    不发请求——容量故障的分层记录单列索引的用量。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _stage(engine, int(datetime(2026, 1, 5, 12, 30, tzinfo=ZoneInfo(engine.context.scenario.timezone)).timestamp()), "home")
    decision = context(engine, "index-capacity")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)

    def observations() -> list[dict]:
        """@brief 这次真实观察交给宿主的一条结构化观察记录（每次决策各一份）。"""
        return [{"turn": 0, "action": "observe", "ok": True, "data": copy.deepcopy(observed)}]

    responses = [_ModelResponse("action", "start_activity", json.dumps(_WAIT_CHOICE, ensure_ascii=False))]
    dispatcher = _GroundedCorrectionDispatcher(responses=list(responses))
    agent, _ = _agent(dispatcher)
    await _run(agent, observations())

    [request] = dispatcher.requests
    layers = json.loads(request["messages"][1]["content"])
    assert compact_render(expand_tool_layer(layers[0])) == compact_render(observed["tool_layer"])
    assert list(layers[0]) == ["layer", "groups", "callable_index"]
    assert layers[0]["callable_index"] == observed["tool_layer"]["callable_index"]
    assert layers[0]["callable_index"]["goods"] and layers[5]["events"] == []
    assert layers[0]["callable_index"]["navigation_apps"] == [
        app.name for app in engine.context.semantics.catalog.apps.values() if "navigation" in app.purposes]
    bare = [{key: value for key, value in layers[0].items() if key != "callable_index"}, *layers[1:]]
    without = prompt_capacity.request_token_count({**request, "messages": [request["messages"][0],
                                                                           user_message(bare)]})
    full = prompt_capacity.request_token_count(request)
    assert full > without
    window = int((full + without) / 2 / prompt_capacity.CONTEXT_THRESHOLD)
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: window)
    tight = _GroundedCorrectionDispatcher(responses=list(responses))
    agent, environment = _agent(tight)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="model request capacity exceeded"):
        await _run(agent, observations())
    assert tight.requests == [] and environment.calls == []
    message = next(record.message for record in caplog.records if "capacity exceeded" in record.message)
    assert "'工具层'" in message and "'工具层.callable_index'" in message
