# WorldMock memory redesign: an owner-literal proposal (HEAD f67c25e5)

**Angle.** This design follows the owner's model as closely as it can:
- three categories: 饮食, 生活, 工作;
- per-round entries for today;
- one entry per day for the last three days;
- one entry per previous month and per previous year;
- a model-written paragraph per category at each tier;
- `search_memory(category, time)` for everything older.

I deviate only where the literal model breaks (F13, F14) or where it would conflict with a hard invariant: the environment owns all facts, events are never regenerated, and the decision loop is not forked. Every deviation is marked **[deviation]** and justified.

---

## 1. Units and glossary

| Owner term | Code name (kept) | Decision |
|---|---|---|
| session | `SessionState`, a `stay` or a `transit` (`state/world/agent.py:57-95`) | A transit is a session too. **[deviation]** I add a derived unit, the **segment (段)**: one session cut at local midnight. A segment's lower bound is the larger of `session_lower` (`session_bounds.py:21-29`) and the commit-aligned last event before local 00:00. That is the same value `rejected_today` already computes (`progress_layer.py:316-322`). Memory and progress are keyed by segment, so the overnight-stay problem (F1, F6) goes away. |
| round | **decision** (`decision_id`). AS2 `round_id` is left alone. | A round ends at the first world request, including `report_status`, `revise_day_plan` and rejected requests (`life_agent.py:563-583`). Docs and code keep the word "decision" so it does not collide with `round_id` and `same_moment_rounds`. |
| turn | model call `…:model:NNNN` | The budget stays at `max_react_turns=16` (`configuration/inputs/models.py:223`). Observed maximum is 2–3 turns (F3). 32 is only a config value: it fits the 1–64 range, needs no code change, and gives a new run identity. Recommendation: keep 16. |
| memory entry | **条目 (entry)**: one per (category, local minute) | Built deterministically from committed events and request texts (§3). There is no new store. |

**What produces an entry:**
- committed facts: activity endings, paid orders, deliveries and receipts, completed errands;
- texts from accepted `report_status`, `pause_activity` and `conclude_activity` items: summary, memories and views;
- reply bodies (`self_note`).

Rounds that leave none of these produce no entry: starts with no fact yet, rejections, `revise_day_plan`, and read-only queries (§3 collapse rules).

---

## 2. Target prompt structure

**User-layer order [deviation from `life_prompt.py:156`]:** 人设层, 记忆层, 待办层, 工具层, 环境层, 进度层.

Today 工具层 comes first and changes in 399 of 405 consecutive requests. Because of that, fresh calls cache only system plus tools, about 5.5k tokens (F15). In the new order the three slow layers come first:
- 人设层 is static once preferences are removed (§7).
- 记忆层 changes only at segment ends and at midnight.
- 待办层 was byte-identical in 98% of consecutive requests.

| Layer | Holds | Budget (compact JSON chars) |
|---|---|---|
| 人设层 | Card only (≤600 hanzi). `preferences` and `relationships[].view` removed. | ~1.2k (measured median without preferences: 1,193) |
| 记忆层 | `可查` → `近三天` → `今天`, ordered from most to least stable | `可查` ≤200; `近三天` ≤9,000; `今天` ≤6,000; hard cap 15.2k. Estimated typical: ~7k in the morning, ~11k in the evening. |
| 待办层 | paused activities, errands, day plan, **new `在途订单`** | ≤1.6k |
| 工具层 | unchanged | ~8.6k |
| 环境层 | unchanged | ~0.4k |
| 进度层 | current **segment** only. Carried records and condensation are unchanged. | No cap (the 85% condensation still applies). Simulated on the variety run: the midnight cut takes median/max from 14.6k/42.8k to 11.5k/34.1k, and the total over 406 prompts falls by 18.6%. |

The memory layer keeps the existing guarantees:
- It is still never condensed (`prompt_capacity.py:1-7`).
- It is still stored as one content-addressed observation part (`observation_parts.py:21,68-78`). Because it changes only about 10 times a day, the parts dedupe.

**The owner's 2×2 mapped onto the layers:**

| Quadrant | Where | What it holds |
|---|---|---|
| memory-today | `记忆层.今天` | Entries from **today's ended segments**, grouped by category. Each entry has 核验 and 自述. 自述 covers summaries, memories, views and reply bodies of past segments. |
| memory-longterm | `记忆层.近三天`, plus `可查` and `search_memory` | One day entry per category for the last 3 days that have records in that category: 核验 plus 回顾. Older days, months and years are reached by search. |
| progress-today | `进度层` (plus the in-flight targets in 工具层 and the body in 环境层) | The current segment's tool calls and results, **query answers**, **reply bodies** while the segment lasts, deliveries, pushes and reminders. `rejected_today` stays here, unchanged and day-scoped. |
| progress-longterm | `待办层` | Paused activities (unchanged), errands (unchanged), day plan (unchanged), and **open orders**. Open orders move out of `记忆层.earlier_days.settled`, which was empty in all 503 entries observed. |

