"""生活场景文档闭集字段、数值边界、跨引用与目录组装测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.scenario import InventoryItemConfig, LifeScenario, RuntimeScenario
from worldmock.life.world_setup import load_scenario
from worldmock.life.map_data import load_map_dataset, validate_map_dataset
from worldmock.life.map_models import MapDataset, RouteConfig
from worldmock.life.semantics import assemble_semantics
from worldmock.scheduling.compile import compile_schedule
from tests.engine_fixture import (_mutated_scenario)  # noqa: F401
from tests.life.work_schedule_fixtures import STANDARD_SHIFT, weekday_schedule
from worldmock.life.scenario import parse_scenario_document


_FORMAL = ENVIRONMENT_ROOT / "scenarios/single-person.json"
_CATALOG = (ENVIRONMENT_ROOT / "catalog")


def _payload() -> dict:
    """读取正式场景为每个反例提供独立对象。"""
    return json.loads(_FORMAL.read_text(encoding="utf-8"))


def _map(document=None):
    """加载与指定场景钉扎一致的完整地图。"""
    document = document or LifeScenario.model_validate(_payload())
    return load_map_dataset((ENVIRONMENT_ROOT / "corpora"), document.map_dataset)


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
        assemble_semantics(document, load_catalog(_CATALOG), _map(document))


@pytest.mark.parametrize(
    ("holding", "message"),
    [
        ({" ": {"milk": 1}}, "locations and holdings must not be empty"),
        ({"home": {}}, "locations and holdings must not be empty"),
        ({"home": {" ": 1}}, "object ids must not be empty"),
        ({"home": {"milk": 0}}, "quantities must be positive finite"),
        ({"home": {"milk": -1}}, "quantities must be positive finite"),
        ({"home": {"milk": float("inf")}}, "quantities must be positive finite"),
        ({"home": {"milk": float("nan")}}, "quantities must be positive finite"),
        ({"unknown": {"milk": 1}}, "references an unknown location"),
    ],
)
def test_initial_inventory_shape_rejects_invalid_holdings(holding, message) -> None:
    """预置库存不能以空身份、非正数或未知地点进入场景。"""
    _assert_invalid(_replace(("agents", 0, "initial_inventory"), holding), message)


@pytest.mark.parametrize("bindings", [{" ": "milk"}, {"food": " "}])
def test_recipe_output_binding_requires_nonblank_category_and_object(bindings) -> None:
    """文档与运行视图均拒绝空白的配方输出品类或对象身份。"""
    _assert_invalid(_replace(("recipe_output_bindings",), bindings), "must use non-empty ids")
    scenario, _ = load_scenario(_FORMAL)
    runtime = scenario.model_dump(mode="json")
    runtime["recipe_output_bindings"] = bindings
    with pytest.raises(ValidationError, match="must use non-empty ids"):
        RuntimeScenario.model_validate(runtime)


@pytest.mark.parametrize("kind", ["unknown_location", "undeclared_product", "overlap"])
def test_runtime_inventory_cross_references_are_validated(kind) -> None:
    """运行视图拒绝未知库存引用以及商品和纯库存对象身份冲突。"""
    scenario, _ = load_scenario(_FORMAL)
    runtime = scenario.model_dump(mode="json")
    product_id = runtime["products"][0]["product_id"]
    location_id = runtime["locations"][0]["location_id"]
    if kind == "unknown_location":
        runtime["agents"][0]["initial_inventory"] = {"missing": {product_id: 1}}
        message = "unknown location"
    elif kind == "undeclared_product":
        runtime["agents"][0]["initial_inventory"] = {location_id: {"missing": 1}}
        message = "undeclared scenario product"
    else:
        runtime["inventory_items"] = [{
            key: value for key, value in runtime["products"][0].items()
            if key in InventoryItemConfig.model_fields
        }]
        message = "must not overlap"
    with pytest.raises(ValidationError, match=message):
        RuntimeScenario.model_validate(runtime)


def test_formal_and_multi_person_scenarios_are_valid() -> None:
    """选定场景完整加载，实例字段与目录身体参数原样进入运行视图。"""
    catalog = load_catalog(_CATALOG)
    for path in (_FORMAL, ENVIRONMENT_ROOT / "scenarios/multi-person.json"):
        document, expected_digest = parse_scenario_document(path)
        scenario, digest = load_scenario(path)
        assert digest == expected_digest and len(digest) == 64
        assert scenario.body.basal_kcal_per_hour == catalog.resources["energy_kcal"].drain_rules.basal_kcal_per_hour
        assert scenario.max_same_moment_decisions == document.max_same_moment_decisions
        transport = next(item.account_id for item in document.accounts if item.role == "transport:service")
        assert scenario.transport_account_id == transport
        assert scenario.schema_version == document.schema_version == 6


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
        (("max_same_moment_decisions",), 0, "greater than 0"),
        (("activity_feedback_seconds",), 59, "greater than or equal to 60"),
        (("catalog_version", "minor"), -1, "greater than or equal to 0"),
        (("supplies", 0, "initial_stock"), -1, "greater than or equal to 0"),
    ],
)
def test_body_instance_and_rejection_ranges_are_enforced(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    """身体实例初值、目录钉扎、库存覆盖、同刻上限与结算间隔下限（60 秒）都有明确范围。"""
    _assert_invalid(_replace(path, value), message)


def test_relief_preference_floor_defaults_to_half_without_changing_scenario_files(tmp_path) -> None:
    """文档未声明下限偏好时，真实加载与身体装配均注入规格缺省值 0.5。"""
    path = _mutated_scenario(_FORMAL,
                             lambda payload: payload["body"].pop("entertainment_relief_preference_floor", None),
                             tmp_path / "default-relief-floor")
    document, _ = parse_scenario_document(path)
    assert document.body.entertainment_relief_preference_floor == 0.5
    scenario, _ = load_scenario(path)
    assert scenario.schema_version == document.schema_version == 6
    assert scenario.body.entertainment_relief_preference_floor == 0.5


@pytest.mark.parametrize("floor", [0.3, 0.7])
def test_declared_relief_preference_floor_is_assembled_into_body_config(floor: float) -> None:
    """场景声明的下限偏好经 _assemble_body 原样并入 BodyConfig，目录与其余身体参数不受影响。"""
    payload = _replace(("body", "entertainment_relief_preference_floor"), floor)
    document = LifeScenario.model_validate(payload)
    body = assemble_semantics(document, load_catalog(_CATALOG), _map(document)).body
    assert body.entertainment_relief_preference_floor == floor
    assert body.entertainment_relief_per_hour == load_scenario(_FORMAL)[0].body.entertainment_relief_per_hour


@pytest.mark.parametrize(("value", "message"), [
    (0, "greater than 0"), (1, "less than 1"), (-0.2, "greater than 0"), (1.5, "less than 1"),
    (float("nan"), "finite number"),
])
def test_relief_preference_floor_outside_the_open_unit_interval_is_rejected(value: float, message: str) -> None:
    """f 取开区间 (0, 1)：0、1、负数、大于 1 与非有限值都在场景加载时拒绝。"""
    _assert_invalid(_replace(("body", "entertainment_relief_preference_floor"), value), message)


def test_relief_preference_floor_field_states_value_meaning_and_hypothesis() -> None:
    """场景层字段说明登记取值、含义与实验假设（敏感区间 0.3–0.7），是 f 在场景层的唯一登记处。"""
    from worldmock.life.scenario import BodyInstanceConfig

    field = BodyInstanceConfig.model_fields["entertainment_relief_preference_floor"]
    assert field.default == 0.5
    for fragment in ("缺省 0.5", "(0, 1)", "f + (1 − f)·w", "实验假设", "0.3–0.7"):
        assert fragment in field.description


def test_relief_preference_floor_is_registered_once_in_the_experiment_hypotheses() -> None:
    """docs/EXPERIMENT.md#experiment-worldmock-实验设置「实验假设声明」恰有一条登记下限偏好 f（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径  验收：f 在场景层字段
    与 docs/EXPERIMENT.md#experiment-worldmock-实验设置 各登记一次）：取值是场景层字段的缺省值，写明敏感区间、有效偏好公式、取值范围与场景层字段名；
    除版本表外，其余章节不再写 f 的取值或字段名（「需求状态」只指向这一条）。"""
    from worldmock.life.scenario import BodyInstanceConfig

    sections = Path("docs/EXPERIMENT.md").read_text(encoding="utf-8").split("\n## ")
    hypotheses = next(part for part in sections if part.startswith("实验假设声明"))
    registered = [item for item in hypotheses.split("\n- ") if "entertainment_relief_preference_floor" in item]
    assert len(registered) == 1
    field = BodyInstanceConfig.model_fields["entertainment_relief_preference_floor"]
    for fragment in (f"`f = {field.default}`", "敏感区间 0.3–0.7", "`f + (1 − f)·w`", "(0, 1)",
                     "`entertainment_relief_preference_floor`"):
        assert fragment in registered[0], fragment
    others = [part for part in sections if not part.startswith(("实验假设声明", "版本表"))]
    assert [part.splitlines()[0] for part in others
            if "entertainment_relief_preference_floor" in part or "f = " in part] == []
    assert "下限偏好 `f` 见「实验假设声明」" in next(part for part in sections if part.startswith("场景参数"))


def test_account_identities_are_unique_and_seller_roles_can_repeat() -> None:
    """账户实例身份唯一；不同卖方允许声明相同业务角色。"""
    duplicated = _payload()
    duplicated["accounts"].append(dict(duplicated["accounts"][0]))
    _assert_invalid(duplicated, "account_id values must be unique")
    repeated_role = _payload()
    entry = dict(repeated_role["accounts"][0])
    entry["account_id"] = "merchant:other"
    repeated_role["accounts"].append(entry)
    assert LifeScenario.model_validate(repeated_role).accounts[-1].role == entry["role"]
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


def test_task_skill_targets_must_be_declared() -> None:
    """任务技能不能引用场景外身份；娱乐偏好条目校验已随随机偏好浮点删除。"""
    _assert_invalid(
        _replace(("tasks", 0, "required_skill"), "missing-skill"),
        "unknown skill",
    )
    # docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」：随机偏好浮点字段整体删除——场景再携带该键按未知字段拒绝。
    payload = _replace(("agents", 0, "entertainment_preferences"), {"missing-content": 0.5})
    _assert_invalid(payload, "Extra inputs are not permitted")


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (("tasks", 0, "required_location_id"), "work task references an unknown location"),
        (("products", 0, "merchant_location_id"), "Extra inputs are not permitted"),
        (("products", 0, "merchant"), "Extra inputs are not permitted"),
    ],
)
def test_all_declared_location_and_merchant_references_are_resolved(
    path: tuple[str | int, ...], message: str
) -> None:
    """工作地点、商品商家地点与商家账户都必须指向场景内声明。"""
    _assert_invalid(_replace(path, "missing-location"), message)


def test_supply_seller_cannot_bind_an_agent_account() -> None:
    """供给卖方不得复用人物账户身份。"""
    document = LifeScenario.model_validate(_payload())
    dataset = _map(document)
    supplies = (dataset.supplies[0].model_copy(update={"seller_account_id": "agent:1"}), *dataset.supplies[1:])
    with pytest.raises(IntegrityError, match="seller|merchant"):
        assemble_semantics(document, load_catalog(_CATALOG), dataset.model_copy(update={"supplies": supplies}))

def test_route_ids_and_lookup_keys_are_unique() -> None:
    """独立地图的路线身份与运行时自然键都保持唯一。"""
    dataset = _map()
    duplicate = dataset.routes[0]
    with pytest.raises(IntegrityError, match="identities must be unique"):
        validate_map_dataset(dataset.model_copy(update={"routes": (*dataset.routes, duplicate)}))
    with pytest.raises(IntegrityError, match="route lookup keys"):
        validate_map_dataset(dataset.model_copy(update={"routes": (
            *dataset.routes, duplicate.model_copy(update={"route_id": "different-id"}))}))

@pytest.mark.parametrize(
    ("route_index", "fare"),
    [
        (0, 0),
        (1, 1),
    ],
)
def test_route_fares_follow_the_catalog_exchange_rule(route_index: int, fare: int) -> None:
    """路线报价偏离目录公式求值时在组装阶段拒绝。"""
    document = LifeScenario.model_validate(_payload())
    dataset = _map(document)
    routes = list(dataset.routes)
    routes[route_index] = routes[route_index].model_copy(update={"fare_fen": fare})
    with pytest.raises(IntegrityError, match="disagrees with the catalog exchange value"):
        assemble_semantics(document, load_catalog(_CATALOG), dataset.model_copy(update={"routes": tuple(routes)}))

def test_route_fare_source_is_closed() -> None:
    """路线费用来源只能标记为场景假设。"""
    payload = _map().routes[0].model_dump()
    payload["fare_source_kind"] = "osrm"
    with pytest.raises(ValidationError, match="Input should be 'scenario'"):
        RouteConfig.model_validate(payload)

def test_catalog_pin_and_reference_admission_are_enforced() -> None:
    """目录版本钉扎与对象引用完整性在组装准入层拒绝。"""
    _assert_assembly_invalid(
        _replace(("catalog_version", "major"), 3),
        "pins catalog",
    )
    _assert_assembly_invalid(
        _replace(("products", 0, "product_id"), "food-0000000000000"),
        "is not in the catalog",
    )
    _assert_invalid(
        _replace(("products", 0, "merchant"), "merchant:general"),
        "Extra inputs are not permitted",
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
        "supports exactly one employer account",
    )


def test_scheduled_records_use_scenario_timezone_and_window() -> None:
    """任务和邮件不会以错误时区或不可到达时刻进入场景。"""
    _assert_invalid(
        _replace(("tasks", 0, "appointment"), {"starts_at": "2026-01-05T09:00:00+00:00",
                                               "reason": "Scheduled review"}),
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
    _assert_invalid(_replace(("locations", 1, "close_hour"), 8), "Extra inputs are not permitted")
    _assert_invalid(_replace(("contents", 0, "kind"), "video"), "subtitle_experience")
    _assert_invalid(_replace(("calendar", "sick_leave_date"), "2026-02-30"), "ISO format")


def test_stock_override_replaces_the_selected_supply_initial_value() -> None:
    """库存覆盖仅作用于指定供给，其他门店同商品库存不变。"""
    payload = _replace(("supplies", 0, "initial_stock"), 7)
    document = LifeScenario.model_validate(payload)
    dataset = _map(document)
    semantics = assemble_semantics(document, load_catalog(_CATALOG), dataset)
    selected = document.supplies[0].supply_id
    assert next(item for item in semantics.scenario.supplies if item.supply_id == selected).initial_stock == 7
    originals = {item.supply_id: item.initial_stock for item in dataset.supplies}
    assert all(item.initial_stock == originals[item.supply_id]
               for item in semantics.scenario.supplies if item.supply_id != selected)


def _scheduled_price(supply_id, version, ordinal):
    """@brief 形成来源完整且位于正式场景窗口内的一次显式调价。"""
    supply = next(item for item in _map().supplies if item.supply_id == supply_id)
    return {"change_id": f"price-{ordinal}", "supply_id": supply_id, "price_version": version,
            "amount_fen": supply.quotes[0].price_fen, "due_at": f"2026-01-05T{10 + ordinal:02d}:00:00+08:00",
            "source": supply.source.model_dump(mode="json")}


def test_scheduled_prices_cannot_return_to_an_earlier_version():
    """同供给的 v2 到 v3 再回 v2 不得重新授权旧报价凭据。"""
    payload = _payload()
    supply = payload["supplies"][0]["supply_id"]
    payload["price_changes"] = [_scheduled_price(supply, version, index)
                                for index, version in enumerate(("v2", "v3", "v2"))]
    _assert_invalid(payload, "price versions must be unique")


@pytest.mark.parametrize("declaration", ["initial", "future"])
def test_scheduled_prices_cannot_reuse_any_declared_quote_version(declaration):
    """显式调价不能复用初始或未来报价声明的版本，即使未来报价尚未生效。"""
    from worldmock.life.map_models import SupplyConfig

    payload = _payload()
    dataset = _map()
    supply = dataset.supplies[0]
    version = supply.quotes[0].price_version
    if declaration == "future":
        original = supply.model_dump(mode="json")
        original["quotes"][0]["effective_to"] = "2026-02-01T00:00:00+08:00"
        original["quotes"].append({**original["quotes"][0], "price_version": "future-v2",
            "effective_from": "2026-02-01T00:00:00+08:00", "effective_to": None})
        supply = SupplyConfig.model_validate(original)
        dataset = dataset.model_copy(update={"supplies": (supply, *dataset.supplies[1:])})
        version = "future-v2"
    payload["price_changes"] = [_scheduled_price(supply.supply_id, version, 0)]
    document = LifeScenario.model_validate(payload)
    with pytest.raises(IntegrityError, match="price versions must be unique"):
        assemble_semantics(document, load_catalog(_CATALOG), dataset)


def test_distinct_supplies_may_use_the_same_local_price_version():
    """报价版本在供给内唯一；不同门店或商品的同名版本相互独立。"""
    payload = _payload()
    payload["price_changes"] = [_scheduled_price(item["supply_id"], "local-v2", index)
                                for index, item in enumerate(payload["supplies"][:2])]
    document = LifeScenario.model_validate(payload)
    result = assemble_semantics(document, load_catalog(_CATALOG), _map(document))
    assert len(result.scenario.price_changes) == 2


@pytest.mark.parametrize("section,reason", [
    ("price_changes", "Scheduled price changes require an explicit scenario source"),
    ("product_releases", "Scheduled supply releases require an explicit scenario source"),
])
def test_scheduled_supply_changes_cannot_claim_retail_observation_sources(section, reason, caplog):
    """@brief 人工调价和补货必须明确为场景假设，不能继承真实零售资料的来源标签。"""
    payload = _payload()
    supply = payload["supplies"][0]["supply_id"]
    price = _scheduled_price(supply, "new-scheduled-price", 0)
    price["source"].update(kind="fact", url="https://retailer.example/observed-price")
    entry = price if section == "price_changes" else {
        "release_id": "new-stock", "supply_id": supply, "stock": 20,
        "due_at": price["due_at"], "source": price["source"]}
    payload[section] = [entry]
    _assert_invalid(payload, reason)
    assert reason in caplog.text


def test_product_use_cannot_require_a_location_outside_the_selected_world():
    """@brief 商品使用场所必须在人物所在的已选择世界内，不能成为无法兑现的使用条件。"""
    runtime, _ = load_scenario(_FORMAL)
    payload = runtime.model_dump(mode="json")
    payload["products"][0]["required_location_id"] = "outside-selected-map"
    with pytest.raises(ValidationError, match="product use requirement references an unknown location"):
        RuntimeScenario.model_validate(payload)


def test_runtime_view_rejects_inverted_product_price_bounds_and_admits_a_fixed_price(caplog) -> None:
    """运行视图里商品的进价上下界不得倒置：正式场景导出的运行视图把一个商品的下界改成比上界高一分后不能重建，唯一的
    校验错误落在该商品上，英文原因写明上下界须有序并留下错误日志；上下界相同的固定价商品照常成立。"""
    from worldmock.life.scenario import ProductConfig

    runtime, _ = load_scenario(_FORMAL)
    payload = runtime.model_dump(mode="json")
    index, product = next((index, item) for index, item in enumerate(payload["products"])
                          if item["purchase_price_fen_min"] < item["purchase_price_fen_max"])
    upper = product["purchase_price_fen_max"]
    fixed = ProductConfig.model_validate({**product, "purchase_price_fen_min": upper})
    assert (fixed.purchase_price_fen_min, fixed.purchase_price_fen_max) == (upper, upper)
    assert "Product purchase price bounds are inverted" not in caplog.text
    product["purchase_price_fen_min"] = upper + 1
    with pytest.raises(ValidationError) as failure:
        RuntimeScenario.model_validate(payload)
    assert [(error["loc"], error["msg"]) for error in failure.value.errors()] == [
        (("products", index), "Value error, product purchase price bounds must be ordered")]
    assert "Product purchase price bounds are inverted" in caplog.text


def _document(path: Path) -> dict:
    """读取一份场景 JSON 文档。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _agent_of(document: dict, agent_id: int) -> dict:
    """按身份取得人物声明。"""
    return next(item for item in document["agents"] if item["agent_id"] == agent_id)


