"""生活场景文档闭集字段、数值边界、跨引用与目录组装测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.scenario import InventoryItemConfig, LifeScenario, RuntimeScenario, load_scenario
from worldmock.life.map_data import load_map_dataset, validate_map_dataset
from worldmock.life.map_models import MapDataset, RouteConfig
from worldmock.life.opening_hours import quote_at
from worldmock.life.semantics import assemble_semantics
from worldmock.scheduling.compile import compile_schedule
from tests.life.conftest import _mutated_scenario
from tests.life.work_schedule_fixtures import STANDARD_SHIFT, weekday_schedule
from worldmock.life.scenario import parse_scenario_document


_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_CATALOG = Path("catalog")


def _payload() -> dict:
    """读取正式场景为每个反例提供独立对象。"""
    return json.loads(_FORMAL.read_text(encoding="utf-8"))


def _map(document=None):
    """加载与指定场景钉扎一致的完整地图。"""
    document = document or LifeScenario.model_validate(_payload())
    return load_map_dataset(Path("corpora"), document.map_dataset)


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
    """正式与多人场景完整加载：文档校验、目录组装与准入全部通过。"""
    for path in (_FORMAL, Path("scenarios/multi-person.json")):
        scenario, digest = load_scenario(path)
        assert len(digest) == 64
        assert scenario.body.work_notes_multiplier == 1.1
        assert scenario.body.basal_kcal_per_hour == 65
        assert scenario.max_same_moment_decisions == 3
        assert scenario.transport_account_id == "transport:service"
        assert all(route.fare_source_kind == "scenario" for route in scenario.routes)


def test_formal_restaurants_select_edible_pickup_meals() -> None:
    """@brief 正式三场景逐店交叉核验选中供给、商品、初始库存与实际菜单。"""
    restaurants = {"poi-3538660453", "poi-3538682404", "poi-3538682408"}
    paths = (_FORMAL, Path("scenarios/multi-person.json"),
             Path("scenarios/multi-person-half-day.json"))
    for path in paths:
        scenario, _ = load_scenario(path)
        places = {item.location_id: item for item in scenario.locations}
        products = {item.product_id: item for item in scenario.products}
        selected = {item.location_id: item for item in scenario.supplies
                    if item.location_id in restaurants}
        assert set(selected) == restaurants
        for location_id in restaurants:
            place = places[location_id]
            supply = selected[location_id]
            product = products[supply.product_id]
            menu = scenario.interaction_menus_by_location_kind[place.kind]
            assert place.business_kind == "restaurant"
            assert product.category_id.startswith("prepared-meal")
            assert "eat" in product.use_modes and product.consumable
            assert product.required_location_id in (None, location_id)
            assert supply.initial_stock > 0 and "pickup" in supply.fulfillment_modes
            quote = quote_at(supply, int(scenario.start_at.timestamp()))
            assert quote is not None and quote.price_fen > 0
            assert {"purchase", "consume"} <= set(menu.allowed_actions)


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


def test_relief_preference_floor_defaults_to_half_without_changing_scenario_files() -> None:
    """下限偏好是场景层 body 的可选字段（docs/dev/SPEC-dayrun-fidelity.md 1.2，裁决 D3）：正式场景不声明，
    文档与组装后的身体参数都取缺省 0.5；场景结构版本为 5，目录钉扎不变。"""
    for path in (_FORMAL, Path("scenarios/multi-person.json"), Path("scenarios/multi-person-half-day.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "entertainment_relief_preference_floor" not in payload["body"]
        assert LifeScenario.model_validate(payload).body.entertainment_relief_preference_floor == 0.5
        scenario, _ = load_scenario(path)
        assert scenario.schema_version == 5
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
    """docs/EXPERIMENT.md「实验假设声明」恰有一条登记下限偏好 f（docs/dev/SPEC-dayrun-fidelity.md 1.2 验收：f 在场景层字段
    与 docs/EXPERIMENT.md 各登记一次）：取值是场景层字段的缺省值，写明敏感区间、有效偏好公式、取值范围与场景层字段名；
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
    # docs/dev/SPEC-life-realism.md「画像编译」：随机偏好浮点字段整体删除——场景再携带该键按未知字段拒绝。
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


