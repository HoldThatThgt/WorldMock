# Verified fact brief: current memory / decision design (HEAD f67c25e5)

Every fact below was mapped by one analyst and re-checked by two independent verifiers, one reading code and one reading run data. Their corrections are already applied. Numbers are for the variety run (V); baseline (B) figures are given only where they differ. Both runs cover 5 simulated days, 2026-10-08 → 10-13.

The full maps are in `scratchpad/memgap/out/map-*.md`, and the gap table plus verifier output are in `gaps.json` and `verify.json` in the same folder (scratchpad = `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad`). Read them for details and citations.

## Units

- **F1 Session.** A `SessionState` is either a `stay` or a `transit` (`src/worldmock/state/world/agent.py:57-95`).
  - The system opens and closes it: at run start, on departure (`begin_transit`), on arrival (`end_transit` then `open_stay`), and at run end.
  - It is never split at midnight or at sleep, so a home stay runs from evening to the next morning. Example: 10-10 18:15 → 10-11 14:30, holding 23 activities.
  - A travel's conclude notes are filed under the session where the conclude request happened, which is the arrival stay.
  - There are 21 sessions: 11 stays and 10 transits.
- **F2 Round.** The owner's "round" is the code's *decision*, identified by `decision_id`.
  - A decision is one frozen observation, then the ReAct loop, then a step tail.
  - It ends at the first registered world request, even when that request is rejected. The world requests are:
    - `start_main_activity` and/or `start_side_activity` (a pair is allowed);
    - `report_status`;
    - `pause_activity`;
    - `conclude_activity`;
    - `revise_day_plan`.
  - Read-only queries do not end it: `recall_task_summary` and `memory_recent`, `memory_search`, `memory_time_range`.
  - The word "round" is already taken in code: AS2 `round_id` and the counter `same_moment_rounds`.
  - Volume: about 81 decisions per simulated day, or 5.5 per awake hour.
  - Tools that ended a decision:

    | tool | V | B |
    |---|---|---|
    | conclude | 137 | 137 |
    | report_status | 136 | 145 |
    | start_side | 82 | 69 |
    | start_main | 71 | 76 |
    | pause | 0 | 1 |
    | revise_day_plan | 0 | 1 |

    Of the V starts, 20 were start pairs.
- **F3 Turn.** One turn is one model call, with id `…:model:NNNN`.
  - The budget is `max_react_turns`, default **16** (allowed 1–64; `configuration/inputs/models.py:223`; package `config.toml:30`). It is frozen into model identity and must be equal on resume.
  - Observed: 402 of 406 decisions used 1 turn; the maximum was 2 in V and 3 in B. Running out of turns raises and fails the run.
  - Messages are built once per decision; later turns only append.
  - Reflection and consolidation calls have their own id families and do not use the turn budget.
- **F4 Same-second continuation.** 103 of 406 decisions replay the previous decision's messages and reasoning, in a same-second chain.
  - `life_continuation.py:8` assumes 记忆层 does not change within a chain.
  - These are the "turn_with_history" calls. Their cache hit rate is about 81%; fresh calls hit about 34%.

## Prompt layers

- **F5 Layout.** The system layer is followed by one user message holding a JSON array of six layers: 工具层, 人设层, 记忆层, 待办层, 环境层, 进度层 (`life_prompt.py:144-156`).
  - `LifeAgent.build_react_messages` replaces AS2's own memory context (`custom/agents/life_agent.py:453`).
- **F6 记忆层** (`src/worldmock/simulation_engine/observation/layers/memory_layer.py`) is rebuilt from the event log at every observation.
  - **earlier_today**
    - Contains one entry for each *session that ended today*. Each entry holds:
      - `settled`: activity ending sentences, purchase sentences and deliveries;
      - `self_notes`: summaries and plain memories from successful pause/conclude calls.
    - It does not contain report_status summaries, reply bodies, query answers or views.
    - Because of overnight sessions, 35–37% of its characters are about the previous calendar day.
  - **earlier_days**
    - `settled` holds only orders that are still open. It was empty in all 503 entries seen in V (753 in B).
    - It also holds bedtime reflections, filed by date.
  - **8000-char window:** trimming deletes the oldest reflections first, then shrinks the oldest non-exempt entry for today.
    - On workdays, reflections disappear after about 17:30 and stay gone for the evening. On 10-11 the 20-hour home session was trimmed away by 15:41.
  - **Midnight wipe:** yesterday's completed facts leave the prompt at 00:00. Past meals are never shown.
  - Size: median 4.4k chars, max 7.8k. It is the most stable large layer, byte-identical in about 93% of consecutive fresh requests.
