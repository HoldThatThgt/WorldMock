"""@brief 人工环境通过真实引擎与恢复入口验证第一集成波次的状态和身份边界。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.integration.test_external_environment_lifecycle import _identity
from tests.life.conftest import (activity_name, advance_next, conclude_request, pause_request, report_request,
                                 start_activity, submit_round, wake_by_mail)
from worldmock import run_setup, runtime
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.models import canonical_json, sha256_json
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention

ROOT = Path(__file__).resolve().parents[2]


def _new_engine(tmp_path):
    """@brief 真实新建隔离环境，身份指纹取当前生产规则而非手写摘要。"""
    root = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path = root / "scenarios/single-person.json"
    engine = LifeEngine.create(path, tmp_path / "parent", _identity(path), run_setup.stage_model_identity())
    return path, engine


def _checkpoint(engine, tmp_path):
    """@brief 用符合真实人物恢复合同的工作区生成共同检查点与内容寻址素材。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENT.json").write_bytes(canonical_json({
        "schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1,
        "profile": {"same_second_continuation": True}, "initialized_at": None,
    }))
    (workspace / "config.json").write_bytes(canonical_json({
        "enable_memory": True, "max_react_turns": run_setup.MAX_REACT_TURNS,
    }))
    return engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})


def _resume_request(path, engine, checkpoint, destination):
    """@brief 直接构造真实恢复入口的输入，父库始终保持独立。"""
    return runtime.ResumeRequest(ROOT, path, engine.context.store.run_dir, checkpoint.checkpoint_id, destination)


def _replace_checkpoint_state(store, checkpoint, state):
    """@brief 反例保持数据库记录摘要和事件身份有效，只改变待校验的世界结构。"""
    row = store.connection.execute("SELECT body FROM checkpoints WHERE checkpoint_id=?",
                                   (checkpoint.checkpoint_id,)).fetchone()
    payload = json.loads(row[0])
    payload["state"] = state
    with store.connection:
        store.connection.execute("DROP TRIGGER checkpoints_no_update")
        store.connection.execute("UPDATE checkpoints SET body=?,record_sha256=? WHERE checkpoint_id=?",
                                 (canonical_json(payload).decode(), sha256_json(payload), checkpoint.checkpoint_id))
    assert store.get_checkpoint(checkpoint.checkpoint_id).state == state


def test_real_create_checkpoint_and_resume_preserve_world_and_parent(tmp_path):
    """@brief 当前世界能实际新建、封存和恢复，子分支保留状态并独立写入。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        parent_before = engine.context.state.model_dump(mode="json")
        destination = tmp_path / "child"
        prepared = runtime._prepare_resume(_resume_request(path, engine, checkpoint, destination))
        child = LifeEngine.open(path, prepared.run_dir)
        try:
            assert child.context.state.schema_version == 10
            assert child.context.state.current_time == engine.context.state.current_time
            assert child.context.state.agents == engine.context.state.agents
            assert child.context.state.run_id != engine.context.state.run_id
            assert engine.context.state.model_dump(mode="json") == parent_before
        finally:
            child.close()
    finally:
        engine.close()


def test_real_open_rejects_world_structure_nine(tmp_path):
    """@brief 打开入口从真实 SQLite 世界读取旧结构，并由严格模型拒绝。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    state = engine.context.state.model_dump(mode="json")
    state["schema_version"] = 9
    with engine.context.store.connection:
        engine.context.store.connection.execute("UPDATE world_state SET body=? WHERE singleton=1",
                                                (canonical_json(state).decode(),))
    engine.close()
    with pytest.raises(ValidationError, match="schema_version"):
        LifeEngine.open(path, run_dir)


