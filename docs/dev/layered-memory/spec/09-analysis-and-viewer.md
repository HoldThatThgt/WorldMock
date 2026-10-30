# 09 分析工具、运行比对与查看器

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。

## 目标与依据

本章实现提案（`docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md`，下称提案）里所有只读的工具面：

| 提案位置 | 内容 | 本章交付 |
| --- | --- | --- |
| 第四章「P0　测量」（:871-875） | 只读分析脚本，计算 M1–M12，复核记忆层 token/字比 0.638 | `tools/analysis/memory_pilot.py` |
| 第四章「试点指标」（:945-962）及 P1、P2、P3 的确定性验收与试点各条（:890-906、:917-924、:932-936） | M1–M12 的定义、阈值和试点规程 | `memory_pilot.py` 的指标与检查项；本章「试点规程」 |
| 第二章「类别」（:369） | 为每个包列出每条内容行和 app 行推导出的类别及理由，包 0001 的推导表由测试钉住 | `tools/analysis/memory_categories.py`（类别与规则取自第 03 章的 `row_categories`，本章只加名称、字段与理由）；包 0001 推导表的钉住由第 03 章的数据集测试负责，本章的数据集测试核对工具输出与 `row_categories` 一致 |
| 第二章「压缩」（:462、:476） | M10 报告各道守卫的丢弃次数，M11 独立复核；行为工具对回顾正文报告相邻日重复率和示例身份残留 | `memory_pilot.py` 的 M10、M11；`tools/analysis/behavior/metrics.py` 的两项回顾报告 |
| 第三章「代码」表（:766） | `compare_runs.py` 只加两条事件身份路径；更新查看器说明 | `scripts/compare_runs.py`；`tools/run-viewer-context.js` 与 `tools/run-viewer-context-view.js` |
| 第三章「测试与门禁」（:814、:843、:849） | 查看器测试跟着记忆层形状改；用真实宿主重录 `tests/fixtures/viewer-context.json`；`tools/analysis` 下的新脚本同样受风格门禁检查 | 本章「测试」 |

相关裁定：裁定 6（目录格式 6 与 `memory_category`，类别表的输入）、9（月、年回顾的验证方式，跨月试点）、13（日回顾引用率只报告）、17 与 25（看法移出提示，M12 和看法对象覆盖率只报告）、23（「未整理」的退路，M3、M10 分开计数）、24（兜底，M6 计触发次数）、29（看法移出放在 P2，M12 的措辞回声旁证）。

按编排方的全局决定，P0–P3 一次交付，提案的分期只是实现次序：本章的工具只读最终形态的运行；P1、P2 两次试点合并成一组最终形态的试点；P4 不在本章。试点要调用真实模型，本次交付不执行，规程与判定写在「试点规程」，由 `memory_pilot.py` 计算。

## 行为

### 一　`memory_pilot.py` 的输入

1. **输入是一次运行的认证导出目录**（`worldmock export` 的输出）。`load_export(directory)` 先调用 `worldmock.recording_engine.validation.validation.validate_export(directory)`（src/worldmock/recording_engine/validation/validation.py:39）认证导出（docs/design/05-recording-engine.md「评估同样先认证输入」），然后才读任何文件。事件的字节摘要与行数（`artifacts.events.rows`）、逐行严格校验、冻结环境素材在内的全部素材的字节数与摘要、各投影以及质量报告与权威事件一致，都由认证核对；认证失败时它抛出的 `IntegrityError` 原样上抛，本工具不再重复这些核对。认证通过后本工具自己只读其中五样：`manifest.json`、`events.jsonl`、`purchases.jsonl`、`quality.json` 和 `assets/<sha256>`。不读运行目录、不读人物工作区、不写任何文件（`--output` 指定的报告除外）。
2. **只接收最终代码写出的导出。** 认证用当前代码重算质量报告（质量版本 10）并与 `quality.json` 逐字节比较，旧代码写出的导出通不过认证；本工具不另查版本号。V、B（质量版本 9、目录格式 5）因此不能作为输入。试点报告并列的 V、B 基线只取提案第四章「试点指标」表与 P0 一条里写出数字的项：M2 0% 与 2 次，M3 21/134，M4 3/4，M5 0.4 次/天与 100% 为空，M6 取提案第二章写出的各层大小，M7 0.336，M8 $0.338，M12 5/18 与 B 0/17，措辞回声 V 60/406。M1 在提案表里写作「P0 测」，本交付不在旧运行上计算；试点报告里 M1 只在 `lm-variety-*` 与 `lm-baseline-*` 之间比较，并写明「无 V、B 基线」。
3. **只接收单人运行。** `manifest.json` 的 `frozen_run_manifest.agent_ids` 必须恰有一个编号，否则拒绝：`memory_pilot reads single-person exports; the export has {n} agents`。
4. **事件。** `events.jsonl` 逐行解析为 JSON 对象（认证已逐行校验），按 `event_seq` 升序使用。导出的观察事件已由生产解码器换回分件，`data.memory_layer` 等层直接可读。
5. **冻结目录。** 取 `frozen_run_manifest.environment_assets` 里 `catalog/merchant-catalog.jsonl`、`catalog/content-catalog.jsonl`、`catalog/app-catalog.jsonl`、`catalog/location.jsonl`，以及登记了时的 `catalog/callbacks.jsonl`，按 `sha256` 读 `assets/<sha256>`（字节数与摘要已由认证核对）；前四个名字有一个不在 `environment_assets` 里即拒绝：`Frozen environment asset {name} is missing from the export`。各行解析为对象，不经目录加载器（不核对格式号，也不做准入）。
6. **时区与日子。** 时区取 `manifest.json` 的 `window.timezone`。`local(t)` 是事件时刻 `t`（`occurred_at`，UTC 秒）在该时区的本地日期。运行日 `days` 取 `manifest.json` 的 `daily_coverage`（导出按 `window_days` 把 `window.start` 到 `window.end`（最后一条导出事件的时刻）沿本地零点切出的本地日期，含不足一天的最后一天）；第 `i` 个运行日的序号为 `i`（从 1 起）。「回顾日」是除最后一个运行日以外的运行日（最后一个运行日之后的零点要么是运行终点、要么不在导出窗口里，都没有它的日回顾）。
7. **错误。** 本工具自己查出的输入问题（上面第 3、5 条与「接口与文件」里的两句）抛 `tools.analysis.errors.EvaluationError("memory_pilot_input", <英文句子>)`；认证失败是 `validate_export` 抛出的 `IntegrityError`（`worldmock.foundation.errors.WorldMockError` 的子类）。两种异常构造时都已写错误日志；命令行入口捕获 `EvaluationError` 与 `WorldMockError` 后返回 1，不另写日志。

### 二　共用的口径

下面的名词在各指标里含义固定。

| 名词 | 定义 |
| --- | --- |
| 决策 `d` | 一条 `decision_started` 事件；`decision_id`、时刻 `t_d`、日期 `D_d = local(t_d)` |
| 决策的回合请求 | `decision_id == d` 且 `data.purpose` 缺失或为 null 的 `model_request`，按 `event_seq` 排列；对应的 `model_response` 按 `caused_by` 找到 |
| 首个请求、新快照、续接 | 回合请求里 `event_seq` 最小的一条；它的 `messages` 恰为两条时是新快照，多于两条时是续接 |
| 回合数 | 回合请求 `call_id` 里 `:model:NNNN:` 的不同 `NNNN` 的个数（截断重试是同一回合） |
| 决策的观察 `O_d` | `decision_id == d` 的 `observation` 事件 |
| 世界请求、受理与结果 | `decision_id == d` 的 `action_requested`；它的受理是 `caused_by` 含它的 `action_accepted`（活动快照为 `data.activity`）；它的结果是 `caused_by` 含它的 `action_result` 中 `event_seq` 最小的一条，本章凡说「结果为 completed」都只看这一条的 `data.status`；被拒是 `caused_by` 含它的 `action_rejected` |
| 提交末序号 `C_j` | 第 02 章【E2】的口径：`decision_id` 等于决策 `j` 的 `action_result` 中 `event_seq` 最小的一条记为 `s`，`C_j` 是从 `s` 起 `state_version_before` 与 `state_version_after` 都与 `s` 相同的连续事件里最大的 `event_seq`（`store.commit_last_seq` 同一算法）；没有 `action_result` 的决策没有 `C_j` |
| 检索结果项 | `search_memory` 成功结果（第 05 章【S11】）的「结果」为列表时取它的各项；为对象时按键序取各值（都是列表）的各项依次接起来 |
| 模型文字 `T_d` | 各回合响应 `data.response.choices[0].message` 的 `reasoning_content`、`content`，以及每个 `tool_calls[].function.arguments`：能解析成对象时，`items` 列表里每一项去掉 `views` 后各自紧凑序列化，其余键紧凑序列化；解析不了时用原文。按响应次序以换行连接 |
| 请求文字 `R_d` | 各回合请求全部消息的字符串 `content`、`reasoning_content` 与 `tool_calls[].function.arguments`，按请求和消息次序以换行连接 |
| 思考文字 `RS_d` | 各回合响应的 `reasoning_content`，以换行连接 |
| 总结文字 `S_d` | 决策里 `tool_name` 为 `conclude_activity`、`pause_activity`、`report_status` 的世界请求 `request.items[].summary`，以换行连接 |
| 已了结的看法 | 结果为 `completed` 的 `conclude_activity` 请求里各 `items[].views[]`，带 `about`、`type`、`text`；看法的日期是该请求 `occurred_at` 的本地日期 |
| 活动的自述 | 结果为 `completed` 的 `conclude_activity`、`pause_activity`、`report_status`：结果 `data.data.activity_ids[i]` 是哪项活动，请求第 `i` 项的 `summary`、`views[].text`、`memories[].text` 就是那项活动的自述 |
| 食品商品 | `category` 为 `meal`、`snack`、`drink` 的商品 |
| 食品状态 | `callbacks.jsonl` 里带 `state` 的行，且某条内容行 `consumes_state` 等于它、同时 `completion_effects.food` 非空（与第 08 章 `Catalog.food_states()` 同义；本工具按 M11 的独立性要求在冻结目录行上另算，不导入目录模型） |
| 食材包 | 食品状态 `reset_on_goods` 里的商品 |
| 做饭活动 | 一条 `callback_effect` 世界事件，`type` 为 `consume_on_start`、`state` 是 `reset_on_goods` 非空的食品状态；它 `caused_by[0]` 所指事件的 `activity_id` 就是这项活动。所用食材包是同一 `state` 在它之前最近一条 `type` 为 `reset_to_cap` 的 `callback_effect` 的 `goods_id`，收到日是那条事件的本地日期；之前没有重置时为「家里原有的食材」。结束时刻取该活动状态进入 `completed`、`cancelled`、`failed`、`truncated` 的那条 `activity_progress` |
| 规范化 `norm(s)` | NFKC 后删去正则 `[\s\W_的了]` 匹配的字符 |
| 最长公共子串 `lcs(a, b)` | `a` 的子串里同时是 `b` 的子串的最长长度 |
| 八字回声 | 文本 `x` 的某个连续 8 字窗口出现在文本 `y` 里（`ECHO_LENGTH = 8`） |
| 句 | 回顾正文按「。」「！」「？」切开，每段连同结尾的标点算一句，最后不带标点的一段也算一句 |
| 餐次时段 | `MEAL_PERIODS`：早饭 [04:00, 10:30)，午饭 [10:30, 14:30)，晚饭 [16:30, 22:00)，本地时刻，左闭右开 |

