"""@brief 统一执行器的真实登记世界、会话与实际推进辅助。"""

from pathlib import Path

from tests.life.purchase_worlds import ENVIRONMENT, registered_world
from worldmock.life.action_run import ActionStart, initialize_action, prepare_action, settle_action
from worldmock.life.activities import ActivityOrigin, advance_activity
from worldmock.life.models import AppPush
from worldmock.life.sessions import current_session, open_stay


def action_world(location="home", multiple=False):
    """@brief 由严格生产加载器和会话构造器建立独立人物世界。"""
    source = ENVIRONMENT / "scenarios" / ("multi-person.json" if multiple else "single-person.json")
    semantics, state = registered_world(location, scenario_path=source)
    names = {key: item.name for key, item in semantics.catalog.locations.items()}
    for agent_id in state.agents:
        state = open_stay(state, agent_id, names)
    return semantics, state


def begin(semantics, state, action="browse-feed", app="douyin", counterpart=None, agent_id=1, suffix="first"):
    """@brief 人物所选动作只经生产准备与初始化构造一次实际内容选择。"""
    request = ActionStart(agent_id, f"activity-{suffix}", action, app, counterpart)
    prepared = prepare_action(state, semantics.catalog, semantics.scenario, request)
    changed = initialize_action(state, prepared, ActivityOrigin(suffix, "decision", "request"), semantics.catalog)
    return changed, changed.activities[request.activity_id]


def pin(state, content="c-short", app="douyin", agent_id=1, status="pending"):
    """@brief 已送达推送由严格状态模型保存，不声明消费历史。"""
    session = current_session(state, agent_id)
    session.app_pushes[app] = AppPush(push_id=f"push:{app}:{content}", content_id=content,
                                    issued_at=state.current_time, status=status)


def advance(semantics, state, activity, seconds):
    """@brief 先实际身体与活动推进，再经统一执行器记录同一区间内容。"""
    now = activity.last_settled_at + seconds
    result = advance_activity(activity, state.agents[activity.agent_id], now, semantics.body)
    changed = state.model_copy(deep=True)
    changed.current_time = result.activity.last_settled_at
    changed.agents[activity.agent_id] = result.agent
    changed.activities[activity.activity_id] = result.activity
    return settle_action(changed, result.activity, semantics.catalog, semantics.scenario,
                         activity.media_position_seconds)
