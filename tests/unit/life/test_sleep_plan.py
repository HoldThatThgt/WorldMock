"""睡眠请求里分钟数的解析、睡前提示的文字边界、开始睡之前 24 小时的睡眠合计、运行开始前的假定睡眠与睡过头的抽取。"""

import hashlib
import tomllib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.input_models import PersonaConfig
from worldmock.life import sleep_plan
from worldmock.life.body import initial_body
from worldmock.life.body_config import assemble_body_config
from worldmock.life.felt import sleep_debt_phrase
from worldmock.life.randomness import random_stream
from worldmock.life.sleep import initial_sleep_pressure, pressure_after_awake
from worldmock.life.sleep_plan import (
    SLEEP_MINUTES_REASON, assumed_sleep, oversleep_seconds, parse_sleep_minutes, sleep_seconds_in_window,
)

_ZONE = ZoneInfo("Asia/Shanghai")
_HOUR = 3600
#: 种子 7 的运行随机态。
_STATE = hashlib.sha256(b"random:7").hexdigest()


def _at(day: int, clock: str) -> int:
    """2026 年 10 月某日某个本地钟点的绝对时刻（上海时区）。"""
    hour, minute = (int(part) for part in clock.split(":"))
    return int(datetime(2026, 10, day, hour, minute, tzinfo=_ZONE).timestamp())


def _span(day: int, start: str, end: str) -> tuple[int, int]:
    """一段从某日某刻开始、在同一天或次日某刻结束的睡眠。"""
    begun, ended = _at(day, start), _at(day, end)
    return begun, ended if ended > begun else ended + 24 * _HOUR


@pytest.mark.parametrize("written,minutes", [
    ("10", 10), ("720", 720), ("470", 470), ("４７０", 470), (" 4 7 0 ", 470), ("060", 60), ("0010", 10),
])
def test_minutes_written_in_digits_within_range_are_read_exactly(written, minutes):
    """全角数字、夹空白和前导零归一后按十进制读出，不换算也不取整。"""
    assert parse_sleep_minutes(written) == minutes


@pytest.mark.parametrize("written", [
    "9", "721", "0", "", "   ", "7.5", "-60", "+60", "470分钟", "8h", "7小时50分", "4e2", "0x10", "１２小时",
    "1" * 5000,
])
def test_units_signs_decimals_and_out_of_range_numbers_are_rejected_with_one_reason(written):
    """带单位、小数、正负号的写法和越界的数都拒绝，原因是规定的那一句，不截到范围内。"""
    with pytest.raises(ValueError) as caught:
        parse_sleep_minutes(written)
    assert str(caught.value) == SLEEP_MINUTES_REASON
    assert SLEEP_MINUTES_REASON == ("For sleep, target must be a whole number of minutes from 10 to 720 written in "
                                    "digits, for example 470.")


@pytest.mark.parametrize("hours,phrase", [
    (0.0, "现在不欠觉"), (0.24, "现在不欠觉"),
    (0.25, "现在睡，约半小时能补满"), (0.74, "现在睡，约半小时能补满"),
    (0.75, "现在睡，约 1 小时能补满"), (1.24, "现在睡，约 1 小时能补满"),
    (1.25, "现在睡，约 1.5 小时能补满"), (2.5, "现在睡，约 2.5 小时能补满"), (5.45, "现在睡，约 5.5 小时能补满"),
    (8.08, "现在睡，约 8 小时能补满"), (12.0, "现在睡，约 12 小时能补满"),
    (12.01, "现在睡，12 小时以上才能补满"), (24.0, "现在睡，12 小时以上才能补满"),
])
def test_pre_sleep_hint_rounds_the_debt_to_half_hours_at_every_boundary(hours, phrase):
    """睡前提示只给半小时一档的时长：0.25、0.75、1.25 与 12 小时各边界两侧的文字。"""
    assert sleep_debt_phrase(hours) == phrase


