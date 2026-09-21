# Bob report

**状态：COMPLETE。** 初始固定提交的 1 项目标推进与中间提交的 4 项提醒签名存活变异，均已在最终固定提交用**原保存补丁**杀死。三个 detached review worktree 因 Git 子模块限制无法普通注销，均经 HEAD/diff/status 核验干净并原样保留。

仓库 caller：`/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-bob-anchor-0axnuyu4`（Git 实际根为 `/private/var/...` 的同一路径）。固定提交：`d998a311ff51f518c975fb29eb1f631d13528d5a`。唯一行为权威：对应固定提交的 `docs/dev/SPEC-dayrun-behavior.md`。初始审查范围是「同一人物的活动集合」第 55–127 行与「持续活动与必要唤醒」第 192–208 行，最终同等范围延至第 210 行；第 9–31 行的流程定义只用于解释这两节，不延伸审查等待、播客、餐馆、记忆等其他特性。

caller 预检：`git rev-parse HEAD` 为上述提交，`git status --porcelain=v1 --untracked-files=all` 为空。在 caller 提交上通过 `mktemp -d` 创建独立 detached worktree `/tmp/worldmock-bob-activity-tree.Vmhdmr`。所有临时生产代码变异只发生于该树；未修改测试、caller、主代理开发树，未提交或推送。以本地参考初始化的 `vendor/agentsociety2` 为 `8768e14df5c887c710fe96bf24981a2d8508346e`；测试导入的 `worldmock.life.engine` 与 `agentsociety2` 均指向该 review tree。未运行网络模型或生产运行。

## 基线与判定口径

每个变异使用同一事先声明的命令；在变异前基线结果为 **116 passed in 25.97s，退出码 0**：

```sh
env MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH=src:.:vendor/agentsociety2/packages/agentsociety2 /Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock/.venv/bin/python -m pytest -q tests/life/test_parallel_activities.py tests/life/test_parallel_acceptance.py tests/life/test_shared_body.py tests/life/test_wake_reasons.py tests/life/test_day_plan.py tests/life/test_energy_feasibility.py
```

下面表格逐条列出所选范围内的可观察要求。`运行测试` 只表示上述六文件基线实际执行；其他已存在测试仅供静态映射，未纳入本次判定命令。每条是单独可观察性质，完整映射数见表后统计。

