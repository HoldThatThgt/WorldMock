# 02 今天条目

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。

## 目标与依据

本章把冻结提案 docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md（下称提案）里关于「今天」每一条条目的规定落成实现，按终稿形态一次交付：条目从一开始就渲染「看法·」，不建提案 P1 期「不渲染看法」的临时写法。

实现的提案条文：

- 第二章「今天条目」一节第 1–12 点（形状、窗口、核验短句、自述前缀、120 字取舍、各类决策、全文、零点切分、发布、与进度层的界、体量、示例）。
- 第二章「归日规则」里涉及条目的两点：活动在哪天结束就记在哪天，跨过零点还没结束的事在零点收尾条目里记一句「跨过零点」；00:10 的决策及其自述属于新的一天。
- 第二章「与现有机制的交互」里 recall_task_summary 一条（`noted_label` 与 `_note_labels` 改为扫描「今天」的前缀）。
- 第二章「存储与事件记录」第一条：条目、全文与零点收尾条目都是已提交事件加冻结目录的纯函数，不另外存储；C_j 和每条条目在本人事件的增量缓存里按决策缓存。
- 第三章「代码」表 `entries.py` 一行里本章的部分（C_j、窗口、核验短句、短名、规则 B、全文、核验超过 120 字时的兜底），`days.py` 一行里的零点界与零点收尾条目，`memory_layer.py` 一行里的 `noted_label` 与「另返回 P_k」。

落实的裁定（第五章）：

| 裁定 | 本章的落实 |
| --- | --- |
| 2（选项 A） | 任何世界请求都结束一次决策，包括 report_status、revise_day_plan 与被拒的请求；每次决策恰一条条目；report_status 的总结写成「进展·〈短名〉：」自述 |
| 11（选项 A） | 「今天」按决策先后排成一条列表，每条带「类别」 |
| 12（选项 A） | 只渲染非 routine 的「记住：」；routine 记忆只进全文 |
| 18 | 只开始活动的决策也产生条目，核验写「开始〈短名〉」，开始组写两项 |
| 19（第三批确认的读法） | 每条 120 字只数「核验」「自述」各字符串的 Python `len()` 之和，键名、引号、「时刻」「类别」不算 |
| 20 | 第 k 次决策提交后第 k 条就发布，从第 k+1 次决策起可见，包括同秒链里的下一次决策 |
| 21（选项 A） | 查询答案进条目全文的「当时查到」，标查询时刻；提示里的条目只有「查完〈app〉·〈范围〉」一行 |
| 26（选项 A） | report_status 的进展按规则 B 写进自述 |
| 27（选项 A） | 「先给每条开头」，即提案的规则 B |

依据的测量：研究目录 docs/dev/research/layered-memory-2026-10-29/out3/entries/（entries.py、v_1012_entries.json）、out3/reports/entries.md、out3/reports/sheet.md 的「今天条目：REWRITE」、out3/reports/code.md 的 (b) 节。本章「120 字以内的取舍」的算法在 V 10-12 全部 89 条决策上逐条复现了测量脚本 `cap_b` 的输出（自述前缀按 sheet.md 第 2 条改成「进展·」以后仍 89/89 相同），测试用这份数据作夹具（见「测试」）。

本章引用而不重述的事实：

| 事实 | 所属章 |
| --- | --- |
| 记忆层的形状、「今天」列表的递推、14,000 字上限与三步兜底、`PublishedEntry`、记忆层的 Schema | 01 记忆层：形状、预算、兜底与层序（Schema 的代码另见 07） |
| `MEMORY_CATEGORIES`、规则 0–7 的类别推导、被拒请求的类别、零点计划的归日窗口、近三天 | 03 类别、归日与近三天 |
| 日回顾的输入如何用条目全文与零点收尾条目编号 | 04 日、月、年回顾 |
| 检索「今天」与「未整理」时怎样取用条目全文 | 05 search_memory 与工具面 |
| 进度层怎样用已发布界 P_k、带入记录、同秒续接里 `changes.memory.added` 的递送与断链 | 06 进度层、待办层与同秒续接 |
| 系统层 rules[1] 里对条目的说明原文、人设层不再带看法、`persona_views` 的去向 | 07 人设层与系统层规则 |
| 「活动 → 所用食材包」的映射与完整句「，用的是…」、反思的退役 | 08 退役项、食材包句与环境包 |
| 版本、指纹、两候选重冻与门禁 | 10 实施：版本、文件、设计文档、测试与门禁 |

## 行为

条款编号【E1】…【E46】供「测试」一节引用。本章的「字」一律是 Python `len()` 的字符数。文中「…」是 U+2026，「–」是 U+2013。

### 记号与界

【E1】**决策。** 一个人物的决策是本人 `action_requested` 事件按 `decision_id` 分成的组；组按组内最小事件序号排序，依次是第 1、2、…次决策，同秒链里的每次决策各算一次。决策时刻 t_j 是组内第一条 `action_requested` 的 `occurred_at`（一次决策内不推进时间）；决策日期 D_j 是 t_j 的场景本地日期。只读查询（`recall_task_summary`、`search_memory`）不写 `action_requested`，不构成决策。一次取数只算组内最小事件序号不超过它的截止序号（【E40】）的组；组内最小事件序号大于截止序号的组在这次取数里还不是决策，也不查它的结果。检索的截止序号是冻结观察的 `event_seq`（05 章【S5】），只算这次观察之前已经登记的决策；检索不能与世界请求同批（05 章【S3】），检索时本次决策还没有登记世界请求。

【E2】**C_j。** 第 j 次决策的提交末序号 C_j = `store.commit_last_seq(s)`（src/worldmock/recording_engine/storage/store.py:357-373），s 是 `decision_id` 等于这次决策的第一条 `action_result` 事件的序号，即这次决策结算那次提交里的第一条结果（受理、拒绝或中止都写 `action_result`：src/worldmock/simulation_engine/decisions/decision_coordinator.py:312-348、:377-411、:216-251）。活动终结时写给原请求的第二条结果也带原决策的 `decision_id`（src/worldmock/simulation_engine/evidence/actions.py:94-102），它的序号总大于 s，不影响「第一条」。按【E1】计入的一次决策没有任何 `action_result` 时，写英文错误日志并抛 `IntegrityError("Decision world request has no committed result")`。C_j 一经算出不再改变。

【E3】**B_D。** D 日的零点界 B_D = `day_bound(store, D, tz, now)`，即 03 章 src/worldmock/simulation_engine/observation/memory/days.py 的函数：`store.last_event_seq_before(day_window(D, tz).start)`，D 日 00:00 之前最后一个事件的序号，没有这样的事件时为 0；`now` 早于 D 日 00:00 时它抛 `IntegrityError("Day bound is read before the day starts")`。tz 是 `RunConfig.timezone`，`now` 是调用方给出的此刻（观察与检索是观察时刻，零点任务是零点时刻）。本章不另定义零点界，缓存只存 `day_bound` 的返回值（【E42】）。时钟推进以零点为边界，同一次提交不跨过零点，所以一次提交的事件不会被 B_D 分开。

【E4】**窗口。** 第 k 次决策的条目窗口是序号落在 (max(C_{k-1}, B_{D_k}), C_k] 的本人事件；第 1 次决策取 C_0 = 0。窗口包括上一次决策之后时钟推进里结算的自然结束、送达和付款，也包括第 k 次决策自己的请求与提交。窗口的两端都落在两次提交之间（C_j 是提交的末序号，B_D 不分开提交），所以同一次提交的事件总在同一个窗口里。

【E5】**已发布界。** 一次观察的已发布界 P = max(C_last, B_today)：C_last 是这次观察之前最后一次决策的 C（还没有决策时为 0），today 是观察时刻的场景本地日期。第 k 次决策的观察里 P 就是提案的 P_k = max(C_{k-1}, B_D)。序号不超过 P 的事件，要么已经写进今天的某条条目，要么属于前一天。P 由「今天」的取数函数一并返回（【E40】），进度层怎样用它见 06 章。

【E6】**零点收尾条目的界。** C_last(D) 是 D 日最后一次决策的 C，D 日没有决策时取 B_D；B_{D+1} 是 D+1 日的零点界 `day_bound(store, D+1, tz, now)`（只在【E39】的前提成立时求），即结束 D 日的那个零点之前最后一个事件的序号。D 日的零点收尾条目的窗口是 (max(C_last(D), B_D), B_{D+1}]。于是每个序号大于 B_D、不超过 B_{D+1} 的事实恰好落进 D 日的一条条目：某次决策的条目，或零点收尾条目。

### 条目对象

【E7】**宿主内部的条目。** 每条条目在宿主内部是一个 `TodayEntry`（字段见「接口与文件」），保存：日期、`decision_id`（零点收尾条目为 `None`）、「时刻」、「类别」串、各行核验（每行带短句、完整行、去标题行、自己的类别、来源事件身份，查完行另带「当时查到」）、各项自述（每项带全文、种类、自己的类别、来源事件身份、所指活动）、这次决策主请求的工具名、受理的控制请求所点名的活动身份、条目里各活动的标注、渲染形状。只有渲染形状进提示。

各行、各项的来源事件身份（`EntryLine.event_ids`、`SelfItem.event_ids`，各按事件序号升序、去重）恰为：结束行与查完行——这项活动的结束事件（`cached.endings`）与按【E12】读到终态快照的那条 `action_result`（两者是同一事件时一项）；购买行——这条 `action_result`；送达行、待取行——这条 `delivery_arrival` 事件；办成行——这条 `errand_completed` 事件；个人事务变更行——这条 `errand_registered`、`errand_updated` 或 `errand_cancelled` 事件；暂停行——这条 pause_activity 请求 R_i 的 `action_requested` 与 F_i；开始行、被拒行——这次决策全部 R_i 的 `action_requested` 与 F_i；改计划行、兜底行——R_1 的 `action_requested` 与 F_1；跨过零点行——这项活动序号不超过 B_{D+1} 的最后一份快照所在的 `action_result`。总结、进展、看法、记住、routine 各项——这一项所属控制请求 R_i 的 `action_requested`；正文——R_1 的 `action_requested`。`TodayEntry.event_ids` 是各行（按行序）再各项（按项序）的来源事件身份依次相连、按首次出现去重。

【E8】**渲染形状。** 模型看到的条目恰为

```json
{"时刻":"HH:MM","类别":"饮食","核验":["…"],"自述":["…"]}
```

键依次是 `时刻`、`类别`、`核验`、`自述`；`核验` 或 `自述` 为空列表时这个键省略；每条至少有 `核验` 或 `自述` 之一（【E32】保证）。`核验` 是【E20】取舍后的各行，`自述` 是【E24】–【E30】取舍后的各项，两者各字符串的 `len` 之和 ≤120。

【E9】**时刻。** 决策条目的「时刻」是 t_j 的场景本地 `HH:MM`，同秒链里的几次决策共用这一分钟。零点收尾条目的「时刻」是 `"24:00"`。

【E10】**主请求与主类别。** 主请求是 03 章 `primary_request(requests)` 给出的那一条，`requests` 是这次决策全部 `action_requested` 事件按序号排列（开始组取 `start_main_activity` 那一条，不论登记先后）。主类别是 03 章 `request_category(book, store, 主请求, 主请求的第一条 action_result, activity_categories)`，取法只在 03 章第 7 节；`book` 与 `activity_categories` 见【E11】。

【E11】**类别。** 条目里的类别全部由 03 章 src/worldmock/simulation_engine/observation/memory/categories.py 的函数给出，本章只给它们的输入：

- `book = memory_book(store, inputs)`。
- 一项活动的类别是 `activity_category(book, 快照, purchase_goods(快照, 开始结果))`：快照是这项活动按【E12】读法取的快照（序号不超过窗口上界的最后一份）；开始结果是 `cached.first_results[快照.origin_action_id]`，即这项活动开始请求的第一条 `action_result`（【E42】），不在其中时写英文错误日志并抛 `IntegrityError("Activity has no start result")`。
- 这条条目涉及的活动是点名活动（【E15】），加上受理的开始请求开始的活动、受理的控制请求 `data.data.activity_ids` 的各项，以及看法、记住、routine 各项所指的活动。`activity_categories` 是这些活动各自的身份 → 类别。
- 各行、各项的类别按 03 章第 6 节的两张表取，表里「那项活动」「这一行所写的订单」与本章的对应是：结束行、暂停行、跨过零点行是所指活动；开始行是主槽位那项活动；查完行是那项查询活动；兜底行是这次请求第一项所点名的活动；购买行的商品是这一行所写已付款订单的 `goods_id`（`goods_category`）；送达行、待取行的商品是这一行所写订单的 `goods_id`；办成行与个人事务变更行按 03 章第 4 节的个人事务规则，取事务事件 `data.errand.product_id`；被拒行是这一行所写第一个被拒请求（即主请求）的 `rejected_category(book, store, request)`；改计划行为生活。自述项的类别按 03 章第 6 节「自述项」表，「这个控制项所点名的活动」是这一项所指的活动，「正文」取主类别。
- 条目的「类别」串是 `category_label(主类别, parts)`：parts 是按【E14】行序的全部行的类别（不论【E20】是否显示），接着是按【E23】次序的全部自述项的类别（含 routine 记忆），例如「工作、饮食」。零点收尾条目没有主请求，是 `category_label(None, parts)`。全文与渲染形状用同一个类别串。

### 核验行

【E12】**窗口里的已结算事实。** 只读窗口里本人的事件，按下面四类产生行；其余事件（模型证据、观察、`app_push`、`callback_reminder`、`balance_decreased`、`errand_reminded`、`errand_progressed`、`goods_received`、`callback_effect`、可见性为 `system` 的环境事件等）不产生已结算事实的行；【E13】的个人事务变更行另读本次决策引起的 `errand_registered`、`errand_updated`、`errand_cancelled`（系统可见）。

