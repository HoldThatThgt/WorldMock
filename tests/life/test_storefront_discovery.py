"""商品窗口、当前报价、库存和可见凭据的合同测试。"""

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from worldmock.categories import PairingState, evaluate_purchase
from worldmock.life import discovery, presentation, projection, retrieval
from worldmock.life.candidates import CandidateEntry, CandidateInputs, LocationView, recall
from worldmock.life.models import DataSource
from worldmock.life.map_models import LocationConfig, RouteConfig, SupplyConfig
from worldmock.life.models import AgentState, SupplyQuoteState, WorldState
from worldmock.life.readable import money_phrase
from worldmock.life.presentation import PoolEntry
from worldmock.life.result_projection import public_supply_row


NOW = int(datetime(2026, 1, 5, 4, tzinfo=timezone.utc).timestamp())


def _source():
    return DataSource(kind="scenario", url="https://example.test/scenario", retrieved_at="2026-01-01T00:00:00Z",
                      license="test-license", notice="Test scenario", fact_cutoff="2026-01-01",
                      applicability="Synthetic test conditions")


def _location(identity, *, calendar=True, required=()):
    opening = {"effective_from": "2026-01-01T00:00:00Z", "effective_to": None,
               "reviewed_on": "2026-01-01", "still_valid_on": "2026-01-01", "source": _source(),
               "weekly": {str(day): [{"opens": 0, "closes": 1440}] for day in range(7)}, "exceptions": {}}
    return LocationConfig(location_id=identity, name=identity, kind="shop", business_kind="convenience",
                          longitude=121.6, latitude=31.2, location_kinds=("venue",),
                          opening_calendars=(opening,) if calendar else (), required_qualifications=required,
                          brand=None, source=_source(), affordances={})


def _supply(identity, place, *, required=()):
    return SupplyConfig(supply_id=identity, location_id=place, product_id="meal", seller_account_id=place,
                        quotes=(), initial_stock=5, fulfillment_modes=("pickup", "delivery"),
                        delivery_destination_ids=("home",), delivery_delay_seconds=120,
                        required_qualifications=required, source=_source())


def _route(place, status, *, origin="home"):
    known = status != "unknown"
    return RouteConfig(route_id=f"{origin}-{place}-walking", origin_id=origin, destination_id=place,
                       mode="walking", status=status,
                       distance_meters=300 if status == "reachable" else None,
                       duration_seconds=200 if status == "reachable" else None,
                       fare_fen=0 if status == "reachable" else None, fare_source_kind="scenario",
                       response_path="test.json" if known else None, response_sha256="a" * 64 if known else None,
                       reason=None if status == "reachable" else status,
                       source_url="https://example.test/routed-foot/route/v1/foot/test",
                       routing_instance="https://example.test/routed-foot", routing_profile="foot", source=_source())


@pytest.fixture
def world():
    locations = (_location("home"), _location("store-a"), _location("store-b", required=("member",)),
                 _location("store-u", calendar=False))
    supplies = (_supply("supply-a", "store-a"), _supply("supply-b", "store-b", required=("staff",)),
                _supply("supply-u", "store-u"))
    dataset = SimpleNamespace(dataset_id="test-map", version=1,
                              locations_file=SimpleNamespace(sha256="a" * 64),
                              supplies_file=SimpleNamespace(sha256="b" * 64),
                              routes_file=SimpleNamespace(sha256="c" * 64))
    product = SimpleNamespace(product_id="meal", name="Meal", energy_kcal_per_unit=500, use_duration_seconds=600)
    statuses = {"home": "reachable", "store-a": "reachable", "store-b": "unreachable", "store-u": "unknown"}
    routes = tuple(_route(destination.location_id, statuses[destination.location_id], origin=origin.location_id)
                   for origin in locations for destination in locations if origin.location_id != destination.location_id)
    scenario = SimpleNamespace(locations=locations, supplies=supplies, products=(product,), timezone="Asia/Shanghai",
                               map_dataset=dataset, routes=routes)
    agent = AgentState.model_construct(agent_id=1, location_id="home", qualifications=set())
    quotes = {item.supply_id: SupplyQuoteState(price_fen=100 + index, price_version=f"v{index}",
                                              effective_from=NOW - 1, effective_to=None,
                                              source=_source().model_dump(mode="json"))
              for index, item in enumerate(supplies)}
    state = WorldState.model_construct(version=0, current_time=NOW, agents={1: agent}, supply_quotes=quotes,
                                       supply_stock={"supply-a": 5, "supply-b": 0, "supply-u": 2},
                                       opening_exceptions={"store-a": {"2026-01-05": []}})
    semantics = SimpleNamespace(travel_planned_seconds=lambda mode, base, now: base * 2,
                                travel_time_factor=lambda mode, now: 2,
                                purchase_pairing=lambda product: SimpleNamespace(
                                    state=PairingState.DEFINED, coefficient=1),
                                purchase_amount=lambda product, price, quantity: price * quantity,
                                candidate_constants=SimpleNamespace(presentation_limit=20))
    return discovery.DiscoveryContext(state, scenario, semantics, agent)


