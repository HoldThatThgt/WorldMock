"""@brief 动机评分层：为决策时刻枚举有界候选行为并按四分量打分。

求值器按“内稳态 + 习惯 + 情绪 − 成本”四分量给每个候选行为打分，softmax(V/tau)
只作排序参考，top-k 连同相对呈现（rank、softmax 概率、四分量权衡）与英文
理由写入 observation.data.motivation_internal（docs/dev/SPEC-life-realism.md「动机层」：内部
仪表不进提示，只供评估与研究）。不暴露绝对总分，负分会被模型误读成
“不值得做”。提示是建议不是账：求值只读状态，任何失败都在引擎侧
fail-open（不注入、不崩），结算路径完全不感知本模块。

评分器口径（docs/dev/SPEC-life-realism.md「动机层」）：成本按候选时长归一（单位小时效用），
价格只在成本项计一次；进食候选按热量门槛/缺口匹配取前三；同一娱乐
家族至多两条候选，娱乐需要低于 0.3 时不生成娱乐候选；生活活动只从
人物启用集采样并按 effects 折算缓解；近 24 小时已食用商品按 1/(1+次数)
降权（Rolls 1981 感官特异性饱足）；候选偏好权重来自 taste_weights
（画像编译 v2 的确定性口味匹配，无缺省抬升）。

通用变换形状（前景指数、归一尺度、时间价值、习惯权重）来自
catalog/rules/motivation.toml；个体参数（weights/tau/beta/loss_aversion/
comfort/emotion_sensitivity/attention_prior）来自场景 agents[].motivation 档案。
"""

from __future__ import annotations

import json
import math
import tomllib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import Field

