"""@brief 在共同结算与独立工作验收前执行权威交互菜单检查。"""

from worldmock.errors import BusinessRuleError
from worldmock.life.interaction_menu import menu_allows_action, resolve_interaction_menu


def require_interaction_menu(scenario, state, pending, request) -> None:
    """@brief 按当前人物位置重算菜单，在领域副作用发生前拒绝越权动作。"""
    agent = state.agents[pending.agent_id]
    menu = resolve_interaction_menu(scenario, agent.agent_id, agent.location_id)
    activity = state.activities.get(getattr(request, "activity_id", None))
    kind = activity.kind if activity is not None and activity.agent_id == agent.agent_id else None
    if not menu_allows_action(menu, pending.tool_name, request, kind):
        raise BusinessRuleError("INTERACTION_MENU_DENIED")
