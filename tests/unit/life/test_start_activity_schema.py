"""@brief 动作与控制请求的模型约束和方式词表。"""

import pytest
from pydantic import ValidationError

from tests.life.request_fixtures import request as _request
from worldmock.life.models import ConcludeActivityRequest, PauseActivityRequest, ReportStatusRequest, StartActivityRequest
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.vocabulary import LIFE_VERBS, WAYS

SPEC_WAYS_BY_VERB = {"travel": {"步行", "驾车"}, "interact": {"进行", "等待"},
                     "obtain": {"购买", "领取"}, "sleep": {"睡觉"}}


def test_start_activity_schema_rejects_model_supplied_calculation():
    """@brief 五键和回答关联都必填，拒绝来源、偏好、数组及模型计算字段。"""
    request = _request(("地点：公司", "步行", None, None, None))
    expected = {"items": [{"target": "地点：公司", "way": "步行", "app": None, "to": None, "pay_app": None}],
                "answering": None}
    assert request.model_dump() == expected
    for missing in ("items", "answering"):
        with pytest.raises(ValidationError):
            StartActivityRequest.model_validate({key: value for key, value in expected.items() if key != missing})
    for missing in ("target", "way", "app", "to", "pay_app"):
        payload = {**expected, "items": [{key: value for key, value in expected["items"][0].items() if key != missing}]}
        with pytest.raises(ValidationError):
            StartActivityRequest.model_validate(payload)
    for field, value in {"price_version": "v1", "planned_duration_seconds": 600, "destination_id": "office",
                         "quantity": 2, "prefer": None, "from": None, "targets": [], "actions": []}.items():
        with pytest.raises(ValidationError):
            StartActivityRequest.model_validate({**expected, field: value})
    for field in ("from", "source", "prefer", "action", "quantity", "duration"):
        with pytest.raises(ValidationError):
            StartActivityRequest.model_validate({**expected, "items": [{**expected["items"][0], field: None}]})



def test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb():
    assert LIFE_VERBS == ("travel", "interact", "obtain", "sleep")
    assert {verb: {way for way, (owner, _) in WAYS.items() if owner == verb} for verb in LIFE_VERBS} == SPEC_WAYS_BY_VERB
    assert {WAYS[way] for way in TRAVEL_WAYS.values()} == {("travel", "travel_to")}
    assert WAYS["购买"] == ("obtain", "purchase") and WAYS["领取"] == ("obtain", "collect_parcel")
    assert not {"食用", "使用", "给予", "到店", "远程下单", "打开", "工作", "阅读", "交谈", "通话"} & set(WAYS)



@pytest.mark.parametrize("items", [(),
    (("地点：公司", "步行", None, None, None), ("地点：公司", "步行", None, None, None)),
    (("地点：公司", "步行", None, None, None), ("自己", "等待", None, None, None),
     ("地点：五里公园", "步行", None, None, None)),
])
def test_invalid_item_count_and_duplicate_targets_are_schema_errors(items):
    with pytest.raises(ValidationError):
        _request(*items)



@pytest.mark.parametrize("field", ["app", "to", "pay_app"])
@pytest.mark.parametrize("value", [[], [None], [None, "人物：同事"]])
def test_route_fields_are_individual_nullable_strings(field, value):
    request = _request(("牛肉面", "购买", "美团", None, "微信")).model_dump()
    request["items"][0][field] = value
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate(request)



def test_control_requests_pair_each_task_with_its_summary():
    """三条控制工具一项一个对象：活动名与自述写在同一对象里，不再靠下标对齐三组数组；一至两项、活动名互不相同且非空、
    自述非空；结束工具每项另有必填的 memories 与 views（都可以是空列表）；旧的平行数组形状不再受理
    （docs/design/04-simulation-engine.md 第六章）；请求级 errands 必填（可以是空列表），缺它的请求不受理
    （docs/design/04-simulation-engine.md ）。"""
    item = {"activity_name": "工作写会议纪要（1月5日 09:00 起）", "summary": "先写完提纲"}
    other = {"activity_name": "休息（1月5日 10:00 起）", "summary": "歇一会儿"}
    for model in (ReportStatusRequest, PauseActivityRequest):
        assert model(items=[item, other], errands=[]).model_dump(mode="json") == {
            "items": [item, other], "errands": []}
        for items in ([], [item, other, {**other, "activity_name": "吃饭"}], [item, item], [{**item, "summary": " "}],
                      [{**item, "activity_name": " "}], [{"activity_name": item["activity_name"]}],
                      [{**item, "memories": []}]):
            with pytest.raises(ValidationError):
                model(items=items, errands=[])
        with pytest.raises(ValidationError):
            model(task=[item["activity_name"]], summary=[item["summary"]])
        with pytest.raises(ValidationError, match="errands"):
            model(items=[item])
    concluded = {**item, "memories": [], "views": []}
    assert ConcludeActivityRequest(items=[concluded], errands=[]).model_dump(mode="json") == {
        "items": [concluded], "errands": []}
    for missing in ("memories", "views"):
        with pytest.raises(ValidationError):
            ConcludeActivityRequest(items=[{key: value for key, value in concluded.items() if key != missing}],
                                    errands=[])
    with pytest.raises(ValidationError, match="errands"):
        ConcludeActivityRequest(items=[concluded])
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=[item["activity_name"]], summary=[item["summary"]], memories=[[]])