**菜名词表**（M1、M2、M11 共用），按长度降序、同长按字典序排列、去重：
- 全部食品商品名；
- 全部食材包名，以及食材包的菜名：名字去掉结尾的「一人份食材包」（没有则去掉结尾的「食材包」）后按「配」切开，长度 ≥2 的各段；
- 食品内容（`completion_effects.food` 非空，或 `consumes_state` 是食品状态）的 `summary` 里，第一个「比如」或「例如」之后、到第一个「，」「。」「；」「…」之前的文字，按「、」「和」「或」切开，长度 ≥2 的各段。

在一段文字里**认菜名**：从左到右扫描，每个位置取以它开头的最长词表项，取到就跳过这一项的长度，取不到前进一字。商品名、食材包名在别处单独认取时同样按「最长、不重叠」扫描，词表换成对应的名称集合。

**比例**一律写成 `{"numerator": n, "denominator": d, "value": v}`，`v = round(n / d, 4)`，`d = 0` 时为 null。**分段比例**写成 `{"all": …, "days_1_3": …, "days_4_plus": …}`，三段各自累计分子分母；某条记录归第几天按各指标写明的日期取运行日序号。**统计量**写成 `{"n", "median", "p90", "max"}`：值升序排列，第 `q` 分位取下标 `(n−1)·q` 处的线性插值，中位数即 `q = 0.5`，均保留一位小数；`n = 0` 时其余三项为 null。报告里的时刻写成本地 `YYYY-MM-DD HH:MM`。

### 三　M1–M12

**M1 往日引用。** 按决策日期分段。
- 饮食决策：决策的某个受理活动是下面之一。查询：`data.targets` 里有 `category` 为 `meal`、`snack`、`drink` 的类别目标，或 `goods_id` 是食品商品或食材包的商品目标；购买：`data.order_ids` 在 `purchases.jsonl` 里对应的商品有食品商品或食材包；做饭：在地点做的事，`data.content_id` 的内容 `consumes_state` 是 `reset_on_goods` 非空的食品状态。查询和购买只算 `t_d` 落在午饭或晚饭时段里的；做饭不限时段。
- 往日文字：`O_d.data.memory_layer` 里「近三天」各类各格的「回顾」字符串；以及本决策里每次 `search_memory` 成功结果的检索结果项中，带「日期」「月份」或「年份」键的项的「回顾」，和「未整理」项「条目」里的全部字符串。
- 往日名称：往日文字里认出的菜名。往日日期：往日文字所属格或结果项的「日期」值，每个日期 `D` 写成 `YYYY-MM-DD`、`M月D日`（不补零）、`MM-DD` 三种。
- 分子：`RS_d + "\n" + S_d` 含任一往日名称或任一种往日日期写法的饮食决策；分母：饮食决策。
- 输出 `{"by_day", "periods", "decisions"}`：`by_day` 是日期到比例；`decisions` 每项 `{"decision_id", "time", "kinds", "past_names", "past_dates", "mentioned"}`，`kinds` 取 `query`、`purchase`、`cooking`。

**M2 晚饭命名。** 按做饭活动的结束日期计。
- 命名：所用食材包不是「家里原有的食材」的做饭活动，记它的结束事件（「做饭活动」一行的那条 `activity_progress`）序号为 `e`、本地日期为 `D`。取 `D_j == D` 且 `C_j ≥ e` 的决策中最早的一次为 `k`（结束句写在第 `k` 条条目里，从第 `k+1` 次决策起可见）；`D` 里在 `k` 之后还有决策时，这项活动进分母；没有这样的 `k`，或 `k` 是 `D` 的最后一次决策时不进分母。分母里的活动，那一天该人物最后一条观察的「今天」里，有条目的某个「核验」字符串包含 `"，用{M}月{D}日收到的{食材包名}"`（月日不补零；核验短句见第 02 章「今天条目」）的是分子。
- 对不上：所用食材包不是「家里原有的食材」的做饭活动，它的自述里认出的菜名中，有一个不是该食材包名子串的，记一次。
- 输出 `{"named", "mismatches", "activities"}`：`named` 是比例；`activities` 每项 `{"activity_id", "name", "ended_at", "kit", "received_on", "line_named", "mismatched_dishes"}`，`line_named` 在不进分母时为 null。

**M3 往日可见。** 按观察日期分段。
- 分母：日期 `D` 的序号 ≥2 的观察，且 `D−1` 有日回顾事件（`tier == "day"`、`period == D−1`），其中 `digests.food` 非空或 `"food"` 在 `fallback` 里。
- 分子：其中 `data.memory_layer` 的「近三天」「饮食」列表有「日期」为 `D−1` 的一格，且这一格「回顾」是非空字符串或「未整理」为 true。
- 输出 `{"by_day", "periods", "unsorted"}`：`unsorted` 是分子里这一格为「未整理」的观察数。

**M4 午饭重复。**
- 工作日：该日期第一条观察的 `data.environment_layer.duty.day` 为「工作日」。
- 午饭：`purchases.jsonl` 里 `fact_kind == "purchase"`、`category == "meal"`、`created_at` 落在工作日午饭时段的行，份数取 `quantity`。
- 重复：同一 `goods_id` 在 `D−3`、`D−2`、`D−1` 任一天有 `fact_kind == "purchase"` 的行。
- 输出 `{"workday_lunch_portions", "repeated_portions", "value", "most_frequent", "lunches"}`：`value` 是重复份数比例；`most_frequent` 是份数最多的商品 `{"name", "portions"}`（同份数取最先出现的；没有午饭时为 null）；`lunches` 每项 `{"time", "name", "portions", "repeat"}`。

**M5 检索。** 按决策日期计。
- 检索调用（结果与错误的形状见第 05 章「search_memory 与工具面」）：回合响应 `tool_calls` 里 `function.name == "search_memory"` 的调用；它的回答是此后同一决策回合请求里 `role == "tool"`、`tool_call_id` 等于调用身份的第一条消息。
- 归类：回答内容解析出带 `error_code` 的对象时，`MEMORY_SEARCH_LIMIT` 计为碰到上限，其余错误码计为校验错误；响应事件 `data.error` 非空的调用也计为校验错误；没有回答、也没有协议错误的计为未回答；其余为成功。成功结果按「检索结果项」至少取出一项的为非空；成功结果的「提示」含「范围太大」或「加关键词缩小范围」（即装填时出现原因「上限」或「按日放不下」，第 05 章【S9】）计为碰到字数上限。参数 `time` 以「今天」开头的调用计为查「今天」。
- 只读核对：每次检索调用所在响应与同一决策下一条回合请求之间，`event_type` 不是 `model_request`、`model_response` 的事件数。
- 输出 `{"calls", "today_calls", "by_day", "succeeded", "non_empty", "validation_errors", "error_codes", "limit_refusals", "capped_results", "unanswered", "non_model_events_between"}`：`non_empty` 的分母是成功数，`validation_errors` 的分母是调用数，二者是比例；`by_day` 是日期到 `{"calls", "today_calls"}`；`error_codes` 是错误码到次数。

**M6 体量。**
- 层大小：新快照首个请求的 user 消息解析为层数组，按各层的 `"layer"` 名取层，每层的大小是 `len(json.dumps(层, ensure_ascii=False, separators=(",", ":")))`；记忆层另取「可查」「近三天」「今天」三个值各自的紧凑序列化长度，键名为「记忆层·可查」「记忆层·近三天」「记忆层·今天」；另计 `system`（system 消息内容）、`user`（user 消息内容）、`tools`（`tools` 的紧凑序列化）的长度。各项输出统计量。
- `memory_layer_by_observation`：每条观察 `data.memory_layer` 的紧凑序列化长度的统计量。
- `fallback_rewrites`：一条观察的「今天」列表不以同一本地日上一条观察的「今天」列表开头（按条目紧凑序列化逐条比较）的观察数，即兜底（裁定 24）触发次数；当天第一条观察不比较。
- `persona_layer_identical`：新快照首个请求里人设层与第一条新快照的人设层逐字节相同的比例。
- `token_ratio`：相邻两条新快照首个请求（按 `event_seq`）之差做无截距最小二乘，`Δprompt_tokens ≈ Σ w_k·Δc_k`。`prompt_tokens` 取该请求对应响应的 `data.response.usage.prompt_tokens`；`c_k` 是整份请求文字（system 内容、user 内容、`tools` 的紧凑序列化）里六类字符的个数：汉字（U+3400–U+4DBF、U+4E00–U+9FFF、U+F900–U+FAFF）、ASCII 字母、ASCII 数字、空格、其余可打印 ASCII（U+0021–U+007E）、其余字符。用正规方程加部分主元的高斯消元求解；差分对少于 12 对或主元绝对值小于 1e-9 时 `weights` 与 `memory_layer_tokens_per_char` 为 null。`memory_layer_tokens_per_char = Σ w_k·n_k / Σ n_k`，`n_k` 是全部新快照首个请求里记忆层紧凑序列化的六类字符数。输出 `{"pairs", "weights", "memory_layer_tokens_per_char", "reference": 0.638, "reference_range": [0.630, 0.645], "within_range"}`，权重保留四位、比值保留三位，`within_range` 在比值为 null 时为 null。
- 输出 `{"layers", "memory_layer_by_observation", "fallback_rewrites", "persona_layer_identical", "token_ratio"}`。

**M7 缓存。** 读 `quality.json` 的 `model_usage.by_call_class`。`fresh_decision_turn` 一行 `priced_tokens` 的 `cache_hit_input_tokens / (cache_hit_input_tokens + cache_miss_input_tokens)` 为命中率。输出 `{"fresh_decision_turn": {"hit_tokens", "miss_tokens", "value"}, "by_call_class": {调用类: 命中率}}`，`priced_tokens` 为 null 的类命中率为 null。

**M8 费用。** 一律按冻结的峰时价：取 `quality.json` 的 `model_usage.pricing_snapshot.peak`，每个调用类的费用为 `(hit·cache_hit_input + miss·cache_miss_input + out·output) / 1,000,000`，`priced_tokens` 为 null 的类记 0。除以运行日数得每模拟日费用。输出 `{"days", "per_day_usd", "by_call_class": {调用类: 每日美元}, "complete"}`，美元保留六位小数，`complete` 照抄 `model_usage.estimated_cost.complete`。

**M9 护栏。**
- 每天决策数：按决策日期计数；带宽 `[81×0.85, 81×1.15]`；不在带宽内的日子列入 `days_outside_band`。
- 拒绝：`action_rejected` 事件数；上限 `rejection_limit = 2 × 6 × 运行日数 / 5`。
- 回合：每次决策的回合数；`exhausted` 是回合数达到 `frozen_run_manifest.stage_model_identity.max_react_turns` 的决策数；`p99`、`max` 只报告；回合数 ≥6 的决策逐个列出 `{"decision_id", "time", "turns"}`。
- `run_failures`：`run_failed` 事件数。`capacity_compressions`：首个 user 消息的进度层里带 `"type": "condensed"` 记录的回合请求数（容量处理的折叠一步）。容量处理的第 0 步「提前结束本秒续接」（src/worldmock/agents/prompt/prompt_capacity.py:3-4、:93-97）把链前缀换成新快照，不留下自己的记录，在事件里与其他断链（非前缀、链中缺思考）无法区分，不单独计数；同秒链里改用新快照的决策数另报 `chain.fresh_followups`。
- 同秒链：`same_second_followups` 是时刻与上一次决策相同的决策数；`continuations` 是其中首个请求为续接的数；`fresh_followups = same_second_followups − continuations`，只报告。对每次续接决策，取首个请求最后一条 `role == "tool"` 消息内容里的 `changes`；`changes.memory.added`（缺失视为空列表）接在上一次决策观察的「今天」之后必须等于本决策观察的「今天」（逐条紧凑序列化相等），不等记一次 `append_violations`；`changes` 含 `relationships` 或 `preferences` 键记一次 `persona_deltas`。
- 刚了结又重新开始：一项活动的终态 `activity_progress` 落在上一次决策的观察事件与本决策的观察事件之间，本决策受理的某项活动与它目标相同，即列出 `{"decision_id", "time", "target"}`。目标：用内容 app 为 `["app", app_id]`；在地点做的事为 `["content", 系列标题或 content_id]`；购买为 `["purchase", 商品身份升序]`；查询为 `["query", app_id, 目标的类别或商品身份升序]`；出行为 `["travel", destination_id]`；睡眠为 `["sleep"]`。
- 输出 `{"decisions_per_day", "decision_band", "days_outside_band", "rejections", "rejection_limit", "turns", "run_failures", "capacity_compressions", "chain", "restarted"}`，`turns` 为 `{"max_react_turns", "p99", "max", "exhausted", "six_or_more"}`，`chain` 为 `{"same_second_followups", "continuations", "fresh_followups", "append_violations", "persona_deltas"}`。