How the current fields map:
- **Query answers** stay progress-only and expire with the segment. They are stale price snapshots, and the existing doc already drops them (`06-agent.md:265`). The query's ending line stays in 核验.
- **Reply bodies** used to be lost when a segment ended. They now carry into `今天` as 自述.
- **`在途订单`** reuses `day_plan_inputs._pending_orders` (`:259-270`), widened to include `ready_for_pickup`.

---

## 3. Memory entry model

### 3.1 Shapes (model-visible, as they would ship)

Today entry and date-search entry:

```json
{"时刻":"12:28","核验":["吃午饭：12:13–12:28，按计划结束"],
 "自述":[{"活动":"吃午饭","内容":"红烧肉有点咸，下回挑清淡的"},
         {"活动":"吃午饭","内容":"看法（一荤一素工作餐）：量足但油大"}]}
```

Day entry in `近三天`:

```json
{"日期":"2026-10-10","星期":"星期六","安排":"工作日",
 "核验":["08:58 买了冰美式咖啡 1 份，付 18 元，预计 09:21 送到练秋湖研发中心",
         "12:13 一荤一素工作餐 1 份（11:50 购买）：已送到练秋湖研发中心",
         "做饭：18:15–19:25，按计划结束，《备菜做饭》《吃饭》《洗碗收拾厨房》，用的是板栗烧鸡配清炒娃娃菜一人份食材包"],
 "回顾":"早上照例一杯冰美式；午饭点了一荤一素工作餐，红烧肉偏咸。晚上用食材包做了板栗烧鸡，比外卖踏实。"}
```

The whole layer:

```json
{"layer":"记忆层","可查":{"最早":"2026-09-28","月份":["2026-09"],"年份":[]},
 "近三天":{"饮食":[…],"生活":[…],"工作":[…]},"今天":{"饮食":[…],"生活":[…],"工作":[…]}}
```

Field rules:
- `星期` and `安排` reuse the `WEEKDAY_NAMES` and `DUTY_DAYS` enums from 环境层.
- `回顾` is `null` until that day has been compacted.
- Keys are Chinese, as the brief requires. The other layers keep their English keys; renaming them is out of scope.

Internal (host-only) record for each entry: `(agent, day, minute, category, facts[(text, event_id)], notes[(label, text, request_event_id)])`. The event ids supply provenance for the digest's `source_event_ids`.

### 3.2 Verified versus subjective

- **核验**: deterministic sentences built from committed environment events by the existing builders (`settled_text.py:63-81` `activity_sentence`, `:112-121` `purchase_sentence`, `record_lines`).
- **自述**: model-written text taken from accepted requests. Views get the prefix `看法（<about>）：`, and the reply body has `活动: null`.
- **回顾**: digest output (§4).

The system rule (§6) names only 核验 as fact. This continues the rule at `life_prompt.py:75-78`.

### 3.3 How decisions become entries

1. **Segment assignment by event seq.** Each event belongs to a segment according to the `session_bounds` rule plus the midnight cut. `今天` shows only ended segments; the current segment is in 进度层. The two stay disjoint (`session_bounds.py:6-7`).
2. **Grouping key (category, local minute).**
   - Facts are keyed by the second of the event that produced them. Boundary events (natural endings, deliveries, arrivals) are keyed at the advance second. A round's own results are keyed at the decision second.
   - Notes are keyed at their request's second.
   - Same-second chains collapse into one entry automatically (103 of 406 decisions, F4).
   - A travel's arrival line (transit segment) and its conclude note (arrival stay) land in the same entry once both segments have ended. This fixes the F1 filing oddity.
3. **Collapse rules:**
   - (a) No 核验 and no 自述 means no entry. This covers starts with no fact yet, rejections, `revise_day_plan` and queries-only rounds.
   - (b) Within a segment, each activity keeps only its **last** note source. A conclude replaces earlier `report_status`/`pause` notes; otherwise the latest report is kept. About 136 report rounds over the 5-day variety run are mostly progress chatter.
   - (c) A memory text that exactly equals its item's summary is dropped. This is the "summary plus paraphrase" duplication.
