# Memory categories and time tiers: what the current system supports (HEAD f67c25e5)

`DS:` means `/Users/atishoo/Project/WorldMock-Dataset/environment/0001-white-collar-lianqiu/package/`. `V` and `B` are the variety and baseline runs. The helper scripts are in `/private/tmp/claude-501/-Users-atishoo-Project-WorldMock/ccadbaa7-e5eb-4f09-8998-6753571863ec/scratchpad/memgap/`. The runs' `input/catalog` is identical to `DS:catalog`.

## 1. Category signals that exist today

**Activity kinds**
- The closed set is sleep, travel, interact, purchase and query (`src/worldmock/state/world/kinds.py:10-17`).
- Interact without an app means doing something at a location (slot "first"). Interact with an app, purchase and query take slot "second" (`src/worldmock/exchange_engine/conditions/activity_rules.py:8-33`).
- Sessions are either stay or transit (`kinds.py:73-78`).

**Location content units**
- Units are looked up by location *kind*. A series counts as one unit (`src/worldmock/simulation_engine/activities/content/location_activities.py:40-51`).
- Concluding an activity records the episode that is current at that moment. So the dinner series 做饭 (cook→eat→dishes) shows up as `home-dinner-3-dishes` 5 times in each run, and never as cook or eat. Classification therefore has to work on the unit, not the `content_id`.
- Structured food signals:
  - `completion_effects.food` (`resource_engine/catalog/models.py:199-205`). It is set only on `home-dinner-2-eat` and `restaurant-team-dinner`.
  - `consumes_state` ∈ {groceries, team_dinner} (`DS:catalog/content-catalog.jsonl`, `callbacks.jsonl:2-3`).
- The 20 location-content rows are:
  - 研发中心: 5 `work-*` rows plus `office-eat-lunch`.
  - 住宅: 8 rows (wash, chores, laundry, the dinner series).
  - 健身房/公园: 4 rows.
  - 商场: 1 row. 中餐厅: the team dinner.
- **`office-eat-lunch` has no food signal at all.** It sits at the workplace kind, has no completion effect, and its calories come from the separate `goods_received` event.

**Apps**
- `role` is either content or function (`models.py:59-66`). Only alipay (purchase) and amap (driving) are function apps.
- Every ordering app is a content app: meituan-takeaway, eleme, hema, luckin, jd, pupu, meituan-grocery, kfc, dianping (`DS:catalog/app-catalog.jsonl`). An app becomes an order channel only through `location.app_ids` (`DS:catalog/location.jsonl`). So role cannot separate food apps from media apps.
- Content inside store apps covers mixed topics. For example, jd has electronics, tissue and detergent guides; eleme has lunch and daily-goods guides.
- `dingtalk-clock-in/out` has `prerequisite.location_kinds=["研发中心"]` and opening hours that mirror the duty calendar. That is a deterministic work signal.
- `phone` has 3 calls to mother, each with `social_relief=high`.
- `social_relief` is not a clean social signal: `work-requirements-review` is "medium".

**Goods**
- The closed set is snack, meal, drink and goods (`src/worldmock/exchange_engine/definitions/types.py:8`). Their labels are 零食/食品/饮料/商品 (`types.py:11-13`).
- **22 of the 25 "goods" items are dinner ingredient kits.** The other 3 are detergent, tissues and a toothbrush. Kits can only be identified as food indirectly: each appears in `groceries.reset_on_goods` (`DS:catalog/callbacks.jsonl:2`), and the dinner content consumes `groceries`.
- Receipt effects differ by category. Meal and snack provide energy; drink and goods only relieve relaxation (`simulation_engine/commerce/fulfillment/receipt.py:70-86`, `exchange_engine/commerce/operators.py:27-35`).
- The quality report counts only meal and snack as food (`recording_engine/reports/quality.py:272-278`).
- A query names a target category. "Goods" (商品) queries on hema return kits and household items together (V activities `data.targets`).

**Work**
- Work comes from three things:
  - the persona's `work_schedule`, with per-date exceptions (`configuration/persona/work_schedule.py:34-39`);
  - duty status (`:56-65`, `:94-102`);
  - duty edges (`simulation_engine/scheduling/clock/duty_edges.py:23-50`).
- `workplace_location_id` is resolved by name (`configuration/inputs/environment.py:41,167-173`) but nothing else reads it.
- **There is no work-task or work-review module at HEAD.** A grep for review/acceptance finds only purchase and metrics code. CLAUDE.md's "work and work review" is stale.

