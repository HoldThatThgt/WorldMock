"""@brief 环境层只显示时钟、四项体感、真实位置、天气、今天的工作安排与明天的日期、星期和上不上班。"""

from worldmock.life import sessions
from worldmock.life.models import ActivityKind, SessionKind
from worldmock.life.readable import iso_time
from worldmock.life.travel_methods import travel_method
from worldmock.life.weather import current_weather, WEATHER_LABELS

_BODY_KEYS = ("appetite", "sleepiness", "relaxation", "social")


def environment_layer(context, levels: dict[str, str]) -> dict:
    """@brief 不把内部数量、账户、身份或任意地点类型放进人物提示。"""
    return {"layer": "环境层", "observed_at": iso_time(context.state.current_time, context.config.timezone),
            "body": {key: levels[key] for key in _BODY_KEYS}, "location": _location(context),
            "weather": WEATHER_LABELS[current_weather(context.state)], "duty": _duty(context),
            "tomorrow": _tomorrow(context)}


def _location(context) -> dict:
    """@brief 地点类型是数据集自由文本，不派生中文类型或菜单规则。"""
    session = sessions.current_session(context.state, context.agent_id)
    agent = context.state.agents[context.agent_id]
    if session is not None and session.kind == SessionKind.TRANSIT:
        travel = next(context.state.activities[key] for key in session.activity_ids
                      if context.state.activities[key].kind == ActivityKind.TRAVEL)
        destination = context.catalog.locations[travel.destination_id].display_name
        return {"session_kind": "transit", "name": f"去{destination}的路上",
                "travel_method": travel_method(context.catalog, travel.travel_method_id).name}
    return {"session_kind": "stay", "name": context.catalog.locations[agent.location_id].display_name}


def _duty(context) -> dict | None:
    """@brief 工作安排直接读取实际人设声明，地点显示名来自完整目录。"""
    if context.persona.work_schedule is None:
        return None
    from worldmock.life.work_schedule import duty

    places = {key: row.display_name for key, row in context.catalog.locations.items()}
    return duty(context.persona.work_schedule, context.state.current_time, context.config.timezone, places)


def _tomorrow(context) -> dict:
    """@brief 明天的日期、星期名与上不上班，按此刻所在本地日的下一天读实际人设声明；没有工作安排时 day 为 None。"""
    from worldmock.life.work_schedule import tomorrow

    places = {key: row.display_name for key, row in context.catalog.locations.items()}
    return tomorrow(context.persona.work_schedule, context.state.current_time, context.config.timezone, places)
