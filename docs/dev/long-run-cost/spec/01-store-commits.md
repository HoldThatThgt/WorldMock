# Store commit path

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements the proposal's section "Store commit path" (its four paragraphs: write-through cache,
evidence-only append, structural sharing, checkpoint single encode) and the "Decisions" row "Where the
engine-equals-committed check runs" (before every version-advancing commit, plus a byte compare on the observation
commit). Every rule here leaves each model prompt, decision input and settled world fact byte-identical; it has none of
the proposal's deliberate exceptions. It changes no model tool schema, no host validation of tool arguments, no CLI
option and no field of the environment dataset (`persona.toml`, `config.toml`, catalog files).

Research inputs: `.work/run30/opt/design_commit.json` and `review_commit.json` (the reviewer's revisions are applied
where they correct the design; see the list below), scratch evidence in `.work/run30/scratch/opt-commit/`,
`.work/run30/scratch/opt-review-commit/` and this chapter's own probes in
`.work/run30/scratch/spec-01-store-commits/` (`probe_world_copy.py`, `proto_store01.py`, `equivalence01.py`) and
`.work/run30/scratch/spec-review-01-store-commits/` (`sharing_day.py`, `guard_cost.py`, the review's runs).

### Measured cost on main `0bd2de65`

Offline, with the run's final world (version 7732, 10,485,389 bytes; `processed_actions` 6.73 MB, `activities`
3.25 MB), medians of interleaved rounds under about 55% machine load [R] (`opt-commit/timing_interleaved.json`,
`opt-review-commit/bench_review.json`):

| Operation | Today | Where the time goes |
| --- | --- | --- |
| Same-version (evidence) commit | 0.76–0.83 s | `validated_payload` 0.13–0.18 s; `store.state` SELECT + `json.loads` 0.12 s; two `canonical_json` in `_validate_state` 0.26 s; a third `canonical_json` for the row rewrite 0.13–0.15 s |
| Version-advancing commit | 0.49 s | `validated_payload`; `json.loads` of the stored world (0.107 s) only to read version, clock and accounts; one encode |
| Checkpoint | 0.97 s | `validated_payload`; `json.loads`; three `canonical_json`; `sha256_json` encodes the payload once more |
| `WorldState.model_copy(deep=True)` | 0.31 s | 95% of the bytes are entries of `processed_actions` and `activities` |

Per day at day 30, with the run's late-week daily counts (511 evidence commits, 255 version-advancing commits, 184 checkpoints,
about 97 observations, about 1,396 whole-world deep copies) this is about 403 + 126 + 181 + 437 ≈ 1,148 s a day [R]
(`opt-commit/project_area.json`), and it grows linearly with the world size.

### Expected saving

| Operation | After | Per day at day 30 |
| --- | --- | --- |
| Evidence append | 0.012 s (model request) to 0.025 s (453 KB observation) [R] | about 10 s |
| Observation byte compare (kept) | dump + encode + compare 0.197 s [R] | about 19 s |
| Version-advancing commit | 0.37 s without the re-read [R], plus the pre-commit compare 0.197 s | about 145 s |
| Checkpoint | 0.47 s [R] | about 86 s |
| Whole-world copy | 0.015 s [R] | about 20 s |

Total about 280 s a day at day 30 instead of about 1,148 s, inside the proposal's "about 270–340 s, before pruning".
This chapter's probe (`probe_world_copy.json`) confirms on the same world, with the code of this tree, that the shared
copy gives byte-identical dumps, keeps the activity order and leaves the original untouched; its timings were taken
under heavier load and are not used.

### Reviewer revisions adopted

- `world_setup.py:134` advances the version (`_run_start_callbacks` returns `version + 1`, `world_setup.py:171`) and
  stays a version-advancing commit; it is not an evidence append.
- The engine-equals-committed check runs immediately before every version-advancing commit, not at the start of a
  staging, and the observation keeps its byte compare.
- The last event time is read by primary key, not cached (`restore_branch` inserts events outside `commit`,
  `checkpoint.py:133-135`).
- The cache loads lazily at the first write-path call, so read-only openers (`cli.py:138` export, `runtime.py:242`
  resume parent) never parse the world.
- Structural sharing is implemented only as `WorldState.__deepcopy__`. The entries get no `__deepcopy__`: pydantic's
  `model_copy(update=..., deep=True)` calls `self.__deepcopy__()` and then `copied.__dict__.update(update)`
  (pydantic 2.12.5 in the project venv, `pydantic/main.py:403`, `:414`), so an entry whose `__deepcopy__` returned
  `self` would receive the update in place and corrupt the committed entry
  (`opt-review-commit/deepcopy_self_probe.json`).
- The leak fixture wraps every public `LifeEngine` method, not only the six staging entry points.
- The checkpoint encodes the payload once by `canonical_json`; it does not splice cached state bytes into hand-built
  JSON.
- `frozen=True` and `DeepFrozenModel` on the entries are not adopted (see "Not in this chapter").

## Behaviour

### Terms

| Term | Meaning |
| --- | --- |
| Committed world | The `world_state` row of the run database: `version` column and `body`, the canonical JSON text of the world |
| Engine world | `EngineContext.state` (`engine_context.py:26`), the `WorldState` the engine reads and stages from |
| Version-advancing commit | As in the specification's terms: one store write that replaces the committed world by a world whose version is one higher, together with at least one causal event, in one SQLite transaction. The code calls it a step (`commit_step`, test names with `version_step`) |
| Evidence append | One store write that appends events only; the world and its version stay as they are |
| Base | The world a version-advancing commit starts from, passed by the caller in JSON form; the store requires it to equal the committed world byte for byte |
| Committed-world cache | The store's in-memory copy of the committed world's version, current time, accounts and canonical bytes |
| Shared entry | An `ActivityState` or `StoredAction` object that belongs to both the engine world and a staged copy of it |
| Canonical bytes | `canonical_json(value)` (`footprints/models.py:189-195`): `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` encoded as UTF-8 |

### Write-through cache of the committed world

- `FootprintStore` keeps one `CommittedWorld` value in `self._committed`, `None` after `__init__`.
- The first call of a write-path method (`commit`, `append_evidence`, `checkpoint`, `require_committed`) loads it:
  `SELECT body FROM world_state WHERE singleton=1`, one `json.loads` of that text for version, current time and
  accounts, and the text's UTF-8 bytes as the canonical bytes. The stored text is always canonical (written by
  `FootprintStore.create`, `store.py:73-75`, and by `commit`), and `canonical_json(json.loads(text))` equals the text's
  bytes; this chapter's probe confirmed it on the 10.5 MB world (`probe_world_copy.json`, `canonical_idempotent`).
- After the load the cache changes only in `commit`, after its transaction has committed: it becomes
  `CommittedWorld.of(state, body)` with the bytes that were just written. A failed transaction leaves it unchanged.
  `append_evidence` and `checkpoint` never change it.
- The accounts in the cache are a copy (`dict(state["accounts"])`), never the caller's dict.
- The last event's `occurred_at` is not cached. `_last_event_time` reads it by primary key with
  `SELECT json_extract(body,'$.occurred_at') FROM events ORDER BY event_seq DESC LIMIT 1`, so events inserted outside
  `commit` (`checkpoint.py:133-135`, and chapter 04's SQL copy that replaces it) are always seen. On an empty
  events table it is the cached current time.
- The store has one writer (`store.py:42`). Every write transaction confirms, after its inserts and before it
  commits, that the stored `world_state.version` still equals the cached version (`_require_stored_version`). If a
  second connection has advanced the world, the write is refused and rolled back; this is the expected-revision guard
  the proposal cites for evidence-only appends ("append guarded by expected revision"), and it keeps today's detection
  of a second writer, which today comes from re-reading the stored world in every commit (`store.py:97`).
- Read methods (`state`, `last_event_seq`, `read_events`, `read_agent_events`, `get_checkpoint` and the other
  readers) never touch the cache. `FootprintStore.state` (`store.py:84-88`) keeps reading the row; writers no longer
  call it.

### Version-advancing commits

`FootprintStore.commit(base, state, events)` performs, in this order:

1. Load the cache if needed.
2. `require_committed(base)`: `canonical_json(base)` must equal the cached bytes, else
   `IntegrityError("Engine world differs from the committed world")`.
3. `_validate_step`: `state["version"]` and `state["current_time"]` are `int` (else
   `"World version and current time must be integers"`); `state["current_time"]` is not below the cached time (else
   `"World time cannot move backwards"`); `state["version"] == cached version + 1` (else
   `"World state version is discontinuous"`; this now also refuses a same-version call, which must use
   `append_evidence`); `events` is not empty (else `"World state changes require at least one causal event"`).
4. `validate_account_transition(cache.ledger_view(), state, events, self.last_event_seq == 0)`, unchanged rules
   (`ledger.py:12-35`); `ledger_view()` is `{"accounts": accounts}` or `{}` when the world has no accounts, which is
   exactly what the function read from the stored world before.
5. `_build_events(cached version, state["version"], state["current_time"], events)`: same references, time and asset
   checks as today (`store.py:125-161`), version pair `(cached, cached + 1)`.
6. `body = canonical_json(state)`; one transaction: insert the events, `_require_stored_version(cached version)`,
   `UPDATE world_state SET version=?,body=? WHERE singleton=1` with `state["version"]` and `body.decode()`.
7. After the transaction: `self._committed = CommittedWorld.of(state, body)`.

The new world's validation (`WorldState.model_validate` through `validated_payload`, `life/models.py:1340-1353`) stays
with every version-advancing commit; the store does no `json.loads`, no world validation and exactly two world-sized encodes (the
base and the new world).

In the life layer every version-advancing commit goes through one new method, `EngineContext.commit_step(staged, drafts)`:

```python
events = self.store.commit(self.state.model_dump(mode="json"), validated_payload(staged), drafts)
self.state = staged
return events
```

The base is the engine world at the moment of the commit, after all staging; this is the "check immediately before
every version-advancing commit". It catches an in-place edit that leaked from a staged copy into a shared entry (the
engine world no longer equals the committed bytes) and any other divergence of the engine world.

Version-advancing commit sites, all of them, today and after:

| Site today | Path | After |
| --- | --- | --- |
| `time_advance.py:145-147` `_commit_advance` | `advance` and `decide_midnight_rhythm` (`:133`, `:201`) | `staged.version = ...; self.context.commit_step(staged, drafts)` |
| `time_advance.py:216-218` | `register_day_plan` | `self.context.commit_step(staged, [*evidence, draft])` |
| `decision_coordinator.py:188-189` | `commit_decision_round` | `events = self.context.commit_step(staged, drafts)` |
| `decision_coordinator.py:251-252` | `abort_decision_round` | `self.context.commit_step(staged, drafts)` |
| `counterfactual_runtime.py:57-58` | `apply_counterfactual` | `event = self.context.commit_step(staged, [draft])[0]` |
| `world_setup.py:134` | `create_world`, run-start callbacks and initial funding | `store.commit(initial, validated_payload(state), [injection, entered, *drafts])`, where `initial = validated_payload(state)` is computed once before `FootprintStore.create` and passed to both |
| `checkpoint.py:138-140` | `_populate_branch`, `branch_created` | `child.commit(checkpoint.state, state, [EventDraft(...)])`, wrapped as shown under "Other production call sites" (the base makes the first line 126 characters otherwise) |

These are the only `.commit(` calls in `src/`, `custom/`, `scripts/` and `tools/` apart from the evidence sites below
(searched with `grep -rn "\.commit(" src custom scripts tools`). At `world_setup.py` and `checkpoint.py` the base is by
construction the world `FootprintStore.create` just wrote; the check is cheap there (once per run or branch) and
keeps the rule without exceptions.

### Evidence appends

`FootprintStore.append_evidence(events)` takes no world. In order:

1. `events` must not be empty, else `IntegrityError("Evidence append requires at least one event")`.
2. Load the cache if needed.
3. `validate_account_transition(view, view, events, False)` with `view = cache.ledger_view()`: an evidence append
   cannot move money (`"Account balances disagree with committed financial events"`), and initial funding is refused
   (`"Initial funding must be the first event of world creation"`); initial funding belongs to the world-creation
   version-advancing commit.
4. `_build_events(cached version, cached version, cached current time, events)`: the same reference, time, observation
   interval and asset checks as today; both versions are the cached version.
5. One transaction: insert the events, then `_require_stored_version(cached version)`.

It never reads, validates, encodes or rewrites the world. It does not restrict event types (today's same-version
commit did not either).

Evidence sites, today and after:

| Site today | Events | After |
| --- | --- | --- |
| `engine.py:88-89` `observe` | observation | `self.context.store.require_committed(self.context.state.model_dump(mode="json"))`, then `data["observation_event_id"] = self.context.store.append_evidence([draft])[0].event_id` |
| `engine.py:168-169` `fail_run` | run_failed | `self.context.store.append_evidence([world_ops._failure_draft(self.context.state.current_time, error, None)])` |
| `model_evidence.py:71-72` `record_model_exchange` | model_request, model_response (failure phase included), and the deferred decision_started | `event = self.context.store.append_evidence([item for item in (start, draft) if item is not None])[-1]` |
| `model_evidence.py:119` `record_memory_update` | memory_updated | `self.context.store.append_evidence([draft])` |
| `decision_coordinator.py:133-134` `_queue_action` | decision_started, action_requested | `event = self.context.store.append_evidence([item for item in (start, draft) if item is not None])[-1]` |
| `runtime.py:660` `_generate_day_plan` | plan_day request and response evidence after the validation retries are spent | `engine.context.store.append_evidence(evidence)` |
| `runtime.py:683` `_call_plan_day` | plan_day evidence plus failure evidence after a transport failure | `engine.context.store.append_evidence(evidence)` |

Only the observation keeps a byte compare, because its content becomes the decision prompt: the host renders the
prompt from the dict `observe()` returns, built from the engine world, and the compare after building it proves that
world is the committed one (and that building the observation did not edit it). The compare runs at the same point as
today's (after `_observation_data`, before the event is written). If it fails, no observation is written and no
model is called.

`fail_run` records `run_failed` without any compare; today it fails to record when the engine world diverged or is
invalid, because its commit validated and compared the world. The run-failure evidence is now recorded in those cases
too; only the event checks of `_build_events` still apply (an engine clock ahead of the committed clock is refused as
`Event time is out of committed world order`, as today).

### Checkpoints

`FootprintStore.checkpoint(state, snapshots)`:

1. `require_committed(state)` (one encode, compared with the cached bytes; no `json.loads`).
2. `snapshots` must not be empty, else `IntegrityError("Checkpoint requires participant snapshots")`; then the
   unchanged participant and asset checks (`store.py:296-298`).
3. `payload = self._checkpoint_payload(state, snapshots)` (unchanged, `store.py:326-332`).
4. `encoded = canonical_json(payload)`; `digest = hashlib.sha256(encoded).hexdigest()`. This equals today's
   `sha256_json(payload)` (`footprints/models.py:198-200`) and today's stored body, so the row and `record_sha256` are
   byte-identical; `get_checkpoint` keeps verifying `sha256(body) == record_sha256` (`store.py:338`).
5. `checkpoint_created` is built with the cached version for both versions and the cached current time.
6. One transaction: insert the checkpoint row with `encoded.decode()` as body (chapter 02 replaces only this stored
   form), the event, the snapshot artifact rows, then `_require_stored_version(cached version)`.

`LifeEngine.checkpoint` passes `self.context.state.model_dump(mode="json")` instead of `validated_payload(...)`
(`engine.py:183`): bytes equal to an already validated committed world need no second validation. A checkpoint now
does two world-sized encodes (the compare and the payload) and no `json.loads` of the world.

### Where the engine-equals-committed check runs

| Moment | Who | Compared |
| --- | --- | --- |
| Before the observation event is written | `LifeEngine.observe` → `store.require_committed` | Engine world after the observation was built |
| Before every version-advancing commit's transaction | `FootprintStore.commit` (base), reached through `EngineContext.commit_step` | Engine world after the staging, that is the world the step started from |
| Before every checkpoint's transaction | `FootprintStore.checkpoint` | Engine world at the checkpoint |
| Model request and response, memory update, decision start and action request, run failure, plan_day evidence | — | No compare |

Today the compare runs at every same-version commit and at checkpoints, but never at a version-advancing commit (the step's
`before` is re-read from the database, `store.py:97`, and the engine world is not compared). Prompts relate to the
check as follows:

- A decision prompt is rendered from the observation, which keeps its compare at the same point as today.
- A plan_day prompt is built from the engine world (`runtime.py:643`). On success its evidence is committed with
  `register_day_plan`, a version-advancing commit that today does not compare the engine world and now does. On failure its
  evidence is appended without a compare (today it was compared), and the run stops right after with the same
  `ProtocolError` or transport error either way.
- Whether a bedtime reflection is due (`due_reflection`, `engine.py:95-99`) and read-only answers
  (`read_task_summary`, `engine.py:149-151`) read the engine world without a compare, today as after. Today the
  reflection's `memory_updated` commit compared the world after the reflection call; now the observation that follows
  in the same decision compares it, before any decision prompt exists. The guard fixture proves in tests that these
  reads never change the world.

So the new placement is at least as strong for every prompt. The one weaker point: an in-place change of the engine
world made between an observation and the next step is refused at that step, observation or checkpoint instead of at
the next evidence commit, so a few truthful evidence events (model response, memory update, action request) may be
appended before the run stops. The persisted world never receives the change.

### Structural sharing of whole-world copies

