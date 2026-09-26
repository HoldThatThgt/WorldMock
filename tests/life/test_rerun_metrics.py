"""@brief 真实复验统计子命令（scripts/inspect_run.py rerun）在合成运行库上逐项计算八项指标。

规格验收台账「真实复验」行：无进行中活动总时长、同刻轮数分布、播报唤醒占比、最低饥饿分级、首次进食前最低能量、
进食次数、步行占比与零点计划是否含早于 08:30 的提醒，全部只读计算、只报告。真实链路用例由引擎与存储产生运行库，
手工用例用存储写入事件，覆盖真实链路走不到的取值（空档、只有驾车、当日无进食、无零点计划）。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agentsociety2.society import DirectToolCall

from tests.life.conftest import StubPlanDayClient, advance_until_ready, context, settle_day_plans, start_activity
from tests.life.test_dayrun_restaurants import MEAL, RESTAURANT, SUPPLY, _finish, _start
from worldmock.footprints.models import EventDraft
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.store import FootprintStore

REPO_ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Asia/Shanghai")
DAY0 = int(datetime(2026, 1, 5, tzinfo=TZ).timestamp())
OPENING = int(datetime(2026, 1, 5, 10, tzinfo=TZ).timestamp())


def _script():
    """@brief 按文件加载只读统计脚本。"""
    spec = importlib.util.spec_from_file_location("inspect_run", REPO_ROOT / "scripts" / "inspect_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metrics(run_dir: Path) -> tuple[dict, list[str]]:
    """@brief 以只读连接计算八项指标并排出报告行。"""
    module = _script()
    conn = module._connect(run_dir)
    try:
        metrics = module.rerun_metrics(conn)
    finally:
        conn.close()
    return metrics, module.rerun_lines(metrics)


def _live_day(engine) -> dict:
    """@brief 真实一天：零点计划带 08:00 提醒；同一秒先被拒再入睡；步行去餐馆、等开门、到店买饭、吃完收尾。"""
    settle_day_plans(engine, StubPlanDayClient(goals=[
        {"intent_kind": "工作", "goal": "八点前出门去吃早饭", "remind_time": "08:00"}]))
    decision = context(engine, "unlisted")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "action-unlisted", "start_activity",
                                        start_activity("等待", "不存在的对象")))
    rejected = engine.commit_decision_round("unlisted")[0]
    assert (rejected.status, rejected.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    _, sleep = _start(engine, "sleep", "self", None, "睡觉")
    _finish(engine, sleep.activity_id)
    _, walk = _start(engine, "to-restaurant", "location", RESTAURANT, "步行")
    _finish(engine, walk.activity_id)
    for step in range(16):
        if engine.state.current_time >= OPENING:
            break
        _, waiting = _start(engine, f"wait-{step}", "self", None, "等待")
        _finish(engine, waiting.activity_id)
    _start(engine, "buy-meal", "supply", SUPPLY, "到店")
    assert advance_until_ready(engine)
    _, eating = _start(engine, "eat-meal", "holding", MEAL, "食用")
    _finish(engine, eating.activity_id)
    return {"walk": walk.activity_id, "eat": eating.activity_id}


def test_rerun_metrics_on_a_real_engine_day(engine_factory) -> None:
    """真实引擎与存储产生的运行库：八项指标与引擎事实、质量报告 decision_rhythm 的同一口径一致。"""
    engine = engine_factory()
    ids = _live_day(engine)
    events = engine.store.read_events(engine.store.last_event_seq)
    rhythm = build_quality_report(events, [], engine.store.manifest)["decision_rhythm"]
    metrics, lines = _metrics(engine.store.run_dir)

    assert metrics["idle_seconds"] == {1: 0}
    assert metrics["same_moment_rounds"] == rhythm["same_moment_round_counts"]
    assert metrics["same_moment_rounds"]["2"] >= 1
    wakes = metrics["progress_report_wakes"]
    offered = [event.data["wake_reasons"] for event in events if event.event_type == "environment_event"
               and event.data.get("kind") == "body_settlement" and event.data["decision_offered"] is True]
    assert wakes == {"offered": rhythm["offered_boundaries"],
                     "with_report": rhythm["wake_reason_counts"].get("progress_report_due", 0),
                     "only_report": sum(reasons == ["progress_report_due"] for reasons in offered)}
    assert wakes["offered"] == len(offered) > 0
    order = _script().APPETITE_ORDER
    levels = [event.data["body"]["levels"]["appetite"] for event in events if event.event_type == "observation"]
    assert metrics["hungriest_level"] == max(levels, key=order.index)

    meal = [event for event in events if event.event_type == "activity_progress" and event.activity_id == ids["eat"]]
    start = meal[0].data["interval_start"]
    at_meal = next(event for event in events if event.event_type == "environment_event"
                   and event.data.get("kind") == "body_settlement" and event.data["interval_start"] == start)
    energy = at_meal.data["body_settlement"]["before"]["energy_kcal"]
    assert metrics["lowest_energy_before_meal"] == [
        {"agent_id": 1, "date": "2026-01-05", "ate": True, "energy_kcal": energy}]
    assert metrics["meal_count"] == 1
    walked = engine.state.activities[ids["walk"]].elapsed_seconds
    assert metrics["walking_share"] == {"walking_seconds": walked, "travel_seconds": walked, "available": True}
    assert metrics["early_reminders"] == [{"agent_id": 1, "date": "2026-01-05", "earliest": "08:00", "early": True}]
    assert lines[7] == f"7. 步行占比：{walked}/{walked}（100%）"
    assert lines[8] == "8. 零点计划含早于 08:30 的提醒：人物 1 2026-01-05 是（最早提醒 08:00）"
    assert lines[1] == "1. 无进行中活动总时长：0小时0分（0 秒）；人物 1 0小时0分（0 秒）"


def _at(hour: int, minute: int = 0, day: int = 0) -> int:
    """@brief 2026-01-05 起第 day 天的本地时刻。"""
    return DAY0 + day * 86400 + hour * 3600 + minute * 60


def _settlement(agent: int, start: int, end: int, energy: tuple[float, float], **fields) -> EventDraft:
    """@brief 共同身体结算：区间、前后能量与（提供决策时的）唤醒原因。"""
    data = {"kind": "body_settlement", "interval_start": start, "interval_end": end, "activity_ids": [],
            "body_settlement": {"before": {"energy_kcal": energy[0]}, "after": {"energy_kcal": energy[1]}},
            "decision_offered": fields.pop("offered", False), "wake_reasons": fields.pop("reasons", [])}
    return EventDraft(event_type="environment_event", occurred_at=end, agent_id=agent,
                      visibility="system", data=data)


def _progress(agent: int, activity_id: str, span: tuple[int, int], **data) -> EventDraft:
    """@brief 一段活动进展（实际结算区间），可附出行方式与食用记录。"""
    snapshot = {"activity_id": activity_id, "kind": data.pop("kind", "wait"), "travel_mode": data.pop("mode", None)}
    return EventDraft(event_type="activity_progress", occurred_at=span[1], agent_id=agent, activity_id=activity_id,
                      data={"activity": snapshot, "interval_start": span[0], "interval_end": span[1], **data})


def _eating(quantity: float, use_mode: str = "eat") -> dict:
    """@brief activity_progress.data.consumption：本区间用量与使用方式。"""
    return {"consumption": {"use_mode": use_mode, "items": [{"product_id": "meal", "interval_quantity": quantity}]}}


def _event(event_type: str, occurred_at: int, agent: int = 1, **data) -> EventDraft:
    """@brief 其他事件（观察、决策开始、零点计划）。"""
    return EventDraft(event_type=event_type, occurred_at=occurred_at, agent_id=agent, data=data)


def _store(run_dir: Path, events: list[EventDraft]) -> Path:
    """@brief 用存储写入一次合成运行（事件按发生时刻排序，世界状态带场景时区）。"""
    ordered = sorted(events, key=lambda item: item.occurred_at)
    end = max((item.occurred_at for item in ordered), default=DAY0)
    store = FootprintStore.create(run_dir, {
        "run_id": "rerun", "agent_ids": [1, 2],
        "window": {"start": DAY0, "end": DAY0 + 3 * 86400, "timezone": "Asia/Shanghai"},
    }, {"version": 0, "current_time": DAY0, "timezone": "Asia/Shanghai"})
    if ordered:
        store.commit({"version": 1, "current_time": end, "timezone": "Asia/Shanghai"}, ordered)
    store.close()
    return run_dir


def _plan(occurred_at: int, *reminders: int | None) -> EventDraft:
    """@brief 零点计划登记：每个目标带提醒时刻（或不设提醒）。"""
    goals = [{"intent_kind": "生活", "goal": f"目标{index}", "registered_at": occurred_at, "remind_at": remind}
             for index, remind in enumerate(reminders)]
    return EventDraft(event_type="environment_event", occurred_at=occurred_at, agent_id=1, visibility="system",
                      data={"kind": "day_plan_generated", "day_plan": {"goals": goals}})


def _observation(occurred_at: int, appetite: str, *modes: str, agent: int = 1) -> EventDraft:
    """@brief 观察：饥饿分级与本轮列出的路线方式。"""
    return _event("observation", occurred_at, agent, body={"levels": {"appetite": appetite}},
                  routes=[{"route_id": f"route-{mode}", "mode": mode} for mode in modes])


def test_rerun_metrics_on_a_stored_run_with_gaps_driving_only_and_missed_meals(tmp_path: Path) -> None:
    """存储写入的运行：空档按并集相减，同刻轮数按（人物，秒），只有驾车时步行占比不适用，当日无进食取全日最低。"""
    events = [
        _settlement(1, _at(0), _at(1), (1600, 1500)),
        _settlement(1, _at(1), _at(2), (1500, 1420), offered=True, reasons=["progress_report_due"]),
        _settlement(1, _at(2), _at(3), (1420, 1300), offered=True, reasons=["planned_end", "progress_report_due"]),
        _settlement(1, _at(3), _at(4), (1300, 1700), offered=True, reasons=["felt_band_change"]),
        _settlement(1, _at(4), _at(5), (1700, 1650), reasons=["progress_report_due"]),
        _settlement(1, _at(23), _at(1, day=1), (900, 850)),
        _settlement(1, _at(1, day=1), _at(2, day=1), (850, 700)),
        _settlement(2, _at(0), _at(2), (1600, 1400)),
        _progress(1, "wait-a", (_at(0, 10), _at(0, 30))),
        _progress(1, "wait-b", (_at(0, 20), _at(0, 40))),
        _progress(1, "drive", (_at(1), _at(1, 30)), kind="travel", mode="driving"),
        _progress(1, "eat", (_at(3), _at(3, 20)), kind="consume", **_eating(0.75)),
        _progress(1, "eat", (_at(3, 20), _at(3, 30)), kind="consume", **_eating(0.25)),
        _progress(1, "empty-plate", (_at(4), _at(4, 10)), kind="consume", **_eating(0)),
        _progress(1, "lotion", (_at(4, 10), _at(4, 20)), kind="consume", **_eating(1, "use")),
        _progress(1, "late-snack", (_at(0, 10, day=1), _at(0, 20, day=1)), kind="consume", **_eating(1)),
        _progress(2, "work", (_at(0), _at(2)), kind="work"),
        *(_event("decision_started", _at(1), wake_reasons=[]) for _ in range(3)),
        _event("decision_started", _at(1), 2, wake_reasons=[]),
        _event("decision_started", _at(2), wake_reasons=[]),
        _observation(_at(0), "有点想吃", "driving"),
        _observation(_at(2), "饿得发慌", "driving"),
        _observation(_at(3), "很饿", "driving"),
        _observation(_at(4), "饿得出奇", "driving", agent=2),
        _plan(_at(0), _at(9), _at(10)),
        _plan(_at(0, day=1), None),
        _plan(_at(0, day=2), _at(8, 29, day=2)),
    ]
    metrics, lines = _metrics(_store(tmp_path / "run", events))
    # 00:00–05:00 的结算并集中有 30+30+30+20 分钟活动（两段等待重叠只算一次），23:00–次日 02:00 只有 10 分钟。
    idle = (5 * 3600 - (30 + 30 + 30 + 20) * 60) + (3 * 3600 - 10 * 60)
    assert metrics["idle_seconds"] == {1: idle, 2: 0}
    assert metrics["same_moment_rounds"] == {"1": 2, "3": 1}
    assert metrics["progress_report_wakes"] == {"offered": 3, "with_report": 2, "only_report": 1}
    assert metrics["hungriest_level"] == "饿得发慌"
    assert metrics["lowest_energy_before_meal"] == [
        {"agent_id": 1, "date": "2026-01-05", "ate": True, "energy_kcal": 1300.0},
        {"agent_id": 1, "date": "2026-01-06", "ate": True, "energy_kcal": None},
        {"agent_id": 2, "date": "2026-01-05", "ate": False, "energy_kcal": 1400.0},
    ]
    assert metrics["meal_count"] == 2
    assert metrics["walking_share"] == {"walking_seconds": 0, "travel_seconds": 1800, "available": False}
    assert metrics["early_reminders"] == [
        {"agent_id": 1, "date": "2026-01-05", "earliest": "09:00", "early": False},
        {"agent_id": 1, "date": "2026-01-06", "earliest": None, "early": False},
        {"agent_id": 1, "date": "2026-01-07", "earliest": "08:29", "early": True},
    ]
    assert lines == [
        "真实复验八项指标（只报告，不作门禁）",
        f"1. 无进行中活动总时长：{idle // 3600}小时{idle % 3600 // 60}分（{idle} 秒）；"
        f"人物 1 {idle // 3600}小时{idle % 3600 // 60}分（{idle} 秒）；人物 2 0小时0分（0 秒）",
        "2. 同刻轮数分布：1 轮 × 2、3 轮 × 1",
        "3. 播报唤醒占比：带 progress_report_due 2/3（67%）；仅因它 1/3（33%）",
        "4. 最低饥饿分级：饿得发慌",
        "5. 首次进食前最低能量：人物 1 2026-01-05 1300.0 kcal；人物 1 2026-01-06 无结算记录；"
        "人物 2 2026-01-05 1400.0 kcal（当日无进食）",
        "6. 进食次数：2",
        "7. 步行占比：不适用（只有驾车）",
        "8. 零点计划含早于 08:30 的提醒：人物 1 2026-01-05 否（最早提醒 09:00）；人物 1 2026-01-06 否（未设提醒）；"
        "人物 1 2026-01-07 是（最早提醒 08:29）",
    ]


def test_rerun_command_reports_not_applicable_metrics_for_a_run_without_records(tmp_path: Path) -> None:
    """命令行子命令只读打开运行目录；没有决策、结算、出行与零点计划的运行逐项报「不适用」，库文件不变。"""
    run_dir = _store(tmp_path / "run", [_observation(_at(0), "不饿", "walking", "driving")])
    before = (run_dir / "footprints.sqlite").read_bytes()
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "inspect_run.py"), str(run_dir), "rerun"],
                            capture_output=True, text=True, encoding="utf-8", check=True,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.stdout.splitlines() == [
        "真实复验八项指标（只报告，不作门禁）",
        "1. 无进行中活动总时长：0小时0分（0 秒）",
        "2. 同刻轮数分布：不适用（无决策）",
        "3. 播报唤醒占比：带 progress_report_due 不适用（无提供决策的唤醒）；仅因它 不适用（无提供决策的唤醒）",
        "4. 最低饥饿分级：不饿",
        "5. 首次进食前最低能量：不适用（无共同身体结算）",
        "6. 进食次数：0",
        "7. 步行占比：不适用（无出行）",
        "8. 零点计划含早于 08:30 的提醒：不适用（无零点计划）",
    ]
    assert (run_dir / "footprints.sqlite").read_bytes() == before
