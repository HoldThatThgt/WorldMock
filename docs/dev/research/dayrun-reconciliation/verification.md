> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-reconciliation/verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日运行修复核查与验收

本次范围是核查用户提供的两份材料、把 dayrun 中仍适用的修复整合到当前主线，
并形成行为设计提案；行为提案尚未冻结，也未实施新的并行活动或记忆机制。

## 源码和运行边界

| 来源 | 本次读取身份 | 用途 |
| --- | --- | --- |
| 主仓库 | `/Users/atishoo/Project/WorldMock`，起始 `20dedcd`，干净 | 当前行为与集成基线 |
| dayrun | `/Users/atishoo/Project/WorldMock-dayrun`，`761241c` 加未提交修改 | 用户列举补丁的实际来源，保持原样 |
| 隔离整合 | `codex/dayrun-reconciliation` | 修复、测试、文档和验收 |
| 已完成旧运行 | `status=completed`，2,208 事件 | 旧程序的行为证据，不是新主线验收 |

主线的 `170e1f0` 快照已包含多数 dayrun 修复，之后的每日目标改动替代了时间段计划。
不能把“dayrun 没提交”推成“主线没有修复”，也不能把旧运行诊断直接当成当前主线表现。
具体代码和反例见[引擎核查](engine-audit.md)、[供应商核查](provider-audit.md)、
[行为证据](behavior-evidence.md)。

## 潜伏引擎问题逐项核查

| 清单序号 | 问题名称 | 起始主线状态 | 本次结论和验证 |
| --- | --- | --- | --- |
| 1 | 活动观察缺少 status | 已包含修复 | 真实引擎到人物控制合同已有回归，本次保留 |
| 2 | 游戏观察缺少 data | 仍缺少 | 增加独立活动数据副本；真实 TextWorld 命令、人物工具和恢复贯通 |
| 3 | 跨午夜就寝锚点 | 已包含修复 | 画像模型的跨午夜窗口和既有回归保留 |
| 4 | 游标 null 字面值 | 已包含请求归一与业务拒绝 | 新增非法内部类型、非有限值和缺项的严格拒绝，消除 TypeError/OverflowError 穿透 |
| 5 | 对睡着同伴发 chat | 已转业务拒绝 | 原回归验证后续轮次存活；候选仍可能列出不可交谈同伴，属于剩余交互问题 |
| 6 | send_message 未知收件人 | 已转业务拒绝 | 保留 RECIPIENT_NOT_FOUND 行为和回归 |
| 7 | send_message 文本超长 | 已转业务拒绝 | 保留 MESSAGE_TEXT_TOO_LONG 行为和回归 |
| 8 | plan_day 长备注污染后续观察 | 已被每日目标替代，目标仍限长 | 保留请求和持久层三百字符上限；不恢复旧 note 字段 |
| 9 | memory 幻觉身份 | 已有可重试失败反馈 | 保留本地记忆工具的边界处理和回归 |
| 10 | dispatch 再校验异常穿透 | 已转业务拒绝 | 保留 ACTION_REQUEST_INVALID 与不落脏状态的回归 |
| 11 | plan_day 猜时间单位 | 旧接口已删除 | 当前目标请求没有 start/end，不移植 epoch 锚点 |
| 12 | plan_day 猜日末边界 | 旧接口已删除 | 不恢复时间段计划；实际时间仍由世界事件负责 |
| 13 | plan_day 猜就寝边界 | 旧接口已删除 | 每日目标不要求模型提交覆盖就寝的时间段 |
| 14 | chat/call 决策控制闭集 | 已包含修复 | 主线已有社交控制种类和动机层排除；不重复移植 |
| 15 | 库存扣减浮点误差 | dayrun 仅钳制最后扣减，仍有遗漏 | 同步九位累计目标、扣减和消耗；多种进度分割、热量、持久化与真实缺货边界回归 |

仅对最后一次扣减取最小值不能修好库存：一份食物分六次吃完时，旧代码每次库存舍入，
误差会在最终守卫前累积成不足。此次保留原容差，统一普通食用的累计精度；
库存配方的精确微量合同保持独立。

## 模型与供应商适配逐项核查

| 清单序号 | 问题名称 | 起始主线状态 | 本次结论和验证 |
| --- | --- | --- | --- |
| 16 | 补全预算被 reasoning 耗尽 | 已包含 8000 预算 | 保留自述、抽取、联系人以及相关验证副本 |
| 17 | 抽取 payload 中 null | 已包含无效载荷处理 | 保留 null 载荷进入解析重试的现行回归 |
| 18 | reasoning 长尾 | 已包含分阶段上限 | 保留自述/联系人 1500、抽取 3000；软上限不构成供应商服从保证 |
| 19 | 自述温度 | 已为 1.0 | 小样本运行经验，不宣称普遍最优或彻底消除病态输出 |
| 20 | 传输瞬断 | 主线尚无独立传输预算 | 使用 LiteLLM 异常类型、独立三次预算和 2/4/8 秒退避；报错显示真实次数 |
| 21 | ReAct 轮次不足 | 主线仍为 10 | 实际人物配置改为 16；预算冻结进运行身份，缺失和篡改拒绝 |
| 22 | 多 key 分摊 | 运行阶段已有 N deployment | 已有代码保留；画像抽取仍取池首把，不能称“全链路均已分摊” |

dayrun 传输补丁按文本识别错误，漏过 LiteLLM 的 ServiceUnavailableError，
且实际四次请求后可能声称仅尝试一次。本次按库的明确异常类型判断；
传输错误与普通错误独立计数，普通错误不能借剩余传输预算多发请求。
Router 不启用内部重试或备用模型，取消执行仍能释放并发许可。

