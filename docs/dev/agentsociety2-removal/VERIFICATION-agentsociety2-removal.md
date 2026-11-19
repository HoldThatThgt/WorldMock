# 去掉 AgentSociety2 与 Ray 的验证记录

> 状态：**验收记录**。证据层为「离线加录音重跑」；项目整体仍为 NOT-COMPLETE。
> 日期：2026-11-17
> 依据：[去掉 AgentSociety2 的开发规格](SPEC-agentsociety2-removal.md)（各章在 `spec/`）与[负责人裁定](RULING-2026-11-17-owner.md)。
> 本记录只登记证据，不另定行为。机器报告与回执不入库，留在基础检出的 `.work/agentsociety2-removal-impl/`（下称 `$R`）
> 与 `.work/agentsociety2-removal/`（下称 `$ONE`）。

## 结论

WorldMock 已不再依赖 AgentSociety2 与 Ray：决策循环（`RunHost`）、人物回合（`LifeAgent`）与模型调用（`ModelClient`）
都由 WorldMock 自己的代码承担，一次运行是一个进程、一个事件循环；`custom/`、`patches/`、`vendor/agentsociety2` 与
75 个只为框架而装的包都已删除。同一份计费录音在基线与新分支上各重跑一遍，两边零偏差，发出的原始请求正文连键序逐字节相同；
运行比对与掩码完整日比较只在规格声明的四条规则下相等。

证据层是「离线加录音重跑」：离线测试、门禁、源码变异与录音重跑都在本机取得，没有新的真实长运行。项目整体仍按三项真实验收
标为 NOT-COMPLETE：正式 12 周 DeepSeek 运行、三次隔离盲评、一次完整自然日的交付都还没有做。

| 提交 | 内容 |
| --- | --- |
| `8a7720bb` | 冻结规格（十章） |
| `9962fe8f` | 设计文档：WorldMock 自有的决策循环、模型客户端与人物工作区 |
| `ed053590` | 实施：去掉 AgentSociety2 与 Ray（十一条轨道并行落下，加登记轨道） |
| `8e3c1a51` | 修正：一轮评审的 48 条发现 |
| `ed05ff27` | 重冻两份完整日基线、离线重录查看器样例 |
| `95fb62b8` | 变异套件未变异基线子进程的上限改为 2400 秒 |

## 基线与录音

| 项 | 值 |
| --- | --- |
| 基线提交 | `74f1b949`（`main`），基线工作树 `.work/wt/as2-base`，自己的 `.venv` |
| 基线回执 | `$R/baseline/receipt.json`：导入的 AgentSociety2 源码摘要 `0a7b9b06…`，`worldmock` 来自基线工作树 |
| 数据集提交 | WorldMock-Dataset `330b6065` |
| 录音窗口 | `2026-10-08T00:00:00+08:00` 到 `2026-10-10T00:00:00+08:00` |
| 第一段 | `run_id` `1ecc8f7a495848229ba26144c3487ffe`；模拟时间到 `2026-10-08T22:30:00+08:00` 时强行停下驱动进程树（计划内重启）；79 条 `model_request`、78 条 `model_response` |
| 重启检查点 | `dc1dcb9421a2446784986cf887dcb0f7`（事件序号 1231） |
| 续跑段 | `run_id` `02efad8c0de04e4bb558bba680ba1762`，`completed`；库里连同续接前的事件共 2,737 条事件、166 条 `model_response` |
| 调用与费用 | 两段合计 166 次应答；人物回合按仓内价目表估算约 0.172 美元（第一段 0.076，续跑段新增 0.096），零点计划与回顾 5 次调用不在该估算内 |
| 旧工具基线重跑 | `$R/recording/replay-base-oldtool/report.json`：零偏差，重启重现 1 次 |

## 证据

全部在最终代码上取得（代码身份 `426b8887b799182c22a476ad3d296285923cbc48bd2c4a40a6dd31eb0e5b0457`）。修正改了 `runner.py` 与 `scripts/compare_runs.py`，所以按规格把证据表前四行在最终代码上重跑了一遍（`-2` 后缀）；首轮证据在实施提交上（代码身份 `6af42d62…`）结论相同。

| 证据 | 结论 | 回执 |
| --- | --- | --- |
| 同一份录音在基线与新分支上各重跑一遍 | 两边 `verdict` 都是 `zero_divergence`，各 166 次调用，`restarts_reproduced` 为 1，`divergences` 为空，导出核验 `passed` | `$R/replay/base-2/`、`$R/replay/head-2/` |
| 两次重跑的原始正文连键序逐字节相同 | `equal`，比较 167 次，`mismatches` 为空 | `$R/evidence/raw-order-2.json` |
| 运行比对只在声明的规则下相等 | `equal`：2,737 条事件、421 个检查点（逐个比较）、13 份素材、10 个导出文件都相同；规则只用到四条：`workspace-members` 159 次、`memory-updated-form` 114 次、`model-request-parameters` 166 次、`prompt-fingerprint` 3 次（`1947c112…` 对 `cd454d1b…`） | `$R/evidence/compare-runs-2.json` |
| 掩码完整日比较 | `equal_under_rules`：activity 与 callback 两种完整日的签名都只在规则覆盖的素材摘要与检查点摘要处不同，各自的运行比对 `equal`；`complete-day-behaviour.json` 的 SHA-256 为 `76611774f2993081de7f1c373af647b7bc39b21aa6cbc6babe3b794f22846783`（与首轮相同） | `$R/evidence/complete-days-2/` |
| 线路探针的固定样例与故障注入 | 两份回执 `verdict` 都是 `equal`；仓内 `tests/integration/runtime/test_model_client_wire.py` 在终门里通过 | `$ONE/probe/wire-receipt.json`、`fault-receipt.json` |
| 去掉的包找不到、没有导入 | `agentsociety2`、`ray`、`mem0`、`mcp` 四个都找不到；终门里十三个守卫用例都通过 | `$R/evidence/not-importable.txt`、`$R/environment/head-receipt.txt` |
| 锁差异 | `passed`；`uv.lock`、`requirements/lock.txt`、`requirements/build-lock.txt` 计数为 60、59、1（`tzdata` 保留，见取舍第一行） | `$ONE/lock-check.json` |
| 离线各层与独立变异评审 | 见「门禁」与「评审」 | `$R/gates/final/`、`$R/review/mutation-probes.json` |

探针回执里两处差异按规格 04 章的两条决定接受，不作判据：

- 请求头：五种请求各有 16 个请求头名只差字母大小写（`Host`、`Accept-Encoding`、`Connection`、`Authorization`、`Accept`、
  `Content-Type`、`User-Agent`、`Content-Length` 与八个 `X-Stainless-*`），值与次序都相同；请求体与序列化结果逐字节相同。
- 连接被拒：第 10 次尝试的错误链尾部由 `ConnectError <- ClientConnectorError <- ConnectionRefusedError` 变为
  `ConnectError <- ConnectError`；失败分类、发送次数、等待序列与 `failed_after_ms`（486,000）都不变，其余八种场景逐字相同。

## 门禁

全量测试在实施提交 `ed053590` 上：6,671 例，失败 16、错误 313、跳过 0；分支覆盖率 96.17%，
未覆盖的公共接口 1 个。313 个错误同一个原因（变异套件未变异基线子进程的 480 秒上限），16 个失败归为 8 个根因，交给评审。

终门在提交 `95fb62b8` 上运行（工作树干净），`documentation_gate`、`build_design_doc`、`tests`、`coverage_gate` 四项为 true、true、true、true；JUnit 共 6685 例，失败 0、错误 0、跳过 0；分支覆盖率 96.21%（门槛 85.0），未覆盖的公共接口 0 个。单独重跑才通过的只有带墙钟上限的用例：`tests/integration/environment/test_test_collection.py::test_fixed_directory_collection_resolves_environment_fixtures`。变异台账恰 313 条，终门里 `tests/system/mutations/test_dataset_acceptance.py` 的 313 例全部通过（每条变异都被行为断言杀死）。
规格各章「测试」表里标「新」的用例展开成 273 个节点，在最终提交上全部收集到（`$R/new-tests.log`）。
没变的号仍是原值：运行清单 7、导出 8、世界 15、质量报告 10、规则 6、目录格式 6、`behavior-metrics-4`、`environment-1`；
`life_tool_schema_sha256()` 为 `aa0ed7fa…`，`life_prompt_sha256()` 为 `cd454d1b…`；`src/` 里没有 `AS2_LOCKED_SOURCE_SHA256`（`$R/versions.txt`）。

## 评审

全量测试与首轮证据之后做了唯一的一轮评审，八个视角并行，只读；共 48 条发现（其中 8 条归集成者）。

| 视角 | 范围 | 结论 |
| --- | --- | --- |
| 规格一致、不多做 A | 01 运行宿主、02 引擎门面、06 失败文字 | 生产代码逐条符合；1 条：H8 用例用了规格没点名、且会被整点规则拒绝的窗口 |
| 规格一致、不多做 B | 03 回合循环、05 工作区与记忆 | 生产代码逐条符合，三个指纹与规格一致；1 条：一个测试辅助没有打开回合工具面 |
| 规格一致、不多做 C | 04 模型客户端、07 安装与身份 | 两章按规格实现，没有多出的辅助或垫片；5 条，集中在线路用例的错误正文与日志记录器 |
| 规格一致、不多做 D | 08 设计文档、09 比较工具与测试迁移 | 08 章逐字一致；09 章 4 条：`workspace-members` 的计数口径与两个存活探针 |
| 正确性 | 全量测试的失败与错误 | 8 个根因；把 313 条变异直接跑了一遍，310 条被杀死，另 3 条的杀手不是以断言失败 |
| 不落下 | 测试表、台账与守卫 | 结构完整；指出 3 条变异的杀手不算数、变异套件从未执行 |
| 独立变异评审（01–04 章） | 33 个手写探针 | 24 个被杀死，9 个存活 |
| 独立变异评审（05、06、07、09 章） | 50 个手写探针 | 43 个被杀死，7 个存活 |

修正一次改完（提交 `8e3c1a51`，17 个文件，只有 `runner.py` 一处生产改动：人物输入的导入挪到准入与父运行核对之后）：
每条发现由所属轨道改自己的文件，16 个存活探针各补了能杀死它的行为测试。修正之后、提交之前，集成者把 16 个探针按变异套件的判定
重跑一遍，16 个都被杀死；把杀手或被变异文件在修正里改过的 63 条台账变异逐条单独跑，63 条都被杀死（`$R/review/mutations/`）。

## 来源核对

`$ONE/provenance.json` 对上游源码（摘要 `5adf4d10…`）只命中一处：`src/worldmock/resource_engine/catalog/identity.py:3`
对 `agentsociety2/agent/base/skill_hook_context.py:12`，是标准导入块，本分支没有改这个文件，复核为不是派生（`$R/review/provenance-review.md`）。
最终的 `UPSTREAM_DERIVED_MODULES` 是五个文件：`src/worldmock/agents/protocol/react_loop.py`、`src/worldmock/state/model/exchange.py`、
`src/worldmock/configuration/model/model_client.py`、`src/worldmock/agents/memory/workspace.py`、`src/worldmock/agents/memory/life_memory_store.py`，
只有它们在来源注明里点名框架，许可证文件随之保留。

## 证明不了的

