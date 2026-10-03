"""@brief 工作负荷与作息偏离的只读子命令（scripts/inspect_run.py workday --scenario，docs/dev/SPEC-errands-workday.md 6.2）。

真实运行目录由 engine_factory 加 declare 改写的场景副本产生，只发真实请求：人物周一 09:00–18:00 当班，上班途中步行到
办公室、15:00 班内离开回家。子命令以只读连接读回事件；班次、任务、住处与播报间隔取自 --scenario 场景文件，其摘要须等于
运行清单的 scenario_sha256，不等即以英文错误拒绝、不打印；作息窗口取自运行工作区 as2/agents/agent_NNNN/AGENT.json 的
结构化卡片，没有卡片时按 null 计并写说明。经子进程调用（同 tests/life/test_rerun_metrics.py），库文件字节不变。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.evaluation.test_workload_metrics import _leave_on_duty_at_three, _workday_run
from tests.life.work_schedule_fixtures import declare
from tests.profile.conftest import make_card
from worldmock.evaluation.workload_metrics import workday_inputs, workday_reports
from worldmock.profile.models import persona_card_struct

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_NAME = "white-collar-12-weeks.json"
#: 人物 1 的运行工作区没有结构化卡片时子命令追加的说明（与行为评估的报告说明同为中文）。
MISSING_CARD_NOTE = "人物 1 的运行工作区没有结构化卡片，作息窗口按 null 计"
#: 子命令输出的四个键（规格 6.2「产出入口」）。
REPORT_KEYS = ["run_id", "workload", "routine_deviation", "notes"]


def _workday(run_dir: Path, scenario: Path) -> subprocess.CompletedProcess:
    """@brief 经子进程调用只读子命令 workday（不检查退出码，标准输出与标准错误分开捕获）。"""
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "inspect_run.py"), str(run_dir), "workday",
                           "--scenario", str(scenario)], capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _left_at_three(engine_factory, tmp_path: Path) -> tuple[Path, Path, list[dict[str, Any]], Any]:
    """@brief 真实运行人物 1 周一当班、15:00 班内离开回家，关闭存储后返回运行目录、建立引擎用的改写场景文件、从 SQLite
    读回的全部事件（Event.model_dump 的 JSON 形状）与引擎解析的场景文档。"""
    engine = engine_factory(mutate=_workday_run)
    _leave_on_duty_at_three(engine)
    events = [event.model_dump(mode="json") for event in engine.store.read_events(engine.store.last_event_seq)]
    document = engine.semantics.document
    engine.close()
    return tmp_path / "run-0", tmp_path / "scenario-0" / "scenarios" / SCENARIO_NAME, events, document


def _write_agent(run_dir: Path, profile: dict[str, Any]) -> None:
    """@brief 写人物 1 的运行工作区 AGENT.json（运行时保存的身份文件形状，profile 按给定字段）。"""
    workspace = run_dir / "as2" / "agents" / "agent_0001"
    workspace.mkdir(parents=True, exist_ok=True)
    body = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "profile": {"id": 1, **profile}}
    (workspace / "AGENT.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _card() -> dict[str, Any]:
    """@brief 经卡片模型校验的结构化卡片：下班 17:30–18:00、到家 18:30–19:00，其余取合法卡片工厂。"""
    anchors = {**make_card().time_anchors.model_dump(), "leave_work": {"start_hour": 17.5, "end_hour": 18.0},
               "arrive_home": {"start_hour": 18.5, "end_hour": 19.0}}
    return persona_card_struct(make_card(time_anchors=anchors))


def _run_id(run_dir: Path) -> str:
    """@brief 运行清单里的 run_id。"""
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["run_id"]


def test_workday_command_prints_the_four_keys_for_a_real_run(engine_factory, tmp_path: Path) -> None:
    """@brief 真实运行目录加建立引擎用的改写场景文件，人物工作区存着下班 17:30–18:00、到家 18:30–19:00 的结构化卡片：
    子命令只读打印恰好 run_id、workload、routine_deviation、notes 四键，值等于进程内以同一批事件、场景文档与卡片调用
    workday_reports 的结果；15:00 班内离开是 3 小时早退，对班次结束早 180 分钟、对下班窗口早 150 分钟，到家早于到家
    窗口；卡片齐全，没有说明；库文件字节不变。"""
    run_dir, scenario, events, document = _left_at_three(engine_factory, tmp_path)
    card = _card()
    _write_agent(run_dir, {"same_second_continuation": True, "persona_card_struct": card})
    before = (run_dir / "footprints.sqlite").read_bytes()
    result = _workday(run_dir, scenario)
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert list(printed) == REPORT_KEYS
    expected = workday_reports(events, document.timezone, workday_inputs(document, {1: card}))
    assert printed == {"run_id": _run_id(run_dir), **json.loads(json.dumps(expected, ensure_ascii=False))}
    assert printed["notes"] == []
    assert printed["workload"]["agents"]["1"]["early_leave_seconds"] == 3 * 3600
    [today] = printed["routine_deviation"]["agents"]["1"]["days"]
    assert (today["leave_vs_shift_end_minutes"], today["leave_vs_window_minutes"]) == (-180.0, -150.0)
    assert today["arrived_home_at"] is not None and today["arrive_home_vs_window_minutes"] < 0
    assert (run_dir / "footprints.sqlite").read_bytes() == before


def test_workday_command_rejects_a_scenario_that_is_not_the_runs_scenario(engine_factory, tmp_path: Path) -> None:
    """@brief 运行由声明了工作安排的改写场景建立，--scenario 给未改写的仓库场景：摘要不等于运行清单的
    scenario_sha256，子命令以非零退出码与英文错误拒绝，标准输出什么都没有；同一运行配它自己的场景文件则照常打印。"""
    engine = engine_factory(mutate=declare())
    engine.close()
    run_dir = tmp_path / "run-0"
    result = _workday(run_dir, REPO_ROOT / "scenarios" / SCENARIO_NAME)
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip() == "Scenario file does not match the run manifest scenario_sha256"
    accepted = _workday(run_dir, tmp_path / "scenario-0" / "scenarios" / SCENARIO_NAME)
    assert accepted.returncode == 0, accepted.stderr
    assert list(json.loads(accepted.stdout)) == REPORT_KEYS


def test_workday_command_reports_null_windows_without_a_workspace_card(engine_factory, tmp_path: Path) -> None:
    """@brief 同样的真实运行，人物工作区没有 AGENT.json，或 AGENT.json 的 profile 没有结构化卡片（没有卡片的人物）：
    作息窗口按 null 计——离开工作地点对下班窗口、到家对到家窗口两项为 null，对班次结束的一项照常是早 180 分钟——并写
    一条说明；其余与进程内不带卡片调用 workday_reports 的结果相同。"""
    run_dir, scenario, events, document = _left_at_three(engine_factory, tmp_path)
    expected = json.loads(json.dumps(workday_reports(events, document.timezone, workday_inputs(document, {1: None})),
                                     ensure_ascii=False))
    for profile in (None, {"same_second_continuation": True}):
        if profile is not None:
            _write_agent(run_dir, profile)
        result = _workday(run_dir, scenario)
        assert result.returncode == 0, result.stderr
        printed = json.loads(result.stdout)
        assert printed == {"run_id": _run_id(run_dir), **expected, "notes": [MISSING_CARD_NOTE]}
        [today] = printed["routine_deviation"]["agents"]["1"]["days"]
        assert (today["leave_vs_shift_end_minutes"], today["leave_vs_window_minutes"]) == (-180.0, None)
        assert today["arrived_home_at"] is not None and today["arrive_home_vs_window_minutes"] is None
