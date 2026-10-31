"""当前环境记录拒绝退役事件、伪造转账和开放正文。"""

import pytest
from pydantic import ValidationError

from worldmock.recording_engine.events.models import EventDraft


def fact(data, **changes):
    return EventDraft(event_type="environment_event", occurred_at=100, agent_id=1,
                      source_kind="environment", visibility="system", data=data, **changes)


def transfer(**changes):
    return {"kind": "callback_effect", "type": "transfer", "rule_id": "fee", "direction": "expense",
            "amount_fen": 100, "from_account_id": "agent:1", "to_account_id": "external:scenario",
            "status": "paid", **changes}


@pytest.mark.parametrize("kind", ["alarm_ring", "message_arrival", "mail_arrival", "model_npc_reply_pending",
                                  "model_npc_reply", "work_content_completed", "bill_due", "payroll",
                                  "goal_progressed"])
def test_retired_environment_event_is_rejected(kind):
    with pytest.raises(ValidationError, match="retired kind"):
        fact({"kind": kind})


def test_paid_transfer_has_exact_actual_account_changes():
    changes = {"account:agent:1": -100, "account:external:scenario": 100}
    assert fact(transfer(), caused_by=["actual-trigger"], resource_changes=changes).resource_changes == changes
    with pytest.raises(ValidationError, match="payment status"):
        fact(transfer(), caused_by=["actual-trigger"])
    with pytest.raises(ValidationError, match="payment status"):
        fact(transfer(status="unpaid"), caused_by=["actual-trigger"], resource_changes=changes)


@pytest.mark.parametrize("changes", [{"amount_fen": True}, {"amount_fen": "100"}, {"amount_fen": 0},
                                     {"from_account_id": "merchant"}, {"direction": "outgoing"}, {"legacy": 1}])
def test_transfer_fields_are_strict(changes):
    with pytest.raises(ValidationError):
        fact(transfer(**changes), caused_by=["actual-trigger"])


@pytest.mark.parametrize("causes", [[], ["one", "two"]])
def test_callback_requires_one_actual_trigger(causes):
    with pytest.raises(ValidationError, match="one actual"):
        fact(transfer(status="unpaid"), caused_by=causes)


def test_time_trigger_is_closed_independent_fact():
    data = {"kind": "time_trigger", "key": "hourly", "scheduled_id": "time-hourly-100"}
    assert fact(data).data == data
    for payload in ({**data, "extra": 1}, {**data, "key": "payroll"}):
        with pytest.raises(ValidationError):
            fact(payload)
    with pytest.raises(ValidationError, match="independent"):
        fact(data, caused_by=["invented-source"])


def test_weather_requires_actual_factor_and_canonical_date():
    data = {"kind": "weather", "local_date": "2026-01-05", "condition": "snow",
            "speed_factor": 0.6, "scheduled_id": "weather-2026-01-05"}
    assert fact(data).data == data
    for payload in ({**data, "speed_factor": 1}, {**data, "local_date": "20260105"}, {**data, "extra": 1}):
        with pytest.raises(ValidationError):
            fact(payload)


def _digest_data(tier="day", period="2026-10-09", **changes):
    """一条合法的回顾登记正文：饮食有回顾、工作没有输入、生活没能整理。"""
    item = {"text": "收到了柠檬茶。", "source_event_ids": ["e1", "e2"], "cited_event_ids": ["e2"]}
    data = {"kind": "memory_digest_generated", "tier": tier, "period": period,
            "digests": {"food": item, "work": None, "life": None}, "fallback": ["life"]}
    data.update(changes)
    return data


def _digest(data, **changes):
    """回顾登记事件草稿：来源 model、可见性 system、起因恰一项。"""
    fields = {"source_kind": "model", "visibility": "system", "caused_by": ["response"], **changes}
    return EventDraft(event_type="environment_event", occurred_at=100, agent_id=1, data=data, **fields)


@pytest.mark.parametrize("tier,period", [("day", "2026-10-09"), ("month", "2026-10"), ("year", "2026")],
                         ids=["day", "month", "year"])
def test_valid_digest_events_are_accepted(tmp_path, tier, period):
    """三级的合法登记草稿可以构造、写入并读回，正文逐值不变；同一个期写在别的级下不合法。"""
    from worldmock.recording_engine.storage.store import FootprintStore

    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
                                  {"version": 0, "current_time": 100})
    try:
        [response] = store.append_evidence([EventDraft(event_type="model_response", occurred_at=100, agent_id=1,
                                                       source_kind="model", visibility="system", data={})])
        data = _digest_data(tier, period)
        store.append_evidence([_digest(data, caused_by=[response.event_id])])
        [stored] = store.read_agent_events("environment_event", 1)
        assert stored.data == data and stored.caused_by == [response.event_id]
    finally:
        store.close()
    for other in ("day", "month", "year"):
        if other != tier:
            with pytest.raises(ValidationError, match="Memory digest event tier or period is invalid"):
                _digest(_digest_data(other, period))


