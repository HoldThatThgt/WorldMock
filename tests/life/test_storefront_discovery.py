"""地点发现、独立供给身份与实际呈现凭据的合同测试。"""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from worldmock.categories import PairingState

import pytest

from worldmock.life import discovery, presentation, projection, retrieval
from worldmock.life.candidates import CandidateEntry, CandidateInputs, LocationView, recall
from worldmock.life.map_models import DataSource, LocationConfig, RouteConfig, SupplyConfig
from worldmock.life.models import AgentState, DiscoverPlacesRequest, SupplyQuoteState, WorldState
from worldmock.life.presentation import PoolEntry


NOW = int(datetime(2026, 1, 5, 4, tzinfo=timezone.utc).timestamp())


def _source():
    return DataSource(kind="scenario", url="https://example.test/scenario", retrieved_at="2026-01-01T00:00:00Z",
                      license="test-license", notice="Test scenario", fact_cutoff="2026-01-01",
                      applicability="Synthetic test conditions")


def _location(identity, *, calendar=True, required=()):
    source = _source()
    opening = {"effective_from": "2026-01-01T00:00:00Z", "effective_to": None,
               "reviewed_on": "2026-01-01", "still_valid_on": "2026-01-01", "source": source,
               "weekly": {str(day): [{"opens": 0, "closes": 1440}] for day in range(7)}, "exceptions": {}}
    return LocationConfig(location_id=identity, name=identity, kind="shop", business_kind="convenience",
                          longitude=121.6, latitude=31.2, location_kinds=("venue",),
                          opening_calendars=(opening,) if calendar else (), required_qualifications=required,
                          brand=None, source=source, affordances={})


def _supply(identity, place, *, product="meal", required=()):
    return SupplyConfig(supply_id=identity, location_id=place, product_id=product, seller_account_id=place,
                        quotes=(), initial_stock=5, fulfillment_modes=("pickup", "delivery"),
                        delivery_destination_ids=("home",), delivery_delay_seconds=120,
                        required_qualifications=required, source=_source())


def _route(place, status, *, seconds=200, origin="home"):
    known = status != "unknown"
    return RouteConfig(route_id=f"{origin}-{place}-walking", origin_id=origin, destination_id=place,
                       mode="walking", status=status,
                       distance_meters=300 if status == "reachable" else None,
                       duration_seconds=seconds if status == "reachable" else None,
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
                                purchase_pairing=lambda product: SimpleNamespace(state=PairingState.DEFINED, coefficient=1),
                                purchase_amount=lambda product, price, quantity: price * quantity,
                                candidate_constants=SimpleNamespace(presentation_limit=20))
    return discovery.DiscoveryContext(state, scenario, semantics, agent)


def _query(world, **overrides):
    request = DiscoverPlacesRequest(**{"product_id": "meal", "business_kind": None, "travel_mode": "walking",
                                       "max_travel_seconds": 399, "cursor": None, **overrides})
    return discovery.discover_places(world.state, world.scenario, world.semantics, world.agent, request)


def test_discovery_keeps_closed_sold_out_unreachable_unknown_and_outside(world):
    result = _query(world)
    places = {row["location_id"]: row for row in result["places"]}
    assert set(places) == {"store-a", "store-b", "store-u"}
    a, b, unknown = (places[key] for key in ("store-a", "store-b", "store-u"))
    assert (a["opening_status"], a["range_status"], a["route"]["duration_seconds"]) == ("closed", "outside", 400)
    assert a["supplies"][0]["purchase_allowed"] is False
    assert b["route"]["status"] == b["range_status"] == "unreachable"
    assert b["supplies"][0]["current_stock"] == 0
    assert b["missing_qualifications"] == ["member"]
    assert b["supplies"][0]["missing_qualifications"] == ["staff"]
    assert unknown["opening_status"] == unknown["route"]["status"] == unknown["range_status"] == "unknown"
    assert unknown["route"]["distance_meters"] is None
    assert unknown["route"]["reason"] == "unknown"
    assert all(row["supplies"][0]["scenario_assumption"] for row in places.values())


def test_on_site_delivery_only_supply_does_not_claim_pickup_allowed(world):
    world.agent.location_id = "store-a"
    supply = world.scenario.supplies[0].model_copy(update={"fulfillment_modes": ("delivery",)})
    row = discovery.supply_row(world, supply)
    assert row["location_id"] == world.agent.location_id
    assert row["pickup_allowed"] is False
    assert row["fulfillment_modes"] == ["delivery"]


