> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/interaction-menu-bob-host.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

## 状态与结论

**状态：COMPLETE**

本审查以 [`SPEC-interaction-menu.md`](../SPEC-interaction-menu.md) 的“观察与人物工具”
“实验身份与恢复”，以及提示递归字段黑名单要求为唯一产品行为权威。范围包括 LifeAgent 收到冻结观察后
构造的实际模型 Schema、本地决策校验、直接宿主派发、只读和动作后阶段、暂停与已有活动控制、恢复前身份
校验、代码与静态模板身份，以及 `narration.py` 的递归黑名单。共享菜单计算、环境权威重验、业务副作用与
幂等重放由独立的
[`interaction-menu-bob-environment.md`](interaction-menu-bob-environment.md) 审查，本报告不重复认领。

审查共设计二十七种不同语义突变，其中二十四种能独立产生可观察的规格违例，三种在当前分层不变式下与
原实现行为等价，判为 invalid。初次测试有四种有效突变存活；追加边界检查时又发现一种社交恢复突变存活。
父任务补充行为断言后，五种存活突变均被同义重施杀死。最终枚举范围内没有未解决的存活突变，也没有
inconclusive 结果。这里不报告 mutation score，也不把这组定向枚举外推为整个规格或整个项目的穷尽覆盖率。

代码检查还发现一个实现偏离：损坏的暂停活动类型会失败关闭，但初始提交没有在抛错前写英文错误日志。
修复提交增加日志；删除该日志的突变被三个坏类型反例杀死。最终快照未发现本范围内仍缺失或反向实现的条款。

## 审查快照

| 用途 | 干净 caller checkout | 提交 | 生产差异 |
| --- | --- | --- | --- |
| 初次映射与突变 | `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-interaction-menu-review-j3d4uxt3/candidate` | `7e89c715aa75fa7d928f0aab173c0e122f22fd0a` | 初始候选 |
| 四项缺口、日志与控制边界复验 | `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-interaction-menu-review-j3d4uxt3/candidate-acceptance` | `1cf2228ae8afdb48f04232ae63ebcfb9bf9e09d2` | 仅 `life_agent.py` 增加损坏暂停类型日志 |
| 社交恢复与游戏现有控制最终复验 | `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-interaction-menu-review-j3d4uxt3/candidate-final` | `443119f8c86a3f7bb59d7337d112e95e058af39b` | 与上一快照生产源码相同，仅增强测试 |

三个 caller checkout 在审查开始和结束时均由 `git status --porcelain=v1 --untracked-files=all` 与
`git diff --exit-code` 证明干净，HEAD 与表中提交完全一致。对五个范围内生产文件逐一计算 SHA-256：
`life_menu.py`、`interaction_menu.py`、`runtime.py`、`narration.py` 在三个快照完全相同；
`life_agent.py` 只在初始快照到复验快照间变化，复验与最终快照相同。这个对照使后续“由测试增强杀死”
和“由生产日志修复杀死”可以分开判断。

## 固定 oracle 与 baseline

三个 detached worktree 都从对应 caller 的干净提交创建并初始化固定 gitlink。解释器统一为
`/Users/atishoo/Project/WorldMock/.venv/bin/python`。测试环境使用 worktree 自己的 `src`、仓库根目录和
AgentSociety2 子模块；导入探针确认 `custom.agents.life_agent`、`worldmock` 与 `agentsociety2` 都来自
相应 detached worktree。API 变量只是离线测试配置占位，本审查没有发起网络或付费模型调用。

宿主固定 oracle 的最终快照命令如下；初始与复验快照只把三个绝对 worktree 路径替换为各自路径，测试文件
集合保持不变：

