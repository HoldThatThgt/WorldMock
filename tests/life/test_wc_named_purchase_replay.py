"""WC 白领 18:15 的三次指名购买离线重放（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-指名购买from-是约束不是语法 「按此设计，WC 18:15 的三次请求都会被
受理」、3.3「以 WC 18:15 走一遍」；验收台账「指名购买」）。

tests/fixtures/dayrun_wc_named_purchase.json 从 4a5b0e6 白领全天运行（WorldMock-Dataset 的 0001-white-collar-lianqiu
runs/2026-09-28-4a5b0e6-day）摘出：三次 start_activity 的原始请求（模型响应 #882、#890、#994）、当时的所在地（家）与最小的
场景事实——手机配置里卖家 merchant:grocery → 盒马（称呼「盒马App」「盒马」），门店「模拟生鲜出货点」，番茄与鸡蛋只远程下单、
只送到家、备货 30 分钟。重放时把名称与履约事实落到白领场景的隔离副本上，保留正式定价声明产生的冻结报价：集市改名
「模拟生鲜出货点」，集市的两件食品改名「番茄」「鸡蛋」，钉定一份只含盒马的手机配置（渠道经真实加载路径派生）。夹具使用
当前严格逐项请求、空值表示本人、购买偏好为最快拿到。宿主本地校验与真实引擎都受理，经 App 的单记 channel_id「hema」。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from tests.life.conftest import stage_day_plan  # noqa: E402
from tests.life.purchase_worlds import ROOT, SCENARIO, at_clock, build_world, decide, phone_config  # noqa: E402
from tests.life.routing_fixtures import place  # noqa: E402
from worldmock.life.basket import delivery_expected_at  # noqa: E402

_FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "dayrun_wc_named_purchase.json").read_text(
    encoding="utf-8"))
#: 白领场景里承载 WC 事实的对象：集市（改名为 WC 的门店）与集市的两件食品（改名为番茄、鸡蛋）。
_STORE = "market"
_PRODUCTS = {"番茄": "food-20917906", "鸡蛋": "food-4891028705949"}


@pytest.fixture(scope="module")
def wc_world(tmp_path_factory):
    """落了 WC 事实的隔离世界（语料、地图、目录与手机配置都按真实准入重新钉扎）。"""
    channel, goods = _FIXTURE["channel"], {row["name"]: row for row in _FIXTURE["goods"]}
    document = json.loads(SCENARIO.read_text(encoding="utf-8"))
    dataset = json.loads((ENVIRONMENT_ROOT / "corpora" / document["map_dataset"]["path"]).read_text(encoding="utf-8"))
    published = json.loads((ENVIRONMENT_ROOT / "corpora" / dataset["supplies_file"]["path"]).read_text(encoding="utf-8"))["supplies"]
    names = {product: name for name, product in _PRODUCTS.items()}
    # 所有同商品供给仅送到家，保持办公室没有可行替代候选的原始场景前提。
    supplies = {row["supply_id"]: {
        "fulfillment_modes": goods[name]["fulfillment_modes"],
        "delivery_destination_ids": goods[name]["delivery_destination_ids"],
        "delivery_delay_seconds": goods[name]["delivery_delay_seconds"]}
        for row in published if (name := names.get(row["product_id"])) is not None}
    return build_world(
        tmp_path_factory.mktemp("wc-world"),
        {"wc": phone_config({channel["seller_account_id"]: channel["channel_id"]},
                            {channel["channel_id"]: channel["names"]})},
        places={_STORE: _FIXTURE["store"]["name"]}, supplies=supplies,
        products={product: name for name, product in _PRODUCTS.items()})


def _engine(engine_factory, wc_world, epoch: int, location: str):
    """重放用的真实引擎：登记空计划，时间移到记录的当地钟点（场景首日），人物在记录的所在地。"""
    engine = engine_factory(str(wc_world.scenario("wc", phone="wc")))
    stage_day_plan(engine)
    moment = datetime.fromtimestamp(epoch, ZoneInfo(_FIXTURE["timezone"]))
    at_clock(engine, moment.hour, moment.minute)
    place(engine, 1, location)
    return engine


@pytest.mark.parametrize("recorded", _FIXTURE["requests"],
                         ids=[f"#{row['model_response_event_seq']}" for row in _FIXTURE["requests"]])
def test_wc_named_purchase_requests_are_accepted_in_an_offline_replay(engine_factory, wc_world, recorded) -> None:
    """记录里分别以 SUPPLY_NOT_FOUND 与 ACTION_REQUEST_INVALID 被拒的三次请求，重放时宿主放行、环境受理：#882 的商品名
    「番茄」「鸡蛋」经 from「盒马App」解析为盒马的成员门店，合成一单送到家，两项都记 channel_id；#890 的完整标签配同一家店
    的 from 按冗余受理并写说明；#994 的完整标签配包含该门店的渠道照常受理并记 channel_id。送达时刻按备货加路线。"""
    engine = _engine(engine_factory, wc_world, recorded["occurred_at"], recorded["agent_location_id"])
    request = recorded["request"]
    _, error, result = decide(engine, f"wc-{recorded['model_response_event_seq']}", request)
    assert recorded["recorded_rejection"]["error_code"] in {"SUPPLY_NOT_FOUND", "ACTION_REQUEST_INVALID"}
    assert error == "" and (result.status, result.error_code) == ("completed", None)
    names = {item.product_id: item.name for item in engine.context.scenario.products}
    sources = {item["from"] for item in request["items"]}
    channel = _FIXTURE["channel"]["channel_id"] if sources & set(_FIXTURE["channel"]["names"]) else None
    written = [item["target"].removeprefix(f"{_FIXTURE['store']['name']}出售的") for item in request["items"]]
    orders = result.data["orders"]
    assert [(names[order["product_id"]], order["location_id"], order["destination_id"], order["fulfillment"],
             order["channel_id"]) for order in orders] == [
        (name, _STORE, "home", "delivery", channel) for name in written]
    supplies = {item.supply_id: item for item in engine.context.scenario.supplies}
    assert {order["expected_at"] for order in orders} == {delivery_expected_at(
        engine.context.semantics, supplies[orders[0]["supply_id"]], "home", engine.context.state.current_time)}
    assert sum(order["total_price_fen"] for order in orders) == result.data["total_price_fen"]
    if recorded["model_response_event_seq"] == 890:
        assert result.data["notes"] == [f"The target already names {_FIXTURE['store']['name']}; from may be null."] * 2


