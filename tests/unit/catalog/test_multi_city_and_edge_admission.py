"""多城分城成图、限定声明边路由、双轨一日时限、无驿站即时包与回调增量校验。"""

from __future__ import annotations

import hashlib
import tomllib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.frozen_inputs import fixture_metadata
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.configuration.inputs.environment import load_environment
from worldmock.exchange_engine.definitions.constants import TRAVEL_MAX_SECONDS
from worldmock.foundation.errors import BusinessRuleError, IntegrityError
from worldmock.resource_engine.catalog.callback_models import CallbackState
from worldmock.resource_engine.catalog.loader import load_catalog
from worldmock.resource_engine.catalog.models import (
    Content,
    ContentPrerequisite,
    Goods,
    Location,
    MapEdge,
    MapGraph,
    OpeningHours,
    TravelMethod,
)
from worldmock.resource_engine.catalog.travel_methods import Method
from worldmock.resource_engine.map.graph import Graph
from worldmock.resource_engine.map.mapgen import generate_edges, generate_map, map_bytes
from worldmock.resource_engine.registration.admission import validate_route_limit
from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.simulation_engine.activities.travel.travel import persisted_trip, plan_trip
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActivityKind, ActivityStatus


def _seed(value: int) -> str:
    """@brief 生成测试用规范 64 位十六进制随机种子。"""
    return hashlib.sha256(f"multi-city:{value}".encode("utf-8")).hexdigest()


def _place(location_id: str, city_id: str, *, is_home: bool = False,
           accepts_parcels: bool = False, sleepable: bool = False,
           sleep_consumes_state: str | None = None, kind: str = "place") -> Location:
    """@brief 构造最小合法地点模型。"""
    kwargs = {"location_id": location_id, "name": location_id, "kind": kind,
              "city_id": city_id, "app_ids": (), "is_home": is_home,
              "accepts_parcels": accepts_parcels, "sleepable": sleepable}
    if sleep_consumes_state is not None:
        kwargs["sleep_consumes_state"] = sleep_consumes_state
    return Location(**kwargs)


def _method(method_id: str, speed: float, *, city_wide: bool = True) -> TravelMethod:
    """@brief 构造测试用出行方式模型。"""
    return TravelMethod(travel_method_id=method_id, name=method_id, speed_meters_per_second=speed,
                        start_price_fen=0, price_per_km_fen=0, exertion="light", city_wide=city_wide)


def test_v7_models_enforce_strict_types_and_reject_explicit_nulls():
    """@brief 新增字段遵循默认值与显式 null/非法类型拒绝合同。"""
    with pytest.raises(ValidationError):
        Location(location_id="bad-null", name="bad", kind="place", city_id="c1",
                 app_ids=(), sleep_consumes_state=None)
    with pytest.raises(ValidationError):
        Location(location_id="bad-city", name="bad", kind="place", city_id="", app_ids=())
    assert TravelMethod(travel_method_id="bus", name="公交", speed_meters_per_second=5,
                        start_price_fen=100, price_per_km_fen=0, exertion="light").city_wide is True
    with pytest.raises(ValidationError):
        TravelMethod(travel_method_id="bus", name="公交", speed_meters_per_second=5,
                     start_price_fen=100, price_per_km_fen=0, exertion="light", city_wide=1)
    for field in ("travel_method_ids", "consumes_state", "opens"):
        with pytest.raises(ValidationError):
            MapEdge(a="a", b="b", length_m=100, **{field: None})
    opens = OpeningHours(weekly={"mon": [["08:00", "10:00"]]})
    gated = MapEdge(a="a", b="b", length_m=100, travel_method_ids=("train",), opens=opens)
    assert gated.is_open(datetime.fromisoformat("2026-01-05T09:00:00+08:00")) is True
    assert gated.is_open(datetime.fromisoformat("2026-01-05T11:00:00+08:00")) is False
    assert MapEdge(a="a", b="b", length_m=100).is_open(datetime.fromisoformat("2026-01-05T11:00:00+08:00")) is True
    assert Goods(goods_id="g1", name="券", category="goods", price_fen=100, app_ids=("wechat",)).instant is False
    with pytest.raises(ValidationError):
        CallbackState(state="s1", initial=0, cap=3, reset_on_goods=(), gain_on_goods=None)
    with pytest.raises(ValidationError):
        ContentPrerequisite(city_ids=None)
    with pytest.raises(ValidationError):
        Content(content_id="c1", title="t1", duration_seconds=60, summary="s",
                summary_source={"kind": "synthetic", "notice": "test", "url": None},
                app_ids=("bilibili",), exertion="idle", repeatable="false")


