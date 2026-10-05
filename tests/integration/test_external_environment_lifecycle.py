"""@brief 人工环境经真实加载、动作、关闭恢复和导出验证，不调用模型。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.life.conftest import advance_next, conclude_request, report_request, start_activity, submit_round
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityStatus, ModelIdentity, RunIdentity
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.models import active_activities
from worldmock.life.scenario import sha256_file


def _identity(path: Path) -> RunIdentity:
    """@brief 冻结离线引擎直驱运行的输入身份，不创建任何模型客户端。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    return RunIdentity(
        run_id="external-environment-test", source_group_id="external-environment-group",
        agent_ids=tuple(item["agent_id"] for item in document["agents"]),
        start_at=datetime.fromisoformat(document["start_at"]).isoformat(),
        end_at=datetime.fromisoformat(document["end_at"]).isoformat(), timezone=document["timezone"],
        code_sha256="2" * 64,
        model_identity=ModelIdentity(
            provider="openai_compatible", base_url="https://api.deepseek.com/beta", requested_model="deepseek-flash",
            thinking="enabled", tool_choice="auto", max_retries=0,
            prompt_sha256="0" * 64, tool_schema_sha256="1" * 64),
    )


def _complete_run(engine: LifeEngine, root: Path):
    """@brief 用真实请求走过剩余边界，再冻结真实人工人物目录和生活参与者检查点。"""
    for step in range(128):
        state = engine.scheduling_state()
        if state.next_event_at is None:
            break
        if state.ready_agent_ids:
            requests = {}
            for agent_id in state.ready_agent_ids:
                pending = pending_conclusions(engine.context.state, agent_id)
                active = active_activities(engine.context.state, agent_id)
                if pending:
                    requests[agent_id] = ("conclude_activity", conclude_request(*(item.name for item in pending)))
                elif active:
                    requests[agent_id] = ("report_status", report_request(*(item.name for item in active)))
                else:
                    requests[agent_id] = ("start_activity", start_activity("等待", "自己"))
            _, results = submit_round(engine, requests, f"complete-{step}")
            assert all(result.status in {"completed", "in_progress"} for result in results)
        else:
            advance_next(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    snapshots = {}
    for agent_id, agent in engine.context.state.agents.items():
        workspace = root / f"agent-{agent_id}"
        workspace.mkdir()
        (workspace / "state.json").write_text(agent.model_dump_json(), encoding="utf-8")
        snapshots[f"agent:{agent_id}"] = snapshot_directory(workspace, engine.context.store.assets)
    return engine.checkpoint(snapshots)


@pytest.mark.parametrize("scenario_name", ["single-person.json", "multi-person.json", "multi-person-half-day.json"])
def test_external_environment_survives_actions_reopen_and_export(tmp_path: Path, scenario_name: str):
    """@brief 显式外部场景路径足以完成真实生命周期，标题和效果取人工环境。"""
    root = tmp_path / "external-environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path, run = root / "scenarios" / scenario_name, tmp_path / "run"
    engine = LifeEngine.create(path, run, _identity(path))
    try:
        assert engine.context.corpus_root == root / "corpora"
        assert engine.context.rules.content_titles == {"test-content": "人工测试内容"}
        requests = {agent: ("start_activity", start_activity("进行", "安静休息"))
                    for agent in engine.scheduling_state().ready_agent_ids}
        _, results = submit_round(engine, requests, "start-rest")
        assert all(result.status == "in_progress" for result in results)
        advance_next(engine)
        saved = engine.context.state.model_dump(mode="json")
        engine.close()
        engine = LifeEngine.open(path, run)
        assert engine.context.state.model_dump(mode="json") == saved
        assert all(activity.data["effects"] == {"entertainment": 0.8, "social": 0.1, "health": 0.2}
                   for activity in engine.context.state.activities.values())
        checkpoint = _complete_run(engine, tmp_path)
        destination = tmp_path / "export"
        manifest = export_run(engine.context.store, checkpoint.last_event_seq, destination)
        checked, events, activities = validate_export(destination)
        assert checked["run_id"] == manifest["run_id"]
        assert activities and any(event["event_type"] == "action_result" for event in events)
    finally:
        engine.close()


def _environment_with_effects(root: Path, effects: dict[str, float]) -> Path:
    """@brief 复制人工环境，只改条目效果和反馈间隔，再绑定实际语料与场景字节。"""
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    items_path = root / "corpora/lifestyle/items.json"
    items = json.loads(items_path.read_text(encoding="utf-8"))
    assert [item["activity_id"] for item in items["items"]] == ["quiet-rest"]
    items["items"][0]["effects"] = effects
    items_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    manifest_path = root / "corpora/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(entry for entry in manifest["entries"] if entry["path"] == "lifestyle/items.json")
    entry.update(sha256=sha256_file(items_path), size_bytes=items_path.stat().st_size)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    path = root / "scenarios/single-person.json"
    scenario = json.loads(path.read_text(encoding="utf-8"))
    scenario["corpus_manifest_sha256"] = sha256_file(manifest_path)
    scenario["activity_feedback_seconds"] = 300
    path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    return path


def _start_effect_rest(engine: LifeEngine, effects: dict[str, float]) -> str:
    """@brief 经真实请求启动条目，并验证实际启动路径冻结完整环境效果。"""
    item, = engine.context.lifestyle_items
    assert item.activity_id == "quiet-rest" and dict(item.effects) == effects
    _, results = submit_round(engine, {1: ("start_activity", start_activity("进行", "安静休息"))}, "start-rest")
    result, = results
    assert result.status == "in_progress"
    activity = engine.context.state.activities[result.activity_id]
    assert activity.target_id == "quiet-rest"
    assert activity.data["effects"] == effects
    assert result.data["activity"]["data"]["effects"] == effects
    return activity.activity_id


def _accumulate_awake_needs(engine: LifeEngine) -> None:
    """@brief 真实等待半小时积累需要，避免零初值把两种娱乐缓解都钳制为零。"""
    started_at = engine.context.state.current_time
    for step in range(6):
        _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, f"wait-{step}")
        result, = results
        assert result.status == "in_progress"
        for _ in range(8):
            activity = engine.context.state.activities[result.activity_id]
            if activity.status == ActivityStatus.COMPLETED:
                break
            if engine.scheduling_state().ready_agent_ids:
                _, reports = submit_round(engine, {1: ("report_status", report_request(activity.name))},
                                           f"wait-report-{step}-{activity.elapsed_seconds}")
                assert all(report.status == "in_progress" for report in reports)
            advance_next(engine)
        activity = engine.context.state.activities[result.activity_id]
        assert activity.status == ActivityStatus.COMPLETED and activity.elapsed_seconds == 300
        _, conclusions = submit_round(engine, {1: ("conclude_activity", conclude_request(activity.name))},
                                      f"wait-conclude-{step}")
        assert all(conclusion.status == "completed" for conclusion in conclusions)
    assert engine.context.state.current_time - started_at == 1800
    assert engine.context.state.agents[1].body.entertainment_need > .03


