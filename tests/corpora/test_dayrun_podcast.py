"""@brief NASA 官方播客素材身份与完整段落揭示回归。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.corpora.manifest import load_manifest
from worldmock.errors import IntegrityError
from worldmock.life.content import load_subtitles, reveal_watch
from worldmock.life.models import ActivityState, AgentState, BodyState
from worldmock.life.scenario import ContentItemConfig, load_scenario

ROOT = Path("corpora")
ITEM_ID = "podcast-nasa-space-habitat-excerpt"


def _item() -> ContentItemConfig:
    """@brief 从正式语料条目取得真实播客配置。"""
    scenario, _ = load_scenario("scenarios/white-collar-12-weeks.json")
    return next(item for item in scenario.contents if item.item_id == ITEM_ID)


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


def test_official_audio_and_anchor_projection_are_frozen() -> None:
    """@brief 来源页、实际音轨、原始锚点与归零时间轴沿同一清单闭合。"""
    manifest = load_manifest(ROOT)
    indexed = {entry.path: entry for entry in manifest.entries}
    identity = json.loads((ROOT / "podcasts/nasa-space-habitat.identity.json").read_text())
    item = _item()
    cues = load_subtitles(item, ROOT)
    assert item.kind == "podcast_episode" and item.duration_seconds == 786
    assert identity["original_audio_url"].endswith("/ep24_space_habitat.mp3")
    assert identity["source_page_url"].startswith("https://www.nasa.gov/podcasts/")
    assert identity["source_start_seconds"] == 112 and identity["source_end_seconds"] == 898
    assert len(identity["source_anchors"]) == 70
    assert identity["segment_count"] == len(cues) == 62
    assert cues[0].start_seconds == 0 and cues[-1].end_seconds == 786
    for relative, digest in ((item.timeline_path, item.timeline_sha256),
                             (item.audio_path, item.audio_sha256)):
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest
        assert indexed[relative].sha256 == digest
    result = subprocess.run([sys.executable, "scripts/build_nasa_podcast.py", "--check"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_podcast_paragraph_is_revealed_only_after_its_official_end_anchor() -> None:
    """@brief 未听完整段落不揭示文字，跨过锚点才揭示一次。"""
    item = _item()
    cues = load_subtitles(item, ROOT)
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
    item = _item()
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_subtitles(item, tmp_path)
    folder = tmp_path / "podcasts"
    folder.mkdir()
    (folder / "nasa-space-habitat.excerpt.mp3").write_bytes(b"fake")
    with pytest.raises(IntegrityError, match="audio hash mismatch"):
        load_subtitles(item, tmp_path)


def test_podcast_audio_identity_is_required_and_exclusive() -> None:
    """@brief 官方播客须同时钉住本地音轨与哈希，其他内容不得借用音轨身份。"""
    official = _item().model_dump(mode="json")
    assert ContentItemConfig.model_validate(official).item_id == ITEM_ID
    for missing in ("audio_path", "audio_sha256"):
        invalid = {**official, missing: None}
        with pytest.raises(ValidationError, match="podcast episode requires audio path and SHA-256"):
            ContentItemConfig.model_validate(invalid)

    non_podcast = {**official, "kind": "feature_film"}
    with pytest.raises(ValidationError, match="non-podcast content cannot declare podcast audio"):
        ContentItemConfig.model_validate(non_podcast)
    non_podcast.update(audio_path=None, audio_sha256=None)
    assert ContentItemConfig.model_validate(non_podcast).audio_path is None
