"""运行检查工具读当前格式的真实足迹：统一写法、运行时区、受理与拒绝等各行读的字段、UTF-8，以及已删除的 queries 子命令。"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts import inspect_run
from tests.integration.reflection_fixtures import host_run
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import action_identity, advance_next, do_at, purchase, sleep, stage_day_plan, travel, use_app
from tests.life.environment_runtime import act, conclude_app_use, events, finish_activity, reach
from worldmock.status import write_status

REPO_ROOT = Path(__file__).resolve().parents[3]
#: 一行开头的序号与时刻，以及一行里任何位置的时刻。
_LINE_HEAD = re.compile(r"^\[\s*(\d+)\] (\d\d-\d\d \d\d:\d\d:\d\d) ")
_CLOCK = re.compile(r"\d\d-\d\d \d\d:\d\d:\d\d")


def _run(engine, command, capsys, **arguments):
    """只读打开真实运行库，执行一个子命令，返回打印的各行；运行库在执行前后没有新事件。"""
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        command(conn, argparse.Namespace(run_dir=store.run_dir, **arguments))
    assert store.last_event_seq == point
    return capsys.readouterr().out.splitlines()


def _events(engine, capsys, **filters):
    """events 子命令的各行（缺省不筛选）。"""
    return _run(engine, inspect_run.cmd_events, capsys,
                **{"type": None, "agent": None, "tail": None, "full": False, **filters})


def _line(lines, seq):
    """某条事件那一行。"""
    [line] = [line for line in lines if line.startswith(f"[{seq:>5}]")]
    return line


def _display(engine):
    """这次真实运行的显示上下文。"""
    with inspect_run._connect(engine.context.store.run_dir) as conn:
        return inspect_run.display_context(conn, engine.context.store.run_dir)


@pytest.mark.parametrize("request_factory,phrase", [
    pytest.param(lambda: use_app("哔哩哔哩"), lambda catalog, data: (
        f"使用哔哩哔哩《{catalog.contents[data['content']['content_id']].title}》"), id="app-content"),
    pytest.param(lambda: do_at("家", "测试休息"), lambda catalog, data: "在家：测试休息", id="location-unit"),
    pytest.param(lambda: purchase("牛肉面"), lambda catalog, data: "购买牛肉面（经美团下单）", id="purchase"),
    pytest.param(sleep, lambda catalog, data: data["activity"]["name"], id="sleep"),
    pytest.param(lambda: travel("公司"), lambda catalog, data: data["activity"]["name"], id="travel"),
])
def test_inspector_activity_summary_uses_the_shared_wording(engine_factory, capsys, request_factory, phrase):
    """真实引擎结算后，进度行是「<写法> → <status>」；出行与睡眠仍写活动名；带 --type、--tail 筛选时购买行照样写得出
    商品和下单渠道（订单记录取全库）。"""
    engine = engine_factory()
    _, result = act(engine, "inspect-activity", request_factory())
    assert result.status == "in_progress", result.error_code
    advance_next(engine)
    progress = [row for row in events(engine, "activity_progress") if row.activity_id == result.activity_id]
    assert progress
    data = progress[-1].data
    expected = f"{phrase(engine.context.catalog, data)} → {data['activity']['status']}"
    assert inspect_run._brief(progress[-1].model_dump(mode="json"), _display(engine)) == expected
    lines = _events(engine, capsys, type="activity_progress", agent=1, tail=1)
    assert lines == [_line(lines, progress[-1].event_seq)] and lines[0].endswith(f" {expected}")


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


def test_inspector_event_lines_spell_out_the_phone_records(engine_factory, capsys):
    """真实引擎买一份外卖并等到送达：前台区间写「app 前台 · 美团 · 下单」和按运行时区的起止，通知写「订单通知 · 美团 ·
    <标题>」「消费通知 · <发通知的 app 的名称> · 支付成功」和订单身份；停留与其余环境事件的写法不变。"""
    engine = engine_factory()
    _, bought = act(engine, "inspect-order", purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "inspect-order")
    (order,) = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    lines = _events(engine, capsys, type="environment_event", agent=1)
    display = _display(engine)
    activity = engine.context.state.activities[bought.activity_id]
    paying = activity.planned_end_at - order.payment_duration_seconds
    span = f"{inspect_run._t(activity.started_at, display.zone)} → {inspect_run._t(paying, display.zone)}"
    assert [line for line in lines if "app 前台 · 美团 · 下单" in line][0].endswith(f"app 前台 · 美团 · 下单 {span}")
    sender = engine.context.catalog.apps[order.payment_app_id or "meituan"].name
    for expected in (f"订单通知 · 美团 · 订单已下单 {order.order_id}",
                     f"消费通知 · {sender} · 支付成功 {order.order_id}",
                     f"订单通知 · 美团 · 订单已发货 {order.order_id}",
                     f"订单通知 · 美团 · 订单已送达 {order.order_id}",
                     "location_entered home run_start"):
        assert [line for line in lines if line.endswith(expected)], expected
    assert any(line.endswith(f"delivery_arrival delivery-{order.order_id}") for line in lines)
    assert inspect_run._environment_brief({"kind": "location_left", "location_id": "home"}, display) == "location_left home"


def _new_york(persona, config):
    """运行时区换成 America/New_York，起止仍是当地 08:00 起的一天。"""
    zone = ZoneInfo("America/New_York")
    config["timezone"] = zone.key
    config["start_time"] = datetime(2026, 1, 5, 8, 0, tzinfo=zone)
    config["stop_time"] = datetime(2026, 1, 6, 8, 0, tzinfo=zone)


@pytest.mark.parametrize("mutate,zone", [(None, "Asia/Shanghai"), (_new_york, "America/New_York")],
                         ids=["shanghai", "new-york"])
def test_inspector_clocks_follow_the_run_timezone(engine_factory, capsys, mutate, zone):
    """运行时区不同的两次运行各买一份外卖等到送达：events、body、money、flows 打印的每个钟点都等于该事件时刻按运行时区
    换算的结果，前台区间的起止也是；两次运行 events 的第一行都是当地 01-05 08:00:00，与测试机的时区无关。"""
    engine = engine_factory(mutate=mutate)
    assert engine.context.config.timezone == zone
    _, bought = act(engine, "clock-order", purchase("牛肉面"))
    finish_activity(engine, bought, "clock-order")
    (order,) = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    local = ZoneInfo(zone)
    recorded = {row.event_seq: row for row in events(engine)}
    lines = _events(engine, capsys)
    assert _LINE_HEAD.match(lines[0]).group(2) == "01-05 08:00:00"
    for line in lines:
        event = recorded[int(_LINE_HEAD.match(line).group(1))]
        moments = [event.occurred_at]
        if event.data.get("kind") == "app_foreground":
            moments += [event.data["interval_start"], event.data["interval_end"]]
        assert _CLOCK.findall(line) == [datetime.fromtimestamp(value, local).strftime("%m-%d %H:%M:%S")
                                        for value in moments], line
    printed = [*_run(engine, inspect_run.cmd_body, capsys, agent=None), *_run(engine, inspect_run.cmd_money, capsys),
               *_run(engine, inspect_run.cmd_flows, capsys, tail=None)]
    heads = [_LINE_HEAD.match(line) for line in printed]
    assert len([head for head in heads if head]) > 3
    for head in filter(None, heads):
        expected = datetime.fromtimestamp(recorded[int(head.group(1))].occurred_at, local)
        assert head.group(2) == expected.strftime("%m-%d %H:%M:%S")


def test_inspector_accepted_and_rejected_lines_read_the_current_fields(engine_factory, capsys):
    """真实引擎先被拒一次、再打开哔哩哔哩、再买一份外卖：受理行是写法加「 → in_progress」，不含「?」；拒绝行是
    「<error_code>: <reason>」，与事件里的两个字段逐字相同。"""
    engine = engine_factory()
    _, refused = act(engine, "refused", travel("家"))
    assert refused.status == "rejected"
    _, watched = act(engine, "watch", use_app("哔哩哔哩"))
    conclude_app_use(engine, watched.activity_id, "watch")
    _, bought = act(engine, "buy", purchase("牛肉面"))
    lines = _events(engine, capsys)
    accepted = {row.activity_id: row for row in events(engine, "action_accepted")}
    content = accepted[watched.activity_id].data["activity"]["data"]["content_id"]
    title = engine.context.catalog.contents[content].title
    assert _line(lines, accepted[watched.activity_id].event_seq).endswith(
        f"action_accepted      使用哔哩哔哩《{title}》 → in_progress")
    assert _line(lines, accepted[bought.activity_id].event_seq).endswith(
        "action_accepted      购买牛肉面（经美团下单） → in_progress")
    [rejection] = events(engine, "action_rejected")
    assert _line(lines, rejection.event_seq).endswith(
        f"action_rejected      {rejection.data['error_code']}: {rejection.data['reason']}")
    assert not [line for line in lines if " action_accepted " in line and "?" in line]


@pytest.mark.asyncio
async def test_inspector_plan_revision_and_memory_lines(engine_factory, capsys, tmp_path):
    """经真实宿主流程修订一次当日计划（current_contract_fixtures 的 decide），再由启用记忆的真实人物在家做一次「测试休息」、
    收尾时写下一条记忆（reflection_fixtures 的 host_run）：不带活动快照的受理行是「Day plan revised → completed」；记忆行
    的条数等于事件 episodes 的条数，且不为 0。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, _, answer, _ = await decide(engine, "plan", [ToolResponse("revise_day_plan", {"goals": []})])
    assert answer == "revise_day_plan registered"
    person = await host_run(engine, tmp_path / "agent")
    await person.rest_and_note("休息让我心情轻松。")
    lines = _events(engine, capsys)
    [revised] = [row for row in events(engine, "action_accepted") if row.data.get("message") == "Day plan revised"]
    assert _line(lines, revised.event_seq).endswith("action_accepted      Day plan revised → completed")
    updates = events(engine, "memory_updated")
    assert any(update.data["episodes"] for update in updates)
    for update in updates:
        assert _line(lines, update.event_seq).endswith(f"memory_updated       {len(update.data['episodes'])} 条")


