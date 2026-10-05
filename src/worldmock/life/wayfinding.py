"""@brief 从此地出发的可达性与下一步去向的说法（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ，裁决 S9）。

下一步提示只指向从当前地点可达的地点：目标此地直达时写「去<地点>」；此地去不了、经别处可以到时写「可经<中转地>前往
<地点>」，中转地是从此地直达、且由它出发能到目标的地点（取总路程最短的一条路径上的第一站）；哪里都到不了时写「这里去不了
<地点>」（到店取得的门店写「这里去不了那家店」）。可达只认冻结路线中状态为 reachable 的一段（任一出行方式），与工具层
只列可达路线同一口径（「不可达的路线不列出」不变）；路程按冻结路线的自由流时长比较，同长时按地点身份决胜，结果确定。

远程下单另看配送路线：同一地点无需道路，其余配送只认门店到送达地恰好一条、且可达的冻结驾车路线（整单结算
`basket.plan_delivery` 与准入共用 delivery_route_code，拒绝码与原因同源）。本模块是纯函数，不读写世界状态。
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: 从此地出发哪里都到不了时，到店取得的门店的说法（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-浏览结果与提示看可达性 ）。
UNREACHABLE_STORE_STEP = "This store is unreachable from the current location."
#: 能送达的地点从此地都到不了时的说法。
UNREACHABLE_DELIVERY_STEP = "No eligible receiving location is reachable from here."
#: 缺少可读名称时的地点说法（不写内部身份）。
_UNNAMED_PLACE = "the required location"


def reachable_places(routes: Iterable[Any], origin: str) -> frozenset[str]:
    """@brief 从 origin 出发有可达冻结路线（任一出行方式）的目的地身份集合。"""
    return frozenset(route.destination_id for route in routes
                     if route.origin_id == origin and route.status == "reachable")


def _edges(routes: Iterable[Any]) -> dict[str, dict[str, int]]:
    """@brief 可达路线图：起点 → 终点 → 各出行方式中最短的自由流时长（秒）。"""
    graph: dict[str, dict[str, int]] = {}
    for route in routes:
        if route.status != "reachable":
            continue
        targets = graph.setdefault(route.origin_id, {})
        seconds = int(route.duration_seconds or 0)
        targets[route.destination_id] = min(seconds, targets.get(route.destination_id, seconds))
    return graph


def shortest_paths(routes: Iterable[Any], origin: str) -> dict[str, tuple[int, str]]:
    """@brief 从 origin 出发经可达路线能到的每个地点：（总路程秒数, 路径上的第一站）；origin 本身不在其中。

    Dijkstra，堆按（路程, 地点身份, 第一站）出队，所以同长路径按地点身份确定地决胜。
    """
    graph = _edges(routes)
    found: dict[str, tuple[int, str]] = {}
    frontier = [(seconds, place, place) for place, seconds in sorted(graph.get(origin, {}).items())]
    heapq.heapify(frontier)
    while frontier:
        seconds, place, hop = heapq.heappop(frontier)
        if place in found or place == origin:
            continue
        found[place] = (seconds, hop)
        for target, step in sorted(graph.get(place, {}).items()):
            if target not in found:
                heapq.heappush(frontier, (seconds + step, target, hop))
    return found


def go_step(routes: Sequence[Any], names: Mapping[str, str], origin: str, target: str,
            unreachable: str | None = None) -> str:
    """@brief 去 target 的下一步：此地直达（或就在那里）写「去<地点>」；只能经别处到达写「可经<第一站>前往<地点>」；
    哪里都到不了写 unreachable（缺省「这里去不了<地点>」）。

    :param routes: 冻结路线（场景 routes）。
    :param names: 地点身份 → 可读名称。
    :param origin: 出发地（人物的 location_id；途中为出发地，与工具层列路线同一口径）。
    :param target: 要去的地点身份。
    :param unreachable: 到不了时的说法；None 时写「这里去不了<地点>」。
    """
    name = names.get(target, _UNNAMED_PLACE)
    if origin == target or target in reachable_places(routes, origin):
        return f"Go to {name}."
    path = shortest_paths(routes, origin).get(target)
    if path is not None:
        return f"Go via {names.get(path[1], _UNNAMED_PLACE)} to {name}."
    return unreachable if unreachable is not None else f"{name} is unreachable from here."


def delivery_route_code(routes: Iterable[Any], origin: str, destination: str) -> str | None:
    """@brief 从门店 origin 配送到 destination 的道路判定：同一地点或恰好一条可达的冻结驾车路线为 None；否则为拒绝码
    DELIVERY_ROUTE_NOT_FOUND（没有或不止一条驾车路线）、DELIVERY_ROUTE_UNREACHABLE 或 DELIVERY_ROUTE_INFORMATION_UNKNOWN。"""
    if origin == destination:
        return None
    matches = [route for route in routes if route.origin_id == origin and route.destination_id == destination
               and route.mode.value == "driving"]
    if len(matches) != 1:
        return "DELIVERY_ROUTE_NOT_FOUND"
    if matches[0].status == "unreachable":
        return "DELIVERY_ROUTE_UNREACHABLE"
    if matches[0].status == "unknown":
        return "DELIVERY_ROUTE_INFORMATION_UNKNOWN"
    return None


def delivery_step(routes: Sequence[Any], names: Mapping[str, str], here: str, store: str,
                  destinations: Sequence[str]) -> str:
    """@brief 远程下单送不到这里时的下一步：能送达（在配送范围内且配送路线走得通）的别处中，此地直达的写
    「去<A>或<B>再下单」；都不直达、经别处能到的写「可经<第一站>前往<地点>再下单」（取总路程最短的）；都到不了写
    「这里去不了能送达的地方」。

    :param here: 此刻的送达地（人物的 location_id，途中为出发地）。
    :param store: 供给所在门店的地点身份（配送路线的起点）。
    :param destinations: 该供给声明的可配送目的地（保持声明顺序）。
    """
    served = [place for place in dict.fromkeys(destinations)
              if place != here and delivery_route_code(routes, store, place) is None]
    direct = reachable_places(routes, here)
    nearby = [place for place in served if place in direct]
    if nearby:
        choices = " or ".join(dict.fromkeys(names.get(place, _UNNAMED_PLACE) for place in nearby))
        return f"Go to {choices}, then place the order."
    paths = shortest_paths(routes, here)
    reached = sorted((paths[place][0], place) for place in served if place in paths)
    if not reached:
        return UNREACHABLE_DELIVERY_STEP
    place = reached[0][1]
    return (f"Go via {names.get(paths[place][1], _UNNAMED_PLACE)} to {names.get(place, _UNNAMED_PLACE)}, "
            "then place the order.")
