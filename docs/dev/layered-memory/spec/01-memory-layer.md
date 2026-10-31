# 01 记忆层：形状、预算、兜底与层序

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。第五批裁定（2026-10-31）修订。

## 目标与依据

本章把提案（docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md，下称提案）里讲记忆层整体的规定落成实现：
记忆层的四个键及其次序和 JSON 形状；按渲染后字数执行的 14,000 字上限；「今天」超限时的兜底；user 数组的终稿层序；
宿主按层名取层、进度层必须在最后的断言；`memory_layer` 观察分件；容量浓缩不碰记忆层；只读问答尚无快照时的空记忆层。

实现的提案段落：

| 提案位置 | 本章落实的内容 |
| --- | --- |
| 第二章「设计原则」第 5 条 | 稳定的层在前；「今天」放在记忆层末尾，只追加；兜底是唯一的改写 |
| 第二章「设计原则」第 6 条 | 记忆层合计 ≤14,000 字，按 `len` 计，含键名，由构建器保证，不用 token 计数器 |
| 「四格落到提示层上」 | 层序：系统层、tools、人设层、记忆层（可查、近三天、今天）、待办层、工具层、环境层、进度层；各部分上限表；「记忆层仍然不参与容量压缩」；「裁剪与兜底」的第 3 条（兜底） |
| 「四格落到提示层上」末句 | 「它仍以 `memory_layer` 观察分件整体存储……键名不变」 |
| 「与现有机制的交互」·「按位置取层」 | `write_prompt_snapshot` 按层名取记忆层；`layers[-1]` 加断言；删去 `_visible_names` 未用的参数 |
| 「与现有机制的交互」·「容量压缩」 | 不变，仍只压进度层 |
| 「存储与事件记录」第 2 条 | `memory_layer` 每次决策都会变，整体一件，不拆分 |
| 第三章「代码」表 | `layout.py`（14,000 字预算、组装）；`memory_layer.py` 删除 8000 字窗口；`prompt_schemas.py`、`life_prompt.py`、`life_persona.py` 的记忆层 Schema、`empty_memory_layer`、层序；`custom/agents/life_agent.py` 按层名取层、断言、`_visible_names` |
| 第三章「设计文档」表 | 06-agent.md「提示与工具」的层序；09-io-formats.md 的记忆层机器形状 |
| 第三章「测试与门禁」 | 「记忆层兜底：超过 14,000 字触发，三步回落到 ≤12,000 字，链内触发时断链」；tests/integration/runtime/test_seven_layer_request.py 的层序 |

适用的裁定（提案第五章）：

| 裁定 | 本章的落实 |
| --- | --- |
| 11（选项 A） | 「今天」是按时刻排列的一条列表，只追加，所以它是记忆层的最后一个键 |
| 15（选项 A） | 只读问答只留 finish；只读请求仍由人设层与记忆层组成，尚无快照时用空记忆层 |
| 16（选项 A） | 记忆层整层用中文键：`可查`、`近三天`、`今天`，以及计数行的 `时刻`、`类别`、`条数` |
| 19 | 不例行折叠；10k token 换算成记忆层合计 14,000 字（`len`，含键名）；每条 120 字只数「核验」「自述」，由第 02 章执行 |
| 24（选项 A） | 追加一条后渲染出的记忆层超过 14,000 字时，三步一次回落到 ≤12,000 字，第三步按小时合成计数行；本次决策在同秒链里时断链 |

编排已定、本章照用的全局决定：本章写终稿。user 数组直接采用终稿层序；提案 P1 期间「现行层序」和「记忆层排在工具层后面」
的过渡形态不实现。「字」一律是 Python `len()` 数出的字符数。

依据数字（取自提案，用于说明，不是验收门槛）：

| 量 | 数 | 出处 |
| --- | --- | --- |
| 新记忆层的 token/字 | 0.638（区间 0.630–0.645）；项目自带的 litellm 计数器在记忆层上为 0.91–0.95，高估约 1.5 倍 | 提案「换算依据」；研究目录/out3/reports/tokens.md |
| 14,000 字 | 约 8.9k token；10k token 约合 15,670 字 | 同上 |
| 最坏记忆层（近三天满格） | 按每格 250 字推算（未实测）V 约 13.6k 字，B 约 12.5k 字；兜底在 V、B 上都不触发 | 提案「裁剪与兜底」（每格 200 字时模拟约 13.1k、12.1k，近三天满格实测 2,266 字；每格 250 字时 2,266 + 9×50 = 2,716 字） |
| 离触发还差多少 | 按每格 250 字推算（未实测），V 最忙的 10-12（89 条，最后一次决策看到 88 条）再多约 3–4 条；B 最忙的时刻再多约 9–13 条 | 同上（每格 200 字时是 6–8 条与 12–17 条，每格多 50 字使余量各少约 450 字） |
| 一次触发的代价 | 多一次约 8k token 的未命中，约 $0.0024 | 同上 |
| 11:50 示例 | 合计 5,422 字：可查 21、近三天 1,719、今天 3,648 | 提案「示例」 |
| `memory_layer` 分件 | 5 天约 406 份，平均约 7k 字，一次运行多约 7–8 MB（V 的 footprints.sqlite 为 131 MB） | 提案「存储与事件记录」 |
| 层序的缓存效果 | 新决策回合缓存命中率模拟值 0.649（现行 0.336）；每模拟日约 $0.207 | 提案 P2「验收」「预计每模拟日成本」 |

## 行为

### 记忆层的形状

记忆层是一个 JSON 对象，恰有四个键，次序固定：

```json
{"layer":"记忆层","可查":{"最早日期":"YYYY-MM-DD"},"近三天":{"饮食":[…],"工作":[…],"生活":[…]},"今天":[…]}
```

| 键 | 值 | 归属 |
| --- | --- | --- |
| `layer` | 常量 `"记忆层"` | 本章 |
| `可查` | `{"最早日期": "YYYY-MM-DD"}`；只读问答尚无快照时的空记忆层里是 `{}` | 本章，见下节 |
| `近三天` | 对象；键是 `MEMORY_CATEGORIES`（`("饮食", "工作", "生活")`）的子集，按这个次序排列；每个值是这一类的格子数组 | 哪些键出现、格子的形状和取哪几天，归第 03 章「类别、归日与近三天」 |
| `今天` | 数组；每项是一条今天条目或一行计数行 | 条目的形状与内容归第 02 章「今天条目」；列表怎样递推、计数行，归本章 |

- 键序就是构建顺序。记忆层只由 `assemble_layer`（见「接口与文件」）构建，渲染时不排序。
- 记忆层只经 `render_layer_json`（src/worldmock/agents/prompt/narration.py:163-175）进入提示：先执行字段黑名单，
  再紧凑序列化，中文不转义，键序不变。`compact_render`（src/worldmock/agents/prompt/tool_groups.py:72-80）只改写工具层，
  所以记忆层里的 `null`（例如第 03 章「未整理」格子的 `"回顾":null`）原样渲染，空列表、空对象也原样渲染。
- 记忆层的新键 `可查`、`最早日期`、`近三天`、`今天`、`时刻`、`类别`、`条数` 都不在 `FORBIDDEN_OBSERVATION_KEYS`
  （narration.py:24-81）里，也都不以 `_sha256` 结尾；渲染不会拒绝它们。
- 观察里记忆层仍在键 `memory_layer` 下（src/worldmock/simulation_engine/observation/layers/observe.py:68），不是只给宿主的键
  （observe.py:35 的 `_HOST_ONLY_KEYS` 不变）。
- 各部分自身的上限由生成它的一侧保证，本章只执行合计上限：`可查` 恒为 21 字；`近三天` 每格回顾 ≤250 字、每类至多三格
  （第 03、04 章），本章不单独核对「近三天」的字数；「今天」每条「核验」加「自述」≤120 字（第 02 章）。「今天」不单设上限，
  受合计约束。

### 「可查」与最早日期

- 最早日期是运行开始时刻 `RunConfig.start_time`（src/worldmock/configuration/inputs/models.py:234）的场景本地日期，
  由 `earliest_date(start_time, timezone)` 算出，写成 `YYYY-MM-DD`。
- 它整次运行不变：同一天里、跨过零点、恢复与重放分支里都相同。恢复和重放从同一环境加载运行配置，并核对
  `state.config_sha256`（src/worldmock/simulation_engine/world/context.py:57-65），所以分支里的最早日期就是父运行的开始日期。
- 第 05 章「search_memory 与工具面」里「早于最早日期时返回空结果，并给出最早日期」用的是同一个 `earliest_date`。
- 只有只读问答尚无快照时的空记忆层没有最早日期，`可查` 是 `{}`（见「只读问答」）。

### 「今天」的递推

记 D 日当天的决策按提交次序为第 1、2、…次（同秒链里的决策各算一次）；第 j 次决策的条目 e_j 按第 02 章「今天条目」的
「发布」规则从第 j+1 次决策起可见。第 k 次决策的观察里，「今天」是下面这个递推的结果：

| 第 k 次决策 | 「今天」 |
| --- | --- |
| k = 1 | `[]` |
| k ≥ 2 | 第 k−1 次决策看到的列表（兜底改过的项保持改后的样子），末尾接上 e_{k−1}；接上以后渲染出的记忆层超过 14,000 字时，按「兜底」回落 |

- 递推只读两样东西：D 日已发布的条目（按决策次序），以及这次观察的「可查」和「近三天」（下称固定部分）。「可查」全程不变；
  第 03、04 章保证「近三天」在 D 日的第一次观察之前就已定下、当天不再变化（「近三天和可查在同一天内逐字节不变」），
  所以递推的每一步都用这次观察的固定部分来量字数，与当时量的一样。
- 递推是纯函数：同样的已发布条目和固定部分，得到逐字节相同的「今天」。进程内缓存只是加速：它记住当天已折入的条目数和
  递推状态，下一次观察只把新发布的条目接上去。缓存在以下任一情形下丢弃，从 D 日的第一条重新递推：
  - 换了存储对象（恢复、重放分支打开新的 `FootprintStore`）；
  - 观察的本地日期变了；
  - 固定部分的渲染文本变了；
  - 已发布的条目比缓存记住的少。
- 因此恢复和重放从当天第一次决策起按同样的次序重算，得到的「今天」与原运行在同一次决策看到的逐字节相同。
- 观察里的「今天」各项是递推状态的深拷贝；宿主或测试改动观察，不影响后面的观察。
- 零点收尾条目（第 02 章「零点收尾条目」，【E36】–【E39】）不是任何决策的条目，从不进入「今天」。

### 上限

