# 去掉 AgentSociety2：开发规格

> 状态：冻结，2026-11-17。冻结后的改动另立开发记录，本规格不再改写。
> 依据：[提案](PROPOSAL-agentsociety2-removal.md)与[负责人裁定记录](RULING-2026-11-17-owner.md)；提案第十二章的十二行都取「推荐」一栏。各章与提案的对应写在各章「目标与依据」；提案留给实现的细节在各章「规格阶段的决定」里定下，汇总在本页末尾。
> 代码基线：WorldMock `74f1b94908f1e77395ee1278093b29f8e5b299f2`（生产代码与 `cb802721` 相同），分支 `refactor/agentsociety2-removal`，工作树 `.work/wt/as2-removal`。文中的「现在」「今天」与行号都指这个提交。AgentSociety2 是上游 `tsinghua-fib-lab/agentsociety` 的 `670c94ff`（2.8.7）加 `patches/agentsociety2/SERIES` 的五份补丁，源码摘要 `0a7b9b06fe5bbf70836fefe407eceb830b2bc6acaa8c81db98176ee3c99709ba`，在工作树的 `vendor/agentsociety2/packages/agentsociety2/agentsociety2/` 恢复；文中 `as2/…` 是这个目录的简写。
> 证据：基础检出 `.work/agentsociety2-removal/` 下的梳理结果（`digest/*.md` 与 `map/*.json`，每条结论带行号）、设计稿与评判（`design/`）、事实核对（`verify.md`），都不入库，是事实而不是权威。本规格不调用模型；规格之后的计费调用只有第 10 章的一次录制与一次短试跑。

## 负责人的指示

裁定记录里负责人的原话：

> in a separate worktree
>
> confirm the details of the implementation plan and the list of files to change (especially the design document) of proposal docs\dev\agentsociety2-removal\PROPOSAL-agentsociety2-removal.md, come up with the final feature-development spec document. spec is a detailed doc about how to implement mental model described in proposal - so no "I think this is better" or any kind of drifting of spec to proposal.
>
> then implement the functionality described in spec . Do not defer any pending implementation work — enforce this with test cases — and do not build anything the spec doesn't describe.
>
> this task is unattended, for questions takes recommend solution and log them, put them in final reports; spec, and Impl gets one worflow, each workflow gets one pass review and fix, no separate review and fix workflows. apply all changes at once with multiple agents in workflow, then run test, then one pass review & fix; don't run tests here and there which would slow down task
>
> when finished, commit, merge, and push to main

据此：

- **规格只说怎样实现提案。** 不另提方案，不加提案没有的功能；提案没写细的地方，各章在「规格阶段的决定」里取与提案和 `AGENTS.md` 最一致的做法，一行一条。
- **问题取推荐做法并记下。** 本该问负责人的问题，取推荐的回答，在「规格阶段的决定」里以「（问题）」开头记下；本页末尾汇总，实施的最终报告再列一遍。
- **一个规格工作流、一个实施工作流，各带一轮评审与修正。** 实施时多个代理在同一个工作树里一次改完，再统一跑一次全量测试，再做一轮评审与修正；这条指示取代提案第九章「按可运行的提交排列」的提交次序（裁定记录「读法」第三条），编排在[第 10 章](spec/10-implementation.md)。
- **不推后，用测试保证；不多做。** 每条规则一条经真实入口的行为测试，每个定下的中心事项一条登记的源码变异，release 档拒绝跳过与预期失败，覆盖率门，另有守住「去除完整」的守卫用例（第 10 章「不落下、不多做」）。规格没写的东西不做，评审时发现就删掉。
- **合并并推送到主干。** 由实施工作流的集成者完成（第 10 章「合并与推送」），不经 PR。
- **仓库规则照旧**（`AGENTS.md`）：不留兼容、回退与迁移，过时代码直接删除；一条规则只在一处；`docs/design/` 是当前行为的唯一权威，先改设计再改代码；生产代码每行至多 120 字符、每函数至多 50 行与 5 个入参、每文件至多 2000 行，函数有带中文的 Doxygen `@brief`；注释中文，代码、日志与报错英文。

## 术语

| 术语 | 含义 |
| --- | --- |
| 运行宿主 `RunHost` | `src/worldmock/runtime/lifecycle/run_host.py` 里驱动决策轮的类：取调度状态、开轮、让就绪人物逐个决定、原子提交、没人就绪才推进、写共同检查点、在三个触发点跑零点任务（第 01 章） |
| 决策轮 | 一次「开轮 → 各人物决定 → 原子提交」；三个数据类型 `DecisionContext`、`DirectToolCall`、`SchedulingState` 在 `src/worldmock/state/decisions/round.py`（第 01 章） |
| 引擎门面 `LifeEngine` | `src/worldmock/simulation_engine/engine.py`，唯一写入者；人物经它观察、登记请求、只读查询、写证据；只有运行宿主导入它（第 02 章） |
| 引擎句柄 `EngineHandle` | 人物模块用 `typing.Protocol` 声明的五个同步方法：`observe`、`submit_action`、`read_query`、`record_model_exchange`、`record_memory_update`（第 03 章） |
| 人物 `LifeAgent` | `src/worldmock/agents/life_agent.py`；每个决策新建一个、决定完就丢弃（第 03 章） |
| 冻结输入 `PersonInputs` | 运行开始时由 `person_inputs(persona, stage_identity)` 建好的人物卡片、卡片结构、名字与模型身份（续接开关与回合上限在模型身份里）（第 03 章） |
| 回合循环 `ReactLoop` | `src/worldmock/agents/protocol/react_loop.py`：至多 `max_react_turns` 个模型回合、调用身份、证据次序、纠错、续接前缀（第 03 章） |
| 模型客户端 `ModelClient` | `src/worldmock/configuration/model/model_client.py`；整次运行一个，持有一个 OpenAI SDK 异步网络客户端，直接调用 `litellm.acompletion`；两类重试、停放、单次期限、失败发送记录；`src` 里唯一导入 litellm 的模块（第 04 章） |
| 业务客户端 | `PlanDayClient`、`MemoryDigestClient`（`IndependentModelClient` 的子类），只在有待办的零点任务里按需包装模型客户端（第 01、04 章） |
| 零点任务 | 回顾（日、月、年）与零点计划，在 `src/worldmock/runtime/lifecycle/midnight.py`；触发点：每个共同检查点之后、零点推进之后、普通恢复启动时（第 01 章） |
| 底稿 | `model_request` 事件里的请求字典，恰为交给模型客户端的六个键（第 04 章） |
| 人物工作区 | `run_dir/agents/agent_NNNN/`，只有 `MEMORY.md` 与 `memory/episodes.jsonl`；共同检查点里的 `agent:<id>` 快照（第 05 章） |
| 记忆存储 `WorldMockMemoryStore` | 每个人物一个、整次运行共用；步尾由 `save_step` 写条目与视图（第 05 章） |
| 「记忆已更新」 | `memory_updated` 事件（第 05 章） |
| 基线、基线工作树 | 提交 `74f1b949` 加锁定的 AgentSociety2；工作树 `.work/wt/as2-base`，有自己的虚拟环境（第 10 章【V2】） |
| 录音、录音重跑、计划内重启 | 一次真实运行里 DeepSeek 的全部回答；由本机假服务照录音作答、让新旧两版各重跑同一段日子（`scripts/recorded_response_run.py`）；录制时在检查点停下再续跑，重跑时在同一位置重现 |
| `raw-order` | 比较两次重跑发出的原始请求正文，连键序（第 09、10 章） |
| 运行比对、声明规则 | `scripts/compare_runs.py`；本次只允许 `workspace-members`、`memory-updated-form`、`model-request-parameters`、`prompt-fingerprint` 四条规则（第 09 章） |
| 掩码完整日比较 | `scripts/compare_complete_days.py`，基线与新分支各用自己的虚拟环境录两个完整日（第 09 章） |
| 线路探针、故障注入比较 | 在基线环境里把同一批请求分别经 AgentSociety2 的客户端与新的模型客户端发到本机服务，比较正文与重试行为（第 04 章） |
| 轨道、集成者 | 实施时一个代理独占的一组文件；做准备、提交、门禁、证据与合并的唯一代理（第 10 章） |
| 全量测试、终门 | 实施提交上唯一一次分片全量测试；最终提交上的放行门禁（第 10 章） |

## 人物会注意到什么

什么都不变：

| 情形 | 今天 | 之后 | 章 |
| --- | --- | --- | --- |
| 12:00 决定去吃饭：看到的七层请求、能用的工具、怎样结算 | 由 WorldMock 的引擎与提示规则决定，经 AgentSociety2 的进程转一手 | 逐字节相同；发给 DeepSeek 的正文逐字节不变，由录音重跑与 `raw-order` 证明 | 01–04、10 |
| 同一秒接着再决定 | 环境从证据重建续接，人物不带内存状态 | 同样由引擎从证据重建；人物每个决策新建，由结构保证不带状态 | 01、03 |
| 零点的回顾与日计划 | 每个检查点之后补跑，零点推进后补跑，普通恢复时补跑 | 同样三个触发点、同样次序 | 01 |
| 从检查点恢复、反事实重放 | 由 `Society` 与 `LifeEnv` 的钩子触发 | 运行宿主三种启动顺序显式写出，步骤相同 | 01 |
| 断网与限流 | 停放至多 12 小时，单次等待至多 120 秒 | 相同；限流只按异常类型判定（正式配置的普通重试为 0，不起作用） | 04 |
| 16 个回合用完 | 运行故障 | 仍是运行故障，文字不再提已不存在的 `finish` | 03、06 |
| 被拒的请求、协议纠错、截断重试 | 现行规则 | 不变 | 03 |
| 被外部提问（只读问答） | 生产中没有入口，真走到会报错 | 删除；人物的生活决策不受影响 | 03 |

