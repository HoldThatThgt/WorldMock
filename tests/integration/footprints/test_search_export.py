"""搜索是第五种活动：导出里一行 search 活动；指定 app 的搜索在 app 使用里恰一行、区间等于执行区间，未指定 app 的
搜索没有行且按规则归因 app_use；提前结束与截断的区间止于那一秒；做完同一秒接着购买时前台不重叠；不写前台事件、不是
下单尝试、没有已核验经历、不算内容接触；时间线里那一行写中文（docs/design/05-recording-engine.md「数字足迹」
「完整导出认证」）。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from tests.engine_fixture import engine_factory_at
from tests.footprints.recording import artifact_rows, export_completed
from tests.life.conftest import advance_next, conclude_request, do_at, purchase, report_request, search_world
from tests.life.environment_runtime import act, act_group, events
from worldmock.foundation.randomness import random_stream
from worldmock.simulation_engine.activities.lifecycle.activity_control import is_pending_conclusion
from worldmock.simulation_engine.activities.search.search_activity import (
    SEARCH_SECONDS_MAX,
    SEARCH_SECONDS_MIN,
    attribute_search_app,
)
from worldmock.simulation_engine.commerce.purchase.order_apps import order_app_ids, selling_app_ids
from worldmock.state.decisions.barrier import SearchSessionOutcome

#: 质量报告的顶层键：结构号 11 的完整键集。
QUALITY_KEYS = {
    "schema_version", "run_id", "source_group_id", "event_range", "run_outcome", "actions", "activities",
    "content_contact", "app_pushes", "conversion_efficiency", "decision_rhythm", "memory_references",
    "model_usage", "acceleration", "lineage", "day_plan_metrics", "errand_metrics", "conclusion_memories",
    "input_warnings", "timeline_labels", "name_rejections", "truncations",
}


def _hour(persona, config):
    """运行从 1 月 5 日 08:00 到 09:00；每 60 秒到一次重估间隔，搜索进行中人物就有决策机会。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=1)
    config["reconsideration_interval_seconds"] = 60


class _Driver:
    """逐边界推进并结算到期搜索：先收尾，再播报进行中的活动，都没有时在家做「测试闲聊」。"""

    def __init__(self, engine):
        """@brief 绑定真实引擎与动作标签计数。"""
        self.engine, self.count = engine, 0

    def act(self, request, tool=None):
        """以不重复的标签提交一项请求并断言受理。"""
        self.count += 1
        _, result = act(self.engine, f"step-{self.count}", request, tool)
        assert result.status in {"in_progress", "completed"}, (result.error_code, result.message)
        return result

    def until(self, predicate):
        """推进到某个决策机会上 predicate 成立为止；到期搜索先通过目录视图结算。"""
        while True:
            if self._settle_searches():
                continue
            if self.engine.scheduling_state().ready_agent_ids:
                if predicate(self.engine):
                    return
                self._default()
            else:
                advance_next(self.engine)

    def _settle_searches(self) -> bool:
        """若有到期搜索活动，经只读目录视图检索真实商品并提交完成。"""
        due = self.engine.due_searches()
        if not due:
            return False
        outcomes = []
        for task, view in due:
            found = view.search_goods({
                "keywords": [task.query], "category": None, "app": None, "store": None,
                "max_price_yuan": None, "open_only": False, "limit": 50, "offset": 0,
            })["items"]
            goods_ids = tuple(item["goods_id"] for item in found[:3])
            outcomes.append(SearchSessionOutcome(
                action_id=task.action_id, agent_id=task.agent_id, activity_id=task.activity_id,
                goods_ids=goods_ids, place_ids=(), summary=f"找到{len(goods_ids)}个结果",
            ))
        self.engine.complete_searches(outcomes)
        return True

    def _default(self):
        """缺省做法：收尾全部待收尾的活动；否则播报进行中的活动；都没有时在家做「测试闲聊」。"""
        activities = self.engine.context.state.activities.values()
        pending = [item.name for item in activities if item.status not in {"active", "paused"}
                   and item.concluded_at is None]
        active = [item.name for item in activities if item.status == "active"]
        if pending:
            self.act(conclude_request(*pending), "conclude_activity")
        elif active:
            self.act(report_request(*active), "report_status")
        else:
            self.act(do_at("家", "测试闲聊"))


def _slot_two_free(engine) -> bool:
    """没有待收尾的活动，第二槽位空着。"""
    return not any((item.status == "active" and "second" in item.resource_claims)
                   or (item.status not in {"active", "paused"} and item.concluded_at is None)
                   for item in engine.context.state.activities.values())


