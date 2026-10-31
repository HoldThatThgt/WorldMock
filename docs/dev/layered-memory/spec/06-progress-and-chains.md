# 06 进度层、待办层与同秒续接

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。第五批裁定（2026-10-31）修订。

## 目标与依据

本章把提案（docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md，下称提案）里「此刻还没了结的事」和「跨决策保留的待办」两格落成实现：
进度层按已发布界取下界、按记录种类各取各的界、带入规则改为不分种类；待办层新增在途订单 `orders`，与零点计划共用一个
选择函数；同秒续接与进度层脱钩，`changes` 加 `memory` 与 `orders` 两键、删去人设增量，「今天」不是前缀时不续接。

实现的提案段落：

| 提案位置 | 本章落实的内容 |
| --- | --- |
| 「四格落到提示层上」·四格表的「今天进度」「长期进度」两行 | 进度层只写还没了结的事；待办层写暂停活动、个人事务、在途订单（键名 `orders`）与今日计划 |
| 「四格落到提示层上」·各部分上限表的「待办层」行 | 在途订单 ≤6 行，多出的合成一行「另有 N 笔在途」 |
| 「今天条目」第 10 点「与进度层」 | P_k = max(C_{k−1}, B_D)；两层按事件序号不相交，只有两处写明的重叠 |
| 「与现有机制的交互」·「同秒续接」 | `layer_changes` 的键序；`memory` 是 `{"added":[…]}`；`orders` 按订单标签列 added 与 removed；`_view`、`_overlay`；非前缀时不续接；环境侧脱钩；两份模块说明；容量第 0 步 |
| 「与现有机制的交互」·「进度层」 | 记录种类与下界的表；会话记录不带 `self_note`；不再保留最新的 report_status；`rejected_today` 不变；`session_started_at` 保留；`memory_view` 返回 P_k、observe.py 传给 `progress_view`；两份模块说明；跨夜睡眠的测试 |
| 「与现有机制的交互」·「其余各项」·「零点计划」 | 在途订单与待办层共用一个辅助函数（含 `ready_for_pickup`） |
| 「人设层看法的去向」·「链内」 | 从 `_view`、`_overlay` 和 `layer_changes` 删掉 relationships 和 preferences |
| 第三章「代码」表 | progress_layer.py、session_bounds.py、todo_layer.py、day_plan_inputs.py、continuation.py、life_continuation.py、observe.py:57-58 各行 |
| 第三章「设计文档」表 | 06-agent.md :153-155、:184（不改，见决定表）、:204、:267-271 与「进度、待办与记忆」（:265 归第 02 章）；04-simulation-engine.md 的在途订单；05-recording-engine.md「权威事实与投影」的两层关系；09-io-formats.md 的待办层机器形状 |
| 第三章「测试与门禁」 | 同秒链里的 memory 增量；已发布界；提交里产生的提醒不丢；计划提醒按时刻取界；跨夜睡眠经带入记录可见；所列 test_same_second_continuation.py、test_carried_records.py 等的改动 |

适用的裁定（提案第五章）：

| 裁定 | 本章的落实 |
| --- | --- |
| 1（选项 A） | 已了结的核验事实在「今天」；进度层只留未了结的事和上次决策之后才到的事 |
| 2（选项 A）、26（选项 A） | 任何世界请求都结束一次决策、都产生条目；report_status 的总结在「今天」，进度层不再保留 report_status 的记录 |
| 16（选项 A） | 待办层的新键用英文 `orders`；续接 `changes` 的新键用英文 `memory` |
| 17（选项 B）、29 | 看法离开人设层，人设层整次运行不变，续接 `changes` 不再有 relationships 与 preferences |
| 20（选项 B）的配套 | 续接 `changes` 加 `memory`（`{"added":[…]}`）；「今天」不是前缀时不续接，改用新快照；续接构造与进度层脱钩 |
| 21（选项 A）的进度层一侧 | 这段停留或路程里做完的查询带答案留在进度层，出发后离开 |
| 24（选项 A） | 兜底改写早先的条目时，「今天」不再是前缀，本次决策不续接 |

编排已定、本章照用的全局决定：本章写终稿，提案 P1 期间的过渡形态（链内仍有人设增量、旧层序）不实现。
条目内容、C_j 与「今天」的递推归第 02 章「今天条目」与第 01 章「记忆层：形状、预算、兜底与层序」；零点界 B_D 与归日规则归
第 03 章「类别、归日与近三天」。

依据数字（取自提案与研究目录/out3/reports/code.md，用于说明，不是验收门槛）：

| 量 | 数 | 出处 |
| --- | --- | --- |
| 进度层现状 | 中位 14,663 字，p90 27,618，最大 42,812；10-11 14:30 之前有 30 次决策请求带着 21.7k–42.3k 字的进度层 | 提案 F7 |
| 进度层按已发布界的估计 | 中位约 2–3k，最大不到 10k | 提案「四格落到提示层上」；研究目录/econ/slim.py |
| 同秒链 | 103/406 次决策在链里；每次续接新增 1 条条目，最多 2 条，平均 157 字 | 提案 F4、「同秒续接」 |
| 不脱钩时的断链率 | 100%（上一决策的请求与结果序号都不超过 C_{k−1}） | code.md (a)「Environment side」 |
| 链里带过人设增量的续接 | V 61/101 次，中位 106 字 | 提案「人设层看法的去向」 |
| 待办层现状 | 865 / 942 / 942 字（中位/p90/最大）；目标上限 ≤1,600 | 提案各部分上限表 |
| 提交里触发的回调 | V、B 里 0 次，代码允许 | code.md (b)「What the progress layer keeps」 |

## 行为

条款编号【P…】【T…】【C…】供「测试」一节引用。

### 进度层

**【P1】已发布界。** 第 k 次决策的观察里，已发布界 `P_k = max(C_{k−1}, B_D)`：
- C_{k−1} 是上一次决策提交的末事件序号，定义与求法归第 02 章「今天条目」（「窗口」一条）；没有上一次决策时取 0。
- B_D 是 D 日（观察时刻所在的场景本地日）的零点界：D 日 00:00 之前最后一个事件的序号，由第 03 章「类别、归日与近三天」定义的
  零点界函数 `day_bound` 求出，它的值恒等于 `store.last_event_seq_before(D 日本地 00:00 的时刻)`（没有这样的事件时为 0）。
- `memory_layer.memory_view` 求出 P_k，放在返回值 `MemoryView.published`（字段与组装照第 02 章）；`ObservationBuilder._observation_data`
  （observe.py:57-58）把它与同一次观察的 `MemoryView.facts.kits` 交给 `progress_layer.progress_view`。进度层不自行求 C_{k−1}。
- 序号不超过 P_k 的事件，要么已经写进「今天」的某一条，要么属于前一天（第 02 章的零点收尾条目）。

**【P2】上一次决策。** 进度层另用到上一次决策的观察序号与时刻：
- 上一次决策是第 02 章【E1】的最后一次决策：`decision_commits(store, agent_events(store, agent_id, catalog.kit_states()),
  store.last_event_seq)[-1]`（`agent_events`、`decision_commits` 在 observation/memory/entries.py，参数照第 02、08 章；截止序号取
  构建观察时的 `store.last_event_seq`，与 `published_today` 相同）；返回空元组时没有上一次决策。
- 决策时刻是这个 `Decision` 的 `occurred_at`；观察是 `store.read_decision_observations(agent_id, 它的 decision_id, 它的 occurred_at)`
  中序号最大的一条，观察序号是这条观察的 `event_seq`。
- 有上一次决策而读不到它的观察时，写英文错误日志 `Previous decision <decision_id> has no observation`，再抛
  `IntegrityError("Previous decision has no observation")`。
- 没有上一次决策时，观察序号取 0，计划提醒不设时刻下界。

**【P3】记录种类与各自的界。** 进度层 `events` 只由下表四种记录组成，每种只按自己那一行取舍：

| 记录 | `type` | 出现条件 |
| --- | --- | --- |
| 记忆事实：送达与到站、个人事务办成 | `delivery`（来自 `delivery_arrival`）、`errand_completed` | 来源事件 `visibility == "agent"`，序号 > P_k |
| 非记忆世界记录：app 推送、回调提醒、余额减少、个人事务提醒 | `app_push`（只取 `pushed` 不为假的）、`callback_reminder`、`balance_decreased`、`errand_reminder`（来自 `errand_reminded`） | 来源事件 `visibility == "agent"`，序号 > max(上一次决策的观察序号, B_D) |
| 派生的计划提醒 | `plan_reminder` | 今日计划里 `reminded_at` 不为空、晚于上一次决策的时刻（没有上一次决策时不设下界）、不晚于此刻的目标，各一条 |
| 开始请求的记录 | `tool_call` | 只有【P4】【P5】的带入记录 |

- 非记忆世界记录取上一次决策的观察为界，所以上一次决策提交里产生的回调（decision_coordinator.py:413-454 的
  `_action_callbacks`、`_same_moment_callbacks`）照样出现在第 k 次决策里；序号不晚于那次观察的已经在第 k−1 次决策里出现过。
- 计划提醒的时刻界取代 session 窗口（progress_layer.py:280-286 的 `in_range` 删除）。同一秒链里的下一次决策不再看到
  上一次决策已经看到的提醒。
- 各种记录的字段与写法不变：送达、事务、推送、回调提醒、余额减少的记录照 progress_layer.py:209-266，计划提醒照 :287-291。
- 记忆事实种类的闭集是 `FACT_EVENT_KINDS = frozenset({"delivery_arrival", "errand_completed"})`，非记忆种类的闭集是
  `NOTICE_EVENT_KINDS = frozenset({"errand_reminded", "app_push", "callback_reminder", "balance_decreased"})`；
  其余环境事件种类不进进度层（与现行一致）。

**【P4】带入记录之一：占槽位的本人活动。** `slot_activities(context.open_activities)`（activity_control.py:82-84）返回的每项活动
各有一条 `tool_call` 记录，不分种类：睡眠、出行、购买、查询、在地点做事与用内容 app 都算；进行中与待收尾都算。
- 记录是开始它的那条请求：请求事件按 `activity.origin_request_event_id` 读出，第一条结果按
  `activity.data["accepted_event_id"]` 读出（每项活动在受理时都写下这个键，decision_coordinator.py:334-337）。
- 活动已结束（`ended_at` 不为空）时另有终态结果：开始动作在第一条结果之后的第一条 `action_result`。终态结果与活动终结在
  同一次提交里、发生时刻等于 `ended_at`（time_advance.py:303-317、activity_control.py:478-488），所以只读
  `max(第一条结果的序号, store.last_event_seq_before(activity.ended_at))` 之后的本人 `action_result`，取第一条
  `action_id == activity.origin_action_id` 的。读不到时写英文错误日志 `Ended activity <activity_id> has no terminal result`，
  再抛 `IntegrityError("Ended activity has no terminal result")`。
- 记录的 `result` 是最新的一条结果（有终态结果就是它，否则是第一条），经 `public_result` 投影（第三个参数是同一次观察的
  `ActivityFacts.kits`，第 08 章：开始结果与完成结果里用到食材包的那一行写食材包句）；记录形状为
  `{"occurred_at": 请求时刻, "type": "tool_call", "tool": 工具名, "arguments": 请求原文, "result": …}`，键序如此，没有 `self_note`。
- 来源是请求、第一条结果，有终态结果时再加终态结果；带入记录不看任何下界，所以活动跨过零点、跨过 session 都照样在。
- 暂停着的活动不占槽位，不带入，只列在待办层 `paused_activities`；恢复后重新占槽位，重新带入。
- 现行「只带 interact」「本段下界之后没有开始它的请求」「本段里有它的第二条结果」三条判定（progress_layer.py:158-166）、
  「出行、购买的记录不带入」与「活动恰在到达那一秒做完时，到达后的停留里不带入」（06-agent.md:271）一并取消。

**【P5】带入记录之二：这段停留或路程里做完的查询。** 本人的查询活动满足以下三条时，照【P4】的写法另有一条 `tool_call` 记录，
结果是带答案的终态结果：
- `kind == query`、`status == completed`（查询做完自己结束，`concluded_at` 随之写下，不占槽位）；
- `ended_at` 不早于当前 session 的开始时刻；
- 终态结果的序号大于当前 session 的事件序号下界（`session_bound` 的第二个值，即 `session_lower`）。

人物出发后 session 换了，这条记录离开进度层。提前结束、被截断的查询状态不是 `completed`，不在这一条里。
查询不能在路上开始，出发前要先结束进行中的查询（tests/integration/life/test_query_activity.py 的
`test_a_query_cannot_start_on_the_road`、`test_departure_waits_until_the_running_query_is_concluded`），所以做完的查询
总在开始它的那段停留里。

**【P6】不再出现在进度层的东西。**
- 控制请求（report_status、pause_activity、conclude_activity、revise_day_plan）、被拒的请求、已经收尾的活动的开始请求
  （【P5】的查询除外）：它们的
  请求与结果都在第 k−1 次或更早的决策提交里，序号不超过 C_{k−1} ≤ P_k，事实与自述在「今天」的条目里（第 02 章）。
  进度层因此不再保留最新的 report_status 记录。被拒的原因只在 `rejected_today`。