- 记忆层的字数是 `len(render_layer_json(layer))`：整个紧凑 JSON 的字符数，含 `{"layer":"记忆层",…}` 的全部键名、引号、
  冒号、逗号和括号。这正是记忆层在 user 消息里占的那一段：`user_message` 把层数组整体交给 `render_layer_json`
  （src/worldmock/agents/prompt/life_prompt.py:159-161），非工具层的渲染与单独渲染这一层逐字相同。
- 每次观察构建出的记忆层都 ≤14,000 字（`MEMORY_LAYER_CHARS`）。构建器在交出记忆层之前核对一次；超过时写英文错误日志
  `Memory layer has <字数> characters, above its 14000-character budget`，再抛
  `RuntimeError("Memory layer exceeds 14000 characters")`，这次观察失败，运行按故障停下。容量浓缩从不替记忆层削减字数。
- 上限按字执行，不调用 litellm 或任何 token 计数器。

### 兜底

**触发。** 递推把 e_{k−1} 接到列表末尾之后，用这次观察的固定部分组装记忆层并量字数；字数严格大于 14,000 时触发。
恰好 14,000 字不触发。

**目标。** 一次触发里按下表的次序逐项改写，每改一项就重新组装、重新量一次，字数 ≤12,000（`MEMORY_BACKSTOP_CHARS`）
时立即停止，后面的项和步骤都不再动。恰好 12,000 字即停止。

| 次序 | 步骤 | 依次处理的项（每一轮都按列表次序从最早的一项起） | 改法 |
| --- | --- | --- | --- |
| 第一步前半 | 换掉已收尾活动的播报自述 | 只交了 report_status 的条目中，它点名的每一项活动都已收尾的那些 | 「自述」换成 `["…"]` |
| 第一步后半 | 换掉其余播报自述 | 其余只交了 report_status 的条目 | 「自述」换成 `["…"]` |
| 第二步 | 只留核验 | 其余条目（计数行除外） | 「自述」换成 `["…"]`；「时刻」「类别」「核验」不动 |
| 第三步 | 按小时合成计数行 | 由「本地小时、类别串」相同的项组成的组，按组里第一项在列表中的位置先后 | 整组换成一行计数行，放在组里第一项的位置 |

各步骤的细则：

- 「只交了 report_status 的条目」是这次决策的世界请求恰是一个受理的 report_status 的条目，即第 02 章「各类决策产生什么」
  表里「只交 report_status」和「report_status，窗口里另有结算」两行；被拒的 report_status 属于「只有被拒请求」一行，归第二步。
- 「它点名的每一项活动都已收尾」：这个 report_status 点名的活动身份集合非空，且其中每一项都出现在第 1 至第 k−1 次决策中
  某次受理的 conclude_activity 所收尾的活动里（包括刚接上的 e_{k−1}）。判定只看已折入的条目，不看此刻世界状态，
  所以冷缓存重算与原来逐字节一致。
- 「换掉已收尾活动的播报自述」「换掉其余播报自述」「只留核验」三轮只改「自述」存在、且不等于 `["…"]` 的项；
  没有「自述」的项和「自述」已是 `["…"]` 的项跳过。兜底从不给项新增「自述」，也从不改「核验」。
- `["…"]` 里的「…」是 U+2026，一个字符。
- 按小时合成计数行：
  - 一项的本地小时是它「时刻」的前两位数字；计数行的本地小时取它「时刻」起点的前两位。类别串是它「类别」的原文，
    「工作」与「工作、饮食」是两个不同的类别串。
  - 一组包含列表中本地小时与类别串都相同的全部项，不要求相邻；先前触发留下的计数行也是组员。
  - 只由一行计数行组成的组跳过；其余的组（包括只有一条条目的组）都合成。
  - 计数行恰为 `{"时刻":"HH:00–HH′:00","类别":<类别串>,"条数":N}`：HH 是两位的本地小时，HH′ 是 HH+1，两位；23 时写
    `"23:00–24:00"`；中间的「–」是 U+2013；N 是组里条目数之和，组里的计数行按它的「条数」计。
  - 合成以后时刻粒度上仍按时间先后：计数行占组里第一项的位置，组里其余的项从列表里去掉。
- 「核验」只在按小时合成计数行时随整项消失，其余任何时候都不删除。

**三步做完仍超过 12,000 字。** 保留做完以后的样子。此时若仍超过 14,000 字，构建器的上限核对按「上限」一节失败。

**日志。** 每次触发写一条英文警告日志 `Memory layer backstop rewrote today from <接上后字数> to <回落后字数> characters
(target 12000)`。冷缓存重算会把同一次触发再写一遍。

**改写之后。**
- 兜底改过的项在当天后面的决策里保持改后的样子；两次触发之间「今天」照样只在末尾追加。
- 触发发生在为第 k 次决策接上 e_{k−1} 的时候，所以第 k 次决策看到的「今天」不再以第 k−1 次决策看到的列表为前缀。
  第 06 章「进度层、待办层与同秒续接」据此判定：第 k 次决策在同秒链里时断链，改用新快照。
- 只读问答快照（见「层序」）保存的是兜底以后的记忆层。
- 「今天」条目的全文不受兜底影响，`search_memory` 查「今天」照样给全文（第 05 章）。

### 层序

user 数组恰有六层，次序固定为 `USER_LAYER_NAMES = ("人设层", "记忆层", "待办层", "工具层", "环境层", "进度层")`。
一次生活决策的请求因此是：

```
系统层 | tools | 人设层 | 记忆层{可查, 近三天, 今天} | 待办层 | 工具层 | 环境层 | 进度层
```

| 层 | 什么时候变 |
| --- | --- |
| 人设层 | 整次运行不变（第 07 章「人设层与系统层规则」） |
| 记忆层 | 「可查」整次运行不变；「近三天」只在零点变；「今天」每次决策在末尾追加一条，兜底触发时改写 |
| 待办层 | 付款完成（下单）、快递到站、送达或领取、暂停与恢复、个人事务变更、改计划，以及零点换上新的今日计划时变 |
| 工具层、环境层 | 每次决策都变 |
| 进度层 | 每次决策都变，排在最后 |

- `life_user_layers` 按 `USER_LAYER_NAMES` 的次序返回六层：人设层是宿主构建的那一层，其余五层取自观察的
  `memory_layer`、`todo_layer`、`tool_layer`、`environment_layer`、`progress_layer`。观察缺某一层、或层名不符，
  照旧写错误日志 `LifeAgent observation layer <键> is unavailable` 并抛同文的 `RuntimeError`。
- 宿主按层名取层，不按位置：`user_layer(layers, name)` 返回层名恰为 `name` 的那一层；没有或不止一层时写英文错误日志
  `User layers carry <个数> layers named <层名>`，再抛 `RuntimeError("User layers must carry exactly one <层名>")`。
- 进度层必须是最后一层：`last_progress_layer(layers)` 返回最后一层；它不是进度层（或数组为空）时写英文错误日志
  `User layers end with <最后一层的层名> instead of 进度层`，再抛 `RuntimeError("Progress layer must be the last user layer")`。
  宿主用它取进度层，再交给 `_progress_meta` 对齐私有元数据；层名只在 `last_progress_layer` 核对，`_progress_meta` 不核对层名。
- 只读问答快照 `life_prompt_snapshot.json` 保存的记忆层用 `user_layer(layers, "记忆层")` 取，不再用 `layers[2]`；
  人设层仍是宿主构建的那个对象。
- `_visible_names` 只收观察一个参数；它从不读待办层，回看名称来自 `goal_bindings` 与 `activity_bindings`。
- 生活决策的六层 user 数组在生产代码里只按层名读取，`last_progress_layer` 读 `layers[-1]` 是唯一的例外；只读问答的两层仍按
  `readonly_layers` 返回的 `(人设层, 记忆层)` 次序解包（custom/agents/life_agent.py:456），不变。
- 只读问答的 user 仍是 `[人设层, 记忆层]` 两层，外部问题另起一条 user 消息（life_prompt.py:164-169 不变）。
- 续接、容量预检和容量故障日志都按层名找层（life_continuation.py:114；prompt_capacity.py:99、:167-171），层序改变不影响它们。

### 容量浓缩

- 浓缩仍只改进度层（src/worldmock/agents/prompt/prompt_capacity.py:98-107）：把最旧一段连续的未受保护记录折成一条 condensed 记录，
  重复到阈值以下或无可折叠（去 `self_note` 的一步随第 06 章【C8】删除，进度层记录不再带 `self_note`）。
  人设层、记忆层、待办层、工具层与环境层从不浓缩；记忆层的字数只由构建器的上限与兜底控制。
- 第 0 步结束同秒续接时，新快照由当前各层构建，已含全部已发布的条目（custom/agents/life_agent.py:491；prompt_capacity.py:93-97）。
- 容量故障日志照旧按层名逐层记 token 用量，记忆层的一项键名是 `记忆层`（prompt_capacity.py:167-168）。

### 只读问答

- `readonly_layers`（src/worldmock/agents/prompt/life_persona.py:78-85）照旧：有快照时返回最近一次生活决策保存的人设层与记忆层，
  记忆层就是那次决策看到的样子（含兜底以后的「今天」）；尚无快照时返回卡片人设层与 `empty_memory_layer()`。
- `empty_memory_layer()` 恰为 `{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}`，由 `assemble_layer(None, {}, [])` 构建，
  通过记忆层 Schema 校验。

### 存储

- `memory_layer` 仍在 `WHOLE_PART_KEYS`（src/worldmock/recording_engine/storage/observation_parts.py:21-22），整层一个观察分件，
  观察正文只带它的 sha256，相同内容只存一次；不新增分件键，也不按条拆分。
- 记忆层随每次决策变化，所以几乎每次决策的观察都登记一个新的 `memory_layer` 分件。
- 分件正文是规范 JSON（src/worldmock/foundation/serialization.py:16，键按码位排序），读回的记忆层与写入前在规范 JSON 下
  逐字节相同；提示只由内存里的观察生成，存储的键序不影响提示。
- 事后读取视图照旧去掉 `memory_layer`（src/worldmock/recording_engine/export/post_run.py:13-16）。

### 删除的东西

- 8000 字单一窗口：`MEMORY_WINDOW_CHARS`（memory_layer.py:35-36）、`_fit`、`_layer`、`_render`、`_size`（:320-353），连同警告日志
  `Memory layer exceeds its %d-character window after trimming: %d characters`。由本章的 14,000 字上限与兜底取代。
- 记忆层旧形状 `earlier_days`、`earlier_today`、`self_notes` 及其 Schema 片段 `_NOTE`（src/worldmock/agents/prompt/prompt_schemas.py:152-162），
  旧的空记忆层 `{"layer":"记忆层","earlier_days":[],"earlier_today":[]}`（life_prompt.py:139-141）。
