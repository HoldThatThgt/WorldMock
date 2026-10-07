"""@brief 固定食品类别的真实动机候选与当前菜单能力投影。"""

from dataclasses import replace
from worldmock.exchange.types import FoodCategory
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.motivation import MotivationContext, _eat_candidates, _enumerate_candidates
from tests.life.purchase_worlds import registered_world


def food_context(balance=30000):
    semantics, state = registered_world(balance=balance)
    return MotivationContext(agent=state.agents[1], balance_fen=balance, body=semantics.body,
        home_location_id="home", catalog=semantics.catalog,
        location_ids=frozenset(place.location_id for place in semantics.scenario.locations),
        menu=InteractionMenu(allowed_actions=("consume", "wait", "sleep")), lifestyle_items=(),
        activity_location_kinds={}, work_topics=(), topic_totals={}, workplace_location_id=None,
        active_activities=(), habit_events=(), consecutive_rejections=0, max_same_moment_decisions=4,
        total_work_seconds=0, current_time=state.current_time, end_time=state.end_time,
        timezone=state.timezone)


def test_meal_candidates_use_meal_category_and_actual_fixed_delivery_wait():
    context = food_context()
    context = replace(context, agent=context.agent.model_copy(update={
        "body": context.agent.body.model_copy(update={"energy_kcal": 2300})}))
    candidates = _eat_candidates(context)
    assert candidates
    assert all(any(food.food_id in candidate.summary for food in context.catalog.foods.values()
                   if food.category == FoodCategory.MEAL) for candidate in candidates)
    noodle = next(candidate for candidate in candidates if "noodle-1:beef-noodles" in candidate.summary)
    distance = context.catalog.shortest_m("noodle-1", "home")
    assert noodle.delay_seconds == 600 + (distance + 4) // 5
    assert noodle.money_now_fen == -3300 and noodle.duration_seconds == 1200


def test_no_affordable_meal_uses_held_snack_without_inventing_a_meal():
    context = food_context(0)
    candidates = _eat_candidates(context)
    assert len(candidates) == 1 and "chips" in candidates[0].summary
    assert candidates[0].money_now_fen == 0 and candidates[0].delay_seconds == 0


def test_current_food_menu_projects_real_families_and_never_enumerates_browse():
    context = food_context()
    candidates = _enumerate_candidates(context, None)
    assert candidates
    assert any(candidate.family == "eat" for candidate in candidates)
    assert all("browse" not in candidate.family and "Browse" not in candidate.summary for candidate in candidates)
    assert not any(candidate.work_type for candidate in candidates)
    disabled = replace(context, menu=InteractionMenu(allowed_actions=("wait", "sleep")))
    assert not any(candidate.family == "eat" for candidate in _enumerate_candidates(disabled, None))
