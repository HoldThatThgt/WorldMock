"""@brief 读取并严格验证瘦身场景文档与组装后的运行时场景。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from worldmock.catalog.affordances import REFERENCEABLE_RESOURCES, default_affordances
from worldmock.errors import IntegrityError
from worldmock.life.felt import APPETITE_STARVING_KCAL
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.base_models import CurvePoint, EfficiencyFactor, FrozenModel
from worldmock.life.models import ContractKind, DataSource, TravelMode
from worldmock.life.map_models import (
    LocationConfig,
    LocationReference,
    MapDataset,
    MapDatasetReference,
    RouteConfig,
    SupplyConfig,
    SupplyReference,
    validate_names,
    validate_price_versions,
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

#: 重估间隔缺省（秒）：Kleitman 基本休息—活动周期约 90
#: 分钟（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）。
RECONSIDERATION_INTERVAL_SECONDS = 5400

#: 候选与呈现常量的合法上界。上界的作用是拦住让两侧口径失效的取值，不是推荐值：
#: 单次呈现上限 1000 已远超一次模型调用能承载的条目数；检索分区池上限 100_000 之外的
#: 池在 15% 换新名额（每次 3 条）下最坏等待约 1.5 年，已超出正式实验窗口，
#: 闭式断言会退化成不断言（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「候选契约与呈现规则」「两级上限」）。
CANDIDATE_PRESENTATION_LIMIT_MAX = 1000
CANDIDATE_POOL_LIMIT_MAX = 100_000

#: 娱乐缓解的下限偏好 f 缺省值（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ，裁决 D3）：实际缓解按有效偏好
#: f + (1 − f)·w 调幅度，不喜欢的内容（w = 0）也按 f 缓解。实验假设（敏感区间 0.3–0.7，文献只能确定 0 < f < 1），
#: 登记在 docs/EXPERIMENT.md#experiment-worldmock-实验设置「实验假设声明」；放场景层以免触发目录版本变更。
DEFAULT_ENTERTAINMENT_RELIEF_PREFERENCE_FLOOR = 0.5

#: 长期未呈现判定阈值的缺省（秒）＝一周。取值理由：轮转按人物的作息周期产生可观测效果，
#: 而作息以周为节律；单池 1000 条、每次呈现 20 条时换新名额 k = floor(20 × 0.15) = 3，
#: 实测每天决策点中位 71、范围 9–112.5（.work/object-model-reanalysis/REPORT.md 2.8），
#: 故最坏未呈现间隔闭式上界 ⌈N ÷ k⌉ ÷ D 约 2.96–37 天、中位约 4.7 天，
#: 一周落在该区间内且略高于中位数，使轮转名额在一个人物的一周内必然被消耗一轮。
DEFAULT_ROTATION_STALE_SECONDS = 604_800


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
    # 下限偏好 f 取自场景 body（semantics._assemble_body 搬运），不从目录解析。
    entertainment_relief_preference_floor: float = Field(gt=0, lt=1, allow_inf_nan=False)
    work_notes_multiplier: float = Field(ge=1)
    source_kind: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_body(self) -> "BodyConfig":
        """@brief 拒绝不可插值的曲线和自相矛盾阈值。

        阈值次序为「饿得发慌」上界 < 紧急阈值 < 饥饿阈值 ≤ 能量上限：紧急阈值同时是「至少饿得难受」的
        下限，不高于 APPETITE_STARVING_KCAL 时两条低能量规则会重叠（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动
        「饥饿在低能量时不失灵」）。
        """
        if self.initial_energy_kcal > self.maximum_energy_kcal:
            raise ValueError("initial energy exceeds maximum energy")
        if not (
            APPETITE_STARVING_KCAL
            < self.emergency_threshold_kcal
            < self.hunger_threshold_kcal
            <= self.maximum_energy_kcal
        ):
            raise ValueError("body thresholds must be ordered within maximum energy and above the starving floor")
        _validate_curve(self.food_efficiency_curve)
        for factor in self.work_efficiency_factors:
            _validate_curve(factor.curve)
        if not math.isfinite(self.work_notes_multiplier):
            raise ValueError("work notes multiplier must be finite")
        return self


#: 大五特质与权重的固定键集合（与画像编译器输出合同一致）。
_BIG_FIVE_TRAITS = frozenset({"openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"})
_MOTIVATION_WEIGHTS = frozenset({"money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need", "goal_task"})


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
        if not self.loss_aversion or not set(self.loss_aversion) <= REFERENCEABLE_RESOURCES:
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
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in self.attention_prior.values()):
            raise ValueError("motivation attention_prior values must be within the unit interval")
        # 注册表规则 6：画像注意力键集 == attention_required=true 键集，多键/少键
        # 均拒绝（取代动机采样侧 .get(kind, 0) 的静默缺省）；attention_required=false
        # 的品类（game_session/subtitle_experience）合法地不在键集内。
        if set(self.attention_prior) != default_affordances().attention_required_kinds:
            raise ValueError("motivation attention_prior keys must equal the registry attention_required kinds")
        return self


class WorkShift(FrozenModel):
    """@brief 一天的班次（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：自当地零点起的分钟，同日、starts < ends，休息时段落在
    班次内、升序且互不重叠；区间一律半开。三个键都必填，没有休息时段写空列表。"""

    starts: int
    ends: int
    breaks: tuple[tuple[int, int], ...]

    @model_validator(mode="after")
    def validate_shift(self) -> "WorkShift":
        """@brief 拒绝跨日或倒置的班次、越出班次的休息时段，以及乱序或重叠的休息时段。"""
        if not 0 <= self.starts < self.ends <= 1440:
            raise ValueError("work shift must satisfy 0 <= starts < ends <= 1440")
        if any(not self.starts <= begin < finish <= self.ends for begin, finish in self.breaks):
            raise ValueError("work shift breaks must lie within the shift")
        if any(previous[1] > following[0] for previous, following in zip(self.breaks, self.breaks[1:])):
            raise ValueError("work shift breaks must be ascending and must not overlap")
        return self


class WorkSchedule(FrozenModel):
    """@brief 人物的工作安排（4.3）：工作地点、每周七天全写的班次（周一为 "0"）与按 ISO 日期覆盖的例外；值为 null 表示
    当日不当班。不带时区字段（复用场景的 timezone），不从星期或任务推断；与所选地点、场景日历的交叉核对在加载检查
    （load_checks.check_work_schedules）。"""

    workplace_location_id: str = Field(min_length=1)
    weekly: dict[str, WorkShift | None]
    exceptions: dict[str, WorkShift | None]

    @model_validator(mode="after")
    def validate_schedule(self) -> "WorkSchedule":
        """@brief 每周七天都要写出，例外日期必须是规范的 ISO 日期。"""
        if set(self.weekly) != {str(day) for day in range(7)}:
            raise ValueError("work_schedule weekly must declare all seven weekdays")
        for key in self.exceptions:
            try:
                canonical = date.fromisoformat(key).isoformat() == key
            except ValueError:
                canonical = False
            if not canonical:
                raise ValueError("work_schedule exception dates must use canonical ISO format")
        return self


class AgentConfig(FrozenModel):
    """@brief 人物的初态、技能、内容偏好、动机档案与人物档案声明。

    修订合同（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）删除 occupation/routine_preference/consumption_
    preference/risk_preference 四个模板字段与随机偏好浮点字段（其生产者已随
    画像编译 v2 删除，单位区间校验一并移除）；其内容并入
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
    qualifications: tuple[str, ...]
    motivation: MotivationProfile | None = None
    # 生活活动目录（corpora/lifestyle/items.json）的启用声明：None=全局启用
    # 全部条目；非空元组=仅启用列出的 activity_id（语义层对照目录准入校验）。
    lifestyle_activities: tuple[str, ...] | None = None
    # 交互菜单人物声明必须显式出现；null 表示不在地点类菜单之上追加限制。
    interaction_menu: InteractionMenu | None
    # 工作安排（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ，裁决 W3）：必须显式出现；null 表示这个人物没有任何在岗语义。
    work_schedule: WorkSchedule | None
    # 人物档案字段（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「两过程睡眠模型」、
    # docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）：时型与睡眠需要可按人声明。
    chronotype_midsleep: float = Field(default=DEFAULT_CHRONOTYPE_MIDSLEEP_H, ge=0.0, lt=24.0)
    sleep_need_hours: float = Field(default=DEFAULT_SLEEP_NEED_H, ge=SLEEP_NEED_MIN_H, le=SLEEP_NEED_MAX_H)
    # 时间锚点窗口（自午夜起的小时数，半开区间 [start, end)）：午餐/晚餐窗口
    # 驱动饥饿体感餐时项（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）；None=未声明（无餐时项）。
    lunch_window: tuple[float, float] | None = None
    dinner_window: tuple[float, float] | None = None
    # 下班、到家窗口（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）：与午餐、晚餐窗口同单位同校验，两者都声明时下班起点不晚于
    # 到家起点；只用于场景作息覆盖卡片与零点计划的人物输入，None=未声明。
    leave_work_window: tuple[float, float] | None = None
    arrive_home_window: tuple[float, float] | None = None
    # 人物声明的就寝窗口，仅用于人物作息表达；末端可跨午夜。
    # 例如 25.0 表示次日 01:00，与画像层 TimeWindow 口径一致。
    bedtime_window: tuple[float, float] | None = None
    # 场景联系人（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「社交最小可行版」与
    # docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「世界素材品类要求」）：按人物声明，
    # schema 复用 life.social.Contact。
    contacts: tuple[Contact, ...] = ()
    # 初始持有物按地点冻结，由目录独立装配使用属性与品类身份，不要求同时可购买。
    # 空映射表示人物没有场景预置持有物。
    initial_inventory: dict[str, dict[str, float]] = Field(default_factory=dict)

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
        _check_day_windows(self)
        validate_names(self.qualifications, "Agent qualifications")
        ids = [item.contact_id for item in self.contacts]
        if len(ids) != len(set(ids)):
            raise ValueError("agent contact ids must be unique")
        for location_id, holding in self.initial_inventory.items():
            if not location_id.strip() or not holding:
                raise ValueError("initial inventory locations and holdings must not be empty")
            if any(not object_id.strip() for object_id in holding):
                raise ValueError("initial inventory object ids must not be empty")
            if any(not math.isfinite(quantity) or quantity <= 0 for quantity in holding.values()):
                raise ValueError("initial inventory quantities must be positive finite numbers")
        return self