- `self_note`：没有任何进度层记录带它（现行 progress_layer.py:137-138 删除），回复正文只在「今天」的条目里（第 02 章的「正文：」）。
- `rejected_today` 的取法、形状与上限不变（progress_layer.py:316-347）；它的零点界改为调用第 03 章的同一个零点界函数，值不变。

**【P7】排序、元数据与键。**
- `events` 按 `(发生时刻, 推导记录在前, 目标次序, 最小来源序号)` 排列，键与现行相同（progress_layer.py:110-112、:291）：
  `tool_call` 的发生时刻是请求时刻，所以带入的记录排在前面。
- `session_started_at` 键保留，值是当前 session 的开始时刻（带时区 ISO 8601）。层的键序仍为 `layer`、`session_started_at`、
  `events`、`rejected_today`。
- 私有浓缩元数据 `meta` 与 `event_ids` 的算法不变：`protected` 照 `_protected`（:303-313），`lines` 照
  `record_lines(context, record, sources)`，`event_ids` 是全部记录来源事件身份按出现次序去重。

**【P8】两层不相交。** 进度层 `events` 的来源事件与「今天」各条目的来源事件按序号不相交，只有两处重叠：
1. 占槽位活动的带入记录与条目里关于这项活动的行：开始行只共用名称与开始时刻；待收尾的活动若在 P_k 之前结束，条目里另有
   它的结束行（购买另有购买行），与带入记录的终态结果是同一事实。
2. 做完的查询的带入记录与条目里的「查完〈app〉·〈范围〉」一行；答案只在带入记录与条目全文的「当时查到」里（第 02、05 章）。

由此：进度层里序号不超过 P_k 的来源事件，只能是【P4】【P5】带入记录的来源，或【P3】第二行在上一次决策提交里产生的非记忆世界
记录（序号落在 (上一次决策的观察序号, C_{k−1}]）；后者不是任何条目的行或项的来源（第 02 章【E12】：`app_push`、`callback_reminder`、
`balance_decreased`、`errand_reminded` 不产生行），所以两层仍按来源不相交。`rejected_today` 是当日拒绝的汇总，不在这一条之内。

**【P9】接口变化。** `progress_view(context, store, published, kits)` 的第三个参数是 P_k，第四个参数是同一次观察的
`ActivityFacts.kits`（第 08 章：每次调用 `public_result` 都传它），不再接收 `ActivityFacts`；`records_after` 删除（唯一的生产调用方
是续接，见【C1】）。

### 待办层

**【T1】在途订单。** 本人的在途订单是满足以下全部条件的商品履约记录（`DeliveryState`，state/world/orders.py:14-57）：
`agent_id` 是本人；`paid_at` 不为空；`status` 属于 `OPEN_ORDER_STATUSES`（`pending` 或 `ready_for_pickup`，progress_layer.py:38）。
- 未付款的 `pending` 记录是进行中购买的报价（simulation_engine/commerce/purchase/basket.py:68-79 建记录时就是 `pending`），不算在途；它由进度层带入的购买记录表达。
- `todo_layer.open_orders(state, agent_id)` 是唯一的选择函数，按 `(expected_at, order_id)` 升序返回记录元组。待办层 `orders`
  与零点计划的「在途订单」都只经它取订单。
- 到店当场买的记录付款时当秒收货（simulation_engine/commerce/purchase/storefront_runtime.py:138-140），永远不在途。

**【T2】`orders` 的行。** 待办层的键依次是 `layer`、`paused_activities`、`errands`、`orders`、`day_plan`；`orders` 是数组：
- 把 `open_orders` 的结果按 `(goods_id, created_at, status, destination_id, expected_at)` 分组，组按首条记录在结果里的先后排列；
  每组一行。`DeliveryState.quantity` 恒为 1（orders.py:38），所以组的份数等于组里的记录数。
- 每行恰为四个键，次序固定：
  - `order`：`order_label({"goods_id": …, "quantity": 份数, "created_at": …}, 商品名表, (时区, 此刻))`（progress_layer.py:269-273），
    即「〈商品名〉 〈份数〉 份（〈购买时刻〉 购买）」，购买时刻与此刻不在同一本地日时带「M月D日 」；
  - `status`：`"pending"` 或 `"ready_for_pickup"`；
  - `destination`：`destination_id` 那个地点的显示名；
  - `expected_at`：冻结的预计到达时刻，带时区 ISO 8601（`iso_time`）。
- 行数多于 `ORDER_ROW_LIMIT = 6` 时，只列前 6 行，末尾再加一行合成行 `{"order": f"另有 {N} 笔在途"}`，N 是没列出的行数
  （正整数，十进制，数字两边各一个半角空格）。恰好 6 行时没有合成行。数组因此至多 7 项。
- 订单在付款完成那一刻进入 `orders`（外卖、快递）；外卖送达、快递被领取时离开（状态变成 `delivered`）；快递到站时同一行的
  `status` 由 `pending` 变成 `ready_for_pickup`。

**【T3】零点计划的「在途订单」。** `day_plan_inputs.plan_inputs` 的「在途订单」改为对 `open_orders(context.state, agent_id)` 的每条记录
各写一行，行仍是 `{"商品", "数量", "预计送达", "送到"}` 四键，写法不变（day_plan_inputs.py:261-268）；与现行相比：
- 已到驿站、等本人领取的 `ready_for_pickup` 记录也在其中，「预计送达」写它冻结的 `expected_at`，「送到」写驿站的显示名；
- 未付款的 `pending` 记录不在其中。
零点计划输入的其余部分不归本章（第 03、08 章）。

### 同秒续接

**【C1】环境侧的结果：直接按请求投影。** `same_second_chain`（continuation.py:44-71）的成链条件与 `messages`、`assistant` 的重建不变；
`tool_results` 改为：
- 对上一决策登记的每条请求（`_decision_evidence` 返回的 `registered`，一条或开始组的两条，按登记次序），读它最新的一条结果：
  本人在这条请求序号之后的 `action_result` 中 `action_id` 相同的最后一条。
- 某条请求读不到结果时返回 None（断链，同现行 `len(own) != 1` 的断链）。
- `result` 是 `tool_call_record(context, 请求, 那条结果, kits)["result"]`，与进度层带入记录的 `result` 是同一个投影（`public_result`）；
  `kits` 是 `activity_facts(agent_events(store, agent_id, context.catalog.kit_states())).kits`（第 02、08 章），与本次观察
  `MemoryView.facts.kits` 同一份（观察事件不是缓存读的事件类型，观察提交前后读到的相同）。
- 不再读进度层：`records_after` 与它对 `_session_records` 的复用删除。

**【C2】环境侧的 `events`。** `events` 是 `notice_records(context, store, previous.event_seq)`：序号大于上一次观察（`previous`，
continuation.py:74-82 读出的那条）的非记忆世界记录（【P3】第二行的四种），形状与进度层同类记录逐字段相同，按【P7】的键排列。
送达、到站与办成不在 `events` 里：它们若发生在上一决策的提交里，是上一决策条目里的事实，经【C4】的 `memory.added` 送达。

**【C3】`changes` 的键集与次序。** `layer_changes` 返回的 `changes` 只可能有下列键，次序恰为：

| 键 | 值 | 比较单位 |
| --- | --- | --- |
| `targets` | `{"updated": [完整目标行…], "removed": [名称…]}`，空的子键省略 | 工具层目标，按 `name`（不变） |
| `memory` | `{"added": [条目…]}` | 记忆层「今天」，见【C4】 |
| `paused_activities` | `{"added": [完整行…], "removed": [名称…]}` | 待办层暂停活动，按 `name`（不变） |
| `errands` | `{"added": [完整行…], "removed": [名称…]}` | 待办层个人事务，按 `name`（不变） |
| `orders` | `{"added": [完整行…], "removed": [订单标签…]}` | 待办层在途订单，按 `order`，见【C5】 |
| `day_plan` | 今日计划整体（可为 null） | 整体替换（不变） |
| `body` | 环境层身体感受整体 | 整体替换（不变） |

- 没有改动的键不写；`added`/`updated` 是此刻新出现或内容有变的完整行（此刻次序），`removed` 是已不存在的名称或标签（所见次序），
  与现行 `_keyed_changes`（life_continuation.py:143-147）相同。
- 相对的「所见」照旧是链首快照依次叠加此前各续接 tool 消息的 `changes`（`_earlier_changes`，:102-108，不变）。
- 人设层与所在地点在链内不变，不进 `changes`；`relationships`、`preferences` 两键删除（:49-52、:121-122、:128、:138-139），
  因为人设层不再含看法（第 07 章「人设层与系统层规则」）。

**【C4】`memory.added` 与前缀判定。**
- 所见的「今天」是链首快照里记忆层的 `今天` 列表，依次接上此前各 `changes.memory.added`；此刻的「今天」是本次观察记忆层的
  `今天` 列表。两边都经 `json.loads(render_layer_json(列表))` 往返规范化后比较（narration.py:163-175）。第 01 章规定记忆层总有
  `今天` 键，空时是 `[]`。
- 所见列表是此刻列表的前缀（长度不超过、逐项相等）时，`added` 是此刻列表在所见长度之后的部分；为空时不写 `memory`。
- 所见列表不是此刻列表的前缀时（只会因为第 01 章的兜底改写了早先的项），这次决策不能续接：见【C6】。
- `added` 里的每一项与新快照里「今天」对应的项在 `render_layer_json` 下逐字节相同。

**【C5】`orders` 增量与重复标签。** 所见与此刻的 `orders` 各按 `order` 建成映射，再按【C3】的规则比较；合成行也按它的 `order`
文字参与比较。链首快照或此刻的 `orders` 里有两行 `order` 相同（同一本地分钟里两次购买同一商品、而两组的购买秒、状态、
目的地或预计到达不同）时，这次决策不能续接：见【C6】。

**【C6】不能续接。** `layer_changes` 在【C4】的非前缀或【C5】的重复标签时返回 None，并各写一条英文警告日志：
- `Memory layer today list is not an extension of the list the model has seen; using a fresh snapshot`
- `To-do layer orders repeat a label; using a fresh snapshot`

`continuation_messages` 随之返回 None；宿主照现行代码改用新快照（custom/agents/life_agent.py:488-492：`continued` 为 None 时
`_react_messages` 不注入，预算没有 `fresh_messages`）。这次决策的首个请求恰为 system 与 user 两条消息。

**【C7】容量第 0 步。** 不改：新快照由当前各层构建（life_agent.py:491），已含全部已发布条目；第 0 步把链前缀整段换成新快照
（prompt_capacity.py:93-97），被换掉的 tool 消息里的 `memory.added` 与新快照里的「今天」末尾是同一批条目，既不重复也不丢失。

**【C8】浓缩不再去 `self_note`。** 进度层记录不再带 `self_note`，`_condensation_steps` 的第一步（prompt_capacity.py:123-126，
「从旧到新逐条去掉未受保护记录的 self_note」）删除；浓缩只剩折叠为 `condensed` 记录这一种步骤，其余不变。

## 接口与文件

行号都在 f67c25e5 上。中文 `@brief` 写要点；代码、日志和报错用英文。每个函数 ≤50 行、≤5 个参数（tests/unit/engineering/test_style_gate.py）。

### `src/worldmock/simulation_engine/observation/layers/progress_layer.py`

模块说明（:1-5）改为：

```python
"""@brief 进度层：此刻还没了结的事，只从真实人物可见事件和已交付提醒派生（docs/design/06-agent.md「进度、待办与记忆」）。

每种记录各取各的下界：送达、到站与办成取已发布界 P_k = max(C_{k-1}, B_D)，序号不超过它的事实已写进记忆层「今天」或属于
前一天；app 推送、回调提醒、余额减少与事务提醒取上一次决策的观察与零点界中较大的一个，上一次决策提交里产生的不丢；计划提醒
取提醒时刻晚于上一次决策的时刻。开始请求的记录只有带入的记录，不看下界：占槽位的本人活动（进行中或待收尾，不分种类）与这段
停留或路程里做完的查询，请求与第一条结果按事件身份读出，结果取最新的一条。控制请求、被拒的请求与已收尾的活动不在这里，
记录不带 self_note；被拒原因只在 rejected_today。
"""
```

常量（`OPEN_ORDER_STATUSES`，:37-38 不变；`_ERRAND_RECORDS`，:39-40 不变），新增：

```python
#: 记忆事实的环境事件种类：它们的事实写进记忆层「今天」的条目，进度层只写序号大于已发布界的。
FACT_EVENT_KINDS = frozenset({"delivery_arrival", COMPLETED_KIND})
#: 非记忆的环境事件种类：不进今天条目，进度层只写序号大于上一次决策观察（且大于零点界）的。
NOTICE_EVENT_KINDS = frozenset({REMINDED_KIND, "app_push", "callback_reminder", "balance_decreased"})


@dataclass(frozen=True, slots=True)
class _PreviousDecision:
    """@brief 上一次决策：它的观察事件序号与决策时刻。"""

    observation_seq: int
    decided_at: int
```

公开函数：