| 类 | 来源 | 产生 |
| --- | --- | --- |
| 活动结束 | 本人每项活动的结束事件（`activity_endings` 求出的带终态快照的最早事件，src/worldmock/simulation_engine/observation/wording/settled_text.py:156-169）落在窗口里 | 查询活动以「已完成」结束时一行查完行；购买活动在同一窗口里有它那条开始请求的购买行时不产生行；其余一行结束行 |
| 购买 | `action_result` 事件 `data.status == "completed"`，它的 `action_id` 属于本人一条开始请求（工具在 `START_TOOLS`），`placed_orders(data.data)` 里至少有一张 `paid_at` 不为空的订单 | 每个这样的结果一行购买行，只写已付款的订单 |
| 送达 | `environment_event`，`kind == "delivery_arrival"`，`visibility == "agent"` | 本人订单里 `status == "delivered"` 的写一行送达行，`status == "ready_for_pickup"` 的写一行待取行，两种都有时送达行在前；出现别的状态时写英文错误日志并抛 `IntegrityError("Delivery arrival order status is not delivered or ready_for_pickup")` |
| 办成 | `environment_event`，`kind == "errand_completed"`，`visibility == "agent"` | 一行办成行 |

活动结束一类读活动的方法：活动的事实取本人 `action_result` 事件携带的活动快照（`data.activity`，以及 `data.data.activities` 的各项；去掉 `content_title` 后按 `ActivityState` 读出），用序号不超过窗口上界的最后一份。活动终结时环境一定给原请求写一条带完整快照（含 `played`、`answer`）的结果（docs/design/06-agent.md:222），它与结束事件在同一次提交、同一个窗口里，所以读到的是终态快照。读到的快照不是终态时写英文错误日志并抛 `IntegrityError("Ended activity has no terminal snapshot")`。这条条目涉及的任何一项活动（【E11】），在序号不超过窗口上界的本人 `action_result` 里没有任何快照时，写英文错误日志并抛 `IntegrityError("Activity has no snapshot within the entry window")`。条目构建只读事件和冻结输入，不读 `WorldState`。

【E13】**本次决策自己的行。** 记这次决策的各条请求按 `action_requested` 的事件序号为 R_1…R_n（n 为 1，开始组为 2；R_1 是最先登记、带 `self_note` 的那条，开始组里不一定是【E10】的主请求），各自的第一条 `action_result` 为 F_i。

- F_i 的 `status` 是 `accepted`、`in_progress` 或 `completed` 时这条请求受理；是 `rejected` 时被拒。同一次决策的各条请求必须同为受理或同为被拒，否则写日志并抛 `IntegrityError("Decision results disagree")`。
- 任一 F_i 的 `status` 是 `failed` 且 `error_code == "DECISION_ROUND_ABORTED"`（中止的决策，src/worldmock/simulation_engine/decisions/decision_coordinator.py:216-251）时写日志并抛 `IntegrityError("Aborted decision has no today entry")`；中止之后运行即失败，不再有观察，恢复从中止之前的检查点分支。其余任何状态写日志并抛 `IntegrityError("Decision result status cannot form a today entry")`。
- 受理时：
  - 开始请求：一行开始行，列出各条开始请求开始的活动；活动身份取 F_i 的 `activity_id`，为空时写日志并抛 `IntegrityError("Accepted start has no started activity")`。
  - pause_activity：每项一行暂停行，加每项的总结（【E21】）。
  - report_status：每项一项进展（【E21】），不另写行。
  - conclude_activity：每项的总结、记住和看法（【E21】），不另写行；被结束的活动的结束行由【E12】的活动结束一类产生。
  - revise_day_plan：一行改计划行。
  - 三个控制请求另有个人事务变更行：窗口里 `kind` 为 `errand_registered`、`errand_updated`、`errand_cancelled` 的 `environment_event`，`caused_by` 含 R_i 的事件身份（src/worldmock/simulation_engine/scheduling/plans/errands.py:194-222、:527-534），按序号每个事件一行。
  - 控制请求逐项连到活动：第 i 项对应 F 的 `data.data.activity_ids[i]`（src/worldmock/simulation_engine/actions/dispatcher.py:257-268）；项数与身份数不等时写日志并抛 `IntegrityError("Control result activities disagree with its items")`。report_status 恢复暂停的活动时不另写行。
- 被拒时：一行被拒行（【E17】），原因不写，只在进度层 `rejected_today`。
- 两种情况都有正文：R_1 的 `data.self_note` 不为 `null` 时一项正文（【E21】）。

【E14】**行序。** 先是【E12】的已结算事实，按来源事件序号升序；同一事件产生的几行：结束行按活动的（`started_at`，`activity_id`）升序，送达行在待取行之前。然后依次是暂停行（按项的次序）、开始行、被拒行、个人事务变更行（按事件序号）、改计划行。兜底行（【E32】）只在没有任何行和项时出现，是唯一的一行。零点收尾条目是已结算事实，后接跨过零点行（按活动的（`started_at`，`activity_id`）升序）。

【E15】**短名与标注。**

- 一项活动的短名 = 它冻结的 `name`（活动快照里的 `name`，开始时由引擎命名，结束后不改名，src/worldmock/simulation_engine/activities/lifecycle/activity_control.py:139-141、src/worldmock/simulation_engine/activities/lifecycle/sessions.py:24-27）去掉第一个 `START_SUFFIX` 匹配（src/worldmock/simulation_engine/observation/targets/object_labels.py:14，「（M月D日 HH:MM 起）」），再去掉开头的一个「进行」。例：「进行处理消息与收尾（10月12日 08:34 起）」→「处理消息与收尾」；「购买美式咖啡（10月12日 08:34 起）」→「购买美式咖啡」；「驾车前往练秋湖研发中心（10月12日 08:03 起）」→「驾车前往练秋湖研发中心」；「睡觉（10月11日 22:08 起）」→「睡觉」。不读观察的 `context.names`，它的「（第二次）」后缀随当天的目标变化。`name` 为空串时写日志并抛 `IntegrityError("Activity has no frozen name")`。
- 条目里点名的活动是：结束行、暂停行、开始行、跨过零点行、兜底行和总结、进展各项所指的活动（查完行与购买行不点名活动）。一项点名活动的标注是它的短名；同一条目里另有一项点名活动的短名与它相同时，两项都用冻结的 `name` 原样作标注（与现行 `_labels` 的做法相同，src/worldmock/simulation_engine/observation/layers/memory_layer.py:299-303）。标注在条目内固定，条目的各行和各项都用它。

【E16】**时刻与数字写法。**（函数在 src/worldmock/foundation/time/readable.py，见「接口与文件」）

- 条目里的时刻相对条目的日期 D 写：与 D 同一本地日期写「HH:MM」；D 的前一天写「昨HH:MM」；其余日期（更早，或预计送达在 D 之后）写「M月D日HH:MM」，月、日不补零，中间没有空格。这个写法只依赖 D，不依赖观察时刻，所以一条条目的文字整天不变。
- 金额写成至多两位小数、去掉末尾零的元数，后面紧接「元」：「18.3元」「21.76元」「17元」。
- 份数写成同样的数字，后面紧接「份」：「1份」「2份」「0.5份」。
- 日期写「M月D日」。
- 需要在句首加时刻时，写时刻、一个空格，再接句子：「08:50 送达美式咖啡」。

【E17】**核验短句。** 写法逐字固定。下表的〈标注〉见【E15】，〈起〉〈止〉是活动快照的 `started_at`、`ended_at` 按【E16】的写法，〈标题段〉见【E18】，〈食材段〉见【E19】。

| 行 | 写法 | 例 |
| --- | --- | --- |
| 结束行 | 〈标注〉+ 空格 + 〈起〉–〈止〉+〈暂停段〉+〈终局段〉+〈标题段〉+〈食材段〉 | 「处理消息与收尾 08:34–08:54」「睡觉 昨22:08–07:48」「处理消息与收尾 11:54–12:13，提前结束」 |
| 〈暂停段〉 | 这项活动已恢复的各段暂停（`pause_intervals`，src/worldmock/simulation_engine/observation/wording/settled_text.py:181-191）写「（暂停 a–b、c–d）」，「暂停」后一个空格，各段用「、」分开；没有时为空 | 「…（暂停 09:10–09:20）」 |
| 〈终局段〉 | 按 `activity_control.ending_phrase(activity)`（src/worldmock/simulation_engine/activities/lifecycle/activity_control.py:180-190）的取值：「到达」「按计划结束」「结束使用」为空；「提前结束」写「，提前结束」；「失败」写「，失败」；以「比打算的多睡了」开头的写「，多睡N分钟」，N = `data.oversleep_seconds // 60` | 「睡觉 昨23:00–07:42，多睡12分钟」 |
| 出行到达 | `kind == "travel"` 且 `status == "completed"` 时，结束行的〈标注〉里第一个「前往」改成「到」 | 「驾车到练秋湖研发中心 08:03–08:33」 |
| 购买行 | 〈时刻前缀〉+「买」+〈商品段〉+「，」+〈共〉+〈金额〉+〈送达段〉 | 「买美式咖啡1份，18.3元，预计08:50送到练秋湖研发中心」「买公仔点心面（海鲜味）、高钙牛奶 10盒各1份，共21.76元，预计09:10送到练秋湖研发中心」 |
| 〈商品段〉 | 一张订单：「〈商品名〉〈份数〉」；两张以上且份数都是 1：各商品名用「、」相连后接「各1份」；其余：「〈商品名〉〈份数〉」各段用「、」相连。订单按结果里的次序 | 「柠檬茶1份」「牛肉面1份、薯片2份」 |
| 〈共〉〈金额〉 | 两张以上订单时写「共」；金额是各订单 `total_price_fen` 之和 | 「共21.76元」 |
| 〈送达段〉 | 订单里有 `fulfillment` 为 `delivery` 或 `station_pickup` 的：「，预计」+ 这些订单的 `expected_at` 按【E16】写出后去重、按时刻升序以「、」相连 +「送到」+ 它们的 `destination_id` 按订单次序首次出现去重以「、」相连；目的地是住处（`book.home_id`，即 `EnvironmentInputs.home_location_id`）写「家」，否则写地点在 `book.catalog` 里的 `display_name`。全部是到店（`pickup`）时为空 | 「，预计08:25送到家」 |
| 〈时刻前缀〉 | 订单的 `paid_at` 按【E16】写出的时刻与条目「时刻」不同时，写那个时刻加一个空格；相同时为空。一次购买的订单同时付款，取第一张的 `paid_at` | 「08:36 买…」 |
| 送达行 | 〈时刻前缀〉+「送达」+ 本人 `delivered` 订单的商品名按事件里的次序去重以「、」相连 | 「送达美式咖啡」「送达公仔点心面（海鲜味）、高钙牛奶 10盒」 |
| 待取行 | 〈时刻前缀〉+ 本人 `ready_for_pickup` 订单的商品名（同上）+「已到取货点待取」 | 「平台笔记本已到取货点待取」 |
| 送达、待取的〈时刻前缀〉 | 事件的 `occurred_at` 按【E16】写出的时刻与条目「时刻」不同时写它加一个空格 | 「08:50 送达美式咖啡」 |
| 办成行 | 「办成「〈事务名〉」」，事务名取事件 `data.errand.name` | 「办成「备好饮料」」 |
| 查完行 | 「查完」+〈app〉+「·」+〈范围〉+「：」+ N +「项可买」+〈未列〉+〈价段〉；这几分钟手机上没有 app（`data.app_id` 为 `null`）时写「查完」+〈范围〉+「：」…，没有「·」 | 「查完美团外卖·食品：20项可买（另160项未列），18–95元」 |
| 〈app〉〈范围〉 | app 是 `data.app_id` 在目录里的名称；范围是这项查询的短名去掉开头的「查询」（查询活动名是「查询」加各项规范名称以「、」相连，src/worldmock/simulation_engine/activities/query/query_activity.py:118-120） | 「美团外卖」「食品」 |
| N、〈未列〉、〈价段〉 | N 是答案 `data.answer.rows` 里 `available` 为真的行所涉及的不同 `goods_id` 个数；`data.answer.omitted` > 0 时〈未列〉写「（另M项未列）」，否则为空；N > 0 时〈价段〉写「，」+ 最小值 +「–」+ 最大值 +「元」：最小值、最大值取这些可买行 `price_fen` 的最小与最大，各写成 `compact_money_phrase(fen).removesuffix("元")`（即【E16】的金额写法去掉末尾「元」，例如 1800 写「18」、1830 写「18.3」）；两数相等时照写两遍；N = 0 时为空 | 「，18–95元」 |
| 暂停行 | 「暂停〈标注〉」 | 「暂停微信读书」 |
| 开始行 | 「开始」+ 各条开始请求开始的活动的标注，`start_main_activity` 那条在前，以「、」相连 | 「开始微信」「开始处理消息与收尾、购买美式咖啡」「开始查询食品」 |
| 被拒行 | 「被拒：」+ 各条请求的〈动作对象〉，`start_main_activity` 那条在前，以「、」相连 | 「被拒：查询盖浇饭」 |
| 〈动作对象〉 | 主工具 travel：「前往」+ `target`；主工具 interact：「开始」+ `detail`；主工具 sleep：「睡觉」；副工具 purchase：「购买」+ `target` 各项以「、」相连；副工具 query：「查询」+ 同上；副工具 interact：「开始」+ `target[0]`；report_status：「播报」+ 各项所写活动名的短名以「、」相连；pause_activity：「暂停」+ 同上；conclude_activity：「收尾」+ 同上；revise_day_plan：「改今日计划」。所写名称照请求原文，短名按【E15】的去法作用在原文上 | 「被拒：前往公司」「被拒：开始测试休息、购买牛肉面」「被拒：改今日计划」 |
| 个人事务变更行 | `errand_registered` 写「新增个人事务「〈事务名〉」」，`errand_updated` 写「更新个人事务「〈事务名〉」」，`errand_cancelled` 写「取消个人事务「〈事务名〉」」，事务名取 `data.errand.name` | 「新增个人事务「备好饮料」」 |
| 改计划行 | 「改了今日计划」 | — |
| 跨过零点行 | 〈起〉+ 空格 +「开始」+〈标注〉+「，跨过零点」，只在零点收尾条目里 | 「23:30 开始睡觉，跨过零点」「20:15 开始微信读书，跨过零点」 |
| 兜底行 | report_status：「播报」+ 各项活动的标注以「、」相连；conclude_activity：「收尾」+ 同上 | 「播报微信」「收尾早上洗漱」 |

各行的类别按【E11】（03 章第 6 节「核验行」表）：购买行是 `goods_category(book, 这一行所写已付款订单的 goods_id)`，不是购买活动全部订单的类别；办成行与个人事务变更行是 03 章第 4 节的个人事务规则 `goods_category(book, [data.errand.product_id])`。

