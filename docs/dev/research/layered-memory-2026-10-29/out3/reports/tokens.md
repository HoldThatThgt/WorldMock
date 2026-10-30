## Token/char conversion for the ruled memory layer (V + B, DeepSeek `prompt_tokens`)

**Answer:** in the new memory layer (记忆层), one 字 costs about **0.64 DeepSeek prompt tokens** (range 0.62–0.67). The owner's 10k-token envelope is therefore about **15,700 字** at the central ratio. I recommend a **14,000 字 hard budget**. A capped 120字 today (今天) entry costs about **106 tokens** including its JSON keys.

**Unit.** 字 here means one Unicode character as Python `len()` counts it, over the compact rendered JSON, keys included. This is the unit the proposal already uses: my `len()` median of V's current 记忆层 is 4,380, the same as the proposal's "中位 4,380 字".

**Data.** 921 request/response pairs, all paired on `data.call_id`, with usage under `data.response.usage`:

| | V | B |
|---|---|---|
| fresh decisions | 305 | 309 |
| same-second chain requests | 105 | 114 |
| consolidation | 27 | 26 |
| reflection | 8 | 9 |
| plan_day | 9 | 9 |

Each recorded request is the object actually sent (`custom/agents/life_agent.py:382-387`). It is rendered compact with `ensure_ascii=False` (`src/worldmock/agents/prompt/narration.py:174-175`), so the Chinese reaches the provider as literal characters.

### 1. Fit of prompt_tokens against character classes

**Basic fit as asked** (prompt_tokens ≈ a·CJK + b·ASCII + c·other + d, numpy least squares, n=921):

| a CJK | b ASCII | c other | d | R² | residual sd | MAE | p95 \|res\| | max \|res\| | relative sd |
|---|---|---|---|---|---|---|---|---|---|
| 0.593 | 0.292 | 2.34 | −158 | 0.9981 | 310 tok | 216 | 449 | 2,137 | 4.3% |

- **This fit is badly identified; don't use its coefficients.**
  - c = 2.34 tokens per "other" character (mostly CJK punctuation) is not physical.
  - The coefficients swing by subset: a = 0.56 on V alone, 0.38 on B alone, 0.81 on fresh requests only.
  - It is biased by request type: it under-predicts plan_day by 13%, consolidation by 11% and reflection by 17%.
- **Cause.** The ~12k-character tools JSON is nearly constant, and the provider renders it in its own way. That constant gets confounded with the content coefficients.
- **Reasoning is counted.** The provider counts `reasoning_content` on chain requests. Fitting it separately gives about the same coefficients (CJK 0.66, ASCII 0.28). Dropping it worsens R² to 0.9966 and the residual sd to 411.

**Refined fits.** Characters split into CJK / letters / digits / space / ASCII punctuation / other, with one dummy per (system layer, tools) variant:

| fit | n | CJK | letters | digits | space | ASCII punct | other | R² | residual sd |
|---|---|---|---|---|---|---|---|---|---|
| levels + variant dummies | 921 | 0.641 | 0.062 | 0.509 | 0.999 | 0.635 | 0.914 | 0.99989 | 75 tok |
| fresh only | 614 | 0.678 | 0.057 | 0.416 | 1.144 | 0.660 | 0.625 | 0.99982 | 57 tok (0.28%) |
| first differences between consecutive fresh requests | 612 | 0.668 | 0.082 | 0.541 | 1.117 | 0.592 | 0.561 | 0.99990 | 17 tok |
| chain deltas (appended messages only) | 227 | 0.604 | 0.291 | 1.07 | 0.07 | 0.259 | 1.64 | 0.99968 | 37 tok |

- The chain-delta fit covers the English reasoning and tool JSON appended inside a chain. Its 0.29 tokens per letter is typical of English text.
- I use the **first-difference fit as the central estimator**. Its content is the same compact Chinese JSON as the memory layer.
- **Letters vs punctuation.** The split between those two coefficients is collinear, because JSON keys bundle letters with punctuation. That doesn't affect the new layer, which has about 0 letters.

**Cross-check with a proxy tokenizer.** prompt_tokens ≈ k·(Qwen3 tokens) + per-message term + variant dummies, using the locally cached Qwen3 tokenizer:
- R² 0.99977, k = 0.904.
- Per layer, over fresh requests (R² 0.99982): 记忆层 0.887 (V 0.875, B 0.894), 进度层 0.903, 人设层 0.916, 工具层 0.831.
- So the memory layer tokenizes like the other layers.

**The 记忆层 alone.** It can't be read off any log: there are 0 capacity or "estimate checked" lines in V's `run/logs/ops-*.jsonl`, because `compare_usage` logs at INFO (`prompt_capacity.py:177-185`). Estimated from its character mix instead (V fresh requests, 1.19M characters):
- CJK 53%, letters 12%, digits 10%, ASCII punctuation 15%, space 2%, other 7%.
- **0.576 tokens/字** (first differences), 0.584 (levels), 0.574 (Qwen proxy).
- The largest instance (7,825 字) is 0.60–0.62 tokens/字, about 4.7–4.9k tokens.

### 2. The project's own estimator vs the provider

The estimator is `prompt_token_count` (`prompt_capacity.py:61-68`), which calls litellm `token_counter`. For the model name "deepseek-flash", litellm has no matching tokenizer and falls back to tiktoken cl100k_base (`.venv/.../litellm/utils.py:2183-2199`, `litellm_core_utils/default_encoding.py:44`). I ran it with `uv run --no-sync python`.

