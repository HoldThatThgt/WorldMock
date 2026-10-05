"""播客以真实媒体位置揭示冻结字幕。"""

from __future__ import annotations

from worldmock.life.content import reveal_watch
from worldmock.life.models import ActivityState


def test_podcast_reveal_waits_for_audio_anchor(engine_factory) -> None:
    """@brief 音轨尚未越过字幕结束点时不提前揭示。"""
    engine = engine_factory()
    item_id = "podcast-nasa-space-habitat-excerpt"
    item = engine.context.rules.content(item_id)
    cues = engine.context.rules.subtitles[item_id]
    activity = ActivityState.model_construct(
        media_position_seconds=2,
        data={"start_position_seconds": 0, "exposure_count": 1},
    )
    person, first = reveal_watch(activity, engine.context.state.agents[1], item, cues)
    assert first["cues"] == []

    activity.media_position_seconds = 3
    person, second = reveal_watch(activity, person, item, cues)
    assert [cue["cue_id"] for cue in second["cues"]] == [1]

    activity.media_position_seconds = item.duration_seconds
    person, final = reveal_watch(activity, person, item, cues)
    assert final["cues"][-1]["end_seconds"] == item.duration_seconds
    assert person.content_progress[item_id].position_seconds == item.duration_seconds
