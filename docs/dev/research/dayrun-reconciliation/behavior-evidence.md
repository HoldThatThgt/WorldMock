> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-reconciliation/behavior-evidence.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日运行行为证据与主线差距

本文只核对行为。旧运行是 `WorldMock-dayrun` 的 `runs/deepseek-day-openrouter/footprints.sqlite`、
`status.json` 和人物记忆文件，生产代码基线为 `761241c` 加未提交修改；现行主线是
`WorldMock` 的 `20dedcd`。两套代码不能混作同一次运行。附件诊断是待核实说法。
数据库以 `sqlite3.connect("file:.../footprints.sqlite?mode=ro", uri=True)` 读取，
`PRAGMA integrity_check` 为 `ok`；`status.json.status=completed`，事件数为 2,208。
时间均按 `Asia/Shanghai` 显示，旧运行模拟日为 2026-01-05。
旧运行 `manifest.json` 的 `run_id=047ce0eacbfc4a6f8e3a391f47095048`、
`code_sha256=e839aeebb120c589349c60756633b9119d86c0d4fa5c6e99bf0acd31c79f44c3`；
当前未提交工作树的内容是否逐字节等于运行时内容，未单独证明。

下文事件序号均是 `events.event_seq`。复核时可用以下只读查询读取结构化字段；
进食不能只数 `purchase`，还须核对配送和 `activity_progress.data.consumption`。

```sql
SELECT event_seq, json_extract(body, '$.agent_id') AS agent_id,
       json_extract(body, '$.data.interval_start') AS start_epoch,
       json_extract(body, '$.data.interval_end') AS end_epoch
FROM events
WHERE json_extract(body, '$.event_type') = 'activity_progress'
  AND json_extract(body, '$.data.activity.kind') = 'wait';
```

## 等待缺少可核验行为

**旧运行事实。** 按 `activity_progress` 的实际区间求和，人物一 `wait` 为 4,260 秒
（71 分钟、13 条进度），人物二为 6,836 秒（113 分 56 秒、17 条进度）。
人物一的 `wait` 请求事件 1055 在 18:53:58 提交 `planned_duration_seconds=2220`，
进度事件 1070、1098、1116、1119、1122、1155 合计覆盖至 19:30:58；
其间 19:30 的观察事件 1125 仍显示当前活动为 `wait`。
早晨的 07:45→08:00、08:41→09:00 附近也有等待，但旧时段计划的
`plan_block_boundary` 是背景条件：旧代码 `src/worldmock/life/schedule.py:15,76,482-513`
有段起点唤醒，不能把该原因投射到主线。

**主线现状。** `catalog/affordances.toml:36-45` 将 `wait` 设为无目标发现的基础活动，
同时声明 `relief.channel=intrinsic`。`src/worldmock/life/body.py:442-469`
会按费率记录被动娱乐缓解分录；这证明身体账本被结算，不能证明人物实际浏览了哪个内容。
`docs/dev/SPEC-day-plan-goals.md:9-11,25-27,59-80` 已把计时段改为当日目标，
删除段起点唤醒。因此“所有等待由计划段边界造成”只适用于旧运行的部分等待。

**差距与可写验收断言。** 数字足迹应能区分“没有可观察内容的等待”和有对象、
时段及结果证据的浏览。确定性回放可断言 `wait` 的累计秒数与区间一致；
没有浏览或内容事件时，导出与记忆不得把其内在缓解分录当成具体刷手机事实。
在提供真实内容入口的情境中，若人物选择浏览，须留下内容身份及实际接触区间，
而非仅有 `wait` 的身体分录。是否限制等待总量是产品阈值，旧运行不能给出阈值。

## 出行中的并行活动

**旧运行事实。** 人物一日程备注曾写“路上听播客”（动作事件 21 的 `plan_day`），
但用同一人物 `activity_progress` 区间交集检查，全部 16 条 `travel` 进度区间
与任何其他活动种类的进度区间交集均为空。人物一早上旅行事件 166 为
08:00:01→08:06:41，晚间旅行事件 1023、1025、1041 为
18:30:01→18:53:58；没有同时结算的内容活动。这只证明本次运行没有通勤内容足迹。