# 修订合同：人物、联系人、日历、独立地图与正餐供给。
_V2_SCENARIOS = (
    _FORMAL,
    Path("scenarios/multi-person.json"),
    Path("scenarios/multi-person-half-day.json"),
)
_MULTI_SCENARIOS = _V2_SCENARIOS[1:]
_REQUIRED_CATEGORIES = {
    "home", "office", "restaurant", "convenience_store", "supermarket", "wet_market", "park", "fitness",
}
_PANTRY_CORPUS = Path("corpora/scene-stock/home-pantry.json")
_LOCATIONS_CORPUS = Path("corpora/zhangjiang-map/locations.json")
_WORK_CORPUS = Path("corpora/scenario-work/materials.json")
_LATE_CHRONOTYPE_MIN = 4.5
_EARLY_CHRONOTYPE_MAX = 3.5

def _document(path: Path) -> dict:
    """读取一份场景 JSON 文档。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _agent_of(document: dict, agent_id: int) -> dict:
    """按身份取得人物声明。"""
    return next(item for item in document["agents"] if item["agent_id"] == agent_id)


def test_persona_declarations_are_complete_and_story_consistent() -> None:
    """每人物声明时型、睡眠需要与餐时窗口，且锚点与 background_story 一致。"""
    for path in _V2_SCENARIOS:
        document = _document(path)
        for agent in document["agents"]:
            assert 0.0 <= agent["chronotype_midsleep"] < 24.0
            assert 7.0 <= agent["sleep_need_hours"] <= 9.0
            lunch, dinner = agent["lunch_window"], agent["dinner_window"]
            assert 0 <= lunch[0] < lunch[1] <= 24
            assert 0 <= dinner[0] < dinner[1] <= 24
            assert lunch[1] <= dinner[0], "午餐与晚餐窗口不得重叠"
            bedtime = agent["bedtime_window"]
            assert 0 <= bedtime[0] < 24 and bedtime[0] < bedtime[1] <= 30, (
                "就寝窗口与画像层 TimeWindow 同口径（跨午夜末端 ≤ 30）")
            assert bedtime[1] - bedtime[0] <= 12, "就寝窗口跨度不得超 12 小时"
            assert dinner[1] <= bedtime[0], "晚餐与就寝窗口不得重叠"
    for path in _MULTI_SCENARIOS:
        document = _document(path)
        chenxi, lifan = _agent_of(document, 1), _agent_of(document, 2)
        # 李凡“经常凌晨一点才睡”→ 晚型；陈曦“每天早上 7 点起床”→ 早型。
        assert "凌晨一点才睡" in lifan["background_story"]
        assert "7点起床" in chenxi["background_story"]
        assert lifan["chronotype_midsleep"] >= _LATE_CHRONOTYPE_MIN
        assert chenxi["chronotype_midsleep"] <= _EARLY_CHRONOTYPE_MAX
        assert lifan["chronotype_midsleep"] > chenxi["chronotype_midsleep"]
        # 陈曦“按时吃午饭和晚饭”，李凡偏晚的加班餐时。
        assert chenxi["lunch_window"][0] < lifan["lunch_window"][0]
        assert chenxi["dinner_window"][0] < lifan["dinner_window"][0]
        # 陈曦“不会为赶 deadline 加班到深夜”→ 当日就寝（末端 23:30，7.5h 睡
        # 眠到 7 点起床）；李凡“经常凌晨一点才睡”→ 跨午夜就寝窗口（末端
        # 25.0=次日 01:00，引擎侧锚点钳制 ≤ 当日结束，声明保持故事真值）。
        assert chenxi["bedtime_window"] == [22.5, 23.5]
        assert lifan["bedtime_window"] == [23.5, 25.0]
        assert chenxi["bedtime_window"][1] < 24 <= lifan["bedtime_window"][1]


def test_contacts_declare_mapping_and_reply_policies() -> None:
    """每人 ≥2 名联系人：同事映射到对方人物，老友/家人为 model 回复场景联系人。"""
    for path in _V2_SCENARIOS:
        document = _document(path)
        for agent in document["agents"]:
            assert len(agent["contacts"]) >= 2
            for contact in agent["contacts"]:
                assert 0 <= contact["closeness"] <= 30
                assert contact["channels"]
                assert contact["salutation"].strip()
    for path in _MULTI_SCENARIOS:
        document = _document(path)
        names = {1: "陈曦", 2: "李凡"}
        for owner_id, counterpart_id in ((1, 2), (2, 1)):
            agent = _agent_of(document, owner_id)
            colleague = next(c for c in agent["contacts"] if c["relation"] == "同事")
            # 同事映射到真实人物：称谓为对方人物，回复来自人物本身（无 NPC 自动回复）。
            assert colleague["salutation"] == names[counterpart_id]
            assert colleague["reply_policy"] == "none"
            others = [c for c in agent["contacts"] if c is not colleague]
            assert others, "老友/家人联系人不得缺席"
            assert all(c["reply_policy"] == "model" for c in others)
            assert all(c["relation"] in ("老友", "家人") for c in others)
    # 单人正式场景没有对方人物可映射：只有 model 回复的场景联系人。
    formal = _document(_FORMAL)
    chenxi = _agent_of(formal, 1)
    assert all(c["reply_policy"] == "model" for c in chenxi["contacts"])
    assert all(c["relation"] in ("老友", "家人") for c in chenxi["contacts"])


def test_payday_and_rent_are_calendar_opportunities_not_obligations() -> None:
    """正式场景补每月 10 日发薪与 5 日房租：后台结算事件，不强制任何人物动作。"""
    document = _document(_FORMAL)
    payrolls = document["payrolls"]
    bills = document["bills"]
    assert [p["due_at"] for p in payrolls] == [
        "2026-01-10T09:00:00+08:00", "2026-02-10T09:00:00+08:00",
        "2026-03-10T09:00:00+08:00"]
    assert [b["due_at"] for b in bills] == [
        "2026-01-05T09:00:00+08:00", "2026-02-05T09:00:00+08:00",
        "2026-03-05T09:00:00+08:00"]
    accounts = {item["account_id"] for item in document["accounts"]}
    assert all(p["employer_account_id"] in accounts for p in payrolls)
    assert all(b["account_id"] == "agent:1" and b["payee_account_id"] in accounts
               for b in bills)
    # 不强制动作：结算走后台预排（账单无决策归属），且不伴随任何合同或邮件义务。
    scenario, _ = load_scenario(_FORMAL)
    start, end = int(scenario.start_at.timestamp()), int(scenario.end_at.timestamp())
    money_events = [event for event in compile_schedule(scenario, start, end)
                    if event.data.get("payroll_id") or event.data.get("bill_id")]
    assert len(money_events) == 6
    assert all(event.agent_id is None for event in money_events
               if event.data.get("bill_id"))
    assert not any("salary" in task["task_id"] or "rent" in task["task_id"]
                   for task in document["tasks"])
    # 变体按各自现状规模：不加发薪与房租预排。
    for path in _MULTI_SCENARIOS:
        variant = _document(path)
        assert variant.get("payrolls", []) == []
        assert variant.get("bills", []) == []


def test_location_business_kinds_cover_all_distinct_required_categories() -> None:
    """三场景共享地图，营业类别分别覆盖，地点属性不再复制进场景。"""
    corpus = json.loads(_LOCATIONS_CORPUS.read_text(encoding="utf-8"))
    corpus_by_id = {item["location_id"]: item for item in corpus["locations"]}
    for path in _V2_SCENARIOS:
        document = _document(path)
        assert all(set(item) == {"location_id"} for item in document["locations"])
        scenario, _ = load_scenario(path)
        assert _REQUIRED_CATEGORIES <= {item.business_kind for item in scenario.locations}
        assert sum(item.business_kind == "restaurant" for item in scenario.locations) >= 2
        for item in scenario.locations:
            original = corpus_by_id[item.location_id]
            assert (item.latitude, item.longitude, item.name) == (
                original["latitude"], original["longitude"], original["name"])
            assert item.location_kinds and "anywhere" not in item.location_kinds

def test_shared_product_has_independent_market_and_store_supplies() -> None:
    """同一商品拥有多个门店供给，地点分别声明现实环境与营业类别。"""
    corpus = json.loads(_LOCATIONS_CORPUS.read_text(encoding="utf-8"))
    market = next(item for item in corpus["locations"] if item["location_id"] == "market")
    assert market["kind"] == "merchant" and market["business_kind"] == "general_retail"
    assert market["location_kinds"] == ["venue"]
    for path in _V2_SCENARIOS:
        scenario, _ = load_scenario(path)
        milk = [item for item in scenario.supplies if item.product_id == "food-6923644266066"]
        assert len(milk) >= 3
        assert len({item.location_id for item in milk}) >= 3
        assert len({item.seller_account_id for item in milk}) >= 3
        assert len({item.quotes[0].price_fen for item in milk}) >= 3
        assert {product.product_id for product in scenario.products} <= {
            item.product_id for item in scenario.supplies}

def test_meal_products_resolve_against_the_catalog() -> None:
    """正餐 ≥30 条：场景引用全部解析为目录 meal-* 对象且窗口合规。"""
    catalog = load_catalog(_CATALOG)
    for path in _V2_SCENARIOS:
        scenario, _ = load_scenario(path)
        meals = [product for product in scenario.products
                 if product.product_id.startswith("meal-")]
        assert len(meals) >= 30
        merchants = {account["account_id"]: account for account in _document(path)["accounts"]}
        for product in meals:
            obj = catalog.objects[product.product_id]
            assert obj.consumable and "eat" in product.use_modes
            supply = next(item for item in scenario.supplies if item.product_id == product.product_id)
            assert 1500 <= supply.quotes[0].price_fen <= 4500
            assert 20 * 60 <= product.use_duration_seconds <= 40 * 60
            assert supply.delivery_delay_seconds >= 0
            assert merchants[supply.seller_account_id]["role"] == "merchant:restaurant"


def test_home_pantry_declares_seven_breakfast_servings() -> None:
    """家的初始储备：语料 7 份早餐全部在场景商品闭集内可解析。"""
    pantry = json.loads(_PANTRY_CORPUS.read_text(encoding="utf-8"))
    items = pantry["items"]
    assert sum(item["quantity"] for item in items) == 7
    catalog = load_catalog(_CATALOG)
    for path in _V2_SCENARIOS:
        scenario, _ = load_scenario(path)
        products = {product.product_id for product in scenario.products}
        for item in items:
            assert item["object_id"] in products
            assert catalog.objects[item["object_id"]].consumable


def test_task_assignments_match_persona_occupations() -> None:
    """合同按人分配：受理人物在场且技能等级覆盖合同门槛。"""
    formal = _document(_FORMAL)
    chenxi = _agent_of(formal, 1)
    assert chenxi["name"] == "陈曦" and "产品经理" in chenxi["background_story"]
    assert all(task["assignee_agent_id"] == "agent:1" for task in formal["tasks"])
    assert all(task["required_skill"] in chenxi["skill_levels"]
               and chenxi["skill_levels"][task["required_skill"]] >= task["minimum_skill"]
               for task in formal["tasks"])
    for path in _MULTI_SCENARIOS:
        document = _document(path)
        skills = {f"agent:{agent['agent_id']}": agent["skill_levels"]
                  for agent in document["agents"]}
        for task in document["tasks"]:
            assert task["assignee_agent_id"] in skills
            levels = skills[task["assignee_agent_id"]]
            assert task["required_skill"] in levels
            assert levels[task["required_skill"]] >= task["minimum_skill"]


def test_work_materials_are_distinct_and_persona_matched() -> None:
    """工作材料 20 份互不重复、按职业面（产品/后端）各 10 份分配到人物。"""
    corpus = json.loads(_WORK_CORPUS.read_text(encoding="utf-8"))
    materials = corpus["materials"]
    assert len(materials) == 20
    assert len({item["material_id"] for item in materials}) == 20
    assert len({item["body"] for item in materials}) == 20
    assert all(item["language"] == "zh" for item in materials)
    per_persona = {}
    for item in materials:
        per_persona[item["target_persona"]] = per_persona.get(item["target_persona"], 0) + 1
    assert per_persona == {"agent:1": 10, "agent:2": 10}
    for path in _MULTI_SCENARIOS:
        agents = {f"agent:{agent['agent_id']}" for agent in _document(path)["agents"]}
        assert set(per_persona) <= agents


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
    """场景下班、到家窗口（docs/dev/SPEC-errands-workday.md 4.2）：越界、退化与下班晚于到家在文档校验层拒绝；正例为两者
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