**M10 回顾。**
- 回顾事件：`environment_event` 且 `data.kind == "memory_digest_generated"`（形状见第 04 章「日、月、年回顾」）；回顾请求：`data.purpose == "memory_digest"` 的 `model_request`，`call_id` 形如 `memory-digest:{agent}:{tier}:{period}:attempt:{n}`。
- `calls_by_day` 是本地日期（ISO，升序，只列有回顾请求的日子）到 `{"day": n, "month": n, "year": n}`，按请求的 `call_id` 里的 `tier` 计数，三键都在。`periods` 恒为 `{"day": n, "month": n, "year": n}`，回顾事件按 `tier` 计数，三键都在，没有事件的级别为 0。`expected` 恒为 `{"month": n, "year": n}`：月回顾为序号 ≥2 的运行日里「日」为 1 的日子数，年回顾为其中「月」也为 1 的日子数。`retries`：每个 `(tier, period)` 的请求数减一之和。
- `fallback`：比例只数日回顾：分母是 `tier == "day"` 的回顾事件里非空或在 `fallback` 里的「类·日」数，分子是其中在 `fallback` 里的。比例对象另加键 `"cells"`，值为全部级别落到 fallback 的 `[{"tier", "period", "category"}]`，按 tier（day、month、year）、period、类别（food、work、life）次序。
- `guard_discards`：读回顾的 `model_response` 中 `data.phase == "response"` 且 `data.error` 非空的（`phase == "failure"` 的传输失败证据不计）。计数单位是「一次响应里的一类」：同一响应、同一类别键被同一守卫报了几条问题都只计一次。分类按第 04 章「输出校验」：
  1. `data.error` 等于 `response must contain exactly one choice`、`response must contain exactly one submit_memory_digest tool call`、`the only tool call must be submit_memory_digest`、`submit_memory_digest arguments must be a JSON string` 之一时计入 `response`；等于 `memory digest output was truncated at max_tokens` 时计入 `truncated`；这两项按响应计一次。
  2. 否则 `data.error` 不以 `digests.` 开头时，是 `schema_feedback(error, "arguments")` 给出的参数结构错误，计入 `schema`，按响应计一次。
  3. 否则用 `re.split(r"; (?=digests\.)", error)` 切成逐条问题（守卫文字里的「; the {tier} digest limit」「; missing:」后面不接 `digests.`，不会被切开）。每条问题按第 4 项的次序逐个模板 `re.fullmatch`，第一个匹配的模板就是它的守卫；类别键取 `re.match(r"digests\.(food|work|life)", 问题)` 的组。模板由第 04 章的问题文字转成：`{key}` 换成 `(food|work|life)`，`{tier}` 换成 `(day|month|year)`，其余 `{…}` 占位换成 `(.+)`，其余字符经 `re.escape` 按字面。
  4. 模板次序与守卫名：`text_without_input`（`digests.{key} must be null: the input has no {key} records`）、`null_with_input`（`digests.{key} must not be null: the input has {key} records`），然后是第 04 章守卫表的十三行，依次 `text_empty`、`text_length`、`chinese`、`hex_identity`、`refs_empty`、`refs_category`、`names`、`food_verified_names`、`food_coverage`、`cooking_sentence`、`numbers`、`numbers_same_record`、`count_lines`。`names` 排在 `numbers` 之前，所以「the name …」的问题不会被 `numbers` 的 `(.+)` 收走。
  5. 没有任何模板匹配的问题计入 `unrecognized`，按问题条数计，并写英文错误日志 `Memory digest error {call_id} matches no guard`。
  6. 输出是守卫名到次数，十九个键按 `response`、`truncated`、`schema`、上面第 4 项的十五个、`unrecognized` 的次序全部列出，没有的为 0。
- `cited`：非空的日回顾格中，被引用的比例。被引用指：某次决策的观察「近三天」里有这一格（同类、同日期），或它的 `search_memory` 成功结果的检索结果项里有这一格，并且这段回顾正文有一个 8 字窗口出现在该决策的 `T_d` 里。
- `view_coverage`：读每个日回顾 `attempt:1` 请求里第 04 章规定的、按类别分开的输入条目，各类「自述」里以「看法·」开头的项，「看法·」与第一个「：」之间是对象；每一「类·日」的对象去重，分母是对象数之和，分子是出现在那一格回顾正文里的对象数之和，正文为 null 的格对象都计为未写到。
- `statistics_copy_violations`：月、年回顾每类正文里每一处匹配 `\d+ ?次` 的文字，若不是该类输入统计行（第 04 章规定的形状）中某一行在正文里出现时的结尾，记一次。
- `day_digest_problems`：有决策的回顾日中没有日回顾事件的日期数，加上同一 `(tier, period)` 有多于一条回顾事件的组数；`missing_day_digests`、`duplicate_digests` 分别列出。
- `retired_calls`：`quality.json` `by_call_class` 里 `consolidation` 与 `reflection` 两行 `request_events` 之和。
- `repetition`、`identity_residue`：调用行为工具的 `digest_repetition(events)`、`digest_identity_residue(events)`（见下文「四」）。
- 输出 `{"calls_by_day", "periods", "expected", "retries", "fallback", "guard_discards", "cited", "view_coverage", "statistics_copy_violations", "day_digest_problems", "missing_day_digests", "duplicate_digests", "retired_calls", "repetition", "identity_residue"}`。

**M11 回顾保真。** 只用事件、`purchases.jsonl` 和冻结目录独立重算，不读回顾的输入，不调用运行时守卫。按回顾日 `D` 计。
- `F(D)`：本地日期为 `D` 的 `goods_received` 世界事件里食品商品的名字，加上结束日期为 `D` 的做饭活动所用食材包的名字（「家里原有的食材」不计）。
- ① 覆盖：分母是满足下面之一的回顾日：`F(D)` 非空；或 `D` 的日回顾事件里 `digests.food` 非空、或 `"food"` 在它的 `fallback` 里（当天有饮食核验）。分子是其中饮食日回顾非空、且 `F(D)` 每个名字都是正文子串的（`F(D)` 为空时只要饮食日回顾非空）。`food_fallback` 的分母相同，分子是其中 `"food"` 在 `fallback` 里的；`coverage_plus_fallback` 是比例：分子为 `coverage` 与 `food_fallback` 两者分子之和，分母为二者共同的分母（两个各自舍入的值相加会得到 0.9999 或 1.0001，所以不相加）。
- ② 做饭错配：饮食日回顾每一句含「做饭」的句子里认出的每个食材包名，不是结束日期为 `D` 的做饭活动所用食材包的，记一次。
- ③ 餐次错配：每一句含「早饭」「午饭」「晚饭」之一的句子里认出的每个食品商品名，若该商品在 `D` 的 `goods_received` 时刻没有一个落在句中任一餐次的时段里，记一次。
- ④ 外来菜名：饮食日回顾正文里认出的每个菜名，若不是 `N(D)` 中任一名字的子串，记一次。`N(D)` 是 `D` 当天 `purchases.jsonl` 购买行（`fact_kind == "purchase"`）的商品名、`goods_received` 的商品名，以及结束日期为 `D` 的做饭活动所用食材包名。
- 输出 `{"coverage", "food_fallback", "coverage_plus_fallback", "cooking_mismatches", "meal_mismatches", "foreign_dishes", "days"}`，`days` 列出 ① 分母里的回顾日，每项 `{"date", "expected", "missing", "fallback", "cooking_mismatch", "meal_mismatch", "foreign"}`。

**M12 表态食品回购率。** 只用事件、`purchases.jsonl` 和冻结目录。
- 购买：`fact_kind == "purchase"`、商品是食品商品或食材包、本地日期不是第一个运行日的行，一行算一次。
- 点名：看法对象 `s = norm(about)`、商品名 `g = norm(name)`。`len(s) < 3` 不点名；全部目录商品名规范化后，等于 `s`、或在 `len(s) ≥ 4` 时包含 `s` 或被 `s` 包含的，多于 2 件时不点名；否则 `s == g`，或 `len(s) ≥ 4` 且 `s` 含 `g` 或 `g` 含 `s`，即点名。
- 分子：被日期早于购买日期的已了结 `preference` 看法点名过的购买。
- 卡片：第一条新快照首个请求的人设层紧凑序列化，规范化后为 `card`；`lcs(g, card) ≥ min(4, len(g))` 时商品在卡片上。年龄：购买日期减去点名它的最晚一条看法的日期，1–3 天为 `"1-3"`，4 天及以上为 `"4+"`。
- 看法依赖（旁证）：决策 `d` 依赖更早日子的看法，是指存在一条日期早于 `D_d` 的已了结看法（`preference` 或 `relationship`），满足下面之一：点名——`norm(about)` 长度 ≥2，是 `norm(T_d)` 的子串，不是 `norm(R_d)` 的子串；回声——看法 `text` 有一个 8 字窗口出现在 `T_d` 里、不出现在 `R_d` 里。
- 输出 `{"purchases", "endorsed", "on_card", "not_on_card", "age", "rows", "earlier_view_reliance"}`：`endorsed` 是比例（分母为购买数）；`on_card`、`not_on_card` 分别是被点名且在、不在卡片上的购买占购买数的比例；`age` 为 `{"1-3": n, "4+": n}`；`rows` 每项 `{"time", "goods", "on_card", "endorsed_by": [{"about", "date"}]}`；`earlier_view_reliance` 为 `{"decisions", "relying", "value", "rows"}`，`rows` 每项 `{"decision_id", "time", "views": [{"about", "date", "by"}]}`，`by` 取 `mention`、`echo`。

### 四　行为工具的两项回顾报告

`tools/analysis/behavior/metrics.py` 的 `_group_report` 在 `reports` 末尾加两项。两项只报告，不进门禁，结构标识 `behavior-metrics-4` 不变（与模块说明里 workload、routine_deviation「只报告，报告结构标识不变」同一规则，metrics.py:12-13）。
- `memory_digest_repetition`（`digest_repetition(events)`）：同一人物、同一类别键（`food`、`work`、`life`），相邻两个日历日都有非空日回顾时成一对；`_bigram_jaccard(前一天, 后一天) ≥ 0.6` 的为相似。输出 `{"pairs", "similar_pairs", "ratio"}`，`ratio = round(similar_pairs / pairs, 6)`，没有对时为 null。
- `memory_digest_identity_residue`（`digest_identity_residue(events)`）：全部各级回顾每类正文小写后命中 `MEMORY_IDENTITY_MARKERS` 的标记。输出 `{"hits", "matches"}`，`hits` 是命中的「类·期」数，`matches` 每项 `{"agent_id", "tier", "period", "category", "markers"}`，标记按字典序。

### 五　报告结构、检查项与对照表

报告的顶层键依次为 `schema_version`（`"memory-pilot-1"`）、`run_id`、`window`（照抄导出清单的 `window`）、`agent_id`、`days`（ISO 日期列表）、`metrics`（键 `M1`…`M12`，依次）、`checks`、`digest_review`。序列化为 `json.dumps(report, ensure_ascii=False, indent=2) + "\n"`；同一导出两次计算逐字节相同。

