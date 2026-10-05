> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-reconciliation/engine-audit.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 长跑引擎修复核查

核查日期：2026-09-21。此记录说明具体代码、反例与本次执行的检查；行为合同以
[CONTRACTS.md](../../../CONTRACTS.md) 为准。

## 核查范围与基线

| 对象 | 实际核查状态 |
| --- | --- |
| 主仓库 | `/Users/atishoo/Project/WorldMock`，`20dedcdd575c2dada8f35b64152cebda61322d4b` |
| 长跑工作树 | `/Users/atishoo/Project/WorldMock-dayrun`，`761241c` 加现有未提交修改 |
| 隔离整合目录 | `/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock`，从主仓库基线整合 |
| 本记录负责的修改 | `life/basket.py`、`life/presentation.py` 与两份新增回归测试 |
| 本记录不承担的验证 | 日程目标、供应商参数、真实模型长跑、观察游戏修复的最终集成 |

主仓库已经包含多数长跑修复，不能将长跑工作树中的未提交修改全部解释为主仓库缺失。
核查使用 `git diff 20dedcd -- <对应路径>` 逐文件对照，未改写原主仓库或长跑工作树。

## 用户清单对应结果

下表的“主仓库”指上述冻结基线，位置也按该基线标注；静态存在不等于本次执行了相应测试。

| 问题 | 长跑工作树与主仓库对照 | 对应已有测试与证据 |
| --- | --- | --- |
| 活动观察缺 `status` | 主仓库 `life/observe.py:203` 已包含，与长跑工作树等效 | `tests/integration/test_observation_agent_seam.py:77` 使用真实等待活动观察接入 LifeAgent，并有缺键、错误状态反例 |
| 游戏活动观察缺 `data` | 主仓库缺失；长跑工作树增加 `active.data`；主仓库 `custom/agents/life_agent.py:1330` 需要 `data.game_session_id` | 既有观察接缝仅用等待活动，不能证明真实游戏接缝；游戏修复与验证由整合负责人承担 |
| 跨午夜就寝锚点 | 主仓库 `profile/models.py:184` 已有 `normalize_wraparound`，与长跑工作树等效 | `tests/profile/test_models.py:84` 及相邻测试覆盖凌晨就寝、字典载荷、保持正常序列与拒绝真正乱序；本次仅静态核查 |
| 字符串空游标与畸形游标 | 主仓库 `life/models.py:602` 已有显式首屏归一，`life/observe.py:435` 已把 `ValueError` 转为业务拒绝 | `tests/life/test_candidate_contract_boundaries.py:254`、`:288`；本次执行通过，但新增反例证明原解析仍会抛其他异常 |
| 对睡着的同伴交谈 | 主仓库 `life/engine.py:1395` 附近已转 `CHAT_PARTNER_NOT_AVAILABLE` | `tests/life/test_model_input_killpoints.py:75` 含真实拒绝与下一轮继续；本次仅静态核查 |
| 消息收件人不存在、文本超长 | 主仓库 `life/engine.py:1331` 附近已有两个业务拒绝转换 | `tests/life/test_model_input_killpoints.py:105`、`:123`；本次仅静态核查 |
| 记忆工具幻觉身份 | 主仓库 `custom/agents/life_agent.py:1021` 已返回失败的 `ReactToolResult`，不再主动抛 `RuntimeError` | `tests/integration/test_agent_branch_coverage.py:931` 覆盖后端失败、未知身份及随后成功读取；本次仅静态核查 |
| 派发再校验 `ValidationError` | 主仓库 `life/engine.py:928` 已转 `ACTION_REQUEST_INVALID` | 主仓库已有改用目标清单的 `test_plan_day_oversized_goal_rejected_and_never_poisons_future_rounds`；未移回旧计划段测试 |
| 库存浮点扣减 | 主仓库缺少长跑树的 `min(delta, held)`；但该修法仍不能处理累计九位舍入误差 | 本次生产函数反例与新增库存回归直接证明，采用同一份量精度修复累计目标与当次结算 |

首屏字符串 `"null"`、`"None"` 的语义已经明确写在 `CONTRACTS.md` 的请求字段合同内。
本次保留这一当前合同，没有添加新的格式兼容、缺省字段或备用结果。

## 新发现的两个可执行反例

### 畸形字段绕过游标错误转换

原 `presentation._cursor_key` 只验证 `key` 是长度为三的列表，随后调用 `int()` 与 `str()`。
在长跑工作树的实际 Python 环境直接调用 `cursor_offset(json.dumps(payload))` 得到：

| 输入差异 | 实际原异常 |
| --- | --- |
| `key=[null,0,"x"]` | `TypeError` |
| `key=[0,0,"x"]`、`offset=null` | `TypeError` |
| `key=[[],0,"x"]` | `TypeError` |
| `key=[Infinity,0,"x"]` | `OverflowError` |

