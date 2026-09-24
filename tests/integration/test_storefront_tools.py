"""门店供给通过可读对象和动作使用冻结报价。"""

from __future__ import annotations

import copy
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent
from tests.integration.test_life_agent_protocol import _context, _observations
from tests.life.conftest import context, engine_factory as _engine_factory, stage_day_plan
from worldmock.life.models import StartActivityRequest
from worldmock.footprints.models import EventDraft
from worldmock.life.session_actions import SessionActionMixin


_OFFER = "家门口便利店出售的牛奶"


def _stage_open_market(engine) -> None:
    """把隔离运行推进到营业时段，并冻结每条可见供给。"""
    now = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[1]
    visible = {row.supply_id for row in engine.scenario.supplies}
    staged.agents[1] = agent.model_copy(update={
        "visible_supply_ids": visible,
        "supply_browsed_at": {identity: now for identity in visible},
        "supply_browsed_location": {identity: agent.location_id for identity in visible},
        "next_decision_at": now,
        "body_settled_at": now,
    }, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now,
        visibility="system", data={"kind": "test_open_market"},
    )])
    engine.state = staged


def _observed_offer(action_labels=("到店购买", "配送到当前位置")) -> list[dict]:
    """给真实人物准备可读供给与独立的引擎冻结报价。"""
    observed = copy.deepcopy(_observations())
    data = observed[0]["data"]
    data["facts"]["interactive_objects"].append({
        "object": _OFFER, "actions": list(action_labels), "preconditions_met": True,
    })
    data["facts"]["products"] = [{
        "object": _OFFER, "product": "牛奶", "place": "家门口便利店",
        "price_fen": 1000, "quote_status": "known", "current_stock": 2,
        "purchase_allowed": True, "fulfillment_modes": ["pickup", "delivery"],
        "delivery_destinations": ["自己家"], "delivery_delay_seconds": 300,
    }]
    data["interaction_bindings"].append({
        "object": _OFFER, "kind": "supply", "object_id": "supply-private",
        "actions": [
            {"label": "到店购买", "canonical_action": "purchase", "fulfillment": "pickup"},
            {"label": "配送到当前位置", "canonical_action": "purchase", "fulfillment": "delivery"},
        ],
        "quote": {"price_version": "price-private", "price_fen": 1000, "purchase_price_fen": 1000},
    })
    return observed


def _prepared_offer(action_labels=("到店购买", "配送到当前位置")) -> LifeAgent:
    """从一次真实结构化观察准备 LifeAgent 工具合同。"""
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(
        0, _context().observed_at, _observed_offer(action_labels)))
    return person


def _functions(person: LifeAgent) -> dict[str, dict]:
    """读取将要发送给模型的工具 Schema。"""
    return {row["function"]["name"]: row["function"]
            for row in person.build_react_tool_schemas(readonly=False)}


def test_model_chooses_named_offer_and_fulfillment_without_echoing_quote():
    """购买模型入参只有对象与可读动作，报价和内部供给身份不进入 Schema。"""
    person = _prepared_offer()
    tools = _functions(person)
    assert "purchase" not in tools and "discover_places" not in tools
    action = tools["start_activity"]["parameters"]
    assert set(action["properties"]) == {"targets", "actions"}
    assert _OFFER in action["properties"]["targets"]["items"]["enum"]
    assert {"到店购买", "配送到当前位置"} <= set(action["properties"]["actions"]["items"]["enum"])
    assert person.validate_react_decisions([
        ReactDecision("", "start_activity", {"targets": [_OFFER], "actions": ["到店购买"]}, ""),
    ], False) == ""
    assert "invalid start_activity arguments" in person.validate_react_decisions([
        ReactDecision("", "start_activity", {
            "targets": [_OFFER], "actions": ["到店购买"], "price_version": "guessed",
        }, ""),
    ], False)


@pytest.mark.parametrize("label", ["未知商店出售的牛奶", "supply-private"])
def test_unoffered_or_internal_supply_name_cannot_be_selected(label):
    """模型不能猜未呈现供给，也不能用私有编号绕过可读对象闭集。"""
    person = _prepared_offer()
    _functions(person)
    error = person.validate_react_decisions([
        ReactDecision("", "start_activity", {"targets": [label], "actions": ["到店购买"]}, ""),
    ], False)
    assert "visible interactive object" in error


