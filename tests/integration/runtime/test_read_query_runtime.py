"""@brief 只读查询转发的四类证据（docs/design/10-runtime.md 「证据分工」）。

1. 真实 Ray：驱动侧与 worker 侧各经代理调用一次，回看任务返回结果字典而非对象引用，世界状态逐字节不变、不追加事件；
2. 主进程内直接构造未经 ray.remote 包装的 LifeEnvActor 并调用 read_query，等于环境直调（守住函数体与私有成员依赖）；
3. 不启动 Ray 的并发度断言：actor 类固定 max_concurrency=1，不提供参数；
4. 运行装配（worldmock.runtime._build_society）建立的是 LifeEnvActor 与带 read_query 的 LifeEnvProxy。
覆盖率门禁只计主进程执行的行，变异执行器也只对主进程生效，所以四类缺一不可。
"""

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT, ENVIRONMENT_ROOT

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import ray

os.environ.setdefault('AGENTSOCIETY_LLM_API_KEY', 'test-only')
os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')

from agentsociety2.env.direct_env_actor import DirectEnvActorSpec
from custom.envs.life_env import LifeEnv
from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan)  # noqa: F401
from worldmock.errors import ProtocolError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import RecallTaskSummaryRequest
from worldmock.life.read_query_runtime import LifeEnvActor, LifeEnvProxy, life_env_actor_class
from worldmock.life.schedule import PlanGoal
from worldmock.life.world_query import QueryWorldRequest, query_world

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT / 'scenarios' / 'single-person.json'
_RECALL_SCENARIO = ARTIFICIAL_ENVIRONMENT_ROOT / 'scenarios' / 'single-person.json'
_ERROR_KEYS = {'error_code', 'reason', 'fix', 'items', 'unmet'}
#: 真实 Ray 用例给 raylet 启动取系统配置的等待秒数（Ray 的 RAY_raylet_start_wait_time_s 配置，经子进程环境传入）。
_RAYLET_START_WAIT_SECONDS = 180


async def _staged_recall(engine_factory) -> SimpleNamespace:
    """经真实引擎备好一份带当日目标的世界，以直接导入的 LifeEnv 求得回看结果作为环境直调的基准；随后关闭引擎，
    把运行目录留给 actor 重新打开。返回基准结果、关闭前的世界状态字节与事件序号、给 actor 用的新决策上下文与请求。"""
    engine = engine_factory()
    stage_day_plan(engine, goals=(PlanGoal(intent_kind='休息', goal='歇一歇',
                                         registered_at=engine.context.state.current_time),))
    direct = context(engine, 'direct-recall')
    engine.begin_decision_round([direct])
    observed = engine.observe(direct)
    request = RecallTaskSummaryRequest(task_name=observed['goal_bindings'][0]['task_name'])
    environment = LifeEnv()
    environment.engine = engine
    expected = await environment.read_query(direct, 'recall_task_summary', request)
    assert expected['recall']['kind'] == 'goal'
    staged = SimpleNamespace(
        expected=expected, request=request, run_dir=engine.context.store.run_dir,
        state=engine.context.state.model_dump_json().encode('utf-8'), sequence=engine.context.store.last_event_seq,
        frozen=context(engine, 'forwarded-recall'))
    engine.close()
    return staged


def _actor_spec(run_dir: Path) -> DirectEnvActorSpec:
    """环境 actor 的构造输入：打开已有运行目录上的正式场景，形状同运行装配。"""
    return DirectEnvActorSpec(
        ('LifeEnv',), {'LifeEnv': {'scenario_path': str(_RECALL_SCENARIO), 'run_dir': str(run_dir), 'open_existing': True}},
        str(run_dir), str(ROOT), {})


