> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/interaction-menu-bob-environment.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

## 状态与边界

**状态：COMPLETE**

本审查只以 [`SPEC-interaction-menu.md`](../SPEC-interaction-menu.md) 的“声明与准入”
（第 50—79 行）、“观察与人物工具”（第 81—121 行）及“环境执行与拒绝”
（第 123—133 行）为产品行为权威。审查覆盖共享菜单计算、观察菜单、候选保持、环境直接调用、
已有活动控制、工作审核预检、幂等重放和真实品类存在性优先级。LifeAgent 宿主 Schema 的另一份独立
Bob 审查、付费模型调用、网络服务和长期实验不在本报告范围。

审查使用两个干净候选提交：

| 用途 | 干净 caller checkout | 提交 |
| --- | --- | --- |
| 初次突变 | `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-interaction-menu-review-j3d4uxt3/candidate` | `7e89c715aa75fa7d928f0aab173c0e122f22fd0a` |
| 缺口修复后复验 | `/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-interaction-menu-review-j3d4uxt3/candidate-acceptance` | `1cf2228ae8afdb48f04232ae63ebcfb9bf9e09d2` |
| 工具字段必填的隔离补验 | 与缺口修复后复验相同 | `1cf2228ae8afdb48f04232ae63ebcfb9bf9e09d2` |

两次 caller checkout 在开始与结束时均由
`git status --porcelain=v1 --untracked-files=all` 证明为空。初次与复验快照中，本审查涉及的七个生产
文件 SHA-256 逐一相同；变化只位于增强 oracle 的
`tests/life/test_interaction_menu.py` 和 `tests/life/test_interaction_menu_runtime.py`。
测试差异为新增反例和把相同准入断言参数化到 `LifeScenario` / `RuntimeScenario`，没有删除旧断言。

## 固定 oracle 与 baseline

初次审查在 detached worktree `/tmp/worldmock-interaction-menu-bob.aU1wu0` 中固定以下 oracle，之后每个
mutant 都运行同一命令：

```bash
PYTHONPATH=/tmp/worldmock-interaction-menu-bob.aU1wu0/src:/tmp/worldmock-interaction-menu-bob.aU1wu0:/tmp/worldmock-interaction-menu-bob.aU1wu0/vendor/agentsociety2/packages/agentsociety2 LITELLM_LOCAL_MODEL_COST_MAP=True UV_NO_SYNC=1 AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/life/test_interaction_menu.py tests/life/test_interaction_menu_invariants.py tests/life/test_interaction_menu_runtime.py tests/integration/test_interaction_menu_roundtrip.py
```

初次 baseline：**68 passed in 8.05s**。导入探针确认 `worldmock.life.interaction_menu` 来自该 detached
worktree，而不是共享工作树。

缺口修复后在新的 detached worktree
`/tmp/worldmock-interaction-menu-bob-acceptance.sxvGXB` 使用相同四个测试文件和以下等价命令：

```bash
PYTHONPATH=/tmp/worldmock-interaction-menu-bob-acceptance.sxvGXB/src:/tmp/worldmock-interaction-menu-bob-acceptance.sxvGXB:/tmp/worldmock-interaction-menu-bob-acceptance.sxvGXB/vendor/agentsociety2/packages/agentsociety2 LITELLM_LOCAL_MODEL_COST_MAP=True UV_NO_SYNC=1 AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/life/test_interaction_menu.py tests/life/test_interaction_menu_invariants.py tests/life/test_interaction_menu_runtime.py tests/integration/test_interaction_menu_roundtrip.py
```

复验 baseline：**87 passed in 9.40s**。导入探针同样确认源码来自复验 detached worktree。

审查初稿复核发现，“工具字段获得隐式默认”的初次尝试还删除了最小长度约束，因此没有把缺字段行为
与显式空集合行为隔离。补验在新的 detached worktree
`/tmp/worldmock-interaction-menu-bob-required-tool-clean.8cbtPf` 固定声明模型 oracle：

