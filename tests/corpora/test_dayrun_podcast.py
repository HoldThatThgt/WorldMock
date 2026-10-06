"""@brief 人工音轨和时间轴验证播客身份及完整段落揭示合同。"""

from __future__ import annotations

import hashlib
import io
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.errors import IntegrityError
from worldmock.life.content import load_subtitles, reveal_watch
from worldmock.life.models import ActivityState, AgentState, BodyState
from worldmock.life.scenario import ContentItemConfig


def _item(root: Path) -> ContentItemConfig:
    """@brief 用标准 WAV 文件和两段人工字幕构造真实可校验音轨。"""
    audio = io.BytesIO()
    with wave.open(audio, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\0\0" * 160000)
    timeline = b"1\n00:00:00,000 --> 00:00:10,000\nFirst paragraph\n\n2\n00:00:10,000 --> 00:00:20,000\nSecond paragraph\n"
    (root / "audio.wav").write_bytes(audio.getvalue())
    (root / "timeline.srt").write_bytes(timeline)
    return ContentItemConfig.model_validate({
        "item_id": "test-podcast", "title": "Artificial podcast", "kind": "podcast_episode",
        "duration_seconds": 20, "timeline_path": "timeline.srt",
        "timeline_sha256": hashlib.sha256(timeline).hexdigest(),
        "audio_path": "audio.wav", "audio_sha256": hashlib.sha256(audio.getvalue()).hexdigest(),
        "entertainment_relief_per_hour": 0.3, "experience_label": "Artificial audio"})


def _agent() -> AgentState:
    """@brief 创建无先前接触的最小人物状态。"""
    body = BodyState(energy_kcal=2000, entertainment_need=0)
    return AgentState(agent_id=1, name="listener", location_id="home",
                      balance_account_id="agent:1", body=body,
                      body_settled_at=0, next_decision_at=0)


def _activity(item: ContentItemConfig) -> ActivityState:
    """@brief 创建具有真实媒体时钟的播客播放活动。"""
    return ActivityState(activity_id="listen", agent_id=1, location_id="home", kind="watch", status="active",
                         started_at=0, planned_end_at=item.duration_seconds,
                         last_settled_at=0, elapsed_seconds=0,
                         planned_duration_seconds=item.duration_seconds,
                         target_id=item.item_id, origin_action_id="action",
                         origin_decision_id="decision", origin_request_event_id="request",
                         data={"start_position_seconds": 0, "exposure_count": 1})


def test_podcast_paragraph_is_revealed_only_after_its_declared_end_anchor(tmp_path: Path) -> None:
    """@brief 未听完整段落不揭示文字，跨过锚点才揭示一次。"""
    item = _item(tmp_path)
    cues = load_subtitles(item, tmp_path)
    first = cues[0]
    activity = _activity(item)
    agent = _agent()
    activity.media_position_seconds = first.end_seconds - 0.1
    agent, early = reveal_watch(activity, agent, item, cues)
    assert early["cues"] == []
    activity.media_position_seconds = first.end_seconds
    agent, completed = reveal_watch(activity, agent, item, cues)
    assert [row["cue_id"] for row in completed["cues"]] == [first.cue_id]
    agent, repeated = reveal_watch(activity, agent, item, cues)
    assert repeated["cues"] == []
    activity.media_position_seconds = cues[1].end_seconds - 0.1
    agent, paused = reveal_watch(activity, agent, item, cues)
    assert paused["cues"] == []
    activity.media_position_seconds = cues[1].end_seconds
    _, resumed = reveal_watch(activity, agent, item, cues)
    assert [row["cue_id"] for row in resumed["cues"]] == [cues[1].cue_id]


def test_podcast_requires_real_audio_and_rejects_corrupt_audio(tmp_path: Path) -> None:
    """@brief 缺失或篡改真实音轨不能降级为只有转写文字的伪播客。"""
    item = _item(tmp_path)
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_subtitles(item, tmp_path / "missing")
    (tmp_path / item.audio_path).write_bytes(b"corrupt audio")
    with pytest.raises(IntegrityError, match="audio hash mismatch"):
        load_subtitles(item, tmp_path)


def test_podcast_audio_identity_is_required_and_exclusive(tmp_path: Path) -> None:
    """@brief 环境声明的播客须同时钉住本地音轨与哈希，其他内容不得借用音轨身份。"""
    declared = _item(tmp_path).model_dump(mode="json")
    assert ContentItemConfig.model_validate(declared).item_id == "test-podcast"
    for missing in ("audio_path", "audio_sha256"):
        invalid = {**declared, missing: None}
        with pytest.raises(ValidationError, match="podcast episode requires audio path and SHA-256"):
            ContentItemConfig.model_validate(invalid)

    non_podcast = {**declared, "kind": "feature_film"}
    with pytest.raises(ValidationError, match="non-podcast content cannot declare podcast audio"):
        ContentItemConfig.model_validate(non_podcast)
    non_podcast.update(audio_path=None, audio_sha256=None)
    assert ContentItemConfig.model_validate(non_podcast).audio_path is None