def _assert_world_untouched(staged: SimpleNamespace) -> None:
    """重新打开运行目录：世界状态逐字节等于查询之前；序号只多出 actor 一侧那一次观察，查询本身不写任何事件。"""
    reopened = LifeEngine.open(_RECALL_SCENARIO, staged.run_dir)
    try:
        assert reopened.context.state.model_dump_json().encode('utf-8') == staged.state
        added = [event for event in reopened.context.store.read_events(reopened.context.store.last_event_seq)
                 if event.event_seq > staged.sequence]
        assert [(event.event_type, event.decision_id) for event in added] == [
            ('observation', staged.frozen.decision_id)]
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_task_recall_crosses_real_ray_without_world_mutation(engine_factory, monkeypatch):
    """真实代理、actor 与 LifeEnv：驱动侧与 worker 侧经代理各调用一次，都拿到与环境直调相同的结果字典（不是对象
    引用）；代理随任务序列化到 worker 后仍可用；查询不追加足迹、不改世界状态。"""
    monkeypatch.setenv('WORKSPACE_PATH', str(ROOT))
    # 机器繁忙时 raylet 向 GCS 取系统配置可能超出其默认等待而退出，驱动随后报「启动超时」；放宽这一等待只改变
    # 基础设施的启动耐心，不改变本用例证明的内容。
    monkeypatch.setenv('RAY_raylet_start_wait_time_s', str(_RAYLET_START_WAIT_SECONDS))
    staged = await _staged_recall(engine_factory)
    ray.init(num_cpus=1, include_dashboard=False, log_to_driver=False)
    actor = None
    try:
        actor = life_env_actor_class().remote(_actor_spec(staged.run_dir))
        proxy = LifeEnvProxy(actor, run_dir=staged.run_dir, env_module_types=['LifeEnv'])
        await proxy.init(staged.frozen.observed_at)
        await proxy.begin_decision_round((staged.frozen,))
        await proxy.observe(staged.frozen)

        @ray.remote
        def worker_side(remote_proxy, frozen, request):
            """worker 进程里的同步任务：代理作为参数序列化过来，体内以事件循环等待只读查询。"""
            import asyncio

            return asyncio.run(remote_proxy.read_query(frozen, 'recall_task_summary', request))

        driver = await proxy.read_query(staged.frozen, 'recall_task_summary', staged.request)
        assert type(driver) is dict and not isinstance(driver, ray.ObjectRef)
        assert driver == staged.expected
        worker = await worker_side.remote(proxy, staged.frozen, staged.request)
        assert type(worker) is dict and worker == staged.expected
        await proxy.close()
    finally:
        if actor is not None:
            ray.kill(actor, no_restart=True)
        ray.shutdown()
    _assert_world_untouched(staged)


@pytest.mark.asyncio
async def test_unwrapped_actor_read_query_equals_direct_environment_call(engine_factory, monkeypatch):
    """主进程内直接构造 LifeEnvActor（不经 ray.remote）：read_query 把查询交给它持有的唯一生活环境，两个工具的结果
    都等于环境直调；回看被业务拒绝时返回错误结构而不抛出；工具与请求类型不符、找不到唯一观察是协议违约；
    查询不写事件、不改世界状态。"""
    monkeypatch.setenv('WORKSPACE_PATH', str(ROOT))
    staged = await _staged_recall(engine_factory)
    actor = LifeEnvActor(_actor_spec(staged.run_dir))
    try:
        await actor.init(staged.frozen.observed_at)
        (environment,) = actor._router.env_modules
        assert type(environment).__name__ == 'LifeEnv'
        await actor.begin_decision_round((staged.frozen,))
        with pytest.raises(ProtocolError, match='Read query requires one frozen decision observation'):
            await actor.read_query(staged.frozen, 'recall_task_summary', staged.request)
        observed = await actor.observe(staged.frozen)
        engine = environment.engine
        before, sequence = engine.context.state.model_dump_json(), engine.context.store.last_event_seq

        recalled = await actor.read_query(staged.frozen, 'recall_task_summary', staged.request)
        assert type(recalled) is dict and recalled == staged.expected
        assert recalled == await environment.read_query(staged.frozen, 'recall_task_summary', staged.request)

        category = observed['tool_layer']['callable_index']['query_categories'][0]['category']
        query = QueryWorldRequest(**{'name': None, 'way': None, 'category': category, 'app': None,
                                     'available_only': False, 'offset': 0})
        page = await actor.read_query(staged.frozen, 'query_world', query)
        assert page['matches'] and page == query_world(observed, query)
        assert page == await environment.read_query(staged.frozen, 'query_world', query)

        unknown = RecallTaskSummaryRequest(task_name='并不存在的任务')
        refused = await actor.read_query(staged.frozen, 'recall_task_summary', unknown)
        assert set(refused) == _ERROR_KEYS and refused['error_code'] == 'TASK_NOT_FOUND'
        assert refused['items'][0]['written'] == '并不存在的任务' and refused['unmet'] == []
        assert refused == await environment.read_query(staged.frozen, 'recall_task_summary', unknown)

        for tool, request in (('query_world', staged.request), ('recall_task_summary', query),
                              ('memory_recent', staged.request)):
            with pytest.raises(ProtocolError, match='Read query tool and request type do not match'):
                await actor.read_query(staged.frozen, tool, request)

        assert engine.context.state.model_dump_json() == before
        assert engine.context.store.last_event_seq == sequence
    finally:
        await actor.close()
    _assert_world_untouched(staged)


