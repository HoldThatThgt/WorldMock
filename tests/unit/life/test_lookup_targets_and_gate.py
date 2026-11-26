"""@brief 顶层工具层 gate 解耦、核心常驻收录、零耗时 lookup_targets 与精细 layer_changes 测试。"""

from __future__ import annotations

from copy import deepcopy

from tests.life.conftest import (
    advance_until_ready,
    sleep,
    stage_day_plan,
    travel,
    use_app,
)
from tests.life.environment_runtime import act, act_group, events
from worldmock.agents.life_agent import life_tool_schema_sha256
from worldmock.agents.prompt.narration import enforce_field_blacklist
from worldmock.simulation_engine.actions.name_resolution import name_book, resolve_start
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
from worldmock.state.decisions.day_session import DaySessionState
from worldmock.state.interaction.requests import StartMainActivityRequest


def _day_session(wakeup) -> DaySessionState:
    """@brief 构造最小日会话状态。"""
    return DaySessionState(
        local_date=wakeup.local_date,
        system_prompt='{"layer":"系统层"}',
        tools_digest=life_tool_schema_sha256(),
        messages=(
            {"role": "system", "content": '{"layer":"系统层"}'},
            {"role": "user", "content": "[]"},
        ),
    )


def test_top_level_gate_reflects_slots_sleep_transit_and_pending_conclusions(engine_factory):
    """工具层顶层 gate 准确反映主副槽位占用、睡眠、在途与待收尾活动，且不阻塞开始行固有可用性。"""
    engine = engine_factory()
    stage_day_plan(engine)
    obs0 = engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]
    assert obs0["tool_layer"]["gate"] == {
        "main_slot_busy": False,
        "side_slot_busy": False,
        "sleeping": False,
        "in_transit": False,
        "pending_conclusions": [],
    }
    # 开启睡眠（占主槽位且 sleeping=True），工具行固有条件仍反映地点可到达与否
    act(engine, "sleep", sleep(60))
    obs_sleep = engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]
    assert obs_sleep["tool_layer"]["gate"]["main_slot_busy"] is True
    assert obs_sleep["tool_layer"]["gate"]["sleeping"] is True
    office_target = next(t for t in obs_sleep["tool_layer"]["targets"] if t["name"] == "公司")
    assert any(action["available"] is True for action in office_target["actions"])
    # 推进至睡眠结束待收尾
    assert advance_until_ready(engine)
    obs_wait = engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]
    assert obs_wait["tool_layer"]["gate"]["pending_conclusions"]
    assert obs_wait["tool_layer"]["gate"]["sleeping"] is False
    # 另起引擎测在途与副槽位
    eng2 = engine_factory()
    stage_day_plan(eng2)
    act_group(eng2, "pair", travel("公司"), use_app("哔哩哔哩"))
    obs_transit = eng2.observations.build_wakeup_observation(eng2.context.state.agents[1])[0]
    assert obs_transit["tool_layer"]["gate"]["main_slot_busy"] is True
    assert obs_transit["tool_layer"]["gate"]["side_slot_busy"] is True
    assert obs_transit["tool_layer"]["gate"]["in_transit"] is True