def test_supply_fulfillment_is_bound_per_visible_object():
    """仅有配送动作的供给不能被模型改成到店自提。"""
    person = _prepared_offer(("配送到当前位置",))
    _functions(person)
    error = person.validate_react_decisions([
        ReactDecision("", "start_activity", {"targets": [_OFFER], "actions": ["到店购买"]}, ""),
    ], False)
    assert "not offered" in error


def test_closed_supply_remains_a_fact_without_a_purchase_action():
    """不可购买的报价事实仍可见，但本轮不产生可调用对象。"""
    observed = _observed_offer()
    data = observed[0]["data"]
    data["facts"]["products"][0]["purchase_allowed"] = False
    data["facts"]["interactive_objects"] = [
        row for row in data["facts"]["interactive_objects"] if row["object"] != _OFFER
    ]
    data["interaction_bindings"] = [
        row for row in data["interaction_bindings"] if row["object"] != _OFFER
    ]
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    _functions(person)
    assert data["facts"]["products"][0]["object"] == _OFFER
    assert "visible interactive object" in person.validate_react_decisions([
        ReactDecision("", "start_activity", {"targets": [_OFFER], "actions": ["到店购买"]}, ""),
    ], False)


def test_each_selected_supply_keeps_its_own_frozen_quote():
    """双件购买逐条取观察冻结的报价版本，并由引擎决定每件数量。"""
    class CapturePurchase(SessionActionMixin):
        def __init__(self):
            self.rules = SimpleNamespace(supplies={
                "supply-a": SimpleNamespace(location_id="home", fulfillment_modes=("pickup",)),
                "supply-b": SimpleNamespace(location_id="home", fulfillment_modes=("pickup",)),
            })
            self.request = None

        def _purchase(self, state, pending, request):
            self.request = request
            return state, SimpleNamespace(status="completed")

    runtime = CapturePurchase()
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")})
    pending = SimpleNamespace(agent_id=1)
    bindings = [
        {"object_id": identity, "selected_action": {"fulfillment": "pickup"},
         "quote": {"price_version": version, "price_fen": price}}
        for identity, version, price in (("supply-a", "quote-a", 1000), ("supply-b", "quote-b", 1600))
    ]
    runtime._start_purchase_action(state, pending, bindings)
    assert [(item.supply_id, item.quantity, item.price_version) for item in runtime.request.items] == [
        ("supply-a", 1, "quote-a"), ("supply-b", 1, "quote-b"),
    ]


def test_real_storefront_purchase_uses_observation_quote(_engine_factory):
    """真实引擎用冻结供给绑定购买，模型请求不含价格、数量和供给编号。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _stage_open_market(engine)
    current = context(engine, "readable-storefront")
    engine.begin_decision_round([current])
    observed = engine.observe(current)
    offer = next(row for row in observed["interaction_bindings"]
                 if row["kind"] == "supply" and row["actions"])
    action = offer["actions"][0]["label"]
    request = StartActivityRequest(targets=[offer["object"]], actions=[action])
    assert set(request.model_dump()) == {"targets", "actions"}
    before_stock = engine.state.supply_stock[offer["object_id"]]
    account = engine.state.agents[current.agent_id].balance_account_id
    before_balance = engine.state.accounts[account]
    engine.submit_action(DirectToolCall(current, "purchase-readable", "start_activity", request))
    result = engine.commit_decision_round(current.round_id)[0]
    assert result.status.value == "completed"
    assert result.error_code is None
    assert result.data["total_price_fen"] == offer["quote"]["purchase_price_fen"]
    assert engine.state.supply_stock[offer["object_id"]] == before_stock - 1
    assert engine.state.accounts[account] == before_balance - result.data["total_price_fen"]
    assert offer["object"] in {row["object"] for row in observed["facts"]["products"]}
    assert all("supply_id" not in row and "price_version" not in row
               for row in observed["facts"]["products"])
