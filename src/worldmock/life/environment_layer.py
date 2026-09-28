"""@brief 环境层：观察时刻、五项体感分级与所在地点或路程的静态信息（docs/dev/SPEC-persona-prompt.md「环境层」）。

体感取现有分级文字（felt.summarize_felt_levels），数值不进提示。停留时 name 为地点名、place_type 为场所类型的中文标签；
路程中 name 为路段标签「去<目的地>的路上」、place_type 为出行方式（步行、驾车）。fixed_rules 是场所的固定规则：
本轮有效菜单排除的每个动作写一句「这里不能<动作说法>」，菜单完整时为 []。
"""

from __future__ import annotations

import logging

from worldmock.catalog.affordances import START_ACTIVITY_ACTIONS
from worldmock.errors import IntegrityError
from worldmock.life import sessions
from worldmock.life.interaction_menu import TRAVEL_WAYS
from worldmock.life.models import ActivityKind, SessionKind
from worldmock.life.readable import iso_time
from worldmock.life.tool_layer import LayerContext

#: 场所类型（地点 kind）→ 中文标签；覆盖仓库场景的全部地点类型，未知类型是场景缺陷。
PLACE_TYPES = {"home": "家", "office": "办公室", "merchant": "商户", "restaurant": "餐馆",
               "convenience_store": "便利店", "supermarket": "超市", "wet_market": "菜市场", "fitness": "健身房",
               "park": "公园"}
#: 内部标准动作的说法（fixed_rules 句子「这里不能<说法>」），按此顺序列出。
ACTION_PHRASES = {"travel_to": "出发去别处", "work": "工作", "watch": "看内容或听播客", "play": "玩游戏",
                  "chat": "当面交谈", "call": "打电话", "read": "看邮件", "browse": "浏览商品",
                  "do": "做生活活动", "purchase": "买东西", "consume": "吃东西或用东西", "wait": "等待",
                  "sleep": "睡觉"}
#: 体感五键的固定顺序。
_BODY_KEYS = ("appetite", "sleepiness", "entertainment", "social", "mood")


def environment_layer(context: LayerContext, levels: dict[str, str]) -> dict:
    """@brief 环境层 JSON。

    :param levels: 本次观察的体感分级（projection.body_projection 的 levels）。
    """
    return {"layer": "环境层", "observed_at": iso_time(context.state.current_time, context.scenario.timezone),
            "body": {key: levels[key] for key in _BODY_KEYS}, "location": _location(context)}


def _location(context: LayerContext) -> dict:
    """@brief 所在地点或路程：会话种类、名称、场所类型或出行方式、固定规则。"""
    state, agent = context.state, context.state.agents[context.agent_id]
    places = {item.location_id: item for item in context.scenario.locations}
    session = sessions.current_session(state, context.agent_id)
    rules = [f"这里不能{phrase}" for action, phrase in ACTION_PHRASES.items()
             if action in START_ACTIVITY_ACTIONS and action not in context.menu.allowed_actions]
    if session is not None and session.kind == SessionKind.TRANSIT:
        travel = next(state.activities[item] for item in session.activity_ids
                      if state.activities[item].kind == ActivityKind.TRAVEL)
        return {"session_kind": "transit",
                "name": sessions.place_label(session, {key: item.name for key, item in places.items()}),
                "place_type": TRAVEL_WAYS[travel.travel_mode.value], "fixed_rules": rules}
    location = places[agent.location_id]
    if location.kind not in PLACE_TYPES:
        logging.error("Location kind has no place type label: %s", location.kind)
        raise IntegrityError("Location kind has no place type label")
    return {"session_kind": "stay", "name": location.name, "place_type": PLACE_TYPES[location.kind],
            "fixed_rules": rules}