def _search_day(engine) -> dict:
    """一小时里的四次搜索：做着「测试休息」搜柠檬茶（写美团）并在做完的那一秒接着经美团购买；搜纸巾（没有 app）；
    搜零食（写美团）、下一次决策就提前结束；临近截止搜饮料（写美团），被截断。"""
    driver = _Driver(engine)
    _, (rest, with_app) = act_group(engine, "pair", do_at("家", "测试休息"), search_world("柠檬茶", prefer="美团"))
    assert (rest.status, with_app.status) == ("in_progress", "in_progress")
    driver.until(lambda e: e.context.state.activities[with_app.activity_id].status == "completed")
    bought = driver.act(purchase("柠檬茶", prefer="美团"))
    driver.until(_slot_two_free)
    tissue = driver.act(search_world("纸巾"))
    driver.until(lambda e: e.context.state.activities[tissue.activity_id].status == "completed")
    driver.until(_slot_two_free)
    stopped = driver.act(search_world("零食", prefer="美团"))
    driver.until(lambda e: e.context.state.current_time > e.context.state.activities[stopped.activity_id].started_at)
    stopped_name = engine.context.state.activities[stopped.activity_id].name
    conclude_names = [
        item.name for item in engine.context.state.activities.values()
        if item.agent_id == 1 and is_pending_conclusion(item) and item.name != stopped_name
    ]
    driver.act(conclude_request(*conclude_names, stopped_name), "conclude_activity")
    driver.until(lambda e: _slot_two_free(e) and 0 < e.context.state.end_time - e.context.state.current_time <= 120)
    cut = driver.act(search_world("饮料", prefer="美团"))
    return {"rest": rest.activity_id, "with_app": with_app.activity_id, "bought": bought.activity_id,
            "tissue": tissue.activity_id, "stopped": stopped.activity_id, "cut": cut.activity_id}


@pytest.fixture(scope="module")
def search_export(tmp_path_factory, frozen_inputs):
    """整份导出只跑一次。"""
    root = tmp_path_factory.mktemp("search-day")
    with engine_factory_at(root, frozen_inputs) as create:
        engine = create(mutate=_hour)
        ids = _search_day(engine)
        manifest, exported_events, rows = export_completed(engine, root / "export")
        recorded = events(engine)
        apps = order_app_ids(engine.context.catalog)
    return {"directory": root / "export", "ids": ids, "manifest": manifest, "events": exported_events,
            "rows": {row["activity_id"]: row for row in rows}, "recorded": recorded, "order_apps": apps}


def _uses(search_export, activity_id) -> list[dict]:
    """app-usage.jsonl 里这项活动的全部 app_use 行。"""
    return [row for row in artifact_rows(search_export["directory"], "app_usage")
            if row["fact_kind"] == "app_use" and row["activity_id"] == activity_id]


def test_search_is_exported_as_the_fifth_activity_type(search_export):
    """做着地点里的事时做完一次搜索：activities.jsonl 有一行 activity_type 为 search，占 second，执行区间恰一段、
    长度等于已做时长；并行的那件事另有一行占 first；导出清单结构号 9、认证通过。"""
    rows, ids = search_export["rows"], search_export["ids"]
    row = rows[ids["with_app"]]
    assert (row["activity_type"], row["status"], row["resource_claims"]) == ("search", "completed", ["second"])
    [segment] = row["segments"]
    assert segment["ended_at"] - segment["started_at"] == row["elapsed_seconds"]
    assert SEARCH_SECONDS_MIN <= row["elapsed_seconds"] <= SEARCH_SECONDS_MAX
    assert row["data"]["action"] == "search" and row["data"]["answer"]["goods_rows"]
    assert (rows[ids["rest"]]["activity_type"], rows[ids["rest"]]["resource_claims"]) == ("interact", ["first"])
    assert search_export["manifest"]["schema_version"] == 9
    assert sum(item["activity_type"] == "search" for item in rows.values()) == 4


def test_search_with_an_app_has_one_app_use_row_over_its_execution(search_export):
    """搜索「柠檬茶」、prefer 写「美团」：这项活动在 app-usage.jsonl 里恰一行 app_use，app_id 是 meituan，
    区间与状态等于活动行的。"""
    row = search_export["rows"][search_export["ids"]["with_app"]]
    uses = [(use["app_id"], use["status"], use["segments"]) for use in _uses(search_export, row["activity_id"])]
    assert uses == [("meituan", row["status"], row["segments"])]
    assert row["data"]["app_id"] == "meituan"


