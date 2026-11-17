# 比较工具、查看器与测试迁移

> 本章属于[去掉 AgentSociety2：开发规格](../SPEC-agentsociety2-removal.md)，读法和叫法见那一页。

## 目标与依据

本章让三件比较工具能拿基线（`74f1b949`，带 AgentSociety2）和新分支（不带）做验收比较，让查看器读新的「记忆已更新」，并把整棵测试树迁到没有 AgentSociety2、Ray 和 `custom/` 的代码上：AgentSociety2 专属测试按各章的移植表移植后删除，导入、测试枢纽、假凭据、工作区夹具、变异台账、两份完整日基线和查看器样例各改到位。

| 提案位置 | 本章落实的内容 |
| --- | --- |
| 第八章「改动面」的「测试」行 | 全部测试文件清单：`custom.*` 导入、决策轮类型导入、`person()`/`decide()` 夹具驱动的文件与枢纽改写、假凭据、十四个 AgentSociety2 专属文件的移植与删除、四个调度器相关文件改测新客户端、工作区夹具、变异台账、两份完整日基线、`viewer-context.json` |
| 第八章「改动面」的「工具与脚本」行 | `scripts/recorded_response_run.py`、`scripts/compare_complete_days.py`、`scripts/compare_runs.py` 与 `tools/run-viewer.html` 的改动 |
| 第九章「实施顺序与验收」的「新写自有模块」「删除」「收尾」三项 | 只在 AgentSociety2 目录里有测试的行为移植成针对新模块的测试；删除已移植的专属测试；重冻、重录、变异台账改指 |
| 第九章「验收证据」表的前四行 | 工具必须能产出这四项证据：两次录音重跑、`raw-order`、运行比对的按路径声明规则、掩码完整日比较 |
| 第四章表「23 个测试文件为了能导入 AgentSociety2 而预设假凭据」（`tests/conftest.py:22-25`） | 假凭据全部删除 |
| 第四章表「另写了一组在进程内直接驱动 LifeEnv 的测试」（`tests/integration/runtime/test_env_lifecycle_coverage.py:1-8`） | 这组测试按[「运行宿主与决策轮」](./01-run-host.md)与[「去掉 LifeEnv」](./02-engine-facade.md)的后继表移植后删除 |

本章落实的裁定（[裁定记录](../RULING-2026-11-17-owner.md)）：

| 裁定 | 落实 |
| --- | --- |
| 3 | 运行比对的 `workspace-members` 规则：基一侧工作区按两个保留文件对齐；工作区夹具只建空目录 |
| 4 | `memory-updated-form` 规则对齐三个删除的键；查看器不再读 `consolidated` |
| 5 | 测试里的 `as2/agents/agent_NNNN` 路径改成 `agents/agent_NNNN` |
| 6 | `model-request-parameters` 规则；`viewer-context.json` 重录成六个键 |
| 7 | `prompt-fingerprint` 规则；只读问答相关测试的删除由[「人物、回合循环与只读问答的删除」](./03-agent-loop.md)规定 |
| 10 | 录音重跑工具同一份代码重放基线与新分支；掩码完整日比较两侧各用自己的虚拟环境 |

依据的事实（均在基线 `74f1b949` 上核对）：

| 事实 | 位置 |
| --- | --- |
| 录音重跑工具检查 `vendor/agentsociety2` 源码、在子进程里定位 `agentsociety2`，子进程 `PYTHONPATH` 带 AgentSociety2 包目录 | `scripts/recorded_response_run.py:61-67`、`:567-574`、`:588-590`、`:904-910` |
| 录音重跑工具给命令行传 `--project-root` | `scripts/recorded_response_run.py:668`、`:672`、`:799`、`:1010` |
| 基线命令行的 `--project-root` 缺省是 `cli.py` 往上两级 | `src/worldmock/cli.py:23` |
| 子进程用运行工具的解释器（`sys.executable`） | `scripts/recorded_response_run.py:732`、`:799`、`:1010` |
| 基线包 `agentsociety2` 以可编辑方式装进基线工作树自己的 `.venv`，`find_spec` 不靠 `PYTHONPATH` 也能解析到它 | `pyproject.toml:45`；`scripts/bootstrap.py:24-26`；`src/worldmock/runtime/framework/setup.py:136-155` |
| 掩码完整日比较要求两侧依赖锁逐字节相同、要求 AgentSociety2 源码、从录音重跑工具导入定位函数、子进程用 `sys.executable` | `scripts/compare_complete_days.py:30`、`:37`、`:52-71`、`:78` |
| 引导出的解释器位置函数 `environment_python(directory)`；引导只在 `<根>/.venv` 建环境 | `scripts/bootstrap_environment.py:33-35`；`scripts/bootstrap.py:24` |
| 运行比对屏蔽 `AGENT.json` 的五条路径；工作区快照按成员列出摘要 | `scripts/compare_runs.py:56-58`、`:661-680` |
| 规则只能落在 `ALLOWED_RULE_TARGETS` 上，规则按名启用；运行清单先对齐再规范化，规范化后的摘要登记为检查点 `manifest_sha256` 的目标 | `scripts/compare_runs.py:142-149`、`:923-956`、`:1130-1144` |
| 工作区快照是成员按路径排序、`mode` 0o600、`mtime` 0 的 tar，再 gzip | `src/worldmock/recording_engine/storage/checkpoint.py:33-53` |
| 基线条目编号是 `m_{tick:08d}_{counter:06d}`，`tick` 与 `tick_duration_sec` 都等于传入的 `tick`（恒为 0）；计数只在新建时加一；条目字段集是十五个 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/memory.py:46-50`、`:526-540`；`src/worldmock/agents/memory/life_memory_store.py:62-87` |
| 基线首次以文本方式写英文模板 `MEMORY.md`（Windows 上是 CRLF）；视图与条目文件也以文本方式写 | 同一 `memory.py` 的 `:52-71`、`:281-283`、`:421`、`:494-496`；`life_memory_store.py:147` |
| 英文模板按 LF 写出的摘要是 `fed81886cb8287697bcc29e8ee01c15a995bae6b891c74672799c143293b9367`，按 CRLF 写出的摘要是 `b0c0cc2fbeb9017c6a1b94217be4ad04fbcb033231a7ddc602feb4a001d3cd6b` | 用锁定解释器对 `DEFAULT_MEMORY_MD` 原文求得 |
| 人物第一条 `memory_updated` 的 `memory_before_sha256` 取自写入前 `MEMORY.md` 的字节 | `src/worldmock/agents/memory/life_memory.py:95` |
| `memory_updated` 数据的键与次序；`memory_sha256`、`memory_before_sha256` 已按首次出现成双射比较；`episodes_sha256` 在基线上恒为摘要 | `src/worldmock/recording_engine/events/memory_evidence.py:56-79`；`scripts/compare_runs.py:89-93` |
| 人物回合的底稿带 `stream`、`max_retries`、`transport_max_retries`；零点计划与回顾带 `stream`、`max_retries`；没有读者从底稿里读这三个键 | `custom/agents/life_agent.py:272-279`；`src/worldmock/simulation_engine/scheduling/plans/day_planning.py:88-90`；`src/worldmock/simulation_engine/evidence/memory_digest.py:353-355`；读者 `src/worldmock/simulation_engine/decisions/continuation.py:129`、`src/worldmock/recording_engine/reports/quality.py:819-828` |
| 提示指纹只在运行清单 `model_identity.prompt_sha256` | `src/worldmock/state/identity.py:16-27`；`src/worldmock/runtime/framework/setup.py:80-89` |
| `memory_updated` 的读者是质量报告、`scripts/inspect_run.py`、行为分析与查看器，没有提示层读它 | `grep memory_updated src scripts tools` |
| 查看器的记忆概要读 `consolidated` 并显示「已巩固」；上下文说明里的记忆视图写「主观归纳」 | `tools/run-viewer.html:553-557`、`:708-711`；`tools/run-viewer-context.js:143-151` |
| 测试树：60 个文件 119 条 `agentsociety2` 导入；33 个文件 47 条 `custom.*` 导入语句，外加 `tests/system/cli/test_cli_clean_process.py:41` 替身源码字符串里的一条；23 个文件预设假凭据；14 个 AgentSociety2 专属文件共 3,565 行 | 本章「接口与文件」各表由 grep 与 AST 扫描得出 |
| 完整日录制的人物快照来自测试夹具 `write_agent_workspace`，不经 LifeAgent | `tests/life/day_baseline_runtime.py:117-136` |
| 完整日基线只打桩 `agentsociety2.agent.person.datetime` 这一处框架对象 | `tests/dayrun_baseline.py:30`、`:41` |
| 双候选规程 | `docs/design/01-architecture.md:262-266`；`scripts/freeze_rrs_baseline.py:79-87` |

## 行为

### 一　录音应答运行

【T1】`scripts/recorded_response_run.py` 不再检查、也不在子进程里定位 AgentSociety2：常量 `AS2_PACKAGE`、`AS2_SOURCE`、`FIND_AS2`（连同三行注释）与函数 `as2_location` 删除，`_run` 不再拒绝没有 `vendor/agentsociety2` 的检出。错误文本 `Project root has no restored AS2 source: …`、`Project AS2 source resolves outside the project: […]` 与日志 `No agentsociety2 package resolves in %s` 从代码里消失。

【T2】子进程的 `PYTHONPATH` 恰为被测检出的 `src` 与根目录两项，按这个次序（`project_pythonpath`）。

【T3】工具起的四条 worldmock 命令都不带 `--project-root`，其余参数与次序同今天：第一段 `["run", "--environment", <work>/input, "--output", <段目录>, "--same-second-continuation", "on"|"off"]`；续跑段 `["resume", "--environment", <work>/input, "--parent", <上一段目录>, "--checkpoint", <检查点身份>, "--output", <段目录>]`（这两条经 `scripts/recorded_response_child.py` 交给 `worldmock.cli.main`）；`finish` 的导出 `[sys.executable, "-P", "-m", "worldmock.cli", "export", "--run-dir", <末段目录>, "--output", <work>/export]` 再按需追加 `["--cutoff", <截止>]`；`export_source` 的导出 `[sys.executable, "-P", "-m", "worldmock.cli", "export", "--run-dir", <副本目录>, "--cutoff", <截止>, "--output", <work>/source-export]`。

【T4】子进程环境去掉名字含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD` 的变量和 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`（不分大小写）；`RAY_ADDRESS` 不再特别处理，照常传入。

【T5】子进程的解释器仍是运行本工具的解释器。同一份工具重放基线时，用基线工作树引导出的解释器运行它：基线命令行的缺省项目根就是被测检出（`PYTHONPATH` 让 `worldmock` 从被测检出的 `src` 导入），`agentsociety2` 经基线 `.venv` 里的可编辑安装解析到基线工作树自己的 `vendor`；重放新分支时用新分支的解释器。偏差判定、计划内重启、`raw-order`、`export-source` 的判定与报告格式不变。

【T6】`scripts/recorded_response_child.py` 的行为不变：运行身份的两个抽取点仍是 `worldmock.cli`（新建）与 `worldmock.recording_engine.storage.checkpoint`（恢复）；模块文档串的 `@brief` 删去「两个模块都不导入 AS2，进程配置仍在 AS2 导入之前完成。」一句，其余字不改。

### 二　掩码完整日比较

【T7】`scripts/compare_complete_days.py` 不再要求两侧 `uv.lock` 与 `requirements/lock.txt` 逐字节相同：两侧锁不同照常录制和比较，常量 `LOCKS` 与拒绝文本 `Base and head use different dependency locks; record each side in its own environment` 删除。

【T8】每一侧用这一侧根目录下引导出来的解释器录制，路径是 `environment_python(<根>/.venv)`。`require_comparable_roots` 依次核对基一侧、头一侧，每一侧先核对解释器文件、再核对录制入口；解释器文件不存在时以退出码 2 拒绝，日志与异常文本为 `Root {root} has no bootstrapped environment at {python}; run scripts/bootstrap.py there`。录制入口缺失的拒绝 `Root {root} has no behaviour recorder (tests/life/behaviour_day.py)` 不变。

【T9】录制子进程的 `PYTHONPATH` 是 `project_pythonpath(<根>)`，即这一侧的 `src` 与根目录；模块只从 `scripts.recorded_response_run` 导入 `project_pythonpath` 这一个名字。不检查 AgentSociety2 源码和它从哪里导入，拒绝文本 `Root {root} has no restored AS2 source; run scripts/bootstrap.py there`、`Root {root} imports AS2 from {location}` 删除。

【T10】签名比较、规则下比较、退出码与报告 `complete-day-behaviour.json` 的键不变。

### 三　运行比对

【T11】`AGENT_MASKS` 删除。不启用 `workspace-members` 时，工作区快照里每个成员列成 `name`、`mode`、`mtime` 与内容摘要：`MEMORY.md` 照旧先把「来源事件」身份映射成序号再取摘要，其余成员（含 `AGENT.json`、`memory/episodes.jsonl`）取原始字节摘要。

【T12】`RULES` 加四条规则，`ALLOWED_RULE_TARGETS` 加四个目标，其余规则与目标不变：

| 规则名 | 种类 | 范围 | 路径 | 章节字段 | 读取者检索（`reader_search`，原文） |
| --- | --- | --- | --- | --- | --- |
| `workspace-members` | `rebuild` | `workspace-asset` | `members` | `05` | `Readers of workspace snapshots: restore_directory (checkpoint.py) and the export copy (export.py); no prompt reads the workspace` |
| `memory-updated-form` | `rebuild` | `event:memory_updated` | `data` | `05` | `Readers of memory_updated: quality.py, scripts/inspect_run.py, tools/analysis/behavior/metrics.py and tools/run-viewer.html; no prompt layer reads it` |
| `model-request-parameters` | `rebuild` | `event:model_request` | `data.request` | `04` | `No reader takes stream, max_retries or transport_max_retries from model_request (continuation.py reads messages, quality.py reads extra_body and messages); they were never sent` |
| `prompt-fingerprint` | `value` | `run-manifest` | `model_identity.prompt_sha256` | `03` | `Only the read-only system layer left the fingerprint; life requests are compared byte for byte by raw-order` |

【T13】`workspace-members` 启用时，基一侧工作区快照先改写成头一侧的形式再列成员：名为 `AGENT.json`、`config.json`、`life_prompt_snapshot.json`、`memory/state.json` 的成员不列；原始字节摘要属于 `BASE_MEMORY_TEMPLATE_SHA256` 的 `MEMORY.md` 不列；内容为空的 `memory/episodes.jsonl` 不列；其余 `MEMORY.md` 先把 `\r\n` 换成 `\n` 再按【T11】映射来源事件。

【T14】同一规则下，基一侧非空的 `memory/episodes.jsonl` 先把 `\r\n` 换成 `\n`，每行解析成 JSON 对象后：`tick` 等于 0 时去掉，`tick_duration_sec` 等于 0 时去掉，`refs` 等于空列表时去掉，`step_index`、`step_count` 无论取值都去掉，`id` 若完全匹配 `m_00000000_(\d{6})` 就改成 `m_` 加那六位；改写后的每行写成规范 JSON（`canonical_bytes`）加 `\n`，取这串字节的摘要。取值不是上述恒定值的 `tick`、`tick_duration_sec`、`refs` 保留，于是报成不同。

【T15】同一规则下，头一侧的 `MEMORY.md` 不换行尾，按【T11】映射；内容为空的 `memory/episodes.jsonl` 不列；非空的 `memory/episodes.jsonl` 若含 `\r` 或不以 `\n` 结尾，按原始字节取摘要（于是报成不同），否则每行解析成 JSON 对象、写成规范 JSON 加 `\n` 再取摘要。

【T16】【T14】【T15】里有一行解析出来不是 JSON 对象时以 `ComparisonError` 拒绝，文本 `Workspace episodes line {n} is not a JSON object`（`n` 从 1 起）。其他成员两侧都按原始字节列出；`name`、`mode`、`mtime` 照常比较；规则在基一侧每有一份快照的成员被改写或不列，`applied` 计数加一。

【T17】`memory-updated-form` 启用时，基一侧每条 `memory_updated` 在规范化之前改写：`data.state_sha256` 无论取值都去掉；`data.consolidated` 恰为 `false` 时去掉；`data.reflection` 恰为 `null` 时去掉；`data.created_episode_ids[]`、`data.merged_episode_ids[]` 与 `data.episodes[].id` 中完全匹配 `m_00000000_(\d{6})` 的编号改成 `m_` 加那六位；`data.memory_before_sha256` 属于 `BASE_MEMORY_TEMPLATE_SHA256` 时改成 `null`，其余取值不动。有改动的事件让 `applied` 计数加一。

【T18】同一规则启用时，两侧 `memory_updated` 的 `data.episodes_sha256` 按首次出现的次序换成 `<episodes-digest:序号>`（新增的首次出现种类 `episodes-digest`，与 `memory-digest` 各自编号），两侧摘要的出现次序不同就报成不同；取值为 `null` 时照 `IdMaps.normalize` 的既有写法原样保留。规则不启用时 `episodes_sha256` 照原文比较。

【T19】`model-request-parameters` 启用时，基一侧 `model_request` 的 `data.request` 去掉键 `stream`、`max_retries`、`transport_max_retries` 中存在的那些，其余键与取值不动；有改动的事件让 `applied` 计数加一。头一侧不改，所以头一侧若还带这些键就报成不同。

【T20】`prompt-fingerprint` 是数值规则，用法 `--rule prompt-fingerprint=<基一侧指纹>:<头一侧指纹>`：两侧运行清单的 `model_identity.prompt_sha256` 与两侧导出清单的 `frozen_run_manifest.model_identity.prompt_sha256` 恰为这两个值时头一侧取基一侧的值，其余照常比较。检查点里的 `manifest_sha256` 随对齐、规范化后的清单摘要对齐，不另设规则。

【T21】代码身份不加新规则：`code_sha256`、`git_commit`、`git_dirty` 已在 `MANIFEST_MASKS` 与 `EXPORT_MANIFEST_MASKS` 上屏蔽，检查点 `manifest_sha256` 与 life 快照素材里的清单摘要经清单摘要登记对齐，质量报告以代码摘要为键的计划指标已改用占位键（`scripts/compare_runs.py:1165-1173`），只有代码身份不同的两次运行不带规则比较为相等。

【T22】四条规则只在 `--rule` 点名时生效；不点名时基线与新分支的运行在这四处报成不同。

### 四　查看器

【T23】`tools/run-viewer.html` 的 `memory_updated` 概要只查 `$.data.operation`、`$.data.created_episode_ids`、`$.data.merged_episode_ids` 三个路径，摘要对象只有 `op`、`created`、`merged` 三个键；概要文字恰为 `记忆更新 · {操作名} · {n} 段记忆`，不再出现「已巩固」；事件里即使带 `consolidated: true` 也不显示。

【T24】`tools/run-viewer-context.js` 改下表六处原文（三项说明的来源，加记忆视图的填法与两步），其余字节不变；`:94` 的基线原文把 `life_prompt.py` 写在不存在的 `src/worldmock/agents/` 下，这里一并改成实际路径：

| 位置（基线） | 之后的原文 |
| --- | --- |
| `:94` `tools.source` | `'src/worldmock/agents/life_agent.py · 原生工具；src/worldmock/agents/prompt/life_prompt.py · TOOL_DESCRIPTIONS'` |
| `:105` `message.source` | `'src/worldmock/agents/protocol/react_loop.py · ReactLoop._turn；src/worldmock/agents/life_agent.py · execute_decisions'` |
| `:144` `memory_view.source` | `'src/worldmock/agents/memory/life_memory.py · render_memory_view、save_step'` |
| `:145` `memory_view.fill` | `'工作区 MEMORY.md 由身份、至多五条已核验经历和恒为「暂无人物自述。」的自述段组成，最多四千字符。'` |
| `:147` 第一步 | `['每步重写', '根据当前身份与事实窗口重新生成视图。']` |
| `:149` 第三步 | `['范围区别', '文件四千字符预算不是整个请求的容量。']` |

【T25】查看器测试里拖入的运行目录用 `agents/agent_NNNN/MEMORY.md`，没有 `as2` 这一层；查看器按 `agent_?0*(\d+)` 认人物的代码（`tools/run-viewer.html:1796-1799`）不变。

### 五　测试树里不留框架

【T26】本章的迁移完成之后，`tests/`、`scripts/`、`tools/` 满足[「安装、代码身份、依赖与许可」](./07-install-identity.md)【I13】的导入规则：没有一个 Python 文件导入顶层模块 `agentsociety2`、`ray`、`mem0`、`mcp` 或 `custom`。守卫是本章新文件里的 `tests/unit/engineering/test_framework_removed_from_tests.py::test_no_test_script_or_tool_module_imports_the_removed_framework`（名字由[「实施、验收证据与完成判据」](./10-implementation.md)「跨章裁定」定下）：用 `ast` 解析这三个目录下全部 `.py`（跳过 `__pycache__`），`Import` 的每个名字与 `level == 0` 的 `ImportFrom` 的模块，按 `.` 切开后的第一段都不在 `{"custom", "agentsociety2", "ray", "mem0", "mcp"}` 里，集合的五个名字由字符串片段拼出；`src/` 一侧由 07 章的 `test_production_source_never_imports_the_removed_framework_or_custom` 守住，本条不重复扫描。

【T27】`tests/`、`scripts/`、`tools/`（不含由第 07 章【I26】管的 `tools/analysis/`）下后缀为 `.py`、`.js`、`.cjs`、`.html` 的文件不含子串 `agentsociety2`、`AGENTSOCIETY_`、`AgentSociety`、`--project-root`、`custom/agents`、`custom.agents`，也不匹配正则 `(?<![A-Za-z0-9_])(?:[Rr]ay|[Aa][Ss]2)(?![A-Za-z0-9_])`（不用 `\b`：中文字符算词字符）。`LifeEnv`、`custom.envs` 与 `custom/envs` 由[「去掉 LifeEnv」](./02-engine-facade.md)【E1】管，十一个进程环境变量名由第 07 章【I5】管，本条不重复。守卫文件里被禁的串由字符串片段拼出，守卫不跳过任何文件。

【T28】`tests/unit/as2/`、`tests/integration/as2/`、`tests/integration/runtime/test_llm_dispatcher_multikey.py`、`tests/integration/runtime/test_env_lifecycle_coverage.py`、`tests/integration/runtime/test_llm_client_close.py` 都不存在（`tests/integration/as2_fixtures.py` 的不存在由第 07 章【I1】【I2】守住）。

【T29】测试不再预设假凭据：`tests/conftest.py` 删去 `:23-24` 两行，只保留 `os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")` 一行环境变量缺省，`:22` 的注释改为 `# 离线测试不读取或要求供应商密钥；先于任何 litellm 导入固定本地价目表。`；另外 22 个文件各删去顶部的 `os.environ.setdefault("AGENTSOCIETY_…", …)` 行（清单见「接口与文件」）。`tests/mutation_runner.py` 子进程环境的写法归第 07 章【I16】。

