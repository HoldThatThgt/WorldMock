"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.foundation.models import FrozenModel
import re
from worldmock.exchange_engine.conditions.activity_rules import MAIN_ACTIVITY_ACTIONS, SIDE_ACTIVITY_ACTIONS
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.sleep import parse_sleep_minutes
from collections.abc import Sequence
from typing import Annotated, Any, Literal
from pydantic import ValidationError, ConfigDict, Field, WithJsonSchema, field_validator, model_validator
from worldmock.state.planning.errands import (
    ERRAND_NAME_MAX_CHARS,
    ERRAND_QUANTITY_MAX,
    ERRAND_QUANTITY_MIN,
    ERRAND_TEXT_MAX_CHARS,
    MAX_ERRAND_CHANGES,
)
from worldmock.state.planning.schedule import MAX_PLAN_GOALS, PLAN_GOAL_MAX_CHARS, IntentKind

from worldmock.state.world.kinds import ActivityChange
from worldmock.state.interaction.schema import ToolRequestModel, purpose_only_schema
from worldmock.state.logging import _LOGGER


class ChangeActivityRequest(FrozenModel):
    """@brief 已有活动的暂停、恢复或退出请求。"""

    activity_id: str = Field(min_length=1)
    operation: ActivityChange



class PlanGoalInput(FrozenModel):
    """@brief 当日目标条目：意图、目标文字与两个必填可空的场景本地 HH:MM——remind_time（要在那一刻被提醒时填写，
    否则为 null）与 around_time（打算在几点前后做，docs/design/04-simulation-engine.md
    ；不提醒、不要求晚于登记时刻）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema(
        "今日计划的一个目标：意图、目标文字、提醒时刻与大致时刻。"))
    intent_kind: IntentKind
    goal: str = Field(pattern=r"\S")
    remind_time: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")] | None
    around_time: Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")] | None

    @field_validator("goal")
    @classmethod
    def validate_goal_length(cls, goal: str) -> str:
        """@brief 限制目标字符数，同时保持受支持的严格工具 Schema。"""
        if len(goal) > PLAN_GOAL_MAX_CHARS:
            _LOGGER.error("Day goal exceeds its character limit")
            raise ValueError(f"day goal exceeds {PLAN_GOAL_MAX_CHARS} characters")
        return goal



def _validate_plan_goals(goals: Sequence[PlanGoalInput], model_name: str) -> Sequence[PlanGoalInput]:
    """@brief 零点计划与修订两个入口共用的清单校验：条数超限与重复意图在同一次校验里一并报告。

    条数超过八条定位在 goals 本身（原值为提交的各条目）；每个重复的意图同时定位首次出现与其后每次出现的条目
    （goals[i].intent_kind，原值为该意图），按下标升序。不因超限提前返回（docs/design/04-simulation-engine.md）。

    :param goals: 已逐条通过条目校验的目标清单。
    :param model_name: 发起校验的请求模型名，作为校验错误的标题。
    """
    errors: list[Any] = []
    if len(goals) > MAX_PLAN_GOALS:
        _LOGGER.error("Day plan exceeds eight goals")
        errors.append({"type": "value_error", "loc": (), "input": [goal.model_dump(mode="json") for goal in goals],
                       "ctx": {"error": ValueError("day goals require at most eight entries")}})
    positions: dict[str, list[int]] = {}
    for index, goal in enumerate(goals):
        positions.setdefault(goal.intent_kind, []).append(index)
    repeated = sorted((index, kind) for kind, indexes in positions.items() if len(indexes) > 1 for index in indexes)
    for index, kind in repeated:
        message = (f"intent_kind '{kind}' may occur only once; keep one goal for this intent "
                   "and merge these entries into it")
        errors.append({"type": "value_error", "loc": (index, "intent_kind"), "input": kind,
                       "ctx": {"error": ValueError(message)}})
    if repeated:
        _LOGGER.error("Day plan contains duplicate intents")
    if errors:
        raise ValidationError.from_exception_data(model_name, errors)
    return goals



class PlanDayRequest(FrozenModel):
    """@brief 当日有序目标清单：零到八条，同一意图最多一条；零点计划的输出与修订请求共用这一形状。"""

    goals: Annotated[
        tuple[PlanGoalInput, ...],
        WithJsonSchema({
            "type": "array",
            "items": PlanGoalInput.model_json_schema(mode="validation"),
        }),
    ]

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, goals: tuple[PlanGoalInput, ...]) -> tuple[PlanGoalInput, ...]:
        """@brief 零点计划的输出经共用校验核验容量与意图唯一性，超限与重复意图逐条定位、同批报告。"""
        return _validate_plan_goals(goals, cls.__name__)



class ReviseDayPlanRequest(ToolRequestModel):
    """@brief 严格修订请求逐项定位重复意图，已推进目标由环境保留。"""

    goals: Annotated[list[PlanGoalInput], WithJsonSchema(
        {"type": "array", "items": PlanGoalInput.model_json_schema(mode="validation")})]

    @field_validator("goals")
    @classmethod
    def validate_goals(cls, goals: list[PlanGoalInput]) -> list[PlanGoalInput]:
        """@brief 修订请求经同一共用校验核验容量与意图唯一性，超限与重复意图逐条定位、同批报告。"""
        return _validate_plan_goals(goals, cls.__name__)



def _cross_errors(model_name: str, problems: list[tuple[str, Any, str]]) -> ValidationError:
    """@brief 把交叉约束的各条违背写成逐字段定位的校验错误：（字段，所写原值，英文原因）各一条。"""
    _LOGGER.error("Start request violates its cross-field constraints")
    return ValidationError.from_exception_data(model_name, [
        {"type": "value_error", "loc": (field,), "input": written, "ctx": {"error": ValueError(reason)}}
        for field, written, reason in problems])



class StartMainActivityRequest(ToolRequestModel):
    """@brief 开始一项占槽位 1 的活动：出行、在当前地点做一件事，或睡觉。"""

    action: Literal[MAIN_ACTIVITY_ACTIONS] = Field(
        description="做什么：travel 出行，interact 在当前地点做一件事，sleep 睡觉。")
    target: str = Field(pattern=r"\S", description=(
        "travel 写目的地地点的完整名称；interact 写当前所在地点的完整名称；sleep 写要睡的分钟数，只写数字，例如 470。"))
    detail: Annotated[str, Field(pattern=r"\S")] | None = Field(description=(
        "travel 写出行方式的名称，照工具层该目的地下列出的写；interact 写要做的那件事的名称，"
        "照工具层当前地点下列出的写；sleep 写 null。"))

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "StartMainActivityRequest":
        """@brief 出行与做事必须写 detail；睡眠的 target 是 10 到 720 的分钟数、detail 必须为 null，各条违背各报一条。"""
        problems: list[tuple[str, Any, str]] = []
        if self.action != "sleep":
            if self.detail is None:
                problems.append(("detail", None, f"detail is required when action is {self.action}"))
        else:
            try:
                parse_sleep_minutes(self.target)
            except ValueError as error:
                problems.append(("target", self.target, str(error)))
            if self.detail is not None:
                problems.append(("detail", self.detail, "detail must be null when action is sleep"))
        if problems:
            raise _cross_errors(type(self).__name__, problems)
        return self



class StartSideActivityRequest(ToolRequestModel):
    """@brief 开始一项占槽位 2 的活动：购买商品、查询商品，或使用一个内容 app。"""

    action: Literal[SIDE_ACTIVITY_ACTIONS] = Field(
        description="做什么：purchase 购买商品，interact 使用内容 app，query 查询商品。")
    target: list[Annotated[str, Field(pattern=r"\S")]] = Field(description=(
        "名称数组。purchase 逐份写商品的完整名称，同一个名称写两次就是两份；interact 只写一个内容 app 的完整名称；"
        "query 写商品的完整名称或商品类别的名称。"))
    #: 想用来下单或在其中查询的 app 名称原文，必填、没有缺省值；没有想法写 null（模型的工具格式不允许可省略的字段）。
    prefer: Annotated[str, Field(pattern=r"\S")] | None = Field(
        description="purchase 写想用来下单的内容 app 的完整名称，query 写想在其中查询的内容 app 的完整名称，"
                    "没有想法写 null；interact 写 null。")

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "StartSideActivityRequest":
        """@brief 名称数组至少一项；用内容 app 时恰好一项且 prefer 必须为 null；购买与查询不设上限并允许同名重复。
        每条违背各报一条。"""
        problems: list[tuple[str, Any, str]] = []
        if not self.target:
            problems.append(("target", self.target, "target requires at least one name"))
        if self.action == "interact" and len(self.target) != 1:
            problems.append(("target", self.target, "target takes exactly one app name when action is interact"))
        if self.action == "interact" and self.prefer is not None:
            problems.append(("prefer", self.prefer, "prefer must be null when action is interact"))
        if problems:
            raise _cross_errors(type(self).__name__, problems)
        return self



class RecallTaskSummaryRequest(ToolRequestModel):
    """@brief 按本轮可见的任务、当日目标或活动名称只读回看进展（会话名不在七层中出现，不可回看）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    task_name: str = Field(pattern=r"\S")


