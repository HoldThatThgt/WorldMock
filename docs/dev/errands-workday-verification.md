# 个人事务与工作安排验收记录

> 状态：**验收记录**。规格实施阶段 1–4 已全部合入，阶段 5 的全量门禁与两轮独立源码变异审查已完成：最终代码与测试提交 `a7e2fdc` 在干净检出上通过全量测试（5851 passed、7 skipped、0 failed）与覆盖率门禁（分支覆盖率 95.15%，未覆盖公共接口 0）；两轮独立源码变异审查共 230 个变异，115 个被既有测试杀死，115 个初次存活，其中 110 个判为真实缺口，全部补测并以原变异复验杀死，补测过程没有发现生产缺陷。证据层级：**离线测试**，本特性没有发起任何真实 DeepSeek 调用。阶段 6（配对全天复跑，只报告）未执行，strict 端点探针未运行；项目整体仍为 NOT-COMPLETE。阶段 7 的实现后文档、重新生成的覆盖率报告与本记录在 `a7e2fdc` 之后提交，实现状态文档最后单独提交。
> 日期：2026-10-04
> 依据：[个人事务与工作安排规格](SPEC-errands-workday.md)（FROZEN，版本表 1.0）的[验收台账](SPEC-errands-workday.md#验收台账)与[实施计划](SPEC-errands-workday.md#实施计划)；逐文档的同步作业、逐阶段的文件修改清单与被钉点见[实施台账](research/errands-workday-implementation-plan.md)。行为以规格为准，本记录只登记证据，不另定行为。

本记录是规格「实施计划」阶段 5（全量门禁与变异审查的结果）、阶段 6（复跑指标写入验收记录）与验收登记表 `tests/errands_workday_acceptance.py` 所指的特性验收记录：
登记源码身份与发布身份、实施过程、各阶段测试结果、评审与修复、独立源码变异审查、全量门禁与覆盖率、验收台账的证据登记、代码与规格的出入与裁定、
文档一致性，以及环境敏感项、未执行的项与范围外的项。验收台账各行的证据节点取自登记表，由 `tests/test_errands_workday_acceptance.py` 逐行判定；本记录不另建一份清单。

本记录的每个数字有两种来源：实施期间实际执行的运行（由编排方逐次记录），或写本记录时重新执行的命令；后一种在文中写明「写本记录时核对」。
没有运行过的项写「未执行」，不记为零，也不以离线结果代替。

## 源码身份与发布身份

| 身份 | 固定值 |
| --- | --- |
| 最终代码与测试提交 | `a7e2fdc3e07a0c1962cd4312e6b9956ab6ea74a2`（`a7e2fdc`）：为覆盖率门禁标出的 13 个公共接口补测试的提交（只改测试），是最终全量测试与覆盖率门禁的被测源码。生产代码自合并提交 `59b81a3` 起没有变化：`git diff 59b81a3 a7e2fdc` 在 `src/`、`custom/`、`scripts/`、`tools/`、`pyproject.toml`、`uv.lock` 上为空（写本记录时核对） |
| 分支 | `feat/errands-workday`，自工具交互特性的提交 `3b4afe5` 起；`git log 3b4afe5..a7e2fdc` 共 49 个提交，其中 3 个合并提交（`9445691`、`cd33c96`、`59b81a3`） |
| 冻结之前 | `eac4643`：规格 1.5 节所说的目录文档对齐，在冻结之前单独提交，不属任何阶段 |
| 规格冻结 | `e9c3ce5`：规格 1.0 FROZEN，「裁决记录」二十二项全部取推荐，【冻】作业落地，变更集只含文档 |
| AS2 | 锁定提交 `8768e14`，未改动；`AS2_LOCKED_SOURCE_SHA256` 为 `65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334`，与 `3b4afe5` 上的值相同。写本记录时本机 `vendor/agentsociety2` 子模块位于 `8768e14`，`worldmock.runtime.require_locked_as2()` 通过（228 个文件） |
| `code_sha256` | `d0740e661d6f5226fdc7ae3b8fbc2ad43889d6a36c0ec5dc572e2c250a1fe455`：写本记录时在本机工作树上以 `worldmock.runtime.runtime_code_sha256` 计算；工作树的 `src/`、`custom/`、`pyproject.toml`、`uv.lock` 与 `a7e2fdc` 相同（`git status` 在这些路径上没有改动） |
| 覆盖率快照 | `docs/dev/coverage-report.json`：`snapshot_id` 为 `b7136dcb-0be8-4241-9f48-3638cda20c5b`，`manifest_hash` 为 `0d9a62cc427c8a1a4bf8a69c345f8e1c06ecaae7af94278a751d31ee5fd2e471`，`source_identity` 为 `a7e2fdc` 的完整哈希，生成于 `2026-10-04T07:51:02+00:00`（写本记录时从报告文件读出） |
| 数据集仓库 `WorldMock-Dataset` | 规格「数据集侧登记」各项由用户另开会话在数据集仓库处理，不属本记录；本仓库测试不读取数据集仓库 |

### 各阶段提交

提交主题见 `git log 3b4afe5..a7e2fdc`；下表按阶段归组，括号内是该提交的内容。

| 阶段 | 提交 |
| --- | --- |
| 冻结之前、冻结 | `eac4643`（目录文档对齐）、`e9c3ce5`（冻结与冻结时的文档作业） |
| 1 运行修复入库 | `a6af0a4`（决策与工具分发失败的日志、登记表与共用变异执行器）、`ffa4ca1`（计划重复意图逐条定位，超限同批报告）、`eb1552b`（只读查询经 `LifeEnvActor` 与 `LifeEnvProxy` 转发）、`a1f680b`（就寝反思经严格工具提交与有界校验重试）、`deb4d4a`（设计章节与手册同步）、`fd93aab`（评审修复）、`350587d`（恢复 `fd93aab` 删去的被钉表达式） |
| 2 个人事务与提示分工（主线） | 十个切片：`5bd75d6`（未完成事务状态、世界结构 9、待办层事务行）、`7231969`（变更的校验、应用与记录纯函数）、`98dea88`（三个控制工具受理 `errands`）、`ec289c0`（宿主用环境的同一函数校验）、`92b87ec`（按已核验证据结算，一次提交一次）、`c1f5c30`（提醒清醒人物一次，不叫醒睡眠）、`4a9d91d`（跨零点、零点计划输入与检查点）、`c87118a`（提示分工：系统层规则、反思补句与唯一出现）、`0f9af2a`（事务与收尾记忆指标、交付边界）、`d09d574`（阶段收尾）；`12a3ccb`（文档）；`0369666`、`dca42a3`（L2 与评审修复） |
| 4 查询筛选与返回精简（分支 `feat/errands-workday-stage4`，自 `350587d`） | `8789dec`（先于代码的文档）、`135f4c6`（来源解析共用、冻结渠道索引）、`9dee1c0`（返回精简，不再逐项附候选）、`19c9dba`（六键 `query_world` 与来源、可执行筛选）、`c9ff925`（可执行筛选、筛选后翻页与只读批次的验收用例）、`318dbf1`（只读子命令 `queries`）、`ba9afa0`（接口合同与设计章节）、`c0e8dae`（阶段收尾）、`8ef5aab`（评审修复）；合并提交 `cd33c96` |
| 3 作息与工作安排（两条并行轨道，自 `c87118a`） | 轨道 A（分支 `feat/errands-workday-stage3a`）：`3a0d951`（下班与到家锚点的抽取与降级）、`e8c8202`（人设层与卡片文本）、`bda5cc4`（场景作息覆盖七字段与零点计划输入）；轨道 B（分支 `feat/errands-workday-stage3b`）：`22570f7`（`work_schedule` 声明与加载核对、场景结构 5）、`334a07f`（环境层 `duty`）、`304fa5d`（系统层第 1、14 条与零点计划输入）；轨道合并 `9445691`；其后在轨道 B 上：`83a1233`（工作负荷与作息偏离）、`56451b4`（只读子命令 `workday`）、`b8f5d47`（文档）、`a400ea6`（阶段收尾）、`83fab86`（评审修复）；合并进主线 `59b81a3` |
| 5 全量门禁与变异审查 | `04249f1`（第一轮变异审查的补测）、`d8a41d3`（第二轮变异审查的补测）、`f23f697`（登记表阶段 5 收尾：「工程门禁」行与「实施阶段全部完成」守卫）、`a7e2fdc`（覆盖率门禁标出的 13 个公共接口的测试）；四个提交都只改 `tests/` 下的文件 |

### 结构版本与指纹

规格 [6.4 首次受影响发布身份](SPEC-errands-workday.md#64-首次受影响发布身份)的各项，按实现后的代码逐项核对（写本记录时核对；「实施前」一列取 `git show 3b4afe5:<文件>`）：

| 产物 | 实施前（`3b4afe5`） | 实现后 | 首次改变的阶段 | 代码位置 |
| --- | --- | --- | --- | --- |
| 世界结构 | 8 | 9 | 2 | `WORLD_SCHEMA_VERSION`（`src/worldmock/life/models.py`） |
| 场景结构 | 4 | 5 | 3 | `LifeScenario.schema_version: Literal[5]`（`src/worldmock/life/scenario.py`）；仓库三份场景的 `schema_version` 都是 5 |
| 卡片结构 | 2 | 3 | 3 | `SCHEMA_VERSION`（`src/worldmock/profile/models.py`） |
| 卡片抽取提示 | `card-v2` | `card-v3` | 3 | `PROMPT_VERSION`（`src/worldmock/profile/models.py`） |
| 质量报告结构 | 3 | 4 | 2 | `build_quality_report` 返回的 `schema_version`（`src/worldmock/footprints/quality.py`） |

规格列为不变的各项，写本记录时核对仍是原值：地图结构 3（`MAP_SCHEMA_VERSION`）、目录 5.3 与品类合同 3（`catalog/manifest.json`）、导出清单 2（`src/worldmock/footprints/export.py`）、
运行清单 2（`src/worldmock/life/engine.py`）、行为评估结构标识 `behavior-metrics-1`、AS2 锁定摘要（见上表）。`scripts/build_catalog.py --check` 通过，目录各文件逐字节相同（写本记录时执行）。

两个指纹由函数计算，仓库里没有它们的字面钉值。写本记录时在 `a7e2fdc` 的生产代码上执行
`uv run --frozen --no-sync python -c "from custom.agents.life_prompt import life_prompt_sha256; from custom.agents.life_agent import life_tool_schema_sha256; print(life_prompt_sha256()); print(life_tool_schema_sha256())"`
（环境变量 `AGENTSOCIETY_LLM_API_KEY=test-only` 只为让模块能导入）得到：

| 指纹 | 值 | 本特性中改变它的阶段（规格 6.4） |
| --- | --- | --- |
| `prompt_sha256` | `ecddf2d467f9366497a3c14bc2bc9b9c17bce8f8fe0fa8eaa38cf01216fc2a36` | 2（系统层第 1、4、6 条；待办层、进度层 Schema）、3（系统层第 1、14 条；人设层、环境层 Schema） |
| `tool_schema_sha256` | `83eb6ecf3d357a5ddae9c899e0ce4374edd5ce209252bde96423a653d66a995d` | 2（三个控制工具）、4（`query_world`） |

仓库三份场景随场景结构 5 改变了 `scenario_sha256`（写本记录时以 `worldmock.life.semantics.load_runtime_scenario` 离线加载，摘要等于文件字节的 SHA-256；三份场景的全部人物 `work_schedule` 都是 null，即裁决 W4）：

| 场景 | `scenario_sha256` |
| --- | --- |
| `scenarios/white-collar-12-weeks.json` | `3512607ed7b0a6dd75d48228057857341b2338508ee5e13322cd9550dede4620` |
| `scenarios/multi-person.json` | `0b07e162dd911f800d2c54e3c0b416b39b30d49d612b52225f1c671c19824aeb` |
| `scenarios/multi-person-half-day.json` | `61e5eb55de1789726b4a4156c63949ffcc5599020cdc97fc4f53ff3a59f3b4ed` |

旧检查点、旧场景、旧卡片、旧四键查询与缺 `errands` 的旧控制请求按版本或缺字段拒绝，没有迁移与读取分支；各自的拒绝用例登记在验收台账「检查点」「工作安排加载」「作息锚点」「查询请求」「交付边界」行。

## 实施过程

- **冻结与基线（阶段 0）。** 目录文档对齐 `eac4643` 之后，冻结提交 `e9c3ce5` 只含文档；两个提交各跑一次全量测试作基线。
- **阶段 1。** 四项运行修复按四个可归因提交依次入库，其后是文档同步、评审修复与一个恢复被钉表达式的提交。阶段 1 早于下述测试层级的约定：它的每个提交都在隔离的 worktree 里跑了一次全量测试。
- **阶段 2、4、3 并行。** 规格规定阶段 4 只依赖阶段 1、可以提前，阶段 3 以阶段 2 的系统层与模板文本为基础。实施时阶段 2 在主线上按十个切片依次提交；阶段 4 自阶段 1 的末提交 `350587d` 起在自己的分支上并行开发；
  阶段 3 自 `c87118a` 起（此时阶段 2 的系统层第 1、4、6 条、零点计划模板第七条与反思补句已经提交）分两条轨道并行，轨道 A 做卡片锚点一侧，轨道 B 做工作安排一侧，
  轨道 A 合入轨道 B（`9445691`）后在轨道 B 上完成指标、只读子命令、文档与收尾。阶段 2 余下的切片（指标与交付边界、收尾）同时在主线上完成。
- **两次合并。** 阶段 4 先合入主线（`cd33c96`，父提交 `dca42a3` 与 `8ef5aab`），阶段 3 再合入（`59b81a3`，父提交 `cd33c96` 与 `83fab86`）。两次合并的冲突与合并后的测试见下节。
- **阶段 5。** 第一轮独立源码变异审查在 `cd33c96`（阶段 1、2、4）上进行，第二轮在 `59b81a3`（阶段 3）上进行；两轮的补测 `04249f1`、`d8a41d3` 都提交在 `59b81a3` 之后。
  全量测试与覆盖率门禁先在 `04249f1` 上预跑一次，门禁标出 13 个未覆盖公共接口，`a7e2fdc` 补测后在 `a7e2fdc` 上重跑通过。

测试层级（2026-10-03 与用户商定，自阶段 2 起使用）：

| 层级 | 何时 | 内容 |
| --- | --- | --- |
| L1 | 每个切片或修复提交之前 | 按改动选出的测试（改动的测试文件、直接导入被改的非枢纽模块的测试、文字上提到被改文件的测试、风格门禁），登记表的静态守卫，只跑本切片自己的变异，文档门禁 |
| L2 | 每个阶段末，在隔离的干净 worktree 里 | L1 的规则作用于整个阶段的改动并展开 conftest 与测试辅助模块，全部根目录守卫（含各登记表的完整变异运行），以及一组端到端冒烟用例 |
| L3 | 全部阶段完成后一次 | 最终提交的干净检出上带覆盖率的全量测试、覆盖率门禁与独立源码变异审查 |

因此阶段 2、3、4 没有各自的全量测试结果：它们的全量证据是 L3 在最终提交上的运行。

## 各阶段测试结果

| 时点 | 被测提交或范围 | 层级与规模 | 结果 | 出现的失败与处置 |
| --- | --- | --- | --- | --- |
| 基线（冻结之前） | `eac4643` | 全量，8 个分片，隔离 worktree | 5177 passed、7 skipped、0 failed | 无 |
| 基线（阶段 0） | `e9c3ce5` | 全量，隔离 worktree | 5177 passed、7 skipped、0 failed | 首轮六个分片跑完，最慢的两个分片被 2 小时的工具时限中止，重跑至完成 |
| 阶段 1 | `a6af0a4` | 全量，隔离 worktree | 5190 passed、7 skipped | 受 2 小时工具时限所限，结果由两次运行拼合 |
| 阶段 1 | `ffa4ca1` | 全量，隔离 worktree | 5205 passed、6 skipped | 这是补完被中断的分片并把一个失败的文件单独重跑之后的结果 |
| 阶段 1 | `eb1552b` | 全量，隔离 worktree | 5217 passed、7 skipped | 无 |
| 阶段 1 | `a1f680b` | 全量，隔离 worktree | 5282 passed、7 skipped | 无 |
| 阶段 1 | `deb4d4a` | 全量，隔离 worktree | 全部分片退出码为 0 | 无 |
| 阶段 1 | `fd93aab` | 全量，隔离 worktree | 5285 passed、1 failed、7 skipped | `tests/test_time_and_energy_completeness.py::test_required_symbols_of_done_stages_are_present` 失败：`fd93aab` 删去了时间与能量登记表钉住的一个表达式。`350587d` 恢复该表达式（行为不变），39 个定向测试通过 |
| 阶段 2 | 至 `12a3ccb` | L2，279 个测试文件中选出 204 个 | 1 个真实失败，3 个内存压力下的失败 | 真实失败是 `tests/tools/test_run_viewer.py`：查看器夹具没有跟上新的系统层，`0369666` 修复；另 3 个失败单独重跑通过 |
| 阶段 2 修复 | `12a3ccb..dca42a3` | L2，25 个文件 | 566 passed | 无 |
| 阶段 4（分支上） | `350587d..c0e8dae` | L2，267 个测试文件中选出 49 个 | 全部通过 | 无 |
| 阶段 4 修复 | `8ef5aab` | L2，29 个文件 | 667 passed | 无 |
| 合并阶段 4 | `dca42a3..cd33c96` | L2，281 个测试文件中选出 54 个 | 1074 passed | 19 个冲突文件：登记表、4 个测试文件与文档，生产代码没有冲突 |
| 阶段 3（分支上） | `c87118a..a400ea6` | L2，279 个测试文件中选出 147 个 | 全部通过 | 无 |
| 阶段 3 修复 | `83fab86` | L2，39 个文件 | 1113 passed | 无 |
| 合并阶段 3 | `cd33c96..59b81a3` | L2，284 个测试文件中选出 149 个 | 3529 passed、1 skipped | 23 个冲突文件：`scripts/inspect_run.py`、登记表、查看器夹具与文档；跳过的一项是 Windows 符号链接权限 |

- `deb4d4a` 一行只记录了各分片的退出码，没有记录通过数。
- `ffa4ca1` 一行没有记录那个文件首次失败的原因，也没有记录跳过数为 6 的原因；该文件单独重跑后计入上述结果。
- 阶段 5 的两次全量运行（`04249f1` 上的预跑与 `a7e2fdc` 上的最终运行）见「全量门禁与覆盖率」。

## 评审与修复

每个阶段的代码完成后做一次独立评审，发现的问题全部在下表所列的修复提交里处理。

| 阶段 | 评审者 | 发现 | 修复提交 | 修复后的测试 |
| --- | --- | --- | --- | --- |
| 1 | 3 | 9 项（3 项重大、6 项次要） | `fd93aab`，其后 `350587d` | `fd93aab` 的全量测试有 1 项失败，`350587d` 修复（见上节） |
| 2 | 4 | 10 项（2 项重大、8 项次要） | `dca42a3` | L2：25 个文件，566 passed |
| 4 | 3 | 3 项（都是次要） | `8ef5aab` | L2：29 个文件，667 passed |
| 3 | 3 | 5 项（1 项重大、4 项次要） | `83fab86` | L2：39 个文件，1113 passed |

四个阶段合计 27 项发现（6 项重大、21 项次要），全部处理。各修复提交改了什么，取自提交说明与 `git show --stat`（写本记录时核对）：

- **阶段 1 的三项重大发现**（`fd93aab`）。
  其一，就寝反思的校验重试让发布门禁 `observation_repetition` 在一次正确的运行上失败：重试的附回消息不含时刻与人物内容，两个人物同类的失败产生逐字相同的用户文本。
  修法是 `behavior_metrics._decision_model_requests` 只把工具里含 `start_activity` 的请求当作行动请求，反思及其校验重试不再进入 `observation_repetition` 与 `decision_prompt_token_median`
  （`tests/evaluation/test_behavior_metrics.py::test_reflection_requests_and_their_retries_are_not_decision_requests`，整日运行用例里一颗种子的反思首次不合合同、经一次重试改正而门禁仍为 1）。
  其二，运行装配的用例核对的是人物从不使用的代理句柄：改为断言交给人物的 `ServiceProxy.env` 就是带 `read_query` 的 `LifeEnvProxy`，并经它调用一次
  （`tests/integration/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query`）。
  其三，「故障传播」一行没有经真实引擎与 SQLite 的证据：补两个经真实引擎、SQLite、`LifeEnv` 与直接导入的 `LifeAgent` 的用例
  （`tests/integration/test_host_failure_stages.py::test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage`、
  `tests/integration/test_host_failure_stages.py::test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged`）。
  `fd93aab` 改写筛选条件时删去了时间与能量登记表（`REQUIRED_SYMBOLS`）钉住的表达式，`350587d` 恢复，行为不变。
- **阶段 2**（`dca42a3`，只改测试与登记表；写本记录所依据的记录没有逐项点名两项重大发现）。评审指出若干台账行的条目只由辅助函数调用或手写事件证明，另有两句规格变异句所列的情形只覆盖了一部分。
  修复把这些条目改由真实请求、引擎与 SQLite 路径证明并登记：同一条未完成事务被第二条更新或取消点名、两条新增的结果与商品相同（事务名称与错误）；
  推进了当日目标的活动与自述办成都不算完成（完成依据）；零点前办成的事务在零点计划输入里不留句子（跨夜可见）；与到达同一边界交付的提醒不进新停留 session 的进度层（提示与唯一出现）；
  真实导出里 `errand_reminder_due` 计入已知唤醒原因（事务提醒）；真实导出上的投影与缺 `errands` 的旧请求被拒（交付边界）；两个分组没有样本为 null（指标）。
  规格变异句「把受理、未送达订单、活动开始、`progressed` 或自述当完成」改为每种情形各一个登记变异。阶段 2 的 L2 另查出查看器夹具没有跟上系统层第 1、4、6 条，`0369666` 修复。
- **阶段 4 的三项次要发现**（`8ef5aab`，改了 `src/worldmock/life/world_query.py` 与 `custom/agents/life_agent.py`）。
  四个筛选全空与其他字段错误（`available_only` 非布尔或缺失、负偏移、未声明的键）同时出现时原先只报字段错误：`QueryWorldRequest.require_filter` 改为包住字段校验的模型校验器，四个位置的错误与字段错误一次全报。
  宿主上请求模型错误原先会压下能在冻结观察上判定的错误：`world_query.query_argument_report` 先列模型错误，再列方式、品类、来源与渠道称呼误写的错误。
  「查询只读」行「最终回合与截断重试不开放查询」原先只有手写观察上的节点：补登两个经真实引擎的节点。
- **阶段 3 的一项重大发现**（`83fab86`，改了 `src/worldmock/evaluation/workload_metrics.py` 与 `src/worldmock/profile/models.py`）。
  工作负荷指标忽略了预约任务的例外（规格 4.5、6.2）：改为使用任务的预约时刻，预约任务从约定当天的本地零点起算作任务外出，约定开始之前出发赴约的路上不再算缺勤与早退
  （`tests/evaluation/test_workload_metrics.py::test_travel_to_an_appointment_before_it_starts_is_a_task_outing_not_absence_or_early_leave`、
  `tests/evaluation/test_workload_metrics.py::test_a_real_task_outing_is_not_absence_and_a_declared_rest_day_counts_nothing`）。
  同一提交处理的四项次要发现：窗口越过当日 24 点的下班、到家锚点也降级（规格 4.1 首版不支持跨午夜，就寝回绕到次日时会出现；新的降级说明格式登记进接口合同）；
  「工作负荷与作息偏离」行补真实运行的任务外出证据；补登三个变异（抽取丢掉写明的到家锚点、人设层丢掉到家锚点、工作提交后 `duty` 消失）；缺 `work_schedule` 键的场景改经真实加载入口被拒。

## 独立源码变异审查

规格阶段 5 要求在干净提交的隔离 worktree 里做独立源码变异审查。两轮审查的做法相同：评审者没有写被审的代码，自行设计登记表里没有的变异，在干净提交的隔离 worktree 里逐个对既有测试运行；
存活的变异由第二位评审者逐个判定是真实缺口、规格之外还是等价；真实缺口补行为测试（只改测试），再以原变异复验。
逐个变异的记录（所在文件、原文与替换文、对应的规格条款、结果、判定、补的测试与复验结果）在 `docs/dev/evidence/errands-workday/mutation-review.json`；下面两张表的数字写本记录时以脚本从该文件重新数出，与实施期间的记录一致。

| 轮次 | 被审提交 | 覆盖 | 变异 | 既有测试杀死 | 初次存活 | 真实缺口 | 规格之外 | 等价 | 补测提交 | 原变异复验 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `cd33c96` | 阶段 1、2、4，五个模块组 | 135 | 74 | 61 | 57 | 3 | 1 | `04249f1`（25 个测试文件） | 57 个全部杀死：54 个经共用执行器，3 个经按文件路径加载的驱动 |
| 2 | `59b81a3` | 阶段 3，三个模块组 | 95 | 41 | 54 | 53 | 1 | 0 | `d8a41d3`（13 个测试文件） | 53 个全部杀死 |
| **合计** | | | **230** | **115** | **115** | **110** | **4** | **1** | | **110 个全部杀死** |

| 模块组 | 轮次 | 变异 | 既有测试杀死 | 初次存活 | 真实缺口 | 规格之外 | 等价 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `g1-runtime-fixes`（运行修复） | 1 | 27 | 15 | 12 | 12 | 0 | 0 |
| `g2-errand-requests`（事务请求与校验） | 1 | 23 | 12 | 11 | 10 | 1 | 0 |
| `g3-settlement-reminders`（结算与提醒） | 1 | 28 | 19 | 9 | 8 | 1 | 0 |
| `g4-presentation-delivery-metrics`（呈现、交付边界与指标） | 1 | 30 | 16 | 14 | 14 | 0 | 0 |
| `g5-query`（查询） | 1 | 27 | 12 | 15 | 13 | 1 | 1 |
| `g6-anchors-override`（作息锚点与场景覆盖） | 2 | 23 | 14 | 9 | 9 | 0 | 0 |
| `g7-schedule-duty-prompt`（工作安排、`duty` 与提示） | 2 | 40 | 19 | 21 | 21 | 0 | 0 |
| `g8-workload`（工作负荷与只读子命令） | 2 | 32 | 8 | 24 | 23 | 1 | 0 |

- **按文件路径复验的三个变异。** 第一轮 `g5-query` 组里 `scripts/inspect_run.py` 上的三个真实缺口（`ir_answers_ignore_decision`、`ir_empty_after_filter_counts_tail_pages`、`ir_purchase_end_is_last_settlement`）不能由共用执行器注入：
  测试按文件路径加载这个脚本，执行器换不掉它。证据文件里这三条的 `replay_after_closing` 记为 `NOT-REPLAYABLE`；它们另以按文件路径加载的驱动复验，三个都被补的测试杀死
  （`tests/life/test_query_cost_report.py::test_a_call_id_reused_by_a_later_decision_does_not_answer_an_earlier_query`、
  `tests/life/test_query_cost_report.py::test_a_tail_page_of_a_filtered_query_is_not_empty_after_filter`、
  `tests/life/test_query_cost_report.py::test_model_calls_to_purchase_stop_at_the_first_settlement_after_the_first_query`）。
- **规格之外与等价。** 判为规格之外的四个：`errcore_issues_not_sorted`、`zero_quantity_eating_counts`（`src/worldmock/life/errands.py`）、
  `wq_host_report_reason_omits_observation_errors`（`src/worldmock/life/world_query.py`）、`wl_first_stay_assumed_at_home`（`src/worldmock/evaluation/workload_metrics.py`）；
  判为等价的一个：`tg_compact_keeps_empty_dict`（`src/worldmock/life/tool_groups.py`）。这五个不补测试，判定记在证据文件的 `verdict` 字段。
- **补测的范围。** 两个补测提交只改测试（`04249f1` 的 25 个文件含 `tests/life/conftest.py`，`d8a41d3` 13 个文件）；补测过程没有发现生产缺陷，生产代码未因变异审查改动。
- **登记表自己的变异。** 与独立审查分开，登记表 `tests/errands_workday_acceptance.py` 登记了 101 个源码变异（写本记录时核对：70 个对应规格「源码变异必须覆盖」一段的 53 句，
  阶段 1、2、3、4 各 8、26、12、7 句；另 31 个登记在 `EXTRA_MUTATIONS` 并写明出处），由守卫 `tests/test_errands_workday_acceptance.py::test_errands_workday_source_mutation_is_killed` 逐个执行，
  在 L2 与 L3 中运行；最终全量运行含这 101 个与工具交互登记表的 46 个变异。

## 全量门禁与覆盖率

### 预跑（`04249f1`，已被最终运行取代）

| 项 | 结果 |
| --- | --- |
| 被测源码 | `04249f1`，隔离的干净 worktree，带覆盖率的 10 个分片 |
| 全量测试 | 5751 passed、7 skipped、0 failed |
| 覆盖率门禁 | **FAILED**：分支覆盖率 94.97%（阈值 85%）达标，但有 13 个公共接口存在未执行的行（都是错误分支） |

13 个未覆盖的公共接口：

| 文件 | 接口 |
| --- | --- |
| `src/worldmock/corpora/storefront.py` | `assortment_objects`、`price_range`、`validate_assortments` |
| `src/worldmock/corpora/storefront_build.py` | `build_storefront_supplies` |
| `src/worldmock/life/map_data.py` | `select_map` |
| `src/worldmock/life/map_models.py` | `validate_assortment`、`validate_assignment` |
| `src/worldmock/life/memory_layer.py` | `persona_views` |
| `src/worldmock/life/name_resolution.py` | `fill_in` |
| `src/worldmock/life/purchase_offers.py` | `order_key` |
| `src/worldmock/life/routing.py` | `combination_issues` |
| `src/worldmock/life/scenario.py` | `validate_price_bounds` |
| `custom/agents/life_agent.py` | `dispatch_react_tool` |

这些文件大多最后由工具交互特性（`3b4afe5`）改动；仓库里此前提交的覆盖率报告生成于 2026-09-30（被测源码 `1478436`），早于该特性。
`a7e2fdc` 补测试关闭（只改 7 个测试文件），一次定向的覆盖率运行确认 18 个缺失的行全部被执行。其中三个接口经正常加载器或已校验的请求到达不了，在最近的公共入口覆盖：
`scenario.ProductConfig.validate_price_bounds` 经 `RuntimeScenario.model_validate`；`purchase_offers.order_key` 经 `resolve_named_supply` 加一条手工构造的询价；
`memory_layer.persona_views` 经以 `FootprintStore.commit` 写入的一对已记录事件。

### 最终运行（`a7e2fdc`）

| 门禁 | 结果 |
| --- | --- |
| 被测源码 | `a7e2fdc`，隔离的干净 worktree |
| 全量测试 | 带覆盖率的 12 个分片（慢文件按测试节点拆分）：5851 passed、7 skipped、0 failed、0 errors，每个分片退出码为 0，墙钟 5678 秒 |
| 覆盖率门禁 | **PASSED**：分支覆盖率 95.15%（covered=8520，missing=434，total=8954；阈值 85%），未覆盖公共接口 0 |
| 跳过 | 7 项都是既有的平台或产物条件跳过：TextWorld 没有 Windows wheel、Windows 账户没有符号链接权限、锚定运行产物不在本机 |
| 运行内含的守卫 | 两份登记表的完整变异守卫（个人事务与工作安排 101 个、工具交互 46 个）、风格门禁、文档门禁与设计说明书汇编检查的测试，都在这次干净检出上执行 |

| 分片 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| passed | 580 | 503 | 520 | 564 | 281 | 315 | 655 | 276 | 576 | 493 | 478 | 610 |
| skipped | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 |

实际执行的命令不是门禁脚本的串行命令：12 个并行分片各执行 `coverage run -p -m pytest -q <均衡分配的测试文件>`，然后 `coverage combine`、`coverage json`，再调用 `scripts/coverage_gate.py` 自己的判定函数。
被测的测试集与度量口径同串行命令 `uv run coverage run -m pytest -q`，判定用的是门禁脚本自己的函数；报告的 `command` 字段如实记为分片命令
（`12 parallel shards of: coverage run -p -m pytest -q <balanced test files>; coverage combine; coverage json (same suite and measurement as: uv run coverage run -m pytest -q)`）。
这次带覆盖率的分片运行就是最终提交上的全量证据。

`docs/dev/coverage-report.json` 由这次运行重新生成（身份见「源码身份与发布身份」），随实现后文档一并提交。这次运行之后只改动文档、覆盖率报告、证据文件与登记表的登记数据（阶段 7 标为完成），
这些提交以文档门禁、设计说明书汇编检查与登记表的静态守卫核对。

## 验收台账逐行登记

守卫 `tests/test_errands_workday_acceptance.py` 以 `tests/errands_workday_acceptance.py` 为数据源判定：登记表的键与规格「验收台账」各行一一对应、顺序与阶段列相同；已完成阶段的每一行都列出测试，
任何行列出的节点与任何变异的杀手都解析到存在、会被收集且未被跳过的测试；只报告的行在规格中仍写明只报告；规格「源码变异必须覆盖」一段逐句登记到变异，每个登记的变异被行为测试杀死；
特性文件清单里的 52 个生产文件、27 个测试文件与登记了节点的测试文件没有延期标记，Python 文件没有空壳函数；实施阶段 1–4 与全量门禁阶段 5 必须全部完成。
写本记录时在 `a7e2fdc` 的代码上执行该守卫的 9 项静态判定（不含逐个变异的运行）：9 passed。

下表逐行列出登记表为该行登记的节点数与所在测试文件（括号内为该文件的节点数），节点本身以登记表为准，全部在最终全量运行中通过。
各行节点合计 258 个，其中 7 个节点各守卫两行，去重后 251 个，分布在 62 个测试文件中（写本记录时以脚本从登记表数出）。

| 契约点 | 阶段 | 节点数 | 证据所在 |
| --- | --- | --- | --- |
| 严格反思 | 1 | 22 | `tests/integration/test_strict_reflection.py`（15）、`tests/integration/test_life_agent_protocol.py`、`tests/integration/test_verified_memory.py`、`tests/integration/test_conclude_memories.py`、`tests/integration/test_dayrun_complete_days.py`、`tests/delivery/test_windows.py`、`tests/life/test_engine_branch_coverage.py`、`tests/evaluation/test_behavior_metrics.py`（各 1） |
| 只读查询转发 | 1 | 5 | `tests/integration/test_read_query_runtime.py`（4）、`tests/life/test_world_query.py`（1） |
| 计划重复意图 | 1 | 9 | `tests/life/test_revise_day_plan_json.py`（6）、`tests/life/test_plan_day_duplicate_feedback.py`（3） |
| 故障传播 | 1 | 8 | `tests/integration/test_host_failure_stages.py`（5）、`tests/integration/test_life_agent_protocol.py`（2）、`tests/integration/test_agent_branch_coverage.py`（1） |
| 事务变更与原子受理 | 2 | 8 | `tests/life/test_errand_requests.py`（6）、`tests/integration/test_activity_schema.py`、`tests/integration/test_conclude_memories.py`（各 1） |
| 事务名称与错误 | 2 | 11 | `tests/life/test_errand_names.py`（9）、`tests/integration/test_life_agent_protocol.py`、`tests/life/test_errand_completion.py`（各 1） |
| 跨夜可见 | 2 | 8 | `tests/life/test_errand_overnight.py`（4）、`tests/life/test_day_planning.py`（3）、`tests/integration/test_errand_prompt_separation.py`（1） |
| 完成依据 | 2 | 14 | `tests/life/test_errand_completion.py`（14） |
| 结算幂等 | 2 | 5 | `tests/life/test_errand_settlement_idempotence.py`（5） |
| 事务提醒 | 2 | 11 | `tests/life/test_errand_reminder.py`（9）、`tests/footprints/test_errand_export.py`、`tests/footprints/test_quality.py`（各 1） |
| 检查点 | 2 | 4 | `tests/life/test_session_state.py`（2）、`tests/delivery/test_publication.py`、`tests/integration/test_errand_checkpoint.py`（各 1） |
| 提示与唯一出现 | 2 | 15 | `tests/integration/test_seven_layer_request.py`（3）、`tests/integration/test_tool_description_references.py`（3）、`tests/integration/test_strict_reflection.py`（2）、`tests/integration/test_same_second_continuation.py`（2）、`tests/integration/test_unique_presence_run.py`、`tests/life/test_errand_todo_layer.py`、`tests/life/test_progress_events.py`、`tests/life/test_errand_reminder.py`、`tests/integration/test_errand_prompt_separation.py`（各 1） |
| 交付边界 | 2 | 8 | `tests/delivery/test_windows.py`（3）、`tests/integration/test_errand_delivery_windows.py`（3）、`tests/life/test_control_items.py`（2） |
| 事务与收尾记忆指标 | 2 | 12 | `tests/evaluation/test_errand_metrics.py`（7）、`tests/integration/test_dayrun_complete_days.py`、`tests/integration/test_errand_delivery_windows.py`、`tests/footprints/test_goal_export.py`、`tests/footprints/test_quality.py`、`tests/footprints/test_errand_export.py`（各 1） |
| 作息锚点 | 3 | 28 | `tests/profile/test_extractor.py`（9）、`tests/integration/test_input_warnings.py`（5）、`tests/profile/test_injection.py`（4）、`tests/profile/test_models.py`（3）、`tests/life/test_persona_layer.py`（3）、`tests/profile/test_textgen.py`（2）、`tests/profile/test_compile.py`、`tests/life/test_scenario.py`（各 1） |
| 工作安排加载 | 3 | 7 | `tests/life/test_load_checks.py`（5）、`tests/life/test_scenario.py`（2） |
| 上班安排呈现 | 3 | 9 | `tests/life/test_work_schedule.py`（7）、`tests/integration/test_same_second_continuation.py`、`tests/life/test_prompt_layers.py`（各 1） |
| 计划与义务 | 3 | 4 | `tests/life/test_day_planning.py`（3）、`tests/integration/test_seven_layer_request.py`（1） |
| 工作合同边界 | 3 | 2 | `tests/life/test_work_schedule.py`（2） |
| 工作负荷与作息偏离 | 3 | 14 | `tests/evaluation/test_workload_metrics.py`（11）、`tests/life/test_workload_report.py`（3） |
| 查询请求 | 4 | 12 | `tests/life/test_world_query_filters.py`（6）、`tests/life/test_world_query.py`（5）、`tests/integration/test_tool_description_references.py`（1） |
| 来源筛选 | 4 | 10 | `tests/life/test_purchase_channels.py`（5）、`tests/life/test_world_query_filters.py`（5） |
| 可执行筛选 | 4 | 6 | `tests/life/test_world_query_filters.py`（6） |
| 分页与返回 | 4 | 6 | `tests/life/test_world_query.py`（3）、`tests/life/test_world_query_filters.py`（3） |
| 查询只读 | 4 | 12 | `tests/life/test_tool_protocol.py`（3）、`tests/life/test_world_query_filters.py`（3）、`tests/life/test_query_cost_report.py`（3）、`tests/life/test_world_query.py`、`tests/life/test_tool_interaction_runtime.py`、`tests/integration/test_output_truncation.py`（各 1） |
| 工程门禁 | 5 | 4 | `tests/test_coverage_gate.py`（2）、`tests/test_style_gate.py`、`tests/test_documentation_gate.py`（各 1） |
| 复跑指标（只报告） | 6 | 0 | 未执行，见「环境敏感项、未执行与范围外」 |
| 文档一致性 | 7 | 4 | `tests/test_dayrun_fidelity_acceptance.py`（2）、`tests/test_documentation_gate.py`、`tests/test_design_doc_build.py`（各 1） |

- 各阶段的行数与节点数：阶段 1 四行 44 个，阶段 2 十行 96 个，阶段 3 六行 64 个，阶段 4 五行 46 个，阶段 5 一行 4 个，阶段 6 一行 0 个，阶段 7 一行 4 个。
- 守卫两行的 7 个节点：反思文案与登记块逐字相同（严格反思、提示与唯一出现）、两人整日运行（严格反思、事务与收尾记忆指标）、`query_world` 两入口等价（只读查询转发、查询只读）、
  认证导出逐值重算（事务提醒、事务与收尾记忆指标）、真实导出上的投影与旧请求被拒（交付边界、事务与收尾记忆指标）、只写 `from` 的查询（查询请求、来源筛选）、文档链接门禁（工程门禁、文档一致性）。
- 「工程门禁」一行登记的是门禁自身的测试；全量测试与覆盖率门禁在最终代码上的结果见上节。
- 「复跑指标（只报告）」按规格只报告、不作门禁，登记表为它登记的节点数为 0；配对全天复跑未执行。
- 「文档一致性」一行属阶段 7。`a7e2fdc` 上登记表的 `STAGES_DONE` 为阶段 1–5；该行已列出四个节点，阶段 7 由紧随实现后文档提交的登记表提交标为完成（只把阶段 7 加入 `STAGES_DONE` 并补一句注释，不改测试逻辑）。

## 代码与规格的出入与裁定

### 规格没有定稿之处的裁定

下列各项规格没有给出确定的文字或做法，由编排方在各阶段开工前裁定，实现与测试照此执行；规格文字与这些裁定有矛盾时以规格为准。

| 事项 | 规格条款 | 裁定与实现 |
| --- | --- | --- |
| 反思补句的排版 | 3.3 | `REFLECTION_PROMPT` 末尾空一行，再把 3.3 的两句作为不折行的一行追加；常量、接口合同的登记块与测试用同一种写法（`custom/agents/life_reflection.py`；`tests/integration/test_strict_reflection.py::test_reflection_prompt_ends_with_the_errand_sentences`） |
| 空白的 `from` | 5.1、5.5 | 按 5.1 与 5.5 的字面：请求模型不对 `from` 加非空白检查，空白的 `from` 既不是地点显示名也不是渠道称呼，由 5.5 第一行报错（`path` 为 `from`，`expected` 为 `null, a known place name, or a known channel name is required.`）；`name`、`way`、`category` 保持非 null 不得为空白 |
| 任务外出例外的范围 | 4.5、6.2 | 班次内前往本人已开放且未结清任务的要求地点的路上、在该地点停留、以及从该地点直接返回工作地点的一程，都算任务外出；其余不在工作地点又不在声明的休息时段内的时间是缺勤。预约任务自约定当天的本地零点起算（阶段 3 评审修复，见「评审与修复」） |
| 非 null 的 `item`、`memo`、`reason` | 2.2、2.3 | 与 `name` 同一约定，不得为空白；空白值是 `errands[j].<键>` 上的 `ARGUMENTS_INVALID` |
| 同一请求内的重复 | 2.2 | 两条新增同名、同一条未完成事务被两条更新或取消点名时，只标出较晚出现的条目 |
| 卡片文本分句与降级说明 | 4.1 | 规格没有定稿的文字（卡片文本追加的两个分句、降级说明的格式、泛化后的 `PERSONA_AGREEMENT_LOW` 消息）取实施蓝图的拟稿，由阶段 3 的文档作业登记进接口合同 |

### 实施台账与规格不一致、按规格实现之处

实施台账（`docs/dev/research/errands-workday-implementation-plan.md`）的文件修改清单与文档同步作业有若干处与冻结规格 1.0 的条款不一致。各阶段开工前逐条比对，一律按规格实现；
从台账抄进文档的句子同样按规格写。实质性的各项如下（「核对」列写本记录时核对所依据的代码或登记的测试）：

| 阶段 | 实施台账的写法 | 规格条款与实现 | 核对 |
| --- | --- | --- | --- |
| 2 | 新增、更新、取消三种请求驱动的事务事件带决策与动作身份 | 2.7、2.9：六种事务事件的决策身份与动作身份都为空，只经 `caused_by` 关联 | `tests/integration/test_errand_delivery_windows.py::test_a_completion_caused_by_another_persons_action_stays_out_of_their_decision_group` |
| 2 | 变更种类的枚举值写作「登记」 | 2.2：`change` 取「新增」「更新」「取消」 | `ErrandChange.change`（`src/worldmock/life/models.py`） |
| 2 | 待办层键序为 `layer, paused_activities, day_plan, errands` | 2.6：`layer, paused_activities, errands, day_plan` | `todo_layer()`（`src/worldmock/life/todo_layer.py`） |
| 2 | 睡眠中错过的提醒在「醒来后的第一个边界」交付 | 2.5：在人物醒来的那个边界与 `wake_from_sleep` 一并交付，不另起边界 | `tests/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary` |
| 2 | 反思文案追加「一句」，常量放在 `custom/agents/life_memory.py` | 3.3：追加两句；常量是阶段 1 的 `REFLECTION_PROMPT`（`custom/agents/life_reflection.py`） | `custom/agents/life_reflection.py` |
| 2 | 同一事务在一次请求里至多出现在一条变更中；重复判定写在请求模型里按所写字符串比较 | 2.2：同一条未完成事务至多被一条更新或取消点名、两条新增不得同名，取消旧事务并以原名新增合法；是否同一条取决于名称解析，判定放在宿主与环境共用的 `errands.change_issues` | `tests/life/test_errand_names.py::test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted`、`tests/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected` |
| 2 | 三种请求事件按派发前后的事务状态差分生成 | 2.2：每条被受理的变更恰好写一条事件，内容没有变化的更新也写；差分看不见没有变化的更新，改为按请求条目逐条生成 | `tests/life/test_errand_requests.py::test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt` |
| 2 | `quantity` 的 Schema 写出取值范围 | 2.1、2.3：`expected` 只给类型与格式，上限由校验执行并写在 `why` | `ErrandChange`（`src/worldmock/life/models.py`） |
| 2 | 动作结果里的证据只取到店成交的顶层订单 | 2.4：顶层 `orders` 里状态为已送达的订单，含到店成交与当场送达的远程订单 | `src/worldmock/life/errands.py`（按 `status == "delivered"` 取，不看履约方式） |
| 2 | 只写「同一提交内排在新增事件之后的证据才累计」 | 2.4 另有一句：本次提交里被取消的事务不再接受该提交的证据 | `tests/life/test_errand_completion.py::test_a_gift_followed_by_a_cancel_in_one_round_credits_nothing` |
| 2 | 唯一出现的例外只列同秒续接一处 | 2.6：三处回显位置（续接带回的调用参数与 tool 消息里的改动、当日操作备忘、拒绝结果明细里所写的内容） | `tests/integration/test_unique_presence_run.py::test_an_errand_full_row_appears_only_in_the_todo_layer` |
| 2 | 取消原因直接取自待结算请求 | 2.9：`control_items` 是控制请求的唯一读取入口，变更经 `control_items.control_errands` 读取 | `src/worldmock/life/errands.py`、`tests/life/test_control_items.py::test_control_errands_reads_every_change_in_order` |
| 3 | 场景值优先：场景窗口与卡片抽取的下班、到家冲突时把卡片锚点降级为 null | 4.2：覆盖与冲突规则不变，落不下的槽保留卡片值（可以是 null）并记 `CARD_FIELD_CONFLICT`；降级只是 4.1 的抽取规则，场景覆盖不调用降级函数 | `tests/profile/test_injection.py::test_a_scenario_window_violating_the_work_anchor_order_keeps_the_card_value_as_a_conflict`；`settle_work_anchors` 的调用方只有 `src/worldmock/profile/extractor.py` 与卡片模型的校验器 |
| 3 | 下班、到家的顺序约束写三条 | 4.1 有四条：`leave_work` 为 null 而 `arrive_home` 非空时出门起点不晚于到家起点 | `tests/profile/test_models.py::test_work_anchor_order_constraints_are_enforced` |
| 3 | 三种声明例外不计缺勤（含当日不当班） | 4.5、6.2：两种（班次内的休息时段、任务要求地点的外出）；当日不当班不产生班次，不进统计 | `tests/evaluation/test_workload_metrics.py::test_breaks_and_task_outings_are_not_absence_and_rest_days_are_left_out` |
| 3 | 抽取的测试只列共识乱序时降级；零点计划输入的测试没有零点边界一例 | 验收台账「作息锚点」「计划与义务」：三次采样各自乱序但共识合法时不降级；前一日休息、当日当班一例 | `tests/profile/test_extractor.py::test_samples_out_of_order_on_their_own_but_legal_in_consensus_are_not_demoted`、`tests/life/test_day_planning.py::test_plan_input_duty_is_for_the_new_day_at_the_midnight_boundary` |
| 3 | `custom/agents/life_continuation.py` 的文件头可补一词 | 4.4：`duty` 不进同秒续接的 `changes`；该文件在阶段 3 完全不改 | `git diff c87118a 59b81a3 -- custom/agents/life_continuation.py` 为空 |
| 4 | `queries` 子命令打印七个键，没有反思重试 | 6.3：八个键，含 `reflection_retries` | `scripts/inspect_run.py`；`tests/life/test_query_cost_report.py::test_query_cost_of_a_run_without_queries_and_the_printed_keys` |
| 4 | 变异句「可执行筛选放到切页之后或在未命中方式的动作上判定」只登记后一种情形 | 规格同句的两种情形都登记变异 | 登记表 `SPEC_MUTATIONS` |
| 4 | 来源候选的排序函数拆在 `purchase_offers.py` 供查询复用 | 5.2 只要求来源解析是被两侧调用的同一个纯函数；两个纯函数放在 `name_resolution.py`，购买一侧委托给它们，宿主的导入链不引入购买模块 | `resolve_source`、`near_source_names`（`src/worldmock/life/name_resolution.py`）；`tests/life/test_purchase_channels.py::test_query_and_purchase_resolve_each_from_form_to_the_same_stores` |
| 4 | 只有接口合同类的文档作业先于代码 | 「实施计划」先后约束：只有被测试逐字比对的文本、设计章节与接口合同字段条款随代码，其余【同】作业先于代码；`docs/VERIFICATION.md` 与手册页在阶段 4 的第一个提交 `8789dec` 落地 | `git log` |
| 4 | `from` 非 null 不得为空白，由请求模型拒绝 | 5.1 只对 `name`、`way`、`category` 写了这条；5.5 第一行覆盖一切既不是地点也不是渠道的 `from`。编排方裁定按规格字面（见上表） | `QueryWorldRequest`（`src/worldmock/life/world_query.py`） |

另有三处属台账的被钉点清单不全或行号过期（卡片结构版本的断言多出三处、接口合同与人物 Prompt 规格逐字文本的行号、工具交互登记表里变异执行器的位置），不涉及行为，按实际位置修改。

### 留给后续的出入

宿主 `revise_day_plan` 在并入保留的已推进目标后超过八条时，协议反馈里超限一项的 `written` 写的是所提交目标的条数（`custom/agents/life_agent.py` 的 `_validate_plan_goals`），
而接口合同写的是所提交的目标本身。规格把这个宿主函数列为不改动的部分，本特性没有改它；这一出入已作为单独的任务登记，不在本记录的范围内。

查看器有三处与文档不一致，都是实现后文档核对时发现的（写本记录时按 `tools/run-viewer.html` 与 `tools/run-viewer-context.js` 核对），本特性没有再改查看器代码，写本记录时留给后续处理（其后已补齐，见本节末「查看器出入的处理」）：

- `docs/dev/SPEC-run-viewer-names.md`「运行内对象名称」写控制请求摘要在活动名与 summary 之后写事务变更的种类与事务名称，六种个人事务事件写事务名称并按既有名称索引显示快照中的商品；
  查看器的控制请求摘要仍只写各项的活动名与 summary，事务事件只显示种类的中文说法。规格 2.9 对查看器的要求（缺 `errands` 键即报错，世界结构 8 的运行用基线提交的查看器查看）已满足。
- `docs/dev/SPEC-run-viewer-context.md`「增长与退出」写进度层含事务提醒与办成两种只写名称的记录；查看器内置的进度层规则文字没有写这两种记录
  （实施台账安排随阶段 2 的代码同步的一处，没有做），待办层与环境层的规则文字已同步。
- 查看器的工具定义条目仍写「八个世界请求工具与三个记忆查询工具」，没有随本特性改动。

这三处写在两份查看器规格的文件头与版本表，以及 `docs/INDEX.md` 文档地图的对应两行。`tests/tools/` 的 62 个 Node 用例通过（写本记录时执行 `node --test tests/tools/run_viewer.test.cjs tests/tools/run_viewer_context.test.cjs`），没有用例逐字断言这些文字。

**查看器出入的处理（2026-10-04，本记录写成之后）。** 上述三处由 `1346e0d` 补齐：控制请求摘要写各条事务变更的种类与名称，六种事务事件写快照里的事务名称与商品名称，
唤醒原因 `errand_reminder_due` 写所提醒事务的名称；进度层规则文字写两种事务记录，工具定义条目改为七个世界请求工具、两个世界查询工具与三个记忆查询工具。
对该提交的独立核对把真实引擎写出的行（`engine_factory` 驱动正式白领场景，记下事务、交付提醒、结算办成）载入查看器，又查出并在随后的修正提交里改了以下各处：

- 人在家里记下事务时，观察的 `data.products` 为空，事务事件对运行已记有名称的商品显示缺失提示；名称索引增观察的 `errand_item_bindings`（商品身份到商品名）为只补缺的来源。
- `controlErrands` 改为与 `control_errands` 同一规则（须是对象、八个键齐全，不查值的类型），条目级的出入与缺 `errands` 键同样报错；缺键报错另经建索引这一步（页面加载数据库的入口）断言。
- 快照名称与事务内部身份相同时按缺失处理（身份只用于这次比较，不留在页面索引里）；唤醒原因找不到对应的提醒事件时显示缺失提示，不再只写错误日志。
- 查看器夹具的七层行动请求改为本特性的层形状（待办层 `errands`、环境层 `duty`、两种事务记录与宿主现行的十二个工具定义）。

两份查看器规格的文件头与版本表（各增 `2026-10-04-errands-viewer` 行，名称规格的条款随该行补充三处）及 `docs/INDEX.md` 文档地图的对应两行已改写。
`tests/tools/` 的 75 个 Node 用例通过（修正提交上执行 `node --test tests/tools/run_viewer.test.cjs tests/tools/run_viewer_context.test.cjs`，并经 `tests/tools/test_run_viewer.py` 执行）。
证据层级仍是离线测试：真实引擎写出的行只在核对时由临时脚本载入查看器，没有新的真实运行。

## 文档一致性

以下各项写本记录时在含全部实现后文档、重新汇编的设计说明书与登记表阶段 7 标记的工作树上执行（代码为 `a7e2fdc`）。

- **实现后文档。** 实施台账标【后】的作业与工作树里的改动逐项对照：除实现状态文档的 IS1–IS15（最后单独提交）外都有对应改动，与本记录同一提交。
  被该规格修订的各文档文件头改为修订部分已实现并通过离线验收，冻结时的未实现注记不再保留，有版本表的文档各加 2026-10-04 的一行；
  `docs/VERIFICATION.md` 测试矩阵补入该规格条款，`docs/dev/ANALYSIS-spec-coverage.md` 增「个人事务与工作安排增量映射」，`docs/INDEX.md` 文档地图登记本记录（状态 `验收记录`），
  「按任务读」个人事务与工作安排一行与版本表有对应条目。
- **核对所按的提交。** 这些文档的版本表行多写「按 `d8a41d3` 的代码核对」：核对在 `d8a41d3` 的工作树上做，其生产代码与 `a7e2fdc` 相同
  （`git diff d8a41d3 a7e2fdc` 在 `src/`、`custom/`、`scripts/`、`tools/`、`tests/tools/` 上为空）。`d8a41d3` 之后 `f23f697` 为登记表「工程门禁」「文档一致性」两行补了节点，
  `docs/VERIFICATION.md` 门禁节与 `docs/dev/ANALYSIS-spec-coverage.md` 增量映射里关于登记表的说法按 `a7e2fdc` 的登记表写：以脚本逐行比对增量映射表与登记表，
  列有节点的二十七行所列节点与登记表逐个相同、顺序相同；实现后文档与本记录引用的测试节点、测试名与仓库路径都解析到现有的函数或文件。
- **覆盖率基线。** `docs/dev/coverage-baseline.md` 的「基线记录」随重新生成的报告改写：首节为 `a7e2fdc` 的门禁结果，一日运行保真一节改为历史基线（实施台账没有单列这一作业）。
- **设计说明书。** 执行 `uv run --frozen --no-sync python scripts/build_design_doc.py` 重新汇编 `docs/design/worldmock-design.html`，随后 `--check` 判为 PASSED；汇编结果与源同一提交。
- **文档门禁与目录。** `uv run --frozen --no-sync python scripts/documentation_gate.py`：扫描 140 份文档，0 处断链，PASSED。`scripts/build_catalog.py --check`：目录各文件逐字节相同。
- **登记的四个节点与被文档逐字比对的文本。** 台账「文档一致性」行登记的四个节点
  （`tests/test_documentation_gate.py::test_real_repository_documentation_links_resolve`、
  `tests/test_design_doc_build.py::test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current`、
  `tests/test_dayrun_fidelity_acceptance.py::test_anchored_links_in_docs_point_to_existing_headings`、
  `tests/test_dayrun_fidelity_acceptance.py::test_docs_carry_no_pending_markers_of_this_feature`）所在的三个文件，连同人物 Prompt 与时间与能量两份登记表的守卫、覆盖率门禁与风格门禁的测试，
  以及与接口合同逐字比对系统层、零点计划模板、反思文案和工具用途句的测试文件，共十三个文件一次执行
  （`tests/test_documentation_gate.py`、`tests/test_design_doc_build.py`、`tests/test_dayrun_fidelity_acceptance.py`、`tests/test_persona_prompt_acceptance.py`、
  `tests/test_time_and_energy_completeness.py`、`tests/test_coverage_gate.py`、`tests/test_style_gate.py`、`tests/integration/test_seven_layer_request.py`、
  `tests/life/test_day_planning.py`、`tests/integration/test_strict_reflection.py`、`tests/integration/test_tool_description_references.py`、`tests/life/test_scenario.py`、
  `tests/footprints/test_quality.py`）：373 passed。带锚点的链接都指向现有标题，文档里没有留下冻结时的临时状态标记。
- **登记表的静态守卫。** `tests/test_errands_workday_acceptance.py` 与 `tests/test_tool_interaction_acceptance.py` 除逐个变异运行之外的判定（`-k "not source_mutation_is_killed"`）：12 passed、147 deselected；
  这时登记表的 `STAGES_DONE` 已含阶段 7。两份登记表的逐个变异运行（101 个与 46 个）在 `a7e2fdc` 的最终全量运行里执行过，这里没有重跑。
- **与查看器代码的出入。** 实现后文档核对出的三处，见「留给后续的出入」；其后已补齐，见同节「查看器出入的处理」。
- **实现状态文档。** `docs/dev/implementation-status.md` 由最后一个提交更新，不在本记录所在的提交里。

## 环境敏感项、未执行与范围外

### 环境敏感项

以下三项是在机器负载很高时观察到的，都不是代码缺陷：

- `tests/integration/test_strict_endpoint.py::test_validate_refuses_the_main_site[identity-on-main-site]` 在同时有 30–140 个 Python 进程运行时失败过两次，错误是 `Ray worker cannot import ray in a clean process`；每次单独重跑都通过，在各次干净的 L2 运行与 L3 预跑中没有失败。
- `tests/integration/test_read_query_runtime.py` 里使用真实 Ray 的用例在高负载下可能撞上 Ray 30 秒的节点注册等待。
- 变异基线合并运行里有一个杀手用例失败过一次，错误是读取观察事件时的 pydantic `string_unicode`；单独重跑通过，此后没有再现，根因没有找到（怀疑是内存压力）。

另外，写本记录的会话里 shell 的 `PATH` 上没有 `node`（`C:/Program Files/nodejs/node.exe` 存在）；`tests/tools/` 的用例断言而不是跳过 `node` 可用，运行它们之前要把该目录加入 `PATH`。

### 未执行

- **阶段 6：配对全天复跑（只报告）。** 未执行。它是计费的真实运行，只在用户明确要求时启动，本轮用户决定不启动；规格计划每臂三次，实际每臂 0 次，未达复跑要求。
  台账「复跑指标（只报告）」各项因此没有任何结果：事务记下后次日可见并被办成的比例、提醒后行动、取消与结束时未完成（读 `quality.json` 的 `errand_metrics` 与 `conclusion_memories`）；
  离开工作地点与到家相对窗口的偏离、工作负荷（读 `scripts/inspect_run.py workday`）；查询调用、返回量、购买结果与反思校验重试（读 `scripts/inspect_run.py queries`）；
  决策 token 以及同时报告的协议错误与截断（读 `quality.json` 既有的各分组）。
  这些读取入口本身的测试登记在阶段 2、3、4 的台账行；它们不是复跑结果。
- **strict 端点探针。** 未运行。`scripts/probe_deepseek_strict.py` 是计费的真实调用。真实的 strict 端点还没有收到过新的控制工具 Schema（可空整数 `quantity` 是此前没有用过的构造）与六键 `query_world` Schema；
  已有的证据只是本地的 strict Schema 校验用例（如 `tests/integration/test_activity_schema.py::test_control_tool_schemas_inline_the_errand_change_object`）。
- **真实模型调用。** 本特性的全部证据来自离线测试，没有任何一次真实 DeepSeek 调用；脚本化模型与测试身份是测试夹具，不能当作真实模型运行或正式 CLI 的认证身份。
- **项目整体。** 仍为 NOT-COMPLETE：正式 12 周 DeepSeek 运行、三份隔离盲评与完整自然日的 LabelKit 交付三项真实验收都不由本记录提供。

### 范围外

- **数据集侧。** 规格[数据集侧登记](SPEC-errands-workday.md#数据集侧登记)的各项（逐店声明、任务输入、料理、目录发布、预检、重建与运行驱动、场景与卡片、控制请求与 LabelKit、查询）在 WorldMock-Dataset 仓库由另一个会话处理，
  不计入本仓库的验收台账，只阻塞阶段 6。规格要求验收记录登记数据集提交号；数据集侧的进展不在本记录所依据的事实之内，本记录没有登记数据集提交号。
- **宿主 `written` 与查看器的出入。** 见「留给后续的出入」；前者已作为单独的任务登记，后者写本记录时留给后续处理，其后已补齐（同节「查看器出入的处理」）。
- **规格的范围外登记。** 规格[范围外登记](SPEC-errands-workday.md#范围外登记)所列各项（待办层统一呈现工作、订单与消息，不可核验的个人事务，环境拒绝班内离岗，班次边界的唤醒等）不在本次范围。
