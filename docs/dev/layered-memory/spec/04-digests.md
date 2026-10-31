# 04 日、月、年回顾

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。第四批裁定（2026-10-30）修订。第五批裁定（2026-10-31）修订。

## 目标与依据

本章规定日回顾、月回顾和年回顾：零点任务里的时机与次序、哪些期次要生成、输入、请求与提示原文、输出校验、重试与降级、`memory_digest_generated` 事件的登记与闭集校验、恢复与分支、质量报告的调用类和运维记录。三级同一次交付；提案里 P1（日回顾）与 P3（月、年回顾）的先后只作实现顺序。

实现的提案章节：

- 第二章「分级：今天、近三天、每一天、月与年」表中「每一天」「每个月」「每一年」三行的生成者与生成时机；
- 第二章「压缩：模型写的回顾段落」全节：日回顾、月回顾、年回顾、「校验」、重试与 `fallback`，以及「退役的东西」表中由日回顾替代睡醒反思的那一侧；
- 第二章「存储与事件记录」中回顾的各条：模型证据加一条 `environment_event`，`data` 的形状，`engine.register_memory_digest` 的提交方式，`validate_environment_event` 的闭集字段分支，按事件判定幂等，恢复、重放、导出与分支的语义；
- 第三章「代码」表中 `state/model/exchange.py`、`simulation_engine/evidence/memory_digest.py`、`runtime/lifecycle/runner.py`、`scheduling/clock/time_advance.py`、`simulation_engine/engine.py`、`custom/envs/life_env.py`、`recording_engine/events/environment_events.py`、`recording_engine/reports/quality.py` 各行的回顾部分；
- 第三章「事件、版本与下游」表中「质量报告 9→10」「运行清单保持 7」「世界、检查点、导出保持 15、1、8」「检查点和恢复」四行的回顾一侧。

适用的裁定：

| 裁定 | 本章的落实 |
| --- | --- |
| 第一批「压缩一律由模型写成描述性的段落」 | 三级回顾都由一次模型调用写成段落；代码只准备输入和次数统计，不写回顾正文 |
| 7（选项 A） | 每一级每一期一次调用，三类的输入和输出分开、逐类校验；一类不通过不连累已通过的类 |
| 8（描述那段时间的经过和感受；子问题的选项 A 由第四批裁定取代） | 回顾是日记式的回忆：写要紧的经过和当时的感受，吃的、做的、去的地方和打交道的人写出名字；时间说早上、中午、下午、晚上（月回顾说月初、月中、月底、某个周末，年回顾说年初、春天、几月份、年底），价钱、数量和时长只在要紧时顺口带一句，次数说几乎每天、常常、偶尔；时刻、价钱、数量、日期、月份和次数既不要求出自输入，也不检查 |
| 9（模型写段落，月 800、年 2000 字；子问题选项 A） | 月回顾、年回顾与日回顾同期交付；本章的验收是确定性测试，跨月短试点的步骤与指标归第 09 章 |
| 13（每天由模型压成一段） | 日回顾不设门槛，每个有条目的日子都生成 |
| 第二批「各级字数上限」（原话见裁定 18；日回顾一级由第五批裁定改） | 每类正文 `len(text)`（标点也算）不超过日 250、月 800、年 2000 |
| 23（选项 A，回顾一侧） | 重试用尽的类记 null 并列入 `fallback`；「近三天」与检索据此认出「未整理」 |
| 「字」按 `len` | 本章所有字数都是 Python `len()` 的结果 |
| 第四批（2026-10-30）「Memory is a diary, not a device log, it records feelings, and some essential facts.」「length should still be capped at what we have desigend」（原话全文见提案第五章） | 三份提示改写成日记（见「提示原文」）；输出只做格式与长度检查：响应级四项、逐类的 null 规则和守卫表的四行（正文非空、正文长度、依据非空、依据归属）。裁定 8 子问题选项 A 的数字规则、日期和月份照抄、次数照抄作废；提案第二章「校验」原列、随第三批选项 A 带入的含中文、不含身份串、名称、饮食核验名称、饮食覆盖、做饭那一句、数字、数字同条、次数照抄九道守卫撤销。回顾与记录是否相符由试点衡量（第 09 章 M11 与模型核对），运行时不查；字数上限照第二批不变（日回顾一级随后由第五批裁定改为 250） |
| 第五批（2026-10-31）「pop length cap to 250 and fix prompt; do a 2day runs to check」（原话与改动清单见提案第五章） | `MEMORY_DIGEST_TEXT_LIMITS` 的日回顾由 200 改为 250，仍按 `len` 计、标点也算，月 800、年 2000 不变；日回顾提示改三处：「150 到 220 字为宜，连标点在内不能超过 250 字」、时间只说早上、中午、下午、晚上而不写钟点（「八点十一」「九点一刻」这样的说法也不写）、字数紧时先省经过的细节而留下感受和看法；月、年回顾提示在字数后加「（标点也算）」（见「提示原文」）；正文长度的问题文字写明连标点的字数、上限和至少要删去的字数（见守卫表）。不变的：重试时不说哪几类已通过，`reflection_validation_max_retries` 仍为 3，响应级不合格时整次尝试不接受任何类，不加任何守卫（钟点只写进提示，运行时不查）。两天的真实试点核对另跑，结果另写试点记录（提案第五章第五批） |

依赖其他章节的事实（本章只消费，不重述）：

- 第 02 章「今天条目」：条目全文的形状、一条条目按类别切出的那一部分、条目的来源事件身份、短名、零点收尾条目、`past_day_entries`、`agent_events` 与 `activity_facts`；
- 第 03 章「类别、归日与近三天」：`MEMORY_CATEGORIES`、活动与商品的类别推导、归日函数与零点界、「近三天」的日期取法与渲染；食品状态与食材包状态由第 08 章的 `Catalog.food_states()`、`Catalog.kit_states()` 给出；
- 第 08 章「退役项、食材包句与环境包」：「活动 → 所用食材包」映射；巩固与睡醒反思的退役；
- 第 05 章「search_memory 与工具面」：检索读回顾、某格「未整理」时退回原始条目；
- 第 09 章「分析工具、运行比对与查看器」：M10、M11 与试点。

### 费用（参考，不是验收门槛）

数字取自提案「压缩」一节与研究目录 out3/reports/econ.md，按冻结的峰时价（命中 $0.006/M、未命中 $0.30/M、输出 $1.20/M，recording_engine/reports/quality.py:55-66）。第五批裁定之后，日回顾输出与月回顾输入随每类 250 字的上限变化，用同一费用模型（研究目录 out3/econ/digest_cost.py，只把日回顾每类的字数改成 250）重算；日回顾提示比原来多约 40 token。

| 级别 | 输入 | 输出 | 首次调用 | 3 次重试用尽 |
| --- | --- | --- | --- | --- |
| 日回顾 | 10.8k–16.3k token，平均 13.4k（自述 5.9–8.6k，查询答案 0–3.1k，键名 1.8–2.4k，核验约 1.1k，卡片 535，提示约 530） | ≤750 字，约 620 token | $0.0038–0.0055，平均 $0.0046 | $0.0087 |
| 月回顾 | 约 20.7k token（93 段日回顾 18.6k，统计约 1.1k） | 2,400 字，约 1,960 token | $0.0084 | $0.0178 |
| 年回顾 | 约 25.7k token（36 段月回顾 21.5k，统计约 3.3k） | 6,000 字，约 4,390 token | $0.0129 | $0.0333 |

三级摊到每一天约 $0.0049（日 $0.0046，月 $0.00028，年 $0.00004），重试全部用尽约 $0.009；退役的巩固和睡醒反思每天约 $0.0048，净值约为零。8,000 的输出上限下每次发送的整次期限是 180 秒（docs/design/08-configuration.md:151 的同一规则）。

## 行为

### 零点任务的次序

`LifeEnv.step`（custom/envs/life_env.py:235-243）的零点分支改为：

```python
engine.advance(tick, t)
if engine.midnight_rhythm_pending:
    await settle_pending_memory_digests(engine)
    await settle_pending_day_plans(engine)
    engine.decide_midnight_rhythm()
self.t = t
```

- `midnight_rhythm_pending` 只在本地零点且不是运行终点的边界上为真（`reminders.defers_rhythm`，simulation_engine/scheduling/plans/reminders.py:32-37）。所以一个零点依次是：推进提交 → 日回顾 → 月回顾 → 年回顾 → 零点计划 → 零点节律判定。这一秒的第一次决策排在节律判定之后，它的观察看得到刚登记的回顾。
- 三级回顾都在 `settle_pending_memory_digests` 的一次调用里完成，按级别 `day`、`month`、`year` 的次序处理（见「哪些期次要生成」）。月初零点的月回顾读的是同一次调用里刚登记的上个月最后一天的日回顾；年初零点的年回顾读的是同一次调用里刚登记的十二月月回顾。
- `LifeEnv.checkpoint`（:205-215）在 `self._checkpoint(engine, request)` 之后先 `await settle_pending_memory_digests(engine)`，再 `await settle_pending_day_plans(engine)`。新登记的回顾和计划由下一个共同检查点封存。这一路径覆盖新建运行的初始检查点和反事实分支的干预检查点。
- 普通恢复：`LifeEnv.init` 里 `branch_kind == "resume"` 的那一支（:106-107）改调 `_settle_midnight_tasks_on_resume(engine)`，它取代 `_settle_day_plans_on_resume`（:109-116），依次 `await settle_pending_memory_digests(engine)` 与 `await settle_pending_day_plans(engine)`；任一抛出时先 `engine.fail_run(repr(error))` 再原样抛出。
- 运行已到终点（`state.current_time >= state.end_time`）时，`settle_pending_memory_digests` 在构造客户端之前返回 `[]`，与 `agents_missing_plan`（simulation_engine/scheduling/plans/day_planning.py:72-78）同一判断。所以运行的最后一天不生成日回顾；终点恰在零点时，那个零点不生成任何一级回顾。
- 回顾任务不经 LifeAgent，不读写人物工作区与 AS2 记忆运行时，不写 `observation`、`decision_started` 或 `memory_updated`，与零点计划相同。
- 回顾只在零点分支、检查点之后和普通恢复的初始化里运行，决策轮进行中从不运行。

### 哪些期次要生成

记 T 为 `local_date(state.current_time, timezone)`（foundation/time/readable.py:35-37）那一天，F 为本足迹库第 1 号事件（`next(store.iter_events(1))`）发生时刻的本地日期。

**期次。** `DigestPeriod(tier, period)`：日回顾 `("day", "YYYY-MM-DD")`，月回顾 `("month", "YYYY-MM")`，年回顾 `("year", "YYYY")`。

**已登记。** `registered_digests(store, agent_id)`（见「登记与事件」）里有这一期的事件。一期有没有回顾只看这条事件；不新增 AgentState 字段，世界结构保持 15。

**候选期次。** 每次调用对每个人物（按编号）先算日回顾的候选，逐个处理完，再算月回顾的候选，最后算年回顾的候选。

- 日回顾的候选日是下面两组的并集，早于 F 的日子剔除，按日期从早到晚处理：
  - 日历上的 T−1、T−2、T−3；
  - T 之前最近的三个「工作类有输入」的日子：从 T−1 往前逐日看，看到 F 为止，取最先遇到的三个。
- 一个已结束的日子 D「某类有输入」的判定：D 已登记时看那条事件，这一类的回顾非 null 或这一类在 `fallback` 里；D 未登记时看条目，D 的全部条目（第 02 章的决策条目与零点收尾条目，即 `past_day_entries(inputs, store, agent_id, D, state.current_time)`）里至少一条有这一类的部分。
- 这就是第 03 章「近三天」的日期规则（饮食和生活取日历上的前三天，工作取最近三个有工作的日子）在「按条目判定」下的同一规则。运行中的零点只会找到 T−1 一个待生成的日子；恢复时找到的是此前缺回顾、且仍在近三天里的日子。
- 月回顾的候选是 T 所在月的上一个日历月；年回顾的候选是 T 的上一个日历年。每次调用都看这一个候选，不只在月初、年初看。
- 已登记的候选和已记为「不需要」的候选都跳过。