## 记录形式的变化

事件的条数与顺序都不变；会变的只是记录的写法（提案第七章）：

| 对象 | 现在 | 之后 | 章 |
| --- | --- | --- | --- |
| 人物工作区 | `run_dir/as2/agents/agent_NNNN/` 六个文件：`AGENT.json`、`config.json`、`MEMORY.md`、`memory/episodes.jsonl`、`memory/state.json`、`life_prompt_snapshot.json` | `run_dir/agents/agent_NNNN/` 两个文件；新建时是空目录；LF；内容不变就不写；首次不再写英文模板 | 05 |
| `memory/episodes.jsonl` 的条目 | AgentSociety2 的格式，编号 `m_00000000_NNNNNN` | 十个字段，去掉恒为 0 的 `tick`、`tick_duration_sec`，恒为空的 `refs`，以及 `step_index`、`step_count`；编号 `m_000001` 起连续，恢复时校验连续且唯一 | 05 |
| 「记忆已更新」`memory_updated` | 十四个键，含恒为 `false` 的 `consolidated`、恒为 `null` 的 `reflection`、`state_sha256`；写入点闭集含 `reflection` | 十一个键；写入点闭集只有 `activity_conclusion`；人物第一条的 `memory_before_sha256` 是 `null` | 05 |
| 共同检查点里的人物快照 | 六个文件的 tar | 两个文件的 tar；快照格式与参与者不变 | 05 |
| 模型请求的底稿 `model_request` | 人物回合多 `stream`、`max_retries`、`transport_max_retries`；零点计划与回顾多 `stream` 与恒为 0 的 `max_retries` | 恰为 `model`、`messages`、`tools`、`tool_choice`、`max_tokens`、`extra_body` 六个键；请求摘要随之变 | 04 |
| 提示指纹 `prompt_sha256` | 绑定生活与只读两套系统层 | 只绑定生活系统层与七层 Schema | 03 |
| 代码身份 `code_sha256` | 含 `custom/` 与导入的 AgentSociety2 包 | 只哈希 `src/` 下的 `.py`、`pyproject.toml`、`uv.lock`；旧运行不能续跑或重放 | 07 |
| 运行目录 | 有 `as2/` 与空目录 `env/LifeEnv` | 都没有 | 01、05 |
| 命令行 | 有 `--project-root` | 删除；项目根由已导入的 `worldmock` 包推出 | 07 |
| `status.json` 与失败路径事件的文字 | 人物故障套一层 `Agent decision failed: [...]`；同秒决策次数用完时多一条假的「决策轮没有开着」 | 中止原因是原异常的 `repr`；只记一条真实的错误；不再解开 Ray 的任务错误 | 06 |
| 运维日志 | 开始组那一轮 `DECISION_ROUND_COMMITTED` 计 1 个动作 | 计 2 个（如实计数）；失败路径上 `DECISION_ROUND_ABORTED`、`RUN_FAILED`、`RUN_CRASHED` 的说明随故障文本变 | 02、06 |
| 发给 DeepSeek 的请求头 | litellm 默认的 httpx 外壳包 aiohttp | OpenAI SDK 默认的 httpx；正文不变，请求头可能不同（裁定 9） | 04 |
| 工具指纹 `tool_schema_sha256` | `aa0ed7fa…` | 不变 | 03 |

## 版本与指纹

| 对象 | 位置 | 之后 |
| --- | --- | --- |
| 运行清单 `RUN_MANIFEST_SCHEMA_VERSION = 7` | `src/worldmock/state/identity.py:12` | 不变 |
| 导出 `EXPORT_SCHEMA_VERSION = 8` | `src/worldmock/recording_engine/export/export.py:39` | 不变 |
| 世界状态 `WORLD_SCHEMA_VERSION = 15` | `src/worldmock/state/world/world.py:21` | 不变 |
| 事件与检查点模型 `schema_version` 1 | `src/worldmock/recording_engine/events/models.py:173`、`:186`；`src/worldmock/recording_engine/storage/store.py:584` | 不变 |
| 质量报告 `schema_version` 10 | `src/worldmock/recording_engine/reports/quality.py:92` | 不变（第 05 章：报告的键与问题码不变） |
| 规则 `RULES_VERSION = 6` | `src/worldmock/exchange_engine/definitions/constants.py:7` | 不变 |
| 目录 `format_version` 6 | `src/worldmock/resource_engine/catalog/models.py:52` | 不变 |
| `persona.toml`、`config.toml` 的 `schema_version` 2 | `src/worldmock/configuration/inputs/models.py:158`、`:231` | 不变 |
| 行为工具 `behavior-metrics-4` | `tools/analysis/behavior/metrics.py:130` | 不变（第 07 章） |
| 环境收据 `environment-1` | `scripts/bootstrap_environment.py:170` | 不变；去掉 `as2_source_sha256`，恰十个键（第 07 章） |
| AgentSociety2 源码锁定摘要 `AS2_LOCKED_SOURCE_SHA256` | `src/worldmock/runtime/framework/setup.py:26` | 删除（第 07 章） |
| 提示指纹 `life_prompt_sha256()` | `src/worldmock/agents/prompt/life_prompt.py` | `1947c112bf46fd262cc159d4163a3831a0aa55d42c70ad7645ffd3ba40524a05` → `cd454d1b18c8a211d8eeb0bb25aaba7ff2ef4b0732c9a2bd7aaa4385a5f67842`（第 03 章【A28】） |
| 生活系统层正文的 SHA-256 | 同上 | `6f51d166a9f94a3fc12ff49f461c30ef76250a9458bace868d4a96f67f2cb5db`，不变（第 03 章【A29】） |
| 工具指纹 `life_tool_schema_sha256()` | 以后在 `src/worldmock/agents/life_agent.py` | `aa0ed7fa01f00558ac94d6e25e71a36d983f25dbb06834c2c31911cffec5bd7e`，不变 |
| 代码身份 `runtime_code_sha256()` | `src/worldmock/runtime/framework/setup.py` | 值变；范围与签名按第 07 章 |
| 依赖锁 | `uv.lock`、`requirements/lock.txt`、`requirements/build-lock.txt` | 135 → 59 项、133 → 58 条、只剩 `setuptools`；保留包的版本与哈希不变（第 07 章） |
| 源码变异台账 | `tests/dataset_acceptance.py` | 298 → 313 条（第 10 章「测试」） |
| 两份完整日基线、查看器样例 | `tests/fixtures/dayrun-activity-baseline.json`、`tests/fixtures/dayrun-callback-baseline.json`、`tests/fixtures/viewer-context.json` | 全部生产改动之后重冻、离线重录各一次（第 09、10 章） |

「记忆已更新」少三个键、人物工作区缩成两个文件，都不升任何版本号：旧运行已经因代码身份与提示指纹不能续跑或重放，读到旧格式记录的质量报告按键集合不符明确报出（第 05 章【W19】），提案第七章的变化表也没有列版本号（第 10 章「规格阶段的决定」最后一行）。

## 章节

每章的小节都是：目标与依据、行为、接口与文件、设计文档、测试、不在本章、规格阶段的决定。