def test_discovery_filters_and_boundary_use_road_time_not_distance(world):
    result = _query(world, max_travel_seconds=400)
    assert result["query"]["max_travel_seconds"] == 400
    assert result["places"][0]["range_status"] == "inside"
    assert _query(world, business_kind="hospital")["places"] == []
    assert _query(world, product_id="unavailable")["places"] == []
    local = _query(world, product_id=None, max_travel_seconds=None)["places"][0]
    assert local["location_id"] == "home"
    assert local["route"]["reason"] == "already_at_destination"
    assert local["route"]["duration_seconds"] == 0


def test_discovery_uses_current_world_quote_and_preserves_unknown(world):
    quote = world.state.supply_quotes["supply-a"]
    world.state.supply_quotes["supply-a"] = quote.model_copy(update={"price_fen": 999, "price_version": "runtime"})
    first = _query(world)["places"][0]["supplies"][0]
    assert first["price_fen"] == 999 and first["price_version"] == "runtime"
    world.state.supply_quotes["supply-a"] = quote.model_copy(update={"effective_to": NOW})
    second = _query(world)["places"][0]["supplies"][0]
    assert second["quote_status"] == "unknown"
    assert second["price_fen"] is None and second["price_version"] is None


def test_supply_visibility_requires_actual_presentation_and_expires(world):
    assert projection.visible_supplies(world.state, world.agent, world.scenario) == []
    staged = discovery.stage_visible_supplies(world.state, 1, {"supply-a"}, world.scenario)
    assert world.agent.visible_supply_ids == set()
    assert projection.visible_supplies(staged, staged.agents[1], world.scenario) == ["supply-a"]
    later = staged.model_copy(update={"current_time": NOW + 1801})
    assert projection.visible_supplies(later, later.agents[1], world.scenario) == []
    moved = staged.agents[1].model_copy(update={"location_id": "store-a"})
    assert projection.visible_supplies(staged, moved, world.scenario) == []


def test_presenting_another_supply_does_not_renew_old_supply_visibility(world):
    first = discovery.stage_visible_supplies(world.state, 1, {"supply-a"}, world.scenario)
    later = first.model_copy(update={"current_time": NOW + 900})
    second = discovery.stage_visible_supplies(later, 1, {"supply-b"}, world.scenario)
    assert second.agents[1].supply_browsed_at == {"supply-a": NOW, "supply-b": NOW + 900}
    expired = second.model_copy(update={"current_time": NOW + 1801})
    assert projection.visible_supplies(expired, expired.agents[1], world.scenario) == ["supply-b"]


def test_empty_presentation_keeps_existing_supply_credentials_without_renewing_them(world):
    """未呈现任何供给不能授权全目录、重置已知集或延长旧供给的可见期。"""
    first = discovery.stage_visible_supplies(world.state, 1, {"supply-a"}, world.scenario)
    later = first.model_copy(update={"current_time": NOW + 900}, deep=True)
    unchanged = discovery.stage_visible_supplies(later, 1, set(), world.scenario)
    assert unchanged is later
    assert unchanged.agents[1].visible_supply_ids == {"supply-a"}
    assert unchanged.agents[1].supply_browsed_at == {"supply-a": NOW}
    expired = unchanged.model_copy(update={"current_time": NOW + 1801})
    assert projection.visible_supplies(expired, expired.agents[1], world.scenario) == []


def test_discovery_paginates_every_match_across_world_versions_and_keeps_empty_places(world):
    world.semantics.candidate_constants.presentation_limit = 1
    rows, cursor = [], None
    while True:
        page = _query(world, product_id=None, cursor=cursor)
        assert page["total"] == 4 and page["returned"] == 1
        rows.extend((place["location_id"], supply["supply_id"] if supply else None)
                    for place in page["places"] for supply in (place["supplies"] or [None]))
        cursor = page["next_cursor"]
        world.state.version = len(rows)
        world.state.current_time += 1
        if cursor is None:
            break
    assert rows == [("home", None), ("store-a", "supply-a"), ("store-b", "supply-b"), ("store-u", "supply-u")]