def test_three_scenarios_share_the_v2_schema() -> None:
    """三场景同 Schema：版本、重估间隔、钉扎哈希与人物卡片字段一致齐备。"""
    manifests = set()
    for path in _V2_SCENARIOS:
        document = _document(path)
        assert document["schema_version"] == 5
        assert document["reconsideration_interval_seconds"] == 5400
        manifests.add(document["corpus_manifest_sha256"])
        for agent in document["agents"]:
            for field in ("chronotype_midsleep", "sleep_need_hours",
                          "lunch_window", "dinner_window", "bedtime_window",
                          "contacts", "work_schedule"):
                assert field in agent
        scenario, digest = load_scenario(path)
        assert len(digest) == 64
    assert len(manifests) == 1


def test_runtime_agents_keep_the_declared_rhythm_fields() -> None:
    """组装后的运行时人物只把场景显式写出的字段记为已设（缺省值照旧由模型补齐，值不变），场景作息覆盖卡片据此只覆盖
    显式声明的作息字段（docs/dev/SPEC-dayrun-fidelity.md 2.1）；声明全部五个字段的正式场景五个都记为已设。"""
    rhythm = {"chronotype_midsleep", "sleep_need_hours", "lunch_window", "dinner_window", "bedtime_window"}
    payload = _payload()
    for name in rhythm - {"chronotype_midsleep"}:
        del payload["agents"][0][name]
    document = LifeScenario.model_validate(payload)

    agent = assemble_semantics(document, load_catalog(_CATALOG), _map(document)).scenario.agents[0]

    assert agent.model_fields_set & rhythm == {"chronotype_midsleep"}
    assert (agent.sleep_need_hours, agent.lunch_window, agent.bedtime_window) == (7.5, None, None)
    assert agent.model_dump() == document.agents[0].model_dump()
    assert load_scenario(_FORMAL)[0].agents[0].model_fields_set & rhythm == rhythm


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
    """班次形状错误留在场景模型的校验器（docs/dev/SPEC-errands-workday.md 4.3）：班次须同日且 starts < ends（≤ 1440），
    休息时段落在班次内、升序、互不重叠；weekly 七天全写（周一为 "0"，多一天少一天都拒），例外日期是规范的 ISO 日期（基本
    格式与无法解析的都拒）；班次多出的键与缺少的 breaks 都被拒，不设缺省。每个用例对应两个校验器的一处拒绝。"""
    payload = _payload()
    schedule = weekday_schedule(payload)
    edit(schedule)
    payload["agents"][0]["work_schedule"] = schedule
    _assert_invalid(payload, message)


