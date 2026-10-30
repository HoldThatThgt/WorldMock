"""家里做的饭叫得出名字：凭食材包状态的活动所用的食材包只从已提交的本人 callback_effect 推出，后收到的顶掉先收到的，
运行开始以来没收到过时是家里原有的食材；三种写法里的「M月D日」不补零（docs/design/04-simulation-engine.md
「出行、内容与查询」）。

都是纯函数：事件用 SimpleNamespace(event_id, event_seq, occurred_at, activity_id, caused_by, data) 构造。
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.time.readable import clock_phrase, month_day_phrase
from worldmock.simulation_engine.observation.wording.settled_text import (
    EMPTY_KIT_TRACK,
    KitTrack,
    KitUse,
    kit_phrase,
    track_kits,
)

ZONE = "Asia/Shanghai"
GROCERIES = "groceries"
#: 只认 groceries 为食材包状态。
STATES = frozenset({GROCERIES})


def _epoch(*parts: int) -> int:
    """场景时区本地时刻的 epoch 秒。"""
    return int(datetime(*parts, tzinfo=ZoneInfo(ZONE)).timestamp())


def _event(seq: int, data: dict, *, activity_id: str | None = None, caused_by: tuple[str, ...] = (),
           at: int | None = None) -> SimpleNamespace:
    """一条手写的本人事件：身份按序号命名，发生时刻缺省为序号。"""
    return SimpleNamespace(event_id=f"event-{seq}", event_seq=seq, occurred_at=seq if at is None else at,
                           activity_id=activity_id, caused_by=list(caused_by), data=data)


def _reset(seq: int, goods_id: str, state: str = GROCERIES, at: int | None = None) -> SimpleNamespace:
    """收货把状态置为上限的 reset_to_cap。"""
    return _event(seq, {"kind": "callback_effect", "type": "reset_to_cap", "rule_id": None, "state": state,
                        "goods_id": goods_id, "before": 0, "after": 1}, at=at)


def _consume(seq: int, start: SimpleNamespace, state: str = GROCERIES) -> SimpleNamespace:
    """凭状态的内容开始时减 1 的 consume_on_start，唯一起因是这次开始的受理结果 start。"""
    return _event(seq, {"kind": "callback_effect", "type": "consume_on_start", "rule_id": None, "state": state,
                        "content_id": "cook", "before": 1, "after": 0}, caused_by=(start.event_id,))


def _start(seq: int, activity_id: str) -> SimpleNamespace:
    """开始一项活动的受理 action_result，信封带活动身份。"""
    return _event(seq, {"status": "in_progress"}, activity_id=activity_id)


def test_a_start_without_an_earlier_reset_uses_the_food_already_at_home():
    """只有一条 consume_on_start、此前同一状态从未被商品重置：这项活动用的是家里原有的食材（商品与时刻都为空）。"""
    start = _start(2, "cook-1")
    track = track_kits([_consume(3, start)], [start], EMPTY_KIT_TRACK, STATES)
    assert track.uses == {"cook-1": KitUse(GROCERIES, None, None)}
    assert track.latest == {}


def test_the_latest_reset_before_the_start_names_the_kit():
    """重置 A、重置 B、再开始：用的是后收到的 B 与 B 的收到时刻；开始之后的重置 C 不影响这次开始，只成为最近一次重置。"""
    first, second = _reset(1, "kit-a", at=100), _reset(2, "kit-b", at=200)
    start = _start(3, "cook-1")
    later = _reset(5, "kit-c", at=500)
    track = track_kits([first, second, _consume(4, start), later], [start], EMPTY_KIT_TRACK, STATES)
    assert track.uses == {"cook-1": KitUse(GROCERIES, "kit-b", 200)}
    assert track.latest == {GROCERIES: KitUse(GROCERIES, "kit-c", 500)}


def test_effects_of_other_states_are_ignored():
    """状态不在 states 里的重置与减 1 不产生记录；kind 不是 callback_effect 的事件跳过；其他 type 的回调效果也跳过。"""
    start = _start(3, "dinner-1")
    other = [_reset(1, "beer", state="team_dinner"), _consume(4, start, state="team_dinner")]
    unrelated = [_event(2, {"kind": "goods_received", "type": "reset_to_cap", "state": GROCERIES, "goods_id": "x"}),
                 _event(5, {"kind": "callback_effect", "type": "state_delta", "rule_id": "r", "state": GROCERIES,
                            "delta": 1, "before": 0, "after": 1})]
    track = track_kits([*other, *unrelated], [start], EMPTY_KIT_TRACK, STATES)
    assert track == KitTrack({}, {})


def test_a_use_whose_start_result_is_missing_is_an_integrity_error(caplog):
    """consume_on_start 的起因不在同一批 action_result 里：先记带这条事件身份的错误日志，再抛 IntegrityError。"""
    elsewhere = _start(2, "cook-1")
    consumed = _consume(3, elsewhere)
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError) as raised:
        track_kits([consumed], [], EMPTY_KIT_TRACK, STATES)
    assert str(raised.value) == "Grocery kit use has no start result"
    assert f"Grocery kit use has no start result: {consumed.event_id}" in caplog.messages


def test_incremental_tracking_equals_one_pass():
    """同一串事件按提交边界分两批 track_kits（第二批以第一批的结果为 previous）与一次读完全部事件的结果相同。"""
    first_start, second_start = _start(2, "cook-1"), _start(6, "cook-2")
    effects = [_reset(1, "kit-a", at=10), _consume(3, first_start), _reset(4, "kit-b", at=40),
               _reset(5, "kit-c", at=50), _consume(7, second_start)]
    results = [first_start, second_start]
    whole = track_kits(effects, results, EMPTY_KIT_TRACK, STATES)
    early = track_kits(effects[:2], results[:1], EMPTY_KIT_TRACK, STATES)
    batched = track_kits(effects[2:], results[1:], early, STATES)
    assert batched == whole
    assert whole.uses == {"cook-1": KitUse(GROCERIES, "kit-a", 10), "cook-2": KitUse(GROCERIES, "kit-c", 50)}


def test_kit_phrase_has_full_and_short_forms(frozen_inputs):
    """有商品时完整式「用的是1月5日收到的扑克牌」、短式「用1月5日收到的扑克牌」；没有商品时「用的是家里原有的食材」与
    「用家里原有的食材」；短语不带前导逗号与句末标点。"""
    catalog = frozen_inputs.record(ENVIRONMENT_ROOT)["inputs"].catalog
    kit = KitUse(GROCERIES, "playing-cards", _epoch(2026, 1, 5, 9, 30))
    home = KitUse(GROCERIES, None, None)
    assert kit_phrase(kit, catalog, ZONE, False) == "用的是1月5日收到的扑克牌"
    assert kit_phrase(kit, catalog, ZONE, True) == "用1月5日收到的扑克牌"
    assert kit_phrase(home, catalog, ZONE, False) == "用的是家里原有的食材"
    assert kit_phrase(home, catalog, ZONE, True) == "用家里原有的食材"


def test_month_day_phrase_does_not_pad():
    """month_day_phrase 写给定时区的本地日期「M月D日」，月与日都不补零；clock_phrase 跨日时仍是「1月5日 08:00」，同日时
    仍是「08:00」。"""
    assert month_day_phrase(_epoch(2026, 10, 10, 20, 26), ZONE) == "10月10日"
    assert month_day_phrase(_epoch(2026, 1, 5, 0, 0), ZONE) == "1月5日"
    after_midnight = _epoch(2026, 1, 6, 1, 0)
    assert (month_day_phrase(after_midnight, ZONE), month_day_phrase(after_midnight, "UTC")) == ("1月6日", "1月5日")
    moment = _epoch(2026, 1, 5, 8, 0)
    assert clock_phrase(moment, _epoch(2026, 1, 6, 9, 0), ZONE) == "1月5日 08:00"
    assert clock_phrase(moment, _epoch(2026, 1, 5, 23, 0), ZONE) == "08:00"