**不需要回顾。** 候选期次的三类都没有输入时，不构造客户端、不调用模型、不写事件，只在进程内缓存里记下「不需要」：

- 日回顾：那天没有任何条目；
- 月回顾：那个月没有一条日回顾事件带非 null 的类或非空的 `fallback`；
- 年回顾：那一年没有一条月回顾事件带非 null 的类或非空的 `fallback`。

**缓存。** 进程内，按足迹库对象与人物键（`WeakKeyDictionary[FootprintStore, dict[int, _Pending]]`，写法同 simulation_engine/observation/layers/memory_layer.py:96）：

- 「不需要」的期次集合；
- 每个已结束日子各类有没有输入（第一次判定后不再算）。

已结束的日子、月份和年份这两项都不会再变，所以缓存从不失效；恢复和分支换成新的足迹库对象，缓存从头建立。同一进程里两次调用之间没有新登记、也没有跨过零点时，第二次调用不构建任何条目。检查点每模拟日约 200 次，这一条保证它们不重读事件、不重算条目。

### 输入

**人物卡片。** `render_card(persona_card(context.persona))`（configuration/persona/persona_text.py:27，configuration/persona/persona_card.py:77），与新建运行写进工作区 profile 的 `persona_card` 是同一函数、同一文字（runtime/framework/setup.py:205-209）。

**类别与键。** 饮食 ↔ `food`、工作 ↔ `work`、生活 ↔ `life`，次序与 `MEMORY_CATEGORIES` 相同。用户消息用中文类别名作键，工具参数与事件用英文键。

**工作类不启用。** 工作类不启用时（第 03 章第 8 节，`book.categories` 不含「工作」），没有行或项归工作，三级输入里工作类都没有行：日回顾写 `"工作":[]`，月回顾写 `"工作":{"日回顾":[],"次数统计":[]}`，年回顾写 `"工作":{"月回顾":[],"次数统计":[]}`。用户消息仍有三个类别键，工具参数仍要求三个键；按「逐类」规则模型必须给 `work: null`，事件的 `digests.work` 恒为 null 且 `work` 从不进 `fallback`。

**编号。** 一次调用的输入行在三类之间连续编号：先饮食、再工作、后生活，类内按时间先后。日回顾为 `n1`、`n2`…，月回顾为 `d1`…，年回顾为 `m1`…。编号只在这一次调用里有效。

**序列化。** 用户消息的内容是 `json.dumps(payload, ensure_ascii=False, separators=(",", ":"))`，键按下文写出的次序插入，不排序。

**日回顾**（period 为 D）：

```json
{"人物卡片":"…",
 "饮食":[{"编号":"n1","时刻":"08:36","核验":["买美式咖啡1份，18.3元，预计08:50送到练秋湖研发中心"],"自述":["购买美式咖啡：…","看法·冰美式咖啡：…"]},
        {"编号":"n2","时刻":"08:50","核验":["送达美式咖啡"]}],
 "工作":[{"编号":"n3","时刻":"08:50","自述":["进展·处理消息与收尾：…"]},
        {"编号":"n4","时刻":"08:54","核验":["处理消息与收尾 08:34–08:54"],"自述":["处理消息与收尾：…"]}],
 "生活":[]}
```

- 每一类的列表：D 的全部条目里，「类别」串含这一类的，各取这一类的部分（第 02 章的切法：只留这一类的核验行、当时查到和自述项），一条一行。上例 08:50 的条目类别为「工作、饮食」，在饮食和工作各出一行。决策条目按决策顺序在前，零点收尾条目在最后，它的「时刻」是 `24:00`。
- 一行的键依次是 `编号`、`时刻`、`核验`、`当时查到`、`自述`；后三个为空列表时省略。内容是第 02 章的全文：核验的完整行、当时查到的全部行、全部自述项（总结、进展、看法、记住（含 routine）、正文），都不截断。
- 没有输入的类写空列表。
- 不给前几天的回顾，也不给日期。

**月回顾**（period 为 M）：

```json
{"饮食":{"日回顾":[{"编号":"d1","日期":"2026-10-09","回顾":"…"},{"编号":"d2","日期":"2026-10-10","未整理":true}],
        "次数统计":["番茄牛肉刀削面 3 次","美式咖啡 2 次"]},
 "工作":{"日回顾":[…],"次数统计":[…]},
 "生活":{"日回顾":[…],"次数统计":[…]}}
```

- 行：M 里每一条已登记的日回顾事件，按日期先后。这一类回顾非 null 时写 `{"编号","日期","回顾"}`；为 null 且在 `fallback` 里时写 `{"编号","日期","未整理":true}`；为 null 且不在 `fallback` 里时，这一天在这一类不出行。
- 「未整理」的行也是输入：一类只要有一行（哪怕全是「未整理」）就算有输入。
- 行为空的类写 `{"日回顾":[],"次数统计":[]}`。
- 不给人物卡片，也不给月份键；日期在各行里。

**年回顾**（period 为 Y）：形状同月回顾，键 `日回顾` 换成 `月回顾`；行是 Y 里每一条已登记的月回顾事件，按月份先后，写 `{"编号","月份","回顾"}` 或 `{"编号","月份","未整理":true}`；行为空的类写 `{"月回顾":[],"次数统计":[]}`。不给人物卡片和年份键。

**次数统计**（`days.count_lines`，只用于月回顾和年回顾）：

- 统计区间：月回顾是 M 的每一天，年回顾是 Y 的每一天；事件按第 03 章的归日函数归到它发生时刻所在的本地日期。年回顾按全年事件统计，不由月统计相加。
- 饮食类：
  - 区间内每条本人 `goods_received`（`environment_event`，simulation_engine/commerce/fulfillment/receipt.py:101-108），商品类别为 `meal`、`snack` 或 `drink` 的，记这件商品的名称一次；
  - 区间内结束（结束事件按 `activity_endings`，simulation_engine/observation/wording/settled_text.py:156-169）、在第 08 章「活动 → 所用食材包」映射里有食材包的每项本人活动，记那份食材包的商品名称一次；
  - 食材包按做饭使用计，它的收货不另计；吃的、喝的按收货计。
- 工作类、生活类：区间内结束、终态是 `completed` 或 `cancelled`（自然结束或人物收尾；`failed` 与运行截止的 `truncated` 都不计）、第 03 章判给这一类的每项本人活动，记它的短名（第 02 章）一次。
- 一行写成 `f"{名称} {次数} 次"`：名称与数字之间、数字与「次」之间各一个半角空格。按次数从多到少排，次数相同时按首次计入那条事件的序号从小到大；不设门槛。
- 行为空的类不给次数统计（写空列表）。
- 次数统计只是输入，让模型知道哪些事常有；正文不照抄统计行，次数也不检查（第四批裁定）。

### 请求

`prepare_digest_request(inputs, model_name, metadata)` 返回：

```python
{"model": model_name,
 "messages": [{"role": "system", "content": DIGEST_PROMPTS[tier]},
              {"role": "user", "content": payload_json}],
 "stream": False,
 "tools": [memory_digest_tool()],
 "tool_choice": "required",
 "max_tokens": min(MEMORY_DIGEST_MAX_TOKENS, metadata.max_output_tokens),
 "max_retries": 0,
 "extra_body": {"thinking": {"type": "disabled"}}}
```

- `MEMORY_DIGEST_MAX_TOKENS = 8000`；冻结元数据的 `max_output_tokens` 更小时用它，写法同 `PLAN_DAY_MAX_TOKENS`（day_planning.py:38、:103）。思考关闭，因为推理和输出共用这份上限；年回顾的 6,000 字约 4.4k token，只在关闭思考时放得下。
- 容量预检：`request_token_count(request)`（含输出预留，agents/prompt/prompt_capacity.py:71-77）不得超过 `CONTEXT_THRESHOLD * model_context_length(model_name, metadata)`（prompt_capacity.py:28）。超过时写错误日志 `Memory digest request exceeds the model context`，抛 `RuntimeError("memory digest request capacity exceeded")`：这是运行故障，输入不裁剪，不留证据。只预检第一次请求，重试请求不再预检，与零点计划相同。
- 工具 `memory_digest_tool()`：

  ```python
  {"type": "function", "function": {
      "name": "submit_memory_digest", "strict": True,
      "description": "Submit one digest paragraph with its record references for each category, "
                     "or null for a category without records.",
      "parameters": deepseek_strict_schema(MemoryDigestRequest)}}
  ```

- 参数 Schema：`MemoryDigestRequest` 与 `DigestParagraph` 都继承 `ToolRequestModel`（state/interaction/schema.py:37-40，不带 description）；三个类别键的类型是 `Annotated[DigestParagraph | None, WithJsonSchema({"anyOf": [DigestParagraph.model_json_schema(mode="validation"), {"type": "null"}]})]`，内联、不产生 `$defs`。`deepseek_strict_schema(MemoryDigestRequest)`（:56-64）逐值等于：

  ```json
  {"additionalProperties":false,"properties":{"food":{"anyOf":[{"additionalProperties":false,"properties":{"text":{"title":"Text","type":"string"},"refs":{"items":{"type":"string"},"title":"Refs","type":"array"}},"required":["text","refs"],"title":"DigestParagraph","type":"object"},{"type":"null"}]},"work":{"anyOf":[{"additionalProperties":false,"properties":{"text":{"title":"Text","type":"string"},"refs":{"items":{"type":"string"},"title":"Refs","type":"array"}},"required":["text","refs"],"title":"DigestParagraph","type":"object"},{"type":"null"}]},"life":{"anyOf":[{"additionalProperties":false,"properties":{"text":{"title":"Text","type":"string"},"refs":{"items":{"type":"string"},"title":"Refs","type":"array"}},"required":["text","refs"],"title":"DigestParagraph","type":"object"},{"type":"null"}]}},"required":["food","work","life"],"title":"MemoryDigestRequest","type":"object"}
  ```

  长度和条数上限不写进 Schema（构建器拒绝 `maxLength`、`minItems` 等键，:45-52），由下文的守卫执行。
- `call_id` 为 `f"memory-digest:{agent_id}:{tier}:{period}:attempt:{n}"`，n 从 1 起，例如 `memory-digest:1:day:2026-10-09:attempt:1`、`memory-digest:1:month:2026-10:attempt:2`、`memory-digest:1:year:2026:attempt:1`。下文称去掉 `:attempt:{n}` 的前缀为这一期的调用前缀。
- 请求与响应证据的 `data.purpose` 是 `MEMORY_DIGEST_PURPOSE = "memory_digest"`（state/model/exchange.py，与 `PLAN_DAY_PURPOSE`（:11-12）并列）。请求字典本身不带 purpose。
- 重试请求 = 原始请求的消息之后接一条 `correction_message(raw, error, "Return a complete new submit_memory_digest tool call using the unchanged schema.")`（foundation/model_feedback.py:15-19）。每次重试都基于原始请求，只接上一次的响应和错误，不累积。

### 提示原文

三份提示按第四批裁定写成日记：记要紧的事和当时的感受，时间、次数用粗略的说法，不记流水账。第五批裁定再改日回顾的三处（字数写成「150 到 220 字为宜，连标点在内不能超过 250 字」；时间只说早上、中午、下午、晚上，不写钟点，连「八点十一」「九点一刻」这样的说法也不写；字数紧时先省经过的细节，感受和看法留着），月、年回顾只在字数后加「（标点也算）」。它们是 `simulation_engine/evidence/memory_digest.py` 的模块常量，计入 `code_sha256`，与 `PLAN_DAY_PROMPT` 同样处理。各为一行，没有首尾空白和换行；`DIGEST_PROMPTS = MappingProxyType({"day": DAY_DIGEST_PROMPT, "month": MONTH_DIGEST_PROMPT, "year": YEAR_DIGEST_PROMPT})`。