def test_every_agent_declares_work_schedule_and_structure_four_is_rejected(tmp_path: Path) -> None:
    """`work_schedule` 是人物配置的必填可空字段（4.3，裁决 W3）：删掉该键即被拒，写 null 照常加载，合法的工作安排照常
    加载；场景结构升为 5，键仍在而版本写回 4 的文档经真实入口（parse_scenario_document 与 load_scenario）按版本拒绝；
    仓库三份场景的每个人物都显式写出 null。"""
    missing = _payload()
    del missing["agents"][0]["work_schedule"]
    _assert_invalid(missing, r"agents\.0\.work_schedule\n\s+Field required")
    assert LifeScenario.model_validate(_payload()).agents[0].work_schedule is None
    declared = _payload()
    declared["agents"][0]["work_schedule"] = weekday_schedule(declared)
    assert LifeScenario.model_validate(declared).agents[0].work_schedule.weekly["5"] is None

    old = _mutated_scenario(_FORMAL, lambda document: document.update(schema_version=4), tmp_path / "structure-4")
    for entry in (parse_scenario_document, load_scenario):
        with pytest.raises(IntegrityError, match="Scenario validation failed") as failure:
            entry(old)
        assert "schema_version" in str(failure.value.__cause__)

    for path in _V2_SCENARIOS:
        assert all(agent["work_schedule"] is None for agent in _document(path)["agents"])
        scenario, _ = load_scenario(path)
        assert scenario.schema_version == 5
        assert scenario.agents and all(agent.work_schedule is None for agent in scenario.agents)
