# Recorded evidence stored once

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements the proposal section "Recorded evidence" (both of its parts, "Each model request stored once" and
"Observation parts stored once"). It also implements these binding rows of the proposal:
- the "Decisions" row "Observation parts in the export: the export rehydrates";
- the recorded-evidence step of "Implementation order", which comes after the store-and-state step;
- the export manifest bump 7 → 8 named under "Versions" (`request: null`).

**Built on the store chapter.** The implementation order puts [the store commit path](01-store-commits.md) first, so this
chapter is written against the store API that chapter specifies, not against main's:
- `FootprintStore.commit(base, state, events)` is a version step only;
- every same-version write goes through `FootprintStore.append_evidence(events)`;
- `LifeEngine.observe` calls `store.require_committed(...)` before it writes the observation.

Line references still mean main at `0bd2de65`, as the specification's header says. Where chapter 01 has already
changed a cited line, the text says so. If chapter 01 changes these three names in its own review, the integrator
renames them here; the rules of this chapter do not depend on the names.

It follows the reviewer's revisions in `.work/run30/opt/review_events.json`:
- no memo of part digests (the review calls them chunks);
- day parts keyed by `started_at`;
- the writer never mutates the observation it returns;
- `todo_layer` and `goal_bindings` stay inline;
- bare sha256 refs;
- one row decoder;
- `inspect_run` resolves parts;
- `request_sha256` is not written on responses;
- an explanation of why `AssetStore` is not reused.

The dropped options ("Keep one copy of the reasoning text", "Store model_request messages and tool schemas by reference",
"Move the final checkpoint record out of the export manifest") are not specified. They are in the proposal's
"Not proposed" table.

### Measured cost

All figures come from the certified 30-day export of issue #15 (`events.jsonl`, 1,920.4 MB, 46,142 events).

| Recorded content | Bytes over 30 days | Source |
| --- | --- | --- |
| `model_response.data.request`, a copy of the request named in `caused_by` | 463.4 MB (24.1%). All 3,720 copies have the same sha256 as their request event; the 42 plan_day responses already carry `null` | [R] `.work/run30/scratch/opt-events/pass_summary.json`, re-checked at seq 46103 in `opt-review-events/check.txt` |
| Observations | 847.1 MB, 43.7 MB/day at day 30 and rising (quadratic total) | [R] `opt-events/pass_days.tsv` |
| of which `activity_bindings` | 462.5 MB. There are 3,632 distinct rows (0.87 MB); every observation lists every activity ever started (`activity_control.py:182-194`, `lambda item: True`) | [R] |
| of which `persona_views`, `errand_item_bindings`, `memory_layer`, `catalog_bindings`, `locations`, `known_activity_actions` | 113.9, 79.7, 27.1, 20.5, 2.4 and 0.14 MB, with 664, 1, 162, 14, 1 and 1 distinct values | [R] |

### Re-checked for this chapter

The script `.work/run30/scratch/spec-03-recorded-evidence/roundtrip_started_at.py` splits every exported observation in
exactly the form specified below:
- the six whole tables, as one part each;
- `activity_bindings` grouped by the local date of each activity's `started_at`, taken from `activities.jsonl`;
- `todo_layer` and `goal_bindings` inline;
- no memo.

It then rehydrates with a digest check [R]:
- **Round trip.** 3,008 of 3,008 observations are byte-identical to the exported line after rehydration.
- **Sort order.** 0 observations have bindings that are not sorted by `(started_at, activity_id)`, and 0 have a date group
  that is not contiguous.
- **Parts.** 3,467 distinct parts, 41.7 MB in total.
- **Stored observations.** 847.1 MB becomes 145.1 MB, plus the 41.7 MB of parts. That is 660 MB saved in 30 days.
- **The day-30 observation (seq 46100).** 453,429 B becomes 43,124 B, carrying 36 refs (30 day parts and 6 whole tables).

### Expected saving

| | 30 days | 60 days | 90 days |
| --- | --- | --- | --- |
| Request copy removed (fit, `opt-events`) | 463 MB | 1.10 GB | 1.92 GB |
| Observation bytes saved (fit, `opt-events`, includes about 9 MB per 30 days kept inline for `todo_layer` and `goal_bindings`) | 660 MB (measured above) | about 2.3 GB | about 4.8 GB |
| Run database events, before → after | 1.92 → about 0.80 GB | 5.13 → about 1.7 GB | 9.62 → about 2.8 GB |

The quadratic term of event growth disappears. What is left of the observations is about 6 MB/day.

Engine time is about neutral [R]. These timings were taken on the day-30 observation on a machine that was loaded by other
work, so read them as ratios. They are in `time_split.txt` and `time_decode.txt` in the same scratch folder.
- **Observation write.**
  - Today the store encodes the full event once (20.0 ms).
  - After this chapter, the writer computes the start dates (5.8 ms), encodes and hashes every part (23.0 ms), encodes the
    stored event (2.1 ms) and re-hashes the parts on insert (2.3 ms).
  - That is about +13 ms per observation, or about 2 s a day at 146 observations a day.
- **Stored-observation read.**
  - Today `Event.model_validate_json` takes 30.5 ms.
  - After this chapter, a stored read plus parts with digest check takes about 22 ms.
- **`rejected_today`** (`progress_layer.py:305-335`). It decodes every `model_response` since local midnight. That decode
  falls from 0.191 s to 0.020 s per observation at day 30, about 4–9 s a day saved [R] (`opt-events/rejected_cost.txt`).
- **`json_extract`** on observation rows falls from 726 µs to 105 µs per call [R] (`opt-events/time_scratch.txt`).

### Owner's rules

- Every model prompt, decision input and settled world fact stays byte-identical. Only the recorded form changes, and no
  information is lost. See "What stays byte-identical" below.
- No model tool schema, host validation of tool arguments, run-command CLI option or dataset field changes.
- `scripts/inspect_run.py` keeps its options and its output.

## Behaviour

### Each model request stored once

Writer: `ModelEvidenceRecorder.record_model_exchange` (`src/worldmock/life/model_evidence.py:29-77`).

**Recorded form.**
- A `model_request` event (phase `request`) keeps `data.request` = the exchange's request, exactly as today.
- A `model_response` event of phase `response` or `failure` writes `"request": null`. This is the form plan_day evidence
  already uses (`src/worldmock/life/day_planning.py:178-195`, `response_draft` and `failure_draft`, the `None` at `:183`
  and `:192`).
- The key stays present with value `null`. It is not omitted.
- Every other key of `data` is unchanged:
  - `phase`, `call_id`, `attempt`, `response` and `error`;
  - `protocol_feedback`, when the response carries it;
  - `caused_by`, which still holds exactly the request's event id.
- The event body byte form is `canonical_json(Event.model_dump())` as today. The only difference in a response line is
  `"request":null` in place of the request object.

**Pairing check (in-process).**
- At request time, the recorder computes `sha256_json(exchange.request)` (`footprints/models.py:198-200`). It keeps that
  digest next to the request event id in `self.request_events`, which becomes
  `dict[tuple[str, str], tuple[str, str]]` (key `(decision_id, call_id)`, value `(request_event_id, request_sha256)`).
- For a `response` or `failure` exchange, the recorder computes `sha256_json(exchange.request)` and compares it with the
  recorded digest.
- On mismatch it:
  - writes `logging.error("Model %s for call %s carries a request that differs from its recorded request",
    exchange.phase, exchange.call_id)`;
  - raises `ProtocolError("Model exchange request differs from its recorded request")`.
- On mismatch nothing is committed and the key stays in `request_events`. The exception reaches the host as today's
  protocol errors do, and the run stops as a run failure.
- The comparison is by canonical content (sorted keys), so a request that is equal but rebuilt, for example after
  pickling across the Ray actor boundary (`vendor/agentsociety2/.../agentsociety2/env/env_router_proxy.py:130-135`, each
  exchange is pickled when it is sent), is accepted.
- Both digests use the same function on the same kind of object (`exchange.request`, the plain dict AS2 builds), so a
  request that is equal is never refused. The request is plain JSON data on every path: `_model_request`
  (`agent_runtime.py:979-998`), `_assistant_tool_message` (`:859-877`, a dict) and `fit_request`
  (`life/prompt_capacity.py:70-98`, a dict).
- The existing checks keep their order and text:
  - `Model request call was recorded twice`;
  - `Model response has no recorded request`.

**Why the check.** It guards the premise that makes dropping the copy lossless: the response's request is the recorded
request. AS2 passes the same object on all three paths:
- the decision turn (`agent_runtime.py:944-966`, `:1028-1040`);
- the decision statement (`agent_runtime.py:586-620`);
- memory dispatch (`custom/agents/life_agent.py:381-401`).

The dispatcher does not mutate it (`config/llm_dispatcher.py:474-494` pops from its own kwargs copy), and the caller
builds the next turn's messages as a new list (`agent_runtime.py:769`, `:778`, `:856`). In the 30-day run, which sent
every exchange through the Ray actor and so pickled the request separately at request and at response time, 3,720 of
3,720 response copies had the sha256 of their request event [R]. So the check never fires on today's code. A future
library change that mutated the request during dispatch would otherwise go unnoticed.

**No AS2 patch.** AS2 still passes the request in memory; the engine just does not persist it twice.
`AS2_LOCKED_SOURCE_SHA256` and `patches/agentsociety2` are unchanged.

**Readers.** No reader uses the response-side copy (inventory [M], re-checked on this tree):
- `continuation.py:105-117` takes the messages from the `model_request` event, and `:147-172` takes reasoning from
  `response.choices[0].message`.
- `progress_layer.py:305-335` (`rejected_today`) reads `protocol_feedback`.
- `decision_coordinator.py:597-608` reads `response`.
- `store.py:186-200` filters on `event_type='model_request'`.
- `quality.py:583-643`, `:800-808` and `:872-884` read `call_id`, `purpose`, `response` and `protocol_feedback`.
- `behavior_metrics.py:239-249` tries `payload.get("request")` for usage. A dict-shaped request never carries `usage`, so
  `null` gives the same result. Its other request reads (`:157-160`, `:189`) take only `model_request` events.
- `status.py:86-99` reads response model and usage.
- `scripts/inspect_run.py:526-600` takes request messages from request rows.
- `tools/run-viewer-context-view.js:14-56` and `:170-201`, and `tools/run-viewer-context.js:304-305` (`parseRequest`,
  given the `model_request` event), take the request from the `model_request` event.
- `evaluation/adapters.py:62-67` classifies by event type.
- Export validation does not read it.
- No test reads it: every `data["request"]` read in `tests/` is on a `model_request` or `action_requested` event
  (`grep -rn 'data\["request"\]' tests`).

**Export validation adds no rule** for `request` on responses. The writer is the only producer, and its tests pin the form.

### Observation parts stored once

#### Concept and name

- **Chinese name.** The new concept is 「观察分件」.
- **English name.** It is "observation part" in English. The table is `parts`, the module is `observation_parts`, and one
  date group of `activity_bindings` is a "day part".
- **Meaning.** An observation part is one table, or one date group of `activity_bindings` rows, that the store keeps
  once, keyed by the sha256 of its canonical JSON text. The stored observation carries only those sha256 strings.
