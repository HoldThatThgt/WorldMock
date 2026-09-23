"""内容分段结构与观看进度的当前呈现合同。"""

from __future__ import annotations

from worldmock.life.models import ContentProgress
from worldmock.life.observe import _content_catalog


def test_unstarted_content_has_segment_structure(engine_factory) -> None:
    """@brief 未观看的条目有时间轴分段信息，没有虚构进度。"""
    engine = engine_factory()
    rows = _content_catalog(engine.scenario, engine.rules.subtitles,
                            engine.rules.content_titles, {})
    by_id = {row["item_id"]: row for row in rows}
    assert by_id["novel-0001"]["segments_total"] == 169
    assert by_id["novel-0001"]["typical_segment_seconds"] == 502.0
    assert by_id["news-feed"]["segments_total"] == 1000
    assert by_id["movie-0001"]["segments_total"] == 1
    assert all("progress" not in row for row in rows)


def test_content_progress_uses_actual_timeline_cues(engine_factory) -> None:
    """@brief 分段完成数由真实字幕结束时刻计算。"""
    engine = engine_factory()
    item_id = "novel-0001"
    progress = {item_id: ContentProgress(item_id=item_id, position_seconds=900, exposure_count=1)}
    rows = _content_catalog(engine.scenario, engine.rules.subtitles,
                            engine.rules.content_titles, progress)
    by_id = {row["item_id"]: row for row in rows}
    expected = sum(cue.end_seconds <= 900 for cue in engine.rules.subtitles[item_id])
    row = by_id[item_id]
    assert row["progress"]["position_seconds"] == 900
    assert row["progress"]["segments_completed"] == expected
    assert row["progress"]["current_segment"] == expected + 1
    assert "progress" not in by_id["movie-0001"]
