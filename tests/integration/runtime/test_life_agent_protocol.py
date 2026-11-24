"""LifeAgent 闭集 Schema、宿主身份注入与无状态回合循环测试。

一次唤醒采用通用 AgentLoop 驱动单一连续对话：请求为原生 tools、系统层 system 与六层 user；全量九个原生工具在常规
回合开放（含 search_world 与两项只读查询），末回合与截断重试仅保留六个核心世界请求。控制工具与只读查询直接在当前沙盒
结算并回传结果及层差量，search_world 发起异步搜索并产出 SuspendedLoopState，有效开始动作提交并封口沙盒。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.integration.runtime.current_contract_fixtures import (
    ScriptedModel,
    decide,
    inputs,
    resolved_binding,
    turns_with_history,
)
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, errand_add, errand_update, main_arguments, side_arguments
from worldmock.agents.life_agent import (
    LifeAgent,
    MEMORY_SEARCHES_PER_DECISION,
    PersonInputs,
    _life_turn_tools,
    life_action_tool_names,
    life_query_tool_names,
    life_tool_schema_sha256,
    person_inputs,
    validate_life_batch,
)
from worldmock.agents.memory.life_memory import MemoryStep, memory_update_evidence, save_step
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes
from worldmock.agents.prompt.life_prompt import (
    LIFE_SYSTEM_LAYER,
    life_prompt_sha256,
    life_user_layers,
    system_message,
    user_layer,
    user_message,
)
from worldmock.agents.prompt.narration import ForbiddenObservationFieldError, render_layer_json
from worldmock.agents.prompt.prompt_schemas import USER_LAYER_NAMES
from worldmock.agents.protocol.agent_loop import (
    LoopToolCall,
    TURNS_EXHAUSTED,
    validate_single_tool_call,
)
from worldmock.agents.protocol.life_feedback import FEEDBACK_INSTRUCTION
from worldmock.configuration.model.model_client import REQUEST_KEYS
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS
from worldmock.foundation.errors import ProtocolError
from worldmock.runtime.lifecycle import run_host
from worldmock.runtime.lifecycle.run_host import RunHost
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING
from worldmock.simulation_engine.observation.memory.layout import assemble_layer
from worldmock.state.decisions.barrier import AgentWakeupInput, SandboxOutcome, ToolDispatchResult
from worldmock.state.decisions.round import SchedulingState
from worldmock.state.world.agent import AgentState, BodyState
from worldmock.state.world.world import WORLD_SCHEMA_VERSION, WorldState
from worldmock.state.interaction.actions import SEARCH_TOOL, START_TOOLS
from worldmock.state.interaction.control_items import control_items
from worldmock.state.interaction.requests import (
    ConcludeActivityRequest,
    REQUEST_MODELS,
    ReportStatusRequest,
    StartMainActivityRequest,
    StartSideActivityRequest,
)
from worldmock.state.interaction.schema import deepseek_strict_schema

#: 一次合法的检索参数与替身环境给出的检索回答。
_SEARCH = '{"category":"全部","time":"今天","keyword":null}'
_SEARCH_ARGS = {"category": "全部", "time": "今天", "keyword": None}
_SEARCH_ANSWER = {"类别": "全部", "时间": "今天", "关键词": None, "结果": [], "未列出": []}

#: 全量工具名与核心世界工具名（九个全量 = 6 核心世界 + search_world + 2 只读查询）。
_BASE_ACTION_NAMES = set(life_action_tool_names())
_CORE_ACTION_NAMES = _BASE_ACTION_NAMES - {SEARCH_TOOL, "recall_task_summary"}
_READ_QUERY_NAMES = set(life_query_tool_names())
_FULL_TOOL_NAMES = _BASE_ACTION_NAMES | _READ_QUERY_NAMES

_OBSERVED_AT = datetime(2026, 1, 5, tzinfo=timezone.utc)
_NOW_EPOCH = int(_OBSERVED_AT.timestamp())
_NOW_ISO = "2026-01-05T00:00:00+00:00"

#: 当前停留会话、活动与任务的可读名称。
_HOME_SESSION = "自己家（1月4日 18:00 起）"
_WAIT = "等待（1月4日 23:45 起）"
_WORK = "整理会议纪要（1月4日 21:00 起）"
_NEWS = "接着看晚间新闻（1月4日 20:00 起）"
_WORK_ACTION = "整理会议纪要"

_TARGET_KINDS = {"app": "app", "supply": "product", "location": "location"}
_TARGET_TOOLS = {"activity": ("report_status", "pause_activity", "conclude_activity")}


def _offer(action: str, note: str | None = None, detail: str | None = None, **private) -> dict:
    """私有绑定的一条动作行。"""
    return {"action": action, "detail": detail, "note": note, **private}


def _unit(name: str, *content_ids: str, note: str | None = None, episode_titles: tuple[str, ...] = ()) -> dict:
    """当前地点的一个可点名内容单元在私有绑定里的动作行。"""
    return _offer("interact", note, name, content_ids=list(content_ids), episode_titles=list(episode_titles))


def _tool_entries(kind: str) -> list[dict]:
    """工具层目标的 tools[]。"""
    return [{"tool": tool, "available": True, "reason": None} for tool in _TARGET_TOOLS.get(kind, ())]


def _visible_object(name: str, kind: str, object_id: str, *offers: dict, **binding) -> tuple[dict, dict]:
    """同一可见对象的工具层目标与私有绑定。"""
    target_kind = _TARGET_KINDS[kind]
    actions = [{"action": item["action"], "detail": item["detail"], "available": item["note"] is None,
                "reason": item["note"], "price": None, "duration": None, "quantity": None, "note": None}
               for item in offers]
    target = {"name": name, "kind": target_kind, "phase": None, "state": None, "actions": actions,
              "tools": _tool_entries(target_kind)}
    return target, {"object": name, "kind": kind, "object_id": object_id, "actions": list(offers), **binding}


def _add_objects(data: dict, *objects: tuple[dict, dict]) -> dict:
    """把可见对象的工具层目标与私有绑定分别追加到观察的工具层与 interaction_bindings。"""
    for target, binding in objects:
        data["tool_layer"]["targets"].append(copy.deepcopy(target))
        data["interaction_bindings"].append(copy.deepcopy(binding))
    return data


_REST = "歇一会儿"
_SELF_OBJECT = _visible_object("自己家", "location", "home",
                               _unit(_REST, "rest-visible"), _offer("sleep"))
_WORK_OBJECT = _visible_object(_WORK_ACTION, "app", "work-visible",
                               _offer("interact", "整理会议纪要需要在办公室"))
_WAIT_CHOICE = main_arguments("interact", "自己家", _REST)
_WORK_CHOICE = side_arguments("interact", _WORK_ACTION)
_BODY = {"appetite": "不饿", "sleepiness": "有点乏", "relaxation": "不闷", "social": "不想找人"}


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求和执行合同。"""
    from worldmock.agents.prompt import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _wakeup(
    observation: dict | None = None,
    *,
    wakeup_id: str = "wakeup-v3-a7-1767571200",
    agent_id: int = 7,
    mode: str = "regular",
    action_seq_start: int = 1,
    suspended_loop=None,
    search_result_payload: dict | None = None,
) -> AgentWakeupInput:
    """构造单次人物唤醒不可变快照 AgentWakeupInput。"""
    obs = copy.deepcopy(observation) if observation is not None else _observations()[0]["data"]
    return AgentWakeupInput(
        wakeup_id=wakeup_id,
        agent_id=agent_id,
        observed_at=int(obs["current_time"]),
        world_state_version=3,
        mode=mode,
        observation=obs,
        observation_parts={},
        pending_presentation={},
        action_seq_start=action_seq_start,
        suspended_loop=suspended_loop,
        search_result_payload=search_result_payload,
    )


