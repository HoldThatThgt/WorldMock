#!/usr/bin/env python3
"""@brief 从冻结 NASA 官方页的原始段落锚点重建播客选段时间轴。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "corpora/podcasts"
SOURCE = ROOT / "nasa-space-habitat.source.html"
AUDIO = ROOT / "nasa-space-habitat.excerpt.mp3"
PAGE_URL = "https://www.nasa.gov/podcasts/houston-we-have-a-podcast/space-habitat/"
AUDIO_URL = "https://www.nasa.gov/wp-content/uploads/2017/12/ep24_space_habitat.mp3"
ORIGINAL_AUDIO_SHA256 = "55bf6bb73f4d5d58e50319f3a3db8c93cf396565e988621dd991d3fc434b6e7c"
SOURCE_SHA256 = "351db2301d36f625de314f3d917457b34d77eb7760082bf9a8f842ca865b3d0e"
AUDIO_SHA256 = "f298a5f15c489c2efbc1e730647665532bb1a8ff3737ea5453a5e08ca44fff25"
START = 112
END = 898
STAMP = re.compile(r"\[(\d\d):(\d\d):(\d\d)\]")


class Paragraphs(HTMLParser):
    """@brief 按原始 HTML 顺序提取 p 文本，保留官方锚点对应关系。"""

    def __init__(self) -> None:
        """@brief 初始化段落解析状态。"""
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []
        self.current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """@brief 开始收集一个段落。"""
        if tag == "p":
            self.depth += 1
            if self.depth == 1:
                self.current = []

    def handle_endtag(self, tag: str) -> None:
        """@brief 结束段落并固定已解码文字。"""
        if tag == "p" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                self.parts.append("".join(self.current).strip())

    def handle_data(self, data: str) -> None:
        """@brief 只采集段落内的原始文字。"""
        if self.depth:
            self.current.append(data)


def _seconds(stamp: str) -> int:
    """@brief 将官方时分秒标记转换为整数秒。"""
    match = STAMP.fullmatch(stamp)
    if match is None:
        raise ValueError("NASA transcript timestamp is invalid")
    hour, minute, second = (int(value) for value in match.groups())
    return hour * 3600 + minute * 60 + second


def _timecode(seconds: int) -> str:
    """@brief 生成既有 SRT 解析器接受的精确秒时标。"""
    hour, rest = divmod(seconds, 3600)
    minute, second = divmod(rest, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d},000"


def _segments(source: bytes) -> tuple[list[dict], list[dict]]:
    """@brief 合并同秒段落，以下一严格递增官方锚点作为片段终点。"""
    parser = Paragraphs()
    parser.feed(source.decode("utf-8"))
    anchors = [(index, _seconds(part)) for index, part in enumerate(parser.parts)
               if STAMP.fullmatch(part)]
    selected = [(index, second) for index, second in anchors if START <= second <= END]
    if not selected or selected[0][1] != START or selected[-1][1] != END:
        raise ValueError("NASA excerpt endpoints are absent")
    grouped: dict[int, list[str]] = {}
    for (index, second), (next_index, _) in zip(selected, selected[1:]):
        grouped.setdefault(second, []).extend(
            part for part in parser.parts[index + 1:next_index] if part)
    moments = sorted(grouped)
    if moments[-1] >= END or len(moments) < 2:
        raise ValueError("NASA excerpt timeline is invalid")
    segments = [{"source_start_seconds": moment, "source_end_seconds": next_second,
                 "text": "\n".join(grouped[moment])}
                for moment, next_second in zip(moments, moments[1:] + [END])]
    if any(not item["text"] for item in segments):
        raise ValueError("NASA transcript segment is empty")
    source_anchors = [{"paragraph_index": index, "source_seconds": second}
                      for index, second in selected]
    return segments, source_anchors


def _outputs() -> dict[str, bytes]:
    """@brief 生成身份、条目与时间轴的完整固定字节内容。"""
    source = SOURCE.read_bytes()
    audio = AUDIO.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError("NASA source page hash mismatch")
    if hashlib.sha256(audio).hexdigest() != AUDIO_SHA256:
        raise ValueError("NASA audio excerpt hash mismatch")
    if AUDIO_URL.encode() not in source:
        raise ValueError("NASA official audio URL is absent from the source page")
    segments, anchors = _segments(source)
    blocks = [f"{index}\n{_timecode(item['source_start_seconds'] - START)} --> "
              f"{_timecode(item['source_end_seconds'] - START)}\n{item['text']}"
              for index, item in enumerate(segments, 1)]
    timeline = ("\n\n".join(blocks) + "\n").encode("utf-8")
    identity = {
        "item_id": "podcast-nasa-space-habitat-excerpt",
        "series": "Houston We Have a Podcast", "episode": "Space Habitat",
        "season": 1, "episode_number": 24, "language": "en",
        "source_page_url": PAGE_URL, "source_page_sha256": SOURCE_SHA256,
        "original_audio_url": AUDIO_URL, "original_audio_sha256": ORIGINAL_AUDIO_SHA256,
        "excerpt_audio_path": "podcasts/nasa-space-habitat.excerpt.mp3",
        "excerpt_audio_sha256": AUDIO_SHA256,
        "source_start_seconds": START, "source_end_seconds": END,
        "duration_seconds": END - START, "source_anchors": anchors,
        "segment_count": len(segments), "transcript_granularity": "official paragraph anchors",
        "audio_transform": "ffmpeg 9.0.1 -ss 112 -t 786 -map 0:a:0 -c:a libmp3lame -b:a 96k "
                           "-map_metadata -1 -id3v2_version 3",
        "media_usage_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
        "source_notice": "NASA source material; no NASA review or endorsement of WorldMock or model outputs.",
    }
    item = {"item_id": identity["item_id"], "title": "Space Habitat (English interview excerpt)",
            "kind": "podcast_episode", "genre": ["science", "spaceflight"], "language": "en",
            "age_rating": 0, "duration_seconds": END - START,
            "timeline_path": "podcasts/podcast-0001.srt",
            "timeline_sha256": hashlib.sha256(timeline).hexdigest(),
            "audio_path": identity["excerpt_audio_path"],
            "audio_sha256": AUDIO_SHA256, "source_identity_path": "podcasts/nasa-space-habitat.identity.json"}
    render = lambda value: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return {"podcast-0001.srt": timeline, "nasa-space-habitat.identity.json": render(identity),
            "items.json": render([item])}


def main() -> None:
    """@brief 写入或验证冻结 NASA 播客资产。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    for filename, expected in _outputs().items():
        path = ROOT / filename
        if check:
            if not path.is_file() or path.read_bytes() != expected:
                raise SystemExit(f"NASA podcast asset differs: {filename}")
        else:
            path.write_bytes(expected)


if __name__ == "__main__":
    main()
