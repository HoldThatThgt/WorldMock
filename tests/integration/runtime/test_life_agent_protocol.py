"""LifeAgent 闭集 Schema、宿主身份注入与回合循环测试。

一次决策只有一个行动阶段（docs/design/06-agent.md 第二章「单一行动阶段」）：请求为原生 tools、系统层 system
与六层 user；生活决策没有 finish，世界请求（一个请求，或同一次回复里两个开始工具各一次的开始组）登记即结束本轮且
不追加 tool 消息，检索记忆是只读查询、不结束本轮；人物记忆
只经 conclude_activity 逐项的 memories 与 views 写入，来源由系统登记。
"""

from __future__ import annotations


from tests.data_paths import ENVIRONMENT_ROOT

import copy
import logging
import os
import json
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.state.decisions.round import DecisionContext, DirectToolCall, SchedulingState
from worldmock.agents.life_agent import (
    LifeAgent,
    _conclusion_memories,
    life_action_tool_names,
    life_tool_schema_sha256,
    person_inputs,
)
from worldmock.agents.memory.life_memory import MemoryStep, memory_update_evidence, save_step
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore, read_episodes
from worldmock.agents.protocol.life_feedback import FEEDBACK_INSTRUCTION
from worldmock.agents.protocol.react_loop import ReactDecision
from worldmock.agents.prompt.life_prompt import (
    LIFE_SYSTEM_LAYER,
    life_prompt_sha256,
    user_layer,
)
from worldmock.agents.prompt.prompt_schemas import USER_LAYER_NAMES
from worldmock.configuration.model.model_client import REQUEST_KEYS
from worldmock.runtime.framework.setup import stage_model_identity
from worldmock.runtime.lifecycle import run_host
from worldmock.runtime.lifecycle.run_host import RunHost
from worldmock.simulation_engine.activities.lifecycle.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING
from worldmock.simulation_engine.observation.memory.layout import assemble_layer
from worldmock.state.decisions.results import ActionReceipt
from worldmock.state.interaction.requests import (
    ConcludeActivityRequest,
    REQUEST_MODELS,
    ReportStatusRequest,
    StartMainActivityRequest,
    StartSideActivityRequest,
)
from worldmock.state.interaction.schema import deepseek_strict_schema
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS
from worldmock.agents.prompt.narration import ForbiddenObservationFieldError, render_layer_json
from tests.integration.runtime.current_contract_fixtures import inputs, turns_with_history
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, errand_add, errand_update, main_arguments, side_arguments


#: 生活决策开放的唯一记忆工具。
_MEMORY_TOOL_NAMES = {"search_memory"}
#: 一次合法的检索参数与替身环境给出的检索回答（没有 result 键，不是续接消息）。
_SEARCH = '{"category":"全部","time":"今天","keyword":null}'
_SEARCH_ARGS = {"category": "全部", "time": "今天", "keyword": None}
_SEARCH_ANSWER = {"类别": "全部", "时间": "今天", "关键词": None, "结果": [], "未列出": []}

#: 生活决策整次运行都开放的六个世界请求工具（两个开始工具、三个活动控制工具与计划修订）。
_BASE_ACTION_NAMES = set(REQUEST_MODELS) - {"recall_task_summary"}
_READ_QUERY_NAMES = _MEMORY_TOOL_NAMES | {"recall_task_summary"}

#: 真实注册表构造的观察合同冻结块（与引擎 observe 输出同一来源）。

_OBSERVED_AT = datetime(2026, 1, 5, tzinfo=timezone.utc)
_NOW_EPOCH = int(_OBSERVED_AT.timestamp())
_NOW_ISO = "2026-01-05T00:00:00+00:00"

#: 当前停留会话、活动与任务的可读名称。
_HOME_SESSION = "自己家（1月4日 18:00 起）"
_WAIT = "等待（1月4日 23:45 起）"
_WORK = "整理会议纪要（1月4日 21:00 起）"
_NEWS = "接着看晚间新闻（1月4日 20:00 起）"
_WORK_ACTION = "整理会议纪要"

#: 私有绑定种类 → 工具层目标种类（docs/design/06-agent.md 第一章「工具层」）。
_TARGET_KINDS = {"app": "app", "supply": "product", "location": "location"}
_TARGET_TOOLS = {"activity": ("report_status", "pause_activity", "conclude_activity")}


def _offer(action: str, note: str | None = None, detail: str | None = None, **private) -> dict:
    """私有绑定的一条动作行：动作、调用里 detail 要写的名称与当前不可用原因，与真实引擎观察同形。

    private 是不进提示的私有键：出行行的 travel_method_id，地点 interact 行的 content_ids 与 episode_titles。
    """
    return {"action": action, "detail": detail, "note": note, **private}


def _unit(name: str, *content_ids: str, note: str | None = None, episode_titles: tuple[str, ...] = ()) -> dict:
    """当前地点的一个可点名内容单元在私有绑定里的动作行。"""
    return _offer("interact", note, name, content_ids=list(content_ids), episode_titles=list(episode_titles))


def _tool_entries(kind: str) -> list[dict]:
    """工具层目标的 tools[]：以目标名称为参数的其他工具，均可用。"""
    return [{"tool": tool, "available": True, "reason": None} for tool in _TARGET_TOOLS.get(kind, ())]


def _visible_object(name: str, kind: str, object_id: str, *offers: dict, **binding) -> tuple[dict, dict]:
    """同一可见对象的工具层目标与私有绑定（docs/design/06-agent.md 第一章「工具层」）。

    工具层目标只有可读名称、种类、状态与动作；私有绑定另带绑定种类、内部身份与 binding 附加项。
    """
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


#: 人物停留的家可点名的那件事。
_REST = "歇一会儿"
#: 当前停留的家恒在工具层（一个可点名的单元与睡觉）；在家时办公室工作照常列出，标不可用并写原因。
_SELF_OBJECT = _visible_object("自己家", "location", "home",
                               _unit(_REST, "rest-visible"), _offer("sleep"))
_WORK_OBJECT = _visible_object(_WORK_ACTION,"app","work-visible",
    _offer("interact", "整理会议纪要需要在办公室"))
#: 在家点名歇一会儿：start_main_activity 的完整参数（工具名为 MAIN_TOOL）。
_WAIT_CHOICE = main_arguments("interact", "自己家", _REST)
#: 打开工作 app：start_side_activity 的完整参数（工具名为 SIDE_TOOL）。
_WORK_CHOICE = side_arguments("interact", _WORK_ACTION)
#: 五项体感分级（取值就是 felt 的分级文字）。
_BODY = {"appetite": "不饿", "sleepiness": "有点乏", "relaxation": "不闷", "social": "不想找人"}


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """测试使用固定供应商窗口，只验证请求和执行合同。"""
    from worldmock.agents.prompt import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _context() -> DecisionContext:
    """构造稳定测试决策上下文。"""
    return DecisionContext(
        "run", "round", "decision", 7, _OBSERVED_AT, 3
    )


def _memory(text: str = "我歇了一会儿，心里踏实了些。", *, kind: str = "routine", importance: float = 0.5,
            keywords: tuple[str, ...] = ("休息",)) -> dict:
    """一条普通收尾记忆 {type, text, importance, keywords}（没有对象字段，看法与偏好另写在 views）；来源与认知状态由系统
    填写，模型不填任何身份。"""
    return {"type": kind, "text": text, "importance": importance, "keywords": list(keywords)}


def _view(about: str, text: str = "和同事处得不错。", *, kind: str = "relationship", importance: float = 0.5,
          keywords: tuple[str, ...] = ("同事",)) -> dict:
    """一条看法（relationship，对人）或偏好（preference，对事物）{type, about, text, importance, keywords}。"""
    return {"type": kind, "about": about, "text": text, "importance": importance, "keywords": list(keywords)}


def _conclude_args(*names: str, memories: list[list[dict]] | None = None,
                   views: list[list[dict]] | None = None) -> dict:
    """conclude_activity 的参数：一项一个对象 {activity_name, summary, memories, views}（缺省每项各写一条普通记忆、没有
    看法）；请求级 errands 为空列表。"""
    groups = memories if memories is not None else [[_memory()] for _ in names]
    seen = views if views is not None else [[] for _ in names]
    return {"items": [{"activity_name": name, "summary": "先收个尾。", "memories": group, "views": opinions}
                      for name, group, opinions in zip(names, groups, seen, strict=True)], "errands": []}


