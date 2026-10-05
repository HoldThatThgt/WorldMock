"""含个人事务的导出经交付窗口（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-事件检查点与恢复 、2.9）。

经真实引擎、FootprintStore、SQLite、认证导出与 prepare_windows：两人场景（陈曦与李凡都在家）里，陈曦以收尾请求记下三条
事务（一条带提醒钟点），到点的边界提醒她，她在提醒带来的那次决策里以播报更新一条、取消一条；李凡随后两次当面给她一份
纯牛奶，第一次累计、第二次办成。每次登记世界请求之前都像宿主一样记下本决策实际发给模型的行动请求（system 加由本轮冻结
观察构成的六层 user），交付窗口以它为决策提示，dreaming 读其中的层 JSON。

六种事务事件都不带决策、动作与活动身份，只经 caused_by 关联，所以既不落进任何动作窗口的目标，也不落进给予者那次决策
或动作的分组；被进度层消费的提醒与办成两种是本人可见的，作为证据展开时公开投影是空对象；观察里的待办层（含事务行）原样
进入窗口。带请求级 errands 的控制请求在意图与 dreaming 窗口里的投影不带 errands，动作窗口的目标保留完整请求；经
FootprintStore 补登的缺 errands 的旧形状请求在交付构造窗口时被拒。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

import pytest  # noqa: E402
from agentsociety2.agent.base import ModelCallEvidence  # noqa: E402

from custom.agents.life_prompt import life_user_layers, system_message, user_message  # noqa: E402
from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (activity_name, errand_add, errand_cancel, errand_update, report_request)  # noqa: F401
from tests.life.routing_fixtures import (HOME, _keep_busy, act, open_world, place, routed, run_until,  # noqa: E402
                                         stage)
from tests.life.test_errand_completion import CHEN, KIT_NAME, MILK, MILK_ID, WAIT, give, note, ready  # noqa: E402
from tests.life.test_errand_reminder import TEA, moment, until  # noqa: E402
from worldmock.delivery.windows import OBSERVATION_LAYER_KEYS, prepare_windows  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.footprints.models import EventDraft  # noqa: E402
from worldmock.footprints.validation import validate_export  # noqa: E402
from worldmock.life.errand_state import ERRAND_EVENT_KINDS  # noqa: E402

#: 提醒带来的那次决策、李凡的两次给予与办成之后陈曦的那次决策（submit_round 的动作身份为 action-<轮>-<人物>）。
_REMINDED, _GIFTS, _AFTER = "action-remind-1", ("action-hand-1-2", "action-hand-2-2"), "action-after-1"
#: 陈曦记下三条事务的那次收尾（note 的收尾一轮）。
_NOTED = "action-note-1"


def _record_prompts(engine, monkeypatch) -> None:
    """每次登记世界请求之前，像宿主一样记下本决策实际发给模型的行动请求：system 加由本轮冻结观察构成的六层 user，
    原生 tools 含所登记的工具（交付窗口按它选取决策提示，dreaming 读其中的层 JSON）。"""
    observed: dict[str, dict] = {}
    observe, submit = engine.observe, engine.submit_action

    def remembered(context) -> dict:
        """冻结一次观察，并按决策身份留下它。"""
        observed[context.decision_id] = observe(context)
        return observed[context.decision_id]

    def recorded(call):
        """先记下本决策的行动请求，再登记世界请求。"""
        persona = {"layer": "人设层", "name": engine.context.state.agents[call.context.agent_id].name}
        layers = life_user_layers(observed[call.context.decision_id], persona)
        prompt = {"model": "fixture", "messages": [system_message(False), user_message(layers)],
                  "tools": [{"type": "function", "function": {"name": call.tool_name, "parameters": {}}}]}
        engine.record_model_exchange(ModelCallEvidence(call.context, f"{call.action_id}-call", "request", 1, prompt))
        return submit(call)

    monkeypatch.setattr(engine, "observe", remembered)
    monkeypatch.setattr(engine, "submit_action", recorded)


def _late_world(engine_factory):
    """两人场景（运行在 1 月 6 日零点结束）：1 月 5 日 23:30 两人都在家、此刻都可以决策，李凡手边有两份纯牛奶。"""
    engine = open_world(engine_factory)
    now = moment(engine, "2026-01-05 23:30")

    def change(state):
        """推到 23:30，两人此刻都可以决策。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = agent.next_decision_at = now
    stage(engine, change)
    place(engine, 2, HOME, {MILK_ID: 2.0})
    return engine