@pytest.mark.parametrize("hours", [0.1, 0.6, 3.3333, 7.83, 11.9, 13.7])
def test_pre_sleep_hint_never_shows_the_raw_body_value(hours):
    """文字里没有欠觉的原值，也不出现「欠觉 N 小时」的说法。"""
    phrase = sleep_debt_phrase(hours)
    assert str(hours) not in phrase and "欠觉 " not in phrase and "sleep_deficit" not in phrase


def test_sleeps_wholly_inside_the_window_count_in_full_and_the_sleep_being_started_does_not_count():
    """窗口是这一觉开始的时刻往回 24 小时：整段落在窗口内的睡眠全长计入，多段相加；刚开始的这一觉不在其中，
    此前没有睡眠时合计就是 0。"""
    now = _at(9, "13:00")
    assert sleep_seconds_in_window(now, []) == 0
    assert sleep_seconds_in_window(now, [_span(9, "00:10", "08:00")]) == 28200
    assert sleep_seconds_in_window(now, [_span(9, "00:10", "08:00"), _span(8, "15:00", "15:30")]) == 30000
    assert sleep_seconds_in_window(now, [_span(9, "12:00", "13:00")]) == 3600


def test_a_sleep_across_the_window_start_counts_only_the_part_inside():
    """早于窗口开始入睡的睡眠只计落在窗口内的那一截；恰在窗口起点结束或整段在窗口之前的计 0。"""
    now = _at(9, "01:00")
    assert sleep_seconds_in_window(now, [_span(8, "00:10", "08:00")]) == 7 * _HOUR
    assert sleep_seconds_in_window(now, [_span(7, "23:00", "01:01")]) == 60
    assert sleep_seconds_in_window(now, [_span(7, "23:00", "01:00")]) == 0
    assert sleep_seconds_in_window(now, [_span(7, "00:10", "08:00")]) == 0
    assert sleep_seconds_in_window(now, [_span(8, "00:10", "08:00"), _span(7, "23:00", "00:05")]) == 7 * _HOUR


#: 人物平时 00:10 睡到 08:00；8 日是照常的一夜。
_NORMAL_NIGHT = _span(8, "00:10", "08:00")
#: 裁决的例子逐行：此前的睡眠、这一觉的开始时刻、写的分钟数、开始时刻往回 24 小时的合计秒数、按时醒的时刻、是否可能
#: 睡过头。
_RULING_ROWS = [
    ("normal_night", [_NORMAL_NIGHT], _at(9, "00:10"), 470, 28200, _at(9, "08:00"), False),
    ("office_nap", [_span(9, "00:10", "08:00")], _at(9, "13:00"), 60, 28200, _at(9, "14:00"), False),
    ("short_night_after_a_normal_night", [_NORMAL_NIGHT], _at(9, "01:00"), 180, 25200, _at(9, "04:00"), False),
    ("office_nap_after_the_short_night", [_NORMAL_NIGHT, _span(9, "01:00", "04:00")], _at(9, "13:00"), 60, 10800,
     _at(9, "14:00"), True),
    ("full_night_after_the_short_night", [_NORMAL_NIGHT, _span(9, "01:00", "04:00")], _at(10, "00:10"), 470, 10800,
     _at(10, "08:00"), True),
    ("twelve_hours_after_the_short_night", [_NORMAL_NIGHT, _span(9, "01:00", "04:00")], _at(9, "20:00"), 720, 10800,
     _at(10, "08:00"), True),
    ("all_nighter_until_eight", [_NORMAL_NIGHT], _at(9, "08:00"), 180, 0, _at(9, "11:00"), True),
    ("noon_nap_after_a_three_hour_night", [_NORMAL_NIGHT, _span(9, "03:00", "06:00")], _at(9, "12:00"), 30, 10800,
     _at(9, "12:30"), True),
    ("noon_nap_after_a_four_hour_night", [_NORMAL_NIGHT, _span(9, "02:00", "06:00")], _at(9, "12:00"), 30, 14400,
     _at(9, "12:30"), False),
]