4. **Day key.** The day is the local date of the entry's minute, with 00:00:00 counted as the new day. This is the planner's, `plan_date`'s and `rejected_today`'s rule (`day_plan_inputs.py:109-115`). The session-end rule (`memory_layer.py:105-111`) and the wake−1 rule (`evidence/memory.py:31-34`) are retired, so the code has **one** day rule.
   - **Overnight sleep 23:30→07:30.** The start leaves no fact. The ending line lands at 07:30 on the waking day as `睡觉：10月9日 23:30–07:30，按计划结束`, because `clock_phrase` dates the earlier day.
   - **Activity between 00:00 and going to sleep.** A decision at 00:10 belongs to the new day. The reflection-to-10-07 defect cannot happen, because nothing is keyed by wake date.
   - **Day entries render clock phrases relative to the end of that day**, so lines show time only, and they strip `START_SUFFIX`. That saves about 700 chars per day compared with the planner's renderings.

### 3.4 Categories and how each is derived

The category set is a closed enum `MEMORY_CATEGORIES = ("饮食","生活","工作")` in `worldmock/state/model/memory.py`. That location is importable by `recording_engine` (F21).

| Source | Category |
|---|---|
| `sleep` (night sleep and office naps) | 生活 |
| `travel` (commute included) | 生活 **[decision]**. Work memory starts at clock-in, which is a 钉钉 entry and therefore 工作. |
| interact at a location | `Content.memory_category` (**new catalog field**) |
| interact with a content app | `App.memory_category` (**new catalog field**) |
| order line, receipt, delivery | goods `meal`/`snack`/`drink` → 饮食; goods listed in `reset_on_goods` of a state consumed by 饮食 content (the kits, `callbacks.jsonl:2`) → 饮食; anything else → 生活 |
| query | 饮食 if any target is a food goods or a food category name (食品/零食/饮料), or the answer has any food row; otherwise 生活 |
| completed errand | the category of its goods |
| control item (summary, memories, **views**) | the category of the named activity (235/235 link exactly, F13) |
| reply body | the category of the first world request's target |

**Catalog change [deviation, minimal-correct].** Existing signals cannot classify `office-eat-lunch`: it has no food effect and sits at the workplace kind. They also do not carry over to the sales, student or homemaker personas (F13).

The change adds `memory_category: Literal["饮食","生活","工作"]`:
- required on `Content` rows with `location_kinds` (20 rows in package 0001);
- required on `App` rows with `role="content"` (32 apps).

Admission is fail-closed in `resource_engine/registration/admission.py`, and `CatalogManifest.format_version` goes from 5 to 6 (`catalog/models.py:51`). WorldMock-Dataset gets a one-shot generator that proposes values from the rules above for human review.

Edge cases:
- `office-eat-lunch`, the `home-dinner-*` series (dishes included) and `restaurant-team-dinner` → 饮食.
- 钉钉 → 工作.
- Cooking-video apps → 生活.
- Other personas: sales visits, library study and similar rows are tagged 工作 by the dataset author. "工作" means "上班或上学的正事".
- Other personas' names appear only inside 自述 and are filed with the activity.

**Naming home-cooked dinners.** `activity_sentence` gets the clause `，用的是<商品名>` for content with `consumes_state` whose state was last reset by a goods receipt. The goods is the latest receipt in `reset_on_goods` at or before the activity started. If there is none (initial `groceries=1`), the clause reads `，用的是家里原有的食材`. The team dinner's state is not reset by goods, so it gets no clause. This shared builder also improves the plan_day input.

---

## 4. Compaction and tiers

**Tier ladder.** All tiers are derived or recorded. **Nothing is ever deleted**, because events are append-only.

| Tier | Facts (deterministic, code) | Paragraph (model) | When |
|---|---|---|---|
| entry | 核验 lines | (its own 自述) | live, derived on every observation |
| day | union of the day's entries' 核验 for the category; ≤800 chars in `近三天`, with overflow shown as `另有 N 项，可用 search_memory 按日期查` | `回顾` ≤200 chars | 00:00 the next day |
| month | `统计`: top 15 counts of completed activity names and received goods names, e.g. `番茄牛肉刀削面 3 次` | `回顾` ≤300 chars | 00:00 on the 1st, after the day digest |
| year | `统计` over the months | `回顾` ≤400 chars | 00:00 on Jan 1 |

**The gap between day 4 and the month boundary (F14).** It is covered without a new tier:
- Every day keeps its entries and its day `回顾`, searchable by date.
- A search on the current, still-uncompacted month returns that month's live `统计` plus each day's `回顾`.
- `可查` tells the model which months and years exist.

A week tier is not built (§11). In the 30-day package run the month tier fires once (September, 3 days) and the year tier never fires. Both share one generic `period_digest(period)` routine and are tested on synthetic calendars, which satisfies the zero-uncovered-interface gate.

**"Last 3 days" [deviation].** This means the last 3 days **with records in that category**. 饮食 and 生活 have records every day, so for them it equals the calendar rule. For 工作 on 10-08 it shows 09-28..09-30 instead of three holiday blanks.