def test_payday_and_rent_are_calendar_opportunities_not_obligations() -> None:
    """场景声明的工资与账单逐项成为后台结算事件，账单不强制人物决策。"""
    document = _document(_FORMAL)
    payrolls = document["payrolls"]
    bills = document["bills"]
    assert payrolls and bills
    scenario, _ = load_scenario(_FORMAL)
    start, end = int(scenario.start_at.timestamp()), int(scenario.end_at.timestamp())
    money_events = [event for event in compile_schedule(scenario, start, end)
                    if event.data.get("payroll_id") or event.data.get("bill_id")]
    assert len(money_events) == len(payrolls) + len(bills)
    assert {event.data["payroll_id"] for event in money_events if event.data.get("payroll_id")} == {
        item["payroll_id"] for item in payrolls}
    assert {event.data["bill_id"] for event in money_events if event.data.get("bill_id")} == {
        item["bill_id"] for item in bills}
    assert all(event.agent_id is None for event in money_events
               if event.data.get("bill_id"))


@pytest.mark.parametrize(
    ("bedtime", "message"),
    [
        # 起点越界：负起点与 ≥24 起点（跨午夜口径只放宽末端，不放宽起点）。
        ((-1.0, 6.0), "0 <= start < 24"),
        ((24.0, 25.5), "0 <= start < 24"),
        # 末端越界：end ≤ start 的退化区间与 end > 30（次日 06:00 上限）。
        ((22.5, 22.5), "start < end <= 30"),
        ((23.0, 30.5), "start < end <= 30"),
        # 跨度病态区间：12.5h 超过 12h 上限（范围检查本身合法）。
        ((10.0, 22.5), "span must not exceed 12 hours"),
        # 压到晚餐窗口：陈曦晚餐 [18.5, 20.0]，就寝起点 19.5 < 20.0。
        ((19.5, 28.0), "must not overlap dinner window"),
    ],
)
def test_bedtime_window_bounds_and_overlap_are_rejected(
    bedtime: tuple[float, float], message: str
) -> None:
    """就寝窗口起点/末端越界、跨度超限与压到晚餐窗口均在文档校验层拒绝。"""
    _assert_invalid(_replace(("agents", 0, "bedtime_window"), list(bedtime)), message)


