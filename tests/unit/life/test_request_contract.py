"""人物工具的封闭 JSON 形状与实际本地限制。"""

import pytest
from pydantic import ValidationError

from tests.life.conftest import conclude_request, errand_add, pause_request, report_request
from worldmock.life.models import (
    ConcludeActivityRequest, ErrandChange, PauseActivityRequest, ReportStatusRequest, StartActivityRequest,
)


@pytest.mark.parametrize("old", ["way", "app", "to", "pay_app", "planned_duration_seconds", "content_id"])
def test_start_item_rejects_every_removed_or_system_owned_field(old):
    payload = {"items": [{"action": "interact", "target": "家", old: None}]}
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate(payload)


@pytest.mark.parametrize("count", [0, 3, 4])
def test_start_must_have_one_or_two_items(count):
    with pytest.raises(ValidationError):
        StartActivityRequest(items=[{"action": "walk", "target": str(index)} for index in range(count)])


@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "", "  "])
@pytest.mark.parametrize("field", ["action", "target"])
def test_written_action_and_target_are_nonblank_strict_strings(field, bad):
    with pytest.raises(ValidationError):
        StartActivityRequest(items=[{**{"action": "interact", "target": "家"}, field: bad}])


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