| 章 | 内容 |
| --- | --- |
| [01 运行宿主与决策轮](spec/01-run-host.md) | 一个进程、一个事件循环；`RunHost` 的步序、同秒再决策、每步一个共同检查点、三种启动顺序、零点任务的三个触发点、身份格式、故障中止与运行级登记的位置、状态阶段、关闭；runner 装配唯一的模型客户端与运行宿主；零点任务搬到 `midnight.py`；决策轮三个类型搬进 `state/decisions/round.py` |
| [02 去掉 LifeEnv：引擎门面、只读查询与提交结果](spec/02-engine-facade.md) | LifeEnv 与只读查询转发的删除和各项职责的去向；引擎的只读查询 `ReadQueryResponder`；`observe` 与只读查询的出口深拷贝；提交返回本轮全部结果；引擎里框架类型与文字的替换；工具面一致性的单元测试；只有运行宿主导入引擎 |
| [03 人物、回合循环与只读问答的删除](spec/03-agent-loop.md) | LifeAgent 搬进 `src`、每个决策一个实例；冻结输入；五方法的引擎句柄；回合循环；`ProtocolFeedback` 取代 `HostFeedback`；请求字典按最终键序构造；只读问答与提示快照的删除、提示指纹 |
| [04 模型客户端与模型请求底稿](spec/04-model-client.md) | `ModelClient` 的构成、寿命、重试、停放、期限、失败发送记录与最终失败文字；模块边界与价目表变量；业务客户端；底稿只记六个键；线路探针、故障注入与固定样例 |
| [05 人物工作区、记忆文件与「记忆已更新」](spec/05-workspace-memory.md) | 工作区路径与两个成员；`MEMORY.md` 的模板与预算；条目的十个字段与连续编号；严格读回与恢复只校验一次；步尾保存；`memory_updated` 的十一个键；质量报告 |
| [06 故障文本](spec/06-failure-text.md) | 原异常直达宿主、中止原因写 `repr`；只在引擎轮次开着时中止；运行级登记与双重故障；`status.json` 不再解 Ray 的错误；`__reduce__` 的删除；回合用完的文字；新旧文字对照 |
| [07 安装、代码身份、依赖与许可](spec/07-install-identity.md) | 补丁目录、重建步骤、11 个进程环境变量与导入顺序守卫的删除；代码身份与项目根；`custom/` 并入后的门禁；依赖与锁；引导、新检出核验与 CI；许可原文与来源句；行为分析门禁；工作树的虚拟环境；锁差异与来源核对 |
| [08 设计文档与手册](spec/08-design-docs.md) | `docs/design/`、`docs/manual/`、`README.md`、`docs/INDEX.md` 的改写：`10-runtime.md` 改名「运行装配与决策循环」并新写决策循环，`06-agent.md`「宿主模型协议」新写回合协议，其余各章点名 AgentSociety2、Ray、LifeEnv 与只读问答的条文 |
| [09 比较工具、查看器与测试迁移](spec/09-tools-tests.md) | 录音应答工具、掩码完整日比较与运行比对的改动和四条声明规则；查看器；AgentSociety2 专属测试的移植与删除；导入、测试枢纽、假凭据、工作区夹具；变异台账改指；重冻与重录 |
| [10 实施、验收证据与完成判据](spec/10-implementation.md) | 规格提交；基线工作树；十二条轨道与文件归属；跨章裁定；工作树环境；计费录制与重跑；探针；提交；全量测试；评审与修正；重冻与重录；验收证据与证据层；终门；验证记录；合并与推送；合并之后；完成判据 |

## 不在本规格内

- 主观记忆完全从事件推导、取消人物快照：裁定 3 定为另立提案。
- 多个人物并发决策：提案写明真有需要时先设计「按决策缓冲证据、提交时按序落库」，另立提案；本次就绪人物逐个决定。
- 只读问答的替代入口：裁定 7 删除整条路径，不另补入口。
- WorldMock-Dataset 的修改与验证：由它自己的会话按第 10 章「交给数据集一侧的说明」完成。
- 基础检出的 `vendor/agentsociety2` 删除与重新引导、本地 `CLAUDE.md` 的安装：命令交给负责人（第 10 章【V35】）。
- 正式 12 周运行、三次隔离盲评、一次完整自然日的交付：项目的三项真实验收；本次的证据层是「离线加录音重跑」，项目整体仍为 NOT-COMPLETE。
- `docs/dev/` 下已冻结的旧规格与开发记录（包括 `docs/dev/SPEC-as2-integration.md` 与 `docs/dev/research/as2-feasibility.md`）：不回写，被裁定 2 取代的条文以裁定记录为准。

## 规格阶段的决定汇总

第 01–10 章「规格阶段的决定」的全部行，取各章评审之后的定稿；另有几行（01 章普通恢复的起始时刻核对、02 章删除守卫读哪些文件与两组文档串、03 章 `_record_response` 的签名、06 章禁用的包装文字）是评审时写进章正文、没有单列在章末表里的同类决定，也一并列出。「（问题）」开头的是本该问负责人、取了推荐回答的事项。第 10 章「跨章裁定」改定的行，按改定后的写。

