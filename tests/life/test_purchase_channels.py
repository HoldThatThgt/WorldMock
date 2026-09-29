"""购买渠道：App 名解析为成员门店（docs/dev/SPEC-dayrun-fidelity.md 3.3，裁决 D8、S7；3.5「收敛」；验收台账「购买渠道」）。

白领场景的隔离副本钉定一份手机配置：卖家 merchant:grocery → 盒马（称呼「盒马App」「盒马」，成员门店是集市），
merchant:haode 与 merchant:nonggongshang → 美团外卖（称呼「美团外卖」「美团」，成员门店是好德与农工商超市）。渠道经
load_runtime_scenario 的真实加载路径派生并准入；真实 LifeEngine、SQLite 与 LifeAgent 上，同一次冻结观察先经宿主本地校验、
再由环境受理：from 为 null（全部门店）、门店名（该门店）、渠道称呼（成员门店，订单记 channel_id）与其他写法
（SOURCE_NOT_FOUND，候选为最相近的门店名与 App 称呼）各一例；渠道内多家都卖时按最快选最近的一家并写进 details.notes，
并列时比到手价、再比门店标识；订单载荷与导出的 purchases.jsonl 带 channel_id；没有手机配置时 App 名报 SOURCE_NOT_FOUND；
准入不成立的渠道在加载时拒绝，手机配置核验失败时不派生渠道、加载照常。
"""

from __future__ import annotations

import json
import logging
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from tests.life.conftest import stage_day_plan, start_arguments  # noqa: E402
from tests.life.purchase_worlds import at_clock, build_world, decide, phone_config  # noqa: E402
from tests.life.routing_fixtures import place, stage  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.footprints.purchases import project_purchase_facts  # noqa: E402
from worldmock.life.basket import delivery_expected_at  # noqa: E402
from worldmock.life.load_checks import check_channel_admission  # noqa: E402
from worldmock.life.readable import clock_phrase  # noqa: E402
from worldmock.life.semantics import load_runtime_scenario  # noqa: E402
from worldmock.profile.coverage_apps import PurchaseChannel  # noqa: E402

_MILK = "纯牛奶"
_MARKET, _HAODE, _NONGGONGSHANG = "market", "poi-3538682402", "poi-3538763552"
#: 好德与农工商超市到它的驾车时长相同（114 秒）：两家的预计送达并列。
_EQUIDISTANT = "poi-3538682404"
_HEMA = {"merchant:grocery": "hema"}
_MEITUAN = {"merchant:haode": "meituan-takeaway", "merchant:nonggongshang": "meituan-takeaway"}
_NAMES = {"hema": ["盒马App", "盒马"], "meituan-takeaway": ["美团外卖", "美团"]}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """一份隔离语料，登记三份手机配置：channels（盒马与美团外卖），shared（两个 App 的称呼只差空白与大小写），
    place（App 称呼等于地点「好德」的显示名）。"""
    return build_world(tmp_path_factory.mktemp("channel-world"), {
        "channels": phone_config({**_HEMA, **_MEITUAN}, _NAMES),
        "shared": phone_config({"merchant:haode": "meituan-takeaway", "merchant:nonggongshang": "meituan-shop"},
                               {"meituan-takeaway": ["美团外卖 App"], "meituan-shop": ["美团外卖app"]}),
        "place": phone_config({"merchant:haode": "haode-app"}, {"haode-app": ["好德"]}),
    })


def _engine(engine_factory, world, phone: str | None = "channels"):
    """钉定一份手机配置的场景副本上的真实引擎：登记空计划，时间在 1 月 5 日 10:00（门店营业），人物在家。"""
    engine = engine_factory(str(world.scenario(f"scenario-{phone}", phone=phone)))
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    return engine


def _buy(name: str = _MILK, source: str | None = None, way: str = "远程下单", prefer: str | None = None) -> dict:
    """一项送给自己的取得请求（start_activity 的模型参数）。"""
    return start_arguments([name], ["obtain"], [way], **{"from": [source], "to": ["自己"], "prefer": prefer})


def _supply(engine, location_id: str):
    """某家门店出售的纯牛奶。"""
    return next(item for item in engine.scenario.supplies if item.location_id == location_id
                and engine.rules.items[item.product_id].name == _MILK)


