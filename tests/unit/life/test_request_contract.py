"""人物工具的封闭 JSON 形状与实际本地限制。"""

import pytest
from pydantic import ValidationError

from tests.life.conftest import conclude_request, errand_add, pause_request, report_request
from worldmock.life.models import (
    ConcludeActivityRequest, ErrandChange, PauseActivityRequest, ReportStatusRequest, StartMainActivityRequest,
    StartSideActivityRequest,
)

_MAIN = {"action": "interact", "target": "家", "detail": "测试休息"}
_SIDE = {"action": "purchase", "target": ["牛肉面"], "prefer": None}


@pytest.mark.parametrize("old", ["way", "app", "to", "pay_app", "planned_duration_seconds", "content_id", "items"])
def test_start_request_rejects_every_removed_or_system_owned_field(old):
    for model, valid in ((StartMainActivityRequest, _MAIN), (StartSideActivityRequest, _SIDE)):
        model.model_validate(valid)
        with pytest.raises(ValidationError):
            model.model_validate({**valid, old: None})


@pytest.mark.parametrize("count", [0, 2, 3, 4])
def test_side_target_takes_at_least_one_name_and_exactly_one_app(count):
    """名称数组不能为空；用内容 app 恰好一项；购买不设上限，同名可以重复。"""
    names = [str(index) for index in range(count)]
    with pytest.raises(ValidationError):
        StartSideActivityRequest(action="interact", target=names, prefer=None)
    if count == 0:
        with pytest.raises(ValidationError):
            StartSideActivityRequest(action="purchase", target=names, prefer=None)
    else:
        assert StartSideActivityRequest(action="purchase", target=names, prefer=None).target == names
        assert StartSideActivityRequest(action="purchase", target=["同一件"] * count,
                                        prefer=None).target == ["同一件"] * count
    assert StartSideActivityRequest(action="interact", target=["哔哩哔哩"], prefer=None).target == ["哔哩哔哩"]


@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "", "  "])
@pytest.mark.parametrize("field", ["action", "target", "detail"])
def test_written_main_action_target_and_detail_are_nonblank_strict_strings(field, bad):
    """detail 只在睡眠时为 null：出行与在地点做事缺了它同样被拒。"""
    with pytest.raises(ValidationError):
        StartMainActivityRequest.model_validate({**_MAIN, field: bad})


@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "", "  "])
def test_written_side_action_and_every_target_name_are_nonblank_strict_strings(bad):
    with pytest.raises(ValidationError):
        StartSideActivityRequest.model_validate({**_SIDE, "action": bad})
    with pytest.raises(ValidationError):
        StartSideActivityRequest.model_validate({**_SIDE, "target": bad})
    with pytest.raises(ValidationError):
        StartSideActivityRequest.model_validate({**_SIDE, "target": ["牛肉面", bad]})


@pytest.mark.parametrize("factory", [pause_request, report_request, conclude_request])
def test_control_names_must_be_distinct_and_there_are_at_most_two(factory):
    with pytest.raises(ValidationError):
        factory("正在做的活动", "正在做的活动")
    with pytest.raises(ValidationError):
        factory("甲", "乙", "丙")
    with pytest.raises(ValidationError):
        factory()
    assert len(factory("甲", "乙").items) == 2


@pytest.mark.parametrize("factory", [pause_request, report_request, conclude_request])
def test_summary_limit_keeps_the_full_text_or_rejects_without_truncation(factory):
    assert factory("活动", summary="a" * 500).items[0].summary == "a" * 500
    with pytest.raises(ValidationError):
        factory("活动", summary="a" * 501)


@pytest.mark.parametrize("model,factory", [(PauseActivityRequest, pause_request),
    (ReportStatusRequest, report_request), (ConcludeActivityRequest, conclude_request)])
def test_control_requires_request_level_errands_and_rejects_old_parallel_arrays(model, factory):
    payload = factory("活动").model_dump(mode="json")
    payload.pop("errands")
    with pytest.raises(ValidationError):
        model.model_validate(payload)
    with pytest.raises(ValidationError):
        model.model_validate({"activity_names": ["活动"], "summaries": ["自述"], "errands": []})


@pytest.mark.parametrize("bad", [0, 10, True, 1.5, "1"])
def test_errand_quantity_is_a_strict_integer_one_through_nine(bad):
    with pytest.raises(ValidationError):
        ErrandChange.model_validate(errand_add("买纸巾", "纸巾", bad))


@pytest.mark.parametrize("field", ["change", "name", "item", "quantity", "memo", "remind_time", "reason"])
def test_every_errand_change_key_is_explicitly_present(field):
    payload = errand_add("买纸巾", "纸巾")
    payload.pop(field)
    with pytest.raises(ValidationError):
        ErrandChange.model_validate(payload)


def test_self_report_cannot_add_an_errand_completion_operation():
    payload = {**errand_add("买纸巾", "纸巾"), "change": "完成"}
    with pytest.raises(ValidationError):
        ErrandChange.model_validate(payload)
