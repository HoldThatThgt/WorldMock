"""@brief LifeAgent 对冻结交互菜单的解析和宿主边界校验。"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from worldmock.life.interaction_menu import InteractionMenu, menu_allows_action


def parse_interaction_menu(data: dict[str, Any]) -> InteractionMenu:
    """@brief 从非只读观察读取并严格核验已解析的有效菜单。

    @param data 当前冻结环境观察的数据段。
    @return 通过闭集、保护项和确定性顺序校验的菜单。
    """
    raw = data.get("interaction_menu")
    if not isinstance(raw, dict):
        return _invalid_menu("LifeAgent interaction menu must be an object")
    try:
        menu = InteractionMenu.model_validate(raw)
    except ValidationError as error:
        return _invalid_menu(f"LifeAgent interaction menu is invalid: {error}", error)
    if menu.allowed_actions != tuple(sorted(menu.allowed_actions)):
        return _invalid_menu("LifeAgent interaction menu actions must be sorted")
    return menu


def menu_action_error(menu: InteractionMenu, action: str, request: BaseModel) -> str:
    """@brief 在本地校验与直接派发边界复用一次冻结菜单。

    地点菜单只剪裁 start_activity 的 action 枚举，其余稳定工具恒可调用
    （docs/dev/SPEC-agent-interaction.md「保护项修订」）。

    @return 允许时为空字符串，禁止时为英文协议错误。
    """
    if not menu_allows_action(menu, action, request):
        return _denied(f"interaction menu denies {action}")
    return ""


def _invalid_menu(message: str, cause: Exception | None = None):
    """@brief 记录英文完整性错误并让非只读人物失败关闭。"""
    logging.error(message)
    if cause is None:
        raise RuntimeError(message)
    raise RuntimeError("LifeAgent interaction menu is invalid") from cause


def _denied(message: str) -> str:
    """@brief 记录英文宿主菜单拒绝并返回同一协议错误。"""
    logging.error(message)
    return message