```python
def session_bound(store: FootprintStore, state: WorldState, agent_id: int) -> tuple[int, int]:
    # 不变（:62-68）。


def progress_view(context: LayerContext, store: FootprintStore, published: int,
                  kits: Mapping[str, KitUse]) -> ProgressView:
    """@brief 进度层 JSON 与私有浓缩元数据及真实来源事件身份。

    :param published: 已发布界 P_k（memory_view 求出）：送达、到站与办成只写序号大于它的。
    :param kits: 同一次观察的 ActivityFacts.kits：带入记录的结果投影（public_result）写食材包句。
    """
    stay = session_bound(store, context.state, context.agent_id)
    cached = agent_events(store, context.agent_id, context.catalog.kit_states())
    previous = _previous_decision(store, cached, context.agent_id)
    now, zone = context.state.current_time, context.config.timezone
    day = day_bound(store, local_date(now, zone), zone, now)
    notice_after = max(0 if previous is None else previous.observation_seq, day)
    records = sorted([*_carried_records(context, store, stay, kits),
                      *_world_records(context, store, (published, notice_after)),
                      *_plan_reminders(context, None if previous is None else previous.decided_at)],
                     key=lambda item: item.key)
    ...  # meta、layer 与 ProgressView 照【P7】，session_started_at 取 stay[0]


def notice_records(context: LayerContext, store: FootprintStore, after: int) -> list[dict]:
    """@brief 序号大于 after 的非记忆世界记录（推送、回调提醒、余额减少、事务提醒），按进度层的次序；同秒续接的 events 读它。"""
    items = _world_records(context, store, (store.last_event_seq, after))
    return [item.record for item in sorted(items, key=lambda item: item.key)]


def tool_call_record(context: LayerContext, request: Event, result: Event, kits: Mapping[str, KitUse]) -> dict:
    """@brief tool_call 记录：请求时刻、工具、请求原文与 result；result 是给定结果的公开投影，kits 是同一次观察的
    ActivityFacts.kits。"""
    data = request.data
    return {"occurred_at": iso_time(request.occurred_at, context.config.timezone), "type": "tool_call",
            "tool": data["tool_name"], "arguments": data["request"],
            "result": public_result(result.data, context.inputs, kits)}


def order_label(order: dict, names: dict[str, str], clock: tuple[str, int]) -> str:
    # 不变（:269-273）。


def rejected_today(context: LayerContext, store: FootprintStore) -> list[dict]:
    # 行为不变（:316-347）；:322 的零点界改为调用第 03 章的零点界函数。
```

`progress_view` 里 `notice_after = max(0 if previous is None else previous.observation_seq, day)` 一行逐字如此（源码变异以它为原文）。
`day_bound` 的签名与行为照第 03 章（`day_bound(store, day, timezone, now)`，第 03 章 `rejected_today` 的 :322 用同一写法）。

私有函数（新增或改写）：

```python
def _previous_decision(store: FootprintStore, cached: AgentEvents, agent_id: int) -> _PreviousDecision | None:
    """@brief 上一次决策（第 02 章【E1】的最后一次决策）的观察（序号最大的一条）与决策时刻；没有决策时为 None，
    缺观察时 IntegrityError。"""
    decisions = decision_commits(store, cached, store.last_event_seq)
    if not decisions:
        return None
    last = decisions[-1]
    observations = store.read_decision_observations(agent_id, last.decision_id, last.occurred_at)
    if not observations:
        logging.error("Previous decision %s has no observation", last.decision_id)
        raise IntegrityError("Previous decision has no observation")
    return _PreviousDecision(max(event.event_seq for event in observations), last.occurred_at)


def _carried_records(context: LayerContext, store: FootprintStore, stay: tuple[int, int],
                     kits: Mapping[str, KitUse]) -> list[_Record]:
    """@brief 全部带入记录：每项带入活动一条；kits 交给结果投影。"""
    return [_carried_record(context, store, activity, kits) for activity in _carried_activities(context, store, stay)]


def _carried_activities(context: LayerContext, store: FootprintStore, stay: tuple[int, int]) -> list[ActivityState]:
    """@brief 要带入的本人活动：占槽位的（进行中或待收尾，不分种类），加上这段停留或路程里做完的查询。"""
    carried = {activity.activity_id: activity for activity in slot_activities(context.open_activities)}
    carried.update((activity.activity_id, activity) for activity in _finished_queries(context, store, stay))
    return list(carried.values())


def _finished_queries(context: LayerContext, store: FootprintStore, stay: tuple[int, int]) -> list[ActivityState]:
    """@brief 这段停留或路程里做完的本人查询：completed、结束不早于本段开始、终态结果序号大于本段下界。"""
    # 照【P5】，遍历 context.state.activities。


def _carried_record(context: LayerContext, store: FootprintStore, activity: ActivityState,
                    kits: Mapping[str, KitUse]) -> _Record:
    """@brief 一项带入活动的 tool_call 记录：请求与第一条结果按事件身份读出，结果取最新的一条（结束了就是终态结果）。"""
    request = store.read_event(activity.origin_request_event_id)
    own = [store.read_event(activity.data["accepted_event_id"])]
    if activity.ended_at is not None:
        own.append(_terminal_result(store, activity, own[0]))
    return _call_record(tool_call_record(context, request, own[-1], kits), request, own)


def _terminal_result(store: FootprintStore, activity: ActivityState, first: Event) -> Event:
    """@brief 已结束活动的终态结果：开始动作在第一条结果之后的那条 action_result，只从结束那一秒读起；缺失时 IntegrityError。"""
    # 照【P4】。


def _world_records(context: LayerContext, store: FootprintStore, bounds: tuple[int, int]) -> list[_Record]:
    """@brief 人物可见的环境事件记录：记忆事实只取序号大于 bounds[0] 的，非记忆提示只取序号大于 bounds[1] 的。"""
    # 只读 after=min(bounds) 之后的本人 environment_event 一次；visibility 不是 agent 的跳过；
    # kind 属于 FACT_EVENT_KINDS 且 event_seq > bounds[0]：delivery_arrival → _delivery_records，errand_completed → _errand_record；
    # kind 属于 NOTICE_EVENT_KINDS 且 event_seq > bounds[1]：errand_reminded → _errand_record，其余三种照 :220-233 的写法。


def _plan_reminders(context: LayerContext, after: int | None) -> list[_Record]:
    """@brief 今日目标的提醒：提醒时刻晚于 after（上一次决策的时刻；None 时不设下界）、不晚于此刻的各一条。"""
    # 取代 _derived_records（:276-292），记录形状与排序键照 :289-291。
```

`_errand_record`（:237-243）、`_delivery_records`（:250-266）、`_call_record`（:169-172）、`_event_key`、`_order_ids`、`_tool_calls`、
`_last_request`、`_protected`、`_memo_*` 不变。

删除：`records_after`（:91-97）、`_session_records`（:100-107）、`_tool_records`（:115-124）、`_session_calls`（:127-140）、
`_carried_calls`（:143-155）、旧 `_carried_activities`（:158-166）、`_tool_record`（:175-180，由公开的 `tool_call_record` 取代）、
`_arguments`（:183-188）、`_derived_records`（:276-292）、`progress_view` 的 `facts` 参数与 `here`（:74-82），以及不再使用的导入
（`schedule`、`CONTROL_TOOLS`、`control_request`、`ActivityFacts`；`local_day_start` 的导入由第 03 章删除）。新增导入：
`from collections.abc import Mapping`；`slot_activities`（`worldmock.simulation_engine.activities.lifecycle.activity_control`）；
`day_bound`（`worldmock.simulation_engine.observation.memory.days`，与第 03 章为 `rejected_today` 加的同一行）；`AgentEvents`、
`agent_events`、`decision_commits`（`worldmock.simulation_engine.observation.memory.entries`）；`KitUse` 加进 settled_text 的那一行导入
（取代 `ActivityFacts`）。都不成环：activity_control 在基线上的传递导入不含 progress_layer；第 03 章规定 days.py、entries.py 及它们的
传递导入不导入 progress_layer.py。

### `src/worldmock/recording_engine/projections/activities/session_bounds.py`

函数不变。模块说明（:1-9）改为：

```python
"""@brief session 的事件序号下界（docs/design/06-agent.md「进度、待办与记忆」）。

一个 session 的记录区间是（前一边界提交的末事件序号, 本边界提交的末事件序号]，同一提交按 state_version_after 聚合：
出发请求所在的提交整体归旧 session，到达那一秒其后提交的收尾调用归新 session，路程中的内容消费与真实送达归路程
session。边界提交由参照点识别：运行开始的 session 下界为 0；到达（发生在时钟推进中，含到达被拒）取该 session 开始时刻的
首个事件所在的提交；由决策引起的切换（出发、途中结束出行）取引起切换的那条结果所在的提交。记忆层不按 session 分区，进度层的
下界也不取这里；只有两处读它：进度层判定「这段停留或路程里做完的查询」（终态结果序号大于本段下界），同秒续接判定上一决策之后
session 是否换过。
"""
```

### `src/worldmock/simulation_engine/observation/layers/memory_layer.py`

`MemoryView`（:41-47）的字段与 `memory_view` 的组装照第 02 章（`layer`、`facts`、`note_labels`、`published`；`published` 是
`published_today(...).published`，即 P_k）；本章只读其中的 `published` 与 `facts.kits`（observe.py:58），不增删字段。

### `src/worldmock/simulation_engine/observation/layers/observe.py`

:57-58 改为：

```python
        memory = memory_layer.memory_view(layer, self.context.store)
        view = progress_layer.progress_view(layer, self.context.store, memory.published, memory.facts.kits)
```

第二行逐字如此（源码变异以它为原文）。其余不变。

### `src/worldmock/simulation_engine/observation/layers/todo_layer.py`

模块说明（:1）改为 `"""@brief 待办层保存暂停的活动、未办完的个人事务、在途订单及当前完整目标清单。"""`。

```python
#: 待办层在途订单至多列出的行数；多出的合成一行「另有 N 笔在途」（docs/design/06-agent.md「进度、待办与记忆」）。
ORDER_ROW_LIMIT = 6


def todo_layer(context: LayerContext, store: FootprintStore) -> dict:
    """@brief 待办层 JSON：暂停的活动、还没办完的个人事务、在途订单与今日计划。"""
    # :18-25 不变，返回的字典在 "errands" 与 "day_plan" 之间加 "orders": _order_rows(context)。


def open_orders(state: WorldState, agent_id: int) -> tuple[DeliveryState, ...]:
    """@brief 本人的在途订单：已付款、还没收到（pending 或 ready_for_pickup）的商品履约记录，按预计到达与订单身份升序；
    待办层与零点计划共用。"""
    return tuple(sorted((order for order in state.deliveries.values()
                         if order.agent_id == agent_id and order.paid_at is not None
                         and order.status in OPEN_ORDER_STATUSES),
                        key=lambda order: (order.expected_at, order.order_id)))


def _order_rows(context: LayerContext) -> list[dict]:
    """@brief 待办层 orders：同商品、同购买时刻、同状态、同目的地、同预计到达的记录合成一行（份数为条数），至多
    ORDER_ROW_LIMIT 行，多出的写一行「另有 N 笔在途」。"""
    # 照【T2】：order_label、地点显示名、iso_time；合成行 {"order": f"另有 {hidden} 笔在途"}。
```

`open_orders` 里 `if order.agent_id == agent_id and order.paid_at is not None` 一行逐字如此（源码变异以它为原文）。
新增导入：`progress_layer` 的 `OPEN_ORDER_STATUSES`、`order_label`，`worldmock.state.world.orders.DeliveryState`（`iso_time` 已在 :11 导入）。

### `src/worldmock/simulation_engine/scheduling/plans/day_plan_inputs.py`

- `_pending_orders`（:259-270）改名 `_open_order_rows`，函数体改为对 `todo_layer.open_orders(context.state, agent_id)`
  逐条写现行四键行；文档字符串 `"""@brief 在途订单及预计送达：本人已付款、还没收到的订单（含已到驿站待领取的），与待办层
  orders 同一个选择。"""`。签名为 `_open_order_rows(context: Any, agent_id: int, window: DayWindow) -> list[dict[str, Any]]`
  （`_Window` 由第 03 章换成 `DayWindow`；第 03 章「day_plan_inputs.py」一节里的 `_pending_orders` 即这个改名后的函数）。
- :103 改为 `"在途订单": _open_order_rows(context, agent_id, window),`。
- 新增导入 `from worldmock.simulation_engine.observation.layers import todo_layer`（progress_layer 已是先例，:13，不成环）。

### `src/worldmock/simulation_engine/decisions/continuation.py`

模块说明（:1-11）改为：