`checks` 每项 `{"name", "path", "comparison", "threshold", "value", "pass"}`，`value` 是报告里 `path` 处的值，为 null 时 `pass` 为 null。阈值全部取自提案对最终形态写出的数值；其中 `progress_layer_median`、`progress_layer_p90` 两项在提案 P1 验收里标为「估计值，P0 校准」，本交付不在旧运行上校准（见「规格阶段的决定」），两项照表中数值判定：

| name | path | 比较 | 阈值 | 提案出处 |
| --- | --- | --- | --- | --- |
| `cooking_lines_name_the_kit` | `M2.named.value` | `==` | 1.0 | M2「要求 100%」；P1「凡是有重置的做饭结束句，都带食材包名」 |
| `yesterday_food_cell_visible` | `M3.periods.all.value` | `==` | 1.0 | M3「要求 100%」 |
| `search_validation_errors` | `M5.validation_errors.value` | `<=` | 0.05 | P1 试点 |
| `search_non_empty` | `M5.non_empty.value` | `>=` | 0.70 | P1 试点 |
| `search_is_read_only` | `M5.non_model_events_between` | `==` | 0 | P1 验收「检索与下一次请求之间除了模型事件没有别的事件」 |
| `memory_layer_budget` | `M6.memory_layer_by_observation.max` | `<=` | 14000 | P1 验收 |
| `progress_layer_median` | `M6.layers.进度层.median` | `<=` | 3000 | P1 验收 |
| `progress_layer_p90` | `M6.layers.进度层.p90` | `<=` | 7000 | P1 验收 |
| `user_message_median` | `M6.layers.user.median` | `<=` | 22000 | P2 验收 |
| `persona_layer_stable` | `M6.persona_layer_identical.value` | `==` | 1.0 | P2 验收 |
| `fresh_turn_cache_hit` | `M7.fresh_decision_turn.value` | `>=` | 0.55 | M7、P2 验收 |
| `cost_per_day` | `M8.per_day_usd` | `<=` | 0.22 | P2 验收 |
| `decisions_per_day_band` | `M9.days_outside_band` 的长度 | `==` | 0 | M9 |
| `rejections` | `M9.rejections` | `<=` | `M9.rejection_limit` | M9 |
| `turns_exhausted` | `M9.turns.exhausted` | `==` | 0 | M9 |
| `run_failures` | `M9.run_failures` | `==` | 0 | M9 |
| `capacity_compressions` | `M9.capacity_compressions` | `==` | 0 | M9 |
| `no_restarted_activities` | `M9.restarted` 的长度 | `==` | 0 | P1 验收「刚了结的活动没有被重复开始」；M9 |
| `chain_append_only` | `M9.chain.append_violations` | `==` | 0 | M9、P1 验收 |
| `chain_without_persona_delta` | `M9.chain.persona_deltas` | `==` | 0 | P2 验收 |
| `digest_fallback_share` | `M10.fallback.value` | `<=` | 0.10 | P1 验收 |
| `one_day_digest_per_day` | `M10.day_digest_problems` | `==` | 0 | P1 验收 |
| `retired_calls` | `M10.retired_calls` | `==` | 0 | P1 验收 |
| `month_digests` | `M10.periods.month` | `==` | `M10.expected.month` | P3 验收 |
| `statistics_copied` | `M10.statistics_copy_violations` | `==` | 0 | P3 验收 |
| `food_coverage_partition` | `M11.coverage_plus_fallback.value` | `==` | 1.0 | M11 ① |
| `cooking_mismatch` | `M11.cooking_mismatches` | `==` | 0 | M11 ② |

`days_outside_band`、`restarted` 两项的 `value` 写列表长度；`rejections`、`month_digests` 两项的 `threshold` 写报告里对应的数。比较浮点数时按报告里已经舍入的值比较。

`digest_review` 是试点报告附的「每天的回顾和当天核验的对照表」：每条日回顾事件按 `period` 升序，每个类别按饮食、工作、生活一行 `{"date", "category", "digest", "status", "verified"}`。`status` 取 `written`（正文非空）、`unsorted`（null 且在 `fallback` 里）、`no_records`（null 且不在）；`verified` 是该日回顾 `attempt:1` 请求里这一类输入条目的全部「核验」字符串，按输入次序，`no_records` 时为空列表。

### 六　`memory_categories.py`

1. **输入**是一个环境包目录（含 `catalog/`、`persona.toml`、`config.toml`），用 `worldmock.configuration.inputs.environment.load_environment(package, metadata_loader=_offline_metadata)` 加载；`_offline_metadata` 返回 `ModelMetadata(requested_model=model.requested_model, metadata_url=model.metadata_url, context_window=model.context_window, max_output_tokens=model.context_window)`，不访问模型服务。加载失败时照原错误退出，返回 1。
2. **推导。** `book = category_book(inputs)`，`rows = row_categories(book)`（第 03 章第 9 节「静态推导表」，`worldmock.simulation_engine.observation.memory.categories`）；本工具不写任何类别规则。**班次地点类型** `workplace_kind` 取 `book.workplace_kind`（没有工作安排时为 null）。
3. **每一行**按 `rows` 的次序（内容行按 `content_id` 升序，然后 app 行按 `app_id` 升序）输出 `{"row", "id", "name", "location_kinds", "app_ids", "category", "rule", "reason"}`：`row`、`id`、`category`、`rule` 照抄 `RowCategory` 的 `kind`、`row_id`、`category`、`rule`；`name` 取内容的 `title` 或 app 的 `name`；内容行的 `location_kinds`、`app_ids` 照抄目录行（列表），app 行的这两项为空列表。理由按 (行种类, 规则, 类别) 取下面的固定文字，`{workplace_kind}` 换成班次地点类型：

   | 行种类 | 规则 | 类别 | 理由 |
   | --- | --- | --- | --- |
   | 内容或 app | 0 | 任一 | 目录行写了 memory_category |
   | 内容 | 3 | 饮食 | 属于食品单元：同一单元有一集写了 completion_effects.food，或凭的是食品状态 |
   | 内容 | 5 | 工作 | 在班次地点类型 {workplace_kind}：工作日在班次地点开始时归工作，休息日归生活 |
   | 内容 | 7 | 生活 | 其余归生活；工作日在岗时段内（不含休息时段）在家以外的地点开始时归工作（规则 5） |
   | 内容 | 6 | 工作 | 前提要求班次地点类型 {workplace_kind} |
   | 内容 | 6 | 生活 | app 内容不要求班次地点，归生活 |
   | app | 6 | 工作 | 有一条内容的前提要求班次地点类型 {workplace_kind} |
   | app | 6 | 生活 | 没有内容要求班次地点，归生活 |

   这八种组合恰是第 03 章静态推导表能给出的全部组合（地点内容 0、3、5、7，app 内容 0、6，app 行 0、6）；理由表是模块常量 `ROW_REASONS`，键为 `(row, rule, category)`，规则 0 的三个类别各占一键。
4. **输出** `{"schema_version": "memory-categories-1", "environment_id", "environment_version", "workplace_kind", "rows"}`。`environment_id`、`environment_version` 照抄包的目录清单。序列化同 `memory_pilot.py`。

### 七　运行比对

`scripts/compare_runs.py` 的 `EVENT_REFERENCES`（:74-89）加两条，种类都是 `"event"`：`"data.digests.*.source_event_ids[]"`、`"data.digests.*.cited_event_ids[]"`。前缀树对 `*` 合并字面键（:569-582），某一类为 null 时 `_map_node` 原样返回 null，照常逐值比较。不加记忆层的路径：记忆层「今天」不带事件身份，模拟时刻本来就不在屏蔽之列（`EVENT_MASKS`，:44）。其余规则、屏蔽和报告形状不变。

### 八　查看器

**层序与分段。** `tools/run-viewer-context.js` 的 `USER_LAYERS`（:7-8）改为第 01 章「记忆层：形状、预算、兜底与层序」规定的最终层序：`[['persona','人设层'], ['memory','记忆层'], ['todo','待办层'], ['tool','工具层'], ['environment','环境层'], ['progress','进度层']]`。`splitLayers` 仍按这张表的次序逐片核对层名，层序不符即按未解析处理。

**规则说明**（`rules`，:11-147）逐项改成下面的文字，其余条目不变：

| 键 | 改动 |
| --- | --- |
| `persona` | `fill`：「宿主从人物卡片的结构化字段与姓名构建身份、作息、习惯、口味与关系，整次运行逐字节相同。」第三步换成 `['不含看法', '收尾时写下的看法不进人设层：当天在记忆层「今天」以「看法·对象：」出现，过了零点只在回顾和 search_memory 里。']` |
| `memory` | `source`：`'src/worldmock/simulation_engine/observation/layers/memory_layer.py · memory_view；src/worldmock/simulation_engine/observation/memory/'`。`fill`：「环境从已提交事件与回顾事件派生「可查」「近三天」「今天」三部分，整层合计不超过 14,000 字。」`steps` 依次：`['今天', '今天的每次决策一条，提交后从下一次决策起可见，同一时刻接着的决策经 changes.memory.added 收到；每条「核验」与「自述」合计至多 120 字，全文用 search_memory 查「今天」。']`、`['近三天', '过了零点，模型把前一天每类的条目整理成一段回顾；饮食和生活取最近三天，工作取最近三个有工作的日子；整理不出时这一格写「未整理」，那天没有这类事时这一格省略。']`、`['可查', '写出 search_memory 能查到的最早日期；更早的日回顾、月回顾和年回顾都要检索。']`、`['兜底', '追加一条后超过 14,000 字时一次回落到 12,000 字以内：先把只交了播报的条目的自述换成「…」，再让其余条目只留核验，最后按小时合成计数行。']`、`['核验区分', '「核验」是环境核验的事实；「自述」和「回顾」是人物自己写下或整理的，未经核验。']` |
| `todo` | `fill` 在「备注与尚未提醒的钟点；」之后插入「在途订单（orders）列出下单后还没送达或取走的订单；」。`steps` 在「个人事务」之后插入 `['在途订单', '至多六行，多出的合成一行「另有 N 笔在途」。']` |
| `progress` | `source`：`'src/worldmock/simulation_engine/observation/layers/progress_layer.py · progress_view'`。`fill`：「此刻还没了结的事：进行中或待收尾的本人活动带开始记录和最新结果，上次决策之后新到的结果、配送、app 推送与提醒，这段停留或路程里已经做完的查询及其答案，以及今天被拒绝的请求，按发生时刻排列。」`steps` 依次：`['已发布界', '下界是上一次决策的提交，且不早于本地零点；在它之前已经了结的事在记忆层「今天」里。']`、`['带入记录', '进行中或待收尾的本人活动和这段停留里做完的查询照样带入；出发后查询答案离开进度层，进条目全文的「当时查到」。']`、`['事务记录', '事务提醒到点（errand_reminder）与环境确认办成（errand_completed）两种记录只写事务名称，由人物可见的事务事件派生；办成同时在记忆层「今天」的核验里写成「办成「事务名」」。']`、`['容量折叠', '超过容量时把连续的未受保护记录折叠为 condensed；受保护记录不变。']`；删去 `['self_note', …]` |
| `tools` | `fill`：「六个世界请求工具（两个开始工具、三个活动控制工具与计划修订）、一个回看工具（recall_task_summary）与一个记忆检索工具（search_memory），共八个，严格参数结构，整次运行字节相同。」`steps` 的「最后回合」改为「只剩最后一个回合时与输出截断后的那次重试时去掉全部只读工具（回看任务与 search_memory），只留世界请求工具。」，其后插入 `['检索上限', '一次决策至多查三次记忆，第四次返回 MEMORY_SEARCH_LIMIT。']` |
| `message` | `fill`：「同一决策内的 assistant 调用、search_memory 与回看任务的结果和协议纠错消息按实际发送顺序保留。」第一步换成 `['查询结果', '查询结果作为一条紧凑 JSON 工具消息追加；search_memory 查到的往日是回顾段落，「当时查到」的价格和库存以当时为准。']` |
| `continuation` | `source`：`'src/worldmock/simulation_engine/decisions/continuation.py · same_second_chain；src/worldmock/agents/protocol/life_continuation.py · continuation_messages'`。`fill` 末尾的「。」前接「；changes.memory.added 是上一决策的今天条目」。`steps` 末尾加 `['记忆追加', '链里记忆层只在「今天」末尾追加；兜底改写了早先的条目时这一次改用新快照。']` |
| `reflection` | 删除，换成键 `digest`：`label` 「日、月、年回顾」；`source` `'src/worldmock/simulation_engine/evidence/memory_digest.py；src/worldmock/runtime/lifecycle/runner.py · settle_pending_memory_digests'`；`fill` 「零点由模型把前一天每类的今天条目整理成日回顾，每类至多 200 字；每月第一个零点把上个月的日回顾整理成月回顾，每类至多 800 字；每年第一个零点把上一年的月回顾整理成年回顾，每类至多 2000 字。」；`steps` 依次 `['顺序', '零点推进之后依次生成日回顾、月回顾、年回顾，然后才做零点计划；运行到终点时不生成。']`、`['调用', '每一级每次一个调用，关闭思考，只给严格工具 submit_memory_digest，三类分开写，refs 写所依据的编号。']`、`['校验重试', '输出被截断、参数不合格式，或正文、依据、名称、饮食核验名称、饮食覆盖、做饭那一句、数字与次数照抄各守卫不通过时附英文错误重试，至多 reflection_validation_max_retries 次；用尽时没通过的类记为 null 并写进 fallback，运行不失败。']`、`['记录', '回顾记成 memory_digest_generated 世界事件；它是整理出来的回忆，环境从不据此结算。']` |
| `memory_view` | 第一步改为 `['每步重写', '根据当前身份与事实窗口重新生成视图；主观归纳不再更新。']` |
| `capacity` | `source` 改为 `'src/worldmock/agents/prompt/prompt_capacity.py · fit_request'`。「再浓缩进度层」改为「把最旧一段连续的未受保护记录折叠为 condensed，重复到阈值以下。」；「保留当前」改为「受保护记录，以及人设层、记忆层、待办层、工具层与环境层都不被压缩。」 |