### 六　删除与移植 AgentSociety2 专属测试

【T30】十四个 AgentSociety2 专属文件、两个包标记文件与 `tests/integration/runtime/test_env_lifecycle_coverage.py`、`tests/integration/runtime/test_llm_client_close.py` 删除；每个用例的去向恰由一章的表规定（「接口与文件」的「删除的测试文件」表指明是哪一章），表外没有遗漏的用例。本章自己的移植表只管其余各章不管的那些文件与用例。移植的用例除表中改名的以外沿用原名，断言主体不变，只按[「人物、回合循环与只读问答的删除」](./03-agent-loop.md)「测试里旧入口的对应」与[「去掉 LifeEnv」](./02-engine-facade.md)「测试里 LifeEnv 调用的替换」换被测对象，再加[「模型客户端与模型请求底稿」](./04-model-client.md)「移植时随本章规则改动的断言」列出的改动。

【T31】四个调度器相关文件改测 `ModelClient`：`tests/integration/runtime/test_model_exchange_attempts.py`、`tests/integration/runtime/test_network_ops_records.py`、`tests/integration/runtime/test_transport_park_run.py`、`tests/unit/engineering/test_shared_model_clients.py`。它们与 `tests/integration/life/test_memory_digest_runtime.py`、`tests/unit/runtime/test_model_client.py`、`tests/integration/runtime/test_model_client_wire.py` 共用的替身集中在新文件 `tests/unit/runtime/model_client_fixtures.py`，它提供的名字与内容恰为第 04 章「本章用例要求的测试夹具」一表；原来的 `_client`、`_error` 不再在任何测试文件里定义。各文件的断言改动按第 04 章「移植时随本章规则改动的断言」。

【T32】`tests/integration/runtime/test_env_lifecycle_coverage.py` 的用例按第 01 章「旧用例后继」表与第 02 章「删除的现有测试」表落位；`tests/integration/runtime/test_llm_client_close.py` 唯一的用例由第 04 章的 `tests/unit/runtime/test_model_client.py::test_close_closes_the_network_client_once` 承担网络客户端的关闭，存储连接的释放由第 01 章的 `test_run_host.py::test_close_keeps_engine_and_client_failures` 承担；`tests/integration/runtime/test_read_query_runtime.py` 按第 02 章的改写表改写（删去四条 Ray 与 actor 用例）。

【T33】只为 Ray 存在的其余用例随它们的生产符号删除，删除范围由各章规定：`tests/unit/runtime/test_error_pickling.py` 整个文件与 `tests/unit/footprints/test_status_liveness.py` 的 Ray 原因用例按[「故障文本」](./06-failure-text.md)；`tests/integration/runtime/test_host_branch_coverage.py` 的起停 Ray、结束失败 actor 与扫描注册用例按第 01 章，`_locked_fake_as2` 与 `test_prepend_pythonpath_is_idempotent` 按第 07 章。

### 七　导入、枢纽与夹具

【T34】决策轮类型的 27 条导入：两个枢纽的 5 条（`tests/life/conftest.py:20`、`:31`、`:295`、`:455`，`tests/life/environment_runtime.py:5`）与 14 个文件的 14 条改为 `from worldmock.state.decisions.round import …`（同名同签名，`:20` 仍在 `TYPE_CHECKING` 块里）；`tests/integration/life/test_memory_digest_runtime.py:679` 的 `SocietyCheckpointRequest` 随第 01 章【H23】那一行的改写消失；`test_env_lifecycle_coverage.py:33` 与 6 个专属文件的 6 条随文件删除。

【T35】`custom.*` 的 48 条导入（47 条语句加替身源码字符串里的一条）：随文件删除 5 条；25 条 `custom.agents.life_agent`（含 `from custom.agents import life_agent`）改为 `worldmock.agents.life_agent`，导入的名字照第 03 章仍存在的名字；17 条 `custom.envs.life_env` 与 `test_cli_clean_process.py:41` 替身源码里的一条删除，原来经 `LifeEnv` 做的事按第 02 章「测试里 LifeEnv 调用的替换」改写，`init` 一类的启动按【T55】。

【T36】其余 `agentsociety2` 导入按「接口与文件」的「导入对照」表换成 WorldMock 的对象或随用例删除。

【T37】`tests/integration/runtime/current_contract_fixtures.py`：
- `PROCESS_VARIABLES` 与 `aligned_process` 删除，调用方（`native_runtime_fixtures.py`、`test_input_warnings.py`、`test_runtime.py`、`test_runtime_inputs_contract.py`、`test_cli_clean_process.py`）去掉这一调用与导入；`environment(engine)` 删除，调用方直接传引擎。
- `ScriptedModel.call(self, request)` 只收一个位置参数，记下 `copy.deepcopy(request)`，其余不变。
- `person(engine, context, model, turns: int = 16) -> LifeAgent` 返回 `LifeAgent(replace(person_inputs(inputs().persona, identity), name="林舟", model_identity={**identity, "max_react_turns": turns}), engine, model, None)`，`identity = stage_model_identity(inputs().config.model, inputs().model_metadata)`；第四个参数（记忆存储）为 `None`，因为夹具只跑回合循环，不做步尾保存。
- 新增 `async def turns_with_history(agent, context, observation) -> tuple[list[ReactDecision], list[dict[str, Any]]]`：`agent._context = context`；`budget, history = agent._prepare(observation)`；`loop = ReactLoop(agent, context, budget, history)`；`registered = await loop.run()`；返回 `(registered, loop._history)`。它与第 03 章 `run_turns` 的三步相同，只多交还回合循环结束时的消息历史；今天读 `agent._react_messages` 的断言（`test_life_agent_protocol.py:916-917`、`:1123-1125`，`test_conclude_memories.py:685-692`，`test_decision_context_roundtrip.py:74-75`，`test_same_second_continuation.py:239`、`:657`，`tests/unit/agent_protocol/test_decision_context_protocol.py:130`）改读这份历史，断言主体不变；直接给 `_react_messages` 赋值的 `test_agent_branch_coverage.py:301-317` 按第 03 章改写。
- `decide(engine, round_id, responses)` 依次：`frozen = context(engine, round_id)`；`engine.begin_decision_round([frozen])`；`observed = engine.observe(frozen)`；`model = ScriptedModel(responses)`；`registered = await person(engine, frozen, model).run_turns(frozen, observed)`；`committed = engine.commit_decision_round(round_id)`；返回 `(observed, model.requests, [decision.action for decision in registered], committed)`。第三项从今天的字符串 `"<工具> registered"`（`custom/agents/life_agent.py:461`，随第 03 章删除）变为登记的工具名列表；读它的三处断言改为 `tests/integration/footprints/test_inspect_current_activity.py:211` 的 `answer == ["revise_day_plan"]`、`tests/integration/runtime/test_current_runtime_contract.py:19` 与 `:154` 的 `answer == ["start_main_activity"]`、`tests/integration/runtime/test_dayrun_parallel_agent.py:26` 的 `answer == [order[0], order[1]]`。

【T38】`tests/integration/life_host_fixtures.py` 只保留 `TruncatedResponse`：`ScriptedModel`、`reply`、`environment_for`、`decide` 与它们的导入删除（今天没有一处调用它们，`test_host_feedback.py:49` 的导入行随之删除）。`tests/integration/host_run_fixtures.py`：
- `DayModel.call(self, request)` 只收一个位置参数，只答行动请求的规则与失败文本 `LifeAgent sent a non-action model request` 不变。
- `host_run(engine, workspace, label="host", create=True)` 建 `inputs = person_inputs(engine.context.inputs.persona, engine.context.store.manifest["stage_model_identity"])` 与记忆存储 `WorldMockMemoryStore(workspace, inputs.agent_id, () if create else read_episodes(workspace, inputs.agent_id))`，返回 `HostRun(engine, inputs, store, DayModel(), label)`。
- `HostRun.__init__(self, engine, inputs, store, model, label)` 存下这些对象；属性 `agent`、`environment` 删除。`open()` 开轮时调 `engine.begin_decision_round([decision])`，不再绑定上下文；`decide(tool, arguments)` 每次新建 `LifeAgent(self.inputs, self.engine, self.model, self.store)`，`await agent.decide(decision)` 走完观察、回合与步尾保存，再 `(result,) = self.engine.commit_decision_round(decision.round_id)`。其余方法不变。

