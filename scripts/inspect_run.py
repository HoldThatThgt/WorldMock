#!/usr/bin/env python3
"""@brief 只读浏览一次运行的足迹数据：摘要、事件流、资金流水、身体轨迹、分录与真实复验指标。

用法：
  python3 scripts/inspect_run.py <run_dir> summary
  python3 scripts/inspect_run.py <run_dir> events [--type TYPE] [--agent N] [--tail K] [--full]
  python3 scripts/inspect_run.py <run_dir> event <seq>
  python3 scripts/inspect_run.py <run_dir> money
  python3 scripts/inspect_run.py <run_dir> body [--agent N]
  python3 scripts/inspect_run.py <run_dir> flows [--tail K]
  python3 scripts/inspect_run.py <run_dir> rerun
  python3 scripts/inspect_run.py <run_dir> workday --environment <环境目录>

时刻一律按运行清单的 window.timezone 显示；events 里活动与手机事件的写法取 worldmock.recording_engine.projections.presentation.display_wording，
名称取这次运行冻结的环境目录（docs/design/05-recording-engine.md「显示写法与检查工具」）。
rerun 只读计算 docs/design/05-recording-engine.md「显示写法与检查工具」的运行指标，只报告，不作门禁。
workday 只读打印 docs/design/05-recording-engine.md 的 {run_id, workload, routine_deviation, notes}（JSON，只报告）：
班次、住处、播报间隔与下班、到家窗口都取自 --environment 完整输入（原文字节身份 persona_sha256s 与 config_sha256 须等于运行清单，否则拒绝、不打印）。
event 与 events 打印的观察已把观察分件换回原表（docs/design/05-recording-engine.md「检查工具」）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from worldmock.recording_engine.projections.presentation.display_wording import (
    DisplayNames,
    activity_phrase,
    event_content_id,
    phone_phrase,
)
from worldmock.recording_engine.validation.frozen_environment import frozen_display_names  # noqa: E402
from worldmock.recording_engine.storage.observation_parts import rehydrate_observation  # noqa: E402
from worldmock.recording_engine.projections.commerce.purchases import orders_by_id  # noqa: E402
from worldmock.recording_engine.storage.store import EVENT_PAGE_SIZE  # noqa: E402
from worldmock.runtime.monitoring.status import event_type_counts  # noqa: E402
from worldmock.simulation_engine.body.felt import (  # noqa: E402
    APETITE_BASE_LEVEL,
    APPETITE_BANDS,
    APPETITE_STARVING_LEVEL,
)

#: 饥饿分级由轻到重（与体感分级同源）：「最低饥饿分级」取观察中出现过的最重一档。
APPETITE_ORDER = (APETITE_BASE_LEVEL, *(label for _, label in APPETITE_BANDS), APPETITE_STARVING_LEVEL)
#: 「播报唤醒占比」统计的唤醒原因。
PROGRESS_REPORT_REASON = "progress_report_due"
#: 「零点计划是否含早于 08:30 的提醒」的界限（场景本地时刻，时与分）。
EARLY_REMINDER = (8, 30)
#: 活动行写活动名而不写写法的两种活动。
NAMED_KINDS = frozenset({"travel", "sleep"})
#: 写法后跟订单身份的两种手机通知。
NOTICE_KINDS = frozenset({"order_notification", "payment_notification"})


@dataclass(frozen=True)
class Display:
    """@brief events 一行摘要读的只读显示上下文。"""

    #: 这次运行冻结的环境目录给出的显示名称。
    names: DisplayNames
    #: 全库订单身份到商品履约记录，不受事件筛选的影响。
    orders: dict
    #: 运行清单的场景时区。
    zone: ZoneInfo


def _connect(run_dir: Path) -> sqlite3.Connection:
    """@brief 以只读模式打开足迹库。"""
    db = (run_dir / "footprints.sqlite").resolve()
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _paged(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Iterator[tuple]:
    """@brief 按事件序号窗口 (after, after+EVENT_PAGE_SIZE] 逐页执行 sql（前两个参数是窗口两端），按页先后给出各行。

    窗口只覆盖开始时已提交的事件；每页一个语句并一次取完，至多读 EVENT_PAGE_SIZE 条事件，页间不持有读锁。
    """
    last = conn.execute("SELECT COALESCE(MAX(event_seq),0) FROM events").fetchone()[0]
    for after in range(0, last, EVENT_PAGE_SIZE):
        yield from conn.execute(sql, (after, after + EVENT_PAGE_SIZE, *args)).fetchall()


def _rows(conn: sqlite3.Connection, where: str = "", args: tuple = ()) -> list[dict]:
    """@brief 按事件序号窗口分页读取匹配的事件体并解析为字典列表（按 event_seq）；每页一个短语句。"""
    sql = "SELECT event_seq, body FROM events WHERE event_seq>? AND event_seq<=?"
    sql += (f" AND ({where})" if where else "") + " ORDER BY event_seq"
    return [dict(json.loads(body), seq=seq) for seq, body in _paged(conn, sql, args)]


def _rehydrated(conn: sqlite3.Connection, row: dict) -> dict:
    """@brief 观察行的分件引用换回原表（与存储层解码相同），其余事件原样；event 与 events 打印的都是完整事件。"""
    if row["event_type"] != "observation":
        return row
    return {**row, "data": rehydrate_observation(row["data"], conn)}


def _t(epoch: int, zone: ZoneInfo) -> str:
    """@brief 事件时间按运行时区转可读时刻，与运行这条命令的电脑的时区无关。"""
    return datetime.fromtimestamp(epoch, zone).strftime("%m-%d %H:%M:%S")


def _zone(run_dir: Path) -> ZoneInfo:
    """@brief 运行清单 window.timezone 给出的场景时区。"""
    return ZoneInfo(_manifest(run_dir)["window"]["timezone"])


def display_context(conn: sqlite3.Connection, run_dir: Path) -> Display:
    """@brief 读一次冻结名称与全库订单记录，取运行时区；名称取不到时照运行故障失败。"""
    manifest = _manifest(run_dir)
    results = _rows(conn, "event_seq IN (SELECT event_seq FROM events "
                          "WHERE json_extract(body,'$.event_type')='action_result')")
    return Display(frozen_display_names(run_dir, manifest), orders_by_id(results),
                   ZoneInfo(manifest["window"]["timezone"]))


def _summary_cache_and_compaction(conn: sqlite3.Connection) -> tuple[int | None, int | None, int | None, int]:
    """@brief 汇总运行库已落盘模型响应的输入/缓存令牌和日内压缩事件次数；缺失缓存明细时返回 None。"""
    cached_expr = (
        "COALESCE(json_extract(body,'$.data.response.usage.prompt_cache_hit_tokens'),"
        " json_extract(body,'$.data.response.usage.prompt_tokens_details.cached_tokens'))"
    )
    row = conn.execute(
        f"SELECT sum(json_extract(body,'$.data.response.usage.prompt_tokens')), "
        f"sum({cached_expr}), "
        f"sum(COALESCE(json_extract(body,'$.data.response.usage.prompt_cache_miss_tokens'),"
        f" json_extract(body,'$.data.response.usage.prompt_tokens') - {cached_expr})) "
        "FROM events WHERE json_extract(body,'$.event_type')='model_response' "
        "AND json_extract(body,'$.data.phase')='response'"
    ).fetchone()
    compactions = conn.execute(
        "SELECT count(*) FROM events WHERE json_extract(body,'$.event_type')='environment_event' "
        "AND json_extract(body,'$.data.kind')='day_session_compacted'"
    ).fetchone()[0]
    prompt, cached, uncached = row if row is not None else (None, None, None)
    return prompt, cached, uncached, compactions


def cmd_summary(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印运行摘要；运行目录里的两份 JSON 一律按 UTF-8 读；条数走类型表达式索引，不读事件正文。"""
    total = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    counts = sorted(event_type_counts(conn), key=lambda item: (-item[1], item[2]))
    manifest = _manifest(a.run_dir)
    status = json.loads((a.run_dir / "status.json").read_text(encoding="utf-8"))
    prompt, cached, uncached, compactions = _summary_cache_and_compaction(conn)
    ratio = f"{cached / prompt:.1%}" if prompt and cached is not None else "—"
    print(f"run_id     : {manifest.get('run_id')}")
    print(f"状态       : {status.get('status')} ({status.get('phase')})")
    print(f"事件总数   : {total}")
    print(f"缓存令牌   : 命中 {cached if cached is not None else '—'} / "
          f"未命中 {uncached if uncached is not None else '—'} / 命中率 {ratio}")
    print(f"日内压缩   : {compactions} 次")
    for name, count, _ in counts:
        print(f"  {name:24s} {count}")