- **Not an asset.** It is not 素材 (asset, `AssetRef`, `AssetStore`) and must never be called so. See "Why `AssetStore`
  is not reused".
- **No second name.** No other word ("chunk", "blob", "piece") names it in code, logs, errors or design text.

#### Stored form

| Observation key | Stored value |
| --- | --- |
| `activity_bindings` | A JSON array of sha256 strings, one per day part, in row order. Each day part is a maximal run of consecutive rows whose activities share the same local date (`readable.local_date(activity.started_at, config.timezone)`). An empty table is stored as `[]` |
| `catalog_bindings`, `errand_item_bindings`, `known_activity_actions`, `locations`, `memory_layer`, `persona_views` | One sha256 string each (the whole table is one part) |
| Every other key | Inline and unchanged. This includes `todo_layer`, `goal_bindings`, `tool_layer`, `interaction_bindings`, `errand_bindings`, `body`, `verified_experiences`, `progress_event_ids`, `active_activities` and `wake_reasons` |

- **Row order.** `activity_bindings` rows are built in the order of `visible_names` (`observe.py:118-123`), which sorts by
  `(started_at, activity_id)` (`activity_control.py:480-485`). So each date is one contiguous run. Grouping consecutive
  equal dates (`itertools.groupby`) is lossless whatever the order, because rehydration concatenates the day parts in
  stored order. The order only decides how much is shared between observations.
- **Bare strings.** Refs are bare 64-character lowercase hex strings, never dict markers. A reader that bypasses
  rehydration gets a `str` where it expects a dict or a list of dicts, and fails loudly: `str` has no `.get`, and
  `row["name"]` on a character raises `TypeError`. It never silently reads empty data.
- **Byte form of a part.**
  - `body` is the TEXT `canonical_json(value).decode()`, where `canonical_json` is `footprints/models.py:189-195`
    (`json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`).
  - `sha256` is `hashlib.sha256(body.encode("utf-8")).hexdigest()`.
  - For a day part, `value` is the list of the group's binding rows. Each row is
    `{"activity_id", "name", "note_labels", "phase", "resumable", "title"}`, as built at `observe.py:118-123`.
  - For a whole part, `value` is the table itself.
- **Byte form of the stored observation event.** The body is `canonical_json(event.model_dump()).decode()` as today, with
  `data.activity_bindings` = an array of sha256 strings and the six keys = sha256 strings.

  Example (seq 46100, abbreviated):

  ```
  "data":{"active_activities":[...],"activity_bindings":["3f1c…","9a02…",…30 items],"agent_id":1,
  "balance_fen":…,"body":{…},"catalog_bindings":"5be0…","current_time":…,"errand_bindings":[…],
  "errand_item_bindings":"c41d…",…}
  ```

#### Table

`store.SCHEMA` (`src/worldmock/footprints/store.py:20-38`) gains:

```sql
CREATE TABLE parts(sha256 TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TRIGGER parts_no_update BEFORE UPDATE ON parts BEGIN SELECT RAISE(ABORT,'Observation parts are immutable'); END;
CREATE TRIGGER parts_no_delete BEFORE DELETE ON parts BEGIN SELECT RAISE(ABORT,'Observation parts are immutable'); END;
```

- **Table type.** It is an ordinary rowid table, not `WITHOUT ROWID`, because SQLite advises against `WITHOUT ROWID` for
  rows larger than about 1/20 of a page, and day parts and `errand_item_bindings` are tens of KB.
- **Inserts.** They use `INSERT OR IGNORE`, which skips an existing key without an update, so the triggers never fire on
  the normal path.

#### Writer

1. **Build.** `LifeEngine.observe` (`life/engine.py:75-93`) builds `data` exactly as today with `_observation_data`.
2. **Split.** It calls `ObservationBuilder.stored_observation(data)` (new, `life/observe.py`), which:
   - builds `kept` = a new dict of `data` without the host-only keys `progress_layer` and `progress_meta`. This is the
     literal set that is at `engine.py:79` today, moved to `observe.py` as `_HOST_ONLY_KEYS`.
   - computes `start_dates`, one local date per `activity_bindings` row, from
     `self.context.state.activities[row["activity_id"]].started_at` (an `int`, `life/models.py:356`) and
     `self.context.config.timezone`. It reads the activities the same way `_name_bindings` reads them
     (`observe.py:115-121`). If the state chapter changes how activities are held, both sites follow that access.
   - returns `split_observation(kept, start_dates)`.
3. **What `split_observation` returns.** It returns a new dict, `{**kept, "activity_bindings": [...], <six keys>: ...}`,
   and a mapping `parts: dict[str, str]` (sha256 → body text) holding every part of this observation, including those
   already stored.
4. **Nothing reused across observations.** Every part is encoded and hashed on every observation. There is no memo by
   date and no cache of digests: a past date's part changes when an activity started that day changes phase or
   `resumable`, is renamed with 「（第N次）」 because a later goal or binding takes its name (`visible_names`,
   `activity_control.py:182-194`), or gains a `note_labels` entry.
5. **Never mutate.** The writer never mutates `data` or any container inside it:
   - `kept` and the stored dict are new dicts;
   - the inline values are the same objects, untouched;
   - parts are fresh encodings;
   - `observe` still returns `data` (plus `observation_event_id` and the continuation, as today), and the host renders the
     prompt from it.
6. **Compare, then write.**
   - `observe` keeps chapter 01's compare
     `self.context.store.require_committed(self.context.state.model_dump(mode="json"))` immediately before the write,
     so it still runs after the observation (and now its split) was built and before anything is written.
   - `observe` then calls `self.context.store.append_observation(draft, parts)` with `draft.data = stored`. That call
     returns the observation's event id, which `observe` stores in `data["observation_event_id"]` as today.
   - `append_observation` opens one transaction (`with self.connection:`), inserts the parts with `insert_parts` as its
     first statement, and then calls chapter 01's `append_evidence([draft])` inside that transaction.
     `append_evidence`'s own `with self.connection:` block does not start a second transaction (Python's `sqlite3`
     begins one implicitly at the first data-modifying statement and the context manager only commits or rolls back).
     So:
     - when `append_evidence` succeeds, its commit commits the parts and the observation together;
     - when it refuses inside its block (an insert or `_require_stored_version` fails), its rollback removes the parts
       too;
     - when it refuses before its block (`_build_events`: time order, a missing cause, a damaged asset), the outer block
       rolls the parts back.
   - Checked on Python 3.12's `sqlite3` with the same nesting
     (`.work/run30/scratch/spec-review-03-recorded-evidence/nested_tx.py`): all three cases leave no part and no event,
     and dropping the outer block leaves the parts in an open transaction when the refusal comes before the inner block.
   - `insert_parts` checks each body's sha256 against its key before inserting.

#### Store write-side checks

- **Refs must resolve.** `_insert_event` is the single place that writes an event row on chapter 01's version-step,
  evidence-append and checkpoint paths. For every event with `event_type == "observation"`, it calls
  `require_parts(self.connection, event.data)` before inserting the row, which:
  - parses the refs of the keys the observation carries (shape errors raise
    `Stored observation part reference is invalid`);
  - checks that each ref is a row of `parts` (otherwise `Observation part is missing`).
- **Generic writers.** `append_evidence` called directly, or `commit`, may therefore write an observation only when its
  parts are already stored. An observation that carries a full table at a part key is refused at write, not at the first
  read.
- **No key requirement in the store.** The store does not require the seven keys. An observation event that carries none
  of them (only test fixtures write such events) has nothing to resolve or rehydrate. Observations written by the engine
  always carry all seven, because `split_observation` refuses data without them.

#### Reader: one row decoder

`FootprintStore._decode(body: str) -> Event` is new. It is the only place that turns an `events.body` row into an `Event`,
and every read method uses it:
- `read_event` (`store.py:246`);
- `read_agent_events` (`:256`);
- `read_decision_observations` (`:268`);
- `read_recent_experience_sources` (`:283`);
- `read_events` (`:290`);
- `get_checkpoint` (`:352`).

What `_decode` does:
- It validates with `Event.model_validate_json(body)` exactly as today.
- For an observation, it returns `event.model_copy(update={"data": rehydrate_observation(event.data, self.connection)})`.
- `rehydrate_observation` returns a new dict whose keys are in the same order as the stored dict (`dict(data)` followed by
  in-place value replacement). The values are parsed freshly from `parts` on every call, with no cache, after checking
  that each body hashes to its ref.
- So every reader gets the same values, in the same key order, as the full-body decode gives today:
  - **Same text.** Canonical JSON is compositional (sorted keys and the same separators at every depth), so a part's
    body is exactly the text that today's stored body carries for that table, or the concatenation of its rows.
  - **Same order.** The stored body is canonical, so the top-level keys come back sorted as today; inside a part,
    `json.loads` of canonical text gives sorted keys, as pydantic's parse of the full body does today.
  - **Same values.** Today the whole body is parsed by pydantic-core; now the parts are parsed by `json.loads`. Both
    give `int` for JSON integers and the correctly rounded `float` otherwise: on 499,913 random finite doubles
    (`.work/run30/scratch/spec-review-03-recorded-evidence/float_parse.py`) the two parsers gave 0 differences in value
    or type.

Who reads observations through the decoder:
- the frozen observation (`start_group.py:67-72`, through `session_actions.py:53`, `:224`, `:238`, `:285`, `:310`,
  `:358`, `:489-503`, `errands.py:360`, `name_resolution.py:164-170` and `decision_coordinator.py:557-560`);
- `read_query` (`custom/envs/life_env.py:161-167`);
- `memory_evidence` (`model_evidence.py:150-157`);
- `continuation.py:76`;
- restore, export, `fail_run` and the counterfactual check (`read_events`).

Readers that need no decoding:
- the envelope-only raw reads `_last_event_time` (`store.py:163-166`), `last_event_seq_before` (`:202-217`) and
  `commit_last_seq` (`:219-235`);
- the SQL predicates on envelope fields: the `events_agent_type` index (`store.py:24`) and the `WHERE` clauses at
  `:180-183`, `:253-254`, `:263-267` and `:277-281`; `:192-199` reads inline keys of `model_request` events.

Invariant for this and later chapters: no SQL predicate, `json_extract` or `json_each` may read inside the seven part keys
of an observation. Every present predicate reads envelope fields or inline keys. Checked with
`grep -rn 'data\.\(activity_bindings\|catalog_bindings\|errand_item_bindings\|known_activity_actions\|locations\|memory_layer\|persona_views\)' src scripts tools custom`:
no match. The observation fields the viewer and `inspect_run.py` read in SQL (`$.data.balance_fen`, `$.data.body…`,
`$.data.active_activities`, `$.data.calendar_context.date`) are inline.

#### Branch

`restore_branch` (`footprints/checkpoint.py:93-114`) keeps `events = store.read_events(checkpoint.last_event_seq)`.
- That call decodes every parent event through `_decode`, so the restore re-validation also checks every observation's
  parts in the parent.
- It keeps using those events for artifact refs and `source_run_ids`.

