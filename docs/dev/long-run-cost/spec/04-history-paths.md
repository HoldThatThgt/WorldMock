# Whole-history paths and live-run safety

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements the proposal's section "Whole-history paths": every row of its table, the paragraph
"Live-run reader safety" and the paragraph "Mid-run cutoffs". It also implements these binding rows of the proposal:
- "Decisions": "Restore re-validation" (kept, streamed), "Live-run reader safety" (short reads in `status`,
  `inspect_run` and `render_day`, plus a 120 s busy timeout on the engine connection; the rollback journal stays) and
  "Export check return value" (the manifest and activities; callers updated, including one line in WorldMock-Dataset's
  `tools/execution.py`).
- "Not proposed": "WAL journal mode" and "Certifying the timeline files inside the export check" are not done here.

Every rule here leaves each model prompt, decision input and settled world fact byte-identical. Two things outside the
persona's world change, both named by the proposal:
- an export whose `--cutoff` is earlier than the last event no longer renders timeline rows past the cutoff (an
  evidence fix; quality.json and every other artifact of such an export are unchanged);
- `validate_export` returns `(manifest, activities)` instead of `(manifest, events, activities)`.

One operator-tool reading changes in edge cases only: `worldmock status` without its read transaction may combine
values of consecutive commits on a live run, and its wall-time fields show the first and the last event's stamps,
which equal today's `min`/`max` unless the machine's clock stepped back during the run. Nothing the model sees, no
settled fact and no exported byte reads `status`.

Nothing here changes a model tool schema, the host validation of tool arguments, a CLI option of the run commands or a
field of the environment dataset (`persona.toml`, `config.toml`, catalog files). The busy timeout and the page size are
engine constants.

Research inputs:
- `.work/run30/opt/design_history.json` and `review_history.json` (binding where they correct the design; the list is
  below);
- the critical-path item "Bound the session opening-result read" in `.work/run30/opt/merged.md` (review: keep, with the
  boundary-check note);
- the checkpoint-area item "Branch by SQL copy", revision 4a, in `merged.md` (the artifact rows and the prefix check);
- scratch evidence in `.work/run30/scratch/opt-history/` (scripts 02–17), `.work/run30/scratch/opt-review-history/`
  (01–07), `.work/run30/scratch/opt-critical/proof_session_lower.py`, and this chapter's own probes in
  `.work/run30/scratch/spec-04-history-paths/` (01–08, listed where used).

### Measured cost on main `0bd2de65`

All figures are for the 30-day run (46,142 events, 1,920.6 MB of event JSON) on a scratch copy of its events, on a
shared 12-core machine (±10–20%). [O] is observed in the run, [R] measured offline with main's code.

| Path | Today at day 30 | Where the time and memory go |
| --- | --- | --- |
| Export (`worldmock export`) | about 342 s [O] (events.jsonl at 13:16:47, quality 13:17:11–13:17:53, timelines 13:17:58–13:20:44); peak about 6× the event bytes, about 11.6 GB [R] (`opt-history/04_export_p10.txt`: 3,180 MB on the 10-day prefix) | `store.read_events` decodes every event into one list (`store.py:285-290`); `rows` copies it (`export.py:71`); 31 per-day timeline renders each scan the whole database for `action_result` and read past the cutoff (`export.py:117-136`, `render_day.py:55-58`, `:150-154`: 166.5 s, `opt-history/03_timeline.txt`) |
| Export check (`validate_export`) | 97 s [O] (check.log 13:20:49 → checks/export.json 13:22:26); about 10 GB [R] (5.26× on the prefix, `opt-history/07b`) | `_read_artifact` reads events.jsonl whole and `json.loads` every line (`validation.py:114-125`); strict records of every event (`:43`) |
| Restore (resume, replay) | 66.7 s, 6,040 MB [R] (`opt-history/08_restore_current.txt`) | `read_events` of the whole prefix (`checkpoint.py:101`), re-serialised insert of every event (`:133-135`) |
| `fail_run`, counterfactual check | 28.7 s, 5,920 MB per call [R] (`opt-history/11_fail_run.txt`) | `read_events` of the whole run and `any()` (`engine.py:165-167`, `counterfactual_runtime.py:77`) |
| Experience window per observation | median 129 ms (0.17–0.25 s on the real file) [R] (`opt-history/09_experiences_plain.txt`) | `MULTI-INDEX OR` plus `USE TEMP B-TREE FOR ORDER BY` over every matching row before the cutoff (`store.py:276-282`) |
| plan_day inputs at midnight | 2.53 s [R] (`opt-history/14_plan_inputs.txt`) | eleven `read_agent_events` calls over four event types (`day_plan_inputs.py:137`, `:138`, `:155`, `:156`, `:161`, `:176`, `:201`, `:205`, and three at `:254`) |
| Storefront acceptance lookup | median 143 ms per purchase completion [R] (`opt-history/10_storefront.txt`) | every `action_result` of the agent since the run start (`storefront_runtime.py:95`) |
| Session opening read (cold start) | first observation after a resume 15.5 s [R] (`opt-critical/growth_cold.json`) | `read_agent_events` decodes every later `action_result` of the agent to keep the first match (`session_bounds.py:37`) |
| `inspect_run summary` / `money` | 22.8 s / 24.2 s, about 4.5 GB each [R] (`opt-history/12_inspect.txt`) | `_rows` decodes every event (`inspect_run.py:77-83`) |
| `worldmock status` | 84.0 s inside one read transaction [R] (`opt-review-history/04_read_status.txt`) | `BEGIN` (`status.py:35`); `GROUP BY` over every body (47.3 s), `min`/`max(recorded_at)` over every body (9.8 s), usage sums found by a table scan (`:83-98`) |
| `worldmock evaluate` | not measured [M] | `read_jsonl(events.jsonl)` and `parse_world_event` build every event into one list (`evaluation/adapters.py:360`, `evaluation/io.py:40-50`) |
| A live run with a reader | the engine's next commit fails with `database is locked` after 5.5 s while a reader holds a statement [R] (`opt-history/15_reader_blocks_writer.txt`) | rollback journal plus Python's default 5 s busy timeout (`store.py:51`) |

### Expected saving at day 30

| Path | After | Basis |
| --- | --- | --- |
| Export | about 70 s, about 1 GB peak (estimate) | [R] prototype 46.8 s and 801 MB (`opt-history/05_stream_full.txt`) plus 21–27 s of re-encoding each line as today (`opt-history/02_roundtrip.txt`: `canonical_json` 20.7 s, `model_dump` 6.8 s). The prototype kept a smaller allowlist view; this chapter's post-run view keeps about 109 MB of the 1,920.6 MB of event JSON [R]: 102.3 MB with requests dropped (`spec-04-history-paths/06_denylist_size.txt`) plus 6.7 MB for the request outlines (`08_request_outline.txt`); chapter 03 (which lands first) writes `"request": null` in responses, so they add no outline |
| Export timelines alone | 166.5 s → under 5 s | [R] `opt-history/03_timeline.txt` |
| Export check | about 1 GB (estimate); time roughly the same as today (a hash pre-pass of about 8 s is added) | not prototyped; the post-run view and one row-list of the small types (66.2 MB of JSON) |
| Restore | about 45–55 s, about 0.1 GB | [R] `opt-history/08c_restore_sqlvalidate.txt`: 44.5 s / 119 MB, minus the two extra scans (9.5 s) this chapter does not make, plus two prefix digests (not measured: each reads the prefix rows once without decoding; a body scan without parsing took 2.0 s, `08b_restore_phases.txt`, plus SHA-256 over 1.9 GB) |
| `fail_run`, counterfactual check | 0.02 s, flat memory | [R] `opt-history/11_fail_run.txt` |
| Experience window | median 10.1 ms | [R] `opt-history/09_experiences_plain.txt`; 3,008 of 3,008 windows equal the recorded `verified_experiences` |
| plan_day inputs | about 1.1 s (four reads instead of eleven) | estimate from `opt-history/14_plan_inputs.txt` |
| Storefront lookup | median 2.1 ms | [R] `opt-history/10_storefront.txt`; 162 of 162 completions give the same cause |
| Session opening read | 15.5 → 2.1 s on the first observation after a resume | [R] `opt-critical/growth_cold.json`; 169 of 169 lower bounds equal and the 467,349-byte observation byte-identical |
| `inspect_run summary` / `money` | 0.14–0.21 s / about 4–5 s and 7 MB | [R] `opt-review-history/02_tools_sql.txt`, `opt-history/12_inspect.txt` (stdout byte-identical; `money` measured as one statement, the window paging below adds one primary-key range statement per 256 events) |
| `worldmock status` | no read transaction; counts 0.17 s, wall time 0.0 s, usage sums 28–38 s as one indexed statement on today's bodies (about a fifth of that once chapter 03 writes `"request": null` in responses) | [R] `spec-04-history-paths/02_status_reads.txt`, `05_usage_warm.txt`; all outputs equal. The usage statement is the longest read left: a live run's commit waits for it (within the busy timeout below) |
| A live run with a reader | the commit waits for the reader's statement, up to 120 s, instead of failing after 5 s | SQLite busy handler (https://www.sqlite.org/c3ref/busy_timeout.html) [W] |

At 60 and 90 days today's export, check and restore need about 31/58 GB, 27/50 GB and 16/30 GB (linear in event bytes,
more than this machine's 31.8 GB from 60 days); after this chapter they stay near 1 GB.

### Where this chapter departs from the research, and why

| Research text | This chapter | Why |
| --- | --- | --- |
| Design: keep big events as an allowlist of read fields | A denylist of bulky payloads; every other key stays | Review revision (binding): an allowlist fails unsafe for future readers and drops keys the list-level checks read |
| Review: drop `data.request` of `model_request` and `model_response` | Keep a request outline: messages without `content` and `reasoning_content`, tools without `function.description` and `function.parameters`; everything else kept | Chapter 06 (which lands after this chapter) adds a post-run reader of the request shape (`quality._call_class`: purpose, `extra_body.thinking`, tool names, message roles). The outline keeps all of it: 467.2 MB → 6.7 MB and 3,762 of 3,762 call classes equal [R] (`08_request_outline.txt`). The proposal's own wording is "request copies, response messages" |
| Design: write the stored body | Write `canonical_json(event.model_dump())` per line, exactly as today | Review revision (binding); also the only rule that stays true after chapter 03 stores observations with part references |
| Review: per-line envelope checks and artifact collection in pass 2 as folds | `_validate_events` and `_validate_assets` run unchanged after pass 2 on the post-run view | They read only envelope fields, which the view never touches, so the fold form saves no memory and adds code |
| Review: count `initial_funds_injected` on the full row | The ledger scan runs unchanged on the post-run view | It reads `data.kind`, which no reduction touches (pinned by a test); the forged-observation negative test the review asks for is added |
| Design: optional `events_agent_type_kind` index; deferred json index during restore | Neither | Review revisions (binding) |
| Design: bound plan_day single-event parts by the review window | No window bounds; each of the four event types read once and shared | Review revision and the proposal's row ("no window bounds, so plan prompts cannot depend on when plans are written") |
| Design: WAL, or short reads | Short reads plus a 120 s busy timeout; rollback journal stays | Proposal "Decisions" and "Not proposed" |
| Design: `inspect_run` summary/money only | Every `inspect_run` read is paged by `event_seq` windows of `EVENT_PAGE_SIZE` sequences | The proposal: inspect_run uses "no long transaction"; one paged helper covers every sub-command. A page bounded by a match count (`LIMIT`) would still scan the whole history in one statement for a sparse filter (`events --type run_failed`), so each statement is bounded by a sequence window instead |
| Design: status wall time by primary key (this chapter) and chapter 05's liveness (first and last event by primary key) | This chapter adds chapter 05's two statements `_FIRST_EVENT_SQL` and `_LAST_EVENT_SQL`, text for text, and uses them in today's `_wall_time` | Both chapters replace the same `min`/`max` scan; one text avoids two versions of it (open question for the integrator below) |
| Review: certify the timeline files in the check (optional) | Not done | Proposal "Not proposed" |

### Order of work inside this chapter

The review's order, each step with its own tests: the timeline read, `fail_run`, short reads in `status`,
`inspect_run`, `render_day` and the busy timeout, the post-run view with the streaming export, the branch copy, the
streaming check with its return value, the evaluation adapter, the experience window, the storefront bound and the
plan inputs, the session opening read.

## Behaviour

### Store invariants this chapter relies on

Each is enforced today and stays enforced:
- **Contiguous sequence.** `event_seq` runs 1..N without gaps: the trigger `events_append_only` (`store.py:29-31`)
  aborts any insert whose `event_seq` is not `MAX+1`, for every row, including rows of an `INSERT … SELECT`
  ([R] `spec-04-history-paths/04_attach_probe.txt`: a descending copy is refused and leaves no row).
- **Immutable rows.** `events_no_update` and `events_no_delete` (`store.py:32-33`). So the rows of `(after, cutoff]`
  never change after they are committed, and short reads of them, one page at a time, read one consistent prefix.
- **Monotone event time.** `_build_events` (`store.py:133-134`) refuses an `occurred_at` earlier than the last event.
- **Unique event identity.** `event_id TEXT NOT NULL UNIQUE` (`store.py:23`).
- **Rows match bodies.** The store inserts `(event.event_seq, event.event_id, canonical_json(event.model_dump()))`
  (`store.py:174-176`), so the two columns equal the body's fields. The paged reader now checks it (below).
- **Own events are a suffix.** A branch's events are the parent prefix (ancestor run ids) followed by its own events;
  the store stamps every new event with `self.manifest["run_id"]` (`store.py:140`), and the child's run id is a fresh
  `uuid4().hex` (`checkpoint.py:122`), which no ancestor event carries.
- **One row decoder.** Chapter 03 adds `FootprintStore._decode(body: str) -> Event` and routes every reader through it.
  This chapter is implemented after chapters 01–03; every new reader below decodes through `self._decode`. (On main
  alone, `_decode(body)` is `Event.model_validate_json(body)`.)
- **No SQL inside observation parts.** Chapter 03's invariant holds for every statement added here: each predicate reads
  envelope fields (`event_type`, `agent_id`, `action_id`, `run_id`, `recorded_at`, `resource_changes`) or inline keys
  of other event types (`data.kind` of environment events, `data.phase` and `data.response` of model responses).

### Paged event reads

