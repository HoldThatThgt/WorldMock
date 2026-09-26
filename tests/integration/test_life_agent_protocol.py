"""LifeAgent 闭集 Schema、宿主身份注入与原生 ReAct 链测试。

生活决策没有 finish：世界请求登记即结束本轮，记忆查询是观察、不结束本轮；人物记忆只经 conclude_activity.memories
写入，来源由系统登记（docs/dev/SPEC-time-and-energy.md 第七章「finish 并入 conclude_activity」）。只读问答保留 finish。
"""

from __future__ import annotations

import copy
import os
import json
import re
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration.storefront_fixtures import supply_row, discovery_catalog

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.agent.memory import AgentMemoryStore
from agentsociety2.agent.runner import _step_agent_batch_async
import agentsociety2.society.society as society_module
from agentsociety2.society import (
    AgentSociety,
    AgentSocietyConfig,
    DecisionContext,
    DirectToolCall,
    SchedulingState,
)
from custom.agents.life_agent import (
    LifeAgent,
    DECISION_STATEMENT_MAX_TOKENS,
    DECISION_STATEMENT_TEMPERATURE,
    _ACTION_REGISTRATION_CONFIRMATION,
    _conclusion_memories,
    life_action_tool_names,
    life_prompt_sha256,
    life_tool_schema_sha256,
)
from worldmock.catalog.affordances import default_affordances
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING
from worldmock.life.engine import affordance_contract
from worldmock.life.models import (
    ActionReceipt,
    ConcludeActivityRequest,
    REQUEST_MODELS,
    START_ACTIVITY_ACTIONS,
    StartActivityRequest,
)


#: 生活决策与只读问答都开放的记忆查询（按条目身份读取的 memory_read 已删除）。
_MEMORY_TOOL_NAMES = {
    "memory_recent",
    "memory_search",
    "memory_step_range",
    "memory_tick_range",
    "memory_time_range",
}

_BASE_ACTION_NAMES = {
    "start_activity",
    "recall_task_summary",
    "submit_work",
}

_BUDGET_MARKER = "\n\nAuthoritative ReAct model-turn budget:\n"

#: 真实注册表构造的观察合同冻结块（与引擎 observe 输出同一来源）。
_AFFORDANCE_CONTRACT = affordance_contract(default_affordances())

_OBSERVED_AT = datetime(2026, 1, 5, tzinfo=timezone.utc)
_NOW_EPOCH = int(_OBSERVED_AT.timestamp())

#: 当前停留会话与活动的可读名称（docs/dev/SPEC-time-and-energy.md 第七章「会话与活动的字段」的名称格式）。
_HOME_SESSION = "自己家（1月4日 18:00 起）"
_WAIT = "等待（1月4日 23:45 起）"
_WORK = "工作整理会议纪要（1月4日 21:00 起）"
_NEWS = "观看晚间新闻（1月4日 20:00 起）"


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求和执行合同。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)

#: 两层观察投影的 scene 段（narration.render_scene 的输入合同）。
_SCENE = {
    "current_time": "2026-01-05T00:00:00+00:00",
    "location": "自己家",
    "people_here": [],
    "felt": {"appetite": "不饿", "sleepiness": "有点困"},
    "current_activities": [],
    "paused_activities": [],
    "just_ended": [],
    "day_plan": None,
    "inbox_head": [],
    "todos_summary": "",
    "reachable_places": [],
    "inventory": [],
}

#: 两层观察投影的 facts 段（routes 行只含白名单键：目的地、方式、分钟、票价、费力与是否可达；进度层按会话分组列活动，经历只写成句子）。
_FACTS = {
    "current_time": "2026-01-05T00:00:00+00:00",
    "location": "自己家",
    "people_here": [],
    "contacts": [],
    "inbox": [],
    "tasks": [
        {"task_name": "整理会议纪要", "due_at": _NOW_EPOCH + 86400,
         "remaining_contract_seconds": 3600, "new_material_ranges": []},
    ],
    "progress": {"sessions": [{"name": _HOME_SESSION, "activities": []}], "results": [], "alarms": []},
    "interactive_objects": [
        {"object": "自己", "actions": ["等待", "睡觉"], "preconditions_met": True},
        {"object": "整理会议纪要", "actions": ["工作"], "preconditions_met": False},
    ],
    "routes": [{"destination": "办公室", "mode": "driving", "minutes": 4,
                "fare_fen": 800, "effort": "轻松", "feasible": True, "status": "reachable", "reason": None}],
    "products": [],
    "inventory": [],
    "day_plan": None,
    "felt": {"appetite": "不饿", "sleepiness": "有点困"},
    "drive_summary": "你有点困。",
    "experiences": [],
}


def _context() -> DecisionContext:
    """构造稳定测试决策上下文。"""
    return DecisionContext(
        "run", "round", "decision", 7, _OBSERVED_AT, 3
    )


def _memory(text: str = "我歇了一会儿，心里踏实了些。", *, kind: str = "routine", importance: float = 0.5,
            keywords: tuple[str, ...] = ("休息",)) -> dict:
    """一条收尾记忆 {type, text, importance, keywords}；来源与认知状态由系统填写，模型不填任何身份。"""
    return {"type": kind, "text": text, "importance": importance, "keywords": list(keywords)}


def _conclude_args(*names: str, memories: list[list[dict]] | None = None) -> dict:
    """conclude_activity 的参数：task、summary 与 memories 三数组等长（缺省每项各写一条记忆）。"""
    groups = memories if memories is not None else [[_memory()] for _ in names]
    return {"task": list(names), "summary": ["先收个尾。" for _ in names], "memories": groups}


def _stored_memories(args: dict) -> list[dict]:
    """宿主把 conclude_activity.memories 按 task 展平成的本步记忆（来源 activity_conclusion，认知状态 subjective）。"""
    return _conclusion_memories("conclude_activity", args)


def _request_budget(request: dict) -> dict:
    """读取实际模型请求携带的权威回合预算。"""
    system = request["messages"][0]["content"]
    return json.loads(system.split(_BUDGET_MARKER, 1)[1])