def _label(engine, supply) -> str:
    """供给的完整标签「<门店>出售的<商品>」。"""
    store = next(item.name for item in engine.scenario.locations if item.location_id == supply.location_id)
    return f"{store}出售的{engine.rules.items[supply.product_id].name}"


def _arrival(engine, supply, destination: str) -> int:
    """现在下单送到 destination 的预计送达时刻（与受理、结算同一算法）。"""
    return delivery_expected_at(engine.semantics, supply, destination, engine.state.current_time)


def test_channels_are_derived_from_the_pinned_phone_config_at_load(world) -> None:
    """渠道由场景钉定的手机配置派生（裁决 S7，不新增场景字段）：每个购买 App 一个渠道，channel_id 取 App 标识，names 取其
    称呼，成员门店取映射到它的卖家有供给的地点；没有钉定手机配置的场景没有渠道。"""
    semantics, _ = load_runtime_scenario(world.scenario("derived", phone="channels"))
    assert semantics.purchase_channels == (
        PurchaseChannel("hema", ("盒马App", "盒马"), (_MARKET,)),
        PurchaseChannel("meituan-takeaway", ("美团外卖", "美团"), (_HAODE, _NONGGONGSHANG)))
    plain, _ = load_runtime_scenario(world.scenario("no-phone"))
    assert plain.purchase_channels == ()


@pytest.mark.parametrize(("phone", "problem"), [
    ("shared", "name '美团外卖 App' is shared by channels meituan-shop and meituan-takeaway"),
    ("place", f"channel haode-app name '好德' equals location {_HAODE}"),
], ids=["name-shared-across-channels", "name-equals-a-location"])
def test_channel_admission_violations_are_rejected_at_load(world, phone: str, problem: str) -> None:
    """渠道准入（3.3，与 8.1 的加载校验同一时机）：称呼在渠道之间重复（去掉空白、不分大小写比较）或等于某个地点的显示名，
    场景加载即以 IntegrityError 拒绝并写明渠道、称呼与地点。"""
    with pytest.raises(IntegrityError, match="Purchase channels are not admissible") as rejected:
        load_runtime_scenario(world.scenario(f"inadmissible-{phone}", phone=phone))
    assert problem in str(rejected.value)


def test_channel_admission_requires_selected_member_stores_with_supplies(world) -> None:
    """成员门店必须是场景选入的地点并至少有一条供给，每个渠道至少一家成员门店；准入逐一写明不成立之处。"""
    semantics, _ = load_runtime_scenario(world.scenario("members", phone="channels"))
    scenario = semantics.scenario
    check_channel_admission(scenario, semantics.purchase_channels)
    park = next(item.location_id for item in scenario.locations
                if not any(supply.location_id == item.location_id for supply in scenario.supplies))
    broken = (PurchaseChannel("empty", ("空渠道",), ()), PurchaseChannel("park", ("公园外卖",), (park,)),
              PurchaseChannel("ghost", ("幽灵外卖",), ("no-such-place",)), PurchaseChannel("blank", (" ",), (_MARKET,)))
    with pytest.raises(IntegrityError) as rejected:
        check_channel_admission(scenario, broken)
    message = str(rejected.value)
    for problem in ("channel empty has no member store", f"channel park member store {park} has no supply",
                    "channel ghost member store no-such-place is not a selected location",
                    "channel blank has an empty name"):
        assert problem in message


def test_a_phone_config_that_fails_verification_derives_no_channels(world, caplog) -> None:
    """钉定的手机配置核验失败（身份：场景钉的是过期的摘要）时不派生渠道、加载照常（新建运行不因手机配置核验失败而失败，
    与覆盖率同一口径），并写一条英文 warning 日志。"""
    stale = "0" * 64
    with caplog.at_level(logging.WARNING):
        semantics, _ = load_runtime_scenario(world.scenario("stale", phone="channels", pin=stale))
    assert semantics.purchase_channels == ()
    assert ("Phone simulation config failed verification; no purchase channels are derived: "
            "Phone simulation config identity is invalid") in caplog.text


