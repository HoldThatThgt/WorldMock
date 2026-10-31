# 05 search_memory 与工具面

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。第四批裁定（2026-10-30）修订。第五批裁定（2026-10-31）修订。

## 目标与依据

本章实现冻结提案 docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md 的下列部分，按终态一次交付：

- 第二章「search_memory」一节全部条目（提案 :508-564），包括参数模型、时间与关键词语义表、匹配规则、「未整理」退路、6,400 字上限、`未列出` 与 `提示`、结果形状、三个错误码、每次决策 3 次的上限、只读保证、三个 `memory_*` 工具的删除和只读问答路径。
- 第二章「系统层规则原文」一节里关于工具说明的一句：「`TOOL_DESCRIPTIONS` 删去三条 `memory_*`，新增 `search_memory`」（:598）中工具集合的一侧；用途句的键、次序与原文由第 07 章「原生工具用途句」定义。
- 第三章「代码」表中 state/interaction/requests.py、custom/agents/life_agent.py（工具表、检索上限、错误码、`_QUERY_TOOL_MODELS`、`life_action_tool_names`、`dispatch_react_tool`、只读路径、删除 `_dispatch_strict_memory`）、custom/envs/life_env.py（放宽 read_query 白名单、核对查询工具面）、simulation_engine/engine.py（`search_memory`）、runtime/framework/read_query.py（`read_query` 转发不变）、新包 `observation/memory/search.py`（P1 部分与 P3 的按月、按年）各行。
- 第四章 P1「`search_memory` 上线……工具面从 10 个变成 8 个，加检索上限和错误码」与 P3「按月、按年查询给月回顾、年回顾」。两期在本次交付中一起实现，期次只决定实施顺序（见本章「行为」末节）。
- 第五章裁定：第 3 条（回合预算保持 16，每次决策最多检索 3 次）、第 15 条（只读问答路径只留 finish）、第 21 条的检索一侧（查询答案以「当时查到」出现在「今天」检索里）、第 22 条（第三个参数 `keyword`，category 和 time 各加「全部」）、第 23 条的检索一侧（按日期查「未整理」的那一格退回原始条目；区间、月、年里「未整理」的日子只进 `未列出`）、第 28 条（「今天 HH:MM」「今天 HH:MM~HH:MM」）。第一批裁定「`search_memory` 是唯一的记忆工具，`recall_task_summary` 保留」。

本章引用而不重述的事实：

| 事实 | 所属章 |
| --- | --- |
| 「可查」与它的「最早日期」（`earliest_date`，observation/memory/layout.py） | 01 记忆层：形状、预算、兜底与层序 |
| 今天条目：形状、已发布条目（C_j 与截止序号，`published_today`）、条目全文与按类别切分（`entry_full`，核验完整行、「当时查到」、全部自述含 routine，切法与日回顾输入相同）、零点收尾条目、某一往日全部条目（`past_day_entries`） | 02 今天条目 |
| `MEMORY_CATEGORIES = ("饮食", "工作", "生活")` 与归日规则 | 03 类别、归日与近三天 |
| `memory_digest_generated` 事件正文、`MEMORY_DIGEST_KEYS`、回顾的读取与按 (store, agent) 的增量缓存（`registered_digests`）、`register_memory_digest`、测试替身 `StubMemoryDigestClient` | 04 日、月、年回顾 |
| 同秒续接的 `changes` 与 `_earlier_changes` | 06 进度层、待办层与同秒续接 |
| 系统层 rules[1]、rules[2] 里教模型用 search_memory 的原文；只读系统层规则 | 07 人设层与系统层规则 |
| `due_reflection` 与反思的删除 | 08 退役项、食材包句与环境包 |
| 查看器和 tests/fixtures/viewer-context.json 的重录、试点指标 M5 | 09 分析工具、运行比对与查看器 |
| 版本、指纹、两候选重冻、门禁 | 10 实施：版本、文件、设计文档、测试与门禁 |

## 行为

条款编号【S1】…【S12】供「测试」一节引用。

### 【S1】工具面

- 生活决策的原生工具共八个，按工具指纹的次序（`_TOOL_MODELS`）是：`start_main_activity`、`start_side_activity`、`recall_task_summary`、`report_status`、`pause_activity`、`conclude_activity`、`revise_day_plan`、`search_memory`。每次模型请求的 `tools` 数组次序是六个世界请求（`_WORLD_TOOL_MODELS` 的次序）、`recall_task_summary`、`search_memory`。
- `search_memory` 是人物唯一的记忆工具。`recall_task_summary` 不是记忆工具，留在 `REQUEST_MODELS`，语义不变。
- `memory_recent`、`memory_search`、`memory_time_range` 三个工具及其全部代码删除（清单见「接口与文件」）。
- 最终模型回合和截断后的那次重试照现行规则只开放世界请求，`search_memory` 与 `recall_task_summary` 一起撤下（life_agent.py:397-404 的条件不变）。其余回合两者都开放；达到检索上限后 `search_memory` 照样开放，工具 Schema 不因次数变化。
- 只读问答（`readonly=True`）每个回合只开放 `finish`。只读路径上没有 `search_memory`，也没有回看任务。
- 工具指纹 `life_tool_schema_sha256()` 的算法不变（life_agent.py:953-959），覆盖上面八个工具。

### 【S2】参数模型与 Schema

- 参数模型 `SearchMemoryRequest` 放在 src/worldmock/state/interaction/requests.py，登记在新增的 `QUERY_MODELS = {"search_memory": SearchMemoryRequest}`，不进 `REQUEST_MODELS`，也不进 `ActionRequest` 联合类型（requests.py:558-565）。
- 三个参数都必填：
  - `category`：`"饮食"`、`"工作"`、`"生活"`、`"全部"` 之一；
  - `time`：字符串，Schema 的 `pattern` 为 `^(今天( \d{2}:\d{2}(~\d{2}:\d{2})?)?|全部|\d{4}(-\d{2}(-\d{2}(~\d{4}-\d{2}-\d{2})?)?)?)$`，与提案逐字相同；
  - `keyword`：字符串或 `null`，Schema 生成 `anyOf[string, null]`。
- `deepseek_strict_schema(SearchMemoryRequest)`（state/interaction/schema.py:56-64）不抛错。生成的 Schema 经 `json.dumps(schema, ensure_ascii=False, separators=(",", ":"))`（键按生成次序，不排序）序列化后逐字节等于：

```json
{"additionalProperties":false,"properties":{"category":{"enum":["饮食","工作","生活","全部"],"title":"Category","type":"string"},"time":{"pattern":"^(今天( \\d{2}:\\d{2}(~\\d{2}:\\d{2})?)?|全部|\\d{4}(-\\d{2}(-\\d{2}(~\\d{4}-\\d{2}-\\d{2})?)?)?)$","title":"Time","type":"string"},"keyword":{"anyOf":[{"type":"string"},{"type":"null"}],"title":"Keyword"}},"required":["category","time","keyword"],"title":"SearchMemoryRequest","type":"object"}
```

- 长度和词数上限不进 Schema（构建器拒绝 `maxLength` 等键，schema.py:45-52、:76-78），由 `keyword` 的 `field_validator` 执行，按下表先后检查，第一条不成立的抛出 `ValueError`，经现行参数纠错（`argument_feedback`，`ARGUMENTS_INVALID`）回给模型，路径为 `["tool_calls", <调用位置>, "function", "arguments", "keyword"]`：

  | 条件（`keyword` 不为 `null` 时） | 英文日志（`_LOGGER.error`） | `ValueError` 文字 |
  | --- | --- | --- |
  | `keyword.split()` 为空（空串或只有空白） | `Memory search keyword is blank` | `memory search keyword must contain a word or be null` |
  | `len(keyword.strip()) > 40` | `Memory search keyword exceeds its character limit` | `memory search keyword exceeds 40 characters` |
  | `len(keyword.split()) > 4` | `Memory search keyword has too many words` | `memory search keyword has more than four words` |

  「字」按 `len()` 计。`str.split()` 不带参数，按 Unicode 空白拆分（含全角空格 U+3000）。校验器原样返回 `keyword`，不改写它。
- `time` 只由 Schema 的 `pattern` 校验形状；日历是否成立、是否晚于此刻、区间方向和长度由环境判定（【S8】）。`pattern` 里的 `\d` 也接受非 ASCII 数字，这类写法通过 Schema，由环境以 `MEMORY_TIME_INVALID` 拒绝。

### 【S3】派发、检索上限与只读通道

- LifeAgent 的 `dispatch_react_tool` 对 `search_memory` 只做一件事：交给 `_search_memory(args)`。它按下面的次序处理：
  1. 本次决策已派发的检索次数 `_life_memory_searches` 达到 `MEMORY_SEARCHES_PER_DECISION = 3` 时，不调用环境，写英文错误日志 `Memory search limit reached for this decision`，令 `data = query_rejection(refusal_for_code("MEMORY_SEARCH_LIMIT").error())`（【S10】），返回 `ReactToolResult(False, json.dumps(data, ensure_ascii=False), data)`；
  2. 否则 `self._env` 为 None 时抛 `RuntimeError("Environment is not initialized")`（与 recall 分支 life_agent.py:609-610 相同，计数不变）；计数加一，用 `_validate_request(QUERY_MODELS["search_memory"], args)` 得到请求，调用 `data = await self._env.read_query(self.require_decision_context(), "search_memory", request)`；
  3. 返回 `ReactToolResult(ok, json.dumps(data, ensure_ascii=False), data)`，其中 `ok` 为 `"error_code" not in data`。`ok` 为假时（含第 1 步），现行 `_append_react_tool_result`（life_agent.py:647-663）把每条 `items[].path` 的前两段改写成 `["tool_calls", <实际调用位置>]`。
