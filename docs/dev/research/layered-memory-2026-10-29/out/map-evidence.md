# Memory redesign: sizing evidence from the variety and baseline runs

Both runs are 5 simulated days, 2026-10-08 00:00 to 10-13 00:00 CST, per `run/manifest.json` `window`. All figures are given as **variety / baseline**. The helper scripts are read-only and live in `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/` (`analyze.py`, `mem.py`, `cls.py`, `cache.py`, `day.py`, `reorder.py`, `refl.py`, `cross.py`).

**Terms.** The owner's "round" is one `decision_started` event, and its id ends in `…:agent:1:order:N`. The code uses "round" for the scheduler round (`round_id`). The owner's "turn" is one model call (`model_request`). The owner's "session" matches the code's `SessionState`: a stay or a trip.

## 1. Rounds, turns and world requests

**Method.** Decisions are counted per local day. Awake hours are 24 h minus the sleep segments in `export/activities.jsonl`. Model calls are grouped by the decision prefix of `call_id`, and world requests by `action_requested.decision_id`.

| day | rounds | awake h | rounds per awake h |
|---|---|---|---|
| 10-08 | 81 / 84 | 14.9 / 14.5 | 5.4 / 5.8 |
| 10-09 | 84 / 80 | 14.5 / 15.0 | 5.8 / 5.3 |
| 10-10 | 83 / 79 | 14.6 / 15.3 | 5.7 / 5.2 |
| 10-11 (Sun) | 69 / 85 | 14.6 / 15.4 | 4.7 / 5.5 |
| 10-12 | 89 / 81 | 15.2 / 14.6 | 5.8 / 5.6 |
| **total** | **406 / 409** | 73.8 / 74.9 | **5.5 / 5.46** |

- **Turns per round.** 402/406 and 396/409 rounds use exactly one model call. The most any round used is 2 (variety) or 3 (baseline). The budget is `max_react_turns=16` (`src/worldmock/configuration/inputs/models.py:223`, bounds 1–64; the run manifest records 16).
  - A 32-turn budget would therefore not bind at the current behaviour.
- **Same-moment rounds.** 103 rounds start at the same instant as the previous one: 303/306 distinct instants for 406/409 rounds (`quality.json` `same_moment_round_counts`).
  - These make up the `turn_with_history` class. They carry the previous round's assistant and tool messages (`life_prompt.py:97`, `quality.py:806`).
- **World requests per round.** 386/406 rounds send one request and 20 send two. The two-request rounds are always the pair `start_main_activity` + `start_side_activity`.
- **Which tool ends the round:**

| tool | variety | baseline |
|---|---|---|
| conclude_activity | 137 | 137 |
| report_status | 136 | 145 |
| start_side_activity | 82 | 69 |
| start_main_activity | 71 | 76 |
| pause_activity | 0 | 1 |
| revise_day_plan | 0 | 1 |

  About a third of rounds end with `report_status`, which the owner's "start/pause/conclude ends a round" does not mention. Pause is almost never used.
- **Memory tools are almost unused.** There were 2 `memory_search` calls in variety and 0 in baseline; `memory_recent` and `memory_time_range` were never called.
  - Both searches returned `episodes: []`. The queries were `"盒马 食材包 土豆烧牛肉"` and `"晚饭 到家 做饭"`, at 10-12 18:00.
  - AS2 search matches the whole query string as one substring of each serialized episode (`vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/memory.py:646-654`). A query of several space-separated keywords therefore can never match.

## 2. How big one entry per round would be

**Method.** For each session I took the first `earlier_today` rendering in the prompts. That copy is the newest entry, so the trim never touches it. I parsed the event time of each settled line, which uses the `activity_sentence`, `purchase_sentence` and delivery formats (`settled_text.py:63-81, 112-121, 57`). Each line was given to the first round at or after its time. Separately, I counted the model-written text in each world request: summary, memories, views and self_note.

| measure | variety | baseline |
|---|---|---|
| settled line chars (mean / median / max) | 59 / 51 / 136 | 58 / 47 / 142 |
| settled lines per complete day | 35–38 | 33–37 |
| settled chars per day | 2,066–2,351 | 1,739–2,407 |
| rounds that receive any settled line | 147/378 (39%) | 140/382 (37%) |
| chars in those rounds (mean / p90 / max) | 70 / 123 / 201 | 70 / 134 / 181 |
| model-written chars per conclude (mean) | 176 | 160 |
| model-written chars per report_status (mean) | 121 | 99 |