def test_per_city_mapgen_seed_isolation_and_single_node_city():
    """@brief 分城按 map:{city_id} 独立成图：单节点城无随机内部边，增删城市互不扰动既有城内道路。"""
    city_a = (_place("a1", "city-a", is_home=True), _place("a2", "city-a"), _place("a3", "city-a"))
    city_b = (_place("b1", "city-b"),)
    only_a = generate_map(city_a, _seed(7))
    bridge = (MapEdge(a="a1", b="b1", length_m=12000, travel_method_ids=("rail",)),)
    with_b = generate_map([*city_a, *city_b], _seed(7), bridge)
    assert with_b.nodes == ("a1", "a2", "a3", "b1")
    a_edges_before = [(e.a, e.b, e.length_m) for e in only_a.edges]
    a_edges_after = [(e.a, e.b, e.length_m) for e in with_b.edges if e.a.startswith("a") and e.b.startswith("a")]
    assert a_edges_before == a_edges_after
    assert [e for e in with_b.edges if e.a == "b1" or e.b == "b1"] == list(bridge)
    assert all(e.travel_method_ids is None for e in only_a.edges)
    # 自由边 omit null 字段，限制边保留显式声明字段。
    raw = map_bytes(with_b).decode("utf-8")
    assert '"travel_method_ids":null' not in raw
    assert '"consumes_state":null' not in raw
    assert '"opens":null' not in raw
    assert '"travel_method_ids":["rail"]' in raw


def test_generate_map_never_creates_intercity_edges_and_respects_declared_bypass():
    """@brief 无跨城声明边时不生成跨城随机边，同城自由声明边不被同城随机 extra 边绕过。"""
    nodes = (_place("a1", "city-a", is_home=True), _place("a2", "city-a"), _place("a3", "city-a"),
             _place("b1", "city-b"), _place("b2", "city-b"))
    declared = (MapEdge(a="a1", b="a3", length_m=9000),)
    generated = generate_map(nodes, _seed(11), declared)
    by_city = {place.location_id: place.city_id for place in nodes}
    assert all(by_city[e.a] == by_city[e.b] for e in generated.edges)
    graph = Graph(generated, by_city)
    assert graph.distance_meters("a1", "a3") == 9000
    assert graph.path("a1", "a3") == ("a1", "a3")
    # 未连接两城时不产生随机跨城边，完整图检查按不连通拒绝。
    with pytest.raises(RegistrationError, match="MAP_DISCONNECTED"):
        from worldmock.resource_engine.registration.admission import validate_map

        validate_map(generated, set(generated.nodes))


def test_graph_intercity_bfs_hub_tiebreak_and_method_isolation():
    """@brief 跨城枢纽指引取最少城市跳数与最短本城距离；限定声明边只作直达单跳、city_wide=False 禁走自由路网。"""
    city_by_node = {
        "a-home": "alpha", "a-dead-hub": "alpha", "a-hub-close": "alpha", "a-hub-far": "alpha",
        "b-hub": "beta", "c-dest": "gamma", "d-dead": "delta",
    }
    edges = (
        MapEdge(a="a-dead-hub", b="a-home", length_m=50),
        MapEdge(a="a-dead-hub", b="d-dead", length_m=1000, travel_method_ids=("flight",)),
        MapEdge(a="a-home", b="a-hub-close", length_m=300),
        MapEdge(a="a-home", b="a-hub-far", length_m=900),
        MapEdge(a="a-hub-close", b="a-hub-far", length_m=100, travel_method_ids=("shuttle",)),
        MapEdge(a="a-hub-close", b="b-hub", length_m=50000, travel_method_ids=("rail",)),
        MapEdge(a="a-hub-far", b="b-hub", length_m=40000, travel_method_ids=("rail",)),
        MapEdge(a="b-hub", b="c-dest", length_m=30000, travel_method_ids=("rail",)),
    )
    graph = Graph(MapGraph(nodes=tuple(sorted(city_by_node)), edges=edges), city_by_node)
    assert graph.city_of("a-home") == "alpha"
    assert graph.declared_edge("a-hub-close", "b-hub") == graph.declared_edge("b-hub", "a-hub-close")
    assert [dest for dest, _ in graph.intercity_edges_from("a-home")] == []
    # 最少城市跳数避开 d-dead 死胡同，在同跳数两座本城枢纽中取步行最近且编号升序者。
    assert graph.next_hub_toward("a-home", "c-dest") == "a-hub-close"
    assert graph.next_hub_toward("a-hub-close", "c-dest") == "b-hub"
    assert graph.next_hub_toward("a-home", "a-hub-far") is None
    # 限定边不得参与自由路径多跳：a-home -> a-hub-far 自由路 900m，不会借 a-hub-close 的 shuttle 缩短到 400m。
    assert graph.shortest_route("a-home", "a-hub-far", "driving", city_wide=True) == (900, ("a-home", "a-hub-far"))
    assert graph.shortest_route("a-home", "a-hub-close", "shuttle", city_wide=False) is None
    assert graph.shortest_route("a-hub-close", "a-hub-far", "shuttle", city_wide=False) == (
        100, ("a-hub-close", "a-hub-far"))
    assert graph.shortest_route("a-home", "b-hub", "rail", city_wide=False) is None
    assert graph.shortest_route("a-hub-close", "b-hub", "rail", city_wide=False) == (50000, ("a-hub-close", "b-hub"))


