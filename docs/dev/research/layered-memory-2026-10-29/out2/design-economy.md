# Memory redesign: prompt economy and staged migration

Scope: WorldMock at HEAD `f67c25e5`. All figures come from the variety run (V); baseline-run (B) figures are given only where they differ. Where this design differs from the owner's literal model, the reason is stated next to the change.

My own measurements were made with read-only scripts in `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/econ/`:

| script | what it measured |
|---|---|
| `prog_fields.py`, `slim.py` | where 进度层 characters go, and its size if it kept only open threads |
| `premidnight.py` | records in 进度层 from before midnight |
| `toolgroups.py` | how often 工具层 groups change |
| `concl.py` | volume of text the model writes |
| `meals.py` | lunches and dinners per day |

## 1. Units and glossary decisions

| Owner term | Code today | Decision |
|---|---|---|
| session | `SessionState`, either `stay` or `transit`, opened and closed by the system (`src/worldmock/state/world/agent.py:57-95`) | Keep the code concept. Transit also counts as a session. Sessions are never split, so **memory stops being keyed by session**: they still scope the open threads in 进度层 and nothing else. |
| round | a decision (`decision_id`). It ends at the first registered world request: start (single or pair), `report_status`, `pause_activity`, `conclude_activity` or `revise_day_plan`, even if the request is rejected (`custom/agents/life_agent.py:563-583`) | Use the code's definition. In docs it is called 决策 (轮). The AS2 `round_id` is left alone. The owner's "start/pause/conclude" covers only about two thirds of rounds; 136 of 406 rounds end with `report_status`. |
| turn | one model call, `…:model:NNNN` | The budget stays `max_react_turns = 16` (`src/worldmock/configuration/inputs/models.py:223`). |
| memory entry | none | A **今天条目**: one per (decision second, category). See §3. |
| day | three rules today: session end, local calendar day, wake − 1 | **One rule: the local calendar date in the configured timezone.** This is the rule that plan_day, `plan_date` and `rejected_today` already use. |

**Turn budget.** 402 of 406 decisions used one turn, and the maximum was 3 (in B). `search_memory` is a read-only query, so it does not end the round. Each search adds one turn of the history class, which is mostly cached; a real follow-up turn hit 16.1k of 16.4k cached tokens. Raising the budget to 32 would only change the model identity. **Keep 16.** Query tools are already removed on the last turn (`life_agent.py:397-404`). Revisit only if a pilot shows any decision using more than 8 turns.

**What produces an entry.**
- Let C_k be the last event committed by decision k.
- Decision k's *window* is the events with sequence in (C_{k-1}, C_k]. That covers settlements during time advance before k (natural endings, deliveries, payments) plus k's own commit, so every fact lands in exactly one window.
- Decision k produces an entry only if its window holds at least one closure fact, a successful conclude or pause, or a non-empty reply body (`self_note`).
- `report_status` summaries do **not** produce entries; see §3.

## 2. Target prompt structure

**Order.** System layer (static), then native tools (static), then the user array:

```
[人设层, 待办层, 记忆层{长期, 近几天, 今天}, 工具层, 环境层, 进度层]
```

The order today is 工具, 人设, 记忆, 待办, 环境, 进度 (`src/worldmock/agents/prompt/life_prompt.py:144-156`). It changes for three reasons:

- **Volatile layers first destroy the cache.** 工具层 changes in nearly every fresh pair. Its purchase group changes in 303 of 304 pairs, its app group in 234 and its travel group in 186. Because it comes first, fresh calls cache only the system message plus tools: 6.65k of 19.8k tokens, a 34% hit rate.
- **A cache hit is 50 times cheaper than a miss.** The frozen pricing snapshot gives $0.006 per million tokens for a hit and $0.30 per million for a miss, and every V call ran at peak rates. The main lever is therefore to **move content out of the volatile tail into an append-only stable block**, not to shrink everything evenly.
- **An append-only list at the end of the stable block keeps its prefix.** The JSON serializer keeps keys in build order (`src/worldmock/agents/prompt/narration.py:163-175`). If 今天 is the last key of 记忆层, and 记忆层 is the last stable layer, the shared prefix runs through the previous request's final entry. F15's warning about "a growing list" applies only when the list sits in the middle of the block.
- **待办层 sits before 记忆层.** It changes in 2–11% of pairs. Placed after a list that grows on about 45% of rounds, it would be invalidated more often.

**Per-part budgets** (compact-JSON characters; "today" is the current median, p90 and max):

| Part | Today (median / p90 / max) | Target | Overflow rule | Cache class |
|---|---|---|---|---|
| System layer | 3,377 | about 3,900 | — | static |
| tools | 11.2k (10 tools) | about 10.4k (8 tools) | — | static |
| 人设层 | 3.0k / 4.9k / 5.3k | ≤1,400 (card only, about 0.98k measured) | card ≤600 hanzi (existing) | static |
| 待办层 | 0.87k / 0.94k / 0.94k | ≤1,600 | errands ≤8, 在途订单 ≤6 | rarely changes |
| 记忆层.长期 | (preferences in 人设层: 1.8k / 3.7k / 4.0k) | ≤2,200 | newest first per category; the rest are reachable by search | changes daily |
| 记忆层.近几天 | (earlier_days: 0 / 1.9k / 1.9k) | ≤3,000 | per day and category, 核验 ≤200 and 自述 ≤120; overflow becomes「等 N 项」 | changes daily |
| 记忆层.今天 | (earlier_today: 3.9k / 5.7k / 7.8k) | ≤6,000 | fold down to ≤4,000 (see §3) | append-only |
| 工具层 | 8.6k / 9.6k / 10.1k | unchanged | existing `compact_render` | volatile |
| 环境层 | 0.38k | unchanged | — | volatile |
| 进度层 | 14.7k / 27.6k / 42.8k | estimated 1.8k / 5.7k / 7.8k | existing condensation at 85% stays as a backstop | volatile |
| user message | 31.7k / 45.3k / 58.2k | about 20k / 28k / 32k | — | — |

