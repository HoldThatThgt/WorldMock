"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from enum import StrEnum




class ActivityKind(StrEnum):
    """@brief 人物能够显式启动的活动类型。"""

    SLEEP = "sleep"
    TRAVEL = "travel"
    INTERACT = "interact"
    PURCHASE = "purchase"
    QUERY = "query"



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



class SameMomentStage(StrEnum):
    """@brief 同刻受限轮阶段闭集（docs/design/04-simulation-engine.md「原子性与恢复」）。

    未达同刻上限时为 open；达到上限后每个受限轮只开放一类动作，阶段依赖上一受限轮的结果，时钟推进复位为 open。
    暂停的活动不占槽位，阶梯不按槽位分流：resume_or_conclude 与 conclude_only 留在闭集中，
    现行阶梯不进入它们（docs/design/04-simulation-engine.md）。
    """

    OPEN = "open"
    CONCLUDE_PENDING = "conclude_pending"
    START_OR_RESUME = "start_or_resume"
    RESUME_OR_CONCLUDE = "resume_or_conclude"
    CONCLUDE_ONLY = "conclude_only"



#: 需要人物收尾的终态（截断不收尾）：状态属于此集合且 concluded_at 为空的活动即待收尾。
CONCLUDABLE_TERMINAL_STATUSES = frozenset({ActivityStatus.COMPLETED, ActivityStatus.FAILED,
                                           ActivityStatus.CANCELLED})