#: 检索记忆关键词的上限：去掉首尾空白后的字数与按空白拆开的词数（DeepSeek strict 禁 maxLength，由本地校验执行）。
MEMORY_KEYWORD_MAX_CHARS = 40
MEMORY_KEYWORD_MAX_WORDS = 4


class SearchMemoryRequest(ToolRequestModel):
    """@brief 按类别和时间查自己过去的记忆，可以加关键词，不改变世界。"""

    category: Literal["饮食", "工作", "生活", "全部"]
    time: str = Field(pattern=r"^(今天( \d{2}:\d{2}(~\d{2}:\d{2})?)?|全部|\d{4}(-\d{2}(-\d{2}(~\d{4}-\d{2}-\d{2})?)?)?)$")
    keyword: str | None

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, keyword: str | None) -> str | None:
        """@brief 关键词去掉首尾空白后非空、至多 40 字、按空白拆开至多 4 个词（DeepSeek strict 禁 maxLength，由本地校验兜住）。"""
        if keyword is None:
            return None
        words = keyword.split()
        if not words:
            _LOGGER.error("Memory search keyword is blank")
            raise ValueError("memory search keyword must contain a word or be null")
        if len(keyword.strip()) > MEMORY_KEYWORD_MAX_CHARS:
            _LOGGER.error("Memory search keyword exceeds its character limit")
            raise ValueError("memory search keyword exceeds 40 characters")
        if len(words) > MEMORY_KEYWORD_MAX_WORDS:
            _LOGGER.error("Memory search keyword has too many words")
            raise ValueError("memory search keyword has more than four words")
        return keyword



