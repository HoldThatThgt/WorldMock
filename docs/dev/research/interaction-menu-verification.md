> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/interaction-menu-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 交互菜单验收记录

> 状态：COMPLETE；本特性实现、文档同步、完整工程门禁与两组 Uncle Bob 复验均已完成，无延期项。
> 日期：2026-09-20。
> 权威合同：[交互菜单特性规格](../SPEC-interaction-menu.md)。
> 证据范围：本记录固定于合并前的独立特性快照；合入品类契约后的源码与门禁见
> [主线合并验收记录](interaction-menu-main-verification.md)。

## 范围与证据边界

地点与人物分别声明工具及活动类型，观察取交集，人物把它投影到实际工具与参数，环境在共同结算时
再次验证；暂停恢复、基本活动控制、候选不变、实验身份与文档同步都属于本次完整交付，没有特性延期项。
正式场景显式初始化为全部既有权限、人物显式 `null`；这不擅自替实验作者制定办公室或人物政策。
不同地点与人物的限制通过真实配置和行为测试验证。

开发前主工作区 HEAD 为 `4cf70db5bdf61776a9562eb9d4623e32877880d7`。
开始时已有 `docs/INDEX.md`、`docs/design/01-architecture.md`、`docs/design/README.md` 的修改，
以及尚未跟踪的交互菜单提案。初始文本与摘要保存在 `.work/interaction-menu/initial/`
和 `.work/interaction-menu/initial-state.json`。并发会话新增的地图提案与导航也被保留，
它们只作为验证快照的文档上下文，不属于本特性的开发成果。主工作区没有为审查而提交、暂存或回退。

测试使用完整场景加载、资源目录、LifeAgent、LifeEnv、LifeEngine 与 SQLite。
人物循环只替换远程模型传输，捕获实际请求与后续消息；没有调用付费模型。
全量测试的 LabelKit 消费者使用既有本地 CLI 与本地测试服务，历史资金锚定数据只读复制。
这些证据不代替全项目正式十二周运行、三次隔离盲评或完整自然日 LabelKit 交付。

## 固定源码与复现条件

| 证据 | 固定值 |
| --- | --- |
| 最终隔离快照 | `443119f8c86a3f7bb59d7337d112e95e058af39b` |
| 项目运行代码身份 `runtime_code_sha256` | `f39439b43d61fce098c76c3fa779fe1ab00b59de8c3d4df8a49b182861145e3e` |
| 覆盖率证据清单 | snapshot `008c9d69-1278-4079-b65b-c61b06b9517d`；清单 SHA-256 `e763714930ab3ee4a5aff6bb723a9a8f10a9a39617ce24abec2299aaaeb784a7` |
| AgentSociety 2 子模块 | `e41550957a50398e08570f10ecd9cdb3fc9dd37c` |
| LabelKit 子模块 | `d0afd606decbc3f4aa64c64ef5339586120c5a7a` |
| 静态 system 模板哈希 | `59f22d2f8aece81545169f2234446420ca8a0f5e4454e03b3f432816cea1ba5a`，与开发前相同 |
| 静态工具模板哈希 | `6bc60d84d4fefbd6fc8aaa81d2be97caf5ad8e6b1da12b30aecb2c73728d609a`，删除办公室散文后重新固定 |
| 文件与场景快照 | `.work/interaction-menu/delivery-snapshot.json` 保存逐文件 SHA-256、代码身份和只读锚定数据身份 |

隔离目录为 `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-interaction-menu-review-j3d4uxt3/candidate-final`。
运行 Python 使用主工作区既有 `.venv/bin/python`，`PYTHONPATH` 明确指向隔离目录的
`src`、根目录与 `vendor/agentsociety2/packages/agentsociety2`；实际导入位置已经核对。
`UV_PROJECT_ENVIRONMENT` 指向既有环境，`UV_NO_SYNC=1`，`LITELLM_LOCAL_MODEL_COST_MAP=True`；
每次覆盖率数据使用独立 `COVERAGE_FILE`，避免并发会话互相覆盖。

特性验收时的主工作区与该快照的 `runtime_code_sha256` 相同；生产文件、场景及测试文件已逐一比较 SHA-256，
均没有漂移。文档结果登记不改变被测生产源码。

## 条款与行为证据

下表的文件名分别位于 `tests/life/` 或 `tests/integration/`；所有测试都检查可观察行为。

