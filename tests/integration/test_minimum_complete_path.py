"""最小完整路径：真实 LifeEnv、SQLite 与脚本化模型重现根因研究的代点场景（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章、
验收台账「最小完整路径」，docs/dev/SPEC-life-realism.md#spec-life-realism-产品行为）。

同事王磊是场景联系人（收货地点办公室）。人物在办公室邀他一起点外卖，王磊回复要一份；人物远程下单直接送给王磊并用
answering 回应那条消息：订单只有一个收货人、带 request_message_id，送达只入王磊的持有，人物的库存与摄入都不变；
其后的决策在进度层看到这笔订单（收货人与所回应的消息）与送达记录，再次为同一条消息下同一商品以 REQUEST_ALREADY_HANDLED
拒绝并指向原订单；再给王磊发消息时，联系人回复请求带双方往来与他实际收到的物品；人物离开办公室（这段停留结束）后，
之后的决策在记忆层的 settled 句里看到这笔订单（所回应的消息与收货人）与它的送达。
"""

from __future__ import annotations

import copy
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_life_agent_protocol import _ModelResponse  # noqa: E402
from tests.life.conftest import context, drive, stage_day_plan, start_arguments  # noqa: E402
from tests.life.routing_fixtures import run_until, stage  # noqa: E402
from tests.life.test_scheduling import _document, _identity, _write_scenario  # noqa: E402
from worldmock.life.activity_control import pending_conclusions  # noqa: E402
from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import active_activities  # noqa: E402
from worldmock.life.readable import clock_phrase, iso_time, money_phrase  # noqa: E402
from worldmock.runtime import settle_pending_model_replies  # noqa: E402

_WANGLEI = {"contact_id": "contact-wanglei", "salutation": "王磊", "relation": "同事", "closeness": 16,
            "channels": ["workplace-IM"], "reply_policy": "model", "receive_location_id": "office"}
_OFFICE_NAME = "张江高科技园区办公室（冻结场景锚点）"


class _ScriptedModel:
    """只替换远程模型传输：每次决策返回预定的一个世界请求，保存真正发送的请求。"""

    def __init__(self, tool: str, arguments: dict):
        self.requests: list[dict] = []
        self.response = _ModelResponse("provider-action", tool, json.dumps(arguments, ensure_ascii=False),
                                       reasoning_content="The person decides what to do next.")

    async def call(self, **request):
        """保存请求并返回提供商形状的响应。"""
        self.requests.append(copy.deepcopy(request))
        return self.response


