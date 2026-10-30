# 07 人设层与系统层规则

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。

## 目标与依据

本章实现[分层记忆提案](../../PROPOSAL_2026-10-29_LAYERED_MEMORY.md)里关于人设层和两份系统层的终稿：

- 第二章「系统层规则原文（终稿，P3 起生效；各期差异见阶段表）」：`LIFE_SYSTEM_LAYER.rules` 的 rules[0]、新增的 rules[1] 与 rules[2]、替换后的 rules[15] 与 rules[21]，`READONLY_SYSTEM_LAYER.rules` 终稿，`TOOL_DESCRIPTIONS` 删去三条 `memory_*`、新增 `search_memory`。
- 第二章「人设层看法的去向（P2）」：人设层只留卡片和静态关系；`persona_views` 留作私有观察证据，供 `known_persons` 与导出。
- 第三章「代码」表中 `agents/prompt/prompt_schemas.py；life_prompt.py；life_persona.py` 一行，以及 `custom/agents/life_agent.py` 一行里的人设路径（人设层的构建与已知人物）；同一行里按位置取层的三处归第 01 章。
- 第三章「设计文档」表中 docs/design/06-agent.md 规则摘要（:74）里「看法在人设层」的说法、docs/design/00-overview.md:138。
- 第三章「事件、版本与下游」表的「指纹」一行在本章范围内的部分。

所依据的裁定：

| 裁定 | 在本章的落点 |
| --- | --- |
| 第一批：`search_memory` 是唯一的记忆工具 | `TOOL_DESCRIPTIONS` 只剩 `search_memory` 一条记忆工具说明；两份系统层都不提 `memory_*` |
| 15（只读路径只留 finish） | 只读系统层第 3 条不再说「查询自己的记忆」，第 4 条照旧要求单独调用 finish |
| 16（新键的语言） | 记忆层 Schema 的键全部用中文，规则文字直接引用这些键；测试核对规则里引用的键都在 Schema 里 |
| 17（三天以外一律经检索，人设层没有看法） | 人设层 Schema 与构建器删去 `preferences` 和关系的 `view` |
| 25（往日看法的原文不另存） | 不新建任何看法列表；`persona_views` 的计算、形状和存储都不变，不进提示 |
| 29（看法移出放在哪一期） | 按编排决定只实现终态：看法不在人设层；提案阶段表里 P1、P2 的临时规则写法一律不实现 |

编排决定 1 规定整份提案的终态一次交付，所以本章写出的规则文字就是提案标为「终稿」的文字；阶段表里「P1」「P2」两行的替换句不出现在代码和测试里。

## 行为

### 人设层

**形状。** 人设层只由人物卡片构建。键依次是：

| 键 | 内容 | 与基线相比 |
| --- | --- | --- |
| `layer` | 「人设层」 | 不变 |
| `identity` | `{name, age, occupation, living_arrangement}` | 不变 |
| `rhythm` | 作息九键，或 null | 不变 |
| `habits` | 至多 5 条 `{trigger, response, time_window}` | 不变 |
| `tastes` | `{content_genres, food_preferences, media}`，或 null | 不变 |
| `coping` | 卡片的应对方式 | 不变 |
| `relationships` | 卡片上的每个人一行，按卡片顺序；每行键依次是 `person`、`role`、`closeness`、`channels` | 删去每行的 `view`；不再追加卡片以外的人 |

- 删去顶层键 `preferences`。
- `relationships` 每行取自卡片：`person` 是 `name_or_appellation`，`role` 是卡片角色，`closeness` 是 `intimacy_text(intimacy)`（很亲近、较亲近、一般、疏远之一），`channels` 是卡片渠道的列表。卡片的 `role` 是 1–12 字的非空文字（src/worldmock/configuration/inputs/models.py:131），所以这四个键都不会为 null。
- 卡片派生字段的 600 汉字上限照旧（life_persona.py:49-53），超出时照旧以 `RenderBudgetExceededError` 失败，不截断。

**不变性。** 人设层不含人物在运行中写下的任何东西。同一次运行里，同一人物每个生活决策请求的人设层，`render_layer_json` 后逐字节相同；收尾写下看法前后也相同。

**看法的去向。** conclude_activity 逐项的 `views`（relationship 与 preference）不进人设层：

- 当天：以「看法·〈对象〉：」出现在那次收尾的记忆层「今天」条目的自述里，取舍与渲染见第 02 章「今天条目」；条目全文的写法同章。
- 过了零点：作为日回顾输入的一部分，写进回顾，见第 04 章「日、月、年回顾」；往日看法经 `search_memory` 查到的是回顾，唯一的例外是按日期查到某一类「未整理」的那一天，这时退回那天这一类的原始条目，其中的「看法·对象：」原样出现（裁定 23），见第 05 章「search_memory 与工具面」。
- 不新建任何「长期」块或带日期的看法列表（裁定 25）。

**`persona_views` 仍是私有观察证据。**

- 计算不变：`memory_layer.persona_views`（src/worldmock/simulation_engine/observation/layers/memory_layer.py:127-144）照旧从成功的 conclude_activity 读出带 `subject` 的 relationship 与 preference 记忆，同一对象按事件顺序取最后一条。
- 形状不变：`{"relationships": [{"person", "view"}], "preferences": [{"subject", "preference", "noted_on"}]}`。
- 存储与导出不变：它照旧是观察顶层键（observe.py:68）、整表观察分件（recording_engine/storage/observation_parts.py:21-22）和事后读取视图要去掉的大载荷（recording_engine/export/post_run.py:13-16），照旧是提示禁键（agents/prompt/narration.py:77）。
- 它的 `preferences` 一半没有任何提示读者；`relationships` 一半只供宿主求已知人物。

**已知人物。** relationship 看法只能写已知人物：

- 已知人物 = 卡片关系名 ∪ `persona_views.relationships` 里的 `person`（`known_persons`，life_persona.py:65-69，函数与签名不变）。
- 收尾写到不认识的人时，照旧逐项以 `SUBJECT_NOT_KNOWN` 纠错（custom/agents/life_agent.py:869-880）。
- 已有关系看法点名过的人也算已知，tests/integration/runtime/test_existing_relationship_subject.py 照旧通过。

### LifeAgent 的人设路径

`LifeAgent._prepare_life_action_state`（custom/agents/life_agent.py:475-492）按下面的次序做，任何一步失败都在模型调用之前失败：

1. 冻结事实窗口与观察身份（`_prepare_verified_experiences`，不变）。
2. 冻结唤醒原因（不变）。
3. `persona = persona_layer(self.get_profile(), self.name)`，不再读 `persona_views`。
4. `layers = life_user_layers(data, persona)`；层序由第 01 章规定。
5. 进度层私有元数据：`_progress_meta(data, last_progress_layer(layers))`。`last_progress_layer` 和它的错误（`RuntimeError("Progress layer must be the last user layer")`）由第 01 章定义；`_progress_meta` 不改。
6. 已知人物与本地校验名称：先 `names = _visible_names(data)`（签名由第 01 章改），再 `known = known_persons(self.get_profile(), _relationship_persons(data))`，最后 `self._life_names = {**names, "known_persons": known}`；私有绑定损坏时先于 `persona_views` 的核对失败。
7. 有工作区时写只读问答快照：`write_prompt_snapshot(self._workspace_root, persona, user_layer(layers, "记忆层"))`；`user_layer` 由第 01 章定义。
8. 续接链注入（`continuation_messages`），不变；续接 `changes` 的键集由第 06 章规定，其中不再有人设增量。

