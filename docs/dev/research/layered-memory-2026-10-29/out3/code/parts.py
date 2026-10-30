"""Current storage of memory_layer observation parts in V/B (read-only)."""
import sqlite3, json, sys
for run in sys.argv[1:]:
    con = sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro", uri=True)
    shas = [r[0] for r in con.execute("select json_extract(body,'$.data.memory_layer') from events where json_extract(body,'$.event_type')='observation' order by event_seq")]
    distinct = set(shas)
    sizes = [len(con.execute("select body from parts where sha256=?", (s,)).fetchone()[0].encode()) for s in distinct]
    total_parts = con.execute("select count(*), sum(length(CAST(body AS BLOB))) from parts").fetchone()
    import os
    print(run.split('/')[-1], "observations", len(shas), "distinct memory parts", len(distinct), "bytes", sum(sizes),
          "all parts", total_parts, "db bytes", os.path.getsize(f"{run}/run/footprints.sqlite"))