【E18】**标题段。** 只有 `kind == "interact"` 的结束行有标题段。标题取活动快照 `data.played` 各项 `content_id` 在冻结目录里的 `title`，按先后，不去重。

| 活动 | 短句的标题段 | 完整行的标题段 |
| --- | --- | --- |
| 在地点做的事（`data.app_id` 为 `null`） | 标题恰好一个且等于这项活动的短名（【E15】，不论这条条目里它的标注是短名还是冻结全名）时，这个标题去掉。剩下的标题不多于 3 条（`TITLE_LIST_LIMIT`，src/worldmock/recording_engine/projections/presentation/display_wording.py:38）时逐条写「《标题》」，多于 3 条时写前 3 条后接「等N条」，N 是剩下的标题总数；没有标题时为空 | 剩下的标题全部逐条写「《标题》」，不截 |
| 用内容 app（`data.app_id` 不为 `null`） | 有放完的标题时写第一个「《标题》」，多于一条时后接「等N条」，N 是放完的条数；没有时为空。没放完的那条不写 | 放完的标题全部逐条写，再按现行 `app_progress` 的判定（src/worldmock/simulation_engine/observation/wording/settled_text.py:97-110）在这项活动确有推进而没放完的内容时接「（《标题》未完）」 |

标题段不为空时，在它前面加「，」接在〈终局段〉之后。例：短句「抖音 12:28–12:58，《豆角焖面｜面条铺在菜上焖熟》等15条」，完整行列出 15 个标题再接「（《X》未完）」；短句「做饭 11:30–12:40，《备菜做饭》《吃饭》《洗碗收拾厨房》，用10月10日收到的板栗烧鸡配清炒娃娃菜一人份食材包」。

【E19】**食材段、完整行与去标题行。**

- 〈食材段〉只在活动身份属于 `ActivityFacts.kits`（08 章；条目取 `cached.kits.uses`，【E42】）时出现，写 `"，" + kit_phrase(kits[activity_id], book.catalog, book.timezone, True)`；短式的文字（「用M月D日收到的〈商品名〉」「用家里原有的食材」）只在 08 章的 `kit_phrase` 里定义。例：「，用10月10日收到的板栗烧鸡配清炒娃娃菜一人份食材包」。完整式「用的是…」属于 settled_text、零点计划和结果投影，不进条目；条目的完整行与去标题行也用短式。
- 每行有三种文字：短句（进渲染形状）、完整行（进全文）、去标题行（核验兜底第 1 步用）。结束行的完整行把短句的标题段换成完整行的标题段，去标题行没有标题段（连同它前面的「，」）；其余各行三种文字相同。

【E20】**核验本身超过 120 字时的兜底。** 记各行短句为 L_1…L_m。

1. Σ len(L_i) ≤ 120 时照写，不做下面两步。
2. 换成各行的去标题行；和 ≤120 时照写。
3. 记尾行 T(N) =「另N项…」，N 是没写出的行数。取最大的 1 ≤ p < m，使前 p 行去标题行的长度之和 + len(T(m−p)) ≤ 120，写这 p 行再接 T(m−p)。没有这样的 p 时（m = 1；或 m > 1 而第一行去标题行的长度加 len(T(m−1)) 已超过 120）：m = 1 写第一行去标题行的前 119 字再接「…」；m > 1 写第一行去标题行的前 (120 − len(T(m−1)) − 1) 字再接「…」，后接 T(m−1)。

V、B 里核验从未超过 120 字（最长 89 和 92 字），这三步只靠单元测试验证。全文（【E33】）总是写全部完整行，不受这一步影响。

### 自述项

【E21】**来源与前缀。** 自述项按下表产生，〈标注〉是这一项所指活动在条目里的标注（【E15】）；文字照请求原文，不去首尾空白。

| 种类 | 来源 | 写法 |
| --- | --- | --- |
| 总结 | 受理的 pause_activity、conclude_activity 每项的 `summary` | 「〈标注〉：」+ summary |
| 进展 | 受理的 report_status 每项的 `summary` | 「进展·〈标注〉：」+ summary |
| 看法 | 受理的 conclude_activity 每项的 `views`（经 `control_items` 读出、`type` 为 relationship 或 preference 的记忆） | 「看法·」+ `subject`（请求里的 `about`）+「：」+ text |
| 记住 | 同一项里 `type` 不是 `routine` 的普通记忆（`plain_memories`） | 「记住：」+ text |
| routine | 同一项里 `type` 是 `routine` 的普通记忆 | 「记住：」+ text，只进全文，从不渲染 |
| 正文 | R_1 的 `data.self_note`（已去首尾空白，src/worldmock/simulation_engine/decisions/decision_coordinator.py:99-127） | 「正文：」+ self_note |

各项的类别按【E11】（03 章第 6 节「自述项」表）：总结、进展、看法、记住、routine 是这一项所指活动的类别，正文是主类别。

【E22】**空白与去重。** `strip()` 之后为空的 summary、记忆或看法文字不产生项（与现行 src/worldmock/simulation_engine/observation/layers/memory_layer.py:293 同一判据）。普通记忆的文字与同一项的 summary 逐字相同时，这条记忆不产生项，渲染和全文里都没有。

【E23】**次序。** 自述项按种类分四档：总结和进展 → 看法 → 记住（routine 在全文里也在这一档）→ 正文；同一档里按请求里的先后：先按项的次序，项内按记忆或看法的次序。渲染只取非 routine 的项。

### 120 字以内的取舍（规则 B）

【E24】**输入与常量。** 规则 B 的输入是核验已占的字数 k（【E20】写出的各行之和，k ≤ 120）和按【E23】排好的可渲染自述项 s_1…s_n。常量：上限 120（`ENTRY_CHARS`）、首轮每项 40 字（`ENTRY_HEAD_CHARS`）、整项给的上限 50 字（`ENTRY_WHOLE_CHARS`）、最少截给 8 字（`ENTRY_MIN_CUT_CHARS`）、标点回退至多 10 字（`ENTRY_BACKOFF_CHARS`）、回补至多 3 遍（`ENTRY_REFILL_PASSES`）、可截的标点「，。；！？、」（`ENTRY_BREAK_MARKS`）。每项有一个给定长度 g_i，初值 0，g_i = 0 表示省略。

【E25】**截断。** 把 s 截成给定长度 g（2 ≤ g < len(s)）：取 body = s 的前 g − 1 字；在 body 里找最后一个可截标点的位置 j；j 存在、j ≥ len(body) // 2、而且 len(body) − j ≤ 10 时，body 改为 body 的前 j 字（标点本身也去掉）；结果是 body +「…」，长度 ≤ g。g ≥ len(s) 时整项照写。

【E26】**第一轮。** R = 120 − k。按次序看每一项：h = len(s_i) ≤ 50 时为 len(s_i)，否则为 40。h ≤ R 时 g_i = h，R 减去 h，看下一项；h > R 时，R ≥ 8 则 g_i = R、R = 0，R < 8 则 g_i = 0；无论哪种，这一项之后的项都 g = 0，第一轮结束。

【E27】**第二轮。** 按次序看每一项：0 < g_i < len(s_i) 且 R > 0 时，加给它 min(R, len(s_i) − g_i)，R 相应减少。

【E28】**显示与补「…」。** 显示列表是按次序把 g_i > 0 的项照【E25】写出；有任何 g_i = 0 的项，而显示列表为空或最后一项不以「…」结尾时，在末尾补一项 `"…"`。

【E29】**标点回补。** 至多做 3 遍：spare = 120 − k − 显示列表各项长度之和；c 是次序最前的 0 < g_c < len(s_c) 的项；spare < 2 或没有这样的 c 时停止；否则 g_c = min(len(s_c), len(截断后的 s_c) + spare)，重算显示列表。

【E30】**收口。** 显示列表使 k + 各项长度之和 > 120 时反复做：没有 g_i > 0 的项（只剩补上的 `"…"`）时去掉这个 `"…"`，自述为空，停止；否则取次序最后的 g_i > 0 的项，over = 超出的字数，g_i = g_i − over，结果小于 2 时 g_i = 0，重算显示列表。核验从不为补「…」缩短。结果就是渲染形状的 `自述`。

【E24】–【E30】的例子（k 是核验字数，「甲」「乙」「丙」「丁」「一」「二」代表任意不含标点的汉字串，下标是长度）：

| k | 自述项 | 渲染的自述 |
| --- | --- | --- |
| 120 | 「睡觉：一二三」 | 无（条目只有核验，不补「…」） |
| 119 | 「睡觉：」+ 一₂₇ | `["…"]` |
| 0 | 一₅₀ | 一₅₀ 整项 |
| 0 | 一₅₁ | 一₅₁ 整项（首轮 40，第二轮补足） |
| 0 | 甲₁₀₀、乙₁₀₀ | 甲₇₉+「…」、乙₃₉+「…」 |
| 112 | 一₃₀ | 一₇+「…」 |
| 113 | 一₃₀ | `["…"]` |
| 80 | 「收尾：」+ 一₂₈ +「，」+ 二₂₀ | 「收尾：」+ 一₂₈ +「…」（标点回退 8 字；回补算出的给定长度与原来相同，没有变化） |
| 0 | 甲₁₀₀、乙₃₀ +「，」+ 丙₆₉ | 甲₈₈+「…」、乙₃₀+「…」（乙回退省下的 9 字回补给甲） |
| 75 | 甲₄₀、乙₃₀ | 甲₄₀、「…」 |
| 80 | 甲₄₀、乙₃₀ | 甲₃₈+「…」（补「…」会到 121 字，缩短甲，甲以「…」结尾后不再补） |
| 0 | 甲₄₅、乙₆₀、丙₆₀、丁₁₀ | 甲₄₅、乙₃₉+「…」、丙₃₄+「…」（丁省略；最后显示的丙以「…」结尾，不再补） |

### 各类决策产生什么

【E31】每次决策恰好一条条目。下表是各类决策条目的组成（「已结算事实」指【E12】在窗口里产生的行，没有时为空；「正文」指 R_1 有 self_note 时的一项；V、B 的次数取自提案）：

| 决策 | V / B | 核验 | 自述 |
| --- | --- | --- | --- |
| 收尾 | 137 / 137 | 已结算事实（含被结束活动的结束行）、个人事务变更行 | 各项总结、看法、记住，正文 |
| 只交 report_status | 124 / 133 | 个人事务变更行 | 各项「进展·〈标注〉：」，正文 |
| report_status，窗口里另有结算 | 12 / 12 | 已结算事实、个人事务变更行 | 进展，正文 |
| 只开始一项 | 77 / 74 | 「开始〈标注〉」 | — |
| 开始组 | 12 / 16 | 「开始A、B」 | — |
| 开始并带正文 | 12 / 13 | 开始行 | 「正文：…」 |
| 开始时窗口里有结算 | 2 / 3 | 已结算事实、开始行 | 正文 |
| 开始购买 | 19 / 16 | 「开始购买〈商品〉」 | 正文 |
| 开始查询 | 6 / 1 | 「开始查询〈范围〉」 | 正文 |
| 只有被拒请求 | 5 / 2 | 已结算事实、「被拒：…」 | 正文 |
| 暂停 | 0 / 1 | 已结算事实、各项「暂停〈标注〉」、个人事务变更行 | 各项总结，正文 |
| 改计划 | 0 / 1 | 已结算事实、「改了今日计划」 | 正文 |

【E32】**兜底行。** 按上表一行核验也没有、可渲染的自述也一项没有时（只在受理的 report_status 或 conclude_activity 的各项 summary 都是空白时出现，经校验的请求不会走到这里），核验写一行兜底行（【E17】）。

### 全文

【E33】**全文的形状。** 条目全文（不渲染，供日回顾输入、检索「今天」和「未整理」时的退路使用）恰为

```json
{"时刻":"HH:MM","类别":"…","核验":["完整行…"],"当时查到":["…"],"自述":["全部项…"]}
```

键依次是 `时刻`、`类别`、`核验`、`当时查到`、`自述`，空列表的键省略。`核验` 是全部行的完整行，不经【E20】；`自述` 是【E23】次序的全部项，含 routine 记忆、看法和正文，不经规则 B；`当时查到` 是各查完行的答案按行序相连。零点收尾条目的全文同样形状，「时刻」是「24:00」，没有 `自述`。

【E34】**当时查到。** 每一行查完行带一组答案：

- 第一项「〈时刻〉 在〈app〉查〈范围〉」：时刻是这项查询 `ended_at` 按【E16】写出的时刻（答案在做完那一秒算出，src/worldmock/simulation_engine/activities/query/query_activity.py:125-135），后接一个空格；没有 app 时写「〈时刻〉 查〈范围〉」。
- 然后答案 `rows` 的每一行按次序各一项「〈商品名〉 〈价钱〉」：商品名后一个空格，价钱按【E16】的金额写法；这一行 `available` 为假时后接「，当时不可买」。
- `omitted` > 0 时最后一项「另M项未列」。

例：`["11:50 在美团外卖查食品","番茄牛肉刀削面 26元","鱼香肉丝盖浇饭 22元，当时不可买","另160项未列"]`。答案只进全文和进度层（06 章），从不进渲染形状。

【E35】**按类别切分。** 一条条目的某一类部分：只留类别等于这一类的行和项，`当时查到` 只留所留查完行的答案，「类别」写这一类，键序与省略规则同【E33】；这一类没有任何行和项时这一部分不存在。日回顾输入（04 章）和指定类别检索「今天」（05 章）用同一切法。

### 零点收尾条目

【E36】**内容。** D 日的零点收尾条目的行是：窗口 (max(C_last(D), B_D), B_{D+1}] 里【E12】的已结算事实，再加上零点时仍在进行的每项本人活动各一行跨过零点行。「零点时仍在进行」的候选是 `cached.snapshots` 里有序号不超过 B_{D+1} 的快照的本人活动（这样的快照发生在 D+1 日 00:00 之前，所以活动已经开始；候选的筛选不抛错），其中同时满足下面两条的才写：结束事件（`cached.endings`）不存在或序号大于 B_{D+1}；在 B_{D+1} 时没有暂停着，即序号不超过 B_{D+1} 的这项活动的 `activity_paused`、`activity_resumed` 环境事件不存在，或其中序号最大的一条是 `activity_resumed`。行里的〈起〉〈标注〉取序号不超过 B_{D+1} 的最后一份快照。零点前已经结束、只等收尾的活动不算，它的结束行已在某条条目里；暂停着的活动不算，它的暂停行已在暂停那次决策的条目里。没有任何行时 D 日没有零点收尾条目。