- `WorldState.__deepcopy__(memo=None)` returns a copy whose `activities` and `processed_actions` are new dicts holding
  the same entry objects, in the same order, and whose every other field is `copy.deepcopy(value, memo)` with one memo
  shared by all fields (as pydantic's own `__deepcopy__` does, `pydantic/main.py:974-993`). `__pydantic_fields_set__`,
  `__pydantic_extra__` (always `None`, `extra="forbid"`) and `__pydantic_private__` (always `None`) are carried as
  pydantic's `__copy__` carries them (`pydantic/main.py:955-972`).
- `model_copy(deep=True)`, `model_copy(update=..., deep=True)` and `copy.deepcopy(state)` all reach this method, so
  all whole-world deep copies (about 30 call sites, e.g. `time_advance.py:104`, `decision_coordinator.py:163`, `:227`,
  `counterfactual_runtime.py:30`, `action_evidence.py:23`) become cheap without editing them.
- `ActivityState` and `StoredAction` get no `__deepcopy__`; copying one entry (`activity.model_copy(..., deep=True)`,
  e.g. `travel.py:85`) still copies it fully.
- Rule: entries of `activities` and `processed_actions` are immutable values. Any change, including to nested `data`,
  `result`, `receipt`, `session_ids`, `note_history` or `resource_claims`, replaces the entry in the staged dict
  (`staged.activities[key] = entry.model_copy(update=...)`) or replaces the dict (`staged.processed_actions = {...}`).
  Subscript assignment into a staged world's dict is safe, because the dict is the copy's own.
- The staged world therefore stays isolated from the engine world: a failed staging leaves it untouched and every
  before/after comparison (`compare_callback_states`, `settle_navigation`, `foreground_events`, `stay_events`,
  `time_advance.py:140-144`, `decision_coordinator.py:178-182`) sees the true "before".
- Runtime guard: the base compare of every version-advancing commit above. An in-place edit of a shared entry during a staging changes the
  engine world, which then differs from the committed bytes, so the step is refused before anything is written. The
  permanent test fixture (see "Tests") reports any such edit, and any edit a read path makes, in every test.
- Assumption, documented in the design: no object is reachable both from an entry and from another field of the same
  world. Worlds loaded from JSON satisfy it and no staging code stores one object in both places; the deep-copy
  versus shared-copy day-run differential test checks the outcome on the scripted days.

### The two in-place entry edits

Both edit an entry created in the same staging, so neither leaks today; they are rewritten as replacements so the rule
has no exception (the reviewer verified `origin_action_id` is only set at activity creation, `activities.py:74`,
`storefront_runtime.py:148`, and `record_arrival` edits the full copy from `travel.py:85`).

- `decision_coordinator.py:364-366` (`_accept_results`) becomes:

  ```python
  for activity_id, activity in list(changed.activities.items()):
      if activity.origin_action_id == pending.action_id:
          data = {**activity.data, "accepted_event_id": result_draft.event_id}
          changed.activities[activity_id] = activity.model_copy(update={"data": data})
  ```

- `travel.py:107-110` `record_arrival(activity, agent) -> None` becomes `record_arrival(activity, agent) ->
  ActivityState`: it returns `activity` unchanged when `activity.elapsed_seconds < activity.planned_duration_seconds`,
  otherwise `activity.model_copy(update={"data": {**activity.data, "arrival": arrival}})` with
  `arrival = {"location_id": agent.location_id, "access_status": "allowed"}`. The caller at `time_advance.py:326`
  becomes `changed=record_arrival(changed,agent)` (the surrounding function's spacing is kept).

Both produce the same dict content and key order as the in-place edit (an existing key keeps its position, a new key
is appended), and nothing in `src/` dumps with `exclude_unset`, so the changed `__pydantic_fields_set__` has no effect
on any recorded or rendered form.

### What stays byte-identical, and why

| Recorded or model-visible form | Why it is unchanged |
| --- | --- |
| `world_state.body` and `version` at every moment | Version-advancing commits write `canonical_json(state)` as today; evidence appends and checkpoints no longer rewrite the row, and today's rewrite wrote identical bytes |
| Every `events` row except `recorded_at` and `wall_elapsed_ms` (wall-clock, never part of a fact) | Same drafts in the same order through the same `_build_events` checks; `state_version_before`/`after` are the cached version, equal to the stored one today; event ids come from the same `EventDraft` default factory calls |
| `checkpoints` rows and `record_sha256` | `canonical_json(payload)` of the same payload; `sha256` of those bytes equals `sha256_json(payload)` |
| `artifacts` rows | Unchanged inserts |
| Observations, every prompt layer, model requests, decision inputs | Built from the same engine world; staged worlds are byte-identical because shared entries are never edited |
| Settled facts (activities, money, bodies, callbacks, stays, foreground intervals) | Same staging code over a staged copy whose bytes equal today's deep copy; before/after compares see the true before |
| Export artifacts, `quality.json`, timelines | Derived from the rows above |

Verified offline [R]: the prototype store ran the same sequence as main's store (initial funding step, evidence,
step with money, evidence, checkpoint) with deterministic ids and clocks, and every `events`, `world_state`,
`checkpoints` and `artifacts` row and the `record_sha256` were identical (`spec-01-store-commits/equivalence01.json`).
The SQLite file's page layout differs (fewer page writes); no reader reads file pages.

### Invariants

- Events are append-only, contiguous and immutable (triggers `store.py:29-37` unchanged).
- A version-advancing commit moves the version by exactly one and carries at least one causal event; an evidence append and a
  checkpoint never move it.
- An evidence append cannot change the world (it has no world argument) or any account.
- World and event time never go backwards; `observed_at` lies inside `[occurred_at, current time]`.
- Account deltas of a version-advancing commit equal its money events; initial funding only in the world-creation step.
- Causes exist and event ids are unique (`store.py:148-161` unchanged); referenced assets are read and hashed.
- A checkpoint's world equals the committed world; `record_sha256 = sha256(canonical payload)`.
- Every world the life layer writes by a version-advancing commit passed `WorldState.model_validate` (`commit_step`,
  `world_setup.py:126/134`). The branch world (`checkpoint.py:136-140`) is the parent checkpoint's world, validated at
  `runtime.py:245`, with a new `run_id` and version, and is validated again when the engine opens it
  (`engine_context.py:36`); this is today's behaviour and does not change.
- After every successful write the cache equals the stored row (version, time, accounts, bytes); after a failed one
  it is unchanged; every write transaction re-checks the stored version against it.
- At every observation, version-advancing commit and checkpoint, the engine world equals the committed world byte for byte.
- Entries of `activities` and `processed_actions` are never edited in place.

### Failure handling

`IntegrityError` logs `"WorldMock failure: %s"` in its constructor (`errors.py:7-13`); branches below that add context
log it first with `logging.error`.

| Condition | Log | Exception |
| --- | --- | --- |
| Base, observation world or checkpoint world differs from the cached bytes | `logging.error("Engine world differs from the committed world at version %d", version)` | `IntegrityError("Engine world differs from the committed world")` |
| Stored version is not the cached version inside a write transaction | `logging.error("Stored world version %d is not the cached version %d", stored, version)`, then the caller's `logging.exception` | `IntegrityError("World state was changed outside this store")`; the transaction rolls back |
| `append_evidence([])` | `logging.error("Evidence append received no event")` | `IntegrityError("Evidence append requires at least one event")` |
| Any exception inside an evidence append transaction | `logging.exception("Evidence append failed; no event was committed")` | re-raised |
| `checkpoint(state, {})` | `logging.error("Checkpoint received no participant snapshot")` | `IntegrityError("Checkpoint requires participant snapshots")` |
| Version-advancing commit: non-integer version or time, clock backwards, version not cached + 1, no events | constructor log | unchanged texts: `"World version and current time must be integers"`, `"World time cannot move backwards"`, `"World state version is discontinuous"`, `"World state changes require at least one causal event"` |
| Event time or observation interval, references, assets, ledger | unchanged | unchanged texts |
| Version-advancing commit or checkpoint transaction failure | unchanged `"Footprint transaction failed; no state or event was committed"`, `"Common checkpoint transaction failed"` | re-raised |

The message `"Checkpoint requires current world state and participant snapshots"` is deleted; its two conditions now
have the two messages above.

### What is deleted

- The same-version path of `FootprintStore.commit`: the delta-0 branch, the double `canonical_json` compare
  (`store.py:122`) and the unconditional row rewrite for same-version commits.
- `FootprintStore._validate_state` (`store.py:115-123`), replaced by `_validate_step` and `require_committed`.
- The `json.loads` of the stored world in every commit (`store.py:97`) and of the whole last event body
  (`store.py:165-166`).
- The checkpoint's `canonical_json(state) != canonical_json(self.state)` (`store.py:294`) and the re-encode in
  `sha256_json(payload)` (`store.py:300`).
- `validated_payload(...)` at every evidence site and at `engine.py:183`, and the now unused imports:
  `engine.py:5`, `model_evidence.py:16`, `decision_coordinator.py:47`, `time_advance.py:54`,
  `counterfactual_runtime.py:10`, and the local imports `runtime.py:639` and `runtime.py:674` (with their registry
  entries `tests/unit/engineering/test_import_graph.py:94` and `:100`).
- The five `self.context.state = staged` assignments (`time_advance.py:147`, `:218`, `decision_coordinator.py:189`,
  `:252`, `counterfactual_runtime.py:58`); `commit_step` is the only writer of `EngineContext.state` after `__post_init__`.

### Ownership of shared files with other chapters

| File | This chapter | Other chapters |
| --- | --- | --- |
| `footprints/store.py` | `CommittedWorld`, `_validate_step`, the `_committed` attribute in `__init__`, `_committed_world`, `require_committed`, `commit`, `append_evidence`, `_require_stored_version`, `_build_events`, `_last_event_time`, and `checkpoint` (compare, single encode, digest, the transaction's statements) | Chapter 02: the stored form of the checkpoint body in `checkpoint`'s `INSERT` (it compresses `encoded`; `digest` stays over `encoded`) and the reading of that row in `get_checkpoint`. Chapter 03: the `parts` table in `SCHEMA`, the body of `_insert_event` (its signature stays), `_index_artifacts`, `append_observation`, `copy_history`, `_decode` and the decoder call in every reader. Chapter 04: the connect line in `__init__` (URI form, 120 s busy timeout; the rollback journal stays, the proposal decided against WAL), `read_event`'s body, `read_recent_experience_sources`' SQL, the deletion of `read_events`, its new read and copy methods. The `_committed` line and chapter 04's connect line are adjacent in `__init__`; whichever lands second rebases |
| `footprints/checkpoint.py` | The `child.commit(...)` call (`:138-140`) gains its base | Chapter 03 (`copy_history`) and then chapter 04 (`copy_event_prefix`) replace the event copy above it (`:133-135`); chapter 05: lineage in the branch manifest |
| `life/engine.py` | `observe`'s compare and write (`:88-89`), `fail_run`'s write (`:168-169`), the `checkpoint` call (`:183`), the import (`:5`) | Chapter 03: `observe` splits the observation (`stored_observation`) and writes it with `append_observation`, which replaces this chapter's `append_evidence([draft])` in `observe`; this chapter's `require_committed(...)` line stays immediately before it (see the contract below the table). Chapter 04: the history read in `fail_run` (`:165-167`, `has_event`) |
| `life/decision_coordinator.py` | `:47`, `:133-134`, `:188-189`, `:251-252`, `:364-366` | Chapter 02: request-identity scoping (`_validate_call`, `_existing_action`, `_reused_results`, `_replay_drafts`, the `reused=reused,` argument) |
| `life/model_evidence.py` | `:16`, `:71-72`, `:119` | Chapter 03: the response's `request` field and the request digest check; chapter 05: `commit_ms` measured around this chapter's `append_evidence` call |
| `life/time_advance.py` | `:54`, `:145-147`, `:216-218`, `:326` | Chapter 02: processed-action pruning after the clock advance (`:105`), `_terminal_for_progress`, `_truncate` |
| `life/models.py` | `WorldState.__deepcopy__`, `_SHARED_ENTRY_FIELDS`, `import copy` | Chapter 02: activity order in the stored form and `WORLD_SCHEMA_VERSION`; it keeps `activities` an in-memory `dict` (chapter 02 "Uninterrupted runs are byte-identical"), so the hook needs no change |
| `runtime.py` | `:639`, `:660`, `:674`, `:683` | Chapter 05 rewrites `_call_plan_day` on top of this chapter's `append_evidence(evidence)` |
| `tests/life/conftest.py` | The four helpers' writes (`:55-58`, `:70-73`, `:98-101`, `:136-139`) | Chapter 02's origin-removal test stages its world as `stage_day_plan` does |
| `docs/design/04-simulation-engine.md`, `05-recording-engine.md`, `09-io-formats.md` | The paragraphs under "Design chapters" | Chapter 02 edits the same sections on top of this chapter's text: in 05 it rewrites this chapter's checkpoint sentence (so that sentence must be kept word for word), in 04 it edits the rest of line 738, in 09 it appends after this chapter's paragraph on immutable entries |

**Contract for chapter 03's observation write.** When chapter 03 lands on this chapter, `append_observation` is an
evidence append and takes no world: `append_observation(draft, parts)` runs the same steps as `append_evidence` (cache,
`validate_account_transition(view, view, [draft], False)`, `_build_events` with the cached version and time, one
transaction ending with `_require_stored_version`), with `insert_parts` as the first statement inside that
transaction. The compare stays the separate `require_committed(...)` call in `observe`, immediately before it.
Chapter 03's text gives `append_observation` a `state` argument filled with `validated_payload(self.context.state)`;
that argument and that import do not exist after this chapter (open for the integrator, see chapter 09).

Implementation order inside this chapter: design chapters, then `store.py` with its tests, then the life call sites,
then `WorldState.__deepcopy__` with the two edit rewrites and the guard fixture, then the remaining test updates.
Chapter 02's checkpoint compression, chapter 03's observation parts and chapter 04's history paths land on top of this
chapter.

## Interfaces and files

### `src/worldmock/footprints/store.py`

New import: `from dataclasses import dataclass`. `sha256_json` stays imported (used by `_checkpoint_payload` and
`get_checkpoint`).

Added before `class FootprintStore` (`store.py:41`):

```python
@dataclass(frozen=True)
class CommittedWorld:
    """@brief 单写入者最近一次成功提交（或第一次写入时读到）的世界摘要；只整体替换，从不原地修改。"""

    #: 已提交世界版本。
    version: int
    #: 已提交世界时钟（UTC 纪元秒）。
    current_time: int
    #: 已提交账户余额的副本；世界没有 accounts 字段时为 None（只有存储单元测试的最小世界如此）。
    accounts: dict[str, int] | None
    #: 已提交世界的规范 JSON 字节，与 world_state.body 的 UTF-8 编码逐字节相同。
    body: bytes

    @classmethod
    def of(cls, state: dict, body: bytes) -> CommittedWorld:
        """@brief 由已提交世界及其规范字节建立摘要；账户复制一份，不与调用方共享。"""
        accounts = dict(state["accounts"]) if "accounts" in state else None
        return cls(state["version"], state["current_time"], accounts, body)

    def ledger_view(self) -> dict:
        """@brief 账本核对用的已提交世界：只含 accounts；世界没有账户时为空字典，账本核对随之跳过。"""
        return {} if self.accounts is None else {"accounts": self.accounts}


def _validate_step(committed: CommittedWorld, state: dict, events: list[EventDraft]) -> None:
    """@brief 一步世界推进只能把版本恰好加一、时钟不回退，并且至少带一条因果事件；同版本写入只能走证据追加。"""
    if type(state["version"]) is not int or type(state["current_time"]) is not int:
        raise IntegrityError("World version and current time must be integers")
    if state["current_time"] < committed.current_time:
        raise IntegrityError("World time cannot move backwards")
    if state["version"] != committed.version + 1:
        raise IntegrityError("World state version is discontinuous")
    if not events:
        raise IntegrityError("World state changes require at least one causal event")
```

`FootprintStore.__init__` (`store.py:44-55`) gains, after `self._started`:

```python
        #: 已提交世界缓存：第一次走写入路径时载入（_committed_world），只在推进世界的事务成功后整体替换。
        self._committed: CommittedWorld | None = None
```

Class docstring (`store.py:42`) becomes `"""@brief 环境 actor 独占写入并缓存已提交世界；其他消费者只读取固定导出。"""`.

New and changed methods (signatures and bodies; Doxygen texts as given):

```python
    def _committed_world(self) -> CommittedWorld:
        """@brief 返回已提交世界缓存；第一次调用时读并解析 world_state 一次，只读的打开者从不付这笔开销。"""
        if self._committed is None:
            body = self.connection.execute("SELECT body FROM world_state WHERE singleton=1").fetchone()[0]
            self._committed = CommittedWorld.of(json.loads(body), body.encode())
        return self._committed

    def require_committed(self, world: dict) -> None:
        """@brief 核对调用方的世界与已提交世界的规范 JSON 逐字节相同（引擎世界等于已提交世界），不写任何东西。

        @param world 调用方的世界：WorldState.model_dump(mode="json")，或存储单元测试的最小世界
        @exception IntegrityError 字节不同（Engine world differs from the committed world）
        """
        committed = self._committed_world()
        if canonical_json(world) != committed.body:
            logging.error("Engine world differs from the committed world at version %d", committed.version)
            raise IntegrityError("Engine world differs from the committed world")

    def commit(self, base: dict, state: dict, events: list[EventDraft]) -> list[Event]:
        """@brief 原子提交一步世界推进：base 逐字节等于已提交世界，state 恰好推进一个版本并带至少一条因果事件。

        任一检查或写入失败则整个事务回滚、缓存不变；成功后缓存换成 state。
        @param base 这一步出发时的世界（引擎当前世界的 JSON 形态）
        @param state 已按加载口径复校的新世界（validated_payload 的结果）
        @param events 本步的因果事件，按提交顺序
        @return 分配了序号与前后版本的事件
        """
        committed = self._committed_world()
        self.require_committed(base)
        _validate_step(committed, state, events)
        validate_account_transition(committed.ledger_view(), state, events, self.last_event_seq == 0)
        records = self._build_events(committed.version, state["version"], state["current_time"], events)
        body = canonical_json(state)
        try:
            with self.connection:
                for record in records:
                    self._insert_event(record)
                self._require_stored_version(committed.version)
                self.connection.execute("UPDATE world_state SET version=?,body=? WHERE singleton=1",
                                        (state["version"], body.decode()))
        except Exception:
            logging.exception("Footprint transaction failed; no state or event was committed")
            raise
        self._committed = CommittedWorld.of(state, body)
        return records

    def append_evidence(self, events: list[EventDraft]) -> list[Event]:
        """@brief 同版本追加证据事件：不接收、不改写世界，事件的前后版本都是已提交版本。

        观察、模型请求与响应、记忆更新、决策开始与行动请求、运行失败和零点计划失败证据都走这里；证据不能带资金变化，
        初始注资只属于创建世界的那一步推进。
        @param events 证据事件，按提交顺序，至少一条
        @return 分配了序号的事件
        @exception IntegrityError 没有事件、带资金变化、时刻越界、原因缺失、素材损坏，或库里的世界被别的写入者改过
        """
        if not events:
            logging.error("Evidence append received no event")
            raise IntegrityError("Evidence append requires at least one event")
        committed = self._committed_world()
        validate_account_transition(committed.ledger_view(), committed.ledger_view(), events, False)
        records = self._build_events(committed.version, committed.version, committed.current_time, events)
        try:
            with self.connection:
                for record in records:
                    self._insert_event(record)
                self._require_stored_version(committed.version)
        except Exception:
            logging.exception("Evidence append failed; no event was committed")
            raise
        return records

    def _require_stored_version(self, version: int) -> None:
        """@brief 在调用方已开始的写事务内核对库里的世界版本仍是缓存版本（按预期版本写入）；不同即拒绝，事务随之回滚。"""
        stored = self.connection.execute("SELECT version FROM world_state WHERE singleton=1").fetchone()[0]
        if stored != version:
            logging.error("Stored world version %d is not the cached version %d", stored, version)
            raise IntegrityError("World state was changed outside this store")
```

`_require_stored_version` must run after at least one insert of the same `with self.connection:` block, because
Python's `sqlite3` opens the implicit transaction only before the first data-modifying statement; every caller
satisfies this (`commit` and `append_evidence` have at least one event, `checkpoint` inserts its row first).

`_build_events` (`store.py:125-146`) changes signature to
`_build_events(self, version_before: int, version_after: int, now: int, drafts: list[EventDraft]) -> list[Event]`;
its docstring becomes `"""@brief 只给因果已存在且素材完整的事件分配严格全序；now 是事件时刻的上界，即写入后的世界时钟。"""`.
In the body `state["current_time"]` (`:133`, `:135`) becomes `now`, and the `Event(...)` arguments
`state_version_before=before["version"], state_version_after=state["version"]` (`:143`) become
`state_version_before=version_before, state_version_after=version_after`. Everything else is unchanged.

`_last_event_time` (`store.py:163-166`):

```python
    def _last_event_time(self) -> int:
        """@brief 按主键取最近事件的发生时刻（只取这一个字段，不解析整条正文）；空库以已提交世界时钟为界。"""
        row = self.connection.execute(
            "SELECT json_extract(body,'$.occurred_at') FROM events ORDER BY event_seq DESC LIMIT 1").fetchone()
        return row[0] if row else self._committed_world().current_time
```

`checkpoint` (`store.py:292-318`):

```python
    def checkpoint(self, state: dict, snapshots: dict[str, AssetRef]) -> Checkpoint:
        """@brief 调用方世界逐字节等于已提交世界、素材完整后，以一个事务提交共同检查点及其事件。

        载荷只按规范 JSON 编码一次：存储正文就是这份字节，record_sha256 是它的 SHA-256（与 sha256_json(payload) 相同）。
        """
        self.require_committed(state)
        if not snapshots:
            logging.error("Checkpoint received no participant snapshot")
            raise IntegrityError("Checkpoint requires participant snapshots")
        self._validate_participants(snapshots)
        for reference in snapshots.values():
            self.assets.read(reference)
        payload = self._checkpoint_payload(state, snapshots)
        encoded = canonical_json(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        checkpoint = Checkpoint(**payload, record_sha256=digest)
        committed = self._committed_world()
        draft = EventDraft(event_type="checkpoint_created", occurred_at=committed.current_time,
                           visibility="system", data={"checkpoint_id": checkpoint.checkpoint_id})
        records = self._build_events(committed.version, committed.version, committed.current_time, [draft])
        try:
            with self.connection:
                self.connection.execute("INSERT INTO checkpoints VALUES(?,?,?)", (
                    checkpoint.checkpoint_id, encoded.decode(), digest,
                ))
                self._insert_event(records[0])
                for reference in snapshots.values():
                    self.connection.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (
                        reference.sha256, canonical_json(reference.model_dump()).decode(),
                    ))
                self._require_stored_version(committed.version)
        except Exception:
            logging.exception("Common checkpoint transaction failed")
            raise
        return checkpoint
```

Deleted: `_validate_state` (`store.py:115-123`). Unchanged: `create`, `state`, `last_event_seq`,
`_validate_references`, `_insert_event` (its signature stays, tests patch it), every read method,
`_validate_participants`, `_checkpoint_payload`, `get_checkpoint`, `close`. All functions stay under 50 lines and five
parameters; `store.py` grows by about 60 lines.

### `src/worldmock/life/engine_context.py`

Imports become `from worldmock.footprints.models import Event, EventDraft` and
`from worldmock.life.models import WorldState, validated_payload`. Docstring of the `state` field (`:25`) becomes
`#: 当前已提交状态；只由 commit_step 在一步世界推进成功后替换。`. New method after `validate`:

```python
    def commit_step(self, staged: WorldState, drafts: list[EventDraft]) -> list[Event]:
        """@brief 提交一步世界推进并换上新世界。

        存储先核对本上下文的当前世界与已提交世界逐字节相同（暂存时原地修改共享条目而漏进当前世界的改动在此拒绝），
        再与 drafts 同一事务写入复校过的 staged；成功后 staged 成为当前世界，失败时当前世界不变。
        @param staged 版本已加一的候选世界
        @param drafts 本步的因果事件
        @return 已提交事件
        @exception IntegrityError 当前世界与已提交世界不同、候选世界违反不变量，或版本、时钟、账本、事件核对失败
        """
        events = self.store.commit(self.state.model_dump(mode="json"), validated_payload(staged), drafts)
        self.state = staged
        return events
```

### `src/worldmock/life/models.py`

Add `import copy` to the module imports. Before `class WorldState` (`:566`):

```python
#: 条目只替换、从不原地修改的两个世界集合（docs/design/09-io-formats.md「世界、人物与活动」）：深复制世界时换新字典、共享条目。
_SHARED_ENTRY_FIELDS = frozenset({"activities", "processed_actions"})
```

New method in `WorldState`, after the field declarations and before `validate_world` (`:597-598`):

```python
    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> "WorldState":
        """@brief 深复制世界：activities 与 processed_actions 换新字典、共享条目，其余字段照常深复制。

        条目是不可变值，变化一律换成新条目（docs/design/09-io-formats.md「世界、人物与活动」），所以候选世界与已提交世界
        共享没变的条目时互不影响。model_copy(deep=True) 与 copy.deepcopy 都经过这里。条目类不定义 __deepcopy__：
        model_copy(update=..., deep=True) 会把更新写进 __deepcopy__ 返回的对象，返回共享条目就会改坏已提交世界。
        @param memo copy.deepcopy 的备忘表；model_copy 不传时新建一张，整份世界共用
        @return 新的世界实例
        """
        memo = {} if memo is None else memo
        copied = self.__copy__()
        for name, value in self.__dict__.items():
            copied.__dict__[name] = dict(value) if name in _SHARED_ENTRY_FIELDS else copy.deepcopy(value, memo)
        return copied
```

`ActivityState` (`:347`) and `StoredAction` (`:519`) are unchanged. `WORLD_SCHEMA_VERSION` is unchanged.

### `src/worldmock/life/engine.py`

- Delete `from worldmock.life.models import validated_payload` (`:5`).
- `observe` (`:88-89`): the two statements given under "Evidence sites".
- `fail_run` (`:168-169`): `self.context.store.append_evidence([world_ops._failure_draft(self.context.state.current_time,
  error, None)])` on one line (116 characters).
- `checkpoint` (`:183`): `committed = self.context.store.checkpoint(self.context.state.model_dump(mode="json"),
  snapshots)` on one line (104 characters).

### Other production call sites

| File | Today | Change |
| --- | --- | --- |
| `life/model_evidence.py` | `:16` import; `:71-72`; `:119` | delete the import; `append_evidence` as in the evidence table |
| `life/decision_coordinator.py` | `:47` import; `:133-134`; `:188-189`; `:251-252`; `:364-366` | delete the import; `append_evidence`; `commit_step` (twice, the assignments `self.context.state = staged` go away); the replacement loop under "The two in-place entry edits" |
| `life/time_advance.py` | `:54` import; `:145-147`; `:216-218`; `:326` | delete the import; `staged.version = ...` then `self.context.commit_step(staged, drafts)` and `self.context.commit_step(staged, [*evidence, draft])`; `changed=record_arrival(changed,agent)` |
| `life/counterfactual_runtime.py` | `:10` import; `:57-58` | delete the import; `event = self.context.commit_step(staged, [draft])[0]` |
| `life/world_setup.py` | `:126`, `:134` | `initial = validated_payload(state)` before `:126`; `FootprintStore.create(Path(run_dir), manifest, initial)`; `store.commit(initial, validated_payload(state), [injection, entered, *drafts])` |
| `life/travel.py` | `:107-110` `record_arrival(activity, agent) -> None` | `record_arrival(activity: ActivityState, agent: AgentState) -> ActivityState`, docstring `"""@brief 行程完成后返回记下已结算位置事实的新活动条目（条目只替换、不原地修改）；没完成时原样返回。"""` |
| `life/presentation.py` | docstring `:44-46` | the three lines given below |
| `footprints/checkpoint.py` | `:138-140` | the three lines given below; the draft's fields are unchanged |
| `runtime.py` | `:639`, `:660`, `:674`, `:683` | delete both local imports; `engine.context.store.append_evidence(evidence)` twice |

`presentation.py:44-46` (the docstring of `stage_presentation`) becomes:

```text
    观察只追加证据、不写世界（`FootprintStore.append_evidence` 不接收世界），因此观察先把留痕记在引擎内存，
    随本轮提交按人物写入 `same_moment_presentation`；同一秒已有暂存的人物合并对象时刻，
    保留该秒首轮置顶的浏览结果（docs/design/04-simulation-engine.md「原子性与恢复」）。
```

`checkpoint.py:138-140` becomes:

```python
        child.commit(checkpoint.state, state, [EventDraft(
            event_type="environment_event", occurred_at=state["current_time"], visibility="system",
            data={"kind": "branch_created", "parent_checkpoint_id": checkpoint.checkpoint_id})])
```

No AgentSociety2 file or patch changes; `AS2_LOCKED_SOURCE_SHA256` is unchanged. `RULES_VERSION`, the export manifest
version, the run manifest version and every dataset package are unchanged.

### Test helper `tests/committed_world_guard.py` (new, not production)

```python
"""@brief 测试用已提交世界守卫：LifeEngine 的任何公开方法都不得原地改动它开始时的那份世界。"""

from __future__ import annotations

import functools
import inspect

from worldmock.footprints.models import canonical_json


def public_methods(cls) -> tuple[str, ...]:
    """@brief 类自身定义的公开实例方法名：不以下划线开头的普通函数（类方法、静态方法与属性不算）。"""
    return tuple(name for name, value in vars(cls).items() if not name.startswith("_") and inspect.isfunction(value))


class CommittedWorldGuard:
    """@brief 包住 LifeEngine 的每个公开方法，记下改动了调用开始时那份世界的调用；夹具在用例结束时断言没有记录。"""

    def __init__(self) -> None:
        #: 改动了开始时世界的调用，按发生先后，形如 "LifeEngine.advance changed the world it started from"。
        self.leaks: list[str] = []

    def install(self, monkeypatch) -> None:
        """@brief 用 monkeypatch 包住 LifeEngine 类上当前的全部公开方法，用例结束时自动还原。"""
        from worldmock.life.engine import LifeEngine

        for name in public_methods(LifeEngine):
            monkeypatch.setattr(LifeEngine, name, self._guarded(getattr(LifeEngine, name)))

    def _guarded(self, method):
        """@brief 调用前后各编码一次调用开始时的世界对象；字节不同即记一条，原异常照常抛出。"""
        @functools.wraps(method)
        def call(engine, *args, **kwargs):
            world = engine.context.state
            before = canonical_json(world.model_dump(mode="json"))
            try:
                return method(engine, *args, **kwargs)
            finally:
                if canonical_json(world.model_dump(mode="json")) != before:
                    self.leaks.append(f"LifeEngine.{method.__name__} changed the world it started from")
        return call
```

The comparison is on the world object the call started from, so a staging call that commits (and replaces
`context.state`) is checked for leaks into the old committed world, and a read call is checked for edits of the
current one. The wrapped methods today are `observe`, `due_reflection`, `begin_decision_round`, `submit_action`,
`commit_decision_round`, `abort_decision_round`, `scheduling_state`, `record_model_exchange`, `record_memory_update`,
`advance`, `decide_midnight_rhythm`, `register_day_plan`, `read_task_summary`, `apply_counterfactual`,
`meal_windows_by_agent`, `fail_run`, `checkpoint` and `close`; a public method added later is wrapped automatically.
Engines running in other processes (Ray actors, CLI subprocesses) are outside the guard.

Autouse fixture added to `tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def committed_world_guard(monkeypatch):
    """@brief 每条用例包住 LifeEngine 的公开方法：调用不得原地改动它开始时的已提交世界；用例结束时不得留有记录。"""
    from tests.committed_world_guard import CommittedWorldGuard

    guard = CommittedWorldGuard()
    guard.install(monkeypatch)
    yield guard
    assert guard.leaks == [], f"Committed world changed in place: {guard.leaks}"
```

Both imports stay lazy: the fixture imports the helper inside its body and the helper imports `LifeEngine` inside
`install`, so a collection-only pytest run still loads no engine (`tests/system/cli/test_cli.py:73-84` asserts this in
a subprocess). The helper's module-level import is `worldmock.footprints.models` only.

A test that provokes a leak on purpose requests `committed_world_guard`, asserts the recorded entry and clears
`committed_world_guard.leaks`. There is no opt-out marker. An edit a test makes outside any engine call is not a
leak; such edits of shared entries are still forbidden in test code (see "Existing tests that change").

## Design chapters

Changed before the code, in Chinese, each rule in one place.

### `docs/design/05-recording-engine.md`「权威事实与投影」

After the paragraph that ends with 「见「显示写法与检查工具」。」, add:

> 运行库只有环境 actor 一个写入者，写入分两种，每次都是一个 SQLite 事务。
>
> - **一步世界推进**：版本恰好加一的新世界与至少一条因果事件。调用方同时交上这一步出发时的世界，存储先核对它与已提交
>   世界的规范 JSON 逐字节相同，不同即以 `Engine world differs from the committed world` 拒绝；新世界在写入前已按加载口径
>   复校。事件的 `state_version_before` 与 `state_version_after` 是推进前后的两个版本。
> - **证据追加**：只写事件，不接收、不改写世界。观察、模型请求与响应、记忆更新、`decision_started` 与
>   `action_requested`、`run_failed` 和零点计划失败时的模型证据都是证据追加，事件的前后版本都等于当前已提交版本。
>   证据追加不能带资金变化；初始注资只属于创建世界的那一步推进。
>
> 检查点不改世界：调用方交上的世界必须与已提交世界逐字节相同，载荷只按规范 JSON 编码一次，存储的正文就是这份字节，
> `record_sha256` 是它的 SHA-256。
>
> 写入者在内存里保存最近一次提交的世界的版本、时钟、账户与规范字节：第一次写入时从库里读一次，之后只在推进世界的事务
> 成功后换成新世界；最近事件的发生时刻每次按主键从库里取，不缓存。每个写事务提交前核对库里的世界版本仍是缓存的版本，
> 不同说明有别的写入者，以 `World state was changed outside this store` 拒绝并整体回滚。只读的打开者（导出、恢复时读取
> 父运行）不载入这份缓存，也就不为它解析世界。

### `docs/design/04-simulation-engine.md`「原子性与恢复」

Replace the first sentence 「观察只能读取已提交版本。」 by:

> 观察只能读取已提交版本。引擎内存里的当前世界必须始终与已提交世界的规范 JSON 逐字节相同：写观察事件之前、每一步
> 世界推进提交之前各比较一次（检查点的比较见[记录引擎](05-recording-engine.md)「权威事实与投影」），不同即运行故障，
> 报 `Engine world differs from the committed world`，不写这条观察、不调用模型、不提交这一步；运行失败事件不比较，
> 照常写入。模型请求与响应、记忆更新、行动请求和零点计划失败时的模型证据只追加证据，不比较。
>
> 候选世界是当前世界的副本：活动与已处理动作两个集合各换一个新容器，与当前世界共享没有变的条目，其余部分完整复制。
> 候选世界改动条目一律换成新条目、从不原地修改（见[状态与序列化](09-io-formats.md)「世界、人物与活动」），所以候选世界
> 被丢弃或被提交，当前世界都保持原样，推进前后的比较（阈值回调、导航前台、购买前台区间、停留进出）总能看到真实的
> 推进前世界。暂存时原地修改共享条目会改动当前世界，在这一步提交之前的比较里被拒绝。

Precisely: line 738 starts with that sentence. The two paragraphs above replace it, and the rest of line 738
(「请求身份重放读取原结果，……」) together with lines 739-740 then forms the next paragraph, unchanged. The rest of the
section (request-identity replay, budgets) is unchanged by this chapter; chapter 02 edits the rest of line 738 and
lines 742-746 on top of this.

### `docs/design/09-io-formats.md`「世界、人物与活动」

After the paragraph that starts with 「活动种类为 sleep、travel、interact、purchase、query。」, add:

> `activities` 与 `processed_actions` 的条目是不可变值：活动或已处理动作的任何变化（包括 `data`、`result`、`receipt`、
> `session_ids`、`note_history`、`resource_claims` 等嵌套字段）都换成一个新条目，不原地修改已有条目；同一个条目对象可以
> 同时属于已提交世界和候选世界。同一个对象不同时挂在条目与世界的其他字段下。

No other design section changes. `docs/manual/` is unaffected (no user step changes).

## Tests

All new tests use English messages in assertions where they match production text, Chinese docstrings, and the
existing fixtures (`engine_factory`, `tests.life.conftest`, `tests.life.environment_runtime`).

### New test files

`tests/unit/footprints/test_store_commit_path.py` — store-level rules. A fixture `store` creates a run with the
minimal world `{"version": 0, "current_time": 100, "accounts": {"agent:1": 100, "external:scenario": -100}}` and
manifest `{"run_id": "run", "agent_ids": [1]}`; helper `_row(store)` returns
`store.connection.execute("SELECT version, body FROM world_state").fetchone()`.

| Test | Asserts |
| --- | --- |
| `test_evidence_append_leaves_the_world_row_untouched_and_keeps_the_version` | `append_evidence` of two observations returns seqs `[1, 2]`; both events have `state_version_before == state_version_after == 0`; `_row(store)` is identical before and after, byte for byte |
| `test_version_step_writes_the_canonical_world_and_advances_one_version` | `commit(store.state, new, [event])` with `new` version 1: the row is `(1, canonical_json(new).decode())`; the event's versions are `(0, 1)` |
| `test_version_step_refuses_a_base_that_differs_from_the_committed_world` | a base with one changed value raises `IntegrityError` matching `^Engine world differs from the committed world$`; `caplog` holds `Engine world differs from the committed world at version 0`; no event; row unchanged |
| `test_consecutive_version_steps_and_appends_follow_the_cached_world` | step to v1 with base `store.state`; evidence; step to v2 with base = the v1 dict passed before; a further step whose base is the v1 dict is refused with the message above; event version pairs are `[(0, 1), (1, 1), (1, 2)]` |
| `test_evidence_append_refuses_money_and_initial_funding` | an `action_result` with balanced `account:` changes raises `Account balances disagree with committed financial events`; on a run without events an `initial_funds_injected` draft raises `Initial funding must be the first event of world creation`; `last_event_seq` stays 0 |
| `test_evidence_append_refuses_an_empty_list` | `append_evidence([])` raises `^Evidence append requires at least one event$` and logs `Evidence append received no event` |
| `test_evidence_append_refuses_an_event_earlier_than_the_last_event` | evidence at 100; step to time 120 with an event at 120; evidence at 110 raises `Event time is out of committed world order` |
| `test_last_event_time_sees_events_inserted_outside_the_write_path` | evidence at 100 (the cache is loaded); step to time 130 with an event at 100; then, as `restore_branch` does (`checkpoint.py:133-135`), `with store.connection: store._insert_event(Event(**draft.model_dump(), run_id="run", event_seq=store.last_event_seq + 1, recorded_at="2026-10-15T00:00:00+00:00", wall_elapsed_ms=0, state_version_before=1, state_version_after=1))` for an `environment_event` draft at 120; evidence at 110 now raises `Event time is out of committed world order`, and evidence at 120 is accepted with versions `(1, 1)`. A last event time cached at load or kept by `commit` would accept 110 |
| `test_a_write_after_another_connection_changed_the_world_is_refused[append]`, `[step]`, `[checkpoint]` | after one write by `store`, a second `FootprintStore(run_dir)` takes a version-advancing commit at the same world time and closes; then `store`'s evidence append, version-advancing commit (base = its last committed dict) or checkpoint (world = that dict) raises `^World state was changed outside this store$`; `last_event_seq` and the checkpoint row count are unchanged |
| `test_checkpoint_body_and_record_hash_are_the_canonical_payload` | after a step, `point = store.checkpoint(state, snapshots)`; `_stored_body(store, point.checkpoint_id) == canonical_json(point.model_dump(exclude={"record_sha256"}))` and `point.record_sha256 == sha256_json(point.model_dump(exclude={"record_sha256"})) == hashlib.sha256(_stored_body(...)).hexdigest()`; `get_checkpoint` returns an equal record. `_stored_body` is a module helper reading the row (chapter 02 changes only this helper) |
| `test_cache_loads_once_at_the_first_write` | with a world padded by `"filler": "x" * 200_000` and the counters below: opening, `last_event_seq`, `last_agent_event_seq`, `read_agent_events` and `last_event_seq_before` parse no world; the first `append_evidence` parses one; further appends, a step and a checkpoint parse none (`read_events` is not used: chapter 04 deletes it) |
| `test_evidence_append_neither_parses_nor_encodes_the_world` | after the first write, an append makes zero world-sized `json.loads` and zero world-sized `canonical_json` calls |
| `test_version_step_parses_nothing_and_encodes_the_base_and_the_new_world` | after the first write, a step makes zero world-sized parses and exactly two world-sized encodes |
| `test_checkpoint_parses_nothing_and_encodes_the_world_and_the_payload_once` | after the first write, a checkpoint makes zero world-sized parses and exactly two world-sized encodes |

Counters: `monkeypatch.setattr(store_module, "json", SimpleNamespace(loads=counting_loads))` and
`monkeypatch.setattr(store_module, "canonical_json", counting_encode)` on `worldmock.footprints.store`; a call is
world-sized when its input text (loads) or output bytes (encode) is at least 200,000 long. `store.py` uses only
`json.loads` from `json`. These tests create the run with `FootprintStore.create` (which encodes the initial world)
and close it before installing the counters, then open a fresh `FootprintStore(run_dir)`. The base of a step and the
world of a checkpoint are dicts the test holds: the dict the run was created with, and the new world of its last step
(`{**initial, "version": 1, "current_time": 110}` with an event at 110). The counted code never calls `store.state`,
which parses the world.

`tests/unit/life/test_world_state_copy.py` — the copy hook, on a real world: `engine_factory()`, then
`act(engine, "walk", travel("公司"))` so the world holds at least one activity and one processed action.

| Test | Asserts |
| --- | --- |
| `test_deep_copy_gives_fresh_entry_containers_that_share_the_entries` | for `copied = state.model_copy(deep=True)`: `copied.activities is not state.activities`, same for `processed_actions`; every entry `is` the original's; `list(copied.activities) == list(state.activities)`; canonical bytes of both dumps are equal |
| `test_every_other_field_is_a_full_deep_copy` | `copied.agents[1] is not state.agents[1]`, `copied.agents[1].body is not state.agents[1].body`, `copied.sessions` entries and `copied.pending_events` items are distinct objects; assigning `copied.agents[1].location_id = "x"` and `copied.accounts["agent:1"] = 0` leaves `state` byte-unchanged |
| `test_update_copy_and_copy_module_share_entries_and_leave_the_original_unchanged` | `state.model_copy(update={"current_time": state.current_time + 1}, deep=True)` and `copy.deepcopy(state)` share the entries; `state` bytes unchanged; the update copy's `current_time` is the new value |
| `test_copying_one_entry_still_copies_its_nested_data` | for a shared entry `entry = copied.activities[key]`, `changed = entry.model_copy(update={"name": entry.name + "x"}, deep=True)` is a new object whose `data` is a new dict, and `state.activities[key].name` is unchanged (the reviewer's corruption scenario cannot occur) |

`tests/integration/life/test_committed_world_guard.py` — the runtime guard and the fixture.

| Test | Asserts |
| --- | --- |
| `test_in_place_edit_of_a_shared_entry_is_refused_before_the_step_commits` | after `_, started = act(engine, "walk", travel("公司"))` and settling any due decision as `finish_activity` does (`settle_decision(engine, f"settle-{n}")` with a counter `n` while `scheduling_state().ready_agent_ids`), monkeypatch `worldmock.life.same_moment.reset_same_moment` (called on the staged copy at `time_advance.py:105`) with a wrapper that sets `state.activities[started.activity_id].data["leaked"] = True` before calling the original; `advance_next(engine)` raises `^Engine world differs from the committed world$`; `last_event_seq` and `store.state` are unchanged; `committed_world_guard.leaks == ["LifeEngine.advance changed the world it started from"]`, then cleared |
| `test_observation_refuses_an_engine_world_that_differs_from_the_committed_world` | after `begin_decision_round`, the test sets `engine.context.state.weather_by_date["2000-01-01"] = "晴"` (outside any engine call); `engine.observe(decision)` raises the same message; no event was appended; the guard records nothing |
| `test_run_failure_is_recorded_even_when_the_engine_world_differs` | after the same edit, `engine.fail_run("probe failure")` raises nothing, `last_event_seq` grows by one and `events(engine, "run_failed")` (`tests/life/environment_runtime.py`) has exactly one entry |
| `test_every_staging_entry_refuses_a_diverged_engine_world[advance]`, `[commit]`, `[abort]` | the same edit outside any engine call, made just before the call under test: `[advance]` after `act(engine, "walk", travel("公司"))` and the settling loop above, then `advance_next(engine)`; `[commit]` after `begin_decision_round`, `observe` and `submit_action` of `travel("公司")` (as `act` does, label `probe`), then `engine.commit_decision_round("probe")`; `[abort]` the same, then `engine.abort_decision_round("probe", "probe abort")`. Each raises `^Engine world differs from the committed world$`; `last_event_seq` and `store.state` equal their values just before the call; the guard records nothing. `register_day_plan`, `decide_midnight_rhythm` and `apply_counterfactual` reach the same `commit_step` |
| `test_guard_wraps_every_public_engine_method` | `{"observe", "advance", "commit_decision_round", "abort_decision_round", "register_day_plan", "decide_midnight_rhythm", "apply_counterfactual", "checkpoint", "record_model_exchange", "record_memory_update", "submit_action", "begin_decision_round", "scheduling_state", "due_reflection", "read_task_summary", "fail_run", "meal_windows_by_agent", "close"} <= set(public_methods(LifeEngine))`, and each of them on `LifeEngine` has `__wrapped__` during the test |

`tests/system/lifecycle/test_structural_sharing_day.py` — the permanent differential for structural sharing
(`PROJECT = Path(__file__).resolve().parents[3]` and `record_complete_day` from `tests/life/day_baseline_runtime.py`,
as in `test_complete_day_baseline.py`; `BaseModel` from pydantic, `WorldState` from `worldmock.life.models`):

| Test | Asserts |
| --- | --- |
| `test_shared_entry_copies_record_the_same_day_as_full_deep_copies[activity]`, `[callback]` | `record_complete_day(monkeypatch, tmp_path / "shared" / kind, PROJECT, kind)`, then `monkeypatch.setattr(WorldState, "__deepcopy__", BaseModel.__deepcopy__)` and the same call under `tmp_path / "deep" / kind`; the two results are equal (persona and config hashes, environment identity, seed, and the whole `day_baseline_signature`: every export artifact including events.jsonl with every observation, every checkpoint body, every run asset) |

### Byte-identity proof for this chapter

1. `tests/system/lifecycle/test_complete_day_baseline.py` passes unchanged against the existing
   `tests/fixtures/dayrun-activity-baseline.json` and `dayrun-callback-baseline.json`, which were recorded by today's
   store and deep copies. This chapter does not re-freeze them. They compare every export artifact (events with every
   observation, activities, purchases, app usage, stays, quality.json, timelines), every checkpoint row and every run
   asset, with only machine paths normalized and ids and wall clocks fixed by `deterministic_day_runtime`
   (`tests/dayrun_baseline.py:27-39`; this chapter adds no `uuid4` call, drops none and keeps their order). Both cases
   pass on `0bd2de65` on the Windows development machine (`.work/lrc-gates/baseline-main/s6.xml`: 37.9 s and 20.2 s).
   On a machine where they do not pass at `0bd2de65` (issue #5), run the same `record_complete_day` on `0bd2de65` and
   on the change on that machine and require equal results (the masked complete-day comparison of chapter 08).
2. `test_structural_sharing_day.py` above: shared against full deep copies on the same machine, every platform.
3. `test_store_commit_path.py`: the world row, event version pairs and checkpoint body and hash are defined by
   `canonical_json` of the same values, as today.
4. The autouse guard over the whole suite: zero leaks.
5. Research evidence, not part of the suite:
   - `spec-01-store-commits/equivalence01.json`: main's store against the prototype of this API, all four tables
     identical;
   - `opt-commit/compare_runs.json`: 631 integration databases, no event, world or checkpoint field differs;
   - `spec-review-01-store-commits/sharing_day.json`: both scripted complete days with this chapter's
     `WorldState.__deepcopy__` against pydantic's own, with the guard of this chapter wrapping every public engine
     method: `record_complete_day` results equal for both kinds, no leak recorded.

### Existing tests that change

Three mechanical rules:
- **Evidence rule.** A same-version `store.commit(store.state, drafts)` (or `commit(state.model_dump(mode="json"),
  drafts)` without a version change) becomes `store.append_evidence(drafts)`.
- **Step rule.** A version-advancing commit `store.commit(new_state, drafts)` becomes `store.commit(store.state, new_state, drafts)`.
- **Helper rule.** An engine-level test helper that stages a world, writes `staged.model_dump(mode="json")` and assigns
  `engine.context.state = staged` keeps doing exactly that, with the engine world as base:
  `engine.context.store.commit(engine.context.state.model_dump(mode="json"), staged.model_dump(mode="json"), [...])`,
  then `engine.context.state = staged`. Helpers do not use `commit_step`: today they write without `validated_payload`
  (some stage worlds the engine cannot produce, such as `tamper_orders`), and adding validation to test setup is not
  this chapter's change. Every one of them copies the engine world first (`model_copy(deep=True)`) and edits only
  fields that are not entries, so their base equals the committed world.

| File | Change |
| --- | --- |
| `tests/unit/footprints/test_store.py` | Evidence rule at `:40`, `:50`, `:71`, `:131`, `:194`, `:201`, `:206`, `:213`; step rule at `:28`, `:33`, `:88`, `:96`, `:109`, `:129`, `:132`, `:143`, `:155`, `:165`, `:181`, `:226` (`:33` passes base `state` and stays refused, now as `World state version is discontinuous`). `test_sql_failure_rolls_back_money_and_all_events` keeps its asserts and then takes the same step again, `store.commit(store.state, {'version': 1, 'current_time': 110, 'balance': 30}, events)` with the same drafts (the patched `_insert_event` fails only on its second call), and asserts `store.state == {'version': 1, 'current_time': 110, 'balance': 30}` and `store.last_event_seq == 2`. The docstring at `:313` says 「commit 的版本步核对」 instead of `_validate_state` |
| `tests/unit/footprints/test_branch_coverage.py` | Evidence rule at `:361`, `:387`, `:653`, `:673`, `:733`, `:738`; step rule at `:748`; `test_store_commit_rejects_clock_regression_and_version_jumps` (`:342-356`) passes `base = store.state` to all four calls (the `{**store.state, "extra": 1}` case is still refused as discontinuous); `test_store_checkpoint_rejects_stale_state_and_empty_snapshots` matches `differs from the committed world` at `:373`; `:375` already matches `participant snapshots` and is unchanged |
| `tests/unit/footprints/test_ledger.py` | Fixture `funded`: initial funding becomes the world-creation step `store.commit(store.state, {**store.state, "version": 1}, [injection])`; the transfer states (`:27`, `:43`, `:49`, `:61`) use version 2 and the step rule with base `funded.state`. In `test_reused_receipt_cannot_record_a_second_payment` the refused second payment is the next step, `funded.commit(funded.state, {**state, "version": 3}, [transfer(...)])`, matching `Account balances disagree with committed financial events` (the reason it is refused today); `last_event_seq == 2` stays |
| `tests/unit/footprints/test_status.py` | Evidence rule at `:60` |
| `tests/integration/footprints/test_verified_experiences.py` | Evidence rule at all 21 sites (`:59`–`:261`) |
| `tests/integration/footprints/test_export.py` | Evidence rule at `:24` |
| `tests/integration/footprints/test_event_fields.py` | Evidence rule at `:186` (same `ValueError` from `_build_events`) |
| `tests/integration/footprints/test_render_day.py` | Step rule at `:59` |
| `tests/integration/runtime/test_life_agent_protocol.py` | Evidence rule at `:1446` |
| `tests/integration/runtime/test_verified_memory.py` | Evidence rule at `:126` |
| `tests/integration/runtime/test_host_branch_coverage.py` | Evidence rule at `:519` |
| `tests/integration/runtime/test_daily_reflection.py` | Evidence rule at `:424` |
| `tests/integration/life/test_phone_records.py` | Evidence rule at `:392` |
| `tests/unit/life/test_memory_progress_contract.py` | Step rule at `:279` (base `store.state` in the loop) |
| `tests/integration/life/test_state_serialization.py` | The subprocess script (`:92-106`) calls `store.require_committed(payload)` and then `store.append_evidence([...])`; the cross-seed byte equality is still enforced by the store |
| `tests/life/conftest.py` | Helper rule in `stage_day_plan` (`:55`), `stage_errands` (`:70`), `stage_legacy_control_request` (`:98`) and `bring_to_window` (`:136`) |
| `tests/life/current_world.py` | Helper rule in `stage_moment` (`:19`) |
| `tests/integration/life/test_purchase_orders.py` | Helper rule in `tamper_orders` (`:103`) |
| `tests/integration/life/test_persisted_dataset_contract.py` | `started()` (`:16-21`) gives the test a private entry: `activity = state.activities[id].model_copy(deep=True)`, `state.activities[id] = activity`, returns it; the tests that edit `activity` in place then edit the private copy, not the engine's shared entry |
| `tests/unit/life/test_environment_contract_gaps.py` | `:87` replaces the entry (`state.activities[id] = entry.model_copy(update={"data": {**entry.data, "navigation_app_id": navigation}})`) instead of editing it |
| `tests/unit/engineering/test_import_graph.py` | Delete the stale local-import registrations `:94` (`_call_plan_day` → `worldmock.life.models`) and `:100` (`_generate_day_plan` → `worldmock.life.models`) |
| `tests/conftest.py` | Add the autouse `committed_world_guard` fixture |

A search found no other test that edits an entry of `activities` or `processed_actions` in place, or edits
`engine.context.state` in place and then expects a version-advancing commit to succeed. Two tests edit the engine world directly
and then make no engine call that writes: `test_shared_observation_contract.py:191` and
`test_external_environment_lifecycle.py:89`. The review's scans (`spec-review-01-store-commits/alias_edits.py`,
`entry_alias_edits.py`) found no edit through a name bound to the engine world or to an entry. Tests that compare
`engine.context.state` with a `model_copy(deep=True)` taken earlier keep their meaning, because entries are never
edited.

### Source mutations

Register in `tests/dataset_acceptance.py` (`MUTATIONS`), each original occurring once in its symbol:

| Name | Symbol | Original | Replacement | Killer tests |
| --- | --- | --- | --- | --- |
| `version_step_skips_the_committed_world_check` | `worldmock.footprints.store::FootprintStore.commit` | `self.require_committed(base)` | `None` | `tests/unit/footprints/test_store_commit_path.py::test_version_step_refuses_a_base_that_differs_from_the_committed_world`, `tests/integration/life/test_committed_world_guard.py::test_in_place_edit_of_a_shared_entry_is_refused_before_the_step_commits`, `tests/integration/life/test_committed_world_guard.py::test_every_staging_entry_refuses_a_diverged_engine_world` |
| `cache_not_advanced_by_a_version_step` | `worldmock.footprints.store::FootprintStore.commit` | `self._committed = CommittedWorld.of(state, body)` | `None` | `tests/unit/footprints/test_store_commit_path.py::test_consecutive_version_steps_and_appends_follow_the_cached_world` |
| `cache_advanced_by_a_failed_version_step` | `worldmock.footprints.store::FootprintStore.commit` | `logging.exception("Footprint transaction failed; no state or event was committed")` | `logging.exception("Footprint transaction failed; no state or event was committed"); self._committed = CommittedWorld.of(state, body)` | `tests/unit/footprints/test_store.py::test_sql_failure_rolls_back_money_and_all_events` |
| `evidence_append_moves_money` | `worldmock.footprints.store::FootprintStore.append_evidence` | `validate_account_transition(committed.ledger_view(), committed.ledger_view(), events, False)` | `None` | `tests/unit/footprints/test_store_commit_path.py::test_evidence_append_refuses_money_and_initial_funding` |
| `evidence_append_claims_a_version_step` | `worldmock.footprints.store::FootprintStore.append_evidence` | `committed.version, committed.version, committed.current_time, events` | `committed.version, committed.version + 1, committed.current_time, events` | `tests/unit/footprints/test_store_commit_path.py::test_evidence_append_leaves_the_world_row_untouched_and_keeps_the_version` |
| `other_writer_not_detected` | `worldmock.footprints.store::FootprintStore._require_stored_version` | `if stored != version:` | `if False:` | `tests/unit/footprints/test_store_commit_path.py::test_a_write_after_another_connection_changed_the_world_is_refused` |
| `last_event_time_reads_the_oldest_event` | `worldmock.footprints.store::FootprintStore._last_event_time` | `ORDER BY event_seq DESC LIMIT 1` | `ORDER BY event_seq LIMIT 1` | `tests/unit/footprints/test_store_commit_path.py::test_evidence_append_refuses_an_event_earlier_than_the_last_event`, `tests/unit/footprints/test_store_commit_path.py::test_last_event_time_sees_events_inserted_outside_the_write_path` |
| `checkpoint_skips_the_committed_world_check` | `worldmock.footprints.store::FootprintStore.checkpoint` | `self.require_committed(state)` | `None` | `tests/unit/footprints/test_branch_coverage.py::test_store_checkpoint_rejects_stale_state_and_empty_snapshots` |
| `observation_skips_the_committed_world_check` | `worldmock.life.engine::LifeEngine.observe` | `self.context.store.require_committed(self.context.state.model_dump(mode="json"))` | `None` | `tests/integration/life/test_committed_world_guard.py::test_observation_refuses_an_engine_world_that_differs_from_the_committed_world` |
| `world_copy_shares_the_entry_containers` | `worldmock.life.models::WorldState.__deepcopy__` | `dict(value) if name in _SHARED_ENTRY_FIELDS` | `value if name in _SHARED_ENTRY_FIELDS` | `tests/unit/life/test_world_state_copy.py::test_deep_copy_gives_fresh_entry_containers_that_share_the_entries` |
| `world_copy_copies_the_entries` | `worldmock.life.models::WorldState.__deepcopy__` | `dict(value) if name in _SHARED_ENTRY_FIELDS` | `copy.deepcopy(value, memo) if name in _SHARED_ENTRY_FIELDS` | `tests/unit/life/test_world_state_copy.py::test_deep_copy_gives_fresh_entry_containers_that_share_the_entries` |
| `world_copy_shares_every_field` | `worldmock.life.models::WorldState.__deepcopy__` | `copy.deepcopy(value, memo)` | `value` | `tests/unit/life/test_world_state_copy.py::test_every_other_field_is_a_full_deep_copy` |

`world_copy_copies_the_entries` is not a behaviour change; it is registered because it removes the saving silently,
and the identity assertions of the killer test are the performance contract. `test_a_write_after_another_connection
_changed_the_world_is_refused` is parametrized; the registration names the function, which runs all three cases. The
two edit rewrites (`_accept_results`, `record_arrival`) get no mutation: an in-place edit there is equivalent today,
because both entries are fresh.

### Acceptance for this chapter

The full suite, the mutation suite with the twelve new mutations, the coverage gate (every new branch is reached by
the tests above), the style gate (`tests/unit/engineering/test_style_gate.py`), the import graph and the
documentation gate pass. Test timings go to `.work/`, as AGENTS.md requires.

The guard costs two world dumps and encodes per public engine call. Measured on the scripted activity day
(`spec-review-01-store-commits/guard_cost.py`): 688 guarded calls, a world of at most 70 KB, 1.7 s of an 8.7 s run,
about 20%. The mutation suite first runs every killer together on the unmutated source within 480 s
(`tests/system/mutations/test_dataset_acceptance.py:15`); that pass already timed out on `0bd2de65` on the loaded
development machine (`.work/lrc-gates/baseline-main/mut.log`). The implementer records the killer pass's time with the
guard in `.work/` and reports it with the change; raising that budget is not part of this chapter.

## Files touched

- `docs/design/04-simulation-engine.md`
- `docs/design/05-recording-engine.md`
- `docs/design/09-io-formats.md`
- `src/worldmock/footprints/store.py`
- `src/worldmock/footprints/checkpoint.py`
- `src/worldmock/life/models.py`
- `src/worldmock/life/engine_context.py`
- `src/worldmock/life/engine.py`
- `src/worldmock/life/model_evidence.py`
- `src/worldmock/life/decision_coordinator.py`
- `src/worldmock/life/time_advance.py`
- `src/worldmock/life/counterfactual_runtime.py`
- `src/worldmock/life/world_setup.py`
- `src/worldmock/life/travel.py`
- `src/worldmock/life/presentation.py`
- `src/worldmock/runtime.py`
- `tests/conftest.py`
- `tests/committed_world_guard.py` (new)
- `tests/dataset_acceptance.py`
- `tests/unit/footprints/test_store_commit_path.py` (new)
- `tests/unit/footprints/test_store.py`
- `tests/unit/footprints/test_branch_coverage.py`
- `tests/unit/footprints/test_ledger.py`
- `tests/unit/footprints/test_status.py`
- `tests/unit/life/test_world_state_copy.py` (new)
- `tests/unit/life/test_memory_progress_contract.py`
- `tests/unit/life/test_environment_contract_gaps.py`
- `tests/unit/engineering/test_import_graph.py`
- `tests/integration/life/test_committed_world_guard.py` (new)
- `tests/integration/life/test_phone_records.py`
- `tests/integration/life/test_purchase_orders.py`
- `tests/integration/life/test_state_serialization.py`
- `tests/integration/life/test_persisted_dataset_contract.py`
- `tests/integration/footprints/test_event_fields.py`
- `tests/integration/footprints/test_export.py`
- `tests/integration/footprints/test_render_day.py`
- `tests/integration/footprints/test_verified_experiences.py`
- `tests/integration/runtime/test_daily_reflection.py`
- `tests/integration/runtime/test_host_branch_coverage.py`
- `tests/integration/runtime/test_life_agent_protocol.py`
- `tests/integration/runtime/test_verified_memory.py`
- `tests/life/conftest.py`
- `tests/life/current_world.py`
- `tests/system/lifecycle/test_structural_sharing_day.py` (new)

No fixture, script, tool, manual page, AgentSociety2 file or patch changes.

## Not in this chapter

| Item | Where |
| --- | --- |
| Compressed checkpoint rows (the stored body in `checkpoint`'s `INSERT`, `get_checkpoint`), compressed workspace snapshots, status reading checkpoint ids only | Chapter 02, state and checkpoint storage |
| Processed actions kept only while readable, the action-id prefix check, deletion of the cross-round replay path | Chapter 02 |
| Activity order kept through checkpoints and the world-state structure version | Chapter 02 |
| Each model request stored once (`model_evidence.py:52-54`), observation parts stored once | Chapter 03, recorded evidence |
| Streaming export, branch by SQL copy (`checkpoint.py:101`, `:133-135`), indexed `fail_run` and counterfactual lookups (`engine.py:165`, `counterfactual_runtime.py:77`), bounded reads, short reads and the 120 s busy timeout (the rollback journal stays; WAL is not proposed) | Chapter 04, whole-history paths |
| Park on transport failure, whole-call deadline, frozen model metadata on resume, readable failures, heartbeat, lineage, ops-log commit time | Chapter 05, network and restarts |
| Open activities computed once, availability evaluated once, memory-episode copies, token count reuse | Chapter 06, per-decision host costs |
| Benchmark kit at 10 MB and 30 MB, recorded-response run, comparator, masked complete-day comparison in every change, backport verification on `49db53cd` | Chapter 08, proof tools |
| `frozen=True` on `ActivityState`/`StoredAction` | Not adopted: the proposal does not specify it; it blocks only top-level assignment and not the nested edits the guard catches |
| `DeepFrozenModel` entries | Not adopted: `model_dump(mode="json")` raises on `mappingproxy` inside `dict[str, Any]` and lists become tuples (reviewer [R]) |
| An entry-level `__deepcopy__` returning `self` | Rejected: corrupts committed entries through `model_copy(update=..., deep=True)` |
| Checking the engine world only at checkpoints (plain A), or a cached-bytes compare at every evidence commit (B), or a dict compare | Not chosen (proposal "Decisions"); the dict compare also confuses `1`, `1.0` and `True` |
| Splicing cached world bytes into the checkpoint payload, faster JSON encoders (orjson) | Not proposed: hand-built JSON, a new dependency and a different canonical form |
| Garbage-collector tuning (`gc.freeze`, thresholds) | Not in the proposal; to be measured on a real dataset day first |
| Batching or deferring evidence commits | Not proposed: each request, response, observation and memory update stays its own durable commit |
| Items awaiting the owner (tool parameters, CLI options, dataset fields) | None is touched here |