- `_visible_names` 的 `todo` 参数（custom/agents/life_agent.py:765）。

## 接口与文件

下面的行号都在 f67c25e5 上。中文 `@brief` 写要点；代码、日志和报错用英文。

### 新文件 `src/worldmock/simulation_engine/observation/memory/__init__.py`

包说明 `"""@brief 本目录所属职责的实现。"""`，与同级包相同。第 02、03、04、05 章的模块（entries.py、categories.py、
days.py、digests.py、search.py）也放在这个包里；`__init__.py` 由本章在第 10 章「类别与归日」一步建立。

### 新文件 `src/worldmock/simulation_engine/observation/memory/layout.py`

纯函数库，不持有缓存，不读存储。只导入 `copy`、`logging`、`dataclasses`、`datetime`、`collections.abc.Callable`、
`worldmock.agents.prompt.narration.render_layer_json` 与 `worldmock.foundation.time.readable.local_date`。
`simulation_engine` 导入 `agents.prompt` 已有先例（src/worldmock/simulation_engine/scheduling/plans/day_planning.py:28），
`narration` 只依赖 `tool_groups` 和 `foundation.errors`，不成环。

模块说明：

```python
"""@brief 记忆层的组装：四个键的形状与次序、按渲染后字数执行的上限，以及「今天」接上一条后超出上限时的兜底回落。

规则见 docs/design/06-agent.md「记忆层的形状、上限与兜底」与 docs/design/09-io-formats.md「提示层的机器形状」：
记忆层是 {"layer":"记忆层","可查":…,"近三天":…,"今天":[…]}，经 render_layer_json 渲染后（含键名）合计至多
MEMORY_LAYER_CHARS 字；「今天」按决策次序只追加，接上一条后超出上限时一次回落到 MEMORY_BACKSTOP_CHARS 字以内。
本模块是纯函数库；按人物缓存递推状态的是 observation/layers/memory_layer.py。
"""
```

常量与类型：

```python
#: 渲染后的整个记忆层的字数上限（len，含键名；约 8.9k token，按 0.638 token/字）。
MEMORY_LAYER_CHARS = 14000
#: 兜底一次回落到的字数。
MEMORY_BACKSTOP_CHARS = 12000


@dataclass(frozen=True, slots=True)
class PublishedEntry:
    """@brief 一条已发布的今天条目在记忆层组装时用到的部分；由第 02 章的条目构建器填写。"""

    #: 模型看到的条目，形状见第 02 章（{"时刻","类别","核验","自述"}，空键省略）。
    item: dict
    #: 这次决策的世界请求恰是一个受理的 report_status。
    report_only: bool
    #: report_only 时这个 report_status 点名的活动身份；否则为空集。
    reported: frozenset[str]
    #: 这次决策受理的 conclude_activity 所收尾的活动身份；否则为空集。
    concluded: frozenset[str]


@dataclass(slots=True)
class _Slot:
    """@brief 「今天」的一项：条目或计数行，连同兜底要读的本地小时、类别串、条数与播报事实。"""

    item: dict
    hour: int
    category: str
    count: int
    report_only: bool
    reported: frozenset[str]
    line: bool


@dataclass(slots=True)
class TodayFold:
    """@brief 一个人物一天里「今天」的递推状态：当前各项（兜底改过的保持改后的样子）与已折入条目收尾的活动身份。"""

    slots: list[_Slot] = field(default_factory=list)
    concluded: set[str] = field(default_factory=set)
```

`reported` 与 `concluded` 里的活动身份，由第 02 章用它把「自述」项连回活动的同一办法得到。

公开函数：

```python
def earliest_date(start_time: datetime, timezone: str) -> str:
    """@brief 能查到的最早日期：运行开始时刻的场景本地日期 YYYY-MM-DD；分支沿用父运行的运行配置，所以全程不变。"""
    return local_date(int(start_time.timestamp()), timezone)


def assemble_layer(earliest: str | None, recent: dict, today: list[dict]) -> dict:
    """@brief 记忆层对象，键序即渲染顺序：layer、可查、近三天、今天；earliest 为 None 时「可查」是空对象。"""
    return {"layer": "记忆层", "可查": {} if earliest is None else {"最早日期": earliest},
            "近三天": recent, "今天": today}


def layer_chars(layer: dict) -> int:
    """@brief 记忆层的字数：render_layer_json 渲染出的紧凑 JSON 的 len，含键名、引号与标点。"""
    return len(render_layer_json(layer))


def require_budget(layer: dict) -> None:
    """@brief 记忆层超过 MEMORY_LAYER_CHARS 字时写英文错误日志并以 RuntimeError 停止；容量浓缩从不改记忆层。"""
    chars = layer_chars(layer)
    if chars > MEMORY_LAYER_CHARS:
        logging.error("Memory layer has %d characters, above its %d-character budget", chars, MEMORY_LAYER_CHARS)
        raise RuntimeError(f"Memory layer exceeds {MEMORY_LAYER_CHARS} characters")


def fold_entry(fold: TodayFold, entry: PublishedEntry, earliest: str | None, recent: dict) -> None:
    """@brief 把一条已发布的条目接到「今天」末尾；接上后记忆层超过 MEMORY_LAYER_CHARS 字时兜底回落。

    :param fold: 当天的递推状态，原地更新。
    :param entry: 第 k−1 次决策的条目。
    :param earliest: 这次观察「可查」的最早日期。
    :param recent: 这次观察的「近三天」。
    """
    item = copy.deepcopy(entry.item)
    fold.slots.append(_Slot(item, int(item["时刻"][:2]), item["类别"], 1, entry.report_only, entry.reported, False))
    fold.concluded |= entry.concluded
    before = _chars(fold, earliest, recent)
    if before > MEMORY_LAYER_CHARS:
        _backstop(fold, lambda: _chars(fold, earliest, recent) <= MEMORY_BACKSTOP_CHARS)
        logging.warning("Memory layer backstop rewrote today from %d to %d characters (target %d)",
                        before, _chars(fold, earliest, recent), MEMORY_BACKSTOP_CHARS)


def fold_items(fold: TodayFold) -> list[dict]:
    """@brief 「今天」此刻的各项（深拷贝，调用方改动不影响递推状态）。"""
    return [copy.deepcopy(slot.item) for slot in fold.slots]
```

私有函数：

```python
def _chars(fold: TodayFold, earliest: str | None, recent: dict) -> int:
    """@brief 以当前各项组装的记忆层字数。"""
    return layer_chars(assemble_layer(earliest, recent, [slot.item for slot in fold.slots]))


def _backstop(fold: TodayFold, fits: Callable[[], bool]) -> None:
    """@brief 兜底：换掉已收尾活动的播报自述、换掉其余播报自述、其余只留核验、按小时合成计数行；每改一项重量一次，
    fits 为真即停。"""
    concluded = [slot for slot in fold.slots
                 if slot.report_only and slot.reported and slot.reported <= fold.concluded]
    if _blank(concluded, fits) or _blank([slot for slot in fold.slots if slot.report_only], fits):
        return
    if _blank([slot for slot in fold.slots if not slot.line], fits):
        return
    _merge_hours(fold, fits)


def _blank(slots: list[_Slot], fits: Callable[[], bool]) -> bool:
    """@brief 依次把各项的「自述」换成 ["…"]（没有自述或已是 ["…"] 的跳过）；fits 为真即返回真。"""
    for slot in slots:
        if slot.item.get("自述", ["…"]) == ["…"]:
            continue
        slot.item = {**slot.item, "自述": ["…"]}
        if fits():
            return True
    return False


def _merge_hours(fold: TodayFold, fits: Callable[[], bool]) -> None:
    """@brief 按组首项的先后，把本地小时与类别串相同的各项合成一行计数行，放在组首项的位置；fits 为真即停。"""
    for key in list(dict.fromkeys((slot.hour, slot.category) for slot in fold.slots)):
        members = [slot for slot in fold.slots if (slot.hour, slot.category) == key]
        if len(members) == 1 and members[0].line:
            continue
        first = next(index for index, slot in enumerate(fold.slots) if (slot.hour, slot.category) == key)
        kept = [slot for slot in fold.slots if (slot.hour, slot.category) != key]
        kept.insert(first, _line(key, sum(slot.count for slot in members)))
        fold.slots = kept
        if fits():
            return


def _line(key: tuple[int, str], count: int) -> _Slot:
    """@brief 一行计数行：{"时刻":"HH:00–HH′:00","类别":类别串,"条数":N}，23 时写 23:00–24:00。"""
    hour, category = key
    item = {"时刻": f"{hour:02d}:00–{hour + 1:02d}:00", "类别": category, "条数": count}
    return _Slot(item, hour, category, count, False, frozenset(), True)
```

- `{**slot.item, "自述": ["…"]}` 保持条目原来的键序（「自述」已在原位）。
- 常量只在 layout.py 的函数体里读，测试用 `monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", …)` 改它们时，观察路径一起生效。

### `src/worldmock/simulation_engine/observation/layers/memory_layer.py`

本章只规定这个门面里组装记忆层的部分；`MemoryView` 的其余字段（活动证据、回看标注、已发布界）和条目、近三天的取法归
第 02、03、06 章。

- 模块说明（:1）改为
  `"""@brief 记忆层的门面：按决策组装「可查」「近三天」「今天」并执行字数上限（组装与兜底在 observation/memory/layout.py），
  另给人设看法证据与回看去重。"""`；第 02 章在句末补上它的部分（全句见第 02 章「起规范作用的模块说明」）。
- 删除 `MEMORY_WINDOW_CHARS`（:35-36）、`_fit`、`_layer`、`_render`、`_size`（:320-353），以及随 `_size` 不再使用的 `import json`。
- 新增导入：`from collections.abc import Sequence`、`from worldmock.agents.prompt.narration import render_layer_json`，以及
  `from worldmock.simulation_engine.observation.memory.layout import (PublishedEntry, TodayFold, assemble_layer, earliest_date,
  fold_entry, fold_items, require_budget)`；保留 `dataclass`、`WeakKeyDictionary`、`FootprintStore`、`local_date`、`LayerContext`
  的导入（`_TodayCache`、`_FOLDS`、`_today` 用它们）。本人事件的增量缓存（现 `_CACHE`，:95-96）随第 02 章移到
  src/worldmock/simulation_engine/observation/memory/entries.py；`_FOLDS` 是本章在 memory_layer.py 里新增的缓存。
- 新增缓存与私有函数：