| 可观察要求 | 规格锚点 | 生产入口 | 现有证据与边界 |
| --- | --- | --- | --- |
| 活动集合是唯一事实源，旧人物单活动字段不参与运行 | 59–60 | `life/models.py:302–329, 498–509`; `life/engine.py:1867–1873` | 代码静态核对；旧字段不在 `AgentState:235–282`；本轮无专门变异 |
| 本人当前和暂停活动稳定排序并完整观察 | 60–61 | `life/models.py:498–509`; `life/observe.py:65–70` | 运行测试 `test_engine_feedback` 未在命令内；代码静态核对 |
| 各活动只占冻结资源表所声明的个人资源 | 63–64, 68–81 | `catalog/affordances.toml:35–155`; `catalog/affordances.py:255–264` | 运行测试允许组合与资源拒绝；`watch_controls_position` 被杀 |
| 播放读取位置却不控制位置，可配步行、使用、进食、等待、生活活动 | 63, 82–85 | `catalog/affordances.toml:68–76`; `life/engine.py:1016–1065` | 运行测试五种组合：`test_parallel_acceptance.py:97–122` |
| 观察资源为当前活动声明的排序并集，不建独立资源槽 | 65–66 | `life/observe.py:69–70`; `life/presentation.py:496–500` | 代码静态核对；本命令未直接断言排序集合 |
| 暂停/终态不占资源，人物间个人资源互不排斥 | 84–85 | `life/models.py:498–509, 535–541`; `life/engine.py:1196–1202` | 暂停及恢复运行测试；跨人物相同资源未专项变异 |
| 恢复拒绝非法归属、资源、目标、时间和能量 | 61, 103 | `life/models.py:512–557`; `life/activity_validation.py:16–34`; `life/engine.py:731–732` | 运行测试 `test_parallel_activities.py:97–124` 的四类篡改；完整类型矩阵未变异 |
| 组合工具只接受至少两项闭集持续活动请求 | 89–91 | `life/models.py:862–888, 994–1004` | 静态请求类型核对；没有本命令中的非法工具类型变异 |
| 每个子项复用本人物、地点、菜单、可见目标与原启动条件 | 92–93 | `life/interaction_menu.py:164–175`; `life/engine.py:1056–1062, 1030–1049` | 运行测试真实组合入口；无各目标不可见组合的逐项变异 |
| 组内及与已有 ACTIVE 的资源交集拒绝，不靠组合白名单 | 82–85, 92–94 | `life/engine.py:1054–1065, 1196–1202` | 运行测试 `test_parallel_activities.py:55–69`；允许组合由资源规则得到 |
| 任何子项失败不留下活动、资金、库存、邀请、游戏或事件草稿 | 92–94 | `life/engine.py:1067–1077, 1085–1098`; `life/engine.py:808–843` | 运行测试 `test_parallel_acceptance.py:272–303`；`inventory_validation` 被杀；未构造合法的“已暂存副作用后失败”语义变异 |
| 非业务程序错误不被组合吞作普通拒绝 | 94 | `life/engine.py:808–843` 仅捕捉 `BusinessRuleError` | 静态控制流；本轮未注入程序错误 |
| 组合是一个幂等动作，结果含独立活动身份 | 95–96 | `life/engine.py:770–783, 926–946, 1077–1083` | 运行测试 `test_parallel_activities.py:29–39` 身份；一般动作幂等测试存在但本命令未执行组合重放 |
| 各子项独立进度和终态，未全部终结时组合保持进行中 | 95–98 | `life/engine.py:1582–1629, 1631–1643`; `life/action_evidence.py:52–63` | 运行测试 `test_parallel_acceptance.py:125–146`；`early_aggregate` 被杀 |
| 聚合仅一次，失败/截断优先于取消，全部完成才完成 | 96–98 | `life/action_evidence.py:55–81`; `life/engine.py:1735–1743` | 运行测试完成、取消、截断及唯一事件；失败类型的全部排列未逐一变异 |
| 一轮只提交一个生活动作，模型工具可动态选择组合 | 99–100 | `life/engine.py:763–768`; `custom/agents/life_agent.py:917–921` | 真实 `DirectToolCall` 组合测试已运行；Agent 动态 schema 未在本命令运行 |
| 新活动不会自动暂停旧活动 | 101 | `life/engine.py:1150–1194` 与 `_put_activity:1867–1873` | 静态核对；`test_engine_feedback.py:105–126` 读到专门测试，未在判定命令 |
| 暂停、继续、恢复、取消、变速只作用点名活动 | 101–102 | `life/engine.py:1100–1112, 1224–1274`; `life/activities.py:388–418` | 运行测试 `test_parallel_acceptance.py:170–253`；`pause_transition` 被杀 |
| 恢复重验地点、资源、能量并保留暂停进度 | 103 | `life/engine.py:1231–1253`; `life/activities.py:398–406` | 运行测试 `test_energy_feasibility.py:268–382` 与并行独立恢复；目标重验路径静态核对 |
| 暂停活动不推进，原有游戏与社交控制合同保持 | 103–104 | `life/models.py:498–509`; `life/activities.py:421–438`; `life/social_actions.py` | 暂停运行测试；游戏/社交分支仅静态核对及 `test_shared_body.py:138–154` 的部分测试 |
| 行走与播放先后独立完成，位置和内容进度不互相抹掉 | 105 | `life/engine.py:1582–1629, 1645–1698` | 运行测试 `test_parallel_acceptance.py:125–146` 的两个结束顺序 |
| 同秒双完成各留证且本人仅获一次决策 | 106 | `life/engine.py:1557–1590`; `life/rhythm.py:90–130` | 运行测试 `test_parallel_acceptance.py:149–167` 验两项进度/一次身体/一次聚合；一次决策从每人物一轮节律推得，未单独计数模型请求 |
| 实验截止逐项截断且不冒称完成 | 106 | `life/engine.py:1726–1744` | 运行测试 `test_parallel_acceptance.py:256–269` |
| 人物共同身体时钟与全部活动/内容/身体/环境/截止边界 | 110–111 | `life/models.py:250`; `life/engine.py:1755–1780`; `life/rhythm.py:133–164` | 代码路径静态核对；终点与部分事件边界在本命令内执行，未穷尽边界组合 |
| 同一真实区间基础代谢和清醒/睡眠压力仅结算一次 | 112 | `life/engine.py:1557–1581`; `life/body.py:175–203` | 运行测试 `test_shared_body.py:46–61`；`basal_once` 被杀 |
| 空白时间也结算；零能量不低于零且照常推进清醒体感 | 113–115 | `life/engine.py:1566–1581`; `life/body.py:181–200, 206–224` | 运行测试 `test_energy_feasibility.py:64–87`；本轮未变异所有体感通道 |
| 并行运动负荷取最大 MET 而非求和 | 116 | `life/body.py:181–185` | 运行测试 `test_shared_body.py:46–61`；`max_met` 被杀 |
| 准入、预测、不可行和实际结算按结束点分段同式 | 117 | `life/body.py:227–252`; `life/engine.py:1204–1212`; `life/rhythm.py:444–459` | 运行测试 `test_shared_body.py:64–83`；`prediction_endpoint` 被杀；不可行离散状态未专项变异 |
| 恢复活动豁免不资助混合非恢复活动 | 118 | `life/engine.py:1204–1212`; `life/body.py:185–188` | 运行测试 `test_parallel_activities.py:72–83`；`mixed_energy` 被杀 |
| 各活动效果按自身 ACTIVE 时间，身体通道汇总限幅且食物热量入账一次 | 119–120 | `life/body.py:206–224`; `life/engine.py:1598–1629, 1695–1700` | 结算路径静态核对；完整效果与食物守恒未专项变异 |
| 每人每区间唯一共同身体事件，活动进度引用它且不复制身体账 | 121–126 | `life/engine.py:1557–1629` | 运行测试 `test_parallel_activities.py:43–49`、`test_parallel_acceptance.py:149–165` |
| 全部进度按本人目标匹配，首次推进和脉冲只发生一次 | 127 | `life/rhythm.py:249–269`; `life/schedule.py:348–370` | 初始有效存活变异 116 通过；最终新增真实双目标测试 `test_parallel_acceptance.py:306–331` 杀死原补丁（1 failed, 124 passed）。本人归属过滤见 `rhythm.py:255–257` |
| 静默边界只推进活动，不提供模型决策；同一人物每边界最多一次并收齐理由 | 194–195 | `life/engine.py:599–620`; `life/rhythm.py:90–130`; `life/schedule.py:255–304` | 运行测试 `test_wake_reasons.py:42–74` 的理由/静默条件；未运行模型记忆路径 |
| 任务提前三十分钟、当前地点闭店提前十五分钟精确提醒 | 196 | `life/schedule.py:224–234`; `life/rhythm.py:305–318, 401–410` | 运行测试 `test_wake_reasons.py:94–102, 156–211` |
| 任务签名绑定身份截止，闭店签名绑定地点/时刻/承诺，旧条件不重复交付 | 197–201 | `life/rhythm.py:98–126, 285–341` | 运行测试第二任务及恢复去重；最终 `test_wake_reasons.py:326–389` 杀死原保存的截止、闭店时刻和本地承诺三个签名变异 |
| 闭店威胁只由当前地点任务或实际因闭店失去准入的活动证明 | 199–200 | `life/rhythm.py:321–327`; `life/rules.py:45–92`; `life/menu_execution.py:9–17` | 当前闭店准入只作用即时购买：`life/basket.py:141–145`；持续活动的启动/恢复不检查营业，故活动条件在本提交无实例。不能将该条件句误报成已缺实现。地点任务正反例运行测试 `test_wake_reasons.py:177–211` |
| 新任务及真实截止/闭店/受威胁承诺变化产生新提醒；进度本身不造新签名 | 201 | `life/rhythm.py:285–341` | 运行测试 `test_wake_reasons.py:241–262, 326–389`；第二任务、同任务改期、新闭店时刻与新增本地承诺变异均被杀；受控权威事实不证明运行中管理工具 |
| 不可行签名按活动与离散原因去重，条件消失清除，可行后再不可行可再提醒 | 202–204 | `life/rhythm.py:98–126, 444–459` | 最终 `test_wake_reasons.py:392–425` 验持续同因不重复、能量恢复/暂停/终态清签名与再现；`threat_clear_on_disappear` 原补丁被杀 |
| 预报与实际唤醒共用威胁事实，扫描全部可见未完成任务 | 204–205 | `life/rhythm.py:290–318, 344–385` | 运行测试第二任务；`second_task_suppressed` 被杀 |
| 睡眠未交付提醒不得提前消耗，醒后仍成立可提醒 | 206 | `life/schedule.py:302–304`; `life/rhythm.py:114–117, 330–341` | 运行测试 `test_wake_reasons.py:265–283`；`sleep_consumes_warning` 被杀 |
| 消息、到达、完成、真实失约、正常重估不被预警去重吞掉 | 207 | `life/rhythm.py:344–385`; `life/schedule.py:255–304` | 运行测试提醒恢复中的消息与通用理由；未逐项变异所有独立事件 |
| 威胁交付状态与身体时钟随检查点恢复，持续同因不重扣/不重报 | 208 | `life/models.py:250–251`; `life/engine.py:1557–1581`; `life/rhythm.py:98–126` | 运行测试 `test_wake_reasons.py:286–315`；`repeated_warning` 被杀；身体重扣另有状态校验，本轮未专项变异 |

