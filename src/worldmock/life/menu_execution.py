"""@brief 在共同结算与独立工作验收前执行权威交互菜单检查。"""

import logging
from collections.abc import Sequence

from worldmock.errors import BusinessRuleError
from worldmock.life.admission import present_location
from worldmock.life.interaction_menu import InteractionMenu, menu_allows_action, resolve_interaction_menu


def current_menu(scenario, state, agent_id: int) -> InteractionMenu:
    """@brief 人物此刻的有效菜单：停留时按所在地点的地点类与人物声明求交集；路程中没有场所，只取人物声明
    （出发地的菜单不带到路上，路上的限制由出行方式的准入收窄）。受理、工具层与宿主本地校验都取这一份。"""
    return resolve_interaction_menu(scenario, agent_id, present_location(state, agent_id))


def require_interaction_menu(scenario, state, pending, canonical_actions: Sequence[str]) -> None:
    """@brief 按人物此刻的所在（停留地点或路程）重算菜单，在领域副作用发生前拒绝越权动作。

    :param canonical_actions: start_activity 各项经本轮私有绑定映射后的内部标准动作（其他工具为空）。
    """
    menu = current_menu(scenario, state, pending.agent_id)
    if not menu_allows_action(menu, pending.tool_name, canonical_actions):
        logging.error("Interaction menu denies action: %s", pending.tool_name)
        raise BusinessRuleError("INTERACTION_MENU_DENIED")