@pytest.mark.parametrize("change", ["query", "origin", "map", "malformed"])
def test_discovery_rejects_cursor_for_other_conditions(world, change):
    from worldmock.errors import BusinessRuleError

    world.semantics.candidate_constants.presentation_limit = 1
    cursor = _query(world)["next_cursor"]
    overrides = {"cursor": cursor}
    if change == "query":
        overrides["max_travel_seconds"] = 400
    elif change == "origin":
        world.agent.location_id = "store-a"
        assert _query(world)["places"][0]["route"]["origin_id"] == "store-a"
    elif change == "map":
        world.scenario.map_dataset.version = 2
    else:
        overrides["cursor"] = "invalid-json"
    with pytest.raises(BusinessRuleError, match="DISCOVERY_CURSOR_INVALID"):
        _query(world, **overrides)


def test_same_product_supplies_keep_distinct_window_identity_and_share_product_taste():
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
    (tmp_path / "places.json").write_text('{"locations":[{"location_id":"x"}]}')
    with pytest.raises(ValueError, match="activity kinds declaration"):
        retrieval.place_kinds(tmp_path, "places.json")


@pytest.mark.parametrize("field,value", [("price_fen", 999), ("price_version", "new"),
                                         ("current_stock", 1), ("opening_status", "open")])
def test_prompt_deduplication_keeps_changed_current_supply_facts(world, field, value):
    from worldmock.life.observe import _current_supply_facts
    from worldmock.life.result_projection import public_supply_row

    old = discovery.supply_row(world, world.scenario.supplies[0])
    event = {"action_id": "discovery", "result": {"data": {"tool": "discover_places",
                                                           "places": [{"supplies": [old]}]}}}
    current = public_supply_row(old)
    assert _current_supply_facts([current], [event]) == []
    current[field] = value
    assert _current_supply_facts([current], [event]) == [
        {"supply_id": current["supply_id"], "based_on_action_id": "discovery", "changes": {field: value}}]


def test_real_discovery_round_reinjects_only_returned_supplies_and_fits_model_budget(engine_factory):
    import json

    from agentsociety2.agent.base import ReactLoopRequest
    from custom.agents.life_agent import LifeAgent
    from tests.life.conftest import context, stage_day_plan, submit_round
    from worldmock.life.narration import USER_MESSAGE_CHAR_BUDGET

    engine = engine_factory()
    stage_day_plan(engine)
    request = DiscoverPlacesRequest(product_id=None, business_kind=None, travel_mode="walking",
                                     max_travel_seconds=None, cursor=None)
    before = engine.state.agents[1].visible_supply_ids.copy()
    receipts, results = submit_round(engine, {1: ("discover_places", request)}, "discover-real")
    assert receipts and results[0].status == "completed"
    data = results[0].data
    returned = {row["supply_id"] for place in data["places"] for row in place["supplies"]}
    assert before == set()
    assert engine.state.agents[1].visible_supply_ids == returned
    assert data["total"] > data["returned"] == engine.scenario.candidate_presentation_limit
    assert data["next_cursor"] is not None
    decision = context(engine, "discover-feedback")
    observation = engine.observe(decision)
    public = observation["facts"]["recent_action_results"][0]["discovery"]
    assert public["total"] == data["total"] and public["next_cursor"] == data["next_cursor"]
    assert {row["supply_id"] for place in public["places"] for row in place["supplies"]} == returned
    agent = LifeAgent()
    loop = ReactLoopRequest(0, decision.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                    "data": observation}])
    agent.prepare_react_loop(loop)
    messages = agent.build_react_messages(loop)
    user = next(row["content"] for row in messages if row["role"] == "user")
    assert len(user) <= USER_MESSAGE_CHAR_BUDGET
    assert "discover_places" in {item["function"]["name"] for item in agent.build_react_tool_schemas(readonly=False)}
    print(f"storefront model user characters={len(user)}, discovery evidence characters={len(json.dumps(data))}")