```bash
env PYTHONPATH=/tmp/worldmock-bob-host-final.SQsoBA/src:/tmp/worldmock-bob-host-final.SQsoBA:/tmp/worldmock-bob-host-final.SQsoBA/vendor/agentsociety2/packages/agentsociety2 LITELLM_LOCAL_MODEL_COST_MAP=True UV_NO_SYNC=1 AGENTSOCIETY_LLM_API_KEY=test-key AGENTSOCIETY_LLM_API_BASE=https://api.deepseek.com/beta /Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_interaction_menu.py tests/integration/test_interaction_menu_roundtrip.py tests/integration/test_grounded_targets.py tests/integration/test_life_agent_protocol.py tests/integration/test_agent_branch_coverage.py tests/integration/test_activity_schema.py
```

| 快照 | detached worktree | baseline |
| --- | --- | ---: |
| 初始 | `/tmp/worldmock-bob-host.xf7MCG` | 233 passed in 4.32s |
| 复验 | `/tmp/worldmock-bob-host-acceptance.8MpCHk` | 240 passed in 4.07s |
| 最终 | `/tmp/worldmock-bob-host-final.SQsoBA` | 244 passed in 3.93s |
| 最终全部突变恢复后 | 同上 | 244 passed in 3.38s |

恢复身份固定 oracle 在初始和复验快照均为 **6 passed**，分别耗时 2.20s 与 2.29s：

```bash
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_interaction_menu_roundtrip.py::test_resume_rejects_menu_changes_before_copying_a_branch tests/integration/test_interaction_menu_roundtrip.py::test_static_prompt_stays_and_old_tool_template_cannot_resume tests/integration/test_grounded_evidence.py::test_runtime_code_identity_includes_life_agent_content tests/integration/test_host_branch_coverage.py::test_prepare_resume_restores_branch_and_logs_resume tests/integration/test_host_branch_coverage.py::test_require_runtime_identity_rejects_diverged_manifest
```

提示递归黑名单固定 oracle 在初始和复验快照均为 **2 passed in 0.01s**：

```bash
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/life/test_interaction_menu_runtime.py::test_menu_support_cannot_be_copied_into_prompt_facts
```

新增异常日志 oracle 在复验快照为 **3 passed in 1.52s**：

```bash
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_interaction_menu.py::test_broken_paused_kind_fails_closed_and_logs
```

每个突变先对目标文件运行 `py_compile`，再运行预先固定的相应 oracle。只有完成导入并抵达目标行为断言的
失败才记为 killed；语法、导入或启动失败不计。突变应用器、可达性对照探针和逐次原始输出位于
`/Users/atishoo/Project/WorldMock/.work/interaction-menu/bob-host/`。

## 规格、实现与 oracle 映射