`FootprintStore.iter_events(cutoff, after=0)` is the only reader of an event range:
- It checks `0 <= after <= cutoff <= self.last_event_seq` when it is called, before it returns the iterator; outside
  that range it logs ERROR `Event range (%d, %d] is outside the committed sequence` and raises
  `IntegrityError("Event cutoff is outside the committed sequence")` (today's text from `store.py:288`).
- It reads `(after, cutoff]` in pages of `EVENT_PAGE_SIZE = 256` events. Each page is one statement,
  `SELECT event_seq,event_id,body FROM events WHERE event_seq>? AND event_seq<=? ORDER BY event_seq`, fetched whole
  with `fetchall()`. In Python's default transaction mode a `SELECT` opens no transaction
  ([R] `spec-04-history-paths/01_wal_probe.txt`: `in_transaction=False` after `fetchall`), so the shared lock is held only
  while one page is read. The largest observation of the run is about 470 KB, so a page is at most about 120 MB of text.
- Each row is decoded by `self._decode(body)`. If the decoded `event_seq` or `event_id` differs from the row's columns,
  it logs ERROR `Event row %d disagrees with its body` and raises `IntegrityError("Event row disagrees with its body")`.
  This never fires on a store written by the store; it ties the two columns the branch copy takes as bytes to the body
  that was validated (today's branch takes them from the decoded body).
- Events are yielded in `event_seq` order. Nothing is cached.

`read_events` (`store.py:285-290`) is deleted. Its four production callers move to the reads below; tests use
`list(store.iter_events(cutoff))`.

### The post-run view

A **post-run view** of an event is the same event with a smaller `data`. Only three event types are reduced; every
other event is returned as the same object. In memory, the export, the export check and the evaluation adapter keep
post-run views; `events.jsonl` and the run database always hold the full events.

| Event type | What the view removes from `data` |
| --- | --- |
| `observation` | the keys `activity_bindings`, `persona_views`, `tool_layer`, `interaction_bindings`, `catalog_bindings`, `memory_layer`, `goal_bindings`, `locations`; and, inside `errand_item_bindings`, the rows no `errand_bindings` row can reach (rule below) |
| `model_request` | inside `request`: each message's `content` and `reasoning_content`; each tool's `function.description` and `function.parameters` |
| `model_response` | the same inside `request` (after chapter 03 it is `null`, which stays `null`); inside `response`: the `message` of every item of `choices` |

Every key not named in the table stays, including keys a later chapter adds (for example `transport_attempts` of
chapter 05) and the keys every list-level check reads on every event: `kind`, `activity`, `data`.

Exact rules (the function never raises, whatever the shape):
- Observation keys: `{key: value for key, value in data.items() if key not in DROPPED_PAYLOADS[event_type]}`, insertion
  order kept.
- `errand_item_bindings`, only when present: if its value is not a list it is kept as is. Otherwise `used` is the set of
  `row["product_id"]` over the rows of `errand_bindings` that are dicts with a `str` `product_id` (empty when
  `errand_bindings` is missing or not a list). A row is dropped only when it is a dict, its `product_id` is a `str`, it
  has a `name` key, and its `product_id` is not in `used`. Every other row is kept, in order.
- Request outline, only when `data["request"]` is a dict: a shallow copy; if its `messages` is a list, each message
  that is a dict loses `content` and `reasoning_content` (other items unchanged); if its `tools` is a list, each tool
  that is a dict whose `function` is a dict gets `function` without `description` and `parameters` (other items
  unchanged). Every other request key stays.
- Response, only for `model_response` when `data["response"]` is a dict whose `choices` is a list: a shallow copy whose
  `choices` holds each dict item without `message` and every other item unchanged.

Why no post-run reader can tell the difference. The inventory of every read of a big event's `data` by the export, the
check and the evaluation adapter (main, plus chapter 06's classifier):

| Reader | Reads | Kept by the view |
| --- | --- | --- |
| `quality._inspect_memory_observation` (`quality.py:409-433`) | observation `verified_experiences`, `progress_event_ids` | yes |
| `errand_metrics._observation_counts` (`errand_metrics.py:173-190`) | observation `todo_layer.errands[].name` | yes |
| `errand_metrics.conclusion_memories` / `_errand_texts` (`:117-118`, `:245-250`) | observation `todo_layer`, `errand_bindings[].product_id`, `errand_item_bindings[].product_id`/`.name` | yes; dropped item rows are never looked up (below) |
| `quality._model_quality`, `_select_terminal`, `_usage_entry` (`quality.py:583-643`) | request `call_id`, envelope `recorded_at`; response `call_id`, `phase`, `error`, `purpose`, `response` (dict test), `response.usage`, `response.model` | yes |
| `quality._inspect_memory_response`, `_name_rejection_quality`, `_terminal_responses` (`:396-406`, `:767-808`) | response `purpose`, `protocol_feedback.items[].path`, `protocol_feedback.error_code` | yes |
| `quality._truncation_quality`, `_finish_reason`, `_usage_count` (`:867-911`) | response `choices[0].finish_reason`, `usage.completion_tokens`, `usage.completion_tokens_details.reasoning_tokens` | yes |
| `quality._usage_by_purpose` (`:753-762`), replaced by chapter 06's `_call_class` / `_message_roles` | request `purpose`; `request.purpose`, `request.extra_body.thinking`, `request.tools[].function.name`, `request.messages[].role` | yes ([R] 3,762 of 3,762 classes equal) |
| `app_usage.validate_activity_prefix`, `activity_snapshots` (`app_usage.py:108-118`, `:244-248`) on every event | `activity`, `data.activity`, `data.activities` | yes (never reduced) |
| `ledger.validate_account_history` (`ledger.py:44`), `invariants._order_invariants` (`invariants.py:177`), `_payment_invariants` (`:288`), `_order_notifications` (`:509`) on every event | `kind` | yes |
| `invariants._validate_payment_order` (`invariants.py:333-337`) on cause events and on every source event, `location_stays._validate_departure` (`location_stays.py:94`) on cause events | `activity`, `data.activities`, `status` | yes |
| `evaluation/goal_metrics._action_evidence` (`evaluation/goal_metrics.py:67-74`) on every event | `reused`, `status` (no effect unless the type is `action_rejected`/`action_result`) | yes |
| `evaluation.adapters._adapt_world_event`, `_world_interval` (`adapters.py:445-497`) on every event | `ended_at`, `status`, `checkpoint_id`, and `validate_app_event_source`'s keys | yes |

No reader of `choices[*].message`, of message `content`/`reasoning_content`, of tool schemas or of the eight
observation keys exists in these paths (`behavior_metrics` reads request messages but has no production caller; it is
not on any of these paths). `_errand_texts` builds `items = {row["product_id"]: row["name"] ...}` and looks up only the
`product_id` of `errand_bindings` rows: a `str` key is looked up only when it is in `used`, a non-`str` lookup key can
never equal a `str` key, and a dropped row is a dict with both keys, so building the dict from it could not raise. The
lookups, the last-wins values and the first exception (from a malformed kept row) are therefore the same.

The table lives in one place, `footprints/post_run.py`. A reader that starts reading a removed key fails the guard test
(the export from full events and from post-run views must be byte-identical on fixtures whose observations carry every
removed key; on the 30-day run 3,008 of 3,008 observations carry all eight [R] `06_denylist_size.txt`). On the 10-day
prefix, activities, verified experiences, purchases, app usage, location stays and quality.json computed by main's own
functions from full events and from the views are byte-identical [R] (`07_denylist_equivalence_p10.txt`).

### Export

`export_run(store, cutoff, destination)` keeps its signature and its checks, in today's order:
1. `events = store.iter_events(cutoff)` raises for a cutoff outside the committed sequence before anything else, with
   today's text.
2. `cutoff == 0` logs ERROR `Cannot export an empty event range` and raises
   `IntegrityError("Cannot export an empty event range")` (the sequence is contiguous from 1, so this is exactly
   today's empty list).
3. `destination` resolved; an existing destination raises `IntegrityError("Export destination already exists")` as
   today; the parent is created; the stage is `tempfile.mkdtemp(prefix=".export-", dir=destination.parent)`.
4. Inside the stage's `try`, one pass writes `events.jsonl` and keeps the views (`_write_events`):
   - for each event from `iter_events`, the line is `canonical_json(event.model_dump()) + b"\n"`, the same expression
     `write_jsonl` uses today (`export.py:39`, with `rows = [event.model_dump() ...]` at `:71`);
   - the line is written to `stage / "events.jsonl"` (opened `"xb"`) and fed to one `hashlib.sha256`;
   - `post_run_view(event)` is appended to the in-memory list;
   - after the pass the file is flushed and `os.fsync`ed; the artifact descriptor is
     `{"path": "events.jsonl", "rows": <count>, "sha256": <hex>}`, the dict `write_jsonl` returns today.
5. `_write_export(store, events, events_artifact, stage)` runs today's body on the list of views; only the `"events"`
   entry of `manifest["artifacts"]` is the descriptor from step 4 instead of `write_jsonl(...)`.
6. Timelines come from the same list (`_write_timeline_projections(store, events, stage, days, names)`):
   `progress, orders = timeline_sources(events)` once; for each agent id of the frozen manifest (sorted, as today) the
   agent's bodies are `[body for body in progress if body.get("agent_id") == agent_id]`; for each day of
   `daily_coverage` the file is `day_markdown(agent_id, day, day_rows(...))` from `scripts/render_day.py`, written and
   hashed as today.
7. `manifest.json` written and the stage renamed, as today. Any exception logs `Footprint export failed before
   publication` (today's line), removes the stage and propagates.

An invalid event body now raises inside the stage (step 4) instead of before the stage exists; the outcome is the same:
nothing is published and the same exception propagates.

Why every exported byte is the same for `cutoff` equal to the last event:
- `events.jsonl`: the same events (paged reads return exactly `(0, cutoff]` in order), the same encoding expression,
  the same order; incremental SHA-256 of the concatenation equals the SHA-256 of the joined bytes (hashlib).
- `activities`, `verified_experiences`, `purchases`, `app_usage`, `location_stays`, `quality.json`: the same functions
  on post-run views, which differ from full events only in keys no reader reads (inventory above). `rows` are now
  `model_dump()` of the views; the projections read no removed key.
- Timelines: today's per-day render reads every `activity_progress` of the agent and every `action_result` of the
  database (`render_day.py:48-58`); at the last event that is exactly the cutoff prefix. `day_rows` reads only `data`
  of these small events, whose values are the same from `json.loads(body)` and from `model_dump(mode="json")`; the
  quality report's `timeline_label_report` already renders from `model_dump(mode="json")` bodies with the same
  `day_rows`. [R] the 10-day prefix export, all 30 files including manifest.json, is byte-identical
  (`opt-history/05_stream_p10.txt`); on 30 days every timeline equals main's per-day render (`opt-history/06`).
- Asset index, checkpoint embed, identity: unchanged code over envelope fields.

**Mid-run cutoffs.** With `cutoff` earlier than the last event, the timelines contain only rows from progress and
orders at or before the cutoff. Today the per-day render reads the whole database: [R] on the 10-day prefix with
cutoff 16,000 of 16,821, `1-2026-10-07.md` gained 24 rows after the cutoff and one changed row
(`opt-history/17_cutoff_timeline.txt`, and the review's diff of `p10_cut_current` against `p10_cut_stream`). Only the timeline files of such exports and their digests
in `manifest.json` change; quality.json already used the cutoff prefix (`timeline_labels.py:47`).

### Export check

`validate_export(directory) -> tuple[dict, list[dict]]`, in this order:
1. `manifest.json` read and `_validate_identity(manifest)`, unchanged.
2. **Pass 1** (`_authenticated_events(root, manifest["artifacts"]["events"])`), before any line is parsed:
   - the path check of `_read_artifact`, same text: `Export artifact path is invalid`;
   - the file is read in binary line mode; every chunk feeds one `hashlib.sha256`, and the row count adds
     `len(chunk.splitlines())` per chunk. This equals `len(raw.splitlines())` of today's whole-file read for any bytes:
     `bytes.splitlines` breaks only at `\n`, `\r` and `\r\n`, binary line iteration ends every chunk but the last right
     after a `\n`, so no `\r\n` pair is split across two chunks and every chunk boundary is a line boundary of the whole
     file ([R] also checked on 200,000 random byte strings over `\n`, `\r`, `\r\n`, `\v`, `\f`, `\x1c`, U+0085, U+2028
     and others: `spec-04-history-paths/03_splitlines_stream.txt`);
   - a different digest raises `Export artifact hash is invalid`; then a different row count raises
     `Export artifact row count is invalid` (today's texts, each now after an ERROR line with the same text);
   - it returns the hex digest.
3. The other six artifacts and the asset index are read with `_read_artifact` as today (unchanged).
4. **Pass 2** (`_read_events(root / "events.jsonl", digest)`): the file is read again in binary line mode; each chunk
   feeds a second SHA-256; for each `line in chunk.splitlines()`:
   - `row = json.loads(line)` (a `JSONDecodeError`, a `ValueError`, propagates as today);
   - `validate_activity_prefix([row])` on the full row (moved here from `validation.py:42`);
   - `record = Event.model_validate(row, strict=True)` (today's call, `:43`);
   - `post_run_row(row)` is appended to the row list and `post_run_view(record)` to the record list.
   After the last line, a digest different from pass 1 logs ERROR `Export events changed between the two reads` and
   raises `IntegrityError("Export artifact hash is invalid")`: the parsed bytes are always the authenticated bytes.
5. Everything after today's `:43` runs unchanged on the two lists: `_validate_checkpoint`, `_validate_events`,
   `_validate_assets`, `validate_account_history`, the six comparisons (the line
   `if project_location_stays(events) != stays:` keeps its text: it is a registered mutation target),
   `validate_frozen_environment`, `validate_footprint_facts`, `validate_location_stays`, `_validate_quality`.
6. It returns `(manifest, activities)`. The event count a caller needs is `manifest["artifacts"]["events"]["rows"]`,
   which step 2 has proved equal to the file and step 5 (`_validate_events`) equal to `cutoff_event_seq`.

Accept and reject are identical to today for every input: every check reads the same values (the view keeps every
key a check reads, inventory above), and nothing new can raise on input today accepts. For an input with one fault the
exception type and text are today's, including the review's case "tampered hash plus an invalid line", which reports
`Export artifact hash is invalid` because pass 1 parses nothing. An input with several faults may report a different
one of them first (for example a bad row count is now found before a malformed line), and is rejected either way. The
evaluation entry maps the same exception types (`evaluation/validation.py:222-224`).

### Evaluation adapter

`_adapt_canonical` (`evaluation/adapters.py:357-388`) reads events with a streaming twin of `read_jsonl`:
`event_rows = [post_run_view(parse_world_event(row)) for row in iter_jsonl(delivery.event_path)]`.
- `iter_jsonl(path)` is today's `read_jsonl` loop as a generator: same open (`encoding="utf-8"`), the same
  `EvaluationError` codes and texts for an unreadable file, a blank row, invalid JSON and a non-object row;
  `read_jsonl(path)` becomes `list(iter_jsonl(path))` and keeps its other callers.
- Each row is parsed in full (`parse_world_event`, unchanged) before its view is kept, so schema rejection is today's.
- The adapter, `worldmock_public_evidence` and `build_days` read no removed key (inventory above; `public_evidence.py`
  reads `data` only of `action_requested`, `action_result`, `environment_event` and `activity_progress`). The
  `DatasetView` is equal to the one built from full events (test below). The LabelKit path is unchanged.

### Branch by SQL copy

`restore_branch(store, checkpoint_id, destination, branch_metadata=None)` keeps its signature and order:
1. `checkpoint = store.get_checkpoint(checkpoint_id)` and `_branch_metadata`, as today. This first read on the parent's
   read-write connection also rolls back a hot journal a killed parent left, which a read-only attach could not do
   ([R] `04_attach_probe.txt`: `attempt to write a readonly database`).
2. The destination check, as today (`Restore requires a new destination outside the parent run`).
3. **Re-validation, streamed** (`_validated_prefix(store, checkpoint.last_event_seq)`), before any directory exists:
   for every event of `store.iter_events(cutoff)` (decoded through `_decode`, so chapter 03's parts are checked, and
   row-checked), its `run_id` joins a set, and every `AssetRef` of its `artifact_refs` is read and hashed with
   `store.assets.read(reference)` (today `checkpoint.py:102-104`) and appended, in order, to a tuple. It returns
   `_ParentPrefix(run_ids, references)`.
4. The stage and `_populate_branch(store, checkpoint, prefix, stage, metadata)`:
   - the manifest as today; `source_run_ids = sorted(prefix.run_ids | {manifest["run_id"]})` (today `:125` from the
     list; the same set);
   - `child = FootprintStore.create(stage, manifest, checkpoint.state)`;
   - assets: `_branch_asset_references(store, checkpoint, prefix.references)` builds the same dict as today (snapshots,
     then environment assets, then event references in event order, later entries overwriting earlier ones) and every
     asset is put as today;
   - `child.copy_event_prefix(store, checkpoint.last_event_seq, prefix.references)` (below) replaces the per-event
     insert (`:133-135`) and chapter 03's `with child.connection: child.copy_history(store, events)` that stands
     there after chapter 03; `copy_history` and `observation_parts.copy_parts` then have no caller and are deleted
     (with `copy_parts` in `store.py`'s import line and in the `OWNERS` entry chapter 03 adds to
     `tests/unit/engineering/test_single_source.py`);
   - `branch_created` committed and `restored-checkpoint.json` written as today (chapter 01 gives the commit its base;
     chapter 05 adds `lineage` to the manifest; neither touches the copy).
5. Publication, cleanup and the returned `FootprintStore(destination)` as today.

`copy_event_prefix(parent, cutoff, references)`, on the child's connection:
1. `ATTACH DATABASE ? AS parent` with `f"{(parent.run_dir / 'footprints.sqlite').as_uri()}?mode=ro"`, outside any
   transaction. URI filenames in `ATTACH` need a connection opened with URI processing; `FootprintStore.__init__`
   therefore opens with `uri=True` (below) ([R] `01_wal_probe.txt`: a `file:` URI on a plain connection is "unable to
   open database").
2. One transaction, `with self.connection:`:
   - after chapter 03: `INSERT INTO main.parts SELECT sha256, body FROM parent.parts` (chapter 03's statement, whole
     table, first);
   - for each `reference` of `references`, in order: `INSERT OR IGNORE INTO artifacts VALUES(?,?)` with
     `(reference.sha256, canonical_json(reference.model_dump()).decode())`, the statement and first-wins order of
     today's `_insert_event` (`store.py:170-173`) over the copied events;
   - `INSERT INTO events SELECT event_seq,event_id,body FROM parent.events WHERE event_seq<=? ORDER BY event_seq`.
     The trigger checks every row ([R] `04_attach_probe.txt`).
   Any exception: `logging.exception("Branch event copy failed; the child keeps no copied event")`, the transaction is
   rolled back by the context manager, and the exception propagates.
3. `DETACH DATABASE parent` in a `finally`, after the commit or rollback. A `DETACH` inside a transaction that read the
   parent fails with `database parent is locked` ([R] `04_attach_probe.txt`).
4. The prefix check: if `self.event_prefix_digest(cutoff) != parent.event_prefix_digest(cutoff)` it logs ERROR
   `Branch event copy disagrees with the parent prefix` and raises
   `IntegrityError("Branch event copy disagrees with the parent prefix")`; `restore_branch` then removes the stage, as
   for any failure.

The parent is attached read-only and only read; its file is unchanged (the existing test
`test_complete_checkpoint_branch_preserves_parent_and_exact_workspace` compares every parent file). A transaction across
attached files is atomic per file; only the child is written, so nothing else is needed
(https://www.sqlite.org/lang_attach.html) [W]. The parent may stay open read-write in the same process
(`runtime.py:242-251` holds it; [R] `04_attach_probe.txt`) or be a live run. A live parent's commits wait for the copy
statement, which holds the parent's shared lock for its whole run: 16.4–30.4 s for the 30-day prefix of 1.95 GB of
events ([R] `opt-history/08b_restore_phases.txt`), growing with the prefix bytes. After chapter 03 the events table
holds about 0.8 GB at day 30 and 2.8 GB at day 90 (proposal), so the statement stays well inside the 120 s busy
timeout. Resumes start from stopped runs; the parent's prefix digest and the re-validation pass are paged and hold the
lock only per page.

Why the resumed run reads exactly what it read before:
- Today the child's rows are `(event.event_seq, event.event_id, canonical_json(event.model_dump()))` of each decoded
  parent event; now they are the parent's own rows. The columns equal the body's fields (checked row by row in step 3).
  The bodies are equal whenever the canonical form round-trips: [R] the events-table SHA-256, the artifacts digest,
  the `branch_created` tail, `source_run_ids` and the copied assets of the 30-day restore all equal main's
  (`opt-history/08c_restore_sqlvalidate.txt` against `08_restore_current.txt`). Where they could differ, the new child
  holds the bytes the parent run itself wrote and read, which is what an uninterrupted run reads.
- After chapter 03 observation bodies carry part references; they are copied as they are, together with the parts, so
  every read through `_decode` rehydrates the same observation.

### `fail_run`

`LifeEngine.fail_run` (`engine.py:161-171`) replaces `read_events(...)` and `any(...)` (`:165-167`) with
`if self.context.store.has_event("run_failed"): return`.
- `has_event(event_type)` runs `SELECT 1 FROM events WHERE json_extract(body,'$.event_type')=? LIMIT 1`. The plan is
  `SCAN events USING COVERING INDEX events_agent_type` (no body read): 0.02 s at day 30 [R]. The review's choice: no
  `agent_id` term, so the duplicate check does not depend on producers leaving `agent_id` null.
- The same yes/no comes from the same committed events, and the same `run_failed` is appended (chapter 01 owns that
  line). A failure being recorded no longer needs memory that grows with the run.

### Counterfactual validation

`CounterfactualRuntime._validate_counterfactual` (`counterfactual_runtime.py:68-90`) and
`_validate_counterfactual_sources` (`:92-101`) read only the run's own events and each source by identity:
1. The manifest check, unchanged text (`Counterfactual branch manifest is invalid`).
2. `start = store.first_own_event_seq()`: walking back from `last_event_seq` by primary key
   (`SELECT json_extract(body,'$.run_id') FROM events WHERE event_seq=?`) while the run id equals the manifest's;
   it returns the first own sequence (`last_event_seq + 1` when there is none). For a legal call the own events are
   `branch_created` and nothing else.
3. `own = [event for event in store.iter_events(store.last_event_seq, start - 1) if event.event_type in
   ("environment_event", "decision_started")]`.
4. `branch = _current_branch_event(own, manifest)` (`world_ops.py:82-92`, unchanged). Its filter requires
   `run_id == manifest["run_id"]`, which only own events carry, so the result equals today's over all events.
   `None` raises `ProtocolError("Counterfactual branch creation evidence is invalid")` as today.
5. `round_open`, or any event of `own` after `branch` that is `decision_started` or an `environment_event` of kind
   `counterfactual_intervention`, raises today's
   `ProtocolError("Counterfactual intervention must precede the first decision and occur once")`. Every event after the
   branch event is an own event, and the predicate matches only those two types, so the set checked is today's.
6. `_validate_counterfactual_sources(intervention, branch)`: an unknown target agent raises today's
   `ProtocolError("Counterfactual branch or target agent is invalid")`; then for each source id,
   `event = store.find_event(event_id)` (unique index); `None`, `event.event_seq >= branch.event_seq`,
   `event.visibility != "agent"` or `event.agent_id not in {None, intervention.agent_id}` raises today's
   `ProtocolError("Counterfactual source is not visible to the target agent")`. Today's dict holds exactly the events
   before the branch event, keyed by their unique id, so the accepted sources are the same; an unknown id is still a
   `ProtocolError`, not the `IntegrityError` of `read_event`.
Every `ProtocolError` raise in these two functions is preceded by `logging.error` with the same text (new log lines,
AGENTS.md).

### Experience window

`read_recent_experience_sources` (`store.py:270-283`) keeps its signature, checks and decoding; only the SQL changes, to
two index-ordered subqueries joined with `UNION ALL` (exact text in "Interfaces and files"). `activity_progress` events
and `goods_received` environment events are disjoint, so the top `limit` rows of the union by `event_seq DESC` equal the
top `limit` rows of today's `OR`; `event_seq` is unique, so there are no ties. [R] 3,008 of 3,008 observation windows
recomputed with it equal the recorded `data.verified_experiences`, the prompt input (`opt-history/09`). No index is
added (review revision).

### plan_day inputs

`plan_inputs` (`day_plan_inputs.py:60-82`) reads the agent's four event types once and passes them down:
`history = _agent_history(context.store, agent_id)` returns `_AgentHistory(progress, environment, results, requests)`,
the results of `read_agent_events` for `activity_progress`, `environment_event`, `action_result` and
`action_requested`.
- `_experience_sentences` uses `history.progress` and `history.environment` (today `:137-138`);
- `_result_sentences` uses `history.results`, `history.environment`, `history.requests`, `history.progress` (today
  `:155-156`, `:161`, `:176`);
- `_conclusion_records` uses `history.results` and `history.requests` (today `:201`, `:205`);
- `_previous_goals` iterates `(("environment_event", history.environment), ("action_result", history.results),
  ("activity_progress", history.progress))` (today the loop at `:253-254`).
No window bound is applied anywhere. `plan_inputs` commits nothing and runs inside the single writer, so every
`read_agent_events` call of today returned the same list; the consumers build new lists and never assign to an event or
its `data` (`settled_text.py:94-110`, `:153-190` mutate only local dicts; `project_verified_experiences` only indexes),
so sharing one list per type gives identical inputs and an identical plan_day prompt.

### Storefront acceptance lookup

`StorefrontRuntime._accepted_cause` (`storefront_runtime.py:93-101`) reads
`store.read_agent_events("action_result", activity.agent_id, store.last_event_seq_before(activity.started_at))`.
The acceptance result is committed in the round that created the activity, at `occurred_at == activity.started_at`
(`storefront_runtime.py:146`, `activities.py:70`), and every later result of the action comes after it; event time is
monotone in `event_seq`, so every candidate has `event_seq > last_event_seq_before(started_at)`, and a result of the
origin action cannot precede its acceptance. The uniqueness check and its text are unchanged. [R] 162 of 162 purchase
completions give the same cause (`opt-history/10_storefront.txt`).

### Session opening read

`session_bounds._opening_result_seq` (`session_bounds.py:31-42`) calls
`store.first_action_result_seq(session.agent_id, session.opened_by_action_id, after)` with today's
`after = store.last_event_seq_before(session.started_at)`; `None` keeps today's log line and
`IntegrityError("Session opening action has no committed result")`.
- `first_action_result_seq` keeps `read_agent_events`' boundary check and text (`Agent event boundary is outside the
  committed sequence`, with an ERROR log) and runs one query on the `events_agent_type` index that stops at the first
  match (text in "Interfaces and files").
- The first matching `action_result` in `event_seq` order is today's `seqs[0]`. SQL `json_extract(body,'$.action_id')=?`
  with a `str` parameter is text equality, the same as Python `==` on the decoded `str`; a JSON `null` never matches,
  as `None == "x"` is false. [R] 169 of 169 session lower bounds equal and the day-30 observation byte-identical
  (`opt-critical/proof_session_lower.py`). Callers are unchanged: `memory_layer.py:188`, `progress_layer.py:58`.

### Live-run reader safety

**Busy timeout.** `FootprintStore.__init__` opens
`sqlite3.connect(database.as_uri(), uri=True, timeout=BUSY_TIMEOUT_SECONDS)` with `BUSY_TIMEOUT_SECONDS = 120`. In the
rollback journal a commit needs the exclusive lock; SQLite's busy handler now retries for up to 120 s while a reader
finishes its statement, instead of raising `database is locked` after 5 s. Durability is unchanged
(`PRAGMA synchronous=FULL`, `store.py:52`). The journal mode is not changed and `close` is not changed (the proposal
decided against WAL; chapter 01's ownership table already records this). Every `FootprintStore` connection gets the
same constant; readers only wait while a commit holds its lock. A commit therefore waits as long as the longest single
read statement another process runs: after this chapter that is `worldmock status`' usage sums (28–38 s at day 30 on
today's bodies, about a fifth of that after chapter 03). Every other read statement of this chapter reads at most
`EVENT_PAGE_SIZE` events, or only the expression index, or one row by key; the exceptions are `render_day.py`'s two
per-type reads (one agent's `activity_progress` bodies, 15.7 MB, and the `action_result` bodies, 0.23 s warm and
1.97 s cold at day 30, `opt-review-history/02_tools_sql.txt` and the review's notes).
A statement longer than 120 s from a tool outside this chapter (for example the `sqlite3` shell) still fails the
commit, as any lock wait beyond the timeout does. The URI form names the same file
(`Path.as_uri()` percent-encodes the absolute path, and SQLite decodes it; `status.py:33`, `inspect_run.py:72` and
`render_day.py:43` already open run databases this way); it is needed because `ATTACH` accepts a `file:` URI only on a
connection opened with URI processing.

**`worldmock status`** (`status.py:28-56`), output unchanged except the wall-clock edge named below:
- no `BEGIN` (`:35` deleted): each statement is its own short read. On a live run the values may come from consecutive
  commits; on a finished run they are today's.
- event counts: `event_type_counts(connection)`, one `SELECT count(*),min(event_seq) FROM events WHERE
  json_extract(body,'$.event_type')=?` per member of `EventType`, in sorted name order, types with zero events left out.
  Today's `GROUP BY` returns the same pairs in ascending name order (the store refuses any other event type and every
  body has one); [R] equal in content and order on the 30-day copy, 110.1 s → 0.17 s (`02_status_reads.txt`).
- usage sums (`_observed_usage`): the same columns, `GROUP BY` and `ORDER BY`, over
  `WHERE event_seq IN (SELECT event_seq FROM events WHERE json_extract(body,'$.event_type')='model_response') AND
  json_extract(body,'$.data.phase')='response'`: the same row set found through the covering index; [R] equal, 86.5 s →
  28–38 s as one statement on today's bodies (`02`, `05_usage_warm.txt`).
- wall time (`_wall_time`): the `recorded_at` of the first and the last event by primary key, read with chapter 05's
  two statements `_FIRST_EVENT_SQL` and `_LAST_EVENT_SQL` (text in "Interfaces and files"; chapter 05 later also reads
  the last event's type from `_LAST_EVENT_SQL` and moves both reads into its `_liveness_view`). Equal to today's
  `min`/`max` whenever `recorded_at` never decreases along `event_seq`, which holds for all 46,142 events of the run
  [R] (`opt-review-history/06_recorded_at_monotonic.txt`, `02_status_reads.txt`): the store stamps
  `datetime.now(timezone.utc)` in sequence order (`store.py:141`) and a branch's copied rows keep their earlier stamps.
  The one difference: if the machine's wall clock stepped back during a run, the fields show the first and the last
  event's stamps, which is what `started_at` and `last_event_at` name, where today they show the extremes. No model
  input, settled fact or exported byte reads these fields. With no event the first read returns no row and the result
  is today's three `None`.
- the world row, the five checkpoint ids (chapter 02 changes `:39-40`) and the per-person reads (`:101-118`, indexed,
  3 rows each) are unchanged.

**`scripts/inspect_run.py`**, output unchanged byte for byte:
- `_rows` (`:77-83`) and `_select` (`:240-247`) read through one new helper, `_paged(conn, sql, args)`: it reads
  `last = SELECT COALESCE(MAX(event_seq),0) FROM events` once, then runs the caller's statement once per window
  `(after, after + EVENT_PAGE_SIZE]` for `after` in `range(0, last, EVENT_PAGE_SIZE)`, each statement starting
  `… WHERE event_seq>? AND event_seq<=? AND (<filter>) ORDER BY event_seq` and fetched whole. Every statement therefore
  reads at most `EVENT_PAGE_SIZE` events whatever the filter; a page bounded by a match count instead would scan to the
  end of the history in one statement when the filter is sparse. The windows are disjoint, ascending and cover
  `(0, last]`, and each page is in `event_seq` order, so the concatenation is today's single ordered statement over the
  events committed when the command started (today's statement reads the snapshot at its own start).
- `display_context` (`:96-101`) filters `action_result` through the index:
  `event_seq IN (SELECT event_seq FROM events WHERE json_extract(body,'$.event_type')='action_result')`; [R] the same
  4,549 bodies, 11.9 s → 0.23 s as one statement (`opt-review-history/02_tools_sql.txt`). Inside the windows the
  subquery runs once per statement (a covering-index scan without body reads; not measured paged).
- `cmd_summary` (`:104-116`): the total is `SELECT count(*) FROM events`; the per-type lines come from
  `event_type_counts` sorted by `(-count, first event_seq)`. Today's dict is filled in first-occurrence order and sorted
  stably by `-count`, which is that order; [R] identical stdout (`opt-review-history/02_tools_sql.txt`).
- `cmd_money` (`:190-204`): the filter becomes `json_extract(body,'$.resource_changes')!='{}'`. SQLite renders an empty
  object as `{}` and a non-empty one never as `{}`; today's `IS NOT NULL` matched every event and the loop skipped the
  empty ones, so the printed lines and balances are identical; [R] stdout identical, 24.2 s / 4.5 GB → 4.2 s / 7 MB
  (one statement; the paged form evaluates the same filter on the same rows). The filtered rows are fetched whole
  before printing, so a paused pager holds no lock.
- Chapter 03's `_rehydrated` for `event` and `events` is unaffected (it works on the rows `_rows` returns).

**`scripts/render_day.py`**: `_result_bodies` (`:55-58`) selects through the same index form, held in a module
constant `_RESULT_BODIES_SQL` so a test can `EXPLAIN` it; [R] the same 4,549 bodies (digest `150e990b84b2`) in 0.23 s
as one statement. `_progress_bodies` is already indexed by agent and type (one agent's progress bodies, 15.7 MB at day
30 for the run's one agent). `_markdown` (`:140-147`) is renamed `day_markdown` because the export now calls it;
`render_day_markdown` and the CLI keep their behaviour (the CLI still renders a day from a whole database, which is what
it is for; its file equals the export's only when the export is cut at the last event, and the design text says so).

### Failure handling

| Where | Condition | Log (ERROR) | Exception |
| --- | --- | --- | --- |
| `iter_events` | range outside `0 <= after <= cutoff <= last_event_seq` | `Event range (%d, %d] is outside the committed sequence` | `IntegrityError("Event cutoff is outside the committed sequence")` |
| `_decoded_row` | a row's `event_seq`/`event_id` differ from its body | `Event row %d disagrees with its body` | `IntegrityError("Event row disagrees with its body")` |
| `export_run` | `cutoff == 0` | `Cannot export an empty event range` | `IntegrityError("Cannot export an empty event range")` |
| `export_run` | anything inside the stage | `Footprint export failed before publication` (exception log, today's) | propagates; stage removed |
| `_authenticated_events` | path, digest, row count | the exception text (new log lines; `_read_artifact` keeps logging nothing) | `IntegrityError("Export artifact path is invalid")` / `("Export artifact hash is invalid")` / `("Export artifact row count is invalid")` |
| `_read_events` | second digest differs | `Export events changed between the two reads` | `IntegrityError("Export artifact hash is invalid")` |
| `copy_event_prefix` | any SQL failure in the copy transaction | `Branch event copy failed; the child keeps no copied event` (exception log) | propagates; stage removed by `restore_branch` |
| `copy_event_prefix` | child prefix digest differs from the parent's | `Branch event copy disagrees with the parent prefix` | `IntegrityError("Branch event copy disagrees with the parent prefix")` |
| `first_action_result_seq` | `after` outside `0..last_event_seq` | `Agent event boundary %d is outside the committed sequence` | `IntegrityError("Agent event boundary is outside the committed sequence")` |
| counterfactual | today's four cases | the exception text | today's `ProtocolError`s |

### What is deleted

- `FootprintStore.read_events` (`store.py:285-290`).
- The whole-history reads at `export.py:46`, `checkpoint.py:101`, `engine.py:165`, `counterfactual_runtime.py:77`.
- `render_day_markdown(store.connection, …)` per day in the export (`export.py:124-135`, the loop body).
- The per-event insert loop of the branch (`checkpoint.py:133-135`); after chapter 03, `FootprintStore.copy_history`,
  `observation_parts.copy_parts` (its only caller is `copy_history`), `copy_parts` in `store.py`'s import from
  `observation_parts`, `'copy_parts'` in the `OWNERS` tuple for `footprints/observation_parts.py`, and chapter 03's two
  registered mutations on `copy_history` (`branch_skips_parts`, `branch_rewrites_observations_in_full`; their symbol no
  longer exists, so the runner would refuse them). This chapter's `branch_skips_parts` below replaces the first; the
  second has no counterpart, because the SQL copy never re-encodes a body (`test_branch_copies_parent_rows_and_artifact_rows_byte_for_byte`
  and chapter 03's `test_branch_copies_the_parts_and_the_stored_rows` compare the stored rows).
- `validate_activity_prefix(events)` at `validation.py:42` (moved into pass 2); the third element of
  `validate_export`'s result.
- `BEGIN` in `read_status` (`status.py:35`); the whole-body `GROUP BY` and `min`/`max` statements (`:37-38`,
  `:133-135`).
- The eleven `read_agent_events` calls inside the plan-input helpers; the `context`/`agent_id` parameters of
  `_conclusion_records` and `_previous_goals`.
- The private name `render_day._markdown` (renamed).

## Interfaces and files

Line numbers are today's, in `D:\code\WorldMock\.claude\worktrees\lrc` (main `0bd2de65`). Every added function has a
Chinese Doxygen `@brief`; every changed or added function stays within 50 lines and 5 parameters, every line within
120 characters; every new exception branch logs an ERROR first. Code is written against the tree after chapters 01–03
(`self._decode`, the `parts` table).

### `src/worldmock/footprints/store.py`

Imports: add `from collections.abc import Iterator, Sequence`.

Constants after `SCHEMA` (`:20-38`):

```python
#: 分页读取事件时每页的条数：每页一个读语句、读完即放开读锁；观察最大约 0.5 MB（30 天运行），一页至多约 120 MB 正文。
EVENT_PAGE_SIZE = 256
#: 足迹库连接遇到读者持锁时最多等待的秒数（docs/design/10-runtime.md「运行与恢复」）；超过才报 database is locked。
BUSY_TIMEOUT_SECONDS = 120
```

`__init__` (`:44-55`): `:51` becomes

```python
        self.connection = sqlite3.connect(database.as_uri(), uri=True, timeout=BUSY_TIMEOUT_SECONDS)
```

`read_event` (`:237-246`) keeps its signature, docstring, log line and exception; its body becomes
`event = self.find_event(event_id)`, then the existing `None` branch, then `return event`.

New methods, placed after `read_agent_events` (`:248-256`):

```python
    def iter_events(self, cutoff: int, after: int = 0) -> Iterator[Event]:
        """@brief 按事件序号读 (after, cutoff] 的事件：调用时先核对区间；之后每页一个短读语句，逐条解码并核对行与正文一致。

        事件只追加、不修改，分页读到的就是这一段已提交前缀；两页之间不持有读锁。
        :raises IntegrityError: 区间不在已提交序号之内。
        """
        if not 0 <= after <= cutoff <= self.last_event_seq:
            logging.error("Event range (%d, %d] is outside the committed sequence", after, cutoff)
            raise IntegrityError("Event cutoff is outside the committed sequence")
        return (self._decoded_row(*row) for row in self._event_rows(after, cutoff))

    def _event_rows(self, after: int, cutoff: int) -> Iterator[tuple[int, str, str]]:
        """@brief 按序号分页取 (after, cutoff] 的原始行（序号、身份、正文），每页一个语句并一次取完。"""
        while after < cutoff:
            stop = min(after + EVENT_PAGE_SIZE, cutoff)
            yield from self.connection.execute(
                "SELECT event_seq,event_id,body FROM events WHERE event_seq>? AND event_seq<=? ORDER BY event_seq",
                (after, stop)).fetchall()
            after = stop

    def _decoded_row(self, sequence: int, identity: str, body: str) -> Event:
        """@brief 用唯一的行解码器解码一行，并核对行的序号与身份等于正文里的值。"""
        event = self._decode(body)
        if event.event_seq != sequence or event.event_id != identity:
            logging.error("Event row %d disagrees with its body", sequence)
            raise IntegrityError("Event row disagrees with its body")
        return event

    def event_prefix_digest(self, cutoff: int) -> tuple[int, str]:
        """@brief 前缀 [1, cutoff] 原始行的条数与 SHA-256（逐行「序号\\t身份\\t正文\\n」的 UTF-8），分页读、不解码。"""
        digest, rows = hashlib.sha256(), 0
        for sequence, identity, body in self._event_rows(0, cutoff):
            digest.update(f"{sequence}\t{identity}\t{body}\n".encode())
            rows += 1
        return rows, digest.hexdigest()

    def copy_event_prefix(self, parent: FootprintStore, cutoff: int, references: Sequence[AssetRef]) -> None:
        """@brief 新分支库在一个事务里按存储字节复制父库前缀 [1, cutoff] 的事件、观察分件与素材索引行。

        父库以只读 URI 附加；素材索引按事件顺序先到先留，与逐条插入相同；提交后核对本库与父库前缀的条数和摘要。
        :raises IntegrityError: 复制结果与父库前缀不一致。
        """
        source = f"{(parent.run_dir / 'footprints.sqlite').as_uri()}?mode=ro"
        self.connection.execute("ATTACH DATABASE ? AS parent", (source,))
        try:
            with self.connection:
                self.connection.execute("INSERT INTO main.parts SELECT sha256, body FROM parent.parts")
                for reference in references:
                    self.connection.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (
                        reference.sha256, canonical_json(reference.model_dump()).decode(),
                    ))
                self.connection.execute("INSERT INTO events SELECT event_seq,event_id,body FROM parent.events "
                                        "WHERE event_seq<=? ORDER BY event_seq", (cutoff,))
        except Exception:
            logging.exception("Branch event copy failed; the child keeps no copied event")
            raise
        finally:
            self.connection.execute("DETACH DATABASE parent")
        if self.event_prefix_digest(cutoff) != parent.event_prefix_digest(cutoff):
            logging.error("Branch event copy disagrees with the parent prefix")
            raise IntegrityError("Branch event copy disagrees with the parent prefix")

    def has_event(self, event_type: EventType) -> bool:
        """@brief 库里是否已有该类型的事件：只走类型表达式索引（不读正文），找到第一条即停。"""
        row = self.connection.execute(
            "SELECT 1 FROM events WHERE json_extract(body,'$.event_type')=? LIMIT 1", (event_type,)).fetchone()
        return row is not None

    def find_event(self, event_id: str) -> Event | None:
        """@brief 按事件身份读取一条事件（events.event_id 有唯一索引）；没有时返回 None，由调用方决定是否为错误。"""
        row = self.connection.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
        return self._decode(row[0]) if row is not None else None

    def first_own_event_seq(self) -> int:
        """@brief 本运行自己写下的第一个事件序号；没有时为 last_event_seq+1。

        分支库里父前缀的事件带祖先运行身份，本运行的事件是连续的后缀：从末事件按主键向前走到运行身份不同为止。
        """
        sequence = self.last_event_seq
        while sequence > 0 and self.connection.execute(
                "SELECT json_extract(body,'$.run_id') FROM events WHERE event_seq=?",
                (sequence,)).fetchone()[0] == self.manifest["run_id"]:
            sequence -= 1
        return sequence + 1

    def first_action_result_seq(self, agent_id: int, action_id: str, after: int) -> int | None:
        """@brief 人物在 after 之后该动作的第一条 action_result 的序号；没有时为 None。沿人物与类型索引读，找到即停。

        :raises IntegrityError: after 不在已提交序号之内。
        """
        if after < 0 or after > self.last_event_seq:
            logging.error("Agent event boundary %d is outside the committed sequence", after)
            raise IntegrityError("Agent event boundary is outside the committed sequence")
        row = self.connection.execute(
            "SELECT event_seq FROM events WHERE json_extract(body,'$.agent_id')=? "
            "AND json_extract(body,'$.event_type')='action_result' AND event_seq>? "
            "AND json_extract(body,'$.action_id')=? ORDER BY event_seq LIMIT 1",
            (agent_id, after, action_id)).fetchone()
        return row[0] if row is not None else None
```

Notes on these methods:
- On main alone (without chapter 03), drop the `parts` statement and read `_decode` as `Event.model_validate_json`.
- `copy_event_prefix` is the mutation target of three entries; the snippets in "Source mutations" match it.

`read_recent_experience_sources` (`:270-283`): the statement becomes exactly

```python
        rows = self.connection.execute("""
            SELECT body FROM (
              SELECT event_seq, body FROM (SELECT event_seq, body FROM events
                WHERE json_extract(body,'$.agent_id')=? AND json_extract(body,'$.event_type')='activity_progress'
                AND event_seq<? ORDER BY event_seq DESC LIMIT ?)
              UNION ALL
              SELECT event_seq, body FROM (SELECT event_seq, body FROM events
                WHERE json_extract(body,'$.agent_id')=? AND json_extract(body,'$.event_type')='environment_event'
                AND json_extract(body,'$.data.kind')='goods_received' AND event_seq<?
                ORDER BY event_seq DESC LIMIT ?)
            ) ORDER BY event_seq DESC LIMIT ?
        """, (agent_id, before_seq, limit, agent_id, before_seq, limit, limit))
```

The docstring becomes `@brief 按倒序有限读取本人活动进度与收货候选事件：两类各沿人物与类型索引倒序取前 limit 条，再合并取前 limit 条，不排序全部历史。`
The decode line stays (chapter 03's `_decode`). Each source line is at most 120 characters.

Deleted: `read_events` (`:285-290`). `store.py` grows by about 90 lines.

**Ownership with chapters 01–03 and 05.** This chapter owns, in `store.py`: the two constants, the connect line in
`__init__`, `read_event`'s body, `read_recent_experience_sources`' SQL, the deletion of `read_events` and of chapter
03's `copy_history` (with `copy_parts` in the import line), and the nine new methods above. Chapter 01 owns the commit
path, `_last_event_time`, `checkpoint` and the `_committed` attribute in `__init__`; chapter 02 the stored checkpoint
form and `get_checkpoint`'s reading of it; chapter 03 `_decode`, the `parts` schema, `_index_artifacts`,
`append_observation` and the decoder call in every reader; chapter 05 `read_last_event` and `read_terminal_event`,
placed after `read_event` (this chapter's methods go after `read_agent_events`). The connect line and the `_committed`
line in `__init__` are adjacent; whichever lands second rebases.

### `src/worldmock/footprints/post_run.py` (new)

```python
"""@brief 事后读取视图：导出、导出认证与行为评估在内存里只保留大事件里事后会读的部分（docs/design/05-recording-engine.md「完整导出认证」）。

events.jsonl 与运行库里的事件始终完整；视图只在内存里去掉下表的庞大载荷，表外的键（含以后新增的键）原样保留。
"""

from __future__ import annotations

from typing import Any

from worldmock.footprints.models import Event

#: 观察在视图里去掉的 data 键；其余键原样保留。
DROPPED_PAYLOADS = {
    "observation": frozenset({"activity_bindings", "persona_views", "tool_layer", "interaction_bindings",
                              "catalog_bindings", "memory_layer", "goal_bindings", "locations"}),
}
#: 请求消息里去掉的提示与思考原文。
_MESSAGE_TEXT_KEYS = frozenset({"content", "reasoning_content"})
#: 工具定义的 function 里去掉的说明与参数结构。
_TOOL_SCHEMA_KEYS = frozenset({"description", "parameters"})
#: 视图会缩小的事件类型；其余类型原样返回同一个对象。
REDUCED_EVENT_TYPES = frozenset({"observation", "model_request", "model_response"})


def post_run_view(event: Event) -> Event:
    """@brief 事件的事后读取视图：大事件换成缩小的 data，其余事件原样返回。"""
    if event.event_type not in REDUCED_EVENT_TYPES:
        return event
    return event.model_copy(update={"data": _post_run_data(event.event_type, event.data)})


def post_run_row(row: dict) -> dict:
    """@brief 已严格校验的事件行（json.loads 的结果）的事后读取视图，与 post_run_view 用同一规则。"""
    if row.get("event_type") not in REDUCED_EVENT_TYPES or not isinstance(row.get("data"), dict):
        return row
    return {**row, "data": _post_run_data(row["event_type"], row["data"])}


def _post_run_data(event_type: str, data: dict) -> dict:
    """@brief 按事件类型缩小 data：观察去掉庞大绑定与提示层，请求只留提纲，响应去掉候选的 message；任何形状都不抛错。"""
    kept = {key: value for key, value in data.items() if key not in DROPPED_PAYLOADS.get(event_type, ())}
    if event_type == "observation" and "errand_item_bindings" in kept:
        kept["errand_item_bindings"] = _reachable_item_bindings(data)
    if event_type != "observation" and isinstance(kept.get("request"), dict):
        kept["request"] = _request_outline(kept["request"])
    if event_type == "model_response" and isinstance(kept.get("response"), dict):
        kept["response"] = _without_messages(kept["response"])
    return kept


def _reachable_item_bindings(data: dict) -> Any:
    """@brief errand_item_bindings 里 errand_bindings 查得到的行；不是列表时原样返回，形状不规整的行一律保留。"""
    rows = data["errand_item_bindings"]
    if not isinstance(rows, list):
        return rows
    bindings = data.get("errand_bindings")
    used = ({row["product_id"] for row in bindings if isinstance(row, dict) and isinstance(row.get("product_id"), str)}
            if isinstance(bindings, list) else set())
    return [row for row in rows if not _unreferenced(row, used)]


def _unreferenced(row: Any, used: set[str]) -> bool:
    """@brief 一行商品绑定形状规整（对象、字符串 product_id、带 name）且没有被任何 errand_bindings 行引用。"""
    return (isinstance(row, dict) and isinstance(row.get("product_id"), str) and "name" in row
            and row["product_id"] not in used)


def _request_outline(request: dict) -> dict:
    """@brief 请求提纲：消息去掉 content 与 reasoning_content，工具的 function 去掉 description 与 parameters，其余原样。"""
    outline = dict(request)
    if isinstance(request.get("messages"), list):
        outline["messages"] = [_without(message, _MESSAGE_TEXT_KEYS) for message in request["messages"]]
    if isinstance(request.get("tools"), list):
        outline["tools"] = [_tool_outline(tool) for tool in request["tools"]]
    return outline


def _tool_outline(tool: Any) -> Any:
    """@brief 工具定义的提纲：function 是对象时去掉说明与参数结构，其余形状原样。"""
    if not isinstance(tool, dict) or not isinstance(tool.get("function"), dict):
        return tool
    return {**tool, "function": _without(tool["function"], _TOOL_SCHEMA_KEYS)}


def _without_messages(response: dict) -> dict:
    """@brief 提供商响应去掉每个候选的 message；choices 不是列表时原样返回。"""
    if not isinstance(response.get("choices"), list):
        return response
    return {**response, "choices": [_without(choice, frozenset({"message"})) for choice in response["choices"]]}


def _without(value: Any, keys: frozenset[str]) -> Any:
    """@brief 对象去掉给定的键（保持其余键的次序）；不是对象时原样返回。"""
    return {key: item for key, item in value.items() if key not in keys} if isinstance(value, dict) else value
```

The table in "The post-run view" is this module: `DROPPED_PAYLOADS` (the eight observation keys) plus the three
shape rules. `DROPPED_PAYLOADS`, `_unreferenced`, `_request_outline` and `_without_messages` are mutation targets; keep
the lines as written.

### `src/worldmock/footprints/export.py`

Imports: add `import os`, `from collections.abc import Iterator`, `from worldmock.footprints.post_run import
post_run_view`, `from worldmock.footprints.timeline_labels import timeline_sources` (next to `window_days`, `:6`).

`export_run` (`:44-62`), same signature; docstring
`@brief 一遍分页读取截止前缀：逐条校验、照旧编码写入 events.jsonl 并哈希，内存里只留事后读取视图；全部工件与素材验证成功后原子发布新目录，不读截止点之后的事件。`

```python
def export_run(store: FootprintStore, cutoff: int, destination: Path) -> dict:
    events = store.iter_events(cutoff)
    if cutoff == 0:
        logging.error("Cannot export an empty event range")
        raise IntegrityError("Cannot export an empty event range")
    destination = Path(destination).resolve()
    if destination.exists():
        raise IntegrityError("Export destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
    try:
        kept, events_artifact = _write_events(events, stage / "events.jsonl")
        manifest = _write_export(store, kept, events_artifact, stage)
        atomic_write(stage / "manifest.json", canonical_json(manifest))
        stage.rename(destination)
    except Exception:
        logging.exception("Footprint export failed before publication")
        shutil.rmtree(stage)
        raise
    return manifest
```

New `_write_events`:

```python
def _write_events(events: Iterator[Event], path: Path) -> tuple[list[Event], dict]:
    """@brief 逐条写入规范 JSONL（与 write_jsonl 同一编码）并边写边哈希；返回事后读取视图与工件身份。"""
    digest, rows, kept = hashlib.sha256(), 0, []
    with path.open("xb") as stream:
        for event in events:
            line = canonical_json(event.model_dump()) + b"\n"
            stream.write(line)
            digest.update(line)
            rows += 1
            kept.append(post_run_view(event))
        stream.flush()
        os.fsync(stream.fileno())
    return kept, {"path": path.name, "rows": rows, "sha256": digest.hexdigest()}
```

`_write_export` (`:65-105`) becomes `_write_export(store, events, events_artifact: dict, stage)`; docstring adds
「events 是截止前缀的事后读取视图」. Changes: `"events": events_artifact,` replaces `:73`; `:104` becomes
`manifest["timeline"] = _write_timeline_projections(store, events, stage, manifest["daily_coverage"], names)`. Every
other line is unchanged.

`_write_timeline_projections` (`:117-136`) becomes `_write_timeline_projections(store, events, stage, days, names)`;
docstring (two lines, each within 120 characters):
`@brief 由截止前缀的同一份事件一次取出进度与订单，逐人逐日投影 timeline/<agent>-<date>.md 并哈希绑定。` and
`投影调用 scripts/render_day.py 的 day_rows 与 day_markdown（单一实现，不重复）；截止之后的事件不进时间线。`

```python
    module = timeline_labels._load_render_day()
    progress, orders = timeline_sources(events)
    zone = store.manifest["window"]["timezone"]
    records = []
    for agent_id in sorted(int(item) for item in store.manifest["agent_ids"]):
        bodies = [body for body in progress if body.get("agent_id") == agent_id]
        for day in days:
            moment = date.fromisoformat(day)
            markdown = module.day_markdown(agent_id, moment, module.day_rows(bodies, orders, names, moment, zone))
            payload = markdown.encode("utf-8")
            relative = f"timeline/{agent_id}-{day}.md"
            atomic_write(stage / relative, payload)
            records.append({"agent_id": agent_id, "date": day, "path": relative,
                            "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    return records
```

`write_jsonl`, `_verified_experience_rows`, `_export_identity`, `reconstruct_activities` and the constants are
unchanged.

### `src/worldmock/footprints/timeline_labels.py`

New, after `timeline_label_report` (`:38-64`):

```python
def timeline_sources(events: list[Event]) -> tuple[list[dict], dict]:
    """@brief 时间线的两份输入：全部 activity_progress 正文（按事件序号）与 action_result 给出的订单记录。

    导出的时间线文件与质量报告的标签检查从同一份已认证事件取，取的都是 model_dump(mode="json") 的正文。
    """
    bodies = [event.model_dump(mode="json") for event in events if event.event_type in _TIMELINE_EVENT_TYPES]
    progress = [body for body in bodies if body["event_type"] == "activity_progress"]
    return progress, orders_by_id([body for body in bodies if body["event_type"] == "action_result"])
```

`timeline_label_report` `:47-50` become `progress, orders = timeline_sources(events)` and
`agents = sorted({body["agent_id"] for body in progress if isinstance(body.get("agent_id"), int)})`; `:53-54` become
`agent_progress = [body for body in progress if body.get("agent_id") == agent_id]` and the `day_rows` call at `:56`
takes `agent_progress`. Its output is unchanged (the same bodies, filters and order). The module docstring's sentence
about the single renderer stays true.

### `scripts/render_day.py`

- New module constant after `_DATE_ARG` (`:37`):
  ```python
  #: 全库 action_result 正文（按 event_seq）：经类型表达式索引取序号，只读这些正文。
  _RESULT_BODIES_SQL = ("SELECT body FROM events WHERE event_seq IN (SELECT event_seq FROM events "
                        "WHERE json_extract(body,'$.event_type')='action_result') ORDER BY event_seq")
  ```
- `_result_bodies` (`:55-58`): `:57` is deleted and `:58` reads `conn.execute(_RESULT_BODIES_SQL)`; docstring
  `@brief 全库 action_result 事件正文（按 event_seq），订单记录的来源；经类型表达式索引取序号，只读这些正文。`
- `_markdown` (`:140-147`) renamed `day_markdown(agent_id: int, day: date, rows: list[dict]) -> str`, body and
  docstring unchanged; `render_day_markdown` (`:150-154`) calls `day_markdown`. The module docstring's sentence
  「day_rows 也接受已排序的事件正文，质量报告的时间线标签检查据此投影同一张时间线」 gains 「导出的时间线文件同样由 day_rows 与 day_markdown 写出」.

### `src/worldmock/footprints/validation.py`

Imports: add `from worldmock.footprints.post_run import post_run_row, post_run_view`.

`validate_export` (`:29-65`):

```python
def validate_export(directory: Path) -> tuple[dict, list[dict]]:
    """@brief 只有完整终态、检查点和所有素材均可信时才返回导出清单与活动行。

    events.jsonl 读两遍：第一遍只按字节核对摘要与行数，第二遍逐行做活动快照检查与严格校验，内存里只留事后读取视图，
    并确认两遍读到同一份字节；其余检查读同一份视图。事件条数取清单 artifacts.events.rows。
    """
    root = directory.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    _validate_identity(manifest)
    digest = _authenticated_events(root, manifest["artifacts"]["events"])
    activities = _read_artifact(root, manifest["artifacts"]["activities"], "activities.jsonl")
    experiences = _read_artifact(
        root, manifest["artifacts"]["verified_experiences"], "verified-experiences.jsonl")
    purchases = _read_artifact(root, manifest["artifacts"]["purchases"], "purchases.jsonl")
    app_usage = _read_artifact(root, manifest["artifacts"]["app_usage"], "app-usage.jsonl")
    stays = _read_artifact(root, manifest["artifacts"]["location_stays"], "location-stays.jsonl")
    indexed = _read_artifact(root, manifest["asset_index"], "asset-index.jsonl")
    events, records = _read_events(root / "events.jsonl", digest)
    checkpoint = _validate_checkpoint(manifest, records)
    ...
    return manifest, activities
```

The `...` stands for today's lines `:45-64`, unchanged and in the same order (they read `events` and `records`, now the
post-run rows and views).

New, after `_read_artifact` (`:114-125`):

```python
def _authenticated_events(root: Path, descriptor: dict) -> str:
    """@brief 第一遍：只按字节流式核对 events.jsonl 的路径、摘要与行数（行按 bytes.splitlines 切分），不解析任何一行。"""
    path = root / "events.jsonl"
    if descriptor["path"] != "events.jsonl" or path.is_symlink() or not path.is_file():
        logging.error("Export artifact path is invalid")
        raise IntegrityError("Export artifact path is invalid")
    digest, rows = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for chunk in stream:
            digest.update(chunk)
            rows += len(chunk.splitlines())
    if digest.hexdigest() != descriptor["sha256"]:
        logging.error("Export artifact hash is invalid")
        raise IntegrityError("Export artifact hash is invalid")
    if rows != descriptor["rows"]:
        logging.error("Export artifact row count is invalid")
        raise IntegrityError("Export artifact row count is invalid")
    return digest.hexdigest()


def _read_events(path: Path, digest: str) -> tuple[list[dict], list[Event]]:
    """@brief 第二遍：逐行解析、活动快照检查与严格校验，只留事后读取视图；读完再核对一次摘要。"""
    hasher, rows, records = hashlib.sha256(), [], []
    with path.open("rb") as stream:
        for chunk in stream:
            hasher.update(chunk)
            for line in chunk.splitlines():
                row = json.loads(line)
                validate_activity_prefix([row])
                record = Event.model_validate(row, strict=True)
                rows.append(post_run_row(row))
                records.append(post_run_view(record))
    if hasher.hexdigest() != digest:
        logging.error("Export events changed between the two reads")
        raise IntegrityError("Export artifact hash is invalid")
    return rows, records
```

`_read_artifact` is unchanged and still reads the six smaller artifacts.

### `src/worldmock/footprints/checkpoint.py`

Imports: add `from collections.abc import Sequence` and `from dataclasses import dataclass`.

New, before `restore_branch` (`:93`):

```python
@dataclass(frozen=True)
class _ParentPrefix:
    """@brief 父库检查点前缀逐条复核的结果。"""

    #: 前缀事件出现过的全部运行身份。
    run_ids: frozenset[str]
    #: 按事件序号、事件内次序排列的全部素材引用（可重复），每个都已读取并核对。
    references: tuple[AssetRef, ...]


def _validated_prefix(store: FootprintStore, cutoff: int) -> _ParentPrefix:
    """@brief 逐条解码校验父库前缀（分页、不留事件列表），读取核对每个素材引用，记下运行身份与引用。"""
    run_ids, references = set(), []
    for event in store.iter_events(cutoff):
        run_ids.add(event.run_id)
        for reference in event.artifact_refs:
            store.assets.read(reference)
            references.append(reference)
    return _ParentPrefix(frozenset(run_ids), tuple(references))
```

`restore_branch` (`:93-114`): `:101-104` become `prefix = _validated_prefix(store, checkpoint.last_event_seq)`;
`:108` passes `prefix`. Docstring `@brief 校验父检查点并逐条复核父前缀后，在暂存目录按字节复制前缀并原子发布新运行，不复制父目录较新的可变状态。`

`_populate_branch(store, checkpoint, prefix: _ParentPrefix, stage, metadata)` (`:117-143`):
- `:125` becomes `manifest["source_run_ids"] = sorted(prefix.run_ids | {manifest["run_id"]})`;
- `:128` becomes `references = _branch_asset_references(store, checkpoint, prefix.references)`;
- `:133-135` (and chapter 03's `copy_history` call) become
  `child.copy_event_prefix(store, checkpoint.last_event_seq, prefix.references)`;
- docstring `@brief 新库保留父事件的存储字节与身份，并绑定子运行和共同初态。`

`_branch_asset_references(store, checkpoint, references: Sequence[AssetRef])` (`:146-157`): `:155-156` become
`references_by_hash.update({reference.sha256: reference for reference in references})` (rename the local dict
`references` to `references_by_hash` so the parameter keeps its name); docstring unchanged.

### `src/worldmock/footprints/observation_parts.py` (chapter 03's module)

`copy_parts` is deleted: after this chapter its only caller, `FootprintStore.copy_history`, is gone, and the branch
copies the `parts` table inside `copy_event_prefix`. `store.py` drops `copy_parts` from its import of this module, and
`tests/unit/engineering/test_single_source.py` drops `'copy_parts'` from the `OWNERS` tuple of
`footprints/observation_parts.py`.

### `src/worldmock/life/engine.py`

`fail_run` (`:161-171`): `:165-167` become

```python
        if self.context.store.has_event("run_failed"):
            return
```

### `src/worldmock/life/counterfactual_runtime.py`

Imports: add `import logging`; `from worldmock.footprints.models import EventDraft` (`:8`) becomes
`from worldmock.footprints.models import Event, EventDraft`.

```python
    def _validate_counterfactual(self, intervention: CounterfactualIntervention, digest: str, round_open: bool) -> None:
        """@brief 核对分支身份、唯一时序及父检查点以前的人物可见证据；只读本运行自己的事件，来源按身份逐条读。"""
        manifest = self.context.store.manifest
        if (manifest.get("branch_kind") != "counterfactual" or manifest.get("intervention_sha256") != digest
                or not manifest.get("parent_checkpoint_id")):
            logging.error("Counterfactual branch manifest is invalid")
            raise ProtocolError("Counterfactual branch manifest is invalid")
        store = self.context.store
        own = [event for event in store.iter_events(store.last_event_seq, store.first_own_event_seq() - 1)
               if event.event_type in ("environment_event", "decision_started")]
        branch = _current_branch_event(own, manifest)
        if branch is None:
            logging.error("Counterfactual branch creation evidence is invalid")
            raise ProtocolError("Counterfactual branch creation evidence is invalid")
        if round_open or any(event.event_seq > branch.event_seq and _forbidden_before_intervention(event)
                             for event in own):
            logging.error("Counterfactual intervention must precede the first decision and occur once")
            raise ProtocolError("Counterfactual intervention must precede the first decision and occur once")
        self._validate_counterfactual_sources(intervention, branch)

    def _validate_counterfactual_sources(self, intervention: CounterfactualIntervention, branch: Event) -> None:
        """@brief 要求全部来源都在分支事件之前（父检查点截止内）且对目标人物可见；来源按事件身份逐条读。"""
        if intervention.agent_id not in self.context.state.agents:
            logging.error("Counterfactual branch or target agent is invalid")
            raise ProtocolError("Counterfactual branch or target agent is invalid")
        for event_id in intervention.source_event_ids:
            event = self.context.store.find_event(event_id)
            if (event is None or event.event_seq >= branch.event_seq or event.visibility != "agent"
                    or event.agent_id not in {None, intervention.agent_id}):
                logging.error("Counterfactual source is not visible to the target agent")
                raise ProtocolError("Counterfactual source is not visible to the target agent")
```

New module-level helper:

```python
def _forbidden_before_intervention(event: Event) -> bool:
    """@brief 分支事件之后不能先于干预出现的事件：决策开始，或另一条反事实干预。"""
    return event.event_type == "decision_started" or (
        event.event_type == "environment_event" and event.data.get("kind") == "counterfactual_intervention")
```

The four exception texts are today's; the `logging.error` lines before them are new.

### `src/worldmock/life/session_bounds.py`

`_opening_result_seq` (`:31-42`):

```python
    after = store.last_event_seq_before(session.started_at)
    sequence = store.first_action_result_seq(session.agent_id, session.opened_by_action_id, after)
    if sequence is None:
        logging.error("Session opening action has no committed result")
        raise IntegrityError("Session opening action has no committed result")
    return sequence
```

The docstring's second sentence gains 「沿人物与类型索引读到第一条匹配即停」.

### `src/worldmock/life/storefront_runtime.py`

`_accepted_cause` (`:93-101`):

```python
        store = self.context.store
        matches = [event for event in store.read_agent_events(
                       "action_result", activity.agent_id, store.last_event_seq_before(activity.started_at))
                   if event.action_id == activity.origin_action_id
                   and event.data.get("status") in {"accepted", "in_progress", "completed"}]
```

The rest is unchanged. Docstring
`@brief 支付证据只关联已提交的真实购买受理结果；受理与活动开始同一秒，只读活动开始那一秒及以后的结果。`

### `src/worldmock/life/day_plan_inputs.py`

Imports: add `from worldmock.footprints.models import Event`.

New after `_Window` (`:35-57`):

```python
@dataclass(frozen=True, slots=True)
class _AgentHistory:
    """@brief 一次零点计划读到的本人四类事件，各读一次、按事件序号，几个部分共用。"""

    #: 本人全部 activity_progress。
    progress: tuple[Event, ...]
    #: 本人全部 environment_event。
    environment: tuple[Event, ...]
    #: 本人全部 action_result。
    results: tuple[Event, ...]
    #: 本人全部 action_requested。
    requests: tuple[Event, ...]


def _agent_history(store: Any, agent_id: int) -> _AgentHistory:
    """@brief 本人四类事件各读一次（不加时间界：恢复、暂停和结束可能跨日）。"""
    return _AgentHistory(*(tuple(store.read_agent_events(event_type, agent_id)) for event_type in
                           ("activity_progress", "environment_event", "action_result", "action_requested")))
```

`plan_inputs` (`:60-82`): after `agent = …` add `history = _agent_history(context.store, agent_id)`; the four calls
become `_experience_sentences(context, observations, agent_id, history, window)`,
`_result_sentences(context, observations, agent_id, history, window)`, `_conclusion_records(history, window)`,
`_previous_goals(history, window)`.

- `_experience_sentences` (`:132-147`): `events = [*history.progress, *history.environment]` replaces `:136-138`
  (`store = context.store` goes with them); `:140` reads `cutoff_seq=context.store.last_event_seq`, still before `:141`
  rebinds `context` to the layer context. `events.sort(...)` (`:139`) sorts this new list, never a shared tuple.
- `_result_sentences` (`:150-184`): `outcomes = history.results`, `environment = history.environment`,
  `for request in history.requests:`, `progress = history.progress`; `store` is no longer needed there.
- `_conclusion_records(history: _AgentHistory, window: _Window)` (`:192-224`): reads `history.results` and
  `history.requests`.
- `_previous_goals(history: _AgentHistory, window: _Window)` (`:250-271`): the loop is
  `for event_type, events in (("environment_event", history.environment), ("action_result", history.results),
  ("activity_progress", history.progress)):` with the same body.
Docstrings keep their sentences and add 「事件取自本次计划共用的一次读取」.

### `src/worldmock/status.py`

Imports: add `from typing import get_args` and `from worldmock.footprints.models import EventType`.

Ownership in this file: this chapter owns `:35`, `:37-38`, `_observed_usage`'s statement, `event_type_counts`,
`_FIRST_EVENT_SQL`/`_LAST_EVENT_SQL` and the two reads in `_wall_time`; chapter 02 owns `:39-41` and `:106`
(checkpoint ids, ordered activities); chapter 05 owns the heartbeat, liveness and `error_message` additions and later
replaces `_wall_time`'s signature, keeping this chapter's two statements.

- `read_status` (`:28-56`): delete `:35`; `:37-38` become
  `counts = {name: count for name, count, _ in event_type_counts(connection)}`. Docstring adds
  「每个读语句各自是一个短读，不开长事务，运行中可以查」.
- New:

  ```python
  def event_type_counts(connection: sqlite3.Connection) -> list[tuple[str, int, int]]:
      """@brief 各事件类型的（类型，条数，首条序号），按类型名升序、没有的类型不列；只走类型表达式索引，不读事件正文。"""
      counts = []
      for name in sorted(get_args(EventType)):
          count, first = connection.execute(
              "SELECT count(*),min(event_seq) FROM events WHERE json_extract(body,'$.event_type')=?",
              (name,)).fetchone()
          if count:
              counts.append((name, count, first))
      return counts
  ```

- `_observed_usage` (`:83-98`): the statement's `FROM … WHERE` becomes
  `FROM events WHERE event_seq IN (SELECT event_seq FROM events WHERE json_extract(body,'$.event_type')='model_response')
  AND json_extract(body,'$.data.phase')='response'` (split over lines ≤ 120); held in a module constant
  `_OBSERVED_USAGE_SQL` so a test can `EXPLAIN` it. Columns, `GROUP BY`, `ORDER BY` and the row dicts are unchanged.
- New module constants after `logger` (`:16`), text for text chapter 05's (its "First and last event by primary key"):

  ```python
  #: 第一条与最后一条事件按主键读（不扫描事件表）；最后一条另带事件类型，供运行活性判断。
  _FIRST_EVENT_SQL = ("SELECT json_extract(body,'$.recorded_at') FROM events "
                      "WHERE event_seq=(SELECT min(event_seq) FROM events)")
  _LAST_EVENT_SQL = ("SELECT json_extract(body,'$.recorded_at'),json_extract(body,'$.event_type') FROM events "
                     "WHERE event_seq=(SELECT max(event_seq) FROM events)")
  ```

- `_wall_time` (`:131-141`), signature unchanged; `:133-138` become

  ```python
      first = connection.execute(_FIRST_EVENT_SQL).fetchone()
      if first is None:
          return {"started_at": None, "last_event_at": None, "elapsed_seconds": None}
      start = datetime.fromisoformat(first[0])
      last = datetime.fromisoformat(connection.execute(_LAST_EVENT_SQL).fetchone()[0])
  ```

  and `:139-141` stay. With no event, `min(event_seq)` is `NULL` and the first statement returns no row. Docstring
  `@brief 根据第一条与最后一条事件的落盘时刻（按主键读）计算墙钟经过时间，终止运行固定在末事件。` Chapter 05 then
  changes this function's signature and moves both reads into its `_liveness_view`; it reuses these constants as they
  are.

### `scripts/inspect_run.py`

Imports: add `from collections.abc import Iterator` (beside `:29`), and `from worldmock.footprints.store import
EVENT_PAGE_SIZE` and `from worldmock.status import event_type_counts` to the existing `# noqa: E402` block.

- New `_paged`, before `_rows`:
  ```python
  def _paged(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Iterator[tuple]:
      """@brief 按事件序号窗口 (after, after+EVENT_PAGE_SIZE] 逐页执行 sql（前两个参数是窗口两端），按页先后给出各行。

      窗口只覆盖开始时已提交的事件；每页一个语句并一次取完，至多读 EVENT_PAGE_SIZE 条事件，页间不持有读锁。
      """
      last = conn.execute("SELECT COALESCE(MAX(event_seq),0) FROM events").fetchone()[0]
      for after in range(0, last, EVENT_PAGE_SIZE):
          yield from conn.execute(sql, (after, after + EVENT_PAGE_SIZE, *args)).fetchall()
  ```
- `_rows` (`:77-83`):
  ```python
  def _rows(conn: sqlite3.Connection, where: str = "", args: tuple = ()) -> list[dict]:
      """@brief 按事件序号窗口分页读取匹配的事件体并解析为字典列表（按 event_seq）；每页一个短语句。"""
      sql = "SELECT event_seq, body FROM events WHERE event_seq>? AND event_seq<=?"
      sql += (f" AND ({where})" if where else "") + " ORDER BY event_seq"
      return [dict(json.loads(body), seq=seq) for seq, body in _paged(conn, sql, args)]
  ```
  The filter is wrapped in parentheses, so a filter with its own `OR` (none today) keeps its meaning; every current
  filter is a conjunction, for which the parentheses change nothing.
- `_select` (`:240-247`), signature and docstring unchanged:
  ```python
      sql = (f"SELECT {columns} FROM events WHERE event_seq>? AND event_seq<=? "
             "AND json_extract(body, '$.event_type') = ?")
      args: list = [event_type]
      if kind is not None:
          sql += " AND json_extract(body, '$.data.kind') = ?"
          args.append(kind)
      return list(_paged(conn, sql + " ORDER BY event_seq", tuple(args)))
  ```
- `display_context` (`:96-101`): the filter becomes
  `"event_seq IN (SELECT event_seq FROM events WHERE json_extract(body,'$.event_type')='action_result')"`.
- `cmd_summary` (`:104-116`):
  ```python
      total = conn.execute("SELECT count(*) FROM events").fetchone()[0]
      counts = sorted(event_type_counts(conn), key=lambda item: (-item[1], item[2]))
  ```
  `print(f"事件总数   : {total}")` and `for name, count, _ in counts: print(f"  {name:24s} {count}")`; the other lines
  unchanged. Docstring adds 「条数走类型表达式索引，不读事件正文」.
- `cmd_money` (`:190-204`): the `_rows` filter becomes `"json_extract(body,'$.resource_changes')!='{}'"`; the loop is
  unchanged.

### `src/worldmock/evaluation/io.py`

`read_jsonl` (`:40-50`) becomes `return list(iter_jsonl(path))` with its docstring; new `iter_jsonl(path: Path) ->
Iterator[JsonObject]` holds today's body (open, `enumerate(handle, start=1)`, `_parse_jsonl_row`) with `yield`;
docstring `@brief 严格逐行读取 JSONL，拒绝空行和非对象；一次只解析一行，供大文件流式使用。` Import `Iterator` from
`typing` (the module already imports from it).

### `src/worldmock/evaluation/adapters.py`

Imports: `from worldmock.evaluation.io import iter_jsonl, read_jsonl` (`:19`) and
`from worldmock.footprints.post_run import post_run_view`. `:360` becomes
`event_rows = [post_run_view(parse_world_event(row)) for row in iter_jsonl(delivery.event_path)]`. The docstring of
`_adapt_canonical` adds 「事件逐行解析，内存里只留事后读取视图」.

### Recorded and exchanged formats

- `events.jsonl`: unchanged, byte for byte: each line `canonical_json(event.model_dump()) + b"\n"`
  (`json.dumps(…, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()`), events in
  `event_seq` order, no trailing data. The manifest descriptor stays `{"path":"events.jsonl","rows":N,"sha256":hex}`.
- Timeline files: same format; for a mid-run cutoff only rows from events at or before the cutoff.
- Branch database: `events` rows are the parent's `(event_seq, event_id, body)` bytes; `artifacts` rows
  `(sha256, canonical_json(AssetRef.model_dump()).decode())`, first reference wins, as today.
- The prefix digest is in memory only: SHA-256 over `f"{event_seq}\t{event_id}\t{body}\n"` UTF-8 for each row in order,
  paired with the row count.
- `worldmock status`, `inspect_run`, `render_day` output: unchanged.
- `validate_export` returns `(manifest, activities)`.

## Design chapters

All in Chinese, edited before the code.

### `docs/design/05-recording-engine.md`「完整导出认证」

Add after the paragraph that ends 「行可以带 `played_count` 是导出清单结构号升到 7 的原因。」:

> 导出按事件序号分页读取截止前缀：每页一条读语句、至多 256 条，读完即放开读锁，截止之后的事件一条不读。每条事件经
> 运行库唯一的行解码器按事件模型校验，照旧以规范 JSON 写成 `events.jsonl` 的一行，边写边算 SHA-256。六份工件、质量报告和日时间线都由
> 这同一遍读出的截止前缀算出；日时间线也只读截止前缀里的活动进度与订单记录：截止早于最后一条事件时，截止之后才结算的
> 段不进时间线。截止在最后一条事件时，导出与以前逐字节相同。
>
> 导出、导出认证和行为评估在内存里保留每条事件的**事后读取视图**：观察、模型请求、模型响应三类大事件去掉下表的庞大
> 载荷，其余事件原样。表外的键（包括以后新增的键）原样保留；`events.jsonl` 与运行库里的事件始终完整。表里的键没有一个
> 事后读取会读；新的质量指标或检查要读其中一个，先把它移出表。
>
> | 事件类型 | 内存里去掉的 |
> | --- | --- |
> | 观察 `observation` | `activity_bindings`、`persona_views`、`tool_layer`、`interaction_bindings`、`catalog_bindings`、`memory_layer`、`goal_bindings`、`locations`；`errand_item_bindings` 里没有被任何 `errand_bindings` 行引用的商品行（不是对象、缺 `product_id` 或 `name`、`product_id` 不是字符串的行一律保留） |
> | 模型请求 `model_request` | `request` 里每条消息的 `content` 与 `reasoning_content`，每个工具 `function` 的 `description` 与 `parameters` |
> | 模型响应 `model_response` | 同上的 `request` 部分；`response.choices` 每一项的 `message` |
>
> 导出认证读 `events.jsonl` 两遍。第一遍只按字节算 SHA-256 和行数（按 `bytes.splitlines` 切行），不解析任何一行，
> 摘要不符报 `Export artifact hash is invalid`，行数不符报 `Export artifact row count is invalid`。第二遍逐行解析，先做活动
> 快照检查（退役种类、退役 `title` 字段），再按事件模型严格校验，然后只把事后读取视图留在内存；读完再算一次摘要，两遍
> 读到的字节不同同样报 `Export artifact hash is invalid`。其余检查与重算不变，读的是事后读取视图。认证返回导出清单与
> 活动行，不返回事件；事件条数取清单的 `artifacts.events.rows`。

### `docs/design/05-recording-engine.md`「冻结输入、检查点与分支」

Add after the paragraph that begins 「检查点绑定准确事件前缀」:

> 分支按存储字节复制父库截止前缀。先按事件序号分页经唯一的行解码器逐条校验父事件，核对每行的序号、身份与正文一致，逐个
> 读取并核对每条事件引用的素材，同时记下前缀里出现的运行身份；`source_run_ids` 仍是这些运行身份加新运行身份的升序集合。
> 然后把父库以只读方式附加，在子库一个事务里复制观察分件、按事件顺序插入素材索引行（同一摘要只留第一条），再用
> `INSERT … SELECT` 按序号复制事件行；提交后子库与父库前缀的条数和逐行摘要（序号、身份、正文）必须相同，否则报
> `Branch event copy disagrees with the parent prefix`，分支不发布。父库只被读取，不被改写。子库的事件行就是父运行当时
> 写下并读过的字节。

### `docs/design/05-recording-engine.md`「检查工具」

Add after the sentence 「时刻一律按运行清单的 `window.timezone` 显示……」:

> `inspect_run.py` 的各子命令按事件序号窗口分页读库：每条语句至多读 256 条事件，读完即放开读锁，只读命令开始时已提交的
> 事件，运行中可以使用。`summary` 的各类条数走类型表达式索引、不读事件正文，次序与以前相同：条数多的在前，条数相同按
> 首条事件的序号；`money` 只取资金变化非空的事件。输出与以前逐字节相同。`render_day.py` 的订单记录经类型索引读取。

Replace the sentence 「`scripts/render_day.py` 写出的时间线文件是 UTF-8、LF，在任何平台上与导出里同名的时间线逐字节相同。」 with:

> `scripts/render_day.py` 写出的时间线文件是 UTF-8、LF。它读整个运行库，所以在任何平台上与截止在最后一条事件的导出里
> 同名的时间线逐字节相同；截止更早的导出只投影截止前缀，见「完整导出认证」。

### `docs/design/10-runtime.md`「运行与恢复」

Add after the paragraph that ends 「含继承自父运行的响应。」:

> 运行中可以执行 `worldmock status`、`scripts/inspect_run.py` 与 `scripts/render_day.py`：它们不开长读事务（各类事件条数
> 走类型表达式索引，墙钟时间取第一条与最后一条事件，`inspect_run.py` 按事件序号窗口分页），最长的一条语句是 `status`
> 的模型用量合计，只读经类型索引找到的模型响应。引擎的足迹库连接遇到读者持锁时最多等 120 秒再提交，而不是 5 秒后失败；
> 日志模式仍是回滚日志，运行结束后运行库是一个自足的文件。
> 恢复与重放怎样复制父库见[记录引擎](05-recording-engine.md)「冻结输入、检查点与分支」。

### `docs/design/07-evaluation.md`「认证与适配」

Add after 「WorldMock 只接受当前认证导出；先校验全部事件前缀，再投影活动。」:

> 适配逐行读 `events.jsonl`，每行按事件模型解析后只把事后读取视图留在内存（[记录引擎](05-recording-engine.md)
> 「完整导出认证」的同一张表）；适配读的字段都不在表里，结果与读完整事件相同。

No other design text changes: the session interval rule (`06-agent.md`「进度、待办与记忆」), the plan_day inputs
(`06-agent.md`) and the purchase payment cause (`02-exchange-engine.md`) are unchanged; only how they are read changes.
No manual page changes.

## Tests

Every test name below is the exact function name; parametrized ids are given where a mutation names one.

Statement-level assertions use one new test helper, `tests/footprints/statement_log.py`:
`StatementLog(connection)` is a proxy whose `execute(sql, parameters=())` appends `(sql, tuple(parameters))` to
`self.statements` and returns the real connection's `execute(sql, parameters)`; every other attribute is delegated by
`__getattr__`. A test installs it with `monkeypatch.setattr(store, "connection", StatementLog(store.connection))` (or
passes it as `conn` to an `inspect_run` function) and, where a plan is asserted, runs
`EXPLAIN QUERY PLAN <captured sql>` with the captured parameters on the real connection, so the plan checked is the
production statement's, not a copy kept in the test.

### New test files

**`tests/unit/footprints/test_post_run_view.py`**
- `test_post_run_view_drops_only_the_listed_payloads`: an observation `data` with the eight keys, `todo_layer`,
  `verified_experiences`, `progress_event_ids`, `errand_bindings`, `kind`, `activity`, `data` and an invented
  `future_key`; the view lacks exactly the eight keys and keeps every other value as the same object; a `model_request`
  keeps `call_id`, `purpose`, `phase` and every request key, with messages keeping `role`, `tool_calls`,
  `tool_call_id` and tools keeping `type` and `function.name`; a `model_response` keeps `usage`, `model`, `id`,
  `choices[0].finish_reason` and `choices[0].index`; an `action_result` is returned as the same object.
- `test_errand_item_bindings_keep_every_row_a_reader_can_reach`: referenced rows kept in order; an unreferenced
  well-formed row dropped; kept: a non-dict row, a row without `product_id`, a row with an `int` `product_id`, a row
  without `name`; a non-list value kept as is; `errand_bindings` missing or not a list drops only well-formed `str` rows.
- `test_errand_texts_are_equal_or_fail_alike_from_full_and_post_run_observations`: for twelve crafted observations
  (well-formed, duplicate `product_id`s, each malformed shape above, `errand_bindings` as `None`, a dict, a string),
  `errand_metrics._errand_texts` on the full and on the reduced `data` returns the same tuple or raises the same
  exception type with the same message.
- `test_request_outline_keeps_every_call_class`: for requests of the six call-class shapes of chapter 06 (fresh
  decision turn, turn with history, truncation retry, consolidation, reflection, plan_day) plus odd shapes (non-dict
  request, non-list tools, a tool without `function`, a non-dict message), `decision_start.is_action_request(request)`,
  the tool-name reader (`behavior_metrics._request_tool_names` today, `decision_start.request_tool_names` after chapter
  06 moves it) and the list of message roles are equal on the full and the outlined request. Chapter 06 lands after this
  chapter; when it does, it adds `quality._call_class(data)` to this comparison.
- `test_finish_reason_usage_and_model_survive_the_response_reduction`: `quality._finish_reason`, `_usage_count` and
  `_usage_entry` give equal results on full and reduced responses, including a non-dict `choices[0]` and a non-list
  `choices`.
- `test_post_run_row_matches_the_view_of_the_validated_event`: for one row of each reduced type,
  `post_run_row(row)["data"] == post_run_view(Event.model_validate(row, strict=True)).data`.
- `test_no_reduced_key_is_a_key_a_list_level_check_reads`: `{"kind", "activity", "data", "content", "status",
  "checkpoint_id", "ended_at", "interval_start", "interval_end", "reused", "verified_experiences", "progress_event_ids",
  "todo_layer", "errand_bindings", "errand_item_bindings", "call_id", "purpose", "phase", "error",
  "protocol_feedback", "response"}` is disjoint from `DROPPED_PAYLOADS["observation"]`.

**`tests/integration/footprints/test_post_run_guard.py`** (fixture runs: `recorded_engine(engine_factory)` from
`tests/footprints/recording.py`, and `errand_engine(engine_factory)`, a new helper there that runs the scenario of
`test_errand_export.py::test_errand_completion_recertifies_actual_received_goods` up to its final checkpoint; ids
`recorded`, `errand`)
- `test_export_is_byte_identical_with_and_without_the_post_run_view[recorded]` and `[errand]`: export twice from the
  same store, the second time with `worldmock.footprints.export.post_run_view` monkeypatched to `lambda event: event`;
  every file of the two directories, `manifest.json` included, is byte-identical; `validate_export` accepts both, and
  the second time also with `worldmock.footprints.validation.post_run_view`/`post_run_row` patched to the identity.
- `test_every_fixture_observation_carries_every_dropped_payload[recorded]` and `[errand]`: every observation of the
  run has all eight keys (`observe.py:39-51` and `_name_bindings`, `:113-118`, write them unconditionally), and the errand run has an observation with
  a non-empty `errand_item_bindings` and a non-empty `todo_layer.errands` (so the guard compares real data).
- `test_export_holds_only_post_run_views_in_memory`: on the `recorded` run, wrap
  `worldmock.footprints.export.build_quality_report` to capture its `events`; the captured list holds at least one
  observation (asserted, so the test is not vacuous) and no captured observation has any of the eight dropped keys.
  These fixture runs make no model call; the model events are guarded in `test_output_truncation.py` below.

**`tests/integration/footprints/test_export_stream.py`**
- `test_streamed_export_equals_the_full_event_reference[recorded]` and `[errand]` (the byte-identity proof, below).
- `test_mid_run_cutoff_timeline_stops_at_the_cutoff`: a `recorded` run (purchase, then the content app use, the same
  local day); `cutoff` = the `event_seq` of the `action_requested` event with `action_id == "action-recorded-content"`,
  minus one, so the purchase is settled and the content use has not started. The export's only timeline file equals
  `day_markdown(1, day, day_rows(progress, orders, names, day, zone))` with `progress, orders` =
  `timeline_sources(list(store.iter_events(cutoff)))` filtered to agent 1; it has no row whose label contains
  `哔哩哔哩`; it differs from `render_day_markdown(store.connection, 1, day, zone, names)` of the whole database, which
  has that row; `quality.json` equals `canonical_json(build_quality_report(prefix, reconstruct_activities(prefix),
  store.manifest, names))` with `prefix = list(store.iter_events(cutoff))`.
- `test_export_rejects_a_cutoff_outside_the_sequence_before_creating_anything`: cutoffs `-1` and `last + 1` raise
  `Event cutoff is outside the committed sequence`, cutoff `0` raises `Cannot export an empty event range`; the
  destination and any `.export-*` stage are absent.
- `test_export_reads_the_store_one_page_at_a_time`: on the `recorded` run, first export with the default page size to
  `default`; then `EVENT_PAGE_SIZE` monkeypatched to 3 in `worldmock.footprints.store`, `store.connection` replaced by a
  `StatementLog`, export to `paged`. Every logged statement that starts with
  `SELECT event_seq,event_id,body FROM events WHERE event_seq>?` has parameters `(after, stop)` with
  `0 < stop - after <= 3`; their windows are disjoint and cover `(0, cutoff]`; no logged statement reads `body` from
  `events` without an `event_seq` bound or a primary-key or `event_id` lookup; every file of `paged` is byte-identical
  to `default`.

**`tests/integration/footprints/test_inspect_short_reads.py`** (loads `scripts/inspect_run.py` the way the existing
inspect tests do)
- `test_summary_prints_the_same_lines_as_counting_every_event`: on a recorded run, stdout equals the text built by
  today's algorithm inside the test (decode every row, count in first-occurrence order, sort by `-count`).
- `test_summary_orders_equal_counts_by_first_occurrence`: a store whose first events are an `observation` and then a
  `decision_started`, with equal counts; `observation` is printed first.
- `test_money_prints_only_changed_events_and_the_same_balances`: stdout equals today's algorithm in the test.
- `test_paged_rows_equal_one_whole_history_read`: on a recorded run (more than 2 events), with `EVENT_PAGE_SIZE`
  patched to 2 in the loaded module and `conn` a `StatementLog`, `_rows` for `""`, a type filter, `_ACTION_REQUEST`,
  `event_seq=?` and the `action_result` index filter, and `_select` for three column sets, equal today's single
  unpaged statement written in the test (`… WHERE <filter> ORDER BY event_seq`, decoded the same way); every logged
  page statement has a window `(after, stop)` with `stop - after == 2`.
- `test_display_context_reads_results_through_the_type_index`: with `conn` a `StatementLog`, `EXPLAIN QUERY PLAN` of the
  logged page statements of `display_context` contains `USING COVERING INDEX events_agent_type`; its orders equal
  `orders_by_id` of the full-scan filter's rows.

**`tests/integration/life/test_history_reads.py`**
- `test_session_lower_bounds_equal_the_full_result_scan`: a run with several decision-opened sessions (two departures
  and an early-ended trip), where an opening action also has a later second `action_result` (its completion); for every
  session of the final world, `session_lower` equals a reference computed in the test from today's rule
  (`read_agent_events("action_result", agent, last_event_seq_before(started_at))` filtered by action id, first
  sequence, then `commit_last_seq`). This is the review's gate (old-versus-new lower bounds); the byte-identical
  observation was shown offline [R] and the restore-equals-live test belongs to chapter 02's activity order.
- `test_purchase_acceptance_lookup_starts_at_the_activity_start`: spy on `read_agent_events` during a purchase
  completion; the call for `action_result` passes `after == store.last_event_seq_before(activity.started_at)`; the
  payment event's `caused_by` is the acceptance result.
- `test_plan_inputs_read_each_event_type_once`: spy on `read_agent_events` during `plan_inputs`; exactly four calls,
  one per type.
- `test_plan_inputs_equal_parts_built_from_fresh_reads`: `plan_inputs(...)` equals the `PlanInputs` assembled in the
  test from the four private part functions, each given its own freshly read `_AgentHistory`.
- `test_counterfactual_reads_only_the_branch_suffix`: on a fresh counterfactual branch (the `_branch` helper of
  `test_counterfactual_rejection_contract.py`), `first_own_event_seq()` is the `branch_created` sequence, and during a
  valid `apply_counterfactual` the only `iter_events` call (spy) has `after == branch_created seq - 1`.

**`tests/integration/evaluation/test_world_adapter_stream.py`** (fixture `evaluation_fixture`, `load_scenario`)
- `test_world_dataset_view_is_equal_with_and_without_the_post_run_view`: `adapt_worldmock` normally and with
  `worldmock.evaluation.adapters.post_run_view` patched to the identity give equal `DatasetView`s.
- `test_world_adapter_keeps_only_post_run_views`: wrap `adapters.worldmock_public_evidence`; the captured events hold
  at least one observation (asserted, so the test is not vacuous) and none has `activity_bindings`.
- `test_iter_jsonl_rejects_like_read_jsonl`: blank row, invalid JSON, non-object row and a missing file give the same
  `EvaluationError` code and text from both.

### Additions to existing test files

**`tests/unit/footprints/test_store.py`**
- `test_iter_events_pages_by_sequence_and_rejects_an_outside_range`: page size patched to 2, five events,
  `store.connection` a `StatementLog`: `list(store.iter_events(5))` is all five in order, read by exactly three page
  statements with windows `(0, 2)`, `(2, 4)`, `(4, 5)`; `list(store.iter_events(5, 2))` is events 3–5; `(after, cutoff)`
  of `(0, 6)`, `(-1, 3)`, `(4, 3)` raise `Event cutoff is outside the committed sequence` with the ERROR log at the
  call, before any page statement is logged.
- `test_paged_reads_reject_a_row_that_disagrees_with_its_body`: a raw `INSERT` of a row whose `event_id` column differs
  from its body (`event_seq` correct, so the trigger accepts it); `list(store.iter_events(...))` raises
  `Event row disagrees with its body`.
- `test_has_event_reads_the_type_index_and_stops_at_the_first_match`: false on an empty store, true after one event of
  the type, false for another type; with a `StatementLog`, the plan of the logged statement contains
  `USING COVERING INDEX events_agent_type`.
- `test_find_event_returns_none_for_an_unknown_identity`: and `read_event` keeps its log and `Event does not exist`.
- `test_first_own_event_seq_walks_back_over_the_branch_suffix`: a store from `FootprintStore.create` with two committed
  events gives 1; the child of `restore_branch` from a checkpoint of it gives the `branch_created` sequence; the same
  child with `child.manifest["run_id"]` replaced in memory by another id gives `last_event_seq + 1`.
- `test_first_action_result_is_the_earliest_result_after_the_boundary`: two results of the same action (seq 3 and 6)
  and one of another action; with `after=0` the answer is 3, with `after=3` it is 6, an unknown action gives `None`,
  `after` of `-1` and `last + 1` raise `Agent event boundary is outside the committed sequence`.
- `test_recent_experience_sources_equal_the_single_query_with_or`: 40 progress events and 10 receipts of agent 1 and 10
  of agent 2 interleaved; for every `before_seq` and `limit` in `{1, 5, 32}` the rows equal today's `OR` statement
  (kept as text in the test).
- `test_store_connection_waits_120_seconds_for_a_reader`: `PRAGMA busy_timeout` on `store.connection` is `120000`.
- `test_event_prefix_digest_covers_sequence_identity_and_body`: equal for two stores with the same rows; a different
  body, identity or count changes it.
- `:202` and `:284`: `store.read_events(n)` → `list(store.iter_events(n))`.

**`tests/unit/footprints/test_branch_coverage.py`**
- `test_branch_copies_parent_rows_and_artifact_rows_byte_for_byte`: a parent with event artifact refs (two events
  sharing one sha256 with different `source`); the child's `events` rows for `seq <= cutoff` equal the parent's
  `(event_seq, event_id, body)` rows; the child's `artifacts` rows equal a reference built in the test by today's rule
  (each event in order, each ref, first wins); `source_run_ids` equals `sorted(parent run ids | {child})`.
- `test_branch_rejects_an_invalid_parent_body_before_creating_the_stage`: before the checkpoint, a raw parent row
  (next `event_seq`, so the trigger accepts it) whose body is valid JSON with the right `event_seq`, `event_id` and
  `occurred_at` but fails `Event` validation (an unknown `event_type`); `restore_branch` raises the validation error;
  neither the destination nor a `.restore-*` stage exists.
- `test_branch_copy_that_disagrees_with_the_parent_publishes_nothing`: `FootprintStore.event_prefix_digest`
  monkeypatched to return a different value for the parent; `IntegrityError("Branch event copy disagrees with the
  parent prefix")`, the ERROR line, no destination, no stage.
- `test_branch_reads_every_parent_artifact_reference`: an event-ref asset file removed from the parent; `restore_branch`
  raises `Artifact is missing or is a symbolic link` before any stage exists.
- `test_branch_detaches_the_parent_after_a_failed_copy`: `copy_event_prefix` called directly on a store that already
  holds one event, so the trigger refuses the first copied row; the exception propagates with the logged
  `Branch event copy failed; the child keeps no copied event`, the store still holds exactly its one event and no
  artifact row from the call, and `PRAGMA database_list` lists no `parent`.
- `:678`: `child.read_events(...)` → `list(child.iter_events(...))`.

**`tests/integration/footprints/test_validation.py`**
- `test_export_accepts_actual_complete_authenticated_run` (`:18-20`): `manifest, activities = validate_export(...)`;
  `events` from `artifact_rows(complete_export, "events")`.
- `test_forged_observation_with_initial_funds_kind_is_rejected`: add `"kind": "initial_funds_injected"` to one
  observation's `data` and rewrite `events.jsonl` with `tests/footprints/recording.py`'s `write_rows` (which re-signs
  the artifact in the manifest; then `write_manifest`); `validate_export` raises
  `Complete account history requires one initial funding event` (today's ledger text), as main does.
- `test_forged_observation_with_a_retired_activity_snapshot_is_rejected`: add
  `"activity": {"activity_id": "x", "agent_id": 1, "kind": "wait"}` to one observation, re-signed the same way; raises
  `Activity snapshot uses a retired activity kind`, as main does.
- `test_tampered_events_with_an_invalid_line_report_the_hash_first`: append `not json\n` without re-signing; raises
  `Export artifact hash is invalid`.
- `test_events_changed_between_the_two_reads_are_rejected`: wrap `validation._authenticated_events` so that after the
  real function returns its digest, the wrapper rewrites `events.jsonl` with one space inserted before the first
  line's `\n` (still valid JSON, the same parsed rows and the same row count, so every other check would accept it);
  `validate_export` raises `Export artifact hash is invalid` and logs `Export events changed between the two reads`.
- `test_row_count_uses_the_same_line_split_as_before`: an export whose `events.jsonl` has a `\r` inside a line,
  re-signed with the count of `raw.splitlines()`; the error is the same `json.loads` error as on main (not a row-count
  error).

**`tests/integration/footprints/test_verified_experiences.py`**
- `test_every_recorded_window_replays_from_the_store`: for every observation of a recorded run,
  `recent_verified_experiences(store, agent_id, event_seq - 1) == data["verified_experiences"]`.
- `:64` and `:205`: `monkeypatch.setattr(store, "read_events", …)` would fail on the deleted attribute; both become
  `monkeypatch.setattr(store, "iter_events", lambda *_: pytest.fail(<same message>))`.

**`tests/integration/runtime/test_output_truncation.py`** (a fixture run with recorded model calls, messages, tool
schemas and provider choices that the suite already exports)
- `test_export_with_model_calls_is_byte_identical_with_and_without_the_post_run_view` (asyncio, `_engine_factory`):
  the scenario of `test_the_exported_quality_report_counts_the_truncations_the_run_recorded` (`:351-361`). The test
  first asserts that the store's `model_request` events carry message `content` and tool `function.parameters`, and
  its `model_response` events carry `choices[*].message`. It exports to `views`, wrapping
  `worldmock.footprints.export.build_quality_report` to capture its events, then exports to `full` with
  `worldmock.footprints.export.post_run_view` patched to `lambda event: event`. Every file of the two directories is
  byte-identical (the quality report's `model_usage` and `truncations` read the reduced responses); no captured
  request message has `content` or `reasoning_content`, no captured tool `function` has `description` or `parameters`,
  and no captured response choice has `message`.

**`tests/unit/engineering/test_single_source.py`**
- `OWNERS` gains `'footprints/post_run.py': ('DROPPED_PAYLOADS', 'post_run_view', 'post_run_row')`, so a second copy
  of the table elsewhere is rejected; `'copy_parts'` leaves the `footprints/observation_parts.py` tuple (deleted).

**`tests/unit/life/test_counterfactual_rejection_contract.py`**
- `test_counterfactual_source_at_or_after_the_branch_event_is_not_visible`: a store from `FootprintStore.create` with
  three committed events, the second an agent-visible `observation` of agent 1; a `CounterfactualRuntime` over a
  `SimpleNamespace` context (that store, `state.agents == {1: …}`);
  `_validate_counterfactual_sources(intervention, branch)` with `branch` a `SimpleNamespace(event_seq=2)` and the
  observation as the only source raises `ProtocolError("Counterfactual source is not visible to the target agent")`;
  with `event_seq=3` it passes. (A real branch cannot carry a known child source id, because the intervention digest is
  bound into the branch manifest before the branch exists; the existing cases `hidden`, `missing`, `agent`, `round`,
  `decision` keep covering the real flow.)

**`tests/unit/footprints/test_status.py`**
- `test_status_counts_and_wall_time_equal_the_whole_history_queries`: on a run with at least four event types,
  `list(read_status(run)["event_counts"].items())` equals the `GROUP BY` statement's rows in order, and
  `wall_time.started_at`/`last_event_at` equal `min`/`max(recorded_at)`.
- `test_status_issues_no_explicit_transaction`: `worldmock.status.sqlite3.connect` monkeypatched to open the real
  connection and call `set_trace_callback(statements.append)` on it; after `read_status`, no recorded statement starts
  with `BEGIN` (Python's legacy mode would log its implicit `BEGIN` there too).
- `test_observed_usage_reads_responses_through_the_type_index`: `EXPLAIN QUERY PLAN _OBSERVED_USAGE_SQL` contains
  `USING COVERING INDEX events_agent_type`; rows equal those of today's statement kept in the test, on a store with
  `model_response` events of both phases and two models.

**`tests/integration/footprints/test_render_day.py`**
- `test_result_bodies_through_the_index_equal_the_full_scan`: on the module's fixture database, `_result_bodies`
  equals the old statement kept in the test, and `EXPLAIN QUERY PLAN render_day._RESULT_BODIES_SQL` contains
  `USING COVERING INDEX events_agent_type`.

### The byte-identity proof for this chapter

1. **Export, permanent.** `test_streamed_export_equals_the_full_event_reference` on `recorded` and `errand` runs:
   `full = list(store.iter_events(cutoff))`; `events.jsonl` equals `b"".join(canonical_json(e.model_dump()) + b"\n")`;
   activities, verified experiences, purchases, app usage and location stays equal `write_jsonl` of today's functions
   on `full`; `quality.json` equals `canonical_json(build_quality_report(full, …))`; every timeline equals
   `render_day_markdown(store.connection, …).encode()` (today's renderer on the whole database); the manifest's
   `artifacts` descriptors equal `write_jsonl`'s. Together with the guard tests (`test_post_run_guard.py` for
   observations, `test_output_truncation.py` for model requests and responses) this pins every exported byte.
2. **Complete-day baselines.** `tests/system/lifecycle/test_complete_day_baseline.py` (both days) pins every export
   artifact. Both baseline exports are cut at the run's last event (`tests/life/day_baseline_runtime.py:114`), where this
   chapter changes no byte, so it needs no re-freeze of its own.
3. **Branch.** `test_branch_copies_parent_rows_and_artifact_rows_byte_for_byte`, the existing resume and counterfactual
   tests, and the proposal's acceptance item "restore-equals-live at every step of a scripted day".
4. **Prompts.** The experience-window replay test, the session lower-bound test, the plan-input tests and the storefront
   test cover every prompt-side read this chapter changes; the masked complete-day comparison (chapter 08) runs too.
5. **Check.** Every existing positive and negative test of `tests/integration/footprints` and `tests/footprints`
   passes unchanged apart from the return shape; the new negative tests add the review's cases.
6. **Offline A/B before merge (receipts in `.work/`).** With base `0bd2de65` (plus chapters 01–03 as integrated) and
   this chapter's head: export the scratch 10-day prefix and the 30-day copy under `.work/run30/scratch/opt-history/`
   and compare SHA-256 per file; `validate_export` and `worldmock status` on both; the restore digests of
   `08c_restore_timed.py`.

### Existing tests that change

- **`validate_export` callers** unpack two values; those that used events read
  `artifact_rows(directory, "events")` (or `read_export`): `tests/integration/footprints/test_acs_export.py:30`,
  `:193`, `:219`; `test_acs_invariants.py:17`; `test_dataset_recording.py:35`, `:88`, `:131`; `test_errand_export.py:36`
  (its quality recomputation uses the full rows from the file); `test_goal_export.py:15`, `:28`;
  `test_pause_recording.py:41`, `:83`; `test_refusal_export.py:40`, `:67`; `test_validation.py:19`;
  `tests/integration/life/test_content_start_evidence.py:119`; `tests/integration/runtime/test_environment_identity.py:156`;
  `tests/life/day_baseline_runtime.py:115`; `tests/system/lifecycle/test_dayrun_complete_days.py:26`;
  `test_external_environment_lifecycle.py:73`, `:118`; `test_parcel_arrival_export.py:86`;
  `test_selected_environment_lifecycle.py:45`; `test_sleep_and_reflection_run.py:123`, `:183`;
  `tests/integration/footprints/test_export.py:23`, `:60` (`validate_export(...)[0]` stays valid).
- **`tests/footprints/recording.py`**: `export_completed` returns
  `(manifest, artifact_rows(directory, "events"), activities)` built from `validate_export`'s two values, so its 15
  callers are unchanged; new helper `errand_engine(engine_factory)`.
- **`read_events` callers** (44 sites in 24 files) become `list(<store>.iter_events(<cutoff>))`:
  `tests/integration/evaluation/test_goal_metrics.py`, `test_workload_metrics.py`,
  `tests/integration/footprints/test_acs_source_guards.py`, `tests/integration/life/test_action_dispatch_contract.py`,
  `tests/integration/reflection_fixtures.py`, `tests/integration/runtime/test_conclude_memories.py`,
  `test_current_runtime_contract.py`, `test_decision_context_roundtrip.py`, `test_env_lifecycle_coverage.py`,
  `test_errand_checkpoint.py`, `test_grounded_evidence.py`, `test_host_failure_stages.py`,
  `test_native_dataset_lifecycle.py`, `test_persona_profile.py`, `test_query_host_flow.py`,
  `test_read_query_runtime.py`, `test_strict_reflection.py`, `test_verified_memory.py`,
  `tests/life/environment_runtime.py`, `tests/system/lifecycle/test_run_viewer_recording.py`,
  `tests/unit/footprints/test_branch_coverage.py`, `test_ledger.py`, `test_purchases.py`, `test_store.py`; and the calls
  chapter 03 adds before this chapter lands: `tests/life/observation_record.py` (`assert_observations_round_trip`) and
  `tests/unit/footprints/test_observation_parts.py`. In the latter,
  `test_every_read_method_returns_the_complete_observation` also covers the two new decoding readers:
  `list(store.iter_events(last))` and `store.find_event(id)` return the complete observation.
- **`monkeypatch.setattr(store, "read_events", …)`** in `tests/integration/footprints/test_verified_experiences.py:64`
  and `:205` patch `"iter_events"` instead (above); `setattr` on a deleted attribute fails.
- **`tests/integration/footprints/test_render_day.py`**: nothing calls `_markdown` today; no change besides the new
  test. `tests/unit/engineering/test_shared_primitives.py` keeps calling `day_rows`.
- **WorldMock-Dataset** (coordinated with the session working on dataset issues #11 and #12; not edited from here):
  `tools/execution.py:220-221` becomes `manifest, activities = validate_export(...)` with
  `"event_count": manifest["artifacts"]["events"]["rows"]`; `tests/test_execution.py:50` becomes
  `manifest, _ = validate_export(...)`. The value written to `checks/export.json` is unchanged.

### Source mutations

Register in `tests/dataset_acceptance.py` (`MUTATIONS`); each original occurs once in its symbol.

| Name | Symbol | Original | Replacement | Killer tests |
| --- | --- | --- | --- | --- |
| `post_run_view_drops_the_todo_layer` | `worldmock.footprints.post_run::DROPPED_PAYLOADS` | `"catalog_bindings", "memory_layer", "goal_bindings", "locations"}),` | `"catalog_bindings", "memory_layer", "goal_bindings", "locations", "todo_layer"}),` | `tests/unit/footprints/test_post_run_view.py::test_post_run_view_drops_only_the_listed_payloads`, `tests/integration/footprints/test_post_run_guard.py::test_export_is_byte_identical_with_and_without_the_post_run_view[errand]` |
| `item_bindings_drop_a_reachable_row` | `worldmock.footprints.post_run::_unreferenced` | `and row["product_id"] not in used)` | `)` | `tests/unit/footprints/test_post_run_view.py::test_errand_item_bindings_keep_every_row_a_reader_can_reach` |
| `request_outline_drops_the_tools` | `worldmock.footprints.post_run::_request_outline` | `outline["tools"] = [_tool_outline(tool) for tool in request["tools"]]` | `outline["tools"] = []` | `tests/unit/footprints/test_post_run_view.py::test_request_outline_keeps_every_call_class` |
| `post_run_view_drops_the_kind` | `worldmock.footprints.post_run::DROPPED_PAYLOADS` | `"catalog_bindings", "memory_layer", "goal_bindings", "locations"}),` | `"catalog_bindings", "memory_layer", "goal_bindings", "locations", "kind"}),` | `tests/integration/footprints/test_validation.py::test_forged_observation_with_initial_funds_kind_is_rejected`, `tests/unit/footprints/test_post_run_view.py::test_no_reduced_key_is_a_key_a_list_level_check_reads` |
| `response_reduction_drops_the_choices` | `worldmock.footprints.post_run::_without_messages` | `"choices": [_without(choice, frozenset({"message"})) for choice in response["choices"]]` | `"choices": []` | `tests/unit/footprints/test_post_run_view.py::test_finish_reason_usage_and_model_survive_the_response_reduction`, `tests/integration/runtime/test_output_truncation.py::test_export_with_model_calls_is_byte_identical_with_and_without_the_post_run_view` |
| `export_keeps_full_big_events_in_memory` | `worldmock.footprints.export::_write_events` | `kept.append(post_run_view(event))` | `kept.append(event)` | `tests/integration/footprints/test_post_run_guard.py::test_export_holds_only_post_run_views_in_memory`, `tests/integration/runtime/test_output_truncation.py::test_export_with_model_calls_is_byte_identical_with_and_without_the_post_run_view` |
| `export_timeline_reads_past_the_cutoff` | `worldmock.footprints.export::_write_timeline_projections` | `progress, orders = timeline_sources(events)` | `progress, orders = timeline_sources(list(store.iter_events(store.last_event_seq)))` | `tests/integration/footprints/test_export_stream.py::test_mid_run_cutoff_timeline_stops_at_the_cutoff` |
| `check_parses_before_authenticating` | `worldmock.footprints.validation::validate_export` | `digest = _authenticated_events(root, manifest["artifacts"]["events"])` | `digest = manifest["artifacts"]["events"]["sha256"]` | `tests/integration/footprints/test_validation.py::test_tampered_events_with_an_invalid_line_report_the_hash_first` |
| `check_trusts_the_second_read` | `worldmock.footprints.validation::_read_events` | `if hasher.hexdigest() != digest:` | `if False:` | `tests/integration/footprints/test_validation.py::test_events_changed_between_the_two_reads_are_rejected` |
| `event_row_disagreeing_with_its_body_accepted` | `worldmock.footprints.store::FootprintStore._decoded_row` | `if event.event_seq != sequence or event.event_id != identity:` | `if False:` | `tests/unit/footprints/test_store.py::test_paged_reads_reject_a_row_that_disagrees_with_its_body` |
| `branch_copy_unchecked` | `worldmock.footprints.store::FootprintStore.copy_event_prefix` | `if self.event_prefix_digest(cutoff) != parent.event_prefix_digest(cutoff):` | `if False:` | `tests/unit/footprints/test_branch_coverage.py::test_branch_copy_that_disagrees_with_the_parent_publishes_nothing` |
| `branch_artifact_rows_not_copied` | `worldmock.footprints.store::FootprintStore.copy_event_prefix` | `for reference in references:` | `for reference in ():` | `tests/unit/footprints/test_branch_coverage.py::test_branch_copies_parent_rows_and_artifact_rows_byte_for_byte` |
| `branch_skips_parts` (replaces chapter 03's mutation of the same name on `copy_history`) | `worldmock.footprints.store::FootprintStore.copy_event_prefix` | `self.connection.execute("INSERT INTO main.parts SELECT sha256, body FROM parent.parts")` | `pass` | `tests/unit/footprints/test_observation_parts.py::test_branch_copies_the_parts_and_the_stored_rows` (chapter 03's test, unchanged: it compares the child's `parts` rows with the parent's) |
| `branch_skips_parent_revalidation` | `worldmock.footprints.checkpoint::_validated_prefix` | `for event in store.iter_events(cutoff):` | `for event in ():` | `tests/unit/footprints/test_branch_coverage.py::test_branch_rejects_an_invalid_parent_body_before_creating_the_stage` |
| `fail_run_appends_a_second_failure` | `worldmock.life.engine::LifeEngine.fail_run` | `if self.context.store.has_event("run_failed"):` | `if False:` | `tests/unit/life/test_current_lifecycle_coverage.py::test_failure_is_one_durable_fact_and_empty_failure_is_rejected` |
| `counterfactual_accepts_a_child_source` | `worldmock.life.counterfactual_runtime::CounterfactualRuntime._validate_counterfactual_sources` | `if (event is None or event.event_seq >= branch.event_seq or event.visibility != "agent"` | `if (event is None or event.visibility != "agent"` | `tests/unit/life/test_counterfactual_rejection_contract.py::test_counterfactual_source_at_or_after_the_branch_event_is_not_visible` |
| `session_opening_takes_the_last_result` | `worldmock.footprints.store::FootprintStore.first_action_result_seq` | `ORDER BY event_seq LIMIT 1` | `ORDER BY event_seq DESC LIMIT 1` | `tests/unit/footprints/test_store.py::test_first_action_result_is_the_earliest_result_after_the_boundary` |
| `experience_window_keeps_the_oldest_rows` | `worldmock.footprints.store::FootprintStore.read_recent_experience_sources` | `) ORDER BY event_seq DESC LIMIT ?` | `) ORDER BY event_seq LIMIT ?` | `tests/unit/footprints/test_store.py::test_recent_experience_sources_equal_the_single_query_with_or` |
| `storefront_reads_the_whole_history` | `worldmock.life.storefront_runtime::StorefrontRuntime._accepted_cause` | `store.last_event_seq_before(activity.started_at))` | `0)` | `tests/integration/life/test_history_reads.py::test_purchase_acceptance_lookup_starts_at_the_activity_start` |
| `busy_timeout_back_to_five_seconds` | `worldmock.footprints.store::BUSY_TIMEOUT_SECONDS` | `BUSY_TIMEOUT_SECONDS = 120` | `BUSY_TIMEOUT_SECONDS = 5` | `tests/unit/footprints/test_store.py::test_store_connection_waits_120_seconds_for_a_reader` |
| `status_counts_list_empty_types` | `worldmock.status::event_type_counts` | `if count:` | `if True:` | `tests/unit/footprints/test_status.py::test_status_counts_and_wall_time_equal_the_whole_history_queries` |
| `inspect_summary_ignores_first_occurrence` | `scripts.inspect_run::cmd_summary` | `key=lambda item: (-item[1], item[2])` | `key=lambda item: -item[1]` | `tests/integration/footprints/test_inspect_short_reads.py::test_summary_orders_equal_counts_by_first_occurrence` |
| `inspect_rows_stop_after_one_page` | `scripts.inspect_run::_paged` | `for after in range(0, last, EVENT_PAGE_SIZE):` | `for after in range(0, min(last, EVENT_PAGE_SIZE), EVENT_PAGE_SIZE):` | `tests/integration/footprints/test_inspect_short_reads.py::test_paged_rows_equal_one_whole_history_read` |
| `evaluation_holds_full_events` | `worldmock.evaluation.adapters::_adapt_canonical` | `event_rows = [post_run_view(parse_world_event(row)) for row in iter_jsonl(delivery.event_path)]` | `event_rows = [parse_world_event(row) for row in iter_jsonl(delivery.event_path)]` | `tests/integration/evaluation/test_world_adapter_stream.py::test_world_adapter_keeps_only_post_run_views` |

The existing `location_stays_artifact_not_compared` mutation keeps its original text (the line is unchanged). Chapter
03's `branch_skips_parts` and `branch_rewrites_observations_in_full` entries on `copy_history` are removed from
`MUTATIONS` (their symbol is deleted; the runner refuses a symbol it cannot resolve); the first is replaced above. Not
registered, because they are equivalent today: removing the per-line `validate_activity_prefix` in pass 2 (the
list-level calls inside `reconstruct_activities` still reject), removing the inner `LIMIT` of either subquery, and
dropping the `ORDER BY event_seq` of the branch copy's `SELECT` (SQLite returns rowid order from a rowid range scan; the
trigger would refuse any other order).

### Acceptance for this chapter

The full suite, the mutation suite with the 24 new mutations (and chapter 03's two `copy_history` entries removed), the
coverage gate, the style gate, the import graph and the documentation gate pass; the offline A/B receipts (proof item
6) are in `.work/`.

## Files touched

- `docs/design/05-recording-engine.md`
- `docs/design/07-evaluation.md`
- `docs/design/10-runtime.md`
- `src/worldmock/footprints/store.py`
- `src/worldmock/footprints/post_run.py` (new)
- `src/worldmock/footprints/export.py`
- `src/worldmock/footprints/validation.py`
- `src/worldmock/footprints/checkpoint.py`
- `src/worldmock/footprints/observation_parts.py` (chapter 03's; `copy_parts` deleted)
- `src/worldmock/footprints/timeline_labels.py`
- `src/worldmock/life/engine.py`
- `src/worldmock/life/counterfactual_runtime.py`
- `src/worldmock/life/session_bounds.py`
- `src/worldmock/life/storefront_runtime.py`
- `src/worldmock/life/day_plan_inputs.py`
- `src/worldmock/status.py`
- `src/worldmock/evaluation/io.py`
- `src/worldmock/evaluation/adapters.py`
- `scripts/render_day.py`
- `scripts/inspect_run.py`
- `tests/dataset_acceptance.py`
- `tests/footprints/recording.py`
- `tests/footprints/statement_log.py` (new)
- `tests/unit/engineering/test_single_source.py`
- `tests/unit/footprints/test_observation_parts.py` (chapter 03's)
- `tests/life/observation_record.py` (chapter 03's)
- `tests/integration/runtime/test_output_truncation.py`
- `tests/unit/footprints/test_post_run_view.py` (new)
- `tests/unit/footprints/test_store.py`
- `tests/unit/footprints/test_branch_coverage.py`
- `tests/unit/footprints/test_status.py`
- `tests/unit/footprints/test_ledger.py`
- `tests/unit/footprints/test_purchases.py`
- `tests/unit/life/test_counterfactual_rejection_contract.py`
- `tests/integration/footprints/test_post_run_guard.py` (new)
- `tests/integration/footprints/test_export_stream.py` (new)
- `tests/integration/footprints/test_inspect_short_reads.py` (new)
- `tests/integration/footprints/test_validation.py`
- `tests/integration/footprints/test_verified_experiences.py`
- `tests/integration/footprints/test_render_day.py`
- `tests/integration/footprints/test_acs_export.py`
- `tests/integration/footprints/test_acs_invariants.py`
- `tests/integration/footprints/test_acs_source_guards.py`
- `tests/integration/footprints/test_dataset_recording.py`
- `tests/integration/footprints/test_errand_export.py`
- `tests/integration/footprints/test_goal_export.py`
- `tests/integration/footprints/test_pause_recording.py`
- `tests/integration/footprints/test_refusal_export.py`
- `tests/integration/footprints/test_export.py`
- `tests/integration/life/test_history_reads.py` (new)
- `tests/integration/life/test_content_start_evidence.py`
- `tests/integration/life/test_action_dispatch_contract.py`
- `tests/integration/evaluation/test_world_adapter_stream.py` (new)
- `tests/integration/evaluation/test_goal_metrics.py`
- `tests/integration/evaluation/test_workload_metrics.py`
- `tests/integration/reflection_fixtures.py`
- `tests/integration/runtime/test_conclude_memories.py`
- `tests/integration/runtime/test_current_runtime_contract.py`
- `tests/integration/runtime/test_decision_context_roundtrip.py`
- `tests/integration/runtime/test_env_lifecycle_coverage.py`
- `tests/integration/runtime/test_environment_identity.py`
- `tests/integration/runtime/test_errand_checkpoint.py`
- `tests/integration/runtime/test_grounded_evidence.py`
- `tests/integration/runtime/test_host_failure_stages.py`
- `tests/integration/runtime/test_native_dataset_lifecycle.py`
- `tests/integration/runtime/test_persona_profile.py`
- `tests/integration/runtime/test_query_host_flow.py`
- `tests/integration/runtime/test_read_query_runtime.py`
- `tests/integration/runtime/test_strict_reflection.py`
- `tests/integration/runtime/test_verified_memory.py`
- `tests/life/environment_runtime.py`
- `tests/life/day_baseline_runtime.py`
- `tests/system/lifecycle/test_dayrun_complete_days.py`
- `tests/system/lifecycle/test_external_environment_lifecycle.py`
- `tests/system/lifecycle/test_parcel_arrival_export.py`
- `tests/system/lifecycle/test_selected_environment_lifecycle.py`
- `tests/system/lifecycle/test_sleep_and_reflection_run.py`
- `tests/system/lifecycle/test_run_viewer_recording.py`
- WorldMock-Dataset, coordinated with its own session: `tools/execution.py`, `tests/test_execution.py`

No fixture, manual page, `tools/` file, AgentSociety2 file or patch changes.

## Not in this chapter

| Item | Where |
| --- | --- |
| WAL journal mode | Proposal "Not proposed"; the rollback journal stays (chapter 01's ownership table records the same) |
| Certifying the timeline files inside the export check | Proposal "Not proposed" (changes what the check rejects) |
| Window bounds on plan_day inputs | Dropped by the review and the proposal row |
| The `events_agent_type_kind` index; a deferred json index during restore | Dropped by the review |
| Carried ancestor checkpoints, binding `get_checkpoint` to lineage, deleting parent folders | Checkpoint review 4b; not in the proposal |
| `status.py:39-40` reading checkpoint ids only; compressed checkpoint rows read by `get_checkpoint` | Chapter 02 |
| The `parts` table, `_decode`, `rehydrate_observation`, `copy_history`, the export manifest 7 → 8, `"request": null` | Chapter 03 (this chapter keeps its parts statement and its decoder, and deletes `copy_history`) |
| `lineage` in the branch manifest and export, `transport_attempts`, heartbeat, liveness and `error_message` in status, `_wall_time`'s new signature and `_liveness_view` (which reuse this chapter's `_FIRST_EVENT_SQL`/`_LAST_EVENT_SQL`), `read_last_event`/`read_terminal_event` | Chapter 05 |
| Cost by call class in quality.json (`_call_class`, the post-run reader the request outline keeps) | Chapter 06 |
| The viewer reading an export | Chapter 07 |
| Recorded-response run, comparator, masked complete-day comparison, benchmark kit, backport verification | Chapter 08 |
| `last_agent_action_request_seq` (1–4 ms at day 30, `max()` stops at the first index row), the memory-layer cache (161 MB, 1.6 s cold fill) | Kept as they are (research "keep as is") |
| The dataset finish stage hashing the whole execution root including the run database (66 s at day 30) | Dataset tooling; not in the proposal |
| Storing bodies as JSONB, STORED generated columns, a bigger page cache or mmap | Research "considered and not proposed" |
| Items awaiting the owner (tool parameters, CLI options, dataset fields) | None is touched here |