@pytest.mark.parametrize(("source", "store", "channel"), [
    (None, _HAODE, None), ("农工商超市", _NONGGONGSHANG, None), ("美团外卖", _HAODE, "meituan-takeaway"),
    ("盒马", _MARKET, "hema"),
], ids=["null-means-every-store", "store-name", "channel-name", "channel-alias"])
def test_each_from_form_resolves_to_its_source_scope(engine_factory, world, source, store, channel) -> None:
    """from 决定来源范围（3.3 解析规则 1）：null 是全部门店（按最快选到从家送最快的好德）；门店名是该门店；渠道称呼（任一
    称呼）是成员门店并记下 channel_id。宿主放行，环境受理，订单的 channel_id 只在 from 是渠道称呼时有值。"""
    engine = _engine(engine_factory, world)
    _, error, result = decide(engine, "source", _buy(source=source))
    assert error == "" and (result.status, result.error_code) == ("completed", None)
    [order] = result.data["orders"]
    assert (order["location_id"], order["channel_id"]) == (store, channel)


def test_an_unknown_source_is_source_not_found_with_near_stores_and_apps(engine_factory, world) -> None:
    """from 既不是门店名也不是渠道称呼（「美团App」）：宿主放行，环境以 SOURCE_NOT_FOUND 拒绝（名称类，3.4），逐项明细的
    参数名是 from，候选为最相近的门店名与 App 称呼写成的可照填请求（目标与方式照写，换 from）；不下单。"""
    engine = _engine(engine_factory, world)
    before = dict(engine.state.deliveries)
    _, error, result = decide(engine, "unknown-source", _buy(source="美团App"))
    assert error == "" and (result.status, result.error_code) == ("rejected", "SOURCE_NOT_FOUND")
    [item] = result.data["items"]
    assert (item["field"], item["written"], item["error_code"]) == ("from", "美团App", "SOURCE_NOT_FOUND")
    sources = [candidate["from"] for candidate in item["candidates"]]
    assert sources[0] == "美团外卖" and "盒马App" in sources
    assert all((candidate["target"], candidate["action"], candidate["way"], candidate["to"]) == (
        _MILK, "obtain", "远程下单", "自己") for candidate in item["candidates"])
    assert result.data["reason"] == "「美团App」：不是门店或 App 的名称"
    assert result.data["fix"] == "把 from 写成 null，或者照候选写门店名或 App 名"
    assert dict(engine.state.deliveries) == before
    [candidate] = [row for row in item["candidates"] if row["from"] == "美团外卖"]
    _, retry_error, retried = decide(engine, "unknown-source-retry", {**_buy(), **{
        "from": [candidate["from"]], "targets": [candidate["target"]], "ways": [candidate["way"]]}})
    assert retry_error == "" and retried.status == "completed"


def test_an_app_name_without_a_phone_config_is_source_not_found(engine_factory) -> None:
    """场景没有钉定手机配置时没有渠道（3.3「渠道的来源」）：App 名写在 from 以 SOURCE_NOT_FOUND 拒绝，候选是此刻能买到这件
    商品的门店名。"""
    engine = engine_factory()
    stage_day_plan(engine)
    at_clock(engine, 10, 0)
    assert engine.semantics.purchase_channels == ()
    _, error, result = decide(engine, "app-without-phone", _buy(source="盒马App"))
    assert error == "" and (result.status, result.error_code) == ("rejected", "SOURCE_NOT_FOUND")
    [item] = result.data["items"]
    stores = {item.name for item in engine.scenario.locations}
    assert item["candidates"] and all(candidate["from"] in stores for candidate in item["candidates"])


