# Proof that nothing the persona experiences changed

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements the proposal's section "Proof that nothing the persona experiences changed"
(`PROPOSAL-long-run-cost.md:334-366`) and the offline items of its acceptance list (`:460-466`): the masked
complete-day comparison, the recorded-response run of segments 1–2 on `d27f2c5a` (determinism control), the backport
verification on `49db53cd` over all 30 days, and the benchmark kit before and after on main. It specifies five things:

| Deliverable | Where | In code identity |
| --- | --- | --- |
| Recorded-response run | `scripts/recorded_response_run.py`, `scripts/recorded_response_child.py` | No (`run_setup.py:107-117` hashes only the `*.py` files under `src/` and `custom/`, `pyproject.toml`, `uv.lock` and the imported AS2) |
| Run comparator | `scripts/compare_runs.py` | No |
| Masked complete-day comparison | `tests/dayrun_baseline.py`, `tests/life/behaviour_day.py`, `scripts/compare_complete_days.py`, a CI job | No |
| Benchmark kit | `.work/long-run-cost/bench/` (untracked) | No |
| Backport verification and determinism control | A procedure using the tools above; receipts in `.work/long-run-cost/verify/` | No |

No production file changes in this chapter. No model tool schema, host validation of tool arguments, CLI option of
`worldmock run`/`resume`/`replay`, or field of `persona.toml`, `config.toml` or the catalog changes. The tools use only
the existing CLI and Python APIs; their own command lines are developer-tool options, not run-command options.

### Where this chapter differs from the proposal

- **"Compared through the production readers, never through a hand-written reconstruction"** (proposal `:356-358`).
  Events are read from exports that the side's own code wrote, so observation parts are rehydrated by the production
  decoder. Restart states come from `restored-checkpoint.json`, and the final checkpoint from the export manifest; both
  were decoded by production code. Some recorded forms have no production reader that returns the base form: the
  dropped request copy, the pruned processed actions and the activity array, at day ends and at every checkpoint. The
  comparator also reads checkpoint rows and workspace snapshots itself, and chapter 02 compresses both. For these it
  applies declared rules, and each rule is checked against recorded bytes. A decoded row must hash to its `record_sha256`. A rebuilt request
  copy must equal the base's copy byte for byte. A projection drops only base data that the head never stores: key
  order, and processed actions that chapter 02 shows are contained in their events. The comparator imports no
  `worldmock` code, so it works on any two code versions.
- **The activity-order fix in the backport** (proposal `:353-354`). The fix stays out of the main backport branch (V1),
  as the proposal says. An extra branch (V2) adds only that commit. V2 shows that the fix changes exactly the one known
  call, toward what the uninterrupted run showed.
- **Order of work** (proposal `:449-456` lists proof tools last). This chapter's commits change no production file. They
  land before the first production commit, because chapters 01, 02 and 05 gate each of their commits with the masked
  complete-day comparison.
- **Equal behaviour signatures** (proposal `:360-361`). A commit that changes the recorded form cannot give equal
  signatures. It passes only with that commit's declared rules, through the comparator (verdict `equal_under_rules`).

### Measured cost of having no instrument today

- **A real run is the only end-to-end check.** The 30-day run cost US$19.70 and 29.9 h, of which 16.4 h was model time
  [O] (`.work/run30/README.md`).
- **The complete-day baselines cannot compare two code versions.** Their signature includes the frozen manifest's
  `code_sha256` (`tests/dayrun_baseline.py:103-129`), and `docs/design/01-architecture.md:252-256` requires a re-freeze
  after any production-source change, comment-only changes included [M].
- **`worldmock replay` calls the live model** (`src/worldmock/runtime.py:155-166`), and resume and replay require an
  identical `code_sha256` and model identity (`src/worldmock/runtime.py:273-286`), so new code cannot continue old
  checkpoints [M].
- **No hash covers a call's dynamic prompt.** `prompt_sha256` and `tool_schema_sha256` cover only the fixed system text,
  the layer schemas and the tool schemas (`run_setup.py:81-89`, `custom/agents/life_agent.py:920-926`), and the latter is
  computed with sorted keys, so even a reorder of schema properties is invisible to it [M].

### Measured properties the tools rely on

All [R] unless marked; scripts and outputs in `.work/run30/scratch/opt-bench/`, `opt-review-bench/` and
`spec-08-proof-tools/`:
- 3,762 of 3,762 recorded requests have a paired provider response (`opt-bench/reqscan.json`).
- Recorded JSON served from 127.0.0.1 through the real AS2 `LLMClient` and LiteLLM Router comes back with
  `model_dump(mode="json")` byte-equal to the recorded response for 7 of 7 call kinds (plan_day, plan_day retry,
  decision, multi-turn, protocol error, thinking-off memory call, truncated) (`opt-bench/cassette_probe.json`).
- LiteLLM's HTTP body holds exactly `model`, `messages`, `tools`, `tool_choice`, `max_tokens` and `thinking` (from
  the recorded `extra_body`), equal to the recorded request; it never sends `stream`, `max_retries` or
  `transport_max_retries`.
- An HTTP 400 ends the call after 1 attempt in 1.44 s (`opt-bench/refusal_probe.txt`), because AS2 treats
  `BadRequestError` as fatal (`vendor/agentsociety2/packages/agentsociety2/agentsociety2/config/llm_dispatcher.py:70-71`).
- Per served call 20–48 ms after warm-up (reviewer's reading of `cassette_probe.json`).
- No recorded request contains a value the tools change:
  - no base URL, metadata URL, `config_sha256`, `persona_sha256` or `code_sha256` in `model`, `messages`, `tools`,
    `tool_choice` or `max_tokens` of any of the 3,762 requests (`spec-08-proof-tools/urlscan.txt`);
  - no wall-clock value in the system, user or tool messages; the only epochs are provider `created` values inside the
    11 plan_day corrections and one simulated `updated_at` (`opt-review-bench/wallscan.json`);
  - the only uuid tokens are provider ids echoed in those 11 plan_day corrections (`opt-bench/reqscan_uuid.txt`).
- The request key sets are `{extra_body, max_retries, max_tokens, messages, model, stream, tool_choice, tools,
  transport_max_retries}` (3,378, which include the bedtime reflection calls with their one tool), the same without
  `transport_max_retries` (42 plan_day) and without `tools` and `tool_choice` (342 memory consolidation calls);
  `extra_body` is always exactly `{"thinking": {...}}`
  (`spec-08-proof-tools/urlscan.txt`).
- Restart boundaries: `branch_created` at seq 7217, 8152, 22587 and 35312, after call 595, 675, 1,887 and 2,879
  (`.work/run30/data/calls.jsonl`). Every ended segment's discarded tail starts with `checkpoint_created` (the
  restored position), then `observation`, `decision_started` and `model_request`. Segments 2–4 then record failure
  evidence (`model_response` phase `failure`, `decision_round_aborted`, `run_failed`). Segment 1 was killed and records
  nothing after the request (`spec-08-proof-tools/tails.txt`). No tail holds an `activity_progress` or `action_result`
  event.
- The redone observation equals the discarded one at the first three boundaries (0 differing keys) and differs in 26
  keys at the fourth, all in `tool_layer.targets[59]` and `[60]`, with no other field of `data` different
  (`scratch/resume/obsdiff.txt`, re-checked without any run-id replacement in `spec-08-proof-tools/obsraw.py`).
- Comparator speed: 5,000 late events, 197.9 MB, in 21.3 s, 9.3 MB/s per side (`opt-bench/compare_cost.txt`).
- Nested `recorded_at` keys are simulated time and model-visible: `data.memory_layer.earlier_today[].recorded_at`
  appears 73 times in 31 sampled observations (`opt-review-bench/nested_recorded_at.txt`).

### A correction found while specifying: run ids are random keys

Action-keyed random streams are keyed by strings that contain the run id. `random_stream(random_state, key)`
(`src/worldmock/life/randomness.py:7-10`) is called with `function-app:{action_id}:…` and `payment-duration:{action_id}:…`
(`life/basket.py:40`, `:43`), `{activity_id}:{draw_index}` (`life/interact.py:64`, `:191`) and callback anchors built
from action ids (`life/decision_coordinator.py:401`, `:457`; `life/callbacks.py:191`). Action ids are
`<decision_id>:action:NNNN` and decision ids start with the run id
(`6c406d98…:round:1790528400:2:agent:1:order:0:action:0001` in `.work/run30/data/actions.jsonl`); the key table is
`docs/design/08-configuration.md:173-200`. A replay under a fresh run id therefore draws different apps, durations and
contents at the first action-keyed draw. The research design did not account for this. This chapter makes the
recorded-response run reuse the source's run id for every segment (see "Run identity reuse"), and the comparator
compares every run-id-bearing compound id literally.

### Expected saving

The tools save no engine time. They replace a real model run as the instrument: US$0 instead of US$19.70, and engine
time only instead of 29.9 h. On this machine:

| Run | Time |
| --- | --- |
| Segments 1–2 (675 calls, one restart) on `d27f2c5a` | about 31–32 min (`days.tsv` engine seconds) |
| 30 days on `d27f2c5a` | about 13.5 h (measured engine time [O]) |
| 30 days on `49db53cd` or main-like code | about 3.8 h (perf regression estimate, not measured) |
| Cassette overhead | 3,762 × 20–48 ms = 1.3–3 min |
| Restart overhead | 65–214 s to the first request at each of 4 boundaries [O] |
| Export and export check | 5–6 min [O] |
| Comparison of two 30-day runs | about 10 min at 9.3 MB/s per side, plus about 1–1.5 h with `--every-checkpoint` on a base with 29.8 GB of checkpoint rows (29.8 GB at 9.3 MB/s is 53 min for that side alone) |
| Disk | about 36 GB per 30-day run at today's checkpoint volume (29.8 GB checkpoints plus 5.3 GB event copies [O]); less once chapter 02 lands |

## Behaviour

### Terms

| Term | Meaning |
| --- | --- |
| Source chain | The run directories of one recorded run, in order: the first run and each resumed branch that the final branch descends from. For run30 these are five directories (`…/20261014T060926Z-white-collar-30day/run`, `…-resume/run`, `…-resume2/run` of `20261014T095627Z`, `…-resume3/run` of `20261014T183746Z`, `…/20261015T030532Z-white-collar-30day-resume3/run`). The failed resume attempt `20261014T182136Z-…-resume2` is not part of the chain |
| Segment k | The k-th directory of a chain (1-based). Its chain events are those with `L_{k-1} < event_seq ≤ L_k` (with `L_0 = 0` and `L_n = ∞`) |
| Chain position `L_k` | `last_event_seq` of the checkpoint that segment k+1 restored (`restored-checkpoint.json` of segment k+1). `branch_created` is at `L_k + 1` |
| Call | A `model_request` event of the final segment's database with its terminal `model_response`; numbered 1..T in event order. A call belongs to the segment that holds its request event |
| Boundary k | The restart between segment k and k+1. Its first call is the first call of segment k+1 |
| Discarded request `D_k` | The first `model_request` event with `event_seq > L_k` in segment k's own database (its discarded tail). Absent when the tail recorded no request |
| Cassette | The calls of the source chain, served in order by the tool's local server |
| Base, head | The two sides of a comparison: the code before and after a change |
| Recorded form | How a fact is stored (row encoding, object versus array, a copy kept or not), as opposed to the fact |
| Declared rule | A named entry of the comparator's rule table that turns the head's recorded form back into the base's |

### What each tool proves and what it cannot

| Tool | Proves | Cannot prove |
| --- | --- | --- |
| Recorded-response run | Every request of one real recording, in its six model-visible fields, is byte-identical (canonical JSON) when the new code replays the recording's own answers, including restarts at the recorded positions. With the comparator, every settled fact of the replay equals the recording's | Anything for a prompt lineage without a recording: run30 is on the `d27f2c5a` lineage, and `0bd2de65` changed prompts, so main itself needs its own recording (the next real run). Paths the recording never took: transport failures, parks, 429, the whole-call deadline, cross-round replay, and failure-phase evidence (the failure tails at the boundaries are discarded and never compared). Tool-schema key order against the recording (the recording stores sorted keys; the tool compares raw order only between two replays). Other personas, environments or operating systems (Windows only until issue #5's libm fix). Behaviour after the recording's end. Whether a prompt change is a quality change |
| Comparator | Two runs' recorded facts are equal after the masks and declared rules of this chapter, and nothing else is masked | Key order (it compares canonical values). That masked fields never reach a prompt: that rests on the request scans above and on the network chapter's tests for `transport_attempts` and `lineage`. That a declared rule is the only change: it proves equality only on the rule's own paths. The discarded tails, and the lineage fields that summarize them |
| Masked complete-day comparison | For two scripted days in the artificial environment, every export artifact, checkpoint and run asset is equal between base and head except code identity, including system-text, layer-schema and tool-schema changes (through `prompt_sha256` and `tool_schema_sha256` in the manifest). For a change of recorded form, the same through the comparator under the declared rules | Dynamic prompt rendering: `tests/life/day_baseline_runtime.py:99-121` drives `LifeEngine` directly, with no LifeAgent, no prompt assembly, no model evidence and no restart. Long-run effects. Other environments |
| Benchmark kit | Exact per-day counters and relative timing of the engine on a scripted calendar, and hot-path timings with write-path row digests | Real behaviour (the persona is scripted). Absolute timing on another machine |
| Backport verification | The optimization commits, ported to `49db53cd`, replay run30 with zero divergence | The main-lineage hunks of files that differ between `49db53cd` and main: those hunks are ported by hand, so the port is what is verified (see "Backport verification") |

### Recorded-response run

#### Command line

```
python scripts/recorded_response_run.py run --project PROJECT --source RUN_DIR [RUN_DIR ...] --work WORK
       [--segments N] [--restart-from K:SEQ]
python scripts/recorded_response_run.py raw-order --base WORK --head WORK --report FILE
python scripts/recorded_response_run.py export-source --project PROJECT --source RUN_DIR --cutoff SEQ --work WORK
```

- `PROJECT` is the checkout whose code is under test. The tool itself may come from another checkout (the backport runs
  the tool from the integration branch against a `49db53cd` worktree).
- `--source` lists every run directory of the source chain in order. The tool reads them with
  `sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)` (the form `scripts/render_day.py:40-45` uses, which
  also handles Windows drive letters and spaces) and never writes into them. Both tools open every database this way.
- `WORK` must not exist; the tool creates it.
- `--segments N` replays segments 1..N only (default: all). With `N < n` the replay ends at boundary N's planned
  refusal.
- `--restart-from K:SEQ` is for negative controls only (see "Negative control option").

#### Preflight (before anything is written except `WORK`)

1. `PROJECT/vendor/agentsociety2/packages/agentsociety2/agentsociety2` must be a directory. Otherwise
   `RecordedResponseError("Project root has no restored AS2 source: {path}")`.
2. Load the chain and the plan (next section). Every source run id must be 32 lowercase hex characters (all CLI runs
   are `uuid4().hex`, `src/worldmock/cli.py:104`). Otherwise
   `RecordedResponseError("Source run id {run_id} is not a 32-character hex identity")`.
3. `as2_location(PROJECT, child environment)` runs a child process with the child environment (below):
   `python -P -c "import importlib.util, json; s = importlib.util.find_spec('agentsociety2'); print(json.dumps(list(s.submodule_search_locations)))"`
   with `cwd=PROJECT`. A child that exits non-zero (no package found) gives no location. There must be exactly one
   location, inside `PROJECT`. Otherwise
   `RecordedResponseError("Project AS2 source resolves outside the project: {location}")`.

#### Chain plan

`load_plan(sources)` builds the plan from `compare_runs.Chain.load(sources)` (one chain reader for both tools):
- **First segment.** Segment 1's manifest has no `parent_run_id` (or `null`); otherwise
  `ComparisonError("Run chain is broken at segment 1: the first directory is a branch; pass every segment directory in order")`.
- **Segment links.** For k ≥ 2: `manifest_k["parent_run_id"] == manifest_{k-1}["run_id"]`; `restored-checkpoint.json`
  of segment k exists, its `checkpoint_id` equals `manifest_k["parent_checkpoint_id"]` and its `run_id` equals
  `manifest_{k-1}["run_id"]`; `L_{k-1}` is its `last_event_seq`. In the final segment's database the event at
  `L_{k-1} + 1` is an `environment_event` with `data.kind == "branch_created"`, `data.parent_checkpoint_id` equal to that
  checkpoint id and `run_id == manifest_k["run_id"]` (one primary-key lookup each). Any failure:
  `ComparisonError("Run chain is broken at segment {k}: {reason}")`, with `reason` one of `parent run id differs`,
  `restored checkpoint is missing`, `restored checkpoint differs from the manifest`, `branch event is missing`.
- **Calls.** Two indexed queries on the final database (the expression index `events_agent_type`,
  `src/worldmock/footprints/store.py:24`), for every agent id of the manifest:
  `SELECT event_seq, event_id, json_extract(body,'$.occurred_at'), json_extract(body,'$.data.purpose') FROM events WHERE json_extract(body,'$.agent_id')=? AND json_extract(body,'$.event_type')='model_request' ORDER BY event_seq`
  and the same for `model_response` with `json_extract(body,'$.caused_by[0]')` and `json_extract(body,'$.data.phase')`.
  Each request must have exactly one response whose `caused_by[0]` is the request's event id, whose phase is
  `response` and whose `data.response` is a JSON object (a plan_day response with a validation error is still phase
  `response` with the provider body, `life/day_planning.py:178-186`). A plan_day retry request's own `caused_by` names
  the previous response (`:167-175`) and is not used for pairing. Otherwise `RecordedResponseError("Recorded call {index} at event {seq} has no provider response")` or
  `"Recorded call {index} at event {seq} has more than one terminal response"`. Calls are numbered 1..T by request
  `event_seq`. Plan_day writes its request and response together (`src/worldmock/runtime.py:649-666`) and memory calls
  record before and after the call (`custom/agents/life_agent.py:381-401`); calls are sequential, so request order is
  call order. Bodies are not loaded here; the server reads them by primary key when needed.
- **Boundaries.** For k = 1..n-1: `first_call(k+1)` is the lowest call whose request seq is greater than `L_k`;
  `D_k` is read from segment k's own database with
  `SELECT event_seq FROM events WHERE event_seq > ? AND json_extract(body,'$.event_type')='model_request' ORDER BY event_seq LIMIT 1`
  (the tail is a handful of rows) and its request body is kept in memory.
- **Model metadata** comes from the final manifest's `model_metadata` (`requested_model`, `context_window`,
  `max_output_tokens`); `api_key_env` from the frozen `config.toml` asset; the same-second switch from
  `stage_model_identity.stages.action.same_second_continuation`.