`_relationship_persons(data)` 读观察私有证据 `persona_views` 的关系人物：

- `persona_views` 必须是对象，其 `relationships` 必须是列表，每行必须是带字符串 `person` 的对象。
- 不符时记错误日志 `LifeAgent persona views are invalid`，抛 `RuntimeError("LifeAgent persona views are invalid")`。错误文字与基线的 `_persona_views` 相同。
- 返回各行的 `person`，次序同列表。
- 不再核对 `preferences`，宿主不读它。

**只读问答的人设与记忆。** `readonly_layers`（life_persona.py:78-85）照旧：有快照时原样取快照的 `persona` 与 `memory`；尚无快照时，人设层为 `persona_layer(profile, name)`，记忆层为 `empty_memory_layer()`。

- 快照文件名、写入时机和 JSON 形状 `{"persona": …, "memory": …}` 不变。
- 只读问答的工具面（只剩 finish）由第 05 章规定。

### 生活决策系统层

`LIFE_SYSTEM_LAYER` 的 `layer` 与 `role` 两项不变，源码里这两行的写法也不变。tests/system/viewer/run_viewer.test.cjs:254 与 run_viewer_context.test.cjs:115 用正则 `LIFE_SYSTEM_LAYER[^=]*= \{\s*"layer": "系统层",\s*"role": "([^"]+)"` 从 life_prompt.py 读出 role 原文。

`rules` 从 20 条变成 22 条。下标对照：

| 新下标 | 话题 | 来源（基线 life_prompt.py） |
| --- | --- | --- |
| 0 | 各层写什么 | 替换原 rules[0]（:25-27），原文见下 |
| 1 | 记忆层怎样组织、怎样查 | 新增，原文见下 |
| 2 | 什么时候看记忆 | 新增，原文见下 |
| 3 | 天气 | 原 rules[1]（:28），逐字不变 |
| 4 | 决策怎样结束、何时再请决策 | 原 rules[2]（:29-37），逐字不变 |
| 5 | start_main_activity | 原 rules[3]（:38-41），逐字不变 |
| 6 | start_side_activity | 原 rules[4]（:42-46），逐字不变 |
| 7 | 名称写法与 prefer | 原 rules[5]（:47-49），逐字不变 |
| 8 | 在地点做哪件事、内容 app | 原 rules[6]（:50-52），逐字不变 |
| 9 | 用内容 app 与购买 | 原 rules[7]（:53-56），逐字不变 |
| 10 | 查询 | 原 rules[8]（:57-60），逐字不变 |
| 11 | 两个槽位 | 原 rules[9]（:61-63），逐字不变 |
| 12 | 睡眠 | 原 rules[10]（:64-67），逐字不变 |
| 13 | 出发前 | 原 rules[11]（:68-69），逐字不变 |
| 14 | 收尾、播报、暂停与恢复 | 原 rules[12]（:70-74），逐字不变 |
| 15 | 核验事实与自己的想法 | 替换原 rules[13]（:75-78），原文见下 |
| 16 | 收货与付款 | 原 rules[14]（:79-81），逐字不变 |
| 17 | 解饿 | 原 rules[15]（:82-86），逐字不变 |
| 18 | 作息与今日计划 | 原 rules[16]（:87-89），逐字不变 |
| 19 | available 与只读查询 | 原 rules[17]（:90-92），逐字不变 |
| 20 | 个人事务 | 原 rules[18]（:93-95），逐字不变 |
| 21 | 正文与思考 | 替换原 rules[19]（:96-97），原文见下 |

保留的 17 条里，rules[4] 的「提交前可以查询记忆或回看任务」和 rules[19] 的「查询记忆和回看任务当场完成，不推进时间」原文不改，它们指的是 `search_memory` 和 `recall_task_summary`。

下面五条逐字取自提案第二章「系统层规则原文」。代码按 120 字符行宽拆成相邻字符串字面量，拼接后必须逐字等于下文；表中长度按 `len()` 计，摘要是 UTF-8 编码的 SHA-256：

| 下标 | `len` | SHA-256 |
| --- | --- | --- |
| 0 | 153 | `d57aa60d0239073aa22189ba89b75416a8ea57f84560c230a51cf0c7d70e6b51` |
| 1 | 788 | `ebb774b3f2d69c66360f579a08ff29bc079c3ea8ffd89891835d92cea88b16e5` |
| 2 | 170 | `c1b35ffa989aca497c9b1083f0d008a1386dd1b9220bf3fcb846e973ca813023` |
| 15 | 141 | `c9536d64fe6dc081e4a33d4ff9844c88c0914d23b70cd39faf79c305b316e923` |
| 21 | 63 | `73ec4117944341fff516e75765cce52c4a5cbc8781833c2599d72d030d00c928` |

rules[0]：

> 人设层写你的身份、作息与习惯；记忆层写已经过去的事；待办层写暂停活动、个人事务、在途订单和今日计划；工具层写此刻的对象、动作、条件与代价；环境层写身体感觉、位置、天气和工作安排；进度层写此刻还没了结的事：进行中或待收尾的活动、上次决策之后新到的结果与消息、这段停留或路程里查到的答案，以及今天被拒绝的请求。

rules[1]：

> 记忆层按饮食、工作、生活三类记已经过去的事。「今天」按时刻列出今天的每次决策，一次决策一条，写明类别；一次决策提交后，下一次决策就能看到它的条目，同一时刻接着的决策也一样。「核验」是环境确认的经过；「自述」是你当时写下的话：「名称：」开头的是收尾或暂停时写的总结，「进展·名称：」是 report_status 写的进展，「看法·对象：」是看法，「记住：」是想记住的事，「正文：」是回复正文。每条至多 120 字，写不下的地方以「…」结尾；当天条目太多时，较早的条目会只留核验，或按小时合成一行，写明「条数」；全文用 search_memory 查「今天」。过了零点，每一类当天的条目会整理成一段「回顾」，写那天这类事的经过，以及你的感受、看法和打算；「近三天」每类列最近三天的回顾，饮食和生活取最近三天，工作取最近三个有工作的日子。回顾里的名称、时刻和价钱都在当时的记录里出现过，但搭配和经过可能有误，它是整理出来的回忆，不是核验事实；某一格写着「未整理」，是那天这一类的回顾没有整理出来，不代表那天没有这类事。日子以零点为界：一件事在哪天结束就记在哪天，跨过零点还没结束的事，前一天只记它开始。更早的事不在请求里，「可查」写出能查到的最早日期；要用时调用 search_memory，写类别和时间：时间写某一天（年-月-日）、一段日子（年-月-日~年-月-日，至多 31 天）、某个月（年-月）、某一年（年）、「今天」或今天的一段时刻（今天 时:分~时:分）；查到的往日是那一天、那个月或那一年的回顾，某天某一类「未整理」时给那天的原始条目；可以加关键词，多个关键词用空格隔开，都出现才算查到；时间写「全部」时必须写关键词，比如查上次吃某样东西是哪天、以前对某样东西怎么看。按名称回看一项任务或活动的进展用 recall_task_summary。一次决策至多查三次记忆。

rules[1] 里的「~」是 ASCII 波浪号 U+007E，「时:分」的冒号是 ASCII 冒号 U+003A；前缀里的「：」是全角冒号 U+FF1A，「·」是 U+00B7，「…」是 U+2026。

rules[2]：

