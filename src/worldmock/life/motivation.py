"""@brief 动机评分层：为决策时刻枚举有界候选行为并按四分量打分。

求值器按“内稳态 + 习惯 + 情绪 − 成本”四分量给每个候选行为打分，softmax(V/tau)
只作排序参考，top-k 连同相对呈现（rank、softmax 概率、四分量权衡）与英文
理由写入 observation.data.motivation_internal（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」：内部
仪表不进提示，只供评估与研究）。不暴露绝对总分，负分会被模型误读成
“不值得做”。提示是建议不是账：求值只读状态，任何失败都在引擎侧
fail-open（不注入、不崩），结算路径完全不感知本模块。

评分器口径（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」）：成本按候选时长归一（单位小时效用），
价格只在成本项计一次；进食候选按热量门槛/缺口匹配取前三；同一娱乐
家族至多两条候选，娱乐需要低于 0.3 时不生成娱乐候选；生活活动只从
人物启用集采样并按 effects 折算缓解；近 24 小时已食用商品按 1/(1+次数)
降权（Rolls 1981 感官特异性饱足）；候选偏好权重来自 taste_weights
（画像编译 v2 的确定性口味匹配，无缺省抬升），排序用原始权重，观看与游戏的
缓解预演按有效偏好 f + (1 − f)·w、有缓解时不再累积，与结算同式
（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）。

通用变换形状（前景指数、归一尺度、时间价值、习惯权重）来自
catalog/rules/motivation.toml；个体参数（weights/tau/beta/loss_aversion/
comfort/emotion_sensitivity/attention_prior）来自场景 agents[].motivation 档案。
"""

from __future__ import annotations
from worldmock.life.vocabulary import WAYS

import math
import tomllib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from worldmock.catalog.affordances import (
    FamilySpec,
    PROTOTYPE_GENERATORS,
    engine_affordances,
)
from worldmock.catalog.graph import Graph
from worldmock.errors import IntegrityError
from worldmock.life import sleep
from worldmock.life.activities import entertainment_relief_rate, lifestyle_relief_rates
from worldmock.life.body import (
    ENERGY_RECOVERY_ACTIVITY_KINDS,
    entertainment_need_rate,
    interpolate,
    maximum_energy_duration_seconds,
    minimum_energy_cost,
)
from worldmock.life.content import (
    LifestyleItem,
)
from worldmock.base_models import FrozenModel
from worldmock.catalog.models import Catalog
from worldmock.exchange.constants import FOOD_CONSTANTS
from worldmock.exchange.operators import delivery_fee_fen, delivery_seconds
from worldmock.exchange.types import FoodCategory
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.models import (ActivityKind, ActivityState, AgentState, TravelMode)
from worldmock.life.travel import plan_trip
from worldmock.life.retrieval import feasible_kinds
from worldmock.life.scenario import (
    BodyConfig,
    MotivationProfile,
    RuntimeWorkTask,
)

#: 可供性注册表视图（冻结文件加载一次；家族闭集、习惯归并路由、枚举序、
#: 候选生成器原型与采样参数全部经它派生——M1 起注册表即事实）。
_AFFORDANCES = engine_affordances()

#: 候选家族名（习惯项的归一键）。
FAMILY_CONTINUE = "continue"
FAMILY_SLEEP = "sleep"
FAMILY_EAT = "eat"
FAMILY_TRAVEL = "travel"
FAMILY_ENTERTAINMENT = "entertainment"
FAMILY_WORK = "work"
FAMILY_LIFESTYLE = "lifestyle"
#: 候选家族闭集（习惯项的归一键）——自注册表 families 表派生
#: （generator ≠ none 的 8 个动机家族；M4 起 lifestyle 家族参与动机，
#: 其 lifestyle:* 前缀习惯路由自 continue 家族迁入）。
FAMILIES = _AFFORDANCES.motivation_families

#: v1 每次决策枚举的候选上限与注入条数（分层菜单采样后生存位 + 4 生活
#: + 14 娱乐；进食家族修正为前三名后生存位至多 10，池尾娱乐按需截断）。
MAX_CANDIDATES = 26
DEFAULT_TOP_K = 5

#: 进食候选返回的前三名条数（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」冻结值）。
MEAL_CANDIDATE_COUNT = 3
#: 成本项归一的最小时长（小时，60 秒；防零除，experiment_assumption）。
MIN_COST_DURATION_HOURS = 60.0 / 3600.0