class InventoryItemConfig(FrozenModel):
    """@brief 可被持有和使用的已解析对象语义，不要求它在场景中可购买。"""

    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    serving_quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    serving_unit: str | None = None
    inventory_unit: str | None = None
    energy_kcal_per_unit: float = Field(default=0, ge=0, allow_inf_nan=False)
    use_duration_seconds: int = Field(gt=0)
    use_modes: tuple[str, ...]
    consumable: bool
    effect: str = Field(min_length=1)
    required_location_id: str | None = None
    source_kind: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_product(self) -> "InventoryItemConfig":
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


class ProductConfig(InventoryItemConfig):
    """@brief 商品使用语义和有来源的价格边界，实际报价由门店供给保存。"""

    purchase_price_fen_min: int = Field(gt=0)
    purchase_price_fen_max: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_price_bounds(self) -> "ProductConfig":
        """@brief 允许固定价，拒绝倒置的商品价格范围。"""
        if self.purchase_price_fen_min > self.purchase_price_fen_max:
            logging.error("Product purchase price bounds are inverted")
            raise ValueError("product purchase price bounds must be ordered")
        return self


class WorkAppointment(FrozenModel):
    """@brief 需要真实预约的活动开始时刻和预约依据。"""

    starts_at: datetime
    reason: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def validate_start(self) -> "WorkAppointment":
        """@brief 预约时刻必须明确时区。"""
        _aware(self.starts_at, "starts_at")
        return self