> 拿不准时可以先看记忆再定：选吃什么，看看最近几顿吃了什么、当时觉得怎样；接着做工作，看看前几个工作日做到哪里；想照以前的某个看法或习惯做，先看看它是哪天的回顾写到的、后来有没有变，近三天以外的用 search_memory 查。记忆只说明过去发生了什么、你当时怎么想，不替你决定；照旧、重复还是换一换都可以，照人设层的习惯和你现在的想法选。

rules[15]：

> 世界请求登记后由环境结算。进度层的结果和记忆层「今天」里的「核验」是核验事实；自己写的 summary、正文，记忆层的「自述」和「回顾」，都只是你的想法和回忆，不能改变身体、资金、内容进度或订单结果，环境也不按它们限制你此刻能做什么；想法变了，就照现在的想法选，收尾时写下新的看法。

rules[21]：

> 正文可以留空；写了就作为这次决策的自述，从下一次决策起记在记忆层「今天」里。思考只在同一时刻连续决策之间保留，时间推进后丢弃。

整体核对值（compact JSON：`json.dumps(value, ensure_ascii=False, separators=(",", ":"))` 的 UTF-8 编码求 SHA-256）：

| 值 | SHA-256 |
| --- | --- |
| 保留的 17 条 `[*rules[3:15], *rules[16:21]]` | `f4e25c9f0fdce4b05da6b5fe0d20605734ebcd854715c0a621d448875b2fccc9`（基线 rules[1:13] 与 rules[14:19] 的同一值） |
| 终稿 22 条 `rules` | `f4749eeaefea3cf0d66b8575b7fc38d72a5d3a540d69e4d951b516c3210c2bae` |

**规则里没有已删除的机制。** 终稿 22 条里不出现：

- 三个 `memory_*` 工具名；
- 旧记忆层的键 `settled`、`self_note`、`self_notes`、`earlier_days`、`earlier_today`；
- 人设层旧键 `preferences`、`noted_on`；
- 「反思」「巩固」「就寝」；
- 基线文字「此前经历」「这段停留或路程中已经发生的事」「本次调用的自述」。

基线的 20 条本来就不提反思与巩固；替换 rules[0]、[13]、[19] 后，上面这些旧键和旧说法也全部消失。

### 只读问答系统层

`READONLY_SYSTEM_LAYER` 的 `layer` 与 `role`（「你是模拟世界中的一个人物，依据人设层与记忆层回答外部问题。」）不变；`rules` 恰为下面四条，次序即下标：

1. 「请求由人设层、记忆层与外部问题组成。」（与基线 life_prompt.py:106 相同）
2. 「记忆层的「核验」是环境核验的事实；「自述」和「回顾」是你自己写下或整理的，未经核验。」（替换 :107）
3. 「只依据人设层与记忆层回答，不能改变世界，也不写入记忆。」（替换 :108）
4. 「回答完成后单独调用 finish。」（与 :109 相同）

外层的「」只是本文的引号，不属于规则文字。四条的 compact JSON 摘要为 `824b1da806e387c3f1e3726c6a431e828e715f9cf9a46b6c1df4b292bc557a3f`。

### 原生工具用途句

`TOOL_DESCRIPTIONS` 的终稿只在本节定义，第 05 章【S12】引用本节。

`TOOL_DESCRIPTIONS` 恰为下面九个键，次序如下：

| 键 | 用途句 |
| --- | --- |
| `start_main_activity` | 不变 |
| `start_side_activity` | 不变 |
| `recall_task_summary` | 不变 |
| `report_status` | 不变 |
| `pause_activity` | 不变 |
| `conclude_activity` | 不变 |
| `revise_day_plan` | 不变 |
| `search_memory` | 「按类别和时间查自己过去的记忆，可以加关键词，不改变世界。」 |
| `finish` | 不变 |

- 删去 `memory_recent`、`memory_search`、`memory_time_range` 三个键。
- `search_memory` 的用途句与 `SearchMemoryRequest` 的文档字符串 `@brief` 之后的文字逐字相同。这个请求模型由第 05 章定义。
- 新用途句满足 tests/unit/agent_protocol/test_tool_description_references.py 的只写用途门禁：一句、以「。」结尾、没有拉丁字母单词、没有协议说法。它也不是任何一条规则的子串。

### 人设层与记忆层的 Schema

`prompt_schemas.LAYER_SCHEMAS` 的键集不变。本章改两处：

- `PERSONA_LAYER_SCHEMA`：删去 `preferences` 与关系的 `view`；关系行的 `role` 改为非空文字，`closeness` 改为四档闭集 `INTIMACY_LEVELS`，都不再接受 null。其余键不变。
- 记忆层 Schema 里的今天条目片段 `_TODAY_ENTRY`，规则见下表。`MEMORY_LAYER_SCHEMA` 的整体、顶层、「可查」与计数行由第 01 章定义，「近三天」的格子由第 03 章定义；本章不另写。

`TODO_LAYER_SCHEMA`（`orders`）和 `PROGRESS_LAYER_SCHEMA`（去掉 `self_note`）的改动属第 06 章。

今天条目的形状由第 02 章【E8】写出，`_TODAY_ENTRY` 是它的机器形式：

| 位置 | 规则 |
| --- | --- |
| 条目 | 封闭对象，键 `时刻`、`类别`，可选 `核验`、`自述`，二者至少有一；`时刻` 是 `HH:MM`（00:00–23:59）；`核验`、`自述` 是至少一项的数组，每项是 1–120 字的文字 |
| `类别` | 第 01 章的 `_CATEGORY_TEXT`：类别闭集 `MEMORY_CATEGORIES` 的一项或几项，以「、」相连 |

- Schema 不表达「每条核验加自述合计 ≤120 字」和「记忆层合计 ≤14,000 字」。前者由第 02 章、后者由第 01 章的构建器保证并测试；Schema 只做逐串上限。
- 键在渲染中的先后由各构建器决定，Schema 不核对键序。

**空记忆层。** `empty_memory_layer()` 由第 01 章定义（`assemble_layer(None, {}, [])`），恰为 `{"layer":"记忆层","可查":{},"近三天":{},"今天":[]}`；它只用于只读问答尚无快照时（life_persona.py:85）。

### 提示指纹与工具指纹

- `life_prompt_sha256()` 的算法不变（life_prompt.py:172-177）：对 `{"life": LIFE_SYSTEM_LAYER, "readonly": READONLY_SYSTEM_LAYER, "layer_schemas": LAYER_SCHEMAS}` 取排序键的 compact JSON 求 SHA-256。
- 本章改了生活系统层、只读系统层、`PERSONA_LAYER_SCHEMA` 与 `MEMORY_LAYER_SCHEMA` 里的今天条目，所以提示指纹的值改变。第 01、03、06 章对其余 Schema 的改动同样计入。
- `TOOL_DESCRIPTIONS` 不进提示指纹；其中八个生活决策工具的用途句经 `_tool_schema` 的 `description` 进工具指纹 `life_tool_schema_sha256()`（custom/agents/life_agent.py:953-959，按 `_TOOL_MODELS` 取），`finish` 的用途句不进任何指纹（tests/integration/runtime/test_seven_layer_request.py:66-72 照旧钉住）。工具集合由第 05 章改为八个，工具指纹的值随之改变。
- 两个指纹都冻结在运行清单的 `model_identity` 里（src/worldmock/runtime/framework/setup.py:81-89）。基线代码建的运行，在新代码下恢复或重放时，照旧在 `_require_runtime_identity` 以 `IntegrityError("Checkpoint model identity differs from the current LifeAgent contract")` 拒绝（src/worldmock/runtime/lifecycle/runner.py:286-287）。这是预期行为，不做迁移，也不做兼容。
- 测试和夹具里没有字面钉住两个指纹的哈希（已按基线值全仓检索）。间接带着它们的是两份完整日基线和查看器录制样例。按 docs/design/01-architecture.md:263、:268，它们要重冻或用真实宿主重录，执行归第 10 章。
- 系统层与人设层的文字由模型读取，行为效果只能由真实模型试点证明。试点的步骤与指标归第 09 章，本任务不执行。

