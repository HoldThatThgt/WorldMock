"""运行检查工具直接读取真实当前活动足迹，不依赖已删除的目录动作类别。"""

import argparse

import pytest

from scripts import inspect_run
from tests.life.conftest import advance_next, sleep, use_app
from tests.life.environment_runtime import act, events


@pytest.mark.parametrize("request_factory,kind,has_content", [
    pytest.param(lambda: use_app("哔哩哔哩"), "interact", True, id="current-content"),
    pytest.param(lambda: use_app("美团"), "interact", False, id="plain-browse"),
    pytest.param(sleep, "sleep", False, id="non-content"),
])
def test_inspector_activity_summary_uses_actual_current_content_title(
    engine_factory, capsys, request_factory, kind, has_content,
):
    """真实引擎结算后，摘要显示当前内容标题；纯浏览与睡眠不会添加内容标题。"""
    engine = engine_factory()
    _, result = act(engine, "inspect-activity", request_factory())
    assert result.status == "in_progress", result.error_code
    advance_next(engine)
    progress = [row for row in events(engine, "activity_progress")
                if row.data["activity"]["activity_id"] == result.activity_id]
    assert progress
    snapshot = progress[-1].data["activity"]
    assert snapshot["kind"] == kind
    assert "title" not in snapshot
    title = snapshot.get("content_title")
    assert bool(title) is has_content
    expected = f"{snapshot['name']}{f'《{title}》' if title else ''} → {snapshot['status']}"
    assert inspect_run._brief(progress[-1].model_dump(mode="json")) == expected
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(type="activity_progress", agent=1, tail=1, full=False))
    output = capsys.readouterr().out
    assert expected in output
    assert ("《" in output) is has_content
    assert store.last_event_seq == point


@pytest.mark.asyncio
async def test_query_report_counts_the_decision_requests_that_offer_start_main_activity(engine_factory):
    """真实宿主流程的一次决策：先查一次世界，再用 start_side_activity 买一份柠檬茶。检查工具从运行的 SQLite 读回：
    两个模型请求都开放 start_main_activity，所以这次决策有两个行动请求；从首次查询到购买结算的模型调用数是 2。"""
    from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
    from tests.life.conftest import side_arguments, stage_day_plan

    engine = engine_factory()
    stage_day_plan(engine)
    _, requests, _, [result] = await decide(engine, "shop", [
        ToolResponse("query_world", {"name": "柠檬茶", "action": None, "category": None,
                                     "available_only": False, "offset": 0}, "query"),
        ToolResponse("start_side_activity", side_arguments("purchase", "柠檬茶"), "buy")])
    assert len(requests) == 2 and result.status == "in_progress", result.error_code
    offered = [{tool["function"]["name"] for tool in request["tools"]} for request in requests]
    assert all({"start_main_activity", "start_side_activity"} <= names for names in offered)
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        report = inspect_run.query_cost_report(conn, store.manifest["run_id"])
        decisions = inspect_run._decisions(conn)
    assert tuple(report) == inspect_run.QUERY_REPORT_KEYS
    assert (report["run_id"], report["calls"], report["failures"]) == (store.manifest["run_id"], 1, 0)
    assert report["model_calls_to_purchase"] == 2
    assert [(row["decision_id"], len(row["calls"])) for row in decisions] == [("decision-shop-1", 2)]
    assert all(terminal is not None for _, terminal in decisions[0]["calls"])
    assert store.last_event_seq == point
