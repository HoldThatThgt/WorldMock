# WorldMock dayrun 根集成审查

初始审查对象为 `/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock` 的整合修改，
后续随根提交 `fa150fb2` 冻结。
未调用真实模型，未运行变异测试。初始审查独立于根集成实现；发现恢复缺口后，按根任务新增授权完成了该处修复。

## 可证实问题

| 问题 | 证据与影响 | 当前处理 |
| --- | --- | --- |
| 恢复后的实际 ReAct 预算可以绕过 manifest 冻结值 | `_validate_agent_workspace` 原来只检查 `enable_memory`；恢复模式不重写 config，AS2 `Agent.restore` 实际从 `config.json` 读取预算，缺失时默认十。既有恢复 fixture 用新 manifest 配不含预算的工作区仍通过恢复。 | 已按授权在恢复发布前要求 `type(max_react_turns) is int` 且为十六；缺失、错值、错类型拒绝，并记录英文错误日志。 |
| 冻结身份允许数值相等但类型被改变 | 离线直接调用确认 `max_react_turns=16.0` 和 `transport_max_retries=3.0` 通过原校验。 | 已报告根任务；根任务已收紧整数类型并新增测试，本代理未修改该处。 |
| 合同表格被段落打断 | 游标新条款插入返回体表格中间，使 `items` 和 `excluded` 脱离表格。 | 已报告根任务；根任务已调整位置。 |

## 必要测试缺口

活动控制行生产实现使用 `deepcopy(active.data)`，符合独立副本要求。
新增真实 game 接缝只修改了 `data.game_session_id` 顶层字符串，不能证明嵌套请求、列表或字典与世界状态分离。
建议用现成嵌套活动数据补一条观察副本修改回归；已报告根任务，未擅自修改相关测试。

## 未发现新缺陷的范围

- 真实游戏接缝经过 `LifeEngine.observe → LifeAgent.prepare_react_loop → 工具 schema / 参数校验 → LifeEnv → commit`，
  执行真实可见命令，并验证保存后的同一会话恢复。该测试没有伪造 `game_session_id`。
- ReAct 的新增十二次坏响应回归从生产 `_agent_specs` 获取预算，运行真实 ReAct 循环，验证十五次模型调用后只登记一个动作。
  该回归本身通过手工装配 agent；本次新增的实际工作区恢复测试补充了配置加载链。
- `apply_meal_progress` 的累计目标、差额与累计消耗统一九位精度；容差内使用实际持有量结算热量，超过容差拒绝。
  已有新增测试覆盖等分、不等分、重复进度、JSON 再水合、容差内与超过容差；配方分支仍绕过普通消费量化。
- 游标严格字段校验在候选生成与呈现写入之前执行；无效类型走 `BROWSE_CURSOR_INVALID`，已有真实提交测试验证随后仍可正常浏览。
- 除上述浮点类型缺口外，新增 manifest 字段的缺失或不同整数值均有拒绝测试，生产新建和恢复身份路径都会读取冻结值。

## 恢复修复范围与验证

生产修改仅 `src/worldmock/runtime.py::_validate_agent_workspace`。
显式恢复 fixture 更新位于 `tests/integration/test_runtime.py`、`test_host_branch_coverage.py`、`test_category_identity.py`。
没有修改普通单元测试中的独立 agent 预算。

新增回归验证工作区预算缺失、十、十七、浮点十六、字符串十六及布尔值均拒绝，且失败不发布子目录。
实际恢复回归使用 `LifeAgent.create` 写正式配置，经共同检查点与 `_prepare_resume` 恢复，再使用
`LifeAgent.from_workspace` 读取文件，断言实际 `_max_react_turns`、读取配置和两阶段身份一致为十六。
该回归没有手工设置 agent 的预算，也没有绕过 `restore`。

执行命令使用隔离 `.venv/bin/python -B -m pytest -q -p no:cacheprovider`，明确 `PYTHONPATH`、`WORKSPACE_PATH`，
配置占位模型凭据，并关闭 telemetry、启用本地 LiteLLM 成本表。
测试文件为 `test_runtime.py`、`test_host_branch_coverage.py`、`test_category_identity.py`。
最终 **97 passed in 15.10s**。
首次新增测试因缺少 `load_scenario` 本地导入失败一次；修正后上述全部文件通过。
完整主仓库门禁和独立 Bob 审查由根任务汇总。

## 缺口关闭收据

- 恢复工作区预算已严格要求整数十六，实际创建、检查点恢复和 `LifeAgent.from_workspace` 回归通过。
- 两个 manifest 预算均已拒绝数值相同的浮点数，缺失、篡改和错误类型回归通过。
- 活动观察新增真实播放内容的嵌套 cues 列表修改测试，观察副本改变不能改写世界中的播放数据。
- 合同表格已恢复完整，文档引用门禁通过。

[根修复 Bob 报告](bob-root.md)进一步以八个独立突变验证上述预算与副本边界，
以及真实游戏、游标和分段进食行为；全部被检出。完整门禁和主线整合收据见
[核查与验收](verification.md)。
