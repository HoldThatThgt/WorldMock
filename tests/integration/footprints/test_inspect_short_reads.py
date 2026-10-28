"""运行检查工具按事件序号窗口分页读库：每条语句至多读一页事件，打印的与以前读整段历史时逐字节相同。"""

import argparse
import json

from scripts import inspect_run
from tests.footprints.recording import recorded_engine
from tests.footprints.statement_log import StatementLog
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.projections.commerce.purchases import orders_by_id
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.runtime.monitoring.status import write_status

#: action_result 经类型表达式索引的筛选（display_context 用的同一写法）。
_RESULT_INDEX = ("event_seq IN (SELECT event_seq FROM events "
                 "WHERE json_extract(body,'$.event_type')='action_result')")


def _printed(run_dir, command, capsys, **arguments):
    """只读打开运行库执行一个子命令，返回打印的各行。"""
    with inspect_run._connect(run_dir) as conn:
        command(conn, argparse.Namespace(run_dir=run_dir, **arguments))
    return capsys.readouterr().out.splitlines()


def _whole_history(run_dir, where="", args=()):
    """以前的做法：一条语句读整段历史里匹配的事件，解析为带 seq 的字典。"""
    with inspect_run._connect(run_dir) as conn:
        sql = "SELECT event_seq, body FROM events" + (f" WHERE {where}" if where else "") + " ORDER BY event_seq"
        return [dict(json.loads(body), seq=seq) for seq, body in conn.execute(sql, args)]


def _summary_lines(run_dir, rows):
    """以前的 summary：解码每一行，按首次出现的次序计数，再按条数多少稳定排序。"""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["event_type"]] = counts.get(row["event_type"], 0) + 1
    return [f"run_id     : {manifest.get('run_id')}", f"状态       : {status.get('status')} ({status.get('phase')})",
            f"事件总数   : {len(rows)}",
            *(f"  {name:24s} {count}" for name, count in sorted(counts.items(), key=lambda item: -item[1]))]


def test_summary_prints_the_same_lines_as_counting_every_event(engine_factory, capsys):
    engine, _ = recorded_engine(engine_factory)
    run_dir = engine.context.store.run_dir
    write_status(run_dir, "completed", "stopped")
    assert _printed(run_dir, inspect_run.cmd_summary, capsys) == _summary_lines(run_dir, _whole_history(run_dir))


def test_summary_orders_equal_counts_by_first_occurrence(tmp_path, capsys):
    run_dir = tmp_path / "run"
    store = FootprintStore.create(run_dir, {"run_id": "run", "agent_ids": [1]}, {"version": 0, "current_time": 100})
    started = {"round_id": "round", "round_order": 0, "world_state_version": 0, "wake_reasons": []}
    store.append_evidence([EventDraft(event_type="observation", occurred_at=100, agent_id=1),
                           EventDraft(event_type="decision_started", occurred_at=100, agent_id=1, data=started),
                           EventDraft(event_type="decision_started", occurred_at=100, agent_id=1, data=started),
                           EventDraft(event_type="observation", occurred_at=100, agent_id=1)])
    store.close()
    write_status(run_dir, "running", "deciding")
    lines = _printed(run_dir, inspect_run.cmd_summary, capsys)
    assert lines[2:] == ["事件总数   : 4", f"  {'observation':24s} 2", f"  {'decision_started':24s} 2"]
    assert lines == _summary_lines(run_dir, _whole_history(run_dir))


def test_money_prints_only_changed_events_and_the_same_balances(engine_factory, capsys):
    engine, _ = recorded_engine(engine_factory)
    run_dir = engine.context.store.run_dir
    zone = inspect_run._zone(run_dir)
    expected, balances = [], {}
    for row in _whole_history(run_dir, "json_extract(body,'$.resource_changes') IS NOT NULL"):
        if not row["resource_changes"]:
            continue
        expected.append(f"[{row['seq']:>5}] {inspect_run._t(row['occurred_at'], zone)} {row['event_type']:18s} "
                        f"{json.dumps(row['resource_changes'], ensure_ascii=False)}")
        for account, delta in row["resource_changes"].items():
            balances[account] = balances.get(account, 0) + delta
    expected.append("── 期末余额 ──")
    expected.extend(f"  {account:32s} {balances[account]:>12,}" for account in sorted(balances))
    assert len(expected) > 3
    assert _printed(run_dir, inspect_run.cmd_money, capsys) == expected


def test_paged_rows_equal_one_whole_history_read(engine_factory, monkeypatch):
    engine, _ = recorded_engine(engine_factory)
    run_dir = engine.context.store.run_dir
    assert engine.context.store.last_event_seq > 2
    monkeypatch.setattr(inspect_run, "EVENT_PAGE_SIZE", 2)
    filters = [("", ()), ("json_extract(body,'$.event_type')=?", ("observation",)), (inspect_run._ACTION_REQUEST, ()),
               ("event_seq=?", (3,)), (_RESULT_INDEX, ())]
    columns = ["event_seq", "json_extract(body, '$.agent_id'), json_extract(body, '$.occurred_at')",
               "body"]
    with inspect_run._connect(run_dir) as real:
        log = StatementLog(real)
        for where, args in filters:
            assert inspect_run._rows(log, where, args) == _whole_history(run_dir, where, args), where
        for column in columns:
            unpaged = real.execute(f"SELECT {column} FROM events WHERE json_extract(body, '$.event_type') = ? "
                                   "ORDER BY event_seq", ("activity_progress",)).fetchall()
            assert unpaged and inspect_run._select(log, "activity_progress", column) == unpaged
        environment = real.execute("SELECT event_seq FROM events WHERE json_extract(body, '$.event_type') = ? "
                                   "AND json_extract(body, '$.data.kind') = ? ORDER BY event_seq",
                                   ("environment_event", "body_settlement")).fetchall()
        assert inspect_run._select(log, "environment_event", "event_seq", "body_settlement") == environment
    pages = [parameters for sql, parameters in log.statements if "event_seq>? AND event_seq<=?" in sql]
    assert pages and all(stop - after == 2 for after, stop, *_ in pages)


def test_display_context_reads_results_through_the_type_index(engine_factory):
    engine, _ = recorded_engine(engine_factory)
    run_dir = engine.context.store.run_dir
    with inspect_run._connect(run_dir) as real:
        log = StatementLog(real)
        display = inspect_run.display_context(log, run_dir)
        pages = [(sql, parameters) for sql, parameters in log.statements if "event_seq>? AND event_seq<=?" in sql]
        assert pages
        assert all("USING COVERING INDEX events_agent_type" in log.plan(sql, parameters) for sql, parameters in pages)
    results = _whole_history(run_dir, "json_extract(body,'$.event_type')='action_result'")
    assert results and display.orders == orders_by_id(results)