- 计数的含义：每次决策从 0 开始（在 `prepare_react_loop` 里与 `_life_action_count` 一起清零，生活决策和只读问答都清零）；凡是通过宿主参数校验、到达 `dispatch_react_tool` 的 `search_memory` 调用各算一次，环境以 `MEMORY_TIME_INVALID` 或 `MEMORY_KEYWORD_REQUIRED` 拒绝的也算；参数校验失败（`ARGUMENTS_INVALID`）的调用没有派发，不算。同一次回复里的多个检索按回复中的先后依次派发，第 4 个及以后的各得一条 `MEMORY_SEARCH_LIMIT`。这个计数是协议护栏，不是回合数的上界：回合仍只受冻结的 `max_react_turns` 约束。
- 同秒续接链里前一决策的检索结果只是消息历史，不计入本次决策。
- `recall_task_summary` 的派发分支不变（life_agent.py:608-613，`ok` 恒为真）。
- `search_memory` 不是世界请求：不在 `_WORLD_TOOL_MODELS`，不登记动作，不结束决策；`validate_react_decisions` 的批次规则不变（只读查询可以同批、个数不限，不能与世界请求同批）。把它当世界请求经 `submit_action` 送达时，`DecisionCoordinator._validate_call` 现行的 `ProtocolError("Direct tool call request type does not match its tool")`（decision_coordinator.py:285-287）原样成立；派发器拦截（dispatcher.py:455-458）不改。

### 【S4】环境侧 read_query

- `LifeEnv.read_query` 的白名单为 `_READ_QUERY_MODELS = {"recall_task_summary": REQUEST_MODELS["recall_task_summary"], **QUERY_MODELS}`。工具名不在白名单里，或请求对象的类型不是白名单为该工具名登记的类型，写错误日志 `Read query tool and request type do not match: <tool>` 并抛 `ProtocolError("Read query tool and request type do not match")`（文字与现行相同）。
- 依次做现行的上下文校验、决策轮校验，再做上一条的白名单核对，再做唯一冻结观察校验（life_env.py:158-168，次序与文字都不变），然后：
  - `recall_task_summary` → `engine.read_task_summary(context.agent_id, request, rows[0].data)`，与现行相同；
  - `search_memory` → `engine.search_memory(context.agent_id, request, rows[0])`，传入的是冻结观察事件本身。
- 两者抛出的 `BusinessRuleError` 都经 `query_rejection(error)` 写成五字段结果返回（【S10】），其余异常照常传播。
- `_validate_registered_tool_surface` 在现有两项核对之后加第三项：`life_query_tool_names() != frozenset(_READ_QUERY_MODELS)` 时抛 `ProtocolError("LifeEnv read query surface disagrees with the LifeAgent query tool set")`。`life_action_tool_names()` 仍等于 `frozenset(REQUEST_MODELS)`，现有两项核对（life_env.py:127-134）不改。
- runtime/framework/read_query.py 的 `LifeEnvActor.read_query` 与 `LifeEnvProxy.read_query` 转发不变，参数仍是 `(context, tool, request)`；只改说明文字（「接口与文件」）。

### 【S5】只读保证

- 检索的回答只依赖：冻结观察事件 `observation`（它的 `event_seq` 是截止序号，它的 `occurred_at` 是「此刻」）、序号不超过截止序号的事件、冻结的环境输入（目录、人设、配置）。它不读 `EngineContext.state`。
- 检索不写任何事件，不改世界状态，不推进时间，不写人物记忆；一次检索前后 `store.last_event_seq` 与 `state.model_dump_json()` 都不变。一次决策里，检索与下一次模型请求之间除了 `model_request`、`model_response` 没有别的事件。
- 回答是确定性的：同一份事件、同一个截止序号和同一个请求，结果逐字节相同；重新打开运行（`LifeEngine.open`）后再算也相同。
- 结果里不出现任何内部身份：没有事件身份、决策身份、`source_event_ids`、`cited_event_ids`、活动身份，检索也不加入任何十六进制串；条目只用 02 的全文形状，回顾只给正文。回顾正文是模型写的原文，第四批裁定之后生成时不查其中有没有十六进制串（04「输出校验」），正文里写了的会原样出现在结果里。
- 结果的顶层没有 `result` 键，所以 `_earlier_changes`（life_continuation.py:102-108）把检索的 tool 消息认作非续接消息。

### 【S6】时间的解析

记今天为 `today = local_date(observation.occurred_at, tz)`，此刻为 `now = clock_phrase(observation.occurred_at, observation.occurred_at, tz)`（HH:MM），昨天为 today 的前一天，`tz = context.inputs.config.timezone`。`parse_search_time(time, today, now)` 只用 ASCII 数字的完整匹配解析，结果是一个 `SearchPeriod`：

| `time` 写法 | 解析结果 | 成立条件（不成立时 `MEMORY_TIME_INVALID`，原因见【S10】） |
| --- | --- | --- |
| `今天` | `today`，时刻 `00:00` 到 `now` | 恒成立 |
| `今天 HH:MM` | `today`，时刻 `HH:MM` 到 `HH:MM` | `00≤HH≤23`、`00≤MM≤59`；`HH:MM ≤ now` |
| `今天 A~B` | `today`，时刻 `A` 到 `min(B, now)` | A、B 都合法；`A ≤ now`；`A ≤ B` |
| `全部` | `all` | 恒成立（是否带关键词见【S7】） |
| `YYYY-MM-DD`，等于 today | 与 `今天` 相同 | 日期成立 |
| `YYYY-MM-DD`，早于 today | `day`，那一天 | 日期成立 |
| `YYYY-MM-DD~YYYY-MM-DD` | `range`，首日到末日（两端都含） | 两个日期都成立；首日 ≤ today；首日 ≤ 末日；`(末日 − 首日).days + 1 ≤ 31` |
| `YYYY-MM` | `month`，该月 1 日到月末 | `01≤MM≤12`，年份 1–9999；该月 1 日 ≤ today |
| `YYYY` | `year`，1 月 1 日到 12 月 31 日 | 年份 1–9999；1 月 1 日 ≤ today |

- 检查次序：先看写法是否全由 ASCII 数字组成并且日历或钟点成立，再看是否晚于今天或此刻，再看区间方向，最后看区间长度；报第一条不成立的。
- 日期晚于 today 的单日写法不成立（「起点晚于今天」）。区间、月、年的末日可以晚于今天，照常成立。
- 时刻按两位补零的 `HH:MM` 字符串比较。
- 区间、月、年包含今天时，往日部分只到昨天：今天既不算「未整理」，也不计入 `未列出`，今天的条目不在这三种查询的结果里。首日等于 today 的区间没有往日部分，结果为空；只有单日写法等于 today 时才等同于「今天」。

### 【S7】结果的组成

记 `words = keyword.split()`（`keyword` 为 `null` 时为空），类别范围 `cats` 在 `category` 为「全部」时是 `MEMORY_CATEGORIES`，否则是这一类。结果由若干「单元」按【S9】的次序装填。单元有三种：整项（一条今天条目、一段回顾）、内联（按日期查「未整理」那一类的原始条目，按条装填）、标签（区间、月、年、全部里「未整理」的日子，只进 `未列出`）。

**关键词匹配。** 一个单元匹配，当且仅当 `words` 的每个词经 `casefold()` 后都是这个单元文字经 `casefold()` 后的子串。单元文字：回顾是它的正文；今天条目或原始条目是它（切分后）的「核验」「当时查到」「自述」各字符串用 `"\n"` 连起来的文字。键名、「时刻」「类别」「日期」不参与匹配。指定类别时先切分、后匹配。标签单元不做匹配。

**今天条目。** 取 02 的当天已发布条目（截止序号为 `observation.event_seq`，本次决策自己的条目尚未发布，不在其中），按 `SearchPeriod` 的时刻段筛「时刻」在段内的条目（`all` 取全部）。`category` 为「全部」时用 02 的完整全文；指定类别时只取「类别」串里含这一类的条目，并用 02 的按类别切分只留这一类的行和项，切分后的条目「类别」写这一类。条目的形状就是 02 的全文形状，「当时查到」在其中，带查询时刻。今天条目一律是整项单元，标签 `今天 HH:MM`。

**回顾。** 回顾取 04 的回顾事件中序号不超过截止序号的那些，每个事件给出一期 `(tier, period)`：某类有正文就是这一类这一期的回顾；某类没有正文而在 `fallback` 里，就是这一类这一期「未整理」；两者都不是，就是这一期这一类没有记录。没有事件的期次，等同于那一期没有任何回顾。

**各写法给什么。**

| `time` | 不带关键词 | 带关键词 |
| --- | --- | --- |
| 今天、`今天 HH:MM`、`今天 A~B`（含等于今天的日期） | 时刻段内的今天条目 | 其中匹配的今天条目 |
| 早于今天的 `YYYY-MM-DD` | 对 `cats` 的每一类：有日回顾 → 整项 `{"日期","类别","回顾"}`；「未整理」→ 内联单元，那天这一类的全部原始条目（02 给出的那天全部条目，含零点收尾条目，按这一类切分）；没有记录 → 无 | 匹配的日回顾整项；「未整理」的类 → 内联单元，只含匹配的原始条目，一条都不匹配就没有这个单元 |
| 日期~日期 | 区间往日部分里每天、每类的「日单元」 | 区间往日部分里匹配的日回顾整项；「未整理」的日子 → 标签单元 |
| `YYYY-MM` | 该月的「月单元」 | 该月往日部分里匹配的日回顾整项和匹配的月回顾整项；「未整理」的日子 → 标签单元 |
| `YYYY` | 该年的「年单元」 | 该年往日部分里匹配的日回顾、月回顾、年回顾整项；「未整理」的日子 → 标签单元 |
| 全部 | `MEMORY_KEYWORD_REQUIRED` | 全部今天条目里匹配的，加上全部日、月、年回顾里匹配的整项；「未整理」的日子 → 标签单元 |

- **日单元**（某一天某一类）：有日回顾 → 整项 `{"日期","类别","回顾"}`；「未整理」→ 标签单元；没有记录或那天没有事件 → 无。
- **月单元**（某月某一类）：该月有月回顾事件时，这一类有正文 → 整项 `{"月份","类别","回顾"}`；这一类「未整理」→ 该月往日部分里这一类的日单元；这一类没有记录 → 无。该月没有月回顾事件时（当月，或这一期没有压过）→ 该月往日部分里这一类的日单元。
- **年单元**（某年某一类）：该年有年回顾事件时，这一类有正文 → 整项 `{"年份","类别","回顾"}`；这一类「未整理」→ 该年各月这一类的月单元；没有记录 → 无。该年没有年回顾事件时 → 该年各月这一类的月单元。
- 月回顾或年回顾「未整理」只换成下一级，自身不产生标签。
- 某一类整类没有任何记录时（例如没有工作安排的人物的工作类），结果里自然没有它，不另作处理。
- **早于最早日期。** 解析为 `day`、`range`、`month`、`year` 且这一期的末日早于 01 的「最早日期」时，不再取单元，直接返回空结果，`未列出` 为空，顶层另加 `最早日期`（【S11】）。末日不早于最早日期的，照常按上表取，最早日期之前的日子自然没有内容。