def meal_repeat_weight(repeat_count: int) -> float:
    """@brief 感官特异性饱足降权：近 24 小时已食用 n 次的商品按 1/(1+n) 降权。

    依据 Rolls (1981) 的感官特异性饱足（sensory-specific satiety）：同种
    食物反复食用使其即时适口度下降；n=0 时权重 1（未降权）。
    """
    return 1.0 / (1.0 + max(0, repeat_count))


#: 候选时长/观察窗口的工程上限（秒）：防止整部电影/整段合同把候选坐标推到极端。
MAX_CANDIDATE_SECONDS = 4 * 3600
#: 习惯项最多回看的足迹事件数（有界化，避免长运行下逐条扫描全历史）。
HABIT_EVENT_WINDOW = 200


from worldmock.exchange.motivation import MotivationTransforms, load_motivation_transforms


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

def classify_action_event(tool_name: str, request: Mapping) -> tuple[str, ...]:
    """@brief 将请求中的每个动作逐项归入习惯家族；不可判定项不计数。

    归并键是内部标准动作名，不是模型动词：start_activity 逐项取 items[].way，经方式闭集（WAYS）映射为内部
    动作，其余稳定工具取工具名本身（归并路由自注册表 families.*.habit_members 展开，
    docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面「稳定工具面」）。
    """

    if tool_name == "start_activity":
        items = request.get("items") if isinstance(request, Mapping) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 2:
            return ()
        ways = [item["way"] for item in items]
        names = [WAYS[way][1] for way in ways if isinstance(way, str) and way in WAYS]
    else:
        names = [tool_name]
    return tuple(family for name in names
                 if (family := _AFFORDANCES.classify_habit_event(name)) is not None)


def habit_shares(events: Sequence[tuple[str, Mapping]], discount: float) -> dict[str, float]:
    """@brief 按指数贴现计数把足迹动作归一为各家族份额 [0,1]。

    第 k 新的事件权重 discount^k（最新为 1）；只统计可判定家族，份额分母
    也只含可判定事件。空足迹返回全零份额。
    """
    weights = {family: 0.0 for family in FAMILIES}
    total = 0.0
    for age, (tool_name, request) in enumerate(reversed(events)):
        families = classify_action_event(tool_name, request)
        weight = discount ** age
        for family in families:
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
    catalog: Catalog
    location_ids: frozenset[str]
    menu: InteractionMenu
    lifestyle_items: tuple[LifestyleItem, ...]
    activity_location_kinds: Mapping[str, frozenset[str]]
    tasks: tuple[RuntimeWorkTask, ...]
    task_totals: Mapping[str, tuple[int, float]]
    #: 继续活动只读取实际进行中的活动，不从应用内容目录生成候选。
    active_activities: tuple[ActivityState, ...]
    habit_events: tuple[tuple[str, Mapping], ...]
    consecutive_rejections: int
    #: 同刻决策上限（场景 max_same_moment_decisions）：受挫度的分母。
    max_same_moment_decisions: int
    total_effective_seconds: float
    current_time: int
    end_time: int
    timezone: str
    #: 人物启用的生活活动闭集（None=全局启用，与 AgentConfig 语义一致）。
    lifestyle_enabled: tuple[str, ...] | None = None
    #: 近 24 小时各商品已进食次数（product_id -> n）：进食候选降权依据。
    recent_meal_counts: Mapping[str, int] = field(default_factory=dict)


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
    sleep_deficit_delta: float = 0.0
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
        """@brief 生成写入 motivation_internal 的 JSON 载荷（相对分呈现）。

        docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」：候选、名次与概率不进提示，只写
        observation.data.motivation_internal 供评估与研究。模型会把负的
        绝对分读成“不值得做”（动机验证轮 a2 08:00 实证），因此只给
        rank（1 起）、softmax 概率与四分量权衡（tradeoff）。
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
    background = _ScoringBackground(shares, *emotion_base)
    scored = [
        _score_candidate(candidate, context, profile, transforms, background)
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

    受挫度 = 连续拒绝次数 / 同刻上限，截到 1（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「实现约定」）；
    目标缺口 = 1 - 有效工时进度比（累计口径，按日切窗需要日粒度足迹聚合，v1 先用累计对单日志向的比值，注明简化）。
    """
    frustration = (min(1.0, context.consecutive_rejections / context.max_same_moment_decisions)
                   if context.max_same_moment_decisions > 0 else 0.0)
    aspiration_seconds = max(1.0, float(profile.aspiration["daily_effective_work_seconds"]))
    progress = min(1.0, context.total_effective_seconds / aspiration_seconds)
    return frustration, 1.0 - progress


