"""@brief 当前分配工作内容的真实请求、出行与客观完成辅助。"""

from tests.life.conftest import conclude_request, context, stage_day_plan
from tests.life.test_acs_amended_runtime import perform, pump
from tests.life.test_acs_runtime import request
from worldmock.life.activity_control import is_pending_conclusion


def trip_to(engine, destination, label):
    """@brief 使用人物明确选择的步行到达真实地点并收尾。"""
    place = next(item.display_name for item in engine.context.scenario.locations if item.location_id == destination)
    result = perform(engine, label, request(f"地点：{place}", None, "步行"))
    assert result.error_code is None, result.data
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[result.activity_id]))
    activity = engine.context.state.activities[result.activity_id]
    ended = perform(engine, f"{label}-conclude", conclude_request(activity.name), "conclude_activity")
    assert ended.error_code is None, ended.data


def at_workplace(engine):
    """@brief 人物通过真实出行到自己的明确工作地点。"""
    stage_day_plan(engine)
    place = engine.context.scenario.agents[0].work_schedule.workplace_location_id
    if engine.context.state.agents[1].location_id != place:
        trip_to(engine, place, "arrive-office")


def start_assigned_work(engine, label):
    """@brief 执行环境明确分配的内容所属动作，不点名内容。"""
    at_workplace(engine)
    content_id = engine.context.scenario.agents[0].work_content_ids[0]
    content = engine.context.semantics.catalog.contents[content_id]
    action = engine.context.semantics.catalog.actions[content.action_id]
    result = perform(engine, label, request(action.name, None))
    assert result.error_code is None, result.data
    return content, result.activity_id


def observed(engine, label):
    """@brief 通过实际观察读取当前七层证据。"""
    return engine.observe(context(engine, label))
