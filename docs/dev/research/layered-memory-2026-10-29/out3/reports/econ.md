## Summary

- **Cache model checks out.** A prefix-cache simulation over V's real 410 decision requests predicts V's decision-turn cost at **$0.3286/day against $0.3273 actual** (+0.4%). Run unchanged on B, it predicts $0.3075 against $0.3068.
- **Proposal order, one entry per decision:**
  - With the P2 slim 进度层: **$0.198/day for decision turns (−$0.131/day, −40% vs current)**. The fresh-call hit rate goes from 0.336 to 0.649.
  - With V's full 进度层: $0.284/day (−$0.045/day).
- **Per-decision appends cost no more than once-per-segment appends.**
  - With the same content, per-decision is $0.002/day cheaper than per-segment (V; $0.0016 on B).
  - It is $0.0006/day dearer than per-second publication. That gap is all the new chain deltas.
- **No better order that holds up.** Putting 待办 (without orders) before 记忆 and moving the orders to the tail saves $0.006/day on V but nothing on B. Keep the proposal order.
- **10k tokens ≈ 16,400 characters of 记忆层, not 23,800.** The memory content runs at about 0.60 tokens/char, not 0.42.
- **Entries per day are higher than assumed.** Each day gets one entry per decision, which is **69–89 a day in V and 79–85 in B** (chain decisions count too), not 60–67.
- **Digests are cheap in total.** Day digest ≈ $0.0045/day (worst case with 3 retries $0.0082). Month ≈ $0.0074, year ≈ $0.0129. Together that is about $0.0048/day, the same as the consolidation and reflection calls they retire.
- **Year output fits one call.** 6,000字 is about 4.4k tokens, well inside an 8,000-token max_tokens reserve.
- **search_memory:** cap each result at **≤6,400字 of digest/entry text, with no 12-item cap**.
  - Each search adds about $0.0007 (small result) to $0.0021 (result at the cap).
  - The worst case stays under 12% of the 85% capacity threshold.

## Method and calibration (scripts in `/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ/`)

**Token model** (`tokfit.py`, `layerfit.py`)
- A fit by character class over all 819 decision requests in V and B gives tokens/char of: CJK 0.709, CJK punctuation 0.955, ASCII letters/digits 0.108, ASCII punctuation 0.657. Intercept 5,755; mean absolute error 90 tokens (0.44%).
- The same model reproduces V's non-decision completion tokens: 22,692 predicted against 22,628 actual.
- Per-layer fits (tokens/char):

  | layer | tok/char | 95% CI |
  |---|---|---|
  | 工具层 | 0.258 | |
  | 人设层 | 0.550 | |
  | 记忆层 | 0.579 | 0.573–0.585 |
  | 待办层 | 0.79 | |
  | 进度层 | 0.425 | |

- Synthetic text built from V's real 核验 and 自述 strings:
  - a 145-char 今天 entry is 86.7 tokens (0.598/char);
  - a 165-char entry, the true ceiling of 120字 plus keys, is 100.5 tokens;
  - 近三天 written to the cap is 1,948 chars and 1,348 tokens (0.69/char).
- The suggested 0.42 only fits the progress-dominated mix, so I used the fitted values.

**What the data shows about the cache** (`hits.py`, `calib_hist.py`)
- All 421 nonzero cache hits in V and all 435 in B are multiples of **128 tokens**.
- Prefixes match inside a message: fresh hits of 7,424–7,680 tokens line up with 4.6–5.1k shared characters of 工具层.
- The cache also holds the *generated* assistant turn. On history turns the gap between actual and simulated hits equals the replayed assistant tokens.
- The cache is shared across runs: B's first fresh request hit 9,728 tokens left by V.

**Simulator** (`sim.py`, `run_sim.py`)
- Every earlier prompt, plus its generated output, stays in the cache.
- A request's hit is its longest common prefix with any earlier one, floored to 128 tokens. System, tools and template count as a constant 5,660 tokens.
- Fit at G=128, T_ST=5,660, without and then with V's completions in the cache:

  | | without outputs cached | with outputs cached |
  |---|---|---|
  | mean abs error, tokens | 262–271 | **130** |
  | fresh hit, sim / actual | | 2,027,776 / 2,029,184 |
  | history hit, sim / actual | | 2,167,936 / 2,189,056 |

