# Agent-side memory store, compaction and search: current system (HEAD f67c25e5)

## 0. How the owner's terms map to the code
- **Round** is one AS2 `PersonAgent` step. It has four parts: the reflection phase (optional), one frozen observation, a ReAct loop, and a step tail. The tail calls `after_step` (vendor/…/agent/person.py:530-553; docs/design/06-agent.md:322-336). LifeAgent ends the loop as soon as any world request is registered (custom/agents/life_agent.py:563-583).
- **Turn budget**: `max_react_turns` defaults to 16, range 1-64 (src/worldmock/configuration/inputs/models.py:223). It is checked at life_agent.py:388-395. Memory tools are taken away on the last turn and on the retry after a truncated response (life_agent.py:397-404).
- **Evidence (variety run)**: 402 of 406 decisions used 1 model turn and the other 4 used 2. The 16-turn budget is effectively never used.

## 1. What gets written per round
- **Only `conclude_activity` writes to the store.** `_conclusion_memories` returns `[]` for every other tool (life_agent.py:750-762). The store therefore gets nothing from start rounds, `report_status` rounds or `pause_activity` rounds.
- **Only the first world request of a reply is read** for memories (life_agent.py:582).
- **Fields written** (requests.py:451-549):
  - `items[].memories` take `{type ∈ commitment|goal|conflict|resolution|identity|routine|reflection, text ≤1000 chars with no hex ids of 8+ characters, importance 0-1, keywords ≤12}`.
  - `items[].views` take `{type ∈ relationship|preference, about, text, importance, keywords}`.
  - Memories and views together are capped at 8 per call (requests.py:412, 546).
- **`summary` (≤500 chars) never enters the store.** It is required on all three control tools (requests.py:223-228, 337-348, 508).
- **`self_note` never enters the store.** It is the reply's free text and is saved on `action_requested.data.self_note` (simulation_engine/decisions/decision_coordinator.py:102-126). It is shown only in the progress layer (progress_layer.py:137-138).
- **Flattening**: `control_items` turns views into stored memories with `subject = about` (control_items.py:110-126). LifeAgent then **drops `subject`** and sets `source=activity_conclusion` and `epistemic_status=subjective` (life_agent.py:760-761). In the store a preference loses the thing it is about, unless that thing appears in its keywords.
- **Validation**: AS2 checks the payload (memory_runtime.py:376-387). WorldMock then requires type ∈ `SUBJECTIVE_TYPES`, status subjective, and the conclusion source (life_memory.py:214-225; state/model/memory.py:23-24).
- **No domain field exists.** There is nothing like food/life/work on a request, a stored record or a snapshot. The `type` set is psychological, not topical.

## 2. Store and merge rule
- **Files**: `MEMORY.md`, `memory/episodes.jsonl` and `memory/state.json` (vendor/…/agent/memory.py:19-22).
- **Record schema** is exactly 15 keys (memory.py:46-50). The id format is `m_{tick:08d}_{counter:06d}` (memory.py:526). Tick is 0 in all 255 variety records, so ids are just a counter.
- **Merge** (life_memory_store.py:13-16, 123-150): it scans existing records newest first. It merges when the type is the same, the record time is 0-24h before the new memory's time, and the character-bigram Jaccard is ≥0.6.
- **On a merge the new text is discarded.** Only `merge_count` goes up and `time` is overwritten (life_memory_store.py:75-79). Because `time` changes, the file is no longer in time order. This happens at variety episodes 214→215.

## 3. Threshold consolidation
- **Trigger** (step tail): computed over unconsolidated entries that are not reflections. It fires when Σimportance ≥3.0 or count ≥10 (life_memory.py:29-31, 202-212). The AS2 `should_consolidate` is not used.
- **Input**: the persona card, the current summary, and the last 30 unconsolidated episodes as `{type, text, time}`; these include reflections (life_memory.py:240-252).
- **Output**: plain Chinese prose written by the model. It is truncated to 4000 chars and replaces `state.json.subjective_summary`. The cursor moves to the last record (life_memory.py:253-261; memory.py:428-445).
- **MEMORY.md** is re-rendered at every step tail with three parts: identity, the 5 verified facts, and the summary quoted (life_memory.py:118-121, 278-287).
- **Nothing in the decision prompt reads the summary or MEMORY.md.** `build_context` returns only recent episodes and paths and says so (life_memory.py:79-92). Its only consumer is AS2's `build_react_messages` (person.py:426-435), which LifeAgent overrides (life_agent.py:453-458). A grep finds no other reader of `subjective_summary` or `MEMORY.md`. Export does not read them either.
- **Behaves as a rolling rewrite, not long-term memory.** The final variety summary is two paragraphs about that last evening only (run/as2/…/memory/state.json).