@pytest.mark.parametrize("replay", [False, True])
def test_real_restore_and_replay_preparation_reject_world_nine_before_publication(tmp_path, replay):
    """@brief 恢复和重放共用的真实入口在发布分支前拒绝记录校验已通过的旧世界。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        state = checkpoint.state.copy()
        state["schema_version"] = 9
        _replace_checkpoint_state(engine.context.store, checkpoint, state)
        destination = tmp_path / "child"
        intervention = CounterfactualIntervention(
            kind="set_balance_fen", agent_id=1, balance_fen=0, hypothesis="核对余额影响",
            source_event_ids=(engine.context.store.read_events(checkpoint.last_event_seq)[0].event_id,),
        ) if replay else None
        with pytest.raises(ValidationError, match="schema_version"):
            runtime._prepare_resume(_resume_request(path, engine, checkpoint, destination), intervention)
        assert not destination.exists()
        assert not list(tmp_path.glob(".worldmock-resume-*"))
    finally:
        engine.close()


def test_real_replay_entry_rejects_world_nine_before_model_runtime(tmp_path):
    """@brief 独立进程通过公开重放入口拒绝旧世界，锁定依赖和进程配置完整执行。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        state = checkpoint.state.copy()
        state["schema_version"] = 9
        _replace_checkpoint_state(engine.context.store, checkpoint, state)
        program = """
import asyncio, sys
from pathlib import Path
from pydantic import ValidationError
from worldmock import run_setup
run_setup._read_deepseek_key = lambda: 'offline-test-key'
run_setup._configure_process(Path(sys.argv[1]))
from worldmock import runtime
from worldmock.life.models import CounterfactualIntervention
request = runtime.CounterfactualRequest(
    runtime.ResumeRequest(*(Path(value) if index != 3 else value
                            for index, value in enumerate(sys.argv[1:]))),
    CounterfactualIntervention(kind='set_balance_fen', agent_id=1, balance_fen=0,
                              hypothesis='Check balance effects', source_event_ids=('test-source',)))
try:
    asyncio.run(runtime.replay_world(request))
except ValidationError as error:
    assert 'schema_version' in str(error)
    print('World 9 rejected before publication')
else:
    raise AssertionError('World 9 was accepted')
"""
        destination = tmp_path / "replay"
        result = subprocess.run(
            [sys.executable, "-c", program, str(ROOT), str(path), str(engine.context.store.run_dir),
             checkpoint.checkpoint_id, str(destination)], cwd=ROOT, env=os.environ.copy(),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "World 9 rejected before publication" in result.stdout
        assert not destination.exists()
    finally:
        engine.close()


def test_real_resume_rejects_same_version_changed_catalog_before_publication(tmp_path):
    """@brief 目录按原环境版本重新发布后仍因实际字节身份变化拒绝恢复。"""
    path, engine = _new_engine(tmp_path)
    try:
        checkpoint = _checkpoint(engine, tmp_path)
        catalog = path.parent.parent / "catalog"
        foods = catalog / "foods.json"
        foods.write_bytes(foods.read_bytes() + b" ")
        refresh_manifest_identity(catalog)
        destination = tmp_path / "child"
        with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
            runtime._prepare_resume(_resume_request(path, engine, checkpoint, destination))
        assert not destination.exists()
    finally:
        engine.close()


def test_real_open_rejects_held_unregistered_food(tmp_path):
    """@brief 真实恢复状态持有目录外食品时在构造期拒绝，不进入人物决策。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    state = engine.context.state.model_dump(mode="json")
    state["agents"]["1"]["inventory"]["home"]["unknown-food"] = 1.0
    with engine.context.store.connection:
        engine.context.store.connection.execute("UPDATE world_state SET body=? WHERE singleton=1",
                                                (canonical_json(state).decode(),))
    engine.close()
    with pytest.raises(IntegrityError, match="unregistered food"):
        LifeEngine.open(path, run_dir)


def _progress_until(engine, target):
    """@brief 只经实际请求和排程边界推进，等待目标时刻不会改写世界时钟。"""
    from worldmock.life.activity_control import pending_conclusions
    from worldmock.life.models import active_activities

    for step in range(2000):
        if engine.context.state.current_time >= target:
            return
        ready = engine.scheduling_state().ready_agent_ids
        if not ready:
            advance_next(engine)
            continue
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
        _, results = submit_round(engine, requests, f"wave-{target}-{step}")
        assert all(result.status in {"completed", "in_progress"} for result in results)
    raise AssertionError("Engine did not reach the requested event boundary")


def _long_video_round(engine, app_id, prefix):
    """@brief 独立冻结抽取公式选择含真实共享长视频的请求身份，不替换抽取器。"""
    from tests.life.test_app_progress import _reference_content

    catalog = engine.context.semantics.catalog
    return next(f"{prefix}-{index}" for index in range(200)
                if _reference_content(catalog, app_id, engine.context.state.random_state,
                                      f"action-{prefix}-{index}-1:{app_id}") == "c-long")


def test_real_app_interleaving_uses_shared_content_cursor_and_reopens_it(tmp_path):
    """@brief 两个应用交错消费同一长视频，暂停与真实重开都从全局最新游标继续。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    try:
        label = _long_video_round(engine, "douyin", "first-app")
        _, results = submit_round(engine, {1: ("start_activity", start_activity("打开", "抖音"))}, label)
        first = results[0].activity_id
        assert results[0].status == "in_progress" and engine.context.state.activities[first].data["content_id"] == "c-long"
        target = wake_by_mail(engine, offset=120)
        _progress_until(engine, target)
        _, results = submit_round(engine, {1: ("pause_activity", pause_request(activity_name(engine, first)))}, "pause-first")
        assert results[0].status == "completed"
        label = _long_video_round(engine, "bilibili", "second-app")
        _, results = submit_round(engine, {1: ("start_activity", start_activity("打开", "哔哩哔哩"))}, label)
        second = results[0].activity_id
        assert engine.context.state.activities[second].media_position_seconds == 120
        target = wake_by_mail(engine, offset=60)
        _progress_until(engine, target)
        submit_round(engine, {1: ("pause_activity", pause_request(activity_name(engine, second)))}, "pause-second")
        _, results = submit_round(engine, {1: ("report_status", report_request(activity_name(engine, first)))}, "resume-first")
        assert results[0].status == "completed"
        assert engine.context.state.activities[first].media_position_seconds == 180
        checkpoint = _checkpoint(engine, tmp_path)
        engine.close()
        engine = LifeEngine.open(path, run_dir)
        assert engine.context.state.model_dump(mode="json") == checkpoint.state
        assert engine.context.state.activities[first].media_position_seconds == 180
        assert engine.context.state.agents[1].content_progress["c-long"].position_seconds == 180
    finally:
        engine.close()


def test_real_app_push_is_persisted_without_reissue_after_open(tmp_path):
    """@brief 真实时钟触发会话推送，保存重开后事件与未消费推送均不重复。"""
    path, engine = _new_engine(tmp_path)
    run_dir = engine.context.store.run_dir
    try:
        target = min(event.due_at for event in engine.context.state.pending_events if event.kind == "app_push")
        _progress_until(engine, target)
        pushed = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                  if event.data.get("kind") == "app_push" and "push_id" in event.data]
        assert pushed and len({event.data["push_id"] for event in pushed}) == len(pushed)
        checkpoint = _checkpoint(engine, tmp_path)
        engine.close()
        engine = LifeEngine.open(path, run_dir)
        assert engine.context.state.model_dump(mode="json") == checkpoint.state
        _progress_until(engine, target + 1)
        restored = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                    if event.data.get("kind") == "app_push" and "push_id" in event.data]
        assert [event.event_id for event in restored] == [event.event_id for event in pushed]
    finally:
        engine.close()


