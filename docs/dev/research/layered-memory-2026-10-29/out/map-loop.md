# Decision loop and its units (session, round, turn): current system on `refactor/simplify-issue-15` at f67c25e5

**Short answer:** the owner's three units mostly exist in code, but under other names, with a turn budget of 16 rather than 32, and with a wider set of tools that end a round. In practice a round uses one model call. The memory tools are almost never used, and the model's reasoning is kept only between decisions made in the same simulated second.

`AS2/` below means `vendor/agentsociety2/packages/agentsociety2/agentsociety2/`. That folder is gitignored (`.gitignore:18`) and is rebuilt from `patches/agentsociety2/SERIES`.

## 0. Terminology mapping

| Owner | Code | Difference |
|---|---|---|
| session | `SessionState`, either `stay` or `transit` | Time on the road is also a session. A stay is not split at midnight or at sleep. |
| round | a **decision** (`decision_id`): one observation and one world request | In code, "round" means the AS2 *decision round* (`round_id = run:round:<epoch>:<version>`, `AS2/society/society.py:276`). With one agent it is 1:1 with a decision. `same_moment_rounds` counts decisions within one second (`state/world/agent.py:121`). |
| turn | a model turn (`…:model:NNNN:attempt:1`, `AS2/agent/base/agent_runtime.py:965-974`) | The budget is **16**, not 32. |

There is also a name collision: the wake reason `session_open` means "a sleep just completed" (`simulation_engine/scheduling/clock/rhythm.py:122,138`). It has nothing to do with a `SessionState` opening.

## 1. Session

- **What it is.** `SessionState` is "one stay at a place or one stretch of road; the system starts and ends it, the persona never does" (`src/worldmock/state/world/agent.py:57-95`).
  - A stay carries `location_id`; a transit carries `origin_id` and `destination_id` (`agent.py:86-90`).
  - `opened_by_action_id` sets the lower event bound for the progress layer (`agent.py:64-66,79`).
- **When sessions open and close** (`simulation_engine/activities/lifecycle/sessions.py`):
  - At run start: `open_stay` (`world/world_setup.py:48`).
  - On an accepted departure: `begin_transit` ends the stay and opens a transit (`sessions.py:72-86`, called at `actions/start_group.py:212`).
  - On arrival: `end_transit` → `open_stay` (`sessions.py:89-95`, `clock/time_advance.py:264-269`).
  - On ending a trip mid-way: `actions/dispatcher.py:129`.
  - At run end: `end_all` (`sessions.py:129-134`, `time_advance.py:130`).
  - No call site splits a session at midnight or at sleep.
- **Evidence from the variety run** (21 sessions: 11 stays, 10 transits). Home stays run across the night:
  - `session-1-0013` lasts from 10-10 18:15 to 10-11 14:30 and holds 23 activities.
  - At the 10-11 09:00 decision, the 进度层 `session_started_at` is `2026-10-10T18:15:20` with 49 events, and 记忆层 `earlier_today` has 0 entries. So yesterday evening reaches "today" through the progress layer.
  - The progress layer covers the current session only (`observation/layers/progress_layer.py:62-87`). A 记忆层 today entry is one *ended* session (`memory_layer.py:216-217`).

## 2. Round (decision): what starts and ends it

- **Driver.** `Society.step` checks `scheduling_state`.
  - If an agent is ready it runs one decision round: `begin_decision_round`, then the agent step, then `commit_decision_round`.
  - Otherwise it advances the clock to the next boundary.
  - It writes a checkpoint after every step (`AS2/society/society.py:224-262`).
  - An agent is ready when `next_decision_at <= current_time` (`time_advance.py:94-96,400-407`). Time cannot advance while an agent is ready (`time_advance.py:98-104`).
- **Agent step.** Optional bedtime reflection, then exactly one `observe`, then the ReAct loop, then the step-tail memory update (`AS2/agent/person.py:527-555`; `custom/agents/life_agent.py:311-322`).
  - The step tail runs *before* the world commit, because the agent step sits between begin and commit (`society.py:252-257`).
  - `decision_started` is written lazily, alongside the first action request (`decisions/decision_start.py:11-61`).