The plan is written to `WORK/plan.json` (format under "Recorded formats").

#### Input copy

The input is rebuilt from the source's frozen environment assets, which are the exact bytes the run loaded
(`src/worldmock/life/world_setup.py:68-81`):
- For every entry of the final manifest's `environment_assets` whose `source.kind` is `environment_input`, read
  `RUN_DIR/assets/<sha256>` of the final segment, check `sha256` and `size_bytes`
  (else `RecordedResponseError("Frozen input asset {path} does not match its recorded digest")`) and write it to
  `WORK/input/<source.path>`. `map.json` (`source.kind` `environment_map`) is generated by the engine and is not copied.
- `base_url` and `metadata_url` are `model.base_url` and `model.metadata_url` of the source `config.toml` parsed with
  `tomllib`. They must equal the final manifest's `stage_model_identity.base_url` and `.metadata_url` (else
  `RecordedResponseError("Frozen config disagrees with the manifest model identity")`).
- `rewrite_model_urls(text, (base_url, metadata_url), port)` replaces, in `config.toml`, the quoted string
  `"<base_url>"` with `"http://127.0.0.1:<port><path of base_url>"` and `"<metadata_url>"` with
  `"http://127.0.0.1:<port><path of metadata_url>"` (run30: `/beta` and `/models`). Each quoted old value must occur
  exactly once (else `RecordedResponseError("Config file must contain each model URL exactly once: {key}")`).
  The result is parsed with `tomllib`; it must equal the source document with only `model.base_url` and
  `model.metadata_url` replaced (else
  `RecordedResponseError("Rewritten config differs from the source beyond the two model URLs")`). The file is written
  as UTF-8 without BOM with LF line ends, which `_read_toml` requires (`src/worldmock/environment.py:78-91`).
- Both URLs share the scheme and host, so `_require_service_urls` (`src/worldmock/model_endpoint.py:46-53`) accepts
  plain http. The precedent for editing only the copy is the dataset's `override_stop_time`
  (`WorldMock-Dataset tools/execution.py:106-118`).
- The port is chosen once, when the server binds `127.0.0.1:0`, before the config is written. The same server serves
  every segment, so every segment's manifest carries the same `base_url` and resume's identity checks
  (`src/worldmock/runtime.py:273-286` for the manifest, `:410-412` for the restored workspace) pass.

#### Server

A `http.server.ThreadingHTTPServer` bound to `127.0.0.1` only, running in a thread of the parent process. Every request
goes through `Cassette.answer(method, path, headers, body)` under one `threading.Lock`; the handler only moves bytes.
It passes its `self.headers` (an `http.client.HTTPMessage`, whose `get` ignores the case of the header name) and
always sends `Content-Length`.

**Exceptions inside the cassette.** `answer` catches any exception it raises itself. It logs
`Recorded-response cassette failed on {method} {path}` with the traceback (`logger.exception`), answers HTTP 400 with
code `cassette_error`, records a divergence of kind `cassette_error` and sets the verdict to `error`. The server never
answers 5xx and never drops a connection. A head with the network chapter's parking would otherwise re-send a 5xx or
a dropped call for up to 12 hours.

**After the first divergence** every further chat request in that child is answered HTTP 400 with code
`after_divergence`. It is written to the request log with outcome `divergence` and adds no divergence record.

**Credential.** Every request must carry `Authorization: Bearer recorded-response-run` (`DUMMY_CREDENTIAL`). Any other
value: HTTP 400 with code `credential`, an error log `Refused a request without the recorded-response credential`, a
divergence of kind `credential`. The header value is never logged or written.

**`GET <metadata path>`** (run30: `/models`) answers 200 with
`{"object": "list", "data": [{"id": <requested_model>, "object": "model", "context_window": <n>, "max_output_tokens": <n>}]}`
from the frozen metadata. `parse_model_metadata` (`model_endpoint.py:56-75`) then yields the frozen capacity. On head,
resume uses the frozen metadata and makes no such request (network chapter); on base every segment makes one. The
report counts them per segment; they are not divergences.

**`POST <base path>/chat/completions`** (run30: `/beta/chat/completions`):
1. Write the raw body to the request log (see "Recorded formats").
2. Parse it with `json.loads`. Not a JSON object: divergence of kind `mismatch`, field `body`.
3. Determine the expectation from the cassette state (next section).
4. Compute `expected_body(recorded_request)`:
   `{key: request[key] for key in VISIBLE_FIELDS if key in request} | request.get("extra_body", {})` with
   `VISIBLE_FIELDS = ("model", "messages", "tools", "tool_choice", "max_tokens")`. For run30 this is exactly the six
   fields `model`, `messages`, `tools`, `tool_choice`, `max_tokens`, `thinking` (four for memory consolidation calls,
   which have no tools).
5. `first_difference(expected, received)` compares the key sets and each value as canonical JSON bytes
   (`json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()`, the same
   form as `canonical_json`, `src/worldmock/footprints/models.py:189-195`). Equal: serve. Different: divergence.
6. Serve: HTTP 200, `Content-Type: application/json`, body
   `json.dumps(recorded_response, ensure_ascii=False).encode("utf-8")` where `recorded_response` is
   `data.response` of the call's `model_response` event (the form the cassette probe proved byte-stable).

**Any other method or path**: HTTP 400 with code `unexpected_path`, error log
`Refused an unexpected request {method} {path}`, divergence of kind `unexpected_path`.

**HTTP 400 body**: `{"error": {"type": "invalid_request_error", "code": <code>, "message": <message>}}` with code one of
`recorded_response_divergence`, `planned_restart`, `extra_call`, `credential`, `unexpected_path`, `cassette_error`,
`after_divergence`. LiteLLM raises `BadRequestError`, which AS2 does not retry (`_FATAL_ERRORS`, unchanged by the
network chapter, whose table keeps `BadRequestError` fatal). The call fails and the run stops through its normal
failure path. This holds for all three call paths: the decision turn, `_dispatch_memory_llm`
(`custom/agents/life_agent.py:392-398` re-raises) and plan_day (`runtime.py:678-684` commits failure evidence and
re-raises). In run30 the first call after every restart point is a decision call.

#### Cassette state machine

The cassette holds `next_call` (1-based, starts at 1), the current segment `segment`, the set `refused` of boundaries
already refused, and the boundary list. The parent sets `segment` before it starts each child
(`Cassette.begin_segment(k)`).

For a chat request in segment k:

| Situation | Expected request | On match | On mismatch |
| --- | --- | --- | --- |
| `next_call` ≤ T, and `next_call` is not `first_call(k+1)` of a boundary k that is unrefused | Recorded request of call `next_call` | Serve call `next_call`; `next_call += 1` | Divergence `mismatch` (HTTP 400) |
| `next_call == first_call(k+1)` and boundary k is not in `refused` (the live run passed `L_k` and makes its next call) | `D_k` when it exists, otherwise the recorded request of call `next_call` | HTTP 400 `planned_restart`; add k to `refused`; `next_call` unchanged; info log `Planned restart refusal before segment {k+1} at call {next_call}` | Divergence `pre_restart_mismatch` (HTTP 400) |
| A request after boundary k's refusal, while still in segment k | none | — | Divergence `after_refusal` (HTTP 400) |
| `next_call > T` | none | — | Divergence `extra_call` (HTTP 400) |

- The pre-restart call is compared with the discarded request `D_k`, not with the redone request, because the live run
  reproduces what the source's live segment did. At the fourth run30 boundary they differ (the activity-order bug), so
  comparing with the redone request would report a false divergence.
- After the parent resumes segment k+1, its first call is compared with the recorded call `first_call(k+1)` (the redone
  request). If it diverges, the divergence record carries `matches_discarded_request`: whether the received body equals
  `expected_body(D_k)`. Every other divergence has `matches_discarded_request: null`.
- The first divergence ends the replay: the parent stops after the current child exits and starts no further segment.

**Divergence record** (one object in `report.json` `divergences`):
- `kind`: `mismatch`, `pre_restart_mismatch`, `after_refusal`, `extra_call`, `missing_calls`, `credential`,
  `unexpected_path`, `segment_completed_early`, `cassette_error`;
- `call_index` (null for `credential`, `unexpected_path`, `extra_call`, `cassette_error`);
- `source_seq`: the recorded request's `event_seq` (for `pre_restart_mismatch`, the seq of `D_k` in segment k's own
  database);
- `segment`: the source segment of that call (for `pre_restart_mismatch`, k);
- `simulated_time`: the recorded request's `occurred_at` as ISO 8601 in the run's timezone, for example
  `2026-10-20T19:43:18+08:00`;
- `purpose`: `plan_day` when the recorded request event has `data.purpose == "plan_day"`, `decision` when its tools
  name `start_main_activity`, `consolidation` when it has no tools, `reflection` when its only tool is
  `submit_reflection`, else `other` (informational; the model-cost chapter's call classes are finer);
- `fields`: the sorted list of differing top-level keys of the expected and received bodies;
- `layer`, `message_index` and `diff` from `first_difference` (below);
- `matches_discarded_request`: as above;
- `body_sha256`: sha256 of the raw received body.

An error log is written for every divergence:
`Recorded-response divergence at call {call} (segment {segment}, event {seq}, {simulated_time}): {layer} differs`.

**`first_difference(expected, received)`** returns `None` when equal, else
`{"fields": [...], "layer": str, "message_index": int | None, "diff": str}`:
- `fields` is built by exactly the line
  `fields = sorted(key for key in set(expected) | set(received) if _differs(expected, received, key))`, where
  `_differs` is true when the key is missing on one side or the canonical bytes differ.
- If `messages` is not in `fields`: `layer` is `tools:<name>` for the first differing tool (by index; `name` is
  `function.name`, else `tools[<i>]`) when `tools` differs, otherwise `",".join(fields)`; `message_index` is null.
- If `messages` is in `fields`: `message_index` is the first index whose canonical bytes differ (the shorter length
  when one list is a prefix of the other). Its role decides `layer`:
  - `system` → `system`;
  - `user` with a content that parses as a JSON array of objects on both sides → the `layer` value (for example
    `工具层`) of the first differing element, or `user[<j>]` when it has none;
  - `user` otherwise → `user`;
  - `assistant` or `tool` → the role name.
- `diff` is `difflib.unified_diff` (`fromfile="recorded"`, `tofile="received"`, `lineterm=""`) of
  `json.dumps(x, ensure_ascii=False, indent=1, sort_keys=True)` of the differing element (layer object, message, tool
  or field value), joined with `\n` and capped at `DIFF_LINE_LIMIT = 400` lines.

#### Segments and restarts

For each segment k (1..N):
1. `Cassette.begin_segment(k)`.
2. Write `WORK/segments/<kk>.json` (`kk` = two digits; the run directory `WORK/segments/<kk>` must not exist, the CLI
   creates it and refuses an existing one, `src/worldmock/cli.py:100-101`) and start the child
   `[sys.executable, "-P", "<tool root>/scripts/recorded_response_child.py", "WORK/segments/<kk>.json"]` with
   `cwd=PROJECT`, the child environment, stdout and stderr to `WORK/logs/segment-<kk>.log`, and a new process group
   (`start_new_session=True` on POSIX, `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` on Windows).
   - k = 1: mode `run`, argv `["--project-root", PROJECT, "run", "--environment", WORK/input, "--output",
     WORK/segments/<kk>, "--same-second-continuation", "on" | "off"]` (the option exists at `d27f2c5a`, `49db53cd` and
     main, `src/worldmock/cli.py:33`).
   - k ≥ 2: mode `resume`, argv `["--project-root", PROJECT, "resume", "--environment", WORK/input, "--parent",
     <run directory of segment k-1>, "--checkpoint", <id>, "--output", WORK/segments/<kk>]`, where `<id>` is
     `data.checkpoint_id` of the event at `L_{k-1}` in the tool's segment k-1 database. That event must be a
     `checkpoint_created` event (else `RecordedResponseError("Segment {k-1} has no checkpoint_created event at sequence {seq}")`).
     Same code and same cassette mean the tool's events align with the source's by seq, so this is the source parent
     checkpoint's chain position.
3. Wait for the child. After the server has answered a planned refusal or any divergence, the parent gives the child
   `REFUSAL_WATCHDOG_SECONDS = 600`; if it is still alive, the parent kills its process tree (POSIX
   `os.killpg(pid, signal.SIGKILL)`, Windows `taskkill /F /T /PID <pid>`), logs
   `Segment {k} did not stop within 600 s after the planned refusal` and records `ended_by: "killed"`.
4. Check the outcome with the pure function `judge_segment(plan, cassette, segment, exit_code)`. It reads only the
   cassette state and the exit code, so unit tests cover it without a child process:

| Segment | Expected | Otherwise |
| --- | --- | --- |
| k < n (or k = N < n) | A planned refusal happened; child exit code non-zero | Exit 0 before the boundary: divergence `segment_completed_early`. Non-zero without a planned refusal or divergence: verdict `error`, `RecordedResponseError("Segment {k} failed before its planned restart; see {log}")` |
| k = n | Child exit 0 and `next_call == T + 1` | `next_call ≤ T` after exit 0: divergence `missing_calls` with `call_index = next_call`. Non-zero exit without divergence: verdict `error` with the same message |

5. Read `WORK/segments/<kk>/manifest.json`. Its `run_id` must equal the source segment's run id (else verdict
   `error`, `RecordedResponseError("Segment {k} ran with run id {actual}, expected {expected}")`). The manifest is read
   whether the child exited 0 or not; a missing manifest is verdict `error` with
   `RecordedResponseError("Segment {k} wrote no run manifest; see {log}")`.

#### Run identity reuse

`scripts/recorded_response_child.py` installs, in the child process only, a one-shot replacement for the single
`uuid4` draw that names the run, then calls the project's `worldmock.cli.main(argv)`:
- mode `run` replaces `worldmock.cli.uuid4` (drawn once at `src/worldmock/cli.py:104`);
- mode `resume` replaces `worldmock.footprints.checkpoint.uuid4` (drawn once at
  `src/worldmock/footprints/checkpoint.py:122`, in the driver process before Ray starts).
- The child imports exactly that module (`importlib.import_module`), sets its `uuid4` attribute, then imports
  `worldmock.cli` and calls `main`. Neither module imports AS2, so `_configure_process` still runs before AS2 is
  imported (`run_setup.py:70-78` refuses the other order).
- The replacement returns `uuid.UUID(hex=<source run id>)` on its first call. A second call logs
  `Recorded-response run expected one run identity draw in {module}` and raises `RuntimeError` with the same text.
- Both sites are identical at `d27f2c5a`, `49db53cd` and `0bd2de65`. The network chapter keeps the draw in
  `_populate_branch` (its `manifest.update` only gains `lineage`). The whole-history chapter replaces only the event
  copy below it. If a later head moves the draw anyway, the replacement is never called, the branch gets a fresh id,
  and step 5 above stops the verification. If the head draws twice in that module, the second draw fails the segment.
  Both fail closed.
- Event ids (`footprints/models.py:131`) and checkpoint ids (`footprints/store.py:328`) stay fresh `uuid4` values in
  the Ray actor process. No random key uses them (`docs/design/08-configuration.md:177`: event uuids may not feed
  any random derivation), so they differ only as identities and the comparator maps them.
- The child is run with `python -P` so that the tool's own `scripts/` directory is not put on `sys.path`; it imports
  only the project's code through `PYTHONPATH`.

#### Child environment (no real key ever leaves the parent)

`child_environment(parent_environment, project, api_key_env)` returns:
- every variable of the parent environment except two kinds, built by exactly the line
  `variables = {name: value for name, value in parent.items() if not _secret_name(name) and not _routing_name(name)}`:
  - names whose upper-cased form contains `KEY`, `TOKEN`, `SECRET` or `PASSWORD` (`_secret_name`);
  - `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `RAY_ADDRESS`, in any case (`_routing_name`). These would send the
    child's model traffic elsewhere, or attach its Ray workers to another cluster that runs other code;
- `api_key_env` (run30: `DEEPSEEK_NODE_KEYS`) set to `recorded-response-run`, which `_configure_process` then copies
  into `AGENTSOCIETY_LLM_API_KEY` (`src/worldmock/run_setup.py:30-52`);
- `NO_PROXY=127.0.0.1,localhost`, `PYTHONDONTWRITEBYTECODE=1`, and the two variables the dataset's execution tool set
  for the recorded run (`WorldMock-Dataset tools/execute.py:46`): `PYTHONUTF8=1` and `LITELLM_LOCAL_MODEL_COST_MAP=True`;
- `PYTHONPATH=PROJECT/src<sep>PROJECT<sep>PROJECT/vendor/agentsociety2/packages/agentsociety2`, followed by nothing
  from the parent (the parent's `PYTHONPATH` is dropped), so the child imports the project's `worldmock`, `custom` and
  AS2.

The tool never reads the variable named by `api_key_env` in the parent. The server's credential check catches any
leak.

#### Finish

After the last segment (or the first divergence):
1. **Export** with the project's code: child `[sys.executable, "-P", "-m", "worldmock.cli", "--project-root", PROJECT,
   "export", "--run-dir", WORK/segments/<last>, "--output", WORK/export]`, adding `"--cutoff", L_N` when N < n. Not run
   after a divergence or error.
2. **Check**: child `[sys.executable, "-P", "-c", "import sys; from pathlib import Path; from worldmock.footprints.validation import validate_export; validate_export(Path(sys.argv[1]))", WORK/export]`
   (`src/worldmock/footprints/validation.py:29`). A non-zero exit sets `export.check` to `failed` and `error` to the
   last line of its stderr; the verdict becomes `error` with
   `RecordedResponseError("Export check failed: {line}")`.
3. **Days table** from the tool's final segment database, one SQL pass over chain events ≤ cutoff
   (`json_extract` of `occurred_at`, `event_type`, `run_id`, `wall_elapsed_ms`): per local date in the run timezone,
   `decisions` (`decision_started`), `calls` (`model_request`), `events`, and `engine_seconds` = the sum of
   `wall_elapsed_ms` deltas between consecutive events of the same `run_id`, attributed to the later event's date.
   It includes the cassette's serving time (1.3–3 min per 30 days). Informational only.
4. Shut the server down (`shutdown()` then `server_close()`), write `WORK/report.json` and print one line:
   `Recorded-response run: {served} calls served, {restarts} restarts reproduced, {divergences} divergences`.

Exit code: 0 for verdict `zero_divergence` with a passed check; 1 for `divergence`; 2 for `error` (also for any
`RecordedResponseError` or `ComparisonError` before the first segment, after `logging.error` of its message).

#### Negative control option

`--restart-from K:SEQ` changes only boundary K: after the planned refusal, the tool resumes segment K+1 from its own
checkpoint at seq `SEQ` (which must be a `checkpoint_created` event of its segment K with `SEQ ≤ L_K`) and rewinds
`next_call` to the first call whose request seq is greater than `SEQ`. The report sets `control` to
`{"boundary": K, "seq": SEQ}`, and the tool does not export. Chain positions after `SEQ` no longer align with the
source, so only the divergence result is meaningful.

#### `raw-order`

Compares two replays' raw request bodies. Both work directories must have been made from the same source chain (same
`plan.json` `calls` and segment run ids, else exit 2 with
`RecordedResponseError("Replays are of different recordings")`). For every `(call, outcome)` line of
`server/requests.jsonl` with outcome `served` or `planned_restart`, `body_sha256` must be equal. For each mismatch the
report lists `call`, both shas and up to 20 `paths` where the two raw bodies, loaded with
`json.loads(..., object_pairs_hook=list)`, differ in key order or value (`path`, `base_keys`, `head_keys`). This is the
only check that sees tool-schema property order, which both the recording (sorted keys, `store.py:175`) and
`tool_schema_sha256` (sorted keys) lose. Exit 0 equal, 1 different, 2 error.

#### `export-source`

Exports a source segment at a cutoff without touching the source: it copies `RUN_DIR/footprints.sqlite` through the
sqlite backup API from a `mode=ro` connection into `WORK/source-run/footprints.sqlite`, copies `manifest.json` and
`assets/`, and runs the project's `worldmock export --run-dir WORK/source-run --cutoff SEQ --output WORK/source-export`
in a child with the child environment. It is used for the determinism control, where `PROJECT` is `d27f2c5a`, the
source's own code, and for the cutoff exports of backport step 7b. The report line
`Source export made by project code at {commit}` goes to stdout.

An export at a cutoff earlier than the database's last event renders timeline rows past the cutoff on main's code. The
whole-history chapter fixes this ("Mid-run cutoffs"). Timelines read only `activity_progress` and `action_result`
events (`scripts/render_day.py:48-58`). Every cutoff this chapter uses is a restart point, and no discarded tail holds
such an event (see "Measured properties"). So the fix cannot change a timeline in these comparisons. A cutoff with such
an event after it would make the two export versions differ in timeline bytes; this chapter uses none.

### Run comparator

#### Command line

```
python scripts/compare_runs.py runs --base-run RUN_DIR [RUN_DIR ...] --base-export DIR
       --head-run RUN_DIR [RUN_DIR ...] --head-export DIR [--cutoff SEQ] [--rule NAME[=BASE:HEAD] ...]
       [--every-checkpoint] --report FILE