#: 控制工具每项活动名的「格式与来源」说明。字段级说明是参数 Schema 的一部分，不是工具 description：只写格式与来源，
#: 不写规则与上限（上限由本地校验执行，docs/design/04-simulation-engine.md）。
_ACTIVITY_NAME_FORMAT = "活动名：照工具层、待办层或进度层写出的名称原样抄写，包括「（9月28日 12:05 起）」这样的开始时刻。"



def _validate_control_items(tool: str, items: list[Any]) -> list[Any]:
    """@brief 控制工具 items 的上限：一至两项、活动名互不相同（DeepSeek strict 禁 minItems 与 maxItems，由本地校验
    兜住）；作为 items 的字段校验，协议纠错的原因落在 items 上。"""
    names = [item.activity_name for item in items]
    if not 1 <= len(items) <= 2:
        _LOGGER.error("Activity control request does not carry one or two items")
        raise ValueError(f"{tool} takes one or two items")
    if len(set(names)) != len(names):
        _LOGGER.error("Activity control request names the same activity twice")
        duplicates = [{"type": "value_error", "loc": (index, "activity_name"), "input": name,
                       "ctx": {"error": ValueError(f"{tool} activity names must be distinct")}}
                      for index, name in enumerate(names) if name in names[:index]]
        raise ValidationError.from_exception_data(tool, duplicates)
    return items



def _validate_summary(summary: str) -> str:
    """@brief 自述不超过 500 字符（DeepSeek strict 禁 maxLength，由本地校验兜住；非空白由 Schema 的 pattern 约束）。"""
    if len(summary) > 500:
        _LOGGER.error("Activity control summary exceeds its character limit")
        raise ValueError("must be within 500 characters")
    return summary



