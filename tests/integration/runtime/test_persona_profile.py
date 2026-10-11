"""人物卡片由 persona.toml 直接构造：人设层、记忆提示、零点计划、身体和评估读到同一份值，新建运行不为它调用模型。"""

from tests.data_paths import ENVIRONMENT_ROOT
import json
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.input_models import PersonaConfig
from tests.integration.runtime.current_contract_fixtures import inputs, profile as current_profile
from tests.integration.runtime.test_native_dataset_lifecycle import native_runtime  # noqa: F401
from worldmock.run_setup import creation_inputs


SCENARIO = ENVIRONMENT_ROOT

#: 人设表里与夹具不同、都不在格点上的作息和卡片字段（到家 18:15–18:45，就寝越过午夜）。
DECLARED = {"chronotype_midsleep": 4.1, "sleep_need_hours": 7.6, "social_need_growth_per_hour": 0.09,
            "wake_window": (6.75, 7.25), "leave_home_window": (8.1, 8.4), "lunch_window": (11.75, 12.75),
            "leave_work_window": (18.0, 18.5), "arrive_home_window": (18.25, 18.75),
            "dinner_window": (19.0, 20.0), "bedtime_window": (23.5, 24.5), "coping": "去江边散步"}
#: 上面这份人设表写到分钟的作息。
DECLARED_RHYTHM = {"wake": "6点三刻到7点一刻", "leave_home": "8点6分到8点24分", "lunch": "11点三刻到12点三刻",
                   "leave_work": "18点到18点半", "arrive_home": "18点一刻到18点三刻", "dinner": "19点到20点",
                   "bedtime": "23点半到0点半", "sleep_midpoint": "4点6分", "sleep_need": "7.6 小时"}


def _declared_environment(tmp_path, **values):
    """夹具环境的副本，人设表换上给定的顶层字段。"""
    from tests.life.environment_inputs import copy_environment
    from tests.life.persona_inputs import declare

    root = copy_environment(tmp_path / "declared-environment")
    declare(root, **values)
    return root


def _loaded(root):
    """按真实完整环境加载一个环境根。"""
    from tests.frozen_inputs import fixture_metadata
    from worldmock.environment import load_environment

    return load_environment(root, metadata_loader=fixture_metadata)


def _declared_inputs(tmp_path, **values):
    """按真实完整环境加载改过人设表的环境副本。"""
    return _loaded(_declared_environment(tmp_path, **values))


def test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer():
    """@brief 夹具人设表经宿主人设层与提示组装保留散文，不依赖其他用例的旧世界观察。"""
    from worldmock.agents.life_persona import persona_layer
    from worldmock.agents.life_prompt import life_user_layers, system_message
    from worldmock.profile.models import persona_card
    from worldmock.profile.textgen import render_card

    frozen = inputs()
    card = persona_card(frozen.persona)
    specification = creation_inputs(frozen)[0][0]
    profile = specification["profile"]
    person = frozen.persona
    persona = persona_layer(profile, person.name, {"relationships": [], "preferences": []})
    layer_names = (("tool_layer", "工具层"), ("memory_layer", "记忆层"), ("todo_layer", "待办层"),
                   ("environment_layer", "环境层"), ("progress_layer", "进度层"))
    observation = {key: {"layer": name} for key, name in layer_names}
    user = json.dumps(life_user_layers(observation, persona), ensure_ascii=False)
    system = system_message(False)["content"]
    assert persona["identity"] == {"name": person.name, "age": 32, "occupation": "产品经理",
                                   "living_arrangement": "与伴侣合租两居室"}
    assert [(row["person"], row["role"]) for row in persona["relationships"]] == [("母亲", "家人"), ("老周", "老友")]
    assert all(row["closeness"] is None and row["channels"] == [] for row in persona["relationships"][2:])
    assert "你不是AI助手" not in system and person.name not in system
    assert profile["persona_card"] == render_card(card)
    assert render_card(card) not in user and card.situation.busy_this_week not in user
    assert persona["tastes"] == card.tastes.model_dump(mode="json")
    for stale in ('"profile"', '"trait_source_kind"', '"persona_card"', '"persona_card_struct"', '"situation"',
                  '"taste_weights"', '"attention_prior"', '"background_story"'):
        assert stale not in user