def _memory(text: str = "我歇了一会儿，心里踏实了些。", *, kind: str = "routine", importance: float = 0.5,
            keywords: tuple[str, ...] = ("休息",)) -> dict:
    """一条普通收尾记忆 {type, text, importance, keywords}。"""
    return {"type": kind, "text": text, "importance": importance, "keywords": list(keywords)}


def _view(about: str, text: str = "和同事处得不错。", *, kind: str = "relationship", importance: float = 0.5,
          keywords: tuple[str, ...] = ("同事",)) -> dict:
    """一条看法或偏好。"""
    return {"type": kind, "about": about, "text": text, "importance": importance, "keywords": list(keywords)}


def _conclude_args(*names: str, memories: list[list[dict]] | None = None,
                   views: list[list[dict]] | None = None) -> dict:
    """conclude_activity 的参数。"""
    groups = memories if memories is not None else [[_memory()] for _ in names]
    seen = views if views is not None else [[] for _ in names]
    return {"items": [{"activity_name": name, "summary": "先收个尾。", "memories": group, "views": opinions}
                      for name, group, opinions in zip(names, groups, seen, strict=True)], "errands": []}


def _control_args(tool: str, *names: str, summary: str = "写完提纲") -> dict:
    """三个控制工具的参数。"""
    extra = {"memories": [], "views": []} if tool == "conclude_activity" else {}
    return {"items": [{"activity_name": name, "summary": summary, **extra} for name in names], "errands": []}


def _stored_memories(args: dict) -> list[dict]:
    """把 conclude_activity 逐项的记忆与看法展平成存储形态。"""
    return [dict(memory) for item in control_items("conclude_activity", args) for memory in item.memories]


def _layers() -> dict:
    """环境构建的五层与看法偏好。"""
    return {
        "tool_layer": {"layer": "工具层", "targets": copy.deepcopy([_SELF_OBJECT[0], _WORK_OBJECT[0]]),
                       "callable_index": {"navigation_apps": [], "payment_apps": [], "goods": [], "prefer_apps": []}},
        "todo_layer": {"layer": "待办层", "paused_activities": [], "errands": [], "orders": [], "day_plan": None},
        "environment_layer": {"layer": "环境层", "observed_at": _NOW_ISO, "body": dict(_BODY),
                              "location": {"session_kind": "stay", "name": "自己家", "place_type": "住宅",
                                           "fixed_rules": []}},
        "memory_layer": assemble_layer(None, {}, []),
        "persona_views": {"relationships": [], "preferences": []},
        "progress_layer": {"layer": "进度层", "session_started_at": "2026-01-04T18:00:00+00:00", "events": []},
        "progress_meta": [],
    }


