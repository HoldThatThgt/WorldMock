
from tests.data_paths import ENVIRONMENT_ROOT
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.input_models import PersonaConfig
from tests.integration.runtime.current_contract_fixtures import inputs, profile as current_profile, compiled_persona
from tests.integration.runtime.test_native_dataset_lifecycle import native_runtime  # noqa: F401
from worldmock.run_setup import creation_inputs


SCENARIO = ENVIRONMENT_ROOT
TRAITS = ('trait_source_kind',)


def test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer():
    """@brief 真实画像输入经宿主人设层与提示组装保留散文，不依赖其他用例的旧世界观察。"""
    from worldmock.agents.life_persona import persona_layer
    from worldmock.agents.life_prompt import life_user_layers, system_message
    from tests.profile.conftest import make_card
    from worldmock.profile.textgen import render_card

    frozen = inputs()
    card = make_card()
    specification = creation_inputs(frozen, {1: card})[0][0]
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
    assert render_card(card) not in user and card.situation.busy_this_week not in user
    assert persona["tastes"] == card.tastes.model_dump(mode="json")
    for stale in ('"profile"', '"trait_source_kind"', '"persona_card"', '"persona_card_struct"', '"situation"',
                  '"taste_weights"', '"attention_prior"'):
        assert stale not in user


@pytest.mark.parametrize('field', ('occupation', 'routine_preference',
                                   'consumption_preference', 'risk_preference'))
def test_deleted_template_fields_stay_out_of_documents(field):
    """docs/design/06-agent.md「画像编译」：四个模板字段删除；文档中再次出现即被封闭模型拒绝。"""
    value = inputs().persona.model_dump(mode="json")
    value[field] = 'legacy'
    with pytest.raises(ValidationError):
        PersonaConfig.model_validate(value)


def test_initial_agent_state_and_profile_do_not_keep_numeric_tastes():
    """@brief 真实初态与画像输入均无口味权重；严格模型也拒绝重新注入旧字段。"""
    from tests.profile.conftest import make_card
    from worldmock.life.models import AgentState
    from worldmock.life.world_setup import _initial_agent

    frozen = inputs()
    compiled = compiled_persona(frozen)
    state = _initial_agent(frozen, compiled.world_dynamics, int(frozen.config.start_time.timestamp()))
    profile = current_profile()
    assert "taste_weights" not in state.model_dump(mode="json") and "taste_weights" not in profile
    assert "attention_prior" not in json.dumps(profile)
    with pytest.raises(ValidationError, match="taste_weights"):
        AgentState.model_validate({**state.model_dump(mode="python"), "taste_weights": {"content": 1.0}})


@pytest.mark.parametrize("profile", [{"taste_weights": {}}, {"taste_weights": {"content": 1.0},
                                                              "persona_card_struct": None}])
