"""班次与休息时段起止的唤醒经真实请求、引擎与 SQLite（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-验收台账 的 T1–T15、
T19、T20）。

正式场景副本经 engine_factory 加 mutate 构造，班次由 tests/life/work_schedule_fixtures.py 的 declare 声明；时刻由测试套件
既有的预置做法置好（经存储提交，版本连续），此后人物只经真实观察、请求、结算与时钟推进过日子。断言读共同身体结算的
decision_offered 与 wake_reasons、SQLite 里 decision_started 的 wake_reasons、人物的下一次决策时刻、调度状态报告的
下一边界、观察的环境层 duty.status 与活动状态（docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界规则 、
docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界情形 ）。

起止避开同一人物的其他候选时刻：午餐、晚餐窗口的起止（12:00、13:30、18:30、20:00），等待的计划结束（开始后 15 分钟），
播报锚点加播报间隔（30 分钟），重估到期与体感跨档；本意就是重合的 T8 与 T19 除外。
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall

from custom.agents.life_prompt import life_user_layers, user_message
from tests.footprints.test_quality import _event, _manifest, _started as _started_data
from tests.life.conftest import (StubPlanDayClient, activity_name, advance_next, conclude_request, context,
                                 pause_request, report_request, settle_day_plans, stage_day_plan, stage_errands,
                                 start_activity, submit_round)
from tests.life.routing_fixtures import HOME, _keep_busy, stage
from tests.life.test_errand_completion import MILK_ID
from tests.life.test_errand_todo_layer import _errand
from tests.life.test_same_moment_decisions import _invalid_start, _pick
from tests.life.test_social_rounds import _chat, _chats
from tests.life.work_schedule_fixtures import declare
from worldmock.footprints.checkpoint import restore_branch
from worldmock.footprints.export import export_run
from worldmock.footprints.models import DECISION_OFFERED_FIELD, WAKE_REASONS_FIELD, Event
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (WORLD_SCHEMA_VERSION, ActivityStatus, CounterfactualIntervention,
                                   ReviseDayPlanRequest)
from worldmock.life.rhythm import awake_duty_edge_times
from worldmock.life.schedule import DUTY_EDGE_REASONS, PlanGoal, WakeReason
from worldmock.runtime import _intervention_sha256

#: 四个成员的名字，按声明次序。
_NAMES = tuple(member.value for member in DUTY_EDGE_REASONS)
#: 08:10–17:30、没有休息时段：17:24 开始的等待计划在 17:39 结束，起止在 17:30（T1、T10–T12、T14、T20）。
_EVENING_END = {"starts": 490, "ends": 1050, "breaks": []}
#: 08:10–17:20、休息 14:10–14:40：四个起止都不在饭点窗口、整点或半点上（T2、T7、T13）。
_DAY = {"starts": 490, "ends": 1040, "breaks": [[850, 880]]}
#: 08:10–17:20、休息 14:10–14:47：14:10 播报之后的播报到期 14:40 与观看的反馈边界都不落在休息结束上（T3）。
_LONG_BREAK = {"starts": 490, "ends": 1040, "breaks": [[850, 887]]}
#: 两段首尾相接的休息 14:30–15:00 与 15:00–15:30（T8）。
_ADJACENT = {"starts": 490, "ends": 1040, "breaks": [[870, 900], [900, 930]]}
#: 两人场景当日的晚间班次 22:10–23:50、休息 22:40–23:10（T5、T6、T15）。
_NIGHT = {"starts": 1330, "ends": 1430, "breaks": [[1360, 1390]]}
#: 清晨班次 03:10–10:10、休息 04:10–04:40：运行开始就入睡的人睡过前三个起止（T4）。
_DAWN = {"starts": 190, "ends": 610, "breaks": [[250, 280]]}
#: 06:30–17:30：周二 06:30 的班次开始与起床提醒同一秒（T19）。
_ALARM = {"starts": 390, "ends": 1050, "breaks": []}
#: 日期例外的班次 10:10–17:10（T7）。
_EXCEPTION = {"starts": 610, "ends": 1030, "breaks": []}
#: 周一 00:00–05:10 与周五 21:10–24:00，周二至周四与周末不当班（T9、T19）。
_MIDNIGHT_WEEK = {"0": {"starts": 0, "ends": 310, "breaks": []}, "1": None, "2": None, "3": None,
                  "4": {"starts": 1270, "ends": 1440, "breaks": []}}
#: 固定的休息日对象（环境层 duty）。
_REST = {"day": "休息日", "hours": None, "place": None, "breaks": [], "status": None}
#: 人物状态的键集合（本特性不增不减，docs/dev/SPEC-idle-fillers.md#spec-idle-fillers-班次边界机制 ）。
_AGENT_KEYS = frozenset({
    "active_effects", "agent_id", "balance_account_id", "body", "body_settled_at", "consecutive_rejections",
    "content_progress", "day_plan", "delivered_warning_signatures", "discomfort_pulse_hours", "discomfort_started_at",
    "errands", "felt_bands_at_last_decision", "inbox", "inventory", "known_task_ids", "last_decision_time",
    "location_id", "material_reveals", "mood_updated_at", "name", "next_decision_at", "outbox", "pending_wake_reasons",
    "people_at_last_decision", "plan_submitted_dates", "qualifications", "read_inbox_ids", "read_message_ids",
    "same_moment_rounds", "same_moment_stage", "skill_levels", "supply_browsed_at", "supply_browsed_location",
    "taste_weights", "visible_message_ids", "visible_supply_ids", "visible_task_ids"})


def _engine(engine_factory, *declarations, scenario: str = "white-collar-12-weeks.json") -> LifeEngine:
    """@brief 依次用 declare(...) 返回的改写函数改写场景副本建立引擎（没有声明时用正式场景），每个人物登记空计划。"""
    def mutate(document: dict) -> None:
        """@brief 把全部声明写进场景副本。"""
        for change in declarations:
            change(document)

    engine = engine_factory(scenario, mutate=mutate if declarations else None)
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    return engine


def _moment(engine: LifeEngine, clock: str, day: str = "2026-01-05") -> int:
    """@brief 场景当地时刻 day clock（HH:MM）的纪元秒。"""
    zone = ZoneInfo(engine.context.scenario.timezone)
    return int(datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=zone).timestamp())


def _clock(engine: LifeEngine, now: int) -> None:
    """@brief 把世界直接置于 now：全部人物在家、身体结算到此刻、此刻可以决策，丢弃不晚于 now 的后台事件，每人登记当日
    空计划（经存储提交，版本连续）。"""
    def change(state) -> None:
        """@brief 推到 now，人物都在家。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for person in state.agents.values():
            person.location_id, person.body_settled_at, person.next_decision_at = HOME, now, now
    stage(engine, change)
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)