def _work_efficiency_snapshot(body: BodyConfig, energy_kcal: float,
                              sleep_deficit_hours: float,
                              entertainment_need: float) -> float:
    """@brief 以给定身体状态取工作效率因子起点插值连乘（工具因子不适用）。

    因子的输入资源名是目录标识（body.CATALOG_SLEEP_RESOURCE 口径）：
    "sleep_debt_hours" 与 "sleep_deficit_hours" 都解析到不足账数值。
    """
    values = {"sleep_deficit_hours": sleep_deficit_hours, "sleep_debt_hours": sleep_deficit_hours,
              "energy_kcal": energy_kcal, "entertainment_need": entertainment_need}
    product = 1.0
    for factor in body.work_efficiency_factors:
        product *= interpolate(factor.curve, values[factor.input_resource])
    return product


def _awake_deficit_delta(body_state, hours: float) -> float:
    """@brief 清醒候选的睡眠不足账增量（与结算同式：need/(24−need) 每小时）。

    新标度（need=7.5h 时约 0.4545 h/h）由 sleep.deficit_after_awake 承载，
    与 body.settle_body 的结算路径一致；钳制语义（[0, 24]）同样由它承载。
    """
    after = sleep.deficit_after_awake(body_state.sleep_deficit_hours, hours,
                                      body_state.sleep_need_hours)
    return after - body_state.sleep_deficit_hours


def _homeostatic(candidate: _Candidate, context: MotivationContext,
                 profile: MotivationProfile, transforms: MotivationTransforms) -> float:
    """@brief 内稳态分量：sum_i w_i * [phi_i(c_after) - phi_i(c_now)]。

    各资源坐标归一到“越高越好”：钱/能量以档案 comfort 为参考点，睡眠
    不足账与娱乐需要以 0 为参考点取负号，目标进度以日志向（单日志向
    完成度）为参考。完成时点才兑现的钱流按 beta 现时偏折现后进入坐标；
    候选价格不进钱坐标（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」：价格只在成本项计一次）。
    """
    agent = context.agent
    beta = profile.beta_present_bias
    deferred_hours = (candidate.delay_seconds + candidate.duration_seconds) / 3600
    money_after = context.balance_fen + candidate.money_deferred_fen * beta ** deferred_hours
    energy_after = agent.body.energy_kcal + candidate.energy_delta
    deficit_after = agent.body.sleep_deficit_hours + candidate.sleep_deficit_delta
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
        ("sleep_debt_hours", -agent.body.sleep_deficit_hours / transforms.sleep_scale_hours,
         -deficit_after / transforms.sleep_scale_hours),
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


def _cost_component(candidate: _Candidate, profile: MotivationProfile,
                    transforms: MotivationTransforms) -> float:
    """@brief 成本项：价格 + 时间机会成本，按候选时长归一到单位小时效用。

    docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」：价格只在成本项计一次（不进内稳态钱坐标）；时间机会
    成本覆盖等待+执行全程（被等待门控的流按 beta 现时偏折现），整个成本
    除以候选时长得到单位小时口径——10 分钟与 120 分钟候选的成本可比。
    """
    duration_hours = max(candidate.duration_seconds / 3600.0, MIN_COST_DURATION_HOURS)
    engaged_hours = (candidate.delay_seconds + candidate.duration_seconds) / 3600.0
    price_fen = abs(min(0.0, candidate.money_now_fen))
    time_fen = (beta_discount(profile, candidate.delay_seconds) * engaged_hours
                * transforms.time_value_fen_per_hour)
    return (price_fen + time_fen) / transforms.money_scale_fen / duration_hours


@dataclass(frozen=True)
class _ScoringBackground:
    """@brief 一次求值内不变的背景量：家族习惯份额与情绪基线。"""

    shares: Mapping[str, float]
    frustration: float
    deficit: float