**进度层下钻。** `progressItems`（:375-385）不再读 `self_note`：标签为 `${record.occurred_at} · ${record.type}` 加「 · 主体」（主体的取法不变），正文为 `JSON.stringify(record, null, 2)`。记忆层不加下钻，整层原文照常可看。

**上下文页的说明。** `tools/run-viewer-context-view.js` 的 `content`（:192）把「；self_note 未经核验。」改为「；「自述」与「回顾」未经核验。」；`bindLayer`（:129）的说明删去「self_note 条目标明未经核验」。

**录制样例。** `tests/fixtures/viewer-context.json` 的 `action`、`continuation` 两项用真实宿主重录：在最终代码上运行 `tests/system/lifecycle/test_run_viewer_recording.py` 的 `_recorded_viewer_requests` 流程（修订计划，再同秒续接打开哔哩哔哩），把得到的两个请求按 `json.dumps({"action": …, "continuation": …}, ensure_ascii=False, indent=2) + "\n"` 写入文件；写入用的临时脚本放 `.work/`，不入库。重录后样例的首个 user 按最终层序排列，`continuation` 最后一条 tool 消息的 `changes.memory.added` 恰为修订计划那次决策的一条条目，工具为八个。

### 九　试点规程

试点要真实调用模型，本次交付不执行；规程固定如下，执行后另写开发记录。

1. **输入。** 都从按格式 6 重建后的包 0001（第 08 章「退役项、食材包句与环境包」）复制，只改下表两处：

   | 试点 | `config.toml` 的 `start_time` → `stop_time` | `persona.toml` |
   | --- | --- | --- |
   | `lm-variety-1`、`lm-variety-2` | 2026-10-08T00:00:00+08:00 → 2026-10-13T00:00:00+08:00 | `food_preferences` 末尾加「每顿饭都愿意换着尝不同的菜」 |
   | `lm-baseline-1`、`lm-baseline-2` | 同上 | 不改 |
   | `lm-variety-10day` | 2026-10-08T00:00:00+08:00 → 2026-10-18T00:00:00+08:00 | 同 `lm-variety-1` |
   | `lm-variety-cross-month` | 2026-09-28T00:00:00+08:00 → 2026-10-03T00:00:00+08:00 | 同 `lm-variety-1` |

   输入目录放在 WorldMock-Dataset 的 `.work/layered-memory/pilot-inputs/<试点>/`。
2. **运行。** 在 WorldMock-Dataset 仓库根逐个执行 `python -m tools.execute run --environment .work/layered-memory/pilot-inputs/<试点> --output execution/<UTC 时刻>-<试点>`，得到运行、认证导出与检查。
3. **计算。** 在 WorldMock 仓库根执行 `uv run python -m tools.analysis.memory_pilot --export <执行目录>/export --output .work/layered-memory/pilot/<试点>.json`。
4. **判定。** 每份报告的 `checks` 全部 `pass` 为 true，或 `pass` 为 null（该项在这次试点里没有分母）；`lm-variety-cross-month` 的 `month_digests` 一项（期望值为 1）即提案 P3 的「跨月试点里恰有一条月回顾事件」。任何一项为 false 即判定不通过（提案 M9「有一项回退就不上线」）。M1、M2 的对不上次数、M4、M5 的次数、M9 的 `fresh_followups`、M10 的各守卫丢弃次数（提案 P3「各道守卫的丢弃次数要报告」）、引用率、重复率、残留与看法对象覆盖率、M11 ③④、M12 及其旁证只报告。头三天与第 4 天以后的比例分开写；`lm-variety-10day` 只用 `days_4_plus` 下结论。与 V、B 比较时注明两者环境版本不同（裁定 6 带来的 `environment_version` 升号）。
5. **报告。** 写 `docs/dev/layered-memory/PILOT-<执行日期>.md`：各试点的检查结果、只报告各项与「行为」一第 2 条所列 V、B 基线的并列（M1 写「无 V、B 基线」；M2 的对不上次数写明「提案 2 次，按本口径 10-11 那次不计入」）、`digest_review` 对照表；抽查写明抽查人、抽查的「类·日」数和判定标准，由模型核对的写「模型核对」，不写人工抽查。报告 JSON 留在 `.work/`，不入库。
6. **费用估计。** 共 35 个模拟日，按提案 P2 的全口径每模拟日约 $0.207（已含零点计划 $0.0044 与回顾 $0.0048，研究目录 out3/reports/econ.md「All-in per day」）计，合计约 $7.2。

## 接口与文件

**新增 `tools/analysis/memory_pilot.py`。** 模块只导入标准库、`tools.analysis.errors`、`tools.analysis.behavior.metrics`、`worldmock.recording_engine.validation.validation`（`validate_export`）、`worldmock.foundation.errors`（`WorldMockError`）与 `worldmock.state` 下的共享取值（`MEMORY_DIGEST_PURPOSE`、`MEMORY_CATEGORIES`）。`tools.analysis.behavior.metrics` 自身导入 `worldmock.recording_engine`、`worldmock.resource_engine`、`worldmock.exchange_engine` 的模块，这些都不含回顾守卫；守卫在 `worldmock.simulation_engine`（第 04 章）。导入本模块之后，`sys.modules` 里没有以 `worldmock.simulation_engine`、`worldmock.agents`、`worldmock.runtime`、`custom` 开头的模块，M11 因此在结构上独立于运行时守卫（由测试 `test_memory_pilot_imports_no_runtime_code` 在子进程里核对）。

```python
SCHEMA_VERSION = "memory-pilot-1"
MEAL_PERIODS = {"早饭": ("04:00", "10:30"), "午饭": ("10:30", "14:30"), "晚饭": ("16:30", "22:00")}
ECHO_LENGTH = 8
DECISION_BASELINE_PER_DAY = 81
DECISION_BAND = 0.15
REJECTION_BASELINE = 6
REJECTION_BASELINE_DAYS = 5
REJECTION_FACTOR = 2
SIX_TURNS = 6
TOKEN_RATIO_REFERENCE = 0.638
TOKEN_RATIO_RANGE = (0.630, 0.645)
MIN_TOKEN_PAIRS = 12
DIGEST_KEYS = {"food": "饮食", "work": "工作", "life": "生活"}

@dataclass(frozen=True)
class PilotExport:
    """@brief 一份认证导出里试点指标要读的全部事实（只读）。"""
    run_id: str
    agent_id: int
    zone: ZoneInfo
    days: tuple[date, ...]
    events: tuple[dict, ...]
    purchases: tuple[dict, ...]
    quality: dict
    manifest: dict
    goods: Mapping[str, dict]
    contents: Mapping[str, dict]
    food_states: frozenset[str]
    kits: frozenset[str]
    lexicon: tuple[str, ...]

@dataclass(frozen=True)
class Decision:
    """@brief 一次决策的事件分组。"""
    decision_id: str
    occurred_at: int
    day: date
    observation: dict | None
    requests: tuple[dict, ...]
    responses: tuple[dict, ...]
    actions: tuple[dict, ...]
    outcomes: tuple[dict, ...]

def load_export(directory: Path) -> PilotExport: ...
def decisions(export: PilotExport) -> list[Decision]: ...
def pilot_report(directory: Path) -> dict: ...
def m1_past_reference(export: PilotExport, rows: list[Decision]) -> dict: ...
def m2_dinner_naming(export: PilotExport, rows: list[Decision]) -> dict: ...
def m3_past_visibility(export: PilotExport) -> dict: ...
def m4_lunch_repeats(export: PilotExport) -> dict: ...
def m5_searches(export: PilotExport, rows: list[Decision]) -> dict: ...
def m6_volume(export: PilotExport, rows: list[Decision]) -> dict: ...
def m7_cache(export: PilotExport) -> dict: ...
def m8_cost(export: PilotExport) -> dict: ...
def m9_guardrails(export: PilotExport, rows: list[Decision]) -> dict: ...
def m10_digests(export: PilotExport, rows: list[Decision]) -> dict: ...
def m11_digest_fidelity(export: PilotExport) -> dict: ...
def m12_endorsed_repurchase(export: PilotExport, rows: list[Decision]) -> dict: ...
def pilot_checks(metrics: dict) -> list[dict]: ...
def digest_review(export: PilotExport) -> list[dict]: ...
def main(argv: list[str] | None = None) -> int: ...
```

命令行：`python -m tools.analysis.memory_pilot --export DIR [--output FILE]`。没有 `--output` 时把报告写到标准输出；给了时文件必须不存在（否则 `EvaluationError("memory_pilot_input", "Output file already exists: {path}")`），写入后返回 0。私有辅助函数（比例、统计量、规范化、认菜名、八字回声、最小二乘、回顾输入解析等）各自不超过 50 行；回顾输入按第 04 章规定的形状取「饮食」「工作」「生活」三类条目和统计行，取不到时抛 `EvaluationError("memory_pilot_input", "Memory digest request {call_id} does not hold the category entries")`。

**新增 `tools/analysis/memory_categories.py`。**

```python
SCHEMA_VERSION = "memory-categories-1"
#: (行种类, 规则编号, 类别) → 理由（「行为」六第 3 条的表）。
ROW_REASONS: Mapping[tuple[str, int, str], str]
def category_table(package: Path) -> dict: ...
def main(argv: list[str] | None = None) -> int: ...
```