【T39】`tests/integration/runtime/native_runtime_fixtures.py`：
- `native_runtime` 不再替换 Society、Ray 任务、`init_dispatchers` 与 `WORKSPACE_PATH`，不再调 `aligned_process`；改为 `monkeypatch.setattr(runner, "_model_client", build)`，`build(prepared)` 调 `MODEL_FACTORY(prepared)`、把结果追加进 `models` 并返回它；`monkeypatch.setattr(run_host, "LifeEngine", _AttachingEngine)`，`_AttachingEngine.create(*arguments)` 与 `_AttachingEngine.open(*arguments)` 调真实的 `LifeEngine.create`、`LifeEngine.open`，令 `models[-1].engine = engine` 后返回引擎；零点计划与回顾的替换点是 `worldmock.runtime.lifecycle.midnight` 的 `PlanDayClient` 与 `MemoryDigestClient`，换成 `lambda client, identity: StubPlanDayClient(identity)` 与 `lambda client, identity: StubMemoryDigestClient(identity)`。返回值 `(root, frozen, models)` 不变。
- `CurrentDecisionModel.__init__(self, prepared)` 置 `self.engine = None`、`self.requests = []`、`self.closed = 0`；`call(self, request)` 记下 `copy.deepcopy(request)` 后从 `self.engine.context.state` 读世界，作答规则不变；`async def close(self)` 让 `closed` 加一。`MODEL_FACTORY = CurrentDecisionModel`，以 `prepared` 调用。
- `_ScriptedSemaphore`、`_ScriptedRouter`、`scripted_dispatcher(env, failures)` 删除，由 `failing_client(monkeypatch, prepared, failures) -> ModelClient` 取代：构造真实 `ModelClient(base_url=prepared.stage_identity["base_url"], api_key="offline", max_retries=prepared.stage_identity["max_retries"], transport_max_retries=prepared.stage_identity["transport_max_retries"])`，内含一个 `CurrentDecisionModel(prepared)`；把 `model_client.litellm.acompletion` 换成协程：记下关键字参数去掉 `client` 之后的深拷贝到列表 `client.wire_requests`，按「已作答次数加一」查 `failures`，还有剩余次数就减一并抛 `APIConnectionError(message="scripted connection failure", llm_provider="openai", model="test-model")`，否则返回 `await inner.call({key: arguments[key] for key in REQUEST_KEYS})`；`_AttachingEngine` 交来的引擎同时设给 `inner.engine`。`test_transport_park_run.py` 改为 `monkeypatch.setattr(fixtures, "MODEL_FACTORY", lambda prepared: fixtures.failing_client(monkeypatch, prepared, failures))`，并以 `[model.wire_requests for model in models]` 取代 `model._router.requests`。

【T40】`tests/life/conftest.py` 删去 `:16` 的假凭据；两个文档串去掉 `LifeEnv`：`:56` 改为「用于经运行宿主驱动的测试之前时传 submitted=True，零点计划任务就不再为该人物生成计划。」，`:317` 的「照 LifeEnv.step」改为「照运行宿主的零点推进」。`tests/life/environment_runtime.py` 改导入；`act_group` 的断言与文档串按第 02 章「要改的现有测试」一表。

【T41】`tests/integration/runtime_fixtures.py`：
- `write_agent_workspace(workspace: Path) -> Path` 只 `workspace.mkdir(parents=True)` 并返回该目录；参数 `agent_document`、`config_document`、`missing`、`initialized` 与辅助函数 `_created_persona_profile` 删除（同一处改动第 05 章也列出，只做一次）；带 `agent_document` 的三处调用中，`test_runtime_inputs_contract.py:48` 与 `test_cli_clean_process.py:98` 随【T55】的改写消失，`test_runtime_inputs_contract.py:218` 随第 05 章删除的用例消失。快照这个空目录得到的就是新建运行在第一次决策之前的人物快照。
- `write_project` 从 `tests/integration/as2_fixtures.py` 搬来，`current_run_identity` 改调 `runtime_code_sha256()`，两处写法按第 07 章。
- 新增 `class OfflineClient`：`__init__` 置 `self.closed = 0`；`async def call(self, request)` 抛 `AssertionError("Offline client received a model request")`；`async def close(self)` 让 `closed` 加一。
- 新增 `async def new_run(prepared, client) -> RunHost`：函数内导入 `RunHost` 与 `runner._creation_options`；`host = RunHost(prepared.run_dir, prepared.inputs, client, prepared.agents, prepared.memory_records)`；`await host.start_new(prepared.run_identity, prepared.stage_identity, _creation_options(prepared))`；返回尚未关闭的 `host`。

【T42】`tests/dayrun_baseline.py::deterministic_day_runtime` 删去 `import agentsociety2.agent.person as person` 与 `monkeypatch.setattr(person, "datetime", _RecordedTime)` 两行，其余打桩不变。

【T43】`tests/integration/runtime/test_native_dataset_lifecycle.py:106`、`:126` 调用 `main` 时不带 `"--project-root", str(ROOT)`（第 07 章也列出这一处）；`:53-54` 读 `AGENT.json` 的两行改为断言 `run_dir/agents/agent_0001` 里全部普通文件的相对 POSIX 路径集合恰为 `{"MEMORY.md", "memory/episodes.jsonl"}`；`:55` 按第 04 章【M21】改；模块文档串改为「完整运行、共同恢复和反事实重放走真实运行宿主、人物工作区、LifeAgent 与引擎。」。

【T44】`tests/integration/life/test_state_serialization.py:79-88` 的 `_subprocess_env` 删去 `vendor` 局部变量与 `WORKSPACE_PATH`，子进程 `PYTHONPATH` 恰为 `os.pathsep.join((str(root / "src"), str(root)))`，`PYTHONHASHSEED` 与 `LITELLM_LOCAL_MODEL_COST_MAP` 不变。

【T45】纯加载边界的禁止模块集合删去已删除的 `custom.envs.life_env` 与 `worldmock.runtime.framework.read_query`，加上 `worldmock.runtime.lifecycle.run_host` 与 `worldmock.configuration.model.model_client`；以 `agentsociety2` 为前缀的断言删除。涉及：
- `tests/integration/environment/test_domain_dependencies.py:27-30`：集合为 `{'worldmock.simulation_engine.engine','worldmock.simulation_engine.game','worldmock.runtime.lifecycle.run_host','worldmock.configuration.model.model_client'}`；
- `tests/system/cli/test_cli.py:62-84` 两个用例：集合为 `('worldmock.simulation_engine.engine','worldmock.simulation_engine.world.context','worldmock.runtime.lifecycle.run_host','worldmock.configuration.model.model_client')`，用例改名为 `test_pure_cli_and_runtime_imports_do_not_load_engine_or_model_client` 与 `test_real_pytest_collection_of_pure_commands_does_not_load_engine_or_model_client`；
- `tests/unit/engineering/test_bootstrap.py:316-320` 的组件探针：集合为 `{'worldmock.evaluation.reporting', 'worldmock.simulation_engine.engine', 'worldmock.simulation_engine.game', 'worldmock.simulation_engine.games', 'worldmock.runtime.lifecycle.run_host', 'worldmock.configuration.model.model_client'}`；
- `tests/unit/evaluation/test_memory_pilot.py:225` 的前缀元组删去 `'custom'`。

【T46】门禁测试里的 `custom` 特例按第 07 章【I14】删除（`test_style_gate.py`、`test_single_source.py`、`test_coverage_gate.py`、`test_import_graph.py` 的扫描根与门面使用者）；`test_import_graph.py` 的 `DELAYED_IMPORTS` 按第 01、02、04、05、07 章各自列出的删改合并成一份，最终与代码里的函数内导入一条不多、一条不少（既有断言）。这几个文件由一个实施代理统一修改，本章不另加改动。

【T53】`tests/integration/runtime/test_life_agent_protocol.py` 的协议替身：
- `_Environment` 与 `_StepEnvironment` 的 `read_query`、`submit_action`、`record_model_exchange`、`record_memory_update`、`observe` 改为同步 `def`（第 03 章 `EngineHandle` 的五个方法），返回值不变。`_StepEnvironment` 的 `scheduling_state`、`begin_decision_round`、`commit_decision_round`、`abort_decision_round`、`fail_run`、`checkpoint` 也改为同步 `def`，记录行为不变；它另有运行宿主读取的两个属性：`context = SimpleNamespace(state=SimpleNamespace(run_id="run", current_time=<observed_at 的整秒时间戳>, end_time=<它加 3600>), config=SimpleNamespace(timezone="UTC"))` 与 `decisions = SimpleNamespace(round=None)`，`begin_decision_round` 把 `decisions.round` 设为本轮身份，`commit_decision_round` 与 `abort_decision_round` 把它设回 `None`。`_service`、`_MemoryRuntime`、`_create_step_workspace` 删除。
- 新增 `_step_host(monkeypatch, run_dir, environment, dispatcher, max_turns=4) -> RunHost`：先把 `worldmock.runtime.lifecycle.run_host` 模块里的 `snapshot_workspaces` 换成返回 `{}` 的记录替身，`settle_pending_memory_digests` 与 `settle_pending_day_plans` 换成返回 `[]` 的异步记录替身（假环境没有 `context.store`，宿主的检查点与零点任务不读它）；再 `host = RunHost(run_dir, inputs(), dispatcher, {7: _wire_agent(environment, dispatcher, max_turns).inputs}, {7: ()})`，`host.engine = environment`，返回 `host`；人物工作区就是 `run_dir / "agents" / "agent_0007"`，记忆存储由宿主经第 05 章的 `memory_stores` 建。假环境原有的 `checkpoint` 记录照旧。
- `_Dispatcher`、`_StepDispatcher`、`_GroundedCorrectionDispatcher` 的 `call(self, **request)` 改为 `call(self, request)`；`_assert_action_wire` 按第 04 章【M21】改。
- `_wire_agent(agent, environment, dispatcher, max_turns, *, memory=True)` 改为 `_wire_agent(environment, dispatcher, max_turns, memory=None) -> LifeAgent`，返回 `LifeAgent(replace(person_inputs(inputs().persona, identity), agent_id=7, name="Alice", model_identity={**identity, "max_react_turns": max_turns}), environment, dispatcher, memory)`，`identity` 同【T37】；需要真实记忆文件的用例传 `WorldMockMemoryStore(tmp_path / "agents" / "agent_0007", 7, ())`，`:1517` 的路径随之改为 `tmp_path / "agents" / "agent_0007"`。
- `_prepared(observations)` 改为 `_prepared(observation: dict | None = None) -> LifeAgent`：`agent = _wire_agent(_Environment(), None, 16)`，`agent._context = _context()`，`agent._prepare(observation or _observations()[0]["data"])`，返回 `agent`；`_functions(agent)` 读 `agent.turn_tools(2, False)`。
- `await agent.run_react_loop(ReactLoopRequest(0, t, observations))` 改为 `registered = await agent.run_turns(_context(), observations[0]["data"])`；随后要读消息历史的用例改为 `registered, history = await turns_with_history(agent, _context(), observations[0]["data"])`（【T37】）；`summary == f"{X} registered"` 改为 `[decision.action for decision in registered] == [X]`；`agent._react_messages` 改读 `history`，`agent._last_finish_memories` 改读 `agent._memories`。
- 经 `AgentSociety` 驱动的 `test_memory_persistence_failure_aborts_society_before_commit`（基线 `:1503-1579`）改为 `host = _step_host(monkeypatch, tmp_path, environment, dispatcher)`，把 `worldmock.agents.memory.life_memory_store.WorldMockMemoryStore.append_episodes` 换成抛 `OSError("injected append failure")` 的替身，再 `await host.run()`；删去 `WORKSPACE_PATH`、`society_module` 的替换与 `AgentSocietyConfig`；读替身 `calls`、`evidence`、`memory_updates`、`commits`、`checkpoints`、`aborts`、`failures` 的断言主体不变，`pytest.raises` 与两处文本按第 06 章该用例一行。

【T54】`tests/integration/runtime/test_same_second_continuation.py` 的决策辅助：`_environment(engine)` 删除，调用方直接传引擎；`_person(engine, ctx, dispatcher) -> LifeAgent` 返回 `LifeAgent(replace(person_inputs(inputs().persona, identity), name=f"人物{ctx.agent_id}", model_identity={**identity, "max_react_turns": 4}), engine, dispatcher, None)`；`_decide(engine, ctx, responses)` 依次 `observation = engine.observe(ctx)`、建 `_Script`、`person = _person(...)`、`_, history = await turns_with_history(person, ctx, observation)`，返回 `(observation, dispatcher.requests, history)`；`_single(engine, round_id, responses)` 开轮、`_decide`、提交，返回同一三元组；第三项今天只在 `:239` 与 `:657` 被读作 `<人物>._react_messages[-1]`，改为 `run["planner"][-1]` 与 `starter[-1]`；`_Script.call(self, request)` 只收一个位置参数。`:732-748` 经工作区建人物与走一步的两个辅助：
- `_workspace_person(engine, workspace, responses) -> tuple[LifeAgent, _Script]`：`dispatcher = _Script(responses)`；`records = read_episodes(workspace, 1) if (workspace / "memory" / "episodes.jsonl").is_file() else ()`；返回 `(LifeAgent(replace(person_inputs(inputs().persona, identity), model_identity={**identity, "max_react_turns": 8}), engine, dispatcher, WorldMockMemoryStore(workspace, 1, records)), dispatcher)`。每次调用都从工作区读回已有条目，等于今天「此后每次决策都从工作区重建」；不再写 `AGENT.json`，不再调 `LifeAgent.create` 与 `from_workspace`；文档串删去「与 AS2 每次决策重建人物相同」。
- `_step(engine, person, round_id) -> list[str]`：`ctx = context(engine, round_id)`；`engine.begin_decision_round([ctx])`；`observation = engine.observe(ctx)`；`registered = await person.run_turns(ctx, observation)`；`person._record_memory(ctx)`；`engine.commit_decision_round(round_id)`；返回 `[decision.action for decision in registered]`。这三步就是第 03 章 `decide` 的观察、回合与步尾，只多交还登记的工具名；读它的两处断言 `:766`、`:776` 改为 `== ["revise_day_plan"]` 与 `== [MAIN_TOOL]`。

`tests/integration/runtime/test_output_truncation.py` 从这里导入的名字不变，调用处把 `_environment(engine)` 换成 `engine`。`tests/integration/runtime/test_errand_prompt_separation.py` 删去 `environment` 的导入，`env = environment(engine)` 删去，`_workspace_person(engine, …)` 与 `await _step(engine, agent, "concluded")` 直接传引擎，断言主体不变。

【T55】在测试进程里经 `LifeEnv(...).init(...)` 建新运行的四处改为 `host = await new_run(prepared, OfflineClient())`，读 `env.engine` 的地方读 `host.engine`，`await env.close()` 改为 `await host.close()`：`tests/integration/runtime/test_runtime.py:397-398`、`tests/integration/runtime/test_runtime_inputs_contract.py:46-47`（`prepared_parent` 另删去 `write_agent_workspace` 与 `snapshot_directory`，检查点改为 `host.engine.checkpoint(snapshot_workspaces(prepared.run_dir, (1,), host.engine.context.store.assets))`）、`tests/system/cli/test_cli_clean_process.py:95-96`（`_parent_with_checkpoint` 同样取检查点，在 `finally` 里 `await host.close()`）、`tests/system/lifecycle/test_run_manifest.py:30-32`（先 `prepared = _prepare_new_run(RunRequest(ENVIRONMENT_ROOT, tmp_path / "run", "manifest", "manifest"))`，清单断言不变）。`test_cli_clean_process.py` 替身源码（`:41-44`）里新建模式的判定改为 `if prepared.run_identity is not None:`，正文改为函数内导入 `RunHost`、`host = RunHost(prepared.run_dir, prepared.inputs, runtime._model_client(prepared), prepared.agents, prepared.memory_records)`、在 `try` 里 `await host.start_new(prepared.run_identity, prepared.stage_identity, runtime._creation_options(prepared))`、`finally` 里 `await host.close()`；替身打印的证据由第 07 章【I5】那一行规定。

【T56】`tests/integration/runtime/test_conclude_memories.py`：`:480-483` 经工作区建人物的辅助改为 `LifeAgent(person_inputs(...), engine, dispatcher, WorldMockMemoryStore(workspace, 1, ()))`，名字「陈曦」照旧经 `replace(..., name="陈曦")` 给出；`:739-760` 的 `_society(monkeypatch, tmp_path, environment, dispatcher)` 删除，用到它的用例改为从 `test_life_agent_protocol.py` 导入 `_step_host`、`host = _step_host(monkeypatch, tmp_path, environment, dispatcher)`、`await host.run()`，`:766` 的工作区创建（`_create_step_workspace(tmp_path / "as2" / "agents" / "agent_0007")`）删除，`pytest.raises` 的类型与文本按第 06 章，其余断言主体不变；原来经 `agent.step(0, t)` 与 `bind_decision_context` 的调用改为 `await agent.decide(context)`；`:189`、`:350` 的 `agent._last_finish_memories` 读作 `agent._memories`，`:685-692` 读消息历史按【T37】的 `turns_with_history`。

### 八　变异台账与执行器