def _errand_day(engine_factory, monkeypatch, tmp_path) -> tuple[list[dict], dict[str, list[dict]]]:
    """两人在家的最后半小时真实生活，过到运行终点、创建共同检查点、导出并构造交付窗口：返回（认证导出的事件，三类
    窗口）。"""
    export_dir = _export(_errand_life(engine_factory, monkeypatch), tmp_path / "errand-export")
    _, events, _ = validate_export(export_dir)
    return events, prepare_windows(export_dir)


def _export(engine, export_dir: Path) -> Path:
    """在此刻创建共同检查点并认证导出到 export_dir（运行已到终点）。"""
    checkpoint = engine.checkpoint({f"agent:{agent_id}": engine.context.store.assets.put_json(
        {"agent_id": agent_id}, {"kind": "errand_windows_test"}) for agent_id in engine.context.state.agents})
    assert export_run(engine.context.store, checkpoint.last_event_seq, export_dir)["experiment_completed"] is True
    return export_dir


def _errand_life(engine_factory, monkeypatch):
    """两人在家的最后半小时真实生活（模块说明里的那一段），过到运行终点：返回引擎。"""
    engine = _late_world(engine_factory)
    _record_prompts(engine, monkeypatch)
    note(engine, "note", [errand_add("囤两份牛奶", MILK, quantity=2, remind_time="23:35"),
                          errand_add("补食材包", KIT_NAME), errand_add("买冰红茶", TEA)])
    waited = act(engine, "wait", routed(WAIT))
    assert waited.status == "in_progress", (waited.error_code, waited.message)
    until(engine, moment(engine, "2026-01-05 23:35"))
    changes = [errand_update("补食材包", memo="周末去超市买"), errand_cancel("买冰红茶", "家里还有冰红茶")]
    reported = act(engine, "remind", report_request(activity_name(engine, waited.activity_id), summary="想起要囤牛奶。",
                                                    errands=changes), tool="report_status")
    assert reported.status == "completed", (reported.error_code, reported.message)
    for label in ("hand-1", "hand-2"):
        ready(engine, 2)
        handed = act(engine, label, give(engine, CHEN), agent_id=2)
        assert handed.status == "completed", (handed.error_code, handed.message)
    assert engine.context.state.agents[1].errands and "囤两份牛奶" not in {
        errand.name for errand in engine.context.state.agents[1].errands.values()}
    ready(engine, 1)
    tool, request = _keep_busy(engine, 1, "after")
    assert act(engine, "after", request, tool=tool).status in {"completed", "in_progress"}
    run_until(engine, lambda current: current.context.state.current_time == current.context.state.end_time)
    return engine


def _errand_events(events: list[dict]) -> dict[str, list[dict]]:
    """导出里六种事务事件，按 kind 分列（事件顺序）。"""
    return {kind: [event for event in events if event["event_type"] == "environment_event"
                   and event["data"].get("kind") == kind] for kind in ERRAND_EVENT_KINDS}


def _window(windows: dict[str, list[dict]], task: str, action_id: str) -> dict:
    """某个动作的动作窗口或意图窗口（目标是该动作的请求事件）。"""
    [record] = [item for item in windows[task] if item["target"]["request"]["action_id"] == action_id]
    return record


def _shown(record: dict) -> dict[str, dict]:
    """窗口输入里的各个事件（按事件身份）。"""
    return {row["event_id"]: row for row in record["input"]["events"]}