- **F7 进度层** (`progress_layer.py`) covers the current session only. It also includes carried interact records and `rejected_today`, which is day-scoped (8 rows).
  - There is no size cap. Median 14.7k chars, p90 27.6k, max 42.8k; 93.5% of it is `tool_call` records. Condensation never ran.
  - It is host-only: it is not stored inline in observation events, which keep `progress_event_ids` instead.
  - Query answers and `self_note` reply bodies live here and are lost when the session ends.
- **F8 待办层** contains paused activities (never expire), errands (max 8) and today's day plan. It does **not** list pending orders.
- **F9 人设层** carries the card plus `preferences` and `relationships`.
  - These come from every successful `conclude_activity` view across the whole run. For each exact `subject` string the last view wins and carries a `noted_on` date. Relationship views carry no date.
  - There is no cap and no expiry. The run ended with 40 preferences (about 4k chars) and near-duplicate subjects such as 美式咖啡/冰美式咖啡.
  - This is effectively the only long-term digested memory the prompt shows. It is not categorised, and it sits before 记忆层.

## Agent-side store (AS2)

- **F10 Store contents.** `run/as2/agents/agent_0001/memory/episodes.jsonl` gets conclude `memories` and `views`, but not the `summary`, plus bedtime reflections. V ended with 255 episodes.
  - Merge rule: same type, within 24h, bigram Jaccard ≥ 0.6. Only 1 merge happened.
  - **Threshold consolidation:** 27 threshold calls plus 4 wake calls in 5 days. Each writes a replacement summary of 108–548 chars into state.json/MEMORY.md. No decision prompt reads it (`life_memory.py:79`).
  - MEMORY.md re-renders cause 153 `refresh_summary` memory_updated events.
  - **Memory tools** search episodes only. Search treats the query as one substring of the record JSON, so multi-word queries never match. There were 2 calls, at 10-12 18:00; both returned empty.
  - `recall_task_summary` gives by-name factual recall over all 146 run activities. It was used 0 times.
- **F11 Bedtime reflection** runs at the first completed sleep of the local day, before that second's observation.
  - Input: the last 30 episodes, 8–12 of which are views. The input windows began between 09:37 and 13:55, so mornings were missed.
  - Output: 1–5 insights of about 54 chars each, filed to wake date − 1.
  - Boundary defect: B filed one reflection under 10-07, before its run started.
  - There is no reflection on day 1 or after the last night.
- **F12 Midnight planner** (`src/worldmock/simulation_engine/scheduling/plans/day_plan_inputs.py`) builds a deterministic review of yesterday (00:00 → now):
  - settled results (29–34 per day);
  - conclusion records (summary + memories + views, marked as unverified self-report);
  - verified experiences, previous goals, orders and errands.
  - It outputs the day plan in 待办层. It never reads memory_updated or reflections, and the reflection for a day arrives about 8h after this plan.

## Categories and calendar

- **F13 No category on memory.** Nothing carries a category today.
  - A closed 11-value `INTENT_KINDS` set exists (`state/planning/schedule.py:58-86`): 睡眠 晨间例程 通勤 工作 用餐 休息 娱乐 社交 家务 运动 外出.
    - It is model-assigned on day-plan goals and is noisy: a nap goal was tagged 用餐.
    - `ActivityState.plan_goal_intent_kind` has no writer, so it is null on 147 of 147 activities.
  - Classifying with rules derived from current data puts 76–80% of decisions cleanly in one class. The ambiguous cases are:
    - commutes;
    - content apps used at the office;
    - washing dishes;
    - coffee;
    - kit queries;
    - `office-eat-lunch`, which has no food signal;
    - 食材包 kits, which have goods category `goods`.
  - The work rule (workplace kind) does not carry over to other personas. Sales has work at other places; students study in the library; the homemaker has no work schedule.
  - Conclude notes link to exactly one activity in 235 of 235 cases.
  - Reflections are cross-cutting: 7 of 20 span several categories.
  - Home-cooked dinners are never named, because they consume a `groceries` state. The dish is recoverable only from the previous kit receipt.
  - "rest" (sleep) is the only kind that crosses midnight.
