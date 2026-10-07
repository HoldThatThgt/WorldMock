"""@brief 人工环境经真实加载、动作、关闭恢复和导出验证，不调用模型。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.life.conftest import advance_next, conclude_request, report_request, start_activity, submit_round
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityStatus, RunIdentity
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.models import active_activities


def _identity(path: Path) -> RunIdentity:
    """@brief 冻结离线引擎直驱运行的输入身份，不创建任何模型客户端。"""
    from worldmock.run_setup import _model_identity, runtime_code_sha256

    document = json.loads(path.read_text(encoding="utf-8"))
    return RunIdentity(
        run_id="external-environment-test", source_group_id="external-environment-group",
        agent_ids=tuple(item["agent_id"] for item in document["agents"]),
        start_at=datetime.fromisoformat(document["start_at"]).isoformat(),
        end_at=datetime.fromisoformat(document["end_at"]).isoformat(), timezone=document["timezone"],
        code_sha256=runtime_code_sha256(Path(__file__).resolve().parents[2]),
        model_identity=_model_identity(),
    )


def _complete_run(engine: LifeEngine, root: Path):
    """@brief 用真实请求走过剩余边界，再冻结真实人工人物目录和生活参与者检查点。"""
    for step in range(2000):
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
    return _snapshot_current(engine, root)


def _snapshot_current(engine: LifeEngine, root: Path):
    """@brief 保存当前真实世界和人物素材，阶段五不要求运行至完整日末尾。"""
    snapshots = {}
    for agent_id, agent in engine.context.state.agents.items():
        workspace = root / f"agent-{agent_id}"
        workspace.mkdir()
        (workspace / "state.json").write_text(agent.model_dump_json(), encoding="utf-8")
        snapshots[f"agent:{agent_id}"] = snapshot_directory(workspace, engine.context.store.assets)
    return engine.checkpoint(snapshots)


def _advance_after_restore(engine: LifeEngine) -> None:
    """@brief 经真实收尾、等待和播报消化恢复时就绪决策，再继续一个实际时间边界。"""
    for step in range(8):
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            advance_next(engine)
            return
        requests = {}
        for agent_id in ready:
            pending = pending_conclusions(engine.context.state, agent_id)
            active = active_activities(engine.context.state, agent_id)
            if pending:
                requests[agent_id] = ("conclude_activity", conclude_request(*(item.name for item in pending)))
            elif active:
                requests[agent_id] = ("report_status", report_request(*(item.name for item in active)))
            else:
                requests[agent_id] = ("start_activity", start_activity("等待", "自己"))
        _, results = submit_round(engine, requests, f"resume-{step}")
        assert all(result.status in {"completed", "in_progress"} for result in results)
    raise AssertionError("Restored engine did not leave its decision boundary")


@pytest.mark.parametrize("scenario_name", ["single-person.json", "multi-person.json", "multi-person-half-day.json"])
def test_external_environment_survives_actions_and_reopen(tmp_path: Path, scenario_name: str):
    """@brief 显式外部场景路径足以完成真实动作生命周期，动作和内容取人工环境。"""
    root = tmp_path / "external-environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path, run = root / "scenarios" / scenario_name, tmp_path / "run"
    engine = LifeEngine.create(path, run, _identity(path))
    try:
        catalog = engine.context.semantics.catalog
        assert catalog.actions["quiet-rest"].name == "安静休息"
        assert catalog.contents["c-rest"].title == "测试休息"
        requests = {agent: ("start_activity", start_activity("进行", "安静休息"))
                    for agent in engine.scheduling_state().ready_agent_ids}
        _, results = submit_round(engine, requests, "start-rest")
        assert all(result.status == "in_progress" for result in results)
        advance_next(engine)
        saved = engine.context.state.model_dump(mode="json")
        engine.close()
        engine = LifeEngine.open(path, run)
        assert engine.context.state.model_dump(mode="json") == saved
        assert all(activity.data["action_id"] == "quiet-rest" and activity.data["content_id"] == "c-rest"
                   for activity in engine.context.state.activities.values())
        _advance_after_restore(engine)
        checkpoint = _snapshot_current(engine, tmp_path)
        assert engine.context.store.get_checkpoint(checkpoint.checkpoint_id).state == checkpoint.state
    finally:
        engine.close()


def _environment_with_content(root: Path, title: str, duration_seconds: int) -> Path:
    """@brief 复制人工环境，只改动作绑定内容并刷新目录身份。"""
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    contents_path = root / "catalog/contents.json"
    document = json.loads(contents_path.read_text(encoding="utf-8"))
    content = next(item for item in document["contents"] if item["content_id"] == "c-rest")
    content.update(title=title, summary=f"{title}的测试用合成摘要。", duration_seconds=duration_seconds)
    contents_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    refresh_manifest_identity(root / "catalog")
    path = root / "scenarios/single-person.json"
    scenario = json.loads(path.read_text(encoding="utf-8"))
    scenario["activity_feedback_seconds"] = 300
    path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    return path


def _start_content_rest(engine: LifeEngine, title: str, duration_seconds: int) -> str:
    """@brief 经真实请求启动动作，并验证实际启动路径冻结环境内容。"""
    content = engine.context.semantics.catalog.contents["c-rest"]
    assert content.title == title and content.duration_seconds == duration_seconds
    _, results = submit_round(engine, {1: ("start_activity", start_activity("进行", "安静休息"))}, "start-rest")
    result, = results
    assert result.status == "in_progress"
    activity = engine.context.state.activities[result.activity_id]
    assert activity.kind == "action"
    assert activity.data["action_id"] == "quiet-rest"
    assert activity.data["content_id"] == "c-rest"
    assert activity.planned_duration_seconds == duration_seconds
    assert result.data["activity"]["data"]["content_id"] == "c-rest"
    return activity.activity_id


def _advance_content_rest(engine: LifeEngine, activity_id: str) -> None:
    """@brief 经真实播报和推进更新内容位置及活动快照。"""
    activity = engine.context.state.activities[activity_id]
    assert activity.status == ActivityStatus.ACTIVE
    if engine.scheduling_state().ready_agent_ids:
        _, results = submit_round(engine, {1: ("report_status", report_request(activity.name))},
                                  f"report-rest-{activity.elapsed_seconds}")
        assert all(result.status == "completed" for result in results)
    before = activity.media_position_seconds
    advance_next(engine)
    current = engine.context.state.activities[activity_id]
    assert current.media_position_seconds > before
    assert current.data["action_id"] == "quiet-rest" and current.data["content_id"] == "c-rest"


def _export_content_run(engine: LifeEngine, root: Path, activity_id: str, duration_seconds: int) -> None:
    """@brief 完成真实运行并验证导出中的同一动作仍保存内容身份和时长。"""
    from worldmock.footprints.export import export_run
    from worldmock.footprints.validation import validate_export

    checkpoint = _complete_run(engine, root)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == ActivityStatus.COMPLETED
    assert activity.data["content_id"] == "c-rest"
    assert activity.planned_duration_seconds == duration_seconds
    destination = root / "export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, destination)
    checked, events, activities = validate_export(destination)
    assert checked["run_id"] == manifest["run_id"]
    exported = next(item for item in activities if item["activity_id"] == activity_id)
    assert exported["status"] == "completed"
    assert exported["data"]["action_id"] == "quiet-rest"
    assert exported["data"]["content_id"] == "c-rest"
    progress = [event for event in events
                if event["event_type"] == "activity_progress" and event["activity_id"] == activity_id]
    assert progress and progress[-1]["data"]["content"]["completed"] is True


def test_same_action_content_identity_freezes_each_environment_through_real_lifecycle(tmp_path: Path):
    """@brief 两个同时加载的外部世界经真实启动、继续和恢复各自使用动作内容。"""
    variants = {"short": ("短暂休息", 600), "long": ("长时间休息", 1200)}
    paths = {name: _environment_with_content(tmp_path / name, *content) for name, content in variants.items()}
    engines, identities = {}, {}
    try:
        for name, path in paths.items():
            engines[name] = LifeEngine.create(path, tmp_path / name / "run", _identity(path))
        for name, (title, duration) in variants.items():
            identities[name] = _start_content_rest(engines[name], title, duration)
        for name, (_, duration) in variants.items():
            engine, activity_id = engines[name], identities[name]
            _advance_content_rest(engine, activity_id)
            activity = engine.context.state.activities[activity_id]
            assert 0 < activity.elapsed_seconds <= activity.planned_duration_seconds == duration
            saved = engine.context.state.model_dump(mode="json")
            engine.close()
            engines[name] = LifeEngine.open(paths[name], tmp_path / name / "run")
            assert engines[name].context.state.model_dump(mode="json") == saved
            assert engines[name].context.state.activities[activity_id].data["content_id"] == "c-rest"
        for name, (_, duration) in variants.items():
            engine, activity_id = engines[name], identities[name]
            for _ in range(8):
                if engine.context.state.activities[activity_id].status != ActivityStatus.ACTIVE:
                    break
                _advance_content_rest(engine, activity_id)
            activity = engine.context.state.activities[activity_id]
            assert activity.status == ActivityStatus.COMPLETED
            assert activity.elapsed_seconds == activity.planned_duration_seconds == duration
            progress = engine.context.state.agents[1].content_progress["c-rest"]
            assert progress.position_seconds == 0 and progress.completed_rounds == 1
        for name in variants:
            checkpoint = _snapshot_current(engines[name], tmp_path / name)
            assert engines[name].context.store.get_checkpoint(checkpoint.checkpoint_id).state == checkpoint.state
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
    assert first.context.scenario.scenario_id == second.context.scenario.scenario_id == "single-person-test"
    assert first.context.scenario.body.mass_kg == second.context.scenario.body.mass_kg == 70.0
    assert changed.context.scenario.body.mass_kg == 68.0
    assert first.context.state.run_id != second.context.state.run_id
    assert first.context.semantics.catalog.contents["c-short"].title == "测试短视频"
    assert second.context.semantics.catalog.actions["browse-feed"].name == "刷推荐"


@pytest.mark.parametrize("scenario_name", ["single-person.json", "multi-person.json", "multi-person-half-day.json"])
def test_external_environment_exports_authenticated_completed_run(tmp_path: Path, scenario_name: str):
    """@brief 导出阶段以实际完成的运行验证工件及原始动作事实。"""
    from worldmock.footprints.export import export_run
    from worldmock.footprints.validation import validate_export

    path = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios" / scenario_name
    engine = LifeEngine.create(path, tmp_path / "run", _identity(path))
    try:
        checkpoint = _complete_run(engine, tmp_path)
        manifest = export_run(engine.context.store, checkpoint.last_event_seq, tmp_path / "export")
        checked, events, _ = validate_export(tmp_path / "export")
        assert checked["run_id"] == manifest["run_id"]
        assert any(event["event_type"] == "action_result" for event in events)
    finally:
        engine.close()


@pytest.mark.parametrize(("title", "duration"), [("短暂休息", 600), ("长时间休息", 1200)])
def test_external_environment_exports_frozen_action_content(tmp_path: Path, title: str, duration: int):
    """@brief 导出阶段以真实启动的动作证明环境内容随足迹保存。"""
    path = _environment_with_content(tmp_path / "environment", title, duration)
    engine = LifeEngine.create(path, tmp_path / "run", _identity(path))
    try:
        activity_id = _start_content_rest(engine, title, duration)
        _export_content_run(engine, tmp_path, activity_id, duration)
    finally:
        engine.close()