def test_dual_track_validate_route_limit():
    """@brief 双轨时限：city_wide=False 不拖累全城路网且高速长边合法，但最慢许可方式超出当日上限仍被拒绝。"""
    nodes = (_place("a1", "city-a", is_home=True), _place("a2", "city-a"), _place("b1", "city-b"))
    city_by_node = {place.location_id: place.city_id for place in nodes}
    # city-a 仅靠限定边相连 -> 自由街区子图不连通
    disconnected = Graph(MapGraph(
        nodes=("a1", "a2", "b1"),
        edges=(MapEdge(a="a1", b="a2", length_m=500, travel_method_ids=("shuttle",)),
               MapEdge(a="a2", b="b1", length_m=5000, travel_method_ids=("rail",))),
    ), city_by_node)
    methods = {"rail": _method("rail", 80.0, city_wide=False), "shuttle": _method("shuttle", 10.0, city_wide=False)}
    with pytest.raises(RegistrationError, match="MAP_DISCONNECTED"):
        validate_route_limit(nodes, disconnected, methods)

    # 长跨城高铁 1,000,000 m @ 80 m/s -> 最差天气 0.6 下用时 20834 s <= 86400 s 放行，步行/慢速 city_wide=False 不误杀。
    long_free_edge = MapEdge(a="a1", b="a2", length_m=2000)
    fast_rail_edge = MapEdge(a="a1", b="b1", length_m=1_000_000, travel_method_ids=("rail",))
    ok_graph = Graph(MapGraph(nodes=("a1", "a2", "b1"), edges=(long_free_edge, fast_rail_edge)), city_by_node)
    slow_local = _method("slow-tram", 1.0, city_wide=False)
    validate_route_limit(nodes, ok_graph, {**methods, "slow-tram": slow_local})

    # 若将该慢速方式加入跨城限定边，单边轨道触发 MAP_ROUTE_TOO_LONG；若设为 city_wide=True 且超距也触发。
    too_slow_gated = Graph(
        MapGraph(nodes=("a1", "a2", "b1"),
                 edges=(long_free_edge, MapEdge(a="a1", b="b1", length_m=100_000,
                                                travel_method_ids=("rail", "slow-tram")))),
        city_by_node,
    )
    with pytest.raises(RegistrationError, match="MAP_ROUTE_TOO_LONG") as err:
        validate_route_limit(nodes, too_slow_gated, {**methods, "slow-tram": slow_local})
    assert f"the limit is {TRAVEL_MAX_SECONDS} s." in err.value.detail

    too_slow_citywide = _method("snail-walk", 0.01, city_wide=True)
    with pytest.raises(RegistrationError, match="MAP_ROUTE_TOO_LONG"):
        validate_route_limit(nodes, ok_graph, {**methods, "snail-walk": too_slow_citywide})