def _wait() -> object:
    """@brief 以「自己」开始一段等待的请求（计划 15 分钟）。"""
    return start_activity("等待", "自己")


def _decide(engine: LifeEngine, name: str, tool: str, request, agent_id: int = 1) -> tuple[dict, object]:
    """@brief 一个人物按真实顺序观察、登记并结算一轮；request 可以是按本轮观察构造请求的函数。返回（观察，结果）。"""
    decision = context(engine, name, agent_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    if callable(request):
        request = request(observed)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def _wait_from(engine: LifeEngine, now: int) -> tuple[str, dict]:
    """@brief 置于 now 并以真实请求开始等待，返回（等待的活动身份，那次观察）。"""
    _clock(engine, now)
    observed, waited = _decide(engine, f"wait-{now}", "start_activity", _wait())
    assert waited.status == "in_progress", (waited.error_code, waited.message)
    return waited.activity_id, observed


def _report(engine: LifeEngine, name: str, activity_id: str, agent_id: int = 1) -> tuple[dict, object]:
    """@brief 播报点名的进行中活动（真实一轮）。"""
    return _decide(engine, name, "report_status", report_request(activity_name(engine, activity_id)), agent_id)


def _next_event(engine: LifeEngine) -> int:
    """@brief 调度状态报告的下一边界（纪元秒）。"""
    return int(engine.scheduling_state().next_event_at.timestamp())


def _settlement(engine: LifeEngine, at: int, agent_id: int = 1):
    """@brief 人物在 at 结束的唯一已提交共同身体结算事件。"""
    [event] = [event for event in engine.context.store.read_agent_events("environment_event", agent_id)
               if event.data.get("kind") == "body_settlement" and event.data["interval_end"] == at]
    return event


def _verdict(engine: LifeEngine, at: int, agent_id: int = 1) -> tuple[bool, list[str]]:
    """@brief 人物在 at 的共同身体结算是否提供决策与唤醒原因。"""
    data = _settlement(engine, at, agent_id).data
    return data[DECISION_OFFERED_FIELD], data[WAKE_REASONS_FIELD]


def _boundaries(engine: LifeEngine, agent_id: int = 1) -> list[int]:
    """@brief 人物经历过的全部结算边界（已提交共同身体结算的区间终点）。"""
    events = engine.context.store.read_agent_events("environment_event", agent_id)
    return [event.data["interval_end"] for event in events if event.data.get("kind") == "body_settlement"]


def _decisions(engine: LifeEngine, agent_id: int = 1) -> list[tuple[int, list[str]]]:
    """@brief 人物全部 decision_started 的（时刻，唤醒原因），读自 SQLite。"""
    return [(event.occurred_at, event.data[WAKE_REASONS_FIELD])
            for event in engine.context.store.read_agent_events("decision_started", agent_id)]


def _started(engine: LifeEngine, at: int, agent_id: int = 1) -> list[list[str]]:
    """@brief 人物在 at 的各次 decision_started 的唤醒原因。"""
    return [reasons for moment, reasons in _decisions(engine, agent_id) if moment == at]


def _named(engine: LifeEngine, agent_id: int = 1) -> list[tuple[int, list[str]]]:
    """@brief 人物带四个名字之一的 decision_started（时刻，唤醒原因）。"""
    return [(moment, reasons) for moment, reasons in _decisions(engine, agent_id) if set(_NAMES) & set(reasons)]


def _advance(engine: LifeEngine, client=None) -> None:
    """@brief 推进到下一个严格未来边界；零点边界先以确定性桩 client（缺省为空计划）生成当日计划，再执行零点节律入口。"""
    state = engine.scheduling_state()
    engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)
    if engine.midnight_rhythm_pending:
        settle_day_plans(engine, client)


def _drive(engine: LifeEngine, done, client=None, limit: int = 400) -> None:
    """@brief 照常过日子直到 done() 成立：就绪的人物先收尾、有进行中活动就播报、否则等待（routing_fixtures._keep_busy），
    没人就绪就推进到下一边界。"""
    for index in range(limit):
        if done():
            return
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            _advance(engine, client)
            continue
        label = f"{engine.context.state.version}-{index}"
        submit_round(engine, {agent_id: _keep_busy(engine, agent_id, label) for agent_id in ready}, f"busy-{label}")
    pytest.fail("the run did not reach the condition")


def _to_edge(engine: LifeEngine, edge: int) -> None:
    """@brief 照常过日子到 edge 之前：没人就绪且调度状态报告的下一边界恰是 edge；再推进一次，时钟落在 edge。"""
    def poised() -> bool:
        """@brief 下一边界恰是 edge 且没人要决策；越过 edge 即失败。"""
        assert engine.context.state.current_time < edge
        state = engine.scheduling_state()
        return not state.ready_agent_ids and int(state.next_event_at.timestamp()) == edge

    _drive(engine, poised)
    _advance(engine)
    assert engine.context.state.current_time == edge


def _observe(engine: LifeEngine, name: str, agent_id: int = 1) -> dict:
    """@brief 不开决策轮的一次真实观察。"""
    return engine.observe(context(engine, name, agent_id))


def _activity_facts(engine: LifeEngine, activity_id: str) -> tuple:
    """@brief 起止不能改动的活动事实：状态、计划结束、下一次交互与播报锚点。"""
    item = engine.context.state.activities[activity_id]
    return item.status, item.planned_end_at, item.next_interaction_at, item.report_anchor_at


def _stage_game_turn(engine: LifeEngine, game_id: str, turn: int) -> None:
    """@brief 把进行中的游戏改成回合制写进存储：计划一小时后结束，下一次交互在 turn（经存储提交，版本连续）。"""
    def change(state) -> None:
        """@brief 游戏的下一次交互与计划结束。"""
        game = state.activities[game_id]
        state.activities[game_id] = game.model_copy(update={
            "next_interaction_at": turn, "planned_end_at": game.started_at + 3600,
            "planned_duration_seconds": 3600}, deep=True)
    stage(engine, change)


def _start_game(engine: LifeEngine) -> str:
    """@brief 以真实请求开始本轮列出的一项可玩游戏，返回活动身份。"""
    _, played = _decide(engine, "play", "start_activity",
                        lambda observed: start_activity(*reversed(_pick(observed, "play", kind="game"))))
    assert played.status == "in_progress", (played.error_code, played.message)
    return played.activity_id