def test_cross_midnight_bedtime_window_is_accepted() -> None:
    """正例：合法跨午夜就寝窗口（23:30 入睡、末端 25.5=次日 01:30）通过校验。"""
    document = LifeScenario.model_validate(
        _replace(("agents", 0, "bedtime_window"), [23.5, 25.5]))
    assert document.agents[0].bedtime_window == (23.5, 25.5)


@pytest.mark.parametrize(
    ("windows", "message"),
    [
        # 下班、到家窗口与午餐、晚餐同单位同校验：负起点、末端越过 24（不跨午夜）、退化区间各一例。
        ({"leave_work_window": [-0.5, 18.0]}, "leave_work_window must satisfy 0 <= start < end <= 24"),
        ({"leave_work_window": [23.5, 24.5]}, "leave_work_window must satisfy 0 <= start < end <= 24"),
        ({"arrive_home_window": [19.0, 19.0]}, "arrive_home_window must satisfy 0 <= start < end <= 24"),
        ({"arrive_home_window": [23.0, 25.0]}, "arrive_home_window must satisfy 0 <= start < end <= 24"),
        # 两者都声明时下班起点不得晚于到家起点。
        ({"leave_work_window": [19.0, 19.5], "arrive_home_window": [18.5, 20.0]},
         "leave_work_window must not start after arrive_home_window"),
    ],
)
def test_work_window_bounds_and_order_are_rejected(windows: dict, message: str) -> None:
    """场景下班、到家窗口（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）：越界、退化与下班晚于到家在文档校验层拒绝；正例为两者
    都声明且起点相等、与午餐、晚餐、就寝不设先后（下班窗口压在晚餐窗口里也合法），未声明时两字段为 None 且不算已设。"""
    payload = _payload()
    payload["agents"][0].update(windows)
    _assert_invalid(payload, message)
    legal = _payload()
    legal["agents"][0].update(leave_work_window=[18.5, 19.0], arrive_home_window=[18.5, 20.0])
    agent = LifeScenario.model_validate(legal).agents[0]
    assert (agent.leave_work_window, agent.arrive_home_window) == ((18.5, 19.0), (18.5, 20.0))
    assert agent.dinner_window[0] <= agent.leave_work_window[0] < agent.dinner_window[1]
    undeclared = LifeScenario.model_validate(_payload()).agents[0]
    assert (undeclared.leave_work_window, undeclared.arrive_home_window) == (None, None)
    assert {"leave_work_window", "arrive_home_window"}.isdisjoint(undeclared.model_fields_set)


