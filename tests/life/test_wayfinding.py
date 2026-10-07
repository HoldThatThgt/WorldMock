"""@brief 连通登记图的去向提示与实际出行工具报价。"""

from types import SimpleNamespace

import pytest

from tests.catalog.test_graph import hand_graph
from tests.life.test_travel_graph import travel_case
from worldmock.life.discovery import DiscoveryContext, route_row
from worldmock.life.wayfinding import go_step


@pytest.mark.parametrize("target,name", [("a", "此地"), ("b", "中间地点"), ("c", "门店")])
def test_go_step_directly_names_the_destination_even_for_a_multi_hop_path(target, name):
    graph = hand_graph([("a", "b", 200), ("b", "c", 300)])
    assert go_step(graph, {"a": "此地", "b": "中间地点", "c": "门店"}, "a", target) == f"Go to {name}."


@pytest.mark.parametrize("mode,seconds,fare", [("walking", 2729, 0), ("driving", 468, 655)])
def test_discovery_quotes_the_same_graph_trip_without_old_route_fields(mode, seconds, fare):
    catalog, scenario, state, trip = travel_case(mode)
    context = DiscoveryContext(state, scenario, SimpleNamespace(catalog=catalog), state.agents[1])
    row = route_row(context, "office", mode)
    assert row == {"trip_id": trip.trip_id, "path": list(trip.path), "origin_id": "home",
                   "destination_id": "office", "mode": mode, "distance_meters": 3274,
                   "duration_seconds": seconds, "fare_fen": fare}


def test_retired_wayfinding_functions_are_physically_removed():
    from worldmock.life import wayfinding

    assert not {"reachable_places", "shortest_paths", "delivery_route_code", "delivery_step",
                "UNREACHABLE_STORE_STEP", "UNREACHABLE_DELIVERY_STEP"} & set(vars(wayfinding))