**映射统计：**上述表格共 42 条可观察要求。未发现当前生产实现明确缺失或背离的条款（0 项）；初始发现 1 项目标推进测试断言缺口，后补测关闭。此统计是所选范围的逐条映射，不是测试覆盖率或变异分数。最新规格 `9a62963` 已明确：现有持续活动不受营业时段限制，不以活动构成闭店威胁。

## 语义变异台账

每个补丁为证据目录中 `<名称>.patch`，对应 `<名称>.log` 和 `<名称>.json` 保留原样复验所需的精确源码差异、统一判定命令输出及退出码。所有变异都保持解析与相关运行入口可用；每次变异结束均执行 `git restore --source=HEAD -- <唯一生产文件>`，并以 `git diff --exit-code` 和完整 `git status` 双重核验为空后才开始下一项。

| 规格与变异（生产路径） | 分类与统一命令结果 | 因果证据 |
| --- | --- | --- |
| 63,82：播放额外占 `position`（`catalog/affordances.py:258`；`watch_controls_position.patch`） | killed；18 failed, 98 passed | 五种本应允许的搭配被 `RESOURCE_CONFLICT` 错拒，`test_each_allowed_watch_partner_starts_through_atomic_tool` 失败 |
| 92–94：跳过持有食物数量校验（`life/engine.py:1289–1290`；`inventory_validation.patch`） | killed；1 failed, 115 passed | `missing-inventory` 组合从 `rejected` 变成 `in_progress`，验收测试第 291 行失败 |
| 118：跳过组合能量准入（`life/engine.py:1072`；`mixed_energy.patch`） | killed；1 failed, 115 passed | 零能量等待+播放错误受理，`test_recovery_activity_cannot_sponsor_unaffordable_watch` 失败 |
| 95–98：首个子项终结即写聚合终态（`life/action_evidence.py:62`；`early_aggregate.patch`） | killed；5 failed, 111 passed | 另一个仍 ACTIVE 时原动作由 `in_progress` 错成 `completed` |
| 101–103：暂停不改变 ACTIVE 状态（`life/activities.py:393`；`pause_transition.patch`） | killed；4 failed, 112 passed | 暂停/恢复与独立控制测试读到错误生命周期，恢复报告 `ACTIVITY_NOT_PAUSED` |
| 112：共同身体按活动数重复基础代谢（`life/body.py:183`；`basal_once.patch`） | killed；2 failed, 114 passed | 基础消耗从应有 1.0833 变成 2.1667，身体测试直接断言失败 |
| 116：共同 MET 求和（`life/body.py:182`；`max_met.patch`） | killed；2 failed, 114 passed | extra kcal 从应有 0.325 变成 1.4083，身体测试直接断言失败 |
| 117：结束点后的活动仍计入下一段 MET（`life/body.py:233`；`prediction_endpoint.patch`） | killed；1 failed, 115 passed | 最长可行时长从 1200 秒错成 1800 秒，分段预测测试失败 |
| 206：睡眠中未提供决策也标记任务威胁交付（`life/rhythm.py:116`；`sleep_consumes_warning.patch`） | killed；1 failed, 115 passed | 睡眠预警边界的交付签名本应为空却含任务身份，测试第 278 行失败 |
| 208：忽略持久交付签名（`life/rhythm.py:100`；`repeated_warning.patch`） | killed；1 failed, 115 passed | 检查点恢复后消息到达把相同 `task_due_soon` 和 `commitment_threatened` 再次送达 |
| 201,205：同窗口多个任务只保留首个签名（`life/rhythm.py:308–311`；`second_task_suppressed.patch`） | killed；1 failed, 115 passed | 第二任务进入窗口时 `task_due_soon` 消失，测试第 261 行失败 |
| **127：每个结算边界只读取共同身体与首项活动进度**（`life/rhythm.py:254`；`second_goal_ignored.patch`） | **survived；116 passed，退出码 0** | 真实双目标诊断中 watch、wait 均完成，但第二项“休息”目标不推进；见下文对照 |