**Mapping the owner's 2×2.**

| Quadrant | Where it lives | What it holds |
|---|---|---|
| memory-longterm | 记忆层.长期 and 记忆层.近几天, plus `search_memory` for anything older | Views by category and relationship views, each dated. One 回顾 per day and category for D-1 to D-3; for work, the last 3 days that had work. Every older 回顾 and entry is searchable. |
| memory-today | 记忆层.今天 | Entries for today's rounds that closed something: 核验 for verified closures, 自述 for summaries, views and reply bodies. |
| progress-longterm | 待办层 | Paused activities from any day (unchanged), errands (unchanged, ≤8), **在途订单** (new), and today's plan (unchanged). |
| progress-today | 进度层, plus 工具层's activity group and 环境层 | The current stay or trip's *open threads*: running or awaiting-conclude activities, each with its start record and its **latest** `report_status`; results, deliveries, pushes and reminders newer than the previous decision; this stay's latest query answers; and `rejected_today`, which stays day-scoped (`progress_layer.py:316-347`). |

Placement of specific items:

| Item | Where it goes |
|---|---|
| Open orders | Move from `earlier_days.settled` (empty in all 503 entries) and from 进度层's protected records to 待办层.在途订单. They reuse `order_label` and the status wording at `progress_layer.py:259-273`. |
| Query answers | The full answer stays in 进度层 for the rest of the stay or trip. 今天's 核验 gets a one-line digest naming at most 3 items. Search reaches it afterwards. |
| Reply bodies | Go to 今天.自述 as「正文：…」. |
| `report_status` summaries | Only the latest one per open activity stays, in 进度层. Older ones are reachable through `recall_task_summary` (`note_history`). |
| `rejected_today` | 进度层, unchanged. |

**Deviation from the owner's model: settled facts.** The owner says the settled facts belong to progress. In this design, facts about *closed* things become memory-today, labelled 核验, and progress keeps only *open* things. The owner's own model says the per-round log *is* the memory entry, and putting the facts in both places would duplicate about 2.2k characters a day. Open orders and the previous evening of an overnight stay, the parts that genuinely posed as memory, leave 记忆层.

## 3. Memory entry model

**Shape as rendered** (keys exactly as shipped; empty keys are omitted):

```json
{"时刻":"12:20","类别":"饮食",
 "核验":["11:46 买了番茄牛肉刀削面 1 份，付 33 元，预计 12:04 送到练秋湖研发中心",
         "12:04 番茄牛肉刀削面 1 份（11:46 购买）：已送到练秋湖研发中心",
         "进行吃午饭（10月9日 12:04 起）：12:04–12:19，按计划结束，《吃午饭》"],
 "自述":["吃午饭：又点了刀削面，汤还是很开胃","看法·番茄牛肉刀削面：吃了两天还没腻，可以换换别的"]}
```

Fields:

- `时刻` is HH:MM of the decision. The date is implicit (today).
- `核验` holds deterministic sentences from `settled_text` (`activity_sentence`, `purchase_sentence` and the delivery line, `settled_text.py:44-178`), limited to events on the entry's own calendar day.
- `自述` holds model-written text: 「〈活动短名〉：summary」, 「看法·〈对象或人〉：text」 and 「正文：self_note」.
- A folded entry carries `"已收起":true` and no 自述.
- Held internally but not rendered: the date, decision ids, source event ids, plain `memories`, and a subtag. Search uses them.

**How rounds yield entries:**
- Only rounds whose window has a closure fact, a successful conclude or pause, or a non-empty reply body produce an entry.
- Start rounds with nothing settled, `report_status` rounds without new facts, `revise_day_plan` rounds and rounds that were only rejected produce nothing. Rejections live in `rejected_today`, and the plan lives in 待办层.
- All rounds in one second merge into a single entry per category. That covers start pairs and continuation chains.
- **An entry is published from the first observation at a later second.** This keeps 记忆层 byte-identical inside same-second chains (F4), and the merge never rewrites anything already shown.
- One round splits into at most 3 entries, one per category, with the same 时刻.

**Deliberate exclusions, with reasons:**

| Excluded from 今天 | Reason |
|---|---|
| `report_status` summaries | 2.3–4.1k characters a day. They describe an open activity and are superseded by its conclusion. |
| Plain `memories` (想记住的) | 1.2–1.9k characters a day. 138 of 160 are `routine` paraphrases of the summary or the card. They go to the digest input and search. |
| Purchase-activity ending sentences when a purchase sentence exists for the same request | Redundant. |

Estimated 今天 volume by the end of a day is 5.4–7.4k characters: facts about 2.0k, conclude summaries 1.8–3.2k, views 0.4–1.0k, reply bodies about 0.2k, keys about 1.0k. Without these exclusions it would be 8–12k (F16).