class WorkTaskConfig(FrozenModel):
    """@brief 一个可见后才能执行和提交的独立工作合同（按人物分配）。

    assignee_agent_id 必填（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「世界素材品类要求」：合同按人分配），引用场景内已声明
    人物（"agent:<id>" 或裸数字 id 两种规范写法，与 A10 ScenarioTaskRef 同名同义）。
    """

    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1, pattern=r"\S")
    assignee_agent_id: str = Field(pattern=r"\S")
    contract_kind: ContractKind
    appointment: WorkAppointment | None
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
        """@brief 截止必须带时区，预约时截止必须晚于开始。"""
        _aware(self.due_at, "due_at")
        if self.appointment is not None and self.due_at <= self.appointment.starts_at:
            logging.error("Work deadline must follow appointment start")
            raise ValueError("work deadline must follow appointment start")
        return self


class RuntimeWorkTask(WorkTaskConfig):
    """@brief 装配出的任务事实，开放时刻仅由通知或预约派生。"""

    available_at: datetime


def runtime_tasks(tasks: tuple, messages: tuple) -> tuple[RuntimeWorkTask, ...]:
    """@brief 普通工作取最早本人通知，预约工作取约定开始。"""
    result = []
    for task in tasks:
        notices = [item for item in messages if item.related_task_id == task.task_id]
        if not notices:
            logging.error("Work task has no covering notification: %s", task.task_id)
            raise ValueError("work task requires a covering notification")
        opening = min(item.sent_at for item in notices) if task.appointment is None else task.appointment.starts_at
        payload = task.model_dump(mode="python", exclude={"available_at"})
        result.append(RuntimeWorkTask.model_validate({**payload, "available_at": opening}))
    return tuple(result)


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
    "podcast_episode",
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
    audio_path: str | None = None
    audio_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    entertainment_relief_per_hour: float = Field(ge=0, le=1)
    experience_label: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_audio(self) -> "ContentItemConfig":
        """@brief 只允许真实播客声明已冻结的本地音轨身份。"""
        has_audio = bool(self.audio_path) and self.audio_sha256 is not None
        if self.kind == "podcast_episode" and not has_audio:
            raise ValueError("podcast episode requires audio path and SHA-256")
        if self.kind != "podcast_episode" and (self.audio_path is not None or self.audio_sha256 is not None):
            raise ValueError("non-podcast content cannot declare podcast audio")
        return self