- 真实网络下的停放、慢流与 `Retry-After`。
- httpx 传输在真实服务上的表现；合并后的短试跑只给一次短运行的传输层证据。
- 单个事件循环连跑数周的资源行为。
- Linux 平台上代码身份是否一致：GitHub Actions 每次都是 `startup_failure`，持续集成这一层未取得。
- 录音没走到的路径：月回顾、年回顾、传输失败、停放、多于一次的续跑与反事实重放。

## 实施阶段的问题与取舍

实施无人值守，遇到的问题都按「取推荐做法」定下，一行一条。先是集成者的决定，再是各轨道在落下阶段（A 开头）与修正阶段（F 开头）
的决定；轨道决定的英文原文在 `$R/review/apply-decisions.json` 与 `$R/review/review-stage-result.json`。

### 集成者

| # | 问题 | 取的做法 | 理由 |
| --- | --- | --- | --- |
| I01 | 规格 07 章【I18】–【I20】与 10 章【V24】把 `tzdata` 列为要删的包（计数 59、58、1），但 `src/` 下 34 个模块用 `zoneinfo`，Windows 上 `zoneinfo` 只能从 `tzdata` 包读时区库；第一次全量测试约 1,000 例以 `ZoneInfoNotFoundError` 失败。 | 把 `tzdata==2026.4` 声明为第六个运行依赖，从基线锁重算，`tzdata` 条目与基线逐字节相同；计数变为 60、59、1；`lock_check.py` 允许 `tzdata` 两行并从删除集合里去掉它（规格原版留作 `lock_check.spec-original.py`）；`test_framework_removal.py` 断言六个依赖声明与 75 个删除的名字。并入尚未推送的实施提交（改写为 `ed053590`）。 | 终门必须在这台 Windows 机器上通过；规格的删除清单来自依赖图，漏看了 `tzdata` 在为 WorldMock 自己的代码提供平台能力。版本与哈希与基线相同，不需要下载。 |
| I02 | `uv lock` 读到负责人的 `uv.toml`（默认索引为清华镜像），改写了每个来源地址。 | `uv lock`、`uv export` 一律加 `--no-config`；把单元素 `build` 数组恢复成上一次序列化用的多行写法（语义相同，`uv lock --no-config --locked` 通过）。 | 锁要逐字节保留基线的 `pypi.org` 来源（锁差异核对的「保留的包不变」）。 |
| I03 | `make_head_env.sh` 因被删的文件在实施提交之前仍在索引里而拒绝删除 `custom/`。 | 改为检查磁盘上的文件，不看索引条目。 | 各轨道已从磁盘删掉这些文件，只剩空目录。 |
| I04 | 第一次全量测试无效（缺 `tzdata` 的环境故障，不是代码故障）。 | 修好 `tzdata` 之后把那一次全量测试重跑一次；无效的一次留在 `$R/gates/full-tzdata-missing/` 备查。 | 37 个模块收集失败的结果给不了评审任何依据。 |
| I05 | 来源核对命中一处：`resource_engine/catalog/identity.py:3` 对上游 `skill_hook_context.py:12`。 | 复核为不是派生：标准导入块，本分支没改这个文件（`$R/review/provenance-review.md`）。 | 按 07 章【I25】人工复核。 |
| I06 | 负责人要求实施只用一个工作流、只做一轮评审与修正，而 10 章把全量测试、探针与首轮证据放在落下与评审之间。 | 实施只跑一个工作流：先跑落下阶段，集成者在工作流之外做【V10】【V15】–【V19】，再以评审阶段续跑同一个工作流（落下阶段的代理从缓存重放）。 | 既只有一个工作流，又让唯一的评审看得到全量测试与证据的结果，符合 10 章的次序。 |
| I07 | `test_freeze_rrs_baseline` 的一例在全量测试里失败，因为 `.work/environment-receipt.json` 还是加 `tzdata` 之前的锁摘要。 | 改写提交之后用 `write_receipt(Path('.').resolve(), 'release')` 重写回执。 | 这是环境产物，不是代码；回执必须描述当前的锁。 |
| I08 | 全量测试的门禁脚本因失败过多（329 例）跳过了单独重跑，而其中 313 例同一个原因。 | 集成者手工做重跑一步：其余 16 个失败加一例变异用例，放在一个单独的 pytest 进程里（`$R/gates/rerun-isolated/`）。 | 在唯一的评审之前把负载造成的超时与真缺陷分开，这正是门禁重跑规则的用意。 |
| I09 | 04 章给 `_local_server` 的缺省错误正文含 `rate_limit`，在 litellm 1.89.2 按文字映射异常的规则下，与同章「故障场景的期望值」一表矛盾，仓内线路用例因此失败 6 例。 | 夹具保持规格原文；线路用例对每个非 200 应答显式传入探针服务的中性正文 `probe failure`，与【V15】冻结结果的探针服务一致。 | 不改规格写明的夹具，也让仓内用例与期望值表的来源一致；改动最小。 |
| I10 | 04 章【M23】要求断言每份 `plan_day` 草稿 `tuple(request) == REQUEST_KEYS`，但草稿是从 SQLite 读回的规范 JSON（键已排序）。 | 用例断言 `sorted(drafts[0]) == sorted(REQUEST_KEYS)`；构造次序由 `test_plan_day_request_has_exactly_the_six_draft_keys` 与 `test_request_caps_output_and_disables_thinking` 钉住。 | 事件按 `sort_keys=True` 写库（`foundation/serialization.py`），规格写的断言不可能成立。 |
| I11 | 09 章【T55】假定四处新建运行用 `new_run(prepared, OfflineClient())` 不发模型请求，但 01 章【H21】【H26】让 `RunHost.start_new` 在初始检查点后补齐缺失的日计划，`OfflineClient` 会报错。 | 这四处同时把零点业务客户端 `worldmock.runtime.lifecycle.midnight.PlanDayClient` 换成桩，接缝与【T39】已用的相同；断言不变。 | 保住 `OfflineClient` 与【T55】的断言；去掉桩会让 `start_new` 失败。 |
| I12 | 03 章把旧入口 `prepare_react_loop` 只对应到 `_prepare`，而基线方法同时打开了工具面；只做校验、不跑回合的测试辅助因此拿到空的 `_turn_models`。 | `test_life_agent_protocol.py` 的 `_prepared` 与 `test_purchase_prefer.py` 的 `_host` 在 `_prepare` 之后再调用 `turn_tools(...)`。 | `_validate` 与 `_dispatch` 读 `_turn_models`，只有 `turn_tools` 写它；断言主体不变。 |
| I13 | `tools/analysis/memory_categories.py` 在 Windows 上以 CRLF 写表，它的测试失败，覆盖率门报 `main` 未覆盖；基线 `74f1b949` 上同样失败。 | 写表改为 `write_text(text, encoding="utf-8", newline="\n")`。 | 这是挡住终门的基线 Windows 缺陷，一行修好，也清掉唯一的未覆盖接口。 |
| I14 | `test_fixed_directory_collection_resolves_environment_fixtures` 在 8 路分片负载下碰到 60 秒子进程上限，单独跑 22 秒通过。 | 不改代码，由终门的单独重跑确认。 | 这是带墙钟上限的用例，【V28】允许单独重跑。 |
| I15 | 修正为存活探针补的行为测试（例如 `test_counted_transport_waits_are_two_times_two_to_the_n`、`test_retry_after_date_without_a_zone_is_read_as_utc`、`test_nested_collectors_restore_the_outer_one`）不在规格各章的测试表里。 | 不改冻结的规格，在本记录登记；这些用例都钉在规格已有的条款上。 | 规格冻结后的变化不回写；它们只加强对既有条款的检验，不加行为。 |
| I16 | 评审时台账从未真正执行过，修正之后的杀手是否咬人要到终门才知道，而修正只有一轮。 | 修正提交之前，把 16 个存活探针连同补上的杀手按变异套件的判定重跑，把杀手或被变异文件改过的 63 条台账变异逐条单独跑。 | 在唯一的修正轮里就发现杀手不咬人；结果 16 个探针、63 条变异全部被杀死。 |
| I17 | 变异套件的未变异基线子进程在 10 章写为 480 秒（`:16`），修正时改为 1200 秒；之后实测 359 个杀手节点在负载下要 1,127 秒，而终门在四个变异分片里同时各跑一次。 | 提交 `95fb62b8` 把这一个上限改为 2400 秒，每条变异的 180 秒上限不变；10 章 `:36` 的描述行保持原文。 | 这是超时保护，不是行为规则；上限太紧会让终门在负载下误报，整轮重跑代价大。 |
| I18 | 最终代码上的证据复跑第一次，基线一侧续跑段的 Ray 节点启动超时（`The current node timed out during startup`），当时机器上同时在测变异基线耗时。 | 把这次结果移到 `$R/replay/attempt1-ray-timeout/` 留存，在没有其他负载时把四行证据整套重跑一遍，全部相等。 | 这是基线环境的基础设施故障，与新分支代码无关；整套重跑保证四行出自同一次执行。 |
| I19 | 两份完整日基线原先在 Darwin arm64 上生成，重冻在这台 Windows AMD64 机器上进行。 | 照 09 章【T50】在本机重冻；`generation` 里的平台字段随之变为 Windows、AMD64，两份候选逐字节相等后才发布。 | 规格要求在全部生产改动之后由新分支的解释器重冻；其余差异只在素材摘要与检查点摘要。 |
| I20 | 【V31】要的「标『新』的用例」散在九章、格式不一（状态列位置不同、09 章写在测试格里、有的节点不带反引号或只写 `::名字`）。 | 用脚本从各章测试表抽出 189 个函数级节点，补全路径后展开参数化，得到 273 个节点，写进 `$R/new-tests.txt`。 | 收集到的节点数等于行数，才能证明一个不缺。 |
| I21 | 规格要求本记录逐条写出实施中定下的问题，而各轨道的决定是英文的。 | 141 条轨道决定逐条译成中文列在下表，英文原文留在 `$R`。 | 文档用中文；原文可查。 |
| I22 | 10 章【V31】的命令 `pytest --collect-only -q $(cat "$R/new-tests.txt")` 会按空白拆词，而 26 个参数化节点的编号含空格（例如 `[ ]`）；另外抽取脚本把 6 个节点编号里的反斜杠写成了斜杠（`文` 成了 `/u6587`），第一次收集只得到 267 个。 | 把 6 行的编号恢复成 pytest 实际的写法（原样留在 `$R/new-tests.mangled.txt`），用 `mapfile -t` 逐行读入、每行作为一个参数传给同一条 `pytest --collect-only -q`；273 行收集到 273 个节点，没有错误。 | 【V31】的判据是「收集到的节点数等于行数」；逐行传参只改变 shell 拆词，不改变收集的节点集合。 |

### 各轨道

