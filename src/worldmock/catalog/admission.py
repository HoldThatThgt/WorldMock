"""@brief 场景加载时的目录准入校验：七条规则全部 fail-closed。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterator, Mapping

from pydantic import ConfigDict, Field, model_validator

from worldmock.catalog.models import (
    Catalog,
    CatalogModel,
    Conservation,
    ConvertEdge,
    DrainEdge,
    Edge,
    ExchangeObject,
    FormulaBinding,
    ResourceKind,
    SourceEdge,
    TransferEdge,
    is_role_placeholder,
)

CATALOG_REF_INTEGRITY = "CATALOG_REF_INTEGRITY"
UNIT_MISMATCH = "UNIT_MISMATCH"
CONSERVATION_DOMAIN_VIOLATION = "CONSERVATION_DOMAIN_VIOLATION"
INITIAL_VALUE_OUT_OF_BOUNDS = "INITIAL_VALUE_OUT_OF_BOUNDS"
TOMBSTONED_REFERENCE = "TOMBSTONED_REFERENCE"
SCHEDULE_INVALID = "SCHEDULE_INVALID"
FORMULA_REF_UNKNOWN = "FORMULA_REF_UNKNOWN"
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
    """@brief 按设计 §8 的七条规则校验场景引用闭集。

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
    _check_formula_refs(refs, catalog, violations)
    return AdmissionResult(ok=not violations, violations=tuple(violations))


def _referenced_edges(refs: ScenarioCatalogRefs, catalog: Catalog) -> Iterator[Edge]:
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
        for endpoint in _edge_resource_ids(edge):
            if endpoint not in catalog.resources:
                violations.append(
                    AdmissionViolation(
                        CATALOG_REF_INTEGRITY,
                        f"edge references resource {endpoint} not in catalog",
                    )
                )


def _edge_resource_ids(edge: Edge) -> tuple[str, ...]:
    """@brief 返回一条边以字面资源 id 触及的全部端点（占位端点除外）。"""
    if isinstance(edge, TransferEdge):
        return _literal_endpoints((edge.from_account, edge.to_account))
    if isinstance(edge, SourceEdge | DrainEdge):
        return (edge.resource,)
    if isinstance(edge, ConvertEdge):
        return _literal_endpoints((edge.from_endpoint, edge.to_endpoint))
    return ()


def _literal_endpoints(endpoints: tuple[str, str]) -> tuple[str, ...]:
    """@brief 过滤掉角色占位端点，只留字面资源 id。"""
    return tuple(endpoint for endpoint in endpoints if not is_role_placeholder(endpoint))


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
    edge: Edge,
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 校验单条边的显式单位、两端单位一致与公式输出单位。"""
    if isinstance(edge, TransferEdge):
        target_unit = _resolved_unit(
            (edge.from_account, edge.to_account), edge.account_roles, refs, catalog,
            violations, compare=True,
        )
    elif isinstance(edge, ConvertEdge):
        target_unit = _resolved_unit(
            (edge.from_endpoint, edge.to_endpoint), edge.account_roles, refs, catalog,
            violations, compare=False,
        )
    else:
        target = edge.resource
        resource = catalog.resources.get(target)
        target_unit = resource.unit if resource is not None else None
    if target_unit is None:
        return
    if edge.unit is not None and edge.unit != target_unit:
        violations.append(
            AdmissionViolation(
                UNIT_MISMATCH,
                f"edge unit {edge.unit} != resource unit {target_unit}",
            )
        )
    for binding in _edge_formula_bindings(edge):
        formula = catalog.formulas.get(binding.formula_ref)
        if formula is not None and formula.unit != target_unit:
            violations.append(
                AdmissionViolation(
                    UNIT_MISMATCH,
                    f"formula {binding.formula_ref} unit {formula.unit}"
                    f" != resource unit {target_unit}",
                )
            )


def _resolved_unit(
    endpoints: tuple[str, str],
    account_roles,
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
    compare: bool,
) -> str | None:
    """@brief 解析 transfer/convert 两端的目标单位。

    字面端点查注册表；占位端点经 account_roles 绑定到场景账户的类型级资源。
    compare 为 True（transfer）时两端必须同单位；convert 只取产出端。
    """
    roles = _accounts_by_role(refs)
    resolved = [
        _resolved_resource_id(endpoint, direction, account_roles, roles)
        for endpoint, direction in zip(endpoints, ("from", "to"))
    ]
    if not compare:
        target = resolved[1]
        resource = catalog.resources.get(target) if target is not None else None
        return resource.unit if resource is not None else None
    units = [
        catalog.resources[target].unit
        for target in resolved
        if target is not None and target in catalog.resources
    ]
    if len(units) == 2 and units[0] != units[1]:
        violations.append(
            AdmissionViolation(
                UNIT_MISMATCH,
                f"edge endpoints use units {units[0]} and {units[1]}",
            )
        )
        return None
    target = next((candidate for candidate in resolved if candidate is not None), None)
    resource = catalog.resources.get(target) if target is not None else None
    return resource.unit if resource is not None else None


def _resolved_resource_id(
    endpoint: str,
    direction: str,
    account_roles,
    roles: Mapping[str, ScenarioAccountRef],
) -> str | None:
    """@brief 把单个端点解析为资源 id：字面端点原样，占位端点走角色绑定。"""
    if not is_role_placeholder(endpoint):
        return endpoint
    if account_roles is None:
        return None
    role_name = account_roles.from_role if direction == "from" else account_roles.to_role
    account = roles.get(role_name)
    return account.resource_id if account is not None else None


def _edge_formula_bindings(edge: Edge) -> tuple[FormulaBinding, ...]:
    """@brief 返回一条边携带的全部公式引用绑定。"""
    if isinstance(edge, TransferEdge) and isinstance(edge.amount_fen, FormulaBinding):
        return (edge.amount_fen,)
    if isinstance(edge, SourceEdge | DrainEdge) and isinstance(edge.amount, FormulaBinding):
        return (edge.amount,)
    return ()


def _check_conservation_domains(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第三条：transfer 限于同封闭域账户，source/drain 限于开放域。

    账户实例由场景声明：场景账户的类型资源必须是 closed 账户；transfer/
    convert 的占位端点必须携带 account_roles 标注、角色已被场景绑定，且绑定
    类型是 closed 账户；transfer 的字面端点仍按注册表 closed 账户校验。
    """
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
    roles = _accounts_by_role(refs)
    for edge in _referenced_edges(refs, catalog):
        if isinstance(edge, TransferEdge):
            _check_transferlike_domains(
                (edge.from_account, edge.to_account),
                edge.account_roles,
                roles,
                catalog,
                violations,
                require_closed_literals=True,
            )
        elif isinstance(edge, ConvertEdge):
            _check_transferlike_domains(
                (edge.from_endpoint, edge.to_endpoint),
                edge.account_roles,
                roles,
                catalog,
                violations,
                require_closed_literals=False,
            )
        elif isinstance(edge, SourceEdge | DrainEdge):
            resource = catalog.resources.get(edge.resource)
            if resource is not None and resource.conservation != Conservation.OPEN:
                violations.append(
                    AdmissionViolation(
                        CONSERVATION_DOMAIN_VIOLATION,
                        f"{edge.type} target {edge.resource} is not in an open domain",
                    )
                )


