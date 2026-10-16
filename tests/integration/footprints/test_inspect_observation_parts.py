"""运行检查工具的 event 与 events 打印换回观察分件后的完整观察，与存储层解码的结果逐字节相同。"""

import argparse
import json

from scripts import inspect_run
from tests.life.conftest import do_at, stage_day_plan
from tests.life.environment_runtime import act, observe_only
from worldmock.footprints.models import canonical_json


def _run(engine, command, capsys, **arguments):
    """只读打开真实运行库，执行一个子命令，返回打印的各行；运行库在执行前后没有新事件。"""
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        command(conn, argparse.Namespace(run_dir=store.run_dir, **arguments))
    assert store.last_event_seq == point
    return capsys.readouterr().out.splitlines()


def test_event_and_events_print_the_complete_observation(engine_factory, capsys):
    engine = engine_factory()
    stage_day_plan(engine)
    act(engine, "rest", do_at("家", "测试休息"))
    observed = observe_only(engine, "after-rest")
    store = engine.context.store
    event = store.read_event(observed["observation_event_id"])
    seq = event.event_seq
    full_row = dict(json.loads(canonical_json(event.model_dump())), seq=seq)
    printed = _run(engine, inspect_run.cmd_event, capsys, seq=seq)
    assert json.loads("\n".join(printed)) == full_row
    assert "\n".join(printed) == json.dumps(full_row, ensure_ascii=False, indent=1)
    bindings = full_row["data"]["activity_bindings"]
    assert bindings and all(isinstance(row, dict) for row in bindings)
    printed = _run(engine, inspect_run.cmd_events, capsys, type="observation", agent=None, tail=1, full=True)
    assert json.loads("\n".join(printed)) == full_row
    printed = _run(engine, inspect_run.cmd_events, capsys, type="observation", agent=None, tail=1, full=False)
    with inspect_run._connect(store.run_dir) as conn:
        display = inspect_run.display_context(conn, store.run_dir)
    assert printed == [f"[{seq:>5}] {inspect_run._t(event.occurred_at, display.zone)} a1 {'observation':20s} "
                       f"{inspect_run._brief(full_row, display)}"]