```bash
PYTHONPATH=/tmp/worldmock-interaction-menu-bob-required-tool-clean.8cbtPf/src:/tmp/worldmock-interaction-menu-bob-required-tool-clean.8cbtPf:/tmp/worldmock-interaction-menu-bob-required-tool-clean.8cbtPf/vendor/agentsociety2/packages/agentsociety2 LITELLM_LOCAL_MODEL_COST_MAP=True UV_NO_SYNC=1 AGENTSOCIETY_LLM_API_KEY=test-only /Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q tests/life/test_interaction_menu.py
```

补验 baseline：**58 passed in 0.33s**。导入探针确认模块来自补验 worktree。唯一突变把工具字段改为
`Field(default=tuple(REQUEST_MODELS), min_length=1)`，保留显式空集合的最小长度约束，只改变缺字段行为。
相同命令得到 **1 failed、57 passed in 1.26s**；唯一失败是缺 `allowed_tools` 时本应出现
`Field required` 却没有抛出 `ValidationError`，显式空集合用例继续通过。

随后按 `VERIFICATION.md` 已写明的最小突变集合补审“人物 `null` 当空集合”“保护项自动补齐”和“遗漏
观察菜单字段或证据”。补审继续使用复验 worktree、相同四文件 oracle 与相同命令；开始前重跑 baseline
得到 **87 passed in 8.12s**，两个导入探针分别确认 `interaction_menu.py` 和 `observe.py` 来自该 worktree。
三个突变逐个施加、逐个恢复，没有修改测试。

## 规格到实现与 oracle 的映射

| 行为要求 | 规格锚点 | 生产路径 | 主要 oracle |
| --- | --- | --- | --- |
| 两字段必填、无额外字段、名称闭集、非空、非空白且无重复 | 第 52—66 行 | `interaction_menu.py:58-75`、`scenario.py:195,547,675` | `test_interaction_menu_declaration_is_closed`、`test_scenario_requires_exact_location_menu_coverage` |
| 七个工具和 `wait` / `sleep` 均为字面保护项 | 第 69—78 行 | `interaction_menu.py:14-26,69-75` | `test_every_specified_protection_must_be_declared` |
| 地点类键精确覆盖且单值地点菜单不可为 `null` | 第 52—56 行 | `scenario.py:580-581,708-709,752-767` | 两种场景模型上的地点缺项、多项、整体空和单值空反例 |
| 地点与非空人物声明取交集并确定性排序，人物 `null` 不追加限制 | 第 83—86 行 | `interaction_menu.py:78-105` | `test_runtime_assembly_preserves_menu_and_resolution_intersects_person`、双方独有权限反例 |
| 观察按当前人物和地点给有效菜单，不泄露全部原始声明 | 第 83—92 行 | `observe.py:80-100` | `test_menu_observation_keeps_facts_candidates_and_other_people_private`、真实跨轮集成 |
| 生活工具同时服从工具轴和既有状态门控 | 第 95—110 行 | `interaction_menu.py:108-174`、`life_menu.py:40-144` | 共享判断单测、真实直接调用、LifeAgent 跨轮集成 |
| 基础、定向、出行和消费活动同时服从活动类型轴，生活活动前缀归一 | 第 98—102 行 | `interaction_menu.py:108-123,163-170,188-191` | 活动判断和直接调用参数用例、生活活动前缀反例 |
| 游戏、工作、内容、生活活动和食品品类使用冻结映射 | 第 112—120 行 | `interaction_menu.py:126-145` | 注册表全品类不变式、食品二选一反例、真实浏览拒绝 |
| 恢复同时检查启动资格；社交只检查社交启动工具；取消保持可用 | 第 102—108、132—133 行 | `interaction_menu.py:115-123,172-174`、`menu_execution.py:7-14` | 启动工具缺失、chat/call 恢复和暂停后取消用例 |
| 不存在品类先保留 `BROWSE_CATEGORY_NOT_FOUND` | 第 120—121 行 | `observe.py:387-402` | `test_unknown_browse_category_keeps_domain_error_when_menu_disallows_browsing` |
| 环境按权威状态重算菜单，拒绝先于领域副作用且留下既有证据 | 第 125—130 行 | `menu_execution.py:7-14`、`engine.py:770-918` | 八类直接调用、购买资金/库存、活动状态、拒绝事件和下轮公开结果 |
| 禁止工作在独立审核前拒绝 | 第 131 行 | `engine.py:296-317` | `test_denied_work_submission_does_not_invoke_review` |
| 已结算请求重放不重执行、不增加拒绝计数并标记 `reused=true` | 第 129—130 行 | `engine.py:770-773,881-901` | `test_replayed_menu_rejection_preserves_counter_and_marks_reused_evidence` |
| 菜单不改变候选、事实、排除明细和原因统计 | 第 28—29、88—89 行 | `observe.py:363-376,414-420` 及原候选链 | 候选摘要、窗口 items、excluded、counts、generated、total 逐项对照 |