class GameConfig(FrozenModel):
    """@brief 预生成 TextWorld 游戏的不可变身份。"""

    game_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
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
    """@brief 一次按供给身份登记的到点调价。"""

    change_id: str = Field(min_length=1)
    supply_id: str = Field(min_length=1)
    price_version: str = Field(min_length=1)
    amount_fen: int = Field(gt=0)
    due_at: datetime
    source: DataSource

    @model_validator(mode="after")
    def validate_price_source(self) -> "PriceChangeConfig":
        """@brief 显式调价属于场景事件，禁止沿用原报价的真实来源身份。"""
        if self.source.kind != "scenario":
            logging.error("Scheduled price changes require an explicit scenario source")
            raise ValueError("Scheduled price changes require an explicit scenario source")
        return self


class ProductReleaseConfig(FrozenModel):
    """@brief 一次到点把指定门店供给库存设为明确数量的补货。"""

    release_id: str = Field(min_length=1)
    supply_id: str = Field(min_length=1)
    stock: int = Field(ge=0)
    due_at: datetime
    source: DataSource

    @model_validator(mode="after")
    def validate_release_source(self) -> "ProductReleaseConfig":
        """@brief 补货数量必须明确标记为本次场景假设。"""
        if self.source.kind != "scenario":
            logging.error("Scheduled supply releases require an explicit scenario source")
            raise ValueError("Scheduled supply releases require an explicit scenario source")
        return self


class AccountInstanceConfig(FrozenModel):
    """@brief 服务侧账户实例：账户身份、初始金额与其扮演的目录角色。

    人物账户（buyer/traveler/worker 三个能力角色）由 agents[] 隐式声明，
    不在 accounts[] 中重复；外部锚点账户用 external: 前缀角色声明。
    """

    account_id: str = Field(min_length=1)
    money_fen: int = Field(ge=0)
    role: str = Field(min_length=1)


class ProductReference(FrozenModel):
    """@brief 场景对一个商品定义的唯一引用。"""

    product_id: str = Field(min_length=1)


class BodyInstanceConfig(FrozenModel):
    """@brief 身体三类 level 资源的实例初值与场景层身体常量（娱乐缓解的下限偏好）。"""

    mass_kg: float = Field(gt=0, le=300)
    initial_energy_kcal: float = Field(ge=0)
    initial_sleep_debt_hours: float = Field(ge=0)
    initial_entertainment_need: float = Field(default=0, ge=0, le=1)
    entertainment_relief_preference_floor: float = Field(
        default=DEFAULT_ENTERTAINMENT_RELIEF_PREFERENCE_FLOOR,
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description=(
            "娱乐缓解的下限偏好 f，缺省 0.5，取值开区间 (0, 1)：观看与游戏的实际缓解按有效偏好 "
            "f + (1 − f)·w 调幅度（w 为口味权重），不喜欢的内容也按 f 缓解，排序仍用原始口味权重。"
            "实验假设，敏感区间 0.3–0.7，文献只能确定 0 < f < 1"
            "（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ，"
            "docs/EXPERIMENT.md#experiment-实验假设声明 「实验假设声明」）。"
        ),
    )


