"""今天条目在手写事件上的分支：经校验的请求或夹具环境走不到的情形（空白总结、失败与睡过头、各条完整性错误、零点收尾）。

存根足迹库按序号存手写事件，seal 把自上次以来的事件登记成一次提交；夹具输入同 test_memory_progress_contract.py。
2026-01-05 是夹具运行的第一天。
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.configuration.inputs.environment import load_environment
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.observation.memory.categories import memory_book
from worldmock.simulation_engine.observation.memory.days import day_window, past_day_entries, published_today
from worldmock.simulation_engine.observation.memory.entries import agent_events, build_closing_entry
from worldmock.state.world.activity import ActivityState

DAY = "2026-01-05"


@pytest.fixture
def inputs():
    path = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"
    metadata = lambda config: ModelMetadata(requested_model=config.requested_model,
        metadata_url=config.metadata_url, context_window=262144, max_output_tokens=8192)
    return load_environment(path, metadata_loader=metadata)


def at(clock: str, day: str = DAY) -> int:
    """上海时区的本地日期与 HH:MM[:SS] 写成纪元秒。"""
    return int(datetime.fromisoformat(f"{day}T{clock}{':00' if clock.count(':') == 1 else ''}+08:00").timestamp())


class Log:
    """存根足迹库：手写事件按序号存放；seal 把自上次以来写下的事件登记成一次提交，commit_last_seq 读它。"""

    def __init__(self):
        self.rows: list[SimpleNamespace] = []
        self.ends: dict[int, int] = {}
        self.sealed = 0

    @property
    def last_event_seq(self) -> int:
        return len(self.rows)

    def read_agent_events(self, event_type, agent_id, after=0):
        return [row for row in self.rows if row.event_type == event_type and row.agent_id == agent_id
                and row.event_seq > after]

    def commit_last_seq(self, event_seq):
        return self.ends[event_seq]

    def last_event_seq_before(self, moment):
        return max((row.event_seq for row in self.rows if row.occurred_at < moment), default=0)

    def add(self, event_type, moment, data, **fields) -> SimpleNamespace:
        """写一条本人事件。"""
        seq = len(self.rows) + 1
        row = SimpleNamespace(event_type=event_type, event_id=f"e-{seq}", event_seq=seq, agent_id=1,
                              occurred_at=moment, decision_id=None, action_id=None, activity_id=None, caused_by=[],
                              visibility="agent", source_kind="environment", data=data)
        for key, value in fields.items():
            setattr(row, key, value)
        self.rows.append(row)
        return row

    def seal(self) -> None:
        """自上次 seal 以来的事件是一次提交。"""
        for seq in range(self.sealed + 1, len(self.rows) + 1):
            self.ends[seq] = len(self.rows)
        self.sealed = len(self.rows)


def snapshot(activity_id, action, start="08:00", end=None, status="active", kind="interact", name=None,
             **data) -> dict:
    """一项活动的快照（JSON 形状）：开始请求的动作身份、起止与状态；interact 缺省是在家做「测试闲聊」。"""
    started = at(start)
    ended = None if end is None else at(end) if isinstance(end, str) else end
    fields = {"interact": {"app_id": None, "content_id": "c-chat", "played": []}, "sleep": {"oversleep_seconds": 0}}
    return ActivityState(
        activity_id=activity_id, agent_id=1, location_id="home", kind=kind, status=status, started_at=started,
        ended_at=ended, planned_end_at=(ended or started) + 3600, last_settled_at=ended or started, elapsed_seconds=0,
        planned_duration_seconds=3600, origin_action_id=action, origin_decision_id="d", origin_request_event_id="e",
        boundary_type="planned_end" if status == "completed" else None,
        name=f"进行测试闲聊（1月5日 {start} 起）" if name is None else name,
        data={**fields.get(kind, {}), **data}).model_dump(mode="json")


def start(log, decision, moment, activity, note=None, status="in_progress"):
    """一次只开始一项活动的决策：请求与受理结果（结果带这项活动的快照）同一次提交。"""
    request = log.add("action_requested", moment, {"tool_name": "start_main_activity",
                      "request": {"action": "interact", "target": "家", "detail": "测试闲聊"}, "self_note": note},
                      decision_id=decision, action_id=activity["origin_action_id"])
    log.add("action_result", moment, {"status": status, "activity": activity, "data": {}}, decision_id=decision,
            action_id=request.action_id, activity_id=activity["activity_id"])
    log.seal()
    return request


def control(log, decision, moment, tool, names, activity_ids, summaries=None, status="completed"):
    """一次控制请求的决策：逐项点名活动，结果写 activity_ids。"""
    texts = summaries or ["继续。"] * len(names)
    items = [{"activity_name": name, "summary": text, **({"memories": [], "views": []}
             if tool == "conclude_activity" else {})} for name, text in zip(names, texts)]
    request = log.add("action_requested", moment, {"tool_name": tool, "request": {"items": items, "errands": []},
                      "self_note": None}, decision_id=decision, action_id=f"{decision}:a")
    log.add("action_result", moment, {"status": status, "data": {"activity_ids": list(activity_ids)}},
            decision_id=decision, action_id=request.action_id)
    log.seal()
    return request


def revise(log, decision, moment):
    """一次受理的改计划决策。"""
    request = log.add("action_requested", moment, {"tool_name": "revise_day_plan", "request": {"goals": []},
                      "self_note": None}, decision_id=decision, action_id=f"{decision}:a")
    log.add("action_result", moment, {"status": "completed", "data": {}}, decision_id=decision,
            action_id=request.action_id)
    log.seal()


def ended(log, moment, activity, terminal=True):
    """一项活动在时钟推进里结束：终态结果给原请求（terminal 为假时只有带终态快照的进度事件）。"""
    if terminal:
        log.add("action_result", moment, {"status": "completed", "activity": activity, "data": {"activity": activity}},
                action_id=activity["origin_action_id"], activity_id=activity["activity_id"])
    else:
        log.add("activity_progress", moment, {"activity": activity}, activity_id=activity["activity_id"])
    log.seal()


def today(inputs, log, moment):
    """这一刻已发布的今天条目。"""
    return published_today(inputs, log, 1, moment, log.last_event_seq).entries


def _shown(inputs, log, moment):
    """这一刻最后一条今天条目的渲染形状。"""
    return today(inputs, log, moment)[-1].shown


@pytest.mark.parametrize("tool,verb", [("report_status", "播报"), ("conclude_activity", "收尾")])
def test_blank_summaries_fall_back_to_the_request_action(inputs, tool, verb):
    """受理的控制请求两项总结都是空白：条目只有一行兜底核验「播报A、B」或「收尾A、B」，没有自述。"""
    log = Log()
    first = snapshot("a-1", "d1:a")
    second = snapshot("a-2", "d2:a", start="08:05", name="进行哔哩哔哩（1月5日 08:05 起）", app_id="bilibili",
                      content_id="c-article")
    start(log, "d1", at("08:00"), first)
    start(log, "d2", at("08:05"), second)
    if tool == "conclude_activity":
        ended(log, at("08:20"), {**first, "status": "completed", "ended_at": at("08:20"),
                                 "last_settled_at": at("08:20"), "boundary_type": "planned_end"})
        ended(log, at("08:20"), {**second, "status": "completed", "ended_at": at("08:20"),
                                 "last_settled_at": at("08:20"), "boundary_type": "planned_end"})
        revise(log, "d3", at("08:20"))
    control(log, "d4", at("08:30"), tool, [first["name"], second["name"]], ["a-1", "a-2"], [" ", " "])
    assert _shown(inputs, log, at("08:30")) == {"时刻": "08:30", "类别": "生活", "核验": [f"{verb}测试闲聊、哔哩哔哩"]}


def test_a_failed_ending_and_an_oversleep_are_written_as_outcomes(inputs):
    """终态 failed 的活动写「，失败」；按计划完成而多睡 750 秒的睡眠写「，多睡12分钟」。"""
    log = Log()
    chat = snapshot("a-1", "d1:a")
    nap = snapshot("a-2", "d2:a", start="08:05", kind="sleep", name="睡觉（1月5日 08:05 起）")
    start(log, "d1", at("08:00"), chat)
    start(log, "d2", at("08:05"), nap)
    ended(log, at("08:10"), {**chat, "status": "failed", "ended_at": at("08:10"), "last_settled_at": at("08:10")})
    ended(log, at("08:30"), {**nap, "status": "completed", "ended_at": at("08:30"), "last_settled_at": at("08:30"),
                             "boundary_type": "planned_end", "data": {"oversleep_seconds": 750}})
    revise(log, "d3", at("08:30"))
    assert _shown(inputs, log, at("08:30"))["核验"] == ["测试闲聊 08:00–08:10，失败", "睡觉 08:05–08:30，多睡12分钟",
                                                       "改了今日计划"]


def _expect(inputs, log, moment, message, caplog):
    """取今天条目以这条英文错误失败，并写同文的错误日志。"""
    with pytest.raises(IntegrityError, match=f"^{message}$"):
        today(inputs, log, moment)
    assert message in caplog.text


def test_decision_without_a_result_fails_closed(inputs, caplog):
    """计入的决策请求没有任何结果：抛 Decision world request has no committed result，并写同文错误日志。"""
    log = Log()
    log.add("action_requested", at("08:00"), {"tool_name": "revise_day_plan", "request": {"goals": []}},
            decision_id="d1", action_id="d1:a")
    log.seal()
    _expect(inputs, log, at("08:00"), "Decision world request has no committed result", caplog)


def test_aborted_decision_fails_closed(inputs, caplog):
    """第一条结果是 failed 且 DECISION_ROUND_ABORTED：抛 Aborted decision has no today entry，并写同文错误日志。"""
    log = Log()
    request = log.add("action_requested", at("08:00"), {"tool_name": "revise_day_plan", "request": {"goals": []}},
                      decision_id="d1", action_id="d1:a")
    log.add("action_result", at("08:00"), {"status": "failed", "error_code": "DECISION_ROUND_ABORTED", "data": {}},
            decision_id="d1", action_id=request.action_id)
    log.seal()
    _expect(inputs, log, at("08:00"), "Aborted decision has no today entry", caplog)


def test_disagreeing_and_unknown_results_fail_closed(inputs, caplog):
    """开始组一条受理、一条被拒：结果不一致；第一条结果是 cancelled：不能成为条目。"""
    log = Log()
    chat = snapshot("a-1", "d1:a1")
    main = log.add("action_requested", at("08:00"), {"tool_name": "start_main_activity", "request": {
        "action": "interact", "target": "家", "detail": "测试闲聊"}}, decision_id="d1", action_id="d1:a1")
    side = log.add("action_requested", at("08:00"), {"tool_name": "start_side_activity", "request": {
        "action": "purchase", "target": ["牛肉面"], "prefer": None}}, decision_id="d1", action_id="d1:a2")
    log.add("action_result", at("08:00"), {"status": "in_progress", "activity": chat, "data": {}}, decision_id="d1",
            action_id=main.action_id, activity_id="a-1")
    log.add("action_result", at("08:00"), {"status": "rejected", "data": {}}, decision_id="d1",
            action_id=side.action_id)
    log.seal()
    _expect(inputs, log, at("08:00"), "Decision results disagree", caplog)
    log = Log()
    request = log.add("action_requested", at("08:00"), {"tool_name": "revise_day_plan", "request": {"goals": []}},
                      decision_id="d1", action_id="d1:a")
    log.add("action_result", at("08:00"), {"status": "cancelled", "data": {}}, decision_id="d1",
            action_id=request.action_id)
    log.seal()
    _expect(inputs, log, at("08:00"), "Decision result status cannot form a today entry", caplog)


def test_accepted_start_without_activity_and_short_control_ids_fail_closed(inputs, caplog):
    """受理的开始结果没有活动身份；两项的控制请求结果只有一个活动身份。"""
    log = Log()
    request = log.add("action_requested", at("08:00"), {"tool_name": "start_main_activity", "request": {
        "action": "interact", "target": "家", "detail": "测试闲聊"}}, decision_id="d1", action_id="d1:a")
    log.add("action_result", at("08:00"), {"status": "in_progress", "data": {}}, decision_id="d1",
            action_id=request.action_id)
    log.seal()
    _expect(inputs, log, at("08:00"), "Accepted start has no started activity", caplog)
    log = Log()
    chat = snapshot("a-1", "d1:a")
    start(log, "d1", at("08:00"), chat)
    control(log, "d2", at("08:10"), "report_status", [chat["name"], chat["name"]], ["a-1"])
    _expect(inputs, log, at("08:10"), "Control result activities disagree with its items", caplog)


def test_missing_terminal_or_window_snapshot_fails_closed(inputs, caplog):
    """结束事件在窗口里而快照仍是进行中；收尾结果点名一项窗口上界以内没有快照的活动；快照的开始动作没有任何结果。"""
    log = Log()
    chat = snapshot("a-1", "d1:a")
    start(log, "d1", at("08:00"), chat)
    ended(log, at("08:10"), {**chat, "status": "completed", "ended_at": at("08:10"), "last_settled_at": at("08:10"),
                             "boundary_type": "planned_end"}, terminal=False)
    revise(log, "d2", at("08:10"))
    _expect(inputs, log, at("08:10"), "Ended activity has no terminal snapshot", caplog)
    log = Log()
    control(log, "d1", at("08:00"), "conclude_activity", ["进行测试闲聊（1月5日 07:00 起）"], ["ghost"])
    _expect(inputs, log, at("08:00"), "Activity has no snapshot within the entry window", caplog)
    log = Log()
    orphan = snapshot("a-1", "never-requested")
    request = log.add("action_requested", at("08:00"), {"tool_name": "start_main_activity", "request": {
        "action": "interact", "target": "家", "detail": "测试闲聊"}}, decision_id="d1", action_id="d1:a")
    log.add("action_result", at("08:00"), {"status": "in_progress", "activity": orphan, "data": {}},
            decision_id="d1", action_id=request.action_id, activity_id="a-1")
    log.seal()
    _expect(inputs, log, at("08:00"), "Activity has no start result", caplog)


def test_an_unnamed_activity_fails_closed(inputs, caplog):
    """活动快照的冻结名称为空：抛 Activity has no frozen name，并写同文错误日志。"""
    log = Log()
    start(log, "d1", at("08:00"), snapshot("a-1", "d1:a", name=""))
    _expect(inputs, log, at("08:00"), "Activity has no frozen name", caplog)


def test_a_delivery_with_an_unknown_order_status_fails_closed(inputs, caplog):
    """送达事件里本人订单的 status 是 pending：抛 Delivery arrival order status is not delivered or ready_for_pickup，并写同文错误日志。"""
    log = Log()
    log.add("environment_event", at("08:05"), {"kind": "delivery_arrival", "orders": [
        {"agent_id": 1, "goods_id": "lemon-tea", "status": "pending"}]})
    log.seal()
    revise(log, "d1", at("08:05"))
    _expect(inputs, log, at("08:05"), "Delivery arrival order status is not delivered or ready_for_pickup", caplog)


def test_settled_lines_ignore_system_events_and_world_records(inputs):
    """窗口里只有余额减少、app 推送、回调提醒、事务提醒与系统可见的送达：没有已结算事实的行。"""
    log = Log()
    log.add("environment_event", at("08:01"), {"kind": "balance_decreased", "amount_fen": 1234})
    log.add("environment_event", at("08:01"), {"kind": "app_push", "app_id": "bilibili"})
    log.add("environment_event", at("08:01"), {"kind": "callback_reminder", "rule_id": "r", "app_id": "a", "text": "t"})
    log.add("environment_event", at("08:01"), {"kind": "errand_reminded", "errand": {"name": "备好饮料"}})
    log.add("environment_event", at("08:01"), {"kind": "delivery_arrival", "orders": [
        {"agent_id": 1, "goods_id": "lemon-tea", "status": "delivered"}]}, visibility="system")
    log.seal()
    revise(log, "d1", at("08:05"))
    assert _shown(inputs, log, at("08:05")) == {"时刻": "08:05", "类别": "生活", "核验": ["改了今日计划"]}


def _closing(inputs, log, day=DAY):
    """某日结束之后求那天的全部条目，返回 build_closing_entry 给出的零点收尾条目（没有时为 None）。"""
    past_day_entries(inputs, log, 1, day, day_window(day, inputs.config.timezone).end)
    return agent_events(log, 1, inputs.catalog.kit_states()).closings[day]


def _resting(log, activity_id, clock, label, title):
    """一项在家做的事（冻结名称写 title）：开始决策。"""
    activity = snapshot(activity_id, f"{label}:a", start=clock, name=f"进行{title}（1月5日 {clock} 起）")
    start(log, label, at(clock), activity)
    return activity


def test_closing_entry_skips_paused_and_already_ended_activities(inputs):
    """23:40 暂停、零点后才恢复的与 23:50 结束、零点后才收尾的不写跨过零点行，零点时进行中的写；从没暂停过的与暂停后
    已恢复的都按进行中写。只有前两项、23:50 的结束事件在 23:55 一次决策的窗口里时没有零点收尾条目。"""
    for case in ("three", "two"):
        log = Log()
        paused = _resting(log, "a-1", "23:00", "d1", "暂停的事")
        done = _resting(log, "a-2", "23:10", "d2", "做完的事")
        if case == "three":
            _resting(log, "a-3", "23:20", "d3", "进行中的事")
            resumed = _resting(log, "a-4", "23:30", "d4", "恢复过的事")
            for clock, kind in (("23:32", "activity_paused"), ("23:35", "activity_resumed")):
                log.add("environment_event", at(clock), {"kind": kind, "activity_id": resumed["activity_id"]})
            log.seal()
        log.add("environment_event", at("23:40"), {"kind": "activity_paused", "activity_id": paused["activity_id"]})
        log.seal()
        ended(log, at("23:50"), {**done, "status": "completed", "ended_at": at("23:50"), "last_settled_at": at("23:50"),
                                 "boundary_type": "planned_end"})
        revise(log, "d5", at("23:55"))
        log.add("environment_event", at("00:10", "2026-01-06"), {"kind": "activity_resumed",
                                                                 "activity_id": paused["activity_id"]})
        log.seal()
        closing = _closing(inputs, log)
        if case == "two":
            assert closing is None
        else:
            assert [line.text for line in closing.lines] == ["23:20 开始进行中的事，跨过零点", "23:30 开始恢复过的事，跨过零点"]
            assert (closing.clock, closing.shown, closing.decision_id) == ("24:00", None, None)


def test_a_crossing_activity_that_spans_two_midnights_gets_a_line_each_night(inputs):
    """1 月 4 日 23:00 开始、跨过两个零点的活动：两天的零点收尾条目各有一行，第二天写「昨23:00 开始…」。"""
    log = Log()
    activity = snapshot("a-1", "d1:a", start="23:00", name="进行测试闲聊（1月4日 23:00 起）")
    activity.update(started_at=at("23:00", "2026-01-04"), last_settled_at=at("23:00", "2026-01-04"))
    start(log, "d1", at("23:00", "2026-01-04"), activity)
    log.add("environment_event", at("12:00"), {"kind": "time_trigger"}, visibility="system")
    log.seal()
    log.add("environment_event", at("00:30", "2026-01-06"), {"kind": "time_trigger"}, visibility="system")
    log.seal()
    first = _closing(inputs, log, "2026-01-04")
    second = _closing(inputs, log, DAY)
    assert [line.text for line in first.lines] == ["23:00 开始测试闲聊，跨过零点"]
    assert [line.text for line in second.lines] == ["昨23:00 开始测试闲聊，跨过零点"]


def test_two_same_short_names_in_one_entry_keep_their_full_names(inputs):
    """一次收尾的两项是冻结名称只差开始时刻的两次哔哩哔哩，两者都在这个窗口里结束：两行结束行与两项总结都用完整名称；
    另一条只点名其中一项的条目用短名。"""
    log = Log()
    names = ["进行哔哩哔哩（1月5日 08:00 起）", "进行哔哩哔哩（1月5日 08:20 起）"]
    first = snapshot("a-1", "d1:a", name=names[0], app_id="bilibili", content_id="c-article")
    second = snapshot("a-2", "d2:a", start="08:20", name=names[1], app_id="bilibili", content_id="c-article")
    start(log, "d1", at("08:00"), first)
    control(log, "d2-report", at("08:10"), "report_status", [names[0]], ["a-1"], ["看了一会。"])
    start(log, "d2", at("08:20"), second)
    for activity in (first, second):
        ended(log, at("08:40"), {**activity, "status": "completed", "ended_at": at("08:40"),
                                 "last_settled_at": at("08:40"), "boundary_type": "planned_end"})
    control(log, "d3", at("08:45"), "conclude_activity", names, ["a-1", "a-2"], ["看完了。", "也看完了。"])
    entries = today(inputs, log, at("08:45"))
    assert entries[1].shown["自述"] == ["进展·哔哩哔哩：看了一会。"]
    assert entries[-1].shown["核验"] == [f"{names[0]} 08:00–08:40", f"{names[1]} 08:20–08:40"]
    assert entries[-1].shown["自述"] == [f"{names[0]}：看完了。", f"{names[1]}：也看完了。"]


def test_day_entries_before_the_day_ended_fail_closed(inputs, caplog):
    """1 月 5 日 23:59:59 求那天的条目是完整性故障；恰在 1 月 6 日 00:00:00、存储里最后一个事件在 23:50 时照常返回。"""
    log = Log()
    revise(log, "d1", at("23:50"))
    with pytest.raises(IntegrityError, match="^Day entries requested before the day ended$"):
        past_day_entries(inputs, log, 1, DAY, at("23:59:59"))
    assert "Day entries requested before the day ended" in caplog.text
    (entry,) = past_day_entries(inputs, log, 1, DAY, at("00:00", "2026-01-06"))
    assert entry.shown == {"时刻": "23:50", "类别": "生活", "核验": ["改了今日计划"]}
