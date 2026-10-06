"""@brief 合法短内容在真实外部世界可观察和按名查询，启动仍受一分钟下限约束。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from custom.envs.life_env import LifeEnv
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.integration.test_external_environment_lifecycle import _identity
from tests.life.conftest import context, start_activity
from worldmock.life.engine import LifeEngine
from worldmock.life.scenario import sha256_file
from worldmock.life.world_query import QueryWorldRequest

_REASON = "The derived activity duration is outside the allowed range."


def _short_environment(root: Path) -> Path:
    """@brief 从固定人工输入发布三个三十秒条目，以真实轮转留下窗口内外两种绑定。"""
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    subtitle = root / "corpora/content/test.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:30,000\nArtificial short content.\n", encoding="utf-8")
    manifest_path = root / "corpora/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(entry for entry in manifest["entries"] if entry["path"] == "content/test.srt")
    entry.update(sha256=sha256_file(subtitle), size_bytes=subtitle.stat().st_size)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    path = root / "scenarios/single-person.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    original, = document["contents"]
    document["contents"] = [{**original, "item_id": f"short-{suffix}", "title": f"人工短内容{suffix}",
                             "duration_seconds": 30, "timeline_sha256": sha256_file(subtitle)}
                            for suffix in ("甲", "乙", "丙")]
    document.update(corpus_manifest_sha256=sha256_file(manifest_path),
                    candidate_presentation_limit=2, rotation_quota_ratio=.5)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


async def _assert_short_query(environment: LifeEnv, decision, name: str) -> None:
    """@brief 真实只读查询保留条目与拒绝原因，过滤可执行对象时返回空集。"""
    payload = {"name": name, "way": None, "category": None, "from": None, "available_only": False, "offset": 0}
    request = QueryWorldRequest.model_validate(payload)
    result = await environment.read_query(decision, "query_world", request)
    assert result["total_count"] == 1 and result["next_offset"] is None
    match, = result["matches"]
    assert match["name"] == name
    action, = match["actions"]
    assert action["available"] is False and action["reason"] == _REASON
    assert action["duration"] == "不到 1 分钟"
    executable = QueryWorldRequest.model_validate({**payload, "available_only": True})
    assert await environment.read_query(decision, "query_world", executable) == {
        "matches": [], "total_count": 0, "next_offset": None}


@pytest.mark.asyncio
async def test_short_external_content_remains_observable_and_unavailable_inside_and_outside_window(tmp_path: Path):
    """@brief 真实三十秒字幕与输入身份可加载；呈现、目录、查询同源，窗口内外的实际启动都拒绝。"""
    path = _short_environment(tmp_path / "external-environment")
    engine = LifeEngine.create(path, tmp_path / "run", _identity(path))
    environment = LifeEnv()
    environment.engine = engine
    try:
        assert all(item.duration_seconds == 30 for item in engine.context.scenario.contents)
        assert all(cues[-1].end_seconds == 30 for cues in engine.context.rules.subtitles.values())
        for source in ("listed", "deferred"):
            decision = context(engine, f"short-{source}", 1)
            await environment.begin_decision_round((decision,))
            observed = await environment.observe(decision)
            shown = {target["name"]: target for target in observed["tool_layer"]["targets"]
                     if target["kind"] == "content"}
            private = {row["object"]: row for row in observed["catalog_bindings"] if row["kind"] == "content"}
            assert len(shown) == 2 and len(private) == 1
            assert not set(shown) & set(private)
            for target in shown.values():
                action, = target["actions"]
                assert action["duration"] == "不到 1 分钟"
                assert action["available"] is False and action["reason"] == _REASON
            for binding in private.values():
                assert binding["note"] == "不到 1 分钟" and binding["reason"] == _REASON
            name = sorted(shown if source == "listed" else private)[0]
            await _assert_short_query(environment, decision, name)
            before = engine.context.state.model_dump(mode="json")
            request = start_activity("从头再看", name)
            await environment.submit_action(DirectToolCall(decision, f"start-{source}", "start_activity", request))
            result, = await environment.commit_decision_round(decision.round_id)
            assert result.status == "rejected" and result.error_code == "ACTIVITY_DURATION_INVALID"
            assert engine.context.state.current_time == before["current_time"]
            expected_body = {**before["agents"]["1"]["body"], "mood_valence": -.05}
            assert engine.context.state.agents[1].body.model_dump(mode="json") == expected_body
            assert not engine.context.state.activities
        assert not any(event.event_type == "run_failed"
                       for event in engine.context.store.read_events(engine.context.store.last_event_seq))
    finally:
        await environment.close()
