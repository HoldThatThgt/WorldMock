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

from worldmock.catalog.affordances import default_affordances
from worldmock.errors import IntegrityError
from worldmock.life.models import (
    ContractKind,
    CurvePoint,
    EfficiencyFactor,
    FrozenModel,
    TravelMode,
)
from worldmock.life.social import Contact
from worldmock.life.sleep import (
    DEFAULT_CHRONOTYPE_MIDSLEEP_H,
    DEFAULT_SLEEP_NEED_H,
    SLEEP_NEED_MAX_H,
    SLEEP_NEED_MIN_H,
)

#: 商品使用方式的封闭集合（eat=食品进食，use=耐用品使用）。
_PRODUCT_USE_MODES = frozenset({"eat", "use"})

#: 重估间隔缺省（秒）：Kleitman 基本休息—活动周期约 90 分钟（修订合同 L1459）。
RECONSIDERATION_INTERVAL_SECONDS = 5400


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
    work_efficiency_factors: tuple[EfficiencyFactor, ...] = Field(min_length=1)
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
        _validate_curve(self.food_efficiency_curve)
        for factor in self.work_efficiency_factors:
            _validate_curve(factor.curve)
        if not math.isfinite(self.work_notes_multiplier):
            raise ValueError("work notes multiplier must be finite")
        return self


#: 动机档案四个 level 资源名（weights/loss_aversion 的合法键闭集）。
_MOTIVATION_LEVEL_RESOURCES = frozenset({
    "money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need"})

#: 大五特质与权重的固定键集合（与画像编译器输出合同一致）。
_BIG_FIVE_TRAITS = frozenset({
    "openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"})
_MOTIVATION_WEIGHTS = frozenset({
    "money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need", "goal_task"})


class MotivationProfileSource(FrozenModel):
    """@brief 档案编译溯源：输入文本哈希、模型、提示版本与共识统计。"""

    persona_text_sha256: str = Field(min_length=8)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    consensus: dict[str, float | int]


class MotivationProfile(FrozenModel):
    """@brief 人物动机档案：动机评分层求值所需的全部个体参数。

    形状与画像编译器的 agents[].motivation 输出合同钉死一致；缺该字段的
    人物不注入动机提示，行为与档案引入前完全一致（向后兼容）。
    """

    schema_version: Literal[1] = 1
    big_five: dict[str, int]
    weights: dict[str, float]
    tau: float = Field(gt=0)
    beta_present_bias: float = Field(gt=0, le=1)
    loss_aversion: dict[str, float]
    comfort: dict[str, float]
    aspiration: dict[str, int]
    emotion_sensitivity: float = Field(ge=0, le=1)
    attention_prior: dict[str, float] = Field(default_factory=dict)
    source: MotivationProfileSource | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> "MotivationProfile":
        """@brief 核对封闭键集、取值区间与权重归一。"""
        if set(self.big_five) != _BIG_FIVE_TRAITS:
            raise ValueError("motivation big_five must use exactly the five trait keys")
        # 钉死合同样例含 conscientiousness=6（超出经典 5 点 Likert），上界放宽到 10。
        if any(not isinstance(value, int) or not 1 <= value <= 10 for value in self.big_five.values()):
            raise ValueError("motivation big_five values must be integers between 1 and 10")
        if set(self.weights) != _MOTIVATION_WEIGHTS:
            raise ValueError("motivation weights must use exactly the five resource keys")
        if any(not math.isfinite(value) or value < 0 for value in self.weights.values()):
            raise ValueError("motivation weights must be non-negative finite numbers")
        if abs(sum(self.weights.values()) - 1) > 1e-6:
            raise ValueError("motivation weights must sum to one")
        if not self.loss_aversion or not set(self.loss_aversion) <= _MOTIVATION_LEVEL_RESOURCES:
            raise ValueError("motivation loss_aversion keys must be a non-empty level resource subset")
        if any(not math.isfinite(value) or value < 1 for value in self.loss_aversion.values()):
            raise ValueError("motivation loss_aversion values must be at least one")
        if set(self.comfort) != {"balance_fen", "energy_kcal"}:
            raise ValueError("motivation comfort must use exactly balance_fen and energy_kcal")
        if any(not math.isfinite(value) or value < 0 for value in self.comfort.values()):
            raise ValueError("motivation comfort values must be non-negative finite numbers")
        if set(self.aspiration) != {"daily_effective_work_seconds"}:
            raise ValueError("motivation aspiration must use exactly daily_effective_work_seconds")
        if any(value < 0 for value in self.aspiration.values()):
            raise ValueError("motivation aspiration must be non-negative")
        if any(not key.strip() for key in self.attention_prior):
            raise ValueError("motivation attention_prior keys must not be blank")
        if any(not math.isfinite(value) or not 0 <= value <= 1
               for value in self.attention_prior.values()):
            raise ValueError("motivation attention_prior values must be within the unit interval")
        # 注册表规则 6：画像注意力键集 == attention_required=true 键集，多键/少键
        # 均拒绝（取代动机采样侧 .get(kind, 0) 的静默缺省）；attention_required=false
        # 的品类（game_session/subtitle_experience）合法地不在键集内。
        if set(self.attention_prior) != default_affordances().attention_required_kinds:
            raise ValueError(
                "motivation attention_prior keys must equal the registry "
                "attention_required kinds")
        return self


