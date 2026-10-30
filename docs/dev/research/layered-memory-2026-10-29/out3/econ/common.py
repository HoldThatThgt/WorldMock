"""Read-only helpers shared by the out3/econ scripts (no writes to runs or repos)."""
import json, sqlite3
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
ROOT = "/Users/atishoo/Project/WorldMock-Dataset/execution/"
RUNS = {"V": ROOT + "20261029T023604Z-white-collar-food-variety",
        "B": ROOT + "20261029T023604Z-white-collar-food-baseline"}
# frozen peak rates, src/worldmock/recording_engine/reports/quality.py:55-66 (USD per 1M tokens)
P_HIT, P_MISS, P_OUT = 0.006e-6, 0.30e-6, 1.20e-6


def load(run):
    con = sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro", uri=True)
    return [(s, json.loads(b)) for (s, b) in con.execute("select event_seq, body from events order by event_seq")]


def day(t):
    return datetime.fromtimestamp(t, TZ).strftime("%m-%d")


def ser(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def is_decision(req):
    m = req.get("messages") or []
    try:
        return m and m[0]["role"] == "system" and m[0]["content"].startswith('{"layer":"系统层"')
    except Exception:
        return False