def test_runtime_agents_keep_the_declared_rhythm_fields() -> None:
    """组装后的运行时人物只把场景显式写出的字段记为已设（缺省值照旧由模型补齐，值不变），场景作息覆盖卡片据此只覆盖
    显式声明的作息字段（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值 ）；声明全部五个字段的正式场景五个都记为已设。"""
    rhythm = {"chronotype_midsleep", "sleep_need_hours", "lunch_window", "dinner_window", "bedtime_window"}
    payload = _payload()
    for name in rhythm - {"chronotype_midsleep"}:
        del payload["agents"][0][name]
    document = LifeScenario.model_validate(payload)

    agent = assemble_semantics(document, load_catalog(_CATALOG), _map(document)).scenario.agents[0]

    assert agent.model_fields_set & rhythm == {"chronotype_midsleep"}
    assert (agent.sleep_need_hours, agent.lunch_window, agent.bedtime_window) == (7.5, None, None)
    assert agent.model_dump() == document.agents[0].model_dump()
    declared = LifeScenario.model_validate(_payload())
    assembled = assemble_semantics(declared, load_catalog(_CATALOG), _map(declared)).scenario.agents[0]
    assert assembled.model_fields_set & rhythm == declared.agents[0].model_fields_set & rhythm


def _shift(**fields) -> dict:
    """标准班次（09:00–18:00，午休 12:00–13:30）改写给定字段后的副本。"""
    return {**STANDARD_SHIFT, **fields}