def _observations(*result_ids: str) -> list[dict]:
    """构造含人物可见来源事件的真实结构化观察记录：人物在当前停留会话里，暂无活动。"""
    data = {
        "observation_event_id": "observation-current",
        "current_time": _NOW_EPOCH,
        "wake_reasons": [],  # 唤醒原因只进私有证据，不改变工具面
        "same_moment_stage": "open",  # 未达同刻上限：不收窄世界请求
        "scene": copy.deepcopy(_SCENE),
        "facts": copy.deepcopy(_FACTS),
        "interaction_bindings": [
            {"object": "自己", "kind": "self", "object_id": "self", "actions": [
                {"label": "等待", "canonical_action": "wait"},
                {"label": "睡觉", "canonical_action": "sleep"}]},
            {"object": "整理会议纪要", "kind": "work_task", "object_id": "task-visible",
             "actions": [{"label": "工作", "canonical_action": "work"}]},
        ],
        "task_bindings": [{"task_name": "整理会议纪要", "task_id": "task-visible",
                           "submission_ready": True}],
        "activity_bindings": [],
        "session_bindings": [{"name": _HOME_SESSION, "session_id": "session-7-0001"}],
        "goal_bindings": [],
        "recent_action_results": [{"event_id": item} for item in result_ids],
        "agent_id": 7, "verified_experiences": [], "occupied_resources": [],
        "active_activities": [],
        "paused_activities": [],
        "pending_conclusions": [],
        "affordance_contract": _AFFORDANCE_CONTRACT,
        "interaction_menu": {"allowed_actions": sorted(START_ACTIVITY_ACTIONS)},
        "tasks": [{"task_id": "task-visible", "remaining_contract_seconds": 3600,
                   "material": {"end_char": 10, "total_chars": 10}}],
        "activity_summary": {"feature_film": 1, "sport": 44, "foods": 1},
        "content_progress": {"content-visible": {
            "item_id": "content-visible", "position_seconds": 0.0,
            "remaining_media_seconds": 630.0, "exposure_count": 0,
        }},
        "game_catalog": [{"game_id": "game-visible"}],
        "messages": [{"message_id": "message-visible"}],
        "browse_targets": ["product_catalog"],
        "products": [{"product_id": "product-visible", "use_modes": ["eat"],
                      "use_duration_seconds": 300, "required_location_id": None}],
        "supplies": [supply_row(supply_id="supply-visible", product_id="product-visible")],
        "discovery_catalog": discovery_catalog("product-visible"),
        "locations": [{"location_id": "home"}, {"location_id": "office"}],
        "location_id": "home",
        "available_inventory": {"product-visible": 1.0},
        "inventory_items": [{"product_id": "product-visible", "name": "现有食品", "use_modes": ["eat"],
                             "use_duration_seconds": 300, "required_location_id": None}],
        "routes": [{"destination_id": "office", "status": "reachable", "mode": "driving",
                    "duration_seconds": 216}],
        "games": {},
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def _with_activities(data: dict, *activities: tuple[str, str], elsewhere: tuple[str, ...] = ()) -> dict:
    """在当前停留会话下加入活动（名称, 阶段说法），同步写入进度层活动行、私有活动绑定、场景句与控制行。

    暂停的活动默认停在开始地点、此刻可以恢复（绑定 resumable 为真）；elsewhere 列出开始地点在别处的暂停活动。
    """
    session = data["facts"]["progress"]["sessions"][-1]
    for name, phase in activities:
        identity = f"activity-private-{len(data['activity_bindings']) + 1}"
        session["activities"].append({"name": name, "phase": phase, "objective_progress": "已做约15分钟"})
        data["activity_bindings"].append({"name": name, "activity_id": identity, "phase": phase,
                                          "resumable": phase == PHASE_PAUSED and name not in elsewhere})
        control = {"activity_id": identity, "kind": "wait", "title": name, "progress": "已做约15分钟",
                   "elapsed_seconds": 900, "planned_duration_seconds": 1800, "data": {}}
        if phase == PHASE_ACTIVE:
            data["scene"]["current_activities"].append({"title": name, "progress": "已做约15分钟", "verb": "做"})
            data["active_activities"].append({**control, "status": "active"})
        elif phase == PHASE_PAUSED:
            data["scene"]["paused_activities"].append({"title": name, "progress": "已做约15分钟"})
            data["paused_activities"].append({**control, "status": "paused"})
        elif phase == PHASE_PENDING:
            data["scene"]["just_ended"].append(name)
            data["pending_conclusions"].append({**control, "status": "completed"})
    return data


def _step_observation() -> dict:
    """人物步骤的观察：当前停留里有一项进行中的等待，人物可以带记忆结束它。"""
    return _with_activities(_observations()[0]["data"], (_WAIT, PHASE_ACTIVE))


def test_life_agent_schema_is_closed_and_host_fields_are_hidden() -> None:
    """正式决策的 Schema 只含本轮可构造的业务动作和五个记忆查询，没有 finish。"""
    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )
    schemas = agent.build_react_tool_schemas(readonly=False)
    functions = {item["function"]["name"]: item["function"] for item in schemas}

    assert set(functions) == _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES
    assert "finish" not in functions
    start_fields = functions["start_activity"]["parameters"]["properties"]
    assert {"agent_id", "decision_id", "action_id"}.isdisjoint(start_fields)
    assert set(start_fields) == {"targets", "actions"}
    assert functions["start_activity"]["parameters"]["additionalProperties"] is False
    assert all(function["strict"] is True for function in functions.values())
    assert len(life_prompt_sha256()) == 64
    assert life_tool_schema_sha256() == life_tool_schema_sha256()


def test_incomplete_work_stays_submittable_for_business_rejection() -> None:
    """合同有效工时未完成的可见未付款任务仍可提交；环境以 WORK_INCOMPLETE 业务拒绝说明原因。

    任务名枚举不按 submission_ready 剪裁（docs/dev/SPEC-time-and-energy.md「仍须修复」第 4 条）。
    """
    observations = _observations()
    observations[0]["data"]["task_bindings"][0]["submission_ready"] = False
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    functions = {item["function"]["name"]: item["function"]
                 for item in agent.build_react_tool_schemas(readonly=False)}
    assert {"recall_task_summary", "start_activity", "submit_work"} <= set(functions)
    task_name = functions["submit_work"]["parameters"]["properties"]["task_name"]
    assert task_name["enum"] == ["整理会议纪要"]
    deliverable = {"summary": "整理了会议要点。", "decisions": ["按计划推进。"], "action_items": ["下周复盘。"]}
    submit = ReactDecision("", "submit_work", {"task_name": "整理会议纪要", "deliverable": deliverable}, "")
    assert agent.validate_react_decisions([submit], False) == ""


def test_life_tool_schema_fingerprint_pins_registry_digest(monkeypatch) -> None:
    """@brief 指纹输入并入注册表摘要：注册表变则工具面变则指纹变（审计链闭合）。"""
    import custom.agents.life_agent as life_agent_module

    original = life_tool_schema_sha256()
    assert len(original) == 64
    monkeypatch.setattr(life_agent_module, "affordance_registry_sha256",
                        lambda: "0" * 64)
    assert life_tool_schema_sha256() != original


def test_plan_day_is_no_person_tool_and_revision_carries_remind_time() -> None:
    """当日计划由零点后台任务生成，人物工具面没有 plan_day，醒来与运行开始也不收窄工具面；
    当日已有计划（含空清单）时开放修订，条目必须给出可为 null 的 remind_time（第四章「计划与提醒」）。"""
    def build_functions(observations: list[dict]) -> dict[str, dict]:
        agent = LifeAgent()
        agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
        return {item["function"]["name"]: item["function"]
                for item in agent.build_react_tool_schemas(readonly=False)}

    from custom.envs.life_env import _STABLE_TOOL_SURFACE

    assert len(_STABLE_TOOL_SURFACE) == 8 and "plan_day" not in _STABLE_TOOL_SURFACE
    assert life_action_tool_names() == frozenset(REQUEST_MODELS) == _STABLE_TOOL_SURFACE
    waking = _observations()
    waking[0]["data"]["wake_reasons"] = ["run_start", "wake_from_sleep"]
    assert set(build_functions(waking)) == _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES
    observations = _observations()
    observations[0]["data"]["facts"]["day_plan"] = {"goals": []}
    revising = build_functions(observations)
    assert "plan_day" not in revising and "revise_day_plan" in revising
    tool = revising["revise_day_plan"]
    assert "at most 300 characters" in tool["description"]
    assert "Progressed goals stay unchanged" in tool["description"]
    assert "remind_time" in tool["description"] and "Do not provide times or durations" not in tool["description"]
    assert set(tool["parameters"]["properties"]) == {"goals"}
    fields = tool["parameters"]["properties"]["goals"]["items"]["properties"]
    assert set(fields) == {"intent_kind", "goal", "remind_time"}
    assert fields["goal"]["pattern"] == r"\S"
    assert fields["remind_time"]["anyOf"] == [{"pattern": "^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", "type": "string"},
                                              {"type": "null"}]
    assert "day_start_epoch" not in json.dumps(tool)


