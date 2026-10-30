# Current prompt layers: what 记忆层, 进度层, 待办层 and 人设层 carry (HEAD f67c25e5)

Every claim below is backed by code I read. Run figures come from read-only SQLite queries of the decision `model_request` user messages. I wrote three scratch scripts for this: `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/layers.py`, `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/trim.py` and `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/progress.py`.

## 1. How the layers are put together
- The user message holds six layers in a fixed order: 工具, 人设, 记忆, 待办, 环境, 进度 (`src/worldmock/agents/prompt/life_prompt.py:144-156`, `prompt_schemas.py:293`).
- Five of them come from one observation (`src/worldmock/simulation_engine/observation/layers/observe.py:57-69`). The 人设层 is built by the host (`custom/agents/life_agent.py:479-481`).
- The system layer describes the layers in one sentence each: "记忆层写此前经历 … 进度层写这段停留或路程中已经发生的事" (`life_prompt.py:25-27`).
- On memory tools it only says you may query memory before submitting (`:32`, `:91`). It says self-written notes are unverified (`:75-78`). It says the reply body lasts only until the current stay or trip ends (`:96`).
- It never explains `earlier_days`/`earlier_today`, trimming, or when to use `memory_*`.
- A code "session" is either a **stay or a transit** (`environment_layer.py:22-30`). The owner's "session" means stays only.
- A code "decision" ends with one world request (`life_prompt.py:29-34`). That roughly matches the owner's "round".

## 2. 记忆层 (`memory_layer.py`)
**What it is.** It is rebuilt from the event log on every observation (`:99-113`). It has two lists, `earlier_days` and `earlier_today` (`:339-343`; schema `prompt_schemas.py:152-162`).

**`earlier_today`**
- One entry per session that ended today, keyed `recorded_at`=session end (`:105-107, 216-222`). Sessions are split by event sequence numbers (`session_bounds.py:21-29`), so an event shows up in either 进度层 or 记忆层, never both (`session_bounds.py:6-7`).
- `settled` lines, in event order (`:240-257`):
  - one ending sentence per activity whose end event falls inside the session: name, start–end with pauses, outcome, content titles (`settled_text.py:63-81, 172-178`);
  - purchase sentences for paid orders (`memory_layer.py:260-270`, `settled_text.py:112-121`);
  - visible delivery arrivals (`memory_layer.py:273-280`).
  - Not included: app pushes, errand events, balance notices, query answers. 06-agent.md:265 says query answers are dropped deliberately.
- `self_notes` (`:283-296`):
  - included: the `summary` and plain (non-view) `memories[].text` of successful `pause_activity`/`conclude_activity` calls, labelled by activity name;
  - excluded: views, `report_status` summaries, errands, reply bodies.
  - A note is filed under the session where the conclude call happened. A drive's notes therefore land in the *next* session's entry: in the run, drive entries show 1 settled line and 0 notes.
- **A session that crosses midnight is filed whole under today.** Its lines are only split by origin date for `earlier_days` (`:111, 230-232`). Run example: the entry recorded at 10-09 08:11 starts with the 10-08 18:05 dinner.

**`earlier_days`**
- Built from sessions that ended before today (`:225-237`).
- `settled` keeps only lines marked open (`open_only`, `:252, 257`), meaning orders still `pending`/`ready_for_pickup` (`progress_layer.py:38`). Lines are grouped by the date of the original request.
- `self_notes` hold only bedtime reflections, filed under `reflection.date` and shown when that date is before today (`:233-236, 306-317`). Reflections are shaped `{"activity": null, "text": …}` and number at most 5 per day.
- The filing date is the wake date minus 1 (`simulation_engine/evidence/memory.py:16, 34`).
- **Doc conflict:** SPEC-life-realism.md:235 and :974 say "sleep start −6h". 06-agent.md:296 agrees with the code. I trusted the code.

**8000-character window** (`:36, 320-336`)
- While the compact JSON is over 8000: first clear the reflections of the oldest dated `earlier_days` entry, one date at a time. Then cut the oldest non-exempt today entry down to its open lines.
- Exempt: the latest ended session (`:108-109`), plus every open line (`earlier_days` lines are never cut).
- If it is still over, a warning is logged and the layer is left as is.
- **Doc conflict:** SPEC-life-realism.md:973 says unreplied direct messages also count as open. The code has orders only.