def _check_transferlike_domains(
    endpoints: tuple[str, str],
    account_roles,
    roles: Mapping[str, ScenarioAccountRef],
    catalog: Catalog,
    violations: list[AdmissionViolation],
    require_closed_literals: bool,
) -> None:
    """@brief 校验 transfer/convert 端点的守恒域一致性。

    占位端点：account_roles 对应方向必须声明（模型层已强制 account_roles
    存在，此处解析角色）、角色必须被场景账户绑定、绑定类型必须是 closed
    账户。字面端点：transfer 要求注册表内 closed 账户；convert 的字面端点
    是资源折算（如能量→货币），不做 closed 限制。
    """
    resolved_domains: list[str] = []
    for endpoint, direction in zip(endpoints, ("from", "to")):
        if is_role_placeholder(endpoint):
            if account_roles is None:
                continue
            role_name = account_roles.from_role if direction == "from" else account_roles.to_role
            account = roles.get(role_name)
            if account is None:
                violations.append(
                    AdmissionViolation(
                        CONSERVATION_DOMAIN_VIOLATION,
                        f"edge endpoint {endpoint} binds role {role_name}"
                        " not declared by scenario accounts",
                    )
                )
                continue
            resolved_domains.append(account.resource_id)
        else:
            resource = catalog.resources.get(endpoint)
            if resource is None:
                continue
            if require_closed_literals and not _is_closed_account(resource):
                violations.append(
                    AdmissionViolation(
                        CONSERVATION_DOMAIN_VIOLATION,
                        f"transfer endpoint {endpoint} is not a closed account",
                    )
                )
            resolved_domains.append(endpoint)
    if len(resolved_domains) == 2 and resolved_domains[0] != resolved_domains[1]:
        violations.append(
            AdmissionViolation(
                CONSERVATION_DOMAIN_VIOLATION,
                f"edge endpoints span closed domains {resolved_domains[0]}"
                f" and {resolved_domains[1]}",
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


def _check_formula_refs(
    refs: ScenarioCatalogRefs,
    catalog: Catalog,
    violations: list[AdmissionViolation],
) -> None:
    """@brief 第七条：对象引用的计费公式必须在注册表中声明。

    覆盖对象顶层 formula_ref 与边金额的公式绑定（travel 类对象的费率公式）。
    """
    for object_ref in refs.objects:
        obj = catalog.objects.get(object_ref.object_id)
        if obj is None:
            continue
        referenced = [obj.formula_ref] if obj.formula_ref is not None else []
        referenced.extend(
            binding.formula_ref for edge in obj.edges for binding in _edge_formula_bindings(edge)
        )
        for formula_ref in referenced:
            if formula_ref not in catalog.formulas:
                violations.append(
                    AdmissionViolation(
                        FORMULA_REF_UNKNOWN,
                        f"object {obj.object_id} references formula"
                        f" {formula_ref} not declared in catalog",
                    )
                )