def _score_candidate(candidate: _Candidate, context: MotivationContext,
                     profile: MotivationProfile, transforms: MotivationTransforms,
                     background: _ScoringBackground) -> ScoredCandidate:
    """@brief 计算单个候选的四分量与总分 V = homeostatic + habit + emotion - cost。"""
    homeostatic = _homeostatic(candidate, context, profile, transforms)
    habit = transforms.habit_weight * background.shares.get(candidate.family, 0.0)
    frustration, deficit = background.frustration, background.deficit
    emotion = profile.emotion_sensitivity * (
        frustration * (1.0 if candidate.comfort_type else 0.0)
        + deficit * (1.0 if candidate.work_type else 0.0))
    cost = _cost_component(candidate, profile, transforms)
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
    """@brief 按注册表 enumeration_rank 排序遍历家族枚举候选并截断到 MAX_CANDIDATES。

    生成器与枚举次序直接由冻结注册表决定。应用使用只参与实际活动的习惯分类，不生成内容候选；生活活动仍按目录采样。
    """
    pool: list[_Candidate] = []
    for family in _AFFORDANCES.families_by_rank():
        if not any(_AFFORDANCES.kinds[kind].action in context.menu.allowed_actions for kind in family.kinds):
            continue
        generator = PROTOTYPE_GENERATORS.get(family.generator)
        if generator is None:
            raise IntegrityError(
                f"candidate generator {family.generator!r} has no registered prototype")
        pool.extend(generator(context, profile, family))
    return pool[:MAX_CANDIDATES]




def _sleep_candidate(context: MotivationContext, params: Mapping) -> _Candidate | None:
    """@brief 睡眠候选：清不足账所需时长（cap_seconds 上限），无账时 fallback 小睡。

    恢复速率走 sleep.DEFICIT_RECOVERY_PER_SLEEP_H（新标度 1h/h，与结算
    同式），不再读旧 sleep_recovery_per_hour 目录参数。
    """
    agent = context.agent
    deficit = agent.body.sleep_deficit_hours
    if deficit > 0:
        seconds = math.ceil(deficit / sleep.DEFICIT_RECOVERY_PER_SLEEP_H * 3600)
    else:
        seconds = params["fallback_seconds"]
    duration = min(max(params["min_session_seconds"], seconds), params["cap_seconds"],
                   MAX_CANDIDATE_SECONDS, context.end_time - context.current_time)
    if duration < 60:
        return None
    hours = duration / 3600
    deficit_after = sleep.deficit_after_sleep(deficit, hours)
    burn = minimum_energy_cost(ActivityKind.SLEEP, duration, context.body)
    energy_after = max(0.0, agent.body.energy_kcal - burn)
    return _Candidate(
        family=FAMILY_SLEEP,
        summary=f"Sleep for {duration / 3600:.1f}h",
        reason=(f"Sleeping {duration / 3600:.1f}h clears sleep deficit "
                f"{deficit:.1f}h -> {deficit_after:.1f}h"),
        duration_seconds=duration,
        energy_delta=energy_after - agent.body.energy_kcal,
        sleep_deficit_delta=deficit_after - deficit,
        comfort_type=True)


def _feasible_food_rows(context: MotivationContext) -> list[tuple[Any, float, float, str]]:
    """@brief 枚举实际持有及固定菜单可得食品，配送价格和等待只取固定规则。"""
    agent, catalog = context.agent, context.catalog
    if any(activity.kind == ActivityKind.TRAVEL for activity in context.active_activities):
        return []
    rows = []
    efficiency = sleep.food_utilization_efficiency(agent.body.sleep_deficit_hours)
    for food in catalog.foods.values():
        constants = FOOD_CONSTANTS[food.category]
        burn = minimum_energy_cost(ActivityKind.CONSUME, constants.duration_seconds, context.body)
        if constants.energy_kcal * efficiency <= burn:
            continue
        if agent.inventory.get(agent.location_id, {}).get(food.food_id, 0) >= 1:
            rows.append((food, 0.0, 0.0, food.food_id))
        for store_id, food_ids in catalog.foods_by_store.items():
            if food.food_id not in food_ids or store_id not in context.location_ids:
                continue
            if store_id == agent.location_id:
                price, wait = food.price_fen, 0
            elif catalog.apps_by_store[store_id]:
                price = food.price_fen + delivery_fee_fen()
                wait = delivery_seconds(catalog.shortest_m(store_id, agent.location_id))
            else:
                continue
            if price <= context.balance_fen:
                rows.append((food, float(price), float(wait), f"{store_id}:{food.food_id}"))
    return rows




