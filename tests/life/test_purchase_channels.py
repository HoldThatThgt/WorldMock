"""购买渠道：App 名解析为成员门店（docs/dev/SPEC-dayrun-fidelity.md 3.3，裁决 D8、S7；3.5「收敛」；验收台账「购买渠道」）。

白领场景的隔离副本钉定一份手机配置：卖家 merchant:grocery → 盒马（称呼「盒马App」「盒马」，成员门店是集市），
merchant:haode 与 merchant:nonggongshang → 美团外卖（称呼「美团外卖」「美团」，成员门店是好德与农工商超市）。渠道经
load_runtime_scenario 的真实加载路径派生并准入；真实 LifeEngine、SQLite 与 LifeAgent 上，同一次冻结观察先经宿主本地校验、
再由环境受理：from 为 null（全部门店）、门店名（该门店）、渠道称呼（成员门店，订单记 channel_id）与其他写法
（SOURCE_NOT_FOUND，候选为最相近的门店名与 App 称呼）各一例；渠道内多家都卖时按最快选最近的一家并写进 details.notes，
并列时比到手价、再比门店标识；订单载荷与导出的 purchases.jsonl 带 channel_id；没有手机配置时 App 名报 SOURCE_NOT_FOUND；
准入不成立的渠道在加载时拒绝，手机配置核验失败时不派生渠道、加载照常。冻结观察另带查询来源筛选的两项私有事实：渠道
索引与供给条目所属门店（docs/dev/SPEC-errands-workday.md 5.2）。查询一侧（同一规格 5.2、5.5）：同一写法经购买的
source_scope 与查询的 from 得到逐一相等的门店集合，翻完的查询恰是这些门店里权限内的供给；渠道称呼写进 name 时给一条把它
移到 from 的候选；渠道称呼同时是商品名时按对象匹配；没有权限内供给的门店是合法空页、也不作候选。
"""

from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from tests.life.conftest import context, stage_day_plan, start_arguments  # noqa: E402
from tests.life.purchase_worlds import ROOT, SCENARIO, at_clock, build_world, decide, phone_config  # noqa: E402
from tests.life.routing_fixtures import place, stage  # noqa: E402
from tests.life.test_browse_and_hints import _show  # noqa: E402
from tests.life.test_world_query_filters import (CHANNEL_IN_NAME_EXPECTED, EMPTY_PAGE, ROOT as QUERY_ROOT,  # noqa: E402
                                                 _ask, _open, _oracle, _query, _source_candidates, _traverse)
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.footprints.purchases import project_purchase_facts  # noqa: E402
from worldmock.life.basket import delivery_expected_at  # noqa: E402
from worldmock.life.load_checks import check_channel_admission  # noqa: E402
from worldmock.life.name_resolution import resolve_source  # noqa: E402
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS  # noqa: E402
from worldmock.life.purchase_offers import source_scope  # noqa: E402
from worldmock.life.readable import clock_phrase  # noqa: E402
from worldmock.life.semantics import load_runtime_scenario  # noqa: E402
from worldmock.profile.coverage_apps import PurchaseChannel  # noqa: E402

_MILK = "纯牛奶"
_MARKET, _HAODE, _NONGGONGSHANG = "market", "poi-3538682402", "poi-3538763552"
#: 紫薇菜市场：盒马渠道除集市外的另一家成员门店；纯牛奶的目录对象身份（好德、农工商超市与集市都卖）。
_ZIWEI, _MILK_PRODUCT = "poi-3538684972", "food-6923644266066"
#: 查询明细共用的 why（5.5 第二行沿用查询的既有说法）。
_QUERY_WHY = "The submitted value is outside the current query contract."
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


def _buy(name: str = _MILK, source: str | None = None, way: str = "远程下单", prefer: str = "最快拿到") -> dict:
    """一项送给自己的取得请求（start_activity 的模型参数）。"""
    return start_arguments([name], ["obtain"], [way], **{"from": [source], "to": [None], "prefer": prefer})


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
        PurchaseChannel("hema", ("盒马App", "盒马"), (_MARKET, "poi-3538684972")),
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