python scripts/compare_runs.py events --base-run RUN_DIR --base-seq SEQ --head-run RUN_DIR --head-seq SEQ --report FILE
```

`scripts/compare_runs.py` uses the standard library only and never imports `worldmock`, so it compares runs of any two
code versions. Both sides are opened `mode=ro`.

#### Path grammar

- **Mask and rule paths** are dot-separated keys from the record root without a leading dot; `[]` stands for every
  element of a list; `*` stands for every key of an object. Example: `data.memory_layer.earlier_today[].recorded_at`.
  Masks use no `*`.
- **Difference paths** in reports use the `obsdiff` flattening (`.work/run30/scratch/resume/obsdiff.py`): a leading
  dot, `[i]` for list elements, and `#len` for a list's length, for example `.tool_layer.targets[59].tools#len`.
- `MASK = "<masked>"` replaces a masked value; the key stays, so presence is still compared.

#### Passes

1. **Chains and cutoff.** `Chain.load` for each side (the checks of "Chain plan"). Both chains must have the same number
   of segments (else problem `SEGMENT_COUNT`, verdict `different`). The cutoff defaults to the final database's last
   `event_seq`; with `--cutoff` it must not exceed either side's last seq
   (`ComparisonError("Cutoff {seq} is beyond the recorded events of {side}")`). Different event counts at the default
   cutoff: problem `EVENT_COUNT`, the common prefix is compared, verdict `different`.
2. **Identity map** from SQL only: `SELECT event_seq, event_id FROM events WHERE event_seq <= ? ORDER BY event_seq`
   (no body decoding), and the `checkpoint_created` events with their `data.checkpoint_id` and `run_id` (expression
   index, `agent_id IS NULL`). Event ids map to `<event:SEQ>`, checkpoint ids to `<checkpoint:SEQ>` (the seq of their
   `checkpoint_created` event), run ids to `<run:K>` (the segment whose manifest names it). Within a side these are
   unique (`events.event_id` is `UNIQUE`, `store.py:23`), so the composed base↔head map is a bijection by construction.
3. **Events.** The two exports' `events.jsonl` are read line by line in lockstep, decoded, normalized and compared.
   Before that, each side's lines must match its own database's `(event_seq, event_id)` list up to the cutoff (else
   `ComparisonError("Export {path} does not cover the run database prefix at event {seq}")`). The exports are read
   instead of the database bodies because the export is the decoded form every reader gets: the recorded-evidence
   chapter rehydrates observation parts there, and a decoding bug would show as a difference. Each side's
   normalized-line sha256 is accumulated for the export manifest's digest map.
4. **Manifests.** Segment k's two `manifest.json` files are paired, aligned by the enabled rules (`align_manifests`),
   then normalized (masks, identity map, digest map) and compared. The digest of each side's aligned and normalized
   manifest is the target of every `manifest_sha256` on that side. This pass runs before any checkpoint is normalized,
   so a version or lineage rule does not make every checkpoint differ through its `manifest_sha256`.
5. **States and checkpoints.** See "What is compared". For every pair of states the order is fixed: `align_states`
   (rules), then `normalize_state` on each side (identity map, digest map), then the comparison. The rules work on raw
   ids and on the head's stored form; the identity map's declared paths are written for the base form
   (`activities.*`).
6. **Export artifacts.** Every file of both export directories.

Every pair of events follows the same order: `align_events` (rules, raw ids), then `normalize_event` on each side,
then the comparison.

#### Identity and reference fields (bijective map, declared paths only)

| Record | Path | Kind |
| --- | --- | --- |
| Event | `event_id` | event (self) |
| Event | `run_id` | run |
| Event | `caused_by[]` | event |
| Event | `data.checkpoint_id` (in `checkpoint_created`) | checkpoint (self) |
| Event | `data.parent_checkpoint_id` (in `branch_created`) | checkpoint |
| Event | `data.activity.origin_request_event_id`, `data.activity.data.accepted_event_id` | event |
| Event | `data.data.activity.origin_request_event_id`, `data.data.activity.data.accepted_event_id` | event |
| Event | `data.data.activities[].origin_request_event_id`, `data.data.activities[].data.accepted_event_id` | event |
| Event | `data.data.memory_sources[].verified_event_ids[]` | event |
| Event | `data.body_settlement_event_id`, `data.source_event_ids[]`, `data.errand.origin_event_id` | event |
| Event | `data.observation_event_id`, `data.verified_event_ids[]`, `data.progress_event_ids[]` | event |
| Event | `data.active_activities[].origin_request_event_id`, `data.active_activities[].data.accepted_event_id` | event |
| Event | `data.verified_experiences[].source_event_id` | event |
| World state | `run_id` | run |
| World state | `activities.*.origin_request_event_id`, `activities.*.data.accepted_event_id` | event |
| World state | `processed_actions.*.result.data.activity.origin_request_event_id`, `processed_actions.*.result.data.activity.data.accepted_event_id`, `processed_actions.*.result.data.memory_sources[].verified_event_ids[]` | event |
| Checkpoint record | `checkpoint_id`, `run_id` | checkpoint (self), run |
| Life snapshot asset | `run_id` | run |
| Run manifest | `run_id`, `parent_run_id`, `source_group_id`, `source_run_ids[]`, `lineage[].run_id` | run |
| Run manifest | `parent_checkpoint_id`, `lineage[].checkpoint_id` | checkpoint |
| Export manifest | `run_id`, `source_group_id`, `source_run_ids[]`, `lineage[].run_id` | run |
| Export manifest | `lineage[].checkpoint_id` | checkpoint |
| Export manifest | `frozen_run_manifest.` followed by every run manifest path above | as there |
| Export manifest | `checkpoint` | as a checkpoint record |
| `activities.jsonl` | `origin_request_event_id`, `data.accepted_event_id`, `source_event_ids[]` | event |
| `app-usage.jsonl`, `verified-experiences.jsonl` | `source_event_id` | event |
| `purchases.jsonl` | `request_event_id`, `source_event_id` | event |
| `location-stays.jsonl` | `enter_source_event_id`, `leave_source_event_id` | event |
| `quality.json` | `run_id`, `source_group_id`, `lineage.parents[].run_id` | run |
| `quality.json` | `lineage.parents[].checkpoint_id` | checkpoint |
| `quality.json` | `run_outcome.run_ended_events[]`, `run_outcome.run_failed_events[]`, `memory_references.problems[].event_id`, `day_plan_metrics.*.external_constraints.source_event_ids[]`, `day_plan_metrics.*.external_constraints.other_rejections.source_event_ids[]` | event |

Rules for this table:
- The list comes from an inventory of 1,034 sampled events of every type and kind, the final world state and every
  export artifact of run30 (`spec-08-proof-tools/idpaths.txt`, `statepaths.txt`, `exportpaths.txt`). The `lineage`
  rows are the network chapter's new fields, with the identity paths that chapter hands over
  (`05-network-and-restarts.md`, "Not in this chapter").
- `None` at a declared path stays `None`.
- A non-null value that is not an id of that kind on that side is left as is and recorded as problem
  `REFERENCE_NOT_AN_ID` (`{kind, id_kind, side, path, value}`); the verdict becomes `different`.
- Compound ids that contain a run id are facts and are compared literally: `decision_id`, `action_id`, `activity_id`,
  `round_id`, `call_id`, `order_id(s)`, `errand_id`, `scheduled_id`, `origin_action_id`, `origin_decision_id`, the
  keys of `activities` and `processed_actions`, and the `push_id`s. They pass only when both runs used the same run
  ids, which equivalence requires anyway (random keys, see "Goal and basis").
- Ids inside free text are never mapped.
- An id that differs at a path missing from the table shows up as a difference. The implementer adds the path, with the
  code line that writes it, in the same commit; the determinism control finds any such path first.

#### Masks (exact paths only)

| Record | Path | Class |
| --- | --- | --- |
| Event | `recorded_at`, `wall_elapsed_ms` (top level only) | wall clock (`store.py:141-142`) |
| Run manifest, export manifest `frozen_run_manifest` | `code_sha256`, `git_commit`, `git_dirty` | code identity |
| Run manifest, export manifest `frozen_run_manifest` | `model_identity.base_url`, `stage_model_identity.base_url`, `stage_model_identity.metadata_url`, `stage_model_identity.model_metadata.metadata_url`, `model_metadata.metadata_url` | run location |
| `AGENT.json` member of a workspace snapshot | `initialized_at` | wall clock (`agent/person.py:469`) |
| `AGENT.json` member of a workspace snapshot | `profile.model_identity.base_url`, `profile.model_identity.metadata_url`, `profile.model_identity.model_metadata.metadata_url` | run location |
| `quality.json` | `acceleration.observed_wall_seconds`, `acceleration.speedup`, `acceleration.wall_seconds_by_run` | wall clock (`quality.py:811-837`) |
| `quality.json` | `model_usage.estimated_cost.known_estimate_usd`, `model_usage.estimated_cost.complete_estimate_usd`, `model_usage.by_actual_model[].known_estimated_cost_usd` | wall clock: the peak band is read from `recorded_at` (`quality.py:586-587`, `:680-691`) |
| `quality.json` | `model_usage.by_call_class[].peak_responses`, `model_usage.by_call_class[].peak_share`, `model_usage.by_call_class[].known_estimated_cost_usd` | wall clock, the same peak band (model-cost chapter, "Model usage per call class") |
| Event `model_response` | `data.transport_attempts` | transport record: failed sends with wall-clock `failed_after_ms` and `wait_ms` and the transport error chain; never read by a prompt (network chapter, which hands this mask over) |
| Run manifest, export manifest and its `frozen_run_manifest` | `lineage[].last_recorded_at`, `lineage[].branched_at` | wall clock (network chapter) |
| Run manifest, export manifest and its `frozen_run_manifest` | `lineage[].last_event_seq`, `lineage[].last_event_time`, `lineage[].discarded_events`, `lineage[].end_reason`, `lineage[].end_error` | discarded tail: how far the parent segment got past its restart point and why it stopped |
| `quality.json` | `lineage.downtime_seconds`, `lineage.parents[].downtime_seconds` | wall clock (network chapter) |
| `quality.json` | `lineage.end_reason_counts`, `lineage.parents[].discarded_events`, `lineage.parents[].end_reason`, `lineage.parents[].end_error` | discarded tail |

- Never masked: any nested `recorded_at` (for example `data.memory_layer.earlier_today[].recorded_at`, simulated time),
  `prompt_sha256`, `tool_schema_sha256`, the environment identity, `persona_sha256`, every event body field except
  `model_response.data.transport_attempts`, every state field. The lineage fields that place the restart are compared:
  `checkpoint_event_seq`, `checkpoint_time`, `simulated_from`, `segment_count`, and the mapped `run_id` and
  `checkpoint_id`.
- **Why the discarded-tail class.** A replay reproduces a restart by refusing the first call after the restart point.
  The source segment may instead have been killed (run30 segment 1: `no terminal event`, 3 discarded events) or have
  failed on a transport error. So these fields legitimately differ between a source and its replay. They summarize the
  discarded tail, which no comparison covers ("Not compared, on purpose"), and no prompt builder reads them (network
  chapter, "Behaviour").
- `config_sha256` is not masked. It goes through the digest map below, which proves the configs differ only in the two
  URLs.
- `quality.json` `day_plan_metrics` is keyed by the code identity (`evaluation/goal_metrics.py:28-32`, fed by
  `quality.py:71`). The key equal to that side's manifest `code_sha256` is renamed `<code-identity>`; nothing else in
  it is masked.
- The constants: `EVENT_MASKS` holds the two top-level event paths, and `MODEL_RESPONSE_MASKS` holds
  `data.transport_attempts`, applied to `model_response` events only. `MANIFEST_MASKS` holds the run manifest paths;
  the export manifest applies them both at its root (`lineage[]…`) and under `frozen_run_manifest.`. `AGENT_MASKS`
  holds the four `AGENT.json` paths. `QUALITY_MASKS` holds the `quality.json` paths.
- A later chapter that adds a wall-clock-derived field adds its exact path to this table in the same commit.

#### Digest map (assets, manifests, config)

Each side keeps `raw sha256 → normalized sha256` (and `raw size → normalized size`) for:
- **Assets**, normalized by the `source.kind` of their reference:

| `source.kind` | Normalized content |
| --- | --- |
| `environment_input`, path `config.toml` | The text with the side's quoted `base_url` and `metadata_url` values replaced by `"<model-url>"` (each exactly once). The two values are that side's run manifest `stage_model_identity.base_url` and `.metadata_url` |
| `workspace` | The archive's members in archive order as canonical JSON `[{"name", "mode", "mtime", "sha256"}]`, where `sha256` is of the member's normalized bytes: `AGENT.json` parsed, masked (table above) and canonical; every other member as is. With rule `workspace-gzip` on the head side, the asset is gunzipped first, and the reference's `media_type` is compared as described under that rule |
| `life_checkpoint` | Canonical JSON with `run_id` mapped and `config_sha256` mapped through this digest map |
| any other | The bytes as they are |

- **Manifests**: the raw sha of each `manifest.json` (equal to `sha256_json(manifest)`, the value checkpoints carry in
  `manifest_sha256`, `store.py:332`) maps to the sha of the canonical aligned and normalized manifest (pass 4). This
  is "recompute `manifest_sha256` from the masked manifest".
- **Export artifacts**: the raw sha of each artifact file maps to the sha of its normalized bytes.
- Any string at a field named `sha256`, `config_sha256` or `manifest_sha256` that is a key of the side's digest map is
  replaced by the normalized digest, and a sibling `size_bytes` by the normalized size; any other 64-hex value
  (`persona_sha256`, `request_sha256`, the `memory_updated` file hashes, `catalog_content_sha256`, `map_sha256`, …) is a
  fact. `record_sha256` is verified and then replaced as described under "What is compared".
- Integrity first: every asset's raw bytes must hash to its reference (else
  `ComparisonError("Asset {sha} does not match its reference")`); every export artifact must match its export-manifest
  digest (else `ComparisonError("Export artifact {name} does not match its manifest digest")`).

#### What is compared

| Layer | Compared | Notes |
| --- | --- | --- |
| Model-visible requests | `model_request.data.request` of every event, canonical | Also checked online by the recorded-response run |
| Decision inputs | Every `observation` in full (including `activity_bindings`, `errand_item_bindings`, the private bindings), `decision_started`, `action_requested`, `action_accepted`, `action_result`, `action_rejected` | No rule may touch them |
| Settled facts | Every `environment_event` (body settlement, deliveries, receipts, pushes, callbacks, weather, branch), every `activity_progress` with its `resource_changes`, every `memory_updated` with its file hashes | `memory_updated` hashes are deterministic: AS2 episode ids are `m_{tick:08d}_{counter:06d}` (`agent/memory.py:526`), tar mtimes are 0 (`checkpoint.py:38`) |
| Model evidence | Every `model_response` in full, including the provider body and reasoning | Served from the cassette, so equal by construction in replays |
| Run manifests | Each segment's | Masks and digest map |
| Restart states | `restored-checkpoint.json` of segments 2..n | As checkpoint records |
| Day-end states | For each local date (run timezone), the chain checkpoint at or before the cutoff with the highest seq whose `current_time` falls on that date | Positions come from the chain's `checkpoint_created` events, `(event_seq, occurred_at)`, without decoding a row: the event's `occurred_at` is the record's `current_time` (`store.py:302-303`, checked again by `get_checkpoint` at `:348-357`). Base and head must have the same list of positions, else problem `CHECKPOINT_POSITIONS` |
| Final state | The final database's `world_state` row | Only when the cutoff is the last event of both final databases |
| Every checkpoint | With `--every-checkpoint`, every chain checkpoint record at or before the cutoff | About 1–1.5 h for a 30-day base |
| Workspace and life snapshots | Through the digest map of every compared checkpoint's `snapshots` | |
| Export | `manifest.json` (normalized, digests mapped, `checkpoint` as a checkpoint record), `events.jsonl` (pass 3), `activities.jsonl`, `verified-experiences.jsonl`, `purchases.jsonl`, `app-usage.jsonl`, `location-stays.jsonl`, `asset-index.jsonl` (rows in order, digests mapped), `quality.json`, every `timeline/*.md` (bytes), every file of `assets/` (normalized digests as a multiset) | |

