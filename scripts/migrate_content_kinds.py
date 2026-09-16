"""Migrate scenario content kinds from the placeholder to real experience kinds.

Every contents entry previously carried the placeholder kind
``subtitle_experience`` while ``experience_label`` held the real experience
(feature_film_watch / drama_series_watch / webnovel_excerpt_reading /
casual_news_feed_scroll / casual_joke_feed_scroll / subtitle_experience).
This one-shot migration derives the real kind from the label and rewrites
both scenario files in place. All other fields stay byte-identical
(canonical indent=2 round-trip is verified before writing).

Usage:
  python3 scripts/migrate_content_kinds.py           # rewrite scenarios in place
  python3 scripts/migrate_content_kinds.py --check   # compare in memory, exit 1 on drift
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    ROOT / "scenarios/multi-person.json",
    ROOT / "scenarios/white-collar-12-weeks.json",
)

LABEL_TO_KIND = {
    "feature_film_watch": "feature_film",
    "drama_series_watch": "drama_series",
    "webnovel_excerpt_reading": "webnovel_excerpt",
    "casual_news_feed_scroll": "news_feed",
    "casual_joke_feed_scroll": "joke_feed",
    "subtitle_experience": "subtitle_experience",
}


def migrate_document(document: dict) -> tuple[dict, dict[str, int]]:
    """按 experience_label 把占位 kind 改写为真实类型并返回迁移计数。"""
    counts: dict[str, int] = {}
    for item in document["contents"]:
        label = item["experience_label"]
        if item["kind"] != "subtitle_experience":
            if item["kind"] == LABEL_TO_KIND.get(label):
                counts.setdefault("(already migrated)", 0)
                counts["(already migrated)"] += 1
                continue
            raise SystemExit(f"unexpected kind {item['kind']!r} for label {label!r}")
        kind = LABEL_TO_KIND.get(label)
        if kind is None:
            raise SystemExit(f"unknown experience_label {label!r}")
        item["kind"] = kind
        counts[kind] = counts.get(kind, 0) + 1
    return document, counts


def main(argv: list[str]) -> int:
    """执行迁移或漂移检查。"""
    check_only = "--check" in argv
    for path in SCENARIOS:
        raw = path.read_text(encoding="utf-8")
        document, counts = migrate_document(json.loads(raw))
        content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        migrated = sum(count for key, count in counts.items() if key != "(already migrated)")
        summary = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
        if check_only:
            status = "OK" if raw == content else "DRIFT"
            print(f"{status} {path.name}: migrated_total={migrated} ({summary})")
            if raw != content:
                return 1
            continue
        path.write_text(content, encoding="utf-8")
        print(f"WROTE {path.name}: migrated_total={migrated} ({summary})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