`DAY_DIGEST_PROMPT`：

```text
你替模拟世界里的一个人物写他昨天的日记。输入按饮食、工作、生活三类给出他昨天的记录：「核验」是环境确认的经过，「当时查到」是他当时查询看到的结果，「自述」是他当时写下的话（「名称：」是收尾或暂停的总结，「进展·」是中途写的进展，「看法·」是看法，「记住：」是想记住的事，「正文：」是回复正文），每条带编号。每类写一段日记，150 到 220 字为宜，连标点在内不能超过 250 字，用他的第一人称，像人回想昨天那样只记要紧的事和当时的感受，不记流水账：经过以核验为准，挑要紧的写，吃了什么、做了什么、去了哪里、和谁打了交道，这些东西写出名字；时间只说早上、中午、下午、晚上，不写钟点，「八点十一」「九点一刻」这样的说法也不写；价钱、数量和花了多久只在对他要紧时顺口带一句，比如「有点贵」「等了挺久」，不逐项列出；感受和看法以自述为准，写他喜不喜欢、累不累、还想不想再来、打算保持还是改掉，不替他编没写过的感受；字数紧时先省经过的细节，感受和看法留着。只凭昨天的记录写，不补造没发生的事或别人说的话，不拿别的日子作比较，不写编号、订单号或其他内部标识。在 refs 里写所依据的记录编号，至少一个。某一类没有记录时写 null。只调用一次 submit_memory_digest。
```

`MONTH_DIGEST_PROMPT`：

```text
你替模拟世界里的一个人物把他上个月的日记整理成这个月的回忆。输入按饮食、工作、生活三类给出那个月每一天的日回顾，每段带日期和编号，没整理出来的日子写着「未整理」；另附代码从事件算出的次数统计，让你知道哪些事常有。每类写一段不超过 800 字（标点也算）的话，用他的第一人称，像人回想上个月那样写：这类事有什么规律和习惯，常吃什么、常做什么、常去哪里，有什么变化，哪些事值得记住；他对具体的东西、地方、人和做法的看法和打算，喜欢的、不喜欢的、说了要保持或要改掉的，后来有没有变。时间说月初、月中、月底、某个周末这样的说法，次数说几乎每天、常常、偶尔一两次这样的说法，不照抄日期和统计行。只凭输入写，不补造输入里没有的经过、看法或别人说的话，不写编号或其他内部标识。在 refs 里写所依据的日回顾编号，至少一个。某一类没有输入时写 null。只调用一次 submit_memory_digest。
```

`YEAR_DIGEST_PROMPT`：

```text
你替模拟世界里的一个人物把他上一年的月回顾整理成这一年的回忆。输入按饮食、工作、生活三类给出那一年每个月的月回顾，每段带月份和编号，没整理出来的月份写着「未整理」；另附代码从事件算出的全年次数统计，让你知道哪些事常有。每类写一段不超过 2000 字（标点也算）的话，用他的第一人称，像人回想过去一年那样写：这一年这类事固定下来的习惯、口味和做法，哪些看法后来变了、大约什么时候变的，到年底还成立的看法和打算，哪些事值得记住。时间说年初、春天、几月份、年底这样的说法，次数说几乎每天、常常、偶尔这样的说法，不照抄统计行。只凭输入写，不补造输入里没有的经过、看法或别人说的话，不写编号或其他内部标识。在 refs 里写所依据的月回顾编号，至少一个。某一类没有输入时写 null。只调用一次 submit_memory_digest。
```

### 输出校验

一次尝试的校验由 `check_attempt(inputs, raw, accepted)` 完成，`accepted` 是此前尝试已接受的类（键 → `DigestParagraph`）。它返回新的 `accepted` 和这次尝试的错误（全部通过时为 None）。

**响应级**，按次序检查，第一处不通过即为这次尝试的错误，这次尝试不接受任何类：

1. `choices = raw.get("choices")` 是恰含一个元素的列表，且这个元素是对象（写法同 day_planning.py:207-210 的 `_tool_arguments`）：否则错误 `response must contain exactly one choice`，先写错误日志 `Memory digest response does not contain exactly one choice`。
2. `choices[0].get("finish_reason") == "length"`：错误 `memory digest output was truncated at max_tokens`，先写错误日志 `Memory digest output was truncated at max_tokens`。
3. `message = choices[0].get("message")`，`calls = message.get("tool_calls") if isinstance(message, dict) else None`，`tool_arguments(calls, MEMORY_DIGEST_TOOL, MemoryDigestOutputError)`（model_feedback.py:41-57）要求恰为一个 `submit_memory_digest` 调用、参数是字符串，否则给出它的三种错误之一（它先写同文错误日志，因为 `MemoryDigestOutputError` 不是 `WorldMockError`）。
4. `MemoryDigestRequest.model_validate_json(arguments)`：失败时错误为 `schema_feedback(error, "arguments")`（model_feedback.py:29-31），例如 `food.text: Input should be a valid string`、`x: Extra inputs are not permitted`、`arguments: Invalid JSON: …`。

**逐类**，按 `food`、`work`、`life` 的次序：

- 这一类没有输入：值必须是 null，否则错误 `digests.{key} must be null: the input has no {key} records`。没有输入的类从不进 `fallback`。
- 这一类有输入、此前的尝试已接受它：跳过，这次尝试给它的值不看。
- 这一类有输入、还没接受：值为 null 时错误 `digests.{key} must not be null: the input has {key} records`；否则按下表次序跑全部守卫，一条问题都没有才接受这一段。

**守卫表**（`{key}` 是类别键，`{tier}` 是 `day`、`month` 或 `year`）：

| 守卫 | 适用 | 规则 | 不通过时的问题文字 |
| --- | --- | --- | --- |
| 正文非空 | 全部 | `text.strip()` 非空 | `digests.{key}.text must not be empty` |
| 正文长度 | 全部 | `len(text)`（标点也算）不超过日 250、月 800、年 2000 | `digests.{key}.text has {n} characters counting punctuation; the {tier} digest limit is {limit}; shorten it by at least {n - limit}`，其中 `n = len(text)` |
| 依据非空 | 全部 | `refs` 非空 | `digests.{key}.refs must cite at least one record number` |
| 依据归属 | 全部 | 每个 ref 都是这一类某一行的编号 | `digests.{key}.refs: {ref} is not a {key} record number` |

- 「依据归属」每个不合的 ref 一条问题，按 refs 的次序，同一个 ref 只报一次。
- 「正文长度」的问题文字写明连标点的字数 `n`、这一级的上限和至少要删去的字数 `n - limit`（第五批裁定；此前的写法是 `digests.{key}.text has {len(text)} characters; the {tier} digest limit is {limit}`，第 09 章的试点工具两种都认）。其余问题文字不变。
- 守卫只有这四道（第四批裁定；第五批裁定也不加守卫，钟点的写法只写进提示）。正文写了什么不检查：名称是否出自输入，时刻、价钱、数量、日期、月份和次数是否出自输入或照抄统计行，有没有中文，含不含十六进制串，都不影响接受。守卫只看正文是否空白、正文的 `len`、`refs` 和这一类各行的编号，不读目录，不切句，不提取数字。

**错误文字。** 一次尝试的全部问题按类别次序、再按守卫表的次序，用 `"; "` 连接，作为 `MemoryDigestOutputError` 的文字；它写进这次响应证据的 `error`，也附回下一次请求。

**接受。** 一类通过全部守卫，就记入 `accepted`，之后的尝试不再看这一类。没有错误时这次尝试结束校验；有错误时只要还有尝试次数就重试，重试的错误文字只含仍未接受的类和多写了内容的无输入类。

### 重试、降级与传输失败

- 尝试次数上限是 `engine.context.config.model.reflection_validation_max_retries + 1`（configuration/inputs/models.py:224，缺省 3，即最多 4 次）。字段名不变，ModelConfig 键集不变，运行清单保持 7；字段说明改为回顾的校验重试（docs/design/08-configuration.md:135，见「设计文档」）。零点计划仍用 `day_plan_validation_max_retries`，用尽仍是运行故障（runner.py:663-665）。
- 某次尝试没有错误：停止，登记。
- 最后一次尝试仍有错误：不抛出，运行继续。有输入而从未被接受的类记为 null 并列入 `fallback`；已接受的类照常登记。先写错误日志 `f"Memory digest validation retries exhausted for {prefix}; fallback categories: {','.join(fallback)}"`（`prefix` 是调用前缀，`fallback` 是这一期 `fallback` 的英文键，按 `food`、`work`、`life` 次序）。
- 每次尝试先把请求证据加进本期的证据列表，再发送；每次响应（合格或不合格）都写一条响应证据，`error` 是这次尝试的错误文字或 null。每次不合格的尝试另写错误日志 `Memory digest call {call_id} failed validation`。
- 传输、鉴权或配置失败（`caller.call` 抛出）：由 `runner._call_memory_digest` 处理，与零点计划的 `_call_plan_day` 相同（runner.py:675-694）。写错误日志 `Memory digest model request failed: {异常类型名}`，写这次调用的失败运维记录，把失败证据接到证据列表后经 `store.append_evidence(evidence)` 先提交已有的请求、响应和失败证据，再原样抛出。零点分支里由 AS2 登记运行故障；普通恢复的初始化里先 `engine.fail_run(repr(error))`；检查点路径照样向上抛出。这一期不登记回顾事件。
- 停放与整次期限照[配置](../../../design/08-configuration.md)「模型配置与凭据」的现行规则；停放中模拟时间不前进。

### 登记与事件

`digest_outcome(inputs, accepted)` 给出 `DigestOutcome(period, digests, fallback)`；`_generate_memory_digest` 随后调用 `engine.register_memory_digest(agent_id, outcome, evidence)`，它转给 `TimeAdvanceCoordinator.register_memory_digest(agent_id, outcome, evidence)`：

- `TimeAdvanceCoordinator.register_memory_digest` 先查证据：`evidence` 为空时先写同文错误日志 `Memory digest registration requires model evidence`，再抛 `ProtocolError("Memory digest registration requires model evidence")`；否则 `causes = [evidence[-1].event_id]`，调用 `memory_digest.registration_draft(self.context, agent_id, outcome, causes)`。
- 「已登记」的检查与日志只在 `registration_draft` 里做一次，`register_memory_digest` 不重复检查：`registered_digests(context.store, agent_id)` 里已有 `(outcome.period.tier, outcome.period.period)` 时，先写错误日志 `f"Memory digest was already generated for {prefix}"`（`prefix` 是这一期的调用前缀），再抛 `ProtocolError("Memory digest was already generated for this period")`。调用方的候选计算已经跳过已登记的期次，所以这只在协议被破坏时出现。
- 提交方式与 `register_day_plan`（simulation_engine/scheduling/clock/time_advance.py:211-225）相同：`staged = state.model_copy(deep=True)`，`staged.version = state.version + 1`，`context.commit_step(staged, [*evidence, draft])`，证据与登记事件同一事务提交。不同的是回顾不改任何状态：不写 `day_plan`、`plan_submitted_dates` 或任何 AgentState 字段，候选世界除版本外与当前世界相同。

**登记事件** `registration_draft(context, agent_id, outcome, causes)`：

- 信封：`event_type="environment_event"`，`occurred_at=state.current_time`，`agent_id` 为本人，`caused_by=list(causes)`，即 `[evidence[-1].event_id]`（最后一次响应），`visibility="system"`，`source_kind="model"`，`resource_changes` 为空，`decision_id`、`action_id`、`activity_id` 为 null。
- `data` 恰为下面五个键，按此次序：

  ```json
  {"kind":"memory_digest_generated","tier":"day","period":"2026-10-09",
   "digests":{"food":{"text":"…","source_event_ids":["…"],"cited_event_ids":["…"]},"work":null,"life":{"text":"…","source_event_ids":["…"],"cited_event_ids":["…"]}},
   "fallback":["work"]}
  ```