def test_several_member_stores_choose_the_nearest_and_note_it(engine_factory, world, tmp_path) -> None:
    """渠道内多家成员门店都卖（美团外卖的好德与农工商超市）：按最快选最近的一家（远程下单取预计送达最早，裁决 D8），所选的
    完整标签写进 details.notes，不报 SUPPLY_NAME_AMBIGUOUS；订单载荷的 channel_id 是美团外卖，导出的 purchases.jsonl 同一列
    （3.3 解析规则 4）；prefer=cheapest 时改选到手价最低的一家。"""
    engine = _engine(engine_factory, world)
    haode, nonggongshang = _supply(engine, _HAODE), _supply(engine, _NONGGONGSHANG)
    assert _arrival(engine, haode, "home") < _arrival(engine, nonggongshang, "home")
    _, error, result = decide(engine, "nearest", _buy(source="美团外卖"))
    assert error == "" and (result.status, result.error_code) == ("completed", None)
    [order] = result.data["orders"]
    assert (order["supply_id"], order["channel_id"]) == (haode.supply_id, "meituan-takeaway")
    moment = clock_phrase(order["expected_at"], engine.state.current_time, engine.scenario.timezone)
    assert result.data["notes"] == [f"「{_MILK}」有 2 处都能远程下单，按最快选了{_label(engine, haode)}"
                                    f"（预计 {moment} 送到）"]
    _, _, cheap = decide(engine, "cheapest", _buy(source="美团外卖", prefer="cheapest"))
    assert [(row["supply_id"], row["channel_id"]) for row in cheap.data["orders"]] == [
        (nonggongshang.supply_id, "meituan-takeaway")]
    assert "按最便宜选了" in cheap.data["notes"][0] and "到手价" in cheap.data["notes"][0]
    stored = [event for event in engine.store.read_agent_events("action_result", 1) if event.action_id == "action-nearest"]
    assert [row["channel_id"] for row in stored[0].data["data"]["orders"]] == ["meituan-takeaway"]
    directory = tmp_path / "export"
    export_run(engine.store, engine.store.last_event_seq, directory)
    rows = [json.loads(line) for line in (directory / "purchases.jsonl").read_text(encoding="utf-8").splitlines()]
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == project_purchase_facts(events)
    assert [(row["supply_id"], row["channel_id"]) for row in rows if row["fact_kind"] == "purchase"] == [
        (haode.supply_id, "meituan-takeaway"), (nonggongshang.supply_id, "meituan-takeaway")]


def test_member_store_ties_break_by_landed_price_then_store_identity(engine_factory, world) -> None:
    """人在鼎盛川菜时好德与农工商超市到那里的预计送达相同（并列）：先比到手价，选便宜的农工商超市；两家到手价也相同时比
    门店标识，选身份在前的好德（3.5「收敛」第 5 条）。"""
    engine = _engine(engine_factory, world)
    place(engine, 1, _EQUIDISTANT)
    haode, nonggongshang = _supply(engine, _HAODE), _supply(engine, _NONGGONGSHANG)
    assert _arrival(engine, haode, _EQUIDISTANT) == _arrival(engine, nonggongshang, _EQUIDISTANT)
    _, _, cheaper = decide(engine, "tie-price", _buy(source="美团外卖"))
    assert [row["supply_id"] for row in cheaper.data["orders"]] == [nonggongshang.supply_id]

    def same_price(state) -> None:
        """@brief 好德的纯牛奶调到与农工商超市同价（同一报价版本内改价）。"""
        state.supply_quotes[haode.supply_id].price_fen = state.supply_quotes[nonggongshang.supply_id].price_fen
    stage(engine, same_price)
    _, _, identity = decide(engine, "tie-identity", _buy(source="美团外卖"))
    assert [row["supply_id"] for row in identity.data["orders"]] == [haode.supply_id]
    assert identity.data["notes"][0].startswith(f"「{_MILK}」有 2 处都能远程下单，按最快选了{_label(engine, haode)}")


def test_a_full_label_accepts_a_channel_that_contains_its_store_and_rejects_one_that_does_not(
        engine_factory, world) -> None:
    """目标是完整标签（3.3 解析规则 2）：from 是包含该门店的渠道时照常受理并记下 channel_id（宿主放行 App 称呼）；是不含该门店
    的渠道时环境以 SUPPLY_SOURCE_MISMATCH 拒绝，候选就是这个标签（from 为 null）。"""
    engine = _engine(engine_factory, world)
    haode = _supply(engine, _HAODE)
    label = _label(engine, haode)
    _, error, accepted = decide(engine, "label-in-channel", _buy(label, source="美团"))
    assert error == "" and accepted.status == "completed"
    assert [(row["supply_id"], row["channel_id"]) for row in accepted.data["orders"]] == [
        (haode.supply_id, "meituan-takeaway")]
    _, error, refused = decide(engine, "label-outside-channel", _buy(label, source="盒马App"))
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "SUPPLY_SOURCE_MISMATCH")
    [item] = refused.data["items"]
    assert (item["field"], item["written"]) == ("from", "盒马App")
    assert item["candidates"] == [{"target": label, "action": "obtain", "way": "远程下单", "from": None, "to": "自己"}]
    assert refused.data["reason"] == "「盒马App」：目标写明的是好德，它不在这个 App 里"
