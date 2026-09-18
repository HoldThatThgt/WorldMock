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
from worldmock.scheduling.compile import compile_schedule


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


def test_task_skill_targets_must_be_declared() -> None:
    """任务技能不能引用场景外身份；娱乐偏好条目校验已随随机偏好浮点删除。"""
    _assert_invalid(
        _replace(("tasks", 0, "required_skill"), "missing-skill"),
        "unknown skill",
    )
    # 修订合同 L1661：随机偏好浮点字段整体删除——场景再携带该键按未知字段拒绝。
    payload = _replace(("agents", 0, "entertainment_preferences"), {"missing-content": 0.5})
    _assert_invalid(payload, "Extra inputs are not permitted")


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
        _replace(("catalog_version", "major"), 3),
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


# ---------------------------------------------------------------------------
# 修订合同 v2：人物卡片、联系人、日历发薪/房租、地点品类、正餐与合同分配。
# ---------------------------------------------------------------------------

_V2_SCENARIOS = (
    _FORMAL,
    Path("scenarios/multi-person.json"),
    Path("scenarios/multi-person-half-day.json"),
)
_MULTI_SCENARIOS = _V2_SCENARIOS[1:]

#: 场景地点 kind → 世界素材品类（修订合同 L1635-1647 的闭集映射）。
_KIND_CATEGORY = {
    "home": "home",
    "office": "office",
    "restaurant": "canteen_or_restaurant",
    "convenience_store": "convenience_store",
    "supermarket": "supermarket_or_wet_market",
    "wet_market": "supermarket_or_wet_market",
    "park": "park_or_fitness",
    "fitness": "park_or_fitness",
}
_REQUIRED_CATEGORIES = {
    "home", "office", "canteen_or_restaurant", "convenience_store",
    "supermarket_or_wet_market", "park_or_fitness",
}
#: 7 份早餐储备的语料来源（scene-stock/home-pantry.json）。
_PANTRY_CORPUS = Path("corpora/scene-stock/home-pantry.json")
_LOCATIONS_CORPUS = Path("corpora/zhangjiang-map/locations.json")
_WORK_CORPUS = Path("corpora/scenario-work/materials.json")
#: 人物锚点：李凡晚型、陈曦早型的可复核分界（background_story 事实）。
_LATE_CHRONOTYPE_MIN = 4.5
_EARLY_CHRONOTYPE_MAX = 3.5


def _document(path: Path) -> dict:
    """读取一份场景 JSON 文档。"""
    return json.loads(path.read_text())


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


def test_location_kinds_cover_the_closed_category_set() -> None:
    """三场景地点覆盖修订合同品类闭集，POI 坐标与张江语料逐项一致。"""
    corpus = json.loads(_LOCATIONS_CORPUS.read_text())
    corpus_by_id = {item["location_id"]: item for item in corpus["locations"]}
    for path in _V2_SCENARIOS:
        document = _document(path)
        categories = {_KIND_CATEGORY[item["kind"]] for item in document["locations"]
                      if item["kind"] in _KIND_CATEGORY}
        assert _REQUIRED_CATEGORIES <= categories
        restaurants = [item for item in document["locations"]
                       if item["kind"] == "restaurant"]
        assert len(restaurants) >= 2
        for item in document["locations"]:
            corpus_entry = corpus_by_id.get(item["location_id"])
            # 坐标与名称逐项一致仅对 OSM POI 断言；home/office 为冻结场景锚点，
            # 语料以坐标回指场景（frozen-scenario-anchor），显示名不要求改写。
            if corpus_entry is not None and item["location_id"].startswith("poi-"):
                assert item["latitude"] == corpus_entry["latitude"]
                assert item["longitude"] == corpus_entry["longitude"]
                assert item["name"] == corpus_entry["name"]


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
            assert 1500 <= product.price_fen <= 4500
            assert 20 * 60 <= product.use_duration_seconds <= 40 * 60
            assert product.delivery_delay_seconds > 0
            assert merchants[product.merchant_account_id]["role"] == "merchant:restaurant"


def test_home_pantry_declares_seven_breakfast_servings() -> None:
    """家的初始储备：语料 7 份早餐全部在场景商品闭集内可解析。"""
    pantry = json.loads(_PANTRY_CORPUS.read_text())
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
    corpus = json.loads(_WORK_CORPUS.read_text())
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


def test_three_scenarios_share_the_v2_schema() -> None:
    """三场景同 Schema：版本、重估间隔、钉扎哈希与人物卡片字段一致齐备。"""
    manifests = set()
    for path in _V2_SCENARIOS:
        document = _document(path)
        assert document["schema_version"] >= 2
        assert document["reconsideration_interval_seconds"] == 5400
        manifests.add(document["corpus_manifest_sha256"])
        for agent in document["agents"]:
            for field in ("chronotype_midsleep", "sleep_need_hours",
                          "lunch_window", "dinner_window", "bedtime_window",
                          "contacts"):
                assert field in agent
        scenario, digest = load_scenario(path)
        assert len(digest) == 64
    assert len(manifests) == 1