def test_an_export_with_errand_reminder_and_completion_builds_all_three_windows(
        engine_factory, monkeypatch, tmp_path) -> None:  # noqa: F811
    """含事务提醒与办成记录的导出构造出动作、意图与 dreaming 三类窗口。提醒与办成两种事件是本人可见的，其余四种系统
    可见；办成之后那次决策的观察把两条只写名称的记录列进进度层，窗口把这两个来源事件作为证据展开，公开投影是空对象，
    也列进可引用的证据；陈曦当天的 dreaming 窗口同样带它们。提醒带来的那次决策的窗口里，观察的待办层是存进 SQLite 的
    那一份（三条事务行原样），四个环境构建层逐键等于存储的观察。任何窗口的目标都不含事务事件。"""
    events, windows = _errand_day(engine_factory, monkeypatch, tmp_path)
    kinds = _errand_events(events)
    assert {kind: len(rows) for kind, rows in kinds.items()} == {
        "errand_registered": 3, "errand_updated": 1, "errand_cancelled": 1, "errand_progressed": 1,
        "errand_completed": 1, "errand_reminded": 1}
    assert {kind: {row["visibility"] for row in rows} for kind, rows in kinds.items()} == {
        "errand_registered": {"system"}, "errand_updated": {"system"}, "errand_cancelled": {"system"},
        "errand_progressed": {"system"}, "errand_completed": {"agent"}, "errand_reminded": {"agent"}}
    [reminded], [completed] = kinds["errand_reminded"], kinds["errand_completed"]
    consumed = {reminded["event_id"], completed["event_id"]}
    assert all(windows[task] for task in ("action", "intent", "dreaming"))
    after = next(event for event in events if event["event_type"] == "observation"
                 and event["decision_id"] == "decision-after-1")
    assert consumed <= set(after["data"]["progress_event_ids"])
    days = [record for record in windows["dreaming"] if record["input"]["person_id"] == 1
            and consumed <= set(_shown(record))]
    assert len(days) == 3
    for record in (_window(windows, "action", _AFTER), _window(windows, "intent", _AFTER), *days):
        shown = _shown(record)
        assert [(shown[identity]["event_type"], shown[identity]["data"]) for identity in sorted(consumed)] == [
            ("environment_event", {}), ("environment_event", {})]
        assert consumed <= set(record["input"]["allowed_evidence_event_ids"])
    stored = next(event for event in events if event["event_type"] == "observation"
                  and event["decision_id"] == "decision-remind-1")
    row = _shown(_window(windows, "action", _REMINDED))[stored["event_id"]]
    assert row["data"] == {key: stored["data"][key] for key in OBSERVATION_LAYER_KEYS}
    assert [item["name"] for item in row["data"]["todo_layer"]["errands"]] == ["囤两份牛奶", "补食材包", "买冰红茶"]
    errand_ids = {event["event_id"] for rows in kinds.values() for event in rows}
    for record in windows["action"] + windows["intent"]:
        targets = {record["target"]["request"]["event_id"], *(row["event_id"] for row in record["target"]["outcomes"])}
        assert not targets & errand_ids


def test_a_completion_caused_by_another_persons_action_stays_out_of_their_decision_group(
        engine_factory, monkeypatch, tmp_path) -> None:  # noqa: F811
    """李凡两次当面给陈曦一份纯牛奶：累计与办成两条事件归陈曦、各以李凡那次给予的动作结果为唯一原因，但六种事务事件
    都不带决策、动作与活动身份，所以李凡那两次给予的决策分组与动作分组里只有李凡自己的事件与无人物的系统事实，没有一条
    属于陈曦；交付照常构造窗口，给予的动作窗口目标只含李凡的结果，任何窗口的目标都不含事务事件。"""
    events, windows = _errand_day(engine_factory, monkeypatch, tmp_path)
    kinds = _errand_events(events)
    rows = [event for kind in ERRAND_EVENT_KINDS for event in kinds[kind]]
    assert len(rows) == 8 and all(kinds.values())
    assert {(event["agent_id"], event["decision_id"], event["action_id"], event["activity_id"]) for event in rows} == {
        (1, None, None, None)}
    gifts = [event for event in events if event["event_type"] == "action_result" and event["action_id"] in _GIFTS]
    assert [event["agent_id"] for event in gifts] == [2, 2]
    settled = kinds["errand_progressed"] + kinds["errand_completed"]
    assert [event["caused_by"] for event in settled] == [[event["event_id"]] for event in gifts]
    for action_id in _GIFTS:
        [requested] = [event for event in events if event["event_type"] == "action_requested"
                       and event["action_id"] == action_id]
        decision = [event for event in events if event["decision_id"] == requested["decision_id"]]
        action = [event for event in events if event["action_id"] == action_id]
        assert {event["agent_id"] for event in decision + action} <= {2, None}
        record = _window(windows, "action", action_id)
        assert {row["agent_id"] for row in record["target"]["outcomes"]} == {2}
    identities = {event["event_id"] for event in rows}
    for record in windows["action"] + windows["intent"]:
        assert not {row["event_id"] for row in record["target"]["outcomes"]} & identities