### 【S8】错误

三个错误码都是业务拒绝，由环境（前两个）或宿主（第三个）写成 read_query 的五字段形状 `{error_code, reason, fix, items, unmet}`（【S10】），`unmet` 恒为空列表：

| 码 | 何时 | 谁判定 |
| --- | --- | --- |
| `MEMORY_TIME_INVALID` | 【S6】表中任一条件不成立：非法日期或时刻（含非 ASCII 数字）、起点晚于今天、起点晚于此刻、反向区间、超过 31 天的区间 | `search.py`（环境） |
| `MEMORY_KEYWORD_REQUIRED` | `time` 为「全部」而 `keyword` 为 `null` | `search.py`（环境），在时间解析之后、取单元之前 |
| `MEMORY_SEARCH_LIMIT` | 本次决策已派发三次检索，这是第 4 次或更晚 | LifeAgent，不送环境 |

### 【S9】装填、上限与次序

- 上限 `MEMORY_SEARCH_RESULT_CHARS = 6400`：一次结果里放进去的回顾正文与条目字符串的 `len()` 之和不超过 6,400。单元大小：回顾整项是正文长度；今天条目是它（切分后）「核验」「当时查到」「自述」各字符串的长度之和；内联单元按条计，每条同今天条目。键名、引号、「时刻」「类别」「日期」「月份」「年份」不计。项数不设上限。
- **次序（从新到旧）。** 今天条目排在所有往日单元之前，按发布次序倒排（最晚发布的在前）。往日单元按这一期的末日（日回顾是那天，月回顾是月末，年回顾是 12 月 31 日）从新到旧；末日相同的，日回顾在前、月回顾其次、年回顾最后；再相同的按 `MEMORY_CATEGORIES` 的次序。标签单元按它那一天的日回顾排位。按日期查（`day`）时，整项单元排在内联单元之前，各自按类别次序；内联单元里的条目按时刻从晚到早（零点收尾条目「24:00」最先），时刻相同的按 02 的条目次序倒排。
- **装填（遇到第一个放不下的就停）。** 按次序逐个处理，剩余额度从 6,400 开始：
  1. 标签单元：标签进 `未列出`，记下原因「未整理」，不占额度；
  2. 停止之前的整项单元：大小不超过剩余额度就放入结果、扣减额度；否则从这里停止，它的标签进 `未列出`，记下原因「上限」；
  3. 停止之前的内联单元：其中的条目按上面的次序逐条放入，放不下的那一条起停止，那一条和这个单元余下的条目各以条目标签进 `未列出`，记下原因「按日放不下」；至少放入一条时，这个单元以放入的条目出现在结果里；
  4. 停止之后的整项单元以它的标签进 `未列出`，记下原因「上限」；内联单元的每条以条目标签进 `未列出`，记下原因「按日放不下」。
- **标签写法。** 今天条目 `今天 HH:MM`；日回顾 `YYYY-MM-DD 类别`；月回顾 `YYYY-MM 类别`；年回顾 `YYYY 类别`；标签单元 `YYYY-MM-DD 类别（未整理，按日期单独查）`；内联单元的条目 `YYYY-MM-DD HH:MM 类别`（零点收尾条目写 `24:00`）。`未列出` 按装填次序排列，相同的标签只留第一次出现的那个。
- **提示。** `未列出` 非空时加顶层键 `提示`，按原因取下列句子，按这个次序用「；」接起来，每句只写一次：
  1. 原因「上限」：`范围太大，换更短的时间、指定类别或加关键词`
  2. 原因「未整理」：`未整理的日子按日期单独查`
  3. 原因「按日放不下」：`加关键词缩小范围`

  按日期查时整项（至多每类一段日回顾）总在内联单元之前且放得下，所以按日期查的 `提示` 只会是第 3 句；第 1、2 句只出现在其余写法里。

### 【S10】错误的五字段形状与说明

- 新函数 `query_rejection(error: BusinessRuleError) -> dict` 返回 `{"error_code": str(error), "reason": guidance["reason"], "fix": guidance["fix"], "items": guidance.get("items", []), "unmet": list(getattr(error, "unmet", []) or [])}`，其中 `guidance = rejection_guidance(error)`。这与现行 `LifeEnv.read_query` 内联的写法（life_env.py:173-180）等价（现行写 `guidance.get("fix")`；`rejection_guidance` 恒带 `fix` 键，guidance.py:191-193），挪成一个函数，环境的两种只读查询和宿主的检索上限共用它。
- `_DEFAULT_REASONS` 与 `_DEFAULT_FIXES`（guidance.py:23-68、:71-85）各新增三项：

  | 码 | 默认原因 | 默认改法 |
  | --- | --- | --- |
  | `MEMORY_TIME_INVALID` | `The memory search time is not a valid period up to now.` | `Use a real date, month or year that does not start after today, a range of at most 31 days, or a clock time of today that does not start after now.` |
  | `MEMORY_KEYWORD_REQUIRED` | `Searching time 全部 requires a keyword.` | `Write a keyword, or replace 全部 with a date, range, month or year.` |
  | `MEMORY_SEARCH_LIMIT` | `This decision has already searched memory three times.` | `本次决策已查过三次记忆，请依据现有信息决定` |

- `MEMORY_TIME_INVALID` 在抛出处写明具体原因（`Refusal(code, reason, items=(item,)).error()`），先写英文错误日志 `Memory search time is invalid: <原因>`。明细一条：`ErrorItem(("time",), <time 原文>, "MEMORY_TIME_INVALID", expected="A past period in one of the declared time forms is required.", why=<原因>)`，载荷路径因此是 `["tool_calls", 0, "function", "arguments", "time"]`。原因按【S6】的检查次序取下列之一：

  | 不成立的条件 | 原因 |
  | --- | --- |
  | 非法日期或时刻 | `The time <time> is not a real date or clock time.` |
  | 起点晚于今天 | `The time <time> starts after today <today>.` |
  | 起点晚于此刻 | `The time <time> starts after the current time <now>.` |
  | 反向区间 | `The time range <time> ends before it starts.` |
  | 超过 31 天 | `The date range <time> spans more than 31 days.` |

- `MEMORY_KEYWORD_REQUIRED` 先写英文错误日志 `Memory search over all time has no keyword`，原因取默认原因，明细一条：`ErrorItem(("keyword",), None, "MEMORY_KEYWORD_REQUIRED", expected="A keyword is required when time is 全部.", why="Searching time 全部 requires a keyword.")`。
- `MEMORY_SEARCH_LIMIT` 由宿主以 `query_rejection(refusal_for_code("MEMORY_SEARCH_LIMIT").error())` 生成：原因、改法取默认，`items` 为空列表。

### 【S11】结果形状

- 顶层键按次序为 `类别`、`时间`、`关键词`、`结果`、`未列出`；`未列出` 非空时其后加 `提示`；早于最早日期的空结果在 `未列出` 之后加 `最早日期`（`YYYY-MM-DD`，值与 01 的「可查」相同），这时没有 `提示`。`类别`、`时间`、`关键词` 原样照抄请求（`时间` 截到此刻时仍写原文）。`未列出` 恒存在，没有时为 `[]`。没有 `result` 键。
- `结果`：
  - `category` 不是「全部」时，是一个列表，按装填次序排列（`all` 时今天条目在前、往日在后）；
  - `category` 是「全部」且 `time` 解析为 `today` 时，是今天条目的列表，不分组，每条只出现一次；
  - `category` 是「全部」且解析为 `day`、`range`、`month`、`year` 时，是按类别分组的对象，键按 `MEMORY_CATEGORIES` 的次序，只列出至少有一项的类别，各类别内按装填次序；
  - `category` 是「全部」且解析为 `all` 时，同上，另在最前面加键 `今天`（有匹配的今天条目时），值是今天条目的列表；
  - 没有任何项时，列表为 `[]`，分组对象为 `{}`。
- 结果项：
  - 今天条目：02 的全文形状（指定类别时为切分后的全文，「类别」写这一类）；
  - 日回顾：`{"日期":"YYYY-MM-DD","类别":…,"回顾":…}`；月回顾：`{"月份":"YYYY-MM","类别":…,"回顾":…}`；年回顾：`{"年份":"YYYY","类别":…,"回顾":…}`；分组结果里也保留「类别」；
  - 「未整理」的一天：`{"日期":"YYYY-MM-DD","类别":…,"未整理":true,"条目":[…]}`，`条目` 是放入的原始条目（02 的全文形状，按这一类切分，「类别」写这一类），次序同【S9】。
- tool 消息正文由现行 `_append_react_tool_result` 写成 `json.dumps(payload, ensure_ascii=False, separators=(",", ":"))`。

示例（10-12 11:50 查 10-09 饮食，那天这一类有日回顾）：

```json
{"类别":"饮食","时间":"2026-10-09","关键词":null,"结果":[{"日期":"2026-10-09","类别":"饮食","回顾":"早上在家点了火腿芝士牛角三明治……"}],"未列出":[]}
```

### 【S12】说明文字

- `TOOL_DESCRIPTIONS`（life_prompt.py:113-126）删去 `memory_recent`、`memory_search`、`memory_time_range`，加 `search_memory`；键、次序与用途句原文以第 07 章「原生工具用途句」为准，本章只规定工具集合为八个原生工具加 `finish`。`SearchMemoryRequest` 的文档字符串 `@brief` 之后的文字与该用途句逐字相同（第 07 章）。
- 教模型怎样用 search_memory 的规则文字只在系统层（07）；工具说明只写用途。

### 实施顺序

先 P1：参数模型与 `QUERY_MODELS`、`query_rejection` 与三个码、`search.py` 的解析、今天条目、按日期与区间、装填与形状、环境与宿主两侧、删除 `memory_*`；再 P3：月单元与年单元、带关键词时的月、年回顾。两步都在本次交付内完成，测试一起合入。

## 接口与文件

### src/worldmock/state/interaction/requests.py（改）

- 新增常量 `MEMORY_KEYWORD_MAX_CHARS = 40`、`MEMORY_KEYWORD_MAX_WORDS = 4`。
- 新增类（放在 `RecallTaskSummaryRequest` 之后）：

