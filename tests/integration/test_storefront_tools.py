"""门店发现和供给购买在真实 LifeAgent 工具 Schema 与宿主派发边界的一致性。"""

import copy
import os
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision
from custom.agents import life_menu
from tests.integration.test_grounded_targets import _catalogs, _context, _Environment, _functions, _prepare
from tests.integration.test_interaction_menu import _menu
from worldmock.errors import BusinessRuleError
from worldmock.life import menu_execution
from worldmock.life.interaction_menu import InteractionMenu
from worldmock.life.models import DiscoverPlacesRequest


def _supply(identity="supply-a", *, place="home", seller="seller", version="v1"):
    return {"supply_id": identity, "product_id": "product-visible", "location_id": place,
            "seller_account_id": seller, "price_version": version, "price_fen": 1000,
            "current_stock": 2, "quote_status": "known", "opening_status": "open",
            "access_allowed": True, "service_allowed": True, "purchase_allowed": True,
            "pickup_allowed": place == "home", "fulfillment_modes": ["pickup", "delivery"],
            "delivery_destination_ids": ["home"]}


def _agent(*, supplies=None, overrides=None):
    data = {"supplies": supplies if supplies is not None else [_supply()],
            "discovery_catalog": {"product_ids": ["product-visible"], "business_kinds": ["convenience"]},
            "locations": [{"location_id": place} for place in ("home", "office", "store", "other")],
            "routes": [{"destination_id": "office", "mode": "driving", "duration_seconds": 216,
                        "minimum_energy_cost_kcal": 6.0, "energy_feasible": True, "status": "reachable"}],
            **(overrides or {})}
    return _prepare(_catalogs(), overrides=data)


def _discover(**overrides):
    return {"product_id": None, "business_kind": None, "travel_mode": "walking",
            "max_travel_seconds": None, "cursor": None, **overrides}


def _purchase(ids=("supply-a",), **overrides):
    return {"items": [{"supply_id": identity, "quantity": 1, "price_version": "v1"} for identity in ids],
            "fulfillment": "delivery", "destination_id": "home", **overrides}


def test_real_schema_uses_supply_id_and_nullable_discovery_filters():
    agent = _agent()
    functions = _functions(agent)
    purchase = functions["purchase"]["parameters"]
    props = purchase["properties"]["items"]["items"]["properties"]
    assert props["supply_id"]["enum"] == ["supply-a"] and "product_id" not in props
    discovery = functions["discover_places"]["parameters"]
    assert set(discovery["required"]) == {"product_id", "business_kind", "travel_mode", "max_travel_seconds", "cursor"}
    Draft202012Validator(discovery).validate(_discover())
    Draft202012Validator(discovery).validate(_discover(product_id="product-visible"))
    invalid = _discover(product_id="unknown-product")
    assert list(Draft202012Validator(discovery).iter_errors(invalid))
    assert agent.validate_react_decisions([ReactDecision("", "discover_places", invalid, "")], False)


@pytest.mark.parametrize("supplies,payload,error", [
    ([_supply()], _purchase(("supply-a", "supply-a")), "unique"),
    ([_supply(), _supply("supply-b", place="other")], _purchase(("supply-a", "supply-b")), "one location"),
    ([_supply(), _supply("supply-b", seller="other")], _purchase(("supply-a", "supply-b")), "seller account"),
    ([_supply()], _purchase(("product-visible",)), "visible pair"),
    ([_supply()], _purchase(destination_id="office"), "delivery scope"),
    ([_supply(place="store")], _purchase(fulfillment="pickup", destination_id="store"), "not supported"),
])
def test_local_validation_preserves_supply_seller_location_and_fulfillment_pairs(supplies, payload, error):
    agent = _agent(supplies=supplies)
    rejected = agent.validate_react_decisions([ReactDecision("", "purchase", payload, "")], False)
    assert error in rejected


def test_closed_unknown_and_unqualified_supply_remain_facts_but_not_purchase_contracts():
    rows = []
    for field, value in (("opening_status", "closed"), ("access_allowed", False), ("service_allowed", False)):
        row = _supply(field)
        row.update({field: value, "purchase_allowed": False})
        rows.append(row)
    unknown = _supply("unknown")
    unknown.update(quote_status="unknown", price_fen=None, price_version=None, purchase_allowed=False)
    rows.append(unknown)
    original = copy.deepcopy(rows)
    assert life_menu.visible_supply_contracts({"supplies": rows, "locations": [{"location_id": "home"}]}) == {}
    assert rows == original
    assert "purchase" not in _functions(_agent(supplies=rows))