A chain checkpoint's record is read from the segment whose manifest `run_id` equals the `checkpoint_created` event's
`run_id`. Its row must satisfy `sha256(body) == record_sha256` (after decoding with rule `checkpoint-zlib` on the head
side), else `ComparisonError("Checkpoint {id} record digest does not match its body")`. A record read from
`restored-checkpoint.json` or from the export manifest's `checkpoint` is verified the same way: its `record_sha256`
must be the sha256 of the canonical JSON of the record without `record_sha256` (`store.py:299-301`). The normalized
record replaces `record_sha256` by `<verified>`. The two sides' `record_sha256` values are never compared with each
other, since each is the digest of its own recorded form.

Not compared, on purpose: `status.json`, `heartbeat.json`, `result.json`, `logs/`, the live `as2/` workspace
directories (their content at each compared checkpoint is in the snapshots), the run database's `artifacts` index rows
(the assets themselves are compared), and the discarded tails of failed segments.

#### Declared reconstruction rules

A rule turns the head's recorded form back into the base's, then the ordinary comparison runs. The table is a constant
in `scripts/compare_runs.py` (`RULES`); `--rule NAME` enables an entry; value rules take `=BASE:HEAD`. Every entry
carries the reader search that justifies it, as text in the table.

| Name | Kind | Target (scope, path) | Head form → base form | Reader search cited | Introduced by |
| --- | --- | --- | --- | --- | --- |
| `request-copy` | rebuild | `event:model_response`, `data.request` | When the base event's `data.request` is not null and the head's is null, set the head's to the `data.request` of the head `model_request` named by `caused_by[0]` | The inventory under "Each model request stored once" in chapter 03 (`continuation.py:105-117`, `progress_layer.py:305-335`, `decision_coordinator.py:597-608`, `store.py:186-200`, `quality.py:583-643`, `behavior_metrics.py:239-249`, `status.py:86-99`, `scripts/inspect_run.py:526-600`, `tools/run-viewer-context-view.js`) | Chapter 03 |
| `checkpoint-zlib` | decode | `checkpoint-row`, `body` | `zlib.decompress` of the stored body; `record_sha256` must be the sha256 of the decoded bytes | Readers of checkpoint rows: `store.get_checkpoint` (`store.py:334-360`), `status.py:39-40`, the export embed (`export.py:86-92`) | Chapter 02 |
| `workspace-gzip` | decode | `workspace-asset`, `content` | `gzip.decompress` before tar normalization. In every `AssetRef` whose `source` is `{"kind": "workspace"}` (checkpoint `snapshots`, the export manifest's checkpoint, `asset-index.jsonl`), the head's `media_type` `application/gzip` is compared as the base's `application/x-tar`; any other pair of values is a difference | Readers of workspace snapshots: `restore_directory` (`checkpoint.py:54-73`), export copy (`export.py:90-91`); chapter 02 hands over the `media_type` alignment ("Rules this chapter hands to chapter 08") | Chapter 02 |
| `activities-array` | projection | `state`, `activities` | The head's array of activity objects, in insertion order, each carrying `activity_id` (chapter 02, "Behaviour", the stored form), becomes `{entry["activity_id"]: entry}`; a repeated id is refused (`ComparisonError("Head activities repeat an activity identity")`) | Same content, order added: the base form sorts keys, so the base never recorded an order | Chapter 02 |
| `processed-actions-pruned` | projection | `state`, `processed_actions` | See "The processed-actions projection" below | Readers of processed actions on this tree: `decision_coordinator.py:297`, `:585`, `same_moment.py:228`, `time_advance.py:306`, `activity_control.py:468-474`; chapter 02 states the readers after pruning and the containment of every pruned entry in its events (3,273 of 3,273 [R]) | Chapter 02 |
| `world-schema-version` | value | `state`, `schema_version` | Replaced by the base value when the base has `BASE` and the head has `HEAD` exactly | Structure version, not a fact | Chapter 02 (`14:15`) |
| `run-manifest-version` | value | `run-manifest`, `schema_version` (also `frozen_run_manifest.schema_version` in the export manifest) | As above | As above | Network chapter (`6:7`) |
| `export-manifest-version` | value | `export-manifest`, `schema_version` | As above | As above | Chapter 03 (`7:8` against main; `6:8` on the backport) |
| `quality-version` | value | `quality`, `schema_version` | As above | As above | Network and model-cost chapters, one bump between them (`8:9`) |
| `lineage` | added | `run-manifest` `lineage`, `export-manifest` `lineage` and `frozen_run_manifest.lineage`, `quality` `lineage` | Removed from the head when the base record lacks it; compared normally, with the masks above, when both have it | New field: readers are status, export and quality only (network chapter) | Network chapter |
| `transport-attempts` | added | `event:model_response`, `data.transport_attempts` (the network chapter writes it on the `response` and `failure` phases only, `[]` when the first send succeeded, never on `model_request`) | Removed from the head when the base event lacks the key; masked when both have it | Never read by a prompt (network chapter: kept out of `data.response`) | Network chapter |
| `cost-by-class` | replaced | `quality`, `model_usage.by_call_class` | When the base has `model_usage.by_purpose` and no `by_call_class`, and the head has `by_call_class` and no `by_purpose`, both keys are removed; any other combination is compared as it is | Report only, recomputed from the compared events by export certification. The model-cost chapter's invariants show that the class rows sum to the old purpose rows ("Model usage per call class") | Model-cost chapter |

**The processed-actions projection** (`project_processed_actions(base_state, head_actions)`, applied to a base state
and the head state of the same position):
- `projected = {key: base_actions[key] for key in head_actions if key in base_actions}` restricts the base map to the
  head's keys. A head key missing in the base stays a difference.
- **Terminal overwrites.** The base overwrites the start result of an activity's origin action with the terminal result
  when the activity ends (`_terminal_action_records`, `action_evidence.py:26-33`, called at `time_advance.py:310`,
  `:394` and `activity_control.py:474`). Chapter 02 deletes that overwrite. So a head that still holds the origin in
  the second the activity ended holds the start result, where the base holds the terminal result. For each head key,
  when the two `result` values differ and all of these hold, the projected base entry takes the head's `result`:
  - the base `result.action_id` is the key;
  - the base activity `result.activity_id` exists, its `origin_action_id` is the key, and its status is `completed`,
    `cancelled`, `failed` or `truncated`;
  - the base `result.status` is `completed`, `cancelled` or `failed`;
  - the head `result.activity_id` is the same activity.

  Each such case counts as one application, `terminal_overwrites`. The start result itself is compared in full in its
  `action_accepted` or `action_result` event, and the terminal result in the activity's terminal event. Every other
  field of the entry (`action_id`, `tool_name`, `request_sha256`, `receipt`) is compared as it is.
- **Exact key set** (chapter 02, "Rules this chapter hands to chapter 08"). `required` is the set of base keys whose
  base `result.occurred_at` equals the state's `current_time`, together with the `origin_action_id` of every base
  activity whose status is `active` or `paused`. The head's keys must be exactly `required`. An origin kept from the
  last advance is either still active or paused, or ended in this second, and then the base's terminal overwrite
  carries `occurred_at == current_time`. The two lines are
  `missing = sorted(key for key in required if key not in head_actions)` and
  `extra = sorted(key for key in head_actions if key not in required)`. Each missing or extra key is a difference at
  `.processed_actions.<key>`. So a head that prunes an entry a reader still needs, or keeps one too long, cannot pass.
- What this cannot see: whether the head would have read a pruned entry in some later second. Every later read shows up
  in the events and prompts that follow, which are compared in full.

Guarantees enforced in code:
- `require_allowed(rule)` refuses any rule whose `(scope, path)` is not in the constant `ALLOWED_RULE_TARGETS` (the
  targets above) with `ComparisonError("Rule {name} targets {scope}:{path}, which a prompt, decision, admission or export path reads")`.
  Observations, bindings, action events, settlement events, `memory_updated`, `model_request` and every state field
  other than `activities`, `processed_actions` and `schema_version` can never be rebuilt, projected or filtered.
- There is no event-type filter. A change that removes an event type cannot pass.
- Unknown rule: `ComparisonError("Rule {name} is unknown")`. A value rule without values:
  `ComparisonError("Rule {name} needs BASE:HEAD values")`.
- The report lists every enabled rule with its number of applications. An enabled rule that never applied is listed
  with 0; it is not an error.
- The targets above are final: the network chapter (`05-network-and-restarts.md`) and the model-cost chapter
  (`06-host-costs-and-model-cost.md`) name these fields. A later chapter that adds a recorded-form change adds its
  rule and target in the commit that adds the field.

#### Output

`--report FILE` gets canonical JSON plus a newline (format under "Recorded formats"). The report lists at most 50
differing events in detail, each with at most 20 leaf paths, and values truncated to 300 characters. All counts are
complete. Stdout gets one line, either `Runs are equal: {events} events, {checkpoints} checkpoints, {artifacts} export artifacts`
or `Runs differ: {n} events, {m} states, {k} artifacts; first difference at event {seq} ({event_type}) {path}`.
Exit code: 0 equal, 1 different, 2 `ComparisonError` (logged with `logging.error` before exit).

`events` subcommand: compares the `data` of one event per side literally (no masks, no map) and writes the sorted
difference paths with values. It reads the stored row bodies, so on a database written after the recorded-evidence
chapter an observation's part tables appear as sha256 strings, not rehydrated. It exists for the Oct 20 self-test
(databases written by `d27f2c5a`) and for debugging.

#### Self-tests (acceptance, run on real data, receipts in `.work/long-run-cost/verify/`)

1. **run30 against itself**: `compare_runs.py runs` with the five run30 run directories and `<run30>/export` on both
   sides → exit 0, zero differences, zero problems. This exercises the chain reader, the integrity checks and the
   digest maps on 46,142 real events.
2. **Oct 20 known keys**: `compare_runs.py events --base-run …20261014T183746Z-white-collar-30day-resume3/run
   --base-seq 35312 --head-run …20261015T030532Z-white-collar-30day-resume3/run --head-seq 35313` → exactly the 26
   paths of `scratch/resume/obsdiff.txt`: for each of `.tool_layer.targets[59]` and `.tool_layer.targets[60]`, the
   suffixes `.name`, `.phase`, `.state`, `.tools#len`, and `.available`, `.reason`, `.tool` of `.tools[0]`, `.tools[1]`
   and `.tools[2]`.
3. **The masks are enough**: the determinism control below (source versus replay differ in every event and checkpoint
   id, wall clock, URL and port) → zero differences.
4. **The masks hide nothing else**: the unit tests under "Tests" change one value at a time next to each mask and each
   mapped path, and require a difference.

### Masked complete-day comparison

#### Behaviour signature

`behaviour_signature(case, project)` in `tests/dayrun_baseline.py` is `DayBaseline(case, project,
mask_code_identity=True).signature()`. It is the code-bound signature with exactly these changes:
- In the run manifest, `code_sha256`, `git_commit` and `git_dirty` are replaced by `CODE_IDENTITY_MASK =
  "<code-identity>"` before the normalized manifest digest is computed. Every `manifest_sha256` in checkpoint bodies, in
  the export manifest's embedded checkpoint and in the normalized digest map therefore points to the masked manifest's
  digest ("recomputes `manifest_sha256` inside checkpoint bodies").
- In the export manifest, `frozen_run_manifest.code_sha256`, `.git_commit` and `.git_dirty` get the same mask.
- In `quality.json`, the `day_plan_metrics` key equal to the run manifest's `code_sha256` is renamed
  `<code-identity>`; the artifact digest and `quality_report.size_bytes` are computed from those normalized bytes.
- Nothing else is masked. `prompt_sha256`, `tool_schema_sha256`, the environment identity and every artifact stay in.
- `day_baseline_signature` (code-bound) is unchanged. The two fixtures `tests/fixtures/dayrun-activity-baseline.json`
  and `dayrun-callback-baseline.json` and their two-candidate freeze (`scripts/freeze_rrs_baseline.py`) stay as they
  are; the masked comparison sits beside them and replaces neither.

#### Comparison

```
python scripts/compare_complete_days.py --base-root BASE --head-root HEAD --work WORK [--rule NAME[=B:H] ...]
```
1. `require_comparable_roots(base, head)`:
   - `uv.lock` and `requirements/lock.txt` must be byte-equal (else
     `ComparisonError("Base and head use different dependency locks; record each side in its own environment")`);
   - each root must contain `vendor/agentsociety2/packages/agentsociety2/agentsociety2` (else
     `ComparisonError("Root {root} has no restored AS2 source; run scripts/bootstrap.py there")`);
   - each root must contain `tests/life/behaviour_day.py` (else
     `ComparisonError("Root {root} has no behaviour recorder (tests/life/behaviour_day.py)")`);
   - in each side's child environment (step 2), `agentsociety2` must resolve inside that root, checked with the same
     `find_spec` child as the recorded-response run's preflight (else
     `ComparisonError("Root {root} imports AS2 from {location}")`). One shared venv serves both sides, and its
     editable AS2 install points at one checkout; `PYTHONPATH` must win over it on both sides.