def test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages() -> None:
    """第六章：决策自述与动作两阶段的 system 含同一节「怎样过一天」；只读问答不含该节；过时的供给差异句已删。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = False
    agent._model_name = "deepseek-flash"
    request = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(request)
    action_system = agent.build_react_messages(request)[0]["content"]
    statement_system = agent.prelude_decision_statement_request(request)["messages"][0]["content"]
    readonly = ReactLoopRequest(0, _context().observed_at, [], "How was today?", True)
    readonly_system = agent.build_react_messages(readonly)[0]["content"]

    sections = [re.search(r"怎样过一天：\n.*?重新权衡。", system, re.S) for system in (action_system, statement_system)]
    assert all(sections) and sections[0].group(0) == sections[1].group(0)
    section = sections[0].group(0)
    for sentence in ("你的一天由你自己安排。", "你手上最多同时挂两件事，暂停的也算",
                     "睡着时只有提醒或睡够了才会醒。", "当前地点没有卖吃的，不代表不能送到这里。",
                     "只有实际开始并完成的事才算数。"):
        assert sentence in section
    assert len(section.splitlines()) == 12
    assert "怎样过一天" not in readonly_system
    for system in (action_system, statement_system):
        assert "based_on_action_id" not in system and "supply_id" not in system
        for banned in ("评审", "评分", "引擎期望", "tool", "finish", "回合", "budget"):
            assert banned not in system


def test_location_norm_is_removed_from_static_tool_and_system_text() -> None:
    """地点行为改由菜单声明，静态工具描述和系统指令均不保留办公室散文。"""
    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )
    schemas = {
        item["function"]["name"]: item["function"]["description"]
        for item in agent.build_react_tool_schemas(readonly=False)
    }
    system_prompt = next(
        message["content"] for message in agent.build_react_messages(
            ReactLoopRequest(0, _context().observed_at, _observations()))
        if message.get("role") == "system")

    assert "at the office, prefer brief phone-friendly content" not in schemas["start_activity"]
    assert "save movies and long drama episodes" not in schemas["start_activity"]
    flattened = " ".join(system_prompt.split())
    assert "watching long entertainment openly at the office" not in flattened
    assert "inappropriate" not in flattened


def test_action_schema_requires_prepared_environment_catalogs() -> None:
    """非只读动作工具在首观察目录冻结前必须明确失败。"""
    with pytest.raises(RuntimeError, match="activity controls are not prepared"):
        LifeAgent().build_react_tool_schemas(readonly=False)


def test_feedback_boundary_exposes_readable_activity_controls() -> None:
    """进行中活动开放三个逐项控制工具，Schema 只展示可读活动名；只有结束工具带逐项记忆。"""
    agent = LifeAgent()
    observations = _observations()
    _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE))
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    schemas = agent.build_react_tool_schemas(readonly=False)
    functions = {item["function"]["name"]: item["function"] for item in schemas}

    controls = {"report_status", "pause_activity", "conclude_activity"}
    assert set(functions) == _BASE_ACTION_NAMES | controls | _MEMORY_TOOL_NAMES
    for name in controls:
        fields = functions[name]["parameters"]["properties"]
        with_memories = name == "conclude_activity"
        assert set(fields) == ({"task", "summary", "memories"} if with_memories else {"task", "summary"})
        assert fields["task"]["items"]["enum"] == [_WORK]
        arguments = {"task": [_WORK], "summary": ["写完提纲"], **({"memories": [[]]} if with_memories else {})}
        assert agent.validate_react_decisions([ReactDecision("", name, arguments, "")], False) == ""
    assert "activity-private-1" not in json.dumps(functions, ensure_ascii=False)


def test_readonly_schema_uses_its_independent_finish_contract() -> None:
    """只读问答始终提供记忆查询和无需生活动作的结束工具。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, [], "How was today?", True)
    agent.prepare_react_loop(request)

    functions = {
        item["function"]["name"]: item["function"]
        for item in agent.build_react_tool_schemas(readonly=True)
    }

    assert set(functions) == _MEMORY_TOOL_NAMES | {"finish"}
    assert set(REQUEST_MODELS).isdisjoint(functions)
    assert set(functions["finish"]["parameters"]["properties"]) == {"answer"}


def test_life_agent_rejects_invalid_or_non_atomic_decisions() -> None:
    """参数错误、多动作与生活决策中的 finish 都在执行前失败（生活决策没有 finish）。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(request)
    valid = ReactDecision("", "start_activity", {"targets": ["自己"], "actions": ["等待"]}, "")

    assert "multiple" in agent.validate_react_decisions([valid, valid], False)
    memory = ReactDecision("", "memory_recent", {"limit": 1}, "")
    assert "only tool" in agent.validate_react_decisions([memory, valid], False)
    invalid = ReactDecision("", "start_activity", {"targets": ["自己"], "actions": ["虚构动作"]}, "")
    assert "not offered" in agent.validate_react_decisions([invalid], False)
    finish = ReactDecision("", "finish", {"memories": []}, "")
    assert "unknown LifeAgent tool: finish" == agent.validate_react_decisions([finish], False)


#: 持久记忆中的一条既往自述：AS2 条目身份与元数据只留在宿主，查询结果只投影四个字段。
_PRIOR_EPISODE = {"id": "m_00000060_000001", "agent_id": 7, "tick": 60, "step_index": 1, "step_count": 1,
                  "time": "2026-01-04T08:00:00+00:00", "type": "routine", "importance": 0.5,
                  "keywords": ["等待"], "text": "I waited yesterday.", "source": "activity_conclusion",
                  "refs": [], "merge_count": 1, "epistemic_status": "subjective"}
_PRIOR_PROJECTION = {"type": "routine", "text": "I waited yesterday.", "time": "2026-01-04T08:00:00+00:00",
                     "epistemic_status": "subjective"}


def test_memory_prompt_section_shows_text_without_episode_identities() -> None:
    """记忆摘要段在自述与行动两阶段只写主观归纳与自述正文，不带条目身份（第七章「发给模型的内容不含内部身份」）。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._build_memory_context = lambda: {  # type: ignore[method-assign]
        "subjective_summary": "# Subjective", "recent_episodes": [dict(_PRIOR_EPISODE)]}
    request = ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    agent.prepare_react_loop(request)

    action_user = agent.build_react_messages(request)[1]["content"]
    statement_user = agent._life_user_sections(request, with_facts=False)

    for user in (action_user, statement_user):
        assert "主观归纳：\n# Subjective" in user
        assert "最近的人物自述：\n- [subjective] I waited yesterday." in user
        for identity in (_PRIOR_EPISODE["id"], "result-prior", "observation-current", "session-7-0001"):
            assert identity not in user
    assert "已核验经历：\n# Subjective" not in action_user


def test_react_messages_are_three_parts_and_observation_written_once() -> None:
    """user 消息由卡片+场景+事实三段组成，同一观察只写一次。"""
    from worldmock.life.narration import render_facts

    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice",
                      "background_story": "在上海张江做工程师，工作认真。"}
    agent._enable_memory = False
    agent._life_decision_statement = "先喝口水，再回消息。"
    request = ReactLoopRequest(0, _context().observed_at, _observations())

    messages = agent.build_react_messages(request)
    user = messages[1]["content"]
    facts_text = render_facts(copy.deepcopy(_FACTS))

    assert [item["role"] for item in messages] == ["system", "user"]
    assert user.count("你是Alice。") == 1
    assert user.count("你在自己家") == 1
    assert user.count(facts_text) == 1
    assert user.count("你刚才心里想的是：先喝口水，再回消息。") == 1
    assert "observation-current" not in user
    assert "game-visible" not in user
    raw_copy = json.dumps(_observations()[0]["data"], ensure_ascii=False,
                          sort_keys=True, default=str)
    assert raw_copy not in user