def _eat_candidates(context: MotivationContext) -> list[_Candidate]:
    """@brief 正餐只取 FoodCategory.MEAL；无可得正餐才选择最接近能量缺口的零食或饮品。"""
    rows = _feasible_food_rows(context)
    if not rows:
        return []
    gap = max(0.0, context.body.maximum_energy_kcal - context.agent.body.energy_kcal)
    meals = [row for row in rows if row[0].category == FoodCategory.MEAL]
    accepted = meals or [min(rows, key=lambda row: (abs(FOOD_CONSTANTS[row[0].category].energy_kcal - gap),
                                                    row[0].food_id, row[3]))]

    def rank_key(row: tuple[Any, float, float, str]) -> tuple:
        """@brief 实际类别热量缺口、近一天食用次数与真实费用决定餐候选顺序。"""
        food, price, wait, identity = row
        repeats = context.recent_meal_counts.get(food.food_id, 0)
        return (abs(FOOD_CONSTANTS[food.category].energy_kcal - gap) / meal_repeat_weight(repeats),
                price, wait, identity)

    return [_eat_candidate(context, row) for row in sorted(accepted, key=rank_key)[:MEAL_CANDIDATE_COUNT]]


def _eat_candidate(context: MotivationContext, row: tuple[Any, float, float, str]) -> _Candidate:
    """@brief 食品候选使用固定类别热量、实际利用效率、身体上限与真实等待。"""
    food, price, wait, identity = row
    agent = context.agent
    constants = FOOD_CONSTANTS[food.category]
    duration = float(constants.duration_seconds)
    hours = duration / 3600
    efficiency = sleep.food_utilization_efficiency(agent.body.sleep_deficit_hours)
    gain = max(0.0, min(constants.energy_kcal * efficiency,
                        context.body.maximum_energy_kcal - agent.body.energy_kcal))
    burn = minimum_energy_cost(ActivityKind.CONSUME, int(duration), context.body)
    energy_delta = gain - burn
    energy_after = max(0.0, agent.body.energy_kcal + energy_delta)
    prefix = "Eat held" if price == 0 else "Buy and eat"
    return _Candidate(family=FAMILY_EAT, summary=f"{prefix} {food.food_id} ({identity})",
        reason=f"Eating {food.food_id} restores energy {agent.body.energy_kcal:.0f}->{energy_after:.0f} kcal",
        money_now_fen=-price, delay_seconds=wait, duration_seconds=duration, energy_delta=energy_delta,
        sleep_deficit_delta=_awake_deficit_delta(agent.body, hours),
        entertainment_delta=_entertainment_accrual(context, hours))




def _continue_candidate(context: MotivationContext, activity: ActivityState) -> _Candidate | None:
    """@brief 继续当前活动：按剩余计划时长推导资源流，不可行能量时不出候选。"""
    if activity.kind in (ActivityKind.CHAT, ActivityKind.CALL):
        return None  # 无当前活动；交谈/通话仅可取消且不在注册表 MET 表内
    remaining = activity.planned_duration_seconds - activity.elapsed_seconds
    if remaining < 60:
        return None
    key = engine_affordances().met_lookup_key(activity.kind.value, travel_mode=activity.travel_mode,
                                               target_id=activity.target_id)
    met = context.body.activity_met[key]
    burn = minimum_energy_cost(activity.kind, remaining, context.body, met)
    if activity.kind not in ENERGY_RECOVERY_ACTIVITY_KINDS and burn > context.agent.body.energy_kcal:
        return None
    hours = remaining / 3600
    energy_delta = -burn
    if activity.kind == ActivityKind.CONSUME:
        rate = float(activity.data.get("food_energy_kcal_per_hour", 0) or 0)
        headroom = context.body.maximum_energy_kcal - context.agent.body.energy_kcal
        energy_delta = max(0.0, min(rate * hours, headroom)) - burn
    debt_delta = _sleep_deficit_delta(context, activity.kind, hours)
    fun_delta = _entertainment_delta(context, activity, hours)
    goal_seconds = 0.0
    if activity.kind == ActivityKind.WORK:
        efficiency = _work_efficiency_snapshot(
            context.body, context.agent.body.energy_kcal,
            context.agent.body.sleep_deficit_hours, context.agent.body.entertainment_need)
        skill = float(context.agent.skill_levels.get(
            activity.data.get("required_skill", ""), 0) or 0)
        goal_seconds = remaining * efficiency * skill
    minutes = remaining / 60
    summary = f"Continue {activity.kind.value} for {minutes:.0f} min"
    reason = (f"Continuing current {activity.kind.value} activity for {minutes:.0f} min"
              f" keeps committed progress")
    if activity.kind == ActivityKind.WAIT:
        summary, reason = _wait_texts(minutes)
    return _Candidate(
        family=FAMILY_CONTINUE,
        summary=summary,
        reason=reason,
        duration_seconds=remaining,
        energy_delta=energy_delta,
        sleep_deficit_delta=debt_delta,
        entertainment_delta=fun_delta,
        goal_effective_seconds=goal_seconds,
        comfort_type=activity.kind in {ActivityKind.USE_APP})