2. For each side, a child `[sys.executable, "-P", "-m", "tests.life.behaviour_day", "--case-root",
   WORK/<side>, "--output", WORK/<side>/behaviour.json]` with `cwd=root` and
   `PYTHONPATH=root/src<sep>root<sep>root/vendor/agentsociety2/packages/agentsociety2` (absolute paths),
   `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, timeout 3,600 s. Each side records with its own code and its own test
   helpers. A timeout or a non-zero exit is `ComparisonError("Recording failed in {root}; see {log}")`, with the
   child's output in `WORK/<side>.log`.
3. Compare the two JSON documents per kind (`activity`, `callback`): `persona_sha256`, `config_sha256`,
   `environment_identity`, `seed`, `summary.artifacts` (per artifact), `summary.checkpoints_sha256`,
   `summary.run_assets`.
4. Verdict:
   - `equal`: all equal;
   - `equal_under_rules`: some differ, `--rule` was given, and `compare_runs.compare` on the two case directories
     (`WORK/<side>/<kind>/run`, `…/export`, one segment each) with those rules reports `equal` for both kinds;
   - `different`: otherwise; the `compare_runs` report of each differing kind is attached, so the leaf paths are in
     the report.
5. Write `WORK/complete-day-behaviour.json`; print `Complete-day behaviour: {verdict}`; exit 0 for `equal` and
   `equal_under_rules`, 1 for `different`, 2 for `ComparisonError`.

"Two LF worktrees": on Windows the comparison is run from two worktrees whose checkout writes LF. The script does not
check line ends: a CR in `config.toml` or `persona.toml` already fails the recording at `_read_toml`
(`environment.py:84-86`), and code identity is masked.

#### Where it runs

- **Every pull request**: a CI job `complete-day-behaviour` in `.github/workflows/tests.yml` (next section). It runs
  without rules. It fails for a behaviour-changing pull request, which is the truthful signal. It also fails for a pull
  request that changes the recorded form or the scripted days' test helpers; such a pull request records the
  comparison with its declared rules locally (`equal_under_rules`) and puts the report in its evidence. A pull request
  that changes a dependency lock fails with exit 2 ("different dependency locks").
- **Integration of this specification**:
  - The proof-tool commits change no production file. The integrator places them before the first production commit.
    This does not reorder any production change.
  - Every production commit is run locally against its parent, with the rules of that commit's chapter.
  - Tests-only commits are not compared against their parent. One of them is chapter 02's test-identity commit
    ("Order of work inside this chapter"). It changes the action ids of the scripted days, and therefore their
    action-keyed draws, so its comparison differs by construction. Chapter 02 makes that commit the base for every
    later commit.
  - The cumulative check runs the test-identity commit as base against the integration head, with `R_V2` (defined
    under "Backport verification") where `export-manifest-version` is `7:8`. Main's own test helpers cannot serve as
    that base, because their action ids give other draws. The test-identity commit's production code is main plus
    chapter 01, and chapter 01's commits each gave `equal` without rules against their parents.
- **The first time**: a base without `tests/life/behaviour_day.py` cannot record. The CI job then writes
  `Base has no behaviour recorder; nothing to compare` to the job summary and passes. If the proof tools reach main
  with the integration pull request, that pull request is this case. If they reach main earlier, the integration pull
  request's job reports `different` because of the test-identity draws alone. Chapter 02 ("Not in this chapter") gives
  the integrator two ways out: land the test-identity commit on main first, or record main to test-identity as a
  tests-only change.

### Benchmark kit (`.work/long-run-cost/bench/`, untracked)

The kit is local tooling under `.work` (AGENTS.md: execution reports and receipts stay in `.work`). It has three files.
The pilot is `.work/run30/scratch/opt-bench/scripted_bench.py`.

**`scripted_calendar.py`** — `python .work/long-run-cost/bench/scripted_calendar.py --engine-root CHECKOUT
--dataset-root WORLDMOCK-DATASET --run30 RUN30_FINAL_RUN_DIR --days 3|30|60 --input INPUT_COPY --out DIR`:
- Drives the real engine through the dataset's scripted persona (`WorldMock-Dataset tools/native_day.py`, `NativeDay`)
  with the pilot's calendar of workday and rest-day chains (2026 calendar: Oct 1–7 holiday, Oct 10 make-up workday).
  The chains use only actions the pinned package offers; a scripted step that the package refuses stops the kit with
  `NativeDayError` (the pilot's behaviour).
- **A checkpoint after every step**, as AS2 Society does (`society/society.py:224-240`); `native_day.py:264-284` alone
  checkpoints only at start and end.
- **Model evidence at run30's rates.** For each scripted decision, before the scripted tool call, it commits run30's
  request/response pairs of the same day index (`day mod 30`, in order, cycling) through
  `engine.record_model_exchange(ModelCallEvidence(context, call_id, phase, 1, request, response))`
  (`src/worldmock/life/engine.py:122-124`; `ModelCallEvidence` in `agent/base/react.py:74-92`). Bodies are point
  lookups in run30's database by the `req_seq` and `resp_seq` of `.work/run30/data/calls.jsonl`. The number of pairs per
  decision follows that day's run30 calls per decision (3,720 decision calls over 3,008 decisions overall). Call ids
  are `{decision_id}:model:{i:04d}:attempt:1`. The `context` is the scripted decision's own open decision context, and
  the request and response phases of one pair pass the same request dict: the recorded-evidence chapter's
  in-process check compares the response's request with the digest recorded at request time.
- **Memory updates at run30's rate** (2,094 in 30 days): after scripted conclusions that carry memories,
  `engine.record_memory_update(...)` (`engine.py:126-128`) with run30's payload shapes for that day; the observation is
  the current decision's. Conclusion summaries, memories and views are sized to run30's medians (from
  `actions.jsonl` `conclude_activity` requests). A `ProtocolError` from `record_memory_update` stops the kit; the kit is
  then fixed, never the engine.
- **The same frozen input copy** (`--input`) for base and head; `bench.json` records `engine_commit`, `engine_dirty`,
  `code_sha256`, `dataset_commit` and the tree sha256 of the input copy.
- **Exact counters per local day**: `decisions`, `calls_committed`, `memory_updates`, `events`, `event_bytes_masked`
  (sum of the canonical length of each event body with the top-level `recorded_at` and `wall_elapsed_ms` removed),
  `checkpoints`, `checkpoint_stored_bytes`, `checkpoint_payload_bytes` (decoded), `state_bytes_end` (canonical length
  of the committed world), `db_bytes_end`, `canonical_json_bytes` (bytes returned by `canonical_json`, counted by
  wrapping `worldmock.footprints.models.canonical_json` in every loaded module that bound the name).
- **Timings per local day**: `wall_s`, `cpu_s`, `decide_s`, `advance_s`, `checkpoint_s`, `evidence_s`,
  `engine_s_per_decision`, `cpu_s_per_decision`; at the end `export_s` and `export_peak_ws_gb`.

**`hot_paths.py`** — `--engine-root CHECKOUT --dataset-root WORLDMOCK-DATASET --run30 RUN30_FINAL_RUN_DIR
--state-mb 10|30 --out DIR`:
- Builds a scratch store from run30's final database with SQLite `ATTACH` and `INSERT … SELECT` (events, artifacts,
  the world row), plus the manifest and the referenced assets. The engine opens it with
  `LifeEngine.open(inputs, scratch_run)` (`life/engine.py:70-73`). `inputs` comes from
  `load_environment(<run30>/../input, metadata_loader=offline_metadata)`, as `native_day.py:423-432` loads its
  environment, so no network call is made.
- 30 MB: expands the 10.5 MB state by cloning concluded activities and processed actions with ids suffixed
  `-x{n}` until the canonical size reaches 30 MB.
- For a head whose world form differs (chapter 02), `to_head_form(state)` builds the head form (activities as the array
  in base key order, processed actions restricted by the head's retention predicate, the head `schema_version`) and
  validates it with the head's own `WorldState.model_validate`. This is benchmark input preparation in `.work`, not a
  migration.
- Times 20 calls each (median and p90) of:
  - an evidence-only commit (head: `append_evidence`; base: `commit` with an unchanged world);
  - a version-advancing commit;
  - `WorldState.model_copy(deep=True)`;
  - `store.checkpoint`;
  - the observation build for agent 1, `engine.observations._observation_data(engine.context.state.agents[1])`. This
    is the build that `LifeEngine.observe` commits (`life/engine.py:75-87`), without the commit, so no decision round
    has to be open.
- The export is timed once, not 20 times: `export_s` and the peak working set of a full export of the scratch store at
  its last event. `export_run` always starts at event 1, so no smaller slice exists.
- **Write-path row digests**: after each timed write, the digest of the rows the call left behind (the world row; the
  inserted event bodies with top-level `recorded_at` and `wall_elapsed_ms` removed and event ids replaced by their seq;
  the checkpoint payload decoded and without `checkpoint_id`), normalized with `scripts/compare_runs.py`'s functions and
  the declared rules. Base and head digests must be equal.

**`compare_bench.py`** — `--base bench.json --head bench.json`:
- `events` and `decisions` per day must be identical (a difference is a behaviour change);
- no byte counter may rise;
- head `cpu_s_per_decision` on days 1, 10, 30 and 60 (those present) must be at most 1.10 × base (the asv default
  factor);
- exit 0 when all hold, 1 otherwise.

Protocol: three base runs first to measure timing noise, then base and head back to back on one machine. The 30- and
60-day times are extrapolations until measured; the pilot measured 11 days (`opt-bench/pilot-30d-days.jsonl`).

### Backport verification and determinism control

#### Why `49db53cd`

run30 is on the `d27f2c5a` prompt lineage, and `0bd2de65` changed prompts. `49db53cd` adds only the history-read fixes to
`d27f2c5a` (`git diff --stat d27f2c5a 49db53cd -- src custom patches`: `life_env.py`, `store.py`, `model_evidence.py`,
`progress_layer.py`, `readable.py`, `session_bounds.py`; `scripts/` unchanged) and keeps `RULES_VERSION = 5`
(`exchange/constants.py:7`), matching run30's input. Between `49db53cd` and main, 22 production files differ
(`git diff --name-only 49db53cd 0bd2de65 -- src custom patches pyproject.toml uv.lock`), plus `scripts/render_day.py`
(3 lines), which writes the export's timelines. Among the files that chapters 01–06 touch:
- identical: `store.py`, `checkpoint.py`, `model_evidence.py`, `observe.py`, `footprints/models.py`, `life/models.py`,
  `engine.py`, `action_evidence.py`, `decision_coordinator.py`, `runtime.py`, `quality.py`, `validation.py`,
  `status.py`, `custom/agents/life_agent.py`, `custom/envs/life_env.py`, the AS2 patches and `run_setup.py`;
- different: `time_advance.py` (4 lines), `activity_control.py` (62), `tool_layer.py` (45), `day_plan_inputs.py` (6),
  `day_planning.py` (3), `settled_text.py` (8), `session_actions.py` (6), `footprints/export.py` (4, the export schema
  6 → 7) and `scripts/render_day.py` (3).

Version numbers on the two bases (`git show`): `WORLD_SCHEMA_VERSION` 14 and `RUN_MANIFEST_SCHEMA_VERSION` 6
(`life/models.py:29-32`), quality report 8 (`quality.py:73`) on both. `EXPORT_SCHEMA_VERSION` is 6 at `49db53cd` and 7
on main. After this specification they are 15, 7, 9 and 8.

#### Procedure

All in LF worktrees, each with its own AS2 restored (`python -c "from pathlib import Path; from
scripts.bootstrap_sources import restore_as2; restore_as2(Path('.'))"` inside the worktree; one shared venv, since the
dependency locks are identical at `d27f2c5a`, `49db53cd` and main). The tool and comparator run from the integration
worktree. `RUN30` is `D:\code\WorldMock-Dataset\execution`, and `SRC` the five run30 run directories in chain order.

| Step | Command (abbreviated) | Required result |
| --- | --- | --- |
| 1. Comparator self-tests | `compare_runs.py runs` run30 against itself; `compare_runs.py events` Oct 20 | Zero differences; exactly the 26 known paths |
| 2. Determinism control on `d27f2c5a` | `recorded_response_run.py run --project <wt d27f2c5a> --source SRC --segments 2 --work V/d27-seg12` | Exit 0; 675 calls served; 1 restart reproduced; refusal at call 676 matched `D_2` (seq 8154 of segment 2). The receipt records the replay's `code_sha256` beside the source's `e494e81a…`. The source ran from a checkout with `git_dirty: true`, so the two need not be equal; zero divergence is the evidence that the worktree behaves as the recording's code did |
| 2b. | `recorded_response_run.py export-source --project <wt d27f2c5a> --source <segment 2 run dir> --cutoff 8151 --work V/d27-src`, then `compare_runs.py runs --base-run <segments 1–2> --base-export V/d27-src/source-export --head-run V/d27-seg12/segments/01 …/02 --head-export V/d27-seg12/export --cutoff 8151` | Exit 0 (zero differences with masks only, no rule) |
| 3. Negative control: one character | In a second `d27f2c5a` worktree, change one character of one sentence of `LIFE_SYSTEM_LAYER` (`src/worldmock/agents/life_prompt.py:21` at that commit); run step 2 there with `--segments 1` | Exit 1; first divergence at call 3 (seq 12, the first decision call; calls 1 and 2 are plan_day), kind `mismatch`, layer `system` |
| 4. Negative control: earlier restart | `--project <wt 49db53cd>`, all segments, `--restart-from 4:35287` (checkpoint `53abf4e8…`, 19:40:00, before both activities exist) | Exit 1; a divergence at or before call 2,880 with layer `工具层` |
| 5. Base replay on `49db53cd` | `run --project <wt 49db53cd> --source SRC --work V/base` | Exit 0; 3,762 calls; 4 restarts; zero divergence. This is the first end-to-end proof of the merged history-read fixes and their first measured engine time per decision (`report.json` `days`) |
| 5b. | `compare_runs.py runs --base-run SRC --base-export <run30 final>/export --head-run V/base/segments/01…05 --head-export V/base/export` | Zero differences (export code is identical between `d27f2c5a` and `49db53cd`) |
| 6. Verification branch V1 | `git switch -c verify/long-run-cost-v1 49db53cd`; cherry-pick the production commits of chapters 01–06 in branch order, except the activity-order commit of chapter 02; then `run --project <wt V1> --source SRC --work V/v1` | Exit 0; 3,762 calls; zero divergence |
| 6b. | `recorded_response_run.py raw-order --base V/base --head V/v1` | Exit 0 (identical raw bodies, key order included) |
| 6c. | `compare_runs.py runs --base-run V/base/segments/* --base-export V/base/export --head-run V/v1/segments/* --head-export V/v1/export --every-checkpoint` with the rule set `R_V1` | Exit 0 |
| 7. Verification branch V2 | V1 plus the activity-order commit; `run --project <wt V2> --source SRC --work V/v2` | Exit 1 with exactly one divergence: call 2,880, kind `mismatch`, layer `工具层`, `matches_discarded_request: true`; no divergence before it |
| 7b. | `export-source --project <wt>` of each side's `segments/04` at cutoff 35311 (each side with its own worktree), then `compare_runs.py runs --base-run V/base/segments/01…04 --head-run V/v2/segments/01…04 --cutoff 35311` with those exports and the rule set `R_V2` | Exit 0 |
| 8. Benchmark kit | On main before and after the integration branch | `compare_bench.py` exit 0 |

- **Rule sets.** Steps 6c and 7b name them with `--rule`:
  - `R_V1` = `request-copy`, `checkpoint-zlib`, `workspace-gzip`, `processed-actions-pruned`,
    `run-manifest-version=6:7`, `export-manifest-version=6:8`, `quality-version=8:9`, `lineage`,
    `transport-attempts`, `cost-by-class`. V1 has no activity-order commit, so its world structure version stays 14
    and it needs no world version rule.
  - `R_V2` = `R_V1` plus `activities-array` and `world-schema-version=14:15`.
- **The activity-order change is its own commit.** Chapter 02 already commits "Ordered activities,
  `WORLD_SCHEMA_VERSION = 15` and the raw readers" on its own, as its activity-order commit ("Order of work inside this
  chapter"). V1 leaves that commit out and V2 adds it. It is the one change whose effect on resumed runs is a
  deliberate behaviour change. The test-identity commit changes only tests; it is picked onto both branches with the
  rest and does not affect a replay.
- **Cherry-pick conflicts** (files that differ between `49db53cd` and main) are resolved by applying the commit's own
  change to the `49db53cd` text. A hunk of main's version is never taken: that would import `0bd2de65` prompt changes
  and break the cassette match. Version constants take the head's value (export schema 8 on top of 6). On V1, a later
  commit whose context lines come from the left-out activity-order commit is resolved the same way, against the text
  without that commit. A commit whose change cannot be applied without a `0bd2de65` behaviour change is left out of the
  branch and listed in the receipt with its reason; it is then verified on main only, by the masked complete-day
  comparison and the next main recording.
- **Excluded from V1 on purpose**: the activity-order commit. It is the proposal's deliberate exception for resumed
  runs; step 7 shows it changes exactly the known call and only toward what the uninterrupted run would have shown
  (`matches_discarded_request: true`). The whole-call deadline (network chapter) never fires against a local server
  that answers in milliseconds, so it needs no exclusion.
- **Receipt**: `.work/long-run-cost/verify/receipt.json` lists each commit as `picked`, `picked with resolution
  (files)` or `excluded (reason)`, the eight steps' exit codes, and the paths of their reports.

#### Which chapters can be verified there

| Chapter | Files that differ at `49db53cd` | Verified on the backport | Rules needed against the `49db53cd` base |
| --- | --- | --- | --- |
| 01 Store commit path | `time_advance.py` (deep-copy sites) | Yes | None (byte-identical records) |
| 02 State and checkpoint storage | `time_advance.py`, `activity_control.py`, `day_plan_inputs.py`, `settled_text.py` | Yes (V1 without activity order; V2 with it) | `checkpoint-zlib`, `workspace-gzip`, `processed-actions-pruned`; for V2 also `activities-array` and `world-schema-version=14:15` |
| 03 Recorded evidence | `footprints/export.py` | Yes | `request-copy`, `export-manifest-version=6:8` |
| 04 Whole-history paths | `footprints/export.py`, `day_plan_inputs.py`, `scripts/render_day.py` | Yes (export, branch copy and restore run in every replay) | None beyond the above |
| 05 Network and restarts | `footprints/export.py`, `day_planning.py` | Yes for frozen metadata on resume, readable failures and lineage; parking and the deadline are not exercised by a local server | `lineage`, `transport-attempts`, `run-manifest-version=6:7`, `quality-version=8:9` |
| 06 Per-decision host costs and model cost | `activity_control.py`, `day_planning.py`, `session_actions.py`, `tool_layer.py` | Yes (prompts must be byte-identical; quality.json by call class in the export) | `cost-by-class`, `quality-version=8:9` |
| 07 Viewer | `tools/` only | No: not part of a run; verified by its own chapter on exports | — |
| 08 Proof tools | none (`scripts/`, `tests/`) | Not applicable | — |

The files touched come from each chapter's "Files touched" list, intersected with the files that differ.

### What stays byte-identical, and why

This chapter adds files under `scripts/`, `tests/` and `.github/` and edits three design documents. Nothing under
`src/`, `custom/`, `vendor/`, `patches/`, `pyproject.toml` or `uv.lock` changes, so `runtime_code_sha256`
(`run_setup.py:107-117`) is unchanged, and so is every model prompt, decision input and settled world fact of every
run. `scripts/render_day.py`, which is outside code identity but writes the export's timelines
(`docs/design/01-architecture.md:257`), is not touched either. In `tests/dayrun_baseline.py` every change is guarded
by `mask_code_identity`. With the default `False`, `quality.json` goes through
`_json(self.normalize(json.loads(raw)))`, the same bytes `content(raw, "quality.json")` gives today (`:99-100`). So
`day_baseline_signature` is unchanged, and the code-bound complete-day fixtures pass unchanged; this chapter does not
re-freeze them.

### Invariants

- The tools never write into a source run directory or a dataset execution directory.
- The recorded-response server listens on 127.0.0.1 only, accepts only the dummy credential, and no child process
  receives a variable whose name marks it as a secret.
- A replay passes only when every chat request equals its recorded request in all visible fields and the call count is
  exact.
- Masks are exact paths; the id map applies only to declared paths and is a bijection onto event, checkpoint and
  segment ordinals; reconstruction rules apply only to the allowed targets; nothing else is relaxed.
- The comparator imports no `worldmock` code; the child imports no tool code.

### What is deleted

Nothing. The pilot scripts under `.work/run30/scratch/opt-bench/` stay where they are (untracked).

## Interfaces and files

All new Python follows AGENTS.md: comments and docstrings in Chinese with a Doxygen `@brief`; code, logs and error
text in English; lines ≤ 120 characters; functions ≤ 50 lines and ≤ 5 parameters (bundle into the dataclasses below);
files ≤ 2,000 lines; every exception branch logs an error. Every `raise RecordedResponseError(...)` and
`raise ComparisonError(...)` is preceded by `logger.error` of the same English text, as in
`model_endpoint.py:52-53`. A handler that catches an exception and goes on logs with `logger.exception`. The style gate
scans only `src/worldmock` and `custom` (`tests/unit/engineering/test_style_gate.py:17`), so reviewers check these
limits for `scripts/`.

### `scripts/compare_runs.py` (new, about 900 lines, standard library only)

Module docstring gist: 「按记录比较两次运行：精确路径屏蔽、双射身份映射、声明的记录形式重建规则；只用标准库，不导入被比较的代码。」