def test_system_prompt_is_identity_first_with_word_share() -> None:
    """系统指令以身份前言为主，人物与情境词占比不低于一半。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = False
    request = ReactLoopRequest(0, _context().observed_at, _observations())

    system = agent.build_react_messages(request)[0]["content"]

    assert "你不是AI助手" in system
    assert "Alice" in system
    for banned in ("评审", "评分", "引擎期望", "tool", "finish", "回合", "budget"):
        assert banned not in system
    sentences = [item for item in re.split(r"[。！？\n]", system) if item.strip()]
    words = [len(item) for item in sentences]
    identity_words = sum(count for item, count in zip(sentences, words)
                         if not re.search(r"[A-Za-z]{4,}", item))
    assert identity_words / sum(words) >= 0.5


def test_blacklisted_observation_fields_never_enter_messages() -> None:
    """黑名单字段（动机内部仪表、完整目录、事件身份、私有活动与会话绑定）不进入任何消息。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = False
    observations = _observations()
    data = _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING))
    data["motivation_internal"] = {"candidates": [{"kind": "eat", "score": 0.9}]}
    data["world_state_version"] = 42
    data["end_time"] = _NOW_EPOCH + 86_400

    messages = agent.build_react_messages(
        ReactLoopRequest(0, _context().observed_at, observations))
    text = "\n".join(item["content"] for item in messages)

    for banned in ("motivation_internal", "motivation_hints", "world_state_version",
                   "end_time", "affordance_contract",
                   "observation_event_id", "game_catalog", "activity_bindings", "session_bindings",
                   "pending_conclusions", "activity-private-1", "session-7-0001"):
        assert banned not in text
    assert f"刚结束：{_WAIT}。" in text


class _ModelResponse:
    """提供原生工具属性及可持久化响应。"""

    def __init__(self, call_id: str, name: str, arguments: str,
                 reasoning_content: str | None = None) -> None:
        function = SimpleNamespace(name=name, arguments=arguments)
        tool_call = SimpleNamespace(id=call_id, type="function", function=function)
        message = SimpleNamespace(
            tool_calls=[tool_call], content="", reasoning_content=reasoning_content)
        self.choices = [SimpleNamespace(message=message)]
        self._call_id = call_id
        self._name = name
        self._arguments = arguments
        self._reasoning_content = reasoning_content

    def model_dump(self, *, mode: str) -> dict:
        """返回最小 JSON 模型响应（reasoning_content 恒在，缺省为 None）。"""
        assert mode == "json"
        return {"choices": [{"message": {"tool_calls": [{
            "id": self._call_id,
            "type": "function",
            "function": {"name": self._name, "arguments": self._arguments},
        }], "reasoning_content": self._reasoning_content}}]}


class _DecisionStatementResponse:
    """提供两阶段决策决策自述阶段的无工具纯文本响应。"""

    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(
            tool_calls=[], content=content))]
        self._content = content

    def model_dump(self, *, mode: str) -> dict:
        """返回决策自述响应的最小 JSON 序列化。"""
        assert mode == "json"
        return {"choices": [{"message": {"content": self._content, "tool_calls": []}}]}


class _MultipleModelResponse:
    """提供同一模型响应中的多个原生工具调用。"""

    def __init__(self, calls: list[tuple[str, str, str]]) -> None:
        self._calls = calls
        tool_calls = [
            SimpleNamespace(
                id=call_id,
                type="function",
                function=SimpleNamespace(name=name, arguments=arguments),
            )
            for call_id, name, arguments in calls
        ]
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(tool_calls=tool_calls, content="")
        )]

    def model_dump(self, *, mode: str) -> dict:
        """返回含多个工具调用的最小 JSON 模型响应。"""
        assert mode == "json"
        tool_calls = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for call_id, name, arguments in self._calls
        ]
        return {"choices": [{"message": {"tool_calls": tool_calls}}]}


def _assert_decision_statement_wire(request: dict) -> None:
    """核对决策自述请求的 wire 形状：无工具、1.0 温度、8000 上限、思考关闭。"""
    assert "tools" not in request
    assert "tool_choice" not in request
    assert request["temperature"] == DECISION_STATEMENT_TEMPERATURE
    assert request["max_tokens"] == DECISION_STATEMENT_MAX_TOKENS
    assert request["max_retries"] == 0
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


class _Dispatcher:
    """按顺序返回决策自述、登记前的一次记忆查询与唯一的生活动作；世界请求登记即结束本轮。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            _ModelResponse(
                "call-memory", "memory_recent", '{"limit":1}'
            ),
            _ModelResponse(
                "call-action",
                "start_activity",
                '{"targets":["自己"],"actions":["等待"]}',
                reasoning_content="Tired person needs a short rest before work.",
            ),
        ]
        self.decision_statements = [_DecisionStatementResponse("有点累，先歇一分钟再干活。")]

    async def call(self, **request):
        """返回决策自述或下一个原生工具响应。"""
        self.requests.append(request)
        if "tools" not in request:
            _assert_decision_statement_wire(request)
            return self.decision_statements.pop(0)
        assert request["tool_choice"] == "auto"
        assert request["max_retries"] == 0
        assert "reasoning_effort" not in request
        assert request["extra_body"] == {"thinking": {"type": "enabled"}}
        assert all(item["function"]["strict"] is True for item in request["tools"])
        return self.responses.pop(0)


class _MemoryRuntime:
    """返回一个带条目身份与元数据的既往记录，并关闭步骤后记忆写入。"""

    store = None

    def __init__(self) -> None:
        self.calls = []

    def build_context(self):
        """返回稳定长期记忆提示上下文。"""
        return {"subjective_summary": "# Subjective", "recent_episodes": []}

    def dispatch_tool(self, action, args):
        """返回 AS2 记忆运行时的完整查询结果（投影前）。"""
        assert action == "memory_recent"
        assert args == {"limit": 1}
        self.calls.append((action, args))
        data = {"epistemic_status": "subjective", "episodes": [dict(_PRIOR_EPISODE)]}
        return True, json.dumps(data), data


class _Environment:
    """捕获 LifeAgent 注入的直接调用和模型证据。"""

    def __init__(self) -> None:
        self.calls: list[DirectToolCall] = []
        self.evidence = []
        self.memory_updates = []

    async def submit_action(self, call: DirectToolCall) -> ActionReceipt:
        """返回与宿主身份一致的提交收据。"""
        self.calls.append(call)
        return ActionReceipt(
            action_id=call.action_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            request_sha256="a" * 64,
        )

    async def record_model_exchange(self, exchange) -> None:
        """保存模型请求与响应证据。"""
        self.evidence.append(exchange)

    async def record_memory_update(self, evidence) -> None:
        """保存人物记忆文件内容身份。"""
        self.memory_updates.append(evidence)


class _StepDispatcher:
    """返回决策自述与一次带记忆结束等待的 conclude_activity（登记即结束本轮，没有 finish）。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            _ModelResponse("call-action", "conclude_activity",
                           json.dumps(_conclude_args(_WAIT), ensure_ascii=False)),
        ]
        self.decision_statements = [_DecisionStatementResponse("先把手头的事做完。")]

    async def call(self, **request):
        """返回决策自述或下一个严格工具响应。"""
        self.requests.append(request)
        if "tools" not in request:
            return self.decision_statements.pop(0)
        return self.responses.pop(0)