| # | 轨道 | 问题 | 取的做法 | 理由 |
| --- | --- | --- | --- | --- |
| A001 | 1 | `_configure_process` 改为模块级 `credential_value` 后，`_prepare_resume` 不再使用函数内的 `from worldmock.runtime.framework import setup as run_setup`；规格没有列出删除这条 `DELAYED_IMPORTS` 登记。 | 删除这条已无用的导入；负责登记的轨道须移除 `('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.runtime.framework.setup')`。 | 依据 `AGENTS.md`：死代码一律删除；`DELAYED_IMPORTS` 必须与实际的函数内导入完全一致。 |
| A002 | 1 | 午夜任务移到 `midnight.py` 后，runner 中导入的 `ProtocolError` 不再被使用；规格只列出 `IndependentModelClient`、`time`、`MEMORY_DIGEST_TIERS`（ch01）和 `json`、`PersonaConfig`（ch05）。 | 同样从 runner 的导入中删除 `ProtocolError`；原有未使用的 `copy`、`hashlib` 导入保持不动。 | 它唯一的使用方已随本次改动移出；规格没有要求处理基线中未触及的导入。 |
| A003 | 1 | `RunHost._advance`：两个午夜 await 是内联写出，还是调用 `_settle_midnight_tasks`？ | `_advance` 先调用 `await self._settle_midnight_tasks()`，再调用 `engine.decide_midnight_rhythm()`。 | 与 H9 相同的两个 await、相同顺序，这一对只在一处出现；`_checkpoint`、`start_resume` 中的变异锚点在各自符号内仍保持唯一。 |
| A004 | 1 | T55/T41 用 `new_run(prepared, OfflineClient())` 启动新运行，但 `start_new` 现在会写入初始检查点并补齐缺失的日计划，这会向 `OfflineClient` 发出 `plan_day` 请求（`AssertionError`）。 | 在我负责的文件（`test_runtime.py`、`test_runtime_inputs_contract.py` 的 `prepared_parent`）中，辅助函数 `_offline_day_plans` 在 `new_run` 之前把 `worldmock.runtime.lifecycle.midnight.PlanDayClient` 替换为 `lambda client, identity: StubPlanDayClient(identity)`。 | 与 T39 为 `native_runtime` 使用的替换点相同；`OfflineClient` 仍能证明没有模型请求到达运行级客户端。 |
| A005 | 1 | H8 说「与 H10 相同的一小时新运行」且其中包含仅推进的步；从 08:00 起，睡眠中智能体在这一小时内的唯一边界可能是 `end_time`，因此不能保证出现仅推进的步。 | `test_every_step_writes_exactly_one_checkpoint` 改用跨越本地午夜的一小时窗口（23:30 至 00:30），借睡眠期间的午夜边界保证出现仅推进的步；H10 仍用 08:00 至 09:00。 | 这是对所述条款的确定性测试；睡眠中的智能体在醒来前的边界上不会被给予决策（见 `next_decision_time` 与基线中 `_step_to_midnight` 的断言）。 |
| A006 | 1 | `_fresh(tmp_path, mutate=None)` 还需要在 `_late_environment(tmp_path)` 上组装宿主。 | `_fresh` 增加可选参数 `root`；依赖晚期环境的测试调用 `_fresh(tmp_path, root=_late_environment(tmp_path))`；`_late_environment` 用同一个 `_mutated_environment` 辅助函数构建。 | 这是能同时服务规格点名的两个辅助函数、又不必把环境复制两份的最简单辅助函数形态。 |
| A007 | 1 | H13 在 `native_runtime` 之外构造 `CurrentDecisionModel`，应传入什么 prepared？ | 先 `CurrentDecisionModel(None)`，再 `model.engine = engine`。 | T39 定义的 `__init__(self, prepared)` 只设置 `engine`、`requests`、`closed`。 |
| A008 | 1 | W13 说 `test_resume_restores_exact_world_card_and_full_model_from_the_frozen_workspace` 中卡片与模型身份的断言「由智能体章节依据冻结输入」重写，但第 03 章没有给出文本。 | 改为读取 `prepared.agents[1].model_identity`：`stages.action.same_second_continuation` 为 `False`，`model_metadata` 等于冻结的元数据；`workspace_mode` 检查改为 `run_identity is None`。 | 与旧的 profile 断言是同一组事实，改从取代工作区 profile 的 `PersonInputs` 中读取。 |
| A009 | 1 | `test_resume_rejects_a_corrupt_memory_workspace_before_publishing` 使用哪种损坏？ | 工作区中 `MEMORY.md` 非空，`memory/episodes.jsonl` 含一行 `{}`（不符合模式），期望 `IntegrityError('Restored agent memory is invalid')` 且不产生子目录。 | 通过真实的 `_prepare_resume` 路径检验 W13 规定的唯一一次严格回读。 |
| A010 | 1 | `test_new_run_freezes_complete_inputs...` 曾断言 `'scenario_path' not in prepared.env_kwargs`，而该字段已不存在。 | 改为 `not hasattr(prepared, 'scenario_path')`，其余断言不变。 | 保留原意（不携带场景路径），又不依赖已删除的字段。 |
| A011 | 1 | 引擎在 `start_new` 内部创建时，`_MidnightClient` 如何为 H1 记录 loop、thread、round？ | `_MidnightClient` 带有 `seen` 与 `watch` 属性；H1 设置 `watch = lambda: host.engine.decisions.round`；关闭失败的用例使用一个覆盖 `close` 的局部子类。 | 对规格中的辅助类做最小补充，使 H1 与 H34 的断言能够成立。 |
| A012 | 2 | `test_read_query_requires_a_valid_context_and_the_open_round` 在复用原上下文前必须先开轮、观察、登记 `do_at` 并提交，但规格给该文件的导入列表中没有 `DirectToolCall` 或工具名常量。 | 从 `tests.life.environment_runtime` 导入 `act`，调用 `act(engine, 'context-check', do_at('家', '测试休息'))`，标签与原上下文相同，因此 `act` 构建的上下文等于原上下文。 | `act` 恰好执行开轮、观察、登记、提交；复用标签得到完全相同的 `DecisionContext`；这是最简单的做法，且规格第 30 行的导入保持不变。 |
| A013 | 2 | 新增和改写的测试中，规格未点名的小辅助函数。 | 在 `test_read_query_runtime.py` 中加入 `_logged(caplog, message)`，在 `test_engine_facade.py` 中加入 `_open(engine, label)` 与 `_committed_detail(engine, round_id)`，与规格的 `_containers`、`_scramble`、`_goal` 并列。 | 避免重复相同的开轮、观察和 ops 日志查找代码；它们只用于测试，带中文 docstring。 |
| A014 | 2 | `test_read_query_business_rejection_keeps_the_five_field_shape`：是否先放置一个日计划目标。 | 不放置目标；请求指名一个在新引擎上不存在的任务。 | 规格只要求开轮、观察，再回忆一个不存在的任务；两种情况都会抛出 `TASK_NOT_FOUND`。 |
| A015 | 2 | 如何检查 `read_query` 返回的副本保持键顺序。 | `test_read_query_returns_a_deep_copy` 比较 `list(returned) == list(built)`，并比较两者的 `json.dumps(..., ensure_ascii=False)`，后者覆盖嵌套键的顺序。 | 规格要求键顺序相同，且该文件导入了 `json`；observe 副本测试只用规格给出的断言，因为观察值未必都能序列化为 JSON。 |
| A016 | 2 | `test_simulation_engine_imports_no_framework`：如何处理相对导入的 `ImportFrom` 节点。 | 检查每个 `Import` 的别名名称，以及每个 `level == 0` 的 `ImportFrom`（取模块首段），范围是整棵语法树，包括 `TYPE_CHECKING` 块和函数体内。 | 相对导入不可能指向顶层包；与第 07、09 章导入守卫所用的 `level == 0` 规则一致。 |
| A017 | 2 | `LifeEnv` 名称守卫使用的正则。 | 原样采用规格的正则：`re.compile(r'\b' + 'Life' + 'Env')`。 | 第 02 章明确给出这个正则（匹配以旧名开头的标识符），不同于有意避免 `\b` 的 T27。 |
| A018 | 2 | 由于后面有一行重新绑定 `second`，`test_conflicting_parallel_request_registers_once_then_changes_no_activities` 中新增的断言应放在哪里。 | 保留规格的 `[result, second]` 解包，把 `assert second == result.model_copy(update={'action_id': records[1].action_id})` 放在读取 `records` 之后、现有 `first, second = ...` 一行之前。 | `records` 到那里才有定义；后面原有的断言保持不变。 |
| A019 | 2 | `engine.py` 中 `import copy` 的位置。 | 放在标准库分组中，紧挨在 `from datetime import datetime` 之前。 | 规格只说要加这条导入；该文件的标准库导入都放在这一组。 |
| A020 | 3 | `_submit_life_action` 过去抛出 `RuntimeError(HostFeedback)`。feedback 改为 dataclass 后，消息应是什么？ | `raise RuntimeError(error.text)`。 | 保持原先 str 子类产生的同一条英文消息。规格对这段函数体只列出机械替换。 |
| A021 | 3 | `_prepare` 不再设置本回合的工具模型（基线的 `prepare_react_loop` 会设置）。准备后调用 `_validate`、`_parse` 的单元测试如何获得可用工具？ | 这些测试在校验前调用 `agent.turn_tools(16, False)`（回合数有影响时用 4）。轨道 4 的 `_prepared` 已调用 `turn_tools(2, False)`，多调用一次无害。 | 真实循环中，`turn_tools` 每回合都在 `parse_reply` 之前运行。跳过循环的测试必须自行设置本回合的工具面。 |
| A022 | 3 | 移植来的 AS2 react 校验用例断言的是 AS2 通用文本（'native tool_calls are required'、'unknown native tool calls'、失败证据中的 repr、证据或工具失败时的原始 `OSError`、一个 'executed' 字段）。移植后应断言什么？ | 保留每个用例的意图，但断言真实 `LifeAgent` 的行为：`TOOL_CALL_REQUIRED`、`ARGUMENTS_NOT_JSON` 反馈，`MODEL_SERVICE_FAILED` 阶段文本，以原异常为 `__cause__` 的 `RuntimeError`（`EVIDENCE_WRITE_FAILED`、`TOOL_SERVICE_FAILED`），`FEEDBACK_INSTRUCTION`，并用 `sorted(request)==sorted(REQUEST_KEYS)` 取代 `max_retries==0`。 | 09 T30 把被测对象换成真实的 `LifeAgent`。AS2 基类的这些文本从未进入生产路径，因为 `LifeAgent` 覆盖了它们。 |
| A023 | 3 | 观察列表移除后，仍读取 `request.observations`（宿主回执、ok 标志）的测试。 | `test_decision_context_protocol` 包装 `agent._submit_life_action` 捕获回执，并去掉已删除的 'observation' 字符串检查；`test_memory_search_tool` 从 `read_query` 的回答推出 ok（`'error_code' not in answer`，与 `ReactToolResult.ok` 同一规则）；`test_decision_context_roundtrip` 从存储的 `action_requested` 事件读取 `action_id`。 | 规格移除了观察列表和 `ReactToolResult.observation` 字段。这些改动通过仍然存在的对象断言同样的事实。 |
| A024 | 3 | `test_relationship_channels...` 调用了 `runner._validate_persona_card_struct`，而 05 W14 删除了该函数。 | 删除这次调用、相应导入，以及 docstring 中关于恢复检查的分句。 | 该函数已不存在。恢复时不再检查卡片（W14）。 |
| A025 | 3 | `test_protocol_feedback_is_a_structure_with_its_evidence_text` 如何证明证据中的错误等于 `feedback.text`？ | 先在 `_step_observation` 上单独校验同一个错误调用，再让同一回复走真实循环（`_corrected_loop`），比较响应证据中的错误与 `feedback.text`。 | 它使用真实的证据路径，而不是只信任 dataclass 本身。 |
| A026 | 3 | `react_loop` 中工具调用对象的共享序列化。 | 私有辅助函数 `_wire_call(tool_call)` 为 `wire_tool_call` 提供输入，供 `response_tool_calls` 与 `accepted_assistant` 共用。 | 两个移入的函数按相同的 getattr 形态序列化（AS2 `_serialize_tool_call`）。用一个辅助函数避免重复。 |
| A027 | 3 | 移植的循环日志（'Model evidence persistence failed'、回合耗尽日志、序列化错误）使用哪个 logger。 | 使用模块级 `logging.error`、`logging.exception`（根 logger），与 `LifeAgent` 其余部分一致。 | 测试按消息文本匹配，而规格给出了文本但没有指定 logger。 |
| A028 | 3 | 带 `ProtocolFeedback` 返回类型的 `merged_feedback` 签名超过 120 个字符。 | 把参数列表折行，函数体不变。 | 风格闸门的行长限制。 |
| A029 | 3 | `test_life_agent_engine` 需要观察真实引擎上的调用顺序。 | 用一个轻量的记录转发器实现 `EngineHandle` 的五个方法，记录调用并委托给真实的 `LifeEngine`。记忆存储取自运行目录中的 `memory_stores`。 | 规格要求真实引擎和 SQLite，并记录调用顺序。转发器让每次写入仍落到真实存储。 |
| A030 | 3 | `exchange.py` 需要 `DecisionContext` 作为 `ModelCallEvidence` 的类型注解。 | 在运行时从 `worldmock.state.decisions.round` 导入。 | `round.py` 只导入标准库和 pydantic（ch01 H40），因此不存在循环；规格点名的就是这个模块。 |
| A031 | 4 | [T53] 把 `_prepared` 定义为只做 `wire` + `_context` + `_prepare`，但按 03，`_turn_models` 只由 `turn_tools` 设置，紧接 `_prepared` 的 `_validate`、`_dispatch` 会拒绝所有工具（基线中由 `prepare_react_loop` 打开工具面）。 | `_prepared` 另外调用 `agent.turn_tools(2, False)`，即 `_functions` 读取的那个非最后回合工具面。 | 这再现了基线中 `_prepared()` 的状态（八个工具开放），使我的文件及导入 `_prepared` 的其他轨道文件中的断言体不变；这是与 [T53] 自身 `_functions` 定义相符的最小补充。 |
| A032 | 4 | `waking_decision`（host_run_fixtures）过去返回 `run.environment` 和 `run.agent`，[T38] 删除了它们，规格没有说明现在返回什么。 | 返回 `SimpleNamespace(engine, agent, decision, model, slept)`。`agent` 是为这一次醒来决策新建的 `LifeAgent(run.inputs, engine, run.model, run.store)`；去掉 environment，因为引擎就是句柄（02 替换表）。 | 每次决策一个实例（03 [A3]），且对 `test_host_failure_stages.py`（人物轨道）改动最小：`run.agent.decide(run.decision)`，原用 `run.environment` 处改用 `run.engine`。 |
| A033 | 4 | `test_plan_day_is_no_person_tool_and_revision_carries_remind_time` 导入了被 02 删除的 `_STABLE_TOOL_SURFACE`；02 [E22] 把这七个名称放进 `test_tool_surface.py`。 | 原先四条 `_STABLE_TOOL_SURFACE` 断言改为在 `REQUEST_MODELS` 和 `life_action_tool_names()` 上断言同样的事实：七个工具、没有 `plan_day`、没有 `start_activity`、两个 start 工具都存在。 | 测试保持原意（`plan_day` 不是人物工具），不依赖已删除的常量，也不重复 02 的字面量测试。 |
| A034 | 4 | `test_step_tail_sleep_boundary_hook_is_not_overridden_and_is_always_false` 只检查父类的 `memory_sleep_boundary` 钩子。 | 删除。 | `LifeAgent` 不再继承任何类，03 又从步末尾移除了 `sleep_boundary`，该测试已没有检验对象。09 把父类钩子用例交给第 03 章。 |
| A035 | 4 | 两个测试断言了 `request.observations`（观察记录列表），而 03 移除了它（「不再向观察列表追加记录」）：`test_start_group_registers_both_start_requests_in_reply_order`（回执记录）和 `test_registered_world_request_ends_the_decision_and_memory_queries_do_not`（已执行动作列表）。 | 删除这两条单独的断言。其余对 `environment.calls`（动作 id、工具、请求）和 `environment.queries` 的断言仍覆盖执行序列。 | 它们读取的对象已不存在。在测试里重建它等于模仿已删除的行为。 |
| A036 | 4 | `test_readonly_finish_batched_with_another_tool_is_rejected`（`test_conclude_memories`）和 `test_life_tool_surface_never_offers_finish` 的只读部分，只测试裁决 7、03 [A27] 删除的只读问答路径，且没有任何章节表列出它们。 | 删除前者。后者只保留生活决策那一半，其 docstring 去掉关于只读的那句。 | 与 03 对 `test_how_to_live_a_day`（去掉只读那一半）和其他只读测试（删除）的处理相同。 |
| A037 | 4 | `test_conclusion_sources_are_registered_by_the_system` 对存储的 episode 断言 `item['refs']`，但 05 [W7] 已从 episode 中移除 `refs`。 | 断言的元组去掉 `refs`，改为 `(type, text, source, epistemic_status)`；episode 用 `read_episodes(person.memory.workspace, person.memory.agent_id)` 读取。 | 05 的字段集要求如此。05 自身对 `test_person_step_records_conclusion_memory_update` 做了同样的修改。 |
| A038 | 4 | `test_memory_persistence_failure_aborts_society_before_commit`：去掉 `'empty'` 后，是否还保留只剩一个取值的 parametrize？ | 删除 parametrize 装饰器和 `failure_mode` 参数，测试名不变。 | 09 写明“删去 empty 参数化、保留用例名”。规格表登记的是不带参数的 node id。 |
| A039 | 4 | `test_conclude_memories` 的 person helper：[T56] 写的是不带参数的 `person_inputs(...)`，且未给出轮数上限（旧工作区配置用 8）。 | `replace(person_inputs(engine.context.inputs.persona, engine.context.store.manifest['stage_model_identity']), name='陈曦')`。轮数上限取自 manifest。 | 真实引擎的冻结输入和 manifest 正是 `RunHost` 传入的内容。规格只覆盖名字，脚本对话最多用 4 轮，因此上限不改变行为。 |
| A040 | 4 | `test_nothing_sent_to_the_person_model_carries_an_internal_identity` 需要循环历史（`turns_with_history`），也需要完整决策（observe、turns、step 结束），但 `decide` 不返回历史。 | 测试先开轮、observe，再运行 `turns_with_history`，调用 `person._record_memory(decision)`，最后 commit。 | 与 03 `decide` 和 [T54] `_step` 的三步相同，决策及其证据与现状一致，同时按 [T37]/[T56] 的要求拿得到历史。 |
| A041 | 4 | `test_views_persist_as_memories_byte_identical_to_the_subject_shape` 需要一行字面量的首条 episode（05 W8 行）。 | `_FIRST_EPISODE_LINE` 取普通记忆的基线行（agent 1、tick 60、`_OBSERVED_AT`），去掉 `tick`/`tick_duration_sec`/`step_index`/`step_count`/`refs`，id 改为 `m_000001`，按 05 [W7] 键序和 `json.dumps` 默认分隔符。两个工作区都用 `WorldMockMemoryStore(tmp_path/label, 1, ())` 和 `MemoryStep('人物画像里的身份', (), 'observation-1', memories, _OBSERVED_AT)`。 | 与基线 `_runtime` helper 一致（agent 1、相同的身份文本和 observation id），因此该字面量正是基线输出，只去掉了五个键。 |
| A042 | 4 | `test_store_fixes_source_and_status_and_refuses_non_subjective_types`：替换测试的形式。 | 对 `WorldMockMemoryStore.append_episodes` 写一个不参数化的测试。输入的 source／epistemic_status 被忽略，记录得到 `activity_conclusion`/`subjective`；type `'event'` 抛出匹配 `'has an invalid type'` 的 `ValueError`；episodes 文件字节、`episode_ids()` 和 `read_episodes` 保持不变。 | 正好覆盖 05 W8 行：字段不读取、非法 type 被拒、文件不变。 |
| A043 | 4 | `decide()` 返回 `None`，`step()` 上原有的摘要断言（`'conclude_activity registered'`）没有可比对的值。 | `test_person_step_records_conclusion_memory_update` 断言 `await agent.decide(_context()) is None`。`conclude_memories` 的 `_decide` helper 只 await `decide`。 | 03 [A4] 规定 `decide` 返回 `None`。登记的请求仍通过 `environment.calls` 和存储的事件检查。 |
| A044 | 4 | `_step_host` 的记录桩如何实现。 | 两个小类：`_Recorder`（同步，返回 `{}`）和 `_AsyncRecorder`（异步，返回 `[]`），各保存一个 `calls` 列表；用 monkeypatch 挂到 `run_host.snapshot_workspaces`、`settle_pending_memory_digests` 和 `settle_pending_day_plans` 上。 | 这是 [T53] 所说“返回 {} / [] 的记录桩”最简单的形式，且通过被替换的模块属性仍可检查这些桩。 |
| A045 | 4 | 删除后遗留的未使用 import（例如 `test_same_second_continuation` 中的 `write_agent_workspace` 和 `ARTIFICIAL_ENVIRONMENT_ROOT`），以及我的测试文件中 `LITELLM_LOCAL_MODEL_COST_MAP` 的 setdefault 行。 | 原本就有的未使用 import 不动。`test_output_truncation` 中因我自己的删除而变得未使用的 import 已移除。LITELLM 的 setdefault 行保留：[T29] 只从这 22 个文件中移除 `AGENTSOCIETY_*` 行。 | 这样改动只限于规格要求的内容；[T29] 守卫只检查 `tests/conftest.py`。 |
| A046 | 5 | `_plan_day_caller`（`test_model_exchange_attempts`）和 `_digest_caller`（`test_memory_digest_runtime`）：第 04 章说返回 `PlanDayClient`/`MemoryDigestClient(scripted ModelClient, identity)`，但跨章裁决说这些调用方改为传入模型层客户端。 | 两者都返回脚本化的真实 `ModelClient`（预算取自冻结的 identity）；由 `settle_pending_*` 把它包进真实的业务客户端。 | 第 10 章的跨章裁决优先，且点名的正是这两个调用方；避免重复包装。 |
| A047 | 5 | M23 测试要求从 SQLite 读回的 plan_day 草稿满足 `tuple(request) == REQUEST_KEYS`。 | 对从存储读回的草稿比较 `sorted(draft) == sorted(REQUEST_KEYS)`；构造顺序由 `test_plan_day_request_has_exactly_the_six_draft_keys` 和 `test_request_caps_output_and_disables_thinking` 固定。 | 事件以带 `sort_keys` 的规范 JSON 存储，读回的键序是字母序（规格本身也说键序不会进入存储字节）。 |
| A048 | 5 | `_Retries` 是带延迟注解的 `@dataclass`，在此情况下如何按文件路径加载 `model_client.py`（M19 测试和两个 probe）。 | 标准做法：`module_from_spec`，在 `exec_module` 之前登记到 `sys.modules`（测试中用 `monkeypatch.setitem`）。 | dataclasses 通过 `sys.modules[cls.__module__]` 解析字符串注解；不登记就加载会抛出 `AttributeError`。 |
| A049 | 5 | `test_a_closed_client_leaves_no_unclosed_session_records` 中的“asyncio 和 litellm”指哪些 logger。 | `logging.getLogger('asyncio')` 和 `logging.getLogger('LiteLLM')`（litellm 的 verbose logger），直接挂一个 DEBUG 级 handler。 | litellm 以 `'LiteLLM'` 为名记录日志；直接挂的 handler 不依赖日志传播。 |
| A050 | 5 | 表中没有写出故障场景 503 和 429-date 的记录链。 | 503 断言 `'ServiceUnavailableError <- OpenAIError <- InternalServerError'`，429-date 断言与 429-seconds 相同的 `RateLimitError` 链。 | 与已写出的 502 和 429-seconds 行映射相同（openai 对所有 5xx 抛 `InternalServerError`，litellm 把 503 映射为 `ServiceUnavailableError`）；故障 probe 核对两侧。 |
| A051 | 5 | `test_real_environment_midnight_step_registers_next_plan_before_rhythm` 原来使用 `LifeEnv.step`。 | 改为 `host_factory(engine, OfflineClient())` 并 `await host._advance(next_event_at)`，`midnight.PlanDayClient`/`MemoryDigestClient` 换成桩工厂。 | 依第 02 章替换表，step 经由 `RunHost`；`_advance` 正是旧 env step 的对应物（推进 + 零点任务 + rhythm）。 |
| A052 | 5 | 规格未给出的 docstring（`IndependentModelClient.__init__`/`call`、`ModelClient` 类及其方法、`collect_transport_attempts` 中过时的 “worker” 措辞）。 | 写了描述当前行为的简短中文 `@brief` docstring；删去 `collect_transport_attempts` 中过时的 worker 说法，函数体逐字保留。 | 风格门要求 docstring；旧文本提到已删除的概念（dispatcher、worker）。 |
| A053 | 5 | 规格未固定的 probe 设计细节。 | 每个 case／场景用一个本地服务器服务两侧，两侧之间重置；viewer-context case 的基础预算用录制 manifest 的值覆盖；故障 probe 另将每侧与预期表核对，connection-refused 尾部归一化；receipts JSON `indent=2`、fixture `indent=1`，均写为 UTF-8 LF 字节；共用准备代码在两个脚本中各写一份，不新增模块。 | 两侧 Host 头相同；“两侧都从 manifest 取预算”；“两侧都必须等于该表”；Windows 的 `write_text` 会写出 CRLF；文件只限于规格列出的那些。 |
| A054 | 5 | 小的测试清理。 | `test_shared_model_clients` 的第一个测试请求不再带 `max_retries`；`CurrentDecisionModel` 删去无用的 `model_name` 属性；`failing_client` 在作答时把 `client.engine` 复制到 `inner.engine`；`test_validation_execution` 不改（M24 说不变）；`prompt_capacity` 的 `token_counter` import 放进 worldmock import 组。 | 草稿不再带重试键；没有代码从客户端读取 `model_name`；这是把已挂接的 engine 交给内层模型的最简单方式；规格有明文规定。 |
| A055 | 6 | track 4 重写之后，`host_run` 是否仍是 async？ | 保留 `await host_run(...)`（规格 T38 没有把它改为同步；当前工作树版本是 `async def`），并直接从工作区路径读取文件，不依赖 `HostRun` 的属性名。 | 规格保留基线签名；按路径读取可避免依赖未规定的属性名。 |
| A056 | 6 | agent 的句柄就是引擎本身之后，`test_memory_retirement` 如何拦截记忆证据？ | `_woken` 在实例上把 `engine.record_memory_update` 替换为包装函数，先运行检查器再调用原绑定方法；拒绝检查器在类上调用 `LifeEngine.record_memory_update(engine, ...)`，以免递归进入包装函数。 | `LifeAgent` 调用 `self.engine.record_memory_update`；实例覆盖是最简单的拦截方式，不改生产代码。 |
| A057 | 6 | `test_a_five_key_snapshot_in_memory_update_evidence_is_rejected`：不含 episodes 的证据无法带五键快照。 | 检查器只作用于含 episodes 的证据（否则提前返回），测试断言至少检查了两条证据。 | `refresh_summary` 证据没有快照；记录的十二条记忆产生两条 `append_episodes` 证据。 |
| A058 | 6 | `test_branch_coverage::test_memory_quality_reports_each_problem_code` 对事件 9 使用了 trigger `('reflection',)`。 | 事件 9 改用空 trigger 加一个 merged id（三个问题码不变：unknown merged id、source window、without source）；`write_points_checked` 改为 2；从 `update_data` 删去旧键。 | reflection 已不再是写入点（会使事件无效）；保持对相同问题码的覆盖。 |
| A059 | 6 | 如何证明 W2“新工作区为空，初始快照是空归档”。 | 在临时目录上 `create_workspaces` 得到空目录；在 `native_runtime` 新运行中，首个 checkpoint 的 `agent:1` 资产解压后是无成员的 tar，sha256 与空目录的 `snapshot_directory` 相同，并恢复为空工作区。 | 使用真实运行路径和真实快照函数，不借助私有钩子。 |
| A060 | 6 | `workspace.py` 中的日志与错误构造。 | 模块 logger 用 `logging.getLogger(__name__)`；每个 `IntegrityError`/`FileExistsError` 之前显式 `logger.error`；`'MEMORY.md must not be empty'` 是 try 内的普通 `ValueError`，由 except 以 `'Restored agent memory is invalid: <error>'` 记录。 | 规格要求先记日志再抛出；except 分支只把原始错误记录一次。 |
| A061 | 6 | `'type'` 不可哈希时的读回类型检查。 | `_validate_record` 以相同的 `'has an invalid type'` 文本拒绝非字符串 type（在 frozenset 成员判断前加 `isinstance(str)` 守卫）。 | 否则列表值会抛 `TypeError` 而不是规定的 `ValueError`；规则相同，文本相同。 |
| A062 | 6 | store 错误写在哪里。 | `life_memory_store` 使用 `worldmock.foundation.errors.reject_value`（记录同样的英文文本，抛出 `ValueError`）。 | 这是“记录相同文本再抛 `ValueError`”的现有 helper；避免第二份实现。 |
| A063 | 6 | 视图预算常量。 | 在 `life_memory.py` 中定义私有模块常量 `_VIEW_CHARACTER_BUDGET = 4000`。 | 为唯一的 4000 字符规则命名；不新增公开接口。 |
| A064 | 6 | `memory_evidence.py` 和 `life_memory.py` 中 `DecisionContext` 的 import。 | 在模块级从 `worldmock.state.decisions.round` 导入。 | 规格说 `DecisionContext` 来自该模块；`round.py` 是纯数据模块，因此不存在循环导入。 |
| A065 | 6 | 在测试中断言旧的 run-dir 工作区根目录不存在，同时不触发 T27 文本守卫。 | 目录名用片段拼接（`"a" + "s2"`），并加中文注释。 | T27 禁止测试中出现完整单词；守卫测试也用同样的片段技巧。 |
| A066 | 6 | 规格要求保留的 `life_memory.py` 私有 helper 注解。 | `_quote`、`_fact_line`、`_amount`、`_classify_episodes`、`_episode_snapshot`、`_file_sha256` 逐字节保留，仅删去 `_SNAPSHOT_FIELDS` 注释中对框架的提及。 | 规格：保留 `:21-22`、`:163-199`、`:221-232`；I26 禁止在注释中点名该框架。 |
| A067 | 7 | `test_run_host_failures.py` 中的模型替身应如何构造决策回复？第 06 章为该文件列的 import 清单没有指明响应类型。 | 替身从 chat-completion 载荷构造真实的 litellm `ModelResponse`：始终设置 `reasoning_content`，以及 content、按序的 tool_calls、`finish_reason` 为 `'tool_calls'` 或 `'stop'`、usage，tool-call id 按决策保持唯一（`call-<n>-<i>`）。文件新增 `from litellm import ModelResponse`。 | `ModelClient.call` 返回的正是该类型（第 04 章：call 原样返回 litellm 的 `ModelResponse`）。`LifeAgent` 按属性及 `model_dump(mode='json')` 读取回复，所以真实类型是不会偏离生产的最简单替身。只有 src 被限制导入 litellm。 |
| A068 | 7 | 预期事件数据中的 R（`round_id`）从哪里来？ | 取自最后一个 `decision_started` 事件的 `data.round_id`。协调器在第一个动作时以及中止时都会写出延迟的 `decision_started` 草稿，因此已开启的轮次总有该事件。 | 这是该轮自己记录的身份。若从被测的中止事件中取，断言就成了循环论证。 |
| A069 | 7 | 在不期望非决策请求的测试中，替身遇到非决策请求（tools 中没有 `start_main_activity`）时怎么办？ | 抛出 `AssertionError('Unexpected non-decision model request: ...')`。只有零点测试传入 `midnight=<LLMDispatchError>`，对每个非决策请求都抛出它。 | 规格说这些情况下不会发生零点调用。大声失败比编造 plan 或 digest 的桩答复更简单、更诚实。 |
| A070 | 7 | 第 06 章 import 清单中的 `RunHost` import 如何使用？ | helper `_attach(host_factory, engine, script, midnight)` 调用 `stage_day_plan(engine, submitted=True)`，构造替身，调用 `host_factory(engine, model)`，断言 `isinstance(host, RunHost)` 并返回 `(host, model)`。 | 它共享测试表中“默认先 `stage_day_plan`”的准备步骤，让所列 import 有实际用途，且不增加行为。 |
| A071 | 7 | `test_failure_message_joins_groups_and_keeps_other_text` 中的 `_CarriesCause`（“本地 class”）放在哪里？ | 放在测试函数内部。 | 它只属于使用它的那一个用例，且模块级的 `_RemoteFailure` 已删除。 |
| A072 | 7 | 零点失败测试断言哪条失败证据（“这次零点调用有 phase 为 failure 的失败证据”）？ | 恰好一条 `data.phase == 'failure'` 的 `model_response`。其 `data.purpose` 为 `memory_digest` 或 `plan_day`，`data.error` 等于注入的 `LLMDispatchError` 的 `repr`。 | 哪个零点任务先发出请求取决于当天是否有 digest 条目。两个 `failure_draft` 函数都写入 `repr(error)`，F18 未改变这一点。 |
| A073 | 7 | 「caplog 有一条 … 的 ERROR 记录」要求多严格？ | 恰好一条 ERROR 记录，且其 `getMessage()` 等于该文本。 | 与同表其他处「恰一条」的措辞一致。决策失败日志按设计只写一次（F3）。 |
| A074 | 8 | 直接运行 `uv lock` 把所有 registry、sdist 和 wheel 的 URL 改写为清华镜像，因为用户级 `%APPDATA%/uv/uv.toml` 把该镜像设为默认索引。怎样让保留的包与基线一致？ | 恢复基线 `uv.lock`，改用 `uv lock --no-config`（仍不加 `--upgrade`）。两次导出也加了 `--no-config`，它在 `--frozen` 下不起作用。 | I19 要求保留的 58 个包的来源、sdist 和 wheel 与基线（`https://pypi.org/simple`）相同。忽略本机索引配置是唯一办法，且不改变其他任何内容。 |
| A075 | 8 | uv 0.12.19 把单元素的 `[package.metadata.requires-dev] build` 数组写成行内形式 `build = [{ name = "setuptools", specifier = "==84.0.0" }]`。这是新侧第三行，而锁文件差异准则只允许恰好两行 tomlkit 行。怎么办？ | 把该数组改写为多行形式 `build = [\n { name = "setuptools", specifier = "==84.0.0" },\n]`，内容不变；`uv lock --check --no-config --offline` 报告已最新，重跑 `uv lock` 文件字节不变，导出不变。`lock_check.py` 按规格准则原样实现并通过。 | V6 要求 `passed=true`，并规定安装轨自行修正自己的文件直至通过。这是规格自身命令的纯格式不动点；保留原始行内形式会不满足规格准则，放宽 `lock_check` 则会改变规格的判定。 |
| A076 | 8 | `test_grounded_evidence.py` 中的 `test_protocol_feedback_is_persisted_in_next_model_request` 走的是已删除的只读路径（finish 工具，无 `decision_started`），且不在任何删除表中。删除还是移植？ | 移植为真实生活决策：在真实引擎观察上调用 `run_turns`，首个回复为 `invented_tool`，随后是有效的 `start_main_activity` `interact 家 测试休息`；`max_react_turns` 为 3，两轮提供同样的 8 个工具。期望工具集改为 `life_action_tool_names()` 与 `life_query_tool_names()` 的并集，`decision_started` 恰为 1；协议反馈断言全部不变。 | 它守护的行为（反馈经真实 SQLite 持久化到下一次请求）仍然存在，只是只读载体没了。只改了只读路径特有的断言。 |
| A077 | 8 | `test_fresh_checkout_disables_submodules_ssh_and_pip_cache` 须断言 `check_environment()` 与 `os.environ` 恰在五个键上不同，但 `tests/conftest.py` 已在测试进程中设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`。怎样让断言确定？ | 先用 `monkeypatch.delenv` 删除这五个键，再断言变化或新增的键等于规定的五个值，其余每个键都等于 `os.environ`。 | 否则成本表这个键在测试会话中永远不会显示为变化，规格的断言无法通过。 |
| A078 | 8 | 怎样在测试运行时把 `repr(REMOVED_PROCESS_VARIABLES)` 填入干净进程桩的名称表？ | 桩源码含占位符 `__NAMES__`，辅助函数 `_stub_entry()` 在每个测试调用它时进行替换。 | 这是让 11 个名称只在 `test_framework_removal.py` 中拼写一次的最简形式。 |
| A079 | 8 | 删去工作区输入短语后，`creation_inputs` 文档字符串的新措辞。 | 「由人设表直接构造唯一人物卡片，生成真实目录输入警告；不调用模型。」 | 第 05 章只说删除该短语。 |
| A080 | 8 | `provenance_check.py` 需要哪些规格未定的细节？ | 每个 4 行元组映射到它在上游的首次出现，上游文件按路径顺序遍历；`line` 与 `upstream_line` 是两个窗口的首行行号；`matches` 只列至少命中一次的模块；解压到 `$ONE/upstream/`。 | 这是对 07 算法最简单的确定性解读。 |
| A081 | 8 | 删除 `patches/agentsociety2` 后，是否保留空的 `patches/` 目录？ | 已删除。 | 它只含 `agentsociety2`，且 `test_removed_framework_paths_are_gone` 要求 `patches` 不存在。 |
| A082 | 8 | 规格未逐字列出的小型健全性断言。 | 依赖守卫还断言三个锁文件各自的包名集合非空；回执测试还检查 `missing_components == ["Node"]`，与其桩一致。 | 防止不相交检查空洞通过，并让既有断言与新桩一致。未增加任何行为。 |
| A083 | 9 | 对 workspace-members 的 applied 计数器而言，什么算「成员被改写或丢弃」？ | `_workspace_listing` 对每个基线侧快照（受该规则约束）计 1，条件是任一成员被跳过，或其列出的字节与原始字节不同。 | 这是对 `_workspace_member` 返回值最简单的检查；真实基线快照总带有框架成员，因此每个都计入；避免两次调用事件 id 映射（会重复产生 `REFERENCE_NOT_AN_ID` 问题）。 |
| A084 | 9 | episodes 文件共用的行解析和规范写出放在哪里？ | 两个小的私有辅助函数：`_episode_records`（按 `\n` 切分、去掉空尾、拒绝非对象行）和 `_episode_bytes`（规范 JSON 加 `\n`），供 `_base_episodes` 和 `_head_episodes` 使用。 | 两侧同一条规则只放一处；规格只点名两个公共形态的辅助函数，但两者需要完全相同的解析。 |
| A085 | 9 | 根本不是有效 JSON 的行，是否与非对象行同样处理？ | 是：JSON 解析错误和非 dict 值都抛出 `ComparisonError` `'Workspace episodes line {n} is not a JSON object'`。 | 无法解析的行不是 JSON 对象；用同一条错误文本让规则保持简单。 |
| A086 | 9 | 基线常量 `tick=0`、`tick_duration_sec=0`、`refs=[]` 匹配得多严格？ | 按规范 JSON 相等比较，因此只移除恰好为 `0`／`[]` 的值（`false` 或 `0.0` 会保留并报告）。 | 规格说只有字段等于该常量时才移除；规范比较避开了 Python 中 `False==0` 与 `0.0==0`。 |
| A087 | 9 | 谁检查 `align_memory_update` 和 `drop_request_parameters` 的事件类型？ | 每个函数自行检查其事件类型（`memory_updated`／`model_request`），否则返回同一对象；`align_events` 在原有 head 对齐之前，用循环把两条已启用规则应用到基线事件上。 | 函数自成一体，符合规格「未改变时返回同一对象」；对 `align_events` 改动最小。 |
| A088 | 9 | T1 测试怎样不跑真实片段就到达 cassette 和输入恢复？ | 用 monkeypatch 把 `tool._replay` 换成返回零分歧报告的记录器；断言退出码为 0、输入文件已恢复、有 `plan.json`、无拒绝文本。 | 离线且不起子进程地走通真实的 `_run` 路径（server、`write_input`、plan）。 |
| A089 | 9 | T3 测试怎样捕获 finish 和 export_source 的命令？ | 用 monkeypatch 让 `tool._child_command` 记录命令并返回失败的 `CompletedProcess`，两个函数构造完命令即停止；finish 分别以 2 段和 1 段调用（带与不带 `--cutoff`）；resume 的 argv 用源片段数据库的副本作为工具的片段 01。 | 不运行 worldmock 即覆盖 T3 的全部四种命令形态。 |
| A090 | 9 | T2/T4 测试：父环境中有哪些代理变量？ | 三个代理变量名的大写与小写形式全部存在，外加 `RAY_ADDRESS`。 | 断言内容是三个代理名的两种拼写都被移除，这需要六个变量都存在。 |
| A091 | 9 | T23 测试：怎样证明摘要对象只有 op/created/merged？ | 除断言摘要文本完全一致外，还通过 harness 暴露的 `S` 断言 `Object.keys(view.state.summaries.get(1))` 等于 `['op','created','merged']`。 | T23 规定摘要对象恰有三个键；harness 已把 `S` 暴露为 `view.state`。 |
| A092 | 9 | T27 正则与测试中的 `RAY_ADDRESS` 字面量。 | 直接按字面写 `RAY_ADDRESS`。 | T27 正则只匹配作为整词的 Ray／ray；`RAY_ADDRESS` 两者都不是，也不在 I5 的十一个名称之内。 |
| A093 | 9 | 删除那句话后 `recorded_response_child.py` 的文档字符串。 | 只删除那句话；前面的「；」保留，段落重新折行。 | T6：其他字符不变。 |
| A094 | 9 | `record_candidate.py`：仓库根在哪里，怎样保证 litellm 离线？ | 仓库根为工作目录（脚本在 `$WT` 下运行，并插入 `sys.path` 以导入 `tests` 包）；与 `tests/conftest.py` 一样设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`；用例目录不存在由 `record_complete_day` 自己的 mkdir 保证；输出用 `write_bytes` 写出（LF）。 | 脚本位于仓库之外；与之后验证基线的 pytest 会话保持一致；`write_text` 在 Windows 上会写出 CRLF。 |
| A095 | 9 | 查看器重录测试文件：名称、fixture 与目标。 | `$ONE/viewer/test_record_viewer_context.py` 从 `tests.conftest` 导入 `frozen_inputs`、从 `tests.engine_fixture` 导入 `engine_factory`，写入前断言两个请求的键等于 `list(REQUEST_KEYS)`，再用 `write_bytes` 写所导入检出的 `tests/fixtures/viewer-context.json`；用法注明为 pytest 加 `--rootdir $WT -c $WT/pyproject.toml`。 | 该文件在 `tests/` 之外，不会加载 `tests/conftest.py`；`-c` 排除基础检出的配置；先断言可避免写出会被已提交测试拒绝的样本。 |
| A096 | 10 | T55 说 `test_run_manifest` 用 `new_run(prepared, OfflineClient())` 建新运行，但 ch01 H21/H26 让 `start_new` 在初始检查点后执行午夜任务，而夹具人物没有 2026-01-05 的日计划（`agents_missing_plan` 非空；H21 自己的测试期望一次计划调用）。`OfflineClient` 会在 `start_new` 内抛错。 | 按 T55 原样保留 `OfflineClient()` 作宿主客户端，并借 T39 已规定的接缝加一处 monkeypatch：`monkeypatch.setattr(midnight, 'PlanDayClient', lambda client, identity: StubPlanDayClient(identity))`。初始日计划由确定性桩回答，其他任何模型请求仍当场失败。 | 这是保留 T55 的客户端和断言、又能让测试通过的最小改动，复用规格自身对午夜业务客户端的替换点而不另造辅助函数。`StubPlanDayClient` 没有 `close()`，不能作宿主客户端。 |
| A097 | 10 | 从 `tests/life/conftest.py` 删去假凭据那一行后，`import os` 已无用处。 | 将 `import os` 与该行一并删除。原已存在的其他未用导入（`shutil`、`nullcontext`、`Path`、`pytest`）未动。 | 它属于同一条被删除的语句；改动较早的未用导入是规格未描述的工作。 |
| A098 | 10 | `test_opslog.py` 中的证据类型导入在任何地方都未使用，但规格行只写「改导入」。 | 按导入表逐字修改：`MemoryUpdateEvidence` 改从 `worldmock.recording_engine.events.memory_evidence` 导入，`ModelCallEvidence` 改从 `worldmock.state.model.exchange` 导入，`DirectToolCall` 改从 `worldmock.state.decisions.round` 导入。 | 按规格行原文执行，不扩大改动。 |
| A099 | 10 | `test_purchase_prefer` 的 `_host` 用 `LifeAgent._prepare` 取代 `prepare_react_loop`，但规格没说是否先设置 `_context`。 | `_host` 先设 `agent._context = decision`，再调用 `agent._prepare(observed)`，然后使用 `agent._validate([ReactDecision(SIDE_TOOL, arguments, '')])`；「无错误」现为 `is None`。 | 与 `run_turns`（ch03）及 T37/T53 辅助函数（`turns_with_history`、`_prepared`）的三步相同；`_validate` 跟踪每次决策的状态。 |
| A100 | 10 | 第 04 章「不在本章」注说生活桩须接受 `(model_client, model_identity)`，而 T39 和 ch01 H23 用适配器（`lambda client, identity: StubPlanDayClient(identity)`；`Recorded.__init__` 调用 `super().__init__(model_identity)`），其他测试以无参方式调用 `StubMemoryDigestClient()`。 | `tests/life/conftest.py` 中 `StubPlanDayClient` 与 `StubMemoryDigestClient` 的构造函数保持不变。 | 改签名会破坏 T39 的 lambda、H23 的 `Recorded` 子类、`_MidnightClient` 和 `test_memory_search_sources`。所属章节中的适配器已解决此问题。 |
| A101 | 10 | `tests/unit/engineering/test_layering_acceptance.py` 不属于任何轨，但 ch02 在 `LifeEngine.__init__` 中加入 `self.queries = ReadQueryResponder(...)`，破坏了它对赋值集合的精确断言。 | 依据 V9（`tests/` 归本轨），在期望集合中加入 `'queries'`，文件其他部分未改。 | 这是 ch02 规则强制的唯一改动；文件其余断言仍然成立。 |
| A102 | 10 | T27 守卫自身的源码怎样避免匹配自己的模式？ | 每个禁用子串、整词正则、模块名和 as2 目录路径都由字符串片段拼出；文档字符串描述该框架而不点名。 | T27 规定守卫不跳过任何文件，包括它自己；同样扫描 `tests/` 的 ch02 E1 与 ch07 I5 守卫也是如此。 |
| A103 | 11 | 任务文本说本轨负责 `AGENTS.md`，但第 08 章【D1】说 `AGENTS.md` 一个字节都不能改。是否编辑它？ | `AGENTS.md` 保持不变。 | 第 08 章是该主题的所属章节，其【D1】和「已检查、未修改」表（`AGENTS.md` 未提及 AgentSociety2、`custom/` 或 patches）已有定论；第 10 章的轨道表也只列出 13 个文件。 |
| A104 | 11 | 怎样无转录错误地执行约 70 处逐字替换？ | 用会话 scratchpad 中的一次性 Python 编辑脚本（不入库），直接从 `spec/08-design-docs.md` 读取全部替换正文（四反引号 `text` 块、10-runtime 全文、宿主模型协议全文、人物工作区一节）。编辑前核对各文件等于其 `74f1b949` 版本；每处「替换」须恰好匹配一次，每处「删除整行」须匹配所引行；写入 LF 字节。 | 规格要求替换文本逐字一致，直接从规格复制是保证这一点的最简方法。 |
| A105 | 11 | 重新生成的设计 HTML 放在哪里？ | `build_design_doc.py` 写到 `$WT/.work/docs/design/worldmock-design.html`（已被 gitignore），未新增受版本控制的 HTML。 | 第 08 章【D16】、`AGENTS.md` 和 `docs/INDEX.md` 都说该 HTML 放在 `.work`、不受版本控制。根 `CLAUDE.md` 关于提交 HTML 和 `--check` 的说法已过时（第 08 章明确指出）。 |
| A106 | 11 | 有四个链接目标属于仍在编写的其他轨，怎样确认文档门禁通过？ | 在 README、AGENTS、`docs/INDEX.md`、`docs/design`、`docs/manual`、`src` 和 `tools` 的临时副本上运行未改动的门禁脚本，仅对四个缺失的新源路径打桩，报告 0 个问题；随后删除该副本。 | 任务禁止创建其他轨所属的文件。这表明剩下的失败只来自规格分配给轨 1 和轨 3、尚未创建的文件。 |
| A107 | 8 | `test_run_starts_from_any_directory_without_preset_variables` 断言 `receipt['token_stats'] == []`。子进程回答 `start_new` 在初始检查点后发出的 plan_day 后，该响应存为 phase 为 `'response'` 的 `model_response` 事件并被 `observed_model_usage` 计入，保留空列表断言会使测试失败。 | 把断言改为桩日计划响应产生的恰好一行：`[{model: 'test-model', responses: 1, responses_with_usage: 1, input_tokens: 30, output_tokens: 9, total_tokens: 39}]`，并加注释说明回执计入了该日计划。 | 第 08 章说 `result.json` 的 token_stats 由持久化响应汇总并包含午夜日计划，H21/H26 让 `start_new` 结算日计划；夹具环境只有一人，故恰为一次响应。断言确切行比复用会同义反复的 `observed_model_usage` 检查得更多。I5 未规定 token_stats 的值。 |
| A108 | 8 | 子进程中不能导入 `tests.life.conftest`（`PYTHONPATH` 仅为 src，cwd 是临时目录，且 I5 要求项目根不在 `sys.path` 上），怎样替换 `PlanDayClient`？ | 在 `_STUB_ENTRY` 中内联最小的 `PlanDay` 类：构造签名为 `(client, identity)`，`model_name` 取自 `identity['requested_model']`，返回与 `tests.life.conftest.plan_day_response` 同形态、goals 为空的响应。替身在新运行分支中、证据行打印之后执行 `midnight.PlanDayClient = PlanDay`。 | `settle_pending_day_plans` 运行时才在 `midnight` 模块上查找 `PlanDayClient`，所以赋值该属性与进程内各处的 `monkeypatch.setattr` 效果相同。证据行之后再打补丁使 I5 证据不变，内联则避免把 `ROOT` 放进 `sys.path`。 |
| A109 | 10 | `test_layering_acceptance.py` 除了把 `queries` 加入门面赋值集合，还需要别的改动吗？ | 不需要。`:38` 处的单行改动保持原样，其他都不改。 | ch02 `:223` 只新增 `self.queries = ReadQueryResponder(self.context, self.decisions, self.actions)`。其余断言（无基类、公开方法名、私有方法 `{__init__, _life_snapshot}`、不超过 600 行、已退役的 mixin、`NEW_PRODUCTION` 构造检查）对工作树 `engine.py` 仍成立，也没有规格章节要求此文件做别的改动。 |
| A110 | 12 | 24 条新台账条目放在哪里？只有 ch09 写了「放在末尾」。 | 24 条全部按章节顺序（01、02、03、04、05、06、07、09）追加到 `MUTATIONS` 末尾，夹在分节注释 `# ---- 自有决策循环：…… ----` 与 `# ---- 自有决策循环结束 ----` 之间。注释避开了被禁止的框架名。 | 这是最简单的放法，与 ch09 T49 一致。它沿用台账现有的分节注释惯例，并让 T27 文本守卫保持干净。 |
| A111 | 12 | `env_close_leaves_cached_clients` 改名：原地替换还是追加？ | 原地替换（位置不变，仍在 第五章 分节内）。 | T47 是对现有条目的改名加重新指向，不是新增；条数保持 298-9+24=313。 |
| A112 | 12 | 「删除一次调用」类变异（`checkpoint_skips_midnight_tasks`、`resume_skips_midnight_tasks`、`model_client_close_leaves_the_network_client_open`）的替换文本是什么？ | `'await self._settle_midnight_tasks()'` -> `'pass'`（在 `_checkpoint` 和 `start_resume` 内），以及 `'await self._client.close()'` -> `'pass'`。 | 这与台账现有惯例一致（如旧的 `'await close_cached_async_clients()'` -> `'pass'`），在 try 块内能编译，且恰好删去所点名的那次调用。 |
| A113 | 12 | 规格留给实现者决定的锚点。 | clock_advances：`if not after.ready_agent_ids and after.next_event_at is not None:` -> `if after.next_event_at is not None:`；continuation_end：`messages, self._budget = end_continuation(...)` -> `_, self._budget = end_continuation(...)`；correction_paired：删去 `thinking_enabled(self._last_request) and `；call_identity：`{turn:04d}` -> `{turn - 1:04d}`；sdk_retries：`client=self._client, max_retries=0,` -> `...max_retries=2,`；rate_limit：`elif isinstance(error, RateLimitError):` 增加 `or "try again in" in str(error).lower()`；draft_keys：`if set(request) != _REQUEST_KEY_SET:` -> `if not _REQUEST_KEY_SET <= set(request):`；identity：`m_` 检查 -> `if False:`；first_view：`else None` -> `else hashlib.sha256(b"").hexdigest()`（`life_memory` 已导入 `hashlib`）；retired_key：在返回的字典中加入 `, "consolidated": False`；base_workspace：`if base and name in BASE_WORKSPACE_ONLY:` -> `if False:`；memory_before：`... in BASE_MEMORY_TEMPLATE_SHA256:` -> `... is not None:`；side_python：`str(side_python(root))` -> `str(Path(sys.executable))`（`sys` 与 `Path` 已导入）。ch02、ch06、ch07 采用规格自己建议的锚点和替换。 | 每处替换都是恰好造成章节表所述破坏的最小改动。每个锚点在其符号内只出现一次，变异后的模块仍能编译（已用脚本检查）。 |
| A114 | 12 | `runner._prepare_resume` 不再导入 `worldmock.runtime.framework.setup`（轨道 1 已删除），但没有章节列出删除对应的 `DELAYED_IMPORTS` 条目。 | 删除了该条目。 | V8 和 T46 要求 `DELAYED_IMPORTS` 与真实的函数级导入完全一致。轨道 1 的交接也要求删除它。 |
| A115 | 12 | 修改后 `DELAYED_IMPORTS` 的键顺序如何？ | 现有条目保持原顺序，新条目插到排序后的位置。唯一一对现有的未排序条目（`CounterfactualRequest.__post_init__` 的 `identity` 在 `decisions.results` 之前）保持原顺序。 | 这样差异最小。顺序对守卫没有影响。 |
| A116 | 12 | 改名后的门面测试 `test_only_run_host_can_import_facade` 写成什么样？ | 构造含 `worldmock.simulation_engine.engine`、`worldmock.agents.life_agent` 和 `worldmock.runtime.lifecycle.run_host` 的合成包，后两者都导入 engine；`violations(tmp_path, {})` 必须包含 `'Facade consumer: worldmock.agents.life_agent'`，且不得包含 `'Facade consumer: worldmock.runtime.lifecycle.run_host'`。另加了中文 `@brief` 文档字符串。 | 这正是 ch02 E4 和 ch07 I14 描述的两条断言。空登记表让无关的过期登记问题不进入结果。 |
| A117 | 12 | 新的 `OWNERS` 行放在什么位置？ | `react_loop.py` 放在 `prompt_schemas.py` 之后，`state/model/exchange.py` 放在 `state/model/memory.py` 之前；`MEMORY_UPDATE_KEYS` 追加到 `memory_evidence.py` 的元组末尾。 | 这样 `OWNERS` 按路径保持有序。元组内部的顺序对扫描没有意义。 |
| A118 | 12 | 除 `py_compile` 之外还做了什么验证（任务要求「用脚本」验证锚点）？ | 用锁定的解释器从会话 scratchpad 运行静态脚本：对全部 313 条运行 `tests.mutation_runner.mutated_source` 加 `tests.acceptance_guard.resolve_problem`；用 `test_import_graph` 自己的 modules/imports/violations 比对 `DELAYED_IMPORTS` 与真实局部导入；`single_source_violations`；`_style_violations`。还把每个变异符号和每个杀手函数体与 `74f1b949` 做了 diff，未运行 pytest、门禁或任何测试会话。 | V8 要求锚点在其符号内唯一，且 `DELAYED_IMPORTS` 不多不少。只有这些 AST 层面的检查能确认这一点，且都不执行测试。 |
| F01 | 1 | H8 的评审意见中两条要求用规格的 `_one_hour` 窗口（08:00-09:00），一条改提 23:00-00:00 窗口。用哪个？ | 采用 `_fresh(tmp_path, mutate=_one_hour)`，并删除 `_midnight_hour` 辅助函数。 | H8 的测试表行（`01-run-host.md:640`）指定与 H10 相同的一小时新运行。评审者的探针显示该窗口得到 `step_count` 4、5 个检查点和 1 个已建代理，两条断言都成立，只推进时钟的步也包括在内。 |
| F02 | 1 | 让 `clock_advances` 变异经由断言失败有两种办法：记录 advance 但不转发，或用一个抛 `AssertionError` 的桩。用哪种？ | 用一个桩：向 `advanced` 追加并抛出 `AssertionError('RunHost advanced while agent 1 was still ready')`。它直接设在引擎实例上，并在第二步之前于 finally 块中弹出。 | 无论宿主在 advance 之后做什么，它都在第一次错误调用时失败。该文件已用过直接实例属性加 `vars(engine).pop` 的模式（`test_close_keeps_engine_and_client_failures`）。 |
| F03 | 1 | 在 `_prepare_new_run` 中，移动过来的 `person_inputs` 导入放在哪里？ | 放在 `agents = ...` 之前、`RUN_STARTED` 之后，并附一行说明位置的中文注释。`_prepare_resume` 中的导入也加了对应注释。 | 这遵照评审意见的要求和规格行 `01-run-host.md:545`（结束段），也让模型客户端调用链不会在准入拒绝之前加载。 |
| F04 | 2 | 三个新用例写成独立测试，还是扩展规格已点名的测试？ | 扩展规格已点名的测试：`test_read_query_answers_task_recall_and_memory_search_only`（E6）、`test_read_query_requires_exactly_one_frozen_observation`（E7）和 `test_checkpoint_rejects_an_incomplete_agent_snapshot_set`（E21）。 | 第 02 章测试表把 E6、E7、E21 正好映射到这些节点 id；扩展它们保持台账映射不变，也不增加规格未给出的名字。每条评审意见都允许两种写法。 |
| F05 | 2 | E7 的两次观察用例需要绑定一个目标。如何布置而不重复代码？ | 在 `test_read_query_runtime.py` 中加一个小的模块辅助函数 `_goal(engine)` 布置目标 歇一歇，E6 和 E7 测试都用它。E7 的零观察断言不变；因 observe 会写事件，事件序列基线在两次 observe 调用之后重新取。 | 评审意见点名用 `stage_day_plan` 布置目标 歇一歇，而 E6 测试里已内联有同样的布置代码，一个辅助函数即可消除重复。 |
| F06 | 3 | 两个新的杀死探针的测试叫什么？第 03 章测试表只点名了现有的 A17/A18 测试。 | `test_a_failed_send_carries_the_failed_sends_the_client_collected`（A17）和 `test_a_response_after_a_failed_send_carries_that_send_in_its_evidence`（A18），都紧接在 `test_a_cancelled_send_records_no_failure_evidence` 之后。 | 规格没有为额外测试给名字。这两个名字沿用该文件现有风格，并说明各自检查的行为。 |
| F07 | 3 | `test_a_failed_send_records_the_model_service_stage_and_reraises_the_original` 中现有断言 `closing.transport_attempts == ()` 是否保留？ | 原样保留。 | 该测试的假客户端直接抛错，从不经过 `ModelClient`，因此收集器为空是正确结果。非空情形由新测试经真实客户端覆盖。 |
| F08 | 5 | 两条评审意见用不同方式修同一批线路故障：改 `model_client_fixtures.py` 里的 `_BUSY`（意见 3），或在 `test_model_client_wire.py` 中加 `_FAILURE`（意见 1 和 4）。用哪种？ | 在线路测试中加 `_FAILURE`，所有非 200 应答都传它（包括 429 的，与 `fault_probe.py` 相同），`_BUSY` 保持规格所写。 | 夹具因此与规格 04:437 的描述完全一致，无需记录偏差；线路测试的服务器像探针服务器那样应答（故障场景的期望值 表冻结的正是探针服务器的结果），且改动更小。 |
| F09 | 5 | M2 的日志器清单列了 `'asyncio'` 和 `'litellm'`。现有的 `'LiteLLM'` 日志器是否保留？ | 三个都监视：`'asyncio'`、`'litellm'` 和 `'LiteLLM'`。 | 评审意见认为保留 `'LiteLLM'` 合理：它是 litellm 实际的详细日志器，而基于 `__name__` 的日志器会传播到 `'litellm'`。多监视几个日志器只会让「不出现」检查更严。 |
| F10 | 5 | M8 和 M10 的探针杀手写成新测试，还是在现有行测试里加断言？ | 写成新测试：`test_counted_transport_waits_are_two_times_two_to_the_n`（M8）和 `test_retry_after_date_without_a_zone_is_read_as_utc`（M10）。M12 的杀手按评审意见给的名字叫 `test_nested_collectors_restore_the_outer_one`。 | 现有行测试保持规格测试表所述内容不变，每个新测试钉住一条规则子句并以它命名。M10 用例采用评审意见验证过的墙钟写法，而不是替换 datetime 的补丁变体。 |
| F11 | 6 | 失败决策测试按评审意见用 `engine_factory()` 建引擎，还是用 `engine_factory(mutate=_evening)`？ | 用 `engine_factory(mutate=_evening)`，随后 `stage_day_plan(engine, submitted=True)`，并在 `tmp_path/people/agent_0001` 上运行 `host_run`。 | 本文件中 `_woken` 已表明，在 `_evening` 下这同一个第一步（`stage_day_plan` 之后在 `host_run` 上执行 `run.start('interact', '家', '测试休息')`）会到达 `engine.submit_action`。评审意见允许任一种布置，两者检验同一条规则。 |
| F12 | 6 | W15 的断言放在本文件，还是轨道 7 的 `test_run_host_failures.py`？ | 放在本文件 `test_memory_retirement.py`，该文件归轨道 6 所有。 | 硬规则 V5：只改本轨道拥有的文件。 |
| F13 | 6 | 失败决策测试除评审意见的两项检查外，还要断言别的吗？ | 另外断言 `len(run.model.requests) == 1`。 | 这表明失败来自模型应答之后的轮次循环，而不是来自观察，仍在所测规则之内。 |
| F14 | 7 | 同一变异的两条评审意见不一致：一条说 `pytest.raises(Exception)`，另一条说 `pytest.raises(BaseException)`。用哪个？ | 两个测试都用 `pytest.raises(BaseException)`，并保留其后的精确检查（`type(error) is ProtocolError` 与 `caught.value is error`）。 | `BaseException` 能捕获 `Exception` 捕获的一切且更多，所以抛出非 `Exception` 的 `BaseException` 的变异也会以断言失败。`tests/integration/runtime/test_run_host.py:477` 已用同样写法，未变异时的行为不变。 |
| F15 | 7 | 三个测试里的「无宿主日志」断言怎么写才不重复列表推导？ | 加一个小测试辅助函数 `_host_records(caplog)`，返回日志器名为 `'worldmock.runtime.lifecycle.run_host'` 的 caplog 记录；三个测试都断言其结果为空。 | `run_host.py` 用 `logging.getLogger(__name__)` 建日志器，正得到该名字。未变异代码在代理故障路径上不从 run_host 记日志（那里仅有的日志调用在 `_abort_round` 和 `_record_failure` 的失败分支、`close` 及启动时检查中），所以新断言对真实代码成立。 |
| F16 | 8 | `tools/analysis/memory_categories.py` 不在第 10 章表的任何轨道行里。轨道 8 可以改它吗？ | 可以。只应用评审意见规定的那一行修复，文件其余部分不变。 | 集成者的 V22 修正轮把这条意见分给轨道 8。轨道 8 拥有第 07 章 [I14]，它使 `tools/analysis` 成为覆盖率根目录，而此缺陷阻断该门禁；没有别的轨道认领此文件。 |
| F17 | 8 | 这项修复需要新的行为测试吗？ | 不加新测试。现有的 `tests/unit/evaluation/test_memory_categories.py::test_cli_prints_or_writes_the_table` 已检查写出的文件，此前只因 CRLF 写入而失败。 | 评审意见点名该测试就是杀死此缺陷的测试。修正轮要求的是根因修复，而不是再加一个测试。 |
| F18 | 8 | 是否重跑 `uv lock` / `lock_check.py`？ | 不重跑。 | 本次修正轮没有改依赖文件（`pyproject.toml`、`uv.lock` 和 requirements 锁文件均未动），所以先前的锁检查结果仍然有效。 |
| F19 | 9 | 评审意见建议让 `_workspace_member` 返回（`bytes` 或 `None`，`rewritten`）二元组；但第 09 章接口表给出的签名是 `_workspace_member(name, raw, context)`、返回 `bytes` 或 `None`，且 `tests/dataset_acceptance.py` 的变异 `base_workspace_keeps_framework_members` 针对其 `if base and name in BASE_WORKSPACE_ONLY:` 一行。规则标志该如何报告？ | `_workspace_member` 及其签名不变。新增私有辅助函数 `_rule_rewrote(name, raw, listed) -> bool`，仅在规则启用且为 base 一侧时由 `_workspace_listing` 调用：成员被丢弃（`listed is None`）、`MEMORY.md` 原始字节含 `b"\r\n"`、或 `memory/episodes.jsonl` 的 `listed != raw` 时返回 True，其他成员返回 False。 | 规格接口表是权威，而评审意见写的是「例如」。这样恰好统计评审意见要求的内容，不改规格声明的签名，也保留变异的目标行。 |
| F20 | 9 | base 一侧非空的 episodes 文件，若 `_base_episodes` 返回的结果与原文逐字节相同（已是规范 LF、无常量键、id 为 head 形式），算作被改写吗？ | 不算。只有重建后的字节与原始字节不同时才算。 | T16 统计成员 被改写（changed）的快照。这与 `MEMORY.md` 的标准一致：只有 CRLF 替换改变了字节才计入。 |
| F21 | 9 | `id_suffix` 用例的显式 base id 怎么设？ | 用现有覆盖途径 `_base_entry(entry, id="m_00000000_0000012")`。`_base_entry` 已把 `**changes` 放在最后合并，显式 id 会被保留，辅助函数无需改动。 | 按评审意见写成 `{**entry, "id": ...}` 传入会得到 `m_00000000_00000000_0000012`，它在 `.match` 和 `.fullmatch` 下都不一致，杀不死该探针；覆盖写法能杀死它。 |
| F22 | 10 | `_host` 打开工具面时用 `turn_tools(2, False)`（意见 1）还是 `turn_tools(16, False)`（意见 3 和 6）？ | `turn_tools(16, False)`。 | 两者给出相同的非最后一轮工具面（世界工具加查询工具），结果不变。16 是真实循环的第一轮（`MAX_REACT_TURNS`），其他迁移过来的宿主校验调用方也用它：`test_host_feedback.py:96`、`test_existing_relationship_subject.py:30`、`test_grounded_targets.py:30` 和 `test_activity_schema.py:55`。 |
| F23 | 10 | 规格第 10 章第 36 行（目录与变量）把未变异基线运行写为 480 s（`:16`）。提到 1200 是否与规格冲突？ | 在测试文件中提到 1200，规格文本不变。 | 该表行描述的是当时的基线（「今天 298 条 ... 上限 480 秒」），不是行为规则；杀手集合此后已增到 359 个节点／313 个变异，普通单进程运行需 432 s，480 s 在变异运行器子进程中无法满足。文档不属于轨道 10，集成者可视需要更新该描述行。 |
