"""Calibrate weekday hourly travel-time factors from the anchored snapshot.

Reads the machine-readable anchor block inside
catalog/snapshots/traffic/amap-report-excerpt.md (the only input),
linearly interpolates the missing hours with exact Decimal arithmetic
(ROUND_HALF_UP to 2 decimals), validates the structural shape of the
resulting curve, and emits catalog/snapshots/traffic/weekday-hour-factors.json
byte-deterministically.

Usage:
  python3 scripts/calibrate_traffic.py           # write the factor table
  python3 scripts/calibrate_traffic.py --check   # compare bytes, exit 1 on drift
"""

from __future__ import annotations

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCERPT = ROOT / "catalog/snapshots/traffic/amap-report-excerpt.md"
OUTPUT = ROOT / "catalog/snapshots/traffic/weekday-hour-factors.json"
ANCHOR_BLOCK_KEY = "traffic-calibration-anchors"
QUANTUM = Decimal("0.01")
#: 锚点与成表数值的合法区间（自由流下界 1.0，防坏数据上界 3.0）。
BOUNDS = (Decimal("1.0"), Decimal("3.0"))
#: 形态合同：全局峰顶 18 时，早高峰窗（7-9 时）峰顶 8 时。
GLOBAL_PEAK_HOUR = 18
MORNING_WINDOW = range(7, 10)
MORNING_PEAK_HOUR = 8


def load_anchors(excerpt: Path = EXCERPT) -> dict:
    """@brief 解析摘录文件里的锚点块并做输入校验。"""
    text = excerpt.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    matches = [json.loads(block) for block in blocks
               if ANCHOR_BLOCK_KEY in json.loads(block)]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {ANCHOR_BLOCK_KEY} block, found {len(matches)}")
    payload = matches[0][ANCHOR_BLOCK_KEY]
    raw = payload["anchors"]
    hours = {int(key): Decimal(value) for key, value in raw.items()}
    if len(hours) != len(raw) or sorted(hours) != sorted(set(hours)):
        raise SystemExit("anchor hours must be distinct integers")
    if not set(hours) <= set(range(24)):
        raise SystemExit("anchor hours must lie in 0..23")
    if len(hours) < 3:
        raise SystemExit("at least three anchor hours are required")
    low, high = BOUNDS
    for hour, value in sorted(hours.items()):
        if not low <= value <= high:
            raise SystemExit(f"anchor {hour:02d} value {value} outside [{low}, {high}]")
    if min(hours.values()) != Decimal("1.00"):
        raise SystemExit("free-flow baseline anchor 1.00 is required")
    return payload


def interpolate(anchors: dict[int, Decimal]) -> dict[int, Decimal]:
    """@brief 邻近锚点线性插值补全 24 小时，两位小数 HALF_UP。"""
    keys = sorted(anchors)
    table: dict[int, Decimal] = {}
    for hour in range(24):
        if hour in anchors:
            table[hour] = anchors[hour].quantize(QUANTUM, rounding=ROUND_HALF_UP)
            continue
        previous = max(key for key in keys if key < hour)
        following = min(key for key in keys if key > hour)
        span = Decimal(following - previous)
        weight = Decimal(hour - previous) / span
        value = anchors[previous] + (anchors[following] - anchors[previous]) * weight
        table[hour] = value.quantize(QUANTUM, rounding=ROUND_HALF_UP)
    return table


def validate_shape(table: dict[int, Decimal]) -> None:
    """@brief 校验成表形态：24 小时齐、夜谷贴 1.0、双峰结构成立。"""
    if sorted(table) != list(range(24)):
        raise SystemExit("the table must cover hours 0..23")
    if min(table.values()) != Decimal("1.00"):
        raise SystemExit("the hourly minimum must stay at the free-flow baseline 1.00")
    if max(range(24), key=lambda hour: table[hour]) != GLOBAL_PEAK_HOUR:
        raise SystemExit(f"the global peak must sit at hour {GLOBAL_PEAK_HOUR}")
    morning = [hour for hour in MORNING_WINDOW if hour in table]
    if max(morning, key=lambda hour: table[hour]) != MORNING_PEAK_HOUR:
        raise SystemExit(f"the morning peak must sit at hour {MORNING_PEAK_HOUR}")


def build_table(excerpt: Path = EXCERPT) -> bytes:
    """@brief 锚点 → 完整系数表的规范序列化字节。"""
    payload = load_anchors(excerpt)
    anchors = {int(key): Decimal(value) for key, value in payload["anchors"].items()}
    table = interpolate(anchors)
    validate_shape(table)
    document = {
        "schema": "worldmock/traffic-hour-factors/1",
        "timezone": payload["timezone"],
        "day_type": payload["day_type"],
        "unit": "travel_time_factor",
        "definition": "planned_seconds = ceil(free_flow_seconds * factor(departure_hour))",
        "index_definition": payload["index_definition"],
        "source": payload["source_family"],
        "provenance": {
            "type": "anchored",
            "artifact": "catalog/snapshots/traffic/amap-report-excerpt.md",
            "method": "manual anchor points + linear interpolation (ROUND_HALF_UP, 2dp)",
            "calibration_script": "scripts/calibrate_traffic.py",
        },
        "anchors": {f"{hour:02d}": str(anchors[hour].quantize(QUANTUM, rounding=ROUND_HALF_UP))
                    for hour in sorted(anchors)},
        "hours": {f"{hour:02d}": str(table[hour]) for hour in range(24)},
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    content = build_table()
    if check_only:
        current = OUTPUT.read_bytes() if OUTPUT.is_file() else None
        if current != content:
            print(f"DRIFT {OUTPUT.relative_to(ROOT)} (bytes differ from calibration)")
            return 1
        print(f"OK    {OUTPUT.relative_to(ROOT)} (byte-identical)")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(content)
    print(f"WROTE {OUTPUT.relative_to(ROOT)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
