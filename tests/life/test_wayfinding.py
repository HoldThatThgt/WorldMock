"""可达性与下一步去向的说法（life.wayfinding，docs/dev/SPEC-dayrun-fidelity.md 5.3）：纯函数单元测试。

直达写「去<地点>」、只能经别处到达写「可经<第一站>前往<地点>」、哪里都到不了写「这里去不了<地点>」（门店写「这里去不了
那家店」）；路程按可达冻结路线的自由流时长取最短、同长按地点身份决胜；配送道路判定与整单结算同一口径（同一地点无需道路，
否则恰好一条可达的驾车路线），远程下单的下一步只列能送达且从此地可达的地点。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from worldmock.life.guidance import DELIVERY_DESTINATION_REASON, DELIVERY_ROUTE_REASON, default_reason
from worldmock.life.wayfinding import (UNREACHABLE_DELIVERY_STEP, UNREACHABLE_STORE_STEP, delivery_route_code,
                                       delivery_step, go_step, reachable_places, shortest_paths)

_NAMES = {"here": "此地", "hub": "枢纽", "side": "侧门", "store": "门店", "far": "远郊", "lone": "孤岛"}


def _route(origin: str, destination: str, seconds: int | None = 60, status: str = "reachable",
           mode: str = "driving") -> SimpleNamespace:
    """一条冻结路线的最小视图（起终点、状态、自由流时长与出行方式）。"""
    return SimpleNamespace(origin_id=origin, destination_id=destination, status=status,
                           duration_seconds=seconds if status == "reachable" else None,
                           mode=SimpleNamespace(value=mode))


#: 此地直达枢纽（驾车 60、步行 300 秒）与侧门，枢纽与侧门都通门店（侧门更近），门店通远郊；孤岛只有不可达路线。
_ROUTES = (_route("here", "hub"), _route("here", "hub", 300, mode="walking"), _route("here", "side", 90),
           _route("hub", "store", 50), _route("side", "store", 10), _route("store", "far", 30),
           _route("here", "store", status="unreachable"), _route("here", "lone", status="unknown"),
           _route("hub", "lone", status="unreachable"))


def test_reachable_places_only_count_reachable_routes_from_the_origin() -> None:
    """可达只认从出发地出发、状态为 reachable 的路线（任一出行方式）。"""
    assert reachable_places(_ROUTES, "here") == frozenset({"hub", "side"})
    assert reachable_places(_ROUTES, "far") == frozenset()


def test_shortest_paths_keep_the_first_hop_of_the_shortest_route() -> None:
    """最短路程取各方式中最短的一段相加：门店经侧门 100 秒比经枢纽 110 秒近，远郊沿同一条路径继承第一站。"""
    paths = shortest_paths(_ROUTES, "here")
    assert paths == {"hub": (60, "hub"), "side": (90, "side"), "store": (100, "side"), "far": (130, "side")}
    tied = (_route("o", "b", 10), _route("o", "a", 10), _route("a", "t", 10), _route("b", "t", 10))
    assert shortest_paths(tied, "o")["t"] == (20, "a")  # 同长按地点身份决胜


@pytest.mark.parametrize(("target", "unreachable", "expected"), [
    ("hub", None, "去枢纽"),
    ("here", None, "去此地"),
    ("store", None, "可经侧门前往门店"),
    ("far", None, "可经侧门前往远郊"),
    ("lone", None, "这里去不了孤岛"),
    ("lone", UNREACHABLE_STORE_STEP, "这里去不了那家店"),
    ("nowhere", None, "这里去不了那里"),
])
def test_go_step_points_only_to_places_reachable_from_here(target, unreachable, expected) -> None:
    """下一步只指向从此地可达的地点：直达（或就在那里）、经第一站、到不了三种说法。"""
    assert go_step(_ROUTES, _NAMES, "here", target, unreachable) == expected


def test_delivery_route_code_matches_the_settlement_rule() -> None:
    """配送道路：同一地点无需道路；恰好一条可达的驾车路线才算走得通；没有、多于一条、只有步行路线都是
    DELIVERY_ROUTE_NOT_FOUND，不可达与未知各自的码。"""
    routes = (_route("s", "a"), _route("s", "b", status="unreachable"), _route("s", "c", status="unknown"),
              _route("s", "d"), _route("s", "d", 70), _route("s", "e", mode="walking"))
    assert delivery_route_code(routes, "s", "s") is None
    assert delivery_route_code(routes, "s", "a") is None
    assert [delivery_route_code(routes, "s", place) for place in ("b", "c", "d", "e", "z")] == [
        "DELIVERY_ROUTE_UNREACHABLE", "DELIVERY_ROUTE_INFORMATION_UNKNOWN", "DELIVERY_ROUTE_NOT_FOUND",
        "DELIVERY_ROUTE_NOT_FOUND", "DELIVERY_ROUTE_NOT_FOUND"]


def test_delivery_step_lists_reachable_served_places_then_a_via_then_nowhere() -> None:
    """远程下单的下一步：能送达（在范围内、配送路线走得通）且此地直达的地点以「或」相连；都不直达时取总路程最短的一处
    写「可经……前往……再下单」；都到不了写「这里去不了能送达的地方」。"""
    routes = (*_ROUTES, _route("depot", "hub"), _route("depot", "side"), _route("depot", "store"),
              _route("depot", "far"), _route("depot", "lone", status="unreachable"))
    names = {**_NAMES, "depot": "仓库"}
    assert delivery_step(routes, names, "here", "depot", ("hub", "side", "hub", "here")) == "去枢纽或侧门再下单"
    assert delivery_step(routes, names, "here", "depot", ("far", "store")) == "可经侧门前往门店再下单"
    assert delivery_step(routes, names, "here", "depot", ("lone",)) == UNREACHABLE_DELIVERY_STEP
    assert delivery_step(routes, names, "here", "depot", ("depot",)) == UNREACHABLE_DELIVERY_STEP
    assert UNREACHABLE_DELIVERY_STEP == "这里去不了能送达的地方"


@pytest.mark.parametrize("status", ["unknown", "unreachable", None])
def test_delivery_step_skips_places_without_a_working_delivery_route(status) -> None:
    """能送达还要配送路线走得通：此地直达的枢纽若门店到那里的驾车路线未知、不可达或缺失，就不作为去处；只剩这样的地点时
    说「这里去不了能送达的地方」。"""
    routes = (*_ROUTES, _route("depot", "side"),
              *(() if status is None else (_route("depot", "hub", status=status),)))
    names = {**_NAMES, "depot": "仓库"}
    assert delivery_step(routes, names, "here", "depot", ("hub", "side")) == "去侧门再下单"
    assert delivery_step(routes, names, "here", "depot", ("hub",)) == UNREACHABLE_DELIVERY_STEP


def test_the_four_delivery_codes_use_the_two_delivery_reasons() -> None:
    """「暂时送不到这里」一句不再覆盖四个配送码：送达地不在范围一句，配送路线缺失（没有、不可达、未知）另一句。"""
    assert default_reason("DELIVERY_DESTINATION_NOT_SUPPORTED") == DELIVERY_DESTINATION_REASON
    assert {default_reason(code) for code in ("DELIVERY_ROUTE_NOT_FOUND", "DELIVERY_ROUTE_UNREACHABLE",
                                              "DELIVERY_ROUTE_INFORMATION_UNKNOWN")} == {DELIVERY_ROUTE_REASON}
    assert DELIVERY_DESTINATION_REASON != DELIVERY_ROUTE_REASON
    assert "暂时送不到这里" not in {DELIVERY_DESTINATION_REASON, DELIVERY_ROUTE_REASON}