【T47】`tests/dataset_acceptance.py` 里受影响的既有变异按下表处理，其余变异不动；改指后的每个符号都能唯一解析，每个杀手都存在且不跳过（既有的 `tests/system/mutations/test_source_mutations.py` 断言不变）。每一行的处理与归属章一致。

| 行 | 变异 | 处理 | 新符号 | 杀手 | 归属 |
| --- | --- | --- | --- | --- | --- |
| 87 | `restored_card_not_compared` | 删除 | — | — | 05 章 |
| 402 | `prompt_snapshot_takes_a_position` | 删除 | — | — | 03 章 |
| 450 | `process_configured_after_the_model_identity` | 删除 | — | — | 07 章【I17】 |
| 492 | `action_output_budget_back_to_eight_thousand` | 改指，原文与替换文不变 | `worldmock.agents.life_agent::ACTION_MAX_TOKENS` | 不变 | 03 章 |
| 605 | `read_query_channel_ignores_the_tool_name` | 改指，原文与替换文不变 | `worldmock.simulation_engine.decisions.read_query::ReadQueryResponder.answer` | 不变（`test_read_query_runtime.py::test_read_query_answers_task_recall_and_memory_search_only` 改测引擎后保持原名） | 02 章 |
| 1231、1234、1238 | 三个 `*_unpicklable` | 删除 | — | — | 06 章 |
| 1241 | `failure_message_ignores_ray_cause` | 删除 | — | — | 06 章 |
| 1268、1273 | `plan_day_correction_attempts_dropped`、`plan_day_response_attempts_dropped` | 改指 | `worldmock.runtime.lifecycle.midnight::_generate_day_plan` | 不变 | 01 章 |
| 1278 | `memory_digest_response_attempts_dropped` | 改指 | `worldmock.runtime.lifecycle.midnight::_generate_memory_digest` | 不变 | 01 章 |
| 1284 | `env_close_leaves_cached_clients` | 改名 `model_client_close_leaves_the_network_client_open`，改指；替换让网络客户端不被关闭，锚点由实施者在 `close` 里选 | `worldmock.configuration.model.model_client::ModelClient.close` | `tests/unit/runtime/test_model_client.py::test_close_closes_the_network_client_once` | 本章（04 章的规则） |
| 1309 | `step_end_copies_every_memory_again` | 删除 | — | — | 05 章 |
| 1319 | `usage_estimate_of_another_request_accepted` | 删除 | — | — | 03 章 |

【T48】`tests/mutation_runner.py` 与 `tests/unit/engineering/test_mutation_runner.py` 的改动归第 07 章【I16】，本章不另改。

【T49】本章新登记三个源码变异（见「测试」的变异表），登记在 `tests/dataset_acceptance.py` 末尾，杀手都是直接导入被变异模块的单元测试。

### 九　基线重冻与样例重录

【T50】两份完整日基线 `tests/fixtures/dayrun-activity-baseline.json`、`tests/fixtures/dayrun-callback-baseline.json` 在全部生产改动完成之后按双候选规程重冻：在 `.work/agentsociety2-removal/freeze/` 写不入库的 `record_candidate.py`（读种类 `activity` 或 `callback`、一个尚不存在的案例目录和输出文件；用 `_pytest.monkeypatch.MonkeyPatch()` 调 `tests.life.day_baseline_runtime.record_complete_day(patch, 案例目录, 仓库根, 种类)`，结束时 `patch.undo()`；结果按 `json.dumps(value, ensure_ascii=False, indent=2) + "\n"` 写出）；每种在两个独立进程里各录一份候选；再用新分支自己引导出的解释器跑 `scripts/freeze_rrs_baseline.py <种类>-1.json <种类>-2.json tests/fixtures/dayrun-<种类>-baseline.json`。两份候选不相等时目标文件保留原字节，查明不确定的来源，不重试到相等为止。

【T51】`tests/fixtures/viewer-context.json` 在全部生产改动完成之后离线重录：在 `.work/agentsociety2-removal/viewer/` 写一个不入库的 pytest 测试文件，用夹具 `tests.engine_fixture.engine_factory` 调 `tests.system.lifecycle.test_run_viewer_recording._recorded_viewer_requests`，把返回的 `{"action": …, "continuation": …}` 按 `json.dumps(value, ensure_ascii=False, indent=2) + "\n"` 写进该文件。重录后两个请求字典的键与次序都恰为 `list(REQUEST_KEYS)`（`worldmock.configuration.model.model_client.REQUEST_KEYS`），没有 `stream`、`max_retries`、`transport_max_retries`。

【T52】重冻与重录之前，`tests/system/lifecycle/test_complete_day_baseline.py::test_complete_day_matches_frozen_current_inputs_and_complete_evidence` 与 `tests/system/lifecycle/test_run_viewer_recording.py::test_viewer_fixture_is_recorded_from_the_current_host` 不作门禁，分片全量时用 `--deselect` 去掉，不加跳过标记；查看器的 Node 用例读样例只当数据，照常运行。重冻与重录之后它们与其余测试一样必须通过。

## 接口与文件

### 工具与脚本

| 文件 | 动作 | 内容 |
| --- | --- | --- |
| `scripts/recorded_response_run.py` | 改 | 删 `:61-67` 三个常量与注释、`:567-574` `as2_location`、`:904-910` 两项检查；`_routing_name`（`:583-585`）的集合为 `{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}`，文档串「会把模型流量引到别处的变量名。」；`project_pythonpath`（`:588-590`）返回 `os.pathsep.join(str(path) for path in (root / "src", root))`，文档串「只导入这一检出自己的代码：它的 src 与根目录。」；`child_environment`（`:593-600`）文档串「子进程环境：去掉凭据与代理，只给占位凭据，只导入项目自己的代码。」；`_segment_argv`（`:662-673`）按【T3】去掉 `"--project-root", project`，局部变量 `project` 随之删除；`finish`（`:799`）与 `export_source`（`:1010`）的命令去掉 `"--project-root", str(project)`；`_error_body`（`:329-330`）文档串「HTTP 400 正文：LiteLLM 把它读成 BadRequestError，模型客户端不重试。」 |
| `scripts/recorded_response_child.py` | 改 | 模块文档串按【T6】删一句，代码不变 |
| `scripts/compare_complete_days.py` | 改 | 按【T7】–【T9】；`:30` 的导入改为 `from scripts.recorded_response_run import project_pythonpath`，加 `from scripts.bootstrap_environment import environment_python`；模块文档串的 `@brief` 改为「每一侧用这一侧引导出来的虚拟环境（<根>/.venv）与这一侧的代码、测试助手录制，子进程只从这一侧的 src 与根目录导入，两侧依赖锁可以不同。签名相等即 equal；不等时若给了 --rule，就用 compare_runs 在声明的重建规则下逐项比较两侧的运行与导出，两种完整日都相等即 equal_under_rules；否则 different，并附上 compare_runs 报告给出叶子路径。设计见 docs/design/01-architecture.md「工程验证」。」 |
| `scripts/compare_runs.py` | 改 | 按【T11】–【T22】 |
| `tools/run-viewer.html` | 改 | `:553-557` 与 `:708-711` 按【T23】 |
| `tools/run-viewer-context.js` | 改 | 按【T24】 |

`scripts/compare_complete_days.py` 改后的接口：

```python
def side_python(root: Path) -> Path:
    """@brief 一侧自己引导出来的解释器：environment_python(<根>/.venv)。"""

def side_environment(root: Path) -> dict[str, str]:
    """@brief 一侧子进程只导入这一侧的代码：PYTHONPATH 是这一侧的 src 与根目录。"""
    # {**os.environ, "PYTHONPATH": project_pythonpath(Path(root).resolve()), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}

def require_comparable_roots(base: Path, head: Path) -> None:
    """@brief 两个检出各有自己引导出来的解释器与录制入口，否则以 ComparisonError 拒绝。"""

def record_side(root: Path, case_root: Path) -> dict:
    """@brief 在一侧检出里用这一侧的解释器录两份完整日并读回签名。"""
    # 命令为 [str(side_python(root)), "-P", "-m", "tests.life.behaviour_day", "--case-root", str(case_root), "--output", str(output)]
```

`scripts/compare_runs.py` 新增与改动的名字：

| 名字 | 定义 |
| --- | --- |
| `BASE_MEMORY_TEMPLATE_SHA256` | `frozenset({"fed81886cb8287697bcc29e8ee01c15a995bae6b891c74672799c143293b9367", "b0c0cc2fbeb9017c6a1b94217be4ad04fbcb033231a7ddc602feb4a001d3cd6b"})`：基线英文模板 `MEMORY.md` 按 LF 与 CRLF 写出的两个摘要 |
| `BASE_WORKSPACE_ONLY` | `frozenset({"AGENT.json", "config.json", "life_prompt_snapshot.json", "memory/state.json"})` |
| `BASE_EPISODE_CONSTANTS` | `(("tick", 0), ("tick_duration_sec", 0), ("refs", []))` |
| `BASE_EPISODE_COUNTERS` | `("step_index", "step_count")` |
| `BASE_EPISODE_ID` | `re.compile(r"m_00000000_(\d{6})")`，用 `fullmatch` |
| `REMOVED_REQUEST_PARAMETERS` | `("stream", "max_retries", "transport_max_retries")` |
| `FIRST_SEEN_KINDS` | `frozenset({"memory-digest", "episodes-digest"})`；上方两行注释（`:91-92`）里的出处 `agents/life_memory.py:306` 改为 `src/worldmock/agents/memory/life_memory.py · render_memory_view`，其余字不改，再加第三行 `#: episodes-digest 是 memory-updated-form 规则下 memory/episodes.jsonl 的摘要，同样只按出现次序成双射比较。` |
| `head_episode_id(value: object) -> object` | 完全匹配 `BASE_EPISODE_ID` 的字符串改写成 `m_` 加六位，其余原样返回 |
| `align_memory_update(event: dict) -> dict` | 【T17】的基一侧改写；没有改动时返回同一个对象 |
| `drop_request_parameters(event: dict) -> dict` | 【T19】的基一侧改写；没有改动时返回同一个对象 |
| `_workspace_member(name: str, raw: bytes, context: NormalizeContext) -> bytes \| None` | 【T11】【T13】【T15】：返回要取摘要的字节，`None` 表示不列这个成员 |
| `_base_episodes(raw: bytes) -> bytes`、`_head_episodes(raw: bytes) -> bytes` | 【T14】【T15】【T16】 |
| `_workspace_listing` | 每个成员经 `_workspace_member`，返回 `None` 的跳过；基一侧在规则下有成员被改写或跳过时 `applied` 加一；文档串删去「AGENT.json 屏蔽壁钟与地址」 |
| `align_events` | 基一侧事件先经 `memory-updated-form` 与 `model-request-parameters`（启用时），再做既有的头一侧对齐 |
| `normalize_event` | `memory-updated-form` 启用且事件为 `memory_updated` 时，`data.episodes_sha256` 经 `IdMaps.normalize(value, "episodes-digest", side, "data.episodes_sha256")` |
| `align_manifests` | `prompt-fingerprint` 启用时，运行清单对 `model_identity.prompt_sha256`、导出清单对 `frozen_run_manifest.model_identity.prompt_sha256` 调 `_value_rule` |
| `AGENT_MASKS` | 删除 |

### 删除的测试文件

| 文件 | 行数 | 用例去向 |
| --- | --- | --- |
| `tests/unit/as2/__init__.py`、`tests/integration/as2/__init__.py` | 0 | — |
| `tests/unit/as2/test_decision_round.py` | 75 | 01 章【H40】 |
| `tests/unit/as2/test_direct_router.py` | 95 | 01 章：删除 |
| `tests/unit/as2/test_direct_runner_failures.py` | 48 | 01 章：删除，没有后继 |
| `tests/unit/as2/test_llm_dispatcher_no_retry.py` | 48 | 本章移植表 |
| `tests/unit/as2/test_llm_dispatcher_transport.py` | 779 | 本章移植表 |
| `tests/unit/as2/test_memory.py` | 347 | 05 章：删除，不移植 |
| `tests/unit/as2/test_memory_restore.py` | 71 | 05 章 |
| `tests/unit/as2/test_society_decision_round.py` | 378 | 01 章「旧用例后继」表 |
| `tests/integration/as2/test_agent_react_validation.py` | 454 | 本章移植表 |
| `tests/integration/as2/test_llm_callback_lifecycle.py` | 439 | 本章移植表 |
| `tests/integration/as2/test_worldmock_life_hooks.py` | 592 | `:151-:326` 本章移植表；`:381-:592` 05 章「删除与移植」 |
| `tests/integration/as2/test_worldmock_memory_boundaries.py` | 162 | 05 章 |
| `tests/integration/as2/test_worldmock_registration.py` | 36 | 本章：两个用例删除（扫描注册没有了） |
| `tests/integration/runtime/test_llm_dispatcher_multikey.py` | 41 | 本章移植表 |
| `tests/integration/runtime/test_env_lifecycle_coverage.py` | 617 | 01 章后继表与 02 章「删除的现有测试」 |
| `tests/integration/runtime/test_llm_client_close.py` | 41 | 【T32】 |

前十四个专属文件（两个 `__init__.py` 之外，到 `test_llm_dispatcher_multikey.py` 为止）共 3,565 行；其中四个调度器文件（传输、零重试、回调生命周期、多 key）共 1,307 行。`tests/integration/as2_fixtures.py` 与 `tests/unit/engineering/test_shared_rules_spec.py` 的删除归第 07 章，`tests/unit/runtime/test_error_pickling.py` 的删除归第 06 章。

### 新测试文件

| 文件 | 内容 |
| --- | --- |
| `tests/unit/runtime/model_client_fixtures.py` | 【T31】；名字与内容恰为第 04 章「本章用例要求的测试夹具」一表 |
| `tests/integration/runtime/test_react_loop.py` | 第 03 章的回合循环用例（替身由那一章规定），加本章移植表里落到这个文件的用例 |
| `tests/unit/engineering/test_framework_removed_from_tests.py` | 【T26】【T27】【T28】【T29】的守卫，四个用例：`test_no_test_script_or_tool_module_imports_the_removed_framework`、`test_no_test_script_or_tool_file_names_the_removed_framework`、`test_the_framework_only_test_files_are_gone`、`test_the_test_session_sets_only_the_local_cost_map`；被禁的串一律由片段拼出 |

`tests/unit/runtime/test_model_client.py`（04 章）放进本章移植表落到它的用例；`tests/unit/runtime/test_decision_round_types.py`、`tests/integration/runtime/test_run_host.py`（01 章）、`tests/unit/agent_protocol/test_tool_surface.py`（02 章）、`tests/unit/agents/test_life_memory_store.py`（05 章）由各自的章新建。

### 本章的移植表

被测对象的改换：原来的 `LLMClient`/Router 换成 `ModelClient`（经 `scripted_client`），原来经 `ReactLoopRequest` 直接驱动 `AgentBase` 的换成第 03 章 `test_react_loop.py` 里的真实 `LifeAgent`、五方法引擎替身与 `call(request)` 模型替身。

**`tests/unit/as2/test_llm_dispatcher_transport.py` → `tests/unit/runtime/test_model_client.py`**