def _advance_effect_rest(engine: LifeEngine, activity_id: str, effects: dict[str, float]) -> None:
    """@brief 真实播报和推进后核对三个身体通道，数值按输入效果及实际区间计算。"""
    activity = engine.context.state.activities[activity_id]
    assert activity.status == ActivityStatus.ACTIVE and activity.data["effects"] == effects
    if engine.scheduling_state().ready_agent_ids:
        _, results = submit_round(engine, {1: ("report_status", report_request(activity.name))},
                                  f"report-rest-{activity.elapsed_seconds}")
        assert all(result.status == "in_progress" for result in results)
    before = engine.context.state.agents[1].model_copy(deep=True)
    start = engine.context.state.current_time
    advance_next(engine)
    hours = (engine.context.state.current_time - start) / 3600
    assert hours > 0
    body = engine.context.state.agents[1].body
    entertainment = max(0, before.body.entertainment_need - effects["entertainment"] * .5 * hours)
    assert body.entertainment_need == pytest.approx(entertainment)
    social = max(0, before.body.social_need - effects["social"] * .5 * hours) + .04 * hours
    assert body.social_need == pytest.approx(social)
    mood = before.body.mood_valence + effects["health"] * .1 * hours * (1 + .5 * before.body.neuroticism_norm)
    if before.mood_updated_at is not None:
        minutes = (engine.context.state.current_time - before.mood_updated_at) / 60
        mood *= .5 ** (minutes / (45 * (1 + .3 * before.body.neuroticism_norm)))
    assert body.mood_valence == pytest.approx(mood)
    assert engine.context.state.activities[activity_id].data["effects"] == effects


