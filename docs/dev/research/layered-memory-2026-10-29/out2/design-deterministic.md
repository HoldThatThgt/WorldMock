# Memory redesign proposal: deterministic first

**Angle.** The model never writes or rewrites a verified fact. Every fact tier is rebuilt from committed events at read time, the same way `memory_view` works today (`simulation_engine/observation/layers/memory_layer.py:99-113`). The model writes only one subjective call per simulated day, and that call reuses the bedtime-reflection seam: `due_reflection`, then `memory_updated` with `reflection.date`. The design needs no new event kind, call class, AS2 patch or ModelConfig field.

**One-paragraph summary.** The 记忆层 is rebuilt into three tiers: 前三天, then 看法, then 今天. Each tier is split into 饮食, 工作 and 生活, and the category comes from deterministic rules over activity kind, goods, content and duty. Past days become fixed fact digests, and the same code produces day, week, month and year rollups that `search_memory` returns. The daily reflection reads the whole calendar day, grouped by category, and its insights are labelled by the categories of the inputs they cite. Threshold consolidation, which nothing reads, is removed. Layers are reordered so that the stable memory sits inside the cached prefix. Open orders move to 待办层. Persona-layer views move into memory-longterm, with a date, a dedupe step and a budget.

---

## 1. Units and glossary decisions

| Owner term | Code | Decision |
|---|---|---|
| session | `SessionState` stay or transit (`state/world/agent.py:57-95`) | Keep the code meaning. A transit is also a session. Memory is **never keyed by session**. The one place a session matters is phases 1–3: until the current session ends, its decisions stay in 进度层, as they do today (`progress_layer.py:62-88`). |
| round | **decision** (`decision_id`). It ends at the first registered world request, even when that request is rejected (`custom/agents/life_agent.py:563-583`): start, or a start pair, `report_status`, `pause_activity`, `conclude_activity`, `revise_day_plan`. | Model-facing text says 决策. Never use "round", because it collides with AS2 `round_id` and `same_moment_rounds`. **Deviation:** the owner's narrower "start/pause/conclude" leaves out a third of decisions (136 `report_status` in V). They still end a decision. Their *summaries* count as progress, not memory (§3). |
| turn | one model call, `…:model:NNNN` | Keep **`max_react_turns = 16`** (`configuration/inputs/models.py:223`). In the evidence 402 of 406 decisions used one turn, and the maximum was 2 in V and 3 in B. The budget is frozen into identity, so raising it to 32 buys nothing and blocks resume. New guard: at most **3 `search_memory` calls per decision**. A fourth call gets `ok=False` feedback ("本次决策已查过三次记忆，请据现有信息决定"), so the model cannot loop until it exhausts its turns, which would fail the run (`life_agent.py:388-395`). |
| memory entry | none today | **One entry per (decision, category) that has at least one verified fact line or one own note in that category.** Decisions in the same second merge into one entry. Detail in §3. |

---

## 2. Target prompt structure

### 2.1 Order

```
system | 人设层 | 记忆层{前三天 → 看法 → 今天} | 待办层 | 工具层 | 环境层 | 进度层
```

Today's order is 工具, 人设, 记忆, 待办, 环境, 进度 (`agents/prompt/life_prompt.py:144-156`). Fresh turns therefore cache only the system message and tools, about 5.5k tokens (F15), because 工具层 comes first and changes on almost every request.

New order, by how often each part changes:
- **人设层** becomes static after phase 3, once views move out.
- **前三天** changes twice a day: at 00:00 and when the wake reflection lands.
- **看法** is frozen at midnight (phase 3).
- **今天** changes only when a session ends in phases 1–3. The current 记忆层 is byte-identical in 384 of 405 consecutive observations, and this keeps that stability. In phase 4 今天 becomes append-only.
- **待办层** sits after 今天 because it is small and changes on errands and orders.
- **工具层, 环境层, 进度层** stay last, because they change on every request.

Appending to the end of the 今天 list keeps the earlier prefix byte-stable, so even phase 4's per-decision growth caches everything up to the new entry.

### 2.2 Layers, contents and budgets (compact JSON characters)

| Part | Holds | Today (V median / max) | Cap |
|---|---|---|---|
| 系统层 | rules | 3,377 | ≈4,300 (2 new rules) |
| tools | native tools | 11,221 | ≈10,600 (phase 2: 3 memory tools replaced by 1) |
| 人设层 | card only, from phase 3 | 3,026 / 5,304 | ≤1,400 from phase 3, static |
| 记忆层.前三天 | 3 categories × 3 days; each cell has ≤6 fact lines (≤300 chars) and ≤2 感想 (≤120 chars each) | 0–1,851 (reflections only) | ≤3,600 |
| 记忆层.看法 | views up to yesterday, by category and person (phase 3) | (lived in 人设层) | ≤2,300 |
| 记忆层.今天 | (decision, category) entries for today's ended sessions; all closed decisions in phase 4 | 3,883 / 7,775 | ≤6,000 |
| 待办层 | paused activities, errands, **未到的订单**, today's plan | 865 / 942 | ≤1,500 |
| 工具层 / 环境层 | unchanged | 8.6k / 0.4k | unchanged |
| 进度层 | unchanged in phases 1–3; in-flight items only in phase 4 | 14,663 / 42,812 | phase 4 median target ≤7k |

The whole 记忆层 is capped at ≤12k, up from 8k today, but it no longer competes with itself because each tier has its own cap. Capacity condensation still never touches it (`prompt_capacity.py:1-7`).

