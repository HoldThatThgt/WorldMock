<a name="spec-parallel-activities-同时做两件事开发规格"></a>
# 同时做两件事开发规格

> 状态：FROZEN。本文件是本修复的唯一开发规格；条款随阶段 1–3 的提交逐阶段生效，某阶段落地之前，当前行为与提示文本仍以所属正文与接口合同为准。
> 日期：2026-10-06。
> 代码基线：183326cc68bda388403df61709e7fcd50abb35c9。
> 来源：[GitHub issue #4](https://github.com/HoldThatThgt/WorldMock/issues/4)（运行证据、三次本地试验 `efa50f8`、`4f39246`、`afebabb` 的改动与配对复跑读数、待裁决清单）；本文按 issue 里的用户裁定与对现行合同、规格、设计和代码的逐条核对收敛，试验分支不在本仓库，本文不依赖它的代码。
> 范围：甲 暂停的活动不占并行名额；乙 只有同一种动作互斥；丙 系统层第 8 条原位改写。只落 issue 里已裁定或已给方向的部分，其余待裁决事项见「不在本规格内」。

<a name="spec-parallel-activities-用户得到什么"></a>
## 用户得到什么

人物午休时可以先把剧看起来，外卖到了边吃边看：暂停的上午工作不再占掉一个名额。
吃东西可以配读邮件、刷手机、看剧，工作时可以等人，看剧时可以聊天：环境只拦同一种的两件。
系统层第 8 条告诉人物哪些事环境会拦、哪些要自己按常理判断、两样吃的怎样算一顿，以及暂停的活动不占名额。

```mermaid
flowchart LR
    A[甲 名额<br/>暂停的不占，恢复要有空位] --> B[乙 占用声明<br/>每种动作只占自己的名字]
    B --> C[丙 系统层第 8 条<br/>写清能拦什么、要自己判断什么]
    C --> D[记录<br/>实现状态与文档入口]
    D -.-> E[完整回归<br/>发布环境重新发布后运行]
```

环境不判断跨种组合合不合情理（issue 待裁决 8，用户裁定先只改第 8 条措辞）；两个名额仍是常量 2；交谈与通话仍不能暂停，路上的活动仍不能暂停。

<a name="spec-parallel-activities-裁决记录"></a>
## 裁决记录

用户在 issue #4 里的裁定与方向：

| 编号 | 定稿 |
| --- | --- |
| D1 | 暂停的活动不计入两个并行名额，这是当初实现时对原话的误解；两个名额管的是「同时在做的事」（2026-10-05） |
| D2 | 只有同一种动作互斥（不能吃 + 吃、出行 + 出行），其余都可并行（2026-10-05，方向） |
| D3 | 环境不拦不合情理的跨种组合，先只改系统层第 8 条的措辞，占用声明与引擎不动（2026-10-05，针对第二次试验的占用声明） |

对现行文档与代码核对后的实施取舍。它们沿用试验分支的做法或 issue 写明的正式修复范围，用户可以逐条推翻：

| 编号 | 定稿 |
| --- | --- |
| R1 | 暂停的活动个数不设上限（issue 待裁决 1，按试验分支） |
| R2 | 同刻受限轮的 `resume_or_conclude`、`conclude_only` 两个阶段不再被进入，但留在阶段闭集中，世界结构版本仍为 9（沿用 `conclude_only` 已有的「留在闭集中，现行阶梯不再进入」的写法）；阶梯的按名额分流删除 |
| R3 | 名额超限的下一步按被点名活动能否暂停写：都能暂停写 `Conclude or pause one of … first.`，都不能写 `Conclude one of … first.`，混合时写 `Conclude one of …, or pause …, first.`；能否暂停与 `pause_activity` 的判定同源（进行中、不在路上、不是交谈或通话）。`RESOURCE_CONFLICT` 等别处的下一步不改 |
| R4 | 恢复暂停的活动依次检查：开始地点、工作截止与约束、空名额、占用冲突；没有空名额以 `SESSION_LIMIT_EXCEEDED` 拒绝，原因点名进行中的活动。一个请求里恢复多项时逐项检查，与开始两项的写法一致 |
| R5 | 「同一种」的粒度按试验分支：睡眠与一切互斥；驾车一行与旧表等价（只能再听播客或和人说话）；生活活动整体算一种；食用、制作、使用同算一种（issue 待裁决 3、10） |
| R6 | 旧的身体部位占用名 `idle`、`eyes`、`ears`、`hands`、`voice` 退役，解析时与此前退役的名字一样拒绝；仍带旧占用名的外部环境目录（WorldMock-Dataset 发布的正式环境与各请求自带的目录）加载时被拒绝，要随新占用名重新发布，这是数据集一侧的工作 |
| R7 | 正式目录、场景与语料由 WorldMock-Dataset 发布，不在本仓库，目录版本由发布方决定；引擎的占用声明是包资源 `src/worldmock/data/affordances.toml`，仓库人工测试环境里的副本与它逐字节相同，随之同步并刷新测试环境目录的清单身份（冻结时写的「目录由 5.4 升到 5.5」在与引擎资源分离后的 `main` 合流时改为本条） |
| R8 | 第 8 条取第三次试验（`afebabb`）的文字，全文见「丙」；第 7 条不改，它属于[让「等待」成为例外规格](SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)，等待的默认时长与第 7 条的定稿另议（issue 待裁决 2、5） |
| R9 | 本修复不设独立的验收登记与变异登记，证据由「随实现一起改的测试」与「门禁与平台」给出；含义已不成立的既有用例改名，并同步登记它们的既有台账，其余原位改断言 |
| R10 | 与把引擎资源和发布环境分开的 `main` 合流后，完整日不再与冻结签名逐字节比较，而是逐字段核对实际存储（[环境数据归属规格](SPEC-environment-data-ownership.md)），本修复不需要新的完整日夹具；完整回归要用 `--environment-root` 指向发布环境，仍带旧占用名的发布环境重新发布之后才能运行（阶段 5） |

<a name="spec-parallel-activities-甲名额"></a>
## 甲：暂停的活动不占名额

<a name="spec-parallel-activities-名额规则"></a>
### 规则

- 每人两个并行名额；占名额的是进行中与待收尾的活动，暂停的不占。`conclude_activity` 之后名额释放，`pause_activity` 也让出名额。
- 能结束的活动是进行中、暂停中与待收尾的活动；暂停的活动在开始地点以外仍可直接结束。
- 开始活动、出行与恢复暂停的活动都要有空名额。开始两项而只剩一个名额时照旧拒绝；恢复时按 R4 的次序检查，没有空名额以 `SESSION_LIMIT_EXCEEDED` 拒绝。工具层、待办层的 `available` 与 `reason` 与受理同文。
- `SESSION_LIMIT_EXCEEDED` 的原因点名占名额的活动：只剩一个名额时 `Only one activity slot remains because … occupies a slot.`，名额占满时 `Both activity slots are occupied by ….`；下一步按 R3。未带原因抛出该码时的缺省原因不再提暂停的活动。
- 出发前仍要结束或暂停进行中的活动；只有暂停的活动时可以出发，它们留在原地。
- 同刻决策：到达同刻上限、没有进行中也没有待收尾的活动时，下一阶段一律是开始或恢复（`start_or_resume`），不再按名额分流。待收尾时仍进入只收尾的阶段。

<a name="spec-parallel-activities-名额情形"></a>
### 各种情形

| 情形 | 旧行为 | 新行为 |
| --- | --- | --- |
| 午休时工作暂停，同时开始看剧与吃饭 | 只剩一个名额，整组拒绝 | 受理 |
| 一项进行、一项暂停，再开始一项 | 名额占满拒绝 | 受理 |
| 两项进行，恢复暂停的第三项 | 不会出现（暂停的那项已占名额） | `SESSION_LIMIT_EXCEEDED`，点名进行中的两项；结束或暂停其中一项后可以恢复 |
| 两项都暂停，选择出行 | 名额占满拒绝 | 受理，两项留在原地 |
| 一项进行、一项暂停，选择出行 | 名额占满拒绝 | 出发前仍有进行中的活动，以 `ACTIVITY_PRECONDITION_UNMET` 拒绝，只点名进行中的那项 |
| 两项都暂停，同刻轮数到上限 | 进入「恢复或结束」 | 进入「开始或恢复」 |
| 在别处结束暂停的活动 | 受理 | 受理 |
| 名额被一项通话与一项观看占满 | 下一步写结束或暂停任一项 | `Conclude one of …, or pause …, first.`，暂停只点名观看 |

<a name="spec-parallel-activities-名额落点"></a>
### 代码落点

| 位置 | 改动 |
| --- | --- |
| `src/worldmock/life/activity_control.py` | `occupies_slot` 只认进行中与待收尾；新增「能结束」与「能暂停」的判定，`_pause_refusal`、`_conclude_refusal` 改用它们；名额拒绝抽成开始、出行与恢复共用的一个函数，`_report_refusal` 在工作截止之后、占用冲突之前调用；下一步按 R3；模块与函数文档字符串随改 |
| `src/worldmock/life/session_actions.py` | `_conclude_activity` 用「能结束」判定；文档字符串里「仍占名额」「释放名额」随改 |
| `src/worldmock/life/same_moment.py` | 删去按名额分流的阶梯与 `RoundOutcome` 的名额字段，`next_stage` 在没有进行中与待收尾时返回开始或恢复；`request_kind` 用「能结束」判定；两个阶段在 `_STAGE_REQUEST_KINDS` 里的条目保留 |
| `src/worldmock/life/decision_coordinator.py` | 构造轮次结果时不再传名额字段 |
| `src/worldmock/life/guidance.py` | `SESSION_LIMIT_EXCEEDED` 的缺省原因 |
| `src/worldmock/life/models.py` | `SameMomentStage`、`PauseActivityRequest`、`ConcludeActivityRequest` 的文档字符串；`purpose_only_schema` 剥掉这些说明，工具指纹不变 |

<a name="spec-parallel-activities-乙同一种"></a>
## 乙：只有同一种动作互斥

<a name="spec-parallel-activities-占用声明"></a>
### 占用声明

准入机制不变：注册表显式声明每种动作的占用，同一人物进行中活动的占用交集非空即 `RESOURCE_CONFLICT`。改的只是声明，每种动作占自己的名字：

| 动作 | 声明的占用 | 与谁互斥 |
| --- | --- | --- |
| 等待 | `wait` | 另一段等待 |
| 工作 | `work` | 另一项工作 |
| 观看与收听内容 | `watch`；播客以外的内容另占 `screen` | 另一项观看或收听 |
| 玩游戏 | `play_game` | 另一局游戏 |
| 读信 | `read_mail` | 另一封信 |
| 浏览 | `browse` | 另一次浏览 |
| 食用、制作、使用 | `consume` | 另一项食用、制作或使用（同一请求里同一方式的两样食物照旧合成一顿，只占一个名额） |
| 生活活动 | `lifestyle` | 另一项生活活动 |
| 交谈、通话 | `talk` | 另一段交谈或通话 |
| 步行 | `travel` | 另一段出行 |
| 驾车 | `travel`，另占 `wait`、`work`、`screen`、`play_game`、`read_mail`、`browse`、`consume`、`lifestyle` | 除听播客与说话以外的全部 |
| 睡眠 | 全部十一个名字 | 全部 |

占用闭集为 `wait`、`work`、`watch`、`screen`、`play_game`、`read_mail`、`browse`、`consume`、`lifestyle`、`talk`、`travel` 十一个；`screen` 只用来让驾车拒绝播客以外的内容。

与旧表相比只放开、不收紧：新放开吃 ∥ 生活活动、吃 ∥ 读信或浏览、生活活动 ∥ 读信或浏览或游戏、观看 ∥ 读信或浏览或游戏、观看 ∥ 交谈或通话、等待 ∥ 工作、等待 ∥ 步行、读信、浏览与游戏两两之间、生活活动 ∥ 观看；仍然互斥的是全部同种的两件、睡眠与任何动作、驾车与上表八种。在场、免手、持有与地点等其他准入检查不变（步行 ∥ 工作仍因不在工作地点被拒）。

<a name="spec-parallel-activities-占用落点"></a>
### 代码与数据落点

| 位置 | 改动 |
| --- | --- |
| `src/worldmock/data/affordances.toml` | 十种动作类型的 `resource_claims` 与文件头的占用名说明按上表 |
| `src/worldmock/catalog/affordances.py` | `RESOURCE_CLAIM_SLOTS` 换成十一个名字；五个身体部位名与此前退役的名字一样被拒绝 |
| `src/worldmock/life/social.py` | `SOCIAL_RESOURCE_CLAIMS` 改为 `talk` |
| `tests/fixtures/environment/catalog/affordances.toml`、`tests/fixtures/environment/catalog/manifest.json` | 测试环境的占用声明副本与包资源逐字节同步，清单的目录内容摘要随之刷新（R7） |

准入（`src/worldmock/life/admission.py`）、恢复核对（`src/worldmock/life/models.py`）、候选与观察证据都把占用当作不透明的名字，不改。

<a name="spec-parallel-activities-丙第8条"></a>
## 丙：系统层第 8 条

十八条与下标不变，只原位改写第 8 条。落地后唯一权威文本在 `docs/CONTRACTS.md#contracts-model-request` 的系统层 JSON 块，模块常量 `custom/agents/life_prompt.py::LIFE_SYSTEM_LAYER` 与之逐字相同。

```text
第 8 条
你可以同时做两件事。环境只拦同一种的两件：吃、做饭、用东西算一种，工具层里方式为「进行」的都算生活活动这一种（不看名称），看和听内容算一种，读信、浏览、玩游戏、等待、工作、出行、和人说话各算一种。不同种的两件环境不拦，但没拦不等于做得到，做不做得到由你照常理判断：边吃边看、边吃边刷手机、边做家务边听，做得到；两只手都占着的事，比如洗漱、洗碗、打扫，配不了吃东西，也配不了拿着手机看，只适合再配听的；你自己觉得别扭的组合就不要一起做，一件做完再做另一件。想一起吃或喝的两样东西，在同一个 start_activity 里写成两项，算一顿；已经在吃了，另一样等这一顿吃完再开始。开车时只能再听播客或和人说话，睡觉时不做别的。手上有一件在进行时，轮到你决策就可以再开始一件，原来那件照常进行。同时进行的最多两项，刚结束还没收尾的也算，暂停的不算，恢复暂停的活动要有空位；活动一结束，同一时刻先用 conclude_activity 收尾，再做别的。
```

文本必须同时满足 `tests/integration/test_seven_layer_request.py` 与 `tests/integration/test_tool_description_references.py` 的既有约束（十八条；「待办」「上班安排」「reason」各自的所在条不变；不写工具用途句；不含语言指定词）；在模块常量里写成相邻的普通字符串字面量，不带转义与注释；不重复让「等待」成为例外与个人事务登记的变异原文（「手上的活动照常进行」等），第 8 条用的是「原来那件照常进行」。
`tests/integration/test_life_agent_protocol.py` 里钉住「你最多同时有两项活动」的断言改为钉住新第 8 条的「同时进行的最多两项」只出现在一条规则里。

<a name="spec-parallel-activities-身份与基线"></a>
## 身份、版本与完整日基线

| 部分 | `prompt_sha256` | `tool_schema_sha256` | `code_sha256` | `catalog_content_sha256` | 世界结构 9、质量报告 4、场景 6 |
| --- | --- | --- | --- | --- | --- |
| 甲 | 不变 | 不变 | 变 | 不变 | 不变 |
| 乙 | 不变 | 变（绑定 `src/worldmock/data/affordances.toml` 的字节） | 变 | 测试环境副本变；外部环境由发布方重新发布 | 不变 |
| 丙 | 变 | 不变 | 变 | 不变 | 不变 |

乙落地后，此前的检查点与运行不能再恢复或重放：活动的占用随世界状态保存，恢复时与注册表逐项核对，旧名字以「world activity resource claims disagree with the registry」拒绝。甲、丙落地前后的运行不可合并比较。没有测试、夹具或现行文档钉着指纹的字面值；`catalog/manifest.json` 的目录摘要由清单用例核对。

完整日用例逐字段核对实际存储与导出，不比较冻结签名（R10）；它需要完整世界，以 `--environment-root` 指向发布环境运行，仍带旧占用名的发布环境加载时被拒，要等数据集一侧按新占用名重新发布。

<a name="spec-parallel-activities-现行文档"></a>
## 现行文档原位改写

冲突条款在所属正文原位改写，不留两套同时有效的要求。每份有版本表的现行文档在改写它的阶段在表内末行加一行，行标识 `2026-10-06-parallel-activities-<阶段>`（`slots`、`claims`、`rule8`、`record`），写被改写条款的当前语义；设计章节（05 除外）在本修复第一次改写它时把文件头「版本」末位加一并改写日期；被改写的每份文件在文件头加一行「按同时做两件事规格修订「<节名>」（阶段 N）」，规格名链接到本文件标题锚点 `spec-parallel-activities-同时做两件事开发规格`，后续阶段在同一行补；手册沿用带日期的文件头修订说明。设计章节或接口合同改动的提交都运行 `python scripts/build_design_doc.py` 并提交重新汇编的 `docs/design/worldmock-design.html`。归档、调研、已裁决与未冻结的提案、既有验收记录与冻结的其他特性规格的裁决原文不改。

<a name="spec-parallel-activities-文档甲"></a>
### 阶段 1（甲）

| 文档与节 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md`「状态归属」 | 名额为常量 2，进行中与待收尾的活动各占一个，暂停的不占；阶段闭集照旧五个值，`resume_or_conclude` 与 `conclude_only` 留在闭集中，现行阶梯不再进入 |
| `docs/CONTRACTS.md`「人物工具」 | `pause_activity`：暂停让出名额，恢复要有空名额 |
| `docs/CONTRACTS.md`「活动受理拒绝码」 | `SESSION_LIMIT_EXCEEDED`：进行中与待收尾的活动占满名额；开始、出行与恢复暂停的活动都可能触发；下一步按 R3；删去「两个名额都被暂停活动占满时出行」一支 |
| `docs/CONTRACTS.md`「行动结果」与「七层」的待办层 | 受限轮不再进入 `resume_or_conclude`；待办层恢复的不可用原因增名额占满 |
| `docs/dev/SPEC-life-runtime.md`「活动组合政策」「并行准入」的名额段、「三层结构」、「活动的生命周期」（含状态图的恢复条件、两个名额、两项都暂停时、恢复的写法）、同刻决策规则 3 与实现约定 | 按「甲」的规则与情形改写；规则 3 的阶梯写成：没有进行中与待收尾时开始或恢复 |
| `docs/dev/SPEC-agent-interaction.md`「会话与活动的关系」「稳定工具面」「受限轮」「冻结条款修订」第 2 行与验收台账 | 名额只算进行中与待收尾；暂停让出名额、恢复要有空位；受限轮清单删去两项的进入条件 |
| `docs/dev/SPEC-life-realism.md` 待办层一节 | 恢复的不可用原因增名额占满 |
| `docs/design/01-architecture.md`、`02-exchange-engine.md`、`04-simulation-engine.md`（门店供给的恢复、校验与占用安排、活动生命周期状态图、验收重点）、`09-io-formats.md`（人物状态的阶段与名额） | 同上 |
| `docs/manual/scenarios.md`「修改日期与模拟节奏」「限制人物可以做什么」 | 同刻上限后只开放开始或恢复；暂停的活动不占名额 |
| `tests/fixtures/acceptance_requirements.json` 同刻决策与会话两行的判据文字 | 随文更正（不改节点名与状态列） |

<a name="spec-parallel-activities-文档乙"></a>
### 阶段 2（乙）

| 文档与节 | 改写后的语义 |
| --- | --- |
| `docs/dev/SPEC-life-runtime.md`「活动组合政策」的占用表、「并行准入」的占用段与占用表、同时发生的组合表、通话的占用 | 换成「乙」的表与闭集；同时发生表里观看与游戏改为受理，同种的两件仍拒绝 |
| `docs/dev/SPEC-dayrun-behavior.md` 占用名与占用表、拒绝的组合清单 | 同上 |
| `docs/CONTRACTS.md`「状态归属」的占用名、「交谈与通话」、「活动控制行」 | 十一个占用名；交谈与通话占 `talk` |
| `docs/design/02-exchange-engine.md`、`03-resource-engine.md`、`08-configuration.md`、`09-io-formats.md`、`README.md`、`SUMMARY.md` | 占用名；仍带旧占用名的外部环境要重新发布 |
| `docs/manual/rules.md`「活动声明」 | 占用名 |
| `docs/EXPERIMENT.md`「交互菜单与运行身份」 | 占用声明改变 `tool_schema_sha256`；仍带旧占用名的外部环境要重新发布 |

<a name="spec-parallel-activities-文档丙"></a>
### 阶段 3（丙）

| 文档与节 | 改写后的语义 |
| --- | --- |
| `docs/CONTRACTS.md#contracts-model-request` | 系统层第 8 条换成定稿文本（与模块常量同一提交） |
| `docs/CONTRACTS.md`「七层 Schema」的分阶段指纹叙述 | 补一句：系统层第 8 条原位改写（仍为十八条）使 `prompt_sha256` 重钉，`tool_schema_sha256` 不变 |
| `docs/EXPERIMENT.md`「交互菜单与运行身份」、`docs/dev/SPEC-labelkit-delivery.md` 下游的「版本」一条、`docs/design/04-simulation-engine.md`「当前实现边界」 | 第 8 条改写改变 `prompt_sha256`，工具面与各层 Schema 不变 |

<a name="spec-parallel-activities-文档记录"></a>
### 阶段 4（记录）

`docs/dev/implementation-status.md` 新增本修复一节（范围、三个阶段的提交、证据层级与平台、未执行的完整日基线）；`docs/INDEX.md` 冻结时登记本规格（`FROZEN`），被修订的生活运行时、数字人交互、人物规格、一日行为、LabelKit 交付各行注明修订部分与阶段，阶段落地后写「修订条款已实现」，版本表加行。

<a name="spec-parallel-activities-测试"></a>
## 随实现一起改的测试

| 阶段 | 位置 | 改动 |
| --- | --- | --- |
| 1 | `tests/life/test_location_sessions.py` | 名额占满、只剩一个名额、带暂停活动出行三个用例按新规则改写：两项进行时第三项被拒，暂停一项后受理；一项暂停时开始两项受理；一项进行一项暂停时出行按出发前仍有进行中活动拒绝 |
| 1 | `tests/life/test_same_moment_decisions.py`、`tests/life/test_tool_layer.py`、`tests/life/test_decision_budget.py` | 依赖「两项都暂停进入恢复或结束」的用例改写为两项都暂停到上限后进入开始或恢复、开始被受理；阶梯单元用例去掉名额分支；最后一轮受限请求成功仍耗尽预算的用例改走待收尾路径；含义已不成立的改名并同步 `tests/time_and_energy_acceptance.py` 与 `tests/persona_prompt_acceptance.py` |
| 1 | `tests/life/test_paused_slots.py`（新增） | 经真实请求、引擎与 SQLite：一项暂停时开始两项受理；两项进行时恢复被拒并点名两项、待办层同一原因，结束一项后恢复受理、第三项再被拒；只有暂停的活动时出发受理且它们留在原地；暂停的活动在别处可直接结束；恢复的检查次序（工作截止先于名额、名额先于占用冲突）；下一步的三种写法（含通话不可暂停）；两项暂停到同刻上限后开始被受理 |
| 2 | `tests/catalog/test_affordance_shadow.py` | 期望占用表换新；闭集断言为十一个名字，九个退役名字（此前退役的四个与五个身体部位名）都被拒绝；新增逐对核对：只有同种、睡眠一行与驾车一行有交集 |
| 2 | `tests/life/test_news_feed.py`、`tests/life/test_parallel_acceptance.py`、`tests/life/test_activity_location_kinds.py`、`tests/life/test_domain_branch_coverage.py`、`tests/life/test_state_serialization.py`、`tests/life/test_grounded_observation.py`、`tests/life/test_candidates.py`、`tests/life/test_parallel_admission.py`、`tests/life/test_same_moment_decisions.py` | 旧占用名换新；断言跨种组合被拒的三个用例改为同种组合，另加跨种受理的断言 |
| 2 | `tests/fixtures/environment/catalog/affordances.toml`、`tests/catalog/test_engine_package_resources.py` | 测试环境副本与包资源逐字节同步；包资源的观看占用断言换新 |
| 3 | `tests/integration/test_life_agent_protocol.py`、`tests/fixtures/viewer-context.json` | 见「丙」；查看器夹具 `action` 与 `continuation` 两处系统层换新，`legacy_` 开头的两处不动 |
| 3 | `tests/integration/test_seven_layer_request.py`（新增断言） | 第 8 条含「暂停的不算」「恢复暂停的活动要有空位」「环境只拦同一种的两件」，不再含「进行中、暂停与刚结束的都算」 |

<a name="spec-parallel-activities-验收"></a>
## 验收

| 契约点 | 阶段 | 必须通过的验证 |
| --- | --- | --- |
| 名额只算进行中与待收尾 | 1 | `tests/life/test_paused_slots.py` 的开始与出行用例；改写后的名额用例 |
| 恢复要有空名额，判定与待办层同文 | 1 | `tests/life/test_paused_slots.py` 的恢复与次序用例；工具层既有的受理一致性用例 |
| 暂停的活动仍可结束 | 1 | `tests/life/test_paused_slots.py`；既有的在别处结束用例 |
| 同刻阶梯不再按名额分流 | 1 | 改写后的同刻决策、工具层与预算用例 |
| 只有同一种互斥 | 2 | 逐对核对用例；跨种受理与同种拒绝的引擎用例；驾车与睡眠的既有用例照常通过 |
| 退役占用名被拒、测试环境副本同步 | 2 | 闭集用例；包资源字节绑定用例与测试环境的完整加载 |
| 系统层逐字 | 3 | 实际请求、接口合同与模块常量三者相等，十八条；既有约束全部通过；查看器 Node 用例 |
| 既有登记与变异不回退 | 1–3 | 既有特性的登记守卫；个人事务与让「等待」成为例外的变异执行（未变异时杀手节点全部通过） |
| 文档与汇编 | 1–4 | 设计说明书汇编 `--check`、文档门禁、风格门禁、导入图与单一来源守卫 |

<a name="spec-parallel-activities-实施阶段"></a>
## 实施阶段与提交

| 阶段 | 内容 | 提交 |
| --- | --- | --- |
| 0 冻结 | 本规格与 INDEX 登记 | `docs:` |
| 1 甲 | 先合同与规格（阶段 1 表），再代码与测试；各自一个提交 | `docs:`、`feat(life):` |
| 2 乙 | 先合同与规格（阶段 2 表），再目录、代码、清单与测试；各自一个提交 | `docs:`、`feat(catalog):` |
| 3 丙 | 合同与模块常量同一提交，连同夹具、测试与阶段 3 表 | `feat(prompt):` |
| 4 记录 | 实现状态与 INDEX | `docs:` |
| 5 完整回归 | 数据集一侧按新占用名重新发布环境之后，以 `--environment-root` 运行完整层（含完整日）与覆盖率门禁（R10） | 不产生代码；结果记入实现状态 |

每个代码提交前，以针对文件的 pytest 运行该阶段改动与新增的测试文件、直接相关的测试文件和既有特性的登记守卫；设计汇编 `--check`。

<a name="spec-parallel-activities-门禁与平台"></a>
## 门禁与平台

本机（Windows）`scripts/run_tests.py` 在收集阶段因 TextWorld 没有 Windows 制品失败，按[让「等待」成为例外规格的门禁与平台](SPEC-idle-fillers.md#spec-idle-fillers-门禁与平台)的口径以针对文件的 pytest 代替；文档门禁与字节相关的检查在 `core.autocrlf=false` 的独立检出里执行。标准层、全量层、覆盖率门禁与完整日基线在 POSIX 平台执行，未执行的如实写进实现状态，不写成通过。

<a name="spec-parallel-activities-配对复跑"></a>
## 配对复跑指标

行为结论只能来自真实运行，何时运行由用户决定；以下只报告。基线是 issue #4 第四节的四个运行。

| 指标 | 基线（`8d507fa` r2） | 预期 |
| --- | --- | --- |
| 并行对数、并行分钟 | 2、30 | 上升，接近 `afebabb` 的 6、89 |
| 吃 ∥ 看、吃 ∥ 读邮件 | 0 | 出现 |
| 做不到的组合（吃 ∥ 洗漱、吃 ∥ 洗碗、洗漱 ∥ 读邮件） | 0 | 0 |
| 10 分钟以上的等待 | 2 | 下降 |
| `SESSION_LIMIT_EXCEEDED`、`RESOURCE_CONFLICT` | 1、1 | 不上升 |

守护项：当班时段的观看分钟；暂停活动的个数与最长暂停时长；同刻决策轮数与 `decision_budget_exhausted`；午餐与晚餐各一次；工时与提交；就寝时间。

<a name="spec-parallel-activities-不在本规格内"></a>
## 不在本规格内

issue #4 的下列待裁决事项不由本规格决定，现行行为不变：系统层第 7 条与等待的默认时长（待裁决 2、5）；工具层的正面并行提示（4）；人设卡「饭后」触发词（6）；准入读仓库注册表还是场景目录的声明（7）；环境侧拦不合情理组合的排除项（8）；场景没有播客时「只适合再配听的」落空（9，数据集一侧补播客）；做饭时能否吃（10）；一顿最多两样（11）。

<a name="spec-parallel-activities-已知局限"></a>
## 已知局限

- 引擎受理身体上做不到的跨种组合（吃 ∥ 洗碗）和两个屏幕（刷B站 ∥ 看剧），只靠第 8 条的措辞让人物不选；`afebabb` 的单次复跑里三对做不到的组合消失，其余人物未验证。
- 人物要自己把工具层的条目对到「种」上，对错了没有反馈（issue 待裁决 4）。
- `RESOURCE_CONFLICT` 的下一步仍写「Conclude or pause …」，被点名的是交谈或通话时暂停会被拒（R3 只改名额超限）。
- 暂停的活动没有上限，长期不处理会在待办层堆积（R1）。
- 一个请求里恢复两项而只剩一个名额时，第二项的原因点名第一项（R4）。

<a name="spec-parallel-activities-版本表"></a>
## 版本表

| 版本 | 日期 | 修改 |
| --- | --- | --- |
| `2026-10-06-parallel-activities-freeze` | 2026-10-06 | 按 issue #4 的用户裁定 D1–D3 冻结；实施取舍 R1–R10 沿用试验分支或 issue 的正式修复范围 |
| `2026-10-06-parallel-activities-record` | 2026-10-06 | 阶段 1–3 实现后更正计数：退役的占用名是九个（此前退役的 `position`、`attention`、`device`、`vehicle` 与五个身体部位名），不是十个 |
| `2026-10-06-parallel-activities-reconcile` | 2026-10-06 | 与把引擎资源和发布环境分开的 `main` 合流：占用声明的落点改为包资源 `src/worldmock/data/affordances.toml` 与测试环境副本；目录版本不再由本仓库决定（R6、R7、乙的落点、身份表、阶段 2 的文档与测试行、验收行随之改写） |
| `2026-10-06-parallel-activities-baseline-scope` | 2026-10-06 | 合流后完整日改为逐字段核对实际存储，R10、身份一节与阶段 5 由「POSIX 平台生成新夹具」改为「发布环境重新发布后运行完整回归」；场景结构随环境数据归属规格为 6 |