## 3. 进度层 (`progress_layer.py`)
- **Scope:** the *current session* only, from `session_started_at` (`:62-68, 77-88`). Exception: "carried" interact records, whose start request came in an earlier session and is pulled in (`:143-166`). Paused activities are not carried.
- **Record types** (`:100-107, 209-292`; schema `prompt_schemas.py:261-283`):
  - `tool_call`: request time, tool, arguments, latest result (`:127-140, 175-180`). Control tools' arguments show `activity_name` and `summary`; conclude also shows plain memories, and views are dropped (`control_items.py:84-96`). The reply body is attached as `self_note` (`:137-138`).
  - `delivery`, `app_push`, `callback_reminder`, `balance_decreased`, `errand_reminder`, `errand_completed`, `plan_reminder` (derived from the day-plan goal `reminded_at`).
- **Query answers** live here (`details.query`) and disappear when the session ends (06-agent.md:265).
- **`rejected_today`:** the 8 newest *day-scoped* rejections. This is the only day-scoped part of the layer (`:316-347`; schema maxItems 8).
- **Size:** no character cap. When the whole request reaches 85% of context, `self_note`s are stripped and then old unprotected runs are folded into `condensed` records (`prompt_capacity.py:28, 80-149`).
- `report_status` summaries and reply bodies never reach 记忆层. After the session they can only be reached through `recall_task_summary`.

## 4. 待办层 (`todo_layer.py:16-73`)
- `paused_activities`: every paused activity of the agent, from any day, with paused time, place, objective progress, and whether resume/conclude is possible. They never expire.
- `errands`: open personal errands (`scheduling/plans/errands.py:107-109`; schema max 8; fields name/done_when/progress/memo/remind_time/noted_on).
- `day_plan`: today's goals with intent, text, remind time and rough time. It is null once the day ends.
- The day plan is produced by plan_day from **yesterday's settled facts, conclusion records, errands, in-flight orders and goals** (`day_plan_inputs.py:83-106`). It is the only existing daily compaction of yesterday. Its output is intentions, not memory. Run example: the 10-12 plan says "用盒马昨晚订的…食材包做晚饭".

## 5. 人设层 (`life_persona.py:29-54`, `memory_layer.py:127-144`)
- **Card fields** (static): identity, rhythm, habits, tastes, coping, relationships. They are capped at 600 hanzi.
- **`preferences`:** read from every successful `conclude_activity` across the whole run (range `(0, None)`). One entry per exact `subject` string, last one wins, with `noted_on` = local date of the latest write. Order follows when the subject first appeared. There is no expiry and no cap.
- **Relationship `view`:** last one wins per person, with no date (`life_persona.py:117-123`).
- **What it really holds:** this is in practice the only long-term digested memory in the prompt. At the end of the variety run there were 39 preferences mixing food (番茄牛肉刀削面, 食材包, 鱼香肉丝), work (功能开发与自测, 需求评审会议) and life (彩虹桥, 打扫卫生).
- **Near-duplicate subjects** survive because matching is on the exact string: 美式咖啡/冰美式咖啡, 午休小睡/午睡五十分钟左右, 自己做的晚饭/自己做晚饭.

## 6. Mapping onto the owner's four quadrants