```python
class SearchMemoryRequest(ToolRequestModel):
    """@brief 按类别和时间查自己过去的记忆，可以加关键词，不改变世界。"""

    category: Literal["饮食", "工作", "生活", "全部"]
    time: str = Field(pattern=r"^(今天( \d{2}:\d{2}(~\d{2}:\d{2})?)?|全部|\d{4}(-\d{2}(-\d{2}(~\d{4}-\d{2}-\d{2})?)?)?)$")
    keyword: str | None

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, keyword: str | None) -> str | None:
        """@brief 关键词去掉首尾空白后非空、至多 40 字、按空白拆开至多 4 个词（DeepSeek strict 禁 maxLength，由本地校验兜住）。"""
        if keyword is None:
            return None
        words = keyword.split()
        if not words:
            _LOGGER.error("Memory search keyword is blank")
            raise ValueError("memory search keyword must contain a word or be null")
        if len(keyword.strip()) > MEMORY_KEYWORD_MAX_CHARS:
            _LOGGER.error("Memory search keyword exceeds its character limit")
            raise ValueError("memory search keyword exceeds 40 characters")
        if len(words) > MEMORY_KEYWORD_MAX_WORDS:
            _LOGGER.error("Memory search keyword has too many words")
            raise ValueError("memory search keyword has more than four words")
        return keyword
```

  `_LOGGER` 是 requests.py 现有的 `worldmock.state.logging._LOGGER`。
- 新增（放在 `REQUEST_MODELS` 之后）：`QUERY_MODELS: dict[str, type[FrozenModel]] = {"search_memory": SearchMemoryRequest}`，注释写明它不进 `REQUEST_MODELS`、由 `LifeEnv.read_query` 回答。
- `REQUEST_MODELS`、`ActionRequest`、`RecallTaskSummaryRequest` 不改。

### src/worldmock/state/interaction/errors.py（改）

- 删除 `_HOST_DEFAULTS` 的 `MEMORY_QUERY_FAILED` 项（:41-42）。它唯一的产生方 `_dispatch_strict_memory` 被删除；`HOST_ERROR_CODES` 随之少一码。

### src/worldmock/simulation_engine/observation/wording/guidance.py（改）

- `_DEFAULT_REASONS`、`_DEFAULT_FIXES` 各加【S10】表中的三项。
- 新增 `query_rejection(error: BusinessRuleError) -> dict`（【S10】）。模块说明补一句：只读查询的拒绝不进进度层，经 `query_rejection` 写成五字段形状直接作为 tool 结果。

### src/worldmock/simulation_engine/observation/memory/search.py（新，P1 与 P3）

模块说明：「@brief search_memory 的回答：在冻结观察的事件序号及以前，按类别与时间取今天条目和日、月、年回顾，可加关键词；只读，不写事件。」新包的 `__init__.py` 由第 01 章建立。

常量：

| 名称 | 值 |
| --- | --- |
| `MEMORY_SEARCH_RESULT_CHARS` | `6400` |
| `MEMORY_RANGE_MAX_DAYS` | `31` |
| `_HINT_TOO_LARGE` | `"范围太大，换更短的时间、指定类别或加关键词"` |
| `_HINT_UNSORTED` | `"未整理的日子按日期单独查"` |
| `_HINT_NARROW_DAY` | `"加关键词缩小范围"` |
| `_UNSORTED_SUFFIX` | `"（未整理，按日期单独查）"` |

数据类（`frozen=True, slots=True`，每个成员注明语义）：

```python
@dataclass(frozen=True, slots=True)
class SearchPeriod:
    """@brief 解析后的检索时间。"""
    kind: Literal["today", "all", "day", "range", "month", "year"]
    first: str   # today：起始时刻 HH:MM；day、range、month、year：首日 YYYY-MM-DD；all：空串
    last: str    # today：终止时刻 HH:MM（已截到此刻）；day、range、month、year：末日 YYYY-MM-DD（不截到昨天）；all：空串
    period: str  # day：YYYY-MM-DD；month：YYYY-MM；year：YYYY；其余：空串

@dataclass(frozen=True, slots=True)
class SearchEntry:
    """@brief 一条可检索的条目。"""
    clock: str                 # 「时刻」HH:MM，零点收尾条目为 24:00
    full: dict                 # 02 的全文形状（全部类别）
    sliced: dict[str, dict]    # 类别 → 按 02 切分后的全文，「类别」写这一类；只含「类别」串里出现的类别

@dataclass(frozen=True, slots=True)
class SearchDigest:
    """@brief 检索一侧的一期回顾（一个 memory_digest_generated 事件），类别换成中文。"""
    tier: Literal["day", "month", "year"]
    period: str                # YYYY-MM-DD、YYYY-MM 或 YYYY
    texts: dict[str, str]      # 类别（中文）→ 回顾正文，只含有正文的类别
    unsorted: frozenset[str]   # 「未整理」的类别（中文）

@dataclass(frozen=True, slots=True)
class MemorySources:
    """@brief 一次检索的全部输入，都取自截止序号及以前的事件与冻结输入。"""
    today: str                                   # 本地日期 YYYY-MM-DD
    now: str                                     # 此刻 HH:MM
    earliest: str                                # 01 的「最早日期」
    entries: tuple[SearchEntry, ...]             # 今天的已发布条目，按发布次序
    digests: tuple[SearchDigest, ...]            # 截止序号及以前的全部回顾期，按事件序号
    past_entries: Callable[[str], tuple[SearchEntry, ...]]  # 往日日期 → 那天全部条目（含零点收尾条目），按 02 的次序
```

类名 `SearchDigest` 与第 04 章 evidence/memory_digest.py 的期次身份类 `DigestPeriod(tier, period)` 不同名：那个只是期次，这个带正文与「未整理」的类别。

函数（全部 ≤50 行、≤5 个参数，中文 `@brief` 说明；私有函数的拆分由实现决定，但下列公开函数和语义固定）：

| 签名 | 语义 |
| --- | --- |
| `search_memory(context: EngineContext, agent_id: int, request: SearchMemoryRequest, observation: Event) -> dict` | `answer_search(request, memory_sources(context, agent_id, observation))` |
| `memory_sources(context: EngineContext, agent_id: int, observation: Event) -> MemorySources` | 读取方，只调用下列上游函数，记 `tz = context.inputs.config.timezone`、`cutoff = observation.event_seq`：today 与 now 按【S6】；`earliest = earliest_date(context.inputs.config.start_time, tz)`（01，observation/memory/layout.py）；`entries` 为 `published_today(context.inputs, context.store, agent_id, observation.occurred_at, cutoff).entries`（02，observation/memory/days.py，按决策次序即发布次序）逐条经 `_search_entry` 换成 `SearchEntry`；`digests` 为 `registered_digests(context.store, agent_id)`（04，observation/memory/digests.py）中 `event_seq <= cutoff` 的事件按 `event_seq` 升序逐个换成 `SearchDigest`：`tier`、`period` 照抄 `data`，`texts = {MEMORY_CATEGORIES[i]: data["digests"][key]["text"] for i, key in enumerate(MEMORY_DIGEST_KEYS) if data["digests"][key] is not None}`，`unsorted = frozenset(MEMORY_CATEGORIES[MEMORY_DIGEST_KEYS.index(key)] for key in data["fallback"])`（`MEMORY_DIGEST_KEYS` 为 04 在 state/model/memory.py 定义的 `("food", "work", "life")`）；`past_entries = lambda day: tuple(_search_entry(entry) for entry in past_day_entries(context.inputs, context.store, agent_id, day, observation.occurred_at))`（02，days.py【E39】，`now` 取冻结观察的 `occurred_at`；含零点收尾条目，按 02 的次序），只在取「未整理」的内联单元时调用。不读 `context.state` |
| `_search_entry(entry: TodayEntry) -> SearchEntry` | `full = entry_full(entry)`（02，observation/memory/entries.py）；`SearchEntry(clock=full["时刻"], full=full, sliced={category: part for category in MEMORY_CATEGORIES if (part := entry_full(entry, category)) is not None})` |
| `parse_search_time(text: str, today: str, now: str) -> SearchPeriod` | 【S6】；不成立时抛 `MEMORY_TIME_INVALID`（【S10】） |
| `answer_search(request: SearchMemoryRequest, sources: MemorySources) -> dict` | 纯函数：解析时间 → 「全部」无关键词抛 `MEMORY_KEYWORD_REQUIRED` → 早于最早日期返回空结果 → 按【S7】取单元 → 按【S9】排序与装填 → 按【S11】组装 |

`answer_search` 只依赖 `MemorySources`，所以它的全部语义可以不经存储单测；`memory_sources` 只负责从存储与冻结输入取数。

### src/worldmock/simulation_engine/engine.py（改）

- 导入 `SearchMemoryRequest`（与 :25 的 `RecallTaskSummaryRequest` 同一行）和 `from worldmock.simulation_engine.observation.memory import search`；`Event` 在 `TYPE_CHECKING` 下从 recording_engine.events.models 导入。
- 新增方法，放在 `read_task_summary`（:147-149）之后：

```python
    def search_memory(self, agent_id: int, request: SearchMemoryRequest, observation: "Event") -> dict:
        """@brief 只读检索本人记忆：只读冻结观察所在事件序号及以前的事件与冻结输入，不写事件、不改世界。"""
        return search.search_memory(self.context, agent_id, request, observation)
```

### custom/envs/life_env.py（改）

- 导入：`from worldmock.state.interaction.requests import QUERY_MODELS, REQUEST_MODELS`；`from custom.agents.life_agent import life_action_tool_names, life_query_tool_names`；`from worldmock.simulation_engine.observation.wording.guidance import query_rejection`（`rejection_guidance` 不再在这里使用，删去它的导入）。
- 新增模块常量 `_READ_QUERY_MODELS`（【S4】），注释：「read_query 的白名单：回看任务与 QUERY_MODELS 的只读查询，工具名 → 请求类型（docs/design/06-agent.md「提示与工具」）」。
- `_validate_registered_tool_surface`（:118-134）：加第三项核对（【S4】），说明文字改为「核对环境与人物两侧登记的工具闭集：请求工具与稳定工具面一致、人物侧生活动作工具与请求工具一致、人物侧只读查询工具与 read_query 白名单一致」。
- `read_query`（:156-180）按【S4】改写：:161 的类型核对一行恰为 `if type(request) is not _READ_QUERY_MODELS.get(tool):`（源码变异 `read_query_channel_ignores_the_tool_name` 以这一行为原文）；回答分两支，`if tool == "recall_task_summary":` 一支调用 `read_task_summary`，其余（只能是 `search_memory`）调用 `search_memory`；业务拒绝 `return query_rejection(error)`。说明：「@brief 对已冻结观察即时回答回看任务与检索记忆，不产生世界动作、事件或人物记忆；白名单以外的工具名或与工具名不符的请求类型一律是协议错误，业务拒绝以五字段形状返回。」