@pytest.mark.parametrize(("source", "channel"), [
    ("盒马app", "hema"), ("美团 外卖", "meituan-takeaway"), ("盒马ＡＰＰ", None),
], ids=["case-insensitive", "spaces-removed", "full-width-compared-verbatim"])
def test_a_channel_name_is_compared_without_spaces_or_case(engine_factory, world, source, channel) -> None:
    """渠道称呼的比较（3.3 解析规则 1）：去掉空白、不分大小写，其余逐字比较——「盒马app」「美团 外卖」都是那个渠道（成员门店
    按最快选定，订单记 channel_id）；「盒马ＡＰＰ」的全角字母不做全半角归一，不是任何门店或渠道的名称，报 SOURCE_NOT_FOUND。
    宿主都放行。"""
    engine = _engine(engine_factory, world)
    _, error, result = decide(engine, "channel-key", _buy(source=source))
    assert error == ""
    if channel is None:
        assert (result.status, result.error_code) == ("rejected", "SOURCE_NOT_FOUND")
        return
    assert (result.status, result.error_code) == ("completed", None)
    assert [order["channel_id"] for order in result.data["orders"]] == [channel]


def test_an_unknown_source_is_source_not_found_with_near_stores_and_apps(engine_factory, world) -> None:
    """from 既不是门店名也不是渠道称呼（「美团App」）：宿主放行，环境以 SOURCE_NOT_FOUND 拒绝（名称类，3.4），逐项明细的
    参数名是 from，候选为最相近的门店名与 App 称呼写成的可照填请求（目标与方式照写，换 from）；不下单。"""
    engine = _engine(engine_factory, world)
    before = dict(engine.state.deliveries)
    _, error, result = decide(engine, "unknown-source", _buy(source="美团App"))
    assert error == "" and (result.status, result.error_code) == ("rejected", "SOURCE_NOT_FOUND")
    [item] = result.data["items"]
    assert (item["path"][-1], item["written"], item["error_code"]) == ("from", "美团App", "SOURCE_NOT_FOUND")
    sources = [candidate["arguments"]["items"][0]["from"] for candidate in item["candidates"]]
    assert sources[0] == "美团外卖" and "盒马App" in sources
    assert all((candidate["arguments"]["items"][0]["target"],
                candidate["arguments"]["items"][0]["way"], candidate["arguments"]["items"][0]["to"]) == (
        _MILK, "远程下单", None) for candidate in item["candidates"])
    assert result.data["reason"] == "The source is not a known store or channel name."
    assert result.data["fix"] == "Set from to null or an exact known store or channel name."
    assert dict(engine.state.deliveries) == before
    [candidate] = [row for row in item["candidates"] if row["arguments"]["items"][0]["from"] == "美团外卖"]
    _, retry_error, retried = decide(engine, "unknown-source-retry", candidate["arguments"])
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
    assert item["candidates"] and all(candidate["arguments"]["items"][0]["from"] in stores for candidate in item["candidates"])


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
    assert result.data["notes"] == [f"「{_MILK}」有 2 处都能远程下单，按最快拿到选了{_label(engine, haode)}"
                                    f"（预计 {moment} 送到）"]
    _, _, cheap = decide(engine, "cheapest", _buy(source="美团外卖", prefer="到手价最低"))
    assert [(row["supply_id"], row["channel_id"]) for row in cheap.data["orders"]] == [
        (nonggongshang.supply_id, "meituan-takeaway")]
    assert "按到手价最低选了" in cheap.data["notes"][0] and "到手价" in cheap.data["notes"][0]
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
    assert identity.data["notes"][0].startswith(f"「{_MILK}」有 2 处都能远程下单，按最快拿到选了{_label(engine, haode)}")


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
    assert (item["path"][-1], item["written"]) == ("from", "盒马App")
    assert [candidate["arguments"] for candidate in item["candidates"]] == [_buy(label)]
    assert refused.data["reason"] == "The target store 好德 is not a member of the requested channel."