```python
"""@brief 同秒续接的环境侧：从模型证据重建上一决策的末次行动请求与响应（docs/design/06-agent.md「提示与工具」）。

成链条件：同一人物在同一模拟秒内的相邻两次决策，同一 session；两者之间没有反事实干预（反事实分支一律断链，普通恢复
照常续接）；此前各 assistant 回合都带非空 reasoning_content；上一决策以一个世界请求或一个开始组结束。任一条件不满足、
模型证据缺失或运行开关 same_second_continuation 关闭时断链（null），下一次请求从新快照构造。

链对象在观察事件提交之后挂到观察返回体的私有键 continuation 上交给宿主：不另存、不导出，并列入禁止渲染的键表。
messages 是上一决策末次行动请求的全部消息（原样取自 model_request），assistant 是它的最终 assistant 消息，tool_results
按调用次序给出每个世界请求的调用身份与公开结果：直接按上一决策登记的请求和它最新的一条结果投影（与进度层 tool_call.result
同一投影，不经进度层），某条请求没有结果即断链。events 是序号大于上一次观察的非记忆世界记录（推送、回调提醒、余额减少、
事务提醒），形状与进度层同类记录相同。上一决策的事实（结束、付款、送达、办成）与它的自述都在它的今天条目里，宿主经
changes.memory.added 交给模型。
"""
```

- 导入（:26）改为

```python
from worldmock.simulation_engine.observation.layers.progress_layer import (
    notice_records, session_bound, tool_call_record)
```

  另加 `from worldmock.simulation_engine.observation.memory.entries import activity_facts, agent_events`（entries.py 及其传递导入
  不导入 continuation.py，不成环）。
- `same_second_chain`（:44-71）的 :62-71 改为：

```python
    messages, assistant, registered = evidence
    context = observations._layer_context(state.agents[agent_id], [])
    kits = activity_facts(agent_events(store, agent_id, context.catalog.kit_states())).kits
    results = []
    for call, requested in zip(assistant["tool_calls"], registered, strict=True):
        latest = _latest_result(store, agent_id, requested)
        if latest is None:
            return None
        record = tool_call_record(context, requested, latest, kits)
        results.append({"tool_call_id": call["id"], "result": record["result"]})
    return {"messages": messages, "assistant": assistant, "tool_results": results,
            "events": notice_records(context, store, previous.event_seq)}
```

  最后一行逐字如此（源码变异以它为原文）。文档字符串的第一段相应写「同秒非记忆世界记录」代替「同秒新事件」。
- 新增：

```python
def _latest_result(store: FootprintStore, agent_id: int, request: Event) -> Event | None:
    """@brief 一条已登记请求最新的结果：本人在它之后的 action_result 中动作身份相同的最后一条；没有时为 None（证据缺失）。"""
    own = [event for event in store.read_agent_events("action_result", agent_id, request.event_seq)
           if event.action_id == request.action_id]
    return own[-1] if own else None
```

- `_previous_observation`、`_session_changed`、`_counterfactual_since`、`_decision_evidence` 与消息重建函数不变。

### `src/worldmock/agents/protocol/life_continuation.py`

模块说明（:1-12）改为：

```python
"""@brief 同秒续接的宿主侧：把环境交来的续接链组装成本次决策的起始消息（docs/design/06-agent.md「提示与工具」）。

消息 = 上一决策末次请求的全部消息（system、链首快照 user、此前各原生回合）＋ 上一决策的最终 assistant（reasoning_content、
content、tool_calls）＋ 对应其每个世界请求 tool_call_id 的各一条 tool 消息（开始组两条，changes 与 events 只写在最后一条上）。
tool 消息是紧凑 JSON {result, changes, events}，空键省略：changes 相对于模型当前所见（链首快照依次叠加此前各 tool 消息的
changes），键依次为工具层目标的 updated 与 removed、记忆层「今天」末尾新增的条目（memory.added）、待办层暂停活动与个人事务
（按名称）和在途订单（按订单标签）的 added 与 removed、今日计划与身体感受的整体替换，以最新为准。所在地点在链内不变；记忆层
「今天」只在末尾追加上一决策的条目，经 changes.memory.added 送达，其余部分只在零点变化；人设层在链内不变，不进 changes。
所见的「今天」不是此刻「今天」的前缀（兜底改写了早先的条目），或在途订单有重复标签时，不续接，本次决策从新快照构造。
原生 tools 不因续接改变。

发送前协议自检：续接链中每个 assistant 都须带非空 reasoning_content，缺失就断链（本决策此后都从新快照续写）；本次决策
内的 assistant 缺失时以英文错误失败。预算第 0 步断链后同样从新快照续写（prompt_capacity.fit_request）。
"""
```

函数：

```python
def layer_changes(head_layers: Sequence[dict], applied: Sequence[dict], current: Sequence[dict]) -> dict | None:
    """@brief 当前六层相对于模型所见（链首六层依次叠加 applied 中此前各 changes）的改动，键依次为 targets、memory、
    paused_activities、errands、orders、day_plan、body，没有改动的键不写；所见的「今天」不是此刻「今天」的前缀、或在途订单
    有重复标签时为 None（不能续接）。"""
    seen = _view(head_layers)
    for changes in applied:
        seen = _overlay(seen, changes)
    now = _view(current)
    if seen["memory"] != now["memory"][:len(seen["memory"])]:
        logging.warning("Memory layer today list is not an extension of the list the model has seen; "
                        "using a fresh snapshot")
        return None
    if not (_orders_unique(head_layers) and _orders_unique(current)):
        logging.warning("To-do layer orders repeat a label; using a fresh snapshot")
        return None
    ...  # 按【C3】的次序组装：targets、memory（{"added": now["memory"][len(seen["memory"]):]}，为空不写）、
         # paused_activities、errands、orders（_keyed_changes(..., ("added", "removed"))）、day_plan、body
```

`if seen["memory"] != now["memory"][:len(seen["memory"])]:` 一行逐字如此（源码变异以它为原文）。

```python
def continuation_messages(chain: Mapping[str, Any] | None, layers: Sequence[dict]) -> list[dict] | None:
    """@brief 由续接链与本次观察的六层组装本次决策的起始消息；没有链、或改动不能表达（layer_changes 为 None）时为 None
    （从新快照构造）。"""
    # :68-77 不变，只在 changes = layer_changes(...) 之后加：if changes is None: return None


def _view(layers: Sequence[dict]) -> dict:
    """@brief 六层中续接会改动的部分：工具层目标、记忆层「今天」（经渲染往返规范化）、待办层暂停活动与个人事务（按名称）、
    在途订单（按订单标签）、今日计划与身体感受。"""
    by_name = {layer["layer"]: layer for layer in layers}
    todo = by_name["待办层"]
    tools = expand_tool_layer(compact_render(by_name["工具层"]))
    return {"targets": {item["name"]: item for item in tools["targets"]},
            "memory": json.loads(render_layer_json(by_name["记忆层"]["今天"])),
            "paused_activities": {item["name"]: item for item in todo["paused_activities"]},
            "errands": {item["name"]: item for item in todo["errands"]},
            "orders": {item["order"]: item for item in todo["orders"]},
            "day_plan": todo["day_plan"], "body": by_name["环境层"]["body"]}


def _overlay(seen: dict, changes: Mapping[str, Any]) -> dict:
    """@brief 把一条 changes 叠加到所见视图：「今天」接上 added；按名称或标签的四类先去掉 removed、再写入 added/updated；
    整体替换项直接替换。"""
    view = {**seen, "memory": list(seen["memory"])}
    for key in ("targets", "paused_activities", "errands", "orders"):
        view[key] = dict(seen[key])
    for key, written, field in (("targets", "updated", "name"), ("paused_activities", "added", "name"),
                                ("errands", "added", "name"), ("orders", "added", "order")):
        part = changes.get(key, {})
        for label in part.get("removed", []):
            view[key].pop(label, None)
        view[key].update({item[field]: item for item in part.get(written, [])})
    view["memory"].extend(changes.get("memory", {}).get("added", []))
    for key in ("day_plan", "body"):
        if key in changes:
            view[key] = changes[key]
    return view


def _orders_unique(layers: Sequence[dict]) -> bool:
    """@brief 待办层在途订单的标签各不相同（按标签比较的前提）。"""
    labels = [row["order"] for row in {layer["layer"]: layer for layer in layers}["待办层"]["orders"]]
    return len(labels) == len(set(labels))
```

`reasoning_complete`、`tool_message`、`send_check`、`end_continuation`、`_earlier_changes`、`_keyed_changes` 不变。
删除 `_view` 里读人设层的两项与 `_overlay` 里 relationships、preferences 的两段。

### `src/worldmock/agents/prompt/prompt_capacity.py`

- `_condensation_steps`（:121-135）删去第一段循环（:123-126），文档字符串改为
  `"""@brief 逐步产出浓缩后的（记录, 元数据）：逐段把最旧一段连续的未受保护记录折为 condensed 记录。"""`。
- 模块说明（:1-12）里的步骤列表改为「0. 提前结束本秒续接……；1. 把最旧一段连续的未受保护记录（紧邻其前的 condensed 记录并入）
  折成一条 condensed 记录，重复到阈值以下或无可折叠；」，删去原第 1 条「从旧到新逐条去掉未受保护记录的 self_note」，原第 2 条
  改称第 1 条。第 01 章对同一说明第 7-8 行的改写照旧。

### `src/worldmock/agents/prompt/prompt_schemas.py`

- `TODO_LAYER_SCHEMA`（:171-183）在 `errands` 与 `day_plan` 之间加：

```python
        "orders": {"type": "array", "maxItems": ORDER_ROW_LIMIT + 1, "items": {"oneOf": [_ORDER, _MORE_ORDERS]}},
```

  其上新增：

```python
#: 待办层的一行在途订单（docs/design/09-io-formats.md「提示层的机器形状」）：订单标签、状态、送到的地点与预计到达。
_ORDER = _object({"order": _TEXT, "status": {"enum": ["pending", "ready_for_pickup"]}, "destination": _TEXT,
                  "expected_at": _TIME})
#: 在途订单多于 ORDER_ROW_LIMIT 行时末尾的合成行。
_MORE_ORDERS = _object({"order": {"type": "string", "pattern": r"^另有 [1-9][0-9]* 笔在途$"}})
```

  `ORDER_ROW_LIMIT` 从 todo_layer 导入。
- `PROGRESS_LAYER_SCHEMA` 的 `tool_call` 记录（:269-271）改为
  `_record("tool_call", {"tool": {"enum": list(START_TOOLS)}, "arguments": {"type": "object"}, "result": _RESULT})`：
  没有 `self_note`，工具只能是两个开始工具；`START_TOOLS` 从 `worldmock.state.interaction.actions` 导入。

### `src/worldmock/state/interaction/control_items.py`

删除 `control_request`（:84-95）与只供它用的 `_MEMORY_KEYS`（:23）：它唯一的调用方是 progress_layer.py:188 的 `_arguments`，
控制请求不再进进度层。`control_items` 的文档字符串（:50-51）读取方列表删去「进度层」（进度层不再导入本模块），其余不变。

### `src/worldmock/simulation_engine/observation/wording/settled_text.py`

`record_lines`（:44-60）删去 `facts` 参数，签名为 `record_lines(context: LayerContext, record: dict, sources: list[Event]) -> list[str]`；
`_tool_lines`（:200-216）删去 `facts` 参数与 conclude_activity 分支（:209-213）。唯一传 `facts` 的调用方是进度层的 `here`
（progress_layer.py:84），收尾请求的记录不再进进度层；零点计划照旧不传它，收尾记录得到 `[]`（收尾结果不含下单）。
settled_text 的其余改动归第 02、08 章。

### 不改的文件

- custom/agents/life_agent.py:488-492：`continued` 为 None 时用新快照，已有。
- src/worldmock/simulation_engine/engine.py:88：`same_second_chain` 的调用不变。
- src/worldmock/recording_engine/storage/observation_parts.py：待办层仍按现行分件规则存储，键名不变。

## 设计文档

文档、实现和行为测试同一次提交（docs/INDEX.md「当前系统设计」）。改完运行 `python scripts/build_design_doc.py`。

### `docs/design/06-agent.md`「提示与工具」

:153-155 那一段（从「同秒续接在上一决策以一个世界请求或一个开始组结束时成链。」到「否则断链。」）换成：

> 同秒续接在上一决策以一个世界请求或一个开始组结束时成链。链对象的 `tool_results` 按调用次序保存一条或两条
> `{tool_call_id, result}`：`result` 是上一决策登记的那条请求最新的一条结果，与进度层带入记录的 `result` 同一个公开投影，不经进度层；
> 某条请求没有结果就断链。宿主为每个 `tool_call_id` 各追加一条 tool 消息，`changes` 与 `events` 只写在最后一条上。
> 重建的 assistant 消息带两个 `tool_calls` 时必须是两个开始工具各一个，并与登记的两条动作按次序同名，否则断链。
>
> `changes` 相对于模型所见（链首快照依次叠加此前各 tool 消息的 `changes`）计算，键依次是 `targets`（工具层目标按名称的 `updated`
> 与 `removed`）、`memory`（`{"added": […]}`，记忆层「今天」末尾新增的条目，与「今天」里渲染的条目逐字节相同）、
> `paused_activities` 与 `errands`（按名称的 `added` 与 `removed`）、`orders`（在途订单按订单标签的 `added` 与 `removed`）、
> `day_plan` 与 `body`（整体替换）；没有改动的键不写。人设层与所在地点在链内不变，不进 `changes`。上一决策的条目一提交就发布，
> 链里的下一次决策经 `memory.added` 看到它。模型所见的「今天」不是此刻「今天」的前缀时（兜底改写了早先的条目，见「进度、待办与记忆」），
> 或在途订单里有两行标签相同时，这次决策不续接，从新快照构造。`events` 是序号大于上一次观察的非记忆世界记录：app 推送、回调提醒、
> 余额减少与个人事务提醒，形状与进度层同类记录相同；送达、到站与办成是上一决策条目里的事实，不另列。

