<a name="cross-platform-baseline-proposal"></a>
# 完整日行为基线跨平台一致提案

> 状态：未冻结提案；2026-10-05 起草，2026-10-06 按 macOS 导出及环境归属主线修订。本文不修改生产行为，也不代替现行合同与规格；裁决之前，任何代码、测试与验收结论不得依赖其内容。
> 代码基线：`0184c979071d01cb432e8337452106f8147709fc`
> 目标：`tests/integration/test_dayrun_complete_days.py::test_complete_two_person_day_uses_real_tools_memory_and_restoration` 在 Windows 与 POSIX（macOS、Linux x86_64）上对同一份夹具通过；不放宽比较。
> 证据层级：Windows 11（x86_64，Python 3.12.13，AS2 源码身份经 `require_locked_as2` 核对）上对基线源码的实际完整日运行，配合只用于测量、不入库的 pytest 插件；macOS arm64（macOS 27.2，Python 3.12.13，AS2 身份核对通过）上 `b83292f` 的同一插件导出（[HoldThatThgt/WorldMock#5](https://github.com/HoldThatThgt/WorldMock/issues/5)，证据包 sha256 `de76d5cfca1030b30353e1e2984cff3fc808dcd9e20b1460da90352d8ce7b976`）；静态阅读；数学库抽样。`b83292f` 与代码基线只差文档与「让等待成为例外」的登记守卫，生产代码、完整日用例、规范化代码与夹具相同。没有 Linux 运行。
> 本提案尚未实施；测量插件与比较脚本见附录。下文取证绑定上述旧源码及旧输入，不是当前主线的平台验收。
> 当前主线：场景结构 6，正式环境与制作工具位于 WorldMock-Dataset 的 `environment/zhangjiang-baseline/`，模拟必须显式传入场景路径，测试必须显式传入 `--environment-root`。当前完整日保留原有业务断言并核验实际存储、完整导出、资产、检查点与时间线全文；两份历史夹具及签名实现保持原字节，不与当前输出宣称跨版本相等。见[完整日验收边界](SPEC-layering-and-test-tiers.md#layering-baseline)。

<a name="cross-platform-baseline-结论与待裁决事项"></a>
## 一、结论与待裁决事项

在取证源码上，完整日用例在 Windows 上对当时夹具 `tests/fixtures/dayrun-shared-rules-baseline.json` 不符，两粒种子都不符；夹具在 macOS arm64 上生成，macOS 上按同一提交重跑与夹具逐字节相同。该历史输入的原因已经全部找到，一共四处；当前结构 6 组合仍须实际跨平台复验：

| 编号 | 原因 | 影响 | 方案在 POSIX 上是否空操作 |
| --- | --- | --- | --- |
| 1 | 工作区快照按 `sorted(Path)` 归档，Windows 路径比较不分大小写 | 快照素材与引用它的检查点 | 是 |
| 2 | 工作区文本文件以文本模式写入，Windows 上是 CRLF | 快照素材与 `memory_updated` 事件里的文件摘要 | 是 |
| 3 | `AGENT.json` 记下的工作区路径在 Windows 上用反斜杠 | 快照素材 | 是 |
| 4 | **平台数学库不同**：macOS 的 `exp` 在一个难舍入的参数上比正确舍入低一个末位，Windows 给出正确舍入的值 | 身体结算与观察里的 `sleep_pressure`、`sleepiness` 末位；种子 20260105 进入检查点 | **否**：夹具记下的正是 macOS 那个不正确舍入的值 |

在 Windows 上把四处都换成 macOS 的行为之后，两粒种子的完整签名与夹具逐项相同，规范化事件与检查点和 macOS 导出逐字节相同（3.4）：四处完整解释了不符，没有第五个原因。第 4 处是取证之前没有定位的那一个：上一版说「平台数学库已排除」，证据只够排除 Windows 一侧（Windows 的末位差异不进入记录），偏离正确舍入的恰恰是 macOS 一侧；本版更正。

第 4 处改变了本提案的形状。前三处的推荐方案在 POSIX 上逐字节不变，可以不动夹具；第 4 处做不到：要让各平台得到同一个数，就得不再依赖平台数学库，macOS 上的输出必然变化（变成现在 Windows 上的值）。所以**要么接受一份新夹具，要么承认完整日基线只在 macOS 上成立**。数学库的差异也不只关乎测试：同一版本、同一输入在两种操作系统上的正式运行，身体状态从第一处难舍入的参数起就不同，困意分档一旦落在档界附近就会让模型看到不同的提示，这与共享规则规格「操作系统不能改变同一版本的运行身份」的要求相违。

需要裁决的事如下，逐项给出推荐。

| 编号 | 事项 | 选项 | 推荐与理由 |
| --- | --- | --- | --- |
| P1 | 是否立项 | （a）四处都修，生成一份跨平台的新夹具；（b）不修，把完整日基线写明为只在 macOS 上判定 | **（a）。** 只修前三处不能让任何一个平台转绿，修与不修只能整体决定。（b）最省事，但 Linux x86_64 上是否也符合从未验证（glibc 的 `exp` 同样不保证正确舍入），正式运行跨平台不可复现的问题也留着。见 4.4 |
| P2 | 快照归档次序 | （a）生产代码按相对路径各段区分大小写排序；（b）基线规范化里重排归档成员 | **（a）。** 一处改动，POSIX 上逐字节不变；（b）改写资产字节，属于放宽比较。见 4.1 |
| P3 | 工作区文本文件换行 | （a）写入处固定 LF：WorldMock 两处加 AS2 补丁；（b）规范化里把 CRLF 换成 LF；（c）WorldMock 子类覆写 AS2 的写入方法 | **（a）。** （b）修不了事件里按原始字节计算的记忆摘要；（c）复制 AS2 逻辑。见 4.2 |
| P4 | 工作区路径分隔符 | （a）规范化把占位符之后的路径段统一成 `/`；（b）AS2 写 `as_posix()`；（c）删去该字段或改成相对路径 | **（a）。** （b）单独不够，仍要改规范化；（c）改变 POSIX 字节。见 4.3 |
| P5 | 平台数学库 | （a）`life/` 里会进入状态或记录的超越函数与非整数幂，统一改用标准库 `decimal` 实现的正确舍入版本，并加结构守卫；（b）只改睡眠压力的两处 `exp`；（c）把睡眠压力量化到固定位数 | **（a）。** 正确舍入的结果在任何平台上唯一，构造上与平台无关；（b）只修观察到的一处，困意阈值的 `cos` 等同类调用以全精度进入观察，换个输入照样分歧；（c）跨越量化边界时仍会分歧，也要新夹具。见 4.4 |
| P6 | 新夹具怎样生成与证明 | （a）四处修完后从隔离源码生成，两次独立运行分别在 Windows 与 macOS 上，守卫要求回执来自至少两种平台；（b）沿用两次运行同一平台 | **（a）。** 新夹具的意义就是跨平台，证明也该跨平台。新旧夹具的逐字段差异只能落在 `sleep_pressure`、`sleepiness` 的末位与由它们派生的摘要上，否则拒绝生成。见第五节 |
| P7 | 旧夹具怎样过规范化摘要守卫 | （a）新夹具之外的夹具都成为历史夹具，改为绑定生成时的规范化代码；（b）旧夹具都在新规范化代码下重跑 | **（a）。** 分层夹具的源码 `2cf2301` 没有引导脚本与补丁序列，AS2 当时来自私有子模块，已无法按现行方式重建环境；（a）只是如实表述历史证据，对现行回归的约束不放宽。见第五节 |
| P8 | 与当前主线整合的先后 | 当前环境归属与「让等待成为例外」整合验收之后落地；或之前 | **之后。** 已实施的行为与当前结构 6 工件核验先按实际输入验收；本提案若获裁决，再从其隔离源码与同一份显式外部环境生成跨平台新证明。原第三份旧格式夹具计划未执行且已被现行验收替代，不能作为本提案的前提 |

本提案明确不做的事见第八节：不按平台各存一份夹具，不在比较里加容差或删字段，不从合并后输出重录旧夹具。

<a name="cross-platform-baseline-现状与约束"></a>
## 二、现状与约束

### 2.1 取证时用例比较什么

取证源码的完整日用例用脚本化模型经过真实人物循环跑完两人一个自然日，13 小时处做检查点、关闭并重新打开，最后导出。测试侧固定记录用的 UUID 与壁钟，然后用 `tests/dayrun_baseline.py` 的 `DayBaseline` 计算签名：

- 全部导出文件的规范化摘要、全部检查点的规范化摘要、全部运行素材（工作区快照归档）的规范化摘要与大小；
- 规范化只做两件事：把 `str(case)`、`str(project)` 换成 `<case>`、`<project>`；把素材、检查点、运行清单、场景与导出文件的原始摘要一致地换成规范化后的摘要（归档成员逐个规范化，成员次序与元信息保留）。其余字段原样比较，包括事件里的提示正文、工具参数、全精度浮点数和其它原始摘要。

### 2.2 夹具与守卫

- `tests/fixtures/dayrun-shared-rules-baseline.json` 是取证时的回归夹具，源码为 `06e3ea0e04bcbb3022eac820a08005ef32b6b1c8`，在 macOS arm64 上生成；`tests/fixtures/dayrun-layering-baseline.json` 绑定分层改造前源码 `2cf230115f4084aef2376e9d77bc976efbed43ea`，生成平台没有记录。两份都由两次独立运行生成，当前均为不可改写的历史证明，当前结构 6 的完整日不再读取它们比较输出。
- `tests/test_layering_acceptance.py::test_real_day_baseline_was_frozen_only_after_two_matching_runs` 要求两份夹具的 `generation.harness_sha256` 都等于当前 `tests/dayrun_baseline.py` 的字节摘要。规范化代码改一个字节，两份夹具都过不了这条守卫。
- `2cf2301` 上没有 `tests/dayrun_baseline.py`、`scripts/bootstrap.py` 与 `patches/agentsociety2/SERIES`：历史夹具是把规范化代码叠加到该源码上、在当时的 AS2 子模块环境里生成的。
- 「让等待成为例外」原第三份旧格式夹具计划未执行；当前由环境归属的实际工件完整性验收替代（[门禁与平台](SPEC-idle-fillers.md#spec-idle-fillers-门禁与平台)）。未来跨平台证明若立项，必须明确绑定当前结构 6 的同一份外部输入及隔离源码，不能重录旧证明。

### 2.3 约束

- **不能放宽当前工件核验或历史证明。** [完整日验收边界](SPEC-layering-and-test-tiers.md#layering-baseline)：当前输出逐字段等于实际存储、核验原始哈希与时间线全文；同输入行为不变的摘要比较只屏蔽代码身份、运行位置和实测壁钟差异，规范化明确列出。两份历史 JSON 及签名代码保持原字节；不删除业务字段、不从合并后输出重录旧证明。
- **操作系统与换行设置不能改变运行身份。** [共享规则与可重建环境规格](SPEC-shared-rules-and-rebuildable-env.md#shared-rules-特性规格共享规则单一归宿与可重建环境)已有这条要求，原子写也规定 JSON 文件使用 LF；本提案的目标与它一致，现有实现在工作区文件与数学库两处没有做到。
- **AS2 源码摘要是一条链。** AS2 的任何补丁改动都会改变源码摘要 `bb655cb3b4d7a68c725431a507192389b4e6770015e9673d6f5e9ef92dba1c46`，要同步 `patches/agentsociety2/SERIES`、`src/worldmock/run_setup.py` 的 `AS2_LOCKED_SOURCE_SHA256`，以及引用它的 `docs/dev/SPEC-as2-integration.md`、`docs/EXPERIMENT.md` 与冻结的共享规则规格（后者要走其版本表）；每台机器都要重新引导，既有运行因 AS2 身份变化不能续跑。
- **不增加依赖。** 依赖锁的任何变化都牵动引导、收据与两套锁文件；数学库的修复只用标准库。
- **Windows 跑不了分层入口。** `scripts/run_tests.py` 的任何一层在 Windows 上都在收集阶段失败，因为锁定的 TextWorld 没有 Windows 制品（[门禁、可选部件与操作系统](SPEC-shared-rules-and-rebuildable-env.md#shared-rules-门禁可选部件与操作系统)）。Windows 上的证据只能用针对文件与节点的 pytest。

<a name="cross-platform-baseline-证据"></a>
## 三、证据

### 3.1 前三处差异

2026-10-05 实施「让等待成为例外」时，在本机未修改的 `b83292f`（AS2 重新引导并核对身份）上查明：

1. `src/worldmock/footprints/checkpoint.py::snapshot_directory` 按 `sorted(root.rglob("*"))` 的次序写归档成员。`WindowsPath` 的比较不分大小写，`PosixPath` 区分：POSIX 上是 `AGENT.json`、`MEMORY.md`、`config.json`……，Windows 上是 `AGENT.json`、`config.json`……、`MEMORY.md`。
2. AS2 以文本模式写 `MEMORY.md` 等工作区文件，Windows 上换行是 CRLF。
3. `AGENT.json` 记下的工作区根路径在 Windows 上用反斜杠，规范化之后是 `<case>\people\agent_0001`，POSIX 上是 `<case>/people/agent_0001`。

用只测量的插件在本机把三处换成 POSIX 语义（文本写入一律 `newline="\n"`，快照排序按 POSIX 路径规则，占位符之后的反斜杠换成 `/`）之后，两粒种子的运行素材与夹具逐项相同，其余仍不同。

### 3.2 Windows 上排除的因素

以下运行都在代码基线上进行，都带 3.1 的三处模拟，每粒种子单独一个进程，约十分钟。

| 运行 | 源码树 | 正确舍入数学 | 20260105 | 20260106 |
| --- | --- | --- | --- | --- |
| A | 本工作树（`core.autocrlf=true`） | 否 | 素材相同；事件、导出清单、检查点不同 | 素材、检查点相同；事件、导出清单不同 |
| B | 同 A | 是 | 规范化事件与检查点和 A 逐字节相同 | 规范化事件与检查点和 A 逐字节相同 |
| C | `git -c core.autocrlf=false archive` 导出的同一提交，放在 C 盘临时目录 | 否 | 规范化事件与检查点和 A 逐字节相同 | 规范化事件与检查点和 A 逐字节相同 |

- **检出换行、路径长度与所在盘。** A 的签名与上一次会话的测量逐字节相同，而两次运行的项目路径与临时目录长度都不同；C 的项目根换到了另一个盘，没有固定换行属性的检出文件（场景、测试夹具等 225 个）由 CRLF 变成 LF，结果不变。
- **Windows 一侧的数学库偏差。** 抽样：对两万个随机参数，Windows 的 `math.exp`、`cos`、`sin`、`asin`、`pow` 与正确舍入的结果（mpmath 256 位再舍入到双精度）末位不同的次数分别为 96、619、526、0、13。运行 B 把 `src/worldmock/life/sleep.py`、`motivation.py`、`felt.py`、`travel.py` 里的这几个函数与 `**` 全部换成正确舍入的结果并计数：`cos` 分别有 1,616 / 58,773 与 1,216 / 60,292 次、`exp` 有 64 / 25,465 与 38 / 26,040 次、`pow` 有 2 / 18,617 与 4 / 19,281 次与正确舍入不同，`sin`、`asin` 没有调用；输出却与 A 逐字节相同。所以 Windows 的偏差不进入记录。这只证明了 Windows 一侧；3.3 表明偏离正确舍入、并进入记录的是 macOS 一侧。
- **规范化事件里的残留。** A 的规范化事件里没有回车；反斜杠只出现在 JSON Schema 的正则 `pattern` 里；没有盘符、临时目录、用户名或项目路径片段；没有平台、解释器或依赖版本字样。

### 3.3 macOS 导出与逐条比较

macOS 上 `release` 环境 `2 passed`，四个导出的摘要都等于夹具：

| 种子 | 规范化事件 | 规范化检查点 |
| --- | --- | --- |
| 20260105 | `8baae5c1c3a3…`（1,278 条） | `eeb81a6507be…`（3 条） |
| 20260106 | `09c1afddbd6d…`（1,314 条） | `2c00625283bf…`（3 条） |

用附录 B 的脚本把它与 Windows 运行 A 的导出逐条比较。两边的记录条数相同，事件类型、正文、提示、工具参数全部相同；不同的只有四个浮点字段，全部是末位：

| 种子 | 第一处不同 | 不同的记录 | 不同的字段 |
| --- | --- | --- | --- |
| 20260105 | 第 534 号事件（人物 1 的身体结算） | 141 条（结算 85、观察 56），两个人物，延续到当天结束 | 结算前后的 `sleep_pressure`，观察里的 `sleep_pressure`、`sleepiness`；第 2、3 个检查点里的 `body.sleep_pressure`（人物 2 两处、人物 1 一处） |
| 20260106 | 第 669 号事件（人物 1 的身体结算） | 8 条（结算 6、观察 2），只有人物 1，到第 730 号事件为止 | 同上；之后两边的值在舍入中重新合一，检查点相同 |

导出清单记录 `events.jsonl` 的规范化摘要，它的不同完全是事件不同的派生结果。

两粒种子的第一处不同是同一个运算：一段 933 秒的清醒结算，`src/worldmock/life/sleep.py::pressure_after_awake` 计算 `exp(-0.25916… / 18.2)`，参数为 `-0.01423992673992674`，两边结算前的值逐位相同。这个参数的真值是 `0.98586098147382134068…`，距离两个相邻双精度数的中点只有 0.0007 个末位：

| 来源 | `exp(-0.01423992673992674)` | 与真值的误差 |
| --- | --- | --- |
| 正确舍入、Windows UCRT | `0.9858609814738214` | 0.4993 个末位 |
| macOS arm64（由结算结果反推） | `0.9858609814738213` | 0.5007 个末位 |

macOS 的结果仍在通常「不超过一个末位」的精度承诺之内，只是不是正确舍入；Windows 恰好给出了正确舍入的值。用 macOS 的值代入，两粒种子的结算结果与 macOS 逐位相同。

### 3.4 确认实验

在 Windows 上用附录 A 的插件（带前三处模拟），另把 `worldmock.life.sleep` 的 `exp` 在这一个参数上换成低一个末位的值、其余参数照旧，两粒种子各跑一次。这个参数在每粒种子里出现两次（两个人物各一次），其余 `exp` 调用（约两万三千与两万四千次）都用 Windows 自己的结果。

| 种子 | 完整签名与夹具 | 规范化事件与 macOS 导出 | 规范化检查点与 macOS 导出 |
| --- | --- | --- | --- |
| 20260105 | 逐项相等 | 逐字节相同 | 逐字节相同 |
| 20260106 | 逐项相等 | 逐字节相同 | 逐字节相同 |

所以 Windows 与 macOS 在这两粒种子上的全部差异就是这四处：三处平台行为与 macOS 数学库在一个参数上的末位。

### 3.5 静态排查

在完整日会走到的代码里找能让两个平台确定地不同的写法，结论与上面的实测一致：

- **目录列举的次序。** 除快照之外，可能经过这条路径的列举只有 `src/worldmock/categories/identity.py` 读品类规则（排序 `Path`，文件名全小写）与 AS2 的技能注册（排序 `Path`，只有一个全小写的技能目录；资源按 `casefold` 排序）。
- **平台分支、本地时区与平台专属依赖。** WorldMock 只有原子写的父目录刷盘、LabelKit 路径与品类维护工具分平台，AS2 只有工作区检索工具在 Windows 上不用 `rg`，都不在这条路径上；时刻换算都带显式 `ZoneInfo`；依赖锁里没有 uvloop 一类只在 POSIX 上启用的组件。
- **超越函数。** `life/` 里调用平台数学库的只有四个模块：`sleep.py`（`exp` 两处，昼夜相位 `cos` 一处，后者经入睡与醒来阈值进入困意）、`felt.py`（食欲昼夜 `cos`、情绪半衰的非整数幂）、`motivation.py`（softmax 的 `exp`、损失厌恶与折现的非整数幂）、`travel.py`（球面距离的 `sin`、`cos`、`asin`）。

<a name="cross-platform-baseline-逐项方案"></a>
## 四、逐项方案

<a name="cross-platform-baseline-快照次序"></a>
### 4.1 快照归档次序（P2）

`PurePath` 的比较按路径各段逐段进行；Windows 先把各段转成小写，POSIX 保持原样。工作区里同时有大写开头的 `AGENT.json`、`MEMORY.md` 与小写开头的 `config.json` 等文件，于是两个平台的归档成员次序不同，归档字节、素材摘要与引用它的检查点都随之不同。

- **（a）生产代码按相对路径各段排序（推荐）。** 排序键取「相对工作区根的各段」，各段按原样比较。这与 `PosixPath` 的比较规则完全相同（逐段比较，不是整串比较：`a/b` 与 `a-c` 两种规则的先后相反），所以 POSIX 上的归档逐字节不变。改动一行，另加一条单元测试：大小写混合的文件名在任何平台上得到同一归档次序，Windows 上可直接验证。恢复不依赖成员次序，已有检查点照常可读。
- **（b）规范化里按名称重排成员。** 成员次序是资产字节的一部分，重排就是放宽比较；还要改规范化代码。不推荐。

同一写法另见 `src/worldmock/delivery/pipeline.py` 的交付清单与 `src/worldmock/validation.py` 的模式列表，不在完整日路径上；是否一并改，由裁决决定。

<a name="cross-platform-baseline-换行"></a>
### 4.2 工作区文本文件换行（P3）

Windows 上文本模式写入把 `\n` 译成 `\r\n`。会写出多行内容的工作区文件有：

| 文件 | 写入者 |
| --- | --- |
| `MEMORY.md`、`memory/state.json` | AS2 `AgentMemoryStore` 的 `ensure`、`write_memory_view`、`write_state` |
| `config.json`、`AGENT.json` | AS2 `AgentBase.create`；之后的 `AGENT.json` 经 AS2 `WorkspaceFS.write_text` |
| `memory/episodes.jsonl` | WorldMock `custom/agents/life_memory_store.py` 的 `LifeMemoryStore._persist_episode_records`（追加）与 `_rewrite_episodes_file`（重写） |

`life_prompt_snapshot.json` 是单行 JSON，不受影响。换行不只影响快照字节：`custom/agents/life_memory.py::memory_update_evidence` 把 `MEMORY.md`、`episodes.jsonl`、`state.json` 原始字节的摘要写进每条 `memory_updated` 事件（`memory_before_sha256`、`memory_sha256`、`episodes_sha256`、`state_sha256`），这些是写入当时的文件内容身份。

- **（a）写入处固定 LF（推荐）。** WorldMock 两处、AS2 补丁中 `agent/memory.py`、`agent/base/agent.py`、`agent/base/workspace_fs.py`（`write_text` 与 `append_text`）几处加 `newline="\n"`；AS2 的 `agent/base/todo.py` 同理，完整日没有启用待办，但规则应一致。POSIX 上逐字节不变。补丁建议新增一份追加在 `SERIES` 末尾，便于单独审查；顺带修正 `WorkspaceFS.write_text` 在 Windows 上少报的 `bytes_written`。成本是 2.3 所列的 AS2 摘要链与全部机器重新引导。可另加一条结构守卫：`src/` 与 `custom/` 里以文本模式写文件必须显式给出 `newline`，防止回归。
- **（b）规范化里把 CRLF 换成 LF。** 只能修归档成员，修不了事件里的原始字节摘要：规范化只看得到检查点时刻的快照，看不到每次写入时的文件，无法重算这些摘要。还要改规范化代码，并让比较变宽。不推荐。
- **（c）WorldMock 子类覆写 AS2 的写入方法。** 避开 AS2 摘要变化，但要在 `LifeMemoryStore` 与 `LifeAgent` 里复制 `ensure`、`write_state`、`write_memory_view`、`create` 与 `persist_agent_json` 的逻辑，形成两份实现；共享规则特性的方向正是同一条规则只有一个实现。不推荐。

<a name="cross-platform-baseline-路径分隔符"></a>
### 4.3 工作区路径分隔符（P4）

AS2 `AgentBase.build_agent_json` 把 `str(self.workspace_root_path())` 写进 `AGENT.json` 的 `workspace.root`。这个字段只写不读，AS2 与 WorldMock 都不读回。规范化用 `str(case)` 替换出 `<case>`，Windows 上剩下的路径段仍是反斜杠。

- **（a）规范化把占位符之后的路径段统一成 `/`（推荐）。** 规则写明：`<case>` 或 `<project>` 之后、到下一个空白或引号为止的反斜杠换成 `/`。POSIX 路径不含反斜杠，POSIX 上是空操作：macOS 导出的事件与检查点里根本没有占位符，占位符只出现在工作区快照的 `AGENT.json` 里；而 Windows 套用这条规则后的快照素材与夹具逐项相同，说明夹具里没有「占位符后接反斜杠」的内容。按[行为不变基线](SPEC-layering-and-test-tiers.md#layering-baseline)「若规范化则明确列出字段」，这条规则要写进该节；规范化代码变了，旧夹具按 P7 处理。
- **（b）AS2 写 `as_posix()`。** Windows 上会写出 `C:/…`，它不再包含 `str(case)`，规范化仍然替换不到，还是要改规范化；另外改动 AS2 摘要。单独不够，不推荐。
- **（c）删去该字段或改成相对路径。** POSIX 上的 `AGENT.json` 字节也变，没有必要。不推荐。

把这条规则写进完整日用例而不是规范化代码，可以绕开规范化摘要的绑定，但那正是绑定要防止的事；否决。

<a name="cross-platform-baseline-数学库"></a>
### 4.4 平台数学库（P1、P5）

C 标准不规定 `exp`、`cos` 等函数的精度，各平台数学库通常只承诺误差在一个末位左右，不承诺正确舍入；macOS、Windows、glibc 的实现各不相同，在难舍入的参数上会给出相邻的两个双精度数。这类参数不罕见：3.2 的抽样里，Windows 的 `cos` 每一百次就有三次不是正确舍入。只要结果以全精度进入状态或记录，同一版本在不同平台上的输出就不同。正确舍入的结果在数学上唯一，与实现无关，所以「各平台一致」只有两条路：都用正确舍入，或都用同一份实现。

- **（a）标准库 `decimal` 的正确舍入实现（推荐）。** 在 `life/` 新增一个小模块：`exp` 用 `decimal` 的 `exp`（规范要求正确舍入），非整数幂用 `decimal` 的幂运算，`cos`、`sin`、`asin` 用十进制级数并以高精度 π 做参数约减；全部以 50 位十进制精度计算，再转成最接近的双精度数。`decimal` 由 libmpdec 实现，只用整数运算，不经平台数学库，各平台结果相同。四个模块的调用点改用它，另加结构守卫：`life/` 里除这个模块外不得直接调用 `math` 的超越函数，不得对浮点数做非整数幂。可行性已在本机试过：对两万个随机参数，`exp` 与 `cos` 的结果与 mpmath 正确舍入逐位相同，上面那个难舍入参数也对；每次约 55 微秒（`exp`）与 97 微秒（`cos`，均在两个测试进程并行时测得）。按 3.2 的调用次数，每粒种子约多 8 秒计算，约为 macOS 上单粒种子用时（两粒合计 298 秒）的 5%、Windows 上的 1.5%。
  后果：Windows 上的输出不变（Windows 在这一天的参数上已经与正确舍入一致，运行 B 为证）；macOS 上 `sleep_pressure`、`sleepiness` 在 3.3 列出的那些记录上改变末位。也就是说，修完四处之后，三个平台都会得到今天 Windows 运行 A 的结果，取证时的旧格式夹具不能作为修复后输出的比较来源；若立项，按 P6 在同一份当前结构 6 输入上生成跨平台新证明。
- **（b）只改 `pressure_after_awake` 与 `pressure_after_sleep` 的 `exp`。** 修掉观察到的这一处，改动最小；但困意的入睡与醒来阈值来自 `cos`，以全精度进入观察，换个种子或场景照样会在别处分歧。不推荐。
- **（c）把 `sleep_pressure` 量化到固定位数。** 两个平台的值落在量化边界两侧时仍会分歧，只是更少；它同样改变 POSIX 输出、需要新夹具，又不能在构造上保证一致。不推荐。
- **不立项（P1 的（b））。** 完整日基线写明只在 macOS 上判定，Windows 与 Linux 不作判据。代价最低，但 Linux x86_64 上是否符合从未验证，正式运行跨平台不可复现的问题也照旧。

不推荐引入 mpmath 等多精度库：要改依赖锁与引导，而标准库已经够用。

<a name="cross-platform-baseline-身份与夹具"></a>
## 五、身份、夹具与守卫的影响

| 改动 | POSIX 输出 | `code_sha256` | AS2 源码摘要 | 规范化摘要 | 要同步的文档 |
| --- | --- | --- | --- | --- | --- |
| P2（a） | 不变 | 变 | 不变 | 不变 | 无条款变化；`docs/design/05-recording-engine.md` 若写归档次序则补一句 |
| P3（a） | 不变 | 变（WorldMock 两处） | 变 | 不变 | `patches/agentsociety2/README.md`、`SERIES`、`docs/dev/SPEC-as2-integration.md`、`docs/EXPERIMENT.md`、共享规则规格（版本表） |
| P4（a） | 不变 | 不变 | 不变 | 变 | `docs/dev/SPEC-layering-and-test-tiers.md` 行为不变基线一节与版本表 |
| P5（a） | `sleep_pressure`、`sleepiness` 等末位变化 | 变 | 不变 | 不变 | `docs/dev/SPEC-life-runtime.md` 身体与睡眠公式处注明按正确舍入计算；`docs/design/04-simulation-engine.md` 基线说法 |

**P6：新夹具。** 四处修完之后，在隔离源码上生成新夹具，沿用现有夹具的全部字段与生成做法，另在每条运行回执里记录平台与机器架构：

- 两次独立运行分别在 Windows 与 macOS 上（Linux x86_64 可作第三次），签名必须逐项相同；守卫要求回执来自至少两种平台。
- 生成前用附录 B 的脚本逐字段比较同一份当前结构 6 外部输入上的修复前后导出：差异只能是授权的 `sleep_pressure`、`sleepiness` 等数学运算末位及派生摘要，否则拒绝生成。旧格式夹具不能作为不同输入的比较对象；历史取证中 P2–P4 在 POSIX 上是空操作的结论仍按原源码保留。
- 若裁决采纳，新证明补充跨平台相等的约束，并继续运行当前完整日的全部业务断言、实际存储、原始哈希与时间线正文核验；分层规格、设计与本特性规格届时按版本表修订。本文不执行该切换。

**P7：旧夹具与规范化摘要守卫。** 当前两份历史夹具仍以原字节的签名实现核对独立双跑来源；不改 JSON、不改当时回执，也不在旧证明里新增字段。未来规范化若改变，应另外登记其隔离源码与新证明的来源，历史夹具按原签名代码与提交核验。只改旧摘要字段而保留旧回执会伪造证明，禁止这样做。

<a name="cross-platform-baseline-取证"></a>
## 六、取证（已完成）与以后的复用

取证按 [HoldThatThgt/WorldMock#5](https://github.com/HoldThatThgt/WorldMock/issues/5) 完成：macOS 上用附录 A 的插件导出 `b83292f` 的规范化事件与检查点，Windows 上用同一插件导出同一代码，用附录 B 的脚本逐条比较，结果见 3.3、3.4。

两份脚本以后在生成新夹具（P6）与排查新的平台差异时可以原样复用：插件在 POSIX 上只导出、不改行为；在 Windows 上自动加上前三处模拟，这一部分在 P2–P4 落地后应当删去。

<a name="cross-platform-baseline-落地与验收"></a>
## 七、落地次序与验收

裁决之后建议依次实施下列内容；当前业务断言与工件完整性核验继续运行，跨平台相等必须由新实测证明，不能从旧格式结果推定红绿：

1. P5：正确舍入模块、四个模块的调用点、单元测试与结构守卫。
2. P2：快照排序与单元测试。
3. P3：WorldMock 两处 LF、AS2 补丁与摘要链、文档，全部机器重新引导。
4. P4 与 P7：规范化规则、分层规格条款、守卫（历史夹具绑定生成时的规范化代码，新夹具要求至少两种平台的回执）。
5. P6：在 Windows 与 macOS 上生成新夹具，切换完整日用例，修订相关文档。

验收：在 Windows（`core.autocrlf=false` 的独立检出）、macOS 与 Linux x86_64 上，显式加载同一份结构 6 数据仓环境，完整日两粒种子通过全部当前核验并取得同一跨平台签名；同输入的修复前后差异只落在授权数学运算末位及其派生摘要。两份历史证明原字节不变；每个平台的回执写明实际输入、平台、提交、命令与结果。Windows 上以针对节点的 pytest 执行，不宣称分层入口通过。以上均为未实施提案的验收目标。

<a name="cross-platform-baseline-不做的事"></a>
## 八、不做的事

- **按平台各存一份夹具。** 每次有意的行为变化都要在每个平台各生成、各证明一次；三种数学库就是三份；也不解决正式运行跨平台不可复现。
- **在比较里加容差、按字段忽略或删去事件内容。** 违反行为不变基线。
- **在规范化里修换行或重排归档成员。** 见 4.1、4.2。
- **把规范化写进用例文件以绕开摘要绑定。** 见 4.3。
- **从合并后的输出重录旧夹具。** 旧夹具保持原样作为历史证据；新夹具按 P6 从隔离源码生成并跨平台证明。
- **引入多精度数学依赖。** 见 4.4。

<a name="cross-platform-baseline-风险"></a>
## 九、风险

- 正确舍入模块要覆盖 `life/` 里全部会进入状态或记录的超越函数；漏掉一处，平台差异就会在别的输入上重现。结构守卫负责这一点，守卫本身也要有变异测试。
- `decimal` 的幂运算规范上是「几乎总是」正确舍入，不是严格保证；但它在各平台上是同一份实现，结果仍然一致，跨平台一致不依赖正确舍入本身。
- AS2 摘要变化要求所有机器重新引导；「让等待成为例外」正在本机与 Mac 上实施，本提案应在它收口之后落地（P8）。
- 3.3 的根因证据来自两粒种子这一天；P5 是按原因修，不是按这两处现象修，新夹具的跨平台回执是最终检验。
- Linux x86_64 上没有运行过；P5 落地之后它与另外两个平台在构造上一致，验收时实测确认。

<a name="cross-platform-baseline-附录"></a>
## 附录

### 附录 A：导出插件 `day_dump_plugin.py`

```python
"""只用于测量、不入库：导出完整日的签名、规范化事件与规范化检查点。

POSIX 上只导出，原夹具比较照常执行；Windows 上另外模拟三处已知的 POSIX 行为（文本写入用 LF、工作区快照按
区分大小写的路径排序、占位符之后的路径段用 /），原夹具比较已知不符，只导出不断言。
用法：DAY_DUMP_OUT=<输出目录> PYTHONPATH=<本文件所在目录> python -m pytest \
      tests/integration/test_dayrun_complete_days.py -p day_dump_plugin -p no:cacheprovider
"""

import builtins
import json
import os
import pathlib
import re
from pathlib import Path, PurePosixPath

import pytest

OUT = Path(os.environ["DAY_DUMP_OUT"])
WINDOWS = os.name == "nt"
_PLACEHOLDER = re.compile(r"<(case|project)>[^\s\"']*")

if WINDOWS:
    _open, _write_text, _path_open = builtins.open, pathlib.Path.write_text, pathlib.Path.open

    def _lf(mode, newline):
        return "\n" if newline is None and "b" not in mode and any(flag in mode for flag in "wax+") else newline

    def _posix_open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        return _open(file, mode, buffering, encoding, errors, _lf(mode, newline), closefd, opener)

    def _posix_write_text(self, data, encoding=None, errors=None, newline=None):
        return _write_text(self, data, encoding=encoding, errors=errors, newline="\n" if newline is None else newline)

    def _posix_path_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        return _path_open(self, mode, buffering, encoding, errors, _lf(mode, newline))

    builtins.open, pathlib.Path.write_text, pathlib.Path.open = _posix_open, _posix_write_text, _posix_path_open


def _posix_sorted(iterable, *, key=None, reverse=False):
    items = list(iterable)
    if key is None and items and all(isinstance(item, pathlib.PurePath) for item in items):
        key = lambda item: PurePosixPath(item.as_posix())
    return sorted(items, key=key, reverse=reverse)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    import tests.dayrun_baseline as harness

    (module,) = {item.module for item in items if item.module.__name__.endswith("test_dayrun_complete_days")}
    original = module._assert_frozen_day
    if WINDOWS:
        import worldmock.footprints.checkpoint as checkpoint

        checkpoint.sorted = _posix_sorted
        plain = harness.DayBaseline.normalize

        def posix_normalize(self, value):
            result = plain(self, value)
            if isinstance(value, str) and isinstance(result, str) and ("<case>" in result or "<project>" in result):
                return _PLACEHOLDER.sub(lambda match: match.group(0).replace("\\", "/"), result)
            return result

        harness.DayBaseline.normalize = posix_normalize

    def dumping(case, seed):
        project = Path(module.__file__).resolve().parents[2]
        OUT.mkdir(parents=True, exist_ok=True)
        signature = harness.day_baseline_signature(case, project)
        (OUT / f"signature-{seed}.json").write_text(
            json.dumps(signature, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        baseline = harness.DayBaseline(case, project)
        checkpoints = baseline.prepare()
        events = baseline.content((case / "export" / "events.jsonl").read_bytes(), "events.jsonl")
        (OUT / f"normalized-events-{seed}.jsonl").write_bytes(events)
        (OUT / f"normalized-checkpoints-{seed}.jsonl").write_bytes(
            b"".join(harness._json(item) + b"\n" for item in checkpoints))
        if not WINDOWS:
            original(case, seed)

    module._assert_frozen_day = dumping
```

### 附录 B：比较脚本 `day_dump_diff.py`

```python
"""只用于测量、不入库：逐条比较两个平台导出的规范化事件或检查点，报告第一处不同与不同字段的分布。

用法：python day_dump_diff.py <POSIX 导出的 .jsonl> <Windows 导出的 .jsonl>
"""

import json
import sys
from collections import Counter


def differing_leaves(left, right, path=""):
    """逐个叶子比较；列表长度不同或类型不同时整体报告一次。"""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            yield from differing_leaves(left.get(key), right.get(key), f"{path}.{key}" if path else key)
    elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for a, b in zip(left, right):
            yield from differing_leaves(a, b, f"{path}[]")
    elif left != right:
        yield path, left, right


def main(left_path, right_path):
    left = [json.loads(line) for line in open(left_path, encoding="utf-8")]
    right = [json.loads(line) for line in open(right_path, encoding="utf-8")]
    print(f"records: {len(left)} vs {len(right)}")
    first, records, fields, examples = None, Counter(), Counter(), {}
    for index, (a, b) in enumerate(zip(left, right)):
        if a == b:
            continue
        kind = a.get("event_type", "checkpoint")
        first = index if first is None else first
        records[kind] += 1
        for path, x, y in differing_leaves(a, b):
            fields[(kind, path)] += 1
            examples.setdefault((kind, path), (index, repr(x)[:160], repr(y)[:160]))
    print("first differing record:", first)
    if first is not None:
        keys = ("event_seq", "event_type", "agent_id", "occurred_at", "checkpoint_id", "current_time")
        print(json.dumps({key: left[first][key] for key in keys if key in left[first]}))
    print("differing records by type:", dict(records.most_common()))
    for key, count in fields.most_common(40):
        index, x, y = examples[key]
        print(f"{count:6d} {key[0]} {key[1]}\n       record {index}: {x}\n                 vs {y}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```