## 4. Bedtime reflection
- **When**: `due_reflection` fires when this second has no decision yet, a completed sleep ends exactly now, and no other sleep has ended on the same local day. The filing date is the wake day minus one (simulation_engine/evidence/memory.py:13-34). This check runs before the observation (life_agent.py:311-337).
- **Steps**: consolidation (reason `daily reflection`), then a reflection call that must use `submit_reflection` and may retry up to the configured count (life_memory.py:127-143, 175-200).
- **Input**: the last 30 store records in file order, as `{id, type, time, text}` (life_memory.py:264-275). No model call is made when the store is empty.
- **Output**: 1-5 insights, each citing input ids, stored with `type=reflection, source=reflection, importance=0.8, keywords=[]` (life_reflection.py:14-15, 101-124).
- **Registration**: the environment records `memory_updated` with `trigger=["reflection"]` and `reflection={date, sleep_activity_id}` (evidence/model.py:173-184).
- **How it reaches 记忆层**: `_reflections` reads `memory_updated.data.episodes[].text` plus `reflection.date`. It puts each insight into `earlier_days[date].self_notes` as `{activity: null}`. It does not read the store (memory_layer.py:225-237, 306-317).
- **Edge case**: if an insight merges into an earlier record, the event carries that older text. If the target was a conclusion-sourced `reflection` record, the insight is filtered out entirely (life_memory.py:140-141).

## 5. What the decision prompt actually gets
- **记忆层 is rebuilt from the event log, not from the store** (memory_layer.py:99-113):
  - `earlier_today` holds sessions that ended today: their settled lines plus `self_notes`, which are pause/conclude `summary` and plain memory text (memory_layer.py:283-296).
  - `earlier_days` holds still-open orders plus reflections.
  - The window is 8000 chars. When it overflows, the oldest days' reflections go first, then older today-entries are cut down to their open lines (memory_layer.py:36, 320-336).
- **Views go to 人设层, not 记忆层.** `persona_views` keeps the last view per `subject`, with no limit (memory_layer.py:125-142). This is the only per-key long-term store that exists today.
- **The system prompt barely teaches memory use.** It has one clause saying the model may query memory before submitting (life_prompt.py:32) and one saying memory queries are instant (life_prompt.py:91). The tool descriptions are one line each (life_prompt.py:122-124). Nothing says the tools return only subjective notes, how entries are organized, or when to search.

## 6. Memory tools (life decision and readonly)

| Tool | Parameters (life_agent.py) | What it does (AS2 store) |
|---|---|---|
| `memory_recent` | `limit` 1-50 (:95-98) | last N records in file order (memory.py:624-634) |
| `memory_search` | `query` ≤200 chars, `limit` 1-50 (:101-114) | case-insensitive **single-substring** match over the full JSON line, ids/type/source/time included; newest first (memory.py:636-657) |
| `memory_time_range` → `memory_range` | `start_time`/`end_time` ISO with timezone, `limit` 1-100 (:117-140, 185-189) | **first** N in file order inside the range, using the merge-mutated `time` (memory.py:673-722) |

- **Scope**: all three search only subjective episodes. Verified facts are not searchable.
- **Results** are projected to `{type, text, time, epistemic_status}` with no id, keywords, importance or source (life_agent.py:192, 665-683). AS2 also has `memory_read`, but it is not exposed.

## 7. Readonly Q&A path
- **Prompt**: the readonly system layer plus `[persona, memory]` and the question (life_prompt.py:101-111, 164-169).
- **Layers come from `life_prompt_snapshot.json`.** Every life decision writes this file (life_agent.py:486-487; life_persona.py:72-84). The snapshot is the 人设层 and 记忆层 of the last decision.
- **Tools**: the three memory tools plus `finish` (life_agent.py:401-403). The readonly path never writes memory (life_prompt.py:108).

## 8. Checkpoint, restore and validation
- **Snapshot**: at every checkpoint the whole workspace is saved as a deterministic tar.gz asset (custom/envs/life_env.py:217-233; recording_engine/storage/checkpoint.py:33-54). That covers MEMORY.md, `memory/*` and the prompt snapshot.
- **Restore**: the workspace is unpacked into a new branch directory (checkpoint.py:68-91; runtime/lifecycle/runner.py:370-383).
- **Validation on restore**: `enable_memory` must be true, and then `AgentMemoryStore.validate_existing` runs (runner.py:412-428; memory.py:314-334). It checks that the files exist and are not symlinks, MEMORY.md is not empty, the state.json key set is exact, ids are unique, `episode_counter == len(records)`, and the cursor id exists. `restore_store` validates again (life_memory.py:67-77).
- **The hashes in `memory_updated` are never compared against the workspace files.** They are only format-checked (recording_engine/events/memory_evidence.py:49-80; reports/quality.py:573-576).

