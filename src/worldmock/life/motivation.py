"""@brief 动机评分层：为决策时刻枚举有界候选行为并按四分量打分。

求值器按"内稳态 + 习惯 + 情绪 − 成本"四分量给每个候选行为打分，softmax(V/tau)
只作排序参考，top-k 连同相对呈现（rank、softmax 概率、四分量权衡）与英文
理由注入观察（motivation_hints）——不暴露绝对总分，负分会被模型误读成
"不值得做"。提示是建议不是账：求值只读状态，任何失败都在引擎侧
fail-open（不注入、不崩），结算路径完全不感知本模块。

通用变换形状（前景指数、归一尺度、时间价值、习惯权重）来自
catalog/rules/motivation.toml；个体参数（weights/tau/beta/loss_aversion/
comfort/emotion_sensitivity/attention_prior）来自场景 agents[].motivation 档案。
"""

from __future__ import annotations

import math
import tomllib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field

from worldmock.errors import IntegrityError
from worldmock.life.body import (
    ENERGY_RECOVERY_ACTIVITY_KINDS,
    interpolate,
    maximum_energy_duration_seconds,
    minimum_energy_cost,
)
from worldmock.life.models import (
    ActivityKind,
    ActivityState,
    AgentState,
    ContentProgress,
    FrozenModel,
)
from worldmock.life.scenario import (
    BodyConfig,
    ContentItemConfig,
    LocationConfig,
    MotivationProfile,
    ProductConfig,
    RouteConfig,
    WorkTaskConfig,
)

#: 候选家族闭集（习惯项的归一键）。
FAMILY_CONTINUE = "continue"
FAMILY_SLEEP = "sleep"
FAMILY_EAT = "eat"
FAMILY_TRAVEL = "travel"
FAMILY_BROWSE = "browse"
FAMILY_ENTERTAINMENT = "entertainment"
FAMILY_WORK = "work"
FAMILIES = frozenset({
    FAMILY_CONTINUE, FAMILY_SLEEP, FAMILY_EAT, FAMILY_TRAVEL,
    FAMILY_BROWSE, FAMILY_ENTERTAINMENT, FAMILY_WORK,
})

#: v1 每次决策枚举的候选上限与注入条数（browse 加入后 8→9）。
MAX_CANDIDATES = 9
DEFAULT_TOP_K = 5

#: 候选时长/观察窗口的工程上限（秒）：防止整部电影/整段合同把候选坐标推到极端。
MAX_CANDIDATE_SECONDS = 4 * 3600
#: 习惯项最多回看的足迹事件数（有界化，避免长运行下逐条扫描全历史）。
HABIT_EVENT_WINDOW = 200


class MotivationTransforms(FrozenModel):
    """@brief catalog/rules/motivation.toml 解析出的通用变换参数。"""

    loss_exponent: float = Field(gt=0, lt=1)
    satiation_exponent: float = Field(gt=0, lt=1)
    money_scale_fen: float = Field(gt=0)
    energy_scale_kcal: float = Field(gt=0)
    sleep_scale_hours: float = Field(gt=0)
    entertainment_scale: float = Field(gt=0)
    goal_scale: float = Field(gt=0)
    time_value_fen_per_hour: float = Field(gt=0)
    habit_weight: float = Field(ge=0, le=1)
    habit_discount: float = Field(ge=0, lt=1)

    def scale_of(self, resource: str) -> float:
        """@brief 返回某资源的归一尺度。"""
        return {"money_fen": self.money_scale_fen,
                "energy_kcal": self.energy_scale_kcal,
                "sleep_debt_hours": self.sleep_scale_hours,
                "entertainment_need": self.entertainment_scale,
                "goal_task": self.goal_scale}[resource]


_TRANSFORM_GROUPS = ("prospect", "homeostatic", "normalization", "cost", "habit")


def load_motivation_transforms(rules_dir: Path) -> MotivationTransforms | None:
    """@brief 读取目录规则中的动机变换参数；文件缺席返回 None（提示关闭）。

    文件在场但结构或取值不合约时 fail-closed 抛 IntegrityError——在场即声明意图，
    坏数据不得静默降级。键布局与 motivation.toml 的五个分组一一对应。
    """
    path = Path(rules_dir) / "motivation.toml"
    if not path.is_file():
        return None
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise IntegrityError(f"Motivation rules TOML is malformed: {error}") from error
    flat: dict[str, float] = {}
    seen: set[str] = set()
    for group in _TRANSFORM_GROUPS:
        table = payload.get(group)
        if not isinstance(table, dict):
            raise IntegrityError(f"Motivation rules group [{group}] is missing")
        for key, value in table.items():
            if key in seen:
                raise IntegrityError(f"Motivation rules key {key} is declared twice")
            seen.add(key)
            flat[key] = value
    unknown = set(payload) - set(_TRANSFORM_GROUPS) - {"version", "provenance"}
    if unknown:
        raise IntegrityError(f"Motivation rules declare unknown groups: {sorted(unknown)}")
    try:
        return MotivationTransforms.model_validate({
            "loss_exponent": flat["loss_exponent"],
            "satiation_exponent": flat["satiation_exponent"],
            "money_scale_fen": flat["money_fen"],
            "energy_scale_kcal": flat["energy_kcal"],
            "sleep_scale_hours": flat["sleep_debt_hours"],
            "entertainment_scale": flat["entertainment_need"],
            "goal_scale": flat["goal_progress"],
            "time_value_fen_per_hour": flat["time_value_fen_per_hour"],
            "habit_weight": flat["weight"],
            "habit_discount": flat["discount"],
        })
    except Exception as error:
        raise IntegrityError(f"Motivation rules values are invalid: {error}") from error


