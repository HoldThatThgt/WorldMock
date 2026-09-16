"""@brief 读取并严格验证瘦身场景文档与组装后的运行时场景。"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from worldmock.errors import IntegrityError
from worldmock.life.models import ContractKind, CurvePoint, FrozenModel, TravelMode

#: 商品使用方式的封闭集合（eat=食品进食，use=耐用品使用）。
_PRODUCT_USE_MODES = frozenset({"eat", "use"})


class BodyConfig(FrozenModel):
    """@brief 组装后的身体运行参数：实例初值加上目录解析的全部静态语义。"""

    mass_kg: float = Field(gt=0, le=300)
    initial_energy_kcal: float = Field(ge=0)
    maximum_energy_kcal: float = Field(gt=0)
    initial_sleep_debt_hours: float = Field(ge=0)
    basal_kcal_per_hour: float = Field(gt=0)
    awake_sleep_debt_per_hour: float = Field(gt=0)
    sleep_recovery_per_hour: float = Field(gt=0)
    entertainment_need_per_awake_hour: float = Field(ge=0, le=1)
    hunger_threshold_kcal: float = Field(ge=0)
    emergency_threshold_kcal: float = Field(ge=0)
    work_efficiency_curve: tuple[CurvePoint, ...]
    food_efficiency_curve: tuple[CurvePoint, ...]
    activity_met: dict[str, float]
    entertainment_relief_per_hour: dict[str, float]
    work_notes_multiplier: float = Field(ge=1)
    source_kind: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_body(self) -> "BodyConfig":
        """@brief 拒绝不可插值的曲线和自相矛盾阈值。"""
        if self.initial_energy_kcal > self.maximum_energy_kcal:
            raise ValueError("initial energy exceeds maximum energy")
        if not self.emergency_threshold_kcal < self.hunger_threshold_kcal <= self.maximum_energy_kcal:
            raise ValueError("body thresholds must be ordered within maximum energy")
        for curve in (self.work_efficiency_curve, self.food_efficiency_curve):
            _validate_curve(curve)
        if not math.isfinite(self.work_notes_multiplier):
            raise ValueError("work notes multiplier must be finite")
        return self


class AgentConfig(FrozenModel):
    """@brief 人物的职业、明确实验倾向、初态、技能和内容偏好。"""

    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    occupation: str = Field(min_length=1)
    routine_preference: str = Field(min_length=1)
    consumption_preference: str = Field(min_length=1)
    risk_preference: str = Field(min_length=1)
    trait_source_kind: Literal["experiment_assumption"]
    home_location_id: str = Field(min_length=1)
    initial_balance_fen: int = Field(ge=0)
    skill_levels: dict[str, float]
    entertainment_preferences: dict[str, float]

    @model_validator(mode="after")
    def validate_traits(self) -> "AgentConfig":
        """@brief 技能和内容偏好使用非空身份与零到一闭区间。"""
        _validate_unit_mapping(self.skill_levels, "skill")
        _validate_unit_mapping(self.entertainment_preferences, "entertainment preference")
        traits = (self.occupation, self.routine_preference, self.consumption_preference, self.risk_preference)
        if any(not value.strip() for value in traits):
            raise ValueError("persona traits must not be blank")
        return self


class LocationConfig(FrozenModel):
    """@brief 一个可定位并可声明营业条件的地点。"""

    location_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    open_hour: int = Field(default=0, ge=0, le=23)
    close_hour: int = Field(default=24, ge=1, le=24)

    @model_validator(mode="after")
    def validate_hours(self) -> "LocationConfig":
        """@brief 当前营业日模型要求关闭时刻严格晚于开放时刻。"""
        if self.close_hour <= self.open_hour:
            raise ValueError("location closing hour must follow opening hour")
        return self


class RouteConfig(FrozenModel):
    """@brief 一条 prepare 阶段冻结的真实 OSRM 路由及其实例报价。

    报价公式本身是目录语义（rules/travel-fare.toml），由 ScenarioSemantics
    在组装时对每条路线的 fare_fen 做交叉核验，本模型不再内嵌公式。
    """

    route_id: str = Field(min_length=1)
    origin_id: str = Field(min_length=1)
    destination_id: str = Field(min_length=1)
    mode: TravelMode
    distance_meters: float = Field(gt=0, allow_inf_nan=False)
    duration_seconds: int = Field(gt=0)
    fare_fen: int = Field(ge=0)
    fare_source_kind: Literal["scenario"]
    response_path: str = Field(min_length=1)
    response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str = Field(min_length=1)


class ProductConfig(FrozenModel):
    """@brief 组装后的商品运行事实：实例绑定加上目录解析的静态语义。"""

    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    merchant_account_id: str = Field(min_length=1)
    merchant_location_id: str = Field(min_length=1)
    price_fen: int = Field(gt=0)
    price_version: str = Field(min_length=1)
    initial_stock: int = Field(ge=0)
    serving_quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    serving_unit: str | None = None
    energy_kcal_per_unit: float = Field(default=0, ge=0, allow_inf_nan=False)
    use_duration_seconds: int = Field(gt=0)
    use_modes: tuple[str, ...]
    consumable: bool
    effect: str = Field(min_length=1)
    required_location_id: str | None = None
    delivery_delay_seconds: int = Field(gt=0)
    source_kind: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_product(self) -> "ProductConfig":
        """@brief 食品必须携带来源份量，耐用品不得伪造营养。"""
        modes = set(self.use_modes)
        if not modes or len(modes) != len(self.use_modes) or not modes <= _PRODUCT_USE_MODES:
            raise ValueError("product use modes must be unique supported values")
        food = "eat" in self.use_modes
        if food != (self.serving_quantity is not None and self.serving_unit is not None):
            raise ValueError("food serving fields are inconsistent")
        if self.serving_unit is not None and not self.serving_unit.strip():
            raise ValueError("food serving unit must not be blank")
        if not food and self.energy_kcal_per_unit:
            raise ValueError("non-food product cannot provide food energy")
        if food and not self.consumable:
            raise ValueError("food product must be consumable")
        return self


class WorkTaskConfig(FrozenModel):
    """@brief 一个可见后才能执行和提交的独立工作合同。"""

    task_id: str = Field(min_length=1)
    contract_kind: ContractKind
    available_at: datetime
    due_at: datetime
    maximum_duration_seconds: int = Field(gt=0)
    pay_fen: int = Field(gt=0)
    employer_account_id: str = Field(min_length=1)
    required_location_id: str = Field(min_length=1)
    required_skill: str = Field(min_length=1)
    minimum_skill: float = Field(ge=0, le=1)
    required_effective_seconds: float = Field(gt=0, allow_inf_nan=False)
    input_path: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    oracle_path: str = Field(min_length=1)
    oracle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_dates(self) -> "WorkTaskConfig":
        """@brief 要求任务时刻带时区且截止晚于开放。"""
        _aware(self.available_at, "available_at")
        _aware(self.due_at, "due_at")
        if self.due_at <= self.available_at:
            raise ValueError("work due time must follow availability")
        return self


class MessageConfig(FrozenModel):
    """@brief 按场景时钟到达的一封不可变邮件。"""

    message_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    recipient_agent_id: int = Field(gt=0)
    sent_at: datetime
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    related_task_id: str | None = None
    attachment_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_time(self) -> "MessageConfig":
        """@brief 邮件到达时刻必须明确时区。"""
        _aware(self.sent_at, "sent_at")
        return self


class ContentItemConfig(FrozenModel):
    """@brief 可接触内容及其真实时间轴。"""

    item_id: str = Field(min_length=1)
    kind: Literal["subtitle_experience"]
    duration_seconds: int = Field(gt=0)
    timeline_path: str = Field(min_length=1)
    timeline_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    entertainment_relief_per_hour: float = Field(ge=0, le=1)
    experience_label: str = Field(min_length=1)


class GameConfig(FrozenModel):
    """@brief 预生成 TextWorld 游戏的不可变身份。"""

    game_id: str = Field(min_length=1)
    seed: int = Field(gt=0)
    game_path: str = Field(min_length=1)
    game_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    walkthrough_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CalendarConfig(FrozenModel):
    """@brief 正式实验中特殊日的机会与约束。"""

    spring_festival_dates: tuple[str, ...]
    makeup_work_dates: tuple[str, ...]
    overtime_opportunity_dates: tuple[str, ...]
    sick_leave_date: str
    personal_leave_date: str
    social_dinner_date: str

    @model_validator(mode="after")
    def validate_dates(self) -> "CalendarConfig":
        """@brief 特殊日期必须是规范 ISO 日期且各自列表内不重复。"""
        groups = (self.spring_festival_dates, self.makeup_work_dates, self.overtime_opportunity_dates)
        for values in groups:
            if len(values) != len(set(values)):
                raise ValueError("calendar date lists must not contain duplicates")
            for value in values:
                _validate_date(value)
        for value in (self.sick_leave_date, self.personal_leave_date, self.social_dinner_date):
            _validate_date(value)
        return self


class CatalogVersionPin(FrozenModel):
    """@brief 场景对目录版本的钉扎声明。"""

    major: int = Field(ge=1)
    minor: int = Field(ge=0)


class AccountInstanceConfig(FrozenModel):
    """@brief 服务侧账户实例：账户身份、初始金额与其扮演的目录角色。

    人物账户（buyer/traveler/worker 三个能力角色）由 agents[] 隐式声明，
    不在 accounts[] 中重复；外部锚点账户用 external: 前缀角色声明。
    """

    account_id: str = Field(min_length=1)
    money_fen: int = Field(ge=0)
    role: str = Field(min_length=1)


class ProductReference(FrozenModel):
    """@brief 场景对一个目录商品的实例引用与商家绑定。"""

    product_id: str = Field(min_length=1)
    merchant: str = Field(min_length=1)
    merchant_location_id: str = Field(min_length=1)
    stock: int | None = Field(default=None, ge=0)


class BodyInstanceConfig(FrozenModel):
    """@brief 身体三类 level 资源的实例初值。"""

    mass_kg: float = Field(gt=0, le=300)
    initial_energy_kcal: float = Field(ge=0)
    initial_sleep_debt_hours: float = Field(ge=0)
    initial_entertainment_need: float = Field(default=0, ge=0, le=1)


class LifeScenario(FrozenModel):
    """@brief 瘦身场景文档：实例数据与目录引用，静态语义全部来自目录。"""

    schema_version: int = Field(ge=2)
    scenario_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    random_seed: int
    activity_feedback_seconds: int = Field(gt=0, le=86_400)
    max_consecutive_rejections: int = Field(gt=0)
    corpus_root: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_version: CatalogVersionPin
    accounts: tuple[AccountInstanceConfig, ...] = Field(min_length=1)
    body: BodyInstanceConfig
    agents: tuple[AgentConfig, ...]
    locations: tuple[LocationConfig, ...]
    routes: tuple[RouteConfig, ...]
    products: tuple[ProductReference, ...]
    tasks: tuple[WorkTaskConfig, ...]
    messages: tuple[MessageConfig, ...]
    contents: tuple[ContentItemConfig, ...]
    games: tuple[GameConfig, ...]
    calendar: CalendarConfig

    @model_validator(mode="after")
    def validate_document(self) -> "LifeScenario":
        """@brief 核对时区、身份唯一性、账户声明和全部跨引用。"""
        _aware(self.start_at, "start_at")
        _aware(self.end_at, "end_at")
        zone = _zone(self.timezone)
        if self.end_at <= self.start_at or str(zone) != self.timezone:
            raise ValueError("scenario time interval or timezone is invalid")
        _unique(self.agents, "agent_id")
        _unique(self.locations, "location_id")
        _unique(self.routes, "route_id")
        _unique(self.products, "product_id")
        _unique(self.tasks, "task_id")
        _unique(self.messages, "message_id")
        _unique(self.contents, "item_id")
        _unique(self.games, "game_id")
        _unique(self.accounts, "account_id")
        _unique(self.accounts, "role")
        places = [(item.merchant_location_id, None) for item in self.products]
        _check_location_references(self.agents, self.routes, places, self.tasks, self.locations)
        targets = {item.item_id for item in self.contents} | {item.game_id for item in self.games}
        _check_catalog_references(self.agents, self.tasks, self.messages, targets)
        self._validate_merchant_bindings()
        self._validate_route_keys()
        _check_times(zone, self.start_at, self.end_at, self.tasks, self.messages)
        return self

    def _validate_merchant_bindings(self) -> None:
        """@brief 商品商家必须是已声明服务账户且不得占用人物账户身份。"""
        declared = {item.account_id for item in self.accounts}
        people = {f"agent:{item.agent_id}" for item in self.agents}
        if any(not item.account_id.strip() or not item.role.strip() for item in self.accounts):
            raise ValueError("account ids and roles must not be blank")
        for reference in self.products:
            if reference.merchant not in declared:
                raise ValueError("product merchant account is not declared by the scenario")
        if any(account.account_id in people for account in self.accounts):
            raise ValueError("service accounts must not reuse agent account ids")

    def _validate_route_keys(self) -> None:
        """@brief 每个起点、终点与方式只允许一条冻结路线。"""
        keys = [(route.origin_id, route.destination_id, route.mode) for route in self.routes]
        if len(keys) != len(set(keys)) or any(origin == destination for origin, destination, _ in keys):
            raise ValueError("route lookup keys must be unique and connect different locations")


class RuntimeScenario(FrozenModel):
    """@brief 组装后的运行时场景：实例数据加上经目录解析的全部静态语义。

    由 ScenarioSemantics 从瘦身文档与冻结目录组装，不可从 JSON 直接构造；
    领域模块（commerce/travel/work/rules/body）消费的全部静态语义都在此处。
    """

    schema_version: int = Field(ge=1)
    scenario_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    random_seed: int
    activity_feedback_seconds: int = Field(gt=0, le=86_400)
    max_consecutive_rejections: int = Field(gt=0)
    corpus_root: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    body: BodyConfig
    agents: tuple[AgentConfig, ...]
    locations: tuple[LocationConfig, ...]
    routes: tuple[RouteConfig, ...]
    products: tuple[ProductConfig, ...]
    tasks: tuple[WorkTaskConfig, ...]
    messages: tuple[MessageConfig, ...]
    contents: tuple[ContentItemConfig, ...]
    games: tuple[GameConfig, ...]
    employer_account_id: str = Field(min_length=1)
    transport_account_id: str = Field(min_length=1)
    calendar: CalendarConfig

    @model_validator(mode="after")
    def validate_scenario(self) -> "RuntimeScenario":
        """@brief 核对时区、身份唯一性和全部跨引用。"""
        _aware(self.start_at, "start_at")
        _aware(self.end_at, "end_at")
        zone = _zone(self.timezone)
        if self.end_at <= self.start_at or str(zone) != self.timezone:
            raise ValueError("scenario time interval or timezone is invalid")
        _unique(self.agents, "agent_id")
        _unique(self.locations, "location_id")
        _unique(self.routes, "route_id")
        _unique(self.products, "product_id")
        _unique(self.tasks, "task_id")
        _unique(self.messages, "message_id")
        _unique(self.contents, "item_id")
        _unique(self.games, "game_id")
        places = [(item.merchant_location_id, item.required_location_id) for item in self.products]
        _check_location_references(self.agents, self.routes, places, self.tasks, self.locations)
        targets = {item.item_id for item in self.contents} | {item.game_id for item in self.games}
        _check_catalog_references(self.agents, self.tasks, self.messages, targets)
        self._validate_route_keys()
        self._validate_accounts()
        _check_times(zone, self.start_at, self.end_at, self.tasks, self.messages)
        return self

    def _validate_route_keys(self) -> None:
        """@brief 每个起点、终点与方式只允许一条冻结路线。"""
        keys = [(route.origin_id, route.destination_id, route.mode) for route in self.routes]
        if len(keys) != len(set(keys)) or any(origin == destination for origin, destination, _ in keys):
            raise ValueError("route lookup keys must be unique and connect different locations")

    def _validate_accounts(self) -> None:
        """@brief 校验合同账户引用并防止角色账户初始化时互相覆盖。"""
        if any(task.employer_account_id != self.employer_account_id for task in self.tasks):
            raise ValueError("work task references an unknown employer account")
        people = {f"agent:{agent.agent_id}" for agent in self.agents}
        merchants = {product.merchant_account_id for product in self.products}
        services = {self.employer_account_id, self.transport_account_id}
        if any(not account.strip() for account in services | merchants):
            raise ValueError("account ids must not be blank")
        if len(services) != 2 or services & people or services & merchants or people & merchants:
            raise ValueError("account roles must use distinct account ids")


def _check_location_references(agents, routes, product_places, tasks, locations) -> None:
    """@brief 确保住处、路线、商品商家与任务地点引用已声明地点。"""
    known = {item.location_id for item in locations}
    if any(agent.home_location_id not in known for agent in agents):
        raise ValueError("agent home is not a declared location")
    if any(route.origin_id not in known or route.destination_id not in known for route in routes):
        raise ValueError("route references an unknown location")
    for merchant_location, required_location in product_places:
        if merchant_location not in known or required_location is not None and required_location not in known:
            raise ValueError("product references an unknown merchant location")
    if any(task.required_location_id not in known for task in tasks):
        raise ValueError("work task references an unknown location")


def _check_catalog_references(agents, tasks, messages, targets) -> None:
    """@brief 确保任务技能、邮件收件人和娱乐偏好引用场景内实体。"""
    agents_by_id = {item.agent_id for item in agents}
    task_ids = {item.task_id for item in tasks}
    skills = {key for agent in agents for key in agent.skill_levels}
    if any(task.required_skill not in skills for task in tasks):
        raise ValueError("work task references an unknown skill")
    if any(message.recipient_agent_id not in agents_by_id for message in messages):
        raise ValueError("message references an unknown agent")
    if any(message.related_task_id not in task_ids for message in messages if message.related_task_id):
        raise ValueError("message references an unknown task")
    if any(key not in targets for agent in agents for key in agent.entertainment_preferences):
        raise ValueError("entertainment preference references an unknown item")


def _check_times(zone, start_at, end_at, tasks, messages) -> None:
    """@brief 场景事件时刻必须属于声明时区和有效运行窗口。"""
    values = (start_at, end_at)
    if any(not _matches_zone(value, zone) for value in values):
        raise ValueError("scenario timestamps do not match the declared timezone")
    for task in tasks:
        if not (_matches_zone(task.available_at, zone) and _matches_zone(task.due_at, zone)):
            raise ValueError("work task timestamps do not match the declared timezone")
        if not start_at <= task.available_at < task.due_at <= end_at:
            raise ValueError("work task is outside the scenario interval")
    for message in messages:
        if not _matches_zone(message.sent_at, zone) or message.sent_at >= end_at:
            raise ValueError("message timestamp is invalid for the scenario")


def _aware(value: datetime, field_name: str) -> None:
    """@brief 校验日期时间具有有效时区。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_curve(curve: tuple[CurvePoint, ...]) -> None:
    """@brief 效率曲线必须可插值且输出保持在零到一。"""
    values = [point.input_value for point in curve]
    if len(curve) < 2 or values != sorted(set(values)):
        raise ValueError("curve inputs must be strictly increasing")
    if any(not math.isfinite(point.output_value) or point.output_value > 1 for point in curve):
        raise ValueError("curve outputs must be between zero and one")