def _legacy_request(engine, requested: dict) -> None:
    """经 FootprintStore 在运行终点补登一条旧形状的 report_status 请求事件（带 items 而缺请求级 errands，是世界结构 8 的
    写法）：同一人物、同一决策、照提醒带来的那次播报的逐项内容，动作身份另起，请求摘要按同一算法对旧形状重算；世界状态
    不变、版本加一。"""
    request = {"items": requested["data"]["request"]["items"]}
    content = json.dumps({"tool_name": "report_status", "request": request}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    data = {**requested["data"], "request": request, "request_sha256": hashlib.sha256(content).hexdigest()}
    staged = engine.context.state.model_copy(deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="action_requested", occurred_at=staged.current_time, agent_id=1,
        decision_id=requested["decision_id"], action_id="action-legacy-1", source_kind="model", data=data)])
    engine.context.state = staged


def test_control_requests_with_errands_reach_windows_without_them_and_legacy_requests_are_refused(
        engine_factory, monkeypatch, tmp_path) -> None:  # noqa: F811
    """交付边界经真实请求、引擎、SQLite 与认证导出（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-交付边界 、2.3）：记下三条事务的收尾与
    提醒带来的那次播报（一条更新、一条取消）都带请求级 errands。意图窗口与陈曦每个显示它们的 dreaming 窗口里，两个请求的
    投影恒不带 errands，每项只留活动名与自述；动作与意图窗口的目标就是导出里的那个请求事件，逐键相等、保留完整的 errands
    （构造窗口的投影没有原地改写它）。这段生活没有任何收尾记忆，导出的 conclusion_memories 按空值约定为 null。随后经
    FootprintStore 补登一条缺 errands 的旧形状播报请求再导出，交付构造窗口时以完整性故障拒绝它，不当作没有事务变更放行。"""
    engine = _errand_life(engine_factory, monkeypatch)
    export_dir = _export(engine, tmp_path / "errand-export")
    _, events, _ = validate_export(export_dir)
    windows = prepare_windows(export_dir)
    requests = {event["action_id"]: event for event in events if event["event_type"] == "action_requested"}
    for action_id, count in ((_NOTED, 3), (_REMINDED, 2)):
        requested, data = requests[action_id], requests[action_id]["data"]
        assert len(data["request"]["errands"]) == count
        trimmed = {"tool_name": data["tool_name"], "self_note": data["self_note"], "request": {"items": [
            {"activity_name": item["activity_name"], "summary": item["summary"]} for item in data["request"]["items"]]}}
        dreams = [record for record in windows["dreaming"] if record["input"]["person_id"] == 1
                  and requested["event_id"] in _shown(record)]
        assert dreams
        for record in (_window(windows, "intent", action_id), *dreams):
            assert _shown(record)[requested["event_id"]]["data"] == trimmed
        for task in ("action", "intent"):
            assert _window(windows, task, action_id)["target"]["request"] == requested
    quality = json.loads((export_dir / "quality.json").read_text(encoding="utf-8"))
    assert quality["conclusion_memories"] is None and quality["errand_metrics"]["registered_count"] == 3
    _legacy_request(engine, requests[_REMINDED])
    legacy = _export(engine, tmp_path / "legacy-export")
    with pytest.raises(IntegrityError, match="report_status request lacks errands"):
        prepare_windows(legacy)