源码检查没有发现缺失实现或与上述范围规格直接相反的实现。初次问题都属于测试 oracle 不能杀死一个
可达的错误实现，而不是生产实现漂移。

## 计数

| 项目 | 结果 |
| --- | ---: |
| 映射的行为要求 | 14 |
| 缺失实现 | 0 |
| 实现偏离 | 0 |
| 不同的有效语义 mutant | 32 |
| 初次有效 mutant | 28 |
| 初次被杀 | 23 |
| 初次存活 | 5 |
| 非隔离、判为 invalid 的尝试 | 2 |
| 缺口修复后重施的旧存活 mutant | 5 |
| 工具字段必填的隔离替代 mutant | 1 |
| 验证合同最小集合的补审 mutant | 3 |
| 复验与补验被杀 | 9 |
| 最终未解决存活 mutant | 0 |
| inconclusive mutant | 0 |

不报告 mutation score 或整个规格的覆盖率；本次只枚举上表十四项范围内的行为。

## 初次存活项与复验

初次存活项没有被隐去。每个存活项在 mutant 保持激活时使用独立探针证明错误行为可达；随后立即恢复
源码。测试增强由父任务在另一个干净快照完成，本审查没有编辑测试。

| 错误行为 | 初次证据 | 补入的可观察断言 | 复验因果结果 |
| --- | --- | --- | --- |
| 不存在品类先做菜单拒绝 | 固定 oracle 68 项全绿；同一请求在 mutant 下为 `INTERACTION_MENU_DENIED`，恢复后为 `BROWSE_CATEGORY_NOT_FOUND` | 菜单禁止浏览时请求 `nonexistent_category`，仍断言原领域错误且状态不变 | 1 failed、86 passed；错误码精确差异被杀 |
| 空地点菜单通过准入 | 固定 oracle 68 项全绿；探针显示 `LifeScenario` 接受 `home=null` | `LifeScenario` 与 `RuntimeScenario` 都提交单值空地点菜单 | 2 failed、85 passed；两种模型都报告本应拒绝却未拒绝 |
| `game_action` 不再受保护 | 固定 oracle 68 项全绿；探针显示缺 `game_action` 的菜单被接受 | 不从生产常量复制预期，按规格字面逐项删除九个保护项 | 2 failed、85 passed；`game_action` 字面反例及交集结果被杀 |
| 人物声明覆盖地点 | 固定 oracle 68 项全绿；探针显示地点禁用的 `purchase` 被人物全量声明重新放回 | 地点与人物分别给独有工具和活动类型，只接受双方交集 | 1 failed、86 passed；交集精确元组不符 |
| 活动字段获得隐式默认 | 固定 oracle 68 项全绿；探针显示缺 `allowed_activity_kinds` 仍被接受 | 单独遗漏 `allowed_activity_kinds` 并期待 `Field required` | 1 failed、86 passed；本应拒绝却未拒绝 |

## 完整 mutation 台账

除工具字段必填的隔离替代使用前述 58 项声明模型 oracle 外，下表每一行都使用四文件固定 oracle。失败
只有在程序完成导入并运行到目标行为断言后才记为 killed；语法、导入或启动故障没有被记为 killed。

