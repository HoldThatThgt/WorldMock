"""@brief 工具面一致性：人物侧开放的世界请求工具与只读查询工具，和引擎侧的请求模型与只读查询白名单是同一份；运行时不再核对，由本用例守住（docs/design/06-agent.md「提示与工具」）。"""

from worldmock.agents.life_agent import life_action_tool_names, life_query_tool_names
from worldmock.simulation_engine.decisions.read_query import _READ_QUERY_MODELS
from worldmock.state.interaction.requests import QUERY_MODELS, REQUEST_MODELS


def test_agent_and_engine_share_one_tool_surface():
    """请求模型恰为七个工具名；人物侧动作工具集等于它；人物侧只读查询工具集等于引擎白名单的工具名，恰为回看任务与
    检索记忆；白名单两项的请求类型就是 REQUEST_MODELS 与 QUERY_MODELS 里的同一对类型。"""
    assert frozenset(REQUEST_MODELS) == frozenset({
        "start_main_activity",
        "start_side_activity",
        "recall_task_summary",
        "report_status",
        "pause_activity",
        "conclude_activity",
        "revise_day_plan",
    })
    assert life_action_tool_names() == frozenset(REQUEST_MODELS)
    assert life_query_tool_names() == frozenset(_READ_QUERY_MODELS)
    assert frozenset(_READ_QUERY_MODELS) == {"recall_task_summary", "search_memory"}
    assert _READ_QUERY_MODELS == {"recall_task_summary": REQUEST_MODELS["recall_task_summary"], **QUERY_MODELS}