## 接口与文件

### src/worldmock/agents/prompt/life_prompt.py

- `LIFE_SYSTEM_LAYER`：`rules` 改为 22 条，组成如下：

  ```python
  "rules": [
      <rules[0] 终稿>,
      <rules[1] 新增>,
      <rules[2] 新增>,
      <基线 :28 的字面量，逐字不动>,
      ...                               # 基线 :29-74 的字面量（原 rules[2]–[12]），逐字不动
      <rules[15] 终稿>,
      ...                               # 基线 :79-95 的字面量（原 rules[14]–[18]），逐字不动
      <rules[21] 终稿>,
  ],
  ```

- `READONLY_SYSTEM_LAYER`：`rules` 改为「只读问答系统层」一节的四条。
- `TOOL_DESCRIPTIONS`：改为「原生工具用途句」一节的九个键。
- `empty_memory_layer()`：由第 01 章改写。
- `system_message`、`user_message`、`readonly_messages`、`life_prompt_sha256` 不变。
- `life_user_layers`、`_OBSERVATION_LAYER_KEYS`、`user_layer`、`last_progress_layer` 和模块说明里的层序由第 01 章改写或新增。

### src/worldmock/configuration/persona/persona_text.py

- 在 `intimacy_text`（:86-94）之前新增：

  ```python
  #: 亲密度的四档文字，由高到低。
  INTIMACY_LEVELS = ("很亲近", "较亲近", "一般", "疏远")
  ```

- `intimacy_text` 的阈值不变（≥22、≥14、≥7、其余），四个分支依次返回 `INTIMACY_LEVELS[0]`、`INTIMACY_LEVELS[1]`、`INTIMACY_LEVELS[2]`、`INTIMACY_LEVELS[3]`，不再写字面文字。

### src/worldmock/agents/prompt/prompt_schemas.py

- 新增导入：`from worldmock.configuration.persona.persona_text import INTIMACY_LEVELS`。`MEMORY_CATEGORIES` 的导入由第 01 章新增；基线从 `worldmock.configuration.persona.work_schedule` 已导入 `DUTY_DAYS`、`DUTY_STATUSES`、`WEEKDAY_NAMES`（:21），这一行不变。
- `PERSONA_LAYER_SCHEMA` 的 `relationships` 与 `preferences` 两项（:144-148）改为：

  ```python
          "relationships": {"type": "array", "items": _object({
              "person": _TEXT, "role": _TEXT, "closeness": {"enum": list(INTIMACY_LEVELS)},
              "channels": {"type": "array", "items": _TEXT}})},
  ```

  即删去 `view` 和顶层 `preferences`；`_object` 封闭对象使两者出现时都不合格。
- 今天条目的 Schema 片段 `_TODAY_ENTRY` 写在第 01 章的 `_CATEGORY_TEXT` 之后、`MEMORY_LAYER_SCHEMA` 之前；第 01 章的 `MEMORY_LAYER_SCHEMA` 以 `{"oneOf": [_TODAY_ENTRY, _COUNT_LINE]}` 引用它：

  ```python
  #: 今天条目的一项核验或自述：每条合计至多 120 字（由条目构建器保证），所以单项也至多 120 字。
  _ENTRY_LINES = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1, "maxLength": 120}}
  #: 今天条目：时刻、类别，核验与自述至少有一。
  _TODAY_ENTRY = _object({"时刻": {"type": "string", "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"},
                          "类别": _CATEGORY_TEXT, "核验": _ENTRY_LINES, "自述": _ENTRY_LINES}, optional=("核验", "自述"))
  _TODAY_ENTRY["anyOf"] = [{"required": ["核验"]}, {"required": ["自述"]}]
  ```

  合计 120 字由第 02 章【E8】的条目构建器保证。python-jsonschema 的 `maxLength` 按 `len()` 计，与全文「字」的单位相同。
- `MEMORY_LAYER_SCHEMA`、`_CATEGORY_TEXT`、`_AVAILABLE`、`_COUNT_LINE` 由第 01 章定义，`_NOTE` 由第 01 章删除，「近三天」的 `_RECENT_CELLS` 由第 03 章定义；本章不另写。
- `validate_layer`、`LAYER_SCHEMAS` 的键集不变。`USER_LAYER_NAMES` 由第 01 章处理。

### src/worldmock/agents/prompt/life_persona.py

- 模块说明（:1-9）改写为：

  ```
  """@brief 宿主侧人设层只由必需的结构化人物卡片和姓名构建，整次运行逐字节不变。

  身份、作息、习惯、口味、应对与人物关系（称呼、角色、亲疏、联系渠道）都来自卡片，卡片派生字段受六百汉字上限约束。
  收尾写下的看法与偏好不进人设层：当天以「看法·对象：」写在记忆层「今天」的条目里，过了零点只在回顾里（按日期查到「未整理」的那一天时，检索退回的原始条目里也有）。
  relationship 看法可写的对象来自卡片关系名，以及观察私有证据 persona_views 里已有关系看法的人物。
  只读问答使用最近决策保存的人设层与记忆层快照；尚无快照时使用卡片人设层与空记忆层。
  工作安排只由环境层给出；卡片由人设表直接构造，作息值是人设表的原值，不取整，时刻显示写到分钟。
  规则见 docs/design/06-agent.md「提示与工具」。
  """
  ```

- `persona_layer`：

  ```python
  def persona_layer(profile: Mapping[str, Any], name: str | None) -> dict[str, Any]:
      """@brief 构建人设层对象；键序即渲染顺序，全部取自卡片与姓名。

      @param profile 含必需 persona_card_struct 的人物公开档案
      @param name 人物姓名
      @return 人设层 JSON 对象
      @exception RenderBudgetExceededError 卡片派生的字段超过 600 汉字
      """
      card = card_struct(profile)
      derived = _card_fields(card)
      layer = {
          "layer": "人设层",
          "identity": {"name": name, **derived["identity"]},
          "rhythm": derived["rhythm"], "habits": derived["habits"], "tastes": derived["tastes"],
          "coping": derived["coping"], "relationships": derived["relationships"],
      }
      hanzi = count_hanzi(json.dumps(derived, ensure_ascii=False))
      if hanzi > PERSONA_CARD_HANZI_BUDGET:
          logging.error("Persona card fields exceed the hanzi budget: %d", hanzi)
          raise RenderBudgetExceededError(f"persona card fields exceed budget: {hanzi} hanzi > "
                                          f"{PERSONA_CARD_HANZI_BUDGET} (input-side problem, not truncated)")
      return layer
  ```

- 删除 `_relationships`（:117-123）。
- `readonly_layers` 的回退一行（:84）改为 `persona = persona_layer(profile, name)`。
- `card_struct`、`known_persons`、`write_prompt_snapshot`、`_card_fields`、`_optional_window`、`PROMPT_SNAPSHOT_FILE` 不变。

### custom/agents/life_agent.py