@pytest.mark.parametrize("earlier,now,minutes,total,on_time,may_oversleep",
                         [row[1:] for row in _RULING_ROWS], ids=[row[0] for row in _RULING_ROWS])
def test_ruling_sleep_examples_give_the_stated_totals_and_outcomes(earlier, now, minutes, total, on_time,
                                                                   may_oversleep):
    """裁决的例子逐行：合计只数这一觉开始之前的 24 小时，与写了多久无关。满 4 小时的无论哪次请求都在写的时长到点时醒；
    不足的约一半到点醒、其余再睡 20 到 60 的整数分钟，写满 470 或 720 分钟的一觉也一样。"""
    assert sleep_seconds_in_window(now, earlier) == total
    assert now + minutes * 60 == on_time
    woke = [now + minutes * 60 + oversleep_seconds(total, _STATE, f"action-{index}") for index in range(200)]
    if not may_oversleep:
        assert set(woke) == {on_time}
        return
    late = [moment - on_time for moment in woke if moment != on_time]
    assert 60 < len(late) < 140 and all(1200 <= value <= 3600 and value % 60 == 0 for value in late)
    assert {1200, 3600} <= {oversleep_seconds(total, _STATE, f"edge-{index}") for index in range(4000)}


def test_exactly_four_hours_wakes_on_time_without_touching_the_random_stream(monkeypatch):
    """合计恰好 14400 秒不取随机流，14399 秒才取：此前只睡了 02:00 到 06:00 的人中午再睡按时醒；照常睡过一夜的人在
    04:00 开始睡时窗口里恰好还剩 4 小时，晚一分钟就不足。"""
    taken = []

    def recording(random_state: str, key: str):
        """记下取过的随机键，再交给真实的随机流。"""
        taken.append(key)
        return random_stream(random_state, key)

    monkeypatch.setattr(sleep_plan, "random_stream", recording)
    assert sleep_seconds_in_window(_at(9, "12:00"), [_span(9, "02:00", "06:00")]) == 14400
    assert sleep_seconds_in_window(_at(9, "04:00"), [_NORMAL_NIGHT]) == 14400
    assert sleep_seconds_in_window(_at(9, "04:01"), [_NORMAL_NIGHT]) == 14340
    assert oversleep_seconds(14400, _STATE, "action-a") == 0 and oversleep_seconds(86400, _STATE, "action-a") == 0
    assert taken == []
    oversleep_seconds(14399, _STATE, "action-a")
    assert taken == ["oversleep:action-a"]
    oversleep_seconds(14340, _STATE, "action-b")
    assert taken == ["oversleep:action-a", "oversleep:action-b"]


def test_a_sleep_declared_at_twelve_hours_can_run_over_and_never_passes_thirteen():
    """12 小时只限制人物写的数：写满 720 分钟的一觉在此前睡得不足 4 小时时照样可能多睡，加上多睡的那段最长 780 分钟，
    两端都能取到；此前睡满 4 小时时写多长都恰好睡到写的时长。"""
    declared = sleep_plan.SLEEP_MINUTES_MAX * 60
    short = sleep_seconds_in_window(_at(9, "20:00"), [_span(9, "01:00", "04:00")])
    lengths = {declared + oversleep_seconds(short, _STATE, f"long-{index}") for index in range(4000)}
    assert min(lengths) == 720 * 60 and max(lengths) == 780 * 60
    assert {740 * 60, 780 * 60} <= lengths and all(value % 60 == 0 for value in lengths)
    rested = sleep_seconds_in_window(_at(9, "20:00"), [_span(9, "00:10", "08:00")])
    assert {declared + oversleep_seconds(rested, _STATE, f"long-{index}") for index in range(4000)} == {declared}