**存活项诊断，非事后替换判定命令。** 外部临时脚本 `probe_two_goals.py` 使用真实 `_engine`、正式组合动作与 `advance_next`，登记“娱乐/休息”两个不同目标，并让 watch+wait 同秒完成。基线：两项目标各自记录其活动身份，经过 120 秒；将原保存补丁原样 `git apply` 后：两项活动仍 `completed`，只有“娱乐”目标记录身份，“休息”的 `progressed_at`、`activity_id` 均为 `null`。诊断输出在 `second_goal_ignored.probe.log`，基线输出见此报告和终端收据。此结果证明变异触达真实多活动进度，漏断言是实质性的。需要在正式测试中经真实组合入口登记两个可同时匹配的目标，断言同一结算后两项都具有各自正确的 `progress_activity_id`、首次推进时刻和不重复脉冲；补齐后用 **同一 `second_goal_ignored.patch`** 在新的固定干净提交上复验。此建议不等于本 review 修改测试。

**初始变异统计（`d998a31`）：**有效变异 12；killed 11；survived 1；invalid 0；inconclusive 0。未做穷举资源冲突矩阵、全部聚合失败排列、所有闭店签名变化、不可行往返原因、真实供应商长跑和多自然日端到端验收；所选六文件的通过不能替代规格第 215–220 行列出的完整项目门禁。

