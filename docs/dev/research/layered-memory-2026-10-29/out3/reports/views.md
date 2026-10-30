## Item 17, option B ("search only"): how the persona's learned views fare once they leave the prompt

Scripts and JSON outputs are in `/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/views/`. All databases were opened read-only and no repository or run files were changed. "字" means `len()` characters. To convert characters to tokens I fitted `prompt_tokens ≈ 0.791·hanzi + 0.31·other chars + 3.9k` over the 305 fresh first-turn requests in V (R² = 0.9995; B gives the same ratios). View text is 91% hanzi, so it costs about **0.75 tokens per character**. At that rate the caps are roughly: one today entry (120字) ≈ 90 tokens, a day digest (200字) ≈ 150, a month digest (800字) ≈ 600, a year digest (2000字) ≈ 1.5k.

### 1. What the persona layer carries besides the card (all of this leaves the prompt)

**What is in it.** Only two parts change during a run:
- **`preferences[]`**: each entry is `{subject, preference, noted_on}` (`prompt_schemas.py:147-148`).
  - It is built from successful `conclude_activity` views. Each exact `subject` keeps only its latest text, and `noted_on` is the local date of that conclude (`memory_layer.py:127-144`).
- **`relationships[].view`**: the latest view per person, with no date (`memory_layer.py:138-139,143`; `life_persona.py:39,117-123`; schema `prompt_schemas.py:144-146`).
  - The `_relationships` branch that would add people not on the card cannot fire in practice. A relationship view may only name a known person (`life_agent.py:484,869-880`), and the known set is the card names plus people who already have a view (`life_persona.py:65-69`), so it never grows beyond the card.

Everything else in the layer comes from the card. Only those card fields are held to the 600-hanzi budget (`life_persona.py:49-53`); views have no cap and never expire. The `importance` and `keywords` fields of a view (`requests.py:476-499`) are never rendered.

**Where views are written and rendered.**
- Written by: the `views` field of a conclude (`requests.py:513-516`; at most 8 memories plus views per conclude, `:546-548`; at most 1,000 characters of text each, `:432-434`).
- Read into the observation as a private part (`observe.py:68`; `observation_parts.py:21-22`; forbidden as a prompt key, `narration.py:77`).
- Read by LifeAgent and rendered (`life_agent.py:479-487,728-735`; `life_persona.py:29-54`).
- Rule text: `life_prompt.py:75-78`, and read-only rule `:107`. In `06-agent.md` they appear only in the rule summary at `:74`.

**Inside same-second chains.** Views are already visible from the very next decision. `layer_changes` sends a `preferences`/`relationships` delta (`life_continuation.py:7,49-52,111-140`). In V, 61 of 101 chain decisions carried such a delta (median 106 characters).

**Views appear nowhere else in the prompt.** In fresh requests, 0 of 5,709 (view, request) pairs in V, and 0 of 4,239 in B, had the view text in any layer other than 人设层.

**Size per decision.** V has 406 decisions. My reconstruction of the persona layer matches all 305 fresh requests byte for byte.

| | median | p90 | max |
|---|---|---|---|
| Whole persona layer (chars) | 3,072 | 4,946 | 5,413 |
| Card-only part (chars) | 1,168 | 1,168 | 1,168 |
| **Leaves the prompt (chars)** | **1,904** | **3,778** | **4,245** |
| Leaves the prompt (hanzi) | 841 | 1,649 | 1,874 |
| Leaves the prompt (tokens) | ≈1.0k | ≈2.0k | ≈2.2k |
| …of which preferences JSON | 1,864 | 3,697 | 4,154 |
| …of which relationship views | 42 | 93 | 101 |

- Views make up 62% of the persona layer at the median and 76% at p90.
- The amount grows by about 0.9k characters a day. The daily median rises 395 → 929 → 1,904 → 3,088 → 3,778, ending with 40 preferences.
- Over the run V wrote 76 views (69 preference, 7 relationship) on 42 distinct subjects.
- B: 1,121 / 2,723 / 2,878 characters; 55 views.
- On fresh requests only, my maxima match the proposal's (5,304 and 4,045). The proposal's medians were computed over a different set of requests.

### 2. How much survives the 120 / 200 / 800 / 2000字 caps

Rendered as `看法·对象：text`, one view is median 52, p90 65, max 78 characters. No view exceeds 120 on its own.

**Today entry (120字).**
- 75 of 137 concludes carry views, totalling a median 52 and max 107 characters per decision.
- In all 75 of those decisions, the verified line (about 59 characters per item, from fact F16 in the brief) plus the summary (median 112, p90 168) already reaches 120.
- So if the entry shows the summary before the views, **no view text is ever visible**.
- If 看法 comes directly after 核验, the whole view fits in **62 of 75 (83%)** of these entries; B 42 of 55 (76%).
- The full self-report never fits (0 of 75).
- Recommendation for capped entries: order them 核验 → 看法 → summary (truncated). The digest and today's search still read the full host-side text.

**Day digest (200字 per category).** Views written per category-day, in rendered characters:

| Category | median | mean | max | days views alone exceed 200 |
|---|---|---|---|---|
| 饮食 | 154 | 193 | 279 | 2 of 5 |
| 工作 | 166 | 215 | 404 | 2 of 5 |
| 生活 | 348 | 377 | 877 (Sunday 10-11, 16 views, 4.4× the cap) | 3 of 5 |

- In B: 生活 is over on 3 of 5 days (max 771), 工作 on 3 of 5 (211–226), 饮食 on none.
- The food digest also has to list every food received plus the grocery kit used. Those names alone take 26–44 characters a day.

*Measured survival.* The only digests that exist are the nine guard-compliant, hand-written examples in the proposal (lines 499-511).
- They contain 237 characters of attitude-bearing clauses against 2,555 characters of that day's views: **9%** overall (3–29% per cell, median 11%).
- They name 29 of 44 view objects, but mostly as events rather than attitudes. The 10-09 food digest keeps "汤头开胃、面也顶饱" and drops "还会再点".

*Plausible upper bound.* Suppose an attitude-aware prompt gives about half of the 200 to attitudes, at about 17 characters per object-plus-stance clause, so 4–6 objects per cell.
- Objects kept: 72–88% overall (饮食 94–100%, 工作 74–95%, 生活 57–77%).
- Content kept: about **24% of view characters**.

So the plausible range is **10–25% of view content and two-thirds to four-fifths of view objects**. The squeeze is worst for **生活**, then 工作 on heavy days (10-10 and 10-12 at about 2× the cap), then 饮食, whose budget is partly taken by the coverage list.

**Month and year.**
- Raw views per category per month in V: 饮食 5.8k, 工作 6.5k, 生活 11.3k characters. The 800 cap is 14%, 12% and 7% of that; the year cap is 2.8%, 2.5% and 1.5% of the yearly raw volume.
- Effective compression is 13% for a month (31 × 200 → 800) and 21% for a year (12 × 800 → 2000).
- Only recurring attitudes can last. In V, 16 of 41 objects recurred on 2 or more days and 6 on 3 or more (哔哩哔哩 5 days; 功能开发与自测 and 王磊 4; 刀削面, 自己做晚饭 and 网易云音乐 3). The other 25 one-day attitudes live only in their own day digest.

### 3. Keep a dated, deduplicated views list as a search-only source?

**For:**
- It keeps the persona's own words verbatim, with dates; digests keep 9–25%.
- "What did I think of X" gets an exact answer.
- It is deterministic, needs no model call, and costs nothing in the prompt.

**Against:**
- It would be the only routine row-shaped past memory. The other raw rows appear only for a 未整理 cell, as a failure fallback. That cuts against the ruling that past memory is written up as paragraphs.
- It only answers keyword queries (draft line 361). With no cue in the prompt the model has to guess the keyword, and V searched twice in 5 days.
- Keeping only the latest view per object hides how attitudes changed, and the merge heuristic is known to misfire.
- It keeps stale and fabricated views verbatim. 「自己做的番茄炒蛋配清炒青菜」 was still being echoed on 10-12.
- It is a third copy of each view, alongside the today entry and the digest.