- G=64 matches the totals equally well but gets only 143 of 410 requests exactly right, against 234 for G=128.
- On B, with no refit: mean abs error 106, fresh hit 2,029,952 against 2,029,184.

**How scenarios are built**
- Each scenario re-renders the user message and keeps every other byte of V's requests.
- Chain and history requests map onto their re-rendered chain head.
- For per-decision publication, each chain step adds one new entry to its continuation tool message as a delta (`今天新增`).
- Prompt tokens are the actual count plus the class-model difference of the changed text. Outputs are V's actual outputs.
- 近三天 is assumed full from day one (steady state).
- 待办 gets in-transit orders taken from purchases and delivery_arrival events.
- "Slim 进度层" reuses the research script `econ/slim.py`.
- All prices are the frozen peak rates (`src/worldmock/recording_engine/reports/quality.py:55-66`, peak at :62).

## (a) Prompt-cache and cost per simulated day (decision turns, V; `final_table.md`)

| scenario | 10-08 | 10-09 | 10-10 | 10-11 | 10-12 | mean/day | Δ vs (i) | fresh hit% | $/fresh turn |
|---|---|---|---|---|---|---|---|---|---|
| (i) current order and content (V as run) | .2789 | .3154 | .3385 | .3025 | .4079 | **.3286** | — | .336 | .00465 |
| current order, new memory, slim 进度层 | .2838 | .2824 | .2751 | .2237 | .3040 | .2738 | −.0548 | .396 | .00375 |
| (ii) proposal order, per-decision, V's full 进度层 | .2612 | .2782 | .2823 | .2805 | .3175 | .2839 | −.0447 | .507 | .00391 |
| **(ii) proposal order, per-decision, slim 进度层 (P2)** | .1967 | .2020 | .1957 | .1745 | .2196 | **.1977** | **−.1309** | **.649** | .00250 |
| proposal order, per-second publish, slim | .1963 | .2012 | .1950 | .1740 | .2189 | .1971 | −.1316 | .649 | .00250 |
| per-segment publish, full 进度层 (P1 shape) | .2384 | .2532 | .2567 | .2584 | .2903 | .2594 | −.0692 | .537 | .00352 |
| per-segment, slim + that segment's entries at the 进度层 tail (same content) | .2014 | .2041 | .1986 | .1706 | .2247 | .1999 | −.1288 | .640 | .00255 |
| (iii) 人设, 待办 (no orders), 记忆, 工具, 环境, 进度 (+orders) | .1934 | .1983 | .1874 | .1686 | .2115 | .1919 | −.1368 | .668 | .00241 |

**Where the −$0.131/day comes from (V):**
- The new memory content in the current order adds +$0.031/day. Memory grows by about 1.7k tokens per fresh call, and all of it misses, because 工具层 comes first and changes every call.
- The proposal order saves −$0.076/day.
- The slim 进度层 saves −$0.086/day.

**All-in per day** (adding plan_day at $0.0044/day, still reading event rows per item 14, and digests at $0.0048/day):

| | per day |
|---|---|
| current | $0.338 |
| **proposal P2** | **$0.207** (B: $0.199) |
| P1 per-segment shape | $0.269 |

**Per-decision vs per-segment publication.**
- The cost is not materially higher.
- Per-decision appends move each entry into the cached prefix almost at once. Per-segment appends keep the current segment's entries in the miss-every-time tail.
- What per-decision gives up: 待办 (about 0.7k tokens) and the partial 工具层 prefix can no longer be cached. That roughly cancels the gain.
- Chain deltas cost about 101 chain requests × 87 tokens of misses, ≈ $0.0005/day.
- Per-decision publication is also what makes the slim 进度层 possible. That, not the append frequency, is the $0.06/day gap to the P1 shape.
- Sensitivity checks:
  - With 165-char entries: per-decision $0.1983 against $0.2045 for per-segment.
  - With G=64, all the gaps stay within $0.0005/day.
- `life_continuation.py:8` assumes 记忆层 is constant within a chain. That no longer holds, so `layer_changes` needs a memory key.