#: HH:MM 钟点的 Schema 格式（与当日目标的 remind_time 同名同形）。
_CLOCK_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"

#: 个人事务变更的交叉约束（docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」）：按变更种类必须非空的键与必须为 null 的键；
#: 其余键（新增的 memo、remind_time，更新的 memo、remind_time）可空。
_ERRAND_REQUIRED = {"新增": ("item", "quantity"), "更新": (), "取消": ("reason",)}

_ERRAND_NULL = {"新增": ("reason",), "更新": ("item", "quantity", "reason"),
                "取消": ("item", "quantity", "memo", "remind_time")}



def _errand_text(value: str | None, limit: int, what: str) -> str | None:
    """@brief 事务文字的字符上限（strict 端点禁 maxLength，由本地校验执行，上限只写在原因里）；null 原样通过。

    :param what: 英文原因里的字段说法（errand name、errand memo、errand cancel reason）。
    """
    if value is not None and len(value) > limit:
        _LOGGER.error("Errand change text exceeds its character limit")
        raise ValueError(f"{what} must be within {limit} characters")
    return value



class ErrandChange(ToolRequestModel):
    """@brief 控制工具请求级 errands 的一条个人事务变更：封闭对象，七个键全部必填，
    按变更种类的交叉约束与字符、份数上限由本地校验执行（上限写在原因里，Schema 只给类型与格式）；没有「完成」。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema(
        "一条个人事务的变更：做什么、名称、要办成的事、备注、提醒钟点与取消原因。"))
    change: Literal["新增", "更新", "取消"] = Field(description="这条变更做什么，从列出的三种里选一种。")
    name: str = Field(pattern=r"\S", description="事务的名称，新增时自己起，之后照待办层里的写法写。")
    item: Annotated[str, Field(pattern=r"\S")] | None = Field(description="要取得的商品名称。")
    quantity: int | None = Field(description="要办成的份数。")
    memo: Annotated[str, Field(pattern=r"\S")] | None = Field(description="用自己的话写打算怎么办、去哪办。")
    remind_time: Annotated[str, Field(pattern=_CLOCK_PATTERN)] | None = Field(description="想被提醒去办这件事的钟点。")
    reason: Annotated[str, Field(pattern=r"\S")] | None = Field(description="不办了的原因。")

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """@brief 事务名称不超过 30 字符。"""
        return _errand_text(name, ERRAND_NAME_MAX_CHARS, "errand name")

    @field_validator("memo")
    @classmethod
    def validate_memo(cls, memo: str | None) -> str | None:
        """@brief 备注不超过 120 字符。"""
        return _errand_text(memo, ERRAND_TEXT_MAX_CHARS, "errand memo")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, reason: str | None) -> str | None:
        """@brief 取消原因不超过 120 字符。"""
        return _errand_text(reason, ERRAND_TEXT_MAX_CHARS, "errand cancel reason")

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, quantity: int | None) -> int | None:
        """@brief 份数在 1 至 9 之间（null 由交叉约束判定）。"""
        if quantity is not None and not ERRAND_QUANTITY_MIN <= quantity <= ERRAND_QUANTITY_MAX:
            _LOGGER.error("Errand change quantity is out of range")
            raise ValueError(f"errand quantity must be between {ERRAND_QUANTITY_MIN} and {ERRAND_QUANTITY_MAX}")
        return quantity

    @model_validator(mode="after")
    def check_shape(self) -> "ErrandChange":
        """@brief 按变更种类核对交叉约束：新增必须写完成依据、取消必须写原因，其余按表必须为 null；每个违背的键各报一条、
        定位到该键（外层再加 errands 与条目下标），原值照写。"""
        required, null = _ERRAND_REQUIRED[self.change], _ERRAND_NULL[self.change]
        errors: list[Any] = []
        for key in type(self).model_fields:
            value = getattr(self, key)
            if key in required and value is None:
                message = f"{key} is required when change is {self.change}"
            elif key in null and value is not None:
                message = f"{key} must be null when change is {self.change}"
            else:
                continue
            errors.append({"type": "value_error", "loc": (key,), "input": value,
                           "ctx": {"error": ValueError(message)}})
        if errors:
            _LOGGER.error("Errand change violates the constraints of its change kind")
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self



#: 三个控制工具请求级 errands 的字段 Schema（内联，不用 $ref）与字段说明。
_ERRANDS_SCHEMA = {"type": "array", "items": ErrandChange.model_json_schema(mode="validation")}

_ERRANDS_PURPOSE = "要新增、更新或取消的个人事务，一条一个对象，没有就写空列表。"



def _validate_errands(tool: str, errands: list[Any]) -> list[Any]:
    """@brief 控制工具 errands 的条数上限：一次请求至多四条变更（strict 端点禁 maxItems，由本地校验执行，可为空列表）；
    重名、同一事务被两条点名等与未完成事务相关的判定在 errands.change_issues（宿主与环境同一冻结观察）。"""
    if len(errands) > MAX_ERRAND_CHANGES:
        _LOGGER.error("Activity control request carries too many errand changes")
        raise ValueError(f"{tool} takes at most four errand changes")
    return errands



class ActivityProgressItem(ToolRequestModel):
    """@brief 播报或暂停的一项：活动名与人物自述。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("一项活动：活动名与进展。"))
    activity_name: str = Field(pattern=r"\S", description=_ACTIVITY_NAME_FORMAT)
    summary: str = Field(pattern=r"\S", description="用自己的话写这项活动到此刻的进展。")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, summary: str) -> str:
        """@brief 自述长度受控。"""
        return _validate_summary(summary)