def test_delivery_only_supply_does_not_claim_pickup_allowed(world):
    world.agent.location_id = "store-a"
    supply = world.scenario.supplies[0].model_copy(update={"fulfillment_modes": ("delivery",)})
    row = discovery.supply_row(world, supply)
    assert row["pickup_allowed"] is False
    assert row["fulfillment_modes"] == ["delivery"]


def test_supply_uses_current_world_quote_and_preserves_unknown(world):
    supply = world.scenario.supplies[0]
    quote = world.state.supply_quotes[supply.supply_id]
    world.state.supply_quotes[supply.supply_id] = quote.model_copy(
        update={"price_fen": 999, "price_version": "runtime"})
    current = discovery.supply_row(world, supply)
    assert current["price_fen"] == current["purchase_price_fen"] == 999
    assert current["price_version"] == "runtime"
    world.state.supply_quotes[supply.supply_id] = quote.model_copy(update={"effective_to": NOW})
    expired = discovery.supply_row(world, supply)
    assert expired["quote_status"] == "unknown"
    assert expired["price_fen"] is None and expired["price_version"] is None
    assert expired["purchase_price_fen"] is None


def test_category_price_does_not_rewrite_source_quote(world):
    supply = world.scenario.supplies[0]
    world.semantics.purchase_pairing = lambda product: SimpleNamespace(
        state=PairingState.DEFINED, coefficient=0.335)
    world.semantics.purchase_amount = lambda product, price, quantity: evaluate_purchase(price, quantity, 0.335)
    raw = discovery.supply_row(world, supply)
    public = public_supply_row(raw)
    assert raw["quote"]["price_fen"] == raw["price_fen"] == 100
    assert raw["price_version"] == "v0"
    assert public["purchase_coefficient"] == 0.335
    assert public["purchase_price_fen"] == 34
    world.state.supply_quotes[supply.supply_id] = None
    unknown = discovery.supply_row(world, supply)
    assert unknown["purchase_price_fen"] is None
    assert unknown["purchase_coefficient"] == 0.335


def test_no_effect_supply_keeps_quote_but_has_no_purchase_amount(world):
    world.semantics.purchase_pairing = lambda product: SimpleNamespace(state=PairingState.NO_EFFECT, coefficient=None)
    world.semantics.purchase_amount = lambda *args: pytest.fail("Inactive purchase must not be evaluated")
    row = discovery.supply_row(world, world.scenario.supplies[0])
    assert row["price_fen"] == row["quote"]["price_fen"] == 100
    assert row["purchase_pairing_state"] == "no_effect"
    assert row["purchase_coefficient"] is None and row["purchase_price_fen"] is None
    assert row["purchase_allowed"] is False


def test_visibility_requires_presentation_and_expires(world):
    assert projection.visible_supplies(world.state, world.agent, world.scenario) == []
    staged = discovery.stage_visible_supplies(world.state, 1, {"supply-a"}, world.scenario)
    assert world.agent.visible_supply_ids == set()
    assert projection.visible_supplies(staged, staged.agents[1], world.scenario) == ["supply-a"]
    later = staged.model_copy(update={"current_time": NOW + 1801})
    assert projection.visible_supplies(later, later.agents[1], world.scenario) == []
    moved = staged.agents[1].model_copy(update={"location_id": "store-a"})
    assert projection.visible_supplies(staged, moved, world.scenario) == []


def test_presenting_other_supply_does_not_renew_old_visibility(world):
    first = discovery.stage_visible_supplies(world.state, 1, {"supply-a"}, world.scenario)
    later = first.model_copy(update={"current_time": NOW + 900})
    second = discovery.stage_visible_supplies(later, 1, {"supply-b"}, world.scenario)
    assert second.agents[1].supply_browsed_at == {"supply-a": NOW, "supply-b": NOW + 900}
    expired = second.model_copy(update={"current_time": NOW + 1801})
    assert projection.visible_supplies(expired, expired.agents[1], world.scenario) == ["supply-b"]


def test_empty_presentation_preserves_existing_credentials_without_renewal(world):
    first = discovery.stage_visible_supplies(world.state, 1, {"supply-a"}, world.scenario)
    later = first.model_copy(update={"current_time": NOW + 900}, deep=True)
    unchanged = discovery.stage_visible_supplies(later, 1, set(), world.scenario)
    assert unchanged is later
    assert unchanged.agents[1].visible_supply_ids == {"supply-a"}
    assert unchanged.agents[1].supply_browsed_at == {"supply-a": NOW}
    expired = unchanged.model_copy(update={"current_time": NOW + 1801})
    assert projection.visible_supplies(expired, expired.agents[1], world.scenario) == []