@pytest.mark.parametrize('field', ('occupation', 'routine_preference',
                                   'consumption_preference', 'risk_preference'))
def test_deleted_template_fields_stay_out_of_documents(field):
    """docs/design/06-agent.md「人物身份与画像」：四个顶层模板字段删除；文档中再次出现即被封闭模型拒绝。"""
    value = inputs().persona.model_dump(mode="json")
    value[field] = 'legacy'
    with pytest.raises(ValidationError):
        PersonaConfig.model_validate(value)


def test_initial_agent_state_and_profile_do_not_keep_numeric_tastes():
    """@brief 真实初态与工作区输入均无口味权重；严格模型也拒绝重新注入旧字段。"""
    from worldmock.life.models import AgentState
    from worldmock.life.world_setup import _initial_agent

    frozen = inputs()
    state = _initial_agent(frozen, int(frozen.config.start_time.timestamp()))
    profile = current_profile()
    assert "taste_weights" not in state.model_dump(mode="json") and "taste_weights" not in profile
    assert "attention_prior" not in json.dumps(profile)
    with pytest.raises(ValidationError, match="taste_weights"):
        AgentState.model_validate({**state.model_dump(mode="python"), "taste_weights": {"content": 1.0}})


@pytest.mark.parametrize("profile", [{"taste_weights": {}}, {"taste_weights": {"content": 1.0},
                                                              "persona_card_struct": None}])
def test_restored_persona_profile_rejects_retired_taste_weights(profile):
    """@brief 恢复时直接拒绝旧数值口味，即使没有结构化卡片也不保留。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    with pytest.raises(IntegrityError, match="retired taste weights"):
        _validate_persona_card_struct(profile, inputs().persona)


def _tampered(struct: dict, kind: str) -> object:
    """把一份结构化卡片改坏一处：返回改过的副本。"""
    changed = json.loads(json.dumps(struct))
    if kind == "rounded_window":
        changed["time_anchors"]["arrive_home"] = {"start_hour": 18.0, "end_hour": 19.0}
    elif kind == "shifted_bedtime":
        changed["time_anchors"]["bedtime"] = {"start_hour": 47.5, "end_hour": 48.5}
    elif kind == "sleep_need":
        changed["sleep_need_hours"] = 7.5
    elif kind == "relationship":
        changed["relationships"][0]["intimacy"] = 3
    elif kind == "habit":
        changed["habits"] = []
    elif kind == "coping":
        changed["coping"] = "拼高达模型"
    elif kind == "extra_field":
        changed["big_five"] = {"extraversion": 6}
    else:
        return {"missing": None, "empty": {}, "list": []}[kind]
    return changed


@pytest.mark.parametrize("kind", ["rounded_window", "shifted_bedtime", "sleep_need", "relationship", "habit",
                                  "coping", "extra_field", "missing", "empty", "list"])
def test_restored_card_must_equal_the_card_built_from_the_frozen_persona(tmp_path, kind):
    """@brief 恢复只留一条核对：工作区里的结构化卡片必须与用冻结的 persona.toml 重新构造出的卡片逐值相等；
    取整过的时段、平移过的就寝、别的睡眠需要、改过的关系习惯或应对方式、多出的字段、缺失或损坏的卡片都停止恢复。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    declared = _declared_inputs(tmp_path, **DECLARED)
    profile = creation_inputs(declared)[0][0]["profile"]
    _validate_persona_card_struct(profile, declared.persona)
    broken = {**profile, "persona_card_struct": _tampered(profile["persona_card_struct"], kind)}
    with pytest.raises(IntegrityError, match="Restored persona card differs from persona.toml"):
        _validate_persona_card_struct(broken, declared.persona)
    for unusable in (None, [], "card"):
        with pytest.raises(IntegrityError, match="Restored persona card differs from persona.toml"):
            _validate_persona_card_struct(unusable, declared.persona)