| 章 | 事项 | 决定 | 理由 |
| --- | --- | --- | --- |
| 01 | 三种启动顺序的写法 | 三个公开方法 `start_new`、`start_resume`、`start_counterfactual`，runner 的 `_start` 按 `_PreparedRun` 选一个 | 提案要求三种启动顺序显式写出 |
| 01 | 引擎由谁建、用哪份输入 | 宿主的启动方法用 runner 已加载的 `EnvironmentInputs` 建立或打开；新建运行只读一次模型元数据 | 重读只因 LifeEnv 在另一个进程里 |
| 01 | （问题）模型客户端由谁构造 | `runner._execute` 经 `_model_client(prepared)` 构造并交给 `RunHost`，`RunHost.close()` 恰好关它一次 | 注入让测试直接给替身；关闭只有一处 |
| 01 | （问题）模型客户端的凭据 | `_model_client` 调 `credential_value(prepared.inputs.config.model)`，不存进 `_PreparedRun` | 凭据只在需要它的地方读 |
| 01 | 零点任务的 `client` | 必填的模型层客户端；业务客户端只在发现第一项待办之后由这两个函数构造，调用方不再传业务客户端 | 宿主只有一个模型客户端；没有待办时不建客户端 |
| 01 | `Society` 的时钟与边界核对 | 不移植（`_require_schedule`、五条推进核对、结束时刻核对、`Environment confirmed a different round_id`） | 只有一个时钟；边界由引擎核对 |
| 01 | 保留的 `Society` 核对 | 未知就绪人物与反事实三条，类与文字不变，抛出前写同文错误日志 | 步序照搬；`AGENTS.md` 要求异常分支有日志 |
| 01 | `Counterfactual intervention identity is unavailable` | 留在 runner 的 `_start`，类与文字不变 | runner 自己的核对 |
| 01 | 判断轮次是否开着 | 读 `engine.decisions.round`，引擎不加成员 | 与 02、06 章一致 |
| 01 | 宿主持有引擎的属性 | 公开属性 `engine`（启动前与关闭成功后为 `None`）；06 章的 `self._engine` 读作 `self.engine` | runner 的崩溃日志与测试装配要读它 |
| 01 | 起始时刻核对的文字 | `ProtocolError("Run host start time disagrees with the engine")`，先关引擎、不登记运行失败 | 原文点名 LifeEnv；类与范围不变 |
| 01 | 记忆存储何时建 | `RunHost.__init__` 里经 05 章 `memory_stores` 建好，整次运行共用 | 建存储不读文件 |
| 01 | 人物冻结输入在哪里建 | `_prepare_new_run`、`_prepare_resume` 里用 `person_inputs(inputs.persona, stage)` 建，放进 `_PreparedRun.agents` | 纯计算；名字以 03 章为准 |
| 01 | 清单 `persona_habit_responses` 的来源 | 仍由 `habit_responses(specs)` 计算，不改 | 同一个值，清单字节不变 |
| 01 | `_society_specs` | 改名 `_creation_options`，返回三个键并写警告运维日志 | 去掉框架名字；日志次序不变 |
| 01 | 测试替换模型客户端的接缝 | 替换 `runner._model_client`；核对构造参数时替换 `model_client.ModelClient`；替换宿主时替换 `run_host.RunHost` | runner 在函数内导入这两个类 |
| 01 | `_PreparedRun.memory_records` 的类型 | `Mapping[int, tuple[dict[str, Any], ...]]` | 与 05 章一致 |
| 01 | 推进条件 | 没人就绪且 `next_event_at` 不是 `None` | `step` 是公开方法，结束时刻直接调用不应推进 |
| 01 | 关闭的次序与故障 | 先关引擎、后关客户端，两项都收集；两项都失败时抛 `ExceptionGroup("Run host close failed", …)`；`WorldMock cleanup failed` 删除 | 两项资源独立；双重故障都保留 |
| 01 | `run_host.py`、`midnight.py` 的导入 | 全部模块级导入 | 没有循环导入 |
| 01 | runner 怎样导入 `RunHost`、`ModelClient`、`person_inputs` | 在用到它们的函数里导入，注解只在 `TYPE_CHECKING` 下；`DELAYED_IMPORTS` 加四条（连同 `_creation_options` 两条共六条） | 导入 runner 不得加载引擎与模型客户端 |
| 01 | （问题）多人物的就绪顺序与 `:order:` | 保留通用写法，不缩成单人物 | 提案：按引擎给出的就绪顺序逐个决定；身份格式逐字不变 |
| 01 | 多人物次序怎样测 | `tests/unit/runtime/test_run_host_order.py` 用只实现宿主所调方法的假引擎 | v1.0 的真实引擎只有一个人物 |
| 01 | （问题）两次状态写入放在决策轮的 `try` 之内还是之外 | 之内 | 今天它们处在 `Society` 的同一个 `try` 中 |
| 01 | （问题）宿主是否写运维日志 | 不写 | 提案只列了运维日志的两处变化 |
| 01 | （问题）宿主用例里换不换 `LifeAgent` | 不以回合循环为对象的用例换成 `_ScriptedAgent`；身份格式与 06 章的故障用例用真实 `LifeAgent` | 宿主的步序不依赖回合循环 |
| 01 | 快照的人物集合 | `sorted(self._agents)` | 与今天相同；集合由引擎核对 |
| 01 | 宿主用例的模型客户端替身 | 一个 `_MidnightClient` 按请求里唯一工具的名字分派给两个现有桩，并记关闭次数 | 宿主只有一个模型客户端 |
| 01 | `test_run_host.py` 用例名的权威 | 以 01 章为准，09 章引用 | 规则与测试同章 |
| 01 | `RunHost` 的公开面 | 三个属性、一个特性、六个方法 | 覆盖率门要求公开函数体全部执行 |
| 01 | 来源注明 | `round.py`、`run_host.py`、`midnight.py` 不写 | 内容来自 WorldMock 的补丁与代码 |
| 01 | 普通恢复的起始时刻核对 | 不做 | 提案只给新建运行列了这一步；今天它恒成立 |
| 02 | 只读查询的代码形状 | `read_query.py` 里的 `ReadQueryResponder(context, decisions, actions).answer`，引擎持有 `self.queries` | 与现有协调对象同一写法，不形成循环 |
| 02 | 白名单放在哪 | `_READ_QUERY_MODELS` 原名原样搬进 `read_query.py` | 白名单原样保留；保留变异锚点 |
| 02 | 七个工具名的稳定面 | 不留在生产代码，写进单元测试的字面量 | 运行时不再核对 |
| 02 | （问题）人物侧 `life_action_tool_names`、`life_query_tool_names` | 保留，两个文档串改写 | 人物侧工具集合的唯一声明处 |
| 02 | 深拷贝放在哪 | 只在 `LifeEngine.observe` 与 `LifeEngine.read_query` 的返回处 | 一条规则一处 |
| 02 | 其余返回值 | 不复制 | 提案只要求观察与只读查询 |
| 02 | 交给引擎的对象 | 入口不复制；测试钉住引擎在调用之内转成副本或落库 | 今天就是这样实现 |
| 02 | 引擎方法同步还是异步 | 同步；句柄按同步声明 | 提案：引擎方法全部同步执行 |
| 02 | 两个证据方法的参数注解 | 保留 `Any` 与按属性核验 | 引擎不依赖人物侧类型的位置 |
| 02 | 「轮次是否开着」的查询 | 读 `engine.decisions.round` | 最简 |
| 02 | 谁可以导入引擎门面 | 只有 `worldmock.runtime.lifecycle.run_host` | 删除 LifeEnv 后唯一的编排者 |
| 02 | `read_task_summary` 与 `search_memory` | 保留不变 | 现有入口，五个测试文件直接调用 |
| 02 | 删除守卫扫哪些地方 | `src/`、`tests/`、`scripts/`、`tools/`、`docs/design/`、`docs/manual/`、`README.md`、`docs/INDEX.md`、`pyproject.toml` 的文本文件 | 冻结记录不回写 |
| 02 | `validate_context` 的新文字 | `Context must use the DecisionContext type` | 只去掉框架名字 |
| 02 | `DECISION_ROUND_COMMITTED` 的计数写不写进设计文档 | 不写 | 现行设计文档没有登记它的字段 |
| 02 | `test_env_lifecycle_coverage.py` | 整个文件删除，按职责拆到新归属 | 文件的对象是 LifeEnv |
| 02 | 引擎检查点人物集合核对的测试 | 新增 `test_checkpoint_rejects_an_incomplete_agent_snapshot_set`，直接调用 `engine.checkpoint({})` | 宿主总建全套快照，只有直接驱动引擎才走得到 |
| 02 | 结果逐值比较的写法 | 比较 `model_dump(mode="json")` | 不受容器类型影响 |
| 02 | `advance(tick, target)` | 签名与核对不变 | 提案没有列它 |
| 02 | 删除守卫读哪些文件 | 只读规定后缀的文本文件，跳过 `__pycache__` | 确定性，不解码二进制 |
| 02 | 工具名函数与 `read_query.py` 的文档串 | 逐字定下 | 风格门要求，实施者不必措辞 |
| 03 | `LifeAgent` 的构造 | `LifeAgent(inputs, engine, model_client, memory)`，`decide(context)` | 整次运行的对象一次传入，每个决策的值按次传入 |
| 03 | 冻结输入的类型与位置 | `PersonInputs`（`agent_id`、`name`、`persona_card`、`persona_card_struct`、`model_identity`）与 `person_inputs(persona, stage_identity)`，在 `life_agent.py` | 只有 LifeAgent 用它 |
| 03 | 续接开关与回合上限 | 放在 `model_identity` 里，不另设字段 | 它们本来就属于冻结身份 |
| 03 | `model_identity` 的副本 | `person_inputs` 深拷贝 | 避免与清单别名 |
| 03 | 人设函数的签名 | `persona_layer`、`known_persons`、`card_struct` 直接收卡片结构 | 输入不再是工作区 profile |
| 03 | 引擎句柄 | `EngineHandle` 在 `agents/protocol/engine_handle.py`，五个同步方法 | 单独模块避免循环导入 |
| 03 | 循环与人物的接口 | `ReactLoop` 直接调用 LifeAgent 的公开方法，只在 `TYPE_CHECKING` 下导入 | 不加防御性抽象 |
| 03 | 请求字典与 `ACTION_*` 常量 | `LifeAgent.turn_request` 构造请求；`ACTION_*` 留在 `life_agent.py`；`react_loop.py` 只有循环机制 | 请求内容是人物的事，循环与人物无关 |
| 03 | 请求键序 | `REQUEST_KEYS`：`model`、`messages`、`tools`、`tool_choice`、`max_tokens`、`extra_body`（第 10 章跨章裁定） | 三条路径一种次序；键序不进任何字节 |
| 03 | 只跑回合循环的入口 | 公开方法 `LifeAgent.run_turns(context, observation)`；`decide` = 观察 + `run_turns` + 步尾 | 09 章的夹具只跑回合循环 |
| 03 | `ModelCallEvidence` 的位置 | `src/worldmock/state/model/exchange.py`，字段同上游 `react.py:73-94` | 人物产出、引擎读取，两边都能导入 |
| 03 | 共用的 assistant 重建 | `exchange.wire_tool_call` 与 `assistant_message` | 键序与缺省在一处，两侧字节不变 |
| 03 | `HostFeedback` 的替代 | `ProtocolFeedback(report, text)`；`stage_feedback(code, path)` | 协议错误按结构传递 |
| 03 | 阶段故障怎样带调用位置 | `stage_error` 抛 `RuntimeError(text)`，属性 `feedback` 带 `ProtocolFeedback` | 类型与文字不变，位置诊断保留 |
| 03 | 续接前缀的替换 | `send_check`、`end_continuation` 返回新列表与新预算，不改入参 | 原地改写只为框架的列表别名 |
| 03 | `ReactDecision`、`ReactToolResult` | 搬到 `react_loop.py`，字段 `(action, args, tool_call_id)`、`(ok, data)` | 其余字段只服务 `finish` 与追踪 |
| 03 | 走不到的核对 | 删除零回合报错、两处接受一致性核对、`Model request evidence is unavailable`、`LLM is not initialized`、人物侧 `Environment is not initialized`、只读状态不符、`Native tool call id is unavailable` | 构造与解析已保证 |
| 03 | 工具执行 | 同步 | 引擎方法同步 |
| 03 | 步尾调用 | `MemoryStep` → `save_step` → `memory_update_evidence`，同步 | 05 章定义的接口 |
| 03 | 卡片文本与 `MEMORY.md` 身份段 | `persona_card` 是不去空白的 `render_card(...)`，身份段取 `strip()`；`life_context.py` 删除 | 两处字节都与今天相同 |
| 03 | 结束记忆的形状 | `_conclusion_memories` 原样交出 `control_items` 的存储形状；`_CONCLUSION_MEMORY_SOURCE` 删除 | 条目归一化归记忆存储 |
| 03 | 回合用完的文字 | `TURNS_EXHAUSTED = "ReAct decision budget exhausted without a world request"`，先写日志 `LifeAgent %s used all %d model turns without a world request` 再抛 `RuntimeError`（文字归 06 章【F4】，第 10 章跨章裁定） | 只换掉已不存在的 `finish`，类不变 |
| 03 | （问题）恢复时的卡片比对 | 随工作区副本删除 | 卡片每次运行由冻结的 `persona.toml` 构建 |
| 03 | （问题）`09-io-formats.md:87` 的空记忆层 | 只删只读问答那半句 | 生活决策里仍会有空的「可查」 |
| 03 | `empty_memory_layer` | 删除，测试用 `assemble_layer(None, {}, [])` | 只有只读路径用它 |
| 03 | 唤醒原因 | 仍调用 `_visible_wake_reasons` 做形状核对，不存结果 | 存下的值没有读者 |
| 03 | `_quoted` | 删除 | 没有调用者 |
| 03 | 名字相等的证明 | 新建与恢复的运行都用首尾带空格的名字，人设层 `identity.name` 等于原串 | `AGENT.json` 不再有 |
| 03 | 两个工具名函数 | 保留 | 02 章的一致性测试用它们 |
| 03 | 带来源句的模块 | `react_loop.py` 与 `state/model/exchange.py`，用 07 章那一句 | 含上游的循环骨架与序列化 |
| 03 | 删除用测试守住 | `test_retired_person_glue_and_readonly_path_are_gone` 扫源码 | 删掉的东西没有行为可测 |
| 03 | 回合循环用例的位置 | `tests/integration/runtime/test_react_loop.py` | 与 09 章的移植同一文件 |
| 03 | `_record_response` 的签名 | 五个参数 | 风格门 |
| 04 | `ModelClient` 的构造参数 | 仅关键字的 `base_url`、`api_key`、`max_retries`、`transport_max_retries`；模型名取自请求 | 底稿就是交给客户端的东西 |
| 04 | `call` 的签名与返回 | `call(request: Mapping)`，原样返回 litellm 的 `ModelResponse` | 两类调用方都不用改读法 |
| 04 | 请求键的约束 | 键集合恰为六个，否则写日志并抛 `ValueError`；不核对键序 | 多出的键会被默默忽略 |
| 04 | 底稿键序 | 三条路径都按 `REQUEST_KEYS`（第 10 章跨章裁定） | 键序不进任何字节 |
| 04 | 请求超时常量 | `_REQUEST_TIMEOUT_SECONDS = 300.0` | 与今天的 300 秒相同 |
| 04 | `AsyncOpenAI` 自身的设置 | 也是 `max_retries=0`、`timeout=300.0` | 客户端从不带 SDK 缺省 |
| 04 | `acompletion` 上的 `api_base`、`api_key` | 照样显式传 | 与 Router 路径相同，避免 litellm 为日志字段读 `OPENAI_*` 环境变量 |
| 04 | `stream=False` | 显式传 | Router 路径就是显式传的，litellm 内部走同一分支 |
| 04 | 零点业务客户端 | `IndependentModelClient` 包装 `ModelClient`，保留隔离、序列化与两条 `ProtocolError`；删除身份反查、预算注入与 `max_tokens` 截断 | 按需构造仍需要它；其余三项已过时 |
| 04 | 模型名核对 | 不核对 | 只有一份冻结身份 |
| 04 | 失败发送的收集 | 原样移植 `ContextVar` 收集器 | 逐段搬迁 |
| 04 | `close` 的错误处理 | 不捕获，由宿主保留两项故障 | 规则在 01 章 |
| 04 | 日志器与告警文字 | `logging.getLogger(__name__)`；去掉 `via LLMClient(%s)` | 没有角色了 |
| 04 | 许可注明 | 用 07 章那一句 | 全规格唯一写法 |
| 04 | 探针脚本的位置 | `.work/agentsociety2-removal/probe/`，不入库；只有固定样例入库 | 脚本导入 AgentSociety2 |
| 04 | 探针语料 | 五个用例：`action`、`continuation`、`truncation_retry`、`plan_day`、`memory_digest` | 覆盖五种请求形状，不编造请求 |
| 04 | 本机服务 | 标准库 `ThreadingHTTPServer`，在自己的线程里 | 两侧都替换了 `asyncio.sleep` |
| 04 | 故障比较的参数 | `max_retries=2`、`transport_max_retries=1`、停放上限 600 秒、慢响应期限 0.5 秒、固定时钟 | 走到计数与停放两段，不真等 |
| 04 | 仓内故障测试 | 只跑新客户端，对照两侧都确认过的期望值表 | 合并后仓内不能导入 AgentSociety2 |
| 04 | 价目表干净进程测试 | 子进程里记录 `connect`、`connect_ex` 与 `getaddrinfo`，要求零次 | 离线机器上解析先于连接失败 |
| 04 | （问题）连接被拒的错误链尾 | 接受由 `ClientConnectorError <- ConnectionRefusedError` 变成 `ConnectError`，写进验证记录 | 裁定 9 的直接后果；分类、次数与等待不变 |
| 04 | （问题）请求头的字母大小写 | 接受八个 `X-Stainless-*` 名字只差大小写，写进探针回执，不作判据 | 裁定 9 预见；HTTP 头名不分大小写 |
| 04 | 客户端寿命的规则放哪 | 只在 01 章 | 一条规则一处 |
| 04 | 移植用例的名字与位置 | 照 09 章的移植表 | 名字只在一处定 |
| 04 | `_attempt_deadline` | 逐字移植，仍用 `.get("max_tokens")` | 移植的期限用例不改 |
| 05 | 工作区模块的位置 | `src/worldmock/agents/memory/workspace.py` | 工作区里只有记忆文件 |
| 05 | `memory/episodes.jsonl` 何时出现 | 人物第一次步尾保存时写出（没有条目时为空文件） | 新建时目录可以为空 |
| 05 | 恢复只校验一次 | 暂存区里 `restore_workspaces` 是唯一一次严格读回，记录经 `_PreparedRun.memory_records` 交给宿主 | 先校验后发布 |
| 05 | 存储的寿命 | 每个人物一个、整次运行共用 | 人物在决策之间不带状态 |
| 05 | `MEMORY.md` 的预算 | 整份视图至多 4000 字符，超过即 `ValueError`；页头不超过 3990 字符时与今天逐字节相同 | 自述是常量，截断它没有意义；超限走不到 |
| 05 | 步尾保存 | 同步 | 单进程单事件循环 |
| 05 | 条目归一化放在哪 | 记忆存储按 `control_items` 的存储形状构造条目 | 一条规则一处 |
| 05 | 写入时校验 | 新条目先过与读回相同的 `_validate_record` | 写出的就是读回接受的 |
| 05 | 空正文的错误文字 | `Memory episode line {n} has empty text fields` | 与读回规则共用 |
| 05 | 读回的类型与来源 | 类型属于 `SUBJECTIVE_TYPES`，来源恰为 `activity_conclusion`；新错误 `has an invalid source` | 读回核对 WorldMock 实际写的格式 |
| 05 | 连续性的错误文字 | `Memory episode line {n} has a non-contiguous identity`，一条核对同时管连续与唯一 | 与其余逐行错误同一写法 |
| 05 | 条目文件的写法 | 字节有变就整份重写 | 少一个分支，内容相同 |
| 05 | `importance` | `float(value)`，不夹紧 | 请求模型已限范围 |
| 05 | 五键的 `reflection` 快照 | 删除，只认四键 | 同属历史运行的再认证 |
| 05 | 质量报告的反思分支 | 删除，先核对恰为十一个键 | 裁定 4 |
| 05 | `memory_before_sha256` 为 `null` 的约束 | 引擎与质量报告接受摘要或 `null`；只在第一条由结构保证，集成测试核对 | 不加防御性核对 |
| 05 | 步尾的事实窗口核对 | 删除 `_current_facts` 与三条错误文字 | 观察时已核对 |
| 05 | `MemoryUpdateEvidence` 的位置 | `src/worldmock/recording_engine/events/memory_evidence.py`，与 `MEMORY_UPDATE_KEYS` 同处（第 10 章跨章裁定确认） | 形状与核对在一处，人物不导入引擎 |
| 05 | 已存在工作区的错误 | 每个人物目录 `FileExistsError(f"Agent workspace already exists: {path}")` | 措辞不变 |
| 05 | 旧变异 `step_end_copies_every_memory_again` | 删除，不改指 | 它守的复制读法不存在了 |
| 05 | （问题）条目里的 `agent_id` | 保留 | 提案只列了五个删除的字段 |
| 05 | （问题）带 `AGENT.json` 的旧快照 | 由成员核对拒绝，不迁移 | 不做向后兼容 |
| 05 | （问题）谁删 `creation_inputs` 的 `config` | 05 章 | `config` 只是 `config.json` 的内容 |
| 05 | （问题）改不改名 `WorldMockMemoryStore` | 不改 | 同一概念保持同一名字 |
| 05 | `save_step` 先渲染后写 | 先算视图，再写条目，最后写视图 | 预算失败时不写任何文件 |
| 05 | 读回怎样切行 | 按 `"\n"` 切，不用 `splitlines()` | 关键词里的 U+2028 等字符不能拆行 |
| 05 | `MEMORY_UPDATE_KEYS` 的单一归宿 | 登记在 `memory_evidence.py` 名下 | 引擎与质量报告共用一个键集合 |
| 06 | 判断轮次是否开着 | 读 `engine.decisions.round` | 轮次状态只归引擎 |
| 06 | 中止原因的形式 | 宿主传 `repr(error)`，引擎的签名与模板不变 | 只改原因文字 |
| 06 | （问题）`run_failed.data.error_type` | 仍是 `RuntimeError` | 提案没有列它；`repr` 里已有类名 |
| 06 | 开轮调用在打开本轮之前失败 | 同提交关闭本轮：不中止，运行级登记，原样传播 | 同一判断 |
| 06 | 运行级登记的文字 | 一律 `repr(error)`，包括关闭引擎失败 | 一条规则一处 |
| 06 | （问题）关闭登记再失败的异常组文字 | `Run and failure recording both failed`；`LifeEnv close and failure recording both failed` 删除 | LifeEnv 已删 |
| 06 | 双重故障的日志与组文字 | 原样搬进 `RunHost`，logger `worldmock.runtime.lifecycle.run_host` | 两项都保留 |
| 06 | 人物故障的宿主日志 | 不写；`LifeAgent decision failed for agent %s` 已记一次 | 不重复记录 |
| 06 | `failure_message` 的空白合并 | 保留 | 设计要求一行可读的原因 |
| 06 | 回合用完的文字与日志 | 见 03 章同名一行 | 06 章是文字的所属章 |
| 06 | `test_error_pickling.py` 里的日志用例 | 搬进新文件 `tests/unit/runtime/test_error_classes.py` | 构造时记日志仍是规则 |
| 06 | `__reduce__` 删除的守卫 | 结构守卫：`src/worldmock` 里没有类定义四种打包钩子 | 没有进程边界，没有行为可测 |
| 06 | `test_branch_lineage.py:166` | 不改 | 手写的样例取值 |
| 06 | 端到端故障证据 | 复用 `test_recorded_response_run.py` 来源夹具的第一段 | 已经经真实命令行走到 400 拒绝 |
| 06 | （问题）运行比对是否为故障原因加规则 | 不加 | 失败段的尾部不在比较材料里 |
| 06 | 三条变异 | 只在轮次开着时中止；中止原因是原异常的 `repr`；`status.json` 不解 `cause` | 裁定 8 的两半与故障文本第三点 |
| 06 | 构造人物失败 | 在决策轮的 `try` 里，中止本轮 | 今天工作进程的同类失败也走中止 |
| 06 | 记忆写入故障用例的 `empty` 参数 | 删除 | 05 章删除了那条核对与文字 |
| 06 | 禁用的包装文字 | `Agent decision failed: `（带冒号） | 保留的日志里含不带冒号的短语 |
| 07 | 项目根的来源 | `Path(worldmock.__file__).resolve().parents[2]` | 提案：由已导入的包推出 |
| 07 | 代码身份的签名 | `runtime_code_sha256()`，没有参数 | 参数只有一个合法值 |
| 07 | （问题）删除进程配置后的凭据核对 | `_prepare_resume` 在原位置调 `credential_value(inputs.config.model)` | 缺凭据的恢复不建分支目录 |
| 07 | `write_receipt` 与收据 | 删去 `sources` 参数；`environment-1`，十个键 | 唯一的额外来源是 AgentSociety2 摘要 |
| 07 | （问题）`requirements/build-lock.txt` | 保留，只含 `setuptools` | 提案写了两份 requirements 锁 |
| 07 | 锁的生成命令 | `uv lock`（不加 `--upgrade`）与两条 `uv export --frozen …` | 在基线上实测能逐字节复现今天的两份锁 |
| 07 | （问题）`uv lock` 用不用网络 | 允许读包索引元数据，不加 `--upgrade`，由锁差异核对把关 | 离线锁定在缓存里缺 litellm |
| 07 | （问题）工作树引导用不用网络 | 先 `UV_OFFLINE=1`，缓存缺包才联网 | 哈希锁保证内容相同 |
| 07 | 锁差异的判据 | 只有删除行与 `tomlkit` 两行；保留包相同；删掉的恰为 76 个名字；计数 59、58、1 | 把提案的两句话变成可判定的核对 |
| 07 | `python-dotenv` | 从直接依赖删去，经 litellm 间接保留 | WorldMock 不再导入它 |
| 07 | （问题）删一项门禁后行为工具标识 | `behavior-metrics-4` 不变 | 提案没有列；生产代码不落这份报告 |
| 07 | （问题）`MEMORY_IDENTITY_MARKERS` | 保留，注释改写不点名框架 | 回顾报告仍用它 |
| 07 | 来源句 | `来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。`，模块文档串最后一行 | 一句写全来源、版本、提交与许可文件，可逐字测试 |
| 07 | 来源核对的算法 | 四个相邻有效行（去空白后不少于 20 字符、不是注释）比对未打补丁的上游归档，命中再人工复核 | 能判定、能复现，补丁里 WorldMock 自己写的代码不算衍生 |
| 07 | 许可字节的保护 | `.gitattributes` 加 `LICENSES/** -text` | Windows 运行器缺省转换换行 |
| 07 | `.gitignore` 的框架条目 | 删 `.ray/` 与 `vendor/agentsociety2/` | 没有东西再生成它们 |
| 07 | `setup.py` 导入 LifeAgent 常量 | 仍在函数内，改指 `worldmock.agents.life_agent`，登记三条延迟导入 | 身份计算不加载人物栈 |
| 07 | 导入图的门面例外 | 只有 `run_host` 可以导入引擎 | 宿主直接持有引擎 |
| 07 | 门禁测试的合成路径 | 改到 `src/worldmock/` 或 `tools/analysis/` 下 | 只需要任一扫描根 |
| 07 | （问题）不用的 pytest 标记 | 保留，只从说明里删 `LifeEnv` | 删标记不在提案内 |
| 07 | `LITELLM_LOCAL_MODEL_COST_MAP` | 在 `fresh_check`、变异执行器与 `tests/conftest.py` 里保留 | 测试会先于模型客户端导入 litellm |
| 07 | 变异 `process_configured_after_the_model_identity` | 删除，不另补 | 守的步骤不存在了 |
| 07 | 工作流步骤名 | `Prepare Linux Node native library` | 步骤只剩安装 Node 原生库 |
| 07 | 命令行与项目描述的英文 | 命令行 `Auditable life simulation`；项目 `Auditable life simulation and digital footprint production` | 裁定 12：不再以 AgentSociety2 自称 |
| 07 | `runner.py:32-37` 的孤立注释 | 删除 | 后面没有对应声明 |
| 07 | `tools/analysis/memory_pilot.py` 文档串里的 `custom` | 删去「与 custom」 | `custom/` 不再存在；09 章【T45】同时从它的用例里删去这个前缀 |
| 07 | `_git_state` 的参数名 | `root` | 不遮住新的 `project_root()` |
| 07 | 不落下的守卫 | 新文件 `tests/unit/engineering/test_framework_removal.py` 十条守卫，模式由片段拼出 | 负责人要求用测试保证 |
| 07 | 工作树的虚拟环境 | 依赖改完、锁核对通过之后、任何测试之前换成自己的 | 免得仍能导入 AgentSociety2、掩盖遗漏 |
| 07 | 锁核对用哪个解释器 | uv 管理的 CPython 3.12.13，只用标准库 | 这时工作树还没有自己的环境 |
| 07 | 一次性脚本的位置 | 基础检出的 `.work/agentsociety2-removal/` | 执行回执归 `.work`，工作树拆掉后仍在 |
| 07 | 测试文字与 09 章【T27】 | 被禁的串由片段拼出；11 个变量名只写在 `REMOVED_PROCESS_VARIABLES` | 两章的守卫同时成立 |
| 07 | 导入守卫的分工 | 07 章扫 `src/`；`tests/`、`scripts/`、`tools/` 的守卫由第 10 章定名在 09 章的守卫文件里 | 同一规则不扫两遍 |
| 07 | 来源句的归属与模块集合 | 07 章那一句是全规格唯一写法；已知集合是 03、04、05 章点名的五个模块，来源核对可以再加 | 可逐字测试，框架名守卫才能成立 |
| 07 | `test_mutation_runner.py` 的样例 | `:86` 改为 `tests.x::a..b`；`custom.` 前缀被拒；`:105` 改为 `tools.analysis` 的样例 | 三个生产根各有一个样例 |
| 08 | `10-runtime.md` 的节名 | 「实际框架边界」改名「运行装配」，新加「决策循环」；「运行与恢复」「原生模型与工具」「安装与工程放行」保留原名 | 01 章的模块文档串引用「决策循环」；代码里引用后两个旧节名，保留就不改代码 |
| 08 | 决策循环规则的家 | 只在 `10-runtime.md`「决策循环」；04、05、06 章文档讲触发点与每步检查点的句子用 01 章的说法并指向这一节 | 每条规则只维护一处 |
| 08 | `10-runtime.md:30` 的新建顺序 | 只写按「决策循环」的新建顺序启动，不重述五个步骤 | 重述就是第二处 |
| 08 | 回合协议的家 | `06-agent.md`「宿主模型协议」末尾的三级标题「回合协议」，显式标识 `agent-回合协议` | 03 章要求接在步序之后；10-runtime 要链接它 |
| 08 | 各身份格式的家 | `round_id`、`decision_id` 在 10-runtime「身份」；`call_id` 在「回合协议」；`action_id` 仍只在 `06-agent.md:87` | 身份写在生成它的一方 |
| 08 | 模型客户端一节的形式 | `08-configuration.md`「模型配置与凭据」节末的三级标题，显式标识 `configuration-模型客户端`；寿命只写在 10-runtime | 06-agent 要链到这一段；寿命规则只在 01 章 |
| 08 | 回合用完的文字 | 文档写 `RuntimeError("ReAct decision budget exhausted without a world request")` | 06 章【F4】与 03 章常量同一串（第 10 章跨章裁定） |
| 08 | 人物回合请求字典的键序 | 文档写三条路径都按 `REQUEST_KEYS` 的次序 | 03、04 章都按它构造；键序不进任何字节 |
| 08 | `06-agent.md:748` 的写法 | 「与运行开始时交给 LifeAgent 的卡片文本逐字相同」 | 卡片文本带首尾空白时，「与 `MEMORY.md` 身份段相同」不成立 |
| 08 | （问题）`09-io-formats.md:93` 空的「可查」 | 写「还没有可查的日期时是空对象」，Schema 不改 | 与 03 章改写后的代码注释一致 |
| 08 | （问题）「重建验收」「重建与运行能力」 | `00-overview.md:187` 写「环境重建验收」，`README.md:67` 写「按哈希锁重建环境与运行的能力」 | 引导不再重建框架，按哈希锁重建虚拟环境仍要在各平台证明 |
| 08 | `00-overview.md:148` 背书表 | 改成「决策循环」一行，背书是 LangGraph 超步与 Concordia 主持人结算同一种结构 | 提案第五章的原话 |
| 08 | `00-overview.md:194` 的新假设 | 单进程、单事件循环的顺序决策足以承载运行；不成立时先设计按决策缓冲证据、提交时按序落库 | 提案第六章「一个进程，一个事件循环」 |
| 08 | `00-overview.md:107`、`:204` | 删除缩写行与参考文献行 | 现行文档不再用缩写 AS2，也不再以它为参考 |
| 08 | `00-overview.md:186` 依赖一栏 | 只删去 AgentSociety 2，不另列 litellm | 07 章只要求删去 |
| 08 | 现行文档点名 AgentSociety2 的地方 | 只有 `README.md` 的 `LICENSES/` 一行与 `05-recording-engine.md`「运行比对」的例外句 | 裁定 12；许可来源是现行事实 |
| 08 | 运行比对例外句的写法 | 不点名基一侧多出的文件名，写「新格式没有的成员」 | 清单在 09 章的比较工具里，文档不写第二份 |
| 08 | 「驱动进程」 | `10-runtime.md:47-61` 与 `docs/manual/runtime.md:21` 不改 | 改名会让同一概念有两个名字 |
| 08 | 调用类 `consolidation`、`reflection` | `05-recording-engine.md:671-672` 与 `07-evaluation.md:329` 不改 | 它们按请求形状识别历史调用，与 `memory_updated` 的键无关 |
| 08 | `01-architecture.md:172` 的记忆模块链接 | 换成 `src/worldmock/agents/memory` 的链接 | 同一职责的现行代码位置 |
| 08 | `01-architecture.md:175` 的链接 | `LifeEngine` 链到 `engine.py`；运行装配链到 `src/worldmock/runtime`，括注链到 `run_host.py` 与 `midnight.py` | 链接目标合并时都存在，由文档门禁核对 |
| 08 | `05-recording-engine.md:159` 的括注 | 删去「跨进程传送后」，留「键的插入次序不同」 | 同一进程不再有跨进程传送 |
| 08 | `08-configuration.md:166-167` | 写「运行进程」 | 新运行只在准入时读一次元数据，没有环境 actor |
| 08 | 回合协议里与 `06-agent.md:77-89` 重复的内容 | 不重述批次形状与最后回合的规则，只写剩余回合数、截断重试与八个工具的次序 | 每条规则只维护一处 |
| 08 | 改后文字的断行 | 不规定 | 断行不进入任何检查 |
| 08 | 文档的测试 | 只用文档门禁、设计汇编与 02 章的名字守卫，不新增测试 | `AGENTS.md`：只检查链接与设计构建 |
| 08 | 设计汇编的核对方式 | 执行 `python scripts/build_design_doc.py`，退出码 0 | 脚本没有 `--check` |
| 08 | INDEX 登记 | 本规格不登记进 `docs/INDEX.md` | INDEX 不逐个列出 `dev/` 下的主题 |
| 09 | （问题）录音重跑工具还剥不剥 `RAY_ADDRESS` | 不剥，`_routing_name` 只剩三个代理变量 | 新分支没有 Ray；基线重放在不设它的环境里进行 |
| 09 | 录音重跑子进程的解释器 | 仍是运行工具的解释器；基线重放用基线工作树的解释器运行新工具 | 只去掉源码检查与 `--project-root` |
| 09 | 掩码完整日比较每侧的环境 | `environment_python(<根>/.venv)`，缺失以退出码 2 拒绝 | 引导只在这个位置建环境，复用已有函数 |
| 09 | 掩码完整日比较的 `PYTHONPATH` | 只从录音重跑工具导入 `project_pythonpath` | 一条规则一处 |
| 09 | 四条规则的名字、种类与目标 | `workspace-members`、`memory-updated-form`、`model-request-parameters`、`prompt-fingerprint`，见【T12】 | 每类改写的记录一条规则 |
| 09 | 规则的章节字段 | `05`、`05`、`04`、`03` | 与现有规则只写章号一致；脚本里不许出现框架包名 |
| 09 | 英文模板的识别 | LF 与 CRLF 两个摘要常量 `BASE_MEMORY_TEMPLATE_SHA256` | 比较工具只用标准库 |
| 09 | 空条目文件与模板 `MEMORY.md` | 两侧都不列空条目文件；基一侧的模板 `MEMORY.md` 不列 | 都不携带事实 |
| 09 | 条目文件的比较 | 每行规范 JSON；头一侧带 `\r` 或没有末尾换行时按原字节比 | 值比较；LF 在这里守住 |
| 09 | `episodes_sha256` | 按首次出现的双射比较，新种类 `episodes-digest` | 05 章总写出条目文件，两侧都是真实文件的摘要 |
| 09 | 代码身份规则 | 不加新规则 | 现有屏蔽与清单摘要登记已覆盖 |
| 09 | 查看器的「已巩固」 | 不再读 `consolidated` | 键已删除，恒为假 |
| 09 | 查看器上下文说明 | 按【T24】改三项来源与记忆视图的填法、两步 | 旧说明指向已删的文件、函数与主观归纳 |
| 09 | 移植用例的位置与名字 | 各章给自己规则的测试定名；本章只管调度器、回合校验、生活钩子 `:151-326` 与扫描注册；同名移植，只在点名已删机制时改名 | 规则与测试同章，断言主体不改 |
| 09 | `decide()` 跑多大范围 | 03 章的 `run_turns`，只跑回合循环，记忆存储传 `None` | 与今天的范围相同，31 个下游不改断言主体 |
| 09 | `decide()` 的第三项 | 登记的工具名列表，三处下游断言改 | 旧字符串由已删代码产生 |
| 09 | `life_host_fixtures.py` 的死助手 | 只留 `TruncatedResponse` | 其余导入已删模块且没有调用者 |
| 09 | 新运行的测试写法 | `runtime_fixtures.new_run(prepared, client)` 一处；子进程替身直接写同样三步 | 新建顺序归宿主，测试只写一次 |
| 09 | 新建运行时传什么客户端 | `OfflineClient`：收到请求就失败 | 这些地方都不发模型请求 |
| 09 | `native_runtime` 的接缝 | 替换 `runner._model_client` 与 `run_host.LifeEngine`；`MODEL_FACTORY(prepared)` | 01 章定的唯一构造点 |
| 09 | 断网重发用例的客户端 | `failing_client`：真实 `ModelClient`，替换 `litellm.acompletion`，线路请求记在 `wire_requests` | 停放与计数必须走真实客户端 |
| 09 | 协议与续接测试里的人物 | 在 `person_inputs` 的结果上用 `dataclasses.replace` 换名字、编号与回合上限 | 冻结输入没有第二种入口 |
| 09 | `Society` 驱动的协议测试 | `_step_host` 把记录型假环境挂成 `RunHost.engine` 再 `host.run()` | 断言主体留在同一假环境上 |
| 09 | 测试读回合历史 | 测试助手 `turns_with_history` 显式建 `ReactLoop` | 生产代码不加测试专用访问器 |
| 09 | `tests/conftest.py` 的价目表变量 | 保留 | 测试先于模型客户端导入 litellm |
| 09 | 纯加载的禁止模块集合 | 去掉两个已删模块，加 `run_host` 与 `model_client`；去掉 `agentsociety2` 前缀断言 | 已删模块的后继 |
| 09 | （问题）测试、脚本、工具里描述旧机制的文字 | 改写，由【T27】守住：`tests/`、`scripts/`、`tools/`（不含 `tools/analysis`），零宽断言的正则 | 旧名字指向不存在的对象；`\b` 会漏掉中文旁的词 |
| 09 | `test_cli.py` 的用例名 | `or_as2` 改为 `or_model_client` | 断言内容变了 |
| 09 | 生产符号已删的变异 | 删除，不补替身 | 被变异的分支不存在了 |
| 09 | `env_close_leaves_cached_clients` | 改名 `model_client_close_leaves_the_network_client_open`，改指 `ModelClient.close`（第 10 章跨章裁定确认） | 关闭网络客户端仍是行为 |
| 09 | （问题）现有的 `workspace-gzip` 规则 | 不动 | 对齐的是别的旧变化，提案没有列它 |
| 09 | 重冻与重录的时机 | 全部生产改动之后、在最终树上各做一次 | 基线绑定代码身份 |
| 09 | 重冻前去掉哪些用例 | 只有两个与样例绑定的用例 | 查看器 Node 用例只把样例当数据 |
| 09 | 工作区夹具的内容 | 只建空目录 | 等于新运行首次决策前的快照 |
| 09 | 变异改指 | 跟随写那段代码的章 | 符号与杀手都在那一章 |
| 09 | 查看器样例的键序断言 | 等于 `list(REQUEST_KEYS)` | 不在测试里写死次序 |
| 09 | `tests/`、`scripts/`、`tools/` 的导入守卫 | 本章 `test_framework_removed_from_tests.py` 的 `test_no_test_script_or_tool_module_imports_the_removed_framework`（第 10 章跨章裁定定名） | 07 章与本章原先互相指向 |
| 09 | `_step` 的返回值 | 登记的工具名列表；步骤是 `decide` 的观察、`run_turns`、`_record_memory` | `decide` 不返回工具名；与 `decide()` 第三项同一改法 |
| 09 | 经工作区建人物的辅助 | 每次用 `read_episodes` 从工作区读回已有条目，回合上限 8 | 与今天「每次决策从工作区重建」同一语义 |
| 09 | `test_life_channel_offers_only_read_query` | 只核对 `LifeEngine` 没有 `due_reflection`、有 `read_query` | 转发类与 `LifeEnv` 已删除 |
| 09 | `run-viewer-context.js:94` 的 `life_prompt.py` 路径 | 改成 `src/worldmock/agents/prompt/life_prompt.py` | 同一行本来就要改；旧路径指向不存在的文件 |
| 10 | 提交次序 | 负责人的指示取代提案第九章「按可运行的提交排列」：一次改完、一次全量测试、一轮评审与修正 | 裁定记录「读法」第三条 |
| 10 | 轨道数与划分 | 十一条第一阶段轨道加一条第二阶段登记轨道，按模块边界划分，一文件一轨道 | 一次改完、冲突只剩共改文件 |
| 10 | 台账、延迟导入登记与单一归宿 | 第二阶段由登记轨道一次写好 | 要对照实现后的源码 |
| 10 | 轨道改文件时的检查 | 只有 `py_compile`、`node --check`、`git diff --check` | 负责人不许零散跑测试 |
| 10 | 基线工作树的环境 | 解开连接点、在 `74f1b949` 上引导自己的 `.venv`、核对实际导入的包摘要 | 实测今天导入的是摘要 `bf716c7b…` 的旧源码 |
| 10 | 录音用的环境 | WorldMock-Dataset `330b6065` 的白领包，只把窗口改成 2026-10-08 至 10-10 | 与 10-31 试点同一个包、同一窗口 |
| 10 | 录音用哪个入口 | WorldMock 命令行 `run` 与 `resume`，基线解释器 | 录音重跑工具读 WorldMock 运行目录链 |
| 10 | （问题）计划内重启怎样停下 | 模拟时刻过了 `2026-10-09T12:00:00+08:00` 就结束驱动进程，从最新的共同检查点续跑 | 手册写明的恢复路径；不多花一天；探针要的零点请求都在第一段 |
| 10 | （问题）录制失败时 | 作废重录，最多两次，费用都进验证记录 | 没有录音不能合并；控制费用 |
| 10 | 基线重跑用哪份工具 | 录完先用基线自己的工具重跑；正式比较两侧都用新工具 | 先证明录音可用，再让 `raw-order` 比较同一工具的输出 |
| 10 | 全量测试时与样例绑定的用例 | `PYTEST_ADDOPTS` 传两个 `--deselect`，`gate.py` 带 `--final` | 09 章【T52】 |
| 10 | 分片工具的解释器 | `gate.py` 副本只改 `PYTHON` 常量 | 07 章【I28】 |
| 10 | 跑几次全量 | 全量测试与终门两次 | 覆盖率门要求最终提交上的整套运行 |
| 10 | 证据的时机 | 全量测试之后、评审之前跑首轮；代码身份或比较工具变了才复跑 | 唯一一轮评审要看到偏差 |
| 10 | 独立变异评审 | 评审视角之一，隔离工作树的实施提交，每个改了生产源码或脚本的章至少三条探针 | 提案验收证据表 |
| 10 | 修正之后又有失败 | 按所属轨道修，从重冻起重做，写进验证记录 | 不落下任何事项 |
| 10 | 回执目录 | `$R` = 基础检出的 `.work/agentsociety2-removal-impl/`，`$ONE` = `.work/agentsociety2-removal/` | 与 07 章一致，工作树拆掉后仍在 |
| 10 | 验证记录 | `VERIFICATION-agentsociety2-removal.md`，终门后单独提交 | 提案要求；机器报告不入库 |
| 10 | （问题）「附在 PR 里」的完整日报告 | 结论与 SHA-256 写进合并提交正文，报告留在 `$R` | 合并不经 PR |
| 10 | 合并的做法 | 在分离的 `origin/main` 上 `--no-ff` 合并，树等于分支头，推送 `HEAD:main`，有界重试 | 与上次并入主干相同 |
| 10 | 推送被拒 | 不换办法，命令交给负责人 | 不绕过权限判定 |
| 10 | 本地 `main` | 只在没有已跟踪文件的改动时快进 | 未提交的东西归负责人 |
| 10 | （问题）基础检出的环境重建 | 本任务不做，命令写进最终报告 | 2026-10-15 裁定要求合并之后、不从工作树做；基础检出的 `.venv` 被别的工作树共用 |
| 10 | （问题）裁定 11 的试跑窗口 | `2026-10-08T18:00` 到 `2026-10-09T06:00`（+08:00），合并之后在工作树里跑 | 跨一个零点，三种请求都发生，费用低 |
| 10 | 交接说明写在哪 | 全文写在第 10 章，实施时复制到 `$R/handoff/` | 说明随规格入库；不改别的仓库 |
| 10 | `CLAUDE.md` 的改写 | 写成工作树里的文件并给出 `cp` 命令，基础检出的那份不动 | 它被 git 忽略，工作树会话不直接改基础检出 |
| 10 | 提案「回复」一栏 | 只填这一栏：裁定的「决定」原文加指向裁定记录的链接 | 冻结的提案只补回复 |
| 10 | 工作树的去留 | 两个工作树保留，隔离评审工作树用完即拆 | 负责人没有要求清理 |
| 10 | 跨章不一致 | 按第 10 章「跨章裁定」一表（`MemoryUpdateEvidence` 的模块、人物请求键序、回合用完的文字、`env_close_leaves_cached_clients`、`persona_habit_responses`、`self.engine`、导入守卫、`06-agent.md:748`、一次性脚本的位置、`persona_layer` 的测试调用者、零点任务收到的业务桩） | 实施只能有一种写法；每行取所属章的说法 |
| 10 | 去除完整的测试 | 不另写第三份守卫：02、07、09 章的七个用例守住目录、导入、依赖锁与可导入性，另有六个名字与结构守卫，十三个一起列进【V30】与完成判据 | 一条规则一处；各守一类遗留 |
| 10 | 台账条数 | 298 − 9 + 24 = 313 条 | 各章变异表与 09 章【T47】的删除相加 |
| 10 | （问题）「记忆已更新」与工作区的变化要不要升版本号 | 都不升：运行清单 7、导出 8、世界 15、检查点 1、质量报告 10 不变 | 旧运行已因代码身份与提示指纹被拒；质量报告按键集合明确报出旧格式；提案第七章没有列版本变化 |
| 10 | 轨道怎样知道自己名下文件的全部改动 | 落下各章点名这个文件的全部条款与测试行，加上通用迁移表与名字守卫；「几章共改的文件」只列最容易漏的 | 共改文件多到无法逐个列全；归属仍一文件一轨道 |
| 10 | 既有变异的锚点与杀手 | 登记轨道逐条对照实现后的源码复核，改写过的锚点按新源码重写，改名或删除的杀手换成后继，不增删条目 | 93 条既有变异落在这次有改动的 23 个模块里 |
| 10 | 阶段一结束的残留检索 | 不检索 `tests/fixtures` | 那里的 `as2/agents/…` 是历史数据的来源记录，不改 |
| 10 | 基线工作树与新分支的虚拟环境怎样得到 | 复制基础检出的 `.venv`，再用 `uv pip` 离线改指可编辑安装；新分支另卸掉新锁没有的包 | 离线引导因 uv 缓存缺包不可行；复制加离线改指不下载任何包，导入的仍是各自检出的源码与锁定的包，【V2】的摘要核对与【V10】的包集合核对照样成立 |
| 10 | 录音的停点 | 停在 2026-10-08 22:30（第一个零点之前），探针读续跑段 `$SEG2` | 录制已经做完；续跑段的库连同续接前的事件保存了三种请求，探针要的语料都在里面，不必重录 |
| 10 | （问题）录制在基线环境建好之前用哪个解释器 | 基础检出 `.venv` 的解释器加 `PYTHONPATH` 指向基线工作树的 `src`、根目录与锁定源码；`run`、`resume` 自己核对摘要 `0a7b9b06…` | 导入的是基线工作树的代码与锁定源码，运行清单的 `code_sha256` 与基线重跑一致，录音可用 |
| 10 | 提案的状态行 | 改为「已裁定」并链到裁定记录与开发规格 | 仓内已裁定提案（`free-time-dead-ends`）的写法；待裁定的字样会误导读者 |