| 用例 | 处理 |
| --- | --- |
| `test_typed_transient_failure_recovers_with_zero_ordinary_retries`、`test_persistent_transport_failure_ends_when_the_park_budget_is_spent`、`test_non_transport_provider_errors_do_not_use_transport_budget`、`test_payload_and_business_errors_do_not_gain_transport_retries`、`test_mixed_failures_spend_separate_budgets`、`test_exhausted_ordinary_budget_does_not_borrow_transport_retries`、`test_exhausted_transport_budget_does_not_borrow_ordinary_retries`、`test_rate_limit_uses_fixed_one_second_backoff_with_sixty_second_cap`、`test_dispatcher_does_not_log_provider_error_body`、`test_configured_transport_budget_controls_actual_attempts`、`test_authentication_and_parameters_fail_with_unspent_retry_budgets`、`test_spent_transport_count_parks_and_resends_the_identical_request`、`test_park_gives_up_after_twelve_hours_with_the_root_cause`、`test_rate_limit_parks_after_the_ordinary_count_and_honours_retry_after`、`test_retry_after_beyond_the_budget_fails_at_once`、`test_unparsable_retry_after_is_ignored_with_a_warning`、`test_counted_rate_limit_waits_honour_a_longer_retry_after`、`test_fatal_and_ordinary_failures_never_park`、`test_deadline_expiry_resends_the_same_request_as_a_transport_retry`、`test_deadline_scales_with_max_tokens`、`test_foreign_timeout_error_is_not_relabelled`、`test_collector_records_each_failed_send_outside_the_request`、`test_failed_call_records_its_final_send_with_a_null_wait`、`test_collectors_are_isolated_between_concurrent_tasks`、`test_without_a_collector_nothing_is_recorded`、`test_error_chain_follows_cause_then_unsuppressed_context` | 同名移植 |
| `test_cancellation_is_not_retried_and_releases_semaphore` | 改名 `test_cancellation_is_not_retried` 移植；许可释放部分删除（并发阀没有了） |
| `test_invalid_retry_budgets_do_not_send_request` | 改名 `test_invalid_retry_budgets_are_refused_when_the_client_is_built` 移植：两类重试次数在构造客户端时校验一次 |
| `test_real_router_makes_only_one_provider_attempt` | 改名 `test_every_send_passes_zero_retries_and_the_three_hundred_second_timeout` 移植：`litellm.acompletion` 每次收到 `max_retries=0` 与 `timeout=300`，每次发送一次调用 |
| `test_router_disables_underlying_retries_and_preserves_declared_credential` | 改名 `test_client_sends_the_declared_credential_as_given` 移植：凭据原样交给网络客户端；Router 参数部分删除 |
| `test_real_router_keeps_accepting_parked_resends`、`test_real_router_waits_exactly_the_providers_retry_after`、`test_real_router_retry_after_beyond_the_park_budget_ends_the_call` | 改名去掉 `real_router`（`test_parked_resends_keep_reaching_the_local_server`、`test_waits_exactly_the_providers_retry_after`、`test_retry_after_beyond_the_park_budget_ends_the_call`）移植：真实 `ModelClient` 对本地服务发送 |
| `test_explicit_non_streaming_request_reaches_router`、`test_streaming_is_rejected_before_any_external_request` | 删除：请求字典不再带 `stream`（裁定 6），发送参数归第 04 章 |
| `test_environment_router_does_not_forward_retired_retry_parameters`、`test_environment_payload_validation_does_not_forward_retired_retry_parameters`、`test_real_router_has_no_fallback_models` | 删除：环境路由、`coder` 角色与 Router 没有了 |

**`tests/unit/as2/test_llm_dispatcher_no_retry.py` → `tests/unit/runtime/test_model_client.py`**：`test_zero_retries_makes_exactly_one_external_attempt` 同名移植。

**`tests/integration/as2/test_llm_callback_lifecycle.py` → `tests/unit/runtime/test_model_client.py`**

| 用例 | 处理 |
| --- | --- |
| `test_service_proxy_closes_every_configured_role` | 改名 `test_close_closes_the_network_client_once` 移植：一次 `await client.close()` 恰好调用一次它持有的 `AsyncOpenAI` 的 `close`，之后 `is_closed()` 为真；`ModelClient` 不记自己的关闭状态，不测重复关闭（第 04 章 `close` 一行） |
| `test_five_real_worker_tasks_leave_no_unclosed_client_records` | 改名 `test_a_closed_client_leaves_no_unclosed_session_records` 移植：真实 `ModelClient` 对本地服务完成五次调用后关闭，没有未关闭会话的告警记录 |
| `test_worker_tasks_extract_stats_before_cleanup`、`test_worker_task_cleanup_covers_failure_and_cancellation`、`test_stats_failure_still_closes_llm_clients`、`test_more_than_one_hundred_task_loops_leave_no_router_callbacks`、`test_worker_helper_closes_real_router_after_event_loop`、`test_cross_loop_rebuild_discards_only_previous_router`、`test_closing_unused_roles_does_not_build_routers`、`test_close_logs_discard_failure_and_clears_runtime`、`test_close_cached_async_clients_closes_and_evicts_only_this_loop`、`test_cached_client_close_failure_evicts_all_then_raises_the_first`、`test_worker_coroutine_closes_cached_clients_inside_its_loop`、`test_worker_task_failure_and_close_failure_are_both_kept` | 删除：Ray 工作任务、用量统计、Router 回调、按事件循环的客户端缓存与三个角色都没有了 |

**`tests/integration/runtime/test_llm_dispatcher_multikey.py`**：`test_single_key_builds_single_deployment` 删除（Router 部署没有了）；`test_deployments_use_openai_prefix_and_passthrough_base` 改名 `test_client_sends_through_the_openai_compatible_route_with_the_configured_base` 移植到 `tests/unit/runtime/test_model_client.py`。

**`tests/integration/as2/test_agent_react_validation.py` → `tests/integration/runtime/test_react_loop.py`**

| 用例 | 处理 |
| --- | --- |
| `test_free_text_never_becomes_a_tool_call`、`test_invalid_json_is_not_repaired`、`test_unknown_tool_gets_bounded_feedback_and_self_correction`、`test_invalid_json_uses_paired_tool_feedback`、`test_unpairable_tool_calls_use_host_user_feedback`、`test_mixed_batch_is_rejected_without_partial_execution`、`test_loop_exhaustion_is_fatal`、`test_protocol_errors_consume_exact_global_turn_budget`、`test_transport_failure_is_fatal_without_protocol_feedback`、`test_cancellation_is_fatal_without_protocol_feedback`、`test_response_evidence_failure_is_fatal_without_new_model_call`、`test_unserializable_response_is_fatal_without_protocol_feedback`、`test_tool_execution_failure_is_fatal_without_new_model_call` | 同名移植 |
| `test_native_finish_accepts_empty_memory_list`、`test_native_finish_rejects_non_list_memory`、`test_finish_mixed_with_action_is_rejected_atomically`、`test_ask_mode_prompt_requires_finish`、`test_invalid_finish_memory_is_fatal` | 删除：`finish` 工具与提问模式随只读问答删除（裁定 7） |

**`tests/integration/as2/test_worldmock_life_hooks.py` 的 `:151-:326`**

| 用例 | 处理 |
| --- | --- |
| `test_structured_observation_appears_once_in_user_message`、`test_reasoning_content_survives_into_followup_request`、`test_reasoning_content_survives_protocol_feedback_request`、`test_assistant_message_without_reasoning_omits_key` | 同名移植到 `tests/integration/runtime/test_react_loop.py` |
| `test_string_observation_keeps_single_text_copy` | 删除：观察恒为结构化，包装与拆包没有了 |
| `test_prelude_decision_statement_disabled_by_default`、`test_prelude_decision_statement_hook_runs_one_toolless_call`、`test_prelude_decision_statement_rejects_tools_and_empty_content`、`test_prelude_decision_statement_transport_failure_propagates` | 删除：决策自述前奏随 AgentSociety2 的无效功能删除 |
| `test_step_finish_with_empty_memories_is_legal`、`test_person_finish_schema_allows_empty_memories` | 删除：AgentSociety2 人物的 `finish` 结构没有了；空记忆的步尾保存由第 05 章 `test_first_step_save_has_no_previous_view_digest_and_writes_no_template` 覆盖 |

**`tests/integration/as2/test_worldmock_registration.py`**：`test_actual_scanner_accepts_life_env_with_the_complete_direct_environment_contract`、`test_scan_diagnostic_rejects_an_incomplete_direct_environment_contract` 删除（扫描注册与直接环境合同都没有了）。

### 导入对照

| 旧导入 | 新写法 |
| --- | --- |
| `agentsociety2.society` 的 `DecisionContext`、`DirectToolCall`、`SchedulingState` | `worldmock.state.decisions.round` 的同名类型 |
| `agentsociety2.society` 的 `SocietyCheckpointRequest`、`AgentStepBatchRequest`、`AgentSociety`、`AgentSocietyConfig`；`agentsociety2.society.society`；`agentsociety2.agent.runner` | 删除；用例改经 `RunHost`（第 01 章、【T53】【T56】）或引擎方法 |
| `agentsociety2.agent.base` 的 `ModelCallEvidence` | `worldmock.state.model.exchange.ModelCallEvidence`（第 03 章） |
| `agentsociety2.agent.base` 的 `MemoryUpdateEvidence`；`agentsociety2.agent.base.react.MemoryUpdateEvidence` | `worldmock.recording_engine.events.memory_evidence.MemoryUpdateEvidence`（第 05 章；第 10 章「跨章裁定」），构造时不再给 `state_sha256` 与 `consolidated` |
| `agentsociety2.agent.base` 的 `ReactDecision`、`ReactToolResult` | `worldmock.agents.protocol.react_loop` 的同名类型（构造参数按第 03 章「测试里旧入口的对应」） |
| `agentsociety2.agent.base` 的 `ReactLoopRequest`、`AgentBase`；`agentsociety2.agent.base.agent_runtime`；`agentsociety2.agent.person_prompt` | 删除；调用按第 03 章「测试里旧入口的对应」改写 |
| `agentsociety2.agent.memory_runtime` 的 `MemoryRuntimeConfig`、`MemoryStepOutcome`、`PersonMemoryRuntime`；`agentsociety2.agent.memory.AgentMemoryStore` | `worldmock.agents.memory.life_memory` 的 `MemoryStepOutcome`、`save_step` 与 `worldmock.agents.memory.life_memory_store.WorldMockMemoryStore`（第 05 章）；`MemoryRuntimeConfig`、`PersonMemoryRuntime` 的用法删除 |
| `agentsociety2.agent.person.PersonAgent`、`agentsociety2.agent.PersonAgent` | 删除；比较「LifeAgent 不覆写 PersonAgent」的断言随之删除 |
| `agentsociety2.config`（打桩 `build_client_for_role`）、`agentsociety2.config.llm_dispatcher`（`LLMClient`、`_build_router`、`init_dispatchers`、`close_cached_async_clients`、`time`、`asyncio`）、`agentsociety2.config.llm_dispatcher.LLMDispatchError` | `worldmock.configuration.model.model_client` 的 `ModelClient`、`LLMDispatchError` 与模块属性；打桩点改为 `runner._model_client`（第 01 章【H19】） |
| `agentsociety2.config.config.Config` | 删除（进程配置没有了） |
| `agentsociety2.env.direct_env_actor`、`agentsociety2.env.router_direct`、`agentsociety2.env.router_base`、`agentsociety2.registry`、`agentsociety2.backend.services.custom.*`、`agentsociety2.agent.service_proxy` | 删除 |
| `custom.agents.life_agent`（含 `from custom.agents import life_agent`） | `worldmock.agents.life_agent` |
| `custom.envs.life_env` 的 `LifeEnv`、`_STABLE_TOOL_SURFACE` | 删除；见【T35】【T55】 |
| `ray`、`ray.cloudpickle`、`ray.exceptions` | 删除 |
| `tests.unit.as2.test_llm_dispatcher_transport` 的 `_client`、`_error` | `tests.unit.runtime.model_client_fixtures` 的 `scripted_client`、`provider_error` |
| `tests.integration.as2_fixtures.write_project` | `tests.integration.runtime_fixtures.write_project`（第 07 章）；`use_fake_as2` 等其余名字的用法删除 |

WorldMock 自己的模块里搬走或删掉的名字，测试按下表改导入（规则在各章，这里只汇总导入写法）：

| 旧导入 | 新写法 |
| --- | --- |
| `worldmock.runtime.lifecycle.runner` 的 `settle_pending_day_plans`、`settle_pending_memory_digests`、`PlanDayClient`、`MemoryDigestClient`、`_call_plan_day`、`_call_memory_digest` | `worldmock.runtime.lifecycle.midnight` 的同名对象（第 01 章）；`settle_pending_*` 的 `client` 必填，含义按第 01 章与第 10 章「跨章裁定」 |
| `worldmock.runtime.lifecycle.runner` 的 `_society_specs` | `_creation_options`（第 01 章） |
| `worldmock.runtime.lifecycle.runner` 的 `_restored_specs`、`_restore_agents`、`_validate_agent_workspace` | 删除；用例按第 05 章改写或删除 |
| `worldmock.runtime.framework.read_query` 的 `LifeEnvActor`、`LifeEnvProxy`、`life_env_actor_class` | 删除；引擎一侧的只读查询是 `LifeEngine.read_query`（第 02 章） |
| `worldmock.runtime.framework.setup` 的 `_configure_process`、`_prepend_pythonpath`、`require_locked_as2`、`AS2_LOCKED_SOURCE_SHA256` | 删除；`runtime_code_sha256()` 不带参数，项目根经 `project_root()`（第 07 章） |
| `worldmock.agents.prompt.life_context` 的 `persona_card_text` | 删除，模块随之删除（第 03 章） |
| `worldmock.agents.protocol.life_feedback` 的 `HostFeedback`、`feedback_report`、`feedback_content` | `ProtocolFeedback`、`report_content(x.report)`（第 03 章「测试里旧入口的对应」） |
| `worldmock.agents.prompt.life_prompt` 的 `READONLY_SYSTEM_LAYER`、`readonly_messages`、`empty_memory_layer`；`worldmock.agents.prompt.life_persona` 的 `PROMPT_SNAPSHOT_FILE`、`write_prompt_snapshot`、`readonly_layers` | 删除；`empty_memory_layer()` 改写 `assemble_layer(None, {}, [])`，`system_message()` 不带参数（第 03 章） |
| `worldmock.agents.memory.life_memory` 的 `WorldMockMemoryRuntime` | `MemoryStep`、`save_step` 与 `worldmock.agents.memory.workspace` 的工作区函数（第 05 章） |

决策轮类型 27 条导入的去向：

| 位置 | 去向 |
| --- | --- |
| `tests/life/conftest.py:20`、`:31`、`:295`、`:455`；`tests/life/environment_runtime.py:5` | 改导入（两个枢纽，下游 148 与 109 个文件不动） |
| `tests/integration/evaluation/test_workload_metrics.py:25`、`tests/integration/life/test_action_identity.py:10`、`tests/integration/life/test_committed_world_guard.py:9`、`tests/integration/life/test_environment_runtime.py:10`、`tests/integration/life/test_opslog.py:10`、`tests/integration/life/test_sleep_runtime.py:107`、`tests/integration/life/test_today_entries.py:15`、`tests/integration/runtime/test_conclude_memories.py:33`（只留 `DirectToolCall`）、`tests/integration/runtime/test_life_agent_protocol.py:35`（只留 `DecisionContext`、`DirectToolCall`、`SchedulingState`）、`tests/integration/runtime/test_memory_search_tool.py:22`、`tests/integration/runtime/test_same_second_continuation.py:35`、`tests/integration/start_interfaces/test_start_group.py:6`、`tests/unit/agent_protocol/test_decision_context_protocol.py:25`、`tests/unit/life/test_current_lifecycle_coverage.py:11` | 改导入 |
| `tests/integration/life/test_memory_digest_runtime.py:679` | 删除；用例按第 01 章【H23】那一行改写 |
| `tests/integration/runtime/test_env_lifecycle_coverage.py:33`；`tests/integration/as2/test_agent_react_validation.py:14`、`tests/integration/as2/test_worldmock_life_hooks.py:24`、`tests/unit/as2/test_decision_round.py:8`、`tests/unit/as2/test_direct_router.py:9`、`tests/unit/as2/test_direct_runner_failures.py:9`、`tests/unit/as2/test_society_decision_round.py:10` | 随文件删除 |