| 行为要求 | 生产入口 | 主要 oracle |
| --- | --- | --- |
| 非只读观察必须有严格、排序后的有效菜单，损坏时记录英文日志并失败关闭 | `life_menu.parse_interaction_menu` | `test_broken_observation_menu_fails_closed_and_logs`、两个排序反例 |
| 工具名轴与原状态、阶段和预算门控取交集，并传入实际模型请求 | `LifeAgent._prepare_life_action_state`、`filter_action_models`、`LifeAgent._model_request` | `test_actual_model_request_projects_tools_kinds_targets_and_categories`、协议预算用例 |
| 基础活动 Schema 只含允许的 `wait`、`sleep` 与已发现生活活动身份 | `basic_activity_types`、`bind_basic_activity_schema` | 基础活动本地/直派反例、生活活动身份绑定、严格 Schema 用例 |
| 定向活动同时收窄活动类型与本轮真实目标，不能借共享工具绕过 | `filter_targeted_kinds`、`filter_targets`、`menu_action_error` | `test_targeted_kind_cannot_bypass_menu_through_shared_tool`、实际请求捕获 |
| 浏览只保留真实存在且菜单允许的品类 | `filter_browse_categories`、`_bind_browse_categories` | 实际请求捕获、既有未知品类领域错误 |
| 出行与消费同时要求工具名和对应活动类型 | `filter_action_contracts` | `test_kind_denial_removes_permitted_travel_or_consume_tool` |
| 恢复按请求的精确暂停身份、活动类型与对应启动工具校验；社交恢复只映射社交启动工具 | `filter_activity_controls`、`_resume_kind`、`menu_allows_activity` | 暂停恢复参数用例、混合暂停身份用例 |
| 禁止恢复不删除取消；已开始活动的继续、暂停、取消、调速与游戏命令不依赖新启动资格 | `filter_activity_controls`、`_available_action_models` | 暂停取消、已有观看控制、当前游戏命令参数用例 |
| 本地决策与直接宿主派发都执行相同菜单判定，且直接派发拒绝发生在环境调用前 | `validate_react_decisions`、`dispatch_react_tool`、`_submit_life_action` | 基础、定向、旅行、消费、恢复的本地与直派反例 |
| 只读不要求世界菜单；提交一次动作后不重新开放生活动作 | `prepare_react_loop`、`_budgeted_tool_models` | `test_readonly_and_action_phases_do_not_reopen_menu_actions` 及既有协议用例 |
| 场景身份漂移必须在复制恢复分支前拒绝 | `runtime._prepare_resume` | `test_resume_rejects_menu_changes_before_copying_a_branch` |
| 恢复绑定静态工具身份与实际宿主源码，旧工具身份不能恢复 | `runtime._require_runtime_identity`、`runtime_code_sha256`、`life_tool_schema_sha256` | 旧模板恢复、宿主源码摘要、manifest 漂移用例 |
| 删除办公室固定散文只改变工具模板身份，system 模板保持 | `_life_tool_descriptions`、`life_prompt_sha256`、`life_tool_schema_sha256` | 工具指纹固定值和静态文本缺席用例 |
| 两个菜单支持字段无论嵌套深度都不能进入公开提示事实 | `narration.FORBIDDEN_OBSERVATION_KEYS`、`_walk_blacklist`、`enforce_field_blacklist` | 两字段参数化递归黑名单用例 |
| 损坏的暂停活动类型在终止准备前写英文错误日志 | `life_agent.py` 的暂停活动投影校验 | `test_broken_paused_kind_fails_closed_and_logs` |

## 初次 mutation 台账

下表的固定 oracle 是初始快照中的 233 项宿主、6 项身份或 2 项提示 oracle。结果为 passed 的三项均继续
用直接对照探针检查；它们没有制造可观察的错误行为，因此属于等价 invalid，而不是存活测试缺口。

