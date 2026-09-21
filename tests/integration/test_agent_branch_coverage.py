"""LifeAgent 协议校验与观察冻结分支的行为覆盖测试（C4a 覆盖率单元）。

对照docs/dev/SPEC-life-runtime.md「活动组合政策」（工具面随当前观察闭集收缩、无合法身份即撤下工具）与
docs/dev/SPEC-as2-integration.md「确定性工具调用与身份绑定」（严格工具合同、身份配对、观察目录 fail-closed 冻结），为
``custom/agents/life_agent.py`` 补齐未执行分支：请求模型本地校验器拒绝、
观察目录冻结错误、活动控制与瞬时动作身份配对、记忆调度边界与两阶段
决策自述的失败传播。只新增行为测试，不改生产代码。
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.integration.storefront_fixtures import supply_row, discovery_catalog
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest, ReactToolResult
from agentsociety2.society import DecisionContext

import custom.agents.life_agent as life_agent_module
from custom.agents.life_agent import (
    FinishRequest,
    LifeAgent,
    LifeMemoryEpisode,
    MemoryReadRequest,
    MemoryRecentRequest,
    MemorySearchRequest,
    MemoryStepRangeRequest,
    MemoryTickRangeRequest,
    MemoryTimeRangeRequest,
    ReadonlyFinishRequest,
    _available_action_models,
    _bind_targeted_activity_catalogs,
    _life_tool_descriptions,
    _tool_schema,
)
from worldmock.catalog.affordances import default_affordances
from worldmock.life.engine import affordance_contract
from worldmock.life.models import REQUEST_MODELS

#: 真实注册表构造的观察合同冻结块（与引擎 observe 输出同一来源）。
_AFFORDANCE_CONTRACT = affordance_contract(default_affordances())

_OBSERVED_AT = datetime(2026, 1, 5, tzinfo=timezone.utc)
_NOW_EPOCH = int(_OBSERVED_AT.timestamp())

_SCENE = {
    "current_time": "2026-01-05T00:00:00+00:00",
    "location": "自己家",
    "people_here": [],
    "felt": {"appetite": "不饿", "sleepiness": "有点困"},
    "current_activity": {},
    "paused_activities": [],
    "day_plan": None,
    "inbox_head": [],
    "todos_summary": "",
    "reachable_places": [],
    "inventory": [],
    "recent_action_results": [],
}

_FACTS = {
    "current_time": "2026-01-05T00:00:00+00:00",
    "location_id": "home",
    "people_here": [],
    "inbox": [],
    "tasks": [
        {"task_id": "task-visible", "title": "整理会议纪要", "due_at": _NOW_EPOCH + 86400,
         "remaining_contract_seconds": 3600, "new_material_ranges": []},
    ],
    "routes": [{"destination": "office", "mode": "driving", "minutes": 4,
                "fare": 800, "feasible": True}],
    "products": [],
    "inventory": {},
    "day_plan": None,
    "felt": {"appetite": "不饿", "sleepiness": "有点困"},
    "drive_summary": "你有点困。",
    "recent_action_results": [],
}

_MEMORY_TOOL_NAMES = {
    "memory_recent", "memory_search", "memory_step_range",
    "memory_tick_range", "memory_time_range", "memory_read",
}


def _context() -> DecisionContext:
    """构造稳定测试决策上下文。"""
    return DecisionContext("run", "round", "decision", 7, _OBSERVED_AT, 3)


def _replace(key_path: tuple[str, ...], value):
    """生成把嵌套键替换为固定值的观察修改函数。"""

    def mutate(data: dict) -> None:
        target = data
        for key in key_path[:-1]:
            target = target[key]
        target[key_path[-1]] = value

    return mutate


def _drop(key_path: tuple[str, ...]):
    """生成删除嵌套键的观察修改函数。"""

    def mutate(data: dict) -> None:
        target = data
        for key in key_path[:-1]:
            target = target[key]
        del target[key_path[-1]]

    return mutate


def _observations() -> list[dict]:
    """构造含人物可见来源事件的真实结构化观察记录（深拷贝防串改）。"""
    data = {
        "observation_event_id": "observation-current",
        "current_time": _NOW_EPOCH,
        "wake_reasons": [],  # 非日首决策：不触发 plan_day 工具面强制
        "scene": copy.deepcopy(_SCENE),
        "facts": copy.deepcopy(_FACTS),
        "recent_action_results": [],
        "active_activity": None,
        "paused_activities": [],
        "energy_feasibility": {"maximum_duration_seconds_by_kind": {
            "work": 86_400, "watch": 86_400, "play_game": 86_400,
            "read_mail": 86_400, "browse": 86_400,
        }},
        "affordance_contract": copy.deepcopy(_AFFORDANCE_CONTRACT),
        "interaction_menu": {
            "allowed_tools": sorted(REQUEST_MODELS),
            "allowed_activity_kinds": sorted(_AFFORDANCE_CONTRACT),
        },
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
        "routes": [{"destination_id": "office", "status": "reachable", "mode": "driving", "duration_seconds": 216,
                    "minimum_energy_cost_kcal": 6.0, "energy_feasible": True}],
        "games": {},
    }
    return [{"turn": 0, "action": "observe", "ok": True, "data": data}]


def _base_product(**overrides) -> dict:
    """取基础可见商品并按需覆盖字段。"""
    product = dict(_observations()[0]["data"]["products"][0])
    product.update(overrides)
    return product


def _prepared(mutate=None, *, readonly: bool = False) -> LifeAgent:
    """按正式入口准备 LifeAgent 决策（可注入观察修改函数）。"""
    agent = LifeAgent()
    observations = _observations()
    if mutate is not None:
        mutate(observations[0]["data"])
    request = ReactLoopRequest(0, _OBSERVED_AT, observations, None, readonly)
    agent.prepare_react_loop(request)
    return agent


def _decision(action: str, args: dict) -> ReactDecision:
    """构造一个已解析的原生工具决定。"""
    return ReactDecision("", action, args, "")


def _tool_names(agent: LifeAgent, *, readonly: bool = False) -> set[str]:
    """读取一次构建出的工具面名字集合。"""
    return {item["function"]["name"]
            for item in agent.build_react_tool_schemas(readonly=readonly)}


# ---------------------------------------------------------------------------
# 一、请求模型本地校验器（docs/CONTRACTS.md「人物工具与管理接口」：数量边界由本地校验器拒绝）
# ---------------------------------------------------------------------------


def test_memory_request_models_enforce_local_bounds() -> None:
    """记忆查询工具保留服务端不表达的数量与区间约束（正例+反例）。"""
    assert MemoryRecentRequest(limit=50).limit == 50
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MemoryRecentRequest(limit=0)

    assert MemorySearchRequest(query="keyword", limit=1).query == "keyword"
    with pytest.raises(ValidationError, match="exceeds 200 characters"):
        MemorySearchRequest(query="word " * 41 + "x", limit=1)

    assert MemoryStepRangeRequest(start_step=0, end_step=9, limit=10).end_step == 9
    with pytest.raises(ValidationError, match="memory step range is reversed"):
        MemoryStepRangeRequest(start_step=5, end_step=1, limit=10)

    assert MemoryTickRangeRequest(start_tick=0, end_tick=60, limit=10).end_tick == 60
    with pytest.raises(ValidationError, match="memory tick range is reversed"):
        MemoryTickRangeRequest(start_tick=60, end_tick=0, limit=10)

    valid_range = MemoryTimeRangeRequest(
        start_time="2026-01-05T00:00:00+08:00",
        end_time="2026-01-05T23:59:59+08:00", limit=10)
    assert valid_range.start_time.endswith("+08:00")
    with pytest.raises(ValidationError, match="time range is reversed"):
        MemoryTimeRangeRequest(
            start_time="2026-01-06T00:00:00+08:00",
            end_time="2026-01-05T00:00:00+08:00", limit=10)
    with pytest.raises(ValidationError, match="requires ISO datetimes"):
        MemoryTimeRangeRequest(start_time="2026-13-45T99:99:99+08:00",
                               end_time="2026-01-05T00:00:00+08:00", limit=10)
    with pytest.raises(ValidationError, match="timezone-aware"):
        MemoryTimeRangeRequest(start_time="2026-01-05T00:00:00",
                               end_time="2026-01-05T01:00:00", limit=10)


def test_memory_read_and_episode_models_enforce_local_limits() -> None:
    """memory_read 与记忆条目保留身份数量、文本与引用约束。"""
    assert len(MemoryReadRequest(ids=[f"m-{i}" for i in range(20)]).ids) == 20
    with pytest.raises(ValidationError, match="one to twenty nonblank ids"):
        MemoryReadRequest(ids=[])
    with pytest.raises(ValidationError, match="one to twenty nonblank ids"):
        MemoryReadRequest(ids=[f"m-{i}" for i in range(21)])

    episode = {
        "type": "observation", "importance": 0.5, "keywords": ["wait"],
        "text": "I waited.", "source": "decision_observation",
        "refs": ["observation-current"],
    }
    assert LifeMemoryEpisode(**episode).text == "I waited."
    crowded = dict(episode, keywords=[f"k{i}" for i in range(13)])
    with pytest.raises(ValidationError, match="twelve keywords"):
        LifeMemoryEpisode(**crowded)
    with pytest.raises(ValidationError, match="exceeds 1000 characters"):
        LifeMemoryEpisode(**dict(episode, text="长" * 1001))
    with pytest.raises(ValidationError, match="hexadecimal identifier"):
        LifeMemoryEpisode(**dict(episode, text="saw event abcdef12 just now"))
    with pytest.raises(ValidationError, match="one to eight refs"):
        LifeMemoryEpisode(**dict(episode, refs=[]))
    with pytest.raises(ValidationError, match="one to eight refs"):
        LifeMemoryEpisode(**dict(episode, refs=[f"r{i}" for i in range(9)]))

    assert len(FinishRequest(memories=[LifeMemoryEpisode(**episode)]).memories) == 1
    assert FinishRequest(memories=[]).memories == []
    with pytest.raises(ValidationError, match="zero to eight memories"):
        FinishRequest(memories=[LifeMemoryEpisode(**episode) for _ in range(9)])

    assert ReadonlyFinishRequest(answer="fine").answer == "fine"
    with pytest.raises(ValidationError, match="exceeds 4000 characters"):
        ReadonlyFinishRequest(answer="word " * 801)


# ---------------------------------------------------------------------------
# 二、观察冻结 fail-closed（docs/dev/SPEC-as2-integration.md「确定性工具调用与身份绑定」：缺目录/类型错/空白身份即协议错误）
# ---------------------------------------------------------------------------


def _paused_activity(activity_id: str, **overrides) -> dict:
    """构造一条合法暂停活动记录（可覆盖字段注入错误）。"""
    entry = {"activity_id": activity_id, "status": "paused", "kind": "work",
             "remaining_duration_seconds": 60,
             "minimum_energy_cost_kcal": 1.0, "resume_energy_feasible": True}
    entry.update(overrides)
    return entry


def _frozen_observation_cases() -> list[tuple[str, object, str]]:
    """收集（用例名, 观察修改函数, 期望错误片段）清单。"""
    return [
        ("contract missing", _drop(("affordance_contract",)),
         "affordance contract is unavailable"),
        ("contract empty", _replace(("affordance_contract",), {}),
         "affordance contract is unavailable"),
        ("contract entry not object", _replace(("affordance_contract", "wait"), 5),
         "contract entry is invalid"),
        ("contract keys changed", _drop(("affordance_contract", "wait", "surface")),
         "contract keys are invalid"),
        ("contract surface blank", _replace(("affordance_contract", "wait", "surface"), ""),
         "contract surface is invalid"),
        ("contract discovery not string",
         _replace(("affordance_contract", "wait", "target_discovery"), 5),
         "target discovery is invalid"),
        ("contract target field shape",
         _replace(("affordance_contract", "work", "target_field"), ["task_id"]),
         "target field is invalid"),
        ("contract bool field", _replace(("affordance_contract", "wait", "continuable"), "yes"),
         "continuable is invalid"),
        ("current time negative", _replace(("current_time",), -1),
         "observation current time is invalid"),
        ("current time boolean", _replace(("current_time",), True),
         "observation current time is invalid"),
        ("wake reasons blank entry", _replace(("wake_reasons",), [""]),
         "wake reasons are invalid"),
        ("observation identity blank", _replace(("observation_event_id",), ""),
         "observation event identity is unavailable"),
        ("action results not list", _replace(("recent_action_results",), "nope"),
         "recent action results are invalid"),
        ("action result identity blank",
         _replace(("recent_action_results",), [{"event_id": ""}]),
         "action result event identity is invalid"),
        ("active activity missing", _drop(("active_activity",)),
         "active_activity is unavailable"),
        ("active activity not object", _replace(("active_activity",), 5),
         "must be an object or null"),
        ("active identity blank",
         _replace(("active_activity",),
                  {"activity_id": "", "kind": "watch", "status": "active"}),
         "active activity identity is invalid"),
        ("active state not active",
         _replace(("active_activity",),
                  {"activity_id": "act-1", "kind": "watch", "status": "paused"}),
         "active activity state is invalid"),
        ("active kind undeclared",
         _replace(("active_activity",),
                  {"activity_id": "act-1", "kind": "undeclared", "status": "active"}),
         "kind has no decision boundary controls"),
        ("paused activities not list", _replace(("paused_activities",), "x"),
         "paused_activities must be a list"),
        ("paused state not paused",
         _replace(("paused_activities",), [_paused_activity("p1", status="active")]),
         "paused activity state is invalid"),
        ("paused identity blank",
         _replace(("paused_activities",), [_paused_activity("")]),
         "paused activity identity is invalid"),
        ("paused energy invalid",
         _replace(("paused_activities",), [_paused_activity("p1",
                                                            remaining_duration_seconds=-1)]),
         "paused activity energy feasibility is invalid"),
        ("paused identities duplicated",
         _replace(("paused_activities",),
                  [_paused_activity("p1"), _paused_activity("p1")]),
         "paused activity identities are duplicated"),
        ("game session missing",
         _replace(("active_activity",),
                  {"activity_id": "act-game", "kind": "play_game", "status": "active"}),
         "active game session is invalid"),
        ("location blank", _replace(("location_id",), ""),
         "current location is invalid"),
        ("products not list", _replace(("products",), "x"),
         "products catalog must be a list"),
        ("product not object", _replace(("products",), [5]),
         "product must be an object"),
        ("product identity blank", _replace(("products",), [_base_product(product_id="")]),
         "product identity is invalid"),
        ("supply stock negative",
         _replace(("supplies",), [supply_row(current_stock=-1)]),
         "supply stock is invalid"),
        ("product use modes empty",
         _replace(("products",), [_base_product(use_modes=[])]),
         "use modes are invalid"),
        ("product duration nonpositive",
         _replace(("products",), [_base_product(use_duration_seconds=0)]),
         "use contract is invalid"),
        ("product required location invalid",
         _replace(("products",), [_base_product(required_location_id=5)]),
         "required location is invalid"),
        ("products duplicated",
         _replace(("products",), [_base_product(), _base_product()]),
         "product identities are duplicated"),
        ("locations not list", _replace(("locations",), "x"),
         "locations catalog must be a list"),
        ("inventory not object", _replace(("available_inventory",), "x"),
         "available inventory or location is invalid"),
        ("inventory entry invalid", _replace(("available_inventory",),
                                             {"product-visible": "2"}),
         "available inventory entry is invalid"),
        ("held product contract unavailable",
         _replace(("available_inventory",), {"product-ghost": 1.0}),
         "held product contract is unavailable"),
        ("facts not object", _replace(("facts",), 5),
         "facts projection is unavailable"),
        ("task material not object",
         _replace(("tasks",), [{"task_id": "task-visible",
                                "remaining_contract_seconds": 3600,
                                "material": "x"}]),
         "task submission contract is invalid"),
        ("tasks not list", _replace(("tasks",), "x"),
         "tasks catalog must be a list"),
        ("task not object", _replace(("tasks",), [5]),
         "tasks catalog item must be an object"),
        ("task material progress invalid",
         _replace(("tasks",), [{"task_id": "task-visible",
                                "remaining_contract_seconds": 3600,
                                "material": {"end_char": 20, "total_chars": 10}}]),
         "task material progress is invalid"),
        ("routes not list", _replace(("routes",), "x"),
         "routes catalog must be a list"),
        ("routes duplicated",
         _replace(("routes",), [_observations()[0]["data"]["routes"][0],
                                _observations()[0]["data"]["routes"][0]]),
         "route contracts are duplicated"),
        ("people not list", _replace(("facts", "people_here"), "x"),
         "people projection is invalid"),
        ("people row not object", _replace(("facts", "people_here"), [5]),
         "people projection row must be an object"),
        ("person identity blank", _replace(("facts", "people_here"), [{"person_id": ""}]),
         "person identity is invalid"),
        ("contacts not list", _replace(("facts", "contacts"), "x"),
         "contacts projection is invalid"),
        ("contacts row not object", _replace(("facts", "contacts"), [5]),
         "contacts projection row must be an object"),
        ("contact identity blank", _replace(("facts", "contacts"), [{"contact_id": ""}]),
         "contact identity is invalid"),
        ("inbox not list", _replace(("facts", "inbox"), "x"),
         "inbox projection is invalid"),
        ("inbox row not object", _replace(("facts", "inbox"), [5]),
         "inbox row must be an object"),
        ("inbox sender blank", _replace(("facts", "inbox"), [{"sender_id": ""}]),
         "inbox sender identity is invalid"),
        ("day plan goals invalid",
         _replace(("facts", "day_plan"), {"goals": "x"}),
         "day plan goals are invalid"),
        ("old day plan shape invalid",
         _replace(("facts", "day_plan"),
                  {"segments": [{"start": -1, "end": 60, "intent_kind": "work", "note": "n"}]}),
         "day plan goals are invalid"),
        ("progress not object", _replace(("content_progress",), []),
         "content_progress must be an object"),
        ("progress identity mismatch",
         _replace(("content_progress",),
                  {"a": {"item_id": "b", "position_seconds": 0.0,
                         "remaining_media_seconds": 10.0, "exposure_count": 0}}),
         "content progress identity is invalid"),
        ("progress exposure invalid",
         _replace(("content_progress",),
                  {"content-visible": {"item_id": "content-visible",
                                       "position_seconds": 0.0,
                                       "remaining_media_seconds": 630.0,
                                       "exposure_count": -1}}),
         "content progress value is invalid"),
        ("progress time negative",
         _replace(("content_progress",),
                  {"content-visible": {"item_id": "content-visible",
                                       "position_seconds": -1.0,
                                       "remaining_media_seconds": 631.0,
                                       "exposure_count": 0}}),
         "content progress time is invalid"),
    ]


@pytest.mark.parametrize("name,mutate,match", _frozen_observation_cases())
def test_observation_corruptions_fail_closed_at_prepare(name, mutate, match) -> None:
    """观察合同与两层投影的损坏在 prepare 时按协议错误拒绝（不替换为空）。"""
    with pytest.raises(RuntimeError, match=match):
        _prepared(mutate)


def test_observation_envelope_and_scene_projection_fail_closed() -> None:
    """观察信封（数量/形状/data 缺失）与 scene 投影损坏都按协议错误拒绝。"""
    with pytest.raises(RuntimeError, match="one structured environment observation"):
        LifeAgent().prepare_react_loop(ReactLoopRequest(0, _OBSERVED_AT, []))
    with pytest.raises(RuntimeError, match="one structured environment observation"):
        LifeAgent().prepare_react_loop(
            ReactLoopRequest(0, _OBSERVED_AT, _observations() + _observations()))
    with pytest.raises(RuntimeError, match="observation data is unavailable"):
        LifeAgent().prepare_react_loop(
            ReactLoopRequest(0, _OBSERVED_AT, [{"turn": 0, "action": "observe",
                                                "ok": True}]))

    observations = _observations()
    observations[0]["data"]["scene"] = 5
    request = ReactLoopRequest(0, _OBSERVED_AT, observations)
    agent = LifeAgent()
    agent.prepare_react_loop(request)
    with pytest.raises(RuntimeError, match="scene projection is unavailable"):
        agent.build_react_messages(request)


def test_defensive_catalog_guards_reject_prevalidated_shapes() -> None:
    """防御层直调：prepare 前置校验已挡住的形状在独立入口同样拒绝。"""
    from custom.agents.life_agent import (
        _browsed_content_durations,
        _validate_activity_control,
        _visible_submittable_tasks,
    )

    with pytest.raises(RuntimeError, match="recent action results are invalid"):
        _browsed_content_durations("nope")
    with pytest.raises(RuntimeError, match="tasks catalog must be a list"):
        _visible_submittable_tasks({"tasks": "x"})
    controls = {"active": None, "paused_ids": (), "resumable_paused_ids": (),
                "game_session_id": None, "game_commands": ()}
    assert _validate_activity_control("purchase", SimpleNamespace(), controls) == ""


def test_active_activity_also_listed_as_paused_is_rejected() -> None:
    """同一活动既活跃又列入暂停是矛盾观察，直接协议错误。"""

    def mutate(data: dict) -> None:
        data["active_activity"] = {"activity_id": "act-1", "kind": "watch",
                                   "status": "active"}
        data["paused_activities"] = [_paused_activity("act-1")]

    with pytest.raises(RuntimeError, match="also listed as paused"):
        _prepared(mutate)


def test_game_progress_and_commands_fail_closed() -> None:
    """游戏会话进度缺失、终局与命令形状错误均为协议错误。"""
    active_game = {"activity_id": "act-game", "kind": "play_game", "status": "active",
                   "data": {"game_session_id": "session-1"}}

    def with_games(games: dict):
        def mutate(data: dict) -> None:
            data["active_activity"] = active_game
            data["games"] = games
        return mutate

    with pytest.raises(RuntimeError, match="active game progress is unavailable"):
        _prepared(with_games({}))
    with pytest.raises(RuntimeError, match="active game progress is terminal"):
        _prepared(with_games({"session-1": {"session_id": "session-1", "won": True,
                                            "lost": False,
                                            "admissible_commands": ["look"]}}))
    with pytest.raises(RuntimeError, match="active game commands must be a list"):
        _prepared(with_games({"session-1": {"session_id": "session-1", "won": False,
                                            "lost": False,
                                            "admissible_commands": "look"}}))


# ---------------------------------------------------------------------------
# 三、browse 结果与观看进度冻结（docs/dev/SPEC-life-runtime.md「活动组合政策」：观看目标闭集来自下钻结果）
# ---------------------------------------------------------------------------


def _browse_entry(event_id: str, items: object, *, status: str = "completed",
                  tool: str = "browse_activities") -> dict:
    """构造一条 browse_activities 动作结果。"""
    return {"event_id": event_id, "result": {"status": status,
                                             "data": {"tool": tool, "items": items}}}


def _targeted_enum(agent: LifeAgent) -> list:
    """读取本轮 start_targeted_activity 的可见目标枚举。"""
    schema = next(item for item in agent.build_react_tool_schemas(readonly=False)
                  if item["function"]["name"] == "start_targeted_activity")
    return schema["function"]["parameters"]["properties"]["target_id"]["enum"]


def test_browsed_results_freeze_watch_targets_and_skip_non_content_items() -> None:
    """下钻结果的内容条目成为观看目标；非内容条目与失败结果跳过。"""

    def mutate(data: dict) -> None:
        data["recent_action_results"] = [
            _browse_entry("ev-1", [
                {"activity_id": "gym-1"},
                {"item_id": "vid-1", "duration_seconds": 600},
            ]),
            _browse_entry("ev-2", [], status="failed"),
            _browse_entry("ev-3", [{"item_id": "ignored", "duration_seconds": 300}],
                          tool="purchase"),
        ]

    agent = _prepared(mutate)
    enum = _targeted_enum(agent)
    assert "vid-1" in enum
    assert "ignored" not in enum


def test_browsed_item_shapes_fail_closed() -> None:
    """下钻条目身份空白、时长非法与重复歧义均拒绝。"""
    with pytest.raises(RuntimeError, match="browsed activity items must be a list"):
        _prepared(_replace(("recent_action_results",), [_browse_entry("ev-1", "nope")]))
    with pytest.raises(RuntimeError, match="browsed content duration is invalid"):
        _prepared(_replace(("recent_action_results",),
                           [_browse_entry("ev-1", [{"item_id": "",
                                                    "duration_seconds": 600}])]))
    with pytest.raises(RuntimeError, match="browsed content duration is invalid"):
        _prepared(_replace(("recent_action_results",),
                           [_browse_entry("ev-1", [{"item_id": "vid-1",
                                                    "duration_seconds": 0}])]))
    ambiguous = [_browse_entry("ev-1", [{"item_id": "vid-1", "duration_seconds": 600}]),
                 _browse_entry("ev-2", [{"item_id": "vid-1", "duration_seconds": 700}])]
    with pytest.raises(RuntimeError, match="browsed content duration is ambiguous"):
        _prepared(_replace(("recent_action_results",), ambiguous))


def test_content_progress_remaining_and_replay_semantics() -> None:
    """进度剩余时长须与总时长-位置一致；播完后下次可用仍为完整时长。"""

    def with_progress(position: float, remaining: float):
        def mutate(data: dict) -> None:
            data["recent_action_results"] = [
                _browse_entry("ev-1", [{"item_id": "vid-1", "duration_seconds": 600}])]
            data["content_progress"] = {"vid-1": {
                "item_id": "vid-1", "position_seconds": position,
                "remaining_media_seconds": remaining, "exposure_count": 2}}
        return mutate

    with pytest.raises(RuntimeError, match="content remaining time is invalid"):
        _prepared(with_progress(100.0, 400.0))

    agent = _prepared(with_progress(600.0, 0.0))
    assert "vid-1" in _targeted_enum(agent)


# ---------------------------------------------------------------------------
# 四、工具面随活动状态收缩（docs/dev/SPEC-life-runtime.md「活动组合政策」：无合法身份即撤下工具）
# ---------------------------------------------------------------------------


def test_consume_contract_only_offers_feasible_held_products() -> None:
    """持有量为零、时长不足与地点不符的持有物不进入 consume 枚举。"""

    def mutate(data: dict) -> None:
        data["inventory_items"] = [
            {"product_id": "product-visible", "name": "现有食品", "use_modes": ["eat"],
             "use_duration_seconds": 300, "required_location_id": None},
            {"product_id": "product-short", "name": "短时物品", "use_modes": ["use"],
             "use_duration_seconds": 30, "required_location_id": None},
            {"product_id": "product-office", "name": "办公室物品", "use_modes": ["use"],
             "use_duration_seconds": 300, "required_location_id": "office"},
        ]
        data["available_inventory"] = {"product-visible": 1.0, "product-zero": 0.0,
                                       "product-short": 2.0, "product-office": 1.0}

    agent = _prepared(mutate)
    schema = next(item for item in agent.build_react_tool_schemas(readonly=False)
                  if item["function"]["name"] == "consume")
    enum = schema["function"]["parameters"]["properties"]["items"]["items"][
        "properties"]["product_id"]["enum"]
    assert enum == ["product-visible"]


def test_remote_merchant_forces_delivery_only_purchase() -> None:
    """当前地点无自提资格时履约枚举只保留 delivery（不发送非法 pickup）。"""

    def mutate(data: dict) -> None:
        data["supplies"] = [supply_row(location_id="office", pickup_allowed=False)]

    agent = _prepared(mutate)
    schema = next(item for item in agent.build_react_tool_schemas(readonly=False)
                  if item["function"]["name"] == "purchase")
    enum = schema["function"]["parameters"]["properties"]["fulfillment"]["enum"]
    assert enum == ["delivery"]


def _feedback_agent() -> LifeAgent:
    """准备一个观看活动反馈边界上的决策。"""

    def mutate(data: dict) -> None:
        data["active_activity"] = {"activity_id": "act-1", "kind": "watch",
                                   "status": "active"}

    return _prepared(mutate)


def test_feedback_boundary_withholds_switch_tools_without_visible_targets() -> None:
    """反馈边界无可切换目标与路线时只保留继续/暂停与基础活动工具。"""

    def mutate(data: dict) -> None:
        data["active_activity"] = {"activity_id": "act-1", "kind": "watch",
                                   "status": "active"}
        data["tasks"] = []
        data["facts"]["tasks"] = []
        data["content_progress"] = {}
        data["game_catalog"] = []
        data["messages"] = []
        data["browse_targets"] = []
        data["routes"] = []

    agent = _prepared(mutate)
    assert _tool_names(agent) == {"continue_activity", "set_activity_state",
                                  "start_basic_activity",
                                  "change_playback_speed"} | _MEMORY_TOOL_NAMES


def test_visible_people_contacts_and_inbox_open_social_tools() -> None:
    """在场人物、显式空联系人表与无发件人收件箱行合成收件人/交谈闭集。"""

    def mutate(data: dict) -> None:
        data["facts"]["people_here"] = [{"person_id": "person-2"}]
        data["facts"]["contacts"] = None
        data["facts"]["inbox"] = [
            {"message_id": "message-plain"},          # 无发件人行跳过
            {"message_id": "message-asked", "sender_id": "contact-1"},
        ]

    agent = _prepared(mutate)
    schemas = {item["function"]["name"]: item["function"]
               for item in agent.build_react_tool_schemas(readonly=False)}
    assert "send_message" in schemas
    assert "start_social_activity" in schemas
    recipients = schemas["send_message"]["parameters"]["properties"][
        "recipient_id"]["enum"]
    assert recipients == ["contact-1", "person-2"]
    social = schemas["start_social_activity"]["parameters"]["properties"]
    assert social["kind"]["enum"] == ["chat"]
    assert social["partner_id"]["enum"] == ["contact-1", "person-2"]


def test_game_tools_follow_current_visible_game_state() -> None:
    """游戏边界按 admissible_commands 提供或撤下 game_action；命令闭集入枚举。"""
    active_game = {"activity_id": "act-game", "kind": "play_game", "status": "active",
                   "data": {"game_session_id": "session-1"}}

    def prepare_games(commands: list) -> LifeAgent:
        def mutate(data: dict) -> None:
            data["active_activity"] = active_game
            data["games"] = {"session-1": {
                "session_id": "session-1", "won": False, "lost": False,
                "admissible_commands": commands}}
        return _prepared(mutate)

    assert _tool_names(prepare_games([])) == {"set_activity_state"} | _MEMORY_TOOL_NAMES

    agent = prepare_games(["look", "take coin"])
    schema = next(item for item in agent.build_react_tool_schemas(readonly=False)
                  if item["function"]["name"] == "game_action")
    properties = schema["function"]["parameters"]["properties"]
    assert properties["game_session_id"]["enum"] == ["session-1"]
    assert properties["command"]["enum"] == ["look", "take coin"]

    valid = _decision("game_action", {"game_session_id": "session-1", "command": "look"})
    assert agent.validate_react_decisions([valid], False) == ""
    invalid = _decision("game_action", {"game_session_id": "session-1", "command": "fly"})
    assert "game_action is not valid" in agent.validate_react_decisions([invalid], False)


def test_social_boundary_activities_cross_decision_boundary_cancel_only() -> None:
    """交谈/通话以 ACTIVE 跨决策边界：控制合同成立、工具面只剩 set_activity_state 且仅可取消。

    run11 round 36 死点复现面：唤醒节律把计划未完的 chat/call 对齐成反馈边界后，
    agent 侧闭集必须认识这两个非注册表 kind（docs/CONTRACTS.md「交谈与通话」：
    不可暂停、取消任意时刻允许），否则协议错误杀死整个决策轮。
    """
    for kind in ("chat", "call"):
        def mutate(data: dict, social_kind: str = kind) -> None:
            data["active_activity"] = {"activity_id": f"act-{social_kind}",
                                       "kind": social_kind, "status": "active"}

        agent = _prepared(mutate)
        # 工具面只含取消类控制：continue_activity 与 start_* 会被引擎按注册表
        # 边界门槛拒绝，暴露即制造业务拒绝节律。
        assert _tool_names(agent) == {"set_activity_state"} | _MEMORY_TOOL_NAMES
        cancel = _decision("set_activity_state",
                           {"activity_id": f"act-{kind}", "operation": "cancel"})
        assert agent.validate_react_decisions([cancel], False) == ""
        pause = _decision("set_activity_state",
                          {"activity_id": f"act-{kind}", "operation": "pause"})
        assert "set_activity_state is not valid" in agent.validate_react_decisions([pause], False)
        resume = _decision("set_activity_state",
                           {"activity_id": f"act-{kind}", "operation": "resume"})
        assert "set_activity_state is not valid" in agent.validate_react_decisions([resume], False)
        continue_action = _decision("continue_activity", {"activity_id": f"act-{kind}"})
        assert "continue_activity is not available" in agent.validate_react_decisions(
            [continue_action], False)


def test_browse_withdrawn_without_categories_and_targeted_removed_when_empty() -> None:
    """无品类可下钻时撤下 browse_activities；全目录空时移除整个目标工具。"""
    models = _available_action_models(
        targets={"work": ()},
        controls={"active": None, "paused_ids": (), "resumable_paused_ids": ()},
        contracts={},
        browse_categories=frozenset(),
    )
    assert "browse_activities" not in models
    assert "start_targeted_activity" not in models

    schema = _tool_schema("start_targeted_activity",
                          life_agent_module._TOOL_MODELS["start_targeted_activity"],
                          _life_tool_descriptions({}))
    schemas = [schema]
    _bind_targeted_activity_catalogs(schemas, ("work",), {"work": ()}, {"work": 600})
    assert schemas == []  # 不发送空枚举（docs/CONTRACTS.md「人物工具与管理接口」）


# ---------------------------------------------------------------------------
# 五、执行前本地配对校验（docs/CONTRACTS.md「人物工具与管理接口」：身份配对错误不提交环境动作）
# ---------------------------------------------------------------------------


def test_activity_control_pairings_are_validated_locally() -> None:
    """控制工具身份/操作配对错误在本地反馈，不提交环境。"""
    agent = _feedback_agent()
    wrong_id = _decision("set_activity_state", {"activity_id": "act-ghost",
                                                "operation": "pause"})
    assert "set_activity_state is not valid" in agent.validate_react_decisions(
        [wrong_id], False)
    wrong_operation = _decision("set_activity_state", {"activity_id": "act-1",
                                                       "operation": "resume"})
    assert "set_activity_state is not valid" in agent.validate_react_decisions(
        [wrong_operation], False)
    assert agent.validate_react_decisions(
        [_decision("set_activity_state", {"activity_id": "act-1", "operation": "pause"})],
        False) == ""

    wrong_continue = _decision("continue_activity", {"activity_id": "act-ghost"})
    assert "continue_activity activity_id is not the current visible activity" in (
        agent.validate_react_decisions([wrong_continue], False))
    assert agent.validate_react_decisions(
        [_decision("continue_activity", {"activity_id": "act-1"})], False) == ""


def test_instant_action_pairings_are_validated_locally() -> None:
    """瞬时动作的身份配对、目的地与履约边界在本地核对。"""
    agent = _prepared()

    ghost_work = _decision("submit_work", {
        "task_id": "task-ghost",
        "deliverable": {"summary": "s", "decisions": ["d"], "action_items": ["a"]}})
    assert "no fully revealed visible material" in agent.validate_react_decisions(
        [ghost_work], False)

    travel = _decision("start_travel", {"destination_id": "office",
                                        "travel_mode": "driving",
                                        "planned_duration_seconds": 216})
    assert agent.validate_react_decisions([travel], False) == ""

    def order(destination: str, fulfillment: str) -> ReactDecision:
        return _decision("purchase", {
            "items": [{"supply_id": 'supply-visible', "quantity": 1,
                       "price_version": "price-v1"}],
            "fulfillment": fulfillment, "destination_id": destination})

    assert "delivery scope" in agent.validate_react_decisions(
        [order("ghost-loc", "delivery")], False)
    assert "pickup requires" in agent.validate_react_decisions(
        [order("office", "pickup")], False)

    two_item_use = _decision("consume", {
        "items": [{"product_id": "product-visible", "quantity": 1},
                  {"product_id": "product-visible", "quantity": 1}],
        "use_mode": "use", "duration_seconds": 300})
    assert "consume use mode accepts exactly one item" in agent.validate_react_decisions(
        [two_item_use], False)


# ---------------------------------------------------------------------------
# 六、ReAct 循环守卫与记忆调度边界（docs/dev/SPEC-as2-integration.md「失败传播改动」）
# ---------------------------------------------------------------------------


async def test_observe_and_submit_require_bound_environment() -> None:
    """环境未绑定时观察与动作提交都明确失败，不静默跳过。"""
    agent = _prepared()
    with pytest.raises(RuntimeError, match="Environment is not initialized"):
        await agent._observe_environment()

    submitted = _prepared()
    submitted._decision_context = _context()
    with pytest.raises(RuntimeError, match="Environment is not initialized"):
        await submitted.dispatch_react_tool(
            "start_basic_activity",
            {"activity_type": "wait", "planned_duration_seconds": 60})


async def test_dispatch_guards_reject_unusable_tools_before_execution() -> None:
    """只读禁生活动作、未知工具、重复提交与未准备动作都拒绝。"""
    with pytest.raises(RuntimeError, match="Life actions are disabled in readonly mode"):
        await _prepared().dispatch_react_tool(
            "start_basic_activity",
            {"activity_type": "wait", "planned_duration_seconds": 60},
            readonly=True)
    with pytest.raises(RuntimeError, match="Unsupported LifeAgent tool dispatch"):
        await _prepared().dispatch_react_tool("ghost_tool", {})

    duplicated = _prepared()
    duplicated._life_action_count = 1
    with pytest.raises(RuntimeError, match="Life action was already submitted"):
        await duplicated.dispatch_react_tool(
            "start_basic_activity",
            {"activity_type": "wait", "planned_duration_seconds": 60})

    feedback = _feedback_agent()
    with pytest.raises(RuntimeError, match="unavailable in the prepared activity state"):
        await feedback.dispatch_react_tool("purchase", {
            "items": [{"supply_id": 'supply-visible', "quantity": 1,
                       "price_version": "price-v1"}],
            "fulfillment": "delivery", "destination_id": "home"})


def test_action_and_finish_state_guards_in_validation() -> None:
    """已提交动作后再验业务动作/结束都得到明确反馈。"""
    agent = _prepared()
    agent._life_action_count = 1
    repeated = _decision("start_basic_activity", {"activity_type": "wait",
                                                  "planned_duration_seconds": 60})
    assert "already submitted its life action" in agent.validate_react_decisions(
        [repeated], False)

    finished = _prepared()
    finished._life_action_count = 1
    finished.build_react_tool_schemas(readonly=False)
    finished._life_action_count = 2  # 防御：动作计数被破坏后 finish 拒绝
    finish = _decision("finish", {"memories": []})
    assert "finish requires exactly one submitted life action" in (
        finished.validate_react_decisions([finish], False))

    unprepared = _prepared(readonly=True)
    unprepared._life_request_models = None
    with pytest.raises(RuntimeError, match="request tool schemas are not prepared"):
        unprepared.validate_react_decisions([], True)


def test_action_schema_requires_frozen_target_catalogs() -> None:
    """动作模型已准备但目标目录未冻结时构建 Schema 明确失败。"""
    agent = LifeAgent()
    agent._max_react_turns = 4
    agent._model_call_sequence = 0
    agent._life_readonly = False
    agent._life_action_count = 0
    agent._life_action_models = {}
    with pytest.raises(RuntimeError, match="visible target catalogs are not prepared"):
        agent.build_react_tool_schemas(readonly=False)


async def test_memory_dispatch_reports_runtime_failures_and_unknown_ids_as_retry_feedback() -> None:
    """记忆工具失败与未知身份引用以可重试的失败反馈返回，不再 raise 杀 run（unit-16 E4）。"""
    agent = _prepared()
    agent._memory_runtime = SimpleNamespace(
        dispatch_tool=lambda action, args: (False, "memory backend unavailable", None))
    failure = await agent.dispatch_react_tool("memory_recent", {"limit": 1})
    assert isinstance(failure, ReactToolResult)
    assert failure.ok is False
    assert "memory backend unavailable" in failure.observation

    agent._memory_runtime = SimpleNamespace(
        dispatch_tool=lambda action, args: (
            True, "{}", {"episodes": [{"id": "m-other", "text": "other"}]}))
    miss = await agent.dispatch_react_tool("memory_read", {"ids": ["m-wanted"]})
    assert isinstance(miss, ReactToolResult)
    assert miss.ok is False
    assert "m-wanted" in miss.observation
    assert miss.data == {"unknown_ids": ["m-wanted"]}
    assert "m-wanted" not in agent._life_visible_refs
    result = await agent.dispatch_react_tool("memory_read", {"ids": ["m-other"]})
    assert result.ok is True
    assert "m-other" in agent._life_visible_refs


# ---------------------------------------------------------------------------
# 七、两阶段决策自述失败传播（docs/dev/SPEC-as2-integration.md「失败传播改动」：传输异常立即传播并保存失败证据）
# ---------------------------------------------------------------------------


class _RecordingEnvironment:
    """捕获模型证据与动作提交的最小环境。"""

    def __init__(self) -> None:
        self.evidence = []
        self.calls = []

    async def record_model_exchange(self, exchange) -> None:
        """保存模型请求与响应证据。"""
        self.evidence.append(exchange)

    async def submit_action(self, call):
        """记录提交的调用。"""
        self.calls.append(call)


class _RaisingDispatcher:
    """决策自述请求即抛出传输异常的调度器。"""

    model_name = "deepseek-v4.1-flash"

    async def call(self, **request):
        """注入一次传输失败。"""
        raise OSError("connection reset")


def _wire(agent: LifeAgent, environment, dispatcher) -> None:
    """装配协议守卫测试所需的最小人物接线。"""
    agent._env = environment
    agent._dispatcher = dispatcher
    agent._model_name = "deepseek-v4.1-flash"
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = False
    agent._decision_context = _context()
    agent._max_react_turns = 4
    agent._model_call_sequence = 0


def test_prelude_decision_statement_requires_initialized_llm() -> None:
    """模型未初始化时决策自述请求明确失败。"""
    agent = LifeAgent()
    with pytest.raises(RuntimeError, match="LLM is not initialized"):
        agent.prelude_decision_statement_request(
            ReactLoopRequest(0, _OBSERVED_AT, _observations()))


async def test_prelude_decision_statement_never_exposes_tools() -> None:
    """决策自述请求参数携带工具时按内部失败拒绝。"""
    agent = LifeAgent()
    agent.prelude_decision_statement_request = (  # type: ignore[method-assign]
        lambda request: {"tools": [{"type": "function"}]})
    with pytest.raises(RuntimeError, match="must not expose tools"):
        await agent._run_prelude_decision_statement(
            ReactLoopRequest(0, _OBSERVED_AT, _observations()))


async def test_decision_statement_once_requires_dispatcher() -> None:
    """调度器缺失时单次决策自述明确失败。"""
    agent = LifeAgent()
    agent._dispatcher = None
    with pytest.raises(RuntimeError, match="LLM is not initialized"):
        await agent._decision_statement_once({"model": "deepseek-v4.1-flash", "messages": []})


async def test_decision_statement_transport_failure_is_recorded_and_propagated() -> None:
    """决策自述传输异常保存失败证据后立即传播。"""
    agent = LifeAgent()
    environment = _RecordingEnvironment()
    _wire(agent, environment, _RaisingDispatcher())
    with pytest.raises(OSError, match="connection reset"):
        await agent._decision_statement_once({"model": "deepseek-v4.1-flash",
                                     "messages": [{"role": "user", "content": "hi"}]})
    assert [item.phase for item in environment.evidence] == ["request", "failure"]
    assert "connection reset" in environment.evidence[1].error
    assert environment.calls == []


# ---------------------------------------------------------------------------
# 八、提示段渲染边界（docs/dev/SPEC-life-realism.md「两阶段决策」：只读问题可选、空记忆段省略）
# ---------------------------------------------------------------------------


def _readonly_agent(memory: dict) -> LifeAgent:
    """构造启用记忆的只读人物（记忆上下文可控）。"""
    agent = LifeAgent()
    agent._id = 7
    agent._name = "Alice"
    agent._profile = {"id": 7, "name": "Alice"}
    agent._enable_memory = True
    agent._build_memory_context = lambda: memory  # type: ignore[method-assign]
    request = ReactLoopRequest(0, _OBSERVED_AT, [], None, True)
    agent.prepare_react_loop(request)
    return agent


def test_readonly_messages_omit_blank_memory_and_missing_question() -> None:
    """无外部问题且记忆摘要为空时，user 只含人物卡片。"""
    agent = _readonly_agent({"memory_md": "", "recent_episodes": []})
    messages = agent.build_react_messages(
        ReactLoopRequest(0, _OBSERVED_AT, [], None, True))
    assert [item["role"] for item in messages] == ["system", "user"]
    assert "外部只读问题" not in messages[1]["content"]
    assert "最近的记忆" not in messages[1]["content"]
    assert "你是Alice" in messages[1]["content"]


def test_memory_section_fails_closed_on_broken_episodes() -> None:
    """记忆渲染输入损坏（非列表/空白身份）按运行时错误拒绝。"""
    agent = _readonly_agent({"memory_md": "# Memory", "recent_episodes": "not-a-list"})
    with pytest.raises(RuntimeError, match="Visible memory episodes must be a list"):
        agent.build_react_messages(ReactLoopRequest(0, _OBSERVED_AT, [], None, True))

    agent = _readonly_agent({"memory_md": "# Memory", "recent_episodes": [{"id": ""}]})
    with pytest.raises(RuntimeError, match="episode identity is unavailable"):
        agent.build_react_messages(ReactLoopRequest(0, _OBSERVED_AT, [], None, True))