def _checkpoint(engine: LifeEngine):
    """@brief 以占位人物快照提交一个共同检查点。"""
    snapshots = {f"agent:{agent_id}": engine.context.store.assets.put_json({"agent_id": agent_id},
                                                                          {"kind": "shift_edge_test"})
                 for agent_id in engine.context.store.manifest["agent_ids"]}
    return engine.checkpoint(snapshots)


def _event_kinds(engine: LifeEngine, since: int) -> set[tuple[str, str | None]]:
    """@brief since 之后写入的事件种类：(event_type, data.kind)。"""
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    return {(event.event_type, event.data.get("kind")) for event in events if event.event_seq > since}


def _progress_types(observed: dict) -> set[str]:
    """@brief 一次观察的进度层里全部记录的类型（进度层不落库，只在观察里）。"""
    return {row["type"] for row in observed["progress_layer"]["events"]}


# --- T1：清醒人物精确到点 ---


def test_t1_a_waiting_person_is_asked_exactly_at_the_shift_end_and_the_wait_goes_on(engine_factory) -> None:
    """17:24 开等待（计划 17:39 结束），班次 17:30 结束：受理之后、推进之前人物的下一次决策时刻与调度状态报告的下一边界都是
    17:30；时钟落在 17:30，共同身体结算与 SQLite 里的 decision_started 都恰好带 shift_end，duty.status 为已下班；等待仍在
    进行、计划结束不变。播报之后下一次决策回到 17:39，随后推进落在 17:39，其后一小时的决策都不带四个名字。另建的引擎在
    17:30 提交被拒的请求，同一秒的再决策不带这个名字。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    end, planned = _moment(engine, "17:30"), _moment(engine, "17:39")
    waited, _ = _wait_from(engine, _moment(engine, "17:24"))
    before = _activity_facts(engine, waited)
    assert before[:2] == (ActivityStatus.ACTIVE, planned)
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == end
    advance_next(engine)
    assert engine.context.state.current_time == end
    assert _verdict(engine, end) == (True, ["shift_end"])
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert _activity_facts(engine, waited) == before
    observed, reported = _report(engine, "at-shift-end", waited)
    assert reported.status == "completed"
    assert observed["environment_layer"]["duty"]["status"] == "已下班"
    assert _started(engine, end) == [["shift_end"]]
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == planned
    advance_next(engine)
    assert engine.context.state.current_time == planned
    _drive(engine, lambda: engine.context.state.current_time >= end + 3600)
    later = [reasons for moment, reasons in _decisions(engine) if moment > end]
    assert later and not [reasons for reasons in later if set(_NAMES) & set(reasons)]

    other = _engine(engine_factory, declare(shift=_EVENING_END))
    other_wait, _ = _wait_from(other, _moment(other, "17:24"))
    advance_next(other)
    _, refused = _decide(other, "refused", "start_activity", start_activity("观看", "不存在的对象"))
    assert refused.status == "rejected"
    assert other.context.state.current_time == end and other.scheduling_state().ready_agent_ids == (1,)
    assert _report(other, "again", other_wait)[1].status == "completed"
    assert _started(other, end) == [["shift_end"], ["rejection_feedback"]]


# --- T2：四种起止各一次机会 ---

#: 成员 → （起止钟点，起止之前的 duty.status，起止当秒的 duty.status）。
_EDGES = {"shift_start": ("08:10", "未到上班时间", "当班中"), "break_start": ("14:10", "当班中", "休息时段"),
          "break_end": ("14:40", "休息时段", "当班中"), "shift_end": ("17:20", "当班中", "已下班")}


@pytest.mark.parametrize("member", list(_EDGES))
def test_t2_each_edge_offers_one_decision_with_its_name_and_moves_the_duty_status(engine_factory, member) -> None:
    """班次 08:10–17:20、休息 14:10–14:40：起止前 6 分钟开等待，下一次决策时刻与下一边界都是起止；起止那一秒给一次机会，
    结算与 decision_started 都恰好带对应的名字（足迹侧词表收下它），duty.status 由起止前的状态变为起止后的状态，等待仍在
    进行。"""
    clock, before, after = _EDGES[member]
    engine = _engine(engine_factory, declare(shift=_DAY))
    edge = _moment(engine, clock)
    waited, observed = _wait_from(engine, edge - 360)
    assert observed["environment_layer"]["duty"]["status"] == before
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == edge
    advance_next(engine)
    assert engine.context.state.current_time == edge
    assert _verdict(engine, edge) == (True, [member])
    assert engine.scheduling_state().ready_agent_ids == (1,)
    seen, reported = _report(engine, f"at-{member}", waited)
    assert reported.status == "completed"
    assert seen["environment_layer"]["duty"]["status"] == after
    assert _started(engine, edge) == [[member]]
    assert engine.context.state.activities[waited].status == ActivityStatus.ACTIVE


# --- T3：不结束活动、不写事件、不进提示 ---


def _watch_long(engine: LifeEngine) -> str:
    """@brief 以真实请求开始观看本轮列出的一项长于两小时的内容，返回活动身份。"""
    contents = engine.context.rules.contents

    def request(observed: dict):
        """@brief 本轮工具层里的长内容与观看方式。"""
        target, way = next((row["object"], action["label"]) for row in observed["interaction_bindings"]
                           if row["kind"] == "content" and contents[row["object_id"]].duration_seconds > 7200
                           for action in row["actions"] if action["canonical_action"] == "watch")
        return start_activity(way, target)

    _, watched = _decide(engine, "watch", "start_activity", request)
    assert watched.status == "in_progress", (watched.error_code, watched.message)
    return watched.activity_id


def test_t3_edges_end_no_activity_write_no_new_kind_and_keep_the_shift_in_the_environment_layer(engine_factory) -> None:
    """班次 08:10–17:20、休息 14:10–14:47 的人物 14:04 开始看一部长片，没有工作安排的同一场景照样做：14:10 与 14:47 各给一次
    机会，推进前后观看的状态、计划结束、下一次交互与播报锚点都不变；那一秒的观察里班次钟点与「上班安排」只在环境层，实际
    发给模型的六层里没有四个名字。照常过到 14:50，有班次的一次运行写下的事件种类与进度层记录类型都不超出没有班次的那一次。"""
    scheduled, plain = _engine(engine_factory, declare(shift=_LONG_BREAK)), _engine(engine_factory)
    marks, started = {}, {}
    for engine in (plain, scheduled):
        _clock(engine, _moment(engine, "14:04"))
        marks[id(engine)] = engine.context.store.last_event_seq
        started[id(engine)] = _watch_long(engine)
    watched = started[id(scheduled)]
    persona = {"layer": "人设层", "name": scheduled.context.state.agents[1].name}
    types: set[str] = set()
    for clock, member in (("14:10", "break_start"), ("14:47", "break_end")):
        edge = _moment(scheduled, clock)
        _drive(scheduled, lambda: not scheduled.scheduling_state().ready_agent_ids and _next_event(scheduled) == edge)
        before = _activity_facts(scheduled, watched)
        advance_next(scheduled)
        assert scheduled.context.state.current_time == edge
        assert _verdict(scheduled, edge) == (True, [member])
        assert _activity_facts(scheduled, watched) == before
        observed, reported = _report(scheduled, f"edge-{member}", watched)
        assert reported.status == "completed"
        others = json.dumps([observed[key] for key in ("tool_layer", "todo_layer", "memory_layer", "progress_layer")],
                            ensure_ascii=False)
        assert "08:10–17:20" in json.dumps(observed["environment_layer"], ensure_ascii=False)
        assert all(text not in others for text in ("08:10–17:20", "14:10–14:47", "上班安排"))
        prompt = user_message(life_user_layers(observed, persona))["content"]
        assert not [name for name in _NAMES if name in prompt]
        types |= _progress_types(observed)
    for engine in (scheduled, plain):
        _drive(engine, lambda: engine.context.state.current_time >= _moment(engine, "14:50"))
    assert _event_kinds(scheduled, marks[id(scheduled)]) <= _event_kinds(plain, marks[id(plain)])
    types |= _progress_types(_observe(scheduled, "after"))
    assert types and types <= _progress_types(_observe(plain, "after"))
    assert scheduled.context.state.activities[watched].status == ActivityStatus.ACTIVE


# --- T4、T5：睡眠 ---


def test_t4_a_sleeper_has_no_edge_boundaries_and_slept_edges_are_not_made_up(engine_factory) -> None:
    """班次 03:10–10:10、休息 04:10–04:40 的人物运行一开始就去睡（计划 07:30 醒）：睡着的整段里清醒人物的起止候选为空，
    下一次决策时刻与下一边界都不是起止，03:10、04:10、04:40 没有边界；醒来那一秒的原因与之后的决策都不补睡过的起止，
    醒来后第一个带名字的决策是下一个未到的起止 10:10 的 shift_end。"""
    engine = _engine(engine_factory, declare(shift=_DAWN))
    slept_edges = [_moment(engine, clock) for clock in ("03:10", "04:10", "04:40")]
    end = _moment(engine, "10:10")
    _, slept = _decide(engine, "sleep", "start_activity", start_activity("睡觉", "自己"))
    assert slept.status == "in_progress"

    def awake() -> bool:
        """@brief 睡眠结束；睡着的每一刻都没有起止候选。"""
        if engine.context.state.activities[slept.activity_id].status != ActivityStatus.ACTIVE:
            return True
        assert awake_duty_edge_times(engine.context.state, engine.context.scenario) == []
        assert engine.context.state.agents[1].next_decision_at not in [*slept_edges, end]
        assert _next_event(engine) not in [*slept_edges, end]
        return False

    _drive(engine, awake)
    woke = engine.context.state.activities[slept.activity_id].ended_at
    assert slept_edges[-1] < woke < end
    assert not set(slept_edges) & set(_boundaries(engine))
    assert not set(_NAMES) & set(_verdict(engine, woke)[1])
    _drive(engine, lambda: bool(_started(engine, end)))
    assert _named(engine) == [(end, ["shift_end"])]
    assert _verdict(engine, end) == (True, ["shift_end"])


def test_t5_of_two_people_on_one_shift_only_the_awake_one_is_asked_and_the_sleeper_sleeps_on(engine_factory) -> None:
    """两人场景里两人声明同一班次 22:10–23:50、休息 22:40–23:10；22:00 人物 1 开始等待，人物 2 去睡：四个起止那一秒人物 1
    的结算各带对应的名字并只有他就绪；人物 2 的结算静默，睡眠继续，他从不带四个名字。"""
    engine = _engine(engine_factory, declare(0, shift=_NIGHT), declare(1, shift=_NIGHT), scenario="multi-person.json")
    _clock(engine, _moment(engine, "22:00"))
    _, results = submit_round(engine, {1: ("start_activity", _wait()),
                                       2: ("start_activity", start_activity("睡觉", "自己"))}, "night")
    sleep = next(item for item in results if item.agent_id == 2)
    assert sleep.status == "in_progress"
    for clock, member in (("22:10", "shift_start"), ("22:40", "break_start"), ("23:10", "break_end"),
                          ("23:50", "shift_end")):
        edge = _moment(engine, clock)
        _to_edge(engine, edge)
        assert _verdict(engine, edge, 1) == (True, [member])
        assert _verdict(engine, edge, 2) == (False, [])
        assert engine.context.state.activities[sleep.activity_id].status == ActivityStatus.ACTIVE
        assert engine.scheduling_state().ready_agent_ids == (1,)
    assert _named(engine, 2) == []


# --- T6：没有工作安排的人不变 ---


def test_t6_people_without_a_schedule_get_no_edges_and_never_take_anothers(engine_factory) -> None:
    """单人场景没有工作安排：17:24 开等待，下一次决策时刻与下一边界都是等待的计划结束 17:39，17:30 不是边界。两人场景只给
    人物 1 声明 22:10–23:50、休息 22:40–23:10，两人 22:04 都开始等待：四个起止那一秒人物 1 带对应的名字，人物 2 的结算
    静默；人物 2 的起止候选为空，duty 为 null，整晚的决策从不带四个名字。"""
    plain = _engine(engine_factory)
    _wait_from(plain, _moment(plain, "17:24"))
    assert plain.context.state.agents[1].next_decision_at == _next_event(plain) == _moment(plain, "17:39")
    assert awake_duty_edge_times(plain.context.state, plain.context.scenario) == []
    advance_next(plain)
    assert plain.context.state.current_time == _moment(plain, "17:39")

    engine = _engine(engine_factory, declare(0, shift=_NIGHT), scenario="multi-person.json")
    _clock(engine, _moment(engine, "22:04"))
    submit_round(engine, {1: ("start_activity", _wait()), 2: ("start_activity", _wait())}, "evening")
    for clock, member in (("22:10", "shift_start"), ("22:40", "break_start"), ("23:10", "break_end"),
                          ("23:50", "shift_end")):
        edge = _moment(engine, clock)
        _to_edge(engine, edge)
        assert _verdict(engine, edge, 1) == (True, [member])
        assert _verdict(engine, edge, 2) == (False, [])
        assert engine.scheduling_state().ready_agent_ids == (1,)
    assert _observe(engine, "second", 2)["environment_layer"]["duty"] is None
    assert _named(engine, 2) == [] and _decisions(engine, 2)


# --- T7：休息日与日期例外 ---


def test_t7_rest_days_have_no_edges_and_a_date_exception_sets_the_clock(engine_factory) -> None:
    """周一至周五 08:10–17:20 的声明里给 2026-01-05（周一）写例外班次 10:10–17:10：08:04 开等待，08:10 不是边界，一直过到
    10:10 才因例外的班次开始得到机会，duty 写例外的钟点。周六 2026-01-10 不当班：duty 是休息日对象，清醒人物的起止候选
    为空，08:10 不是边界。"""
    engine = _engine(engine_factory, declare(shift=_DAY, exceptions={"2026-01-05": _EXCEPTION}))
    weekly, excepted = _moment(engine, "08:10"), _moment(engine, "10:10")
    _wait_from(engine, _moment(engine, "08:04"))
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == _moment(engine, "08:19")
    _to_edge(engine, excepted)
    assert weekly not in _boundaries(engine)
    assert _verdict(engine, excepted) == (True, ["shift_start"])
    assert _named(engine) == []
    duty = _observe(engine, "excepted")["environment_layer"]["duty"]
    assert (duty["hours"], duty["status"]) == ("10:10–17:10", "当班中")

    rest = _engine(engine_factory, declare(shift=_DAY))
    saturday = _moment(rest, "08:10", "2026-01-10")
    _, observed = _wait_from(rest, saturday - 360)
    assert observed["environment_layer"]["duty"] == _REST
    assert awake_duty_edge_times(rest.context.state, rest.context.scenario) == []
    _drive(rest, lambda: rest.context.state.current_time > saturday + 600)
    assert saturday not in _boundaries(rest) and _named(rest) == []


# --- T8：重合 ---


def test_t8_coinciding_edges_reminders_and_a_planned_end_are_one_opportunity_in_enum_order(engine_factory) -> None:
    """两段休息 14:30–15:00 与 15:00–15:30 首尾相接，15:00 同时是一段的结束与另一段的开始；当日目标与个人事务都提醒 15:00，
    14:45 开始的等待恰在 15:00 结束：这一秒只有一次机会，原因按枚举次序并列，两个起止各记一次；duty.status 仍是休息时段，
    这一秒的首个 decision_started 带同样的原因。"""
    engine = _engine(engine_factory, declare(shift=_ADJACENT))
    edge, now = _moment(engine, "15:00"), _moment(engine, "14:45")
    _clock(engine, now)
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="外出", goal="三点出门取快递", registered_at=now, remind_at=edge),))
    stage_errands(engine, 1, [_errand("staged:errand:0", "买牛奶", MILK_ID, now, remind_at=edge)])
    _, waited = _decide(engine, "wait", "start_activity", _wait())
    assert engine.context.state.activities[waited.activity_id].planned_end_at == edge
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == edge
    advance_next(engine)
    expected = ["planned_end", "no_active_activity", "plan_reminder_due", "errand_reminder_due", "break_start",
                "break_end"]
    assert _verdict(engine, edge) == (True, expected)
    observed, concluded = _decide(engine, "edge", "conclude_activity",
                                  conclude_request(activity_name(engine, waited.activity_id)))
    assert concluded.status == "completed"
    assert observed["environment_layer"]["duty"]["status"] == "休息时段"
    assert _started(engine, edge) == [expected]


# --- T9：零点 ---


def test_t9_midnight_edges_are_judged_by_the_midnight_rhythm(engine_factory) -> None:
    """周五 21:10–24:00、周一 00:00–05:10。周五 23:50 开等待：推进前的下一次决策时刻就是周六零点；零点推进之后判定段留到
    零点计划之后，零点节律入口判定的原因只有 shift_end，计划事件在前；周六零点的观察里 duty 是休息日对象。周日 23:50 开
    等待：推进前的下一次决策时刻就是周一零点，零点只有 shift_start。两人场景（周一 24:00 运行结束）只给人物 1 声明周一
    21:10–24:00：运行结束恰在零点，推进内的共同身体结算带 shift_end，没有人就绪。"""
    engine = _engine(engine_factory, declare(weekly=_MIDNIGHT_WEEK))
    saturday = _moment(engine, "00:00", "2026-01-10")
    waited, _ = _wait_from(engine, saturday - 600)
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == saturday
    engine.advance(600, engine.scheduling_state().next_event_at)
    assert engine.midnight_rhythm_pending and engine.context.state.current_time == saturday
    assert saturday not in _boundaries(engine)
    settle_day_plans(engine)
    assert _verdict(engine, saturday) == (True, ["shift_end"])
    [plan] = [event for event in engine.context.store.read_agent_events("environment_event", 1)
              if event.data.get("kind") == "day_plan_generated" and event.occurred_at == saturday]
    assert plan.event_seq < _settlement(engine, saturday).event_seq
    observed, reported = _report(engine, "saturday", waited)
    assert reported.status == "completed" and observed["environment_layer"]["duty"] == _REST
    assert _started(engine, saturday) == [["shift_end"]]

    monday_engine = _engine(engine_factory, declare(weekly=_MIDNIGHT_WEEK))
    monday = _moment(monday_engine, "00:00", "2026-01-12")
    _wait_from(monday_engine, monday - 600)
    assert monday_engine.context.state.agents[1].next_decision_at == _next_event(monday_engine) == monday
    advance_next(monday_engine)
    assert _verdict(monday_engine, monday) == (True, ["shift_start"])

    final = _engine(engine_factory, declare(0, weekly={"0": {"starts": 1270, "ends": 1440, "breaks": []}}),
                    scenario="multi-person.json")
    end = final.context.state.end_time
    assert end == _moment(final, "00:00", "2026-01-06")
    _clock(final, end - 600)
    submit_round(final, {1: ("start_activity", _wait()), 2: ("start_activity", _wait())}, "last")
    assert final.context.state.agents[1].next_decision_at == _next_event(final) == end
    final.advance(600, final.scheduling_state().next_event_at)
    assert not final.midnight_rhythm_pending and final.context.state.current_time == end
    assert _verdict(final, end, 1) == (True, ["shift_end"])
    assert final.scheduling_state().ready_agent_ids == ()


# --- T10–T12：回合制游戏、路上、通话与交谈 ---


def test_t10_a_game_turn_after_the_edge_does_not_hide_the_edge_from_the_clock(engine_factory) -> None:
    """17:24 开始一项游戏，存储里把它改成回合制（下一次交互 17:35）；同一秒播报之后游戏回合把下一次决策时刻设在 17:35，越过
    17:30 的班次结束，调度状态报告的下一边界仍是 17:30；时钟落在 17:30，人物就绪并带 shift_end，游戏的下一次交互仍是
    17:35。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    end, turn = _moment(engine, "17:30"), _moment(engine, "17:35")
    _clock(engine, _moment(engine, "17:24"))
    game = _start_game(engine)
    _stage_game_turn(engine, game, turn)
    stage(engine, lambda state: setattr(state.agents[1], "next_decision_at", state.current_time))
    _, reported = _report(engine, "game-turn", game)
    assert reported.status == "completed"
    assert engine.context.state.agents[1].next_decision_at == turn
    assert _next_event(engine) == end
    advance_next(engine)
    assert engine.context.state.current_time == end
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert engine.context.state.agents[1].pending_wake_reasons == ["shift_end"]
    assert _verdict(engine, end) == (True, ["shift_end"])
    assert _activity_facts(engine, game)[:3] == (ActivityStatus.ACTIVE, _moment(engine, "17:24") + 3600, turn)