def test_core_resident_tool_layer_excludes_static_products_and_non_anchor_places(engine_factory):
    """核心常驻工具层仅保留当前点/家/工作地/驿站/枢纽、合格应用与已载入目标；未常驻对象仍可经 catalog_bindings 直呼。"""
    engine = engine_factory()
    obs = engine.observations.build_wakeup_observation(engine.context.state.agents[1])[0]
    targets = obs["tool_layer"]["targets"]
    kinds = {row["kind"] for row in targets}
    assert "product" not in kinds
    loc_names = {row["name"] for row in targets if row["kind"] == "location"}
    assert {"家", "公司", "快递驿站"} <= loc_names
    assert "四季便利" not in loc_names
    # 未常驻的四季便利仍存在于 catalog_bindings，可直接按完整名称调用解析
    book = name_book(obs)
    resolved = resolve_start(
        book, "start_main_activity",
        StartMainActivityRequest(action="travel", target="四季便利", detail="步行"),
    )
    assert not resolved.failures and resolved.rows[0]["object"] == "四季便利"
    # 传入 loaded_target_keys 后，相应地点与商品进入 tool_layer
    obs_loaded = engine.observations.build_wakeup_observation(
        engine.context.state.agents[1],
        loaded_target_keys=(("location", "store"), ("product", "lemon-tea")),
    )[0]
    loaded_names = {(r["kind"], r["name"]) for r in obs_loaded["tool_layer"]["targets"]}
    assert ("location", "四季便利") in loaded_names
    assert ("product", "柠檬茶") in loaded_names