class _GroundedCorrectionDispatcher:
    """复现记忆查询后捏造旧游戏工具、再改用当前目标工具的响应序列（世界请求登记即结束本轮）。"""

    model_name = "deepseek-flash"

    def __init__(self, responses: list[_ModelResponse] | None = None,
                 decision_statements: list[_DecisionStatementResponse] | None = None) -> None:
        self.requests = []
        defaults = [
            _ModelResponse("call-memory", "memory_recent", '{"limit":1}'),
            _ModelResponse(
                "call-invalid",
                "play_game",
                '{"game_id":"game-visible","planned_duration_seconds":600}',
            ),
            _ModelResponse(
                "call-action",
                "start_activity",
                json.dumps({"targets": ["自己"], "actions": ["等待"]}),
            ),
        ]
        self.responses = list(responses) if responses is not None else defaults
        self.decision_statements = decision_statements if decision_statements is not None else [
            _DecisionStatementResponse("先查查昨天做了什么。")]

    async def call(self, **request):
        """记录真实 wire 并返回决策自述或下一个协议响应。"""
        self.requests.append(request)
        if "tools" not in request:
            return self.decision_statements.pop(0)
        return self.responses.pop(0)


class _StepEnvironment(_Environment):
    """提供实际人物步骤与 Society 故障边界所需环境合同。"""

    def __init__(self) -> None:
        super().__init__()
        self.current_time = _context().observed_at
        self.version = _context().world_state_version
        self.ready = (7,)
        self.rounds = []
        self.commits = []
        self.aborts = []
        self.failures = []
        self.checkpoints = []
        self.observed_contexts = []

    async def observe(self, context: DecisionContext) -> dict:
        """返回带稳定来源事件身份的当前人物观察（一项进行中的等待）。"""
        assert context.agent_id == 7
        self.observed_contexts.append(context)
        return _step_observation()

    async def scheduling_state(self) -> SchedulingState:
        """返回当前共同决策边界。"""
        return SchedulingState(
            self.current_time,
            self.ready,
            self.current_time + timedelta(seconds=60),
            self.version,
        )

    async def begin_decision_round(self, contexts) -> str:
        """记录 Society 冻结的决策顺序。"""
        self.rounds.append(contexts)
        return contexts[0].round_id

    async def commit_decision_round(self, round_id: str) -> list[dict]:
        """记录不应在记忆保存失败后发生的结算。"""
        self.commits.append(round_id)
        return [{"agent_id": 7, "status": "completed"}]

    async def abort_decision_round(self, round_id: str, error: str) -> None:
        """保存失败轮身份与错误证据。"""
        self.aborts.append((round_id, error))

    async def fail_run(self, error: str) -> None:
        """保存 Society 传播的运行失败证据。"""
        self.failures.append(error)

    async def checkpoint(self, request) -> None:
        """记录不应在失败轮后创建的共同检查点。"""
        self.checkpoints.append(request)


def _create_step_workspace(workspace: Path) -> None:
    """创建启用严格记忆且关闭技能待办的人物工作区。"""
    LifeAgent.create(
        workspace,
        {"id": 7, "name": "Alice", "occupation": "engineer"},
        {
            "enable_memory": True,
            "enable_todo_list": False,
            "max_react_turns": 4,
            "memory_consolidation_pending_threshold": 100,
            "memory_consolidation_interval_steps": 100,
        },
    )


def _service(environment: _StepEnvironment, dispatcher: _StepDispatcher):
    """构造测试使用的最小共享服务容器。"""
    return SimpleNamespace(
        env=environment,
        llm=SimpleNamespace(default=dispatcher),
        trace=None,
    )


def _wire_agent(agent: LifeAgent, environment: _Environment, dispatcher,
                max_turns: int, *, memory: bool = True) -> None:
    """按协议测试需要装配人物、环境与调度器。"""
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice", "occupation": "engineer"}
    agent._enable_memory = memory
    agent._decision_context = _context()
    agent._max_react_turns = max_turns
    if memory:
        agent._memory_runtime = _MemoryRuntime()
        agent._build_memory_context = _MemoryRuntime().build_context  # type: ignore[method-assign]
    agent.trace_span = lambda *args, **kwargs: nullcontext(  # type: ignore[method-assign]
        SimpleNamespace(attributes={})
    )


def _react_tool_names(requests: list[dict]) -> list[set[str]]:
    """逐个带工具的模型请求读取实际开放的工具名。"""
    return [{item["function"]["name"] for item in request["tools"]} for request in requests if "tools" in request]


@pytest.mark.asyncio
async def test_native_react_submits_one_host_bound_life_action() -> None:
    """原生 ReAct 完成决策自述、登记前的记忆查询与唯一动作；世界请求登记即结束本轮，不再请求模型。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _Dispatcher()
    _wire_agent(agent, environment, dispatcher, 4)

    summary = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    )

    assert summary == "start_activity registered"
    assert len(environment.calls) == 1
    call = environment.calls[0]
    assert call.context == _context()
    assert call.action_id == "decision:action:0001"
    assert isinstance(call.request, StartActivityRequest)
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 3
    assert len(dispatcher.requests) == 3
    first, second = dispatcher.requests[1:]
    assert [item["role"] for item in first["messages"]] == ["system", "user"]
    assert [item["role"] for item in second["messages"]] == ["system", "user", "assistant", "tool"]
    memory = second["messages"][-1]
    assert memory["tool_call_id"] == "call-memory"
    assert json.loads(memory["content"])["data"] == {
        "epistemic_status": "subjective", "episodes": [_PRIOR_PROJECTION]}
    assert _PRIOR_EPISODE["id"] not in memory["content"]
    assert second["messages"][1] == first["messages"][1]
    assert "你在自己家" in first["messages"][1]["content"]
    assert "observation-current" not in first["messages"][1]["content"]
    assert _react_tool_names([first, second]) == [_BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES] * 2
    start = next(
        item["function"]["parameters"]["properties"]
        for item in first["tools"]
        if item["function"]["name"] == "start_activity"
    )
    assert start["targets"]["items"]["enum"] == ["整理会议纪要", "自己"]
    assert set(start["actions"]["items"]["enum"]) == {"工作", "等待", "睡觉"}
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in (first, second)] == [3, 2]
    assert [_request_budget(item)["phase"] for item in (first, second)] == ["before_action", "before_action"]
    # 回合预算句只在剩余回合 ≤2 时出现（docs/CONTRACTS.md「人物决策提示与生成参数」），
    # 且预算 JSON 保持尾部可解析。
    assert "剩余回合已经不多" not in first["messages"][0]["content"]
    assert "剩余回合已经不多" in second["messages"][0]["content"]
    registration = agent._react_messages[-1]
    assert registration["tool_call_id"] == "call-action"
    assert json.loads(registration["content"]) == {
        "ok": True, "observation": _ACTION_REGISTRATION_CONFIRMATION, "data": {}}
    assert agent._last_finish_memories == []


@pytest.mark.asyncio
async def test_two_stage_decision_statement_evidence_and_single_retry() -> None:
    """决策自述空文本消耗一个回合重试一次，第二次成功进入行动消息。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _GroundedCorrectionDispatcher(
        responses=[
            _ModelResponse(
                "call-action",
                "start_activity",
                '{"targets":["自己"],"actions":["等待"]}',
            ),
        ],
        decision_statements=[
            _DecisionStatementResponse(""),
            _DecisionStatementResponse("有点累，先歇一分钟再干活。"),
        ],
    )
    _wire_agent(agent, environment, dispatcher, 4)

    await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations()))

    decision_statement_requests = [item for item in dispatcher.requests if "tools" not in item]
    assert len(decision_statement_requests) == 2
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 3
    assert environment.evidence[1].error is not None
    assert "empty" in environment.evidence[1].error
    assert environment.evidence[3].error is None
    assert environment.evidence[0].request["messages"][1]["content"] == (
        environment.evidence[2].request["messages"][1]["content"])
    assert "你刚才心里想的是：有点累，先歇一分钟再干活。" in (
        dispatcher.requests[2]["messages"][1]["content"])
    assert agent._life_decision_statement == "有点累，先歇一分钟再干活。"