class LifeScenario(FrozenModel):
    """@brief 瘦身场景文档：实例数据与目录引用，静态语义全部来自目录。"""

    schema_version: Literal[5]
    scenario_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    random_seed: int
    activity_feedback_seconds: int = Field(ge=60, le=86_400)
    # 决策节律（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）：距上一次实际决策满该秒数触发重估唤醒。
    reconsideration_interval_seconds: int = Field(default=RECONSIDERATION_INTERVAL_SECONDS, gt=0, le=86_400)
    idle_reconsideration_interval_seconds: int = Field(default=10800, gt=0, le=86_400)
    #: 播报节律间隔（docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「决策轮循环与播报节律」）
    #: ：活跃对话距上次报告满该秒数唤醒人物。
    progress_report_interval_seconds: int = Field(default=1800, gt=0)
    #: 同刻决策上限（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「场景参数」）：同一人物同一模拟秒的决策轮数上限。
    max_same_moment_decisions: int = Field(default=3, gt=0)
    # 候选与呈现常量（docs/CONTRACTS.md#contracts-worldmock-接口合同「候选与呈现合同」的「场景常量」一节）。
    # 刻意落在场景层而非 catalog/manifest.json：目录常量的一次增量会触发目录版本 major 变更。
    candidate_presentation_limit: int = Field(default=20, gt=0, le=CANDIDATE_PRESENTATION_LIMIT_MAX)
    candidate_pool_limit: int = Field(default=1000, gt=0, le=CANDIDATE_POOL_LIMIT_MAX)
    # 比例取上开区间：比例 1 意味着全部名额都留给未呈现对象，排序键不再起作用。
    rotation_quota_ratio: float = Field(default=0.15, gt=0, lt=1, allow_inf_nan=False)
    # 无缺省：契约要求每个场景显式声明本次实验的判定阈值（同上）。
    rotation_stale_seconds: int = Field(gt=0, le=31_536_000)
    retrieval_index_threshold: int = Field(default=1000, gt=0, le=CANDIDATE_POOL_LIMIT_MAX)
    corpus_root: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    phone_simulation_config: str | None = Field(default=None, pattern=r"\S")
    phone_simulation_config_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    catalog_version: CatalogVersionPin
    accounts: tuple[AccountInstanceConfig, ...] = Field(min_length=1)
    body: BodyInstanceConfig
    agents: tuple[AgentConfig, ...]
    map_dataset: MapDatasetReference
    locations: tuple[LocationReference, ...]
    interaction_menus_by_location_kind: dict[str, InteractionMenu]
    products: tuple[ProductReference, ...]
    supplies: tuple[SupplyReference, ...]
    tasks: tuple[WorkTaskConfig, ...]
    messages: tuple[MessageConfig, ...]
    contents: tuple[ContentItemConfig, ...]
    games: tuple[GameConfig, ...]
    calendar: CalendarConfig
    bills: tuple[BillConfig, ...] = ()
    payrolls: tuple[PayrollConfig, ...] = ()
    price_changes: tuple[PriceChangeConfig, ...] = ()
    product_releases: tuple[ProductReleaseConfig, ...] = ()
    recipe_output_bindings: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_document(self) -> "LifeScenario":
        """@brief 核对时区、身份唯一性、账户声明和全部跨引用。"""
        if self.idle_reconsideration_interval_seconds < self.reconsideration_interval_seconds:
            logging.getLogger(__name__).error("Idle reconsideration interval must not be shorter than regular interval")
            raise ValueError("idle reconsideration interval must not be shorter than regular interval")
        if (self.phone_simulation_config is None) != (self.phone_simulation_config_sha256 is None):
            raise ValueError("phone simulation config path and hash must be declared together")
        _check_candidate_constants(
            self.candidate_presentation_limit, self.rotation_quota_ratio, self.rotation_stale_seconds
        )
        _aware(self.start_at, "start_at")
        _aware(self.end_at, "end_at")
        zone = _zone(self.timezone)
        if self.end_at <= self.start_at or str(zone) != self.timezone:
            raise ValueError("scenario time interval or timezone is invalid")
        _unique(self.agents, "agent_id")
        _unique(self.locations, "location_id")
        _unique(self.products, "product_id")
        _unique(self.tasks, "task_id")
        _unique(self.messages, "message_id")
        _unique(self.contents, "item_id")
        _unique(self.games, "game_id")
        _unique(self.accounts, "account_id")
        _unique(self.supplies, "supply_id")
        _check_location_references(self.agents, (), (), self.tasks, self.locations)
        targets = {item.item_id for item in self.contents} | {item.game_id for item in self.games}
        _check_catalog_references(self.agents, self.tasks, self.messages, targets)
        self._validate_account_identities()
        self._validate_inventory_references()
        self._validate_recipe_bindings()
        _check_times(zone, self.start_at, self.end_at, self.tasks, self.messages)
        self._validate_schedule_sections(zone)
        return self

    def _validate_inventory_references(self) -> None:
        """@brief 文档层只校验初始持有地点；对象存在性由冻结目录组装校验。"""
        locations = {item.location_id for item in self.locations}
        for agent in self.agents:
            if any(location_id not in locations for location_id in agent.initial_inventory):
                raise ValueError("initial inventory references an unknown location")

    def _validate_recipe_bindings(self) -> None:
        """@brief 配方输出绑定要求非空输出品类身份和对象身份。"""
        if any(
            not category_id.strip() or not object_id.strip()
            for category_id, object_id in self.recipe_output_bindings.items()
        ):
            raise ValueError("recipe output bindings must use non-empty ids")

    def _validate_schedule_sections(self, zone: ZoneInfo) -> None:
        """@brief 四个可选预排段的身份、时区、窗口与文档内跨引用校验。"""
        _unique(self.bills, "bill_id")
        _unique(self.payrolls, "payroll_id")
        _unique(self.price_changes, "change_id")
        _unique(self.product_releases, "release_id")
        validate_price_versions((), self.price_changes)
        declared = {item.account_id for item in self.accounts}
        people = {f"agent:{item.agent_id}" for item in self.agents}
        supply_ids = {item.supply_id for item in self.supplies}
        for section, items in (
            ("bill", self.bills),
            ("payroll", self.payrolls),
            ("price change", self.price_changes),
            ("product release", self.product_releases),
        ):
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
            if change.supply_id not in supply_ids:
                raise ValueError("price change references an unknown supply")
        for release in self.product_releases:
            if release.supply_id not in supply_ids:
                raise ValueError("product release references an unknown supply")

    def _validate_account_identities(self) -> None:
        """@brief 账户必须具备明确身份和角色且不得占用人物账户身份。"""
        people = {f"agent:{item.agent_id}" for item in self.agents}
        if any(not item.account_id.strip() or not item.role.strip() for item in self.accounts):
            raise ValueError("account ids and roles must not be blank")
        if any(account.account_id in people for account in self.accounts):
            raise ValueError("service accounts must not reuse agent account ids")