```python
@dataclass(slots=True)
class _TodayCache:
    """@brief 一个人物当天「今天」的递推缓存：日期、固定部分（可查与近三天）的渲染文本、已折入的条目数与递推状态。"""

    date: str
    fixed: str
    count: int
    fold: TodayFold


#: 存储对象 → 人物 → 当天的递推缓存；换成恢复后的新存储对象时从当天第一条重新递推。
_FOLDS: WeakKeyDictionary[FootprintStore, dict[int, _TodayCache]] = WeakKeyDictionary()


def _today(context: LayerContext, store: FootprintStore, entries: Sequence[PublishedEntry],
           earliest: str, recent: dict) -> list[dict]:
    """@brief 这次观察的「今天」：从缓存的递推状态接着折入新发布的条目；日期或固定部分变了、条目变少时从头递推。"""
    date = local_date(context.state.current_time, context.config.timezone)
    fixed = render_layer_json(assemble_layer(earliest, recent, []))
    folds = _FOLDS.setdefault(store, {})
    cached = folds.get(context.agent_id)
    if cached is None or (cached.date, cached.fixed) != (date, fixed) or cached.count > len(entries):
        cached = folds[context.agent_id] = _TodayCache(date, fixed, 0, TodayFold())
    for entry in entries[cached.count:]:
        fold_entry(cached.fold, entry, earliest, recent)
    cached.count = len(entries)
    return fold_items(cached.fold)
```

- `memory_view(context, store)`（:99-113）组装记忆层的部分恰为：

```python
    earliest = earliest_date(context.config.start_time, context.config.timezone)
    recent = ...   # 这次观察的「近三天」，第 03 章
    entries = ...  # 本次观察之前已发布、日期为今天的条目，按决策次序，每条一个 PublishedEntry，第 02 章
    layer = assemble_layer(earliest, recent, _today(context, store, entries, earliest, recent))
    require_budget(layer)
```

  返回的 `MemoryView.layer` 就是这个 `layer`，`observe.py:68` 把它放进观察的 `memory_layer`，键名不变。

### `src/worldmock/agents/prompt/prompt_schemas.py`

- 新增导入 `import itertools` 与 `from worldmock.state.model.memory import MEMORY_CATEGORIES`（常量由第 03 章新增）。
- 删除 `_NOTE` 与旧的 `MEMORY_LAYER_SCHEMA`（:152-162），在原处依次写：

```python
#: 「类别」：类别闭集里互不相同的一到三项，以「、」相连（今天条目与计数行共用）。
_CATEGORY_TEXT = {"enum": ["、".join(names) for size in range(1, len(MEMORY_CATEGORIES) + 1)
                           for names in itertools.permutations(MEMORY_CATEGORIES, size)]}
```

  其后是第 07 章写的今天条目片段 `_ENTRY_LINES` 与 `_TODAY_ENTRY`（「类别」用这里的 `_CATEGORY_TEXT`），再其后：

```python
#: 「可查」：能查到的最早日期；只读问答尚无快照时的空记忆层里是空对象。
_AVAILABLE = _object({"最早日期": _DATE}, optional=("最早日期",))
#: 计数行「时刻」的全部写法：HH:00–(HH+1):00，23 时写 23:00–24:00。
_HOUR_SPANS = [f"{hour:02d}:00–{hour + 1:02d}:00" for hour in range(24)]
#: 兜底合成的计数行：同一本地小时、同一类别串的各项合成一行。
_COUNT_LINE = _object({"时刻": {"enum": _HOUR_SPANS}, "类别": _CATEGORY_TEXT,
                       "条数": {"type": "integer", "minimum": 1}})
```

  其后是第 03 章写的 `_recent_cells` 与 `_RECENT_CELLS`（近三天三类各自的格子数组，只有工作格带「安排」），再其后：

```python
MEMORY_LAYER_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "记忆层",
    **_object({
        "layer": {"const": "记忆层"},
        "可查": _AVAILABLE,
        "近三天": _object(_RECENT_CELLS, optional=MEMORY_CATEGORIES),
        "今天": {"type": "array", "items": {"oneOf": [_TODAY_ENTRY, _COUNT_LINE]}},
    }),
}
```

  「–」是 U+2013。「可查」必有，其中「最早日期」可省；「近三天」的三个类别键都可省。条目没有「条数」键，计数行没有
  「核验」「自述」键，两者都封闭，一项只能符合其中之一。
- `USER_LAYER_NAMES`（:292-293）改为

```python
#: user 消息中六层的固定顺序：整次运行不变的在前，进度层在最后（docs/design/06-agent.md「提示与工具」）。
USER_LAYER_NAMES: tuple[str, ...] = ("人设层", "记忆层", "待办层", "工具层", "环境层", "进度层")
```

  它从此是层序的唯一来源，由 `life_prompt.life_user_layers` 读取。
- `LAYER_SCHEMAS`（:294-298）的键集不变；`MEMORY_LAYER_SCHEMA` 的值改变，所以 `life_prompt_sha256()`（life_prompt.py:172-177）
  改变（重冻归第 10 章）。`USER_LAYER_NAMES` 不进提示指纹，层序改变本身不改变提示指纹。

### `src/worldmock/agents/prompt/life_prompt.py`

- 模块说明（:1-7）改为：

```python
"""@brief 七层请求的宿主侧：生活与只读系统层、原生工具用途说明、六层 user 组装、按层名取层与提示指纹。

docs/design/06-agent.md「提示与工具」：system 为系统层 JSON（整次运行逐字节不变），user 为人设层、记忆层、待办层、
工具层、环境层、进度层六个对象组成的 JSON 数组（次序即 prompt_schemas.USER_LAYER_NAMES，进度层在最后）；宿主按层名
取层，不按位置。工具定义只经原生 tools，description 只写用途，世界交互规则只在系统层 rules 写一次。只读问答的 user
为人设层与记忆层，外部问题另起一条 user 消息原样给出。
"""
```

- 导入改为 `from worldmock.agents.prompt.prompt_schemas import LAYER_SCHEMAS, USER_LAYER_NAMES`，新增
  `from worldmock.simulation_engine.observation.memory.layout import assemble_layer`。
- `_OBSERVATION_LAYER_KEYS`（:128-130）改为下面这张表（注释同改），它只管键与层名的对应，次序由 `USER_LAYER_NAMES` 决定：

```python
#: 观察中由环境构建的五层所在的键与层名（人设层由宿主从人物档案构建）。
_OBSERVATION_LAYER_KEYS = (("memory_layer", "记忆层"), ("todo_layer", "待办层"), ("tool_layer", "工具层"),
                           ("environment_layer", "环境层"), ("progress_layer", "进度层"))
```

- `empty_memory_layer`（:139-141）：

```python
def empty_memory_layer() -> dict[str, Any]:
    """@brief 只读问答尚无快照时的空记忆层：「可查」与「近三天」是空对象，「今天」是空列表。"""
    return assemble_layer(None, {}, [])
```

- `life_user_layers`（:144-156）：

```python
def life_user_layers(observation: Mapping[str, Any], persona: dict[str, Any]) -> list[dict[str, Any]]:
    """@brief 按 USER_LAYER_NAMES 的层序取六层：人设层、记忆层、待办层、工具层、环境层、进度层。

    五层取自环境观察的同名键，人设层由宿主构建；缺失或层名不符的观察是损坏的宿主观察。
    """
    layers = {"人设层": persona}
    for key, name in _OBSERVATION_LAYER_KEYS:
        layer = observation.get(key)
        if not isinstance(layer, dict) or layer.get("layer") != name:
            logging.error("LifeAgent observation layer %s is unavailable", key)
            raise RuntimeError(f"LifeAgent observation layer {key} is unavailable")
        layers[name] = layer
    return [layers[name] for name in USER_LAYER_NAMES]
```

- 新增两个公开函数，放在 `life_user_layers` 之后：

```python
def user_layer(layers: Sequence[dict[str, Any]], name: str) -> dict[str, Any]:
    """@brief 按层名取 user 数组里的一层；恰好一层同名，否则是损坏的层数组。"""
    found = [layer for layer in layers if layer.get("layer") == name]
    if len(found) != 1:
        logging.error("User layers carry %d layers named %s", len(found), name)
        raise RuntimeError(f"User layers must carry exactly one {name}")
    return found[0]


def last_progress_layer(layers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """@brief user 数组的最后一层，必须是进度层（层序以进度层收尾，进度元数据与它逐条对齐）；否则是损坏的层数组。"""
    last = layers[-1].get("layer") if layers else None
    if last != "进度层":
        logging.error("User layers end with %s instead of 进度层", last)
        raise RuntimeError("Progress layer must be the last user layer")
    return layers[-1]
```

- `system_message`、`user_message`、`readonly_messages`、`life_prompt_sha256` 不变。

### `src/worldmock/agents/prompt/life_persona.py`

- 本章不改 life_persona.py 的代码：`readonly_layers`（:78-85）在尚无快照时返回 `empty_memory_layer()`、`write_prompt_snapshot`
  （:72-75）整层写入，这两点不变；:84 的人设层回退一行由第 07 章改。
- 模块说明（:1-8）由第 07 章改写；改写后关于只读问答的那一句写成「只读问答使用最近决策保存的人设层与记忆层快照；
  尚无快照时使用卡片人设层与空记忆层」。

### `src/worldmock/agents/prompt/prompt_capacity.py`

- 本章不改代码（`_condensation_steps` 删去第一段归第 06 章）。
- 模块说明（:1-11）里「每步之后重新计数。……不静默删减。」一句（基线第 7-8 行）改为：「每步之后重新计数。受保护记录从不改变；
  人设层、记忆层、待办层、工具层与环境层不浓缩，记忆层的 14,000 字上限由构建器保证
  （simulation_engine/observation/memory/layout.py），这里不量它的字数。仍超限时按层记录 token 用量并以容量故障停止，不静默删减。」

### `src/worldmock/agents/prompt/narration.py`

- 代码不变。
- :22 里「（记忆层 self_notes 与结果 outcome 的活动名）」改为「（结果 outcome 的活动名）」，:20-23 其余文字不变：记忆层不再有 `self_notes`。

### `custom/agents/life_agent.py`

- 导入（:48-54）加上 `last_progress_layer`、`user_layer`。
- `_prepare_life_action_state`（:475-492）里：
  - :482 改为 `self._life_budget = ProgressBudget(tuple(layers), tuple(_progress_meta(data, last_progress_layer(layers))))`；
  - :483 改为 `names = _visible_names(data)`；
  - :487 改为 `write_prompt_snapshot(self._workspace_root, persona, user_layer(layers, "记忆层"))`。
  其余各行不由本章改：人设层的构建与已知人物（:479-480、:484）归第 07 章，续接（:488-492）不变。第 07 章「接口与文件」给出
  这个函数改后的整份代码块，其中这三行照抄本章，是同一份改动，只实施一次。