| set | n | litellm / actual, mean (range) | bias, mean (median) | est vs actual | r |
|---|---|---|---|---|---|
| stratified sample | 30 | 1.242 (1.056–1.635), sd 0.13 | +3,739 (+3,834) tok | est ≈ 1.252·actual − 690 | 0.994 |
| all requests | 921 | 1.239, sum ratio 1.223 | +4,217 tok | — | — |

- **By request type:** fresh decisions 1.22 (1.05–1.30), chain 1.20, plan_day 1.35, consolidation 1.55, reflection 1.35 (one outlier at 0.63).
- **The over-count grows through the day.** Small morning requests (~10k tokens) are about 1.06; ~25k-token evening requests are about 1.28. The layers that grow are CJK-heavy.
- **On the memory layer alone, litellm is ~1.5× high:** 0.910 tokens/字 on the current 记忆层 (about 1.57× DeepSeek) and 0.953 on the new-layer proxy (about 1.49×).
- **Consequence:** the 85% context pre-check is safely conservative. But the 10k memory envelope must not be enforced with litellm. A cap of 10k litellm tokens would allow only about 10,500 字, which is about 6.7k real tokens. **Enforce the envelope in 字.**

### 3. The new layer: mix, conversion and recommendation

**Proxy.** `docs/dev/research/layered-memory-2026-10-29/synth/example_fmt.json` (V data for 10-12), reshaped to the rulings:
- 「长期」 dropped (ruling 17).
- 近三天 holds only `{"日期","星期","回顾"}` per category per day.
- 今天 keeps its 11 `{"时刻","类别","核验","自述"}` entries.
- Total 3,083 字. Mix: CJK 55.1%, digits 13.2%, ASCII punctuation 21.4%, space 2.7%, other 7.4%, letters 0.2%.

| piece | tokens/字 (first differences; bootstrap 95% CI) | levels: V / B | Qwen proxy |
|---|---|---|---|
| whole new layer | **0.639** (0.630–0.645) | 0.641 / 0.655 | 0.623 |
| 今天 content (核验 + 自述 text) | 0.655 (0.642–0.668) | 0.650 / 0.690 | 0.72* |
| 回顾 prose | 0.657 (0.642–0.671) | 0.670 / 0.663 | 0.64 |
| JSON shells (keys, HH:MM, YYYY-MM-DD) | 0.60 | — | — |

\*The Qwen proxy is high on 今天 content because Qwen splits digits one by one. DeepSeek's digit coefficient is about 0.42–0.54, which is consistent with 2-digit groups.

**字 per 10k tokens:**

| tokens/字 | 字 per 10k tokens |
|---|---|
| 0.638 (central) | 15,670 |
| 0.655 | 15,270 |
| 0.67 (highest char-class estimate) | 14,925 |
| 0.70 (central + 10%) | 14,285 |
| 0.72 (Qwen-proxy upper bound) | 13,890 |

**Recommended budget: 14,000 字** (`len` of the whole rendered 记忆层, keys included).
- That is about 8.9k tokens at the central ratio, 9.4k at 0.67 and 9.8k at 0.70.
- Margin: 11% against the central estimate and 6% against the highest char-class estimate.
- Only the digit-skewed Qwen bound reaches 10.08k. Use 13,800 字 if that case should be covered too.
- litellm would count 14,000 字 as about 13.3k tokens.

**Unit costs:**

| item | 字 | ≈ tokens |
|---|---|---|
| 今天 entry at the 120字 cap: content + shell `{"时刻":"08:36","类别":"饮食","核验":[""],"自述":[""]},` (45 字) | 165 | **106** (78.6 + 27.2); 103–111, ≤114 at 0.72 |
| each extra list item (`,""`) | 3 | +2 |
| shell with only 核验 or only 自述 | 35 | 21 |
| 日回顾 at 200字 | 200 | 131 (cell incl. shell 155–161) |
| 近三天 at full caps (9 cells + wrapper) | 2,266 | 1,461 |
| 月回顾 at 800字 | 800 | 526 |
| 年回顾 at 2000字 | 2,000 | 1,314 |

- The month and year digests arrive only through search_memory, so they sit outside the in-prompt envelope.
- **Compared with the owner's earlier token caps** (today 100 / day 400 / month 1,200 / year 2,400 tokens): the today entry is about the same (~79 content tokens, ~106 with keys). The day, month and year caps came out tighter, at about 33%, 44% and 55% of those token caps.

**The overflow rule will fire on normal days if entries fill the cap.**
- The 67-per-day figure counts only fresh decisions. With one entry per decision, entries per day are V 81 / 84 / 83 / 69 / 89 and B 84 / 80 / 79 / 85 / 81 (from `decision_started` events, by local date).
- At full caps:

| entries at end of day | 字 | ≈ tokens |
|---|---|---|
| 60 | 12,166 | 7,811 |
| 67 | 13,321 | 8,552 |
| 81 (V mean) | 15,631 | 10,034 |
| 89 (V max, 10-12) | 16,951 | 10,880 |

- 14,000 字 holds 71 capped entries plus a full 近三天; 10k tokens at the central ratio holds 80.
- So with every entry at the cap, every V and B day except V 10-11 crosses the 14,000 字 budget late in the evening.
- The overflow rule stays a backstop only if the **average 今天 content per entry is ≤ about 87字** (89 entries) **or ≤ about 100字** (81 entries). I did not measure the realistic average entry length; that is the open variable.

All files are in `/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/tokens/`:
- fit_tokens.py
- fit_refined.py
- memlayer.py
- litellm_check.py
- litellm_lowratio.py
- bootstrap.py
- budget.py
- fit_report.json
- refined_report.json
- memlayer_report.json
- litellm_report.json
- bootstrap_report.json
- budget_report.json
- rows.json
- coef_basic.npy
- coef_fine.npy
- coef_level.npy
- coef_fd.npy