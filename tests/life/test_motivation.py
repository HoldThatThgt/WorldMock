"""@brief 动机评分层：四分量、前景变换、格点、枚举边界与观察注入。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from tests.life.conftest import advance_next
from tests.life.conftest import context as round_context
from tests.life.conftest import submit_round

from worldmock.errors import IntegrityError
from worldmock.life.models import (
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    BodyState,
    ContentProgress,
    StartActivityRequest,
)
from worldmock.life.map_models import SupplyConfig
from worldmock.life.content import load_lifestyle_items
from worldmock.life.retrieval import item_kinds
from worldmock.life.storefront_state import quote_state
from worldmock.life.motivation import (
    ENTERTAINMENT_FAMILY_CAP,
    FAMILIES,
    MAX_CANDIDATES,
    MotivationContext,
    _awake_deficit_delta,
    _eat_candidates,
    classify_action_event,
    evaluate_motivation,
    habit_shares,
    load_motivation_transforms,
    meal_repeat_weight,
    prospect_value,
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TIMEZONE = "Asia/Shanghai"
CURRENT = 1_700_000_000  # 2023-11-14 22:13 +08，夜间商家按 0-24 营业口径开放

#: 钉死的档案合同样例（与画像编译器输出合同一致）。
CONTRACT_PROFILE = {
    "schema_version": 1,
    "big_five": {"openness": 4, "conscientiousness": 6, "extraversion": 3,
                 "agreeableness": 4, "neuroticism": 5},
    "weights": {"money_fen": 0.45, "energy_kcal": 0.15, "sleep_debt_hours": 0.15,
                "entertainment_need": 0.20, "goal_task": 0.05},
    "tau": 1.0,
    "beta_present_bias": 0.80,
    "loss_aversion": {"money_fen": 2.25, "energy_kcal": 1.50},
    "comfort": {"balance_fen": 20000, "energy_kcal": 1200},
    "aspiration": {"daily_effective_work_seconds": 14400},
    "emotion_sensitivity": 0.3,
    # M4：attention 键集 = 注册表 attention_required（娱乐 5 + 生活 9 = 14 键）。
    "attention_prior": {"feature_film": 0.3, "news_feed": 0.2, "joke_feed": 0.1,
                        "webnovel_excerpt": 0.05, "drama_series": 0.05, "podcast_episode": 0.03,
                        "sport": 0.05, "hobby": 0.04, "outdoor": 0.03,
                        "wellness": 0.03, "household": 0.03, "learning": 0.03,
                        "pet": 0.03, "social": 0.03, "volunteer": 0.03},
}


def _profile(**overrides) -> MotivationProfile:
    payload = json.loads(json.dumps(CONTRACT_PROFILE))
    payload.update(overrides)
    return MotivationProfile.model_validate(payload)


@pytest.fixture(scope="module")
def transforms():
    loaded = load_motivation_transforms(PROJECT_ROOT / "catalog" / "rules")
    assert loaded is not None, "real catalog must ship catalog/rules/motivation.toml"
    return loaded


def _body() -> BodyConfig:
    return BodyConfig.model_validate({
        "mass_kg": 65, "initial_energy_kcal": 1600, "maximum_energy_kcal": 2400,
        "initial_sleep_debt_hours": 0,
        "basal_kcal_per_hour": 65, "awake_sleep_debt_per_hour": 0.125,
        "sleep_recovery_per_hour": 0.125, "entertainment_need_per_awake_hour": 0.02,
        "hunger_threshold_kcal": 800, "emergency_threshold_kcal": 400,
        "work_efficiency_factors": [
            {"input_resource": "sleep_debt_hours",
             "curve": [{"input_value": 0, "output_value": 1.0},
                       {"input_value": 16, "output_value": 0.5}]},
            {"input_resource": "energy_kcal",
             "curve": [{"input_value": 0, "output_value": 0.5},
                       {"input_value": 400, "output_value": 0.8},
                       {"input_value": 800, "output_value": 1.0}]}],
        "food_efficiency_curve": [{"input_value": 0, "output_value": 1.0},
                                  {"input_value": 16, "output_value": 0.5}],
        "activity_met": {"wait": 1.0, "sleep": 1.0, "work": 1.5, "watch": 1.2,
                         "play_game": 1.3, "travel:driving": 1.5, "travel:walking": 2.5,
                         "read_mail": 1.1, "browse": 1.2, "consume": 1.3},
        "entertainment_relief_per_hour": {"watch": 0.15, "play_game": 0.2},
        "work_notes_multiplier": 1.2, "source_kind": "test"})


def _agent(energy=1600.0, sleep_debt=4.0, need=0.5, location="home",
           inventory=None, preferences=None, skills=None) -> AgentState:
    return AgentState(agent_id=1, name="Lin", location_id=location,
                      balance_account_id="agent:1", next_decision_at=0, body_settled_at=0,
                      body=BodyState(energy_kcal=energy, sleep_pressure=0.0, sleep_deficit_hours=sleep_debt,
                                     entertainment_need=need),
                      skill_levels=skills if skills is not None else {"ami": 0.8},
                      taste_weights=preferences if preferences is not None
                      else {"movie-0001": 0.8, "news-1": 0.6},
                      inventory=inventory or {})


SOURCE = {
    "kind": "scenario", "url": "https://example.com/test-fixture", "retrieved_at": "2023-11-01T00:00:00+08:00",
    "license": "test", "notice": "Synthetic test declarations", "fact_cutoff": "2023-11-01",
    "applicability": "Synthetic motivation test only",
}
DECLARATION = {
    "effective_from": "2023-11-01T00:00:00+08:00", "effective_to": None,
    "reviewed_on": "2023-11-01", "still_valid_on": "2023-11-01", "source": SOURCE,
}


@dataclass(frozen=True)
class FoodOffer:
    """独立商品与具体门店供给组成的显式测试样例。"""
    product: ProductConfig
    supply: SupplyConfig


def _food_offer(product_id="food-1", price=800, kcal=600, location="market", duration=900, stock=10):
    product = ProductConfig.model_validate({
        "product_id": product_id, "name": "Test meal", "category_id": "food", "serving_quantity": 1.0, "serving_unit": "serving",
        "energy_kcal_per_unit": kcal, "use_duration_seconds": duration,
        "use_modes": ["eat"], "consumable": True, "effect": "fed", "source_kind": "test"})
    supply = SupplyConfig.model_validate({
        "supply_id": f"supply-{product_id}", "location_id": location, "product_id": product_id,
        "seller_account_id": "merchant:g", "quotes": [{**DECLARATION, "price_version": "v1", "price_fen": price}],
        "initial_stock": stock, "fulfillment_modes": ["pickup", "delivery"],
        "delivery_destination_ids": ["home", "office"], "delivery_delay_seconds": 600,
        "required_qualifications": [], "source": SOURCE})
    return FoodOffer(product, supply)


def _route(route_id, origin, destination, mode="driving", seconds=600, fare=600) -> RouteConfig:
    return RouteConfig.model_validate({
        "route_id": route_id, "origin_id": origin, "destination_id": destination, "status": "reachable",
        "mode": mode, "distance_meters": 3000, "duration_seconds": seconds,
        "fare_fen": fare, "fare_source_kind": "scenario", "response_path": "routes/x.json",
        "response_sha256": "a" * 64, "source_url": "https://example.com/route",
        "routing_instance": "https://example.com", "routing_profile": "foot" if mode == "walking" else "car",
        "source": SOURCE})


def _task(task_id="task-1", pay=50000, location="office", required=3600.0) -> WorkTaskConfig:
    zone = ZoneInfo(TIMEZONE)
    return WorkTaskConfig.model_validate({
        "task_id": task_id, "title": "测试工作合同", "assignee_agent_id": "agent:1", "contract_kind": "package",
        "available_at": datetime(2023, 11, 13, tzinfo=zone),
        "due_at": datetime(2023, 11, 16, tzinfo=zone),
        "maximum_duration_seconds": 14400, "pay_fen": pay,
        "employer_account_id": "employer:1", "required_location_id": location,
        "required_skill": "ami", "minimum_skill": 0.3, "required_effective_seconds": required,
        "input_path": "work/in.txt", "input_sha256": "b" * 64,
        "oracle_path": "work/oracle.json", "oracle_sha256": "c" * 64})


def _content(item_id="movie-0001", kind="feature_film", seconds=3600, relief=0.2,
             ) -> ContentItemConfig:
    return ContentItemConfig.model_validate({
        "item_id": item_id, "kind": kind, "duration_seconds": seconds,
        "timeline_path": "x.srt", "timeline_sha256": "d" * 64,
        "entertainment_relief_per_hour": relief, "experience_label": "Movie"})


def _location(location_id="market", open_hour=0, close_hour=24) -> LocationConfig:
    return LocationConfig.model_validate({
        "location_id": location_id, "name": location_id, "kind": "shop", "business_kind": "grocery",
        "longitude": 121.4, "latitude": 31.2, "location_kinds": ["venue"], "required_qualifications": [],
        "brand": None, "affordances": {}, "source": SOURCE,
        "opening_calendars": [{**DECLARATION, "exceptions": {}, "weekly": {
            str(day): [{"opens": open_hour * 60, "closes": close_hour * 60}] for day in range(7)}}]})


@lru_cache(maxsize=1)
def _lifestyle_fixture():
    payload = json.loads((PROJECT_ROOT / "corpora/lifestyle/items.json").read_text(encoding="utf-8"))
    met = {f"lifestyle:{row['activity_id']}": row["met_value"] for row in payload["items"]}
    return load_lifestyle_items(PROJECT_ROOT / "corpora", met)


def _context(**overrides) -> MotivationContext:
    food_offers = overrides.pop("food_offers", ())
    catalog_food_offers = overrides.pop("catalog_food_offers", ())
    defaults = dict(
        agent=_agent(), balance_fen=20_000, body=_body(), home_location_id="home",
        lifestyle_items=_lifestyle_fixture(), activity_location_kinds=item_kinds(PROJECT_ROOT / "corpora"),
        purchase_pairing_states={item.product.product_id: "defined" for item in food_offers},
        products=tuple(item.product for item in food_offers), supplies=tuple(item.supply for item in food_offers),
        catalog_supplies=tuple(item.supply for item in catalog_food_offers),
        supply_stock={item.supply.supply_id: item.supply.initial_stock for item in food_offers},
        supply_quotes={item.supply.supply_id: quote_state(item.supply.quotes[0]) for item in food_offers},
        opening_exceptions={key: {} for key in overrides.get("locations", {})}, locations={"home": _location("home").model_copy(
            update={"location_kinds": ("home", "office", "outdoor", "fitness", "venue")})}, routes=(), tasks=(),
        task_totals={}, contents=(), content_progress={}, active_activities=(),
        habit_events=(), consecutive_rejections=0, max_same_moment_decisions=3,
        total_effective_seconds=0.0, current_time=CURRENT,
        end_time=CURRENT + 86400, timezone=TIMEZONE,
        planned_travel_seconds=lambda mode, base, departure: base,
        purchase_amount=lambda product, price, quantity: price * quantity)
    defaults.update(overrides)
    return MotivationContext(**defaults)


# ---------------------------------------------------------------------------
# 前景变换与格点
# ---------------------------------------------------------------------------

class TestProspectValue:
    def test_zero_and_monotone_grid(self, transforms):
        for lam in (1.0, 2.25):
            previous = None
            for steps in range(-12, 13):
                z = steps * 0.25
                value = prospect_value(z, transforms, lam)
                if z == 0:
                    assert value == 0.0
                if previous is not None:
                    assert value > previous, f"value must increase in z at {z}"
                previous = value

    def test_gain_concavity(self, transforms):
        for x in (0.25, 0.5, 1.0, 2.0, 4.0):
            assert prospect_value(2 * x, transforms, 1.0) < 2 * prospect_value(x, transforms, 1.0)

    def test_loss_aversion_slope(self, transforms):
        lam = 2.25
        for x in (0.25, 0.5, 1.0, 2.0):
            assert abs(prospect_value(-x, transforms, lam)) == pytest.approx(
                lam * prospect_value(x, transforms, 1.0))

    def test_diminishing_relief_away_from_reference_grid(self, transforms):
        """@brief TK 敏感度递减：同量恢复在深亏缺段的效用增益更小（格点递减）。"""
        gain, scale, lam = 300.0, transforms.energy_scale_kcal, 1.5
        previous = None
        for deficit in range(300, 1400, 100):
            relief = lam * ((deficit / scale) ** transforms.satiation_exponent
                            - ((deficit - gain) / scale) ** transforms.satiation_exponent)
            if previous is not None:
                assert relief < previous, "relief must shrink as deficit deepens"
            previous = relief


# ---------------------------------------------------------------------------
# 四分量正反例
# ---------------------------------------------------------------------------

class TestHomeostatic:
    def test_hungry_eat_scores_positive_energy_term(self, transforms):
        # 应急能量 + 廉价高热量食物 + 余额充裕（平缓损失段）→ 内稳态净收益为正。
        context = _context(agent=_agent(energy=100.0), balance_fen=50_000,
                           food_offers=(_food_offer(price=200, kcal=800),),
                           supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),))
        scored = evaluate_motivation(context, _profile(), transforms)
        eat = next(item for item in scored if item.candidate.family == "eat")
        assert eat.components["homeostatic"] > 0
        assert "restores energy 100->" in eat.candidate.reason

    def test_money_at_comfort_keeps_price_out_of_homeostatic(self, transforms):
        # docs/dev/SPEC-life-realism.md「动机层」：价格只在成本项计一次——购餐与持餐候选的 homeostatic
        # 完全一致（钱坐标不含价格流），价格损失全量落在成本项。
        base = dict(food_offers=(_food_offer(price=800),), supply_stock={"supply-food-1": 5},
                    locations={"market": _location()})
        held = evaluate_motivation(
            _context(agent=_agent(energy=455.0, location="market",
                                  inventory={"market": {"food-1": 2}}), **base),
            _profile(), transforms)
        buy = evaluate_motivation(
            _context(agent=_agent(energy=455.0, location="market"), **base),
            _profile(), transforms)
        held_eat = next(item for item in held
                        if item.candidate.summary.startswith("Eat held"))
        buy_eat = next(item for item in buy if item.candidate.family == "eat")
        assert held_eat.components["homeostatic"] == pytest.approx(
            buy_eat.components["homeostatic"])
        hours = buy_eat.candidate.duration_seconds / 3600
        assert buy_eat.components["cost"] - held_eat.components["cost"] == pytest.approx(
            800 / transforms.money_scale_fen / hours, rel=1e-6)

    def test_reference_recomputation_matches_component(self, transforms):
        """@brief 独立重算公式逐项对上 homeostatic 分量（测试内另写一遍公式）。"""
        profile = _profile()
        agent = _agent(energy=900.0)
        context = _context(agent=agent, food_offers=(_food_offer(),), supply_stock={"supply-food-1": 5},
                           locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),))
        scored = evaluate_motivation(context, profile, transforms)
        candidate = next(item for item in scored
                         if item.candidate.family == "eat").candidate
        # docs/dev/SPEC-life-realism.md「动机层」：价格只进成本项——内稳态钱坐标只含折现的延期钱流。
        money_after = context.balance_fen
        energy_after = agent.body.energy_kcal + candidate.energy_delta
        deficit_after = agent.body.sleep_deficit_hours + candidate.sleep_deficit_delta
        fun_after = agent.body.entertainment_need + candidate.entertainment_delta

        def term(weight, resource, now, after, lam):
            return weight * (prospect_value(after, transforms, lam)
                             - prospect_value(now, transforms, lam))

        expected = (
            term(profile.weights["money_fen"], "money_fen",
                 (context.balance_fen - profile.comfort["balance_fen"]) / transforms.money_scale_fen,
                 (money_after - profile.comfort["balance_fen"]) / transforms.money_scale_fen, 2.25)
            + term(profile.weights["energy_kcal"], "energy_kcal",
                   (agent.body.energy_kcal - profile.comfort["energy_kcal"]) / transforms.energy_scale_kcal,
                   (energy_after - profile.comfort["energy_kcal"]) / transforms.energy_scale_kcal, 1.5)
            + term(profile.weights["sleep_debt_hours"], "sleep_debt_hours",
                   -agent.body.sleep_deficit_hours / transforms.sleep_scale_hours,
                   -deficit_after / transforms.sleep_scale_hours, 1.0)
            + term(profile.weights["entertainment_need"], "entertainment_need",
                   -agent.body.entertainment_need / transforms.entertainment_scale,
                   -fun_after / transforms.entertainment_scale, 1.0)
            + term(profile.weights["goal_task"], "goal_task", -1.0, -1.0, 1.0))
        actual = next(item for item in scored
                      if item.candidate.family == "eat").components["homeostatic"]
        assert actual == pytest.approx(expected, rel=1e-9)

    def test_sated_agent_gain_capped(self, transforms):
        near_max = _context(agent=_agent(energy=2300.0), food_offers=(_food_offer(),),
                            supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                            routes=(_route("r1", "market", "home"),))
        scored = evaluate_motivation(near_max, _profile(), transforms)
        eat = next(item for item in scored if item.candidate.family == "eat")
        assert eat.candidate.energy_delta < 100  # 2400 上限截断 + 进食自身代谢


def _start(*pairs: tuple[str, str]) -> tuple[str, dict]:
    """@brief 一条 start_activity 请求足迹：逐项的动词与方式（对象名不参与习惯归并）。"""
    return ("start_activity", {"targets": [f"对象{index}" for index, _ in enumerate(pairs)],
                               "actions": [verb for verb, _ in pairs], "ways": [way for _, way in pairs]})


class TestHabit:
    def test_classification_table(self):
        """start_activity 逐项按方式闭集（WAYS）映射的内部动作归并，不按动词；其余工具取工具名本身。"""
        assert classify_action_event("report_status", {}) == ("continue",)
        table = {("sleep", "睡觉"): ("sleep",), ("interact", "等待"): ("continue",),
                 ("obtain", "到店"): ("eat",), ("obtain", "远程下单"): ("eat",), ("consume", "食用"): ("eat",),
                 ("travel", "步行"): ("travel",), ("travel", "驾车"): ("travel",),
                 ("interact", "观看"): ("entertainment",), ("interact", "接着看"): ("entertainment",),
                 ("interact", "从头再听"): ("entertainment",), ("interact", "工作"): ("work",),
                 ("interact", "浏览"): ("browse",)}
        for pair, families in table.items():
            assert classify_action_event(*_start(pair)) == families, pair
        assert classify_action_event("submit_work", {}) == ("work",)
        assert classify_action_event(*_start(("travel", "步行"), ("interact", "接着听"))) == (
            "travel", "entertainment")
        # 只有动词、旧短语或不在闭集的方式都不可判定，不计数。
        assert classify_action_event("start_activity", {"actions": ["sleep"]}) == ()
        assert classify_action_event(*_start(("travel", "步行前往"))) == ()
        assert classify_action_event(*_start(("interact", "到店购买"))) == ()

    def test_shares_normalize_and_recency(self):
        events = [_start(("interact", "接着看"))] * 3
        shares = habit_shares(events, 0.9)
        assert set(shares) == FAMILIES
        assert shares["entertainment"] == pytest.approx(1.0)
        assert sum(shares.values()) == pytest.approx(1.0)
        mixed = [_start(("obtain", "到店")), _start(("interact", "接着看")), _start(("interact", "从头再看"))]
        shares = habit_shares(mixed, 0.5)
        # 逆序贴现：最新 watch=1.0、次新 watch=0.5、最早 purchase=0.25。
        assert shares["eat"] == pytest.approx(0.25 / 1.75)
        assert shares["entertainment"] == pytest.approx(1.5 / 1.75)
        concurrent = habit_shares([_start(("travel", "步行"), ("interact", "接着听"))], 0.5)
        assert concurrent["travel"] == concurrent["entertainment"] == pytest.approx(0.5)

    def test_empty_footprint_gives_zero_habit(self, transforms):
        context = _context(agent=_agent(energy=455.0), food_offers=(_food_offer(),),
                           supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),))
        scored = evaluate_motivation(context, _profile(), transforms)
        assert all(item.components["habit"] == 0.0 for item in scored)

    def test_habit_lifts_frequent_family(self, transforms):
        base = dict(agent=_agent(energy=455.0), food_offers=(_food_offer(),),
                    supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                    routes=(_route("r1", "market", "home"),))
        cold = evaluate_motivation(_context(**base), _profile(), transforms)
        warm = evaluate_motivation(
            _context(habit_events=[("purchase", {})] * 5, **base), _profile(), transforms)
        cold_eat = next(item for item in cold if item.candidate.family == "eat")
        warm_eat = next(item for item in warm if item.candidate.family == "eat")
        assert warm_eat.components["habit"] > cold_eat.components["habit"]


class TestEmotion:
    def test_zero_state_gives_zero_emotion(self, transforms):
        context = _context(agent=_agent(energy=455.0), food_offers=(_food_offer(),),
                           supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),),
                           total_effective_seconds=14_400.0)
        scored = evaluate_motivation(context, _profile(), transforms)
        assert all(item.components["emotion"] == 0.0 for item in scored)

    def test_frustration_lifts_comfort_candidates(self, transforms):
        base = dict(agent=_agent(energy=455.0), food_offers=(_food_offer(),),
                    supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                    routes=(_route("r1", "market", "home"),))
        calm = evaluate_motivation(_context(**base), _profile(), transforms)
        frustrated = evaluate_motivation(
            _context(consecutive_rejections=3, **base), _profile(), transforms)
        calm_sleep = next(item for item in calm if item.candidate.family == "sleep")
        frustrated_sleep = next(item for item in frustrated if item.candidate.family == "sleep")
        assert calm_sleep.components["emotion"] == 0.0
        assert frustrated_sleep.components["emotion"] == pytest.approx(0.3 * 1.0)
        # 时间与能量规格第一章「实现约定」：受挫度分母为同刻上限，连续拒绝超过上限时截到 1。
        capped = evaluate_motivation(
            _context(consecutive_rejections=7, **base), _profile(), transforms)
        capped_sleep = next(item for item in capped if item.candidate.family == "sleep")
        assert capped_sleep.components["emotion"] == pytest.approx(frustrated_sleep.components["emotion"])

    def test_goal_deficit_lifts_work_candidates(self, transforms):
        context = _context(agent=_agent(location="office"), tasks=(_task(),),
                           task_totals={"task-1": (0, 0.0)}, total_effective_seconds=0.0)
        scored = evaluate_motivation(context, _profile(), transforms)
        work = next(item for item in scored if item.candidate.family == "work")
        assert work.components["emotion"] == pytest.approx(0.3 * 1.0)
        assert "yields about" in work.candidate.reason


class TestCost:
    def test_held_food_cost_excludes_price(self, transforms):
        held = _context(agent=_agent(energy=455.0, location="market",
                                     inventory={"market": {"food-1": 2}}),
                        food_offers=(_food_offer(price=800),), supply_stock={"supply-food-1": 5},
                        locations={"market": _location()})
        scored = evaluate_motivation(held, _profile(), transforms)
        eat = next(item for item in scored if item.candidate.family == "eat")
        assert eat.candidate.summary.startswith("Eat held food-1")
        assert eat.candidate.money_now_fen == 0.0

    def test_price_enters_cost(self, transforms):
        base = dict(food_offers=(_food_offer(price=800),), supply_stock={"supply-food-1": 5},
                    locations={"market": _location()})
        held = evaluate_motivation(
            _context(agent=_agent(energy=455.0, location="market",
                                  inventory={"market": {"food-1": 2}}), **base),
            _profile(), transforms)
        buy = evaluate_motivation(
            _context(agent=_agent(energy=455.0, location="market"), **base),
            _profile(), transforms)
        held_eat = next(item for item in held if item.candidate.family == "eat")
        buy_eat = next(item for item in buy if item.candidate.family == "eat")
        hours = buy_eat.candidate.duration_seconds / 3600
        assert buy_eat.components["cost"] - held_eat.components["cost"] == pytest.approx(
            800 / transforms.money_scale_fen / hours, rel=1e-6)

    def test_travel_cost_includes_fare(self, transforms):
        context = _context(agent=_agent(location="office"),
                           routes=(_route("r1", "office", "home", fare=600),))
        scored = evaluate_motivation(context, _profile(), transforms)
        travel = next(item for item in scored if item.candidate.family == "travel")
        hours = travel.candidate.duration_seconds / 3600
        # docs/dev/SPEC-life-realism.md「动机层」：价格单计 + 时间机会成本，整体按候选时长归一到单位小时口径。
        expected = (600 + hours * transforms.time_value_fen_per_hour) \
            / transforms.money_scale_fen / hours
        assert travel.components["cost"] == pytest.approx(expected, rel=1e-6)

    def test_cost_normalized_per_hour_across_durations(self, transforms):
        """@brief docs/dev/SPEC-life-realism.md「动机层」单位小时效用：10 分钟与 120 分钟零价格候选的时间成本可比。"""
        short = _context(agent=_agent(need=0.5), contents=(_content("movie-a",
                                                                    seconds=600),),
                         taste_weights={"movie-a": 0.8})
        long = _context(agent=_agent(need=0.5), contents=(_content("movie-b",
                                                                   seconds=7200),),
                        taste_weights={"movie-b": 0.8})
        short_eat = next(item for item in evaluate_motivation(short, _profile(), transforms,
                                                              top_k=10)
                         if item.candidate.family == "entertainment")
        long_eat = next(item for item in evaluate_motivation(long, _profile(), transforms,
                                                             top_k=10)
                        if item.candidate.family == "entertainment")
        # 无价格、无延迟：单位小时时间成本 = time_value / money_scale，与时长无关。
        expected = transforms.time_value_fen_per_hour / transforms.money_scale_fen
        assert short_eat.components["cost"] == pytest.approx(expected, rel=1e-6)
        assert long_eat.components["cost"] == pytest.approx(expected, rel=1e-6)
        assert short_eat.candidate.duration_seconds * 12 == \
            pytest.approx(long_eat.candidate.duration_seconds)


class TestSoftmaxAndShape:
    def test_ranked_topk_with_normalized_probabilities(self, transforms):
        context = _context(agent=_agent(energy=455.0), food_offers=(_food_offer(),),
                           supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),),
                           contents=(_content(), _content("news-1", "news_feed", 600, 0.1),
                                     _content("drama-1", "drama_series", 1800, 0.15)),
                           tasks=(_task(),), task_totals={"task-1": (0, 0.0)})
        scored = evaluate_motivation(context, _profile(), transforms)
        assert 0 < len(scored) <= 5
        scores = [item.score for item in scored]
        assert scores == sorted(scores, reverse=True)
        assert sum(item.softmax_probability for item in scored) == pytest.approx(
            sum(item.softmax_probability for item in scored))
        for item in scored:
            hint = item.to_hint()
            assert set(hint["tradeoff"]) == {"homeostatic", "habit", "emotion", "cost"}
            assert isinstance(hint["reason_text"], str) and hint["reason_text"]
            assert hint["softmax_probability"] >= 0
            assert "score" not in hint  # 相对分呈现：不暴露绝对总分
        assert [item.rank for item in scored] == list(range(1, len(scored) + 1))

    def test_enumeration_capped_at_nine(self, transforms):
        contents = tuple(_content(f"movie-{index:04d}", "feature_film", 3600, 0.2)
                         for index in range(1, 11))
        tasks = tuple(_task(f"task-{index}", pay=50_000 - index) for index in range(1, 5))
        activity = ActivityState(
            activity_id="activity-w1", agent_id=1, location_id="office", kind=ActivityKind.WATCH,
            status=ActivityStatus.ACTIVE, started_at=CURRENT - 600,
            planned_end_at=CURRENT + 1800, last_settled_at=CURRENT - 600, elapsed_seconds=600,
            planned_duration_seconds=2400, target_id="movie-0001", origin_action_id="a",
            origin_decision_id="d", origin_request_event_id="e",
            data={"entertainment_relief_per_hour": 0.2, "exposure_count": 1})
        context = _context(agent=_agent(energy=1500.0, location="office"),
                           food_offers=(_food_offer(),), supply_stock={"supply-food-1": 5},
                           catalog_food_offers=(_food_offer(), _food_offer("food-2", price=600)),
                           locations={"market": _location()},
                           routes=(_route("rd", "market", "office"),
                                   _route("rh", "office", "home")),
                           contents=contents, tasks=tasks, active_activities=(activity,),
                           taste_weights={item.item_id: 0.8 for item in contents},
                           task_totals={item.task_id: (0, 0.0) for item in tasks})
        scored = evaluate_motivation(context, _profile(), transforms, top_k=20)
        assert len(scored) <= MAX_CANDIDATES
        families = {item.candidate.family for item in scored}
        assert {"eat", "sleep", "continue", "work", "browse", "entertainment"} <= families


# ---------------------------------------------------------------------------
# 候选枚举边界
# ---------------------------------------------------------------------------

class TestEnumerationBoundaries:
    def test_no_tasks_no_work_candidates(self, transforms):
        context = _context(agent=_agent(location="office"))
        scored = evaluate_motivation(context, _profile(), transforms)
        assert all(item.candidate.family != "work" for item in scored)

    def test_no_money_and_no_hold_excludes_eat(self, transforms):
        context = _context(agent=_agent(energy=455.0), balance_fen=0,
                           food_offers=(_food_offer(price=800),), supply_stock={"supply-food-1": 5},
                           locations={"market": _location()})
        scored = evaluate_motivation(context, _profile(), transforms)
        assert all(item.candidate.family != "eat" for item in scored)

    def test_zero_energy_keeps_only_recovery_candidates(self, transforms):
        context = _context(agent=_agent(energy=1.0), food_offers=(_food_offer(),),
                           supply_stock={"supply-food-1": 5}, locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),),
                           contents=(_content(),), tasks=(_task(),),
                           task_totals={"task-1": (0, 0.0)})
        scored = evaluate_motivation(context, _profile(), transforms)
        families = {item.candidate.family for item in scored}
        assert "sleep" in families  # 恢复类：能量近零仍可睡眠
        assert "entertainment" not in families  # 观看需能量预算，槽长为 0 被裁
        assert "work" not in families

    def test_continue_candidate_uses_remaining_progress(self, transforms):
        activity = ActivityState(
            activity_id="activity-a1", agent_id=1, location_id="home", kind=ActivityKind.WATCH,
            status=ActivityStatus.ACTIVE, started_at=CURRENT - 600,
            planned_end_at=CURRENT + 1200, last_settled_at=CURRENT - 600, elapsed_seconds=600,
            planned_duration_seconds=1800, target_id="movie-0001", origin_action_id="a",
            origin_decision_id="d", origin_request_event_id="e",
            data={"entertainment_relief_per_hour": 0.2, "exposure_count": 1})
        context = _context(active_activities=(activity,), contents=(_content(),),
                           taste_weights={"movie-0001": 0.8})
        scored = evaluate_motivation(context, _profile(), transforms)
        keep = next(item for item in scored if item.candidate.family == "continue")
        assert keep.candidate.duration_seconds == 1200
        assert keep.candidate.entertainment_delta < 0

    def test_continue_wait_reports_only_elapsed_awake_time(self, transforms):
        """等待不预测任何未实际播放内容产生的娱乐缓解。"""
        activity = ActivityState(
            activity_id="activity-w1", agent_id=1, location_id="home", kind=ActivityKind.WAIT,
            status=ActivityStatus.ACTIVE, started_at=CURRENT - 600,
            planned_end_at=CURRENT + 1200, last_settled_at=CURRENT - 600, elapsed_seconds=600,
            planned_duration_seconds=1800, origin_action_id="a",
            origin_decision_id="d", origin_request_event_id="e")
        body = _body().model_copy(update={"entertainment_relief_per_hour": {
            **_body().entertainment_relief_per_hour, "wait": 0.01}})
        context = _context(active_activities=(activity,), body=body)
        scored = evaluate_motivation(context, _profile(), transforms)
        keep = next(item for item in scored if item.candidate.family == "continue")

        assert keep.candidate.summary == "Keep waiting for 20 min"
        assert keep.candidate.reason == "Waiting for 20 min advances awake time without content exposure"
        # 即使旧实验费率表含 wait 项，纯等待仍只累计清醒需要。
        accrual = body.entertainment_need_per_awake_hour * 1200 / 3600
        assert keep.candidate.entertainment_delta == pytest.approx(accrual)


class TestEntertainmentSegmentSlots:
    """@brief 娱乐候选的分段时长槽：长内容以约两段典型时长入场，不再整块承诺。"""

    NOVEL_SECONDS = 84_780
    NOVEL_SEGMENTS = 169

    def _novel_boundaries(self) -> tuple[float, ...]:
        return tuple(self.NOVEL_SECONDS / self.NOVEL_SEGMENTS * (index + 1)
                     for index in range(self.NOVEL_SEGMENTS))

    def _entertainment(self, context, transforms):
        scored = evaluate_motivation(context, _profile(), transforms, top_k=20)
        return next(item for item in scored if item.candidate.family == "entertainment")

    def test_unstarted_novel_offers_two_chapters_not_whole_book(self, transforms):
        typical = self.NOVEL_SECONDS / self.NOVEL_SEGMENTS
        context = _context(
            agent=_agent(preferences={"novel-0001": 0.8}),
            contents=(_content("novel-0001", "webnovel_excerpt", self.NOVEL_SECONDS, 0.35),),
            content_segments={"novel-0001": self._novel_boundaries()},
            taste_weights={"novel-0001": 0.8})
        keep = self._entertainment(context, transforms)
        # 一次约两章（~17 分钟）而不是整本 24 小时的整块承诺
        assert keep.candidate.duration_seconds == pytest.approx(typical * 2)
        assert keep.candidate.summary == f"Read chapter 1 of novel-0001 (~{typical * 2 / 60:.0f} min)"
        assert keep.candidate.reason.startswith("Reading one chapter of novel-0001")

    def test_started_novel_continues_at_next_chapter(self, transforms):
        typical = self.NOVEL_SECONDS / self.NOVEL_SEGMENTS
        context = _context(
            agent=_agent(preferences={"novel-0001": 0.8}),
            contents=(_content("novel-0001", "webnovel_excerpt", self.NOVEL_SECONDS, 0.35),),
            content_segments={"novel-0001": self._novel_boundaries()},
            taste_weights={"novel-0001": 0.8},
            content_progress={"novel-0001": ContentProgress(
                item_id="novel-0001", position_seconds=typical * 5, exposure_count=1)})
        keep = self._entertainment(context, transforms)
        # 已读 5 章后从第 6 章继续，槽长仍是约两章而非剩余 91 章整块
        assert keep.candidate.summary == f"Read chapter 6 of novel-0001 (~{typical * 2 / 60:.0f} min)"
        assert "Continuing novel-0001 at chapter 6/169" in keep.candidate.reason
        assert keep.candidate.duration_seconds == pytest.approx(typical * 2)

    def test_started_movie_proposes_resume_not_restart(self, transforms):
        context = _context(
            agent=_agent(preferences={"movie-0001": 0.8}),
            contents=(_content("movie-0001", "feature_film", 6780, 0.35),),
            taste_weights={"movie-0001": 0.8},
            content_progress={"movie-0001": ContentProgress(
                item_id="movie-0001", position_seconds=1800, exposure_count=1)})
        keep = self._entertainment(context, transforms)
        # 看到第 30 分钟的电影显示"从第 30 分钟继续"，槽长为剩余 83 分钟
        assert keep.candidate.summary == "Continue movie-0001 from minute 30 (~83 min)"
        assert keep.candidate.duration_seconds == pytest.approx(6780 - 1800)
        assert keep.candidate.reason.startswith("Continuing movie-0001 from minute 30")

    def test_unsegmented_movie_still_offers_full_watch(self, transforms):
        context = _context(
            agent=_agent(preferences={"movie-0001": 0.8}),
            contents=(_content("movie-0001", "feature_film", 6780, 0.35),),
            taste_weights={"movie-0001": 0.8})
        keep = self._entertainment(context, transforms)
        # 电影单 cue：整段观看不受分段槽影响
        assert keep.candidate.summary == "Watch movie-0001 (~113 min)"
        assert keep.candidate.duration_seconds == pytest.approx(6780)

    def test_short_segment_feed_floors_at_ten_minute_session(self, transforms):
        boundaries = tuple(38_032 / 1000 * (index + 1) for index in range(1000))
        context = _context(
            agent=_agent(preferences={"news-feed": 0.6}),
            contents=(_content("news-feed", "news_feed", 38_032, 0.25),),
            content_segments={"news-feed": boundaries},
            taste_weights={"news-feed": 0.6})
        keep = self._entertainment(context, transforms)
        # 两段典型时长只有 ~1 分钟：保底一次 10 分钟会话，避免候选被裁光
        assert keep.candidate.duration_seconds == pytest.approx(600.0)
        assert keep.candidate.summary == "Watch segment 1 of news-feed (~10 min)"

    def test_resume_keeps_exposures_and_restart_adds_one(self, transforms):
        """缓解按接触次数摊薄（第七章「三层结构」）：接着看不算新的一次接触，按已有次数摊薄；第一次观看与从头再看
        （已看完或剩余不足 60 秒，第八章「呈现」）各算一次新的接触。"""
        movie = _content("movie-0001", "feature_film", 6780, 0.35)

        def watch(position: float | None):
            progress = {} if position is None else {"movie-0001": ContentProgress(
                item_id="movie-0001", position_seconds=position, exposure_count=2)}
            context = _context(agent=_agent(need=1.0, preferences={"movie-0001": 0.8}), contents=(movie,),
                               taste_weights={"movie-0001": 0.8}, content_progress=progress)
            candidate = self._entertainment(context, transforms).candidate
            return candidate, -candidate.entertainment_delta * 3600 / candidate.duration_seconds

        first, first_rate = watch(None)
        resumed, resumed_rate = watch(1800)
        finished, finished_rate = watch(6780)
        tail, tail_rate = watch(6780 - 30)
        assert first_rate == pytest.approx(0.35 * 0.8)
        assert resumed_rate == pytest.approx(0.35 * 0.8 / 2)
        assert resumed.summary == "Continue movie-0001 from minute 30 (~83 min)"
        assert finished_rate == pytest.approx(0.35 * 0.8 / 3) and tail_rate == pytest.approx(0.35 * 0.8 / 3)
        assert first.summary == finished.summary == tail.summary == "Watch movie-0001 (~113 min)"

    def test_finished_novel_restarts_from_chapter_one(self, transforms):
        typical = self.NOVEL_SECONDS / self.NOVEL_SEGMENTS
        context = _context(
            agent=_agent(preferences={"novel-0001": 0.8}),
            contents=(_content("novel-0001", "webnovel_excerpt", self.NOVEL_SECONDS, 0.35),),
            content_segments={"novel-0001": self._novel_boundaries()},
            taste_weights={"novel-0001": 0.8},
            content_progress={"novel-0001": ContentProgress(
                item_id="novel-0001", position_seconds=self.NOVEL_SECONDS, exposure_count=2)})
        keep = self._entertainment(context, transforms)
        # 看完的内容重复观看从头开始，段落索引回到第 1 章
        assert keep.candidate.summary == f"Read chapter 1 of novel-0001 (~{typical * 2 / 60:.0f} min)"
        assert keep.candidate.duration_seconds == pytest.approx(typical * 2)


class TestLifestyleCandidates:
    """@brief M4 lifestyle 激活：开放命名空间的分层菜单候选（每品类 top-1、配额 4）。

    候选目录来自仓库默认语料（183 条、9 品类），场景身份按 body.activity_met
    的 lifestyle:<id> 键求交；排序权 = attention_prior[品类] × 偏好（缺省 0.5）。
    """

    #: 覆盖 5 品类的样例条目（id 取自真实语料；MET 值自拟）。
    SAMPLE_MET = {
        "lifestyle:jogging": 7.0,                 # sport
        "lifestyle:archery": 4.3,                 # sport（同品类竞争者）
        "lifestyle:pet-walk-dog": 3.0,            # pet
        "lifestyle:meditation-improve-focus": 1.0,  # wellness
        "lifestyle:housework-cleaning": 3.3,      # household
        "lifestyle:journaling": 1.3,              # wellness（同品类竞争者）
    }

    def _lifestyle_body(self, **extra_met) -> BodyConfig:
        return _body().model_copy(update={
            "activity_met": dict(_body().activity_met,
                                 **self.SAMPLE_MET, **extra_met)})

    def _lifestyle_only_context(self, **overrides) -> MotivationContext:
        # 注意力压在生活品类上、清空 contents：只观察 lifestyle 家族的枚举。
        profile = _profile(attention_prior={
            "feature_film": 0.0, "news_feed": 0.0, "joke_feed": 0.0, "podcast_episode": 0.0,
            "webnovel_excerpt": 0.0, "drama_series": 0.0,
            "sport": 0.3, "hobby": 0.2, "outdoor": 0.15, "wellness": 0.15,
            "household": 0.1, "learning": 0.05, "pet": 0.03, "social": 0.01,
            "volunteer": 0.01})
        taste = {"jogging": 0.9, "archery": 0.6, "pet-walk-dog": 0.8,
                 "meditation-improve-focus": 0.7, "journaling": 0.4,
                 "housework-cleaning": 0.5}
        defaults = dict(agent=_agent(preferences=taste), body=self._lifestyle_body(),
                        taste_weights=taste)
        defaults.update(overrides)
        context = _context(**defaults)
        return context, profile

    def test_per_kind_top_one_and_pool_cap(self, transforms):
        context, profile = self._lifestyle_only_context()
        scored = evaluate_motivation(context, profile, transforms, top_k=30)
        lifestyle = [item for item in scored if item.candidate.family == "lifestyle"]
        # 每品类 top-1：jogging 胜 archery（0.3×0.9 > 0.3×0.6）、
        # meditation 胜 journaling（0.15×0.7 > 0.15×0.4）；品类 4 个、配额 4。
        ids = {item.candidate.summary for item in lifestyle}
        assert len(lifestyle) == 4
        assert all("lifestyle:" in summary for summary in ids)
        assert any("lifestyle:jogging" in summary for summary in ids)
        assert not any("lifestyle:archery" in summary for summary in ids)
        assert any("lifestyle:meditation-improve-focus" in summary for summary in ids)
        assert not any("lifestyle:journaling" in summary for summary in ids)

    def test_pool_cap_truncates_from_weakest_category(self, transforms):
        # 第 5 个品类（sport 之外再给 outdoor/hobby/social 高权条目）挤不进配额 4。
        taste = {"jogging": 0.9, "archery": 0.6, "pet-walk-dog": 0.8,
                 "meditation-improve-focus": 0.7, "journaling": 0.4,
                 "housework-cleaning": 0.5, "backpacking-trek": 0.9,
                 "3d-printing": 0.9, "board-games": 0.9}
        context, profile = self._lifestyle_only_context(
            agent=_agent(preferences=taste),
            taste_weights=taste,
            body=self._lifestyle_body(**{
                "lifestyle:backpacking-trek": 6.0,   # outdoor
                "lifestyle:3d-printing": 1.8,        # hobby
                "lifestyle:board-games": 1.5}))      # social
        scored = evaluate_motivation(context, profile, transforms, top_k=30)
        lifestyle = [item for item in scored if item.candidate.family == "lifestyle"]
        # 6 个品类各有 top-1，配额 4：household（0.1×0.5）与 social（0.01）出局，
        # 高权 outdoor/hobby/sport/wellness 在场。
        assert len(lifestyle) == 4
        summaries = " | ".join(item.candidate.summary for item in lifestyle)
        assert "lifestyle:backpacking-trek" in summaries
        assert "lifestyle:3d-printing" in summaries
        assert "lifestyle:housework-cleaning" not in summaries

    def test_scenario_met_table_scopes_identity(self, transforms):
        # archery 不在场景 MET 表 → 即使注意与偏好全高也不枚举。
        met = {key: value for key, value in self.SAMPLE_MET.items()
               if key != "lifestyle:archery"}
        body = _body().model_copy(update={"activity_met": dict(_body().activity_met, **met)})
        context, profile = self._lifestyle_only_context(body=body)
        scored = evaluate_motivation(context, profile, transforms, top_k=30)
        summaries = " | ".join(item.candidate.summary for item in scored
                               if item.candidate.family == "lifestyle")
        assert "lifestyle:archery" not in summaries
        assert "lifestyle:jogging" in summaries

    def test_energy_infeasible_item_skipped(self, transforms):
        # 低能量 + 高 MET：能量可行上限把时长槽压到 60 秒以下 → 该条目出局；
        # 低 MET 条目（冥想）不受影响。时长槽先收缩、收缩后仍 <60s 才跳过。
        met = dict(self.SAMPLE_MET)
        met["lifestyle:jogging"] = 60.0  # (65 + 59*65) kcal/h：50 kcal 只够 ~46s
        body = _body().model_copy(update={"activity_met": dict(_body().activity_met, **met)})
        context, profile = self._lifestyle_only_context(
            agent=_agent(energy=50.0, preferences={
                "jogging": 0.9, "archery": 0.6, "pet-walk-dog": 0.8,
                "meditation-improve-focus": 0.7, "journaling": 0.4,
                "housework-cleaning": 0.5}), body=body)
        scored = evaluate_motivation(context, profile, transforms, top_k=30)
        summaries = " | ".join(item.candidate.summary for item in scored
                               if item.candidate.family == "lifestyle")
        assert "lifestyle:jogging" not in summaries
        assert "lifestyle:meditation-improve-focus" in summaries

    def test_lifestyle_events_accumulate_own_habit_share(self, transforms):
        # 生活活动通过人物可读动作进入独立习惯家族。
        events = (_start(("interact", "进行")),)
        shares = habit_shares(events, transforms.habit_discount)
        assert shares.get("lifestyle") == pytest.approx(1.0)
        assert shares.get("continue", 0.0) == 0.0


# ---------------------------------------------------------------------------
# docs/dev/SPEC-life-realism.md「动机层」评分器修正
# ---------------------------------------------------------------------------

class TestScorerContract:
    """@brief 修订合同的评分器口径：门槛、配额、降权与标度。"""

    FULL_ATTENTION = {**CONTRACT_PROFILE["attention_prior"]}

    def test_meal_floor_and_top_three(self, transforms):
        # 热量 >=300 的食品进入候选并按缺口接近度取前三；<300 的零食出局。
        foods = (_food_offer("food-big", kcal=800, price=700),
                 _food_offer("food-mid", kcal=600, price=500),
                 _food_offer("food-ok", kcal=400, price=300),
                 _food_offer("food-snack", kcal=120, price=100),
                 _food_offer("food-tiny", kcal=90, price=80))
        context = _context(agent=_agent(energy=1000.0, location="market"),
                           food_offers=foods,
                           supply_stock={item.supply.supply_id: 5 for item in foods},
                           locations={"market": _location()})
        menu = [item.summary for item in _eat_candidates(context)]
        # 缺口 = 2400-1000 = 1400：|800-gap| < |600-gap| < |400-gap| 定前三名。
        assert menu == ["Buy and eat food-big (supply-food-big)", "Buy and eat food-mid (supply-food-mid)",
                        "Buy and eat food-ok (supply-food-ok)"]
        scored = evaluate_motivation(context, _profile(), transforms, top_k=20)
        eats = [item for item in scored if item.candidate.family == "eat"]
        assert {item.candidate.summary for item in eats} == set(menu)
        assert not any("food-snack" in summary or "food-tiny" in summary
                       for summary in menu)

    def test_sub_floor_foods_fall_back_to_closest_meal(self, transforms):
        # 全部低于 300 kcal 时按"最接近缺口的正餐类食品"兜底取一条。
        foods = (_food_offer("food-a", kcal=120, price=100), _food_offer("food-b", kcal=200, price=150))
        context = _context(agent=_agent(energy=2100.0, location="market"),
                           food_offers=foods,
                           supply_stock={item.supply.supply_id: 5 for item in foods},
                           locations={"market": _location()})
        # 缺口 = 300：200 比 120 更接近缺口（兜底只取一条）。
        assert [item.summary for item in _eat_candidates(context)] == ["Buy and eat food-b (supply-food-b)"]

    def test_same_entertainment_family_capped_at_two(self, transforms):
        # 同一娱乐家族（内容品类）至多两条候选（docs/dev/SPEC-life-realism.md「动机层」）。
        contents = tuple(_content(f"movie-{index:04d}", "feature_film", 3600, 0.2)
                         for index in range(1, 6))
        context = _context(contents=contents,
                           taste_weights={item.item_id: 0.8 for item in contents})
        scored = evaluate_motivation(context, _profile(), transforms, top_k=20)
        entertainment = [item for item in scored
                         if item.candidate.family == "entertainment"]
        assert 0 < len(entertainment) <= ENTERTAINMENT_FAMILY_CAP == 2

    def test_need_below_floor_suppresses_entertainment(self, transforms):
        # 娱乐需要低于 0.3 时不生成娱乐候选；恰在 0.3 时恢复生成（docs/dev/SPEC-life-realism.md「动机层」）。
        contents = (_content(),)
        taste = {"movie-0001": 0.8}
        low = _context(agent=_agent(need=0.29), contents=contents, taste_weights=taste)
        high = _context(agent=_agent(need=0.30), contents=contents, taste_weights=taste)
        low_scored = evaluate_motivation(low, _profile(), transforms, top_k=20)
        high_scored = evaluate_motivation(high, _profile(), transforms, top_k=20)
        assert all(item.candidate.family != "entertainment" for item in low_scored)
        assert any(item.candidate.family == "entertainment" for item in high_scored)

    def test_lifestyle_enabled_set_scopes_sampling(self, transforms):
        # 生活活动只从人物启用集采样：启用集外的目录内条目不进候选（docs/dev/SPEC-life-realism.md「动机层」）。
        met = {"lifestyle:jogging": 7.0, "lifestyle:archery": 4.3, "lifestyle:yoga": 3.0}
        body = _body().model_copy(update={"activity_met": dict(_body().activity_met, **met)})
        context = _context(body=body, lifestyle_enabled=("yoga",))
        scored = evaluate_motivation(context, _profile(), transforms, top_k=30)
        summaries = [item.candidate.summary for item in scored
                     if item.candidate.family == "lifestyle"]
        assert len(summaries) == 1
        assert "lifestyle:yoga" in summaries[0]
        assert not any("lifestyle:jogging" in summary or "lifestyle:archery" in summary
                       for summary in summaries)

    def test_repeated_food_downweighted_by_sensory_satiety(self, transforms):
        # Rolls 1981 感官特异性饱足：吃过 2 次的商品按 1/3 降权，排在等距新鲜食物后。
        assert meal_repeat_weight(0) == pytest.approx(1.0)
        assert meal_repeat_weight(2) == pytest.approx(1.0 / 3.0)
        foods = (_food_offer("food-fresh", kcal=600, price=500),
                 _food_offer("food-old", kcal=600, price=500))
        context = _context(agent=_agent(energy=1000.0, location="market"),
                           food_offers=foods,
                           supply_stock={item.supply.supply_id: 5 for item in foods},
                           locations={"market": _location()},
                           recent_meal_counts={"food-old": 2})
        assert [item.summary for item in _eat_candidates(context)] == [
            "Buy and eat food-fresh (supply-food-fresh)", "Buy and eat food-old (supply-food-old)"]

    def test_zero_taste_weights_keep_entertainment_ranked_last(self, transforms):
        # docs/dev/SPEC-life-realism.md「画像编译」：口味权重是排序信号不是准入条件。
        # 零权重条目仍在候选集里，只排在非零权重之后；不做 0.5 缺省抬升。
        contents = (_content("movie-0001", "feature_film", 3600, 0.2),
                    _content("movie-0002", "feature_film", 3600, 0.2))
        context = _context(contents=contents, taste_weights={"movie-0001": 0.8})
        scored = evaluate_motivation(context, _profile(), transforms, top_k=20)
        entertainment = [item.candidate.summary for item in scored
                         if item.candidate.family == "entertainment"]
        # 两条都进候选（零权重那条不被丢弃），且零权重排在非零权重之后
        assert len(entertainment) == 2
        assert "movie-0001" in entertainment[0] and "movie-0002" in entertainment[1]

    def test_unstated_genre_items_stay_in_candidate_pool(self, transforms):
        # 卡片未声明某题材时该题材条目权重为零：必须仍在候选内，不得在枚举时被丢弃。
        contents = tuple(_content(f"movie-{index:04d}", "feature_film", 3600, 0.2)
                         for index in range(1, 4))
        context = _context(contents=contents, taste_weights={})
        scored = evaluate_motivation(context, _profile(), transforms, top_k=20)
        assert any(item.candidate.family == "entertainment" for item in scored)

    def test_sleep_candidate_clears_deficit_at_one_hour_per_hour(self, transforms):
        # 新标度：睡眠每小时恢复 1h 不足账——4h 账给出 4.0h 睡眠候选并清零。
        context = _context(agent=_agent(sleep_debt=4.0))
        scored = evaluate_motivation(context, _profile(), transforms, top_k=10)
        sleeper = next(item for item in scored if item.candidate.family == "sleep")
        assert sleeper.candidate.duration_seconds == 4 * 3600
        assert sleeper.candidate.sleep_deficit_delta == pytest.approx(-4.0)
        assert "clears sleep deficit 4.0h -> 0.0h" in sleeper.candidate.reason

    def test_awake_deficit_delta_uses_need_ratio_scale(self):
        # 新累积标度 need/(24-need)（7.5h -> 约 0.4545 h/h），与结算同式。
        body = _agent().body
        assert _awake_deficit_delta(body, 2.0) == pytest.approx(7.5 / 16.5 * 2.0)


class TestBrowseCandidate:
    """@brief browse 候选：目录有未见商品时在场，全可见时退场。"""

    def test_browse_offered_when_catalog_has_unseen_products(self, transforms):
        # 目录 2 件、可见 0 件：无 eat 候选（链路断），browse 候选在场（打通链路）。
        context = _context(agent=_agent(energy=455.0), food_offers=(),
                           catalog_food_offers=(_food_offer(), _food_offer("food-2", price=600)),
                           supply_stock={"supply-food-1": 5, "supply-food-2": 5},
                           locations={"market": _location()})
        scored = evaluate_motivation(context, _profile(), transforms)
        families = {item.candidate.family for item in scored}
        assert "browse" in families
        assert "eat" not in families
        browse = next(item for item in scored if item.candidate.family == "browse")
        assert "nearby markets" in browse.candidate.reason
        assert browse.candidate.duration_seconds == 600

    def test_browse_absent_when_catalog_fully_visible(self, transforms):
        context = _context(agent=_agent(energy=455.0), food_offers=(_food_offer(),),
                           catalog_food_offers=(_food_offer(),), supply_stock={"supply-food-1": 5},
                           locations={"market": _location()},
                           routes=(_route("r1", "market", "home"),))
        scored = evaluate_motivation(context, _profile(), transforms)
        assert all(item.candidate.family != "browse" for item in scored)

    def test_browse_absent_without_catalog(self, transforms):
        context = _context(agent=_agent(energy=455.0))
        scored = evaluate_motivation(context, _profile(), transforms)
        assert all(item.candidate.family != "browse" for item in scored)


# ---------------------------------------------------------------------------
# 档案与变换参数
# ---------------------------------------------------------------------------

class TestContractModels:
    def test_frozen_contract_validates(self):
        assert MotivationProfile.model_validate(CONTRACT_PROFILE).tau == 1.0

    @pytest.mark.parametrize("mutation", [
        {"weights": {"money_fen": 0.9, "energy_kcal": 0.05, "sleep_debt_hours": 0.05,
                     "entertainment_need": 0.05, "goal_task": 0.05}},
        {"weights": {"money_fen": 0.45}},
        {"loss_aversion": {"money_fen": 0.5}},
        {"loss_aversion": {}},
        {"comfort": {"balance_fen": 100}},
        {"aspiration": {"daily_effective_work_seconds": -1}},
        {"big_five": {"openness": 4, "conscientiousness": 6, "extraversion": 3,
                      "agreeableness": 4}},
        {"attention_prior": {"": 0.5}},
        {"attention_prior": {"feature_film": 1.5}},
        # 注册表规则 6：键集必须精确等于 attention_required=true 键集——
        # 缺键（少 drama_series，总和仍 1.0）与多键（多的 game_session 是
        # attention_required=false 的显式排除面）都拒绝。
        {"attention_prior": {"feature_film": 0.25, "news_feed": 0.25,
                             "joke_feed": 0.25, "webnovel_excerpt": 0.25}},
        {"attention_prior": {**CONTRACT_PROFILE["attention_prior"], "game_session": 0.0}},
        {"schema_version": 2},
        {"extra_field": 1},
    ])
    def test_contract_violations_rejected(self, mutation):
        payload = json.loads(json.dumps(CONTRACT_PROFILE))
        payload.update(mutation)
        with pytest.raises(Exception):
            MotivationProfile.model_validate(payload)

    def test_contract_attention_keys_equal_registry_attention_required_kinds(self):
        """@brief 影子钉死：合同档案注意力键集 == 注册表 attention_required=true 键集
        （老画像 5 键在新注册表下仍合法——键集恰好相等）。"""
        from worldmock.catalog.affordances import default_affordances
        assert set(CONTRACT_PROFILE["attention_prior"]) == \
            default_affordances().attention_required_kinds

    def test_transforms_missing_dir_is_none(self, tmp_path):
        assert load_motivation_transforms(tmp_path) is None

    def test_transforms_fail_closed_on_bad_values(self, tmp_path):
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "motivation.toml").write_text(
            '[prospect]\nloss_exponent = 1.5\n[homeostatic]\nsatiation_exponent = 0.88\n'
            '[normalization]\nmoney_fen = 1000.0\nenergy_kcal = 500.0\n'
            'sleep_debt_hours = 2.0\nentertainment_need = 0.25\ngoal_progress = 1.0\n'
            '[cost]\ntime_value_fen_per_hour = 2400.0\n[habit]\nweight = 0.3\ndiscount = 0.9\n',
            encoding="utf-8")
        with pytest.raises(IntegrityError):
            load_motivation_transforms(rules)

    def test_transforms_reject_unknown_group(self, tmp_path):
        rules = tmp_path / "rules"
        rules.mkdir()
        (rules / "motivation.toml").write_text(
            '[prospect]\nloss_exponent = 0.88\n[homeostatic]\nsatiation_exponent = 0.88\n'
            '[normalization]\nmoney_fen = 1000.0\nenergy_kcal = 500.0\n'
            'sleep_debt_hours = 2.0\nentertainment_need = 0.25\ngoal_progress = 1.0\n'
            '[cost]\ntime_value_fen_per_hour = 2400.0\n[habit]\nweight = 0.3\ndiscount = 0.9\n'
            '[bogus]\nx = 1\n', encoding="utf-8")
        with pytest.raises(IntegrityError):
            load_motivation_transforms(rules)


# ---------------------------------------------------------------------------
# 引擎注入（集成）：格式、兼容与不进结算
# ---------------------------------------------------------------------------

def _profiled_engine(engine_factory):
    engine = engine_factory()
    agents = tuple(item.model_copy(
        update={"motivation": _profile()} if item.agent_id == 1 else {"motivation": None})
        for item in engine.scenario.agents)
    engine.scenario = engine.scenario.model_copy(update={"agents": agents}, deep=True)
    return engine


class TestEngineInjection:
    def test_profiled_agent_gets_hints(self, engine_factory):
        engine = _profiled_engine(engine_factory)
        first = round_context(engine, "r-hints", agent_id=1)
        data = engine.observe(first)
        assert "motivation_internal" in data
        hints = data["motivation_internal"]
        assert hints["schema_version"] == 1
        assert 0 < len(hints["candidates"]) <= 5
        for candidate in hints["candidates"]:
            assert set(candidate) == {"rank", "family", "action_summary",
                                      "softmax_probability", "tradeoff", "reason_text"}
            assert set(candidate["tradeoff"]) == {"homeostatic", "habit", "emotion", "cost"}
            assert "score" not in candidate  # 相对分呈现：绝对总分不入观察
        ranks = [candidate["rank"] for candidate in hints["candidates"]]
        assert ranks == list(range(1, len(ranks) + 1))  # rank 从 1 起单调

    def test_hints_rank_orders_by_relative_preference(self, engine_factory):
        """@brief rank 1 的 softmax 概率不低于后续候选（相对排序自洽）。"""
        engine = _profiled_engine(engine_factory)
        data = engine.observe(round_context(engine, "r-rank", agent_id=1))
        candidates = data["motivation_internal"]["candidates"]
        probabilities = [candidate["softmax_probability"] for candidate in candidates]
        assert probabilities == sorted(probabilities, reverse=True)

    def test_hints_include_lifestyle_recommendations(self, engine_factory):
        """@brief M4 激活后：真实场景（183 条全启用）的候选池出现生活活动推荐——
        family=lifestyle、动作摘要携带可执行的 lifestyle:<id> 目标、配额 ≤4。
        激活前（generator=none）lifestyle 家族不进枚举序，此类候选不可能出现。
        初态的 top-5 提示通常被 watch 候选占据（relief=none 使生活候选的内稳态
        分量天然靠后），故断言打在完整枚举池上。"""
        engine = _profiled_engine(engine_factory)
        agent = engine.state.agents[1]
        profile = next(a.motivation for a in engine.scenario.agents
                       if a.agent_id == 1 and a.motivation is not None)
        scored = evaluate_motivation(
            engine._motivation_context(agent, profile), profile,
            engine.semantics.motivation_transforms, top_k=MAX_CANDIDATES)
        lifestyle = [item for item in scored if item.candidate.family == "lifestyle"]
        assert 0 < len(lifestyle) <= 4
        for item in lifestyle:
            assert "lifestyle:" in item.candidate.summary
            assert item.candidate.summary.startswith("Do ")

    def test_unprofiled_scenario_never_injects(self, engine_factory):
        engine = engine_factory()
        # 真实场景现已携带编译档案；本测试显式剥离以验证“无档案”分支。
        agents = tuple(item.model_copy(update={"motivation": None})
                       for item in engine.scenario.agents)
        engine.scenario = engine.scenario.model_copy(update={"agents": agents}, deep=True)
        data = engine.observe(round_context(engine, "r-plain", agent_id=1))
        assert "motivation_internal" not in data

    def test_hints_do_not_enter_settlement(self, engine_factory):
        """@brief 有无提示的两个同源运行走同一决策轮，结果与终态逐字节一致。"""
        plain, hinted = engine_factory(), _profiled_engine(engine_factory)
        request = ("start_activity", StartActivityRequest(targets=["自己"], actions=["sleep"], ways=["睡觉"]))
        results = []
        states = []
        for engine in (plain, hinted):
            _, committed = submit_round(engine, {1: request}, "r-settle")
            results.append([item.model_dump(mode="json") for item in committed])
            states.append(engine.state.model_dump(mode="json"))
        # 事件 UUID 逐运行随机（origin_request_event_id）、工厂按创建序分配 run_id，
        # 两者都与提示无关，剔除后必须逐字节一致。
        noise_keys = {"origin_request_event_id", "run_id"}

        def strip_event_ids(node):
            if isinstance(node, dict):
                return {key: strip_event_ids(value) for key, value in node.items()
                        if key not in noise_keys}
            if isinstance(node, list):
                return [strip_event_ids(value) for value in node]
            return node
        assert strip_event_ids(results[0]) == strip_event_ids(results[1])
        assert strip_event_ids(states[0]) == strip_event_ids(states[1])

    def test_evaluation_failure_fails_open(self, engine_factory, monkeypatch):
        engine = _profiled_engine(engine_factory)

        def explode(context, profile, transforms, top_k=5):
            raise RuntimeError("boom")

        monkeypatch.setattr("worldmock.life.observe.evaluate_motivation", explode)
        data = engine.observe(round_context(engine, "r-boom", agent_id=1))
        assert "motivation_internal" not in data

    def test_missing_transforms_disable_hints(self, engine_factory):
        engine = _profiled_engine(engine_factory)
        engine.semantics = replace(engine.semantics, motivation_transforms=None)
        data = engine.observe(round_context(engine, "r-off", agent_id=1))
        assert "motivation_internal" not in data


class TestMarketSummary:
    """商店存在性摘要不泄露尚未实际呈现的门店报价。"""

    def test_summary_common_knowledge_without_browse(self, engine_factory):
        engine = engine_factory()
        data = engine.observe(round_context(engine, "r-mkt", agent_id=1))
        assert not [row for row in data["tool_layer"]["targets"] if row["kind"] == "product"
                    and "出售的" in row["name"]]
        assert data["market_summary"] == {"supply_count": len(engine.scenario.supplies)}
        assert not {"open", "price_range_fen", "price_version"} & data["market_summary"].keys()

    def test_summary_stays_public_when_market_opens(self, engine_factory):
        engine = engine_factory()
        request = ("start_activity", StartActivityRequest(targets=["自己"], actions=["sleep"], ways=["睡觉"]))
        submit_round(engine, {1: request}, "r-mkt-open")
        advance_next(engine)
        data = engine.observe(round_context(engine, "r-mkt-open2", agent_id=1))
        assert data["market_summary"] == {"supply_count": len(engine.scenario.supplies)}
        assert data["supplies"] == []

    def test_no_store_scenario_omits_summary(self, engine_factory):
        engine = engine_factory()
        engine.scenario = engine.scenario.model_copy(update={"supplies": ()})
        data = engine.observe(round_context(engine, "r-mkt-none", agent_id=1))
        assert "market_summary" not in data


def test_food_affordability_uses_category_amount_instead_of_raw_quote():
    """同一供给报价在品类系数改变后按实际付款判断余额与记录成本。"""
    from dataclasses import replace
    from worldmock.categories import evaluate_purchase

    offer = _food_offer("meal", price=100, kcal=600)
    context = _context(agent=_agent(energy=1000.0, location="market"), food_offers=(offer,),
                       balance_fen=34, locations={"market": _location()},
                       purchase_amount=lambda product, price, quantity: evaluate_purchase(price, quantity, 0.335))
    candidates = _eat_candidates(context)
    assert len(candidates) == 1
    assert candidates[0].money_now_fen == -34
    assert _eat_candidates(replace(context, balance_fen=33)) == []
    expensive = replace(context, balance_fen=100,
                        purchase_amount=lambda product, price, quantity: evaluate_purchase(price, quantity, 2))
    assert _eat_candidates(expensive) == []


def test_no_effect_purchase_does_not_hide_held_food():
    """无作用的购买没有供给进食候选，但人物已持有食品仍然可用。"""
    offer = _food_offer("meal", price=100, kcal=600)
    context = _context(agent=_agent(energy=1000.0, location="market"), food_offers=(offer,),
                       locations={"market": _location()}, purchase_pairing_states={"meal": "no_effect"},
                       purchase_amount=lambda *args: pytest.fail("Inactive purchase must not be evaluated"))
    assert _eat_candidates(context) == []
    held = context.agent.model_copy(update={"inventory": {"market": {"meal": 1}}}, deep=True)
    candidates = _eat_candidates(replace(context, agent=held))
    assert len(candidates) == 1 and candidates[0].money_now_fen == 0
