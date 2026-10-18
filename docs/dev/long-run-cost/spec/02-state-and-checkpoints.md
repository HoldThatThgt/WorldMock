# World state and checkpoint storage

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements the proposal's section "State and checkpoint storage":
- "Processed actions only while a rule can read them";
- "Activity order kept through checkpoints";
- "Compressed checkpoint rows and workspace snapshots";
- "Status reads checkpoint ids only".

It applies these rows of the proposal's "Decisions" table:

| Question | Decision applied here |
| --- | --- |
| Checkpoint storage form | zlib-compressed full rows; `record_sha256` over the uncompressed payload |
| Workspace snapshot form | gzip-compressed tar, same restore path |
| Request-identity replay across rounds | Narrowed to the open round; the never-used cross-round path is deleted |
| Activity order | Persist insertion order |

Research basis:
- `.work/run30/opt/design_checkpoint.json`, option 1, with the reviewer's revision in `review_checkpoint.json` (binding).
  The revision adds the precise keep rule, the deletion of the unreachable replay path and the test-id migration.
- The reviewer's "missing options" 2 and 3 in the same file: status reads ids only; zlib-compressed full rows.
- `design_resilience.json`, "Keep live activity order through a checkpoint", with the revision in
  `review_resilience.json`: two more raw readers, no boundary-4 offline proof, a per-step system test, and a
  same-version commit test.
- Checks made for this chapter: `.work/run30/scratch/spec-02-state-and-checkpoints/`; the reviewer's checks (gzip
  header, `is_tarfile` on gzip, tar errors inside a valid gzip, SQLite BLOB and TEXT typing) in
  `.work/run30/scratch/spec-review-02-state-and-checkpoints/checks.txt`.

Measured cost and expected effect ([O] observed in the run, [R] measured offline with main's code, [M] read from code):

| Measure | Today | After this chapter | Source |
| --- | --- | --- | --- |
| World state at the run's end | 10.49 MB canonical JSON; `processed_actions` 6.73 MB (3,273 entries), `activities` 3.25 MB | 3.64 MB at the final moment, where no processed action is kept because every activity is truncated; 3.77 MB at the reviewer's earlier moment, where 2 of 3,273 are kept | [R] `pruned_row.txt`; review |
| One checkpoint row at day 30 | 10.49 MB of text | 0.71 MB: zlib level 1 is 5.2× on the pruned state, 5.8× on today's state | [R] `pruned_row.txt`, `opt-checkpoint/zlib_state.txt` |
| Checkpoint table at 30/60/90 days | 30 / 118 / 263 GB | about 2.2 / 8 / 17 GB | proposal [R] |
| Largest workspace snapshot of the final segment | 1.79 MB tar | 0.34 MB gzip at level 6 (5.2×) | [R] `gzip_levels.txt` |
| Workspace snapshot assets at 30/60/90 days | 2.8 / 10.5 / 23 GB | about 0.5 / 2 / 4.4 GB | proposal [R] |
| `store.checkpoint` CPU at 10.49 MB | 0.793 s; 0.399 s with chapter 01's single encode | 0.243 s pruned (0.142 s with chapter 01) | [R] `opt-review-checkpoint/time_checkpoint_path.txt` |
| Added by compression per checkpoint | — | zlib-1 of the pruned state 0.03–0.09 s; gzip-6 of a 1.79 MB tar 0.10–0.18 s (about 25–50 s per simulated day at 184.6 checkpoints a day; the machine was shared, so these are upper bounds) | [R] proposal and `pruned_row.txt`, `gzip_levels.txt` |
| Added on restore | — | decompress the state 0.02 s; decompress the tar about 0.01–0.02 s | [R] same files |
| `worldmock status` | parses the 5 newest full checkpoint bodies to list their ids (about 10 MB and 0.15 s each at day 30) | reads 5 ids from the primary-key column | [M] `status.py:39-40`, review |
| Activity order after a restore | 96 of 3,008 observations had two activities whose order a restore would change; the Oct 20 resume showed the persona a swapped tool layer | Restored order equals live order | proposal [R][O] |
| Cross-round request replay | Never exercised: `reused_request_events` 0 in 30 days | Deleted | [O] quality.json of the run |

Net CPU at day 30: pruning removes about 62 s a day from the checkpoint path, net of chapter 01's single encode.
Compression adds about 25–50 s a day. The commit and deep-copy savings of the smaller state are counted once, in
chapter 01. Disk is the main gain: with chapter 03's event savings, the run database (events and checkpoints) falls from about
32 GB to about 3 GB at 30 days (proposal, "Projected effect").

## Behaviour

### Request identity and processed actions

**Action identity form.**
- Every action id that reaches `DecisionCoordinator.submit_action` must be exactly
  `f"{call.context.decision_id}:action:{n:04d}"`: the decision id, the literal `:action:` and four ASCII digits.
  This is the form LifeAgent already produces (`custom/agents/life_agent.py:598`) and the form
  `docs/design/06-agent.md` already documents.
- The model never writes an action id, so this is not a tool parameter. It is host protocol.
- AS2 forms decision ids as `f"{run_id}:round:{epoch}:{world_state_version}:agent:{agent_id}:order:{order}"`
  (`vendor/agentsociety2/packages/agentsociety2/agentsociety2/society/society.py:276-281`). A decision id therefore belongs to exactly one decision round, and so does
  an action id. An id from a settled round can never be submitted again.
- The check runs in `_validate_call` after the round check (`decision_coordinator.py:286-287`) and before the
  "already submitted" check (`:288-290`). So a call for another round still fails with
  `Direct tool call belongs to another decision round`, and a well-formed second call still fails with
  `Agent already submitted its decision round action`.

**Request identity inside the open round.** This is the only request identity left.
- A second `submit_action` with the same action id and the same tool and request digest, in the same open round,
  returns a receipt equal to the first one: same action id, round id, decision id and request digest. It writes no
  event and appends nothing to the round. This is the existing pending branch of `_existing_action`
  (`decision_coordinator.py:298`, `:301-315`).
- The same id with a different tool or digest is `ProtocolError("Action identity was reused with different
  parameters")`, as today (`:301-302`).

**What is deleted with the cross-round replay.**
- The stored branch of `_existing_action` (`:297`, `:299-300`, `:316`).
- `submit_action`'s replay route (`:100-101`) and the `reused` parameter of `_queue_action`.
- `PendingAction.reused` (`models.py:544`).
- The replay branch and the "Start group replay is inconsistent" check in `_resolve` (`:330-335`).
- `_reused_results` (`:580-595`) and `_replay_drafts` (`:625-635`).
- `_terminal_action_records` (`action_evidence.py:26-33`) and its three call lines: `time_advance.py:310`, `:394` and
  `activity_control.py:474`. A terminated activity's start result is no longer overwritten with the terminal result.
- The `if not pending.reused` guards at `decision_coordinator.py:170` and `:238`: the result is always remembered.
- The `"reused"` key of the `ACTION_ACCEPTED`/`ACTION_REJECTED` ops-log record (`:222`). The ops log is not an
  event, an export artifact or a model input.

**Events stay byte-identical.** `action_requested.data.reused` is still written, always as `false`
(`decision_coordinator.py:129`). Every non-replayed `action_requested` already carries exactly that today, so these
events are unchanged byte for byte. Dropping the key would be an event-format change, which the owner has not
approved. Only the replay route ever wrote `"reused": true`: into the replayed `action_requested`
(`_queue_action(call, request_sha, reused=True)`, `:100`, `:129`) and, through `_replay_drafts` (`:629`), into its
`action_accepted` or `action_rejected` and its `action_result`. That route never ran in the run
(`reused_request_events` 0).

**Readers that branch on `reused` are deleted.** They are dead once no event can carry `true` (AGENTS.md: delete
obsolete code). Each deletion leaves the reader's output byte-identical for every event stream the engine can now
produce:

| Site | Today | After |
| --- | --- | --- |
| `evaluation/errand_metrics.py:195` | `if event["event_type"] == "action_accepted" and not event["data"].get("reused"):` | `if event["event_type"] == "action_accepted":` |
| `evaluation/errand_metrics.py:241-242` | `return (… == "conclude_activity"` / `and not data.get("reused"))` | `return event["event_type"] == "action_requested" and data.get("tool_name") == "conclude_activity"` |
| `evaluation/goal_metrics.py:70-71` | `if data.get("reused"):` / `return None` | deleted |
| `footprints/purchases.py:108-109` | `… != "start_side_activity"` / `or data.get("reused")):` | `if event["event_type"] != "action_requested" or data.get("tool_name") != "start_side_activity":` |
| `footprints/quality.py:783-784` | `… if event.event_type == "action_rejected" and event.data.get("reused") is not True]` | `… if event.event_type == "action_rejected"]` |
| `life/day_plan_inputs.py:202` | `if event.data.get("status") == "completed" and not event.data.get("reused")` | `if event.data.get("status") == "completed"` |
| `life/day_plan_inputs.py:208` | `or event.data.get("reused")` | line deleted |
| `life/memory_layer.py:204` | `if event.data.get("tool_name") == tool and not event.data.get("reused")` | `if event.data.get("tool_name") == tool` |
| `life/memory_layer.py:256-257` | `if (request.data["tool_name"] in START_TOOLS and orders and result.data.get("status") == "completed"` / `and not request.data.get("reused")):` | `if request.data["tool_name"] in START_TOOLS and orders and result.data.get("status") == "completed":` |
| `life/progress_layer.py:322` | `if event.event_type == "action_accepted" and not event.data.get("reused"):` | `if event.event_type == "action_accepted":` |
| `life/progress_layer.py:325` | `if event.event_type not in {"model_response", "action_rejected"} or event.data.get("reused"):` | `if event.event_type not in {"model_response", "action_rejected"}:` |
| `life/settled_text.py:48-49` | `if any(event.data.get("reused") for event in sources):` / `return []` | deleted |

`footprints/quality.py:170` keeps `"reused_request_events"`. It reports the recorded field of `action_requested`, which
still exists; it is always 0 now. Keeping it leaves quality.json's key set and the export certification
(`validation.py:68-81` recomputes the report) unchanged.

These readers also run on exports and on events copied into a branch. Neither can carry `"reused": true` once this
chapter lands: a branch or resume of an older run is refused by its world structure 14 (below), the export check
refuses an embedded world of another structure (`footprints/validation.py:92`), and evaluation refuses an export
manifest of another version (`evaluation/validation.py:257-258`; chapter 09 moves it to 8).

Docstrings and comments that describe request replay are rewritten without it (Chinese, meaning only). They are listed
under "Interfaces and files". "重放" meaning the counterfactual `replay` command is a different concept and stays.

**What `processed_actions` keeps.** At each clock advance, right after
`staged = same_moment.reset_same_moment(staged)` (`time_advance.py:105`), call
`staged = prune_processed_actions(staged)`. It keeps an entry only if its key is the `origin_action_id` of an
activity whose status is `active` or `paused` in that staged world. At that point `current_time` already equals the
target, and no activity of the advance has been settled yet.

Consequences, which the invariant tests assert after every committed step. "Origins at the last advance" means the
`origin_action_id`s of the activities that were `active` or `paused` in the engine world just before the last
`advance` call (empty before the first advance):
- Every entry is either a result settled in the current second (its stored `result.occurred_at` equals
  `current_time`) or the start action of an activity among the origins at the last advance.
- Every origin at the last advance is present, and every activity that is `active` or `paused` now has its start
  action present.
- An activity that terminates during an advance, or in a decision round after it, keeps its start action until the
  next advance.
- After the final advance (`run_ended`), only the start actions of the origins at that advance remain: the
  activities truncated at the end and those that finished in that same advance. No decision round follows it.

The readers of `processed_actions` after this chapter (search: `rg -n processed_actions src custom scripts tools` and
the dataset repository):

| Reader | What it reads | Why the kept set suffices |
| --- | --- | --- |
| `same_moment._second_rejections` (`same_moment.py:222-234`), used by `rejection_counts`, `rejection_pulse`, `latest_rejection_code`, `refusal_pulse_applied` | Results with `occurred_at == current_time` and status `rejected` | Every current-second result was added after the last advance; pruning never removes one before the clock moves |
| `require_origin_action` at the three termination sites | Presence of the terminating activity's `origin_action_id` | The activity was active or paused at the last advance, or started in this second |

No export, quality, evaluation, status, viewer, inspect or dataset code reads the field; the reviewer confirmed
this by grep [M]. The full record of every action stays in its `action_requested` and `action_result` events: 3,273 of
3,273 stored results are contained in their event [R] (`opt-checkpoint/prune_check.txt`).

**Prompts and settled facts are byte-identical.**
- `_second_rejections` gets the same entries, in the same relative order (deleting dict keys keeps order).
- The termination checks still pass.
- No other code reads the ledger.
- Terminal overwrites replaced the whole stored `result` of the start action (status, message, `occurred_at`, the
  activity snapshot and orders), and nothing reads it except `_second_rejections`, which ignores it, since a terminal
  status is never `rejected` (`action_evidence.py:49-52`). The terminal `action_result` event still carries that
  result in full (`_activity_terminal_draft`, unchanged), and `advance` still returns it to its caller.

**Failure handling.**

| Condition | Log | Exception |
| --- | --- | --- |
| Malformed action id at `submit_action` | `logging.error("Action identity %s is not formed from decision %s", action_id, decision_id)` | `ProtocolError("Action identity must have the form <decision_id>:action:<four digits>")`; no event, nothing pending |
| Same id, different tool or digest, same round | `logging.error("Action identity %s was reused with different parameters", action_id)` | `ProtocolError("Action identity was reused with different parameters")` (text unchanged) |
| A terminating activity's start action is absent | `logging.error("Activity %s origin action %s is missing from processed actions", activity_id, origin_action_id)` | `ProtocolError("Activity origin action is missing from processed actions")` (text unchanged; today raised at `time_advance.py:306-307` and inside `_terminal_action_records`) |

### Activity order through checkpoints

**Stored form.** `WorldState.activities` stays a `dict[str, ActivityState]` in memory. Its JSON form becomes an
array of activity objects in dict insertion order:
- A JSON-mode `field_serializer` returns `list(value.values())`.
- A before-validator turns an array back into a dict in array order.
- `canonical_json` sorts object keys only (`footprints/models.py:192`), so the array order survives every store
  write, checkpoint, export embed and restore.

**Validation.**
- The field accepts only an array. Each item is a JSON object, or an `ActivityState` for Python-side construction,
  whose `activity_id` is a string.
- A mapping, a non-object item or an item without a string `activity_id` is refused: `_LOGGER.error("World activities
  are not an array of activity objects")`, then `ValueError("world activities must be an array of activity objects")`.
- A repeated `activity_id` is refused: `_LOGGER.error("World activities repeat the activity identity %s", identity)`,
  then `ValueError("world activities repeat an activity identity")`.
- Pydantic wraps both in `ValidationError`. On the write path `validated_payload` wraps that again as
  `IntegrityError("World state invariant violated before persisting: …")` (`models.py:1348-1352`). On load,
  `EngineContext.__post_init__` and `runtime.py:245` raise the `ValidationError`.
- A world of structure 14 is refused: its `schema_version` fails the literal, and its mapping-form `activities` fails
  the validator above, in the same `ValidationError`. There is no migration.
- `worldmock status` reads the stored world raw and does not validate it. On a run directory written before this
  chapter it lists the checkpoint ids and then fails with a `TypeError` while iterating the mapping-form activities;
  like resume, such a run needs the code it was recorded with.

**Version.** `WORLD_SCHEMA_VERSION` goes from 14 to 15 (`models.py:29`): the accepted form of a field changed
(`docs/design/09-io-formats.md:9`). Chapter 09 lists this version change. `RUN_MANIFEST_SCHEMA_VERSION`, the export
manifest version and `RULES_VERSION` are not changed by this chapter.

**Uninterrupted runs are byte-identical.**
- The engine reads the store's world only in `EngineContext.__post_init__` (`engine_context.py:36`). Every later
  assignment takes an in-memory staged world, so live dict order is unchanged.
- The seven dict-order readers keep iterating the same order: `tool_layer.py:119`, `observe.py:133`,
  `admission.py:108` and `:172`, `memory_evidence.py:39`, `rhythm.py:118`, `time_advance.py:376`.
- Every rebuild of the dict keeps positions: `interact.py:226`, `storefront_runtime.py:47` and `:69`, `sessions.py:117`,
  `session_actions.py:109-114` and `:262-267`, `world_ops.py:24`, and assignments to existing keys.
- Chapter 01's `WorldState.__deepcopy__` keeps the dict type, so its hook needs no change.

**Resumed and replayed runs.** These are the deliberate exception listed in the specification's owner's rules. They
now see the order an uninterrupted run shows. A start group starts the side activity before the main one
(`start_group.py:203`); a restore used to put them in sorted-id order instead.

**Raw readers of the stored form change to arrays.**

| Reader | Today | After |
| --- | --- | --- |
| `status.py:41` | `for activity in state["activities"].values()` | `for activity in state["activities"]` |
| `status.py:106-107` | `(item for item in state["activities"].values() if …)` | `(item for item in state["activities"] if …)` |
| `footprints/invariants.py:313` (export certification) | `for activity in state.get("activities", {}).values()` | `for activity in state.get("activities", [])` |
| `tools/run-viewer.html:1179` (session table) | `S.world?.activities\|\|{}` indexed by id | chapter 07 owns this edit (`07-viewer.md`, its `renderSessions` row): `new Map(Object.values(S.world?.activities\|\|{}).map(activity=>[activity.activity_id, activity]))`; this chapter requires it and does not edit the file |

`status.py`'s `active_activities` list now follows insertion order instead of sorted-id order. `_people_status`
sorts its own list (`:108`).

The tracked files of the dataset repository read neither field (search for `world_state`, `FROM checkpoints`,
`processed_actions` and `["activities"]` in its `tools/` and `tests/`).

Other mappings also lose insertion order on a restore: `sessions`, `deliveries`, `callback_states`, app pushes and
`content_progress`. Their prompt-side readers iterate them sorted or order-insensitively (`memory_layer.py:178`,
`activity_control.py:188`, `sessions.py:30`, `:153`, `admission.py:209`), and boundaries 1–3 of the run were
byte-identical [R]. This chapter does not change them; the per-step restore test checks the whole observation.

### Compressed checkpoint rows

**Schema.** `CREATE TABLE checkpoints(checkpoint_id TEXT PRIMARY KEY, body BLOB NOT NULL, record_sha256 TEXT NOT
NULL);` (`store.py:28`). The append-only triggers `store.py:34-37` are unchanged. Old databases are not migrated:
reading a pre-change row fails as a damaged record (below). Such runs cannot be resumed by this code anyway, because
their world has structure 14.

**Write.** On top of chapter 01's `checkpoint` (this chapter lands after it), which encodes the payload once into
`encoded` and sets `digest = hashlib.sha256(encoded).hexdigest()` (equal to `sha256_json(payload)`,
`footprints/models.py:198-200`), the `INSERT` stores `zlib.compress(encoded, _CHECKPOINT_ZLIB_LEVEL)` as `body` and
`digest` as `record_sha256`. The returned `Checkpoint` and the `checkpoint_created` event are unchanged.

**Read.** `get_checkpoint` first calls `_decompressed_record(checkpoint_id, row)`:
- The row must exist.
- `body` must be `bytes` and must decompress with `zlib.decompress`, which checks Adler-32.
- `sha256(decompressed) == record_sha256`.

It then builds `Checkpoint(**json.loads(payload), record_sha256=…)` and runs today's identity, manifest, participant,
event and asset checks unchanged (`store.py:341-359`). The `Checkpoint` it returns is equal, field for field, to the
one `checkpoint` returned.

**Byte-identical.**
- The `Checkpoint` record and its `record_sha256`.
- `restored-checkpoint.json` (`checkpoint.py:141`).
- The export manifest's embedded final checkpoint (`export.py:88-89`) and its certification (`validation.py:128-150`
  recomputes `sha256_json` over the payload).