- **World requests that end the decision.** The six tools in `REQUEST_MODELS` other than `recall_task_summary`: `start_main_activity`, `start_side_activity`, `report_status`, `pause_activity`, `conclude_activity`, `revise_day_plan` (`state/interaction/requests.py:568-575`; `life_agent.py:182-183`).
  - Registering one ends the turn loop (`_run_react_turn`, `life_agent.py:563-583`). No tool message is appended for it (`life_agent.py:647-653`), and dispatching anything afterwards raises (`life_agent.py:641-645`).
  - Allowed batches are: only read queries; exactly one world request; or one of each start tool. Any other mix is rejected as a whole (`life_agent.py:883-895`).
  - The system prompt states the same rule (`agents/prompt/life_prompt.py:29-33`).
- **Read-only tools that do not end it.**
  - `recall_task_summary` goes to `LifeEnv.read_query` against the frozen observation and writes no events (`custom/envs/life_env.py:156-180`). The coordinator rejects it as a world action (`decision_coordinator.py:272-274`).
  - `memory_recent`, `memory_search` and `memory_time_range` are answered by the memory runtime and return subjective episodes (`life_agent.py:665-683`).
- **Same-moment re-decisions.** After each settled request, `schedule_after_round` keeps the same second when any of these hold (`decisions/same_moment.py:124-135`; called from `decision_coordinator.py:463-505`):
  - no activity is running;
  - an activity is waiting to be concluded;
  - the request was rejected and the open limit is not yet reached.

  Otherwise the next decision follows the rhythm (`rhythm.py:164-183`): planned end, a felt-band crossing, arrivals, the reconsideration interval (5400 s), progress reports (1800 s), reminders and shift edges.
  - Same-second wake reasons are `no_active_activity` or `rejection_feedback` (`same_moment.py:92-102`).
  - The cap is 3 open plus 3 restricted decisions per second (`same_moment.py:38-41`). Going past it means `decision_budget_exhausted`, and the run fails (`same_moment.py:139-145`; `decision_coordinator.py:178-187`). The per-second counter resets when the clock advances (`same_moment.py:265-270`).
- **Boundary wake reasons.** Eleven triggers (`state/planning/schedule.py:43-54`), evaluated in `evaluate_wake_reasons` (`schedule.py:235-260`). While asleep, only `planned_end` and `session_open` can wake the persona. A finished activity sets `next_decision_at` to the boundary (`time_advance.py:288-290`).

## 3. Turns

- **Budget.** `max_react_turns` defaults to 16, strict integer 1–64 (`configuration/inputs/models.py:223`); the variety run's `input/config.toml` sets it to 16. Restore requires the same value (`runtime/lifecycle/runner.py:418-421`).
- **Loop.** `for turn in 1..max` (`AS2/agent/base/agent_runtime.py:536-540`). The message list is built **once per decision** and turns only append to it (`_react_message_history`, `agent_runtime.py:826-834`).
  - `LifeAgent.build_react_messages` returns the system layer plus a user message made from the six layers, which were frozen in `prepare_react_loop` (`life_agent.py:453-492`).
  - The observation and prompt are therefore built per round, not per turn. The one exception is send-time progress-layer condensation by `fit_request` when the request is over capacity (`life_agent.py:381-385`; `prompt/prompt_capacity.py:80-102`).
- **Shared history within a decision.**
  - Accepted assistant messages keep their `reasoning_content` (`agent_runtime.py:861-877`).
  - Memory-query results are added as tool messages (`life_agent.py:662-663`).
- **Protocol-feedback retry.**
  - If thinking is on and the response contained tool calls, AS2 pairs the assistant message with a feedback tool message.
  - Otherwise the feedback goes in as a user message that replaces any identical earlier feedback (`life_agent.py:276-288`; `AS2/…/agent_runtime.py:756-781`; `agents/protocol/life_truncation.py:69-75`).