- `digests` 恰有 `food`、`work`、`life` 三个键，按此次序；接受了的类是 `{"text", "source_event_ids", "cited_event_ids"}`（按此次序），其余为 null。
  - `text`：模型给的原文，不修剪、不改写。
  - `source_event_ids`：这一类全部输入行背后的事件，按行序去重。日回顾是每行所属条目的来源事件身份（第 02 章）；月回顾是每行那天的日回顾事件身份；年回顾是每行那个月的月回顾事件身份。次数统计不进来源。
  - `cited_event_ids`：`refs` 各编号那一行背后的同一种事件，按 refs 次序去重。它只表示模型自报的依据，不作来源证明。
- `fallback`：有输入、重试用尽仍未接受的类的键，按 `food`、`work`、`life` 次序。某类为 null 且在 `fallback` 里，表示「没能整理」；为 null 且不在里面，表示这一期没有这一类的输入。三类都没有输入的期次不写事件（见「不需要回顾」）。

**闭集校验。** `validate_environment_event`（recording_engine/events/environment_events.py:23-44）新增分支 `kind == "memory_digest_generated"` → `_memory_digest_generated(event)`。它在草稿构造、写入和读取时都执行（recording_engine/events/models.py:155-167）。函数说明写明：回顾正文是模型写的主观回忆，不是核验事实，这里只核对形状。任一条不成立时经 `_invalid`（:118-121，先写同文错误日志）抛 `ValueError`：

| 检查 | 消息 |
| --- | --- |
| `set(data) == {"kind", "tier", "period", "digests", "fallback"}`；`source_kind == "model"`；`visibility == "system"`；`resource_changes` 为空；`agent_id` 非空；`caused_by` 恰一项 | `Memory digest event fields or source are invalid` |
| `tier` 是 `MEMORY_DIGEST_TIERS` 中的一个；`period` 是字符串，并且与 `tier` 对应：`day` 时 `date.fromisoformat(p).isoformat() == p`（抛 `ValueError` 视为不合），`month` 时 `re.fullmatch(r"[0-9]{4}-(0[1-9]\|1[0-2])", p)` 不为 None，`year` 时 `re.fullmatch(r"[0-9]{4}", p)` 不为 None；任一不成立都报这条消息 | `Memory digest event tier or period is invalid` |
| `digests` 是键恰为 `food`、`work`、`life` 的对象；每个值为 null 或键恰为 `text`、`source_event_ids`、`cited_event_ids` 的对象；`text` 是 `strip()` 后非空、`len` 不超过该级上限的字符串；两个身份列表都非空、元素是非空白字符串且不重复；`cited_event_ids` 是 `source_event_ids` 的子集；至少一个值非 null 或 `fallback` 非空 | `Memory digest event digests are invalid` |
| `fallback` 是列表，元素属于三个键、不重复、按 `food`、`work`、`life` 的相对次序；其中每个键的 `digests` 值为 null | `Memory digest event fallback is invalid` |

**读取：** `registered_digests(store, agent_id)`（simulation_engine/observation/memory/digests.py）是回顾事件的唯一读取入口，返回 `(tier, period) → Event` 的只读映射。按足迹库对象与人物增量缓存：每次调用先记下 `store.last_event_seq`，再经 `store.read_agent_events("environment_event", agent_id, after=已读到的序号)`（recording_engine/storage/store.py:403-411）读新事件，只收 `data.kind == "memory_digest_generated"` 的。同一 `(tier, period)` 读到第二条时先写同文错误日志 `Memory digest period was registered twice`，再抛 `IntegrityError("Memory digest period was registered twice")`。第 03 章的「近三天」和第 05 章的检索都经它读回顾，按自己的截止序号筛选。

### 恢复、分支、重放与导出

- 恢复、重放和导出都只读事件，已登记的回顾从不重新生成。
- 普通恢复和反事实分支继承检查点及以前的事件（`copy_event_prefix`，store.py:449），其中的回顾事件照样算已登记；检查点之后经过的零点在分支里由 `LifeEnv.step` 重新生成，语义与零点计划相同。
- 零点崩溃：零点推进已提交、回顾调用传输失败时，证据先提交，运行以故障停下，这一期没有回顾事件。从零点之前的检查点开分支，分支库里没有父运行在检查点之后的事件，经过那个零点时这一期只生成一次；分支里的 `call_id` 可以与父运行相同，因为父运行检查点之后的证据不进分支库。
- 检查点之后、下一个零点之前恢复时，候选计算按「哪些期次要生成」补齐近三天里缺的日回顾和上个月、上一年缺的月、年回顾；正常运行里检查点总在零点任务之后，所以这一步什么都不做。
- 工作区快照的格式和内容、恢复校验、检查点结构 1、导出结构 8 都不变；导出产物集合不变，回顾事件随事件导出。

### 回顾不是事实

- 环境从不读回顾来结算、核验或限制任何请求。读回顾事件的只有：记忆层「近三天」（第 03 章）、`search_memory`（第 05 章）、本章的月回顾和年回顾输入、分析工具（第 09 章）。
- 回顾是日记式的回忆，记要紧的事和当时的感受，不是设备日志（第四批裁定）。运行时只做「输出校验」的格式与长度检查，正文里的名称、时刻、价钱、数量、日期和次数都不查：写了输入里没有的东西、把早饭写成午饭、记错价钱或次数的段落，只要格式与长度合格，照样接受和登记。
- 回顾与当天记录是否相符只由试点衡量：第 09 章的 M11 不读回顾的输入、不加载运行时代码，从事件独立测量饮食回顾的覆盖与错配；试点报告附每天的回顾与当天核验的对照表（`digest_review`），由模型核对的抽查写「模型核对」，不写人工抽查。

### 质量报告

- `CALL_CLASSES`（recording_engine/reports/quality.py:49-50）末尾加 `memory_digest`，成为七类：`("fresh_decision_turn", "turn_with_history", "truncation_retry", "consolidation", "reflection", "plan_day", "memory_digest")`。
- `_call_class`（:795-812）在 `purpose == PLAN_DAY_PURPOSE` 一句之后加 `if purpose == MEMORY_DIGEST_PURPOSE: return "memory_digest"`；日、月、年三级都归这一类，按 purpose 判定，不看工具名。
- `_call_class` 的文档字符串（:796-799）在「purpose 为 plan_day 是零点计划」之后加「；purpose 为 memory_digest 是日、月、年回顾」。
- `build_quality_report` 的 `schema_version` 从 9 改为 10（:86）；说明里的「报告结构 9」（:72）改为「报告结构 10」，并写明 `memory_digest` 调用类是升到 10 的原因；:47、:51、:824 的「六类」改为「七类」。
- 带 purpose 的请求不算记忆写入点，回顾响应的错误不计入 `memory_protocol_error`（`_inspect_memory_response`（:430）已按 purpose 跳过，:435-436，代码不改；它的文档字符串 :433 改为「带 purpose 的系统请求（工作验收、零点计划 plan_day、日月年回顾 memory_digest）不是记忆写入点，其响应错误不计入记忆问题。」）；截断统计 `purpose_counts` 里回顾的截断记在 `memory_digest` 键下（:971，不改）。
- LabelKit 不核对质量报告的版本号；`consolidation`、`reflection` 两类的去留归第 08 章。

### 运维日志

回顾调用由 `runner._log_memory_digest_call` 记录，形状同零点计划的 `_log_plan_day_call`（见「接口与文件」）。每次回顾调用结束写一条 `agent_runtime` 记录：

```json
{"code":"MEMORY_DIGEST_CALL","detail":"Memory digest call {call_id} ended with {outcome}",
 "agent_id":1,"call_id":"memory-digest:1:day:2026-10-09:attempt:1","outcome":"response","call_ms":812.4}
```

- `outcome` 为 `response` 时级别 `info`，为 `failure` 时级别 `error`；`call_ms` 保留一位小数。
- 这次调用有失败发送时，另写一条级别 `warn` 的 `transport_wait_record(call_id, attempts)` 并带 `agent_id`，与零点计划相同。
- 零点计划的记录码仍是 `PLAN_DAY_CALL`，文字仍是 `Plan day call {call_id} ended with {outcome}`。

## 接口与文件

### `src/worldmock/simulation_engine/evidence/memory_digest.py`（新建）

回顾的输入、提示、工具、守卫、结果、证据草稿与登记草稿。函数都是纯函数或只读足迹库；只有一处进程内缓存（「哪些期次要生成」的「缓存」）。

| 名称 | 签名与说明 |
| --- | --- |
| `MEMORY_DIGEST_MAX_TOKENS` | `int = 8000` |
| `MEMORY_DIGEST_TOOL` | `str = "submit_memory_digest"` |
| `DAY_DIGEST_PROMPT`、`MONTH_DIGEST_PROMPT`、`YEAR_DIGEST_PROMPT`、`DIGEST_PROMPTS` | 见「提示原文」 |
| `class MemoryDigestOutputError(ValueError)` | 一次尝试不合格；文字是附回模型的英文问题列表 |
| `class DigestParagraph(ToolRequestModel)` | `text: str`；`refs: list[str]` |
| `class MemoryDigestRequest(ToolRequestModel)` | `food`、`work`、`life`，类型见「请求」 |
| `@dataclass(frozen=True, slots=True) class DigestPeriod` | `tier: str`；`period: str`；`def call_id(self, agent_id: int, attempt: int) -> str` |
| `@dataclass(frozen=True, slots=True) class DigestRow` | `number: str`（`n3`、`d2`、`m1`）；`payload: dict`（交给模型的那一行）；`sources: tuple[str, ...]`（这一行背后的事件身份） |
| `@dataclass(frozen=True, slots=True) class DigestInputs` | `period: DigestPeriod`；`payload: dict`（用户消息，月、年回顾的次数统计行只在这里）；`rows: Mapping[str, tuple[DigestRow, ...]]`（类别键 → 行）。`check_attempt` 与 `digest_outcome` 只读这三个字段 |
| `@dataclass(frozen=True, slots=True) class DigestOutcome` | `period: DigestPeriod`；`digests: Mapping[str, dict \| None]`（事件 `data.digests` 的值）；`fallback: tuple[str, ...]` |
| `memory_digest_tool() -> dict[str, Any]` | 唯一严格工具 |
| `pending_inputs(context: EngineContext, observations: ObservationBuilder, agent_id: int, tier: str) -> list[DigestInputs]` | 这一级的候选期次中未登记、有输入的各期输入，按期次先后；没有输入的期次记进「不需要」缓存。层上下文取 `observations._layer_context(agent, [])`，与 day_plan_inputs.py:164 相同 |
| `prepare_digest_request(inputs: DigestInputs, model_name: str, metadata: ModelMetadata) -> dict[str, Any]` | 请求与容量预检 |
| `prepare_digest_correction(request: dict[str, Any], raw: dict[str, Any], error: MemoryDigestOutputError) -> dict[str, Any]` | 重试请求 |
| `check_attempt(inputs: DigestInputs, raw: dict[str, Any], accepted: Mapping[str, DigestParagraph]) -> tuple[dict[str, DigestParagraph], MemoryDigestOutputError \| None]` | 一次尝试的校验与接受 |
| `digest_outcome(inputs: DigestInputs, accepted: Mapping[str, DigestParagraph]) -> DigestOutcome` | 正文、来源、依据与 fallback |
| `request_draft(now: int, agent_id: int, link: tuple[str, int, str \| None], request: dict[str, Any]) -> EventDraft` | 一次回顾模型请求的证据；`link = (call_id, attempt, previous_event_id)`；`data` 与 day_planning.py:173-181 的 `request_draft` 同键同序：`phase="request"`、`purpose`、`call_id`、`attempt`、`request`、`response=None`、`error=None`，其中 `purpose=MEMORY_DIGEST_PURPOSE`；有前次响应时 `caused_by` 指向它，否则为空 |
| `response_draft(now: int, agent_id: int, link: tuple[str, int, str, tuple[dict, ...]], raw: dict[str, Any], error: BaseException \| None) -> EventDraft` | 一次回顾模型响应的证据；`link = (call_id, attempt, request_event_id, transport_attempts)`；`data` 与 day_planning.py:184-192 同键同序：`phase="response"`、`purpose=MEMORY_DIGEST_PURPOSE`、`call_id`、`attempt`、`request=None`、`response`、`usage`（`raw.get("usage")`）、`error`（`str(error)` 或 None）、`transport_attempts`（列表）；`caused_by` 为请求证据 |
| `failure_draft(now: int, agent_id: int, link: tuple[str, int, str, tuple[dict, ...]], error: BaseException) -> EventDraft` | 回顾的传输或配置失败证据；`link` 同上；`data` 与 day_planning.py:195-202 同键同序：`phase="failure"`、`purpose=MEMORY_DIGEST_PURPOSE`、`call_id`、`attempt`、`request=None`、`response=None`、`error=repr(error)`、`transport_attempts` |
| `registration_draft(context: EngineContext, agent_id: int, outcome: DigestOutcome, causes: list[str]) -> EventDraft` | 这一期已登记时写错误日志并抛协议错误（「已登记」检查的唯一位置）；否则给出登记事件 |

