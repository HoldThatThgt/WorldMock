"""跨城门户、非直达枢纽指引、限定边窗口与状态扣减、跨城禁提前结束、分城天气、酒店日切与同城睡眠引导。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import (
    advance_next,
    conclude_pending,
    conclude_request,
    sleep,
    tool_targets,
    travel,
    travel_rows,
    target_tools,
)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity, observe_only, reach
from worldmock.exchange_engine.definitions.constants import WEATHER_FACTORS
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.observation.wording.content_prerequisite import time_phrase
from worldmock.simulation_engine.scheduling.clock.weather import WEATHER_LABELS, current_weather


def _weather_for(seed: int, city_id: str, day: str) -> str:
    """@brief 按生产唯一随机键 weather:{city_id}:{day} 计算指定城市当日天气。"""
    rstate = hashlib.sha256(f"random:{seed}".encode("utf-8")).hexdigest()
    return random_stream(rstate, f"weather:{city_id}:{day}").choice(tuple(WEATHER_FACTORS))


def _intercity_seed(day: str = "2026-01-05") -> int:
    """@brief 查找使 test-city 为晴、city-b 为大雨、且两城系数存在严格差别的确定性种子。"""
    return next(
        seed for seed in range(1, 500)
        if _weather_for(seed, "test-city", day) == "sunny" and _weather_for(seed, "city-b", day) == "heavy_rain"
    )


def _all_days(intervals: list[list[str]]) -> dict:
    """@brief 构造周一至周日同开放时段周历表。"""
    return {"weekly": {day: intervals for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}


def _multicity_environment(root, *, ticket_initial: int = 1, hotel_initial: int = 2,
                           with_b_home: bool = True, edge_opens: dict | None = None):
    """@brief 构建含 test-city、city-b、city-c、city-d 四城及限定边、酒店与车票的隔离环境副本。"""
    locs = catalog_rows(root, "location.jsonl")
    locs.extend([
        {"app_ids": [], "city_id": "test-city", "kind": "park", "location_id": "station-a", "name": "甲城北站"},
        {"app_ids": [], "city_id": "city-b", "kind": "park", "location_id": "hub-b", "name": "乙城中心站"},
        {"app_ids": [], "city_id": "city-b", "kind": "place", "location_id": "b-cafe", "name": "乙城小馆"},
        {"app_ids": [], "city_id": "city-b", "kind": "hotel", "location_id": "b-hotel-1",
         "name": "乙城云栖酒店", "sleep_consumes_state": "hotel-night", "sleepable": True},
        {"app_ids": [], "city_id": "city-b", "kind": "hotel", "location_id": "b-hotel-2",
         "name": "乙城远郊酒店", "sleep_consumes_state": "hotel-night", "sleepable": True},
        {"app_ids": [], "city_id": "city-b", "kind": "place", "location_id": "b-shuttle-dest", "name": "乙城空港"},
        {"app_ids": [], "city_id": "city-c", "kind": "park", "location_id": "hub-c", "name": "丙城东站"},
        {"app_ids": [], "city_id": "city-d", "kind": "park", "location_id": "z-hub-d", "name": "丁城南站"},
    ])
    if with_b_home:
        locs.append({"app_ids": [], "city_id": "city-b", "is_home": True, "kind": "home",
                     "location_id": "b-home", "name": "乙城寓所", "sleepable": True})
    write_catalog_rows(root, "location.jsonl", sorted(locs, key=lambda r: r["location_id"]))

    methods = catalog_rows(root, "travel-method.jsonl")
    methods.extend([
        {"city_wide": False, "exertion": "light", "name": "高铁", "price_per_km_fen": 0,
         "speed_meters_per_second": 50, "start_price_fen": 10000, "travel_method_id": "hsr"},
        {"city_wide": False, "exertion": "light", "name": "摆渡专车", "price_per_km_fen": 0,
         "speed_meters_per_second": 10, "start_price_fen": 500, "travel_method_id": "shuttle"},
    ])
    write_catalog_rows(root, "travel-method.jsonl", sorted(methods, key=lambda r: r["travel_method_id"]))

    apps = catalog_rows(root, "app-catalog.jsonl")
    amap = next(r for r in apps if r["app_id"] == "amap")
    amap["travel_method_ids"] = sorted([*amap["travel_method_ids"], "hsr", "shuttle"])
    write_catalog_rows(root, "app-catalog.jsonl", apps)

    goods = catalog_rows(root, "merchant-catalog.jsonl")
    goods.extend([
        {"app_ids": ["bilibili"], "category": "goods", "goods_id": "hotel-voucher",
         "instant": True, "name": "乙城酒店房晚", "price_fen": 30000},
        {"app_ids": ["bilibili"], "category": "goods", "goods_id": "rail-pass",
         "instant": True, "name": "高铁次票", "price_fen": 5000},
    ])
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(goods, key=lambda r: r["goods_id"]))

    callbacks = [
        {"cap": 5, "gain_on_goods": ["hotel-voucher"], "initial": hotel_initial,
         "reset_on_goods": [], "state": "hotel-night"},
        {"cap": 5, "gain_on_goods": ["rail-pass"], "initial": ticket_initial,
         "reset_on_goods": [], "state": "rail-ticket"},
        {"effect": {"delta": -1, "state": "hotel-night", "type": "state_delta"},
         "rule_id": "r-rail-empty-tax",
         "trigger": {"below": 1, "class": "state", "key": "rail-ticket"}},
    ]
    write_catalog_rows(root, "callbacks.jsonl", callbacks)

    edge_ab = {"a": "hub-b", "b": "station-a", "consumes_state": "rail-ticket",
               "length_m": 100000, "travel_method_ids": ["driving", "hsr"]}
    if edge_opens is not None:
        edge_ab["opens"] = edge_opens
    map_rows = [
        {"a": "b-shuttle-dest", "b": "hub-b", "length_m": 1500, "travel_method_ids": ["shuttle"]},
        {"a": "hub-b", "b": "hub-c", "length_m": 50000, "travel_method_ids": ["hsr"]},
        edge_ab,
        {"a": "station-a", "b": "z-hub-d", "length_m": 80000, "travel_method_ids": ["hsr"]},
    ]
    write_catalog_rows(root, "map.jsonl", sorted(map_rows, key=lambda r: (r["a"], r["b"])))
    return root


def _start_at(place_name: str, *, seed: int = 7, feedback: int = 60, hours: int = 48):
    """@brief 返回在指定起始地点、指定反馈周期启动的 mutate 回调。"""
    def mutate(persona, config):
        persona["initial_location"] = place_name
        config["seed"] = seed
        config["activity_feedback_seconds"] = feedback
        config["stop_time"] = config["start_time"] + timedelta(hours=hours)
    return mutate


def test_tool_layer_city_isolation_portal_suffix_and_city_wide_false(engine_factory, tmp_path):
    """@brief 工具层隔离非直达异地地点、追加本城枢纽城际可达后缀、并在同城与跨城严格隔离 city_wide=False 方式。"""
    root = _multicity_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家"))
    obs_home = observe_only(engine, "obs-home")
    targets_home = tool_targets(obs_home)
    assert "甲城北站" in targets_home
    assert "乙城中心站" not in targets_home and "丙城东站" not in targets_home
    assert targets_home["甲城北站"]["state"] == "全天营业；城际可达：乙城中心站（驾车、高铁）、丁城南站（高铁）"

    # 走到甲城北站：直接相连的乙城中心站与丁城南站可见，但非直达的乙城小馆与丙城东站仍隐藏，且异地枢纽不写城际后缀。
    _, res = act(engine, "to-station", travel("甲城北站", "驾车"))
    assert res.status == "in_progress"
    finish_activity(engine, res, "arrived-station")
    obs_station = observe_only(engine, "obs-station")
    targets_st = tool_targets(obs_station)
    assert "乙城中心站" in targets_st and "丁城南站" in targets_st
    assert "乙城小馆" not in targets_st and "丙城东站" not in targets_st
    assert targets_st["乙城中心站"]["state"] == "全天营业"
    assert set(travel_rows(obs_station, "乙城中心站")) == {"驾车", "高铁"}

    # 在 city-b 的乙城小馆检验 city_wide=False 的摆渡专车：未在直连两端时隐藏且拒载，移至乙城中心站后可用。
    engine_b = engine_factory(environment_root=root, mutate=_start_at("乙城小馆"))
    obs_b = observe_only(engine_b, "obs-b-cafe")
    assert "摆渡专车" not in travel_rows(obs_b, "乙城空港")
    assert "乙城云栖酒店" in tool_targets(obs_b) and "家" not in tool_targets(obs_b)

    _, refused = act(engine_b, "bad-shuttle", travel("乙城空港", "摆渡专车"))
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PRECONDITION_UNMET"

    _, to_hub = act(engine_b, "walk-hub", travel("乙城中心站", "步行"))
    finish_activity(engine_b, to_hub, "reach-hub")
    obs_hub = observe_only(engine_b, "obs-hub")
    assert "摆渡专车" in travel_rows(obs_hub, "乙城空港")
    _, ok_shuttle = act(engine_b, "take-shuttle", travel("乙城空港", "摆渡专车"))
    assert ok_shuttle.status == "in_progress"


def test_non_direct_intercity_refusal_uses_progressive_hub_guidance(engine_factory, tmp_path):
    """@brief 对非直达异地地点发起出行按渐进枢纽返回 Travel to <hub> first.，方式不支持时指明允许方式。"""
    root = _multicity_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家"))
    _, r1 = act(engine, "direct-c", travel("丙城东站", "高铁"))
    assert r1.status == "rejected" and r1.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert r1.data["next_step"] == "Travel to 甲城北站 first."
    assert r1.data["unmet"] == ["travel_route"]

    _, trip = act(engine, "to-a", travel("甲城北站", "驾车"))
    finish_activity(engine, trip, "at-a")
    _, r2 = act(engine, "direct-c-from-a", travel("丙城东站", "高铁"))
    assert r2.status == "rejected" and r2.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert r2.data["next_step"] == "Travel to 乙城中心站 first."

    _, r_mode = act(engine, "bus-to-b", travel("乙城中心站", "公共交通"))
    assert r_mode.status == "rejected" and r_mode.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert r_mode.data["next_step"] == "Travel using 驾车 or 高铁."


def test_gated_edge_opens_and_consumes_state_with_travel_callback_cause(engine_factory, tmp_path):
    """@brief 限定声明边按 opens 窗口、ticket 扣减与 ConsumeOnStartResult(content_id=None) 执行，拒载与工具一致。"""
    opens = _all_days([["10:00", "12:00"]])
    root = _multicity_environment(
        copy_environment(tmp_path / "env"), ticket_initial=0, hotel_initial=2, edge_opens=opens,
    )
    engine = engine_factory(environment_root=root, mutate=_start_at("甲城北站"))

    # start_time 08:00：处于 10:00-12:00 发车窗外，先报 LOCATION_CLOSED。
    obs = observe_only(engine, "obs-early")
    row_early = travel_rows(obs, "乙城中心站")["高铁"]
    assert row_early["available"] is False
    expected_phrase = time_phrase(engine.context.catalog.graph.declared_edge("hub-b", "station-a").opens,
                                  datetime.fromtimestamp(engine.context.state.current_time,
                                                         ZoneInfo(engine.context.config.timezone)).date())
    assert expected_phrase in row_early["reason"]

    _, closed = act(engine, "early-train", travel("乙城中心站", "高铁"))
    assert closed.status == "rejected" and closed.error_code == "LOCATION_CLOSED"
    assert closed.data["reason"] == f"高铁 from 甲城北站 to 乙城中心站 is {expected_phrase}."

    # 推进至 10:00 窗口内：现在因缺少 rail-ticket 报 ACTIVITY_PRECONDITION_UNMET 并含补票商品名。
    ten_am = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
    stage_moment(engine, ten_am, "station-a")
    obs_open = observe_only(engine, "obs-no-ticket")
    row_no_ticket = travel_rows(obs_open, "乙城中心站")["高铁"]
    assert row_no_ticket["available"] is False
    assert row_no_ticket["reason"] == "高铁 to 乙城中心站 is not available now; receiving 高铁次票 makes it available."

    _, no_ticket = act(engine, "no-ticket", travel("乙城中心站", "高铁"))
    assert no_ticket.status == "rejected" and no_ticket.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert no_ticket.data["reason"] == row_no_ticket["reason"]

    # 购买即时高铁次票（rail-pass -> rail-ticket 0 -> 1）后再乘高铁出发，验证扣减与 ConsumeOnStart(content_id=None) 及阈值级联。
    from tests.life.conftest import purchase

    _, buy = act(engine, "buy-pass", purchase("高铁次票"))
    finish_activity(engine, buy, "pass-settled")
    assert engine.context.state.agents[1].callback_states["rail-ticket"] == 1

    _, depart = act(engine, "board-hsr", travel("乙城中心站", "高铁"))
    assert depart.status == "in_progress"
    assert engine.context.state.agents[1].callback_states["rail-ticket"] == 0
    consume_events = [e for e in events(engine, "environment_event")
                      if e.data.get("type") == "consume_on_start" and e.data.get("state") == "rail-ticket"]
    assert len(consume_events) == 1
    ev = consume_events[0]
    assert ev.data["content_id"] is None and ev.data["before"] == 1 and ev.data["after"] == 0
    accepted_ev = next(e for e in events(engine, "action_result") if e.action_id == depart.action_id)
    assert ev.caused_by == [accepted_ev.event_id]
    # CallbackCause("action", "travel", ...) 支撑 r-rail-empty-tax 在同一提交扣减 hotel-night 2 -> 1。
    assert engine.context.state.agents[1].callback_states["hotel-night"] == 1


def test_intercity_conclude_activity_forbidden_while_intracity_allowed(engine_factory, tmp_path):
    """@brief 同城出行途可提前结束退回起点；跨城出行途工具层与控制请求均禁止提前结束。"""
    root = _multicity_environment(copy_environment(tmp_path / "env"))
    engine = engine_factory(environment_root=root, mutate=_start_at("家", feedback=60))

    # 同城步行中途可 conclude_activity
    _, intra = act(engine, "walk-cafe", travel("三杯咖啡", "步行"))
    intra_name = engine.context.state.activities[intra.activity_id].name
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    obs_i = observe_only(engine, "chk-intra")
    assert target_tools(obs_i, intra_name)["conclude_activity"]["available"] is True
    _, ended_intra = act(engine, "end-intra", conclude_request(intra_name), "conclude_activity")
    assert ended_intra.status == "completed"
    assert engine.context.state.agents[1].location_id == "home"

    # 前往甲城北站后发起跨城赴乙城中心站
    _, to_st = act(engine, "to-st", travel("甲城北站", "驾车"))
    finish_activity(engine, to_st, "at-st")
    _, inter = act(engine, "to-b", travel("乙城中心站", "高铁"))
    inter_name = engine.context.state.activities[inter.activity_id].name
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    assert engine.context.state.activities[inter.activity_id].status == "active"

    obs_c = observe_only(engine, "chk-inter")
    conclude_row = target_tools(obs_c, inter_name)["conclude_activity"]
    assert conclude_row["available"] is False
    expected_reason = f"{inter_name} is an active intercity trip and cannot be concluded before arrival."
    assert conclude_row["reason"] == expected_reason

    _, blocked = act(engine, "try-conclude-inter", conclude_request(inter_name), "conclude_activity")
    assert blocked.status == "rejected" and blocked.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert blocked.data["reason"] == expected_reason
    assert blocked.data["next_step"] == "Wait until arrival at 乙城中心站."
    assert engine.context.state.activities[inter.activity_id].status == "active"

    finish_activity(engine, inter, "arrived-b")
    assert engine.context.state.agents[1].location_id == "hub-b"


def test_intercity_weather_uses_min_factor_freezes_and_switches_environment(engine_factory, tmp_path):
    """@brief 跨城按起终两城较差天气系数冻结行程，零点按城市升序发天气事件，到站切换环境层当前城天气。"""
    seed = _intercity_seed("2026-01-05")
    root = _multicity_environment(copy_environment(tmp_path / "env"))

    def late_start(persona, config):
        persona["initial_location"] = "甲城北站"
        config["seed"] = seed
        config["activity_feedback_seconds"] = 300
        config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
        config["stop_time"] = datetime.fromisoformat("2026-01-06T03:00:00+08:00")

    engine = engine_factory(environment_root=root, mutate=late_start)
    t2340 = int(datetime.fromisoformat("2026-01-05T23:40:00+08:00").timestamp())
    stage_moment(engine, t2340, "station-a")
    state0 = engine.context.state
    assert current_weather(state0, "test-city") == "sunny"
    assert current_weather(state0, "city-b") == "heavy_rain"

    obs0 = observe_only(engine, "obs-before-inter")
    assert obs0["environment_layer"]["weather"] == WEATHER_LABELS["sunny"]

    _, trip_res = act(engine, "night-intercity", travel("乙城中心站", "高铁"))
    assert trip_res.status == "in_progress"
    act_obj = engine.context.state.activities[trip_res.activity_id]
    # 100,000 m / (50 m/s * min(1.0, 0.7)) = 2858 s，跨越 2026-01-06 00:00:00 零点仍沿用 0.7 冻结行程。
    assert act_obj.data["weather_factor"] == WEATHER_FACTORS["heavy_rain"]
    assert act_obj.planned_duration_seconds == 2858

    finish_activity(engine, trip_res, "arrive-city-b")
    weather_events = [e for e in events(engine, "environment_event")
                      if e.data.get("kind") == "weather" and e.data.get("local_date") == "2026-01-06"]
    assert [e.data["city_id"] for e in weather_events] == ["city-b", "city-c", "city-d", "test-city"]
    assert [e.data["scheduled_id"] for e in weather_events] == [
        f"weather-{cid}-2026-01-06" for cid in ("city-b", "city-c", "city-d", "test-city")]

    obs_after = observe_only(engine, "obs-city-b")
    assert obs_after["environment_layer"]["weather"] == WEATHER_LABELS[
        engine.context.state.weather_by_date["2026-01-06"]["city-b"]]


def test_hotel_lodging_stays_0600_daycut_reuse_nap_and_second_home(engine_factory, tmp_path):
    """@brief 商业酒店遵循 06:00 日切算 12:00 退房、有效期内复睡免扣且跨退房不中断，次城自有住处零状态免费睡。"""
    root = _multicity_environment(copy_environment(tmp_path / "env"), hotel_initial=2)

    def early_hotel(persona, config):
        persona["initial_location"] = "乙城云栖酒店"
        config["activity_feedback_seconds"] = 60
        config["start_time"] = datetime.fromisoformat("2026-01-05T05:00:00+08:00")
        config["stop_time"] = datetime.fromisoformat("2026-01-06T21:00:00+08:00")

    engine = engine_factory(environment_root=root, mutate=early_hotel)
    # 05:30 入睡（< 06:00）：扣 hotel-night 2 -> 1，退房纪元为当日 2026-01-05 12:00:00。
    t0530 = int(datetime.fromisoformat("2026-01-05T05:30:00+08:00").timestamp())
    stage_moment(engine, t0530, "b-hotel-1")
    _, s1 = act(engine, "sleep-early", sleep(30))
    assert s1.status == "in_progress"
    assert engine.context.state.agents[1].callback_states["hotel-night"] == 1
    checkout_same_day = int(datetime.fromisoformat("2026-01-05T12:00:00+08:00").timestamp())
    assert engine.context.state.agents[1].lodging_stays["b-hotel-1"] == checkout_same_day
    hotel_consumes = [e for e in events(engine, "environment_event")
                      if e.data.get("type") == "consume_on_start" and e.data.get("state") == "hotel-night"]
    assert len(hotel_consumes) == 1 and hotel_consumes[0].data["content_id"] is None
    finish_activity(engine, s1, "wake-early")

    # 11:00 午休 90 分钟（入住有效期内）：免扣状态、且跨越 12:00:00 不中断至 12:30 及以后自然醒来。
    t1100 = int(datetime.fromisoformat("2026-01-05T11:00:00+08:00").timestamp())
    assert engine.context.state.current_time <= t1100
    stage_moment(engine, t1100, "b-hotel-1")
    _, s2 = act(engine, "nap-free", sleep(90))
    assert s2.status == "in_progress"
    assert engine.context.state.agents[1].callback_states["hotel-night"] == 1
    assert engine.context.state.activities[s2.activity_id].planned_end_at > checkout_same_day
    finish_activity(engine, s2, "wake-noon")
    assert engine.context.state.current_time >= checkout_same_day

    # 退房过期后于 13:00 起（>= 06:00）新开睡眠：扣 1 -> 0，退房设至次日 2026-01-06 12:00:00。
    t1300 = max(
        int(datetime.fromisoformat("2026-01-05T13:00:00+08:00").timestamp()),
        engine.context.state.current_time,
    )
    stage_moment(engine, t1300, "b-hotel-1")
    _, s3 = act(engine, "sleep-pm", sleep(30))
    assert s3.status == "in_progress"
    assert engine.context.state.agents[1].callback_states["hotel-night"] == 0
    checkout_next_day = int(datetime.fromisoformat("2026-01-06T12:00:00+08:00").timestamp())
    assert engine.context.state.agents[1].lodging_stays["b-hotel-1"] == checkout_next_day
    finish_activity(engine, s3, "wake-pm")

    # 在当晚 23:55 入睡跨越零点与次日 12:00 退房窗口：有效期内 0 房晚依然允许开睡；醒来后若未过 12:00 再睡一段跨过 12:00。
    t2355 = max(
        int(datetime.fromisoformat("2026-01-05T23:55:00+08:00").timestamp()),
        engine.context.state.current_time,
    )
    if t2355 < checkout_next_day:
        stage_moment(engine, t2355, "b-hotel-1")
        _, s4 = act(engine, "sleep-overnight", sleep(720))
        assert s4.status == "in_progress"
        assert engine.context.state.agents[1].callback_states["hotel-night"] == 0
        finish_activity(engine, s4, "wake-day2")
    if engine.context.state.current_time < checkout_next_day:
        _, s5 = act(engine, "sleep-before-noon", sleep(30))
        assert s5.status == "in_progress"
        finish_activity(engine, s5, "wake-after-noon")

    # 次日 2026-01-06 12:00:00 及之后有效期已过且状态为 0：再睡被拒。
    assert engine.context.state.current_time >= checkout_next_day
    _, expired = act(engine, "sleep-expired", sleep(30))
    assert expired.status == "rejected" and expired.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert expired.data["reason"] == (
        "Sleeping at 乙城云栖酒店 is not available now; receiving 乙城酒店房晚 makes it available."
    )

    # 前往次城自有住处乙城寓所（is_home=True, 无 sleep_consumes_state）：0 房晚状态下免费入睡。
    _, to_home_b = act(engine, "walk-home-b", travel("乙城寓所", "步行"))
    finish_activity(engine, to_home_b, "at-home-b")
    _, s_home = act(engine, "sleep-home-b", sleep(20))
    assert s_home.status == "in_progress"
    assert engine.context.state.agents[1].callback_states["hotel-night"] == 0


def test_sleep_guidance_follows_same_city_five_tier_priority_and_cross_city_fallback(engine_factory, tmp_path):
    """@brief 非可睡地点睡眠拒载按本城有效房晚>本城家>本城可用酒店>本城需买房晚酒店>跨城最近(家先于酒店)五级引导。"""
    # 场景 1 & 2：乙城同时存在有效酒店与自有住处时，有效期内优先引向酒店，无有效酒店时引向本城家。
    root_with_home = _multicity_environment(copy_environment(tmp_path / "with-home"),
                                            hotel_initial=1, with_b_home=True)
    engine_1 = engine_factory(environment_root=root_with_home, mutate=_start_at("乙城小馆"))
    _, tier2 = act(engine_1, "tier2-home", sleep(20))
    assert tier2.status == "rejected" and tier2.data["next_step"] == "Go to 乙城寓所."

    engine_hotel = engine_factory(environment_root=root_with_home, mutate=_start_at("乙城云栖酒店"))
    _, checkin = act(engine_hotel, "checkin", sleep(20))
    finish_activity(engine_hotel, checkin, "out-checkin")
    _, walk_cafe = act(engine_hotel, "to-cafe-b", travel("乙城小馆", "步行"))
    finish_activity(engine_hotel, walk_cafe, "at-cafe-b")
    _, tier1 = act(engine_hotel, "tier1-checked", sleep(20))
    assert tier1.status == "rejected" and tier1.data["next_step"] == "Go to 乙城云栖酒店."

    # 场景 3 & 4：乙城无自有住处时，有房晚引导最近可用酒店，无房晚引导酒店并提示房晚商品。
    root_no_home = _multicity_environment(copy_environment(tmp_path / "no-home"),
                                          hotel_initial=1, with_b_home=False)
    engine_3 = engine_factory(environment_root=root_no_home, mutate=_start_at("乙城小馆"))
    nearest_hotel = min(
        (engine_3.context.catalog.locations[k] for k in ("b-hotel-1", "b-hotel-2")),
        key=lambda loc: (engine_3.context.catalog.shortest_m("b-cafe", loc.location_id), loc.location_id),
    )
    _, tier3 = act(engine_3, "tier3-ready-hotel", sleep(20))
    assert tier3.status == "rejected" and tier3.data["next_step"] == f"Go to {nearest_hotel.display_name}."

    root_no_state = _multicity_environment(copy_environment(tmp_path / "no-state"),
                                           hotel_initial=0, with_b_home=False)
    engine_4 = engine_factory(environment_root=root_no_state, mutate=_start_at("乙城小馆"))
    _, tier4 = act(engine_4, "tier4-unready-hotel", sleep(20))
    assert tier4.status == "rejected"
    assert tier4.data["next_step"] == f"Go to {nearest_hotel.display_name}."
    assert "receiving 乙城酒店房晚 makes it available." in tier4.data["reason"]

    # 场景 5：单节点城 丙城东站 既无家又无酒店，跨城按家先于酒店（同距优先 test-city 的家）。
    engine_5 = engine_factory(environment_root=root_with_home, mutate=_start_at("丙城东站"))
    homes = [loc for loc in engine_5.context.catalog.locations.values() if loc.is_home and loc.sleepable]
    nearest_home = min(
        homes,
        key=lambda loc: (
            engine_5.context.catalog.graph.distance_meters("hub-c", loc.location_id, city_wide=False),
            loc.location_id,
        ),
    )
    _, tier5 = act(engine_5, "tier5-cross-city", sleep(20))
    assert tier5.status == "rejected" and tier5.data["next_step"] == f"Go to {nearest_home.display_name}."