def test_goods_that_cannot_reach_the_receiving_place_say_why_and_where_the_store_delivers(
        engine_factory, wc_world) -> None:
    """分段匹配的 M3 为空（3.2（c）第 4 步）：人在办公室时番茄只送到家，远程下单以 ACTIVITY_PRECONDITION_UNMET 拒绝；只有
    一家出售门店，原因与非指名的远程下单同一说法（5.3）「这里不在这家店的配送范围内」（unmet 为 delivery_destination），
    下一步只指向那家店能送达、且从此地可达的家。方式为「购买」时两者都不行，而出售番茄的门店只能远程下单（去了也买不到）：
    下一步不指向那家店（「收敛」第 2 条只指向能到店买的出售门店），与远程下单同一原因与下一步。没有别处能送到的同名商品，
    候选为空；不下单。"""
    request = _FIXTURE["requests"][0]
    engine = _engine(engine_factory, wc_world, request["occurred_at"], "office")
    store = _FIXTURE["store"]["name"]
    home = next(item.name for item in engine.context.scenario.locations if item.location_id == "home")
    before = dict(engine.context.state.deliveries)
    remote = {"items": [{"target": "番茄", "way": "远程下单", "from": store, "to": None}],
              "answering": None, "prefer": "最快拿到"}
    _, error, refused = decide(engine, "wc-office-remote", remote)
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert (refused.data["reason"], refused.data["unmet"]) == ("The current place is outside this store's delivery area.",
                                                           ["delivery_destination"])
    assert refused.data["next_step"] == f"Go to {home}, then place the order."
    [item] = refused.data["items"]
    assert (item["path"], item["written"], item["candidates"]) == (
        ["tool_calls", 0, "function", "arguments", "items", 0, "target"], "番茄", [])
    system_request = {**remote, "items": [{**remote["items"][0], "way": "购买"}]}
    _, error, system = decide(engine, "wc-office-system", system_request)
    assert error == "" and system.error_code == "ACTIVITY_PRECONDITION_UNMET"
    assert (system.data["reason"], system.data["unmet"]) == ("The current place is outside this store's delivery area.",
                                                         ["delivery_destination"])
    assert system.data["next_step"] == f"Go to {home}, then place the order." and store not in system.data["next_step"]
    [item] = system.data["items"]
    assert (item["path"], item["written"], item["candidates"]) == (
        ["tool_calls", 0, "function", "arguments", "items", 0, "target"], "番茄", [])
    assert dict(engine.context.state.deliveries) == before