def _wait_texts(minutes: float) -> tuple[str, str]:
    """@brief 等待只表达经过时间，不推断任何内容接触或娱乐行为。"""
    return (f"Keep waiting for {minutes:.0f} min",
            f"Waiting for {minutes:.0f} min advances awake time without content exposure")


def _sleep_deficit_delta(context: MotivationContext, kind: ActivityKind, hours: float) -> float:
    """@brief 活动引起的睡眠不足账变化：睡眠按 1h/h 恢复，其余清醒活动按新标度累积。"""
    if kind == ActivityKind.SLEEP:
        after = sleep.deficit_after_sleep(context.agent.body.sleep_deficit_hours, hours)
        return after - context.agent.body.sleep_deficit_hours
    return _awake_deficit_delta(context.agent.body, hours)


def _entertainment_accrual(context: MotivationContext, hours: float) -> float:
    """@brief 清醒非娱乐活动的娱乐需要累积（触顶钳制）。"""
    projected = context.agent.body.entertainment_need + (
        context.body.entertainment_need_per_awake_hour * hours)
    return min(1.0, projected) - context.agent.body.entertainment_need




def _entertainment_change(context: MotivationContext, relief_per_hour: float,
                          hours: float) -> float:
    """@brief 与共同身体结算（body._shared_entries）同式的娱乐需要变化：有缓解时只按缓解下降、
    不再累积，没有缓解时按清醒累积，结果限在 [0, 1]。"""
    need = context.agent.body.entertainment_need
    rate = entertainment_need_rate(relief_per_hour, context.body)
    return min(1.0, max(0.0, need + rate * hours)) - need


def _entertainment_delta(context: MotivationContext, activity: ActivityState,
                         hours: float) -> float:
    """@brief 进行中活动引起的娱乐需要变化：有缓解的活动按实际缓解速率下降、不再累积，其余累积。"""
    return _entertainment_change(context, entertainment_relief_rate(
        activity, context.body), hours)


def _travel_candidate(context: MotivationContext, label: str,
                      destination_id: str) -> _Candidate | None:
    """@brief 出行候选：登记图上可负担行程中选择最低票价的方式。"""
    agent = context.agent
    if destination_id == agent.location_id:
        return None
    plans = [(mode, plan_trip(context.catalog.graph, agent.location_id, destination_id, mode)) for mode in TravelMode]
    feasible = [(mode, trip) for mode, trip in plans if trip.fare_fen <= context.balance_fen]
    if not feasible:
        return None
    mode, trip = min(feasible, key=lambda item: (item[1].fare_fen, item[0].value))
    duration = trip.duration_seconds
    if duration < 60:
        return None
    met = context.body.activity_met[engine_affordances().met_lookup_key("travel", travel_mode=mode)]
    burn = minimum_energy_cost(ActivityKind.TRAVEL, duration, context.body, met)
    if burn > agent.body.energy_kcal:
        return None
    hours = duration / 3600
    minutes = duration / 60
    return _Candidate(
        family=FAMILY_TRAVEL,
        summary=f"Travel to {label} ({destination_id}) by {mode.value}",
        reason=(f"Traveling to {label} by {mode.value} takes {minutes:.0f} min"
                f" and costs {trip.fare_fen} fen"),
        money_now_fen=-float(trip.fare_fen),
        duration_seconds=duration,
        energy_delta=-burn,
        sleep_deficit_delta=_awake_deficit_delta(context.agent.body, hours),
        entertainment_delta=_entertainment_accrual(context, hours))