## 补测固定快照的原样复验

主代理交付新的干净 caller：`/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-bob-recheck-anchor-io6or6l7`，提交 `aa22dc830d6a66d13f14a22a04babae5cb41ec98`。该快照中的同名规格第 199–200 行现明确：现有持续活动启动和恢复不受营业时段限制，闭店威胁只由当前地点绑定的未完成任务证明。该改写与上一节的生产路径反证一致，未改变执行行为。`d998a31..aa22dc8` 唯一生产源码差异是 `engine.advance` 删除已有共同身体事件覆盖的空草稿 `time_advanced` 分支；本轮所变异的签名、目标推进等生产行未变。新测试 `test_parallel_acceptance.py:306–331` 经真实组合和共同结算断言两个目标各自的活动身份与同一推进时刻。

在 `/tmp/worldmock-bob-activity-recheck.7cYzyF` detached tree，caller 与 review tree 都从干净的 `aa22dc8` 开始；导入 `worldmock.life.engine` 与 `agentsociety2` 均指向该 review tree。相同六文件、相同环境和命令基线 **120 passed in 27.04s，退出码 0**。原始保存的 `second_goal_ignored.patch` 未改动、`git apply --check` 成功，原样复验 **1 failed, 119 passed in 26.35s，退出码 1**；唯一失败新测试第 328 行，休息目标的 `progress_activity_id=None`，而 wait 活动身份为 `activity-action-two-goals:parallel:1`。日志和命令收据为 `second_goal_ignored.recheck.log/.json`。该原存活项在新快照已被杀死，每次恢复双重 clean。

