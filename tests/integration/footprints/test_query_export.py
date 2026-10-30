"""查询是第五种活动：导出里一行 query 活动；带 app 的查询在 app 使用里恰一行、区间等于执行区间，没有 app 的查询
没有行；提前结束与截断的区间止于那一秒；做完同一秒接着购买时前台不重叠；不写前台事件、不是下单尝试、没有已核验经历、
不算内容接触；时间线里那一行写中文（docs/design/05-recording-engine.md「数字足迹」「完整导出认证」）。

一个真实引擎跑出全部情形后只导出、认证一次，各用例共用。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from tests.engine_fixture import engine_factory_at
from tests.footprints.recording import artifact_rows, export_completed
from tests.life.conftest import advance_next, conclude_request, do_at, purchase, query, report_request
from tests.life.environment_runtime import act, act_group, events

#: 质量报告的顶层键：结构号 9 的完整键集，查询不增不减。
QUALITY_KEYS = {"schema_version", "run_id", "source_group_id", "event_range", "run_outcome", "actions", "activities",
                "content_contact", "app_pushes", "conversion_efficiency", "decision_rhythm", "memory_references",
                "model_usage", "acceleration", "lineage", "day_plan_metrics", "errand_metrics", "conclusion_memories",
                "input_warnings", "timeline_labels", "name_rejections", "truncations"}


def _hour(persona, config):
    """运行从 1 月 5 日 08:00 到 09:00；每 60 秒到一次重估间隔，查询进行中人物就有决策机会。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=1)
    config["reconsideration_interval_seconds"] = 60


class _Driver:
    """逐边界推进并按缺省做法处理决策机会：先收尾，再播报进行中的活动，都没有时在家做「测试闲聊」。"""

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
        """推进到某个决策机会上 predicate 成立为止；之前的决策机会按缺省做法处理。"""
        while True:
            if self.engine.scheduling_state().ready_agent_ids:
                if predicate(self.engine):
                    return
                self._default()
            else:
                advance_next(self.engine)

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
    """没有待收尾的活动，槽位 2 空着。"""
    return not any((item.status == "active" and "second" in item.resource_claims)
                   or (item.status not in {"active", "paused"} and item.concluded_at is None)
                   for item in engine.context.state.activities.values())


def _query_day(engine) -> dict:
    """一小时里的五次查询：做着「测试休息」查柠檬茶（写美团）并在做完的那一秒接着经美团购买；查纸巾（没有 app）；
    查零食、下一次决策就提前结束；临近截止查饮料，被截断。"""
    driver = _Driver(engine)
    _, (rest, with_app) = act_group(engine, "pair", do_at("家", "测试休息"), query("柠檬茶", prefer="美团"))
    assert (rest.status, with_app.status) == ("in_progress", "in_progress")
    driver.until(lambda e: e.context.state.activities[with_app.activity_id].status == "completed")
    bought = driver.act(purchase("柠檬茶", prefer="美团"))
    driver.until(_slot_two_free)
    tissue = driver.act(query("纸巾"))
    driver.until(lambda e: e.context.state.activities[tissue.activity_id].status == "completed")
    driver.until(_slot_two_free)
    stopped = driver.act(query("零食"))
    driver.until(lambda e: e.context.state.current_time > e.context.state.activities[stopped.activity_id].started_at)
    driver.act(conclude_request(engine.context.state.activities[stopped.activity_id].name), "conclude_activity")
    driver.until(lambda e: _slot_two_free(e) and e.context.state.end_time - e.context.state.current_time < 300)
    cut = driver.act(query("饮料"))
    return {"rest": rest.activity_id, "with_app": with_app.activity_id, "bought": bought.activity_id,
            "tissue": tissue.activity_id, "stopped": stopped.activity_id, "cut": cut.activity_id}


@pytest.fixture(scope="module")
def query_export(tmp_path_factory, frozen_inputs):
    """整份导出只跑一次。"""
    root = tmp_path_factory.mktemp("query-day")
    with engine_factory_at(root, frozen_inputs) as create:
        engine = create(mutate=_hour)
        ids = _query_day(engine)
        manifest, exported_events, rows = export_completed(engine, root / "export")
        recorded = events(engine)
    return {"directory": root / "export", "ids": ids, "manifest": manifest, "events": exported_events,
            "rows": {row["activity_id"]: row for row in rows}, "recorded": recorded}


def _uses(query_export, activity_id) -> list[dict]:
    """app-usage.jsonl 里这项活动的全部 app_use 行。"""
    return [row for row in artifact_rows(query_export["directory"], "app_usage")
            if row["fact_kind"] == "app_use" and row["activity_id"] == activity_id]


def test_query_is_exported_as_the_fifth_activity_type(query_export):
    """做着地点里的事时做完一次查询：activities.jsonl 有一行 activity_type 为 query，占 second，执行区间恰一段、长度等于
    已做时长；并行的那件事另有一行占 first；导出认证通过（夹具已认证）。"""
    rows, ids = query_export["rows"], query_export["ids"]
    row = rows[ids["with_app"]]
    assert (row["activity_type"], row["status"], row["resource_claims"]) == ("query", "completed", ["second"])
    [segment] = row["segments"]
    assert segment["ended_at"] - segment["started_at"] == row["elapsed_seconds"] > 0
    assert row["data"]["action"] == "query" and row["data"]["answer"]["rows"]
    assert (rows[ids["rest"]]["activity_type"], rows[ids["rest"]]["resource_claims"]) == ("interact", ["first"])
    assert query_export["manifest"]["schema_version"] == 8
    assert sum(item["activity_type"] == "query" for item in rows.values()) == 4