def test_inspector_request_lines_read_only_the_current_keys(engine_factory, capsys):
    """请求行是「start_side_activity 」加请求 JSON 的前 80 个字符；只带 kind、arguments 的旧格式请求，以及去掉所读的键的
    受理、拒绝、记忆事件，都照 KeyError 失败，不显示替代文字。"""
    engine = engine_factory()
    _, bought = act(engine, "buy", purchase("牛肉面", "薯片", "柠檬茶", "扑克牌", "牛肉面", "薯片", "柠檬茶", "扑克牌"))
    assert bought.status == "in_progress", bought.error_code
    lines = _events(engine, capsys)
    request = next(row for row in events(engine, "action_requested") if row.action_id == action_identity("buy"))
    text = json.dumps(request.data["request"], ensure_ascii=False)
    assert len(text) > 80
    assert _line(lines, request.event_seq).endswith(f"start_side_activity {text[:80]}")
    display = _display(engine)
    accepted = next(row for row in events(engine, "action_accepted")).model_dump(mode="json")
    stale = [{"event_type": "action_requested", "resource_changes": {},
              "data": {"kind": "purchase", "arguments": request.data["request"]}},
             {**accepted, "data": {key: value for key, value in accepted["data"].items() if key != "status"}},
             {**accepted, "data": {key: value for key, value in accepted["data"].items()
                                   if key not in {"activity", "message"}}},
             {"event_type": "action_rejected", "resource_changes": {}, "data": {"error_code": "MERCHANT_CLOSED"}},
             {"event_type": "memory_updated", "resource_changes": {}, "data": {"episode_ids": ["m1"]}}]
    for event in stale:
        with pytest.raises(KeyError):
            inspect_run._brief(event, display)