### 2.3 The owner's 2x2

| Quadrant | Where it lives | Items |
|---|---|---|
| **memory-today** | 记忆层.今天 | verified fact lines plus 自述 (conclude and pause summaries, plain memories, reply bodies) from today's closed decisions |
| **memory-longterm** | 记忆层.前三天 (in the prompt), 记忆层.看法 (in the prompt, phase 3), and day, week, month and year rollups (through `search_memory`) | daily digests, bedtime 感想, keyed views |
| **progress-today** | 进度层 (current session), 工具层 targets, `rejected_today` (`progress_layer.py:316-347`) | running or awaiting-conclusion activities, `report_status` summaries, **query answers** (session-scoped, as now), **reply bodies until the session ends**, deliveries, pushes, reminders, rejections |
| **progress-longterm** | 待办层 | paused activities (no expiry, as now), errands (≤8), **open orders placed before the current session** (moved from `earlier_days.settled`, `memory_layer.py:225-257`; at most 6 rows, each the existing purchase sentence plus a status), **today's plan** (it is dated today, but it stays in 待办层 because it is an intention, and moving it would churn the schema) |

Orders placed in the current session stay visible through their 进度层 records. 未到的订单 lists only orders from earlier sessions, so it cannot change inside a same-second chain (§8).

---

## 3. Memory entry model

### 3.1 Shape (model-facing, exactly as shipped)

```json
{"layer":"记忆层",
 "前三天":{
  "饮食":[{"日期":"2026-10-11","事实":["09:40 收到 豆浆油条套餐 1 份（12 元）","12:30 收到 黄焖鸡米饭 1 份（26 元）","19:20 自己做的晚饭吃完：土豆烧牛肉配清炒菌菇（10月10日 收到的一人份食材包）"],"感想":["周末自己做饭吃得踏实，土豆烧牛肉比外卖香。"]}],
  "工作":[{"日期":"2026-10-10","事实":["上班 08:55–17:40，在练秋湖研发中心","做了：功能开发与自测 4 小时、代码评审与缺陷修复 1 小时 30 分、需求评审会议 1 小时","通勤：驾车 30 分钟、32 分钟"],"感想":["和王磊互评代码对事不对人，我挺踏实。"]}],
  "生活":[{"日期":"2026-10-11","事实":["睡觉 00:10–08:20（8 小时 10 分）","家务：洗衣服、打扫卫生","用 app：抖音 45 分钟、哔哩哔哩 1 小时 30 分","外出：彩虹桥往返步行 50 分钟"],"感想":[]}]},
 "看法":{"饮食":[{"对象":"番茄牛肉刀削面","看法":"汤头开胃、面顶饱，一个人吃午饭份量正合适，换口味时还想再点。","记于":"2026-10-12"}],"工作":[],"生活":[],"人":[{"对象":"王磊","看法":"互评代码直接、对事不对人。","记于":"2026-10-09"}]},
 "今天":[
  {"时刻":"08:36","类别":"饮食","活动":["购买美式咖啡"],"事实":["08:36 买了美式咖啡 1 份，付 18.3 元，预计 08:50 送到练秋湖研发中心"],"自述":["到公司打完上班卡后照习惯点了冰美式，比上班时间早，先不急着开电脑。"]},
  {"时刻":"12:28","类别":"饮食","活动":["吃午饭"],"事实":["进行吃午饭（10月12日 12:13 起）：12:13–12:28，按计划结束，《吃午饭》"],"自述":["……"]}]}
```

Labels:
- **事实** is environment-rendered, using the existing sentence functions in `settled_text.py` plus the digest templates in §4.2.
- **自述** is what the persona wrote at the time.
- **感想** is that day's bedtime reflection.
- **看法** is a keyed view.

Only 事实 is verified. Rule text is in §6.

Key language: these Chinese keys are what ships. The other layers keep their English keys, and an English mapping is a one-line constant change if the owner prefers it (decision D1).

### 3.2 Which decisions produce entries

Notation: decisions dᵢ, each with an observation at event sequence number sᵢ and time tᵢ.

**Attributing fact lines.** Each settled line has a source event e.
- If e is the `action_result` of dᵢ's own request, the line goes to dᵢ. Examples: a paid purchase, an early conclude.
- Otherwise it goes to the first decision whose observation sequence is at least e.seq. In other words, the decision that first observed it. Examples: a natural activity end, a delivery, a wake from sleep.

Effects:
- The ending sentence of an activity and the conclude notes for it land in the **same** entry.
- A night's sleep belongs to the wake decision.
- No line is counted twice.

**Notes from dᵢ.**
- Successful `pause_activity` and `conclude_activity` items: the summary and the first plain memory. At most 2 per activity, each cut to ≤120 chars with "…". The full text stays reachable through search. Views are excluded in phase 1, because they are still shown in 人设层; from phase 3 they appear here prefixed 「看法·对象：」.
- The decision's non-empty reply body (`action_requested.data.self_note`, `decision_coordinator.py:552-563`).

**Excluded:**
- `report_status` summaries. They are progress: they live in 进度层 and in `recall_task_summary` note_history.
- Query answers.
- Rejections, which already live in `rejected_today`.

**Collapse rules:**
1. A (decision, category) pair with no fact lines and no notes produces no entry. Most bare start decisions produce nothing.
2. Decisions in the same second (103 of 406 in V) merge per category. Their 时刻 is the same.
3. When a purchase sentence exists for an order, the redundant ending line of the purchase activity is dropped, e.g. 「购买美式咖啡（…）：08:34–08:36，按计划结束」.
4. A memory whose text equals the summary is dropped.