**Goal intents**
- There is an 11-value closed set: 睡眠, 晨间例程, 通勤, 工作, 用餐, 休息, 娱乐, 社交, 家务, 运动, 外出 (`state/planning/schedule.py:58-70`). Activities can carry it as `plan_goal_intent_kind` (`state/world/activity.py:53`).
- **It is null on 147/147 (V) and 143/143 (B) activities.** The model never bound an activity to a goal, so it cannot serve as a category signal. Day-plan goals themselves do carry an intent.

**Subjective memories**
- Each conclude item names its activity (`state/interaction/control_items.py:28-44,113-121`).
- Matching `activity_name` plus `concluded_at == request.occurred_at` linked **235/235 (V) and 222/222 (B)** conclusion episodes to exactly one activity, with no ambiguity.
- Bedtime reflection insights carry `refs` to 2–4 episodes. In V, 13 of 20 reference episodes from a single category and 7 of 20 span several.

## 2. Prototype classification of the real runs

Rules, applied in order:
1. sleep → rest.
2. purchase or query → food if the goods are meal/snack/drink or a kit, otherwise errand.
3. Location content → food if it has a food signal; work if it is at the workplace kind; otherwise life.
4. dingtalk → work; phone → social; any other app → media.
5. home↔workplace travel → commute.

| | V (147 acts) | B (143) |
|---|---|---|
| food (purchase/query/dinner) | 27 + 3 hema queries | 22 |
| work (dingtalk + workplace units) | 8 + 27 (4 of them are `office-eat-lunch`) | 7 + 23 (3 lunch) |
| media (content apps) | 37 | 35 |
| household | 17 | 22 |
| sleep (4 office naps each) | 10 | 10 |
| travel (commute / outing) | 8 / 2 | 9 / 5 |
| social / exercise / weather | 2 / 1 / 5 | 3 / 2 / 5 |

- All 21 orders in each run were food, including 5 kits per run. Neither run bought a non-food item.
- **Every stay session mixes 4–7 categories.** For example, the 10-12 office stay holds work 10, food 4, media 1 and sleep 1. This is the "muddle" in concrete form.
- Per local date there are 25–32 concluded activities. On the rest day (10-11), "life" alone reaches about 21.

## 3. Category signals and edge cases

- **food**
  - Signals: goods category is meal/snack/drink; goods is in the reset list of a state that a food-effect unit consumes; the unit has `completion_effects.food` or consumes a food state.
  - Edges:
    - `office-eat-lunch` has no signal.
    - The team dinner is food, social and colleagues at once.
    - Cooking videos (the 家常菜教程 series) are media, not food.
    - Drinks count as food for memory but not for calories.
    - Buying at the office is still food.
- **work**
  - Signals: content at the workplace location kind; app content whose prerequisite names that kind; commute (home↔workplace).
  - Edges:
    - Naps and media at the office are not work.
    - This rule does not carry to other personas:
      - Sales (0002) has work content at 总部园/科技园, not at its workplace kind 写字楼.
      - Students do library self-study away from their workplace kind (0003: 图书馆 vs 教学楼; 0005 is similar).
      - The homemaker (0004) has no `work_schedule` at all.
- **rest**
  - Signal: `kind=sleep`, at home or as an office nap.
  - Edge: this is the only kind that crosses midnight (5 of 147 activities have different start and conclusion dates, and all of them are night sleeps).
- **life** (household, errands, media, social, exercise, weather, outings)
  - Edges:
    - It is the largest bucket, and media makes up about 58% of it.
    - 41 interact activities in V were cancelled, not completed.

## 4. Day boundaries the code already uses (four different ones)

1. **Memory layer:**
   - "Today" means sessions whose `ended_at` falls on today's date (`simulation_engine/observation/layers/memory_layer.py:106`).
   - Overnight home stays therefore land on the next day. For example, the 10-11 15:41→10-12 08:03 stay counts as "today" on 10-12.
   - The current session is never in memory.
2. **Midnight planner:**
   - It reviews from the previous local 00:00 to now (`simulation_engine/scheduling/plans/day_plan_inputs.py:109-115`).
   - Activity endings are assigned by the time of their end event (`:204-207`). Experience intervals are open on the left (`:49-51,169`).
3. **Bedtime reflection:**
   - It runs at the first wake of the day and is filed to the wake date minus one (`simulation_engine/evidence/memory.py:13-34`).
   - Observed run times were 07:30–08:02, so it arrives **about 8 hours after** the midnight plan.
   - V has no reflection on 10-08. B filed one to 2026-10-07, which is before its run started.
4. **`plan_date`** is the local date (`scheduling/plans/day_planning.py:67-69`).

The persona's bedtime window is 24.0–24.33 (`DS:persona.toml:21`). Observed bedtimes were 22:08–00:15.