### custom/agents/life_agent.py（改）

删除（连同只为它们存在的导入 `model_validator`）：

| 位置 | 内容 |
| --- | --- |
| :95-99 | `MemoryRecentRequest` |
| :101-114 | `MemorySearchRequest` |
| :117-140 | `MemoryTimeRangeRequest` |
| :158-166 | `_parse_memory_time` |
| :169-173 | `_MEMORY_TOOL_MODELS` |
| :185-189 | `_MEMORY_RUNTIME_ACTIONS` |
| :191-192 | `_MEMORY_RESULT_FIELDS` 与其注释 |
| :194 | 孤立的注释「以活动名为参数的控制工具。」 |
| :614-615 | `dispatch_react_tool` 的 `_MEMORY_TOOL_MODELS` 分支 |
| :665-683 | `_dispatch_strict_memory` |

改动：

- 导入 `QUERY_MODELS` 与 `REQUEST_MODELS`（同一行，:71），导入 `query_rejection`、`refusal_for_code`（guidance）。
- 新增常量 `MEMORY_SEARCHES_PER_DECISION = 3`，注释：「每次决策至多派发的检索次数；第 4 次起以 MEMORY_SEARCH_LIMIT 回答、不送环境（协议护栏，不是回合上界）」。
- `_TOOL_MODELS = {**REQUEST_MODELS, **QUERY_MODELS}`，注释改为「生活决策的工具面：七个请求工具（REQUEST_MODELS 顺序，两个开始工具在前）与检索记忆，共八个；没有 finish」。
- `_QUERY_TOOL_MODELS = {"recall_task_summary": REQUEST_MODELS["recall_task_summary"], **QUERY_MODELS}`；`_WORLD_TOOL_MODELS` 不改（:183）。
- `life_action_tool_names()` 返回 `frozenset(_TOOL_MODELS) - frozenset(QUERY_MODELS)`。
- 新增 `life_query_tool_names() -> frozenset[str]`：「@brief 返回 LifeAgent 开放的全部只读查询工具名（env 侧与 read_query 白名单核对用）。」返回 `frozenset(_QUERY_TOOL_MODELS)`。
- `prepare_react_loop`（:460-473）：在 `self._life_action_count = 0` 之后加 `self._life_memory_searches = 0`。
- `_request_tool_models`（:397-404）改为：先取 `remaining = self._remaining_turns()`（保留零回合时的明确失败）；`readonly` 时返回 `{"finish": ReadonlyFinishRequest}`；否则照旧按 `remaining > 1 and not retry` 决定是否加 `_QUERY_TOOL_MODELS`。说明：「@brief 只读问答只开放 finish；生活决策的最终模型回合和截断重试只开放世界请求，其余回合另开放回看任务与检索记忆。」
- `dispatch_react_tool`（:595-616）：在 recall 分支之后加 `if action == "search_memory": return await self._search_memory(args)`；说明改为「@brief 执行生活动作；回看任务与检索记忆送 LifeEnv.read_query 当场回答」。
- 新增 `async def _search_memory(self, args: dict[str, Any]) -> ReactToolResult`，行为按【S3】，说明：「@brief 检索记忆：本次决策前三次送 LifeEnv.read_query，第四次起当场以 MEMORY_SEARCH_LIMIT 回答、不送环境；回答带 error_code 时 ok 为假，宿主据此把明细路径改写到实际调用位置。」
- 说明文字：模块说明 :3-5 的「可以先做记忆查询」改为「可以先做只读查询（回看任务与检索记忆）」；`build_react_tool_schemas`（:348-352）写「生活决策为七个请求工具与 search_memory，共八个（没有 finish），只读问答只有 finish」；`_prepared_action_error`（:556-558）的「非生活动作（记忆查询、finish）」改为「非生活动作（检索记忆、finish）」；`_run_react_turn`（:563-568）的「记忆查询是观察」改为「回看任务与检索记忆是只读查询」；`_append_react_tool_result`（:647-651）的「记忆查询的结果」改为「只读查询的结果」。
- `ReadonlyFinishRequest`、`_validate_request`、`_stage_feedback`、`_tool_schema`、`life_tool_schema_sha256` 不改。

### src/worldmock/agents/prompt/life_prompt.py（第 07 章改）

- `TOOL_DESCRIPTIONS` 的改写由第 07 章「原生工具用途句」规定（【S12】），本章不另改这个文件。

### src/worldmock/runtime/framework/read_query.py（改说明，转发不变）

- 模块说明（:3-9）与两个类说明（:21、:37-38）随 `due_reflection` 的删除由第 08 章改写，本章不另改。
- `LifeEnvActor.read_query` 说明：:24「将回看任务交给当前唯一生活环境」改为「将回看任务与检索记忆交给当前唯一生活环境」；:27「回看的业务拒绝是返回值里的错误结构」改为「两者的业务拒绝是返回值里的错误结构」。
- `LifeEnvProxy.read_query` 说明：:41「等待远端回看任务的真实结果」改为「等待远端回看任务与检索记忆的真实结果」。

### tools/run-viewer-context.js（第 09 章改）

- `tools` 一项（:86-92）的 `fill` 与 `steps`（八个工具、最后回合去掉的只读工具、检索上限）由第 09 章「八　查看器」一节改写，本章不另改这个文件。

### 不改的位置

- `src/worldmock/simulation_engine/actions/dispatcher.py:455-458`、`decision_coordinator.py:272-287`：按名字或类型挡住非世界请求，`search_memory` 不在 `REQUEST_MODELS`，原样被挡。
- `docs/manual/runtime.md:73`：「记忆查询和回看任务仍然当场完成」对 search_memory 仍成立，用户操作不变。
- 历史运行的记录数据：tests/fixtures/dayrun_truncations.json 与 tests/unit/evaluation/test_behavior_metrics.py:352 里的旧工具名是旧运行的证据，不改。

## 设计文档

下列改写与代码、测试同一次提交（docs/INDEX.md:18）。

1. **docs/design/06-agent.md「提示与工具」**（:52-59）。改写后写明：
   - 生活决策的原生工具共八个；表格两行：「请求工具｜七个｜start_main_activity、start_side_activity、recall_task_summary、report_status、pause_activity、conclude_activity、revise_day_plan」「检索记忆｜一个｜search_memory」；
   - 世界请求是请求工具里除 recall_task_summary 外的六个；recall_task_summary 与 search_memory 是只读查询，由 `LifeEnv.read_query` 当场回答；search_memory 是人物唯一的记忆工具，见「检索记忆」；
   - 只读问答每个回合只开放 finish；
   - :71「最后回合和截断后的那次纠正……只开放世界请求」不改。
2. **docs/design/06-agent.md「进度、待办与记忆」**：新增小节「### 检索记忆」，它是本节最后一个小节，在第 04 章「日、月、年回顾」小节之后、「## 宿主模型协议」之前（各小节的次序见第 10 章「设计文档」；01、02、03、04、06 改写本节其余部分），写明：三个参数及其取值和 `keyword` 的三条上限（40 字、4 个词、不能为空白）；【S6】的九种写法与成立条件；【S7】的语义表、日单元、月单元、年单元的取法和「早于最早日期」；关键词匹配（按空白拆、`casefold`、全部出现、指定类别先切分）；【S9】的 6,400 字上限、计数口径、从新到旧的次序、遇到第一个放不下的就停、标签写法和三句 `提示`；【S11】的顶层键、`结果` 的三种组织方式和各结果项形状；三个错误码、它们的五字段形状与默认原因和改法（引用 guidance.py，不抄英文原文）；每次决策至多派发三次、第 4 次起由宿主回答、计数口径；只读保证（截止序号为冻结观察的 `event_seq`，不写事件，不推进时间，结果不含内部身份，没有 `result` 键）。同节 :281 一句改为「检索记忆（search_memory）与回看任务只读：不推进时间、不写事件，只读本次决策冻结观察所在序号及以前的事件；查商品是一项活动，见[模拟引擎](04-simulation-engine.md)「出行、内容与查询」。」
3. **docs/design/10-runtime.md「原生模型与工具」**（:79-91）：「十个原生工具」「全部十个原生工具」改为「八个」；Life 扩展通道一段改为：「Life 扩展通道在锁定的 AgentSociety2 之上只加一个只读方法 `read_query`，环境代理与环境 actor 子类各有一个同名的薄转发，LifeEnv 按工具名把它交给生活引擎的 `read_task_summary` 或 `search_memory`，AS2 补丁不改。`read_query` 回答回看任务（`recall_task_summary`）与检索记忆（`search_memory`），只接受这两个工具名与各自的请求类型，别的组合是协议错误；回答只读本次决策冻结观察所在事件序号及以前的事件，不写事件、不改世界。LifeEnv 在 init 时核对人物侧的只读查询工具集等于这份白名单。」这一段同时是 08 删除 `due_reflection` 之后的终稿。
4. **docs/design/01-architecture.md「观察、行动与执行」**（:122）：「只读查询（记忆查询与回看任务）」改为「只读查询（检索记忆与回看任务）」。
5. **docs/design/00-overview.md**（:194）：AS2 可复用清单删去「记忆检索」（人物不再经 AS2 的记忆检索取记忆）；「回看任务的只读查询由……转发」改为「回看任务与检索记忆的只读查询由……转发」，其余不改。
6. docs/manual：不改（见「不改的位置」）。

## 测试

新增测试的每个函数都对应条款；在实现缺任何一块时至少一个测试失败。

### 新增 tests/unit/agent_protocol/test_search_memory_request.py