def _observations(*progress_event_ids: str) -> list[dict]:
    """构造结构化观察记录。"""
    data = {
        "observation_event_id": "observation-current",
        "current_time": _NOW_EPOCH,
        "wake_reasons": [],
        **_layers(),
        "progress_event_ids": list(progress_event_ids),
        "presented_message_ids": [],
        "interaction_bindings": copy.deepcopy([_SELF_OBJECT[1], _WORK_OBJECT[1]]),
        "activity_bindings": [],
        "goal_bindings": [],
        "catalog_bindings": [],
        "errand_bindings": [],
        "errand_item_bindings": [{"name": "现有食品", "product_id": "product-visible"}],
        "agent_id": 7, "verified_experiences": [], "occupied_resources": [],
        "active_activities": [],
        "paused_activities": [],
        "pending_conclusions": [],
        "interaction_menu": {"allowed_actions": sorted(ACTIVITY_ACTIONS)},
        "actions": [{"action_id": "work-visible", "name": _WORK_ACTION, "category": "work_study",
                     "manner": None, "app_ids": [], "communication_mode": None}],
        "apps": [], "contents": [], "app_pushes": {},
        "activity_summary": {"exercise": 44, "goods_received": 1},
        "messages": [{"message_id": "message-visible"}],
        "goods": [{"goods_id": "product-visible", "name": "现有食品", "category": "snack",
                   "price_fen": 500, "store_ids": ["store"]}],
        "locations": [{"location_id": "home", "name": "自己家", "kind": "home"},
                      {"location_id": "office", "name": "办公室", "kind": "office"}],
        "location_id": "home", "home_location_id": "home",
        "routes": [{"destination_id": "office", "status": "reachable", "mode": "driving",
                    "duration_seconds": 216}],
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def _activity_target(name: str, phase: str) -> dict:
    """进行中或刚结束活动的工具层目标。"""
    return {"name": name, "kind": "activity", "phase": "进行中" if phase == PHASE_ACTIVE else "刚结束",
            "state": "已做约 15 分钟", "actions": [], "tools": _tool_entries("activity")}


def _with_activities(data: dict, *activities: tuple[str, str], elsewhere: tuple[str, ...] = ()) -> dict:
    """在当前停留会话下加入活动，同步写入工具层或待办层、私有活动绑定与活动证据。"""
    for name, phase in activities:
        identity = f"activity-private-{len(data['activity_bindings']) + 1}"
        data["activity_bindings"].append({"name": name, "activity_id": identity, "phase": phase,
                                          "resumable": phase == PHASE_PAUSED and name not in elsewhere,
                                          "note_labels": []})
        control = {"activity_id": identity, "kind": "interact", "title": name, "progress": "已做约15分钟",
                   "elapsed_seconds": 900, "planned_duration_seconds": 1800, "data": {}}
        if phase == PHASE_PAUSED:
            resume = ({"tool": "report_status", "available": False, "reason": "要回到办公室才能接着做"}
                      if name in elsewhere else {"tool": "report_status", "available": True, "reason": None})
            data["todo_layer"]["paused_activities"].append({
                "name": name, "paused_at": "2026-01-04T23:50:00+00:00",
                "place": "办公室" if name in elsewhere else "自己家", "progress": "已做约 15 分钟",
                "tools": [resume, {"tool": "conclude_activity", "available": True, "reason": None}]})
            data["paused_activities"].append({**control, "status": "paused"})
            continue
        data["tool_layer"]["targets"].append(_activity_target(name, phase))
        if phase == PHASE_ACTIVE:
            data["active_activities"].append({**control, "status": "active"})
        elif phase == PHASE_PENDING:
            data["pending_conclusions"].append({**control, "status": "completed"})
    return data


def _step_observation() -> dict:
    """带一项进行中等待的观察。"""
    return _with_activities(_observations()[0]["data"], (_WAIT, PHASE_ACTIVE))


def _functions(remaining: int = 2, retry: bool = False) -> dict[str, dict]:
    """开放的原生工具 Schema（名称 → function 定义）。"""
    return {item["function"]["name"]: item["function"] for item in _life_turn_tools(remaining, retry)[0]}


def _person_7(name: str = "Alice") -> PersonInputs:
    """构造 7 号人物冻结人设输入。"""
    frozen = inputs()
    return replace(person_inputs(frozen.persona_by_id[1]), agent_id=7, name=name)


def _binding(dispatcher, *, stage: str = "action", max_turns: int = 16):
    """为替身客户端构造真实 ModelRegistry 解析出的绑定。"""
    return resolved_binding(dispatcher, stage=stage, agent_id=1, max_turns=max_turns)


def _stub_world() -> WorldState:
    """@brief 仅用于沙盒桩返回 SandboxOutcome 的最小合法 WorldState。"""
    body = BodyState(
        energy_kcal=1800.0, sleep_pressure=0.1, sleep_deficit_hours=0.0, sleep_need_hours=7.5,
        chronotype_midsleep=4.0, social_need=0.2, relaxation_need=0.2, social_need_growth_per_hour=0.06,
    )
    agent = AgentState(
        agent_id=7, name="林舟", location_id="home", balance_account_id="agent:7",
        body=body, body_settled_at=1767571200, next_decision_at=1767571200,
    )
    return WorldState(
        schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=1767571200, end_time=1767657600,
        run_id="run-1", timezone="Asia/Shanghai", persona_sha256s={7: "0" * 64},
        config_sha256="0" * 64, agents={7: agent}, accounts={"agent:7": 0, "external:scenario": 0},
        random_seed=7, random_state="0" * 64,
    )


class _SandboxStub:
    """实现 SandboxHandle 协议的沙盒替身，捕获查询、世界工具派遣、模型证据与封口挂起。"""

    def __init__(self, observation: dict | None = None, *, dispatch_error: Exception | None = None) -> None:
        self.calls: list[SimpleNamespace] = []
        self.queries: list[tuple[str, Any]] = []
        self.evidence: list[Any] = []
        self.pending_memories: list[dict] = []
        self.suspended = None
        self.finished = False
        self.dispatch_error = dispatch_error
        self._obs = copy.deepcopy(observation) if observation is not None else _observations()[0]["data"]

    def search_memory(self, request) -> dict[str, Any]:
        self.queries.append(("search_memory", request))
        return dict(_SEARCH_ANSWER)

    def recall_task_summary(self, request) -> dict[str, Any]:
        self.queries.append(("recall_task_summary", request))
        return {"task": getattr(request, "task_name", ""), "summary": "暂无进展摘要。"}

    def dispatch_world_tool(self, tool_name: str, arguments, call_id: str,
                            note: str | None = None) -> ToolDispatchResult:
        if self.dispatch_error is not None:
            raise self.dispatch_error
        req_dict = arguments.model_dump(mode="json") if hasattr(arguments, "model_dump") else dict(arguments)
        memories = ([dict(item) for grp in control_items(tool_name, req_dict) for item in grp.memories]
                    if tool_name == "conclude_activity" else [])
        self.pending_memories.extend(memories)
        self.calls.append(SimpleNamespace(action_id=call_id, tool_name=tool_name, request=arguments, note=note))
        terminal = tool_name in START_TOOLS or tool_name == SEARCH_TOOL
        suspended = tool_name == SEARCH_TOOL
        payload: dict[str, Any] = {
            "result": {"status": "active" if suspended else ("in_progress" if terminal else "accepted")},
            "changes": {}, "events": [],
        }
        if suspended:
            payload["activity_id"] = "search-act-1"
        return ToolDispatchResult(accepted=True, terminal_exit=terminal, suspended=suspended,
                                  tool_message_payload=payload, pending_memories=tuple(memories))

    def record_model_exchange(self, exchange) -> None:
        self.evidence.append(exchange)

    def finish_wakeup(self, suspended) -> SandboxOutcome:
        self.finished = True
        self.suspended = suspended
        status = "suspended" if suspended is not None else "completed"
        return SandboxOutcome(
            wakeup_id="wakeup-v3-a7-1767571200", agent_id=7, status=status,
            state=_stub_world(), evidence_drafts=(), observation_parts={},
            pending_presentation={}, accepted_action_ids=tuple(c.action_id for c in self.calls),
            pending_memories=tuple(self.pending_memories), verified_experiences=(),
            observation_event_id=str(self._obs["observation_event_id"]),
            observation_payload=self._obs, memory_evidence=None,
        )


def test_life_agent_schema_is_closed_and_host_fields_are_hidden() -> None:
    """常规回合开放全量九个原生工具，末回合与重试只留六个核心世界请求；宿主身份不入 Schema。"""
    functions = _functions(remaining=2, retry=False)
    assert set(functions) == _FULL_TOOL_NAMES
    assert set(list(functions)[:len(REQUEST_MODELS)]) == set(REQUEST_MODELS)
    assert list(functions)[:2] == [MAIN_TOOL, SIDE_TOOL]
    assert not {"start_activity", "finish", "memory_step_range", "memory_tick_range"} & set(functions)
    assert all(function["strict"] is True for function in functions.values())
    assert set(_functions(remaining=1, retry=False)) == _CORE_ACTION_NAMES
    assert set(_functions(remaining=4, retry=True)) == _CORE_ACTION_NAMES
    closed = copy.deepcopy({name: functions[name] for name in (MAIN_TOOL, SIDE_TOOL, SEARCH_TOOL, "report_status")})
    for tool, fields, actions in (
        (MAIN_TOOL, ["action", "target", "detail"], ["travel", "interact", "sleep"]),
        (SIDE_TOOL, ["action", "target", "prefer"], ["purchase", "interact"]),
    ):
        parameters = closed[tool]["parameters"]
        assert {"agent_id", "wakeup_id", "action_id"}.isdisjoint(parameters["properties"])
        assert list(parameters["properties"]) == parameters["required"] == fields
        assert parameters["additionalProperties"] is False
        assert parameters["properties"].pop("action")["enum"] == actions
    search_params = closed[SEARCH_TOOL]["parameters"]
    assert list(search_params["properties"]) == search_params["required"] == ["query", "prefer"]
    assert search_params["additionalProperties"] is False
    change = closed["report_status"]["parameters"]["properties"]["errands"]["items"]["properties"]
    assert change.pop("change")["enum"] == ["新增", "更新", "取消"]
    assert '"enum"' not in json.dumps(closed)
    assert len(life_prompt_sha256()) == 64
    assert life_tool_schema_sha256() == life_tool_schema_sha256()


def test_plan_day_is_no_person_tool_and_revision_carries_remind_time() -> None:
    """请求模型共八个（无 plan_day、含 search_world 与 recall_task_summary），revise_day_plan 整次运行都在。"""
    assert len(REQUEST_MODELS) == 8 and "plan_day" not in REQUEST_MODELS
    assert {MAIN_TOOL, SIDE_TOOL, SEARCH_TOOL} <= set(REQUEST_MODELS)
    assert "start_activity" not in REQUEST_MODELS
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)
    functions = _functions(2, False)
    assert "plan_day" not in functions and "revise_day_plan" in functions
    tool = functions["revise_day_plan"]
    assert tool["description"] == "用完整的新清单替换今日计划中的目标。"
    assert set(tool["parameters"]["properties"]) == {"goals"}
    fields = tool["parameters"]["properties"]["goals"]["items"]["properties"]
    assert set(fields) == {"intent_kind", "goal", "remind_time", "around_time"}
    assert tool["parameters"] == deepseek_strict_schema(REQUEST_MODELS["revise_day_plan"])
    assert fields["goal"]["pattern"] == r"\S"
    for clock in ("remind_time", "around_time"):
        assert fields[clock]["anyOf"] == [{"pattern": "^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", "type": "string"},
                                          {"type": "null"}]
    assert "day_start_epoch" not in json.dumps(tool)