| 规格要求 | 真实行为断言与入口 | 验收结果 |
| --- | --- | --- |
| 显式声明与准入 | `test_interaction_menu.py`：两个必填字段、全部九个保护项按规格字面值逐项删除、未知/重复/空值/坏类型；LifeScenario 与 RuntimeScenario 分别核对字段、空地点值和精确地点覆盖；正式三场景加载 | 已通过定向回归 |
| 地点与人物交集 | `test_location_and_person_each_remove_the_other_sources_exclusive_permissions` 让两侧各有独有工具和类型，只接受排序交集；多人运行观察各自菜单且不泄漏原始声明 | 已通过定向回归 |
| 观察冻结与切换 | `test_real_person_requests_use_current_location_menu_and_next_round_results` 捕获真实人物循环，实际出行到办公室后下轮工具变化；同状态观察菜单与落盘观察一致，既有决策上下文测试守护单轮只读冻结 | 已通过定向回归 |
| 模型与本地一致 | integration 菜单测试捕获 `_model_request` 中实际工具、基础类型、定向类型/目标与浏览枚举；分别验证工具允许但 travel/consume 类型禁止；本地及直接宿主派发均拒绝且无环境调用 | 已通过定向回归 |
| 暂停恢复与控制 | 同时覆盖禁止活动类型、禁止对应启动工具、两种暂停活动共用恢复枚举时按请求身份精确校验；真实观看暂停、出行后恢复拒绝、取消成功且进度不丢；chat/call 的环境与宿主都按社交启动工具判断；撤掉新开游戏资格仍可派发当前游戏合法命令 | 已通过定向回归 |
| 环境不可绕过 | `test_direct_calls_are_rejected_without_domain_effects` 直接提交被禁工具/活动/真实品类；实际商品已发现且商家营业仍不能越权扣款；被禁工作提交不进入验收准备或模型调用 | 已通过定向回归 |
| 错误优先级与幂等 | 不存在品类即使下钻工具关闭仍为 `BROWSE_CATEGORY_NOT_FOUND`；真实品类被禁为 `INTERACTION_MENU_DENIED`；重放不增拒绝计数，追加证据明确 `reused=true` | 已通过定向回归 |
| 提示与损坏观察 | 菜单缺失/损坏/无序时宿主失败关闭并记录英文错误；递归黑名单拒绝两种菜单字段误入提示；暂停活动 kind 损坏必须有英文错误日志 | 已通过定向回归 |
| 候选合同保持 | 不同菜单下逐项比较 scene、facts、activity_summary、products、affordance_contract；真实 PresentationWindow 的候选、排除清单与原因计数完全相同；覆盖全部注册表内容/生活活动品类 | 已通过定向回归 |
| 身份与恢复 | 地点或人物声明变化均改变场景字节哈希并拒绝打开；恢复在复制子分支前拒绝漂移；静态 system 保持、旧工具模板身份拒绝 | 已通过定向回归 |
| 工程与文档 | 全量根测试、严格覆盖率、风格、文档路径与 Markdown 链接、独立一致性审查；有效源码变异与恢复证据见下节 | 已通过，最终未解决存活变异为零 |

行为文件为 `tests/life/test_interaction_menu.py`、`test_interaction_menu_runtime.py`、
`test_interaction_menu_invariants.py`、`tests/integration/test_interaction_menu.py` 和
`test_interaction_menu_roundtrip.py`。既有场景、候选、控制、决策上下文与恢复套件同时作为回归边界。

## 实测门禁

| 检查 | 实际结果 |
| --- | --- |
| 开发前场景、Schema 与运行清单基线 | 86 passed |
| 最终生产源码、补强测试前的固定快照 | 2958 passed，零跳过；分支 98.79%，未覆盖公共接口 0，完整 coverage gate PASSED |
| 独立审查补强后的定向测试、风格和文档测试 | 108 passed |
| 最终快照完整门禁 | 2985 passed，零跳过，24 个既有警告；分支 98.79%（5979/6052），未覆盖公共接口 0，coverage gate PASSED |
| 文档路径与引用 | documentation gate 扫描 49 份文档，损坏引用 0；本次 23 份变更文档的 308 个本地 Markdown 链接全部可解析 |
| 合并前工作区与被测快照 | 24 份生产、场景及测试文件的 SHA-256 完全一致；两处运行代码身份均为上表固定值，`git diff --check` 通过 |

完整门禁执行 `python scripts/coverage_gate.py`，内部实际运行 `uv run coverage run -m pytest -q`
并重新生成覆盖率 JSON。原始输出保存在 `.work/interaction-menu/final-coverage-gate.log`；
运行环境按上一节固定。文档门禁单独执行 `python scripts/documentation_gate.py`，
另核查本次变更 Markdown 中的相对文件和目录链接。

首次隔离运行因检出不含被忽略的 LabelKit CLI 环境，产生十二个依赖失败，并因未复制只读历史锚定数据
跳过四个测试；补齐既有执行依赖和数据后全量重跑通过。没有放宽断言、修改业务实现或把这次失败计为通过。

