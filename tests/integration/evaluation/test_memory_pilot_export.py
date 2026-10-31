"""分层记忆试点指标读真实宿主写出的两天导出（tools/analysis/memory_pilot.py，docs/design/07-evaluation.md
「分层记忆试点指标」）。

真实 LifeEngine、LifeEnv 与 SQLite 上，脚本化模型经真实 LifeAgent 决策（current_contract_fixtures.decide）：第一天 21:00
起修订计划、下单一份食品、在家开始一项事、播报，其间按实际活动收尾，最后上床；用 engine.advance 推进过零点，以生产入口
settle_pending_memory_digests 与确定性桩生成日回顾，再做零点计划与节律；第二天睡醒再决策两次，睡到运行截止。写最终检查点后
用 export_run 导出，pilot_report 走一次真实认证。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.engine_fixture import engine_factory_at
from tests.footprints.recording import final_checkpoint
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import (
    MAIN_TOOL,
    NIGHT_SLEEP_MINUTES,
    SIDE_TOOL,
    StubMemoryDigestClient,
    StubPlanDayClient,
    main_arguments,
    side_arguments,
    stage_day_plan,
)
from tests.unit.evaluation.test_memory_pilot import CHECK_NAMES
from tools.analysis.memory_pilot import main, pilot_report
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.export.export import export_run
from worldmock.recording_engine.validation.validation import validate_export
from worldmock.runtime.lifecycle.runner import settle_pending_day_plans, settle_pending_memory_digests

#: 修订计划的参数。
PLAN = {"goals": [{"intent_kind": "休息", "goal": "早点睡", "remind_time": None, "around_time": None}]}
#: 在家歇一刻钟：夹具里家的内容单元「测试休息」。
REST = main_arguments("interact", "家", "测试休息")


def _evening(persona, config):
    """运行配置：2026-01-05（星期一）21:00 开始，次日 08:00 截止。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T21:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-06T08:00:00+08:00")


def _items(names, **extra) -> list[dict]:
    """控制工具的逐项参数：每项点名一项活动，自述一句。"""
    return [{"activity_name": name, "summary": "这件事先这样。", **extra} for name in names]


def _response(engine, label: str) -> ToolResponse:
    """按此刻的实际活动选一次回复：有已结束待收尾的活动就收尾，有进行中的就播报；都没有时今天还没歇过就在家歇一刻钟，
    歇过了就在家上床。"""
    activities = list(engine.context.state.activities.values())
    pending = [item.name for item in activities if item.status not in {"active", "paused"} and item.concluded_at is None]
    if pending:
        return ToolResponse("conclude_activity", {"items": _items(pending, memories=[], views=[]), "errands": []},
                            call_id=label)
    active = [item.name for item in activities if item.status == "active"]
    if active:
        return ToolResponse("report_status", {"items": _items(active), "errands": []}, call_id=label)
    if not any("测试休息" in item.name for item in activities):
        return ToolResponse(MAIN_TOOL, REST, call_id=label)
    return ToolResponse(MAIN_TOOL, main_arguments("sleep", str(NIGHT_SLEEP_MINUTES)), call_id=label)


def _advance(engine) -> None:
    """推进到下一个调度边界（零点任务由用例自己做）。"""
    state = engine.scheduling_state()
    engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)


async def _until_midnight(engine) -> None:
    """逐边界推进，人物就绪时按实际活动决策；停在零点（零点节律待判）。"""
    counter = 0
    while not engine.midnight_rhythm_pending:
        if engine.scheduling_state().ready_agent_ids:
            counter += 1
            await decide(engine, f"evening-{counter}", [_response(engine, f"evening-{counter}")])
        else:
            _advance(engine)


