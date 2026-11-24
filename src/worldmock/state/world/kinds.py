"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType


class ActivityKind(StrEnum):
    """@brief 人物能够显式启动的活动类型。"""

    SLEEP = "sleep"
    TRAVEL = "travel"
    INTERACT = "interact"
    PURCHASE = "purchase"
    SEARCH = "search"


class ActivityStatus(StrEnum):
    """@brief 活动生命周期状态。"""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TRUNCATED = "truncated"


class ActionStatus(StrEnum):
    """@brief 动作从提交到终态的状态。"""

    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ActivityChange(StrEnum):
    """@brief 进行中活动允许的显式变更。"""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


class FulfillmentKind(StrEnum):
    """@brief 订单的履约方式。"""

    DELIVERY = "delivery"
    PICKUP = "pickup"
    STATION_PICKUP = "station_pickup"
    INSTANT = "instant"


#: 履约途径的机器取值（沿用 FulfillmentKind）到人物可读中文名的只读映射。
ROUTE_NAMES = MappingProxyType({
    "pickup": "到店",
    "delivery": "外卖",
    "station_pickup": "快递",
    "instant": "即时到账",
})


class SessionStatus(StrEnum):
    """@brief 地点会话的生命周期状态闭集（docs/design/09-io-formats.md「世界、人物与活动」）。"""

    ACTIVE = "active"
    ENDED = "ended"


class SessionKind(StrEnum):
    """@brief 地点会话的种类：一段地点停留（stay）或两地之间的一段路（transit）。"""

    STAY = "stay"
    TRANSIT = "transit"


class SessionTaskKind(StrEnum):
    """@brief 活动关联的任务类型闭集（从原会话移到活动）。"""

    GOAL = "goal"
    NONE = "none"


#: 需要人物收尾的终态（截断不收尾）：状态属于此集合且 concluded_at 为空的活动即待收尾。
CONCLUDABLE_TERMINAL_STATUSES = frozenset({
    ActivityStatus.COMPLETED,
    ActivityStatus.FAILED,
    ActivityStatus.CANCELLED,
})
