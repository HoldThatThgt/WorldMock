"""运行输入警告与场景作息覆盖的真实路径（docs/dev/SPEC-dayrun-fidelity.md 1.3、2.1，验收台账「输入警告」「场景作息」）。

新建运行经 runtime 的真实准备与装配（_prepare_new_run → _build_society，画像编译走真实 compile_persona、只打桩三次一致性
抽取）、LifeEnv 与引擎建立运行：警告冻结进运行清单、逐条写 ops 警告、导出后计入 quality.json，警告不使创建失败；
validate（validate_project 与 CLI）打印同一组警告，覆盖率低于 80% 仍失败；场景作息只覆盖显式声明的字段，
身体结算、零点计划输入与真实请求的人设层读同一组值，恢复时核对冻结卡片的作息。
"""

from __future__ import annotations

import copy
import hashlib
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
from tests.life.conftest import (_mutated_scenario, advance_next, stage_day_plan, start_activity,  # noqa: E402
                                 submit_round)
from tests.profile.conftest import make_card, make_window  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.export import export_run  # noqa: E402
from worldmock.life.day_plan_inputs import plan_inputs  # noqa: E402
from worldmock.life.scenario import load_scenario  # noqa: E402
from worldmock.profile.compile import compile_persona  # noqa: E402
from worldmock.profile.coverage import PersonaCoverageError  # noqa: E402
from worldmock.profile.extractor import PersonaExtraction  # noqa: E402
from worldmock.profile.injection import INPUT_WARNING_CODES, apply_scenario_rhythm  # noqa: E402
from worldmock.profile.models import BigFiveProfile, HabitSignature, Tastes, persona_card_struct  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios" / "white-collar-12-weeks.json"
_RHYTHM = ("chronotype_midsleep", "sleep_need_hours", "lunch_window", "dinner_window", "bedtime_window")
#: WC 白领场景声明的作息（与卡片抽取值不同的中点、午餐与晚餐）。
_WC_RHYTHM = {"chronotype_midsleep": 4.75, "sleep_need_hours": 8.0, "lunch_window": [12.0, 12.5],
              "dinner_window": [18.5, 19.5], "bedtime_window": [23.5, 24.5]}


def _scenario(tmp_path: Path, **declared) -> Path:
    """正式场景的副本：人物 1 只显式声明给出的作息字段（其余作息字段从文档删去）。"""
    def mutate(document: dict) -> None:
        agent = document["agents"][0]
        for name in _RHYTHM:
            agent.pop(name, None)
        agent.update(declared)
    return _mutated_scenario(SCENARIO, mutate, tmp_path / "scenario")


def _extraction(card, weak_fields: tuple[str, ...] = ()) -> PersonaExtraction:
    """打桩的三次一致性抽取结果：weak_fields 的众数占比低于 2/3（编译把它们写进 warnings）。"""
    return PersonaExtraction(
        card=card, big_five=BigFiveProfile(openness=5, conscientiousness=4, extraversion=6, agreeableness=4,
                                           neuroticism=3),
        runs=3, agreement=0.9, per_field={name: 1 / 3 for name in weak_fields}, warning=bool(weak_fields),
        model="deepseek-flash", prompt_version="card-v1")


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
    from agentsociety2.env import direct_env_actor
    from custom.envs.life_env import LifeEnv

    import worldmock.profile.compile as compile_module

    monkeypatch.setattr(compile_module, "extract_persona", lambda story: extraction)
    captured: dict = {}
    original = runtime.creation_inputs

    def recording(*arguments):
        captured["specs"], captured["warnings"] = result = original(*arguments)
        return result

    monkeypatch.setattr(runtime, "creation_inputs", recording)
    monkeypatch.setenv("WORKSPACE_PATH", str(ROOT))
    monkeypatch.setattr(llm_dispatcher, "build_client_for_role", lambda role: role)
    monkeypatch.setattr(direct_env_actor, "get_direct_env_actor_class",
                        lambda: SimpleNamespace(remote=lambda spec: (captured.__setitem__("actor", spec), "actor")[1]))
    prepared = runtime._prepare_new_run(runtime.RunRequest(ROOT, scenario_path, tmp_path / "run", "warned", "warned"))
    runtime._build_society(prepared)
    options = captured["actor"].env_kwargs["LifeEnv"]
    env = LifeEnv(**options)
    await env.init(prepared.start_time)
    return {"prepared": prepared, "env": env, "options": options["create_options"], **captured}