:184 不改（「环境层只有体感进 changes」仍成立；完整键集与断链条件只写在上面的新段落，见「规格阶段的决定」）。

### `docs/design/06-agent.md`「零点计划」

:204 一行换成：

> | 在途订单 | 本人的在途订单（已付款、还没收到，含已到驿站等本人领取的，见[模拟引擎](04-simulation-engine.md)「购买、付款与收货」），与待办层 `orders` 同一个函数选出：每条记录写商品、数量、预计送达与送到哪里，按预计送达的先后 |

### `docs/design/06-agent.md`「进度、待办与记忆」

1. :218-220 换成：

> 进度层写此刻还没了结的事：占槽位的本人活动（进行中或待收尾）、上次决策之后新到的结果与消息、这段停留或路程里做完的查询和答案，
> 以及今天被拒绝的请求。内容短语统一使用实际已消费与总时长，跨容器恢复读取共享进度。
> 待办层写跨决策保留的事：暂停的活动、人物记下的个人事务、在途订单和今日计划；商品事务只按真实收到的数量累计。已经了结的事实、
> 自己写下的总结与回复正文都在记忆层「今天」里。

2. :265 一句的改写归第 02 章「设计文档」（同一节「另改三处」的第一处），本章不另写。:239 与 :263 两句改为：
   - :239「人物读到的查询答案是第二条结果的 `details.query`，有两种形状。」改为「查询的第二条结果 `details.query` 有两种形状；
     进度层里带第二条结果的查询只有查完的（进行中的查询带的是第一条结果，不写 `query`），人物读到的是查完的形状。」，
     其后「查完的形状是 `app`、`scope`、`rows` 和可选的 `omitted`：」不变；
   - :263「没有查完的形状（提前结束或被截断）恰两个键：`app`，和 `state`「没有查完」。」改为「没有查完的形状（提前结束或被截断）
     恰两个键：`app`，和 `state`「没有查完」，只在事件与导出里出现。」，其后「进行中的第一条结果不写 `query`。」不变。

3. :267-271（从「进度层按段记」到「到达后的停留里不带入。」）整段删去，换成新小节 `### 进度层的下界与带入的记录`，写明：
   - 已发布界 P_k = max(C_{k−1}, B_D)：C_{k−1} 是上一次决策提交的末事件序号（「今天条目」小节），B_D 是今天的零点界；序号不超过 P_k 的
     事件，要么已写进「今天」的条目，要么属于前一天；
   - 上一次决策是「今天条目」小节所定义的最后一次决策（本人世界请求按决策分组），它的观察与时刻怎样取；
   - 【P3】的四行表（记录、`type`、出现条件），以及「上一次决策提交里产生的提醒不会丢」「同一秒链里的下一次决策不再看到已看到的计划提醒」；
   - 带入的记录：占槽位的本人活动不分种类（睡眠、出行、购买、查询、interact 都算），这段停留或路程里做完的查询；记录是开始请求原文
     与它最新的一条结果，请求与第一条结果按事件身份读出，终态结果从活动结束那一秒读起；暂停着的活动不带入、只在待办层，恢复后重新带入；
   - 一段停留或一段路各有一个事件序号下界（出发那次提交整体归出发前的停留，到达那次推进整体归路上那一段），只用来判定这段停留或
     路程里做完的查询，以及同秒续接判定 session 换没换；
   - 控制请求、被拒的请求与已收尾的活动不在进度层，记录不带 `self_note`；被拒原因在 `rejected_today`；
   - 排列：按发生时刻（开始请求的记录取请求时刻），同一时刻推导记录在前，再按最小来源序号；`session_started_at` 是这段停留或路程的开始时刻；
   - 两层不相交与两处重叠（【P8】的原文），`rejected_today` 不在此列。
4. 在上述小节之后新增小节 `### 待办层`，写明：键依次为 `paused_activities`、`errands`、`orders`、`day_plan`；`orders` 列本人的在途订单
   （定义见[模拟引擎](04-simulation-engine.md)「购买、付款与收货」）；合成规则（同商品、同购买时刻、同状态、同目的地、同预计到达的记录
   合成一行，份数为条数）；排列（预计到达、订单身份）；至多 6 行、多出的写一行合成行；付款完成时进入、送达或领取时离开、到站时状态变化；
   行的机器形状见[状态与序列化](09-io-formats.md)「提示层的机器形状」。
5. 本节其余段落（结果明细、查询答案的形状表与各行键、记忆层的写法；:239、:263 两句除外，见第 2 点）的改写归第 01、02、03、05、08 章。

### `docs/design/04-simulation-engine.md`「购买、付款与收货」

在 :666「付款后到店订单当秒收货，外卖在到期时真实送达，快递先到驿站，再由本人到站后在本站开始一件事时领取。」之后加一句：

> 已付款、状态为 `pending`（外卖或快递在路上）或 `ready_for_pickup`（快递已到驿站、等本人领取）的商品履约记录是本人的在途订单；
> 未付款的 `pending` 记录是进行中购买的报价，不算在途。待办层的 `orders` 与零点计划的「在途订单」都用同一个函数按这一条选出，
> 见[人物](06-agent.md)「进度、待办与记忆」与「零点计划」。

### `docs/design/05-recording-engine.md`「权威事实与投影」

在 :5-6（从「运行的权威记录是按提交顺序保存的事件和共同世界状态。」到「不能根据模型自述、资金变化或活动名称补造内容、收货和世界事件。」）
之后加一句：

> 进度层与记忆层「今天」不按 session 的事件序号区间投影，两层按来源事件序号不相交，只有两处写明的重叠；session 的事件序号下界
> （recording_engine/projections/activities/session_bounds.py）只用来判定这段停留或路程里做完的查询与同秒续接里 session 换没换；
> 已发布界、重叠与带入规则只在[人物](06-agent.md)「进度层的下界与带入的记录」定义。

### `docs/design/09-io-formats.md`「提示层的机器形状」

这一节由第 01 章新建（「输入与模型身份」与「事件与检查点」之间）；本章在其中写 `### 待办层` 小节，写明：
- 顶层键依次为 `layer`（常量「待办层」）、`paused_activities`、`errands`、`orders`、`day_plan`；`paused_activities`、`errands`、`day_plan`
  的行形状不变（见人物「进度、待办与记忆」与模拟引擎「唤醒、日计划与个人事务」）；
- `orders` 至多 7 项；订单行恰为 `order`（订单标签「〈商品名〉 〈份数〉 份（〈购买时刻〉 购买）」，与进度层送达记录的 `order` 同一写法）、
  `status`（`pending` 或 `ready_for_pickup`）、`destination`（地点显示名）、`expected_at`（带时区 ISO 8601）四个键，次序如此；
- 合成行恰为 `{"order": "另有 N 笔在途"}`，N 是正整数，只出现在 6 行订单行之后；
- Schema 是 `TODO_LAYER_SCHEMA`。

### `docs/manual/`

用户操作不变，不改。

### 起规范作用的模块说明

同期改写：progress_layer.py:1-5、session_bounds.py:1-9、continuation.py:1-11、life_continuation.py:1-12、todo_layer.py:1，
prompt_capacity.py 的步骤列表；原文见「接口与文件」。

## 测试

所有新测试都有中文文档字符串；断言生产英文文字时逐字照抄；失败分支同时用 `caplog` 断言英文日志。真实引擎的测试用现有夹具
`engine_factory`、`tests.life.conftest`（`stage_day_plan`、`advance_next`、`advance_until_ready`、`progress_calls`、`conclude_pending`、`purchase`、
`sleep`）、`tests.life.environment_runtime`（`act`、`act_group`、`observe_only`、`until_ended`、`finish_activity`、`reach`、`every_five_minutes`）。读 P_k 的测试用
`memory_layer.memory_view(layer_context, store).published`。

### 新文件 `tests/integration/life/test_progress_bounds.py`

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_every_slot_activity_is_carried_whatever_its_kind`，参数化 sleep、travel、purchase、location-interact、app-interact | 开始之后的观察里恰有一条这项活动的 `tool_call`，结果是第一条；活动结束、待收尾时同一条记录（`occurred_at`、`arguments` 不变）的结果是终态结果；收尾之后的观察里没有它；每次观察的进度层通过 `validate_layer` | P4、P6、P7 |
| `test_a_query_is_carried_while_running_and_its_answer_until_departure` | 查询进行中：一条 `tool_call`，结果是第一条；做完以后再做两次别的决策：同一条记录的结果是带 `details.query` 答案的终态结果；出发之后的观察里没有它 | P4、P5 |
| `test_overnight_sleep_is_carried_and_facts_before_midnight_stay_out_of_the_morning_progress_layer` | 运行从 23:00 开始（`mutate` 把 `start_time` 的钟点改为 23:00，`stop_time` 为其后 10 小时）；23:00 `act(engine, "takeaway", purchase("牛肉面"))` 经外卖 app 下单（夹具里预计 23:18:25 送达），`finish_activity` 在 23:02:35 付款并收尾购买；同一秒 `act(engine, "sleep", sleep(480))` 开始睡 8 小时（`until_ended` 推进到 07:02:35 醒）；外卖在睡眠中 23:18:25 送达，早于零点。醒来那一秒的观察：带入的睡眠记录是终态结果（`completed`）；进度层没有那次送达的 `delivery`，待办层 `orders` 为空；全部非 `tool_call` 记录的来源序号都大于 B_D | P1、P3、P4 |
| `test_a_delivery_is_in_the_progress_layer_until_the_next_decision_publishes_it` | 08:00 `act(engine, "takeaway", purchase("牛肉面"))`（夹具里预计 08:18:25 送达），`finish_activity` 在 08:02:35 收尾购买；同一秒 `act(engine, "nap", sleep(30))` 开始睡 30 分钟；`advance_next` 推进到外卖在时钟推进里送达（睡着时送达不给决策机会）：送达之后、下一次决策之前的 `observe_only` 观察有那条 `delivery`，且来源序号 > P_k；推进到 08:32:35 醒来，给到期待收尾的睡眠做一次 `conclude_activity`，之后的观察没有它，此时 P_k ≥ 送达事件的序号 | P1、P3、P9 |
| `test_a_balance_notice_from_a_decision_commit_reaches_the_next_decision_only` | 环境副本的 callbacks.jsonl 加一条 `{"class":"action","key":"travel","travel_method_id":"driving"}` 触发、`{"type":"transfer","direction":"expense","amount_fen":100}`、`notify` 为 `{"visible": true}` 的规则（写法同 tests/integration/life/test_callback_runtime.py 的 `callback_environment`），引擎用 `mutate=every_five_minutes`（路上每五分钟一次决策机会）；开车出发那次决策的提交里有 `balance_decreased`（序号介于那次观察与 C_{k−1} 之间）；其后的观察有一条 `balance_decreased`，`message` 是「余额减少了 〈这次提交前后余额之差的 money_phrase〉」（含车费，callbacks.py:107-111）；路上再做一次 report_status 之后的观察没有它 | P2、P3 |
| `test_plan_reminder_shows_from_its_moment_until_the_next_decision` | 今日计划一条目标带提醒；推进到提醒时刻：观察里有那条 `plan_reminder`；同一秒做一次决策之后再观察（仍是同一秒）：没有它；时钟推进之后也没有 | P2、P3 |
| `test_control_and_rejected_requests_never_enter_the_progress_layer` | 依次做 revise_day_plan、一次被拒的开始、开始并 report_status、pause_activity、恢复、conclude_activity；每次之后的观察里 `tool_call` 的 `tool` 都是开始工具，且只对应占槽位或这段做完的查询；被拒那次的原因出现在 `rejected_today`；没有任何记录带 `self_note` | P6 |
| `test_progress_records_lie_after_their_bounds_except_the_carried_ones` | 一段含开车出行、购买、外卖送达、查询、内容 app 与收尾的脚本化上午；环境副本加 `test_a_balance_notice_from_a_decision_commit_reaches_the_next_decision_only` 的那条开车出发时可见的支出回调（`callback_environment` 写法），所以开车出发那次决策的提交里有一条 `balance_decreased`。每次观察：每条非 `tool_call` 记录的来源序号大于它那一种的下界；序号不超过 P_k 的来源只属于带入记录，或属于序号大于上一次决策观察序号的 `app_push`、`callback_reminder`、`balance_decreased`、`errand_reminder` 记录（开车出发之后的那次观察里这一分支确实出现）；每条 `tool_call` 都对应 `slot_activities(open_activities)` 里的一项活动或【P5】的一项查询；另取同一时刻 `published_today(engine.context.inputs, store, 1, now, store.last_event_seq)` 各条目 `event_ids` 的并集 E，E 与这次观察 `progress_event_ids` 的交集只能是【P4】【P5】带入记录的来源（请求、第一条结果、终态结果），把带入记录的来源去掉以后交集为空 | P3、P8 |
| `test_memory_view_returns_the_published_bound` | 运行开始的第一次观察 `published == max(0, B_D)`；一次决策之后 `== store.commit_last_seq(那次决策第一条 action_result 的序号)`；睡过零点后醒来的第一次观察 `== B_D` | P1 |

### 新文件 `tests/integration/life/test_todo_orders.py`

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_a_paid_delivery_is_an_order_in_transit_until_it_arrives` | 经 app 买一件外卖：购买进行中（未付款）时 `orders == []`；付款完成后恰一行，`list(row) == ["order", "status", "destination", "expected_at"]`，`order` 等于 `order_label` 的写法、`status == "pending"`、`expected_at` 等于记录的 `expected_at` 的 `iso_time`；送达后 `orders == []` | T1、T2 |
| `test_a_parcel_waiting_at_the_station_is_ready_for_pickup_until_collected` | 买一件快递：在途时 `status == "pending"`、`destination` 是驿站；到站后同一 `order` 的行 `status == "ready_for_pickup"`；在驿站领取之后 `orders == []` | T1、T2 |
| `test_two_portions_of_one_goods_bought_together_are_one_row` | 一次购买写同一商品两次：`orders` 只有一行，`order` 为「〈商品名〉 2 份（HH:MM 购买）」 | T2 |
| `test_orders_list_six_rows_and_one_summary_row` | 世界状态里放入 8 组、6 组、7 组互不合并的已付款在途记录：8 组时 7 项，末项恰为 `{"order": "另有 2 笔在途"}`；6 组时 6 项、没有合成行；7 组时末项为 `{"order": "另有 1 笔在途"}`；行按 `(expected_at, order_id)` 排列 | T2 |
| `test_the_day_plan_lists_the_same_open_orders_as_the_todo_layer` | 一笔未付款的报价、一笔在途外卖、一件已到站的快递：`plan_inputs(...).payload["在途订单"]` 恰两行、四键与写法照旧，与 `open_orders` 一一对应（含 `ready_for_pickup` 那件，不含报价） | T1、T3 |
| `test_todo_layer_with_orders_passes_layer_validation` | 上面几种情形的待办层都通过 `validate_layer`；`orders` 放 8 项、合成行不合 `另有 N 笔在途` 的写法、订单行多一个键或少一个键时 `validate_layer` 抛 `ValueError` | T2 |