def test_a_listed_label_with_an_app_in_from_is_resolved_against_that_channel(engine_factory, world) -> None:
    """本轮列出的商品目标写了 App 称呼作 from（3.3 解析规则 2 对列出的标签同样成立）：宿主放行 App 称呼，环境按来源约束解析
    ——包含该门店的渠道照常受理并记下 channel_id；不含该门店的渠道以 SUPPLY_SOURCE_MISMATCH 拒绝，候选就是这个标签，不按
    列出的绑定行忽略 from 直接下单。"""
    engine = _engine(engine_factory, world)
    haode = _supply(engine, _HAODE)
    label = _label(engine, haode)
    _show(engine, haode.supply_id)
    observed, error, accepted = decide(engine, "listed-in-channel", _buy(label, source="美团"))
    assert label in {row["object"] for row in observed["interaction_bindings"]}
    assert error == "" and accepted.status == "completed"
    assert [(row["supply_id"], row["channel_id"]) for row in accepted.data["orders"]] == [
        (haode.supply_id, "meituan-takeaway")]
    _show(engine, haode.supply_id)
    observed, error, refused = decide(engine, "listed-outside-channel", _buy(label, source="盒马App"))
    assert label in {row["object"] for row in observed["interaction_bindings"]}
    assert error == "" and (refused.status, refused.error_code) == ("rejected", "SUPPLY_SOURCE_MISMATCH")
    [item] = refused.data["items"]
    assert (item["path"][-1], item["written"]) == ("from", "盒马App")
    assert [candidate["arguments"] for candidate in item["candidates"]] == [_buy(label)]


def test_frozen_observation_carries_the_channel_index_and_supply_stores(engine_factory, world) -> None:
    """冻结观察只新增两项私有事实（docs/dev/SPEC-errands-workday.md 5.2）：渠道索引 world_query_channels 按声明顺序列出各渠道
    的身份、称呼与成员门店，与场景语义派生的购买渠道逐值相等；每个查询冻结条目带 location_id——供给条目是该供给所在的门店，
    其余条目为 null。观察事件从 SQLite 读回的两项与宿主拿到的逐值相等（环境入口读同一份）；两项都不进五个环境层，渠道索引
    列入提示禁键（location_id 已在禁键内）。"""
    engine = _engine(engine_factory, world)
    observed = engine.observe(context(engine, "frozen-sources"))
    channels = [{"channel_id": item.channel_id, "names": list(item.names),
                 "store_location_ids": list(item.store_location_ids)} for item in engine.semantics.purchase_channels]
    assert [row["channel_id"] for row in channels] == ["hema", "meituan-takeaway"]
    assert observed["world_query_channels"] == channels
    entries = observed["world_query_bindings"]
    for entry in entries:
        assert list(entry) == ["identity", "category", "aliases", "location_id", "match"]
        identity = entry["identity"]
        supply = engine.rules.supplies[identity.removeprefix("supply:")] if identity.startswith("supply:") else None
        assert entry["location_id"] == (None if supply is None else supply.location_id), identity
    stores = {entry["location_id"] for entry in entries}
    assert {_HAODE, _NONGGONGSHANG, _MARKET, None} <= stores
    [stored] = [event for event in engine.store.read_agent_events("observation", 1)
                if event.event_id == observed["observation_event_id"]]
    assert stored.data["world_query_channels"] == channels
    assert [(row["identity"], row["location_id"]) for row in stored.data["world_query_bindings"]] == [
        (entry["identity"], entry["location_id"]) for entry in entries]
    layers = json.dumps([observed[key] for key in ("tool_layer", "todo_layer", "environment_layer", "memory_layer",
                                                   "progress_layer")], ensure_ascii=False)
    assert '"world_query_channels"' not in layers and '"location_id"' not in layers
    assert {"world_query_channels", "location_id"} <= FORBIDDEN_OBSERVATION_KEYS


