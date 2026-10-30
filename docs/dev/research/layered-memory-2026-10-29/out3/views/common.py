"""Shared read-only loaders for the views analysis (V and B runs)."""
import sqlite3, json, datetime, zoneinfo, re, unicodedata
TZ = zoneinfo.ZoneInfo("Asia/Shanghai")
ROOT = "/Users/atishoo/Project/WorldMock-Dataset/execution/"
RUNS = {"V": ROOT + "20261029T023604Z-white-collar-food-variety", "B": ROOT + "20261029T023604Z-white-collar-food-baseline"}

def connect(run):
    return sqlite3.connect(f"file:{RUNS[run]}/run/footprints.sqlite?mode=ro", uri=True)

def load(run):
    db = connect(run)
    events = [json.loads(body) for _, body in db.execute("select event_seq, body from events order by event_seq")]
    parts = {sha: body for sha, body in db.execute("select sha256, body from parts")}
    return events, parts, db

def part(parts, sha):
    return json.loads(parts[sha])

def day(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%m-%d")

def hm(ts):
    return datetime.datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M")

def compact(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

HAN = re.compile(r"[一-鿿]")
def hanzi(s):
    return len(HAN.findall(s))

def norm(s):
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\s\W_的了]", "", s)

def pct(xs, q):
    xs = sorted(xs)
    if not xs: return None
    k = (len(xs) - 1) * q
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

def stats(xs):
    return {"n": len(xs), "median": pct(xs, .5), "p90": pct(xs, .9), "max": max(xs) if xs else None,
            "mean": round(sum(xs) / len(xs), 1) if xs else None}