- `_prepare_life_action_state`（:475-492）改为：

  ```python
      def _prepare_life_action_state(self, data: dict[str, Any]) -> None:
          """@brief 从冻结观察组装六层和本地校验所用的私有绑定名称；有续接链时注入本次决策的起始消息。

          人设层只由人物卡片构建；relationship 看法可写的对象另取观察私有证据 persona_views 的关系人物。
          只读问答快照里的记忆层按层名取。
          """
          self._prepare_verified_experiences(data)
          self._life_wake_reasons = frozenset(_visible_wake_reasons(data))
          persona = persona_layer(self.get_profile(), self.name)
          layers = life_user_layers(data, persona)
          self._life_budget = ProgressBudget(tuple(layers), tuple(_progress_meta(data, last_progress_layer(layers))))
          names = _visible_names(data)
          known = known_persons(self.get_profile(), _relationship_persons(data))
          self._life_names = {**names, "known_persons": known}
          if self._workspace_root is not None:
              write_prompt_snapshot(self._workspace_root, persona, user_layer(layers, "记忆层"))
          continued = continuation_messages(data.get(CONTINUATION_KEY), layers)
          if continued is not None:
              self._react_messages = continued
              self._life_budget = replace(self._life_budget, fresh_messages=(system_message(False), user_message(layers)),
                                          chain_length=len(continued))
  ```

- 删除 `_persona_views`（:728-735），新增：

  ```python
  def _relationship_persons(data: dict[str, Any]) -> list[str]:
      """@brief 观察私有证据 persona_views 里已有关系看法的人物（只用于已知人物，不进任何提示层）。"""
      views = data.get("persona_views")
      rows = views.get("relationships") if isinstance(views, dict) else None
      if not isinstance(rows, list) or any(
              not isinstance(row, dict) or not isinstance(row.get("person"), str) for row in rows):
          logging.error("LifeAgent persona views are invalid")
          raise RuntimeError("LifeAgent persona views are invalid")
      return [row["person"] for row in rows]
  ```

- 本函数里 :482（`last_progress_layer`）、:483（`names = _visible_names(data)`）与 :487（`user_layer(layers, "记忆层")`）三行由第 01 章规定，上面的代码块照抄它们；本章改的是 :479-480（人设层只由卡片构建）与 :484（`_relationship_persons`）；续接（:488-492）不改，`changes` 的内容由第 06 章规定。文档字符串按上面的代码块改写。
- `_visible_names` 的签名由第 01 章改为 `_visible_names(data: dict[str, Any])`，它的文档字符串由第 02 章（「custom/agents/life_agent.py（改，只改文档字符串）」）改写；本章不改这个函数。
- 导入：`last_progress_layer`、`user_layer` 从 life_prompt 导入（第 01 章）；`known_persons`、`persona_layer`、`readonly_layers`、`write_prompt_snapshot` 照旧从 life_persona 导入。
- 记忆工具、只读工具面、`search_memory` 派发和检索上限由第 05 章改；反思与巩固的删除和模块说明的改写由第 08 章做。

### 说明文字（不改行为）

| 位置 | 改写后 |
| --- | --- |
| src/worldmock/simulation_engine/observation/layers/memory_layer.py `persona_views` 的文档字符串（:128-130） | 「@brief 收尾写下的关系看法与偏好，作为观察私有证据（不进任何提示层，供宿主求已知人物并随观察存储与导出）：成功的 conclude_activity 写下的看法与偏好（经 control_items 读出的带 subject 的 relationship 与 preference 记忆，由现行请求的 views 派生），同一对象按事件顺序取最后一条（事件照旧只追加，两条都保留，裁决 #33）。」 |
| src/worldmock/state/interaction/control_items.py:20 的注释 | 「#: 看法与偏好两类记忆：请求里写在 views，存储为带 subject 的记忆；不进人设层，也不进进度层。」 |

## 设计文档

### docs/design/06-agent.md「提示与工具」

1. **:74 的规则摘要整句替换。** 同一段的 :75-85（讲睡眠的条目、讲决策怎样结束的条目、唤醒表措辞）不变。替换后的全文：

   > 系统层用规则条目写明：各层写什么，进度层只写此刻还没了结的事；记忆层按饮食、工作、生活三类记已经过去的事，「今天」一次决策一条，提交后从下一次决策起可见，同一时刻接着的决策也一样，「核验」是环境确认的经过，「自述」是人物当时写下的话，五种前缀分别是收尾或暂停的总结、report_status 的进展、看法、想记住的事和回复正文，每条至多 120 字、写不下的地方以「…」结尾，当天条目太多时较早的条目只留核验或按小时合成计数行；过了零点每类当天的条目整理成一段回顾，「近三天」的饮食和生活取最近三天、工作取最近三个有工作的日子，回顾里的名称、时刻和价钱出自当时的记录，搭配和经过可能有误，是回忆而不是核验事实，「未整理」只说明那天这一类没整理出回顾；日子以零点为界，一件事在哪天结束就记在哪天；更早的事和今天条目的全文经 search_memory 按类别、时间和可选关键词去查，按名称回看任务或活动的进展用 recall_task_summary，一次决策至多查三次记忆；拿不准时可以先看记忆再定，记忆只说明过去发生了什么、人物当时怎么想，不替人物决定，照旧、重复还是换一换都可以；每次决策怎样结束，什么时候时钟停着、醒着时什么事会再请人物决策；两个开始工具各填什么，副工具填三项，`prefer` 照工具层 `prefer_apps` 列出的名称写；名称要写完整，`prefer` 只是意向、写得不准不返回候选；在地点做哪件事由人物点名；内容 app 打开后自动连续放，人物结束它才停，找不到能接的内容时这次使用停下、等人物收尾；用内容 app 只是看，买东西只有购买，不知道买什么先查询；两个槽位各放什么；睡眠怎样写；出发前要先结束什么；活动怎样收尾，查询做完自己结束；在地点做的事结束就算做完，只有暂停保留进度；暂停的活动怎样恢复；进度层的结果和「今天」里的核验是核验事实，自己写的总结、正文，记忆层的自述和回顾都只是想法和回忆，不改变世界事实，也不缩小人物此刻能做的事，想法变了就照现在的想法选；解饿只有两种办法，系列哪一集供一餐写在单元行的 `note` 或进行中活动的 `state` 里；今日计划是人物给自己定的打算，环境不判定完成与否，计划里有在工具层和拒绝原因中都看不到做法的事时，人物用 revise_day_plan 去掉它或改成做得到的；`available` 为 `true` 的动作单独提交一定受理，重复与先后环境不另加限制；个人事务只用于要买到或领取的商品，通话、家务之类没有商品的打算不记；正文写了就作为这次决策的自述，从下一次决策起记在记忆层「今天」里，思考只在同一时刻连续决策之间保留。规则文字只在系统层写一次，逐字原文只在代码里；工具说明只写用途与参数格式。

