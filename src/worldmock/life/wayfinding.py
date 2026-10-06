"""@brief 在已登记连通图上查询地点，只提示直接前往目标地点。"""

from typing import Mapping

from worldmock.catalog.graph import Graph


def go_step(graph: Graph, names: Mapping[str, str], origin: str, target: str) -> str:
    """@brief 全图已经保证连通，完整路径由图查询取得而无需人物逐跳决策。"""
    graph.path(origin, target)
    return f"Go to {names[target]}."