def _control_args(tool: str, *names: str, summary: str = "写完提纲") -> dict:
    """三个控制工具的参数：一项一个对象；结束工具每项另带空的 memories 与 views；请求级 errands 为空列表。"""
    extra = {"memories": [], "views": []} if tool == "conclude_activity" else {}
    return {"items": [{"activity_name": name, "summary": summary, **extra} for name in names], "errands": []}


def _stored_memories(args: dict) -> list[dict]:
    """宿主把 conclude_activity 逐项的记忆与看法按项展平成的本步记忆：control_items 读出的存储形状原样（看法带 subject），
    来源与认知状态由记忆存储写定。"""
    return _conclusion_memories("conclude_activity", args)


def _layers() -> dict:
    """环境构建的五层与看法偏好：在家停留，暂无活动与计划，本段停留尚无记录。"""
    return {
        "tool_layer": {"layer": "工具层", "targets": copy.deepcopy([_SELF_OBJECT[0], _WORK_OBJECT[0]]),
                       "callable_index": {"navigation_apps":[],"payment_apps":[],"goods":[],"prefer_apps":[]}},
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
    """构造 docs/design/06-agent.md 第七章形状的结构化观察记录：人物在当前停留会话里，暂无活动；私有事务绑定
    里没有未完成事务，商品名只有手边的现有食品（docs/design/04-simulation-engine.md ）。"""
    data = {
        "observation_event_id": "observation-current",
        "current_time": _NOW_EPOCH,
        "wake_reasons": [],  # 唤醒原因只进私有证据，不改变工具面
        "same_moment_stage": "open",
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
        "actions":[{"action_id":"work-visible","name":_WORK_ACTION,"category":"work_study",
                    "manner":None,"app_ids":[],"communication_mode":None}],
        "apps": [], "contents": [], "app_pushes": {},
        "activity_summary": {"exercise":44,"goods_received":1},
        "messages": [{"message_id": "message-visible"}],
        "goods": [{"goods_id": "product-visible", "name": "现有食品", "category": "snack",
                   "price_fen": 500, "store_ids": ["store"]}],
        "locations": [{"location_id": "home", "name": "自己家", "kind":"home"}, {"location_id": "office", "name": "办公室", "kind":"office"}],
        "location_id": "home", "home_location_id":"home",
        "routes": [{"destination_id": "office", "status": "reachable", "mode": "driving",
                    "duration_seconds": 216}],
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def _activity_target(name: str, phase: str) -> dict:
    """进行中或刚结束活动的工具层目标：没有动作，控制工具以活动名为参数。"""
    return {"name": name, "kind": "activity", "phase": "进行中" if phase == PHASE_ACTIVE else "刚结束",
            "state": "已做约 15 分钟", "actions": [], "tools": _tool_entries("activity")}


def _with_activities(data: dict, *activities: tuple[str, str], elsewhere: tuple[str, ...] = ()) -> dict:
    """在当前停留会话下加入活动（名称, 阶段说法），同步写入工具层或待办层、私有活动绑定与活动证据。

    进行中与刚结束的活动是工具层的活动目标；暂停的活动只在待办层，elsewhere 列出开始地点在别处的暂停活动
    （其恢复标不可用并写明地点）。
    """
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
                "name": name, "paused_at": "2026-01-04T23:50:00+00:00", "place": "办公室" if name in elsewhere
                else "自己家", "progress": "已做约 15 分钟",
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
    """人物步骤的观察：当前停留里有一项进行中的等待，人物可以带记忆结束它。"""
    return _with_activities(_observations()[0]["data"], (_WAIT, PHASE_ACTIVE))


def _functions(agent: LifeAgent) -> dict[str, dict]:
    """本次请求实际开放的原生工具（名称 → function 定义）。"""
    return {item["function"]["name"]: item["function"] for item in agent.turn_tools(2, False)}


def _prepared(observation: dict | None = None) -> LifeAgent:
    """按一次冻结观察准备好的生活人物：上下文已记下，六层、私有名称与预算已准备，工具面按非最后回合开放。"""
    agent = _wire_agent(_Environment(), None, 16)
    agent._context = _context()
    agent._prepare(observation or _observations()[0]["data"])
    agent.turn_tools(2, False)
    return agent


def test_life_agent_schema_is_closed_and_host_fields_are_hidden() -> None:
    """正式决策只开放当前人物工具与检索记忆，隐藏宿主字段；两个开始工具排在最前，参数里只有 action 的固定闭集，
    对象名与 detail 没有逐轮名称枚举。"""
    functions = _functions(_prepared())

    assert set(functions) == _BASE_ACTION_NAMES | _READ_QUERY_NAMES
    action_names = [name for name in REQUEST_MODELS if name != "recall_task_summary"]
    assert list(functions)[:len(action_names)] == action_names
    assert action_names[:2] == [MAIN_TOOL, SIDE_TOOL] and "start_activity" not in functions
    assert "finish" not in functions and not {"memory_step_range", "memory_tick_range"} & set(functions)
    assert all(function["strict"] is True for function in functions.values())
    closed = copy.deepcopy({name: functions[name] for name in (MAIN_TOOL, SIDE_TOOL, "report_status")})
    for tool, fields, actions in ((MAIN_TOOL, ["action", "target", "detail"], ["travel", "interact", "sleep"]),
                                  (SIDE_TOOL, ["action", "target", "prefer"], ["purchase", "interact", "query"])):
        parameters = closed[tool]["parameters"]
        assert {"agent_id", "decision_id", "action_id"}.isdisjoint(parameters["properties"])
        assert list(parameters["properties"]) == parameters["required"] == fields
        assert parameters["additionalProperties"] is False
        assert parameters["properties"].pop("action")["enum"] == actions
    change = closed["report_status"]["parameters"]["properties"]["errands"]["items"]["properties"]
    assert change.pop("change")["enum"] == ["新增", "更新", "取消"]
    assert "outcome" not in change
    assert '"enum"' not in json.dumps(closed)
    assert len(life_prompt_sha256()) == 64
    assert life_tool_schema_sha256() == life_tool_schema_sha256()




def test_session_names_are_not_recall_names() -> None:
    """回看只认已绑定的目标、活动与笔记名称，停留会话名与目录动作名都不可回看。"""
    observations = _observations()
    agent = _prepared(observations[0]["data"])

    def recall(name: str) -> ReactDecision:
        """按名称回看的一次决策。"""
        return ReactDecision("recall_task_summary", {"task_name": name}, "")

    assert agent._validate([recall(_WORK_ACTION)]).text == (
        f"NAME_NOT_FOUND: The name {_WORK_ACTION} is not known.")
    _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE))
    agent = _prepared(observations[0]["data"])
    assert agent._validate([recall(_WORK)]) is None
    assert agent._validate([recall(_HOME_SESSION)]).text == (
        f"NAME_NOT_FOUND: The name {_HOME_SESSION} is not known.")






def test_plan_day_is_no_person_tool_and_revision_carries_remind_time() -> None:
    """当日计划由零点后台任务生成，人物工具面没有 plan_day；修订工具整次运行都在，条目必须给出可为 null 的 remind_time
    与 around_time（docs/design/04-simulation-engine.md ）。"""
    assert len(REQUEST_MODELS) == 7 and "plan_day" not in REQUEST_MODELS
    assert {MAIN_TOOL, SIDE_TOOL} <= set(REQUEST_MODELS)
    assert "start_activity" not in REQUEST_MODELS
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)
    waking = _observations()
    waking[0]["data"]["wake_reasons"] = ["run_start", "wake_from_sleep"]
    planned = _observations()
    planned[0]["data"]["todo_layer"]["day_plan"] = {"date": "2026-01-05", "goals": []}
    without_plan, with_plan = _functions(_prepared(waking[0]["data"])), _functions(_prepared(planned[0]["data"]))
    assert without_plan == with_plan
    assert "plan_day" not in with_plan and "revise_day_plan" in with_plan
    tool = with_plan["revise_day_plan"]
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
    """起居规则只在系统层 rules 写一次：生活决策的每个请求都带同一份系统层；「怎样过一天」已删。
    同时进行的两个槽位只在讲槽位的一条（rules[11]）说一次，醒着时何时再请决策只在讲决策怎样结束的一条（rules[4]）说一次，睡着后何时再请决策只在睡眠那一条说一次（docs/design/04-simulation-engine.md）。"""
    action_system = _prepared()._prepare(_observations()[0]["data"])[1][0]["content"]

    assert action_system == render_layer_json(LIFE_SYSTEM_LAYER)
    rules = json.loads(action_system)["rules"]
    for sentence in ("同时最多两项活动", "睡着后到你写的时长才会再请你决策", "商品实际收到时自动吃掉或用掉",
                     "都只是你的想法和回忆，不能改变身体、资金、内容进度或订单结果", "工作安排由环境给出",
                     "没有进行中的活动时时间不会往前走", "打开后放完一条自动接下一条", "环境也不按它们限制你此刻能做什么",
                     "系列的 note 或进行中活动的 state", "今日计划只是自己的打算", "available 为 true 的动作单独提交都会受理",
                     "个人事务只记要买到或取到的商品"):
        assert sum(sentence in rule for rule in rules) == 1
    assert "同时最多两项活动，槽位 1 和槽位 2 各放一项" in rules[11]
    assert "醒着时，手上的事做完" in rules[4]
    assert all("名额" not in rule and "睡够了" not in rule for rule in rules)
    assert sum("不保存库存" in rule for rule in rules) == 1
    assert "怎样过一天" not in action_system and "你不是AI助手" not in action_system
    for banned in ("评审", "评分", "引擎期望", "budget", "based_on_action_id", "supply_id", "正确答案", "环境期望"):
        assert banned not in action_system.replace("店铺评分", "")