def test_how_to_live_a_day_is_one_resident_section_of_the_life_system_layer() -> None:
    """起居与搜索恢复规则只在系统层 rules 写一次，「怎样过一天」已删。"""
    action_system = system_message()["content"]
    assert action_system == render_layer_json(LIFE_SYSTEM_LAYER)
    rules = json.loads(action_system)["rules"]
    for sentence in ("同时最多两项活动", "睡着后到你写的时长才会再请你决策", "商品实际收到时自动吃掉或用掉",
                     "都只是你的想法和回忆，不能改变身体、资金、内容进度或订单结果", "工作安排由环境给出",
                     "没有进行中的活动时时间不会往前走", "打开后放完一条自动接下一条", "环境也不按它们限制你此刻能做什么",
                     "系列的 note 或进行中活动的 state", "今日计划只是自己的打算", "available 为 true 的动作单独提交都会受理",
                     "个人事务只记要买到或取到的商品", "调用 search_world 会发起异步搜索并挂起本次唤醒"):
        assert sum(sentence in rule for rule in rules) == 1
    assert sum("同时最多两项活动，槽位 1 和槽位 2 各放一项" in rule for rule in rules) == 1
    assert sum("醒着时，手上的事做完" in rule for rule in rules) == 1
    assert all("名额" not in rule and "睡够了" not in rule for rule in rules)
    assert sum("不保存库存" in rule for rule in rules) == 1
    assert "怎样过一天" not in action_system and "你不是AI助手" not in action_system
    for banned in ("评审", "评分", "引擎期望", "budget", "based_on_action_id", "supply_id", "正确答案", "环境期望"):
        assert banned not in action_system.replace("店铺评分", "")


def test_location_norm_is_removed_from_static_tool_and_system_text() -> None:
    """地点行为改由菜单声明，静态工具描述和系统层均不保留办公室散文。"""
    schemas = {name: fn["description"] for name, fn in _functions(2, False).items()}
    for tool in (MAIN_TOOL, SIDE_TOOL):
        assert "at the office, prefer brief phone-friendly content" not in schemas[tool]
        assert "save movies and long drama episodes" not in schemas[tool]
        assert "office" not in schemas[tool] and "办公室" not in schemas[tool]
    flattened = " ".join(system_message()["content"].split())
    assert "watching long entertainment openly at the office" not in flattened
    assert "inappropriate" not in flattened


def test_feedback_boundary_exposes_readable_activity_controls() -> None:
    """三个控制工具整次运行都在，参数结构保持可读活动名与无私有身份；具体名称由沙盒执行期核对。"""
    _, models = _life_turn_tools(2, False)
    functions = _functions(2, False)
    for name in ("report_status", "pause_activity", "conclude_activity"):
        fields = functions[name]["parameters"]["properties"]
        assert set(fields) == {"items", "errands"}
        item = fields["items"]["items"]["properties"]
        with_memories = name == "conclude_activity"
        assert set(item) == ({"activity_name", "summary", "memories", "views"} if with_memories
                             else {"activity_name", "summary"})
        assert "enum" not in item["activity_name"]
        assert validate_single_tool_call(LoopToolCall(name, _control_args(name, _WORK), "c1"), models) is None
        repeated = _control_args(name, _WORK, _WORK)
        fb = validate_single_tool_call(LoopToolCall(name, repeated, "c2"), models)
        assert fb is not None and "activity names must be distinct" in fb.text
    assert "activity-private-1" not in json.dumps(functions, ensure_ascii=False)


_BATCH_REASON = ("Submit one world request, start_main_activity together with start_side_activity, "
                 "or a batch containing only read queries.")
_BATCH_FIX = "Submit one world request, the two start tools together, or only read queries."


