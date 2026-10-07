"""@brief 活动收尾结果的实际终局。"""

from worldmock.life.models import ActivityState, ActivityStatus

#: 收尾终局只取环境实际状态。
OUTCOME_COMPLETED, OUTCOME_ENDED_EARLY, OUTCOME_FAILED = "完成", "提前结束", "失败"


def conclusion_outcome(activity: ActivityState, name: str) -> dict:
    """@brief 人物自述不改变活动的完成、提前结束或失败终局。"""
    ending = {ActivityStatus.COMPLETED: OUTCOME_COMPLETED, ActivityStatus.FAILED: OUTCOME_FAILED}.get(
        activity.status, OUTCOME_ENDED_EARLY)
    return {"activity": name, "outcome": ending}