| 表格序号 | 行为突变 | 生产路径 | 初次结果与因果证据 | 判定 |
| ---: | --- | --- | --- | --- |
| 1 | 缺少观察菜单时替换成最小保护菜单 | `life_menu.py` | 1 failed、232 passed；缺菜单反例本应抛错却未抛 | killed |
| 2 | 跳过动作模型最后一层工具名过滤 | `life_menu.py` | 233 passed；合同、目标、品类和保护控制的前置投影已产生相同工具集合 | equivalent invalid |
| 3 | 跳过定向活动类型投影 | `life_menu.py` | 233 passed；目标投影、Schema 绑定和本地校验仍移除每个禁止组合 | equivalent invalid |
| 4 | 跳过定向目标投影 | `life_menu.py` | 233 passed；活动类型投影、Schema 绑定和本地校验仍移除每个禁止组合 | equivalent invalid |
| 5 | 浏览品类不按菜单收窄 | `life_menu.py` | 1 failed、232 passed；实际模型请求泄露禁止品类 | killed |
| 6 | 基础活动 Schema 删除活动类型闭集 | `life_menu.py` | 5 failed、228 passed；实际请求、基础活动与严格 Schema 均检测到无界参数 | killed |
| 7 | 实际模型传输额外加入被禁消息工具 | `life_agent.py` | 9 failed、224 passed；捕获的请求、只读与动作后预算均出现多余工具 | killed |
| 8 | 本地决策跳过菜单精确校验 | `life_agent.py` | 3 failed、230 passed；基础、生活活动身份和恢复在本地被错误接受 | killed |
| 9 | 直接宿主派发跳过菜单精确校验 | `life_agent.py` | 2 failed、231 passed；基础活动和恢复不再抛错 | killed |
| 10 | 只读准备错误进入世界菜单路径 | `life_agent.py` | 6 failed、227 passed；只读依赖环境目录并破坏独立 finish 合同 | killed |
| 11 | 一次动作提交后重新开放生活动作 | `life_agent.py` | 24 failed、209 passed；动作后边界和多个协议/Schema 断言失败 | killed |
| 12 | 恢复 Schema 只看活动类型，不看对应启动工具 | `life_menu.py` | 233 passed；直接探针显示被禁恢复仍出现 `resume` | survived |
| 13 | 恢复校验读取第一条暂停活动类型，不按请求身份读取 | `life_menu.py` | 233 passed；混合暂停探针错误接受被禁身份 | survived |
| 14 | 出行工具忽略 `travel` 活动类型 | `life_menu.py` | 233 passed；工具开放而类型关闭时直接探针仍暴露出行 | survived |
| 15 | 消费工具忽略 `consume` 活动类型 | `life_menu.py` | 233 passed；工具开放而类型关闭时直接探针仍暴露消费 | survived |
| 16 | `play_game` 活动类型无条件允许 | `interaction_menu.py` | 2 failed、231 passed；实际模型请求和共享工具绕过反例均失败 | killed |
| 17 | 恢复复制前跳过场景哈希校验 | `runtime.py` | 2 failed、4 passed；地点菜单和人物菜单变化都不再抛 `IntegrityError` | killed |
| 18 | 恢复身份比较忽略工具 Schema 哈希 | `runtime.py` | 1 failed、5 passed；旧工具模板身份被错误接受 | killed |
| 19 | 运行代码摘要遗漏 `custom` 源码 | `runtime.py` | 1 failed、5 passed；修改 `life_agent.py` 不再改变代码身份 | killed |
| 20 | 把固定办公室娱乐偏好重新放入工具描述 | `life_agent.py` | 2 failed、231 passed；固定工具哈希和静态文本缺席断言失败 | killed |
| 21 | 从提示黑名单删除当前人物菜单字段 | `narration.py` | 1 failed、1 passed；嵌套字段不再抛 `ForbiddenObservationFieldError` | killed |
| 22 | 提示黑名单停止递归字典 | `narration.py` | 2 failed；两个菜单支持字段都穿透嵌套校验 | killed |

## 存活项、实现修复与补强复验

初次四项存活没有被隐去。每项在突变保持激活时运行独立对照探针，随后立即恢复源码；父任务在另一个
干净快照补测试，本审查没有编辑测试。复验快照还对异常日志、取消和已有观看控制增加独立反例。

| 表格序号 | 错误行为 | 可达性或缺口证据 | 补入的行为断言 | 同义复验结果 |
| ---: | --- | --- | --- | --- |
| 1 | 恢复忽略对应启动工具 | 突变时操作为 `resume,cancel`，原实现只有 `cancel` | 只撤掉启动工具，检查 Schema、本地和直派拒绝，取消仍成功 | 1 failed、239 passed；killed |
| 2 | 恢复读取错误暂停身份 | work 允许、game 禁止且请求 game 身份时，突变返回空错误，原实现返回菜单拒绝 | 共享枚举仍有恢复时按请求身份做本地和直派精确拒绝，再证明允许身份可恢复 | 1 failed、239 passed；killed |
| 3 | 忽略旅行活动类型 | 工具允许、类型禁止时，突变仍暴露工具，原实现不暴露 | 单独关闭 `travel`，捕获实际请求并检查本地、直派和零环境调用 | 1 failed、239 passed；killed |
| 4 | 忽略消费活动类型 | 工具允许、类型禁止时，突变仍暴露工具，原实现不暴露 | 单独关闭 `consume`，捕获实际请求并检查本地、直派和零环境调用 | 1 failed、239 passed；killed |
| 5 | 删除损坏暂停类型的错误日志 | 异常仍抛出，但日志为空 | `None`、空串和整数都断言失败关闭并留下英文错误日志 | 3 failed；killed |
| 6 | 禁止恢复时同时删除取消身份 | 取消选项随恢复资格一起消失 | 菜单禁止恢复和能量禁止恢复时都保留取消，并保持每个身份的能量配对 | 5 failed、235 passed；killed |
| 7 | 已有观看控制错误要求新的 watch 启动资格 | 地点变化后继续、状态控制和调速都被撤掉 | 移除新启动类型后，已有观看仍保留继续、暂停、取消与调速 | 2 failed、238 passed；killed |

