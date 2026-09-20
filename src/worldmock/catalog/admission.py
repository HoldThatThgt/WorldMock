"""@brief 场景加载时的目录准入校验：十条规则全部 fail-closed。

资源注册表的八条规则作用于场景引用闭集（`validate_scenario_against_catalog`），
素材可达性的两条作用于世界素材品类（`validate_world_categories`）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterator, Mapping

from pydantic import ConfigDict, Field, ValidationError, model_validator

from worldmock.catalog.models import (
    Catalog,
    CatalogModel,
    CompiledEdge,
    Conservation,
    DrainEdge,
    ResourceKind,
    RuleEdge,
    SourceEdge,
    TransferEdge,
)
from worldmock.life.retrieval import entry_from_content, feasible_kinds

CATALOG_REF_INTEGRITY = "CATALOG_REF_INTEGRITY"
UNIT_MISMATCH = "UNIT_MISMATCH"
CONSERVATION_DOMAIN_VIOLATION = "CONSERVATION_DOMAIN_VIOLATION"
INITIAL_VALUE_OUT_OF_BOUNDS = "INITIAL_VALUE_OUT_OF_BOUNDS"
TOMBSTONED_REFERENCE = "TOMBSTONED_REFERENCE"
SCHEDULE_INVALID = "SCHEDULE_INVALID"
RULE_REF_UNKNOWN = "RULE_REF_UNKNOWN"
CATALOG_VERSION_PIN_MISMATCH = "CATALOG_VERSION_PIN_MISMATCH"


class ScheduleRefKind(StrEnum):
    """@brief 场景预排表引用的封闭事件类型。"""

    PRICE_CHANGE = "price_change"
    PRODUCT_RELEASE = "product_release"
    BILL_DUE = "bill_due"
    PAYROLL = "payroll"
    CALENDAR_FLAG = "calendar_flag"


class ScenarioResourceRef(CatalogModel):
    """@brief 场景侧对一个类型级资源的静态初始化引用。"""

    resource_id: str = Field(pattern=r"\S")
    initial_value: float
    unit: str = Field(pattern=r"\S")


class ScenarioAccountRef(CatalogModel):
    """@brief 场景侧声明的账户实例：按角色绑定到类型级 closed 账户资源。"""

    account_id: str = Field(pattern=r"\S")
    resource_id: str = Field(pattern=r"\S")
    role: str = Field(pattern=r"\S")
    initial_value: float
    unit: str = Field(pattern=r"\S")


class ScenarioObjectRef(CatalogModel):
    """@brief 场景侧引用的一个交换对象。"""

    object_id: str = Field(pattern=r"\S")


class ScenarioScheduleRef(CatalogModel):
    """@brief 场景预排表中的一条事件引用。"""

    kind: ScheduleRefKind
    due_at: int = Field(ge=0)
    sequence: int = Field(ge=0)
    object_id: str | None = Field(default=None, pattern=r"\S")
    object_version: int | None = Field(default=None, ge=1)
    account_id: str | None = Field(default=None, pattern=r"\S")


class ScenarioCatalogRefs(CatalogModel):
    """@brief 场景钉定目录后携带的资源、账户、对象与预排引用闭集。"""

    model_config = ConfigDict(extra="forbid")

    catalog_major: int | None = Field(default=None, ge=1)
    catalog_minor: int | None = Field(default=None, ge=0)
    resources: tuple[ScenarioResourceRef, ...] = ()
    accounts: tuple[ScenarioAccountRef, ...] = ()
    objects: tuple[ScenarioObjectRef, ...] = ()
    schedule: tuple[ScenarioScheduleRef, ...] = ()

    @model_validator(mode="after")
    def validate_refs_shape(self) -> "ScenarioCatalogRefs":
        """@brief 要求版本钉扎成对声明且角色绑定唯一。

        同一账户实例可扮演多个角色（如 agent 账户既是 buyer 又是 traveler），
        但一个角色只许绑定到一个账户实例，否则占位端点解析有歧义。
        """
        if (self.catalog_major is None) != (self.catalog_minor is None):
            raise ValueError("catalog version pin requires both major and minor")
        roles = [account.role for account in self.accounts]
        if len(set(roles)) != len(roles):
            raise ValueError("scenario account roles must be unique")
        return self


@dataclass(frozen=True)
class AdmissionViolation:
    """@brief 一条带稳定错误码的准入拒绝。"""

    code: str
    detail: str


@dataclass(frozen=True)
class AdmissionResult:
    """@brief 准入校验结论：零违例才放行。"""

    ok: bool
    violations: tuple[AdmissionViolation, ...]


def validate_scenario_against_catalog(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
) -> AdmissionResult:
    """@brief 按 docs/design/03-resource-engine.md「准入边界」的八条规则校验场景引用闭集。

    :param refs: 场景侧的资源初始化、账户声明、对象引用与预排表引用。
    :param catalog: 场景钉定版本的冻结目录视图。
    :returns: ok 为 True 才允许加载；否则 violations 逐条给出稳定错误码。
    """
    violations: list[AdmissionViolation] = []
    _check_version_pin(refs, catalog, violations)
    _check_reference_integrity(refs, catalog, violations)
    _check_units(refs, catalog, violations)
    _check_conservation_domains(refs, catalog, violations)
    _check_initial_bounds(refs, catalog, violations)
    _check_tombstones(refs, catalog, violations)
    _check_schedule(refs, catalog, violations)
    _check_rule_refs(refs, catalog, violations)
    return AdmissionResult(ok=not violations, violations=tuple(violations))


def _referenced_edges(refs: ScenarioCatalogRefs, catalog: Catalog) -> Iterator[CompiledEdge]:
    """@brief 产出场景引用对象的全部边。"""
    for object_ref in refs.objects:
        obj = catalog.objects.get(object_ref.object_id)
        if obj is not None:
            yield from obj.edges


def _accounts_by_role(refs: ScenarioCatalogRefs) -> Mapping[str, ScenarioAccountRef]:
    """@brief 以角色为键索引场景声明的账户实例。"""
    return {account.role: account for account in refs.accounts}


def _check_version_pin(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 场景钉扎的目录版本必须与加载视图一致。"""
    if refs.catalog_major is None:
        return
    pinned = (refs.catalog_major, refs.catalog_minor)
    loaded = (catalog.version_major, catalog.version_minor)
    if pinned != loaded:
        violations.append(
            AdmissionViolation(
                CATALOG_VERSION_PIN_MISMATCH,
                f"scenario pins catalog {pinned[0]}.{pinned[1]} but loaded {loaded[0]}.{loaded[1]}",
            )
        )