`_populate_branch` (`:117-143`) stops re-inserting decoded events (`:133-135`), which would write full observations into
the child. Inside `with child.connection:` it calls `child.copy_history(store, events)`, which:
- copies the parent's whole `parts` table as it is (`copy_parts`);
- writes each event of the prefix with the parent's stored body text (`SELECT body FROM events WHERE event_seq=?`), so
  observations keep their refs;
- indexes the event's artifact refs, as `_insert_event` does.

**Whole table, not only referenced parts.** Parts that only the discarded tail references come along. They are harmless:
- no reader enumerates `parts`;
- the export does not ship parts;
- a child observation can only point at a part by building the same content.

**Same bytes for other events.** Non-observation bodies are byte-identical to what `_insert_event(event)` writes today:
the stored body is `canonical_json(model_dump())`, and a decode followed by a dump is the identity.

**Rest of `_populate_branch` unchanged.** The `branch_created` version step after the copy stays chapter 01's
`child.commit(checkpoint.state, state, [...])` (`checkpoint.py:138` today).

**Whole-history chapter.** It replaces the event copy with its ATTACH + `INSERT … SELECT` copy. It must keep the
statement `INSERT INTO main.parts SELECT sha256, body FROM <parent>.parts`, keep the streamed per-event re-validation
through `_decode`, and then delete `copy_history` if nothing else calls it.

#### Export

`export_run` (`footprints/export.py:44-62`) reads `store.read_events(cutoff)`, which goes through `_decode`.
- `rows = [event.model_dump() ...]` (`:71`) therefore holds full observations, and every `events.jsonl` observation line
  is byte-identical to today's.
- `parts` is not an export artifact, and `EXPORT_ARTIFACTS` is unchanged.
- No export consumer changes: `validation.py`, `evaluation/*`, `errand_metrics.py:176-181`, `:245-250`, LabelKit and the
  dataset tools. The two version checks (`footprints/validation.py:86`, `evaluation/validation.py:257`) import
  `EXPORT_SCHEMA_VERSION`, so they follow the bump without an edit. WorldMock-Dataset's `tools/execution.py:217-237`
  exports and checks through these same functions.
- The export code itself changes only in `EXPORT_SCHEMA_VERSION = 8`, because model response lines now carry
  `"request":null`. As the design already says (「结构号不符的导出直接拒绝，不迁移」), the new code refuses existing schema-7
  exports; nothing converts them.
- The whole-history chapter's streaming export must decode each row through `FootprintStore._decode`. It must not
  decode with `Event.model_validate_json` alone.

#### `scripts/inspect_run.py`

- **What changes.** `event <seq>`, and `events` both with and without `--full`, print the rehydrated observation:
  - `cmd_event` (`:184-187`) and `cmd_events` (`:119-136`) pass each row through a new
    `_rehydrated(conn, row)`, which applies `rehydrate_observation(row["data"], conn)` to observation rows only.
  - Their output is byte-identical to today's, both the one-line brief from `_brief(...)[:90]` and the `--full` JSON.
- **Why not in `_rows`.** `_rows` (`:77-83`) stays a raw reader. The other subcommands read no part key:
  - `body` reads `data.body`;
  - `rerun` reads `data.body.levels.appetite` through `_select`;
  - `continuation` and `workday` read other event types.

  Rehydrating there would only add cost.
- **Failure.** A missing or damaged part makes the command exit with the store's `IntegrityError`.

#### Viewer

No change in this chapter:
- today's viewer reads observation fields `$.data.balance_fen` and `$.data.body…` (`run-viewer.html:481-490`), which are
  inline;
- the raw detail pane (`fetchBody`/`prettyBody`, `:813-833`) would show refs when opened on a run database;
- the viewer chapter makes the viewer read the export only, where observations are rehydrated;
- both chapters land in one integration branch, so no released state shows refs.

#### Why `AssetStore` is not reused

WorldMock already stores content by hash: `AssetStore` (`footprints/assets.py:40-72`), `Event.artifact_refs`
(`models.py:142`), the `artifacts` index (`store.py:27`, `:170-173`), the branch copy (`checkpoint.py:146-157`) and the
export's `assets/` with `asset-index.jsonl`.

It does not fit observation parts:
- **Separate files.** Each asset is a separate file published with `fsync` (`atomic_write`, `assets.py:15-38`). An
  observation has about 36 parts at day 30, so a write would cost up to about 36 file syncs per decision, against one
  SQLite transaction.
- **Outside the transaction.** Assets are written before and outside the SQLite transaction (see `engine.py:180` before
  `store.checkpoint`). A failed observation commit would leave files behind, while parts must commit or roll back with
  their observation.
- **Re-read everywhere.**
  - Every commit re-reads and re-hashes each referenced asset file (`store.py:137-138`).
  - Restore re-reads every event's refs (`checkpoint.py:102-104`).
  - Export rewrites each asset file once per referencing event (`export.py:93-97`).

  With about 3,000 observations and about 36 refs each, that is about 100,000 file operations per restore or export.
- **Wrong reference type.** `AssetRef` (`models.py:110-116`) carries `media_type` and `source`, which a part does not
  have.
- **Exported as assets.** Asset refs live in the event envelope and are exported as assets. The decision here is that the
  export rehydrates.

So parts are rows in the run database, in the observation's transaction, referenced by bare sha256. The name 「观察分件」
keeps the two concepts apart, as AGENTS.md asks (one concept, one name).

### What stays byte-identical, and why

**Model prompts.**
- The host builds every user layer and its local name book from the in-memory dict `observe()` returns:
  - `custom/agents/life_agent.py:438-452`, which receives the observation through `request.observations`;
  - `:667-675`;
  - `_persona_views` (`:695`);
  - `_visible_names` (`:732-748`).
- It never builds them from the stored event.
- Same-second continuation rebuilds messages from `model_request` events and response messages
  (`continuation.py:105-117`, `:147-172`), which are unchanged.
- `rejected_today` reads `protocol_feedback`, which is unchanged.
- So every prompt is byte-identical.

**Decision inputs and settled facts.**
- Every environment-side reader of a stored observation (frozen observation, name resolution, refusal candidates,
  `recall_task_summary`, errand checks, memory-update checks) receives `_decode`'s rehydrated dict.
- That dict has the same values in the same key order as the full-body decode today. Shown by:
  - construction: the round trip of canonical JSON, and `dict` order preserved;
  - the 3,008/3,008 re-check above;
  - the tests below.
- So settlement inputs and settled facts are identical.

**World state and checkpoints.** No state field is touched.

**Recorded form only.**
- `model_response.data.request` becomes `null`. The information is stored once in `model_request`, and the digest check
  proves equality in process.
- The observation row stores refs, with the parts in `parts`.
- `events.jsonl` observation lines and `quality.json` are byte-identical. `quality.json` reads `call_id`, `purpose`,
  `response`, `protocol_feedback`, `verified_experiences` and `progress_event_ids` only.
- Response lines change only in `data.request`.

**The full binding set is kept.** Every activity ever started stays in `activity_bindings`, with its recallable name and
「第二次」 numbering. Recall and name resolution are unchanged.

### Failure handling

Every Python branch below writes the English error log shown, then raises. The exception type's constructor also logs
`WorldMock failure: <message>` (`errors.py:10-13`).