async def _two_day_export(engine, directory: Path) -> Path:
    """两天的真实宿主运行：第一天的决策、零点回顾与计划、第二天的两次决策，睡到截止后导出到 directory。"""
    stage_day_plan(engine, submitted=True)
    await decide(engine, "plan", [ToolResponse("revise_day_plan", PLAN, call_id="plan")])
    await decide(engine, "tea", [ToolResponse(SIDE_TOOL, side_arguments("purchase", "柠檬茶"), call_id="tea")])
    await _until_midnight(engine)
    model = {"requested_model": engine.context.config.model.requested_model}
    await settle_pending_memory_digests(engine, StubMemoryDigestClient(model))
    await settle_pending_day_plans(engine, StubPlanDayClient(model))
    engine.decide_midnight_rhythm()
    for label in ("wake", "morning"):
        while not engine.scheduling_state().ready_agent_ids:
            _advance(engine)
        await decide(engine, label, [_response(engine, label)])
    while engine.context.state.current_time < engine.context.state.end_time:
        assert not engine.scheduling_state().ready_agent_ids
        _advance(engine)
    final_checkpoint(engine)
    store = engine.context.store
    export_run(store, store.last_event_seq, directory)
    return directory


@pytest.fixture(scope="module")
def two_day_export(tmp_path_factory, frozen_inputs) -> Path:
    """模块共享的一份两天导出，用例只读或复制后再改。"""
    root = tmp_path_factory.mktemp("pilot-export")
    with engine_factory_at(root, frozen_inputs) as create:
        return asyncio.run(_two_day_export(create(mutate=_evening), root / "export"))


def _requested_tools(directory: Path) -> dict[str, list[str]]:
    """导出里每个本地日（上海）登记的世界请求工具名，按事件次序。"""
    tools: dict[str, list[str]] = {}
    for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["event_type"] == "action_requested":
            day = datetime.fromtimestamp(event["occurred_at"], ZoneInfo("Asia/Shanghai")).date().isoformat()
            tools.setdefault(day, []).append(event["data"]["tool_name"])
    return tools


def test_pilot_report_reads_a_real_two_day_export(two_day_export):
    tools = _requested_tools(two_day_export)
    assert {"revise_day_plan", MAIN_TOOL, SIDE_TOOL, "report_status"} <= set(tools["2026-01-05"])
    assert len(tools["2026-01-06"]) == 2
    report = pilot_report(two_day_export)
    metrics = report["metrics"]
    assert report["days"] == ["2026-01-05", "2026-01-06"]
    assert metrics["M9"]["decisions_per_day"]["2026-01-06"] == 2
    assert metrics["M10"]["periods"]["day"] == 1
    assert metrics["M3"]["periods"]["all"]["value"] == 1.0
    assert metrics["M6"]["memory_layer_by_observation"]["max"] <= 14000
    assert metrics["M9"]["chain"]["continuations"] > 0
    assert metrics["M9"]["chain"]["append_violations"] == 0
    assert {check["name"] for check in report["checks"]} == set(CHECK_NAMES)
    assert len(report["checks"]) == len(CHECK_NAMES)
    # M11 ② 照算、只报告，不是检查项。
    assert "cooking_mismatch" not in {check["name"] for check in report["checks"]}
    assert isinstance(metrics["M11"]["cooking_mismatches"], int)
    assert [(row["date"], row["category"]) for row in report["digest_review"]] == [
        ("2026-01-05", "饮食"), ("2026-01-05", "工作"), ("2026-01-05", "生活")]


def test_pilot_report_refuses_a_tampered_export(two_day_export, tmp_path):
    tampered = tmp_path / "tampered"
    shutil.copytree(two_day_export, tampered)
    events = tampered / "events.jsonl"
    raw = bytearray(events.read_bytes())
    raw[raw.index(b'"occurred_at":') + len(b'"occurred_at":')] ^= 1
    events.write_bytes(bytes(raw))
    with pytest.raises(IntegrityError) as direct:
        validate_export(tampered)
    with pytest.raises(IntegrityError) as raised:
        pilot_report(tampered)
    assert str(raised.value) == str(direct.value)
    assert main(["--export", str(tampered)]) == 1
