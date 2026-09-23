import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.profile.conftest import make_card, make_window
from worldmock.delivery.windows import prepare_windows
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export
from worldmock.life.models import StartActivityRequest
from worldmock.profile.coverage import PersonaCoverageError
from worldmock.profile.models import HabitSignature, Tastes
from worldmock.validation import build_coverage_targets, validate_project

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = PROJECT_ROOT / "scenarios" / "white-collar-12-weeks.json"
_WINDOWS_WITHOUT_TEXTWORLD = sys.platform == "win32" and importlib.util.find_spec("textworld") is None
_REQUIRES_TEXTWORLD = pytest.mark.skipif(
    _WINDOWS_WITHOUT_TEXTWORLD,
    reason="TextWorld 1.7.0 has no Windows wheel in the project lockfile",
)


def _compiler(card):
    """确定性画像编译桩：同一卡片响应任意 background_story。"""
    return lambda story: SimpleNamespace(card=card)


def _covered_card():
    """全部命中真实目录目标的卡片（紫薇公园/悬疑/牛肉炒面/电影）。"""
    return make_card(
        habits=(HabitSignature(trigger="下班到家", response="去紫薇公园散步",
                               time_window=make_window(19.0, 20.0)),),
        tastes=Tastes(content_genres=("悬疑",), food_preferences=("牛肉炒面",),
                      media=("电影",)))