def test_map_jsonl_and_gain_on_goods_registration_refusals(tmp_path):
    """@brief map.jsonl 各限制条款与 CallbackState.gain_on_goods 重叠、未排序和未知引用均被精确拒绝。"""
    def env(name):
        return copy_environment(tmp_path / name)

    cases = [
        ("unordered-pair", [{"a": "store", "b": "cafe", "length_m": 500}], "ENVIRONMENT_KIND_INVALID"),
        ("same-city-free-edge", [{"a": "cafe", "b": "home", "length_m": 500}], None),
        ("free-with-opens", [{"a": "cafe", "b": "home", "length_m": 500,
                              "opens": {"weekly": {"mon": [["08:00", "10:00"]]}}}], "ENVIRONMENT_KIND_INVALID"),
        ("free-with-state", [{"a": "cafe", "b": "home", "length_m": 500,
                              "consumes_state": "ticket"}], "ENVIRONMENT_KIND_INVALID"),
        ("unsorted-methods", [{"a": "cafe", "b": "home", "length_m": 500,
                               "travel_method_ids": ["walking", "driving"]}], "ENVIRONMENT_KIND_INVALID"),
        ("unknown-method", [{"a": "cafe", "b": "home", "length_m": 500,
                             "travel_method_ids": ["rocket"]}], "ENVIRONMENT_REF_UNKNOWN"),
        ("unknown-state", [{"a": "cafe", "b": "home", "length_m": 500,
                            "travel_method_ids": ["driving"], "consumes_state": "missing-state"}],
         "ENVIRONMENT_REF_UNKNOWN"),
        ("free-bypass", [{"a": "cafe", "b": "home", "length_m": 400},
                         {"a": "cafe", "b": "store", "length_m": 400},
                         {"a": "home", "b": "store", "length_m": 1500}], "MAP_DISTANCE_CONTRADICTED"),
        ("gated-bypass-ignored", [{"a": "cafe", "b": "home", "length_m": 400},
                                  {"a": "cafe", "b": "store", "length_m": 400},
                                  {"a": "home", "b": "store", "length_m": 1500,
                                   "travel_method_ids": ["driving"]}], None),
    ]
    for label, map_rows, expected_code in cases:
        root = env(label)
        write_catalog_rows(root, "callbacks.jsonl",
                           [{"state": "ticket", "initial": 1, "cap": 3, "reset_on_goods": []}])
        write_catalog_rows(root, "map.jsonl", map_rows)
        if expected_code is None:
            load_catalog(root / "catalog", 7)
        else:
            with pytest.raises(RegistrationError) as failure:
                load_catalog(root / "catalog", 7)
            assert failure.value.code == expected_code

    # 跨城边缺 travel_method_ids 或包含 walking 拒绝；同城未加限定时受自由绕过校验，限定边不参与自由绕过检查。
    intercity = env("intercity-rules")
    loc_rows = catalog_rows(intercity, "location.jsonl")
    loc_rows.append({"app_ids": [], "city_id": "remote", "kind": "park",
                     "location_id": "remote-hub", "name": "远方站"})
    write_catalog_rows(intercity, "location.jsonl", sorted(loc_rows, key=lambda r: r["location_id"]))
    write_catalog_rows(intercity, "map.jsonl", [{"a": "home", "b": "remote-hub", "length_m": 5000}])
    with pytest.raises(RegistrationError, match="ENVIRONMENT_KIND_INVALID"):
        load_catalog(intercity / "catalog", 7)
    write_catalog_rows(intercity, "map.jsonl", [{"a": "home", "b": "remote-hub", "length_m": 5000,
                                                 "travel_method_ids": ["driving", "walking"]}])
    with pytest.raises(RegistrationError, match="ENVIRONMENT_KIND_INVALID"):
        load_catalog(intercity / "catalog", 7)

    # gain_on_goods 未知商品、重排及与 reset_on_goods 重叠拒绝。
    for label, state_row, expected_code in (
        ("gain-unknown", {"state": "pts", "initial": 0, "cap": 2, "reset_on_goods": [], "gain_on_goods": ["nope"]},
         "ENVIRONMENT_REF_UNKNOWN"),
        ("gain-unsorted", {"state": "pts", "initial": 0, "cap": 2, "reset_on_goods": [],
                           "gain_on_goods": ["online-notebook", "lemon-tea"]}, "ENVIRONMENT_KIND_INVALID"),
        ("gain-overlap", {"state": "pts", "initial": 0, "cap": 2, "reset_on_goods": ["lemon-tea"],
                          "gain_on_goods": ["lemon-tea"]}, "ENVIRONMENT_KIND_INVALID"),
        ("gain-null", {"state": "pts", "initial": 0, "cap": 2, "reset_on_goods": [], "gain_on_goods": None},
         "ENVIRONMENT_KIND_INVALID"),
    ):
        root = env(label)
        write_catalog_rows(root, "callbacks.jsonl", [state_row])
        with pytest.raises(RegistrationError) as failure:
            load_catalog(root / "catalog", 7)
        assert failure.value.code == expected_code

    # 店铺商品不允许声明 instant（哪怕写 false）。
    instant_store = env("instant-store")
    goods_rows = catalog_rows(instant_store, "merchant-catalog.jsonl")
    next(row for row in goods_rows if row.get("store_kinds"))["instant"] = False
    write_catalog_rows(instant_store, "merchant-catalog.jsonl", goods_rows)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(instant_store / "catalog", 7)
    assert failure.value.code == "ENVIRONMENT_KIND_INVALID"