def test_query_and_purchase_resolve_each_from_form_to_the_same_stores(engine_factory, world) -> None:
    """来源解析购买与查询共用一个纯函数（docs/dev/SPEC-errands-workday.md 5.2）：同一写法——门店名、带「地点：」前缀的
    门店名、「家」「住处」、渠道称呼与大小写或空白不同的渠道称呼——经购买的 source_scope 与用冻结观察（地点表、住处、
    渠道索引）调用的 resolve_source 得到逐一相等的门店集合；沿 next_offset 翻完的 {from: 写法} 查询恰是这些门店里权限内
    的供给条目（住处没有供给，是合法空页）。既不是地点也不是渠道的写法两侧都拒绝：购买为 None，查询报 5.5 第一行的
    参数错误，候选含渠道称呼。"""
    engine = _engine(engine_factory, world)
    session = _open(engine, "query-sources")
    observed = session.observed
    purchase = SimpleNamespace(scenario=engine.scenario, semantics=engine.semantics, agent_id=1)
    places = {row["location_id"]: row["name"] for row in observed["locations"]}
    channels = [(row["channel_id"], row["names"], row["store_location_ids"]) for row in observed["world_query_channels"]]
    home = observed["home_location_id"]
    expected = {"好德": {_HAODE}, "地点：好德": {_HAODE}, "家": {home}, "住处": {home},
                "美团外卖": {_HAODE, _NONGGONGSHANG}, "盒马app": {_MARKET, _ZIWEI}, "美团 外卖": {_HAODE, _NONGGONGSHANG}}
    for written, stores in expected.items():
        scope = source_scope(purchase, written)
        match = resolve_source(written, places, home, channels)
        assert scope is not None and match is not None, written
        assert match.store_ids == scope.store_ids == stores, written
        assert (match.channel_id, match.name) == (scope.channel_id, scope.name), written
        matches, _ = _traverse(session, written)
        assert [row["name"] for row in matches] == _oracle(
            observed, lambda entry: entry["location_id"] in stores), written
    for written, offered in (("美团App", {"美团外卖", "盒马App"}), ("盒马ＡＰＰ", {"盒马App"})):
        assert source_scope(purchase, written) is None and resolve_source(written, places, home, channels) is None
        arguments = _query(written)
        assert offered <= set(_source_candidates(_ask(session, arguments), arguments)), written


def test_a_channel_name_written_in_name_gets_one_candidate_moving_it_to_from(engine_factory, world) -> None:
    """渠道称呼写进 name（5.5 第二行）：from 为 null、name 按对象名称解析无任何匹配而写法恰是渠道称呼时，不论其余筛选，
    在 name 位置报查询参数错误，候选是一条把该值照写移到 from、name 写 null 的完整请求；不自动改写（请求被拒，什么也没
    派发），照抄候选得到成员门店的供给。与未声明的方式同写时两条明细依次是方式与名称；方式空白或 available_only 不是布尔
    （请求模型错误）时名称这一条仍在同一份报告里、排在其后，候选其余键照原样；from 已写时是交集筛选的合法空页。"""
    engine = _engine(engine_factory, world)
    session = _open(engine, "channel-in-name")
    category = session.observed["tool_layer"]["callable_index"]["query_categories"][0]["category"]
    for arguments in (_query(name="美团外卖"), _query(name="美团外卖", way="远程下单", available_only=True),
                      _query(name="美团外卖", category=category)):
        asked = _ask(session, arguments)
        assert asked.report["error_code"] == "ARGUMENTS_INVALID"
        [item] = asked.report["items"]
        assert (item["path"], item["written"], item["error_code"], item["allowed_values"]) == (
            [*QUERY_ROOT, "name"], "美团外卖", "ARGUMENTS_INVALID", [])
        assert (item["expected"], item["why"]) == (CHANNEL_IN_NAME_EXPECTED, _QUERY_WHY)
        assert item["candidates"] == [{"tool": "query_world", "note": None,
                                       "arguments": {**arguments, "name": None, "from": "美团外卖"}}]
    assert session.host._life_action_count == 0
    spaced = _query(name="美团 外卖", way="随便做")
    items = _ask(session, spaced).report["items"]
    assert [(item["path"][-1], item["written"]) for item in items] == [("way", "随便做"), ("name", "美团 外卖")]
    assert [candidate["arguments"] for candidate in items[1]["candidates"]] == [
        {**spaced, "name": None, "from": "美团 外卖"}]
    for arguments, field in ((_query(name="美团外卖", way=" "), "way"), (_query(name="美团外卖", available_only=1),
                                                                      "available_only")):
        items = _ask(session, arguments).report["items"]
        assert [(item["path"][-1], item["written"]) for item in items] == [
            (field, arguments[field]), ("name", "美团外卖")], arguments
        assert [candidate["arguments"] for candidate in items[1]["candidates"]] == [
            {**arguments, "name": None, "from": "美团外卖"}]
    assert session.host._life_action_count == 0
    places = {row["location_id"]: row["name"] for row in session.observed["locations"]}
    assert _ask(session, _query(places[_HAODE], name="美团外卖")).page == EMPTY_PAGE
    matches, _ = _traverse(session, "美团外卖")
    assert [row["name"] for row in matches] == _oracle(
        session.observed, lambda entry: entry["location_id"] in {_HAODE, _NONGGONGSHANG})