- `_progress_meta`（:738-747）不改，不核对层名；进度层的层名只在 `last_progress_layer` 核对。第 07 章不另写层名核对。
- `_visible_names`（:765）签名改为 `def _visible_names(data: dict[str, Any]) -> dict[str, Any]:`，函数体的语句不变；文档字符串里
  「记忆层 self_notes 所写的活动名」一句不归本章改写（第 02 章「接口与文件」）。
- 生活决策的六层在别处不按位置读取：`build_react_messages`（:453-458）照旧把 `self._life_budget.layers` 整体交给 `user_message`；
  只读问答的 `persona, memory = self._life_budget.layers`（:456）解包的是 `readonly_layers` 返回的两层，不变。

### 不改的文件

- `src/worldmock/recording_engine/storage/observation_parts.py`：`WHOLE_PART_KEYS` 不变，`memory_layer` 仍整层一件。
- `src/worldmock/simulation_engine/observation/layers/observe.py`：:57-58 的 P_k 传递归第 06 章；:68 的键名不变。
- `src/worldmock/recording_engine/export/post_run.py`：`DROPPED_PAYLOADS` 不变。

### 风格

layout.py 每个函数 ≤50 行、≤5 个参数，都有含中文的文档字符串；`_today` 恰 5 个参数。life_agent.py 与 life_prompt.py 的改动
不增加行数上限风险（life_prompt.py 现 177 行）。

## 设计文档

文档、实现和行为测试同一次提交（docs/INDEX.md「当前系统设计」）。

### `docs/design/06-agent.md`「提示与工具」

把 :48 一句换成一段，改后写明：
- 人物每次读取已提交世界的七层提示：system 是系统层；user 是六层组成的 JSON 数组，次序固定为人设层、记忆层、待办层、
  工具层、环境层、进度层，即 [prompt_schemas.py](../../src/worldmock/agents/prompt/prompt_schemas.py) 的 `USER_LAYER_NAMES`；
- 这个次序让整次运行不变的部分在前、每次决策都变的部分在后，并用一张表逐层写出变化时机（内容同本章「层序」一节的表）；
- 宿主按层名取层，不按位置；进度层必须是最后一层，不是时以 `RuntimeError("Progress layer must be the last user layer")` 停止；
- 只读问答的 user 是人设层与记忆层两层，取自最近一次生活决策保存的快照，尚无快照时是卡片人设层与空记忆层
  （形状见[状态与序列化](09-io-formats.md)「提示层的机器形状」）；
- 容量浓缩只改进度层，人设层、记忆层、待办层、工具层与环境层不浓缩，记忆层的上限见「记忆层的形状、上限与兜底」。

这一节的工具表、系统层规则摘要和续接的 `changes` 由第 05、06、07 章改写。

### `docs/design/06-agent.md`「进度、待办与记忆」

在 :216 的 `##` 标题之下新增小节 `### 记忆层的形状、上限与兜底`，位置在第 06 章的「进度层的下界与带入的记录」（取代 :267-271）
与「待办层」两个小节之后、:273（第 02 章「今天条目」小节取代的两段）之前（各小节的次序见第 10 章「设计文档」）；:218-265 的导语与结果说明（:218-220 由第 06 章改写）仍直接在
`##` 标题之下，不归入本小节。第 02、03、04、05、06 章在同一节里另写今天条目、记忆的类别、回顾、检索和进度层的小节。
本小节改后写明：
- 记忆层恰有 `layer`、`可查`、`近三天`、`今天` 四个键，次序固定，机器形状见[状态与序列化](09-io-formats.md)「提示层的机器形状」；
- 「可查」的最早日期是运行开始时刻的场景本地日期，整次运行、恢复与分支都不变；
- 「今天」是当天已发布条目按决策次序的递推，只在末尾追加，兜底是唯一的改写；恢复和重放从当天第一次决策起重算，逐字节一致；
- 上限：经 `render_layer_json` 渲染后的整个记忆层（含键名、引号和标点）按 Python `len` 至多 14,000 字，约 8.9k token
  （0.638 token/字），不用 token 计数器；超出时以 `RuntimeError("Memory layer exceeds 14000 characters")` 停止；
- 兜底：触发条件（严格大于 14,000 字）、目标（≤12,000 字，每改一项重量一次）、「兜底」一节的步骤表与各步细则（计数行的形状
  不在此重写，引用[状态与序列化](09-io-formats.md)「提示层的机器形状」）、三步做完仍超 12,000 字时保留、每次触发一条英文警告日志；
- 兜底改写之后，这次决策在同秒链里时断链，链的规则见第 06 章改写的续接段落；
- 记忆层不参与容量浓缩；
- 记忆层以观察键 `memory_layer` 整层存为一个观察分件，见[记录引擎](05-recording-engine.md)「事件与来源」。

同时，「进度、待办与记忆」里不再出现 8000 字符窗口：:300-301「直到被 8000 字符窗口裁掉；裁剪先删最旧日期的感想」随第 08 章
删除 :285-304 就寝反思那一段一起删去，本章核对整份 06-agent.md 不再有「8000 字符」。

### `docs/design/09-io-formats.md`

在「输入与模型身份」与「事件与检查点」之间新增一节 `## 提示层的机器形状`，其中本章写 `### 记忆层` 小节（第 06 章在同一节写
`### 待办层`）。改后写明：
- 记忆层的顶层键表：`layer` 常量「记忆层」；`可查` 对象，唯一的键 `最早日期`（`YYYY-MM-DD`），只读空记忆层里为空对象；
  `近三天` 对象，键是饮食、工作、生活的子集并按此次序，值是格子数组；`今天` 数组，每项是今天条目或计数行；只读问答尚无快照时的
  空记忆层恰为 `{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}`；
- 计数行恰为 `{"时刻":"HH:00–HH′:00","类别":<类别串>,"条数":<正整数>}`，HH′ 是 HH+1，23 时写「23:00–24:00」，「–」是 U+2013；
- 类别串是饮食、工作、生活里互不相同的一到三项，以「、」相连；
- 今天条目与近三天格子的形状写在同一小节的表里，由第 02、03 章写；
- 渲染：`render_layer_json`，键序即构建顺序，中文不转义，`null`、空列表与空对象原样保留；Schema 是 `MEMORY_LAYER_SCHEMA`。

### `docs/design/05-recording-engine.md`

- :138 的观察分件表行（`memory_layer` 整表一个观察分件）不改。
- :731 的例子「记忆层 `earlier_today[].recorded_at`」不再成立，由第 09 章「分析工具、运行比对与查看器」随 compare_runs 一起改写。

### `docs/manual/`

用户操作不变，不改。

### 起规范作用的模块说明

同期改写：layout.py（新）、memory_layer.py:1、life_prompt.py:1-7、prompt_capacity.py:1-11、narration.py:22-23，原文见「接口与文件」。

## 测试

所有新测试都有中文文档字符串；断言生产英文文字时逐字照抄；失败分支同时用 `caplog` 断言英文日志。真实引擎的测试用现有夹具
`engine_factory`、`tests.life.conftest`、`tests.life.environment_runtime`（`act`、`observe_only`）、
`tests.life.observation_record` 与 `tests.integration.runtime_fixtures.branch_of`。

下面构造条目的单元测试用一个辅助函数 `_entry(time, category, verified=(), notes=(), report=(), concluded=())` 生成
`PublishedEntry`：`item` 按第 02 章的形状（空键省略），`report_only = bool(report)`，`reported = frozenset(report)`，
`concluded = frozenset(concluded)`；字数靠调整「核验」字符串的长度凑到要的值，用 `layer_chars` 核对。
固定部分用 `earliest="2026-10-08"`、`recent={}`。

### 新文件 `tests/unit/life/test_memory_layout.py`

| 测试 | 断言 | 对应行为 |
| --- | --- | --- |
| `test_memory_layer_has_layer_available_recent_today_in_this_order` | `list(assemble_layer("2026-10-08", {}, []))` 等于 `["layer", "可查", "近三天", "今天"]`，值依次是 `"记忆层"`、`{"最早日期": "2026-10-08"}`、`{}`、`[]` | 记忆层的形状 |
| `test_an_absent_earliest_date_gives_an_empty_available_object` | `assemble_layer(None, {}, [])["可查"] == {}`，且 `render_layer_json` 输出 `{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}` | 只读问答 |
| `test_earliest_date_is_the_scenario_local_date_of_the_run_start`，参数化 | `2026-10-08T07:00+08:00` → `"2026-10-08"`；`2026-10-08T00:00+08:00` → `"2026-10-08"`；`2026-10-07T23:00+08:00` → `"2026-10-07"`；`2026-10-08T01:00+08:00`（UTC 仍是 10-07）→ `"2026-10-08"`，时区都是 `Asia/Shanghai` | 可查与最早日期 |
| `test_layer_chars_is_the_length_of_the_rendered_layer_with_its_keys` | 带一条条目和一个「近三天」格子的记忆层：`layer_chars(layer) == len(render_layer_json(layer))`；并且 `user_message([persona, layer, ...])["content"]` 里恰好含 `render_layer_json(layer)` 这一段 | 上限 |
| `test_entries_are_appended_unchanged_while_the_layer_fits` | 五条条目逐条 `fold_entry` 之后，`fold_items(fold)` 等于五个 `item`；每接一条，渲染出的记忆层去掉末尾 `]}` 以后是下一次渲染的前缀 | 「今天」的递推 |
| `test_a_layer_of_exactly_14000_characters_is_not_backstopped` | 本测试把 `layout.MEMORY_BACKSTOP_CHARS` 设成 14000。两个各接三条条目的递推，第一条都带一项 10 字的「自述」：第一个接上第三条后恰 14,000 字，三项与传入的 `item` 逐值相同，没有警告；第二个只把第三条的「核验」多写一个字，接上后恰 14,001 字：第一条的「自述」变为 `["…"]`，第二、三条不变，恰有一条警告 `Memory layer backstop rewrote today from 14001 to 13992 characters (target 14000)` | 兜底·触发 |
| `test_the_backstop_first_blanks_report_notes_whose_activities_were_concluded`，参数化三例 | 列表依次是播报 A 的条目、播报 B 的条目、收尾一项活动的条目，字数使得换掉一项播报自述即达标。收尾 B：换掉的是 B 的播报，A 的播报原样（已收尾的先于更早的未收尾的）；收尾 A：换掉的是 A 的播报；收尾 C（两项都未收尾）：换掉的是列表里更早的 A 的播报。点名 A、B 两项的播报只在 A、B 都收尾时归入前半 | 兜底·第一步前半、后半 |
| `test_the_backstop_then_keeps_only_verified_lines` | 播报都换完仍超时，其余条目从最早的一条起「自述」换成 `["…"]`，「时刻」「类别」「核验」与键序不变 | 兜底·第二步 |
| `test_the_backstop_never_adds_notes_and_never_drops_verified_lines` | 没有「自述」的条目在第一、二步后仍没有「自述」；「自述」原本就是 `["…"]` 的条目不计入改写；第三步以前每条的「核验」逐字不变 | 兜底·细则 |
| `test_the_backstop_merges_same_hour_same_category_items_into_count_lines_last` | 07 时三条「生活」、一条「工作」、一条「工作、饮食」交错排列，第二步之后仍超：先合成 `{"时刻":"07:00–08:00","类别":"生活","条数":3}`，放在第一条「生活」的位置；「工作」与「工作、饮食」各自成组；单条一组也合成，`"条数":1` | 兜底·第三步 |
| `test_count_lines_absorb_earlier_count_lines_and_write_23_to_24` | 先前触发留下 `{"时刻":"23:00–24:00","类别":"生活","条数":2}`，其后又有两条 23 时「生活」条目；再次触发到第三步时合成一行 `"条数":4`，位置是原计数行的位置；只由一行计数行组成的组不动 | 兜底·第三步 |
| `test_the_backstop_stops_at_exactly_12000_characters` | 换掉一项后恰 12,000 字：之后的项都不动；换掉一项后 12,001 字：继续换下一项 | 兜底·目标 |
| `test_backstopped_items_keep_their_reduced_form_and_later_entries_append` | 触发以后再接两条（不再超限）：先前改过的项不恢复，新条目原样接在末尾，两次之间列表只追加 | 兜底·改写之后 |
| `test_a_fold_rebuilt_from_the_first_entry_equals_the_incremental_fold` | 二十条条目，其中两处触发：逐条增量递推与每个 k 都从第一条重新递推，`render_layer_json(assemble_layer(...))` 逐字节相同 | 「今天」的递推 |
| `test_a_backstop_logs_one_warning_with_its_sizes` | 一次触发恰写一条 `Memory layer backstop rewrote today from <前> to <后> characters (target 12000)`，两个数与 `layer_chars` 一致；三步做完仍超 12,000 字时照样保留做完以后的样子，警告里后一个数大于 12000 | 兜底·日志、三步做完仍超 |
| `test_a_memory_layer_above_14000_characters_is_refused` | 14,000 字的记忆层通过 `require_budget`；14,001 字时抛 `RuntimeError`，消息恰为 `Memory layer exceeds 14000 characters`，日志含 `Memory layer has 14001 characters, above its 14000-character budget` | 上限 |
| `test_fold_items_are_copies` | 改动 `fold_items` 返回的项与传入的 `entry.item`，再 `fold_items` 得到原值 | 「今天」的递推 |