def _export_effect_run(engine: LifeEngine, root: Path, activity_id: str, effects: dict[str, float]) -> None:
    """@brief 完成真实运行并验证导出中的同一活动仍保存启动效果。"""
    checkpoint = _complete_run(engine, root)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == ActivityStatus.COMPLETED and activity.data["effects"] == effects
    destination = root / "export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, destination)
    checked, events, activities = validate_export(destination)
    assert checked["run_id"] == manifest["run_id"]
    exported = next(item for item in activities if item["activity_id"] == activity_id)
    assert exported["target_id"] == "quiet-rest" and exported["status"] == "completed"
    assert exported["data"]["effects"] == effects
    assert any(event["event_type"] == "activity_progress" and event["activity_id"] == activity_id for event in events)


def test_same_lifestyle_identity_freezes_each_external_environment_through_real_lifecycle(tmp_path: Path):
    """@brief 两个同时加载的外部世界经真实启动、继续、恢复和导出各自使用条目效果。"""
    variants = {"mild": {"entertainment": .2, "social": .3, "health": .4},
                "strong": {"entertainment": .6, "social": .7, "health": .9}}
    paths = {name: _environment_with_effects(tmp_path / name, effects) for name, effects in variants.items()}
    engines, identities, outcomes = {}, {}, {}
    try:
        for name, path in paths.items():
            engines[name] = LifeEngine.create(path, tmp_path / name / "run", _identity(path))
        for engine in engines.values():
            _accumulate_awake_needs(engine)
        for name, effects in variants.items():
            identities[name] = _start_effect_rest(engines[name], effects)
        for name, effects in variants.items():
            engine, activity_id = engines[name], identities[name]
            _advance_effect_rest(engine, activity_id, effects)
            activity = engine.context.state.activities[activity_id]
            assert activity.status == ActivityStatus.ACTIVE
            assert 0 < activity.elapsed_seconds < activity.planned_duration_seconds
            saved = engine.context.state.model_dump(mode="json")
            engine.close()
            engines[name] = LifeEngine.open(paths[name], tmp_path / name / "run")
            assert engines[name].context.state.model_dump(mode="json") == saved
            assert engines[name].context.state.activities[activity_id].data["effects"] == effects
        for name, effects in variants.items():
            engine, activity_id = engines[name], identities[name]
            for _ in range(8):
                if engine.context.state.activities[activity_id].status != ActivityStatus.ACTIVE:
                    break
                _advance_effect_rest(engine, activity_id, effects)
            activity = engine.context.state.activities[activity_id]
            assert activity.status == ActivityStatus.COMPLETED
            assert activity.elapsed_seconds == activity.planned_duration_seconds == 900
            assert activity.data["effects"] == effects
            outcomes[name] = engine.context.state.agents[1].body.model_copy(deep=True)
        assert outcomes["mild"].entertainment_need > outcomes["strong"].entertainment_need
        assert 0 < outcomes["mild"].mood_valence < outcomes["strong"].mood_valence
        for name, effects in variants.items():
            _export_effect_run(engines[name], tmp_path / name, identities[name], effects)
    finally:
        for engine in engines.values():
            engine.close()


def test_engine_factory_uses_artificial_inputs_and_isolates_scenario_changes(engine_factory):
    """@brief 工厂缓存和场景改写都绑定人工环境，场景实例之间不共享可变状态。"""
    path = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json"
    first, second = engine_factory(path), engine_factory(path)

    def change(document):
        """@brief 仅改变人物身体质量，保留实际输入路径与目录校验。"""
        document["body"]["mass_kg"] = 68.0

    changed = engine_factory(path, mutate=change)
    assert first.context.corpus_root == second.context.corpus_root == changed.context.corpus_root == (
        ARTIFICIAL_ENVIRONMENT_ROOT / "corpora")
    assert first.context.scenario.scenario_id == second.context.scenario.scenario_id == "single-person-test"
    assert first.context.scenario.body.mass_kg == second.context.scenario.body.mass_kg == 70.0
    assert changed.context.scenario.body.mass_kg == 68.0
    assert first.context.state.run_id != second.context.state.run_id
    assert first.context.rules.content_titles == {"test-content": "人工测试内容"}