def _weekly(day: str, shift: dict):
    """把每周声明的某一天改成给定班次的改写函数。"""
    return lambda schedule: schedule["weekly"].update({day: shift})


_SHIFT_RANGE = "work shift must satisfy 0 <= starts < ends <= 1440"
_BREAK_INSIDE = "work shift breaks must lie within the shift"
_BREAK_ORDER = "work shift breaks must be ascending and must not overlap"
_SEVEN_DAYS = "work_schedule weekly must declare all seven weekdays"
_ISO_DATES = "work_schedule exception dates must use canonical ISO format"


@pytest.mark.parametrize(("edit", "message"), [
    (_weekly("0", _shift(starts=600, ends=600)), _SHIFT_RANGE),
    (_weekly("1", _shift(starts=1200, ends=1500, breaks=[])), _SHIFT_RANGE),
    (_weekly("2", _shift(breaks=[[500, 600]])), _BREAK_INSIDE),
    (_weekly("3", _shift(breaks=[[720, 810], [780, 840]])), _BREAK_ORDER),
    (_weekly("4", _shift(breaks=[[900, 930], [720, 810]])), _BREAK_ORDER),
    (lambda schedule: schedule["weekly"].pop("6"), _SEVEN_DAYS),
    (lambda schedule: schedule["weekly"].update({"7": None}), _SEVEN_DAYS),
    (lambda schedule: schedule["exceptions"].update({"20260214": None}), _ISO_DATES),
    (lambda schedule: schedule["exceptions"].update({"someday": None}), _ISO_DATES),
    (_weekly("0", _shift(lunch=[720, 810])), "Extra inputs are not permitted"),
    (_weekly("0", {"starts": 540, "ends": 1080}), "Field required"),
], ids=["starts-not-before-ends", "ends-after-midnight", "break-outside-shift", "overlapping-breaks",
        "descending-breaks", "weekly-missing-a-day", "weekly-extra-key", "non-canonical-date", "unparsable-date",
        "unknown-shift-key", "missing-breaks"])