| Content | Where it is now | Quadrant |
|---|---|---|
| Current-session tool results, deliveries, pushes, reminders | 进度层.events | progress-today (but session-scoped: 56/410 decisions had a session that started on an earlier date) |
| `rejected_today` | 进度层 | progress-today |
| Settled lines of sessions that ended today | **记忆层**.earlier_today.settled | progress-today, sitting in the memory layer (the owner's complaint) |
| In-progress / just-ended activity state | 工具层 targets (`tool_layer.py:216-229`) | progress-today |
| Open orders from earlier days | 记忆层.earlier_days.settled | progress-longterm (seen in **0** decisions in both runs) |
| Paused activities, errands, day plan | 待办层 | progress-longterm |
| Conclude/pause summaries and memories of today's ended sessions | 记忆层.earlier_today.self_notes | memory-today |
| Reply-body `self_note`, report_status/conclude summaries in the current session | 进度层 | memory-today (lost when the session ends) |
| Bedtime reflections | 记忆层.earlier_days.self_notes | memory-longterm (trimmed first) |
| Preferences / relationship views | 人设层 | memory-longterm (never expire) |
| MEMORY.md consolidation summary, AS2 `episodes.jsonl` | not in the prompt (`life_memory.py:79-81`); reachable only through `memory_*` tools (`life_agent.py:169-188`) | memory-longterm, never shown |

**Never shown:**
- completed facts from past days: meals, purchases, content watched, work done;
- past days' conclude summaries and memories;
- report_status summaries;
- query answers after the session;
- any food/work/life grouping. The only category field is the memory `type` (commitment/goal/…/routine/reflection, `state/interaction/requests.py:456`). In the variety run nearly every conclude item writes one `routine` memory: 138 routine out of 140 items.

**Duplicates:**
- Each conclude yields two notes: an episodic summary plus a generalised routine. Both are in `self_notes`.
- `self_notes` repeat the settled facts (prices, times).
- Routines and reflections repeat preferences and card habits.
- Objective progress appears in both 工具层 and 待办层 (`activity_control.objective_progress`).

## 7. Measured sizes (variety, 410 decision requests, compact JSON characters)

| Layer | median | p90 | max |
|---|---|---|---|
| system | 3377 | 3377 | 3377 |
| 工具层 | 8596 | 9624 | 10120 |
| 人设层 | 3026 | 4946 | 5304 (preferences alone: 1818 / 3697 / 4045) |
| 记忆层 | 4380 | 6929 | 7825 |
| ↳ earlier_today | 3883 | 5721 | 7775 (after 20:00: median 5007, max 5831) |
| ↳ earlier_days | 2 (`[]`) | 1851 | 1851 |
| 待办层 | 865 | 942 | 942 |
| 环境层 | 379 | 395 | 426 |
| 进度层 | 14663 | 27618 | 42812 (26 / 54 / 75 events) |
| whole user message | 31681 | 45335 | 58162 |

Baseline run: 记忆层 4581 / 7761 / 7878, 进度层 10263 / 22195 / 29467, 人设层 2277 / 3875 / 4011.

**What fills 进度层:**
- 93.5% of its characters are `tool_call` records.
- By tool: start_side 31.4%, conclude 30.9%, report_status 21.3%.
- `details.query` alone is 11.5%. `self_note` is 0.4%.
- Condensation never ran, in either run.
- The largest 进度层 was one home session from 10-10 18:15 to 10-11 14:30.

**The window trims every day:**
- Reflections were trimmed in 125 of 410 decisions in the variety run and 177 of 423 in the baseline.
- The first trim falls at about 17:30–18:00 on workdays, when the work session closes, and at 14:50 on 10-11.
- After the trim, the morning home session is removed in full: it holds the previous evening, the sleep and breakfast. Example: at 10-10 20:00, `earlier_today` held only drive (1 line), work (20 lines / 35 notes), drive.
- On 10-11 the 20-hour home session (30 lines / 48 notes) was trimmed away by 15:41.
- In the evenings the persona has nothing in the prompt about its own morning, and nothing from earlier days except preferences and the day plan.

**Memory tools are almost unused:** `memory_search` was called 2 times in variety and 0 in baseline; `recall_task_summary` 0 times in both.

## Open questions / uncertainties
- I measured sizes by re-serialising each parsed layer, which assumes compact JSON. The tool layer may have been compacted into groups by `compact_render` (`narration.py:175`), so its numbers describe the sent form.
- I inferred trimming from missing reflections and missing sessions, not from logs. `run/logs` showed no "Memory layer exceeds" warning, which is consistent: the warning only fires when trimming fails.
- I did not check what `memory_*` and `recall_task_summary` actually return, or whether `episodes.jsonl` holds facts as well as subjective entries.
- Schema field `last_report` is declared (`prompt_schemas.py:83, 86`) but no builder writes it. It could be dead code or unfinished work.
- `max_react_turns` defaults to 16 with a range of 1–64 (`configuration/inputs/models.py:223`). The owner's "32 turns" would be a config change; I did not check how turns are counted per decision.