def test_oversleep_draw_is_reproducible_and_keyed_on_the_run_seed_and_the_action():
    """同一随机态和动作身份重复计算结果相同；换动作身份或换种子是另一条流；规格给出的种子 7 例子逐值相符。"""
    action = "run-a:round:1791356400:42:agent:1:order:0:action:0001"
    assert [oversleep_seconds(10800, _STATE, action) for _ in range(3)] == [1620, 1620, 1620]
    stream = random_stream(_STATE, f"oversleep:{action}")
    assert (stream.getrandbits(1), stream.randint(20, 60)) == (1, 27)
    assert oversleep_seconds(0, _STATE, action) == oversleep_seconds(14399, _STATE, action) == 1620
    others = {oversleep_seconds(10800, _STATE, f"{action}-{index}") for index in range(40)}
    assert len(others) > 5 and 0 in others
    other_seed = hashlib.sha256(b"random:8").hexdigest()
    assert [oversleep_seconds(10800, _STATE, f"a-{index}") for index in range(40)] != [
        oversleep_seconds(10800, other_seed, f"a-{index}") for index in range(40)]


def test_assumed_sleep_before_the_run_ends_at_the_habitual_wake_time_used_for_the_initial_body():
    """运行开始前的假定睡眠：在惯常起床时刻结束、此前睡满每日睡眠需要；这个起床时刻就是身体初值用的那一个。"""
    start = _at(5, "08:00")
    assert assumed_sleep(start, 8.0, 4.0, 8.0) == (_at(5, "00:00"), _at(5, "08:00"))
    assert assumed_sleep(start, 8.0, 4.0, 7.5) == (_at(5, "00:15"), _at(5, "07:45"))
    assert assumed_sleep(_at(5, "00:00"), 0.0, 4.0, 7.5) == (_at(4, "00:15"), _at(4, "07:45"))
    raw = tomllib.loads((ENVIRONMENT_ROOT / "persona.toml").read_text(encoding="utf-8"))
    for midsleep, need, clock in ((4.0, 7.5, "08:00"), (3.25, 8.25, "06:30"), (5.0, 7.0, "23:00")):
        persona = PersonaConfig.model_validate({**raw, "chronotype_midsleep": midsleep, "sleep_need_hours": need})
        hour = int(clock[:2]) + int(clock[3:]) / 60
        began, ended = assumed_sleep(_at(5, clock), hour, midsleep, need)
        assert ended - began == round(need * 3600) and 0 <= _at(5, clock) - ended < 86400
        at_wake = initial_sleep_pressure((midsleep + need / 2) % 24, need, midsleep)
        body = initial_body(assemble_body_config(persona), hour, persona)
        assert body.sleep_pressure == pytest.approx(pressure_after_awake(at_wake, (_at(5, clock) - ended) / 3600))


def test_the_first_day_nap_counts_the_assumed_sleep_and_wakes_on_time():
    """睡眠中点 4.0、每日睡眠需要 8 小时、周一 08:00 开始：13:00 午睡时往回 24 小时里有运行开始前的假定睡眠 8 小时，
    按时醒；不计假定睡眠时这次午睡之前一秒都没睡过。假定睡眠只有这一段：窗口伸到运行开始之前时只计它落在窗口内的
    那一截，第二天 04:00 起不足 4 小时，08:00 以后的窗口里不再有它。"""
    assumed = assumed_sleep(_at(5, "08:00"), 8.0, 4.0, 8.0)
    total = sleep_seconds_in_window(_at(5, "13:00"), [assumed])
    assert total == 8 * _HOUR
    assert {oversleep_seconds(total, _STATE, f"nap-{index}") for index in range(50)} == {0}
    assert sleep_seconds_in_window(_at(5, "13:00"), []) == 0
    assert sleep_seconds_in_window(_at(5, "08:00"), [assumed]) == 8 * _HOUR
    assert sleep_seconds_in_window(_at(6, "03:00"), [assumed]) == 5 * _HOUR
    assert sleep_seconds_in_window(_at(6, "04:00"), [assumed]) == 4 * _HOUR
    assert sleep_seconds_in_window(_at(6, "04:30"), [assumed]) == 12600
    assert sleep_seconds_in_window(_at(6, "08:00"), [assumed]) == sleep_seconds_in_window(_at(6, "09:00"), [assumed]) == 0