**Who compacts, and where.** An environment-side task, `settle_pending_memory_digests(engine)` in `runtime/lifecycle/runner.py`, built on the `settle_pending_day_plans` precedent (`runner.py:619-670`):
- It runs in `LifeEnv.step` after `engine.advance` and **before** the day plans (`life_env.py:235-243`).
- It also runs on the startup and resume paths, for any day that ended before now and has entries but no digest.
- It does not go through LifeAgent and does not touch the workspace, so it needs no `due_*` forward and no AS2 patch (F21).

**Call.** One call per (period, key, category) **that has entries**. On the rest day 10-11 that means 2 calls; on a workday, 3.
- Client: a `MemoryDigestClient` like `PlanDayClient`.
- Thinking off.
- One strict tool, `submit_memory_digest {text}`.
- Validation retries are bounded by the **existing** `reflection_validation_max_retries`, which is repurposed. Adding no ModelConfig field keeps `RUN_MANIFEST_SCHEMA_VERSION` at 7 (F19).

The prompt is a module constant covered by `code_sha256`, as `PLAN_DAY_PROMPT` is (`day_planning.py:3-4`):

```text
你替一个人写他自己的一段回顾。输入有他的人物卡片、一类记忆（饮食、生活或工作）在一个时段（一天、一个月或一年）里的记录，以及他上一次写的同类回顾。
记录里「核验」是确实发生过的事，「自述」和上一次的「回顾」是他自己当时的想法。
用第一人称写一段中文回顾，不超过输入的「字数上限」：先写这段时间里具体做了什么、吃了什么、做到哪一步，名称照记录原样写；再写感受、看法和还想做的事。
只写记录里有的事，不补造经过、数量、价钱或别人说的话；上一次回顾里没有被新记录推翻的看法和打算保留下来。
只调用一次 submit_memory_digest，参数 text 写这段回顾。
```

**User payload:** `{"人物卡片", "类别", "时段":"2026-10-10（星期六，工作日）", "字数上限":200, "上一次回顾":{时段,回顾}|null, "记录":[entries]}`. For a month or year, `记录` holds `[{日期|月份, 回顾}]` plus `统计`.

**Validation:**
- non-empty and within the limit;
- contains CJK;
- no hex run of 8 or more characters;
- **every catalog goods name, location-content title or app name in the text must appear in the input**. A violation returns `NAME_NOT_IN_RECORDS` with a retry. This is a cheap deterministic guard against invented dishes.

**Recording (F18).**
- Model evidence: `model_request`/`model_response` with `purpose="memory_digest"` and `call_id=memory-digest:{agent}:{period}:{key}:{category}:attempt:{n}`.
- Registration: one `environment_event` with `kind="memory_digest"`, `visibility="system"`, `source_kind="model"`, `caused_by=[response]`, and `data={kind, period, key, category, paragraph, source_event_ids}`. It is committed through an `engine.register_memory_digest` entry modelled on `register_day_plan` (`time_advance.py:211-226`; state unchanged, version+1).
- `validate_environment_event` gains a closed-field branch (`environment_events.py:24-44`).
- Idempotence: a digest event for that (agent, period, key, category) exists. That is event-derived, so no new `AgentState` field and the world schema stays at 15.
- Resume, replay, branch and export only read these events.

**What this replaces:**
- Bedtime reflection (`due_reflection`, `_wake_reflection`, `life_reflection.py`, the `reflection` write point): the day 回顾 is the daily paragraph.
- Threshold consolidation (31 calls per 5 days, no reader, `life_memory.py:79-92,240-262`): removed.
- `MEMORY.md` stays valid for `validate_existing`. Its 人物自述 section is rendered deterministically from the latest `回顾` in the frozen 记忆层, so the viewer and `memory_md_identity_residue` keep a meaningful source.
- The AS2 `episodes.jsonl` keeps receiving conclude memories as an archive, but no tool exposes it.

**Cost per simulated day at the observed peak prices** (all calls ran at peak; miss $0.30/M, hit $0.006/M, output $1.20/M):
- Day digests: about 6.2k input plus 0.45k output tokens ≈ **$0.0024 per day**.
- Month digests: about $0.0035 per month.
- Retired: consolidation ($0.0037 per day) and reflection (about $0.001 per day).
- Net ≈ −$0.002 per day.

---

## 5. Retrieval: `search_memory`

**Schema** (`SearchMemoryRequest` in `state/interaction/requests.py`, added to `REQUEST_MODELS` as a read query like `recall_task_summary` at `:568-575`; purpose-only, with no field descriptions):