【E37】**形状与去向。** 零点收尾条目只有全文形状 `{"时刻":"24:00","类别":…,"核验":[…]}`（窗口里有查询做完时另有 `当时查到`），没有自述，不经【E20】和规则 B。它不是任何决策的条目，从不进「今天」，也从不进提示；只进 D 日的日回顾输入（04 章）和按日期检索「未整理」时的退路（05 章）。它排在 D 日各决策条目之后。

【E38】**例子。** 23:30 睡下以后、零点之前送达的外卖记进 D 日的零点收尾条目，写「23:50 送达〈商品〉」（时刻前缀因为与「24:00」不同而总在）；零点之后送达的，记进 D+1 日第一次决策的条目。B 10-10 的零点收尾条目只有「20:15 开始微信读书，跨过零点」，这项活动 00:15 结束，结束行「微信读书 昨20:15–00:15」记进 10-11 第一次决策的条目。跨夜睡眠「睡觉 昨23:30–07:30」记在醒来那天收尾它的那次决策的条目里。

【E39】**前提。** 求 D 日的零点收尾条目和「某一往日全部条目」时，调用方给出此刻 `now`；只有 `now` 不早于 D+1 日 00:00（`day_window(D+1, tz).start`）时才求，B_{D+1} 由 `day_bound(store, D+1, tz, now)` 取得：事件按发生时刻单调追加，时钟到达 D+1 日 00:00 之后不会再有发生在它之前的事件，所以 B_{D+1} 此后不再变化（零点那次推进的提交里不一定有发生在 00:00 的事件，src/worldmock/simulation_engine/scheduling/clock/rhythm.py:212-229 把共同身体结算留到零点计划之后提交，所以前提按时钟判断，不按存储里最后一个事件）。`now` 早于 D+1 日 00:00 时写日志并抛 `IntegrityError("Day entries requested before the day ended")`。05 章的检索传冻结观察的 `occurred_at`，04 章的零点任务传 `context.state.current_time`（零点时刻）。

### 发布、只追加与缓存

【E40】**发布。** 一次观察的「今天」条目是日期等于观察日期、C 不超过截止序号的各次决策的条目，按决策次序；截止序号在构建观察时是 `store.last_event_seq`，在只读检索时是冻结观察的事件序号（05 章）。第 k 次决策一提交，第 k 条就满足这个条件，所以从第 k+1 次决策起可见，包括同秒链里的下一次决策；第 k 次决策自己的观察里没有第 k 条。新快照里「今天」列表的递推见 01 章；同秒链里经 `changes.memory.added` 送达见 06 章。

【E41】**只追加。** 第 k 条的文字只依赖序号 ≤C_k 的事件和冻结目录：活动的事实取窗口上界以内的快照（【E12】），标注只在条目内比较（【E15】），时刻写法只依赖条目日期（【E16】），所以条目一经发布，当天后续每次观察里逐字节不变；只有 01 章的兜底会改写早先的条目。恢复和重放打开新的存储对象、缓存从空开始，按同样的规则重算，得到逐字节相同的条目。

【E42】**缓存。** 本人事件的增量缓存（现 src/worldmock/simulation_engine/observation/layers/memory_layer.py:50-61、:95-96、:165-180，本章把它移到 entries.py）另存：每条动作的第一条 `action_result`（`first_results`）、每次决策的 C_j（`commits`，取代 `lowers`）、按决策身份的已建条目（`entries`）、按日期的零点界（`day_bounds`，只存 `day_bound` 的返回值）与零点收尾条目（`closings`）、各活动按序号的快照（`snapshots`），以及 08 章的食材包推导（`kits`）。C_j、已建条目、某日的零点界在算出之后不再改变；某日的零点收尾条目只在【E39】的前提成立后建一次。缓存只是加速：去掉缓存全量重算，结果逐字节相同。

【E43】**往日条目。** 某一往日 D 的全部条目（04 章的日回顾输入、05 章的「未整理」退路用）是 D 日各次决策的条目按决策次序，最后接 D 日的零点收尾条目（有时）。

### 回看名称与回看去重

【E44】**标注进回看名称。** 观察的 `activity_bindings[].note_labels`（src/worldmock/simulation_engine/observation/layers/observe.py:142-152）来自「今天」：取这次观察渲染出的「今天」列表里每一项 `自述` 的全部字符串（01 章的兜底改写之后，计数行没有 `自述`）；对当天每条已发布条目的每个点名活动（标注 L，活动身份 a），只要有一个字符串以「L：」或「进展·L：」开头，就把 L 记到 a 名下。一个标注因此映射到当天所有用这个标注写过、且这个前缀仍在「今天」里出现的活动（同名的不同活动都在内）。`note_labels` 是标注 → 这些活动在本次观察里的可见名称（`context.names["activities"]`）的元组，按条目先后去重；标注等于可见名称的不记。

【E45】**回看去重。** `recall_task_summary` 回看一项活动、活动有 `progress_note` 时，在冻结观察的记忆层「今天」各项 `自述` 里找恰好等于「L：」+ progress_note 或「进展·L：」+ progress_note 的字符串，L 依次取这项活动的短名和冻结的 `name`；找到时 `_remembered` 照现行形状返回 `{"activity": L, "progress_note": "已在记忆层"}`（src/worldmock/simulation_engine/actions/dispatcher.py:50、:511-518），`_recall_activity` 照旧再接上 `note_history`（:334-336），不写地点、阶段和客观进度。被截断的项以「…」结尾，与 progress_note 不会逐字相等，所以只有整项照写的总结或进展才算「已在记忆层」。

【E46】**本章的英文错误。** 都先以同一句写 `logging.error`，再抛 `IntegrityError`：

| 情形 | 文字 |
| --- | --- |
| 决策有世界请求而没有结果（【E2】） | `Decision world request has no committed result` |
| 中止的决策（【E13】） | `Aborted decision has no today entry` |
| 同一决策的结果有受理也有拒绝（【E13】） | `Decision results disagree` |
| 第一条结果的状态不是受理、拒绝或中止（【E13】） | `Decision result status cannot form a today entry` |
| 受理的开始结果没有活动身份（【E13】） | `Accepted start has no started activity` |
| 控制结果的活动身份数与项数不等（【E13】） | `Control result activities disagree with its items` |
| 结束的活动读不到终态快照（【E12】） | `Ended activity has no terminal snapshot` |
| 条目涉及的活动在窗口上界以内没有快照（【E12】） | `Activity has no snapshot within the entry window` |
| 活动的开始请求没有结果（【E11】） | `Activity has no start result` |
| 活动的冻结名称为空（【E15】） | `Activity has no frozen name` |
| 送达订单的状态不合（【E12】） | `Delivery arrival order status is not delivered or ready_for_pickup` |
| 当天还没结束就求往日条目（【E39】） | `Day entries requested before the day ended` |

### 示例（V 10-12，按本章规则逐字成立）

- 08:00，只开始，4 字：`{"时刻":"08:00","类别":"生活","核验":["开始微信"]}`
- 08:50，播报加送达，112 字：`{"时刻":"08:50","类别":"工作、饮食","核验":["送达美式咖啡"],"自述":["进展·处理消息与收尾：08:34 起在工位上清钉钉里积压的工作消息和评审意见，顺手把待办补了一遍，做了约十五分钟，还剩几分钟就能收完。刚点的冰美式 08:50 已经送到前台，拿到手先喝一口，再把这点尾巴处理干净…"]}`
- 08:56，收尾两件购买、带看法，120 字：`{"时刻":"08:56","类别":"饮食","核验":["买公仔点心面（海鲜味）、高钙牛奶 10盒各1份，共21.76元，预计09:10送到练秋湖研发中心"],"自述":["购买公仔点心面（海鲜味）、高钙牛奶 10盒：08:54 开工的同时用淘宝闪购下单了一份公仔…","看法·公仔点心面（海鲜味）：海鲜味的公仔点心面量小…"]}`
- 10:54:52 同秒链里的第 2 次决策，开始并带正文：`{"时刻":"10:54","类别":"工作","核验":["开始需求评审会议"],"自述":["正文：开发活动已收尾。上午还剩一个钟头左右，正适合开需求评审会，开完刚好接午饭。"]}`
- 11:45，只有被拒，8 字：`{"时刻":"11:45","类别":"饮食","核验":["被拒：查询盖浇饭"]}`
- 11:50，查询做完并开始购买：`{"时刻":"11:50","类别":"饮食","核验":["查完美团外卖·食品：20项可买（另160项未列），18–95元","开始购买番茄牛肉刀削面"]}`

## 接口与文件

行号都在 f67c25e5 上。每个新函数 ≤50 行、≤5 个参数（`self`/`cls` 不计），文档字符串是含中文的 `@brief` 一行；代码、日志和报错用英文。

### 新文件 `src/worldmock/simulation_engine/observation/memory/entries.py`

包 `observation/memory/` 与它的 `__init__.py` 由 01 章建立。

模块说明：

```python
"""@brief 今天条目：每次决策一条，从已提交事件与冻结目录逐条生成（docs/design/06-agent.md「今天条目」）。

第 j 次决策的提交末序号 C_j 由 store.commit_last_seq 求出，第 k 条的窗口是 (max(C_{k-1}, B_D), C_k]；条目文字只依赖窗口
上界以内的事件与冻结目录，发布后当天不再改变（记忆层兜底的改写见 layout.py）。核验短句、短名与标注、自述前缀、120 字
以内的取舍（规则 B）、核验超过 120 字时的兜底、条目全文与按类别切分、回看标注都只在这里定义。本人事件的增量缓存也在
这里，按存储对象与人物持有，换成新的存储对象时从头读取。
"""
```

常量：

```python
#: 每条条目「核验」与「自述」各字符串 len 之和的上限。
ENTRY_CHARS = 120
#: 规则 B 第一轮每项先给的字数。
ENTRY_HEAD_CHARS = 40
#: 第一轮整项照给的长度上限。
ENTRY_WHOLE_CHARS = 50
#: 截给一项的最少字数（含「…」）。
ENTRY_MIN_CUT_CHARS = 8
#: 截断处向前退到标点时至多舍去的字数（含标点）。
ENTRY_BACKOFF_CHARS = 10
#: 标点回补的遍数上限。
ENTRY_REFILL_PASSES = 3
#: 截断处可以退到的标点。
ENTRY_BREAK_MARKS = "，。；！？、"
#: 截断与省略的记号（U+2026）。
ELLIPSIS = "…"
#: 零点收尾条目的「时刻」。
CLOSING_CLOCK = "24:00"
#: 四种自述前缀。
PROGRESS_PREFIX, VIEW_PREFIX, MEMORY_PREFIX, BODY_PREFIX = "进展·", "看法·", "记住：", "正文："
#: 核验行的种类闭集。
LINE_KINDS = ("ending", "purchase", "delivery", "pickup", "errand_done", "query", "pause", "start", "rejection",
              "errand_change", "revise", "fallback", "crossing")
#: 自述项的种类闭集与渲染次序档位（routine 只进全文）。
SELF_RANKS = {"summary": 0, "progress": 0, "view": 1, "memory": 2, "routine": 2, "body": 3}
```

类型：

```python
@dataclass(slots=True)
class AgentEvents:
    """@brief 一个人物已读取的事件（进程内加速缓存）与按决策缓存的今天条目。"""

    last_seq: int = 0
    events: dict[str, list[Event]] = field(default_factory=lambda: {name: [] for name in AGENT_EVENT_TYPES})
    seqs: dict[str, list[int]] = field(default_factory=lambda: {name: [] for name in AGENT_EVENT_TYPES})
    endings: dict[str, Event] = field(default_factory=dict)
    #: 当前部分观看只由本人活动的认证进度事件派生，完成后清除。
    app_progress: dict[str, str] = field(default_factory=dict)
    #: 活动身份 → [(事件序号, 快照)]，取自本人 action_result 携带的活动快照，按序号升序。
    snapshots: dict[str, list[tuple[int, ActivityState]]] = field(default_factory=dict)
    #: 动作身份 → 这条动作的第一条 action_result（受理、拒绝或中止那一刻）。
    first_results: dict[str, Event] = field(default_factory=dict)
    #: 决策身份 → C_j。
    commits: dict[str, int] = field(default_factory=dict)
    #: 决策身份 → 已建的今天条目。
    entries: dict[str, "TodayEntry"] = field(default_factory=dict)
    #: 日期 → B_D（day_bound 的返回值，03 章）。
    day_bounds: dict[str, int] = field(default_factory=dict)
    #: 日期 → 零点收尾条目（没有时为 None）。
    closings: dict[str, "TodayEntry | None"] = field(default_factory=dict)
    #: 食材包的增量推导（08 章）。
    kits: KitTrack = EMPTY_KIT_TRACK


#: 存储对象 → 人物 → 已读事件与已建条目；换成恢复后的新存储对象时从头读取（现 src/worldmock/simulation_engine/observation/layers/memory_layer.py:95-96 移来）。
_CACHE: WeakKeyDictionary[FootprintStore, dict[int, AgentEvents]] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class Decision:
    """@brief 一次决策：身份、决策时刻（首条世界请求的发生时刻）与提交末序号 C_j。"""

    decision_id: str
    occurred_at: int
    commit: int


@dataclass(frozen=True, slots=True)
class EntryLine:
    """@brief 一行核验：种类、短句、完整行、去标题行、类别与来源事件身份；查完行另带「当时查到」。"""

    kind: str
    text: str
    full: str
    bare: str
    category: str
    event_ids: tuple[str, ...]
    answers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelfItem:
    """@brief 一项自述：种类、带前缀的全文、类别、来源事件身份与所指活动（正文没有）。"""

    kind: str
    text: str
    category: str
    event_ids: tuple[str, ...]
    activity_id: str | None


@dataclass(frozen=True, slots=True)
class TodayEntry:
    """@brief 一条今天条目或零点收尾条目：日期、决策、时刻、类别串、各行、各项、主请求、点名活动与渲染形状。"""

    day: str
    decision_id: str | None
    clock: str
    category: str
    lines: tuple[EntryLine, ...]
    items: tuple[SelfItem, ...]
    #: 主请求的工具名；零点收尾条目为 None。
    tool: str | None
    #: 受理的控制请求按项次序点名的活动身份；其余为空。
    activity_ids: tuple[str, ...]
    #: 条目内的（标注, 活动身份），按首次出现的先后。
    labels: tuple[tuple[str, str], ...]
    #: 各行（按行序）再各项（按项序）的来源事件身份依次相连、按首次出现去重（【E7】）。
    event_ids: tuple[str, ...]
    #: 渲染形状（【E8】）；零点收尾条目为 None。
    shown: dict | None
```

