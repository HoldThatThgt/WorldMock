"""缺少必填字段时，纠错原因写明它在参数里的位置，包括嵌套数组与 60 字投影。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.state.interaction.errors import ErrorReport
from worldmock.state.interaction.request_errors import argument_report
from worldmock.state.interaction.requests import ConcludeActivityRequest


def _report(arguments: dict) -> ErrorReport:
    """结束活动的严格校验失败时，宿主与环境共用的报告；校验没失败即断言失败。"""
    with pytest.raises(ValidationError) as caught:
        ConcludeActivityRequest.model_validate(arguments)
    report = argument_report("conclude_activity", caught.value, ConcludeActivityRequest, arguments)
    assert report.error_code == "ARGUMENTS_INVALID" and report.fix == "Correct every reported parameter."
    assert report.reason == "; ".join(item.why for item in report.items)
    return report


def test_a_missing_field_is_named_by_its_position():
    """一次漏两处（数组项里缺 views、顶层漏 errands）：两条明细各带自己的位置，报告两句以 "; " 相接；
    第一句自带句号再接分隔符，所以中间出现 "views.;"，这是拼接规则的自然结果。"""
    report = _report({"items": [{"activity_name": "等快递", "summary": "等到中午才到。", "memories": []}]})
    assert [(item.path, item.written, item.allowed_values, item.expected, item.why)
            for item in report.items] == [
        (("items", 0, "views"), None, (), "This field is required; JSON type: array.",
         "This required field was omitted: items.0.views."),
        (("errands",), None, (), "This field is required; JSON type: array.",
         "This required field was omitted: errands.")]
    assert report.reason == ("This required field was omitted: items.0.views.; "
                             "This required field was omitted: errands.")


def test_a_missing_field_inside_an_array_item_is_named():
    """个人事务条目里缺 item：位置写到数组下标，expected 仍是 Schema 推出的 anyOf 那句，与位置无关。"""
    report = _report({
        "items": [{"activity_name": "等快递", "summary": "等到中午才到。", "memories": [], "views": []}],
        "errands": [{"change": "新增", "name": "取快递", "quantity": None, "memo": None,
                     "remind_time": None, "reason": None}]})
    assert [(item.path, item.written, item.allowed_values, item.expected, item.why)
            for item in report.items] == [
        (("errands", 0, "item"), None, (),
         "This field is required; A value satisfying one of the declared JSON types is required.",
         "This required field was omitted: errands.0.item.")]
    assert report.reason == "This required field was omitted: errands.0.item."


def test_the_longest_position_still_fits_the_projection():
    """位置最长的一句是 memories 里的 keywords：整句 61 字，截到 60 字只丢句号，字段名仍完整。"""
    report = _report({
        "items": [{"activity_name": "等快递", "summary": "等到中午才到。", "views": [],
                   "memories": [{"type": "routine", "text": "快递一般中午到。", "importance": 0.4}]}],
        "errands": []})
    assert [(item.path, item.written, item.allowed_values, item.expected, item.why)
            for item in report.items] == [
        (("items", 0, "memories", 0, "keywords"), None, (), "This field is required; JSON type: array.",
         "This required field was omitted: items.0.memories.0.keywords.")]
    assert report.reason == "This required field was omitted: items.0.memories.0.keywords."
    [item] = report.items
    assert len(item.why) == 61
    assert item.why[:60].endswith("keywords")