@pytest.mark.asyncio
async def test_decision_statement_failing_twice_fails_the_whole_decision() -> None:
    """决策自述连续两次协议失败即本次决策失败，不再进入行动阶段。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _GroundedCorrectionDispatcher(
        decision_statements=[_DecisionStatementResponse(""), _DecisionStatementResponse("")])
    _wire_agent(agent, environment, dispatcher, 4)

    with pytest.raises(RuntimeError, match="failed the protocol twice"):
        await agent.run_react_loop(
            ReactLoopRequest(0, _context().observed_at, _observations()))

    assert len(dispatcher.requests) == 2
    assert environment.calls == []
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response"]
    assert all(item.error is not None for item in environment.evidence[1::2])


@pytest.mark.asyncio
async def test_reasoning_content_is_persisted_in_model_response_evidence() -> None:
    """思考开启后模型推理随现有序列化进入 model_response 足迹证据。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _Dispatcher()
    _wire_agent(agent, environment, dispatcher, 4)

    await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations("result-prior"))
    )

    requests = [item for item in environment.evidence if item.phase == "request"]
    responses = [item for item in environment.evidence if item.phase == "response"]
    # 决策自述请求关闭思考；行动阶段请求开启思考并采用兼容的工具选择。
    assert requests[0].request["extra_body"] == {"thinking": {"type": "disabled"}}
    for item in requests[1:]:
        assert item.request["extra_body"] == {"thinking": {"type": "enabled"}}
        assert item.request["tool_choice"] == "auto"
    # 决策自述响应是纯文本；反例：不带推理的记忆查询响应序列化后 reasoning_content 为 None，不伪造内容。
    assert responses[0].response["choices"][0]["message"]["content"] == (
        "有点累，先歇一分钟再干活。")
    memory_message = responses[1].response["choices"][0]["message"]
    assert memory_message["tool_calls"][0]["function"]["name"] == "memory_recent"
    assert memory_message["reasoning_content"] is None
    # 正例：带 reasoning_content 的动作响应按原样进入足迹事件。
    action_message = responses[2].response["choices"][0]["message"]
    assert action_message["reasoning_content"] == (
        "Tired person needs a short rest before work."
    )
    assert action_message["tool_calls"][0]["function"]["name"] == "start_activity"


@pytest.mark.asyncio
async def test_budgeted_memory_search_preserves_the_action_turn() -> None:
    """有限记忆读取在只剩世界请求回合时撤下，保留一个自主生活动作回合；登记即结束本轮。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-memory-2", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-memory-3", "memory_recent", '{"limit":1}'),
        _ModelResponse(
            "call-action",
            "start_activity",
            '{"targets":["自己"],"actions":["等待"]}',
        ),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 5)

    result = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )

    assert result == "start_activity registered"
    assert len(environment.calls) == 1
    react_requests = [item for item in dispatcher.requests if "tools" in item]
    assert _react_tool_names(react_requests) == [
        _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES,
        _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES,
        _BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES,
        _BASE_ACTION_NAMES,
    ]
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in react_requests] == [4, 3, 2, 1]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0003", "0004", "0005"]


@pytest.mark.asyncio
async def test_multiple_memory_calls_share_one_model_turn() -> None:
    """同一响应的多个记忆调用只消耗一个模型回合，余下的回合仍够登记世界请求。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _MultipleModelResponse([
            ("call-memory-1", "memory_recent", '{"limit":1}'),
            ("call-memory-2", "memory_recent", '{"limit":1}'),
        ]),
        _ModelResponse(
            "call-action",
            "start_activity",
            '{"targets":["自己"],"actions":["等待"]}',
        ),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 3)

    result = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )

    assert result == "start_activity registered"
    assert len(environment.calls) == 1
    assert len(agent._memory_runtime.calls) == 2
    react_requests = [item for item in dispatcher.requests if "tools" in item]
    assert _react_tool_names(react_requests) == [_BASE_ACTION_NAMES | _MEMORY_TOOL_NAMES, _BASE_ACTION_NAMES]
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in react_requests] == [2, 1]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0003"]


@pytest.mark.asyncio
async def test_reserved_action_boundary_rejects_memory_without_extra_request() -> None:
    """只剩世界请求回合时非法记忆调用零执行，预算耗尽后在下次请求前失败（行动前须剩 1 个回合）。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory", "memory_recent", '{"limit":1}'),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 2)

    with pytest.raises(RuntimeError, match="before_action requires 1 model turns but only 0 remain"):
        await agent.run_react_loop(
            ReactLoopRequest(0, _context().observed_at, _observations())
        )

    assert len(dispatcher.requests) == 2
    react_request = dispatcher.requests[1]
    assert {item["function"]["name"] for item in react_request["tools"]} == _BASE_ACTION_NAMES
    assert environment.calls == []
    assert agent._memory_runtime.calls == []
    assert [item.phase for item in environment.evidence] == [
        "request", "response", "request", "response"]
    assert "unknown native tool calls: ['memory_recent']" == environment.evidence[-1].error


@pytest.mark.asyncio
async def test_readonly_budget_reserves_finish_and_resets_between_loops() -> None:
    """只读循环末回合仅开放结束，并在下一次循环重置实际调用序号。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-finish-1", "finish", '{"answer":"first"}'),
        _ModelResponse("call-memory-2", "memory_recent", '{"limit":1}'),
        _ModelResponse("call-finish-2", "finish", '{"answer":"second"}'),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 2)
    request = ReactLoopRequest(0, _context().observed_at, [], "What happened?", True)

    assert await agent.run_react_loop(request) == "first"
    assert await agent.run_react_loop(request) == "second"

    names = [
        {item["function"]["name"] for item in current["tools"]}
        for current in dispatcher.requests
    ]
    assert names == [
        _MEMORY_TOOL_NAMES | {"finish"}, {"finish"},
        _MEMORY_TOOL_NAMES | {"finish"}, {"finish"},
    ]
    assert [_request_budget(item)["remaining_model_turns_including_current"]
            for item in dispatcher.requests] == [2, 1, 2, 1]
    assert [_request_budget(item)["phase"] for item in dispatcher.requests] == ["readonly"] * 4
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0001", "0002"]


@pytest.mark.asyncio
async def test_second_world_request_in_one_decision_is_refused() -> None:
    """一轮决策恰好一个世界请求：登记之后，同一决策的第二个生活动作在校验与分派两道边界都被拒绝。"""
    agent = LifeAgent()
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))

    receipt = await agent.dispatch_react_tool("start_activity", {"targets": ["自己"], "actions": ["等待"]})

    assert receipt.ok is True
    recall = {"task_name": "整理会议纪要"}
    assert agent.validate_react_decisions([ReactDecision("", "recall_task_summary", recall, "")], False) == (
        "this decision already submitted its life action")
    with pytest.raises(RuntimeError, match="already submitted"):
        await agent.dispatch_react_tool("recall_task_summary", recall)
    assert len(environment.calls) == 1


