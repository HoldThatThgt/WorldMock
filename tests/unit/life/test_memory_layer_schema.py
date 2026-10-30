"""记忆层的机器形状：Schema 接受条目、计数行、近三天格子与只读空记忆层，拒绝未知键、坏计数行与旧形状。"""

import copy

import pytest

from worldmock.agents.prompt.life_prompt import empty_memory_layer
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.simulation_engine.observation.memory.layout import assemble_layer

#: 两条今天条目、一行计数行与一个近三天格子组成的记忆层。
LAYER = assemble_layer("2026-10-08", {"饮食": [{"日期": "2026-10-07", "星期": "星期三", "回顾": "中午吃了牛肉面。"}]}, [
    {"时刻": "08:00", "类别": "生活", "核验": ["开始微信"]},
    {"时刻": "08:50", "类别": "工作、饮食", "核验": ["送达美式咖啡"], "自述": ["进展·处理消息与收尾：快收完了。"]},
    {"时刻": "09:00–10:00", "类别": "工作", "条数": 3},
])


def test_memory_layer_schema_accepts_entries_count_lines_and_the_empty_layer():
    """Schema 接受只读空记忆层，也接受带两条条目、一行计数行和一个近三天格子的记忆层。"""
    validate_layer(empty_memory_layer())
    validate_layer(LAYER)


def _count_line(**changes):
    """把第三项计数行改几个键后的记忆层。"""
    layer = copy.deepcopy(LAYER)
    layer["今天"][2].update(changes)
    return layer


def _without(key):
    """去掉一个顶层键的记忆层。"""
    layer = copy.deepcopy(LAYER)
    del layer[key]
    return layer


def _count_line_without(key):
    """第三项计数行去掉一个键后的记忆层。"""
    layer = copy.deepcopy(LAYER)
    del layer["今天"][2][key]
    return layer


def _changed(path, value):
    """把一处的值换掉后的记忆层。"""
    layer = copy.deepcopy(LAYER)
    target = layer
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return layer


@pytest.mark.parametrize("layer", [
    {**LAYER, "extra": 1}, _without("今天"), _without("可查"),
    _changed(("可查",), {"最早日期": "2026-10-08", "最晚日期": "2026-10-09"}), _changed(("可查", "最早日期"), "10月8日"),
    _changed(("近三天",), {"休息": []}),
    _count_line_without("条数"),
    _count_line(条数=0), _count_line(时刻="07:30–08:00"), _count_line(时刻="07:00–09:00"),
    _count_line(时刻="07:00-08:00"), _count_line(类别="休息"), _count_line(类别="工作、工作"),
    _changed(("今天", 0, "类别"), "饮食、饮食"), _count_line(核验=["开始微信"]),
    {"layer": "记忆层", "earlier_days": [], "earlier_today": []},
], ids=["extra-key", "no-today", "no-available", "available-extra-key", "earliest-not-a-date", "rest-category",
        "count-line-without-count", "zero-count", "half-hour-span", "two-hour-span", "ascii-hyphen", "rest-label",
        "repeated-work", "repeated-food-entry", "count-line-with-verified", "old-shape"])
def test_memory_layer_schema_refuses_unknown_keys_and_malformed_count_lines(layer):
    """Schema 拒绝多余或缺失的顶层键、坏的可查、近三天的休息键、坏计数行、重复类别、带核验的计数行与旧形状。"""
    with pytest.raises(ValueError):
        validate_layer(layer)