运行阶段多 deployment 的构造验证不证明真实云端请求均匀分布。
画像抽取的首 key 限制没有现成 dayrun 修复可移植；本次明确登记缺口，不临时增加另一路轮转器。
这不妨碍核查和适用补丁整合完成，也不代表全链路分摊目标已完成。

## 附件诊断的更正

| 原说法 | 最终数据库或当前源码支持的结论 |
| --- | --- |
| 98 次决策、242 次模型请求 | 最终完成库为 138 次决策、485 次模型请求；原统计不能作为最终统计 |
| 每个结算边界都重新决策 | 198 条活动进度中 88 条静默；已有结算与唤醒分离 |
| 全系统没有并行 | 不同人物及配送可并行；缺少同一人物两个活动同时推进 |
| 餐馆菜单过期是主因 | 发现结果及在场观察均无供给，输入中该餐馆供给数为零；TTL 主因未证实 |
| 记忆中的晚饭没有对应事件 | 确认：人物一到店等待、后买外卖，未消费该餐，却写入吃过的记忆 |

真实长跑能发现新的反例，但有限次长跑同样不能证明缺陷清单完备。
此次将已发现的反例转为确定性行为回归和源码突变验证；真实性仍须独立验收。

## 验证收据

| 检查 | 结果 |
| --- | --- |
| 根项目完整测试与覆盖门禁 | **3745 passed, 4 skipped**，576.58 秒；分支覆盖 **96.51%**，公共接口未覆盖数 **0**，门禁通过 |
| AS2 全套离线测试 | **480 passed**，15.02 秒；最后两个测试执行真实 LiteLLM Router 的重试与路由流程 |
| 根修复 Uncle Bob 审查 | 基线 89 例；八个独立有效突变全部被检出，见[根审查](bob-root.md) |
| 供应商 Uncle Bob 审查 | [首次审查](bob-vendor.md)十四个被检出、两个存活；补测试后[原样复验](bob-vendor-recheck.md)两个均被检出 |
| 文档引用门禁 | 97 篇，零断链，通过 |
| 设计与证据一致性 | 独立核对四个机制、五个差距、来源与路径；两处文字更正已处理 |

完整门禁使用隔离目录的冻结依赖和独立 Python 环境，执行：

```sh
MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
uv run python scripts/coverage_gate.py
```

[覆盖收据](../../coverage-report.json)绑定生产提交 `fa150fb2fc874db568f1812b606608217e42e574`。
原始输出为 `/tmp/worldmock-dayrun-coverage-final.log`。
初次全量执行因隔离 LabelKit 未安装 CLI，十二项本地 HTTP 夹具测试失败；
在该 vendor 目录完成 `uv sync --frozen --no-dev` 后相关测试通过，并重新执行上述完整门禁。
没有跳过失败用例、放宽断言或修改生产路径以绕过环境问题。
最终四项跳过均来自 `tests/resources/test_equivalence_anchor.py`，原因是隔离目录未带历史账本数据库。

AS2 生产修复提交为 `8fb24348953c5cfa3e8d4b280c8830a8efcf5de6`，
最终子模块提交为 `ae3224ce982d52f1eb773a4f70521a7b60e19cba`。
后者仅追加两个 Router 测试，生产源码相同；完整根门禁与 Bob 基线期间未修改生产源码。
原始首次 Bob 记录保留，后续复验另存，避免把修复前的测试遗漏隐去。

## 本地主线整合收据

本地 `main` 从 `20dedcd` 快进到整合提交 `040cda4`，AS2 子模块检出 `ae3224ce`。
主目录实际导入路径已核对为 `/Users/atishoo/Project/WorldMock` 下的 runtime 与 vendor dispatcher，
运行预算分别为十六轮与三次传输重试。

在该主目录执行以下关键路径回归，**133 passed in 28.65s**，没有跳过：

```sh
MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
.venv/bin/python -m pytest -q \
  tests/life/test_dayrun_inventory_regressions.py \
  tests/life/test_dayrun_cursor_regressions.py \
  tests/integration/test_observation_agent_seam.py \
  tests/integration/test_run_manifest.py \
  tests/integration/test_runtime.py \
  tests/integration/test_host_branch_coverage.py::test_restored_life_agent_reads_the_frozen_budget_from_its_real_workspace \
  tests/integration/test_life_agent_protocol.py::test_runtime_budget_handles_twelve_bad_responses_then_submits_once \
  tests/resources/test_equivalence_anchor.py
```

原始输出为 `/tmp/worldmock-dayrun-main-smoke.log`。
四项历史账本测试实际读取主目录既有 `runs/deepseek-multi-47931e06/footprints.sqlite`，
补齐隔离完整门禁中因数据未随工作树复制而跳过的验证。
主目录文档门禁扫描九十九篇、零断链，通过。
后续提交只补充本页验收收据，生产源码和测试保持已验证版本。

合入前对另一任务新增的外生事件研究文档及索引修改保存快照，
Git 自动暂存与恢复后核对：研究文档字节相同，索引去掉本次新增的两个入口后也与原输入字节相同。
这两处并行改动没有纳入本次提交。原 dayrun 工作树保持原样；没有向远端推送。


所有新增测试使用确定性输入或本地替身，不调用付费模型；TextWorld 和 SQLite 路径实际执行。
本次没有启动新主线的一整天云端模型运行，因此不会把旧 dayrun 的 completed 当作新代码长跑完成。