def test_restored_persona_profile_rejects_retired_taste_weights(profile):
    """@brief 恢复画像校验直接拒绝旧数值口味，即使没有结构化卡片也不保留。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    with pytest.raises(IntegrityError, match="retired taste weights"):
        _validate_persona_card_struct(profile)


@pytest.mark.parametrize("profile", [None, {}, {"persona_card_struct": None},
                                   {"persona_card_struct": {}}, {"persona_card_struct": []}])
def test_restore_requires_a_valid_frozen_persona_card(profile):
    """@brief 缺失、空值与损坏卡片都拒绝恢复，不用正文补齐卡片。"""
    from worldmock.errors import IntegrityError
    from worldmock.runtime import _validate_persona_card_struct

    with pytest.raises(IntegrityError, match="Restored persona card structure is invalid"):
        _validate_persona_card_struct(profile)


def test_creation_requires_a_compiled_persona_card():
    """@brief 新建人物在工作区写入之前拒绝缺失的画像编译结果。"""
    from worldmock.errors import IntegrityError

    frozen = inputs()
    with pytest.raises(IntegrityError, match="Compiled persona card is missing"):
        creation_inputs(frozen, {})


def test_valid_frozen_persona_card_can_be_restored_without_compilation(monkeypatch):
    """@brief 实际新建输入的卡片在恢复时验证原结构，不请求编译模型。"""
    from tests.profile.conftest import make_card
    from worldmock.runtime import _validate_persona_card_struct

    frozen = inputs()
    profile = current_profile()
    monkeypatch.setattr("worldmock.profile.compile.compile_persona",
                        lambda *args, **kwargs: pytest.fail("Restoring must not compile a persona"))
    _validate_persona_card_struct(profile, frozen.persona)


#: 七项都声明、都不要求落在格点上的人物作息（到家 18:15–18:45）。
DECLARED_RHYTHM = {"chronotype_midsleep": 4.1, "sleep_need_hours": 7.6, "lunch_window": (11.75, 12.75),
                   "leave_work_window": (18.0, 18.5), "arrive_home_window": (18.25, 18.75),
                   "dinner_window": (19.0, 20.0), "bedtime_window": (23.5, 24.5)}


async def _created_run(tmp_path, native_runtime, run_id: str, **declared):
    """经真实的运行创建路径（run_world、原生 Society 与工作区）用声明了作息的人设新建一次运行。"""
    from tests.integration.runtime.current_contract_fixtures import ROOT
    from tests.life.persona_inputs import declare_rhythm
    from worldmock.footprints.store import FootprintStore
    from worldmock.runtime import RunRequest, run_world

    root, _, models = native_runtime
    declare_rhythm(root, **declared)
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
            "plan": json.dumps(plan, ensure_ascii=False), "persona_layer": _sent_persona_layer(models[0].requests[0])}


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


def _arrival_deviation(root, card, clock: str) -> float:
    """评估的到家偏离：周一 18:00 下班离开公司、给定钟点到家，作息窗口取打包的结构化卡片。"""
    from tests.frozen_inputs import fixture_metadata
    from tests.integration.evaluation.test_workload_metrics import CODE, ZONE, _Events, _at
    from worldmock.environment import load_environment
    from worldmock.evaluation.behavior_metrics import behavior_metrics_report
    from worldmock.evaluation.workload_metrics import workday_inputs

    workday = workday_inputs(load_environment(root, metadata_loader=fixture_metadata), {1: card})
    assert workday.agents[1].arrive_home_window == (18.25, 18.75)
    assert workday.agents[1].leave_work_window == (18.0, 18.5)
    events = _Events()
    events.body(1, _at(0, "07:00"), _at(0, "22:00"))
    events.trip(1, _at(0, "08:20"), _at(0, "08:50"), ("home", "office"))
    events.trip(1, _at(0, "18:00"), _at(0, clock), ("office", "home"))
    reports = behavior_metrics_report(events.rows, timezone=ZONE, workday=workday)[CODE]["reports"]
    return reports["routine_deviation"]["agents"]["1"]["days"][0]["arrive_home_vs_window_minutes"]


@pytest.mark.asyncio
async def test_declared_rhythm_is_the_same_time_in_card_layer_plan_body_and_evaluation(
        tmp_path, native_runtime, engine_factory):
    """@brief 声明七项（到家 18:15–18:45）后新建运行：工作区里的结构化卡片、卡片文本、人设层、零点计划的输入、
    引擎的饭点窗口和评估的到家窗口是同一个时间，都不取整；声明值不在格点上的运行可以从检查点恢复。"""
    from tests.integration.runtime.current_contract_fixtures import ROOT
    from worldmock.agents.life_persona import persona_layer
    from worldmock.life.day_plan_inputs import plan_inputs
    from worldmock.runtime import ResumeRequest, _validate_persona_card_struct, resume_world

    run = await _created_run(tmp_path, native_runtime, "rhythm-parent", **DECLARED_RHYTHM)
    card, profile = run["profile"]["persona_card_struct"], run["profile"]
    anchors = card["time_anchors"]
    assert (card["chronotype_midsleep"], card["sleep_need_hours"]) == (4.1, 7.6)
    assert {name: (anchors[name]["start_hour"], anchors[name]["end_hour"])
            for name in ("lunch", "leave_work", "arrive_home", "dinner", "bedtime")} == {
        "lunch": (11.75, 12.75), "leave_work": (18.0, 18.5), "arrive_home": (18.25, 18.75),
        "dinner": (19.0, 20.0), "bedtime": (23.5, 24.5)}
    rhythm = persona_layer(profile, "林舟", {"relationships": [], "preferences": []})["rhythm"]
    assert rhythm == {"wake": "7点到7点半", "leave_home": "8点到8点半", "lunch": "11点三刻到12点三刻",
                      "leave_work": "18点到18点半", "arrive_home": "18点一刻到18点三刻", "dinner": "19点到20点",
                      "bedtime": "23点半到0点半", "sleep_midpoint": "4点6分", "sleep_need": "7.6 小时"}
    for phrase in ("午餐11点三刻到12点三刻", "到家18点一刻到18点三刻", "睡眠中点约4点6分", "需睡7.6小时"):
        assert phrase in profile["persona_card"]
    assert run["persona_layer"]["rhythm"] == rhythm
    assert "18:15–18:45" in run["plan"] and "11:45–12:45" in run["plan"] and "23:30–00:30" in run["plan"]
    assert [item["code"] for item in run["manifest"]["input_warnings"]
            if item["code"].startswith("CARD_FIELD")] == ["CARD_FIELD_OVERRIDDEN"] * 7

    engine = engine_factory(environment_root=run["root"])
    windows = engine.meal_windows_by_agent()[1]
    assert (windows.lunch, windows.dinner) == ((11.75, 12.75), (19.0, 20.0))
    body = engine.context.state.agents[1].body
    assert (body.chronotype_midsleep, body.sleep_need_hours) == (4.1, 7.6)
    planned = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["人物"]
    assert (planned["午饭时段"], planned["到家时段"], planned["就寝时段"]) == (
        "11:45–12:45", "18:15–18:45", "23:30–00:30")
    assert _arrival_deviation(run["root"], card, "18:50") == 5.0
    assert _arrival_deviation(run["root"], card, "18:05") == -10.0
    assert _arrival_deviation(run["root"], card, "18:30") == 0.0

    _validate_persona_card_struct(profile, engine.context.persona)
    checkpoint = next(event.data["checkpoint_id"] for event in run["events"]
                      if event.event_type == "checkpoint_created")
    restored = await resume_world(ResumeRequest(ROOT, run["root"], run["result"].run_dir, checkpoint,
                                                tmp_path / "rhythm-child"))
    assert restored.current_time == engine.context.config.stop_time


@pytest.mark.asyncio
async def test_a_declaration_against_the_extracted_departure_keeps_the_card_value_and_warns(
        tmp_path, native_runtime, engine_factory):
    """@brief 唯一例外：声明的午饭 07:00–08:00 排在模型读出的出门 08:00 之前，运行照常创建，清单的 input_warnings 有一条
    CARD_FIELD_CONFLICT，人设层显示卡片值，零点计划和饭点窗口仍用声明值。"""
    from worldmock.agents.life_persona import persona_layer

    run = await _created_run(tmp_path, native_runtime, "rhythm-conflict", lunch_window=(7.0, 8.0))
    conflicts = [item for item in run["manifest"]["input_warnings"] if item["code"].startswith("CARD_FIELD")]
    assert [(item["code"], item["agent_id"], item["details"]) for item in conflicts] == [
        ("CARD_FIELD_CONFLICT", 1, {"field": "lunch_window", "card_value": [12.0, 13.0],
                                    "persona_value": [7.0, 8.0], "reason": "anchor_order"})]
    lunch = run["profile"]["persona_card_struct"]["time_anchors"]["lunch"]
    assert (lunch["start_hour"], lunch["end_hour"]) == (12.0, 13.0)
    rhythm = persona_layer(run["profile"], "林舟", {"relationships": [], "preferences": []})["rhythm"]
    assert rhythm["lunch"] == "12点到13点" and "午餐12点到13点" in run["profile"]["persona_card"]
    assert run["persona_layer"]["rhythm"]["lunch"] == "12点到13点"
    assert "07:00–08:00" in run["plan"]
    engine = engine_factory(environment_root=run["root"])
    assert engine.meal_windows_by_agent()[1].lunch == (7.0, 8.0)


def test_restored_card_must_hold_the_unrounded_declared_rhythm():
    """@brief 恢复时「卡片与声明一致」比较的是未取整的值：卡片里是取整到半小时的到家 18:00–19:00、声明是
    18:15–18:45 时以完整性错误停止；卡片原样保存声明值时通过。"""
    from tests.profile.conftest import make_card
    from tests.profile.model_fixture import persona
    from worldmock.errors import IntegrityError
    from worldmock.profile.injection import apply_persona_rhythm
    from worldmock.profile.models import TimeAnchors, persona_card_struct
    from worldmock.runtime import _validate_persona_card_struct

    declared = persona().model_copy(update={"arrive_home_window": [18.25, 18.75], "sleep_need_hours": 7.6})
    assert {"arrive_home_window", "sleep_need_hours"} <= declared.model_fields_set
    kept = apply_persona_rhythm(make_card(), declared).card
    _validate_persona_card_struct({"persona_card_struct": persona_card_struct(kept)}, declared)
    for stale in ({"arrive_home": {"start_hour": 18.0, "end_hour": 19.0}}, {}):
        anchors = TimeAnchors.model_validate({**kept.time_anchors.model_dump(), **stale})
        rounded = kept.model_copy(update={"time_anchors": anchors, "sleep_need_hours": 7.5 if not stale else 7.6})
        with pytest.raises(IntegrityError, match="Restored persona card rhythm disagrees with the scenario-declared "
                                                 "rhythm"):
            _validate_persona_card_struct({"persona_card_struct": persona_card_struct(rounded)}, declared)