三种证据草稿的信封与 day_planning.py 的同名函数相同：`event_type` 依次为 `model_request`、`model_response`、`model_response`，`visibility="system"`、`source_kind="model"`；文档字符串写明是日、月、年回顾的证据。day_planning.py 的三个草稿函数不改，零点计划照旧用它们。

私有辅助函数（每个不超过 50 行、5 个参数，中文文档字符串）：日、月、年三种输入的装配，候选日与各日各类有无输入的判定，响应级检查与守卫表的四道守卫（只读 `DigestInputs`）。模块不保存世界状态，不写事件，不读目录名称，不导入 `contains_hex_identity`。

### `src/worldmock/simulation_engine/observation/memory/digests.py`（新建，位于记忆投影包 `observation/memory/`）

| 名称 | 签名与说明 |
| --- | --- |
| `registered_digests(store: FootprintStore, agent_id: int) -> Mapping[tuple[str, str], Event]` | 见「登记与事件」的「读取」 |

### `src/worldmock/simulation_engine/observation/memory/days.py`（第 03 章的模块，本章加一个函数）

| 名称 | 签名与说明 |
| --- | --- |
| `count_lines(context: LayerContext, first_day: str, last_day: str) -> dict[str, tuple[str, ...]]` | 闭区间内各类的次数统计行，键是中文类别名；规则见「输入」的「次数统计」 |

count_lines 写在 days.py（提案第三章代码表「days.py……P3 加次数统计」），读 `store = context.engine.store`（`LayerContext.engine` 是 `EngineContext`）。它用到的第 03 章类别推导（`FOOD_GOODS_CATEGORIES`、`activity_category`、`memory_book`、`purchase_goods`）经 days.py 的模块级导入取得：第 03 章允许 days.py 导入 categories.py（它的禁令只针对 progress_layer.py、memory_layer.py 与 day_plan_inputs.py），第 02 章的 `published_today` 已在模块级用 `memory_book`；不做函数内导入，`DELAYED_IMPORTS` 不加键。记 `cached = entries.agent_events(store, context.agent_id, context.catalog.kit_states())`、`book = memory_book(store, context.inputs)`：区间内结束的一项本人活动的快照取 `cached.snapshots[activity_id]` 的最后一份，它的类别是 `activity_category(book, 快照, purchase_goods(快照, cached.first_results[快照.origin_action_id]))`（第 03 章第 3 节、第 02 章【E11】的同一读法，不读 `WorldState`）；短名取 `entries.short_name(快照.name)`（第 02 章）；「活动 → 所用食材包」取 `entries.activity_facts(cached).kits`（第 02、08 章）。不导入 settled_text.py。

### `src/worldmock/runtime/lifecycle/runner.py`

| 改动 | 说明 |
| --- | --- |
| 新增 `class MemoryDigestClient(IndependentModelClient)` | `__init__(self, model_identity: dict[str, Any])` 调 `super().__init__(model_identity, "Memory digest")`，写法同 `PlanDayClient`（:611-616） |
| 新增 `async def settle_pending_memory_digests(engine: Any, client: Any \| None = None) -> list[dict]` | 运行已到终点返回 `[]`；对每个人物（编号升序）、每一级（`MEMORY_DIGEST_TIERS` 次序）取 `pending_inputs`，逐期生成；第一次需要调用时才构造 `MemoryDigestClient(engine.context.store.manifest["stage_model_identity"])`（传入 `client` 时用它）；返回每期一项 `{"agent_id", "tier", "period", "fallback"}`。函数内导入 `memory_digest` |
| 新增 `async def _generate_memory_digest(engine: Any, caller: Any, agent_id: int, inputs: Any) -> dict` | 「重试、降级与传输失败」的循环：每次尝试先 `evidence.append(memory_digest.request_draft(now, agent_id, (call_id, attempt, previous), request))`，`link = (call_id, attempt, evidence[-1].event_id)`，再 `raw, attempts = await _call_memory_digest(engine, caller, request, (evidence, link, agent_id))`；每次尝试的响应证据恰由函数体里唯一的一行 `evidence.append(memory_digest.response_draft(now, agent_id, (*link, attempts), raw, error))` 写出，其中 `error` 是 `check_attempt` 返回的这次尝试的错误（全部通过时为 None）；最后 `engine.register_memory_digest(agent_id, outcome, evidence)`；函数内导入 `memory_digest`；源码变异 `memory_digest_response_attempts_dropped` 以这一行为锚点 |
| 新增 `async def _call_memory_digest(engine: Any, caller: Any, request: dict, context: tuple[list, tuple, int]) -> tuple[dict, tuple[dict, ...]]` | 写法同 `_call_plan_day`（:675-694）：`context = (evidence, link, agent_id)`；在 `collect_transport_attempts()` 里 `await caller.call(request)`；抛出时写错误日志 `"Memory digest model request failed: %s"`（参数为异常类型名），`_log_memory_digest_call(..., "failure")`，把 `memory_digest.failure_draft(now, agent_id, (*link, tuple(collected)), error)` 接到证据列表后 `engine.context.store.append_evidence(evidence)`，再原样抛出；成功时 `_log_memory_digest_call(..., "response")`，返回原始响应与这次调用的失败发送；函数内导入 `agentsociety2.config.llm_dispatcher` 的 `collect_transport_attempts` 与 `memory_digest` |
| 新增 `def _log_memory_digest_call(engine: Any, ids: tuple[str, int], started: float, attempts: tuple, outcome: str) -> None` | 写法同 `_log_plan_day_call`（:697-708）：`ids = (call_id, agent_id)`；记录码 `MEMORY_DIGEST_CALL`，`detail = f"Memory digest call {call_id} ended with {outcome}"`，形状见「运维日志」；有失败发送时另写传输等待记录；函数内导入 opslog 的 `transport_wait_record` |
| `settle_pending_day_plans` 的说明（:620-626） | 「LifeEnv 在零点推进之后」改为「LifeEnv 在零点的回顾任务之后」 |

`_generate_day_plan`（:640-672）、`_call_plan_day`（:675-694）、`_log_plan_day_call`（:697-708）不改；本章不改 simulation_engine/scheduling/plans/day_planning.py。

### `src/worldmock/simulation_engine/scheduling/clock/time_advance.py`

新增 `TimeAdvanceCoordinator.register_memory_digest(self, agent_id: int, outcome: DigestOutcome, evidence: list[EventDraft]) -> None`，规则见「登记与事件」。模块级导入 `memory_digest`（与 `day_planning` 同样）。

### `src/worldmock/simulation_engine/engine.py`

新增 `LifeEngine.register_memory_digest(self, agent_id: int, outcome: DigestOutcome, evidence: list[EventDraft]) -> None`，说明「系统登记一期回顾并同事务保存其模型证据」，转给 `self.clock.register_memory_digest`。

### `custom/envs/life_env.py`

- 导入改为 `from worldmock.runtime.lifecycle.runner import settle_pending_day_plans, settle_pending_memory_digests`。
- `step`、`checkpoint`、`init` 的恢复分支照「零点任务的次序」修改；`_settle_day_plans_on_resume` 删除，新增 `async def _settle_midnight_tasks_on_resume(self, engine: LifeEngine) -> None`。
- 说明文字：`init`（:78-83）、`checkpoint`（:206-210）、`step`（:236）写明先回顾、后计划；`close`（:289）的「零点计划用过的」改为「零点计划与回顾用过的」。

### `src/worldmock/state/model/exchange.py`

新增 `MEMORY_DIGEST_PURPOSE = "memory_digest"`（注释：日、月、年回顾请求与响应的用途标记）；`is_action_request` 的说明（:23）「工作验收与零点计划带 purpose」改为「工作验收、零点计划与日、月、年回顾带 purpose」。

### `src/worldmock/state/model/memory.py`

新增共享常量（记录引擎只能从 `worldmock.state` 引用）：

```python
#: 回顾登记事件的 data.kind。
MEMORY_DIGEST_KIND = "memory_digest_generated"
#: 回顾的三级，按零点任务里的处理次序。
MEMORY_DIGEST_TIERS = ("day", "month", "year")
#: 回顾正文与事件里的类别键，与 MEMORY_CATEGORIES 逐一对应。
MEMORY_DIGEST_KEYS = ("food", "work", "life")
#: 各级每类正文的字数上限（len，标点也算）。
MEMORY_DIGEST_TEXT_LIMITS = MappingProxyType({"day": 250, "month": 800, "year": 2000})
```

### `src/worldmock/state/interaction/requests.py`

新增 `def contains_hex_identity(text: str) -> bool`（是否含 8 位以上十六进制串，即 `_MEMORY_HEX_IDENTITY.search(text) is not None`，:419）；`validate_memory_text`（:423-438）改用它，行为不变。只有 `validate_memory_text` 用它：第四批裁定之后回顾不查十六进制串，函数名与位置照旧。

### `src/worldmock/recording_engine/events/environment_events.py`

新增 `_memory_digest_generated(event) -> None` 与 `validate_environment_event` 中的分支，见「闭集校验」；从 `worldmock.state.model.memory` 导入上面四个常量。

### `src/worldmock/recording_engine/reports/quality.py`

见「质量报告」。从 `worldmock.state.model.exchange` 导入 `MEMORY_DIGEST_PURPOSE`。

### `src/worldmock/recording_engine/storage/store.py`

只改 `FootprintStore.append_evidence`（:195）的文档字符串（:198）：「观察、模型请求与响应、记忆更新、决策开始与行动请求、运行失败和零点计划失败证据都走这里」改为「观察、模型请求与响应、记忆更新、决策开始与行动请求、运行失败、零点计划与回顾失败证据都走这里」。代码不变。

### `tools/analysis/behavior/metrics.py`

`_decision_model_requests` 的说明（:145-147）把日、月、年回顾列进被排除的带 purpose 的系统请求；代码不变（它已排除一切带 purpose 的请求）。

## 设计文档

文档、实现和行为测试同一次提交（docs/INDEX.md:18）。下表是每处改完之后必须写明的内容。

