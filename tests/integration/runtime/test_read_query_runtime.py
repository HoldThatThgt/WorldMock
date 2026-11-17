"""@brief 引擎只读查询 LifeEngine.read_query 的证据：白名单、核对次序、唯一冻结观察、五字段业务拒绝、不写事件不改世界，以及人物 tool 消息就是引擎的回答（docs/design/10-runtime.md）。"""

import json
from types import SimpleNamespace

import pytest

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.life.conftest import context, do_at, stage_day_plan
from tests.life.environment_runtime import act
from worldmock.foundation.errors import BusinessRuleError, ProtocolError
from worldmock.simulation_engine.decisions import read_query as read_query_module
from worldmock.simulation_engine.observation.wording.guidance import query_rejection
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, ReviseDayPlanRequest, SearchMemoryRequest
from worldmock.state.planning.schedule import PlanGoal

_ERROR_KEYS = {'error_code', 'reason', 'fix', 'items', 'unmet'}


class _Recall(RecallTaskSummaryRequest):
    """回看请求的子类：白名单按请求类型恒等核对，子类不算白名单里的类型。"""


def _logged(caplog, message: str) -> bool:
    """caplog 里是否有一条 ERROR 级、文字恰为 message 的记录。"""
    return any(record.levelname == 'ERROR' and record.getMessage() == message for record in caplog.records)


def _goal(engine) -> None:
    """预置一条当日目标「歇一歇」，回看任务才有可回看的目标。"""
    stage_day_plan(engine, goals=(PlanGoal(intent_kind='休息', goal='歇一歇',
                                         registered_at=engine.context.state.current_time),))


def test_read_query_answers_task_recall_and_memory_search_only(engine_factory, caplog):
    """引擎只读查询只回答回看任务与检索记忆：白名单恰为这两个工具名与各自的请求类型。工具名写已删除的 query_world 或
    memory_recent、或工具名对而请求类型不对（search_memory 配回看请求、recall_task_summary 配检索请求或计划修订、
    recall_task_summary 配回看请求的子类），都是协议违约，原文相同，抛出前写英文错误日志；两种合法查询照常回答。全程
    不写事件、不改世界状态。"""
    assert read_query_module._READ_QUERY_MODELS == {'recall_task_summary': RecallTaskSummaryRequest,
                                                    'search_memory': SearchMemoryRequest}
    engine = engine_factory()
    _goal(engine)
    frozen = context(engine, 'recall-only')
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    recall = RecallTaskSummaryRequest(task_name=observed['goal_bindings'][0]['task_name'])
    before, sequence = engine.context.state.model_dump_json(), engine.context.store.last_event_seq
    search = SearchMemoryRequest(category='饮食', time='今天', keyword=None)
    for tool, request in (('query_world', recall), ('memory_recent', recall), ('search_memory', recall),
                          ('recall_task_summary', search), ('recall_task_summary', ReviseDayPlanRequest(goals=[])),
                          ('recall_task_summary', _Recall(task_name=recall.task_name))):
        with pytest.raises(Exception) as caught:
            engine.read_query(frozen, tool, request)
        assert caught.type is ProtocolError, (tool, caught.value)
        assert str(caught.value) == 'Read query tool and request type do not match', tool
    assert _logged(caplog, 'Read query tool and request type do not match: query_world')
    recalled = engine.read_query(frozen, 'recall_task_summary', recall)
    assert recalled['recall']['kind'] == 'goal'
    searched = engine.read_query(frozen, 'search_memory', search)
    assert searched == {'类别': '饮食', '时间': '今天', '关键词': None, '结果': [], '未列出': []}
    assert engine.context.state.model_dump_json() == before
    assert engine.context.store.last_event_seq == sequence


def test_read_query_requires_a_valid_context_and_the_open_round(engine_factory):
    """核对次序：先核对决策上下文，再核对开放轮。没开轮时，不是 DecisionContext 的上下文得到类型错误，有效上下文得到
    「轮次没开」；同一身份的一轮开轮、观察、登记并提交之后再用原上下文，得到世界版本过期。三次查询都不写事件。"""
    engine = engine_factory()
    store = engine.context.store
    frozen = context(engine, 'context-check')
    request = RecallTaskSummaryRequest(task_name='歇一歇')
    for given, message in ((SimpleNamespace(round_id='context-check'), 'Context must use the DecisionContext type'),
                           (frozen, 'Decision round is not open')):
        sequence = store.last_event_seq
        with pytest.raises(ProtocolError) as caught:
            engine.read_query(given, 'recall_task_summary', request)
        assert str(caught.value) == message
        assert store.last_event_seq == sequence
    act(engine, 'context-check', do_at('家', '测试休息'))
    sequence = store.last_event_seq
    with pytest.raises(ProtocolError) as caught:
        engine.read_query(frozen, 'recall_task_summary', request)
    assert str(caught.value) == 'Decision context world version is stale'
    assert store.last_event_seq == sequence