def test_life_env_actor_class_fixes_single_concurrency_without_parameters(monkeypatch):
    """不启动 Ray：actor 类由 ray.remote(max_concurrency=1) 包装 LifeEnvActor 得到，构造函数不接受任何参数。"""
    captured = []

    def remote(*arguments, **options):
        """记录 ray.remote 的参数，返回记录被包装类的装饰器。"""
        captured.append((arguments, options))
        return lambda wrapped: ('ray-actor-class', wrapped)

    monkeypatch.setattr(ray, 'remote', remote)

    assert life_env_actor_class() == ('ray-actor-class', LifeEnvActor)
    assert captured == [((), {'max_concurrency': 1})]
    assert not inspect.signature(life_env_actor_class).parameters
    with pytest.raises(TypeError):
        life_env_actor_class(2)


@pytest.mark.asyncio
async def test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query(monkeypatch, tmp_path):
    """运行装配不启动 Ray 也可判定：ray.remote 包装的是 LifeEnvActor 且并发度为 1；交给 Society 的环境代理是
    LifeEnvProxy，持有装配出的 actor 句柄，交给人物的服务代理（ServiceProxy.env）是同一个代理；经人物一侧调用
    read_query 把查询送到该句柄并返回等待后的结果对象（代理不等待远端时得到的是协程而不是结果）。装配改回 AS2 原类
    （没有 read_query 的 EnvRouterProxy 或 DirectEnvActor，含只给人物一侧换回 EnvRouterProxy）即失败。"""
    import worldmock.runtime as runtime
    from agentsociety2.config import llm_dispatcher
    from agentsociety2.env import direct_env_actor
    from worldmock.life.world_setup import load_scenario

    wrapped, received = [], []
    answer = {'recall': {'kind': 'goal', 'name': '歇一歇'}}

    async def resolved(*arguments):
        """远端调用的替身：记录参数，等待后给出结果。"""
        received.append(arguments)
        return answer

    handle = SimpleNamespace(read_query=SimpleNamespace(remote=resolved))

    def remote(**options):
        """记录 ray.remote 的参数与被包装的类，返回只产出替身句柄的 actor 类。"""
        return lambda actor_class: (wrapped.append((actor_class, options)),
                                    SimpleNamespace(remote=lambda spec: handle))[1]

    monkeypatch.setattr(ray, 'remote', remote)
    monkeypatch.setattr(direct_env_actor, '_CACHED_ACTOR_CLASSES', {})
    monkeypatch.setenv('WORKSPACE_PATH', str(ROOT))
    monkeypatch.setattr(llm_dispatcher, 'build_client_for_role', lambda role: role)
    scenario, _ = load_scenario(SCENARIO)

    actor, society = runtime._build_society(SimpleNamespace(
        project_root=ROOT, run_dir=tmp_path / 'run', run_id='assembly', env_kwargs={},
        scenario_path=SCENARIO, scenario=scenario, workspace_mode='restored',
        start_time=scenario.start_at, same_second_continuation=True))

    assert actor is handle
    assert wrapped == [(LifeEnvActor, {'max_concurrency': 1})]
    proxy = society._config.env_router
    assert type(proxy) is LifeEnvProxy and proxy._actor is handle
    # 人物不经 env_router：Society 把 service_proxy 交给人物任务，人物的环境取 service_proxy.env（AS2
    # AgentBase._bind_services），回看任务从这里发出，所以交给人物的必须是同一个 Life 代理。
    agent_side = society._config.service_proxy.env
    assert agent_side is proxy
    assert callable(getattr(agent_side, 'read_query', None))
    result = await agent_side.read_query('context', 'recall_task_summary', 'request')
    assert result is answer
    assert received == [('context', 'recall_task_summary', 'request')]