| 文档与位置 | 改完之后写明 |
| --- | --- |
| docs/design/06-agent.md「进度、待办与记忆」新增小节「日、月、年回顾」（锚点 `<a name="agent-日月年回顾"></a>`），放在第 03 章的「近三天」小节之后、第 05 章的「检索记忆」小节之前（各小节的次序见第 10 章「设计文档」）；原 :285-304 的就寝反思文字的删除见第 08 章 | 回顾是运行时在零点为每个人物做的独立模型调用，不经人物、不是决策；三级的期次、生成时机与零点次序（日→月→年→零点计划→节律），检查点之后与普通恢复的初始化里同样补做，运行到终点不做；哪些期次要生成（近三天的日子、上个月、上一年），三类都没有输入时不调用；三级的输入表（人物卡片只给日回顾，由环境一侧用 `render_card(persona_card(persona))` 从 persona.toml 构造，与工作区 profile 的 `persona_card` 逐字相同；条目全文按类切分并编号；工作类不启用时工作类没有行、必须为 null；月、年回顾的行与「未整理」行；次数统计的规则与写法）；请求：中文固定模板（逐字原文只在代码里），要求写成日记（要紧的事和当时的感受，时间只说早上、中午、下午、晚上而不写钟点，日回顾 150 到 220 字为宜、连标点不超过 250 字，字数紧时先省经过的细节，不记流水账）、紧凑 JSON 的用户消息、唯一严格工具 `submit_memory_digest`、思考关闭、底层不重试、输出上限见「宿主模型协议」；输出只做格式与长度检查（响应格式、逐类 null 规则、正文非空、每类字数上限（日 250、月 800、年 2000，`len` 计、标点也算；超出时问题文字写明连标点的字数和至少要删去的字数）、refs 非空且都是这一类的编号），正文写的名称、数字和时间不检查；重试次数取 `reflection_validation_max_retries`，用尽时没通过的类记 null 并列入 `fallback`、运行继续；传输失败先提交证据再以运行故障停下；结果是 `memory_digest_generated`（链到记录引擎「事件与来源」）；回顾是整理出来的回忆，不是核验事实，环境从不据它结算 |
| docs/design/06-agent.md「零点计划」:189-190 | 零点推进之后先做日、月、年回顾（链到新小节），再做零点计划，最后判定这一秒的唤醒；新建运行和反事实分支在开始的检查点之后、普通恢复在初始化时，也先补回顾、再做计划 |
| docs/design/06-agent.md「人物身份与画像」:33-35 | 本章不改写，:35 的文字照第 08 章；回顾输入里的人物卡片只在新小节「日、月、年回顾」的输入表里写明（见上一行） |
| docs/design/06-agent.md「宿主模型协议」:311 | 本章不改写，文字照第 08 章（其中「零点计划与回顾各用自己的 8,000」的 8,000 即本章的 `MEMORY_DIGEST_MAX_TOKENS`） |
| docs/design/04-simulation-engine.md「唤醒、日计划与个人事务」，在「日计划保存自然语言目标……」一段之前新增一段 | 零点边界（不是运行终点）推进提交之后依次执行：日回顾、月回顾（每月第一个零点）、年回顾（每年第一个零点）、零点计划，最后判定这一秒的唤醒与下一次决策；检查点之后和普通恢复的初始化里按同一次序补做；运行到终点时都不做；规则链到人物文档的「日、月、年回顾」与「零点计划」 |
| docs/design/04-simulation-engine.md「原子性与恢复」:741 | 「零点计划失败时的模型证据」改为「零点计划与回顾失败时的模型证据」 |
| docs/design/05-recording-engine.md「事件与来源」，在 :50-52（callback_effect 等闭合字段合同）之后新增一段 | `memory_digest_generated` 的信封（`environment_event`、来源 `model`、可见性 `system`、无资金变化、起因恰一项为最后一次响应）、`data` 的五个键与每类项的三个键、`source_event_ids` 与 `cited_event_ids` 的含义（后者只是模型自报的依据）、`fallback` 的含义（null 且在 fallback 里为「没能整理」，不在为没有输入）、它与本期模型证据在版本加一的同一次提交里写入、世界除版本外不变、正文是主观回忆；闭集校验的四类检查与四条拒绝消息；同一人物同一级同一期至多一条 |
| docs/design/05-recording-engine.md「权威事实与投影」:26-27 与「事件与来源」:130 | 「零点计划失败时的模型证据」改为「零点计划与回顾失败时的模型证据」；「零点计划的证据本就如此」改为「零点计划与回顾的证据本就如此」 |
| docs/design/05-recording-engine.md「事件与来源」:100 | 本章不改写，文字照第 08 章 |
| docs/design/05-recording-engine.md「完整导出认证」:624、:628-629 与调用类表 :637-644、:646 | :624 写「质量报告 `quality.json` 的结构号是 10。」；:628-629 保留「lineage 节是结构号升到 9 的原因」，其后加一句「调用类 `memory_digest` 是结构号升到 10 的原因」；调用类表末新增一行「`memory_digest` \| purpose 为 `memory_digest`：日、月、年回顾及其校验重试」（`consolidation`、`reflection` 两行的改写见第 08 章）；:646「六类按上表次序总是列出」改为「七类」 |
| docs/design/08-configuration.md「模型配置与凭据」:135 | `reflection_validation_max_retries`：默认 `3`，严格整数 `0–5`，日、月、年回顾输出校验重试；用完时没通过的类记为 null 并列入 `fallback`，运行继续，不是运行故障 |
| docs/design/08-configuration.md :151 | 整次期限的例子写「零点计划和回顾的 8,000 时 180 秒」 |
| docs/design/09-io-formats.md「事件与检查点」:88 与「输入与模型身份」:80 | 回顾事件 `memory_digest_generated` 与回调、时间、天气、提醒、停留事件一样使用闭合模型，写入和读取时都校验，字段归记录引擎「事件与来源」；「反思与日计划两项校验预算」改为「回顾与日计划两项校验预算」 |
| docs/design/10-runtime.md :22、:60、:66-68 | :22 本章不改写，文字照第 08 章；:60 写「零点计划与回顾的请求与响应一起落盘，它们的停放显示为 running」；:66-68 的运维日志列举在零点计划调用耗时 `PLAN_DAY_CALL` 之后加回顾调用耗时 `MEMORY_DIGEST_CALL` |
| docs/design/01-architecture.md :63、:73、:172 | 文字和链接都照第 08 章（:172 在「数字人」一行把就寝反思工具链接原位换成 `[日、月、年回顾](../../src/worldmock/simulation_engine/evidence/memory_digest.py)`，该文件由本章新建），本章不另改 |
| docs/design/00-overview.md :138「引用完整性」 | 就寝反思洞见那一句改为：日、月、年回顾每类的 `refs` 非空、全部是这一类输入行的编号，不合的类重试，用完记为 null 并列入 `fallback`（第 08 章同一格删去就寝反思一句的操作由这句改写取代，两章只留这一句；同一格 `subject` 的改写见第 07 章） |
| docs/manual/personas.md :105 | 本章不改写，文字照第 08 章 |

## 测试

新测试都带中文文档字符串。确定性客户端桩见下面的 tests/life/conftest.py。

### 新增

**tests/unit/life/test_memory_digest_output.py**（纯函数；`DigestInputs` 由测试手工构造）

| 测试 | 断言（对应的行为条款） |
| --- | --- |
| `test_digest_prompts_match_the_frozen_texts` | 三个提示常量逐字等于本章「提示原文」的日记式原文（测试里写死原文）；`DIGEST_PROMPTS` 的键恰为三级（提示原文） |
| `test_submit_memory_digest_tool_and_schema_are_pinned` | `memory_digest_tool()` 逐值等于本章的工具字典；`deepseek_strict_schema(MemoryDigestRequest)` 逐值等于本章的 Schema（请求） |
| `test_request_caps_output_and_disables_thinking[32000-8000]`、`[4096-4096]` | `prepare_digest_request` 的全部键与值；`max_tokens == min(8000, max_output_tokens)`；用户消息是紧凑、不排序的 JSON（请求、序列化） |
| `test_request_over_capacity_fails_before_any_call` | 把 `context_window` 调小：抛 `RuntimeError("memory digest request capacity exceeded")`，日志有 `Memory digest request exceeds the model context`（请求） |
| `test_call_ids_name_agent_tier_period_and_attempt` | 三级各一例的 `call_id` 文字（请求） |
| `test_correction_appends_one_message_to_the_original_request` | 第二、三次重试的消息都是原始消息加一条纠正消息，纠正消息的三个键与指令文字（请求） |
| `test_response_level_errors[no-choice]`、`[two-choices]`、`[no-tool-call]`、`[other-tool]`、`[invalid-json]`、`[missing-key]`、`[extra-key]` | 错误文字逐字；这次尝试不接受任何类（响应级） |
| `test_truncated_output_is_a_validation_failure` | `finish_reason="length"` 而工具调用合法：错误恰为 `memory digest output was truncated at max_tokens`，不接受任何类（响应级） |
| `test_null_rules_follow_category_input` | 有输入的类给 null、无输入的类给段落各报对应文字；无输入的类给 null 不报（逐类） |
| `test_text_guards[day-201]`、`[day-250]`、`[day-251]`、`[day-punctuation]`、`[month-800]`、`[month-801]`、`[year-2000]`、`[year-2001]`、`[empty]`、`[blank]` | 日回顾 201 字通过（200 < len ≤ 250 现在都接受）；三级恰在上限时通过，多一字时错误逐字为正文长度的问题文字（连标点的字数、上限、至少删 1 字）；230 个汉字加 26 个标点共 256 字也按 256 字报（至少删 6 字）；空串、全空白各报正文非空的问题文字（守卫表：正文非空、正文长度；`MEMORY_DIGEST_TEXT_LIMITS`） |
| `test_refs_must_cite_this_categorys_numbers` | 空 refs 恰报依据非空的问题文字；别的类的编号、不存在的编号各报一条依据归属的问题文字，按 refs 次序，重复的错编号只报一次（守卫表：依据非空、依据归属） |
| `test_text_within_the_limit_is_accepted_whatever_it_mentions[clock-time]`、`[price]`、`[date]`、`[count]`、`[absent-name]`、`[hex]`、`[no-chinese]` | 不超过字数上限、refs 都是这一类编号的一段，正文分别写了输入里没有的时刻（`8:09`）、价钱（`35元`）、日期（`10月9日`）、次数（月回顾里不照抄统计行的 `5 次`）、夹具目录里有而这一类输入里没有的商品名、8 位以上的十六进制串（`a1b2c3d4e5`），或全文没有中文（`ok`）：这一类被接受，这次尝试的错误为 None，`digest_outcome` 的 `text` 逐字是原文；`[count]` 为月回顾，其余为日回顾的饮食类（守卫表只有四行） |
| `test_removed_guard_names_no_longer_exist` | `memory_digest` 模块没有 `NUMBER_TOKEN`、`QUANTITY_UNITS` 属性；`DigestInputs` 的字段恰为 `period`、`payload`、`rows`，`DigestRow` 的字段恰为 `number`、`payload`、`sources`（`names`、`goods_names`、`kit_names`、`food_required`、`kits_cooked` 与只供已撤销守卫用的 `counts`、`text`、`verified` 都不存在）（接口与文件） |
| `test_problems_are_joined_in_category_and_guard_order` | 日回顾三类同时不合：food 正文 251 字（另含输入里没有的时刻、价钱和商品名）且 refs 含工作类的编号，work 正文全空白且 refs 为空，life 有输入而为 null：错误文字逐字等于正文长度、依据归属、正文非空、依据非空、null 规则这五条问题按类别、再按守卫表次序用 `"; "` 连接，没有别的问题（错误文字、守卫表只有四行） |
| `test_accepted_categories_survive_later_attempts` | 第一次 food 通过、work 不通过；第二次 food 改成不合格、work 通过：两类都按各自通过的那次接受，第二次的错误为 None（接受） |
| `test_outcome_marks_unaccepted_categories_as_fallback` | 用尽后有输入未接受的类为 null 且在 `fallback`，次序为 food、work、life；无输入的类为 null 且不在（登记事件） |
| `test_outcome_sources_and_citations_follow_rows` | `source_event_ids` 按行序去重，`cited_event_ids` 按 refs 次序去重且是前者的子集；月回顾的两者是日回顾事件身份（登记事件） |
| `test_contains_hex_identity_is_shared_with_memory_text` | `contains_hex_identity` 与 `validate_memory_text` 对同一组字符串判定一致（requests.py；回顾的守卫不用它，见 `test_text_within_the_limit_is_accepted_whatever_it_mentions[hex]`） |

**tests/unit/footprints/test_environment_events.py**（现有文件新增）