**主线现状。** 步行 `travel` 不声明 `attention` 占用，驾车则占用注意力、双手和车辆
（`catalog/affordances.toml:92-103`）。但人物状态只有一个 `current_activity_id`
（`src/worldmock/life/models.py:247-252`）；启动新主活动会先暂停旧活动
（`src/worldmock/life/engine.py:1078-1085`），`start_activity` 也拒绝已有当前活动
（`src/worldmock/life/activities.py:116-147`）。因此资源声明留下“步行时注意力可用”
的条件，当前活动生命周期却没有同一人物的两个同时推进槽位。
附件说“全系统完全无并行”过宽：规范明确不同人物可并行，环境事件与配送也可在活动期间到达
（`docs/dev/SPEC-life-runtime.md:166-183,188-194`；旧运行配送事件 1866 与人物一观看活动并存）。

**差距与可写验收断言。** 若产品要求通勤听播客，步行 `travel` 与播客播放必须在
同一模拟区间留下各自真实进度、内容身份和资源结算；驾车与占用全部注意力的内容
须按同一规则拒绝。不得只凭人物卡、计划文字或记忆推断发生了播放。
这一能力尚需决定主活动与随行内容的状态归属；资源锁为空本身不足以证明已实现。

## 餐馆进食路径

**旧运行事实。** 人物一 18:30 发现餐馆的动作事件 976，其结果事件 990 返回三家餐馆，
每家的 `supplies` 均为空。它 18:53:58 到达 `poi-3538682408`；观察事件 1045
的 `supplies=[]`、`available_inventory={}`，19:30 的观察事件 1125 仍为空。
其后提交上述 37 分钟 `wait`，19:30:59 出发回家（动作事件 1210）。
本运行 `corpora/zhangjiang-map/supplies.json` 对该地点的供给数为零；
现行主线同一语料文件对该地点的供给数也为零。此处首先是餐馆无可购买供给，
不能把“30 分钟可见期过期”判成已证实根因。

人物一 19:39:46 的购买请求事件 1403 选择另一门店 `supply-market-meal-0001`
配送到家；20:14:06 的环境事件 1866 确认 `meal-0001` 到达 `home`。
但人物一全天唯一 `consume` 请求是 15:45:01 的事件 791，目标为另一食品；
没有针对 `meal-0001` 的人物一进食请求或正时长消费进度。
人物二则有 20:00 与 22:26 的 `meal-0001` 进食请求事件 1826、2093，
随后有 `consumption` 进度，说明配送与进食链条在本运行并非普遍失效。
针对人物一晚餐的反证可直接复核：

```sql
SELECT event_seq FROM events
WHERE json_extract(body, '$.event_type') = 'action_requested'
  AND json_extract(body, '$.agent_id') = 1
  AND json_extract(body, '$.data.tool_name') = 'consume'
  AND json_extract(body, '$.data.request.items[0].product_id') = 'meal-0001';
-- 最终运行返回零行。
```

**主线现状。** 当前已有“可见供给→购买→交付→持有物进食”的完整路径：
观察把在场供给纳入呈现窗口，但实际出现取决于场景供给定义
（`src/worldmock/life/observe.py:72-83,887-890`）；可见凭据受地点和 1800 秒期限约束
（`src/worldmock/life/projection.py:29-30,68-74`），购买复查凭据
（`src/worldmock/life/storefront_runtime.py:18-25`）。发现结果可返回无供给餐馆
（`src/worldmock/life/discovery.py:52-65`）。自提在场即时交付、配送到达才加库存
（`docs/dev/SPEC-map-and-storefront.md:95-107`；`src/worldmock/life/basket.py:64-94`）；
进食只接受已持有食品并产生消费进度
（`docs/dev/SPEC-life-realism.md:389-394`；`src/worldmock/life/engine.py:1169-1204,1516-1527`）。

**差距与可写验收断言。** 对允许堂食的冻结餐馆输入，应至少有可发现、可购买且
支持在场履约的餐食供给；人物到店后能经真实模型工具到 `purchase`、即时交付、
`consume` 的正时长进度走通，食物和资金守恒。若店确实无供给，观察与评估应明确
“此处没有餐食”，不能把到店加等待当作吃过饭。是否把堂食合并为单个动作由产品
合同决定；现有结算路径并不要求先发明新动作。

## 记忆事实约束