**Volume** (V): about 40–48 entries per day, holding about 1.9k fact chars plus 3.5–4k note chars.

**今天 trimming** above 6,000 chars: drop the 自述 of the oldest entries first. If that is not enough, fold the oldest entries into one 「今天早些时候」 entry per category, rendered with the day-digest template. The trim only runs when a session ends, so the cached prefix rarely moves.

### 3.3 Categories (`MEMORY_CATEGORIES = ("饮食","工作","生活")`, in `worldmock/state/model/memory.py`)

This follows the precedent of `SUBJECTIVE_TYPES` (`state/model/memory.py:23-24`) and the rule that shared enums live in `worldmock.state` (F21). Sleep stays inside 生活. It used to justify its own category only because it crosses midnight, and dating by observation removes that problem (decision D3).

Derivation is applied to each activity or order line; the first matching rule wins.

| # | Rule | Signal (existing data only) |
|---|---|---|
| 1 | `kind=sleep` → 生活 | `kinds.py:10-17` |
| 2 | purchase or query line: the goods is meal/snack/drink, **or** it is in `reset_on_goods` of a state consumed by content with `completion_effects.food` (`callbacks.jsonl:2`; `catalog/models.py:199-205,241`) → 饮食; otherwise 生活 | goods category, callback declaration |
| 3 | location interact whose content has a food effect or consumes such a state (whole dinner series including dishes; team dinner) → 饮食 | `consumes_state`, `completion_effects.food` |
| 4 | travel between home and the day's shift place on a duty day → 工作 (commute) | `work_schedule.day_arrangement` (`configuration/persona/work_schedule.py:68-83`) |
| 5 | location interact at the shift place outside a break, **or** at any non-home place while on duty → 工作 | `duty_status`, `break_spans` (`work_schedule.py:49-66`) |
| 6 | app content whose `prerequisite.location_kinds` includes the shift place's kind (DingTalk) → 工作; any other app (media, phone) → 生活 | `catalog/models.py:182-186` |
| 7 | anything else → 生活 | — |

Notes and views take the category of the activity they name; 235 of 235 notes link to exactly one activity (F13). Relationship views go under 看法.人.

**Edge cases:**
- **Office lunch** (`office-eat-lunch`) falls in a break, so it lands in 生活. That is a known misfile, but the lunch *dish* is still 饮食 through its receipt. Phase 5 adds the catalog fix.
- **Kits** are 饮食 by rule 2.
- **Commute** is 工作 (decision D4).
- **Coffee** is 饮食, because drink goods count.
- **Naps** are 生活.
- **Cooking videos** are 生活, because they are app media.
- **Team dinner** is 饮食.
- **Calls and other personas' interactions** are 生活; views about people go to 人.
- **Other personas:**
  - Sales (work away from the workplace kind) is covered by "on duty and not at home".
  - Students are covered if their schedule is declared as duty.
  - The homemaker has no schedule, so there are no 工作 entries; housework is 生活.

  Rule 5 is what makes this persona-agnostic, unlike the "workplace kind" rule from F13.

### 3.4 Day key

**An entry's date is the local date of its decision's observation time.** This replaces the session-end rule (`memory_layer.py:105-107`), which is retired. It lines up with the planner's calendar-midnight window, `plan_date` and `rejected_today`, and with the reflection's "wake date − 1".

- **Overnight stays** split naturally by the date of each decision.
- **Sleep crossing midnight** is dated by the wake decision: "a day's sleep is the sleep that ended that day".
- **Activity between 00:00 and going to sleep** (baseline had decisions at 00:08 and 00:15) belongs to the new day, and that day's reflection covers it.
- **An activity ending exactly at 00:00** is observed by the 00:00 decision, so it belongs to the new day. The planner counts it in the old day. That one-second difference is documented.

Each 事实 line keeps its own clock text.

---

## 4. Compaction and tiers

### 4.1 Tier ladder

| Tier | Unit | Built by | When | Shown |
|---|---|---|---|---|
| 今天 | (decision, category) | code, from events | every observation | prompt |
| 前三天 | (day, category): digest plus 感想 | code (digest) + the daily reflection (感想) | digest at 00:00; 感想 at the wake reflection | prompt |
| 日 | every entry of that day, untruncated, plus digest and 感想 | code | on request | `search_memory(time=date)` |
| 周 (ISO week) | 7 day digests plus a rollup | code | on request | search |
| 月 / 年 | weekly / monthly rollups | code | on request | search |

- **Day 4 to the month boundary** is covered by the day and week tiers, both searchable.
- **Month and year** fire once and never on the 30-day calendar (F14). They are the same bucketed rollup function with another bucket key, so they cost nothing to keep.
- **工作 uses "the last 3 dates that have a 工作 fact"**, not the last 3 calendar days. On 10-08 that shows 09-28..09-30 instead of three holidays. 饮食 and 生活 use calendar D-1..D-3 (deviation D7).
- **Cold start:** 前三天 is empty on the first day of a run. A branch inherits its parent's events and recomputes the tiers.

### 4.2 Deterministic digests (no model involved)

Each (day, category) cell has at most 6 lines and ≤300 chars. When a cell overflows, it keeps lines in priority order and adds 「另有 N 条，见 search_memory」.