`AGENT_EVENT_TYPES = ("action_requested", "action_result", "environment_event")`：缓存读的本人事件类型。现行的 `memory_updated`（src/worldmock/simulation_engine/observation/layers/memory_layer.py:38）只为 `_reflections` 读，08 章删除反思以后不再读。`activity_progress` 照现行只用于结束事件和部分观看，不存列表。

公开函数：

| 签名 | 行为 |
| --- | --- |
| `agent_events(store: FootprintStore, agent_id: int, states: frozenset[str]) -> AgentEvents` | 现行 `_agent_events`（src/worldmock/simulation_engine/observation/layers/memory_layer.py:165-180）移来，按 `after` 增量读取，缓存在 `_CACHE`；同一增量步骤里依次：`cached.kits = track_kits(fresh["environment_event"], fresh["action_result"], cached.kits, states)`（08 章）；新读到的每条 `action_result` 按序号 `first_results.setdefault(event.action_id, event)`；其中每份活动快照（`data.activity` 与 `data.data.activities` 各项，去掉 `content_title` 后 `ActivityState.model_validate`）按序号追加进 `snapshots`。`states` 由调用方传 `inputs.catalog.kit_states()`（观察路径是 `context.catalog.kit_states()`） |
| `successful_requests(cached: AgentEvents, tool: str) -> list[Event]` | 本人这个工具的全部受理请求（最新一条结果 `completed`），即现行 `_successful(…, (0, None))`；`persona_views` 改用它 |
| `activity_facts(cached: AgentEvents) -> ActivityFacts` | 见下文「私有函数」一段后的定义；`memory_view`、06 章续接与 04 章 `count_lines` 共用 |
| `decision_commits(store: FootprintStore, cached: AgentEvents, cutoff: int) -> tuple[Decision, ...]` | 【E1】【E2】：只取组内最小事件序号不超过 cutoff 的决策，按次序排列；`commits` 里没有的才调用 `store.commit_last_seq`。序号大于 cutoff 的 `action_requested` 不构成决策，也不查它的结果 |
| `build_entry(book: CategoryBook, store: FootprintStore, cached: AgentEvents, decision: Decision, lower: int) -> TodayEntry` | 窗口 (lower, decision.commit] 上的决策条目（【E7】–【E32】）；lower 由调用方按【E4】给出；结果存进 `cached.entries` |
| `build_closing_entry(book: CategoryBook, cached: AgentEvents, day: str, window: tuple[int, int]) -> TodayEntry \| None` | 【E36】【E37】；window 是 (max(C_last(D), B_D), B_{D+1}) |
| `fit_verified(lines: Sequence[EntryLine]) -> list[str]` | 【E20】 |
| `fit_items(verified_chars: int, items: Sequence[str]) -> list[str]` | 【E24】–【E30】，纯函数 |
| `short_name(name: str) -> str` | 【E15】；空串抛 `Activity has no frozen name` |
| `entry_full(entry: TodayEntry, category: str \| None = None) -> dict \| None` | category 为 None 时是【E33】的全文；否则是【E35】的这一类部分，不存在时为 None |
| `published_entry(entry: TodayEntry) -> PublishedEntry` | 01 章 layout.py 的 `PublishedEntry`：`item = entry.shown`，`report_only = entry.tool == "report_status" and bool(entry.activity_ids)`，`reported = frozenset(entry.activity_ids)`（report_only 时，否则空集），`concluded = frozenset(entry.activity_ids)`（`entry.tool == "conclude_activity"` 时，否则空集）；被拒的控制请求 `activity_ids` 为空 |
| `note_labels(entries: Sequence[TodayEntry], today: Sequence[dict], names: Mapping[str, str]) -> dict[str, tuple[str, ...]]` | 【E44】；today 是渲染出的「今天」列表，names 是 `context.names["activities"]` |

私有函数按行的种类拆分（结束行、标题段、购买行、送达行、查完行与答案、被拒行、个人事务变更行、自述项、标注、类别串、截断、回补），名称由实现定，各自 ≤50 行。公开的 `activity_facts(cached: AgentEvents) -> ActivityFacts` 恰为 `ActivityFacts(pause_intervals(cached.events["environment_event"]), cached.endings, cached.app_progress, cached.kits.uses)`（现行 `_build` 的事实部分，src/worldmock/simulation_engine/observation/layers/memory_layer.py:155-162，加上 08 章的第四个字段）：条目的结束行读其中的暂停区间、部分观看与食材包；`MemoryView.facts`（交给进度层）、06 章的同秒续接与 04 章的 `count_lines` 也只经它取 `ActivityFacts`。类别只调用 03 章 categories.py 的 `memory_book(store, inputs)`、`purchase_goods(activity, start_result)`、`activity_category(book, activity, goods_ids)`、`goods_category(book, goods_ids)`、`rejected_category(book, store, request)`、`request_category(book, store, request, result, activity_categories)`、`primary_request(requests)`、`category_label(primary, parts)`（【E10】【E11】）。两个构建函数的入参是 03 章的 `CategoryBook`（它带 `catalog`、`timezone`、`home_id`，条目要的冻结输入都在其中）、存储（只为 `rejected_category` 读决策观察）与缓存，没有 `LayerContext` 和 `WorldState`，所以观察、零点任务与检索都能调用，且条目构建读不到世界状态。

### `src/worldmock/simulation_engine/observation/memory/days.py`（03 章建立，本章加两个函数与一个类型）

```python
@dataclass(frozen=True, slots=True)
class PublishedToday:
    """@brief 一次观察的今天：已发布的决策条目（按决策次序）与已发布界 P。"""

    entries: tuple[TodayEntry, ...]
    published: int
```

| 签名 | 行为 |
| --- | --- |
| `published_today(inputs: EnvironmentInputs, store: FootprintStore, agent_id: int, moment: int, cutoff: int) -> PublishedToday` | 【E4】【E5】【E40】：`book = memory_book(store, inputs)`，`cached = agent_events(store, agent_id, inputs.catalog.kit_states())`，today = moment 的本地日期，B_today = `cached.day_bounds` 里已有的值，否则 `day_bound(store, today, inputs.config.timezone, moment)` 存入；`decision_commits(store, cached, cutoff)` 里日期为 today、C ≤ cutoff 的决策逐次按【E4】求窗口下界，`cached.entries` 里没有的用 `build_entry` 建；published = max(C ≤ cutoff 的最后一次决策的 C（没有时为 0），B_today) |
| `past_day_entries(inputs: EnvironmentInputs, store: FootprintStore, agent_id: int, day: str, now: int) -> tuple[TodayEntry, ...]` | 【E39】【E43】：先按 `now` 查前提；B_day 与 B_{day+1} 照上行经 `cached.day_bounds` 与 `day_bound(..., now)` 取；那天的决策取 `decision_commits(store, cached, B_{day+1})` 里日期为 day 的，按【E4】建条目，最后接零点收尾条目（`build_closing_entry` 的结果缓存在 `cached.closings`，有时才接） |

days.py 导入 entries.py，entries.py 经 03 章 categories.py 推导类别；entries.py 不导入 days.py（B_D 由调用方传入窗口），不成环。entries.py、categories.py 及它们的传递导入都不导入 progress_layer.py、memory_layer.py、day_plan_inputs.py（03 章「days.py」一节的导入限制）。

### `src/worldmock/foundation/time/readable.py`（改）

本章新增四个函数（「M月D日」只在 `month_day_phrase` 里写；08 章的 `kit_phrase` 与改写后的 `clock_phrase` 调用它），模块说明（:1-12）末尾加一段：「今天条目的核验另用紧凑写法：金额与份数的数字后紧接单位（「18.3元」「1份」），时刻相对条目日期写「HH:MM」「昨HH:MM」或「M月D日HH:MM」；这几种写法也只在这里定义。」

```python
def month_day_phrase(epoch: int, timezone_name: str) -> str:
    """@brief 场景本地日期「M月D日」，月与日不补零，例如「10月10日」。"""
    moment = _local(epoch, timezone_name)
    return f"{moment.month}月{moment.day}日"


def compact_money_phrase(fen: int) -> str:
    """@brief 今天条目里的金额：至多两位小数、去掉末尾零，数字后紧接「元」，例如「18.3元」。"""
    return f"{_decimal(fen / 100)}元"


def compact_portion_phrase(quantity: float) -> str:
    """@brief 今天条目里的份数：数字后紧接「份」，例如「1份」「0.5份」。"""
    return f"{_decimal(quantity)}份"


def entry_clock(epoch: int, day: str, timezone_name: str) -> str:
    """@brief 今天条目里的时刻：与条目日期 day 同一天写「HH:MM」，前一天写「昨HH:MM」，其余写「M月D日HH:MM」。"""
```

`entry_clock` 的函数体：`moment = _local(epoch, timezone_name)`；`moment.date().isoformat() == day` 时返回 `f"{moment:%H:%M}"`；`moment.date() == date.fromisoformat(day) - timedelta(days=1)` 时返回 `f"昨{moment:%H:%M}"`；否则返回 `f"{month_day_phrase(epoch, timezone_name)}{moment:%H:%M}"`。readable.py 的 `from datetime import datetime`（:15）改为 `from datetime import date, datetime, timedelta`。

### `src/worldmock/simulation_engine/observation/layers/memory_layer.py`（改）

本章只动下面几处，记忆层的组装归 01 章。

- `MemoryView`（:41-47）的字段恰为 `layer: dict`、`facts: ActivityFacts`、`note_labels: dict[str, tuple[str, ...]]`、`published: int`，次序如此（基线三个字段不变，末尾加 `published`，注释「已发布界 P = max(C_last, B_today)（【E5】）」；进度层怎样读它见 06 章）。
- `memory_view` 取今天条目恰为：`today = published_today(context.inputs, store, context.agent_id, context.state.current_time, store.last_event_seq)`；01 章的 `entries` 是 `tuple(published_entry(entry) for entry in today.entries)`；`MemoryView.facts` 是 `activity_facts(agent_events(store, context.agent_id, context.catalog.kit_states()))`；`MemoryView.note_labels` 是 `entries.note_labels(today.entries, layer["今天"], context.names["activities"])`，在 01 章组装出 `layer` 之后求；`MemoryView.published` 是 `today.published`。
- `noted_label` 改为：

```python
def noted_label(layer: dict, activity: ActivityState) -> str | None:
    """@brief 这项活动的最新自述若以整项照写的总结或进展留在记忆层「今天」里，返回它所用的标注，否则为 None；回看据此
    不重复正文（docs/design/06-agent.md「今天条目」）。"""
```

  函数体按【E45】：`note = activity.progress_note`；`texts` 是 `layer["今天"]` 各项 `自述` 的全部字符串（没有这个键的项跳过）；依次试 `short_name(activity.name)` 与 `activity.name`，返回第一个使 `f"{label}：{note}"` 或 `f"{PROGRESS_PREFIX}{label}：{note}"` 在 `texts` 里的标注。
- `persona_views`（:127-144）只把读取改成 `cached = agent_events(store, context.agent_id, context.catalog.kit_states())` 与 `successful_requests(cached, "conclude_activity")`，计算、形状与文档字符串的其余部分归 07 章。
- 删除（被 entries.py 取代或不再有读者）：`_EVENT_TYPES`（:37-38）、`_AgentEvents`（:50-61）、`_Build`（:64-71）、`_Line`（:74-80）、`_Entry`（:83-92）、`_CACHE`（:95-96，移到 entries.py）、`_note_labels`（:116-124）、旧 `noted_label`（:147-152）、`_build`（:155-162）、`_agent_events`（:165-180，移到 entries.py）、`_session_ranges`（:183-190）、`_lower`（:193-197）、`_slice`（:200-205）、`_successful`（:208-213，移到 entries.py）、`_today_entry`（:216-222）、`_day_entries`（:225-237，旧形状 `earlier_days` 的构建，连同其中读反思的循环）、`_session_lines`（:240-257）、`_request_lines`（:260-270）、`_world_lines`（:273-280）、`_self_notes`（:283-296）、`_labels`（:299-303），以及随之不再使用的导入（`progress_layer`、`START_TOOLS`、`ActivityKind`、`SessionStatus`、`SessionState`、`START_SUFFIX`、`iso_time`、`session_lower`、`bisect_right`，以及 settled_text 里除 `ActivityFacts`（`MemoryView.facts` 的类型）以外的名称）。`_reflections` 的删除归 08 章，`MEMORY_WINDOW_CHARS`、`_fit`、`_layer`、`_render`、`_size` 的删除归 01 章。

### `src/worldmock/simulation_engine/actions/dispatcher.py`（改）

- `_remembered`（:511-518）的取值改为 `label = memory_layer.noted_label(observed["memory_layer"], activity)`，不再读 `names`；文档字符串改为「@brief 活动的最新自述以整项照写的总结或进展留在本轮记忆层「今天」里时的回看结果：只写它在「今天」里的标注与「已在记忆层」，不写其他回看事实；否则为 None（docs/design/06-agent.md「今天条目」）。」
- `_recall_names`（:499-505）的文档字符串改为「…本轮活动名与记忆层「今天」自述前缀所写的活动标注（note_labels）。」；`_recall_activity`（:326-331）文档字符串里「自述仍在记忆层时」改为「最新自述以整项照写留在记忆层「今天」里时」。代码不变。

### `custom/agents/life_agent.py`（改，只改文档字符串）

- `_visible_names`（:765-773）文档字符串里「记忆层 self_notes 所写的活动名（活动绑定的 note_labels）」改为「记忆层「今天」自述前缀所写的活动标注（活动绑定的 note_labels）」。
- `_note_labels`（:827-828）文档字符串改为「@brief 活动绑定的 note_labels：记忆层「今天」里「标注：」「进展·标注：」前缀所写的活动标注（只作回看名称）。」