class RuntimeScenario(FrozenModel):
    """@brief 组装后的运行时场景：实例数据加上经目录解析的全部静态语义。

    由 ScenarioSemantics 从瘦身文档与冻结目录组装，不可从 JSON 直接构造；
    领域模块（commerce/travel/work/rules/body）消费的全部静态语义都在此处。
    """

    schema_version: Literal[5]
    scenario_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    random_seed: int
    activity_feedback_seconds: int = Field(ge=60, le=86_400)
    max_same_moment_decisions: int = Field(gt=0)
    # 候选与呈现常量：由 _runtime_payload 从瘦身文档原样搬运（docs/CONTRACTS.md#contracts-worldmock-接口合同「场景常量」）。
    candidate_presentation_limit: int = Field(gt=0, le=CANDIDATE_PRESENTATION_LIMIT_MAX)
    candidate_pool_limit: int = Field(gt=0, le=CANDIDATE_POOL_LIMIT_MAX)
    rotation_quota_ratio: float = Field(gt=0, lt=1, allow_inf_nan=False)
    rotation_stale_seconds: int = Field(gt=0, le=31_536_000)
    retrieval_index_threshold: int = Field(gt=0, le=CANDIDATE_POOL_LIMIT_MAX)
    corpus_root: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    phone_simulation_config: str | None = Field(default=None, pattern=r"\S")
    phone_simulation_config_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    map_dataset: MapDataset
    body: BodyConfig
    agents: tuple[AgentConfig, ...]
    locations: tuple[LocationConfig, ...]
    interaction_menus_by_location_kind: dict[str, InteractionMenu]
    routes: tuple[RouteConfig, ...]
    products: tuple[ProductConfig, ...]
    inventory_items: tuple[InventoryItemConfig, ...] = ()
    supplies: tuple[SupplyConfig, ...]
    tasks: tuple[RuntimeWorkTask, ...]
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
    recipe_output_bindings: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scenario(self) -> "RuntimeScenario":
        """@brief 核对时区、身份唯一性和全部跨引用。"""
        if (self.phone_simulation_config is None) != (self.phone_simulation_config_sha256 is None):
            raise ValueError("phone simulation config path and hash must be declared together")
        _check_candidate_constants(
            self.candidate_presentation_limit, self.rotation_quota_ratio, self.rotation_stale_seconds
        )
        _aware(self.start_at, "start_at")
        _aware(self.end_at, "end_at")
        zone = _zone(self.timezone)
        if self.end_at <= self.start_at or str(zone) != self.timezone:
            raise ValueError("scenario time interval or timezone is invalid")
        _unique(self.agents, "agent_id")
        _unique(self.locations, "location_id")
        _unique(self.routes, "route_id")
        _unique(self.products, "product_id")
        _unique(self.inventory_items, "product_id")
        _unique(self.tasks, "task_id")
        _unique(self.messages, "message_id")
        _unique(self.contents, "item_id")
        _unique(self.games, "game_id")
        _check_interaction_menu_location_kinds(self.locations, self.interaction_menus_by_location_kind)
        _unique(self.supplies, "supply_id")
        validate_price_versions(self.supplies, self.price_changes)
        places = [item.location_id for item in self.supplies]
        known = {item.location_id for item in self.locations}
        if any(item.required_location_id not in known for item in self.products if item.required_location_id):
            raise ValueError("product use requirement references an unknown location")
        _check_location_references(self.agents, self.routes, places, self.tasks, self.locations)
        targets = {item.item_id for item in self.contents} | {item.game_id for item in self.games}
        _check_catalog_references(self.agents, self.tasks, self.messages, targets)
        self._validate_inventory_references()
        if any(
            not category_id.strip() or not object_id.strip()
            for category_id, object_id in self.recipe_output_bindings.items()
        ):
            raise ValueError("recipe output bindings must use non-empty ids")
        self._validate_route_keys()
        self._validate_accounts()
        _check_times(zone, self.start_at, self.end_at, self.tasks, self.messages)
        return self

    def _validate_inventory_references(self) -> None:
        """@brief 运行视图中的初始持有物必须具有完整商品语义。"""
        locations = {item.location_id for item in self.locations}
        products = {item.product_id for item in (*self.products, *self.inventory_items)}
        if {item.product_id for item in self.products} & {item.product_id for item in self.inventory_items}:
            raise ValueError("purchasable products and inventory-only items must not overlap")
        for agent in self.agents:
            if any(location_id not in locations for location_id in agent.initial_inventory):
                raise ValueError("initial inventory references an unknown location")
            held = {object_id for rows in agent.initial_inventory.values() for object_id in rows}
            if not held <= products:
                raise ValueError("initial inventory references an undeclared scenario product")

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
        merchants = {supply.seller_account_id for supply in self.supplies}
        services = {self.employer_account_id, self.transport_account_id}
        if any(not account.strip() for account in services | merchants):
            raise ValueError("account ids must not be blank")
        if len(services) != 2 or services & people or services & merchants or people & merchants:
            raise ValueError("account roles must use distinct account ids")