def test_parcel_station_is_optional_for_instant_only_and_must_match_home_city(tmp_path):
    """@brief 仅含 instant 线上商品时零驿站仍可加载；含快递商品时仅在 home_location 同城找驿站。"""
    root = copy_environment(tmp_path / "instant-only")
    locs = [row for row in catalog_rows(root, "location.jsonl") if not row.get("accepts_parcels")]
    contents = [row for row in catalog_rows(root, "content-catalog.jsonl")
                if "parcel_station" not in row.get("location_kinds", ())]
    goods = catalog_rows(root, "merchant-catalog.jsonl")
    for row in goods:
        if row.get("app_ids"):
            row["instant"] = True
    write_catalog_rows(root, "location.jsonl", locs)
    write_catalog_rows(root, "content-catalog.jsonl", contents)
    write_catalog_rows(root, "merchant-catalog.jsonl", goods)
    try:
        loaded = load_environment(root, metadata_loader=fixture_metadata)
    except RegistrationError as error:
        raise AssertionError(f"Instant-only catalog must not require a parcel station: {error}") from error
    assert all(not loc.accepts_parcels for loc in loaded.catalog.locations.values())

    # 还原非 instant 线上商品：若只把驿站放在 city-b，而 persona.home_location 在 test-city，则加载环境拒绝，
    # 若人设住进 city-b 自有住处则通过且 nearest_parcel_station 限定在同城。
    courier_root = copy_environment(tmp_path / "remote-station")
    c_locs = catalog_rows(courier_root, "location.jsonl")
    next(r for r in c_locs if r["location_id"] == "parcel-station")["city_id"] = "city-b"
    c_locs.append({"app_ids": [], "city_id": "city-b", "is_home": True, "kind": "home",
                   "location_id": "home-b", "name": "乙城之家", "sleepable": True})
    c_locs.sort(key=lambda r: r["location_id"])
    write_catalog_rows(courier_root, "location.jsonl", c_locs)
    write_catalog_rows(courier_root, "map.jsonl",
                       [{"a": "home", "b": "home-b", "length_m": 20000, "travel_method_ids": ["driving"]}])
    with pytest.raises(RegistrationError, match="PARCEL_STATION_REQUIRED"):
        load_environment(courier_root, metadata_loader=fixture_metadata)

    persona_path = courier_root / "persona.toml"
    text = persona_path.read_text(encoding="utf-8").replace('home_location = "家"', 'home_location = "乙城之家"')
    persona_path.write_text(text, encoding="utf-8", newline="\n")
    env_b = load_environment(courier_root, metadata_loader=fixture_metadata)
    assert env_b.catalog.nearest_parcel_station("home-b") == "parcel-station"
    assert env_b.catalog.locations["parcel-station"].city_id == "city-b"


def test_content_prerequisite_city_ids_and_series_repeatable_rules(tmp_path):
    """@brief ContentPrerequisite.city_ids 校验与未知城市拒绝，及同系列不同 repeatable 拒载。"""
    root = copy_environment(tmp_path / "prereq")
    rows = catalog_rows(root, "content-catalog.jsonl")
    target = next(row for row in rows if row["content_id"] == "c-article")
    target["prerequisite"] = {"city_ids": ["unknown-city"]}
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    with pytest.raises(RegistrationError, match="CONTENT_BINDING_DANGLING"):
        load_catalog(root / "catalog", 7)

    # 同一系列第一集 repeatable=false 第二集未写（默认 true）拒绝。
    rep_root = copy_environment(tmp_path / "repeatable-mismatch")
    c_rows = catalog_rows(rep_root, "content-catalog.jsonl")
    next(r for r in c_rows if r["content_id"] == "c-drama-1")["repeatable"] = False
    write_catalog_rows(rep_root, "content-catalog.jsonl", c_rows)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(rep_root / "catalog", 7)
    assert failure.value.code == "CONTENT_SERIES_INVALID"