def test_work_schedule_shape_errors_are_rejected(edit, message: str) -> None:
    """班次形状错误留在场景模型的校验器（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：班次须同日且 starts < ends（≤ 1440），
    休息时段落在班次内、升序、互不重叠；weekly 七天全写（周一为 "0"，多一天少一天都拒），例外日期是规范的 ISO 日期（基本
    格式与无法解析的都拒）；班次多出的键与缺少的 breaks 都被拒，不设缺省。每个用例对应两个校验器的一处拒绝。"""
    payload = _payload()
    schedule = weekday_schedule(payload)
    edit(schedule)
    payload["agents"][0]["work_schedule"] = schedule
    _assert_invalid(payload, message)


def test_every_agent_declares_work_schedule_and_structure_four_is_rejected(tmp_path: Path) -> None:
    """`work_schedule` 是人物配置的必填可空字段（4.3，裁决 W3）：删掉该键的文档经场景模型与真实入口
    （parse_scenario_document 与 load_scenario）都被拒、原因点名该字段，写 null 照常加载，合法的工作安排照常加载；场景
    结构版本为 6，键仍在而版本写回 4 的文档经同样两个真实入口按版本拒绝。"""
    missing = _payload()
    del missing["agents"][0]["work_schedule"]
    _assert_invalid(missing, r"agents\.0\.work_schedule\n\s+Field required")
    nullable = _payload()
    nullable["agents"][0]["work_schedule"] = None
    assert LifeScenario.model_validate(nullable).agents[0].work_schedule is None
    declared = _payload()
    declared["agents"][0]["work_schedule"] = weekday_schedule(declared)
    assert LifeScenario.model_validate(declared).agents[0].work_schedule.weekly["5"] is None

    keyless = _mutated_scenario(_FORMAL, lambda document: document["agents"][0].pop("work_schedule"),
                                tmp_path / "no-work-schedule")
    old = _mutated_scenario(_FORMAL, lambda document: document.update(schema_version=4), tmp_path / "structure-4")
    for path, field in ((keyless, "work_schedule"), (old, "schema_version")):
        for entry in (parse_scenario_document, load_scenario):
            with pytest.raises(IntegrityError, match="Scenario validation failed") as failure:
                entry(path)
            assert field in str(failure.value.__cause__)

    path = _mutated_scenario(_FORMAL, lambda document: document["agents"][0].update(work_schedule=None),
                             tmp_path / "null-work-schedule")
    scenario, _ = load_scenario(path)
    assert scenario.schema_version == 6
    assert scenario.agents[0].work_schedule is None