`category_table` 加载包、调用 `category_book` 与 `row_categories`（`worldmock.simulation_engine.observation.memory.categories`，第 03 章），按「行为」六组装输出；模块里没有任何类别判定。命令行：`python -m tools.analysis.memory_categories --package DIR [--output FILE]`，输出规则同 `memory_pilot.py`；加载失败时 `load_environment` 抛出的 `WorldMockError` 已写错误日志，入口捕获它与 `EvaluationError` 后返回 1。

**改 `tools/analysis/behavior/metrics.py`。** 新增公开函数 `digest_repetition(events: list[dict[str, Any]]) -> dict[str, Any]` 与 `digest_identity_residue(events: list[dict[str, Any]]) -> dict[str, Any]`，复用 `_bigram_jaccard`（:592）与 `MEMORY_IDENTITY_MARKERS`（:39）；`_group_report`（:97-127）的 `reports` 末尾加 `"memory_digest_repetition"`、`"memory_digest_identity_residue"` 两键，`schema_version` 仍为 `"behavior-metrics-4"`；模块说明在 :12-13 那一句之后加一句「两项回顾报告 memory_digest_repetition 与 memory_digest_identity_residue 只读事件，只报告，报告结构标识不变。」

**改 `scripts/compare_runs.py`。** `EVENT_REFERENCES` 加两条路径（见「行为」七）。

**改查看器。** `tools/run-viewer-context.js`（`USER_LAYERS`、`rules`、`progressItems`）、`tools/run-viewer-context-view.js`（`content`、`bindLayer` 的说明）。

**重录 `tests/fixtures/viewer-context.json`。**

**WorldMock-Dataset。** `tests/test_white_collar_package.py` 加一个测试（见「测试」），不加夹具；包 0001 推导表的夹具 `tests/fixtures/memory-categories-0001.json` 由第 03 章新建。

## 设计文档

- **`docs/design/07-evaluation.md`。**
  - 「职责与边界」第一段改为同时列出 `tools/analysis/behavior/metrics.py`、`workday.py`、`memory_pilot.py` 与 `memory_categories.py`：前三者只读已记录事实，`memory_categories.py` 只读一个环境包的冻结输入；四者都不推进世界、不写入人物、不调用模型服务。
  - 「行为报告」写明两项回顾报告的定义（本章「行为」四），只报告，不进门禁，`behavior-metrics-4` 标识不变。
  - 新增一节「分层记忆试点指标」：输入先经 `validate_export` 认证，只认当前代码认证得过的单人导出；共用口径表；M1–M12 的计算；报告结构与 `checks` 阈值表；`digest_review`。内容即本章「行为」一至三、五，此后以这一节为准。
  - 新增一节「记忆类别表」：`memory_categories.py` 的输入、班次地点类型、内容行与 app 行的写法和输出结构（本章「行为」六）；类别规则本身指向第 03 章更新后的类别规则所在模块，不在这里重复。
- **`docs/design/05-recording-engine.md`。**
  - 「运行比对」的「身份」（:727）末尾加：「回顾事件 `memory_digest_generated` 的 `data.digests.*.source_event_ids[]` 与 `data.digests.*.cited_event_ids[]` 同样映射到所指事件的序号；某一类为 null 时照常比较 null。」
  - 「壁钟」（:731）的末句改为：「data 里的模拟时刻是事实，照常比较，例如记忆层「今天」各条的「时刻」。」
- **`docs/design/01-architecture.md`** :268 改为：「查看器录制样例 `tests/fixtures/viewer-context.json` 逐字记着系统层、工具定义、用户层的层序和续接 `changes` 的键，改了其中任何一样都要用真实宿主重录。」
- **`docs/manual/evaluation.md`。**「在仓库中调用行为统计」不改（`behavior-metrics-4` 标识不变）；新增一节「计算分层记忆试点指标」：命令 `uv run python -m tools.analysis.memory_pilot --export <导出目录> [--output <文件>]`、先认证导出、只接收当前代码认证得过的单人导出（旧代码写出的导出认证不过）、`checks` 与 `digest_review` 怎么读、结果只报告不改运行。
- **`docs/manual/resources.md`。** 第 03 章在「凭状态与完成效果」之后新增的那段（「推导结果用 tools/analysis/memory_categories.py 逐行查看」）之后，新增一节「检查内容行的记忆类别」，只写工具的用法：命令 `uv run python -m tools.analysis.memory_categories --package <包目录> [--output <文件>]`；输出的 `workplace_kind`；每行的 `category`、`rule`、`reason` 怎么读；改了 `memory_category` 之后重建包再运行一遍。何时写 `memory_category` 只在第 03 章那段里写，这一节不重复。

## 测试

新测试放在 `tests/unit/evaluation/` 与 `tests/integration/evaluation/`。`tests/unit/evaluation/memory_pilot_fixtures.py` 提供 `PilotExportBuilder`：在临时目录写出最小的导出形状（`manifest.json` 带 `frozen_run_manifest.agent_ids`、`environment_assets`、`stage_model_identity.max_react_turns` 与 `window`；`events.jsonl`；`purchases.jsonl`；`quality.json` 带 `model_usage`；`assets/`），按需追加决策、观察、请求、响应、世界请求、回顾事件与目录行；另提供夹具 `skip_authentication`，用 pytest `monkeypatch` 把 `tools.analysis.memory_pilot.validate_export` 换成只记录所收目录的函数（最小形状通不过真实认证）。单元测试都经 `load_export` 读这份目录；真实认证由集成测试覆盖。每个指标都用手算答案断言，所以任何一项没实现或口径偏了都会失败。

**`tests/unit/evaluation/test_memory_pilot.py`：**

| 测试 | 对应条款 |
| --- | --- |
| `test_load_export_authenticates_before_reading_any_file` | 输入 1：替身 `validate_export` 抛 `IntegrityError` 时它原样上抛，替身收到的是这个目录，此时 `events.jsonl` 被删掉也不报别的错 |
| `test_pilot_report_refuses_a_multi_person_export` | 输入 3 |
| `test_pilot_report_refuses_a_frozen_catalog_file_missing_from_the_manifest` | 输入 5 |
| `test_cli_writes_the_report_and_refuses_an_existing_output`、`test_cli_logs_the_input_error_and_returns_one` | 命令行；后者另含替身认证抛 `IntegrityError` 时返回 1 |
| `test_memory_pilot_imports_no_runtime_code` | 在子进程（`sys.executable -c`，工作目录为仓库根）里导入 `tools.analysis.memory_pilot`，断言 `sys.modules` 没有以 `worldmock.simulation_engine`、`worldmock.agents`、`worldmock.runtime`、`custom` 开头的模块 |
| `test_report_has_every_metric_section_with_its_keys` | 顶层键序、`M1`…`M12` 各自的键集 |
| `test_report_is_byte_identical_on_two_computations` | 报告结构 |
| `test_ratios_split_first_three_days_from_day_four_on` | 分段比例 |
| `test_m1_counts_lunch_and_dinner_food_decisions_and_any_cooking_start` | M1 饮食决策 |
| `test_m1_counts_a_past_dish_or_date_from_recent_days_or_search_results` | M1 往日文字、名称、三种日期写法 |
| `test_m1_ignores_names_only_in_today_or_in_the_request_target` | M1 分子只看思考和总结 |
| `test_m2_names_the_kit_and_its_received_date_in_the_cooking_line` | M2 命名 |
| `test_m2_counts_cooking_whose_line_is_visible_to_a_later_decision` | M2 分母：家里原有的食材不计；时钟推进里自然结束、当天后面只有一次决策的不计，后面有两次的计；经 conclude 在决策 `k` 的提交里结束、当天后面还有一次决策的计；在当天最后一次决策里结束的不计 |
| `test_m2_counts_dishes_in_cooking_self_reports_outside_the_used_kit` | M2 对不上、菜名词表三个来源；自述只点名所用食材包（V 10-11 的「那份板栗烧鸡配清炒娃娃菜食材包留着做晚饭」）时不计 |
| `test_m3_requires_yesterdays_food_cell_written_or_unsorted` | M3 分子与 `unsorted` |
| `test_m3_excludes_days_after_a_day_without_food_records` | M3 分母 |
| `test_m4_counts_workday_lunch_portions_repeated_within_three_days` | M4 |
| `test_m5_counts_calls_today_queries_non_empty_errors_limits_and_caps` | M5 归类；「提示」含「范围太大」与含「加关键词缩小范围」的成功结果都计入 `capped_results`；「结果」为列表、为按类别分组的对象（含 `今天` 键）、为 `[]` 与 `{}` 四种形状的非空判定 |
| `test_m5_counts_events_between_a_search_and_the_next_request` | M5 只读核对 |
| `test_m6_layer_statistics_by_layer_name_and_memory_parts` | M6 层大小（按层名取层，与层序无关） |
| `test_m6_detects_a_fallback_rewrite_as_a_non_prefix_today` | M6 兜底次数 |
| `test_m6_persona_layer_identical_share` | M6 人设层 |
| `test_m6_fits_memory_layer_tokens_per_character_by_first_differences` | M6 拟合：按已知权重造的数据解回原权重 |
| `test_m6_token_fit_is_null_when_singular_or_too_few_pairs` | M6 拟合边界 |
| `test_m7_reads_the_fresh_decision_turn_cache_hit_rate` | M7 |
| `test_m8_prices_every_call_class_at_the_frozen_peak_price_per_day` | M8 |
| `test_m9_band_rejections_turns_failures_and_compressions` | M9 前五项；`fresh_followups` 等于同秒决策数减续接数 |
| `test_m9_chain_must_append_the_previous_entry_and_carry_no_persona_delta` | M9 同秒链 |
| `test_m9_lists_a_restart_of_a_just_ended_activity` | M9 重新开始与六种目标 |
| `test_m10_counts_calls_retries_and_day_fallback_share` | M10 `calls_by_day`、`periods`、`expected` 的三键（没有月、年回顾时为 0）、`retries`；`fallback` 比例只数日回顾，月回顾落到 fallback 的格只进 `cells`，`cells` 的次序 |
| `test_m10_guard_discards_follow_the_digest_problem_texts` | M10 `guard_discards`：每条第 04 章问题文字（含 `text_length` 的「; the day digest limit」与 `food_coverage` 的「; missing:」）计入各自的守卫；同一响应同一类的两条名称问题计一次；「the name …」计入 `names` 不计入 `numbers`；响应级四句、截断、参数结构错误、`phase == "failure"` 不计；不认识的问题计入 `unrecognized` 并写错误日志；十九个键全部列出 |
| `test_m10_digest_is_cited_by_an_eight_character_echo_while_visible` | M10 引用 |
| `test_m10_view_object_coverage_reads_the_digest_input` | M10 看法对象覆盖率 |
| `test_m10_counts_number_mentions_not_copied_from_statistics` | M10 统计行照抄 |
| `test_m10_lists_missing_and_duplicate_day_digests_and_expected_months` | M10 期次与 `expected` |
| `test_m10_repetition_and_residue_come_from_the_behavior_tool` | M10 复用行为工具 |
| `test_m11_coverage_and_food_fallback_partition_the_food_days` | M11 ①：`F(D)` 为空、日回顾事件的饮食格非空或在 `fallback` 里的日子进分母，两者都不是的不进；`F(D)` 非空、饮食格为 null 且不在 `fallback` 里的日子计为未覆盖；`coverage_plus_fallback` 的分子是两者分子之和；1 天覆盖、159 天 fallback 的 160 天时它的 `value` 为 1.0（两个舍入值相加会是 1.0001） |
| `test_m11_cooking_meal_and_foreign_dish_mismatches` | M11 ②③④ |
| `test_m12_counts_food_purchases_named_by_an_earlier_preference` | M12 分子 |
| `test_m12_short_or_ambiguous_subjects_never_name_goods` | M12 点名（「一人份食材包」、两字对象、恰好匹配两件仍算） |
| `test_m12_splits_by_card_and_view_age` | M12 分开报告 |
| `test_m12_earlier_view_reliance_by_mention_or_echo_absent_from_the_request` | M12 旁证 |
| `test_checks_apply_the_final_state_thresholds` | 检查项表：每项通过、失败、null 各一例 |
| `test_digest_review_lists_each_category_with_its_verified_lines` | 对照表 |