`custom.*` 的导入（AST 扫描 47 条语句、33 个文件，另有替身源码字符串里的 1 条）：随文件删除的 5 条在 `tests/integration/as2/test_worldmock_life_hooks.py:20`、`tests/integration/as2/test_worldmock_registration.py:8`、`tests/integration/runtime/test_env_lifecycle_coverage.py:29`、`:30`、`:34`。改为 `worldmock.agents.life_agent` 的 25 条：`tests/integration/host_run_fixtures.py:15`、`tests/integration/life_host_fixtures.py:11`（随【T38】删除）、`tests/integration/runtime/current_contract_fixtures.py:107`、`tests/integration/runtime/test_agent_branch_coverage.py:17`、`tests/integration/runtime/test_conclude_memories.py:34`、`tests/integration/runtime/test_decision_context_roundtrip.py:19`、`tests/integration/runtime/test_grounded_evidence.py:18`、`tests/integration/runtime/test_host_feedback.py:25`、`tests/integration/runtime/test_life_agent_protocol.py:36`、`tests/integration/runtime/test_memory_retirement.py:21`、`tests/integration/runtime/test_memory_search_tool.py:23`、`tests/integration/runtime/test_output_truncation.py:26`、`tests/integration/runtime/test_persona_layer_card_only.py:68`、`tests/integration/runtime/test_persona_profile.py:292`、`tests/integration/runtime/test_same_second_continuation.py:37`、`tests/integration/runtime/test_seven_layer_request.py:12`、`:42`、`:74`、`tests/integration/runtime/test_verified_memory.py:20`、`tests/unit/agent_protocol/test_activity_schema.py:20`、`tests/unit/agent_protocol/test_decision_context_protocol.py:26`、`:28`、`tests/unit/agent_protocol/test_tool_description_references.py:21`（`ReadonlyFinishRequest` 随第 03 章删除，用例按第 03 章改）、`tests/unit/runtime/test_model_identity.py:28`、`tests/unit/start_interfaces/test_persona_and_memory_schemas.py:9`。删除的 18 条 `custom.envs.life_env`（含替身源码字符串里的一条）：`tests/integration/host_run_fixtures.py:16`、`tests/integration/life/test_memory_digest_runtime.py:680`、`tests/integration/life_host_fixtures.py:12`、`tests/integration/runtime/current_contract_fixtures.py:118`、`tests/integration/runtime/native_runtime_fixtures.py:101`、`tests/integration/runtime/test_conclude_memories.py:36`、`tests/integration/runtime/test_decision_context_roundtrip.py:20`、`tests/integration/runtime/test_life_agent_protocol.py:364`、`tests/integration/runtime/test_memory_retirement.py:22`、`tests/integration/runtime/test_public_runtime_boundaries.py:189`、`:205`、`tests/integration/runtime/test_read_query_runtime.py:28`、`tests/integration/runtime/test_runtime.py:383`、`tests/integration/runtime/test_runtime_inputs_contract.py:38`、`tests/integration/runtime/test_same_second_continuation.py:39`、`tests/system/cli/test_cli_clean_process.py:41`（替身源码字符串）、`:89`、`tests/system/lifecycle/test_run_manifest.py:9`。断言模块名的字符串 `test_life_agent_protocol.py:1621` 与 `test_read_query_runtime.py:307` 的 `"custom.agents.life_agent"` 改为 `"worldmock.agents.life_agent"`。

假凭据的 23 个文件（每个删去顶部的 `os.environ.setdefault("AGENTSOCIETY_…", …)` 一到两行；`tests/conftest.py` 另按【T29】）：`tests/conftest.py`、`tests/life/conftest.py`、`tests/system/lifecycle/test_sleep_runs.py`、`tests/unit/agent_protocol/test_activity_schema.py`、`tests/unit/agent_protocol/test_search_memory_request.py`、`tests/unit/agent_protocol/test_tool_description_references.py`，以及 `tests/integration/runtime/` 下的 `test_agent_branch_coverage.py`、`test_conclude_memories.py`、`test_env_lifecycle_coverage.py`（随文件删除）、`test_grounded_evidence.py`、`test_host_branch_coverage.py`、`test_host_failure_stages.py`、`test_host_feedback.py`、`test_life_agent_protocol.py`、`test_llm_dispatcher_multikey.py`（随文件删除）、`test_memory_retirement.py`、`test_memory_search_tool.py`、`test_output_truncation.py`、`test_persona_profile.py`、`test_read_query_runtime.py`、`test_runtime.py`、`test_same_second_continuation.py`、`test_verified_memory.py`。另有同类写法：`tests/integration/runtime/current_contract_fixtures.py:49-71`（【T37】）、`tests/integration/runtime/native_runtime_fixtures.py:136`（【T39】）、`tests/integration/life/test_state_serialization.py:86`（【T44】）、`tests/integration/runtime/test_conclude_memories.py:742`、`tests/integration/runtime/test_life_agent_protocol.py:1524`（随【T53】【T56】改写的用例删去）、`tests/integration/runtime/test_host_branch_coverage.py:303`（第 01 章删去的用例里）、`tests/mutation_runner.py:160-161`（第 07 章【I16】）、`tests/system/cli/test_cli_clean_process.py:1`、`:27`、`:54-63`（第 07 章【I5】）。

经 `person()`/`decide()` 夹具驱动人物的 31 个文件（导入 `decide`、`person`、`ScriptedModel`、`aligned_process` 或 `environment` 中至少一个）：只用 `decide` 与 `ToolResponse` 的 18 个文件不因夹具改写而改代码，`test_inspect_current_activity.py`、`test_current_runtime_contract.py`、`test_dayrun_parallel_agent.py` 三处读第三项的断言按【T37】改——`tests/integration/evaluation/test_memory_pilot_export.py`、`tests/integration/footprints/test_inspect_current_activity.py`、`tests/integration/life/test_carried_records.py`、`tests/integration/life/test_pause_limit.py`、`tests/integration/runtime/` 下的 `test_acs_engine_wave.py`、`test_current_runtime_contract.py`、`test_dayrun_parallel_agent.py`、`test_finished_activity_prompt.py`、`test_host_feedback.py`、`test_interaction_menu_roundtrip.py`、`test_observation_agent_seam.py`、`test_order_app_refusal.py`、`test_query_host_flow.py`、`test_seven_layer_request.py`、`test_unique_presence_run.py`，`tests/integration/start_interfaces/test_location_activities.py`、`test_sleep_request.py`，`tests/system/lifecycle/test_run_viewer_recording.py`；用 `person`/`environment`/`ScriptedModel` 的 8 个文件把 `environment(engine)` 换成引擎本身、`ScriptedModel` 的桩按【T37】——`tests/integration/runtime/test_existing_relationship_subject.py`、`test_memory_search_tool.py`、`test_model_exchange_attempts.py`、`test_persona_layer_card_only.py`、`test_errand_prompt_separation.py`，`tests/integration/start_interfaces/test_purchase_prefer.py`，`tests/unit/agent_protocol/test_grounded_targets.py`，以及随文件删除的 `test_llm_client_close.py`；用 `aligned_process` 的 5 个文件删去这一调用——`tests/integration/runtime/native_runtime_fixtures.py`、`test_input_warnings.py`、`test_runtime.py`、`test_runtime_inputs_contract.py`，`tests/system/cli/test_cli_clean_process.py`。

工作区夹具 `write_agent_workspace` 的 23 个下游：直接调用的 17 个文件（`tests/integration/life/test_grocery_cooking.py`、`test_location_meal.py`、`test_memory_digest_runtime.py`，`tests/integration/runtime/test_env_lifecycle_coverage.py`（随文件删除）、`test_errand_checkpoint.py`、`test_host_branch_coverage.py`、`test_runtime.py`、`test_runtime_inputs_contract.py`、`test_same_second_continuation.py`，`tests/integration/start_interfaces/test_purchase_prefer.py`，`tests/life/day_baseline_runtime.py`，`tests/system/cli/test_cli_clean_process.py`，`tests/system/lifecycle/test_dayrun_complete_days.py`、`test_external_environment_lifecycle.py`、`test_parcel_arrival_export.py`、`test_sleep_runs.py`，`tests/unit/life/test_counterfactual_rejection_contract.py`）与经 `branch_of` 的 6 个文件（`tests/integration/life/test_memory_layer_assembly.py`、`test_phone_record_resume.py`、`test_processed_actions.py`、`test_state_serialization.py`，`tests/system/lifecycle/test_purchase_resume.py`、`test_restore_equals_live.py`）。只有带 `agent_document` 的三处调用改代码（【T41】）。

`RunRequest`/`ResumeRequest`/`CounterfactualRequest` 按第 07 章去掉项目根参数的 11 个文件：`tests/integration/life/test_creation_model_identity.py`、`tests/integration/runtime/` 下的 `test_frozen_metadata_resume.py`、`test_host_branch_coverage.py`、`test_input_warnings.py`、`test_native_dataset_lifecycle.py`、`test_persona_profile.py`、`test_public_runtime_boundaries.py`、`test_runtime.py`、`test_runtime_inputs_contract.py`、`test_transport_park_run.py`，`tests/system/cli/test_cli_clean_process.py`。

### 其余受影响的测试文件

上面各表之外，还要改的测试文件与改动归属：

| 文件 | 本章的改动 | 别章驱动的改动 |
| --- | --- | --- |
| `tests/dataset_acceptance.py` | 【T47】【T49】 | — |
| `tests/unit/engineering/test_recorded_response_run.py` | 见「测试」 | — |
| `tests/unit/engineering/test_compare_complete_days.py` | 见「测试」 | — |
| `tests/unit/engineering/test_compare_runs.py` | 见「测试」 | — |
| `tests/system/viewer/run_viewer.test.cjs`、`run_viewer_files.test.cjs`、`run_viewer_identity.test.cjs` | `:1266`、`:94`、`:141` 的 `directoryHandle('as2', [directoryHandle('agents', […])])` 改为 `directoryHandle('agents', […])`；`run_viewer.test.cjs` 加【T23】的用例 | — |
| `tests/system/lifecycle/test_run_viewer_recording.py` | 加【T51】的键次序断言 | — |
| `tests/integration/life/test_memory_digest_runtime.py` | `:41` 改导入（第 04 章那一行） | `:679-697` 按第 01 章【H23】；客户端接缝按第 04 章 |
| `tests/integration/runtime/test_model_exchange_attempts.py`、`test_network_ops_records.py`、`test_transport_park_run.py`、`tests/unit/engineering/test_shared_model_clients.py` | 改导入与夹具（【T31】【T39】） | 断言按第 04 章 |
| `tests/integration/runtime/test_native_dataset_lifecycle.py` | 【T43】 | `_configure_process` 计数的删除按第 07 章 |
| `tests/integration/life/test_state_serialization.py` | 【T44】 | — |
| `tests/integration/environment/test_domain_dependencies.py`、`tests/unit/engineering/test_bootstrap.py:316-320`、`tests/system/cli/test_cli.py`、`tests/unit/evaluation/test_memory_pilot.py` | 【T45】 | `test_bootstrap.py` 的其余改动按第 07 章 |
| `tests/integration/runtime/test_host_branch_coverage.py` | 删 `as2_fixtures` 导入、删假凭据 | 【T33】所列两章 |
| `tests/integration/runtime/test_runtime.py` | 改导入、删假凭据、`:397-398` 按【T55】 | AgentSociety2 身份用例按第 07 章删除，执行流程用例按第 01 章 |
| `tests/integration/runtime/test_runtime_inputs_contract.py` | 改导入；`prepared_parent` 按【T55】 | 进程配置次序用例按第 07 章删除；改卡片后恢复被拒的用例按第 05 章改写 |
| `tests/system/cli/test_cli_clean_process.py` | 改导入；替身与父运行按【T55】 | `_STARTUP_FAILURES`、替身证据、用例名里的 `agentsociety_variables` 按第 07 章【I5】 |
| `tests/system/lifecycle/test_run_manifest.py` | 【T55】 | — |
| `tests/integration/runtime/test_persona_profile.py` | 改导入、删假凭据；`:215` 不再读 `AGENT.json` | 名字取自 `persona.toml` 的相等测试按第 03 章；卡片核对用例按第 05 章 |
| `tests/integration/runtime/test_life_agent_protocol.py` | 改导入、删假凭据、【T53】 | 只读问答、按对象身份配对、PersonAgent 钩子的用例按第 03 章；记忆证据用例（`test_native_memory_update_hashes_real_workspace_files`、`test_native_memory_update_records_real_subjective_merge_without_duplicate`、`test_person_step_records_conclusion_memory_update`）按第 05 章的测试表；故障文本按第 06 章；`test_memory_persistence_failure_aborts_society_before_commit` 删去 `empty` 参数化、保留用例名 |
| `tests/integration/runtime/test_conclude_memories.py` | 改导入、删假凭据、【T56】 | 记忆写入按第 05 章的测试表（含 `:670`、`:716` 两例）；`test_conclusion_memories_flatten_by_task_with_system_source` 按第 03 章改名为 `test_conclusion_memories_flatten_by_task_in_the_stored_shape` 并改为存储形状 |
| `tests/integration/runtime/test_same_second_continuation.py`、`test_output_truncation.py` | 改导入、删假凭据、【T54】 | 回合协议按第 03 章 |
| `tests/integration/runtime/test_errand_prompt_separation.py` | 删 `current_contract_fixtures.environment` 的导入与调用，经【T54】改写后的 `_workspace_person`、`_step` 直接传引擎 | — |
| `tests/integration/runtime/test_decision_context_roundtrip.py`、`test_agent_branch_coverage.py`、`test_existing_relationship_subject.py`、`test_grounded_evidence.py`、`test_host_failure_stages.py`、`test_host_feedback.py`、`test_memory_search_tool.py`、`test_persona_layer_card_only.py`、`test_seven_layer_request.py`，`tests/integration/start_interfaces/test_purchase_prefer.py`，`tests/unit/agent_protocol/test_activity_schema.py`、`test_decision_context_protocol.py`、`test_grounded_targets.py`、`test_search_memory_request.py`、`test_tool_description_references.py`，`tests/unit/start_interfaces/test_batch_shapes.py`、`test_system_layer_text.py`，`tests/unit/start_interfaces/test_persona_and_memory_schemas.py` | 改导入、删假凭据、换掉 `LifeEnv` 句柄（第 02 章的替换表）、桩模型改为 `call(request)` | 回合协议、`HostFeedback` 删除、只读问答删除、请求字典六键按第 03、04 章；`persona_layer` 改收结构化卡片按第 03 章，连同第 10 章「跨章裁定」补上的 `test_decision_context_protocol.py:195`、`test_persona_and_memory_schemas.py:24`；`test_host_failure_stages.py` 的停机原因文本按第 06 章 |
| `tests/unit/profile/test_textgen.py` | — | `:156` 的 `persona_layer({"persona_card_struct": persona_card_struct(card)}, "林舟")` 改为 `persona_layer(persona_card_struct(card), "林舟")`（第 03 章签名，第 10 章「跨章裁定」） |
| `tests/integration/runtime/test_memory_retirement.py` | 改导入、删假凭据；模块文档串 `:3`、`:5` 与 `:50` 的文档串不再点名 `LifeEnv`；`test_life_channel_offers_only_read_query`（`:125-130`）改为只核对 `LifeEngine`：没有 `due_reflection`、`read_query` 可调用（两个转发类与 `LifeEnv` 已删，导入随之删去）；比较 `PersonAgent` 的 `:144` 随第 05 章删去的 `test_retired_memory_paths_are_gone` 一起消失 | `memory_updated` 键集、工作区文件集、记忆运行时删除按第 05 章 |
| `tests/integration/runtime/test_verified_memory.py`，`tests/unit/footprints/test_branch_coverage.py`、`test_quality.py`，`tests/unit/life/test_current_lifecycle_coverage.py`，`tests/system/lifecycle/test_sleep_runs.py` | 改导入、删假凭据 | `memory_updated` 键集、工作区文件集、记忆运行时删除按第 05 章 |
| `tests/integration/life/test_opslog.py`、`test_today_entries.py`，`tests/unit/life/test_model_evidence_pairing.py` | 改导入（导入对照表） | 证据类型按第 03 章 |
| `tests/integration/runtime/test_public_runtime_boundaries.py`、`tests/integration/environment/test_validation_execution.py`，`tests/unit/life/test_memory_digest_output.py`、`tests/unit/runtime/test_model_identity.py` | 改导入；`test_public_runtime_boundaries.py:189`、`:205` 按第 02 章的替换表 | 客户端构造点、零点与回顾请求的底稿键按第 04 章 |
| `tests/integration/life/test_creation_model_identity.py`、`tests/integration/runtime/test_frozen_metadata_resume.py`、`tests/integration/runtime/test_input_warnings.py`、`tests/integration/runtime/test_grounded_evidence.py` | 删 `as2_fixtures` 导入、删 `aligned_process` | 请求类型与代码身份按第 07 章 |
| `tests/unit/footprints/test_status_liveness.py` | — | 第 06 章 |
| `tests/integration/life/test_day_attribution.py`、`tests/integration/evaluation/test_memory_pilot_export.py`、`tests/integration/runtime/test_order_app_refusal.py`、`tests/integration/life/test_environment_runtime.py`、`tests/integration/start_interfaces/test_start_group.py`、`tests/integration/life/test_action_identity.py`、`tests/integration/life/test_committed_world_guard.py`、`tests/integration/life/test_sleep_runtime.py`、`tests/integration/evaluation/test_workload_metrics.py`、`tests/integration/environment/test_domain_dependencies.py` | 文档串与注释里的 `AS2`、`Ray`、`LifeEnv` 等改写成新对象的名字（【T27】与第 02 章【E1】） | — |