def _check_reference_integrity(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第一条：资源、账户类型、对象与字面边端点必须存在于目录。"""
    for resource_ref in refs.resources:
        if resource_ref.resource_id not in catalog.resources:
            violations.append(
                AdmissionViolation(
                    CATALOG_REF_INTEGRITY,
                    f"scenario resource {resource_ref.resource_id} not in catalog",
                )
            )
    for account in refs.accounts:
        if account.resource_id not in catalog.resources:
            violations.append(
                AdmissionViolation(
                    CATALOG_REF_INTEGRITY,
                    f"scenario account {account.account_id} binds unknown resource type"
                    f" {account.resource_id}",
                )
            )
    for object_ref in refs.objects:
        if object_ref.object_id not in catalog.objects:
            violations.append(
                AdmissionViolation(
                    CATALOG_REF_INTEGRITY,
                    f"scenario object {object_ref.object_id} not in catalog",
                )
            )
    for edge in _referenced_edges(refs, catalog):
        for resource_id in _edge_resource_ids(edge, catalog):
            if resource_id not in catalog.resources:
                violations.append(
                    AdmissionViolation(
                        CATALOG_REF_INTEGRITY,
                        f"edge references resource {resource_id} not in catalog",
                    )
                )


def _edge_resource_ids(edge: CompiledEdge, catalog: Catalog) -> tuple[str, ...]:
    """@brief 返回一条编译边声明触及的资源域。"""
    if isinstance(edge, TransferEdge):
        return (edge.resource,)
    if isinstance(edge, SourceEdge | DrainEdge):
        return (edge.resource,)
    if isinstance(edge, RuleEdge):
        rule = catalog.exchange_rules.rules.get(edge.rule_id)
        return (rule.resource_id,) if rule is not None else ()
    return ()


def _check_units(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第二条：边与初值的单位必须与资源注册表一致。"""
    for resource_ref in refs.resources:
        resource = catalog.resources.get(resource_ref.resource_id)
        if resource is not None and resource_ref.unit != resource.unit:
            violations.append(
                AdmissionViolation(
                    UNIT_MISMATCH,
                    f"resource {resource_ref.resource_id} scenario unit {resource_ref.unit}"
                    f" != catalog unit {resource.unit}",
                )
            )
    for account in refs.accounts:
        resource = catalog.resources.get(account.resource_id)
        if resource is not None and account.unit != resource.unit:
            violations.append(
                AdmissionViolation(
                    UNIT_MISMATCH,
                    f"account {account.account_id} scenario unit {account.unit}"
                    f" != catalog unit {resource.unit}",
                )
            )
    for edge in _referenced_edges(refs, catalog):
        _check_edge_units(edge, refs, catalog, violations)


def _check_edge_units(
    edge: CompiledEdge,
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 校验单条编译边的显式单位及转账角色资源。"""
    if isinstance(edge, TransferEdge):
        _check_transfer_roles(edge, refs, violations)
        resource_id = edge.resource
    elif isinstance(edge, RuleEdge):
        rule = catalog.exchange_rules.rules.get(edge.rule_id)
        resource_id = rule.resource_id if rule is not None else None
    else:
        resource_id = edge.resource
    resource = catalog.resources.get(resource_id) if resource_id is not None else None
    if resource is None or isinstance(edge, RuleEdge):
        return
    if edge.unit is not None and edge.unit != resource.unit:
        violations.append(
            AdmissionViolation(
                UNIT_MISMATCH,
                f"edge unit {edge.unit} != resource unit {resource.unit}",
            )
        )


def _check_transfer_roles(
    edge: TransferEdge,
    refs: ScenarioCatalogRefs,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 要求转账两端角色均绑定到规则声明的同一资源域。"""
    if edge.account_roles is None:
        return
    accounts = _accounts_by_role(refs)
    for role in (edge.account_roles.from_role, edge.account_roles.to_role):
        account = accounts.get(role)
        if account is None or account.resource_id != edge.resource:
            violations.append(
                AdmissionViolation(
                    UNIT_MISMATCH,
                    f"transfer role {role} does not bind resource {edge.resource}",
                )
            )


def _check_conservation_domains(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第三条：transfer 限于封闭账户域，source/drain 限于开放域。"""
    for account in refs.accounts:
        resource = catalog.resources.get(account.resource_id)
        if resource is not None and not _is_closed_account(resource):
            violations.append(
                AdmissionViolation(
                    CONSERVATION_DOMAIN_VIOLATION,
                    f"scenario account {account.account_id} binds {account.resource_id}"
                    " which is not a closed account",
                )
            )
    for edge in _referenced_edges(refs, catalog):
        if isinstance(edge, TransferEdge):
            resource = catalog.resources.get(edge.resource)
            if resource is not None and not _is_closed_account(resource):
                violations.append(AdmissionViolation(
                    CONSERVATION_DOMAIN_VIOLATION,
                    f"transfer resource {edge.resource} is not a closed account",
                ))
        elif isinstance(edge, SourceEdge | DrainEdge):
            resource = catalog.resources.get(edge.resource)
            if resource is not None and resource.conservation != Conservation.OPEN:
                violations.append(
                    AdmissionViolation(
                        CONSERVATION_DOMAIN_VIOLATION,
                        f"{edge.type} target {edge.resource} is not in an open domain",
                    )
                )
def _is_closed_account(resource: object) -> bool:
    """@brief 判断资源是否为封闭守恒域的账户。"""
    return (
        getattr(resource, "kind", None) == ResourceKind.ACCOUNT
        and getattr(resource, "conservation", None) == Conservation.CLOSED
    )


def _check_initial_bounds(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第四条：静态初值必须落在资源 bounds 内（上界为 null 时只查下界）。"""
    for resource_ref in refs.resources:
        resource = catalog.resources.get(resource_ref.resource_id)
        if resource is None or resource.bounds is None:
            continue
        bounds = resource.bounds
        below_min = resource_ref.initial_value < bounds.min
        above_max = bounds.max is not None and resource_ref.initial_value > bounds.max
        if below_min or above_max:
            violations.append(
                AdmissionViolation(
                    INITIAL_VALUE_OUT_OF_BOUNDS,
                    f"resource {resource_ref.resource_id} initial"
                    f" {resource_ref.initial_value} outside [{bounds.min},"
                    f" {bounds.max}]",
                )
            )


def _check_tombstones(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第五条：新场景不得引用墓碑条目。"""
    tombstoned = catalog.tombstoned_ids()
    for resource_ref in refs.resources:
        if resource_ref.resource_id in tombstoned:
            violations.append(
                AdmissionViolation(
                    TOMBSTONED_REFERENCE,
                    f"scenario resource {resource_ref.resource_id} is tombstoned",
                )
            )
    for object_ref in refs.objects:
        if object_ref.object_id in tombstoned:
            violations.append(
                AdmissionViolation(
                    TOMBSTONED_REFERENCE,
                    f"scenario object {object_ref.object_id} is tombstoned",
                )
            )


def _check_schedule(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第六条：预排表严格递增可排序且引用存在。"""
    previous: tuple[int, int] | None = None
    for entry in refs.schedule:
        current = (entry.due_at, entry.sequence)
        if previous is not None and current <= previous:
            violations.append(
                AdmissionViolation(
                    SCHEDULE_INVALID,
                    f"schedule entry {current} does not strictly follow {previous}",
                )
            )
        previous = current
        _check_schedule_entry(entry, refs, catalog, violations)


def _check_schedule_entry(
    entry: ScenarioScheduleRef,
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 校验单条预排引用的对象版本与账户存在性。

    账户实例由场景声明：BILL_DUE/PAYROLL 引用的账户必须是场景声明过的账户。
    """
    if entry.object_id is not None:
        obj = catalog.objects.get(entry.object_id)
        if obj is None:
            violations.append(
                AdmissionViolation(
                    SCHEDULE_INVALID,
                    f"schedule entry references unknown object {entry.object_id}",
                )
            )
        elif entry.object_version != obj.version:
            violations.append(
                AdmissionViolation(
                    SCHEDULE_INVALID,
                    f"schedule entry pins object {entry.object_id} version"
                    f" {entry.object_version} != catalog version {obj.version}",
                )
            )
    if entry.kind in (ScheduleRefKind.BILL_DUE, ScheduleRefKind.PAYROLL):
        declared = {account.account_id for account in refs.accounts}
        if entry.account_id not in declared:
            violations.append(
                AdmissionViolation(
                    SCHEDULE_INVALID,
                    f"schedule entry {entry.kind.value} references undeclared account"
                    f" {entry.account_id}",
                )
            )


def _check_rule_refs(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第八条：编译对象的动态边只能引用冻结 typed rule。"""
    for object_ref in refs.objects:
        obj = catalog.objects.get(object_ref.object_id)
        if obj is None:
            continue
        for edge in obj.edges:
            if isinstance(edge, RuleEdge) and edge.rule_id not in catalog.exchange_rules.rules:
                violations.append(
                    AdmissionViolation(
                        RULE_REF_UNKNOWN,
                        f"object {obj.object_id} references unknown typed rule {edge.rule_id}",
                    )
                )


# ---------------------------------------------------------------------------
# 世界素材品类准入（修订合同《世界素材品类要求》，逐条 fail-closed）
# ---------------------------------------------------------------------------

CATEGORY_REQUIREMENTS_UNDECLARED = "CATEGORY_REQUIREMENTS_UNDECLARED"
CATEGORY_MEALS_MISSING = "CATEGORY_MEALS_MISSING"
CATEGORY_MEALS_BELOW_MIN = "CATEGORY_MEALS_BELOW_MIN"
CATEGORY_MEALS_SOURCE_MISSING = "CATEGORY_MEALS_SOURCE_MISSING"
CATEGORY_MEALS_FIELD_INVALID = "CATEGORY_MEALS_FIELD_INVALID"
CATEGORY_MEALS_CATALOG_OBJECT_MISSING = "CATEGORY_MEALS_CATALOG_OBJECT_MISSING"
CATEGORY_LOCATION_CORPUS_MISSING = "CATEGORY_LOCATION_CORPUS_MISSING"
CATEGORY_LOCATION_KIND_MISSING = "CATEGORY_LOCATION_KIND_MISSING"
CATEGORY_HOME_AFFORDANCE_MISSING = "CATEGORY_HOME_AFFORDANCE_MISSING"
CATEGORY_OFFICE_AFFORDANCE_MISSING = "CATEGORY_OFFICE_AFFORDANCE_MISSING"
CATEGORY_CONTENT_CORPUS_MISSING = "CATEGORY_CONTENT_CORPUS_MISSING"
CATEGORY_CONTENT_METADATA_MISSING = "CATEGORY_CONTENT_METADATA_MISSING"
CATEGORY_CONTENT_AGE_RATING_REJECTED = "CATEGORY_CONTENT_AGE_RATING_REJECTED"
CATEGORY_DRAMA_NOT_CHINESE = "CATEGORY_DRAMA_NOT_CHINESE"
CATEGORY_WORK_CORPUS_MISSING = "CATEGORY_WORK_CORPUS_MISSING"
CATEGORY_WORK_MATERIALS_BELOW_MIN = "CATEGORY_WORK_MATERIALS_BELOW_MIN"
CATEGORY_WORK_MATERIALS_DUPLICATE = "CATEGORY_WORK_MATERIALS_DUPLICATE"
CATEGORY_WORK_AMI_MISSING = "CATEGORY_WORK_AMI_MISSING"
CATEGORY_LIFESTYLE_CORPUS_MISSING = "CATEGORY_LIFESTYLE_CORPUS_MISSING"
CATEGORY_LIFESTYLE_FIELD_MISSING = "CATEGORY_LIFESTYLE_FIELD_MISSING"
CATEGORY_CONTACTS_BELOW_MIN = "CATEGORY_CONTACTS_BELOW_MIN"
CATEGORY_CONTACTS_MAPPING_INVALID = "CATEGORY_CONTACTS_MAPPING_INVALID"
CATEGORY_PARTITION_KEY_MISSING = "CATEGORY_PARTITION_KEY_MISSING"
CATEGORY_PARTITION_LOCATION_INFEASIBLE = "CATEGORY_PARTITION_LOCATION_INFEASIBLE"
TASK_ASSIGNEE_MISSING = "TASK_ASSIGNEE_MISSING"

MEALS_CORPUS = "meals/items.json"
LOCATIONS_CORPUS = "zhangjiang-map/locations.json"
PANTRY_CORPUS = "scene-stock/home-pantry.json"
CONTACTS_CORPUS = "scenario-contacts/contacts.json"
WORK_CORPUS = "scenario-work/materials.json"
LIFESTYLE_CORPUS = "lifestyle/items.json"
CORPUS_MANIFEST = "manifest.json"
MORNING_WASH_ACTIVITY = "wellness-morning-wash"
#: 生活活动 location_kinds 的封闭集（与 corpora/lifestyle/enrichment.json 声明一致）。
LIFESTYLE_LOCATION_KINDS = frozenset(
    {"home", "office", "outdoor", "fitness", "venue", "anywhere"})
#: 观看内容语料清单：路径与其条目装载方式（list / items 包装 / 单文档）。
#: `*-items.json` 是「流式语料的分区键文件」：一条流对应一条内容条目，声明该
#: 条目的 genre/language（`feed`/`jokes` 与三条社交流各一份）；`games/library/
#: items.json` 的条目自带 `item_id`/`genre`/`language`，直接参与投影。
CONTENT_CORPORA: tuple[tuple[str, str], ...] = (
    ("movies/items.json", "list"),
    ("drama/items.json", "list"),
    ("novels/items.json", "list"),
    ("feed/items.json", "list"),
    ("feed/feed-items.json", "list"),
    ("jokes/items.json", "list"),
    ("jokes/feed-items.json", "list"),
    ("games/library/items.json", "list"),
    ("social/weibo/feed-items.json", "list"),
    ("social/zhihu/feed-items.json", "list"),
    ("social/movie_reviews/feed-items.json", "list"),
    ("sintel/media-identity.json", "single"),
)
#: 白领场景联系人映射的封闭类型集。
CONTACT_TYPES = frozenset({"colleague", "old_friend", "family"})


class ScenarioTaskRef(CatalogModel):
    """@brief 场景任务条目的最小 Schema：tasks[].assignee_agent_id 必填。"""

    task_id: str = Field(pattern=r"\S")
    assignee_agent_id: str = Field(pattern=r"\S")
    input_path: str | None = Field(default=None, pattern=r"\S")


def validate_task_assignees(tasks) -> AdmissionResult:
    """@brief 校验场景任务闭集：每条任务必须声明受理人物（合同按人分配）。

    :param tasks: 场景侧任务条目（可迭代映射）。
    :returns: ok 为 True 才允许加载；缺 assignee_agent_id 时逐条给出稳定错误码。
    """
    violations: list[AdmissionViolation] = []
    for index, task in enumerate(tasks):
        try:
            ScenarioTaskRef.model_validate(task)
        except ValidationError as error:
            violations.append(AdmissionViolation(
                TASK_ASSIGNEE_MISSING,
                f"task[{index}] missing required assignee_agent_id: {error.errors()[0]['msg']}",
            ))
    return AdmissionResult(ok=not violations, violations=tuple(violations))


def validate_world_categories(catalog: Catalog, corpora_root) -> AdmissionResult:
    """@brief 按品类（非条数）校验世界素材：任一要求不满足即拒绝启动。

    :param catalog: 已加载的冻结目录视图（含品类要求声明）。
    :param corpora_root: 语料根目录（Path 或 str）。
    :returns: ok 为 True 才允许启动；violations 携带品类级稳定错误码。
    """
    violations: list[AdmissionViolation] = []
    requirements = catalog.category_requirements
    if requirements is None:
        violations.append(AdmissionViolation(
            CATEGORY_REQUIREMENTS_UNDECLARED,
            "catalog manifest does not declare category_requirements",
        ))
        return AdmissionResult(ok=False, violations=tuple(violations))
    root = Path(corpora_root)
    _check_meals(catalog, root, requirements.meals, violations)
    _check_locations(root, requirements, violations)
    _check_home(catalog, root, requirements.home_pantry, violations)
    _check_office(root, violations)
    _check_content(root, requirements.content_metadata, violations)
    _check_work(root, requirements.work_materials, violations)
    _check_lifestyle(root, violations)
    _check_contacts(root, requirements.contacts, violations)
    _check_partition_keys(root, violations)
    _check_partition_feasibility(root, violations)
    return AdmissionResult(ok=not violations, violations=tuple(violations))


def _load_corpus(corpora_root, relative: str):
    """@brief 读取语料 JSON 文档；缺失或不可解析时返回 None。"""
    path = corpora_root / relative
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _corpus_items(document, mode: str) -> list:
    """@brief 按装载方式把语料文档展开为条目列表。"""
    if mode == "single":
        return [document]
    if mode == "items":
        return list(document.get("items", [])) if isinstance(document, dict) else []
    return list(document) if isinstance(document, list) else []


def _check_meals(catalog: Catalog, root, requirements, violations) -> None:
    """@brief 正餐品类：语料在场、条数、逐条来源/窗口/可供性与目录对象齐备。"""
    document = _load_corpus(root, MEALS_CORPUS)
    if document is None:
        violations.append(AdmissionViolation(
            CATEGORY_MEALS_MISSING, f"meals corpus {MEALS_CORPUS} missing or malformed"))
        return
    meals = document.get("items", [])
    if len(meals) < requirements.min_count:
        violations.append(AdmissionViolation(
            CATEGORY_MEALS_BELOW_MIN,
            f"meals count {len(meals)} below required {requirements.min_count}"))
    for meal in meals:
        _check_meal_entry(catalog, meal, requirements, violations)


def _check_meal_entry(catalog: Catalog, meal, requirements, violations) -> None:
    """@brief 校验单条正餐：热量来源、价格/时长窗口、堂食或配送与目录对象。"""
    meal_id = meal.get("meal_id", "?")
    source = meal.get("energy_source", {})
    database = str(source.get("database", ""))
    if (not database or not any(allowed in database for allowed in requirements.energy_databases)
            or not source.get("source_url") or not source.get("retrieved_at")
            or not source.get("unit_conversion")
            or not isinstance(source.get("energy_kcal_per_100g"), (int, float))
            or source["energy_kcal_per_100g"] <= 0):
        violations.append(AdmissionViolation(
            CATEGORY_MEALS_SOURCE_MISSING,
            f"{meal_id}: energy source (database/url/retrieved_at/conversion/kcal_per_100g)"
            " incomplete"))
    price = meal.get("price_fen")
    duration = meal.get("meal_duration_minutes")
    price_ok = isinstance(price, int) and requirements.price_fen_min <= price <= requirements.price_fen_max
    duration_ok = (isinstance(duration, int) and requirements.duration_minutes_min
                   <= duration <= requirements.duration_minutes_max)
    if not price_ok or not duration_ok or not (meal.get("dine_in") or meal.get("delivery")):
        violations.append(AdmissionViolation(
            CATEGORY_MEALS_FIELD_INVALID,
            f"{meal_id}: price/duration outside window or neither dine-in nor delivery"))
    obj = catalog.objects.get(meal_id)
    if obj is None or not obj.consumable:
        violations.append(AdmissionViolation(
            CATEGORY_MEALS_CATALOG_OBJECT_MISSING,
            f"{meal_id}: no consumable catalog object for meal"))


def _check_locations(root, requirements, violations) -> None:
    """@brief 地点品类：全部必需品类覆盖且达到每类下限（餐馆 ≥2）。"""
    document = _load_corpus(root, LOCATIONS_CORPUS)
    if document is None:
        violations.append(AdmissionViolation(
            CATEGORY_LOCATION_CORPUS_MISSING,
            f"locations corpus {LOCATIONS_CORPUS} missing or malformed"))
        return
    counts: dict[str, int] = {}
    for location in document.get("locations", []):
        for kind in location.get("covers", []):
            counts[kind] = counts.get(kind, 0) + 1
    for kind in requirements.location_required_kinds:
        floor = requirements.location_min_per_kind.get(kind, 1)
        if counts.get(kind, 0) < floor:
            violations.append(AdmissionViolation(
                CATEGORY_LOCATION_KIND_MISSING,
                f"location kind {kind} holds {counts.get(kind, 0)} below required {floor}"))


def _check_home(catalog: Catalog, root, requirements, violations) -> None:
    """@brief 家的可供性：晨间洗漱 15 分钟活动 + 场景早餐储备 ≥7 份可扣减。"""
    lifestyle = _load_corpus(root, LIFESTYLE_CORPUS)
    wash = None
    if lifestyle is not None:
        wash = next((item for item in lifestyle.get("items", [])
                     if item.get("activity_id") == MORNING_WASH_ACTIVITY), None)
    pantry = _load_corpus(root, PANTRY_CORPUS)
    servings = 0
    stock_ok = pantry is not None
    if pantry is not None:
        for entry in pantry.get("items", []):
            obj = catalog.objects.get(entry.get("object_id"))
            if obj is None or not obj.consumable:
                stock_ok = False
            servings += entry.get("quantity", 0)
    if (wash is None or wash.get("typical_duration_minutes") != requirements.morning_wash_minutes
            or not stock_ok or servings < requirements.breakfast_servings_min):
        violations.append(AdmissionViolation(
            CATEGORY_HOME_AFFORDANCE_MISSING,
            f"home affordance incomplete: wash={wash is not None}, stock_ok={stock_ok},"
            f" breakfast_servings={servings}"))


def _check_office(root, violations) -> None:
    """@brief 办公室的可供性：茶水间饮水、会议室与同事在场。"""
    document = _load_corpus(root, LOCATIONS_CORPUS)
    offices = []
    if document is not None:
        offices = [loc for loc in document.get("locations", []) if loc.get("kind") == "office"]
    office_ok = bool(offices)
    for office in offices:
        affordances = office.get("affordances", {})
        water = affordances.get("pantry_water", {})
        water_ok = (water.get("cost_fen") == 0 and water.get("duration_minutes") == 5
                    and isinstance(water.get("entertainment_relief_per_hour"), (int, float))
                    and water["entertainment_relief_per_hour"] > 0)
        if not (water_ok and affordances.get("meeting_room") and affordances.get("colleagues_present")):
            office_ok = False
    if not office_ok:
        violations.append(AdmissionViolation(
            CATEGORY_OFFICE_AFFORDANCE_MISSING,
            "office affordances incomplete: pantry_water/meeting_room/colleagues_present"))


def _check_content(root, requirements, violations) -> None:
    """@brief 内容品类：每条观看条目带四字段元数据且年龄分级低于上限。"""
    for relative, mode in CONTENT_CORPORA:
        document = _load_corpus(root, relative)
        if document is None:
            violations.append(AdmissionViolation(
                CATEGORY_CONTENT_CORPUS_MISSING, f"content corpus {relative} missing or malformed"))
            continue
        for item in _corpus_items(document, mode):
            _check_content_item(item, requirements, violations)
        if relative.startswith("drama/"):
            _check_drama_chinese(_corpus_items(document, mode), violations)


def _check_content_item(item, requirements, violations) -> None:
    """@brief 校验单条观看条目的元数据完整性与年龄上限。"""
    item_id = item.get("item_id", item.get("title", "?"))
    fields_ok = (isinstance(item.get("title"), str) and item["title"].strip()
                 and isinstance(item.get("genre"), list) and item["genre"]
                 and isinstance(item.get("language"), str) and item["language"].strip()
                 and isinstance(item.get("age_rating"), int) and item["age_rating"] >= 0)
    if not fields_ok:
        violations.append(AdmissionViolation(
            CATEGORY_CONTENT_METADATA_MISSING,
            f"content item {item_id} lacks title/genre[]/language/age_rating"))
    elif item["age_rating"] > requirements.max_age_rating:
        violations.append(AdmissionViolation(
            CATEGORY_CONTENT_AGE_RATING_REJECTED,
            f"content item {item_id} age_rating {item['age_rating']} exceeds"
            f" white-collar ceiling {requirements.max_age_rating}"))


def _check_drama_chinese(drama_items, violations) -> None:
    """@brief 修订合同要求 drama 语料整体替换为中文/合规内容。"""
    for item in drama_items:
        if item.get("language") != "zh":
            violations.append(AdmissionViolation(
                CATEGORY_DRAMA_NOT_CHINESE,
                f"drama item {item.get('item_id', '?')} language"
                f" {item.get('language')!r} is not zh (corpus replaced)"))


def _check_work(root, requirements, violations) -> None:
    """@brief 工作品类：≥20 份互不相同的中文材料且 AMI 元数据保留。"""
    document = _load_corpus(root, WORK_CORPUS)
    if document is None:
        violations.append(AdmissionViolation(
            CATEGORY_WORK_CORPUS_MISSING, f"work corpus {WORK_CORPUS} missing or malformed"))
        return
    materials = document.get("materials", [])
    if len(materials) < requirements.min_distinct:
        violations.append(AdmissionViolation(
            CATEGORY_WORK_MATERIALS_BELOW_MIN,
            f"work materials {len(materials)} below required {requirements.min_distinct}"))
    bodies = [str(material.get("body", "")) for material in materials]
    ids = [str(material.get("material_id", "")) for material in materials]
    if len(set(bodies)) != len(bodies) or len(set(ids)) != len(ids):
        violations.append(AdmissionViolation(
            CATEGORY_WORK_MATERIALS_DUPLICATE,
            "work materials contain duplicate bodies or ids"))
    for material in materials:
        if material.get("language") != requirements.language or material.get("type") != "scenario":
            violations.append(AdmissionViolation(
                CATEGORY_WORK_MATERIALS_BELOW_MIN,
                f"work material {material.get('material_id', '?')} must be zh and type=scenario"))
            break
    manifest = _load_corpus(root, CORPUS_MANIFEST)
    paths = {entry.get("path", "") for entry in (manifest or {}).get("entries", [])}
    if not any(path.startswith("ami-es2002/") for path in paths):
        violations.append(AdmissionViolation(
            CATEGORY_WORK_AMI_MISSING, "corpora manifest retains no ami-es2002 metadata"))


def _check_lifestyle(root, violations) -> None:
    """@brief 生活活动品类：每条带 v2 四字段且取值合法。"""
    document = _load_corpus(root, LIFESTYLE_CORPUS)
    if document is None:
        violations.append(AdmissionViolation(
            CATEGORY_LIFESTYLE_CORPUS_MISSING,
            f"lifestyle corpus {LIFESTYLE_CORPUS} missing or malformed"))
        return
    for item in document.get("items", []):
        if not _lifestyle_fields_valid(item):
            violations.append(AdmissionViolation(
                CATEGORY_LIFESTYLE_FIELD_MISSING,
                f"lifestyle item {item.get('activity_id', '?')} lacks valid"
                " location_kinds/time_window/needs_partner/effects"))


def _lifestyle_fields_valid(item) -> bool:
    """@brief 校验单条生活活动的 v2 字段形状与取值域。"""
    kinds = item.get("location_kinds")
    window = item.get("time_window")
    effects = item.get("effects")
    if (not isinstance(kinds, list) or not kinds
            or not set(kinds) <= LIFESTYLE_LOCATION_KINDS):
        return False
    if (not isinstance(window, dict)
            or not isinstance(window.get("start_hour"), int)
            or not isinstance(window.get("end_hour"), int)
            or not 0 <= window["start_hour"] <= 24 or not 0 <= window["end_hour"] <= 24
            or window["start_hour"] >= window["end_hour"]):
        return False
    if not isinstance(item.get("needs_partner"), bool):
        return False
    if not isinstance(effects, dict) or set(effects) != {"entertainment", "social", "health"}:
        return False
    return all(isinstance(value, (int, float)) and 0.0 <= value <= 1.0
               for value in effects.values())


def _check_partition_keys(root, violations) -> None:
    """@brief 素材可达性第一半：每个可检索条目必须齐备全部分区键。

    条目的检索分区键（内容为题材、语言、时长段）必须齐备；任一键缺省的条目
    落不进任何分区，判定为不可达并阻断启动（docs/dev/SPEC-life-realism.md
    「素材可达性」）。
    """
    for entry in _retrieval_entries(root):
        missing = entry.missing_dimensions
        if missing:
            violations.append(AdmissionViolation(
                CATEGORY_PARTITION_KEY_MISSING,
                f"entry {entry.object_id} from {entry.source} lacks retrieval partition"
                f" dimensions {list(missing)} (genre/language/duration_bucket)"))


def _check_partition_feasibility(root, violations) -> None:
    """@brief 素材可达性第二半：每个分区必须存在非空的地点可行组合。

    分区内至少有一条条目的 `location_kinds` 与场景至少一个地点的
    `location_kinds` 交集非空，否则该分区在场景里恒不可行，判定为不可达
    （docs/dev/SPEC-life-realism.md「素材可达性」）。
    """
    entries = _retrieval_entries(root)
    places = _place_kinds(root)
    for partition, members in sorted(_partitions_of(entries).items()):
        if any(feasible_kinds(place, member.location_kinds)
               for member in members for place in places):
            continue
        blocked = sorted(member.object_id for member in members)
        violations.append(AdmissionViolation(
            CATEGORY_PARTITION_LOCATION_INFEASIBLE,
            f"partition {list(partition)} is infeasible at every scenario location: {blocked}"))


def _partitions_of(entries):
    """@brief 按完整分区键归组条目；分区键缺省的条目不进任何分区。"""
    groups: dict[tuple[str, str, str], list] = {}
    for entry in entries:
        if entry.partition is not None:
            groups.setdefault(entry.partition, []).append(entry)
    return groups


def _place_kinds(root) -> tuple[frozenset[str], ...]:
    """@brief 地点语料里全部地点的地点类集合。

    地点未声明 `location_kinds` 时按 `anywhere` 处理：未声明不是「哪里都去不了」，
    而是「没有声明限制」，与 `life.presentation` 的运行期缺省同口径。声明了却为空
    不是缺省——那是「哪里都去不了」，按空集投影。
    """
    document = _load_corpus(root, LOCATIONS_CORPUS)
    if document is None:
        return ()
    places = []
    for location in document.get("locations", []):
        declared = location.get("location_kinds")
        if isinstance(declared, list) and declared:
            places.append(frozenset(str(kind) for kind in declared))
        else:
            places.append(frozenset({"anywhere"}))
    return tuple(places)


def _retrieval_entries(root) -> tuple:
    """@brief 该语料根下全部可检索条目的分区侧事实（内容语料逐条投影）。

    条目身份取语料自身的 `item_id`；时长优先 `duration_seconds`，阅读类条目
    退到 `estimated_read_seconds`（同为整数秒量）。条目的地点类取条目自身的
    `location_kinds` 声明；未声明的按空集投影——空集不构成地点要求，与
    `life.candidates` 的 `_location_reason` 同口径（无要求即恒可行）。

    `single` 装载方式的语料（`sintel/media-identity.json`）是单条内容条目的
    身份文件，本身携带 `item_id` 与分区键（题材、语言），因此与列表式语料一样
    逐条投影：它就是要检索的那一条，不再由场景的 `contents[]` 代声明分区键。
    """
    entries = []
    for relative, mode in CONTENT_CORPORA:
        document = _load_corpus(root, relative)
        if document is None:
            continue
        for item in _corpus_items(document, mode):
            if not isinstance(item, dict):
                continue
            object_id = str(item.get("item_id") or item.get("title") or "")
            if not object_id:
                continue
            entries.append(entry_from_content(object_id, relative, item,
                                              item.get("location_kinds") or ()))
    return tuple(entries)


def _check_contacts(root, requirements, violations) -> None:
    """@brief 联系人品类：每人物 ≥2 条且同事/老友家人的映射与回复策略正确。"""
    document = _load_corpus(root, CONTACTS_CORPUS)
    if document is None:
        violations.append(AdmissionViolation(
            CATEGORY_CONTACTS_BELOW_MIN, f"contacts corpus {CONTACTS_CORPUS} missing"))
        return
    personas = {persona.get("persona_id") for persona in document.get("personas", [])}
    per_persona: dict[str, int] = {}
    mapping_ok = True
    for contact in document.get("contacts", []):
        owner = contact.get("persona_id")
        per_persona[owner] = per_persona.get(owner, 0) + 1
        if contact.get("contact_type") not in CONTACT_TYPES:
            mapping_ok = False
        if contact.get("contact_type") == "colleague":
            if (contact.get("mapped_persona_id") not in personas
                    or contact.get("reply_policy") != requirements.colleague_reply_policy):
                mapping_ok = False
        elif (contact.get("mapped_persona_id") is not None
              or contact.get("reply_policy") != requirements.other_reply_policy):
            mapping_ok = False
    starved = [persona for persona in personas
               if per_persona.get(persona, 0) < requirements.min_per_persona]
    if starved:
        violations.append(AdmissionViolation(
            CATEGORY_CONTACTS_BELOW_MIN,
            f"personas below {requirements.min_per_persona} contacts: {sorted(starved)}"))
    if not mapping_ok:
        violations.append(AdmissionViolation(
            CATEGORY_CONTACTS_MAPPING_INVALID,
            "contact types/mappings/reply policies violate colleague->persona,"
            " old_friend/family->model"))
