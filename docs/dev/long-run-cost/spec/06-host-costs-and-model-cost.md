# Per-decision host costs and model cost reporting

> Part of [SPEC-long-run-cost.md](../SPEC-long-run-cost.md); read its terms and order there.

## Goal and basis

This chapter implements two proposal sections:
- "Per-decision host costs": open activities computed once, availability evaluated once, no memory-episode deep copies,
  token count reused. With them comes the "Quieter logs" line "Previews evaluated once", which is a consequence of
  availability evaluated once and needs no code of its own.
- "Model cost": cost by call class in `quality.json`, and the off-peak launch guidance in the runtime manual.

It also applies the "Decisions" row "Expected refusals at ERROR: unchanged rule; evaluated once per observation instead
of twice".

Every change here leaves each model prompt, decision input, settled world fact, recorded event and checkpoint
byte-identical. None of the proposal's deliberate exceptions applies. Two new fail-loud checks (a memory consolidation
cursor that names no stored record, and a token count fitted for another request) fire only in states no run reaches;
in every reachable state the code behaves as today. The single change of recorded form is
`quality.json`: `model_usage.by_purpose` is replaced by the finer `model_usage.by_call_class`. That section is derived
from events that already exist, and its purpose rows are sums of class rows, so no information is lost. The chapter
changes:
- no model tool schema and no host validation of tool arguments;
- no CLI option of the run commands;
- no field of `persona.toml`, `config.toml` or the catalog files;
- no `behavior_metrics` population.

Research inputs, with the reviewer's revisions applied where they correct the design:
- `.work/run30/opt/design_critical.json` and `review_critical.json`: options 3, 4, 5 and the reviewer's missing
  option M1;
- `design_model.json` and `review_model.json`: the off-peak option and the cost-by-class option.

Scratch evidence:
- the research's `.work/run30/scratch/opt-critical/` (`prof_warm_observation.txt`, `bench_agent.json`) and
  `opt-review-critical/` (`proof_open_activities.json`, `proof_reuse_availability.json`);
- this chapter's own probes in `.work/run30/scratch/spec-06-host-costs-and-model-cost/`:
  - `proof_observation_bytes.py` and `.json`;
  - `proof_memory_reads.py` and `.json`;
  - `call_classes.py` and `.json`.

### Measured cost on main `0bd2de65` (day-30 world of the 30-day run)

| Item | Where today | Cost | Growth |
| --- | --- | --- | --- |
| Open activities scanned per tool-layer row | `admission._occupant` (`src/worldmock/life/admission.py:106-110`), `_sleep_refusal`'s `asleep` (`:149-150`), `activity_control._agent_activities` through `pending_conclusions` and `slot_activities` (`src/worldmock/life/activity_control.py:76-83`, `:480-485`) | Profiled per 3 warm builds: `_occupant` 0.279 s, `_agent_activities` 0.251 s, `_sleep_refusal` 0.123 s. That is about 0.18 s per observation profiled (about 0.12 s unprofiled), with 1,253 activities and 1 open [R `prof_warm_observation.txt`] | Linear in all activities ever started (about 42 a day) |
| Availability evaluated twice per action row | `observe.py:36` (`annotated_bindings`) and `observe.py:46` → `tool_layer._action_row` (`tool_layer.py:161`) | 151–155 evaluations per build → 76–79 with reuse, on five checkpoints from day 23 to day 30 [R `proof_observation_bytes.json`]. Warm build median 0.667 → 0.506 s on its own [R `proof_reuse_availability.json`]; 1.79 → 1.06 s together with the open-activity index under heavy load [R `proof_open_activities.json`] | Partly linear in activities until the index lands |
| Two whole-list deep copies of memory episodes per decision | `life_memory.py:106` (`iter_episodes()` for ids) and `:202` (`unconsolidated()` → `iter_episodes()`) | 0.049 + 0.059 s per decision at 2,692 episodes [R `bench_agent.json`]. This chapter's probe gives 0.153 → 0.003 s and 0.156 → 0.004 s under load [R `proof_memory_reads.json`] | Linear in episodes (about 90 a day) |
| Prompt counted twice per model call | `fit_request` (`prompt_capacity.py:79`, `:84`, `:94`), then `compare_usage` recounts (`:172`) after every response (`custom/agents/life_agent.py:208-209`) | 37 / 70 / 110 ms for 92K / 106K / 220K-character day-30 requests, under load [R `bench_agent.json`] | With prompt size |

Expected saving at day 30 is about 0.4–0.5 s per decision, about 35–50 s per simulated day. It rises roughly linearly
with run length: about 11–16 s a day of it is the open-activity scan alone at day 30, 22–32 s at day 60 and 33–47 s at
day 90; the memory copies are about 10, 19 and 29 s a day at days 30, 60 and 90. This is small next to the store-commit
chapter's saving, and it is taken because it is free of any quality risk.

### Model cost of the 30-day run, by call class

The rules of this chapter's call classes were run on all 3,762 recorded calls of the run
[R `call_classes.json`]. Every call fell into one of the six classes; none was unrecognized.

| Class | Calls | Prompt tokens | Cache-hit input (rate) | Output tokens | Peak share | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- |
| `fresh_decision_turn` | 2,139 | 67,580,513 | 12,392,958 (0.183) | 5,272,509 | 0.351 | 15.22 |
| `turn_with_history` | 1,177 | 46,561,206 | 41,961,984 (0.901) | 3,505,174 | 0.340 | 3.88 |
| `truncation_retry` | 33 | 1,091,411 | 163,712 (0.150) | 4,419 | 0.364 | 0.20 |
| `consolidation` | 342 | 555,921 | 0 | 148,030 | 0.357 | 0.23 |
| `reflection` | 29 | 117,423 | 11,136 | 13,332 | 0.345 | 0.03 |
| `plan_day` | 42 | 873,407 | 252,672 (0.289) | 21,807 | 0.357 | 0.14 |
| Total | 3,762 | | | | | 19.70 |