**(iii) Better order.**
- Under per-decision publication, everything after 今天 always misses on a fresh call. The order of 待办, 工具, 环境 and 进度 after memory therefore doesn't matter.
- The only lever is putting something stable *before* 记忆. 待办 changed in 42 of 304 fresh pairs in V, 38 of them because of orders.
- Results:

  | option | V | B |
  |---|---|---|
  | 待办 first, with orders | −$0.0013/day | — |
  | 待办 first, orders moved to the tail | −$0.0058/day | +$0.0003/day |

- Not robust and not worth the change. **Keep the proposal order.**
- The rule to write down: nothing that changes more than once per decision may come before 今天, and 今天 is the last key of the stable block. Memory before 人设 is worse ($0.2073).

**The 10k-token envelope in characters** (`memory_budget.py`)
- Fixed part: 可查, 近三天 and keys come to about 2,050 chars, 1,421 tokens.
- That leaves about 8,580 tokens for 今天. At a measured 0.60 tok/char that is **about 14,300 chars**: 99 entries at 145 chars, or 85 at the 165-char ceiling.
- **Whole 记忆层 ≤ about 16,400 chars ≈ 10k tokens.**
- At V's busiest day (89 decisions) the layer peaks at:
  - 14.7k chars / 9.0k tokens with 145-char entries;
  - 16.4k chars / **10.2k tokens** if every entry hits the 120字 cap.
- So the backstop could just fire at the very end of a busy V day when every entry is at the cap. It should trigger on characters and fold a large chunk once, for example down to 75%, so it breaks the cache once. One fold costs at most about 8k miss tokens, ≈ $0.0024.

**Proposal figures to correct:**

| proposal says | modelled |
|---|---|
| 记忆层 cap 11,500字 | about 16,400 chars ↔ 10k tokens |
| 今天 ≤6,000, fold to ≤4,000 | superseded: no routine fold |
| P2 fresh turn: 9–10k hit, 5k miss, $0.0021 | 10.9k hit, 5.9k miss, $0.00250 (of which $0.00067 is output) |
| M7 ≥0.55 | reachable only with the slim 进度层; 0.507 with V's full 进度层 |

The P2 target of ≤$0.22/day holds.

## (b) Digest costs at the new caps (`digest_cost.py`, `digest_input.py`, `query_answers.py`)

**Retries.**
- Retries are capped by `reflection_validation_max_retries`, default **3** (`src/worldmock/configuration/inputs/models.py:224`). That means up to 4 attempts, the same pattern as `runner.py:663`.
- The retry cost model comes from V's plan_day retries:
  - each retry hits the attempt-1 prompt (12,288 of 13,210 tokens);
  - it misses the rejected output plus the error message, about 700 tokens;
  - it writes a full new output.