- **F14 Calendar.** The package config runs 30 days, 09-28 → 10-28, including the national holiday 10-01 to 10-07 and a make-up workday on Saturday 10-10.
  - A month tier would fire once (September, 3 days). A year tier never fires.
  - The owner's ladder leaves a gap from day 4 to the month boundary.
  - On 10-08, the calendar "last 3 days" are all holiday, so the last workday (09-30) is out of view.
  - Three day rules are in use:
    - calendar midnight: planner, `plan_date`, `rejected_today`;
    - session end date: 记忆层 today;
    - wake date − 1: reflection.

## Cost and caching

- **F15 Cost.** About $0.33 per simulated day; V total $1.683.
  - Fresh decision turns are 84% of cost, about $0.0046 each. History turns cost about $0.0021.
  - Memory upkeep (consolidation and reflection) is 1.4%.
  - Overall cache hit rate is 48%. Fresh turns cache only the system message plus tools (about 5.5k tokens), because 工具层 comes first and changes on almost every request.
  - Simulated reordering, putting the stable 人设/记忆/待办 layers before 工具层, would add 2.3–2.7k cached tokens per fresh call, saving about $0.12–0.24 per 5-day run.
  - A per-round memory list that grows inside the stable block would break the prefix.
- **F16 Size of one entry per decision.**
  - Settled lines: 59 chars on average, 35–38 lines per day, 2.0–2.4k chars per day.
  - Model-written text: about 176 chars per conclude and 121 per report_status.
  - Settled plus model text: 8–12k chars per day, or about 3.4–5.1k tokens.

## Contracts and gates (authority: `docs/design/*.md`)

- **F17 Stale references.** `docs/CONTRACTS.md`, `docs/VERIFICATION.md`, `docs/ARCHITECTURE.md` and `docs/dev/ANALYSIS-spec-coverage.md` do **not** exist; CLAUDE.md references them, so it is stale.
  - The rule is that a design doc, its implementation and its behaviour tests change in the same commit (`docs/INDEX.md:18`, `01-architecture.md:283`).
  - There is no clause-to-test matrix any more. `SPEC-as2-integration.md` is historical; the live AS2 boundary is in `docs/design/10-runtime.md`.
- **F18 Events.** Event types form a closed set of 14 (`recording_engine/events/models.py:110-114`), and new kinds are avoided by precedent.
  - `memory_updated` has fixed operations (append_episodes, refresh_summary) and fixed write points (activity_conclusion, reflection).
  - It stores episode snapshots, but only SHA-256 hashes of MEMORY.md, episodes.jsonl and state.json. The summary text appears in no event.
  - The AS2 `MemoryUpdateEvidence` operation is a Literal.
  - Recovery and replay read committed events and never regenerate them.
- **F19 Quality and manifest.**
  - Quality call classes are keyed by request shape: no tools → consolidation; submit_reflection → reflection; anything else → unrecognized. A new class requires a quality schema bump.
  - A new ModelConfig field changes the stage identity key set, which bumps `RUN_MANIFEST_SCHEMA_VERSION` 7 → 8.
  - LabelKit-DataSet pins export 8, run-manifest 7, world 15, checkpoint 1 and the 14-type event enum. Event `data` is a free object there.
- **F20 Fingerprints.**
  - The prompt sha covers both system layers and LAYER_SCHEMAS; the tool-schema sha covers all tools. Both are frozen, so runs started under old code cannot be resumed under new code.
  - `test_system_layer_text.py` pins the 20 rules verbatim.
  - "Ten native tools" is pinned in docs (06-agent.md:52-57, 10-runtime.md:79-81) and in tests.
- **F21 Integration boundary.**
  - The Life extension channel exposes read-only methods only (`read_query`, `due_reflection`), each with a thin forward.
  - Do not fork the AS2 loop or monkey-patch it. AS2 patches go through `patches/agentsociety2/SERIES`.
  - `recording_engine` must not import `simulation_engine`, `agents` or `runtime`. Shared enums belong in `worldmock.state`.
- **F22 Engineering gates.**
  - Style gate (`tests/unit/engineering/test_style_gate.py`): lines ≤ 120 chars, functions ≤ 50 lines and ≤ 5 params, files ≤ 2000 lines, Chinese docstrings. `life_agent.py` is 1016 lines.
  - Coverage: branch ≥ 85% and no uncovered public interfaces; no stub implementations allowed.
  - Day-run baselines need a two-candidate refreeze. viewer-context must be re-recorded with a real host.
  - Prompt changes can only be proven with real-model runs; recorded responses reject model-visible changes.
  - The readonly Q&A path has no production caller.