| Where | Condition | `logging.error` text | Exception |
| --- | --- | --- | --- |
| `split_observation` | `data` lacks `activity_bindings` or one of the six keys | `Observation lacks the tables stored as parts: %s` (the list of missing keys) | `IntegrityError("Observation lacks a table stored as a part")` |
| `split_observation` | `len(start_dates) != len(data["activity_bindings"])` | `Observation start dates (%d) do not match its activity bindings (%d)` | `IntegrityError("Observation start dates do not match its activity bindings")` |
| `insert_parts` | a body's sha256 differs from its key | `Observation part %s does not match its sha256` | `IntegrityError("Observation part content does not match its sha256")` |
| `require_parts`, `_part_value` | a ref is not a row of `parts` | `Observation part %s is missing` | `IntegrityError("Observation part is missing")` |
| `_part_value` | a stored body's sha256 differs from its ref | `Observation part %s does not match its sha256` | `IntegrityError("Observation part content does not match its sha256")` |
| `_day_refs`, `_whole_ref` (through `_part_refs`, `rehydrate_observation`) | a present part key does not hold a sha256 (or, for `activity_bindings`, a list of sha256) | `Stored observation key %s carries an invalid part reference` | `IntegrityError("Stored observation part reference is invalid")` |
| `FootprintStore.append_observation` | the draft is not an observation | `Observation append received a %s event` | `IntegrityError("Observation append requires an observation event")` |
| `FootprintStore.append_observation` | any exception inside its transaction (a part check, `append_evidence`'s refusals, SQLite) | `logging.exception("Observation append failed; no observation or new part was committed")` | the original exception, re-raised |
| SQLite trigger | `UPDATE` or `DELETE` on `parts` | (none; SQLite) | `sqlite3.IntegrityError: Observation parts are immutable` |
| `ModelEvidenceRecorder._request_cause` | response or failure request digest differs from the recorded one | `Model %s for call %s carries a request that differs from its recorded request` | `ProtocolError("Model exchange request differs from its recorded request")` |

When these errors stop the run:
- **Inside `observe`.** An `IntegrityError` raised there is a run failure, as every store integrity error is today. The
  transaction is rolled back, so no part and no observation is committed.
- **On a later read.** An error raised while reading during settlement or a query aborts the round and fails the run,
  exactly as a damaged event body does today.
- **No fallback.** No branch falls back to an inline table or skips a part.

### What is deleted

- **The request copy.** The response-side copy of the request (`model_evidence.py:54`, `"request": exchange.request` for
  every phase).
- **The host-only key literal.** The literal at `engine.py:79`, which moves to `observe.py:_HOST_ONLY_KEYS`.
- **Inline decodes.** The six inline `Event.model_validate_json(row[0])` decodes in `store.py`, replaced by `_decode`.
- **Branch re-insert.** The decoded-event re-insert loop of `_populate_branch` (`checkpoint.py:133-135`).
- **Old mutation.** The mutation `export_schema_version_still_six`, replaced by `export_schema_version_still_seven`.
- **No compatibility for older runs.**
  - A run database written before this chapter has no `parts` table, and stores full tables where refs are expected.
  - It is rejected when opened, by the world-state structure version the state chapter bumps (`life/models.py:570`).
  - Even alone, it fails loudly: on read, a full table at a part key raises `Stored observation part reference is
    invalid`; on write, `no such table: parts` is logged by `Observation append failed…` and re-raised.
  - This includes `inspect_run.py event` and `events` on such a database, and `worldmock export` of it: both stop with
    `Stored observation part reference is invalid`. Their existing exports stay readable as files, but the new export
    check refuses them by schema version (see "Export").

## Interfaces and files

### `src/worldmock/footprints/observation_parts.py` (new, about 140 lines)

This is the single implementation of the observation-part form. The `parts` DDL stays in `store.SCHEMA`.

```python
"""@brief 观察分件：观察里按内容单独存放一次的大表与按开始日期分段的活动绑定，观察正文只带它们的 sha256。

存储形式与规则见 docs/design/05-recording-engine.md「事件与来源」；parts 表的建表语句在 store.SCHEMA，
本模块是拆分、登记、核对、换回与复制观察分件的唯一实现。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import operator
import sqlite3
from typing import Any, NoReturn

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json, is_sha256

#: 整表各存为一个观察分件的观察键。
WHOLE_PART_KEYS = ("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations",
                   "memory_layer", "persona_views")
#: 按所指活动开始时刻的本地日期分段、每段存为一个观察分件的观察键。
DAY_PART_KEY = "activity_bindings"


def split_observation(data: dict[str, Any], start_dates: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    """@brief 观察的存储形式与它的观察分件；不修改 data 及其中任何容器。

    activity_bindings 按行序把开始日期相同的相邻行归为一段，每段一个分件，存为分件 sha256 的列表；
    WHOLE_PART_KEYS 各整表一个分件，存为一个 sha256；其余键原样保留（同一对象）。每次都对全部分件重新编码求摘要。

    :param data: 要写入的观察（已去掉只给宿主的键）。
    :param start_dates: 与 data["activity_bindings"] 逐行对应的所指活动开始本地日期。
    :returns: (存储形式的观察, 观察分件 sha256 → 规范 JSON 正文)。
    :raises IntegrityError: 缺少要存为分件的表，或开始日期与绑定行数不同。
    """
    missing = [key for key in (DAY_PART_KEY, *WHOLE_PART_KEYS) if key not in data]
    if missing:
        logging.error("Observation lacks the tables stored as parts: %s", missing)
        raise IntegrityError("Observation lacks a table stored as a part")
    rows = data[DAY_PART_KEY]
    if len(start_dates) != len(rows):
        logging.error("Observation start dates (%d) do not match its activity bindings (%d)",
                      len(start_dates), len(rows))
        raise IntegrityError("Observation start dates do not match its activity bindings")
    parts: dict[str, str] = {}
    groups = itertools.groupby(zip(start_dates, rows), key=operator.itemgetter(0))
    day_refs = [_put([row for _, row in group], parts) for _, group in groups]
    whole = {key: _put(data[key], parts) for key in WHOLE_PART_KEYS}
    return {**data, DAY_PART_KEY: day_refs, **whole}, parts


def rehydrate_observation(data: dict[str, Any], connection: sqlite3.Connection) -> dict[str, Any]:
    """@brief 把存储形式观察的分件引用换回原表，返回键序不变的新字典；data 不变。不缓存，每次从 parts 表读并核对摘要。

    :raises IntegrityError: 引用不是 sha256、分件缺失或正文的 sha256 与引用不符。
    """
    result = dict(data)
    if DAY_PART_KEY in data:
        result[DAY_PART_KEY] = [row for ref in _day_refs(data) for row in _part_value(connection, ref)]
    for key in WHOLE_PART_KEYS:
        if key in data:
            result[key] = _part_value(connection, _whole_ref(data, key))
    return result


def insert_parts(connection: sqlite3.Connection, parts: dict[str, str]) -> None:
    """@brief 在调用方事务内登记观察分件：先核对每个正文的 sha256，已存在的同一分件不重写。

    :raises IntegrityError: 某个正文的 sha256 与它的键不符。
    """
    for digest, body in parts.items():
        if hashlib.sha256(body.encode()).hexdigest() != digest:
            logging.error("Observation part %s does not match its sha256", digest)
            raise IntegrityError("Observation part content does not match its sha256")
    connection.executemany("INSERT OR IGNORE INTO parts VALUES(?,?)", parts.items())


def require_parts(connection: sqlite3.Connection, data: dict[str, Any]) -> None:
    """@brief 写观察之前核对它引用的每个观察分件都已在 parts 表。

    :raises IntegrityError: 引用形状不对，或分件不在表中。
    """
    for ref in _part_refs(data):
        if connection.execute("SELECT 1 FROM parts WHERE sha256=?", (ref,)).fetchone() is None:
            logging.error("Observation part %s is missing", ref)
            raise IntegrityError("Observation part is missing")


def copy_parts(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    """@brief 在 target 的调用方事务内原样复制 source 的整张 parts 表（分支用）。"""
    target.executemany("INSERT INTO parts VALUES(?,?)",
                       source.execute("SELECT sha256,body FROM parts ORDER BY sha256"))


def _part_refs(data: dict[str, Any]) -> list[str]:
    """@brief 存储形式观察带的全部分件引用：先 activity_bindings 的各段，再按 WHOLE_PART_KEYS；不带这些键的观察为空。"""
    refs = list(_day_refs(data)) if DAY_PART_KEY in data else []
    return refs + [_whole_ref(data, key) for key in WHOLE_PART_KEYS if key in data]


def _put(value: Any, parts: dict[str, str]) -> str:
    """@brief 把一张表或一段行登记为观察分件，返回其 sha256。"""
    body = canonical_json(value)
    digest = hashlib.sha256(body).hexdigest()
    parts[digest] = body.decode()
    return digest


def _day_refs(data: dict[str, Any]) -> list[str]:
    """@brief activity_bindings 的分段引用列表。"""
    value = data[DAY_PART_KEY]
    if not isinstance(value, list) or not all(is_sha256(item) for item in value):
        _invalid_reference(DAY_PART_KEY)
    return value


def _whole_ref(data: dict[str, Any], key: str) -> str:
    """@brief 整表存为一个观察分件的引用。"""
    if not is_sha256(data[key]):
        _invalid_reference(key)
    return data[key]


def _invalid_reference(key: str) -> NoReturn:
    """@brief 存储形式观察的某个键不是分件引用：记英文错误并拒绝。"""
    logging.error("Stored observation key %s carries an invalid part reference", key)
    raise IntegrityError("Stored observation part reference is invalid")


def _part_value(connection: sqlite3.Connection, ref: str) -> Any:
    """@brief 读一个观察分件并核对摘要，返回解析出的表或那一段行。"""
    row = connection.execute("SELECT body FROM parts WHERE sha256=?", (ref,)).fetchone()
    if row is None:
        logging.error("Observation part %s is missing", ref)
        raise IntegrityError("Observation part is missing")
    if hashlib.sha256(row[0].encode()).hexdigest() != ref:
        logging.error("Observation part %s does not match its sha256", ref)
        raise IntegrityError("Observation part content does not match its sha256")
    return json.loads(row[0])
```

- **Mutation anchors.** The exact source lines above are mutation anchors (see Tests). Keep them literally.
- **`is_sha256`.** It is the existing `footprints/models.py:203-205`.
- **Parsing.** `json.loads` is used for part bodies. Measured on the day-30 parts, `pydantic_core.from_json` was not
  faster (14.95 ms against 13.83 ms, `time_decode.txt`).

### `src/worldmock/footprints/store.py` (365 lines today, about 425 after chapter 01)

`commit`, `append_evidence`, `require_committed`, `_build_events` and `checkpoint` are as chapter 01 leaves them; this
chapter does not edit their bodies, so chapter 01's mutation anchors in them stay valid.

| Symbol | Today | Change |
| --- | --- | --- |
| `SCHEMA` | `:20-38` | Add the `parts` table after `artifacts` (`:27`) and the two triggers after the checkpoint triggers (`:34-37`), text as in "Table" above |
| imports | `:15-18` | Add `from worldmock.footprints.observation_parts import copy_parts, insert_parts, rehydrate_observation, require_parts` |
| `append_observation(self, draft: EventDraft, parts: dict[str, str]) -> str` | new, after chapter 01's `append_evidence` | Code below |
| `copy_history(self, parent: FootprintStore, events: list[Event]) -> None` | new | `copy_parts(parent.connection, self.connection)`, then for each event: `row = parent.connection.execute("SELECT body FROM events WHERE event_seq=?", (event.event_seq,)).fetchone()`, `self._index_artifacts(event)`, `self.connection.execute("INSERT INTO events VALUES(?,?,?)", (event.event_seq, event.event_id, row[0]))`. @brief: 「分支：在调用方事务内原样复制父库整张观察分件表，再按父库存储原文写入已解码复核的事件前缀（观察仍带引用）并登记素材索引。」 |
| `_insert_event(self, event: Event) -> None` | `:168-176` | Signature unchanged (tests monkeypatch it). Calls `self._index_artifacts(event)`; if `event.event_type == "observation"`, calls `require_parts(self.connection, event.data)`; then inserts the row as today. @brief: 「在调用方事务内插入事件和素材索引；观察引用的每个观察分件须已在 parts 表。」 |
| `_index_artifacts(self, event: Event) -> None` | new (lines `:170-173` today) | The artifact-index loop moved out of `_insert_event`. @brief: 「在调用方事务内登记事件引用的素材索引。」 |
| `_decode(self, body: str) -> Event` | new | `event = Event.model_validate_json(body)`; `if event.event_type != "observation": return event`; `return event.model_copy(update={"data": rehydrate_observation(event.data, self.connection)})`. @brief: 「事件行的唯一解码，每个读取方法都经过它：校验事件；观察把分件引用换回原表，读者拿到写入前的完整观察；不缓存。」 |
| `read_event`, `read_agent_events`, `read_decision_observations`, `read_recent_experience_sources`, `read_events`, `get_checkpoint` | `:246`, `:256`, `:268`, `:283`, `:290`, `:352` | Replace `Event.model_validate_json(row[0])` (and `event[0]` at `:352`) with `self._decode(...)`. Signatures and @briefs unchanged |

`append_observation`:

```python
    def append_observation(self, draft: EventDraft, parts: dict[str, str]) -> str:
        """@brief 同版本写入一条存储形式的观察，并在同一事务登记它的观察分件（已有的同一分件不重写），返回观察的事件身份。

        外层事务先登记分件，再经 append_evidence 写观察：append_evidence 的事务提交时分件与观察一起提交；它在写入之前或
        写入之中拒绝时分件随之回滚，库里不留下没有观察引用的新分件（docs/design/05-recording-engine.md「事件与来源」）。
        @param draft data 为 split_observation 给出的存储形式的观察
        @param parts 观察分件 sha256 → 规范 JSON 正文（split_observation 的第二个返回值）
        @return 观察的事件身份
        @exception IntegrityError 不是观察事件、分件正文与摘要不符，或 append_evidence 的任一拒绝
        """
        if draft.event_type != "observation":
            logging.error("Observation append received a %s event", draft.event_type)
            raise IntegrityError("Observation append requires an observation event")
        try:
            with self.connection:
                insert_parts(self.connection, parts)
                return self.append_evidence([draft])[0].event_id
        except Exception:
            logging.exception("Observation append failed; no observation or new part was committed")
            raise
```

- **Why it wraps `append_evidence`.** The observation keeps every check of chapter 01's evidence append (no money, the
  reference, time, observation-interval and asset checks, the stored-version check) without a second copy of them, and
  chapter 01's mutation anchors inside `append_evidence` keep covering the observation path. "Writer" above gives the
  transaction argument.
- **The generic evidence append writes no parts.** No other caller changes; `append_evidence` keeps chapter 01's
  signature.

### `src/worldmock/footprints/checkpoint.py`