def test_location_norm_is_removed_from_static_tool_and_system_text() -> None:
    """地点行为改由菜单声明，静态工具描述和系统层均不保留办公室散文。"""
    agent = _prepared()
    schemas = {name: function["description"] for name, function in _functions(agent).items()}
    system_prompt = agent._prepare(_observations()[0]["data"])[1][0]

    for tool in (MAIN_TOOL, SIDE_TOOL):
        assert "at the office, prefer brief phone-friendly content" not in schemas[tool]
        assert "save movies and long drama episodes" not in schemas[tool]
        assert "office" not in schemas[tool] and "办公室" not in schemas[tool]
    flattened = " ".join(system_prompt["content"].split())
    assert "watching long entertainment openly at the office" not in flattened
    assert "inappropriate" not in flattened


def test_feedback_boundary_exposes_readable_activity_controls() -> None:
    """三个控制工具整次运行都在，参数只列可读活动名字段；名称按私有活动绑定核对，不查阶段与可用性。"""
    observations = _observations()
    _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE), (_NEWS, PHASE_PAUSED))
    agent = _prepared(observations[0]["data"])
    functions = _functions(agent)

    for name in ("report_status", "pause_activity", "conclude_activity"):
        fields = functions[name]["parameters"]["properties"]
        assert set(fields) == {"items", "errands"}
        item = fields["items"]["items"]["properties"]
        with_memories = name == "conclude_activity"
        assert set(item) == ({"activity_name", "summary", "memories", "views"} if with_memories
                             else {"activity_name", "summary"})
        assert "enum" not in item["activity_name"]
        for activity in (_WORK, _NEWS):
            arguments = _control_args(name, activity)
            assert agent._validate([ReactDecision(name, arguments, "")]) is None
        unknown = _control_args(name, "虚构活动")
        assert agent._validate([ReactDecision(name, unknown, "")]).text == (
            "NAME_NOT_FOUND: The name 虚构活动 is not known.")
        repeated = _control_args(name, _WORK, _WORK)
        assert "activity names must be distinct" in agent._validate(
            [ReactDecision(name, repeated, "")]).text
    assert "activity-private-1" not in json.dumps(functions, ensure_ascii=False)


#: 批次不合法时宿主回给模型的原因与改法（三种合法批次：一个世界请求、两个开始工具各一次、只有只读查询）。
_BATCH_REASON = ("Submit one world request, start_main_activity together with start_side_activity, "
                 "or a batch containing only read queries.")
_BATCH_FIX = "Submit one world request, the two start tools together, or only read queries."


def test_life_agent_rejects_invalid_or_non_atomic_decisions() -> None:
    """参数错误、不合法的批次与生活决策中的 finish 都在执行前失败（生活决策没有 finish）。

    合法的批次只有三种：全部是只读查询、恰好一个世界请求、两个开始工具各一次（先后不限）。同一个开始工具写两次、
    开始工具与控制工具同批、世界请求与只读查询同批、三个调用的批次都整批拒绝，明细定位在 tool_calls。闭集之外的
    action 是请求模型错误并带回全部合法值；已知对象不提供所写动作是 ACTION_NOT_OFFERED；对象名找不到是 NAME_NOT_FOUND。
    """
    agent = _prepared()
    valid = ReactDecision(MAIN_TOOL, _WAIT_CHOICE, "")
    side = ReactDecision(SIDE_TOOL, _WORK_CHOICE, "")
    memory = ReactDecision("search_memory", _SEARCH_ARGS, "")
    revise = ReactDecision("revise_day_plan", {"goals": []}, "")

    for legal in ([valid], [side], [valid, side], [side, valid], [memory], [memory, memory], [revise]):
        assert agent._validate(legal) is None
    for batch in ([valid, valid], [side, side], [memory, valid], [valid, memory], [valid, revise], [revise, side],
                  [valid, side, memory], [valid, side, valid], [revise, revise]):
        report = agent._validate(batch).report
        assert report.error_code == "TOOL_CALL_REQUIRED" and report.reason == _BATCH_REASON
        assert report.fix == _BATCH_FIX
        assert (report.items[0].path, report.items[0].written) == (("tool_calls",), [item.action for item in batch])
    invalid = ReactDecision(MAIN_TOOL, main_arguments("invented_action", "自己家", _REST), "")
    detail = agent._validate([invalid]).report.payload()["items"][0]
    assert (detail["error_code"], detail["written"]) == ("ARGUMENTS_INVALID", "invented_action")
    assert detail["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert detail["allowed_values"] == ["travel", "interact", "sleep"]
    unsupported = ReactDecision(MAIN_TOOL, main_arguments("travel", "自己家", "步行"), "")
    detail = agent._validate([unsupported]).report.payload()["items"][0]
    assert (detail["error_code"], detail["written"]) == ("ACTION_NOT_OFFERED", "travel")
    assert detail["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert detail["allowed_values"] == ["interact", "sleep"]
    assert detail["candidates"] == [{"tool": MAIN_TOOL, "arguments": _WAIT_CHOICE, "note": None}]
    unlisted = ReactDecision(MAIN_TOOL, main_arguments("interact", "虚构对象", _REST), "")
    report = agent._validate([unlisted]).report
    assert report.error_code == "NAME_NOT_FOUND" and report.items[0].path[-1] == "target"
    finish = ReactDecision("finish", {"memories": []}, "")
    assert agent._validate([finish]).report.items[0].path[-1] == "name"


def test_react_messages_are_system_layer_and_six_layer_user() -> None:
    """消息为系统层 system 与六层 user；user 按固定层序由观察各层与宿主人设层渲染，同一观察只写一次。"""
    agent = _wire_agent(_Environment(), None, 16)
    agent._context = _context()
    observations = _observations("result-prior")

    messages = agent._prepare(observations[0]["data"])[1]
    layers = json.loads(messages[1]["content"])

    assert [item["role"] for item in messages] == ["system", "user"]
    assert [layer["layer"] for layer in layers] == list(USER_LAYER_NAMES)
    data = observations[0]["data"]
    for name, key in (("人设层", None), ("记忆层", "memory_layer"), ("待办层", "todo_layer"), ("工具层", "tool_layer"),
                      ("环境层", "environment_layer"), ("进度层", "progress_layer")):
        layer = user_layer(layers, name)
        if key == "tool_layer":
            assert "groups" in layer and "targets" not in layer
            assert layer["callable_index"] == data[key]["callable_index"]
        elif key is not None:
            assert layer == data[key]
    assert user_layer(layers, "人设层")["identity"] == {"name": "Alice", "age": 32, "occupation": "产品经理",
                                                         "living_arrangement": "与伴侣合租两居室"}
    user = messages[1]["content"]
    assert user == render_layer_json(layers)
    for leaked in ("observation-current", "result-prior", "game-visible", "session-7-0001", "task-visible",
                   "在上海张江做工程师"):
        assert leaked not in user


def test_blacklisted_observation_fields_never_enter_messages() -> None:
    """黑名单字段（动机内部仪表、完整目录、事件身份、私有活动与会话绑定）不进入任何消息；层中混入禁键即失败。"""
    observations = _observations()
    data = _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE), (_WAIT, PHASE_PENDING))
    data["motivation_internal"] = {"candidates": [{"kind": "eat", "score": 0.9}]}
    data["world_state_version"] = 42
    data["end_time"] = _NOW_EPOCH + 86_400
    agent = _prepared(data)

    messages = agent._prepare(data)[1]
    text = "\n".join(item["content"] for item in messages)

    for banned in ("motivation_internal", "motivation_hints", "world_state_version",
                   "end_time", "affordance_contract", "interaction_bindings",
                   "observation_event_id", "game_catalog", "activity_bindings", "session_bindings",
                   "pending_conclusions", "activity-private-1", "session-7-0001", "progress_meta"):
        assert banned not in text
    assert _WAIT in text and "刚结束" in text
    leaking = _observations()
    leaking[0]["data"]["tool_layer"]["targets"][0]["activity_id"] = "activity-private-9"
    with pytest.raises(ForbiddenObservationFieldError, match="activity_id"):
        _prepared(leaking[0]["data"])