def test_completed_unscoped_search_attributes_app_id_and_emits_app_use_row(search_export):
    """未指定 prefer 的搜索「纸巾」：开始时 data.app_id 为空，完成后 data.app_id 与 answer.app_use 均写入确定性归因的应用，
    且 app-usage.jsonl 按该 app 记入一行 app_use。"""
    activity_id = search_export["ids"]["tissue"]
    row = search_export["rows"][activity_id]
    [accepted] = [event for event in search_export["recorded"] if event.event_type == "action_accepted"
                  and event.data.get("activity", {}).get("activity_id") == activity_id]
    assert accepted.data["activity"]["data"]["app_id"] is None
    assert accepted.data["activity"]["data"]["scoped"] is False
    assert row["activity_type"] == "search" and row["data"]["scoped"] is False
    assert row["data"]["app_id"] == row["data"]["answer"]["app_use"] == row["data"]["app_use"]
    assert row["data"]["app_id"] in search_export["order_apps"]
    uses = [(use["app_id"], use["status"], use["segments"]) for use in _uses(search_export, activity_id)]
    assert uses == [(row["data"]["app_id"], row["status"], row["segments"])]


def test_cancelled_unscoped_search_leaves_data_app_id_none_and_no_app_use_row(tmp_path, frozen_inputs):
    """未指定 prefer 且在完成前被取消的搜索：data.app_id 保持 None，app-usage.jsonl 没有这项活动的 app_use 行。"""
    with engine_factory_at(tmp_path, frozen_inputs) as create:
        engine = create(mutate=_hour)
        _, started = act(engine, "unscoped-cancel", search_world("纸巾"))
        advance_next(engine)
        name = engine.context.state.activities[started.activity_id].name
        _, concluded = act(engine, "cancel", conclude_request(name), "conclude_activity")
        assert concluded.status == "completed", concluded.error_code
        _, _, rows = export_completed(engine, tmp_path / "export")
    row = next(item for item in rows if item["activity_id"] == started.activity_id)
    assert row["status"] == "cancelled" and row["data"]["app_id"] is None
    assert [item for item in artifact_rows(tmp_path / "export", "app_usage")
            if item.get("activity_id") == started.activity_id] == []


def test_concluded_and_truncated_searches_keep_their_actual_interval(search_export):
    """提前结束的搜索：活动行与 app_use 行都是 cancelled，区间止于结束那一秒；跑到截止仍在进行的搜索是 truncated，
    区间止于截止时刻。"""
    rows, ids = search_export["rows"], search_export["ids"]
    [ended] = [event.data["end_time"] for event in search_export["recorded"] if event.event_type == "run_ended"]
    concluded = [event for event in search_export["recorded"] if event.event_type == "action_requested"
                 and event.data["tool_name"] == "conclude_activity"
                 and any(item["activity_name"] == rows[ids["stopped"]]["name"]
                         for item in event.data["request"]["items"])]
    for key, status, end in (("stopped", "cancelled", concluded[0].occurred_at), ("cut", "truncated", ended)):
        row = rows[ids[key]]
        assert (row["status"], row["ended_at"], row["segments"][-1]["ended_at"]) == (status, end, end)
        assert [(use["status"], use["segments"]) for use in _uses(search_export, ids[key])] == [
            (status, row["segments"])]
    assert "answer" not in rows[ids["stopped"]]["data"] and "answer" not in rows[ids["cut"]]["data"]


def test_search_then_purchase_in_the_same_second_keeps_one_app_in_the_foreground(search_export):
    """搜索做完的那一秒接着经美团购买：搜索区间的终点等于购买第一段的起点；app-usage.jsonl 里搜索那一行排在购买的
    各行之前；导出认证通过。"""
    ids = search_export["ids"]
    searched = search_export["rows"][ids["with_app"]]
    usage = [row for row in artifact_rows(search_export["directory"], "app_usage") if row["fact_kind"] == "app_use"]
    order = [row["activity_id"] for row in usage]
    bought = [row for row in usage if row["activity_id"] == ids["bought"]]
    assert bought and order.index(ids["with_app"]) < order.index(ids["bought"])
    first = min(segment["started_at"] for row in bought for segment in row["segments"])
    assert searched["segments"][-1]["ended_at"] == first


def test_search_leaves_no_foreground_event_order_attempt_experience_or_content_contact(search_export):
    """搜索不写 app_foreground，不是下单尝试，没有已核验经历，没有 content_consumed；quality.json 结构号 11。"""
    searches = {search_export["ids"][key] for key in ("with_app", "tissue", "stopped", "cut")}
    foreground = [row for row in search_export["events"] if row["data"].get("kind") == "app_foreground"
                  and row["data"].get("activity_id") in searches]
    assert foreground == []
    directory = search_export["directory"]
    assert not [row for row in artifact_rows(directory, "purchases") if row.get("activity_id") in searches]
    assert not [row for row in artifact_rows(directory, "verified_experiences") if row.get("activity_id") in searches]
    assert not [row for row in artifact_rows(directory, "app_usage")
                if row["fact_kind"] == "content_consumed" and row.get("activity_id") in searches]
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["schema_version"] == 11 and set(quality) == QUALITY_KEYS