### 不改的文件

- `src/worldmock/simulation_engine/observation/wording/settled_text.py`：完整句照旧供零点计划和进度层浓缩使用；条目不调用 `activity_sentence`、`purchase_sentence`、`record_lines`、`ending_lines`、`tool_record`，只复用 `activity_endings`、`pause_intervals`、`app_progress` 与 `ActivityFacts`。食材包句的改动归 08 章。
- `src/worldmock/simulation_engine/observation/layers/observe.py`：`_name_bindings`（:142-152）照旧读 `memory.note_labels`；P 的传递归 06 章。
- `src/worldmock/recording_engine/storage/store.py`：`commit_last_seq`、`last_event_seq_before` 不改。

## 设计文档

文档、实现和行为测试同一次提交（docs/INDEX.md:18）。

### `docs/design/06-agent.md`「进度、待办与记忆」

在 01 章新增的「记忆层的形状、上限与兜底」之后新增小节 `### 今天条目`，取代 :273-277 两段（「记忆层的结束句写活动名、起止（含暂停）和终局……它不计入 K。」）。改后写明：

- 每次决策恰一条条目，任何世界请求都结束决策（含 report_status、revise_day_plan 与被拒的请求），只读查询不算；条目的渲染形状、键序与空键省略（【E8】【E9】），主请求、主类别与类别串（【E10】【E11】）；
- C_j、B_D、窗口、已发布界的定义（【E2】–【E5】），一个事实恰进 D 日的一条条目（【E6】）；
- 已结算事实的四类来源和不产生行的事件（【E12】），本次决策自己的行（【E13】），行序（【E14】）；
- 短名与标注（【E15】）；条目里时刻、金额、份数的紧凑写法（【E16】），并链到 [readable.py](../../src/worldmock/foundation/time/readable.py)；
- 核验短句表（【E17】的全部行，逐字）、标题段表（【E18】）、食材段与三种文字（【E19】），核验超过 120 字时的三步（【E20】）；
- 自述的五种前缀与 routine 只进全文、空白与去重、次序（【E21】–【E23】）；
- 120 字以内的取舍（规则 B）的七个常量和【E25】–【E30】的步骤，「120 字只数核验和自述各字符串的 len」；
- 各类决策的条目表（【E31】）与兜底行（【E32】）；
- 全文与「当时查到」的形状（【E33】【E34】），按类别切分（【E35】）；
- 零点收尾条目：窗口、跨过零点的判定、「24:00」、只有全文、从不进提示（【E36】–【E39】）；
- 发布从下一次决策起、包括同秒链；只追加；恢复重算逐字节一致；缓存不改变结果（【E40】–【E43】）；
- 回看名称来自「今天」的前缀，回看去重只认整项照写的总结或进展（【E44】【E45】）；
- 条目构建只读已提交事件与冻结输入，不读世界状态，代码在 [entries.py](../../src/worldmock/simulation_engine/observation/memory/entries.py)。

同一节里另改三处：

- :265 一句改为：「答案作为带入记录留到这段停留结束；人物出发后这条记录离开进度层，记忆层「今天」只有「查完〈app〉·〈范围〉」一行摘要，答案进条目全文的「当时查到」，当天可用 search_memory 查「今天」。」（删去「和进度层的其他记录一样」。）
- :279-280 的「记忆层与零点计划的购买句不写渠道」改为「今天条目与零点计划的购买句不写渠道」；这两行其余文字不变。
- 「零点计划」表 :201「动作结果」一行的内容改为「回顾时段里本人已结算的公开动作结果、送达和结束的活动，写成 settled 句的完整写法（今天条目的核验另用短句，见「今天条目」）」。

:267-271（进度层的段与带入）归 06 章改写；06-agent.md 里「回顾」「近三天」「search_memory」的段落归 03、04、05 章。

### 其余设计文档

- docs/design/09-io-formats.md「提示层的机器形状」里今天条目的一行（01 章建表）写：条目是封闭对象，键 `时刻`（`HH:MM`）、`类别`（类别串）、可选 `核验`、`自述`，二者至少有一，各是至少一项的非空字符串数组；渲染时键序为时刻、类别、核验、自述；全文另有 `当时查到`，不进提示。
- docs/manual/：用户操作不变，不改。

### 起规范作用的模块说明

entries.py（新）的模块说明见上；readable.py:1-12 加一段。src/worldmock/simulation_engine/observation/layers/memory_layer.py:1 用 01 章给的那一句，本章在句末补上自己的部分，全句为：

```python
"""@brief 记忆层的门面：按决策组装「可查」「近三天」「今天」并执行字数上限（组装与兜底在 observation/memory/layout.py），
另给人设看法证据与回看去重；「今天」的条目每次决策一条、从下一次决策起可见，由 observation/memory/entries.py 生成。"""
```

## 测试

所有新测试都有中文文档字符串；断言英文错误时逐字照抄，并用 `caplog` 断言同文字的错误日志。真实引擎的用例用现有夹具 `engine_factory`（tests/engine_fixture.py:48-51）、`tests.life.conftest`（`context`、`do_at`、`use_app`、`purchase`、`query`、`travel`、`sleep`、`report_request`、`pause_request`、`conclude_request`、`errand_add`、`errand_update`、`errand_cancel`、`advance_until_ready`、`activity_name`）、`tests.life.environment_runtime`（`act`、`act_group`、`observe_only`、`finish_activity`、`until_ended`、`events`）与 `tests.life.observation_record.record_observations`。夹具环境是 2026-01-05（周一）08:00 在家。取条目的辅助函数 `_today(engine)` 调 `published_today(engine.context.inputs, engine.context.store, 1, engine.context.state.current_time, engine.context.store.last_event_seq)`。需要正文的用例照 tests/unit/life/test_current_lifecycle_coverage.py:154-172 的写法，在登记请求之前用 `engine.record_model_exchange` 记一对模型请求与响应，响应的 `choices[0].message.content` 是正文。

### 新文件 `tests/fixtures/today-entry-rule-b.json`

由研究目录 out3/entries/v_1012_entries.json 一次生成后提交，89 行，每行 `{"verified_chars": <int>, "items": [...], "shown": [...]}`：`verified_chars` 是这一行「条目」里「核验」各串长度之和（即规则 B 的 k；V 10-12 的 89 行里它与「全文」的核验字数相同）；`items` 是「全文」的「自述」，先把匹配 `^(.+?)进行中：` 的项改写成 `进展·\1：`，再按「看法·」1、「记住：」2、「正文：」3、其余 0 稳定排序；`shown` 是「条目」的「自述」（没有时为空列表），做同样的改写。

### 新文件 `tests/unit/life/test_today_entry_fit.py`（纯函数）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_rule_b_vectors`，按【E30】下例子表参数化 12 例 | `fit_items(k, items)` 逐字节等于表里的渲染结果 | E24–E30 |
| `test_rule_b_reproduces_the_measured_v_entries` | 夹具每一行 `fit_items(verified_chars, items) == shown` | E24–E30 |
| `test_items_up_to_fifty_characters_are_whole_in_the_first_pass` | 50 字的项在 R = 50 时整项，51 字的项在 R = 50 时得 40 再于第二轮补到 50 截断 | E26、E27 |
| `test_a_cut_needs_eight_characters_of_room` | R = 8 截成 7 字加「…」；R = 7 整项省略并补「…」 | E26、E28 |
| `test_cut_moves_back_to_a_mark_only_within_ten_characters_in_the_second_half` | len(body) − j = 10 时回退、= 11 时不回退；j < len(body)//2 时不回退；六个标点逐个生效，「·」「：」不生效 | E25 |
| `test_refill_gives_saved_characters_to_the_first_cut_item` | 例子表里「甲₁₀₀、乙₃₀ +「，」+ 丙₆₉」一例：照常得甲₈₈+「…」、乙₃₀+「…」；`monkeypatch.setattr(entries, "ENTRY_REFILL_PASSES", 0)` 时得甲₇₉+「…」、乙₃₀+「…」（合计 111） | E29 |
| `test_the_closing_ellipsis_never_shortens_verified_lines` | k = 120 时任何自述都得空列表；k = 119 时任何长度 ≥2 的单项自述都得 `["…"]` | E28、E30 |
| `test_verified_lines_within_120_are_kept_as_they_are` | 三行合计恰 120 字照写 | E20 |
| `test_verified_backstop_first_drops_title_lists` | 两行带标题段、合计 130 字，去标题后 ≤120，得两行去标题行，没有「另N项」 | E20 |
| `test_verified_backstop_keeps_whole_lines_and_counts_the_rest` | 去标题后长度 60、50、30：得前两行加「另1项…」；长度 60、50、30、25 得前两行加「另2项…」 | E20 |
| `test_verified_backstop_cuts_a_first_line_that_alone_is_too_long` | 单行 130 字得前 119 字加「…」；118、10 两行得第一行前 115 字加「…」与「另1项…」，合计 120 | E20 |
| `test_short_name_strips_one_start_suffix_and_one_leading_jinxing` | 「进行处理消息与收尾（10月12日 08:34 起）」→「处理消息与收尾」；「进行进行曲练习（1月5日 08:00 起）」→「进行曲练习」；「驾车前往练秋湖研发中心（10月12日 08:03 起）」→「驾车前往练秋湖研发中心」；`short_name("")` 抛 `Activity has no frozen name` | E15 |

### `tests/unit/life/test_readable.py`（加两个测试）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_compact_money_and_portion` | `compact_money_phrase(1830) == "18.3元"`、`(2176) == "21.76元"`、`(1700) == "17元"`；`compact_portion_phrase(1) == "1份"`、`(0.5) == "0.5份"`（`month_day_phrase` 的测试归 08 章） | E16 |
| `test_entry_clock_is_relative_to_the_entry_day` | 条目日期 2026-01-05：同日「08:50」，前一日「昨22:08」，更早「1月3日23:00」，次日「1月6日00:20」；跨月的前一日（2026-02-01 的条目里 1 月 31 日的时刻）写「昨」 | E16 |

### 新文件 `tests/unit/life/test_today_entry_events.py`（存根事件：经校验的请求或夹具环境走不到的分支）

