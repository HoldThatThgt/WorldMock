"""运行输入警告与场景作息覆盖的真实路径（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 、2.1，验收台账「输入警告」「场景作息」）。

新建运行经 runtime 的真实准备与装配（_prepare_new_run → _build_society，画像编译走真实 compile_persona、只打桩三次一致性
抽取）、LifeEnv 与引擎建立运行：警告冻结进运行清单、逐条写 ops 警告、导出后计入 quality.json，警告不使创建失败；
validate（validate_project 与 CLI）打印同一组警告，覆盖率低于 80% 仍失败；场景作息只覆盖显式声明的字段，
身体结算、零点计划输入与真实请求的人设层读同一组值，恢复时核对冻结卡片的作息。
"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT, ENVIRONMENT_ROOT
import worldmock.run_setup as worldmock_run_setup_module

import copy
import json
import logging
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import worldmock.runtime as runtime  # noqa: E402
from tests.engine_fixture import (_mutated_scenario)  # noqa: F401
from tests.life.conftest import (advance_next, stage_day_plan, start_activity, submit_round)  # noqa: F401
from tests.profile.conftest import make_card, make_payload, make_window  # noqa: E402
from tests.profile.test_extractor import FakeResponse, FakeSession  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.life.day_plan_inputs import plan_inputs  # noqa: E402
from worldmock.life.world_setup import load_scenario
from worldmock.profile.compile import compile_persona  # noqa: E402
from worldmock.profile.coverage import PersonaCoverageError  # noqa: E402
from worldmock.profile.extractor import PersonaExtraction, extract_persona  # noqa: E402
from worldmock.profile.injection import INPUT_WARNING_CODES, apply_scenario_rhythm  # noqa: E402
from worldmock.profile.models import BigFiveProfile, HabitSignature, Tastes, persona_card_struct  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"
_RHYTHM = ("chronotype_midsleep", "sleep_need_hours", "lunch_window", "dinner_window", "bedtime_window")
#: WC 白领场景声明的作息（与卡片抽取值不同的中点、午餐与晚餐）。
_WC_RHYTHM = {"chronotype_midsleep": 4.75, "sleep_need_hours": 8.0, "lunch_window": [12.0, 12.5],
              "dinner_window": [18.5, 19.5], "bedtime_window": [23.5, 24.5]}
#: 人设层 rhythm 的九键与键序（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）。
_RHYTHM_KEYS = ["wake", "leave_home", "lunch", "leave_work", "arrive_home", "dinner", "bedtime", "sleep_midpoint",
                "sleep_need"]
#: 写明下班与到家时刻的背景故事（真实抽取的输入；响应由脚本化会话给出）。
_WORK_STORY = "陈曦在张江做产品经理，每天六点下班，七点到家。"
_BACKGROUND_STORY = "陈曦在上海工作和生活，会按自己的作息安排工作、用餐、休息与应用使用。" * 5


def _scenario(tmp_path: Path, **declared) -> Path:
    """正式场景的副本：人物带可编译故事，只显式声明给出的作息字段。"""
    def mutate(document: dict) -> None:
        agent = document["agents"][0]
        for name in _RHYTHM:
            agent.pop(name, None)
        agent["background_story"] = _BACKGROUND_STORY
        agent.update(declared)
    return _mutated_scenario(SCENARIO, mutate, tmp_path / "scenario")


def _extraction(card, weak_fields: tuple[str, ...] = ()) -> PersonaExtraction:
    """打桩的三次一致性抽取结果：weak_fields 的众数占比低于 2/3（编译把它们写进 warnings）。"""
    return PersonaExtraction(
        card=card, big_five=BigFiveProfile(openness=5, conscientiousness=4, extraversion=6, agreeableness=4,
                                           neuroticism=3),
        runs=3, agreement=0.9, per_field={name: 1 / 3 for name in weak_fields}, warning=bool(weak_fields),
        model="deepseek-flash", prompt_version="card-v1")


def _extracted(work_anchors: dict) -> PersonaExtraction:
    """真实 extract_persona 的单次抽取：脚本化会话给出一份工厂载荷，其 work_anchors 取给定值（不消耗解析重试）。"""
    session = FakeSession([FakeResponse(content=make_payload(work_anchors=work_anchors))])
    extraction = extract_persona(_WORK_STORY, n_runs=1, api_key="test-key", session=session)
    assert len(session.calls) == 1
    return extraction


def _card_with_work_anchors():
    """工厂卡片写明下班 18:00–18:30、到家 19:00–19:30（其余锚点不变）。"""
    anchors = {**make_card().time_anchors.model_dump(), "leave_work": {"start_hour": 18.0, "end_hour": 18.5},
               "arrive_home": {"start_hour": 19.0, "end_hour": 19.5}}
    return make_card(time_anchors=anchors)


def _card_with_anchors(leave_work: list[float] | None, arrive_home: list[float] | None):
    """工厂卡片（出门 08:00、就寝 23:00）换上给定的下班、到家窗口（[start, end]，None 为 null；其余锚点不变）。"""
    windows = {name: None if span is None else {"start_hour": span[0], "end_hour": span[1]}
               for name, span in (("leave_work", leave_work), ("arrive_home", arrive_home))}
    return make_card(time_anchors={**make_card().time_anchors.model_dump(), **windows})


def _rhythm_warnings(frozen: list[dict]) -> list[tuple[str, dict]]:
    """清单冻结的场景作息警告（CARD_FIELD_CONFLICT、CARD_FIELD_OVERRIDDEN）的（码, details），按冻结顺序。"""
    return [(item["code"], item["details"]) for item in frozen
            if item["code"] in {"CARD_FIELD_CONFLICT", "CARD_FIELD_OVERRIDDEN"}]


def _compiled(extraction: PersonaExtraction):
    """validate 路径用的编译产物：同一份抽取结果经真实 compile_persona。"""
    return compile_persona("陈曦，32岁，产品经理，住在上海张江。" * 5, extraction=extraction)


def _platform_card():
    """口味只写平台词、习惯与饮食世界里没有的卡片（WC 白领的毛病）：题材对不上、覆盖率低于 80%。"""
    return make_card(sleep_need_hours=8.0,
                     habits=(HabitSignature(trigger="睡前", response="刷B站", time_window=make_window(22.0, 24.0)),),
                     tastes=Tastes(content_genres=("B站视频",), food_preferences=("冰美式咖啡",), media=("B站",)))




async def _create_run(tmp_path: Path, monkeypatch, scenario_path: Path, extraction: PersonaExtraction) -> dict:
    """真实新建：runtime 准备与装配（画像编译走真实 compile_persona，只替换三次一致性抽取、模型客户端与 Ray actor），
    再按捕获的参数在进程内建立 LifeEnv；返回装配出的人物工作区输入与警告。"""
    from agentsociety2.config import llm_dispatcher
    from worldmock.life import read_query_runtime
    from custom.envs.life_env import LifeEnv

    import worldmock.profile.compile as compile_module

    monkeypatch.setattr(compile_module, "extract_persona", lambda story: extraction)
    captured: dict = {}
    original = worldmock_run_setup_module.creation_inputs

    def recording(*arguments):
        captured["specs"], captured["warnings"] = result = original(*arguments)
        return result

    monkeypatch.setattr(worldmock_run_setup_module, 'creation_inputs', recording)
    monkeypatch.setenv("WORKSPACE_PATH", str(ROOT))
    monkeypatch.setattr(llm_dispatcher, "build_client_for_role", lambda role: role)
    monkeypatch.setattr(read_query_runtime, "life_env_actor_class",
                        lambda: SimpleNamespace(remote=lambda spec: (captured.__setitem__("actor", spec), "actor")[1]))
    prepared = runtime._prepare_new_run(runtime.RunRequest(ROOT, scenario_path, tmp_path / "run", "warned", "warned"))
    runtime._build_society(prepared)
    options = captured["actor"].env_kwargs["LifeEnv"]
    env = LifeEnv(**options)
    await env.init(prepared.start_time)
    return {"prepared": prepared, "env": env, "options": options["create_options"], **captured}










def _persona_layer(profile: dict) -> dict:
    """真实 LifeAgent 为这份人物档案构建的行动请求里的人设层。"""
    from agentsociety2.agent.base import ReactLoopRequest

    from custom.agents.life_agent import LifeAgent
    from tests.integration.test_life_agent_protocol import _observations

    agent = LifeAgent()
    agent._id, agent._name, agent._profile = profile["id"], profile["name"], profile
    agent._enable_memory = False
    request = ReactLoopRequest(0, load_scenario(SCENARIO)[0].start_at, copy.deepcopy(_observations()))
    agent.prepare_react_loop(request)
    return json.loads(agent.build_react_messages(request)[1]["content"])[1]


def _validate_root(tmp_path: Path) -> Path:
    """validate 的工程根：只放 LabelKit 命令的占位文件（这里验证的是输入警告，不执行 LabelKit）。"""
    root = tmp_path / "project"
    (root / "labelkit-projects").mkdir(parents=True)
    command = root / "vendor/labelkit/.venv" / ("Scripts/labelkit.exe" if os.name == "nt" else "bin/labelkit")
    command.parent.mkdir(parents=True)
    command.write_bytes(b"")
    return root


async def test_run_creation_freezes_input_warnings_into_the_manifest_ops_log_and_quality_report(
        tmp_path, monkeypatch) -> None:
    """输入警告：新建运行时逐条计算，冻结进结构三清单并逐条写一条 runtime 组件的 warn 级 ops 记录；
    导出后结构五 quality.json 的 input_warnings 一节按码计数；题材对不上、覆盖率低于 80% 与编译一致性
    偏低都不使创建失败，运行照常推进。"""
    path = _scenario(tmp_path, chronotype_midsleep=4.75)
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(_platform_card(), ("habits",)))
    env, warnings = created["env"], created["warnings"]
    try:
        manifest = env.engine.context.store.manifest
        assert manifest["schema_version"] == 3
        assert manifest["input_warnings"] == warnings == created["options"]["input_warnings"]
        codes = [item["code"] for item in warnings]
        assert {"COVERAGE_BELOW_THRESHOLD",
                "CARD_FIELD_OVERRIDDEN", "PERSONA_AGREEMENT_LOW"} <= set(codes)
        assert [item["details"]["field"] for item in warnings if item["code"] == "CARD_FIELD_OVERRIDDEN"] == [
            "chronotype_midsleep"]
        logged = [json.loads(line) for day in sorted((tmp_path / "run" / "logs").glob("ops-*.jsonl"))
                  for line in day.read_text(encoding="utf-8").splitlines()]
        warned = [row for row in logged if row["component"] == "runtime" and row["level"] == "warn"]
        assert [row["code"] for row in warned] == codes and set(codes) <= set(INPUT_WARNING_CODES)
        assert [(row.get("agent_id"), row["message"], row["details"]) for row in warned] == [
            (item["agent_id"], item["message"], item["details"]) for item in warnings]
        stage_day_plan(env.engine, submitted=True)
        submit_round(env.engine, {1: ("start_activity", start_activity("等待", "自己"))}, "wait")
        advance_next(env.engine)
        export_run(env.engine.context.store, env.engine.context.store.last_event_seq, tmp_path / "export")
        quality = json.loads((tmp_path / "export" / "quality.json").read_text(encoding="utf-8"))
    finally:
        await env.close()
    section = quality["input_warnings"]
    assert quality["schema_version"] == 5 and section["recorded"] is True
    assert section["warning_count"] == len(warnings)
    assert section["code_counts"] == {code: codes.count(code) for code in INPUT_WARNING_CODES}
    assert section["agent_counts"] == {"1": len(warnings)} and section["scenario_warning_count"] == 0




async def test_validate_prints_the_same_warnings_the_run_froze(tmp_path, monkeypatch, capsys, caplog) -> None:
    """validate 与运行路径一致：同一份编译结果下，worldmock validate（validate_project）输出的 input_warnings 与新建运行
    清单冻结的警告完全相同（场景作息覆盖走同一个函数），并逐条写到标准错误流；覆盖率达标时校验通过。"""
    import worldmock.validation as validation
    from worldmock.cli import main

    covered = make_card(habits=(HabitSignature(trigger="下班到家", response="去五里公园散步",
                                               time_window=make_window(19.0, 20.0)),),
                        tastes=Tastes(content_genres=("悬疑",), food_preferences=("牛肉面",), media=("长视频",)))
    extraction = _extraction(covered, ("coping",))
    compiled = _compiled(extraction)
    path = _scenario(tmp_path, **_WC_RHYTHM)
    created = await _create_run(tmp_path, monkeypatch, path, extraction)
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    monkeypatch.setattr(worldmock_run_setup_module, "_configure_process", lambda _root: None)
    monkeypatch.setattr(validation, "_compile_persona", lambda story: compiled)
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        code = main(["--project-root", str(_validate_root(tmp_path)), "validate", "--scenario", str(path),
                     "--coverage-out", str(tmp_path / "coverage.json")])

    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["valid"] is True and result["input_warnings"] == frozen
    assert {"CARD_FIELD_OVERRIDDEN", "PERSONA_AGREEMENT_LOW"} <= {item["code"] for item in frozen}
    printed = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Input warning")]
    assert printed == [f"Input warning {item['code']} (agent {item['agent_id']}): {item['message']}" for item in frozen]


def test_validate_still_fails_below_the_coverage_threshold_after_printing_the_warnings(
        tmp_path, monkeypatch, capsys, caplog) -> None:
    """裁决 D4：覆盖率低于 80% 时 worldmock validate 照旧失败（非零退出），但先把同一组输入警告写到标准错误流；
    运行路径对同一份编译结果只给出 COVERAGE_BELOW_THRESHOLD 警告，不抛错。"""
    import worldmock.validation as validation
    from worldmock.cli import main

    compiled = _compiled(_extraction(_platform_card()))
    path = _scenario(tmp_path, **_WC_RHYTHM)
    monkeypatch.setattr(worldmock_run_setup_module, "_configure_process", lambda _root: None)
    monkeypatch.setattr(validation, "_compile_persona", lambda story: compiled)

    with caplog.at_level(logging.WARNING):
        code = main(["--project-root", str(_validate_root(tmp_path)), "validate", "--scenario", str(path),
                     "--coverage-out", str(tmp_path / "coverage.json")])
    _, warnings = worldmock_run_setup_module.creation_inputs(path, load_scenario(path)[0], {1: compiled})

    assert code == 1 and capsys.readouterr().out == ""
    assert "COVERAGE_BELOW_THRESHOLD" in [item["code"] for item in warnings]
    printed = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Input warning")]
    assert printed == [f"Input warning {item['code']} (agent {item['agent_id']}): {item['message']}"
                       for item in warnings]
    failures = [record for record in caplog.records if record.exc_info]
    assert failures and isinstance(failures[-1].exc_info[1], PersonaCoverageError)




async def test_a_scenario_declaring_only_the_midpoint_changes_only_the_midpoint_in_the_request(
        tmp_path, monkeypatch) -> None:
    """场景作息：场景只声明睡眠中点时，真实请求的人设层显示场景中点「4点三刻」，起床、出门、三餐、就寝与睡眠需要仍是卡片值
    （场景人物的缺省 7.5 小时、未声明的窗口不进卡片）；冻结的结构化卡片同样只改了中点。"""
    card = make_card(sleep_need_hours=8.0)
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path, chronotype_midsleep=4.75), _extraction(card))
    await created["env"].close()
    profile = created["specs"][0]["profile"]

    persona = _persona_layer(profile)

    assert persona["rhythm"] == {"wake": "7点到7点半", "leave_home": "8点到8点半", "lunch": "12点到13点",
                                 "leave_work": None, "arrive_home": None,
                                 "dinner": "18点半到19点半", "bedtime": "23点到0点", "sleep_midpoint": "4点三刻",
                                 "sleep_need": "8 小时"}
    assert list(persona["rhythm"]) == _RHYTHM_KEYS
    assert profile["persona_card_struct"] == persona_card_struct(card.model_copy(update={"chronotype_midsleep": 4.75}))


async def test_body_settlement_day_plan_inputs_and_the_persona_layer_read_the_same_rhythm(
        tmp_path, monkeypatch) -> None:
    """场景作息：场景声明全部五个作息字段（WC 白领的值）时，引擎的身体（两过程睡眠的中点与睡眠需要）、零点计划输入的
    三个时段与真实请求人设层的作息是同一组值；卡片抽取的不同值不再出现在人设层。"""
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path, **_WC_RHYTHM), _extraction(make_card()))
    env = created["env"]
    try:
        body = env.engine.context.state.agents[1].body
        person = plan_inputs(env.engine.context, env.engine.observations, env.engine.midnight_rhythm_pending, 1).payload["人物"]
    finally:
        await env.close()
    profile = created["specs"][0]["profile"]
    anchors = profile["persona_card_struct"]["time_anchors"]
    persona = _persona_layer(profile)

    assert (body.chronotype_midsleep, body.sleep_need_hours) == (4.75, 8.0)
    assert (profile["persona_card_struct"]["chronotype_midsleep"], profile["persona_card_struct"]["sleep_need_hours"]) \
        == (4.75, 8.0)
    assert [[anchors[slot]["start_hour"], anchors[slot]["end_hour"]] for slot in ("lunch", "dinner", "bedtime")] == [
        _WC_RHYTHM["lunch_window"], _WC_RHYTHM["dinner_window"], _WC_RHYTHM["bedtime_window"]]
    assert (person["午饭时段"], person["晚饭时段"], person["就寝时段"]) == ("12:00–12:30", "18:30–19:30", "23:30–00:30")
    rhythm = persona["rhythm"]
    assert (rhythm["lunch"], rhythm["dinner"], rhythm["bedtime"]) == ("12点到12点半", "18点半到19点半", "23点半到0点半")
    assert (rhythm["sleep_midpoint"], rhythm["sleep_need"]) == ("4点三刻", "8 小时")


async def test_a_conflicting_scenario_window_keeps_the_card_value_and_is_frozen_as_a_conflict(
        tmp_path, monkeypatch) -> None:
    """场景作息冲突：场景午餐 06:00–06:30 早于卡片出门时刻，覆盖后锚点乱序——人设层与冻结卡片保留卡片午餐，
    清单冻结 CARD_FIELD_CONFLICT；同时声明的中点照常覆盖。"""
    path = _scenario(tmp_path, chronotype_midsleep=4.75, lunch_window=[6.0, 6.5])
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(make_card()))
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    profile = created["specs"][0]["profile"]

    conflicts = [item for item in frozen if item["code"] == "CARD_FIELD_CONFLICT"]
    assert [item["details"] for item in conflicts] == [{"field": "lunch_window", "card_value": [12.0, 13.0],
                                                        "scenario_value": [6.0, 6.5], "reason": "anchor_order"}]
    assert profile["persona_card_struct"]["time_anchors"]["lunch"] == {"start_hour": 12.0, "end_hour": 13.0}
    assert _persona_layer(profile)["rhythm"]["lunch"] == "12点到13点"
    assert profile["persona_card_struct"]["chronotype_midsleep"] == 4.75


async def test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input(
        tmp_path, monkeypatch) -> None:
    """场景作息覆盖七字段（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）：场景在 WC 作息之外声明下班 18:00–18:30、到家
    18:30–19:00，卡片两槽为 null——真实新建把两个窗口落进冻结的结构化卡片与卡片文本，清单各冻结一条
    CARD_FIELD_OVERRIDDEN（card_value 为 null）、没有冲突；真实请求的人设层显示两个窗口，零点计划的人物输入在午饭与
    晚饭时段之间写出下班、到家时段。"""
    path = _scenario(tmp_path, **_WC_RHYTHM, leave_work_window=[18.0, 18.5], arrive_home_window=[18.5, 19.0])
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(make_card()))
    env = created["env"]
    try:
        person = plan_inputs(env.engine.context, env.engine.observations, env.engine.midnight_rhythm_pending, 1).payload["人物"]
        frozen = env.engine.context.store.manifest["input_warnings"]
    finally:
        await env.close()
    profile = created["specs"][0]["profile"]
    anchors = profile["persona_card_struct"]["time_anchors"]

    rhythm = _persona_layer(profile)["rhythm"]

    assert (anchors["leave_work"], anchors["arrive_home"]) == ({"start_hour": 18.0, "end_hour": 18.5},
                                                               {"start_hour": 18.5, "end_hour": 19.0})
    assert "；下班18点到18点半；到家18点半到19点。" in profile["persona_card"]
    assert (rhythm["leave_work"], rhythm["arrive_home"]) == ("18点到18点半", "18点半到19点")
    assert (person["下班时段"], person["到家时段"]) == ("18:00–18:30", "18:30–19:00")
    assert list(person)[-5:] == ["午饭时段", "下班时段", "到家时段", "晚饭时段", "就寝时段"]
    work = [item["details"] for item in frozen if item["code"] == "CARD_FIELD_OVERRIDDEN"
            and item["details"]["field"] in {"leave_work_window", "arrive_home_window"}]
    assert work == [{"field": "leave_work_window", "card_value": None, "scenario_value": [18.0, 18.5],
                     "applied_value": [18.0, 18.5]},
                    {"field": "arrive_home_window", "card_value": None, "scenario_value": [18.5, 19.0],
                     "applied_value": [18.5, 19.0]}]
    assert [item for item in frozen if item["code"] == "CARD_FIELD_CONFLICT"] == []


async def test_card_only_work_anchors_never_enter_the_plan_input(tmp_path, monkeypatch) -> None:
    """只来自卡片的下班与到家时刻同起床、出门一样不进零点计划的结构化输入（规格 4.2）：卡片写明两个锚点、场景只声明 WC
    的五个作息字段时，两个锚点照常到达冻结卡片与真实请求的人设层，零点计划人物输入只有场景声明的三个时段，没有下班与
    到家时段；清单也没有这两个字段的覆盖或冲突。"""
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path, **_WC_RHYTHM),
                                _extraction(_card_with_work_anchors()))
    env = created["env"]
    try:
        person = plan_inputs(env.engine.context, env.engine.observations, env.engine.midnight_rhythm_pending, 1).payload["人物"]
        frozen = env.engine.context.store.manifest["input_warnings"]
    finally:
        await env.close()
    profile = created["specs"][0]["profile"]

    rhythm = _persona_layer(profile)["rhythm"]

    assert (rhythm["leave_work"], rhythm["arrive_home"]) == ("18点到18点半", "19点到19点半")
    assert profile["persona_card_struct"]["time_anchors"]["leave_work"] == {"start_hour": 18.0, "end_hour": 18.5}
    assert "下班时段" not in person and "到家时段" not in person
    assert [key for key in person if key.endswith("时段")] == ["午饭时段", "晚饭时段", "就寝时段"]
    assert not {"leave_work_window", "arrive_home_window"} & {item["details"].get("field") for item in frozen}


async def test_a_conflicting_scenario_work_window_keeps_the_null_card_slot_and_is_frozen_as_a_conflict(
        tmp_path, monkeypatch) -> None:
    """新槽的冲突规则走真实路径（规格 4.2，不降级卡片锚点）：场景下班 06:00–06:30 早于卡片出门 08:00、卡片下班槽为
    null——新建运行照常建立，清单恰好冻结一条 CARD_FIELD_CONFLICT（card_value 为 null，正文写 null），没有覆盖；
    冻结卡片与真实请求人设层的下班仍为 null。"""
    path = _scenario(tmp_path, leave_work_window=[6.0, 6.5])
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(make_card()))
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    profile = created["specs"][0]["profile"]

    conflicts = [item for item in frozen if item["code"] == "CARD_FIELD_CONFLICT"]
    assert [item["details"] for item in conflicts] == [{"field": "leave_work_window", "card_value": None,
                                                        "scenario_value": [6.0, 6.5], "reason": "anchor_order"}]
    assert conflicts[0]["agent_id"] == 1 and conflicts[0]["message"].endswith("keeping card value null.")
    assert [item for item in frozen if item["code"] == "CARD_FIELD_OVERRIDDEN"] == []
    assert profile["persona_card_struct"]["time_anchors"]["leave_work"] is None
    assert _persona_layer(profile)["rhythm"]["leave_work"] is None


async def test_extracted_work_anchors_reach_the_real_request_persona_layer(tmp_path, monkeypatch) -> None:
    """作息锚点贯通（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）：真实抽取得到的下班、到家窗口经真实新建（画像编译、场景作息
    覆盖、人物装配）冻结进结构化卡片，卡片文本在作息行末追加两个分句；真实 LifeAgent 发出的行动请求里人设层 rhythm 按
    九键键序显示两个窗口。场景没有声明作息，卡片值原样到达。"""
    extraction = _extracted({"leave_work": "18:00-18:30", "arrive_home": "19:00-19:30"})
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path), extraction)
    await created["env"].close()
    profile = created["specs"][0]["profile"]
    anchors = profile["persona_card_struct"]["time_anchors"]

    rhythm = _persona_layer(profile)["rhythm"]

    assert (anchors["leave_work"], anchors["arrive_home"]) == ({"start_hour": 18.0, "end_hour": 18.5},
                                                               {"start_hour": 19.0, "end_hour": 19.5})
    assert list(rhythm) == _RHYTHM_KEYS
    assert (rhythm["leave_work"], rhythm["arrive_home"]) == ("18点到18点半", "19点到19点半")
    assert "需睡7.5小时；下班18点到18点半；到家19点到19点半。" in profile["persona_card"]


async def test_a_demotion_note_is_frozen_as_a_compiler_warning_that_does_not_blame_agreement(
        tmp_path, monkeypatch) -> None:
    """降级说明的出口（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 ）：真实抽取里下班早于出门的单次采样只为放行而降级，最终卡片的
    下班为 null 并带一条说明，各字段一致性都满；真实新建把它作为画像编译警告冻结进运行清单——恰好一条
    PERSONA_AGREEMENT_LOW，details 只有 compiler_warning（说明原文），消息不预设原因是一致性偏低；警告码仍在闭集 10 之内，
    创建不失败，人设层的下班为 null。"""
    note = "work_anchor_demoted: leave_work start 7 is outside leave_home 8..bedtime 23"
    extraction = _extracted({"leave_work": "07:00-07:30", "arrive_home": None})
    assert extraction.demotion_notes == (note,) and extraction.warning is False
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path), extraction)
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    profile = created["specs"][0]["profile"]

    compiler = [item for item in frozen if item["code"] == "PERSONA_AGREEMENT_LOW"]
    assert [(item["agent_id"], item["details"]) for item in compiler] == [(1, {"compiler_warning": note})]
    assert compiler[0]["message"] == f"Persona compilation warning: {note}." and "一致性" not in compiler[0]["message"]
    assert set(INPUT_WARNING_CODES) == {
        "CONTENT_EMPTY_WITH_MEDIA_TASTES",
        "COVERAGE_BELOW_THRESHOLD",
        "CARD_FIELD_OVERRIDDEN",
        "CARD_FIELD_CONFLICT",
        "PERSONA_AGREEMENT_LOW",
        "LOCATION_NAME_SUSPICIOUS",
    }
    assert {item["code"] for item in frozen} <= set(INPUT_WARNING_CODES)
    assert profile["persona_card_struct"]["time_anchors"]["leave_work"] is None
    assert _persona_layer(profile)["rhythm"]["leave_work"] is None


async def test_every_compiler_warning_reaches_the_manifest_and_the_ops_log_in_compile_order(
        tmp_path, monkeypatch) -> None:
    """画像编译警告逐条透传（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-下班与到家锚点 「降级说明的出口」）：真实抽取里三次采样的应对各不相同
    （一致性警告），下班早于出门、到家晚于就寝（两条降级说明）；真实编译把降级说明接在一致性警告之后，真实新建把这三条
    按编译顺序各冻结为一条 PERSONA_AGREEMENT_LOW（details.compiler_warning 带原文），ops 警告记录逐条相同，一条不少。"""
    notes = ("work_anchor_demoted: leave_work start 7 is outside leave_home 8..bedtime 23",
             "work_anchor_demoted: arrive_home start 23.5 is outside leave_home 8..bedtime 23")
    expected = ("field_agreement_below_two_thirds: ['coping']", *notes)
    work = {"leave_work": "07:00-07:30", "arrive_home": "23:30-24:00"}
    session = FakeSession([FakeResponse(content=make_payload(work_anchors=work, coping=coping))
                           for coping in ("拼高达模型", "去江边散步", "听老歌")])
    extraction = extract_persona(_WORK_STORY, n_runs=3, api_key="test-key", session=session)
    assert len(session.calls) == 3 and extraction.warning is True and extraction.demotion_notes == notes
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path), extraction)
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()

    compiler = [item for item in frozen if item["code"] == "PERSONA_AGREEMENT_LOW"]
    assert [(item["agent_id"], item["details"]) for item in compiler] == [
        (1, {"compiler_warning": text}) for text in expected]
    assert [item["message"] for item in compiler] == [f"Persona compilation warning: {text}." for text in expected]
    logged = [json.loads(line) for day in sorted((tmp_path / "run" / "logs").glob("ops-*.jsonl"))
              for line in day.read_text(encoding="utf-8").splitlines()]
    assert [row["details"] for row in logged if row["component"] == "runtime" and row["level"] == "warn"
            and row["code"] == "PERSONA_AGREEMENT_LOW"] == [{"compiler_warning": text} for text in expected]


@pytest.mark.parametrize(("work", "declared", "kept"), [
    pytest.param(([18.0, 18.5], [18.5, 19.0]), {"leave_work_window": [19.0, 19.5]},
                 {"card_value": [18.0, 18.5], "text": "；下班18点到18点半；到家18点半到19点。",
                  "rhythm": {"leave_work": "18点到18点半", "arrive_home": "18点半到19点", "bedtime": "23点到0点"}},
                 id="leave_work_after_the_card_arrive_home"),
    pytest.param(([21.0, 21.5], None), {"bedtime_window": [20.0, 21.0]},
                 {"card_value": [23.0, 24.0], "text": "就寝23点到24点，睡眠中点约4点，需睡7.5小时；下班21点到21点半。",
                  "rhythm": {"leave_work": "21点到21点半", "arrive_home": None, "bedtime": "23点到0点"}},
                 id="bedtime_before_the_card_leave_work"),
])
async def test_a_scenario_window_conflicting_with_a_card_work_anchor_keeps_every_card_anchor(
        tmp_path, monkeypatch, work, declared, kept) -> None:
    """冲突时该槽保留卡片值、覆盖不降级任何卡片锚点（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）走真实路径，卡片的下班、到家
    槽非空：场景只声明晚于卡片到家的下班，或只声明早于卡片下班的就寝——真实新建照常建立，清单恰好冻结一条
    CARD_FIELD_CONFLICT（card_value 为该槽的卡片值）、没有覆盖；冻结的结构化卡片、卡片文本与真实请求的人设层都保留
    卡片的下班、到家与就寝。"""
    card = _card_with_anchors(*work)
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path, **declared), _extraction(card))
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    profile = created["specs"][0]["profile"]
    (field, scenario_value), = declared.items()

    rhythm = _persona_layer(profile)["rhythm"]

    assert _rhythm_warnings(frozen) == [("CARD_FIELD_CONFLICT", {
        "field": field, "card_value": kept["card_value"], "scenario_value": scenario_value, "reason": "anchor_order"})]
    assert profile["persona_card_struct"] == persona_card_struct(card)
    assert kept["text"] in profile["persona_card"]
    assert {name: rhythm[name] for name in kept["rhythm"]} == kept["rhythm"]


async def test_scenario_windows_that_each_fit_alone_but_not_together_land_in_slot_order(
        tmp_path, monkeypatch) -> None:
    """落槽顺序（午餐、下班、到家、晚餐、就寝，docs/CONTRACTS.md#contracts-worldmock-接口合同「场景作息覆盖」）走真实路径：场景同时声明下班
    22:00–22:30 与就寝 21:00–22:00，各自都放得进卡片、合起来放不下——真实新建先落下班，清单冻结 bedtime_window 的
    CARD_FIELD_CONFLICT（保留卡片就寝）与 leave_work_window 的 CARD_FIELD_OVERRIDDEN；冻结的结构化卡片、卡片文本与
    真实请求的人设层的下班取场景值、就寝仍是卡片值。"""
    path = _scenario(tmp_path, leave_work_window=[22.0, 22.5], bedtime_window=[21.0, 22.0])
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(make_card()))
    try:
        frozen = created['env'].engine.context.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    profile = created["specs"][0]["profile"]
    anchors = profile["persona_card_struct"]["time_anchors"]

    rhythm = _persona_layer(profile)["rhythm"]

    assert _rhythm_warnings(frozen) == [
        ("CARD_FIELD_CONFLICT", {"field": "bedtime_window", "card_value": [23.0, 24.0],
                                 "scenario_value": [21.0, 22.0], "reason": "anchor_order"}),
        ("CARD_FIELD_OVERRIDDEN", {"field": "leave_work_window", "card_value": None,
                                   "scenario_value": [22.0, 22.5], "applied_value": [22.0, 22.5]})]
    assert (anchors["leave_work"], anchors["bedtime"]) == ({"start_hour": 22.0, "end_hour": 22.5},
                                                           {"start_hour": 23.0, "end_hour": 24.0})
    assert "就寝23点到24点，睡眠中点约4点，需睡7.5小时；下班22点到22点半。" in profile["persona_card"]
    assert (rhythm["leave_work"], rhythm["bedtime"]) == ("22点到22点半", "23点到0点")


def _parent_with_card(root: Path, scenario_path: Path, struct: dict, run_id: str):
    """建立带当前身份、真实场景和指定结构化卡片的可恢复父运行。"""
    from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace
    from worldmock.footprints.checkpoint import snapshot_directory
    from worldmock.life.engine import LifeEngine
    from worldmock.life.world_setup import load_runtime_scenario
    from worldmock.run_setup import stage_model_identity

    semantics, _ = load_runtime_scenario(scenario_path)
    engine = LifeEngine.create(
        scenario_path,
        root / "parent",
        current_run_identity(semantics.scenario, run_id),
        stage_model_identity(),
    )
    parent = engine.context.store
    state = engine.context.state.model_dump(mode="json")
    agent = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
             "profile": {"id": 1, "same_second_continuation": True, "persona_card_struct": struct}}
    workspace = write_agent_workspace(root / "agent", agent_document=agent)
    snapshots = {
        "agent:1": snapshot_directory(workspace, parent.assets),
        "life": parent.assets.put_json({"state_version": state["version"]}, {"kind": "life_checkpoint"}),
    }
    return parent, parent.checkpoint(state, snapshots)


def test_restore_verifies_the_frozen_card_rhythm_against_the_scenario(tmp_path) -> None:
    """恢复时校验一致：新建运行冻结的（经场景作息覆盖的）结构化卡片照常恢复；作息与场景显式声明不一致的卡片
    （例如直接存了抽取值）阻止分支发布。"""
    from worldmock.runtime import ResumeRequest, _prepare_resume

    scenario_path = _scenario(tmp_path, chronotype_midsleep=4.75)
    scenario, _ = load_scenario(scenario_path)
    card = make_card()
    overridden = apply_scenario_rhythm(card, scenario.agents[0]).card
    assert overridden != card
    for name, struct in (("frozen", persona_card_struct(overridden)), ("extracted", persona_card_struct(card))):
        root = tmp_path / name
        root.mkdir()
        parent, checkpoint = _parent_with_card(root, scenario_path, struct, f"parent-{name}")
        try:
            request = ResumeRequest(
                ROOT,
                scenario_path,
                root / "parent",
                checkpoint.checkpoint_id,
                root / "child",
            )
            if name == "frozen":
                assert _prepare_resume(request).workspace_mode == "restored"
            else:
                with pytest.raises(IntegrityError, match="rhythm disagrees with the scenario"):
                    _prepare_resume(request)
                assert not (root / "child").exists()
        finally:
            parent.close()


@pytest.fixture
def profile_environment(tmp_path):
    """@brief 复制当前人工登记环境，只通过真实加载器验证画像输入。"""
    root = tmp_path / "profile-environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path = root / "scenarios/single-person.json"
    payload = json.loads(path.read_bytes())
    payload["agents"][0]["background_story"] = "测试人物每天使用应用并在真实目录内生活。" * 8
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return root, path


def _catalog_edit(root: Path, filename: str, transform) -> None:
    """@brief 真实文件反例通过生产目录发布函数重新钉定身份。"""
    from worldmock.catalog.identity import refresh_manifest_identity

    path = root / "catalog" / filename
    payload = json.loads(path.read_bytes())
    transform(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    refresh_manifest_identity(root / "catalog")


def _empty_registered_contents(root: Path) -> None:
    """@brief 删除内容及仅有内容绑定的应用，保留有真实店铺绑定的应用。"""
    _catalog_edit(root, "contents.json", lambda value: value.update(contents=[]))
    _catalog_edit(root, "apps.json", lambda value: value.update(
        apps=[row for row in value["apps"] if row["app_id"] in {"meituan", "eleme"}]))


def _directory_card():
    """@brief 画像只使用人工环境真实地点、食品和应用名，题材刻意写成未知散文。"""
    return make_card(habits=(HabitSignature(trigger="下班后", response="去五里公园休息",
                                          time_window=make_window(19, 20)),),
                     tastes=Tastes(content_genres=("完全未知的散文题材",), food_preferences=("牛肉面",),
                                   media=("美团", "哔哩哔哩", "短视频")))


def test_catalog_names_drive_coverage_without_phone_configuration(profile_environment):
    """@brief 真实加载、覆盖目标和人物输入不依赖退役手机配置或题材文件。"""
    from worldmock.life.world_setup import load_runtime_scenario
    from worldmock.profile.coverage import validate_coverage
    from worldmock.profile.coverage_targets import build_coverage_targets

    root, path = profile_environment
    semantics, _ = load_runtime_scenario(path)
    targets = build_coverage_targets(semantics.scenario, semantics.catalog, root / "corpora")
    assert targets.app_names == ("哔哩哔哩", "抖音", "美团", "饿了么")
    assert targets.media_kinds == ("文章", "图文", "短视频", "长视频", "实况")
    assert set(targets.food_kinds) == {"牛肉面", "薯片", "三明治", "柠檬茶", "饭团"}
    assert "安静休息" in targets.habit_response_keywords and "五里公园" in targets.habit_response_keywords
    assert "content_genres" not in type(targets).model_fields
    card = _directory_card()
    report = validate_coverage(card, targets)
    assert report.total_items == 5 and report.covered_items == 5 and report.passed
    specs, warnings = worldmock_run_setup_module.creation_inputs(path, semantics.scenario, {1: card})
    assert warnings == [] and specs[0]["profile"]["persona_card_struct"]["tastes"] == card.tastes.model_dump(mode="json")
    assert "taste_weights" not in specs[0]["profile"] and "attention_prior" not in json.dumps(specs[0]["profile"])


def test_empty_warning_uses_app_bindings_and_keeps_five_media_labels(profile_environment):
    """@brief 空内容判断来自真实应用绑定；固定类型覆盖不因没有内容而退化。"""
    from worldmock.life.world_setup import load_runtime_scenario

    root, path = profile_environment
    card = make_card(habits=(), tastes=Tastes(content_genres=("散文题材",), food_preferences=(), media=("短视频",)))
    scenario = load_runtime_scenario(path)[0].scenario
    assert worldmock_run_setup_module.creation_inputs(path, scenario, {1: card})[1] == []
    _empty_registered_contents(root)
    semantics, _ = load_runtime_scenario(path)
    scenario = semantics.scenario
    warnings = worldmock_run_setup_module.creation_inputs(path, scenario, {1: card})[1]
    assert [item["code"] for item in warnings] == ["CONTENT_EMPTY_WITH_MEDIA_TASTES"]
    assert warnings[0]["details"] == {"media": ["短视频"], "habits": []}
    assert worldmock_run_setup_module._coverage_targets(path, scenario, semantics.catalog).media_kinds == (
        "文章", "图文", "短视频", "长视频", "实况")
    business = make_card(habits=(), tastes=Tastes(content_genres=("散文题材",), food_preferences=(), media=("美团",)))
    assert worldmock_run_setup_module.creation_inputs(path, scenario, {1: business})[1] == []


def test_validate_persona_coverage_reuses_actual_creation_warnings(profile_environment, tmp_path, caplog):
    """@brief validate 与运行输入使用同一编译卡片、真实目录目标和六码警告。"""
    from worldmock.life.world_setup import load_runtime_scenario
    from worldmock.validation import validate_persona_coverage

    _, path = profile_environment
    document = json.loads(path.read_bytes())
    document["agents"][0]["chronotype_midsleep"] = 4.75
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    scenario = load_runtime_scenario(path)[0].scenario
    compiled = _compiled(_extraction(_directory_card(), ("coping",)))
    expected = worldmock_run_setup_module.creation_inputs(path, scenario, {1: compiled})[1]
    output = tmp_path / "persona_coverage.json"
    with caplog.at_level(logging.WARNING):
        result = validate_persona_coverage(path, persona_compiler=lambda story: compiled, coverage_out=output)
    assert result["input_warnings"] == expected
    assert [item["code"] for item in expected] == ["CARD_FIELD_OVERRIDDEN", "PERSONA_AGREEMENT_LOW"]
    assert json.loads(output.read_bytes())["agents"][0]["executable_ratio"] == 1.0
    printed = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Input warning")]
    assert printed == [f"Input warning {item['code']} (agent {item['agent_id']}): {item['message']}" for item in expected]


def test_low_coverage_writes_report_and_warns_before_gate(profile_environment, tmp_path, caplog):
    """@brief 运行输入只警告，validate 保存同一真实缺失项后仍按八成门禁拒绝。"""
    from worldmock.life.world_setup import load_runtime_scenario
    from worldmock.validation import validate_persona_coverage

    _, path = profile_environment
    card = make_card(habits=(), tastes=Tastes(content_genres=("无关散文",), food_preferences=(), media=("B站",)))
    scenario = load_runtime_scenario(path)[0].scenario
    warnings = worldmock_run_setup_module.creation_inputs(path, scenario, {1: card})[1]
    assert [item["code"] for item in warnings] == ["COVERAGE_BELOW_THRESHOLD"]
    assert warnings[0]["details"]["missing"] == [{"kind": "media", "value": "B站"}]
    output = tmp_path / "persona_coverage.json"
    with caplog.at_level(logging.WARNING), pytest.raises(PersonaCoverageError, match="PERSONA_COVERAGE_FAILED"):
        validate_persona_coverage(path, persona_compiler=lambda story: _compiled(_extraction(card)), coverage_out=output)
    report = json.loads(output.read_bytes())["agents"][0]
    assert report["executable_ratio"] == 0 and not report["passed"]
    assert report["missing"] == warnings[0]["details"]["missing"]
    assert any("Input warning COVERAGE_BELOW_THRESHOLD" in record.getMessage() for record in caplog.records)


def test_runtime_warning_caller_logs_and_freezes_all_six_codes(profile_environment, tmp_path, monkeypatch):
    """@brief 真实画像创建调用写入六码 ops 警告并经清单冻结；此处不创建 Engine。"""
    from worldmock.life.world_setup import load_runtime_scenario, _run_manifest
    from worldmock.life.models import RunIdentity

    root, path = profile_environment
    _empty_registered_contents(root)
    _catalog_edit(root, "locations.json", lambda payload: next(
        row for row in payload["locations"] if row["location_id"] == "park").update(name="公园（31.2, 121.5）"))
    payload = json.loads(path.read_bytes())
    payload["agents"][0].update(chronotype_midsleep=4.75, lunch_window=[4.0, 5.0])
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    semantics, digest = load_runtime_scenario(path)
    compiled = _compiled(_extraction(make_card(), ("habits",)))
    monkeypatch.setattr(worldmock_run_setup_module, "_compile_persona", lambda agent: compiled)
    run_dir = tmp_path / "run"
    options = {}
    prepared = SimpleNamespace(workspace_mode="create", scenario=semantics.scenario, scenario_path=path,
                               same_second_continuation=True, env_kwargs={"create_options": options},
                               run_dir=run_dir, start_time=semantics.scenario.start_at)
    specs = runtime._society_specs(prepared)
    warnings = options["input_warnings"]
    assert set(item["code"] for item in warnings) == set(INPUT_WARNING_CODES)
    assert "agent_taste_weights" not in options and "taste_weights" not in specs[0]["profile"]
    records = [json.loads(line) for log in run_dir.glob("logs/ops-*.jsonl") for line in log.read_text().splitlines()]
    warned = [row for row in records if row["component"] == "runtime" and row["level"] == "warn"]
    assert [(row["code"], row["details"]) for row in warned] == [(item["code"], item["details"]) for item in warnings]
    scenario = semantics.scenario
    identity = RunIdentity(run_id="profile-test", source_group_id="profile-test", agent_ids=(1,),
                           start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
                           timezone=scenario.timezone, code_sha256="0" * 64,
                           model_identity=worldmock_run_setup_module._model_identity())
    manifest = _run_manifest(semantics, digest, identity, worldmock_run_setup_module.stage_model_identity(), options)
    assert manifest["schema_version"] == 3 and manifest["input_warnings"] == warnings


def test_warning_caller_rejects_catalog_identity_tampering(profile_environment):
    """@brief 画像入口直接读取当前目录身份，不从旧内容配置补出绿色结果。"""
    from worldmock.life.world_setup import load_runtime_scenario

    root, path = profile_environment
    scenario = load_runtime_scenario(path)[0].scenario
    app_path = root / "catalog/apps.json"
    app_path.write_bytes(app_path.read_bytes() + b" ")
    with pytest.raises(IntegrityError):
        worldmock_run_setup_module.creation_inputs(path, scenario, {1: _directory_card()})
