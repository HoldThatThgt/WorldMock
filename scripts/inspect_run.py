#!/usr/bin/env python3
"""@brief 只读浏览一次运行的足迹数据：摘要、事件流、资金流水、身体轨迹、分录。

用法：
  python3 scripts/inspect_run.py <run_dir> summary
  python3 scripts/inspect_run.py <run_dir> events [--type TYPE] [--agent N] [--tail K] [--full]
  python3 scripts/inspect_run.py <run_dir> event <seq>
  python3 scripts/inspect_run.py <run_dir> money
  python3 scripts/inspect_run.py <run_dir> body [--agent N]
  python3 scripts/inspect_run.py <run_dir> flows [--tail K]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _connect(run_dir: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库。"""
    db = (run_dir / "footprints.sqlite").resolve()
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _rows(conn: sqlite3.Connection, where: str = "", args: tuple = ()) -> list[dict]:
    """@brief 读取事件体并解析为字典列表。"""
    sql = "SELECT event_seq, body FROM events"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY event_seq"
    return [dict(json.loads(b), seq=s) for s, b in conn.execute(sql, args)]


def _t(epoch: int) -> str:
    """@brief 事件时间转可读时刻。"""
    return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M:%S")


def cmd_summary(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印运行摘要。"""
    rows = _rows(conn)
    manifest = json.loads((a.run_dir / "manifest.json").read_text())
    status = json.loads((a.run_dir / "status.json").read_text())
    print(f"run_id     : {manifest.get('run_id')}")
    print(f"状态       : {status.get('status')} ({status.get('phase')})")
    print(f"事件总数   : {len(rows)}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["event_type"]] = counts.get(r["event_type"], 0) + 1
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:24s} {v}")


def cmd_events(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印事件流（一行一事件，可过滤）。"""
    where, args = [], []
    if a.type:
        where.append("json_extract(body,'$.event_type')=?")
        args.append(a.type)
    if a.agent is not None:
        where.append("json_extract(body,'$.agent_id')=?")
        args.append(a.agent)
    rows = _rows(conn, " AND ".join(where), tuple(args))
    if a.tail:
        rows = rows[-a.tail:]
    for r in rows:
        agent = f"a{r['agent_id']}" if r.get("agent_id") else "  "
        if a.full:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            continue
        brief = _brief(r)
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} {agent} {r['event_type']:20s} {brief}")


def _brief(r: dict) -> str:
    """@brief 按事件类型提取一行摘要。"""
    d = r.get("data", {})
    t = r["event_type"]
    if t == "action_requested":
        payload = d.get("request", d.get("arguments", ""))
        return f"{d.get('tool_name') or d.get('kind','?')} {json.dumps(payload, ensure_ascii=False)[:80]}"
    if t == "action_accepted":
        return f"{d.get('tool_name') or d.get('kind','?')}"
    if t == "action_rejected":
        return f"{d.get('tool_name','?')} → {d.get('error_code','?')}"
    if t == "environment_event":
        return f"{d.get('kind')} {d.get('scheduled_id','')}"
    if t == "activity_progress":
        act = d.get("activity", {})
        return f"{act.get('kind','?')} → {act.get('status','?')} target={act.get('target_id','')}"
    if t == "memory_updated":
        return f"{len(d.get('episode_ids', []))} 条"
    if "resource_changes" in d and d["resource_changes"]:
        return json.dumps(d["resource_changes"], ensure_ascii=False)[:100]
    return json.dumps({k: v for k, v in d.items() if k not in ("request", "response")}, ensure_ascii=False)[:90]


def cmd_event(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印单个事件全文。"""
    rows = _rows(conn, "event_seq=?", (a.seq,))
    print(json.dumps(rows[0], ensure_ascii=False, indent=1) if rows else f"seq {a.seq} 不存在")


def cmd_money(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印全部资金流水与期末余额。"""
    balances: dict[str, int] = {}
    for r in _rows(conn, "json_extract(body,'$.data.resource_changes') IS NOT NULL"):
        changes = r["data"].get("resource_changes") or {}
        if not changes:
            continue
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} {r['event_type']:18s} {json.dumps(changes, ensure_ascii=False)}")
        for acct, delta in changes.items():
            balances[acct] = balances.get(acct, 0) + delta
    print("── 期末余额 ──")
    for acct in sorted(balances):
        print(f"  {acct:32s} {balances[acct]:>12,}")


def cmd_body(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印身体轨迹（时间/能量/睡眠债/告警）。"""
    where = "json_extract(body,'$.event_type')='observation'"
    args: tuple = ()
    if a.agent is not None:
        where += " AND json_extract(body,'$.agent_id')=?"
        args = (a.agent,)
    prev = None
    for r in _rows(conn, where, args):
        b = r["data"].get("body") or {}
        cur = (b.get("energy_kcal"), b.get("sleep_debt_hours"), b.get("alert"))
        if cur == prev:
            continue
        prev = cur
        flag = f" ⚠{b['alert']}" if b.get("alert") else ""
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} a{r['agent_id']}  能量={cur[0]:>7.1f}  睡眠债={cur[1]:>5.2f}h{flag}")


def cmd_flows(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印资源流水分录。"""
    rows = _rows(conn, "json_extract(body,'$.data.resource_flows') IS NOT NULL")
    if a.tail:
        rows = rows[-a.tail:]
    for r in rows:
        for f in r["data"]["resource_flows"]:
            print(f"[{r['seq']:>5}] {_t(r['occurred_at'])} a{r['agent_id']}  {f['resource']:>18s} {f['direction']:>6s} {f['amount']:>10.2f}  {f['cause']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只读浏览运行足迹")
    parser.add_argument("run_dir", type=Path)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary")
    pe = sub.add_parser("events")
    pe.add_argument("--type")
    pe.add_argument("--agent", type=int)
    pe.add_argument("--tail", type=int)
    pe.add_argument("--full", action="store_true")
    sub.add_parser("event").add_argument("seq", type=int)
    sub.add_parser("money")
    pb = sub.add_parser("body")
    pb.add_argument("--agent", type=int)
    pf = sub.add_parser("flows")
    pf.add_argument("--tail", type=int)
    args = parser.parse_args()
    conn = _connect(args.run_dir)
    {"summary": cmd_summary, "events": cmd_events, "event": cmd_event,
     "money": cmd_money, "body": cmd_body, "flows": cmd_flows}[args.cmd](conn, args)