def test_life_agent_rejects_invalid_or_non_atomic_batches() -> None:
    """合法批次只有三种：全为只读查询、恰好一个世界请求（含 search_world）、两个开始工具各一次。"""
    _, models = _life_turn_tools(2, False)
    valid = LoopToolCall(MAIN_TOOL, _WAIT_CHOICE, "c1")
    side = LoopToolCall(SIDE_TOOL, _WORK_CHOICE, "c2")
    search = LoopToolCall(SEARCH_TOOL, {"query": "咖啡", "prefer": None}, "cs")
    memory = LoopToolCall("search_memory", _SEARCH_ARGS, "cm")
    revise = LoopToolCall("revise_day_plan", {"goals": []}, "cr")

    for legal in ([valid], [side], [search], [valid, side], [side, valid], [memory], [memory, memory], [revise]):
        assert validate_life_batch(legal, models) is None
    for batch in ([valid, valid], [side, side], [search, valid], [valid, search], [memory, valid],
                  [valid, revise], [revise, side], [valid, side, memory], [revise, revise]):
        report = validate_life_batch(batch, models).report
        assert report.error_code == "TOOL_CALL_REQUIRED" and report.reason == _BATCH_REASON
        assert report.fix == _BATCH_FIX
        assert (report.items[0].path, report.items[0].written) == (("tool_calls",), [item.tool for item in batch])
    invalid = LoopToolCall(MAIN_TOOL, main_arguments("invented_action", "自己家", _REST), "ci")
    detail = validate_single_tool_call(invalid, models).report.payload()["items"][0]
    assert (detail["error_code"], detail["written"]) == ("ARGUMENTS_INVALID", "invented_action")
    assert detail["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert detail["allowed_values"] == ["travel", "interact", "sleep"]
    finish = LoopToolCall("finish", {"memories": []}, "cf")
    assert validate_single_tool_call(finish, models).report.items[0].path[-1] == "name"


@pytest.mark.asyncio
async def test_wakeup_messages_are_system_layer_and_six_layer_user() -> None:
    """消息为系统层 system 与六层 user；tool_layer 经 compact_render 渲染成 groups，黑名单字段永不入提示。"""
    obs = _observations("result-prior")[0]["data"]
    sandbox = _SandboxStub(obs)
    dispatcher = _Dispatcher()
    dispatcher.responses.pop(0)  # 单回合直接开始动作
    agent = LifeAgent(_person_7())
    await agent.decide_wakeup(_wakeup(obs), sandbox, _binding(dispatcher, max_turns=4))

    request = dispatcher.requests[0]
    messages = request["messages"]
    layers = json.loads(messages[1]["content"])
    assert [item["role"] for item in messages] == ["system", "user"]
    assert [layer["layer"] for layer in layers] == list(USER_LAYER_NAMES)
    for name, key in (("人设层", None), ("记忆层", "memory_layer"), ("待办层", "todo_layer"), ("工具层", "tool_layer"),
                      ("环境层", "environment_layer"), ("进度层", "progress_layer")):
        layer = user_layer(layers, name)
        if key == "tool_layer":
            assert "groups" in layer and "targets" not in layer
            assert layer["callable_index"] == obs[key]["callable_index"]
        elif key is not None:
            assert layer == obs[key]
    assert user_layer(layers, "人设层")["identity"]["name"] == "Alice"
    for leaked in ("observation-current", "result-prior", "game-visible", "session-7-0001", "task-visible"):
        assert leaked not in messages[1]["content"]


def test_blacklisted_observation_fields_never_enter_messages() -> None:
    """黑名单字段不得进入任何消息；层中混入禁键即由 render_layer_json 拒绝。"""
    data = _with_activities(_observations()[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING))
    data["motivation_internal"] = {"candidates": [{"kind": "eat", "score": 0.9}]}
    data["world_state_version"] = 42
    data["end_time"] = _NOW_EPOCH + 86_400
    layers = life_user_layers(data, {"identity": {"name": "Alice"}})
    text = user_message(layers)["content"]
    for banned in ("motivation_internal", "motivation_hints", "world_state_version",
                   "end_time", "interaction_bindings", "observation_event_id",
                   "activity_bindings", "pending_conclusions", "activity-private-1", "progress_meta"):
        assert banned not in text
    assert _WAIT in text and "刚结束" in text
    leaking = _observations()[0]["data"]
    leaking["tool_layer"]["targets"][0]["activity_id"] = "activity-private-9"
    with pytest.raises(ForbiddenObservationFieldError, match="activity_id"):
        user_message(life_user_layers(leaking, {"identity": {"name": "Alice"}}))


class _ModelResponse:
    """提供原生工具属性及可持久化响应。"""

    def __init__(self, call_id: str, name: str, arguments: str,
                 reasoning_content: str | None = None, content: str = "") -> None:
        function = SimpleNamespace(name=name, arguments=arguments)
        tool_call = SimpleNamespace(id=call_id, type="function", function=function)
        message = SimpleNamespace(
            tool_calls=[tool_call], content=content, reasoning_content=reasoning_content)
        self.choices = [SimpleNamespace(message=message)]
        self._call_id = call_id
        self._name = name
        self._arguments = arguments
        self._reasoning_content = reasoning_content
        self._content = content

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {"choices": [{"message": {"content": self._content, "tool_calls": [{
            "id": self._call_id,
            "type": "function",
            "function": {"name": self._name, "arguments": self._arguments},
        }], "reasoning_content": self._reasoning_content}}]}


class _MultipleModelResponse:
    """提供同一模型响应中的多个原生工具调用。"""

    def __init__(self, calls: list[tuple[str, str, str]]) -> None:
        self._calls = calls
        tool_calls = [
            SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments))
            for call_id, name, arguments in calls
        ]
        self.choices = [SimpleNamespace(message=SimpleNamespace(tool_calls=tool_calls, content=""))]

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        tool_calls = [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            for call_id, name, arguments in self._calls
        ]
        return {"choices": [{"message": {"tool_calls": tool_calls}}]}


def _assert_action_wire(request: dict) -> None:
    """核对行动请求生成参数。"""
    assert request["tool_choice"] == "auto"
    assert request["max_tokens"] == 16000
    assert sorted(request) == sorted(REQUEST_KEYS)
    assert "reasoning_effort" not in request and "temperature" not in request
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert all(item["function"]["strict"] is True for item in request["tools"])


class _Dispatcher:
    """按顺序返回一次检索记忆与唯一生活动作。"""

    model_name = "test-model"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses = [
            _ModelResponse("call-memory", "search_memory", _SEARCH),
            _ModelResponse(
                "call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False),
                reasoning_content="Tired person needs a short rest before work.",
                content="  先歇一分钟。  ",
            ),
        ]

    async def call(self, request):
        self.requests.append(copy.deepcopy(request))
        _assert_action_wire(request)
        return self.responses.pop(0)