```json
{"type":"function","function":{"name":"search_memory",
 "description":"按类别和时间查自己更早的记忆：某一天每个时刻的条目和当天回顾，或某个月、某一年的回顾。",
 "parameters":{"type":"object","properties":{
   "category":{"type":"string","enum":["饮食","生活","工作"]},
   "time":{"type":"string","pattern":"^\\d{4}(-\\d{2}(-\\d{2})?)?$"}},
  "required":["category","time"],"additionalProperties":false},"strict":true}}
```

**Semantics.** The finer the time key, the finer the result.

| `time` | Result |
|---|---|
| `YYYY-MM-DD` | `{"类别","日期","星期","安排","回顾","条目":[entries]}`. This is the whole day, including entries trimmed from `今天`; today returns ended segments only. |
| `YYYY-MM` | `{"类别","月份","回顾"\|null,"统计":[…],"日子":[{"日期","回顾"}]}` |
| `YYYY` | `{"类别","年份","回顾"\|null,"统计":[…],"月份":[{"月份","回顾"}]}` |

Rules:
- A future time or an invalid date returns `MEMORY_TIME_INVALID` in the existing `read_query` error shape (`life_env.py:172-180`).
- A time before the earliest record returns empty lists.
- A result is capped at 6,000 chars. The trim drops 自述 from the earliest entries first, then the earliest entries, with `"省略":"另有 N 条没列"`.
- **No keyword parameter [decision]**: it is not in the owner's model. Name lookup stays with `recall_task_summary`.

**Read-only guarantee.** LifeAgent routes `search_memory` to the existing `LifeEnv.read_query` (`life_env.py:156-180`), which widens its accepted tool set. There is no new forward.
- The engine answers from events with seq ≤ the frozen observation's event seq, using the frozen catalog.
- It writes no events and never advances time.
- It is deterministic under replay.

**Relation to the other memory tools:**
- `recall_task_summary` is unchanged: by-name, current-run tasks.
- The three AS2 tools `memory_recent/search/time_range` are **removed** from both tool surfaces (`life_agent.py:169-195,665-683`). In the variety run they made 2 calls, both returning `[]` (F10).
- The tool count goes from 10 to **8**.

**Turn budget.**
- Each search costs one turn, a cached history turn at about $0.0021 (verifier T4).
- The last-turn rule removes query tools (`life_agent.py:397-404`).
- 16 turns are plenty.

---

## 6. System-prompt teaching (exact text)

`LIFE_SYSTEM_LAYER.rules` (`life_prompt.py:24-98`) goes from 20 to **21** rules: rules[0], [12] and [19] are replaced and a new rule[1] is inserted.

**rules[0]** (replaces `:25-27`):

```text
工具层写此刻的对象、动作、条件与代价；人设层写你的作息与习惯；记忆层按饮食、生活、工作三类写此前的经历；待办层写暂停活动、个人事务、在途订单和今日计划；环境层写身体感觉、位置、天气和工作安排；进度层写今天这段停留或路程中已经发生的事，跨过零点的停留或路程从零点记起。
```

**rules[1]** (new):

```text
记忆层的今天写今天已经结束的各段停留和路程，每类按时刻各一条：核验是环境确认的经过，自述是你当时写下的总结、想记住的事、看法和正文；当前这一段的经过只在进度层，这一段结束或过了零点才记进今天。近三天写每类最近三个有记录的日子，每天一条，核验是那天实际发生的事，回顾是零点时按那天的记录替你写下的回顾。更早的日子、整月和整年不在请求里，可查写出能查到的最早日期和已整理成回顾的月份、年份；要用时调用 search_memory，写类别和时间：写日期得到那天每个时刻的条目和当天回顾，写月份得到那个月的回顾、统计和每天的回顾，写年份得到那一年的回顾、统计和每月的回顾。想知道以前做过什么、吃过什么、一件工作做到哪一步、某个看法从哪来时，先看记忆层，不够再查；按名称回看一项任务或活动用 recall_task_summary。记忆只供参考，怎么安排照人设层的习惯、今日计划和你此刻的想法。
```

**rules[13]** (was [12], `:75-78`):

```text
世界请求登记后由环境结算。进度层结果和记忆层的核验是核验事实；自己写的 summary、self_note、看法以及记忆层的自述和回顾只是自己的想法，不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。
```

**rules[20]** (was [19], `:96-97`):

```text
正文可以留空；写了作为本次调用的自述，这段停留或路程里留在进度层，之后记进今天的记忆。思考只在同一时刻连续决策之间保留，时间推进后丢弃。
```

`READONLY_SYSTEM_LAYER.rules` (`:105-110`):

```text
请求由人设层、记忆层与外部问题组成。
记忆层的核验是环境核验的事实，自述与回顾是你自己写下的，未经核验。
只依据人设层与记忆层回答，不能改变世界，也不写入记忆。
回答完成后单独调用 finish。
```