def test_query_with_an_app_has_one_app_use_row_over_its_execution(query_export):
    """查询「柠檬茶」、prefer 写「美团」：这项活动在 app-usage.jsonl 里恰一行 app_use，app_id 是 meituan，
    区间与状态等于活动行的。"""
    row = query_export["rows"][query_export["ids"]["with_app"]]
    uses = [(use["app_id"], use["status"], use["segments"]) for use in _uses(query_export, row["activity_id"])]
    assert uses == [("meituan", row["status"], row["segments"])]
    assert row["data"]["app_id"] == "meituan"


def test_query_without_an_app_has_no_app_use_row(query_export):
    """查询「纸巾」：活动行存在、data.app_id 为空，app-usage.jsonl 没有这项活动的任何行。"""
    activity_id = query_export["ids"]["tissue"]
    row = query_export["rows"][activity_id]
    assert (row["activity_type"], row["data"]["app_id"]) == ("query", None)
    assert [item for item in artifact_rows(query_export["directory"], "app_usage")
            if item.get("activity_id") == activity_id] == []


def test_concluded_and_truncated_queries_keep_their_actual_interval(query_export):
    """提前结束的查询：活动行与 app_use 行都是 cancelled，区间止于结束那一秒；跑到截止仍在进行的查询是 truncated，
    区间止于截止时刻。"""
    rows, ids = query_export["rows"], query_export["ids"]
    [ended] = [event.data["end_time"] for event in query_export["recorded"] if event.event_type == "run_ended"]
    concluded = [event for event in query_export["recorded"] if event.event_type == "action_requested"
                 and event.data["tool_name"] == "conclude_activity"
                 and event.data["request"]["items"][0]["activity_name"] == rows[ids["stopped"]]["name"]]
    for key, status, end in (("stopped", "cancelled", concluded[0].occurred_at), ("cut", "truncated", ended)):
        row = rows[ids[key]]
        assert (row["status"], row["ended_at"], row["segments"][-1]["ended_at"]) == (status, end, end)
        assert [(use["status"], use["segments"]) for use in _uses(query_export, ids[key])] == [
            (status, row["segments"])]
    assert "answer" not in rows[ids["stopped"]]["data"] and "answer" not in rows[ids["cut"]]["data"]


def test_query_then_purchase_in_the_same_second_keeps_one_app_in_the_foreground(query_export):
    """查询做完的那一秒接着经美团购买：查询区间的终点等于购买第一段的起点；app-usage.jsonl 里查询那一行排在购买的
    各行之前；导出认证通过。"""
    ids = query_export["ids"]
    queried = query_export["rows"][ids["with_app"]]
    usage = [row for row in artifact_rows(query_export["directory"], "app_usage") if row["fact_kind"] == "app_use"]
    order = [row["activity_id"] for row in usage]
    bought = [row for row in usage if row["activity_id"] == ids["bought"]]
    assert bought and order.index(ids["with_app"]) < order.index(ids["bought"])
    first = min(segment["started_at"] for row in bought for segment in row["segments"])
    assert queried["segments"][-1]["ended_at"] == first


def test_query_leaves_no_foreground_event_order_attempt_experience_or_content_contact(query_export):
    """查询不写 app_foreground，不是下单尝试，没有已核验经历，没有 content_consumed；quality.json 结构号 8。"""
    queries = {query_export["ids"][key] for key in ("with_app", "tissue", "stopped", "cut")}
    foreground = [row for row in query_export["events"] if row["data"].get("kind") == "app_foreground"
                  and row["data"].get("activity_id") in queries]
    assert foreground == []
    directory = query_export["directory"]
    assert not [row for row in artifact_rows(directory, "purchases") if row.get("activity_id") in queries]
    assert not [row for row in artifact_rows(directory, "verified_experiences") if row.get("activity_id") in queries]
    assert not [row for row in artifact_rows(directory, "app_usage")
                if row["fact_kind"] == "content_consumed" and row.get("activity_id") in queries]
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["schema_version"] == 10 and set(quality) == QUALITY_KEYS


def test_query_line_of_the_timeline_is_chinese(query_export):
    """导出的时间线里四项查询各一行，「活动」一栏以「查询」开头；全文不含英文 query；质量报告的英文种类名计数为 0。"""
    directory = query_export["directory"]
    text = "".join(path.read_text(encoding="utf-8") for path in sorted((directory / "timeline").glob("*.md")))
    rows = [line.split(" | ") for line in text.splitlines() if line.startswith("| ") and not line.startswith("| 起止")
            and not line.startswith("| ---")]
    assert len([cells for cells in rows if cells[1].startswith("查询")]) == 4
    assert "query" not in text
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["timeline_labels"]["english_kind_names"] == 0


def test_status_lists_a_running_query(engine_factory):
    """查询进行中时 read_status 的 active_activities 里有一项 kind 为 query。"""
    from worldmock.runtime.monitoring.status import read_status

    engine = engine_factory()
    _, result = act(engine, "q", query("柠檬茶"))
    assert result.status == "in_progress"
    active = read_status(engine.context.store.run_dir)["active_activities"]
    assert [(item["activity_id"], item["kind"]) for item in active] == [(result.activity_id, "query")]