### `tests/unit/life/test_memory_progress_contract.py`（追加）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_world_records_take_facts_after_the_published_bound_and_notices_after_the_notice_bound` | 一条 `errand_completed`（序号 6）、一条 `errand_reminded`（4）、一条 `balance_decreased`（7）：界 `(5, 3)` 时三条都在；界 `(6, 4)` 时只剩余额那条；界 `(0, 7)` 时只剩办成那条 | P3 |
| `test_a_previous_decision_without_its_observation_is_an_integrity_fault` | `monkeypatch.setattr(progress_layer, "decision_commits", …)` 返回 `(Decision("decision-x", 100, 7),)`，假存储的 `read_decision_observations` 返回空：`progress_layer._previous_decision(store, AgentEvents(), 1)` 抛 `IntegrityError("Previous decision has no observation")`，日志含 `Previous decision decision-x has no observation`；`decision_commits` 返回 `()` 时返回 None；读到两条观察（序号 3、5）时返回 `_PreviousDecision(5, 100)` | P2 |
| `test_an_ended_activity_without_its_terminal_result_is_an_integrity_fault` | 假存储在结束那一秒之后没有这条动作的 `action_result`：`progress_layer._terminal_result` 抛 `IntegrityError("Ended activity has no terminal result")`，日志含 `Ended activity <activity_id> has no terminal result` | P4 |
| `test_the_session_record_path_is_deleted` | `progress_layer` 没有 `records_after`、`_session_records`、`_tool_records`、`_session_calls`、`_carried_calls`、`_arguments`、`_derived_records`；`control_items` 没有 `control_request`、`_MEMORY_KEYS`；`inspect.signature(record_lines)` 恰为 `context, record, sources`；`inspect.signature(progress_view)` 恰为 `context, store, published, kits`，`inspect.signature(tool_call_record)` 恰为 `context, request, result, kits`；`inspect.getsource(prompt_capacity._condensation_steps)` 不含 `self_note`；对一条带 `self_note` 的未受保护记录（元数据 `{"protected": False, "lines": ["…"]}`）调用 `_condensation_steps`，第一次产出的记录列表恰为一条 `condensed` 记录 | P9、C8 |
| `test_progress_tool_call_schema_takes_only_start_requests_without_self_note` | 一条合法的带入记录通过 `validate_layer`；加 `self_note`、`tool` 改成 `conclude_activity` 时各抛 `ValueError` | P6 |

### `tests/integration/runtime/test_same_second_continuation.py`（追加）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_a_chain_delivers_the_previous_decisions_entry_as_memory_added` | `_plan_then_wait`：第二次请求最后一条 tool 消息的 `changes` 键恰为 `["memory", "day_plan"]`；`changes["memory"] == {"added": [entry]}`，`render_layer_json(entry)` 与本次观察记忆层「今天」最后一项的渲染逐字节相同；链首 user 消息逐字节不变 | C3、C4 |
| `test_changes_add_each_published_entry_once_across_a_three_decision_chain` | 同一秒三次决策（收尾、改计划、开始）：第二次的 tool 消息 `memory.added` 恰是第一次决策的条目；第三次的恰是第二次的条目，第一次的不再出现；第三次请求的前缀逐字节等于第二次请求 | C3、C4 |
| `test_layer_changes_add_memory_after_the_seen_prefix_and_keep_the_key_order` | 单元：链首「今天」两条、applied 里一条 `memory.added`、此刻四条：`changes["memory"] == {"added": [第四条]}`；所有七个键都有改动时 `list(changes) == ["targets", "memory", "paused_activities", "errands", "orders", "day_plan", "body"]`；此刻与所见相同时 `layer_changes(...) == {}` | C3、C4 |
| `test_a_rewritten_today_list_ends_the_continuation` | 单元：此刻「今天」的第一项被改成 `自述: ["…"]`：`layer_changes` 返回 None，日志含 `Memory layer today list is not an extension of the list the model has seen; using a fresh snapshot`；`continuation_messages(chain, layers)` 返回 None | C4、C6 |
| `test_order_rows_enter_changes_by_label_and_a_repeated_label_ends_the_continuation` | 单元：所见两行、此刻一行状态变成 `ready_for_pickup`、一行消失、一行新增：`changes["orders"] == {"added": [变状态的行, 新行], "removed": [消失的标签]}`；合成行从「另有 1 笔在途」变「另有 2 笔在途」时出现在 added 与 removed 两边；此刻两行同标签时返回 None，日志含 `To-do layer orders repeat a label; using a fresh snapshot`；链首两行、`applied` 里一条 `{"orders": {"added": [新行], "removed": [其中一行的标签]}}`、此刻恰为叠加后的两行：`layer_changes` 的结果没有 `orders` 键（`_overlay` 按 `order` 标签叠加，不再报出已送达的行） | C5、C6 |
| `test_chain_events_are_the_notices_after_the_previous_observation` | 环境副本加一条规则 `rule("feedback-fee", {"class": "event", "key": "outcome_feedback"}, {"type": "transfer", "direction": "expense", "amount_fen": 100}, notify={"visible": True})`（写法同 test_callback_runtime.py 的 `callback_environment` 与 `rule`）；`_recorded_decision` 的被拒决策提交里于是有 `balance_decreased`；`_next_chain(engine)["events"] == [{"occurred_at": …, "type": "balance_decreased", "message": "余额减少了 1 元"}]`，同一次观察的进度层也有这条记录；没有这条规则时 `events == []` | C2、P3 |
| `test_chain_results_project_the_previous_requests_without_the_progress_layer` | 被拒决策之后的链：`tool_results[0]["result"] == public_result(那条 action_result.data, inputs, kits)`，`kits` 是 `activity_facts(agent_events(store, 1, engine.context.catalog.kit_states())).kits`；同一次观察的进度层没有任何 `tool_call` | C1、P6 |
| `test_chain_results_are_projected_with_the_grocery_kits_of_the_observation` | 用 test_grocery_cooking.py 的 `grocery_engine`（`initial=1`）与 `cook` 在家开始做饭，`advance_until_ready` 到做完待收尾；再 `_recorded_decision(engine, _BASE, _answer([_CALL]))`（被拒）；用 `monkeypatch` 包住 `continuation.tool_call_record`、记下每次调用的第四个参数：`_next_chain(engine)` 不为 None，记下的参数等于 `activity_facts(agent_events(store, 1, engine.context.catalog.kit_states())).kits`，且含做饭那项活动的身份 | C1 |
| `test_chain_events_leave_out_memory_facts_from_the_previous_commit` | 运行六个本地日（`mutate` 同 test_grocery_cooking.py 的 `_six_days`），开始时 `stage_day_plan(engine)`；`act` 买平台笔记本并 `finish_activity`，推进到它到快递驿站（`ready_for_pickup`，照 test_grocery_cooking.py:386-387 的 `reach` 与 `_until_free`）；`act` 驾车去快递驿站，`until_ended` 到站，以 `conclude_request(出行活动名, errands=[errand_add("取笔记本", "平台笔记本", 1)])` 收尾出行；在驿站 `act(engine, "keep", use_app("Keep"))`，`until_ended` 到 Keep 放完（1200 秒）、待收尾；然后经脚本化模型 `_single(environment, "collect", [_reply("call-collect", MAIN_TOOL, main_arguments("interact", "快递驿站", "驿站取件"), "去取快递。")])` 开始驿站取件：同一提交里有 `goods_received` 与 `errand_completed`；槽位 2 的 Keep 仍待收尾，所以受理之后人物同一秒再决策（same_moment.py:133）；同一秒 `observation, [request], _ = await _single(environment, "next", _plan_reply())`：`observation["continuation"]` 不为 None，它的 `events` 不含 `errand_completed` 与 `delivery` 记录，同一次观察的进度层也没有 `errand_completed`；`_tool_payload(request)["changes"]["memory"]["added"]` 恰一项，其 `核验` 有一行恰为「办成「取笔记本」」 | C2、P3 |

### 改动的现有测试

