"""模型伪造的候选游标必须明确拒绝，不能强制转换或穿透为运行故障。"""

import json

import pytest

from tests.life.conftest import advance_until_ready, submit_round
from worldmock.life import presentation
from worldmock.life.models import BrowseActivitiesRequest


@pytest.mark.parametrize("key,offset", [
    ([None, 0, "x"], 0), ([[], 0, "x"], 0), ([{}, 0, "x"], 0),
    ([0, None, "x"], 0), ([True, 0, "x"], 0), ([0.5, 0, "x"], 0),
    (["0", 0, "x"], 0), ([float("inf"), 0, "x"], 0),
    ([0, float("nan"), "x"], 0), ([0, 0, None], 0), ([0, 0, 7], 0),
    ([0, 0, " "], 0), ([0, 0, "x"], None), ([0, 0, "x"], True),
    ([0, 0, "x"], []), ([0, 0, "x"], float("inf")),
    ([0, 0, "x"], 0.5), ([0, 0, "x"], "0"), ([0, 0, "x"], -1),
])
def test_cursor_rejects_wrong_field_types_without_coercion(key, offset) -> None:
    """排序键、偏移和身份必须使用生成器实际输出的类型。"""
    cursor = json.dumps({"key": key, "offset": offset, "identity": "visible-page"})
    with pytest.raises(ValueError, match="candidate cursor"):
        presentation.cursor_offset(cursor)


@pytest.mark.parametrize("payload", [None, [], True, 0, "null", {},
    {"key": [0, 0, "x"], "identity": "id"},
    {"key": [0, 0, "x"], "offset": 0},
    {"key": [0, 0, "x"], "offset": 0, "identity": None},
    {"key": [0, 0, "x"], "offset": 0, "identity": ""},
    {"key": [0, 0, "x"], "offset": 0, "identity": "id", "extra": 1},
])
def test_cursor_requires_the_complete_generated_shape(payload) -> None:
    """伪造或缺字段的游标不得使用缺省偏移或虚构身份继续分页。"""
    with pytest.raises(ValueError, match="candidate cursor"):
        presentation.cursor_offset(json.dumps(payload))


def test_cursor_does_not_catch_internal_failures(monkeypatch) -> None:
    """内部故障保留原异常，避免把代码缺陷误报为模型输入问题。"""
    def fail(_cursor):
        raise RuntimeError("internal parser failure")
    monkeypatch.setattr(presentation.json, "loads", fail)
    with pytest.raises(RuntimeError, match="internal parser failure"):
        presentation.cursor_offset('{"key":[0,0,"x"],"offset":0,"identity":"id"}')


@pytest.mark.parametrize("key,offset", [([None, 0, "x"], 0),
    ([0, 0, "x"], None), ([float("inf"), 0, "x"], 0)])
def test_malformed_cursor_is_a_business_rejection_and_next_round_survives(engine_factory, key, offset) -> None:
    """真实提交拒绝空值与非有限数，保存拒绝事件并允许随后正常浏览。"""
    engine = engine_factory()
    cursor = json.dumps({"key": key, "offset": offset, "identity": "invalid-page"})
    _, results = submit_round(engine, {1: ("browse_activities",
        BrowseActivitiesRequest(category="sport", cursor=cursor))}, "bad-cursor")
    assert results[0].status.value == "rejected"
    assert results[0].error_code == "BROWSE_CURSOR_INVALID"
    assert any(event.data.get("error_code") == "BROWSE_CURSOR_INVALID"
               for event in engine.store.read_agent_events("action_rejected", 1))
    assert advance_until_ready(engine)
    _, retry = submit_round(engine, {1: ("browse_activities",
        BrowseActivitiesRequest(category="sport", cursor=None))}, "cursor-retry")
    assert retry[0].status.value == "completed"