扫描命中但不改的文件：`tests/integration/footprints/test_branch_lineage.py`（`:166` 的 `Agent decision failed` 只是样例取值，不与生产文本比较）、`tests/unit/footprints/test_workspace_snapshot.py`（通用快照测试的样例文件名）、`tests/integration/runtime/test_strict_endpoint.py`、`tests/unit/life/test_environment_contract.py`、`tests/unit/test_environment_inputs.py`（`transport_max_retries` 是配置字段，配置不变）；`tests/integration/life/test_day_plan_arrangement.py`、`tests/integration/life/test_memory_search_sources.py`（经 `settle_midnight_tasks` 传返回字典的桩，包装之后结果不变，见第 10 章「跨章裁定」）；`tests/unit/profile/test_models.py`（用的是 `persona_card_struct`，不是 `persona_layer`）；`tests/integration/evaluation/test_errand_metrics.py`、`tests/unit/evaluation/memory_pilot_fixtures.py`、`tests/unit/footprints/test_post_run_view.py`、`tests/unit/life/test_persisted_lifecycle_shape.py`（`"reflection"` 是主观记忆的类型或调用类的名字，不是删掉的 `memory_updated` 键）；`tests/integration/footprints/test_acs_invariants.py`（用例名里的 `current_facts` 与删掉的 `_current_facts` 无关）。

## 设计文档

全文由[「设计文档与手册」](./08-design-docs.md)写出，本章只列必须陈述的事实：

| 位置（基线） | 改后必须说的事 |
| --- | --- |
| `docs/design/01-architecture.md:276` 掩码完整日比较一行 | 每一侧用自己根目录引导出来的虚拟环境录制，子进程只从这一侧的 `src` 与根目录导入；两侧依赖锁可以不同 |
| `docs/design/01-architecture.md:277` 录音应答运行一行 | 子进程用运行工具的解释器，只从被测检出的 `src` 与根目录导入；同一份工具可重放任一检出，重放基线时用基线工作树自己的解释器运行它 |
| `docs/design/05-recording-engine.md:763`「壁钟」 | 去掉「人物工作区 AGENT.json 的 `initialized_at`」 |
| `docs/design/05-recording-engine.md:766`「运行位置与代码身份」 | 去掉「人物工作区里的模型服务地址」与「人物工作区 AGENT.json 的 `workspace.root`」，只剩运行清单里的地址与三项代码身份 |
| `docs/design/05-recording-engine.md:757`「身份」 | 不变：`MEMORY.md` 已核验经历行的来源事件身份映射到事件序号，`memory_sha256`、`memory_before_sha256` 按首次出现成双射 |
| `docs/design/05-recording-engine.md:768`「记录形式」 | 加一句：去掉 AgentSociety2 时声明的四条规则（`workspace-members`、`memory-updated-form`、`model-request-parameters`、`prompt-fingerprint`）对应裁定 3、4、6、7 的格式变更，只改写基一侧（提示指纹为数值规则），只用于这次新旧对比，是本条「记忆写入与模型请求不能重建」的唯一例外：模型请求只许去掉从未发出的三个参数，记忆写入只许对齐删去的三个键、条目编号的写法、首条的模板摘要与条目文件摘要的出现次序 |
| `docs/design/01-architecture.md:262-269` | 不变：两份基线按双候选规程重冻；查看器样例在请求字典改变时重录 |

`docs/manual/runtime.md:76-79` 不变：查看器仍要同时拖入运行目录才能显示人物的 `MEMORY.md`。

## 测试

规则到测试（「新」为新写，「改」为改写，其余为不改的既有测试）：

| 规则 | 测试 |
| --- | --- |
| 【T1】 | 新 `tests/unit/engineering/test_recorded_response_run.py::test_a_project_without_the_removed_framework_source_is_accepted`（没有 `vendor` 目录的项目目录，`main(["run", …])` 不因源码位置拒绝，日志里没有源码位置的拒绝文本，走到录音带与输入复原）；删 `::test_project_without_restored_as2_is_refused`、`::test_as2_location_follows_the_child_pythonpath` |
| 【T2】【T4】 | 改 `tests/unit/engineering/test_recorded_response_run.py::test_child_environment_drops_credentials_and_proxies`：父环境去掉 `ray_address`，加 `https_proxy`（小写）与 `RAY_ADDRESS`；断言三个代理名两种写法都被去掉、`RAY_ADDRESS` 原样传入，`PYTHONPATH` 恰为 `[<根>/src, <根>]` |
| 【T3】 | 新 `tests/unit/engineering/test_recorded_response_run.py::test_child_command_lines_name_no_project_root`：第一段与续跑段的 `_segment_argv` 分别等于【T3】的两条参数表（用现有两段来源夹具）；用 monkeypatch 截获 `_child_command`，`finish` 与 `export_source` 的命令分别等于【T3】的两条命令；四条都不含 `--project-root` |
| 【T5】 | 既有 `tests/system/lifecycle/test_recorded_response_run.py::test_recorded_response_run_reproduces_a_restarted_recording` 在新分支上零偏差；基线一侧的重放由实施一章在基线工作树上执行 |
| 【T6】 | 既有 `tests/unit/engineering/test_recorded_response_run.py::test_one_shot_uuid_returns_the_run_id_once` |
| 【T7】 | 删 `tests/unit/engineering/test_compare_complete_days.py::test_dependency_lock_mismatch_is_refused`；新 `::test_different_dependency_locks_are_compared`（两侧 `uv.lock` 不同，结论 `equal`） |
| 【T8】 | 新 `::test_root_without_its_own_environment_is_refused`（头一侧缺解释器文件，退出码 2，日志含 `has no bootstrapped environment at`）；新 `::test_each_side_records_with_its_own_environment_python`（不替换 `record_side`，monkeypatch `subprocess.run` 记下参数并写出签名文件；两侧命令首项分别为 `str(environment_python(<根>/.venv))`，`env["PYTHONPATH"]` 恰为 `[<根>/src, <根>]`） |
| 【T9】 | 删 `::test_root_whose_as2_resolves_elsewhere_is_refused`；改 `_root`：不建 AgentSociety2 目录，建 `environment_python(<根>/.venv)` 处的空文件；`roots` 不再替换 `as2_location`；既有 `::test_root_without_recorder_is_refused` |
| 【T10】 | 既有 `::test_equal_signatures_give_equal`、`::test_different_signatures_without_rules_give_different`、`::test_different_signatures_equal_under_rules`、`::test_an_unexpected_failure_exits_2_not_as_different`；既有 `tests/system/lifecycle/test_complete_day_behaviour.py::test_complete_day_behaviour_is_equal_for_one_checkout` |
| 【T11】 | 改 `tests/unit/engineering/test_compare_runs.py` 的 `_workspace(memory=…, episodes=…)`：快照成员为 `MEMORY.md` 与 `memory/episodes.jsonl`；改 `_relocated`：删 `:406` 一行；删 `::test_agent_json_initialized_at_is_masked_inside_workspace_snapshots`、`::test_agent_json_workspace_root_is_masked_only_at_its_path`；改 `::test_gzip_workspace_snapshots_on_both_sides_are_compared_by_their_members_without_a_rule`（删前半段壁钟屏蔽，留成员内容不同报为不同）；新 `::test_agent_json_is_an_ordinary_member_without_the_rule`（两侧 `AGENT.json` 的 `initialized_at` 不同即报为不同） |
| 【T12】【T22】 | 新 `::test_migration_rules_are_declared_on_their_targets`（四条规则的种类、范围、路径、章节字段与读取者检索等于【T12】表，都在 `ALLOWED_RULE_TARGETS` 里，`parse_rules` 接受它们，`prompt-fingerprint` 不带值时拒绝）；既有 `::test_a_rule_on_decision_inputs_is_refused` |
| 【T13】【T14】【T16】 | 新 `::test_workspace_members_rule_rebuilds_the_base_workspace_into_the_two_kept_files`（基一侧六个成员：渲染后 CRLF 的 `MEMORY.md`、带十五字段与 `m_00000000_000001` 的 CRLF 条目文件、四个框架文件；头一侧同内容的 LF 两文件；带规则相等、`applied` 为基一侧快照数，不带规则不同）；新 `::test_workspace_members_rule_keeps_entry_text_and_line_endings_strict`（条目正文差一字、`tick` 为 1、头一侧条目文件为 CRLF、头一侧缺结尾换行，各报为不同；一行不是 JSON 对象时退出码 2 且日志为 `Workspace episodes line 1 is not a JSON object`） |
| 【T15】 | 同上两条；另 `::test_empty_episode_files_and_the_template_are_absent_on_both_sides`（基一侧英文模板 CRLF 的 `MEMORY.md` 加空条目文件与 `AGENT.json`、头一侧空目录，带规则相等） |
| 【T17】 | 新 `::test_memory_updated_form_rule_aligns_keys_ids_and_the_template_digest`；新 `::test_memory_updated_form_rule_nulls_only_the_template_digest`（基一侧 `memory_before_sha256` 是别的摘要时不改，报为不同；`consolidated` 为 `true` 时不删，报为不同） |
| 【T18】 | 新 `::test_memory_updated_form_rule_maps_episodes_digests_by_first_appearance`（两侧摘要不同但出现次序一致时相等；头一侧在第二条重复第一条摘要而基一侧不重复时不同；不带规则时不同） |
| 【T19】 | 新 `::test_model_request_parameters_rule_drops_only_the_three_parameters_from_the_base`（带规则相等；不带规则不同；头一侧还带 `stream` 时不同；基一侧别的键不同时不同） |
| 【T20】 | 新 `::test_prompt_fingerprint_rule_accepts_only_the_declared_values`（运行清单与导出清单都对齐，检查点 `manifest_sha256` 随之相等；声明值不符时不同；不带规则时不同） |
| 【T21】 | 既有 `::test_code_identity_and_model_urls_are_masked_only_at_their_paths`（`_relocated` 改后断言主体不变） |
| 【T23】 | 新 Node 用例 `a memory update summary reads the operation and episode counts only`（`tests/system/viewer/run_viewer.test.cjs`，事件带 `consolidated: true`，`view.summary(1)` 恰为 `记忆更新 · 追加记忆片段 · 2 段记忆`），经 `tests/system/viewer/test_run_viewer.py::test_run_viewer_recorded_names` 运行 |
| 【T24】【T25】 | `tests/system/viewer/test_run_viewer.py::test_run_viewer_recorded_names`（三个 `.cjs` 文件改后的拖入用例）；新 Node 用例 `context descriptions name the current modules`（`tests/system/viewer/run_viewer_context.test.cjs`：经 `parser().api.rules` 读，`tools`、`message`、`memory_view` 三项的 `source`、`memory_view.fill` 与 `memory_view.steps` 的第一、三项等于【T24】表）；新 `tests/unit/engineering/test_framework_removed_from_tests.py::test_no_test_script_or_tool_file_names_the_removed_framework` |
| 【T26】 | 新 `tests/unit/engineering/test_framework_removed_from_tests.py::test_no_test_script_or_tool_module_imports_the_removed_framework`（`tests/`、`scripts/`、`tools/`）；`src/` 一侧是第 07 章的 `tests/unit/engineering/test_framework_removal.py::test_production_source_never_imports_the_removed_framework_or_custom` |
| 【T27】 | 新 `tests/unit/engineering/test_framework_removed_from_tests.py::test_no_test_script_or_tool_file_names_the_removed_framework` |
| 【T28】 | 新 `tests/unit/engineering/test_framework_removed_from_tests.py::test_the_framework_only_test_files_are_gone` |
| 【T29】 | 新 `tests/unit/engineering/test_framework_removed_from_tests.py::test_the_test_session_sets_only_the_local_cost_map`（`tests/conftest.py` 的 `os.environ.setdefault` 调用恰为一处，键为 `LITELLM_LOCAL_MODEL_COST_MAP`，用 `ast` 读）；第 07 章【I5】的名字守卫 |
| 【T30】 | 本章移植表里每个「移植」行的新测试（`tests/unit/runtime/test_model_client.py`、`tests/integration/runtime/test_react_loop.py`）；各章表里的新测试 |
| 【T31】 | 改 `tests/integration/runtime/test_model_exchange_attempts.py`、`test_network_ops_records.py`、`test_transport_park_run.py::test_parked_run_settles_like_an_uninterrupted_run`（停放等待序列 `[2, 4, 8, 16, 32, 64]` 与失败发送四字段的断言不变）、`tests/unit/engineering/test_shared_model_clients.py` |
| 【T32】【T33】 | 各章表里的测试 |
| 【T34】【T35】【T36】 | 【T26】的导入守卫与第 07 章 `src/` 一侧的导入守卫；`tests/unit/engineering/test_engine_facade_boundary.py::test_life_env_and_its_forwarders_are_gone`（第 02 章，`LifeEnv` 与 `custom.envs` 不在 `tests/` 里）；全量测试能收集 |
| 【T37】【T38】 | 改动的枢纽经 31 个下游文件全部通过；改 `test_inspect_current_activity.py::test_inspector_accepted_and_rejected_lines_read_the_current_fields`、`test_current_runtime_contract.py` 两条、`test_dayrun_parallel_agent.py::test_native_parallel_walk_and_app_interaction_use_both_actual_slots` 的第三项断言；既有 `tests/system/lifecycle/test_run_viewer_recording.py::test_viewer_fixture_is_recorded_from_the_current_host`；`host_run` 的下游 `test_memory_retirement.py`、`test_host_failure_stages.py`、`test_sleep_runs.py`、`test_inspect_current_activity.py` |
| 【T39】 | 改 `tests/integration/runtime/test_native_dataset_lifecycle.py` 两条、`test_transport_park_run.py` 两条、`test_persona_profile.py`、`test_frozen_metadata_resume.py` 用 `native_runtime` 的用例 |
| 【T40】 | 第 02 章的 `test_start_group.py` 用例；下游 109、148 个文件 |
| 【T41】 | 既有 `tests/integration/runtime/test_errand_checkpoint.py::test_checkpoint_restores_identical_errands_and_todo_without_renoting`、`tests/system/lifecycle/test_restore_equals_live.py::test_a_branch_at_every_step_matches_the_live_world_and_observation` |
| 【T42】 | 既有 `tests/unit/engineering/test_dayrun_baseline.py` 八条与 `tests/system/lifecycle/test_complete_day_scripts.py` 两条 |
| 【T43】 | 改 `tests/integration/runtime/test_native_dataset_lifecycle.py::test_native_dataset_run_resume_and_replay_finish_with_real_frozen_workspaces`、`::test_receipts_count_every_persisted_model_response_including_the_day_plan` |
| 【T44】 | 改 `tests/integration/life/test_state_serialization.py::test_readonly_commit_is_identical_across_python_hash_seeds` |
| 【T45】 | 改 `tests/integration/environment/test_domain_dependencies.py::test_domain_checks_do_not_load_the_complete_engine`、`tests/unit/engineering/test_bootstrap.py::test_component_probe_keeps_production_font_import_outside_parent_process`、改名 `tests/system/cli/test_cli.py::test_pure_cli_and_runtime_imports_do_not_load_engine_or_model_client` 与 `::test_real_pytest_collection_of_pure_commands_does_not_load_engine_or_model_client`、改 `tests/unit/evaluation/test_memory_pilot.py::test_memory_pilot_imports_no_runtime_code` |
| 【T46】 | 第 07 章【I14】【I15】的测试；改 `tests/unit/engineering/test_import_graph.py::test_production_import_graph_obeys_all_boundaries` |
| 【T47】 | 既有 `tests/system/mutations/test_source_mutations.py::test_source_mutations_have_unique_compilable_anchors_and_existing_killers` |
| 【T48】 | 第 07 章【I16】的测试 |
| 【T49】 | 下面的变异表 |
| 【T50】 | 重冻后的 `tests/system/lifecycle/test_complete_day_baseline.py` 两组参数化用例 |
| 【T51】 | 改 `tests/system/lifecycle/test_run_viewer_recording.py::test_viewer_fixture_is_recorded_from_the_current_host`（加 `list(recorded["action"]) == list(recorded["continuation"]) == list(REQUEST_KEYS)` 与 `list(requests["action"]) == list(REQUEST_KEYS)`） |
| 【T52】 | 实施一章的放行记录：重冻与重录之后的分片全量不带 `--deselect` |
| 【T53】 | 改 `tests/integration/runtime/test_life_agent_protocol.py` 全部留下的用例（断言主体按【T53】的对应改写），其中 `::test_memory_persistence_failure_aborts_society_before_commit` 经真实引擎与运行宿主 |
| 【T54】 | 改 `tests/integration/runtime/test_same_second_continuation.py` 与 `tests/integration/runtime/test_output_truncation.py` 全部用例，其中 `test_same_second_continuation.py::test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte` 经改写后的 `_workspace_person` 与 `_step`；改 `tests/integration/runtime/test_errand_prompt_separation.py::test_errand_arguments_never_become_memory_or_progress_parameters` |
| 【T55】 | 改 `tests/system/cli/test_cli_clean_process.py` 四条、`tests/system/lifecycle/test_run_manifest.py::test_native_environment_manifest_contains_full_declared_service_identity`、`tests/integration/runtime/test_runtime.py::test_run_manifest_records_git_commit_and_dirty_flag`、`tests/integration/runtime/test_runtime_inputs_contract.py` 用 `prepared_parent` 的用例 |
| 【T56】 | 改 `tests/integration/runtime/test_conclude_memories.py` 全部留下的用例 |