**Fold.**
- When 今天 renders past 6,000 characters, the oldest unfolded entries drop their 自述 until 今天 is ≤4,000.
- This hysteresis means at most 1–2 cache breaks a day, each costing about 1.7k miss tokens, roughly $0.0005.
- Folded 自述 stays reachable with `search_memory(…, "今天")`.

**Verified vs subjective.**
- 核验 is produced by code from committed events and nothing else.
- 自述 is text the model wrote. The rule at `life_prompt.py:75-78` is rewritten to name these two labels (§6).
- The model never writes into 核验 at any tier, compactions included.

**Categories.** The closed set is `饮食 / 工作 / 生活`; it goes in `src/worldmock/state/model/memory.py` next to `SUBJECTIVE_TYPES`, as the dependency rule in F21 requires. Rest stays inside 生活: sleep is short and numeric, and 环境层 already reports sleep debt. Derivation is deterministic, and the first matching rule wins:

1. A catalog override `memory_category` on the content or app row, if set (new optional field; see below).
2. Purchases, deliveries and queries. 饮食 when the goods category is `meal`, `snack` or `drink`, or when the goods appears in the `reset_on_goods` list of a state that some food-effect content consumes. The second condition catches all 22 食材包 kits, through `groceries` in DS `callbacks.jsonl:2`. Anything else is 生活. A query takes the union of its target categories.
3. Interact at a location. 饮食 when the content has `completion_effects.food`, or `consumes_state` is consumed by a food-effect content. That makes the whole 做饭 series, the dishes episode included, 饮食, along with the team dinner. 工作 when the persona is on duty and the location kind is the workplace kind. Anything else is 生活.
4. App interact. 工作 when one of the app's content rows requires the workplace location kind (the dingtalk clock-in). Anything else is 生活.
5. Travel on a duty day between home and the workplace is 工作 (commute). Other travel is 生活.
6. Sleep, office naps included, is 生活.
7. Conclude, pause and reply text inherits the category of the named activity. Linkage was 235 of 235.

Edge cases (F13):

| Case | Category | How |
|---|---|---|
| `office-eat-lunch` (DS `content-catalog.jsonl:760`) | 饮食 | It has no food signal, so the rules alone would say 工作. It gets the catalog override instead. The dish itself is already 饮食 through the order. |
| Kits | 饮食 | Rule 2 |
| Coffee | 饮食 | Drink goods, rule 2 |
| Naps | 生活 | Rule 6 |
| Cooking videos | 生活 | App rule 4 |
| Team dinner | 饮食 | `completion_effects.food`; views about colleagues go to 关系 |

Other personas: the workplace-kind rule fails for sales, students and the homemaker. Each package gets a deterministic coverage report that lists the derived category of every content and app row, and the dataset adds overrides where a row is wrong.

The catalog change is an optional field on `Content` and `App` (`src/worldmock/resource_engine/catalog/models.py:51-60,146-170`) with **no format bump**. LabelKit pins `CATALOG_FORMAT_VERSION` but only hashes the content rows (`LabelKit-DataSet/…/export_source.py:585-604`).

**Named dinners.** The ending sentence of any content that consumes `groceries` appends「（用 M月D日 收到的 〈食材包名〉）」.
- The kit is the last kit receipt before the activity started. The cap is 1, so the newest kit replaces the state. The card habit buys the kit the evening before, which this rule handles.
- If there has been no kit since the run started, the suffix is「（用家里原有的食材）」.

**Day key.**
- An entry belongs to the calendar date of its round.
- Its 核验 lists only facts dated that day. Earlier facts that were not yet in any entry belong to the previous day's 回顾, which is built from event times.
- An overnight stay contributes nothing by session.
- An activity between 00:00 and going to sleep belongs to the new day, matching plan_day.
- A sleep across midnight is listed in day D's 回顾 as「23:50 开始睡觉（跨夜）」, and its ending sentence is in the waking day's entry.

## 4. Compaction and tiers

**Ladder.**

| Tier | Contents | In the prompt? | Built by |
|---|---|---|---|
| 今天 | per-round entries | yes | code |
| 近几天 | one 回顾 per day and category: D-1 to D-3 for 饮食 and 生活; the last 3 days that had 工作 entries, for 工作 | yes | 核验: code. 自述: model (P4) |
| every past day | its 回顾 and its derived per-round entries | search | code (plus the stored 自述 from P4) |
| month / year | an on-demand listing of daily 回顾. Model-written month and year 回顾 only when a run spans at least 2 full calendar months | search | code; model in P5 |

The work rule exists because on 10-08 the calendar "last 3 days" are all holiday and would hide the 09-30 workday.

**The gap from day 4 to the month.** It is closed by keeping every daily 回顾 searchable. On the 30-day package calendar a month tier would fire once, for the 3 days of September, and a year tier never fires (F14). A whole month of daily 回顾 is about 30 × 560 = 17k characters, which a paged search can return without a model call. That is the deviation from the owner's literal ladder: monthly and yearly entries become virtual for runs under two months.

**The 核验 roll-up** is deterministic and has the same shape on every tier. Examples:

> 饮食: 「09:37 美式咖啡（外送，18.3 元）；11:38 番茄牛肉刀削面（外送，33 元）；19:30 在家做饭（用 10月8日 收到的 可乐鸡翅配清炒菌菇一人份食材包）」
>
> 工作: 「09:05 上班打卡，17:30 下班打卡；功能开发与自测 2 小时、需求评审会议 1 小时；驾车通勤 35 分钟、32 分钟」
>
> 生活: 「00:00–07:50 睡觉，12:17–13:17 午睡；微信 55 分钟、抖音 24 分钟；洗澡、洗碗收拾厨房」

**The 自述 for each day and category:**

- **P1–P3:** none per category. That day's bedtime reflections are shown on the day row as `反思`, inside the 近几天 budget, so the 8000-character window no longer evicts them by evening (F6).
- **P4:** one model call per day at the midnight hook, run *before* plan_day.
  - The input is the persona card plus, for each category, that day's 核验 (marked as already saved) and all of its 自述 sources: summaries, plain memories, views and reply bodies.
  - The output is the strict tool `submit_day_digest` with fields `food`, `work` and `life`, each a string of at most 120 characters or null.
  - It is **one call with category-separated inputs and outputs**, not three. Isolation is the same and fixed overhead is a third. The owner's "each compacts on its own" holds for storage and budgets.
  - Validation retries reuse `day_plan_validation_max_retries`. **No new `ModelConfig` field**, so `RUN_MANIFEST_SCHEMA_VERSION` stays 7.

The digest call's system prompt, exactly as it would ship:

> 你替模拟世界里的这个人物写昨天的回顾。下面按饮食、工作、生活三类给出昨天的核验事实和他当时写下的自述。每类写一段不超过 120 字的话，以这个人物的第一人称写当天的感受、印象和以后的打算，只依据他写下的自述；时刻、价格、菜名等核验事实已经单独保存，不必复述，也不要编造没写过的事。某一类当天没有自述时写 null。写完调用 submit_day_digest。

**What replaces the retired machinery:**

| Retired | Phase | Replacement |
|---|---|---|
| Threshold and wake-phase consolidation | P1 | Nothing. No prompt reads them (`src/worldmock/agents/memory/life_memory.py:79-92`), and reflection reads episodes, not the summary (`life_memory.py:264-275`). This saves 31 calls per 5 days. |
| MEMORY.md re-rendered at every step tail | P1 | Rendered once at initialisation. Restore validation still needs a non-empty file and an exact `state.json` key set. This removes 153 `refresh_summary` events per 5 days. |
| Bedtime reflection | P4 | The P4 自述. It reads the whole day, is filed to the correct calendar day, and is ready before plan_day instead of about 8 hours later. |
| The 8000-character window | P2 | The per-tier budgets in §2. |

**Storage and events (F18).**
- Per-round entries and 核验 roll-ups are pure functions of committed events. They are not stored. They are cached in-process per (store, agent, date), and past days never change.
- The P4 自述 is recorded as `model_request`/`model_response` with `purpose:"day_digest"`, plus an `environment_event` with `kind:"day_digest_generated"`. Its data is exactly `{kind, date, digests:{饮食,工作,生活}, source_event_ids}`, committed atomically. This is the `day_plan_generated` precedent (`src/worldmock/simulation_engine/scheduling/plans/day_planning.py:146-170`; `src/worldmock/runtime/lifecycle/runner.py:619-671`).
- The idempotency key is the existence of that event for (agent, date). **No `AgentState` field is added**, so the world schema stays at 15.
- Resume, replay and export read the event and never regenerate it. The settle hook runs on all three existing paths: the midnight step and init/checkpoint (`custom/envs/life_env.py:113,214,235-243`).
- Failure handling mirrors plan_day: the evidence is committed first, then the run fails.

**Cost per simulated day of memory upkeep:**