def test_every_real_discovery_page_is_usable_after_world_advances(engine_factory):
    from agentsociety2.agent.base import ReactLoopRequest
    from custom.agents.life_agent import LifeAgent
    from tests.life.conftest import advance_until_ready, context, stage_day_plan, submit_round
    from worldmock.life.narration import USER_MESSAGE_CHAR_BUDGET

    engine = engine_factory()
    stage_day_plan(engine)
    cursor, seen, sizes = None, set(), []
    for page in range(20):
        assert advance_until_ready(engine)
        request = DiscoverPlacesRequest(product_id=None, business_kind=None, travel_mode="walking",
                                         max_travel_seconds=None, cursor=cursor)
        _, results = submit_round(engine, {1: ("discover_places", request)}, f"discover-all-{page}")
        assert results[0].status == "completed"
        data = results[0].data
        ids = {row["supply_id"] for place in data["places"] for row in place["supplies"]}
        assert not seen & ids
        seen |= ids
        decision = context(engine, f"discover-all-feedback-{page}")
        observation = engine.observe(decision)
        current_ids = {row["supply_id"] for row in observation["facts"]["products"]}
        assert len(ids | current_ids) <= engine.scenario.candidate_presentation_limit
        assert engine._pending_visible_supplies[1] == ids | current_ids
        loop = ReactLoopRequest(0, decision.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                        "data": observation}])
        agent = LifeAgent()
        agent.prepare_react_loop(loop)
        messages = agent.build_react_messages(loop)
        user = next(row["content"] for row in messages if row["role"] == "user")
        sizes.append(len(user))
        assert len(user) <= USER_MESSAGE_CHAR_BUDGET
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    assert seen == {item.supply_id for item in engine.scenario.supplies}
    print(f"all discovery page model sizes={sizes}")


@pytest.mark.parametrize("mode", ["menu", "timed"])
def test_real_product_browse_returns_only_its_window_and_fits_model_budget(engine_factory, mode):
    from agentsociety2.agent.base import ReactLoopRequest
    from custom.agents.life_agent import LifeAgent
    from tests.life.conftest import advance_until_ready, context, stage_day_plan, submit_round
    from worldmock.life.models import BrowseActivitiesRequest, StartTargetedActivityRequest
    from worldmock.life.narration import USER_MESSAGE_CHAR_BUDGET

    engine = engine_factory()
    stage_day_plan(engine)
    if mode == "menu":
        request = BrowseActivitiesRequest(category="foods", cursor=None)
        tool = "browse_activities"
    else:
        request = StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                               planned_duration_seconds=60, playback_speed=1)
        tool = "start_targeted_activity"
    _, result = submit_round(engine, {1: (tool, request)}, f"browse-{mode}")
    assert result[0].error_code is None
    assert advance_until_ready(engine)
    decision = context(engine, f"browse-{mode}-feedback")
    observation = engine.observe(decision)
    results = [row["browse_result"] for row in observation["facts"]["recent_action_results"]
               if "browse_result" in row]
    assert len(results) == 1
    shown = {row["supply_id"] for row in results[0]["items"]}
    assert len(shown) == engine.scenario.candidate_presentation_limit
    assert engine.state.agents[1].visible_supply_ids == shown
    assert results[0]["total"] == len(engine.scenario.supplies)
    assert results[0]["next_cursor"] is not None
    agent = LifeAgent()
    loop = ReactLoopRequest(0, decision.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                    "data": observation}])
    agent.prepare_react_loop(loop)
    messages = agent.build_react_messages(loop)
    user = next(row["content"] for row in messages if row["role"] == "user")
    assert len(user) <= USER_MESSAGE_CHAR_BUDGET - 4000
    print(f"{mode} product browse model user characters={len(user)}")


@pytest.mark.parametrize("timed", [False, True])
def test_public_browse_exclusion_groups_restore_every_original_fact(timed):
    import json
    from collections import Counter
    from copy import deepcopy

    from worldmock.life.result_projection import public_action_result

    exclusions = [
        {"object_id": "a", "kind": "product", "reason": "ROTATION_DEFERRED", "stage": "rotation"},
        {"object_id": "b", "kind": "product", "reason": "ELIGIBILITY_OUT_OF_STOCK", "stage": "eligibility"},
        {"object_id": "c", "kind": "product", "reason": "ROTATION_DEFERRED", "stage": "rotation"},
        {"object_id": "d", "kind": "content", "reason": "ROTATION_DEFERRED", "stage": "rotation"},
        {"object_id": "e", "kind": "product", "reason": "ROTATION_DEFERRED", "stage": "rotation", "detail": 7},
    ]
    data = {"tool": "browse_activities", "category": "foods", "items": [], "excluded": exclusions}
    row = {"action_id": "browse", "occurred_at": NOW, "status": "completed", "error_code": None,
           "message": "Done", "result": {"data": {"browse_result": data} if timed else data}}
    original = deepcopy(row)
    groups = public_action_result(row)["browse_result"]["excluded"]
    restored = [{**{key: value for key, value in group.items() if key != "object_ids"}, "object_id": identity}
                for group in groups for identity in group["object_ids"]]
    assert len(groups) == 4
    assert Counter(json.dumps(item, sort_keys=True) for item in restored) == Counter(
        json.dumps(item, sort_keys=True) for item in exclusions)
    assert row == original