def test_read_query_requires_exactly_one_frozen_observation(engine_factory, caplog):
    """开轮而不观察：白名单核对在观察核对之前，白名单外的工具名得到工具与请求类型不符；合法组合找不到唯一冻结观察，
    抛出协议错误前写英文错误日志（找到 0 条）。同一决策观察两次之后，按已绑定的目标名回看同样是协议违约（找到 2 条），
    不从其中一条回答。几次查询都不写事件。"""
    engine = engine_factory()
    _goal(engine)
    frozen = context(engine, 'unobserved')
    engine.begin_decision_round([frozen])
    sequence = engine.context.store.last_event_seq
    request = RecallTaskSummaryRequest(task_name='歇一歇')
    with pytest.raises(ProtocolError) as caught:
        engine.read_query(frozen, 'query_world', request)
    assert str(caught.value) == 'Read query tool and request type do not match'
    with pytest.raises(ProtocolError) as caught:
        engine.read_query(frozen, 'recall_task_summary', request)
    assert str(caught.value) == 'Read query requires one frozen decision observation'
    assert _logged(caplog, 'Read query requires one frozen decision observation, found 0')
    assert engine.context.store.last_event_seq == sequence
    observed = engine.observe(frozen)
    engine.observe(frozen)
    bound = RecallTaskSummaryRequest(task_name=observed['goal_bindings'][0]['task_name'])
    sequence = engine.context.store.last_event_seq
    with pytest.raises(ProtocolError, match='^Read query requires one frozen decision observation$'):
        engine.read_query(frozen, 'recall_task_summary', bound)
    assert _logged(caplog, 'Read query requires one frozen decision observation, found 2')
    assert engine.context.store.last_event_seq == sequence


def test_read_query_business_rejection_keeps_the_five_field_shape(engine_factory):
    """回看一个不存在的任务是业务拒绝：回答恰为五个字段、依此键序，错误码 TASK_NOT_FOUND 并点名所写的任务名；序列化与
    引擎回看同一请求抛出的业务错误经 query_rejection 得到的结构逐字节相同。"""
    engine = engine_factory()
    frozen = context(engine, 'refused')
    engine.begin_decision_round([frozen])
    observed = engine.observe(frozen)
    request = RecallTaskSummaryRequest(task_name='并不存在的任务')
    answer = engine.read_query(frozen, 'recall_task_summary', request)
    assert list(answer) == ['error_code', 'reason', 'fix', 'items', 'unmet']
    assert answer['error_code'] == 'TASK_NOT_FOUND'
    assert answer['items'][0]['written'] == '并不存在的任务' and answer['unmet'] == []
    with pytest.raises(BusinessRuleError) as refused:
        engine.read_task_summary(1, request, observed)
    assert json.dumps(answer, ensure_ascii=False) == json.dumps(query_rejection(refused.value), ensure_ascii=False)


@pytest.mark.asyncio
async def test_host_tool_message_is_the_environment_recall_result(engine_factory, monkeypatch):
    """宿主一半（回看任务送到引擎并取得真实结果）：真实引擎、SQLite 与 LifeAgent 跑真实回合循环，脚本模型先按唯一的
    目标名回看、再按两个目标共用的名称回看、最后提交世界请求。两次回看各自的 tool 消息就是引擎对同一冻结观察给出的
    结果：受理的那次逐值等于 LifeEngine.read_query 与 engine.read_task_summary（首键 kind 为 goal，带目标文字与意图），
    业务拒绝的那次是 TASK_NOT_FOUND 的错误结构——都不是空对象，也不是登记确认；回看不登记世界请求、不写动作事件，
    随后的世界请求照常登记一次。"""
    from tests.integration.runtime.test_same_second_continuation import _PLAN, _Script, _person, _reply
    from worldmock.agents.prompt import prompt_capacity

    monkeypatch.setattr(prompt_capacity, 'model_context_length', lambda model, metadata: metadata.context_window)
    engine = engine_factory()
    now = engine.context.state.current_time
    stage_day_plan(engine, goals=tuple(PlanGoal(intent_kind=kind, goal=goal, registered_at=now) for kind, goal in (
        ('用餐', '好好吃顿午饭'), ('休息', '歇一歇'), ('娱乐', '歇一歇'))))
    decision = context(engine, 'host-recall')
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    assert [row['task_name'] for row in observed['goal_bindings']] == ['好好吃顿午饭', '歇一歇', '歇一歇']
    unique, shared = (RecallTaskSummaryRequest(task_name=name) for name in ('好好吃顿午饭', '歇一歇'))
    model = _Script([_reply('call-recall', 'recall_task_summary', unique.model_dump(), '回看午饭这个目标。'),
                     _reply('call-shared', 'recall_task_summary', shared.model_dump(), '再回看歇一歇。'),
                     _reply('call-plan', 'revise_day_plan', _PLAN, '回看完了，改一下计划。')])
    person = _person(engine, decision, model)
    assert type(person).__module__ == 'worldmock.agents.life_agent'
    sequence = engine.context.store.last_event_seq

    registered = await person.run_turns(decision, observed)

    assert [item.action for item in registered] == ['revise_day_plan'] and len(model.requests) == 3
    tools = [request['messages'][-1] for request in model.requests[1:]]
    assert [(tool['role'], tool['tool_call_id']) for tool in tools] == [
        ('tool', 'call-recall'), ('tool', 'call-shared')]
    recalled, refused = (json.loads(tool['content']) for tool in tools)
    assert recalled == engine.read_query(decision, 'recall_task_summary', unique)
    assert recalled == engine.read_task_summary(1, unique, observed)
    assert list(recalled) == ['recall'] and next(iter(recalled['recall'])) == 'kind'
    assert recalled['recall'] == {'kind': 'goal', 'goal': '好好吃顿午饭', 'intent_kind': '用餐'}
    assert refused == engine.read_query(decision, 'recall_task_summary', shared)
    assert set(refused) == _ERROR_KEYS and refused['error_code'] == 'TASK_NOT_FOUND'
    assert refused['items'][0]['written'] == '歇一歇' and refused['unmet'] == []
    added = [event for event in list(engine.context.store.iter_events(engine.context.store.last_event_seq))
             if event.event_seq > sequence]
    assert [event.event_type for event in added] == [
        'decision_started', *(['model_request', 'model_response'] * 3), 'action_requested']
    assert added[-1].data['tool_name'] == 'revise_day_plan'