- **Truncation retry.** A response with `finish_reason=length`, empty content and no tool calls gets `OUTPUT_TRUNCATED` (`life_truncation.py:18-26`; `life_agent.py:257-258`). The next request turns thinking off and uses `tool_choice=required` (`life_agent.py:374-380`; `life_truncation.py:57-65`).
- **Last turn.** On the last turn, and on a truncation retry, the memory tools are removed (`life_agent.py:397-404`).
- **Exhaustion.** With no turns left, `_remaining_turns` raises (`life_agent.py:388-395`), or AS2 raises "ReAct decision budget exhausted" (`agent_runtime.py:540`). The round is then aborted: actions are marked `FAILED/DECISION_ROUND_ABORTED` and `run_failed` is written (`decision_coordinator.py:216-251`; `society.py:262`). A run with exhausted turns does not continue.
- **Call classes** (`recording_engine/reports/quality.py:794-806`):
  - `fresh_decision_turn`: the request has exactly the `[system, user]` messages.
  - `turn_with_history`: any action request with more messages. This counts both a decision's later turns and first turns that carry a same-second continuation chain.

## 4. Where reasoning is kept or dropped

- The system rule says: "思考只在同一时刻连续决策之间保留，时间推进后丢弃" (`life_prompt.py:96-97`).
- **Same-second continuation chain** (`simulation_engine/decisions/continuation.py:1-11,44-71`). It carries the previous decision's last request messages, its final assistant message with reasoning, the world-request results, and same-second events. The host injects it (`agents/protocol/life_continuation.py:66-77`; `life_agent.py:488-492`).
- **The chain breaks** when any of these hold:
  - a different second (`continuation.py:74-82`);
  - a session change (`continuation.py:85-88`);
  - a counterfactual intervention;
  - missing reasoning (`life_continuation.py:80-90`);
  - the over-capacity check (`prompt_capacity.py:93-97`);
  - the `same_second_continuation` switch is off.
- **The visible reply text survives longer.** The model's visible reply text (not its reasoning) becomes `action_requested.data.self_note` (`decision_coordinator.py:552-563`). It shows in the progress layer for the rest of the session (`progress_layer.py:137-138`) and then in 记忆层 `self_notes`.

## 5. Model calls outside the decision loop

| Call | When it fires | Variety run |
|---|---|---|
| Midnight `plan_day` | After the midnight advance, or after the startup checkpoint; outside LifeAgent, with up to 3 extra validation attempts (`life_env.py:205-215,235-243`; `runner.py:619-662`) | 9 calls over 5 dates (4 retries, all for duplicate `intent_kind`) |
| Bedtime reflection, plus a consolidation first | Before the observation, on the first wake of a local day (`evidence/memory.py:13-34`; `life_agent.py:311-337`; `agents/memory/life_memory.py:127-143`) | 4 reflection calls (~07:30–07:58, 10-09 to 10-12) |
| Threshold consolidation | Step tail, when ≥10 pending episodes or importance ≥3 (`life_memory.py:30-31,94-125,202-212`) | 31 total: 27 at step tails, every one after a `conclude_activity` decision, plus 4 inside wake reflection |
| Read-only Q&A | AS2 readonly ReAct with memory tools plus `finish` (`life_agent.py:401-403,455-457`) | 0 |

Reflection and step-tail memory calls do not use up the turn budget (`life_agent.py:339-346`; `docs/design/06-agent.md:345`).

## 6. AS2 constraints on changing this loop

