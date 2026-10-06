"""@brief 环境层：观察时刻、五项体感分级、所在地点或路程的静态信息与今天的上班安排（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求
「环境层」、docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-环境层-duty ）。

体感取现有分级文字（felt.summarize_felt_levels），数值不进提示。停留时 name 为地点名、place_type 为场所类型的中文标签；
路程中 name 为路段标签「去<目的地>的路上」、place_type 为出行方式（步行、驾车）。fixed_rules 是场所的固定规则：
本轮有效菜单排除的每个动作写一句「这里不能<动作说法>」，菜单完整时为 []。场所类型、动作说法与出行方式三张中文表
的唯一来源是 worldmock.life.readable（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-时间线只出现中文名称 ）；本模块的 PLACE_TYPES、
ACTION_PHRASES
就是那里的对象，照旧可从这里导入。

duty 是今天的上班安排：本人在场景里声明的工作安排按世界时钟求值（worldmock.life.work_schedule.duty），没有工作安排为
null；当日不当班是固定的休息日对象，当班日写班次、工作地点的显示名、休息时段与此刻状态。它只由场景与时钟决定，不看
计划、工作提交或人物在不在场，停留与路程中同样求值，不含内部身份。
"""

from __future__ import annotations

import logging

from worldmock.exchange.types import START_ACTIVITY_ACTIONS
from worldmock.errors import IntegrityError
from worldmock.life import sessions, work_schedule
from worldmock.life.models import ActivityKind, SessionKind
from worldmock.life.readable import ACTION_PHRASES, PLACE_TYPES, TRAVEL_WAYS, iso_time
from worldmock.life.tool_layer import LayerContext

#: 体感五键的固定顺序。
_BODY_KEYS = ("appetite", "sleepiness", "entertainment", "social", "mood")


def environment_layer(context: LayerContext, levels: dict[str, str]) -> dict:
    """@brief 环境层 JSON。

    :param levels: 本次观察的体感分级（projection.body_projection 的 levels）。
    """
    return {"layer": "环境层", "observed_at": iso_time(context.state.current_time, context.scenario.timezone),
            "body": {key: levels[key] for key in _BODY_KEYS}, "location": _location(context),
            "duty": _duty(context)}


def _duty(context: LayerContext) -> dict | None:
    """@brief 今天的上班安排：本人在场景里声明的工作安排按世界时钟求值；没有工作安排为 None。"""
    config = next(item for item in context.scenario.agents if item.agent_id == context.agent_id)
    places = {item.location_id: item.name for item in context.scenario.locations}
    return work_schedule.duty(config.work_schedule, context.state.current_time,
                              context.scenario.timezone, places)


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