复验时又加入“暂停社交活动恢复无条件允许”突变。固定 240 项 oracle 全绿；对照探针显示移除
`start_social_activity` 后，chat 暂停活动在突变下仍有 `resume,cancel`，原实现只有 `cancel`。父任务把
chat 与 call 加入同一恢复参数用例，最终快照同义重施得到 **2 failed、242 passed**，两个社交类型都在
Schema 断言处杀死突变。

最终快照另施加“当前游戏控制错误要求新的游戏启动资格”突变：分别撤掉 `play_game` 活动类型和
`start_targeted_activity` 工具时，突变会删除现有会话的 `game_action`。新增用例在两种情况下都先检查
命令闭集，再走本地和直接派发；固定 oracle 得到 **2 failed、242 passed**。恢复源码后同一 244 项 oracle
再次全绿。

## 计数

| 项目 | 结果 |
| --- | ---: |
| 映射的行为要求 | 15 |
| 初始语义突变尝试 | 22 |
| 初始有效突变 | 19 |
| 初始被杀 | 15 |
| 初始存活 | 4 |
| 等价 invalid | 3 |
| 后续新增的不同有效行为突变 | 5 |
| 复验时重复施加的旧存活突变 | 5 |
| 所有快照上的突变应用次数 | 32 |
| 不同有效语义突变 | 24 |
| 最终未解决存活突变 | 0 |
| inconclusive | 0 |
| 最终实现缺失或偏离 | 0 |

“后续新增”包括社交恢复、异常日志、禁止恢复时保留取消、已有观看控制、已有游戏控制。重复施加只用于
证明补入 oracle 能杀死完全相同的错误行为，不把它们重复计为不同语义突变。

## 恢复与清理证据

每个突变后立即对被改生产文件执行 `git restore --source=HEAD -- <path>`，随后同时验证：

```bash
git diff --exit-code
git status --porcelain=v1 --untracked-files=all
```

每次均成功且输出为空，下一突变才开始。最终状态如下：

| detached worktree | HEAD | 源码恢复 | 子模块 |
| --- | --- | --- | --- |
| `/tmp/worldmock-bob-host.xf7MCG` | `7e89c715aa75fa7d928f0aab173c0e122f22fd0a` | diff 与完整 status 均空 | 两个 gitlink 为固定提交，当前已 deinit |
| `/tmp/worldmock-bob-host-acceptance.8MpCHk` | `1cf2228ae8afdb48f04232ae63ebcfb9bf9e09d2` | diff 与完整 status 均空 | deinit 前子模块 status 均空，当前已 deinit |
| `/tmp/worldmock-bob-host-final.SQsoBA` | `443119f8c86a3f7bb59d7337d112e95e058af39b` | diff 与完整 status 均空 | deinit 前子模块 status 均空，当前已 deinit |

三个 worktree 的子模块目前均已 deinit；对它们执行普通 `git worktree remove` 时，Git 仍返回
`fatal: working trees containing submodules cannot be moved or removed`。依照审查规则，没有使用
`--force`，也没有递归删除；上表路径和 worktree 登记被保留。三个 caller checkout 的 HEAD、diff 与完整
status 在结束时仍干净，这些临时登记没有把生产突变带回候选仓库或共享主工作树。

## 未覆盖边界

本审查没有运行付费模型、网络服务、正式长期实验或全项目门禁；不能从 233、240、244 项固定 oracle 推断
这些层级已经通过。共享菜单声明、观察计算和环境执行由独立 Bob 报告给出证据。当前没有需要父任务裁决的
未解决存活项或实现缺口。