class _ReplyClient:
    """联系人回复模型桩：王磊的回复正文按脚本给出，保存请求。"""

    model_name = "deepseek-flash"

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    async def call(self, request):
        """保存请求并返回一条回复。"""
        self.calls.append(request)
        return {"model": self.model_name, "choices": [{"message": {"content": self.text}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 12}}


def _engine(tmp_path) -> LifeEngine:
    """白领场景加同事联系人王磊；人物 10:30 在办公室，刚看过一件能送到办公室的集市食品，登记空计划。"""
    document = _document("white-collar-12-weeks.json")
    document["agents"][0]["contacts"].append(_WANGLEI)
    engine = LifeEngine.create(_write_scenario(document, tmp_path), tmp_path / "run", _identity(document, "path"))
    stage_day_plan(engine)
    now = engine.context.state.current_time + 10 * 3600 + 1800

    def change(state):
        """@brief 推到 10:30 并把人物放在办公室。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        agent = state.agents[1]
        agent.location_id, agent.body_settled_at, agent.next_decision_at = "office", now, now
    stage(engine, change)
    _browsed(engine)
    return engine


def _browsed(engine: LifeEngine) -> None:
    """人物此刻在办公室看过（或又看了一遍）同一件集市食品：它是工具层里唯一的商品目标。"""
    supply = next(item for item in engine.context.scenario.supplies if item.location_id == "market"
                  and "office" in item.delivery_destination_ids
                  and "eat" in engine.context.rules.items[item.product_id].use_modes)

    def change(state):
        """@brief 只留这一件供给的可见凭据。"""
        agent = state.agents[1]
        agent.visible_supply_ids = {supply.supply_id}
        agent.supply_browsed_at = {supply.supply_id: state.current_time}
        agent.supply_browsed_location = {supply.supply_id: agent.location_id}
    stage(engine, change)


def _ready(engine: LifeEngine) -> bool:
    """人物可以决策，且没有刚结束待收尾的活动。"""
    return engine.scheduling_state().ready_agent_ids == (1,) and not pending_conclusions(engine.context.state, 1)


def _person(environment, ctx, model) -> LifeAgent:
    """把人物绑定到真实环境、本轮宿主身份与脚本化模型。"""
    person = LifeAgent()
    person._id, person._name = ctx.agent_id, "陈曦"
    person._profile = {"id": ctx.agent_id, "name": "陈曦"}
    person._env, person._dispatcher, person._model_name = environment, model, "deepseek-flash"
    person._decision_context, person._max_react_turns, person._enable_memory = ctx, 3, False
    person.trace_span = lambda *args, **kwargs: nullcontext(SimpleNamespace(attributes={}))
    return person


async def _decide(environment, round_id: str, choose):
    """一次真实决策：观察、人物循环（脚本化模型按本轮观察给出唯一世界请求）、结算；返回观察、发给模型的请求与结果。"""
    engine = environment.engine
    assert engine.scheduling_state().ready_agent_ids == (1,)
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observation = await environment.observe(ctx)
    model = _ScriptedModel(*choose(observation))
    await _person(environment, ctx, model).run_react_loop(
        ReactLoopRequest(0, ctx.observed_at, [{"turn": 0, "action": "observe", "ok": True, "data": observation}]))
    [result] = engine.commit_decision_round(round_id)
    return observation, model.requests, result


def _events(observation: dict, kind: str) -> list[dict]:
    """观察进度层中某类记录。"""
    return [row for row in observation["progress_layer"]["events"] if row["type"] == kind]


def _order_for_wanglei(state: dict):
    """按观察中人物看到的内容下单：第一件能远程下单的商品，直接送给王磊，并回应王磊最近那条消息。"""
    def choose(observation: dict) -> tuple[str, dict]:
        """从工具层与进度层取名称（消息名是发件人加送达时刻）。"""
        target = next(row["name"] for row in observation["tool_layer"]["targets"] if row["kind"] == "product"
                      and any(action["way"] == "远程下单" for action in row["actions"]))
        message = [row for row in _events(observation, "message_arrival") if row["sender"] == "王磊"][-1]
        answering = f"王磊 {message['occurred_at'][11:16]} 的消息"
        state.update(target=target, answering=answering)
        return "start_activity", start_arguments([target], ["obtain"], ["远程下单"], **{'from': [None], 'to': ["联系人：王磊"], 'answering': answering, 'prefer': '最快拿到'})
    return choose


@pytest.mark.asyncio
async def test_requested_order_goes_to_the_colleague_and_is_not_placed_twice(tmp_path, monkeypatch) -> None:
    """请求 → 关联下单 → 实际送达王磊 → 后续决策看到订单与送达、不再重复下单（重复以 REQUEST_ALREADY_HANDLED
    拒绝）→ 联系人回复请求带往来与收到的物品。"""
    monkeypatch.setattr("worldmock.life.prompt_capacity.model_context_length", lambda _model: 131072)
    engine = _engine(tmp_path)
    environment = LifeEnv()
    environment.engine = engine
    _, _, invited = await _decide(environment, "invite", lambda _: ("send_message", {
        "recipient_name": "联系人：王磊", "text": "中午一起点外卖吗？"}))
    assert invited.status == "completed", (invited.error_code, invited.message)
    assert [item.text for item in engine.context.state.agents[1].outbox.values()] == ["中午一起点外卖吗？"]
    reply = _ReplyClient("好啊，帮我点一份，少辣，谢谢！")
    [settled] = drive(settle_pending_model_replies(engine, client=reply))
    payload = json.loads(reply.calls[0]["messages"][1]["content"])
    assert payload["history"] == [] and payload["received"] == []
    await _decide(environment, "wait", lambda _: ("start_activity", start_arguments(["自己"], ["interact"], ["等待"], **{'from': [None], 'to': [None], 'answering': None, 'prefer': None})))
    run_until(engine, lambda current: settled["reply_message_id"] in current.context.state.agents[1].inbox
              and _ready(current))

    chosen: dict = {}
    seen, requests, ordered = await _decide(environment, "order", _order_for_wanglei(chosen))
    assert "好啊，帮我点一份，少辣，谢谢！" in requests[0]["messages"][1]["content"]
    assert [row["name"] for row in seen["message_bindings"]] == [chosen["answering"]]
    assert ordered.status == "completed", (ordered.error_code, ordered.message)
    [order] = ordered.data["orders"]
    assert (order["agent_id"], order["recipient_id"], order["destination_id"]) == (1, "contact-wanglei", "office")
    assert order["request_message_id"] == settled["reply_message_id"]
    product = order["product_id"]
    energy = engine.context.state.agents[1].body.energy_kcal

    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered"
              and _ready(current))
    assert engine.context.state.contact_holdings == {"contact-wanglei": {product: 1.0}}
    assert all(product not in holdings for holdings in engine.context.state.agents[1].inventory.values())
    assert engine.context.state.agents[1].body.energy_kcal <= energy
    assert not [item for item in engine.context.state.activities.values() if item.kind.value == "consume"]

    _browsed(engine)
    later, requests, repeated = await _decide(environment, "reorder", _order_for_wanglei({}))
    [call] = [row for row in _events(later, "tool_call") if row["arguments"].get("answering") == chosen["answering"]
              and row["result"]["status"] == "completed"]
    name = engine.context.rules.items[product].name
    [purchase] = call["result"]["details"]["purchases"]
    assert (purchase["product"], purchase["recipient"], purchase["request"]) == (name, "王磊", chosen["answering"])
    [delivered] = _events(later, "delivery")
    assert delivered["message"] == f"已送到{_OFFICE_NAME}，收货人王磊" and delivered["status"] == "delivered"
    assert json.loads(requests[0]["messages"][1]["content"])[5] == later["progress_layer"]
    assert (repeated.status, repeated.error_code) == ("rejected", "REQUEST_ALREADY_HANDLED")
    linked_at = iso_time(order["expected_at"], engine.context.scenario.timezone)
    assert repeated.data["linked_orders"] == [{"order": f"{name} 1 份（10:35 远程下单）", "expected_at": linked_at}]
    expected = clock_phrase(order["expected_at"], engine.context.state.current_time, engine.context.scenario.timezone)
    assert len(engine.context.state.deliveries) == 1 and engine.context.state.contact_holdings["contact-wanglei"] == {product: 1.0}

    run_until(engine, _ready)
    _, _, asked = await _decide(environment, "ask", lambda _: ("send_message", {
        "recipient_name": "联系人：王磊", "text": "外卖送到了吗？"}))
    assert asked.status == "completed"
    follow = _ReplyClient("收到了，谢谢！")
    drive(settle_pending_model_replies(engine, client=follow))
    payload = json.loads(follow.calls[0]["messages"][1]["content"])
    assert [(item["speaker"], item["text"]) for item in payload["history"]] == [
        ("陈曦", "中午一起点外卖吗？"), ("王磊", "好啊，帮我点一份，少辣，谢谢！")]
    assert payload["received"] == [{"item": name, "quantity": "1 份", "time": expected}]
    assert payload["incoming_message"] == "外卖送到了吗？"

    run_until(engine, _idle)
    await _decide(environment, "leave", _walk_home)
    run_until(engine, lambda current: current.context.state.agents[1].location_id == "home" and _idle(current))
    home, requests, _ = await _decide(environment, "home", lambda _: ("start_activity", start_arguments(["自己"], ["interact"], ["等待"], **{'from': [None], 'to': [None], 'answering': None, 'prefer': None})))
    assert not _events(home, "delivery") and not [row for row in _events(home, "tool_call")
                                                  if row["arguments"].get("answering")]
    settled_lines = [line for entry in home["memory_layer"]["earlier_today"] for line in entry["settled"]]
    placed = (f"10:35 远程下单{name} 1 份，付 {money_phrase(order['total_price_fen'])}，预计 {expected} "
              f"送到{_OFFICE_NAME}（回应{chosen['answering']}，收货人王磊）")
    assert placed in settled_lines
    assert f"{expected} {name} 1 份（10:35 远程下单）：已送到{_OFFICE_NAME}，收货人王磊" in settled_lines
    assert json.loads(requests[0]["messages"][1]["content"])[2] == home["memory_layer"]
    assert len(engine.context.state.deliveries) == 1


def _idle(engine: LifeEngine) -> bool:
    """人物可以决策，且没有进行中或待收尾的活动（可以出发）。"""
    return _ready(engine) and not active_activities(engine.context.state, 1)


def _walk_home(observation: dict) -> tuple[str, dict]:
    """步行回住处（离开办公室，这段停留随出发结束）。"""
    home = next(row["name"] for row in observation["tool_layer"]["targets"] if row["kind"] == "location"
                and "张江居所" in row["name"])
    return "start_activity", start_arguments([home], ["travel"], ["步行"], **{'from': [None], 'to': [None], 'answering': None, 'prefer': None})