def prospect_value(z: float, transforms: MotivationTransforms, loss_aversion: float) -> float:
    """@brief 前景价值函数：增益段 z^satiation（凹），损失段 -lambda*(-z)^alpha（凸）。

    参考点语义由调用方归一进 z（z=0 即舒适参考点）；lambda 取自人物档案的
    loss_aversion（缺省资源为 1.0）。锚定 Tversky & Kahneman (1992)。
    """
    if z >= 0:
        return z ** transforms.satiation_exponent
    return -loss_aversion * ((-z) ** transforms.loss_exponent)


# ---------------------------------------------------------------------------
# 习惯项：足迹 action_requested 家族频率的贴现计数
# ---------------------------------------------------------------------------

def classify_action_event(tool_name: str, request: Mapping) -> str | None:
    """@brief 把一次动作请求归入候选家族；不可判定的动作返回 None。"""
    activity_type = request.get("activity_type") if isinstance(request, Mapping) else None
    if tool_name == "continue_activity":
        return FAMILY_CONTINUE
    if tool_name == "start_basic_activity":
        return FAMILY_SLEEP if activity_type == "sleep" else FAMILY_CONTINUE
    if tool_name in {"purchase", "consume"}:
        return FAMILY_EAT
    if tool_name == "start_travel":
        return FAMILY_TRAVEL
    if tool_name == "start_targeted_activity":
        if activity_type in {"watch", "play_game"}:
            return FAMILY_ENTERTAINMENT
        if activity_type == "work":
            return FAMILY_WORK
        if activity_type == "browse":
            return FAMILY_BROWSE
        return None
    if tool_name == "submit_work":
        return FAMILY_WORK
    return None


def habit_shares(events: Sequence[tuple[str, Mapping]], discount: float) -> dict[str, float]:
    """@brief 按指数贴现计数把足迹动作归一为各家族份额 [0,1]。

    第 k 新的事件权重 discount^k（最新为 1）；只统计可判定家族，份额分母
    也只含可判定事件。空足迹返回全零份额。
    """
    weights = {family: 0.0 for family in FAMILIES}
    total = 0.0
    for age, (tool_name, request) in enumerate(reversed(events)):
        family = classify_action_event(tool_name, request)
        if family is None:
            continue
        weight = discount ** age
        weights[family] += weight
        total += weight
    if total <= 0:
        return weights
    return {family: value / total for family, value in weights.items()}