class AgentConfig(FrozenModel):
    """@brief 人物的初态、技能、内容偏好、动机档案与人物档案声明。

    修订合同（L1661-1663）删除 occupation/routine_preference/consumption_
    preference/risk_preference 四个模板字段与 entertainment_preferences 的
    单位区间校验（随机偏好浮点的生产者已随画像编译 v2 删除）；其内容并入
    画像编译产出的 PersonaCard。人物档案字段（chronotype_midsleep、
    sleep_need_hours、时间锚点窗口、contacts）为可选声明，缺省走规格默认
    （04:00、7.5h；experiment_assumption），不是兼容层。
    """

    agent_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    background_story: str | None = None
    health_habits: str | None = None
    social_style: str | None = None
    trait_source_kind: Literal["experiment_assumption"]
    home_location_id: str = Field(min_length=1)
    initial_balance_fen: int = Field(ge=0)
    skill_levels: dict[str, float]
    entertainment_preferences: dict[str, float] = Field(default_factory=dict)
    motivation: MotivationProfile | None = None
    # 生活活动目录（corpora/lifestyle/items.json）的启用声明：None=全局启用
    # 全部条目；非空元组=仅启用列出的 activity_id（语义层对照目录准入校验）。
    lifestyle_activities: tuple[str, ...] | None = None
    # 人物档案字段（修订合同 L1494-1501/L1656）：时型与睡眠需要可按人声明。
    chronotype_midsleep: float = Field(default=DEFAULT_CHRONOTYPE_MIDSLEEP_H, ge=0.0, lt=24.0)
    sleep_need_hours: float = Field(default=DEFAULT_SLEEP_NEED_H, ge=SLEEP_NEED_MIN_H,
                                    le=SLEEP_NEED_MAX_H)
    # 时间锚点窗口（自午夜起的小时数，半开区间 [start, end)）：午餐/晚餐窗口
    # 驱动饥饿体感餐时项（规格 L1508）；None=未声明（无餐时项）。
    lunch_window: tuple[float, float] | None = None
    dinner_window: tuple[float, float] | None = None
    # 场景联系人（修订合同 L1627/L1647）：按人物声明，schema 复用 life.social.Contact。
    contacts: tuple[Contact, ...] = ()

    @model_validator(mode="after")
    def validate_traits(self) -> "AgentConfig":
        """@brief 技能使用非空身份与零到一闭区间；档案窗口与联系人声明合法。"""
        _validate_unit_mapping(self.skill_levels, "skill")
        if self.background_story is not None and len(self.background_story.strip()) < 100:
            raise ValueError("background_story must contain at least 100 characters")
        if self.lifestyle_activities is not None:
            if not self.lifestyle_activities:
                raise ValueError("lifestyle_activities must list ids or stay null to enable all")
            if len(set(self.lifestyle_activities)) != len(self.lifestyle_activities):
                raise ValueError("lifestyle_activities ids must be unique")
            if any(not item.strip() for item in self.lifestyle_activities):
                raise ValueError("lifestyle_activities ids must not be blank")
        for name, window in (("lunch_window", self.lunch_window),
                             ("dinner_window", self.dinner_window)):
            if window is not None and not 0 <= window[0] < window[1] <= 24:
                raise ValueError(f"{name} must satisfy 0 <= start < end <= 24")
        if self.lunch_window is not None and self.dinner_window is not None:
            if (self.lunch_window[1] > self.dinner_window[0]
                    and self.dinner_window[1] > self.lunch_window[0]):
                raise ValueError("dinner window must not overlap lunch window")
        ids = [item.contact_id for item in self.contacts]
        if len(ids) != len(set(ids)):
            raise ValueError("agent contact ids must be unique")
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
    """@brief 一个可见后才能执行和提交的独立工作合同（按人物分配）。

    assignee_agent_id 必填（修订合同 L1645：合同按人分配），引用场景内已声明
    人物（"agent:<id>" 或裸数字 id 两种规范写法，与 A10 ScenarioTaskRef 同名同义）。
    """

    task_id: str = Field(min_length=1)
    assignee_agent_id: str = Field(pattern=r"\S")
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


CONTENT_KINDS = Literal[
    "feature_film",
    "drama_series",
    "webnovel_excerpt",
    "news_feed",
    "joke_feed",
    "subtitle_experience",
    "game_session",
]