@pytest.mark.parametrize("status,missing", [("allowed", []), ("denied", ["member"])])
def test_public_arrival_preserves_location_access_and_source_without_activity_internals(status, missing):
    from worldmock.life.result_projection import public_action_result

    arrival = {"location_id": "store", "access_status": status, "missing_qualifications": missing,
               "source": _source().model_dump(mode="json"), "internal_detail": "do not publish"}
    row = {"action_id": "travel", "occurred_at": NOW, "status": "completed" if not missing else "failed",
           "error_code": None if not missing else "LOCATION_ACCESS_DENIED", "message": "Arrived",
           "result": {"data": {"activity": {"data": {"arrival": arrival}, "hidden": "private"}}}}
    public = public_action_result(row)
    assert public["arrival"] == {"location_id": "store", "access_status": status,
                                 "missing_qualifications": missing,
                                 "source": {"kind": "scenario", "reference": "https://example.test/scenario",
                                            "fact_cutoff": "2026-01-01"}}
    assert "activity" not in public
    assert public["error_code"] == row["error_code"]
    assert arrival["internal_detail"] == "do not publish"


def test_shared_observation_window_keeps_changed_returned_supply_and_only_fills_remaining_slots(world):
    from worldmock.life.candidates import Exclusion
    from worldmock.life.observe import _current_supply_facts, _shared_supply_window
    from worldmock.life.presentation import PresentationWindow
    from worldmock.life.result_projection import public_supply_row

    world.semantics.candidate_constants.presentation_limit = 2
    rows = [discovery.supply_row(world, item) for item in world.scenario.supplies]
    old = dict(rows[0])
    world.state.supply_quotes["supply-a"] = world.state.supply_quotes["supply-a"].model_copy(
        update={"price_fen": 999, "price_version": "changed"})
    world.state.supply_stock["supply-a"] = 0
    recent = [{"action_id": "discovery", "result": {"data": {"tool": "discover_places",
                                                            "places": [{"supplies": [old]}]}}}]
    window = PresentationWindow(items=tuple(rows[1:]), total=3, next_cursor=None,
                                 presented=("supply-b", "supply-u"),
                                 excluded=(Exclusion("supply-a", "product", "ELIGIBILITY_OUT_OF_STOCK"),))
    shared = _shared_supply_window(world, window, recent)
    assert shared.presented == ("supply-a", "supply-b")
    assert [(item.object_id, item.reason) for item in shared.excluded] == [("supply-u", "ROTATION_DEFERRED")]
    public = _current_supply_facts([public_supply_row(row) for row in shared.items], recent)
    difference = next(row for row in public if row["supply_id"] == "supply-a")
    changed = {**public_supply_row(old), **difference["changes"]}
    assert (changed["price_fen"], changed["price_version"], changed["current_stock"]) == (999, "changed", 0)
    assert {row["supply_id"] for row in public} == {"supply-a", "supply-b"}


@pytest.mark.parametrize("changes", [
    {"price_fen": 300, "price_version": "new"},
    {"price_fen": None, "price_version": None, "quote": None, "quote_source": None,
     "quote_status": "unknown", "purchase_allowed": False},
    {"source": {"kind": "fact", "reference": "https://example.test/new-source", "fact_cutoff": "2026-01-01"}},
    {"opening_status": "unknown", "current_stock": 0, "service_allowed": False},
    {"quote": {"effective_from": NOW, "effective_to": None},
     "quote_source": {"kind": "fact", "reference": "https://example.test/quote", "fact_cutoff": "2026-01-01"}},
])
def test_changed_supply_fact_can_restore_null_nested_source_price_opening_and_stock(world, changes):
    from copy import deepcopy

    from worldmock.life.observe import _current_supply_facts
    from worldmock.life.result_projection import public_supply_row

    raw = discovery.supply_row(world, world.scenario.supplies[0])
    original = deepcopy(raw)
    previous = public_supply_row(raw)
    current = {**previous, **changes}
    recent = [{"action_id": "known-supply", "result": {"data": {"tool": "browse_activities", "items": [raw]}}}]
    [difference] = _current_supply_facts([current], recent)
    assert difference["based_on_action_id"] == "known-supply"
    assert {**previous, **difference["changes"]} == current
    assert raw == original
    unseen = public_supply_row(discovery.supply_row(world, world.scenario.supplies[1]))
    assert _current_supply_facts([unseen], recent) == [unseen]