The run's `quality.json` reported `by_purpose.decision` as 3,720 calls. That is the first five rows together. The
consolidations and reflections are indistinguishable there from decision turns [O issue #15].

Off-peak:
- 1,310 calls (34.8%) ran in DeepSeek's peak band. They cost $9.72 and would have cost $4.86 off-peak [R].
- Peak is 35 of 168 hours a week. Against a launch at a random time the expected saving is about $3.1 per 30-day run
  (16%); $4.86 is this run's Wednesday–Thursday timing only [R `review_model.json`].
- Responses in both bands report the same model and system fingerprint, and their reasoning length and protocol-error
  rate do not differ by band [R `review_model.json`, r5 and r6].
- Nothing in the prompt path reads the wall clock [M `review_model.json`], so launch time changes cost only.
- The worst stall of the run (605.6 s) was off-peak. No claim is made that off-peak launches reduce restarts.

## Behaviour

### Open activities computed once per observation build

**Definition.** The *open activities* of an agent are its activities for which `activity_control.concludable` is true:
active, paused, or pending conclusion (`activity_control.py:91-93`).
- They are listed in `state.activities` iteration order. That is today's dict order, which the store-and-state chapter
  keeps through checkpoints.
- They are computed by the new `activity_control.open_activities(state, agent_id)`.

**Who reads them.** Only predicates that are false for every activity outside {active, paused, pending conclusion} may
read open activities instead of the whole state:

| Reader | Predicate | Today | After |
| --- | --- | --- | --- |
| `activity_control.pending_conclusions` | `is_pending_conclusion` | all activities of the agent, sorted | open activities, sorted |
| `activity_control.slot_activities` (and `free_slots`, `slot_refusal`) | `occupies_slot` | all activities of the agent, sorted | open activities, sorted |
| `admission._occupant` | `occupies_slot` and the slot in `resource_claims`, first match | all activities in dict order | open activities in the same order |
| `admission._sleep_refusal`, the "asleep" test | `kind == SLEEP and status == ACTIVE` | all activities of the agent | open activities |

Nothing else changes reader:
- `visible_names` (`activity_control.py:182-194`) keeps reading every activity, because names and the 「（第二次）」 numbering
  count concluded activities.
- `tool_layer.tool_layer`'s activity targets (`tool_layer.py:119-121`) keep their own scan.
- `observe._name_bindings` (`observe.py:113-124`) keeps its own scan.
- `observe._evidence` (`observe.py:132-134`) keeps its own scan.
- `models.active_activities` and its callers are unchanged. This includes `_report_refusal`'s conflict scan
  (`activity_control.py:314`).
- `admission._travel_refusal` (`admission.py:172-174`) is unchanged.
- `location_activities.held_refusal` (`location_activities.py:184-198`) is unchanged.

**Where they are computed.**
- **Observation.** `ObservationBuilder._layer_context` (`observe.py:106-111`) computes them once from
  `self.context.state` and stores them on the new last field `LayerContext.open_activities`. Every tool-layer, todo-layer
  and refusal evaluation of that observation build reads that tuple:
  - `tool_layer.refusal_for` passes `context.open_activities` to `pending_conclusions`;
  - `tool_layer._admission_context` passes `context.open_activities` into the new `admission.Context.open_activities`.
- **Other `_layer_context` callers.** `continuation.same_second_chain` (`continuation.py:61`) and
  `day_plan_inputs._experience_sentences` / `_result_sentences` (`day_plan_inputs.py:141`, `:154`) also build a
  `LayerContext` through `_layer_context`. They get the field computed the same way, from the same engine state, but
  none of their readers (`records_after`, `experience_sentence`, `record_lines`, `_delivery_records`) calls
  `availability` or `refusal_for`, so the tuple is never read there. That costs one scan of the agent's activities per
  continuation chain and per plan input, the same as one `_agent_activities` call today.
- **Acceptance.** Each acceptance path computes them from the very `state` it builds its `admission.Context` from:
  - `StartGroups._verified`, once, used for both the pending check and the `Context`;
  - `StartGroups._start_travel`;
  - `ActionHandlers._admission_context`, every call, so the per-activity loop in `_report_status`
    (`session_actions.py:194-197`) still sees the evolving `changed`. This matters: when `report_status` names two
    paused activities and resuming the first fills the last free slot, the second must be refused with
    `SESSION_LIMIT_EXCEEDED` naming the first. A tuple taken before the loop would miss the first (paused activities
    hold no slot) and give `RESOURCE_CONFLICT` instead. A new test and a mutation pin this (see Tests);
  - `ActionHandlers._conclude_activity` (`:97`) and `_control` (`:461`);
  - `DecisionCoordinator._round_schedule` (`decision_coordinator.py:527`).

**Invariants.**
- For every `LayerContext`: `open_activities == activity_control.open_activities(engine.state, agent_id)` for as long
  as the `LayerContext` lives. The observation build only reads `engine.state` and never replaces it.
  `_product_window` writes `pending_presentation`, not the state. The `LayerContext` of an observation lives only
  inside `_observation_data` (`observe.py:27-52`); the observation commit and `same_second_chain` run after it is gone.
- For every `admission.Context`: `open_activities == activity_control.open_activities(state, agent_id)` of its own
  `state` and `agent_id`.
- `control_refusal` is only called for an activity of `context.agent_id`. The tool layer filters targets by agent
  (`tool_layer.py:119-121`), and acceptance resolves names to the agent's own activities (`session_actions.py:270-301`).
  `_report_refusal` therefore uses `context.open_activities` where it used `activity.agent_id` today.
- The tuple holds the same `ActivityState` objects as `state.activities`. No reader mutates them, and the tuple lives
  only for one observation build or one admission check, so no later write to the state can be seen through it.

**Why byte-identical.**
- Each changed reader applies a predicate that is false outside the open set. The filtered sequence is therefore the
  same items in the same relative order.
- `next()` in `_occupant` returns the same first match, because the open tuple keeps world order. That is the dict-order
  behaviour the activity-order fix is about.
- `sorted()` with the unique key `(started_at, activity_id)` returns the same tuple.
- `any()` returns the same boolean.
- Proven on five day-23 to day-30 checkpoints of the 30-day run, four of them with an activity pending conclusion. The
  canonical observation bytes were equal in all five (433,869 to 505,875 bytes) [R `proof_observation_bytes.json`].

**Failure handling.** No new failure branch. Every refusal text, code and log line is produced by the same functions as
today.

**Deleted.** `activity_control._agent_activities` (`activity_control.py:480-485`). Its sorting moves to
`_in_start_order`, and its filtering moves to the callers.

### Availability evaluated once per action row

**Rule.**
- `annotated_bindings` (`tool_layer.py:109-113`) stays the only evaluation of a start candidate's availability. It writes
  `availability(...)["reason"]` into each private binding action as `note`, for every action, locked or not.
- `tool_layer._action_row` stops calling `availability`. It writes `"available": action["note"] is None` and
  `"reason": action["note"]` in the same key position as today: `action, detail, available, reason`, then the four facts.
- `_activity_target` (`tool_layer.py:205-216`) and `todo_layer` (`todo_layer.py:44`) still call `availability` for
  control tools. They evaluate each tool once, as today.

**Precondition.** `tool_layer.tool_layer` receives only bindings produced by `annotated_bindings`. Its single caller
`observe.py:46` already passes them (`observe.py:36`).

**Why byte-identical.**
- `availability` reads only the state, the agent, the names, the open activities, the inputs and the candidate's
  `binding` and `action`. It never reads `context.bindings`.
- The candidate's row in `annotated_bindings` differs from the one in `tool_layer` only by the `note` keys of its
  actions. No refusal check reads `note` (`name_resolution.py:177` reads it only for host correction options).
- So the reason recorded in `note` equals the reason a second evaluation returns.
- `note is None` is exactly "available": `availability` writes `reason = None` only when there is no refusal, and every
  `Refusal` carries a non-empty `str` reason (`guidance.default_reason`, `rejection_guidance`'s `or default_reason`,
  `items_refusal`'s joined sentences, and literal texts at every other construction site).
- Evaluating once instead of twice moves no later random draw: admission and quotes use only keyed streams
  (`randomness.random_stream(state.random_state, key)` in `basket.py:40-43` and `purchase_offers.py:114-143`), which
  advance no state, and the build never writes the world.
- Proven byte-identical in the probe above, and earlier by the reviewer at seq 45241 (467,349 bytes).

**Log effect.**
- Each row's admission now runs once per observation instead of twice. This halves these ERROR lines:
  - `Purchase quote was refused: …` (`admission.py:264`);
  - `Travel route was refused: …` (`admission.py:182`);
  - the `WorldMock failure: …` line that the error constructor writes for each refused quote or route
    (`errors.py:12`).
- `Purchase preview is unavailable: …` (`tool_layer.py:201`) is written by `_purchase_facts`, not by availability. It
  stays at once per purchase row.
- The logging rule itself is unchanged, per the "Decisions" row.

**Failure handling.** None new. A binding action without `note` reaching `_action_row` is a programming error and raises
`KeyError` from the dictionary read. No caller can produce it.

**Deleted.** The second `availability` call in `_action_row` (`tool_layer.py:161`).

### Memory reads from the loaded episode cache

**Rule.**
- `WorldMockMemoryStore` (`src/worldmock/agents/life_memory_store.py:46`) gains two read-only methods over the cache that
  `AgentMemoryStore._load_episodes_cache` keeps (vendor AS2 `agent/memory.py:559-567`). The restore path fills that cache
  through `validate_existing` (`memory.py:314-334`); the first-run path fills it lazily on first read.
  - `episode_ids() -> set[str]`: `{str(item["id"]) for item in self._load_episodes_cache()}`.
  - `unconsolidated_view() -> list[dict[str, Any]]`: the same selection as `AgentMemoryStore.unconsolidated`
    (`memory.py:848-864`) in every state a run can reach. It returns the cached record objects in a new list instead of
    a deep copy. The consolidation cursor `last_consolidated_episode_id` is still read from `state.json` through
    `self.read_state()` on every call:
    - no cursor gives all records;
    - a cursor found at index `i` gives `records[i + 1:]`;
    - a cursor that names no stored record is a broken workspace. It logs ERROR
      `Last consolidated memory episode does not exist` and raises `ValueError` with the same text, the check
      `validate_existing` already makes at restore (`memory.py:331-333`). AS2's `unconsolidated` silently returns
      every record there; that branch is unreachable in a run (the cursor is only ever set to the id of the last stored
      record by `mark_subjective_consolidated`, records are never removed, and a restore refuses a dangling cursor), so
      the view does not copy it as a fallback.
- `WorldMockMemoryRuntime` (`src/worldmock/agents/life_memory.py`) uses them:
  - `after_step` `:106` and `wake_reflection` `:133`: `known = store.episode_ids()`;
  - `_consolidation_due` `:202`: iterates `self._require_store().unconsolidated_view()`;
  - `_consolidate_subjective` `:240`: `episodes = store.unconsolidated_view()`.

**Ordering kept.**
- `known` is still taken before `_append_subjective` mutates or merges cache records (`life_memory.py:106` before
  `:108`). It is a new `set[str]`, so later merges cannot change it.
- `_consolidate_subjective` builds its payload from `episodes[-30:]` before the model call (`:243-247`). Nothing touches
  the cache between the read and the payload. Calls are strictly sequential: `asyncio.to_thread` is awaited and there
  are no concurrent tasks.

**Read-only contract.**
- Callers never mutate the returned records and never keep them across a store write.
- The two call sites only read `id`, `source`, `importance`, `type`, `text` and `time`.

**Why byte-identical.**
- The ids, the unconsolidated selection and the source filter are computed from the same records as today. Nothing is
  mutated.
- `MEMORY.md`, `episodes.jsonl`, `state.json`, the consolidation request payload and the `memory_updated` evidence
  (hashes, created and merged ids) are unchanged.
- Proven on a copy of the 30-day workspace (2,692 episodes) [R `proof_memory_reads.json`]. Ids and the unconsolidated
  selection were equal to today's copies in four cases:
  - the workspace cursor;
  - no cursor;
  - the cursor on the last episode;
  - after one merge plus one new episode.

**Failure handling.** `read_state` and `_load_episodes_cache` keep their strict errors (`memory.py:306-312`,
`_read_episodes_file` at `:569`). The one new branch is the dangling cursor above (ERROR log, `ValueError`), reachable
only from a workspace edited outside the engine.

**Not changed.**
- `_reflection_episodes` (`life_memory.py:262`) keeps `iter_episodes()[-30:]`. It runs once a day and its records are
  passed on to validation.
- `store.recent()` in `build_context` is not on the LifeAgent path.
- AS2's `mark_subjective_consolidated` (`memory.py:428-445`) copies once per consolidation (342 per 30 days) through
  `recent(limit=1)`. Changing it needs an AS2 patch for no material gain.

### Prompt token count reused for the usage log

**Rule.**
- `prompt_capacity.fit_request` returns `FittedRequest(request, prompt_tokens)` instead of the bare request.
  `prompt_tokens` is `measured - reserve` taken from the last `request_token_count` call in the branch that returns.
  `reserve` is the validated `request["max_tokens"]` (`prompt_capacity.py:74-77`); condensation and step 0 never change
  `max_tokens`. So `prompt_tokens == prompt_token_count(fitted.request)` on every return path:
  - under the threshold (`:79-81`);
  - after step 0 (`:84-86`);
  - after a condensation step (`:94-96`).
- `compare_usage(estimate, response)` takes that number. It no longer calls `prompt_token_count`. Its log line keeps the
  same text and the same value: `Model prompt token estimate checked: estimated=%s actual=%s difference=%s`.

**Carrier across the AS2 boundary.**
- For memory calls, `LifeAgent._dispatch_memory_llm` owns fit, request evidence, dispatch and response evidence.
- For decision turns, the request comes from `LifeAgent._model_request` (`life_agent.py:329-357`), but the response
  evidence is recorded by locked AS2 code: `_complete_react_once` → `_call_react_llm` → `_record_react_response` with
  `self._last_model_request` (vendor `agent/base/agent_runtime.py:929-958`, `:725-752`, `:1016-1040`).
- The request dict and the agent are the only objects that travel from `_model_request` to the response. The count must
  not enter the request dict, which LiteLLM would forward. So `LifeAgent` keeps the last `FittedRequest` as
  `self._life_fitted`, declared on the class with a `#:` comment and the default `None`:
  - It is set in exactly two places: `_model_request` and `_dispatch_memory_llm`.
  - It is read in exactly one place: `_usage_estimate`, which checks that `fitted.request is evidence.request`.
  - The identity holds on every real path. AS2 stores the very dict `_model_request` returns as
    `_last_model_request` (`agent_runtime.py:944-945`) and hands that object to the response evidence
    (`:1030-1040`); `dispatcher.call(**request)` unpacks it into a new keyword dict and does not replace it.
    `LifeAgent._record_model_evidence` rebuilds the evidence with `dataclasses.replace`, which keeps the same `request`
    object. `_dispatch_memory_llm` passes its fitted request to both evidence records itself.
  - Between a fit and its response evidence no other fit can happen. `_call_react_llm` records the response before any
    tool runs. Memory calls happen only before the observation (reflection phase) or after the decision loop (step
    end). The read-only Q&A path never binds a decision context, but still goes through `_model_request`, so the check
    holds there too.
  - The only other AS2 evidence path, the prelude decision statement (`agent_runtime.py:577-621`), is off for LifeAgent:
    it does not override `prelude_decision_statement_request`, whose AS2 default returns `None` (`:541-551`). If it were
    ever switched on, its responses would fail the check loudly instead of logging a recount.
- The same count reaches the log as today because nothing changes the request between the fit and the response
  evidence: the messages list is appended to only after `_record_react_response` (`agent_runtime.py:745-751`), and
  the recorded-evidence chapter's in-process digest check already relies on the dispatched request being unchanged.

**Deviation note.** The research review asked for "no hidden agent state". On the decision path there is no carrier
other than the agent without either a request-dict field or an AS2 patch. The attribute is therefore declared on the
class, single-writer and single-reader, and checked by identity on every use, so a stale count can never be logged.
LifeAgent already keeps its per-decision request state this way (`_life_budget`, `_life_request_models`). This is the
narrowest form the code allows; the integrator should confirm it.

**Failure handling.** If `_usage_estimate` finds no fitted request (`None`), or one fitted for another request object,
it logs ERROR `Model prompt token estimate does not belong to the recorded request` and raises
`RuntimeError("model prompt token estimate does not belong to the recorded request")`.
- This sits at the place where `compare_usage` can already raise `RuntimeError("model request token count failed")`
  today, after the response evidence is committed.
- It is a host programming error and fails loudly.
- The check runs on every response, including one without an integer `usage.prompt_tokens` (where `compare_usage`
  then logs nothing, as today), so a broken carrier fails on the first response of any test or run.

**Why byte-identical.** The count never reaches a request, prompt, event or decision. The log line carries the same
number, because it is the same function on the same request.

**Deleted.** The recount inside `compare_usage` (`prompt_capacity.py:172`).

### Model usage per call class in `quality.json`

**Rule.**
- `quality.json` `model_usage.by_purpose` is replaced by `model_usage.by_call_class`.
- A call's class is decided only from its `model_request` event (`event.data`). The response's copy of the request is
  never read. The recorded-evidence chapter writes `"request": null` there.
- The fields read are exactly `data.purpose`, `data.call_id`, and inside `data.request`: `purpose`, `extra_body`,
  `tools[].function.name` and `messages[].role`. The post-run view of the whole-history chapter keeps all of them in its
  request outline (its reader table lists `_call_class`); any later reduction of `model_request` must keep them too.
- Nothing about calls, prompts, events, the export manifest or `behavior_metrics` changes.

Classification, first match wins (`quality._call_class`):

| Class | Request event shape |
| --- | --- |
| `plan_day` | `data.purpose == PLAN_DAY_PURPOSE` (`"plan_day"`) |
| `unrecognized` | any other non-null `data.purpose`, or `data.request` not a dict |
| `fresh_decision_turn` | `decision_start.is_action_request(request)` (no purpose, `start_main_activity` among tools, thinking enabled) and the message roles are exactly `["system", "user"]` |
| `turn_with_history` | `is_action_request(request)` and any other roles. These are later turns of a decision and a request that starts with a same-second continuation chain |
| `truncation_retry` | `start_main_activity` among `request_tool_names(request)` and `request["extra_body"] == life_truncation.RETRY_THINKING` (`{"thinking": {"type": "disabled"}}`, the switch `LifeAgent._model_request` writes for the retry after a truncation, `life_agent.py:351`; memory calls carry the same switch but never `start_main_activity`) |
| `consolidation` | no tool names (no `tools` key, or an empty list): memory consolidation, including the wake-phase one |
| `reflection` | tool names exactly `["submit_reflection"]` (`REFLECTION_TOOL`): bedtime reflection and its validation retries |
| `unrecognized` | anything else, for example a request with `start_main_activity` whose thinking switch is neither the action one nor the retry one |

The retry is matched on its own switch, not by elimination, so a future change of the action request's thinking
switch shows up as `unrecognized` instead of being counted as retries. All 33 retries of the 30-day run carry thinking
`disabled` [R `call_classes.json` shapes], and `_model_request` writes `extra_body` only as `dict(RETRY_THINKING)` or
`dict(ACTION_THINKING)`.

Report rows (`quality._usage_by_call_class`):
- The six classes are always listed, in the order of `CALL_CLASSES`:
  `fresh_decision_turn, turn_with_history, truncation_retry, consolidation, reflection, plan_day`.
- An `unrecognized` row is appended only when at least one request event or usage entry is unrecognized. Usage entries
  whose `call_id` has no request event are unrecognized.
- On every export from current code all calls are recognized (3,762 of 3,762 in the 30-day run). The row exists so that
  a future request shape cannot vanish silently, in the way `input_warnings.unrecognized_code_counts` already works.

Each row is an object with exactly these keys:

| Key | Value |
| --- | --- |
| `call_class` | the class name |
| `request_events` | number of `model_request` events of this class, classified per event as `by_purpose` counted them |
| `provider_responses` | number of selected terminal responses with a provider response (same selection as `model_usage.provider_responses`) whose call's class is this one |
| `token_usage` | `_aggregate_tokens(rows, reported)`, the same object `by_purpose` carried: `responses_with_reported_usage`, `provider_responses_missing_usage`, `calls_without_provider_response` (always 0 here, as in `by_purpose`), `known_tokens`, `complete_tokens` |
| `estimated_responses` | number of these responses with a price estimate (a priced response) |
| `priced_tokens` | `null` when there is no priced response. Otherwise `{"cache_hit_input_tokens", "cache_miss_input_tokens", "output_tokens"}`, summed over the priced responses from `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens` and `completion_tokens` |
| `peak_responses` | number of priced responses in the `peak` band |
| `peak_share` | `round(peak_responses / estimated_responses, 6)`, or `null` without priced responses |
| `known_estimated_cost_usd` | `round(sum of estimated_cost_usd, 12)` over priced responses, or `null` without priced responses |

Invariants:
- Σ `request_events` over all rows == `model_usage.request_events`.
- Σ `provider_responses` == `model_usage.provider_responses`.
- Σ `known_estimated_cost_usd` (non-null rows) == `estimated_cost.known_estimate_usd` up to the 12-digit rounding.
- Current code records no purpose other than `plan_day`, writes the same `purpose` on a call's request and response
  events, and commits every request event before its response (so `orphan_terminal_call_ids` is empty and a cutoff
  cannot orphan a response). Therefore, on every export from current code:
  - the five non-`plan_day` classes plus `unrecognized`, summed field by field (`request_events`, `provider_responses`,
    the three `token_usage` counts and `known_tokens`), equal the old `by_purpose[decision]` entry;
  - the `plan_day` row carries the old `by_purpose[plan_day]` entry's `request_events`, `provider_responses` and
    `token_usage` unchanged.

  So no information of the old section is lost. The complete-day comparison below checks both equalities on real
  exports. (`by_purpose` grouped responses by the response event's `purpose`; a response without a request event would
  have been grouped by its own `purpose` there and is `unrecognized` here. No current writer produces one.)

The peak band is unchanged (`_peak_band`, `quality.py:680-691`): UTC Monday–Friday 01:00–04:00 and 06:00–10:00, keyed on
the request's `recorded_at`. It ignores Chinese public holidays: a call on a weekday holiday is billed off-peak by the
provider, but `quality.json` prices it as peak. This is documented in the design text and the manual, not coded. The
review judged a holiday table not worth it.

**Structure number.**
- `quality.json` `schema_version` goes from 8 to 9, shared with the lineage section of the network-and-restarts
  chapter. In the single integration branch, whichever of the two chapters lands first bumps 8 → 9; the other leaves it
  at 9.
- Export certification recomputes the whole report (`docs/design/05-recording-engine.md`「完整导出认证」), so an old
  export no longer certifies under the new code. That is by design: no compatibility layer.

**Failure handling.** None new. The classifier never raises; malformed shapes are `unrecognized`.

**Deleted.**
- `quality._usage_by_purpose` (`quality.py:753-764`).
- The `by_purpose` key of `model_usage`.
- The `purpose` key of the internal usage entry, replaced by `call_class`.
- `behavior_metrics._request_tool_names`, moved unchanged to `decision_start.request_tool_names`.
- The `PLAN_DAY_PURPOSE` definition in `day_planning.py:29-30`, moved to `decision_start.py` with the same value; its
  comment gains the quality report as a reader.
  - The reason for the move: `quality.py` must not import `day_planning`, which pulls LiteLLM through
    `prompt_capacity` (measured 52 s import under load). `quality.py` imports no LiteLLM today. The new imports
    `decision_start`, `life_truncation` and `life_reflection` only add modules whose dependencies `quality.py` already
    loads (re-checked: importing the three after `worldmock.footprints.quality` takes 0.01 s and loads neither
    `litellm` nor `agentsociety2`).

### Off-peak launch guidance (operator step, no code)

- The runtime manual gains a section saying when to launch long model-backed runs:
  - outside DeepSeek's peak band;
  - on weekends, from Friday 10:00 UTC to Monday 01:00 UTC (63 h);
  - on weekdays from 10:00 to 01:00 UTC, and from 04:00 to 06:00 UTC.
- It states the saving: half the cost of whatever share of calls falls in peak hours. That is about $3.1 per 30-day run
  against a random launch time, and $4.86 for the October run's timing.
- It explains how to check: the `peak_share` of each class in `quality.json`.
- It notes that `quality.json` prices weekday Chinese public holidays as peak.
- It notes that the pricing page must be checked before launch, because `quality.json` uses the frozen
  `pricing_snapshot` (`checked_at` 2026-09-15).
- The engine does not pause in peak hours. An engine gate is "Awaiting the owner".

## Interfaces and files

Line numbers are today's, in the worktree `D:\code\WorldMock\.claude\worktrees\lrc` (main `0bd2de65`). Every added
function has a Chinese Doxygen `@brief`. Every changed function keeps ≤ 50 lines and ≤ 5 parameters, and every line
≤ 120 characters.

### `src/worldmock/life/activity_control.py`

- **New** after `concludable` (`:91-93`):

  ```python
  def open_activities(state: WorldState, agent_id: int) -> tuple[ActivityState, ...]:
      """@brief 人物的未了活动：进行中、暂停中或待收尾（concludable 为真），按 state.activities 的次序。

      只对未了活动成立的判定读它，不再逐行扫描全部活动：占槽位（occupies_slot）、待收尾（is_pending_conclusion）与睡着
      （进行中的睡眠）。活动名（visible_names）、工具层的活动目标与私有绑定要读全部活动，不读它。一次观察只算一次
      （observe 放进 LayerContext，再带进 admission.Context）；受理各自从所受理的那份世界算。
      """
      return tuple(item for item in state.activities.values() if item.agent_id == agent_id and concludable(item))
  ```

- **Changed** `pending_conclusions` (`:76-78`):

  ```python
  def pending_conclusions(activities: Sequence[ActivityState]) -> tuple[ActivityState, ...]:
      """@brief 这些活动里待收尾的，按开始时刻与身份稳定排序；传人物的未了活动（open_activities）。"""
      return _in_start_order(item for item in activities if is_pending_conclusion(item))
  ```

- **Changed** `slot_activities` (`:81-83`):

  ```python
  def slot_activities(activities: Sequence[ActivityState]) -> tuple[ActivityState, ...]:
      """@brief 这些活动里占槽位的（进行中与待收尾，暂停的不占），按开始时刻与身份稳定排序；传人物的未了活动。"""
      return _in_start_order(item for item in activities if occupies_slot(item))
  ```

- **Changed** `free_slots` (`:107-109`):

  ```python
  def free_slots(activities: Sequence[ActivityState]) -> int:
      """@brief 人物剩余的活动槽位；传人物的未了活动。"""
      return SLOT_LIMIT - len(slot_activities(activities))
  ```

- **Changed** `slot_refusal` (`:387-403`):
  - The new signature is `slot_refusal(state: WorldState, activities: Sequence[ActivityState], needed: int,
    names: Mapping[str, str]) -> Refusal | None`.
  - The body uses `free_slots(activities)` and `slot_activities(activities)`. `state` is still needed by
    `_slot_next_step` (`pausable` reads `current_time`).
  - The docstring's `:param` list gains `:param activities: 人物的未了活动（open_activities）。`
  - The reason and next-step text is unchanged.

- **Changed** `visible_names` `:191`:

  ```python
      activities = _in_start_order(item for item in state.activities.values() if item.agent_id == agent_id)
  ```

- **Changed** `_report_refusal` `:311`:

  ```python
      refusal = slot_refusal(context.state, context.open_activities, 1, context.activity_names)
  ```

- **Changed** `_conclude_refusal` `:379`:

  ```python
      waiting = pending_conclusions(context.open_activities)
  ```

- **Deleted** `_agent_activities` (`:480-485`). In its place:

  ```python
  def _in_start_order(activities: Iterable[ActivityState]) -> tuple[ActivityState, ...]:
      """@brief 活动按开始时刻与身份稳定排序。"""
      return tuple(sorted(activities, key=lambda item: (item.started_at, item.activity_id)))
  ```

`Sequence` and `Iterable` are already imported (`:6`). The three public readers take a `Sequence` (every caller
passes the `open_activities` tuple), so `slot_refusal`, which reads its argument twice, can never be handed a
one-shot generator; only the private `_in_start_order` takes an `Iterable`.

### `src/worldmock/life/admission.py`

- **Changed** `Context` (`:28-41`). A required field goes third, right after `agent_id`:

  ```python
      #: 唯一人物身份。
      agent_id: int
      #: 本人在 state 里的未了活动（activity_control.open_activities(state, agent_id)）；槽位、睡着与待收尾的判定只读它。
      open_activities: tuple[ActivityState, ...]
      #: 完整地点显示名。
      place_names: Mapping[str, str]
  ```

  The remaining fields and defaults are unchanged: `activity_names` default `{}`, `inputs` default `None`.

- **Changed** `_occupant` (`:106-110`):

  ```python
  def _occupant(context: Context, slot: str) -> ActivityState | None:
      """@brief 占着这个槽位的本人活动：进行中和待收尾占槽位，暂停的活动让出槽位；只看本人的未了活动，次序同世界。"""
      return next((activity for activity in context.open_activities
                   if slot in activity.resource_claims and activity_control.occupies_slot(activity)), None)
  ```

- **Changed** `_sleep_refusal` `:149-150`:

  ```python
      asleep = any(activity.kind == ActivityKind.SLEEP and activity.status == ActivityStatus.ACTIVE
                   for activity in context.open_activities)
  ```

### `src/worldmock/life/tool_layer.py`

- **Changed** `LayerContext` (`:25-31`). A new last field:

  ```python
      bindings: list[dict]
      #: 本次观察开始时算一次的本人未了活动（activity_control.open_activities），经 _admission_context 带进准入上下文，
      #: 待收尾、槽位与睡着的判定只读它；活动目标与私有绑定不读它。
      open_activities: tuple[ActivityState, ...]
  ```

  Import `ActivityState` from `worldmock.life.models` next to `ActivityStatus` (`:19`). `with_bindings`
  (`:219-221`) keeps `replace(context, bindings=…)`.

- **Changed** `_admission_context` (`:73-76`):

  ```python
      return admission.Context(context.state, context.agent_id, context.open_activities, places,
                               context.names["activities"], context.inputs)
  ```

- **Changed** `refusal_for` `:87`:

  ```python
      waiting = activity_control.pending_conclusions(context.open_activities)
  ```

- **Changed** `annotated_bindings` docstring (`:110`): `@brief 私有绑定仅附同源资格原因：每条动作（含 locked）的 note 是这一行唯一的一次可用性求值，
  工具层的动作行直接取它。`

- **Changed** `tool_layer` docstring (`:117`): append `context.bindings 必须是 annotated_bindings 的结果。`

- **Changed** `_action_row` (`:147-162`). The return becomes:

  ```python
      return {"action": name, "detail": action["detail"], "available": action["note"] is None,
              "reason": action["note"], **facts}
  ```

  The docstring becomes `@brief 八个字段的公开动作行只表达调用里要写的名称、真实价格、时长和可用性；可用性取私有绑定上
  annotated_bindings 已写下的原因（note 为空即可用），不再求值第二次。`

### `src/worldmock/life/observe.py`

- **Changed** `_layer_context` (`:106-111`):

  ```python
      def _layer_context(self, agent, bindings: list[dict]) -> LayerContext:
          """@brief 活动与目标名称来自同一世界，不再绑定社交或消息对象；本人的未了活动在这里算一次，整次观察共用。"""
          goals = [goal.goal for goal in visible_goals(self.context.state, agent.agent_id) or ()]
          taken = [*goals, *(row["object"] for row in bindings)]
          names = activity_control.visible_names(self.context.state, agent.agent_id, taken)
          current = activity_control.open_activities(self.context.state, agent.agent_id)
          return LayerContext(self.context, agent.agent_id, names, bindings, current)
  ```

### Acceptance paths

- `src/worldmock/life/start_group.py`, `StartGroups._verified` (`:112-129`):
  - Before `:118` add `open_activities = activity_control.open_activities(state, agent_id)`.
  - `:118` becomes `waiting = activity_control.pending_conclusions(open_activities)`.
  - `:125` becomes:

    ```python
            context = admission.Context(state, agent_id, open_activities, place_names(self.context.catalog), names,
                                        self.context.inputs)
    ```

- `start_group.py`, `StartGroups._start_travel` (`:255-264`). `:259` becomes:

  ```python
          open_activities = activity_control.open_activities(state, pending.agent_id)
          context = admission.Context(state, pending.agent_id, open_activities, places, {}, self.context.inputs)
          trip = admission.planned_trip(context, row)
  ```

- `src/worldmock/life/session_actions.py`:
  - `:97` becomes
    `waiting = activity_control.pending_conclusions(activity_control.open_activities(state, pending.agent_id))`.
  - `:461` is the same with `changed`.
  - `_admission_context` (`:415-417`) becomes:

    ```python
        def _admission_context(self, state: WorldState, agent_id: int, names: dict | None = None) -> admission.Context:
            """@brief 观察与受理共用固定槽位和在场判定；本人的未了活动从所受理的这份世界算。"""
            return admission.Context(state, agent_id, activity_control.open_activities(state, agent_id),
                                     place_names(self.context.catalog), names or {}, self.context.inputs)
    ```

- `src/worldmock/life/decision_coordinator.py`, `_round_schedule` (`:506-533`):
  - Before `outcome = …` (`:522`) add `open_activities = activity_control.open_activities(state, agent.agent_id)`.
  - `:527` becomes `pending=bool(activity_control.pending_conclusions(open_activities)),`.

### `src/worldmock/agents/life_memory_store.py`

**New** methods on `WorldMockMemoryStore` (after `append_episodes`, `:49-92`):

```python
    def episode_ids(self) -> set[str]:
        """@brief 已存全部条目的身份集合：直接读已载入的条目缓存，不复制条目。"""
        return {str(item["id"]) for item in self._load_episodes_cache()}

    def unconsolidated_view(self) -> list[dict[str, Any]]:
        """@brief 巩固游标之后的条目（游标取 state.json 的 last_consolidated_episode_id：没有游标是全部，找到游标是它之后
        的条目），返回缓存里的原对象而不是深拷贝：只读，调用方不得修改，也不得在下一次写入之后继续持有。
        游标指向不存在的条目是损坏的工作区，记错误日志并失败，与恢复时 validate_existing 的核验同一说法。"""
        last_id = self.read_state().get("last_consolidated_episode_id")
        records = self._load_episodes_cache()
        if not last_id:
            return list(records)
        for index, item in enumerate(records):
            if item.get("id") == last_id:
                return records[index + 1:]
        logging.error("Last consolidated memory episode does not exist")
        raise ValueError("Last consolidated memory episode does not exist")
```

`logging` is already imported (`life_memory_store.py:6`).

### `src/worldmock/agents/life_memory.py`

- `:106` becomes `known = store.episode_ids()`.
- `:133` becomes `known = store.episode_ids()`.
- `:202` becomes
  `pending = [item for item in self._require_store().unconsolidated_view() if item.get("source") != "reflection"]`.
  That is 118 characters.
- `:240` becomes `episodes = store.unconsolidated_view()`.
- Docstrings are unchanged except `_consolidation_due`: append `读存储缓存的只读视图，不复制条目。`

### `src/worldmock/life/prompt_capacity.py`

- **New** after `ProgressBudget` (`:36-48`):

  ```python
  @dataclass(frozen=True, slots=True)
  class FittedRequest:
      """@brief 容量预检通过、实际要发送的请求，与发送前为它计量的输入 token 数。"""

      #: 实际发送的请求；低于阈值时就是传入的那个对象。
      request: dict[str, Any]
      #: request 的消息与工具定义的 token 数（不含预留输出），等于 prompt_token_count(request)。
      prompt_tokens: int
  ```

- **Changed** `fit_request` (`:70-98`):
  - The return type becomes `FittedRequest`.
  - The three returns become, in order:
    - `return FittedRequest(request, measured - reserve)` at `:81`;
    - the same at `:86`;
    - the same at `:96`.
  - The docstring (`:72`) becomes:

    ```python
        """@brief 请求低于阈值时原样放进结果；否则先结束本秒续接，再按第四章步骤浓缩进度层，每步重计，仍超限即容量故障。
        结果带实际发送的请求与它的输入 token 数（最后一次计量减去预留输出），用量日志直接用这个数，不再重数。"""
    ```
  - The raises and log lines are unchanged.

- **Changed** `compare_usage` (`:166-174`):

  ```python
  def compare_usage(estimate: int, response: dict[str, Any] | None) -> None:
      """@brief 用供应商返回的实际输入 token 校核发送前的估算；估算是容量预检为同一请求计量的数（FittedRequest.prompt_tokens），
      这里不再重数。"""
      usage = response.get("usage") if isinstance(response, dict) else None
      actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
      if not isinstance(actual, int):
          return
      logging.info("Model prompt token estimate checked: estimated=%s actual=%s difference=%s",
                   estimate, actual, actual - estimate)
  ```

  Every line above stays within 120 characters (the longest, the docstring's first line, has 87).

### `custom/agents/life_agent.py`

- **New** class attribute, first in the body of `class LifeAgent` (`:186`), before `description`:

  ```python
      #: 本人物最近一次容量预检的结果：_model_request 与 _dispatch_memory_llm 写入，响应留证后 _usage_estimate 读它的
      #: token 数并核对它属于同一个请求对象；还没有预检时为 None。
      _life_fitted: FittedRequest | None = None
  ```

- `_record_model_evidence` `:208-209` becomes:

  ```python
          if evidence.phase == "response":
              compare_usage(self._usage_estimate(evidence.request), evidence.response)
  ```

- **New** method right after `_record_model_evidence`:

  ```python
      def _usage_estimate(self, request: dict[str, Any]) -> int:
          """@brief 这条响应所配请求在发送前的输入 token 计量：取本人物最近一次容量预检的结果 _life_fitted
          （_model_request 与 _dispatch_memory_llm 写入），它必须是为同一个请求对象算的；对不上是宿主编排错误，记错误日志
          并失败，不重数，也不记别的请求的数。"""
          fitted = self._life_fitted
          if fitted is None or fitted.request is not request:
              logging.error("Model prompt token estimate does not belong to the recorded request")
              raise RuntimeError("model prompt token estimate does not belong to the recorded request")
          return fitted.prompt_tokens
  ```

- `_model_request` `:353-357` becomes:

  ```python
          fitted = fit_request(request, identity["model_metadata"], self._life_budget)
          if len(fitted.request["messages"]) < len(messages):
              self._life_budget = end_continuation(messages, self._life_budget)
          self._life_fitted = fitted
          return fitted.request
  ```

  Append to the docstring: `容量预检的结果另存为 _life_fitted，响应留证后的用量日志用它的 token 数。`

- `_dispatch_memory_llm` `:388` becomes:

  ```python
          self._life_fitted = fit_request(request, identity["model_metadata"])
          request = self._life_fitted.request
  ```

The import at `:60` becomes `from worldmock.life.prompt_capacity import FittedRequest, ProgressBudget, compare_usage,
fit_request` (one line, 99 characters).

### `src/worldmock/life/decision_start.py`

- **New** constant before `_ACTION_TOOL` (`:16-17`), moved from `day_planning.py:29-30`; the comment keeps its
  readers and adds the quality report:

  ```python
  #: 零点计划请求与响应的用途标记；交付窗口、决策 token 统计、行为指标的决策请求与质量报告的调用类按它区分。
  PLAN_DAY_PURPOSE = "plan_day"
  ```

- **New** function, moved unchanged in body from `behavior_metrics._request_tool_names` (`behavior_metrics.py:143-147`):

  ```python
  def request_tool_names(request: Any) -> list[str]:
      """@brief 一次模型请求实际开放的工具名；没有工具或形状不符时为空列表（行为指标与质量报告的调用类共用）。"""
      tools = request.get("tools") if isinstance(request, Mapping) else None
      return [item["function"].get("name") for item in tools if isinstance(item, Mapping)
              and isinstance(item.get("function"), Mapping)] if isinstance(tools, list) else []
  ```

  Add `from collections.abc import Mapping`. `is_action_request` (`:58-73`) is unchanged.

### `src/worldmock/life/day_planning.py`

Delete `:29-30`. Import `PLAN_DAY_PURPOSE` from `worldmock.life.decision_start`. `:171`, `:182` and `:192` are unchanged.

### `src/worldmock/evaluation/behavior_metrics.py`

Delete `_request_tool_names` (`:143-147`). `:159` calls `request_tool_names` imported from
`worldmock.life.decision_start`. `_decision_model_requests` and every population are unchanged.

### `src/worldmock/footprints/quality.py`

- **Imports.** Add:
  - `from worldmock.agents.life_reflection import REFLECTION_TOOL`;
  - `from worldmock.agents.life_truncation import RETRY_THINKING`;
  - `from worldmock.life.action_contract import START_TOOLS`;
  - `from worldmock.life.decision_start import PLAN_DAY_PURPOSE, is_action_request, request_tool_names`.

  None of these is a local import, so `tests/unit/engineering/test_import_graph.py` `DELAYED_IMPORTS` is unchanged. None
  creates a cycle: the four modules and what they import (`footprints.models`, `life.models`, `life.action_contract`,
  `agents.life_feedback`, `life.error_contract`, `life.request_errors`, `model_feedback`, pydantic) never import
  `footprints.quality`. The import-graph test proves it.

- **Constants.**
  - `:40-41`: the comment of `_DECISION_PURPOSE` becomes `#: 输出截断按用途分列时，无 purpose 的请求的分组名。` It is used only by
    `_truncation_quality` (`:884`).
  - New after `_DECISION_PURPOSE`:

    ```python
    #: 模型调用按请求形状分的六类，model_usage.by_call_class 按这个次序逐类列出
    #: （docs/design/05-recording-engine.md「完整导出认证」）。
    CALL_CLASSES = ("fresh_decision_turn", "turn_with_history", "truncation_retry", "consolidation", "reflection",
                    "plan_day")
    #: 请求形状不属于六类、或终态响应没有对应请求事件的调用的分组名；只在出现时列在六类之后。
    _UNRECOGNIZED_CLASS = "unrecognized"
    #: 可计价响应按计价拆开的三种 token。
    _PRICED_TOKEN_KEYS = ("cache_hit_input_tokens", "cache_miss_input_tokens", "output_tokens")
    ```

- **`build_quality_report`.** `:59` docstring `报告结构 8` becomes `报告结构 9`. `:73` `"schema_version": 8` becomes `9`.
  This is shared with the lineage chapter, as described in Behaviour.

- **`_model_quality`** (`:583-618`):
  - After `request_times` add:

    ```python
        classes = {event.data["call_id"]: _call_class(event.data) for event in requests
                   if isinstance(event.data.get("call_id"), str)}
    ```

  - The `usage` comprehension passes `classes.get(event.data["call_id"], _UNRECOGNIZED_CLASS)` as the third argument
    of `_usage_entry`.
  - `"by_purpose": _usage_by_purpose(requests, usage),` becomes `"by_call_class": _usage_by_call_class(requests, usage),`.
    It stays at the same position in the dict (canonical JSON sorts keys anyway).

- **`_usage_entry`** (`:627-643`). The new signature is
  `_usage_entry(event: Event, request_recorded_at: str | None, call_class: str) -> dict`:
  - The `"purpose"` key is replaced by `"call_class": call_class`.
  - After `"tokens"` add:

    ```python
            "priced_tokens": None if cost is None else {
                "cache_hit_input_tokens": usage["prompt_cache_hit_tokens"],
                "cache_miss_input_tokens": usage["prompt_cache_miss_tokens"],
                "output_tokens": usage["completion_tokens"]},
    ```

    A non-null `cost` implies `_estimate_cost` validated all four fields (`:661-667`).
  - The docstring becomes `@brief 提取一次提供商响应的调用类、实际模型、实报 token、可计价时按计价拆开的 token 和可核价格估算。`

- **New** `_call_class`. It is the mutation target for two entries; the snippets must match:

  ```python
  def _call_class(data: dict) -> str:
      """@brief 一次模型调用的类，只凭它的 model_request 事件判定，不读响应里的请求副本：purpose 为 plan_day 是零点计划；
      没有 purpose 时，行动请求（decision_start.is_action_request）消息恰为 system、user 两条是新决策回合，更多是带历史的
      回合；工具含 start_main_activity 且思考开关是截断重试的那一个（life_truncation.RETRY_THINKING）是截断重试；不带
      工具是记忆巩固；工具恰为 submit_reflection 是就寝反思；其余为 unrecognized。"""
      purpose, request = data.get("purpose"), data.get("request")
      if purpose == PLAN_DAY_PURPOSE:
          return "plan_day"
      if purpose is not None or not isinstance(request, dict):
          return _UNRECOGNIZED_CLASS
      if is_action_request(request):
          return "fresh_decision_turn" if _message_roles(request) == ["system", "user"] else "turn_with_history"
      names = request_tool_names(request)
      if START_TOOLS[0] in names and request.get("extra_body") == RETRY_THINKING:
          return "truncation_retry"
      if not names:
          return "consolidation"
      return "reflection" if names == [REFLECTION_TOOL] else _UNRECOGNIZED_CLASS


  def _message_roles(request: dict) -> list:
      """@brief 请求消息逐条的 role；消息不是列表时为空列表，不是对象的消息记为 None。"""
      messages = request.get("messages")
      if not isinstance(messages, list):
          return []
      return [message.get("role") if isinstance(message, dict) else None for message in messages]
  ```

- **New** `_usage_by_call_class`, replacing `_usage_by_purpose` (`:753-764`):

  ```python
  def _usage_by_call_class(requests: list[Event], usage: list[dict]) -> list[dict]:
      """@brief 按调用类分列：六类按 CALL_CLASSES 次序总是列出，unrecognized 只在出现时列在最后；每类给出请求事件数、
      提供商响应数、已知 token、可计价响应按计价拆开的 token、峰时占比与估算成本。"""
      counts = Counter(_call_class(event.data) for event in requests)
      groups: dict[str, list[dict]] = defaultdict(list)
      for item in usage:
          groups[item["call_class"]].append(item)
      seen = counts[_UNRECOGNIZED_CLASS] or groups[_UNRECOGNIZED_CLASS]
      names = (*CALL_CLASSES, _UNRECOGNIZED_CLASS) if seen else CALL_CLASSES
      return [_call_class_usage(name, counts[name], groups[name]) for name in names]


  def _call_class_usage(name: str, request_events: int, rows: list[dict]) -> dict:
      """@brief 一类调用的用量行：峰时占比是峰时可计价响应占可计价响应的比例（六位小数）；没有可计价响应时按计价拆开的
      token、峰时占比与估算成本为 null，不记为零。"""
      reported = [item for item in rows if item["tokens"] is not None]
      priced = [item for item in rows if item["priced_tokens"] is not None]
      peak = sum(item["price_band"] == "peak" for item in priced)
      return {
          "call_class": name,
          "request_events": request_events,
          "provider_responses": len(rows),
          "token_usage": _aggregate_tokens(rows, reported),
          "estimated_responses": len(priced),
          "priced_tokens": {key: sum(item["priced_tokens"][key] for item in priced)
                            for key in _PRICED_TOKEN_KEYS} if priced else None,
          "peak_responses": peak,
          "peak_share": round(peak / len(priced), 6) if priced else None,
          "known_estimated_cost_usd": round(sum(item["estimated_cost_usd"] for item in priced), 12) if priced else None,
      }
  ```

The file grows by about 55 lines (931 → about 985), still under 2,000.

**Recorded format** (`quality.json` is `canonical_json`: sorted keys, `,` and `:` separators, UTF-8, list order kept). An
example row, as it would come out of the 30-day run's `fresh_decision_turn` (`peak_responses` 751 = 0.351099 × 2,139):

```json
{"call_class":"fresh_decision_turn","estimated_responses":2139,"known_estimated_cost_usd":15.222286…,"peak_responses":751,"peak_share":0.351099,"priced_tokens":{"cache_hit_input_tokens":12392958,"cache_miss_input_tokens":55187555,"output_tokens":5272509},"provider_responses":2139,"request_events":2139,"token_usage":{"calls_without_provider_response":0,"complete_tokens":{…},"known_tokens":{…},"provider_responses_missing_usage":0,"responses_with_reported_usage":2139}}
```

## Design chapters

Design text changes before code, per `AGENTS.md`.

**`docs/design/05-recording-engine.md`「完整导出认证」** (`:418-430`):
- `:425`: `质量报告 \`quality.json\` 的结构号是 8。` becomes `9`.
- After the sentence ending `…都报 \`Export quality report disagrees with authoritative events\`。` (`:427-428`), insert
  this paragraph and table:

> 质量报告的 `model_usage.by_call_class` 按调用类分列模型用量。一次调用的类只凭它的 `model_request` 事件判定，
> 不读响应事件里的请求副本；读的是事件的 `purpose` 与 `call_id`，以及请求里的 `purpose`、`extra_body`、各工具的
> `function.name` 与各消息的 `role`：
>
> | 类 | 请求形状 |
> | --- | --- |
> | `fresh_decision_turn` | 没有 purpose 的行动请求（工具含 `start_main_activity`、思考开启），消息恰为 system、user 两条 |
> | `turn_with_history` | 同样的行动请求，消息多于两条：本次决策的后续回合，以及带同一秒续接链开始的请求 |
> | `truncation_retry` | 没有 purpose，工具含 `start_main_activity`，`extra_body` 恰为关闭思考 `{"thinking": {"type": "disabled"}}`：截断之后的那一次重试 |
> | `consolidation` | 没有 purpose，不带工具：记忆巩固，含反思阶段的那一次 |
> | `reflection` | 没有 purpose，工具恰为 `submit_reflection`：就寝反思及其校验重试 |
> | `plan_day` | purpose 为 `plan_day`：零点计划及其校验重试 |
>
> 六类按上表次序总是列出；请求形状不属于任何一类、或终态响应没有对应请求事件的调用归入 `unrecognized`，这一行只在出现时
> 列在最后。每行给出请求事件数、提供商响应数、已知 token（与 `model_usage.token_usage` 同一口径）、可计价响应数、
> 可计价响应的缓存命中输入、未命中输入与输出 token 合计、峰时响应数、峰时占比（峰时响应数除以可计价响应数，保留六位小数）
> 与估算成本；没有可计价响应时后几项为 null，不记为零。各行请求事件数与提供商响应数之和等于 `model_usage` 顶层的同名计数。
> 这一节只读已认证事件，不改变任何事件或请求。
>
> 计价时段按 `pricing_snapshot.peak_utc`：UTC 周一至周五 01:00–04:00 与 06:00–10:00，按请求事件的 `recorded_at` 判定。
> 供应商在中国法定节假日不按峰时计价，质量报告不读节假日：落在工作日节假日的调用按峰时估价，估算成本偏高。

**No design text changes** for the other items:
- **Open activities, availability once.** These are implementation only. The rules in
  `docs/design/04-simulation-engine.md` (「工具层的可用性与受理一致」 `:255`, slot and sleep rules) and the tool-layer row
  contract in `docs/design/06-agent.md:87-92` are unchanged and stay true.
- **Memory reads.** The host implementation of the memory rules in `docs/design/06-agent.md`「进度、待办与记忆」 and
  「宿主模型协议」 is unchanged.
- **Token count reuse.** No design text names the usage log. The 85% capacity rule in `docs/design/06-agent.md` is
  unchanged.
- **`docs/design/07-evaluation.md`.** `behavior-metrics-4` and its gates are unchanged. `_decision_model_requests` keeps
  counting tool-less consolidation requests as decision requests.

**`docs/manual/runtime.md`.** This is the operator manual, not a design chapter, written in Chinese. Insert a new
section between「检查工具交互」and「从检查点创建分支」. The quoted lines below are the text to insert, without the
leading `> `; the first line is the HTML anchor itself, in the form the manual already uses (`runtime.md:26`):

> <a name="runtime-长时间运行的启动时刻"></a>
> ## 长时间运行的启动时刻
>
> DeepSeek 在 UTC 周一至周五 01:00–04:00 与 06:00–10:00（北京时间 09:00–12:00 与 14:00–18:00）按峰时价计费，其余
> 时段半价；按供应商的说明，中国法定节假日不算峰时。引擎不读墙钟，人物经历的一切与启动时刻无关，启动时刻只影响费用。
>
> 长时间的模型运行尽量放在非峰时段：
>
> | 时段（UTC） | 北京时间 | 长度 |
> | --- | --- | --- |
> | 周五 10:00 至周一 01:00 | 周五 18:00 至周一 09:00 | 连续 63 小时 |
> | 工作日 10:00 至次日 01:00 | 18:00 至次日 09:00 | 15 小时 |
> | 工作日 04:00 至 06:00 | 12:00 至 14:00 | 2 小时 |
>
> 省下的钱是落在峰时的那部分调用费用的一半。一周 168 小时里有 35 小时是峰时，随意时刻启动的运行约有两成调用落在
> 峰时；整段放进非峰时段，比随意时刻启动平均省约 16%。例如一次 30 天白领运行共 19.70 美元，35% 的调用在峰时，
> 整段放在非峰时可省 4.86 美元。模型时间每模拟日约半小时，30 天的运行至少要十几个小时；放不进一个非峰时段的运行
> 会跨过峰时，照常完成，只是那段费用高一倍。
>
> 导出后在 `quality.json` 的 `model_usage.by_call_class` 里看每类调用的 `peak_share`：整段落在非峰时段的运行各类都是 0。
> 质量报告按 UTC 工作日时段判定峰时，不读节假日：落在工作日法定节假日的调用实际按非峰时计费，报告里仍按峰时估价。
> 启动前先看一眼供应商的价格页（https://api-docs.deepseek.com/quick_start/pricing/），时段或价格变了以价格页为准；
> 质量报告按冻结的 `pricing_snapshot`（核对日期写在 `checked_at`）估价。

## Tests

All test names are new unless marked *changed*. Every new test module has a Chinese module docstring; test
docstrings follow the existing files.

### Open activities

**New file `tests/unit/life/test_open_activities.py`.** It builds hand-made worlds with
`world()`/`running()` from `tests/unit/test_environment_engine_admission.py`, the way
`tests/unit/life/test_memory_progress_contract.py:11` imports them. Activities are made with
`running(...).model_copy(update={...})`. Set `activity_id`, `status`, `started_at` with `last_settled_at` equal to it,
`ended_at` (≥ `last_settled_at`, only for terminal statuses) and `concluded_at` (≥ `ended_at`, only for completed, failed
or cancelled). That keeps every activity valid under `ActivityState.validate_lifecycle` (`models.py:396-414`), although
`model_copy` does not run it.

- `test_open_activities_are_the_concludable_ones_in_world_order`:
  - The world, inserted in this order, holds:
    - a pending completed activity started at 300;
    - an active one started at 100;
    - a concluded completed one;
    - a paused one started at 200;
    - a truncated one;
    - a pending cancelled one;
    - a concluded cancelled one;
    - a pending failed one;
    - another agent's active one (agent 2 added to `agents`).
  - Assert `open_activities(state, 1)` is exactly the pending completed, active, paused, pending cancelled and pending
    failed activities of agent 1, in insertion order. It must not be start order.
- `test_slot_pending_and_sleep_checks_see_the_same_activities_as_a_full_scan`. On the same world plus an active sleep:
  - For each predicate `occupies_slot`, `is_pending_conclusion` and `lambda a: a.kind == ActivityKind.SLEEP and a.status
    == ActivityStatus.ACTIVE`, the list over all of agent 1's activities in `state.activities` order equals the list
    over `open_activities(state, 1)`.
  - `pending_conclusions(open)` and `slot_activities(open)` equal the full-scan lists sorted by
    `(started_at, activity_id)`.

**`tests/unit/life/test_shared_observation_contract.py`:**
- *changed* `_context` (`:29-31`): `LayerContext(engine.context, 1, names, [], open_activities(engine.context.state, 1))`.
- `test_observation_computes_open_activities_once`:
  - `engine_factory()`, `stage_day_plan`, then start an app use (`act(engine, "app", use_app("哔哩哔哩"))`) so one
    activity is open.
  - Wrap `activity_control.open_activities` with a counter through `monkeypatch.setattr(activity_control,
    "open_activities", counting)`.
  - Build one observation with `engine.observations._observation_data(engine.context.state.agents[1])`, not
    `engine.observe`: `observe` also runs `same_second_chain`, which may build its own `LayerContext` after the
    observation is committed, and that one is not part of the build.
  - Assert exactly one call, with agent 1.
- `test_tool_layer_rows_take_availability_from_the_private_binding_note`:
  - With both slots full, set up as in `tests/integration/runtime/test_query_prompt_contract.py:37-44`.
  - Wrap `tool_layer.availability` with a counter, then observe.
  - Pair each binding target with its private binding row: targets come first in binding order, actions skip
    `locked`. For each, assert `row["available"] is (action["note"] is None)` and `row["reason"] == action["note"]`.
  - Rebuild a `LayerContext` from the current state (names from the observation's `activity_bindings`, bindings from
    `interaction_bindings`, `open_activities(state, 1)`). Assert `availability(layer, Candidate(start_tool(binding["kind"],
    action["action"]), binding, action))` gives the same `available` and `reason`. This proves the reuse equals a fresh
    evaluation.
  - Assert the counter equals Σ `len(row["actions"])` over `interaction_bindings` plus Σ `len(target["tools"])` over
    the activity targets. That is one evaluation per binding action and per activity tool. `todo_layer` imports
    `availability` by name and is not counted.

**Existing tests that change** (constructor or signature only; assertions unchanged):

| File | Change |
| --- | --- |
| `tests/unit/test_environment_engine_admission.py:92` | `Context(state, 1, open_activities(state, 1), {"home": "Home", "store": "Store"}, names or {})` |
| `tests/unit/test_environment_engine_admission.py:195` | `Context(engine.context.state, 1, open_activities(engine.context.state, 1), place_names(…), names, engine.context.inputs)` |
| `tests/unit/life/test_environment_commerce.py:203` | the same pattern |
| `tests/unit/life/test_pause_limit.py:38`, `:146` | the same pattern; at `:38` first bind `state = occupied(activity)` and pass `state` twice |
| `tests/unit/life/test_series_pause_wording.py:49` | the same pattern |
| `tests/unit/start_interfaces/test_grocery_units.py:139` | the same pattern |
| `tests/unit/life/test_activity_boundary_contract.py:95`, `:99`, `:102` | `slot_refusal(state, open_activities(state, 1), …)` |
| `tests/unit/life/test_pause_limit.py:132-136` | the same pattern |
| `tests/evaluation/conftest.py:325` | `pending_conclusions(open_activities(state, agent_id))` |
| `tests/integration/evaluation/test_workload_metrics.py:379` | `pending_conclusions(open_activities(engine.context.state, 1))` |
| `tests/integration/runtime/test_current_runtime_contract.py:172` | `LayerContext(engine.context, 1, names, [], open_activities(engine.context.state, 1))` |
| `tests/integration/runtime/test_same_second_continuation.py:590` | the same pattern |

**`tests/integration/start_interfaces/test_start_group.py`:**
- `test_resuming_two_paused_activities_checks_the_second_against_the_first_resumed`. The scenario was run on main's code
  (scratch `test_resume_two_probe.py`, passed):
  - `engine_factory(mutate=_attentive())`, `stage_day_plan`.
  - Start `use_app("哔哩哔哩")`, `advance_until_ready`, pause it by name.
  - Start `do_at("家", "测试休息")`, `advance_until_ready`, pause it by name.
  - Start `use_app("抖音")`, `advance_until_ready`.
  - `act(engine, "resume-both", report_request(rest_name, bilibili_name), "report_status")`.
  - Assert the result is `rejected` with `SESSION_LIMIT_EXCEEDED`, the message names both the rest and 抖音 activities
    (`Both activity slots are occupied by …`), and the rest activity is still `paused` (the whole request rolled back).
  - With open activities taken before the loop, the second item would pass the slot check and be refused with
    `RESOURCE_CONFLICT` against 抖音 instead.

### Memory reads

**`tests/integration/as2/test_worldmock_memory_boundaries.py`:**
- `test_cache_reads_equal_the_copying_reads_at_every_cursor`:
  - A `WorldMockMemoryStore` on `tmp_path`. Append memories at three times: two similar memories within 24 h that
    merge, and three distinct ones.
  - Assert `episode_ids() == {str(item["id"]) for item in iter_episodes()}` and `unconsolidated_view() ==
    unconsolidated()`:
    - with no cursor;
    - after `mark_subjective_consolidated` (cursor on the last record, so the view is empty);
    - after two more appends (cursor in the middle, so the view is exactly the two new records).
- `test_a_dangling_consolidation_cursor_is_refused`: write `state.json`'s `last_consolidated_episode_id` to an id no
  record has (through `read_state` / `write_state`). `unconsolidated_view()` raises `ValueError` matching
  `Last consolidated memory episode does not exist`, and caplog has the ERROR line.
- `test_cache_reads_return_the_cached_records_without_copying`: with the cursor on record `k - 1` of the cache,
  `unconsolidated_view()[0] is store._load_episodes_cache()[k]`, and with no cursor `unconsolidated_view() is not
  store._load_episodes_cache()` while its items are the cached objects.

**`tests/integration/as2/test_worldmock_life_hooks.py`:**
- `test_step_end_below_the_threshold_reads_memory_without_copying_it`:
  - `monkeypatch.setattr(AgentMemoryStore, "iter_episodes", boom)` and the same for `unconsolidated`, where `boom`
    raises `AssertionError("copying memory read")`.
  - Using `_runtime(tmp_path)` (`:374`), run `after_step` with two memories, below both thresholds.
  - Assert the outcome's created ids equal the written ids. Assert `consolidated is False`.
  - Consolidation itself still copies once inside AS2's `mark_subjective_consolidated`, so the test stays below the
    threshold.

### Token count reuse

- *changed* `tests/unit/life/test_native_environment_contract.py::test_capacity_uses_verified_actual_context_and_enforces_verified_output_limit`
  (`:128-135`): `fit_request(payload, metadata(window=500000))` returns a `FittedRequest` with `.request is payload` and
  `.prompt_tokens == 220000`. The other two raises are unchanged.
- *changed* `tests/integration/runtime/test_same_second_continuation.py::test_budget_step_zero_ends_the_continuation_before_any_condensation`
  (`:890-907`):
  - `fitted.request["messages"]` and `condensed.request["messages"]`;
  - plus `fitted.prompt_tokens == prompt_capacity.prompt_token_count(fitted.request)`;
  - and the same for `condensed`.
- *changed* `tests/integration/runtime/test_query_prompt_contract.py::test_actual_usage_is_checked_against_the_sent_prompt_estimate`
  (`:70-87`), renamed `test_actual_usage_is_checked_against_the_count_taken_before_sending`:
  - `compare_usage(100, response)` for the four non-integer usages logs nothing, and for `{"usage": {"prompt_tokens":
    101}}` logs `estimated=100 actual=101 difference=1`.
  - `token_counter` (monkeypatched to record calls) is never called by `compare_usage`.
- `tests/integration/runtime/test_life_agent_protocol.py::test_usage_check_reuses_the_count_taken_before_sending`:
  - Use the four-turn decision of `test_budgeted_memory_search_preserves_the_action_turn` (`:996`; three
    `memory_recent` turns, then the world request) under the autouse `_fixed_context_window`.
  - The scripted responses must report usage, or `compare_usage` returns before logging: wrap each `_ModelResponse` in
    a small subclass whose `model_dump` adds `"usage": {"prompt_tokens": 1000, "completion_tokens": 1,
    "total_tokens": 1001}`.
  - Monkeypatch `prompt_capacity.token_counter` to return 1000 and record calls (17,000 with the 16,000 reserve, far
    below 85% of the fixture's 524,288-token window, so nothing is condensed).
  - Run the decision with `caplog.at_level("INFO")`.
  - Assert the number of `token_counter` calls equals `len(dispatcher.requests)` (4): one per model call, none after
    responses. Today's code gives 8.
  - Assert `estimated=1000 actual=1000 difference=0` appears exactly 4 times in the log.
- `tests/integration/runtime/test_life_agent_protocol.py::test_usage_check_refuses_a_count_taken_for_another_request`:
  - `agent = _prepared()`; `agent._life_fitted = FittedRequest({"messages": []}, 5)`.
  - `await agent._record_model_evidence(ModelCallEvidence(None, "call", "response", 1, {"messages": []},
    response={"usage": {"prompt_tokens": 5}}))`, with a distinct request object and context `None`, so AS2's writer
    returns early.
  - The call raises `RuntimeError` matching `does not belong to the recorded request`, and caplog has the ERROR line.
  - A second, fresh `_prepared()` agent that never fitted a request (`_life_fitted is None`) raises the same way.

### Model usage per call class

**`tests/unit/footprints/test_quality.py`:**
- *changed* `test_model_usage_counts_plan_day_and_lists_it_by_purpose` (`:218-234`) is replaced by
  `test_model_usage_lists_every_call_class_from_the_recorded_request_shape`:
  - Request events carry real shapes:
    - fresh: system and user messages, a `start_main_activity` tool, `extra_body.thinking.type == "enabled"`;
    - history: the same with assistant and tool messages;
    - retry: a `start_main_activity` tool, `extra_body == {"thinking": {"type": "disabled"}}`, three messages;
    - consolidation: no tools;
    - reflection: a single `submit_reflection` tool;
    - plan_day: `purpose: "plan_day"` on the event data and a `submit_day_plan` tool;
    - plus `{"call_id": "odd"}` with no request.
  - Each has a response with `_usage()`. `recorded_at` is peak (`2026-09-15T07:00:00+00:00`, a Tuesday) for fresh and
    plan_day, and off-peak (`2026-09-15T12:00:00+00:00`) for the rest.
  - Assert:
    - `[row["call_class"] for row in usage["by_call_class"]] == [*CALL_CLASSES, "unrecognized"]`;
    - every row's `request_events == provider_responses == 1`;
    - `priced_tokens == {"cache_hit_input_tokens": 40, "cache_miss_input_tokens": 60, "output_tokens": 20}`;
    - `peak_responses` 1 or 0 and `peak_share` 1.0 or 0.0 as placed;
    - `known_estimated_cost_usd` equals `_estimate_cost`'s value;
    - Σ `request_events` == `usage["request_events"]` and Σ `provider_responses` == `usage["provider_responses"]`;
    - `"by_purpose" not in usage`.
- `test_call_class_comes_from_the_request_event_not_the_response`:
  - The fresh request event's response carries `"request": null`, and another response carries a reflection-shaped
    request copy for a fresh-shaped request event.
  - Both are classified by their request event. Without any odd event there is no `unrecognized` row.
- `test_a_start_tool_request_without_the_retry_switch_is_unrecognized`: a request event with a `start_main_activity`
  tool and no `extra_body`, and another with `extra_body == {"thinking": {"type": "enabled", "budget": 1}}`. Both are
  `unrecognized` (`request_events == 2` on that row) and `truncation_retry` has `request_events == 0`.
- `test_call_classes_without_priced_responses_stay_unknown`:
  - A consolidation response from `another-model`, and a reflection response without cache fields.
  - Their rows keep `token_usage` but have `estimated_responses == 0`, `priced_tokens is None`, `peak_share is None`
    and `known_estimated_cost_usd is None`.
  - Classes with no calls are listed with zeros and `null`s.
- *changed* `test_report_sections_are_the_documented_grouping_without_a_language_count` (`:635-650`):
  `report["schema_version"] == 9`. The docstring's 「报告结构为 8」 becomes 9. The lineage chapter adds its own section to
  `documented`; this chapter does not touch that list.

**`tests/system/lifecycle/test_sleep_and_reflection_run.py::test_run_with_naps_exports_clean_memory_sources_and_a_resume_at_wake_up_reflects_once`**
(*changed*, `:136-200`). After the existing `quality` read, add:
- `rows = {row["call_class"]: row for row in quality["model_usage"]["by_call_class"]}`;
- `"unrecognized" not in rows`;
- `rows["reflection"]["request_events"] == len(run.model.of("reflection"))`;
- `rows["consolidation"]["request_events"] == len(run.model.of("consolidation"))`;
- the sum of the three decision classes == `len(run.model.of("action"))`;
- `rows["plan_day"]["request_events"]` equals the number of exported `model_request` events whose `data.purpose` is
  `plan_day`, and that number is positive: the engine's midnight plans run through the test fixture's plan client
  (3 in the scratch copy below).

This ties the classifier to the real request shapes LifeAgent sends through `DayModel`
(`tests/integration/reflection_fixtures.py:54-88`). A scratch copy of this scenario with the chapter's rules applied to
today's export passed: fresh 7 and history 5 against 12 scripted action requests, consolidation 2 = 2, reflection
2 = 2, plan_day 3, nothing unrecognized (`.work/run30/scratch/spec-review-06-host-costs-and-model-cost/
call_class_probe.json`).

`behavior_metrics` tests are not edited. Their passing unchanged is the check that populations did not move.

**`tests/unit/engineering/test_import_graph.py`** (*changed*):
`test_moved_declarations_have_no_same_name_binding_at_old_modules` gains the row
`("worldmock.evaluation.behavior_metrics", {"_request_tool_names"})`. `PLAN_DAY_PURPOSE` gets no row: `day_planning`
keeps the name bound through its import.

### Byte-identity proof for this chapter

This is offline acceptance, run once before merge; the outputs go to `.work/`. "Base" is the integration branch
just before this chapter's commits (main `0bd2de65` when the chapter is tried alone). "Head" is base plus this
chapter, so other chapters' format changes cancel out.

- **Observation bytes on the 30-day run.**
  - Re-run the harness of `.work/run30/scratch/spec-06-host-costs-and-model-cost/proof_observation_bytes.py` twice, with
    `PYTHONPATH` pointing at the base `src` and at the head `src`, with its `patches()` step removed: the head runs the
    implemented code. The only wrapper kept is the counter around `tool_layer.availability`, which calls through
    unchanged.
  - Each run writes the sha256 of `canonical_json(_observation_data(agent))` per checkpoint for the five checkpoints
    listed in `proof_observation_bytes.json` (seq 35670, 38602, 41523, 44057, 45241).
  - The digests must be equal. The head's availability-evaluation counts must be 76–79.
- **Complete-day masked comparison.**
  - Run `tests/life/day_baseline_runtime.record_complete_day` for `activity` and `callback` on base and head.
  - Every entry of `summary.artifacts` must be equal except `quality.json` and `manifest.json`. That includes
    `events.jsonl`, which carries every observation, prompt and model evidence, and `activities.jsonl`.
  - `checkpoints_sha256` and `run_assets` must be equal.
  - The two `quality.json` files, parsed, must be equal after deleting `schema_version`, `model_usage.by_purpose` and
    `model_usage.by_call_class`.
  - No information lost, checked on these real exports: head's `by_call_class` `plan_day` row has the
    `request_events`, `provider_responses` and `token_usage` of base's `by_purpose` `plan_day` entry, and the other rows
    summed field by field (`request_events`, `provider_responses`, the three `token_usage` counts, `known_tokens`)
    equal base's `decision` entry, or are all zero when base has none.
  - The baselines `tests/fixtures/dayrun-activity-baseline.json` and `dayrun-callback-baseline.json` are re-frozen once
    at the end of the integration branch, as the proposal's "Versions" says.
- **Recorded-response run** (proof-tools chapter). The 30-day backport gate must show zero divergence in this
  chapter's area. Every request is byte-identical by construction.

### Source mutations (`tests/dataset_acceptance.py`, append to `MUTATIONS`)

```python
    SourceMutation("open_activities_omit_pending_conclusions", "worldmock.life.activity_control::open_activities",
        "if item.agent_id == agent_id and concludable(item))",
        "if item.agent_id == agent_id and item.status in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED})",
        ("tests/unit/life/test_open_activities.py::test_open_activities_are_the_concludable_ones_in_world_order",
         "tests/integration/runtime/test_query_prompt_contract.py"
         "::test_real_full_slots_and_pending_conclusion_use_the_same_tool_refusal")),
    SourceMutation("asleep_check_reads_no_activity", "worldmock.life.admission::_sleep_refusal",
        "for activity in context.open_activities)", "for activity in ())",
        ("tests/unit/test_environment_engine_admission.py::test_sleep_blocks_second_slot_start_until_its_own_end",)),
    SourceMutation("tool_layer_admission_without_open_activities", "worldmock.life.tool_layer::_admission_context",
        "context.open_activities, places,", "(), places,",
        ("tests/integration/runtime/test_query_prompt_contract.py"
         "::test_real_full_slots_and_pending_conclusion_use_the_same_tool_refusal",)),
    SourceMutation("tool_row_ignores_the_binding_note", "worldmock.life.tool_layer::_action_row",
        '"available": action["note"] is None,', '"available": True,',
        ("tests/integration/runtime/test_query_prompt_contract.py"
         "::test_real_full_slots_and_pending_conclusion_use_the_same_tool_refusal",
         "tests/unit/life/test_shared_observation_contract.py"
         "::test_tool_layer_rows_take_availability_from_the_private_binding_note")),
    SourceMutation("unconsolidated_view_keeps_the_cursor_episode",
        "worldmock.agents.life_memory_store::WorldMockMemoryStore.unconsolidated_view",
        "return records[index + 1:]", "return records[index:]",
        ("tests/integration/as2/test_worldmock_memory_boundaries.py"
         "::test_cache_reads_equal_the_copying_reads_at_every_cursor",)),
    SourceMutation("step_end_copies_every_memory_again",
        "worldmock.agents.life_memory::WorldMockMemoryRuntime.after_step",
        "known = store.episode_ids()", 'known = {str(item["id"]) for item in store.iter_episodes()}',
        ("tests/integration/as2/test_worldmock_life_hooks.py"
         "::test_step_end_below_the_threshold_reads_memory_without_copying_it",)),
    SourceMutation("fitted_count_includes_the_output_reserve", "worldmock.life.prompt_capacity::fit_request",
        "return FittedRequest(request, measured - reserve)\n    if budget",
        "return FittedRequest(request, measured)\n    if budget",
        ("tests/unit/life/test_native_environment_contract.py"
         "::test_capacity_uses_verified_actual_context_and_enforces_verified_output_limit",)),
    SourceMutation("usage_estimate_of_another_request_accepted", "custom.agents.life_agent::LifeAgent._usage_estimate",
        "if fitted is None or fitted.request is not request:", "if fitted is None:",
        ("tests/integration/runtime/test_life_agent_protocol.py"
         "::test_usage_check_refuses_a_count_taken_for_another_request",)),
    SourceMutation("history_turn_counted_as_fresh", "worldmock.footprints.quality::_call_class",
        'if _message_roles(request) == ["system", "user"] else', "if len(_message_roles(request)) >= 2 else",
        ("tests/unit/footprints/test_quality.py"
         "::test_model_usage_lists_every_call_class_from_the_recorded_request_shape",)),
    SourceMutation("truncation_retry_not_recognized", "worldmock.footprints.quality::_call_class",
        'if START_TOOLS[0] in names and request.get("extra_body") == RETRY_THINKING:', "if False:",
        ("tests/unit/footprints/test_quality.py"
         "::test_model_usage_lists_every_call_class_from_the_recorded_request_shape",)),
    SourceMutation("truncation_retry_matched_without_its_switch", "worldmock.footprints.quality::_call_class",
        'if START_TOOLS[0] in names and request.get("extra_body") == RETRY_THINKING:',
        "if START_TOOLS[0] in names:",
        ("tests/unit/footprints/test_quality.py"
         "::test_a_start_tool_request_without_the_retry_switch_is_unrecognized",)),
    SourceMutation("report_status_reads_the_open_activities_of_the_committed_world",
        "worldmock.life.session_actions::ActionHandlers._admission_context",
        "activity_control.open_activities(state, agent_id)",
        "activity_control.open_activities(self.context.state, agent_id)",
        ("tests/integration/start_interfaces/test_start_group.py"
         "::test_resuming_two_paused_activities_checks_the_second_against_the_first_resumed",)),
    SourceMutation("peak_share_ignores_the_price_band", "worldmock.footprints.quality::_call_class_usage",
        'peak = sum(item["price_band"] == "peak" for item in priced)', "peak = 0",
        ("tests/unit/footprints/test_quality.py"
         "::test_model_usage_lists_every_call_class_from_the_recorded_request_shape",)),
```

Each original occurs exactly once in its symbol as specified above (`known = store.episode_ids()` occurs once in
`after_step`; the other occurrence is in `wake_reflection`, a different symbol). `fit_request`'s first return is the
only one followed by `\n    if budget`: the second return is indented twelve spaces and the third sixteen. The mutation
runner reads sources with universal newlines, so the `\n` also matches a CRLF checkout. Existing mutations whose
symbols this chapter touches keep their snippets:
- `sleep_refusal_offers_pausing_whatever_holds_slot_two` (`_sleep_refusal`);
- `locked_unit_listed_in_the_tool_layer` (`_binding_target`, unchanged);
- the `pausable` mutations.

## Files touched

Production:
- `src/worldmock/life/activity_control.py`
- `src/worldmock/life/admission.py`
- `src/worldmock/life/tool_layer.py`
- `src/worldmock/life/observe.py`
- `src/worldmock/life/start_group.py`
- `src/worldmock/life/session_actions.py`
- `src/worldmock/life/decision_coordinator.py`
- `src/worldmock/agents/life_memory_store.py`
- `src/worldmock/agents/life_memory.py`
- `src/worldmock/life/prompt_capacity.py`
- `custom/agents/life_agent.py`
- `src/worldmock/life/decision_start.py`
- `src/worldmock/life/day_planning.py`
- `src/worldmock/evaluation/behavior_metrics.py`
- `src/worldmock/footprints/quality.py`

Docs:
- `docs/design/05-recording-engine.md`
- `docs/manual/runtime.md`

Tests:
- `tests/unit/life/test_open_activities.py` (new)
- `tests/integration/start_interfaces/test_start_group.py`
- `tests/unit/engineering/test_import_graph.py`
- `tests/unit/life/test_shared_observation_contract.py`
- `tests/unit/test_environment_engine_admission.py`
- `tests/unit/life/test_environment_commerce.py`
- `tests/unit/life/test_pause_limit.py`
- `tests/unit/life/test_series_pause_wording.py`
- `tests/unit/start_interfaces/test_grocery_units.py`
- `tests/unit/life/test_activity_boundary_contract.py`
- `tests/evaluation/conftest.py`
- `tests/integration/evaluation/test_workload_metrics.py`
- `tests/integration/runtime/test_current_runtime_contract.py`
- `tests/integration/runtime/test_same_second_continuation.py`
- `tests/integration/runtime/test_query_prompt_contract.py`
- `tests/unit/life/test_native_environment_contract.py`
- `tests/integration/runtime/test_life_agent_protocol.py`
- `tests/integration/as2/test_worldmock_memory_boundaries.py`
- `tests/integration/as2/test_worldmock_life_hooks.py`
- `tests/unit/footprints/test_quality.py`
- `tests/system/lifecycle/test_sleep_and_reflection_run.py`
- `tests/dataset_acceptance.py`

Shared with other chapters (same file, different lines unless noted; the integrator merges them in the order of
`SPEC-long-run-cost.md`):
- `src/worldmock/footprints/quality.py`: `build_quality_report` `:59`/`:73` with the network-and-restarts chapter (one
  8 → 9 bump, plus its `lineage` section); `_model_quality` is read but not changed by the whole-history chapter's
  post-run view.
- `src/worldmock/life/day_planning.py`: the network-and-restarts chapter changes `response_draft` and `failure_draft`;
  this chapter only moves `PLAN_DAY_PURPOSE` out.
- `src/worldmock/life/activity_control.py`, `admission.py`, `start_group.py`, `session_actions.py`: the
  state-and-checkpoints chapter edits other functions. Its list of dict-order readers names `admission.py:108`
  (`_occupant`); after this chapter that order is read by `activity_control.open_activities` instead, with the same
  order.
- `custom/agents/life_agent.py`, `src/worldmock/life/prompt_capacity.py`: the recorded-evidence and network chapters
  edit other lines.
- `docs/design/05-recording-engine.md`「完整导出认证」: the quality-structure sentence is edited by both this chapter and
  the network-and-restarts chapter; `docs/manual/runtime.md`: the network chapter edits the command block and
  「从检查点创建分支」, this chapter adds a section between two others.
- `tests/unit/footprints/test_quality.py::test_report_sections_are_the_documented_grouping_without_a_language_count`
  and `tests/dataset_acceptance.py` are edited by several chapters.

Fixtures, re-frozen once at the end of the integration branch and shared with every chapter that changes export bytes:
- `tests/fixtures/dayrun-activity-baseline.json`
- `tests/fixtures/dayrun-callback-baseline.json`

Scratch, not tracked:
- `.work/run30/scratch/spec-06-host-costs-and-model-cost/proof_observation_bytes.py`
- `.work/run30/scratch/spec-06-host-costs-and-model-cost/proof_memory_reads.py`
- `.work/run30/scratch/spec-06-host-costs-and-model-cost/call_classes.py`
- the outputs of the three scripts above;
- the review's probes `.work/run30/scratch/spec-review-06-host-costs-and-model-cost/test_call_class_probe.py`
  (call classes of the sleep-and-reflection scenario, `call_class_probe.json`) and `test_resume_two_probe.py` (the
  two-item `report_status` scenario on main's code).

## Not in this chapter

| Item | Where it lives |
| --- | --- |
| Bounded session opening-result read (cold start after resume, `session_bounds._opening_result_seq`) | Whole-history paths chapter |
| Closing LiteLLM's cached async clients ("Unclosed client session" lines); one connection pool per worker | Network and restarts chapter (closing); the pool is in the proposal's "Not proposed" |
| Ops log gains commit time and plan_day call time; client-side call timing | Network and restarts chapter (ops log only); no event field |
| Measured time per call class (`elapsed_ms` in model evidence) | Not specified: the proposal's cost-by-class item says "No event or request changes" |
| A recorded `purpose` on memory requests | Not specified: the class is derived from the request shape; `behavior_metrics` populations stay |
| Whole-call deadline on a stalled model call | Network and restarts chapter |
| Structural sharing of `processed_actions` and `activities`; activity order kept through checkpoints | Store-and-state chapters. `open_activities` follows whatever `state.activities` iteration order they define; if they change the container type, they update this helper with every other `.values()` site |
| Archiving concluded activities out of the hot state | "Not proposed" (recall and 第二次 numbering read every activity) |
| `request: null` on model responses | Recorded-evidence chapter. This chapter already classifies from request events only |
| `quality.json` lineage section | Network and restarts chapter (shares the 8 → 9 structure bump) |
| User-layer order for prompt caching | "Awaiting the owner" |
| An engine gate that holds model calls in peak hours | "Awaiting the owner" |
| A Chinese-holiday calendar in `quality._peak_band` | Not proposed (the review judged it not worth it); the manual and design text note the gap |
| Other per-row scans of all activities: `admission._travel_refusal` (`:172-174`), `location_activities.held_refusal` (`:192`), `purchase_offers.present_location` → `models.active_activities` (`purchase_offers.py:48`), `_report_refusal`'s conflict list (`activity_control.py:314`) | Not in the proposal. Unmeasured and left as they are; the same helper can serve them later with its own byte-identity proof |
| Incremental `persona_views` fold; built-in module registry builds at process start | Research notes M2 and M3, not in the proposal |
| The deep copy inside AS2 `mark_subjective_consolidated` (`recent(limit=1)`, once per consolidation) and `_reflection_episodes`' copy (once a day) | Not changed: rare, and the first needs an AS2 patch |
| `truncations.purpose_counts` still groups by purpose with a `decision` bucket | Not changed: outside the proposal's cost-by-class item |