@pytest.mark.asyncio
async def test_host_tool_message_is_the_environment_recall_result(engine_factory, monkeypatch):
    """宿主一半（规格 1.2：回看任务送到环境并等待真实结果）：真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent 跑真实
    ReAct 循环，脚本模型先按唯一的目标名回看、再按两个目标共用的名称回看、最后提交世界请求。两次回看各自的 tool 消息
    就是环境对同一冻结观察给出的结果：受理的那次逐值等于 LifeEnv.read_query 与 engine.read_task_summary（首键 kind 为
    goal，带目标文字、意图与是否已推进），业务拒绝的那次是 TASK_NOT_FOUND 的错误结构——都不是空对象，也不是登记确认；
    回看不登记世界请求、不写动作事件，随后的世界请求照常登记一次。"""
    import json

    from agentsociety2.agent.base import ReactLoopRequest
    from tests.integration.runtime.test_same_second_continuation import _PLAN, _Script, _person, _reply
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, 'model_context_length', lambda model: 1_048_576)
    engine = engine_factory()
    now = engine.context.state.current_time
    stage_day_plan(engine, goals=tuple(PlanGoal(intent_kind=kind, goal=goal, registered_at=now) for kind, goal in (
        ('用餐', '好好吃顿午饭'), ('休息', '歇一歇'), ('娱乐', '歇一歇'))))
    environment = LifeEnv()
    environment.engine = engine
    decision = context(engine, 'host-recall')
    engine.begin_decision_round([decision])
    observed = await environment.observe(decision)
    assert [row['task_name'] for row in observed['goal_bindings']] == ['好好吃顿午饭', '歇一歇', '歇一歇']
    unique, shared = (RecallTaskSummaryRequest(task_name=name) for name in ('好好吃顿午饭', '歇一歇'))
    model = _Script([_reply('call-recall', 'recall_task_summary', unique.model_dump(), '回看午饭这个目标。'),
                     _reply('call-shared', 'recall_task_summary', shared.model_dump(), '再回看歇一歇。'),
                     _reply('call-plan', 'revise_day_plan', _PLAN, '回看完了，改一下计划。')])
    person = _person(environment, decision, model)
    assert type(person).__module__ == 'custom.agents.life_agent'
    sequence = engine.context.store.last_event_seq

    result = await person.run_react_loop(ReactLoopRequest(0, decision.observed_at, [
        {'turn': 0, 'action': 'observe', 'ok': True, 'data': observed}]))

    assert result == 'revise_day_plan registered' and len(model.requests) == 3
    tools = [request['messages'][-1] for request in model.requests[1:]]
    assert [(tool['role'], tool['tool_call_id']) for tool in tools] == [
        ('tool', 'call-recall'), ('tool', 'call-shared')]
    recalled, refused = (json.loads(tool['content']) for tool in tools)
    assert recalled == await environment.read_query(decision, 'recall_task_summary', unique)
    assert recalled == engine.read_task_summary(1, unique, observed)
    assert list(recalled) == ['recall'] and next(iter(recalled['recall'])) == 'kind'
    assert recalled['recall'] == {'kind': 'goal', 'goal': '好好吃顿午饭', 'intent_kind': '用餐', 'progressed': False}
    assert refused == await environment.read_query(decision, 'recall_task_summary', shared)
    assert set(refused) == _ERROR_KEYS and refused['error_code'] == 'TASK_NOT_FOUND'
    assert refused['items'][0]['written'] == '歇一歇' and refused['unmet'] == []
    added = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq) if event.event_seq > sequence]
    assert [event.event_type for event in added] == [
        'decision_started', *(['model_request', 'model_response'] * 3), 'action_requested']
    assert added[-1].data['tool_name'] == 'revise_day_plan'