2. **:85 之后新增两段**，各以空行隔开，次序如下。

   人设层一段：

   > 人设层只由人物卡片构建，键依次是 `layer`、`identity`、`rhythm`、`habits`、`tastes`、`coping`、`relationships`；`relationships` 按卡片顺序每人一行，键依次是 `person`（姓名或称谓）、`role`、`closeness`（很亲近、较亲近、一般、疏远之一）、`channels`，卡片以外的人不进人设层。人设层不含人物在运行中写下的任何东西，同一次运行里每个生活决策请求的人设层逐字节相同。收尾写下的关系看法与偏好（conclude_activity 逐项的 `views`）不进人设层：当天以「看法·对象：」写在那次收尾的记忆层「今天」条目里，过了零点只出现在回顾里；经 search_memory 查到的往日是回顾，只有按日期查到某一类「未整理」的那一天时，退回的原始条目里还有当时的「看法·对象：」；不另存带日期的看法列表。环境照旧从成功的收尾算出观察私有证据 `persona_views`，形状 `{relationships: [{person, view}], preferences: [{subject, preference, noted_on}]}`，同一对象取最后一条；它作为观察分件存储并随导出保留，不进任何提示层。宿主只读其中 `relationships` 的人物：这些人物与卡片关系名合起来是已知人物，收尾的 relationship 看法只能写已知人物，写别的名字按 `SUBJECT_NOT_KNOWN` 纠错。

   只读问答一段：

   > 只读问答的 system 是只读系统层，`role` 为「你是模拟世界中的一个人物，依据人设层与记忆层回答外部问题。」，四条规则依次写明：请求由人设层、记忆层与外部问题组成；记忆层的「核验」是环境核验的事实，「自述」和「回顾」是人物自己写下或整理的，未经核验；只依据人设层与记忆层回答，不能改变世界，也不写入记忆；回答完成后单独调用 finish。

3. 本节的层序、工具表与「十个」的说法由第 01、05 章改写；本章新增的两段不写层序和工具数。只读问答的 user、快照与空记忆层由第 01 章、只读问答的工具面由第 05 章写在同一节。

### docs/design/00-overview.md「数据质量原则」

:138「引用完整性」一格里的「relationship、preference 记忆的 `subject` 指向人设层中的对象（relationship 须是已知人物）」改为「relationship、preference 记忆的 `subject` 指向这次收尾点名的对象，relationship 须是卡片上的人物」。同一格里关于就寝反思 `refs` 的分句由第 04 章改写成回顾 `refs` 的规则。

### 手册

本章不改变任何用户操作。docs/manual/personas.md:106 对 `relationships` 的说明（「人物看到的关系，以及记忆里能写到的人」）仍然准确，手册不改。

## 测试

所有新增与改动的测试都必须在本章实现之前失败，实现之后通过。每个测试后注明它守护的行为。

### tests/unit/start_interfaces/test_system_layer_text.py（整份改写）

本文件由本章整份改写，第 05 章测试表里本文件一行（键集与 `memory_*` 退役词）由本节的 `test_retired_wording_is_gone_from_the_system_layers_and_tool_descriptions` 与 `test_tool_descriptions_are_the_nine_purpose_sentences` 承担；用途句的键集、次序与 search_memory 的用途句只在后者钉一次，第 05 章 `test_retired_memory_tools_are_gone` 不再断言 `TOOL_DESCRIPTIONS`。

模块说明改为「系统层恰二十二条，逐字、按序钉住；新五条与提案原文逐字节相同，保留的十七条与基线逐字相同；只读系统层四条；旧文字与已删除的机制不再出现；用途句恰九个键；唤醒表每一行都有系统层的措辞说出；规则引用的记忆层键都在 Schema 里；提示指纹随文字改变。」

- `_RULES`：22 条，次序即下标，内容同「生活决策系统层」一节。注释改为「下标 3–14、16–20 十七条与改动之前（原下标 1–12、14–18）逐字相同」。
- `test_system_layer_is_the_twenty_two_rules_verbatim_in_order`：整表相等、`len == 22`、互不相同；`layer`、`role` 不变；`validate_layer(LIFE_SYSTEM_LAYER)` 通过。守护：生活决策系统层。
- `test_new_rules_are_the_proposal_text_byte_for_byte`：下标 0、1、2、15、21 各自的 `len` 与 SHA-256 等于「生活决策系统层」一节表中的值。守护：五条终稿逐字照抄。
- `test_kept_rules_and_the_whole_list_match_their_digests`：`[*rules[3:15], *rules[16:21]]` 与整份 `rules` 的 compact JSON 摘要分别等于 `f4e25c9f…2fccc9` 和 `f4749eea…0c2bae`（全文见上表）。守护：其余各条不改、下标顺移。
- `test_wake_sentence_names_every_wake_trigger`：`_WAKE_PHRASES` 的下标 2 全部改为 4，10 改为 12，措辞不变。守护：唤醒表与系统层措辞对应。
- `test_readonly_system_layer_is_the_four_rules_verbatim`：`READONLY_SYSTEM_LAYER` 的 `role` 不变，`rules` 等于四条终稿，compact JSON 摘要为 `824b1da8…557a3f`；`validate_layer` 通过。守护：只读问答系统层。
- `test_retired_wording_is_gone_from_the_system_layers_and_tool_descriptions`（由基线的 `test_retired_wording_is_gone_from_the_system_layer_and_tool_descriptions` 改名）：基线的退役词表照旧；两份系统层的全部 rules 与全部用途句拼接后，另外不得含「规则里没有已删除的机制」一段列出的每个词，以及「只能查询自己的记忆」；基线末尾的 `set(TOOL_DESCRIPTIONS)` 断言删去，键集只在下一项钉住。守护：规则不提已删除的机制。
- `test_tool_descriptions_are_the_nine_purpose_sentences`：`list(TOOL_DESCRIPTIONS)` 等于「原生工具用途句」表的九个键、次序相同；`TOOL_DESCRIPTIONS["search_memory"]` 等于该句；`QUERY_MODELS["search_memory"].__doc__.strip() == "@brief " + TOOL_DESCRIPTIONS["search_memory"]`。守护：用途句。
- `test_memory_rules_quote_existing_memory_layer_keys`：
  - 取 rules[0]、rules[1]、rules[2]、rules[15]、rules[21] 与只读系统层四条里全部「」内的文字，去掉以「：」结尾的前缀写法和「…」「全部」，剩下的集合恰为 {今天, 核验, 自述, 条数, 回顾, 近三天, 未整理, 可查}；
  - 这八个都是 `MEMORY_LAYER_SCHEMA` 中某个 `properties` 的键（递归收集）。
  - 守护：裁定 16，规则引用的键与记忆层形状一致。
- `test_prompt_fingerprint_is_recomputable_and_follows_the_sleep_rule`：改动的下标由 10 改为 12（讲睡眠的那一条），其余不变。守护：提示指纹。

### tests/unit/start_interfaces/test_persona_and_memory_schemas.py（新增）

- `test_card_persona_layer_passes_and_carries_no_views`：
  - 取 `tests.profile.conftest.make_card()`（夹具人设表的卡片，关系依次是母亲、老周），经 `persona_card_struct` 放进 profile，调用 `persona_layer(profile, "林舟")`；
  - 键序恰为 `["layer", "identity", "rhythm", "habits", "tastes", "coping", "relationships"]`；
  - 每行关系的键序恰为 `["person", "role", "closeness", "channels"]`，`person` 依次是「母亲」「老周」；
  - `validate_layer` 通过。
  - 守护：人设层形状。