## Uncle Bob 行为变异审查

两组审查都从干净提交建立独立 detached worktree，先运行预声明测试取得通过基线，再逐个修改生产代码。
每次还原后检查 `git diff --exit-code` 与完整状态，不在用户工作区施加临时变异，也不让变异工作树共用源码。

首轮审查暴露了真实的测试证明缺口：单个地点菜单为空、保护项不完整、人物覆盖地点、活动类型字段缺失、
不存在品类错误优先级、恢复启动工具缺失、恢复使用错误活动身份，以及旅行/消费只禁类型。
父任务补齐独立断言后，审查者使用新的干净快照重施相同行为变异；历史存活结果保留在各自账本，
不把初次未检出改写为一次通过。新增异常日志也单独接受“删除日志”变异验证。

每个因果失败、无效变异和清理边界分别保存在
[环境侧 Bob 报告](interaction-menu-bob-environment.md)与[宿主侧 Bob 报告](interaction-menu-bob-host.md)。
宿主侧二十四个、环境侧三十二个有效语义变异最终全部被检出，两组均无未解决存活项或 inconclusive。
宿主固定 oracle 最终为 244 passed，环境固定 oracle 为 87 passed；工具字段必填的隔离补验另有
58 passed 基线。宿主三个等价尝试和环境两个未隔离尝试明确标为 invalid，不计入有效变异。
环境补审还分别验证人物 `null` 被误当空集合、缺保护项被自动补齐、观察菜单字段及证据被遗漏；
三项均由对应行为断言检出。两组计数分别对应各自范围，不外推为整个项目的穷尽覆盖率。

初次和后续快照按被审查生产路径逐一比较 SHA-256，只有新增暂停类型错误日志属于生产修复，且单独接受
删除日志的变异验证；其余存活项靠补强测试检出。特性最终快照与当时主工作区的运行代码身份一致，
没有把旧提交的未验证源码直接视为当前完成证据。

所有变异已经逐项还原并用 diff 与完整 status 核对。Git 拒绝普通移除包含子模块的临时工作树，
因此没有强制删除；每份报告记录了保留路径与已还原状态。临时登记属于隔离审查仓库，不在主工作区。
环境报告另外记录了一个在 baseline 前子模块初始化失败的临时目录；它没有施加变异或参与验收结论，
因普通清理拒绝也保留原状，没有把该目录计入“审查工作树已干净还原”的范围。

## 实际修改与设计一致性

| 范围 | 实际文件与结果 |
| --- | --- |
| 共享声明与判定 | 新增 `src/worldmock/life/interaction_menu.py`、`menu_execution.py`；修改 `scenario.py`、`semantics.py`，只有显式声明与现有闭集，无兼容层或迁移 |
| 观察、结算与恢复 | 修改 `src/worldmock/life/observe.py`、`engine.py`、`narration.py`、`src/worldmock/runtime.py`；先拒绝再产生领域副作用，恢复先校验场景身份再复制 |
| 人物宿主 | 新增 `custom/agents/life_menu.py`，修改 `life_agent.py`；共享规则驱动 Schema、本地校验与派发，静态办公室偏好散文删除 |
| 正式配置 | 修改三份生活场景；`scenarios/evaluation.json` 是评估配置，未加入生活菜单 |
| 行为验收 | 新增上述五份菜单测试，更新六份既有观察/恢复夹具；未放宽原合同断言 |
| 现行设计 | 同步总体架构、资源、模拟、记录、配置、输入输出及设计导航；交换引擎职责未变化，`docs/design/02-exchange-engine.md` 仅核查 |
| 规范与导航 | 同步 CONTRACTS、EXPERIMENT、VERIFICATION、决策上下文/人物/生活运行规格、INDEX、实现状态与追溯矩阵；原提案让位冻结特性规格 |
| 研究与证据 | 成熟机制核查、当前验收记录与两组 Bob 报告各自标明规范权威和实测边界 |

独立文档审查已确认：配置文档不虚构独立菜单报告字段；幂等描述允许带 `reused=true` 的重放证据；
宿主协议错误与环境业务拒绝分别描述；修改清单不再把只核查的交换引擎列成已修改文件。

本次同时纠正两项事实：原提案所称十二个动作实际为十五个；`game_session` 在下钻分区时必须优先
识别为游戏，不能因同时出现在内容声明中而误进空内容池。真实游戏下钻测试守护后一项。
既有候选资格的 `location_kinds` 能力集合没有进入正式地点输入，这项历史落差已在现行文档明确，
与本次使用的单值 `locations[].kind` 不同，不能拿菜单验收替代它。