新登记的源码变异（登记在 `tests/dataset_acceptance.py` 末尾，锚点原文由实施者从真实源码里选，在符号内唯一）：

| 变异 | 符号 | 替换破坏了什么 | 杀手 |
| --- | --- | --- | --- |
| `base_workspace_keeps_framework_members` | `scripts.compare_runs::_workspace_member` | 规则下基一侧不再丢掉四个框架成员（`BASE_WORKSPACE_ONLY` 的判断恒为假），基线与新分支的工作区快照永远报成不同 | `tests/unit/engineering/test_compare_runs.py::test_workspace_members_rule_rebuilds_the_base_workspace_into_the_two_kept_files` |
| `memory_before_nulled_for_every_digest` | `scripts.compare_runs::align_memory_update` | 基一侧任何 `memory_before_sha256` 都被改成 `null`（模板摘要的判断换成「不为空」），真实的视图变化被抹掉 | `tests/unit/engineering/test_compare_runs.py::test_memory_updated_form_rule_nulls_only_the_template_digest` |
| `side_records_with_the_tool_interpreter` | `scripts.compare_complete_days::record_side` | 录制改回用运行工具的解释器（`side_python(root)` 换成 `Path(sys.executable)`），两侧不再各用自己的虚拟环境 | `tests/unit/engineering/test_compare_complete_days.py::test_each_side_records_with_its_own_environment_python` |

## 不在本章

| 内容 | 归属 |
| --- | --- |
| 运行宿主、零点任务模块、三种启动顺序，`test_run_host.py` 与 `test_decision_round_types.py` 的用例与名字，Society 与 LifeEnv 生命周期测试的后继表，`host_factory` 夹具 | [「运行宿主与决策轮」](./01-run-host.md) |
| 引擎只读查询、提交返回全部结果、工具面一致性的规则与 `test_tool_surface.py`、`test_read_query_runtime.py` 的改写，测试里 LifeEnv 调用的替换表 | [「去掉 LifeEnv」](./02-engine-facade.md) |
| `LifeAgent` 的构造参数与入口、请求字典的六个键、只读问答的删除、证据类型、`test_react_loop.py` 的替身与本章之外的用例、测试里旧入口的对应表 | [「人物、回合循环与只读问答的删除」](./03-agent-loop.md) |
| `ModelClient` 的规则、`model_client_fixtures.py` 的内容、移植用例随规则改动的断言、线路探针与故障注入 | [「模型客户端与模型请求底稿」](./04-model-client.md) |
| 人物工作区的文件集、`MEMORY.md` 与条目文件的写法、`memory_updated` 的新键集、恢复时的校验、记忆类 AgentSociety2 测试的移植 | [「人物工作区、记忆文件与「记忆已更新」」](./05-workspace-memory.md) |
| 停机原因文本、`__reduce__` 与 Ray 原因解包的删除及其测试 | [「故障文本」](./06-failure-text.md) |
| 引导、代码身份函数的新签名、依赖锁、`--project-root` 在命令行与请求类型里的删除、`fresh_check`、CI、门禁的扫描根、导入守卫、进程环境变量名守卫、变异执行器 | [「安装、代码身份、依赖与许可」](./07-install-identity.md) |
| 设计文档与手册的全文 | [「设计文档与手册」](./08-design-docs.md) |
| 录音重跑、`raw-order`、运行比对、掩码完整日比较的实际运行、证据层与放行 | [「实施、验收证据与完成判据」](./10-implementation.md) |
| `tests/fixtures` 里没有使用者的五个夹具文件 | 不动：提案没有把它们列进范围 |

## 规格阶段的决定

| 事项 | 决定 | 理由 |
| --- | --- | --- |
| （问题）录音重跑工具还要不要剥掉 `RAY_ADDRESS` | 不剥，`_routing_name` 只剩三个代理变量 | 新分支没有 Ray，AGENTS.md 要求删掉过时代码；基线重放由实施一章在不设 `RAY_ADDRESS` 的环境里进行 |
| 录音重跑子进程用哪个解释器 | 仍用运行工具的解释器；基线重放用基线工作树的解释器运行新工具 | 提案只要求去掉源码检查和 `--project-root`；基线 `.venv` 里的可编辑安装让 `agentsociety2` 不靠 `PYTHONPATH` 也解析到基线自己的源码 |
| 掩码完整日比较怎样「各用自己的虚拟环境」 | 解释器取 `environment_python(<根>/.venv)`，复用引导脚本已有的函数；缺失即拒绝 | `.venv` 是 `scripts/bootstrap.py` 唯一的产物位置（`scripts/bootstrap.py:24`），不新增参数 |
| 掩码完整日比较的 `PYTHONPATH` | 继续从录音重跑工具导入 `project_pythonpath` | 「只导入这一检出的 src 与根目录」是一条规则，放一处 |
| 四条规则的名字、种类与目标 | 见【T12】表 | 每类记录一条规则，范围与路径落在实际改写的记录上；提示指纹用已有的数值规则机制，只接受声明的两个值 |
| 四条规则的章节字段 | 只写本规格的章号 `05`、`05`、`04`、`03` | 与既有规则只写章号的写法一致；不写目录名，因为【T27】不许脚本里出现框架包名 |
| 英文模板怎样认 | 用 LF、CRLF 两种写法的摘要常量 `BASE_MEMORY_TEMPLATE_SHA256` | 比较工具只用标准库，不能导入框架；摘要比照抄模板正文更窄、更准 |
| 空条目文件与模板 `MEMORY.md` | 工作区快照里两侧都不列空条目文件；基一侧的模板 `MEMORY.md` 不列 | 新建运行的人物快照在头一侧是空目录，基一侧带模板与空文件；两者都不承载事实 |
| 条目文件怎样比较 | 两侧逐行写成规范 JSON 再取摘要；头一侧含 `\r` 或缺结尾换行时按原始字节 | 与运行比对按值比较的口径一致；键序由第 05 章的写法与测试负责，行尾 LF 由这里守住 |
| `episodes_sha256` 怎样比较 | 规则下按首次出现成双射，新种类 `episodes-digest`；不给空摘要或 `null` 设特别的占位 | 与 `memory_sha256` 的既有做法相同，比直接屏蔽更严；第 05 章定下条目文件在第一次步尾保存时总会写出，两侧在同样的事件处都是真实文件的摘要，空文件的摘要两侧相同，双射本身就对齐 |
| 代码身份要不要新规则 | 不要，沿用既有屏蔽与摘要登记 | 既有 `MANIFEST_MASKS` 与清单摘要登记已覆盖检查点的 `manifest_sha256`；工作区快照摘要由 `workspace-members` 覆盖 |
| 查看器的「已巩固」 | 删掉读取和显示 | 键已删除，恒为假；裁定 4 |
| 查看器上下文说明的来源与「主观归纳」 | 按【T24】改三处来源与记忆视图的说明 | 旧说明指向 `custom/`、AgentSociety2 文件与已删除的 `build_context` 和主观归纳；第 05 章把这处改动交给本章 |
| 移植用例落在哪些文件、谁定名字 | 各章自己的规则测试与后继表定名字；本章只管其余各章不管的文件（调度器四个文件、回合校验、生活钩子的 `:151-:326`、扫描注册）；同名移植，只在名字点名已删机制时改名 | 一条规则与它的测试写在同一章；沿用原名便于对照，断言主体不改（提案第十章第一行风险的应对） |
| `decide()` 跑多大范围 | 调第 03 章的 `run_turns`，只跑回合循环，不做步尾保存；人物的记忆存储传 `None` | 与今天 `run_react_loop` 加 `_enable_memory=False` 的范围相同，31 个下游的断言主体不变；完整决策由 `host_run_fixtures` 与运行宿主测试覆盖 |
| `decide()` 的第三项 | 改为登记的工具名列表，三处读它的断言随之改 | 今天的字符串 `"<工具> registered"` 由已删除的 `_run_react_turn` 生成，新代码里没有它；在测试里重拼这句旧文字就是模仿已删格式 |
| `life_host_fixtures.py` 里没人调用的四个名字 | 删除，只留 `TruncatedResponse` | 它们导入 `custom` 与框架，今天没有一处调用；AGENTS.md 要求删掉过时代码，而不是改写死代码 |
| 测试里「建新运行」的写法 | `runtime_fixtures.new_run(prepared, client)` 一处，测试进程里的四个调用点都用它；子进程替身只能导入 `src`，直接写同样的三步 | 今天各处各自经 `LifeEnv.init` 建运行；新建的启动顺序归运行宿主，测试只写一次 |
| 新建运行时传什么客户端 | `OfflineClient`：收到请求就失败 | 今天这五处都不发模型请求；有请求就是测试环境变了，当场失败而不是去联网 |
| `native_runtime` 的替换点 | `runner._model_client` 与 `run_host.LifeEngine`；`MODEL_FACTORY` 以 `prepared` 调用 | 第 01 章把 `_model_client` 定为运行装配里构造模型客户端的唯一一处；脚本模型要读世界，引擎由宿主建好后才有 |
| 断网重发用例的客户端 | `failing_client`：真实 `ModelClient`，替换 `litellm.acompletion`，线路请求记在 `wire_requests`，去掉不可序列化的 `client` 参数 | 停放与计数必须走真实客户端；`client` 是网络客户端对象，两次运行各不相同，也进不了规范 JSON |
| 协议测试与续接测试里的人物 | 用 `dataclasses.replace` 在 `person_inputs` 的结果上换名字、编号与回合上限 | 今天这些测试用「Alice」、编号 7、回合上限 3 或 4；卡片仍来自夹具 `persona.toml`，冻结输入没有第二种入口 |
| 经 `AgentSociety` 驱动的用例 | 改为 `_step_host` 把记录型假环境挂成运行宿主的引擎，`await host.run()`；假环境补上宿主读取的 `context.state` 与 `decisions.round` | 运行宿主取代 Society；同一个假环境照旧记下提交、中止、登记与检查点，断言主体不变；第 01 章的多人物次序测试用的也是这种记录型假引擎 |
| 读回合循环的消息历史 | 测试辅助 `turns_with_history` 显式建 `ReactLoop` 并交还 `loop._history` | 今天的历史挂在人物对象上（`_react_messages`）；新代码的历史属于每次决策的回合循环，人物不保存它，生产代码不为测试另开出口 |
| `tests/conftest.py` 的 `LITELLM_LOCAL_MODEL_COST_MAP` | 保留 | 测试会在导入模型客户端之前直接导入 litellm 的异常类型，这一行让离线测试不去联网 |
| 纯加载边界换成哪些模块 | 删去两个已删除的模块名，加 `worldmock.runtime.lifecycle.run_host` 与 `worldmock.configuration.model.model_client`，四处测试同一写法 | 运行宿主接替 LifeEnv 成为唯一的引擎门面使用者，模型客户端接替被前缀断言守着的框架调度器；第 01 章据此让 runner 在函数内导入它们 |
| （问题）测试与脚本里描述旧机制的文档串要不要一起改，并用守卫钉住 | 改，【T27】的守卫覆盖 `tests/`、`scripts/`、`tools/`（`tools/analysis/` 归第 07 章【I26】） | 旧说法会把读者引向不存在的对象；涉及的文件几乎都在迁移范围里；与第 02 章【E1】、第 07 章【I5】【I13】【I26】各管一段，不重复 |
| `test_cli.py` 两个用例名里的 `or_as2` | 改名为 `or_model_client` | 断言内容随之改变，名字跟着说真话 |
| 变异台账里随生产符号删除的变异 | 直接删除，不另找替身；`step_end_copies_every_memory_again` 按第 05 章删除 | 被变异的分支已不存在；新行为的变异由各章自己登记（每章至多三个） |
| `env_close_leaves_cached_clients` | 改名并改指 `ModelClient.close` | 「运行结束关闭网络客户端」这条行为仍在，只是换了主人（提案第九章「变异台账改指」）；第 04 章的规则测试就是杀手 |
| （问题）既有规则 `workspace-gzip` 要不要删 | 不动 | 它对齐的是更早的一次格式变化，与本次无关；提案没有把它列进范围 |
| 重冻与重录的时点 | 全部生产改动之后、在最终树上各做一次 | 基线绑定代码身份，任何生产改动都会让它失效；先重冻就要再冻一次 |
| 重冻与重录之前去掉哪些用例 | 只去掉完整日基线比对与查看器样例比对两条 | 查看器的 Node 用例只把样例当数据读，旧样例照样通过；去掉整条 Node 用例会连带去掉【T23】的新用例 |
| 工作区夹具的内容 | `write_agent_workspace(workspace)` 只建空目录 | 等于新建运行在第一次决策之前的人物快照；`AGENT.json` 与 `config.json` 已不存在 |
| 变异改指跟谁 | 【T47】每一行的处理跟随写那段代码的章 | 符号归写代码的那一章，杀手也在那一章 |
| 查看器样例的键序断言 | 等于 `list(REQUEST_KEYS)`，从 `model_client` 导入 | 键序由第 03、04 章定，测试里不写死次序 |
| `tests/`、`scripts/`、`tools/` 的导入守卫放在哪 | 本章新文件 `test_framework_removed_from_tests.py` 的 `test_no_test_script_or_tool_module_imports_the_removed_framework`（第 10 章「跨章裁定」定名），`src/` 一侧仍是第 07 章的守卫 | 第 07 章与本章原先互相指向，规则没有测试；【T27】的文字守卫管不到 `mem0`、`mcp` |
| `_step` 的返回值 | 登记的工具名列表；步骤照第 03 章 `decide` 的观察、`run_turns`、`_record_memory` 三步 | `decide` 不返回登记的工具；今天的字符串 `"<工具> registered"` 由已删代码生成，与【T37】第三项同一种改法 |
| 经工作区建人物的辅助 | 每次从工作区读回已有条目建 `WorldMockMemoryStore`（`read_episodes`），回合上限 8 | 与今天「此后每次决策都从工作区重建」同一语义，条目编号接着上一次决策 |
| `test_life_channel_offers_only_read_query` | 改为只核对 `LifeEngine` 没有 `due_reflection`、有 `read_query` | 被核对的转发类与 `LifeEnv` 已删除；退役断言对引擎仍成立 |
| `run-viewer-context.js:94` 的 `life_prompt.py` 路径 | 改成实际路径 `src/worldmock/agents/prompt/life_prompt.py` | 同一行的来源本来就要改；指向不存在的文件是假话 |