**Recommendation: drop it.** Carry attitudes through the digests (section 5). Report how well digests do this as a new M10 figure, "看法对象覆盖率" (the share of a day's view objects that the digest names; 66% in the examples). Reconsider the list only if the pilot metric in section 5 shows a shift the owner does not want. The 「长期装不下的看法」 parts of the search table (proposal lines 358, 360-361) and of rules[1] should go.

### 4. How much V's behaviour leaned on views

**Broad count.** 250 of the 392 decisions that had views present mention a then-present preference subject. 149 mention a subject that is not on the card. Only 27 mention a subject that appears nowhere else in the request.

**Strict count** (a view visible only in the persona layer, and either a ≥8-character wording echo found nowhere else in the request, or a non-card subject named nowhere else):
- **85 of 406 decisions (21%)**. That is 17 of 127 start decisions (13%), 47 of 130 concludes, and 20 others. The signal is in the reasoning in 65 cases and in the written arguments in 33; 59 of the echoes are 10 characters or longer.
- B: 85 of 409.

**Age of the view relied on.** Same day 24; 1–3 days 53; 4 or more days 7 (all on 10-12). So **60 of 85 (71%) relied on a view from an earlier day**. Under option B these depend on a digest (1–3 days old) or on search (4+ days). On day 5, 22% of these decisions used a view older than 近三天, and that share grows with run length.

**Food purchases.**
- V made 21. In 9 of them a then-present view on that item was in the prompt, all positive, and 5 of the 6 repeat purchases had one.
- Leaving out items named on the card (冰美式 is a card taste and an arrival habit): 6 purchases, 3 of them repeats.
- The clear case is 番茄牛肉刀削面, ordered on 3 of 4 workdays. Both repeat orders cite the view: on 10-09 at 11:46 the reasoning says "Preference says 番茄牛肉刀削面确实好吃", and on 10-12 at 11:50 "之前有偏好…记忆里说喜欢".
- B: 0 food purchases matched a view about the item itself. B's food views are about practices and channels (在家做饭, 盒马, 淘宝闪购).

**What this means for the "forgets her tastes after day 3" risk.** The risk is real but narrow. In V it mostly removes the view-driven noodle repeat (which runs against the card's 每顿饭都愿意换着尝不同的菜) and stale or fabricated views. Recurring habits are either on the card or rewritten every 1–2 days, so the digests will keep bringing them back.

### 5. Which phase, digest wording, and a pilot metric

**Phase: P2, not P1.**
- *Confounding.* P1's pilot reads food variety (M1, M4), and views drove 2 of the 3 noodle lunches. Removing views in P1 would mix their effect with the digests' effect.
- *Rule-text variants.* P1 keeps 4 temporary sentences: the 自述 list in rules[1], rules[2], rules[15], and read-only rule 2. That is a one-time edit to the pinned-text test.
- *Duplication.* Under the new one-entry-per-decision ruling, P1's today entries must **not** render 看法. Otherwise every view would appear twice from the next decision on, and inside chains it would travel in both a persona delta and a memory delta. The digests still read the views from host-side data.
- *Why P2 specifically.* P2 is also the latest workable phase: its cache-friendly layer order needs a byte-stable persona layer (its acceptance criterion is 100% identical), and every conclude that writes a view changes the layer.
- *Attribution inside P2 is still clean.* P2's other changes (layer reorder, slimmer progress layer) do not touch views: views appear in no other layer.
- *Cost of waiting.* About 1k tokens per decision stay in P1, roughly $0.018 per simulated day.

**Digest prompt wording.**
- Day: replace 「写那天这类事经过了什么、他觉得怎样、打算怎样；经过以核验为准，感受和打算以自述为准」 with:
  > 「先写那天这类事经过了什么，以核验为准；再写他对具体的东西、地方、人和做法的看法、习惯和打算，以自述里的「看法」「记住」和总结为准：每条看法用一句短话写出对象的名字和态度（喜不喜欢、还想不想再来、打算保持还是改掉）；写不下时先写说了以后要照做、要换掉或不再做的，再写当天写过不止一次的；只写自述里有的看法，不从经过推测感受。」
- Month (≤800): add
  > 「把日回顾里反复出现、或说了以后要照做或要换掉的看法、习惯和打算，各用一句短话写出对象和态度，写明最早、最近是哪天的回顾写到的（日期照抄输入）、后来有没有变」
- Year (≤2000):
  > 「写这一年固定下来的习惯、口味和做法，哪些看法在哪个月变了；到年底仍成立的看法各用一句短话写出对象和态度，注明出自哪个月的回顾」
- The number guard has to accept date strings that are copied from the input.

**Pilot metric M12 「表态食品回购率」** (deterministic):
- Among food purchases from run day 2 on, the share whose goods was named by a preference view written on an *earlier* local day.
- "Named" means the normalised subject equals the goods name or contains it, or is contained in it with at least 4 characters. Subjects that match more than 2 catalog goods (for example 一人份食材包) do not count.
- Report it split by whether the item is named on the card, and by the age of the youngest endorsing view (1–3 days vs 4+).
- It is computed from events and the frozen catalog only, so it means the same thing whether or not views are in the prompt.
- Baselines: **V 5/18 = 0.28 (0.11 for non-card items: the two noodle repeats); B 0/17**.
- Statistical power is low (about 4 non-card events expected in the 10-day V pilot), so report it only, for direction, next to M4.
- As a side check on mechanism (not a second metric): the rate of wording echoes from views at least a day old is 60/406 in V. If views stop reaching the model, it should drop to near 0.

### Changes the ruling forces elsewhere in the proposal
- **Item 4:** its recommendation drops the 「关系」 grouping. Relationship views take the category of their activity (V: 王磊 ×5 → 工作, 母亲 ×2 → 生活).
- **Remove 「长期」 throughout:**
  - text lines 101, 115 (this justification needs new wording), 122, 129, 140, 149, 156 (frees about 2.6k characters, roughly 2.0k tokens, of the memory budget), 165, 202, 226, 266, 323, 358-361, 603, 681;
  - the rule texts rules[1], [2], [15] and read-only rule 2;
  - the section 「人设层看法的去向」 (lines 424-434), which becomes "views leave; `persona_views` stays only for `known_persons`";
  - in the 11:50 example, the memory layer shrinks from 6,671 to about 4,059 characters;
  - M11 ④.

Files are in `/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/views/`:
- Section 1 (persona-layer content and size): `part1_sizes.py`, `tokratio.py`, `chain_deltas.py`, `where_views.py`
- Section 2 (caps and digest survival): `part2_flow.py`, `part2_survival.py`, `part2_capacity.py`
- Section 4 (behavioural reliance): `part4b_reliance.py`, `part4c_split.py`, `lunch_reasoning.py`
- Section 5 (pilot metric): `part5_m12.py`
- Shared loaders and category rules: `common.py`, `model.py`
- Superseded drafts: `part4_reliance.py` (its food-polarity code has a bug, fixed in `part4b`) and `part5_metric.py` (a revisit metric I rejected as too noisy)