@pytest.fixture
def complete_export(tmp_path):
    manifest = {'run_id': 'run', 'source_group_id': 'run', 'agent_ids': [1],
                'model_identity': {'name': 'fixture'}, 'window': {'start': 100, 'end': 200, 'timezone': 'UTC'}}
    store = FootprintStore.create(tmp_path / 'run', manifest, {'version': 0, 'current_time': 100})
    observation = EventDraft(event_type='observation', occurred_at=100, agent_id=1, decision_id='decision',
                              data={'energy_kcal': 100})
    model_request = EventDraft(event_type='model_request', occurred_at=100, agent_id=1,
                               decision_id='decision', visibility='system', data={
                                   'phase': 'request', 'request': {
                                       'model': 'fixture',
                                       'messages': [{'role': 'system', 'content': '身份'},
                                                    {'role': 'user', 'content': '实际场景与人物记忆'}],
                                       'tools': [{'type': 'function', 'function': {
                                           'name': 'start_activity',
                                           'parameters': StartActivityRequest.model_json_schema(
                                               mode='validation')}}],
                                   }})
    request = EventDraft(event_type='action_requested', occurred_at=100, agent_id=1, decision_id='decision',
                         action_id='request', data={'tool_name': 'start_activity', 'request': {
                             'targets': ['自己'], 'actions': ['等待']}})
    store.commit(store.state, [observation, model_request, request])
    store.commit({'version': 1, 'current_time': 200}, [EventDraft(event_type='run_ended', occurred_at=200)])
    reference = store.assets.put_json({'saved': 'complete'}, {'kind': 'fixture'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    directory = tmp_path / 'export'
    export_run(store, point.last_event_seq, directory)
    store.close()
    return directory


def test_production_windows_require_and_accept_complete_authenticated_export(complete_export):
    manifest, events, activities = validate_export(complete_export)
    assert manifest['experiment_completed'] is True
    assert len(events) == 5 and activities == []
    windows = prepare_windows(complete_export)
    assert {task: len(rows) for task, rows in windows.items()} == {'action': 1, 'intent': 1, 'dreaming': 3}
    assert windows['action'][0]['input']['decision_prompt']['messages'][1]['content'] == \
        '实际场景与人物记忆'
    assert windows['action'][0]['provenance']['decision_prompt_event_id'] == events[1]['event_id']


@pytest.mark.parametrize('field', ['version', 'committed', 'completed', 'rows', 'path', 'cutoff', 'window',
                                  'coverage', 'source_runs', 'checkpoint_hash', 'missing_asset', 'event_hash'])
def test_incomplete_or_tampered_export_never_reaches_labelkit(complete_export, field):
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
    with pytest.raises(IntegrityError): prepare_windows(complete_export)


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


@_REQUIRES_TEXTWORLD
def test_validate_project_passes_and_writes_persona_coverage(tmp_path):
    """validate 全链路通过：品类准入+任务受理+两阶段身份+persona_coverage.json。"""
    coverage_out = tmp_path / "persona_coverage.json"
    result = validate_project(PROJECT_ROOT, SCENARIO,
                              persona_compiler=_compiler(_covered_card()),
                              coverage_out=coverage_out)

    assert result["valid"] is True
    assert result["model_identity"] == {"stages": ["action", "decision_statement", "work_review"],
                                        "consistent": True}
    report = result["persona_coverage"][0]
    assert report["agent_id"] == 1 and report["passed"] is True
    assert report["executable_ratio"] >= 0.8
    document = json.loads(coverage_out.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1 and document["threshold"] == 0.8
    assert document["agents"][0]["covered_items"] == report["covered_items"]


@_REQUIRES_TEXTWORLD
def test_validate_project_rejects_coverage_below_threshold(tmp_path):
    """可执行比例低于 80% 时校验失败并列出缺失项（画像写了世界里没有的东西）。"""
    uncovered = make_card(
        habits=(HabitSignature(trigger="通勤", response="听播客两集",
                               time_window=make_window(8.0, 9.0)),),
        tastes=Tastes(content_genres=("西部片", "太空歌剧"), food_preferences=("分子料理",),
                      media=("播客", "全息投影")))

    with pytest.raises(PersonaCoverageError, match="PERSONA_COVERAGE_FAILED") as caught:
        validate_project(PROJECT_ROOT, SCENARIO,
                         persona_compiler=_compiler(uncovered),
                         coverage_out=tmp_path / "persona_coverage.json")
    assert "播客" in str(caught.value)


@_REQUIRES_TEXTWORLD
def test_validate_project_runs_world_category_admission(tmp_path, monkeypatch):
    """世界素材品类准入纳入启动前校验：任一违例即拒绝。"""
    import worldmock.validation as validation_module

    class _Violation:
        code = "CATEGORY_MEALS_MISSING"
        detail = "meals corpus is absent"

    original = validation_module.validate_world_categories
    monkeypatch.setattr(validation_module, "validate_world_categories",
                        lambda catalog, root: SimpleNamespace(ok=False,
                                                              violations=(_Violation(),)))
    try:
        with pytest.raises(IntegrityError, match="World category admission rejected"):
            validate_project(PROJECT_ROOT, SCENARIO,
                             persona_compiler=_compiler(_covered_card()),
                             coverage_out=tmp_path / "persona_coverage.json")
    finally:
        monkeypatch.setattr(validation_module, "validate_world_categories", original)


def test_build_coverage_targets_derives_from_catalog_and_corpora():
    """覆盖率目标集从 catalog 品类/目录条目确定性派生。"""
    from worldmock.catalog.loader import load_catalog
    from worldmock.life.scenario import load_scenario

    scenario = load_scenario(SCENARIO)[0]
    targets = build_coverage_targets(scenario, load_catalog(PROJECT_ROOT / "catalog"),
                                     PROJECT_ROOT / "corpora")

    assert "紫薇公园" in targets.habit_response_keywords
    assert "悬疑" in targets.content_genres
    assert "牛肉炒面" in targets.food_kinds
    assert targets.media_kinds and set(targets.media_kinds) <= {
        "电影", "剧集", "播客", "网文", "新闻", "段子", "字幕体验", "游戏"}
    again = build_coverage_targets(scenario, load_catalog(PROJECT_ROOT / "catalog"),
                                   PROJECT_ROOT / "corpora")
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
    assert result["schema_version"] == 2
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
    code = main(["profile", "validate", "--scenario", str(SCENARIO),
                 "--coverage-out", str(coverage_out)])

    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scenario_id"]
    document = json.loads(coverage_out.read_text(encoding="utf-8"))
    assert document["agents"][0]["passed"] is True
