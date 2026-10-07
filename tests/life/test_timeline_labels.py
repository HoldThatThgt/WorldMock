"""时间线只出现中文名称（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-时间线只出现中文名称 ）：真实引擎跑交谈、通话、工作与等待，导出的时间线按人物姓名、
联系人称谓与任务标题写中文名称，没有 call、work 这类英文种类名，也没有 task- 形态的标识；导出的 quality.json 时间线标签
检查为零，并与同批事件重算一致。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.life.conftest import start_activity, submit_round
from tests.life.routing_fixtures import COLLEAGUE_TARGET, OFFICE, PERSON_TARGET, act, open_world, run_until, stage, place, with_colleague
from worldmock.footprints.export import export_run
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.timeline_labels import label_findings
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.models import active_activities
from worldmock.life.readable import ENGLISH_KIND_NAMES


def _idle(engine) -> bool:
    """@brief 人物 1 此刻可以决策，且没有进行中或待收尾的活动。"""
    return (1 in engine.scheduling_state().ready_agent_ids and not active_activities(engine.context.state, 1)
            and not pending_conclusions(engine.context.state, 1))


def _at_office(engine) -> None:
    """@brief 人物在办公室工作；主题一直存在，不再需要任务开放事件。"""
    place(engine, 1, OFFICE)


def _labels(markdown: str) -> list[str]:
    """@brief 时间线表格各行的活动列。"""
    return [line.split(" | ")[1] for line in markdown.splitlines() if re.match(r"^\| \d\d:\d\d–", line)]


def test_exported_timeline_names_chats_calls_and_work_in_chinese(engine_factory, tmp_path: Path) -> None:
    """交谈、通话与工作在导出的时间线里写「和李凡交谈」「和王磊通话」「工作「<任务标题>」」；两人的时间线都没有英文种类名
    与内部标识，quality.json 的 timeline_labels 两项为零且与同批事件重算一致。"""
    def named_people(document):
        with_colleague(document)
        document["agents"][0]["name"] = "陈曦"
        document["agents"][1]["name"] = "李凡"
    engine = open_world(engine_factory, mutate=named_people)
    first, second = engine.context.state.agents[1].name, engine.context.state.agents[2].name
    submit_round(engine, {1: ("start_activity", start_activity("交谈", f"人物：{second}")),
                          2: ("start_activity", start_activity("等待", "自己"))}, "timeline-invite")
    submit_round(engine, {2: ("start_activity", start_activity("交谈", f"人物：{first}"))}, "timeline-accept")
    called = act(engine, "timeline-call", start_activity("通话", COLLEAGUE_TARGET))
    assert called.status == "in_progress", (called.error_code, called.message)
    run_until(engine, _idle)
    topic = engine.context.scenario.work_topics[0]
    _at_office(engine)
    worked = act(engine, "timeline-work", start_activity("工作", f"工作：{topic.topic}"))
    assert worked.status == "in_progress", (worked.error_code, worked.message)
    run_until(engine, lambda current: current.context.state.activities[worked.activity_id].elapsed_seconds > 0)

    snapshots = {f"agent:{agent_id}": engine.context.store.assets.put_json({"agent_id": agent_id}, {"kind": "timeline_test"})
                 for agent_id in engine.context.state.agents}
    checkpoint = engine.checkpoint(snapshots)
    export_dir = tmp_path / "export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, export_dir)
    timelines = {record["agent_id"]: (export_dir / record["path"]).read_text(encoding="utf-8")
                 for record in manifest["timeline"] if record["date"] == "2026-01-05"}
    assert {"和李凡交谈", "和王磊通话", f"工作「{topic.topic}」"} <= set(_labels(timelines[1]))
    assert f"和{first}交谈" in _labels(timelines[2]) and second == "李凡"
    for markdown in timelines.values():
        assert not [(label, found) for label in _labels(markdown) for found in label_findings(label)]
        assert "task-" not in markdown and not set(re.findall(r"[a-z_]+", markdown)) & ENGLISH_KIND_NAMES

    quality = json.loads((export_dir / "quality.json").read_text(encoding="utf-8"))["timeline_labels"]
    assert quality["rows_checked"] == len(_labels(timelines[1])) + len(_labels(timelines[2]))
    assert (quality["english_kind_names"], quality["internal_ids"], quality["findings"]) == (0, 0, [])
    events = engine.context.store.read_events(checkpoint.last_event_seq)
    assert build_quality_report(events, [], engine.context.store.manifest)["timeline_labels"] == quality