用 tests/unit/life/test_memory_progress_contract.py:34-70 同样的夹具输入与存根存储（另给存根实现 `last_event_seq`、`commit_last_seq`、`last_event_seq_before`），手写事件。

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_blank_summaries_fall_back_to_the_request_action`，参数化 report_status 与 conclude_activity | 受理的控制请求两项 summary 都是「 」：条目只有核验一行「播报A、B」或「收尾A、B」，没有自述 | E22、E32 |
| `test_a_failed_ending_and_an_oversleep_are_written_as_outcomes` | 终态 `failed` 的活动写「，失败」；完成且 `oversleep_seconds = 750` 的睡眠写「，多睡12分钟」 | E17 |
| `test_decision_without_a_result_fails_closed` | 抛 `Decision world request has no committed result` | E2、E46 |
| `test_aborted_decision_fails_closed` | `failed` 且 `DECISION_ROUND_ABORTED` 抛 `Aborted decision has no today entry` | E13、E46 |
| `test_disagreeing_and_unknown_results_fail_closed` | 一受理一被拒抛 `Decision results disagree`；第一条结果 `cancelled` 抛 `Decision result status cannot form a today entry` | E13、E46 |
| `test_accepted_start_without_activity_and_short_control_ids_fail_closed` | 抛 `Accepted start has no started activity`；两项而 `activity_ids` 一个抛 `Control result activities disagree with its items` | E13、E46 |
| `test_missing_terminal_or_window_snapshot_fails_closed` | 结束事件在窗口里而快照仍是 `active` 抛 `Ended activity has no terminal snapshot`；收尾结果的 `activity_ids` 指向一项窗口上界以内没有任何快照的活动抛 `Activity has no snapshot within the entry window`；快照的 `origin_action_id` 没有任何 `action_result` 抛 `Activity has no start result` | E11、E12、E46 |
| `test_an_unnamed_activity_fails_closed` | 快照 `name == ""` 抛 `Activity has no frozen name` | E15、E46 |
| `test_a_delivery_with_an_unknown_order_status_fails_closed` | 订单 `status == "pending"` 抛 `Delivery arrival order status is not delivered or ready_for_pickup` | E12、E46 |
| `test_settled_lines_ignore_system_events_and_world_records` | 窗口里只有 `balance_decreased`、`app_push`、`callback_reminder`、`errand_reminded` 与可见性为 system 的 `delivery_arrival`：没有已结算行（取代 test_memory_progress_contract.py:108 对 `_world_lines` 的断言） | E12 |
| `test_closing_entry_skips_paused_and_already_ended_activities` | 1 月 5 日的三项活动：23:40 暂停、零点后才恢复的；23:50 结束、零点后才收尾的；零点时进行中的。1 月 5 日零点收尾条目的跨过零点行只有第三项一行「…开始…，跨过零点」；另一例只有前两项，23:50 的结束事件在 23:55 一次决策的窗口里（零点收尾窗口里没有已结算事实）：`build_closing_entry` 返回 None；从没暂停过的活动与暂停后已恢复的活动都按进行中写行 | E36 |
| `test_a_crossing_activity_that_spans_two_midnights_gets_a_line_each_night` | 1 月 4 日 23:00 开始、跨过 1 月 5 日与 6 日两个零点的活动：两天的零点收尾条目各有「…开始…，跨过零点」，第二天写「昨23:00 开始…」 | E36、E17 |
| `test_two_same_short_names_in_one_entry_keep_their_full_names` | 一次收尾的两项是冻结名称为「进行哔哩哔哩（1月5日 08:00 起）」与「进行哔哩哔哩（1月5日 08:20 起）」的两项活动，两者都在这个窗口里结束：两行结束行与两项总结都用完整名称作标注；另一条只点名其中一项的条目用短名「哔哩哔哩」 | E15 |
| `test_day_entries_before_the_day_ended_fail_closed` | `past_day_entries(..., "2026-01-05", now)`：`now` 是 1 月 5 日 23:59:59 时抛 `Day entries requested before the day ended`；`now` 恰为 1 月 6 日 00:00:00 而存储里最后一个事件发生在 1 月 5 日 23:50 时照常返回（不按存储里最后一个事件判断） | E39、E46 |

### 新文件 `tests/integration/life/test_today_entries.py`（真实引擎）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_each_decision_publishes_one_entry_from_the_next_decision` | 三次决策（开始测试休息、收尾、开始哔哩哔哩）：第 k 次决策的观察里 `memory_layer["今天"]` 恰有 k−1 项，最后一项是第 k−1 次决策的条目；`_today(engine).entries` 与之逐项对应（`shown`） | E1、E40 |
| `test_a_same_second_chain_decision_sees_the_previous_entry` | 同一秒先收尾、再开始：第二次决策的观察「今天」末项是收尾条目 | E40 |
| `test_window_holds_clock_settled_facts_and_the_own_commit` | 开始查询柠檬茶后推进到它做完（查询自己写下 `concluded_at`，不挡后面的开始，tests/life/day_baseline_runtime.py:73-77），在做完那一刻的决策开始哔哩哔哩：开始哔哩哔哩的条目核验恰为「查完〈app〉·柠檬茶：N项可买，P–Q元」「开始哔哩哔哩」，〈app〉、N、P、Q 写成夹具运行的字面值 | E4、E12、E14 |
| `test_published_bound_is_the_previous_commit_or_the_midnight_bound` | 第二次决策观察时 `published` 等于第一次决策的 `commit_last_seq`；跨过零点后的第一次观察 `published` 等于 `last_event_seq_before(次日 00:00)`；`_today(engine).published` 与 `memory_view(...).published` 相等 | E5 |
| `test_a_world_request_registered_after_the_cutoff_is_not_yet_a_decision` | 收尾一次之后：`begin_decision_round`、`observe`，再 `submit_action` 登记一条 conclude_activity 而不结算；以这次观察事件的 `occurred_at` 与 `event_seq` 调 `published_today`：不抛错，`entries` 与登记之前逐项相同（不含这次决策），`published` 相同；随后 `commit_decision_round` 结算，下一次观察的「今天」末项是这次收尾的条目 | E1、E2、E40 |
| `test_twelve_decision_kinds_have_pinned_entries`，按【E31】表的 12 行参数化 | 每种决策下一次观察「今天」的末项逐字节等于测试里写死的 JSON 字面量：夹具运行是确定的，名称、时刻与金额都照字面写出，测试不调用被测函数、也不按【E17】【E21】重新拼出期望。例：只开始一项 `{"时刻":"08:00","类别":"生活","核验":["开始测试休息"]}`；只交 report_status（`report_request` 缺省总结「继续。」）`{"时刻":…,"类别":…,"自述":["进展·哔哩哔哩：继续。"]}`；暂停（`pause_request` 缺省总结「先停一下。」）`{"时刻":…,"类别":…,"核验":["暂停哔哩哔哩"],"自述":["哔哩哔哩：先停一下。"]}`；改计划用 `ReviseDayPlanRequest`，核验 `["改了今日计划"]`、类别「生活」；开始并带正文的期望末尾是「正文：…」 | E31 |
| `test_ending_lines_of_the_fixture_activities`，参数化：按计划结束、被收尾提前结束、app 被收尾（不写「结束使用」）、出行到达（「步行到公司…」）、隔夜睡眠（「睡觉 昨…」）、暂停后恢复（「（暂停 …）」） | 结束行逐字节等于期望 | E17 |
| `test_title_lists_in_short_and_full_lines` | 沿用 test_autoplay_display.py 的五集副本与四集「做晚饭」：app 收尾短句「哔哩哔哩 08:00–08:30，《测试剧集第一集》等4条」，完整行列四集再接「（《测试剧集第五集》未完）」；放完的短句「…等5条」、完整行五集没有「未完」；「做晚饭」短句「…《备菜做饭》《吃晚饭》《洗碗收拾厨房》等4条」，完整行四集全列；单集与短名同名的「测试休息」不写标题 | E18、E19 |
| `test_purchase_lines`，参数化：一件送到家、两件各一份经饿了么、份数不同、到店购买、付款分钟与条目时刻不同 | 购买行逐字节等于期望，例如「买柠檬茶1份，17元，预计08:25送到家」；不带下单 app；购买活动没有结束行 | E12、E17 |
| `test_delivery_and_pickup_lines` | 外卖送达写「送达〈商品〉」（时刻不同时带前缀）；平台笔记本到驿站写「平台笔记本已到取货点待取」 | E12、E17 |
| `test_query_line_and_its_answers` | 查「柠檬茶」做完：下一次决策的条目核验有「查完〈app〉·柠檬茶：N项可买，P–Q元」，渲染形状里没有店名与答案行；`entry_full` 的「当时查到」第一项是「HH:MM 在〈app〉查柠檬茶」，各答案行「柠檬茶 17元」或「，当时不可买」；查询提前结束时写「查询柠檬茶 …，提前结束」 | E17、E33、E34 |
| `test_errand_lines` | report_status 带 `errand_add`：核验「新增个人事务「…」」；之后 `errand_update`、`errand_cancel` 分别写「更新个人事务「…」」「取消个人事务「…」」；买到所需商品后写「办成「…」」 | E12、E13、E17 |
| `test_rejection_lines`，参数化：查询不存在的商品、开始组被拒、`ReviseDayPlanRequest` 没有计划时被拒、收尾不存在的活动 | 被拒行逐字节为「被拒：查询…」「被拒：开始…、购买…」「被拒：改今日计划」「被拒：收尾…」，不带原因 | E13、E17 |
| `test_line_order_puts_settled_facts_first` | 08:00 在家只开始购买柠檬茶（送到家）；购买做完时收尾它并带 `errand_add("备好饮料", "柠檬茶")`；收尾之后同一秒的下一次决策（同秒续接）在家 `sleep(60)`，测试先断言这一刻早于订单的 `expected_at`；外卖在睡着时送达（src/worldmock/simulation_engine/scheduling/clock/effects.py:137：睡着不唤醒），同一次推进提交里写下送达与 `errand_completed`；醒来收尾睡眠并带 `errand_add("买纸巾", "纸巾")`。这次收尾的条目核验恰为四行，依次是「HH:MM 送达柠檬茶」、「办成「备好饮料」」（两行按来源事件序号）、睡眠的结束行「睡觉 HH:MM–HH:MM」（它的结束事件序号更大），然后本次决策自己的「新增个人事务「买纸巾」」；各时刻写成夹具运行的字面值。已结算事实在开始行之前由 `test_window_holds_clock_settled_facts_and_the_own_commit` 钉住 | E14 |
| `test_self_items_follow_prefix_order_and_routine_stays_in_the_full_text` | 收尾一项带两条看法、一条 commitment、一条 routine 与正文：渲染的自述依次是「〈标注〉：总结」「看法·…」「看法·…」「记住：commitment」「正文：…」，没有 routine；全文的「自述」在「记住：commitment」之后是「记住：routine」；与 summary 相同的记忆两处都没有 | E21–E23、E33 |
| `test_category_string_lists_the_main_category_first` | 在家开始测试休息并购买牛肉面的开始组：「类别」以测试休息的类别开头，后接饮食 | E10、E11 |
| `test_closing_entry_collects_facts_after_the_last_decision_and_crossing_lines` | `mutate` 让运行从 1 月 5 日 23:00 开始：23:10 买一份外卖（预计 23:50 前送到），23:30 睡下；跨过零点后 `past_day_entries(..., "2026-01-05", engine.context.state.current_time)` 最后一条是 `{"时刻":"24:00",…,"核验":["23:50 送达…","23:30 开始睡觉，跨过零点"]}`（送达时刻按实际），1 月 6 日醒来收尾睡眠那次决策的条目有「睡觉 昨23:30–…」 | E6、E36–E38 |
| `test_closing_entry_never_enters_the_prompt` | 同上运行，零点后每次观察的 `render_layer_json(memory_layer)` 不含「24:00」与「跨过零点」，第一次观察「今天」为空 | E37 |
| `test_entries_are_append_only_within_a_day` | `record_observations` 记下 tests/life/day_baseline_runtime.py 活动日的全部观察：同一天里前一次观察的「今天」总是后一次的前缀 | E41 |
| `test_a_recorded_day_has_one_entry_per_decision_within_120_characters`，参数化活动日与回调日 | `play_complete_day` 跑完后，`past_day_entries(..., "2026-01-05", engine.context.state.current_time)` 的决策条目数等于那天的决策数（零点收尾条目不计），每条渲染形状「核验」「自述」各串长度之和 ≤120，且都有「核验」或「自述」 | E8、E31 |
| `test_a_fresh_store_rebuilds_byte_identical_entries` | 同一 footprints.sqlite 新开一个 `FootprintStore` 对象（缓存为空），`published_today` 与原对象的 `entries` 逐项 `shown`、`entry_full` 相同，`published` 相同 | E41、E42 |
| `test_recall_dedup_only_counts_untruncated_text` | 播报一句 30 字的进展后回看：回看结果的 `activity` 是「哔哩哔哩」、`progress_note` 是「已在记忆层」；播报一句 200 字的进展（渲染被截断）后回看得到完整的 `progress_note` | E45 |
| `test_a_short_label_maps_to_every_same_named_activity_of_the_day` | 一天里两次收尾不同的「测试休息」：活动绑定里两项活动的 `note_labels` 都含「测试休息」，按「测试休息」回看得 `TASK_NOT_FOUND`（歧义） | E44 |

### 要改的现有测试

| 测试 | 改法 | 条款 |
| --- | --- | --- |
| tests/integration/life/test_query_answer.py::test_answer_stays_for_the_stay_and_leaves_a_settled_line_after_departure（:301） | :301-317 的进度层断言不变；:318-319 改为：到达后「今天」里有查询的查完行「查完…·柠檬茶：…」，渲染的记忆层不含「一心面馆」，`entry_full` 的「当时查到」含答案；文档字符串同改 | E17、E34 |
| tests/integration/start_interfaces/test_purchase_prefer.py::test_settled_purchase_sentence_keeps_its_wording_without_the_channel（:536） | :545-547 改为：先断言 `order.paid_at` 所在分钟等于含这条购买行的条目的「时刻」（购买在做完那一秒付款，src/worldmock/simulation_engine/commerce/purchase/storefront_runtime.py:61-127，收尾它的决策在同一秒），再断言这条条目的核验里恰有「买柠檬茶1份，17元，预计08:25送到家」；:548 不带「饿了么」照旧 | E17 |
| tests/integration/start_interfaces/test_location_activities.py::test_memory_sentence_keeps_the_unfinished_half_only_for_app_content（:527-545） | 从「今天」取这项活动的结束行：在地点做的事以「，提前结束」结尾；app 的短句没有「结束使用」「提前结束」也没有「未完」，`entry_full` 的完整行以「（《测试慢跑》未完）」结尾；改名为 `test_unfinished_half_is_only_in_the_full_line_of_app_content`，tests/dataset_acceptance.py 里变异 `concluded_app_use_ends_early` 的杀死测试同步改成新名字 | E17、E18 |
| tests/integration/life/test_autoplay.py::test_a_concluded_app_use_reads_end_of_use（:437-460） | 收尾结果的 `outcome` 仍是「结束使用」；:456-460 改为：结束行不写「结束使用」，完整行以「（《测试慢跑》未完）」结尾 | E17、E18 |
| tests/integration/life/test_autoplay_display.py::test_settled_sentence_lists_three_titles_the_count_and_the_unfinished_item（:225-241）与 ::test_settled_sentence_of_a_place_series_over_three_episodes_lists_three_and_the_count（:243-266） | 期望改成【E18】的短句与完整行（与新测试 `test_title_lists_in_short_and_full_lines` 同值），从「今天」与 `entry_full` 取 | E18 |
| tests/integration/life/test_carried_records.py::test_departure_and_purchase_records_are_not_carried（:229）与 ::test_activity_finishing_at_the_arrival_second_is_told_by_the_memory_layer（:283） | 记忆层一半：购买行与 Keep 的结束行在它们之后那次决策的条目里（`observe_only` 时尚未发布），断言改为在下一次决策之后的「今天」里找短句「买平台笔记本1份，…」与「Keep 08:00–…，《测试慢跑》」；进度层一半归 06 章 | E4、E40 |
| tests/unit/life/test_memory_progress_contract.py::test_balance_decrease_displays_only_declared_loss_and_never_enters_memory（:102） | 删去 :108 的 `memory_layer._world_lines` 断言，由新测试 `test_settled_lines_ignore_system_events_and_world_records` 覆盖；进度层断言不变 | E12 |
| tests/unit/life/test_current_lifecycle_coverage.py::test_activity_recall_is_readonly_and_returns_real_progress_and_note_history（:245） | :258 改为恰好等于 `"已在记忆层"`，并断言 `recall["activity"] == "哔哩哔哩"`（播报「看了一段内容。」整项照写） | E45 |

tests/unit/life/test_shared_observation_contract.py:225-239（看法进「今天」）由 07 章改写；记忆层旧形状的其余读者（test_narration.py:96、test_decision_context_protocol.py:198、test_life_agent_protocol.py:197、test_compare_runs.py:132、:313-318）随 01、09 章改写。

### 门禁

- 公开接口零未覆盖：entries.py、days.py 本章的函数由上面的测试直接调用；readable.py 本章的四个新函数中 `compact_money_phrase`、`compact_portion_phrase`、`entry_clock` 由上面的测试直接调用，`month_day_phrase` 由 08 章 `test_month_day_phrase_does_not_pad` 直接调用（10 章跨章裁定）。
- 分支覆盖：【E46】的每条错误、【E20】的三步、【E25】–【E30】的每个分支、【E32】、【E36】的三个判定（结束、暂停、进行中，`test_closing_entry_skips_paused_and_already_ended_activities`）、【E1】的截止序号（`test_a_world_request_registered_after_the_cutoff_is_not_yet_a_decision`）各有测试；没有占位实现。
- 风格门禁（tests/unit/engineering/test_style_gate.py）：entries.py 不超过 2000 行；每个函数 ≤50 行、≤5 个参数（`self`/`cls` 不计），每个函数有含中文的 `@brief` 文档字符串。

## 不在本章