| 表格序号 | 行为突变 | 生产路径 | 初次结果与因果证据 | 最终状态 |
| ---: | --- | --- | --- | --- |
| 1 | 在保留显式空集合最小长度校验时，为工具字段增加全量默认 | `interaction_menu.py` | 补验 killed；仅缺工具字段反例失败，显式空集合反例继续通过 | killed |
| 2 | 跳过工具名的重复、空白和未知闭集校验 | `interaction_menu.py` | killed；三个对应反例均变为“不抛 ValidationError” | killed |
| 3 | 跳过保护工具校验 | `interaction_menu.py` | killed；缺保护工具声明被接受 | killed |
| 4 | 跳过地点类键精确覆盖校验 | `scenario.py` | killed；缺地点、多地点和文件入口断言失败 | killed |
| 5 | 人物工具与地点工具求并集 | `interaction_menu.py` | killed；人物限制和观察隐私用例重新出现 `send_message` | killed |
| 6 | 活动准入忽略活动类型轴 | `interaction_menu.py` | killed；共享判断、八类直接调用和暂停恢复共 13 项失败 | killed |
| 7 | `game_session` 错映射为 `watch` | `interaction_menu.py` | killed；注册表全品类不变式和真实游戏浏览拒绝失败 | killed |
| 8 | 食品从“购买或消费”改为“两者都要” | `interaction_menu.py` | killed；仅购买可浏览食品的断言失败 | killed |
| 9 | chat/call 恢复无条件允许 | `interaction_menu.py` | killed；共享判断及 chat、call 两个真实恢复用例失败 | killed |
| 10 | 有效菜单改为逆序输出 | `interaction_menu.py` | killed；交集排序和真实 LifeAgent 解析失败 | killed |
| 11 | 跳过保护活动类型校验 | `interaction_menu.py` | killed；缺保护活动类型声明被接受 | killed |
| 12 | 跳过活动类型名的重复、空白和未知闭集校验 | `interaction_menu.py` | killed；三个对应反例均变为“不抛 ValidationError” | killed |
| 13 | 人物菜单字段获得隐式 `null` 默认 | `scenario.py` | killed；人物缺字段场景被接受 | killed |
| 14 | 关闭环境权威菜单检查 | `menu_execution.py` | killed；直接调用、购买副作用、恢复、重放和工作预检共 15 项失败 | killed |
| 15 | 移除工作审核前菜单预检 | `engine.py` | killed；禁止工作到达被 monkeypatch 标记的工作准备调用 | killed |
| 16 | 幂等重放重新进入领域派发 | `engine.py` | killed；拒绝被再次执行，计数/证据断言失败 | killed |
| 17 | 菜单检查先于真实品类存在性 | `observe.py` | 初次 survived；控制探针确认错误码由领域错误变成菜单错误 | 复验 killed |
| 18 | 暂停活动取消也要求当前启动资格 | `interaction_menu.py` | killed；共享取消资格和办公室取消真实活动均失败 | killed |
| 19 | 观察始终按第一个地点解析菜单 | `observe.py` | killed；跨地点观察、恢复和真实 LifeAgent 端到端三项失败 | killed |
| 20 | 按菜单过滤候选摘要 | `observe.py` | killed；摘要、完整排除账和三个真实浏览拒绝共五项失败 | killed |
| 21 | 地点菜单值类型放宽为可空 | `scenario.py` | 初次 survived；探针确认 `home=null` 通过 `LifeScenario` 准入 | 复验 killed |
| 22 | 从保护常量删除 `game_action` | `interaction_menu.py` | 初次 survived；探针确认缺该工具的菜单被接受 | 复验 killed |
| 23 | 从保护活动常量删除 `wait` | `interaction_menu.py` | killed；等待活动共享判断和动作准入失败 | killed |
| 24 | 非空人物菜单覆盖地点菜单 | `interaction_menu.py` | 初次 survived；探针确认地点禁止的 `purchase` 被重新放回 | 复验 killed |
| 25 | 在保留显式空集合最小长度校验时，为活动字段增加全量默认 | `interaction_menu.py` | 初次 survived；探针确认缺字段被接受 | 复验 killed |
| 26 | 活动准入忽略对应启动工具轴 | `interaction_menu.py` | killed；注册表品类和暂停恢复启动工具反例失败 | killed |
| 27 | 不把 `lifestyle:<id>` 归一到 `lifestyle` | `interaction_menu.py` | killed；真实生活活动子类型被错误拒绝 | killed |
| 28 | 场景文件入口泄漏原始 `ValidationError` | `scenario.py` | killed；入口不再给既有 `IntegrityError` | killed |
| 29 | 观察暴露全部地点类原始菜单 | `observe.py` | killed；人物观察隐私断言发现 `interaction_menus_by_location_kind` | killed |
| 30 | 人物 `null` 被当作空集合 | `interaction_menu.py` | 补审 killed；有效场景的解析和真实观察路径抛出空元组 `ValidationError`，20 failed、67 passed | killed |
| 31 | 缺少的保护工具与活动类型被自动补齐 | `interaction_menu.py` | 补审 killed；九个规格字面保护项和两个闭集反例均发现声明被静默接受，11 failed、76 passed | killed |
| 32 | 观察结果遗漏有效菜单字段及对应持久化证据 | `observe.py` | 补审 killed；公开观察对 `interaction_menu` 的直接字段断言首先报 `KeyError`，5 failed、82 passed | killed |