- **`_populate_branch`** (`:117-143`). Lines `:133-135` become:

  ```python
          with child.connection:
              child.copy_history(store, events)
  ```

  Nothing else in the function changes.
- **`restore_branch`** (`:93-114`). Unchanged. `store.read_events` now decodes through `_decode`.

### `src/worldmock/footprints/export.py`

`:30-31` becomes:

```python
#: 导出清单的结构号：工件是六份，含地点停留；活动种类含查询；进行中的 interact 活动行带 played_count；
#: 模型响应与失败事件的 request 为 null（请求只在请求事件存一次）；结构号不符的导出直接拒绝，不迁移。
EXPORT_SCHEMA_VERSION = 8
```

Nothing else in `export.py` changes.

**The bump is made once.** This chapter makes the 7 → 8 bump. The network chapter's lineage field is added under the same
version 8, without a second bump.

### `src/worldmock/life/model_evidence.py` (164 lines today)

- **Imports.** Add `sha256_json` to the import from `worldmock.footprints.models` (`:13`).
- **`__init__`** (`:24-27`).
  - `self.request_events: dict[tuple[str, str], tuple[str, str]] = {}`.
  - Comment: `#: (决策身份, 调用身份) → (请求事件身份, 请求规范 JSON 的 sha256)；响应或失败登记后移除。`
- **`record_model_exchange(self, exchange: Any) -> None`** (`:29-77`). The signature is unchanged. The @brief adds:
  「请求只存在请求事件里；响应与失败事件的 request 为 null，经 caused_by 指向请求事件，并须带来与登记时摘要相同的请求
  （docs/design/05-recording-engine.md「事件与来源」）。」

  Body:

  ```python
          key = (context.decision_id, exchange.call_id)
          digest = sha256_json(exchange.request)
          cause = self._request_cause(key, exchange, digest)
          data = {
              "phase": exchange.phase, "call_id": exchange.call_id, "attempt": exchange.attempt,
              "request": exchange.request if exchange.phase == "request" else None,
              "response": exchange.response, "error": exchange.error,
          }
          # protocol_feedback, draft, decision start and the store call stay as at :56-72
          if exchange.phase == "request":
              self.request_events[key] = (event.event_id, digest)
          else:
              self.request_events.pop(key)
  ```

  This replaces `:44-55` and `:73-76`. The phase/attempt validation (`:37-43`), the store call (chapter 01's
  `event = self.context.store.append_evidence([item for item in (start, draft) if item is not None])[-1]`, which replaces
  `:71-72`) and `_ops_exchange` are unchanged. The digest is computed after the phase/attempt validation and before the
  store call, so a refused exchange writes nothing.
- **`_request_cause(self, key: tuple[str, str], exchange: Any, digest: str) -> list[str]`** (new).

  ```python
      def _request_cause(self, key: tuple[str, str], exchange: Any, digest: str) -> list[str]:
          """@brief 请求阶段不得重复登记，原因为空；响应与失败须有本进程登记的请求，且带来的请求摘要与登记时相同，
          原因是那条请求事件。"""
          if exchange.phase == "request":
              if key in self.request_events:
                  raise ProtocolError("Model request call was recorded twice")
              return []
          if key not in self.request_events:
              raise ProtocolError("Model response has no recorded request")
          event_id, recorded = self.request_events[key]
          if digest != recorded:
              logging.error("Model %s for call %s carries a request that differs from its recorded request",
                            exchange.phase, exchange.call_id)
              raise ProtocolError("Model exchange request differs from its recorded request")
          return [event_id]
  ```

### `src/worldmock/life/observe.py` (150 lines today)

**Imports.** Add:
- `from worldmock.footprints.observation_parts import split_observation`;
- `from worldmock.life.readable import local_date`. This is the existing `readable.py:42-44`; do not add a second local-date
  helper.

**Constant**, after the imports:

```python
#: 只给宿主生成提示、不写入观察事件的观察键（docs/design/05-recording-engine.md「事件与来源」）。
_HOST_ONLY_KEYS = frozenset({"progress_layer", "progress_meta"})
```

**New method on `ObservationBuilder`**, after `_observation_data` (`:27-52`):

```python
    def stored_observation(self, data: dict) -> tuple[dict, dict[str, str]]:
        """@brief 观察的存储形式与它的观察分件：去掉只给宿主的键，activity_bindings 按所指活动开始时刻（started_at）的
        场景本地日期分段；data 不变，宿主照旧用它生成提示。"""
        kept = {key: value for key, value in data.items() if key not in _HOST_ONLY_KEYS}
        activities, zone = self.context.state.activities, self.context.config.timezone
        dates = [local_date(activities[row["activity_id"]].started_at, zone)
                 for row in data["activity_bindings"]]
        return split_observation(kept, dates)
```

### `src/worldmock/life/engine.py`

`observe` (`:75-93`): lines `:78-89` (as chapter 01 leaves them: the build, the `stored` literal, the draft,
`require_committed` and `append_evidence`) become:

```python
        data = self.observations._observation_data(self.context.state.agents[context.agent_id])
        stored, parts = self.observations.stored_observation(data)
        draft = EventDraft(
            event_type="observation",
            occurred_at=self.context.state.current_time,
            agent_id=context.agent_id,
            decision_id=context.decision_id,
            observed_at=self.context.state.current_time,
            data=stored,
        )
        self.context.store.require_committed(self.context.state.model_dump(mode="json"))
        data["observation_event_id"] = self.context.store.append_observation(draft, parts)
```

- The `require_committed` line is chapter 01's, character for character, because chapter 01's mutation
  `observation_skips_the_committed_world_check` is anchored on it.
- `validated_payload` is not imported back; chapter 01 removed it from `engine.py`.

The continuation lines (`:90-93`) are unchanged. The @brief becomes: 「提交一次真实观察并附当前同秒续接；返回的观察与存储层解码后的
观察（去掉只给宿主的键）逐字节相同。」

### `scripts/inspect_run.py` (773 lines today)

**Import**, next to the other `worldmock` imports (`:37-42`, with `# noqa: E402`):
`from worldmock.footprints.observation_parts import rehydrate_observation`.

**New helper** after `_rows` (`:77-83`):

```python
def _rehydrated(conn: sqlite3.Connection, row: dict) -> dict:
    """@brief 观察行的分件引用换回原表（与存储层解码相同），其余事件原样；event 与 events 打印的都是完整事件。"""
    if row["event_type"] != "observation":
        return row
    return {**row, "data": rehydrate_observation(row["data"], conn)}
```

**`cmd_events`** (`:119-136`): the loop becomes `for r in (_rehydrated(conn, row) for row in rows):`. Nothing else
changes.

**`cmd_event`** (`:184-187`): `rows = [_rehydrated(conn, row) for row in _rows(conn, "event_seq=?", (a.seq,))]`.

**Module docstring**: add 「event 与 events 打印的观察已把观察分件换回原表（docs/design/05-recording-engine.md「检查工具」）。」

### Style check

- Every new function is at most 25 lines, with at most 4 parameters including `self`. `record_model_exchange` shrinks
  from 49 to about 45 lines.
- Every new line is at most 120 characters.
- No file approaches 2,000 lines: `store.py` is about 465 after chapters 01 and 03, `observation_parts.py` about 140,
  `observe.py` about 165.

## Design chapters

The design text is written before the code, in Chinese. What follows is the substance each section must state.

### `docs/design/05-recording-engine.md` 「事件与来源」

Append at the end of the section (after `:107`, before 「数字足迹」):

- **模型证据**：模型调用逐次留证。
   - 请求事件 `model_request` 的 `data.request` 是实际发出的完整请求（消息、工具与参数原样）。
   - 同一调用的响应或失败事件 `model_response` 的 `data.request` 恒为 null，`caused_by` 恰一项，指向它的请求事件。请求只存一次；零点计划的证据本就如此。
   - 环境登记请求时记下请求规范 JSON 的 sha256；登记响应或失败时，核对它带来的请求与之相同。
   - 不同说明请求在调用途中被改动：先写英文错误日志 `Model <phase> for call <call_id> carries a request that differs from its recorded request`，再抛 `ProtocolError("Model exchange request differs from its recorded request")`。这条响应不写入，运行按故障停下。
- **观察分件**：观察里七张随时间增长或几乎不变的表按内容单独存放一次，观察事件的正文只带它们的 sha256。用一张表写清存储形式：

   | 键 | 存储形式 |
   | --- | --- |
   | `activity_bindings` | 按行序，把所指活动开始时刻 `started_at` 的场景本地日期相同的相邻行归为一段，每段一个观察分件；正文存这些分件 sha256 的列表，次序即行序 |
   | `catalog_bindings`、`errand_item_bindings`、`known_activity_actions`、`locations`、`memory_layer`、`persona_views` | 整表一个观察分件，正文存一个 sha256 |
   | 其余键（含 `todo_layer`、`goal_bindings`） | 照旧内联 |

   接着写明：
   - 观察分件的正文是那张表或那一段行的规范 JSON 文本，sha256 按它的 UTF-8 字节计算。
   - 观察分件存在运行库的 `parts` 表（`sha256` 主键、`body` 正文），只能新增，不能修改或删除；同一内容只存一次。
   - 观察与它的全部观察分件在同一事务写入：先登记分件（已有的同一分件不重写），再写观察；观察引用的每个分件都必须已在表中，否则整笔回滚。
   - 每次观察都把全部分件重新编码、求摘要，不按日期缓存：过去某天的一段会因活动阶段变化、改称「（第二次）」或记忆层标注变化而改变。
- **读取**：存储层读取任何事件都经同一个解码。
   - 观察的引用换回原表；换回前核对分件存在、正文的 sha256 与引用相同。
   - 不成立先写英文错误日志，再以 `IntegrityError` 拒绝，原因是 `Observation part is missing`、`Observation part content does not match its sha256` 或 `Stored observation part reference is invalid`。
   - 冻结观察、回看、名称解析与结算读到的观察，与写入前逐字节相同，键序也相同。
   - 没经过这个解码的读取拿到的是裸 sha256 字符串，按行取字段立即失败，不会静默读到别的内容。
   - 任何 SQL 条件只读信封字段和内联键，不读这七个键的内部。
   - 宿主照旧用 `observe` 返回的内存观察生成提示，存储形式不进入任何提示。
- **观察分件不是素材**：
   - 素材按 `AssetRef`（带模态与来源）引用，逐个文件落盘刷盘，在事务之外先发布，每次提交与恢复都重读复核，导出时逐个复制。
   - 观察分件是运行库里与观察同一事务的行，按裸 sha256 引用；导出时换回原表，不另成工件。
   - 两者不共用名称。

Add a Mermaid flowchart (AGENTS.md prefers Mermaid for design) with exactly these edges:
- `observe` → 内存观察 → 宿主提示;
- 内存观察 → 拆分 → `parts` 表 + 带引用的观察事件（同一事务）;
- 读取 → 唯一解码 → 完整观察 → 冻结观察、导出与检查工具.

### `docs/design/05-recording-engine.md` 「冻结输入、检查点与分支」