def _check_location_references(agents, routes, supply_locations, tasks, locations) -> None:
    """@brief 确保住处、联系人收货地点、路线、供给与任务地点引用已声明地点。"""
    known = {item.location_id for item in locations}
    if any(agent.home_location_id not in known for agent in agents):
        raise ValueError("agent home is not a declared location")
    if any(contact.receive_location_id not in {None, *known} for agent in agents for contact in agent.contacts):
        raise ValueError("contact receive location is not a declared location")
    if any(route.origin_id not in known or route.destination_id not in known for route in routes):
        raise ValueError("route references an unknown location")
    if any(location_id not in known for location_id in supply_locations):
        logging.error("Supply references an unknown location")
        raise ValueError("supply references an unknown location")
    if any(task.required_location_id not in known for task in tasks):
        raise ValueError("work task references an unknown location")


def _check_interaction_menu_location_kinds(
    locations: tuple[LocationConfig, ...],
    menus: dict[str, InteractionMenu],
) -> None:
    """@brief 要求地点类菜单键集与场景中实际地点类精确一致。"""
    expected = {item.kind for item in locations}
    actual = set(menus)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        message = (
            "interaction menu location kinds must exactly match declared location kinds: "
            f"missing={missing}, unexpected={unexpected}"
        )
        logging.error(message)
        raise ValueError(message)


def _check_catalog_references(agents, tasks, messages, targets) -> None:
    """@brief 确保任务技能、任务受理人、邮件收件人引用场景内实体。

    随机偏好浮点字段及其条目引用校验已随docs/dev/SPEC-life-realism.md「画像编译」整体删除；
    targets 参数保留供调用方组装内容目录。
    """
    agents_by_id = {item.agent_id for item in agents}
    task_ids = {item.task_id for item in tasks}
    skills = {key for agent in agents for key in agent.skill_levels}
    assignees = {f"agent:{item.agent_id}" for item in agents} | {str(item.agent_id) for item in agents}
    if any(task.required_skill not in skills for task in tasks):
        raise ValueError("work task references an unknown skill")
    if any(task.assignee_agent_id not in assignees for task in tasks):
        raise ValueError("work task references an unknown assignee agent")
    if any(message.recipient_agent_id not in agents_by_id for message in messages):
        raise ValueError("message references an unknown agent")
    if any(message.related_task_id not in task_ids for message in messages if message.related_task_id):
        logging.error("Message references an unknown work task")
        raise ValueError("message references an unknown task")
    by_id = {task.task_id: task for task in tasks}
    for message in messages:
        if not message.related_task_id:
            continue
        task = by_id[message.related_task_id]
        recipient = int(task.assignee_agent_id.removeprefix("agent:"))
        if message.recipient_agent_id != recipient:
            logging.error("Work notification recipient differs from the task assignee")
            raise ValueError("work notification recipient must match the task assignee")
        if task.title not in message.subject:
            logging.error("Work notification does not name its full task title")
            raise ValueError("work notification must name its full task title")
    runtime_tasks(tasks, messages)