# ---------------------------------------------------------------------------
# 求值输入与输出
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MotivationContext:
    """@brief 一次求值所需的只读世界切片（引擎装配，测试可独立构造）。"""

    agent: AgentState
    balance_fen: int
    body: BodyConfig
    home_location_id: str
    products: tuple[ProductConfig, ...]
    merchant_stock: Mapping[str, int]
    locations: Mapping[str, LocationConfig]
    routes: tuple[RouteConfig, ...]
    tasks: tuple[WorkTaskConfig, ...]
    task_totals: Mapping[str, tuple[int, float]]
    contents: tuple[ContentItemConfig, ...]
    content_progress: Mapping[str, ContentProgress]
    active_activity: ActivityState | None
    habit_events: tuple[tuple[str, Mapping], ...]
    consecutive_rejections: int
    max_consecutive_rejections: int
    total_effective_seconds: float
    current_time: int
    end_time: int
    timezone: str
    planned_travel_seconds: Callable[[str, int], int]
    #: 全量商品目录（不经 browse 可见性门控）：browse 候选的存在性依据。
    catalog_products: tuple[ProductConfig, ...] = ()
    #: 内容分段边界（item_id -> 各段结束秒）：娱乐候选的分段时长槽依据。
    content_segments: Mapping[str, tuple[float, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Candidate:
    """@brief 一个枚举出的候选行为及其资源流（有符号变化量）。"""

    family: str
    summary: str
    reason: str
    money_now_fen: float = 0.0
    money_deferred_fen: float = 0.0
    delay_seconds: float = 0.0
    duration_seconds: float = 0.0
    energy_delta: float = 0.0
    sleep_debt_delta: float = 0.0
    entertainment_delta: float = 0.0
    goal_effective_seconds: float = 0.0
    comfort_type: bool = False
    work_type: bool = False


@dataclass(frozen=True)
class ScoredCandidate:
    """@brief 打分后的候选：四分量、总分、softmax 概率与提示载荷。"""

    candidate: _Candidate
    score: float
    components: dict[str, float] = field(default_factory=dict)
    softmax_probability: float = 0.0
    rank: int = 0

    def to_hint(self) -> dict:
        """@brief 生成注入观察的 JSON 载荷（相对分呈现，不暴露绝对总分）。

        模型会把负的绝对分读成"不值得做"（动机验证轮 a2 08:00 实证），
        因此只给 rank（1 起）、softmax 概率与四分量权衡（tradeoff）。
        """
        return {"rank": self.rank,
                "family": self.candidate.family,
                "action_summary": self.candidate.summary,
                "softmax_probability": round(self.softmax_probability, 4),
                "tradeoff": {key: round(value, 4) for key, value in self.components.items()},
                "reason_text": self.candidate.reason}


def evaluate_motivation(context: MotivationContext, profile: MotivationProfile,
                        transforms: MotivationTransforms, top_k: int = DEFAULT_TOP_K,
                        ) -> list[ScoredCandidate]:
    """@brief 枚举候选、计算四分量并按 softmax(V/tau) 排序返回 top-k。

    softmax 只作排序参考注入概率，不做选择；候选枚举上限 MAX_CANDIDATES。
    """
    candidates = _enumerate_candidates(context, profile)
    shares = habit_shares(context.habit_events[-HABIT_EVENT_WINDOW:],
                          transforms.habit_discount)
    emotion_base = _emotion_state(context, profile)
    scored = [
        _score_candidate(candidate, context, profile, transforms, shares, emotion_base)
        for candidate in candidates
    ]
    ordered = sorted(scored, key=lambda item: (-item.score, item.candidate.summary))
    probabilities = _softmax([item.score for item in ordered], profile.tau)
    return [ScoredCandidate(item.candidate, item.score, item.components, probability, rank)
            for rank, (item, probability) in enumerate(zip(ordered, probabilities), start=1)
            ][:max(0, top_k)]


# ---------------------------------------------------------------------------
# 四分量
# ---------------------------------------------------------------------------

def _emotion_state(context: MotivationContext, profile: MotivationProfile) -> tuple[float, float]:
    """@brief v1 简化情绪评估：受挫度与目标缺口，各自线性落在 [0,1]。

    受挫度 = 连续拒绝次数 / 上限；目标缺口 = 1 - 有效工时进度比（累计口径，
    按日切窗需要日粒度足迹聚合，v1 先用累计对单日志向的比值，注明简化）。
    """
    frustration = (context.consecutive_rejections / context.max_consecutive_rejections
                   if context.max_consecutive_rejections > 0 else 0.0)
    aspiration_seconds = max(1.0, float(profile.aspiration["daily_effective_work_seconds"]))
    progress = min(1.0, context.total_effective_seconds / aspiration_seconds)
    return min(1.0, frustration), 1.0 - progress


def _work_efficiency_snapshot(body: BodyConfig, energy_kcal: float,
                              sleep_debt_hours: float,
                              entertainment_need: float) -> float:
    """@brief 以给定身体状态取工作效率因子起点插值连乘（工具因子不适用）。"""
    values = {"sleep_debt_hours": sleep_debt_hours, "energy_kcal": energy_kcal,
              "entertainment_need": entertainment_need}
    product = 1.0
    for factor in body.work_efficiency_factors:
        product *= interpolate(factor.curve, values[factor.input_resource])
    return product


def _homeostatic(candidate: _Candidate, context: MotivationContext,
                 profile: MotivationProfile, transforms: MotivationTransforms) -> float:
    """@brief 内稳态分量：sum_i w_i * [phi_i(c_after) - phi_i(c_now)]。

    各资源坐标归一到"越高越好"：钱/能量以档案 comfort 为参考点，睡眠债与
    娱乐需要以 0 为参考点取负号，目标进度以日志向（单日志向完成度）为参考。
    完成时点才兑现的钱流按 beta 现时偏折现后进入坐标。
    """
    agent = context.agent
    beta = profile.beta_present_bias
    deferred_hours = (candidate.delay_seconds + candidate.duration_seconds) / 3600
    money_after = (context.balance_fen + candidate.money_now_fen
                   + candidate.money_deferred_fen * beta ** deferred_hours)
    energy_after = agent.body.energy_kcal + candidate.energy_delta
    debt_after = agent.body.sleep_debt_hours + candidate.sleep_debt_delta
    fun_after = agent.body.entertainment_need + candidate.entertainment_delta
    aspiration_seconds = max(1.0, float(profile.aspiration["daily_effective_work_seconds"]))
    goal_now = min(1.0, context.total_effective_seconds / aspiration_seconds) - 1.0
    goal_after = min(1.0, (context.total_effective_seconds
                           + candidate.goal_effective_seconds) / aspiration_seconds) - 1.0
    coordinates = (
        ("money_fen", (context.balance_fen - profile.comfort["balance_fen"]) / transforms.money_scale_fen,
         (money_after - profile.comfort["balance_fen"]) / transforms.money_scale_fen),
        ("energy_kcal", (agent.body.energy_kcal - profile.comfort["energy_kcal"]) / transforms.energy_scale_kcal,
         (energy_after - profile.comfort["energy_kcal"]) / transforms.energy_scale_kcal),
        ("sleep_debt_hours", -agent.body.sleep_debt_hours / transforms.sleep_scale_hours,
         -debt_after / transforms.sleep_scale_hours),
        ("entertainment_need", -agent.body.entertainment_need / transforms.entertainment_scale,
         -fun_after / transforms.entertainment_scale),
        ("goal_task", goal_now / transforms.goal_scale, goal_after / transforms.goal_scale),
    )
    total = 0.0
    for resource, now, after in coordinates:
        loss_aversion = profile.loss_aversion.get(resource, 1.0)
        total += profile.weights[resource] * (
            prospect_value(after, transforms, loss_aversion)
            - prospect_value(now, transforms, loss_aversion))
    return total


def _score_candidate(candidate: _Candidate, context: MotivationContext,
                     profile: MotivationProfile, transforms: MotivationTransforms,
                     shares: Mapping[str, float], emotion_base: tuple[float, float],
                     ) -> ScoredCandidate:
    """@brief 计算单个候选的四分量与总分 V = homeostatic + habit + emotion - cost。"""
    homeostatic = _homeostatic(candidate, context, profile, transforms)
    habit = transforms.habit_weight * shares.get(candidate.family, 0.0)
    frustration, deficit = emotion_base
    emotion = profile.emotion_sensitivity * (
        frustration * (1.0 if candidate.comfort_type else 0.0)
        + deficit * (1.0 if candidate.work_type else 0.0))
    hours = (candidate.delay_seconds + candidate.duration_seconds) / 3600
    cost = ((abs(min(0.0, candidate.money_now_fen))
             + (beta_discount(profile, candidate.delay_seconds) * hours
                * transforms.time_value_fen_per_hour)) / transforms.money_scale_fen)
    score = homeostatic + habit + emotion - cost
    return ScoredCandidate(candidate, score, {"homeostatic": homeostatic, "habit": habit,
                                              "emotion": emotion, "cost": cost})


def beta_discount(profile: MotivationProfile, delay_seconds: float) -> float:
    """@brief 现时偏好折现：延迟开始的时间成本按 beta^(delay_hours) 折扣。

    v1 简化口径：动作自身的执行过程不折现（收益/成本都在期内兑现），只有
    被等待门控的流（配送延迟后的进食时间、完成时点后的报酬）才折现。
    """
    return profile.beta_present_bias ** (delay_seconds / 3600)


def _softmax(values: list[float], tau: float) -> list[float]:
    """@brief 数值稳定的 softmax(V/tau)；空列表返回空。"""
    if not values:
        return []
    peak = max(values)
    exponentials = [math.exp((value - peak) / tau) for value in values]
    total = sum(exponentials)
    return [value / total for value in exponentials]


# ---------------------------------------------------------------------------
# 候选枚举（v1 有界集合）
# ---------------------------------------------------------------------------

def _enumerate_candidates(context: MotivationContext,
                          profile: MotivationProfile) -> list[_Candidate]:
    """@brief 按固定优先级枚举候选并截断到 MAX_CANDIDATES。

    优先级：进食 > 睡眠 > 继续当前活动 > 去工作地点 > 工作任务(至多2) >
    回家 > 浏览商品目录 > 娱乐(至多3)。生存与目标导向优先于消遣，浏览
    （工具性：解锁进食候选的枚举）排在纯消遣之前；截断从队尾开始，
    保证任何决策点的候选数不超过 MAX_CANDIDATES。
    """
    pool: list[_Candidate] = []
    eat = _eat_candidate(context)
    sleep = _sleep_candidate(context)
    keep = _continue_candidate(context)
    work_travel, work = _work_candidates(context)
    home_travel = _travel_candidate(context, "home", context.home_location_id)
    browse = _browse_candidate(context)
    entertainment = _entertainment_candidates(context, profile)
    for candidate in (eat, sleep, keep, work_travel, *work, home_travel, browse,
                      *entertainment):
        if candidate is not None:
            pool.append(candidate)
    return pool[:MAX_CANDIDATES]


def _browse_candidate(context: MotivationContext) -> _Candidate | None:
    """@brief 浏览候选：目录中尚有未见过商品时给出（10 分钟目录速览）。

    浏览是工具性动作：browse 之后商品目录对人物可见，进食候选才可
    枚举——因此只要还有没见过的商品就保持此候选在场。
    """
    seen = {product.product_id for product in context.products}
    if not any(product.product_id not in seen for product in context.catalog_products):
        return None
    duration = min(600, MAX_CANDIDATE_SECONDS,
                   context.end_time - context.current_time)
    if duration < 60:
        return None
    burn = minimum_energy_cost(ActivityKind.BROWSE, duration, context.body)
    if burn > context.agent.body.energy_kcal:
        return None
    hours = duration / 3600
    return _Candidate(
        family=FAMILY_BROWSE,
        summary=f"Browse product catalog for {duration / 60:.0f} min",
        reason=("Browsing the product catalog shows what nearby markets have on sale; "
                "food choices become enumerable only after browsing"),
        duration_seconds=duration,
        energy_delta=-burn,
        sleep_debt_delta=context.body.awake_sleep_debt_per_hour * hours,
        entertainment_delta=_entertainment_accrual(context, hours))


def _sleep_candidate(context: MotivationContext) -> _Candidate:
    """@brief 睡眠候选：清债所需时长（上限 8 小时），无债时 1 小时小睡。"""
    agent = context.agent
    debt = agent.body.sleep_debt_hours
    if debt > 0:
        seconds = math.ceil(debt / context.body.sleep_recovery_per_hour * 3600)
    else:
        seconds = 3600
    duration = min(max(60, seconds), 8 * 3600, MAX_CANDIDATE_SECONDS,
                   context.end_time - context.current_time)
    if duration < 60:
        return None
    hours = duration / 3600
    debt_after = max(0.0, debt - context.body.sleep_recovery_per_hour * hours)
    burn = minimum_energy_cost(ActivityKind.SLEEP, duration, context.body)
    energy_after = max(0.0, agent.body.energy_kcal - burn)
    return _Candidate(
        family=FAMILY_SLEEP,
        summary=f"Sleep for {duration / 3600:.1f}h",
        reason=(f"Sleeping {duration / 3600:.1f}h clears sleep debt "
                f"{debt:.1f}h -> {debt_after:.1f}h"),
        duration_seconds=duration,
        energy_delta=energy_after - agent.body.energy_kcal,
        sleep_debt_delta=debt_after - debt,
        comfort_type=True)


def _merchant_open(context: MotivationContext, product: ProductConfig) -> bool:
    """@brief 以场景本地小时执行商家冻结营业时间（与 commerce 口径一致）。"""
    location = context.locations.get(product.merchant_location_id)
    if location is None:
        return False
    from datetime import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.fromtimestamp(context.current_time, ZoneInfo(context.timezone)).hour
    return location.open_hour <= hour < location.close_hour


def _eat_candidate(context: MotivationContext) -> _Candidate | None:
    """@brief 进食候选：最廉可行"取得+食用"组合（手持食物优先，其次可购食品）。

    可行性口径：报价可负担、库存可取、商家营业，且净能量恢复为正
    （进食活动自身代谢后仍有盈余——净负的"餐"不构成恢复性进食）。
    不在商家处时走配送，等待 = 交付延迟 + 商家到当前位置的冻结驾驶配送路线时长。
    """
    agent = context.agent
    efficiency = interpolate(context.body.food_efficiency_curve, agent.body.sleep_debt_hours)
    best = None
    for product in context.products:
        if "eat" not in product.use_modes:
            continue
        duration = float(product.use_duration_seconds)
        raw_gain = product.energy_kcal_per_unit * efficiency
        burn = minimum_energy_cost(ActivityKind.CONSUME, int(duration), context.body)
        if raw_gain <= burn:
            continue
        holding = float(agent.inventory.get(agent.location_id, {}).get(product.product_id, 0))
        if holding >= 1:
            key = (0, 0, product.product_id)
            if best is None or key < best[0]:
                best = (key, product, 0, 0.0)
            continue
        if (product.price_fen > context.balance_fen
                or context.merchant_stock.get(product.product_id, 0) < 1
                or not _merchant_open(context, product)):
            continue
        if agent.location_id == product.merchant_location_id:
            wait = 0.0
        else:
            route = _delivery_route_seconds(context, product.merchant_location_id, agent.location_id)
            if route is None:
                continue
            wait = float(product.delivery_delay_seconds + route)
        key = (product.price_fen, wait, product.product_id)
        if best is None or key < best[0]:
            best = (key, product, product.price_fen, wait)
    if best is None:
        return None
    _, product, price, wait = best
    duration = float(product.use_duration_seconds)
    hours = duration / 3600
    raw_gain = product.energy_kcal_per_unit * efficiency
    burn = minimum_energy_cost(ActivityKind.CONSUME, int(duration), context.body)
    headroom = context.body.maximum_energy_kcal - agent.body.energy_kcal
    gain = max(0.0, min(raw_gain, headroom))
    burn = minimum_energy_cost(ActivityKind.CONSUME, int(duration), context.body)
    energy_delta = gain - burn
    energy_after = max(0.0, agent.body.energy_kcal + energy_delta)
    efficiency_before = _work_efficiency_snapshot(
        context.body, agent.body.energy_kcal, agent.body.sleep_debt_hours,
        agent.body.entertainment_need)
    efficiency_after = _work_efficiency_snapshot(
        context.body, energy_after, agent.body.sleep_debt_hours,
        agent.body.entertainment_need)
    debt_delta = context.body.awake_sleep_debt_per_hour * hours
    fun_delta = _entertainment_accrual(context, hours)
    prefix = "Eat held" if price == 0 else "Buy and eat"
    return _Candidate(
        family=FAMILY_EAT,
        summary=f"{prefix} {product.product_id}",
        reason=(f"Eating {product.product_id} now restores energy "
                f"{agent.body.energy_kcal:.0f}->{energy_after:.0f} kcal, work efficiency "
                f"{efficiency_before:.2f}->{efficiency_after:.2f}"),
        money_now_fen=-float(price),
        delay_seconds=wait,
        duration_seconds=duration,
        energy_delta=energy_delta,
        sleep_debt_delta=debt_delta,
        entertainment_delta=fun_delta)


def _delivery_route_seconds(context: MotivationContext, origin: str,
                            destination: str) -> int | None:
    """@brief 取商家到目的地的冻结驾驶配送路线时长（无路线则不可配送）。"""
    matches = [item for item in context.routes if item.origin_id == origin
               and item.destination_id == destination and item.mode.value == "driving"]
    return matches[0].duration_seconds if len(matches) == 1 else None


def _continue_candidate(context: MotivationContext) -> _Candidate | None:
    """@brief 继续当前活动：按剩余计划时长推导资源流，不可行能量时不出候选。"""
    activity = context.active_activity
    if activity is None:
        return None
    remaining = activity.planned_duration_seconds - activity.elapsed_seconds
    if remaining < 60:
        return None
    if activity.kind == ActivityKind.TRAVEL and activity.travel_mode is not None:
        met = context.body.activity_met[f"travel:{activity.travel_mode.value}"]
    else:
        met = context.body.activity_met[activity.kind.value]
    burn = minimum_energy_cost(activity.kind, remaining, context.body, met)
    if activity.kind not in ENERGY_RECOVERY_ACTIVITY_KINDS and burn > context.agent.body.energy_kcal:
        return None
    hours = remaining / 3600
    energy_delta = -burn
    if activity.kind == ActivityKind.CONSUME:
        rate = float(activity.data.get("food_energy_kcal_per_hour", 0) or 0)
        headroom = context.body.maximum_energy_kcal - context.agent.body.energy_kcal
        energy_delta = max(0.0, min(rate * hours, headroom)) - burn
    debt_delta = _sleep_debt_delta(context, activity.kind, hours)
    fun_delta = _entertainment_delta(context, activity, hours)
    goal_seconds = 0.0
    if activity.kind == ActivityKind.WORK:
        efficiency = _work_efficiency_snapshot(
            context.body, context.agent.body.energy_kcal,
            context.agent.body.sleep_debt_hours, context.agent.body.entertainment_need)
        skill = float(context.agent.skill_levels.get(
            activity.data.get("required_skill", ""), 0) or 0)
        goal_seconds = remaining * efficiency * skill
    minutes = remaining / 60
    summary = f"Continue {activity.kind.value} for {minutes:.0f} min"
    return _Candidate(
        family=FAMILY_CONTINUE,
        summary=summary,
        reason=f"Continuing current {activity.kind.value} activity for {minutes:.0f} min"
               f" keeps committed progress",
        duration_seconds=remaining,
        energy_delta=energy_delta,
        sleep_debt_delta=debt_delta,
        entertainment_delta=fun_delta,
        goal_effective_seconds=goal_seconds,
        comfort_type=activity.kind in {ActivityKind.WATCH, ActivityKind.PLAY_GAME})


def _sleep_debt_delta(context: MotivationContext, kind: ActivityKind, hours: float) -> float:
    """@brief 活动引起的睡眠债变化：睡眠恢复，其余清醒活动累积。"""
    if kind == ActivityKind.SLEEP:
        return -min(context.body.sleep_recovery_per_hour * hours,
                    context.agent.body.sleep_debt_hours)
    return context.body.awake_sleep_debt_per_hour * hours


def _entertainment_accrual(context: MotivationContext, hours: float) -> float:
    """@brief 清醒非娱乐活动的娱乐需要累积（触顶钳制）。"""
    projected = context.agent.body.entertainment_need + (
        context.body.entertainment_need_per_awake_hour * hours)
    return min(1.0, projected) - context.agent.body.entertainment_need


def _entertainment_delta(context: MotivationContext, activity: ActivityState,
                         hours: float) -> float:
    """@brief 活动引起的娱乐需要变化：观看/游戏按实际缓解速率，其余累积。"""
    preference = context.agent.entertainment_preferences.get(activity.target_id or "", 0)
    if activity.kind == ActivityKind.WATCH:
        rate = activity.data.get("entertainment_relief_per_hour")
        count = activity.data.get("exposure_count", 1)
        if isinstance(rate, (int, float)) and isinstance(count, int) and count >= 1:
            relief = min(float(rate) * preference / count * hours,
                         context.agent.body.entertainment_need)
            return -relief
    if activity.kind == ActivityKind.PLAY_GAME:
        base = context.body.entertainment_relief_per_hour.get(ActivityKind.PLAY_GAME.value, 0)
        relief = min(preference * base * hours, context.agent.body.entertainment_need)
        return -relief
    return _entertainment_accrual(context, hours)


def _travel_candidate(context: MotivationContext, label: str,
                      destination_id: str) -> _Candidate | None:
    """@brief 出行候选：当前位置出发、报价可负担路线中的最低票价一条。"""
    agent = context.agent
    if destination_id == agent.location_id:
        return None
    feasible = [item for item in context.routes
                if item.origin_id == agent.location_id and item.destination_id == destination_id
                and item.fare_fen <= context.balance_fen]
    if not feasible:
        return None
    route = min(feasible, key=lambda item: (item.fare_fen, item.route_id))
    duration = int(context.planned_travel_seconds(route.mode.value, route.duration_seconds))
    if duration < 60:
        return None
    met = context.body.activity_met[f"travel:{route.mode.value}"]
    burn = minimum_energy_cost(ActivityKind.TRAVEL, duration, context.body, met)
    if burn > agent.body.energy_kcal:
        return None
    hours = duration / 3600
    minutes = duration / 60
    return _Candidate(
        family=FAMILY_TRAVEL,
        summary=f"Travel to {label} ({destination_id}) by {route.mode.value}",
        reason=(f"Traveling to {label} by {route.mode.value} takes {minutes:.0f} min"
                f" and costs {route.fare_fen} fen"),
        money_now_fen=-float(route.fare_fen),
        duration_seconds=duration,
        energy_delta=-burn,
        sleep_debt_delta=context.body.awake_sleep_debt_per_hour * hours,
        entertainment_delta=_entertainment_accrual(context, hours))


def _work_candidates(context: MotivationContext) -> tuple[_Candidate | None, list[_Candidate]]:
    """@brief 工作候选：至多两个报酬最高的可用任务，外加去工作地点的出行候选。

    可用口径与 validate_work_start 一致（可见、有效期内、技能达标）；工作
    候选只在人物已位于任务地点时给出，否则给出去该地点的出行候选。
    """
    agent = context.agent
    now = context.current_time
    available = []
    for task in context.tasks:
        if now >= int(task.due_at.timestamp()):
            continue
        if agent.skill_levels.get(task.required_skill, 0) < task.minimum_skill:
            continue
        available.append(task)
    available.sort(key=lambda item: (-item.pay_fen, item.task_id))
    if not available:
        return None, []
    travel = None
    work: list[_Candidate] = []
    for task in available[:2]:
        if agent.location_id != task.required_location_id:
            if travel is None:
                travel = _travel_candidate(context, f"work ({task.task_id})",
                                           task.required_location_id)
            continue
        worked, effective = context.task_totals.get(task.task_id, (0, 0.0))
        slot = min(task.maximum_duration_seconds - worked,
                   maximum_energy_duration_seconds(ActivityKind.WORK,
                                                   agent.body.energy_kcal, context.body),
                   int(task.due_at.timestamp()) - now, MAX_CANDIDATE_SECONDS)
        if slot < 60:
            continue
        efficiency = _work_efficiency_snapshot(
            context.body, agent.body.energy_kcal, agent.body.sleep_debt_hours,
            agent.body.entertainment_need) * agent.skill_levels[task.required_skill]
        goal_seconds = slot * efficiency
        required = max(1.0, task.required_effective_seconds)
        already = min(1.0, effective / required)
        after = min(1.0, (effective + goal_seconds) / required)
        deferred = float(task.pay_fen) * (after - already)
        hours = slot / 3600
        burn = minimum_energy_cost(ActivityKind.WORK, slot, context.body)
        work.append(_Candidate(
            family=FAMILY_WORK,
            summary=f"Work task {task.task_id} for {slot / 60:.0f} min",
            reason=(f"Working {task.task_id} for {slot / 60:.0f} min yields about "
                    f"{round(deferred)} fen and advances goal progress"),
            money_deferred_fen=deferred,
            duration_seconds=slot,
            energy_delta=-burn,
            sleep_debt_delta=context.body.awake_sleep_debt_per_hour * hours,
            entertainment_delta=_entertainment_accrual(context, hours),
            goal_effective_seconds=goal_seconds,
            work_type=True))
    return travel, work


def _entertainment_candidates(context: MotivationContext,
                              profile: MotivationProfile) -> list[_Candidate]:
    """@brief 娱乐候选：attention_prior x 内容偏好加权后取 top-3 的可观看内容。

    时长槽按分段语义给：一次约两段典型时长（typical_segment_seconds x 2），
    让长内容以"读一章/看一段"的小步会话入场而不是一整天的整块承诺；槽长
    受剩余媒体时长、能量可行上限、MAX_CANDIDATE_SECONDS 与剩余世界时间
    截断，短段内容（剧集/信息流）保底一次十分钟会话避免候选被裁光。看完
    的内容从头再来；缓解速率与引擎口径一致（偏好/接触次数）。
    """
    agent = context.agent
    ranked = sorted(
        (item for item in context.contents
         if profile.attention_prior.get(item.kind, 0)
         * agent.entertainment_preferences.get(item.item_id, 0.5) > 0),
        key=lambda item: (-profile.attention_prior.get(item.kind, 0)
                          * agent.entertainment_preferences.get(item.item_id, 0.5),
                          item.item_id))
    candidates = []
    for item in ranked[:3]:
        progress = context.content_progress.get(item.item_id)
        position = progress.position_seconds if progress else 0.0
        exposures = progress.exposure_count if progress else 0
        boundaries = context.content_segments.get(item.item_id) or ()
        segments_total = len(boundaries) or 1
        typical = item.duration_seconds / segments_total
        remaining = item.duration_seconds - position
        if remaining <= 0:  # 看完的内容重复观看从头开始
            position = 0.0
            remaining = item.duration_seconds
        outer_cap = min(remaining,
                        maximum_energy_duration_seconds(ActivityKind.WATCH,
                                                        agent.body.energy_kcal, context.body),
                        MAX_CANDIDATE_SECONDS, context.end_time - context.current_time)
        slot = min(typical * 2, outer_cap)
        slot = max(slot, min(600.0, outer_cap))
        if slot < 60:
            continue
        verb, noun = ("Read", "chapter") if item.kind == "webnovel_excerpt" else ("Watch", "segment")
        started = progress is not None and position > 0 and remaining > 0
        each = (f"~{typical:.0f} sec each" if typical < 120 else f"~{typical / 60:.0f} min each")
        if segments_total > 1:
            segment_index = min(bisect_right(boundaries, position) + 1, segments_total)
            summary = f"{verb} {noun} {segment_index} of {item.item_id} (~{slot / 60:.0f} min)"
            lead = (f"Continuing {item.item_id} at {noun} {segment_index}/{segments_total}"
                    if started else
                    f"{verb}ing one {noun} of {item.item_id} ({each})")
        elif started:
            summary = f"Continue {item.item_id} from minute {position / 60:.0f} (~{slot / 60:.0f} min)"
            lead = f"Continuing {item.item_id} from minute {position / 60:.0f}"
        else:
            summary = f"{verb} {item.item_id} (~{slot / 60:.0f} min)"
            lead = f"{verb}ing {item.item_id}"
        hours = slot / 3600
        preference = agent.entertainment_preferences.get(item.item_id, 0.5)
        relief_rate = item.entertainment_relief_per_hour * preference / (exposures + 1)
        relief = min(relief_rate * hours, agent.body.entertainment_need)
        fun_after = agent.body.entertainment_need - relief
        burn = minimum_energy_cost(ActivityKind.WATCH, slot, context.body)
        candidates.append(_Candidate(
            family=FAMILY_ENTERTAINMENT,
            summary=summary,
            reason=(f"{lead} relieves entertainment need "
                    f"{agent.body.entertainment_need:.2f}->{fun_after:.2f}"),
            duration_seconds=slot,
            energy_delta=-burn,
            sleep_debt_delta=context.body.awake_sleep_debt_per_hour * hours,
            entertainment_delta=-relief,
            comfort_type=True))
    return candidates