from worldmock.catalog.affordances import (
    FamilySpec,
    PROTOTYPE_GENERATORS,
    default_affordances,
)
from worldmock.errors import IntegrityError
from worldmock.life import sleep
from worldmock.life.activities import lifestyle_relief_rates
from worldmock.life.body import (
    ENERGY_RECOVERY_ACTIVITY_KINDS,
    interpolate,
    maximum_energy_duration_seconds,
    minimum_energy_cost,
)
from worldmock.life.content import (
    LIFESTYLE_ITEMS_PATH,
    LifestyleItem,
    load_lifestyle_items,
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

#: 可供性注册表视图（冻结文件加载一次；家族闭集、习惯归并路由、枚举序、
#: 候选生成器原型与采样参数全部经它派生——M1 起注册表即事实）。
_AFFORDANCES = default_affordances()

#: 候选家族名（习惯项的归一键）。
FAMILY_CONTINUE = "continue"
FAMILY_SLEEP = "sleep"
FAMILY_EAT = "eat"
FAMILY_TRAVEL = "travel"
FAMILY_BROWSE = "browse"
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

#: 娱乐候选按品类采样的每品类上限（7 内容品类 x 2 = 14 条娱乐候选）——
#: 自注册表 families.entertainment.attention.per_kind 派生。
ENTERTAINMENT_PER_KIND = _AFFORDANCES.attention_params(FAMILY_ENTERTAINMENT)["per_kind"]

#: 同一娱乐家族（内容品类）至多两条候选（docs/dev/SPEC-life-realism.md「动机层」冻结值：代码侧钳制，
#: 注册表 per_kind 声明大于该值时仍按 2 截断）。
ENTERTAINMENT_FAMILY_CAP = 2
#: 娱乐需要低于该值时不生成娱乐候选（docs/dev/SPEC-life-realism.md「动机层」冻结值 0.3）。
ENTERTAINMENT_NEED_FLOOR = 0.3
#: 进食候选的热量门槛（docs/dev/SPEC-life-realism.md「动机层」：正餐热量不低于 300 kcal，规格冻结值）。
MEAL_KCAL_FLOOR = 300.0
#: 进食候选返回的前三名条数（docs/dev/SPEC-life-realism.md「动机层」冻结值）。
MEAL_CANDIDATE_COUNT = 3
#: 成本项归一的最小时长（小时，60 秒；防零除，experiment_assumption）。
MIN_COST_DURATION_HOURS = 60.0 / 3600.0


def meal_repeat_weight(repeat_count: int) -> float:
    """@brief 感官特异性饱足降权：近 24 小时已食用 n 次的商品按 1/(1+n) 降权。

    依据 Rolls (1981) 的感官特异性饱足（sensory-specific satiety）：同种
    食物反复食用使其即时适口度下降；n=0 时权重 1（未降权）。
    """
    return 1.0 / (1.0 + max(0, repeat_count))


@lru_cache(maxsize=1)
def _repo_lifestyle_items() -> tuple[LifestyleItem, ...]:
    """@brief 仓库默认语料的生活活动目录（lifestyle 候选枚举的品类/时长来源）。

    动机上下文只携带场景 MET 表（body.activity_met 的 lifestyle:<id> 键），
    不携带目录条目本身；候选生成按 MET 键与目录求交界定场景身份——键不在
    场景 MET 表内的条目不枚举。解析复用 content.load_lifestyle_items 的
    fail-closed 行（重复 id/坏字段拒绝），身份核对表从语料自身的 met_value
    合成（引擎侧另有 语料↔场景 MET 的一一核对，两道闸口互为冗余）。
    """
    root = Path(__file__).resolve().parents[3] / "corpora"
    payload = json.loads((root / LIFESTYLE_ITEMS_PATH).read_text(encoding="utf-8"))
    met = {f"lifestyle:{row['activity_id']}": float(row["met_value"])
           for row in payload["items"]}
    return load_lifestyle_items(root, met)

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
    """@brief 把一次动作请求归入候选家族；不可判定的动作返回 None。

    归并路由自注册表 families.*.habit_members 展开的查找表取得（精确 >
    前缀 lifestyle:* > 通配；start_basic_activity 非 sleep 一律归 continue
    的现行决定已显式写进 continue 家族的通配成员）。
    """
    activity_type = request.get("activity_type") if isinstance(request, Mapping) else None
    return _AFFORDANCES.classify_habit_event(tool_name, activity_type)


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
    #: 内容候选是品类摘要 + 每品类 top-2 采样（引擎侧 _sample_contents 装配），
    #: 不是全量目录；评分器对传入列表仍按品类重采样，直接构造全量亦安全。
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
    #: 品类摘要（kind -> 条目数）：观察 activity_summary 的内容段同源。
    content_category_counts: Mapping[str, int] = field(default_factory=dict)
    #: 确定性口味权重（画像编译 v2 的 taste_weights，item_id -> [0,1]；
    #: 缺省空映射=未接线，条目权重一律 0，不做 0.5 缺省抬升）。
    taste_weights: Mapping[str, float] = field(default_factory=dict)
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

        docs/dev/SPEC-life-realism.md「动机层」：候选、名次与概率不进提示，只写
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

    受挫度 = 连续拒绝次数 / 上限；目标缺口 = 1 - 有效工时进度比（累计口径，
    按日切窗需要日粒度足迹聚合，v1 先用累计对单日志向的比值，注明简化）。
    """
    frustration = (context.consecutive_rejections / context.max_consecutive_rejections
                   if context.max_consecutive_rejections > 0 else 0.0)
    aspiration_seconds = max(1.0, float(profile.aspiration["daily_effective_work_seconds"]))
    progress = min(1.0, context.total_effective_seconds / aspiration_seconds)
    return min(1.0, frustration), 1.0 - progress


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
    候选价格不进钱坐标（docs/dev/SPEC-life-realism.md「动机层」：价格只在成本项计一次）。
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

    docs/dev/SPEC-life-realism.md「动机层」：价格只在成本项计一次（不进内稳态钱坐标）；时间机会
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

    现行优先级（注册表枚举序）：进食(10，门槛筛选取前三) > 睡眠(20) >
    继续当前活动(30) > 去工作地点+工作任务至多2(40) > 回家(50) >
    浏览商品目录(60) > 生活活动(60，同秩按名排在 browse 后；每品类 top-1
    采样 ≤4，只从人物启用集采样) > 娱乐(70，每品类 top-2 采样 ≤14、需要
    ≥0.3)。生存与目标导向优先于消遣，浏览（工具性：解锁进食候选的枚举）
    排在纯消遣之前；内容类候选在池尾按采样权重排序，截断从队尾开始，
    保证任何决策点的候选数不超过 MAX_CANDIDATES 且存活的是最强候选。
    """
    pool: list[_Candidate] = []
    for family in _AFFORDANCES.families_by_rank():
        generator = PROTOTYPE_GENERATORS.get(family.generator)
        if generator is None:
            raise IntegrityError(
                f"candidate generator {family.generator!r} has no registered prototype")
        pool.extend(generator(context, profile, family))
    return pool[:MAX_CANDIDATES]


def _browse_candidate(context: MotivationContext, params: Mapping) -> _Candidate | None:
    """@brief 浏览候选：目录中尚有未见过商品时给出（probe_duration_seconds 速览）。

    浏览是工具性动作：browse 之后商品目录对人物可见，进食候选才可
    枚举——因此只要还有没见过的商品就保持此候选在场。
    """
    seen = {product.product_id for product in context.products}
    if not any(product.product_id not in seen for product in context.catalog_products):
        return None
    duration = min(params["probe_duration_seconds"], MAX_CANDIDATE_SECONDS,
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
        sleep_deficit_delta=_awake_deficit_delta(context.agent.body, hours),
        entertainment_delta=_entertainment_accrual(context, hours))


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


def _merchant_open(context: MotivationContext, product: ProductConfig) -> bool:
    """@brief 以场景本地小时执行商家冻结营业时间（与 commerce 口径一致）。"""
    location = context.locations.get(product.merchant_location_id)
    if location is None:
        return False
    from datetime import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.fromtimestamp(context.current_time, ZoneInfo(context.timezone)).hour
    return location.open_hour <= hour < location.close_hour


def _feasible_food_rows(context: MotivationContext) -> list[tuple[ProductConfig, float, float]]:
    """@brief 可行食品枚举（手持优先，其次可购）：返回 (商品, 价格, 等待秒) 行。

    可行性口径：报价可负担、库存可取、商家营业，且净能量恢复为正
    （进食活动自身代谢后仍有盈余——净负的“餐”不构成恢复性进食）。
    不在商家处时走配送，等待 = 交付延迟 + 商家到当前位置的冻结驾驶配送
    路线时长。食品利用效率与结算同式（sleep.food_utilization_efficiency，
    输入睡眠不足账）。
    """
    agent = context.agent
    efficiency = sleep.food_utilization_efficiency(agent.body.sleep_deficit_hours)
    rows: list[tuple[ProductConfig, float, float]] = []
    for product in context.products:
        if "eat" not in product.use_modes:
            continue
        burn = minimum_energy_cost(ActivityKind.CONSUME,
                                   int(product.use_duration_seconds), context.body)
        if product.energy_kcal_per_unit * efficiency <= burn:
            continue
        holding = float(agent.inventory.get(agent.location_id, {}).get(product.product_id, 0))
        if holding >= 1:
            rows.append((product, 0.0, 0.0))
            continue
        if (product.price_fen > context.balance_fen
                or context.merchant_stock.get(product.product_id, 0) < 1
                or not _merchant_open(context, product)):
            continue
        if agent.location_id == product.merchant_location_id:
            wait = 0.0
        else:
            route = _delivery_route_seconds(context, product.merchant_location_id,
                                            agent.location_id)
            if route is None:
                continue
            wait = float(product.delivery_delay_seconds + route)
        rows.append((product, float(product.price_fen), wait))
    return rows


def _eat_candidates(context: MotivationContext) -> list[_Candidate]:
    """@brief 进食候选：热量门槛/缺口匹配筛选取前三（docs/dev/SPEC-life-realism.md「动机层」）。

    接受口径：热量不低于 300 kcal 的食品；全部低于门槛时按“最接近缺口的
    正餐类食品”兜底取一条（目录 use_modes 含 eat 的商品即正餐类候选面）。
    排序键 = |热量−能量缺口| / 感官饱足权重（近 24 小时已食用 n 次按
    1/(1+n) 降权，Rolls 1981），再比价格、等待与身份；返回前三名。
    """
    rows = _feasible_food_rows(context)
    if not rows:
        return []
    gap = max(0.0, context.body.maximum_energy_kcal - context.agent.body.energy_kcal)
    accepted = [row for row in rows if row[0].energy_kcal_per_unit >= MEAL_KCAL_FLOOR]
    if not accepted:
        accepted = [min(rows, key=lambda row: (abs(row[0].energy_kcal_per_unit - gap),
                                               row[0].product_id))]

    def rank_key(row: tuple[ProductConfig, float, float]) -> tuple:
        product, price, wait = row
        repeats = context.recent_meal_counts.get(product.product_id, 0)
        return (abs(product.energy_kcal_per_unit - gap) / meal_repeat_weight(repeats),
                price, wait, product.product_id)

    return [_eat_candidate(context, row)
            for row in sorted(accepted, key=rank_key)[:MEAL_CANDIDATE_COUNT]]


def _eat_candidate(context: MotivationContext,
                   row: tuple[ProductConfig, float, float]) -> _Candidate:
    """@brief 由可行行构造单个进食候选（资源流与英文理由）。"""
    product, price, wait = row
    agent = context.agent
    duration = float(product.use_duration_seconds)
    hours = duration / 3600
    efficiency = sleep.food_utilization_efficiency(agent.body.sleep_deficit_hours)
    raw_gain = product.energy_kcal_per_unit * efficiency
    burn = minimum_energy_cost(ActivityKind.CONSUME, int(duration), context.body)
    headroom = context.body.maximum_energy_kcal - agent.body.energy_kcal
    gain = max(0.0, min(raw_gain, headroom))
    energy_delta = gain - burn
    energy_after = max(0.0, agent.body.energy_kcal + energy_delta)
    efficiency_before = _work_efficiency_snapshot(
        context.body, agent.body.energy_kcal, agent.body.sleep_deficit_hours,
        agent.body.entertainment_need)
    efficiency_after = _work_efficiency_snapshot(
        context.body, energy_after, agent.body.sleep_deficit_hours,
        agent.body.entertainment_need)
    prefix = "Eat held" if price == 0 else "Buy and eat"
    return _Candidate(
        family=FAMILY_EAT,
        summary=f"{prefix} {product.product_id}",
        reason=(f"Eating {product.product_id} now restores energy "
                f"{agent.body.energy_kcal:.0f}->{energy_after:.0f} kcal, work efficiency "
                f"{efficiency_before:.2f}->{efficiency_after:.2f}"),
        money_now_fen=-price,
        delay_seconds=wait,
        duration_seconds=duration,
        energy_delta=energy_delta,
        sleep_deficit_delta=_awake_deficit_delta(agent.body, hours),
        entertainment_delta=_entertainment_accrual(context, hours))


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
    elif activity.kind == ActivityKind.LIFESTYLE:
        met = context.body.activity_met[f"lifestyle:{activity.target_id}"]
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
        summary, reason = _wait_texts(context, minutes)
    return _Candidate(
        family=FAMILY_CONTINUE,
        summary=summary,
        reason=reason,
        duration_seconds=remaining,
        energy_delta=energy_delta,
        sleep_deficit_delta=debt_delta,
        entertainment_delta=fun_delta,
        goal_effective_seconds=goal_seconds,
        comfort_type=activity.kind in {ActivityKind.WATCH, ActivityKind.PLAY_GAME})


def _wait_texts(context: MotivationContext, minutes: float) -> tuple[str, str]:
    """@brief wait=边等边刷的候选文案：呈现被动缓解而非干坐。"""
    rate = context.body.entertainment_relief_per_hour.get(ActivityKind.WAIT.value, 0)
    summary = f"Keep waiting while casually browsing for {minutes:.0f} min"
    reason = (f"Waiting while casually browsing for {minutes:.0f} min "
              f"(passive relief ~{rate:.2f}/h)")
    return summary, reason


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


def _preference(context: MotivationContext, item_id: str) -> float:
    """@brief 条目的口味权重（taste_weights 查表，缺项 0——不做 0.5 缺省抬升）。"""
    return context.taste_weights.get(item_id, 0.0)


def _entertainment_delta(context: MotivationContext, activity: ActivityState,
                         hours: float) -> float:
    """@brief 活动引起的娱乐需要变化：观看/游戏按实际缓解速率，其余累积。"""
    preference = _preference(context, activity.target_id or "")
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
    if activity.kind == ActivityKind.WAIT:
        # wait=边等边刷：被动缓解抵消部分清醒累积（净效果仍为缓慢正累积），
        # 与 body.py 结算分支的速率语义一致，不额外降权候选。
        rate = context.body.entertainment_relief_per_hour.get(ActivityKind.WAIT.value, 0)
        accrual = _entertainment_accrual(context, hours)
        relief = min(rate * hours, context.agent.body.entertainment_need + max(accrual, 0))
        return accrual - relief
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


def _work_slot_candidate(context: MotivationContext, task: WorkTaskConfig,
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


def _entertainment_candidates(context: MotivationContext,
                              profile: MotivationProfile,
                              family: FamilySpec) -> list[_Candidate]:
    """@brief 娱乐候选：每内容品类采 top-N（attention_prior x 口味加权）。

    docs/dev/SPEC-life-realism.md「动机层」：娱乐需要低于 0.3 时不生成娱乐候选；同一娱乐家族
    （内容品类）至多两条候选（代码侧钳制 ENTERTAINMENT_FAMILY_CAP，注册表
    per_kind 更大时仍按 2 截断）。偏好权重只来自 taste_weights（确定性
    口味匹配），无缺省抬升。时长槽按分段语义给：一次约两段典型时长，
    看完的内容从头再来；缓解速率与引擎口径一致（条目费率 x 口味 ÷ 当前
    接触次数，首次与重复同式）。
    """
    agent = context.agent
    if agent.body.entertainment_need < ENTERTAINMENT_NEED_FLOOR:
        return []
    per_kind = min(family.attention_params["per_kind"], ENTERTAINMENT_FAMILY_CAP)
    pool_cap = family.attention_params["pool_cap"]
    ranked = sorted(
        (item for item in context.contents
         if profile.attention_prior.get(item.kind, 0) > 0),
        key=lambda item: (-profile.attention_prior.get(item.kind, 0)
                          * _preference(context, item.item_id), item.item_id))
    sampled: list = []
    taken_per_kind: dict[str, int] = {}
    for item in ranked:
        taken = taken_per_kind.get(item.kind, 0)
        if taken >= per_kind:
            continue
        taken_per_kind[item.kind] = taken + 1
        sampled.append(item)
    candidates = []
    for item in sampled[:pool_cap]:
        candidate = _watch_candidate(context, item, family)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _watch_candidate(context: MotivationContext, item: ContentItemConfig,
                     family: FamilySpec) -> _Candidate | None:
    """@brief 单条内容的观看候选：时长槽、相对呈现摘要与缓解资源流。"""
    agent = context.agent
    params = family.generator_params
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
    slot = max(slot, min(float(params["min_session_seconds"]), outer_cap))
    if slot < 60:
        return None
    summary, lead = _watch_texts(item, position, typical, boundaries, slot)
    hours = slot / 3600
    relief_rate = (item.entertainment_relief_per_hour * _preference(context, item.item_id)
                   / (exposures + 1))
    relief = min(relief_rate * hours, agent.body.entertainment_need)
    fun_after = agent.body.entertainment_need - relief
    burn = minimum_energy_cost(ActivityKind.WATCH, slot, context.body)
    return _Candidate(
        family=family.name,
        summary=summary,
        reason=(f"{lead} relieves entertainment need "
                f"{agent.body.entertainment_need:.2f}->{fun_after:.2f}"),
        duration_seconds=slot,
        energy_delta=-burn,
        sleep_deficit_delta=_awake_deficit_delta(agent.body, hours),
        entertainment_delta=-relief,
        comfort_type=True)


def _watch_texts(item: ContentItemConfig, position: float, typical: float,
                 boundaries: tuple[float, ...], slot: float) -> tuple[str, str]:
    """@brief 观看候选的动作摘要与理由引导语（分段/续看/整看三形态）。"""
    verb, noun = ("Read", "chapter") if item.kind == "webnovel_excerpt" else ("Watch", "segment")
    segments_total = len(boundaries) or 1
    remaining = item.duration_seconds - position
    started = position > 0 and remaining > 0
    each = f"~{typical:.0f} sec each" if typical < 120 else f"~{typical / 60:.0f} min each"
    if segments_total > 1:
        segment_index = min(bisect_right(boundaries, position) + 1, segments_total)
        summary = f"{verb} {noun} {segment_index} of {item.item_id} (~{slot / 60:.0f} min)"
        lead = (f"Continuing {item.item_id} at {noun} {segment_index}/{segments_total}"
                if started else f"{verb}ing one {noun} of {item.item_id} ({each})")
    elif started:
        summary = f"Continue {item.item_id} from minute {position / 60:.0f} (~{slot / 60:.0f} min)"
        lead = f"Continuing {item.item_id} from minute {position / 60:.0f}"
    else:
        summary = f"{verb} {item.item_id} (~{slot / 60:.0f} min)"
        lead = f"{verb}ing {item.item_id}"
    return summary, lead


def _lifestyle_candidates(context: MotivationContext, profile: MotivationProfile,
                          family: FamilySpec) -> list[_Candidate]:
    """@brief 生活活动候选：每品类采 top-1，只从人物启用集采样（docs/dev/SPEC-life-realism.md「动机层」「世界素材品类要求」）。

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
    items = [item for item in _repo_lifestyle_items()
             if f"lifestyle:{item.activity_id}" in met_table
             and (enabled is None or item.activity_id in enabled)]
    ranked = sorted(
        (item for item in items if profile.attention_prior.get(item.category, 0) > 0),
        key=lambda item: (-profile.attention_prior.get(item.category, 0)
                          * _preference(context, item.activity_id), item.activity_id))
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
    met = context.body.activity_met[f"lifestyle:{item.activity_id}"]
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
    relief = min(lifestyle_relief_rates(item.activity_id)[0] * hours,
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
                f"(start via start_basic_activity "
                f"activity_type lifestyle:{item.activity_id})"),
        duration_seconds=slot,
        energy_delta=-burn,
        sleep_deficit_delta=_awake_deficit_delta(agent.body, hours),
        entertainment_delta=-relief)


# ---------------------------------------------------------------------------
# 候选生成器原型登记：原型函数保持 Python 实现，登记名进 PROTOTYPE_GENERATORS。
# 选择过程见 docs/design/03-resource-engine.md「从目录到候选」；_enumerate_candidates 按注册表
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
    candidate = _continue_candidate(context)
    return [candidate] if candidate is not None else []


def _cheapest_route_adapter(context: MotivationContext, profile: MotivationProfile,
                            family: FamilySpec) -> list[_Candidate]:
    """@brief cheapest_route 原型：可负担最低票价出行腿（travel 家族；
    destination=home；task_location 腿内建在 task_slot 原型里）。"""
    label = family.generator_params.get("destination", "home")
    candidate = _travel_candidate(context, label, context.home_location_id)
    return [candidate] if candidate is not None else []


def _catalog_visibility_adapter(context: MotivationContext, profile: MotivationProfile,
                                family: FamilySpec) -> list[_Candidate]:
    """@brief catalog_visibility 原型：目录速览候选（browse 家族）。"""
    candidate = _browse_candidate(context, family.generator_params)
    return [candidate] if candidate is not None else []


def _content_menu_adapter(context: MotivationContext, profile: MotivationProfile,
                          family: FamilySpec) -> list[_Candidate]:
    """@brief content_menu 原型：分层菜单内容候选（entertainment/lifestyle 家族）。

    两种条目来源形态：封闭内容条目（场景 contents，如 watch 品类）走娱乐
    分支；开放命名空间条目（met_key 含 "{item_id}" 的 kind，目录来自语料、
    身份来自场景 MET 表）走生活活动分支。采样参数（per_kind/pool_cap）与
    候选参数（min_session_seconds/cap_seconds）都按调用家族自取。
    """
    if any("{item_id}" in _AFFORDANCES.kinds[kind].met_key for kind in family.kinds):
        return _lifestyle_candidates(context, profile, family)
    return _entertainment_candidates(context, profile, family)


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
    catalog_visibility=_catalog_visibility_adapter,
    content_menu=_content_menu_adapter,
    task_slot=_task_slot_adapter,
)
