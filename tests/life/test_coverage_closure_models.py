"""覆盖闭合：冻结模型身份、活动目标、交互菜单解析与组装期品类配对的失败关闭分支。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.interaction_menu import InteractionMenu, resolve_interaction_menu
from worldmock.life.models import ActivityKind, ActivityStartParams, ModelIdentity
from worldmock.model_endpoint import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

#: 与正式运行同形、只差被测字段的冻结模型身份。
_IDENTITY = {
    "provider": "openai_compatible",
    "base_url": DEEPSEEK_BASE_URL,
    "requested_model": DEEPSEEK_MODEL,
    "thinking": "enabled",
    "tool_choice": "auto",
    "max_retries": 0,
    "prompt_sha256": "0" * 64,
    "tool_schema_sha256": "1" * 64,
}
_ENDPOINT_MISMATCH = "Model identity endpoint does not match the configured DeepSeek model"

#: 字段说明登记的六种必须携带业务目标的活动类型。
_TARGETED_KINDS = (
    ActivityKind.WORK,
    ActivityKind.WATCH,
    ActivityKind.PLAY_GAME,
    ActivityKind.READ_MAIL,
    ActivityKind.LIFESTYLE,
)

#: 运行视图缺少某一字段的占位；构造视图时直接省略该属性。
_ABSENT = object()
_LOCATION_MENU = InteractionMenu(allowed_actions=("sleep", "travel_to", "wait"))
_RAW_MENU = {"allowed_actions": ["sleep", "wait"]}


@pytest.mark.parametrize(
    ("field", "value"),
    [("base_url", "https://api.openai.com/v1"), ("base_url", "https://api.deepseek.com"),
     ("requested_model", "deepseek-reasoner")],
    ids=["foreign-base-url", "main-site-base-url", "foreign-model"],
)
def test_model_identity_accepts_only_the_configured_deepseek_endpoint(
    field: str, value: str, caplog: pytest.LogCaptureFixture,
) -> None:
    """冻结身份只认单一官方端点，即 strict 所在的 /beta（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-strict-端点 ）：换地址（含主站）或换模型
    都以英文原因拒绝并记错误日志。"""
    assert ModelIdentity.model_validate(_IDENTITY).base_url == DEEPSEEK_BASE_URL == "https://api.deepseek.com/beta"
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError, match=_ENDPOINT_MISMATCH):
        ModelIdentity.model_validate({**_IDENTITY, field: value})
    assert _ENDPOINT_MISMATCH in caplog.messages


@pytest.mark.parametrize("kind", _TARGETED_KINDS, ids=lambda kind: kind.value)
@pytest.mark.parametrize("target_id", [None, ""], ids=["missing", "empty"])
def test_targeted_activity_requires_a_business_target(kind: ActivityKind, target_id: str | None) -> None:
    """有业务目标的活动缺目标或给空目标都在受理前拒绝；补上目标即可受理。"""
    with pytest.raises(ValidationError, match="target_id is required for the selected activity"):
        ActivityStartParams(activity_type=kind, planned_duration_seconds=600, target_id=target_id)
    accepted = ActivityStartParams(activity_type=kind, planned_duration_seconds=600, target_id="target")
    assert accepted.target_id == "target"


def _menu_view(**overrides: object) -> SimpleNamespace:
    """构造只含菜单解析所读字段的运行视图，按需替换或省略其中一项。"""
    view = {
        "agents": (SimpleNamespace(agent_id=1, interaction_menu=None),),
        "locations": (SimpleNamespace(location_id="home", kind="home"),),
        "interaction_menus_by_location_kind": {"home": _LOCATION_MENU},
    }
    view.update(overrides)
    return SimpleNamespace(**{key: value for key, value in view.items() if value is not _ABSENT})


def test_menu_view_without_person_declaration_uses_the_location_menu() -> None:
    """基准视图有效：人物未声明菜单时有效菜单即地点类菜单，下面各反例只改一处。"""
    assert resolve_interaction_menu(_menu_view(), 1, "home") == _LOCATION_MENU


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"interaction_menus_by_location_kind": _ABSENT},
         "Interaction menu location mapping is unavailable"),
        ({"interaction_menus_by_location_kind": (("home", _LOCATION_MENU),)},
         "Interaction menu location mapping is unavailable"),
        ({"interaction_menus_by_location_kind": {"office": _LOCATION_MENU}},
         "Interaction menu for the current location kind is unavailable"),
        ({"interaction_menus_by_location_kind": {"home": _RAW_MENU}},
         "Interaction menu for the current location kind is unavailable"),
        ({"agents": (SimpleNamespace(agent_id=1),)},
         "Interaction menu agent declaration is invalid"),
        ({"agents": (SimpleNamespace(agent_id=1, interaction_menu=_RAW_MENU),)},
         "Interaction menu agent declaration is invalid"),
        ({"agents": (SimpleNamespace(agent_id=2, interaction_menu=None),)},
         "Interaction menu agent identity is missing or duplicated"),
    ],
    ids=["mapping-absent", "mapping-not-dict", "kind-undeclared", "kind-raw-declaration",
         "agent-declaration-absent", "agent-raw-declaration", "agent-unknown"],
)
def test_menu_resolution_fails_closed_on_a_damaged_runtime_view(
    overrides: dict, message: str, caplog: pytest.LogCaptureFixture,
) -> None:
    """地点类映射缺失或非映射、地点类无冻结菜单、人物声明缺失或未冻结、人物身份缺失都以完整性错误终止并记日志。"""
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match=f"^{message}$"):
        resolve_interaction_menu(_menu_view(**overrides), 1, "home")
    assert caplog.messages == [f"WorldMock failure: {message}"]