def test_t11_on_the_road_the_walk_goes_on_and_pausing_is_still_refused(engine_factory) -> None:
    """17:24 步行去办公室（17:52:38 到）：17:30 在路上得到机会并带 shift_end；暂停照旧以 ACTIVITY_PAUSE_UNAVAILABLE 被拒，
    行程照常进行、计划到达不变，同一秒再决策播报它，之后按计划到达。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    end = _moment(engine, "17:30")
    _clock(engine, _moment(engine, "17:24"))
    _, walked = _decide(engine, "walk", "start_activity",
                        lambda observed: start_activity(*reversed(_pick(observed, "travel_to", place="office"))))
    assert walked.status == "in_progress"
    before = _activity_facts(engine, walked.activity_id)
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == end
    advance_next(engine)
    assert _verdict(engine, end) == (True, ["shift_end"])
    observed, paused = _decide(engine, "pause-walk", "pause_activity",
                               pause_request(activity_name(engine, walked.activity_id)))
    assert (paused.status, paused.error_code) == ("rejected", "ACTIVITY_PAUSE_UNAVAILABLE")
    assert observed["environment_layer"]["location"]["session_kind"] == "transit"
    assert _activity_facts(engine, walked.activity_id) == before
    _, reported = _report(engine, "walk-on", walked.activity_id)
    assert reported.status == "completed"
    assert _started(engine, end) == [["shift_end"], ["rejection_feedback"]]
    _drive(engine, lambda: engine.context.state.activities[walked.activity_id].status != ActivityStatus.ACTIVE)
    assert engine.context.state.activities[walked.activity_id].ended_at == before[1]


def test_t11_in_a_call_the_call_goes_on_and_pausing_is_still_refused(engine_factory) -> None:
    """17:24 给联系人陈母打电话（计划 17:39 结束）：17:30 通话中得到机会并带 shift_end；暂停照旧以
    SOCIAL_ACTIVITY_CANNOT_PAUSE 被拒，通话照常进行、计划结束不变，到 17:39 按计划结束。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    end = _moment(engine, "17:30")
    _clock(engine, _moment(engine, "17:24"))
    _, called = _decide(engine, "call", "start_activity", start_activity("通话", "联系人：陈母"))
    assert called.status == "in_progress"
    before = _activity_facts(engine, called.activity_id)
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == end
    advance_next(engine)
    assert _verdict(engine, end) == (True, ["shift_end"])
    _, paused = _decide(engine, "pause-call", "pause_activity",
                        pause_request(activity_name(engine, called.activity_id)))
    assert (paused.status, paused.error_code) == ("rejected", "SOCIAL_ACTIVITY_CANNOT_PAUSE")
    assert _activity_facts(engine, called.activity_id) == before
    _, reported = _report(engine, "call-on", called.activity_id)
    assert reported.status == "completed"
    _drive(engine, lambda: engine.context.state.activities[called.activity_id].status != ActivityStatus.ACTIVE)
    assert engine.context.state.activities[called.activity_id].ended_at == before[1] == _moment(engine, "17:39")