def test_inspector_other_lines_read_the_envelope_resource_changes(engine_factory, capsys):
    """坐公共交通去公司（起步价 200 分）：这次出行的结果行恰为信封上的资金变化；信封没有资金变化的结果行仍是正文 JSON 的
    前 90 个字符（去掉 request、response）。"""
    engine = engine_factory()
    _, refused = act(engine, "refused", travel("家"))
    assert refused.status == "rejected"
    _, ride = act(engine, "ride", travel("公司", "公共交通"))
    assert ride.status == "in_progress", ride.error_code
    lines = _events(engine, capsys)
    results = {row.action_id: row for row in events(engine, "action_result")}
    fare = results[action_identity("ride")]
    assert fare.resource_changes == {"account:agent:1": -200, "account:external:scenario": 200}
    assert _line(lines, fare.event_seq).endswith('action_result        {"account:agent:1": -200, '
                                                 '"account:external:scenario": 200}')
    other = results[action_identity("refused")]
    assert not other.resource_changes
    body = {key: value for key, value in other.data.items() if key not in ("request", "response")}
    assert _line(lines, other.event_seq).endswith(f"action_result        {json.dumps(body, ensure_ascii=False)[:90]}")


def test_inspector_summary_reads_the_run_files_as_utf8(engine_factory, capsys, monkeypatch):
    """summary 读运行目录里的 manifest.json 与 status.json 时都写明 UTF-8：期间没有一次编码为 None 的文本读取，打印出
    运行身份与状态。"""
    engine = engine_factory()
    run_dir = engine.context.store.run_dir
    write_status(run_dir, "running", "deciding")
    defaulted, original = [], io.text_encoding

    def recording(encoding, stacklevel=2):
        """记下没有写编码的文本读取，再交给原函数。"""
        if encoding is None:
            defaulted.append(encoding)
        return original(encoding, stacklevel + 1)

    monkeypatch.setattr(io, "text_encoding", recording)
    lines = _run(engine, inspect_run.cmd_summary, capsys)
    monkeypatch.undo()
    assert defaulted == []
    assert lines[0] == f"run_id     : {engine.context.store.manifest['run_id']}"
    assert lines[1] == "状态       : running (deciding)"


def test_queries_is_no_longer_a_subcommand(engine_factory):
    """子进程运行 scripts/inspect_run.py <运行目录> queries：退出码 2，标准错误含 invalid choice: 'queries'；
    同一运行目录的 summary 退出码 0。"""
    engine = engine_factory()
    run_dir = engine.context.store.run_dir
    write_status(run_dir, "running", "deciding")
    script = str(REPO_ROOT / "scripts" / "inspect_run.py")
    child = {**os.environ, "PYTHONUTF8": "1"}
    refused = subprocess.run([sys.executable, script, str(run_dir), "queries"], capture_output=True, text=True,
                             encoding="utf-8", env=child, check=False)
    assert refused.returncode == 2 and "invalid choice: 'queries'" in refused.stderr
    summary = subprocess.run([sys.executable, script, str(run_dir), "summary"], capture_output=True, text=True,
                             encoding="utf-8", env=child, check=False)
    assert summary.returncode == 0, summary.stderr
    assert engine.context.store.manifest["run_id"] in summary.stdout