**`tests/unit/evaluation/test_memory_categories.py`**（在 `tests/fixtures/dataset-environment` 的副本上加行，样例环境按第 08 章已是格式 6；类别规则本身由第 03 章的 `tests/unit/memory/test_categories.py` 测）：

| 测试 | 对应条款 |
| --- | --- |
| `test_category_table_copies_row_categories_with_names_and_fields` | 「行为」六第 2、3 条：`rows` 的次序与每行的 (`row`, `id`, `category`, `rule`) 等于 `row_categories(category_book(load_environment(副本)))`；`name`、`location_kinds`、`app_ids` 取自目录行，app 行后两项为空列表 |
| `test_category_table_gives_each_row_kind_rule_and_category_its_reason` | 「行为」六第 3 条：副本加行后理由表的八行都至少出现一次，共九种行（内容行写 `memory_category`、食品单元、班次地点、其余地点、office 前提的 app 内容、无前提的 app 内容；app 行写 `memory_category`、含 office 前提内容、不含），理由逐字等于表中文字且 `{workplace_kind}` 已替换；`ROW_REASONS` 恰有表中的十三个键 |
| `test_a_persona_without_work_schedule_has_no_workplace_kind` | 「行为」六第 2 条：`workplace_kind` 为 null，没有规则 5 与工作类的规则 6 |
| `test_cli_prints_or_writes_the_table` | 命令行：标准输出与 `--output` 两种写法逐字节相同；已存在的输出文件被拒绝；包加载失败时返回 1 |

**`tests/unit/evaluation/test_behavior_metrics.py`：** 新增 `test_digest_repetition_pairs_adjacent_days_of_one_category`、`test_digest_identity_residue_counts_example_markers`；:678 所在测试另断言 `reports` 的最后两个键依次为 `memory_digest_repetition`、`memory_digest_identity_residue`，:678 的 `behavior-metrics-4` 断言不变。其他断言结构标识的测试（含第 08 章改名后的 `tests/system/lifecycle/test_sleep_runs.py`）本章不改。

**`tests/integration/evaluation/test_memory_pilot_export.py::test_pilot_report_reads_a_real_two_day_export`：** 用 `tests/engine_fixture` 的引擎与 `tests/integration/runtime/current_contract_fixtures.decide` 驱动真实宿主，第一天做几次决策（修订计划、开始一项在地点做的事、下单一份食品、播报），用 `engine.advance` 推进过零点，以第 04 章的 `settle_pending_memory_digests(engine, StubMemoryDigestClient(...))` 生成日回顾，第二天再决策两次，用 `export_run(store, store.last_event_seq, 目录)` 导出后跑 `pilot_report`。断言：`M10.periods.day == 1`；`M3.periods.all.value == 1.0`；`M6.memory_layer_by_observation.max <= 14000`；`M9.chain.append_violations == 0`；`checks` 的名字集合等于检查项表；`digest_review` 每类一行。这条测试把工具钉在第 01–06 章的真实形状上，也让 `load_export` 走一次真实认证。同一文件另有 `test_pilot_report_refuses_a_tampered_export`：把同样流程导出的 `events.jsonl` 改动一个字节后，`pilot_report` 抛 `validate_export` 的 `IntegrityError`，`main` 返回 1（输入 1）。

**`tests/unit/engineering/test_compare_runs.py`：**
- 夹具 `Recording._events`（:132-133）的观察改为最终记忆层形状：`{"layer":"记忆层","可查":{"最早日期":"2026-01-05"},"近三天":{"饮食":[],"工作":[],"生活":[]},"今天":[{"时刻":"07:50","类别":"生活","核验":["早饭"]}]}`。
- `test_nested_recorded_at_is_compared`（:313）改名为 `test_nested_simulated_time_is_compared`，改动 `今天[0].时刻`，断言结论为 different 且差异路径为 `.data.memory_layer.今天[0].时刻`。
- 新增 `test_memory_digest_references_follow_event_identity`：两侧在 `activity_progress` 的 `data.digests` 里各写 `{"food": {"text": "…", "source_event_ids": [eid(4)], "cited_event_ids": [eid(5)]}, "work": None, "life": None}`（头一侧前缀不同），结论为 equal。
- 新增 `test_a_nested_key_named_like_a_wall_clock_mask_is_compared`：两侧同一条 `activity_progress` 事件的 `data` 各加一个键 `recorded_at`，值分别为 `"2026-10-15T09:00:00+00:00"` 与 `"2026-10-16T09:00:00+00:00"`；结论为 different，差异路径为 `.data.recorded_at`。它取代改名前的 `test_nested_recorded_at_is_compared`，作为 tests/dataset_acceptance.py 变异 `wall_clock_masked_by_key_name`（`scripts.compare_runs::mask_paths`，按键名而不是按完整路径屏蔽）的杀死测试：记忆层不再有嵌套的 `recorded_at`，改名后的 `test_nested_simulated_time_is_compared` 杀不死它；该变异的 `tests` 改为这条新测试。
- 新增 `test_memory_digest_reference_to_another_event_is_a_difference`：头一侧 `source_event_ids` 指向别的事件，差异路径为 `.data.digests.food.source_event_ids[0]`，没有 problem；指向未知事件时报 `REFERENCE_NOT_AN_ID`。

**`tests/system/viewer/run_viewer_context.test.cjs`：**
- `LAYER_KEYS`、`LAYER_NAMES` 改为最终层序；`inlineRequest` 把工具层放在第 4 位。
- `genuine current requests have seven layers and ten offered tools` 改名为 `… and eight offered tools`：工具名依次为 `start_main_activity`、`start_side_activity`、`report_status`、`pause_activity`、`conclude_activity`、`revise_day_plan`、`recall_task_summary`、`search_memory`，没有三个 `memory_*`。
- `current progress records keep source facts and mark self notes unverified` 改名为 `current progress records keep source facts`，记录不带 `self_note`，标签与正文按新写法断言。
- `nested brackets and escaped quotes never change lossless layer boundaries` 改写记忆层（下标 1）的「今天」：`[{"时刻":"08:00","类别":"生活","核验":["名字 [a,b] 与 \"引号\""],"自述":["…"]}]`。
- 新增 `the fixture continuation appends the previous decision entry in changes.memory.added`：续接最后一条 tool 消息的 `changes.memory.added` 恰为一条，`类别` 为「生活」，`核验` 为 `["改了今日计划"]`。
- 新增 `rule texts describe the layered memory, digests and eight tools`：`rules.memory.fill` 含「14,000 字」；`rules.digest` 存在、`rules.reflection` 不存在；`rules.tools.fill` 含「共八个」；`rules.persona.steps` 没有「看法偏好」；`rules.progress.steps` 没有 `self_note`。

**`tests/system/viewer/run_viewer.test.cjs`：**
- `context reads current seven-layer actions and complete other requests`：用户层键序改为最终层序；`self_note 未经核验` 改为 `「自述」与「回顾」未经核验`。
- `context navigation, stage filter and all rule paths are available through the actual view`：「tools」一句改为新的「最后回合」文字，`progress` 的事务句改为新的「事务记录」文字；另逐条查看 `memory` 的「兜底」、`progress` 的「已发布界」、`digest` 的「校验重试」，三步的说明文字逐字等于「行为」八的表（`digest` 一步即「输出被截断、参数不合格式，或正文、依据、名称、饮食核验名称、饮食覆盖、做饭那一句、数字与次数照抄各守卫不通过时……」整句）。
- `context drills into current tools and real-shaped progress self notes` 改名为 `context drills into current tools and progress records`：进度层记录不带 `self_note`，工具数为 8，`填入` 文字为新的 `tools.fill`。
- `context drills into an inline current tool layer …`：`inlineContextRequest` 按最终层序排层，`填入` 断言改为新的 `tools.fill`。

**`tests/system/viewer/run_viewer_identity.test.cjs`：** `observation()` 的 `memory_layer` 改为最终形状，`progress_layer` 记录去掉 `self_note`。

**`tests/system/lifecycle/test_run_viewer_recording.py`：** 断言改为：工具恰为八个且含 `search_memory`、不含三个 `memory_*`；录制样例 `action` 首个 user 的层名序列等于宿主请求的层名序列；`continuation` 最后一条 tool 消息的 `changes` 键集与 `changes.memory.added` 都等于宿主请求里的；其余断言不变。`tests/system/viewer/test_run_viewer.py` 不改，照常执行全部 Node 测试。

**WorldMock-Dataset `tests/test_white_collar_package.py::test_memory_categories_cli_matches_row_categories`：** 以 `tools.execution.engine_root()` 为工作目录、`sys.executable -m tools.analysis.memory_categories --package <包 0001>` 运行，返回 0；输出 `rows` 逐行的 (`row`, `id`, `category`, `rule`) 等于同一进程里 `row_categories(category_book(load_environment(<包 0001>)))` 的同一行（`kind`, `row_id`, `category`, `rule`），行数相同。包 0001 逐行的钉住由第 03 章的 `test_memory_categories_of_every_row_match_the_pinned_table` 与 `test_office_lunch_override_and_the_rows_the_proposal_names` 负责。

**风格与覆盖。** 两个新模块受 `tests/unit/engineering/test_style_gate.py` 检查（`SCAN_ROOTS` 含 `tools/analysis`，:17；这个门禁在 f67c25e5 上没有历史违规台账，任何违规都失败）；受 `scripts/coverage_gate.py` 检查（`PRODUCTION_ROOTS` 含 `tools/analysis`，:25），公开接口全部由上面的测试覆盖。

## 不在本章