def _work_candidates(context: MotivationContext,
                     params: Mapping) -> tuple[_Candidate | None, list[_Candidate]]:
    """@brief 工作候选：至多 max_tasks 个报酬最高的可用任务，外加去工作地点的出行腿。

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
    for task in available[:params["max_tasks"]]:
        if agent.location_id != task.required_location_id:
            if travel is None:
                travel = _travel_candidate(context, f"work ({task.task_id})",
                                           task.required_location_id)
            continue
        worked, effective = context.task_totals.get(task.task_id, (0, 0.0))
        candidate = _work_slot_candidate(context, task, worked, effective, now)
        if candidate is not None:
            work.append(candidate)
    return travel, work


def _work_slot_candidate(context: MotivationContext, task: RuntimeWorkTask,
                         worked: int, effective: float, now: int) -> _Candidate | None:
    """@brief 在任务地点的一个工作候选：时长槽、折现报酬与目标进度。"""
    agent = context.agent
    slot = min(task.maximum_duration_seconds - worked,
               maximum_energy_duration_seconds(ActivityKind.WORK,
                                               agent.body.energy_kcal, context.body),
               int(task.due_at.timestamp()) - now, MAX_CANDIDATE_SECONDS)
    if slot < 60:
        return None
    efficiency = _work_efficiency_snapshot(
        context.body, agent.body.energy_kcal, agent.body.sleep_deficit_hours,
        agent.body.entertainment_need) * agent.skill_levels[task.required_skill]
    goal_seconds = slot * efficiency
    required = max(1.0, task.required_effective_seconds)
    already = min(1.0, effective / required)
    after = min(1.0, (effective + goal_seconds) / required)
    deferred = float(task.pay_fen) * (after - already)
    hours = slot / 3600
    burn = minimum_energy_cost(ActivityKind.WORK, slot, context.body)
    return _Candidate(
        family=FAMILY_WORK,
        summary=f"Work task {task.task_id} for {slot / 60:.0f} min",
        reason=(f"Working {task.task_id} for {slot / 60:.0f} min yields about "
                f"{round(deferred)} fen and advances goal progress"),
        money_deferred_fen=deferred,
        duration_seconds=slot,
        energy_delta=-burn,
        sleep_deficit_delta=_awake_deficit_delta(agent.body, hours),
        entertainment_delta=_entertainment_accrual(context, hours),
        goal_effective_seconds=goal_seconds,
        work_type=True)








def _lifestyle_candidates(context: MotivationContext, profile: MotivationProfile,
                          family: FamilySpec) -> list[_Candidate]:
    """@brief 生活活动候选：每品类采 top-1，只从人物启用集采样（docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「动机层」「世界素材品类要求」）。

    开放命名空间的分层菜单：条目目录来自仓库默认语料（183 条、9 品类），
    场景身份按 body.activity_met 的 lifestyle:<id> 键求交，再按上下文
    lifestyle_enabled（None=全局启用）过滤——照做会被拒的条目不枚举。
    候选资格按品类注意力判定（attention_prior[品类] > 0；生活活动是习惯
    型行为，不按内容口味门控），taste_weights 只作类内排序权重。时长槽取
    目录典型时长；娱乐需要按条目 effects 折算的缓解速率预演（与 activities
    结算同式，正向缓解替代清醒累积）。
    """
    params = family.generator_params
    per_kind = family.attention_params["per_kind"]
    pool_cap = family.attention_params["pool_cap"]
    met_table = context.body.activity_met
    enabled = context.lifestyle_enabled
    place = context.catalog.locations.get(context.agent.location_id)
    if place is None:
        return []
    items = [item for item in context.lifestyle_items
             if engine_affordances().met_lookup_key("lifestyle", target_id=item.activity_id) in met_table
             and feasible_kinds(frozenset(context.catalog.location_kinds(place.kind)),
                                context.activity_location_kinds[item.activity_id])
             and (enabled is None or item.activity_id in enabled)]
    ranked = sorted(
        items, key=lambda item: item.activity_id)
    sampled: list[LifestyleItem] = []
    taken_per_kind: dict[str, int] = {}
    for item in ranked:
        taken = taken_per_kind.get(item.category, 0)
        if taken >= per_kind:
            continue
        taken_per_kind[item.category] = taken + 1
        sampled.append(item)
    candidates = []
    for item in sampled[:pool_cap]:
        candidate = _lifestyle_candidate(context, item, family, params)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _lifestyle_candidate(context: MotivationContext, item: LifestyleItem,
                         family: FamilySpec, params: Mapping) -> _Candidate | None:
    """@brief 单条生活活动候选：时长槽、能量预算与 effects 缓解资源流。"""
    agent = context.agent
    met = context.body.activity_met[engine_affordances().met_lookup_key("lifestyle", target_id=item.activity_id)]
    typical = float(item.typical_duration_minutes * 60)
    outer_cap = min(
        maximum_energy_duration_seconds(ActivityKind.LIFESTYLE,
                                        agent.body.energy_kcal, context.body, met),
        float(params["cap_seconds"]), MAX_CANDIDATE_SECONDS,
        float(context.end_time - context.current_time))
    slot = min(typical, outer_cap)
    slot = max(slot, min(float(params["min_session_seconds"]), outer_cap))
    if slot < 60:
        return None
    burn = minimum_energy_cost(ActivityKind.LIFESTYLE, slot, context.body, met)
    if burn > agent.body.energy_kcal:
        return None
    hours = slot / 3600
    relief = min(lifestyle_relief_rates(item.effects)[0] * hours,
                 agent.body.entertainment_need)
    fun_after = agent.body.entertainment_need - relief
    return _Candidate(
        family=family.name,
        summary=(f"Do {item.name} (lifestyle:{item.activity_id}) "
                 f"for {slot / 60:.0f} min"),
        reason=(f"{item.name} is a {item.category} activity in the browsed "
                f"lifestyle catalog; a {slot / 60:.0f} min session burns about "
                f"{burn:.0f} kcal and relieves entertainment need "
                f"{agent.body.entertainment_need:.2f}->{fun_after:.2f} "
                f"(start via start_activity action do "
                f"target {item.activity_id})"),
        duration_seconds=slot,
        energy_delta=-burn,
        sleep_deficit_delta=_awake_deficit_delta(agent.body, hours),
        entertainment_delta=-relief)


# ---------------------------------------------------------------------------
# 候选生成器原型登记：原型函数保持 Python 实现，登记名进 PROTOTYPE_GENERATORS。
# 选择过程见 docs/design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎「从目录到候选」；_enumerate_candidates 按注册表
# families.*.candidate.generator 从此表取函数，枚举序按 enumeration_rank。
# 原型统一接收 FamilySpec：candidate 参数经 generator_params、注意力采样参数
# 经 attention_params 各取各的（content_menu 家族两者都用）。
# ---------------------------------------------------------------------------

def _clear_backlog_adapter(context: MotivationContext, profile: MotivationProfile,
                           family: FamilySpec) -> list[_Candidate]:
    """@brief clear_backlog 原型：清债睡眠候选（sleep 家族）。"""
    candidate = _sleep_candidate(context, family.generator_params)
    return [candidate] if candidate is not None else []


def _cheapest_feasible_meal_adapter(context: MotivationContext, profile: MotivationProfile,
                                    family: FamilySpec) -> list[_Candidate]:
    """@brief cheapest_feasible_meal 原型：可行餐前三名（eat 家族；门槛与降权内建）。"""
    return _eat_candidates(context)


def _continue_current_adapter(context: MotivationContext, profile: MotivationProfile,
                              family: FamilySpec) -> list[_Candidate]:
    """@brief continue_current 原型：继续当前活动（continue 家族）。"""
    return [candidate for activity in context.active_activities
            if (candidate := _continue_candidate(context, activity)) is not None]


def _cheapest_route_adapter(context: MotivationContext, profile: MotivationProfile,
                            family: FamilySpec) -> list[_Candidate]:
    """@brief cheapest_route 原型：可负担最低票价出行腿（travel 家族；
    destination=home；task_location 腿内建在 task_slot 原型里）。"""
    label = family.generator_params.get("destination", "home")
    candidate = _travel_candidate(context, label, context.home_location_id)
    return [candidate] if candidate is not None else []




def _content_menu_adapter(context: MotivationContext, profile: MotivationProfile,
                          family: FamilySpec) -> list[_Candidate]:
    """@brief content_menu 原型只从本人启用的生活活动目录生成候选。"""
    return _lifestyle_candidates(context, profile, family)


def _task_slot_adapter(context: MotivationContext, profile: MotivationProfile,
                       family: FamilySpec) -> list[_Candidate]:
    """@brief task_slot 原型：去工作地点出行腿 + 至多 max_tasks 个任务候选
    （work 家族；出行腿排在任务前，与现行枚举序一致）。"""
    travel, work = _work_candidates(context, family.generator_params)
    return [candidate for candidate in (travel, *work) if candidate is not None]


PROTOTYPE_GENERATORS.update(
    clear_backlog=_clear_backlog_adapter,
    cheapest_feasible_meal=_cheapest_feasible_meal_adapter,
    continue_current=_continue_current_adapter,
    cheapest_route=_cheapest_route_adapter,
    content_menu=_content_menu_adapter,
    task_slot=_task_slot_adapter,
)
