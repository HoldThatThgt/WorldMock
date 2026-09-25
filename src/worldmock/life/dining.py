"""@brief 当地餐食句与饥饿时的吃饭途径句（确定性生成，不调用模型、不扩大供给呈现窗口）。

饥饿低于「饿」时，场景的 `local_food_summary` 是原当地餐食句；饥饿为「饿」及以上时改为吃饭途径句：
按手边能吃的、在路上的、这里就能买的、能送到这里的、附近能买的、都没有六档取第一条成立的，只引用本轮
观察里已有的事实，不授权未呈现的供给；在途送达距现在超过 3600 秒时再补第 3、4、5 档中第一条成立的
（docs/dev/SPEC-time-and-energy.md「让人物趁早吃」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from worldmock.life import felt
from worldmock.life.discovery import DiscoveryContext, supply_row

#: 在途送达距现在超过该秒数时另补一条途径（experiment_assumption，docs/EXPERIMENT.md「实验假设声明」）。
LATE_DELIVERY_SECONDS = 3600
#: 改用吃饭途径句的饥饿分级：「饿」及以上，含只由能量决定的「饿得发慌」。
FOOD_PATH_LEVELS = frozenset(level for _, level in felt.APPETITE_BANDS[1:]) | {felt.APPETITE_STARVING_LEVEL}
#: 路线方式的可读称呼（途径句只写一种方式与分钟数）。
_MODE_WORDS = {"walking": "步行", "driving": "驾车"}


@dataclass(frozen=True, slots=True)
class MealOption:
    """@brief 一项可吃的食品事实：可读名称与每份热量（热量只用于排序，不写进句子）。"""

    name: str
    energy_kcal: float


@dataclass(frozen=True, slots=True)
class PendingMeal:
    """@brief 本人在途的食品订单：名称、预计送达时刻与送达地点（送到当前地点时为 None）。"""

    name: str
    expected_at: int
    place: str | None = None


@dataclass(frozen=True, slots=True)
class NearbyFood:
    """@brief 附近能买到吃的地点：地点名称、可达路线的分钟数与出行方式。"""

    place: str
    minutes: int
    mode: str


@dataclass(frozen=True, slots=True)
class FoodPathFacts:
    """@brief 生成吃饭途径句所需的本轮观察事实，各档的取数规则见模块说明。"""

    now: int
    timezone: str
    local_sentence: str
    on_hand: tuple[MealOption, ...] = ()
    pending: tuple[PendingMeal, ...] = ()
    here: tuple[MealOption, ...] = ()
    deliverable: tuple[int, ...] = ()
    nearby: tuple[NearbyFood, ...] = ()
    browse_available: bool = False


def local_meal_sentence(context: DiscoveryContext) -> str:
    """@brief 原当地餐食句：描述本人所在地点的实际餐食可购状态，不授予购买凭据。"""
    products = {item.product_id: item for item in context.scenario.products}
    local = [item for item in context.scenario.supplies
             if item.location_id == context.agent.location_id
             and "eat" in products[item.product_id].use_modes]
    if not local:
        return "当前地点没有餐食供给。"
    balance = context.state.accounts[context.agent.balance_account_id]
    for supply in local:
        row = supply_row(context, supply)
        if (row["purchase_allowed"] and row["pickup_allowed"]
                and row["purchase_price_fen"] <= balance):
            return "当前地点有可购买餐食；只能购买本轮已呈现的供给。"
    return "当前地点有餐食供给，但现在没有可购买餐食。"


def local_food_summary(hunger: str, facts: FoodPathFacts) -> str:
    """@brief 场景 `local_food_summary`：饥饿低于「饿」时为原当地餐食句，否则为六档吃饭途径句。"""
    if hunger not in FOOD_PATH_LEVELS:
        return facts.local_sentence
    on_hand = _best_meal(facts.on_hand)
    if on_hand is not None:
        return f"你手边有{on_hand}，可以直接吃。"
    if facts.pending:
        meal = min(facts.pending, key=lambda item: (item.expected_at, item.name))
        when = readable_clock(facts.now, meal.expected_at, facts.timezone)
        sentence = f"你点的{meal.name}预计{when} 送到{meal.place or ''}。"
        if meal.expected_at - facts.now > LATE_DELIVERY_SECONDS:
            sentence += _alternative(facts)
        return sentence
    return _alternative(facts) or _nothing(facts)


def readable_duration(seconds: int) -> str:
    """@brief 人读时长：不到一分钟、约 N 分钟或约 N 小时（不给原始秒数）。"""
    if seconds < 60:
        return "不到 1 分钟"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"约 {minutes} 分钟"
    return f"约 {round(seconds / 3600)} 小时"


def readable_clock(now: int, moment: int, timezone_name: str) -> str:
    """@brief 人读时刻：今天、明天、后天或月日加场景本地 HH:MM。"""
    zone = ZoneInfo(timezone_name)
    today = datetime.fromtimestamp(now, zone).date()
    local = datetime.fromtimestamp(moment, zone)
    days = {today: "今天", today + timedelta(days=1): "明天", today + timedelta(days=2): "后天"}
    return f"{days.get(local.date(), f'{local.month}月{local.day}日')} {local:%H:%M}"


def _best_meal(options: tuple[MealOption, ...]) -> str | None:
    """@brief 热量高者优先、同分按名称取一项食品名称；没有食品时为 None。"""
    if not options:
        return None
    return min(options, key=lambda item: (-item.energy_kcal, item.name)).name


def _alternative(facts: FoodPathFacts) -> str:
    """@brief 第 3、4、5 档中第一条成立的途径句（这里就能买的、能送到这里的、附近能买的），都不成立时为空串。"""
    here = _best_meal(facts.here)
    if here is not None:
        return f"这里就能买到{here}。"
    if facts.deliverable:
        fastest = readable_duration(min(facts.deliverable) - facts.now)
        if len(facts.deliverable) == 1:
            return f"列出的餐食里，有一样能送到这里，{fastest}。"
        return f"列出的餐食里，有几样能送到这里，最快{fastest}。"
    if facts.nearby:
        nearest = min(facts.nearby, key=lambda item: (item.minutes, item.place))
        mode = _MODE_WORDS.get(nearest.mode, nearest.mode)
        return f"最近能买到吃的是{nearest.place}，{mode}{readable_duration(nearest.minutes * 60)}。"
    return ""


def _nothing(facts: FoodPathFacts) -> str:
    """@brief 第 6 档：本轮观察里没有任何吃饭途径；浏览可用时提示可以浏览商品。"""
    if facts.browse_available:
        return "现在不知道附近哪里有吃的，可以浏览商品看看。"
    return "现在不知道附近哪里有吃的。"
