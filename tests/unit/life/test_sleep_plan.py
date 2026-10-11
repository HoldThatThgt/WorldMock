"""睡眠请求里分钟数的解析与睡前提示的文字边界。"""

import pytest

from worldmock.life.felt import sleep_debt_phrase
from worldmock.life.sleep_plan import SLEEP_MINUTES_REASON, parse_sleep_minutes


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