class _ModelResponse:
    """提供原生工具属性及可持久化响应；content 是回复正文（缺省为空）。"""

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
        """返回最小 JSON 模型响应（reasoning_content 恒在，缺省为 None）。"""
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


def _assert_action_wire(request: dict) -> None:
    """核对行动请求的生成参数：思考开启、tool_choice=auto、16000 上限、恰为六个底稿键、不发 reasoning_effort、全部严格。"""
    assert request["tool_choice"] == "auto"
    assert request["max_tokens"] == 16000
    assert sorted(request) == sorted(REQUEST_KEYS)
    assert "reasoning_effort" not in request and "temperature" not in request
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert all(item["function"]["strict"] is True for item in request["tools"])


class _Dispatcher:
    """按顺序返回登记前的一次检索记忆与唯一的生活动作；世界请求登记即结束本轮。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            _ModelResponse(
                "call-memory", "search_memory", _SEARCH
            ),
            _ModelResponse(
                "call-action",
                MAIN_TOOL,
                json.dumps(_WAIT_CHOICE, ensure_ascii=False),
                reasoning_content="Tired person needs a short rest before work.",
                content="  先歇一分钟。  ",
            ),
        ]

    async def call(self, request):
        """返回下一个原生工具响应。"""
        self.requests.append(request)
        _assert_action_wire(request)
        return self.responses.pop(0)


class _Environment:
    """捕获 LifeAgent 注入的直接调用、只读查询和模型证据。"""

    def __init__(self) -> None:
        self.calls: list[DirectToolCall] = []
        self.queries: list[tuple[str, object]] = []
        self.evidence = []
        self.memory_updates = []

    def read_query(self, context: DecisionContext, tool: str, request) -> dict:
        """记录一次只读查询并返回固定的检索回答。"""
        self.queries.append((tool, request))
        return dict(_SEARCH_ANSWER)

    def submit_action(self, call: DirectToolCall) -> ActionReceipt:
        """返回与宿主身份一致的提交收据。"""
        self.calls.append(call)
        return ActionReceipt(
            action_id=call.action_id,
            round_id=call.context.round_id,
            decision_id=call.context.decision_id,
            request_sha256="a" * 64,
        )

    def record_model_exchange(self, exchange) -> None:
        """保存模型请求与响应证据。"""
        self.evidence.append(exchange)

    def record_memory_update(self, evidence) -> None:
        """保存人物记忆文件内容身份。"""
        self.memory_updates.append(evidence)


class _StepDispatcher:
    """返回一次带记忆结束等待的 conclude_activity（登记即结束本轮，没有 finish）。"""

    model_name = "deepseek-flash"

    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            _ModelResponse("call-action", "conclude_activity",
                           json.dumps(_conclude_args(_WAIT), ensure_ascii=False)),
        ]

    async def call(self, request):
        """返回下一个严格工具响应。"""
        self.requests.append(request)
        return self.responses.pop(0)


class _GroundedCorrectionDispatcher:
    """复现检索记忆后捏造旧游戏工具、再改用当前目标工具的响应序列（世界请求登记即结束本轮）。"""

    model_name = "deepseek-flash"

    def __init__(self, responses: list[_ModelResponse] | None = None) -> None:
        self.requests = []
        defaults = [
            _ModelResponse("call-memory", "search_memory", _SEARCH),
            _ModelResponse(
                "call-invalid",
                "play_game",
                '{"game_id":"game-visible","planned_duration_seconds":600}',
            ),
            _ModelResponse(
                "call-action",
                MAIN_TOOL,
                json.dumps(_WAIT_CHOICE, ensure_ascii=False),
            ),
        ]
        self.responses = list(responses) if responses is not None else defaults

    async def call(self, request):
        """记录真实 wire 并返回下一个协议响应。"""
        self.requests.append(request)
        return self.responses.pop(0)


class _StepEnvironment(_Environment):
    """提供实际人物步骤与运行宿主故障边界所需的引擎合同：宿主读取的 context.state、decisions.round 与记录型方法。"""

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
        self.context = SimpleNamespace(
            state=SimpleNamespace(run_id="run", current_time=_NOW_EPOCH, end_time=_NOW_EPOCH + 3600),
            config=SimpleNamespace(timezone="UTC"))
        self.decisions = SimpleNamespace(round=None)

    def observe(self, context: DecisionContext) -> dict:
        """返回带稳定来源事件身份的当前人物观察（一项进行中的等待）。"""
        assert context.agent_id == 7
        self.observed_contexts.append(context)
        return _step_observation()

    def scheduling_state(self) -> SchedulingState:
        """返回当前共同决策边界。"""
        return SchedulingState(
            self.current_time,
            self.ready,
            self.current_time + timedelta(seconds=60),
            self.version,
        )

    def begin_decision_round(self, contexts) -> None:
        """记录宿主冻结的决策顺序并打开本轮。"""
        self.rounds.append(contexts)
        self.decisions.round = contexts[0].round_id

    def commit_decision_round(self, round_id: str) -> list[dict]:
        """记录不应在记忆保存失败后发生的结算，并关闭本轮。"""
        self.commits.append(round_id)
        self.decisions.round = None
        return [{"agent_id": 7, "status": "completed"}]

    def abort_decision_round(self, round_id: str, error: str) -> None:
        """保存失败轮身份与错误证据，并关闭本轮。"""
        self.aborts.append((round_id, error))
        self.decisions.round = None

    def fail_run(self, error: str) -> None:
        """保存宿主登记的运行失败证据。"""
        self.failures.append(error)

    def checkpoint(self, snapshots) -> None:
        """记录不应在失败轮后创建的共同检查点。"""
        self.checkpoints.append(snapshots)


class _Recorder:
    """记下每次调用的参数并返回固定结果的同步替身。"""

    def __init__(self, result) -> None:
        self.result, self.calls = result, []

    def __call__(self, *arguments):
        """记下参数，返回固定结果。"""
        self.calls.append(arguments)
        return self.result


class _AsyncRecorder(_Recorder):
    """记下每次调用的参数并返回固定结果的异步替身。"""

    async def __call__(self, *arguments):
        """记下参数，返回固定结果。"""
        self.calls.append(arguments)
        return self.result


def _wire_agent(environment, dispatcher, max_turns: int, memory: WorldMockMemoryStore | None = None) -> LifeAgent:
    """按协议测试需要装配真实 LifeAgent：冻结输入取自夹具人设表与模型身份，换成编号 7、名字 Alice 与给定的回合上限。"""
    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    person = replace(person_inputs(frozen.persona, identity), agent_id=7, name="Alice",
                     model_identity={**identity, "max_react_turns": max_turns})
    return LifeAgent(person, environment, dispatcher, memory)


def _step_host(monkeypatch, run_dir: Path, environment: _StepEnvironment, dispatcher, max_turns: int = 4) -> RunHost:
    """运行宿主驱动记录型假环境：人物 7 的工作区是 run_dir/agents/agent_0007，记忆存储由宿主建；假环境没有足迹库，
    宿主的快照与两个零点任务换成记录替身。"""
    monkeypatch.setattr(run_host, "snapshot_workspaces", _Recorder({}))
    monkeypatch.setattr(run_host, "settle_pending_memory_digests", _AsyncRecorder([]))
    monkeypatch.setattr(run_host, "settle_pending_day_plans", _AsyncRecorder([]))
    host = RunHost(run_dir, inputs(), dispatcher, {7: _wire_agent(environment, dispatcher, max_turns).inputs}, {7: ()})
    host.engine = environment
    return host


def _react_tool_names(requests: list[dict]) -> list[set[str]]:
    """逐个模型请求读取实际开放的工具名。"""
    return [{item["function"]["name"] for item in request["tools"]} for request in requests]


@pytest.mark.asyncio
async def test_native_react_submits_one_host_bound_life_action() -> None:
    """登记前的一次检索记忆与唯一动作共两次请求；检索结果只序列化一次，世界请求登记后不追加 tool 消息。"""
    environment = _Environment()
    dispatcher = _Dispatcher()
    agent = _wire_agent(environment, dispatcher, 4)

    registered, history = await turns_with_history(agent, _context(), _observations("result-prior")[0]["data"])

    assert [decision.action for decision in registered] == [MAIN_TOOL]
    assert len(environment.calls) == 1
    call = environment.calls[0]
    assert call.context == _context()
    assert call.action_id == "decision:action:0001"
    assert call.tool_name == MAIN_TOOL and isinstance(call.request, StartMainActivityRequest)
    assert call.request.model_dump(mode="json") == _WAIT_CHOICE
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 2
    assert len(dispatcher.requests) == 2
    first, second = dispatcher.requests
    assert [item["role"] for item in first["messages"]] == ["system", "user"]
    assert [item["role"] for item in second["messages"]] == ["system", "user", "assistant", "tool"]
    assert first["messages"][0] == second["messages"][0] == {"role": "system",
                                                            "content": render_layer_json(LIFE_SYSTEM_LAYER)}
    memory = second["messages"][-1]
    assert memory["tool_call_id"] == "call-memory"
    assert memory["content"] == json.dumps(_SEARCH_ANSWER, ensure_ascii=False, separators=(",", ":"))
    assert [tool for tool, _ in environment.queries] == ["search_memory"]
    assert second["messages"][1] == first["messages"][1]
    assert json.dumps(first["tools"]) == json.dumps(second["tools"])
    assert _react_tool_names([first, second]) == [_BASE_ACTION_NAMES | _READ_QUERY_NAMES] * 2
    assert all("Authoritative" not in item["messages"][0]["content"] for item in (first, second))
    assert history[-1]["role"] == "assistant"
    assert history[-1]["tool_calls"][0]["id"] == "call-action"
    assert agent._memories == []


@pytest.mark.asyncio
async def test_reasoning_content_is_persisted_in_model_response_evidence() -> None:
    """思考开启后模型推理与回复正文随现有序列化进入 model_response 足迹证据。"""
    environment = _Environment()
    dispatcher = _Dispatcher()
    agent = _wire_agent(environment, dispatcher, 4)

    await agent.run_turns(_context(), _observations("result-prior")[0]["data"])

    requests = [item for item in environment.evidence if item.phase == "request"]
    responses = [item for item in environment.evidence if item.phase == "response"]
    for item in requests:
        _assert_action_wire(item.request)
    # 反例：不带推理的检索记忆响应序列化后 reasoning_content 为 None，不伪造内容。
    memory_message = responses[0].response["choices"][0]["message"]
    assert memory_message["tool_calls"][0]["function"]["name"] == "search_memory"
    assert memory_message["reasoning_content"] is None
    # 正例：带 reasoning_content 的动作响应按原样进入足迹事件，回复正文一并保留。
    action_message = responses[1].response["choices"][0]["message"]
    assert action_message["reasoning_content"] == (
        "Tired person needs a short rest before work."
    )
    assert action_message["content"] == "  先歇一分钟。  "
    assert action_message["tool_calls"][0]["function"]["name"] == MAIN_TOOL


@pytest.mark.asyncio
async def test_budgeted_memory_search_preserves_the_action_turn() -> None:
    """检索记忆在只剩世界请求回合时撤下，保留一个自主生活动作回合；登记即结束本轮。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "search_memory", _SEARCH),
        _ModelResponse("call-memory-2", "search_memory", _SEARCH),
        _ModelResponse("call-memory-3", "search_memory", _SEARCH),
        _ModelResponse(
            "call-action",
            MAIN_TOOL,
            json.dumps(_WAIT_CHOICE, ensure_ascii=False),
        ),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 4)

    result = await agent.run_turns(_context(), _observations()[0]["data"])

    assert [decision.action for decision in result] == [MAIN_TOOL]
    assert len(environment.calls) == 1
    assert _react_tool_names(dispatcher.requests) == [
        _BASE_ACTION_NAMES | _READ_QUERY_NAMES,
        _BASE_ACTION_NAMES | _READ_QUERY_NAMES,
        _BASE_ACTION_NAMES | _READ_QUERY_NAMES,
        _BASE_ACTION_NAMES,
    ]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002", "0003", "0004"]