| 测试 | 断言 |
| --- | --- |
| `test_valid_digest_events_are_accepted[day]`、`[month]`、`[year]` | 三级合法草稿可以构造、写入、读回（闭集校验） |
| `test_digest_event_rejects_invalid_shapes[...]` | 逐一破坏：多键、少键、`source_kind` 为 environment、可见性 agent、带资金变化、`caused_by` 两项、`tier` 为 week、日期非规范（`2026-1-9`）、月份 `2026-13`、年份 `26`、`digests` 少一键、项多一键、正文空白、正文 251 字（day）、月份 `2026-10\n`（末尾换行）、年份 `２０２６`（全角数字）、`period` 不是字符串、身份列表为空、`cited` 不是 `source` 的子集、三类都为 null 且 `fallback` 空、`fallback` 含重复、次序颠倒、含非 null 的类：各报本章表中对应的那条消息 |
| `test_digest_event_text_may_reach_the_tier_limit[day]`、`[month]`、`[year]` | 正文连标点恰为这一级的上限（日 250、月 800、年 2000，末字是「。」）的草稿合法、正文逐字读回；多一个标点就报 `Memory digest event digests are invalid`（闭集校验跟着 `MEMORY_DIGEST_TEXT_LIMITS`） |

**tests/integration/life/test_memory_digest_runtime.py**（真实引擎，`engine_factory`；跨月、跨年的用例经 `engine_factory(mutate=...)` 改 config.toml 的 `start_time`、`stop_time`）

| 测试 | 断言（对应的行为条款） |
| --- | --- |
| `test_day_digest_payload_numbers_category_parts_of_full_entries` | 用例脚本出一天，含混合类别条目、做完的查询和跨过零点的睡眠：用户消息逐字节等于测试里写死的 JSON（先饮食、后工作、再生活连续编号；混合条目在两类各出一行，只含各自的行和项；收尾条目「时刻」为 `24:00` 排在类内最后；空类为 `[]`；人物卡片等于 `render_card(persona_card(persona))`）（输入） |
| `test_day_without_entries_calls_no_model_and_is_remembered` | 没有条目的日子：客户端桩没有被构造；再次调用时 `worldmock.simulation_engine.observation.memory.days.past_day_entries` 的调用次数为 0（monkeypatch 这个模块属性计数；memory_digest 经 `days.past_day_entries(...)` 调用它）（不需要回顾、缓存） |
| `test_category_without_entries_is_null_and_not_fallback` | 只有饮食和生活条目的日子：事件 `work` 为 null，`fallback` 为 `[]`（登记事件） |
| `test_disabled_work_category_is_always_null` | 无工作安排、目录无工作覆盖的人物（`book.categories == ("饮食", "生活")`）跨过零点：日回顾用户消息的「工作」为 `[]`，事件 `digests.work` 为 null，`fallback` 不含 `work`；桩给 `work` 一段时这次尝试报 `digests.work must be null: the input has no work records`（工作类不启用） |
| `test_registration_commits_evidence_and_event_in_one_version` | 一次登记：世界版本加一、其余字段逐值不变；证据与登记事件序号连续、前后版本相同；信封、`data` 键序、`caused_by` 为最后一次响应（登记） |
| `test_registering_the_same_period_twice_is_a_protocol_error` | 直接再调 `engine.register_memory_digest`：`ProtocolError("Memory digest was already generated for this period")`，库里没有新事件（登记） |
| `test_registry_rejects_a_period_registered_twice` | 手工追加第二条同期事件后 `registered_digests` 抛 `IntegrityError("Memory digest period was registered twice")`（读取） |
| `test_validation_exhaustion_records_fallback_and_the_run_continues` | 桩四次都给不合格的 food（260 字）：4 次请求、4 次响应证据，最后一次 `error` 逐字为 `digests.food.text has 260 characters counting punctuation; the day digest limit is 250; shorten it by at least 10`，事件 `fallback == ["food"]`，`settle_pending_memory_digests` 不抛出；错误日志有 `Memory digest validation retries exhausted for memory-digest:1:day:…`（重试、降级） |
| `test_retry_budget_is_reflection_validation_max_retries[0]`、`[3]` | 上限 0 时一次尝试就降级，3 时四次（重试） |
| `test_transport_failure_commits_evidence_before_raising` | 桩抛 `ConnectionError`：请求与失败证据已在库里，`memory_digest_generated` 没有写，异常原样抛出（传输失败） |
| `test_month_digest_reads_day_digests_and_count_lines` | 10-31 20:00 → 11-01 08:00 的运行：11-01 零点先登记 10-31 的日回顾，再登记 2026-10 的月回顾；月回顾的用户消息含 d 编号行（含一条「未整理」行）与次数统计行；事件序号上 10-31 日回顾 < 2026-10 月回顾 < 11-01 的 `day_plan_generated`（输入、零点次序） |
| `test_year_digest_reads_month_digests_and_year_counts` | 12-31 20:00 → 2027-01-01 08:00 的运行：01-01 零点依次登记 12-31 日回顾、2026-12 月回顾、2026 年回顾，序号递增，且 2026 年回顾 < 2027-01-01 的 `day_plan_generated`；年回顾消息含 m 编号行和全年次数统计（输入、零点次序） |
| `test_month_and_year_digests_are_not_regenerated_on_resume_or_branch` | 12-31 20:00 → 2027-01-01 08:00 的运行，过零点后取检查点 A：从 A 普通恢复、从 A 开反事实分支，两者的 `settle_pending_memory_digests` 都返回 `[]`，各自库里 2026-12 月回顾和 2026 年回顾各恰一条；从 12-31 22:00 的检查点开分支跑过零点，分支库里 12-31 日回顾、2026-12 月回顾、2026 年回顾各恰一条（恢复、分支） |
| `test_count_lines_count_food_receipts_cooked_kits_and_ended_activities` | 区间内两次收到同一饮料、一次收到食材包并做饭、一项失败的活动、一项人物收尾（`cancelled`）的生活活动、两项同短名的工作活动：饮料 `… 2 次`、食材包 `… 1 次`（只按做饭计）、失败活动不计、收尾的活动计入、同短名合并；推进到运行终点后直接调 `count_lines`，被运行截止截断（`truncated`）的活动不计；次数相同时按首次出现排序（次数统计） |
| `test_run_end_generates_no_digest` | 终点恰在零点、终点在白天两种运行：最后一天没有日回顾事件，客户端没有被构造（运行终点） |
| `test_resume_fills_only_missing_recent_days` | 连续五个工作日推进时零点只跑零点计划与节律、不跑回顾，再调 `settle_pending_memory_digests`：只为 T−1…T−3 生成，T−4、T−5 不生成，按日期先后；另一例让 T−1…T−3 没有工作类条目（夹具日历设为休息日）：最近三个有工作类条目的更早日子也生成（候选期次） |
| `test_checkpoint_after_registration_builds_no_entries` | 登记之后连续两次 `LifeEnv.checkpoint`：`worldmock.simulation_engine.observation.memory.days.past_day_entries` 的调用次数为 0（同上，monkeypatch 模块属性计数），没有新事件（缓存） |
| `test_branch_inherits_digests_and_regenerates_after_its_checkpoint` | 父运行过了零点后取检查点 A，再过一个零点：从 A 开分支，分支里 A 之前的回顾不重复，A 之后的那个零点生成一次（恢复、分支） |
| `test_midnight_crash_resume_generates_once` | 零点回顾调用传输失败使运行故障；从零点前的检查点恢复后跑过零点：分支库里这一期恰有一条回顾事件（恢复、分支） |
| `test_digest_ops_records_and_transport_waits` | 每次调用一条 `MEMORY_DIGEST_CALL`，文字、级别、`call_ms`；有失败发送时另有一条传输等待记录（运维日志） |
| `test_digest_does_not_change_the_world` | 登记前后的世界 JSON 除 `version` 外逐字节相同（回顾不是事实） |

**tests/integration/runtime/test_env_lifecycle_coverage.py**（在现有文件里新增；`_inject_memory_digest_client` 与 `_inject_plan_day_client` 并列，二者共用一个调用次序列表）

| 测试 | 断言 |
| --- | --- |
| `test_midnight_step_runs_digests_then_plans_then_rhythm` | 经 `LifeEnv.step` 过零点：回顾桩的调用早于计划桩；事件序号上 `memory_digest_generated` < `day_plan_generated` < 零点节律那次提交（零点次序） |
| `test_checkpoint_runs_digests_before_day_plans` | 把 `custom.envs.life_env` 的模块属性 `settle_pending_memory_digests` 与 `settle_pending_day_plans` 换成先记下调用次序、再转调原函数的包装：`LifeEnv.checkpoint` 先调前者、后调后者，各一次（零点次序） |
| `test_resume_init_runs_digests_before_day_plans_and_records_failures` | 父运行用引擎直接推进过零点、只跑零点计划与节律、不跑回顾，再取检查点；从这个检查点普通恢复：用上一行的同一种包装，`init` 先调回顾、后调计划；回顾桩抛出时库里有 `run_failed`，异常原样抛出（零点次序） |

**tests/unit/footprints/test_quality.py**（现有文件新增）

| 测试 | 断言 |
| --- | --- |
| `test_memory_digest_requests_form_their_own_call_class` | purpose 为 `memory_digest` 的请求归 `memory_digest`，无论工具名；`CALL_CLASSES[-1] == "memory_digest"`；报告 `schema_version == 10`（质量报告） |

**tests/integration/runtime/test_model_exchange_attempts.py**（现有文件新增）

| 测试 | 断言 |
| --- | --- |
| `test_memory_digest_evidence_carries_attempts[response]`、`[validation-retry]` | 经真实 `MemoryDigestClient` 与真实分发器：每次调用的失败发送进入这次调用自己的响应证据（证据） |

### 需要修改的现有测试