def test_t11_in_a_conversation_both_chats_go_on_and_pausing_is_still_refused(engine_factory) -> None:
    """两人场景只给人物 1 声明 08:10–17:30：17:24 人物 1 对人物 2 选择交谈、人物 2 接受。17:30 人物 1 交谈中得到机会并带
    shift_end，人物 2 的结算静默；暂停照旧以 SOCIAL_ACTIVITY_CANNOT_PAUSE 被拒，两边的交谈照常进行、计划结束不变。"""
    engine = _engine(engine_factory, declare(0, shift=_EVENING_END), scenario="multi-person.json")
    end = _moment(engine, "17:30")
    _clock(engine, _moment(engine, "17:24"))
    submit_round(engine, {1: _chat(engine, 1, 2), 2: ("start_activity", _wait())}, "invite")
    submit_round(engine, {2: _chat(engine, 2, 1)}, "accept")
    chats = _chats(engine)
    before = {agent_id: _activity_facts(engine, item.activity_id) for agent_id, item in chats.items()}
    assert all(facts[0] == ActivityStatus.ACTIVE and facts[1] > end for facts in before.values())
    _to_edge(engine, end)
    assert _verdict(engine, end, 1) == (True, ["shift_end"])
    assert _verdict(engine, end, 2) == (False, [])
    _, paused = _decide(engine, "pause-chat", "pause_activity",
                        pause_request(activity_name(engine, chats[1].activity_id)))
    assert (paused.status, paused.error_code) == ("rejected", "SOCIAL_ACTIVITY_CANNOT_PAUSE")
    assert {agent_id: _activity_facts(engine, item.activity_id) for agent_id, item in chats.items()} == before
    assert _started(engine, end) == [["shift_end"]]