@pytest.fixture(scope="module")
def filter_world(tmp_path_factory):
    """查询来源筛选用的另一份隔离语料（同一 channels 手机配置）：紫薇菜市场的九条供给都要求人物没有的资格 member
    （人物在那里没有权限内供给，盒马渠道仍有权限内的成员门店集市），纯牛奶的目录名称改为「盒马」（渠道称呼同时是商品名）。"""
    document = json.loads(SCENARIO.read_text(encoding="utf-8"))
    dataset = json.loads((ROOT / "corpora" / document["map_dataset"]["path"]).read_text(encoding="utf-8"))
    published = json.loads((ROOT / "corpora" / dataset["supplies_file"]["path"]).read_text(encoding="utf-8"))
    supplies = {row["supply_id"]: {"required_qualifications": ["member"]} for row in published["supplies"]
                if row["location_id"] == _ZIWEI}
    assert len(supplies) == 9
    return build_world(tmp_path_factory.mktemp("filter-world"),
                       {"channels": phone_config({**_HEMA, **_MEITUAN}, _NAMES)},
                       supplies=supplies, products={_MILK_PRODUCT: "盒马"})


def test_a_channel_name_that_is_also_an_object_name_is_matched_as_an_object(engine_factory, filter_world) -> None:
    """渠道称呼同时是对象名称时按对象匹配（5.5 第二行只在 name 无任何对象匹配时成立）：name=「盒马」得到三家门店出售的
    「盒马」，不报错；只是渠道称呼、不是任何对象名的「盒马App」写进 name 仍报第二行的参数错误。"""
    session = _open(_engine(engine_factory, filter_world), "channel-object")
    asked = _ask(session, _query(name="盒马"))
    assert asked.error == ""
    names = [row["name"] for row in asked.page["matches"]]
    assert len(names) == 3 and all(name.endswith("出售的盒马") for name in names)
    assert names == _oracle(session.observed, lambda entry: "盒马" in entry["aliases"])
    [item] = _ask(session, _query(name="盒马App")).report["items"]
    assert (item["path"][-1], item["expected"]) == ("name", CHANNEL_IN_NAME_EXPECTED)
    assert [candidate["arguments"]["from"] for candidate in item["candidates"]] == ["盒马App"]


def test_a_store_without_permitted_supply_is_an_empty_page_and_never_a_candidate(engine_factory, filter_world) -> None:
    """来源解析成功但该来源没有权限内供给（5.5 第三行）：紫薇菜市场的供给都不在冻结条目里，from=紫薇菜市场 是合法空页；
    与它相近的未知写法报第一行的参数错误，候选只取权限内有供给条目的门店与渠道，不泄露紫薇菜市场；盒马渠道仍有权限内的
    成员门店集市，from=盒马 只得到集市的供给。"""
    session = _open(_engine(engine_factory, filter_world), "unpermitted-store")
    observed = session.observed
    places = {row["location_id"]: row["name"] for row in observed["locations"]}
    assert not any(entry["location_id"] == _ZIWEI for entry in observed["world_query_bindings"])
    asked = _ask(session, _query(places[_ZIWEI]))
    assert asked.error == "" and asked.page == EMPTY_PAGE
    arguments = _query(places[_ZIWEI][:-1])
    sources = _source_candidates(_ask(session, arguments), arguments)
    permitted = {entry["location_id"] for entry in observed["world_query_bindings"]} - {None}
    assert places[_ZIWEI] not in sources
    assert set(sources) <= {places[store] for store in permitted} | {"盒马App", "美团外卖"}
    matches, _ = _traverse(session, "盒马")
    assert [row["name"] for row in matches] == _oracle(observed, lambda entry: entry["location_id"] == _MARKET)
    assert matches