def _scheduled_payload(edit) -> dict:
    """正式场景文档的副本：人物 1 写上与场景日历一致的工作安排，再交给 edit(schedule) 改写。"""
    payload = _payload()
    schedule = weekday_schedule(payload)
    edit(schedule)
    payload["agents"][0]["work_schedule"] = schedule
    return payload


@pytest.mark.parametrize(("shift", "message"), [
    (_shift(starts=-60, ends=540, breaks=[]), _SHIFT_RANGE),
    (_shift(breaks=[[810, 720]]), _BREAK_INSIDE),
    (_shift(breaks=[[720, 720]]), _BREAK_INSIDE),
], ids=["starts-before-midnight", "inverted-break", "empty-break"])
def test_a_shift_starting_before_midnight_and_an_inverted_or_empty_break_are_rejected(shift: dict, message: str):
    """班次是自当地零点起的分钟、落在同一天，休息时段是班次内的半开区间（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ；接口合同
    「工作安排声明」：0 ≤ starts < ends ≤ 1440）：周一的班次从前一天 23:00 开始（starts 为负）时被场景模型拒绝；休息时段
    两端都在班次内而止点早于或等于起点（13:30 到 12:00、12:00 到 12:00）时同样被拒，原因分别是班次范围与休息时段须落在
    班次内。同一份文档不改周一班次时照常通过。"""
    assert LifeScenario.model_validate(_scheduled_payload(lambda schedule: None)).agents[0].work_schedule is not None
    _assert_invalid(_scheduled_payload(_weekly("0", shift)), message)


