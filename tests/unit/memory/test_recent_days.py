"""「近三天」：饮食和生活取日历上的前三天，工作取最近三个有工作记录的日子；格子的两种形状、键序与 Schema。

日回顾直接构造成 DayDigest，不经存储。2026-09-30 是星期三，2026-10-03 是星期六，2026-10-05 是星期一，今天是 2026-10-08。
"""

import json
from types import SimpleNamespace

import pytest

from worldmock.agents.prompt.prompt_schemas import MEMORY_LAYER_SCHEMA, validate_layer
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.observation.memory.days import DayDigest, recent_block
from worldmock.simulation_engine.observation.memory.layout import assemble_layer
from worldmock.state.model.memory import MEMORY_CATEGORIES, MEMORY_DIGEST_TEXT_LIMITS

TODAY = "2026-10-08"
#: 周一到周五当班的工作安排（键是 date.weekday()）。
SHIFT = {"starts": 540, "ends": 1080, "breaks": []}
SCHEDULE = SimpleNamespace(weekly={str(day): SHIFT if day < 5 else None for day in range(7)}, exceptions={})


def _days(days, **texts) -> dict[str, DayDigest]:
    """这几天各类都写同一种正文的日回顾：texts 是类别 → 正文。"""
    return {day: DayDigest({category: f"{day}{text}" for category, text in texts.items()}, frozenset()) for day in days}


def _dates(cells) -> list[str]:
    """格子的日期。"""
    return [cell["日期"] for cell in cells]


def test_food_and_life_take_the_three_calendar_days_before_today():
    digests = _days(("2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"), 饮食="吃了面。", 生活="散步了。")
    block = recent_block(digests, TODAY, MEMORY_CATEGORIES, SCHEDULE)
    assert _dates(block["饮食"]) == _dates(block["生活"]) == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert block["饮食"][0]["回顾"] == "2026-10-05吃了面。" and block["工作"] == []


def test_work_takes_the_last_three_days_with_work_records_across_a_holiday():
    """假前三天有工作回顾，10-01 到 10-07 工作没有记录：工作格是假前三天，饮食与生活只取日历上的前三天。"""
    digests = {**_days(("2026-09-28", "2026-09-29", "2026-09-30"), 饮食="食堂。", 工作="评审。", 生活="回家。"),
               **_days([f"2026-10-0{day}" for day in range(1, 8)], 饮食="在家吃。", 生活="放假。")}
    block = recent_block(digests, TODAY, MEMORY_CATEGORIES, SCHEDULE)
    assert _dates(block["工作"]) == ["2026-09-28", "2026-09-29", "2026-09-30"]
    assert _dates(block["饮食"]) == _dates(block["生活"]) == ["2026-10-05", "2026-10-06", "2026-10-07"]


def test_an_unsettled_work_day_counts_as_a_work_record_day():
    digests = {"2026-09-29": DayDigest({"工作": "评审。"}, frozenset()),
               "2026-09-30": DayDigest({}, frozenset({"工作"}))}
    block = recent_block(digests, TODAY, MEMORY_CATEGORIES, SCHEDULE)
    assert block["工作"][-1] == {"日期": "2026-09-30", "星期": "星期三", "安排": "工作日", "回顾": None, "未整理": True}
    assert _dates(block["工作"]) == ["2026-09-29", "2026-09-30"]


def test_an_omitted_cell_and_an_unsettled_cell_differ():
    """同一天饮食没有记录、生活未整理：饮食列表里没有这一天，生活列表里是带「未整理」的格子。"""
    block = recent_block({"2026-10-07": DayDigest({}, frozenset({"生活"}))}, TODAY, MEMORY_CATEGORIES, SCHEDULE)
    assert json.dumps(block, ensure_ascii=False, separators=(",", ":")) == (
        '{"饮食":[],"工作":[],"生活":[{"日期":"2026-10-07","星期":"星期三","回顾":null,"未整理":true}]}')