Append to the second paragraph (`:414-416`):
- 分支在建子库的同一事务里，原样复制父库的整张 `parts` 表与截止序号之前的事件存储原文（观察仍带引用）。
- 复制之前照旧逐条解码父库事件复核，观察分件在解码时一并核对。

### `docs/design/05-recording-engine.md` 「完整导出认证」

- **`:421`**: 「导出清单的结构号是 7」 becomes 8.
- **`:433`**: the sentence 「行可以带 `played_count` 是导出清单结构号升到 7 的原因。」 is replaced by:
  「模型响应与失败事件的 `data.request` 为 null（见「事件与来源」）是结构号升到 8 的原因。」
  The network chapter appends its lineage reason to the same sentence.
- **Add**:
  - 导出经存储层的同一解码读取事件，`events.jsonl` 里的观察是换回原表的完整观察，每行自足。
  - `parts` 表不进导出，下游读者（含 LabelKit 与数据集工具）不解析引用。

### `docs/design/05-recording-engine.md` 「检查工具」

Add after the table (`:369-379`):
- `event` 与 `events`（含 `--full` 和一行摘要）读到的观察先换回原表，与存储层解码的结果相同，打印与写入时的观察一致。
- 分件缺失或摘要不符时以 `IntegrityError` 退出；观察里在分件键上带整表的旧运行库同样以 `Stored observation part reference is invalid` 退出，不为旧库留读取分支。
- 其余子命令不读观察分件。

### `docs/design/06-agent.md` 「宿主模型协议」

- **After 「一次决策恰有一份冻结观察。」** (`:316`), add:
  - 冻结观察是存储层解码后的已提交观察（见[记录引擎](05-recording-engine.md)「事件与来源」的观察分件）。
  - 它与 `observe` 交给宿主的观察，去掉只给宿主的 `progress_layer`、`progress_meta`，以及 `observe` 之后附上的 `observation_event_id` 与同秒续接 `continuation` 之后，规范 JSON 逐字节相同。
  - 宿主只用内存里的观察生成提示。
- **`:336`**: 「请求与响应照常逐次留证。」 becomes 「请求与响应照常逐次留证（请求只存一次，见[记录引擎](05-recording-engine.md)「事件与来源」）。」
  The rule itself lives only in 05, as AGENTS.md requires (one rule, one place).

### `docs/design/09-io-formats.md` 「事件与检查点」

Add one sentence after `:69`: 「运行库的观察分件表 `parts` 与观察的存储形式归[记录引擎](05-recording-engine.md)「事件与来源」，编码同样是确定性 UTF-8 JSON。」

Not changed:
- **`docs/design/07-evaluation.md`**: errand metrics read the export, which is rehydrated.
- **`docs/manual/`**: no user step changes.

## Tests

The suite is run by the implementer (`python -m pytest tests`). Test names below are the node ids the mutations cite.

### New: `tests/unit/footprints/test_observation_parts.py`

**Fixtures.**
- A `store` fixture, as in `tests/unit/footprints/test_store.py:16-22` (toy world `{'version': 0, 'current_time': 100,
  'balance': 50}`, manifest with `agent_ids: [1]` and `environment_assets: {}`).
- A helper `_observation()` returning a dict that holds:
  - five `activity_bindings` rows over three dates, with the module constant
    `START_DATES = ["2026-01-04", "2026-01-04", "2026-01-05", "2026-01-06", "2026-01-06"]`;
  - all six whole tables, each with content distinct from the others and from every date group; `memory_layer`
    carries the float `0.1 + 0.2` (repr `0.30000000000000004`);
  - inline `todo_layer`, `goal_bindings` and `tool_layer`;
  - a `body` with a non-integer float (for example `1234.5`).
- A helper `_stored(store, data, dates=START_DATES, decision_id="decision-a") -> str` that calls
  `split_observation(data, dates)` and returns `store.append_observation(EventDraft(event_type="observation",
  occurred_at=100, observed_at=100, agent_id=1, decision_id=decision_id, data=stored), parts)`.
- A helper `_raw(store, event_id) -> dict` returning `json.loads` of `SELECT body FROM events WHERE event_id=?`.

**Tests.**
- `test_split_groups_bindings_by_start_date_and_stores_six_tables_once`
  - `stored["activity_bindings"]` is a list of three sha256 strings.
  - Each of the literal keys `("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations",
    "memory_layer", "persona_views")` holds a sha256. Spell the names out in the test; do not import `WHOLE_PART_KEYS`.
  - `parts` has exactly 9 entries.
  - For every entry, `sha256(body.encode()) == key`, and `json.loads(body)` equals its group or table.
  - `stored["todo_layer"] is data["todo_layer"]` and `stored["goal_bindings"] is data["goal_bindings"]`.
- `test_split_leaves_the_observation_untouched`
  - After the split, `data == copy.deepcopy(data)` as taken before.
  - Every value of `data` is the same object as before (`is`).
- `test_split_refuses_missing_tables_and_mismatched_dates`, parametrized `ids=["missing-table", "date-count"]`:
  - the exact `IntegrityError` message;
  - the English log line in `caplog`;
  - for each case, see the failure table.
- `test_append_and_read_round_trip_the_observation_bytes`
  - `canonical_json(store.read_event(id).data) == canonical_json(data)`.
  - The rows keep their order.
  - The raw `SELECT body FROM events` row holds sha256 strings at all seven keys.
- `test_every_read_method_returns_the_complete_observation`
  - Write one observation with `agent_id=1`, a decision id and `observed_at`.
  - `read_event`, `read_agent_events("observation", 1)`, `read_decision_observations(1, decision, moment)` and
    `read_events(last)` each return `data` canonically equal to the original.
- `test_identical_tables_are_stored_once`
  - A second observation that equals the first except for one new row on a fourth date.
  - `SELECT COUNT(*) FROM parts` is 10: 9 from the first observation, plus the new day part. The fourth-date group
    differs, and the other two day parts and six tables are reused.