| 文件与测试 | 改动 | 条款 |
| --- | --- | --- |
| test_same_second_continuation.py 导入（:17、:80-81） | 删去 :80 的 `records_after`、:81 的 `LayerContext` 与 :17 的 `open_activities`（三者只为 :613-619 用）；:37 不改；另加 `activity_facts`、`agent_events`（entries.py）与 `public_result` 的导入 | C1 |
| `test_the_continued_request_starts_with_the_previous_request_byte_for_byte`（:223） | 删去从进度层取 revise_day_plan 记录的 `[record] = …`；`payload["result"]` 改与 `observation["continuation"]["tool_results"][0]["result"]` 比较且 `status == "completed"`；`payload["changes"] == {"memory": {"added": [observation["memory_layer"]["今天"][-1]]}, "day_plan": observation["todo_layer"]["day_plan"]}`；断言进度层没有 revise_day_plan 记录 | C1、C3、C4、P6 |
| `test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once`（:255） | 第一条 tool 消息的 `changes` 含 `memory` 与 `day_plan`；最后一条 tool 消息的键改为 `["result", "changes"]`，`changes == {"memory": {"added": [第三次观察「今天」的最后一项]}}` | C3、C4 |
| `test_duty_is_constant_within_a_same_second_chain_and_never_enters_changes`（:282） | `list(...["changes"]) == ["memory", "day_plan"]` | C3 |
| `test_after_time_advances_the_request_has_no_native_turns_or_earlier_thinking`（:323） | 进度层的 `tool_call` 只剩等待那项活动的带入记录（`MAIN_TOOL`），没有 revise_day_plan，全层不含 `self_note` | P4、P6 |
| `test_a_missing_result_of_the_previous_request_breaks_the_chain`（:556） | `monkeypatch.setattr(continuation, "_latest_result", lambda store, agent_id, request: None)` 取代对 `records_after` 的替换 | C1 |
| `test_a_rejected_request_continues_with_the_rejection_reason`（:568） | 删去 `[record] = …`；`result` 与那条 `action_result` 的 `public_result` 投影相等；进度层没有 `tool_call`，`rejected_today` 有这次拒绝 | C1、P6 |
| `test_a_carried_record_is_not_repeated_in_the_continuation`（:591） | 删去 `records_after` 那几行（:613-619）；Keep 的带入记录在进度层恰一条；`chain["events"] == []` | C1、C2、P4 |
| `test_a_start_group_is_continued_with_one_tool_message_per_call`（:628） | `records = progress_calls(observation)` 改为断言为空；两条 tool 消息的 `result` 与 `continuation.tool_results` 相等；`payloads[1]["changes"] == {"memory": {"added": [观察「今天」最后一项]}, "body": observation["environment_layer"]["body"]}` | C1、C3 |
| `test_a_decision_with_memory_and_correction_turns_is_continued_byte_for_byte`（:747） | 末尾 tool 消息的 `changes` 含 `memory`（检索工具的改写归第 05 章） | C3 |
| `test_changes_overlay_relationships_and_preferences_so_later_tool_messages_do_not_repeat_them`（:795） | 删除，由 `test_changes_add_each_published_entry_once_across_a_three_decision_chain` 取代 | C3、C7 |
| `test_errand_changes_reach_the_next_tool_message_as_added_and_removed`（:837） | `_tool_payload(third)["changes"] == {"memory": {"added": [current 的「今天」最后一项]}, "day_plan": current["todo_layer"]["day_plan"]}` | C3 |
| `_chained_request` 与 `test_budget_step_zero_ends_the_continuation_before_any_condensation`（:896-935） | 夹具记录不带 `self_note`，`arguments` 带一段长文字；第二段的「去掉 self_note」改为断言浓缩后进度层是一条 `condensed` 记录 | C8 |
| `test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn`（:938） | 另断言两次请求 user 消息里记忆层的「今天」等于 `observation["memory_layer"]["今天"]`，最后一项是改计划那次决策的条目 | C7 |
| `test_continuation_messages_read_earlier_changes_and_skip_memory_and_feedback_tool_messages`（:989）、`test_layer_changes_overlay_earlier_changes_and_keep_the_latest`（:1038）、`test_layer_changes_report_errand_rows_added_changed_and_removed`（:1060） | 夹具层：人设层为 `{"layer": "人设层"}`，记忆层为 `{"layer": "记忆层", "可查": {}, "近三天": {}, "今天": []}`，待办层加 `"orders": []`；:1038 删去关系看法那一项及其期望；:1061 的文档字符串改指 docs/design/06-agent.md「提示与工具」 | C3、C7 |
| tests/integration/life/test_carried_records.py 模块说明 | 改为「带入的记录：占槽位的本人活动不分种类、这段停留或路程里做完的查询……」 | P4、P5 |
| test_carried_records.py::`test_paused_activity_concluded_elsewhere_is_recorded_where_it_ended`（:173） | 改名 `test_paused_activity_concluded_elsewhere_leaves_the_progress_layer`：结束之后的观察里没有它；终态 `action_result` 事件是 `cancelled`、带 `contents` | P6 |
| `test_carried_record_has_no_self_note`（:208） | 出发前那次观察里的记录也没有 `self_note`（:221 改为 `"self_note" not in home`） | P6 |
| `test_departure_and_purchase_records_are_not_carried`（:229） | 改名 `test_departure_and_purchase_records_are_carried_while_they_hold_a_slot`：路上观察里出行与购买各一条带入记录，购买的结果是终态结果；另断言 `todo_layer.orders` 恰是那件笔记本的一行；记忆层一半的改写照第 02 章「要改的现有测试」同一行（在下一次决策之后的「今天」里找购买行），不删 | P4、T1 |
| `test_paused_activity_is_not_carried_until_resumed`（:243） | :261 改为 `assert not progress_calls(held, "pause_activity")` | P6 |
| `test_activity_finishing_at_the_arrival_second_is_told_by_the_memory_layer`（:283） | 改名 `test_activity_finishing_at_the_arrival_second_is_carried_into_the_stay`：到达后的观察里有 Keep 的带入记录，结果是终态结果；记忆层一半的改写照第 02 章「要改的现有测试」同一行（在下一次决策之后的「今天」里找 Keep 的结束行），不删 | P4 |
| `test_carried_record_sorts_first`（:330） | 路上 report_status 之后：前两条是出行与 Keep 的带入记录（按请求序号），`occurred_at` 都不晚于 `session_started_at`；没有 report_status 记录 | P6、P7 |
| tests/integration/life/test_progress_layer_schema.py | 删去 `_location_activity_ended_early` 与 `_unfinished_query` 两种情形（它们的记录收尾后不再进进度层）；新增 `_sleep_awaiting_conclusion`、`_trip_awaiting_conclusion`、`_paid_purchase_awaiting_conclusion` 三种情形 | P4、P6 |
| tests/integration/life/test_query_activity.py::`test_concluded_query_has_no_answer_and_frees_slot_two`（:402） | :413-416 改为读这条查询与那条收尾的 `action_result` 事件做同样的断言；断言收尾之后的观察里两者都不在进度层 | P5、P6 |
| tests/integration/life/test_query_answer.py::`test_answer_stays_for_the_stay_and_leaves_a_settled_line_after_departure`（:301） | 进度层部分照旧（答案在两次决策之后仍在、出发后离开）；:317-319 的记忆层断言改写归第 02 章 | P5 |
| tests/integration/life/test_result_contents.py::`test_finished_app_use_lists_what_was_watched`（:65） | 文档字符串「收尾之后这条记录仍在」改为「收尾之后这条记录离开进度层」；:81-82 改为断言收尾之后的观察里没有它 | P6 |
| test_result_contents.py::`test_ended_activity_lists_finished_items_then_the_unfinished_one`（:111） | :121 改为 `(terminal,) = _second_results(engine, dinner.action_id)` 与 `result = public_result(terminal.data, engine.context.inputs, {})`，其后 `status`、`message`、`details` 的断言照旧；另断言 `observe_only(engine, "ended")` 的进度层没有 `arguments["detail"] == "工作日晚饭"` 的 `start_main_activity` 记录 | P6 |
| test_result_contents.py::`test_series_ended_at_an_episode_boundary_lists_only_finished_episodes`（:131） | :142-143 同样改为 `(terminal,) = _second_results(engine, dinner.action_id)`，断言 `public_result(terminal.data, engine.context.inputs, {})["details"] == {"contents": [_row("备菜", "约 20 分钟", True)]}`；另断言收尾之后的观察里同样没有「工作日晚饭」的 `start_main_activity` 记录；这两例改完后 `_dinner_record`（:41-45）没有调用方，删去 | P6 |
| test_result_contents.py::`test_paused_then_ended_activity_lists_what_was_done_before_the_pause`（:149） | :161-164 改为 `(terminal,) = _second_results(engine, rest.action_id)`，断言 `public_result(terminal.data, engine.context.inputs, {})` 的 `status == "cancelled"`、`details == {"contents": [_row("测试休息", "约 15 分钟", False)]}`；另断言收尾之后的观察里没有 `arguments["detail"] == "测试休息"` 的 `start_main_activity` 记录 | P6 |
| tests/integration/life/test_pause_limit.py（:91-94） | 删去 :91-94 的 `shown = last_result(seen)` 及其三条断言（被拒结果已由 :79-83 对 `refused` 的断言覆盖）；改为断言 `seen`（拒绝之后、收尾之前的观察）里 `progress_calls(seen, "pause_activity") == []`，`seen["progress_layer"]["rejected_today"]` 恰有一行，其 `why` 为 `_reason(name)[:60]`；导入（:15）的 `last_result` 换成 `progress_calls` | P6 |
| tests/integration/life/test_autoplay_display.py::`test_ended_app_use_lists_three_contents_and_says_how_many_are_left_out`（:268） | 两例都改为对终态 `action_result`（:281-282 取出的 `terminal`）断言 `public_result(terminal.data, engine.context.inputs, {})["details"] == {"contents": rows, "contents_omitted": "还有 2 条没列"}`（`{}` 照第 08 章「没有食材包的用例传 `{}`」；:279 的断言因此移到 :281-282 之后，模块导入 `public_result`）；`[record] = progress_calls(observed, SIDE_TOOL)`（:276）改为断言 `progress_calls(observed, SIDE_TOOL) == []`（收尾之后这条记录离开进度层），`validate_layer(observed["progress_layer"])` 照旧；文档字符串「五集副本收尾之后，进度层里开始请求的记录是第二条结果：恰为……」改为「五集副本收尾之后进度层没有这条记录；终态结果的投影恰为……」；它仍是变异 `result_contents_list_every_row`（tests/dataset_acceptance.py:704-708）的杀死者 | P4、P6 |
| tests/integration/life/test_phone_records.py::`test_phone_records_never_reach_any_prompt_layer`（:392） | `purchases` 的 `channel` 断言移到收尾之前的一次观察；收尾之后的观察里进度层没有那条购买记录，「冰美式」只在送达记录里 | P6 |
| tests/integration/start_interfaces/test_location_activities.py（:192-194） | `finish_activity` 改为 `until_ended`，在收尾之前读进度层记录；:252 经 `reach` 停在晚饭待收尾时读，已在收尾之前，不改 | P4 |
| tests/integration/start_interfaces/test_purchase_array.py（:67-68） | 同上 | P4 |
| tests/integration/start_interfaces/test_purchase_prefer.py | `_side_record`、`_channels` 的调用点凡在收尾之后的，改为收尾之前观察 | P4 |
| tests/integration/runtime/test_errand_prompt_separation.py（:33-35） | 改为断言下一次观察的进度层没有 conclude_activity 记录、全层不含「独特事务备注」 | P6 |
| tests/integration/runtime/test_decision_context_roundtrip.py（:120） | `waited_call` 是等待活动的带入记录，断言它没有 `self_note`；`_REPLY` 仍在 user 消息里（来自记忆层「今天」的「正文：」，第 02 章） | P4、P6 |
| tests/integration/runtime/test_conclude_memories.py::`test_memory_queries_and_the_reply_body_reach_the_next_progress_layer`（:701） | 改名 `test_the_reply_body_and_the_conclusion_stay_out_of_the_next_progress_layer`：下一次观察的进度层没有 conclude_activity 记录、不含 `self_note`、不含收尾写下的记忆与看法正文；检索部分归第 05 章 | P6 |
| tests/unit/life/test_memory_progress_contract.py（:98-99、:105） | `_world_records(ctx, store, 0)` 改为 `_world_records(ctx, store, (0, 0))` | P3 |
| tests/integration/runtime/test_life_agent_protocol.py（:193） | 观察夹具的待办层加 `"orders": []` | T2 |

上表即全部受影响的现有测试。读进度层记录而不在上表的测试文件逐个核过，不受本章影响：
tests/integration/start_interfaces/test_start_group.py 与 tests/integration/runtime/test_order_app_refusal.py 只读 `rejected_today`；
tests/integration/runtime/test_model_exchange_attempts.py 比较有无重发时的进度层逐字节相同；tests/system/lifecycle/test_parcel_arrival_export.py
在驿站取件仍占槽位时读它的带入记录；tests/integration/runtime/test_agent_branch_coverage.py 只往夹具里加一条 `plan_reminder`。
实施时若另有测试因本章行为失败，即本表遗漏，把它补进本表，并照同样的两种改法之一处理（在活动仍占槽位时读，`until_ended` 代替
`finish_activity`；或改读那条 `action_result` 事件），不另设例外。

必须照旧通过：tests/unit/life/test_memory_progress_contract.py::`test_rejected_today_counts_only_evidence_from_local_midnight_to_now`（:268）、
tests/unit/life/test_shared_observation_contract.py::`test_progress_layer_requires_an_active_real_session`（:133）、
tests/integration/life/test_history_reads.py::`test_session_lower_bounds_equal_the_full_result_scan`（:39）。

### 源码变异（`tests/dataset_acceptance.py`）

| 名称 | 符号 | 原文 → 替换 | 杀死它的测试 |
| --- | --- | --- | --- |
| `carried_records_only_interact`（取代 :394-398 的 `no_record_carried_into_a_later_segment`） | `worldmock.simulation_engine.observation.layers.progress_layer::_carried_activities` | `carried = {activity.activity_id: activity for activity in slot_activities(context.open_activities)}` → `carried = {activity.activity_id: activity for activity in slot_activities(context.open_activities) if activity.kind == ActivityKind.INTERACT}` | test_progress_bounds.py::`test_every_slot_activity_is_carried_whatever_its_kind[sleep]`、`[travel]` |
| `finished_queries_not_carried` | 同上 | `carried.update((activity.activity_id, activity) for activity in _finished_queries(context, store, stay))` → `carried.update(())` | test_progress_bounds.py::`test_a_query_is_carried_while_running_and_its_answer_until_departure`、test_query_answer.py::`test_answer_stays_for_the_stay_and_leaves_a_settled_line_after_departure` |
| `notices_bounded_by_the_published_bound` | `worldmock.simulation_engine.observation.layers.progress_layer::progress_view` | `notice_after = max(0 if previous is None else previous.observation_seq, day)` → `notice_after = max(published, day)` | test_progress_bounds.py::`test_a_balance_notice_from_a_decision_commit_reaches_the_next_decision_only` |
| `observation_passes_no_published_bound` | `worldmock.simulation_engine.observation.layers.observe::ObservationBuilder._observation_data` | `view = progress_layer.progress_view(layer, self.context.store, memory.published, memory.facts.kits)` → `view = progress_layer.progress_view(layer, self.context.store, 0, memory.facts.kits)` | test_progress_bounds.py::`test_a_delivery_is_in_the_progress_layer_until_the_next_decision_publishes_it` |
| `unpaid_quotes_listed_as_orders` | `worldmock.simulation_engine.observation.layers.todo_layer::open_orders` | `if order.agent_id == agent_id and order.paid_at is not None` → `if order.agent_id == agent_id` | test_todo_orders.py::`test_a_paid_delivery_is_an_order_in_transit_until_it_arrives` |
| `chain_memory_prefix_unchecked` | `worldmock.agents.protocol.life_continuation::layer_changes` | `if seen["memory"] != now["memory"][:len(seen["memory"])]:` → `if False:` | test_same_second_continuation.py::`test_a_rewritten_today_list_ends_the_continuation` |
| `chain_events_dropped` | `worldmock.simulation_engine.decisions.continuation::same_second_chain` | `"events": notice_records(context, store, previous.event_seq)}` → `"events": []}` | test_same_second_continuation.py::`test_chain_events_are_the_notices_after_the_previous_observation` |