### 新文件 `tests/unit/life/test_memory_layer_schema.py`

| 测试 | 断言 | 对应行为 |
| --- | --- | --- |
| `test_memory_layer_schema_accepts_entries_count_lines_and_the_empty_layer` | `validate_layer` 通过：`empty_memory_layer()`；带两条条目（第 02 章形状）、一行计数行和一个近三天格子（第 03 章形状）的记忆层 | 记忆层的形状 |
| `test_memory_layer_schema_refuses_unknown_keys_and_malformed_count_lines`，参数化 | 以下各例都抛 `ValueError`：顶层多一个键；缺 `今天`；缺 `可查`；`可查` 多一个键；`最早日期` 不是日期；`近三天` 有「休息」键；计数行缺「条数」、「条数」为 0、「时刻」是「07:30–08:00」、「时刻」是「07:00–09:00」、「时刻」用 ASCII 连字符写成「07:00-08:00」、「类别」是「休息」、「类别」是「工作、工作」；条目的「类别」是「饮食、饮食」；计数行带「核验」；旧形状 `{"layer":"记忆层","earlier_days":[],"earlier_today":[]}` | 记忆层的形状、计数行 |

### 新文件 `tests/unit/agent_protocol/test_user_layer_order.py`

| 测试 | 断言 | 对应行为 |
| --- | --- | --- |
| `test_user_layer_names_are_persona_memory_todo_tool_environment_progress` | `USER_LAYER_NAMES == ("人设层", "记忆层", "待办层", "工具层", "环境层", "进度层")` | 层序 |
| `test_life_user_layers_follow_user_layer_names` | 观察五个键各放一个 `{"layer": 层名, "marker": 键}` 对象：`life_user_layers` 返回的层名依次等于 `USER_LAYER_NAMES`，第一项就是传入的人设层对象，其余各项就是观察里的同一对象（`is`） | 层序 |
| `test_a_missing_or_misnamed_observation_layer_is_refused`，参数化五个键 | 去掉一个键或把层名写错：抛 `RuntimeError`，消息 `LifeAgent observation layer <键> is unavailable`，日志同文 | 层序 |
| `test_user_layer_returns_the_one_layer_with_that_name` | 对 `life_user_layers` 的结果，`user_layer(layers, name)` 对每个层名返回对应对象 | 层序·按层名取层 |
| `test_user_layer_refuses_a_missing_or_repeated_layer` | 缺「记忆层」、有两个「记忆层」：都抛 `RuntimeError("User layers must carry exactly one 记忆层")`，日志分别含 `User layers carry 0 layers named 记忆层`、`User layers carry 2 layers named 记忆层` | 层序·按层名取层 |
| `test_the_progress_layer_must_be_the_last_user_layer` | 正确层序时返回最后一层；把进度层挪到第一位、或传空列表：抛 `RuntimeError("Progress layer must be the last user layer")`，日志含 `User layers end with 环境层 instead of 进度层`（空列表时为 `None`） | 层序·进度层在最后 |
| `test_empty_memory_layer_is_the_assembled_empty_layer` | `empty_memory_layer() == assemble_layer(None, {}, []) == {"layer": "记忆层", "可查": {}, "近三天": {}, "今天": []}`，两次调用返回不同对象 | 只读问答 |

### 新文件 `tests/unit/agent_protocol/test_prompt_capacity_memory.py`

| 测试 | 断言 | 对应行为 |
| --- | --- | --- |
| `test_condensation_rewrites_only_the_progress_layer` | 按 `USER_LAYER_NAMES` 构造六层：记忆层「今天」有十条条目，进度层有四条未受保护、不带 `self_note` 的记录；用 `monkeypatch` 把 `prompt_capacity.model_context_length` 设成只有浓缩后才放得下：`fit_request` 的结果里 user 消息解析出的前五层与原来逐值相同，记忆层的渲染文本逐字节相同，只有进度层变了（四条记录折成一条 `condensed` 记录） | 容量浓缩 |
| `test_capacity_failure_logs_the_memory_layer_by_name` | 阈值设成浓缩到底也放不下：抛 `RuntimeError("model request capacity exceeded")`，日志 `Model request capacity exceeded` 的 parts 里有键 `记忆层`；记忆层未被改动 | 容量浓缩 |

### 新文件 `tests/integration/life/test_memory_layer_assembly.py`

| 测试 | 断言 | 对应行为 |
| --- | --- | --- |
| `test_the_first_observation_names_the_run_start_date_and_has_no_entries_today` | 新引擎第一次 `observe_only`：`memory_layer["可查"] == {"最早日期": config.start_time.astimezone(ZoneInfo(config.timezone)).date().isoformat()}`（独立于 `earliest_date` 计算），`memory_layer["今天"] == []`，键序是 `layer、可查、近三天、今天`，`validate_layer` 通过 | 记忆层的形状、可查 |
| `test_the_available_date_stays_after_midnight_and_in_a_branch` | `engine_factory(mutate=…)` 让运行从 2026-01-05 20:00 开始、跨过零点：零点前后两次观察的「可查」相同；`branch_of(engine, tmp_path, "resume", source)`（`source` 取父运行任一事件身份）打开的分支里第一次观察的「可查」也相同 | 可查与最早日期 |
| `test_each_decision_only_extends_today_at_the_end_of_the_rendered_layer` | 同一天里依次 `act` 三次（开始、收尾、开始），每次之前 `observe_only`：后一次观察的 `render_layer_json(memory_layer)` 以前一次去掉末尾 `]}` 的文本开头；「今天」每次多一项 | 「今天」的递推、层序 |
| `test_the_observation_path_applies_the_backstop_and_the_budget` | 当天第 1–3 次决策恰为：`act` 在家开始「测试休息」；`advance_until_ready` 推进到它结束（900 秒，其间没有别的决策）后 `act` 收尾它（`summary` 60 字以上，不写记忆与看法）；`act` 再开始「测试休息」。第 4 次决策之前 `observe_only` 量出记忆层字数 L（「今天」三项，第 1、3 项没有「自述」）。清空 `memory_layer._FOLDS`，把 `layout.MEMORY_LAYER_CHARS` 与 `layout.MEMORY_BACKSTOP_CHARS` 都设成 L−1，再 `observe_only`：「今天」恰三项，第 2 项的「自述」恰为 `["…"]`，它的「时刻」「类别」「核验」及第 1、3 项与补丁前逐值相同，没有计数行；`layer_chars` ≤ L−1；`caplog` 恰有一条 `Memory layer backstop rewrote today from <L> to <新字数> characters (target <L−1>)`。再清空缓存，把 `layout.MEMORY_LAYER_CHARS` 设成固定部分（`assemble_layer(可查的最早日期, 近三天, [])`）的字数减一：`observe_only` 抛 `RuntimeError`，消息恰为 `Memory layer exceeds <固定部分字数−1> characters`，日志含 `Memory layer has` 与 `above its` | 兜底、上限 |
| `test_a_new_store_rebuilds_today_byte_for_byte` | 按上一条测试的前半打补丁并触发一次兜底之后：`branch_of` 打开的分支（新存储对象，缓存为空）与清空缓存后的父运行，此刻观察的记忆层与触发后父运行的观察在 `render_layer_json` 下逐字节相同 | 「今天」的递推 |
| `test_today_restarts_when_the_day_the_fixed_part_or_the_entry_count_changes` | 用只带 `agent_id`、`state.current_time`、`config.timezone` 的替身上下文和 `tmp_path` 下的一个 `FootprintStore` 直接调 `memory_layer._today`，条目用与 test_memory_layout.py 相同的 `_entry` 构造。同一存储对象先在 D 日以三条条目、`recent={}` 递推，然后分三例（每例之前恢复到这一状态）：①时刻换到 D+1 日，传入另外三条条目：结果等于清空 `_FOLDS` 后以这三条递推的结果，不含 D 日的项；②同一天把 `layout.MEMORY_LAYER_CHARS` 与 `layout.MEMORY_BACKSTOP_CHARS` 都设成三条条目在 `recent={}` 下的字数，再传同样三条和一份带一格 200 字回顾的 `recent`：结果等于清空 `_FOLDS` 后以同样输入递推的结果（已经过兜底，与 `recent={}` 时的三项不同）；③同一天只传前两条：结果等于清空 `_FOLDS` 后只用前两条递推的结果（两项） | 「今天」的递推·缓存丢弃 |
| `test_each_changed_memory_layer_is_stored_as_one_whole_part` | `record_observations` 记下两次中间隔一次决策的观察：两份存储正文的 `memory_layer` 都是 sha256 且互不相同，`parts` 表里各有一行；读回的观察与构建时的 `memory_layer` 在 `canonical_json` 下相同 | 存储 |