对规格第 201–203 行又在 `aa22dc8` 以**同一个事先声明的六文件命令**审查了四个单独的有效生产变异。这个快照的测试只有首次任务截止、首次本地闭店、第二个不同任务、睡眠与检查点去重；未断言以下同一事实的变化或消失后再现。每项均 `120 passed`、退出码 0、恢复 clean。原始存活证据必须保留，后续新补测的杀死结果不能覆盖这一事实：

| 规格与变异 | 初次结果与实际断言缺口 |
| --- | --- |
| 同任务真实截止改变仍应提醒：`task_deadline_identity.patch` 从 `_task_signature` 删除 `due_at` | survived；`task_deadline_identity.log/.json`，120 passed。现有测试只变更**第二个不同任务**的截止，没有对同任务改期后的再次交付作断言 |
| 新闭店边界仍应提醒：`closing_boundary_identity.patch` 从闭店签名删除 `close_at` | survived；`closing_boundary_identity.log/.json`，120 passed。现有本地闭店测试只有一个闭店时刻 |
| 新增本地受威胁承诺仍应提醒：`closing_commitment_identity.patch` 从闭店签名删除具体 `commitment` | survived；`closing_commitment_identity.log/.json`，120 passed。现有本地闭店测试只有一个地点任务 |
| 威胁消失后再现应清理旧交付：`threat_clear_on_disappear.patch` 删除 `delivered_warning_signatures & current` 的当前事实交集 | survived；`threat_clear_on_disappear.log/.json`，120 passed。现有测试只验证持续同一事实跨检查点不重复，没有不可行恢复/暂停或终态解除后再次出现的断言 |

这四项是由公开 `engine_rhythm_inputs`、`apply_decision_rhythm` 所接受的类型化事实变化触发的条件语义。正式运行中的新任务与后续营业区间提供实际变化来源；本特性未要求新增运行中改期或改营业时间管理工具，不把受控输入测试冒充此类工具的端到端证据。

## 最终固定提交的独立复验

最终干净 caller：`/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-bob-final-anchor-ufmd7tg2`，提交 `9a62963841694cb668cc49a039c59f39caa757a6`。`aa22dc8..9a62963` 没有 `src/worldmock/life` 生产源码差异；本次新增 `test_wake_reasons.py:318–425` 的公开 `engine_rhythm_inputs` + `apply_decision_rhythm` 转换测试，以及规格第 202–203 行对条件输入与管理工具边界的澄清。实测导入的 WorldMock 和 AgentSociety2 均来自 detached review tree `/tmp/worldmock-bob-activity-final.Kfo5ZH`。同一六文件命令在未变异状态 **125 passed in 29.83s，退出码 0**；恢复后再次捕获原始日志 `final/baseline.log` 为 **125 passed in 28.03s，退出码 0**。

`replay_final.py` 对每个原保存补丁先运行 `git apply --check`，应用后将 `git diff` 的**字节**与原 `<名称>.patch` 比较相同，运行事先声明的同一命令，随后恢复唯一生产文件并再次要求 diff/status 为空。结果如下，证据在 `final/<名称>.log/.json`：