def test_search_line_of_the_timeline_is_chinese(search_export):
    """导出的时间线里四项搜索各一行，「活动」一栏以「搜索」开头；全文不含英文 search 或 query；
    质量报告的英文种类名计数为 0。"""
    directory = search_export["directory"]
    text = "".join(path.read_text(encoding="utf-8") for path in sorted((directory / "timeline").glob("*.md")))
    rows = [line.split(" | ") for line in text.splitlines() if line.startswith("| ") and not line.startswith("| 起止")
            and not line.startswith("| ---")]
    assert len([cells for cells in rows if cells[1].startswith("搜索")]) == 4
    assert "search" not in text and "query" not in text
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["timeline_labels"]["english_kind_names"] == 0


def test_status_lists_a_running_search(engine_factory):
    """搜索进行中时 read_status 的 active_activities 里有一项 kind 为 search。"""
    from worldmock.runtime.monitoring.status import read_status

    engine = engine_factory()
    _, result = act(engine, "s", search_world("柠檬茶"))
    assert result.status == "in_progress"
    active = read_status(engine.context.store.run_dir)["active_activities"]
    assert [(item["activity_id"], item["kind"]) for item in active] == [(result.activity_id, "search")]


def test_search_duration_prefer_and_app_attribution(engine_factory):
    """校验搜索随机耗时流、prefer 仅匹配可下单 app 时落库 app_id、未指定时多数票与词典序平局归因及随机流。"""
    engine = engine_factory()
    catalog = engine.context.catalog
    apps_by_goods = {gid: selling_app_ids(catalog, (gid,)) for gid in catalog.goods}
    single_app = [(gid, apps[0]) for gid, apps in apps_by_goods.items() if len(apps) == 1]
    multi_app = [(gid, apps) for gid, apps in apps_by_goods.items() if len(apps) > 1]
    assert single_app and multi_app

    _, scoped = act(engine, "scoped", search_world("柠檬茶", prefer="美团"))
    scoped_act = engine.context.state.activities[scoped.activity_id]
    assert scoped_act.data["app_id"] == "meituan" and scoped_act.data["scoped"] is True

    unmatched_engine = engine_factory()
    _, unmatched = act(unmatched_engine, "unmatched", search_world("柠檬茶", prefer="不存在的应用"))
    unmatched_act = unmatched_engine.context.state.activities[unmatched.activity_id]
    assert unmatched_act.data["app_id"] is None and unmatched_act.data["scoped"] is False

    unscoped_engine = engine_factory()
    _, started = act(unscoped_engine, "unscoped", search_world("柠檬茶"))
    activity = unscoped_engine.context.state.activities[started.activity_id]
    expected_duration = random_stream(
        unscoped_engine.context.state.random_state, f"search-duration:{activity.origin_action_id}"
    ).randint(SEARCH_SECONDS_MIN, SEARCH_SECONDS_MAX)
    assert SEARCH_SECONDS_MIN <= activity.planned_duration_seconds <= SEARCH_SECONDS_MAX
    assert activity.planned_duration_seconds == expected_duration
    assert activity.data["app_id"] is None and activity.data["scoped"] is False

    gid_tie, tied_apps = multi_app[0]
    assert attribute_search_app(unscoped_engine.context.inputs, unscoped_engine.context.state.random_state,
                                activity, (gid_tie,)) == min(tied_apps)
    favored_gid, favored_app = next(((gid, aid) for gid, aid in single_app if aid in tied_apps), single_app[0])
    extra = (gid_tie, favored_gid) if favored_app in tied_apps else (favored_gid,)
    assert attribute_search_app(unscoped_engine.context.inputs, unscoped_engine.context.state.random_state,
                                activity, extra) == favored_app

    while not unscoped_engine.due_searches():
        advance_next(unscoped_engine)
    unscoped_engine.complete_searches([SearchSessionOutcome(
        action_id=activity.origin_action_id, agent_id=activity.agent_id, activity_id=activity.activity_id,
        goods_ids=(), place_ids=(), summary="无命中",
    )])
    done = unscoped_engine.context.state.activities[activity.activity_id]
    expected_fallback = random_stream(
        unscoped_engine.context.state.random_state, f"search-app:{activity.origin_action_id}"
    ).choice(order_app_ids(unscoped_engine.context.catalog))
    assert done.data["app_id"] == done.data["app_use"] == done.data["answer"]["app_use"] == expected_fallback