def test_real_bulk_quote_expiry_and_stock_change_preserves_complete_facts_within_budget(engine_factory):
    from agentsociety2.agent.base import ReactLoopRequest
    from custom.agents.life_agent import LifeAgent
    from tests.life.conftest import advance_until_ready, context, stage_day_plan, submit_round
    from tests.life.test_grounded_observation import _commit_test_state
    from worldmock.life.narration import USER_MESSAGE_CHAR_BUDGET
    from worldmock.life.result_projection import public_supply_row

    engine = engine_factory()
    stage_day_plan(engine)
    business = next(item.business_kind for item in engine.scenario.locations if item.location_id == "market")
    query = DiscoverPlacesRequest(product_id=None, business_kind=business, travel_mode="walking",
                                   max_travel_seconds=None, cursor=None)
    _, results = submit_round(engine, {1: ("discover_places", query)}, "bulk-before-change")
    previous = {row["supply_id"]: public_supply_row(row)
                for place in results[0].data["places"] for row in place["supplies"]}
    assert len(previous) == engine.scenario.candidate_presentation_limit
    staged = engine.state.model_copy(deep=True)
    for identity in previous:
        staged.supply_quotes[identity] = None
        staged.supply_stock[identity] = 0
    _commit_test_state(engine, staged, "bulk_quote_expiry_and_stock_change")
    assert advance_until_ready(engine)
    decision = context(engine, "bulk-changed-feedback")
    observation = engine.observe(decision)
    current = {row["supply_id"]: public_supply_row(row) for row in observation["supplies"]}
    assert len(observation["facts"]["products"]) == len(previous)
    for difference in observation["facts"]["products"]:
        identity = difference["supply_id"]
        assert difference["based_on_action_id"] == results[0].action_id
        assert {**previous[identity], **difference["changes"]} == current[identity]
        assert difference["changes"]["quote"] is None
        assert difference["changes"]["quote_source"] is None
        assert difference["changes"]["current_stock"] == 0
    agent = LifeAgent()
    loop = ReactLoopRequest(0, decision.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                    "data": observation}])
    agent.prepare_react_loop(loop)
    messages = agent.build_react_messages(loop)
    user = next(row["content"] for row in messages if row["role"] == "user")
    assert len(user) < USER_MESSAGE_CHAR_BUDGET - 4000
    assert "null 是该字段的当前值" in messages[0]["content"]
    print(f"bulk changed supply user characters={len(user)}")


def test_discovery_exposes_category_amount_without_rewriting_quote(world):
    """品类计价在比较行可见，来源报价与版本仍保持原始身份。"""
    from worldmock.categories import evaluate_purchase
    from worldmock.life.result_projection import public_supply_row

    world.semantics.purchase_pairing = lambda product: SimpleNamespace(state=PairingState.DEFINED, coefficient=0.335)
    world.semantics.purchase_amount = lambda product, price, quantity: evaluate_purchase(price, quantity, 0.335)
    row = _query(world)["places"][0]["supplies"][0]
    public = public_supply_row(row)
    assert row["quote"]["price_fen"] == row["price_fen"] == 100
    assert row["price_version"] == "v0"
    assert public["purchase_coefficient"] == 0.335
    assert public["purchase_price_fen"] == 34
    world.state.supply_quotes["supply-a"] = None
    unknown = _query(world)["places"][0]["supplies"][0]
    assert unknown["purchase_price_fen"] is None
    assert unknown["purchase_coefficient"] == 0.335


def test_no_effect_supply_remains_visible_without_a_purchase_amount(world):
    """已验证无作用的购买保留供给原报价，不伪装成免费且不阻止观察。"""
    world.semantics.purchase_pairing = lambda product: SimpleNamespace(state=PairingState.NO_EFFECT, coefficient=None)
    world.semantics.purchase_amount = lambda *args: pytest.fail("Inactive purchase must not be evaluated")
    row = _query(world)["places"][0]["supplies"][0]
    assert row["price_fen"] == 100 and row["quote"]["price_fen"] == 100
    assert row["purchase_pairing_state"] == "no_effect"
    assert row["purchase_coefficient"] is None and row["purchase_price_fen"] is None
    assert row["purchase_allowed"] is False