- 记忆层、今天条目、近三天、回顾、检索、进度层、续接、人设层和系统层规则本身的行为（第 01–07 章）；本章只读它们记下的形状。
- 类别规则 0–7 的定义、`memory_category` 字段、静态推导表 `row_categories` 与包 0001 推导表的夹具和钉住测试（第 03 章）；包 0001–0005 的重建、目录格式 6 和 LabelKit 通知（第 08 章）。
- `tools/run-viewer-context.js` 之外第 05 章列出的工具面说明文字（life_agent.py、read_query.py 等）。
- 质量版本 10、`memory_digest` 调用类（第 04 章、第 10 章）；两份完整日基线的重冻和门禁次序（第 10 章）。
- 07-evaluation.md 里「就寝反思」等退役说法的改写（第 08 章）。
- 记忆层在查看器里的下钻；`scripts/inspect_run.py` 的改动。
- 试点的实际运行，以及提案 P4。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 提案 P0 要在 V 上复现第一章的数字，V、B 是质量版本 9、目录格式 5 的旧运行 | `memory_pilot.py` 只读当前代码认证得过的导出；V、B 基线用提案写出的数字；P0 的复现改由逐指标手算答案的单元测试和真实宿主导出的集成测试承担 | AGENTS.md 禁止兼容层；新加载器拒收格式 5，认证在当前代码上重算质量报告；编排方决定 P0 与 P1–P3 同时交付、验收靠确定性测试 |
| M1 的 V、B 基线 | 不计算，试点报告写「无 V、B 基线」，M1 只在 `lm-variety-*` 与 `lm-baseline-*` 之间比较 | 提案表里 M1 写「P0 测」，没有冻结数字；旧运行的记忆层是退役形状，读它要写兼容读取，AGENTS.md 禁止 |
| 输入怎样认证 | `load_export` 先调 `validate_export`，事件行数、逐行校验、素材摘要与质量报告都交给它；本工具只另查单人和四个目录文件在不在 `environment_assets` 里 | docs/design/05-recording-engine.md「评估同样先认证输入」；一条规则一处，认证之后再查同样的事是永不触发的分支 |
| 提案 P0 写「只读 footprints.sqlite」，而 M7、M8 要的 `model_usage` 只在导出的 `quality.json` | 改读同一运行的认证导出：`events.jsonl` 是生产解码器写出的事件，`purchases.jsonl` 和 `quality.json` 是记录引擎的投影 | 同一份事实；一条规则一处，不在工具里重写调用分类、计价和分件解码；`compare_runs.py` 与查看器都读导出 |
| M11 要「独立于运行时守卫重新实现」 | 模块只导入标准库、`tools.analysis` 两个模块、`validate_export`、`WorldMockError` 和 `worldmock.state` 的共享取值；测试在子进程里导入模块，断言没有加载 `worldmock.simulation_engine`、`worldmock.agents`、`worldmock.runtime`、`custom` 下的任何模块；回顾类别键 `food`、`work`、`life` 到中文类别的三项对照在本模块另写 | 守卫在 `worldmock.simulation_engine`；`tools.analysis.behavior.metrics` 本身导入记录引擎，按直接导入核对证明不了独立，要核对实际加载的模块；三项对照是回顾事件的形状（第 04 章），不是判定规则 |
| 「早饭、午饭、晚饭」的时段没有给出 | 固定为本地 [04:00,10:30)、[10:30,14:30)、[16:30,22:00) | 人设的 `lunch_window` 是食欲锚点，V 的午饭在 11:33–11:50 下单，早于 11:50 的窗口；固定时段与人物无关、可复现 |
| M1「饮食决策」「提示里往日出现过的」「reasoning 或总结」的口径 | 查询、下单限午饭和晚饭时段，做饭开始不限；往日文字为「近三天」回顾与检索里的往日项；名称按菜名词表、日期按三种写法；只看思考和收尾、暂停、播报的总结 | 逐词对应提案定义；请求参数里的商品名本来就会写，不能当作引用 |
| 「词表取目录内容摘要和自述里的菜名」不能从自由文字确定性抽取 | 词表取食品商品名、食材包名及其按「配」切出的菜名、食品内容摘要里「比如／例如」后的列举；自述只作为认菜名的对象 | 唯一可复现的读法；包 0001 的食材包名覆盖了自述里常见的菜名 |
| M2「做饭自述里的菜和实际用掉的食材包对不上」 | 只对用了食材包的做饭计数，计自述里认出的、不是所用食材包名子串的菜名；用家里原有食材时不计 | 用家里原有食材时不知道做了什么。提案基线 2 次里只有 10-09 是菜名错配；10-11 的自述点名的正是用掉的那份食材包（说它还留着做晚饭，V events.jsonl #4709），菜名相符，本口径不计。试点报告并列 V 基线时写明「提案 2 次，按本口径 10-11 那次不计入」 |
| M2 的核验句什么时候算「看得到」 | 结束句所在的条目是当天第一个 `C_k ≥ e` 的决策 `k` 的条目；当天在 `k` 之后还有决策时进分母，读当天最后一条观察的「今天」 | 提案第 `k` 条的窗口是 (max(C_{k-1}, B_D), C_k]，从第 `k+1` 次决策起可见；经 conclude 在 `k` 自己的提交里结束的做饭，`k` 之后只需一次决策；最后一次决策的条目从不进提示 |
| M3 遇到前一天没有饮食记录 | 那天的观察不进分母 | 那一格按规定省略，不是缺失；缺回顾事件另由 `one_day_digest_per_day` 拦下 |
| M4 的定义是「与此前三天重复的份数」，基线写的是「刀削面 3/4」 | 两者都报：`repeated_portions` 按定义计（同一商品在前三天有购买），`most_frequent` 给出最多那一样的份数 | V 按定义是 2/4，3/4 是最多那一样的占比；两种读法都保留，结论只看方向 |
| M5「校验错误」与「碰到上限」 | 校验错误含协议错误响应和除 `MEMORY_SEARCH_LIMIT` 以外的错误码；碰到上限分成第 4 次检索被拒和 6,400 字放不下两项 | 提案同时有「每次决策 3 次」和「结果 6,400 字」两个上限 |
| M6 的层大小在哪里量、兜底怎么认、0.638 怎么复核 | 层大小量新快照首个请求，记忆层合计另量全部观察；「今天」不是前一条的前缀即算一次兜底；用相邻请求差分、六类字符的无截距最小二乘复核 | 续接请求的首个 user 是链首旧层；兜底是唯一改写早先条目的路径；与研究目录 out3/tokens 的中心估计法相同 |
| M8 的价格 | 一律按 `pricing_snapshot.peak` 计 | 提案「价格口径一律按冻结的峰时价」，试点之间不受启动时刻影响 |
| 「拒绝不超过基线 6 次的 2 倍」用于长短不同的试点 | 上限按运行日数折算：2×6×日数/5 | 基线 6 次出自 5 天的 V |
| M9「容量压缩」与「刚了结的活动被重复开始」的认法 | 前者认进度层里的 `condensed` 记录；后者认上一次与本次观察之间结束、本次又受理同一目标的活动，按事件身份认目标，不解析活动名，并作为检查项 `no_restarted_activities`（`==` 0） | 进度层记录不再带 `self_note`（第 06 章），浓缩只剩折叠一步；目标身份来自受理快照，可复现；提案 P1 验收「尤其是刚了结的活动没有被重复开始」与 M9「有一项回退就不上线」 |
| 容量处理第 0 步「提前结束本秒续接」 | 不计入 `capacity_compressions`；另报 `chain.fresh_followups`（同秒决策数减续接数），只报告 | 第 0 步不留记录，改用的新快照在事件里与非前缀、链中缺思考两种断链一样；prompt_capacity.py 把它与「浓缩进度层」分开写 |
| M10「各道守卫的丢弃次数」要按守卫分 | 按第 04 章「输出校验」分类：响应级四句与截断按全文相等，参数结构错误按「不以 `digests.` 开头」，其余在 `; ` 后接 `digests.` 处切成逐条问题，按两条空值检查加守卫表十三行的模板逐条 `fullmatch`，第一个匹配的为准；计数单位是「一次响应里的一类」 | 问题文字都以 `digests.{key}` 开头，第一个「:」之前是类别键而不是守卫；一条错误里有多道守卫的问题；「丢弃」的是一次尝试里的一类段落，同一守卫报几条名称仍是一次丢弃 |
| M10 的 fallback 比例 | 只数日回顾的「类·日」；月、年回顾的 fallback 格只列在 `cells` | 提案 P1 验收「落到 fallback 的「类·日」不超过 10%」 |
| M11 ① 的分母「有饮食核验的类·日」 | `F(D)` 非空的回顾日，并上日回顾事件表明饮食类有记录（非空或在 fallback 里）的回顾日 | 独立重算只能从事件认出收货与做饭；只有查询、未送达订单或公司午饭的日子由回顾事件表明有饮食记录；`F(D)` 非空的日子不依赖回顾事件，饮食格被错记为 null 时照样计为未覆盖 |
| M11 ① 与饮食 fallback 之和 | 写成比例，分子相加、分母共用 | 两个各自舍入到四位的值相加，在分母 160 时就会得到 1.0001，检查项 `== 1.0` 会误判 |
| M10「回顾被引用」「看法对象覆盖率」的口径 | 引用：回顾在提示里可见时，模型文字与它有八字回声；覆盖率：对象取自回顾输入里的「看法·」项 | 与 M12 的回声同一规则；对象按输入的类别切分，不重算类别 |
| M12「能匹配 2 件以上目录商品的对象不算」 | 读作多于 2 件才不算；「至少 4 字」对两个方向的包含都适用；对象不足 3 字不点名 | 研究目录 out3/views/part5_m12.py 按此算出 5/18 与 0/17，views.md 写作 more than 2 |
| 措辞回声的判定 | 看法正文的某个 8 字窗口出现在模型文字里、不在请求文字里；或看法对象出现在模型文字、不在请求文字里 | 与研究目录 out3/views/part4b_reliance.py 一致；人设层不再含看法时，请求文字就是全部提示 |
| 检查项的范围 | 只放提案对最终形态写明的绝对阈值；「续接成功率与现状相同」等对比项只报告 | 与 V、B 的比较要人读；P1 的约 $0.30 被 P2 的 $0.22 取代 |
| 进度层两项估计阈值 | 照提案数值（中位 ≤3000、p90 ≤7000）判定，不校准 | 提案标为「估计值，P0 校准」；本交付的 P0 不读质量版本 9 的运行，没有可校准的输入 |
| P1、P2、P3 的试点在一次交付里怎么跑 | 合并成一组：V 卡片两次、B 卡片两次（10-08→10-13），V 卡片 10 天，V 卡片跨月（09-28→10-03），共 35 个模拟日、约 $7.2 | 三期代码已是同一份，分期试点会重复跑同一身份；提案 P2 的 $0.207 是全口径，已含零点计划与回顾（out3/reports/econ.md） |
| 只接收单人运行 | 导出不是一个人物时拒绝 | `RunConfig` 是「一次单人运行」 |
| 运行日从哪里取 | 取导出清单的 `daily_coverage`；回顾日是除最后一个运行日以外的运行日 | 导出的 `window.end` 是最后一条导出事件的时刻（src/worldmock/recording_engine/export/export.py:169、:183），按 `window.end` 的本地日期止（不含）会丢掉不足一天的最后一天；`daily_coverage` 由 `window_days` 沿本地零点切出（export.py:172），含这一天 |
| 行为工具的两项回顾报告归哪一章 | 归本章，结构标识 `behavior-metrics-4` 不变 | 它在 `tools/analysis`，M10 直接复用；只报告的新增项不改标识：metrics.py:12-13 的模块说明，83a12337 加 workload、routine_deviation 时标识也未改 |
| `memory_categories.py` 的类别与规则从哪里来 | 规则全部由第 03 章 `row_categories` 给出，本工具只加名称、字段与理由；理由按 (行种类, 规则, 类别) 取固定文字，规则 5 的时段条件写进理由 | 第 03 章「每条规则只在这些函数里写一次」；目录行没有时刻 |
| app 行没有覆盖时的类别 | 照抄 `row_categories`：有内容要求班次地点为 (工作, 6)，否则 (生活, 6) | 第 03 章第 9 节静态推导表的 app 行判定 |
| 包 0001 的推导表在哪里钉住 | 由第 03 章的数据集测试与夹具 `memory-categories-0001.json` 钉住；本章的数据集测试 `test_memory_categories_cli_matches_row_categories` 以子进程在引擎检出根运行工具，核对输出与 `row_categories` 一致 | 一张表只钉一处；包只在数据集仓库；两仓的 `tools` 包同名，只能以子进程在引擎根运行；`engine_root()` 已是数据集执行工具的做法 |
| 查看器进度层规则「二者不迁入记忆层」 | 改为「办成同时在记忆层「今天」的核验里写成「办成「事务名」」」 | 提案核验短句表把个人事务办成写进今天条目，原句与之相冲 |
| 查看器要不要给记忆层加下钻 | 不加；只改层序、说明文字（含 `rules.digest`）与 self_note 的说明；回顾事件在时间线和记忆页照现有写法显示（未知种类照原值） | 提案只要求更新查看器说明，并让测试跟着记忆层形状改 |
| `tests/fixtures/viewer-context.json` 怎么重录、怎么守住 | 用临时脚本跑录制测试的同一流程写文件，不入库；录制测试另钉层名序列和续接 `changes` | 现有录制测试只比系统层和工具，查看器的测试依赖层序和续接形状 |
| `compare_runs.py` 的嵌套时刻测试 | 改为比较记忆层「今天」的「时刻」；05-recording-engine.md 的示例同改 | 最终形状里已没有嵌套的 `recorded_at` |