| 测试 | 条款 |
| --- | --- |
| `test_search_memory_schema_is_deepseek_strict_and_pinned`：`deepseek_strict_schema(SearchMemoryRequest)` 不抛错，经 `json.dumps(schema, ensure_ascii=False, separators=(",", ":"))` 序列化后逐字节等于【S2】的 JSON | S2 |
| `test_search_memory_categories_are_the_memory_categories_and_all`：`get_args` 得到 `(*MEMORY_CATEGORIES, "全部")` | S2 |
| `test_search_memory_time_pattern_accepts_the_declared_forms`（参数化）：`今天`、`今天 08:03`、`今天 08:03~09:00`、`全部`、`2026`、`2026-10`、`2026-10-09`、`2026-10-01~2026-10-31` 通过；`今天08:03`、`今天 8:03`、`昨天`、`2026/10/09`、`2026-10-09~2026-10`、空串被 `string_pattern_mismatch` 拒绝 | S2 |
| `test_search_memory_keyword_bounds_are_local_checks`（参数化）：`null`、四个词、首尾带空白的 40 字通过；空串、全空白、41 字、五个词各得【S2】表中的 `ValueError` 文字，`loc == ("keyword",)`，并各写对应英文日志 | S2 |
| `test_search_memory_argument_errors_use_the_host_argument_contract`：LifeAgent 对五个词的关键词给 `ARGUMENTS_INVALID`，路径 `["tool_calls",0,"function","arguments","keyword"]` | S2 |
| `test_query_models_hold_only_search_memory_outside_request_models`：`QUERY_MODELS == {"search_memory": SearchMemoryRequest}`，与 `REQUEST_MODELS` 不相交，`ActionRequest` 的成员不含它 | S2 |

### 新增 tests/unit/life/test_memory_search.py（纯函数，合成 `MemorySources`）

| 测试 | 条款 |
| --- | --- |
| `test_today_forms_select_entries_by_clock`：`今天`、`今天 HH:MM`、`今天 A~B` 选中的条目；终点晚于此刻时截到此刻，`时间` 照抄原文 | S6、S7、S11 |
| `test_todays_date_is_the_same_as_today` | S6 |
| `test_invalid_times_are_memory_time_invalid`（参数化）：`2026-02-30`、`今天 24:00`、`今天 12:60`、全角数字日期、明天、下个月、明年、晚于此刻的 `今天 HH:MM`、起点晚于此刻的时刻段、反向时刻段、反向日期区间、32 天区间、`0000`；断言五字段（经 `query_rejection`）、原因逐字、`items[0]` 的路径与 `written` | S6、S8、S10 |
| `test_thirty_one_day_range_and_periods_ending_after_today_are_valid` | S6 |
| `test_all_time_without_keyword_is_memory_keyword_required`：原因、改法、明细逐字 | S8、S10 |
| `test_keyword_words_match_case_insensitively_and_all_must_appear`：英文大小写、全角空格拆词、跨字符串不拼接、键名与时刻不参与 | S7 |
| `test_category_slices_today_entries_before_matching`：「工作、饮食」条目查饮食只留饮食的行和项，「类别」写饮食；只在工作行出现的词查饮食查不到 | S7、S11 |
| `test_date_gives_digests_unsorted_raw_entries_and_nothing_for_no_record`：一天三类分别为有回顾、「未整理」、没有记录 | S7、S11 |
| `test_date_with_keyword_inlines_only_matching_raw_entries`：「未整理」的那一类只给匹配的原始条目；一条都不匹配时结果里没有这一类的 `"未整理":true` 项；有回顾的类只在回顾匹配时出现 | S7 |
| `test_range_and_month_with_keyword_give_matching_digests_and_list_unsorted_days`：区间加关键词只给匹配的日回顾，「未整理」的日子照样进 `未列出`；月加关键词同时给匹配的日回顾与月回顾 | S7、S9 |
| `test_today_entries_are_newest_first`：今天条目按发布次序倒排，同一分钟的两条后发布的在前 | S9 |
| `test_unsorted_day_overflow_lists_entry_labels_and_hints_keyword`：内联条目超过 6,400 字，从晚到早放整条，`未列出` 为 `YYYY-MM-DD HH:MM 类别`（含 `24:00`），`提示` 恰为 `加关键词缩小范围` | S9、S11 |
| `test_range_lists_unsorted_days_and_stops_at_yesterday`：区间含今天，往日只到昨天，「未整理」的日子写 `（未整理，按日期单独查）`，`提示` 为第 2 句；首日为今天的区间结果为空 | S6、S7、S9 |
| `test_month_prefers_month_digest_and_falls_back_to_day_digests`：有月回顾；某类「未整理」改给日单元；当月没有月回顾给日回顾 | S7 |
| `test_year_prefers_year_digest_then_months_then_days` | S7 |
| `test_keyword_periods_search_every_tier_newest_first`：年加关键词返回匹配的日、月、年回顾，次序按末日从新到旧、日在月前、月在年前 | S7、S9 |
| `test_all_time_searches_today_entries_and_every_digest`：单一类别时今天条目在前；「全部」类别时 `今天` 键在最前 | S7、S11 |
| `test_fill_stops_at_the_first_unit_that_does_not_fit`：较大的新项放不下后，较小的旧项也进 `未列出`；恰好 6,400 字放得下，6,401 放不下；计数只算正文与条目字符串 | S9 |
| `test_hints_join_in_order_and_labels_are_unique`：同时有「上限」和「未整理」时 `提示` 为两句用「；」接；同一分钟两条只留一个 `今天 HH:MM` | S9 |
| `test_all_category_groups_past_items_and_omits_empty_categories`：键序饮食、工作、生活，空类不列；没有项时为 `{}` | S11 |
| `test_period_before_the_earliest_date_is_empty_with_the_earliest_date`：键序 `类别、时间、关键词、结果、未列出、最早日期`，没有 `提示` | S7、S11 |
| `test_result_keys_are_fixed_and_never_include_result`：所有写法的顶层键都在【S11】的集合里，没有 `result` | S5、S11 |
| `test_answers_are_deterministic`：同一 `MemorySources` 两次回答逐字节相同 | S5 |

### 新增 tests/integration/life/test_memory_search_sources.py（真实引擎）

| 测试 | 条款 |
| --- | --- |
| `test_today_search_returns_the_published_full_entries`：经 `submit_round` 做两次决策（收尾带 routine 记忆、一次查询做完），第三次决策里检索「今天」：条目逐字节等于 02 的全文，含 `记住：` 的 routine 记忆和「当时查到」，不含本次决策自己的条目 | S5、S7 |
| `test_today_search_in_a_same_second_chain_sees_the_previous_decision`：同一秒先 `revise_day_plan`、再检索「今天」，看到「改了今日计划」那一条 | S7 |
| `test_digest_events_become_search_digests_up_to_the_cutoff`：经 04 的 `register_memory_digest` 登记日、月、年三期（含一类在 `fallback`），`memory_sources(...).digests` 的 `texts` 与 `unsorted` 照事件换成中文类别；截止序号之前登记的才出现 | S5、S7 |
| `test_unsorted_day_falls_back_to_that_days_raw_entries`：第一天做两次决策，跨零点由 `StubMemoryDigestClient` 让生活类重试用尽，第二天检索第一天生活类得到 `"未整理":true` 和那天的原始条目（含零点收尾条目） | S7、S11 |
| `test_search_reads_nothing_after_the_frozen_observation`：以较早的观察事件调用 `engine.search_memory`，之后登记的回顾和条目不出现 | S5 |
| `test_search_does_not_write_or_change_the_world`：前后 `last_event_seq` 与世界状态 JSON 相同 | S5 |
| `test_search_results_carry_no_internal_identity`：结果里没有任何事件身份、决策身份、`source_event_ids` 的值，也没有 8 位以上十六进制串（夹具的回顾由确定性桩 `StubMemoryDigestClient` 写成，不含十六进制串） | S5 |
| `test_search_is_byte_identical_after_reopening_the_run` | S5 |

### 新增 tests/integration/runtime/test_memory_search_tool.py

| 测试 | 条款 |
| --- | --- |
| `test_life_tool_surface_has_eight_tools_in_fingerprint_and_request_order` | S1 |
| `test_readonly_questions_offer_only_finish`：每个回合 `tools` 只有 finish；只读回复调用 search_memory 得 `TOOL_CALL_REQUIRED`，环境没有收到调用 | S1 |
| `test_search_memory_goes_through_read_query_and_leaves_only_model_events`：经 `decide` 先检索再开始活动，环境收到 `("search_memory", SearchMemoryRequest)`，tool 消息等于 `LifeEnv.read_query` 的回答；观察与 `action_requested` 之间只有模型事件 | S3、S4、S5 |
| `test_fourth_search_in_a_decision_is_answered_by_the_host`：分四个回合各检索一次，环境只收到三次，第 4 条 tool 消息逐字等于 `MEMORY_SEARCH_LIMIT` 的五字段，`fix` 为 `本次决策已查过三次记忆，请依据现有信息决定`；工具面不变；下一次决策重新计数 | S3、S10 |
| `test_four_searches_in_one_reply_get_three_answers_and_one_limit` | S3 |
| `test_environment_rejected_searches_count_toward_the_limit`：前三次检索被环境以 `MEMORY_TIME_INVALID` 拒绝，第 4 次合法检索得 `MEMORY_SEARCH_LIMIT`，环境只收到三次 | S3 |
| `test_search_dispatch_without_an_environment_fails_closed`：`_env` 为 None 时 `dispatch_react_tool("search_memory", 合法参数)` 抛 `RuntimeError("Environment is not initialized")`，`_life_memory_searches` 仍为 0；已达上限时同一调用不抛错，返回 `ok` 为假的 `MEMORY_SEARCH_LIMIT` 五字段 | S3 |
| `test_argument_errors_do_not_count_as_searches`：三次参数错误加三次合法检索，合法的三次都到达环境 | S3 |
| `test_rejected_search_paths_point_to_the_real_call`：同一回复里第二个调用写 `2026-02-30`，它的 `items[0].path` 为 `["tool_calls",1,"function","arguments","time"]`，`ok` 为假 | S3、S10 |
| `test_search_memory_cannot_be_submitted_as_a_world_action`：经 `submit_action` 送 `search_memory` 得 `ProtocolError("Direct tool call request type does not match its tool")` | S3 |
| `test_search_tool_messages_are_not_continuation_messages`：检索 tool 消息的 `_earlier_changes` 为 None | S5 |
| `test_retired_memory_tools_are_gone`：`custom.agents.life_agent` 上没有 `MemoryRecentRequest`、`MemorySearchRequest`、`MemoryTimeRangeRequest`、`_parse_memory_time`、`_MEMORY_TOOL_MODELS`、`_MEMORY_RUNTIME_ACTIONS`、`_MEMORY_RESULT_FIELDS`、`_dispatch_strict_memory`；`MEMORY_QUERY_FAILED` 不在 `HOST_ERROR_CODES`（用途句的键集与原文由第 07 章的 `test_tool_descriptions_are_the_nine_purpose_sentences` 钉住） | S1 |