class _StepDispatcher:
    """返回一次带记忆结束等待的 conclude_activity。"""

    model_name = "test-model"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses = [
            _ModelResponse("call-action", "conclude_activity", json.dumps(_conclude_args(_WAIT), ensure_ascii=False)),
        ]

    async def call(self, request):
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


class _GroundedCorrectionDispatcher:
    """复现纠错序列。"""

    model_name = "test-model"

    def __init__(self, responses: list[_ModelResponse] | None = None) -> None:
        self.requests: list[dict] = []
        defaults = [
            _ModelResponse("call-memory", "search_memory", _SEARCH),
            _ModelResponse("call-invalid", "play_game", '{"game_id":"game-visible","planned_duration_seconds":600}'),
            _ModelResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
        ]
        self.responses = list(responses) if responses is not None else defaults

    async def call(self, request):
        self.requests.append(copy.deepcopy(request))
        return self.responses.pop(0)


def _react_tool_names(requests: list[dict]) -> list[set[str]]:
    return [{item["function"]["name"] for item in request["tools"]} for request in requests]


@pytest.mark.asyncio
async def test_native_loop_submits_one_host_bound_life_action() -> None:
    """登记前的一次检索记忆与唯一动作共两次请求；检索结果写入 tool 消息，开始动作退出循环不追加 tool 消息。"""
    obs = _observations("result-prior")[0]["data"]
    sandbox = _SandboxStub(obs)
    dispatcher = _Dispatcher()
    agent = LifeAgent(_person_7())
    wakeup = _wakeup(obs)
    outcome, history = await turns_with_history(agent, wakeup, sandbox, _binding(dispatcher, max_turns=4))

    assert outcome.status == "completed" and len(sandbox.calls) == 1
    call = sandbox.calls[0]
    assert call.action_id == f"{wakeup.wakeup_id}:action:0001"
    assert call.tool_name == MAIN_TOOL and isinstance(call.request, StartMainActivityRequest)
    assert call.request.model_dump(mode="json") == _WAIT_CHOICE
    assert [item.phase for item in sandbox.evidence] == ["request", "response"] * 2
    assert len(dispatcher.requests) == 2
    first, second = dispatcher.requests
    assert [item["role"] for item in first["messages"]] == ["system", "user"]
    assert [item["role"] for item in second["messages"]] == ["system", "user", "assistant", "tool"]
    memory = second["messages"][-1]
    assert memory["tool_call_id"] == "call-memory"
    assert memory["content"] == json.dumps(_SEARCH_ANSWER, ensure_ascii=False, separators=(",", ":"))
    assert [tool for tool, _ in sandbox.queries] == ["search_memory"]
    assert _react_tool_names([first, second]) == [_FULL_TOOL_NAMES] * 2
    assert history[-1]["role"] == "assistant"
    assert history[-1]["tool_calls"][0]["id"] == "call-action"
    assert sandbox.pending_memories == []


@pytest.mark.asyncio
async def test_reasoning_content_is_persisted_in_model_response_evidence() -> None:
    """思考开启后模型推理与回复正文随现有序列化进入 model_response 足迹证据。"""
    obs = _observations("result-prior")[0]["data"]
    sandbox = _SandboxStub(obs)
    dispatcher = _Dispatcher()
    await LifeAgent(_person_7()).decide_wakeup(_wakeup(obs), sandbox, _binding(dispatcher, max_turns=4))

    requests = [item for item in sandbox.evidence if item.phase == "request"]
    responses = [item for item in sandbox.evidence if item.phase == "response"]
    for item in requests:
        _assert_action_wire(item.request)
    memory_message = responses[0].response["choices"][0]["message"]
    assert memory_message["tool_calls"][0]["function"]["name"] == "search_memory"
    assert memory_message["reasoning_content"] is None
    action_message = responses[1].response["choices"][0]["message"]
    assert action_message["reasoning_content"] == "Tired person needs a short rest before work."
    assert action_message["content"] == "  先歇一分钟。  "
    assert action_message["tool_calls"][0]["function"]["name"] == MAIN_TOOL


@pytest.mark.asyncio
async def test_budgeted_memory_search_preserves_the_action_turn() -> None:
    """三轮查记忆后仅剩最后 1 回合：只开放 6 核心世界工具，随后顺利提交动作。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "search_memory", _SEARCH),
        _ModelResponse("call-memory-2", "search_memory", _SEARCH),
        _ModelResponse("call-memory-3", "search_memory", _SEARCH),
        _ModelResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ])
    sandbox = _SandboxStub()
    wakeup = _wakeup()
    await LifeAgent(_person_7()).decide_wakeup(wakeup, sandbox, _binding(dispatcher, max_turns=4))

    assert len(sandbox.calls) == 1
    assert _react_tool_names(dispatcher.requests) == [
        _FULL_TOOL_NAMES, _FULL_TOOL_NAMES, _FULL_TOOL_NAMES, _CORE_ACTION_NAMES,
    ]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in sandbox.evidence[::2]] == ["0001", "0002", "0003", "0004"]


@pytest.mark.asyncio
async def test_multiple_memory_calls_share_one_model_turn() -> None:
    """同一响应的多个检索调用只消耗一个模型回合。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _MultipleModelResponse([
            ("call-memory-1", "search_memory", _SEARCH),
            ("call-memory-2", "search_memory", _SEARCH),
        ]),
        _ModelResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ])
    sandbox = _SandboxStub()
    await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, _binding(dispatcher, max_turns=2))

    assert len(sandbox.calls) == 1
    assert [tool for tool, _ in sandbox.queries] == ["search_memory", "search_memory"]
    assert _react_tool_names(dispatcher.requests) == [_FULL_TOOL_NAMES, _CORE_ACTION_NAMES]
    assert [item["tool_call_id"] for item in dispatcher.requests[1]["messages"][3:]] == [
        "call-memory-1", "call-memory-2"]