def _check_times(zone, start_at, end_at, tasks, messages) -> None:
    """@brief 场景事件时刻必须属于声明时区和有效运行窗口。"""
    values = (start_at, end_at)
    if any(not _matches_zone(value, zone) for value in values):
        raise ValueError("scenario timestamps do not match the declared timezone")
    for task in runtime_tasks(tasks, messages):
        if not (_matches_zone(task.available_at, zone) and _matches_zone(task.due_at, zone)):
            logging.error("Work task timestamps do not match the declared timezone")
            raise ValueError("work task timestamps do not match the declared timezone")
        if not task.available_at < task.due_at <= end_at or task.due_at <= start_at:
            logging.error("Work task is outside the scenario interval")
            raise ValueError("work task is outside the scenario interval")
        if task.appointment is not None:
            earliest = min(item.sent_at for item in messages if item.related_task_id == task.task_id)
            if earliest > task.available_at:
                logging.error("Appointment notification must arrive no later than the start")
                raise ValueError("appointment notification must arrive no later than the start")
    for message in messages:
        if not _matches_zone(message.sent_at, zone) or message.sent_at >= end_at:
            raise ValueError("message timestamp is invalid for the scenario")


def _check_candidate_constants(presentation_limit: int, quota_ratio: float, stale_seconds: int) -> None:
    """@brief 候选常量必须让轮转名额与判定阈值都真的生效。

    单次呈现上限与换新比例的组合下界：`floor(上限 × 比例)` 至少 1，否则轮转
    名额恒为零，"长期未呈现的对象一定会被轮到"这条纪律无法成立（同一次呈现里
    名额被排序键挤占，也是同一类失败）。`allow_inf_nan=False` 在字段层已拒绝
    `NaN` 与无穷，这里只再确认比例落在开区间下界之上。
    """
    if not math.isfinite(quota_ratio) or quota_ratio <= 0 or quota_ratio >= 1:
        raise ValueError("rotation_quota_ratio must be a finite value in (0, 1)")
    if math.floor(presentation_limit * quota_ratio) < 1:
        raise ValueError("candidate_presentation_limit and rotation_quota_ratio leave no rotation quota")
    if stale_seconds <= 0:
        raise ValueError("rotation_stale_seconds must be positive")


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


def _check_day_windows(agent: "AgentConfig") -> None:
    """@brief 人物档案的日内窗口：午餐、晚餐、下班、到家窗口满足 0 ≤ 起点 < 末端 ≤ 24，午餐与晚餐不重叠，就寝窗口可跨
    午夜且不压晚餐；下班与到家都声明时下班起点不晚于到家起点（起点相等可以），与午餐、晚餐、就寝不设先后
    （docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）。"""
    for name, window in (
        ("lunch_window", agent.lunch_window),
        ("dinner_window", agent.dinner_window),
        ("leave_work_window", agent.leave_work_window),
        ("arrive_home_window", agent.arrive_home_window),
    ):
        if window is not None and not 0 <= window[0] < window[1] <= 24:
            raise ValueError(f"{name} must satisfy 0 <= start < end <= 24")
    if agent.lunch_window is not None and agent.dinner_window is not None:
        if agent.lunch_window[1] > agent.dinner_window[0] and agent.dinner_window[1] > agent.lunch_window[0]:
            raise ValueError("dinner window must not overlap lunch window")
    if agent.bedtime_window is not None:
        bedtime_start, bedtime_end = agent.bedtime_window
        # 跨午夜口径与画像层 TimeWindow 一致：start < 24、end ≤ 30（次日
        # 06:00）、跨度 ≤ 12h（防病态区间）；引擎侧另钳制锚点 ≤ 当日结束。
        if not 0 <= bedtime_start < 24 or not bedtime_start < bedtime_end <= 30:
            raise ValueError("bedtime_window must satisfy 0 <= start < 24 and start < end <= 30")
        if bedtime_end - bedtime_start > 12:
            raise ValueError("bedtime_window span must not exceed 12 hours")
        if agent.dinner_window is not None and bedtime_start < agent.dinner_window[1]:
            raise ValueError("bedtime window must not overlap dinner window")
    if (
        agent.leave_work_window is not None
        and agent.arrive_home_window is not None
        and agent.leave_work_window[0] > agent.arrive_home_window[0]
    ):
        raise ValueError("leave_work_window must not start after arrive_home_window")


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
        logging.error("Scenario validation failed for %s", source)
        raise IntegrityError("Scenario validation failed") from error
    root = (source.parent / scenario.corpus_root).resolve()
    manifest = root / "manifest.json"
    if not manifest.is_file() or sha256_file(manifest) != scenario.corpus_manifest_sha256:
        raise IntegrityError("Scenario corpus manifest identity is invalid")
    return scenario, hashlib.sha256(content).hexdigest()