#: 播报与暂停的 items 字段 Schema（内联，不用 $ref）。
_PROGRESS_ITEMS_SCHEMA = {"type": "array", "items": ActivityProgressItem.model_json_schema(mode="validation")}



class ReportStatusRequest(ToolRequestModel):
    """@brief 逐项播报进行中活动的人物自述；点名暂停的活动即在其开始地点恢复它（docs/design/04-simulation-engine.md
    ：一项一个对象）。

    :param items: 本次播报的一至两项，每项写活动名与进展自述。
    :param errands: 请求级的个人事务变更，零至四条（docs/design/04-simulation-engine.md）。
    """

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    items: Annotated[list[ActivityProgressItem], WithJsonSchema(_PROGRESS_ITEMS_SCHEMA)] = Field(
        description="要播报进展的活动，一项一个对象。")
    errands: Annotated[list[ErrandChange], WithJsonSchema(_ERRANDS_SCHEMA)] = Field(description=_ERRANDS_PURPOSE)

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityProgressItem]) -> list[ActivityProgressItem]:
        """@brief 保证一至两项、活动名互不相同。"""
        return _validate_control_items("report_status", items)

    @field_validator("errands")
    @classmethod
    def validate_errands(cls, errands: list[ErrandChange]) -> list[ErrandChange]:
        """@brief 一次请求至多四条事务变更。"""
        return _validate_errands("report_status", errands)



class PauseActivityRequest(ToolRequestModel):
    """@brief 逐项写下人物自述并暂停所选进行中活动；暂停让出槽位，恢复要有空槽位，路上的活动不能暂停（一项一个对象）。

    :param items: 本次暂停的一至两项，每项写活动名与进展自述。
    :param errands: 请求级的个人事务变更，零至四条（docs/design/04-simulation-engine.md）。
    """

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    items: Annotated[list[ActivityProgressItem], WithJsonSchema(_PROGRESS_ITEMS_SCHEMA)] = Field(
        description="要暂停的进行中活动，一项一个对象。")
    errands: Annotated[list[ErrandChange], WithJsonSchema(_ERRANDS_SCHEMA)] = Field(description=_ERRANDS_PURPOSE)

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityProgressItem]) -> list[ActivityProgressItem]:
        """@brief 保证一至两项、活动名互不相同。"""
        return _validate_control_items("pause_activity", items)

    @field_validator("errands")
    @classmethod
    def validate_errands(cls, errands: list[ErrandChange]) -> list[ErrandChange]:
        """@brief 一次请求至多四条事务变更。"""
        return _validate_errands("pause_activity", errands)



#: 一次 conclude_activity 写入的记忆与看法合计上限、单条文字字符上限与关键词个数上限（docs/design/04-simulation-engine.md
#: ；DeepSeek strict 禁 maxItems/maxLength，由本地校验兜住）。
CONCLUSION_MEMORIES_MAX = 8

