"""环境层 Schema 里上班安排的说明 note 与明天 tomorrow（docs/design/06-agent.md#agent-环境层-duty ）：都是封闭对象里的必填键。"""

from __future__ import annotations

import copy

import pytest

from worldmock.agents.prompt.prompt_schemas import BODY_LEVELS, validate_layer


def _layer() -> dict:
    """一份合格的环境层：工作日带 null 说明，明天是星期二上班。"""
    return {"layer": "环境层", "observed_at": "2026-01-05T08:00:00+08:00",
            "body": {key: levels[0] for key, levels in BODY_LEVELS.items()},
            "location": {"session_kind": "stay", "name": "家"}, "weather": "晴",
            "duty": {"day": "工作日", "hours": "09:00–18:00", "place": "公司", "breaks": ["12:00–13:00"],
                     "note": None, "status": "未到上班时间"},
            "tomorrow": {"date": "2026-01-06", "weekday": "星期二", "day": "工作日"}}


def _without_note(layer):
    """duty 缺 note。"""
    del layer["duty"]["note"]


def _empty_note(layer):
    """note 是空串。"""
    layer["duty"]["note"] = ""


def _without_tomorrow(layer):
    """环境层缺 tomorrow。"""
    del layer["tomorrow"]


def _extra_tomorrow_key(layer):
    """tomorrow 多一个键。"""
    layer["tomorrow"]["hours"] = "09:00–18:00"


def _without_day(layer):
    """tomorrow 缺 day。"""
    del layer["tomorrow"]["day"]


def _short_weekday(layer):
    """weekday 写「周一」。"""
    layer["tomorrow"]["weekday"] = "周一"


def _date_with_time(layer):
    """date 带时刻。"""
    layer["tomorrow"]["date"] = "2026-01-06T00:00:00+08:00"


def _unknown_day(layer):
    """day 写「调休」。"""
    layer["tomorrow"]["day"] = "调休"


def _note_text(layer):
    """note 是一句说明。"""
    layer["duty"]["note"] = "平常星期一休息，今天按工作安排上班"


def _rest_day_with_note(layer):
    """休息日带说明，明天 day 为 null。"""
    layer["duty"] = {"day": "休息日", "hours": None, "place": None, "breaks": [],
                     "note": "平常星期一上班，今天按工作安排休息", "status": None}
    layer["tomorrow"]["day"] = None


def _no_duty(layer):
    """没有工作安排：duty 为 null，明天 day 为 null。"""
    layer["duty"] = None
    layer["tomorrow"]["day"] = None


@pytest.mark.parametrize("change,accepted", [
    (_without_note, False), (_empty_note, False), (_without_tomorrow, False), (_extra_tomorrow_key, False),
    (_without_day, False), (_short_weekday, False), (_date_with_time, False), (_unknown_day, False),
    (None, True), (_note_text, True), (_rest_day_with_note, True), (_no_duty, True),
], ids=["without-note", "empty-note", "without-tomorrow", "extra-tomorrow-key", "without-day", "short-weekday",
        "date-with-time", "unknown-day", "null-note", "note-text", "rest-day-with-note", "no-duty"])
def test_duty_note_and_tomorrow_are_required_and_closed(change, accepted):
    """note 与 tomorrow 是封闭 Schema 里的必填键：缺 note、note 为空串、缺 tomorrow、tomorrow 多一个键、缺 day、
    weekday 不是七个星期名之一、date 带时刻、day 不是工作日或休息日，各自被拒绝；note 为文字或 null、day 为 null、
    duty 为 null 都通过。"""
    layer = copy.deepcopy(_layer())
    if change is not None:
        change(layer)
    if accepted:
        validate_layer(layer)
    else:
        with pytest.raises(ValueError, match="^Prompt layer 环境层 violates its schema"):
            validate_layer(layer)