def test_t12_in_a_game_the_turn_stays_and_the_game_only_stops_at_its_own_turn(engine_factory) -> None:
    """存储里一项进行中的回合制游戏（下一次交互 17:35，人物的下一次决策是这个回合），引擎推进到 17:30 的班次结束：人物得到
    机会并带 shift_end；暂停与结束都以 ACTIVITY_FEEDBACK_NOT_DUE 被拒，游戏照常进行，下一次交互不被移动；播报之后下一次
    决策回到 17:35 的回合，那时暂停才被受理。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    end, turn = _moment(engine, "17:30"), _moment(engine, "17:35")
    _clock(engine, _moment(engine, "17:24"))
    game = _start_game(engine)
    _stage_game_turn(engine, game, turn)
    stage(engine, lambda state: setattr(state.agents[1], "next_decision_at", turn))
    assert engine.scheduling_state().ready_agent_ids == () and _next_event(engine) == end
    advance_next(engine)
    assert _verdict(engine, end) == (True, ["shift_end"])
    before = _activity_facts(engine, game)
    name = activity_name(engine, game)
    _, paused = _decide(engine, "pause-game", "pause_activity", pause_request(name))
    _, ended = _decide(engine, "end-game", "conclude_activity", conclude_request(name))
    assert [(item.status, item.error_code) for item in (paused, ended)] == [
        ("rejected", "ACTIVITY_FEEDBACK_NOT_DUE")] * 2
    assert _activity_facts(engine, game) == before and before[2] == turn
    _, reported = _report(engine, "game-on", game)
    assert reported.status == "completed"
    assert engine.context.state.agents[1].next_decision_at == turn
    _drive(engine, lambda: engine.context.state.current_time == turn
           and bool(engine.scheduling_state().ready_agent_ids))
    _, paused_at_turn = _decide(engine, "pause-at-turn", "pause_activity", pause_request(name))
    assert paused_at_turn.status == "completed"
    assert [moment for moment, _ in _named(engine)] == [end]


# --- T13：大致时刻 ---


def test_t13_an_around_time_is_still_not_a_boundary(engine_factory) -> None:
    """14:04 以真实 revise_day_plan 写下大致时刻 14:25、不带提醒的目标，随后等待：14:10 与 14:40 的休息起止是边界并各带
    名字，14:25 不是边界，人物的决策也不落在 14:25。"""
    engine = _engine(engine_factory, declare(shift=_DAY))
    around = _moment(engine, "14:25")
    _clock(engine, _moment(engine, "14:04"))
    request = ReviseDayPlanRequest.model_validate({"goals": [
        {"intent_kind": "休息", "goal": "下午歇一会儿，喝杯茶", "remind_time": None, "around_time": "14:25"}]})
    _, revised = _decide(engine, "revise", "revise_day_plan", request)
    assert revised.status == "completed"
    [goal] = engine.context.state.agents[1].day_plan.goals
    assert (goal.around_at, goal.remind_at) == (around, None)
    _, waited = _decide(engine, "wait", "start_activity", _wait())
    assert waited.status == "in_progress"
    assert engine.context.state.agents[1].next_decision_at == _next_event(engine) == _moment(engine, "14:10")
    _drive(engine, lambda: engine.context.state.current_time >= _moment(engine, "14:45"))
    assert around not in _boundaries(engine) and around not in [moment for moment, _ in _decisions(engine)]
    assert _verdict(engine, _moment(engine, "14:10")) == (True, ["break_start"])
    assert _verdict(engine, _moment(engine, "14:40")) == (True, ["break_end"])


# --- T14、T15：恢复、落库与质量报告 ---


def _branches(engine: LifeEngine, checkpoint, tmp_path, observation_id: str) -> list[LifeEngine]:
    """@brief 从检查点恢复的普通分支与反事实分支（后者在首个决策前提交一次余额干预），用引擎创建时那份改写后的场景文件
    打开。"""
    scenario = tmp_path / "scenario-0" / "scenarios" / "white-collar-12-weeks.json"
    restore_branch(engine.context.store, checkpoint.checkpoint_id, tmp_path / "resumed").close()
    resumed = LifeEngine.open(scenario, tmp_path / "resumed")
    intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=123_456,
                                              hypothesis="More money changes the evening.",
                                              source_event_ids=(observation_id,))
    metadata = {"branch_kind": "counterfactual", "intervention_sha256": _intervention_sha256(intervention)}
    restore_branch(engine.context.store, checkpoint.checkpoint_id, tmp_path / "counterfactual", metadata).close()
    counterfactual = LifeEngine.open(scenario, tmp_path / "counterfactual")
    counterfactual.apply_counterfactual(intervention)
    return [resumed, counterfactual]


def test_t14_after_restore_and_a_counterfactual_branch_the_edge_fires_once(engine_factory, tmp_path) -> None:
    """17:30 的 shift_end 已判定而尚未决策时提交共同检查点：世界结构仍是 9，人物状态的键集合不变，已判定的原因留在
    pending_wake_reasons。原运行、普通恢复的分支与反事实分支各自在 17:30 就绪并带 shift_end，决策一次之后照常过半小时，
    每条运行里带四个名字的决策都只有 17:30 那一次。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    end = _moment(engine, "17:30")
    _, observed = _wait_from(engine, _moment(engine, "17:24"))
    advance_next(engine)
    checkpoint = _checkpoint(engine)
    assert checkpoint.state["schema_version"] == WORLD_SCHEMA_VERSION == 9
    assert set(checkpoint.state["agents"]["1"]) == _AGENT_KEYS
    assert checkpoint.state["agents"]["1"]["pending_wake_reasons"] == ["shift_end"]
    branches = _branches(engine, checkpoint, tmp_path, observed["observation_event_id"])
    try:
        for run in (engine, *branches):
            assert run.context.state.current_time == end and run.scheduling_state().ready_agent_ids == (1,)
            assert run.context.state.agents[1].pending_wake_reasons == ["shift_end"]
            assert _named(run) == []
            _drive(run, lambda: run.context.state.current_time >= end + 1800)
            assert _named(run) == [(end, ["shift_end"])]
            assert run.context.state.schema_version == WORLD_SCHEMA_VERSION
            assert set(run.context.state.agents[1].model_dump(mode="json")) == _AGENT_KEYS
    finally:
        for branch in branches:
            branch.close()