`result_details_schema_refuses_query`（:303-309）的杀死者去掉
`tests/integration/life/test_progress_layer_schema.py::test_real_progress_layer_passes_layer_validation[unfinished-query]`，其余照旧。
每条原文在所在符号里恰出现一次（tests/mutation_runner.py:126）。

### 门禁

- 分支覆盖率 ≥85%，公开接口零未覆盖（scripts/coverage_gate.py）：`notice_records`、`tool_call_record`、`open_orders` 由上面的测试覆盖；
  `_previous_decision`、`_terminal_result` 的错误分支、`layer_changes` 的两个 None 分支各有测试。
- 两份日运行基线（tests/fixtures/dayrun-{activity,callback}-baseline.json）与查看器上下文的重冻归第 10 章。

## 不在本章

- 今天条目的形状、核验短句、自述、C_j 与「发布」：第 02 章「今天条目」。
- 记忆层四个键、「今天」的递推、14,000 字上限与兜底、层序、按层名取层：第 01 章「记忆层：形状、预算、兜底与层序」。
- 零点界 B_D、归日规则、零点计划昨日窗口的归日函数：第 03 章「类别、归日与近三天」。
- 回看去重（`noted_label`、`note_labels` 改扫「今天」前缀）、`search_memory` 与查询工具面：第 02、05 章。
- 人设层删去看法、系统层与只读规则原文（含 rules[0] 对进度层的说明）、提示指纹：第 07 章「人设层与系统层规则」。
- 零点计划输入里的食材包句、反思与巩固的退役：第 08 章「退役项、食材包句与环境包」。
- 进度层与待办层体量、续接成功率等试点指标（M6、M9）的统计与查看器：第 09 章「分析工具、运行比对与查看器」。
- 版本号、两份日运行基线的重冻、LabelKit 通知里待办层新键与记忆层形状的说明：第 10 章「实施：版本、文件、设计文档、测试与门禁」。
- 公开结果明细 `_RESULT_DETAILS` 的键集不变（续接 tool 消息里控制请求与被拒请求的结果仍用它）。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 「上一次决策」怎样从存储里找 | 第 02 章【E1】的最后一次决策（`decision_commits(...)[-1]`）；它的观察用 `read_decision_observations` 读、取序号最大的一条，决策时刻取 `Decision.occurred_at`；没有决策时观察序号为 0、计划提醒不设下界；有决策却缺观察时 `IntegrityError("Previous decision has no observation")` | 一条规则只在一处：决策的分组与次序只由第 02 章【E1】定义，P_k 里的 C_{k−1} 也取自同一组决策，两处不会指向不同的决策；裁定 2：任何世界请求都结束一次决策；`observe_only` 之类的观察不属于决策，不能用「最近一条观察」；没有观察的决策不能结算（start_group.py:78-84），不另设回退 |
| 进度层还剩哪些 `tool_call` | 只剩带入记录（开始请求）；控制请求、被拒请求与已收尾活动的记录不再出现，Schema 的 `tool` 收窄为两个开始工具 | 它们的请求与结果都在第 k−1 次或更早的提交里，序号 ≤ C_{k−1} ≤ P_k；提案进度层表里 `tool_call` 只有两类带入记录；11:50 示例的进度层只剩进行中的会议、做完的查询与新事件 |
| 第一类重叠的范围 | 写明包含待收尾活动在 P_k 之前结束时的结束行（购买另有购买行） | 提案表把「待收尾」的活动列为带入记录，带入记录取最新一条结果；「只共用名称和开始时刻」只对进行中的活动成立 |
| 终态结果怎样读、缺失怎么办 | 从 `max(第一条结果序号, last_event_seq_before(ended_at))` 读起取第一条同动作的 `action_result`；缺失时 `IntegrityError("Ended activity has no terminal result")` | 终态结果与活动终结同一次提交、发生时刻相同（time_advance.py:303-317、activity_control.py:478-488）；AGENTS.md 不设回退 |
| 「这段停留里做完的查询」的判定 | `completed` 的查询、结束不早于本段开始、终态结果序号大于本段下界；本段可以是停留或路程 | 提案术语对照「只有这段停留里做完的查询仍按当前 session 判定」；rules[0] 写「这段停留或路程里查到的答案」；查询不能在路上开始、出发前要先收尾，所以总在开始它的那段 |
| 非记忆与记忆事实的闭集 | 记忆事实：`delivery_arrival`、`errand_completed`；非记忆：`errand_reminded`、`app_push`、`callback_reminder`、`balance_decreased` | 提案进度层表两行；第一行「结束、付款、送达、办成」里，结束与付款在进度层没有环境事件记录，只作为带入记录（【P4】待收尾的活动、【P5】做完的查询）的终态结果出现，带入记录不看下界（见「第一类重叠的范围」）；所以环境事件的记忆事实只剩送达（含到站）与办成，与第 02 章【E12】「送达」「办成」两类的来源事件同一闭集；第二行的四种照原文 |
| `rejected_today` 与「不相交」 | 不相交的断言只针对 `events`；`rejected_today` 是当日汇总，读法照旧 | 提案「`rejected_today` 不变」；它按日读拒绝证据（progress_layer.py:316-347），与条目里的「被拒：」行同源是既有设计 |
| 在途订单的判定 | 已付款（`paid_at` 不为空）且 `pending` 或 `ready_for_pickup`；未付款的报价不算 | 现行记忆层的「未了结订单」就是已付款购买的这两种状态（memory_layer.py:264-269）；报价由进度层带入的购买记录表达，算进待办层会重复；零点计划现行收所有 `pending`，差别只在跨零点的购买活动 |
| `orders` 行的形状 | `{order, status, destination, expected_at}` | 提案只定键名、≤6 行、合成行与按订单标签比较；`order`、`status` 沿用进度层送达记录的键（progress_layer.py:262-264），`destination`、`expected_at` 沿用购买明细的键（prompt_schemas.py:211-212） |
| 同一商品多份 | 同商品、同购买秒、同状态、同目的地、同预计到达的记录合成一行，份数为条数 | `DeliveryState.quantity` 恒为 1（orders.py:38），「同一个名称写两次就是两份」（state/interaction/requests.py:168）会产生标签相同的记录；按标签比较要求标签唯一；核验短句也把多份写成「A2份」 |
| 合成行的写法 | 同一数组的末项 `{"order": "另有 N 笔在途"}`，N 按行计 | 提案「多出的合成一行」；放在 `order` 键上，`changes` 照样按标签比较 |
| 合并之后仍有重复标签 | 不续接，与非前缀同一处理 | 同一本地分钟里两次购买同一商品时标签仍可能相同，按标签的 added/removed 表达不了；断链是提案为不能表达的情形定下的处理（裁定 20 配套） |
| 待办层「≤1,600 字」 | 只当体量预估，不新增运行时字数上限；`orders` 由【T2】限为至多 6 行加一行合成行，`paused_activities`、`errands`、`day_plan` 照旧；待办层的大小由第 09 章 M6 按层名统计、只报告 | 提案只在上限表的「目标上限」列给出这个数，执行方式只写了在途订单 ≤6 行与合成行；新增一道字数上限是提案以外的行为（与第 01 章「近三天」≤2,900、第 07 章人设层 ≤1,400 同样处理） |
| 零点计划在途订单的行 | 仍是四个键，`ready_for_pickup` 的行不加状态键 | 提案「输入沿用原有内容，只做三处改动」 |
| 续接 `events` 的范围 | 只列非记忆世界记录 | 提案「`events` 改为序号大于上一次观察的非记忆世界记录」；记忆事实经 `memory.added` 送达，列两遍会重复 |
| 续接结果取哪一条、缺失怎么办 | 取请求最新的一条结果；缺失时断链 | 与进度层 `tool_call.result` 同一投影（06-agent.md:223「只显示最新的一条」）；现行 `len(own) != 1` 时断链 |
| 「不能续接」怎样传出 | `layer_changes` 返回 None 并写英文警告；`continuation_messages` 随之返回 None | 提案「`continuation_messages` 返回 None，宿主改用新快照（life_agent.py:488-492）」；与 `send_check` 断链时写警告（life_continuation.py:89）一致 |
| 续接读「今天」的前提 | 记忆层总带 `今天` 键（第 01 章规定空时为 `[]`）；比较前两边都经 `render_layer_json` 往返 | 提案「「今天」在链首和当前两边都经 render_layer_json 往返规范化」；第 01 章「记忆层的形状」 |
| 进度层与续接的结果投影要不要传食材包 | `progress_view`、`tool_call_record` 增加参数 `kits`，带入记录与续接 `tool_results` 都以同一次观察的 `ActivityFacts.kits` 调用 `public_result`；`MemoryView` 字段照第 02 章（保留 `facts`，P_k 在 `published`） | 第 08 章 `public_result` 的 `kits` 必填，并要求进度层与续接投影都传同一次观察的 kits；提案第三章代码表 result_projection.py 一行要求开始结果与完成结果带食材包句 |
| 控制请求参数投影 | 删除 `_arguments`、`control_items.control_request` 与 `_MEMORY_KEYS`；`control_items` 文档字符串的读取方列表删去「进度层」 | 控制请求不再进进度层后它们没有调用方；进度层不再导入 control_items |
| 浓缩第 1 步（去 `self_note`） | 删除，浓缩只剩折叠；第 01 章「容量浓缩」的说明与 `test_condensation_rewrites_only_the_progress_layer` 的夹具（四条不带 `self_note` 的未受保护记录，浓缩后折成一条 `condensed` 记录）与此一致；`test_the_session_record_path_is_deleted` 断言这一步已删 | 提案「会话记录不再带 `self_note`」之后这一步永不触发；pyproject 的覆盖规则与 AGENTS.md 都不接受永不执行的分支；「容量压缩：不变」指仍只压进度层，第 0 步与折叠不变 |
| `record_lines` 的 `facts` 参数 | 删除，连同收尾分支 | 唯一传它的是进度层的 `here`；零点计划不传它、收尾记录本来就得到 `[]` |
| 05-recording-engine.md「session 投影中两层不相交」 | 05 只在「权威事实与投影」:5-6 之后加一句链接句（两层不再按 session 的事件序号区间投影、按来源事件序号不相交、只有两处写明的重叠，session 下界只剩两处用途）；「两层不相交」的定义、已发布界与带入规则只写在 06-agent.md「进度层的下界与带入的记录」 | 提案第三章「设计文档」表的 05-recording-engine.md 一行要「session 投影中「两层不相交」的新说法」；05 现在没有讲两层关系的句子，旧说法（两层都取 session 区间）在 session_bounds.py 的模块说明里；提案术语表「记忆不再按 session 归档……也不再按 session 取」，所以新说法写的是不按 session 投影（【P1】、session_bounds.py 的新模块说明）；一条规则只在一处，05 只写一句并链接 |
| 待办层的机器形状写在哪里 | 09-io-formats.md「提示层的机器形状」的「待办层」小节写键与取值，06 写行为并链接过去 | 提案设计文档表把「待办层的机器形状」列在 09；第 01 章已建这一节并把「待办层」小节分给本章 |
| B_D 的取法 | 调用第 03 章的 `day_bound(store, local_date(now, zone), zone, now)`；`rejected_today` 也改调它，值不变 | 一条规则只在一处（AGENTS.md）；提案「取法与 `rejected_today` 相同」；第 03 章是零点界的规定者，它给 progress_layer.py 写的导入与 `rejected_today` 的调用就是这个签名（第 02 章已改为同一签名） |
| 06-agent.md:184 怎样改 | 完整键集与断链条件只写在 :153-155 的新段落；:184 原句只讲环境层，仍成立，不改 | 提案「设计文档」表列 :153-155 与 :184；一条规则只在一处，键集写在「提示与工具」一处，:184 只讲环境层的 `duty`、`tomorrow` 不进 `changes` |
| 06-agent.md:265 归哪一章 | 归第 02 章（它的「设计文档」已写这一句），本章不另写；本章改 :239、:263 两句 | 同一句只由一章改写；本章的带入规则使「没有查完」的形状不再出现在任何提示层里，:239、:263 的「人物读到」须随之改 |
| 跨夜睡眠测试的时刻 | 23:00 下单、23:02:35 收尾购买并开始睡 8 小时、23:18:25 外卖在睡眠中送达、07:02:35 醒 | 提案的测试要的是跨过零点的睡眠与零点前送达的外卖（「23:30 睡下以后、零点之前送达的外卖」）；运行窗口必须落在整点上，睡眠要求槽位 2 为空，所以睡眠只能在购买收尾之后开始；夹具里 23:00 下单的外卖 23:18:25 送达，落在睡眠之中、零点之前，与提案的情形相同 |