def test_a_card_frozen_for_another_persona_table_does_not_restore(tmp_path):
    """@brief 同一份工作区卡片换一份人设表就不相等：夹具人物的卡片不能恢复到改过作息的人设表上。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    declared = _declared_inputs(tmp_path, **DECLARED)
    with pytest.raises(IntegrityError, match="Restored persona card differs from persona.toml"):
        _validate_persona_card_struct(current_profile(), declared.persona)
    _validate_persona_card_struct(current_profile(), inputs().persona)


async def _created_run(tmp_path, native_runtime, run_id: str, **declared):
    """经真实的运行创建路径（run_world、原生 Society 与工作区）用改过人设表的环境新建一次运行。"""
    from tests.integration.runtime.current_contract_fixtures import ROOT
    from tests.life.persona_inputs import declare
    from worldmock.footprints.store import FootprintStore
    from worldmock.runtime import RunRequest, run_world

    root, _, models = native_runtime
    declare(root, **declared)
    result = await run_world(RunRequest(ROOT, root, tmp_path / run_id, run_id, run_id))
    store = FootprintStore(result.run_dir)
    try:
        events = store.read_events(store.last_event_seq)
        manifest = store.manifest
    finally:
        store.close()
    assert not any(event.event_type == "run_failed" for event in events)
    profile = json.loads((result.run_dir / "as2/agents/agent_0001/AGENT.json").read_text(encoding="utf-8"))["profile"]
    plan = next(event.data["request"] for event in events
                if event.event_type == "model_request" and event.data.get("purpose") == "plan_day")
    return {"root": root, "result": result, "events": events, "manifest": manifest, "profile": profile,
            "plan": json.dumps(plan, ensure_ascii=False), "models": models,
            "persona_layer": _sent_persona_layer(models[0].requests[0])}


def _sent_persona_layer(request: dict) -> dict:
    """第一次生活决策实际发给模型的人设层（用户消息里的那一层 JSON）。"""
    def layers(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from layers(child)
        elif isinstance(value, list):
            for child in value:
                yield from layers(child)

    for message in request["messages"]:
        if message["role"] == "user" and isinstance(message["content"], str):
            for layer in layers(json.loads(message["content"])):
                if layer.get("layer") == "人设层":
                    return layer
    raise AssertionError("The first life decision request carries no persona layer")


@pytest.mark.asyncio
async def test_new_run_makes_no_model_request_for_the_persona_and_shows_the_table_in_the_persona_layer(
        tmp_path, native_runtime, monkeypatch):
    """@brief 人设层这一处：真实新建运行里第一次决策发给模型的人设层，就是 persona.toml 里写的作息、身份、习惯、口味和
    应对方式。新建运行不为人物卡片调用模型：独立模型客户端一旦被构造或调用就让运行失败，落库的模型请求只有零点计划和
    人物自己的决策；工作区里的卡片、清单的习惯反应文本都来自人设表，运行能从检查点恢复。"""
    from tests.integration.runtime.current_contract_fixtures import ROOT
    from worldmock import model_endpoint
    from worldmock.profile.models import persona_card, persona_card_struct
    from worldmock.runtime import ResumeRequest, resume_world

    def refuse(*arguments, **keywords):
        raise AssertionError("No model request may be made for the persona card")

    monkeypatch.setattr(model_endpoint.IndependentModelClient, "__init__", refuse)
    monkeypatch.setattr(model_endpoint.IndependentModelClient, "call", refuse)
    run = await _created_run(tmp_path, native_runtime, "persona-parent", **DECLARED)
    loaded = _loaded(run["root"])
    persona = loaded.persona
    layer = run["persona_layer"]
    assert layer["rhythm"] == DECLARED_RHYTHM and layer["coping"] == "去江边散步"
    assert layer["identity"] == {"name": "林舟", "age": 32, "occupation": "产品经理",
                                 "living_arrangement": "与伴侣合租两居室"}
    assert [row["response"] for row in layer["habits"]] == ["公园慢跑半小时", "读几页小说"]
    assert layer["tastes"] == {"content_genres": ["悬疑", "纪录片"], "food_preferences": ["川菜", "面食"],
                               "media": ["长视频", "短视频"]}
    assert run["profile"]["persona_card_struct"] == persona_card_struct(persona_card(persona))
    assert run["manifest"]["persona_habit_responses"] == {"1": ["公园慢跑半小时", "读几页小说"]}
    assert "world_dynamics" not in run["manifest"] and run["manifest"]["input_warnings"] == []
    requests = [event for event in run["events"] if event.event_type == "model_request"]
    assert requests and requests[0].data.get("purpose") == "plan_day"
    assert all(event.data.get("purpose") == "plan_day" or event.decision_id for event in requests)
    first = run["models"][0].requests[0]
    assert {"start_main_activity", "start_side_activity"} <= {tool["function"]["name"] for tool in first["tools"]}

    checkpoint = next(event.data["checkpoint_id"] for event in run["events"]
                      if event.event_type == "checkpoint_created")
    restored = await resume_world(ResumeRequest(ROOT, run["root"], run["result"].run_dir, checkpoint,
                                                tmp_path / "persona-child"))
    assert restored.current_time == loaded.config.stop_time


@pytest.mark.asyncio
async def test_memory_prompts_carry_the_card_text_rendered_from_the_persona_table(tmp_path):
    """@brief 记忆提示这一处：真实 LifeAgent 的记忆巩固请求里的 persona_card，就是由 persona.toml 构造的卡片渲染出的
    文本，时刻写到分钟；人物工作区里保存的也是同一段文本。"""
    from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig
    from custom.agents.life_agent import LifeAgent
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from worldmock.agents.life_memory import WorldMockMemoryRuntime
    from worldmock.profile.models import persona_card
    from worldmock.profile.textgen import render_card

    declared = _declared_inputs(tmp_path, **DECLARED)
    profile = creation_inputs(declared)[0][0]["profile"]
    requests = []

    async def dispatch(**request):
        requests.append(request)
        message = SimpleNamespace(content="我想保留这些记忆。", tool_calls=None, reasoning_content="")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    agent = LifeAgent()
    agent._id, agent._name, agent._profile, agent._model_name = 1, "林舟", profile, "test-model"
    agent._dispatch_memory_llm, agent.trace_span = dispatch, None
    runtime = WorldMockMemoryRuntime(agent, MemoryRuntimeConfig())
    store = runtime.ensure_store(tmp_path / "memory")
    moment = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
    store.append_episodes([{"type": "goal", "importance": 0.7, "text": "我想周末去江边走走",
                            "epistemic_status": "subjective", "source": "activity_conclusion"}],
                          tick=60, t=moment, step_count=1)
    assert await runtime._consolidate_subjective(60, moment, 1, "daily reflection")
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["persona_card"] == profile["persona_card"] == render_card(persona_card(declared.persona))
    for phrase in ("约6点三刻到7点一刻起床、8点6分到8点24分出门", "午餐11点三刻到12点三刻、晚餐19点到20点",
                   "就寝23点半到0点半，睡眠中点约4点6分，需睡7.6小时；下班18点到18点半；到家18点一刻到18点三刻",
                   "压力时去江边散步", "本周在忙新版上线收尾"):
        assert phrase in payload["persona_card"]


def test_midnight_plan_input_reads_the_persona_table(tmp_path, engine_factory):
    """@brief 零点计划这一处：真实引擎装配的计划输入里，人物段的背景、午饭、下班、到家、晚饭、就寝时段是
    persona.toml 的原值，分钟与人设层相同；没有写的下班、到家、健康习惯与社交风格不出现。"""
    from worldmock.life.day_plan_inputs import plan_inputs

    story = "林舟住在公司附近，喜欢去江边散步。"
    engine = engine_factory(environment_root=_declared_environment(tmp_path, **DECLARED, background_story=story,
                                                                   health_habits="晚饭后不喝咖啡"))
    planned = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["人物"]
    assert planned == {"名字": "林舟", "背景": story, "健康习惯": "晚饭后不喝咖啡", "午饭时段": "11:45–12:45",
                       "下班时段": "18:00–18:30", "到家时段": "18:15–18:45", "晚饭时段": "19:00–20:00",
                       "就寝时段": "23:30–00:30"}
    plain = engine_factory()
    fixture = plan_inputs(plain.context, plain.observations, plain.midnight_rhythm_pending, 1).payload["人物"]
    assert set(fixture) == {"名字", "背景", "午饭时段", "晚饭时段", "就寝时段"}
    assert (fixture["午饭时段"], fixture["晚饭时段"], fixture["就寝时段"]) == (
        "12:00–13:00", "18:30–19:30", "23:00–00:00")


def test_body_reads_meal_windows_sleep_rhythm_and_social_growth_from_the_persona_table(tmp_path, engine_factory):
    """@brief 身体这一处：真实引擎的饭点窗口、困意用的睡眠中点、欠觉用的睡眠需要和社交需要增长率都是 persona.toml
    的原值；饭点加成恰在写的午饭、晚饭时段里出现，清醒一小时社交需要长的就是写的那个量。"""
    from worldmock.life import felt
    from worldmock.life.body import settle_shared_body
    from worldmock.life.body_config import assemble_body_config

    engine = engine_factory(environment_root=_declared_environment(tmp_path, **DECLARED))
    windows = engine.meal_windows_by_agent()[1]
    assert (windows.lunch, windows.dinner) == ((11.75, 12.75), (19.0, 20.0))
    hungry = {hour: felt.compute_appetite(1000.0, hour, windows) - felt.compute_appetite(1000.0, hour)
              for hour in (11.74, 11.75, 12.74, 12.75, 18.99, 19.0, 19.99, 20.0)}
    assert [round(value, 9) for value in hungry.values()] == [0.0, 0.15, 0.15, 0.0, 0.0, 0.15, 0.15, 0.0]
    body = engine.context.state.agents[1].body
    assert (body.chronotype_midsleep, body.sleep_need_hours, body.social_need_growth_per_hour) == (4.1, 7.6, 0.09)
    settled = settle_shared_body(body, (), 3600, assemble_body_config(engine.context.persona)).after
    assert settled.social_need == pytest.approx(body.social_need + 0.09)
    assert settled.sleep_deficit_hours == pytest.approx(body.sleep_deficit_hours + 7.6 / (24 - 7.6))


def test_evaluation_reads_the_work_windows_from_the_persona_table(tmp_path):
    """@brief 评估这一处：下班、到家窗口是 persona.toml 的原值（到家 18:15–18:45），18:50 到家记晚 5 分钟、18:05 到家
    记早 10 分钟；没有写这两个时段的夹具人物偏离为空。"""
    from tests.integration.evaluation.test_workload_metrics import CODE, ZONE, _Events, _at
    from worldmock.evaluation.behavior_metrics import behavior_metrics_report
    from worldmock.evaluation.workload_metrics import workday_inputs

    def deviation(workday, clock: str):
        events = _Events()
        events.body(1, _at(0, "07:00"), _at(0, "22:00"))
        events.trip(1, _at(0, "08:20"), _at(0, "08:50"), ("home", "office"))
        events.trip(1, _at(0, "18:00"), _at(0, clock), ("office", "home"))
        reports = behavior_metrics_report(events.rows, timezone=ZONE, workday=workday)[CODE]["reports"]
        return reports["routine_deviation"]["agents"]["1"]["days"][0]["arrive_home_vs_window_minutes"]

    workday = workday_inputs(_declared_inputs(tmp_path, **DECLARED))
    assert workday.agents[1].leave_work_window == (18.0, 18.5)
    assert workday.agents[1].arrive_home_window == (18.25, 18.75)
    assert [deviation(workday, clock) for clock in ("18:50", "18:05", "18:30")] == [5.0, -10.0, 0.0]
    fixture = workday_inputs(inputs())
    assert (fixture.agents[1].leave_work_window, fixture.agents[1].arrive_home_window) == (None, None)
    assert deviation(fixture, "18:50") is None


def test_a_table_whose_card_text_exceeds_the_budget_loads_but_fails_when_the_card_is_built(tmp_path):
    """@brief 600 汉字预算仍在构造卡片时检查：口味里写成长句的人设表通过加载，新建运行的工作区输入如实报错，不截断。"""
    from worldmock.profile.textgen import RenderBudgetExceededError

    long_tastes = {"content_genres": ["纪" * 300, "录" * 300], "food_preferences": ["川菜"], "media": ["长视频"]}
    declared = _declared_inputs(tmp_path, tastes=long_tastes)
    assert declared.persona.tastes.content_genres == ("纪" * 300, "录" * 300)
    with pytest.raises(RenderBudgetExceededError, match="input-side problem, not truncated"):
        creation_inputs(declared)


@pytest.mark.parametrize("declared,layer,plan", [
    ({"bedtime_window": (5.0, 7.0)}, {"bedtime": "5点到7点"}, {"就寝时段": "05:00–07:00"}),
    ({"dinner_window": (19.0, 20.0), "bedtime_window": (19.5, 20.5)},
     {"dinner": "19点到20点", "bedtime": "19点半到20点半"}, {"晚饭时段": "19:00–20:00", "就寝时段": "19:30–20:30"}),
    ({"dinner_window": (19.0, 20.0), "bedtime_window": (17.0, 18.0)},
     {"dinner": "19点到20点", "bedtime": "17点到18点"}, {"晚饭时段": "19:00–20:00", "就寝时段": "17:00–18:00"}),
    ({"leave_work_window": (25.0, 25.5), "arrive_home_window": (25.75, 26.1)},
     {"leave_work": "1点到1点半", "arrive_home": "1点三刻到2点6分"}, {"下班时段": "01:00–01:30", "到家时段": "01:45–02:06"}),
    ({"lunch_window": (12.0, 13.5), "dinner_window": (13.0, 14.0)},
     {"lunch": "12点到13点半", "dinner": "13点到14点"}, {"午饭时段": "12:00–13:30", "晚饭时段": "13:00–14:00"}),
    ({"lunch_window": (6.0, 20.5), "wake_window": (10.0, 10.5)},
     {"lunch": "6点到20点半", "wake": "10点到10点半", "leave_home": "8点到8点半"}, {"午饭时段": "06:00–20:30"}),
])
def test_formerly_rejected_windows_reach_the_persona_layer_and_the_plan_as_written(
        tmp_path, engine_factory, declared, layer, plan):
    """@brief 修订前被成对关系、上界或跨度拒绝的时段，现在通过加载并照写的样子到达人物：人设层和卡片文本按钟面写到分钟
    （大于 24 的小时数对一天取模），零点计划写同一分钟，卡片里存的仍是原值；引擎不替作者挪动或纠正任何时段。"""
    from worldmock.agents.life_persona import persona_layer
    from worldmock.life.day_plan_inputs import plan_inputs

    root = _declared_environment(tmp_path, **declared)
    loaded = _loaded(root)
    profile = creation_inputs(loaded)[0][0]["profile"]
    rhythm = persona_layer(profile, loaded.persona.name, {"relationships": [], "preferences": []})["rhythm"]
    assert {name: rhythm[name] for name in layer} == layer
    assert all(text in profile["persona_card"] for text in layer.values())
    anchors = profile["persona_card_struct"]["time_anchors"]
    for name, (start, end) in declared.items():
        assert anchors[name.removesuffix("_window")] == {"start_hour": start, "end_hour": end}
    engine = engine_factory(environment_root=root)
    planned = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["人物"]
    assert {name: planned[name] for name in plan} == plan