- `test_damaged_missing_or_malformed_parts_fail_loudly`, parametrized `ids=["missing", "damaged", "malformed"]`:
  - **missing**: drop `parts_no_delete`, delete one part, then read. Expect `Observation part is missing`.
  - **damaged**: drop `parts_no_update`, set one part's body to other valid JSON, then read. Expect `Observation part
    content does not match its sha256`.
  - **malformed**: drop `events_no_update`, rewrite the stored body with the full `memory_layer` dict in place of its ref,
    then read. Expect `Stored observation part reference is invalid`.
  - Each case checks the exact message (`match="^...$"`) and the English log line.
- `test_parts_are_immutable`. `UPDATE parts …` and `DELETE FROM parts` raise `sqlite3.IntegrityError` matching
  `Observation parts are immutable`.
- `test_parts_and_their_observation_share_one_transaction`, parametrized
  `ids=["refused-inside-the-write", "refused-before-the-write"]`:
  - **refused-inside-the-write**: monkeypatch `store._insert_event` (instance attribute) to raise
    `sqlite3.OperationalError("injected")`, then call `append_observation` with the split observation.
  - **refused-before-the-write**: the observation draft carries `caused_by=["missing-cause"]`, so chapter 01's
    `append_evidence` refuses it in `_build_events` (`Event cause does not exist`) before its own transaction block.
  - In both: the call raises; on the same connection `SELECT COUNT(*) FROM parts` is 0 and `store.last_event_seq == 0`;
    `store.connection.in_transaction is False`; `caplog` has `Observation append failed; no observation or new part was
    committed`.
- `test_an_observation_whose_part_is_not_stored_is_refused`
  - Write `EventDraft(event_type="observation", occurred_at=100, data=stored)` with no parts, through the generic
    evidence append: `store.append_evidence([draft])`.
  - Raises `Observation part is missing`; `SELECT COUNT(*) FROM events` is 0.
- `test_a_part_whose_body_does_not_match_its_key_is_refused`
  - One body in `parts` is replaced by other valid JSON under the same key, then `append_observation` is called.
  - Raises `Observation part content does not match its sha256`; `parts` and `events` are empty.
- `test_append_observation_refuses_other_events`
  - An `action_result` draft raises `^Observation append requires an observation event$`, `caplog` has
    `Observation append received a action_result event`, and `parts` and `events` are empty.
- `test_branch_copies_the_parts_and_the_stored_rows`
  - Setup:
    - write an observation with `_stored`;
    - take a checkpoint as `test_store.py:219-224` does (`snapshot_directory` of a tmp workspace, then
      `store.checkpoint(store.state, {'agent:1': reference, 'life': reference})`);
    - write a later observation whose bindings add a row on `"2026-01-07"` (a new day part; the discarded tail);
    - `restore_branch(store, point.checkpoint_id, tmp_path / 'child')`.
  - The child's raw body for the first observation's seq equals the parent's raw body (both hold refs).
  - The child's `SELECT sha256, body FROM parts ORDER BY sha256` equals the parent's, including the tail's part.
  - `child.read_event(id).data == parent.read_event(id).data`.

### New test helper: `tests/life/observation_record.py`

Shared by the integration tests below and the amended activity-day test, so the byte-identity check has one
implementation:
- `record_observations(monkeypatch) -> list[tuple[str, dict]]`:
  - wraps `ObservationBuilder._observation_data` (class attribute) so that each call appends `copy.deepcopy` of the
    built dict to a list `built`;
  - wraps `LifeEngine.observe` so that, immediately on return, it asserts that the returned dict without
    `observation_event_id` and `CONTINUATION_KEY` (`worldmock.life.continuation`) equals `built[-1]`, and appends
    `(returned["observation_event_id"], built[-1])` to the result list. This proves nothing between build and return
    mutated the observation.
- `assert_observations_round_trip(store: FootprintStore, recorded: list[tuple[str, dict]], started: dict[str, int],
  zone: str) -> None`, where `started` maps every activity id to its `started_at` and `zone` is the run's timezone:
  - `[event.event_id for event in store.read_events(store.last_event_seq) if event.event_type == "observation"]` equals
    the recorded ids in order (every observation is covered);
  - for each pair, `canonical_json(store.read_event(id).data)` equals `canonical_json` of the copy without
    `progress_layer` and `progress_meta`;
  - the raw body (`SELECT body FROM events WHERE event_id=?`) holds a list of `is_sha256` strings at `activity_bindings`
    and an `is_sha256` string at each of the six whole keys, spelled out literally;
  - for each pair, the number of refs at `activity_bindings` equals the number of distinct
    `datetime.fromtimestamp(started[row["activity_id"]], ZoneInfo(zone)).date()` over the copy's rows. This is computed
    independently of `readable.local_date`, so a split by UTC date or by the observation's own time is caught.

### New: `tests/integration/life/test_observation_parts.py`

These use a real `engine_factory` and the helpers in `tests/life/conftest.py` and `tests/life/environment_runtime.py`.
Each test calls `recorded = record_observations(monkeypatch)` first and, last,
`assert_observations_round_trip(engine.context.store, recorded, {key: item.started_at for key, item in
engine.context.state.activities.items()}, engine.context.config.timezone)`.

- **`test_observe_returns_what_it_built_and_the_store_reads_it_back`**
  - `engine = engine_factory()`, `stage_day_plan(engine)`.
  - `_, rest = act(engine, "rest", do_at("家", "测试休息"))`; `rest.status == "in_progress"`.
  - `observed = observe_only(engine, "after-rest")`; its `activity_bindings` has the rest row.
  - The helper's assertions cover both observations (the empty-bindings one inside `act`, and `observed`).
- **`test_a_past_day_part_follows_the_activity_phase_after_midnight`**
  - Setup (the evening pattern of `tests/integration/life/test_sleep_runtime.py:378-396`):
    - `engine_factory(mutate=evening)` with `start_time = 2026-01-05T20:00:00+08:00` and
      `stop_time = 2026-01-07T20:00:00+08:00`; `stage_day_plan(engine, submitted=True)`.
    - `_, night = act(engine, "evening", sleep(300))`; `night.status == "in_progress"`.
    - `while engine.context.state.activities[night.activity_id].status == "active": advance_next(engine)`.
      `advance_next` (`tests/life/conftest.py:226-232`) runs the stub midnight plan at 2026-01-06 00:00.
    - `woke = context(engine, "woke")`, `first = engine.observe(woke)`; `name` is the sleep row's `name` in `first`.
    - `act(engine, "conclude-night", conclude_request(name), "conclude_activity")`.
    - `_, rest = act(engine, "night-rest", do_at("家", "测试休息"))`; `rest.status == "in_progress"`.
    - `later = context(engine, "later")`, `second = engine.observe(later)`.
  - Assertions:
    - The sleep row's `phase` differs between `first` and `second` (pending conclusion, then concluded); this is the
      premise of the test.
    - `frozen = engine.context.store.read_decision_observations(1, later.decision_id,
      engine.context.state.current_time)`; it has one event, its sleep row equals `second`'s sleep row, and its rest row
      equals `second`'s rest row.
    - The raw stored `activity_bindings` of `first` is one ref (the 2026-01-05 day part). The raw stored
      `activity_bindings` of `second` is two refs, the 2026-01-05 day part and the 2026-01-06 day part, and its first
      ref differs from `first`'s ref.
- **`test_a_new_goal_renames_an_older_activity_in_its_day_part`**
  - Setup:
    - `engine = engine_factory()`, `stage_day_plan(engine)`.
    - `_, rest = act(engine, "rest", do_at("家", "测试休息"))`, then `finish_activity(engine, rest, "rest")` (it ends
      and concludes the activity).
    - `before = context(engine, "before-goal")`, `first = engine.observe(before)`; `n` is the rest row's `name`.
    - `stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal=n,
      registered_at=engine.context.state.current_time),))`.
    - `after = context(engine, "after-goal")`, `second = engine.observe(after)`.
  - Assertions:
    - The frozen read `read_decision_observations(1, after.decision_id, now)` has the rest row with name
      `n + "（第二次）"`, equal to `second`'s rest row.
    - Both raw stored bodies carry one ref at `activity_bindings`, and the two refs differ.

### New: `tests/unit/life/test_model_evidence_pairing.py`

These follow the pattern of `tests/unit/life/test_current_lifecycle_coverage.py:140-158`.

- `test_response_and_failure_carry_no_request_and_link_to_it`, parametrized `ids=["response", "failure"]`
  - Record a request `{"messages": [{"role": "user", "content": "x"}]}`, then the same request with that phase.
  - The stored `model_response.data["request"] is None`.
  - `caused_by == [request_event_id]`.
  - The stored request's `data["request"]` equals the original.
  - `"request" in data` is `True` (the key is present and null).
- `test_response_or_failure_with_a_changed_request_is_refused`, parametrized `ids=["response", "failure"]`
  - A response or failure whose request has a different message raises `ProtocolError`, matching
    `^Model exchange request differs from its recorded request$`.
  - `caplog` has `Model <phase> for call call carries a request that differs from its recorded request`.
  - No `model_response` event exists, and `engine.evidence.request_events` still holds the key.
  - A following exchange with the original request is then recorded.
- `test_an_equal_request_rebuilt_in_another_key_order_is_accepted`
  - The response carries a deep copy of the request, rebuilt with reversed key insertion order.
  - It is recorded.

### New: `tests/integration/footprints/test_inspect_observation_parts.py`

`test_event_and_events_print_the_complete_observation`:
- **Setup.** `engine = engine_factory()`, `stage_day_plan(engine)`, `act(engine, "rest", do_at("家", "测试休息"))`, then
  `observed = observe_only(engine, "after-rest")`, so the observation has a binding row. `event =
  store.read_event(observed["observation_event_id"])`, `seq = event.event_seq`, and
  `full_row = dict(json.loads(canonical_json(event.model_dump())), seq=seq)` (the dict `_rows` would build from a full
  body). Commands run through the `_run` pattern of `test_inspect_current_activity.py:28-35`, with
  `argparse.Namespace` arguments `seq` for `event` and `type="observation", agent=None, tail=1, full=...` for `events`
  (`tail=1` prints only this observation, the last one).
- **`cmd_event`.** `json.loads` of the joined output equals `full_row`; its `data["activity_bindings"]` is a non-empty
  list of dicts.
- **`cmd_events(full=True)`.** `json.loads` of the joined output equals `full_row`.
- **`cmd_events(full=False)`.** The single line equals
  `f"[{seq:>5}] {inspect_run._t(event.occurred_at, display.zone)} a1 {'observation':20s} "
  f"{inspect_run._brief(full_row, display)}"`, with `display = inspect_run.display_context(conn, store.run_dir)` read
  on a second read-only connection (`inspect_run._connect`).

### Existing tests that change

| Test | Change |
| --- | --- |
| `tests/unit/life/test_current_lifecycle_coverage.py::test_model_response_requires_one_request_and_keeps_its_actual_cause` (`:140-158`) | Add `assert stored_request.data["request"] == {"messages": []}` and `assert stored_response.data["request"] is None` |
| `tests/system/lifecycle/test_complete_day_scripts.py::test_activity_day_names_units_buys_portions_and_sleeps_at_the_office` (`:37-63`) | Before `record_complete_day`, `recorded = record_observations(monkeypatch)`. After it, open `store = FootprintStore(case / "run")` and call `assert_observations_round_trip(store, recorded, started, store.manifest["window"]["timezone"])` with `started` from the export's `activities.jsonl` (`activity_id` → `started_at`); then check that each `events.jsonl` observation line's `data` is canonically equal to `store.read_event(event_id).data`; close the store. This is the byte-identity proof over every observation of a full scripted day. The day starts at 00:00 local, which is 16:00 UTC the day before, so the day-part count also tells the local date from the UTC date |
| `tests/system/lifecycle/test_complete_day_scripts.py:104`, `tests/integration/footprints/test_acs_export.py:31`, `test_autoplay_export.py:41`, `test_phone_record_export.py:85`, `test_query_export.py:128` | `schema_version == 7` becomes `== 8` |
| `tests/integration/footprints/test_acs_export.py:84` (and its docstring at `:80`), `tests/integration/footprints/test_phone_record_export.py:320` | The "previous manifest" case writes `schema_version = 7` instead of 6, and the docstring says 「上一版导出清单（7）」: the export made before this chapter, with request copies, is the one that must be refused |
| `tests/integration/evaluation/test_branch_coverage.py:898-899` | Both `match` strings become `"schema_version must be 8"`; the second case mutates to `schema_version=7` (the previous version) |
| `tests/unit/engineering/test_single_source.py` `OWNERS` (`:12+`) | Add `'footprints/observation_parts.py': ('WHOLE_PART_KEYS', 'DAY_PART_KEY', 'split_observation', 'rehydrate_observation', 'insert_parts', 'require_parts', 'copy_parts')` |
| `tests/dataset_acceptance.py` | See "Source mutations" |
| `tests/fixtures/dayrun-activity-baseline.json`, `dayrun-callback-baseline.json` | Not by this chapter. They are code-bound (the run manifest's `code_sha256`) and are re-frozen once at the end of the integration branch, as the proposal's "Versions" says |

Unchanged by this chapter:
- Tests that write toy observations with data lacking the seven keys (`test_store.py:38-44`, `:102-123`, `:125-139`,
  `:186-196`, `test_branch_coverage.py:344-387`, `:651-676`, `:731-751`, `test_render_day.py:464`,
  `test_verified_experiences.py:60-62`, `test_export.py:24`, `test_host_branch_coverage.py:519-521`). Chapter 01 moves
  their same-version `commit` calls to `append_evidence`; the store accepts and returns these observations as they are,
  because none carries a part key (checked: no test writes an observation event with a part key).
- `test_sql_failure_rolls_back_money_and_all_events` and `test_failed_checkpoint_transaction_does_not_publish_record`.
  They monkeypatch `_insert_event(event)`, whose signature is unchanged.
- Chapter 01's `test_observation_refuses_an_engine_world_that_differs_from_the_committed_world`: the split runs before
  `require_committed` and only reads the world, so the refusal and "no event was appended" still hold, and no part is
  written either.

### Byte-identity proof for this chapter

1. **Every observation of a scripted day.** The amended activity-day test above checks, through
   `tests/life/observation_record.py`, that the decoded observation equals what `observe()` built, observation by
   observation and for every observation of the run. It also checks that the export lines equal the decoded events,
   that no `observe()` return was mutated, and that the day parts follow the local start date.
2. **The real 30-day run, with production functions.** This is a one-time verification, not committed. The receipt goes
   under `.work/long-run-cost/03-recorded-evidence/`.
   - Rerun `.work/run30/scratch/spec-03-recorded-evidence/roundtrip_started_at.py`, replacing its local `split` and
     `rehydrate` with `worldmock.footprints.observation_parts.split_observation` and `rehydrate_observation`.
   - Rehydrate against an in-memory SQLite database created with the store's `parts` DDL.
   - Compute the dates with `worldmock.life.readable.local_date` and the run's timezone.
   - Expected:
     - 3,008 of 3,008 byte-identical;
     - 0 non-contiguous date groups;
     - 3,467 distinct parts.
3. **The day-baseline signature, unchanged apart from code identity.** This is also a one-time verification with its
   receipt in the same folder.
   - **How.** On the integration branch, at the commit before this chapter (the head of the store-and-state work) and
     at this chapter's head, run `tests.life.day_baseline_runtime.record_complete_day` for `activity` and `callback`.
     - Monkeypatch `worldmock.run_setup.runtime_code_sha256` to return `"0" * 64` in both arms. This is the
       code-identity mask; `current_run_identity` imports it at call time.
     - Compare the two `summary` dicts. Run both arms on the same machine, because of the known cross-platform
       baseline differences (issue #5).
   - **Expected.** Every artifact digest is equal except `artifacts["manifest.json"]`.
     - Equal digests include `events.jsonl` (the scripted days send nothing through `record_model_exchange`; any
       plan_day evidence from the stub planner is written by `day_planning.py`, which already writes `"request": null`;
       observations are rehydrated), `activities.jsonl`, `quality.json`, every `timeline/*.md`, plus `checkpoints_sha256` and
       `run_assets`.
     - The manifest's only difference is `schema_version` 7 → 8.
   - **Failure.** If any other difference appears, it is a failure. For a model exchange that does appear, the response
     line must become equal once its `data.request` is re-inserted from its `caused_by` request event.
4. **Model evidence.** `test_response_and_failure_carry_no_request_and_link_to_it` and the amended lifecycle test prove the
   recorded form. The proof chapter's recorded-response run and backport verification prove that the prompts are
   unchanged over the 30-day recording.

### Source mutations (`tests/dataset_acceptance.py`)

Append to `MUTATIONS`, and replace `export_schema_version_still_six`:

| Name | `module::symbol` | Original | Replacement | Killer test ids |
| --- | --- | --- | --- | --- |
| `model_response_keeps_request_copy` | `worldmock.life.model_evidence::ModelEvidenceRecorder.record_model_exchange` | `"request": exchange.request if exchange.phase == "request" else None,` | `"request": exchange.request,` | `tests/unit/life/test_model_evidence_pairing.py::test_response_and_failure_carry_no_request_and_link_to_it[response]`, `…[failure]`, `tests/unit/life/test_current_lifecycle_coverage.py::test_model_response_requires_one_request_and_keeps_its_actual_cause` |
| `changed_model_request_accepted` | `worldmock.life.model_evidence::ModelEvidenceRecorder._request_cause` | `if digest != recorded:` | `if False:` | `tests/unit/life/test_model_evidence_pairing.py::test_response_or_failure_with_a_changed_request_is_refused[response]`, `…[failure]` |
| `observation_read_skips_rehydration` | `worldmock.footprints.store::FootprintStore._decode` | `if event.event_type != "observation":` | `if True:` | `tests/unit/footprints/test_observation_parts.py::test_every_read_method_returns_the_complete_observation`, `tests/unit/footprints/test_observation_parts.py::test_append_and_read_round_trip_the_observation_bytes`, `tests/integration/life/test_observation_parts.py::test_a_past_day_part_follows_the_activity_phase_after_midnight` |
| `observation_parts_outside_the_observation_transaction` | `worldmock.footprints.store::FootprintStore.append_observation` | `with self.connection:` | `if True:` | `tests/unit/footprints/test_observation_parts.py::test_parts_and_their_observation_share_one_transaction[refused-before-the-write]` |
| `day_parts_keyed_by_observation_time` | `worldmock.life.observe::ObservationBuilder.stored_observation` | `local_date(activities[row["activity_id"]].started_at, zone)` | `local_date(self.context.state.current_time, zone)` | `tests/integration/life/test_observation_parts.py::test_a_past_day_part_follows_the_activity_phase_after_midnight` |
| `observation_part_digest_unchecked_on_read` | `worldmock.footprints.observation_parts::_part_value` | `if hashlib.sha256(row[0].encode()).hexdigest() != ref:` | `if False:` | `tests/unit/footprints/test_observation_parts.py::test_damaged_missing_or_malformed_parts_fail_loudly[damaged]` |
| `observation_part_digest_unchecked_on_write` | `worldmock.footprints.observation_parts::insert_parts` | `if hashlib.sha256(body.encode()).hexdigest() != digest:` | `if False:` | `tests/unit/footprints/test_observation_parts.py::test_a_part_whose_body_does_not_match_its_key_is_refused` |
| `unstored_observation_part_accepted` | `worldmock.footprints.observation_parts::require_parts` | `if connection.execute("SELECT 1 FROM parts WHERE sha256=?", (ref,)).fetchone() is None:` | `if False:` | `tests/unit/footprints/test_observation_parts.py::test_an_observation_whose_part_is_not_stored_is_refused` |
| `split_writes_refs_into_its_input` | `worldmock.footprints.observation_parts::split_observation` | `return {**data, DAY_PART_KEY: day_refs, **whole}, parts` | the Python string `"data.update({DAY_PART_KEY: day_refs, **whole})\n    return data, parts"` | `tests/unit/footprints/test_observation_parts.py::test_split_leaves_the_observation_untouched` |
| `activity_bindings_stored_as_one_part` | `worldmock.footprints.observation_parts::split_observation` | `key=operator.itemgetter(0)` | `key=lambda pair: 0` | `tests/unit/footprints/test_observation_parts.py::test_split_groups_bindings_by_start_date_and_stores_six_tables_once` |
| `day_parts_rehydrated_out_of_order` | `worldmock.footprints.observation_parts::rehydrate_observation` | `for ref in _day_refs(data)` | `for ref in reversed(_day_refs(data))` | `tests/unit/footprints/test_observation_parts.py::test_append_and_read_round_trip_the_observation_bytes` |
| `memory_layer_stored_inline` | `worldmock.footprints.observation_parts::WHOLE_PART_KEYS` | `"memory_layer", "persona_views")` | `"persona_views")` | `tests/unit/footprints/test_observation_parts.py::test_split_groups_bindings_by_start_date_and_stores_six_tables_once` |
| `host_only_keys_stored` | `worldmock.life.observe::ObservationBuilder.stored_observation` | `if key not in _HOST_ONLY_KEYS}` | `}` | `tests/integration/life/test_observation_parts.py::test_observe_returns_what_it_built_and_the_store_reads_it_back` |
| `branch_skips_parts` | `worldmock.footprints.store::FootprintStore.copy_history` | `copy_parts(parent.connection, self.connection)` | `pass` | `tests/unit/footprints/test_observation_parts.py::test_branch_copies_the_parts_and_the_stored_rows` |
| `branch_rewrites_observations_in_full` | `worldmock.footprints.store::FootprintStore.copy_history` | `(event.event_seq, event.event_id, row[0])` | `(event.event_seq, event.event_id, canonical_json(event.model_dump()).decode())` | `tests/unit/footprints/test_observation_parts.py::test_branch_copies_the_parts_and_the_stored_rows` |
| `inspect_prints_stored_observation` | `scripts.inspect_run::_rehydrated` | `if row["event_type"] != "observation":` | `if True:` | `tests/integration/footprints/test_inspect_observation_parts.py::test_event_and_events_print_the_complete_observation` |
| `export_schema_version_still_seven` (replaces `export_schema_version_still_six`, `:299-301`) | `worldmock.footprints.export::EXPORT_SCHEMA_VERSION` | `EXPORT_SCHEMA_VERSION = 8` | `EXPORT_SCHEMA_VERSION = 7` | `tests/integration/footprints/test_acs_export.py::test_actual_purchase_content_and_current_artifacts_are_authenticated` |

Each original snippet is unique inside its symbol's source, as the runner requires (`tests/mutation_runner.py:121-140`).
The runner injects a mutation by module name in the test process only, so every killer imports the mutated module
directly: the store and `observation_parts` tests import them, the engine tests run `LifeEngine` in process, and the
inspect test uses `from scripts import inspect_run` as `test_inspect_current_activity.py:16` does. The mutation run uses
the existing receipts directory (`RECEIPTS`, `tests/dataset_acceptance.py:5`).

Chapter 01's mutations stay valid: this chapter does not edit `append_evidence`, `commit` or `checkpoint`, and keeps
`LifeEngine.observe`'s `require_committed` line character for character.

## Files touched

- `src/worldmock/footprints/observation_parts.py` (new)
- `src/worldmock/footprints/store.py`
- `src/worldmock/footprints/checkpoint.py`
- `src/worldmock/footprints/export.py`
- `src/worldmock/life/model_evidence.py`
- `src/worldmock/life/observe.py`
- `src/worldmock/life/engine.py`
- `scripts/inspect_run.py`
- `docs/design/05-recording-engine.md`
- `docs/design/06-agent.md`
- `docs/design/09-io-formats.md`
- `tests/life/observation_record.py` (new test helper)
- `tests/unit/footprints/test_observation_parts.py` (new)
- `tests/integration/life/test_observation_parts.py` (new)
- `tests/unit/life/test_model_evidence_pairing.py` (new)
- `tests/integration/footprints/test_inspect_observation_parts.py` (new)
- `tests/unit/life/test_current_lifecycle_coverage.py`
- `tests/system/lifecycle/test_complete_day_scripts.py`
- `tests/integration/footprints/test_acs_export.py`
- `tests/integration/footprints/test_autoplay_export.py`
- `tests/integration/footprints/test_phone_record_export.py`
- `tests/integration/footprints/test_query_export.py`
- `tests/integration/evaluation/test_branch_coverage.py`
- `tests/unit/engineering/test_single_source.py`
- `tests/dataset_acceptance.py`
- `.work/long-run-cost/03-recorded-evidence/` (untracked verification receipts and scripts, not committed)

Not touched: `vendor/`, `patches/agentsociety2/`, `custom/`, `tools/`, `docs/manual/`, `tests/fixtures/` (the baselines
are re-frozen once at the end of the integration branch), dataset packages and every tool schema.

## Not in this chapter

| Item | Where it lives |
| --- | --- |
| Evidence-only append (`append_evidence`), the write-through cache and the observation's byte compare (`require_committed`) | Store chapter (01). This chapter's `append_observation` wraps `append_evidence` in one transaction with the parts and edits none of chapter 01's methods (see "Interfaces and files") |
| World-state structure version, ordered activities, processed-action pruning | State chapter (02). Interactions: pruning removes only processed actions, never activities, because `visible_names` and the day parts read every activity; if ordered activities change `WorldState.activities` from a mapping, `stored_observation` and `_name_bindings` (`observe.py:115-121`) follow the new access together |
| Branch copy by ATTACH + `INSERT … SELECT`, streaming export, streamed restore re-validation, bounded reads | Whole-history chapter. It must keep the `parts` copy, decode through `_decode`, and never read inside a part key in SQL |
| The `lineage` field under export schema 8 | Network chapter. It adds its field and its reason to the same version and sentence; no second bump |
| Viewer reading the export, and its detail pane | Viewer chapter. The export's observations are rehydrated, so the viewer needs no part resolution |
| Comparator reconstruction rule `model_response.data.request` ← the `caused_by` request's `data.request` (reader search: the inventory under "Each model request stored once"), and the masked complete-day comparison | Proof chapter (`scripts/compare_runs.py`) |
| Pointing the viewer's `RS_PATH` at `message.reasoning_content` | Not proposed. It is a separate reader fix with no storage change; the duplicate stays (proposal "Not proposed") |
| `request_sha256` on responses | Left out (reviewer: `caused_by` plus the in-process check is enough) |
| Storing `model_request` messages or tool schemas by reference; dropping `provider_specific_fields.reasoning_content`; moving the checkpoint out of the export manifest | Proposal "Not proposed" |
| Parts for `todo_layer`, `goal_bindings`, `tool_layer` or `interaction_bindings`; binding only live activities | Kept inline or kept whole, by the proposal and the reviewer (realism: recall reads every activity) |
| Envelope columns for faster SQL predicates; per-member deduplication of workspace snapshots | Not proposed |
| Ad-hoc scripts that read raw run-database bodies (for example `.work/run30/getev.py` and the dataset repo's `.work` investigations) | Outside the product. They should read the export or use `inspect_run.py event`; nothing in the repository depends on them |
| Tool parameters, host validation of tool arguments, run-command CLI options, dataset fields | Untouched. None is needed (proposal "Awaiting the owner") |