## 9. Evidence from the variety run (baseline in brackets)
- **Episode counts**: 255 episodes [244]. By (type, source): routine/conclusion 138, preference/conclusion 68, reflection/reflection 20, reflection/conclusion 13, commitment 9, relationship 7. Goal, conflict, resolution and identity never appear. Only 1 merge happened: the 番茄牛肉刀削面 preference, `merge_count` 2.
- **Conclude calls**: 137 [137], all of them with memories; 75 [55] also had views. They held 160 memories and 76 views. There were 0 pause calls [1].
- **Memories are often one-day progress facts.** quality.json counts 138 of 160 conclusion memories as routine. Example: "10月8日早上看墨迹天气，今天小雨，出门上班要带伞" is typed `routine`.
- **Food is not categorized but is everywhere.** A rough keyword split gives food-only 62, work-only 79, both 57, neither 57 [79/74/24/67]. Examples:
  - "午饭想吃点带汤的热面，番茄牛肉刀削面…" (preference)
  - "跟王磊互相评审代码效率高…" (relationship)
- **Memory tool use**: 2 calls in total [0], both `memory_search` at 10-12 18:00: `"盒马 食材包 土豆烧牛肉"` and `"晚饭 到家 做饭"`. **Both returned `[]`**, although 12 records have keyword 食材包 and 8 have 盒马. The space-joined query is matched as one literal substring.
- **`memory_updated` events**: 294. Of these, 137 are conclusion writes, 4 are reflections, and 153 have an empty trigger: MEMORY.md re-renders with no episode change. 31 consolidations (27 at step tails, 4 in reflection phases), about 6 a day.
- **Reflections**: 4 filings (10-08 to 10-11), 5 insights each. The baseline also filed one for 10-07: 3 insights from a single 00:10 note.
- **Reflection input misses most of the previous day.** "Last 30" covered only about 13:45-23:45 of it, for example 10-10 13:55 to 23:30.
- **Reflections get evicted from 记忆层 every afternoon.** They first drop out at 17:30, 17:46, 14:50 and 18:00 on 10-09 to 10-12. Observations that still show them: 56/84, 53/83, 32/69, 60/89.
- **By the last observation (10-12 23:52) `earlier_days` was empty** and the morning session entry was gone. 记忆层 size: mean 3890 chars, max 7825.
- **Two `memory_protocol_error` flags** in quality.json. Both are replies that left out `items[0].views`.

## 10. Where the current design differs from the target
- **No compaction per day, month or year.** There is one rolling summary that nothing reads, and one reflection a day built from about half a day's notes. Old memory exists only as raw episodes reachable through tools.
- **Everything is in one store.** There is no category key; `type` is psychological, and many entries mix food and work.
- **"Last days" in 记忆层 means reflections only, and they are evicted by evening.** Today's section mixes settled facts (progress) with self-notes (memory).
- **Writes happen on about 34% of rounds** (137 of 406, conclude only), not on every round.
- **Search cannot do "category + time".** It is a single-substring or time-range match, with no category filter and no multi-term matching.
- **The system prompt does not teach the memory system.**

## Open questions / uncertainties
- Should `report_status`/`pause_activity` summaries and `self_note` count as per-round memory entries? Today they live only in events and the progress layer.
- Views keep a key by `about` in 人设层 but lose it in the store. Which of the two should own long-term preferences such as food?
- The food/work/life split rests on keyword heuristics I wrote. A real split would need a rule for mixed entries like lunch at the office.
- I did not check whether `last_decision_time` edge cases (naps, all-nighters) skip reflections in other runs. I also did not check how the midnight plan's "收尾记录" input overlaps with day-level compaction (06-agent.md:201-206).
- Tick is 0 for every record. I didn't check whether that is intended (`_life_tick`).
- I only cross-checked docs against code where they overlap. CLAUDE.md is stale as noted. docs/design/06-agent.md matches the code on reflection and threshold behaviour.

Helper scripts I used (scratch only): /private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/{episodes.py,examples.py,runstats.py,memsearch.py,memlayer.py,reflin.py,cats.py}