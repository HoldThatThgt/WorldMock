"""@brief 场景语义组装器：引擎与冻结目录之间的唯一通道。

从 load_catalog(catalog_dir) 的目录视图与瘦身场景文档（LifeScenario）组装出
引擎运行所需的一切静态语义：商品（价格/热量/交付延迟/使用时长/耐用品效果）、
身体参数（阈值→告警码、MET、基础代谢、效率曲线）、出行费率公式求值与三种
合同模板的 convert 公式求值。引擎不直接接触目录，也不从场景文档读取静态语义。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from worldmock.catalog import (
    Catalog,
    CatalogError,
    ConvertEdge,
    ExchangeObject,
    FormulaBinding,
    ObjectKind,
    ResourceDefinition,
    ScenarioAccountRef,
    ScenarioCatalogRefs,
    ScenarioObjectRef,
    ScenarioResourceRef,
    ScenarioScheduleRef,
    ScheduleRefKind,
    SourceEdge,
    TransferEdge,
    load_catalog,
    validate_scenario_against_catalog,
)
from worldmock.errors import IntegrityError
from worldmock.life.models import ContractKind, CurvePoint, EfficiencyFactor
from worldmock.life.scenario import (
    AccountInstanceConfig,
    BodyConfig,
    LifeScenario,
    ProductConfig,
    RuntimeScenario,
    parse_scenario_document,
)
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.events import ScheduledKind

#: 目录内固定身份：四类资源、出行服务与三种合同模板。
MONEY_RESOURCE = "money_fen"
ENERGY_RESOURCE = "energy_kcal"
SLEEP_RESOURCE = "sleep_debt_hours"
ENTERTAINMENT_RESOURCE = "entertainment_need"
TRAVEL_OBJECT = "travel-service"
TRAVEL_TIME_FORMULA = "travel-time"
#: travel-time 系数的合法区间：自由流为下界 1.0，上界防止坏数据放大时长。
TRAVEL_TIME_FACTOR_BOUNDS = (1.0, 5.0)
CONTRACT_OBJECTS = {
    ContractKind.HOURLY: "contract-hourly",
    ContractKind.PIECE: "contract-piece",
    ContractKind.PACKAGE: "contract-package",
}

#: 能量告警码：目录大写 → 引擎阈值参数名（引擎现行告警为小写形式）。
ALERT_THRESHOLDS = (("HUNGRY", "hunger_threshold_kcal"), ("EMERGENCY_ENERGY", "emergency_threshold_kcal"))

#: 人物账户隐式扮演的能力角色；准入层绑定到首个人物账户做类型级校验。
CONTEXTUAL_AGENT_ROLES = ("buyer", "traveler", "worker")

#: 外部锚点账户角色前缀：不参与运行账本，只吸收注入与销毁。
EXTERNAL_ROLE_PREFIX = "external:"

#: 引擎会按键读取的封闭 MET 键集合（body 结算按活动名查表）。
ENGINE_ACTIVITY_MET_KEYS = frozenset({
    "wait", "sleep", "work", "watch", "play_game", "travel:driving", "travel:walking",
    "read_mail", "browse", "consume",
})
ENGINE_RELIEF_KEYS = frozenset({"watch", "play_game"})


@dataclass(frozen=True)
class ScenarioSemantics:
    """@brief 瘦身场景 + 冻结目录组装出的全部静态语义与运行时场景。"""

    document: LifeScenario
    catalog: Catalog
    scenario: RuntimeScenario
    body: BodyConfig
    refs: ScenarioCatalogRefs
    accounts: Mapping[str, int]
    alert_codes: Mapping[str, str] = field(default_factory=dict)

    def travel_fare(self, mode: str, distance_meters: float) -> int:
        """@brief 按 travel-fare 公式分支求值出行费率（分）。"""
        formula = self.catalog.formulas.get("travel-fare")
        if formula is None or mode not in formula.branches:
            raise IntegrityError(f"travel-fare formula has no branch for mode {mode}")
        amount = formula.branches[mode].amount
        if isinstance(amount, int):
            return amount
        return evaluate_int_expression(amount, {"m": distance_meters})

    def travel_time_factor(self, mode: str, departure_second: int) -> float:
        """@brief 按 travel-time 公式分支查出发时刻的分时系数。

        查表键为场景时区的出发小时：walking 固定 1.0（自由流步行已合理），
        driving 走 driving_HH 分支（HH 为两位小时，00-23 工作日 profile）。
        """
        formula = self.catalog.formulas.get(TRAVEL_TIME_FORMULA)
        if formula is None:
            raise IntegrityError(
                f"{TRAVEL_TIME_FORMULA} formula is not declared in the catalog")
        hour = datetime.fromtimestamp(
            departure_second, ZoneInfo(self.document.timezone)).hour
        branch = "walking" if mode == "walking" else f"{mode}_{hour:02d}"
        if branch not in formula.branches:
            raise IntegrityError(f"travel-time formula has no branch for {branch}")
        amount = formula.branches[branch].amount
        try:
            factor = float(amount)
        except (TypeError, ValueError) as error:
            raise IntegrityError(f"travel-time branch {branch} is not numeric") from error
        low, high = TRAVEL_TIME_FACTOR_BOUNDS
        if not low <= factor <= high:
            raise IntegrityError(
                f"travel-time branch {branch} factor {factor} is outside [{low}, {high}]")
        return factor

    def travel_planned_seconds(self, mode: str, base_seconds: int,
                               departure_second: int) -> int:
        """@brief 计划时长 = 冻结自由流时长 × factor(出发时刻)，向上取整。

        用 Decimal 精确乘法后 ROUND_CEILING，避免浮点误差让可复现的
        报价在边界值上抖动。
        """
        factor = Decimal(str(self.travel_time_factor(mode, departure_second)))
        return int((factor * int(base_seconds)).to_integral_value(rounding=ROUND_CEILING))

    def with_price(self, product_id: str, amount_fen: int, price_version: str) -> "ScenarioSemantics":
        """@brief 受控价格替换：返回单个商品 transfer 边金额更新后的新语义。

        PRICE_CHANGE 到点由引擎换持返回值；price_version 必须同步变化，否则
        购买侧 PRICE_VERSION_MISMATCH 守卫无法区分新旧报价观察。
        """
        if product_id not in {item.product_id for item in self.scenario.products}:
            raise IntegrityError(f"price change references unknown product {product_id}")
        if amount_fen <= 0:
            raise IntegrityError("price change amount must be positive")
        products = tuple(
            item if item.product_id != product_id else
            item.model_copy(update={"price_fen": amount_fen, "price_version": price_version})
            for item in self.scenario.products)
        return replace(self, scenario=self.scenario.model_copy(update={"products": products}, deep=True))

    def contract_payment(self, task, worked_seconds: int, piece_count: int | None) -> int:
        """@brief 按合同模板 convert 公式求值整数分付款。"""
        formula = self._contract_formula(task.contract_kind)
        variables = {
            "pay_fen": task.pay_fen,
            "worked_seconds": worked_seconds,
            "maximum_duration_seconds": task.maximum_duration_seconds,
        }
        if "piece_count" in _free_names(formula):
            if piece_count is None:
                raise IntegrityError("piece contract payment requires an oracle piece count")
            variables["piece_count"] = piece_count
        return evaluate_int_expression(formula, variables)

    def _contract_formula(self, kind: ContractKind) -> str:
        """@brief 取得某合同模板 settle 边的 convert 公式文本。"""
        obj = self.catalog.objects.get(CONTRACT_OBJECTS[kind])
        if obj is None:
            raise IntegrityError(f"contract template {CONTRACT_OBJECTS[kind]} is not in the catalog")
        edges = [edge for edge in obj.edges if isinstance(edge, ConvertEdge) and edge.on == "settle"]
        if len(edges) != 1 or edges[0].formula is None:
            raise IntegrityError(f"contract template {obj.object_id} has no settle formula")
        return strip_formula_annotation(edges[0].formula)


def load_runtime_scenario(
    scenario_path: Path, catalog_dir: Path | None = None,
) -> tuple[ScenarioSemantics, str]:
    """@brief 完整加载：文档校验、目录组装、路线费率核验与目录准入。

    :param scenario_path: 瘦身场景 JSON 路径。
    :param catalog_dir: 目录根；缺省取场景兄弟目录 catalog/。
    :returns: 组装结果与场景文件 SHA-256。
    :raises IntegrityError: 文档、目录、组装或准入任一环节失败。
    """
    document, digest = parse_scenario_document(scenario_path)
    directory = Path(catalog_dir) if catalog_dir is not None else resolve_catalog_dir(scenario_path)
    try:
        catalog = load_catalog(directory)
    except CatalogError as error:
        raise IntegrityError(f"Catalog load failed: {error}") from error
    return assemble_semantics(document, catalog), digest


def resolve_catalog_dir(scenario_path: Path) -> Path:
    """@brief 场景文件的默认目录位置：scenarios/ 的兄弟目录 catalog/。"""
    return Path(scenario_path).resolve().parent.parent / "catalog"


def assemble_semantics(document: LifeScenario, catalog: Catalog) -> ScenarioSemantics:
    """@brief 组装运行时场景并执行目录准入（七条规则 fail-closed）。"""
    pinned = (document.catalog_version.major, document.catalog_version.minor)
    loaded = (catalog.version_major, catalog.version_minor)
    if pinned != loaded:
        raise IntegrityError(
            f"Scenario pins catalog {pinned[0]}.{pinned[1]} but loaded {loaded[0]}.{loaded[1]}")
    body = _assemble_body(document, catalog)
    products = _assemble_products(document, catalog)
    services = _service_accounts(document)
    employer_id = _role_account(services, _contract_role(catalog, "from"))
    transport_id = _role_account(services, _travel_role(catalog))
    scenario = _build_runtime(document, body, products, employer_id, transport_id)
    semantics = ScenarioSemantics(
        document=document, catalog=catalog, scenario=scenario, body=body,
        refs=_catalog_refs(document, catalog, scenario),
        accounts=_ledger_accounts(document, services),
        alert_codes={rule.code: rule.code.lower()
                     for resource in (catalog.resources[ENERGY_RESOURCE],)
                     for rule in resource.alert_rules},
    )
    _check_route_fares(semantics)
    _check_travel_time_formula(semantics)
    result = validate_scenario_against_catalog(semantics.refs, catalog)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"Scenario admission rejected by catalog: {detail}")
    return semantics


def _assemble_body(document: LifeScenario, catalog: Catalog) -> BodyConfig:
    """@brief 从三个 level 资源与合同效率参数组装身体运行配置。"""
    energy = _resource(catalog, ENERGY_RESOURCE)
    sleep = _resource(catalog, SLEEP_RESOURCE)
    entertainment = _resource(catalog, ENTERTAINMENT_RESOURCE)
    energy_rules = _drain_rules(energy)
    sleep_rules = _drain_rules(sleep)
    fun_rules = _drain_rules(entertainment)
    thresholds = _alert_thresholds(energy)
    instance = document.body
    maximum = energy.bounds.max if energy.bounds else None
    if maximum is None:
        raise IntegrityError(f"catalog resource {ENERGY_RESOURCE} must declare an upper bound")
    return BodyConfig.model_validate({
        "mass_kg": instance.mass_kg,
        "initial_energy_kcal": instance.initial_energy_kcal,
        "maximum_energy_kcal": maximum,
        "initial_sleep_debt_hours": instance.initial_sleep_debt_hours,
        "basal_kcal_per_hour": _positive(energy_rules.basal_kcal_per_hour, "basal_kcal_per_hour"),
        "awake_sleep_debt_per_hour": _positive(sleep_rules.awake_sleep_debt_per_hour,
                                               "awake_sleep_debt_per_hour"),
        "sleep_recovery_per_hour": _positive(sleep_rules.sleep_recovery_per_hour,
                                             "sleep_recovery_per_hour"),
        "entertainment_need_per_awake_hour": _unit_value(
            fun_rules.entertainment_need_per_awake_hour, "entertainment_need_per_awake_hour"),
        "hunger_threshold_kcal": thresholds["hunger_threshold_kcal"],
        "emergency_threshold_kcal": thresholds["emergency_threshold_kcal"],
        "work_efficiency_factors": _contract_work_efficiency_factors(catalog),
        "food_efficiency_curve": _curve(energy_rules.food_efficiency_curve, "food_efficiency_curve"),
        "activity_met": _closed_map(energy_rules.activity_met, ENGINE_ACTIVITY_MET_KEYS, "activity MET"),
        "entertainment_relief_per_hour": _closed_map(
            fun_rules.entertainment_relief_per_hour, ENGINE_RELIEF_KEYS, "entertainment relief"),
        "work_notes_multiplier": _contract_efficiency(catalog, "work_notes_multiplier"),
        "source_kind": f"catalog:{catalog.version_major}.{catalog.version_minor}",
    })


def _assemble_products(document: LifeScenario, catalog: Catalog) -> tuple[ProductConfig, ...]:
    """@brief 逐个商品引用解析目录对象边并绑定为运行时商品。"""
    role_index = {item.role: item for item in document.accounts}
    products = []
    for reference in document.products:
        obj = catalog.objects.get(reference.product_id)
        if obj is None:
            raise IntegrityError(f"scenario product {reference.product_id} is not in the catalog")
        if obj.object_kind != ObjectKind.PRODUCT:
            raise IntegrityError(f"catalog object {obj.object_id} is not a product")
        products.append(_product_payload(reference, obj, role_index))
    return tuple(products)


def _product_payload(
    reference, obj: ExchangeObject, role_index: Mapping[str, AccountInstanceConfig],
) -> ProductConfig:
    """@brief 从对象边与字段解析单个商品的全部静态语义。"""
    purchase = _purchase_edge(obj)
    consume = _consume_edge(obj)
    binding = role_index.get(reference.merchant)
    if binding is None or purchase.account_roles.to_role != binding.role:
        raise IntegrityError(
            f"product {reference.product_id} merchant {reference.merchant} does not bind"
            f" catalog role {purchase.account_roles.to_role}")
    if obj.stock is None and reference.stock is None:
        raise IntegrityError(f"product {reference.product_id} has no stock source")
    payload = {
        "product_id": obj.object_id,
        "name": obj.name,
        "merchant_account_id": reference.merchant,
        "merchant_location_id": reference.merchant_location_id,
        "price_fen": _literal_fen(purchase.amount_fen, obj.object_id),
        "price_version": f"catalog:{obj.object_id}:v{obj.version}",
        "initial_stock": reference.stock if reference.stock is not None else obj.stock.initial,
        "energy_kcal_per_unit": consume.amount if consume is not None else 0,
        "use_duration_seconds": _positive(obj.use_duration_seconds, "use_duration_seconds"),
        "use_modes": obj.use_modes,
        "consumable": obj.consumable,
        "effect": obj.legacy_effect or obj.effect,
        "required_location_id": obj.required_location_id,
        "delivery_delay_seconds": _positive(obj.delivery_delay_seconds, "delivery_delay_seconds"),
        "source_kind": f"catalog:{obj.object_id}:v{obj.version}",
    }
    if obj.serving is not None:
        payload.update(serving_quantity=obj.serving.quantity, serving_unit=obj.serving.unit)
    return ProductConfig.model_validate(payload)


def _build_runtime(
    document: LifeScenario, body: BodyConfig, products: tuple[ProductConfig, ...],
    employer_id: str, transport_id: str,
) -> RuntimeScenario:
    """@brief 用组装好的语义构造运行时场景并触发其全部跨引用校验。"""
    try:
        return _runtime_payload(document, body, products, employer_id, transport_id)
    except Exception as error:
        raise IntegrityError(f"Scenario assembly failed: {error}") from error


def _runtime_payload(
    document: LifeScenario, body: BodyConfig, products: tuple[ProductConfig, ...],
    employer_id: str, transport_id: str,
) -> RuntimeScenario:
    """@brief 构造运行时场景的载荷字典并执行模型校验。"""
    return RuntimeScenario.model_validate({
        "schema_version": 1,
        "scenario_id": document.scenario_id,
        "timezone": document.timezone,
        "start_at": document.start_at,
        "end_at": document.end_at,
        "random_seed": document.random_seed,
        "activity_feedback_seconds": document.activity_feedback_seconds,
        "max_consecutive_rejections": document.max_consecutive_rejections,
        "corpus_root": document.corpus_root,
        "corpus_manifest_sha256": document.corpus_manifest_sha256,
        "body": body.model_dump(mode="python"),
        "agents": [item.model_dump(mode="python") for item in document.agents],
        "locations": [item.model_dump(mode="python") for item in document.locations],
        "routes": [item.model_dump(mode="python") for item in document.routes],
        "products": [item.model_dump(mode="python") for item in products],
        "tasks": [item.model_dump(mode="python") for item in document.tasks],
        "messages": [item.model_dump(mode="python") for item in document.messages],
        "contents": [item.model_dump(mode="python") for item in document.contents],
        "games": [item.model_dump(mode="python") for item in document.games],
        "employer_account_id": employer_id,
        "transport_account_id": transport_id,
        "calendar": document.calendar.model_dump(mode="python"),
        "bills": [item.model_dump(mode="python") for item in document.bills],
        "payrolls": [item.model_dump(mode="python") for item in document.payrolls],
        "price_changes": [item.model_dump(mode="python") for item in document.price_changes],
        "product_releases": [item.model_dump(mode="python") for item in document.product_releases],
    })


def _catalog_refs(document: LifeScenario, catalog: Catalog,
                  scenario: RuntimeScenario) -> ScenarioCatalogRefs:
    """@brief 组装目录准入引用闭集：资源初值、账户实例、对象与预排表引用。"""
    money = _resource(catalog, MONEY_RESOURCE)
    unit_index = {name: _resource(catalog, name).unit
                  for name in (ENERGY_RESOURCE, SLEEP_RESOURCE, ENTERTAINMENT_RESOURCE)}
    instance = document.body
    resources = (
        (ENERGY_RESOURCE, instance.initial_energy_kcal),
        (SLEEP_RESOURCE, instance.initial_sleep_debt_hours),
        (ENTERTAINMENT_RESOURCE, instance.initial_entertainment_need),
    )
    admitted = [ScenarioResourceRef(
        resource_id=name, initial_value=value, unit=unit_index[name])
        for name, value in resources]
    accounts = [
        ScenarioAccountRef(account_id=item.account_id, resource_id=money.resource_id,
                           role=item.role, initial_value=item.money_fen, unit=money.unit)
        for item in document.accounts
    ]
    agents = sorted(document.agents, key=lambda item: item.agent_id)
    if agents:
        first = agents[0]
        accounts.extend(
            ScenarioAccountRef(account_id=f"agent:{first.agent_id}", resource_id=money.resource_id,
                               role=role, initial_value=first.initial_balance_fen, unit=money.unit)
            for role in CONTEXTUAL_AGENT_ROLES)
    objects = [ScenarioObjectRef(object_id=item.product_id) for item in document.products]
    objects.append(ScenarioObjectRef(object_id=TRAVEL_OBJECT))
    objects.extend(ScenarioObjectRef(object_id=CONTRACT_OBJECTS[kind])
                   for kind in {task.contract_kind for task in document.tasks})
    return ScenarioCatalogRefs(
        catalog_major=document.catalog_version.major,
        catalog_minor=document.catalog_version.minor,
        resources=tuple(admitted),
        accounts=tuple(accounts),
        objects=tuple(objects),
        schedule=_schedule_refs(scenario, catalog),
    )


#: 预排表引用只覆盖 §7.2 五种新事件：邮件/任务/配送由场景文档校验器负责。
_SCHEDULE_REF_KINDS = frozenset({
    ScheduledKind.CALENDAR_FLAG, ScheduledKind.BILL_DUE, ScheduledKind.PAYROLL,
    ScheduledKind.PRICE_CHANGE, ScheduledKind.PRODUCT_RELEASE})


def _schedule_refs(scenario: RuntimeScenario, catalog: Catalog) -> tuple[ScenarioScheduleRef, ...]:
    """@brief 从编译后的预排表提取新事件的准入引用（账户与对象版本钉扎）。"""
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    refs = []
    for item in compile_schedule(scenario, start, end):
        if item.kind not in _SCHEDULE_REF_KINDS:
            continue
        account_id = object_id = object_version = None
        if item.kind == ScheduledKind.BILL_DUE:
            account_id = item.data["account_id"]
        elif item.kind == ScheduledKind.PAYROLL:
            account_id = item.data["employer_account_id"]
        elif item.kind == ScheduledKind.PRICE_CHANGE:
            object_id = item.data["object_id"]
            object_version = item.data["object_version"]
        elif item.kind == ScheduledKind.PRODUCT_RELEASE:
            object_id = item.data["object_id"]
            entry = catalog.objects.get(object_id)
            object_version = entry.version if entry is not None else None
        refs.append(ScenarioScheduleRef(kind=ScheduleRefKind(item.kind.value),
                                        due_at=item.due_at, sequence=item.sequence,
                                        object_id=object_id, object_version=object_version,
                                        account_id=account_id))
    return tuple(refs)


def _ledger_accounts(
    document: LifeScenario, services: Mapping[str, AccountInstanceConfig],
) -> dict[str, int]:
    """@brief 运行账本账户集合：服务账户加人物账户，外部锚点除外。"""
    accounts = {item.account_id: item.money_fen for item in services.values()
                if not item.role.startswith(EXTERNAL_ROLE_PREFIX)}
    for agent in document.agents:
        accounts[f"agent:{agent.agent_id}"] = agent.initial_balance_fen
    return accounts


def _check_route_fares(semantics: ScenarioSemantics) -> None:
    """@brief 每条冻结路线报价必须与目录公式分支求值一致。"""
    for route in semantics.document.routes:
        expected = semantics.travel_fare(route.mode.value, route.distance_meters)
        if route.fare_fen != expected:
            raise IntegrityError(
                f"Route {route.route_id} fare {route.fare_fen} disagrees with the"
                f" catalog formula value {expected}")


def _check_travel_time_formula(semantics: ScenarioSemantics) -> None:
    """@brief travel-time 公式必须在场且形状完整（存在性准入，缺公式即拒绝）。

    形状合同：walking 分支恒为 1.0；driving 覆盖 00-23 全部 24 个小时分支，
    每个分支都是区间内的数值。场景时区取文档声明。
    """
    formula = semantics.catalog.formulas.get(TRAVEL_TIME_FORMULA)
    if formula is None:
        raise IntegrityError(
            f"{TRAVEL_TIME_FORMULA} formula is not declared in the catalog")
    if "walking" not in formula.branches:
        raise IntegrityError("travel-time formula has no walking branch")
    probe = datetime.fromtimestamp(
        0, ZoneInfo(semantics.document.timezone)).replace(hour=12)
    if semantics.travel_time_factor("walking", int(probe.timestamp())) != 1.0:
        raise IntegrityError("travel-time walking branch must be the constant 1.0")
    for hour in range(24):
        branch = f"driving_{hour:02d}"
        if branch not in formula.branches:
            raise IntegrityError(f"travel-time formula has no branch for {branch}")
        semantics.travel_time_factor("driving", int(
            probe.replace(hour=hour).timestamp()))


def _service_accounts(document: LifeScenario) -> dict[str, AccountInstanceConfig]:
    """@brief 以账户 id 为键索引场景声明的服务侧账户实例。"""
    return {item.account_id: item for item in document.accounts}


def _role_account(services: Mapping[str, AccountInstanceConfig], role: str) -> str:
    """@brief 按目录角色解析具体账户 id。"""
    matches = [item.account_id for item in services.values() if item.role == role]
    if len(matches) != 1:
        raise IntegrityError(f"catalog role {role} must bind exactly one scenario account")
    return matches[0]


def _travel_role(catalog: Catalog) -> str:
    """@brief 从 travel-service 对象的 depart 边解析收款方角色。"""
    obj = catalog.objects.get(TRAVEL_OBJECT)
    if obj is None:
        raise IntegrityError(f"catalog object {TRAVEL_OBJECT} is missing")
    edges = [edge for edge in obj.edges if isinstance(edge, TransferEdge) and edge.on == "depart"]
    if len(edges) != 1 or edges[0].account_roles is None:
        raise IntegrityError(f"catalog object {TRAVEL_OBJECT} has no role-annotated depart edge")
    return edges[0].account_roles.to_role


def _contract_role(catalog: Catalog, direction: str) -> str:
    """@brief 从合同模板 settle 边解析雇主或工人角色。"""
    obj = catalog.objects.get("contract-package")
    if obj is None:
        raise IntegrityError("catalog object contract-package is missing")
    edges = [edge for edge in obj.edges if isinstance(edge, ConvertEdge)]
    if len(edges) != 1 or edges[0].account_roles is None:
        raise IntegrityError("contract-package has no role-annotated settle edge")
    roles = edges[0].account_roles
    return roles.from_role if direction == "from" else roles.to_role


def _contract_efficiency(catalog: Catalog, attribute: str):
    """@brief 从三种合同模板读取一致的标量效率参数（如笔记倍率）。"""
    values = []
    for object_id in CONTRACT_OBJECTS.values():
        obj = catalog.objects.get(object_id)
        if obj is None or obj.efficiency is None:
            raise IntegrityError(f"contract template {object_id} has no efficiency params")
        values.append(getattr(obj.efficiency, attribute))
    if any(value != values[0] for value in values):
        raise IntegrityError(f"contract templates disagree on {attribute}")
    return values[0]


def _contract_work_efficiency_factors(catalog: Catalog) -> tuple[EfficiencyFactor, ...]:
    """@brief 从三种合同模板读取一致的工作效率因子列表并转为引擎配置。"""
    values = []
    for object_id in CONTRACT_OBJECTS.values():
        obj = catalog.objects.get(object_id)
        if obj is None or obj.efficiency is None:
            raise IntegrityError(f"contract template {object_id} has no efficiency params")
        values.append(obj.efficiency.work_efficiency_factors)
    if any(value != values[0] for value in values):
        raise IntegrityError("contract templates disagree on work_efficiency_factors")
    return tuple(
        EfficiencyFactor(
            input_resource=factor.input_resource,
            curve=_curve(factor.curve, f"work_efficiency_factors[{factor.input_resource}]"))
        for factor in values[0])


def _purchase_edge(obj: ExchangeObject) -> TransferEdge:
    """@brief 取得商品对象的 purchase 转账边。"""
    edges = [edge for edge in obj.edges if isinstance(edge, TransferEdge) and edge.on == "purchase"]
    if len(edges) != 1 or edges[0].account_roles is None:
        raise IntegrityError(f"product {obj.object_id} has no role-annotated purchase edge")
    return edges[0]


def _consume_edge(obj: ExchangeObject) -> SourceEdge | None:
    """@brief 取得商品对象的 consume 能量边（耐用品没有）。"""
    edges = [edge for edge in obj.edges if isinstance(edge, SourceEdge) and edge.on == "consume"]
    if len(edges) > 1:
        raise IntegrityError(f"product {obj.object_id} has multiple consume edges")
    return edges[0] if edges else None


def _literal_fen(amount: int | FormulaBinding, object_id: str) -> int:
    """@brief 静态价格必须是字面金额；公式定价需要预排事件，当前不支持。"""
    if isinstance(amount, FormulaBinding):
        raise IntegrityError(f"product {object_id} uses a dynamic price formula")
    return amount


def _resource(catalog: Catalog, resource_id: str) -> ResourceDefinition:
    """@brief 取得一个必须存在的目录资源定义。"""
    resource = catalog.resources.get(resource_id)
    if resource is None:
        raise IntegrityError(f"catalog resource {resource_id} is missing")
    return resource


def _drain_rules(resource: ResourceDefinition):
    """@brief 取得 level 资源的动态参数表。"""
    if resource.drain_rules is None:
        raise IntegrityError(f"catalog resource {resource.resource_id} has no drain rules")
    return resource.drain_rules


def _alert_thresholds(energy: ResourceDefinition) -> dict[str, float]:
    """@brief 按告警码映射把目录 alert_rules 解析为引擎阈值参数。"""
    thresholds: dict[str, float] = {}
    for code, parameter in ALERT_THRESHOLDS:
        rules = [rule for rule in energy.alert_rules
                 if rule.code.lower() == code.lower() and rule.below is not None]
        if len(rules) != 1:
            raise IntegrityError(f"catalog resource {energy.resource_id} needs one {code} alert rule")
        thresholds[parameter] = rules[0].below
    return thresholds


def _positive(value, label: str):
    """@brief 校验正数参数存在。"""
    if value is None or value <= 0:
        raise IntegrityError(f"catalog parameter {label} must be positive")
    return value


def _unit_value(value, label: str):
    """@brief 校验零到一参数存在。"""
    if value is None or not 0 <= value <= 1:
        raise IntegrityError(f"catalog parameter {label} must be within the unit interval")
    return value


def _closed_map(values, expected: frozenset[str], label: str) -> dict[str, float]:
    """@brief 校验封闭键集映射与引擎活动名一致且数值为正。"""
    if values is None or set(values) != expected:
        raise IntegrityError(f"catalog {label} keys must match the engine activity set")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in values.values()):
        raise IntegrityError(f"catalog {label} values must be positive")
    return dict(values)


def _curve(points, label: str) -> tuple[CurvePoint, ...]:
    """@brief 把目录曲线点转换为引擎曲线模型。"""
    if not points:
        raise IntegrityError(f"catalog curve {label} is empty")
    return tuple(CurvePoint(input_value=point.input_value, output_value=point.output_value)
                 for point in points)


#: ---------------------------------------------------------------------------
#: 公式安全求值：递归下降解析四则、整除、min/max/ceil/floor 与命名变量。
#: ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\s*(\d+(?:\.\d+)?|//|[A-Za-z_][A-Za-z_0-9]*|[()+\-*/,])")
_ANNOTATION = re.compile(r"\s+\([^()]*\)\s*$")
_FUNCTIONS = {"min": min, "max": max, "ceil": math.ceil, "floor": math.floor}


def strip_formula_annotation(formula: str) -> str:
    """@brief 去掉公式文本末尾的文档性括注（如 " (oracle piece_count)"）。

    括注以空白开头；函数调用（min/max/ceil）的括号紧跟标识符，不会被剥离。
    """
    text = formula.strip()
    while True:
        stripped = _ANNOTATION.sub("", text)
        if stripped == text or not stripped.strip():
            return text.strip()
        text = stripped.strip()


def _free_names(formula: str) -> set[str]:
    """@brief 列出公式中出现的全部标识符。"""
    return {token for token in _TOKEN.findall(formula)
            if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", token)}


def evaluate_int_expression(expression: str, variables: Mapping[str, float]) -> int:
    """@brief 安全求值整数表达式，返回 int 结果。

    只支持字面量、命名变量、+ - * / //、括号、逗号与 min/max/ceil/floor；
    任何未知记号、未知变量或非整数结果都以 IntegrityError 拒绝。
    """
    tokens = _TOKEN.findall(expression)
    trailing = _TOKEN.sub("", expression).strip()
    if trailing:
        raise IntegrityError(f"Formula contains unsupported tokens: {trailing!r}")
    parser = _Parser(tokens, variables, expression)
    value = parser.parse()
    if not isinstance(value, int):
        raise IntegrityError(f"Formula {expression!r} must evaluate to an integer")
    return value


class _Parser:
    """@brief 整数公式的递归下降解析器。"""

    def __init__(self, tokens: list[str], variables: Mapping[str, float], source: str):
        """@brief 绑定记号流、变量表与原文。"""
        self._tokens = tokens
        self._variables = variables
        self._source = source
        self._index = 0

    def parse(self):
        """@brief 解析完整表达式并要求记号耗尽。"""
        value = self._expression()
        if self._index != len(self._tokens):
            raise IntegrityError(f"Formula {self._source!r} has trailing tokens")
        return value

    def _peek(self) -> str | None:
        """@brief 预读当前记号。"""
        return self._tokens[self._index] if self._index < len(self._tokens) else None

    def _take(self, expected: str | None = None) -> str:
        """@brief 消费当前记号，可选校验。"""
        token = self._peek()
        if token is None or (expected is not None and token != expected):
            raise IntegrityError(f"Formula {self._source!r} is malformed near {token!r}")
        self._index += 1
        return token

    def _expression(self):
        """@brief expr := term (('+'|'-') term)*。"""
        value = self._term()
        while self._peek() in {"+", "-"}:
            operator = self._take()
            right = self._term()
            value = value + right if operator == "+" else value - right
        return value

    def _term(self):
        """@brief term := factor (('*'|'/'|'//') factor)*。"""
        value = self._factor()
        while self._peek() in {"*", "/", "//"}:
            operator = self._take()
            right = self._factor()
            if operator == "*":
                value = value * right
            elif operator == "//":
                value = value // right
            else:
                value = value / right
        return value

    def _factor(self):
        """@brief factor := 数 | 变量 | 函数调用 | '(' expr ')' | 一元负号。"""
        token = self._peek()
        if token == "(":
            self._take("(")
            value = self._expression()
            self._take(")")
            return value
        if token == "-":
            self._take()
            return -self._factor()
        if token is not None and re.fullmatch(r"\d+(?:\.\d+)?", token):
            self._take()
            return float(token) if "." in token else int(token)
        if token in _FUNCTIONS:
            self._take()
            self._take("(")
            arguments = [self._expression()]
            while self._peek() == ",":
                self._take(",")
                arguments.append(self._expression())
            self._take(")")
            return _FUNCTIONS[token](*arguments)
        if token in self._variables:
            self._take()
            return self._variables[token]
        raise IntegrityError(f"Formula {self._source!r} references unknown name {token!r}")
