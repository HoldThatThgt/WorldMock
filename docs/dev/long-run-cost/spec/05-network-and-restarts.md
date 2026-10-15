# Network failures, restarts and run status

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements the proposal's "Network and restarts" section (`PROPOSAL-long-run-cost.md:256-304`): park on
transport failure, the whole-call deadline, frozen model metadata on resume, readable failures (picklable errors, root
cause in status, heartbeat, issue #7), lineage, and the "Quieter logs" items that belong to the network path (closing
LiteLLM's cached clients, ops records). It applies these "Decisions" rows: "Park budget" (12 hours, engine constant, 429
parks too) and "Resume capacity check" (frozen metadata on resume, fresh runs still check live). It applies the
reviewer's binding revisions from `.work/run30/opt/review_resilience.json`: attempts in the event envelope, not in
`data.response`; constructor logging of fault classes kept; `source_run_ids` kept as the sorted set; the lineage report
outside `transport_or_terminal_failures`; killed parents end with `no terminal event`; the heartbeat thread survives
write failures and ships with the issue #7 retry; status reports the last-event age; cached clients are evicted as well
as closed.

### Measured cost

All figures are from the 30-day run (engine `d27f2c5a`) and were checked against `main` `0bd2de65`.

| Fact | Evidence |
| --- | --- |
| 3 of 4 stops were one decision call whose 4 sends failed within 34.4–35.3 s. The real error was `ClientConnectorError → httpx.ConnectError → APIConnectionError`, relabelled `InternalServerError`; only the type name reached `run_failed` and `status.json` | [O] segment logs, e.g. `20261014T092059Z-…-resume/logs/resume.log:991-1188`; `llm_dispatcher.py:499-511` [M] |
| The retry count cannot be raised mid-chain: it is frozen in `config_sha256`, `ModelIdentity` (`run_setup.py:88`), the stage identity (`run_setup.py:97`) and the agent profile checked at `runtime.py:410` | [M] |
| Downtime at the three transport boundaries: 470.3, 2,124.2 and 251.5 s; cold start to first request 64.7 → 111.2 → 214.3 s | [O] |
| The three transport resumes re-copied 1,645.5 MB of event bodies (about 3.1 GB of pages) | [R] `.work/run30/scratch/opt-resilience/copy_bytes.txt` |
| 13 resume attempts failed; 8 died on the live `/models` fetch (TLS), which has no retry (`model_endpoint.py:35`) and runs twice per resume (driver `runtime.py:239`; actor `life_env.py:87` → `world_setup.py:156`). One left a 753 MiB orphan branch | [O] |
| `EnvironmentInputError`, `PreconditionError` and `RegistrationError` do not survive unpickling, so the actor's metadata failure reached `status.json` as `UnserializableException` | [R] `opt-resilience/pickle_errors.txt`; [O] `20261014T182136Z-…-resume2/run/status.json` |
| The 300 s request timeout (`run_setup.py:48`) is per read, not per call: seq 29544 took 605.6 s and returned one token, which became a `TOOL_CALL_REQUIRED` correction turn the persona then read in its progress layer for the rest of the evening | [R] `opt-resilience/timeout_repro.txt`, [O] `opt-resilience/stall_29544.txt`, `opt-review-resilience/stall_check.txt` |
| Every real answer took ≤ 114.9 s, including the 47 calls that hit the 16,000-token cap; 2 of 3,720 calls ran past 240 s (605.6 s and 357.6 s) | [R] `opt-resilience/latency.txt` |
| A killed run stays `{running, deciding}` forever and `elapsed_seconds` keeps growing | [O][R] |
| 5,065 ERROR lines are aiohttp "Unclosed client session/connector", about one per decision round | [O]; [R] `opt-resilience/unclosed_repro.txt` |
| quality.json of the final certified export reports `run_failed_events: []` and `transport_or_terminal_failures: 0`; `source_run_ids` is sorted, not ordered; the parents' end reasons live only in the parent databases | [O]; `checkpoint.py:125`, `export.py:156` [M] |

### Expected saving

- **Parking.** In this run it would have removed 3 of 4 stops, all 13 failed resume attempts, the 753 MiB orphan,
  1,645.5 MB of re-copied bodies and about 18–54 minutes of downtime (the reviewer's corrected range: the outage lengths
  at boundaries 2 and 4 are unknown) [O][R]. Projected restarts from network outages shorter than 12 hours: about
  2 / 5 / 9 in 30 / 60 / 90-day runs today, 0 after.
- **Frozen metadata on resume.** Two network round trips per resume, and kill, crash and replay resumes no longer depend
  on the network. At the transport boundaries this overlaps with parking.
- **Deadline.** Replaces the 605.6 s one-token stall with a fresh answer to the same request, about 6 minutes earlier.
- **Logs.** The 5,065 "Unclosed" ERROR lines disappear [R]. The 92 transport warnings name the root exception.
- **Diagnosis.** A killed run reads `stale`, a parked run reads `waiting_for_model`; resumed exports report their
  parents' end reasons and downtime.

No per-decision engine time changes; this chapter is about wall time lost to stops and about evidence.

## Behaviour

### What stays byte-identical, and the deliberate exception

