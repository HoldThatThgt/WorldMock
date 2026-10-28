"""@brief 活动收尾结果的实际终局。"""

from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.state.world.activity import ActivityState

#: 收尾终局只取环境实际状态。
OUTCOME_COMPLETED, OUTCOME_ENDED_EARLY, OUTCOME_FAILED = "完成", "提前结束", "失败"
#: 被人物收尾结束的一次 app 使用的终局说法：自动接续之下一次 app 使用没有计划的结束，被收尾不是提前。
OUTCOME_APP_USE_ENDED = "结束使用"


def app_use_ended(activity: ActivityState) -> bool:
    """@brief 是否是被收尾结束的一次 app 使用：状态 cancelled、种类 interact、活动数据 app_id 不为空。

    settled 句的终局说法（activity_control.ending_phrase）与收尾结果的 outcome（conclusion_outcome）都读它。
    """
    return (activity.status == ActivityStatus.CANCELLED and activity.kind == ActivityKind.INTERACT
            and activity.data["app_id"] is not None)


def conclusion_outcome(activity: ActivityState, name: str) -> dict:
    """@brief 人物自述不改变活动的完成、结束使用、提前结束或失败终局。"""
    if app_use_ended(activity):
        return {"activity": name, "outcome": OUTCOME_APP_USE_ENDED}
    ending = {ActivityStatus.COMPLETED: OUTCOME_COMPLETED, ActivityStatus.FAILED: OUTCOME_FAILED}.get(
        activity.status, OUTCOME_ENDED_EARLY)
    return {"activity": name, "outcome": ending}