In `TOOL_DESCRIPTIONS`, the three `memory_*` entries are removed and `search_memory` is added (text above).

**Teaching without forcing.**
- The rule says what each part is, when looking helps (先看…不够再查), and that memory is reference only (只供参考…照人设层的习惯…此刻的想法).
- It never says to vary food. The card line 「每顿饭都愿意换着尝不同的菜」 now has three days of named meals to act on. Behaviour stays card-driven, which keeps the result valid as a realism test.

---

## 7. Persona-layer preferences and relationship views

**Fate [owner-literal]:** they leave 人设层.
- `PERSONA_LAYER_SCHEMA` loses `preferences` and `relationships[].view` (`prompt_schemas.py:146-148`; `life_persona.py:29-54`).
- Views keep their request schema. They flow into the 自述 of today's entry (`看法（about）：…`) under the activity's category, into the day 回顾 (the digest prompt carries forward still-valid views), and into search.
- `persona_views` (`memory_layer.py:127-144`) is still computed as **private** observation evidence for `known_persons` name binding. It is no longer rendered.

Effects:
- Removes about 1.8k chars per prompt (median 人设层 3.0k → 1.19k).
- Removes the near-duplicate subjects (美式咖啡/冰美式咖啡).
- Removes the undated 「番茄牛肉刀削面…以后想换口味还会再点」 that kept pulling lunch back.

**Risk: preference amnesia after 3 days.** Paragraph chaining (each 回顾 sees the previous one) and the month 回顾 mitigate it. The pilot measures it (§10). **Fallback (B):** a per-category `看法` list inside 记忆层, at most 8 per category, keyed by subject, deduped, dated, and kept for 30 days.

---

## 8. Interactions

- **Same-second continuation (F4).** 记忆层 now changes only at a segment end (a session change, which breaks the chain, `continuation.py:3-5`) or at midnight (a time change, which also breaks it). The invariant at `life_continuation.py:8` therefore holds without new deltas. In the chain `changes`:
  - the persona preference/relationship update items are removed;
  - **`在途订单` added/removed is added**, because a purchase inside a chain changes 待办层.
- **Read-only snapshot.** `write_prompt_snapshot` (`life_agent.py:487`) stores the new 记忆层 shape. The read-only tools shrink to `finish` only, since the snapshot already holds today plus three days. That path has no production caller (F22).
- **Midnight planner (F12).** The order is: advance, then digests, then `plan_day`, then `decide_midnight_rhythm`. `plan_day` inputs are unchanged in phases 1–2; it still reads the raw review (`day_plan_inputs.py:83-106`). Phase 3 option: replace `收尾记录` with yesterday's three 回顾 to shrink its input. Both `在途订单` lists use one shared helper.
- **Capacity condensation.** Unchanged: only 进度层 is condensed. 记忆层's hard cap of 15.2k chars (about 6.4k tokens) is far below 85% of the 262k context.
- **`recall_task_summary`.** `noted_label` (`memory_layer.py:147-152`) and `_remembered` scan `今天` 自述 labels instead of `self_notes`. The "已在记忆层" deduplication is kept.
- **Bedtime 00:00–00:20.** After the midnight cut, a decision at 00:05 sees the previous evening only as day entries (capped) plus carried active interactions. This is accepted; §11 lists it.

---

## 9. Contract and gate change list

The authority is `docs/design/*.md`, not CLAUDE.md, which is stale (F17). Each change lands in one commit with its design doc and behaviour tests (`docs/INDEX.md:18`).

**Code:**
- `state/model/memory.py`: `MEMORY_CATEGORIES`.
- `state/model/exchange.py`: `MEMORY_DIGEST_PURPOSE`.
- `resource_engine/catalog/models.py` (`App`, `Content`, format 6) and `registration/admission.py`.
- `observation/layers/memory_layer.py`: rewritten and split into `memory_entries.py` (categories, entries, day key, collapse rules), `memory_layer.py` (layout, budgets) and `memory_search.py` (answers), to stay within the style gate.
- `recording_engine/projections/activities/session_bounds.py`: segment lower bound with the midnight cut.
- `progress_layer.py`: uses the segment bound; the carry rule (`:158-171`) is unchanged.
- `todo_layer.py`: `在途订单`.
- `wording/settled_text.py`: kit clause and a `now` override.
- `prompt_schemas.py`: 人设, 记忆, 待办 schemas and `USER_LAYER_NAMES`.
- `life_prompt.py`: rules, read-only rules, descriptions, `life_user_layers`, `empty_memory_layer`.
- `life_persona.py`, `life_continuation.py`.
- `custom/agents/life_agent.py`:
  - remove `_MEMORY_*`, `_dispatch_strict_memory`, `_wake_reflection` and the `due_reflection` call;
  - route `search_memory` through `read_query`;
  - read-only tools become `finish` only.
