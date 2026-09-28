"""门店供给通过可读对象和动作使用冻结报价。"""

from __future__ import annotations

import copy
import json
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
from tests.integration.test_life_agent_protocol import (
    _add_objects, _context, _observations, _offer, _visible_object,
)
from tests.life.conftest import (context, engine_factory as _engine_factory, stage_day_plan, start_arguments,
                                 start_request)
from worldmock.footprints.models import EventDraft
from worldmock.life.models import ActionResult
from worldmock.life.session_actions import SessionActionMixin


_OFFER = "家门口便利店出售的牛奶"
#: 取得方式短语 → 私有绑定的履约方式（docs/dev/SPEC-time-and-energy.md 第五章「请求形状与菜单」）。
_FULFILLMENT = {"到店": "pickup", "远程下单": "delivery"}


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


def _observed_offer(ways=("到店", "远程下单"), reason: str | None = None) -> list[dict]:
    """给真实人物准备可读供给（取得：到店、远程下单）与独立的引擎冻结报价；reason 是各取得方式不可用的原因。"""
    observed = copy.deepcopy(_observations())
    data = observed[0]["data"]
    _add_objects(data, _visible_object(
        _OFFER, "supply", "supply-private",
        *(_offer("obtain", way, "purchase", reason, fulfillment=_FULFILLMENT[way]) for way in ways),
        quote={"price_version": "price-private", "price_fen": 1000, "purchase_price_fen": 1000}))
    return observed


def _prepared_offer(ways=("到店", "远程下单")) -> LifeAgent:
    """从一次真实结构化观察准备 LifeAgent 工具合同。"""
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(
        0, _context().observed_at, _observed_offer(ways)))
    return person


def _functions(person: LifeAgent) -> dict[str, dict]:
    """读取将要发送给模型的工具 Schema。"""
    return {row["function"]["name"]: row["function"]
            for row in person.build_react_tool_schemas(readonly=False)}


def _obtain(target: str, way: str = "到店", **extra) -> ReactDecision:
    """对一个对象选择取得（obtain）及其方式的 start_activity 决定。"""
    return ReactDecision("", "start_activity", start_arguments([target], ["obtain"], [way], **extra), "")


def test_model_chooses_named_offer_and_fulfillment_without_echoing_quote():
    """购买模型入参只有对象、动词与方式；商品目标只在工具层，报价身份、履约方式和内部供给身份不进入请求。"""
    observed = _observed_offer()
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    tools = _functions(person)
    assert "purchase" not in tools and "discover_places" not in tools
    action = tools["start_activity"]["parameters"]
    assert set(action["properties"]) == {"targets", "actions", "ways", "from", "to", "answering"}
    user = person.build_react_messages(ReactLoopRequest(0, _context().observed_at, observed))[1]["content"]
    [target] = [row for row in json.loads(user)[0]["targets"] if row["name"] == _OFFER]
    assert target["kind"] == "product"
    assert [(item["action"], item["way"]) for item in target["actions"]] == [("obtain", "到店"), ("obtain", "远程下单")]
    for text in ("pickup", "delivery", "price-private", "supply-private"):
        assert text not in str(action) and text not in user
    for way in ("到店", "远程下单"):
        assert person.validate_react_decisions([_obtain(_OFFER, way)], False) == ""
    assert "not offered" in person.validate_react_decisions([
        ReactDecision("", "start_activity", start_arguments([_OFFER], ["consume"], ["到店"]), ""),
    ], False)
    assert "invalid start_activity arguments" in person.validate_react_decisions([
        _obtain(_OFFER, price_version="guessed"),
    ], False)


@pytest.mark.parametrize("label", ["未知商店出售的牛奶", "supply-private"])
def test_unoffered_supply_name_is_resolved_by_the_environment_not_the_host(label):
    """指名购买（docs/dev/SPEC-persona-prompt.md 第五章）：obtain 以到店或远程下单写本轮未列出的名称时宿主不按闭集拦截，
    交给环境按标签或商品名解析（私有编号既不是标签也不是商品名，环境以 SUPPLY_NOT_FOUND 拒绝，见
    tests/life/test_named_purchase.py）；其余动词写未列出的名称仍按协议错误纠正。"""
    person = _prepared_offer()
    _functions(person)
    for way in ("到店", "远程下单"):
        assert person.validate_react_decisions([_obtain(label, way)], False) == ""
    error = person.validate_react_decisions([
        ReactDecision("", "start_activity", start_arguments([label], ["consume"], ["食用"]), "")], False)
    assert "visible interactive object" in error