def test_real_published_route_and_store_food_reach_consumption_evidence(tmp_path):
    """@brief 人物沿发布图到店购买并进食，真实活动保存路径和食品消费事实。"""
    from tests.life.test_work_protocol import trip_to

    path, engine = _new_engine(tmp_path)
    try:
        trip_to(engine, "noodle-2", "visit-store")
        trip = next(item for item in engine.context.state.activities.values() if item.kind == "travel")
        catalog = engine.context.semantics.catalog
        assert trip.data["path"] == list(catalog.graph.path("home", "noodle-2"))
        assert trip.data["distance_meters"] == catalog.graph.distance_meters("home", "noodle-2")
        _, results = submit_round(engine, {1: ("start_activity", start_activity("到店", "二味小炒出售的牛肉面"))}, "buy-food")
        assert results[0].status == "completed", results[0].error_code
        assert engine.context.state.agents[1].inventory["noodle-2"]["beef-noodles"] == 1
        _, results = submit_round(engine, {1: ("start_activity", start_activity("食用", "持有物：牛肉面"))}, "eat-food")
        assert results[0].status == "in_progress", results[0].error_code
        meal = results[0].activity_id
        end = engine.context.state.activities[meal].planned_end_at
        assert engine.context.state.activities[meal].planned_duration_seconds == 1200
        _progress_until(engine, end)
        assert engine.context.state.activities[meal].data["consumed"] == {"beef-noodles": 1}
        progress = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                    if event.event_type == "activity_progress" and event.activity_id == meal]
        portions = [row for event in progress for row in event.data["consumption"]["items"]]
        assert {row["food_id"] for row in portions} == {"beef-noodles"}
        assert sum(row["interval_portion"] for row in portions) == pytest.approx(1)
        assert sum(row["interval_kcal"] for row in portions) == pytest.approx(600)
    finally:
        engine.close()