- `custom/envs/life_env.py`:
  - `read_query` accepts `search_memory`;
  - `due_reflection` is removed;
  - digests run in `step`, `checkpoint` and init.
- `simulation_engine/engine.py` / `actions/dispatcher.py`: `read_memory`.
- New `simulation_engine/evidence/memory_digest.py`: inputs, prompt, tool, validation, draft.
- `scheduling/clock/time_advance.py`: `register_memory_digest`.
- `runtime/lifecycle/runner.py`: `settle_pending_memory_digests`.
- `agents/memory/life_memory.py`: drop consolidation and wake reflection; `life_reflection.py` deleted.
- `simulation_engine/evidence/memory.py` (`due_reflection`) and `evidence/model.py` (reflection registration) deleted.
- `recording_engine/events/memory_evidence.py:14`: `MEMORY_WRITE_POINTS = ("activity_conclusion",)`.
- `environment_events.py`: `memory_digest` branch.
- `reports/quality.py`:
  - `CALL_CLASSES = (fresh_decision_turn, turn_with_history, truncation_retry, plan_day, memory_digest)`;
  - schema 9 → 10 (Phase 2);
  - digest metrics.
- `scripts/compare_runs.py` paths; `tools/run-viewer-context.js`; `tools/analysis/behavior/metrics.py`.

**Event decisions:**
- No new event type; the enum of 14 is unchanged (`events/models.py:110-114`).
- `memory_updated` operations and the AS2 `MemoryUpdateEvidence` Literal are untouched; only the `reflection` write point retires.
- Digests are `environment_event` records with `kind="memory_digest"`.

**Fingerprints:**
- The prompt sha changes (rules and LAYER_SCHEMAS).
- The tool sha changes (8 tools).
- `code_sha256` changes.
- **Old runs cannot be resumed under new code**, as with any prompt change.

**Versions:**

| Item | Change |
|---|---|
| run manifest | stays 7 (no new ModelConfig field) |
| world | stays 15 (no `AgentState` field) |
| checkpoint | stays 1 |
| export | stays 8 (artifact set unchanged) |
| catalog format | 5 → 6 |
| quality | 9 → 10 |

**LabelKit-DataSet:** no pin breaks, since event `data` is a free object there. Notify them about the new observation `memory_layer` shape and the `memory_digest` events.

**AS2 patch:** none.

**Docs:**
- `06-agent.md`: the 提示与工具 table (十个 → 八个 tools); the 进度、待办与记忆 section rewritten; the 就寝反思 section removed; 零点回顾 added.
- `04-simulation-engine.md`: midnight order.
- `05-recording-engine.md`: digest events, quality 10, call classes.
- `10-runtime.md:79-91`: eight tools; the Life channel is `read_query` only.
- `03-resource-engine.md` and `09-io-formats.md`: catalog field and format 6.
- `08-configuration.md`: the retry field's new meaning.

**Tests:**
- `test_system_layer_text.py`: 21 rules verbatim.
- `test_seven_layer_request.py`: order.
- `test_tool_description_references.py`.
- `test_batch_shapes.py`, `test_life_agent_protocol.py`, `test_read_query_runtime.py`.
- `test_same_second_continuation.py`.
- `test_observation_parts.py`, `test_post_run_view.py`, `test_compare_runs.py`.
- New unit tests: category table (all 20 rows and 32 apps); day key (00:00:00, overnight sleep, 00:10 decision); collapse rules; budgets and trims; search grammar, cutoff and no-write; digest idempotence across crash and resume; month/year on synthetic calendars; the name-guard validator.

**Gates:**
- day-run baselines: two-candidate refreeze;
- `viewer-context.json`: re-recorded with a real host;
- coverage: ≥85% branch, zero uncovered interfaces;
- style gate.

---

## 10. Phased rollout

**Phase 1: categorized deterministic memory (no new model calls).**
- Scope:
  - catalog field (packages 0001–0005 in one dataset change);
  - entries, `近三天` with 核验 only (`回顾:null`), `可查`, `search_memory` (with `统计`);
  - segment cut, `在途订单`, layer reorder, preferences removed;
  - AS2 memory tools removed; reflection and consolidation retired (their call-class keys stay at zero, so no quality bump);
  - kit clause; the 21 rules.
- This phase alone fixes the pilot failure mode: past meals and named dinners become visible.
- Acceptance:
  - unit and integration tests above;
  - 记忆层 identical within every same-second chain in a recorded day-run;
  - no budget-overflow warnings;
  - export authenticates.