def test_t15_the_four_names_are_known_wake_reasons_in_the_quality_report() -> None:
    """质量报告按足迹侧词表分桶：四个名字在共同身体结算与 decision_started 里都计入已知原因，与同一秒的其他原因各计一次，
    两个未识别原因的分桶为空。"""
    edges = list(_NAMES)
    events = [
        _event(1, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                                         WAKE_REASONS_FIELD: ["planned_end", *edges]}),
        _event(2, "decision_started", agent_id=1, decision_id="decision-1",
               data=_started_data(["planned_end", *edges])),
        _event(3, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                                         WAKE_REASONS_FIELD: ["shift_end"]}),
        _event(4, "decision_started", agent_id=1, decision_id="decision-2", data=_started_data(["shift_end"])),
        _event(5, "run_ended", occurred_at=1100),
    ]
    rhythm = build_quality_report(events, [], _manifest())["decision_rhythm"]
    expected = {"planned_end": 1, "shift_start": 1, "shift_end": 2, "break_start": 1, "break_end": 1}
    assert rhythm["wake_reason_counts"] == rhythm["decision_started_wake_reason_counts"] == expected
    assert rhythm["unrecognized_wake_reason_counts"] == rhythm["unrecognized_decision_started_wake_reason_counts"] == {}
    assert rhythm["offered_boundaries"] == 2


