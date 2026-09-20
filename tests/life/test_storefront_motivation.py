"""动机候选必须服从具体门店、真实营业与可达性事实。"""

from dataclasses import replace

import pytest

from tests.life.test_motivation import _agent, _context, _food_offer, _location, _profile, _route
from worldmock.life.motivation import _eat_candidates, _travel_candidate, evaluate_motivation
from worldmock.life.narration import render_scene


def _meal_context():
    return _context(agent=_agent(energy=500), food_offers=(_food_offer(),),
                    locations={"market": _location()}, routes=(_route("delivery", "market", "home"),))


@pytest.mark.parametrize("restriction", ["unknown_quote", "future_quote", "expired_quote", "empty_stock",
                                       "poor", "closed", "unknown_hours", "place_access", "supply_access",
                                       "pickup_only", "destination", "unreachable", "unknown_route"])
def test_food_candidates_never_override_storefront_restrictions(restriction):
    context = _meal_context()
    supply = context.supplies[0]
    identity = supply.supply_id
    quote = context.supply_quotes[identity]
    if restriction == "unknown_quote":
        context = replace(context, supply_quotes={identity: None})
    elif restriction == "future_quote":
        context = replace(context, supply_quotes={identity: quote.model_copy(
            update={"effective_from": context.current_time + 1})})
    elif restriction == "expired_quote":
        context = replace(context, supply_quotes={identity: quote.model_copy(
            update={"effective_to": context.current_time})})
    elif restriction == "empty_stock":
        context = replace(context, supply_stock={identity: 0})
    elif restriction == "poor":
        context = replace(context, balance_fen=quote.price_fen - 1)
    elif restriction in {"closed", "unknown_hours", "place_access"}:
        fields = {"closed": {"opening_calendars": (_location(open_hour=7, close_hour=8).opening_calendars[0],)},
                  "unknown_hours": {"opening_calendars": ()},
                  "place_access": {"required_qualifications": ("member",)}}[restriction]
        context = replace(context, locations={"market": context.locations["market"].model_copy(update=fields)})
    elif restriction in {"supply_access", "pickup_only", "destination"}:
        fields = {"supply_access": {"required_qualifications": ("member",)},
                  "pickup_only": {"fulfillment_modes": ("pickup",), "delivery_destination_ids": ()},
                  "destination": {"delivery_destination_ids": ("office",)}}[restriction]
        context = replace(context, supplies=(supply.model_copy(update=fields),))
    else:
        context = replace(context, routes=(context.routes[0].model_copy(
            update={"status": "unreachable" if restriction == "unreachable" else "unknown",
                    "duration_seconds": None, "distance_meters": None, "fare_fen": None}),))
    assert _eat_candidates(context) == []


def test_same_product_retains_distinct_store_prices_and_preparation_departure():
    context = _meal_context()
    supply = context.supplies[0]
    second = supply.model_copy(update={"supply_id": "other-store", "location_id": "second"})
    quote = context.supply_quotes[supply.supply_id]
    departures = []

    def road_time(mode, base, departure):
        departures.append((mode, base, departure))
        return base * 2

    context = replace(context, supplies=(supply, second),
                      locations={"market": _location(), "second": _location("second")},
                      opening_exceptions={"market": {}, "second": {}},
                      supply_stock={supply.supply_id: 2, second.supply_id: 2},
                      supply_quotes={supply.supply_id: quote, second.supply_id: quote.model_copy(
                          update={"price_fen": 100, "price_version": "second-v1"})},
                      routes=context.routes + (_route("other", "second", "home"),),
                      planned_travel_seconds=road_time)
    choices = _eat_candidates(context)
    assert [item.money_now_fen for item in choices] == [-100, -800]
    assert [item.delay_seconds for item in choices] == [1800, 1800]
    assert "other-store" in choices[0].summary and supply.supply_id in choices[1].summary
    assert departures == [("driving", 600, context.current_time + 600)] * 2


def test_held_food_and_same_place_delivery_do_not_require_a_road():
    context = _meal_context()
    context = replace(context, agent=_agent(energy=500, location="market"), routes=())
    supply = context.supplies[0].model_copy(update={"fulfillment_modes": ("delivery",),
                                                  "delivery_destination_ids": ("market",)})
    context = replace(context, supplies=(supply,))
    assert _eat_candidates(context)[0].delay_seconds == 600
    context = replace(context, agent=context.agent.model_copy(update={"inventory": {"market": {"food-1": 1}}}),
                      supplies=())
    assert _eat_candidates(context)[0].money_now_fen == 0
    assert _eat_candidates(context)[0].delay_seconds == 0


@pytest.mark.parametrize("status", ["unknown", "unreachable"])
def test_travel_motivation_never_treats_missing_route_metrics_as_a_trip(status):
    context = _meal_context()
    route = _route("outward", "home", "market").model_copy(update={
        "status": status, "duration_seconds": None, "fare_fen": None, "distance_meters": None})
    assert _travel_candidate(replace(context, routes=(route,)), "Store", "market") is None


def test_lifestyle_candidates_use_current_explicit_location_environments():
    from worldmock.life.motivation import load_motivation_transforms

    context = _context(lifestyle_enabled=("jogging",))
    jogging = next(item for item in context.lifestyle_items if item.activity_id == "jogging")
    context = replace(context, body=context.body.model_copy(update={
        "activity_met": {**context.body.activity_met, "lifestyle:jogging": jogging.met_value}}))
    transforms = load_motivation_transforms("catalog/rules")
    assert any(item.candidate.family == "lifestyle" for item in evaluate_motivation(
        context, _profile(), transforms, top_k=30))
    indoors = replace(context, locations={"home": _location("home").model_copy(
        update={"location_kinds": ("office",)})})
    assert not any(item.candidate.family == "lifestyle" for item in evaluate_motivation(
        indoors, _profile(), transforms, top_k=30))
    assert not any(item.candidate.family == "lifestyle" for item in evaluate_motivation(
        replace(context, locations={}), _profile(), transforms, top_k=30))


def test_scene_distinguishes_unknown_business_hours_from_closed():
    scene = render_scene({"reachable_places": [{"name": "Unknown store", "opening_status": "unknown"}]})
    assert "营业信息未知" in scene
    assert "未营业" not in scene
