# Notes: per-decision 今天 entries (A) and search-only views (B)

Scripts here (all read-only on V/B; outputs beside them):
- windows.py → windows_V.json, windows_B.json, windows_summary.json: per-decision windows (C_{k-1}, C_k],
  fresh vs chain, decision classes, 自述 lengths. 核验 lines approximated at 59 chars each (brief F16).
- entries120.py → entries120.json: entry sizes under "each 今天 entry ≤120字" (核验 kept whole, 自述 fills the rest),
  start/rejection/plan code lines added (approx 20/~30/12 chars), JSON skeleton overhead measured.
- views120.py: survival of 看法 items under the 120 cap, summary-first vs views-first.
- incommit.py: agent-visible world events inside decision commits vs time advance; what follows rejections.
- labels.py: short labels naming two activities on one day.
- parts.py: current memory_layer observation-part storage.
- peek.py, peek2.py: event shape probes.

Key numbers
- V: 406 decisions (305 fresh, 101 chain by "first action request has >2 messages"; brief F4 says 103).
  Per day 81/84/83/69/89 → one entry per decision means up to 89 entries/day (not 67; 67 is fresh only).
  B: 409 decisions, 309 fresh, 100 chain; per day max 85.
- Chain runs: V 95×1 + 3×2 continuations; B 96×1 + 2×2 → each chain tool message would carry exactly one new entry.
- Classes V: conclude 137, report_status 136 (121 with no facts, no note), start-only 128 (103 no facts no note,
  17 note only), rejected 5. B similar (+1 pause, +1 revise_day_plan).
- Under the 120 cap: median entry 155 chars incl. JSON (V), p90 171, max 224; today at day end 8.7–11.7k chars (V),
  10.2–10.7k (B); median 今天 seen by a decision ≈5.2k (V).
- 215/406 V entries exceed 120 before capping; 核验 alone >120 in only 2 (V) / 9 (B).
- Conclude entries untruncated at 120: 11/137 (V), 17/137 (B) → recall "已在记忆层" dedup fires ≤12%.
- 看法: 76 in V; summary-first ordering → 0 survive whole in the prompt; views-first → 49/76 whole.
- In V/B no agent-visible world event was committed inside a decision commit (all in time advance), but code can:
  decision_coordinator.py:342-347,413-435 (action callbacks incl. balance_decreased, callbacks.py:111,320).
- All 7 rejected-only decisions (V 5, B 2) were followed by a same-second chain decision.
- Short labels naming 2+ activities on one day: 26/111 (V), 21/112 (B).
- memory_layer parts today: 22 distinct (V, 186 KB), 30 (B, 313 KB); DB 131 MB / 123 MB. Per-decision → ~406 parts,
  ≈7k chars each → ≈7–8 MB more per 5-day run (approx 2.7 B/char).