MEMORY_TEXT_MAX_CHARS = 1000

MEMORY_KEYWORDS_MAX = 12

#: 记忆文字中不允许出现的十六进制身份（八位以上）。
_MEMORY_HEX_IDENTITY = re.compile(r"[0-9a-fA-F]{8,}")



def contains_hex_identity(text: str) -> bool:
    """@brief 文字是否含八位以上十六进制串；只由主观记忆文字的校验 validate_memory_text 使用，日、月、年回顾不查这一条。"""
    return _MEMORY_HEX_IDENTITY.search(text) is not None



def validate_memory_text(text: str) -> str:
    """@brief 进入主观记忆的文字的共用校验：不超过 1000 字符且不含八位以上十六进制身份。

    收尾记忆与看法两处共用这一个函数（docs/design/06-agent.md），不区分条目来源；十六进制身份检查是
    contains_hex_identity，日、月、年回顾正文不经这一检查。

    :param text: 模型写下的一条记忆文字。
    :returns: 通过校验的原文。
    :raises ValueError: 文字超长或含十六进制身份；英文原因由调用方的校验错误带给模型。
    """
    if len(text) > MEMORY_TEXT_MAX_CHARS:
        _LOGGER.error("Memory text exceeds its character limit")
        raise ValueError("memory text exceeds 1000 characters")
    if contains_hex_identity(text):
        _LOGGER.error("Memory text contains a hexadecimal identifier")
        raise ValueError("memory text contains a hexadecimal identifier")
    return text



def _validate_memory_keywords(keywords: list[str]) -> list[str]:
    """@brief 关键词上限在具体字段校验以保留完整位置。"""
    if len(keywords) > MEMORY_KEYWORDS_MAX:
        _LOGGER.error("Conclusion memory has too many keywords")
        raise ValueError("memory has more than twelve keywords")
    return keywords



class ConclusionMemory(ToolRequestModel):
    """@brief 结束活动时写下的一条普通记忆（看法与偏好另写在 views）；没有对象字段，来源与认知状态（subjective）由系统
    固定，模型不填任何身份。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("一条想记住的事：类型、文字、重要程度与关键词。"))
    type: Literal["commitment", "goal", "conflict", "resolution", "identity", "routine", "reflection"] = Field(
        description="这条记忆的类型，从列出的七种里选一种；对人的看法与对事物的偏好另外写。")
    text: str = Field(pattern=r"\S", description="用自己的话写下想记住的事，不写编号或身份串。")
    importance: float = Field(ge=0, le=1, description="这件事对自己的重要程度，0 到 1 之间的数。")
    keywords: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="便于日后回想的几个关键词。")

    @field_validator("text")
    @classmethod
    def validate_text(cls, text: str) -> str:
        """@brief 在文字字段上校验长度与身份泄漏。"""
        return validate_memory_text(text)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, keywords: list[str]) -> list[str]:
        """@brief 在关键词字段上校验总数。"""
        return _validate_memory_keywords(keywords)



class ConclusionView(ToolRequestModel):
    """@brief 结束活动时写下的一条看法（relationship，对某人）或偏好（preference，对某物）；about 写对象，宿主写入记忆时
    作为 subject 存储（请求里叫 about、存储里叫 subject，指同一件事，docs/design/00-overview.md「数据质量原则」）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("一条看法或偏好：类型、对象、文字、重要程度与关键词。"))
    type: Literal["relationship", "preference"] = Field(
        description="看法的类型：对某个人的看法，或对某样东西、地方、事情的偏好。")
    about: str = Field(pattern=r"\S", description="看法或偏好的对象：人照人设层里的写法写名字或称呼，东西、地方或事情"
                                                  "写它的名称。")
    text: str = Field(pattern=r"\S", description="用自己的话写下这条看法或偏好，不写编号或身份串。")
    importance: float = Field(ge=0, le=1, description="这条看法或偏好对自己的重要程度，0 到 1 之间的数。")
    keywords: list[Annotated[str, Field(pattern=r"\S")]] = Field(description="便于日后回想的几个关键词。")

    @field_validator("text")
    @classmethod
    def validate_text(cls, text: str) -> str:
        """@brief 在文字字段上校验长度与身份泄漏。"""
        return validate_memory_text(text)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, keywords: list[str]) -> list[str]:
        """@brief 在关键词字段上校验总数。"""
        return _validate_memory_keywords(keywords)



