"""运行检查工具直接读取真实当前活动足迹，不依赖已删除的目录动作类别。"""

import argparse

import pytest

from scripts import inspect_run
from tests.life.conftest import advance_next, purchase, sleep, travel, use_app
from tests.life.environment_runtime import act, events, finish_activity, reach


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


def test_rerun_walking_share_counts_only_the_built_in_walking_among_all_travel(engine_factory, capsys):
    """真实引擎先驾车去公司、再步行回家，检查工具从运行的 SQLite 读回步行占比。

    只驾过车时没有步行，报告写「不适用（没有步行）」；步行之后，步行秒数是那一趟步行的实际用时，出行秒数是两趟之和。
    """
    engine = engine_factory()
    store = engine.context.store
    home = engine.context.catalog.locations[engine.context.inputs.home_location_id].display_name

    def measured():
        """只读打开运行库，返回步行占比和复验报告里的那一行。"""
        with inspect_run._connect(store.run_dir) as conn:
            share = inspect_run.walking_share(conn)
            inspect_run.cmd_rerun(conn, argparse.Namespace())
        return share, [line for line in capsys.readouterr().out.splitlines() if line.startswith("7. ")]

    _, drive = act(engine, "drive", travel("公司", "驾车"))
    assert drive.status == "in_progress", drive.error_code
    finish_activity(engine, drive, "drive")
    driven = engine.context.state.activities[drive.activity_id].elapsed_seconds
    assert measured() == ({"walking_seconds": 0, "travel_seconds": driven, "available": False},
                          ["7. 步行占比：不适用（没有步行）"])
    _, walk = act(engine, "walk", travel(home))
    assert walk.status == "in_progress", walk.error_code
    finish_activity(engine, walk, "walk")
    walked = engine.context.state.activities[walk.activity_id].elapsed_seconds
    assert 0 < driven < walked
    point = store.last_event_seq
    assert measured() == (
        {"walking_seconds": walked, "travel_seconds": driven + walked, "available": True},
        [f"7. 步行占比：{walked}/{driven + walked}（{walked / (driven + walked):.0%}）"])
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


def test_inspector_event_lines_spell_out_the_phone_records(engine_factory, capsys):
    """真实引擎买一份外卖并等到送达：事件流里前台区间写 app、步骤与起止，通知写发送的 app 与标题，停留写地点；
    其余环境事件的写法不变。"""
    engine = engine_factory()
    _, bought = act(engine, "inspect-order", purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "inspect-order")
    (order,) = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    store = engine.context.store
    with inspect_run._connect(store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(type="environment_event", agent=1, tail=0, full=False))
    lines = capsys.readouterr().out.splitlines()
    activity = engine.context.state.activities[bought.activity_id]
    paying = activity.planned_end_at - order.payment_duration_seconds
    span = f"{inspect_run._t(activity.started_at)} → {inspect_run._t(paying)}"
    assert [line for line in lines if "app_foreground" in line][0].endswith(f"app_foreground meituan ordering {span}")
    sender = order.payment_app_id or "meituan"
    for expected in (f"order_notification meituan 订单已下单 {order.order_id}",
                     f"payment_notification {sender} 支付成功 {order.order_id}",
                     f"order_notification meituan 订单已发货 {order.order_id}",
                     f"order_notification meituan 订单已送达 {order.order_id}",
                     "location_entered home run_start"):
        assert [line for line in lines if line.endswith(expected)], expected
    assert any(line.endswith(f"delivery_arrival delivery-{order.order_id}") for line in lines)
    assert inspect_run._environment_brief({"kind": "location_left", "location_id": "home"}) == "location_left home"