- **饮食.** One line per food receipt (goods, count, price, from the `goods_received` receipt at `commerce/fulfillment/receipt.py:104`). One line per completed food activity. One line per kit order. Priority: meals, then kits, then drinks, then snacks.
  - **Naming home-cooked dinners:** the dinner series consumes `groceries`. A `goods_received` receipt of a reset goods resets that state, with the receipt as its cause (`scheduling/callbacks/callbacks.py:259-261`). So the dish is the most recent kit received before the dinner, not used by an earlier dinner. It renders as 「自己做的晚饭吃完：<套餐名>（<日期> 收到的一人份食材包）」, which shows the kit name without string surgery. With no kit on record it renders 「用家里已有的食材」 (this is the 10-08 case).
- **工作.** The duty arrangement and the actual first and last work times, time per work content, commute durations, and overtime.
- **生活.** Sleep span and length, naps, chores, minutes per app, outings, calls.
- **Week, month and year rollups.** Counts and totals per category, for example:
  - 「收到的饭菜 9 份：番茄牛肉刀削面 3、黄焖鸡米饭 2…」
  - 「自己做晚饭 4 次：…」
  - 「上班 5 天，加班 1 天」
  - 「平均每晚睡 7 小时 40 分」

Past days are immutable once their last decision closes, so their digests are memoized per (agent, date).

### 4.3 The one daily model call: retargeted bedtime reflection

**Trigger.** Unchanged: `due_reflection` fires at the first completed sleep of the local day (`simulation_engine/evidence/memory.py:13-34`), and the registration is unchanged (`evidence/model.py:173-184`).

**Input** is the whole calendar day D-1, grouped by category. `engine.due_reflection` (`engine.py:93-97`) now returns `{date, sleep_activity_id, day_input}`. This is the same read-only forward through `LifeEnv.due_reflection` (`custom/envs/life_env.py:147-149`) and `read_query.py:31-49`, so no new channel method is needed.
- The registration still compares only the 2-key pure `due_reflection` value (`quality.py` `_valid_update_binding` pins those keys).
- `day_input` holds the day's full fact lines, ids `f1…`, and all own notes, ids `n1…`: conclude summaries, memories and views, pause summaries, reply bodies. About 7–8k chars.
- The model call is skipped when the day has no own notes. This also fixes the B defect where a reflection was filed under 10-07 from a 10-08 00:10 note: 10-07 now has no rows.

**User payload** (as sent):

```json
{"日期":"2026-10-11","饮食":{"事实":[{"编号":"f1","内容":"12:30 收到 黄焖鸡米饭 1 份（26 元）"}],"自述":[{"编号":"n1","内容":"……"}]},"工作":{"事实":[],"自述":[]},"生活":{"事实":[…],"自述":[…]}}
```

**System prompt** (replaces the English `REFLECTION_PROMPT`, `agents/memory/life_reflection.py:21-37`):

> 你在回顾一个模拟人物某一天的生活。输入按饮食、工作、生活三类给出这一天的核验事实（环境记下的，只作背景）和这个人自己写下的记录。用这个人的口吻写一到五条睡前感想，每类至多两条，没有值得写的类别就不写：这一天哪些事让他在意、感觉怎样；对自己的习惯、口味、工作或身边的人有什么新的认识；接下来想保持什么、想换换什么。每条是一句第一人称的话，不超过八十个字；refs 写支撑这条感想的输入编号，至少要有一个自述编号。不要复述事实清单，不要写编号或长数字串，不要把没有发生的事写成已经发生。感想只是这个人自己的想法，不是核验事实。只调用一次 submit_reflection。

**Tool.** Unchanged: `submit_reflection` with 1–5 insights and the same JSON schema (`life_reflection.py:52-97`). Its description becomes 「写下睡前感想，并注明依据的输入编号。」. The quality call class keys on the tool name, so it stays `reflection` (`quality.py:795-812`).

**Validation** gains one rule: each insight cites at least one `n*` id. A failure goes through the existing retry path, so every 感想 rests on the persona's own words.

**Category of an insight.** The host computes it as the majority category of the cited inputs, breaking ties 饮食 > 工作 > 生活. The model never assigns a category. Before storing, the host translates refs to source event ids, so `episodes.jsonl` keeps durable provenance.

**Recording.** The reflection snapshot gains a single string field, `category` (`life_memory.py:351-359`; `state/model/memory.py:31-40`). That fits AS2 `MemoryUpdateEvidence.episodes: tuple[dict[str, str], …]` without a patch. Registration checks that it is in `MEMORY_CATEGORIES`. 前三天 reads its 感想 from these events, exactly as `_reflections` does today (`memory_layer.py:306-317`).

**No merging.** The 24-hour Jaccard merge (`life_memory_store.py:123-136`) skips reflection-sourced records in both directions, so dated insights never fold across days.

### 4.4 What is removed or replaced

| Today | Replacement |
|---|---|
| Threshold consolidation (27 per run) and wake consolidation (4 per run) (`life_memory.py:114-117,139`). Nothing reads them (`life_memory.py:79-92`). | Removed. The `consolidation` call class stays and counts 0. `consolidated` is always false. |
| Reflection input "last 30 episodes", which missed every morning (`life_memory.py:264-267`) | The full calendar day, grouped by category |
| MEMORY.md | Mechanics unchanged; it remains workspace evidence and its fact window stays. Its subjective section stops refreshing ("暂无人物自述"). The 153 empty-trigger re-renders are a separate cleanup. |
| AS2 `episodes.jsonl` | Still written (conclude memories and views, reflections) as hashed workspace evidence. **The decision prompt and the tools no longer read it** (phase 2). |
| 8000-char single window with reflections evicted first (`memory_layer.py:320-336`) | A cap per tier. 前三天 is never evicted by 今天. |

