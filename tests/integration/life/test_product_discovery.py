"""@brief 途中长途商品资格由唯一配送规划决定，工具、索引和查询读取同一集合。"""

from dataclasses import replace

import pytest

from tests.catalog.conftest import edit_row, read_payload, write_payload
from tests.unit.catalog.test_supplement import add_stations
from tests.life.action_contract_world import contract_world
from tests.integration.life.test_content_select import registered_content_world
from tests.integration.life.test_world_query import query_payload
from worldmock.errors import IntegrityError
from worldmock.life.callable_index import callable_index
from worldmock.life.activity_control import visible_names
from worldmock.life.discovery import DiscoveryContext, product_bindings, product_rows
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.models import ActivityState, SessionState, WorldState
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.scenario import LocationReference
from worldmock.life.semantics import assemble_semantics
from worldmock.life.world_query import QueryWorldRequest, attach_world_query, query_world


def transit_context(catalog_dir, shipping):
    """@brief 独立目录、准入场景和严格出行状态构成纯观察上下文。"""
    add_stations(catalog_dir)
    if shipping is None:
        graph = read_payload(catalog_dir, "map.json")
        for edge in graph["edges"]:
            edge["length_m"] *= 10000
        write_payload(catalog_dir, "map.json", graph)
    else:
        edit_row(catalog_dir, "locations.json", "cafe", {"shipping_seconds": shipping})
    semantics, state = registered_content_world(catalog_dir)
    document = semantics.document.model_copy(update={"locations": (
        *semantics.document.locations, LocationReference(location_id="parcel"))})
    semantics = assemble_semantics(document, semantics.catalog)
    now = state.current_time
    state.activities["trip"] = ActivityState(
        activity_id="trip", agent_id=1, kind="travel", status="active", location_id="home", started_at=now,
        planned_end_at=now + 1000, last_settled_at=now, elapsed_seconds=0, planned_duration_seconds=1000,
        destination_id="office", travel_mode="walking", resource_claims={"travel"}, name="步行前往公司",
        origin_action_id="a", origin_decision_id="d", origin_request_event_id="r", session_ids=["stay-1"])
    state.sessions["stay-1"] = SessionState(session_id="stay-1", agent_id=1, kind="transit", status="active",
        origin_id="home", destination_id="office", started_at=now, name="Transit", activity_ids=["trip"])
    state = WorldState.model_validate(state.model_dump(mode="python"))
    layer, observation = contract_world()
    layer = replace(layer, state=state, scenario=semantics.scenario, semantics=semantics,
                    rules=LifeRuleEngine(semantics.scenario, semantics.catalog), bindings=[],
                    names=visible_names(state, 1, {}),
                    menu=resolve_interaction_menu(semantics.scenario, 1, "home"))
    return layer, observation


@pytest.mark.parametrize("shipping,expected", [(86401, {"chips", "lemon-tea"}),
                                             (86400, set()), (None, {"beef-noodles", "chips", "lemon-tea"})])
def test_explicit_and_graph_derived_delivery_share_exact_long_transit_boundary(catalog_dir, shipping, expected):
    layer, _ = transit_context(catalog_dir, shipping)
    before = layer.state.model_dump(mode="json")
    discovery = DiscoveryContext(layer.state, layer.scenario, layer.semantics, layer.state.agents[1])
    rows = product_rows(discovery)
    assert {row["goods_id"] for row in rows} == expected
    assert all(row["app_ids"] for row in rows)
    assert layer.state.model_dump(mode="json") == before


def test_transit_goods_index_query_and_private_bindings_use_identical_catalog_set(catalog_dir):
    layer, observation = transit_context(catalog_dir, 86401)
    discovery = DiscoveryContext(layer.state, layer.scenario, layer.semantics, layer.state.agents[1])
    expected = {row["name"] for row in product_rows(discovery)}
    assert expected == {"薯片", "柠檬茶"}
    assert {row["object"] for row in product_bindings(layer)} == expected
    assert {name for category in callable_index(layer)["goods"] for name in category["examples"]} == expected
    attach_world_query(observation, layer)
    result = query_world(observation, QueryWorldRequest.model_validate(query_payload(category=None, way="购买")))
    assert {row["name"] for row in result["matches"]} == expected
    assert result["total_count"] == 2


def test_delivery_planner_integrity_errors_are_not_swallowed_as_unavailable_products(catalog_dir, monkeypatch):
    from worldmock.life import basket

    layer, _ = transit_context(catalog_dir, 86401)
    def corrupt(*args):
        raise IntegrityError("Corrupted delivery graph")
    monkeypatch.setattr(basket, "plan_recipient_delivery", corrupt)
    discovery = DiscoveryContext(layer.state, layer.scenario, layer.semantics, layer.state.agents[1])
    with pytest.raises(IntegrityError, match="Corrupted delivery graph"):
        product_rows(discovery)
