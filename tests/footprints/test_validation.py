import worldmock.run_setup as worldmock_run_setup_module
import hashlib
import json
import os
import sys
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.integration.as2_fixtures import use_fake_as2
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT, ENVIRONMENT_ROOT
from tests.profile.conftest import make_card, make_window
from tests.footprints.prompt_fixtures import layered_user
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export
from worldmock.life.models import StartActivityRequest
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.life.run_identity import environment_identity
from worldmock.profile.coverage import PersonaCoverageError
from worldmock.profile.models import HabitSignature, Tastes
from worldmock.profile.coverage_targets import build_coverage_targets
from worldmock.validation import validate_project

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"



@pytest.fixture(autouse=True)
def _locked_fake_as2(monkeypatch, tmp_path_factory):
    """validate_project 先核对 AS2：本文件解析到锁定的临时 AS2 包，不依赖本机实际安装的 AS2。"""
    return use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))


def _compiler(card):
    """确定性画像编译桩：同一卡片响应任意 background_story。"""
    return lambda story: SimpleNamespace(card=card)


def _covered_card():
    """全部命中真实目录目标的卡片（紫薇公园/悬疑/牛肉炒面/电影）。"""
    return make_card(
        habits=(HabitSignature(trigger="下班到家", response="去五里公园散步",
                               time_window=make_window(19.0, 20.0)),),
        tastes=Tastes(content_genres=("悬疑",), food_preferences=("牛肉面",),
                      media=("长视频",)))


def _story_scenario(tmp_path):
    """@brief 隔离副本明确声明人物故事，画像验证不得借人工环境缺省无故事而空跑。"""
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "scenarios/single-person.json"
    document = json.loads(path.read_text())
    document["agents"][0]["background_story"] = (
        "人物在家里安静休息，常吃牛肉面，下班去五里公园散步，使用哔哩哔哩观看长视频。" * 4)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def complete_export(tmp_path):
    manifest = {'schema_version': 3, 'environment_identity': environment_identity(load_runtime_scenario(SCENARIO)[0]),
                'run_id': 'run', 'source_group_id': 'run', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'}, 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    store = FootprintStore.create(tmp_path / 'run', manifest, {'schema_version': 10, 'version': 0, 'current_time': 100})
    observation = EventDraft(event_type='observation', occurred_at=100, agent_id=1, decision_id='decision',
                              data={'energy_kcal': 100})
    model_request = EventDraft(event_type='model_request', occurred_at=100, agent_id=1,
                               decision_id='decision', visibility='system', data={
                                   'phase': 'request', 'request': {
                                       'model': 'fixture',
                                       'messages': [{'role': 'system', 'content': '身份'}, layered_user()],
                                       'tools': [{'type': 'function', 'function': {
                                           'name': 'start_activity',
                                           'parameters': StartActivityRequest.model_json_schema(
                                               mode='validation')}}],
                                   }})
    request = EventDraft(event_type='action_requested', occurred_at=100, agent_id=1, decision_id='decision',
                         action_id='request', data={'tool_name': 'start_activity', 'request': StartActivityRequest(
                             items=[{'target': '自己', 'way': '等待', 'from': None, 'to': None}],
                             answering=None, prefer=None).model_dump(mode='json')})
    store.commit(store.state, [observation, model_request, request])
    store.commit({'schema_version': 10, 'version': 1, 'current_time': 200}, [EventDraft(event_type='run_ended', occurred_at=200, visibility='system')])
    reference = store.assets.put_json({'saved': 'complete'}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    directory = tmp_path / 'export'
    export_run(store, point.last_event_seq, directory)
    store.close()
    return directory


def test_export_accepts_complete_authenticated_run(complete_export):
    """@brief 完整模拟终态可经公共导出认证，并保留真实模型请求。"""
    manifest, events, activities = validate_export(complete_export)
    assert manifest['experiment_completed'] is True
    assert len(events) == 5 and activities == []
    assert events[1]['data']['request']['messages'][1] == layered_user()

@pytest.mark.parametrize('field', ['version', 'committed', 'completed', 'rows', 'path', 'cutoff', 'window',
                                  'coverage', 'source_runs', 'checkpoint_hash', 'missing_asset', 'event_hash'])
def test_incomplete_or_tampered_export_is_rejected(complete_export, field):
    path = complete_export / 'manifest.json'
    manifest = json.loads(path.read_text())
    if field == 'version': manifest['schema_version'] = 999
    if field == 'committed': manifest['committed'] = False
    if field == 'completed': manifest['experiment_completed'] = False
    if field == 'rows': manifest['artifacts']['events']['rows'] = 999
    if field == 'path': manifest['artifacts']['events']['path'] = 'wrong.jsonl'
    if field == 'cutoff': manifest['cutoff_event_seq'] = 999
    if field == 'window': manifest['window']['end'] = '2026-03-30T00:00:00+00:00'
    if field == 'coverage': manifest['daily_coverage'] = []
    if field == 'source_runs': manifest['source_run_ids'] = ['invented']
    if field == 'checkpoint_hash': manifest['checkpoint']['record_sha256'] = '0' * 64
    if field == 'missing_asset': next((complete_export / 'assets').iterdir()).unlink()
    if field == 'event_hash': (complete_export / 'events.jsonl').write_text('{}\n')
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): validate_export(complete_export)


@pytest.mark.parametrize('rehash', [False, True])
def test_quality_report_requires_bytes_and_actual_event_recomputation(complete_export, rehash):
    path = complete_export / 'quality.json'
    quality = json.loads(path.read_text())
    quality['actions']['unique_action_ids'] = 999
    path.write_text(json.dumps(quality))
    if rehash:
        manifest_path = complete_export / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['quality_report'].update(size_bytes=path.stat().st_size,
                                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): validate_export(complete_export)


@pytest.mark.parametrize("alteration", ["version_two", "missing_app_usage", "extra_artifact"])
def test_export_rejects_retired_version_or_noncurrent_artifact_set(complete_export, alteration):
    """@brief 当前五文件合同是封闭集合；旧版本、遗漏和额外文件都不能进入下游。"""
    path = complete_export / "manifest.json"
    manifest = json.loads(path.read_text())
    if alteration == "version_two":
        manifest["schema_version"] = 2
    elif alteration == "missing_app_usage":
        manifest["artifacts"].pop("app_usage")
    else:
        manifest["artifacts"]["invented"] = dict(manifest["artifacts"]["events"])
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError):
        validate_export(complete_export)


