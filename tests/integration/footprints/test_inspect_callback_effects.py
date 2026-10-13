"""检查工具 events 的回调效果行：凭状态的地点内容开始时用掉状态的那一条在种类后写出效果类型 consume_on_start，
别的回调效果仍只写种类（docs/design/05-recording-engine.md「事件与来源」「检查工具」）。

经真实请求、引擎与 SQLite：样例环境副本 grocery_environment 登记状态 groceries，外卖送来扑克牌时置为上限，
家里凭它的「做饭」开始时把它减 1。
"""

from __future__ import annotations

import argparse

from scripts import inspect_run
from tests.life.conftest import advance_until_ready, conclude_request, do_at, purchase
from tests.life.environment_inputs import grocery_environment, set_content
from tests.life.environment_runtime import act, act_group, events, reach


def _events(engine, capsys):
    """只读打开真实运行库，执行 events 子命令（只看环境事件），返回打印的各行；运行库前后没有新事件。"""
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(run_dir=store.run_dir, type="environment_event", agent=None,
                                                        tail=None, full=False))
    assert store.last_event_seq == point
    return capsys.readouterr().out.splitlines()


def _effect_line(engine, lines, effect_type):
    """某一种回调效果恰有的那一条事件在 events 里的那一行。"""
    [row] = [row for row in events(engine, "environment_event")
             if row.data.get("kind") == "callback_effect" and row.data["type"] == effect_type]
    [line] = [line for line in lines if line.startswith(f"[{row.event_seq:>5}]")]
    return line


def test_events_line_names_the_state_used_up_when_cooking_starts(engine_factory, tmp_path, capsys):
    """在家做着三小时的「测试休息」时同组点一副扑克牌，送到后收货把 groceries 置为上限，结束休息再点名「做饭」：
    开始那一刻的回调效果行以「callback_effect consume_on_start」结尾，收货置为上限的那一行不写效果类型。"""
    root = grocery_environment(tmp_path / "groceries")
    set_content(root, "c-rest", duration_seconds=3 * 3600)
    engine = engine_factory(root)
    _, (rest, bought) = act_group(engine, "buy", do_at("家", "测试休息"), purchase("扑克牌"))
    assert (rest.status, bought.status) == ("in_progress", "in_progress"), (rest.error_code, bought.error_code)
    [order] = engine.context.state.activities[bought.activity_id].data["order_ids"]
    reach(engine, engine.context.state.deliveries[order].expected_at)
    assert advance_until_ready(engine)
    names = [item.name for item in engine.context.state.activities.values() if item.concluded_at is None]
    _, ended = act(engine, "buy-end", conclude_request(*names), "conclude_activity")
    assert ended.status == "completed", ended.error_code
    _, cooking = act(engine, "cook", do_at("家", "做饭"))
    assert cooking.status == "in_progress", cooking.error_code
    lines = _events(engine, capsys)
    assert _effect_line(engine, lines, "consume_on_start").endswith(" callback_effect consume_on_start")
    assert "reset_to_cap" not in _effect_line(engine, lines, "reset_to_cap")
    assert sum("consume_on_start" in line for line in lines) == 1