| Name | Signature | Gist of the Chinese `@brief` |
| --- | --- | --- |
| `MASK` | `str = "<masked>"` | 屏蔽占位 |
| `EVENT_MASKS` | `frozenset[str] = frozenset({"recorded_at", "wall_elapsed_ms"})` | 事件只屏蔽顶层两项壁钟 |
| `MODEL_RESPONSE_MASKS` | `frozenset[str] = frozenset({"data.transport_attempts"})` | 模型响应信封里的失败发送记录（壁钟与传输错误） |
| `MANIFEST_MASKS` | `frozenset[str]` (the run manifest paths of "Masks": code identity, model URLs, the lineage wall-clock and discarded-tail fields) | 运行清单的代码身份、模型地址与 lineage 的壁钟和丢弃尾段 |
| `AGENT_MASKS` | `frozenset[str]` (`initialized_at` and the three `profile.model_identity` paths) | 工作区 AGENT.json 的壁钟与地址 |
| `QUALITY_MASKS` | `frozenset[str]` (the `quality.json` paths of "Masks") | 质量报告里由壁钟导出的项与丢弃尾段 |
| `EVENT_REFERENCES`, `STATE_REFERENCES`, `MANIFEST_REFERENCES`, `EXPORT_REFERENCES` | `dict[str, str]` path → kind (`event`, `checkpoint`, `run`) | 声明的身份与引用路径 |
| `ALLOWED_RULE_TARGETS` | `frozenset[tuple[str, str]]` | 重建规则唯一允许的（范围, 路径） |
| `RULES` | `dict[str, Rule]` | 规则表，每条带读取者检索 |
| `ComparisonError` | `class ComparisonError(RuntimeError)` | 输入不完整或损坏，比较不能进行 |
| `Rule` | `@dataclass(frozen=True) name: str; kind: str; scope: str; path: str; reader_search: str; chapter: str` | 一条声明的重建规则 |
| `AppliedRule` | `@dataclass(frozen=True) rule: Rule; base_value: object = None; head_value: object = None` | 本次启用的规则及数值规则的两端取值 |
| `Chain` | `@dataclass(frozen=True) runs: tuple[Path, ...]; manifests: tuple[dict, ...]; restored: tuple[dict \| None, ...]; positions: tuple[int, ...]; timezone: str`; `@classmethod load(cls, runs: list[Path]) -> Chain`; `database(self, index: int = -1) -> sqlite3.Connection`; `segment_of_run(self, run_id: str) -> int` | 按次序读一条运行链并核对父子链接；只读打开 |
| `Side` | `@dataclass(frozen=True) chain: Chain; export: Path` | 比较的一侧 |
| `Options` | `@dataclass(frozen=True) cutoff: int \| None; rules: tuple[AppliedRule, ...]; every_checkpoint: bool` | 比较选项 |
| `IdMaps` | `class IdMaps`; `@classmethod load(cls, base: Chain, head: Chain, cutoff: int) -> IdMaps`; `normalize(self, value: object, kind: str, side: str, path: str) -> object`; attribute `problems: list[dict]` | 两侧身份到序号占位的映射，记下不是身份的引用 |
| `Digests` | `class Digests`; `__init__(self, base: "Side", head: "Side")`; `asset(self, reference: dict, side: str) -> tuple[str, int]` (reads the asset from the first run directory of that side whose `assets/` holds it, else from its export's `assets/`, and caches the result); `manifest(self, raw_sha: str, side: str) -> str`; `artifact(self, raw_sha: str, side: str) -> str` | 原始摘要到规范化摘要的映射 |
| `canonical_bytes` | `(value: object) -> bytes` | 与 canonical_json 相同的唯一编码 |
| `flatten` | `(value: object, path: str = "", out: dict \| None = None) -> dict[str, object]` | obsdiff 写法的叶子展开 |
| `leaf_diff` | `(base: object, head: object, limit: int = 20) -> list[dict]` | 两值的不同叶子路径 |
| `mask_paths` | `(value: object, paths: frozenset[str], path: str = "") -> object` | 只在精确路径上屏蔽；must contain the single line `if path in paths:` |
| `map_ids` | `(value: object, fields: dict[str, str], context: "NormalizeContext", path: str = "") -> object` | 只在声明路径上做身份映射 |
| `NormalizeContext` | `@dataclass side: str; ids: IdMaps; digests: Digests; rules: tuple[AppliedRule, ...]; requests: dict[str, dict]` (`requests` holds the head side's `model_request` bodies by event id until their response is seen) | 一侧规范化所需的映射、规则与待配对的请求 |
| `normalize_event` | `(event: dict, context: NormalizeContext) -> dict` | 屏蔽并映射一条事件；must contain the line `event = mask_paths(event, EVENT_MASKS)`, and applies `MODEL_RESPONSE_MASKS` to `model_response` events |
| `align_events` | `(base: dict, head: dict, context: "CompareContext") -> tuple[dict, dict]` | 一对事件按启用的规则对齐头一侧记录形式（请求副本重建、新增字段移除），在 normalize_event 之前调用 |
| `align_manifests` | `(base: dict, head: dict, scope: str, rules: tuple[AppliedRule, ...]) -> tuple[dict, dict]` | 一对运行清单或导出清单按启用的规则对齐（结构号、lineage），在 normalize_manifest 与清单摘要之前调用 |
| `normalize_state` | `(state: dict, context: NormalizeContext) -> dict` | 一侧世界状态的身份映射与摘要映射 |
| `align_states` | `(base: dict, head: dict, rules: tuple[AppliedRule, ...]) -> tuple[dict, dict]` | 两侧规范化之前按启用的规则对齐记录形式：活动数组还原、已处理动作投影、结构号；读原始身份 |
| `normalize_checkpoint` | `(record: dict, context: NormalizeContext) -> dict` | 规范化检查点记录，摘要换成规范化摘要，状态交给 normalize_state |
| `normalize_manifest` | `(manifest: dict, scope: str, context: NormalizeContext) -> dict` | 规范化运行清单或导出清单 |
| `normalize_asset` | `(content: bytes, reference: dict, context: NormalizeContext) -> bytes` | 按素材来源规范化内容 |
| `require_allowed` | `(rule: Rule) -> None`; must contain `if (rule.scope, rule.path) not in ALLOWED_RULE_TARGETS:` | 规则只能落在允许的记录形式上 |
| `parse_rules` | `(texts: list[str]) -> tuple[AppliedRule, ...]` | 按名称启用规则并核对数值 |
| `apply_request_copy` | `(event: dict, requests: dict[str, dict], copy_expected: bool) -> dict`; must contain `data["request"] = request` | 用 caused_by 指向的请求重建响应里的请求副本 |
| `project_activities` | `(head_activities: list[dict]) -> dict[str, dict]` | 有序活动数组还原成以活动身份为键的对象 |
| `project_processed_actions` | `(base_state: dict, head_actions: dict) -> tuple[dict, list[str], int]`, returning the projected base map, the missing and extra keys (as `.processed_actions.<key>` difference paths) and the number of terminal overwrites; `base_actions = base_state["processed_actions"]`; must contain the lines `projected = {key: base_actions[key] for key in head_actions if key in base_actions}` and `missing = sorted(key for key in required if key not in head_actions)` | 基一侧只保留头一侧仍有的已处理动作，对齐终态覆盖，列出头一侧缺少或多留的条目 |
| `drop_replaced` | `(base: dict, head: dict) -> tuple[dict, dict]` | 基一侧按用途、头一侧按调用类的模型用量分列同时移除 |
| `apply_value_rule` | `(base_value: object, head_value: object, rule: AppliedRule) -> object`; must contain `if (base_value, head_value) != (rule.base_value, rule.head_value):` | 结构号只在声明的两值上对齐 |
| `drop_added` | `(head: dict, base: dict, paths: tuple[str, ...]) -> dict` | 基一侧没有的新增字段从头一侧移除 |
| `compare_events` | `(base: Side, head: Side, context: "CompareContext") -> dict` | 逐行流式比较两份 events.jsonl |
| `compare_states` | `(base: Side, head: Side, context: "CompareContext") -> dict` | 比较重启点、日终、终态与（可选）全部检查点 |
| `compare_exports` | `(base: Side, head: Side, context: "CompareContext") -> dict` | 比较导出的每个工件 |
| `CompareContext` | `@dataclass options: Options; ids: IdMaps; base: NormalizeContext; head: NormalizeContext` | 一次比较的共享上下文 |
| `compare` | `(base: Side, head: Side, options: Options) -> dict` | 库入口：返回报告，verdict 为 equal 或 different |
| `compare_event_data` | `(base_event: dict, head_event: dict) -> list[dict]` | events 子命令：两条事件 data 的字面差异 |
| `main` | `(argv: list[str] \| None = None) -> int` | 命令入口；0 相同、1 不同、2 输入错误 |

`IdMaps.normalize` body (the mutation anchor is `if ordinal is None:`):

```python
if value is None:
    return None
ordinal = self.ordinals[kind][side].get(value) if isinstance(value, str) else None
if ordinal is None:
    self.problems.append({"kind": "REFERENCE_NOT_AN_ID", "id_kind": kind, "side": side, "path": path, "value": value})
    return value
return f"<{kind}:{ordinal}>"
```

### `scripts/recorded_response_run.py` (new, about 750 lines)

Imports at module level: standard library only; it inserts its own repository root into `sys.path` (as
`scripts/freeze_rrs_baseline.py:13-14` does) and imports `from scripts.compare_runs import Chain, ComparisonError,
canonical_bytes`. It never imports `worldmock`.

| Name | Signature | Gist of the Chinese `@brief` |
| --- | --- | --- |
| `DUMMY_CREDENTIAL` | `str = "recorded-response-run"` | 子进程唯一可用的占位凭据 |
| `VISIBLE_FIELDS` | `tuple[str, ...] = ("model", "messages", "tools", "tool_choice", "max_tokens")` | 请求顶层的模型可见字段，另加 extra_body 的各键 |
| `REFUSAL_WATCHDOG_SECONDS` | `int = 600` | 拒绝后等子进程退出的上限 |
| `DIFF_LINE_LIMIT` | `int = 400` | 偏差差异文本行数上限 |
| `RecordedResponseError` | `class RecordedResponseError(RuntimeError)` | 录音应答运行无法进行或结论无效 |
| `RecordedCall` | `@dataclass(frozen=True) index: int; request_seq: int; response_seq: int; segment: int; occurred_at: int; purpose: str \| None` | 一次录音调用 |
| `Boundary` | `@dataclass(frozen=True) segment: int; checkpoint_seq: int; first_call: int; discarded_request: dict \| None; discarded_seq: int \| None; next_run_id: str` | 一次重启边界 |
| `Plan` | `@dataclass(frozen=True) chain: Chain; calls: tuple[RecordedCall, ...]; boundaries: tuple[Boundary, ...]; metadata: dict; api_key_env: str; continuation: bool` | 录音运行计划 |
| `load_plan` | `(sources: list[Path]) -> Plan` | 读来源链、调用与边界 |
| `write_input` | `(plan: Plan, destination: Path, port: int) -> None` | 从冻结素材复原输入并改写两个模型地址 |
| `rewrite_model_urls` | `(text: str, urls: tuple[str, str], port: int) -> str`; must contain `if tomllib.loads(rewritten) != expected:` | 只改 base_url 与 metadata_url，并核对其余不变 |
| `expected_body` | `(request: dict) -> dict` | 录音请求应有的 HTTP 正文 |
| `first_difference` | `(expected: dict, received: dict) -> dict \| None`; must contain the `fields = sorted(...)` line quoted under "Cassette state machine" | 首个不同之处的字段、层与差异文本 |
| `Cassette` | `class Cassette`; `__init__(self, plan: Plan, work: Path)`; `begin_segment(self, segment: int) -> None`; `answer(self, method: str, path: str, headers: email.message.Message, body: bytes) -> tuple[int, bytes]` (the handler passes its `http.client.HTTPMessage`; unit tests build an `email.message.Message`); `_refuse_restart(self, boundary: Boundary) -> tuple[int, bytes]`; `_pre_restart_expected(self, boundary: Boundary) -> dict`; attributes `next_call`, `segment`, `refused`, `divergences`, `refusal_at`, `models_requests` | 录音带状态机与请求日志 |
| `serve` | `(cassette: Cassette) -> ThreadingHTTPServer` | 在 127.0.0.1 起服务线程 |
| `as2_location` | `(root: Path, environment: Mapping[str, str]) -> list[Path]` | 在 root 下用给定环境起子进程，返回 agentsociety2 解析出的全部包目录；是否恰好一个且在 root 内由调用方判定并以各自的错误拒绝 |
| `_secret_name`, `_routing_name` | `(name: str) -> bool` | 凭据类变量名；会把模型流量或 Ray 工作进程引到别处的变量名 |
| `child_environment` | `(parent: Mapping[str, str], project: Path, api_key_env: str) -> dict[str, str]`; must contain the `variables = {...}` line quoted under "Child environment" | 子进程环境：去掉凭据、代理与外部 Ray 地址，只给占位凭据 |
| `judge_segment` | `(plan: Plan, cassette: Cassette, segment: int, exit_code: int) -> dict` | 只凭录音带状态与退出码判定一段的结局（计划内重启、提前完成、缺调用或错误） |
| `run_segment` | `(job: "SegmentJob") -> dict` | 起一段子进程、看守拒绝后的退出、交 judge_segment 判定并核对运行身份 |
| `SegmentJob` | `@dataclass(frozen=True) plan: Plan; cassette: Cassette; project: Path; work: Path; segment: int` | 一段的输入 |
| `finish` | `(plan: Plan, work: Path, project: Path, report: dict) -> None` | 导出、核验并写日表 |
| `raw_order` | `(base: Path, head: Path, report: Path) -> int` | 比较两次重放的原始请求正文 |
| `export_source` | `(project: Path, source: Path, cutoff: int, work: Path) -> int` | 不碰来源地导出来源某段 |
| `main` | `(argv: list[str] \| None = None) -> int` | 命令入口 |

The anchor lines inside `Cassette`:
- in `answer`: `if headers.get("Authorization") != f"Bearer {DUMMY_CREDENTIAL}":`;
- in `_refuse_restart`: `self.refused.add(boundary.segment)` (and no change of `next_call` anywhere in that method);
- in `_pre_restart_expected`, its whole body:

  ```python
  if boundary.discarded_request is not None:
      return boundary.discarded_request
  return self._recorded_request(boundary.first_call)
  ```

### `scripts/recorded_response_child.py` (new, about 50 lines)

Imports only the standard library at module level; it is run with `python -P` and imports the project's
`worldmock.cli` inside `main`.

| Name | Signature | Gist |
| --- | --- | --- |
| `RUN_IDENTITY_MODULES` | `dict[str, str] = {"run": "worldmock.cli", "resume": "worldmock.footprints.checkpoint"}` | 各模式下运行身份抽取所在模块 |
| `one_shot_uuid` | `(run_id: str, module: str) -> Callable[[], uuid.UUID]`; must contain `if drawn:` | 只能用一次的 uuid4 替身，第二次记错误并拒绝 |
| `main` | `(argv: list[str]) -> int` | 读段计划、替换抽取、调用被测检出的 CLI 并返回其退出码 |

### `scripts/compare_complete_days.py` (new, about 200 lines)

Imports `from scripts.compare_runs import Chain, ComparisonError, Options, Side, compare, parse_rules` and
`from scripts.recorded_response_run import as2_location` after inserting its repository root into `sys.path`
(`Chain.load` builds the one-segment chains of the two case directories).

| Name | Signature | Gist |
| --- | --- | --- |
| `KINDS` | `tuple[str, str] = ("activity", "callback")` | 两份完整日 |
| `require_comparable_roots` | `(base: Path, head: Path) -> None` | 两个检出必须同锁、各有 AS2 源码与录制入口 |
| `side_environment` | `(root: Path) -> dict[str, str]` | 一侧子进程只导入这一侧的代码 |
| `record_side` | `(root: Path, case_root: Path) -> dict` | 在一侧检出里录两份完整日并读回签名 |
| `differing_keys` | `(base: dict, head: dict) -> list[str]` | 两份签名里不同的项 |
| `main` | `(argv: list[str] \| None = None) -> int` | 命令入口；0 相等、1 不同、2 输入错误 |

### `tests/dayrun_baseline.py` (changed)

| Name | Today | Change |
| --- | --- | --- |
| `CODE_IDENTITY_FIELDS` | — | New: `("code_sha256", "git_commit", "git_dirty")`; `@brief` 行为签名只屏蔽的代码身份字段 |
| `CODE_IDENTITY_MASK` | — | New: `"<code-identity>"` |
| `DayBaseline.__init__` | `(self, case: Path, project: Path)` at `:56-62` | `(self, case: Path, project: Path, mask_code_identity: bool = False)`; stores `self.mask_code_identity` and `self.code_sha256 = None` |
| `DayBaseline._code_masked` | — | New `(self, manifest: dict) -> dict`: a copy with the three fields set to the mask when `mask_code_identity`; `@brief` 行为签名时屏蔽运行清单的代码身份，其余原样 |
| `DayBaseline._quality_content` | — | New `(self, raw: bytes) -> bytes`: parse, rename the `day_plan_metrics` key equal to `self.code_sha256` when masking, then `_json(self.normalize(...))` |
| `DayBaseline.prepare` | `:103-129` | Reads `self.code_sha256` from the manifest; the normalized manifest digest is computed from `self._code_masked(manifest)`; both raw digests (`_sha(manifest_raw)`, `_sha(_json(manifest))`) map to it as today |
| `DayBaseline.signature` | `:131-155` | `quality.json` goes through `_quality_content`; after normalizing the export manifest, `frozen_run_manifest` goes through `_code_masked` |
| `behaviour_signature` | — | New `(case: Path, project: Path) -> dict`: `DayBaseline(case, project, mask_code_identity=True).signature()`; `@brief` 只屏蔽代码身份的完整日行为签名 |
| `day_baseline_signature` | `:158-160` | Unchanged |

Two other chapters edit this file. Chapter 02 changes `prepare` at `:124-127` (it decodes compressed checkpoint rows).
The network chapter changes `:109` (manifest structure 6 → 7). This chapter changes `__init__` and `:119-121`, and adds
the new names. The lines differ, and the integrator merges them. This chapter's commits land first (see "Where it
runs").

### `tests/life/day_baseline_runtime.py` (changed)

`record_complete_day(monkeypatch, case, project, kind)` (`:99-121`) gains a fifth parameter
`signature: Callable[[Path, Path], dict] = day_baseline_signature`, used at `:119` in place of the direct call. All
current callers (`test_complete_day_baseline.py:21`, `test_complete_day_scripts.py:42`, `:136`) keep the default.

### `tests/life/behaviour_day.py` (new, about 50 lines)

- `import tests.conftest` first, for the process defaults it sets (`AGENTSOCIETY_LLM_API_KEY`,
  `AGENTSOCIETY_LLM_API_BASE`, `LITELLM_LOCAL_MODEL_COST_MAP`, `tests/conftest.py:23-25`) when the module runs outside
  pytest.
- `record(case_root: Path) -> dict`: for each kind, `with pytest.MonkeyPatch.context() as monkeypatch:` calls
  `record_complete_day(monkeypatch, case_root / kind, PROJECT, kind, behaviour_signature)`; returns `{kind: record}`.
- `main(argv: list[str] | None = None) -> int`: options `--case-root` and `--output`; writes canonical JSON plus a
  newline; returns 0.

### `.github/workflows/tests.yml` (changed)

New job `complete-day-behaviour`, `if: github.event_name == 'pull_request'`, `runs-on: ubuntu-latest`, steps:
1. checkout with the existing pinned action, `persist-credentials: false`, `fetch-depth: 0`;
2. the same setup-python, setup-node, apt and `python scripts/bootstrap.py --profile release` steps as the `tests` job;
3. `git worktree add --detach .work/complete-day-base "${{ github.event.pull_request.base.sha }}"`, then inside it
   `../../.venv/bin/python -c "from pathlib import Path; from scripts.bootstrap_sources import restore_as2; restore_as2(Path('.'))"`;
4. if `.work/complete-day-base/tests/life/behaviour_day.py` is missing, append
   `Base has no behaviour recorder; nothing to compare` to `$GITHUB_STEP_SUMMARY` and skip step 5;
5. `.venv/bin/python scripts/compare_complete_days.py --base-root .work/complete-day-base --head-root . --work
   .work/complete-day-behaviour`, appending its last stdout line to `$GITHUB_STEP_SUMMARY`;
6. upload `.work/complete-day-behaviour/` with the existing pinned upload action (`if: always()`, retention 30 days).

### Recorded formats

All JSON files below are canonical JSON (`sort_keys`, `separators=(",", ":")`, `ensure_ascii=False`) plus one `\n`;
JSONL files have one canonical object per line.

**`WORK/plan.json`**:
`{"api_key_env": str, "boundaries": [{"checkpoint_seq": int, "discarded_seq": int|null, "first_call": int, "next_run_id": str, "segment": int}], "calls": int, "continuation": bool, "port": int, "requested_model": str, "segments": [{"index": int, "run_dir": str, "run_id": str}], "timezone": str}`.

**`WORK/segments/<kk>.json`**: `{"argv": [str, ...], "mode": "run"|"resume", "run_id": str}`.

**`WORK/server/requests.jsonl`**, one line per HTTP request:
`{"arrival": int, "body_bytes": int, "body_sha256": str|null, "call": int|null, "method": str, "outcome": "served"|"planned_restart"|"divergence"|"extra_call"|"credential"|"unexpected_path"|"cassette_error"|"models", "path": str, "segment": int}`.
No wall clock. Raw bodies go to `WORK/server/bodies/<body_sha256>.json.gz` (`gzip.compress(body, mtime=0)`), written
once per distinct sha.

**`WORK/report.json`**:
```
{"calls_served": int, "control": null | {"boundary": int, "seq": int},
 "days": [{"calls": int, "date": "YYYY-MM-DD", "decisions": int, "engine_seconds": float,
           "engine_seconds_per_decision": float|null, "events": int}],
 "divergences": [{"body_sha256": str|null, "call_index": int|null, "diff": str, "fields": [str],
                  "kind": str, "layer": str|null, "matches_discarded_request": bool|null,
                  "message_index": int|null, "purpose": str|null, "segment": int,
                  "simulated_time": str|null, "source_seq": int|null}],
 "error": str|null,
 "export": null | {"check": "passed"|"failed", "cutoff": int, "dir": str, "error": str|null},
 "project": {"code_sha256": str|null, "commit": str|null, "dirty": bool|null, "root": str},
 "restarts_reproduced": int, "schema_version": 1,
 "segments": [{"ended_by": "completed"|"planned_restart"|"divergence"|"failure"|"killed", "exit_code": int,
               "first_call": int|null, "index": int, "last_call": int|null, "mode": "run"|"resume",
               "models_requests": int, "parent_checkpoint_id": str|null, "run_dir": str, "run_id": str,
               "wall_seconds": float}],
 "source": {"calls": int, "segments": [{"checkpoint_seq": int|null, "index": int, "run_dir": str, "run_id": str}],
            "timezone": str},
 "verdict": "zero_divergence"|"divergence"|"error"}
```
`project.commit` and `project.dirty` use `git -C PROJECT rev-parse HEAD` and `git status --porcelain
--untracked-files=normal`, as `run_setup._git_state` does (`run_setup.py:173-184`); null when git fails.

**`compare_runs` report**:
```
{"cutoff": int,
 "layers": {"assets": L, "day_end_states": L, "events": L, "every_checkpoint": L|null, "export": {"<artifact>": L},
            "final_state": L|null, "manifests": L, "restart_states": L},
 "masks": {"<record>:<path>": int}, "problems": [{"kind": str, ...}],
 "rules": {"<name>": {"applied": int, "values": "B:H"|null}}, "schema_version": 1,
 "verdict": "equal"|"different"}
```
with `L = {"compared": int, "different": int, "first": [{"key": str, "paths": [{"base": str, "head": str, "path": str}]}]}`,
where `key` is the event seq with its type and kind, the checkpoint seq, or the artifact row number.

**`WORK/complete-day-behaviour.json`**:
`{"base_root": str, "head_root": str, "kinds": {"activity": K, "callback": K}, "rules": [str], "verdict": "equal"|"equal_under_rules"|"different"}`
with `K = {"differing": [str], "equal": bool, "runs": <compare_runs report> | null}`.

## Design chapters

These edits come before the code, in Chinese. The 「运行比对」 classes name `lineage`, `transport_attempts` and
`by_call_class`, which the network and model-cost chapters add on the same integration branch. This chapter lands
first, so the comparator's masks and rules for those fields exist before the fields do; until then they match nothing.
Main receives all of it in one merge.

### `docs/design/01-architecture.md`「工程验证」

After the line ending 「…长期人物行为必须由对应真实运行证明，离线测试不能替代。」 (`:259`), add:

> 引擎优化指声明不改变人物经历、只改成本或记录形式的变更。证明它的工具有三项，都在 `scripts/`，不计入代码身份：
>
> | 证明 | 做什么 | 证明不了什么 |
> | --- | --- | --- |
> | 掩码完整日比较 `scripts/compare_complete_days.py` | 在基与头两个检出里各录活动日与回调日两份完整日，比较行为签名：与绑定代码身份的签名相同，只把运行清单与导出清单的 `code_sha256`、`git_commit`、`git_dirty` 换成占位、检查点里的 `manifest_sha256` 按屏蔽后的清单重算、质量报告里以代码摘要为键的那一项改用占位键。每个 PR 在 CI 里运行 | 引擎被直接驱动，不经 LifeAgent：看不见动态提示渲染，系统文本、层结构和工具定义只经清单里的两个指纹看到；只有一天、人工环境，没有模型证据和重启 |
> | 录音应答运行 `scripts/recorded_response_run.py` | 用一次真实运行自己录下的模型应答，在 127.0.0.1 上重跑新代码：每次请求的模型可见字段必须与录音逐值相等，任何偏差以 HTTP 400 拒绝并写偏差报告；在录音的检查点位置重现重启；各段沿用来源的运行身份，因为动作随机流以带运行身份的动作身份为键（[配置](08-configuration.md)「唯一种子与随机键」） | 只覆盖有录音的提示谱系与操作系统；录音没走过的路径（传输失败、停驻、整次截止）不被检验；工具定义的键序只在两次重放之间比较 |
> | 运行比对 `scripts/compare_runs.py` | 按[记录引擎](05-recording-engine.md)「运行比对」的字段分类比较两次运行的事件、清单、重启点与日终世界、检查点和每个导出工件 | 比的是存下的值，键序看不见 |
>
> 掩码完整日比较在每个 PR 的 CI 里不带规则运行；改了记录形式的变更在本地带声明的重建规则运行，结论为「在规则下相等」，报告附在 PR 里。
> 录音应答运行与运行比对需要同一提示谱系的录音；一项优化是否必须以录音应答运行零偏差为合入条件，由该次开发记录规定。
> 两份绑定代码身份的完整日基线照旧按双候选规程重冻，掩码比较与它们并存，不替代、不放宽它们。

### `docs/design/05-recording-engine.md`, new section「运行比对」at the end

> ## 运行比对
>
> 两次运行是否给了人物同样的经历按记录比对。字段分类只在这里定义，`scripts/compare_runs.py` 照此实现：
>
> - **身份**：事件的 `event_id`、`caused_by` 与工具里声明的引用字段，映射到所指事件的序号；检查点身份映射到它的 `checkpoint_created` 事件的序号；运行身份映射到段次序。映射是双射，引用指向别的事件仍然算不同。动作、活动、决策、订单等复合身份含运行身份，照原文比较：它们也是随机流的键，比对的两次运行必须使用同样的运行身份。
> - **壁钟**：只有事件顶层的 `recorded_at` 与 `wall_elapsed_ms`、人物工作区 AGENT.json 的 `initialized_at`、运行清单 `lineage` 各项的 `last_recorded_at` 与 `branched_at`、质量报告 `acceleration` 下的实测墙钟项、按记录时刻定峰谷的估价项与峰时计数、`lineage` 节的停机秒数。嵌套的同名字段是模拟时间（例如记忆层 `earlier_today[].recorded_at`），照常比较。
> - **传输记录**：`model_response` 信封里的 `transport_attempts`（失败发送的壁钟毫秒数与传输错误），不进任何提示。
> - **丢弃尾段**：`lineage` 各项描述父运行越过重启点走了多远、为何停下（`last_event_seq`、`last_event_time`、`discarded_events`、`end_reason`、`end_error`）及质量报告 `lineage` 节里对应的项。重放以拒绝重启点之后的第一次调用重现重启，来源那一段可能是被杀或传输失败，所以这几项在来源与重放之间本来就不同；丢弃尾段本身不比较。
> - **运行位置与代码身份**：运行清单与人物工作区里的模型服务地址（`base_url`、`metadata_url`），以及 `code_sha256`、`git_commit`、`git_dirty`。`config_sha256` 不屏蔽：去掉两项地址后按 config.toml 原文重算摘要，再比较。
> - **其余一律是事实**，逐值比较。
> - **记录形式**的变化只能用声明的重建规则对齐。规则只用于没有任何提示、决策、准入或导出路径读取的记录形式，每条写明所依据的读取者检索；观察、绑定、动作事件、结算事件、记忆写入、模型请求与世界事实不能重建、投影或过滤，也没有按事件种类过滤的规则。
>
> 模型证据是录音应答运行的录音带：`model_request.data.request` 与 `model_response.data.response` 完整保存提供商原文，含推理，不得裁剪。

### `docs/design/10-runtime.md`「原生模型与工具」

After the paragraph ending at `:54`, add:

> 模型服务也可以是开发工具在本机起的录音应答服务（`scripts/recorded_response_run.py`）。它从运行冻结的输入素材复原一份输入，只把 config.toml 的 `base_url` 与 `metadata_url` 改到 127.0.0.1，凭据变量给占位值，真实密钥不进入子进程；运行代码不为它留任何入口或开关。

## Tests

### New unit tests (standard library plus pytest; no Ray, no model)

`tests/unit/engineering/test_recorded_response_run.py` imports `scripts.recorded_response_run` and
`scripts.recorded_response_child` by module name (the mutation runner injects by module name,
`tests/mutation_runner.py:47-62`). Fixtures are built in `tmp_path`: a minimal source chain with a `footprints.sqlite`
holding the `events` table (schema subset of `store.py:23`) and hand-made request/response bodies, manifests,
`restored-checkpoint.json` and an `assets/` directory.

| Test | Asserts |
| --- | --- |
| `test_expected_body_carries_exactly_the_visible_fields` | A recorded request with `extra_body.thinking`, `max_retries`, `transport_max_retries` and `stream` gives exactly `{model, messages, tools, tool_choice, max_tokens, thinking}`; a memory request without tools gives four keys |
| `test_equal_values_in_another_key_order_match` | A received body with the same values in another key order gives `first_difference(...) is None` |
| `test_one_character_in_the_system_layer_is_a_divergence_named_system` | `layer == "system"`, `message_index == 0`, the diff contains the changed sentence on a `-` and a `+` line |
| `test_a_changed_user_layer_is_named_by_its_layer` | Two user contents (JSON arrays of layers) that differ in the second layer give `layer == "人设层"` |
| `test_a_changed_tool_schema_is_a_divergence_named_tools` | One description changed in `tools[3]` gives `fields == ["tools"]` and `layer == "tools:<name>"` |
| `test_an_extra_request_field_is_a_divergence` | A received `"stream": true` gives `fields == ["stream"]` |
| `test_cassette_serves_recorded_responses_in_order` | `Cassette.answer` returns 200 with exactly `json.dumps(recorded_response, ensure_ascii=False).encode()` for calls 1 and 2, and `next_call == 3` |
| `test_a_call_after_the_last_is_refused_as_extra` | A request after the last call returns 400 with code `extra_call` and a divergence of that kind |
| `test_planned_restart_refuses_once_without_advancing` | At boundary 1 the first matching request returns 400 `planned_restart`, `next_call` unchanged; after `begin_segment(2)` the same request returns 200 and `next_call` advances by one |
| `test_a_second_request_after_the_planned_refusal_is_a_divergence` | After the planned refusal and before `begin_segment(2)`, a request returns 400 and records a divergence of kind `after_refusal` |
| `test_requests_after_a_divergence_add_no_record` | After a `mismatch`, two more requests return 400 `after_divergence`; `divergences` still holds one record, and `requests.jsonl` has three lines with outcome `divergence` |
| `test_a_cassette_failure_is_answered_with_400_and_an_error_verdict` | With `Cassette._recorded_request` monkeypatched to raise `KeyError`, `answer` returns 400 `cassette_error`, records that divergence kind, and `caplog` has the `Recorded-response cassette failed` line |
| `test_the_credential_header_is_read_without_regard_to_case` | An `email.message.Message` carrying `authorization: Bearer recorded-response-run` is served |
| `test_pre_restart_call_is_matched_against_the_discarded_request` | With `D_1` different from the redone call: a request equal to `D_1` is refused as planned; a request equal to the redone call before the restart is a `pre_restart_mismatch` |
| `test_first_call_after_a_restart_reports_whether_it_equals_the_discarded_request` | After the restart, a request equal to `D_1` but not to the redone call is a divergence with `matches_discarded_request is True` |
| `test_a_credential_other_than_the_dummy_is_refused_and_not_logged` | `Authorization: Bearer sk-real` returns 400 `credential`; neither `requests.jsonl` nor the report contains `sk-real` |
| `test_models_endpoint_serves_the_frozen_metadata` | The `/models` answer passed to `worldmock.model_endpoint.parse_model_metadata` with the rewritten config gives the frozen `context_window` and `max_output_tokens` |
| `test_unexpected_path_is_refused` | `POST /v1/embeddings` returns 400 `unexpected_path` |
| `test_input_copy_rewrites_only_the_two_model_urls` | `write_input` writes every `environment_input` asset; `config.toml` differs from the source only on the two URL lines, uses LF and parses to the source document with only the two URLs replaced |
| `test_url_rewrite_refuses_a_url_that_is_not_unique` | The quoted base URL appearing twice raises `RecordedResponseError` with the "exactly once" message |
| `test_url_rewrite_refuses_a_config_whose_url_is_written_differently` | A config whose real `metadata_url` uses single quotes, while the double-quoted value appears once in a comment, raises the "beyond the two model URLs" error |
| `test_input_copy_refuses_a_changed_frozen_asset` | One byte of `persona.toml`'s asset changed: `write_input` raises the "does not match its recorded digest" error and writes nothing under `input/` |
| `test_frozen_config_must_agree_with_the_manifest_model_identity` | A manifest whose `stage_model_identity.base_url` differs from the frozen config raises the "disagrees with the manifest model identity" error |
| `test_child_environment_drops_credentials_and_proxies` | From a parent with `DEEPSEEK_NODE_KEYS`, `OPENAI_API_KEY`, `GH_TOKEN`, `HTTPS_PROXY`, `ray_address` and `PYTHONPATH=x`: only `api_key_env` is present with the dummy value; no proxy or Ray address variables; `NO_PROXY`, `PYTHONUTF8=1` and `LITELLM_LOCAL_MODEL_COST_MAP=True` set; `PYTHONPATH` lists the three project paths only |
| `test_project_without_restored_as2_is_refused` | `main(["run", "--project", <tmp without vendor/agentsociety2>, …])` returns 2, and `caplog` has the "no restored AS2 source" line |
| `test_as2_location_follows_the_child_pythonpath` | With a `tmp_path/agentsociety2/__init__.py` and `PYTHONPATH=tmp_path`, `as2_location(tmp_path, environment)` returns `[tmp_path / "agentsociety2"]` |
| `test_judge_segment_outcomes` (parametrized) | With hand-set cassette states: a planned refusal and exit 1 gives `planned_restart`; exit 0 before the boundary gives the divergence `segment_completed_early`; the last segment with exit 0 and `next_call ≤ T` gives `missing_calls` at `next_call`; exit 1 without refusal or divergence gives verdict `error` with the "failed before its planned restart" message |
| `test_chain_plan_from_a_restarted_source` | Two synthetic segments: boundary at `L_1`, `first_call(2)` correct, calls assigned to segments 1 and 2, `D_1` read from segment 1's tail |
| `test_chain_plan_rejects_a_missing_segment_directory` | Passing only the final directory of a two-segment chain raises `ComparisonError` naming segment 1 |
| `test_recorded_call_without_provider_response_is_refused` | A request without a terminal response raises `RecordedResponseError` with its call index and seq |
| `test_one_shot_uuid_returns_the_run_id_once` | `one_shot_uuid(run_id, "m")()` returns `UUID(hex=run_id)`; a second call raises `RuntimeError` |
| `test_raw_order_detects_a_key_order_difference` | Two work directories whose bodies for call 3 differ only in the key order of one tool's `function` object: `raw_order` returns 1 and reports call 3 with that path |

`tests/unit/engineering/test_compare_runs.py` imports `scripts.compare_runs`. A private helper `_run(root, events,
checkpoints, manifest, assets)` writes a minimal run directory and export (`events.jsonl`, `manifest.json` with
artifact digests, the other artifacts empty, `quality.json`).

| Test | Asserts |
| --- | --- |
| `test_identical_runs_compare_equal` | Same files on both sides: verdict `equal`, exit 0 |
| `test_event_and_checkpoint_ids_and_wall_clock_are_mapped_or_masked` | The head with fresh event and checkpoint ids, other `recorded_at` and `wall_elapsed_ms`: `equal` |
| `test_nested_recorded_at_is_compared` | A different `data.memory_layer.earlier_today[0].recorded_at`: `different`, the path `.data.memory_layer.earlier_today[0].recorded_at` reported |
| `test_a_reference_to_another_event_is_a_difference` | The head's `caused_by` names a different earlier event: `different` |
| `test_a_reference_to_an_unknown_event_is_a_problem` | The head's `caused_by` names an id that is no event: problem `REFERENCE_NOT_AN_ID` recorded |
| `test_ids_inside_free_text_are_compared_literally` | An event id inside a summary string differs: `different` |
| `test_compound_ids_require_equal_run_ids` | A `decision_id` with another run prefix: `different` |
| `test_an_amount_change_is_a_difference` | A changed `resource_changes` amount: `different` |
| `test_code_identity_and_model_urls_are_masked_only_at_their_paths` | Different manifest `code_sha256` and URLs: `equal`; the same URL string placed in an event field: `different` |
| `test_config_digest_is_mapped_through_url_normalized_content` | Configs differing only in the two URLs: `equal`; also differing in `seed`: `different` |
| `test_agent_json_initialized_at_is_masked_inside_workspace_snapshots` | Snapshot tars that differ only in `AGENT.json` `initialized_at` and `profile.model_identity.base_url`: `equal`; differing in `MEMORY.md`: `different` |
| `test_request_copy_rule_rebuilds_the_response_request` | The base response with the request copy, the head with `null`: `equal` with `--rule request-copy`, `different` without it |
| `test_a_wrong_request_copy_is_a_difference` | The base's copy differs from the request its `caused_by` names: `different` with the rule |
| `test_a_rule_on_decision_inputs_is_refused` | `require_allowed(Rule(..., scope="event:observation", path="data.activity_bindings", ...))` raises `ComparisonError` |
| `test_checkpoint_zlib_rule_requires_the_record_hash_of_the_decoded_payload` | A compressed head row with the right `record_sha256`: `equal`; a wrong one: `ComparisonError` |
| `test_activities_array_rule_requires_unique_ids_and_equal_entries` | Array form equal to the base object: `equal`; a repeated id: `ComparisonError`; one changed entry: `different` |
| `test_processed_actions_rule_rejects_a_changed_entry` | The head keeps a subset, one entry changed: `different`; an unchanged subset: `equal` |
| `test_processed_actions_rule_accepts_only_terminal_overwrites` | Base: an origin whose activity is `completed` holds the terminal result; head: the same key holds the start result. `equal`, with `terminal_overwrites` 1. The same pair with the base activity still `active`: `different`. The same pair with a changed `request_sha256`: `different` |
| `test_processed_actions_rule_requires_every_readable_entry` | The head drops a key whose base activity is `paused`: `different` at `.processed_actions.<key>`. The head drops a base key whose result settled at `current_time`: `different`. The head drops an origin of an activity that ended before `current_time`: `equal`. The head keeps that origin instead: `different` (kept too long) |
| `test_head_activity_array_is_projected_before_ids_are_mapped` | The head stores activities as an array whose entries carry fresh `origin_request_event_id` values pointing at the same seqs: `equal` with `--rule activities-array`; the ids would be left unmapped if the projection ran after normalization |
| `test_manifest_rules_apply_before_manifest_digests` | The head manifest has `schema_version` 7 and `lineage`, and every head checkpoint's `manifest_sha256` is that manifest's sha: `equal` with `run-manifest-version=6:7` and `lineage`; the checkpoint layer reports no difference |
| `test_workspace_gzip_rule_compares_the_tar_inside_and_its_media_type` | Head snapshots are `gzip.compress(tar, mtime=0)` with `media_type` `application/gzip`: `equal` with `--rule workspace-gzip`, `different` without it; a head reference with `media_type` `application/zip`: `different` |
| `test_transport_attempts_are_dropped_or_masked` | Head responses carry `transport_attempts: []`, base responses lack the key: `equal` with `--rule transport-attempts`, `different` without it. Both carry attempts with different `failed_after_ms`: `equal` without the rule |
| `test_lineage_compares_restart_fields_and_masks_the_tail` | Both manifests carry `lineage`: different `branched_at`, `end_error` and `discarded_events`: `equal`; a different `checkpoint_event_seq`: `different` |
| `test_cost_by_class_rule_drops_both_usage_sections_only_together` | Base `quality.json` with `model_usage.by_purpose`, head with `model_usage.by_call_class`: `equal` with `--rule cost-by-class`, `different` without it; the head also keeping `by_purpose` with other counts: `different` with the rule |
| `test_unknown_and_incomplete_rules_are_refused` | `--rule nonsense` and `--rule world-schema-version` (no values) each raise `ComparisonError` with their messages |
| `test_an_asset_that_does_not_match_its_reference_is_refused` | One byte of a head asset changed: `ComparisonError` with the "does not match its reference" message |
| `test_chains_of_different_length_are_different` | A two-segment base against a one-segment head: problem `SEGMENT_COUNT`, verdict `different` |
| `test_version_rule_accepts_only_the_declared_values` | `world-schema-version=14:15` with base 14 and head 15: `equal`; head 16: `different` |
| `test_added_field_rule_drops_only_its_path_on_head` | Head manifest with `lineage`, base without: `equal` with `--rule lineage`; the same head with a changed `persona_habit_responses` as well: `different` |
| `test_leaf_diff_uses_obsdiff_paths` | Two observations whose targets 1 and 2 are swapped report exactly the expected `.tool_layer.targets[i]…` and `#len` paths |
| `test_quality_wall_fields_and_code_key_are_masked` | Different `acceleration.speedup`, estimated cost and the `day_plan_metrics` code key: `equal`; a different `actions.request_events`: `different` |
| `test_export_must_cover_the_database_prefix` | An export missing the last event raises `ComparisonError` with the "does not cover" message |
| `test_day_end_and_restart_states_are_compared` | A changed field in a day-end checkpoint state and in `restored-checkpoint.json`: both reported in their layers |
| `test_every_checkpoint_mode_compares_intermediate_states` | A change in a checkpoint that is neither day end nor restart is reported only with `--every-checkpoint` |

`tests/unit/engineering/test_compare_complete_days.py` (child recording replaced by writing prepared JSON files
through a monkeypatched `record_side`):

| Test | Asserts |
| --- | --- |
| `test_equal_signatures_give_equal` | Exit 0, verdict `equal` |
| `test_different_signatures_without_rules_give_different` | Exit 1, the differing artifact named |
| `test_dependency_lock_mismatch_is_refused` | Exit 2 with the "different dependency locks" message |
| `test_root_without_recorder_is_refused` | Exit 2 with the "no behaviour recorder" message |
| `test_root_whose_as2_resolves_elsewhere_is_refused` | `as2_location` monkeypatched to return a path outside the root: exit 2 with the "imports AS2 from" message |
| `test_different_signatures_equal_under_rules` | Signatures differ, `--rule checkpoint-zlib` given, `compare` monkeypatched to return verdict `equal` for both kinds: exit 0, verdict `equal_under_rules`; `compare` returning `different` for one kind: exit 1 with that report attached |

`tests/unit/engineering/test_dayrun_baseline.py` gains (its `_case` helper, `:24-47`, gains `code_sha256`,
`git_commit`, `git_dirty` in the manifest and a `quality.json` with a `day_plan_metrics` entry keyed by that code
sha):

| Test | Asserts |
| --- | --- |
| `test_behaviour_signature_ignores_only_code_identity` | Two cases differing only in `code_sha256` (manifest, frozen manifest, checkpoint `manifest_sha256` consistent, quality key) and `git_commit`: behaviour signatures equal, code-bound signatures differ |
| `test_behaviour_signature_detects_fingerprint_and_business_changes` (parametrized `prompt_sha256`, `tool_schema_sha256`, `amount`) | Each change makes the behaviour signatures differ |
| `test_behaviour_signature_keeps_every_other_manifest_field` | `DayBaseline(..., mask_code_identity=True)._code_masked(m)` changes only the three fields |

### New system tests

`tests/system/lifecycle/test_recorded_response_run.py`:
- Builds a two-segment source in `tmp_path` with the real CLI (subprocesses, as `tests/system/cli/test_cli.py:21-25`
  does) against a scripted local model server defined in the test module:
  - environment: a copy of `tests/fixtures/dataset-environment` with `stop_time` twenty minutes after `start_time`
    (2026-01-05 08:00–08:20, written with `tomlkit` as `tests/integration/runtime/test_native_dataset_lifecycle.py:50-55`
    does) and the model URLs pointed at the server (as `test_cli.py:28-60` does);
  - `GET /v1/models`: the test-model metadata; `POST /v1/chat/completions`: a complete chat completion (`id`,
    `object`, `created`, `model`, `choices[0].message` with one tool call, `usage` with cache fields) chosen by request
    kind. The kind is read from the tool names: `submit_day_plan` is plan_day, `start_main_activity` is a decision.
    - plan_day: `submit_day_plan` with `{"goals": []}` (the arguments of `tests/life/conftest.py:142-151`);
    - decisions, in order: `start_main_activity` with `main_arguments("interact", "家", "测试闲聊")`
      (`tests/life/conftest.py:251-253`), then `conclude_activity` with
      `{"items": [{"activity_name": "进行测试闲聊（1月5日 08:00 起）", "summary": "聊完了。", "memories": [], "views": []}],
      "errands": []}`, then the same `start_main_activity` again. This is the activity and conclusion of
      `test_native_dataset_lifecycle.py:28-38`. The unit lasts 600 s and draws no random number (`docs/design/08-configuration.md`,
      content selection: a named unit at a place draws none). A sleep is not used, because its oversleep draw is keyed by
      the action id, which carries the fresh run id, so the source's call count would change from run to run;
    - any other request kind or an exhausted script fails the test with
      `Scripted model got an unexpected {kind} request`;
  - segment 1: `worldmock run` with the server refusing the second decision request once (HTTP 400); segment 2:
    `worldmock resume` from the last `checkpoint_created` of segment 1, with the server answering from then on.
  - The source must show: segment 1 failed, segment 2 completed, one `branch_created`, no `protocol_feedback`, and the
    scripted number of calls. If the host's call sequence changes, the script is updated here; the assertions keep it
    honest.
- `test_recorded_response_run_reproduces_a_restarted_recording`: the tool on that source → exit 0, verdict
  `zero_divergence`, `calls_served` equal to the source's calls, `restarts_reproduced == 1`, segment 2's `run_id`
  equal to the source's, export check passed; `compare_runs.py runs` of the source (exported by the test with
  `worldmock export`) against the replay → exit 0; `raw-order` of the replay against itself → exit 0.
- `test_one_changed_system_character_stops_the_run_at_that_call`: a copy of the source in which the test drops the
  copy's `events_no_update` trigger and changes one character of `messages[0].content` of the first decision request
  in segment 2's database → exit 1; `divergences[0]` has `kind == "mismatch"`, `segment == 1`,
  `layer == "system"`, `source_seq` equal to that event's seq and `simulated_time == "2026-01-05T08:00:00+08:00"`.

`tests/system/lifecycle/test_complete_day_behaviour.py`, marked `day_run`:
- `test_complete_day_behaviour_is_equal_for_one_checkout`: `compare_complete_days.py --base-root ROOT --head-root ROOT`
  → exit 0, verdict `equal`. This proves the recording plus the behaviour signature is deterministic across two
  processes. The test then runs `compare_runs.compare` on the two recorded case directories of each kind, with no rule,
  and requires `equal`. That shows the comparator's masks and maps are enough for complete-day data, and that no
  machine path leaks into a recorded value (the two case directories differ in path, and the signature replaces paths,
  `tests/dayrun_baseline.py:69`, where the comparator does not).

### Byte-identity proof for this chapter

- `git diff --stat <parent> -- src custom patches pyproject.toml uv.lock requirements scripts/render_day.py` is empty
  for every commit of this chapter (`vendor/agentsociety2` is restored from `patches/`, `.gitignore`). So
  `runtime_code_sha256`, the export's timelines, and every run's prompts, decision inputs and settled facts are
  unchanged.
- `tests/system/lifecycle/test_complete_day_baseline.py` passes against the unchanged code-bound fixtures. They include
  `code_sha256`, so an unchanged code identity is part of what they check. They also go through the changed
  `tests/dayrun_baseline.py` with `mask_code_identity=False`, so they show the code-bound signature is unchanged. This
  holds on the commits of this chapter, which land before chapter 02 makes that test fail until the final re-freeze.
- The tools' own correctness is shown by the unit tests above and the acceptance runs below.

### Existing tests that change

- `tests/unit/engineering/test_dayrun_baseline.py`: `_case` gains the code identity fields and a `quality.json`; the
  three new tests above. The existing tests keep their assertions.
- No other existing test changes. `test_complete_day_baseline.py` and `test_complete_day_scripts.py` keep calling
  `record_complete_day` with the default signature.

### Source mutations (register in `tests/dataset_acceptance.py`)

Append a block `# ---- 证明工具（长时运行成本第八章）----` with:

| Name | Symbol | Original | Replacement | Killer test ids |
| --- | --- | --- | --- | --- |
| `cassette_compares_messages_only` | `scripts.recorded_response_run::first_difference` | `fields = sorted(key for key in set(expected) \| set(received) if _differs(expected, received, key))` | `fields = sorted(key for key in {"messages"} if _differs(expected, received, key))` | `tests/unit/engineering/test_recorded_response_run.py::test_a_changed_tool_schema_is_a_divergence_named_tools`, `::test_an_extra_request_field_is_a_divergence` |
| `restart_refusal_advances_the_cassette` | `scripts.recorded_response_run::Cassette._refuse_restart` | `self.refused.add(boundary.segment)` | `self.refused.add(boundary.segment); self.next_call += 1` | `…::test_planned_restart_refuses_once_without_advancing` |
| `pre_restart_call_matched_against_the_redone_call` | `scripts.recorded_response_run::Cassette._pre_restart_expected` | `if boundary.discarded_request is not None:` | `if False:` | `…::test_pre_restart_call_is_matched_against_the_discarded_request` |
| `dummy_credential_not_checked` | `scripts.recorded_response_run::Cassette.answer` | `if headers.get("Authorization") != f"Bearer {DUMMY_CREDENTIAL}":` | `if False:` | `…::test_a_credential_other_than_the_dummy_is_refused_and_not_logged` |
| `child_keeps_real_credentials` | `scripts.recorded_response_run::child_environment` | `variables = {name: value for name, value in parent.items() if not _secret_name(name) and not _routing_name(name)}` | `variables = dict(parent)` | `…::test_child_environment_drops_credentials_and_proxies` |
| `url_rewrite_unchecked` | `scripts.recorded_response_run::rewrite_model_urls` | `if tomllib.loads(rewritten) != expected:` | `if False:` | `…::test_url_rewrite_refuses_a_config_whose_url_is_written_differently` |
| `run_identity_drawn_twice` | `scripts.recorded_response_child::one_shot_uuid` | `if drawn:` | `if False:` | `…::test_one_shot_uuid_returns_the_run_id_once` |
| `wall_clock_masked_by_key_name` | `scripts.compare_runs::mask_paths` | `if path in paths:` | `if path.rsplit(".", 1)[-1] in {item.rsplit(".", 1)[-1] for item in paths}:` | `tests/unit/engineering/test_compare_runs.py::test_nested_recorded_at_is_compared` |
| `event_wall_clock_not_masked` | `scripts.compare_runs::normalize_event` | `event = mask_paths(event, EVENT_MASKS)` | `event = dict(event)` | `…::test_event_and_checkpoint_ids_and_wall_clock_are_mapped_or_masked` |
| `unknown_reference_not_reported` | `scripts.compare_runs::IdMaps.normalize` | `if ordinal is None:` | `if False:` | `…::test_a_reference_to_an_unknown_event_is_a_problem` |
| `request_copy_not_rebuilt` | `scripts.compare_runs::apply_request_copy` | `data["request"] = request` | `data["request"] = None` | `…::test_request_copy_rule_rebuilds_the_response_request` |
| `rule_on_decision_inputs_accepted` | `scripts.compare_runs::require_allowed` | `if (rule.scope, rule.path) not in ALLOWED_RULE_TARGETS:` | `if False:` | `…::test_a_rule_on_decision_inputs_is_refused` |
| `pruned_actions_projection_copies_the_head` | `scripts.compare_runs::project_processed_actions` | `projected = {key: base_actions[key] for key in head_actions if key in base_actions}` | `projected = dict(head_actions)` | `…::test_processed_actions_rule_rejects_a_changed_entry` |
| `pruned_actions_required_keys_unchecked` | `scripts.compare_runs::project_processed_actions` | `missing = sorted(key for key in required if key not in head_actions)` | `missing = []` | `…::test_processed_actions_rule_requires_every_readable_entry` |
| `version_rule_accepts_any_value` | `scripts.compare_runs::apply_value_rule` | `if (base_value, head_value) != (rule.base_value, rule.head_value):` | `if False:` | `…::test_version_rule_accepts_only_the_declared_values` |

Each original snippet occurs exactly once in its symbol (the runner requires a unique anchor). All killers are unit
tests and run in seconds, inside the 480 s unmutated killer baseline
(`tests/system/mutations/test_dataset_acceptance.py:11-17`).

### Acceptance runs (not part of pytest; receipts in `.work/long-run-cost/verify/`)

Steps 1–8 of "Backport verification and determinism control", in that order. The integration branch merges only when
all of these hold:
- steps 1, 2, 2b, 3, 4, 5, 5b, 6, 6b, 6c, 7 and 7b give the required results;
- the cumulative masked complete-day comparison gives `equal_under_rules`. Its base is chapter 02's test-identity
  commit and its head is the integration head, with the rule set `R_V2` in which `export-manifest-version` is `7:8`
  (main's export schema is 7). See "Where it runs";
- step 8 passes.

## Files touched

- `scripts/recorded_response_run.py` (new)
- `scripts/recorded_response_child.py` (new)
- `scripts/compare_runs.py` (new)
- `scripts/compare_complete_days.py` (new)
- `tests/dayrun_baseline.py` (changed; chapter 02 also edits `DayBaseline.prepare`)
- `tests/life/day_baseline_runtime.py` (changed)
- `tests/life/behaviour_day.py` (new)
- `tests/unit/engineering/test_dayrun_baseline.py` (changed)
- `tests/unit/engineering/test_recorded_response_run.py` (new)
- `tests/unit/engineering/test_compare_runs.py` (new)
- `tests/unit/engineering/test_compare_complete_days.py` (new)
- `tests/system/lifecycle/test_recorded_response_run.py` (new)
- `tests/system/lifecycle/test_complete_day_behaviour.py` (new)
- `tests/dataset_acceptance.py` (changed: 15 mutations)
- `.github/workflows/tests.yml` (changed: job `complete-day-behaviour`)
- `docs/design/01-architecture.md` (「工程验证」)
- `docs/design/05-recording-engine.md` (new section「运行比对」)
- `docs/design/10-runtime.md` (「原生模型与工具」)
- `.work/long-run-cost/bench/scripted_calendar.py`, `hot_paths.py`, `compare_bench.py` (untracked)
- `.work/long-run-cost/verify/` (untracked receipts)

## Not in this chapter

| Item | Where it lives |
| --- | --- |
| The activity-order fix, processed-action pruning, compressed checkpoint rows and workspace snapshots, and their own tests (including restore-equals-live at every step of a scripted day) | Chapter 02. This chapter only supplies their comparator rules and the V1/V2 split |
| `request: null` and observation parts | Chapter 03; this chapter supplies the `request-copy` rule |
| Streaming export, branch by SQL copy, short reads and the busy timeout (the rollback journal stays) | Whole-history chapter; the comparator reads the exports it writes |
| Parking, the whole-call deadline, frozen metadata on resume, lineage, heartbeat | Network chapter; this chapter supplies the `lineage` and `transport-attempts` rules, the masks that chapter hands over, and records `/models` requests per segment |
| `model_usage.by_call_class` in `quality.json` | Model-cost chapter; this chapter supplies the `cost-by-class` rule and the peak-band masks |
| Re-freezing the two complete-day baselines and the viewer sample | Integration, once at the end (proposal "Versions") |
| The reference recording on main's prompt lineage | The next real run on main (dataset #11 acceptance, run past day 20). Until it exists, main-lineage changes are proven by the masked complete-day comparison and the backport |
| A scripted persona through the real host (self-recorded cassette) | Deferred by the review; the proposal does not specify it |
| A protocol of realism metrics for changes that fail the gate, and model-backed acceptance of prompt changes (context compaction, user-layer order) | Not specified by the proposal; "Awaiting the owner" lists the user-layer order |
| Whether a zero-divergence replay is a merge blocker for every future optimization, the timing threshold beyond the asv default, and whether counters may only go down | Not decided by the proposal; this chapter applies the proposal's acceptance list to this specification only |
| Cross-platform replays (issue #5) | Out of scope; replays run on the recording's operating system |
| Tool parameters, host validation of tool arguments, run-command CLI options, environment dataset fields | Untouched (proposal "Awaiting the owner") |