def test_t15_an_offline_run_with_a_shift_exports_validates_and_reports(engine_factory, tmp_path) -> None:
    """两人场景只给人物 1 声明 22:10–23:50、休息 22:40–23:10，22:04 起两人照常过日子（脚本化的决策经真实请求、引擎与
    SQLite）直到运行终点，提交共同检查点并认证导出：校验通过，quality.json 结构为 4，四个名字在结算与 decision_started
    两处各计一次，两个未识别分桶为空，从导出的事件逐值重算出相同的报告。"""
    engine = _engine(engine_factory, declare(0, shift=_NIGHT), scenario="multi-person.json")
    _clock(engine, _moment(engine, "22:04"))
    _drive(engine, lambda: engine.context.state.current_time == engine.context.state.end_time)
    checkpoint = _checkpoint(engine)
    export_dir = tmp_path / "shift-export"
    assert export_run(engine.context.store, checkpoint.last_event_seq, export_dir)["experiment_completed"] is True
    manifest, events, activities = validate_export(export_dir)
    quality = json.loads((export_dir / "quality.json").read_text(encoding="utf-8"))
    assert quality["schema_version"] == 4
    rhythm = quality["decision_rhythm"]
    for bucket in ("wake_reason_counts", "decision_started_wake_reason_counts"):
        assert {name: rhythm[bucket].get(name) for name in _NAMES} == dict.fromkeys(_NAMES, 1)
    assert rhythm["unrecognized_wake_reason_counts"] == rhythm["unrecognized_decision_started_wake_reason_counts"] == {}
    rebuilt = build_quality_report([Event.model_validate(row) for row in events], activities,
                                   manifest["frozen_run_manifest"])
    assert rebuilt == quality


# --- T19、T20：同一秒醒来、运行开始与预算暂停 ---


def test_t19_an_alarm_at_the_shift_start_keeps_the_edge_after_the_reminder(engine_factory) -> None:
    """周一至周五 06:30–17:30：周一 23:20 入睡，零点计划给周二写 06:30 的起床提醒。06:30 提醒结束睡眠、班次也在这一秒开始：
    判定时人已清醒，shift_start 照记并排在已有原因之后（枚举次序），decision_started 带同样的原因。"""
    engine = _engine(engine_factory, declare(shift=_ALARM))
    alarm = _moment(engine, "06:30", "2026-01-06")
    _clock(engine, _moment(engine, "23:20"))
    _, slept = _decide(engine, "sleep", "start_activity", start_activity("睡觉", "自己"))
    assert engine.context.state.activities[slept.activity_id].planned_end_at > alarm
    client = StubPlanDayClient(goals=[{"intent_kind": "晨间例程", "goal": "六点半起床去上班", "remind_time": "06:30",
                                       "around_time": "06:30"}])
    _drive(engine, lambda: engine.context.state.current_time >= alarm, client)
    sleep = engine.context.state.activities[slept.activity_id]
    assert (sleep.status, sleep.ended_at, sleep.interruption_reason) == ("completed", alarm, "plan_reminder")
    offered, reasons = _verdict(engine, alarm)
    assert offered and reasons == [member.value for member in WakeReason if member.value in reasons]
    assert {"wake_from_sleep", "plan_reminder_due"} <= set(reasons) and reasons.count("shift_start") == 1
    assert reasons[-1] == "shift_start" and not {"shift_end", "break_start", "break_end"} & set(reasons)
    _, concluded = _decide(engine, "woke", "conclude_activity",
                           conclude_request(activity_name(engine, sleep.activity_id)))
    assert concluded.status == "completed"
    assert _started(engine, alarm)[0] == reasons


def test_t19_a_run_starting_on_a_shift_start_records_only_run_start(engine_factory) -> None:
    """周一 00:00–05:10 当班，运行恰在 2026-01-05 00:00 开始：第一次决策只带 run_start，不带 shift_start；第一次观察的
    duty.status 已是当班中；这一秒之后再没有 shift_start。"""
    engine = _engine(engine_factory, declare(weekly=_MIDNIGHT_WEEK))
    start = engine.context.state.current_time
    assert start == _moment(engine, "00:00")
    assert engine.context.state.agents[1].pending_wake_reasons == ["run_start"]
    observed, waited = _decide(engine, "first", "start_activity", _wait())
    assert waited.status == "in_progress"
    assert observed["environment_layer"]["duty"]["status"] == "当班中"
    assert _started(engine, start) == [["run_start"]]
    _drive(engine, lambda: engine.context.state.current_time > start + 1800)
    assert _named(engine) == []


def test_t20_an_edge_ends_the_technical_pause_after_the_same_second_budget(engine_factory) -> None:
    """17:24 没有进行中活动，同刻上限加三轮都被拒：预算耗尽后的技术性暂停把下一次决策排在 17:39，调度状态报告的下一边界
    却是 17:30 的班次结束；时钟落在 17:30，本人的结算以 no_active_activity 与 shift_end 提供决策，暂停随之结束，
    decision_started 带同样的原因。"""
    engine = _engine(engine_factory, declare(shift=_EVENING_END))
    now, end = _moment(engine, "17:24"), _moment(engine, "17:30")
    _clock(engine, now)
    for index in range(engine.context.scenario.max_same_moment_decisions + 3):
        assert _invalid_start(engine, f"budget-{index}").status == "rejected"
    exhausted = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                 if event.data.get("kind") == "decision_budget_exhausted"]
    assert len(exhausted) == 1
    feedback = engine.context.scenario.activity_feedback_seconds
    assert engine.context.state.agents[1].next_decision_at == now + feedback > end
    assert engine.scheduling_state().ready_agent_ids == () and _next_event(engine) == end
    advance_next(engine)
    assert engine.context.state.current_time == end
    assert _verdict(engine, end) == (True, ["no_active_activity", "shift_end"])
    assert engine.scheduling_state().ready_agent_ids == (1,)
    _, waited = _decide(engine, "after-pause", "start_activity", _wait())
    assert waited.status == "in_progress"
    assert _started(engine, end) == [["no_active_activity", "shift_end"]]