def _map_variant(tmp_path: Path, rename: tuple[str, str], unknown_route: tuple[str, str]) -> Path:
    """正式场景的副本，改用一份隔离语料：地点改显示名、一条驾车路线改成 unknown，按真实准入重新钉扎地图与语料清单。"""
    corpus = tmp_path / "corpora"
    shutil.copytree(ROOT / "corpora", corpus, copy_function=_link_or_copy)
    places = json.loads((corpus / "zhangjiang-map/locations.json").read_text(encoding="utf-8"))
    next(item for item in places["locations"] if item["location_id"] == rename[0])["name"] = rename[1]
    routes = json.loads((corpus / "zhangjiang-map/routes.json").read_text(encoding="utf-8"))
    route = next(item for item in routes["routes"]
                 if (item["origin_id"], item["destination_id"], item["mode"]) == (*unknown_route, "driving"))
    route.update(status="unknown", reason="route not prepared for this test", distance_meters=None,
                 duration_seconds=None, fare_fen=None, response_path=None, response_sha256=None)
    dataset = json.loads((corpus / "zhangjiang-map/dataset.json").read_text(encoding="utf-8"))
    dataset["locations_file"]["sha256"] = _rewrite(corpus / "zhangjiang-map/locations.json", places)
    dataset["routes_file"]["sha256"] = _rewrite(corpus / "zhangjiang-map/routes.json", routes)
    dataset_sha = _rewrite(corpus / "zhangjiang-map/dataset.json", dataset)
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        if entry["path"].startswith("zhangjiang-map/") and entry["path"].count("/") == 1:
            path = corpus / entry["path"]
            entry.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    document = json.loads(SCENARIO.read_text(encoding="utf-8"))
    document.update(corpus_root=str(corpus), corpus_manifest_sha256=_rewrite(corpus / "manifest.json", manifest))
    document["map_dataset"]["sha256"] = dataset_sha
    shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    (tmp_path / "scenarios").mkdir()
    path = tmp_path / "scenarios" / "map-variant.json"
    _rewrite(path, document)
    return path


def _link_or_copy(source: str, target: str) -> None:
    """隔离语料副本：同一卷上用硬链接，跨卷时复制（改写前先解除链接，正式语料字节不变）。"""
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _rewrite(path: Path, value: dict) -> str:
    """解除硬链接后写入 JSON，返回新字节的 SHA-256。"""
    if path.exists():
        path.unlink()
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    """输入警告：新建运行时逐条计算，冻结进清单 input_warnings（清单版本 2）、逐条写一条 runtime 组件的 warn 级 ops 记录，
    导出后 quality.json（结构版本 3）的 input_warnings 一节按码计数；题材对不上、权重全零、覆盖率低于 80% 与编译一致性
    偏低都不使创建失败，运行照常推进。"""
    path = _scenario(tmp_path, chronotype_midsleep=4.75)
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(_platform_card(), ("habits",)))
    env, warnings = created["env"], created["warnings"]
    try:
        manifest = env.engine.store.manifest
        assert manifest["schema_version"] == 2
        assert manifest["input_warnings"] == warnings == created["options"]["input_warnings"]
        codes = [item["code"] for item in warnings]
        assert {"TASTE_GENRES_UNMATCHED", "CONTENT_WEIGHTS_ALL_ZERO", "COVERAGE_BELOW_THRESHOLD",
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
        export_run(env.engine.store, env.engine.store.last_event_seq, tmp_path / "export")
        quality = json.loads((tmp_path / "export" / "quality.json").read_text(encoding="utf-8"))
    finally:
        await env.close()
    section = quality["input_warnings"]
    assert quality["schema_version"] == 3 and section["recorded"] is True
    assert section["warning_count"] == len(warnings)
    assert section["code_counts"] == {code: codes.count(code) for code in INPUT_WARNING_CODES}
    assert section["agent_counts"] == {"1": len(warnings)} and section["scenario_warning_count"] == 0


async def test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds(tmp_path, monkeypatch) -> None:
    """裁决 D19（规格 8.1）：地点显示名带坐标、配送门店到声明目的地的驾车路线是 unknown 都只发场景级输入警告——
    真实新建路径照常建立运行，清单冻结 LOCATION_NAME_SUSPICIOUS 与 DELIVERY_ROUTE_UNAVAILABLE（列出受影响的供给）。"""
    path = _map_variant(tmp_path, ("poi-1868478037", "篮球场（31.2, 121.5）"), ("market", "poi-1868478037"))
    created = await _create_run(tmp_path, monkeypatch, path, _extraction(make_card()))
    env = created["env"]
    try:
        manifest = env.engine.store.manifest
        loaded = {(item.origin_id, item.destination_id, item.mode): item.status for item in env.engine.scenario.routes}
        names = {item.location_id: item.name for item in env.engine.scenario.locations}
    finally:
        await env.close()
    assert loaded[("market", "poi-1868478037", "driving")] == "unknown"
    assert names["poi-1868478037"] == "篮球场（31.2, 121.5）"
    scenario_level = [item for item in manifest["input_warnings"] if item["agent_id"] is None]
    assert [item["code"] for item in scenario_level] == ["DELIVERY_ROUTE_UNAVAILABLE", "LOCATION_NAME_SUSPICIOUS"]
    route, name = (item["details"] for item in scenario_level)
    assert (route["location_id"], route["destination_id"], route["route_status"]) == ("market", "poi-1868478037",
                                                                                         "unknown")
    assert len(route["supply_ids"]) == 159 and route["supply_ids"] == sorted(route["supply_ids"])
    assert name == {"location_id": "poi-1868478037", "name": "篮球场（31.2, 121.5）", "patterns": ["coordinates"]}


async def test_validate_prints_the_same_warnings_the_run_froze(tmp_path, monkeypatch, capsys, caplog) -> None:
    """validate 与运行路径一致：同一份编译结果下，worldmock validate（validate_project）输出的 input_warnings 与新建运行
    清单冻结的警告完全相同（场景作息覆盖走同一个函数），并逐条写到标准错误流；覆盖率达标时校验通过。"""
    import worldmock.validation as validation
    from worldmock.cli import main

    covered = make_card(habits=(HabitSignature(trigger="下班到家", response="去紫薇公园散步",
                                               time_window=make_window(19.0, 20.0)),),
                        tastes=Tastes(content_genres=("悬疑",), food_preferences=("牛肉炒面",), media=("电影",)))
    extraction = _extraction(covered, ("coping",))
    compiled = _compiled(extraction)
    path = _scenario(tmp_path, **_WC_RHYTHM)
    created = await _create_run(tmp_path, monkeypatch, path, extraction)
    try:
        frozen = created["env"].engine.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    monkeypatch.setattr("worldmock.runtime._configure_process", lambda _root: None)
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
    monkeypatch.setattr("worldmock.runtime._configure_process", lambda _root: None)
    monkeypatch.setattr(validation, "_compile_persona", lambda story: compiled)

    with caplog.at_level(logging.WARNING):
        code = main(["--project-root", str(_validate_root(tmp_path)), "validate", "--scenario", str(path),
                     "--coverage-out", str(tmp_path / "coverage.json")])
    _, warnings = runtime.creation_inputs(path, load_scenario(path)[0], {1: compiled})

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
                                 "dinner": "18点半到19点半", "bedtime": "23点到0点", "sleep_midpoint": "4点三刻",
                                 "sleep_need": "8 小时"}
    assert profile["persona_card_struct"] == persona_card_struct(card.model_copy(update={"chronotype_midsleep": 4.75}))