### 要改的现有测试

| 文件 | 改法 |
| --- | --- |
| tests/unit/start_interfaces/test_system_layer_text.py | 由第 07 章整份改写（其中 `test_retired_wording_is_gone_from_the_system_layers_and_tool_descriptions` 的退役词含 `memory_recent`、`memory_search`、`memory_time_range`，不再断言用途句键集；用途句键集为八个原生工具加 finish，由 `test_tool_descriptions_are_the_nine_purpose_sentences` 钉住）；本章不另改这个文件 |
| tests/unit/start_interfaces/test_batch_shapes.py | `_RECENT`、`_SEARCH` 换成两条不同参数的 `search_memory` 调用，各批次形状断言不变 |
| tests/unit/agent_protocol/test_tool_description_references.py | 由第 07 章改（删去三个 `Memory*Request` 的导入，`_PARAMETER_MODELS = {**REQUEST_MODELS, **QUERY_MODELS, "finish": ReadonlyFinishRequest}`）；本章不另改这个文件 |
| tests/unit/agent_protocol/test_activity_schema.py::`test_provider_array_cardinality_gap_is_locally_rejected` | :314-316 改成 `search_memory` 的五个词关键词：Schema 接受、本地校验拒绝；删去 :20 的 `MemorySearchRequest` 导入 |
| tests/unit/agent_protocol/test_decision_context_protocol.py | `test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user` 改用 `search_memory`，计数改为环境 `read_query` 的调用；`test_readonly_loop_uses_only_persona_memory_and_question` 改为只读只开放 finish |
| tests/integration/runtime/test_life_agent_protocol.py | `_MEMORY_TOOL_NAMES` 改为 `{"search_memory"}`；`_Dispatcher`、`_GroundedCorrectionDispatcher` 的检索响应改用 `search_memory`；删去 `_MemoryRuntime.dispatch_tool`，`_Environment` 加记录调用的 `read_query`；`test_life_tool_schema_fingerprint_is_the_person_tool_surface_without_the_reflection_tool` 由第 08 章删除，八个工具的指纹次序由 test_seven_layer_request.py 的 `test_current_host_request_has_one_system_layer_and_six_user_layers` 钉住；`test_readonly_schema_uses_its_independent_finish_contract`、`test_readonly_budget_reserves_finish_and_resets_between_loops` 改为每回合只有 finish；`test_multiple_memory_calls_share_one_model_turn`、`test_reserved_action_boundary_rejects_memory_without_extra_request` 的计数改为 `read_query` 调用；其余引用 `memory_recent` 的测试（:307、:518、:954、:980、:992、:1128、:1170、:1847）改用 `search_memory`，断言含义不变 |
| tests/integration/runtime/test_current_runtime_contract.py | `test_removed_query_world_reply_is_refused_as_a_tool_that_is_not_offered`：`offered` 改为【S1】请求次序的八个；`test_deleted_start_activity_reply_is_refused_as_a_tool_that_is_not_offered`：:90 `len(offered) == 10` 改为 `== 8`，说明 :83「开放的十个工具」改为「开放的八个工具」 |
| tests/integration/runtime/test_seven_layer_request.py | `_NATIVE_TOOLS`、`_REQUEST_ORDER` 改为【S1】的八个，:41 `len(request["tools"]) == 8`；`test_tool_identity_covers_each_of_the_ten_native_tool_descriptions` 改名为 `test_tool_identity_covers_each_of_the_eight_native_tool_descriptions`，:56 `len(life_agent._TOOL_MODELS) == 10` 改为 `== 8`，说明 :52「十个原生工具」改为「八个原生工具」；:14 注释改为「生活决策的八个原生工具，按计算工具指纹的次序：七个请求工具（两个开始工具在前）与 search_memory。」，:18 注释改为「每次模型请求的 tools 数组次序：六个世界请求、回看任务与检索记忆。」；`test_tool_identity_ignores_tools_outside_the_native_surface` 的说明 :67「十个原生工具」改为「八个原生工具」（层序与规则条数归 01、07） |
| tests/integration/runtime/test_interaction_menu_roundtrip.py::`test_native_request_uses_current_location_availability_and_stable_tools` | :29 `len(request["tools"]) == 10` 改为 `== 8`；说明 :13「十个原生工具」改为「八个原生工具」 |
| tests/integration/runtime/test_read_query_runtime.py | `test_unwrapped_actor_read_query_equals_direct_environment_call` 加一次经 actor 与直接调用相等的 `search_memory`，`memory_recent` 仍作为白名单外的工具名；`test_read_query_answers_only_task_recall` 改名为 `test_read_query_answers_task_recall_and_memory_search_only`：`memory_recent`、`query_world`、`search_memory` 配 `RecallTaskSummaryRequest`、`recall_task_summary` 配 `SearchMemoryRequest` 都抛 `Read query tool and request type do not match`，两种合法查询都照常回答，全程不写事件（【S4】、【S5】） |
| tests/integration/runtime/test_env_lifecycle_coverage.py::`test_registered_tool_surface_fails_closed` | 加第三项核对的失败例：把 `life_env_module.life_query_tool_names` 换成多一个名字的集合，init 抛 `LifeEnv read query surface disagrees with the LifeAgent query tool set`（【S4】） |
| tests/integration/runtime/test_output_truncation.py | `_MEMORY_TOOLS` 改为 `set(QUERY_MODELS)`；:288 的参数改用 `search_memory`；`test_readonly_questions_keep_their_parameters_and_a_truncated_answer_is_retried_with_finish_only` 去掉只读里的记忆调用，四个请求的工具都是 `{"finish"}` |
| tests/integration/runtime/test_conclude_memories.py | `_MEMORY_TOOL_NAMES` 改为 `set(QUERY_MODELS)`；`test_life_tool_surface_never_offers_finish` 的只读工具为 `{"finish"}`；`test_registered_world_request_ends_the_decision_and_memory_queries_do_not`、`test_budget_exhausted_without_a_registered_world_request_fails_the_run` 改用 `search_memory`；`test_readonly_finish_batched_with_another_tool_is_rejected`：同批为 `search_memory` 与 finish，只读回合不开放 `search_memory`，整批零执行；`_response_errors(environment) == ["TOOL_CALL_REQUIRED: The tool call batch is invalid.", None]`；下一次请求里两条 tool 消息的 `error_code`、`reason`、`fix` 都是 `TOOL_CALL_REQUIRED` 与它的默认原因和改法（errors.py:30-31），`items` 恰为一条：路径 `["tool_calls",0,"function","name"]`、`written` 为 `search_memory`、`allowed_values` 为 `["finish"]`、`why` 为 `The tool is not offered in this round.`；随后单独的 finish 给出答案；环境 `read_query` 没有被调用，说明改为「只读问答只开放 finish：与 search_memory 同批时整批零执行、按未开放的工具纠错，单独的 finish 才给出答案。」；`test_nothing_sent_to_the_person_model_carries_an_internal_identity` 改为两次 `search_memory`，断言检索回答不含内部身份，删去 `episodes` 的断言；`test_memory_queries_and_the_reply_body_reach_the_next_progress_layer`（第 06 章改名为 `test_the_reply_body_and_the_conclusion_stay_out_of_the_next_progress_layer`）的检索部分改为「`search_memory` 不在进度层、只在模型请求证据里」（`self_note` 部分与改名归 06） |
| tests/integration/runtime/test_same_second_continuation.py | :476 的工具名改为 `search_memory`；`_EARLIER` 的 tool 内容改为检索结果形状；`test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte` 改为 `search_memory`（`{"category":"全部","time":"今天","keyword":null}`），断言那条 tool 消息在下一次请求里恰好出现一次、没有 `result` 键，删去 `append_episodes` |
| tests/integration/runtime/test_grounded_evidence.py::`test_protocol_feedback_is_persisted_in_next_model_request` | :192 改为 `search_memory` |
| tests/integration/runtime/test_host_failure_stages.py::`test_tool_service_failure_points_to_real_call_without_claiming_execution` | 两个 `search_memory` 调用，环境 `read_query` 抛异常；日志为 `LifeAgent tool dispatch failed: search_memory` |
| tests/integration/runtime/test_agent_branch_coverage.py | 删去 `test_memory_request_models_preserve_semantic_bounds`、`test_memory_dispatch_returns_retry_feedback_and_hides_episode_identities` 与 :19-21 的导入；`test_tool_messages_follow_memory_queries_only` 改为 `search_memory` 的失败结果按实际位置改写路径，删去 `MEMORY_QUERY_FAILED` 的断言 |
| tests/integration/life/test_committed_world_guard.py::`test_guard_wraps_every_public_engine_method` | `expected` 加 `search_memory` |
| tests/system/viewer/run_viewer_context.test.cjs | 由第 09 章改（工具列表与个数改为八个）；本章不另改这个文件 |
| tests/dataset_acceptance.py:554-557 | 源码变异 `read_query_channel_ignores_the_tool_name` 随 `LifeEnv.read_query` 改写：原文改为 `if type(request) is not _READ_QUERY_MODELS.get(tool):`，变异后为 `if type(request) not in _READ_QUERY_MODELS.values():`；杀手改为 `tests/integration/runtime/test_read_query_runtime.py::test_read_query_answers_task_recall_and_memory_search_only`（其中 `search_memory` 配 `RecallTaskSummaryRequest` 的协议错误例杀死它）。tests/system/mutations/test_source_mutations.py 照旧通过 |

### 删除的现有测试

| 文件 | 原因 |
| --- | --- |
| tests/integration/runtime/test_coverage_closure_agent.py（整个文件） | 两个测试都只验证已删除的 `memory_time_range` |
| tests/integration/runtime/test_verified_memory.py::`test_memory_tool_returns_explicit_subjective_label_even_when_empty` | 验证的是 AS2 记忆运行时的 `dispatch_tool`，WorldMock 不再有任何调用方 |

