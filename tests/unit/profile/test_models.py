"""@brief 人物卡片由人设表直接构造：逐字段照搬、数值原样、形状固定。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests.life.persona_inputs import persona_document, persona_with
from tests.profile.conftest import make_card
from worldmock.configuration.persona.persona_card import (
    PersonaCard,
    PersonaCardStruct,
    TimeAnchors,
    TimeWindow,
    persona_card,
    persona_card_struct,
)

#: 卡片时间锚点 → 人设表里的时段字段。
ANCHOR_FIELDS = {"wake": "wake_window", "leave_home": "leave_home_window", "lunch": "lunch_window",
                 "dinner": "dinner_window", "bedtime": "bedtime_window", "leave_work": "leave_work_window",
                 "arrive_home": "arrive_home_window"}


def _spans(card: PersonaCard) -> dict:
    """卡片七个锚点的（起, 止）小时，null 锚点为 None。"""
    return {name: None if window is None else (window.start_hour, window.end_hour)
            for name, window in ((name, getattr(card.time_anchors, name)) for name in ANCHOR_FIELDS)}


def test_card_is_the_persona_table_field_by_field():
    """卡片的每个字段都是人设表里写的那个值：身份、处境、关系、口味、应对方式照搬，时段成为锚点，习惯的时段成为时段对象。"""
    document = persona_document()
    card = persona_card(persona_with())
    dumped = card.model_dump(mode="json")
    assert set(PersonaCard.model_fields) == {
        "identity", "relationships", "situation", "time_anchors", "chronotype_midsleep",
        "sleep_need_hours", "habits", "tastes", "coping"}
    for name in ("identity", "situation", "relationships", "tastes", "coping", "chronotype_midsleep",
                 "sleep_need_hours"):
        assert dumped[name] == document[name]
    assert _spans(card) == {
        "wake": (7.0, 7.5), "leave_home": (8.0, 8.5), "lunch": (12.0, 13.0), "dinner": (18.5, 19.5),
        "bedtime": (23.0, 24.0), "leave_work": None, "arrive_home": None}
    assert dumped["habits"] == [
        {"trigger": row["trigger"], "response": row["response"],
         "time_window": {"start_hour": row["time_window"][0], "end_hour": row["time_window"][1]}}
        for row in document["habits"]]


def test_card_construction_is_deterministic_and_reads_nothing_but_the_table():
    """同一份人设表构造出相等的卡片；background_story、健康习惯和社交风格不进卡片。"""
    first, second = persona_card(persona_with()), persona_card(persona_with())
    assert first == second and persona_card_struct(first) == persona_card_struct(second)
    retold = persona_with(background_story="换了一个完全不同的故事。", health_habits="每天游泳", social_style="话少")
    assert persona_card(retold) == first


@pytest.mark.parametrize("declared", [
    {"bedtime_window": (5.0, 7.0)},
    {"bedtime_window": (17.0, 18.0), "dinner_window": (19.0, 20.0)},
    {"bedtime_window": (19.5, 20.5), "dinner_window": (19.0, 20.0)},
    {"bedtime_window": (0.5, 1.5)},
    {"leave_work_window": (25.0, 25.5), "arrive_home_window": (24.0, 24.5)},
    {"lunch_window": (18.0, 19.0), "dinner_window": (12.0, 13.0)},
    {"wake_window": (10.0, 10.5), "leave_home_window": (8.0, 8.5)},
    {"lunch_window": (12.1, 12.2), "dinner_window": (6.0, 20.5)},
])
def test_windows_enter_the_card_exactly_as_written(declared):
    """时段不取整、不平移、不排序：晚于次日清晨的就寝、早于或压着晚饭的就寝、午夜后的下班、倒挂的午饭晚饭、
    跨十几个小时的时段，卡片里存的都是人设表写的那两个数。"""
    card = persona_card(persona_with(**declared))
    spans = _spans(card)
    for field, value in declared.items():
        anchor = next(name for name, source in ANCHOR_FIELDS.items() if source == field)
        assert spans[anchor] == value


def test_work_windows_are_null_only_when_the_table_leaves_them_out():
    """下班、到家时段没有写时锚点是 null，写了就照搬；与人物有没有工作安排无关。"""
    without_job = persona_with(work_schedule=None, leave_work_window=(18.0, 18.5))
    assert _spans(persona_card(without_job))["leave_work"] == (18.0, 18.5)
    assert _spans(persona_card(without_job))["arrive_home"] is None
    with_job = persona_with()
    assert with_job.work_schedule is not None
    assert _spans(persona_card(with_job))["leave_work"] is None


def test_empty_relationships_and_habits_give_empty_card_lists():
    """关系和习惯可以是空数组，卡片里就是空的。"""
    card = persona_card(persona_with(relationships=[], habits=[]))
    assert card.relationships == () and card.habits == ()


def test_card_struct_drops_the_situation_and_round_trips_through_json():
    """结构化卡片去掉当下处境，经 JSON 往返后逐值相等，时段是 {start_hour, end_hour} 对象。"""
    card = make_card()
    struct = persona_card_struct(card)
    assert "situation" not in struct and set(struct) == set(PersonaCard.model_fields) - {"situation"}
    assert struct["time_anchors"]["lunch"] == {"start_hour": 12.0, "end_hour": 13.0}
    assert struct["time_anchors"]["leave_work"] is None
    restored = PersonaCardStruct.model_validate(json.loads(json.dumps(struct, ensure_ascii=False)))
    assert persona_card_struct(restored) == struct
    assert PersonaCard.model_validate_json(card.model_dump_json()) == card


def test_card_shape_requires_every_anchor_key_and_rejects_unknown_fields():
    """卡片结构只规定形状：七个锚点键都必须出现（下班、到家可以是 null），未知字段被拒绝。"""
    anchors = make_card().time_anchors.model_dump()
    for name in ANCHOR_FIELDS:
        with pytest.raises(ValidationError) as caught:
            TimeAnchors.model_validate({key: value for key, value in anchors.items() if key != name})
        assert [(item["loc"], item["type"]) for item in caught.value.errors()] == [((name,), "missing")]
    with pytest.raises(ValidationError):
        TimeAnchors.model_validate({**anchors, "nap": {"start_hour": 13.0, "end_hour": 13.5}})
    with pytest.raises(ValidationError):
        TimeWindow.model_validate({"start_hour": 7.0, "end_hour": 7.5, "label": "wake"})
    with pytest.raises(ValidationError):
        PersonaCardStruct.model_validate({**persona_card_struct(make_card()), "big_five": {"openness": 4}})


def test_card_has_no_time_order_rule_of_its_own():
    """卡片不检查时间先后：起床晚于出门、就寝早于晚饭、下班晚于就寝的锚点都能构造，数值不变。"""
    anchors = TimeAnchors(
        wake=TimeWindow(start_hour=10.0, end_hour=10.5), leave_home=TimeWindow(start_hour=8.0, end_hour=8.5),
        lunch=TimeWindow(start_hour=12.0, end_hour=13.0), dinner=TimeWindow(start_hour=18.5, end_hour=19.5),
        bedtime=TimeWindow(start_hour=1.0, end_hour=7.0), leave_work=TimeWindow(start_hour=23.5, end_hour=24.5),
        arrive_home=None)
    assert (anchors.wake.start_hour, anchors.bedtime.start_hour, anchors.leave_work.end_hour) == (10.0, 1.0, 24.5)