def test_cells_have_the_fixed_keys_in_order():
    """饮食格：日期、星期、回顾；工作格：日期、星期、安排、回顾；未整理格末尾多「未整理」。周六补班日的「安排」是工作日，
    周一假日是休息日；没有工作安排时工作格的「安排」是休息日。"""
    schedule = SimpleNamespace(weekly=SCHEDULE.weekly, exceptions={"2026-10-03": SHIFT, "2026-10-05": None})
    digests = {"2026-10-03": DayDigest({"工作": "补班。"}, frozenset()),
               "2026-10-05": DayDigest({"饮食": "吃面。"}, frozenset({"工作"}))}
    block = recent_block(digests, "2026-10-06", MEMORY_CATEGORIES, schedule)
    assert list(block["饮食"][0]) == ["日期", "星期", "回顾"]
    assert [list(cell) for cell in block["工作"]] == [["日期", "星期", "安排", "回顾"],
                                                    ["日期", "星期", "安排", "回顾", "未整理"]]
    assert [cell["安排"] for cell in block["工作"]] == ["工作日", "休息日"]
    assert [cell["安排"] for cell in recent_block(digests, "2026-10-06", MEMORY_CATEGORIES, None)["工作"]] == [
        "休息日", "休息日"]


def test_the_work_key_is_absent_when_work_is_disabled():
    digests = _days(("2026-10-07",), 饮食="吃面。", 生活="散步。")
    assert list(recent_block(digests, TODAY, ("饮食", "生活"), None)) == ["饮食", "生活"]


def test_the_first_day_has_empty_lists():
    assert recent_block({}, TODAY, MEMORY_CATEGORIES, SCHEDULE) == {"饮食": [], "工作": [], "生活": []}


def test_a_day_digest_cannot_be_written_and_unsettled(caplog):
    with pytest.raises(IntegrityError, match="^Day digest marks a category both written and unsettled$"):
        DayDigest({"饮食": "…"}, frozenset({"饮食"}))
    assert "Day digest marks a category both written and unsettled" in caplog.text


FOOD = {"日期": "2026-09-30", "星期": "星期三", "回顾": "吃了面。"}
WORK = {"日期": "2026-09-30", "星期": "星期三", "安排": "工作日", "回顾": "评审。"}
UNSETTLED = {"日期": "2026-09-30", "星期": "星期三", "安排": "工作日", "回顾": None, "未整理": True}


@pytest.mark.parametrize("recent,valid", [
    ({"饮食": [FOOD], "工作": [WORK, UNSETTLED], "生活": [{**FOOD, "回顾": None, "未整理": True}]}, True),
    ({"饮食": [{**FOOD, "安排": "工作日"}]}, False),
    ({"工作": [{key: value for key, value in WORK.items() if key != "安排"}]}, False),
    ({"饮食": [{**FOOD, "回顾": "饭" * 250}]}, True),
    ({"饮食": [{**FOOD, "回顾": "饭" * 251}]}, False),
    ({"饮食": [{**FOOD, "回顾": None}]}, False),
    ({"工作": [{**UNSETTLED, "未整理": False}]}, False),
    ({"饮食": [FOOD] * 4}, False),
], ids=["two-shapes", "food-with-arrangement", "work-without-arrangement", "250-characters", "251-characters",
        "null-without-flag", "flag-false", "four-cells"])
def test_recent_cell_schema_accepts_only_the_two_cell_shapes(recent, valid):
    """整个记忆层经 validate_layer 校验：有回顾（至多 250 字）与未整理两种格子通过，其余形状（含 251 字的回顾）都不合格。"""
    layer = assemble_layer("2026-10-01", recent, [])
    if valid:
        validate_layer(layer)
    else:
        with pytest.raises(ValueError):
            validate_layer(layer)


def test_recent_cell_text_limit_is_the_day_digest_limit():
    """三类格子「回顾」的 maxLength 都取日回顾的字数上限常量，与第 04 章同值，现在是 250。"""
    recent = MEMORY_LAYER_SCHEMA["properties"]["近三天"]["properties"]
    limits = {name: recent[name]["items"]["oneOf"][0]["properties"]["回顾"]["maxLength"] for name in MEMORY_CATEGORIES}
    assert limits == {name: MEMORY_DIGEST_TEXT_LIMITS["day"] for name in MEMORY_CATEGORIES}
    assert MEMORY_DIGEST_TEXT_LIMITS["day"] == 250