- 记忆层的整体形状、「今天」列表的递推、14,000 字上限与三步兜底、层序、记忆层 Schema 的代码（01、07 章）。
- 规则 0–7 的类别推导、`MEMORY_CATEGORIES`、零点计划的归日窗口与「近三天」（03 章）。
- 日、月、年回顾的输入编号、调用与校验（04 章）；检索「今天」与「未整理」退路的结果形状与上限（05 章）。
- 进度层的已发布界与带入记录、`changes.memory.added`、兜底改写时断链（06 章）。
- 系统层 rules[1] 对条目的说明文字（07 章）。
- 「活动 → 所用食材包」映射本身、settled_text 与结果投影的食材包句、反思与巩固的退役（08 章）。
- 把「今天」拆成每条一个观察分件（提案「存储与事件记录」写明可选、不在提案内）。
- 真实模型试点（总规格全局决定 2），查看器与分析工具（09 章），版本与重冻（10 章）。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 「与短名相同的单个标题去掉」怎样读 | 只在标题恰好一个且等于这项活动的短名时去掉（条目里同名活动改用冻结全名作标注时也按短名比较）；多个标题时照列 | 字面是「单个」；V 的在地点做的事只有单标题或三集系列，三集系列不含单元名（只读核对 V 的 played），两种读法在 V、B 上结果相同 |
| 完整行的标题怎样写 | 在地点做的事列出全部剩下的标题（同一去法，不截）；app 列出全部放完的标题再接「（《X》未完）」 | 提案第 7 点「app 那一行写全部放完的标题，再接（《X》未完）；系列写全部集数的标题」 |
| 「等N条」的 N 与空格 | N 是总条数，数字两边不留空格（「等15条」） | 提案例「等15条」；display_wording.title_list 的 K 也是总条数 |
| 紧凑写法放在哪里 | readable.py 新增 `month_day_phrase`、`compact_money_phrase`、`compact_portion_phrase`、`entry_clock`；「M月D日」只在 `month_day_phrase` 里写，08 章的 `kit_phrase` 与 `clock_phrase` 调用它（10 章跨章裁定：此前 02、08 两章互指对方新增它）；查完行的价段用 `compact_money_phrase(fen).removesuffix("元")` | readable.py:1-12 写明全仓短语只有这一套口径；一个规则一处；`_decimal` 是私有函数，价段经公开的金额写法取数字 |
| 预计送达在条目日期之后怎样写 | 与更早的日期同写「M月D日HH:MM」 | 提案表只列了更早的写法；`entry_clock` 只依赖条目日期，保持只追加 |
| 句首时刻前缀的格式 | 时刻加一个空格 | 提案的跨过零点句「HH:MM 开始〈短名〉，跨过零点」 |
| 多件份数不全为 1 | 只有份数全为 1 时写「各1份」，其余逐件写「A2份、B1份」（全为 2 也逐件写） | 提案两行只区分「同为 1 份」与「份数不同」；测量脚本 entries.py 的写法 |
| 一次购买有几个预计时刻或目的地 | 预计时刻按【E16】写出后去重，按时刻升序；目的地按订单次序首次出现去重；各以「、」相连 | 提案未写；V、B 的多件单子都只有一个时刻和地点，测量脚本（按字符串和地点身份排序，out3/entries/entries.py:344-347）与这一写法在 V、B 上结果相同 |
| 一个送达事件里的商品重名与两种状态 | 商品名按先后去重；送达行在待取行之前，各一行 | 提案两种写法各自成句；去重避免「送达A、A」 |
| 「N项可买」数什么 | 可买行涉及的不同商品件数；价段即使最低等于最高也写「X–X元」；N 为 0 时不写价段；没有 app 时不写「〈app〉·」；「当时查到」第一项没有 app 时写「〈时刻〉 查〈范围〉」 | `omitted` 数的是商品件数（src/worldmock/simulation_engine/activities/query/query_answer.py:94-106），两者同一单位；价段照提案字面；query_app 在没有任何 app 卖所查商品时返回 None（src/worldmock/simulation_engine/activities/query/query_activity.py:61-62），提案的查完行与「当时查到」写法都以有 app 为前提 |
| 「范围」从哪里取 | 查询活动短名去掉开头的「查询」 | 查询活动名即「查询」加各项规范名称（src/worldmock/simulation_engine/activities/query/query_activity.py:118-120），不另引私有函数 `_target_name` |
| 「当时查到」的时刻 | 查询的 `ended_at` | 答案在做完那一秒算出（src/worldmock/simulation_engine/activities/query/query_activity.py:125-135）；提案「标查询时刻」 |
| 被拒的 revise_day_plan 写什么 | 「被拒：改今日计划」 | 提案的动作清单没有改计划，而 revise_day_plan 会被拒（src/worldmock/simulation_engine/actions/dispatcher.py:395-410 的 PLAN_NOT_FOUND 等） |
| 被拒行的对象与次序 | 照请求原文；控制请求取所写活动名的短名；开始组主槽位在前 | 提案「被拒：〈动作〉〈对象〉」与「主请求取槽位 1」 |
| 开始组两项的次序 | 主槽位在前 | 提案示例「开始处理消息与收尾、购买美式咖啡」；主请求取槽位 1 |
| 暂停多项 | 每项一行「暂停〈标注〉」 | 测量脚本的写法；与「一次购买一行」同为一事一行 |
| 个人事务变更行与购买行的类别 | 个人事务变更行按 03 章第 4 节的个人事务规则，取事务快照的 `product_id`；购买行取这一行所写已付款订单的商品类别 | 03 章第 6 节核验行表把办成与新增、更新、取消同列为个人事务规则，购买行写「这次购买已付款各订单的商品」；03 章第 6 节写明本章两句改为引用该节 |
| 空白自述与原文 | `strip()` 后为空的不产生项；产生的项照原文，不去首尾空白 | 现行 src/worldmock/simulation_engine/observation/layers/memory_layer.py:293；请求校验要求非空白，原文照写使回看去重能与 `progress_note` 逐字比较 |
| 「与总结逐字相同的记忆」比什么 | 只与同一项的 summary 逐字比较，渲染和全文都去掉 | 提案「与总结逐字相同的记忆去掉」；记忆属于这一项 |
| 中止的决策 | 抛 `Aborted decision has no today entry` | 中止后运行失败，不再有观察，恢复从中止之前的检查点分支；不留降级分支（AGENTS.md） |
| 名称为空的活动 | 抛 `Activity has no frozen name`，不取 `_kind_name` | 所有开始路径都给活动命名（src/worldmock/simulation_engine/actions/start_group.py:281-289、src/worldmock/simulation_engine/activities/content/interact.py:61、src/worldmock/simulation_engine/activities/query/query_activity.py:119、src/worldmock/simulation_engine/commerce/purchase/storefront_runtime.py:156）；不做回退 |
| 结束事实从哪里读 | 本人 `action_result` 携带的活动快照，窗口上界以内最后一份；不读 `WorldState` | 终态结果总带完整快照（evidence/actions.py:68-102）；只追加与恢复重算由此成立；05 章的检索不读世界状态 |
| 核验兜底第 3 步的单位 | 整行保留；尾行写「另N项…」，N ≥10 时按尾行实长计；第一行本身放不下时截它 | 「另N项」数的是行；V、B 从未触发，这一支只靠单元测试 |
| 标点回退的「后一半」与「10 字以内」 | j ≥ len(body)//2，len(body) − j ≤ 10（body 是「…」之前保留的部分，舍去的字含标点） | 与测量脚本 cut_text 相同，89/89 复现 V 10-12 的测量结果 |
| 回补的公式 | g_c = min(len, 截断后长度 + 余量)，余量 < 2 停止，至多 3 遍 | 测量脚本 cap_b 的写法；保证回补后不超过 120。按这个公式第一遍之后余量不再改变结果（10 万组随机输入核对），提案的「最多 3 遍」照写为上限，测试只钉住回补本身 |
| 零点收尾条目与 120 字 | 只有全文形状，不做取舍；有查询做完时带「当时查到」 | 它从不进提示；全文形状对查询条目带答案 |
| 零点时暂停着的活动 | 不写跨过零点行 | 提案「零点时仍在进行」；暂停行已在暂停那次决策的条目里，活动列在待办层 |
| 往日条目的前提 | 调用方给出此刻 `now`；`now` 早于 D+1 日 00:00 时求 D 日的零点收尾条目或往日全部条目抛错；`past_day_entries` 加参数 `now`，05 章传冻结观察的 `occurred_at`，04 章传零点时刻 | B_{D+1} 在那之前还会变化；零点那次推进的提交里不一定有发生在 00:00 的事件（src/worldmock/simulation_engine/scheduling/clock/rhythm.py:212-229 留下共同身体结算），按存储里最后一个事件判断会让零点的日回顾误报；03 章 `day_bound` 同样按 `now` 判断 |
| 缓存放在哪里 | 本人事件缓存从 memory_layer.py 移到 entries.py（`AgentEvents`、`agent_events`、`_CACHE`）；`lowers` 换成 `commits`；另存 `first_results` 与 08 章的 `kits`，`agent_events` 加参数 `states`；不再读 `memory_updated` | memory_layer.py 成为门面（提案第三章代码表）；days.py、05 章检索都要读缓存，留在门面里会成环；`lowers` 只服务按 session 归档；`memory_updated` 的唯一读者是 08 章删除的 `_reflections` |
| 零点界与往日条目放在哪里 | 零点界只用 03 章的 `day_bound(store, day, timezone, now)`，本章不另定义；days.py 加 `PublishedToday`、`published_today`、`past_day_entries`；entries.py 不导入 days.py | 提案第三章代码表把零点界、零点收尾条目放在 days.py；全仓零点界一处定义（03 章）；单向导入避免成环 |
| 类别串里算不算 routine | 算全部行与全部项（含 routine），渲染与全文共用一个类别串 | routine 记忆跟着项所指活动归类，与总结同类；一个类别串让全文切分与渲染一致 |
| 回看标注的映射 | 标注 → 当天所有以这个标注写过且前缀仍在「今天」里的活动的可见名称；`noted_label` 签名改为 `(layer, activity)` | 提案「一个短名映射到当天所有同名的活动」；回看去重要同时试短名和冻结全名，需要活动本身 |
| 本章依赖的他章接口 | 类别只调用 03 章 categories.py 的 `memory_book`、`purchase_goods`、`activity_category(book, activity, goods_ids)`、`goods_category`、`rejected_category`、`request_category(book, store, request, result, activity_categories)`、`primary_request`、`category_label`（【E10】【E11】）；零点界调用 03 章 `day_bound`；`PublishedEntry` 的字段照 01 章 layout.py（`item`、`report_only`、`reported`、`concluded`）；食材包照 08 章 `KitTrack`、`track_kits`、`kit_phrase`、`ActivityFacts.kits`；`MemoryView.published` 由本章给出、06 章读取；两个构建函数的签名是 `build_entry(book, store, cached, decision, lower)` 与 `build_closing_entry(book, cached, day, window)` | 条目只读事件与冻结输入，不读 `WorldState`；05 章检索不读世界状态；03 章的这些函数都不取 `WorldState`，`CategoryBook` 带着条目要的目录、时区与住处；每个函数 ≤5 个参数，`build_closing_entry` 不读决策观察，所以不带存储 |
| 截止序号与已登记未结算的请求 | 决策只算组内最小事件序号不超过截止序号的组，`decision_commits` 带参数 `cutoff` | 取数结果只依赖截止序号以内的事件：检索的截止序号是冻结观察的 `event_seq`（05 章【S5】），往日条目取 `decision_commits(store, cached, B_{day+1})`（`past_day_entries`），构建观察与 06 章的上一次决策取 `store.last_event_seq`；检索与世界请求不能同批（05 章【S3】，custom/agents/life_agent.py:883-894），不会出现先登记世界请求、后检索的情形；引擎上登记而未结算的请求（`test_a_world_request_registered_after_the_cutoff_is_not_yet_a_decision`）不过滤时【E2】会抛错 |
| 条目涉及的活动读不到快照时的错误 | 本章在读快照处抛 `Activity has no snapshot within the entry window`；03 章第 6 节把这一情形指向本章的这条错误，`Memory category names an activity that was not read` 只用于 03 章第 7 节 `request_category` 在 `activity_categories` 里找不到身份的情形 | 快照由本章【E12】读取，读不到时在读取处失败；本章先读快照、再按【E11】把条目涉及的全部活动放进 `activity_categories`，所以经本章调用时 03 章那条错误不会先于本章的错误出现 |
| 开始结果从哪里取 | 缓存 `first_results`（动作身份 → 第一条 `action_result`），活动的开始结果是 `first_results[origin_action_id]`，没有时抛 `Activity has no start result` | 03 章 `purchase_goods` 要这项活动开始请求的第一条 `action_result`；【E13】的 F_i 也是各请求的第一条结果，同一份缓存给两处用 |
| 零点收尾条目的候选活动 | `snapshots` 里有序号不超过 B_{D+1} 的快照的本人活动，筛选不抛错；「开始早于 D+1 日 00:00」由此蕴含 | 序号不超过 B_{D+1} 的事件都发生在 D+1 日 00:00 之前；从没暂停过的活动没有暂停、恢复事件，按「不存在或最后一条是恢复」判定为没有暂停着 |
| `MemoryView` 的字段 | 恰为 `layer`、`facts`、`note_labels`、`published`；`facts` 由公开的 `entries.activity_facts(cached)` 构造（10 章跨章裁定，取代本章此前「删除 `facts`、`published_bound`」的写法） | 基线 `MemoryView` 已有 `layer`、`facts`、`note_labels`（memory_layer.py:41-47），提案「`memory_view` 另返回 P_k」只加一个字段；进度层要同一次观察的 `ActivityFacts.kits`（06 章【P1】【P9】、08 章），续接与 04 章 `count_lines` 也要 `ActivityFacts`，一个公开构造函数供三处使用 |
| 记忆层 Schema 里的条目 | 本章只写形状要件（【E8】、09-io-formats 的一行），Schema 代码由 01、07 章写在 prompt_schemas.py | 01、07 章都给出了 `MEMORY_LAYER_SCHEMA` 的代码，条目只写一处 |
| 条目各行、各项的来源事件身份 | 见【E7】末段：每行取产生它的事件，本次决策的行取请求与第一条结果，自述项取所属请求 | 提案「宿主内部另外保存…来源事件身份」（:210）未写各种行取哪些事件；04 章日回顾的 `source_event_ids` 与 06 章【P8】的不相交检查读它，后者只容许带入记录的来源（请求、第一条结果、终态结果）与条目共用 |