| 文件 | 修改 |
| --- | --- |
| tests/life/conftest.py | 新增 `memory_digest_response(arguments: dict \| str, finish_reason: str = "tool_calls", model: str = "test-model") -> dict`；新增 `StubMemoryDigestClient(model_identity=None, responses=None, raises=None)`：记录请求，按脚本返回；饮食类用到的商品名取 tests/fixtures/dataset-environment 目录全部商品的 `name`（不按长度过滤）。脚本用尽后，桩把请求的用户消息按 JSON 解析（有「人物卡片」键为日回顾，否则为月、年回顾），对每个有输入（这一类的行列表非空）的类写一段：日回顾的饮食类，取这些商品名中作子串出现在饮食各行「核验」字符串里的名称，按在这些字符串（按行序接起来）里首次出现的位置排、去重，写成 `"收到和用到了" + "、".join(名称) + "。"`，没有这样的名称时写「这一天吃喝照常。」；日回顾的工作类、生活类写「这一天这一类的事照常。」；月、年回顾写「这段时间的回顾已经整理。」。refs 为这一类全部编号，无输入的类为 null；这样写出的段落非空、refs 都是这一类的编号，在夹具的条目量下不超过字数上限，能通过全部守卫。`raises` 非空时下一次调用抛出。`settle_day_plans`（:202-209）改名为 `settle_midnight_tasks(engine, plan_client=None, digest_client=None)`：先跑回顾（缺省用桩），再跑零点计划，再在零点待判时判定节律；`advance_next`（:241-247）改调它；tests/integration/life/test_day_plan_arrangement.py:38 与 tests/integration/life/test_planning_and_errand_evidence.py:215 两处调用随之改名 |
| tests/integration/runtime/native_runtime_fixtures.py:130、tests/integration/runtime/test_public_runtime_boundaries.py:204 | 同时把 `runtime.MemoryDigestClient` 换成 `StubMemoryDigestClient` |
| tests/integration/runtime/test_public_runtime_boundaries.py:149 | 加一句 `MemoryDigestClient(identity).call(request)` 的同样断言 |
| tests/unit/engineering/test_shared_model_clients.py:20 | `CLIENTS` 加 `(MemoryDigestClient, 'Memory digest')` |
| tests/unit/engineering/test_import_graph.py:71-79、:105 | 零点计划的各键（`_call_plan_day`、`_log_plan_day_call`、`_generate_day_plan`、`settle_pending_day_plans`）不变；新增 `_call_memory_digest` 对 `agentsociety2.config.llm_dispatcher` 与 `worldmock.simulation_engine.evidence.memory_digest`、`_log_memory_digest_call` 对 `worldmock.recording_engine.storage.opslog`、`settle_pending_memory_digests` 与 `_generate_memory_digest` 对 `worldmock.simulation_engine.evidence.memory_digest` 的延迟导入，理由文字与同表零点计划各键相同；`count_lines` 不登记（它的导入都在 days.py 模块级，见 days.py 一节） |
| tests/integration/runtime/test_network_ops_records.py:12-13、:52-57 | 零点计划一例不变（照旧从 day_planning 导入草稿、调用 `_call_plan_day`、断言 `PLAN_DAY_CALL`）；:12-13 加 `memory_digest` 的 `request_draft`、`response_draft` 与 `_call_memory_digest` 的导入；新增同形的回顾参数化一例：用 memory_digest 的草稿、调用 `_call_memory_digest`，断言 `MEMORY_DIGEST_CALL` 的 outcome、级别、`call_ms` 与传输等待记录 |
| tests/dataset_acceptance.py:1214-1223 | 零点计划的两个变异 `plan_day_correction_attempts_dropped`、`plan_day_response_attempts_dropped` 不变。其后新增变异 `memory_digest_response_attempts_dropped`：符号 `worldmock.runtime.lifecycle.runner::_generate_memory_digest`，原文 `evidence.append(memory_digest.response_draft(now, agent_id, (*link, attempts), raw, error))`，替换文 `evidence.append(memory_digest.response_draft(now, agent_id, (*link, ()), raw, error))`，杀死测试 `tests/integration/runtime/test_model_exchange_attempts.py::test_memory_digest_evidence_carries_attempts[response]` |
| tests/unit/footprints/test_quality.py:601、:632、:1047-1071 | `schema_version` 断言改为 10；`_class_request` 加 `memory_digest` 一支（purpose 为 `memory_digest`，工具 `submit_memory_digest`），逐类用例随 `CALL_CLASSES` 多一行 |
| tests/integration/footprints/test_export.py:47、test_acs_export.py:118、test_query_export.py:191、test_phone_record_export.py:175、tests/integration/runtime/test_output_truncation.py:388 | 质量结构号断言改为 10 |
| tests/integration/life/test_committed_world_guard.py:118-122 | 引擎公开方法表加 `register_memory_digest` |
| tests/system/lifecycle/test_run_manifest.py:11、tests/unit/runtime/test_model_identity.py:39、tests/integration/runtime/test_strict_endpoint.py:16 | 不改，照旧通过（字段名 `reflection_validation_max_retries` 与断言不变；文件里没有「反思」字样） |

所有跨过零点的系统与集成用例经 `advance_next` 或 `LifeEnv.step` 跑回顾任务，因此回顾的生成路径在每一个多日用例里都被执行；任何一级没有实现时，上面的输入、次序和登记用例失败。覆盖门禁（分支覆盖率 ≥85%，公开接口零未覆盖）同样适用于本章新增的全部公开函数。

## 不在本章

- 今天条目的形状、全文、核验短句、短名、按类切分、来源事件身份、零点收尾条目与发布（第 02 章）；
- 类别推导、归日函数、零点界、「近三天」的日期取法与渲染，包括「未整理」那一格的写法（第 03 章）；食品状态与食材包状态（第 08 章）；
- `search_memory` 读回顾和「未整理」时退回原始条目（第 05 章）；
- 系统层规则里怎样向人物说明回顾（第 07 章）；
- 「活动 → 所用食材包」映射与食材包句、阈值巩固与睡醒反思的退役、`consolidation` 与 `reflection` 两个调用类的去留、MEMORY_WRITE_POINTS（第 08 章）；
- `memory_pilot.py` 的 M10、M11（按本章的问题文字统计各守卫的丢弃次数）、跨月短试点、`compare_runs.py` 的 `data.digests.*.source_event_ids[]`、`data.digests.*.cited_event_ids[]` 身份路径（第 09 章）；
- 版本与指纹汇总、两候选重冻、查看器重录、风格门禁台账与 LabelKit 通知（第 10 章）。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 「恢复时还包括此前缺回顾、且仍在近三天里的日子」怎样算 | 候选日是日历上的 T−1…T−3 与最近三个工作类有输入的日子的并集；未登记的日子按条目判定有无输入，已登记的按事件 | 第 03 章近三天的取法；裁定 10；正常运行里检查点总在零点任务之后，所以只有 T−1 会待生成 |
| 月、年回顾的候选 | 每次调用只看上一个日历月和上一个日历年，检查点和恢复同样 | 提案「每月第一个零点为上个月生成」；幂等由事件保证 |
| 编号在类间是否重新开始 | 三类连续编号，先饮食、再工作、后生活 | 提案要求核对「refs 确实属于这一类」，只有编号跨类唯一时这项核对才有意义 |
| 有输入的类给 null | 不合格，要求重写 | 提案只在「没有记录时写 null」；有输入而为 null 会被读成「没有这类记录」，与 fallback 的含义冲突 |
| 重试时已通过的类 | 每类取最早一次通过守卫的那一段，后续尝试只对仍未通过的类报错；用尽时只有从未通过的类进 fallback | 裁定 7「三类照样隔离」；提案「这些类记为 null」 |
| 月、年回顾要不要人物卡片 | 不给 | 提案只在日回顾的输入里列人物卡片 |
| 全是「未整理」行的类 | 算有输入，照样要求一段 | 提案把「未整理」行列为输入；「没有输入的类写 null」按字面 |
| 用户消息的序列化 | 紧凑 JSON、不排序，键按本章次序 | `sort_keys` 会把「编号」排到行尾；提案示例里编号在前 |
| 日回顾要不要日期键，月、年回顾要不要期次键 | 都不给 | 提案的输入清单不含它们 |
| 次数统计里的食材包 | 按做饭使用计，收货不另计；其余饮食商品按收货计（只算 meal、snack、drink） | 提案「每样收到的商品和每份用过的食材包」；避免同名两行 |
| 工作、生活类计哪些活动 | 区间内结束、终态是 `completed` 或 `cancelled`、判给这一类的活动；`failed` 与运行截止的 `truncated` 不计 | 提案「收尾或自然结束的活动」；`truncated` 只在运行截止时出现（activities.py:138-142，docs/design/04-simulation-engine.md:507），既不是收尾也不是自然结束 |
| 次数统计行的写法 | `f"{名称} {次数} 次"`，两个半角空格 | 提案「〈商品名〉 N 次」的字面 |
| 行为空的类的次数统计 | 写空列表 | 避免模型在必须写 null 的类里看到统计 |
| 证据草稿与调用记录的归属 | 回顾的三个证据草稿在 memory_digest.py，runner 新增 `_call_memory_digest`、`_log_memory_digest_call`，零点计划一侧不改；运维码 MEMORY_DIGEST_CALL | 提案第三章代码表把回顾的证据草稿列在 memory_digest.py，代码表没有 day_planning.py |
| 容量预检 | 第一次请求不得超过窗口的 85%，超过即运行故障，不裁剪 | 现有每次模型调用都做容量预检；回顾的输入按规则不截断 |
| 回顾事件的读取 | 只有 `observation/memory/digests.py` 的 `registered_digests` 一处，同期两条即完整性错误 | 第 03、05 章和本章都读回顾；「按 (store, agent) 增量缓存」 |
| 「不需要回顾」怎样记 | 只在进程内缓存，不写事件 | 提案「记一次『不需要回顾』」且不新增 AgentState 字段、世界保持 15 |
| 事件闭集校验的范围 | 只核对形状（键、取值、长度、身份列表、fallback 与 null 的对应）：正文非空与正文长度随形状一并核对，依据非空对应 `cited_event_ids` 非空；不按这一期的输入行重跑「依据归属」 | 提案「闭集字段分支（tier、period 的取值和对应关系）」；「依据归属」依赖这一期的输入行，读取时没有 |
| 十六进制身份串检查 | requests.py 公开 `contains_hex_identity`，只由 `validate_memory_text` 使用；回顾不查十六进制串 | 第四批裁定只留格式与长度检查；函数名与位置照旧，不为此改名 |
| 只供已撤销守卫用的数据 | `DigestRow.text`、`DigestRow.verified` 与 `DigestInputs.counts` 随 `names`、`goods_names`、`kit_names`、`food_required`、`kits_cooked` 一并删除；次数统计行只留在 `payload` 里 | 第四批裁定撤销九道守卫，连同只为它们存在的概念与数据：`text` 只供名称守卫查找，`verified` 只供饮食核验名称与必写名称，`counts` 只供数字与次数照抄守卫；剩下的四道守卫只读正文、`refs` 与各行编号 |
| 工具与参数对象的说明 | 工具 description 用英文；参数对象不带 description | 零点计划工具的写法；`ToolRequestModel` 的缺省 |
| 正文是否修剪 | 原样保存，非空按 `strip()` 判定 | 提案不要求改写模型输出 |
| 用尽时的日志 | 写 Python 错误日志，不新增运维码 | 异常分支必须有错误日志（AGENTS.md）；运维码只记调用 |
| 恢复辅助函数的名称 | `_settle_day_plans_on_resume` 改名为 `_settle_midnight_tasks_on_resume` | 它现在依次运行两个零点任务 |
| 测试辅助函数的名称 | `settle_day_plans` 改名为 `settle_midnight_tasks` | 它现在依次运行回顾、计划与节律 |
| 工作类不启用时的回顾 | 三个类别键照常出现，工作类为空列表或空行，模型必须给 null，`work` 不进 `fallback` | 第 03 章第 8 节「04 不为它生成回顾」；工具 Schema 固定要求三个键 |
| 次数统计放在哪里、怎样取类别与食材包 | `days.count_lines`；categories.py 与 entries.py 都在 days.py 模块级导入；活动类别按第 02 章【E11】的快照与开始结果读法；「活动 → 所用食材包」取 `entries.activity_facts(cached).kits`（10 章跨章裁定：此前写的函数内导入与 `activity_category(book, context.state, activity)` 不合第 03 章的导入边界与签名） | 提案第三章代码表「days.py……P3 加次数统计」；第 03 章只禁止 days.py 导入 progress_layer.py、memory_layer.py、day_plan_inputs.py；`activity_category(book, activity, goods_ids)` 不读世界状态 |
| 「已登记」检查放在哪里 | 只在 `registration_draft` 做一次并写日志；证据为空的检查在 `TimeAdvanceCoordinator.register_memory_digest` | 每条规则只在一处；`causes` 由协调器从证据取得 |
| `period` 的格式检查 | `date.fromisoformat` 往返相等；月、年用 `re.fullmatch` 与 ASCII 数字类 | `re.match`/`re.search` 的 `$` 接受末尾换行，`\d` 接受非 ASCII 数字 |
| 缺省测试桩写什么 | 饮食类只列饮食核验里出现的商品名，其余类与月、年回顾写不含名称和数字的固定句 | 把核验行原样接起来会超过日回顾的字数上限，使用缺省桩的多日用例的结果取决于夹具当天的字数 |
| 与第 08 章重叠的设计文档改写 | 06-agent.md :35、:311，05-recording-engine.md :100，10-runtime.md :22，01-architecture.md :63、:73、:172，docs/manual/personas.md :105 都照第 08 章的文字，本章不另写；00-overview.md :138 保留本章的改写句，取代第 08 章「删去」的操作 | 同一行只有一个终稿；提案第三章「设计文档」01-architecture 一行是原位改链；00-overview.md 「引用完整性」需要陈述回顾的 `refs` 规则 |
| `_require_midnight_decided` 的报错文字（time_advance.py:227-231） | 不改 | 节律仍在零点计划登记之后判定，`Midnight rhythm must be decided after the day plans are registered` 仍然成立 |
