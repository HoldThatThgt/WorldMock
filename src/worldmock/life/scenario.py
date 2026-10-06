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

from pydantic import Field, field_validator, model_validator

from worldmock.catalog.affordances import REFERENCEABLE_RESOURCES, engine_affordances
from worldmock.errors import IntegrityError
from worldmock.life.felt import APPETITE_STARVING_KCAL
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.base_models import DeepFrozenModel
from worldmock.catalog.models import App, Content, Food, Location, WorkTopic
from worldmock.exchange.types import LocationKind
from worldmock.life.models import ContractKind
from worldmock.life.social import Contact
from worldmock.life.sleep import (
    DEFAULT_CHRONOTYPE_MIDSLEEP_H,
    DEFAULT_SLEEP_NEED_H,
    SLEEP_NEED_MAX_H,
    SLEEP_NEED_MIN_H,
)

#: 重估间隔缺省（秒）：Kleitman 基本休息—活动周期约 90
#: 分钟（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「决策节律：结算边界与唤醒条件分离」）。
RECONSIDERATION_INTERVAL_SECONDS = 5400

#: 候选与呈现常量的合法上界。上界的作用是拦住让两侧口径失效的取值，不是推荐值：
#: 单次呈现上限 1000 已远超一次模型调用能承载的条目数；检索分区池上限 100_000 之外的
#: 池在 15% 换新名额（每次 3 条）下最坏等待约 1.5 年，已超出正式实验窗口，
#: 闭式断言会退化成不断言（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「候选契约与呈现规则」「两级上限」）。
CANDIDATE_PRESENTATION_LIMIT_MAX = 1000
CANDIDATE_POOL_LIMIT_MAX = 100_000

#: 长期未呈现判定阈值的缺省（秒）＝一周。取值理由：轮转按人物的作息周期产生可观测效果，
#: 而作息以周为节律；单池 1000 条、每次呈现 20 条时换新名额 k = floor(20 × 0.15) = 3，
#: 实测每天决策点中位 71、范围 9–112.5（.work/object-model-reanalysis/REPORT.md 2.8），
#: 故最坏未呈现间隔闭式上界 ⌈N ÷ k⌉ ÷ D 约 2.96–37 天、中位约 4.7 天，
#: 一周落在该区间内且略高于中位数，使轮转名额在一个人物的一周内必然被消耗一轮。
DEFAULT_ROTATION_STALE_SECONDS = 604_800


class BodyConfig(DeepFrozenModel):
    """@brief 身体实例初值与 WorldMock 固定身体规则的运行视图。"""

    mass_kg: float = Field(gt=0, le=300, allow_inf_nan=False)
    initial_energy_kcal: float = Field(ge=0, allow_inf_nan=False)
    initial_sleep_debt_hours: float = Field(ge=0, allow_inf_nan=False)
    initial_entertainment_need: float = Field(ge=0, le=1, allow_inf_nan=False)
    maximum_energy_kcal: float = Field(gt=0, allow_inf_nan=False)
    basal_kcal_per_hour: float = Field(gt=0, allow_inf_nan=False)
    entertainment_need_per_awake_hour: float = Field(ge=0, le=1, allow_inf_nan=False)
    hunger_threshold_kcal: float = Field(ge=0, allow_inf_nan=False)
    emergency_threshold_kcal: float = Field(ge=0, allow_inf_nan=False)
    activity_met: dict[str, float]
    entertainment_relief_per_hour: dict[str, float]
    source_kind: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_body(self) -> "BodyConfig":
        """@brief 初始能量与固定体感阈值必须在系统能量上限内。"""
        if self.initial_energy_kcal > self.maximum_energy_kcal:
            _invalid("initial energy exceeds maximum energy")
        if not APPETITE_STARVING_KCAL < self.emergency_threshold_kcal < self.hunger_threshold_kcal:
            _invalid("body thresholds must be ordered above the starving floor")
        if self.hunger_threshold_kcal > self.maximum_energy_kcal:
            _invalid("body hunger threshold exceeds maximum energy")
        return self