@pytest.mark.asyncio
async def test_multiple_memory_calls_share_one_model_turn() -> None:
    """同一响应的多个检索调用只消耗一个模型回合，各得一条 tool 消息，余下的回合仍够登记世界请求。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _MultipleModelResponse([
            ("call-memory-1", "search_memory", _SEARCH),
            ("call-memory-2", "search_memory", _SEARCH),
        ]),
        _ModelResponse(
            "call-action",
            MAIN_TOOL,
            json.dumps(_WAIT_CHOICE, ensure_ascii=False),
        ),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 2)

    result = await agent.run_turns(_context(), _observations()[0]["data"])

    assert [decision.action for decision in result] == [MAIN_TOOL]
    assert len(environment.calls) == 1
    assert [tool for tool, _ in environment.queries] == ["search_memory", "search_memory"]
    assert _react_tool_names(dispatcher.requests) == [_BASE_ACTION_NAMES | _READ_QUERY_NAMES, _BASE_ACTION_NAMES]
    assert [item["tool_call_id"] for item in dispatcher.requests[1]["messages"][3:]] == [
        "call-memory-1", "call-memory-2"]
    assert [item.call_id.rsplit(":model:", 1)[1].split(":", 1)[0]
            for item in environment.evidence[::2]] == ["0001", "0002"]


@pytest.mark.asyncio
async def test_reserved_action_boundary_rejects_memory_without_extra_request() -> None:
    """只剩世界请求回合时非法检索调用零执行；回合用完仍没有世界请求即本次决策失败。"""
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-memory-1", "search_memory", _SEARCH),
        _ModelResponse("call-memory-2", "search_memory", _SEARCH),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 2)

    with pytest.raises(RuntimeError, match="ReAct decision budget exhausted"):
        await agent.run_turns(_context(), _observations()[0]["data"])

    assert len(dispatcher.requests) == 2
    assert {item["function"]["name"] for item in dispatcher.requests[1]["tools"]} == _BASE_ACTION_NAMES
    assert environment.calls == []
    assert len(environment.queries) == 1
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 2
    assert environment.evidence[-1].response["protocol_feedback"]["items"][0]["written"] == "search_memory"


@pytest.mark.asyncio
async def test_second_world_request_in_one_decision_is_refused() -> None:
    """世界请求登记即结束本次决策：前一次回复已经登记了一个世界请求之后，同一决策里再来的任何批次（只读查询、另一个
    开始工具、控制请求、两个开始工具组成的开始组）都在校验处被拒，再派发任何工具在分派处被拒；开始组只能出自同一次回复。"""
    agent = _prepared()
    environment = agent.engine

    receipt = agent._dispatch(MAIN_TOOL, _WAIT_CHOICE)

    assert receipt.ok is True
    query = _SEARCH_ARGS
    main = ReactDecision(MAIN_TOOL, _WAIT_CHOICE, "")
    side = ReactDecision(SIDE_TOOL, _WORK_CHOICE, "")
    for later in ([ReactDecision("search_memory", query, "")], [side], [main, side],
                  [ReactDecision("revise_day_plan", {"goals": []}, "")]):
        assert agent._validate(later).text == f"TOOL_CALL_REQUIRED: {_BATCH_REASON}"
    with pytest.raises(RuntimeError, match="already submitted"):
        agent._dispatch("search_memory", query)
    with pytest.raises(RuntimeError, match="already submitted"):
        agent._dispatch(SIDE_TOOL, _WORK_CHOICE)
    assert [call.action_id for call in environment.calls] == ["decision:action:0001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [(MAIN_TOOL, SIDE_TOOL), (SIDE_TOOL, MAIN_TOOL)])
async def test_start_group_registers_both_start_requests_in_reply_order(order: tuple[str, str]) -> None:
    """同一次回复里两个开始工具各调用一次是一个开始组（先后不限）：两条世界请求按回复中的先后依次登记，动作身份的序号
    是它在本回复世界请求中的位置（从 1 起）；两条都登记后本次决策结束，不追加 tool 消息、不再请求模型，之后不再派发工具。"""
    arguments = {MAIN_TOOL: _WAIT_CHOICE, SIDE_TOOL: _WORK_CHOICE}
    models = {MAIN_TOOL: StartMainActivityRequest, SIDE_TOOL: StartSideActivityRequest}
    dispatcher = _GroundedCorrectionDispatcher(responses=[_MultipleModelResponse([
        (f"call-{tool}", tool, json.dumps(arguments[tool], ensure_ascii=False)) for tool in order])])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 4)

    registered, history = await turns_with_history(agent, _context(), _observations()[0]["data"])

    assert [decision.action for decision in registered] == [order[0], order[1]]
    assert len(dispatcher.requests) == 1
    assert [(call.action_id, call.tool_name) for call in environment.calls] == [
        ("decision:action:0001", order[0]), ("decision:action:0002", order[1])]
    for call in environment.calls:
        assert call.context == _context() and type(call.request) is models[call.tool_name]
        assert call.request.model_dump(mode="json") == arguments[call.tool_name]
    assert history[-1]["role"] == "assistant"
    assert [item["id"] for item in history[-1]["tool_calls"]] == [f"call-{tool}" for tool in order]
    assert not any(item["role"] == "tool" for item in history)
    assert agent._memories == []
    with pytest.raises(RuntimeError, match="already submitted"):
        agent._dispatch("search_memory", _SEARCH_ARGS)
    assert len(environment.calls) == 2


@pytest.mark.asyncio
async def test_grounded_unknown_game_tool_is_corrected_without_alias() -> None:
    """真实失败形状在同一有限循环内改用当前目标工具且仅提交一次，登记后不再请求模型。"""
    environment = _Environment()
    dispatcher = _GroundedCorrectionDispatcher()
    agent = _wire_agent(environment, dispatcher, 5)

    result = await agent.run_turns(_context(), _observations()[0]["data"])

    assert [decision.action for decision in result] == [MAIN_TOOL]
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartMainActivityRequest)
    assert environment.calls[0].request.model_dump() == _WAIT_CHOICE
    assert [item.phase for item in environment.evidence] == ["request", "response"] * 3
    assert environment.evidence[3].response["protocol_feedback"]["items"][0]["written"] == "play_game"
    assert len(dispatcher.requests) == 3
    correction = dispatcher.requests[2]
    assert correction["messages"][-1]["tool_call_id"] == "call-invalid"
    feedback = json.loads(correction["messages"][-1]["content"])
    assert (feedback["error_code"], feedback["reason"]) == ("TOOL_CALL_REQUIRED", "The tool call batch is invalid.")
    assert feedback["instruction"] == "No world action was registered. Correct the reported parameters and submit again."
    assert correction["messages"][-2]["tool_calls"][0]["function"]["name"] == "play_game"
    third_names = {item["function"]["name"] for item in correction["tools"]}
    assert {MAIN_TOOL, SIDE_TOOL} <= third_names and "start_activity" not in third_names
    assert "play_game" not in third_names
    assert "finish" not in third_names


@pytest.mark.asyncio
async def test_runtime_budget_handles_twelve_bad_responses_then_submits_once() -> None:
    """生产配置允许超过十次模型调用后纠正，完整循环仍只登记一个动作。"""
    frozen = inputs()
    max_turns = stage_model_identity(frozen.config.model, frozen.model_metadata)["max_react_turns"]
    responses = [_ModelResponse(f"bad-{index}", "invented_game", "{}") for index in range(12)]
    responses.append(_ModelResponse("action", MAIN_TOOL,
                                    json.dumps(_WAIT_CHOICE, ensure_ascii=False)))
    dispatcher = _GroundedCorrectionDispatcher(responses=responses)
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, max_turns)
    result = await agent.run_turns(_context(), _observations()[0]["data"])
    assert [decision.action for decision in result] == [MAIN_TOOL]
    assert len(dispatcher.requests) == 13
    assert len(environment.calls) == 1
    assert isinstance(environment.calls[0].request, StartMainActivityRequest)
    assert sum(item.error is not None for item in environment.evidence if item.phase == "response") == 12


@pytest.mark.asyncio
async def test_life_schema_and_target_errors_are_corrected_before_execution() -> None:
    """Pydantic参数与目标配对错误各耗一回合且只有最终合法动作执行：缺少 action、所写动作不属于所选对象都在本轮纠正。

    缺 action 是请求模型错误，带回这个工具 action 的全部合法值；对工作 app 写购买是已知对象不提供所写动作，带回它
    提供的动作与可以照抄的完整调用（属于同一个工具）。"""
    arguments = dict(_WAIT_CHOICE)
    missing = copy.deepcopy(arguments)
    missing.pop("action")
    wrong_pair = side_arguments("purchase", _WORK_ACTION)
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-schema", MAIN_TOOL, json.dumps(missing, ensure_ascii=False)),
        _ModelResponse("call-pair", SIDE_TOOL, json.dumps(wrong_pair, ensure_ascii=False)),
        _ModelResponse("call-action", MAIN_TOOL, json.dumps(arguments, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 5)

    await agent.run_turns(_context(), _observations()[0]["data"])

    assert len(environment.calls) == 1
    assert environment.calls[0].tool_name == MAIN_TOOL
    assert environment.calls[0].request == StartMainActivityRequest(**arguments)
    assert environment.evidence[1].error.startswith("ARGUMENTS_INVALID:")
    omitted = environment.evidence[1].response["protocol_feedback"]["items"][0]
    assert omitted["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert (omitted["written"], omitted["allowed_values"]) == (None, ["travel", "interact", "sleep"])
    unsupported = environment.evidence[3].response["protocol_feedback"]["items"][0]
    assert (unsupported["error_code"], unsupported["written"]) == ("ACTION_NOT_OFFERED", "purchase")
    assert unsupported["path"] == ["tool_calls", 0, "function", "arguments", "action"]
    assert unsupported["allowed_values"] == ["interact"]
    assert unsupported["candidates"] == [
        {"tool": SIDE_TOOL, "arguments": _WORK_CHOICE, "note": "整理会议纪要需要在办公室"}]
    assert len(dispatcher.requests) == 3
    assert [json.loads(request["messages"][-1]["content"])["error_code"]
            for request in dispatcher.requests[1:3]] == ["ARGUMENTS_INVALID", "ACTION_NOT_OFFERED"]


@pytest.mark.asyncio
async def test_invalid_conclusion_memories_are_corrected_before_registration() -> None:
    """收尾记忆越界由本地请求模型拒绝、按协议错误在本轮重试；只有改正后的请求登记，工具面不变且没有 finish。"""
    invalid = _conclude_args(_WAIT, memories=[[_memory("我记得 3e71a031bb3d4103 那会儿很累。")]])
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _ModelResponse("call-invalid", "conclude_activity", json.dumps(invalid, ensure_ascii=False)),
        _ModelResponse("call-action", "conclude_activity", json.dumps(_conclude_args(_WAIT), ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 4)

    result = await agent.run_turns(_context(), _step_observation())

    assert [decision.action for decision in result] == ["conclude_activity"]
    assert [call.request for call in environment.calls] == [
        ConcludeActivityRequest.model_validate(_conclude_args(_WAIT))]
    assert "hexadecimal identifier" in environment.evidence[1].error
    assert len(dispatcher.requests) == 2
    feedback = json.loads(dispatcher.requests[1]["messages"][-1]["content"])
    assert (feedback["error_code"], feedback["reason"]) == (
        "ARGUMENTS_INVALID", "memory text contains a hexadecimal identifier")
    assert feedback["items"][0]["path"][-5:] == ["items", 0, "memories", 0, "text"]
    assert "error" not in feedback and "input_value" not in json.dumps(feedback, ensure_ascii=False)
    names = _react_tool_names(dispatcher.requests)
    assert names[0] == names[1]
    assert "conclude_activity" in names[0] and "finish" not in names[0]
    assert agent._memories == _stored_memories(_conclude_args(_WAIT))


#: 一条未完成的个人事务：待办层写出的名称与它的私有绑定（事务身份只在绑定里）。
_ERRAND = "补一份食品"
_ERRAND_BINDING = {"name": _ERRAND, "errand_id": "errand-private-1", "product_id": "product-visible"}


def _errand_observation() -> dict:
    """人物步骤的观察另带一条未完成事务：待办层一行，私有事务绑定一行（docs/design/04-simulation-engine.md 、2.6）。"""
    data = _step_observation()
    data["errand_bindings"] = [dict(_ERRAND_BINDING)]
    data["todo_layer"]["errands"] = [{"name": _ERRAND, "done_when": "取得现有食品 1 份", "progress": "已取得 0 份",
                                      "memo": None, "remind_time": None, "noted_on": "2026-01-04"}]
    return {"turn": 0, "action": "observe", "ok": True, "data": data}


@pytest.mark.asyncio
async def test_errand_errors_join_the_single_host_correction() -> None:
    """个人事务的错误与活动名、参数结构错误并入宿主的同一次纠错（docs/design/04-simulation-engine.md ）：同一响应里播报
    点名不存在的活动并更新一条不存在的事务，暂停写了份数为 0 的新增。宿主在执行前整批校验一次，各调用的每条工具消息都是
    同一份纠错：批次错误之后，第一个调用的活动名错误与事务名错误（与环境复核同一函数：合法值是本人全部未完成事务名，不带
    候选）合并在同一调用里，再是第二个调用的结构错误（上限写在 why），路径都绑定到真实调用下标；这一响应没有登记任何世界
    请求。改正后的播报以无损归一的写法点名那条事务，才登记，发出的请求里没有事务身份。"""
    report = {"items": [{"activity_name": "虚构活动", "summary": "继续。"}], "errands": [errand_update("补食品", memo="下班前买")]}
    pause = {"items": [{"activity_name": _WAIT, "summary": "先停。"}], "errands": [errand_add("囤食品", "现有食品", quantity=0)]}
    corrected = {"items": [{"activity_name": _WAIT, "summary": "继续。"}],
                 "errands": [errand_update("补一份 食品", memo="下班前买")]}
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        _MultipleModelResponse([("call-report", "report_status", json.dumps(report, ensure_ascii=False)),
                                ("call-pause", "pause_activity", json.dumps(pause, ensure_ascii=False))]),
        _ModelResponse("call-action", "report_status", json.dumps(corrected, ensure_ascii=False)),
    ])
    environment = _Environment()
    agent = _wire_agent(environment, dispatcher, 4)

    result = await agent.run_turns(_context(), _errand_observation()["data"])

    assert [decision.action for decision in result] == ["report_status"]
    assert [call.request for call in environment.calls] == [ReportStatusRequest.model_validate(corrected)]
    assert len(dispatcher.requests) == 2
    tools = [message for message in dispatcher.requests[1]["messages"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tools] == ["call-report", "call-pause"]
    assert tools[0]["content"] == tools[1]["content"]
    feedback = json.loads(tools[0]["content"])
    assert feedback["instruction"] == FEEDBACK_INSTRUCTION and feedback["error_code"] == "TOOL_CALL_REQUIRED"
    # 改法是各份报告各自的改法、互不相同的依次相连：批次、名称、参数结构各一句。
    assert feedback["fix"] == "; ".join([_BATCH_FIX, "Use an exact known name.", "Correct every reported parameter."])
    assert feedback["reason"].startswith(f"{_BATCH_REASON}; ")
    call = ["function", "arguments"]
    assert [(item["path"], item["written"], item["error_code"], item["candidates"]) for item in feedback["items"]] == [
        (["tool_calls"], ["report_status", "pause_activity"], "TOOL_CALL_REQUIRED", []),
        (["tool_calls", 0, *call, "items", 0, "activity_name"], "虚构活动", "NAME_NOT_FOUND", []),
        (["tool_calls", 0, *call, "errands", 0, "name"], "补食品", "NAME_NOT_FOUND", []),
        (["tool_calls", 1, *call, "errands", 0, "quantity"], 0, "ARGUMENTS_INVALID", [])]
    errand, quantity = feedback["items"][2:]
    assert (errand["allowed_values"], errand["why"]) == ([_ERRAND], "The errand name 「补食品」 is not known.")
    assert (quantity["allowed_values"], quantity["why"]) == ([], "errand quantity must be between 1 and 9")
    assert all(_ERRAND_BINDING["errand_id"] not in json.dumps(request, ensure_ascii=False)
               for request in dispatcher.requests)


def test_native_memory_update_hashes_real_workspace_files(tmp_path) -> None:
    """结束活动写下的真实记忆经步尾保存落盘后，以 memory_update_evidence 交给引擎登记内容身份；人物第一条更新没有写入前的
    视图摘要，新建条目的编号从 m_000001 起。"""
    store = WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ())
    outcome = save_step(store, MemoryStep("林舟", (), "observation-current",
                                          tuple(_stored_memories(_conclude_args(_WAIT))), _context().observed_at))
    environment = _Environment()
    environment.record_memory_update(memory_update_evidence(_context(), store, outcome))
    written = outcome.created_episodes

    assert written[0]["source"] == "activity_conclusion"
    assert len(environment.memory_updates) == 1
    evidence = environment.memory_updates[0]
    assert evidence.context == _context()
    assert evidence.created_episode_ids == (written[0]["id"],)
    assert evidence.memory_before_sha256 is None
    assert evidence.created_episode_ids == ("m_000001",)
    for digest in (
        evidence.memory_sha256,
        evidence.episodes_sha256,
    ):
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")


def test_native_memory_update_records_real_subjective_merge_without_duplicate(tmp_path) -> None:
    """同一记忆存储上两次步尾保存：第二次合并旧身份，登记的证据保留合并、来源及文件摘要。"""
    import hashlib

    from worldmock.recording_engine.projections.activities.experiences import project_verified_experiences
    from worldmock.recording_engine.events.models import EventDraft
    from worldmock.recording_engine.storage.store import FootprintStore
    from worldmock.recording_engine.events.memory_evidence import _memory_evidence_data

    footprint = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [7]},
        {"version": 0, "current_time": _NOW_EPOCH},
    )
    source = footprint.append_evidence([EventDraft(
        event_type="activity_progress", occurred_at=_NOW_EPOCH,
        agent_id=7, activity_id="wait-activity",
        data={"interval_start": _NOW_EPOCH - 60, "interval_end": _NOW_EPOCH,
              "activity": {"activity_id": "wait-activity", "kind": "sleep"}},
    )])[0]
    facts = project_verified_experiences([source], agent_id=7, cutoff_seq=source.event_seq)
    footprint.close()
    environment = _StepEnvironment()
    store = WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ())
    conclusion = tuple(_stored_memories(_conclude_args(_WAIT)))

    first = save_step(store, MemoryStep("林舟", tuple(facts), "observation-current", conclusion,
                                        _context().observed_at))
    environment.record_memory_update(memory_update_evidence(_context(), store, first))
    initial_merge_count = read_episodes(store.workspace, 7)[0]["merge_count"]
    second = save_step(store, MemoryStep("林舟", tuple(facts), "observation-current", conclusion,
                                         _context().observed_at + timedelta(minutes=1)))
    environment.record_memory_update(memory_update_evidence(_context(), store, second))

    episodes = read_episodes(store.workspace, 7)
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
    assert merged.verified_event_ids == ()
    assert merged.memory_before_sha256 == created.memory_sha256
    assert merged.episodes_sha256 != created.episodes_sha256
    for actual, path in ((merged.memory_sha256, store.memory_md_path),
                         (merged.episodes_sha256, store.episodes_path)):
        assert actual == hashlib.sha256(path.read_bytes()).hexdigest()
    data = _memory_evidence_data(merged)
    assert data["created_episode_ids"] == []
    assert data["merged_episode_ids"] == [identity]
    assert data["episodes"] == list(merged.episodes)
    assert data["observation_event_id"] == "observation-current"
    assert data["verified_event_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_path", ["normal", "action_correction"])
async def test_person_step_records_conclusion_memory_update(tmp_path, decision_path: str) -> None:
    """真实人物决策每次仅观察一次；行动纠错后，conclude_activity 的记忆经步尾保存落盘并登记。"""
    workspace = tmp_path / "agents" / "agent_0007"
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    if decision_path == "action_correction":
        dispatcher.responses.insert(0, _ModelResponse("call-invalid", "invented_action", "{}"))
    agent = _wire_agent(environment, dispatcher, 4, WorldMockMemoryStore(workspace, 7, ()))

    assert await agent.decide(_context()) is None

    assert environment.observed_contexts == [_context()]
    assert [call.request for call in environment.calls] == [
        ConcludeActivityRequest.model_validate(_conclude_args(_WAIT))]
    expected_calls = 1 if decision_path == "normal" else 2
    assert [item.phase for item in environment.evidence] == ["request", "response"] * expected_calls
    errors = [item.error for item in environment.evidence if item.error]
    expected_error = {"normal": [], "action_correction": ["TOOL_CALL_REQUIRED: The tool call batch is invalid."]}
    assert errors == expected_error[decision_path]
    assert len(environment.memory_updates) == 1
    evidence = environment.memory_updates[0]
    lines = (workspace / "memory" / "episodes.jsonl").read_bytes().decode("utf-8").split("\n")
    episodes = [json.loads(line) for line in lines[:-1]]
    assert lines[-1] == ""
    assert evidence.context == _context()
    assert evidence.created_episode_ids == (episodes[0]["id"],)
    assert [(item["text"], item["source"], item["epistemic_status"]) for item in episodes] == [
        (_memory()["text"], "activity_conclusion", "subjective")]


@pytest.mark.asyncio
async def test_memory_persistence_failure_aborts_society_before_commit(
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    """收尾记忆在世界请求登记后未落盘时人物决策失败：运行宿主以原异常的 repr 中止本轮并登记运行失败，不结算、不建检查点，
    把同一个异常对象原样上抛；人物决策在上抛前留一条带完整异常链的英文 error 日志（docs/design/10-runtime.md ）。"""
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    host = _step_host(monkeypatch, tmp_path, environment, dispatcher)
    injected = OSError("injected append failure")

    def fail_append(*_args, **_kwargs):
        raise injected

    monkeypatch.setattr(WorldMockMemoryStore, "append_episodes", fail_append)

    with caplog.at_level(logging.ERROR), pytest.raises(OSError, match="^injected append failure$") as failure:
        await host.run()

    assert failure.value is injected
    records = [record for record in caplog.records if record.getMessage().startswith("LifeAgent decision failed")]
    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.ERROR, "LifeAgent decision failed for agent 7")]
    assert "injected append failure" in "".join(traceback.format_exception(*records[0].exc_info))
    assert len(environment.calls) == 1
    assert [item.phase for item in environment.evidence] == ["request", "response"]
    assert environment.memory_updates == []
    assert environment.commits == []
    assert environment.checkpoints == []
    assert len(environment.aborts) == 1
    assert environment.aborts[0][1] == "OSError('injected append failure')"
    assert len(environment.failures) == 1
    assert environment.failures[0] == "OSError('injected append failure')"


class _InjectedDecisionFailure(Exception):
    """注入的决策故障：独立的异常类型，用来核对上抛的类型没有被换成别的。"""


def _decision_failure(failure_point: str, environment: _StepEnvironment, dispatcher: _StepDispatcher,
                      monkeypatch) -> _InjectedDecisionFailure:
    """在人物决策的指定位置（读取观察、模型调用、收尾记忆落盘）注入同一个故障对象并返回它。"""
    injected = _InjectedDecisionFailure(f"injected {failure_point} failure")

    def fail(*_args, **_kwargs):
        raise injected

    async def fail_async(*_args, **_kwargs):
        raise injected

    if failure_point == "observe":
        monkeypatch.setattr(environment, "observe", fail)
    elif failure_point == "model":
        monkeypatch.setattr(dispatcher, "call", fail_async)
    else:
        monkeypatch.setattr(WorldMockMemoryStore, "append_episodes", fail)
    return injected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["observe", "model", "memory"])
async def test_decision_failure_is_logged_once_and_reraised_unchanged(
    monkeypatch,
    tmp_path,
    caplog,
    failure_point: str,
) -> None:
    """直接导入的 LifeAgent：决策失败时 decide 写恰好一条带完整异常链的英文 error 日志，再把同一个异常对象原样上抛，
    类型不变（docs/design/10-runtime.md ）；日志不是持久证据，不新增事件，也不改变已登记的世界请求。"""
    environment = _StepEnvironment()
    dispatcher = _StepDispatcher()
    agent = _wire_agent(environment, dispatcher, 4, WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ()))
    assert type(agent) is LifeAgent and type(agent).__module__ == "worldmock.agents.life_agent"
    injected = _decision_failure(failure_point, environment, dispatcher, monkeypatch)

    with caplog.at_level(logging.ERROR), pytest.raises(_InjectedDecisionFailure) as failure:
        await agent.decide(_context())

    assert failure.value is injected and type(failure.value) is _InjectedDecisionFailure
    records = [record for record in caplog.records if record.getMessage().startswith("LifeAgent decision failed")]
    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.ERROR, "LifeAgent decision failed for agent 7")]
    assert records[0].exc_info[0] is _InjectedDecisionFailure and records[0].exc_info[1] is injected
    assert "injected" in "".join(traceback.format_exception(*records[0].exc_info))
    assert len(environment.calls) == (1 if failure_point == "memory" else 0)
    assert environment.memory_updates == []
    assert environment.commits == [] and environment.aborts == [] and environment.failures == []


def test_home_identity_never_enters_messages_and_a_layer_carrying_it_is_rejected() -> None:
    """@brief 本人住处的地点身份 home_location_id 是观察私有事实（「家」「住处」与查询的 from 按它解析，docs/design/09-io-formats.md
    「禁止进入提示的字段」）：它留在观察顶层时，宿主构建的消息里既没有这个键也没有它的值；混进任何一层（工具层目标、
    待办层、环境层及其地点、记忆层、进度层）时，宿主渲染即以 ForbiddenObservationFieldError 拒绝并写出该键。"""
    observations = _observations()
    observations[0]["data"]["home_location_id"] = "home-private-1"
    observations[0]["data"]["locations"][0]["location_id"] = "home-private-1"
    observations[0]["data"]["location_id"] = "home-private-1"
    messages = _prepared(observations[0]["data"])._prepare(observations[0]["data"])[1]
    text = "\n".join(item["content"] for item in messages)
    assert "home_location_id" not in text and "home-private-1" not in text
    for path in (("tool_layer", "targets", 0), ("todo_layer",), ("environment_layer",),
                 ("environment_layer", "location"), ("memory_layer",), ("progress_layer",)):
        leaking = _observations()
        node = leaking[0]["data"]
        for step in path:
            node = node[step]
        node["home_location_id"] = "home-private-1"
        with pytest.raises(ForbiddenObservationFieldError, match="home_location_id"):
            _prepared(leaking[0]["data"])


def test_retired_submit_tool_is_absent_while_actions_and_activity_recall_remain() -> None:
    """工作按目录动作名称开始，实际活动名称可回看，退役提交工具不再开放。"""
    observations = _observations()
    _with_activities(observations[0]["data"], (_WORK, PHASE_ACTIVE))
    agent = _prepared(observations[0]["data"])
    assert "submit_work" not in _functions(agent)
    assert agent._validate([ReactDecision(SIDE_TOOL, _WORK_CHOICE, "")]) is None
    assert agent._validate([ReactDecision("recall_task_summary",
        {"task_name": _WORK}, "")]) is None



@pytest.mark.asyncio
async def test_usage_check_reuses_the_count_taken_before_sending(monkeypatch, caplog):
    from worldmock.agents.prompt import prompt_capacity

    class UsageResponse(_ModelResponse):
        def model_dump(self, *args, **kwargs):
            return {**super().model_dump(*args, **kwargs),
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 1, "total_tokens": 1001}}

    counts = []

    def count(**kwargs):
        counts.append(kwargs)
        return 1000

    monkeypatch.setattr(prompt_capacity, "token_counter", count)
    dispatcher = _GroundedCorrectionDispatcher(responses=[
        UsageResponse("call-memory-1", "search_memory", _SEARCH),
        UsageResponse("call-memory-2", "search_memory", _SEARCH),
        UsageResponse("call-memory-3", "search_memory", _SEARCH),
        UsageResponse("call-action", MAIN_TOOL, json.dumps(_WAIT_CHOICE, ensure_ascii=False))])
    agent = _wire_agent(_Environment(), dispatcher, 4)
    with caplog.at_level("INFO"):
        await agent.run_turns(_context(), _observations()[0]["data"])
    assert len(counts) == len(dispatcher.requests) == 4
    assert caplog.text.count("estimated=1000 actual=1000 difference=0") == 4