def test_lookup_targets_is_zero_footprint_paginated_and_blacklist_clean(engine_factory):
    """lookup_targets 零耗时零事件足迹、按类与关键词分页排序、无黑名单键且写入 loaded_target_keys。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before_time = engine.context.state.current_time
    before_req_count = len(events(engine, "action_requested"))
    before_res_count = len(events(engine, "action_result"))
    [wakeup] = engine.prepare_barrier((1,))
    sandbox = engine.open_sandbox(wakeup)

    # 分页查询地点
    page1 = sandbox.lookup_targets({"kind": "location", "keyword": None, "limit": 2, "offset": 0})
    assert page1["ok"] is True
    res1 = page1["result"]
    assert res1["loaded_count"] == len(res1["targets"]) == 2
    assert res1["total"] >= 3 and res1["has_more"] is True and res1["next_offset"] == 2
    enforce_field_blacklist(page1)

    # 关键词大小写不敏感匹配商品并加载至工具层
    prod = sandbox.lookup_targets({"kind": "product", "keyword": "柠檬", "limit": 10, "offset": 0})
    assert prod["ok"] is True
    assert any(item["name"] == "柠檬茶" and item["kind"] == "product" for item in prod["result"]["targets"])
    enforce_field_blacklist(prod)

    # kind=None 按 location -> app -> product 顺序合并
    all_res = sandbox.lookup_targets({"kind": None, "keyword": None, "limit": 30, "offset": 0})
    kinds_order = [item["kind"] for item in all_res["result"]["targets"]]
    assert kinds_order == sorted(kinds_order, key=("location", "app", "product").index)

    # 零耗时零世界副作用并随 finish_wakeup 写入 day_session.loaded_target_keys
    assert sandbox._state.current_time == before_time
    outcome = sandbox.finish_wakeup(None, _day_session(wakeup))
    assert ("product", "lemon-tea") in outcome.day_session.loaded_target_keys
    assert any(
        t["kind"] == "product" and t["name"] == "柠檬茶"
        for t in outcome.day_session.last_observed_layers["tool_layer"]["targets"]
    )
    engine.commit_barrier([outcome])
    assert engine.context.state.current_time == before_time
    assert len(events(engine, "action_requested")) == before_req_count
    assert len(events(engine, "action_result")) == before_res_count


def test_observation_builder_layer_changes_outputs_fine_grained_chinese_diffs():
    """ObservationBuilder.layer_changes 仅输出变化层的精细子键差量，并对 group_deltas 显式紧凑化。"""
    before = {
        "environment_layer": {
            "layer": "环境层",
            "observed_at": "2026-01-05T08:00:00+08:00",
            "body": {"appetite": "不饿", "sleepiness": "清醒", "relaxation": "不闷", "social": "不想找人"},
            "location": {"session_kind": "stay", "name": "家"},
            "weather": "晴",
            "duty": None,
            "tomorrow": {"date": "2026-01-06", "weekday": "星期二", "day": "工作日"},
        },
        "memory_layer": {
            "layer": "记忆层",
            "可查": {"最早日期": "2026-01-05"},
            "近三天": {},
            "今天": [{"时刻": "08:00", "类别": "生活", "核验": ["开始测试休息"]}],
        },
        "todo_layer": {
            "layer": "待办层",
            "paused_activities": [],
            "errands": [],
            "orders": [],
            "day_plan": None,
        },
        "progress_layer": {
            "layer": "进度层",
            "session_started_at": "2026-01-05T08:00:00+08:00",
            "events": [{"type": "old"}],
            "rejected_today": [],
        },
        "tool_layer": {
            "layer": "工具层",
            "gate": {"main_slot_busy": False, "side_slot_busy": False, "sleeping": False,
                     "in_transit": False, "pending_conclusions": []},
            "targets": [
                {"name": "家", "kind": "location", "phase": None, "state": "全天营业",
                 "actions": [{"action": "sleep", "detail": None, "available": True, "reason": None,
                              "price": None, "duration": None, "quantity": None, "note": None}],
                 "tools": []},
                {"name": "旧目标", "kind": "product", "phase": None, "state": None, "actions": [], "tools": []},
            ],
            "callable_index": {"goods": [], "prefer_apps": []},
        },
    }
    after = deepcopy(before)
    after["environment_layer"]["observed_at"] = "2026-01-05T08:30:00+08:00"
    after["environment_layer"]["body"]["sleepiness"] = "有点乏"
    after["memory_layer"]["今天"].append({"时刻": "08:30", "类别": "生活", "核验": ["测试休息 08:00–08:30"]})
    after["progress_layer"]["events"].append({"type": "new"})
    after["tool_layer"]["gate"]["main_slot_busy"] = True
    after["tool_layer"]["targets"] = [
        before["tool_layer"]["targets"][0],
        {"name": "新目标", "kind": "product", "phase": None, "state": None,
         "actions": [{"action": "purchase", "detail": None, "available": True, "reason": None,
                      "price": "12 元", "duration": "1–5 分钟", "quantity": "1 件", "note": None}],
         "tools": []},
    ]
    diff = ObservationBuilder.layer_changes(before, after)
    assert diff["环境层"] == {"observed_at": "2026-01-05T08:30:00+08:00", "body": {"sleepiness": "有点乏"}}
    assert diff["记忆层"] == {"今天_append": [{"时刻": "08:30", "类别": "生活", "核验": ["测试休息 08:00–08:30"]}]}
    assert "待办层" not in diff
    assert diff["进度层"] == {"events_append": [{"type": "new"}]}
    assert diff["工具层"]["gate"]["main_slot_busy"] is True
    assert diff["工具层"]["group_deltas"] == {
        "added": [{"name": "新目标", "kind": "product", "actions": [
            {"action": "purchase", "available": True, "price": "12 元", "duration": "1–5 分钟", "quantity": "1 件"},
        ]}],
        "removed": [{"kind": "product", "name": "旧目标"}],
    }
    # 同日尾部覆写输出 今天_tail
    tail_after = deepcopy(before)
    tail_after["memory_layer"]["今天"][0] = {"时刻": "08:00", "类别": "饮食", "核验": ["改写尾部"]}
    tail_after["memory_layer"]["今天"].insert(0, {"时刻": "07:00", "类别": "生活", "核验": ["早间"]})
    before_two = deepcopy(before)
    before_two["memory_layer"]["今天"] = [
        {"时刻": "07:00", "类别": "生活", "核验": ["早间"]},
        {"时刻": "08:00", "类别": "生活", "核验": ["旧尾部"]},
    ]
    tail_diff = ObservationBuilder.layer_changes(before_two, tail_after)
    assert tail_diff["记忆层"] == {
        "今天_tail": {"from_index": 1, "entries": [{"时刻": "08:00", "类别": "饮食", "核验": ["改写尾部"]}]},
    }
    assert ObservationBuilder.layer_changes(before, before) == {}