- `test_persona_schema_rejects_views_and_view_only_rows`：参数化四种改动——加顶层 `"preferences": []`；第一行加 `"view": None`；第一行 `role` 改 None；第一行 `closeness` 改 None——每种都使 `validate_layer` 抛 `ValueError`；另断言 `PERSONA_LAYER_SCHEMA["properties"]["relationships"]["items"]["properties"]["closeness"] == {"enum": list(INTIMACY_LEVELS)}`，且 `[intimacy_text(value) for value in (30, 22, 21, 14, 13, 7, 6, 0)]` 依次是 `INTIMACY_LEVELS` 的第 0、0、1、1、2、2、3、3 项。守护：人设层 Schema、亲疏闭集只有一处。
- `test_retired_persona_view_paths_are_gone`：`worldmock.agents.prompt.life_persona` 没有 `_relationships`；`custom.agents.life_agent` 没有 `_persona_views`，有 `_relationship_persons`；`list(inspect.signature(persona_layer).parameters) == ["profile", "name"]`；`PERSONA_LAYER_SCHEMA["properties"]` 没有 `preferences`，`PERSONA_LAYER_SCHEMA["properties"]["relationships"]["items"]["properties"]` 的键恰为 `["person", "role", "closeness", "channels"]`。守护：删除过时代码。
- `test_today_entry_schema_accepts_the_entry_shapes`：下面每个条目单独作为 `empty_memory_layer()` 的「今天」唯一的一项，都通过 `validate_layer`：只带核验的条目；只带自述的条目；两者都带的条目；自述为 `["…"]` 的条目。守护：今天条目的 Schema 接受【E8】的形状。
- `test_today_entry_schema_rejects_malformed_entries`：参数化下列条目，每个照上一项放进「今天」后都使 `validate_layer` 抛 `ValueError`：既无核验也无自述；时刻 `"24:00"`；类别「休息」；核验 `[]`；一项核验 121 字；只带核验的条目另加 `"条数": 1`。守护：今天条目的 Schema 拒绝不合格形状。近三天格子、计数行、顶层与基线形状的拒绝由第 01 章的 tests/unit/life/test_memory_layer_schema.py 与第 03 章的 `test_recent_cell_schema_accepts_only_the_two_cell_shapes` 承担。

### tests/integration/runtime/test_persona_layer_card_only.py（新增）

三个测试共用一个辅助函数：取 `context(engine, label)` 与 `engine.observe`，用 `current_contract_fixtures.person(environment(engine), decision, ScriptedModel([]))` 造 LifeAgent，再 `prepare_react_loop`。写法同 tests/integration/runtime/test_existing_relationship_subject.py。

- `test_persona_layer_is_the_card_and_unchanged_by_conclusion_views(engine_factory)`：
  - 在家开始「测试休息」，推进到待收尾，先取一次准备后的人设层；
  - 再以 preference「纸巾 / 喜欢柔软的纸巾。」和 relationship「母亲 / 她总惦记我吃饭。」两条看法收尾，再取一次；
  - 断言 `observed["persona_views"]` 里有这两条；
  - 两次人设层（按层名从 `agent._life_budget.layers` 取）`render_layer_json` 逐字节相同，且等于 `render_layer_json(persona_layer(profile(), "林舟"))`；
  - 第二次的 user 消息里，人设层部分不含「喜欢柔软的纸巾」「她总惦记我吃饭」；
  - `agent._life_names["known_persons"] == frozenset({"母亲", "老周"})`。
  - 守护：人设层不变性、看法不进人设层、已知人物。
- `test_prompt_snapshot_holds_the_card_persona_layer(engine_factory, tmp_path)`：
  - 给 agent 设 `_workspace_root = tmp_path`，准备一次生活决策；
  - `json.loads((tmp_path / PROMPT_SNAPSHOT_FILE).read_text())` 的 `persona` 等于 `persona_layer(profile(), "林舟")`。
  - 快照的记忆层按层名取，由第 01 章的 `test_prompt_snapshot_stores_the_memory_layer_taken_by_name` 断言。
  - 守护：快照里的人设层是卡片人设层。
- `test_progress_layer_must_be_the_last_user_layer(engine_factory, monkeypatch, caplog)`：
  - 把 `custom.agents.life_agent.life_user_layers` 换成返回 `list(reversed(life_prompt.life_user_layers(data, persona)))` 的函数；
  - `prepare_react_loop` 抛 `RuntimeError("Progress layer must be the last user layer")`，错误日志含 `User layers end with 人设层 instead of 进度层`。
  - 守护：LifeAgent 经第 01 章的 `last_progress_layer` 取进度层。

### 改动的现有测试

| 测试 | 改动 | 守护 |
| --- | --- | --- |
| tests/integration/runtime/test_seven_layer_request.py:32 | `len(rules) == 20` 改为 22；本文件的层序、工具数与工具次序断言由第 01、05 章改 | 真实宿主请求里的系统层 |
| tests/integration/runtime/test_life_agent_protocol.py::test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages（:411-432） | 文档字符串的 rules[9]、rules[2] 改为 rules[11]、rules[4]；句表里的「只是自己的想法，不能改变身体、资金、内容进度或订单结果」改为「都只是你的想法和回忆，不能改变身体、资金、内容进度或订单结果」；:431 的 `rules[9]` 改为 `rules[11]`，:432 的 `rules[2]` 改为 `rules[4]` | 下标顺移、rules[15] 措辞 |
| tests/unit/agent_protocol/test_tool_description_references.py（:21-26、:45-46） | 删去三个 `Memory*Request` 的导入；`_PARAMETER_MODELS = {**REQUEST_MODELS, **QUERY_MODELS, "finish": ReadonlyFinishRequest}`，`QUERY_MODELS` 从 `worldmock.state.interaction.requests` 导入（第 05 章同一行以此为准） | 用途句键集与参数模型一致 |
| tests/integration/runtime/test_agent_branch_coverage.py::test_corrupt_observation_fails_before_any_model_tool（:120-121、:142） | 保留 `data.pop("persona_views")` 一行；`preferences=None` 一行换成 `data["persona_views"].update(relationships=None)` 和 `data["persona_views"]["relationships"].append({"view": "可靠。"})` 两行，匹配文字仍是 `persona views are invalid`；文档字符串的「看法偏好」改为「关系看法」 | `_relationship_persons` 的核对 |
| tests/unit/life/test_shared_observation_contract.py::test_actual_conclusion_views_update_persona_without_duplicate_memory_prose（:225-239） | 改名为 `test_actual_conclusion_views_stay_private_evidence_and_reach_today_entries`；:237-238 不变；:239 改为断言 `render_layer_json(observed["memory_layer"]["今天"])` 里「看法·纸巾：喜欢柔软的纸巾。」和「看法·林舟：提醒自己做事认真。」各出现一次 | 看法当天进今天条目，`persona_views` 不变 |
| tests/integration/runtime/test_persona_profile.py:68、:103、:413、:463 | `persona_layer(profile, name, {"relationships": [], "preferences": []})` 改为 `persona_layer(profile, name)` | 新签名 |
| tests/integration/runtime/test_persona_profile.py:77 | 空断言（`[2:]` 恒为空）改为 `assert [list(row) for row in persona["relationships"]] == [["person", "role", "closeness", "channels"]] * 2` | 关系行只来自卡片 |
| tests/unit/profile/test_textgen.py:156-157 | 同上改为两参数调用 | 新签名 |
| tests/integration/runtime/test_public_runtime_boundaries.py:40 | 同上改为两参数调用 | 新签名 |
| tests/unit/agent_protocol/test_decision_context_protocol.py::test_readonly_loop_uses_only_persona_memory_and_question（:197） | 人设层改为 `persona_layer(agent._profile, "Alice")`；记忆层字面量的替换（:198）归第 01 章、工具面与脚本响应归第 05 章 | 只读回退的人设层 |

### 照旧通过、不改的测试