@pytest.mark.asyncio
async def test_reserved_action_boundary_rejects_query_without_extra_request() -> None:
    """只剩末回合时非法检索零执行，回合用完后抛出 ProtocolError(TURNS_EXHAUSTED)。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "search_memory", _SEARCH),
        _ModelResponse("call-memory-2", "search_memory", _SEARCH),
    ])
    sandbox = _SandboxStub()
    with pytest.raises(ProtocolError, match=TURNS_EXHAUSTED):
        await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, _binding(dispatcher, max_turns=2))

    assert len(dispatcher.requests) == 2
    assert {item["function"]["name"] for item in dispatcher.requests[1]["tools"]} == _CORE_ACTION_NAMES
    assert sandbox.calls == [] and len(sandbox.queries) == 1
    assert [item.phase for item in sandbox.evidence] == ["request", "response"] * 2
    assert "TOOL_CALL_REQUIRED:" in sandbox.evidence[-1].error
    fb_items = sandbox.evidence[-1].response["protocol_feedback"]["items"]
    assert any(item["written"] == "search_memory" and "not offered" in item["why"] for item in fb_items)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)])
async def test_start_group_registers_both_start_requests_in_reply_order(order: tuple[str, str]) -> None:
    """同一次回复调用两个开始工具组成开始组：按回复先后依次登记并结束唤醒。"""
    arguments = {MAIN_TOOL: _WAIT_CHOICE, SIDE_TOOL: _WORK_CHOICE}
    models = {MAIN_TOOL: StartMainActivityRequest, SIDE_TOOL: StartSideActivityRequest}
    dispatcher = _GroundedCorrectionDispatcher(responses=[_MultipleModelResponse([
        (f"call-{tool}", tool, json.dumps(arguments[tool], ensure_ascii=False)) for tool in order])])
    sandbox = _SandboxStub()
    wakeup = _wakeup()
    outcome, history = await turns_with_history(
        LifeAgent(_person_7()), wakeup, sandbox, _binding(dispatcher, max_turns=4),
    )

    assert outcome.status == "completed" and len(dispatcher.requests) == 1
    assert [(call.action_id, call.tool_name) for call in sandbox.calls] == [
        (f"{wakeup.wakeup_id}:action:0001", order[0]), (f"{wakeup.wakeup_id}:action:0002", order[1])]
    for call in sandbox.calls:
        assert type(call.request) is models[call.tool_name]
        assert call.request.model_dump(mode="json") == arguments[call.tool_name]
    assert history[-1]["role"] == "assistant"
    assert [item["id"] for item in history[-1]["tool_calls"]] == [f"call-{tool}" for tool in order]
    assert not any(item["role"] == "tool" for item in history)


@pytest.mark.asyncio
async def test_grounded_unknown_tool_is_corrected_without_alias() -> None:
    """捏造旧工具名在同一连续循环内纠错后改交真实工具。"""
    sandbox = _SandboxStub()
    dispatcher = _GroundedCorrectionDispatcher()
    await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, _binding(dispatcher, max_turns=5))

    assert len(sandbox.calls) == 1 and isinstance(sandbox.calls[0].request, StartMainActivityRequest)
    assert sandbox.calls[0].request.model_dump() == _WAIT_CHOICE
    assert [item.phase for item in sandbox.evidence] == ["request", "response"] * 3
    assert len(dispatcher.requests) == 3
    correction = dispatcher.requests[2]
    assert correction["messages"][-1]["tool_call_id"] == "call-invalid"
    feedback = json.loads(correction["messages"][-1]["content"])
    assert feedback["error_code"] == "TOOL_CALL_REQUIRED"
    assert feedback["reason"].endswith("The tool call batch is invalid.")
    assert feedback["instruction"] == FEEDBACK_INSTRUCTION


@pytest.mark.asyncio
async def test_runtime_budget_handles_twelve_bad_responses_then_submits_once() -> None:
    """生产回合上限允许 12 次本地纠错后再成功提交一次世界请求。"""
    max_turns = inputs().config.models["reasoner"].max_react_turns
    responses = [_ModelResponse(f"bad-{index}", "invented_game", "{}") for index in range(12)]
    responses.append(_ModelResponse("action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)))
    dispatcher = _GroundedCorrectionDispatcher(responses=responses)
    sandbox = _SandboxStub()
    await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, _binding(dispatcher, max_turns=max_turns))

    assert len(dispatcher.requests) == 13 and len(sandbox.calls) == 1
    assert isinstance(sandbox.calls[0].request, StartMainActivityRequest)
    assert sum(item.error is not None for item in sandbox.evidence if item.phase == "response") == 12


@pytest.mark.asyncio
async def test_life_schema_and_sandbox_rejection_are_corrected_before_acceptance(engine_factory) -> None:
    """缺 action 的 Pydantic 错误在本地纠错，ACTION_NOT_OFFERED 由真实沙盒拒绝回传候选后在同一唤醒改投通过。"""
    from tests.life.conftest import stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine)
    rest = main_arguments("interact", "家", "测试休息")
    missing = copy.deepcopy(rest)
    missing.pop("action")
    wrong_pair = side_arguments("interact", "美团")
    _, requests, answer, results = await decide(engine, "schema-pair", [
        _ModelResponse("call-schema", MAIN_TOOL, json.dumps(missing, ensure_ascii=False)),
        _ModelResponse("call-pair", SIDE_TOOL, json.dumps(wrong_pair, ensure_ascii=False)),
        _ModelResponse("call-action", MAIN_TOOL, json.dumps(rest, ensure_ascii=False)),
    ])
    assert answer == [SIDE_TOOL, MAIN_TOOL] and results[0].status == "rejected" and results[1].status == "in_progress"
    assert len(requests) == 3
    assert json.loads(requests[1]["messages"][-1]["content"])["error_code"] == "ARGUMENTS_INVALID"
    rejected_msg = json.loads(requests[2]["messages"][-1]["content"])
    assert rejected_msg["result"]["error_code"] == "ACTION_NOT_OFFERED"


@pytest.mark.asyncio
async def test_invalid_conclusion_memories_are_corrected_before_registration() -> None:
    """收尾记忆含十六进制 ID 时由本地请求校验拒绝、纠错后再提交通过，工具面保持无 finish。"""
    obs = _step_observation()
    invalid = _conclude_args(_WAIT, memories=[[_memory("我记得 3e71a031bb3d4103 那会儿很累。")]])
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-invalid", "conclude_activity", json.dumps(invalid, ensure_ascii=False)),
        _ModelResponse("call-conclude", "conclude_activity", json.dumps(_conclude_args(_WAIT), ensure_ascii=False)),
        _ModelResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ])
    sandbox = _SandboxStub(obs)
    await LifeAgent(_person_7()).decide_wakeup(_wakeup(obs), sandbox, _binding(dispatcher, max_turns=4))

    assert [c.tool_name for c in sandbox.calls] == ["conclude_activity", MAIN_TOOL]
    assert "hexadecimal identifier" in sandbox.evidence[1].error
    feedback = json.loads(dispatcher.requests[1]["messages"][-1]["content"])
    assert (feedback["error_code"], feedback["reason"]) == (
        "ARGUMENTS_INVALID", "memory text contains a hexadecimal identifier")
    assert sandbox.pending_memories == _stored_memories(_conclude_args(_WAIT))


def test_native_memory_update_hashes_real_workspace_files(tmp_path) -> None:
    """步尾保存写盘后，首次更新的写入前视图摘要为 null、条目从 m_000001 起。"""
    store = WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ())
    outcome = save_step(store, MemoryStep("林舟", (), "observation-current",
                                          tuple(_stored_memories(_conclude_args(_WAIT))), _OBSERVED_AT))
    evidence = memory_update_evidence("wakeup-1", 7, store, outcome)
    assert outcome.created_episodes[0]["source"] == "activity_conclusion"
    assert evidence.wakeup_id == "wakeup-1"
    assert evidence.memory_before_sha256 is None
    assert evidence.created_episode_ids == ("m_000001",)


@pytest.mark.asyncio
async def test_wakeup_step_records_conclusion_memory_update(engine_factory, tmp_path) -> None:
    """经真实 RunHost 步尾保存：conclude_activity 的记忆写入工作区并记录 memory_updated。"""
    from tests.integration.host_run_fixtures import host_run, memory
    from tests.life.conftest import stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    run = await host_run(engine, tmp_path / "people" / "agent_0001")
    await run.start("interact", "家", "测试休息")
    await run.conclude(memory("测试收尾记录。"))
    updates = run.events("memory_updated")
    assert any(row.data["operation"] == "append_episodes" for row in updates)
    episodes = read_episodes(tmp_path / "people" / "agent_0001", 1)
    assert episodes[0]["source"] == "activity_conclusion" and episodes[0]["epistemic_status"] == "subjective"


@pytest.mark.asyncio
async def test_memory_persistence_failure_fails_run_before_commit(engine_factory, host_factory, monkeypatch, caplog):
    """屏障提交后步尾写盘失败：RunHost 写错误日志并记下 run_failed，原异常对象直接传播。"""
    from tests.life.conftest import advance_until_ready, stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    injected = OSError("injected append failure")
    model = ScriptedModel([_ModelResponse("c1", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息")))])
    host = host_factory(engine, model)
    await host.step()
    assert advance_until_ready(engine)
    name = next(iter(engine.context.state.activities.values())).name
    model.responses.extend([
        _ModelResponse("c2", "conclude_activity", json.dumps(_conclude_args(name), ensure_ascii=False)),
        _ModelResponse("c3", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"))),
    ])
    monkeypatch.setattr(WorldMockMemoryStore, "append_episodes", lambda *a, **kw: (_ for _ in ()).throw(injected))

    with caplog.at_level(logging.ERROR), pytest.raises(OSError, match="^injected append failure$") as caught:
        await host.run()
    assert caught.value is injected
    assert any(event.data["error"] == "OSError('injected append failure')"
               for event in engine.context.store.iter_events(engine.context.store.last_event_seq)
               if event.event_type == "run_failed")


@pytest.mark.asyncio
async def test_decision_failure_is_logged_once_and_reraised_unchanged(monkeypatch, caplog) -> None:
    """LifeAgent.decide_wakeup 失败记一条带异常链日志并原样重抛原异常对象。"""
    injected = RuntimeError("injected wakeup failure")
    dispatcher = ScriptedModel([])

    async def fail_call(_req):
        raise injected

    monkeypatch.setattr(dispatcher, "call", fail_call)
    sandbox = _SandboxStub()
    wakeup = _wakeup()
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as caught:
        await LifeAgent(_person_7()).decide_wakeup(wakeup, sandbox, _binding(dispatcher, max_turns=4))
    assert caught.value is injected
    records = [r for r in caplog.records if r.getMessage().startswith("LifeAgent wakeup failed for agent 7")]
    assert len(records) == 1 and records[0].exc_info[1] is injected
    assert sandbox.calls == [] and sandbox.finished is False


def test_home_identity_never_enters_messages_and_a_layer_carrying_it_is_rejected() -> None:
    """home_location_id 是观察私有事实，混入六层任一层均触发 ForbiddenObservationFieldError。"""
    obs = _observations()[0]["data"]
    obs["home_location_id"] = "home-private-1"
    obs["locations"][0]["location_id"] = "home-private-1"
    obs["location_id"] = "home-private-1"
    text = user_message(life_user_layers(obs, {"identity": {"name": "Alice"}}))["content"]
    assert "home_location_id" not in text and "home-private-1" not in text
    for path in (("tool_layer", "targets", 0), ("todo_layer",), ("environment_layer",),
                 ("environment_layer", "location"), ("memory_layer",), ("progress_layer",)):
        leaking = _observations()[0]["data"]
        node = leaking
        for step in path:
            node = node[step]
        node["home_location_id"] = "home-private-1"
        with pytest.raises(ForbiddenObservationFieldError, match="home_location_id"):
            user_message(life_user_layers(leaking, {"identity": {"name": "Alice"}}))


def test_retired_submit_tool_is_absent_while_actions_and_activity_recall_remain() -> None:
    """退役提交工具不再开放，开始动作与活动回看保留，且全量工具不含 finish。"""
    functions = _functions(2, False)
    _, models = _life_turn_tools(2, False)
    assert "submit_work" not in functions and "finish" not in functions
    assert validate_single_tool_call(LoopToolCall(SIDE_TOOL, _WORK_CHOICE, "c1"), models) is None
    assert validate_single_tool_call(LoopToolCall("recall_task_summary", {"task_name": _WORK}, "c2"), models) is None


@pytest.mark.asyncio
async def test_usage_check_reuses_the_count_taken_before_sending(monkeypatch, caplog) -> None:
    """容量核对复用发送前计数结果，不重复计费同一请求。"""
    from worldmock.agents.prompt import prompt_capacity

    class UsageResponse(_ModelResponse):
        def model_dump(self, *args, **kwargs):
            return {**super().model_dump(*args, **kwargs),
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 1, "total_tokens": 1001}}

    counts = []
    monkeypatch.setattr(prompt_capacity, "token_counter", lambda **kw: (counts.append(kw), 1000)[1])
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        UsageResponse("call-memory-1", "search_memory", _SEARCH),
        UsageResponse("call-memory-2", "search_memory", _SEARCH),
        UsageResponse("call-memory-3", "search_memory", _SEARCH),
        UsageResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False)),
    ])
    sandbox = _SandboxStub()
    with caplog.at_level("INFO"):
        await LifeAgent(_person_7()).decide_wakeup(_wakeup(), sandbox, _binding(dispatcher, max_turns=4))
    assert len(counts) == len(dispatcher.requests) == 4
    assert caplog.text.count("estimated=1000 actual=1000 difference=0") == 4