**Window crowding (observed in V's stored observation parts):**
- `earlier_days` (the reflections) appears at wake and vanishes once the office session ends at about 17:30 each workday. It stays gone through the evening.
- This is because `_fit` drops all older-day notes first and then shrinks the oldest today entry (`memory_layer.py:320-336`, 8000-char window `:36`).
- Example: at 10-12 08:33 the layer held 4 dates; at 18:00 it held none, and the 08:03 home-stay entry was also gone.
- The same thing will happen to a "last 3 days" tier unless each tier has its own budget.

## 5. Calendar and how often the tiers would fire

- The package config covers 2026-09-28 00:00 → 10-28 00:00 (`DS:config.toml:4-5`). Both evidence runs cover 10-08 → 10-13.
- Duty calendar computed from `DS:persona.toml:80-135`:
  - 19 workdays and 12 rest days.
  - National holiday 10-01→10-07.
  - Saturday 10-10 is a make-up workday.
  - 10-14 and 10-15 are overtime days ending 20:30.
  - The social dinner is on 10-16 (`DS:config.toml:15-19`, `callbacks.jsonl:7-10`).
  - Rent is due on day 1 and salary arrives on day 15 (`callbacks.jsonl:5-6`).
- **The month tier fires once**: a "September" entry holding only 09-28→09-30. October never becomes a "previous month" before the run ends. **The year tier never fires.**
- The run starts on a Monday, so ISO weeks line up: 4 full weeks plus 10-26 and 10-27.
- **The owner's ladder has a gap.** On 10-20, days 10-01→10-16 are neither in the last 3 days nor in a previous month.
- **Calendar "last 3 days" fails for work after the holiday.** On 10-08 those days are 10-05→10-07, all holiday, so the last real workday (09-30) is out of view.

## 6. What the midnight planner already builds and a daily rollup could reuse

`plan_inputs` (`day_plan_inputs.py:83-106`) assembles a deterministic review of the previous day, with source event ids:
- **动作结果** (action results): settled lines plus deliveries and activity endings, as parallel text and source lists (`:173-209`).
- **收尾记录** (conclusion records): summary plus memories, marked as unverified self-report (`:217-247`). It appends one source per *request*, not per item (`:246`).
- **Verified experiences** (played, received, arrived) (`:156-170`).
- **上一天的目标** (previous day's goals), each with its intent (`:273-296`).
- In-transit orders and personal errands.

None of it carries a category or an `activity_id` on each row. A category can be derived from each source event (activity, order, goods) using the rules in §3. It reads the full history every midnight (`:77-80`).

## 7. Recommendation

- **Categories: food, work, rest, life**, with a deterministic subtag on each entry (sleep, nap, commute, chores, errand, media, social, exercise, outing) so `search_memory` can filter at either level.
  - Rest stands on its own because it is the only kind that crosses midnight. It is tiny and numeric, so its rollup can be built without a model call.
  - Media becomes a subtag of life so it doesn't swamp the life stream.
- **Make the signal explicit in the catalog.** Add a fail-closed `domain` field on Content rows (and optionally Goods/App) and check it at admission. This would need a catalog format bump (`models.py:51`) and a change to `resource_engine/registration/admission.py`.
  - Today's signals can't tell `office-eat-lunch` apart and don't carry to other personas.
  - Use the derived rules above only as a cross-check.
- **Subjective memories inherit their category** through the activity they name (100% linkage in both runs). Reflections take the union of the categories they reference, so they can carry several.
- **Day key = the local date of each round's end event**, using the planner's rule that an interval ending exactly at midnight belongs to the earlier day. That makes the daily rollup's window identical to the planner's.
  - Key entries by round, not by session: at midnight the home session is still open.
  - The reflection is filed later, so either compact on waking or attach the reflection to that day's entry afterwards.
- **Tiers:**
  - Today: per round.
  - Days D-1..D-3: in the prompt. For the work stream, use the last 3 days that actually have work entries.
  - Every day: kept as a searchable entry.
  - ISO weeks: weekly rollups.
  - Month and year rollups: only worth having for runs longer than 30 days.
  - Give each tier its own character budget.

## Open questions / uncertainties

- Should commute count as work or as life? Is an office nap rest, or work-day context?
- Should store-app content browsing (jd/eleme guides) and cooking videos stay media?
- The run's code (`code_sha256` ec5031…) may differ from HEAD. The trimming behaviour was confirmed only from stored observation parts.
- I didn't check how reflection selects its episodes, so whether a wake-time compaction can see all of the previous day is unverified.
- How many turns a round gets: the config sets `max_react_turns=16` (`DS:config.toml:30`); the owner suggested 32.
- `_conclusion_records` gives one source per request, so a category per item needs the name resolution from §1.