def cmd_events(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印事件流（一行一事件，可过滤）；名称与订单记录在筛选之前读一次。"""
    display = display_context(conn, a.run_dir)
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
    for r in (_rehydrated(conn, row) for row in rows):
        agent = f"a{r['agent_id']}" if r.get("agent_id") else "  "
        if a.full:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            continue
        brief = _brief(r, display)
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'], display.zone)} {agent} {r['event_type']:20s} {brief}")


def _brief(r: dict, display: Display) -> str:
    """@brief 按事件类型提取一行摘要；受理、拒绝、记忆、请求四种行直接取当前格式的键，缺键照 KeyError 失败。"""
    d = r["data"]
    t = r["event_type"]
    if t == "action_requested":
        return f"{d['tool_name']} {json.dumps(d['request'], ensure_ascii=False)[:80]}"
    if t == "action_accepted":
        phrase = _activity_brief(d, display) if "activity" in d else d["message"]
        return f"{phrase} → {d['status']}"
    if t == "action_rejected":
        return f"{d['error_code']}: {d['reason']}"
    if t == "environment_event":
        return _environment_brief(d, display)
    if t == "activity_progress":
        return f"{_activity_brief(d, display)} → {d['activity']['status']}"
    if t == "memory_updated":
        return f"{len(d['episodes'])} 条"
    if r["resource_changes"]:
        return json.dumps(r["resource_changes"], ensure_ascii=False)[:100]
    return json.dumps({k: v for k, v in d.items() if k not in ("request", "response")}, ensure_ascii=False)[:90]


def _activity_brief(d: dict, display: Display) -> str:
    """@brief 受理或进度事件里的活动：出行和睡眠写活动名，其余写这条事件指向的那一条内容的写法。"""
    snapshot = d["activity"]
    if snapshot["kind"] in NAMED_KINDS:
        return snapshot["name"]
    return activity_phrase(display.names, snapshot, [event_content_id(d)], display.orders)


def _environment_brief(d: dict, display: Display) -> str:
    """@brief 环境事件的一行摘要：三种手机事件写写法再跟起止或订单身份，停留进出写地点。"""
    kind = d.get("kind")
    if kind == "app_foreground":
        span = f"{_t(d['interval_start'], display.zone)} → {_t(d['interval_end'], display.zone)}"
        return f"{phone_phrase(display.names, d)} {span}"
    if kind in NOTICE_KINDS:
        return f"{phone_phrase(display.names, d)} {' '.join(d['order_ids'])}"
    if kind in ("location_entered", "location_left"):
        return f"{kind} {d['location_id']} {d.get('entry_kind', '')}".rstrip()
    if kind == "day_session_compacted":
        return (f"day_session_compacted #{d.get('compaction_count', '?')} "
                f"{d.get('before_tokens')}->{d.get('after_tokens')}t "
                f"folded={d.get('folded_groups')} kept={d.get('kept_tail_groups')}")
    return f"{kind} {d.get('scheduled_id','')}"


def cmd_event(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印单个事件全文。"""
    rows = [_rehydrated(conn, row) for row in _rows(conn, "event_seq=?", (a.seq,))]
    print(json.dumps(rows[0], ensure_ascii=False, indent=1) if rows else f"seq {a.seq} 不存在")


def cmd_money(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印全部资金流水与期末余额。"""
    zone = _zone(a.run_dir)
    balances: dict[str, int] = {}
    for r in _rows(conn, "json_extract(body,'$.resource_changes')!='{}'"):
        changes = r["resource_changes"]
        if not changes:
            continue
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'], zone)} {r['event_type']:18s} "
              f"{json.dumps(changes, ensure_ascii=False)}")
        for acct, delta in changes.items():
            balances[acct] = balances.get(acct, 0) + delta
    print("── 期末余额 ──")
    for acct in sorted(balances):
        print(f"  {acct:32s} {balances[acct]:>12,}")


def cmd_body(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印身体轨迹（时间/能量/睡眠不足账/饥饿与困意分级），读观察事件的 body.values 与 body.levels。"""
    zone = _zone(a.run_dir)
    where = "json_extract(body,'$.event_type')='observation'"
    args: tuple = ()
    if a.agent is not None:
        where += " AND json_extract(body,'$.agent_id')=?"
        args = (a.agent,)
    prev = None
    for r in _rows(conn, where, args):
        b = r["data"].get("body") or {}
        values, levels = b.get("values") or {}, b.get("levels") or {}
        cur = (values.get("energy_kcal"), values.get("sleep_deficit_hours"),
               levels.get("appetite"), levels.get("sleepiness"))
        if cur == prev or cur[0] is None:
            continue
        prev = cur
        print(f"[{r['seq']:>5}] {_t(r['occurred_at'], zone)} a{r['agent_id']}  能量={cur[0]:>7.1f}  "
              f"睡眠不足={cur[1]:>5.2f}h  饥饿={cur[2]}  困意={cur[3]}")


def cmd_flows(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印资源流水分录。"""
    zone = _zone(a.run_dir)
    rows = _rows(conn, "json_extract(body,'$.data.resource_flows') IS NOT NULL")
    if a.tail:
        rows = rows[-a.tail:]
    for r in rows:
        for f in r["data"]["resource_flows"]:
            print(f"[{r['seq']:>5}] {_t(r['occurred_at'], zone)} a{r['agent_id']}  {f['resource']:>18s} "
                  f"{f['direction']:>6s} {f['amount']:>10.2f}  {f['cause']}")


def _select(conn: sqlite3.Connection, event_type: str, columns: str, kind: str | None = None) -> list[tuple]:
    """@brief 只取某类事件的指定列（SQL 表达式，不解析整条事件体），按序号升序；环境事件可再按 data.kind 过滤。"""
    sql = (f"SELECT {columns} FROM events WHERE event_seq>? AND event_seq<=? "
           "AND json_extract(body, '$.event_type') = ?")
    args: list = [event_type]
    if kind is not None:
        sql += " AND json_extract(body, '$.data.kind') = ?"
        args.append(kind)
    return list(_paged(conn, sql + " ORDER BY event_seq", tuple(args)))


def _merged(intervals: list[tuple[int, int]]) -> list[list[int]]:
    """@brief 把区间合并为互不重叠、按起点升序的并集。"""
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _uncovered_seconds(base: list[list[int]], cover: list[list[int]]) -> int:
    """@brief 并集 base 中不被并集 cover 覆盖的总秒数（两者都已合并、按起点升序，线性扫描）。"""
    total, first = 0, 0
    for start, end in base:
        while first < len(cover) and cover[first][1] <= start:
            first += 1
        cursor, index = start, first
        while index < len(cover) and cover[index][0] < end:
            total += max(0, cover[index][0] - cursor)
            cursor = max(cursor, cover[index][1])
            index += 1
        total += max(0, end - cursor)
    return total


def _valid_interval(start: object, end: object) -> bool:
    """@brief 结算区间两端都是整数秒且终点晚于起点。"""
    return isinstance(start, int) and isinstance(end, int) and end > start


def idle_seconds(conn: sqlite3.Connection) -> dict[int, int]:
    """@brief 无进行中活动时长：每人共同身体结算区间的并集，减去其活动实际区间（activity_progress）的并集。"""
    settled: dict[int, list] = defaultdict(list)
    active: dict[int, list] = defaultdict(list)
    columns = ("json_extract(body, '$.agent_id'), json_extract(body, '$.data.interval_start'), "
               "json_extract(body, '$.data.interval_end')")
    for target, rows in ((settled, _select(conn, "environment_event", columns, "body_settlement")),
                         (active, _select(conn, "activity_progress", columns))):
        for agent, start, end in rows:
            if _valid_interval(start, end):
                target[agent].append((start, end))
    return {agent: _uncovered_seconds(_merged(spans), _merged(active[agent]))
            for agent, spans in sorted(settled.items())}


def progress_report_wakes(conn: sqlite3.Connection) -> dict[str, int]:
    """@brief 播报唤醒占比：提供决策的共同身体结算（decision_offered 为真）中，带与仅因 progress_report_due 的次数。"""
    offered = [set(json.loads(reasons or "[]")) for reasons, flag in _select(
        conn, "environment_event",
        "json_extract(body, '$.data.wake_reasons'), json_type(body, '$.data.decision_offered')",
        "body_settlement") if flag == "true"]
    return {"offered": len(offered),
            "with_report": sum(PROGRESS_REPORT_REASON in reasons for reasons in offered),
            "only_report": sum(reasons == {PROGRESS_REPORT_REASON} for reasons in offered)}


def hungriest_level(conn: sqlite3.Connection) -> str | None:
    """@brief 最低饥饿分级：观察 body.levels.appetite 中最饿的一档；没有可识别的分级时为 None。"""
    seen = {level for (level,) in _select(conn, "observation", "json_extract(body, '$.data.body.levels.appetite')")}
    ranked = [level for level in APPETITE_ORDER if level in seen]
    return ranked[-1] if ranked else None


def food_receipts(conn: sqlite3.Connection) -> list[tuple[int, str, int]]:
    """@brief 本人食品收货的真实订单身份和发生秒数，不构造用餐活动。"""
    rows = _select(conn, "environment_event",
        "json_extract(body, '$.agent_id'), json_extract(body, '$.data.order_id'), "
        "json_extract(body, '$.occurred_at'), json_extract(body, '$.data.category')", "goods_received")
    return [(agent, order, moment) for agent, order, moment, category in rows
            if category in {"meal", "snack", "drink"}]


def _local_date(epoch: int, zone: ZoneInfo) -> str:
    """@brief 场景时区下的本地日期（ISO 格式）。"""
    return datetime.fromtimestamp(epoch, zone).date().isoformat()


def _energy_points(conn: sqlite3.Connection, zone: ZoneInfo) -> dict[tuple[int, str], list[tuple[int, float]]]:
    """@brief 共同身体结算记下的能量点：区间起点取 before、终点取 after 的 energy_kcal，按（人物，本地日）分组。"""
    points: dict[tuple[int, str], list[tuple[int, float]]] = defaultdict(list)
    rows = _select(conn, "environment_event",
                   "json_extract(body, '$.agent_id'), json_extract(body, '$.data.interval_start'), "
                   "json_extract(body, '$.data.body_settlement.before.energy_kcal'), "
                   "json_extract(body, '$.data.interval_end'), "
                   "json_extract(body, '$.data.body_settlement.after.energy_kcal')", "body_settlement")
    for agent, start, before, end, after in rows:
        for moment, energy in ((start, before), (end, after)):
            if isinstance(moment, int) and isinstance(energy, (int, float)):
                points[(agent, _local_date(moment, zone))].append((moment, float(energy)))
    return points


def _location_meals(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """@brief 在地点吃的一餐：data.content.completion_effects.food 不为 null 的 activity_progress（两个档位都算），
    每条一项，返回人物身份与事件时刻。"""
    rows = _select(conn, "activity_progress",
                   "json_extract(body, '$.agent_id'), json_extract(body, '$.occurred_at'), "
                   "json_extract(body, '$.data.content.completion_effects.food')")
    return [(agent, moment) for agent, moment, food in rows if food is not None]


def lowest_energy_before_meal(conn: sqlite3.Connection, zone: ZoneInfo) -> list[dict]:
    """@brief 首次进食前最低能量：每人每个本地日第一次进食之前，共同身体结算的最低 energy_kcal。

    与进食次数读同一组进食时刻：食品收货与在地点吃的一餐（_location_meals），当天最早的一次取两者中较早的那个。
    当日没有进食时取全日最低并标 ate=False；进食前没有结算记录时 energy_kcal 为 None。
    """
    first_meal: dict[tuple[int, str], int] = {}
    meals = [(agent, moment) for agent, _, moment in food_receipts(conn)] + _location_meals(conn)
    for agent, moment in meals:
        key = (agent, _local_date(moment, zone))
        first_meal[key] = min(first_meal.get(key, moment), moment)
    report = []
    for (agent, date), points in sorted(_energy_points(conn, zone).items()):
        limit = first_meal.get((agent, date))
        before = [energy for moment, energy in points if limit is None or moment <= limit]
        report.append({"agent_id": agent, "date": date, "ate": limit is not None,
                       "energy_kcal": min(before) if before else None})
    return report


def meal_count(conn: sqlite3.Connection) -> int:
    """@brief 进食次数：本人零食、食品、饮料收货的不同订单数，加上在地点吃的一餐的条数（每条计一次）。"""
    return len({(agent, order_id) for agent, order_id, _ in food_receipts(conn)}) + len(_location_meals(conn))


def walking_share(conn: sqlite3.Connection) -> dict:
    """@brief 步行占比：自带步行的出行时长 / 全部出行时长；没有步行秒数时 available 为 False。"""
    seconds: Counter = Counter()
    for mode, start, end in _select(conn, "activity_progress",
                                     "json_extract(body, '$.data.activity.travel_method_id'), "
                                     "json_extract(body, '$.data.interval_start'), "
                                     "json_extract(body, '$.data.interval_end')"):
        if mode is not None and _valid_interval(start, end):
            seconds[mode] += end - start
    return {"walking_seconds": seconds["walking"], "travel_seconds": sum(seconds.values()),
            "available": seconds["walking"] > 0}


def early_reminders(conn: sqlite3.Connection, zone: ZoneInfo) -> list[dict]:
    """@brief 零点计划是否含早于 08:30 的提醒：逐份 day_plan_generated 列出最早提醒的本地时刻与判定。"""
    report = []
    for agent, occurred_at, goals in _select(conn, "environment_event",
                                             "json_extract(body, '$.agent_id'), json_extract(body, '$.occurred_at'), "
                                             "json_extract(body, '$.data.day_plan.goals')", "day_plan_generated"):
        moments = sorted(datetime.fromtimestamp(goal["remind_at"], zone) for goal in json.loads(goals or "[]")
                         if isinstance(goal, dict) and isinstance(goal.get("remind_at"), int))
        earliest = moments[0] if moments else None
        report.append({"agent_id": agent, "date": _local_date(occurred_at, zone),
                       "earliest": earliest.strftime("%H:%M") if earliest else None,
                       "early": earliest is not None and (earliest.hour, earliest.minute) < EARLY_REMINDER})
    return report


def rerun_metrics(conn: sqlite3.Connection) -> dict:
    """@brief 汇总「真实复验」七项指标（只读；本地日与提醒时刻按世界状态记录的场景时区）。"""
    zone = ZoneInfo(json.loads(conn.execute("SELECT body FROM world_state").fetchone()[0])["timezone"])
    return {"idle_seconds": idle_seconds(conn),
            "progress_report_wakes": progress_report_wakes(conn), "hungriest_level": hungriest_level(conn),
            "lowest_energy_before_meal": lowest_energy_before_meal(conn, zone), "meal_count": meal_count(conn),
            "walking_share": walking_share(conn), "early_reminders": early_reminders(conn, zone)}


def _duration(seconds: int) -> str:
    """@brief 人读时长「H小时M分（N 秒）」。"""
    return f"{seconds // 3600}小时{seconds % 3600 // 60}分（{seconds} 秒）"


def _ratio(part: int, whole: int) -> str:
    """@brief 分数与百分比；分母为 0 时不适用。"""
    return f"{part}/{whole}（{part / whole:.0%}）" if whole else "不适用（无提供决策的唤醒）"


def rerun_lines(metrics: dict) -> list[str]:
    """@brief 把七项指标排成中文报告行（只报告，不作门禁）。"""
    idle = metrics["idle_seconds"]
    wakes, walking = metrics["progress_report_wakes"], metrics["walking_share"]
    energy = "；".join(
        f"人物 {row['agent_id']} {row['date']} "
        + ("无结算记录" if row["energy_kcal"] is None else f"{row['energy_kcal']:.1f} kcal")
        + ("" if row["ate"] else "（当日无进食）") for row in metrics["lowest_energy_before_meal"])
    if not walking["available"]:
        walk = "不适用（没有步行）"
    elif not walking["travel_seconds"]:
        walk = "不适用（无出行）"
    else:
        walk = _ratio(walking["walking_seconds"], walking["travel_seconds"])
    reminders = "；".join(
        f"人物 {row['agent_id']} {row['date']} " + ("是" if row["early"] else "否")
        + (f"（最早提醒 {row['earliest']}）" if row['earliest'] else "（未设提醒）")
        for row in metrics["early_reminders"])
    return [
        "真实复验七项指标（只报告，不作门禁）",
        f"1. 无进行中活动总时长：{_duration(sum(idle.values()))}"
        + "".join(f"；人物 {agent} {_duration(value)}" for agent, value in idle.items()),
        f"2. 播报唤醒占比：带 progress_report_due {_ratio(wakes['with_report'], wakes['offered'])}；"
        f"仅因它 {_ratio(wakes['only_report'], wakes['offered'])}",
        f"3. 最低饥饿分级：{metrics['hungriest_level'] or '不适用（无饥饿分级观察）'}",
        f"4. 首次进食前最低能量：{energy or '不适用（无共同身体结算）'}",
        f"5. 进食次数：{metrics['meal_count']}",
        f"6. 步行占比：{walk}",
        f"7. 零点计划含早于 08:30 的提醒：{reminders or '不适用（无零点计划）'}",
    ]


def cmd_rerun(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 打印「真实复验」七项指标。"""
    print("\n".join(rerun_lines(rerun_metrics(conn))))


#: 行动请求的 SQL 判定（与 footprints.store 的行动请求谓词同义）。
_ACTION_REQUEST = ("json_extract(body,'$.event_type')='model_request' AND json_extract(body,'$.data.purpose') IS NULL "
                   "AND json_extract(body,'$.data.request.tool_choice')='auto' AND EXISTS "
                   "(SELECT 1 FROM json_each(body,'$.data.request.tools') "
                   "WHERE json_extract(value,'$.function.name')='start_main_activity')")


def _manifest(run_dir: Path) -> dict:
    """@brief 读取运行清单。"""
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def cmd_workday(conn: sqlite3.Connection, a: argparse.Namespace) -> None:
    """@brief 工作安排与下班、到家窗口取已核对多人物人设与配置原文身份的完整环境，不读人物工作区。"""
    from worldmock.configuration.inputs.environment import load_environment
    from tools.analysis.behavior.workday import workday_inputs, workday_reports

    manifest = _manifest(a.run_dir)
    inputs = load_environment(a.environment)
    manifest_personas = {int(agent_id): digest for agent_id, digest in manifest["persona_sha256s"].items()}
    if (manifest_personas != dict(inputs.persona_sha256s)
            or inputs.config_sha256 != manifest["config_sha256"]):
        raise SystemExit("Environment inputs do not match the run manifest")
    report = workday_reports(_rows(conn), inputs.config.timezone, workday_inputs(inputs))
    print(json.dumps({"run_id": manifest["run_id"], **report}, ensure_ascii=False, indent=2))


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
    sub.add_parser("rerun")
    pw = sub.add_parser("workday")
    pw.add_argument("--environment", type=Path, required=True)
    args = parser.parse_args()
    conn = _connect(args.run_dir)
    {"summary": cmd_summary, "events": cmd_events, "event": cmd_event, "money": cmd_money, "body": cmd_body,
     "flows": cmd_flows, "rerun": cmd_rerun, "workday": cmd_workday}[args.cmd](conn, args)