## 不在本章

- 今天条目的生成、全文、切分和发布（02）；回顾的生成、校验、事件和读取（04）；「可查」与记忆层组装（01）；类别推导与归日（03）。本章只调用它们。
- 系统层与只读系统层的规则文字，包括教模型用 search_memory 的 rules[1]、rules[2]；`TOOL_DESCRIPTIONS` 的键、次序与用途句原文；test_system_layer_text.py 与 test_tool_description_references.py 的改写（07）。
- `due_reflection` 在通道、引擎和 LifeAgent 的删除，read_query.py 的模块说明与类说明，以及反思、巩固相关测试（08）。
- 同秒续接 `changes` 的改动（06）。
- 查看器的全部文字（含 `tools` 一项）与它的测试、tests/fixtures/viewer-context.json 重录、试点指标 M5 与 memory_pilot.py（09）。
- 指纹与版本变化、两候选重冻、覆盖率与风格门禁（10）；新包 `__init__.py`（01）。
- 提案 P4 的各项。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 「结果里标『未整理，以下是当时的条目』」怎样标 | 用结果项的 `"未整理":true` 加 `条目` 表示，不另加文字键 | 提案给出了这一项的 JSON 形状；`提示` 只在有未列出项时出现 |
| 早于最早日期时「给出最早日期」放在哪里 | 顶层在 `未列出` 之后加 `最早日期`，与「可查」同一个键名和值；这时没有 `提示` | 提案要求给出最早日期而没有指定位置；沿用记忆层已有的键名，不新造提示句 |
| 区间、月、年跨过最早日期时算不算「早于最早日期」 | 只有这一期的末日早于最早日期（单日即那天早于最早日期）才返回空结果并给出 `最早日期`；末日不早于最早日期的照常取单元，不加 `最早日期`，最早日期之前的日子自然没有内容 | 提案只写「早于最早日期时返回空结果，并给出最早日期」，没有说跨过最早日期的一期怎样算；只有整段都早于最早日期时，这一期里才没有任何可查的日子 |
| 放不下时是否跳过它、继续放更旧更小的项 | 不跳过：遇到第一个放不下的就停，其后全部进 `未列出` | 提案「新的在前……放不下的计入未列出」及其三个举例；`未列出` 因此是连续的较旧部分。第五批裁定把日回顾提到每类 250 字以后，6,400 字的上限和这条规则都不变：单一类别 31 天满格是 7,750 字，返回最近 25 天（6,250 字），较早的 6 天以原因「上限」进 `未列出`，`提示` 是第 1 句；三类 31 天满格返回最近 8 天的三类和第 9 天的饮食 |
| 6,400 字数什么 | 只数回顾正文和条目「核验」「当时查到」「自述」的字符串；键名、时刻、类别、日期不数 | 提案「回顾正文和条目各字符串合计」；与裁定 19 只数文字的读法一致 |
| 不同级别回顾之间的「新的在前」 | 按期末日期从新到旧；同一天日回顾在前、月其次、年最后；再按类别次序 | 提案只写「新的在前」，需要一个确定的次序才可测试与复现 |
| 按日期查时回顾和「未整理」条目谁先装 | 整项回顾先装、内联条目后装；所以按日期查只会出现「加关键词缩小范围」 | 提案为按日期查「未整理」那一天放不下单独规定了提示句 |
| 几句 `提示` 同时成立 | 按「范围太大…」「未整理…」「加关键词…」的次序用「；」接，每句一次；`未列出` 去重 | 提案只规定了前两句同时出现时的接法 |
| 「全部」类别的分组形状 | 往日结果为按类别的对象，键按 `MEMORY_CATEGORIES` 次序、只列有项的类别；「今天」写法为不分组的列表；`全部` 时间另在最前加 `今天` 键 | 提案「往日的回顾按类别分组；『今天』的条目不分组，按时刻排列，每条只出现一次」 |
| 单一类别加 `全部` 时间 | 一个列表，今天条目在前，往日在后 | 同上，单一类别没有分组 |
| 指定类别查「今天」时切分后的「类别」 | 写这一类 | 提案「只给其中这一类的行和项」 |
| 空白关键词 | 参数错误，不当作 `null` | 提案「去掉首尾空白后 ≤40 字」；`keyword` 可以为 null 的写法是 `null` 本身 |
| 关键词的规范化 | 校验器不改写，`关键词` 原样照抄；拆词用 `str.split()`，匹配用 `casefold()`，按单元内各字符串匹配 | 提案「按空白拆开…不分大小写，按子串匹配，必须全部出现」 |
| `\d` 接受非 ASCII 数字 | Schema 原样保留提案的 `pattern`；环境只认 ASCII 数字，其余以 `MEMORY_TIME_INVALID`「非法日期或时刻」拒绝 | 提案逐字给出 `pattern` 并要求 Schema 逐字节钉住；非法日期属于 `MEMORY_TIME_INVALID` |
| 首日为今天的区间 | 没有往日部分，结果为空，不等同于「今天」 | 提案只把「写的是今天的日期」的单日写法等同于「今天」，区间、月、年只给到昨天 |
| 31 天怎样数 | `(末日 − 首日).days + 1 ≤ 31`，末日可以晚于今天 | 提案的例子「单一类别 31 天」；不成立条件里没有「末日晚于今天」 |
| 月回顾或年回顾某类「未整理」 | 换成下一级（日单元或月单元），自身不产生标签 | 提案「退回那个月的日回顾或那一年的月回顾」 |
| 带关键词的区间、月、年、全部遇到「未整理」的日子 | 照样进 `未列出`，不做关键词筛选；`全部` 按跨日期写法的同一规则处理 | 提案「区间、月、年查询里，『未整理』的日子只计入『未列出』」；提案「往日的原始条目不在检索范围内，按日期查『未整理』的那一格除外」，`全部` 跨越全部往日，不是按日期查，所以与区间、月、年同一规则 |
| 检索次数怎样计 | 每次派发（通过宿主参数校验）算一次，环境拒绝的也算；参数错误不算；第 4 次起由 LifeAgent 回答，不送环境 | 提案「这个计数是 LifeAgent 的协议护栏」「第 4 次返回 `MEMORY_SEARCH_LIMIT`」 |
| `MEMORY_SEARCH_LIMIT` 怎样生成五字段 | `query_rejection(refusal_for_code(...).error())`，与环境共用一个函数 | 提案「错误一律走现有 read_query 的五字段形状……每个错误码在 rejection_guidance 里有一条说明」；一条规则只在一处 |
| 检索结果的 `ok` | `"error_code" not in data`；回看任务保持恒为真 | 提案要求 `items[].path` 能被宿主改写，而 life_agent.py:658-661 只在 `ok` 为假时改写 |
| 三个码的原因和改法用什么语言 | 英文，与其余业务拒绝一致；只有 `MEMORY_SEARCH_LIMIT` 的改法用提案钉住的中文原文 | 提案只钉住这一句；现行 `_DEFAULT_REASONS`、`_DEFAULT_FIXES` 全为英文 |
| `MEMORY_QUERY_FAILED` | 删除 | 唯一产生方 `_dispatch_strict_memory` 被删除；AGENTS.md 要求删除过时代码 |
| 时刻段截到此刻后 `时间` 写什么 | 照抄请求原文 | 三个回显键都是请求的回显，条目本身带时刻 |
| 相同的「今天 HH:MM」标签 | 只留一个；零点收尾条目的标签写 `24:00` | 标签用来指引按时刻再查，同一分钟一次查询就能取回；零点收尾条目的「时刻」是 24:00 |
| 年回顾的标签写法 | `YYYY 类别` | 提案给出了日、月的写法，年按同一格式 |
| `QUERY_MODELS` 的类型与 `category` 的写法 | `dict[str, type[FrozenModel]]`，与 `REQUEST_MODELS` 相同；`category` 照提案写成字面 `Literal`，另由测试核对它等于 `MEMORY_CATEGORIES` 加「全部」 | 提案逐字给出参数模型；同一闭集由测试防漂移 |
| 检索的模块结构 | 读取方 `memory_sources` 与纯函数 `answer_search` 分开 | 提案要求时间、关键词、上限各边界逐一测试；纯函数不需要跨多天推进运行就能测到月、年两级 |
| 只读问答没有回合时 | `_request_tool_models` 先调用 `_remaining_turns()`，只读也照旧明确失败 | 保留 life_agent.py:388-395 的现行语义，只读改为只开放 finish |
| 查看器 `tools` 说明归哪一章 | tools/run-viewer-context.js 与 run_viewer_context.test.cjs 全部由 09 改，本章只给出它们依据的事实（八个工具、最后回合撤下的只读工具、检索上限） | 一条规则只维护一处；09 的「八　查看器」已写出 `tools` 一项的终稿 |
| `TOOL_DESCRIPTIONS` 归哪一章 | 键、次序与用途句原文归 07，本章只规定工具集合；test_system_layer_text.py 与 test_tool_description_references.py 由 07 改 | 07「原生工具用途句」写明终稿只在那一节定义；同一测试不由两章各写一种改法 |
| read_query.py 的说明文字归哪一章 | 模块说明与两个类说明随 `due_reflection` 的删除归 08；本章只改两个 `read_query` 方法说明里回答哪些查询的那几个词 | 08 已改写模块说明与类说明；方法说明写的是回答的工具，属于本章 |
| 检索一侧的回顾期叫什么 | `SearchDigest` | 04 的 `DigestPeriod(tier, period)` 是期次身份；同一个名字不指两种东西 |
| `memory_sources` 怎样取上游数据 | 只调用 01 的 `earliest_date`、02 的 `published_today`、`entry_full`、`past_day_entries`、04 的 `registered_digests`，并按截止序号筛回顾事件；`SearchEntry.clock` 取全文的「时刻」 | 这些是 01、02、04 为检索公开的函数；零点收尾条目的「时刻」在全文里写作 24:00（02【E33】） |
| 没有环境时检索 | 与回看任务相同：抛 `RuntimeError("Environment is not initialized")`，计数不变；已达上限时不需要环境，照常以 `MEMORY_SEARCH_LIMIT` 回答 | 照 recall 分支（life_agent.py:608-613）的写法；第 4 次起不送环境 |
| 00-overview.md 的 AS2 可复用清单 | 删去「记忆检索」 | 人物的记忆检索不再经 AS2 的记忆工具 |
