"""活动集合的明确测试数据构造，不修改任何真实引擎输出。"""

from worldmock.catalog.affordances import default_affordances
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS


def occupied_resources(rows: list[dict]) -> list[str]:
    """从测试声明活动构造对应的观察资源字段：交谈与通话取社交占用，其余取注册表占用（含出行方式与内容品类的条件占用，
    docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」占用表）。"""
    claims = set()
    for row in rows:
        kind = row["kind"]
        if kind in {"chat", "call"}:
            claims.update(SOCIAL_RESOURCE_CLAIMS)
        elif kind in default_affordances().kinds:
            claims.update(default_affordances().resource_claims(kind, row.get("travel_mode"), row.get("content_kind")))
    return sorted(claims)


def paused_activity(activity_id: str, kind: str = "work", **overrides) -> dict:
    """从实际活动资源声明构造一条可恢复状态投影。"""
    row = {"activity_id": activity_id, "status": "paused", "kind": kind,
           "remaining_duration_seconds": 60,
           "resource_claims": occupied_resources([{"kind": kind}])}
    row.update(overrides)
    return row