| 原保存补丁 | 最终分类和精确结果 | 因果失败 |
| --- | --- | --- |
| `second_goal_ignored.patch` | killed；1 failed, 124 passed | `test_parallel_acceptance.py:328`：第二目标“休息”的活动身份为 None，应为 wait 活动身份 |
| `task_deadline_identity.patch` | killed；1 failed, 124 passed | `test_wake_reasons.py:344`：同任务改期后缺 `task_due_soon`；新测试也核对旧签名清除和 JSON 中新截止 |
| `closing_boundary_identity.patch` | killed；1 failed, 124 passed | `test_wake_reasons.py:376`：同地点闭店改为下一分钟后缺 `location_hours_change` 与 `commitment_threatened` |
| `closing_commitment_identity.patch` | killed；1 failed, 124 passed | `test_wake_reasons.py:381`：同闭店新增第二个地点任务后缺新的闭店提醒 |
| `threat_clear_on_disappear.patch` | killed；5 failed, 120 passed | `test_wake_reasons.py:350,388,418`：已完成任务与本地承诺、能量恢复/暂停/终态后的旧签名未清除；后三种不可行转移参数均失败 |

**最终复验统计（`9a62963`）：**原样复验 5 个有效补丁，killed 5，survived 0，invalid 0，inconclusive 0。三个快照合计 **16 个不同的有效语义变异**：最初 11 个直接被杀，另外 5 个先存活、在新增行为测试后被原样杀死；无未解决的存活变异。初始另 11 个已杀变异的生产路径没有变更，但未在 `9a62963` 逐一重跑，因此不把两个提交的结果合成一个“最终变异分数”。最新明确测试证据分别是：同任务截止变更与任务完成清签名 `test_wake_reasons.py:326–351`；闭店时刻与新地点承诺 `:354–389`；同因进度不重报、能量恢复或暂停/终态清签名、再现 `:392–425`。这些是受控的类型化权威事实经公开节律入口的合同验证；不是运行中修改任务或营业时间管理工具的端到端测试，规格也不要求该接口。完整自然日、真实模型与全仓 coverage 由主代理独立执行，不在本 Bob worker 的授权运行范围内。

## 恢复与交付边界

所有初始 12 次、`aa22dc8` 的 4 次新增变异、`9a62963` 的 5 次原样复验以及目标补丁在 `aa22dc8` 的中途复验，均在每次源码恢复后以 `git diff --exit-code` 与 `git status --porcelain=v1 --untracked-files=all` 判空；日志输出 `restoration=clean`。存活目标补丁的事后真实入口诊断也双重恢复 clean。最终逐树与 caller 复核：

| 固定提交 | review tree | review tree HEAD/diff/status | caller HEAD/diff/status | 普通注销结果 |
| --- | --- | --- | --- | --- |
| `d998a31` | `/tmp/worldmock-bob-activity-tree.Vmhdmr` | HEAD 相等；diff 退出 0；完整 status 空 | HEAD 相等；diff 退出 0；完整 status 空 | `git worktree remove` 退出 128；`fatal: working trees containing submodules cannot be moved or removed` |
| `aa22dc8` | `/tmp/worldmock-bob-activity-recheck.7cYzyF` | HEAD 相等；diff 退出 0；完整 status 空 | HEAD 相等；diff 退出 0；完整 status 空 | 同一 Git 子模块错误，退出 128 |
| `9a62963` | `/tmp/worldmock-bob-activity-final.Kfo5ZH` | HEAD 相等；diff 退出 0；完整 status 空 | HEAD 相等；diff 退出 0；完整 status 空 | 同一 Git 子模块错误，退出 128 |

按照 Uncle Bob skill 的“普通 worktree remove 失败则保留并报告精确路径，不强制清理”要求，三个已注册的临时树原样保留；没有使用 `--force`、递归删除或清理他人工作树。这是唯一未完成的环境清理，不影响上述测试和变异因果结论。是否另行采取仓库允许的子模块 worktree 清理步骤由主代理决定。


归档说明：原始补丁、各阶段命令与最终原样复验见[证据包](evidence.tar.gz)，逐文件 SHA-256 见[清单](evidence-manifest.json)。
