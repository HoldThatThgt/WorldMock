"""三个社媒流（微博/知乎/影评）预排新鲜感语料的完整性与场景准入测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from worldmock.corpora import load_manifest
from worldmock.life.content import load_subtitles
from worldmock.life.scenario import ContentItemConfig, load_scenario

SCENARIOS = ("scenarios/white-collar-12-weeks.json", "scenarios/multi-person.json")
START = date(2026, 1, 5)
DAYS = 84

PLATFORMS = {
    "weibo-feed": {
        "dir": "weibo",
        "platform": "weibo",
        "count": 3000,
        "read_window": (30, 60),
        "srt": "social/weibo/weibo.srt",
        "experience_label": "casual_weibo_scroll",
    },
    "zhihu-feed": {
        "dir": "zhihu",
        "platform": "zhihu",
        "count": 1000,
        "read_window": (60, 120),
        "srt": "social/zhihu/zhihu.srt",
        "experience_label": "casual_zhihu_read",
    },
    "movie-reviews-feed": {
        "dir": "movie_reviews",
        "platform": "douban_reviews",
        "count": 1000,
        "read_window": (30, 60),
        "srt": "social/movie_reviews/movie-reviews.srt",
        "experience_label": "casual_movie_review_scroll",
    },
}
ITEM_FIELDS = {"item_id", "platform", "author_hint", "body", "estimated_read_seconds"}


def _day_docs(spec: dict) -> list[dict]:
    root = Path("corpora/social") / spec["dir"]
    day_files = sorted(root.glob("day-*.json"))
    assert len(day_files) == DAYS
    return [json.loads(p.read_text(encoding="utf-8")) for p in day_files]


def _scenario_item(scenario, item_id: str) -> ContentItemConfig:
    return next(item for item in scenario.contents if item.item_id == item_id)


def test_social_day_batches_cover_84_days_with_fresh_daily_unlock() -> None:
    """每流 84 个日批文件：日期逐日对齐场景、字段齐全、正文去重、批内读时达标。"""
    all_bodies: set[str] = set()
    for spec in PLATFORMS.values():
        docs = _day_docs(spec)
        assert [doc["day"] for doc in docs] == list(range(1, DAYS + 1))
        assert [doc["date"] for doc in docs] == [
            (START + timedelta(days=d - 1)).isoformat() for d in range(1, DAYS + 1)
        ]
        items = [item for doc in docs for item in doc["items"]]
        assert len(items) == spec["count"]
        low, high = spec["read_window"]
        for item in items:
            assert set(item) == ITEM_FIELDS
            assert item["platform"] == spec["platform"]
            assert item["author_hint"].strip()
            assert item["body"].strip()
            assert low <= item["estimated_read_seconds"] <= high
            assert item["estimated_read_seconds"] == round(len(item["body"]) / 5)
        ids = [item["item_id"] for item in items]
        assert len(set(ids)) == len(ids)
        bodies = {item["body"] for item in items}
        assert len(bodies) == len(items)
        assert not (bodies & all_bodies), "cross-platform duplicate bodies"
        all_bodies |= bodies


def test_social_day_segments_are_prefix_sums_of_read_seconds() -> None:
    """每天的 segment_start_seconds 等于之前各天 estimated_read_seconds 之和。"""
    for spec in PLATFORMS.values():
        offset = 0
        for doc in _day_docs(spec):
            assert doc["segment_start_seconds"] == offset
            offset += sum(item["estimated_read_seconds"] for item in doc["items"])
            assert doc["platform"] == spec["platform"]


def test_social_timelines_are_day_batches_in_order() -> None:
    """拼接 SRT 与日批逐条一致：cue 文本=正文、边界=日批段起点、总时长吻合。"""
    scenario = load_scenario(SCENARIOS[0])[0]
    for item_id, spec in PLATFORMS.items():
        item = _scenario_item(scenario, item_id)
        assert item.timeline_path == spec["srt"]
        cues = load_subtitles(item, Path("corpora"))
        docs = _day_docs(spec)
        flat = [entry for doc in docs for entry in doc["items"]]
        assert len(cues) == len(flat)
        assert cues[-1].end_seconds == item.duration_seconds
        assert item.duration_seconds == sum(entry["estimated_read_seconds"] for entry in flat)
        for cue, entry in zip(cues, flat):
            assert cue.text == entry["body"]
        cursor = 0
        for doc in docs:
            assert cues[cursor].start_seconds == doc["segment_start_seconds"]
            cursor += len(doc["items"])


def test_both_scenarios_admit_social_feeds_with_full_preferences() -> None:
    """两场景准入三个社媒流条目；随机偏好浮点已随修订合同 L1661 删除。"""
    for path in SCENARIOS:
        scenario = load_scenario(path)[0]
        for item_id, spec in PLATFORMS.items():
            item = _scenario_item(scenario, item_id)
            assert item.experience_label == spec["experience_label"]
            assert 0.2 <= item.entertainment_relief_per_hour <= 0.3
        # 修订合同 L1661：人物不再携带逐流偏好浮点（0.4-0.8 断言随合同移除）。
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        for agent in doc["agents"]:
            assert "entertainment_preferences" not in agent, (
                f"{path} agent {agent['agent_id']} still carries random preference floats")


def test_manifest_registers_every_social_file_with_exact_identity() -> None:
    """manifest 逐文件登记 social/ 全部文件，哈希与字节数与磁盘一致。"""
    manifest = load_manifest(Path("corpora"))
    index = {entry.path: entry for entry in manifest.entries}
    disk = sorted(
        p.relative_to(Path("corpora")).as_posix()
        for p in Path("corpora/social").rglob("*")
        if p.is_file()
    )
    assert disk and all(path in index for path in disk)
    for path in disk:
        blob = (Path("corpora") / path).read_bytes()
        assert index[path].size_bytes == len(blob)
        assert index[path].sha256 == hashlib.sha256(blob).hexdigest()
    social_sources = {index[path].source.license for path in disk if not path.endswith("README.md")}
    assert social_sources == {"unspecified (grey: research use only)"}
