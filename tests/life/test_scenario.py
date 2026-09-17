"""生活场景文档闭集字段、数值边界、跨引用与目录组装测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.scenario import LifeScenario, load_scenario
from worldmock.life.semantics import assemble_semantics


_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_CATALOG = Path("catalog")


def _payload() -> dict:
    """读取正式场景为每个反例提供独立对象。"""
    return json.loads(_FORMAL.read_text())


def _replace(path: tuple[str | int, ...], value: object) -> dict:
    """替换一个场景字段并返回反例。"""
    payload = _payload()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return payload


def _assert_invalid(payload: dict, message: str) -> None:
    """确认场景文档在启动前以预期原因失败。"""
    with pytest.raises(ValidationError, match=message):
        LifeScenario.model_validate(payload)


def _assert_assembly_invalid(payload: dict, message: str) -> None:
    """确认目录组装阶段以稳定英文原因拒绝。"""
    document = LifeScenario.model_validate(payload)
    with pytest.raises(IntegrityError, match=message):
        assemble_semantics(document, load_catalog(_CATALOG))


def test_formal_and_multi_person_scenarios_are_valid() -> None:
    """正式与多人场景完整加载：文档校验、目录组装与准入全部通过。"""
    for path in (_FORMAL, Path("scenarios/multi-person.json")):
        scenario, digest = load_scenario(path)
        assert len(digest) == 64
        assert scenario.body.work_notes_multiplier == 1.1
        assert scenario.body.basal_kcal_per_hour == 65
        assert scenario.max_consecutive_rejections == 10
        assert scenario.transport_account_id == "transport:service"
        assert all(route.fare_source_kind == "scenario" for route in scenario.routes)


def test_motivation_attention_prior_must_equal_registry_kinds() -> None:
    """注册表规则 6：画像注意力键集 == attention_required=true 键集，多键/少键均拒绝。"""
    index = next(position for position, agent in enumerate(_payload()["agents"])
                 if agent.get("motivation"))
    # 少键：删掉一个注意力键（数值区间不受影响，键集检查单独拒绝）。
    payload = _payload()
    prior = payload["agents"][index]["motivation"]["attention_prior"]
    del prior[next(iter(prior))]
    _assert_invalid(payload, "attention_required kinds")
    # 多键：塞一个 attention_required=false 的显式排除品类（game_session）。
    payload = _payload()
    payload["agents"][index]["motivation"]["attention_prior"]["game_session"] = 0.0
    _assert_invalid(payload, "attention_required kinds")


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("body", "mass_kg"), 0, "greater than 0"),
        (("body", "initial_entertainment_need"), 1.5, "less than or equal to 1"),
        (("body", "initial_energy_kcal"), -1, "greater than or equal to 0"),
        (("max_consecutive_rejections",), 0, "greater than 0"),
        (("catalog_version", "minor"), -1, "greater than or equal to 0"),
        (("products", 0, "stock"), -1, "greater than or equal to 0"),
    ],
)
def test_body_instance_and_rejection_ranges_are_enforced(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    """身体实例初值、目录钉扎、库存覆盖与拒绝预算都有明确范围。"""
    _assert_invalid(_replace(path, value), message)


def test_accounts_and_roles_stay_unique_and_non_blank() -> None:
    """账户实例身份与角色在文档内唯一且不得为空白。"""
    duplicated = _payload()
    duplicated["accounts"].append(dict(duplicated["accounts"][0]))
    _assert_invalid(duplicated, "account_id values must be unique")
    duplicated_role = _payload()
    entry = dict(duplicated_role["accounts"][0])
    entry["account_id"] = "merchant:other"
    duplicated_role["accounts"].append(entry)
    _assert_invalid(duplicated_role, "role values must be unique")
    _assert_invalid(_replace(("accounts", 0, "account_id"), " "), "account ids and roles must not be blank")


@pytest.mark.parametrize(
    ("skills", "message"),
    [
        ({"meeting_analysis": -0.1}, "skill values"),
        ({"meeting_analysis": 1.1}, "skill values"),
        ({" ": 0.5}, "skill names"),
    ],
)
def test_agent_skills_have_valid_names_and_levels(skills: dict[str, float], message: str) -> None:
    """人物技能名称非空且强度限定在零到一。"""
    _assert_invalid(_replace(("agents", 0, "skill_levels"), skills), message)


def test_task_skill_and_preference_targets_must_be_declared() -> None:
    """任务技能和娱乐偏好不能引用场景外身份。"""
    _assert_invalid(
        _replace(("tasks", 0, "required_skill"), "missing-skill"),
        "unknown skill",
    )
    _assert_invalid(
        _replace(("agents", 0, "entertainment_preferences"), {"missing-content": 0.5}),
        "unknown item",
    )


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (("tasks", 0, "required_location_id"), "work task references an unknown location"),
        (("products", 0, "merchant_location_id"), "product references an unknown merchant location"),
        (("products", 0, "merchant"), "product merchant account is not declared"),
    ],
)
def test_all_declared_location_and_merchant_references_are_resolved(
    path: tuple[str | int, ...], message: str
) -> None:
    """工作地点、商品商家地点与商家账户都必须指向场景内声明。"""
    _assert_invalid(_replace(path, "missing-location"), message)


def test_product_merchant_cannot_bind_an_agent_account() -> None:
    """服务账户不得复用人物账户身份。"""
    _assert_invalid(
        _replace(("products", 0, "merchant"), "agent:1"),
        "product merchant account is not declared",
    )


def test_route_ids_and_lookup_keys_are_unique() -> None:
    """稳定路线身份与运行时查找自然键都保持唯一。"""
    duplicate_id = _replace(("routes", 1, "route_id"), _payload()["routes"][0]["route_id"])
    _assert_invalid(duplicate_id, "route_id values must be unique")
    duplicate_key = _payload()
    copied = dict(duplicate_key["routes"][0])
    copied["route_id"] = "different-id"
    duplicate_key["routes"].append(copied)
    _assert_invalid(duplicate_key, "route lookup keys")


@pytest.mark.parametrize(
    ("route_index", "fare"),
    [
        (0, 0),
        (1, 1),
    ],
)
def test_route_fares_follow_the_catalog_formula(route_index: int, fare: int) -> None:
    """路线报价偏离目录公式求值时在组装阶段拒绝。"""
    _assert_assembly_invalid(
        _replace(("routes", route_index, "fare_fen"), fare),
        "disagrees with the catalog formula",
    )


def test_route_fare_source_is_closed() -> None:
    """路线费用来源只能标记为场景假设。"""
    _assert_invalid(
        _replace(("routes", 0, "fare_source_kind"), "osrm"),
        "Input should be 'scenario'",
    )


def test_catalog_pin_and_reference_admission_are_enforced() -> None:
    """目录版本钉扎与对象引用完整性在组装准入层拒绝。"""
    _assert_assembly_invalid(
        _replace(("catalog_version", "major"), 2),
        "pins catalog",
    )
    _assert_assembly_invalid(
        _replace(("products", 0, "product_id"), "food-0000000000000"),
        "is not in the catalog",
    )
    _assert_assembly_invalid(
        _replace(("products", 0, "merchant"), "merchant:general"),
        "does not bind catalog role",
    )


def test_work_and_message_references_stay_inside_scenario() -> None:
    """任务与邮件引用不能越出地点、账户、人物和任务目录。"""
    _assert_invalid(
        _replace(("messages", 0, "recipient_agent_id"), 999),
        "message references an unknown agent",
    )
    _assert_invalid(
        _replace(("messages", 0, "related_task_id"), "missing-task"),
        "message references an unknown task",
    )
    _assert_assembly_invalid(
        _replace(("tasks", 0, "employer_account_id"), "missing-account"),
        "unknown employer",
    )


def test_scheduled_records_use_scenario_timezone_and_window() -> None:
    """任务和邮件不会以错误时区或不可到达时刻进入场景。"""
    _assert_invalid(
        _replace(("tasks", 0, "available_at"), "2026-01-05T09:00:00+00:00"),
        "task timestamps do not match",
    )
    _assert_invalid(
        _replace(("tasks", 0, "due_at"), "2026-04-01T20:00:00+08:00"),
        "task is outside",
    )
    _assert_invalid(
        _replace(("messages", 0, "sent_at"), "2026-03-30T00:00:00+08:00"),
        "message timestamp is invalid",
    )
    _assert_invalid(_replace(("timezone",), "Missing/Zone"), "timezone is unknown")


def test_location_hours_content_kind_and_calendar_are_closed() -> None:
    """当前运行时使用的营业、内容和日历集合在启动前闭合。"""
    _assert_invalid(_replace(("locations", 1, "close_hour"), 8), "closing hour")
    _assert_invalid(_replace(("contents", 0, "kind"), "video"), "subtitle_experience")
    _assert_invalid(_replace(("calendar", "sick_leave_date"), "2026-02-30"), "ISO format")


def test_stock_override_replaces_the_catalog_default() -> None:
    """库存覆盖优先生效，缺省回退目录对象库存。"""
    payload = _replace(("products", 0, "stock"), 7)
    document = LifeScenario.model_validate(payload)
    semantics = assemble_semantics(document, load_catalog(_CATALOG))
    product = semantics.scenario.products[0]
    assert product.initial_stock == 7
    default = next(item for item in semantics.scenario.products if item.product_id != product.product_id)
    assert default.initial_stock in (120, 80)