| Item | Cost |
|---|---|
| P4 call: about 2.3–3.4k input tokens and about 0.25k output | about $0.0013 a day |
| Retired: consolidation and reflection | $0.0047 a day |
| Code-side cost | O(the day's events) per observation, which the existing incremental cache already pays (`memory_layer.py:165-180`) |

## 5. Retrieval

**Tool** (strict schema; parameter names stay English, like the other nine tools, to avoid provider restrictions on schema keys):

```json
{"name":"search_memory",
 "description":"按类别和时间查自己过去的经历：某天、某段日子、某月或某年的回顾和当天各条记录，可以加关键词，不改变世界。",
 "parameters":{"type":"object","additionalProperties":false,
  "required":["category","period","keyword","limit"],
  "properties":{
   "category":{"type":"string","enum":["饮食","工作","生活","全部"]},
   "period":{"type":"string","pattern":"^(今天|\\d{4}(-\\d{2}(-\\d{2}(~\\d{4}-\\d{2}-\\d{2})?)?)?)$"},
   "keyword":{"type":["string","null"],"maxLength":20},
   "limit":{"type":"integer","minimum":1,"maximum":20}}}}
```

**Semantics:**
- `period` is resolved against the frozen observation time. It accepts `今天`, a single date, a date range of at most 92 days, a month, or a year. Future dates and reversed ranges return the existing read-query error shape (`life_env.py:172-180`).
- `keyword` is split on whitespace into at most 4 terms, matched case-insensitively with AND. This fixes the two real searches that returned `[]` (F10).
- Without a keyword, the result is daily 回顾, newest first; for `今天` it is today's full entries, folded 自述 included.
- With a keyword, the result is the matching per-round entries, plain memories included, from the period, plus any matching 长期 views.
- A month or year returns its model 回顾 first, when one exists (P5), and otherwise falls back to the daily rule.

**Result shape:**

```json
{"类别":"饮食","时间":"2026-10-05~2026-10-11","关键词":"面",
 "结果":[{"日期":"2026-10-09","时刻":"12:20","层级":"条目","核验":["…"],"自述":["…"]},
        {"日期":"2026-10-08","层级":"回顾","核验":"…","自述":"…"}],
 "未列出":3}
```

Results are capped at `limit` items and 4,000 characters; the remainder is counted in `未列出`.

**Implementation.**
- The query is answered env-side through the **existing** `LifeEnv.read_query(context, tool, request)` (`life_env.py:156-180`). Its type check widens from `recall_task_summary` alone to a `QUERY_MODELS` map in `src/worldmock/state/interaction/requests.py`.
- `engine.search_memory` reads only events at or below the frozen observation's sequence number.
- The forward in the Life extension channel is unchanged (`src/worldmock/runtime/framework/read_query.py:21-47`), and no AS2 patch is needed.
- It writes no events and no memory. The answer reaches the evidence log as the tool message in the next `model_request`.

**Relation to existing tools.**
- `recall_task_summary` stays as a name-keyed lookup of one task's history; the system prompt now teaches it.
- `memory_recent`, `memory_search` and `memory_time_range` are removed, together with `_MEMORY_TOOL_MODELS`, `_MEMORY_RUNTIME_ACTIONS` and `_dispatch_strict_memory` (`life_agent.py:169-195,665-683`). They searched only subjective episodes, by single-substring match.
- The life tool surface goes from 10 tools to 8.

**Turn cost.** One search is one more history-class turn: about $0.0021 today and about $0.001 after P2. Even with 15% of rounds searching, that is about $0.01 a day.

## 6. System-prompt teaching

Edits to `LIFE_SYSTEM_LAYER.rules` (`life_prompt.py:21-99`), final form after P3:

`rules[0]` (replaced):

> 人设层写你是谁、作息与习惯；待办层写暂停活动、个人事务、在途订单和今日计划；记忆层写过去的经历；工具层写此刻的对象、动作、条件与代价；环境层写身体感觉、位置、天气和工作安排；进度层写此刻还没了结的事：进行中或待收尾的活动、上次决策之后新到的结果与消息，以及今天被拒绝的请求。

`rules[1]` (new; the list grows from 20 to 21 rules):

> 记忆层分三块：「长期」是你写下的看法，按饮食、工作、生活和关系分开，注明写于哪天；「近几天」是前三天每天每类一段回顾，工作取最近三个有工作的日子；「今天」按时刻记下今天已经了结的事，每条注明类别。「核验」是环境确认的事实，「自述」是你当时写下的总结、看法和正文。更早的经历不在请求里，可以用 search_memory 按类别和时间查：时间写成 年-月-日、年-月-日~年-月-日、年-月 或 年，可以加关键词，多个关键词用空格隔开，都出现才算查到；回看一项任务的进展用 recall_task_summary。拿主意前可以先看看相关的记忆，比如安排吃饭、工作或休息时看看前几天是怎么过的；记忆只供参考，怎么选照人设层和此刻的感受决定。

The old rule 14 (replaced):

> 世界请求登记后由环境结算。进度层的结果和记忆层的「核验」是核验事实；自己写的 summary、正文、记忆层的「自述」和「长期」看法只是自己的想法，不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。

The old rule 20 (replaced):

> 正文可以留空；写了会作为这次决策的自述记进记忆层「今天」。思考只在同一时刻连续决策之间保留，时间推进后丢弃。

The phrases「提交前可以查询记忆或回看任务」and「查询记忆和回看任务当场完成，不推进时间」stay as they are.

**Interim versions:**

P1 `rules[0]`:

> 工具层写此刻的对象、动作、条件与代价；人设层写你的作息与习惯；记忆层写此前经历：「近几天」是前三天每天按饮食、工作、生活各一段的核验回顾，earlier_today 是今天已经结束的停留或路程；待办层写暂停活动、个人事务、在途订单和今日计划；环境层写身体感觉、位置、天气和工作安排；进度层写这段停留或路程中今天已经发生的事。

P1 rule 14:

> 世界请求登记后由环境结算。进度层结果、记忆层 settled 和「核验」是核验事实；自己写的 summary、self_note、self_notes、「反思」与看法（人设层 preferences 和关系里的看法也是）只是自己的想法，不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。

P2 `rules[1]`: the final text with the sentence from「更早的经历」through「recall_task_summary。」removed.

**`READONLY_SYSTEM_LAYER.rules` (P3):**

1. 「请求由人设层、记忆层与外部问题组成。」
2. 「记忆层的「核验」是环境核验的事实，「自述」和「长期」看法是你自己写下的，未经核验。」
3. 「可以用 search_memory 查更早的经历；只能查询自己的记忆，不能改变世界，也不写入记忆。」
4. 「回答完成后单独调用 finish。」

**Teaching without forcing.**
- The rule explains the structure and says *when looking helps*, using a neutral list of examples: meals, work, rest.
- It then states that memory is reference only and that the choice follows the persona card and current feeling. It never says to vary food.
- Whether the model varies its meals stays a consequence of the card line「每顿饭都愿意换着尝不同的菜」now that past meals are visible.
- Tool descriptions stay purpose-only (06-agent.md:74).

## 7. Persona-layer preferences and relationship views

They move from 人设层 to 记忆层.长期. The card stays in 人设层 and becomes static for the whole run. 人设层 currently changes in 24% of pairs.

- **Categorise.** A view inherits the category of the activity it was concluded on. Relationship views go to the separate list `关系`.
- **Date.** Every item carries `写于`, the local date of its latest write. Relationship views get dates too (today they have none, `life_persona.py:117-123`).
- **Dedupe**, in code, within one category. Subjects are merged when one contains the other (the shorter subject must be at least 2 characters) or when their character-bigram Jaccard is at least 0.5. The newer subject and text win. This merges 美式咖啡/冰美式咖啡, 彩虹桥/彩虹桥往返步行, 微信读书/睡前微信读书 and 自己做的晚饭/自己做晚饭 (Jaccard 0.5). 午休小睡/午睡五十分钟左右 stay separate, which is harmless.
- **Budget.** Newest first, up to 600 characters per category and 400 for `关系`. Text is never cut; items that do not fit are reachable by keyword search.
- **Refresh at midnight only.** Views written today appear in 今天 as「看法·…」and merge into 长期 at the next midnight. This keeps the stable block stable and removes the persona-view deltas from continuation chains.

Rendered shape:

```json
{"饮食":[{"对象":"番茄牛肉刀削面","看法":"…","写于":"2026-10-08"}],"工作":[…],"生活":[…],"关系":[{"人":"王磊","看法":"…","写于":"2026-10-10"}]}
```

The undated line「番茄牛肉刀削面…以后想换口味还会再点」now sits, dated, next to a 近几天 row that shows the same noodles on two of the last three days. The static card field `tastes.food_preferences` is identity and is unchanged.

## 8. Interactions

- **Same-second continuation (F4).** Entries publish only after the second ends, and 长期 changes only at midnight, so the assumption at `src/worldmock/agents/protocol/life_continuation.py:8` that 记忆层 does not change inside a chain becomes true by construction. The `changes` key for 人设层 relationships and preferences goes away. 进度层 deltas inside a chain are unchanged, because records fold into 今天 only after the clock moves on.
- **Readonly snapshot.** `write_prompt_snapshot` stores the new 人设层 and 记忆层. The index lookups `layers[3]`, `layers[2]` and `layers[-1]` (`life_agent.py:482-487`) become lookups by layer name, because the order changes. The readonly path has no production caller; it is kept and its four rules are updated.
- **Midnight planner (F12).** Its inputs are unchanged through P3. In P4 the digest settles before plan_day in the same hook. plan_day keeps reading raw history until P5 (owner decision D8).
- **Capacity condensation.** It stays as a backstop (`src/worldmock/agents/prompt/prompt_capacity.py:1-12`). 记忆层 is still never condensed, but it is now self-bounded at about 11k characters. With a maximum user message of about 32k characters, the 85% threshold is unreachable in practice.
- **`recall_task_summary`.** Unchanged. `noted_label` and `_note_labels` (`memory_layer.py:116-152`) read activity labels from the「活动短名：」prefix in 今天.自述, so the recall name book and the de-duplication of recall bodies keep working.
- **8000-character window.** `MEMORY_WINDOW_CHARS` and `_fit` (`memory_layer.py:36,320-336`) are retired in P2.

## 9. Contract and gate change list

**Code:**

| Area | Change |
|---|---|
| `src/worldmock/agents/prompt/life_prompt.py` | rules, readonly rules, `TOOL_DESCRIPTIONS`, `life_user_layers` order, `empty_memory_layer` |
| `src/worldmock/agents/prompt/prompt_schemas.py` | `MEMORY_LAYER_SCHEMA` (:153), `TODO_LAYER_SCHEMA` (:171) gains 在途订单, `PERSONA_LAYER_SCHEMA` (:133) loses preferences and relationships |
| Memory-layer builder | `memory_layer.py` is split into `memory_entries.py` (windows, entries, fold), `memory_days.py` (roll-ups), `memory_longterm.py` (views) and `memory_search.py`, all with functions under 50 lines (style gate, F22) |
| Category derivation | new `src/worldmock/simulation_engine/observation/wording/memory_category.py`; constants in `src/worldmock/state/model/memory.py` |
| Wording | `settled_text.py`: kit naming |
| `progress_layer.py` | P1: cut closed records from before midnight; P2: open-threads filter and latest-report rule |
| `todo_layer.py` | 在途订单 |
| `custom/agents/life_agent.py` | tool set, name-based layer lookup, removal of `_dispatch_strict_memory`; P4: remove `due_reflection` from `_observe_environment` (:311-322) |
| `src/worldmock/agents/memory/life_memory.py` | P1: remove `_consolidation_due`, `_consolidate_subjective` and the per-step `write_memory_view`; P4: remove `wake_reflection` |
| Env and engine | `custom/envs/life_env.py`: `read_query` dispatch; P4 adds `settle_pending_day_digests`. `src/worldmock/simulation_engine/engine.py` and `evidence/memory.py`: `search_memory`; P4 retires `due_reflection` and its forward in `src/worldmock/runtime/framework/read_query.py` |
| `src/worldmock/recording_engine/events/environment_events.py` | exact key set for `day_digest_generated` |
| `src/worldmock/recording_engine/reports/quality.py` | P4: `CALL_CLASSES` gains `day_digest` (:49) through a purpose branch (:795-812); `schema_version` 9 → 10 (:86); `consolidation` and `reflection` are kept as zero-count classes so historical keys remain |
| Catalog | `catalog/models.py`: optional `memory_category`; admission checks the closed set; WorldMock-Dataset package 0001 sets the override on `office-eat-lunch` |

**Events.**
- No new event types. The set of 14 is untouched (`recording_engine/events/models.py:110-114`).
- `memory_updated` keeps its shape. After P1 it no longer emits `refresh_summary` or consolidations; after P4 it no longer emits `reflection`. Validators keep accepting those values for historical runs.
- The digest goes in `environment_event`, not `memory_updated`. AS2's `MemoryUpdateEvidence.operation` is a Literal and would need an AS2 patch.

**Fingerprints.**
- The prompt SHA changes in P1–P4. The tool-schema SHA changes in P3.
- Every phase is therefore a new run identity, and runs cannot be resumed across phases. This is the existing rule (`runner.py:276-290`).

**Manifest and LabelKit.**
- Run-manifest stays 7, world 15, checkpoint 1 and export 8.
- LabelKit cuts `environment_event` data of unknown kinds down to `{kind}` (`export_source.py:167,355-357`), so digests are invisible to it.
- It parses `quality.json` without checking its version (`export_source.py:209-213`). The P4 quality bump still needs confirmation from the LabelKit side.

**AS2.** No patch. The episode store stays, because restore validation needs it (`runner.py:412-428`) and `memory_repetition_ratio` reads it.

**Docs**, changed in the same commit as code and tests (`docs/INDEX.md:18`):
- `docs/design/06-agent.md`: :46-57 tool count from 10 to 8; :216-306 rewrite; :307-346 reflection.
- `docs/design/10-runtime.md`: :79-91 tool count and channel methods.
- `docs/design/05-recording-engine.md`: the new kind and call class.
- `docs/design/04-simulation-engine.md`: midnight tasks.
- `docs/design/03-resource-engine.md`: the catalog field.

**Tests:**

| Area | Tests |
|---|---|
| Prompt and tools | `tests/unit/start_interfaces/test_system_layer_text.py` (20 → 21 rules; the `rules[10]` sleep index shifts); `tests/integration/runtime/test_seven_layer_request.py`; `tests/unit/agent_protocol/test_tool_description_references.py`; `tests/unit/start_interfaces/test_batch_shapes.py`; `tests/integration/runtime/test_life_agent_protocol.py` |
| Queries and continuation | `tests/integration/runtime/test_read_query_runtime.py`; `tests/integration/runtime/test_same_second_continuation.py` |
| Footprints and run comparison | `tests/unit/footprints/test_observation_parts.py`; `tests/unit/footprints/test_post_run_view.py`; `tests/unit/engineering/test_compare_runs.py` (path `earlier_today[].recorded_at`) |
| Memory and progress layers | `tests/unit/life/test_memory_progress_contract.py`; `tests/unit/life/test_narration.py`; `tests/integration/life/test_carried_records.py`; `tests/unit/life/test_life_memory.py`; `tests/integration/runtime/test_verified_memory.py`; `tests/integration/runtime/test_existing_relationship_subject.py` |
| Reflection (retired in P4) | `tests/integration/runtime/test_daily_reflection.py`; `tests/integration/runtime/test_strict_reflection.py`; `tests/unit/life/test_daily_reflection_trigger.py` |
| Viewer and baselines | `tools/run-viewer-context.js:39-47` with `tests/system/viewer/run_viewer_context.test.cjs`; `tests/fixtures/viewer-context.json` re-recorded with a real host; two-candidate refreeze of `tests/fixtures/dayrun-{activity,callback}-baseline.json` |

Coverage gate: branch coverage ≥85% and no uncovered public interfaces.

## 10. Phased rollout

The phases are ordered by benefit per unit of risk. Each one leaves a consistent system and can be the last.

**P0 – measurement, no runtime change.** Turn the scratch metrics into `tools/analysis/memory_pilot.py` and record baselines from V and B. The metrics:

| Metric | Definition | V | B |
|---|---|---|---|
| M1 | workday lunches whose dish appeared in a lunch in the previous 3 days | 2 of 3 (刀削面 on 10-08, 10-09, 10-12) | 0 of 3 |
| M2 | kits re-bought within 3 days | 0 of 4 | 2 of 4 |
| M3 | share of food query or purchase decisions whose reasoning or summary names a past-day dish shown in the prompt | | |
| M4 | `search_memory` calls per day and share with non-empty results | | |
| M5 | characters per layer, median / p90 / max | | |
| M6 | fresh-turn cache hit rate | 34% | |
| M7 | dollars per simulated day by call class | $0.336 | |
| M8 | regression guards: decisions per day within ±15% of 81; rejections at most 2× baseline; maximum turns ≤4; no run failure; no condensation | | |

**P1 – day boundary and past-day facts.** This phase gives the highest behavioural benefit for the lowest risk, because every addition is deterministic.
- Changes:
  - calendar day key for `earlier_today` lines;
  - 进度层 drops closed records from before local midnight;
  - 近几天 with deterministic 核验 per category and `反思` moved into it;
  - 在途订单 in 待办层;
  - kit-named dinners;
  - category derivation plus the catalog override;
  - consolidation and MEMORY.md re-renders retired;
  - the P1 rule texts.
- Deterministic acceptance:
  - every fresh call after day 1 carries D-1's 饮食 核验 with lunch and dinner named;
  - no `earlier_today` line is dated before today;
  - the dinner sentence names its kit wherever a kit exists;
  - consolidation calls = 0 and `refresh_summary` events = 0.
- Cost:
  - cutting pre-midnight records removes 18% of all 进度层 characters (39 of 305 fresh calls, 20.2k characters each), about −$0.02 a day;
  - 近几天 adds about 2k uncached characters, about +$0.015 a day;
  - retired consolidation saves $0.004 a day;
  - net about −$0.01 a day.
- Pilot: rerun the 5-day food pilot with both cards, V and B, at about $3.4 in total. Expect M3 > 30% and pooled M1+M2 ≤ the baseline pool of 4 of 14. With four lunches per run, the repeat rates are directional only; M3 is the mechanism check.

**P2 – per-round today memory and cache order.** This is the largest cost benefit, at medium risk.
- Changes: 今天 entries with fold; 进度层 open threads; layer reorder; 长期 with categories, dates, dedupe, budgets and midnight refresh; the window retired; persona deltas removed from continuation; P2 rule texts.
- Acceptance:
  - 进度层 median ≤3k and p90 ≤7k characters;
  - user message median ≤21k;
  - 记忆层 identical inside 100% of same-second chains;
  - fresh cache hit ≥55%;
  - $/day ≤ $0.20;
  - M8 holds.
- Cost model per fresh turn: about 14.0k prompt tokens, of which 9.3k hit and 4.7k miss, giving $0.0021 instead of $0.0046.
  - Run total: about $0.85–0.95 instead of $1.68, or about $0.17–0.19 a day.
- Pilot: both cards, about $1.9.

**P3 – `search_memory` and teaching.** Medium benefit: it grows with run length. Medium risk: the tool surface changes.
- Changes: the tool replaces the three memory tools, so 10 tools become 8; the final rule texts; readonly rules.
- Acceptance:
  - search validation errors ≤5% of calls;
  - non-empty results ≥70%;
  - no events between a search and the next request other than model events.
  - Usage is observed, not gated.
- Pilot: both cards for 5 days, plus one **10-day** run from 10-08 to 10-18. The longer run exercises the last-3-workdays rule over a weekend, the overtime days 10-14 and 10-15, the team dinner on 10-16, and searches older than 3 days. About $1.9 plus about $1.8.

**P4 – daily 自述 and retiring reflection.** Lower benefit; it is cleanup plus subjective quality.
- Changes: the day digest call, the `day_digest` call class, quality schema 10, reflection and its channel method retired, `反思` removed from 近几天.
- Acceptance:
  - exactly 1 digest call per day;
  - retries ≤1 a day;
  - reflection calls = 0;
  - every 自述 ≤120 characters;
  - $/day within $0.003 of P3.
- Pilot: both cards, about $1.9.
- Gate: if the P3 pilot shows reasoning almost never cites 回顾, ship P4 deterministic-only, leaving 自述 empty and skipping the quality bump.

**P5 – conditional.**
- Month and year 回顾 for runs of at least 2 months.
- plan_day reading digests instead of raw history: its input today is 12.5k tokens.
- Overrides for other personas.
- 工具层 slimming. Its travel group is 4.2k characters and changes in 61% of pairs.

**Every phase also needs:** the docs/code/tests single-commit rule, the two-candidate day-run refreeze (about $0.7), and a viewer-context re-record. The whole programme costs roughly $15 in model spend; the real cost is wall-clock time.

## 11. Risks and decisions for the owner

| # | Decision or risk | Recommendation |
|---|---|---|
| D1 | What a round is | Use the code's decision: any world request ends it. `report_status` text stays progress, not memory. |
| D2 | Closed facts in memory-today, labelled 核验, rather than in progress | Yes. It avoids duplicating about 2.2k characters a day, and progress keeps only open things. |
| D3 | Three categories, or a fourth for rest | Three, with sleep inside 生活. |
| D4 | Office lunch and other personas | Use the catalog `memory_category` override without a format bump; confirm WorldMock's catalog policy allows this. The fallback is a heuristic ("first interact after a meal receipt"), which is fragile. |
| D5 | Month and year tiers | Virtual for runs under 2 months; model-written only for longer runs. |
| D6 | "Last 3 days" for work | The last 3 days that had work. |
| D7 | Model-written daily 自述 or deterministic only | Model-written, gated on the P3 pilot's citation rate. |
| D8 | plan_day reads digests | Not before P5. |
| D9 | Turn budget | Keep 16. |
| D10 | Readonly path | Keep and update; retire only if it is confirmed dead. |
| D11 | AS2 episode writes | Keep them; analysis metrics and restore depend on them. |
| D12 | Language of layer keys | New memory and to-do keys in Chinese, as shown. Do not rename the English keys of other layers now. |
| R1 | Removing closed records from 进度层 may drop cues the model relied on | Watch rejections and repeated starts in P2. |
| R2 | The model may over-index on memory | The rule says memory is reference only; compare M1, M2 and card adherence. |
| R3 | About 20% of activities are ambiguous to the category rules | This only changes which bucket a 回顾 lands in; the facts are always shown. |
| R4 | Assumptions about DeepSeek prefix caching | Verify with M6 in P2. |
| R5 | A digest validation failure fails the run, as plan_day does | Keep validation to lengths and nulls only. |
| R6 | Cold start | 近几天 is empty or partial for the first 3 days of every run and branch. |