Settled text plus model-written text, per round:

| day | variety chars per day (per round, p90, max) | baseline chars per day (per round) |
|---|---|---|
| 10-08 | 8,051 (99, 216, 510) | 8,039 (96) |
| 10-09 | 9,908 (118) | 8,912 (111) |
| 10-10 | **12,209 (147, 326, 422)** — worst day | 9,321 (118) |
| 10-11 | 9,775 (142) | **10,289 (121)** — worst day |
| 10-12* | 11,544 (130) | 10,103 (125) |

\*The settled part of 10-12 is under-counted, because the evening home session never ended before the run stopped.

- At about 0.42 prompt tokens per char (measured on fresh calls), a full-day "today" list is roughly **1k tokens** with settled facts only, or **3.4–5.1k tokens** if model summaries are included.
- **Today's 记忆层 for comparison:**
  - Size is mean 3,882 / 4,430 chars and max 7,825 / 7,878.
  - The window is `MEMORY_WINDOW_CHARS=8000` (`memory_layer.py:36`).
  - In baseline, the trim (`memory_layer.py:320-336`) shrank entries in 47 requests.
- **"Today" holds yesterday.** `earlier_today` is made of sessions that ended today (`memory_layer.py:104-107`). The overnight home session therefore carries the previous evening.
  - 50/177 and 44/169 of the rendered settled lines (37% / 35% of their chars) actually happened the previous calendar day.
- **"Earlier days" holds no facts.** Every one of the 503 / 753 `earlier_days` entries seen across all prompts had empty `settled`.
  - Only still-open orders would appear there (`memory_layer.py:225-237, 252`), and none were open at day end.
  - So, in practice, that section is just bedtime reflections.

## 3. Classifying into food / work / life

**Rules, using available data only:**
- **Purchases:** goods category from `merchant-catalog.jsonl`.
  - `meal`/`snack` → food; `drink` → ambiguous; `goods` → life.
  - 食材包 kits are category **`goods`**, the same as 洗衣液 and 纸巾. They can only be told apart by name or by `callbacks.jsonl` `groceries.reset_on_goods`.
- **Place units:**
  - `work-*` units → work.
  - The cook/eat episodes of 做饭 and 吃午饭 → food. 吃午饭 has **no** `completion_effects.food`; only 吃饭 and 和同事聚餐 do (`location_activities.py:98,148-160`).
  - 洗碗收拾厨房 → ambiguous.
- **Apps:** 钉钉 → work; shopping apps used as content → ambiguous; content apps at the office → ambiguous.
- **Travel:** home↔office → ambiguous (commute).
- **Everything else** → life.

| measure | variety | baseline |
|---|---|---|
| activities: food / work / life / ambiguous | 22 / 31 / 66 / **28 (19%)** | 16 / 27 / 70 / **30 (21%)** |
| reasons for ambiguous activities | commute 8, content app at office 8, dishes 5, coffee 4, query mixing goods+kit 3 | commute 9, office app 10, dishes 5, coffee 3, travel to a food place 2, mixed basket 1 |
| paid orders: meal / kit / drink / snack | 10 / 5 / 4 / 2 | 10 / 5 / 4 / 2 |
| rounds in one clean class (life / work / food) | **323/406 = 79.6%** (159 / 124 / 40) | **310/409 = 75.8%** (167 / 114 / 29) |
| rounds ambiguous / multi-class / rejected | 52 / 26 / 5 | 73 / 23 / 2 (+1 plan) |
| conclude memories by concluded activity: life / work / food / ambiguous | 71 / 44 / 19 / 26 | 76 / 43 / 16 / 34 |
| conclude views by concluded activity: life / work / food / ambiguous | 30 / 18 / 12 / 16 | 25 / 11 / 4 / 15 |

- Every conclude `activity_name` resolved to an activity (0 unresolved), so a category can be inherited from the activity.
- Inheriting it misfiles content, though. For example, a 小红书 conclude memory about waiting for and eating breakfast is filed as life.
- AS2 `episodes.jsonl` rows carry `type`, `keywords`, `text` and `source`, but no reference to the activity. Joining an episode to its activity needs a text match.

## 4. Prompt caching

