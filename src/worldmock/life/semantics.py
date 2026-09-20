"""@brief 场景语义组装器：引擎与冻结目录之间的唯一通道。

从 load_catalog(catalog_dir) 的目录视图与瘦身场景文档（LifeScenario）组装出
引擎运行所需的一切静态语义：商品（价格/热量/交付延迟/使用时长/耐用品效果）、
身体参数（阈值→告警码、MET、基础代谢、效率曲线）、闭集出行费率与三种
合同付款算子。引擎不直接接触目录，也不从场景文档读取静态语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

from worldmock.catalog import (
    Catalog,
    CatalogError,
    ExchangeObject,
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
from worldmock.categories import (
    Action,
    CategoryError,
    TravelFareRule,
    WorkHourlyRule,
    WorkPackageRule,
    WorkPieceRule,
    evaluate_hourly_pay,
    evaluate_package_pay,
    evaluate_piece_pay,
    evaluate_purchase,
    evaluate_travel_fare,
)
from worldmock.catalog.affordances import (
    AFFORDANCES_FILE,
    AffordanceView,
    default_affordances,
    load_affordances,
)
from worldmock.errors import IntegrityError
from worldmock.life.models import ContractKind, CurvePoint, EfficiencyFactor
from worldmock.life.category_contract import (
    RuntimeCategoryRule,
    admit_reachable_categories,
    optional_defined_rule,
    require_runtime_rule,
)
from worldmock.life.motivation import MotivationTransforms, load_motivation_transforms
from worldmock.life.scenario import (
    AccountInstanceConfig,
    BodyConfig,
    InventoryItemConfig,
    LifeScenario,
    ProductConfig,
    RuntimeScenario,
    parse_scenario_document,
)
from worldmock.life.travel_time import TravelTimeProfile, parse_travel_time_profile
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.events import ScheduledKind

#: 目录内固定身份：四类资源、出行服务与三种合同模板。
MONEY_RESOURCE = "money_fen"
ENERGY_RESOURCE = "energy_kcal"
SLEEP_RESOURCE = "sleep_debt_hours"
ENTERTAINMENT_RESOURCE = "entertainment_need"
TRAVEL_OBJECT = "travel-service"
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

#: 引擎会按键读取的封闭 MET 键集合（body 结算按活动名查表）——自可供性
#: 注册表 kinds.*.met_key 派生（M1：字面常量已删，注册表即事实；含
#: travel:{mode} 模板的封闭展开域 travel:driving/travel:walking）。
_AFFORDANCES = default_affordances()
ENGINE_ACTIVITY_MET_KEYS = _AFFORDANCES.met_closed_keys
#: 娱乐缓解表键集（wait 走被动缓解：边等边刷手机/听点什么，结算在 body.py
#: 的 wait 分支）——自注册表 relief.channel ≠ none 的活动类型派生。
ENGINE_RELIEF_KEYS = _AFFORDANCES.relief_keys


@dataclass(frozen=True)
class CandidateConstants:
    """@brief 候选与呈现常量在运行期的只读视图（取值来源是场景声明）。

    字段名与场景层同名同义，不在运行期另立口径；五个值都在场景加载时校验，
    此处只做搬运（docs/CONTRACTS.md「候选与呈现合同」的「场景常量」一节）。
    """

    presentation_limit: int
    pool_limit: int
    rotation_quota_ratio: float
    rotation_stale_seconds: int
    retrieval_index_threshold: int


@dataclass(frozen=True, slots=True)
class RuntimeItems:
    """@brief 场景可购买商品与仅供持有或配方输出的对象语义。"""

    products: tuple[ProductConfig, ...]
    inventory: tuple[InventoryItemConfig, ...]


@dataclass(frozen=True)
class ScenarioSemantics:
    """@brief 瘦身场景 + 冻结目录组装出的全部静态语义与运行时场景。"""

    document: LifeScenario
    catalog: Catalog
    scenario: RuntimeScenario
    body: BodyConfig
    refs: ScenarioCatalogRefs
    accounts: Mapping[str, int]
    travel_time_profile: TravelTimeProfile
    alert_codes: Mapping[str, str] = field(default_factory=dict)
    motivation_transforms: MotivationTransforms | None = None
    #: 可供性注册表派生视图（assemble_semantics 按场景目录加载并挂入；
    #: 下游各层经 semantics 访问，不直接读注册表文件）。
    affordances: AffordanceView = field(default_factory=default_affordances)

    @property
    def candidate_constants(self) -> "CandidateConstants":
        """@brief 候选与呈现常量（场景声明值经组装后原样透出）。

        下游（候选层、轮转、观察投影与下推返回体）只经此处读取这五个值，
        不各自解析场景文档，也不在代码里另写一份缺省值
        （docs/CONTRACTS.md「候选与呈现合同」的「场景常量」一节）。
        """
        scenario = self.scenario
        return CandidateConstants(
            presentation_limit=scenario.candidate_presentation_limit,
            pool_limit=scenario.candidate_pool_limit,
            rotation_quota_ratio=scenario.rotation_quota_ratio,
            rotation_stale_seconds=scenario.rotation_stale_seconds,
            retrieval_index_threshold=scenario.retrieval_index_threshold,
        )

    def travel_fare(self, mode: str, distance_meters: float) -> int:
        """@brief 按运行时复查后的闭集规则计算出行费率。"""
        category_id = self._object(TRAVEL_OBJECT).category_id
        try:
            action = Action(f"travel:{mode}")
        except ValueError as error:
            raise IntegrityError(f"Travel mode {mode} has no category action") from error
        match = require_runtime_rule(self.catalog, category_id, action)
        return self._travel_fare(match, distance_meters)

    def travel_time_factor(self, mode: str, departure_second: int) -> float:
        """@brief 按 travel-time 公式分支查出发时刻的分时系数。

        查表键为场景时区的出发小时：walking 固定 1.0（自由流步行已合理），
        driving 走 driving_HH 分支（HH 为两位小时，00-23 工作日 profile）。
        """
        hour = datetime.fromtimestamp(
            departure_second, ZoneInfo(self.document.timezone)).hour
        return self.travel_time_profile.factor(mode, hour)

    def travel_planned_seconds(self, mode: str, base_seconds: int,
                               departure_second: int) -> int:
        """@brief 计划时长 = 冻结自由流时长 × factor(出发时刻)，向上取整。

        用 Decimal 精确乘法后 ROUND_CEILING，避免浮点误差让可复现的
        报价在边界值上抖动。
        """
        factor = Decimal(str(self.travel_time_factor(mode, departure_second)))
        return int((factor * int(base_seconds)).to_integral_value(rounding=ROUND_CEILING))

    def with_price(self, product_id: str, amount_fen: int, price_version: str) -> "ScenarioSemantics":
        """@brief 受控价格替换：按当前冻结配对系数返回新商品语义。

        PRICE_CHANGE 到点由引擎换持返回值；price_version 必须同步变化，否则
        购买侧 PRICE_VERSION_MISMATCH 守卫无法区分新旧报价观察。
        """
        if product_id not in {item.product_id for item in self.scenario.products}:
            raise IntegrityError(f"price change references unknown product {product_id}")
        if amount_fen <= 0:
            raise IntegrityError("price change amount must be positive")
        product = self._object(product_id)
        match = require_runtime_rule(self.catalog, product.category_id, Action.PURCHASE)
        try:
            price = evaluate_purchase(amount_fen, 1, match.coefficient)
        except CategoryError as error:
            raise IntegrityError(f"Price change exchange failed: {error.code}") from error
        products = tuple(
            item if item.product_id != product_id else
            item.model_copy(update={"price_fen": price, "price_version": price_version})
            for item in self.scenario.products)
        return replace(self, scenario=self.scenario.model_copy(update={"products": products}, deep=True))

    def contract_payment(self, task, worked_seconds: int, piece_count: int | None) -> int:
        """@brief 按合同类型唯一对应的闭集算子计算整数分付款。"""
        match = self.contract_match(task)
        try:
            if isinstance(match.rule, WorkHourlyRule):
                return evaluate_hourly_pay(
                    _contract_value(task, match.rule.rate_slot), worked_seconds,
                    _contract_value(task, match.rule.maximum_duration_slot), match.coefficient)
            if isinstance(match.rule, WorkPieceRule):
                return evaluate_piece_pay(
                    _contract_value(task, match.rule.rate_slot), piece_count, match.coefficient)
            if isinstance(match.rule, WorkPackageRule):
                return evaluate_package_pay(
                    _contract_value(task, match.rule.amount_slot), True, match.coefficient)
        except CategoryError as error:
            raise IntegrityError(f"Contract exchange failed: {error.code}") from error
        raise IntegrityError(f"Contract {task.task_id} resolved to an incompatible exchange rule")

    def contract_match(self, task) -> RuntimeCategoryRule:
        """@brief 复查任务合同品类配对并返回足迹所需命中身份。"""
        target = self._object(CONTRACT_OBJECTS[task.contract_kind])
        return require_runtime_rule(self.catalog, target.category_id, Action.WORK_SETTLE)

    def travel_match(self, mode: str) -> RuntimeCategoryRule:
        """@brief 复查一次出行动作并返回足迹所需命中身份。"""
        target = self._object(TRAVEL_OBJECT)
        try:
            action = Action(f"travel:{mode}")
        except ValueError as error:
            raise IntegrityError(f"Travel mode {mode} has no category action") from error
        return require_runtime_rule(self.catalog, target.category_id, action)

    def _travel_fare(self, match: RuntimeCategoryRule, distance_meters: float) -> int:
        """@brief 求值一个已经解析的出行规则。"""
        if not isinstance(match.rule, TravelFareRule):
            raise IntegrityError("Travel category resolved to an incompatible exchange rule")
        try:
            return evaluate_travel_fare(match.rule, distance_meters, match.coefficient)
        except CategoryError as error:
            raise IntegrityError(f"Travel exchange failed: {error.code}") from error

    def _object(self, object_id: str) -> ExchangeObject:
        """@brief 返回一个运行必需的已解析对象。"""
        obj = self.catalog.objects.get(object_id)
        if obj is None:
            raise IntegrityError(f"Catalog object {object_id} is missing")
        return obj


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
        motivation_transforms = load_motivation_transforms(directory / "rules")
    except CatalogError as error:
        raise IntegrityError(f"Catalog load failed: {error}") from error
    affordances = load_affordances(directory / AFFORDANCES_FILE)
    return assemble_semantics(document, catalog, motivation_transforms, affordances), digest


def resolve_catalog_dir(scenario_path: Path) -> Path:
    """@brief 场景文件的默认目录位置：scenarios/ 的兄弟目录 catalog/。"""
    return Path(scenario_path).resolve().parent.parent / "catalog"


def assemble_semantics(document: LifeScenario, catalog: Catalog,
                       motivation_transforms: MotivationTransforms | None = None,
                       affordances: AffordanceView | None = None,
                       ) -> ScenarioSemantics:
    """@brief 组装运行时场景并执行目录准入（七条规则 fail-closed）。

    可供性注册表视图缺省取仓库默认注册表（引擎闭集的派生源）；
    load_runtime_scenario 按场景目录加载后显式传入，使目录表键集
    （MET/缓解）对照该场景钉定版本的注册表派生集校验。
    """
    view = default_affordances() if affordances is None else affordances
    pinned = (document.catalog_version.major, document.catalog_version.minor)
    loaded = (catalog.version_major, catalog.version_minor)
    if pinned != loaded:
        raise IntegrityError(
            f"Scenario pins catalog {pinned[0]}.{pinned[1]} but loaded {loaded[0]}.{loaded[1]}")
    body = _assemble_body(document, catalog, view)
    _check_lifestyle_declarations(document, body, view)
    admit_reachable_categories(document, catalog)
    items = _assemble_items(document, catalog)
    services = _service_accounts(document)
    employer_id = _employer_account(document, services)
    transport_id = _role_account(services, "transport:service")
    scenario = _build_runtime(document, body, items, employer_id, transport_id)
    semantics = ScenarioSemantics(
        document=document, catalog=catalog, scenario=scenario, body=body,
        refs=_catalog_refs(document, catalog, scenario),
        accounts=_ledger_accounts(document, services),
        travel_time_profile=parse_travel_time_profile(catalog.travel_time_rules_bytes),
        alert_codes={rule.code: rule.code.lower()
                     for resource in (catalog.resources[ENERGY_RESOURCE],)
                     for rule in resource.alert_rules},
        motivation_transforms=motivation_transforms,
        affordances=view,
    )
    _check_contract_bindings(semantics)
    _check_route_fares(semantics)
    result = validate_scenario_against_catalog(semantics.refs, catalog)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"Scenario admission rejected by catalog: {detail}")
    return semantics


def _check_contract_bindings(semantics: ScenarioSemantics) -> None:
    """@brief 在创建运行产物前确认每条合同规则的命名槽能由任务实例提供。"""
    for task in semantics.scenario.tasks:
        target = semantics.catalog.objects[CONTRACT_OBJECTS[task.contract_kind]]
        match = optional_defined_rule(semantics.catalog, target.category_id, Action.WORK_SETTLE)
        if match is None:
            continue
        rule = match.rule
        if isinstance(rule, WorkHourlyRule):
            _contract_value(task, rule.rate_slot)
            _contract_value(task, rule.maximum_duration_slot)
        elif isinstance(rule, WorkPieceRule):
            _contract_value(task, rule.rate_slot)
        elif isinstance(rule, WorkPackageRule):
            _contract_value(task, rule.amount_slot)
        else:
            raise IntegrityError(
                f"Contract {task.task_id} resolved to an incompatible exchange rule")


def _contract_value(task, slot: str) -> int:
    """@brief 按合同类型把规则命名槽精确绑定到场景任务实例值。"""
    values = {
        ContractKind.HOURLY: {
            "work_rate_fen": task.pay_fen,
            "maximum_duration_seconds": task.maximum_duration_seconds,
        },
        ContractKind.PIECE: {"work_rate_fen": task.pay_fen},
        ContractKind.PACKAGE: {"package_amount_fen": task.pay_fen},
    }[task.contract_kind]
    if slot not in values:
        raise IntegrityError(f"Contract {task.task_id} does not bind rule slot {slot}")
    return values[slot]


def _assemble_body(document: LifeScenario, catalog: Catalog,
                   view: AffordanceView) -> BodyConfig:
    """@brief 从三个 level 资源与合同效率参数组装身体运行配置（封闭键集
    对照可供性注册表派生集校验）。"""
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
        "activity_met": _activity_met_map(energy_rules.activity_met, "activity MET", view),
        "entertainment_relief_per_hour": _closed_map(
            fun_rules.entertainment_relief_per_hour, view.relief_keys, "entertainment relief"),
        "work_notes_multiplier": _contract_efficiency(catalog, "work_notes_multiplier"),
        "source_kind": f"catalog:{catalog.version_major}.{catalog.version_minor}",
    })


def _assemble_items(document: LifeScenario, catalog: Catalog) -> RuntimeItems:
    """@brief 解析可购买商品和初始持有、配方输出所需的对象。"""
    role_index = {item.role: item for item in document.accounts}
    products = []
    for reference in document.products:
        obj = catalog.objects.get(reference.product_id)
        if obj is None:
            raise IntegrityError(f"scenario product {reference.product_id} is not in the catalog")
        if obj.object_kind != ObjectKind.PRODUCT:
            raise IntegrityError(f"catalog object {obj.object_id} is not a product")
        products.append(_product_payload(reference, obj, role_index, catalog))
    product_ids = {item.product_id for item in products}
    inventory_ids = _inventory_object_ids(document) - product_ids
    inventory = tuple(_inventory_payload(_product_object(catalog, object_id))
                      for object_id in sorted(inventory_ids))
    return RuntimeItems(tuple(products), inventory)


def _product_payload(
    reference, obj: ExchangeObject, role_index: Mapping[str, AccountInstanceConfig],
    catalog: Catalog,
) -> ProductConfig:
    """@brief 从对象边与字段解析单个商品的全部静态语义。"""
    consume = _consume_edge(obj)
    binding = next((item for item in role_index.values()
                    if item.account_id == reference.merchant), None)
    if binding is None:
        raise IntegrityError(f"Product {reference.product_id} merchant account is not declared")
    expected_role = _purchase_merchant_role(catalog, obj)
    if binding.role != expected_role:
        raise IntegrityError(
            f"Product {reference.product_id} merchant does not bind catalog role {expected_role}")
    if obj.stock is None and reference.stock is None:
        raise IntegrityError(f"product {reference.product_id} has no stock source")
    payload = {**_inventory_values(obj, consume),
        "merchant_account_id": reference.merchant,
        "merchant_location_id": reference.merchant_location_id,
        "price_fen": _purchase_price(catalog, obj),
        "price_version": f"catalog:{obj.object_id}:v{obj.version}",
        "initial_stock": reference.stock if reference.stock is not None else obj.stock.initial,
    }
    return ProductConfig.model_validate(payload)


def _inventory_payload(obj: ExchangeObject) -> InventoryItemConfig:
    """@brief 把不能购买但可能持有的对象编译成消费运行视图。"""
    return InventoryItemConfig.model_validate(_inventory_values(obj, _consume_edge(obj)))


def _inventory_values(obj: ExchangeObject, consume: SourceEdge | None) -> dict:
    """@brief 形成商品与库存对象共享的已解析静态语义。"""
    payload = {
        "product_id": obj.object_id, "name": obj.name, "category_id": obj.category_id,
        "inventory_unit": obj.attributes.get("inventory_unit"),
        "energy_kcal_per_unit": consume.amount if consume is not None else 0,
        "use_duration_seconds": _positive(obj.use_duration_seconds, "use_duration_seconds"),
        "use_modes": obj.use_modes, "consumable": obj.consumable,
        "effect": obj.effect,
        "required_location_id": obj.required_location_id,
        "delivery_delay_seconds": _positive(obj.delivery_delay_seconds, "delivery_delay_seconds"),
        "source_kind": f"catalog:{obj.object_id}:v{obj.version}",
    }
    if obj.serving is not None:
        payload.update(serving_quantity=obj.serving.quantity, serving_unit=obj.serving.unit)
    return payload


def _inventory_object_ids(document: LifeScenario) -> set[str]:
    """@brief 汇总初始持有与所有显式配方输出对象身份。"""
    held = {object_id for agent in document.agents for rows in agent.initial_inventory.values()
            for object_id in rows}
    return held | set(document.recipe_output_bindings.values())


def _product_object(catalog: Catalog, object_id: str) -> ExchangeObject:
    """@brief 返回一个必须存在且属于商品树的目录对象。"""
    obj = catalog.objects.get(object_id)
    if obj is None or obj.object_kind != ObjectKind.PRODUCT:
        raise IntegrityError(f"Inventory object {object_id} is not a catalog product")
    return obj


def _build_runtime(
    document: LifeScenario, body: BodyConfig, items: RuntimeItems,
    employer_id: str, transport_id: str,
) -> RuntimeScenario:
    """@brief 用组装好的语义构造运行时场景并触发其全部跨引用校验。"""
    try:
        return _runtime_payload(document, body, items, employer_id, transport_id)
    except Exception as error:
        raise IntegrityError(f"Scenario assembly failed: {error}") from error


def _runtime_payload(
    document: LifeScenario, body: BodyConfig, items: RuntimeItems,
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
        "candidate_presentation_limit": document.candidate_presentation_limit,
        "candidate_pool_limit": document.candidate_pool_limit,
        "rotation_quota_ratio": document.rotation_quota_ratio,
        "rotation_stale_seconds": document.rotation_stale_seconds,
        "retrieval_index_threshold": document.retrieval_index_threshold,
        "corpus_root": document.corpus_root,
        "corpus_manifest_sha256": document.corpus_manifest_sha256,
        "body": body.model_dump(mode="python"),
        "agents": [item.model_dump(mode="python") for item in document.agents],
        "locations": [item.model_dump(mode="python") for item in document.locations],
        "routes": [item.model_dump(mode="python") for item in document.routes],
        "products": [item.model_dump(mode="python") for item in items.products],
        "inventory_items": [item.model_dump(mode="python") for item in items.inventory],
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
        "recipe_output_bindings": dict(document.recipe_output_bindings),
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
    object_ids = {item.product_id for item in document.products} | _inventory_object_ids(document)
    object_ids.add(TRAVEL_OBJECT)
    object_ids.update(CONTRACT_OBJECTS[kind] for kind in {task.contract_kind for task in document.tasks})
    objects = [ScenarioObjectRef(object_id=object_id) for object_id in sorted(object_ids)]
    return ScenarioCatalogRefs(
        catalog_major=document.catalog_version.major,
        catalog_minor=document.catalog_version.minor,
        resources=tuple(admitted),
        accounts=tuple(accounts),
        objects=tuple(objects),
        schedule=_schedule_refs(scenario, catalog),
    )


#: 预排表引用只覆盖 docs/design/04-simulation-engine.md「预排事件」五种新事件：邮件/任务/配送由场景文档校验器负责。
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
    """@brief defined 路线报价必须与闭集规则一致；no_effect 留给运行拒绝。"""
    target = semantics.catalog.objects.get(TRAVEL_OBJECT)
    if target is None:
        raise IntegrityError(f"Catalog object {TRAVEL_OBJECT} is missing")
    for route in semantics.document.routes:
        action = Action(f"travel:{route.mode.value}")
        match = optional_defined_rule(semantics.catalog, target.category_id, action)
        if match is None:
            continue
        expected = semantics._travel_fare(match, route.distance_meters)
        if route.fare_fen != expected:
            raise IntegrityError(
                f"Route {route.route_id} fare {route.fare_fen} disagrees with the"
                f" catalog exchange value {expected}")


def _service_accounts(document: LifeScenario) -> dict[str, AccountInstanceConfig]:
    """@brief 以账户 id 为键索引场景声明的服务侧账户实例。"""
    return {item.account_id: item for item in document.accounts}


def _role_account(services: Mapping[str, AccountInstanceConfig], role: str) -> str:
    """@brief 按目录角色解析具体账户 id。"""
    matches = [item.account_id for item in services.values() if item.role == role]
    if len(matches) != 1:
        raise IntegrityError(f"catalog role {role} must bind exactly one scenario account")
    return matches[0]


def _employer_account(
    document: LifeScenario, services: Mapping[str, AccountInstanceConfig],
) -> str:
    """@brief 从任务实例取得唯一雇主；无任务时要求唯一 employer 角色账户。"""
    task_accounts = {task.employer_account_id for task in document.tasks}
    if len(task_accounts) == 1:
        account_id = next(iter(task_accounts))
        if account_id not in services:
            raise IntegrityError("Work task employer account is not declared")
        return account_id
    if len(task_accounts) > 1:
        raise IntegrityError("Runtime scenario supports exactly one employer account")
    matches = [item.account_id for item in services.values()
               if item.role.startswith("employer:")]
    if len(matches) != 1:
        raise IntegrityError("Scenario must declare exactly one employer account")
    return matches[0]


def _contract_efficiency(catalog: Catalog, attribute: str):
    """@brief 从三种合同模板读取一致的标量效率参数（如笔记倍率）。"""
    values = []
    for object_id in CONTRACT_OBJECTS.values():
        obj = catalog.objects.get(object_id)
        if obj is None:
            raise IntegrityError(f"contract template {object_id} has no efficiency params")
        values.append(obj.attribute(attribute))
    if any(value != values[0] for value in values):
        raise IntegrityError(f"contract templates disagree on {attribute}")
    return values[0]


def _contract_work_efficiency_factors(catalog: Catalog) -> tuple[EfficiencyFactor, ...]:
    """@brief 从三种合同模板读取一致的工作效率因子列表并转为引擎配置。"""
    values = []
    for object_id in CONTRACT_OBJECTS.values():
        obj = catalog.objects.get(object_id)
        if obj is None:
            raise IntegrityError(f"contract template {object_id} has no efficiency params")
        values.append(obj.attribute("work_efficiency_factors"))
    if any(value != values[0] for value in values):
        raise IntegrityError("contract templates disagree on work_efficiency_factors")
    return tuple(EfficiencyFactor.model_validate(dict(factor)) for factor in values[0])


def _purchase_price(catalog: Catalog, obj: ExchangeObject) -> int:
    """@brief defined 购买按系数编译单价，no_effect 保留只读展示值。"""
    match = optional_defined_rule(catalog, obj.category_id, Action.PURCHASE)
    if match is None:
        price = obj.attribute("price_fen")
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            raise IntegrityError(f"Product {obj.object_id} has an invalid price")
        return price
    price_slot = getattr(match.rule, "price_slot", None)
    if price_slot is None:
        raise IntegrityError(f"Product {obj.object_id} resolved to an incompatible purchase rule")
    try:
        return evaluate_purchase(obj.attribute(price_slot), 1, match.coefficient)
    except CategoryError as error:
        raise IntegrityError(f"Product price exchange failed: {error.code}") from error


def _consume_edge(obj: ExchangeObject) -> SourceEdge | None:
    """@brief 取得商品对象的 consume 能量边（耐用品没有）。"""
    edges = [edge for edge in obj.edges if isinstance(edge, SourceEdge) and edge.on == "consume"]
    if len(edges) > 1:
        raise IntegrityError(f"product {obj.object_id} has multiple consume edges")
    return edges[0] if edges else None


def _purchase_merchant_role(catalog: Catalog, obj: ExchangeObject) -> str:
    """@brief 从 defined 购买边或显式 no_effect 对象槽取得具体商家角色。"""
    if optional_defined_rule(catalog, obj.category_id, Action.PURCHASE) is None:
        role = obj.attribute("merchant_role")
        if not isinstance(role, str) or not role:
            raise IntegrityError(f"product {obj.object_id} has no merchant role")
        return role
    edges = [edge for edge in obj.edges if isinstance(edge, TransferEdge) and edge.on == "purchase"]
    if len(edges) != 1 or edges[0].account_roles is None:
        raise IntegrityError(f"product {obj.object_id} needs one purchase transfer edge")
    if edges[0].account_roles.from_role != "buyer":
        raise IntegrityError(
            f"Product {obj.object_id} purchase transfer from_role must be buyer")
    return edges[0].account_roles.to_role


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


#: 生活活动 MET 键前缀（corpora/lifestyle/items.json 的 activity_id 查表键）——
#: 自注册表 met_key 的 "{item_id}" 开放命名空间模板派生。
_LIFESTYLE_MET_PREFIX = _AFFORDANCES.met_open_prefixes[0]


def _activity_met_map(values, label: str, view: AffordanceView) -> dict[str, float]:
    """@brief 校验活动 MET 表：封闭基键集（注册表 met_key 派生）+ 开放
    命名空间前缀键（lifestyle:<id>）且数值为正。"""
    if values is None:
        raise IntegrityError(f"catalog {label} table is missing")
    prefixes = view.met_open_prefixes
    base = {key for key in values
            if not any(key.startswith(prefix) for prefix in prefixes)}
    if base != view.met_closed_keys:
        raise IntegrityError(f"catalog {label} base keys must match the engine activity set")
    for key in values:
        prefix = next((p for p in prefixes if key.startswith(p)), None)
        if prefix is None:
            continue
        activity_id = key[len(prefix):]
        if not activity_id or any(not (c.isalnum() and c.isascii()) and c != "-" for c in activity_id):
            raise IntegrityError(f"catalog {label} lifestyle key {key!r} is malformed")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in values.values()):
        raise IntegrityError(f"catalog {label} values must be positive")
    return dict(values)


def _check_lifestyle_declarations(document: LifeScenario, body: BodyConfig,
                                  view: AffordanceView | None = None) -> None:
    """@brief 人物声明启用的生活活动必须存在于目录 MET 表（fail-closed）。"""
    prefix = (default_affordances() if view is None else view).met_open_prefixes[0]
    known = {key[len(prefix):] for key in body.activity_met if key.startswith(prefix)}
    for agent in document.agents:
        if agent.lifestyle_activities is None:
            continue
        unknown = sorted(set(agent.lifestyle_activities) - known)
        if unknown:
            raise IntegrityError(
                f"agent {agent.agent_id} declares unknown lifestyle activities: {unknown}")


def _curve(points, label: str) -> tuple[CurvePoint, ...]:
    """@brief 把目录曲线点转换为引擎曲线模型。"""
    if not points:
        raise IntegrityError(f"catalog curve {label} is empty")
    return tuple(CurvePoint(input_value=point.input_value, output_value=point.output_value)
                 for point in points)