**旧运行事实。** 人物一的 `as2/agents/agent_0001/memory/episodes.jsonl:75`
（记忆 `m_00000000_000075`）于 18:53:58
写“煲仔饭刚上桌……自己吃”，事件 1060 确认该条记忆已落盘；而同刻观察事件 1045
只证明到店、无供给、无持有餐食，随后动作事件 1055 是 `wait`。
同一文件第 78 行的 `m_00000000_000078` 又据前序记忆写“把那份煲仔饭吃完了”；
最终 `as2/agents/agent_0001/MEMORY.md:20-25` 将“傍晚吃了顿热乎的煲仔饭”
写成长期记忆。引用身份可存在，所引用事件却不支持“实际吃过”这一命题。
人物一晚间那份已到家但未消费的外卖，也不能由下单或配送证明已经吃过。

**主线现状。** `finish` 提示明确要求只记观察事实和意图、不可把待处理动作写成成功
（`custom/agents/life_agent.py:352-362`）；本地校验要求来源引用出现在本轮可见身份集合
（同文件 `:953-959`），模型结构还允许引用既有记忆身份
（`docs/CONTRACTS.md:928-950`）。人物执行结束后，AS2 以 `finish_memories`
写入 episodes、触发巩固（`vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/person.py:524-543`；
`agent/memory_runtime.py:301-347`）；环境只登记文件摘要与记忆身份
（`src/worldmock/life/engine.py:547-566`）。这些是结构和持久化校验，未见把记忆命题
与 `consume`、`travel` 等已结算事件作语义对账的现行路径。

**差距与可写验收断言。** 对“已吃完”“已到家”“已播放”一类完成事实，
验收须要求相应已结算事件及实际区间，不能只凭观察事件、意图或另一条记忆。
以本次 18:53 的真实观察和 `wait` 结果构造反例：来源身份合法但无消费进度时，
该记忆不能升级为已完成的进食事实；同一反例不得在巩固后进入长期记忆。
意图仍可保存，但须与完成事实分开。具体采用写入拒绝、标注未证实或巩固过滤，
需要父任务确定权威合同。

## 唤醒与活动持续性

**旧运行事实。** 完整库中有 198 条 `activity_progress`、138 条 `decision_started`
和 485 条 `model_request`；88 条进度标记 `decision_offered=false` 且
`wake_reasons=[]`。因此附件“每次结算都重新决策”和“98 次决策、242 次请求”
都不适用于这份最终完成的数据库；附件曾在运行中取快照，后两项不能作为最终统计。
不过 19:41:46→19:53:46 人物一观看同一电影时，每分钟的进度事件
1443、1474、1501、1530、1557、1584、1611、1638、1667、1694、1721、1748、1775
均再次提供决策，原因均含 `task_due_soon`，并导致重复 `continue_activity`。

**主线现状。** 规范把 900 秒结算与唤醒分开；静默结算续行已提交的活动，
不调用模型（`docs/dev/SPEC-life-runtime.md:85-145`；
`docs/design/04-simulation-engine.md:485-504`）。实现确实在结算后先评估唤醒、
把 `decision_offered` 和原因写入进度；未唤醒时重新安排活动反馈
（`src/worldmock/life/rhythm.py:79-105,444-465`）。
但 `src/worldmock/life/schedule.py:224-226,274-284` 对 `task_due_soon`
使用整个截止前 30 分钟的电平条件；从源码推断，只要该任务仍被视为待办，
此窗口内的其他结算边界仍可能重复命中。主线已删除旧计划段边界
（`docs/dev/SPEC-day-plan-goals.md:59-80`），尚无主线长跑证明晚间重复唤醒已消失。

**差距与可写验收断言。** 在长时观看或工作跨多个 900 秒边界、无新消息、
体感跨级或其他新事件的情境中，所有中间进度应为静默续行，并保持同一活动身份与
累计进度；截止预警首次进入窗口提供一次机会，后续窗口内结算不得仅因同一持续条件
反复唤醒。若人物实际切换活动，暂停与新活动各留独立事实。正式一日行为报告应分别
给出结算数、决策机会数、模型请求数与重复唤醒原因，不以结算数冒充决策数。

当前主线的确定性代码与规范能支撑上述差距判断；这里没有对 `20dedcd` 启动真实模型长跑，
因此主线在完整自然日的等待占比、餐馆选择和记忆准确率仍未实测。