- **Every model prompt, decision input and settled world fact is unchanged for every call that does not stall past
  its deadline.**
  - A parked call re-sends the same request object. The wire bytes of every attempt within a call were byte-identical,
    and the retry count never reached the wire [R] `opt-resilience/retry_identity_proof.txt`; today's test
    `tests/unit/as2/test_llm_dispatcher_transport.py:313-321` asserts the same.
  - No wall clock reaches a decision. Wall time is written only to `recorded_at` and `wall_elapsed_ms`
    (`footprints/store.py:141-142`), the ops log and status, and after this chapter also to
    `transport_attempts[].failed_after_ms`, the lineage times and `heartbeat.json`, none of which a prompt builder
    reads. All randomness is keyed by state (`life/randomness.py:9`).
  - Simulated time cannot move while a decision call is pending: AS2 `Society.step` advances only after the round
    commits (`vendor/…/society/society.py:224-240`), and nothing in AS2's society, runner or agent, or in
    `runtime.py`/`life_env.py`, puts a timeout around a decision (reviewer's grep).
  - So a parked run settles exactly like a never-interrupted run that got the same answers. Today's fail-and-resume
    brings a new `run_id` instead (`checkpoint.py:122`), which redraws every action-keyed random number, plus the
    activity-order swap.
  - The longer dispatcher message changes no prompt either. LifeAgent rewrites the `error` of every decision and
    memory failure to the fixed `MODEL_SERVICE_FAILED` feedback before it is recorded (`life_agent.py:197-198`), and a
    failed call ends the decision and the run. The message reaches only `run_failed.data.error`, plan_day's failure
    evidence (`repr(error)`), the runner's log line and `status.json`.
- **The recorded form changes, without losing information.**
  - `model_response` events gain `data.transport_attempts`.
  - `status.json` gains `error_message`.
  - The run manifest gains `lineage` (run manifest structure 6 → 7).
  - The export manifest gains `lineage`, under export structure 8 (bumped once by
    [03-recorded-evidence.md](03-recorded-evidence.md)).
  - `quality.json` gains a `lineage` section (structure 8 → 9, bumped once together with
    [06-host-costs-and-model-cost.md](06-host-costs-and-model-cost.md)).
  - The run directory gains `heartbeat.json`.
  - The ops log gains four records.
  - None of these is read by any prompt builder. `transport_attempts` sits beside `phase` and `attempt`, never inside
    `data.response`, which `continuation._final_assistant` (`life/continuation.py:147`, through
    `_decision_evidence`, `:98-119`) and `DecisionCoordinator._decision_self_note` (`life/decision_coordinator.py:597`)
    read back into later prompts. `rejected_today` (`life/progress_layer.py:306`) reads the envelope's
    `protocol_feedback` (`:333`), a different key. No reader hashes or iterates a whole `model_response.data`.
- **Resume reads frozen metadata.** A resume can succeed today only when the live `/models` value equals the frozen
  copy (`life/run_identity.py:22`). Every prompt-side reader already reads the frozen copy:
  - the agent profile (`custom/agents/life_agent.py:346-353`, `:383-388`), checked against the manifest at
    `runtime.py:410`;
  - `IndependentModelClient` (`model_endpoint.py:108`);
  - `PlanDayClient` via the manifest (`runtime.py:628`).
  So on every resume that can succeed today the value is unchanged. The only behavioural effect: a provider capacity
  reduction mid-chain surfaces as a fatal `BadRequestError` at the first model call instead of at resume start.
- **The deliberate exception: the whole-call deadline on the stall path.** A send that runs past
  120 s + 7.5 ms × `max_tokens` is abandoned and the identical request is sent again, so the model's answer to that
  request is a fresh sample.
  - In the 30-day run this would have touched 2 of 3,720 calls. One was the 605.6 s stall whose one-token answer became
    a correction turn. The other was one valid 357.6 s answer (seq 32815) that would have been resampled.
  - This is one of the two deliberate exceptions (`PROPOSAL-long-run-cost.md:271-277`; SPEC "The owner's rules"). No
    call that finishes within its deadline is affected.
  - Known limit, stated for the owner: if the provider slows down so far that every answer to a request takes longer
    than its deadline, every attempt is cut, and the call parks and fails after 12 hours, where today it would have
    succeeded slowly. The run's slowest legitimate answer (114.9 s) is less than half the 240 s deadline.

### Parking transport failures and 429

The dispatcher is `LLMClient.call` in the AS2 `model-lifecycle.patch`
(`vendor/agentsociety2/packages/agentsociety2/agentsociety2/config/llm_dispatcher.py:474-517`). Every WorldMock model
call goes through it:
- decision turns (`agent/base/agent_runtime.py:952`);
- memory consolidation and reflection (`custom/agents/life_agent.py:393`);
- plan_day (`model_endpoint.py:111` from `runtime.py:679`).

**Failure classes** (unchanged definitions, `llm_dispatcher.py:69-72`):

| Class | Members | Counter | After the counter is spent |
| --- | --- | --- | --- |
| Fatal | `AuthenticationError`, `BadRequestError`, `NotFoundError`, `PermissionDeniedError`, `UnprocessableEntityError` | none | fail at once (unchanged) |
| Transport | `APIConnectionError`, `Timeout` (including the deadline below), `InternalServerError`, `ServiceUnavailableError` | `transport_max_retries` (frozen, unchanged) | **park** |
| Rate limit | `litellm.RateLimitError` (HTTP 429), matched by type only | `max_retries` (frozen, unchanged; its documented meaning 「可重试非传输服务拒绝」 already covers 429) | **park** |
| Ordinary | everything else, including `RouterRateLimitError`, text-matched overload and `BadGatewayError` (HTTP 502, `litellm_core_utils/exception_mapping_utils.py:605-613`; see "Not in this chapter") | `max_retries` | fail (unchanged) |

**Waits.** `n` is the 0-based index of the failure within its class in this call. Waits within the counter are exactly
today's (`llm_dispatcher.py:500`, `:504`), except that a 429 now waits at least its `Retry-After`, and a counted wait
longer than the 12-hour budget ends the call.

| Failure | Within the counter | Parked |
| --- | --- | --- |
| Transport | `2 · 2^n` s (2, 4, 8, … uncapped, as today) | `min(2 · 2^n, 120)` s |
| 429 | `max(min(2^n, 60), Retry-After)` s | `max(min(2^n, 120), Retry-After)` s |
| Ordinary, rate-limit-like text | `min(2^n, 60)` s (as today) | — |
| Ordinary, other | 0 s (as today) | — |

`2^n` is computed as `2.0 ** min(n, 16)`, so a long park never overflows a float.

**Park rule.**
- Parking starts at the first failure whose class counter is spent. That moment is `parked_since`, read from
  `time.monotonic()` in the dispatcher module.
- Before each parked wait the dispatcher checks `now + wait - parked_since > 43,200` (12 h). If true, the call fails;
  otherwise it sleeps `wait` and re-sends the identical request.
- A wait within the counter that is longer than 43,200 s also ends the call at once. Only a 429's `Retry-After` can
  make a counted wait that long (counted transport waits are at most `2 · 2^9` = 1,024 s); without this bound a
  server's `Retry-After` would stall the run for longer than the park budget allows.
- The 12 h budget, the 120 s cap and the deadline constants are engine constants in `llm_dispatcher.py`. They are not
  config fields and not CLI options (proposal "Awaiting the owner" keeps those out). Because AS2 is part of
  `code_sha256` (`run_setup.py:107-117`), they are frozen per chain like any engine rule.
- Counters stay per class and per call. A transport failure never spends `max_retries`, and a 429 never spends
  `transport_max_retries`. One park window serves both classes within a call.
- The concurrency permit is released during every wait, as today (`_complete_once`, `llm_dispatcher.py:538-539`;
  asserted by the existing test helper at `test_llm_dispatcher_transport.py:67-70`).
- The LiteLLM router does not cool down at park rates: a single-deployment group cools down on 429 only with several
  deployments, and on 100 % failure only at 1,000 requests a minute (`litellm/router_utils/cooldown_handlers.py:190-236`,
  litellm 1.89.2) [M].

**Worked numbers** (fake clock, zero-duration sends, `transport_max_retries = 3`, `max_retries = 0`), from
`.work/run30/scratch/spec-05-network-and-restarts/park_sim.txt`:
- 10 transport failures, then success: waits `[2, 4, 8, 16, 32, 64, 120, 120, 120, 120]`, 11 sends.
- Endless transport failures: the call fails after **366** sends, having waited 43,206.0 s in total, of which 43,192 s
  were parked. The waits are 2, 4, 8, 16, 32, 64, then 359 × 120.
- Three 429s with `Retry-After: 30`, then success: waits `[30, 30, 30]`.
- Nine 429s without the header, then success: waits `[1, 2, 4, 8, 16, 32, 64, 120, 120]`.
- A 429 with `Retry-After: 50000` fails after 1 send: the first parked wait already exceeds the budget. With
  `max_retries = 2` the same 429 also fails after 1 send: the counted wait exceeds the budget.

**`Retry-After`** is read from `error.response.headers["retry-after"]` (litellm keeps the provider's headers,
`litellm/exceptions.py:341-351`):
- a value made only of digits is that many seconds;
- otherwise it is parsed as an HTTP date with `email.utils.parsedate_to_datetime` (naive results are taken as UTC), and
  the wait is the seconds from now, floored at 0;
- an unparsable value logs WARNING `Ignoring unparsable Retry-After header: %r` and is ignored.

**Logs** (English, type names only, never the provider's error text, which may carry credentials or request text; the
existing test `test_dispatcher_does_not_log_provider_error_body` keeps guarding this):
- Every re-send logs WARNING:
  `Request failed for '%s' via LLMClient(%s) (attempt %d%s). Backoff %.1fs. Error: %s`. The fourth argument is `""`
  within the counter and `", parked %.0fs"` (seconds since `parked_since`) while parked. The last argument is the
  error chain (below).
- A failed call logs ERROR `Model request failed after %d attempts in %.1f s: %s` (attempts, seconds since the first
  send, error chain).

**Error chain.** `error_chain(error)` follows Python's own traceback rule: `__cause__` if set, otherwise `__context__`
unless `__suppress_context__`. It stops at a repeat or after 16 links, and joins the type names outermost first with
`" <- "`. For the run's failure it reads
`InternalServerError <- OpenAIError <- APIConnectionError <- ConnectError <- ClientConnectorError <- ConnectionResetError`;
offline against a closed port the last link is `ConnectionRefusedError`
[R] `.work/run30/scratch/spec-05-network-and-restarts/chain_probe.txt`.

**The failure exception.** `LLMDispatchError` keeps its class and `rate_limit_like` flag. Its message becomes:

```
Failed to get valid response after {attempts} attempts in {elapsed:.1f} s. Last error type: {Type}. Error chain: {chain}
```

It is raised `from` the last error, as today. It reaches `run_failed`, the runner's
`Agent %s decision %s failed: %s` log line and `status.json` through the existing `RuntimeError("Agent decision failed:
…")` text (`vendor/…/society/society.py:332`).

### Whole-call deadline per send

- Each send in `_complete_once` runs inside `asyncio.timeout(deadline)`, with
  `deadline = 120.0 + 0.0075 × max_tokens`: 240 s at 16,000 (decisions, memory) and 180 s at plan_day's 8,000
  (`life/day_planning.py:32`). A request without a positive integer `max_tokens` gets 120 s. No WorldMock call lacks
  it: the decision stage sets it at `custom/agents/life_agent.py:348`, memory at `:386` and plan_day at
  `day_planning.py:83`; `request_token_count` refuses a request without it (`life/prompt_capacity.py:61-67`).
- When the deadline fires (`scope.expired()` is true), the `TimeoutError` is replaced by
  `litellm.Timeout(message=f"Model call exceeded its {deadline:.1f} s deadline", model=model, llm_provider="openai")`,
  raised from it. `Timeout` is a transport error, so it spends `transport_max_retries` and then parks like any other.
- A `TimeoutError` raised while the scope has not expired is someone else's and propagates unchanged; it stays an
  ordinary error, as today.
- The per-read 300 s (`run_setup.py:48`) stays. It never bounded these calls [R].
- Offline proof: against a server that trickles blank lines for 8 s, a 3 s deadline raised `litellm.Timeout` at 3.0 s
  with cause `TimeoutError`, and a 1 s hold returned normally [R] `deadline_probe.txt`.

### Failed sends in the model-exchange envelope

**Collector.** The dispatcher records every failed send of a call into the collector that is current in the calling
asyncio task, if any. The collector is a `contextvars.ContextVar`, set only by `collect_transport_attempts()`. It is
not state on the shared `LLMClient`. Concurrent persona tasks in one worker each have their own context
(`asyncio.gather` gives each task a copy), and the LLMClient's API and every test double of `call(**request)` stay
unchanged.

**One record per failed send**, in order:

```json
{"attempt":1,"error":"InternalServerError <- OpenAIError <- APIConnectionError <- ConnectError <- ClientConnectorError <- ConnectionResetError","failed_after_ms":8612,"wait_ms":2000}
```

| Key | Type | Meaning |
| --- | --- | --- |
| `attempt` | int ≥ 1 | the send's number within this call (1 = first send) |
| `error` | str | the error chain of that failure (type names only) |
| `failed_after_ms` | int ≥ 0 | `int((failure monotonic − first-send monotonic) × 1000)` |
| `wait_ms` | int ≥ 0 or null | `int(wait × 1000)` before the next send; `null` on the failure that ended the call |

A call that succeeds at the first send records `[]`.

**Where it is recorded.**
- **Decision turns** (AS2 `failure-propagation.patch`). `ModelCallEvidence` (`agent/base/react.py:73-92`) gains the
  last field `transport_attempts: tuple[dict[str, Any], ...] = ()`.
  - `_complete_react_once` (`agent/base/agent_runtime.py:929-965`) wraps the dispatcher call in
    `collect_transport_attempts()`. Its failure evidence carries `transport_attempts=tuple(attempts)`, and on success it
    keeps the tuple in `self._last_transport_attempts`.
  - `_record_react_response` (`:1016-1040`) passes `transport_attempts=self._last_transport_attempts`. It is called
    only from `_call_react_llm` (`:725-752`) right after a successful `_complete_react_once`, so the attribute always
    holds this call's tuple.
  - `prepare_react_loop` (`:639-648`) resets the attribute to `()`; `LifeAgent.prepare_react_loop` calls it first
    (`life_agent.py:429`).
- **Memory calls.** `LifeAgent._dispatch_memory_llm` (`custom/agents/life_agent.py:381-401`) wraps its dispatcher call
  the same way and passes the tuple to both the failure and the response evidence.
- **Unchanged.** `LifeAgent._record_model_evidence` (`:195-210`) uses `dataclasses.replace`, so the field survives its
  error rewrite unchanged.
- **plan_day.** `runtime._call_plan_day` (`runtime.py:671-684`) wraps `caller.call(request)` in the collector and
  returns `(raw, attempts)`. `day_planning.response_draft` and `failure_draft` receive the tuple as the fourth element of
  `link` and write it.
- **Host side.** `ModelEvidenceRecorder.record_model_exchange` (`life/model_evidence.py:29-77`) validates the tuple
  before the store write (so a refused exchange writes nothing) and writes `data["transport_attempts"]` for the
  `response` and `failure` phases only. A `request` phase that carries attempts is a `ProtocolError`. The key is never
  written inside `data.response`.

**Validation** (host, fail closed):
- `transport_attempts` must be a tuple of dicts, each with exactly the keys `{attempt, error, failed_after_ms, wait_ms}`,
  and the `attempt` values must be `1 … len`. Otherwise: ERROR log
  `Model exchange transport attempts are invalid: %s` (call id), then `ProtocolError("Model exchange transport attempts
  are invalid")`.
- A request with attempts: ERROR log `Model request evidence must not carry transport attempts: %s`, then
  `ProtocolError("Model request evidence must not carry transport attempts")`.

**Not covered.** Two AS2 paths that WorldMock never runs construct response evidence without the collector, so they
would record `[]`:
- `agent_runtime._complete_prelude_decision_statement` (`:577-616`), because LifeAgent does not override
  `prelude_decision_statement_request`, which returns `None` (`:541-551`);
- `PersonAgent._dispatch_memory_llm` (`agent/person.py:237-270`), which LifeAgent overrides.

They are left unchanged.

### Root cause in status

- `status.json` gains `error_message`. Its bytes are `canonical_json` of exactly four keys:
  `{"error_message":…,"error_type":…,"phase":…,"status":…}`. Every writer passes it: the actor's phase writes
  (`life_env.py:136`, `:182`, `:209`, `:235`) leave it `null`.
- `cli._run_command` (`cli.py:117-121`) writes `error_type=type(error).__name__` (unchanged) and
  `error_message=failure_message(error)`.
- `failure_message(error)` returns one line:
  - for a `BaseExceptionGroup`, each sub-exception's message joined with `" | "`;
  - for an exception with an attribute `cause` holding another exception, the message of that exception. This is how
    Ray's `RayTaskError` and its dual class `RayTaskError(X)` keep the remote exception (`ray/exceptions.py:103-232`,
    Ray 2.55.1);
  - otherwise `f"{type(error).__name__}: {error}"`, with ANSI colour sequences (`\x1b[…m`, which Ray's text carries)
    removed and every run of whitespace collapsed to one space. Nothing is truncated.
- For the run's transport stops the message reads
  `RuntimeError: Agent decision failed: [{… 'error': "LLMDispatchError('Failed to get valid response after 366 attempts
  in 43206.0 s. Last error type: InternalServerError. Error chain: …')"}]`.
- For the 182136Z failure it reads
  `EnvironmentInputError: model context metadata is unavailable`.

### Picklable errors

- `EnvironmentInputError`, `PreconditionError` and `RegistrationError` gain a `__reduce__` that returns
  `(type(self), constructor arguments, self.__dict__)`. The third element is the state that
  `BaseException.__reduce__` already carries for every other class, so attributes set after construction (for example a
  `__notes__` list from `add_note`) survive as well. The other `WorldMockError` classes take one message argument and
  already round-trip. With `pickle` and `ray.cloudpickle`, each of the three classes carrying a note gives back an equal
  `type`, `args` and `vars()` [R] `.work/run30/scratch/spec-review-05-network-and-restarts/reduce_probe.txt`.
- Unpickling calls `__init__`, so the receiving process logs `WorldMock failure: …` once more, exactly as
  `IntegrityError` and `ProtocolError` already do today. Constructor logging (`errors.py:10-13`) is **kept**: 324 of
  432 fault raise sites rely on it as their only ERROR line [R] `opt-review-resilience/raise_sites.txt`.
- With the fix, `RayError.from_bytes(RayTaskError(…, cause=EnvironmentInputError(…)).to_bytes())` gives back a
  `RayTaskError` whose `cause` is the original error, and `as_instanceof_cause()` gives `RayTaskError(EnvironmentInputError)`
  [R] `pickle_probe.txt`. Without it, the same call returns `UnserializableException`, which is what the run saw.

### Resume and replay use the frozen model metadata

**`_prepare_resume`** (`runtime.py:222-259`) runs in this order:
1. `_require_run_shell(destination)` (unchanged);
2. open the parent store, `FootprintStore(parent_run_dir)`;
3. `parent.get_checkpoint(request.checkpoint_id)`: an empty or unknown id raises
   `IntegrityError("Checkpoint is missing or its record is damaged")` (`footprints/store.py:336-339`);
4. `WorldState.model_validate(checkpoint.state)`;
5. `load_environment(root, metadata_loader=frozen_metadata_loader(parent.manifest))`;
6. `run_setup._configure_process(...)`;
7. `_require_runtime_identity(...)`;
8. `require_environment_identity(...)`;
9. `_restore_branch(...)`, which is the first step that copies anything.

So a missing parent database, a bad checkpoint id, a damaged checkpoint or a manifest without valid frozen metadata
fails before the environment is loaded, before the process is configured, and before any file is copied. No step makes
a network call. Replay uses the same function (`runtime.py:162`).

**`open_world`** (`life/world_setup.py:154-157`), used by the actor through `LifeEngine.open` (`life/engine.py:70-73`)
and by `LifeEnv.restore` (`custom/envs/life_env.py:265-276`), now opens the store first. When it is given a path it
loads the environment with `frozen_metadata_loader(store.manifest)`, then runs `require_environment_identity`. On any
failure it logs ERROR `Existing run could not be opened`, closes the store and re-raises. Callers that pass an
`EnvironmentInputs` (tests only) keep their inputs; their metadata is no longer compared (see "Deleted check").

**`frozen_metadata_loader(source)`** validates `source["model_metadata"]` with `ModelMetadata.model_validate` and
returns `lambda _config: metadata`. A missing key or an invalid value logs ERROR
`Frozen model metadata is missing or invalid` and raises `IntegrityError` with the same text.

**Fresh runs** still fetch live, in both the driver (`runtime.py:192`) and the actor (`life_env.py:91`). `validate`
(`validation.py:39-44`) fetches live too. All three go through `load_model_metadata`, which now retries the whole
fetch:
- **Retried:** `requests.ConnectionError` (including `SSLError` and `ProxyError`), `requests.Timeout`,
  `requests.exceptions.ChunkedEncodingError`, and `requests.HTTPError` whose response status is 429, 500, 502, 503 or
  504. The whole sequence is retried, not one phase: `requests.get`, `raise_for_status()` and `response.json()`.
- **Not retried:** any other HTTP error (401, 403, 404 …), a JSON decode error (`requests.exceptions.JSONDecodeError`
  is a `RequestException` in requests 2.32.5, so it reaches the same handler and is classified as not transient), the
  missing-credential and service-URL `ValueError`s (raised before the fetch), and every parse or validation failure.
- **Schedule:** at most 5 tries, waiting 2, 4, 8 and 16 s between them (30 s in total). The 8 observed failures all
  raised inside the request phase [O] `opt-review-resilience/ssl_phase.txt`, so all of them are covered.
- **Each retry** logs WARNING `Model metadata fetch failed (%s); retrying in %.0f s` with the error type name.
- **The last failure** logs ERROR `Model metadata fetch failed after %d tries: %s` (count, type name). It then raises
  `EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", "model context metadata is unavailable")`, unchanged
  (`model_endpoint.py:38-42`).

**Deleted check.** The term `manifest.get("model_metadata") == inputs.model_metadata.model_dump(mode="json")` in
`require_environment_identity` (`life/run_identity.py:22`) is deleted. Its three callers are:
- `_prepare_resume` (`runtime.py:248`) and `open_world` given a path: the inputs' metadata is now loaded from that same
  manifest, and `ModelMetadata` (`input_models.py:265-278`, four strict fields) dumps back to the value it was
  validated from;
- `EngineContext.validate` (`life/engine_context.py:65`) after `create_world`: the manifest was written from those
  inputs (`world_setup.py:95`);
- `open_world` given an `EnvironmentInputs`: only tests call it this way (no production or script caller passes
  inputs to `LifeEngine.open`); for them the metadata is no longer compared, and the other four identity terms still
  are.

So no production path can make the term false. Missing or invalid frozen metadata is refused by
`frozen_metadata_loader` instead. `open_environment_validated_store` (`run_identity.py:28-37`) is deleted, because its
only caller, `open_world`, now opens and validates the store itself.

### Driver heartbeat and `worldmock status`

**Writer.**
- `runtime._execute` (`runtime.py:443-480`) runs its whole body inside `with DriverHeartbeat(prepared.run_dir):`. The
  run directory exists by then: `_prepare_new_run` has written the ops log into it, and `_prepare_resume` has published
  the branch.
- `DriverHeartbeat.__enter__` writes `heartbeat.json` once, then starts a daemon thread named `worldmock-heartbeat`
  that rewrites it every `HEARTBEAT_INTERVAL_SECONDS = 60` until `__exit__`.
- `__exit__` sets the stop event and joins the thread with a 60 s timeout. The file is left in place.
- A write failure logs ERROR `Driver heartbeat write failed: %s` and the thread keeps beating. `atomic_write` has
  already logged its own exception.
- The heartbeat is driver-only, a single writer of a separate file. It never races the actor's `status.json` writes.

**Bytes**, `canonical_json` of exactly three keys, with UTC `isoformat()` times:

```json
{"heartbeat_at":"2026-10-15T03:00:43.777119+00:00","pid":15228,"started_at":"2026-10-15T02:59:43.700112+00:00"}
```

**Reader.** `read_status` (`status.py:28-56`) keeps every key it returns today and adds three. One key gains a member.

| Key | Value |
| --- | --- |
| `heartbeat` | `null` when `heartbeat.json` is absent. Otherwise its three keys plus `age_seconds`, `round(max(0, now − heartbeat_at), 3)`. Read as UTF-8; content that is not JSON, not an object or has no timezone-aware ISO `heartbeat_at` logs ERROR `Run heartbeat is invalid: %s` and raises `IntegrityError("Run heartbeat is invalid")`. An `OSError` while reading propagates unchanged, as for `status.json` today |
| `liveness` | `null` when `status.json` is absent; `"stopped"` when its status is not `running`; `"stale"` when it is `running` and the heartbeat is absent or older than `HEARTBEAT_STALE_INTERVALS × HEARTBEAT_INTERVAL_SECONDS` = 180 s; `"waiting_for_model"` when it is running with a fresh heartbeat and the last event is a `model_request`; otherwise `"running"` |
| `waiting_for_model_since` | the last event's `recorded_at` when that event is a `model_request`, else `null` |
| `wall_time` | gains `last_event_age_seconds`, `round(max(0, now − last_event_at), 3)`, `null` without events |

`wall_time.elapsed_seconds` stops at:
- now, while `liveness` is `running` or `waiting_for_model`;
- `heartbeat_at`, when it is `stale` and a heartbeat exists;
- otherwise the last event's `recorded_at`, as today for finished runs.

**What the states mean.**
- **Stale** means the driver process is gone. It does not say where the world stopped.
- **A fresh heartbeat with an old last event** means a live driver whose actor or model call is slow.
- **Plan_day parks** are not shown as `waiting_for_model`. A plan_day's request evidence is committed together with its
  response (`runtime.py:649-667`), so during a plan_day park the last event is not a `model_request`; status reads
  `running` with a growing `last_event_age_seconds`.
- **No pid probe.** `psutil` is not installed, and `os.kill(pid, 0)` terminates the process on Windows [R]. Detection
  therefore rests on heartbeat age only; `pid` is informational.

**First and last event by primary key.** [04-history-paths.md](04-history-paths.md) replaces the `min/max(recorded_at)`
full-table scan (`status.py:133-135`) with two primary-key reads, deletes the `BEGIN` (`:35`) and replaces the
`GROUP BY` count and the usage scan. This chapter uses the same two statements, held as module constants, and the last
one also returns the event type:

```python
_FIRST_EVENT_SQL = "SELECT json_extract(body,'$.recorded_at') FROM events ORDER BY event_seq LIMIT 1"
_LAST_EVENT_SQL = ("SELECT json_extract(body,'$.recorded_at'),json_extract(body,'$.event_type') FROM events "
                   "ORDER BY event_seq DESC LIMIT 1")
```

- The statements move from `_wall_time` into `_liveness_view` (below), which passes their values to `_wall_time`.
  Whichever of the two chapters lands second writes this form; the output keys chapter 04 keeps unchanged are not
  touched.
- The values equal today's min and max whenever `recorded_at` never decreases along `event_seq`, which holds for all
  46,142 events of the run (chapter 04's measurement). A branch's copied events keep their earlier stamps.
- On the 14 GB database both lookups took ≤ 6 ms [R] `terminal_probe.txt`.

### `atomic_write` retry on Windows (issue #7)

`atomic_write` (`footprints/assets.py:15-37`) calls `_replace(temporary, path)` instead of `os.replace` at `:25`.
`_replace` is the change from issue #7:
- It tries `os.replace` before each wait in `_REPLACE_RETRY_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4)`, then
  once more. That is 9 tries and 12.75 s of waiting.
- A `PermissionError` on Windows (`os.name == "nt"`) logs WARNING `Atomic replace denied, retrying in %.2fs: %s` (wait,
  file name) and sleeps.
- On any other platform it re-raises at once.
- The ninth failure propagates to the existing handler, which logs ERROR `Atomic artifact publication failed: %s` and
  re-raises.
- Other `OSError`s are not retried.

This protects every writer: checkpoint assets, workspace snapshots, `status.json` (the actor's four phase writes,
`life_env.py:136`, `:182`, `:209`, `:235`), `heartbeat.json` and exports. A `worldmock status` that reads
`heartbeat.json` while the driver replaces it is exactly the race of issue #7, so the heartbeat ships with this retry.

### Lineage

**Run manifest.**
- Every run manifest gains the top-level key `lineage`. It is a list ordered oldest first. A fresh run writes `[]`
  (`world_setup._run_manifest`, `:84-100`).
- A branch (resume or replay) writes `[*parent.manifest["lineage"], lineage_entry(parent, checkpoint)]`, built in
  `_populate_branch` (`footprints/checkpoint.py:117-143`) before the copy starts.
- `RUN_MANIFEST_SCHEMA_VERSION` goes from 6 to 7 (`life/models.py:32`; its comment already says the version moves when
  the top-level field set changes). No other chapter changes this version (chapter 02 says so at `:185`); the SPEC
  index's version list and chapter 09 must name it.
- The checkpoint binds the manifest by `manifest_sha256` (`footprints/store.py:345-346`), and `lineage` is written once
  at creation, so every later checkpoint of the branch binds it too.
- `source_run_ids` stays exactly as it is: the sorted set (`checkpoint.py:125`). Export certification
  (`footprints/validation.py:178`) and evaluation (`evaluation/validation.py:287-296`) use it as a set.

**One entry describes one parent segment.** Keys are sorted by `canonical_json`; the types are exact.

| Key | Type | Value |
| --- | --- | --- |
| `run_id` | str | the parent's run id |
| `simulated_from` | int | where the parent segment began: the previous entry's `checkpoint_time`, or the manifest's `window.start` when the parent's own lineage is empty |
| `checkpoint_id` | str | the checkpoint the child branched from |
| `checkpoint_event_seq` | int | that checkpoint's `last_event_seq` |
| `checkpoint_time` | int | that checkpoint's `current_time` |
| `last_event_seq` | int | the parent's last event sequence at branch time |
| `last_event_time` | int | that event's `occurred_at` (how far the parent got) |
| `last_recorded_at` | str | that event's `recorded_at` (wall clock) |
| `discarded_events` | int | `last_event_seq − checkpoint_event_seq` |
| `end_reason` | str | `"run_failed"`, `"run_ended"` or `"no terminal event"` |
| `end_error` | str or null | the `data.error` of that `run_failed`, else `null` |
| `branched_at` | str | `datetime.now(timezone.utc).isoformat()` when the entry is built |

**End reason.**
- It is the event type of the parent segment's **last** `run_failed` or `run_ended` event. The search covers only the
  parent's own events, which are the events after the previous entry's `checkpoint_event_seq` (after 0 for a fresh
  parent).
- `run_failed` can follow a `run_ended` (`world_ops._failure_draft` docstring, `life/world_ops.py:95-103`), and the
  later one wins.
- No such event, as with a kill or a run still going, gives `"no terminal event"`.
- The lookup is bounded. Both event types carry no `agent_id`, so the query is served by the
  `events_agent_type` index (`footprints/store.py:24`): plan
  `SEARCH events USING INDEX events_agent_type (<expr>=? AND <expr>=? AND event_seq>?)`, 0.5–9 ms on the run's
  databases [R] `terminal_probe.txt`.
- The last event is read by primary key.

**For the 30-day chain** this gives four parents:

| Parent segment | `end_reason` | `discarded_events` |
| --- | --- | --- |
| Segment 1 (`6c406d98…`) | `no terminal event` | 3 |
| Segment 2 | `run_failed` | 6 |
| Segment 3 | `run_failed` | 6 |
| Segment 4 | `run_failed` | 6 |

The tails are seq 7217–7219, 8152–8157, 22587–22592 and 35312–35317 [R] `tail_probe.txt`.

**Export manifest.** `_export_identity` (`footprints/export.py:141-159`) adds `"lineage": run["lineage"]` (a copy of
the run manifest's list) under export structure 8 (bumped by
[03-recorded-evidence.md](03-recorded-evidence.md); this chapter adds no second bump). `frozen_run_manifest` carries
the same list (`export.py:99`). Certification (`_validate_identity`, `footprints/validation.py:84-112`) requires the
frozen manifest's `lineage` to be a list equal to the export manifest's `lineage`. Otherwise it logs ERROR
`Export lineage disagrees with the frozen run manifest` and raises `IntegrityError` with the same text.

**quality.json** gains the top-level section `lineage`, computed only from the frozen manifest by `_lineage_quality`.
For a branch with one killed parent (illustrative ids and times) its bytes are:

```json
{"downtime_seconds":470.3,"end_reason_counts":{"no terminal event":1},"method":"Parent segments read from the frozen run manifest lineage; downtime is branched_at minus the parent's last recorded event","parents":[{"checkpoint_id":"b09fb09b77604e2b9e1cf96e96e32789","discarded_events":3,"downtime_seconds":470.3,"end_error":null,"end_reason":"no terminal event","run_id":"6c406d986e5a49f6bb224e779e3a9535"}],"segment_count":2}
```

- `parents` holds one row per lineage entry, in order. Each row is the entry's `run_id`, `checkpoint_id`,
  `end_reason`, `end_error` and `discarded_events`, plus
  `downtime_seconds = round((branched_at − last_recorded_at).total_seconds(), 3)`.
- `end_reason_counts` is keyed by reason in sorted order.
- `downtime_seconds` is `round(sum(row downtimes, 0.0), 3)`, so a fresh run reports `0.0`, `parents` `[]`,
  `end_reason_counts` `{}` and `segment_count` 1.
- `segment_count` is `len(lineage) + 1`.
- Export certification recomputes the whole report from the frozen manifest and compares it
  (`docs/design/05-recording-engine.md:425-428`); the section reads only the manifest, so the recomputation is equal.
- `transport_or_terminal_failures` (`quality.py:608`) keeps its meaning: failure-phase model evidence in the exported
  events.
- `_validate_inputs` (`quality.py:101-124`) refuses a manifest whose `lineage` is not a list:
  `IntegrityError("Quality report manifest has no lineage")`.
- The report's `schema_version` becomes 9. [06-host-costs-and-model-cost.md](06-host-costs-and-model-cost.md) also
  bumps 8 → 9; whichever lands first bumps, and the other leaves it at 9.

### Closing LiteLLM's cached async clients

- **Why.** LiteLLM caches `AsyncOpenAI` and `AsyncHTTPHandler` clients in `litellm.in_memory_llm_clients_cache`. Each
  cache key is suffixed with `-{id(running loop)}` (`litellm/caching/llm_caching_handler.py:22-50`). `LLMClient.close()`
  only calls `Router.discard()` (`llm_dispatcher.py:411-423`), and only after the task's loop has ended
  (`agent/runner.py:355-364`). The cached clients are then never closed, and aiohttp reports them at garbage collection.
- **`close_cached_async_clients()`** (new, `llm_dispatcher.py`). Inside the running loop it handles every cache entry
  whose key ends with `-{id(asyncio.get_running_loop())}`:
  - it removes the entry with `cache.delete_cache(key)`;
  - if the entry's `close` is a coroutine function, it awaits it.

  Close failures are logged with `logger.exception("Failed to close cached LiteLLM client %s", type name)`. Every entry
  is still evicted, and after the loop the first failure is raised. The function returns the number of entries handled.
- **Why evict as well.** A later task in the same worker can never receive a closed client. A later loop can also
  reuse the same `id()`, which would otherwise hand it a client bound to a dead loop.
- **Workers.** `_run_worker_coroutine` (`agent/runner.py:355-364`) runs `asyncio.run(_closing_llm_clients(coroutine))`.
  `_closing_llm_clients` awaits the coroutine and then calls `close_cached_async_clients()` inside the same loop.
  - If the coroutine raised and the close also fails, it raises `BaseExceptionGroup("Agent task and LiteLLM client close
    both failed", [failure, close_error])` from the failure, the same pattern as `_run_bound_agent`.
  - `_finish_llm_task` still runs after the loop (stats, then `Router.discard()`).
- **The env actor.** `LifeEnv.close` (`custom/envs/life_env.py:285-294`) awaits `close_cached_async_clients()` first and
  closes the engine in a `finally`. `_cleanup_runtime` calls `society.close()` before `ray.kill`
  (`runtime.py:488-497`), and `society.close()` reaches `LifeEnv.close` inside the actor's own loop
  (`society.py:473-486` → `router_base.py:385-388`). So plan_day's clients are closed before the actor is killed.
- **Proof.** Over 5 worker-style tasks with real routers against a local server: discard only gives 10 "Unclosed"
  records; closing and evicting the loop's entries gives 0, and every later task still succeeds [R] `close_probe.txt`.

### Ops records

All four are written with the existing `OpsLogger` (`opslog.py`), component `agent_runtime`.

| Code | Level | When | Fields besides `code`/`detail` |
| --- | --- | --- | --- |
| `MODEL_EXCHANGE` (existing) | info / error | every recorded exchange | existing fields, plus `commit_ms`: float, `round((perf_counter after − before) × 1000, 1)` around the store write in `record_model_exchange` (`commit` today, `append_evidence` after [01-store-commits.md](01-store-commits.md)) |
| `MODEL_TRANSPORT_WAIT` (new) | warn | a response or failure exchange (decision or memory) or a plan_day call whose `transport_attempts` is not empty | `call_id`, `failed_sends` (len), `waited_ms` (sum of non-null `wait_ms`), `last_error` (the last record's `error`), plus `agent_id` (and `decision_id` on the decision path) |
| `MODEL_PROTOCOL_REJECTED` (new) | warn | a `response`-phase exchange whose response carries `protocol_feedback`. Failure-phase exchanges also carry it (LifeAgent's `MODEL_SERVICE_FAILED`, `life_agent.py:197-201`) and are not protocol rejections, so the phase is checked | `agent_id`, `decision_id`, `call_id`, `error_code` (`protocol_feedback["error_code"]`) |
| `PLAN_DAY_CALL` (new) | info / error | every plan_day model call (one per validation attempt; a call may contain several sends), on success (info) and on failure (error) | `agent_id`, `call_id`, `call_ms` (float, 0.1 ms, around `caller.call`, parked waits included), `outcome` (`"response"` or `"failure"`) |

Detail texts:
- `MODEL_TRANSPORT_WAIT`: `f"Model call {call_id} failed {n} sends and waited {waited} ms"`.
- `MODEL_PROTOCOL_REJECTED`: `f"Model response for call {call_id} was rejected: {error_code}"`.
- `PLAN_DAY_CALL`: `f"Plan day call {call_id} ended with {outcome}"`.

The ops log is not audited and nothing reads it back.

### Failure handling summary

| Condition | Log | Raised |
| --- | --- | --- |
| Park budget spent, a single wait longer than the budget, or a fatal or ordinary failure past its counter | ERROR `Model request failed after %d attempts in %.1f s: %s` | `LLMDispatchError("Failed to get valid response after … Error chain: …")` from the last error |
| Deadline expired | — (the dispatcher's re-send WARNING names `Timeout <- TimeoutError <- CancelledError`) | `litellm.Timeout("Model call exceeded its %.1f s deadline")`, handled as transport |
| Bad `Retry-After` | WARNING `Ignoring unparsable Retry-After header: %r` | none |
| Cached client close fails | `Failed to close cached LiteLLM client %s` with traceback | the first failure, after every entry is evicted |
| Task and close both fail | as above | `BaseExceptionGroup("Agent task and LiteLLM client close both failed", …)` |
| Invalid attempts in evidence | ERROR `Model exchange transport attempts are invalid: %s` | `ProtocolError("Model exchange transport attempts are invalid")` |
| Attempts on a request | ERROR `Model request evidence must not carry transport attempts: %s` | `ProtocolError("Model request evidence must not carry transport attempts")` |
| Frozen metadata missing or invalid | ERROR `Frozen model metadata is missing or invalid` | `IntegrityError` (same text) |
| Existing run cannot be opened | ERROR `Existing run could not be opened` | the original error |
| Metadata fetch, transient | WARNING `Model metadata fetch failed (%s); retrying in %.0f s` | none |
| Metadata fetch, final | ERROR `Model metadata fetch failed after %d tries: %s` | `EnvironmentInputError(…, "model context metadata is unavailable")` (unchanged) |
| Heartbeat write fails | ERROR `Driver heartbeat write failed: %s` | none (thread continues) |
| Heartbeat unreadable | ERROR `Run heartbeat is invalid: %s` | `IntegrityError("Run heartbeat is invalid")` |
| Windows `PermissionError` on replace | WARNING `Atomic replace denied, retrying in %.2fs: %s` | after the ninth try: the `PermissionError` (and the existing ERROR) |
| Parent without events at branch time | ERROR `Run has no committed event` | `IntegrityError("Run has no committed event")` |
| Export lineage differs | ERROR `Export lineage disagrees with the frozen run manifest` | `IntegrityError` (same text) |
| Quality manifest without lineage | — (`IntegrityError` logs itself) | `IntegrityError("Quality report manifest has no lineage")` |

### What is deleted

- `exhausted = transport_retries >= transport_max_retries` as a failure condition (`llm_dispatcher.py:499`). Spending
  the count now starts the park.
- The `model_metadata` term in `require_environment_identity` (`life/run_identity.py:22`).
- `open_environment_validated_store` (`life/run_identity.py:28-37`), and its import at `life/world_setup.py:20`.
- The live `/models` fetch on resume and replay: in the driver (`runtime.py:239`) and in the actor (`world_setup.py:156`).
- The `min/max(recorded_at)` scan in `status._wall_time` (`status.py:133-135`); chapter 04 deletes the same lines, and
  the deletion happens once.
- The `RateLimitError` case of `test_non_transport_provider_errors_do_not_use_transport_budget` (see Tests).

## Interfaces and files

Style for every item: comments and docstrings in Chinese, `@brief` on every interface, English logs and errors, lines
≤ 120 characters, functions ≤ 50 lines and ≤ 5 parameters. In `src/worldmock` and `custom` every function and method,
`__init__`, `__enter__` and `__exit__` included, has a docstring with Chinese text (`tests/unit/engineering/
test_style_gate.py` counts a missing one). Code below is normative where a mutation or a byte format depends on its
exact text.

### AS2 `config/llm_dispatcher.py` (via `patches/agentsociety2/model-lifecycle.patch`)

**Imports** (`:17-48`). Add `email.utils`, `inspect`, `from collections.abc import Iterator`,
`from contextlib import contextmanager`, `from contextvars import ContextVar` and
`from datetime import datetime, timezone`.

**`__all__`** (`:50-58`). Add `"close_cached_async_clients"`, `"collect_transport_attempts"` and `"error_chain"`.

**Constants** after `_FATAL_ERRORS` (`:71-72`):

```python
#: 计数用尽后停放而不失败的类别：传输错误与限流 429（按类型判定，不按文本猜测）。
_PARKED_ERRORS = (*_TRANSPORT_ERRORS, RateLimitError)
#: 停放的总时长（秒），从停放开始计；引擎常量，不是配置字段或命令行参数。
_PARK_BUDGET_SECONDS = 12 * 60 * 60
#: 停放期间两次发送之间的等待上限（秒）。
_PARK_MAX_WAIT_SECONDS = 120.0
#: 单次发送的整次期限：基础秒数加请求 max_tokens 每个的秒数（16000 时 240 秒）。
_DEADLINE_BASE_SECONDS = 120.0
_DEADLINE_SECONDS_PER_TOKEN = 0.0075
#: 当前 asyncio 任务里登记失败发送的收集器；只由 collect_transport_attempts 设置，不是 LLMClient 上的共享状态。
_TRANSPORT_ATTEMPTS: ContextVar[list[dict[str, Any]] | None] = ContextVar("transport_attempts", default=None)
```

**New module functions** (after `is_rate_limit_like_error`, `:91-104`):

```python
def error_chain(error: BaseException) -> str:
    """@brief 异常及其原因链的类型名，自外向内以「 <- 」连接：显式原因优先，未抑制时取隐式上下文，至多 16 层；
    只含类型名，不含可能带凭据或请求片段的异常正文。"""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(names) < 16:
        seen.add(id(current))
        names.append(type(current).__name__)
        if current.__cause__ is not None:
            current = current.__cause__
        else:
            current = None if current.__suppress_context__ else current.__context__
    return " <- ".join(names)


@contextmanager
def collect_transport_attempts() -> Iterator[list[dict[str, Any]]]:
    """@brief 在当前任务上下文收集其间模型调用的失败发送，供证据信封写入 transport_attempts；退出时恢复外层收集器。

    收集器属于调用方所在的 asyncio 任务；同一 worker 里并发的人物任务各有各的，调用方每次只包住一次模型调用。
    """
    attempts: list[dict[str, Any]] = []
    token = _TRANSPORT_ATTEMPTS.set(attempts)
    try:
        yield attempts
    finally:
        _TRANSPORT_ATTEMPTS.reset(token)


def _attempt_record(attempt: int, chain: str, elapsed: float, wait: float | None) -> dict[str, Any]:
    """@brief 一次失败发送的证据：发送序号、错误链、距第一次发送的毫秒数、到下一次发送的等待毫秒数（结束调用时为空）。"""
    return {"attempt": attempt, "error": chain, "failed_after_ms": int(elapsed * 1000),
            "wait_ms": None if wait is None else int(wait * 1000)}


def _retry_after_seconds(error: Exception) -> float | None:
    """@brief 429 响应头 Retry-After 给出的等待秒数（整数秒或 HTTP 日期，不小于 0）；没有或无法解析时为空。"""
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        moment = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        logger.warning("Ignoring unparsable Retry-After header: %r", value)
        return None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


def _backoff(error: Exception, index: int, counted: bool) -> float:
    """@brief 同类第 index 次失败后的等待秒数：传输 2·2^n，限流与其他过载 2^n（计数内封顶 60），其余 0；
    停放时一律封顶 120；429 不短于 Retry-After。计数内的值与原规则相同。"""
    exponent = min(index, 16)
    if isinstance(error, _TRANSPORT_ERRORS):
        wait = 2.0 * 2.0 ** exponent
    elif is_rate_limit_like_error(error):
        wait = min(2.0 ** exponent, 60.0) if counted else 2.0 ** exponent
    else:
        wait = 0.0
    if not counted:
        wait = min(wait, _PARK_MAX_WAIT_SECONDS)
    retry_after = _retry_after_seconds(error) if isinstance(error, RateLimitError) else None
    return wait if retry_after is None else max(wait, retry_after)


def _attempt_deadline(kwargs: dict[str, Any]) -> float:
    """@brief 单次发送的整次期限：120 秒加 max_tokens 每个 7.5 毫秒；没有正整数 max_tokens 时为 120 秒。"""
    tokens = kwargs.get("max_tokens")
    tokens = tokens if type(tokens) is int and tokens > 0 else 0
    return _DEADLINE_BASE_SECONDS + _DEADLINE_SECONDS_PER_TOKEN * tokens


async def close_cached_async_clients() -> int:
    """@brief 在当前事件循环里关闭并移出 LiteLLM 客户端缓存中属于本循环的全部条目，返回处理的条目数。

    LiteLLM 以「键-事件循环 id」缓存 AsyncOpenAI 与 AsyncHTTPHandler（litellm/caching/llm_caching_handler.py）；
    循环结束前在本循环里关闭，避免 aiohttp 的 Unclosed client session；移出缓存使后续任务不会拿到已关闭的客户端。
    耦合点：litellm.in_memory_llm_clients_cache.cache_dict 与键后缀格式，随锁定的 LiteLLM 版本核对。
    关闭失败逐个记录，全部移出后抛出第一个。
    """
    import litellm

    cache = litellm.in_memory_llm_clients_cache
    suffix = f"-{id(asyncio.get_running_loop())}"
    keys = [key for key in cache.cache_dict if key.endswith(suffix)]
    failures: list[Exception] = []
    for key in keys:
        client = cache.cache_dict[key]
        cache.delete_cache(key)
        close = getattr(client, "close", None)
        if inspect.iscoroutinefunction(close):
            try:
                await close()
            except Exception as error:
                logger.exception("Failed to close cached LiteLLM client %s", type(client).__name__)
                failures.append(error)
    if failures:
        raise failures[0]
    return len(keys)
```

**New private class** before `LLMClient` (`:363`):

```python
@dataclass
class _Retries:
    """@brief 一次 call 的两类重试计数与停放起点；只在这次调用内存在，不跨调用共享。"""

    max_retries: int
    transport_max_retries: int
    ordinary: int = 0
    transport: int = 0
    parked_since: float | None = None

    def next_wait(self, error: Exception, now: float) -> float | None:
        """@brief 下一次发送前的等待秒数；None 表示这次调用失败。计数用尽后传输与限流停放，十二小时为限；
        任何一次等待长于十二小时也立即失败。"""
        if isinstance(error, _FATAL_ERRORS):
            return None
        transport = isinstance(error, _TRANSPORT_ERRORS)
        index = self.transport if transport else self.ordinary
        counted = index < (self.transport_max_retries if transport else self.max_retries)
        if transport:
            self.transport += 1
        else:
            self.ordinary += 1
        if not counted and not isinstance(error, _PARKED_ERRORS):
            return None
        wait = _backoff(error, index, counted)
        if counted:
            return None if wait > _PARK_BUDGET_SECONDS else wait
        if self.parked_since is None:
            self.parked_since = now
        return None if now + wait - self.parked_since > _PARK_BUDGET_SECONDS else wait
```

**`LLMClient.call`** (`:474-517`). The signature is unchanged. @brief:
「发送模型请求；普通与传输重试分别计数，计数内退避不变；计数用尽后传输失败与限流 429 原样重发同一请求停放至多十二小时；
每次失败发送登记到当前收集器；失败报告发送次数、耗时、最后错误类型与根因异常链。」 Body after the unchanged stream and
budget checks (`:477-487`):

```python
        retries, collector = _Retries(max_retries, transport_max_retries), _TRANSPORT_ATTEMPTS.get()
        first_send, attempts = time.monotonic(), 0
        while True:
            attempts += 1
            try:
                return await self._complete_once(effective_model, messages or [], kwargs)
            except Exception as error:
                now = time.monotonic()
                wait, chain = retries.next_wait(error, now), error_chain(error)
                if collector is not None:
                    collector.append(_attempt_record(attempts, chain, now - first_send, wait))
                if wait is None:
                    raise self._dispatch_error(error, attempts, now - first_send) from error
                parked = "" if retries.parked_since is None else f", parked {now - retries.parked_since:.0f}s"
                logger.warning("Request failed for '%s' via LLMClient(%s) (attempt %d%s). Backoff %.1fs. Error: %s",
                               effective_model, self.model_type, attempts, parked, wait, chain)
                if wait:
                    await asyncio.sleep(wait)
```

**`LLMClient._dispatch_error(self, error: Exception, attempts: int, elapsed: float) -> LLMDispatchError`** (new).
@brief 「记录并构造最终失败：发送次数、耗时、最后错误类型与根因异常链，不含供应商错误正文。」

```python
        chain = error_chain(error)
        logger.error("Model request failed after %d attempts in %.1f s: %s", attempts, elapsed, chain)
        return LLMDispatchError(
            f"Failed to get valid response after {attempts} attempts in {elapsed:.1f} s. "
            f"Last error type: {type(error).__name__}. Error chain: {chain}",
            rate_limit_like=is_rate_limit_like_error(error))
```

**`LLMClient._complete_once`** (`:519-539`). The router call at `:528-529` becomes
`response = await self._send_within(_attempt_deadline(kwargs), model, messages, kwargs)`. The @brief adds
「整次期限内执行一次」. Nothing else changes.

**`LLMClient._send_within(self, deadline: float, model: str, messages: list, kwargs: dict) -> ModelResponse`** (new).
@brief 「整次期限包住一次底层发送；期限触发时改抛 LiteLLM 传输超时（以原 TimeoutError 为原因），别处的
TimeoutError 原样传播；日志由调用层统一记录。」

```python
        scope = asyncio.timeout(deadline)
        try:
            async with scope:
                return await self._router.acompletion(
                    model=model, messages=messages, stream=False, timeout=_LLM_REQUEST_TIMEOUT, **kwargs)
        except TimeoutError as error:
            if not scope.expired():
                raise
            raise Timeout(message=f"Model call exceeded its {deadline:.1f} s deadline", model=model,
                          llm_provider="openai") from error
```

### AS2 `agent/runner.py` (via `model-lifecycle.patch`)

- **Import.** Add `from agentsociety2.config.llm_dispatcher import close_cached_async_clients` at module level, next to
  the decision-round import (`:46-49`).
- **New `async def _closing_llm_clients(coroutine: Any) -> Any`**, before `_run_worker_coroutine`. @brief
  「在任务自己的事件循环里运行协程，返回前在同一循环关闭并移出本循环缓存的 LiteLLM 异步客户端；任务与关闭都失败时保留两者。」

  ```python
      try:
          result = await coroutine
      except BaseException as failure:
          try:
              await close_cached_async_clients()
          except Exception as close_error:
              raise BaseExceptionGroup("Agent task and LiteLLM client close both failed",
                                       [failure, close_error]) from failure
          raise
      await close_cached_async_clients()
      return result
  ```

- **`_run_worker_coroutine`** (`:355-364`). `:361` becomes
  `result = asyncio.run(_closing_llm_clients(coroutine))`. Nothing else changes.

### AS2 `agent/base/react.py` and `agent/base/agent_runtime.py` (via `failure-propagation.patch`)

- **`ModelCallEvidence`** (`react.py:73-92`). Last field
  `transport_attempts: tuple[dict[str, Any], ...] = ()`. Docstring line:
  `:param transport_attempts: 这次调用在结果之前失败的发送（llm_dispatcher.collect_transport_attempts 收集）；请求证据恒为空。`
- **`agent_runtime.py` import.** `from agentsociety2.config.llm_dispatcher import collect_transport_attempts` at
  module level. Importing `agentsociety2.agent` already loads `agentsociety2.config` in the same statement
  (`agent/base/agent.py:33` imports `agent_runtime`, then `:38` imports `env/router_base.py`, which imports
  `agentsociety2.config` at `:62`), and `config/config.py`, `config/llm_dispatcher.py` and `logger/__init__.py`
  import nothing from `agentsociety2.agent`. So `agentsociety2.config` is loaded a few lines earlier in that one import, never before
  the process is configured, and no cycle appears. The same holds for the `runner.py` import above.
- **`prepare_react_loop`** (`:639-648`). Add
  `self._last_transport_attempts: tuple[dict[str, Any], ...] = ()` after `:647`.
- **`_complete_react_once`** (`:929-965`). `:951-965` become:

  ```python
          with collect_transport_attempts() as transport_attempts:
              try:
                  response = await self._dispatcher.call(**request)
              except Exception as exc:
                  await self._record_model_evidence(
                      ModelCallEvidence(context, call_id, "failure", attempt, request, error=repr(exc),
                                        transport_attempts=tuple(transport_attempts))
                  )
                  raise
          self._last_transport_attempts = tuple(transport_attempts)
          return response
  ```

  The @brief adds 「失败发送随响应或失败证据一并登记」.
- **`_record_react_response`** (`:1016-1040`). The `ModelCallEvidence(...)` gains
  `transport_attempts=self._last_transport_attempts`.

### `patches/agentsociety2/` and the worktree-local vendor copy

In this worktree, `vendor/agentsociety2` and `.venv` are directory junctions to the **shared main checkout**
(`D:\code\WorldMock\vendor\agentsociety2` and `D:\code\WorldMock\.venv`). Editing through the junction, running
`scripts/bootstrap.py` here, or `Remove-Item -Recurse` on a junction (which in Windows PowerShell 5.1 deletes the
target's contents) would change every other session's checkout. The procedure:

1. **Unlink only the vendor junction.**

   ```powershell
   cmd /c rmdir D:\code\WorldMock\.claude\worktrees\lrc\vendor\agentsociety2
   Test-Path D:\code\WorldMock\vendor\agentsociety2\packages\agentsociety2\agentsociety2\__init__.py  # must print True
   ```

   `rmdir` on a junction removes the link, never the target. Leave `.venv` linked. Never run `scripts/bootstrap.py` in
   this worktree while `.venv` is a junction: `create_environment` and `install_source` would rebuild and reinstall the
   shared environment.
2. **Rebuild the patched tree locally** from the current SERIES. Run this from the worktree root, with `cwd` set to it:

   ```powershell
   D:\code\WorldMock\.venv\Scripts\python.exe -c "from pathlib import Path; from scripts.bootstrap_sources import restore_as2; print(restore_as2(Path('.').resolve()))"
   ```

   It must print `bf716c7b982ea1aa057207b4ad06e1758b598bccef90834e18da2ca4253db3c6`. It stages under
   `<worktree>/.work/bootstrap` and copies to `<worktree>/vendor/agentsociety2/packages/agentsociety2`.
3. **Import the local copy.** The shared venv's `_editable_impl_agentsociety2.pth` names the main checkout's copy.
   `PYTHONPATH` entries precede `.pth` entries, so set, for in-process runs:

   ```powershell
   $env:PYTHONPATH = "D:\code\WorldMock\.claude\worktrees\lrc\src;D:\code\WorldMock\.claude\worktrees\lrc;D:\code\WorldMock\.claude\worktrees\lrc\vendor\agentsociety2\packages\agentsociety2"
   ```

   Check that `python -c "import agentsociety2; print(agentsociety2.__file__)"` prints the worktree path. Ray workers
   inherit the copy through `_build_ray_job_config` (`llm_dispatcher.py:547-563`).
   - **Subprocess tests that overwrite `PYTHONPATH`.** `tests/system/cli/test_cli_clean_process.py:62-66` sets it to
     `src` alone. These still import the shared copy, and they need an interpreter whose `.pth` names the worktree
     copy. Make one outside `.venv`:
     - `py -3.12 -m venv <worktree>\.work\venv-lrc`;
     - `<venv>\Scripts\python -m pip install --disable-pip-version-check --require-hashes -r requirements\lock.txt`;
     - `<venv>\Scripts\python -m pip install --no-deps --no-build-isolation -e vendor\agentsociety2\packages\agentsociety2`;
     - the same with `-e .`.

     These are the bootstrap's own commands (`scripts/bootstrap_environment.py:89-115`) aimed at a different directory.
     Run those tests with that interpreter.
4. **Edit** the four files in the local copy: `config/llm_dispatcher.py`, `agent/runner.py`, `agent/base/react.py`
   and `agent/base/agent_runtime.py`. Keep LF line endings; a CRLF edit changes `source_sha256`. The worktree has
   `core.autocrlf=false` in `config.worktree`.
5. **Regenerate the two patches** with a scratch script under `<worktree>/.work/`. On today's SERIES this procedure
   regenerates `failure-propagation.patch` and `model-lifecycle.patch` byte-identically [R] `regen_probe.txt`.

   ```python
   """Regenerate model-lifecycle.patch and failure-propagation.patch from the edited worktree vendor copy."""
   import hashlib, json, shutil, subprocess, sys
   from pathlib import Path
   ROOT = Path(r"D:/code/WorldMock/.claude/worktrees/lrc")
   sys.path.insert(0, str(ROOT))
   from scripts.bootstrap_sources import extract_archive, source_sha256
   PATCHES, PACKAGE = ROOT / "patches/agentsociety2", ROOT / "vendor/agentsociety2/packages/agentsociety2"
   WORK = ROOT / ".work/as2-regen"
   shutil.rmtree(WORK, ignore_errors=True)
   WORK.mkdir(parents=True)
   series = json.loads((PATCHES / "SERIES").read_text(encoding="utf-8"))
   extract_archive(PATCHES / series["upstream"]["archive"], WORK)
   git = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "diff.noprefix=false",
          "-c", "diff.mnemonicPrefix=false", "-c", "user.name=regen", "-c", "user.email=regen@local"]
   subprocess.run([*git, "init", "-q"], cwd=WORK, check=True)
   subprocess.run([*git, "add", "-A"], cwd=WORK, check=True)
   subprocess.run([*git, "commit", "-qm", "baseline"], cwd=WORK, check=True)
   owned = {}
   for entry in series["patches"]:
       patch = PATCHES / entry["file"]
       subprocess.run([*git, "apply", str(patch)], cwd=WORK, check=True)
       owned[entry["file"]] = [line.split()[2][2:] for line in patch.read_text(encoding="utf-8").splitlines()
                               if line.startswith("diff --git ")]
   for name in ("model-lifecycle.patch", "failure-propagation.patch"):
       for relative in owned[name]:
           shutil.copyfile(PACKAGE / relative, WORK / relative)
   edited = source_sha256(PACKAGE / "agentsociety2", (".",), "agentsociety2/")
   if source_sha256(WORK / "agentsociety2", (".",), "agentsociety2/") != edited:
       raise SystemExit("The vendor copy differs from the SERIES tree outside the two patches' files")
   subprocess.run([*git, "add", "-A"], cwd=WORK, check=True)
   for name in ("model-lifecycle.patch", "failure-propagation.patch"):
       diff = subprocess.run([*git, "diff", "--cached", "HEAD", "--", *owned[name]], cwd=WORK, check=True,
                             capture_output=True).stdout
       (PATCHES / name).write_bytes(diff)
       print(name, hashlib.sha256(diff).hexdigest())
   print("source_sha256", edited)
   ```

   - The two patches own disjoint files: model-lifecycle owns `agent/runner.py`, `agent/service_proxy.py` and
     `config/llm_dispatcher.py`; failure-propagation owns `agent/base/__init__.py`, `agent.py`, `agent_runtime.py` and
     `react.py`. No other patch touches them, so their order in SERIES is unaffected.
   - The `source_sha256` comparison before writing proves the edits stay inside those files: the work tree is the
     baseline with every SERIES patch applied plus the copied files, so any other difference makes it unequal to the
     vendor copy and the script stops. The other patches are not regenerated and not compared as text:
     `decision-round.patch` does not regenerate byte-identically from `git diff` (128,779 bytes regenerated against
     128,753 stored, `regen_probe.txt`), so it is left untouched.
6. **Update the digests.**
   - `patches/agentsociety2/SERIES`: the two `patches[].sha256` values printed above, and `source_sha256` printed
     above. `install_files` and the `upstream` block are unchanged.
   - `src/worldmock/run_setup.py:26` `AS2_LOCKED_SOURCE_SHA256` takes the new `source_sha256`.
7. **Verify** without touching the main checkout:
   - `restore_as2` on a temporary copy of `patches/` returns the new digest; this is what
     `tests/unit/engineering/test_bootstrap.py::test_series_rebuilds_the_real_package_and_refuses_source_corruption`
     does;
   - `tests/unit/engineering/test_shared_rules_spec.py` passes (SERIES equals `AS2_LOCKED_SOURCE_SHA256`);
   - `python -c "from worldmock.run_setup import require_locked_as2; require_locked_as2()"` passes under the
     `PYTHONPATH` above.
8. **Merge-time note (not part of the implementation).** After the integration branch reaches `main`, the main
   checkout's `vendor/agentsociety2` no longer matches SERIES, and `restore_as2` refuses it with `AS2 runtime identity
   differs from the locked source`. It is rebuilt there by deleting `vendor/agentsociety2/packages/agentsociety2` and
   rerunning `restore_as2`. Other sessions share that directory, so this is coordinated with the owner and never done
   from this worktree. Do not re-create the vendor junction here.

`patches/agentsociety2/README.md` is updated as in "Design chapters".

### `src/worldmock/model_endpoint.py`

- **Import** `time` (module level).
- **Constants** after `_LOGGER`:

  ```python
  #: 元数据读取整次重试的等待秒数（至多五次读取）。
  _METADATA_FETCH_WAITS = (2.0, 4.0, 8.0, 16.0)
  #: 视为服务暂时不可用、整次重试的 HTTP 状态。
  _METADATA_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
  ```

- **`load_model_metadata`** (`:28-43`). `:35-37` become `payload = _fetch_metadata(config.metadata_url, secret)` and
  `metadata = parse_model_metadata(config, payload)`. The except clause and its reason are unchanged. The @brief adds
  「暂时性失败整次重试，至多五次」.
- **New `_get_metadata(url: str, secret: str) -> object`.** @brief 「一次读取元数据：GET、核对状态并解析 JSON，任何一步的
  requests 异常原样抛出。」

  ```python
      response = requests.get(url, headers={"Authorization": f"Bearer {secret}"}, timeout=30)
      response.raise_for_status()
      return response.json()
  ```

- **New `_fetch_metadata(url: str, secret: str) -> object`.** @brief 「整次读取元数据（GET、状态与 JSON）；连接、超时、
  429 与 5xx 按固定间隔重试，至多五次；用尽或其他失败时写英文错误日志并抛出最后一次错误。」

  ```python
      for tries, wait in enumerate(_METADATA_FETCH_WAITS, start=1):
          try:
              return _get_metadata(url, secret)
          except requests.RequestException as error:
              if not _transient_metadata_error(error):
                  _LOGGER.error("Model metadata fetch failed after %d tries: %s", tries, type(error).__name__)
                  raise
              _LOGGER.warning("Model metadata fetch failed (%s); retrying in %.0f s", type(error).__name__, wait)
              time.sleep(wait)
      try:
          return _get_metadata(url, secret)
      except requests.RequestException as error:
          _LOGGER.error("Model metadata fetch failed after %d tries: %s", len(_METADATA_FETCH_WAITS) + 1,
                        type(error).__name__)
          raise
  ```

  The loop makes the first four tries; the fifth follows the last wait. Every line is reachable: the five-failure test
  covers the final `except`, the 401 test the loop's.
- **New `_transient_metadata_error(error: requests.RequestException) -> bool`.** @brief
  「连接（含 TLS 与代理）、超时、响应体中断及 429/5xx 视为暂时性失败。」

  ```python
      if isinstance(error, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)):
          return True
      response = error.response if isinstance(error, requests.HTTPError) else None
      return response is not None and response.status_code in _METADATA_RETRY_STATUSES
  ```

- **New `frozen_metadata_loader(source: Mapping[str, Any]) -> Callable[[ModelConfig], ModelMetadata]`.** @brief
  「恢复、重放与打开已有运行的元数据来源：读运行清单冻结的 model_metadata，不访问模型服务；键缺失或值不合法时拒绝。」

  ```python
      from worldmock.errors import IntegrityError

      try:
          metadata = ModelMetadata.model_validate(source["model_metadata"])
      except (KeyError, TypeError, ValidationError) as error:
          _LOGGER.error("Frozen model metadata is missing or invalid")
          raise IntegrityError("Frozen model metadata is missing or invalid") from error
      return lambda _config: metadata
  ```

  Module-level imports: `from collections.abc import Callable, Mapping` and `from typing import Any`. The local import
  of `worldmock.errors` follows `load_model_metadata`'s (`:30`) and is registered in `DELAYED_IMPORTS` (Tests).

### `src/worldmock/runtime.py`

- **Imports.**
  - `:5` becomes `from worldmock.model_endpoint import IndependentModelClient, frozen_metadata_loader`.
  - Add `import time` and `from worldmock.status import DriverHeartbeat`, both at module level. `worldmock.status`
    imports only `footprints.assets`, `footprints.models` and `errors`, so importing `worldmock.runtime` still loads no
    engine module and no AS2 (`tests/system/cli/test_cli.py:62-70`).
- **`_prepare_resume`** (`:222-259`). The body follows the order in Behaviour. Lines `:236-251` become:

  ```python
      destination = Path(request.run_dir).resolve()
      _require_run_shell(destination)
      root = Path(request.environment_root).absolute()
      digest = _intervention_sha256(intervention) if intervention is not None else None
      metadata = {"branch_kind": "counterfactual", "intervention_sha256": digest} if digest else None
      parent = FootprintStore(Path(request.parent_run_dir))
      try:
          checkpoint = parent.get_checkpoint(request.checkpoint_id)
          WorldState.model_validate(checkpoint.state)
          inputs = load_environment(root, metadata_loader=frozen_metadata_loader(parent.manifest))
          run_setup._configure_process(request.project_root, inputs.config.model)
          _require_runtime_identity(parent.manifest, Path(request.project_root), inputs)
          require_environment_identity(parent.manifest, inputs)
          manifest, state = _restore_branch(parent, checkpoint, destination, metadata, inputs)
      finally:
          parent.close()
  ```

  The @brief becomes 「先打开父运行、取到检查点并校验检查点世界，再用父清单冻结的模型元数据加载环境（不访问模型服务）、
  配置进程、核对身份，最后原子恢复工作区与世界；父运行的库、清单或检查点不对时，不加载环境、不配置进程、不复制任何文件。」
- **`_execute`** (`:443-480`). The whole body after the local imports, the `try`/`finally` with `_cleanup_runtime`
  included, is indented under `with DriverHeartbeat(prepared.run_dir):`. It is 39 lines afterwards, and the longest
  re-indented line is 107 characters. The @brief adds 「运行期间驱动进程每 60 秒写一次心跳」.
- **`_generate_day_plan`** (`:635-668`).
  - `:654` becomes `raw, attempts = await _call_plan_day(engine, caller, request, (evidence, link, agent_id))`.
  - The two `response_draft(now, agent_id, link, raw, …)` calls (`:658`, `:665`) pass `(*link, attempts)` as `link`.
- **`_call_plan_day`** (`:671-684`). The new return type is `tuple[dict, tuple[dict, ...]]`. Its @brief:
  「发出一次零点计划请求，返回原始响应与这次调用的失败发送，并写调用耗时运维日志；失败时先提交已有尝试的证据与带失败发送的
  失败证据再抛出。」 The body is written on top of the store-commits chapter's end state (its `append_evidence(evidence)`
  call and the deleted `validated_payload` import):

  ```python
      from agentsociety2.config.llm_dispatcher import collect_transport_attempts
      from worldmock.life import day_planning

      evidence, link, agent_id = context
      now, started = engine.context.state.current_time, time.perf_counter()
      with collect_transport_attempts() as collected:
          try:
              raw = await caller.call(request)
          except BaseException as error:
              logger.error("Plan day model request failed: %s", type(error).__name__)
              _log_plan_day_call(engine, (link[0], agent_id), started, tuple(collected), "failure")
              evidence.append(day_planning.failure_draft(now, agent_id, (*link, tuple(collected)), error))
              engine.context.store.append_evidence(evidence)
              raise
      _log_plan_day_call(engine, (link[0], agent_id), started, tuple(collected), "response")
      return raw, tuple(collected)
  ```

- **New `_log_plan_day_call(engine: Any, ids: tuple[str, int], started: float, attempts: tuple, outcome: str) -> None`.**
  @brief 「零点计划调用的运维日志：调用耗时毫秒数与结果；有失败发送时另写一条传输等待记录。」

  ```python
      from worldmock.opslog import transport_wait_record

      call_id, agent_id = ids
      ops, now = engine.context.ops, engine.context.state.current_time
      ops.log(now, "agent_runtime", "error" if outcome == "failure" else "info",
              {"code": "PLAN_DAY_CALL", "detail": f"Plan day call {call_id} ended with {outcome}",
               "agent_id": agent_id, "call_id": call_id, "outcome": outcome,
               "call_ms": round((time.perf_counter() - started) * 1000, 1)})
      if attempts:
          ops.log(now, "agent_runtime", "warn", {**transport_wait_record(call_id, attempts), "agent_id": agent_id})
  ```

### `src/worldmock/opslog.py`

**New `transport_wait_record(call_id: str, attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]`** after
`OPS_LEVELS` (`:15`). @brief 「一次模型往来在结果之前失败发送的运维记录正文：失败次数、累计等待毫秒数与最后一次的错误链。」

```python
    waited = sum(item["wait_ms"] for item in attempts if item["wait_ms"] is not None)
    return {"code": "MODEL_TRANSPORT_WAIT",
            "detail": f"Model call {call_id} failed {len(attempts)} sends and waited {waited} ms",
            "call_id": call_id, "failed_sends": len(attempts), "waited_ms": waited,
            "last_error": attempts[-1]["error"]}
```

### `src/worldmock/life/model_evidence.py`

This is written on top of [03-recorded-evidence.md](03-recorded-evidence.md) (the `data` dict and `_request_cause`) and
[01-store-commits.md](01-store-commits.md) (`append_evidence`).

- **Imports.** `import time`; `from worldmock.opslog import transport_wait_record`.
- **Constant:** `#: 失败发送记录的字段闭集。` `_ATTEMPT_FIELDS = frozenset({"attempt", "error", "failed_after_ms",
  "wait_ms"})`.
- **New module function `_attempt_fields(exchange: Any) -> dict`.** @brief 「核对证据带来的失败发送并给出写入信封的
  字段：请求证据不得带，返回空；响应与失败证据每项恰含四个字段、序号从 1 起连续，返回 transport_attempts 的副本。」

  ```python
      attempts = exchange.transport_attempts
      if exchange.phase == "request" and attempts:
          logging.error("Model request evidence must not carry transport attempts: %s", exchange.call_id)
          raise ProtocolError("Model request evidence must not carry transport attempts")
      valid = isinstance(attempts, tuple) and all(
          isinstance(item, dict) and set(item) == _ATTEMPT_FIELDS for item in attempts)
      if not valid or [item["attempt"] for item in attempts] != list(range(1, len(attempts) + 1)):
          logging.error("Model exchange transport attempts are invalid: %s", exchange.call_id)
          raise ProtocolError("Model exchange transport attempts are invalid")
      if exchange.phase == "request":
          return {}
      return {"transport_attempts": [dict(item) for item in attempts]}
  ```

- **`record_model_exchange`** (`:29-77`). Directly after the protocol_feedback copy (`:56-57`, kept by chapter 03) and
  before the draft, add `data.update(_attempt_fields(exchange))`. The validation therefore runs before the store
  write, after chapter 03's `_request_cause`, which changes no state before the write.

  Around the store write, add `started = time.perf_counter()` directly before it and
  `commit_ms = round((time.perf_counter() - started) * 1000, 1)` directly after it. The last line becomes
  `self._ops_exchange(context, exchange, commit_ms)`. The @brief adds 「响应与失败证据在信封顶层带 transport_attempts，
  不进 response」. Chapter 03 leaves the function at about 45 lines; these three added lines keep it under the 50-line
  limit, and the `data` dict stays in this function (chapter 03's mutation `model_response_keeps_request_copy` targets
  it there).
- **`_ops_exchange(self, context, exchange, commit_ms: float)`** (`:79-94`). @brief 「写模型交换运维日志（模型名、用量与
  这次证据写入耗时）；有失败发送时写传输等待，响应带协议纠错时写协议拒绝。」

  ```python
          response = exchange.response if isinstance(exchange.response, dict) else {}
          usage = response.get("usage") or {}
          ids = {"agent_id": context.agent_id, "decision_id": context.decision_id}
          ops, now = self.context.ops, self.context.state.current_time
          ops.log(now, "agent_runtime", "error" if exchange.phase == "failure" else "info",
                  {"code": "MODEL_EXCHANGE", "detail": f"Model {exchange.phase} recorded for call {exchange.call_id}",
                   **ids, "model": response.get("model"), "tokens_in": usage.get("prompt_tokens"),
                   "tokens_out": usage.get("completion_tokens"), "commit_ms": commit_ms})
          if exchange.transport_attempts:
              ops.log(now, "agent_runtime", "warn", {**transport_wait_record(exchange.call_id,
                                                                              exchange.transport_attempts), **ids})
          feedback = response.get("protocol_feedback")
          if exchange.phase == "response" and isinstance(feedback, dict):
              ops.log(now, "agent_runtime", "warn",
                      {"code": "MODEL_PROTOCOL_REJECTED", **ids, "call_id": exchange.call_id,
                       "detail": f"Model response for call {exchange.call_id} was rejected: {feedback['error_code']}",
                       "error_code": feedback["error_code"]})
  ```

### `src/worldmock/life/day_planning.py`

- **`response_draft`** (`:178-186`). `link` becomes `tuple[str, int, str, tuple[dict, ...]]`, unpacked as
  `call_id, attempt, request_event_id, transport_attempts = link`. `data` gains
  `"transport_attempts": list(transport_attempts)`. The @brief adds 「及这次调用的失败发送」.
- **`failure_draft`** (`:189-195`). The same change to `link` and `data`.
- [06-host-costs-and-model-cost.md](06-host-costs-and-model-cost.md) moves `PLAN_DAY_PURPOSE` out of this file. The
  two functions keep using the name through that chapter's import.

### `src/worldmock/life/world_setup.py`

- **Imports.** `:20` becomes `from worldmock.life.run_identity import require_environment_identity`. Add
  `from worldmock.model_endpoint import frozen_metadata_loader`.
- **`_run_manifest`** (`:84-100`). The returned dict gains `"lineage": []`. The @brief adds 「新建运行的 lineage 为空」.
- **`open_world`** (`:154-157`):

  ```python
  def open_world(environment: EnvironmentInputs | Path, run_dir: Path):
      """@brief 已有运行先打开存储；从目录加载环境时模型元数据取运行清单冻结的那一份，不访问模型服务；再核对目录及两个
      原文字节身份，不重建初值或重复运行开始回调。"""
      store = FootprintStore(Path(run_dir))
      try:
          inputs = (environment if isinstance(environment, EnvironmentInputs)
                    else load_environment(environment, metadata_loader=frozen_metadata_loader(store.manifest)))
          require_environment_identity(store.manifest, inputs)
      except Exception:
          logging.error("Existing run could not be opened")
          store.close()
          raise
      return inputs, store
  ```

### `src/worldmock/life/run_identity.py`

- **`require_environment_identity`** (`:15-25`). Delete the `model_metadata` term (`:22`). The @brief becomes
  「在读取已有运行状态前核对运行清单结构版本、目录、人物与配置；模型元数据由清单本身提供（model_endpoint.frozen_metadata_loader），
  不再比较。」
- **Delete** `open_environment_validated_store` (`:28-37`) and the now-unused imports (`Path`, `FootprintStore`).

### `src/worldmock/life/models.py`

`:30-32`: `RUN_MANIFEST_SCHEMA_VERSION = 7`. The comment is unchanged (it already states the rule).

### `src/worldmock/errors.py` and `src/worldmock/catalog/errors.py`

Each class gets one method after `__init__`:

```python
    def __reduce__(self) -> tuple:
        """@brief 按构造参数重建并带回实例字典，使异常穿过进程（Ray）后保留所属文件、错误码、原因与附加说明。"""
        return type(self), (self.path, self.code, self.reason), self.__dict__
```

- **`EnvironmentInputError`** (`errors.py:20-26`): as above.
- **`PreconditionError`** (`errors.py:37-52`): `return type(self), (self.args[0], self.unmet, self.suggested,
  self.public_reason), self.__dict__`, @brief 「…保留错误码、未满足项、建议动作、公开原因与附加说明。」
- **`RegistrationError`** (`catalog/errors.py:49-57`): `return type(self), (self.code, self.detail), self.__dict__`,
  @brief 「…保留登记码、详情与附加说明。」
- Unpickling calls the constructor with the arguments and then `BaseException.__setstate__` with the dictionary, which
  restores the same attributes plus anything set later.
- `WorldMockError.__init__` (`errors.py:10-13`) is unchanged.

### `src/worldmock/status.py`

- **Imports.** `os`, `re`, `threading`, `from typing import Any`. The file is written on top of chapter 04's
  `status.py` (no `BEGIN`, `event_type_counts`, the indexed usage statement).
- **Constants:**

  ```python
  #: 驱动心跳文件名与写入间隔（秒）。
  HEARTBEAT_FILE = "heartbeat.json"
  HEARTBEAT_INTERVAL_SECONDS = 60
  #: status.json 仍是 running 而心跳超过这么多个间隔未更新时，status 报告驱动已失联（stale）。
  HEARTBEAT_STALE_INTERVALS = 3
  #: Ray 等写进异常文本的终端颜色序列。
  _ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
  ```

  Also `_FIRST_EVENT_SQL` and `_LAST_EVENT_SQL`, as in Behaviour.
- **`write_status(run_dir, status, phase, error_type=None, error_message=None)`** (`:19-25`). The written dict is
  `{"status": status, "phase": phase, "error_type": error_type, "error_message": error_message}`. The @brief adds
  「失败时另写一行可读原因 error_message」.
- **New `failure_message(error: BaseException) -> str`.** @brief 「status.json 的 error_message：一行可读失败原因。
  异常组逐项以「 | 」连接；带 cause 属性且其值是另一异常的（Ray 任务错误）取该异常；其余为「类型名: 文本」，去掉终端颜色
  序列、连续空白折成一个空格，不截断。」

  ```python
      if isinstance(error, BaseExceptionGroup):
          return " | ".join(failure_message(item) for item in error.exceptions)
      cause = getattr(error, "cause", None)
      if isinstance(cause, BaseException) and cause is not error:
          return failure_message(cause)
      return " ".join(_ANSI_ESCAPE.sub("", f"{type(error).__name__}: {error}").split())
  ```

- **New `class DriverHeartbeat`:**

  ```python
  class DriverHeartbeat:
      """@brief 驱动进程心跳：进入时立即原子写一次 heartbeat.json，之后后台守护线程每 60 秒重写；写失败记录错误并继续；
      退出时停止线程，文件保留。"""

      def __init__(self, run_dir: Path) -> None:
          """@brief 绑定心跳文件、驱动开始时刻、停止信号与后台线程。"""
          self._path = Path(run_dir) / HEARTBEAT_FILE
          self._started_at = datetime.now(timezone.utc).isoformat()
          self._stop = threading.Event()
          self._thread = threading.Thread(target=self._run, name="worldmock-heartbeat", daemon=True)

      def __enter__(self) -> "DriverHeartbeat":
          """@brief 立即写一次心跳并启动后台线程。"""
          self._beat()
          self._thread.start()
          return self

      def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
          """@brief 停止后台线程，不吞掉运行中的异常；心跳文件保留。"""
          self._stop.set()
          self._thread.join(HEARTBEAT_INTERVAL_SECONDS)

      def _run(self) -> None:
          """@brief 每个间隔重写一次心跳，直到停止信号。"""
          while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
              self._beat()

      def _beat(self) -> None:
          """@brief 原子写一次心跳；写失败记录英文错误日志后继续，不让心跳线程退出。"""
          record = {"heartbeat_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
                    "started_at": self._started_at}
          try:
              atomic_write(self._path, canonical_json(record))
          except Exception as error:
              logger.error("Driver heartbeat write failed: %s", error)
  ```

  `HEARTBEAT_INTERVAL_SECONDS` is read at call time, so tests can set it. `atomic_write` creates the parent directory
  (`assets.py:17`), so the first beat never depends on another writer.
- **`read_status`** (`:28-56`). `:53` (`result["wall_time"] = …`) becomes
  `result.update(_liveness_view(connection, root, result["runtime"]))`. The @brief adds 「心跳、活性与等待模型的起点；
  首末事件按主键读取」.
- **New `_liveness_view(connection: sqlite3.Connection, root: Path, runtime: dict | None) -> dict`.** @brief 「心跳、活性、
  等待模型的起点与墙钟，首末事件按主键读取，不扫描事件表。」

  ```python
      heartbeat = _heartbeat(root)
      first = connection.execute(_FIRST_EVENT_SQL).fetchone()
      last = connection.execute(_LAST_EVENT_SQL).fetchone()
      last_type = last[1] if last else None
      liveness = _liveness(runtime, heartbeat, last_type)
      return {"heartbeat": heartbeat, "liveness": liveness,
              "waiting_for_model_since": last[0] if last_type == "model_request" else None,
              "wall_time": _wall_time(first[0] if first else None, last[0] if last else None, liveness, heartbeat)}
  ```

- **New `_heartbeat(root: Path) -> dict | None`.** It reads with `read_text(encoding="utf-8")`
  (`tests/unit/footprints/test_status.py:64-79` forbids default-encoding reads):

  ```python
      path = root / HEARTBEAT_FILE
      if not path.exists():
          return None
      try:
          beat = json.loads(path.read_text(encoding="utf-8"))
          age = (datetime.now(timezone.utc) - datetime.fromisoformat(beat["heartbeat_at"])).total_seconds()
      except (ValueError, KeyError, TypeError) as error:
          logger.error("Run heartbeat is invalid: %s", error)
          raise IntegrityError("Run heartbeat is invalid") from error
      return {**beat, "age_seconds": round(max(0.0, age), 3)}
  ```

  @brief 「读取驱动心跳并加上距今秒数；文件不存在为空；内容不是 JSON 对象或心跳时刻不可读时写英文错误日志并拒绝，读文件的
  系统错误原样传播。」 `UnicodeDecodeError` and `json.JSONDecodeError` are `ValueError`s; a non-object or a naive time
  gives `TypeError`.
- **New `_liveness(runtime: dict | None, heartbeat: dict | None, last_event_type: str | None) -> str | None`.** @brief
  「运行活性：status.json 缺失为空；不是 running 为 stopped；心跳缺失或超过三个间隔为 stale（驱动进程已不在，不表示世界停在
  哪里）；最后一条事件是模型请求为 waiting_for_model；其余为 running。」

  ```python
      if runtime is None:
          return None
      if runtime["status"] != "running":
          return "stopped"
      if heartbeat is None or heartbeat["age_seconds"] > HEARTBEAT_STALE_INTERVALS * HEARTBEAT_INTERVAL_SECONDS:
          return "stale"
      if last_event_type == "model_request":
          return "waiting_for_model"
      return "running"
  ```

- **`_wall_time`** (`:131-141`, after chapter 04 the function that runs the two primary-key reads) becomes a pure
  function of the values `_liveness_view` read:

  ```python
  def _wall_time(started: str | None, last: str | None, liveness: str | None, heartbeat: dict | None) -> dict:
      """@brief 墙钟：起点为第一条事件的落盘时刻；运行中与等待模型时止于此刻，失联时止于最后一次心跳，其余止于最后一条
      事件；另报最后一条事件距今秒数。没有事件时四项都为空。"""
      if started is None:
          return {"started_at": None, "last_event_at": None, "elapsed_seconds": None, "last_event_age_seconds": None}
      start, end, now = datetime.fromisoformat(started), datetime.fromisoformat(last), datetime.now(timezone.utc)
      if liveness in ("running", "waiting_for_model"):
          stop = now
      elif liveness == "stale" and heartbeat is not None:
          stop = datetime.fromisoformat(heartbeat["heartbeat_at"])
      else:
          stop = end
      return {"started_at": start.isoformat(), "last_event_at": end.isoformat(),
              "elapsed_seconds": max(0, (stop - start).total_seconds()),
              "last_event_age_seconds": round(max(0.0, (now - end).total_seconds()), 3)}
  ```

  `elapsed_seconds` keeps today's unrounded form.
- [02-state-and-checkpoints.md](02-state-and-checkpoints.md) changes `status.py:39-41` and `:106` (checkpoint ids,
  ordered activities); [04-history-paths.md](04-history-paths.md) changes `:35`, `:37-38`, `:83-98` and `:131-141`.
  This chapter touches only the lines named above, and its `_wall_time` replaces chapter 04's body of that function.

### `src/worldmock/cli.py`

- `:17` imports `failure_message` as well.
- `:120` becomes `write_status(output, state, "stopped", type(error).__name__, failure_message(error))`.

### `src/worldmock/footprints/assets.py`

- **Import** `time`.
- **New constant and function** before `atomic_write`:

  ```python
  #: Windows 上同步或杀毒软件短暂占用目标时 os.replace 被拒绝访问，按这些间隔（秒）重试（issue #7），共 12.75 秒。
  _REPLACE_RETRY_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4)


  def _replace(temporary: Path, path: Path) -> None:
      """@brief 原子替换；仅 Windows 的 PermissionError 按固定间隔重试，用尽后抛出最后一次的错误，其他平台与其他错误立即抛出。"""
      for delay in _REPLACE_RETRY_SECONDS:
          try:
              os.replace(temporary, path)
              return
          except PermissionError:
              if os.name != "nt":
                  raise
              logging.warning("Atomic replace denied, retrying in %.2fs: %s", delay, path.name)
              time.sleep(delay)
      os.replace(temporary, path)
  ```

- **`atomic_write`**: `:25` becomes `_replace(temporary, path)`.

### `src/worldmock/footprints/store.py`

Two read methods, after `read_event` (`:237-246`). [01-store-commits.md](01-store-commits.md) owns the write path, and
the whole-history chapter owns the other read methods.

```python
    def read_last_event(self) -> Event:
        """@brief 按主键读取最后一条已提交事件；空库没有最后事件，写英文错误日志并拒绝。"""
        row = self.connection.execute("SELECT body FROM events ORDER BY event_seq DESC LIMIT 1").fetchone()
        if row is None:
            logging.error("Run has no committed event")
            raise IntegrityError("Run has no committed event")
        return Event.model_validate_json(row[0])

    def read_terminal_event(self, after: int) -> Event | None:
        """@brief 经人物与类型索引读取序号大于 after 的最后一条 run_failed 或 run_ended（两者都没有人物）；没有时为空。"""
        row = self.connection.execute("""
            SELECT body FROM events WHERE json_extract(body,'$.agent_id') IS NULL
            AND json_extract(body,'$.event_type') IN ('run_failed','run_ended') AND event_seq>?
            ORDER BY event_seq DESC LIMIT 1
        """, (after,)).fetchone()
        return Event.model_validate_json(row[0]) if row else None
```

If [03-recorded-evidence.md](03-recorded-evidence.md) routes every row through a single decoder (`_decode`), both
methods use it instead of `Event.model_validate_json`.

### `src/worldmock/footprints/checkpoint.py`

- **Imports.** `from datetime import datetime, timezone`, and `Checkpoint` from `worldmock.footprints.models`.
- **New `branch_lineage(store: FootprintStore, checkpoint: Checkpoint) -> list[dict]`.** @brief 「子分支的 lineage：父运行
  的 lineage 加一项描述父运行这一段的条目，按段先后。」 Body:
  `return [*store.manifest["lineage"], lineage_entry(store, checkpoint)]`.
- **New `lineage_entry(store: FootprintStore, checkpoint: Checkpoint) -> dict`.** @brief 「父运行这一段的 lineage 条目：
  从哪一模拟时刻开始、在哪个检查点分出、走到哪里、为什么结束、丢弃多少事件、子分支何时分出；只做主键与人物类型索引上的
  有界查询。」

  ```python
      earlier = store.manifest["lineage"]
      last = store.read_last_event()
      terminal = store.read_terminal_event(earlier[-1]["checkpoint_event_seq"] if earlier else 0)
      if terminal is None:
          reason, error = "no terminal event", None
      else:
          reason = terminal.event_type
          error = terminal.data["error"] if reason == "run_failed" else None
      return {"run_id": store.manifest["run_id"],
              "simulated_from": earlier[-1]["checkpoint_time"] if earlier else store.manifest["window"]["start"],
              "checkpoint_id": checkpoint.checkpoint_id, "checkpoint_event_seq": checkpoint.last_event_seq,
              "checkpoint_time": checkpoint.current_time, "last_event_seq": last.event_seq,
              "last_event_time": last.occurred_at, "last_recorded_at": last.recorded_at,
              "discarded_events": last.event_seq - checkpoint.last_event_seq,
              "end_reason": reason, "end_error": error,
              "branched_at": datetime.now(timezone.utc).isoformat()}
  ```

- **`_populate_branch`** (`:117-143`). The `manifest.update(...)` at `:122-124` gains
  `lineage=branch_lineage(store, checkpoint)`. `:125` (`source_run_ids`) is unchanged. The whole-history chapter
  replaces the event copy in this function; whatever builds the branch manifest there must keep this keyword.
- `lineage_entry` reads `manifest["lineage"]` and `manifest["window"]["start"]` without a default: every run manifest
  of structure 7 has both, and `_prepare_resume` refuses any other structure before `_restore_branch`
  (`require_environment_identity`). The two unit-test stores that branch from a hand-written manifest gain both keys
  (see "Existing tests that change").

### `src/worldmock/footprints/export.py`

`_export_identity` (`:141-159`). The returned dict gains `"lineage": run["lineage"]`. The @brief adds 「并带运行清单的
lineage」. `EXPORT_SCHEMA_VERSION` is set to 8 by [03-recorded-evidence.md](03-recorded-evidence.md). This chapter adds
one clause to its comment (`:30`): 「顶层带 lineage」.

### `src/worldmock/footprints/quality.py`

- **`build_quality_report`** (`:56-98`). Insert `"lineage": _lineage_quality(manifest),` after `"acceleration"`
  (`:90`). `"schema_version": 9` at `:73`, shared with [06-host-costs-and-model-cost.md](06-host-costs-and-model-cost.md).
  Add the sentence 「lineage 节报告父运行各段的结束原因与停机秒数」 to the docstring.
- **`_validate_inputs`** (`:101-124`). Directly after the run-window check (`:113-114`), so that the existing
  identity and window refusals keep their own messages, add
  `if not isinstance(manifest.get("lineage"), list): raise IntegrityError("Quality report manifest has no lineage")`
  (two lines).
- **New `_lineage_quality(manifest: dict) -> dict`.** @brief 「父运行各段的结束原因、丢弃事件数与停机秒数（分出时刻减父运行
  最后一条事件的落盘时刻），只读冻结清单的 lineage，不计入 transport_or_terminal_failures。」

  ```python
      parents = []
      for entry in manifest["lineage"]:
          downtime = (datetime.fromisoformat(entry["branched_at"])
                      - datetime.fromisoformat(entry["last_recorded_at"])).total_seconds()
          parents.append({"run_id": entry["run_id"], "checkpoint_id": entry["checkpoint_id"],
                          "end_reason": entry["end_reason"], "end_error": entry["end_error"],
                          "discarded_events": entry["discarded_events"], "downtime_seconds": round(downtime, 3)})
      reasons = Counter(row["end_reason"] for row in parents)
      return {"segment_count": len(parents) + 1, "parents": parents,
              "end_reason_counts": {reason: reasons[reason] for reason in sorted(reasons)},
              "downtime_seconds": round(sum((row["downtime_seconds"] for row in parents), 0.0), 3),
              "method": "Parent segments read from the frozen run manifest lineage; downtime is branched_at minus "
                        "the parent's last recorded event"}
  ```

### `src/worldmock/footprints/validation.py`

`_validate_identity` (`:84-112`). After `_validate_run_identity(frozen)` (`:90`):

```python
    if not isinstance(frozen.get("lineage"), list) or manifest.get("lineage") != frozen["lineage"]:
        logging.error("Export lineage disagrees with the frozen run manifest")
        raise IntegrityError("Export lineage disagrees with the frozen run manifest")
```

### `custom/agents/life_agent.py`

- **Import** `collect_transport_attempts` from `agentsociety2.config.llm_dispatcher`, at module level next to the other
  AS2 imports (`:30-38`).
- **`_dispatch_memory_llm`** (`:381-401`). `:392-401` become:

  ```python
          with collect_transport_attempts() as attempts:
              try:
                  response = await self._dispatcher.call(**request)
              except Exception as error:
                  logging.exception("LifeAgent memory model dispatch failed")
                  await self._record_model_evidence(ModelCallEvidence(
                      context, call_id, "failure", 1, request, error=repr(error), transport_attempts=tuple(attempts)))
                  raise
          await self._record_model_evidence(ModelCallEvidence(
              context, call_id, "response", 1, request, response=self._serialize_model_response(response),
              transport_attempts=tuple(attempts)))
          return response
  ```

  The @brief adds 「失败发送随证据登记」.

### `custom/envs/life_env.py`

- **Import** `close_cached_async_clients` from `agentsociety2.config.llm_dispatcher`, at module level.
- **`close`** (`:285-294`):

  ```python
      async def close(self) -> None:
          """@brief 在本 actor 的事件循环里关闭并移出 LiteLLM 缓存的异步客户端（零点计划用过的），再关闭持久连接并释放环境实例。"""
          try:
              await close_cached_async_clients()
          finally:
              if self.engine is not None:
                  try:
                      self.engine.close()
                  except Exception as close_error:
                      self._record_close_failure(close_error)
                      raise
                  else:
                      self.engine = None
  ```

### Data formats recorded by this chapter

| Where | Form |
| --- | --- |
| `model_response.data.transport_attempts` | array of `{attempt, error, failed_after_ms, wait_ms}`, `[]` when the first send succeeded; on `model_request` the key is absent |
| plan_day `model_response.data.transport_attempts` | the same |
| `status.json` | `canonical_json({"error_message", "error_type", "phase", "status"})` |
| `heartbeat.json` | `canonical_json({"heartbeat_at", "pid", "started_at"})` |
| run `manifest.json` `lineage` | ordered array of lineage entries; `[]` for a fresh run; structure 7 |
| export `manifest.json` `lineage` | equal to `frozen_run_manifest.lineage`; export structure 8 |
| `quality.json` `lineage` | `{downtime_seconds, end_reason_counts, method, parents, segment_count}`; structure 9 |
| `logs/ops-*.jsonl` | `MODEL_EXCHANGE.commit_ms`; `MODEL_TRANSPORT_WAIT`; `MODEL_PROTOCOL_REJECTED`; `PLAN_DAY_CALL` |

All times written by this chapter are UTC `isoformat()` strings (wall clock) or integer epoch seconds (simulated). The
comparator masks the wall ones (see "Not in this chapter").

## Design chapters

Design text changes before code. The substance below is the text to write (Chinese), in the section named.

### `docs/design/08-configuration.md`「模型配置与凭据」

- Table rows:
  - `max_retries`: 「默认 `0`，严格整数 `0–10`，可重试非传输服务拒绝；限流 429 用尽后停放重发」.
  - `transport_max_retries`: 「默认 `3`，严格整数 `0–10`，连接、超时（含单次发送整次期限）与服务暂不可用；用尽后停放重发」.
- **Replace** the paragraph 「凭据值不进入文件……不合成成功或跳过模拟时间。」 (`:140-143`) with:
  - 凭据值不进入文件、清单、提示或日志。两类服务重试由同一个 dispatcher 循环计数，每次失败只消耗一类预算，计数内的退避不变：
    传输失败第 n 次（从 0 起）等 2·2ⁿ 秒；限流与其他过载等 2ⁿ 秒、至多 60 秒，429 不短于服务给出的 `Retry-After`。
  - 计数用尽后，传输失败与限流 429 不使运行失败而是停放：同一请求原样重发，两次发送之间按同一指数退避、至多 120 秒（429 不短于
    `Retry-After`），从停放开始至多十二小时；无论计数内还是停放中，任何一次等待长于十二小时都立即失败。十二小时、120 秒
    与整次期限都是引擎常量，随代码身份冻结，不在 `[model]` 里，也不是命令行参数。
  - 停放期间模拟时间不前进，发送的字节与第一次相同，所以停放过的运行与一次没断网、拿到同样回答的运行结算相同。
  - 十二小时内仍未成功才失败，错误写明发送次数、耗时、最后的错误类型和根因异常链（只有类型名，不含服务返回的正文）。
  - 其他普通失败（含 HTTP 502 `BadGatewayError`）在 `max_retries` 用尽后失败；鉴权与参数错误立即失败；底层隐式重试关闭。
  - 每次发送受整次期限约束：120 秒加请求 `max_tokens` 每个 7.5 毫秒（16,000 时 240 秒，零点计划的 8,000 时 180 秒）。到期按
    传输超时计，重发同一请求；这是同一请求会得到新回答的唯一情形，只在服务卡住时发生。
  - 输出纠正是新的逻辑请求，各自受同一规则约束。

### `docs/design/08-configuration.md`「权威上下文核验」

**Replace** the paragraph 「纯环境校验允许只读元数据 HTTP……预留输出满足服务输出上限。」 (`:153-154`) with:
- 纯环境校验允许只读元数据 HTTP，不发生成请求。连接（含 TLS）、超时、响应体中断与 429/5xx 整次重试，至多五次，间隔 2、4、8、
  16 秒；鉴权失败、内容不合法与容量不足不重试。
- 新运行在驱动与环境 actor 各读一次并冻结核验值与来源。
- 恢复与重放不访问模型服务：驱动与环境 actor 都读父运行清单冻结的 `model_metadata`，与每个提示侧读者本来就读的那一份相同。
  服务容量在一条链的中途变小时，恢复不会提前发现，第一次模型调用以参数错误失败。
- 发送容量与浓缩阈值取已核验实际总容量，预留输出满足服务输出上限。

### `docs/design/06-agent.md`「宿主模型协议」

**Replace** 「普通失败和传输失败只消耗对应的一种预算，底层隐式重试关闭；身份或参数错误立即失败。」 (`:312`) with:
- 普通失败和传输失败只消耗对应的一种预算；计数用尽后传输失败与限流停放重发同一请求，规则见
  [配置](08-configuration.md)「模型配置与凭据」；身份或参数错误立即失败。
- 每次模型往来的响应与失败证据在信封 `data` 顶层带 `transport_attempts`：这次调用在结果之前失败的每一次发送，按顺序各一项
  `{attempt, error, failed_after_ms, wait_ms}`，依次是发送序号、根因异常链（只有类型名）、距第一次发送的毫秒数、到下一次
  发送的等待毫秒数（结束调用的那次为 null）。一次就成功时是空数组；请求证据不带它。
- 它不进 `response`，所以后续提示从已保存响应重建的内容（同秒续接、自述、进度层的被拒记录）与没有断网时逐字相同。零点计划的
  响应与失败证据同样带它。

### `docs/design/10-runtime.md`「实际框架边界」

- **First paragraph** (`:5-7`).
  - 「完整环境纯加载先核对人设、运行配置、目录与实际模型元数据」 becomes 「……与模型元数据（新运行读实际服务，恢复与重放读父运行
    清单冻结的那一份）」.
  - 「恢复时父运行的数据库或检查点不存在，仍然先于进程配置报出」 becomes 「恢复先打开父运行、取到检查点并校验检查点世界，然后才
    加载环境、配置进程和复制任何文件；父运行的数据库、清单或检查点不对时不访问网络、不建分支目录」.
- **After** 「生成请求与后台日计划、反思共用相同 dispatcher 和冻结身份，输出纠错是独立逻辑请求。」 add:
  - 停放与单次发送整次期限的规则归[配置](08-configuration.md)。
  - AgentSociety2 worker 任务在自己的事件循环结束之前，关闭并移出本循环在 LiteLLM 客户端缓存里的异步客户端；环境 actor 在 LifeEnv
    关闭时做同样的事，在 Ray 结束 actor 之前完成。

### `docs/design/10-runtime.md`「运行与恢复」

Add after the `result.json` paragraph:
- **失败诊断。** 运行目录的 `status.json` 恰含 `status`、`phase`、`error_type` 与 `error_message`。`error_message` 是一行可读的
  失败原因：异常组逐项连接，Ray 任务错误取其原始异常，其余为「类型名: 文本」。环境里的输入错误、前置条件错误与登记错误能完整
  穿过 Ray，所以这里写的是原始错误，不是 `UnserializableException`。
- **心跳。** 驱动进程在 run、resume、replay 运行期间每 60 秒原子重写 `heartbeat.json`（`heartbeat_at`、`pid`、`started_at`）。
- **`worldmock status`** 另报告：
  - `heartbeat`，带距今秒数；
  - `liveness`：`status.json` 为 running 而心跳缺失或超过 180 秒为 `stale`，表示驱动进程已不在，不表示世界停在哪里；最后一条
    事件是模型请求为 `waiting_for_model`，即停放或长思考；其余为 `running`，不在运行为 `stopped`；
  - `waiting_for_model_since`；
  - `wall_time.last_event_age_seconds`。

  墙钟在失联时停在最后一次心跳。首末事件按主键读取。心跳新鲜而最后一条事件很旧，表示驱动还在、环境或模型调用很慢；零点计划的
  请求与响应一起落盘，它的停放显示为 running。
- **写文件。** 原子写在 Windows 上遇到拒绝访问按固定间隔重试约 13 秒（issue #7）。
- **运维日志。** `logs/ops-*.jsonl` 只用于定位长运行问题，不参与审计：模型往来记录带证据写入耗时 `commit_ms`；另有传输等待
  `MODEL_TRANSPORT_WAIT`、协议拒绝 `MODEL_PROTOCOL_REJECTED` 与零点计划调用耗时 `PLAN_DAY_CALL`。

### `docs/design/09-io-formats.md`「单一状态归属」

`:5` and `:10-11`: 「运行清单结构版本为 6」 → 7, 「不是 6 的运行」 → 7, 「不是 6 时」 → 7. On `:5` the world-state version is
edited by the state-and-checkpoint chapter; take both edits.

### `docs/design/09-io-formats.md`「输入与模型身份」

- `:55-57`: the field list of a new run's manifest adds `lineage` after `window`, with the clause 「新建运行的 `lineage` 为空
  数组；恢复与重放分支另带分支来源字段，`lineage` 是父运行的 `lineage` 加一项父运行这一段，见[记录引擎](05-recording-engine.md)
  「冻结输入、检查点与分支」」.
- `:63-64` (模型身份 sentence): unchanged; the frozen budgets stay.

### `docs/design/09-io-formats.md`「事件与检查点」

`:69`: the UTF-8 sentence lists `heartbeat.json` too. Add:
- `status.json` 与 `heartbeat.json` 是规范 JSON，字段见[运行与框架集成](10-runtime.md)「运行与恢复」。
- 时刻都是 UTC 的 ISO 8601 文本。

### `docs/design/05-recording-engine.md`「冻结输入、检查点与分支」

Add a paragraph:
- 分支清单带 `lineage`：父运行清单的 `lineage` 加一项，描述父运行这一段。该项依次记：父运行身份 `run_id`；这一段的模拟起点
  `simulated_from`；分出所用检查点 `checkpoint_id`、`checkpoint_event_seq`、`checkpoint_time`；父运行在分出时的最后事件
  `last_event_seq`、`last_event_time`、`last_recorded_at`；丢弃的事件数 `discarded_events`；结束原因 `end_reason` 与
  `end_error`；分出时刻 `branched_at`。
- 结束原因取父运行这一段自己的最后一条 `run_failed` 或 `run_ended`；都没有（被结束进程或仍在运行）为 `no terminal event`。
  查询只走主键和人物类型索引。
- `source_run_ids` 仍是排好序的来源集合，顺序只由 `lineage` 表示。

### `docs/design/05-recording-engine.md`「完整导出认证」

- **Lineage in the export.** After the export structure sentence (now 8, edited by
  [03-recorded-evidence.md](03-recorded-evidence.md)), append to the reason sentence: 「导出清单顶层带 `lineage`（与冻结运行
  清单的 `lineage` 相等）也是升到 8 的原因」. Add: 「`lineage` 不是数组或与冻结运行清单不同时报
  `Export lineage disagrees with the frozen run manifest`。」
- **The quality section.** The quality structure sentence (now 9, shared with
  [06-host-costs-and-model-cost.md](06-host-costs-and-model-cost.md)) adds:
  - 质量报告的 `lineage` 节只读冻结清单：父运行各段 `parents`（身份、检查点、结束原因、错误、丢弃事件数、停机秒数 =
    分出时刻减父运行最后一条事件的落盘时刻）、按结束原因的计数、停机总秒数与段数 `segment_count`。
  - 它不计入 `transport_or_terminal_failures`，后者仍只数导出事件里失败阶段的模型证据。

### `docs/manual/runtime.md`

- **Commands block (`:17`).** After the `worldmock status` line, add a sentence:
  - status 的 `liveness` 是 `stale` 时驱动进程已经不在（例如被结束），用 `recent_checkpoints` 里的检查点恢复；
  - 是 `waiting_for_model` 时运行正在等模型服务：断网时引擎原样重发同一请求至多十二小时，不需要人工恢复；
  - `error_message` 写着失败的根因。
- **「从检查点创建分支」 (`:86`).** 「恢复生成调用核验完整模型身份和实际元数据」 becomes 「恢复与重放使用父运行冻结的模型元数据，
  不访问模型服务的 /models；检查点身份不对时在复制任何文件之前报错」.

### `patches/agentsociety2/README.md`

- **failure-propagation line.** Append 「；模型调用证据带这次调用的失败发送 `transport_attempts`」.
- **model-lifecycle line.** It becomes: 「`model-lifecycle.patch` 修改 worker 模型客户端清理（在任务自己的事件循环里关闭并移出
  LiteLLM 缓存的异步客户端）、配置中的普通与传输独立重试预算、计数用尽后的停放重发（传输错误与限流 429，十二小时，
  `Retry-After`）、单次发送整次期限与失败发送收集，关闭底层隐式重试及响应缓存。」

## Tests

Tests are run with `python -m pytest tests` under the procedure in "`patches/agentsociety2/` and the worktree-local
vendor copy" (the `PYTHONPATH` of its step 3). Every new test has a Chinese docstring.

### New tests

**`tests/unit/as2/test_llm_dispatcher_transport.py`** (new tests in the existing file, reusing `_Router`, `_Semaphore`,
`_client`, `_error`). `_client` gains a keyword `clock: list[float] | None`. When it is given, the test replaces
`llm_dispatcher.time` with `SimpleNamespace(monotonic=lambda: clock[0])` (the dispatcher reads only `time.monotonic`),
and the fake `asyncio.sleep` the helper already installs (`:67-72`) also adds each delay to `clock[0]`. This replaces
the dispatcher's own module reference, never the global `time`. Retry-After dates are compared with the real
`datetime.now`, which the fake clock does not touch.

| Test | Asserts |
| --- | --- |
| `test_spent_transport_count_parks_and_resends_the_identical_request` | 10 × `APIConnectionError`, then success, `transport_max_retries=3`: the result is the response; `delays == [2, 4, 8, 16, 32, 64, 120, 120, 120, 120]`; all 11 router requests equal the first; no request contains `transport_max_retries` or `transport_attempts` |
| `test_park_gives_up_after_twelve_hours_with_the_root_cause` | A script of 400 copies of one `InternalServerError` raised from `ConnectionResetError` (built with `raise … from`), fake clock, `transport_max_retries=3`: `LLMDispatchError` matching `^Failed to get valid response after 366 attempts in 43206\.0 s\. Last error type: InternalServerError\. Error chain: InternalServerError <- ConnectionResetError$`; `__cause__` is the last failure; 366 router requests; `sum(delays) == 43206` |
| `test_rate_limit_parks_after_the_ordinary_count_and_honours_retry_after` | Three `RateLimitError` with `response=httpx.Response(429, headers={"retry-after": "30"}, request=…)`, then success, `max_retries=0`: `delays == [30, 30, 30]`. The same with an HTTP-date header 45 s ahead of the real clock: each delay is within 1 s of 45. Nine without the header: `[1, 2, 4, 8, 16, 32, 64, 120, 120]` |
| `test_retry_after_beyond_the_budget_fails_at_once` | `retry-after: 50000` with `max_retries=0` (parked) and with `max_retries=2` (counted): each fails `after 1 attempts`; no sleep |
| `test_unparsable_retry_after_is_ignored_with_a_warning` | `retry-after: "soon"`: the wait is the backoff; the WARNING text appears once |
| `test_counted_rate_limit_waits_honour_a_longer_retry_after` | `max_retries=2`, `retry-after: 5`: `delays == [5, 5]`, then success |
| `test_fatal_and_ordinary_failures_never_park` | `AuthenticationError`, `BadRequestError` and `ValueError` with `max_retries=0` each fail after 1 attempt; `RouterRateLimitError`-like text (`ValueError("try again in 3s")`) with `max_retries=1` fails after 2 |
| `test_deadline_expiry_resends_the_same_request_as_a_transport_retry` | `_DEADLINE_BASE_SECONDS` set to 0.05 and `_DEADLINE_SECONDS_PER_TOKEN` to 0 by monkeypatch. A router whose first `acompletion` awaits an `asyncio.Event` that is never set and whose second returns: the result is returned; both requests are equal; inside `collect_transport_attempts()` the single record's `error` starts with `Timeout <- TimeoutError` and its `wait_ms == 2000` |
| `test_deadline_scales_with_max_tokens` | `_attempt_deadline({"max_tokens": 16000}) == 240.0`; `8000` → `180.0`; `{}` → `120.0`; `{"max_tokens": True}` → `120.0` |
| `test_foreign_timeout_error_is_not_relabelled` | The router raises `TimeoutError()` at once: `LLMDispatchError` `after 1 attempts`, `Last error type: TimeoutError` |
| `test_collector_records_each_failed_send_outside_the_request` | Within `collect_transport_attempts()`, with a fake clock and failures `[APIConnectionError, Timeout]` then success: `attempts == [{"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 0, "wait_ms": 2000}, {"attempt": 2, "error": "Timeout", "failed_after_ms": 2000, "wait_ms": 4000}]`; the router never saw the key |
| `test_failed_call_records_its_final_send_with_a_null_wait` | `AuthenticationError` within the collector: one record with `wait_ms is None` |
| `test_collectors_are_isolated_between_concurrent_tasks` | Two `asyncio.gather` tasks, each with its own collector and its own scripted failure count (1 and 2), share one `LLMClient`: each collector has exactly its own records |
| `test_without_a_collector_nothing_is_recorded` | A failure then success outside any collector: no error, and `_TRANSPORT_ATTEMPTS.get() is None` afterwards |
| `test_error_chain_follows_cause_then_unsuppressed_context` | Explicit cause, implicit context, `from None` (suppressed), a self-cycle, and a 20-deep chain (truncated at 16 names) |
| `test_real_router_keeps_accepting_parked_resends` | A real `LLMClient` (real `_build_router`) against `http://127.0.0.1:9/v1` (closed port), fake sleep and clock, `transport_max_retries=0`, `_PARK_BUDGET_SECONDS` = 600 by monkeypatch: every send raises a transport error (no `RouterRateLimitError` or "No deployments available"), and the call fails with `after 10 attempts` (waits 2, 4, 8, 16, 32, 64, 120, 120, 120; the next 120 would end at 606 s > 600). The same against a local aiohttp server that answers every request with HTTP 429 and no `Retry-After`, `max_retries=0`: every send raises `RateLimitError`, never `RouterRateLimitError`, and the call fails `after 11 attempts` (waits 1, 2, 4, 8, 16, 32, 64, 120, 120, 120; the next would end at 607 s). Each case awaits `close_cached_async_clients()` and `client.close()` in `finally` |

**`tests/integration/as2/test_llm_callback_lifecycle.py`** (new tests in the existing file).

| Test | Asserts |
| --- | --- |
| `test_close_cached_async_clients_closes_and_evicts_only_this_loop` | Fake clients with an async `close` that records the running loop are put in `litellm.in_memory_llm_clients_cache.cache_dict` under `k-{id(loop)}` and `k-1` (another loop): the function returns 1; the own client was closed inside the loop and evicted; the other remains and is not closed. The test cleans the cache in `finally` |
| `test_cached_client_close_failure_evicts_all_then_raises_the_first` | Two own entries whose `close` raises `RuntimeError("a")` and `RuntimeError("b")`: both are evicted; `RuntimeError("a")` is raised; the log has two `Failed to close cached LiteLLM client` records |
| `test_worker_coroutine_closes_cached_clients_inside_its_loop` | `runner._run_worker_coroutine` with a coroutine that registers a fake cached client under its running loop's key: after return the client's `close` ran in that same loop and the entry is gone; `proxy.events == ["stats", "close"]` |
| `test_worker_task_failure_and_close_failure_are_both_kept` | The coroutine raises `RuntimeError("task")` and the cached client's close raises `RuntimeError("close")`: `BaseExceptionGroup` "Agent task and LiteLLM client close both failed" with both |
| `test_five_real_worker_tasks_leave_no_unclosed_client_records` | The `close_probe.py` scenario, with a local aiohttp server and 5 `_run_worker_coroutine` tasks with real routers: 0 log records containing `Unclosed`; all 5 calls return `ok` |

**`tests/integration/runtime/test_llm_client_close.py`** (new).
- `test_life_env_close_closes_cached_clients_in_its_own_loop`: a fake cached client keyed to the running loop is closed
  and evicted by `await env.close()`, and the engine is closed.
- `test_life_env_close_still_closes_the_engine_when_client_close_fails`: the client close raises, the error propagates,
  and `env.engine is None` with the store connection closed.

**`tests/unit/runtime/test_error_pickling.py`** (new).
- `test_every_worldmock_error_survives_pickle_and_ray_transport`:
  - `SAMPLES` maps every class to constructor arguments.
  - The set of classes found by recursive `__subclasses__()` from `WorldMockError` (after importing `worldmock.errors`
    and `worldmock.catalog.errors`), plus `WorldMockError` itself, must equal `SAMPLES`' keys. An AST scan of
    `src/worldmock` and `custom` for `ClassDef`s deriving, transitively by name, from these classes must find no class
    outside `SAMPLES`. So a new subclass must be added here.
  - For each class, with `pickle` and `ray.cloudpickle`: `type`, `args` and `vars()` are equal after a round trip,
    also for an instance that carries a note added with `add_note("context")`.
- `test_actor_error_reaches_the_driver_as_its_own_type`:
  - Build `RayTaskError("LifeEnvActor.init", "Traceback …", EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID",
    "model context metadata is unavailable"), proctitle="p", pid=1, ip="127.0.0.1")`.
  - `RayError.from_bytes(error.to_bytes())` is a `RayTaskError` whose `cause` has the same path, code and reason.
  - `type(restored.as_instanceof_cause()).__name__ == "RayTaskError(EnvironmentInputError)"`.
  - `failure_message(restored.as_instanceof_cause()) == "EnvironmentInputError: model context metadata is unavailable"`.
- `test_fault_classes_still_log_when_constructed`: `IntegrityError`, `ProtocolError`, `EnvironmentInputError` and
  `RegistrationError` each produce one ERROR `WorldMock failure: …` record.

**`tests/unit/runtime/test_model_metadata_fetch.py`** (new). `requests.get` is patched, and `model_endpoint.time.sleep`
records the waits.

| Test | Asserts |
| --- | --- |
| `test_transient_failures_are_retried_whole_with_fixed_waits` | Parametrized over `ConnectionError`, `SSLError`, `Timeout`, `ChunkedEncodingError` and an `HTTPError` with status 429/500/502/503/504 raised by `raise_for_status`, failing twice then succeeding: 3 GETs; waits `[2, 4]`; the metadata equals the success value |
| `test_five_transient_failures_end_as_the_unchanged_input_error` | Five `SSLError`: `EnvironmentInputError` `model context metadata is unavailable`, code `RUN_CONFIG_INVALID`; waits `[2, 4, 8, 16]`; ERROR `Model metadata fetch failed after 5 tries: SSLError` |
| `test_authentication_and_payload_failures_are_not_retried` | HTTP 401, HTTP 404 and a JSON decode error each make 1 GET and no wait |
| `test_frozen_metadata_loader_returns_the_frozen_value_without_network` | `requests.get` raises: the loader returns the manifest's `ModelMetadata` for any config |
| `test_frozen_metadata_loader_rejects_missing_or_invalid_metadata` | A missing key, `None`, and a dict without `context_window` each raise `IntegrityError("Frozen model metadata is missing or invalid")` |

**`tests/unit/footprints/test_status_liveness.py`** (new). Stores are built with the `_status_store` pattern of
`tests/integration/runtime/test_host_branch_coverage.py:389-397`, and heartbeats are written with explicit times.

| Test | Asserts |
| --- | --- |
| `test_status_json_carries_the_one_line_error_message` | `write_status(run, "failed", "stopped", "RuntimeError", "RuntimeError: boom")` writes exactly `{"error_message":"RuntimeError: boom","error_type":"RuntimeError","phase":"stopped","status":"failed"}` |
| `test_failure_message_unwraps_groups_and_ray_causes` | A group of two gives `"A: x \| B: y"`; an object with a `cause` exception gives the cause's line; ANSI sequences are stripped; newlines and runs of spaces collapse |
| `test_cli_failure_writes_the_root_cause_into_status` | `cli.main` with `runtime.run_world` patched to raise `RuntimeError("Agent decision failed: … Error chain: InternalServerError <- ConnectError")` → `status.json.error_message` contains `ConnectError` and `error_type == "RuntimeError"` |
| `test_fresh_heartbeat_reads_running` | Running status, a heartbeat 10 s old, last event an observation: `liveness == "running"`, and `elapsed_seconds` grows with now |
| `test_fresh_heartbeat_with_a_pending_request_reads_waiting_for_model` | The last event is a `model_request`: `liveness == "waiting_for_model"` and `waiting_for_model_since` equals its `recorded_at` |
| `test_old_heartbeat_reads_stale_and_stops_elapsed_time` | A heartbeat 181 s old: `liveness == "stale"`; `elapsed_seconds == heartbeat_at − first recorded_at` |
| `test_missing_heartbeat_while_running_reads_stale` | No file: `liveness == "stale"`; `heartbeat is None` |
| `test_finished_runs_read_stopped_regardless_of_heartbeat` | `completed`, `failed` and `interrupted` read `stopped`; without `status.json`, `liveness is None` |
| `test_unreadable_heartbeat_is_refused` | Parametrized: `heartbeat.json` = `b"{"`, `b"[]"`, and an object whose `heartbeat_at` has no timezone: each raises `IntegrityError("Run heartbeat is invalid")` with the ERROR logged |
| `test_first_and_last_event_are_read_by_primary_key` | `EXPLAIN QUERY PLAN` of `_FIRST_EVENT_SQL` and `_LAST_EVENT_SQL` contains no `SCAN events`; `wall_time.started_at` and `last_event_at` equal the first and last events' `recorded_at` |
| `test_driver_heartbeat_writes_at_once_and_every_interval` | `HEARTBEAT_INTERVAL_SECONDS` = 0.05: the file exists right after `__enter__`; within 2 s `heartbeat_at` changes at least twice; after `__exit__` the thread is not alive; the bytes have exactly the three keys |
| `test_heartbeat_write_failure_is_logged_and_beating_continues` | `status.atomic_write` raises once, then works: ERROR `Driver heartbeat write failed` appears and the file appears afterwards |
| `test_killed_driver_reads_stale_within_three_intervals` | A subprocess (the venv's `sys.executable`) enters `DriverHeartbeat(run)` with the interval set to 0.2 s and sleeps; the test kills it after the first beat, sets the reader's interval to 0.2 s and polls `read_status` for at most 2 s: `liveness == "stale"` |
| `test_status_reads_heartbeat_as_utf8` | Under `default_encoding_reads` (`tests/text_encoding.py`), with a heartbeat present: no default-encoding read |

**`tests/unit/footprints/test_atomic_replace_retry.py`** (new). This uses the pattern of
`tests/unit/footprints/test_platform_branches.py:26-56`: replace `assets_module.os` with a proxy that reports `name` and
counts `replace`, and replace `assets_module.time` with `SimpleNamespace(sleep=record)`.

| Test | Asserts |
| --- | --- |
| `test_windows_permission_error_is_retried_then_published` | `name="nt"`, `replace` raises `PermissionError` 3 times then delegates: the file holds the new bytes; sleeps `[0.05, 0.1, 0.2]`; 3 WARNING `Atomic replace denied` |
| `test_windows_permission_error_gives_up_after_bounded_waits` | Always raises: `PermissionError`; 9 `replace` calls; sleeps equal `_REPLACE_RETRY_SECONDS`; ERROR `Atomic artifact publication failed`; no `.pending-` file left |
| `test_permission_error_off_windows_is_raised_at_once` | `name="posix"`: 1 call, no sleep |
| `test_other_os_errors_are_not_retried` | `OSError("disk")` on `nt`: 1 call |

**`tests/integration/runtime/native_runtime_fixtures.py`** (new helper, not a test). Move `CurrentDecisionModel` and the
`native_runtime` fixture from `tests/integration/runtime/test_native_dataset_lifecycle.py:20-80` here. The only change:
the fixture's `build` makes the model with a module-level `MODEL_FACTORY = CurrentDecisionModel` (called with `env`),
which a test may replace with `monkeypatch.setattr`. Add `scripted_dispatcher(env, failures: dict[int, int]) ->
LLMClient`:
- a real `LLMClient("test-model", "https://model.example.invalid/v1", "offline")` whose `_ensure_runtime` is a no-op and
  whose `_sem` is the minimal semaphore of `test_llm_dispatcher_transport.py`;
- its `_router.acompletion(**request)` records every request it receives (failed sends included) and counts answered
  calls. While the current logical call (answered + 1) still has failures left in `failures`, it raises
  `APIConnectionError` before consulting any rule. Otherwise it answers with `await model.call(**request)` of one
  `CurrentDecisionModel(env)` held by the helper, so the tool-call ids (`call-{n}`) count answered requests only and
  match a clean run.

Both test files import from this module.

**`tests/integration/runtime/test_transport_park_run.py`** (new). This is the byte-identity proof for parking.
- `test_parked_run_settles_like_an_uninterrupted_run`:
  - Two `run_world(RunRequest(ROOT, root, dir, "park", "park"))` runs (same `run_id`), each with
    `MODEL_FACTORY = lambda env: scripted_dispatcher(env, failures)`: one with `failures={}`, one with
    `failures={2: 6}`.
  - Both use a fake `asyncio.sleep` and the dispatcher `time` namespace with a fake clock (as above). The fixture
    `config.toml` does not set `transport_max_retries`, so the default 3 applies.
  - Asserted:
    - Both runs complete, and neither has a `branch_created` event.
    - The router requests of the faulted run, with consecutive duplicates collapsed, equal the clean run's router
      requests byte for byte (`canonical_json`).
    - The two event streams are equal after removing `recorded_at` and `wall_elapsed_ms` from every event and
      `data.transport_attempts` from every `model_response`, and after mapping every 32-character lowercase hex token
      (event and checkpoint ids) to its first-occurrence index. 64-character content hashes are not mapped.
    - The faulted run's second call's response event has six records with `wait_ms == [2000, 4000, 8000, 16000, 32000,
      64000]`; every other response has `[]`.
    - The final world states are equal after the same id mapping.
  - Any further difference fails the test. A new mask may be added only with the writer that puts wall time there.
- `test_parked_run_ops_log_names_the_wait`: the faulted run's ops log has one `MODEL_TRANSPORT_WAIT` with
  `failed_sends == 6` and `waited_ms == 126000`.

**`tests/integration/runtime/test_model_exchange_attempts.py`** (new).

| Test | Asserts |
| --- | --- |
| `test_response_and_failure_events_carry_transport_attempts_in_the_envelope` | `engine.record_model_exchange` with request, then response evidence carrying two records, and a second call with failure evidence carrying one: `data["transport_attempts"]` equals them; the request events have no such key; neither `data.response` contains it |
| `test_request_evidence_with_attempts_is_rejected` | Request evidence carrying one well-formed record (`attempt` 1, all four keys): `ProtocolError("Model request evidence must not carry transport attempts")`, and no event was written |
| `test_malformed_attempts_are_rejected` | Parametrized: a list instead of a tuple, a missing key, an extra key, attempts numbered `[2]`: `ProtocolError("Model exchange transport attempts are invalid")` |
| `test_memory_call_evidence_carries_attempts` | `LifeAgent._dispatch_memory_llm` through `person()` (`current_contract_fixtures.py:106-115`) with a real `LLMClient` that fails once with `APIConnectionError` (fake sleep): the response evidence has one record; with a fatal error the failure evidence has one record with `wait_ms is None` |
| `test_plan_day_evidence_carries_attempts` | `settle_pending_day_plans` with a `PlanDayClient` whose inner client fails once then answers: the plan_day response draft has one record; with a final failure the failure draft has its records and the call raises |
| `test_later_prompts_are_byte_identical_with_and_without_attempts` | A same-second scenario built like `tests/integration/runtime/test_same_second_continuation.py:400-500`, with a first response rejected by `TOOL_CALL_REQUIRED` and then a valid one, run twice on fresh engines: once with a real `LLMClient` failing every first send with `APIConnectionError` (fake sleep), once without failures. The next decision's request `messages` and `tools`, the progress layer of its observation, and `_decision_self_note` are equal byte for byte |

**`tests/integration/runtime/test_frozen_metadata_resume.py`** (new, on the `native_runtime` fixture). Every test here
also replaces `model_endpoint.time.sleep` with a recorder, so a build that fetches live again (the mutations below)
fails within the test instead of sleeping 30 s per fetch.

| Test | Asserts |
| --- | --- |
| `test_resume_and_replay_make_no_metadata_request` | After a parent run, `requests.get` is replaced by one that records calls and raises `SSLError`: both `resume` and `replay` complete; no call was recorded; each child manifest's `model_metadata` equals the parent's |
| `test_resume_uses_the_frozen_capacity_even_when_the_live_service_differs` | The metadata service now answers a smaller `max_output_tokens` (12,000): the resume completes, and the child's first decision request has `max_tokens == min(16000, frozen max_output_tokens)` |
| `test_bad_checkpoint_id_fails_before_environment_load_or_copy` | `ResumeRequest` with `checkpoint_id=""`; `worldmock.environment.load_environment` and `run_setup._configure_process` are patched to fail the test if called: `IntegrityError("Checkpoint is missing or its record is damaged")`; the destination does not exist; no `.worldmock-resume-` directory remains |
| `test_parent_with_an_edited_manifest_fails_before_environment_load_or_copy` | The parent's `manifest.json` rewritten without `model_metadata`: the checkpoint's `manifest_sha256` binding (`footprints/store.py:345-346`) refuses it with `Checkpoint and frozen manifest disagree` before `load_environment`, with no network call and no destination. The loader's own refusal of a missing or invalid value is covered by `test_frozen_metadata_loader_rejects_missing_or_invalid_metadata` |
| `test_engine_open_reads_the_store_manifest_metadata` | `LifeEngine.open(environment_root_path, run_dir)` with `requests.get` raising: it opens; `engine.context.inputs.model_metadata` equals the manifest value |
| `test_fresh_run_fetch_retries_and_freezes_the_same_metadata` | A fresh run whose metadata service raises `SSLError` twice then answers (sleep patched): the run starts; the manifest's `model_metadata` equals a clean run's |

**`tests/integration/footprints/test_branch_lineage.py`** (new, on `engine_factory` and `restore_branch`).

| Test | Asserts |
| --- | --- |
| `test_fresh_run_manifest_has_empty_lineage_and_structure_seven` | `manifest["lineage"] == []` and `manifest["schema_version"] == 7` |
| `test_lineage_chains_parents_in_order_with_their_end_reasons` | Parent P: checkpoint C1, two more events, `engine.fail_run("RuntimeError('boom')")`. Child A = `restore_branch(P, C1)`: checkpoint C2, one more event, store closed without a terminal event. Grandchild B = `restore_branch(A, C2)`. Then `B.manifest["lineage"]` is two entries in order P, A with `end_reason` `run_failed` / `no terminal event`, `end_error` `"RuntimeError('boom')"` / `None`, `discarded_events` equal to the tail lengths, `simulated_from` of A's entry equal to `checkpoint_time` of P's entry, and every key and type as specified. `B.manifest["source_run_ids"] == sorted({P, A, B})` |
| `test_terminal_lookup_ignores_ancestor_events_and_takes_the_last` | A parent with `run_ended` then `run_failed` (`engine.fail_run` after the end) after its own start reads `run_failed`. Ancestor case: P runs to `run_ended` and its final checkpoint C_end; A = `restore_branch(P, C_end)` therefore copies P's `run_ended`; A records one more event and its store is closed without a terminal event; B = `restore_branch(A, C_A)`: A's entry reads `no terminal event`, not the copied `run_ended` |
| `test_terminal_event_lookup_uses_the_agent_type_index` | `EXPLAIN QUERY PLAN` of the `read_terminal_event` statement mentions `USING INDEX events_agent_type` |
| `test_export_and_quality_report_carry_lineage` | Exporting B at its last checkpoint: `manifest["lineage"] == B.manifest["lineage"] == manifest["frozen_run_manifest"]["lineage"]`; `quality["lineage"]["segment_count"] == 3`, `end_reason_counts == {"no terminal event": 1, "run_failed": 1}`, and per-row `downtime_seconds` equals the entry's `branched_at − last_recorded_at` |
| `test_export_lineage_must_equal_the_frozen_run_manifest` | `_validate_identity` on a complete export manifest copy whose `lineage` differs by one value: `IntegrityError("Export lineage disagrees with the frozen run manifest")` |
| `test_quality_lineage_section_is_computed_from_the_manifest_only` | `_lineage_quality` on a hand-written lineage of the 30-day chain's four parents (end reasons from `tail_probe.txt`): the counts, the rounding and `segment_count == 5` |

**`tests/integration/runtime/test_network_ops_records.py`** (new).
- `MODEL_EXCHANGE` records carry `commit_ms` as a float ≥ 0.
- `MODEL_TRANSPORT_WAIT` is written exactly when the attempts are non-empty, with `failed_sends`, `waited_ms` and
  `last_error`.
- `MODEL_PROTOCOL_REJECTED` carries `error_code == "TOOL_CALL_REQUIRED"` for a rejected response, and is absent for an
  accepted one and for a failure exchange (whose recorded response carries `MODEL_SERVICE_FAILED`).
- `PLAN_DAY_CALL` is written for a successful call (info, `outcome == "response"`) and for a failing one (error,
  `outcome == "failure"`), with `call_ms ≥ 0`.

### Byte-identity proof for this chapter

The tests that prove the chapter changes no prompt, no decision input and no settled fact:
- `test_spent_transport_count_parks_and_resends_the_identical_request` and the collector tests: every send of a call is
  the same request object, and nothing is added to the wire.
- `test_parked_run_settles_like_an_uninterrupted_run`: a whole run with a parked call equals a clean run in every
  request and every event, apart from wall fields, random ids and the new `transport_attempts`.
- `test_later_prompts_are_byte_identical_with_and_without_attempts`: the readers that rebuild prompts from stored
  responses see no difference.
- `test_resume_uses_the_frozen_capacity_even_when_the_live_service_differs`: resume prompts use the frozen value, which
  is the value every resume that succeeds today already uses.
- The deliberate exception is pinned by `test_deadline_expiry_resends_the_same_request_as_a_transport_retry`: the
  re-sent request is identical, and only the answer is a new sample.
- The proof chapter's masked complete-day comparison runs on every change, and the recorded-response runs of the
  `d27f2c5a` and `49db53cd` verification branches, give the end-to-end check with the masks listed under "Not in this
  chapter".

### Existing tests that change

| Test | Change |
| --- | --- |
| `tests/unit/as2/test_llm_dispatcher_transport.py::test_persistent_transport_failure_stops_after_four_attempts` (`:100-111`) | Monkeypatch `_PARK_BUDGET_SECONDS = 0` so that the first parked failure ends the call; keep the assertions (`after 4 attempts`, delays `[2.0, 4.0, 8.0]`). Rename to `test_persistent_transport_failure_ends_when_the_park_budget_is_spent` |
| `…::test_non_transport_provider_errors_do_not_use_transport_budget` (`:114-126`) | Drop `RateLimitError` from the `kind` parameters; 429 is covered by the new park tests |
| `…::test_exhausted_transport_budget_does_not_borrow_ordinary_retries` (`:185-191`) | Script `[Timeout] * 5 + [success]`, `max_retries=2`: succeeds; `delays == [2, 4, 8, 16, 32]`; then assert with `_PARK_BUDGET_SECONDS = 0` that `[Timeout] * 5` fails `after 4 attempts` while the ordinary counter was never used |
| `…::test_configured_transport_budget_controls_actual_attempts` (`:313-321`) | With `_PARK_BUDGET_SECONDS = 0`, the original assertions hold (`after {budget + 1} attempts`, `len(delays) == budget`); keep the no-key-on-the-wire assertion |
| `…::test_dispatcher_does_not_log_provider_error_body` (`:230-245`) | Two calls with the same fake logger. First `[failure] * 5 + [success]` under the real budget and the fake clock: the fourth and fifth re-send WARNINGs carry `, parked 0s` and `, parked 16s`, and no WARNING contains the sensitive text. Then `[failure] * 4` with `_PARK_BUDGET_SECONDS = 0`: the final ERROR line and `str(LLMDispatchError)` contain `APIConnectionError` and not the sensitive text |
| `…::test_mixed_failures_spend_separate_budgets`, `…::test_exhausted_ordinary_budget_does_not_borrow_transport_retries`, `…::test_rate_limit_uses_fixed_one_second_backoff_with_sixty_second_cap`, `…::test_typed_transient_failure_recovers_with_zero_ordinary_retries`, `…::test_cancellation_is_not_retried_and_releases_semaphore`, `…::test_environment_router_does_not_forward_retired_retry_parameters` | Unchanged and still passing: every failure stays within its counter, the scripted `RateLimitError`s carry no `Retry-After`, `CancelledError` is not an `Exception`, and `_send_within` sends the same four keys |
| `tests/unit/as2/test_llm_dispatcher_no_retry.py` | Unchanged: `ValueError` is ordinary |
| `tests/integration/as2/test_llm_callback_lifecycle.py::test_worker_task_cleanup_covers_failure_and_cancellation` | Unchanged: `_closing_llm_clients` re-raises the same `RuntimeError` or `CancelledError` after closing an empty cache |
| `tests/integration/runtime/test_host_branch_coverage.py::test_replay_reads_its_intervention_file_as_utf8` (`:459-479`) | The parent `tmp_path / "parent"` does not exist, so `_prepare_resume` now stops at `FootprintStore(parent)` before loading the environment: assert `"Footprint database does not exist" in caplog.text` instead of the metadata reason; the docstring's 「凭据未设置时环境加载失败」 becomes 「父运行不存在时在加载环境之前失败」 |
| `tests/unit/footprints/test_store.py` `store` fixture (`:16-21`) | The manifest gains `'lineage': []` and `'window': {'start': 100, 'end': 1000, 'timezone': 'UTC'}`: `restore_branch` (`:229`, `:249`, `:275`) now builds a lineage entry from them |
| `tests/unit/footprints/test_branch_coverage.py::_make_store` (`:98-104`) | The manifest gains `"lineage": []` (branch at `:676`, export at `:732`) |
| `tests/integration/runtime/test_public_runtime_boundaries.py::test_actual_model_metadata_rejects_transport_capacity_and_service_identity` (`:105-129`) | For `failure == "transport"`, patch `model_endpoint.time.sleep` to record; assert 5 GET calls and waits `[2, 4, 8, 16]` |
| `tests/integration/runtime/test_environment_identity.py::test_each_frozen_input_identity_is_required` (`:25-31`) | Remove `"model_metadata"` from the parameters (the term is deleted; `test_frozen_metadata_loader_rejects_missing_or_invalid_metadata` covers it) |
| `…::test_retired_or_unknown_run_manifest_versions_are_rejected` (`:34-42`) | Versions `[1, 2, 3, 4, 5, 6, 8, "7", 7.5, None]`; the current is 7; docstring 「结构版本是 7：上一版的 6……」 |
| `…::test_run_whose_stored_manifest_carries_the_previous_structure_number_cannot_be_opened` (`:45-…`) | The previous number is 6. It now fails in `open_world` after the store opens, with the same message; the docstring says 6 and 7 |
| Manifest version 6 → 7 assertions | `tests/integration/runtime/test_environment_identity.py:38`, `tests/integration/footprints/test_acs_export.py:92`, `tests/integration/runtime/test_native_dataset_lifecycle.py:112`, `tests/integration/runtime/test_runtime_inputs_contract.py:143`, `tests/system/lifecycle/test_run_manifest.py:35`, `tests/dayrun_baseline.py:109`, `tests/unit/engineering/test_dayrun_baseline.py:35` |
| `tests/integration/runtime/test_native_dataset_lifecycle.py` | Import `CurrentDecisionModel` and `native_runtime` from `native_runtime_fixtures.py` |
| `tests/integration/runtime/test_host_branch_coverage.py:381`, `:413-414`, `:525-526` | The `status.json` dict gains `"error_message": None`; the empty `wall_time` dict gains `"last_event_age_seconds": None`; `runtime` dicts gain `"error_message": None` |
| `tests/unit/footprints/test_status.py:24`, `:78` | `runtime` dicts gain `"error_message": None` |
| `tests/unit/footprints/test_quality.py::_manifest` (`:34-…`), `tests/unit/footprints/test_branch_coverage.py::_manifest` (`:56-60`), `tests/integration/evaluation/test_errand_metrics.py:121` manifest | Add `"lineage": []` |
| `tests/unit/footprints/test_quality.py::test_report_sections_are_the_documented_grouping_without_a_language_count` (`:635-650`) | Add `"lineage"` to the `documented` list (the `schema_version == 9` change belongs to the host-costs chapter) |
| `tests/unit/engineering/test_import_graph.py` `DELAYED_IMPORTS` | Add `('worldmock.runtime', '_call_plan_day', 'agentsociety2.config.llm_dispatcher'): '运行配置完成后加载重依赖'`, `('worldmock.runtime', '_log_plan_day_call', 'worldmock.opslog'): '按执行职责延迟加载，保持输入纯加载边界'` and `('worldmock.model_endpoint', 'frozen_metadata_loader', 'worldmock.errors'): '按执行职责延迟加载，保持输入纯加载边界'`. The store-commits chapter deletes `:94` and `:100` |
| `tests/fixtures/viewer-context.json`, both complete-day baselines | Re-recorded once at the end of the integration branch, with the other chapters (proposal "Versions") |
| Any test that compares a whole `model_response.data` dict or a whole `status.json` | Add the new key. `grep -rn "error_type\": None\|'error_type': None" tests` and `grep -rn "\"phase\": \"response\"" tests` find them; the list above is the result on `0bd2de65` |

### Source mutations (`tests/dataset_acceptance.py`)

The dispatcher lives in vendored AS2, outside the mutation roots (`tests/mutation_runner.py:58-63`), so its rules are
held by the unit tests above.

| Name | Symbol | Original | Replacement | Killer |
| --- | --- | --- | --- | --- |
| `resume_fetches_live_metadata` | `worldmock.runtime::_prepare_resume` | `load_environment(root, metadata_loader=frozen_metadata_loader(parent.manifest))` | `load_environment(root)` | `tests/integration/runtime/test_frozen_metadata_resume.py::test_resume_and_replay_make_no_metadata_request` |
| `open_world_fetches_live_metadata` | `worldmock.life.world_setup::open_world` | `load_environment(environment, metadata_loader=frozen_metadata_loader(store.manifest))` | `load_environment(environment)` | `tests/integration/runtime/test_frozen_metadata_resume.py::test_engine_open_reads_the_store_manifest_metadata` |
| `metadata_fetch_not_retried` | `worldmock.model_endpoint::_fetch_metadata` | `if not _transient_metadata_error(error):` | `if True:` | `tests/unit/runtime/test_model_metadata_fetch.py::test_transient_failures_are_retried_whole_with_fixed_waits` |
| `metadata_fetch_retries_rejections` | `worldmock.model_endpoint::_transient_metadata_error` | `response.status_code in _METADATA_RETRY_STATUSES` | `response.status_code >= 400` | `tests/unit/runtime/test_model_metadata_fetch.py::test_authentication_and_payload_failures_are_not_retried` |
| `environment_input_error_unpicklable` | `worldmock.errors::EnvironmentInputError.__reduce__` | `return type(self), (self.path, self.code, self.reason), self.__dict__` | `return super().__reduce__()` | `tests/unit/runtime/test_error_pickling.py::test_every_worldmock_error_survives_pickle_and_ray_transport` |
| `precondition_error_unpicklable` | `worldmock.errors::PreconditionError.__reduce__` | `return type(self), (self.args[0], self.unmet, self.suggested, self.public_reason), self.__dict__` | `return super().__reduce__()` | same |
| `registration_error_unpicklable` | `worldmock.catalog.errors::RegistrationError.__reduce__` | `return type(self), (self.code, self.detail), self.__dict__` | `return super().__reduce__()` | same |
| `failure_message_ignores_ray_cause` | `worldmock.status::failure_message` | `if isinstance(cause, BaseException) and cause is not error:` | `if False:` | `tests/unit/runtime/test_error_pickling.py::test_actor_error_reaches_the_driver_as_its_own_type` |
| `status_error_message_dropped` | `worldmock.status::write_status` | `"error_message": error_message` | `"error_message": None` | `tests/unit/footprints/test_status_liveness.py::test_status_json_carries_the_one_line_error_message` |
| `stale_heartbeat_reads_running` | `worldmock.status::_liveness` | `heartbeat["age_seconds"] > HEARTBEAT_STALE_INTERVALS * HEARTBEAT_INTERVAL_SECONDS` | `heartbeat["age_seconds"] > 1e12` | `tests/unit/footprints/test_status_liveness.py::test_old_heartbeat_reads_stale_and_stops_elapsed_time` |
| `park_not_reported` | `worldmock.status::_liveness` | `if last_event_type == "model_request":` | `if False:` | `tests/unit/footprints/test_status_liveness.py::test_fresh_heartbeat_with_a_pending_request_reads_waiting_for_model` |
| `replace_not_retried` | `worldmock.footprints.assets::_replace` | `for delay in _REPLACE_RETRY_SECONDS:` | `for delay in ():` | `tests/unit/footprints/test_atomic_replace_retry.py::test_windows_permission_error_is_retried_then_published` |
| `replace_retried_off_windows` | `worldmock.footprints.assets::_replace` | `if os.name != "nt":` | `if False:` | `tests/unit/footprints/test_atomic_replace_retry.py::test_permission_error_off_windows_is_raised_at_once` |
| `lineage_forgets_ancestors` | `worldmock.footprints.checkpoint::branch_lineage` | `[*store.manifest["lineage"], lineage_entry(store, checkpoint)]` | `[lineage_entry(store, checkpoint)]` | `tests/integration/footprints/test_branch_lineage.py::test_lineage_chains_parents_in_order_with_their_end_reasons` |
| `killed_parent_reported_failed` | `worldmock.footprints.checkpoint::lineage_entry` | `reason, error = "no terminal event", None` | `reason, error = "run_failed", None` | same |
| `terminal_lookup_reads_ancestors` | `worldmock.footprints.checkpoint::lineage_entry` | `store.read_terminal_event(earlier[-1]["checkpoint_event_seq"] if earlier else 0)` | `store.read_terminal_event(0)` | `tests/integration/footprints/test_branch_lineage.py::test_terminal_lookup_ignores_ancestor_events_and_takes_the_last` |
| `export_lineage_unchecked` | `worldmock.footprints.validation::_validate_identity` | `manifest.get("lineage") != frozen["lineage"]` | `False` | `tests/integration/footprints/test_branch_lineage.py::test_export_lineage_must_equal_the_frozen_run_manifest` |
| `transport_attempts_dropped` | `worldmock.life.model_evidence::_attempt_fields` | `return {"transport_attempts": [dict(item) for item in attempts]}` | `return {"transport_attempts": []}` | `tests/integration/runtime/test_model_exchange_attempts.py::test_response_and_failure_events_carry_transport_attempts_in_the_envelope` |
| `request_attempts_accepted` | `worldmock.life.model_evidence::_attempt_fields` | `if exchange.phase == "request" and attempts:` | `if False:` | `tests/integration/runtime/test_model_exchange_attempts.py::test_request_evidence_with_attempts_is_rejected` |
| `env_close_leaves_cached_clients` | `custom.envs.life_env::LifeEnv.close` | `await close_cached_async_clients()` | `pass` | `tests/integration/runtime/test_llm_client_close.py::test_life_env_close_closes_cached_clients_in_its_own_loop` |
| `previous_run_manifest_version_accepted` (existing, `:105-108`) | unchanged | unchanged | `'manifest.get("schema_version") in (6, 7)'` | unchanged |

## Files touched

Production (vendored AS2 through the patches):
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/config/llm_dispatcher.py` (worktree-local copy only)
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/runner.py` (worktree-local copy only)
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/react.py` (worktree-local copy only)
- `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py` (worktree-local copy only)
- `patches/agentsociety2/model-lifecycle.patch`
- `patches/agentsociety2/failure-propagation.patch`
- `patches/agentsociety2/SERIES`
- `patches/agentsociety2/README.md`

Production (WorldMock):
- `src/worldmock/run_setup.py`
- `src/worldmock/model_endpoint.py`
- `src/worldmock/runtime.py`
- `src/worldmock/opslog.py`
- `src/worldmock/status.py`
- `src/worldmock/cli.py`
- `src/worldmock/errors.py`
- `src/worldmock/catalog/errors.py`
- `src/worldmock/life/world_setup.py`
- `src/worldmock/life/run_identity.py`
- `src/worldmock/life/models.py`
- `src/worldmock/life/model_evidence.py`
- `src/worldmock/life/day_planning.py`
- `src/worldmock/footprints/assets.py`
- `src/worldmock/footprints/store.py`
- `src/worldmock/footprints/checkpoint.py`
- `src/worldmock/footprints/export.py`
- `src/worldmock/footprints/quality.py`
- `src/worldmock/footprints/validation.py`
- `custom/agents/life_agent.py`
- `custom/envs/life_env.py`

Design and manual:
- `docs/design/05-recording-engine.md`
- `docs/design/06-agent.md`
- `docs/design/08-configuration.md`
- `docs/design/09-io-formats.md`
- `docs/design/10-runtime.md`
- `docs/manual/runtime.md`

Tests and fixtures:
- `tests/unit/as2/test_llm_dispatcher_transport.py`
- `tests/integration/as2/test_llm_callback_lifecycle.py`
- `tests/integration/runtime/test_llm_client_close.py` (new)
- `tests/unit/runtime/test_error_pickling.py` (new)
- `tests/unit/runtime/test_model_metadata_fetch.py` (new)
- `tests/unit/footprints/test_status_liveness.py` (new)
- `tests/unit/footprints/test_atomic_replace_retry.py` (new)
- `tests/integration/runtime/native_runtime_fixtures.py` (new)
- `tests/integration/runtime/test_transport_park_run.py` (new)
- `tests/integration/runtime/test_model_exchange_attempts.py` (new)
- `tests/integration/runtime/test_frozen_metadata_resume.py` (new)
- `tests/integration/runtime/test_network_ops_records.py` (new)
- `tests/integration/footprints/test_branch_lineage.py` (new)
- `tests/integration/runtime/test_native_dataset_lifecycle.py`
- `tests/integration/runtime/test_public_runtime_boundaries.py`
- `tests/integration/runtime/test_environment_identity.py`
- `tests/integration/runtime/test_host_branch_coverage.py`
- `tests/integration/runtime/test_runtime_inputs_contract.py`
- `tests/integration/footprints/test_acs_export.py`
- `tests/integration/evaluation/test_errand_metrics.py`
- `tests/system/lifecycle/test_run_manifest.py`
- `tests/dayrun_baseline.py`
- `tests/unit/engineering/test_dayrun_baseline.py`
- `tests/unit/engineering/test_import_graph.py`
- `tests/unit/footprints/test_status.py`
- `tests/unit/footprints/test_store.py`
- `tests/unit/footprints/test_quality.py`
- `tests/unit/footprints/test_branch_coverage.py`
- `tests/dataset_acceptance.py`
- `tests/fixtures/viewer-context.json` (re-recorded once at the end, shared)

Scratch (not tracked): `<worktree>/.work/as2-regen/` and the regeneration script; `<worktree>/.work/venv-lrc/` when
used.

## Not in this chapter

| Item | Where |
| --- | --- |
| A CLI option or config field for the park budget; removing `transport_max_retries` from the package; an engine gate for DeepSeek peak hours | Proposal "Awaiting the owner" (`PROPOSAL-long-run-cost.md:422`, `:425`, `:423`). The count, its config field and `ModelIdentity.transport_max_retries` (`life/models.py:76`) stay exactly as they are |
| Run-independent random keys | Proposal "Not proposed" (`:406`); the reviewer dropped it |
| One persistent event loop and connection pool per worker | Proposal "Not proposed" (`:410`); only closing the cached clients is specified here |
| HTTP 502 (`litellm.BadGatewayError`, an `APIStatusError`) as a transport error | Not in the proposal. Today it is ordinary and, with the run's `max_retries = 0`, ends the call at once; it still does. Moving it into `_TRANSPORT_ERRORS` would be a one-line engine rule (no tool parameter, CLI option or dataset field, and the re-send is wire-identical); left for the integrator to raise with the owner because the proposal names only the existing transport class and 429 |
| Activity order kept through checkpoints (the Oct 20 swap) and the world-state structure version | State and checkpoint storage chapter |
| Status reading checkpoint ids only (`status.py:39-40`) and status's reads of `activities` (`:41`, `:106`) | State and checkpoint storage chapter |
| Short reads in `worldmock status`: no `BEGIN`, per-type counts, the indexed usage statement, and the two primary-key reads of the first and last event | [04-history-paths.md](04-history-paths.md). This chapter reuses its two statements (the last one extended with the event type) and replaces its `_wall_time` body |
| `append_evidence` in `record_model_exchange` and `_call_plan_day`; deleting the `validated_payload` local imports | [01-store-commits.md](01-store-commits.md). This chapter's `commit_ms` and `_call_plan_day` code are written on top of it |
| `"request": null` on responses, the request digest check, export structure 8 | [03-recorded-evidence.md](03-recorded-evidence.md). This chapter adds `lineage` under the same 8 |
| The event copy inside `restore_branch` / `_populate_branch` (ATTACH + `INSERT … SELECT`); `fail_run`'s history read; WAL | Whole-history chapter. It must keep `lineage=branch_lineage(store, checkpoint)` in the branch manifest |
| `quality.json` cost by call class and its share of the 8 → 9 bump; moving `PLAN_DAY_PURPOSE` | [06-host-costs-and-model-cost.md](06-host-costs-and-model-cost.md) |
| Expected purchase-preview refusals at ERROR | Proposal "Decisions" (`:444`): the logging rule is unchanged; the host-costs chapter evaluates availability once |
| Showing lineage in the viewer | [07-viewer.md](07-viewer.md) does not read it |
| Comparator rules for the new recorded fields | Proof chapter (`scripts/compare_runs.py`, rules `lineage` and `transport-attempts`, `08-proof-tools.md:611-612`): the fields are dropped from the head when the base record lacks them. When both sides have them, the wall-derived values are `transport_attempts[].failed_after_ms`, `lineage[].last_recorded_at`, `lineage[].branched_at` and `quality.json` `lineage.parents[].downtime_seconds` / `lineage.downtime_seconds`, and `lineage[].run_id` / `lineage[].checkpoint_id` are identities; the proof chapter must mask or map these exact paths if a comparison ever has them on both sides. The recorded-response run must accept `transport_attempts: []` on new responses, where the old recordings have no key |
| Provider-stall handling beyond the deadline (classifying a degenerate one-token response as a transport fault) | The reviewer's missing option. It is a behaviour change that needs a model-backed acceptance run; not in the proposal |
| A fresh run handing the driver's fetched metadata to the actor | Not in the proposal. Fresh runs keep both live fetches, now retried |
| `scripts/inspect_run.py cmd_workday` still loading live metadata (`:739`) | Out of scope. It is an offline tool; its manifest check compares only persona and config digests |
| Response evidence of the AS2 prelude statement and of `PersonAgent._dispatch_memory_llm` | Not run by WorldMock; they record `[]` (see "Not covered" under Behaviour) |
| Rebuilding the main checkout's vendor copy after the merge | Owner-coordinated merge step (see the patch procedure); never from this worktree |