### `tests/integration/runtime/test_life_agent_protocol.py`

- 新增 `test_prompt_snapshot_stores_the_memory_layer_taken_by_name`：`LifeAgent` 设好 `_workspace_root = tmp_path` 后
  `prepare_react_loop`；`life_prompt_snapshot.json` 解析出的 `memory` 等于观察的 `memory_layer`，`persona["layer"] == "人设层"`；
  对应「层序·只读问答快照」。
- `_layers()`（:188-201）的 `memory_layer`（:197）改为 `empty_memory_layer()`：夹具不再带已删除的旧形状。
- 改 `test_react_messages_are_system_layer_and_six_layer_user`（:556）：:570 的层名列表改成 `list(USER_LAYER_NAMES)`；:572-578 的
  `zip` 改为按 `("人设层", None), ("记忆层", "memory_layer"), ("待办层", "todo_layer"), ("工具层", "tool_layer"),
  ("环境层", "environment_layer"), ("进度层", "progress_layer")` 用 `user_layer` 取层比较；:579 的 `layers[1]` 改成
  `user_layer(layers, "人设层")`。

### `tests/integration/runtime/test_seven_layer_request.py`

- 改 `test_current_host_request_has_one_system_layer_and_six_user_layers`（:25）：:34 断言层名等于
  `["人设层", "记忆层", "待办层", "工具层", "环境层", "进度层"]`；:37 改成 `user_layer(layers, "环境层")["body"]`；
  :39 改成 `expand_tool_layer(user_layer(layers, "工具层"))`。工具数与工具次序的断言归第 05 章。
- 新增 `test_consecutive_fresh_requests_share_the_prefix_through_today`：同一天里 `decide` 一次（开始一项在地点做的事），推进到它
  结束，再 `decide` 一次（收尾）；两次都是新快照：第二次请求 user 消息的文本以
  `"[" + render_layer_json(人设层) + "," + render_layer_json(第一次的记忆层)[:-2]` 开头。对应「层序」与「今天」只追加。

### `tests/integration/runtime/test_same_second_continuation.py`（追加）

| 测试 | 断言 | 对应行为 |
| --- | --- | --- |
| `test_a_backstop_inside_a_same_second_chain_starts_the_decision_from_a_fresh_snapshot` | 夹具 `_engine_factory`、`monkeypatch`、`caplog`，用本文件的 `_environment`、`_single`、`_reply`、`_plan_reply`、`_wait_reply` 与 `_PLAN`。开始时 `stage_day_plan(engine)`，同一秒经 `_single` 依次三次决策：①`_single(environment, "plan", _plan_reply())`，它的条目带「正文：…」自述；②`_single(environment, "revise", [_reply("call-revision", "revise_day_plan", _PLAN, "再看一遍计划。")])`，不带回复正文，是续接；③第三次 `_single` 之前，用 `monkeypatch` 把 `layout.MEMORY_LAYER_CHARS` 与 `layout.MEMORY_BACKSTOP_CHARS` 都设成 A，A 是第二次决策的观察里 `memory_layer` 的 `layer_chars`（其「今天」只有第一条条目），于是接上第二条条目后字数必然大于 A、触发兜底，兜底从第二次决策已经看到的第一条条目起改写；然后 `observation, requests, _ = await _single(environment, "wait", _wait_reply())`。断言：第三次观察的「今天」[0] 与第二次观察的「今天」[0] 不同；`observation["continuation"]` 不为 None；`caplog` 恰有一条 `Memory layer backstop rewrote today from <接上后字数> to <回落后字数> characters (target <A>)` 与一条 `Memory layer today list is not an extension of the list the model has seen; using a fresh snapshot`；第三次决策的第一个请求 `requests[0]["messages"]` 恰两条，角色依次是 system、user；`user_layer(json.loads(requests[0]["messages"][1]["content"]), "记忆层")["今天"] == observation["memory_layer"]["今天"]` | 兜底·改写之后；链内触发时断链、改用新快照（第 06 章「进度层、待办层与同秒续接」的判定） |

### 其他要改的现有测试

| 测试 | 改法 | 原因 |
| --- | --- | --- |
| tests/integration/runtime/test_public_runtime_boundaries.py:44 `test_readonly_prompt_uses_saved_snapshot_and_requires_the_persona_card` | 加断言 `memory == {"layer": "记忆层", "可查": {}, "近三天": {}, "今天": []}` 与 `validate_layer(memory)` | 只读问答 |
| tests/unit/agent_protocol/test_decision_context_protocol.py:198 | 记忆层字面量换成 `empty_memory_layer()`；:259 的 `[0]` 换成 `user_layer(…, "工具层")`（记忆工具部分归第 05 章） | 只读问答、层序 |
| tests/integration/runtime/test_unique_presence_run.py:43、test_observation_agent_seam.py:21、test_current_runtime_contract.py:155、test_interaction_menu_roundtrip.py:19、test_conclude_memories.py:413 与 :696 | 解析出的 user 数组按位置取 `[0]` 的地方一律换成 `user_layer(layers, "工具层")` | 层序：工具层从第 1 位移到第 4 位 |
| tests/unit/life/test_narration.py:94 `TestFieldBlacklist.test_activity_names_in_memory_notes_and_outcomes_pass` | 改名 `test_activity_names_in_outcomes_and_chinese_memory_keys_pass`：放行一份新形状的记忆层（含条目、计数行与「可查」）和带 `outcome` 的结果；禁键 `activities` 照旧被拒 | 记忆层的形状 |
| tests/integration/runtime/test_same_second_continuation.py:815 | 按位置取人设层的这一处随第 06 章删除 :795 的关系与偏好叠加测试一起删去 | 层序 |
| tests/integration/reflection_fixtures.py:177 | 按位置取记忆层的这一处随第 08 章删除反思夹具一起删去 | 层序 |
| tests/integration/runtime/test_daily_reflection.py:400-424 | 8000 字符窗口的断言随第 08 章删除这个文件一起删去 | 删除的东西 |

tests/footprints/prompt_fixtures.py:7 的 `LAYER_NAMES` 改为 `('人设层', '记忆层', '待办层', '工具层', '环境层', '进度层')`，与宿主请求的
层序一致；:13 读 `layers[-1]` 不改。test_same_second_continuation.py:290 读 `[4]`、:340 与 :961 读 `[5]`：新层序里环境层仍是第 5 位、
进度层仍是第 6 位，这几处不改。

LifeAgent 经 `last_progress_layer` 取进度层（层序不以进度层收尾时在模型调用之前失败），由第 07 章的
tests/integration/runtime/test_persona_layer_card_only.py::test_progress_layer_must_be_the_last_user_layer 断言，消息与日志用本章的
`Progress layer must be the last user layer`、`User layers end with 人设层 instead of 进度层`。

### 唯一归宿登记：`tests/unit/engineering/test_single_source.py`

`OWNERS` 新增两行：

```python
    'src/worldmock/simulation_engine/observation/memory/layout.py': ('MEMORY_LAYER_CHARS', 'MEMORY_BACKSTOP_CHARS', 'PublishedEntry', 'TodayFold', 'earliest_date', 'assemble_layer', 'layer_chars', 'require_budget', 'fold_entry', 'fold_items'),
    'src/worldmock/agents/prompt/life_prompt.py': ('empty_memory_layer', 'life_user_layers', 'user_layer', 'last_progress_layer'),
```

并在 `'src/worldmock/agents/prompt/prompt_schemas.py'` 名下登记 `('USER_LAYER_NAMES',)`（该文件现在没有登记行，新增一行）。

### 源码变异：`tests/dataset_acceptance.py`

在 `MUTATIONS` 里新增下列变异；每个原文在所指符号的源码里恰好出现一次：

| 名称 | 符号 | 原文 → 替换 | 必须杀死它的测试 |
| --- | --- | --- | --- |
| `memory_backstop_triggers_at_the_budget` | `worldmock.simulation_engine.observation.memory.layout::fold_entry` | `if before > MEMORY_LAYER_CHARS:` → `if before >= MEMORY_LAYER_CHARS:` | tests/unit/life/test_memory_layout.py::test_a_layer_of_exactly_14000_characters_is_not_backstopped |
| `memory_backstop_overshoots_its_target` | 同上 | `<= MEMORY_BACKSTOP_CHARS)` → `< MEMORY_BACKSTOP_CHARS)` | tests/unit/life/test_memory_layout.py::test_the_backstop_stops_at_exactly_12000_characters |
| `memory_backstop_ignores_conclusions` | `…layout::_backstop` | ` and slot.reported <= fold.concluded`（含前导空格）→ 删去 | tests/unit/life/test_memory_layout.py::test_the_backstop_first_blanks_report_notes_whose_activities_were_concluded |
| `memory_backstop_adds_notes` | `…layout::_blank` | `if slot.item.get("自述", ["…"]) == ["…"]:` → `if slot.item.get("自述") == ["…"]:` | tests/unit/life/test_memory_layout.py::test_the_backstop_never_adds_notes_and_never_drops_verified_lines |
| `memory_single_entries_left_unmerged` | `…layout::_merge_hours` | `if len(members) == 1 and members[0].line:` → `if len(members) == 1:` | tests/unit/life/test_memory_layout.py::test_the_backstop_merges_same_hour_same_category_items_into_count_lines_last |
| `memory_layer_budget_unchecked` | `…layout::require_budget` | `if chars > MEMORY_LAYER_CHARS:` → `if False:` | tests/unit/life/test_memory_layout.py::test_a_memory_layer_above_14000_characters_is_refused |
| `memory_budget_counts_today_only` | `…layout::layer_chars` | `return len(render_layer_json(layer))` → `return len(render_layer_json(layer["今天"]))` | tests/unit/life/test_memory_layout.py::test_layer_chars_is_the_length_of_the_rendered_layer_with_its_keys |
| `user_layers_in_the_old_order` | `worldmock.agents.prompt.life_prompt::life_user_layers` | `return [layers[name] for name in USER_LAYER_NAMES]` → `return [layers[name] for name in ("工具层", "人设层", "记忆层", "待办层", "环境层", "进度层")]` | tests/unit/agent_protocol/test_user_layer_order.py::test_life_user_layers_follow_user_layer_names |
| `progress_layer_position_unchecked` | `worldmock.agents.prompt.life_prompt::last_progress_layer` | `if last != "进度层":` → `if False:` | tests/unit/agent_protocol/test_user_layer_order.py::test_the_progress_layer_must_be_the_last_user_layer |
| `prompt_snapshot_takes_a_position` | `custom.agents.life_agent::LifeAgent._prepare_life_action_state` | `user_layer(layers, "记忆层")` → `layers[2]` | tests/integration/runtime/test_life_agent_protocol.py::test_prompt_snapshot_stores_the_memory_layer_taken_by_name |