def test_same_product_supplies_keep_distinct_window_identity_and_share_taste():
    entries = tuple(PoolEntry(object_id=identity, kind="product", bucket="meal",
                              row={"supply_id": identity, "product_id": "meal"})
                    for identity in ("supply-a", "supply-b"))
    place = LocationView("home", frozenset({"home"}), opening_status="open")
    inputs = CandidateInputs(location=place, locations={"home": place},
                             sources=presentation.entry_sources(entries), current_time=NOW,
                             window_end_time=NOW + 1000, presentation_limit=1,
                             taste_weights={"meal": 0.7}, attention_priors={"meal": 0.5}, local_hour=None)
    first = presentation.present(entries, inputs, "product", None)
    assert first.total == 2 and len(first.items) == 1
    assert first.items[0]["supply_id"] == "supply-a"
    assert first.deferred == ("supply-b",)
    assert {item.taste_weight for item in recall(inputs).retained} == {7000}
    second = presentation.present(entries, inputs, "product", first.next_cursor)
    assert second.items[0]["supply_id"] == "supply-b"
    assert first.items[0]["product_id"] == second.items[0]["product_id"]


def test_activity_location_kinds_and_unknown_opening_are_not_permissive():
    place = LocationView("home", frozenset({"home"}), opening_status="unknown")
    entry = CandidateEntry("yoga", "lifestyle", location_kinds=frozenset({"fitness"}))
    inputs = CandidateInputs(location=place, locations={"home": place}, sources={"activated": (entry,)},
                             current_time=NOW, window_end_time=NOW + 600, local_hour=12)
    assert recall(inputs).excluded[0].reason == "ELIGIBILITY_LOCATION_KIND_MISMATCH"
    free = replace(entry, location_kinds=frozenset({"anywhere"}))
    assert recall(replace(inputs, sources={"activated": (free,)})).excluded[0].reason == "ELIGIBILITY_OPENING_UNKNOWN"
    assert retrieval.feasible_kinds({"home"}, {"anywhere"})
    assert not retrieval.feasible_kinds({"anywhere"}, {"fitness"})
    assert not retrieval.feasible_kinds(set(), {"anywhere"})


def test_location_corpus_missing_activity_kinds_is_rejected(tmp_path):
    (tmp_path / "places.json").write_text('{"locations":[{"location_id":"x"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="activity kinds declaration"):
        retrieval.place_kinds(tmp_path, "places.json")


def _visible_supply(engine, supply_id):
    """@brief 本轮观察里该供给的私有绑定与工具层商品目标（到店取得的动作即其价格与份数）。"""
    observed = engine.observations._observation_data(engine.context.state.agents[1])
    binding = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == "supply" and row["object_id"] == supply_id)
    target = next(row for row in observed["tool_layer"]["targets"] if row["name"] == binding["object"])
    return observed, binding, target


def _price(target) -> str:
    """@brief 商品目标首个取得动作的可读价格。"""
    return next(action["price"] for action in target["actions"] if action["action"] == "obtain")


def test_real_observation_keeps_complete_current_supply_facts_and_private_quote(engine_factory):
    from tests.life.conftest import stage_day_plan
    from tests.life.test_grounded_observation import _commit_test_state, _stage_open_single_stock

    engine = engine_factory()
    stage_day_plan(engine)
    supply_id = _stage_open_single_stock(engine)
    original, binding, target = _visible_supply(engine, supply_id)
    assert target["kind"] == "product"
    assert _price(target) == money_phrase(binding["quote"]["purchase_price_fen"])
    assert any(action["available"] for action in target["actions"] if action["action"] == "obtain")
    assert {"supply_id", "price_version", "changes", "based_on_action_id", "current_stock"}.isdisjoint(target)
    assert supply_id not in json.dumps(original["tool_layer"], ensure_ascii=False)

    state = engine.context.state.model_copy(deep=True)
    quote = state.supply_quotes[supply_id]
    state.supply_quotes[supply_id] = quote.model_copy(update={"price_fen": 999, "price_version": "changed"})
    _commit_test_state(engine, state, "storefront_quote_changed")
    _, changed_binding, changed_target = _visible_supply(engine, supply_id)
    assert _price(changed_target) == money_phrase(changed_binding["quote"]["purchase_price_fen"])
    assert changed_binding["quote"]["price_fen"] == 999
    assert changed_binding["quote"]["price_version"] == "changed"
    assert set(changed_target) == set(target)

    state = engine.context.state.model_copy(deep=True)
    state.supply_quotes[supply_id] = quote.model_copy(update={"effective_to": state.current_time})
    state.supply_stock[supply_id] = 0
    _commit_test_state(engine, state, "storefront_quote_expired_and_sold_out")
    expired = engine.observations._observation_data(engine.context.state.agents[1])
    assert all(row["object_id"] != supply_id for row in expired["interaction_bindings"])
    assert all(row["name"] != binding["object"] for row in expired["tool_layer"]["targets"])
    private = next(row for row in expired["supplies"] if row["supply_id"] == supply_id)
    assert private["quote_status"] == "unknown"
    assert private["price_fen"] is None and private["current_stock"] == 0
    assert private["purchase_allowed"] is False
    assert supply_id not in json.dumps(expired["tool_layer"], ensure_ascii=False)