def test_back_to_back_breaks_are_admitted_and_a_one_minute_overlap_is_not() -> None:
    """休息时段互不重叠按半开区间判定（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：前一段的止点等于后一段的起点不算重叠，
    12:00–13:00 紧接 13:00–13:30 的班次经场景模型照常加载、两段原样保留；后一段早开始一分钟才是重叠，被拒。"""
    adjacent = _scheduled_payload(_weekly("0", _shift(breaks=[[720, 780], [780, 810]])))
    shift = LifeScenario.model_validate(adjacent).agents[0].work_schedule.weekly["0"]
    assert (shift.starts, shift.ends, shift.breaks) == (540, 1080, ((720, 780), (780, 810)))
    _assert_invalid(_scheduled_payload(_weekly("0", _shift(breaks=[[720, 780], [779, 810]]))), _BREAK_ORDER)


@pytest.mark.parametrize("key", ["workplace_location_id", "weekly", "exceptions"])
def test_work_schedule_requires_all_three_keys_without_defaults(key: str) -> None:
    """工作安排的三个键都必填、没有缺省（接口合同「工作安排声明」，裁决 W3）：删掉工作地点、每周声明或日期例外中的任意
    一个键，场景模型都按缺字段拒绝并点名该键；没有日期例外须显式写空对象，写 {} 照常加载。"""
    _assert_invalid(_scheduled_payload(lambda schedule: schedule.pop(key)),
                    rf"agents\.0\.work_schedule\.{key}\n\s+Field required")
    empty = _scheduled_payload(lambda schedule: schedule.update(exceptions={}))
    assert LifeScenario.model_validate(empty).agents[0].work_schedule.exceptions == {}


def test_work_schedule_rejects_an_unknown_key_such_as_a_timezone() -> None:
    """工作安排不带时区字段（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ，复用场景唯一的 timezone），未知键拒绝（接口合同「工作
    安排声明」）：多写 timezone 的安排被场景模型按未知字段拒绝并点名该键，不被悄悄丢弃；不多写时同一份安排照常加载，
    加载出的安排只有三个键。"""
    extra = _scheduled_payload(lambda schedule: schedule.update(timezone="Europe/Berlin"))
    _assert_invalid(extra, r"agents\.0\.work_schedule\.timezone\n\s+Extra inputs are not permitted")
    loaded = LifeScenario.model_validate(_scheduled_payload(lambda schedule: None)).agents[0].work_schedule
    assert set(loaded.model_dump()) == {"workplace_location_id", "weekly", "exceptions"}