@pytest.mark.asyncio
async def test_native_react_rejects_impossible_one_turn_life_decision() -> None:
    """决策自述前不足自述加世界请求两个回合时不发送注定失败的请求。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _GroundedCorrectionDispatcher(responses=[], decision_statements=[])
    _wire_agent(agent, environment, dispatcher, 1, memory=False)

    with pytest.raises(RuntimeError, match="decision_statement requires 2 model turns but only 1 remain"):
        await agent.run_react_loop(
            ReactLoopRequest(0, _context().observed_at, _observations())
        )

    assert dispatcher.requests == []
    assert environment.calls == []
    assert environment.evidence == []


@pytest.mark.asyncio
async def test_grounded_unknown_game_tool_is_corrected_without_alias() -> None:
    """真实失败形状在同一有限循环内改用当前目标工具且仅提交一次，登记后不再请求模型。"""
    agent = LifeAgent()
    environment = _Environment()
    dispatcher = _GroundedCorrectionDispatcher()
    _wire_agent(agent, environment, dispatcher, 5)

    result = await agent.run_react_loop(
        ReactLoopRequest(0, _context().observed_at, _observations())
    )

    assert result == "start_activity registered"
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartActivityRequest)
    assert environment.calls[0].request.targets == ["自己"]
    assert environment.calls[0].request.actions == ["等待"]
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 4
    assert "unknown native tool calls" in environment.evidence[5].error
    assert len(dispatcher.requests) == 4
    correction = dispatcher.requests[3]
    assert correction["messages"][-1]["tool_call_id"] == "call-invalid"
    feedback = json.loads(correction["messages"][-1]["content"])
    assert feedback["executed"] is False
    assert correction["messages"][-2]["tool_calls"][0]["function"]["name"] == "play_game"
    third_names = {item["function"]["name"] for item in correction["tools"]}
    assert "start_activity" in third_names
    assert "play_game" not in third_names
    assert "finish" not in third_names


@pytest.mark.asyncio
async def test_runtime_budget_handles_twelve_bad_responses_then_submits_once() -> None:
    """生产配置允许超过十次模型调用后纠正，完整循环仍只登记一个动作。"""
    from worldmock.life.scenario import load_scenario
    from worldmock.runtime import _agent_specs

    scenario = load_scenario(Path("scenarios/white-collar-12-weeks.json"))[0]
    config = _agent_specs(scenario, persona_compiler=lambda _: None)[0]["config"]
    responses = [_ModelResponse(f"bad-{index}", "invented_game", "{}") for index in range(12)]
    responses.append(_ModelResponse("action", "start_activity",
                                    '{"targets":["自己"],"actions":["等待"]}'))
    dispatcher = _GroundedCorrectionDispatcher(responses=responses)
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, config["max_react_turns"])
    result = await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))
    assert result == "start_activity registered"
    assert len(dispatcher.requests) == 14
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartActivityRequest)
    assert sum(item.error is not None for item in environment.evidence if item.phase == "response") == 12


def test_readonly_state_mismatch_is_an_internal_failure() -> None:
    """宿主只读状态不一致必须抛出，不能作为模型可纠正错误。"""
    agent = LifeAgent()
    request = ReactLoopRequest(0, _context().observed_at, _observations())
    agent.prepare_react_loop(request)

    with pytest.raises(RuntimeError, match="readonly decision state is inconsistent"):
        agent.validate_react_decisions([], True)


@pytest.mark.asyncio
async def test_life_schema_and_target_errors_are_corrected_before_execution() -> None:
    """Pydantic参数与目标配对错误各耗一回合且只有最终合法动作执行。"""
    arguments = {"targets": ["自己"], "actions": ["等待"]}
    missing = dict(arguments)
    missing.pop("actions")
    wrong_pair = {"targets": ["整理会议纪要"], "actions": ["等待"]}
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-schema", "start_activity", json.dumps(missing)),
        _ModelResponse("call-pair", "start_activity", json.dumps(wrong_pair)),
        _ModelResponse("call-action", "start_activity", json.dumps(arguments)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 5, memory=False)

    await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, _observations()))

    assert len(environment.calls) == 1
    assert environment.calls[0].request == StartActivityRequest(**arguments)
    assert "invalid start_activity arguments" in environment.evidence[3].error
    assert "action is not offered" in environment.evidence[5].error
    react_requests = [item for item in dispatcher.requests if "tools" in item]
    assert len(react_requests) == 3
    assert all(json.loads(request["messages"][-1]["content"])["executed"] is False
               for request in react_requests[1:3])


@pytest.mark.asyncio
async def test_invalid_conclusion_memories_are_corrected_before_registration() -> None:
    """收尾记忆越界由本地请求模型拒绝、按协议错误在本轮重试；只有改正后的请求登记，工具面不变且没有 finish。"""
    invalid = _conclude_args(_WAIT, memories=[[_memory("我记得 3e71a031bb3d4103 那会儿很累。")]])
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-invalid", "conclude_activity", json.dumps(invalid, ensure_ascii=False)),
        _ModelResponse("call-action", "conclude_activity", json.dumps(_conclude_args(_WAIT), ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = LifeAgent()
    _wire_agent(agent, environment, dispatcher, 4, memory=False)
    observation = {"turn": 0, "action": "observe", "ok": True, "data": _step_observation()}

    result = await agent.run_react_loop(ReactLoopRequest(0, _context().observed_at, [observation]))

    assert result == "conclude_activity registered"
    assert [call.request for call in environment.calls] == [
        ConcludeActivityRequest.model_validate(_conclude_args(_WAIT))]
    assert "hexadecimal identifier" in environment.evidence[3].error
    react_requests = [item for item in dispatcher.requests if "tools" in item]
    assert len(react_requests) == 2
    feedback = json.loads(react_requests[1]["messages"][-1]["content"])
    assert feedback["executed"] is False
    assert "invalid conclude_activity arguments" in feedback["error"]
    names = _react_tool_names(react_requests)
    assert names[0] == names[1]
    assert "conclude_activity" in names[0] and "finish" not in names[0]
    assert agent._last_finish_memories == _stored_memories(_conclude_args(_WAIT))


@pytest.mark.asyncio
async def test_native_memory_update_hashes_real_workspace_files(tmp_path) -> None:
    """结束活动写下的真实记忆落盘后通过 PersonAgent 原生路径登记内容身份。"""
    store = AgentMemoryStore(tmp_path, agent_id=7)
    written = store.append_episodes(
        _stored_memories(_conclude_args(_WAIT)),
        tick=60,
        t=_context().observed_at,
        step_count=1,
    )
    agent = LifeAgent()
    environment = _Environment()
    agent._env = environment
    agent._decision_context = _context()
    agent._step_count = 1
    agent._memory_runtime = SimpleNamespace(store=store)

    from agentsociety2.agent.memory_runtime import MemoryStepOutcome

    await agent._record_memory_update(MemoryStepOutcome(tuple(written), (), "a" * 64))

    assert written[0]["source"] == "activity_conclusion"
    assert len(environment.memory_updates) == 1
    evidence = environment.memory_updates[0]
    assert evidence.context == _context()
    assert evidence.created_episode_ids == (written[0]["id"],)
    assert evidence.consolidated is False
    for digest in (
        evidence.memory_sha256,
        evidence.episodes_sha256,
        evidence.state_sha256,
    ):
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


@pytest.mark.asyncio
async def test_native_memory_update_records_real_subjective_merge_without_duplicate(tmp_path) -> None:
    """真实记忆运行时合并旧身份，原生记录入口保留合并、来源及文件证据。"""
    import hashlib

    from custom.agents.life_memory import WorldMockMemoryRuntime
    from worldmock.footprints.experiences import project_verified_experiences
    from worldmock.footprints.models import EventDraft
    from worldmock.footprints.store import FootprintStore
    from worldmock.life.memory_evidence import _memory_evidence_data

    footprint = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [7]},
        {"version": 0, "current_time": _NOW_EPOCH},
    )
    source = footprint.commit(footprint.state, [EventDraft(
        event_type="activity_progress", occurred_at=_NOW_EPOCH,
        agent_id=7, activity_id="wait-activity",
        data={"interval_start": _NOW_EPOCH - 60, "interval_end": _NOW_EPOCH,
              "activity": {"activity_id": "wait-activity", "kind": "wait"}},
    )])[0]
    facts = project_verified_experiences([source], agent_id=7, cutoff_seq=source.event_seq)
    footprint.close()
    workspace = tmp_path / "agent_0007"
    environment = _StepEnvironment()
    _create_step_workspace(workspace)
    agent = await LifeAgent.from_workspace(workspace, _service(environment, _StepDispatcher()))
    agent.bind_decision_context(_context())
    observations = _observations()
    observations[0]["data"]["verified_experiences"] = facts
    agent.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observations))
    runtime = agent._memory_runtime
    assert isinstance(runtime, WorldMockMemoryRuntime)
    store = runtime.store
    assert store is not None
    conclusion = _stored_memories(_conclude_args(_WAIT))

    first = await runtime.after_step(
        tick=60, t=_context().observed_at, step_count=1,
        finish_memories=conclusion,
    )
    await agent._record_memory_update(first)
    initial_merge_count = store.iter_episodes()[0]["merge_count"]
    second = await runtime.after_step(
        tick=60, t=_context().observed_at + timedelta(minutes=1), step_count=2,
        finish_memories=conclusion,
    )
    await agent._record_memory_update(second)

    episodes = store.iter_episodes()
    assert len(episodes) == 1
    identity = episodes[0]["id"]
    assert episodes[0]["merge_count"] == initial_merge_count + 1
    assert episodes[0]["source"] == "activity_conclusion"
    assert len(environment.memory_updates) == 2
    created, merged = environment.memory_updates
    assert created.created_episode_ids == (identity,)
    assert created.merged_episode_ids == ()
    assert merged.created_episode_ids == ()
    assert merged.merged_episode_ids == (identity,)
    assert merged.episodes == ({"id": identity, "type": "routine",
                                "text": episodes[0]["text"], "epistemic_status": "subjective"},)
    assert merged.context == _context()
    assert merged.observation_event_id == "observation-current"
    assert merged.verified_event_ids == (source.event_id,)
    assert merged.memory_before_sha256 == created.memory_sha256
    assert merged.episodes_sha256 != created.episodes_sha256
    for actual, path in ((merged.memory_sha256, store.memory_md_path),
                         (merged.episodes_sha256, store.episodes_path),
                         (merged.state_sha256, store.state_path)):
        assert actual == hashlib.sha256(path.read_bytes()).hexdigest()
    data = _memory_evidence_data(merged)
    assert data["created_episode_ids"] == []
    assert data["merged_episode_ids"] == [identity]
    assert data["episodes"] == list(merged.episodes)
    assert data["observation_event_id"] == "observation-current"
    assert data["verified_event_ids"] == [source.event_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_path", ["normal", "decision_statement_retry", "action_correction"])
async def test_person_step_records_conclusion_memory_update(tmp_path, decision_path: str) -> None:
    """真实人物步骤每次仅观察一次；自述重试或行动纠错后，conclude_activity 的记忆经原生 after_step 落盘并登记。"""
    workspace = tmp_path / "agent_0007"
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    if decision_path == "decision_statement_retry":
        dispatcher.decision_statements.insert(0, _DecisionStatementResponse(""))
    elif decision_path == "action_correction":
        dispatcher.responses.insert(0, _ModelResponse("call-invalid", "invented_action", "{}"))
    _create_step_workspace(workspace)
    agent = await LifeAgent.from_workspace(
        workspace,
        _service(environment, dispatcher),
    )
    agent.bind_decision_context(_context())

    summary = await agent.step(0, _context().observed_at)

    assert summary == "conclude_activity registered"
    assert environment.observed_contexts == [_context()]
    assert [call.request for call in environment.calls] == [
        ConcludeActivityRequest.model_validate(_conclude_args(_WAIT))]
    expected_calls = 2 if decision_path == "normal" else 3
    assert [item.phase for item in environment.evidence] == ["request", "response"] * expected_calls
    errors = [item.error for item in environment.evidence if item.error]
    expected_error = {"normal": [], "decision_statement_retry": ["decision_statement is empty"],
                      "action_correction": ["unknown native tool calls: ['invented_action']"]}
    assert errors == expected_error[decision_path]
    assert len(environment.memory_updates) == 1
    evidence = environment.memory_updates[0]
    episodes = [
        json.loads(line)
        for line in (workspace / "memory" / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert evidence.context == _context()
    assert evidence.created_episode_ids == (episodes[0]["id"],)
    assert [(item["text"], item["source"], item["epistemic_status"], item["refs"]) for item in episodes] == [
        (_memory()["text"], "activity_conclusion", "subjective", [])]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["exception", "empty"])
async def test_memory_persistence_failure_aborts_society_before_commit(
    monkeypatch,
    tmp_path,
    failure_mode,
) -> None:
    """收尾记忆在世界请求登记后未落盘时 runner 失败，Society 保存故障并中止，不结算。"""
    workspace = tmp_path / "as2" / "agents" / "agent_0007"
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    service = _service(environment, dispatcher)
    _create_step_workspace(workspace)
    marker = "injected append failure"
    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("WORKSPACE_PATH", str(project_root))

    def fail_append(*_args, **_kwargs):
        if failure_mode == "exception":
            raise OSError(marker)
        return []

    monkeypatch.setattr(AgentMemoryStore, "append_episodes", fail_append)

    def run_actual_batch(request, proxy):
        async def done():
            return {
                "results": await _step_agent_batch_async(request, proxy),
                "token_stats": {},
            }

        return done()

    monkeypatch.setattr(
        society_module,
        "step_agent_batch",
        type("Task", (), {"remote": staticmethod(run_actual_batch)})(),
    )
    config = AgentSocietyConfig(
        run_id="run",
        agent_specs=({"id": 7, "profile": {}, "config": {}},),
        agent_class_name="LifeAgent",
        env_router=environment,
        service_proxy=service,
        start_time=environment.current_time,
        end_time=environment.current_time + timedelta(hours=1),
        run_dir=tmp_path,
        custom_root=project_root,
        workspace_mode="restored",
    )
    society = AgentSociety(config)
    society._initialized = True
    society._environment_started = True

    with pytest.raises(RuntimeError, match="Agent decision failed"):
        await society.run()

    expected = marker if failure_mode == "exception" else "did not persist finish memories"
    assert len(environment.calls) == 1
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 2
    assert environment.memory_updates == []
    assert environment.commits == []
    assert environment.checkpoints == []
    assert len(environment.aborts) == 1
    assert expected in environment.aborts[0][1]
    assert len(environment.failures) == 1
    assert expected in environment.failures[0]


def test_memory_sleep_boundary_follows_engine_wake_signal() -> None:
    """就寝后第一个边界（wake_from_sleep）驱动记忆巩固与反思。"""
    agent = LifeAgent()
    request = ReactLoopRequest(
        0, _context().observed_at, _observations())
    agent.prepare_react_loop(request)
    assert agent.memory_sleep_boundary() is False

    waking = _observations()
    waking[0]["data"]["wake_reasons"] = ["wake_from_sleep"]
    agent.prepare_react_loop(
        ReactLoopRequest(0, _context().observed_at, waking))
    assert agent.memory_sleep_boundary() is True