- **Forbidden** (`docs/dev/SPEC-as2-integration.md:380-384`): runtime monkey patching, copying the AS2 scheduler, keeping two persona decision loops, rewriting the agent framework.
- **Allowed** (`SPEC:386-390`): explicit hooks, small helper functions, new interface methods, removing unsuitable automatic paths.
- **Registered LifeAgent overrides** (`SPEC:348-361`): `_run_react_turn`, continuation injection after `prepare_react_loop`, `_run_reflection`, `_protocol_feedback_content`, `_parse_react_responses`, `_validate_react_tool_names`, `_append_react_protocol_feedback`, `step`, `_execute_react_tool`.
- **AS2 source.** It is a baseline archive plus five ordered patches (`patches/agentsociety2/SERIES`). The loop lives in `failure-propagation.patch` (`agent_runtime.py`, `react.py`) and the round in `decision-round.patch` (`society.py`). At startup the imported package is hashed and must match SERIES, or the run refuses to start (`SPEC:346`).
- **Fingerprints.** The prompt and tool-schema fingerprints are frozen into the stage identity (`runtime/framework/setup.py:83-89`). Any change to the system layer or the tool set changes the run identity.

## 7. Measurements from the variety run (baseline in brackets)

- **Decisions per local day:** 81 / 84 / 83 / 69 / 89, 406 in total [84 / 80 / 79 / 85 / 81, 409 in total].
- **Turns per decision:** 402 × 1 and 4 × 2; mean 1.01, max 2 of 16 [396 × 1, 12 × 2, 1 × 3].
  - The four 2-turn decisions: 3 were `ARGUMENTS_INVALID` feedback on `conclude_activity`; 1 called `memory_search` twice, then `start_side_activity`.
  - Memory-tool decisions: **1 of 406** [0 of 409].
- **Call classes** (`export/quality.json`): `fresh_decision_turn` 305 and `turn_with_history` 105. The 105 are 101 decisions whose first turn carries a continuation chain plus 4 second turns. `truncation_retry` 0.
- **Ending tools:**

| Tool | Variety | Baseline |
|---|---|---|
| `conclude_activity` | 137 | 137 |
| `report_status` | 136 | 145 |
| `start_side_activity` | 62 | 49 |
| `start_main_activity` | 51 | 56 |
| both start tools together | 20 | 20 |
| `pause_activity` | 0 | 1 |
| `revise_day_plan` | 0 | 1 |

  `action_requested` = 426 (406 decisions + 20 start pairs); 420 accepted, 6 rejected.
- **Same-moment chains:** 303 seconds had decisions: 203 had 1, 97 had 2, 3 had 3.
- **Wake reasons** (on `decision_started`): `no_active_activity` 164, `planned_end` 105, `progress_report_due` 87, `app_push` 67, `felt_band_change` 44, `delivery_arrival` 19, `duty_boundary` 16, `session_open` 10, `reminder_due` 9, `reconsideration_interval` 5, `rejection_feedback` 5.
- **Silent boundaries:** 631 of 933 body-settlement boundaries offered no decision.

## Where the docs and code disagree (I followed the code)

- `SPEC-as2-integration.md:29` gives the patch source digest as `bb655cb3…`, but `SERIES:31` has `0a7b9b06…`.
- The spec lists a `query_world` tool (`SPEC:408,443,693`), but nothing in `src/` or `custom/` defines it.
- `CLAUDE.md` is stale on the package layout, as noted in the task.

## Open questions / uncertainties

- Should the owner's "round" equal the code's decision? That would include `report_status` (about one third of decisions, mostly from progress-report and app-push wakes), `revise_day_plan`, and same-second re-decisions. The alternative is only activity start/pause/conclude.
- 103 same-second follow-up decisions, but only 101 carried a chain. I did not check why the other 2 broke; a session switch is likely.
- The paths for turn exhaustion, truncation retry and read-only Q&A never ran in these two runs. They are traced from code only.
- I did not check whether a midnight boundary alone ever offers a decision, or how `reminders.defers_rhythm` affects which wake reasons fire at 00:00.
- The step-tail consolidation runs before the world commit. I have not checked what that does to the provenance of consolidated memories.

The helper scripts I used are in `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/`: `loop_stats.py`, `two_turn.py`, `overnight.py`. They are scratch only; no repository files were changed.