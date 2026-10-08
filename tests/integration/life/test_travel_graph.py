"""@brief 图上出行的真实纯函数、活动身份及持久状态继续推进。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.catalog import load_catalog
from worldmock.catalog.affordances import engine_affordances
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.activity_validation import validate_activity_targets
from worldmock.life.models import ActivityState, ActivityStatus, AgentState, BodyState, WorldState
from worldmock.life.travel import (
    persisted_trip, plan_trip, planned_travel, record_arrival, require_travel_duration, travel_activity_data,
    update_travel,
)
from worldmock.life import sessions
from worldmock.life.models import ActivityStartParams, SessionKind, SessionStatus

ARTIFICIAL = Path(__file__).resolve().parents[3] / 'tests' / "fixtures/environment/catalog"


def travel_case(mode="walking"):
    """@brief 生产目录、严格世界模型和真实行程组成纯出行输入。"""
    catalog = load_catalog(ARTIFICIAL)
    trip = plan_trip(catalog.graph, "home", "office", mode)
    agent = AgentState(agent_id=1, name="Alice", location_id="home", balance_account_id="agent:1",
                       driving_days=3, last_driving_date=None,
                       body=BodyState(energy_kcal=500, sleep_pressure=0, sleep_deficit_hours=1,
                                      entertainment_need=0.5), body_settled_at=1000, next_decision_at=1000)
    activity = ActivityState(activity_id="travel-1", agent_id=1, location_id="home", kind="travel",
                             status="active", started_at=1000, planned_end_at=1000 + trip.duration_seconds,
                             last_settled_at=1000, elapsed_seconds=0, planned_duration_seconds=trip.duration_seconds,
                             resource_claims=engine_affordances().resource_claims("travel", travel_mode=mode),
                             destination_id="office", travel_mode=mode,
                             origin_action_id="action-1", origin_decision_id="decision-1",
                             origin_request_event_id="request-1", data=travel_activity_data(trip))
    state = WorldState(schema_version=11, version=1, current_time=1000, end_time=10000, run_id="pure-travel",
                       timezone="Asia/Shanghai", scenario_sha256="a" * 64,
                       agents={1: agent}, activities={activity.activity_id: activity},
                       accounts={"agent:1": 10000, "transport:service": 0}, random_seed=7, random_state="fixed")
    scenario = SimpleNamespace(locations=(catalog.locations["home"], catalog.locations["office"]),
                               agents=(), transport_account_id="transport:service")
    return catalog, scenario, state, trip


def request_for(trip, mode="walking", destination="office"):
    return ActivityStartParams(activity_type="travel", destination_id=destination, travel_mode=mode,
                               planned_duration_seconds=max(1, trip.duration_seconds))


@pytest.mark.parametrize("mode,seconds,fare", [("walking", 2729, 0), ("driving", 468, 655)])
def test_plan_uses_full_graph_fixed_speed_and_fare(mode, seconds, fare):
    catalog, scenario, state, trip = travel_case(mode)
    accepted, expected = planned_travel(scenario, SimpleNamespace(catalog=catalog), "home", request_for(trip, mode),
                                       state.current_time)
    assert accepted == trip and expected == seconds
    assert trip.fare_fen == fare and trip.distance_meters == 3274
    assert trip.path == ("home", "noodle-1", "office")
    assert trip.trip_id == f"home->office:{mode}"


def test_request_duration_is_recomputed_from_the_published_graph():
    catalog, scenario, _, trip = travel_case()
    semantics = SimpleNamespace(catalog=catalog)
    request = request_for(trip)
    assert require_travel_duration(scenario, semantics, "home", request, 1000) == trip
    with pytest.raises(BusinessRuleError, match="ROUTE_DURATION_MISMATCH"):
        require_travel_duration(scenario, semantics, "home",
                                request.model_copy(update={"planned_duration_seconds": trip.duration_seconds - 1}), 1000)


def test_same_place_travel_is_a_precondition_rejection():
    catalog, scenario, _, trip = travel_case()
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as failure:
        planned_travel(scenario, SimpleNamespace(catalog=catalog), "home", request_for(trip, destination="home"), 1000)
    assert failure.value.unmet == ["already_at_destination"]
    assert failure.value.public_reason == "You are already at this place."


def test_unselected_place_is_not_a_destination_but_remains_a_path_node():
    catalog, scenario, _, trip = travel_case()
    with pytest.raises(BusinessRuleError, match="DESTINATION_NOT_FOUND"):
        planned_travel(scenario, SimpleNamespace(catalog=catalog), "home",
                       request_for(trip, destination="noodle-1"), 1000)
    assert "noodle-1" in trip.path


def test_travel_activity_evidence_uses_trip_and_full_path():
    _, _, state, trip = travel_case()
    row = state.activities["travel-1"].model_dump(mode="json")
    assert row["location_id"] == "home" and row["destination_id"] == "office"
    assert row["data"] == {"trip_id": trip.trip_id, "path": ["home", "noodle-1", "office"],
                           "distance_meters": 3274, "origin_id": "home", "destination_id": "office"}
    assert "route_id" not in json.dumps(row) and "coordinate" not in json.dumps(row)


def test_travel_progress_snapshot_keeps_origin_and_arrival_is_verified():
    catalog, scenario, state, trip = travel_case()
    activity = state.activities["travel-1"].model_copy(update={"elapsed_seconds": 100,
                                                            "last_settled_at": 1100})
    progressed, agent = update_travel(activity, trip, state.agents[1])
    agent = agent.model_copy(update={"body_settled_at": 1100})
    assert agent.location_id == "home" and progressed.location_id == "home"
    assert progressed.route_progress_meters == pytest.approx(3274 * 100 / 2729)
    state = state.model_copy(update={"current_time": 1100, "activities": {"travel-1": progressed},
                                     "agents": {1: agent}})
    restored = WorldState.model_validate_json(state.model_dump_json())
    validate_activity_targets(restored, SimpleNamespace(graph=catalog.graph), scenario)
    resumed = persisted_trip(catalog.graph, restored.activities["travel-1"])
    assert resumed == trip and resumed.path == ("home", "noodle-1", "office")
    finished = restored.activities["travel-1"].model_copy(update={"elapsed_seconds": trip.duration_seconds,
        "last_settled_at": 1000 + trip.duration_seconds, "status": ActivityStatus.COMPLETED, "ended_at": 1000 + trip.duration_seconds})
    arrived, agent = update_travel(finished, resumed, restored.agents[1])
    record_arrival(arrived, agent)
    assert arrived.location_id == "home" and agent.location_id == "office"
    assert arrived.route_progress_meters == 3274
    assert arrived.data["arrival"] == {"location_id": "office", "access_status": "allowed"}


@pytest.mark.parametrize("field,value", [("trip_id", "wrong"), ("path", ["home", "office"]),
                                       ("distance_meters", 1), ("origin_id", "wrong-origin"),
                                       ("destination_id", "wrong-destination")])
def test_persisted_travel_evidence_drift_is_rejected(field, value):
    catalog, _, state, _ = travel_case()
    activity = state.activities["travel-1"]
    activity.data[field] = value
    with pytest.raises(IntegrityError, match="published graph"):
        persisted_trip(catalog.graph, activity)


@pytest.mark.parametrize("branch_labels", [False, True])
def test_tool_targets_list_all_other_selected_places_with_both_graph_quotes(tmp_path, branch_labels):
    from worldmock.life.rules import LifeRuleEngine
    from worldmock.life.interaction_menu import resolve_interaction_menu
    from worldmock.life.observe import ObservationBuilder, _route_catalog
    from worldmock.life.projection import ProjectionInputs, _interaction_binding
    from worldmock.life.readable import duration_phrase, money_phrase
    from worldmock.life.tool_layer import LayerContext, _binding_target
    from worldmock.life.world_setup import load_runtime_scenario

    path = ARTIFICIAL.parent / "scenarios/single-person.json"
    if branch_labels:
        from tests.integration.life.test_load_checks import _changed_locations

        def change(rows):
            rows["noodle-1"].update(name="面馆", branch_label="北店")
            rows["noodle-2"].update(name="面馆", branch_label="南店")

        path = _changed_locations(tmp_path, change)
    semantics, _ = load_runtime_scenario(path)
    _, _, state, _ = travel_case()
    state = state.model_copy(update={"activities": {}})
    agent = state.agents[1]
    scenario = semantics.scenario.model_copy(update={"agents": tuple(
        item.model_copy(update={"owns_car": True, "initial_driving_days": 3}) for item in semantics.scenario.agents)})
    from dataclasses import replace
    semantics = replace(semantics, scenario=scenario)
    catalog_rows = _route_catalog(scenario, state, 1, semantics)
    context = SimpleNamespace(scenario=scenario, state=state, semantics=semantics)
    builder = ObservationBuilder(context)
    route_rows = builder._route_rows(agent)
    projection = ProjectionInputs(state, scenario, agent, extras={"route_rows": route_rows})
    menu = resolve_interaction_menu(scenario, 1, agent.location_id)
    facts = LifeRuleEngine(scenario, semantics.catalog)
    layer = LayerContext(state, scenario, facts, semantics, 1, {"activities": {}}, [], menu)
    destinations = {item.location_id for item in scenario.locations} - {agent.location_id}
    assert len(destinations) == 6 and len(catalog_rows) == len(route_rows) == 12
    places = builder._reachable_places(agent)
    assert {row["location_id"] for row in places} == destinations
    assert all(set(row) == {"location_id", "name", "kind"} for row in places)
    names = {row["location_id"]: row["name"] for row in places}
    targets = []
    assert {(row["destination_id"], row["mode"]) for row in catalog_rows} == {
        (destination, mode) for destination in destinations for mode in ("walking", "driving")}
    for location in scenario.locations:
        row = {"kind": "location", "object_id": location.location_id, "name": location.display_name, "actions": []}
        binding = _interaction_binding(projection, row)
        actions = binding["actions"]
        if location.location_id == agent.location_id:
            assert actions == []
            continue
        assert {action["travel_mode"] for action in actions} == {"walking", "driving"}
        target = _binding_target(layer, binding)
        targets.append(target)
        assert target["state"] is None and target["name"] == f"地点：{names[location.location_id]}"
        for action, facts in zip(actions, target["actions"], strict=True):
            trip = plan_trip(semantics.catalog.graph, agent.location_id, location.location_id,
                             action["travel_mode"])
            assert facts["price"] == money_phrase(trip.fare_fen)
            assert facts["duration"] == duration_phrase(trip.duration_seconds)
            assert facts["note"].startswith("费力程度：")
            assert facts["available"] is True
            if action["travel_mode"] == "walking":
                assert facts["price"] == "0 元"
    assert len({target["name"] for target in targets}) == 6
    assert names["noodle-1"] != names["noodle-2"]
    if branch_labels:
        assert names["noodle-1"] == "面馆（北店）" and names["noodle-2"] == "面馆（南店）"


def test_minimum_length_driving_trip_is_quoted_accepted_and_initialized():
    from dataclasses import replace
    from worldmock.catalog import Graph
    from worldmock.catalog.admission import validate_map
    from worldmock.catalog.models import MapEdge, MapGraph
    from worldmock.life.activities import ActivityOrigin, start_activity
    from worldmock.life.interaction_menu import resolve_interaction_menu
    from worldmock.life.tool_layer import LayerContext, _travel_check, _travel_facts
    from worldmock.life.world_setup import load_runtime_scenario

    semantics, _ = load_runtime_scenario(ARTIFICIAL.parent / "scenarios/single-person.json")
    edges = [edge for edge in semantics.catalog.map_data.edges if (edge.a, edge.b) != ("home", "noodle-1")]
    edges.append(MapEdge(a="home", b="office", length_m=200))
    published = MapGraph.model_validate({**semantics.catalog.map_data.model_dump(),
                                        "edges": sorted(edges, key=lambda edge: (edge.a, edge.b))})
    validate_map(published, set(semantics.catalog.locations))
    catalog = replace(semantics.catalog, map_data=published, graph=Graph(published))
    scenario = semantics.scenario.model_copy(update={"agents": tuple(
        item.model_copy(update={"owns_car": True, "initial_driving_days": 3}) for item in semantics.scenario.agents)})
    semantics = replace(semantics, catalog=catalog, scenario=scenario)
    _, _, state, _ = travel_case()
    state = state.model_copy(update={"activities": {}})
    agent = state.agents[1]
    menu = resolve_interaction_menu(semantics.scenario, 1, agent.location_id)
    layer = LayerContext(state, semantics.scenario, None, semantics, 1, {}, [], menu)
    row, action = {"object_id": "office"}, {"travel_mode": "driving"}
    trip = plan_trip(catalog.graph, "home", "office", "driving")
    assert trip.path == ("home", "office") and trip.duration_seconds == 29 and trip.fare_fen == 500
    facts = _travel_facts(layer, row, action)
    assert facts["price"] == "5 元" and facts["duration"] == "不到 1 分钟"
    assert _travel_check(layer, row, action) is None
    request = request_for(trip, "driving")
    assert require_travel_duration(semantics.scenario, semantics, "home", request, state.current_time) == trip
    _, activity = start_activity(agent, request, state.current_time, ActivityOrigin("a", "d", "r"), semantics.body)
    activity.data.update(travel_activity_data(trip))
    assert activity.planned_duration_seconds == 29 and activity.planned_end_at == state.current_time + 29
    assert activity.resource_claims == engine_affordances().resource_claims("travel", travel_mode="driving")
    assert persisted_trip(catalog.graph, activity) == trip
    with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT"):
        start_activity(agent, ActivityStartParams(activity_type="wait", planned_duration_seconds=59),
                       state.current_time, ActivityOrigin("wait", "d", "r"), semantics.body)


def test_graph_departure_and_arrival_switch_real_sessions_without_engine():
    """@brief 真实世界模型的出发与到达转换可直接验证，完整引擎接线留给集成阶段。"""

    catalog, _, state, trip = travel_case()
    names = {identity: place.display_name for identity, place in catalog.locations.items()}
    state = sessions.open_stay(state, 1, names)
    state = sessions.begin_transit(state, 1, "office", names, opened_by_action_id="action-1")
    transit = sessions.current_session(state, 1)
    assert transit.kind == SessionKind.TRANSIT and transit.origin_id == "home"
    assert transit.destination_id == "office" and state.agents[1].location_id == "home"
    state = sessions.assign(state, transit.session_id, ["travel-1"])
    done_at = 1000 + trip.duration_seconds
    completed = state.activities["travel-1"].model_copy(update={"elapsed_seconds": trip.duration_seconds,
        "last_settled_at": done_at, "status": ActivityStatus.COMPLETED, "ended_at": done_at})
    activity, agent = update_travel(completed, trip, state.agents[1])
    record_arrival(activity, agent)
    agent = agent.model_copy(update={"body_settled_at": done_at})
    state = state.model_copy(update={"current_time": done_at, "activities": {"travel-1": activity}, "agents": {1: agent}})
    state, ended = sessions.end_transit(state, 1, names)
    assert ended == [] and sessions.current_session(state, 1).location_id == "office"
    assert state.sessions[transit.session_id].status == SessionStatus.ENDED
    assert state.activities["travel-1"].location_id == "home"

    assert WorldState.model_validate_json(state.model_dump_json()).agents[1].location_id == "office"