async def test_body_settlement_day_plan_inputs_and_the_persona_layer_read_the_same_rhythm(
        tmp_path, monkeypatch) -> None:
    """场景作息：场景声明全部五个作息字段（WC 白领的值）时，引擎的身体（两过程睡眠的中点与睡眠需要）、零点计划输入的
    三个时段与真实请求人设层的作息是同一组值；卡片抽取的不同值不再出现在人设层。"""
    created = await _create_run(tmp_path, monkeypatch, _scenario(tmp_path, **_WC_RHYTHM), _extraction(make_card()))
    env = created["env"]
    try:
        body = env.engine.state.agents[1].body
        person = plan_inputs(env.engine, 1).payload["人物"]
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
        frozen = created["env"].engine.store.manifest["input_warnings"]
    finally:
        await created["env"].close()
    profile = created["specs"][0]["profile"]

    conflicts = [item for item in frozen if item["code"] == "CARD_FIELD_CONFLICT"]
    assert [item["details"] for item in conflicts] == [{"field": "lunch_window", "card_value": [12.0, 13.0],
                                                        "scenario_value": [6.0, 6.5], "reason": "anchor_order"}]
    assert profile["persona_card_struct"]["time_anchors"]["lunch"] == {"start_hour": 12.0, "end_hour": 13.0}
    assert _persona_layer(profile)["rhythm"]["lunch"] == "12点到13点"
    assert profile["persona_card_struct"]["chronotype_midsleep"] == 4.75


def test_restore_verifies_the_frozen_card_rhythm_against_the_scenario(tmp_path) -> None:
    """恢复时校验一致：新建运行冻结的（经场景作息覆盖的）结构化卡片照常恢复；作息与场景显式声明不一致的卡片
    （例如直接存了抽取值）阻止分支发布。"""
    from tests.integration.test_host_branch_coverage import (ResumeRequest, _agent_workspace, _parent_store,
                                                             _prepare_resume)

    scenario, _ = load_scenario(SCENARIO)
    card = make_card()
    overridden = apply_scenario_rhythm(card, scenario.agents[0]).card
    assert overridden != card
    for name, struct in (("frozen", persona_card_struct(overridden)), ("extracted", persona_card_struct(card))):
        agent = {"schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
                 "profile": {"id": 1, "same_second_continuation": True, "persona_card_struct": struct}}
        root = tmp_path / name
        root.mkdir()
        parent, checkpoint = _parent_store(root, workspace_builder=lambda path, document=agent: _agent_workspace(
            path, agent_document=document))
        try:
            request = ResumeRequest(ROOT, SCENARIO, root / "parent", checkpoint.checkpoint_id, root / "child")
            if name == "frozen":
                assert _prepare_resume(request).workspace_mode == "restored"
            else:
                with pytest.raises(IntegrityError, match="rhythm disagrees with the scenario"):
                    _prepare_resume(request)
                assert not (root / "child").exists()
        finally:
            parent.close()