class ActivityConclusionItem(ToolRequestModel):
    """@brief 结束的一项：活动名、总结、普通记忆与看法（两者都必填，可以为空列表）。"""

    model_config = ConfigDict(json_schema_extra=purpose_only_schema("结束的一项活动：活动名、总结、想记住的事与看法。"))
    activity_name: str = Field(pattern=r"\S", description=_ACTIVITY_NAME_FORMAT)
    summary: str = Field(pattern=r"\S", description="用自己的话写这项活动的总结。")
    memories: Annotated[
        list[ConclusionMemory],
        WithJsonSchema({"type": "array", "items": ConclusionMemory.model_json_schema(mode="validation")}),
    ] = Field(description="关于这项活动、以后做决定用得上的新情况，一条一个对象，没有就写空列表。")
    views: Annotated[
        list[ConclusionView],
        WithJsonSchema({"type": "array", "items": ConclusionView.model_json_schema(mode="validation")}),
    ] = Field(description="这项活动让你形成的对人的看法或对事物的偏好，一条一个对象，没有就写空列表。")

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, summary: str) -> str:
        """@brief 总结长度受控。"""
        return _validate_summary(summary)



class ConcludeActivityRequest(ToolRequestModel):
    """@brief 逐项写下总结、记忆与看法并结束所选活动（进行中、暂停中或刚结束待收尾），进行中与待收尾的结束后释放槽位；一项一个对象
    （docs/design/04-simulation-engine.md）。

    :param items: 本次结束的一至两项；全部项的 memories 与 views 合计不超过八条。
    :param errands: 请求级的个人事务变更，零至四条（docs/design/04-simulation-engine.md）。
    """

    model_config = ConfigDict(json_schema_extra=purpose_only_schema())
    items: Annotated[
        list[ActivityConclusionItem],
        WithJsonSchema({"type": "array", "items": ActivityConclusionItem.model_json_schema(mode="validation")}),
    ] = Field(description="要结束的活动，一项一个对象。")
    errands: Annotated[list[ErrandChange], WithJsonSchema(_ERRANDS_SCHEMA)] = Field(description=_ERRANDS_PURPOSE)

    @field_validator("items")
    @classmethod
    def validate_items(cls, items: list[ActivityConclusionItem]) -> list[ActivityConclusionItem]:
        """@brief 保证一至两项、活动名互不相同，全部项的记忆与看法合计不超过八条。"""
        _validate_control_items("conclude_activity", items)
        if sum(len(item.memories) + len(item.views) for item in items) > CONCLUSION_MEMORIES_MAX:
            _LOGGER.error("Conclude activity carries too many memories and views")
            raise ValueError("conclude_activity memories and views exceed eight entries in total")
        return items

    @field_validator("errands")
    @classmethod
    def validate_errands(cls, errands: list[ErrandChange]) -> list[ErrandChange]:
        """@brief 一次请求至多四条事务变更。"""
        return _validate_errands("conclude_activity", errands)



ActionRequest = Annotated[
    StartMainActivityRequest | StartSideActivityRequest | RecallTaskSummaryRequest |
    ReportStatusRequest | PauseActivityRequest | ConcludeActivityRequest |
    ReviseDayPlanRequest,
    Field(union_mode="left_to_right"),
]



REQUEST_MODELS: dict[str, type[FrozenModel]] = {
    **dict(zip(START_TOOLS, (StartMainActivityRequest, StartSideActivityRequest), strict=True)),
    "recall_task_summary": RecallTaskSummaryRequest,
    "report_status": ReportStatusRequest,
    "pause_activity": PauseActivityRequest,
    "conclude_activity": ConcludeActivityRequest,
    "revise_day_plan": ReviseDayPlanRequest,
}

#: 只读查询的工具模型：不进 REQUEST_MODELS，也不进 ActionRequest，由 LifeEnv.read_query 回答
#: （docs/design/06-agent.md「检索记忆」）。
QUERY_MODELS: dict[str, type[FrozenModel]] = {"search_memory": SearchMemoryRequest}