- The `checkpoint_created` event.

Only the bytes inside the SQLite column change, and they decompress to exactly today's text.

**Failure handling.** Every case raises `IntegrityError("Checkpoint is missing or its record is damaged")`, the text
today's single check uses (`store.py:338-339`), after its own log line:

| Condition | Log |
| --- | --- |
| No row | `logging.error("Checkpoint %s does not exist", checkpoint_id)` |
| `body` is not `bytes` (for example a TEXT body written by earlier code) | `logging.error("Checkpoint %s body is not a compressed record", checkpoint_id)` |
| `zlib.error` while decompressing | `logging.error("Checkpoint %s body does not decompress: %s", checkpoint_id, error)` |
| Digest mismatch | `logging.error("Checkpoint %s record hash does not match its payload", checkpoint_id)` |

### Compressed workspace snapshots

**Snapshot.** `snapshot_directory` builds the same tar in memory as today (`checkpoint.py:29-39`):
- regular files only, sorted;
- member name is the relative POSIX path;
- size, mode `0o600` and mtime 0;
- the same `_check_members` call before writing.

It then compresses those bytes in one `write` through
`gzip.GzipFile(fileobj=io.BytesIO(), mode="wb", compresslevel=_WORKSPACE_GZIP_LEVEL, mtime=0)`, and publishes them with
`{"media_type": "application/gzip", "source": {"kind": "workspace"}}`.

`GzipFile` over a nameless `BytesIO` writes this fixed 10-byte header (verified, `gzip_levels.txt`):
- magic `1f 8b`, method `08`, flags `00` (no file name);
- mtime `00 00 00 00`;
- XFL `00` for level 6;
- OS `ff`.

`gzip.compress(…, mtime=0)` is not used, because it writes zlib's platform OS byte (`0a` on Windows, verified).

The same workspace therefore yields the same asset bytes at any time. Unchanged workspaces still deduplicate in the
`AssetStore` by sha256, as today. The deflate stream is produced by the interpreter's zlib (1.3.2 here). Different zlib
builds could compress differently; the day baseline normalizes the tar contents (below), so its signature does not
depend on that.

**Restore.** `restore_directory` keeps its order:
1. The destination must not exist.
2. `assets.read` checks the sha256 and size.
3. The whole asset is decompressed with `gzip.decompress`, which checks CRC-32 and length.
4. The bytes are opened as a plain tar (`mode="r:"`).
5. Every member passes `_check_members` before anything is written.
6. Each member is written with `atomic_write`, refusing aliases.