@pytest.mark.parametrize("tier,period,limit", [("day", "2026-10-09", 250), ("month", "2026-10", 800),
                                               ("year", "2026", 2000)], ids=["day", "month", "year"])
def test_digest_event_text_may_reach_the_tier_limit(tier, period, limit):
    """正文连标点恰为这一级的上限（日 250、月 800、年 2000）时草稿合法，多一个标点就报回顾不合格。"""
    text = "我" * (limit - 1) + "。"
    data = _digest_data(tier, period, digests={"food": {**_ITEM, "text": text}, "work": None, "life": None})
    assert _digest(data).data["digests"]["food"]["text"] == text
    longer = _digest_data(tier, period, digests={"food": {**_ITEM, "text": text + "。"}, "work": None, "life": None})
    with pytest.raises(ValidationError, match="Memory digest event digests are invalid"):
        _digest(longer)


_FIELDS = "Memory digest event fields or source are invalid"
_PERIOD = "Memory digest event tier or period is invalid"
_DIGESTS = "Memory digest event digests are invalid"
_FALLBACK = "Memory digest event fallback is invalid"
_ITEM = {"text": "收到了柠檬茶。", "source_event_ids": ["e1", "e2"], "cited_event_ids": ["e2"]}


@pytest.mark.parametrize("data,changes,message", [
    (_digest_data(extra=1), {}, _FIELDS),
    ({key: value for key, value in _digest_data().items() if key != "fallback"}, {}, _FIELDS),
    (_digest_data(), {"source_kind": "environment"}, _FIELDS),
    (_digest_data(), {"visibility": "agent"}, _FIELDS),
    (_digest_data(), {"resource_changes": {"account:agent:1": -1}}, _FIELDS),
    (_digest_data(), {"caused_by": ["one", "two"]}, _FIELDS),
    (_digest_data("week", "2026-W41"), {}, _PERIOD),
    (_digest_data("day", "2026-1-9"), {}, _PERIOD),
    (_digest_data("month", "2026-13"), {}, _PERIOD),
    (_digest_data("year", "26"), {}, _PERIOD),
    (_digest_data("month", "2026-10\n"), {}, _PERIOD),
    (_digest_data("year", "２０２６"), {}, _PERIOD),
    (_digest_data("year", 2026), {}, _PERIOD),
    (_digest_data(digests={"food": _ITEM, "work": None}), {}, _DIGESTS),
    (_digest_data(digests={"food": {**_ITEM, "extra": 1}, "work": None, "life": None}), {}, _DIGESTS),
    (_digest_data(digests={"food": {**_ITEM, "text": "  "}, "work": None, "life": None}), {}, _DIGESTS),
    (_digest_data(digests={"food": {**_ITEM, "text": "我" * 251}, "work": None, "life": None}), {}, _DIGESTS),
    (_digest_data(digests={"food": {**_ITEM, "source_event_ids": [], "cited_event_ids": []}, "work": None,
                           "life": None}), {}, _DIGESTS),
    (_digest_data(digests={"food": {**_ITEM, "cited_event_ids": ["e3"]}, "work": None, "life": None}), {}, _DIGESTS),
    (_digest_data(digests={"food": None, "work": None, "life": None}, fallback=[]), {}, _DIGESTS),
    (_digest_data(fallback=["life", "life"]), {}, _FALLBACK),
    (_digest_data(digests={"food": None, "work": None, "life": _ITEM}, fallback=["work", "food"]), {}, _FALLBACK),
    (_digest_data(fallback=["food"]), {}, _FALLBACK),
], ids=["extra-key", "missing-key", "environment-source", "agent-visibility", "resource-changes", "two-causes",
        "week-tier", "non-canonical-date", "month-13", "two-digit-year", "month-trailing-newline", "full-width-year",
        "period-not-string", "missing-category", "item-extra-key", "blank-text", "day-text-251", "empty-identities",
        "cited-not-source", "all-null-without-fallback", "repeated-fallback", "fallback-out-of-order",
        "fallback-names-a-written-category"])
def test_digest_event_rejects_invalid_shapes(caplog, data, changes, message):
    """逐一破坏信封、级与期、三类回顾或 fallback：各报对应的那条消息，并先写同文错误日志。"""
    with pytest.raises(ValidationError, match=message):
        _digest(data, **changes)
    assert message in caplog.messages
