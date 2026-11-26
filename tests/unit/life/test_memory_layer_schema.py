"""记忆层的机器形状：Schema 接受条目、可选折叠摘要、近三天格子与空记忆层，拒绝未知键与已退场计数行。"""

import copy

import pytest

from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.simulation_engine.observation.memory.layout import assemble_layer

#: 两条今天条目与一个近三天格子组成的记忆层。
LAYER = assemble_layer("2026-10-08", {"饮食": [{"日期": "2026-10-07", "星期": "星期三", "回顾": "中午吃了牛肉面。"}]}, [
    {"时刻": "08:00", "类别": "生活", "核验": ["开始微信"]},
    {"时刻": "08:50", "类别": "工作、饮食", "核验": ["送达美式咖啡"], "自述": ["进展·处理消息与收尾：快收完了。"]},
])


def test_memory_layer_schema_accepts_entries_count_lines_and_the_empty_layer():
    """Schema 接受空记忆层，也接受带今天条目、近三天格子与可选折叠摘要的记忆层。"""
    validate_layer(assemble_layer(None, {}, []))
    validate_layer(LAYER)
    compacted = {
        **LAYER,
        "折叠摘要": {
            "folded_through_seq": 5, "folded_until_clock": "09:00",
            "completed_activities": ["08:30 晨练结束"], "places_visited": ["家"],
            "purchases": [], "body_and_balance_notes": ["当前体感：不饿"], "open_commitments": [],
        },
    }
    validate_layer(compacted)


def _without(key):
    """去掉一个顶层键的记忆层。"""
    layer = copy.deepcopy(LAYER)
    del layer[key]
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
    {**LAYER, "今天": [*LAYER["今天"], {"时刻": "09:00–10:00", "类别": "工作", "条数": 3}]},
    _changed(("今天", 0, "类别"), "饮食、饮食"),
    {"layer": "记忆层", "earlier_days": [], "earlier_today": []},
], ids=["extra-key", "no-today", "no-available", "available-extra-key", "earliest-not-a-date", "rest-category",
        "retired-count-line", "repeated-food-entry", "old-shape"])
def test_memory_layer_schema_refuses_unknown_keys_and_malformed_count_lines(layer):
    """Schema 拒绝多余或缺失的顶层键、坏的可查、近三天的休息键、已退场计数行、重复类别与旧形状。"""
    with pytest.raises(ValueError):
        validate_layer(layer)