A plain tar, a truncated stream, a bad CRC or a corrupt deflate stream raises `gzip.BadGzipFile`, `EOFError` or
`zlib.error` (verified, `gzip_decompress_errors.txt`). A valid gzip of a bad tar (a truncated tar, or 1 KiB of `\x01`)
raises `tarfile.ReadError`, a `tarfile.TarError` (verified, reviewer's `checks.txt`). All of them become
`IntegrityError("Workspace snapshot archive is invalid")` after
`logging.error("Workspace snapshot %s is not a valid gzip-compressed tar: %s", reference.sha256, exc)`. The member-name
safety rules are unchanged: they run at snapshot and at restore. `IntegrityError` from `_check_members` and the alias
check are not caught. `gzip.decompress(b"")` returns `b""`; an empty asset therefore fails at `tarfile.open` with
`ReadError` and is refused the same way.

**Branch, export and validation: no code change.**
- `_populate_branch` copies each snapshot with its own `media_type` and `source` (`checkpoint.py:128-132`).
- `export.py:90-92` copies the asset bytes.
- `validation._validate_assets` (`validation.py:182-205`) and `evaluation/validation.py:355-385` check only the
  presence, size and sha256 of the asset.
- `runtime.py:378` restores through `restore_directory`.

Today's restore through `_restore_agents` and `_validate_agent_workspace` (`runtime.py:366-411`) is unchanged. Restored
files are byte-identical; the tar inside the gzip is the tar today's code writes.

### Status reads checkpoint ids only

`read_status` lists `recent_checkpoints` as
`[row[0] for row in connection.execute("SELECT checkpoint_id FROM checkpoints ORDER BY rowid DESC LIMIT 5")]`
(`status.py:39-40`). The values and their order are unchanged, because the column holds the payload's `checkpoint_id`
(`store.py:307-308`). No body is read or parsed. With compressed bodies the old `json.loads(row[0])` would fail, so
this change is required.

### What does not change

- Cadence: one common checkpoint after every AS2 step (`society.py:224-240`, `_checkpoint` `:406-421`).
- Retention: rows are immutable and never deleted (`store.py:34-37`). Every step keeps its own restorable checkpoint,
  so a resume still continues from the failure second.
- A workspace snapshot at every checkpoint (`life_env.py:214-230`), the life receipt asset (`engine.py:179-182`) and
  the plan_day hook (`life_env.py:211`).
- The `Checkpoint` model (`footprints/models.py:174-186`, `schema_version` 1), `restore_branch`, the export embed, the
  export manifest version and the certification cross-checks.

### Order of work inside this chapter

This chapter's commits land after chapter 01's, so chapter 01's baseline proof runs on unchanged fixtures. They land
in this order, one commit each on the integration branch, each with its own tests and its own registered mutations:

| Commit | Content |
| --- | --- |
| Design commit | The design chapters (below) |
| Test-identity commit | Tests only: the action-identity migration and the split of `record_complete_day` into `create_complete_day_engine` and `play_complete_day` (see "Tests") |
| Identity-check commit | The action-id form check, the deletion of the replay path and of the `reused` readers |
| Pruning commit | `prune_processed_actions` and `require_origin_action` |
| Activity-order commit | Ordered activities, `WORLD_SCHEMA_VERSION = 15` and the raw readers |
| Checkpoint-row commit | Compressed checkpoint rows and status ids, on chapter 01's `checkpoint` |
| Snapshot commit | Compressed workspace snapshots |

**The test-identity commit and the comparisons.**
- It is valid on main's engine, which accepts any id. It changes action-keyed draws in the scripted tests, so their
  expected values are re-derived in the same commit.
- Chapter 08's masked complete-day comparison records each side with its own test helpers. Main's helpers submit
  `action-<label>` ids and the migrated helpers submit `decision-<label>-1:action:NNNN` ids, which key different random
  draws. Main against any tree at or after the test-identity commit therefore reports `different` although no
  production code changed. The test-identity commit is therefore gated by the full suite only (it changes no
  production file), and the masked comparison of every later commit of this chapter (and of later chapters) uses its
  parent commit as base, which is the test-identity commit or later.
- Chapter 08's CI job takes the pull request's base as base. For the integration pull request into main it would
  report `different` for this reason alone. This is open for the integrator (see "Not in this chapter").

The complete-day baselines change at the test-identity commit (other action-keyed draws) and at the pruning,
activity-order, checkpoint-row and snapshot commits (the stored form). They are re-frozen once at the end (chapter 09).
Until then `test_complete_day_baseline.py` fails on the integration branch, and the masked complete-day comparison
against the test-identity commit gates each commit.

## Interfaces and files

All lines below are on `0bd2de65`. Chinese `@brief` texts give the gist to write; code, logs and errors are English.

### `src/worldmock/life/decision_coordinator.py`

- `:37`: remove `StoredAction` from the models import.
- `submit_action` (`:91-102`):

  ```python
      def submit_action(self, call: "DirectToolCall") -> ActionReceipt:
          """@brief 严格登记宿主绑定的有类型工具调用，尚不改变生活状态；同一开放轮内重复登记同一动作返回第一次的回执。"""
          self._validate_call(call)
          request_sha = _request_sha(call.tool_name, call.request)
          registered = self._registered_action(call.action_id, call.tool_name, request_sha)
          if registered is not None:
              return ActionReceipt(action_id=registered.action_id, round_id=registered.round_id,
                                   decision_id=registered.decision_id, request_sha256=request_sha)
          return self._queue_action(call, request_sha)
  ```

- `_queue_action(self, call, request_sha)` (`:104-151`): the `reused` parameter is removed. `:129` becomes
  `"reused": False,` with the comment `# 没有跨轮重放：reused 恒为 false，只为事件格式不变而写`. `:148`
  (`reused=reused,`) is deleted. The `@brief` drops "重放动作仍不再次执行". Chapter 01 owns the commit lines `:133-134`.
- `commit_decision_round` `:170-171` → `staged = _remember_action(staged, pending, result)`, unconditionally.
- `_ops_action` `:222`: the dict ends with `"error_code": result.error_code})`; the `"reused"` key is gone.
- `abort_decision_round` `:238-239` → `staged = _remember_action(staged, pending, result)`, unconditionally. Its
  `@brief` (`:225`) becomes 「保存失败结果，不执行本轮生活动作。」 (the stored failure is kept until the next advance;
  nothing reads it as a request identity any more).
- `_validate_call` (`:276-293`): after `:286-287`, insert

  ```python
          if not _formed_action_identity(call.action_id, call.context.decision_id):
              logging.error("Action identity %s is not formed from decision %s",
                            call.action_id, call.context.decision_id)
              raise ProtocolError("Action identity must have the form <decision_id>:action:<four digits>")
  ```

  The `@brief` becomes 「拒绝任意字典请求、错误工具类型、非当前决策轮和不由本决策身份形成的动作身份。」.
- `_existing_action` (`:295-316`) is replaced by:

  ```python
      def _registered_action(self, action_id: str, tool_name: str, request_sha: str) -> PendingAction | None:
          """@brief 本开放轮已登记的同一动作（请求身份只在开放轮内复用）；身份相同而参数不同是协议故障。"""
          pending = next((item for item in self.round.pending_actions if item.action_id == action_id), None)
          if pending is not None and (pending.tool_name != tool_name or pending.request_sha256 != request_sha):
              logging.error("Action identity %s was reused with different parameters", action_id)
              raise ProtocolError("Action identity was reused with different parameters")
          return pending
  ```

- `_resolve` (`:324-340`): the docstring becomes the one-line `@brief` of `:326` (delete `:327-328`, the blank line
  and the replay sentence), and `:330-335` are deleted. The body is the `try` block.
- `_accept_results` `:348`: `records = activity_control.record_changes(state, changed, first, self.actions.record_context)`,
  because `record_changes` now returns only drafts.
- `_round_schedule` docstring `:513`: `:param rejection: 本轮业务拒绝的错误码，受理时为 None。`
- Delete `_reused_results` (`:580-595`) and `_replay_drafts` (`:625-635`).
- `_decision_self_note` docstring `:600`: drop 「，恢复后重放登记同样得到一致结果」.
- New module function after `_start_pair` (`:619-622`):

  ```python
  def _formed_action_identity(action_id: str, decision_id: str) -> bool:
      """@brief 动作身份恰为 {decision_id}:action:{四位序号}（docs/design/06-agent.md「宿主模型协议」）。"""
      number = action_id.removeprefix(f"{decision_id}:action:")
      return number != action_id and len(number) == 4 and number.isascii() and number.isdigit()
  ```

### `src/worldmock/life/models.py`

- `:29`: `WORLD_SCHEMA_VERSION = 15` (comment unchanged).
- `StoredAction` (`:519-526`): fields unchanged. `@brief` becomes 「一条已结算动作的回执与即时结果：本秒的拒绝计数读它，活动终结时
  核对开始动作仍在（docs/design/04-simulation-engine.md「原子性与恢复」）。」. `tool_name` and `request_sha256` are no
  longer read by any rule, but they stay, so a kept entry is byte-identical to today's entry for the same action.
- `PendingAction` (`:529-544`): delete `reused: bool = False`.
- `WorldState`:
  - Comment above `activities` (`:580`): `#: 活动按加入世界的先后保存；JSON 里是按这个先后排列的数组
    （docs/design/09-io-formats.md「世界、人物与活动」）。`
  - Comment above `processed_actions` (`:584`): `#: 规则仍会读取的已结算动作：本秒的结果与进行中或暂停活动的开始动作
    （docs/design/04-simulation-engine.md「原子性与恢复」）。`
  - New methods between `random_state` (`:594`) and `validate_world` (`:597`):

    ```python
        @field_serializer("activities", when_used="json")
        def serialize_activities(self, value: dict[str, ActivityState]) -> list[ActivityState]:
            """@brief 活动按加入世界的先后写成数组：规范 JSON 只给对象的键排序，恢复后的先后与不中断的运行相同。"""
            return list(value.values())

        @field_validator("activities", mode="before")
        @classmethod
        def activities_from_array(cls, value: Any) -> dict[str, Any]:
            """@brief 数组按先后还原为以 activity_id 为键的映射；不是活动对象数组或身份重复即拒绝，不接受映射写法。"""
            if not isinstance(value, list):
                _LOGGER.error("World activities are not an array of activity objects")
                raise ValueError("world activities must be an array of activity objects")
            mapping: dict[str, Any] = {}
            for item in value:
                identity = item.activity_id if isinstance(item, ActivityState) else (
                    item.get("activity_id") if isinstance(item, dict) else None)
                if not isinstance(identity, str):
                    _LOGGER.error("World activities are not an array of activity objects")
                    raise ValueError("world activities must be an array of activity objects")
                if identity in mapping:
                    _LOGGER.error("World activities repeat the activity identity %s", identity)
                    raise ValueError("world activities repeat an activity identity")
                mapping[identity] = item
            return mapping
    ```

  - The default stays `Field(default_factory=dict)`. Pydantic does not validate defaults, so `world_setup.py:32`
    keeps constructing without the field. Prototype check: `ordered_activities.txt` (dump order, both validate paths,
    canonical round trip, the three refusals).
- `validated_payload` (`:1340-1353`) is unchanged: it now produces the array form and re-validates it.

### `src/worldmock/life/action_evidence.py`

- Imports: add `import logging`. Add `ActivityState` to the `worldmock.life.models` import.
- `_remember_action` (`:14-23`): code unchanged. `@brief` becomes 「保存这条动作的回执与即时结果，供本秒的拒绝计数与活动终结时的
  开始动作核对。」.
- Delete `_terminal_action_records` (`:26-33`).
- Add after `_remember_action`:

  ```python
  def prune_processed_actions(state: WorldState) -> WorldState:
      """@brief 时钟推进时只留下规则仍会读取的动作：进行中或暂停活动的开始动作；其余的完整记录在动作事件里
      （docs/design/04-simulation-engine.md「原子性与恢复」）。

      @param state 已移到新时刻、尚未结算这次推进的世界副本
      @return processed_actions 只含这些开始动作的世界副本，其余字段原样
      """
      origins = {activity.origin_action_id for activity in state.activities.values()
                 if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}}
      kept = {key: stored for key, stored in state.processed_actions.items() if key in origins}
      return state.model_copy(update={"processed_actions": kept})


  def require_origin_action(state: WorldState, activity: ActivityState) -> None:
      """@brief 活动终结时开始它的那条动作必须仍在 processed_actions 里。

      @exception ProtocolError 开始动作缺失（Activity origin action is missing from processed actions）
      """
      if activity.origin_action_id not in state.processed_actions:
          logging.error("Activity %s origin action %s is missing from processed actions",
                        activity.activity_id, activity.origin_action_id)
          raise ProtocolError("Activity origin action is missing from processed actions")
  ```

  `model_copy(update=…)` is shallow on purpose. The caller's `staged` is already a fresh copy (`time_advance.py:104-105`),
  and the kept entries are shared settled entries, which are never edited in place (chapter 01).

### `src/worldmock/life/time_advance.py`

- `:26-27` import: `from worldmock.life.action_evidence import (_activity_terminal_draft, _activity_terminal_result,
  prune_processed_actions, require_origin_action)`.
- After `:105`: `staged = prune_processed_actions(staged)`.
- `_terminal_for_progress` (`:297-314`): `:306-307` becomes `require_origin_action(state, activity)`. `:308-314`
  becomes

  ```python
          index = title_index(self.context.catalog)
          return (
              _activity_terminal_result(state, activity, target, index),
              _activity_terminal_draft(state, activity, target, [], index),
          )
  ```

  The `@brief` is unchanged (it still writes the start action's terminal result as an event).
- `_truncate` (`:373-396`): `:393-394` are replaced by `require_origin_action(state, activity)`. `:392` and `:395` stay.
  The `@brief` becomes 「在实验终点截断仍进行或暂停的活动并保留实际进度：逐项写截断快照，并给开始它的动作写一次终态事件。」.
- Chapter 01 owns `:54`, `:145-147`, `:216-218`, `:326`.

### `src/worldmock/life/activity_control.py`

- `:18-19` import: `from worldmock.life.action_evidence import _activity_terminal_draft, require_origin_action`.
- `record_changes` (`:422-429`) returns `list[EventDraft]`:

  ```python
  def record_changes(before: WorldState, after: WorldState, pending: PendingAction,
                     context: RecordContext) -> list[EventDraft]:
      """@brief 本次控制改变的每项活动写一条 environment_event；活动终结时给开始它的动作写一次终态；控制请求里每条被受理
      的个人事务变更各写一条事务事件（errands.request_drafts，只在这条成功分支生成，docs/design/04-simulation-engine.md
      「唤醒、日计划与个人事务」）。"""
      drafts = _lifecycle_drafts(before, after, pending, context.snapshot)
      terminal = _terminal_records(before, after, pending.requested_event_id, context.index)
      return drafts + terminal + errands.request_drafts(before, after, pending)
  ```

- `_terminal_records` (`:466-477`) returns `list[EventDraft]`. `@brief`: 「本次新终结的活动各写一次终态；开始它的那条动作必须仍在
  processed_actions 里。」. Body: the `newly` list unchanged; then, for each activity, `require_origin_action(after, activity)`
  followed by the unchanged `drafts.append(_activity_terminal_draft(...))`; `return drafts`.

### `src/worldmock/life/same_moment.py`

Docstrings only:
- `:51`: `:param rejected: 本轮是否为业务拒绝`;
- `:64`: 「受理与业务拒绝各使本秒的同刻轮数加一……」;
- `:223`: 「（依据已存的动作结果，按结算先后）」.

### Readers of `reused`

The code changes are in the table under "Behaviour". Docstrings:
- `errand_metrics.py:9`, `:72`, `:107`, `:192`, `:239`: drop 「非重放的」/「重放的不算」.
- `purchases.py:87`: 「统一购买请求保留首个结果状态，非法结构不重复计尝试。」.
- `purchases.py:106`: 「只把当前购买请求归为尝试，不解析已退役方式。」.
- `quality.py:772`: drop 「（不含重放）」.
- `day_plan_inputs.py:152`: 「失败的请求与其余工具不写」.
- `day_plan_inputs.py:196`: 「被拒的请求不列」.
- `memory_layer.py:202`: 「（即时结果 completed）」.
- `settled_text.py:44`: drop 「；重放的请求不写」.

### `src/worldmock/footprints/store.py`

- Imports: add `import zlib`.
- `SCHEMA` `:28`: `CREATE TABLE checkpoints(checkpoint_id TEXT PRIMARY KEY, body BLOB NOT NULL, record_sha256 TEXT NOT
  NULL);`. Chapter 03 adds the `parts` table in the same string; the edits do not overlap.
- New constant below `SCHEMA`:
  `#: 检查点行正文的 zlib 压缩级别（docs/design/05-recording-engine.md「冻结输入、检查点与分支」）。` then
  `_CHECKPOINT_ZLIB_LEVEL = 1`.
- `checkpoint`: the `INSERT` row becomes `(checkpoint.checkpoint_id, zlib.compress(encoded, _CHECKPOINT_ZLIB_LEVEL),
  digest)`. In chapter 01's docstring the second paragraph 「载荷只按规范 JSON 编码一次：存储正文就是这份字节，record_sha256
  是它的 SHA-256（与 sha256_json(payload) 相同）。」 becomes 「载荷只按规范 JSON 编码一次：行正文是这份字节的 zlib 流（级别 1），
  record_sha256 是未压缩字节的 SHA-256（与 sha256_json(payload) 相同）。」. Everything else in `checkpoint` is chapter 01's.
- `get_checkpoint` (`:334-360`): `:338-340` become
  `payload = _decompressed_record(checkpoint_id, row)` and
  `checkpoint = Checkpoint(**json.loads(payload), record_sha256=row[1])`. The `@brief` becomes 「解压并按未压缩载荷核对
  记录摘要，再校验运行身份、事件与每个素材，不读取松散 JSON 收据。」. Chapter 03 owns the event decode at `:352`.
- New module function after the class:

  ```python
  def _decompressed_record(checkpoint_id: str, row: tuple | None) -> bytes:
      """@brief 解压检查点行正文并核对 record_sha256 是未压缩规范载荷的摘要。

      @param checkpoint_id 检查点身份，只用于错误日志
      @param row 查询得到的 (body, record_sha256)；不存在时为 None
      @return 未压缩的规范载荷字节
      @exception IntegrityError 行不存在、正文不是 zlib 流或摘要不符（Checkpoint is missing or its record is damaged）
      """
      if row is None:
          logging.error("Checkpoint %s does not exist", checkpoint_id)
          raise IntegrityError("Checkpoint is missing or its record is damaged")
      body, digest = row
      if not isinstance(body, bytes):
          logging.error("Checkpoint %s body is not a compressed record", checkpoint_id)
          raise IntegrityError("Checkpoint is missing or its record is damaged")
      try:
          payload = zlib.decompress(body)
      except zlib.error as error:
          logging.error("Checkpoint %s body does not decompress: %s", checkpoint_id, error)
          raise IntegrityError("Checkpoint is missing or its record is damaged") from error
      if hashlib.sha256(payload).hexdigest() != digest:
          logging.error("Checkpoint %s record hash does not match its payload", checkpoint_id)
          raise IntegrityError("Checkpoint is missing or its record is damaged")
      return payload
  ```

### `src/worldmock/footprints/checkpoint.py`

- Imports: add `import gzip` and `import zlib`.
- New constant after the imports: `#: 工作区快照的 gzip 压缩级别（docs/design/05-recording-engine.md「冻结输入、检查点与分支」）。`
  then `_WORKSPACE_GZIP_LEVEL = 6`.
- `snapshot_directory` (`:23-40`): `:24-25` `@brief` adds 「归档以固定头部 gzip 压缩（修改时刻 0），同一工作区在任何时刻得到同一
  素材。」. Lines `:26-39` are unchanged. `:40` becomes

  ```python
      compressed = io.BytesIO()
      with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=_WORKSPACE_GZIP_LEVEL, mtime=0) as stream:
          stream.write(content.getvalue())
      return assets.put(compressed.getvalue(), {"media_type": "application/gzip", "source": {"kind": "workspace"}})
  ```

- `restore_directory` (`:54-73`): `@brief` 「完整解压 gzip 并核对全部成员后才展开普通相对文件，不覆盖已有目标目录，也不让两个
  成员写到同一个文件。」. Body:

  ```python
      destination = Path(destination)
      if destination.exists():
          logging.error("Participant restore destination already exists: %s", destination)
          raise IntegrityError("Participant restore destination already exists")
      content = assets.read(reference)
      try:
          archive_bytes = gzip.decompress(content)
          with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
              members = archive.getmembers()
              _check_members(members)
              destination.mkdir(parents=True)
              for member in members:
                  source = archive.extractfile(member)
                  target = destination / member.name
                  # 目标目录是新建的：写入前已存在只能是先前成员的别名（Windows 8.3 短名、大小写或 Unicode 规范化）。
                  if os.path.lexists(target):
                      logging.error("Workspace snapshot member %s aliases an earlier member", member.name)
                      raise IntegrityError("Workspace snapshot contains an unsafe member")
                  atomic_write(target, source.read())
      except (gzip.BadGzipFile, EOFError, zlib.error, tarfile.TarError) as exc:
          logging.error("Workspace snapshot %s is not a valid gzip-compressed tar: %s", reference.sha256, exc)
          raise IntegrityError("Workspace snapshot archive is invalid") from exc
  ```

  The loop body is today's (`:62-71`); the two `logging.error` lines are added because AGENTS.md requires an error
  log on every exception branch of a function this chapter rewrites. `atomic_write` raises `OSError` subclasses other
  than `BadGzipFile`; they are not caught, as today.
- `_check_members`, `restore_branch`, `_populate_branch` and `_branch_asset_references` are unchanged by this chapter.

### `src/worldmock/status.py`

- `:39-40`: `points = [row[0] for row in connection.execute(` /
  `"SELECT checkpoint_id FROM checkpoints ORDER BY rowid DESC LIMIT 5")]`.
- `:41` and `:106-107`: array iteration, as in the table under "Behaviour".
- Chapter 05 adds `error_message` and the heartbeat in this file; the edits do not overlap.

### `src/worldmock/footprints/invariants.py`

`:313`: `return {identity: activity["activity_id"] for activity in state.get("activities", [])`; `:314` is
unchanged. The `@brief` is unchanged.

### `tools/run-viewer.html`

No edit here. Chapter 07's `renderSessions` edit (`:1177-1185`) reads both the object and the array form; this chapter
depends on it.

### Recorded formats, byte by byte

**World state** (`world_state.body`, checkpoint `state`, export `manifest.checkpoint.state`). Canonical JSON as today,
except:
- `"schema_version":15`;
- `"activities":[<activity>,<activity>,…]`: an array in insertion order, each `<activity>` today's `ActivityState`
  JSON object with sorted keys;
- `"processed_actions":{…}`: an object keyed by action id, keys sorted, holding only the kept entries. Each entry is
  today's `StoredAction` JSON, `{"action_id","receipt":{…},"request_sha256","result":{…},"tool_name"}`, holding the
  action's own settlement result (never a terminal overwrite).

Example of two running activities started by one start group:
`"activities":[{"activity_id":"activity-r:round:1:7:agent:1:order:0:action:0002",…},{"activity_id":"activity-r:round:1:7:agent:1:order:0:action:0001",…}]`.
The side activity is first.

**Checkpoint row.** `checkpoints(checkpoint_id TEXT, body BLOB, record_sha256 TEXT)`:
- `body` is an RFC 1950 zlib stream: bytes 0–1 `78 01` (level 1), a deflate stream, and the Adler-32 of the
  uncompressed bytes, big-endian.
- The uncompressed bytes are UTF-8 `canonical_json(payload)`, exactly today's TEXT body. `payload` has the keys
  `checkpoint_id`, `current_time`, `last_event_seq`, `manifest_sha256`, `run_id`, `schema_version` (1), `snapshots`,
  `state` and `state_version`, in sorted order.
- `record_sha256` is the lowercase hex SHA-256 of the uncompressed bytes.

**Workspace snapshot asset.** An RFC 1952 gzip member:
- header `1f 8b 08 00 00 00 00 00 00 ff`;
- a deflate stream at level 6;
- a trailer of CRC-32 and ISIZE, both little-endian.

The uncompressed bytes are today's tar:
- `tarfile` default (PAX) format;
- one REGTYPE member per regular file in `sorted(_workspace_files(root))` order;
- name is the relative POSIX path; mode `0o600`, mtime 0, uid and gid 0, empty user and group names;
- end-of-archive blocks padded to 10,240 bytes.

The `AssetRef` is `{"sha256": sha256(gzip bytes), "size_bytes": len(gzip bytes), "media_type": "application/gzip",
"source": {"kind": "workspace"}}`.

**`action_requested.data`** is unchanged: `{"tool_name", "request", "request_sha256", "reused": false, "self_note"}`.

### Style limits

All changed functions stay under 50 lines and five parameters. The longest are `_validate_call` (about 22 lines),
`restore_directory` (about 24) and `_decompressed_record` (about 22). Every new or changed line is at most 120
characters. `decision_coordinator.py` shrinks by about 40 lines. `models.py` grows by about 25 and stays under 2,000.

## Design chapters

Changed before the code, in Chinese, each rule in one place. Chapter 01 edits the same sections; the instructions
below apply on top of its text.

### `docs/design/04-simulation-engine.md`「原子性与恢复」

In line 738, after the first sentence (chapter 01 replaces that sentence), delete
「请求身份重放读取原结果，参数冲突直接拒绝；」 and keep 「中止的决策轮不执行请求。」. Replace lines 742-746 (from
「一个决策可以登记两条开始动作」 to 「也按组各算一次。」) by:

> 动作身份 `action_id` 恰为 `{decision_id}:action:{四位序号}`，由人物一侧形成（见[人物](06-agent.md)「宿主模型协议」）。环境登记时
> 核对这个形式，不符是协议故障 `Action identity must have the form <decision_id>:action:<four digits>`：这次登记不写事件、不进入本轮，决策轮随后中止，运行停止。
> 决策身份含运行、时刻与世界版本，所以一个动作身份只属于一个决策轮。同一轮里用同一 `action_id`、相同参数再登记，返回
> 第一次的回执，不再写请求事件；参数不同是协议故障 `Action identity was reused with different parameters`。没有跨轮的请求重放：
> `action_requested` 的 `data.reused` 恒为 `false`，只为事件格式不变而保留，没有读取方据它分支。
>
> 世界状态的 `processed_actions` 以动作身份为键，保存已结算动作的回执与即时结果，只留规则还会读取的条目：
>
> - 本秒已结算的结果：本秒的拒绝计数、名称类拒绝免计与拒绝脉冲读它；
> - 开始了进行中或暂停活动的那条动作：活动终结（自然结束、被控制工具结束或在实验终点截断）时核对它仍在，
>   不在是协议故障 `Activity origin action is missing from processed actions`。
>
> 时钟每推进一次，先删去其余条目，再结算这次推进；在这次推进中或其后的决策轮里终结的活动，开始动作留到下一次推进才
> 删去。活动终结不改写开始动作存下的结果。每条动作的完整记录始终在 `action_requested` 与 `action_result` 事件里。
>
> 一个决策可以登记两条开始动作：两个开始工具各一条，`decision_id` 相同、`action_id` 不同。
> 除此之外同一人物本轮的第二条登记是协议故障，运行停止。
> 同刻轮数按组计：一个开始组受理或整组被拒都只加一轮；每人每秒前两次名称类拒绝不计轮数，这一条和下一次决策的安排
> 也按组各算一次。

Line 747 (「耗尽同秒拒绝预算……」) stays.

### `docs/design/09-io-formats.md`

- 「单一状态归属」 line 5: 「运行状态结构版本为 14」 → 「运行状态结构版本为 15」.
- 「世界、人物与活动」 line 24-25: the list becomes 「……人物、两个资金账户、活动、订单、调度边界、已处理动作和已提交呈现留痕。」
- At the end of the section (after line 49, and after chapter 01's paragraph on immutable entries), add:

> 世界状态的 `activities` 在 JSON 里是数组，按活动加入世界的先后排列，每项是一个完整活动对象；读回时按数组先后还原为以
> `activity_id` 为键的映射。规范 JSON 只给对象的键排序、不改变数组次序，所以从检查点恢复的世界与不中断的运行有相同的
> 活动先后，人物在恢复后看到的工具层与进行中活动也是同样的先后。这个字段只接受数组：不是活动对象的数组、或有一项缺少
> 字符串 `activity_id`，报 `world activities must be an array of activity objects`；两项的 `activity_id` 相同，报
> `world activities repeat an activity identity`；写入与加载都拒绝。世界状态里其余以身份为键的集合仍是 JSON 对象。
> `processed_actions` 以动作身份为键，保留哪些条目见[模拟引擎](04-simulation-engine.md)「原子性与恢复」。

### `docs/design/05-recording-engine.md`

**「冻结输入、检查点与分支」.** After 「检查点绑定准确事件前缀、共同世界和人物工作区素材。」 (line 414), insert:

> 每个 AgentSociety2 步骤之后写一个共同检查点；检查点不更新、不删除，每一步都能恢复，恢复从失败的那一秒继续。
> 检查点表的一行是 `(checkpoint_id, body, record_sha256)`：`body` 是 zlib 流（RFC 1950，压缩级别 1，头两字节 `78 01`），
> 解压后恰为检查点载荷的规范 JSON 字节；`record_sha256` 是这些未压缩字节的 SHA-256。检查点记录、恢复分支写出的
> `restored-checkpoint.json`、导出清单里的最终检查点和它的认证因此都与压缩无关。读取检查点先解压、再核对摘要：行不存在、
> `body` 不是二进制、解不开或摘要不符，都报 `Checkpoint is missing or its record is damaged`。
> 人物工作区快照是 gzip 压缩（级别 6）的 POSIX tar，素材的 `media_type` 是 `application/gzip`，来源是 `{kind: workspace}`。
> gzip 头部固定为 `1f 8b 08 00 00 00 00 00 00 ff`：修改时刻为 0、不写文件名、系统字节为 255。tar 与压缩前相同：只含普通文件，
> 按相对路径排序，权限 0600、修改时刻 0。同一工作区在任何时刻快照都得到同一素材。恢复先完整解压并核对 CRC 与长度，
> 解不开报 `Workspace snapshot archive is invalid`，再照下文核对成员后展开。导出与认证把快照当作按摘要和字节数核对的
> 素材，不打开它。

The following sentence 「分支复制全部冻结环境素材和所需快照，恢复前认证归档成员……」 stays.

**「权威事实与投影」.** In chapter 01's added paragraph, the sentence 「检查点不改世界：调用方交上的世界必须与已提交世界逐字节
相同，载荷只按规范 JSON 编码一次，存储的正文就是这份字节，`record_sha256` 是它的 SHA-256。」 becomes 「检查点不改世界：调用方交上的
世界必须与已提交世界逐字节相同，载荷只按规范 JSON 编码一次，`record_sha256` 是这份字节的 SHA-256，存储的正文是它的 zlib 流
（见「冻结输入、检查点与分支」）。」

### `docs/design/06-agent.md`「宿主模型协议」

Line 70: 「`action_id` 为 `{decision_id}:action:{序号:04d}`，登记完本次决策结束。」 → 「`action_id` 为 `{decision_id}:action:{序号:04d}`
（环境只受理这个形式，见[模拟引擎](04-simulation-engine.md)「原子性与恢复」），登记完本次决策结束。」

### `docs/design/07-evaluation.md`

Line 46: 「同一动作的拒绝与结果只计一次，重放不增加次数。」 → 「同一动作的拒绝与结果只计一次。」 This follows the deletion in
`goal_metrics.py:70-71`.

No `docs/manual/` change: no user step changes, and `worldmock status` prints the same fields. Run
`python scripts/build_design_doc.py` after the edits.

## Tests

All new tests:
- have Chinese docstrings;
- match production English text exactly where they assert it;
- use the existing fixtures (`engine_factory`, `tests.life.conftest`, `tests.life.environment_runtime`,
  `tests.integration.runtime_fixtures.branch_of`, `tests.life.day_baseline_runtime`).

A test that checks a failure branch also asserts its English log line with `caplog`. Between two rounds a test calls
`advance_until_ready(engine)` (`tests/life/conftest.py:235`) whenever the agent is not ready, because
`begin_decision_round` accepts only the scheduled agents (`decision_coordinator.py:73-75`).

### New test files and tests

**`tests/integration/life/test_action_identity.py`**

| Test | Asserts |
| --- | --- |
| `test_an_action_identity_not_formed_from_its_decision_is_refused_before_any_event`, parametrized over the ids `"action-plain"`, `f"{d}:action:1"`, `f"{d}:action:00001"`, `f"{d}:action:00a1"`, `f"{d}:action:"` + four full-width digits `０００１`, `f"{d}x:action:0001"`, `f"{other}:action:0001"` (`d` the decision's id, `other` another decision id) | After `begin_decision_round` and `observe`, `submit_action` raises `ProtocolError` matching `^Action identity must have the form <decision_id>:action:<four digits>$`. `store.last_event_seq` is unchanged and `engine.decisions.round.pending_actions == []`. `caplog` holds `is not formed from decision`. Then `f"{d}:action:0001"` is accepted and its receipt's `action_id` is that id |
| `test_a_settled_action_identity_cannot_be_submitted_in_a_later_round` | `act(engine, "first", do_at("家", "测试休息"))`, `advance_until_ready(engine)`, then a round `"second"` submitting `action_identity("first")` raises the same `ProtocolError`. No `action_requested` is added; `same_moment_rounds` and the activity are unchanged; `abort_decision_round("second", …)` then succeeds |
| `test_every_action_request_records_reused_false_and_no_result_carries_it` | After `act`, `act_group` (accepted and rejected) and a control request, every `action_requested` has `data["reused"] is False`, and no `action_accepted`, `action_rejected` or `action_result` has the key `reused` |

**Shared helpers in `tests/life/environment_runtime.py`** (used by the integration and the system tests below):

```python
def check_every_step(monkeypatch, engine, check) -> None:
    """在引擎实例上包住 commit_decision_round、advance 与 decide_midnight_rhythm：调用原方法之后（零点判定未完成时除外）
    执行 check(engine, origins)；origins 是最近一次 advance 调用之前进行中或暂停活动的 origin_action_id 集合。"""


def ledger_holds_only_readable_entries(state, origins: frozenset[str]) -> None:
    """processed_actions 只含规则还会读的条目：本秒结算的结果与最近一次推进前未终结活动的开始动作。"""
```

- `check_every_step` wraps each of the three names with `monkeypatch.setattr(engine, name, wrapper)` (an instance
  attribute; chapter 01's guard wraps the class methods, so both apply). Before calling the original, the `advance`
  wrapper sets `origins = frozenset(a.origin_action_id for a in engine.context.state.activities.values()
  if a.status in {"active", "paused"})`; `origins` starts empty. After the original returns, the wrapper calls
  `check(engine, origins)` unless `engine.midnight_rhythm_pending`.
- `ledger_holds_only_readable_entries(state, origins)` asserts, with `keys = set(state.processed_actions)`:
  - every key is in `origins` or has `state.processed_actions[key].result["occurred_at"] == state.current_time`;
  - `origins <= keys`;
  - the `origin_action_id` of every activity whose status is `active` or `paused` is in `keys`.

**`tests/integration/life/test_processed_actions.py`**

`every_five_minutes` (`tests/life/environment_runtime.py:154-156`) sets a 300 s reconsideration interval, so the
person can decide while an activity is half done. "Remove the origin" below means: `staged = engine.context.state.model_copy(deep=True)`,
`del staged.processed_actions[activity.origin_action_id]`, `staged.version += 1`, then chapter 01's
`engine.context.commit_step(staged, [EventDraft(event_type="environment_event", occurred_at=staged.current_time,
visibility="system", data={"kind": "test_origin_removed"})])` (the pattern of `stage_day_plan`,
`tests/life/conftest.py:38-58`).

| Test | Asserts |
| --- | --- |
| `test_processed_actions_keep_only_this_second_and_unfinished_origins` | On the activity complete day (`engine = create_complete_day_engine(tmp_path / "case", "activity")`, `check_every_step(monkeypatch, engine, lambda engine, origins: ledger_holds_only_readable_entries(engine.context.state, origins))`, then `play_complete_day(engine, "activity")`), the invariant holds after every step. After the final advance, `set(processed_actions)` equals the `origins` of that advance, and each of those activities has `ended_at == end_time` |
| `test_a_paused_activity_keeps_its_origin_until_it_ends` | `engine_factory(mutate=every_five_minutes)`, the step wrapper as above, `act(engine, "rest", do_at("家", "测试休息"))`, `advance_until_ready(engine)`, `act(engine, "pause", pause_request(name), "pause_activity")` (status `completed`), then `act(engine, "chat", do_at("家", "测试闲聊"))` (status `in_progress`). After each of at least two further advances (`advance_next`, settling due decisions with `settle_decision` as `finish_activity` does), the paused activity's `origin_action_id` is a key. `run_to_end(engine)` raises nothing; the paused activity ends `truncated` with `ended_at == end_time`, and its origin is still a key |
| `test_an_activity_whose_origin_action_is_missing_cannot_end[natural]`, `[control]`, `[truncated]` | `[natural]`: `engine_factory()`, `act(engine, "rest", do_at("家", "测试休息"))`, remove the origin, then `finish_activity(engine, rest, "rest")`. `[control]`: `engine_factory(mutate=every_five_minutes)`, the same start, `advance_until_ready(engine)`, remove the origin, then `act(engine, "stop", conclude_request(name), "conclude_activity")`. `[truncated]`: `engine_factory(mutate=one_hour)` (`tests/life/environment_runtime.py`), `act(engine, "commute", travel("公司", "驾车"))` and `finish_activity`, `act(engine, "work", do_at("公司", "季度销售数据整理"))`, remove the origin, then `run_to_end(engine)`. Each final call raises `ProtocolError` matching `^Activity origin action is missing from processed actions$`; `caplog` holds `origin action`. The failing step was not committed: the activity in the committed world (`engine.context.store.state["activities"]`, found by `activity_id`) still has status `active`, and no `action_result` event for its origin has a terminal status |
| `test_an_activity_that_spans_a_branch_ends_in_the_branch` | Start `do_at("家", "测试休息")`, then `child = branch_of(parent, tmp_path, "resume", "-")` while it is active. In the child, `finish_activity` raises nothing and the child has exactly one `action_result` event for the activity's origin after the branch point with a terminal status. After the child's next advance the origin is not a key |
| `test_a_branch_at_a_second_with_rejections_keeps_the_same_moment_budget_and_pulse` | In the parent, at one second: two name-class rejections (`act(engine, label, purchase("不存在的商品"))`, `SUPPLY_NOT_FOUND`, free under `same_moment.py:69-78`), then a counted rejection (`use_app("不存在")`, `ACTION_REQUEST_INVALID`). Branch there. For the child and the parent, `same_moment_rounds`, `latest_rejection_code(state, 1)`, `rejection_counts(state, 1, code)` and `refusal_pulse_applied(state, 1)` are equal for the codes `SUPPLY_NOT_FOUND` and `ACTION_REQUEST_INVALID`. One further rejected `act` in each gives equal `status`, `error_code`, `data["same_moment_round"]` and `body.relaxation_need` |

**`tests/integration/life/test_state_serialization.py`** (new tests in the existing file)

| Test | Asserts |
| --- | --- |
| `test_activities_are_an_array_in_insertion_order` | A world built with activities `[z, a]` (ids sorting opposite to insertion): `model_dump(mode="json")["activities"]` is a list with ids `[z, a]`. `WorldState.model_validate(payload)` and `model_validate_json(json.dumps(payload))` keep `list(state.activities) == [z, a]`. `canonical_json(payload)` keeps the order. `model_dump()` (python mode) stays a dict |
| `test_repeated_or_malformed_activity_arrays_are_refused` | A repeated id raises `ValidationError` matching `world activities repeat an activity identity`. A mapping, an item without `activity_id` and an item that is a string raise one matching `world activities must be an array of activity objects`. `caplog` has the matching English lines. `validated_payload` of a world whose in-memory dict was given a duplicate cannot happen (dict keys are unique); not tested |
| `test_world_state_schema_is_fifteen_and_the_previous_version_is_refused` | Renamed from `…_fourteen_…` (`:182-195`): `WORLD_SCHEMA_VERSION == 15`; 14, `"15"` and `None` are refused; a missing field is refused |
| `test_restored_world_keeps_the_live_activity_order` | `engine_factory()`, `act_group(engine, "pair", do_at("家", "测试休息"), use_app("哔哩哔哩"))`. Precondition: `list(parent.context.state.activities) != sorted(...)`, so the side activity comes first. `branch_of(parent, tmp_path, "resume", …)` gives `list(child.context.state.activities) == list(parent.context.state.activities)`. The child's stored `world_state.body` holds the array in that order |
| `test_same_version_commit_after_an_activity_update_keeps_the_stored_order` | After the same start group, a `report_status` on the main activity (a version step that replaces the main entry in place), then `engine.observe(context(engine, "look"))`. The observation's committed-world byte compare (chapter 01's `require_committed`) passes, and the `world_state` row's `activities` ids equal `list(engine.context.state.activities)` (review revision 4) |

**`tests/system/lifecycle/test_restore_equals_live.py`**

| Test | Asserts |
| --- | --- |
| `test_a_branch_at_every_step_matches_the_live_world_and_observation` | Drive the activity complete day: `parent = create_complete_day_engine(tmp_path / "case", "activity")`, `check_every_step(monkeypatch, parent, probe)`, then `play_complete_day(parent, "activity")`. `probe(parent, origins)` makes a new `step_dir` under `tmp_path`, `child = branch_of(parent, step_dir, "resume", "-")`, and asserts: (1) the canonical JSON of `child.context.state.model_dump(mode="json")` and of the parent's are equal after removing `run_id` and `version`, and `child.context.state.version == parent.context.state.version + 1`; (2) `list(child.context.state.activities) == list(parent.context.state.activities)`; (3) when `parent.scheduling_state().ready_agent_ids` is non-empty, the child's is equal, and `canonical_json(e.observations._observation_data(e.context.state.agents[1]))` is equal for `e` in (`parent`, `child`). The observation is built with the private builder because `observe` would append an observation event to the parent and change the rest of its day; `parent.observations.pending_presentation` is saved before the probe and restored after it. The probe closes the child and removes `step_dir`. Record the run time in `.work/` (the writer's estimate is 1–3 minutes) |

If a difference appears in a key that depends on another mapping's order (sessions, deliveries, callback states,
content progress), stop and report it: persisting those in order is not in the proposal.

**`tests/system/lifecycle/test_state_pruning_identity.py`** (the byte-identity proof for pruning)

| Test | Asserts |
| --- | --- |
| `test_pruning_leaves_every_event_and_export_artifact_unchanged[activity]`, `[callback]` | `record_complete_day(monkeypatch, tmp_path / "pruned" / kind, PROJECT, kind)`, then `monkeypatch.setattr(worldmock.life.time_advance, "prune_processed_actions", lambda state: state)` and the same call under `tmp_path / "kept" / kind`. (1) `SELECT event_seq, event_id, body FROM events ORDER BY event_seq` is identical. (2) Every file under `export/` except `manifest.json` is byte-identical, and the path sets are equal. (3) `manifest.json` without `checkpoint` is equal; `checkpoint.state` without `processed_actions` is equal. (4) `SELECT checkpoint_id FROM checkpoints ORDER BY rowid` is equal on both sides (the complete day takes one checkpoint, at the end); for each id, `FootprintStore(run).get_checkpoint(id).state` (the production reader, so this test needs no change when the checkpoint-row commit compresses the body) is equal on both sides without `processed_actions`; the pruned side's keys are a subset of the kept side's keys with equal entries (no terminal overwrite exists any more); every pruned-side key is either a result with `occurred_at == current_time` or the origin of an activity in that state, and every `active` or `paused` activity's origin is a key |

**`tests/unit/footprints/test_checkpoint_storage.py`**

Store fixture as in `tests/unit/footprints/test_store.py`.

| Test | Asserts |
| --- | --- |
| `test_checkpoint_row_is_the_zlib_stream_of_the_canonical_payload` | `point = store.checkpoint(...)`. The row body is `bytes` starting with `b"\x78\x01"`. `zlib.decompress(body) == canonical_json(point.model_dump(exclude={"record_sha256"}))`. `hashlib.sha256` of it equals `point.record_sha256` and the column. `store.get_checkpoint(point.checkpoint_id) == point` |
| `test_damaged_checkpoint_rows_are_refused`, parametrized: TEXT body (`canonical_json(payload).decode()`), truncated zlib (`body[:-5]`), zlib of another payload with the original digest, and the original body with `"0" * 64` | With `checkpoints_no_update` dropped and the row rewritten, `get_checkpoint` raises `IntegrityError` matching `^Checkpoint is missing or its record is damaged$`. `caplog` holds, respectively, `body is not a compressed record`, `does not decompress`, `record hash does not match` and `record hash does not match`. A missing id logs `does not exist` |

**`tests/unit/footprints/test_workspace_snapshot.py`**

| Test | Asserts |
| --- | --- |
| `test_workspace_snapshot_is_a_gzip_of_the_sorted_tar_with_a_fixed_header` | `reference.media_type == "application/gzip"` and `reference.source == {"kind": "workspace"}`. The first ten bytes are `bytes.fromhex("1f8b08000000000000ff")`. `gzip.decompress` gives a tar whose members are the workspace files in sorted order: REGTYPE, mode `0o600`, mtime 0, equal bytes |
| `test_workspace_snapshot_bytes_do_not_depend_on_the_wall_clock` | `monkeypatch.setattr(time, "time", lambda: 1_000_000_000.0)`, snapshot; set `2_000_000_000.0`, snapshot the same directory into another `AssetStore`. Equal `sha256` |
| `test_restore_refuses_a_plain_tar_and_damaged_gzip`, parametrized: the plain tar, the gzip truncated to 515 bytes, the gzip with its first CRC-32 trailer byte flipped (`len - 8`), 1 KiB of `\x01` | Each is put into an `AssetStore` as `application/gzip`. `restore_directory` raises `IntegrityError` matching `^Workspace snapshot archive is invalid$`; `caplog` holds `is not a valid gzip-compressed tar`; the destination does not exist |
| `test_restore_refuses_a_gzip_of_a_damaged_tar`, parametrized: the tar truncated to 515 bytes, 1 KiB of `\x01`, each gzipped with the production call | The same `IntegrityError` and log line (the `tarfile.TarError` branch). This replaces `test_branch_coverage.py::test_restore_directory_reports_a_corrupt_archive_as_an_integrity_error`, which is deleted (its two cases now fail at the gzip layer) |

**`tests/unit/footprints/test_status.py`** (new test in the existing file)

| Test | Asserts |
| --- | --- |
| `test_status_lists_the_five_latest_checkpoint_ids_without_reading_their_bodies` | A store as in `test_status_reads_confirmed_state_without_modifying_any_run_file` (`'activities': []`). Six rows are inserted directly, `INSERT INTO checkpoints VALUES(?,?,?)` with ids `cp-1` … `cp-6`, body `b"not zlib"` and record `"0" * 64`, in that order. `read_status(run)["recent_checkpoints"] == ["cp-6", "cp-5", "cp-4", "cp-3", "cp-2"]`. A reader that parsed or decompressed a body would fail on these rows |

### Byte-identity proof for this chapter

1. **Pruning.** `test_state_pruning_identity.py`: events, every export artifact and every non-ledger part of every
   checkpoint are identical with and without pruning, on both scripted days.
2. **Replay deletion and readers.**
   - The scripted days never produced a replayed request, so events are unchanged; `test_every_action_request_…` pins
     the request field.
   - The masked complete-day comparison (chapter 08) runs base and head on the migrated test helpers and requires
     equal events and artifacts. Its rules for this chapter are below.
   - The recorded-response run (chapter 08) replays the 30-day recording, whose ids LifeAgent formed, with zero
     divergence.
3. **Activity order.**
   - A live run never re-reads its stored world (`engine_context.py:36` is the only load) [M].
   - `test_a_branch_at_every_step_matches_the_live_world_and_observation` shows that a restore at any step reproduces
     the live world and the live observation byte for byte.
   - The masked comparison shows live runs unchanged against base.
4. **Checkpoint rows.** `test_checkpoint_row_is_the_zlib_stream_…`: the decompressed body is exactly today's text, and
   `get_checkpoint` returns the identical record.
5. **Workspace snapshots.** The tar inside the gzip is built by the unchanged tar code (`checkpoint.py:29-39`).
   `test_snapshot_and_restore_keep_every_workspace_file_byte_for_byte` (existing) restores every file byte for byte.
   `test_workspace_snapshot_is_a_gzip_…` pins the container.
6. **Status.** `recent_checkpoints` values are the same column the payload's `checkpoint_id` was inserted into
   (`store.py:307-308`).

### Rules this chapter hands to chapter 08 (comparator, masked complete-day comparison)

Recording-only differences between base (main) and head:
- **`schema_version` of the world state:** 14 versus 15. Masked by exact path.
- **`activities`:** base stores an object, head an array. Compare head's array as a mapping by `activity_id`; it must
  equal base's object. Base has lost insertion order, so order is compared only by the in-suite restore test. Readers
  searched: the "Raw readers" table above.
- **`processed_actions`:** readers are `same_moment.py:222-234` and the three termination checks. For each compared
  world (the committed world and every checkpoint state), with `now = current_time`, `H` the head's map and `B` the
  base's:
  - **Key set.** `set(H) == {k in B : B[k]["result"]["occurred_at"] == now} | {a.origin_action_id for every head
    activity a with status active or paused}`. This is exact: an entry settled in this second is in both; an origin
    kept from the last advance is either still active or paused, or ended in this second, and then base's terminal
    overwrite carries `occurred_at == now`.
  - **Entries.** For every head key `k`, `H[k] == B[k]`, except when `k` is the `origin_action_id` of a head activity
    whose status is terminal (completed, cancelled, failed or truncated). There base overwrote `result` with the
    terminal result (`action_evidence.py:26-33`), which the terminal `action_result` event carries in full; the
    comparison then covers every field except `result`.
  - Consequences, not separate checks: `same_moment._second_rejections` gives the same list from both worlds (base and
    head both reload the map in key order, and a terminal result is never `rejected`), and every active or paused
    activity's origin is a key in both.
  - Chapter 08's `processed-actions-pruned` entry says "every head entry equals the base entry". That reports a
    difference at every checkpoint taken in a second in which an activity ended (the base's terminal overwrite), on the
    complete days and in the backport verification. Chapter 08 must take this rule; see "Not in this chapter".
- **Checkpoint rows:** head's `record_sha256` must equal `sha256(zlib.decompress(body))`, and base's must equal
  `sha256(body)`. The decoded payloads are then compared under the two state rules above. The two `record_sha256`
  values are not compared with each other: each is the digest of its own recorded form, which the state rules allow to
  differ.
- **Workspace snapshot assets:** compare head's `gzip.decompress(bytes)` with base's tar bytes, byte for byte. Map the
  two `AssetRef`s through those bytes. `media_type` is masked by exact path (`application/gzip` against
  `application/x-tar`).
- **Ops log:** ACTION records lose the key `reused`. Not compared, since the ops log is not a recorded fact.

### Existing tests that change

| File | Change |
| --- | --- |
| `tests/integration/start_interfaces/test_start_group.py` | `test_commit_returns_one_result_per_decision_and_persists_both` (`:111-130`): migrated ids; the docstring drops 「并可按动作身份重放」. `test_group_counts_one_same_moment_round_when_accepted_rejected_or_replayed` (`:303-331`) is renamed `…_when_accepted_or_rejected`; the replay half (`:312-331`) is replaced by one fully rejected group (`act_group(engine, "refused", sleep(60), use_app("哔哩哔哩"))`, both `rejected`) adding exactly one round. `test_inconsistent_group_replay_is_a_protocol_fault` (`:346-361`) is deleted; its successor is `test_a_settled_action_identity_cannot_be_submitted_in_a_later_round`. Every other site gets the id migration |
| `tests/integration/life/test_body_settlement_records.py` | `test_replaying_a_rejected_request_applies_no_pulse_and_writes_no_flows` (`:182-194`) is deleted. The pulse rules stay covered by the other tests of the file and by `test_a_branch_at_a_second_with_rejections_…` |
| `tests/integration/life/test_query_answer.py` | `test_answer_is_the_second_result_and_replaying_the_action_returns_it` (`:279-289`) becomes `test_answer_is_the_second_result`. It keeps `:282-284`, then asserts that the last `action_result` event for the query's action has `data["status"] == "completed"` and `data["activity"]["data"]["answer"] == done.data["answer"]`. The `processed_actions` read and the replay are deleted |
| `tests/integration/life/test_environment_runtime.py` | `test_action_id_replay_is_idempotent_and_parameter_conflict_is_protocol_error` (`:265-285`) is deleted. The in-round idempotence and the conflict stay in `test_current_lifecycle_coverage.py::test_duplicate_submission_reuses_receipt_and_commits_one_actual_activity`. Id migration elsewhere, including `:207-217` and `:305-308` |
| `tests/unit/life/test_current_lifecycle_coverage.py` | Every id becomes `f"{frozen.decision_id}:action:0001"` (`"another-action"` becomes `…:0002`), so each test still reaches the check it names. The `round_id="foreign"` call keeps `…:0001` and still fails with `another decision round` |
| `tests/integration/evaluation/test_errand_metrics.py` | `_Log.bought` (`:74-84`) loses its `reused` field and docstring clause. Delete the replayed purchase at `:242` (the expected `(8, 3)` stays, since it was ignored). In `test_no_errand_or_no_conclusion_memory_gives_null` (`:273-295`) the loop at `:282-285` becomes the single `False` case and the docstring drops 「重放的收尾」. In `test_conclusion_memories_count_routine_and_errand_overlap`, `concluded` (`:315-322`) loses its `reused` parameter and always writes `"reused": False`, and the replayed call at `:326` is deleted (the expected counts stay); the docstring drops 「非重放」 |
| `tests/integration/evaluation/test_goal_metrics.py` | `test_terminal_action_replay_does_not_turn_cancelled_activity_into_rejection` (`:156-163`) is deleted: it tests only the replay. Delete the replayed rows at `:174` and `:241`; the assertions stay, since they already excluded the replay |
| `tests/unit/footprints/test_quality.py` | `test_a_replayed_conclusion_recorded_without_errands_is_an_integrity_fault` (`:376-391`) is renamed `test_a_conclusion_recorded_without_errands_is_an_integrity_fault`; it drops `"reused": True` from the request data (`:384`; the old-shape errands are still an integrity fault) and the docstring's replay sentences. `_rejected` (`:984-988`) loses `reused`; delete `_rejected(13, "SUPPLY_NOT_FOUND", 600, reused=True)` at `:1005`; the expected counts stay, since that event was already excluded |
| `tests/integration/life/test_opslog.py` | Id migration only (`:96-98`, `:106-108`) |
| `tests/integration/life/test_state_serialization.py` | `_set_arrays` (`:67-74`): `activity = payload["activities"][0]`. `:127` unchanged (python mode is a dict). `_world_state` (`:60`): `activities=[activity]` |
| `tests/unit/life/test_navigation_foreground.py:46`, `tests/unit/life/test_daily_reflection_trigger.py:48`, `tests/unit/life/test_environment_contract.py:53` | `activities=[...]` (a list of `ActivityState`) instead of a dict |
| `tests/unit/footprints/test_status.py` | Raw states use `'activities': []` (`:14`, `:52`) and a one-item list for `watch` (`:36-38`) |
| `tests/integration/runtime/test_host_branch_coverage.py` | `:394` and `:518`: `"activities": []` |
| `tests/unit/footprints/test_environment_contract.py` | `:140`: `"activities": [activity]`. `_two_purchases_on_one_route` (`:147`) merges `deliveries` as today and `activities` as `state["activities"] + later["activities"]`. `:172` and `:182` find the activity by `activity_id` in the list |
| `tests/unit/footprints/test_phone_records.py` | `:579`: `"activities": [{"activity_id": "buy", …}]` |
| `tests/unit/footprints/test_branch_coverage.py` | `_insert_checkpoint_row` (`:380-382`) inserts `zlib.compress(canonical_json(body))`. `test_snapshot_directory_skips_directories_and_packs_files` (`:426-435`) expects `application/gzip`. `test_restore_directory_rejects_existing_destination_and_unsafe_members` (`:467-485`) and `_workspace_snapshot` (`:488-498`) gzip the tar with the production `GzipFile(..., mtime=0)` call and put it as `application/gzip`. `test_restore_directory_reports_a_corrupt_archive_as_an_integrity_error` (`:568-577`) is deleted; `test_workspace_snapshot.py` covers both layers |
| `tests/unit/footprints/test_store.py` | `test_rehashed_checkpoint_with_inconsistent_world_is_rejected` (`:264-276`): the rewritten body is `zlib.compress(raw)` (with the digest of `raw`), so the test still reaches the identity check; its `pytest.raises(IntegrityError)` gains `match="disagrees with its world snapshot"`, without which a TEXT body would pass it at the damaged-record check |
| `tests/unit/footprints/test_store_commit_path.py` (chapter 01) | `_stored_body(store, checkpoint_id)` returns `zlib.decompress(...)` of the column |
| `tests/unit/engineering/test_dayrun_baseline.py` | `_case` (`:41-42`): `CREATE TABLE checkpoints (body BLOB, record_sha256 TEXT)` and insert `zlib.compress(body)` |
| `tests/dayrun_baseline.py` | `prepare` (`:124-127`): `raw = zlib.decompress(body)`; assert `_sha(raw) == raw_sha` with the same message; `json.loads(raw)`. The tar normalization (`:86-95`) is unchanged: `tarfile.is_tarfile` and `tarfile.open` read gzip transparently and re-emit a plain tar, so normalized workspace hashes do not depend on gzip bytes |
| `tests/dataset_acceptance.py` | Replace `world_schema_version_still_thirteen` (`:295-298`) with `world_schema_version_still_fourteen` below; in `terminal_result_without_query_details` (`:626-631`) the killer `test_query_answer.py::test_answer_is_the_second_result_and_replaying_the_action_returns_it` becomes `…::test_answer_is_the_second_result` (it still kills the mutation through `_asked`, `:80`); add the mutations below |
| `tests/fixtures/dayrun-activity-baseline.json`, `dayrun-callback-baseline.json` | Re-frozen once at the end (chapter 09) |

`tests/system/viewer/run_viewer.test.cjs:960` changes to an array of activities; that change is chapter 07's.

### Test action-identity migration

Rule: every action id that reaches `LifeEngine.submit_action` or `LifeEnv.submit_action` in a test is
`f"{decision.decision_id}:action:{n:04d}"`, numbered from 1 within the decision.

Helpers:
- `tests/life/conftest.py`: add

  ```python
  def action_identity(label: str, number: int = 1, agent_id: int = 1) -> str:
      """@brief 测试决策 decision-<标签>-<人物> 的第 number 条动作身份，形式与 LifeAgent 相同。"""
      return f"decision-{label}-{agent_id}:action:{number:04d}"
  ```

  `context()` (`:29-35`) is unchanged. `submit_round` (`:215`) uses `f"{item.decision_id}:action:0001"`.
  `conclude_pending` (`:370`) uses `f"{decision.decision_id}:action:0001"`. `_drawn` from `test_sleep_runtime.py`
  moves here as `oversleep_identity(engine, label, seconds=None)`. It searches `action_identity(label, n)` for `n` in
  `range(1, 10000)` with the existing `oversleep_seconds` predicate.
- `tests/life/environment_runtime.py`: `act` (`:11-17`) defaults to `f"{decision.decision_id}:action:0001"` and keeps
  `action_id=` for draw-selected ids. `act_group` (`:28`) uses `f"{decision.decision_id}:action:{index:04d}"`.
- `tests/life/phone_records.py`: `paying_label` and `returning_label` (`:63-80`) build keys from
  `action_identity(label)` and `f"activity-{action_identity(label)}"`.
- `tests/evaluation/conftest.py:339` and `tests/integration/evaluation/test_workload_metrics.py:369`:
  `f"{context.decision_id}:action:0001"`.

Lookups:
- `"action-<label>"` → `action_identity(label)`; `"action-<label>-<n>"` → `action_identity(label, n)`.
- `"activity-action-<label>…"` → `f"activity-{action_identity(label)}…"`.
- Random keys built from those strings change the same way.

Draw-dependent expectations: the action id keys these draws:
- `purchase-duration`, `function-app` and `payment-duration` (`basket.py:40-43`, `storefront_runtime.py:40`);
- `query-duration` and `query-app` (`query_activity.py:38`, `:57`);
- `oversleep` (`sleep_plan.py:87`);
- through the activity id, content picks (`interact.py:64`, `:191`) and navigation draws
  (`navigation_foreground.py:36`, `:99`).

Where a test already searches for a label or number with the wanted draw (`label_where`, `_drawn`, `test_autoplay.py:44-52`,
`test_navigation_foreground.py:68`), only the key construction changes. Where a test hard-codes a drawn value, the
expectation is recomputed from the same key with `random_stream`, or the id is chosen by search so the old value holds:
- `_LATE_ACTION` (`test_sleep_runtime.py:21`, `test_sleep_and_reflection_run.py:29`) is deleted. Each use takes
  `late = oversleep_identity(engine, label, 27 * 60)` with the label of its own `act`: `"late"`
  (`test_sleep_runtime.py:133`, `:139`; `test_sleep_and_reflection_run.py:70`), `"long"` (`:194`) and `"nap"` (`:358`),
  so the asserted 1,620 s (`_LATE_SECONDS` in `test_sleep_runtime.py:22`) stays. An id formed from another decision
  would be refused by the identity check.
- `_drawn(engine, prefix, seconds)` callers (`test_sleep_runtime.py:156`, `:212`, `:231`, `:250`) become
  `oversleep_identity(engine, <the act's label>, seconds)`.
- `test_sleep_runtime.py:173-175` searches `action_identity("on-time", n)` for a zero draw.
- The worked example in `tests/unit/life/test_sleep_plan.py:176` calls `oversleep_seconds` directly and never reaches
  `submit_action`; it stays.

Files, found by `rg -n 'DirectToolCall\(|"action-|f"action-|activity-action-|action_id="' tests`. The suite pinpoints
anything missed: a non-conforming id fails with the exact `ProtocolError` text.
- Helpers: `tests/life/conftest.py`, `tests/life/environment_runtime.py`, `tests/life/phone_records.py`,
  `tests/life/day_baseline_runtime.py`, `tests/evaluation/conftest.py`.
- Direct submissions: `tests/integration/evaluation/test_workload_metrics.py`,
  `tests/integration/life/test_environment_runtime.py`, `tests/integration/life/test_sleep_runtime.py`,
  `tests/integration/runtime/test_conclude_memories.py`, `tests/integration/runtime/test_daily_reflection.py`,
  `tests/integration/runtime/test_env_lifecycle_coverage.py`, `tests/integration/runtime/test_same_second_continuation.py`,
  `tests/integration/start_interfaces/test_start_group.py`, `tests/unit/life/test_current_lifecycle_coverage.py`.
- Lookups and draw keys: `tests/integration/evaluation/test_public_evidence.py`,
  `tests/integration/footprints/test_inspect_current_activity.py`, `tests/integration/footprints/test_acs_export.py`,
  `tests/integration/footprints/test_grocery_export.py`, `tests/integration/footprints/test_phone_record_export.py`,
  `tests/integration/life/test_autoplay.py`, `tests/integration/life/test_autoplay_display.py`,
  `tests/integration/life/test_body_settlement_records.py`, `tests/integration/life/test_callback_runtime.py`,
  `tests/integration/life/test_grocery_cooking.py`, `tests/integration/life/test_opslog.py`,
  `tests/integration/life/test_phone_records.py`, `tests/integration/life/test_query_answer.py`,
  `tests/integration/life/test_purchase_runtime.py`, `tests/integration/start_interfaces/test_purchase_array.py`,
  `tests/integration/start_interfaces/test_sleep_request.py`,
  `tests/integration/start_interfaces/test_location_activities.py`, `tests/system/lifecycle/test_complete_day_scripts.py`,
  `tests/system/lifecycle/test_sleep_and_reflection_run.py`.

Untouched: hand-built events, states and entries that never reach `submit_action`. Examples are
`tests/unit/life/test_activity_boundary_contract.py:135-185`, `tests/unit/footprints/test_app_usage.py` and
`tests/integration/life/test_location_meal.py:503`. The AS2 unit tests `tests/unit/as2/test_decision_round.py` and
`test_direct_router.py`, which never reach `LifeEngine`, are also untouched.

`tests/life/day_baseline_runtime.py` also gains two public helpers in the test-identity commit, taken from the body of
`record_complete_day`, which then calls them:
- `create_complete_day_engine(case: Path, kind: str) -> LifeEngine`: `case.mkdir(parents=True)`, `_environment`,
  `current_run_identity` and `LifeEngine.create` (`:101-105`); `deterministic_day_runtime` stays the caller's job, and
  `record_complete_day` reads the input hashes from `engine.context.inputs`;
- `play_complete_day(engine: LifeEngine, kind: str) -> list`: `stage_day_plan(engine)`, the kind's script
  (`_activity_day` or `_callback_day`) and `run_to_end(engine)`, returning its events (`:107-109`).

`record_complete_day` keeps its parameters (including the fifth one chapter 08 adds, which lands earlier), its steps
and its result, so chapter 01's `test_structural_sharing_day.py` and chapter 08's behaviour recorder, which call it,
need no change for this split.

The new tests use these two helpers, not the private script functions.

### Source mutations to register in `tests/dataset_acceptance.py`

| Name | Symbol | Original | Replacement | Killer tests |
| --- | --- | --- | --- | --- |
| `action_identity_form_unchecked` | `worldmock.life.decision_coordinator::_formed_action_identity` | `return number != action_id and len(number) == 4 and number.isascii() and number.isdigit()` | `return True` | `tests/integration/life/test_action_identity.py::test_an_action_identity_not_formed_from_its_decision_is_refused_before_any_event`, `tests/integration/life/test_action_identity.py::test_a_settled_action_identity_cannot_be_submitted_in_a_later_round` |
| `processed_actions_never_pruned` | `worldmock.life.action_evidence::prune_processed_actions` | `kept = {key: stored for key, stored in state.processed_actions.items() if key in origins}` | `kept = dict(state.processed_actions)` | `tests/integration/life/test_processed_actions.py::test_processed_actions_keep_only_this_second_and_unfinished_origins` |
| `paused_origin_pruned` | `worldmock.life.action_evidence::prune_processed_actions` | `if activity.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}}` | `if activity.status == ActivityStatus.ACTIVE}` | `tests/integration/life/test_processed_actions.py::test_a_paused_activity_keeps_its_origin_until_it_ends` |
| `missing_origin_not_detected` | `worldmock.life.action_evidence::require_origin_action` | `if activity.origin_action_id not in state.processed_actions:` | `if False:` | `tests/integration/life/test_processed_actions.py::test_an_activity_whose_origin_action_is_missing_cannot_end` (all three cases) |
| `activities_stored_in_identity_order` | `worldmock.life.models::WorldState.serialize_activities` | `return list(value.values())` | `return sorted(value.values(), key=lambda activity: activity.activity_id)` | `tests/integration/life/test_state_serialization.py::test_activities_are_an_array_in_insertion_order`, `tests/integration/life/test_state_serialization.py::test_restored_world_keeps_the_live_activity_order` |
| `repeated_activity_identity_accepted` | `worldmock.life.models::WorldState.activities_from_array` | `if identity in mapping:` | `if False:` | `tests/integration/life/test_state_serialization.py::test_repeated_or_malformed_activity_arrays_are_refused` |
| `world_schema_version_still_fourteen` (replaces `world_schema_version_still_thirteen`) | `worldmock.life.models::WORLD_SCHEMA_VERSION` | `WORLD_SCHEMA_VERSION = 15` | `WORLD_SCHEMA_VERSION = 14` | `tests/integration/life/test_state_serialization.py::test_world_state_schema_is_fifteen_and_the_previous_version_is_refused` |
| `checkpoint_row_stored_uncompressed` | `worldmock.footprints.store::FootprintStore.checkpoint` | `zlib.compress(encoded, _CHECKPOINT_ZLIB_LEVEL)` | `encoded` | `tests/unit/footprints/test_checkpoint_storage.py::test_checkpoint_row_is_the_zlib_stream_of_the_canonical_payload` |
| `checkpoint_record_hash_unchecked` | `worldmock.footprints.store::_decompressed_record` | `if hashlib.sha256(payload).hexdigest() != digest:` | `if False:` | `tests/unit/footprints/test_checkpoint_storage.py::test_damaged_checkpoint_rows_are_refused` |
| `workspace_gzip_takes_the_wall_clock` | `worldmock.footprints.checkpoint::snapshot_directory` | `compresslevel=_WORKSPACE_GZIP_LEVEL, mtime=0)` | `compresslevel=_WORKSPACE_GZIP_LEVEL)` | `tests/unit/footprints/test_workspace_snapshot.py::test_workspace_snapshot_bytes_do_not_depend_on_the_wall_clock` |
| `workspace_restore_accepts_a_plain_tar` | `worldmock.footprints.checkpoint::restore_directory` | `archive_bytes = gzip.decompress(content)` | `archive_bytes = content` | `tests/unit/footprints/test_workspace_snapshot.py::test_restore_refuses_a_plain_tar_and_damaged_gzip` |
| `status_lists_the_oldest_checkpoints` | `worldmock.status::read_status` | `"SELECT checkpoint_id FROM checkpoints ORDER BY rowid DESC LIMIT 5"` | `"SELECT checkpoint_id FROM checkpoints ORDER BY rowid LIMIT 5"` | `tests/unit/footprints/test_status.py::test_status_lists_the_five_latest_checkpoint_ids_without_reading_their_bodies` |

Each original occurs exactly once inside its symbol, as `tests/mutation_runner.py:126-128` requires. None of the
registered mutations today has its original in a line this chapter changes. One registered killer is renamed
(`terminal_result_without_query_details`, see the `tests/dataset_acceptance.py` row above), and
`world_schema_version_still_thirteen` is replaced; every other killer keeps its name, and only its ids are migrated.
The three termination sites are each covered by one case of `test_an_activity_whose_origin_action_is_missing_cannot_end`,
so dropping any one `require_origin_action` call fails that case.

## Files touched

Production:
- `src/worldmock/life/decision_coordinator.py` (shared with chapter 01: `:47`, `:133-134`, `:188-189`, `:251-252`,
  `:364-366`; chapter 06: `:527`)
- `src/worldmock/life/models.py` (shared with chapter 01: `WorldState.__deepcopy__`)
- `src/worldmock/life/action_evidence.py`
- `src/worldmock/life/time_advance.py` (shared with chapter 01: `:54`, `:104`, `:145-147`, `:216-218`, `:326`)
- `src/worldmock/life/activity_control.py` (shared with chapter 06, in other functions; both may edit the import
  block)
- `src/worldmock/life/same_moment.py` (docstrings)
- `src/worldmock/life/progress_layer.py`
- `src/worldmock/life/memory_layer.py`
- `src/worldmock/life/settled_text.py`
- `src/worldmock/life/day_plan_inputs.py` (shared with chapter 04, which rewrites the reads of `_result_sentences` and
  `_conclusion_records`; it lands after this chapter and keeps this chapter's conditions on its `history` lists)
- `src/worldmock/evaluation/errand_metrics.py`
- `src/worldmock/evaluation/goal_metrics.py`
- `src/worldmock/footprints/purchases.py`
- `src/worldmock/footprints/quality.py` (shared with chapters 03 and 06, in other functions)
- `src/worldmock/footprints/store.py` (shared with chapters 01, 03, 04)
- `src/worldmock/footprints/checkpoint.py` (shared with chapters 01, 04, 05)
- `src/worldmock/footprints/invariants.py` (shared with chapter 04, in other functions)
- `src/worldmock/status.py` (shared with chapters 04, 05)

Docs:
- `docs/design/04-simulation-engine.md`
- `docs/design/05-recording-engine.md`
- `docs/design/06-agent.md`
- `docs/design/07-evaluation.md`
- `docs/design/09-io-formats.md`

New tests:
- `tests/integration/life/test_action_identity.py`
- `tests/integration/life/test_processed_actions.py`
- `tests/system/lifecycle/test_restore_equals_live.py`
- `tests/system/lifecycle/test_state_pruning_identity.py`
- `tests/unit/footprints/test_checkpoint_storage.py`
- `tests/unit/footprints/test_workspace_snapshot.py`

Changed tests and test support:
- `tests/dataset_acceptance.py` (every chapter registers mutations here)
- `tests/dayrun_baseline.py` (shared with chapter 08: `DayBaseline.prepare`)
- `tests/life/conftest.py` (shared with chapters 01 and 03)
- `tests/life/environment_runtime.py` (shared with chapters 01, 03 and 04)
- `tests/life/phone_records.py`
- `tests/life/day_baseline_runtime.py` (shared with chapters 01, 04 and 08)
- `tests/evaluation/conftest.py`
- `tests/integration/life/test_state_serialization.py`
- `tests/integration/start_interfaces/test_start_group.py`
- `tests/integration/life/test_body_settlement_records.py`
- `tests/integration/life/test_query_answer.py`
- `tests/integration/life/test_environment_runtime.py`
- `tests/unit/life/test_current_lifecycle_coverage.py`
- `tests/integration/evaluation/test_errand_metrics.py`
- `tests/integration/evaluation/test_goal_metrics.py`
- `tests/unit/footprints/test_quality.py`
- `tests/integration/life/test_opslog.py`
- `tests/unit/life/test_navigation_foreground.py`
- `tests/unit/life/test_daily_reflection_trigger.py`
- `tests/unit/life/test_environment_contract.py`
- `tests/unit/footprints/test_status.py` (shared with chapters 04 and 05)
- `tests/integration/runtime/test_host_branch_coverage.py`
- `tests/unit/footprints/test_environment_contract.py`
- `tests/unit/footprints/test_phone_records.py`
- `tests/unit/footprints/test_branch_coverage.py` (shared with chapters 01, 03 and 04)
- `tests/unit/footprints/test_store.py` (shared with chapters 01, 03 and 04)
- `tests/unit/footprints/test_store_commit_path.py` (chapter 01's file)
- `tests/unit/engineering/test_dayrun_baseline.py`
- `tests/integration/evaluation/test_workload_metrics.py`
- `tests/integration/life/test_sleep_runtime.py`
- `tests/integration/runtime/test_conclude_memories.py`
- `tests/integration/runtime/test_daily_reflection.py`
- `tests/integration/runtime/test_env_lifecycle_coverage.py`
- `tests/integration/runtime/test_same_second_continuation.py`
- `tests/integration/evaluation/test_public_evidence.py`
- `tests/integration/footprints/test_inspect_current_activity.py`
- `tests/integration/footprints/test_acs_export.py`
- `tests/integration/footprints/test_grocery_export.py`
- `tests/integration/footprints/test_phone_record_export.py`
- `tests/integration/life/test_autoplay.py`
- `tests/integration/life/test_autoplay_display.py`
- `tests/integration/life/test_callback_runtime.py`
- `tests/integration/life/test_grocery_cooking.py`
- `tests/integration/life/test_phone_records.py`
- `tests/integration/life/test_purchase_runtime.py`
- `tests/integration/start_interfaces/test_purchase_array.py`
- `tests/integration/start_interfaces/test_sleep_request.py`
- `tests/integration/start_interfaces/test_location_activities.py`
- `tests/system/lifecycle/test_complete_day_scripts.py`
- `tests/system/lifecycle/test_sleep_and_reflection_run.py`

Fixtures, re-frozen at the end by chapter 09:
- `tests/fixtures/dayrun-activity-baseline.json`
- `tests/fixtures/dayrun-callback-baseline.json`

Other repository, as a companion change: `WorldMock-Dataset` `tools/native_day.py`, `NativeDay.act`, the
`submit_action` line (`:390` at dataset commit `7918adf`; the file is being edited by another session, so find it by
the text `f"action-{round_id}"`). It becomes `f"{context.decision_id}:action:0001"`. This is tool code, not a dataset
field. The new id is valid on today's engine too, so the companion change lands before, or at the latest together with,
the identity-check commit; after that commit the old id is refused. Its tests
(`tests/test_white_collar_native_day.py`) may see changed action-keyed draws and are re-checked in the same change.

## Not in this chapter

| Item | Where it lives |
| --- | --- |
| The write-through cache, evidence appends, the engine-equals-committed check (including the checkpoint's compare at `store.py:294`), the single encode of the checkpoint payload, `WorldState.__deepcopy__` with shared entries | Chapter 01 (this chapter's compression lands on its `checkpoint`) |
| Observation parts and the `request: null` response evidence; the `parts` table in `SCHEMA` | Chapter 03 |
| Branch by SQL copy (`checkpoint.py:101`, `:133-135`), indexed `fail_run` and counterfactual lookups, WAL, short reads in status | Chapter 04 |
| Lineage in the branch manifest, `error_message` and the heartbeat in `status.py` | Chapter 05 |
| The viewer's loader, its `renderSessions` edit for the array form and the SQL console text about checkpoint bodies | Chapter 07 |
| The comparator, the recorded-response run and the masked complete-day comparison (this chapter supplies their rules above) | Chapter 08 |
| The version table, re-freezing the complete-day baselines, track ownership | Chapter 09 |
| Removing `action_requested.data.reused` | An event-format change the owner has not approved; the field stays as constant `false` |
| Slimming `StoredAction` (its `tool_name` and `request_sha256` are no longer read by a rule) | Not specified; keeping the entry form lets kept entries equal today's |
| `quality.json`'s `reused_request_events` | Kept; it reports a recorded field |
| Persisting insertion order of `sessions`, `deliveries`, `callback_states`, app pushes, `content_progress` and `processed_actions` | Not in the proposal. Residual, the same today and after this chapter (pruning never removes an entry of the current second): after a restore, `processed_actions` comes back in sorted-key order. Of the readers, only `latest_rejection_code` depends on order (the free flags, the counted decision and the pulse depend only on counts), and it feeds only the code named by the budget-exhausted event. It differs only with two counted rejections of different codes in the restored second whose action ids do not sort in settlement order, which happens only across a run-id change or a version digit-length change in that second. Report to the owner if a test shows it |
| Differential checkpoints, chunked or deduplicated workspace snapshots, carried ancestor checkpoints, archiving concluded activities, fewer checkpoints or retention | Proposal, "Not proposed" |
| Skipping the gzip of an unchanged workspace | Not proposed; it costs about 0.1–0.18 s per checkpoint at day 30, and unchanged snapshots still deduplicate on disk |
| Chapter 08's `processed-actions-pruned` rule | Open for the integrator: chapter 08's text ("every head entry equals the base entry", its `project_processed_actions` and its rule test) must take the exception for origins of terminated activities stated in "Rules this chapter hands to chapter 08", or the complete-day comparison and the backport verification report differences at every checkpoint in a second in which an activity ended |
| The masked complete-day comparison of the integration pull request | Open for the integrator (chapter 09): main against the integration branch differs by the test-identity commit's draws alone. Either land the test-identity commit on main first as its own pull request (valid on main's engine; the two complete-day fixtures are then re-frozen there for the new draws), or run that pull request's comparison against the test-identity commit and record main to test-identity as a tests-only change |
| `worldmock status` on run directories written before this chapter | Not supported (a `TypeError` on the mapping-form activities, see "Validation"); a readable refusal would need a structure check in `status.py` and a schema field in every raw test state of chapters 04 and 05, so it is left to the integrator |