### 4.5 Storage, resume, replay, export (F18)

- **Facts.** Nothing new is stored. They are a pure function of committed events and the frozen catalog, so recomputing them after restore must be byte-identical. That gets a test.
- **Subjective.** Text and category are inside `memory_updated` (the existing route), and episodes are in the checkpointed workspace. Nothing is regenerated.
- **search_memory answers** live only in the next turn's `model_request`, as `recall_task_summary` answers do today.

### 4.6 Cost per simulated day

Prices assumed: about $0.28/M for an uncached input token, $0.028/M cached and $0.42/M output. These reproduce quality.json's $1.418 for fresh turns to within about 5%, but they are an assumption.

| Item | Δ $/day |
|---|---|
| Reflection: 1 call, about 3.4k input and 0.3k output tokens | +0.001 |
| Consolidation removed (about 6 per day) and the old reflection | −0.005 |
| Reorder: about +3.9k cached / −2.7k uncached tokens per fresh call × 61 | −0.04 |
| search_memory: 4–8 history turns at about $0.0025 | +0.01–0.02 |
| **Net** | **about −0.02 to −0.03 (from $0.33 to about $0.30/day)** |

Deterministic compute is negligible: today's entries are built incrementally on the existing event cache, and past days are memoized.

---

## 5. Retrieval: `search_memory`

**Schema** (a strict tool; parameter names are English like the other tools, values are Chinese):

```json
{"type":"function","function":{"name":"search_memory","strict":true,
 "description":"按类别和时间查自己过去的记忆（核验事实、自述、感想与看法），可加关键词；不改变世界。",
 "parameters":{"type":"object","additionalProperties":false,"required":["category","time","keyword"],
  "properties":{"category":{"type":"string","enum":["饮食","工作","生活","全部"]},
   "time":{"type":"string","pattern":"^(\\d{4}(-\\d{2}(-\\d{2})?|-W\\d{2})?|全部)$"},
   "keyword":{"type":["string","null"],"maxLength":40}}}}}
```