- tests/integration/runtime/test_existing_relationship_subject.py（已知人物含已有关系看法的人物）；
- tests/integration/runtime/test_conclude_memories.py:540（`persona_views.preferences` 的计算）；
- tests/unit/footprints/test_observation_parts.py、tests/unit/footprints/test_post_run_view.py（`persona_views` 的存储与事后视图）；
- tests/unit/life/test_narration.py:26、:167（`persona_views` 仍是提示禁键）；
- tests/integration/life/test_creation_model_identity.py 的 `_edit_tool_description`、`_edit_system_rule` 与 `test_resume_rejects_a_changed_prompt_or_tool_fingerprint_without_writing`（:110-140），以及 tests/unit/agent_protocol/test_decision_context_protocol.py:43-58（改任一规则或用途句都改变对应指纹）；
- tests/integration/runtime/test_seven_layer_request.py:75-95（任一规则或层 Schema 改变都改变提示指纹）。

## 不在本章

- user 数组的层序、`life_user_layers`、`USER_LAYER_NAMES`、按层名取层的 `user_layer`、`last_progress_layer`、`empty_memory_layer` 和 life_prompt.py 的模块说明：第 01 章「记忆层：形状、预算、兜底与层序」。
- 记忆层各部分的内容和构建：第 01 章「记忆层：形状、预算、兜底与层序」、第 02 章「今天条目」（含「看法·」的渲染与取舍）、第 03 章「类别、归日与近三天」（含 `MEMORY_CATEGORIES`）；`MEMORY_LAYER_SCHEMA` 除今天条目片段以外的部分归第 01、03 章。
- 日、月、年回顾的提示与输入，其中日回顾读人物卡片：第 04 章「日、月、年回顾」。
- `SearchMemoryRequest`、`QUERY_MODELS`、八个原生工具、只读路径只留 finish、检索上限与错误码：第 05 章「search_memory 与工具面」。
- 待办层与进度层的 Schema；续接 `layer_changes`、`_view`、`_overlay` 删去 relationships 与 preferences，以及 `changes.memory`：第 06 章「进度层、待办层与同秒续接」。
- 删除巩固与反思，包括 `LifeAgent._persona_card_text` 的文档（函数保留）、life_agent.py 模块说明里讲反思阶段的一句、06-agent.md:34-35 与「宿主模型协议」里的反思文字：第 08 章「退役项、食材包句与环境包」；00-overview.md:138 的反思分句改写成回顾 `refs` 的规则：第 04 章「日、月、年回顾」。
- 查看器里人设层与记忆层的说明（tools/run-viewer-context.js 的 `persona`、`memory` 两项要改为「人设层只由卡片构建，不带看法与偏好」与终稿记忆层），以及试点指标里人设层的体量统计：第 09 章「分析工具、运行比对与查看器」。
- 两份完整日基线重冻、查看器录制样例重录、各版本号：第 10 章「实施：版本、文件、设计文档、测试与门禁」。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 提案阶段表里 P1、P2 的临时规则写法要不要实现 | 不实现；代码和测试只有提案标为终稿的 rules[0]、[1]、[2]、[15]、[21] 和只读规则四条 | 编排决定 1；提案「系统层规则原文（终稿）」 |
| 关系行的 `role`、`closeness` 是否仍可为 null | 不可：`role` 为非空文字，`closeness` 为四档之一 | 可为 null 只服务于被删除的「只有看法、不在卡片上的人物」一支（life_persona.py:117-123）；卡片 `role` 是 1–12 字（configuration/inputs/models.py:131），`intimacy_text` 只返回四档 |
| 静态关系是否保留 `role` | 保留，四键 `person`、`role`、`closeness`、`channels` | 提案「称呼、亲疏、联系渠道」是举例；`role` 是卡片原有字段，删掉它属于提案以外的改动 |
| LifeAgent 对 `persona_views` 核对到什么程度 | 收窄为只核对 `relationships`；`_persona_views` 改为 `_relationship_persons`，错误文字不变 | 提案（第三章「测试与门禁」「只在收窄 `_persona_views` 时」）留了口；宿主只读 `relationships`；过时代码直接删除（AGENTS.md） |
| 按位置取层的三处 | `layers[-1]` 与按层名取记忆层由第 01 章的 `last_progress_layer`、`user_layer` 实现；本章只改人设层的构建与已知人物（:479-480、:484） | 第 01 章「接口与文件」custom/agents/life_agent.py；一条规则只在一处 |
| `_prepare_life_action_state` 里名称与已知人物的先后 | 先 `names = _visible_names(data)`，再 `known = known_persons(…, _relationship_persons(data))`，再合并 | 第 01 章把 :483 定为 `names = _visible_names(data)`、:485 不变；与基线 :483-485 的次序相同 |
| 记忆层 Schema 由谁写 | 本章只写今天条目片段 `_TODAY_ENTRY`；整份 `MEMORY_LAYER_SCHEMA`、「可查」、计数行由第 01 章写，「近三天」格子由第 03 章写 | 第 01 章 prompt_schemas.py 一节引用 `_TODAY_ENTRY`，第 02 章「记忆层 Schema 里的条目」一行把条目 Schema 代码交给 01、07；一个名字在 prompt_schemas.py 里只能有一个定义 |
| Schema 里的 120 | 写字面数，只做逐串上限；每条合计 120 字由第 02 章保证 | JSON Schema 不能表达跨字符串求和；与本文件现有的 `maxItems: 8` 一样写字面数 |
| 关系亲疏的闭集放在哪里 | `persona_text.INTIMACY_LEVELS`，`intimacy_text` 与 Schema 共用 | prompt_schemas.py:5「Schema 不维护字面副本」；本章改写的正是 :145 这一行 |
| 00-overview.md:138 的写法 | 「指向这次收尾点名的对象，relationship 须是卡片上的人物」 | 提案第三章「设计文档」表 docs/design/00-overview.md:138 一行给定原文；提案「人设层看法的去向」：已知人物集合不会超出卡片；已知人物的代码规则（卡片关系名 ∪ `persona_views.relationships`）写在本章「已知人物」一节与 06-agent.md 人设层一段 |
| `TOOL_DESCRIPTIONS` 的键序 | `search_memory` 排在 `revise_day_plan` 之后、`finish` 之前 | 与第 05 章的工具面次序一致；键序不进任何指纹（提示指纹不含它，工具指纹按 `_TOOL_MODELS` 次序取） |
| 其他写着「看法只在人设层」的代码说明 | 本章改写 memory_layer.persona_views 的文档字符串与 control_items.py:20 的注释；progress_layer._arguments、_tool_record 与 control_items.control_request 由第 06 章删除，narration.py:22-23 由第 01 章改写，`_visible_names` 的文档字符串由第 02 章改写（10 章跨章裁定：此前 02、07 两章各写了一种改法） | 06「接口与文件」progress_layer.py 与 control_items.py；01「接口与文件」narration.py；02「接口与文件」life_agent.py |
| 人设层「≤1,400 字」 | 只当体量预估，不新增运行时上限；卡片 600 汉字上限照旧 | 提案只在「目标上限」列给出这个数，没有描述执行方式；新增一道上限是提案以外的行为 |
| 五条新规则怎样防止抄错 | 测试另钉每条的 `len` 与 SHA-256、保留 17 条与整份 22 条的摘要 | 规则是模型可见文字，录制的响应查不出抄错；摘要由提案原文直接算出 |
| test_life_agent_protocol 里那句「只是自己的想法」断言 | 改为 rules[15] 终稿里的「都只是你的想法和回忆，不能改变身体、资金、内容进度或订单结果」 | 终稿措辞变了；这一句仍只在一条规则里出现 |