#: 大五特质与权重的固定键集合（与画像编译器输出合同一致）。
_BIG_FIVE_TRAITS = frozenset({"openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"})
_MOTIVATION_WEIGHTS = frozenset({"money_fen", "energy_kcal", "sleep_debt_hours", "entertainment_need", "goal_task"})


class MotivationProfileSource(DeepFrozenModel):
    """@brief 档案编译溯源：输入文本哈希、模型、提示版本与共识统计。"""

    persona_text_sha256: str = Field(min_length=8)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    consensus: dict[str, float | int]


class MotivationProfile(DeepFrozenModel):
    """@brief 人物动机档案：动机评分层求值所需的全部个体参数。

    形状与画像编译器的 agents[].motivation 输出合同钉死一致。
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
    source: MotivationProfileSource | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> "MotivationProfile":
        """@brief 核对封闭键集、取值区间与权重归一。"""
        if set(self.big_five) != _BIG_FIVE_TRAITS:
            _invalid("motivation big_five must use exactly the five trait keys")
        # 钉死合同样例含 conscientiousness=6（超出经典 5 点 Likert），上界放宽到 10。
        if any(not isinstance(value, int) or not 1 <= value <= 10 for value in self.big_five.values()):
            _invalid("motivation big_five values must be integers between 1 and 10")
        if set(self.weights) != _MOTIVATION_WEIGHTS:
            _invalid("motivation weights must use exactly the five resource keys")
        if any(not math.isfinite(value) or value < 0 for value in self.weights.values()):
            _invalid("motivation weights must be non-negative finite numbers")
        if abs(sum(self.weights.values()) - 1) > 1e-6:
            _invalid("motivation weights must sum to one")
        if not self.loss_aversion or not set(self.loss_aversion) <= REFERENCEABLE_RESOURCES:
            _invalid("motivation loss_aversion keys must be a non-empty level resource subset")
        if any(not math.isfinite(value) or value < 1 for value in self.loss_aversion.values()):
            _invalid("motivation loss_aversion values must be at least one")
        if set(self.comfort) != {"balance_fen", "energy_kcal"}:
            _invalid("motivation comfort must use exactly balance_fen and energy_kcal")
        if any(not math.isfinite(value) or value < 0 for value in self.comfort.values()):
            _invalid("motivation comfort values must be non-negative finite numbers")
        if set(self.aspiration) != {"daily_work_seconds"}:
            _invalid("motivation aspiration must use exactly daily_work_seconds")
        if any(value < 0 for value in self.aspiration.values()):
            _invalid("motivation aspiration must be non-negative")
        return self


class WorkShift(DeepFrozenModel):
    """@brief 一天的班次（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：自当地零点起的分钟，同日、starts < ends，休息时段落在
    班次内、升序且互不重叠；区间一律半开。三个键都必填，没有休息时段写空列表。"""

    starts: int
    ends: int
    breaks: tuple[tuple[int, int], ...]

    @model_validator(mode="after")
    def validate_shift(self) -> "WorkShift":
        """@brief 拒绝跨日或倒置的班次、越出班次的休息时段，以及乱序或重叠的休息时段。"""
        if not 0 <= self.starts < self.ends <= 1440:
            _invalid("work shift must satisfy 0 <= starts < ends <= 1440")
        if any(not self.starts <= begin < finish <= self.ends for begin, finish in self.breaks):
            _invalid("work shift breaks must lie within the shift")
        if any(previous[1] > following[0] for previous, following in zip(self.breaks, self.breaks[1:])):
            _invalid("work shift breaks must be ascending and must not overlap")
        return self


class WorkSchedule(DeepFrozenModel):
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
            _invalid("work_schedule weekly must declare all seven weekdays")
        for key in self.exceptions:
            try:
                canonical = date.fromisoformat(key).isoformat() == key
            except ValueError:
                canonical = False
            if not canonical:
                _invalid("work_schedule exception dates must use canonical ISO format")
        return self


class AgentConfig(DeepFrozenModel):
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
    work_topic_ids: tuple[str, ...] = ()
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
        if self.background_story is not None and len(self.background_story.strip()) < 100:
            _invalid("background_story must contain at least 100 characters")
        if self.lifestyle_activities is not None:
            if not self.lifestyle_activities:
                _invalid("lifestyle_activities must list ids or stay null to enable all")
            if len(set(self.lifestyle_activities)) != len(self.lifestyle_activities):
                _invalid("lifestyle_activities ids must be unique")
            if any(not item.strip() for item in self.lifestyle_activities):
                _invalid("lifestyle_activities ids must not be blank")
        _check_day_windows(self)
        if len(set(self.work_topic_ids)) != len(self.work_topic_ids) or any(
            not topic.strip() for topic in self.work_topic_ids
        ):
            _invalid("work topic ids must be unique non-empty names")
        ids = [item.contact_id for item in self.contacts]
        if len(ids) != len(set(ids)):
            _invalid("agent contact ids must be unique")
        for location_id, holding in self.initial_inventory.items():
            if not location_id.strip() or not holding:
                _invalid("initial inventory locations and holdings must not be empty")
            if any(not object_id.strip() for object_id in holding):
                _invalid("initial inventory object ids must not be empty")
            if any(not math.isfinite(quantity) or quantity <= 0 for quantity in holding.values()):
                _invalid("initial inventory quantities must be positive finite numbers")
        return self


class WorkAppointment(DeepFrozenModel):
    """@brief 需要真实预约的活动开始时刻和预约依据。"""

    starts_at: datetime
    reason: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def validate_start(self) -> "WorkAppointment":
        """@brief 预约时刻必须明确时区。"""
        _aware(self.starts_at, "starts_at")
        return self


class WorkTaskConfig(DeepFrozenModel):
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
            _invalid("work deadline must follow appointment start")
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
            _invalid("work task requires a covering notification")
        opening = min(item.sent_at for item in notices) if task.appointment is None else task.appointment.starts_at
        payload = task.model_dump(mode="python", exclude={"available_at"})
        result.append(RuntimeWorkTask.model_validate({**payload, "available_at": opening}))
    return tuple(result)


class MessageConfig(DeepFrozenModel):
    """@brief 按场景时钟到达的一封不可变邮件。"""

    message_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    recipient_agent_id: int = Field(gt=0)
    sent_at: datetime
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    attachment_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_time(self) -> "MessageConfig":
        """@brief 邮件到达时刻必须明确时区。"""
        _aware(self.sent_at, "sent_at")
        return self


class CalendarConfig(DeepFrozenModel):
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
                _invalid("calendar date lists must not contain duplicates")
            for value in values:
                _validate_date(value)
        for value in (self.sick_leave_date, self.personal_leave_date, self.social_dinner_date):
            _validate_date(value)
        return self


class BillConfig(DeepFrozenModel):
    """@brief 一条到点从付款账户向收款账户扣款的账单（房租/水电类）。"""

    bill_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    payee_account_id: str = Field(min_length=1)
    amount_fen: int = Field(gt=0)
    due_at: datetime


class PayrollConfig(DeepFrozenModel):
    """@brief 一条到点从雇主账户向人物账户入账的固定金额工资。"""

    payroll_id: str = Field(min_length=1)
    employer_account_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    amount_fen: int = Field(gt=0)
    due_at: datetime


class AccountInstanceConfig(DeepFrozenModel):
    """@brief 服务侧账户实例：账户身份、初始金额与其扮演的目录角色。

    人物账户（buyer/traveler/worker 三个能力角色）由 agents[] 隐式声明，
    不在 accounts[] 中重复；外部锚点账户用 external: 前缀角色声明。
    """

    account_id: str = Field(min_length=1)
    money_fen: int = Field(ge=0)
    role: str = Field(min_length=1)


class EnvironmentPin(DeepFrozenModel):
    """@brief 场景明确选择的环境发布身份。"""

    environment_id: str = Field(min_length=1, pattern=r"\S")
    environment_version: str = Field(min_length=1, pattern=r"\S")


class LocationReference(DeepFrozenModel):
    """@brief 场景选入的登记地点身份。"""

    location_id: str = Field(min_length=1, pattern=r"\S")


class BodyInstanceConfig(DeepFrozenModel):
    """@brief 场景只声明身体质量和三种需要的初值。"""

    mass_kg: float = Field(gt=0, le=300, allow_inf_nan=False)
    initial_energy_kcal: float = Field(ge=0, allow_inf_nan=False)
    initial_sleep_debt_hours: float = Field(ge=0, allow_inf_nan=False)
    initial_entertainment_need: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)


class LifeScenario(DeepFrozenModel):
    """@brief 结构七场景：人物实例、环境选择与保留的日历事件。"""

    schema_version: Literal[7]
    scenario_id: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    random_seed: int = Field(strict=True)
    activity_feedback_seconds: int = Field(ge=60, le=86_400)
    reconsideration_interval_seconds: int = Field(default=RECONSIDERATION_INTERVAL_SECONDS, gt=0, le=86_400)
    idle_reconsideration_interval_seconds: int = Field(default=10800, gt=0, le=86_400)
    progress_report_interval_seconds: int = Field(default=1800, gt=0)
    max_same_moment_decisions: int = Field(default=3, gt=0)
    candidate_presentation_limit: int = Field(default=20, gt=0, le=CANDIDATE_PRESENTATION_LIMIT_MAX)
    candidate_pool_limit: int = Field(default=1000, gt=0, le=CANDIDATE_POOL_LIMIT_MAX)
    rotation_quota_ratio: float = Field(default=0.15, gt=0, lt=1, allow_inf_nan=False)
    rotation_stale_seconds: int = Field(gt=0, le=31_536_000)
    corpus_root: str = Field(min_length=1)
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: EnvironmentPin
    accounts: tuple[AccountInstanceConfig, ...] = Field(min_length=1)
    body: BodyInstanceConfig
    agents: tuple[AgentConfig, ...] = Field(min_length=1)
    locations: tuple[LocationReference, ...] = Field(min_length=1)
    interaction_menus_by_location_kind: dict[LocationKind, InteractionMenu]
    messages: tuple[MessageConfig, ...] = ()
    calendar: CalendarConfig
    bills: tuple[BillConfig, ...] = ()
    payrolls: tuple[PayrollConfig, ...] = ()

    @model_validator(mode="after")
    def validate_document(self) -> "LifeScenario":
        """@brief 校验身份、人物地点、菜单、账户、时区与保留事件的跨引用。"""
        if self.idle_reconsideration_interval_seconds < self.reconsideration_interval_seconds:
            _invalid("idle reconsideration interval must not be shorter than regular interval")
        _check_candidate_constants(
            self.candidate_presentation_limit, self.rotation_quota_ratio, self.rotation_stale_seconds
        )
        _aware(self.start_at, "start_at")
        _aware(self.end_at, "end_at")
        zone = _zone(self.timezone)
        if (self.end_at <= self.start_at
                or not all(_matches_zone(value, zone) for value in (self.start_at, self.end_at))):
            _invalid("scenario time interval or timezone is invalid")
        for rows, field in ((self.agents, "agent_id"), (self.locations, "location_id"),
                            (self.accounts, "account_id"), (self.messages, "message_id")):
            _unique(rows, field)
        _check_document_references(self)
        _check_menu_actions(self)
        _check_account_identities(self)
        _check_event_sections(self, zone)
        return self


class RuntimeScenario(LifeScenario):
    """@brief 经注册目录与固定规则组装的结构七运行场景。"""

    body: BodyConfig
    locations: tuple[Location, ...]
    apps: tuple[App, ...]
    foods: tuple[Food, ...]
    contents: tuple[Content, ...]
    work_topics: tuple[WorkTopic, ...]
    employer_account_id: str | None
    transport_account_id: str


def _invalid(message: str) -> None:
    """@brief 在模型拒绝异常分支记录英文原因。"""
    logging.error(message)
    raise ValueError(message)


def _check_document_references(document: LifeScenario) -> None:
    """@brief 人物住处、持有地点、联系人收货地和邮件收件人必须已经选入。"""
    places = {item.location_id for item in document.locations}
    people = {item.agent_id for item in document.agents}
    for agent in document.agents:
        if agent.home_location_id not in places:
            _invalid("agent home is not a declared location")
        if not set(agent.initial_inventory) <= places:
            _invalid("initial inventory references an unknown location")
        if any(contact.receive_location_id not in {None, *places} for contact in agent.contacts):
            _invalid("contact receive location is not a declared location")
    if any(item.recipient_agent_id not in people for item in document.messages):
        _invalid("message references an unknown agent")


def _check_menu_actions(document: LifeScenario) -> None:
    """@brief 八类地点菜单必须完整，动作仅允许系统当前固定闭集。"""
    if set(document.interaction_menus_by_location_kind) != set(LocationKind):
        _invalid("interaction menus must declare all fixed location kinds")


def _check_account_identities(document: LifeScenario) -> None:
    """@brief 声明账户不能覆盖人物账户，角色必须唯一且属于当前账户域。"""
    people = {f"agent:{item.agent_id}" for item in document.agents}
    roles = [item.role for item in document.accounts]
    if len(roles) != len(set(roles)):
        _invalid("account roles must be unique")
    for account in document.accounts:
        if account.account_id in people or not account.account_id.strip():
            _invalid("declared accounts must not reuse agent account ids")
        if not (account.role.startswith(("merchant:", "employer:"))
                or account.role in {"transport:service", "external:scenario"}):
            _invalid("account role is outside the fixed account domain")


def _check_event_sections(document: LifeScenario, zone: ZoneInfo) -> None:
    """@brief 保留的邮件、账单与工资只能引用已有主体和合法场景时刻。"""
    people = {f"agent:{item.agent_id}" for item in document.agents}
    accounts = {item.account_id for item in document.accounts} | people
    _unique(document.bills, "bill_id")
    _unique(document.payrolls, "payroll_id")
    for item in document.messages:
        if not _matches_zone(item.sent_at, zone) or item.sent_at >= document.end_at:
            _invalid("message timestamp is invalid for the scenario")
    for row in (*document.bills, *document.payrolls):
        _aware(row.due_at, "event due_at")
        if not _matches_zone(row.due_at, zone) or not document.start_at < row.due_at < document.end_at:
            _invalid("scheduled event is outside the scenario interval or timezone")
    for bill in document.bills:
        if bill.account_id == bill.payee_account_id or not {bill.account_id, bill.payee_account_id} <= accounts:
            _invalid("bill references invalid payer or payee accounts")
    for payroll in document.payrolls:
        if payroll.employer_account_id not in accounts or f"agent:{payroll.agent_id}" not in people:
            _invalid("payroll references an undeclared employer or person")


def _check_candidate_constants(presentation_limit: int, quota_ratio: float, stale_seconds: int) -> None:
    """@brief 候选常量必须让轮转名额与判定阈值都真的生效。

    单次呈现上限与换新比例的组合下界：`floor(上限 × 比例)` 至少 1，否则轮转
    名额恒为零，"长期未呈现的对象一定会被轮到"这条纪律无法成立（同一次呈现里
    名额被排序键挤占，也是同一类失败）。`allow_inf_nan=False` 在字段层已拒绝
    `NaN` 与无穷，这里只再确认比例落在开区间下界之上。
    """
    if not math.isfinite(quota_ratio) or quota_ratio <= 0 or quota_ratio >= 1:
        _invalid("rotation_quota_ratio must be a finite value in (0, 1)")
    if math.floor(presentation_limit * quota_ratio) < 1:
        _invalid("candidate_presentation_limit and rotation_quota_ratio leave no rotation quota")
    if stale_seconds <= 0:
        _invalid("rotation_stale_seconds must be positive")


def _aware(value: datetime, field_name: str) -> None:
    """@brief 校验日期时间具有有效时区。"""
    if value.tzinfo is None or value.utcoffset() is None:
        _invalid(f"{field_name} must be timezone-aware")


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
            _invalid(f"{name} must satisfy 0 <= start < end <= 24")
    if agent.lunch_window is not None and agent.dinner_window is not None:
        if agent.lunch_window[1] > agent.dinner_window[0] and agent.dinner_window[1] > agent.lunch_window[0]:
            _invalid("dinner window must not overlap lunch window")
    if agent.bedtime_window is not None:
        bedtime_start, bedtime_end = agent.bedtime_window
        # 跨午夜口径与画像层 TimeWindow 一致：start < 24、end ≤ 30（次日
        # 06:00）、跨度 ≤ 12h（防病态区间）；引擎侧另钳制锚点 ≤ 当日结束。
        if not 0 <= bedtime_start < 24 or not bedtime_start < bedtime_end <= 30:
            _invalid("bedtime_window must satisfy 0 <= start < 24 and start < end <= 30")
        if bedtime_end - bedtime_start > 12:
            _invalid("bedtime_window span must not exceed 12 hours")
        if agent.dinner_window is not None and bedtime_start < agent.dinner_window[1]:
            _invalid("bedtime window must not overlap dinner window")
    if (
        agent.leave_work_window is not None
        and agent.arrive_home_window is not None
        and agent.leave_work_window[0] > agent.arrive_home_window[0]
    ):
        _invalid("leave_work_window must not start after arrive_home_window")


def _validate_date(value: str) -> None:
    """@brief 校验规范的 ISO 自然日文本。"""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        logging.error("Calendar date must use ISO format")
        raise ValueError("calendar date must use ISO format") from error
    if parsed.isoformat() != value:
        _invalid("calendar date must use canonical ISO format")


def _matches_zone(value: datetime, zone: ZoneInfo) -> bool:
    """@brief 判断带偏移时刻是否匹配声明区域在该时刻的偏移。"""
    return value.utcoffset() == value.astimezone(zone).utcoffset()


def _zone(value: str) -> ZoneInfo:
    """@brief 将未知时区稳定转换为场景校验错误。"""
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        logging.error("Scenario timezone is unknown")
        raise ValueError("scenario timezone is unknown") from error


def _unique(items: tuple, field_name: str) -> None:
    """@brief 校验实体稳定身份不重复。"""
    values = [getattr(item, field_name) for item in items]
    if len(values) != len(set(values)):
        _invalid(f"{field_name} values must be unique")


def sha256_file(path: Path) -> str:
    """@brief 计算完整文件的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_scenario_document(path: Path) -> tuple[LifeScenario, str]:
    """@brief 读取唯一 JSON 场景文档并返回其内容身份。"""
    candidate = Path(path)
    if candidate.is_symlink():
        logging.error("Scenario file is missing or is a symbolic link")
        raise IntegrityError("Scenario file is missing or is a symbolic link")
    source = candidate.resolve()
    if not source.is_file():
        logging.error("Scenario file is missing or is a symbolic link")
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
        logging.error("Scenario corpus manifest identity is invalid")
        raise IntegrityError("Scenario corpus manifest identity is invalid")
    return scenario, hashlib.sha256(content).hexdigest()