| call class | n | hit / (hit+miss) | mean prompt tokens |
|---|---|---|---|
| fresh_decision_turn | 305 / 309 | **0.336 / 0.361** | 19,783 / 18,166 |
| turn_with_history | 105 / 114 | 0.808 / 0.806 | 25,803 / 24,132 |
| plan_day | 9 / 9 | 0.49 / 0.44 | 12.5k / 10.6k |
| consolidation | 31 / 30 | 0 / 0 | ~1.2k |
| **overall** | 454 / 467 | **0.480 / 0.504** | — |

- **What is cached today.** Every fresh call uses the same single system message (3,377 chars) and tools array (11,221 chars). Fresh hit tokens cluster at 5,504–5,632, which is just that system + tools prefix, and at 7,296–7,680.
- **How often each layer is byte-identical to the same layer in the previous fresh request:**

| layer | variety | baseline | mean chars (variety) |
|---|---|---|---|
| 工具层 (tool) | 0% | 0% | 8.7k |
| 人设层 (persona) | 76% | 82% | 3.2k |
| 记忆层 (memory) | 93% | 90% | 3.9k |
| 待办层 (to-do) | 98% | 89% | 0.76k |
| 环境层 (environment) | 1% | 1% | 0.38k |
| 进度层 (progress) | 0% | 0% | 14.6k (max 42.8k) |

- **Where the stable prefix ends.** The first differing layer is 工具层 in 303/304 and 308/308 pairs; its purchase group changes in every pair. The common prefix of the user message has a median of 101 chars.
- **Layer order.** The order is set at `life_prompt.py:156`. Moving persona, memory and to-do ahead of 工具层 (simulated on the same data) gives:
  - a stable prefix of 5.5–6.5k chars (about 2.3–2.7k tokens) per fresh call;
  - about 0.7–0.84M tokens per run moving from miss to hit, out of 4.0M / 3.6M fresh miss tokens;
  - my estimate of savings at list prices is about $0.12–0.24 per run.
- **Effect on the proposed per-round entries.** A "today" list that grows every round, placed inside this stable block, would break the prefix at that layer on almost every round.

## 5. Cost (`quality.json` `model_usage`)

| class | variety USD | share | baseline USD | share |
|---|---|---|---|---|
| fresh_decision_turn | 1.418 | 84.3% | 1.302 | 82.5% |
| turn_with_history | 0.218 | 13.0% | 0.232 | 14.7% |
| plan_day | 0.022 | 1.3% | 0.021 | 1.3% |
| consolidation | 0.019 | 1.1% | 0.018 | 1.1% |
| reflection | 0.005 | 0.3% | 0.005 | 0.3% |
| **total** | **1.683** (9.13M tokens) | | **1.578** (8.75M tokens) | |

- That is about $0.33 per simulated day, and about $0.0046 per fresh round.
- Memory upkeep, i.e. consolidation plus reflection, costs about 1.4% of the total.

## 6. Bedtime reflections and meals

- **Reflection volume.** There were 4 / 5 reflection calls, each writing 5 episodes of about 54 chars, so roughly 230–310 chars per day.
  - Baseline filed 3 of its episodes under 2026-10-07, from the bedtime at run start.
- **Topics, by keyword match:**

| | food | work | both | neither | total |
|---|---|---|---|---|---|
| variety | 9 | 10 | 5 | 6 | 20 |
| baseline | 6 | 9 | 2 | 9 | 22 |

  Single episodes often mix topics, e.g. "饭前把钉钉消息清干净…". Reflections are cross-cutting, not split by category.
- **Meals from events.** Each day has 2–4 meal/snack receipts or completed 吃饭 episodes, plus about one coffee and one kit purchase.
  - Lunches are always named; 番茄牛肉刀削面 appears 3× in variety.
  - The home-cooked dinner is **never named**. The 吃饭 episode consumes the `groceries` state, so the dish is only recoverable by joining it to the previous evening's kit receipt.
  - The 10-08 dinner is unknown, because the run starts with groceries = 1 and no kit on record.

## Open questions / uncertainties
- The token figures use a single 0.42 tokens/char ratio, measured over the whole prompt. Per-layer tokenization of JSON-heavy layers will differ.
- The cache simulation compares only consecutive requests and whole layers. It ignores DeepSeek's 64-token granularity and cache eviction.
- The food/work/life rules are mine. "Life" absorbs content-app use at home, even when the text is about food.
- 10-12 totals are truncated, because the open final session never rendered.
- I assumed the AS2 search in `vendor/` is the build that actually ran; the patches in `patches/agentsociety2/SERIES` were not checked.
- Whether `report_status` should count as ending a round in the owner's model is undecided.