现有的 `memory_layer_stored_inline`（tests/dataset_acceptance.py:1033-1036）保持不变。

## 不在本章

- 今天条目的形状、核验短句、120 字取舍（规则 B）、核验超过 120 字时的兜底、全文、C_j 与窗口、发布时机，以及 `PublishedEntry`
  各字段的取值：第 02 章「今天条目」。
- `MEMORY_CATEGORIES`、类别推导、归日、零点界、「近三天」的格子（含 Schema 片段 `_recent_cells`、`_RECENT_CELLS`）、取哪几天、
  哪些类别键出现：第 03 章「类别、归日与近三天」。
- 回顾的生成与校验：第 04 章「日、月、年回顾」。
- `search_memory`，包括「早于最早日期时返回空结果」：第 05 章「search_memory 与工具面」。
- 同秒续接的 `changes.memory.added`、前缀判定与断链，进度层的已发布界，待办层的 `orders`：第 06 章「进度层、待办层与同秒续接」。
- 人设层看法移出、系统层与只读系统层的规则原文（包括 rules[0] 里讲各层的那一句）、今天条目的 Schema 片段 `_ENTRY_LINES` 与
  `_TODAY_ENTRY`、`_prepare_life_action_state` 里人设层与已知人物两行：第 07 章「人设层与系统层规则」（记忆层 Schema 的其余部分
  与空记忆层除外，二者归本章）。
- 就寝反思、巩固与三个 `memory_*` 工具的退役：第 08 章「退役项、食材包句与环境包」。
- 查看器的层序（tools/run-viewer-context.js:7-8 的 `USER_LAYERS`、:142-143 的浓缩说明）、compare_runs、memory_pilot.py 的兜底触发计数：
  第 09 章「分析工具、运行比对与查看器」。
- 提示指纹、代码指纹、日运行基线重冻、查看器夹具重录与覆盖门禁：第 10 章「实施：版本、文件、设计文档、测试与门禁」。
- 把「今天」拆成每条一个观察分件：提案写明不在提案内。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 「最早日期」取什么 | 运行开始时刻 `RunConfig.start_time` 的场景本地日期，`earliest_date(start_time, timezone)`；第 05 章的检索共用它 | 提案上限表写「可查」全程不变；11:50 示例的 2026-10-08 就是 V 的开始日期；分支从同一运行配置加载并核对 `config_sha256`，所以全程不变 |
| 只读问答尚无快照时「可查」写什么 | `{}`，Schema 里「最早日期」可选；`empty_memory_layer()` 恰为 `{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}` | 宿主的人物档案里没有运行开始时刻；条目形状「值为空的键省略」；裁定 15 保留这条路径，生产调用方为零 |
| 「渲染后的记忆层」怎样量 | `len(render_layer_json(layer))`，含整层的键名、引号与标点 | 设计原则第 6 条「按 `len` 计，含键名」；提案「渲染后的整个记忆层 ≤14,000 字」；这一段就是 user 消息里记忆层所占的文本 |
| 递推怎样落地 | 当天已发布条目上的纯折叠，按决策次序；进程内缓存只加速，日期、固定部分变了或条目变少就从头折叠 | 裁定 24 的递推原文与「恢复和重放从当天第一次决策起按同样顺序重算，结果逐字节一致」 |
| 「那项活动之后已经收尾」怎样判定 | report_status 点名的活动身份非空，且每一项都已被第 1 至第 k−1 次决策中受理的 conclude_activity 收尾；不读此刻世界状态 | 读此刻世界状态会让冷缓存重算与原来不同；report_status 一次可点名两项，只有都收尾了它的进展才全部过时 |
| 换播报自述与只留核验三轮遇到没有「自述」或「自述」已是 `["…"]` 的项 | 跳过，不新增「自述」 | 兜底的目的是缩短；提案「核验……从不删除」只约束删除，新增 `["…"]` 会让项变长 |
| 按小时合成计数行的「同一本地小时、同一类别串」 | 列表里本地小时与类别串都相同的全部项为一组，不要求相邻；先前的计数行是组员，条数相加；只由一行计数行组成的组跳过；单条一组也合成；组按首项位置先后处理；计数行放在首项的位置；23 时写「23:00–24:00」 | 计数行的「时刻」覆盖整个小时，同一小时同一类别只该有一行；放在首项位置让列表在小时粒度上仍按时间排列 |
| 触发与停止的边界 | 严格大于 14,000 字触发；每改一项重量一次，≤12,000 字即停；三步做完仍超 12,000 字则保留；交出记忆层前再核一次，超过 14,000 字以 `RuntimeError("Memory layer exceeds 14000 characters")` 停止 | 提案「如果会超过 14,000 字」「回落到 ≤12,000 字」「做到达标为止」；设计原则第 6 条「由构建器保证」，而容量浓缩从不改记忆层，构建器以外没有别的机制 |
| 兜底触发要不要留痕 | 每次触发写一条英文警告日志，带前后字数与目标；M6 的触发次数由第 09 章从观察里「今天」不以同日上一条观察的「今天」开头来数，不读日志 | 触发会断链并多一次约 8k token 的未命中；冷缓存重算会重写同一条日志，日志不作计数依据 |
| 兜底放在哪个模块 | layout.py；第三章代码表里 entries.py 的「兜底」指核验超过 120 字时的兜底（第 02 章） | 第三章代码表写 layout.py 管「14,000 字预算、组装」；记忆层兜底要量整层字数 |
| 兜底要的决策事实从哪里来 | layout.py 定义 `PublishedEntry`（条目形状、是否只交 report_status、点名与收尾的活动身份），第 02 章的条目构建器填写 | 兜底先换播报自述，要知道条目是不是播报、点名的活动收尾没有，这些只在条目构建时知道 |
| 层序的唯一来源 | 现有但无人读取的 `USER_LAYER_NAMES` 改成终稿次序，`life_user_layers` 按它返回 | 一条规则只在一处；改前 `life_user_layers` 与 `USER_LAYER_NAMES` 各写一份次序 |
| 「给 `layers[-1]` 加断言」怎样写 | 公开函数 `last_progress_layer`，不符时写错误日志并抛 `RuntimeError`；不用 Python `assert` | `python -O` 会去掉 `assert`；AGENTS.md「异常分支必须有错误日志」 |
| 「按层名取记忆层」怎样写 | 公开函数 `user_layer(layers, name)`，恰好一层同名，否则写错误日志并抛 `RuntimeError`；测试里按位置取层的地方也改用它 | 提案「按层名取记忆层」；层序变了以后按位置的测试会悄悄读错层 |
| `empty_memory_layer` 放在哪里 | 留在 life_prompt.py，由 `assemble_layer(None, {}, [])` 构建 | 提案第三章把它列在 life_prompt.py 一行；记忆层的键名只在 `assemble_layer` 写一次 |
| 8000 字窗口在哪一章删除 | 代码（`MEMORY_WINDOW_CHARS`、`_fit`、`_layer`、`_render`、`_size` 与警告日志）由本章删除；第 08 章「8000 字窗口与反思读者」与它 memory_layer.py 一节里对 `MEMORY_WINDOW_CHARS`、`_fit` 的删除是同一项，只实施一次；06-agent.md 里讲窗口的句子随第 08 章删除反思段落 | 它被本章的 14,000 字上限与兜底直接取代 |
| 新建的设计文档小节 | 06-agent.md「进度、待办与记忆」里新增「记忆层的形状、上限与兜底」，放在第 06 章「进度层的下界与带入的记录」「待办层」之后、第 02 章「今天条目」之前（10 章跨章裁定定下整节的小节次序），本节导语与结果说明不归入它；09-io-formats.md 新增「提示层的机器形状」一节，本章写其中的「记忆层」小节，第 06 章写「待办层」小节 | 提案设计文档表：09-io-formats.md 写「记忆层和待办层的机器形状」，06-agent.md 重写「进度、待办与记忆」；形状只在 09 写一次，行为在 06 |
| 「类别」串与计数行「时刻」的 Schema | `_CATEGORY_TEXT` 是 `MEMORY_CATEGORIES` 里互不相同的一到三项按任意次序以「、」相连的枚举（15 个值），计数行与第 07 章的今天条目片段共用；计数行「时刻」是 `_HOUR_SPANS` 的 24 个值之一 | prompt_schemas.py 说明「枚举直接读取各领域的冻结闭集，Schema 不维护字面副本」；第 02 章【E11】的类别串由互不相同的类别按首次出现的先后相连，主类别可以是任一类；计数行恰覆盖一个本地小时（HH′ = HH+1） |
| 记忆层 Schema 与空记忆层归哪一章 | `MEMORY_LAYER_SCHEMA` 及 `_CATEGORY_TEXT`、`_AVAILABLE`、`_HOUR_SPANS`、`_COUNT_LINE` 与 `empty_memory_layer()` 只由本章定义：空记忆层恰为 `{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}`，「可查」必有、其中「最早日期」可省；今天条目片段 `_ENTRY_LINES`、`_TODAY_ENTRY` 由第 07 章写，「近三天」的 `_recent_cells`、`_RECENT_CELLS` 由第 03 章写，本章引用 | 编排把记忆层的键、次序、JSON 形状与只读空记忆层划给本章；一个名字在 prompt_schemas.py 里只有一个定义；「记忆层恰有四个键」只在「可查」必有时成立 |
| 按位置取层的三处归哪一章 | 本章：`last_progress_layer`、`_visible_names(data)`、`user_layer(layers, "记忆层")` 三行；第 07 章只改人设层与已知人物两行，它的整函数代码块照抄这三行；`_progress_meta` 不核对层名 | 编排把按层名取层与进度层在最后的核对划给本章；同一函数只写一份改动，层名核对只在一处 |
| 「近三天」≤2,900 字 | 只当体量预估，不新增运行时上限；每格 250 字与每类至多三格由第 03、04 章执行，合计受本章 14,000 字上限约束 | 提案只在上限表的「目标上限」列给出这个数，没有描述执行方式；新增一道上限是提案以外的行为。这个数按每格 250 字推算（每格 200 字时满格实测 2,266，加九格各 50 字为 2,716），未实测 |