**Day digest** (V's real days; the input is the card, the prompt plus tool, every entry with untruncated 自述 including report_status summaries and reply bodies, the query answers, and per-entry keys for 69–89 entries):

| day | input tokens | first try | worst with 3 retries |
|---|---|---|---|
| 10-11 (lowest) | 10,645 | $0.0037 | $0.0064 |
| 10-12 (highest) | 16,192 | $0.0054 | $0.0082 |
| **mean** | **13,320** | **$0.0045** | — |

- The input splits into: 自述 5.9–8.6k, query answers 0–3.1k, keys 1.8–2.4k, 核验 about 1.1k, card 535, prompt about 420 tokens.
- Output ≤3×200字 is about 515 tokens.
- The proposal's "4–6k tokens, about $0.002" is about 2.3× too low. Every decision now has an entry, and 自述 and query answers go in untruncated.

**Month and year digests:**

| | input tokens | output tokens | first try | worst with 3 retries |
|---|---|---|---|---|
| month | about 17.3k (93 × 200字 digests 15.2k + count lines about 1.1k) | about 1,960 for 3×800字 | **$0.0074** | $0.0167 |
| year | about 25.7k (36 × 800字 = 21.5k + year count lines about 3.3k) | about **4,390** for 6,000字 | **$0.0129** | $0.0333 |

The proposal's month figure was about $0.004.

**Amortised over the calendar:** $0.0045 + $0.00024 + $0.00004 ≈ **$0.0048/day**, or about $0.008/day if every call used all its retries. Retiring consolidation and reflection saves $0.0048/day (V: $0.0187 + $0.0051 over 5 days). Net ≈ 0.

**Max output.**
- The frozen V `model_metadata.max_output_tokens` is 393,216, so the per-call reserve is the binding limit:
  - `PLAN_DAY_MAX_TOKENS = 8000` (`simulation_engine/scheduling/plans/day_planning.py:38`);
  - `ACTION_MAX_TOKENS = 16000`, which `custom/agents/life_agent.py:81-83` says memory calls also use.
- The 6,000字 year output, about 4.4k tokens and still under 5k at 0.75 tok/char, **fits in one call** with either reserve, *with thinking disabled*. It would not fit a 4,096 reserve.
- Recommendations:
  - set max_tokens = min(8000, metadata.max_output_tokens) explicitly;
  - keep thinking disabled (reasoning would share the reserve);
  - treat `finish_reason=length` as a validation failure and retry.

## (c) search_memory results (`search_cost.py`)

**Sizes of the queries that must fit** (rendered):

| query | chars | tokens |
|---|---|---|
| single category, 31-day range (31×200字) | 7,290 | 5,072 |
| 全部, one year (3×2000字) | 6,137 | 4,368 |
| 全部, one month | 2,549 | 1,822 |
| 全部, one day | 746 | 514 |

The old cap of 12 items / 4,000字 would cut the 31-day query to 12 days.

**Proposed cap.**
- **≤6,400字 of 回顾/entry text per result, newest first; rest named in `未列出`; no separate item cap.**
- Rendered size at the cap is about 7,500 chars and **about 5.2k tokens**.
- How the overflow cases come out:
  - a single-category year before a year digest exists (12×800 = 9,600字) returns 8 months, with 4 listed in 未列出;
  - a 全部 31-day range (18,600字) returns about 10 days × 3, with the hint to narrow the range or pick a category.
- Detail to fix in item 23's option A (still unruled, recommendation unchanged): in range, month and year queries, list a 未整理 day in 未列出 ("按日期单独查") instead of inlining its raw entries. A raw day of about 80 entries would blow the cap on its own.

**Cost per decision.** Each search adds one turn, which hits the prior prompt and output and misses the new result:

| searches | small result (1 day × 3 categories): prompt tokens / cost | result at the cap: prompt tokens / cost |
|---|---|---|
| 1 | +0.57k / +$0.0007 | +5.3k / +$0.0021 |
| 2 | +1.15k / +$0.0015 | +10.6k / +$0.0043 |
| 3 | +1.72k / +$0.0022 | +15.9k / +$0.0065 |

- At V's rate (2 searches in 5 days) this is negligible.
- Under ruling 17 (search only), one cap-size search per food decision (about 6/day) would add about $0.013/day. Dropping 「长期」 itself saves only about $0.001/day, since it would have been cached.

**Capacity.**
- The threshold is 0.85 × 1,048,576 = **891,290 tokens**, including the reserve (`agents/prompt/prompt_capacity.py:28,89`).
- The worst single decision is V's max prompt of 37,421 + 3 cap-size searches + the 16k reserve = 69k tokens, **7.8%** of the threshold.
- The worst chain (V's max chain length is 3, and replayed decisions keep their results) is about 101k tokens, **11.3%**.
- The part that can't be shrunk (system, tools, 工具/人设/记忆/待办/环境 per `prompt_capacity.py:1-7`, plus the search tool messages) is about 36k tokens, about 4%.
- No threat. The only part of that floor without a natural bound is 今天, which is why the character backstop above is needed.

## Files

The main entry points are `sim.py`, `run_sim.py` and `final_table.py`. All files are in `/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ/`:

- Calibration: `tokfit.py` (`tokfit.json`, `tokfit.txt`), `layerfit.py` (`layerfit.txt`), `calib.txt`, `calib_hist.py`.
- Scenarios: `scenarios.py` (`scenarios_full_progress.txt`), `scenarios2.py` (`scen2_128_145.txt`, `scen2_128_165.txt`, `scen2_64_145.txt`), `scen3.py`, `todo_changes.py`, `b_check.py` (`b_check.txt`), `final_table.md`.
- Memory budget: `memory_budget.py` (`memory_budget.txt`).
- Digests: `digest_cost.py` (`digest_cost.txt`), `digest_input.py`, `query_answers.py`, `outratio.py` (`outratio.txt`).
- Search: `search_cost.py` (`search_cost.txt`).

The runs were opened read-only, and nothing in either repo was modified.