class ContentItemConfig(FrozenModel):
    """@brief 可接触内容及其真实时间轴。"""

    item_id: str = Field(min_length=1)
    kind: CONTENT_KINDS
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


class BillConfig(FrozenModel):
    """@brief 一条到点从付款账户向收款账户扣款的账单（房租/水电类）。"""

    bill_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    payee_account_id: str = Field(min_length=1)
    amount_fen: int = Field(gt=0)
    due_at: datetime


class PayrollConfig(FrozenModel):
    """@brief 一条到点从雇主账户向人物账户入账的固定金额工资。"""

    payroll_id: str = Field(min_length=1)
    employer_account_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    amount_fen: int = Field(gt=0)
    due_at: datetime


class PriceChangeConfig(FrozenModel):
    """@brief 一次到点把目录对象的 purchase 转账边金额替换为给定值的调价。"""

    change_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    object_version: int = Field(ge=1)
    amount_fen: int = Field(gt=0)
    due_at: datetime


class ProductReleaseConfig(FrozenModel):
    """@brief 一次到点把商家某对象库存置为给定值的上架。"""

    release_id: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    merchant: str = Field(min_length=1)
    stock: int = Field(ge=0)
    due_at: datetime


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
    # 决策节律（修订合同 L1459/L1470）：距上一次实际决策满该秒数触发重估唤醒。
    reconsideration_interval_seconds: int = Field(default=RECONSIDERATION_INTERVAL_SECONDS,
                                                  gt=0, le=86_400)
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
    bills: tuple[BillConfig, ...] = ()
    payrolls: tuple[PayrollConfig, ...] = ()
    price_changes: tuple[PriceChangeConfig, ...] = ()
    product_releases: tuple[ProductReleaseConfig, ...] = ()

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
        self._validate_schedule_sections(zone)
        return self

    def _validate_schedule_sections(self, zone: ZoneInfo) -> None:
        """@brief 四个可选预排段的身份、时区、窗口与文档内跨引用校验。"""
        _unique(self.bills, "bill_id")
        _unique(self.payrolls, "payroll_id")
        _unique(self.price_changes, "change_id")
        _unique(self.product_releases, "release_id")
        declared = {item.account_id for item in self.accounts}
        people = {f"agent:{item.agent_id}" for item in self.agents}
        product_merchants = {item.product_id: item.merchant for item in self.products}
        for section, items in (("bill", self.bills), ("payroll", self.payrolls),
                               ("price change", self.price_changes),
                               ("product release", self.product_releases)):
            for item in items:
                _aware(item.due_at, f"{section} due_at")
                if not _matches_zone(item.due_at, zone):
                    raise ValueError(f"{section} timestamps do not match the declared timezone")
                if not self.start_at < item.due_at < self.end_at:
                    raise ValueError(f"{section} is outside the scenario interval")
        for bill in self.bills:
            if bill.account_id == bill.payee_account_id:
                raise ValueError("bill payer and payee accounts must differ")
            for account in (bill.account_id, bill.payee_account_id):
                if account not in declared | people:
                    raise ValueError("bill references an undeclared account")
        for payroll in self.payrolls:
            if payroll.employer_account_id not in declared | people:
                raise ValueError("payroll references an undeclared employer account")
            if f"agent:{payroll.agent_id}" not in people:
                raise ValueError("payroll references an unknown agent")
        for change in self.price_changes:
            if change.object_id not in product_merchants:
                raise ValueError("price change references an unknown product")
        for release in self.product_releases:
            if release.object_id not in product_merchants:
                raise ValueError("product release references an unknown product")
            if release.merchant != product_merchants[release.object_id]:
                raise ValueError("product release merchant disagrees with the product binding")

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
    bills: tuple[BillConfig, ...] = ()
    payrolls: tuple[PayrollConfig, ...] = ()
    price_changes: tuple[PriceChangeConfig, ...] = ()
    product_releases: tuple[ProductReleaseConfig, ...] = ()

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
    """@brief 确保任务技能、任务受理人、邮件收件人引用场景内实体。

    entertainment_preferences 的条目引用校验已随随机偏好浮点合同删除
    （修订合同 L1661）；targets 参数保留供调用方组装内容目录。
    """
    agents_by_id = {item.agent_id for item in agents}
    task_ids = {item.task_id for item in tasks}
    skills = {key for agent in agents for key in agent.skill_levels}
    assignees = {f"agent:{item.agent_id}" for item in agents} | {
        str(item.agent_id) for item in agents}
    if any(task.required_skill not in skills for task in tasks):
        raise ValueError("work task references an unknown skill")
    if any(task.assignee_agent_id not in assignees for task in tasks):
        raise ValueError("work task references an unknown assignee agent")
    if any(message.recipient_agent_id not in agents_by_id for message in messages):
        raise ValueError("message references an unknown agent")
    if any(message.related_task_id not in task_ids for message in messages if message.related_task_id):
        raise ValueError("message references an unknown task")


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