def test_host_checks_from_to_and_answering_against_private_bindings():
    """宿主按私有绑定本地校验来源、去向与 answering（与环境同一判定，docs/dev/SPEC-persona-prompt.md 第五章）：去向是
    「自己」、本轮的人物或不在身边的同世界人物（私有绑定 person_bindings），answering 是本轮可见的直接消息且至少一项送给
    发件人；不合法时返回英文协议错误原文。去向不是任何人物是拒绝码 RECIPIENT_NOT_FOUND：宿主放行，由环境业务拒绝。"""
    observed = _observed_offer()
    data = observed[0]["data"]
    _add_objects(data, _visible_object("联系人：王磊", "contact", "contact-wanglei", _offer("interact", "通话", "call")))
    data["person_bindings"] = [{"name": "人物：李凡", "person_id": "agent:2"}]
    data["message_bindings"] = [{"name": "王磊 10:35 的消息", "message_id": "reply-1", "sender_id": "contact-wanglei"},
                                {"name": "李凡 10:40 的消息", "message_id": "direct-2", "sender_id": "agent:2"}]
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))

    def error(**fields) -> str:
        """一次远程下单的本地校验结果（空串即通过）。"""
        return person.validate_react_decisions([_obtain(_OFFER, "远程下单", **fields)], False)

    assert error() == "" and error(to=["联系人：王磊"], answering="王磊 10:35 的消息") == ""
    assert error(to=["人物：李凡"], answering="李凡 10:40 的消息") == ""
    assert error(to=["联系人：陌生人"]) == ""
    assert error(answering="王磊 10:35 的消息") == (
        "start_activity answering needs an item whose to is the message sender")
    assert error(to=["联系人：王磊"], answering="王磊 9:00 的消息") == "start_activity answering is not a visible message"
    assert error(**{"from": ["家门口便利店"]}) == "start_activity from is null when the target already names its store"
    assert error(to=[None]) == "start_activity obtain and consume need to"


def test_supply_fulfillment_is_bound_per_visible_object():
    """仅有远程下单的供给不能被模型改成到店取得。"""
    person = _prepared_offer(("远程下单",))
    _functions(person)
    assert "not offered" in person.validate_react_decisions([_obtain(_OFFER, "到店")], False)
    assert person.validate_react_decisions([_obtain(_OFFER, "远程下单")], False) == ""


def test_closed_supply_remains_a_fact_without_a_purchase_action():
    """暂时不能购买的供给照常列为商品目标，取得动作标不可用并写原因（与私有绑定 note 同一文本）；宿主不查可用性，
    选它原样交给环境按同一原因业务拒绝（docs/dev/SPEC-persona-prompt.md 第一章「工具层」「列出即可提交」）。"""
    reason = "家门口便利店还没开门"
    observed = _observed_offer(("到店",), reason)
    data = observed[0]["data"]
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, _context().observed_at, observed))
    [target] = [row for row in data["tool_layer"]["targets"] if row["name"] == _OFFER]
    assert [(item["available"], item["reason"]) for item in target["actions"]] == [(False, reason)]
    assert data["interaction_bindings"][-1]["actions"][0]["note"] == reason
    assert "enum" not in _functions(person)["start_activity"]["parameters"]["properties"]["targets"]["items"]
    assert person.validate_react_decisions([_obtain(_OFFER)], False) == ""


def test_each_selected_supply_keeps_its_own_frozen_quote():
    """双件购买逐条取观察冻结的报价版本，并由引擎决定每件数量。"""
    class CapturePurchase(SessionActionMixin):
        def __init__(self):
            self.rules = SimpleNamespace(supplies={
                "supply-a": SimpleNamespace(location_id="home", fulfillment_modes=("pickup",)),
                "supply-b": SimpleNamespace(location_id="home", fulfillment_modes=("pickup",)),
            })
            self.scenario = self.semantics = SimpleNamespace()
            self.request = None
            self.named = None

        def _purchase(self, state, pending, request, named=frozenset()):
            self.request, self.named = request, named
            return state, ActionResult(action_id="a", agent_id=1, status="completed", occurred_at=0,
                                       message="Purchase accepted", data={"orders": []})

    runtime = CapturePurchase()
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, activities={})
    pending = SimpleNamespace(agent_id=1)
    bindings = [
        {"object_id": identity, "selected_action": {"fulfillment": "pickup"}, "recipient_id": "agent:1",
         "quote": {"price_version": version, "price_fen": price}}
        for identity, version, price in (("supply-a", "quote-a", 1000), ("supply-b", "quote-b", 1600))
    ]
    runtime._start_purchase_action(state, pending, bindings)
    assert [(item.supply_id, item.quantity, item.price_version) for item in runtime.request.items] == [
        ("supply-a", 1, "quote-a"), ("supply-b", 1, "quote-b"),
    ]
    assert runtime.named == frozenset()


def test_real_storefront_purchase_uses_observation_quote(_engine_factory):
    """真实引擎用冻结供给绑定购买，模型请求不含价格、数量和供给编号。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    _stage_open_market(engine)
    current = context(engine, "readable-storefront")
    engine.begin_decision_round([current])
    observed = engine.observe(current)
    offer, action = next((row, action) for row in observed["interaction_bindings"]
                         if row["kind"] == "supply" for action in row["actions"] if action["note"] is None)
    assert (action["verb"], action["canonical_action"]) == ("obtain", "purchase")
    assert action["label"] in {"到店", "远程下单"}
    request = start_request(targets=[offer["object"]], actions=["obtain"], ways=[action["label"]])
    assert set(request.model_dump()) == {"targets", "actions", "ways", "from", "to", "answering"}
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
    [target] = [row for row in observed["tool_layer"]["targets"] if row["name"] == offer["object"]]
    assert target["kind"] == "product"
    tool_layer = json.dumps(observed["tool_layer"], ensure_ascii=False)
    assert "supply_id" not in tool_layer and "price_version" not in tool_layer