def test_graph_and_mapgen_edge_error_and_tiebreak_paths():
    """@brief 验证未知城市/节点拒绝、非可达城市剪枝与失联返回 None、直连/自由短路径择优，以及非法边入图拒绝。"""
    locs = [_place("a1", "city-a"), _place("a2", "city-a"), _place("a3", "city-a")]
    with pytest.raises(RegistrationError, match="MAP_EDGE_INVALID"):
        generate_edges(locs, _seed(1), [MapEdge(a="a1", b="missing", length_m=500)])

    nodes = ("a1", "a2", "a3", "b1", "z1")
    cities = {"a1": "city-a", "a2": "city-a", "a3": "city-a", "b1": "city-b", "z1": "city-z"}
    edges = (
        MapEdge(a="a1", b="a2", length_m=2000),
        MapEdge(a="a2", b="a3", length_m=2000),
        MapEdge(a="a1", b="a3", length_m=1000, travel_method_ids=("bus",)),
        MapEdge(a="a1", b="z1", length_m=50000, travel_method_ids=("hsr",)),
        MapEdge(a="a3", b="b1", length_m=50000, travel_method_ids=("hsr",)),
    )
    graph = Graph(MapGraph(nodes=nodes, edges=edges), cities)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_REF_UNKNOWN"):
        graph.city_of("unknown")
    # z1 与 dest b1 不连通，next_hub_toward 跳过不可达枢纽并选出 a3；直连与自由路比较取更短者。
    assert graph.next_hub_toward("a2", "b1") == "a3"
    assert graph.shortest_route("a1", "a3", "bus", city_wide=True) == (1000, ("a1", "a3"))

    longer_direct = Graph(
        MapGraph(nodes=("a1", "a2", "a3"), edges=(
            MapEdge(a="a1", b="a2", length_m=2000),
            MapEdge(a="a2", b="a3", length_m=2000),
            MapEdge(a="a1", b="a3", length_m=5000, travel_method_ids=("bus",)),
        )),
        {"a1": "city-a", "a2": "city-a", "a3": "city-a"},
    )
    assert longer_direct.shortest_route("a1", "a3", "bus", city_wide=True) == (4000, ("a1", "a2", "a3"))

    isolated = Graph(MapGraph(nodes=("a1", "b1"), edges=()), {"a1": "city-a", "b1": "city-b"})
    assert isolated.next_hub_toward("a1", "b1") is None

    dead_end_hub = Graph(
        MapGraph(nodes=("a1", "b1", "z1"), edges=(
            MapEdge(a="a1", b="z1", length_m=50000, travel_method_ids=("hsr",)),
        )),
        {"a1": "city-a", "b1": "city-b", "z1": "city-z"},
    )
    assert dead_end_hub.next_hub_toward("a1", "b1") is None


def test_plan_trip_and_persisted_trip_reject_missing_routes(tmp_path):
    """@brief plan_trip 无可用路径拒绝为 ACTIVITY_PRECONDITION_UNMET，persisted_trip 包装为 IntegrityError。"""
    graph = Graph(MapGraph(nodes=("a1", "b1"), edges=()), {"a1": "city-a", "b1": "city-b"})
    walk = Method("walking", "步行", 1.4, 0, 0, "moderate", "heavy", True)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET"):
        plan_trip(graph, "a1", "b1", walk)

    root = copy_environment(tmp_path / "persisted")
    methods = catalog_rows(root, "travel-method.jsonl")
    methods.append({"city_wide": False, "exertion": "light", "name": "城际专列",
                    "price_per_km_fen": 0, "speed_meters_per_second": 50, "start_price_fen": 0,
                    "travel_method_id": "hsr"})
    write_catalog_rows(root, "travel-method.jsonl", sorted(methods, key=lambda row: row["travel_method_id"]))
    loaded = load_environment(root, metadata_loader=fixture_metadata)
    activity = ActivityState(
        activity_id="act-1", origin_action_id="req-1", origin_decision_id="dec-1", origin_request_event_id="ev-1",
        agent_id=1, kind=ActivityKind.TRAVEL, name="城际专列前往公司", status=ActivityStatus.ACTIVE,
        started_at=100, last_settled_at=100, elapsed_seconds=0, planned_end_at=200,
        planned_duration_seconds=100, location_id="home", destination_id="office", travel_method_id="hsr",
        data={"action": "travel", "trip_id": "home->office:hsr", "path": ["home", "office"],
              "exertion": "light", "distance_meters": 1000, "weather_factor": 1.0},
    )
    with pytest.raises(IntegrityError, match="published graph"):
        persisted_trip(loaded.catalog, activity)