@pytest.mark.parametrize("overrides,error", [
    ({"supplies": None}, "supplies catalog must be a list"),
    ({"supplies": [None]}, "supply must be an object"),
    ({"supplies": [{**_supply(), "supply_id": ""}]}, "supply identity is invalid"),
    ({"supplies": [_supply(), _supply()]}, "supply identities are duplicated"),
    ({"supplies": [{**_supply(), "location_id": "undeclared"}]}, "supply references an unknown location"),
    ({"supplies": [{**_supply(), "delivery_destination_ids": ["undeclared"]}]},
     "supply references an unknown location"),
    ({"locations": [{"location_id": ""}]}, "location identities are invalid"),
    ({"supplies": [{**_supply(), "current_stock": True}]}, "supply stock is invalid"),
    ({"supplies": [{**_supply(), "quote_status": "missing"}]}, "supply quote status is invalid"),
    ({"supplies": [{**_supply(), "price_version": None}]}, "known supply quote is invalid"),
    ({"supplies": [{**_supply(), "price_fen": None}]}, "known supply quote is invalid"),
    ({"supplies": [{**_supply(), "quote_status": "unknown", "price_version": None}]},
     "unknown supply quote contains a price"),
    ({"supplies": [{**_supply(), "quote_status": "unknown", "price_fen": None}]},
     "unknown supply quote contains a price"),
    ({"supplies": [{**_supply(), "access_allowed": None}]}, "supply qualification is invalid"),
    ({"supplies": [{**_supply(), "fulfillment_modes": []}]}, "supply fulfillment is invalid"),
    ({"supplies": [{**_supply(), "delivery_destination_ids": None}]}, "supply fulfillment is invalid"),
    ({"supplies": [{**_supply(), "opening_status": "unsure"}]}, "supply opening status is invalid"),
    ({"supplies": [{**_supply(), "current_stock": 0}]}, "supply purchase qualification is inconsistent"),
    ({"discovery_catalog": None}, "discovery catalog must be an object"),
    ({"discovery_catalog": {"product_ids": None, "business_kinds": []}}, "discovery catalog values are invalid"),
    ({"discovery_catalog": {"product_ids": [7], "business_kinds": []}}, "discovery catalog values are invalid"),
    ({"discovery_catalog": {"product_ids": [], "business_kinds": [""]}}, "discovery catalog values are invalid"),
])
def test_invalid_supply_or_discovery_observation_stops_real_agent_preparation(overrides, error, caplog):
    """不完整或矛盾的世界观察必须终止宿主准备，不能转成可调用购买或发现工具。"""
    original = copy.deepcopy(overrides)
    with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match=error):
        _agent(overrides=overrides)
    assert error in caplog.text
    assert overrides == original


def test_remote_pickup_only_supply_has_no_executable_purchase_contract():
    """已知供给在远处且只支持自提时保留事实，但不能生成配送或远程自提合同。"""
    row = {**_supply(place="store"), "fulfillment_modes": ["pickup"], "delivery_destination_ids": []}
    original = copy.deepcopy(row)
    data = {"supplies": [row], "locations": [{"location_id": "home"}, {"location_id": "store"}]}
    assert life_menu.visible_supply_contracts(data) == {}
    agent = _agent(supplies=[row])
    assert "purchase" not in _functions(agent)
    assert agent.validate_react_decisions([ReactDecision("", "purchase", _purchase(), "")], False)
    assert row == original


def test_unknown_route_cannot_become_a_travel_schema_choice():
    agent = _agent(overrides={"routes": [{"destination_id": "office", "mode": "walking",
                                         "status": "unknown", "duration_seconds": None,
                                         "reason": "profile_not_verified"}]})
    assert "start_travel" not in _functions(agent)
    assert "discover_places" in _functions(agent)


@pytest.mark.asyncio
async def test_discovery_is_one_pending_life_action_not_a_synchronous_query():
    agent = _agent()
    environment = _Environment()
    agent._env, agent._decision_context = environment, _context()
    result = await agent.dispatch_react_tool("discover_places", _discover())
    assert len(environment.calls) == 1
    call = environment.calls[0]
    assert call.tool_name == "discover_places" and isinstance(call.request, DiscoverPlacesRequest)
    assert "places" not in str(result.data)
    error = agent.validate_react_decisions([ReactDecision("", "purchase", _purchase(), "")], False)
    assert "already submitted" in error


def test_menu_denies_discovery_in_schema_local_validation_and_environment(monkeypatch):
    declared = _menu(tools={"purchase"}, kinds={"consume"})
    agent = _agent(overrides={"interaction_menu": declared})
    assert "discover_places" not in _functions(agent)
    assert agent.validate_react_decisions([ReactDecision("", "discover_places", _discover(), "")], False)
    menu = InteractionMenu.model_validate(declared)
    monkeypatch.setattr(menu_execution, "resolve_interaction_menu", lambda *args: menu)
    state = SimpleNamespace(agents={1: SimpleNamespace(agent_id=1, location_id="home")}, activities={})
    pending = SimpleNamespace(agent_id=1, tool_name="discover_places")
    with pytest.raises(BusinessRuleError, match="INTERACTION_MENU_DENIED"):
        menu_execution.require_interaction_menu(None, state, pending, DiscoverPlacesRequest(**_discover()))