**Phase 2: 回顾 at day, month and year.**
- Scope: the digest task, events, quality 10, `MEMORY.md` render.
- Acceptance:
  - exactly one digest per (agent, period, key, category) in a crash-at-midnight resume test;
  - a branch inherits digests and never regenerates them;
  - the 30-day run produces a September month digest.

**Phase 3: optional cleanup.**
- `plan_day` reads 回顾;
- preference fallback (B) if needed;
- week tier if asked for;
- the read-only path decision.

**Real-model pilots.** Prompt changes need real runs (F22). Each pilot reruns the 5-day food pilot: variety config, 10-08 → 10-13, same seed, ×2 for variance. Phase 2 adds one 30-day package run.

| Metric | Baseline (V) | Target / check |
|---|---|---|
| Workday lunches repeating a dish from the previous 3 days | 刀削面 3 of 4 | report it; expected to fall, nothing forced |
| Kit-cooked dinners with a named kit | 0% | 100% where a receipt exists |
| Fresh decisions whose reasoning cites a `近三天`/search item | ~0 | report it |
| `search_memory` calls per day, empty-result rate | 0.4 per day, 100% empty | report; empty < 30% |
| Max turns per decision | 2 | ≤4 |
| Layer chars median/p90/max; trim counts | 记忆层 4.4k/6.9k/7.8k | within §2 budgets |
| Fresh-call cache hit | 0.336 | ≥0.5 |
| Cost per simulated day | $0.337 | ≤ $0.30 |
| Digest retries, `NAME_NOT_IN_RECORDS` count (P2) | — | ≤1 retry per day |
| Views re-mentioned in 回顾 within 3 days (P2) | — | report (amnesia check) |
| Guards: decisions per day, rejections, protocol errors | 69–89 / 6 / 7 | no regression |

**Projected cost per day.** These are estimates at 0.42 tokens/char, to be measured:
- The reorder caches about 4.6k more tokens per fresh call: about −$0.05 to −$0.06.
- The 进度层 cut: about −$0.028.
- Digests minus retired calls: about −$0.002.
- Searches: +$0.006 to +$0.017.
- Total ≈ $0.26–0.29 per day.

**Pilot spend:**
- P1: about $3.
- P2: about $3, plus about $9 for the 30-day run.
- Total about $15.

---

## 11. Risks and decisions for the owner

1. **Catalog `memory_category` field (fail-closed) versus code rules.** Recommend the catalog field. It is the only correct handling of `office-eat-lunch` and the other personas, and the cost is about 52 tagged rows per package.
2. **Commute category.** Recommend 生活; clock-in and clock-out carry work timing.
3. **Do `report_status` rounds produce entries?** Recommend yes, but only the latest per activity per segment, and a conclude replaces them.
4. **Preferences out of 人设层.** Recommend yes, together with the amnesia metric, and fallback (B) if fewer than half of views survive 3 days.
5. **Environment-side digest (plan_day precedent) versus agent-side.** Recommend environment-side: no AS2 patch, no new forward, and event-recorded text (memory_updated stores only hashes, F18).
6. **One call per category versus one call for all three.** Recommend per category. It matches "each compacts on its own", isolates the categories, and costs about $0.0024 per day.
7. **Retire bedtime reflection and threshold consolidation.** Recommend yes. Reflection's role moves to 回顾; consolidation has no reader.
8. **Turn budget 32.** Recommend keeping 16; the observed maximum is 3.
9. **Week tier and search keyword.** Recommend neither for now. Date and month search covers the gap; revisit when runs exceed 30 days.
10. **Midnight cut loses evening detail for decisions between 00:00 and 00:20.** Recommend accepting it: one consistent day key outweighs this.
11. **Memory-layer growth (~2.5× chars).** Mostly cached, with hard caps. The risk is attention dilution; the pilot watches decision quality guards.
12. **Hallucination in 回顾.** Mitigated by the 核验 lines printed beside it, by the name-guard validator, and by the 只供参考 rule.
13. **Final day's digest at run end.** Recommend skipping it: it has no consumer.
14. **The 工作 label for students and the homemaker.** Recommend keeping it, documented as "上班或上学的正事"; the dataset decides tags.
15. **Breaking change.** Pre-change runs cannot be resumed (fingerprints). Recommend shipping Phase 1 and Phase 2 as separate identities, each with its own pilot.

Read-only scratch helpers I used (no repo or dataset files changed):
- `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/owner_literal/cut.py` simulates the midnight cut and the persona size without preferences.
- `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/owner_literal/planfacts.py` measures per-category daily fact sizes: 饮食 455–720, 工作 0–591 and 生活 959–1,613 chars per day; conclusion text 3.4–5.6k per day.