观察层只捕获 `ValueError`，上述错误仍能穿透真实提交。新实现按生成器的完整字段闭集验证：
`key` 为两个整数和一个非空字符串，`offset` 为非负整数，`identity` 为非空字符串；
布尔值、浮点数、字符串数字、空值、缺字段与额外字段均明确拒绝，不靠类型转换猜测。
畸形 JSON 和字段形状产生带错误日志的 `ValueError`，现有引擎路径转为 `BROWSE_CURSOR_INVALID`。
内部 `RuntimeError` 不被吞掉，已有专门反例证明其原样传播。

### 长跑树的库存钳制仍不能吃完一份

在长跑工作树直接调用生产函数 `apply_meal_progress`，使用既有领域测试工厂构造一份食品，
总时长取等分数乘以六十秒，每六十秒推进一次，得到：

| 进度划分 | 长跑树已有钳制后的实际结果 |
| --- | --- |
| 两等分 | 完成，库存为零、累计消耗为一份 |
| 六等分 | 最后一次抛 `PRODUCT_NOT_HELD`，此前库存为 `0.166666665` |
| 七等分 | 库存归零，但累计消耗为 `0.9999999991428571` |
| 十八等分 | 最后一次抛 `PRODUCT_NOT_HELD`，此前库存为 `0.055555548` |

`basket.change_inventory` 原本逐次 `round(..., 9)`，而累计消耗使用未量化的分数；
两条账本的误差逐次累积，最终超过既有 `1e-9` 容差。单独钳制最后一次扣减没有消除这种差异。
原清单描述的 `-5e-17` 不能直接视作本次代码中的精确触发值，因为现有库存写入本就保留九位。

新实现沿用普通进食的现有九位份量精度，同时量化累计目标、当次消耗和累计消耗登记，
库存与热量使用同一个实际消耗量。保留原有缺货守卫；容差内仅结算实际持有量，
实际不足超过容差仍拒绝，包括 `1.4e-9` 的不足。
技术依据参考 [Python 官方浮点说明](https://docs.python.org/3/tutorial/floatingpoint.html)：
二进制浮点运算会持续引入表示误差，因此这里同步记账精度，不扩大业务容差。

该规则仅作用于 `apply_meal_progress` 的普通 `eat` / `use` 路径。
`inventory_recipe` 仍由引擎独立分派到配方结算，保持合同中的可表示微量变化、不固定小数位、
不以库存容差放行不足输入的要求。

## 本次执行的验证

所有命令在隔离整合目录执行，使用主仓库现有 Python 环境，并显式选择隔离目录的生产代码与 AS2。
未启动真实模型请求，未读取或输出密钥。

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/src:/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock:/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/vendor/agentsociety2/packages/agentsociety2
```

新增库存六等分反例在修复前失败：第二次结算后库存加累计消耗按九位计仅为 `0.999999999`。
新增游标空值反例在修复前抛 `TypeError`，而测试要求 `ValueError`。

以下命令在最终实现上得到 **117 passed in 34.03s**：

```sh
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/life/test_dayrun_inventory_regressions.py \
  tests/life/test_dayrun_cursor_regressions.py \
  tests/life/test_commerce.py \
  tests/life/test_candidate_contract_boundaries.py \
  tests/life/test_candidate_wiring.py
```

新增库存测试遍历两至四十九等分，并在每次结算后执行 `WorldState` 与活动的 JSON 再水合；
还覆盖非均匀推进、重复结算、恰好归零、累计消耗、热量和容差边界。
新增游标测试包含真实引擎提交、拒绝事件与下一轮正常浏览，并验证内部异常不被转换。

以下相邻路径检查得到 **20 passed in 0.06s**：

```sh
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/life/test_domain_branch_coverage.py::test_basket_apply_meal_progress_durable_completion_and_paused \
  tests/life/test_category_runtime_edges.py
```

本次负责文件的 `git diff --check` 通过。全仓库测试、独立破坏性变异检查与最终合并由整合负责人完成，
上述局部结果不替代真实模型长跑验收。

## 保留的审计发现

| 发现 | 边界与影响 |
| --- | --- |
| 不可交谈人物仍进入伙伴枚举 | 主仓库与长跑树的 `_visible_people_ids` 都只采集身份，没有按 `can_talk` 过滤；异常已变为业务拒绝，但工具面仍会诱发不必要的错误选择 |
| 消息异常分类依赖错误文本 | `_send_message` 以异常字符串辨认收件人缺失，其余 `ValueError` 一律视为文本超长；当前两个已知域校验有测试，未来域模型新增校验时可能误分类 |
| 交谈捕获涵盖整个域调用 | 所有 `ValueError` 被标为伙伴不可交谈，包括理论上的构造错误；本次未以扩大捕获范围的方式修复其他路径 |
| 真实游戏观察缺少原接缝证明 | 增加 `data` 键的等待活动测试无法独立证明游戏会话能进入下一次 Agent 决策，最终整合必须补实际游戏路径 |