def test_validate_project_passes_and_writes_persona_coverage(tmp_path, monkeypatch):
    """validate 全链路通过：品类准入+任务受理+阶段身份+persona_coverage.json。"""
    monkeypatch.setattr(worldmock_run_setup_module, "_configure_process", lambda _root: None)
    coverage_out = tmp_path / "persona_coverage.json"
    result = validate_project(PROJECT_ROOT, _story_scenario(tmp_path),
                              persona_compiler=_compiler(_covered_card()),
                              coverage_out=coverage_out)

    assert result["valid"] is True
    assert result["model_identity"] == {"stages": ["action"],
                                        "consistent": True}
    report = result["persona_coverage"][0]
    assert report["agent_id"] == 1 and report["passed"] is True
    assert report["executable_ratio"] >= 0.8
    document = json.loads(coverage_out.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1 and document["threshold"] == 0.8
    assert document["agents"][0]["covered_items"] == report["covered_items"]


def test_validate_project_rejects_a_retired_stage_through_the_manifest_validator(tmp_path, monkeypatch):
    """校验入口与新建、打开、恢复同用 validate_manifest_stage_identity：新运行将冻结的阶段身份含退役的决策自述阶段即拒绝
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第七章「清单」）。"""
    import worldmock.runtime as runtime
    import worldmock.validation as validation_module

    monkeypatch.setattr(worldmock_run_setup_module, "_configure_process", lambda _root: None)
    retired = worldmock_run_setup_module.stage_model_identity()
    retired["stages"]["decision_statement"] = {"thinking": "disabled"}
    monkeypatch.setattr(worldmock_run_setup_module, 'stage_model_identity', lambda *_args: retired)
    seen = []
    original = worldmock_run_setup_module.validate_manifest_stage_identity
    monkeypatch.setattr(worldmock_run_setup_module, 'validate_manifest_stage_identity', lambda manifest=None: seen.append(manifest) or original(manifest))

    with pytest.raises(IntegrityError, match="Model identity stage decision_statement is retired"):
        validate_project(PROJECT_ROOT, _story_scenario(tmp_path), persona_compiler=_compiler(_covered_card()),
                         coverage_out=tmp_path / "persona_coverage.json")
    assert seen == [None]


def test_validate_project_rejects_coverage_below_threshold(tmp_path, monkeypatch):
    """可执行比例低于 80% 时校验失败并列出缺失项（画像写了世界里没有的东西）。"""
    monkeypatch.setattr(worldmock_run_setup_module, "_configure_process", lambda _root: None)
    uncovered = make_card(
        habits=(HabitSignature(trigger="通勤", response="听播客两集",
                               time_window=make_window(8.0, 9.0)),),
        tastes=Tastes(content_genres=("西部片", "太空歌剧"), food_preferences=("分子料理",),
                      media=("播客", "全息投影")))

    with pytest.raises(PersonaCoverageError, match="PERSONA_COVERAGE_FAILED") as caught:
        validate_project(PROJECT_ROOT, _story_scenario(tmp_path),
                         persona_compiler=_compiler(uncovered),
                         coverage_out=tmp_path / "persona_coverage.json")
    assert "播客" in str(caught.value)


def test_validate_project_rejects_an_invalid_current_food_registration(tmp_path, monkeypatch):
    """@brief 当前食品绑定违反真实登记合同，在任何人物启动或画像生成前即拒绝。"""
    from worldmock.catalog.identity import refresh_manifest_identity

    monkeypatch.setattr(worldmock_run_setup_module, "_configure_process", lambda _root: None)
    path = _story_scenario(tmp_path)
    foods = path.parent.parent / "catalog/foods.json"
    payload = json.loads(foods.read_text())
    payload["foods"][0]["store_ids"] = ["unknown-store"]
    foods.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    refresh_manifest_identity(foods.parent)
    output = tmp_path / "persona_coverage.json"
    with pytest.raises(IntegrityError, match="ENVIRONMENT_REF_UNKNOWN"):
        validate_project(PROJECT_ROOT, path, persona_compiler=_compiler(_covered_card()), coverage_out=output)
    assert not output.exists()


def test_build_coverage_targets_derives_from_catalog_and_corpora():
    """覆盖率目标集从 catalog 品类/目录条目确定性派生。"""
    from worldmock.catalog.loader import load_catalog
    from worldmock.life.world_setup import load_scenario

    scenario = load_scenario(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]
    targets = build_coverage_targets(scenario, load_catalog(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog"),
                                     ARTIFICIAL_ENVIRONMENT_ROOT / "corpora")

    assert "五里公园" in targets.habit_response_keywords
    assert "安静休息" in targets.habit_response_keywords
    assert "牛肉面" in targets.food_kinds
    assert set(targets.media_kinds) == {"文章", "图文", "短视频", "长视频", "实况"}
    assert set(targets.app_names) == {"哔哩哔哩", "抖音", "饿了么", "美团"}
    assert "content_genres" not in type(targets).model_fields
    again = build_coverage_targets(scenario, load_catalog(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog"),
                                   ARTIFICIAL_ENVIRONMENT_ROOT / "corpora")
    assert again == targets


def _fake_extraction(card):
    """构造走完整 compile_persona 路径的确定性抽取结果。"""
    from worldmock.profile.extractor import PersonaExtraction
    from worldmock.profile.models import BigFiveProfile

    return PersonaExtraction(
        card=card,
        big_five=BigFiveProfile(openness=5, conscientiousness=4, extraversion=6,
                                agreeableness=4, neuroticism=3),
        runs=3, agreement=1.0, per_field={},
        warning=False, model="deepseek-chat", prompt_version="card-v1")


def test_profile_cli_compile_outputs_card_and_rendered_text(tmp_path, monkeypatch, capsys):
    """profile compile（v2）：卡片+大五+世界层动力学+渲染文本。"""
    from worldmock.cli import main
    from worldmock.profile import extractor

    story = tmp_path / "story.txt"
    story.write_text("陈曦，32岁，产品经理，住在上海张江。", encoding="utf-8")
    monkeypatch.setattr(extractor, "extract_persona",
                        lambda text, n_runs: _fake_extraction(make_card()))

    code = main(["profile", "compile", "--text", str(story), "--runs", "3"])

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 3
    assert result["card"]["identity"]["occupation"] == "产品经理"
    assert result["persona_card_text"].startswith("产品经理，32岁")
    assert "big_five" in result and "world_dynamics" in result and "source" in result


def test_profile_cli_check_asserts_three_compile_consistency(tmp_path, monkeypatch, capsys):
    """profile check：三次编译卡片一致即通过，漂移即失败（非零退出）。"""
    from worldmock.cli import main
    from worldmock.profile import extractor

    story = tmp_path / "story.txt"
    story.write_text("陈曦，32岁，产品经理，住在上海张江。", encoding="utf-8")
    monkeypatch.setattr(extractor, "extract_persona",
                        lambda text, n_runs: _fake_extraction(make_card()))
    assert main(["profile", "check", "--text", str(story)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["card_mode_hit"] == 1.0
    assert all(rate == 1.0 for rate in report["big_five_mode_hit"].values())

    drifted = iter([make_card(coping="跑步解压"), make_card(coping="找朋友聊天"),
                     make_card()])
    monkeypatch.setattr(extractor, "extract_persona",
                        lambda text, n_runs: _fake_extraction(next(drifted)))
    assert main(["profile", "check", "--text", str(story)]) == 1


def test_profile_cli_validate_writes_coverage(tmp_path, monkeypatch, capsys):
    """profile validate：输出 persona_coverage.json（与 validate 同一覆盖门禁）。"""
    import worldmock.validation as validation_module
    from worldmock.cli import main

    coverage_out = tmp_path / "persona_coverage.json"
    monkeypatch.setattr(validation_module, "_compile_persona",
                        lambda story: SimpleNamespace(card=_covered_card()))
    code = main(["profile", "validate", "--scenario", str(_story_scenario(tmp_path)),
                 "--coverage-out", str(coverage_out)])

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scenario_id"]
    document = json.loads(coverage_out.read_text(encoding="utf-8"))
    assert document["agents"][0]["passed"] is True


@pytest.mark.parametrize("alteration", ["world_nine", "missing_world", "run_two", "missing_identity",
                                       "extra_identity", "bad_hash", "bad_rules", "blank_version"])
def test_current_export_identity_is_required_before_any_consumer(complete_export, alteration):
    """@brief 当前世界、运行版本和完整环境身份缺一不可，所有下游在读取前拒绝。"""
    path = complete_export / 'manifest.json'
    manifest = json.loads(path.read_bytes())
    frozen = manifest['frozen_run_manifest']
    if alteration == 'world_nine': manifest['checkpoint']['state']['schema_version'] = 9
    if alteration == 'missing_world': manifest['checkpoint']['state'].pop('schema_version')
    if alteration == 'run_two': frozen['schema_version'] = 2
    if alteration == 'missing_identity': frozen.pop('environment_identity')
    if alteration == 'extra_identity': frozen['environment_identity']['extra'] = 'invented'
    if alteration == 'bad_hash': frozen['environment_identity']['map_sha256'] = 'invented'
    if alteration == 'bad_rules': frozen['environment_identity']['rules_version'] = True
    if alteration == 'blank_version': frozen['environment_identity']['environment_version'] = ' '
    path.write_text(json.dumps(manifest), encoding='utf-8')
    message = 'world schema' if alteration in {'world_nine', 'missing_world'} else 'environment identity'
    with pytest.raises(IntegrityError, match=message):
        validate_export(complete_export)
    with pytest.raises(IntegrityError, match=message):
        validate_export(complete_export)