**Semantics** (cutoff: events up to the decision's frozen observation sequence number):

| time | keyword = null | keyword set |
|---|---|---|
| `YYYY-MM-DD` | every entry of that day, untruncated, plus the digest and 感想 | entries of that day that match |
| `YYYY-Www` | 7 day digests plus 感想 | entries that match, newest first |
| `YYYY-MM` / `YYYY` | weekly / monthly rollups | entries that match |
| `全部` | rejected with feedback: 「查全部时要写关键词」 | whole run |

- **Keyword matching:** the keyword is split on spaces, and every term must appear as a substring (AND) in the entry's 事实, 自述 or 感想, or in a view. This fixes the two empty V searches, which failed because a multi-word query was matched as one literal substring (`agentsociety2/agent/memory.py:646-654`).
- **Category 全部** groups the results by category.
- **Future or out-of-run times** return empty 结果 with a 提示 rather than an error.

**Result shape and limits:** at most 12 items and ≤4,000 chars. An item reuses the 今天 entry shape (plus 日期) or the 前三天 cell shape. A rollup item carries 周 or 月.

```json
{"类别":"饮食","时间":"2026-10","关键词":"刀削面","结果":[{"日期":"2026-10-12","时刻":"11:53","活动":["购买番茄牛肉刀削面"],"事实":["11:53 买了番茄牛肉刀削面 1 份，付 33 元，预计 12:13 送到练秋湖研发中心"],"自述":[]}],"截断":false}
```

When the result overflows: `"截断":true,"提示":"范围太大，换更短的时间或加关键词"`.

**Implementation and read-only guarantee.** The call goes through the existing generic `read_query(context, tool, request)` channel, which forwards any tool name (`runtime/framework/read_query.py:23-45`). `LifeEnv.read_query` adds `search_memory` to its whitelist (`life_env.py:161`). It validates the context and round, reads only up to the observation sequence, writes no events, and does not advance time.

**Relations to other tools:**
- `search_memory` replaces `memory_recent`, `memory_search` and `memory_time_range` (`life_agent.py:95-140,169-195,665-683`). The decision tool count goes from **10 to 8**.
- `recall_task_summary` is kept for by-name recall of an activity or goal's progress (`dispatcher.py:336-349`), including the `report_status` notes that memory excludes.
- The read-only Q&A path drops its memory tools and keeps only `finish`. It has no production caller (F22).

**Turn budget.** Each search is one history turn, about $0.0021 (F4). On the last turn query tools are already removed (`life_agent.py:397-404`). The cap of 3 searches per decision bounds the worst case at 4 turns, well under 16.

---

## 6. System-prompt teaching (exact text)

Edits to `LIFE_SYSTEM_LAYER.rules` (`life_prompt.py:21-99`). There are 20 rules today and 22 after the change.

**Rule 1 (replaces `:25-27`):**
> 人设层写你的身份、作息与习惯；记忆层写已经过去的事；待办层写暂停活动、个人事务、此前下单还没到的订单和今日计划；工具层写此刻的对象、动作、条件与代价；环境层写身体感觉、位置、天气和工作安排；进度层写这段停留或路程中已经发生的事。

**New rule 2, the memory system:**
> 记忆层按饮食、工作、生活三类记已经过去的事。「今天」是今天已经结束的停留和路程里的决策，每次决策每类一条，写时刻、类别、活动、事实和自述；眼下这段停留或路程里的事还在进度层，结束后才记进「今天」。「前三天」每类每天一条，事实是当天这类事的摘要，感想是那天睡前写下的；饮食和生活取最近三天，工作取最近三个上过班的日子。「看法」是以前收尾时写下、到昨天为止的看法，按类别和人列出，写着记下的日期，今天新写的看法在「今天」里。更早的事、某一天的全部细节，用 search_memory 按类别和日期、周、月或年去查，可以加关键词，比如查上次吃某样东西是哪天。

**New rule 3, how to use it without being steered:**
> 拿不准时可以先看记忆再定：选吃什么，看看最近几顿吃了什么、当时觉得怎样；接着做工作，看看前几个工作日做到哪里，某项活动或目标的进展用 recall_task_summary 按名称回看；照某条看法做之前，看看它是哪天记的、后来有没有变。记忆只说明过去发生了什么、你当时怎么想，不替你决定；照旧、重复还是换一换都可以，照人设层的习惯和你现在的想法选。

**Fragment in the decision rule (`:32`):** 「提交前可以查询记忆或回看任务」 becomes 「提交前可以用 search_memory 查记忆、用 recall_task_summary 回看任务」.

**Verified-vs-own rule (replaces `:75-78`):**
> 世界请求登记后由环境结算。进度层结果和记忆层的「事实」是核验事实；自己写的 summary、self_note，以及记忆层的「自述」「感想」「看法」都只是自己的想法，不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。

**Reply-body rule (replaces `:96-97`):**
> 正文可以留空；写了就作为这次决策的自述，先留在进度层，这段停留或路程结束后记进记忆层「今天」。思考只在同一时刻连续决策之间保留，时间推进后丢弃。

**Phase variants:**
- Phase 1 has no 看法 block yet, and keeps the old memory tools. It ships rule 2 without its 「看法」 sentence, with the last sentence replaced by 「更早的事不在提示里，可以用记忆查询工具找自己写过的记录。」. It also keeps today's `:32` fragment and today's `:75-78`, which still names 人设层 preferences.
- Phase 4 replaces the clause 「眼下这段停留或路程里的事还在进度层，结束后才记进「今天」」 with 「每次决策结束后记一条」.

**Read-only layer** (`:102-111`), rules 2 and 3:
> 记忆层的「事实」是环境核验的事实；「自述」「感想」「看法」和人设层是你自己写下的，未经核验。

> 只依据人设层与记忆层回答，不能改变世界，也不写入记忆。

**Tool descriptions:** remove the three `memory_*` entries (`:122-124`) and add the `search_memory` description from §5. Tool descriptions state purpose only (06-agent.md:74).

**Why this does not force behaviour.** The rules state what each tier is and when looking is useful, with examples on both sides ("照旧、重复还是换一换都可以"). Nothing tells the persona to vary. Variety should come from the persona card and from the facts now being visible. That makes repeat rate a real measurement rather than an instructed outcome.

---

## 7. Persona-layer preferences and relationship views (phase 3)

Today views are the last value per exact subject, with no cap or expiry. V ended with 40 preferences, about 4k chars, inside 人设层 (`memory_layer.py:127-144`; `life_persona.py:29-54`), and they change 人设层 on 74 of 405 requests.

**Move.** Views move into 记忆层.看法. 人设层 keeps the static card, including card relationships without the dynamic `view` field, so it becomes byte-static. The schema changes at `prompt_schemas.py:145-148`.

**Categorize.** Each view takes the category of the activity named in its conclude item (76 of 76 linked). Relationship views go under 人.

**Date.** 记于 is the local date of the latest write. Relationships gain a date too.

**Dedupe.** Within a category, subjects are compared deterministically: NFKC, then remove spaces, punctuation, 的 and 了. If one subject then contains the other, the later view wins. This catches 美式咖啡/冰美式咖啡, 自己做的晚饭/自己做晚饭, 彩虹桥/彩虹桥往返步行 and 微信读书/睡前微信读书. It misses 午休小睡/午睡五十分钟左右, which is accepted.

**Budget.**
- Each category holds at most 6 items and 600 chars; 人 holds at most 5 items.
- Each text is cut to ≤100 chars.
- Total ≤2,300 chars.
- Items are ordered newest 记于 first.
- Evicted views stay searchable, because they live in conclude events.

**Freeze.** 看法 shows views up to the end of D-1. Today's views appear immediately in two places:
- in the 进度层 conclude record. `control_request` currently drops views (`state/interaction/control_items.py:84-96`); it now keeps them.
- in 今天 自述, once the session ends.

So nothing changes inside a chain.

**Effect on the motivating case.** 「番茄牛肉刀削面…换口味时还想再点（记于 10-12）」 now sits next to 前三天 饮食, which lists that noodle on three dates.

---

## 8. Interactions

- **Same-second continuation (F4).** `life_continuation.py:8` assumes 记忆层 is constant inside a chain.
  - In phases 1–3 this still holds. 今天 changes only on a session switch, which breaks chains anyway (`continuation.py:85-88`). 前三天 changes at midnight and wake, which are new seconds. 看法 is frozen. 未到的订单 lists only orders from before the session.
  - Phase 3 removes `relationships` and `preferences` from `_view` and `layer_changes` (`life_continuation.py:46-53,111-122`).
  - Phase 4 adds an append-only `changes` key, 「今天」, listing the new entries.
- **Read-only snapshot.** `write_prompt_snapshot` (`life_agent.py:486-487`; `life_persona.py:72-85`) stores the new 人设层 and 记忆层. The read-only path uses `finish` only.
- **Midnight planner (F12).** Unchanged. It still reads yesterday's raw facts and conclusion records (`day_plan_inputs.py:83-115`), and its window matches the new day key. Optional, decision D10: add the deterministic 前三天 饮食 and 工作 cells to its input, so dinner and work goals see the same facts.
- **Capacity condensation.** Unchanged. 记忆层 is hard-capped by its builder and never condensed. In phase 4 the slimmer 进度层 makes condensation even less likely.
- **recall_task_summary.** Unchanged semantics. `noted_label` (`memory_layer.py:147-152`) reads 今天[].自述 together with the 活动 labels and deduplicates only untruncated text. `note_labels` come from the entries' 活动.

---

## 9. Contract and gate change list

**Code (WorldMock only).**
- `state/model/memory.py`: `MEMORY_CATEGORIES`. Snapshot validation accepts `category` on reflection snapshots, and old snapshots without it remain valid (they show as uncategorized). This keeps old exports verifiable.
- New `simulation_engine/observation/memory/` holding `categories.py`, `entries.py`, `digests.py` and `search.py`.
- `memory_layer.py`: rewritten.
- `settled_text.py`: dinner join and digest templates.
- `todo_layer.py`: 未到的订单.
- `progress_layer.py`: phase 4 only.
- `control_items.py:84-96`: keep views in the conclude record.
- `prompt_schemas.py`: 记忆, 待办 and 人设 schemas.
- `life_prompt.py`: rules, order, descriptions, `empty_memory_layer`.
- `life_persona.py`: card only.
- `life_continuation.py`.
- `state/interaction/requests.py`: `SearchMemoryRequest` and a query-model map.
- `custom/agents/life_agent.py`: tool maps; remove `_dispatch_strict_memory`; the search cap; pass `day_input` into `wake_reflection`. It gets about 120 lines shorter.
- `custom/envs/life_env.py:156-180`, `engine.py:93-97`.
- `life_memory.py`: drop the consolidations; day-scoped input; category.
- `life_reflection.py`: prompt; at least one note ref.
- `life_memory_store.py`: no merge for reflections.
- `evidence/model.py:173-184`: category check.

**Docs** (each changes in the same commit as its implementation, per docs/INDEX.md:18):
- `docs/design/06-agent.md`: tools and tool count at `:52-57`; the 进度、待办与记忆 section at `:216-306`; reflection and consolidation at `:296-346`.
- `docs/design/05-recording-engine.md`: the `memory_updated` snapshot, `:80-90`.
- `docs/design/10-runtime.md:79-91`: "十个" becomes 八个; `read_query` now serves `search_memory`.
- `docs/design/04-simulation-engine.md`: memory derivation.
- CLAUDE.md stays stale; separate owner task.

**Tests:**
- `tests/unit/start_interfaces/test_system_layer_text.py`: 22 rules verbatim; the `TOOL_DESCRIPTIONS` key set.
- The ten-tool pins in `test_batch_shapes.py:48`, `test_activity_schema.py:260`, `integration/runtime/test_seven_layer_request.py:41`, `test_current_runtime_contract.py:90`, `test_interaction_menu_roundtrip.py:29`. `test_seven_layer_request.py` also pins layer order.
- `test_tool_description_references.py`, `test_life_agent_protocol.py`, `test_read_query_runtime.py`, `test_same_second_continuation.py`.
- Reflection: `tests/unit/agents/test_life_memory.py`, `integration/runtime/test_daily_reflection.py`, `test_strict_reflection.py`, `system/lifecycle/test_sleep_and_reflection_run.py`.
- `test_compare_runs.py:132,315-318`, plus field paths in `scripts/compare_runs.py:81-92`.
- The viewer: `tools/run-viewer-context.js:39-47`, `tests/system/viewer`.
- New tests:
  - category table over the whole catalog (every row resolves; the office-lunch misfile is pinned);
  - day-key boundaries at 23:59, 00:00 and 00:15, and an overnight session;
  - dinner join for kit, no kit, and two kits;
  - per-tier budgets and trim order;
  - 前三天 and 看法 byte-stable within a day;
  - restore recomputation byte-identical;
  - search: read-only (`last_event_seq` unchanged), cutoff, AND keywords, limits, the cap of 3.

**Unchanged:**
- **Event shapes:** 14 event types; `memory_updated` operations, triggers and write points (`memory_evidence.py:14,58`); the `reflection` binding keys.
- **Identity and quality:** `CALL_CLASSES` (no quality schema bump); `RUN_MANIFEST_SCHEMA_VERSION` 7 (no ModelConfig field; budgets are code constants).
- **Storage and AS2:** observation part keys (`observation_parts.py:21-22`, key `memory_layer` stays); checkpoint format; AS2 sources and SERIES (no patch).

**Fingerprints.** The prompt sha changes in phases 1, 3 and 4. The tool-schema sha changes in phase 2. code_sha256 changes in every phase. Old runs cannot resume or branch under new code, which is expected.

**LabelKit.** Its pins (export 8, manifest 7, world 15, checkpoint 1, the 14-type enum) are unaffected, and event `data` is a free object there. Send a heads-up that the `memory_layer` and 人设层 shapes inside observations changed.

**Gates (F22):**
- style: functions ≤50 lines, ≤5 parameters, Chinese docstrings;
- branch coverage ≥85% with no uncovered public interfaces;
- a two-candidate refreeze of `tests/fixtures/dayrun-{activity,callback}-baseline.json` for each phase;
- `viewer-context.json` re-recorded with a real host;
- each phase proven by a real-model run, because recorded responses reject model-visible changes.

---

## 10. Phased rollout

Each phase is a consistent, shippable state, and each later phase builds on phase 1.

**Phase 1: categorized day memory** (the core; fixes the food pilot).
- **Changes:**
  - per-(decision, category) 今天 for ended sessions;
  - 前三天 digests with dinner naming, and 感想 from the retargeted reflection;
  - open orders move to 待办层;
  - the new layer order;
  - consolidation removed;
  - rules as in the §6 phase-1 variant.
- **Acceptance:** the new tests; 前三天 non-empty in 100% of observations after day 1 (V had earlier_days in only 21 of 169 evening requests); 记忆层 ≤12k; one reflection registration per day after day 1; 0 consolidation calls; no new quality memory problems.
- **Pilot:** rerun the V 5-day food pilot (10-08→10-13) twice. Metrics against V and B:
  - **lunch repeats:** workday lunches whose main dish matches one of the 2 previous lunches. V repeated the same noodle on 3 of 4 lunches. Report the number; target ≤1 of 4, given the card line 「每顿饭都愿意换着尝不同的菜」.
  - distinct dishes across about 10 meals;
  - dinner-naming coverage: 100% after a kit;
  - memory citations: the share of food decisions whose reasoning or summary names a dish or date shown in 记忆层, matched by substring. Target ≥40%;
  - prompt characters per layer;
  - fresh-turn cache hit, target ≥0.45 (V 0.336);
  - cost per day, ≤$0.33;
  - regressions: rejections, turns per decision, run failures.

**Phase 2: search_memory.**
- **Changes:** tools go 10 → 8; read-only path uses `finish` only; final rule text for search.
- **Acceptance:** read-only and determinism tests.
- **Pilot:** searches per day; non-empty share ≥80%; mean turns per decision ≤1.15 and p99 ≤3; 0 exhaustions; cost per day.

**Phase 3: views as memory-longterm.**
- **Changes:** §7.
- **Acceptance:** 人设层 byte-identical in 100% of requests; 看法 ≤2.3k chars; known near-duplicate pairs shown at most once each.
- **Pilot:** same metrics, plus the cache hit uplift.

**Phase 4: per-decision 今天, the owner's literal model.**
- **Changes:**
  - 今天 includes every closed decision of the current session;
  - 进度层 keeps only in-flight items: running, paused-in-session and awaiting-conclusion records, query records, new pushes and deliveries, carried records, rejected_today;
  - the 「今天」 delta in chain `changes`.
- **Pilot:** 进度层 median ≤7k (from 14.7k); continued-chain share unchanged; cache hit held.

**Phase 5 (optional): catalog `memory_category` on content and app rows.** This is an authored field in WorldMock-Dataset with catalog format 5 → 6 (`catalog/models.py:51`). It is validated fail-closed at admission, and the derived rules remain as a test cross-check. It fixes office lunch and generalizes to personas 0002–0005.

**Cost of the rollout.**
- 5-day pilot: about $1.6–1.8 per run, 2 runs per phase, about $3.5.
- Day-run refreezes: about $1.3 per phase.
- Total for phases 1–4: about $20.

---

## 11. Risks and decisions for the owner

| # | Decision or risk | Recommendation |
|---|---|---|
| D1 | Chinese keys in 记忆层 (the rules quote them) vs the English convention of the other layers | Chinese in 记忆层 and in the new 待办层 field; English mapping kept as a constant |
| D2 | Phases 1–3 keep current-session decisions in 进度层 (deviates from "every round → today") | Accept. It avoids duplication, keeps 今天 cache-stable and keeps chains intact. Phase 4 delivers the literal model once 进度层 is slimmed |
| D3 | 3 categories vs adding 休息 | 3, as the owner asked; sleep goes in 生活 because dating by observation removes the midnight problem |
| D4 | Commute in 工作 or 生活 | 工作: it belongs to workday continuity and leave-time choices |
| D5 | Office lunch filed under 生活 until the catalog field exists | Accept for phases 1–4 (the dish is still 饮食 through its receipt); do phase 5 before multi-persona runs |
| D6 | `report_status` summaries are progress, not memory | Yes. They are in-flight; the conclude summary supersedes them, and they stay reachable by name |
| D7 | Last 3 workdays for 工作 vs the calendar | Workdays, because the holiday gap would otherwise hide the last workday |
| D8 | Facts shown to the reflection call | Yes, as labelled background. Every 感想 must cite at least one own note; facts are never stored from model output |
| D9 | Remove consolidation and the AS2 memory tools; read-only path gets `finish` only | Yes: no reader, and 2 lifetime searches that both returned nothing |
| D10 | The planner consumes 前三天 cells | Not initially; revisit after the phase 1 pilot |
| D11 | Turn budget 32 | Keep 16 plus a cap of 3 searches per decision; revisit if p99 turns exceed 4 |
| R1 | Repetition persists because the card's habits win | Allowed by design. Judge success on informed choices (citations), not forced variety |
| R2 | Reflection 感想 states falsehoods | It is labelled subjective, requires note refs, and facts sit beside it verbatim |
| R3 | The prompt grows by about 7% | The growth is cached; the net cost is about −$0.02/day; phase 4 then removes about half of 进度层 |
| R4 | Over-searching | The per-decision cap and the teaching examples; watch the pilot metric |
| R5 | Old exports and validators | Snapshot validation accepts the legacy shape without a category, so old exports still verify |