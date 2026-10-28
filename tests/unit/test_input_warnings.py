"""@brief 两种运行输入警告与清单冻结边界的纯函数合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.life.persona_inputs import persona_with
from worldmock.foundation.errors import IntegrityError
from worldmock.configuration.inputs.warnings import (
    INPUT_WARNING_CODES,
    content_empty_warnings,
    frozen_input_warnings,
    location_name_warnings,
    suspicious_name_patterns,
)

#: 没有任何 app 绑定内容的目录。
EMPTY = SimpleNamespace(contents={})


def _tastes(media: list[str]) -> dict:
    """只写媒介的口味表。"""
    return {"content_genres": ["悬疑"], "food_preferences": [], "media": media}


def _habit(trigger: str, response: str) -> dict:
    """一条习惯签名声明。"""
    return {"trigger": trigger, "response": response, "time_window": [22.0, 24.0]}


def test_the_warning_code_set_is_the_closed_two() -> None:
    """闭集恰为两个码：与模型抽取、覆盖率和作息覆盖有关的四个码随它们一起删除。"""
    assert INPUT_WARNING_CODES == ("CONTENT_EMPTY_WITH_MEDIA_TASTES", "LOCATION_NAME_SUSPICIOUS")


def test_empty_content_warns_for_media_and_habits_only() -> None:
    """没有 app 绑定内容时，人设表的媒介口味与带「看、刷、听」的习惯发警告，内容题材不参与。"""
    persona = persona_with(agent_id=5, tastes=_tastes(["短视频", "钉钉"]), habits=[_habit("听完以后", "提醒自己早点睡")])
    warnings = content_empty_warnings(persona, EMPTY)
    assert [(item["code"], item["agent_id"]) for item in warnings] == [("CONTENT_EMPTY_WITH_MEDIA_TASTES", 5)]
    assert warnings[0]["details"] == {"media": ["短视频", "钉钉"], "habits": ["提醒自己早点睡"]}
    assert warnings[0]["message"] == ("No app has bound content, but the persona mentions media use: "
                                      "media [短视频, 钉钉], habits [提醒自己早点睡].")
    bound = SimpleNamespace(contents={"content": SimpleNamespace(app_ids=("douyin",))})
    assert content_empty_warnings(persona, bound) == []
    quiet = persona_with(tastes=_tastes(["钉钉"]), habits=[])
    assert content_empty_warnings(quiet, EMPTY)[0]["details"] == {"media": ["钉钉"], "habits": []}
    assert "habits [none]" in content_empty_warnings(quiet, EMPTY)[0]["message"]


@pytest.mark.parametrize("medium", ["音乐", "播客", "游戏", "任意媒体偏好"])
def test_every_declared_media_preference_warns_when_no_content_is_bound(medium) -> None:
    """口味里写的媒介本身就是媒体偏好，不由标签或动词词表筛掉。"""
    warnings = content_empty_warnings(persona_with(tastes=_tastes([medium]), habits=[]), EMPTY)
    assert [item["code"] for item in warnings] == ["CONTENT_EMPTY_WITH_MEDIA_TASTES"]
    assert warnings[0]["details"] == {"media": [medium], "habits": []}


def test_content_empty_warning_does_not_infer_media_from_other_prose() -> None:
    """没有媒介口味、习惯里也没有媒体动词时，内容题材与日常习惯不触发缺内容警告。"""
    persona = persona_with(tastes=_tastes([]), habits=[_habit("睡前", "安静休息")])
    assert content_empty_warnings(persona, EMPTY) == []


def test_suspicious_display_names_are_reported_only_for_coordinates_and_identifiers() -> None:
    """LOCATION_NAME_SUSPICIOUS：带经纬度的显示名逐个警告；「7-Eleven（日常采买）」这类名字与普通店名不算。"""
    business = {f"location-{value}": "客户园区（东经121.5°、北纬31.2°）" for value in range(11)}
    locations = [SimpleNamespace(location_id=key, name=name) for key, name in business.items()]

    warnings = location_name_warnings(SimpleNamespace(locations={item.location_id: item for item in locations}))

    assert len(warnings) == 11 and {item["code"] for item in warnings} == {"LOCATION_NAME_SUSPICIOUS"}
    assert all(item["agent_id"] is None and "东经" in item["details"]["name"]
               and item["details"]["patterns"] == ["coordinates"] for item in warnings)
    white_collar = {"store": "7-Eleven（日常采买）", "home": "家", "office": "公司"}
    assert location_name_warnings(SimpleNamespace(locations={key: SimpleNamespace(location_id=key, name=name)
                                                             for key, name in white_collar.items()})) == []
    assert suspicious_name_patterns("便利店（31.2, 121.5）") == ["coordinates"]
    assert suspicious_name_patterns("星巴克-sim-01") == ["identifier"]
    assert suspicious_name_patterns("客户园区 task-client-a") == ["identifier"]
    assert suspicious_name_patterns("poi-node-1（东经121.5°）") == ["coordinates", "identifier"]
    for plain in ("7-Eleven（日常采买）", "星巴克（浦东南路 855 号）", "e-bike 租赁点", "增庆菜馆（炒菜.拌菜.盖饭）"):
        assert suspicious_name_patterns(plain) == []


def test_frozen_warnings_keep_the_closed_shape_and_null_means_not_computed() -> None:
    """冻结前校验：闭集形状原样通过，None 保持 None（引擎直驱没有计算警告），未知码或多余键即内部合同违约。"""
    warning = content_empty_warnings(persona_with(), EMPTY)[0]
    assert frozen_input_warnings([warning]) == [warning]
    assert frozen_input_warnings(None) is None and frozen_input_warnings([]) == []
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "code": "SOMETHING_ELSE"}])
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "extra": True}])


@pytest.mark.parametrize("code", ["CARD_FIELD_OVERRIDDEN", "CARD_FIELD_CONFLICT", "PERSONA_AGREEMENT_LOW",
                                  "COVERAGE_BELOW_THRESHOLD", "TASTE_GENRES_UNMATCHED"])
def test_deleted_warning_codes_are_rejected_at_manifest_boundary(code) -> None:
    """清单冻结边界拒绝已删除的警告码：作息覆盖的两个、抽取一致性、覆盖率，以及更早退役的码。"""
    warning = content_empty_warnings(persona_with(), EMPTY)[0]
    with pytest.raises(IntegrityError, match="closed warning shape"):
        frozen_input_warnings([{**warning, "code": code}])