def _validate_unit_mapping(values: dict[str, float], label: str) -> None:
    """@brief 校验开放身份映射的键和零到一有限数值。"""
    if any(not key.strip() for key in values):
        raise ValueError(f"{label} names must not be blank")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in values.values()):
        raise ValueError(f"{label} values must be between zero and one")


def _validate_date(value: str) -> None:
    """@brief 校验规范的 ISO 自然日文本。"""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("calendar date must use ISO format") from error
    if parsed.isoformat() != value:
        raise ValueError("calendar date must use canonical ISO format")


def _matches_zone(value: datetime, zone: ZoneInfo) -> bool:
    """@brief 判断带偏移时刻是否匹配声明区域在该时刻的偏移。"""
    return value.utcoffset() == value.astimezone(zone).utcoffset()


def _zone(value: str) -> ZoneInfo:
    """@brief 将未知时区稳定转换为场景校验错误。"""
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError("scenario timezone is unknown") from error


def _unique(items: tuple, field_name: str) -> None:
    """@brief 校验实体稳定身份不重复。"""
    values = [getattr(item, field_name) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} values must be unique")


def sha256_file(path: Path) -> str:
    """@brief 计算完整文件的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_scenario_document(path: Path) -> tuple[LifeScenario, str]:
    """@brief 读取唯一 JSON 场景文档并返回其内容身份。"""
    candidate = Path(path)
    if candidate.is_symlink():
        raise IntegrityError("Scenario file is missing or is a symbolic link")
    source = candidate.resolve()
    if not source.is_file():
        raise IntegrityError("Scenario file is missing or is a symbolic link")
    content = source.read_bytes()
    try:
        scenario = LifeScenario.model_validate(json.loads(content))
    except Exception as error:
        raise IntegrityError("Scenario validation failed") from error
    root = (source.parent / scenario.corpus_root).resolve()
    manifest = root / "manifest.json"
    if not manifest.is_file() or sha256_file(manifest) != scenario.corpus_manifest_sha256:
        raise IntegrityError("Scenario corpus manifest identity is invalid")
    return scenario, hashlib.sha256(content).hexdigest()


def load_scenario(path: Path) -> tuple[RuntimeScenario, str]:
    """@brief 完整加载场景：文档校验、目录组装与准入通过后返回运行时场景。"""
    from worldmock.life.semantics import load_runtime_scenario

    semantics, digest = load_runtime_scenario(path)
    return semantics.scenario, digest