另有两次 invalid 尝试。工具字段的初次尝试把
`allowed_tools: tuple[str, ...] = Field(min_length=1)` 替换为
`allowed_tools: tuple[str, ...] = tuple(sorted(_KNOWN_TOOLS))`；活动字段的初次尝试也在增加默认时删除了
`Field(min_length=1)`。两次测试失败都同时包含显式空集合行为改变，不能证明“缺字段必拒绝”被检测，因此
都没有计入有效 mutant。工具字段随后以表格中的“工具字段增加全量默认”隔离变体补验，得到唯一缺字段
因果失败；活动字段则以“活动字段增加全量默认”隔离变体替代，得到真实 survived 与复验 killed 结果。

逐 mutant 原始输出保存在
`/Users/atishoo/Project/WorldMock/.work/interaction-menu/bob-environment/`。日志文件包含 baseline、初次
mutant、可达性探针、acceptance 复验、工具字段隔离补验和验证合同补审结果。

## 恢复与清理证据

每个 mutant 后都立即对被改生产文件执行 `git checkout HEAD -- <path>`，并同时验证：

```bash
git diff --exit-code
git status --porcelain=v1 --untracked-files=all
```

每次结果均为空且成功，下一 mutant 才开始。最后再次确认：

| detached worktree | HEAD | 源码恢复 | 子模块状态 |
| --- | --- | --- | --- |
| `/tmp/worldmock-interaction-menu-bob.aU1wu0` | `7e89c715aa75fa7d928f0aab173c0e122f22fd0a` | diff 与完整 status 均空 | 两个 gitlink 在 deinit 前均为期望提交；普通 deinit 成功 |
| `/tmp/worldmock-interaction-menu-bob-acceptance.sxvGXB` | `1cf2228ae8afdb48f04232ae63ebcfb9bf9e09d2` | diff 与完整 status 均空 | 两个 gitlink 在 deinit 前均为期望提交；普通 deinit 成功 |
| `/tmp/worldmock-interaction-menu-bob-required-tool-clean.8cbtPf` | `1cf2228ae8afdb48f04232ae63ebcfb9bf9e09d2` | diff 与完整 status 均空 | 两个 gitlink 在 deinit 前均为期望提交；普通 deinit 成功 |

两个 caller checkout 的 HEAD 和完整 status 在结束时也分别保持上述提交且为空。

普通 `git worktree remove` 对三个完成审查的目录都返回
`fatal: working trees containing submodules cannot be moved or removed`。依照审查规则，没有使用 `--force`，
也没有递归删除。三个已恢复、已 deinit 且 Git 状态干净的目录及其登记被保留在：

- `/tmp/worldmock-interaction-menu-bob.aU1wu0`
- `/tmp/worldmock-interaction-menu-bob-acceptance.sxvGXB`
- `/tmp/worldmock-interaction-menu-bob-required-tool-clean.8cbtPf`

补验前还有一个未进入 baseline 或突变阶段的初始化失败目录
`/tmp/worldmock-interaction-menu-bob-required-tool.xhLFw0`。中断的子模块初始化留下不完整的 LabelKit 工作树，
普通 `git submodule deinit --all` 因该子模块存在本地删除而拒绝。依照相同规则，没有强制清理；该路径及登记
被保留。它没有修改主仓生产源码，也没有参与任何 mutation 结论。

这只留下可明确定位的临时 worktree 登记，不留下生产源码 mutation。付费、网络和全项目测试未由本 Bob
审查运行；它们不能从本报告的 68 项、87 项或 58 项结果推断。
