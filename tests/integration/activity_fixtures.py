"""活动集合的明确测试数据构造，不修改任何真实引擎输出。"""

from worldmock.catalog.affordances import default_affordances


def occupied_resources(rows: list[dict]) -> list[str]:
    """从测试声明活动构造对应的观察资源字段。"""
    claims = set()
    for row in rows:
        kind = row["kind"]
        if kind in {"chat", "call"}:
            claims.update({"position", "attention"})
        elif kind in default_affordances().kinds:
            claims.update(default_affordances().resource_claims(kind, row.get("travel_mode")))
    return sorted(claims)


def paused_activity(activity_id: str, kind: str = "work", **overrides) -> dict:
    """从实际活动资源声明构造一条可恢复状态投影。"""
    row = {"activity_id": activity_id, "status": "paused", "kind": kind,
           "remaining_duration_seconds": 60,
           "resource_claims": occupied_resources([{"kind": kind}])}
    row.update(overrides)
    return row
