# 03 类别、归日与近三天

> 属于[分层记忆开发规格](../SPEC-layered-memory.md)。代码基线 f67c25e5。

## 目标与依据

本章实现提案 docs/dev/PROPOSAL_2026-10-29_LAYERED_MEMORY.md 的以下部分（行号指提案原文）：

| 提案位置 | 本章落实的内容 |
| --- | --- |
| 第二章「类别」（:338-371） | 类别闭集与模块位置；规则 0–7；边界情形；被拒请求、revise_day_plan、零点收尾条目的类别；自述、看法、正文的继承；目录字段 `memory_category`（目录格式 5→6 归第 08 章） |
| 第二章「归日规则」（:373-381） | 共享的归日函数；零点界；零点计划的昨日窗口改用同一个函数并统一成左闭右开；四个测试时点 |
| 第二章「今天条目」第 1 点（:209-210） | 「类别」串：主请求的类别打头，其余按首次出现的次序以「、」相连；每一行、每一项各自的类别 |
| 第二章「今天条目」第 2 点（:214） | 零点界 B_D 的定义 |
| 第二章「分级」（:396、:399） | 「近三天」的取法：饮食和生活取日历上的前三天，工作取最近三个有工作记录的日子；头三天只有部分回顾 |
| 第二章「裁剪与兜底」第 1 条（:187） | 一格「省略」与一格「未整理」是两种情形 |
| 第二章「设计原则」第 2 条（:103） | 全系统只用一条归日规则 |
| 第三章「代码」表（:742、:744、:745、:750） | state/model/memory.py、resource_engine/catalog/models.py、新包的 categories.py 与 days.py、day_plan_inputs.py 的归日 |
| 第三章「设计文档」表（:778、:779） | 03-resource-engine.md 的 `memory_category`（格式 6 的文字归 08）；04-simulation-engine.md 的归日规则 |
| 第三章「测试与门禁」（:819、:820、:827） | 包 0001 每一行的推导类别；归日的四个时点；「近三天」在同一天内逐字节不变 |

本章落实的裁定：

| 裁定 | 落实 |
| --- | --- |
| 4（选项 A） | 三类闭集 `("饮食", "工作", "生活")`，由代码从目录和事件推导；睡眠归生活；关系方面的看法跟着收尾时点名的那项活动归类，不设第四类 |
| 5（选项 A） | 通勤归工作（规则 4） |
| 6（选项 A） | 可选的 `memory_category` 覆盖字段；目录格式 5→6、环境包重建与 LabelKit 的同期改动归 08 |
| 10（选项 A） | 「近三天」：饮食和生活按日历取，工作取最近三个有工作记录的日子 |
| 16（选项 A） | 「近三天」格子的键用中文 |
| 23（选项 A） | 「未整理」格子的含义：那天这一类的回顾没整理出来，不等于那天没有这类事 |

依据的事实（docs/dev/research/layered-memory-2026-10-29/brief.md）：F13（没有任何记录带类别；规则推导能把 76–80% 的决策干净归到一类；`office-eat-lunch` 没有食品信号；食材包的商品类别是 `goods`；235/235 条收尾能连回唯一活动；`ActivityState.plan_goal_intent_kind` 没有写入方）、F14（30 天日历；10-08 往前三天全是假期）。

## 行为

### 1. 类别闭集

- 记忆的类别恰为三类，次序固定：饮食、工作、生活。这个次序是「近三天」的键序、回顾输入与输出的类别次序、检索按类别分组的次序，也是测试里一切按类别排列的次序。
- 定义只有一处：src/worldmock/state/model/memory.py，与 `SUBJECTIVE_TYPES`（:23-24）并列。recording_engine 只能从 `worldmock.state` 引用共享枚举（tests/unit/engineering/test_import_graph.py:382-394 的 `ENGINE_BOUNDARIES`），所以闭集放在 `worldmock.state`。
- 条目、行与项的类别是派生值：不进 `AgentState`、`ActivityState`，也不写进任何事件正文（回顾事件 `memory_digest_generated` 按 `food`、`work`、`life` 键存放各类回顾，归 04），不改世界版本 15。每次需要时由第 2–8 节的函数从冻结目录、人物声明和已提交事件重新算出，不读 `WorldState`；同样的输入得到同样的类别。
- 不复用零点计划的 `intent_kind`（src/worldmock/state/planning/schedule.py:58-70），`ActivityState.plan_goal_intent_kind`（src/worldmock/state/world/activity.py:53）也不读。

### 2. 推导用到的冻结输入

推导只读下面这些。它们由 `category_book(inputs)` 从 `EnvironmentInputs`（src/worldmock/configuration/inputs/environment.py:28-41）一次算出，放进 `CategoryBook`：

| 字段 | 取值 |
| --- | --- |
| `catalog` | `inputs.catalog` |
| `schedule` | `inputs.persona.work_schedule`，可为 None |
| `timezone` | `inputs.config.timezone` |
| `home_id` | `inputs.home_location_id` |
| `workplace_id` | `inputs.workplace_location_id`，没有工作安排时为 None（environment.py:153-173 按显示名解析） |
| `workplace_kind` | `catalog.locations[workplace_id].kind`；`workplace_id` 为 None 时为 None。下文称「班次地点类型」 |
| `food_goods` | 食品商品的身份集合，见下 |
| `food_names` | `food_goods` 里各商品的 `name`，按商品身份升序 |
| `food_units` | 食品单元里全部地点内容的身份集合，见下 |
| `categories` | 启用的类别，见第 8 节 |

定义：

- **食品状态。** 食品状态的集合恰为 `book.catalog.food_states()`：判定条件只在第 08 章 `Catalog.food_states()` 定义，本章调用它，不另写。包 0001 里 `groceries`（家里做饭三集都凭它，第 2 集写了 `food`）和 `team_dinner`（同事聚餐凭它并写了 `food`）是食品状态，`detergent`（洗衣凭它，不补能）不是。
- **食品商品。** 商品 G 是食品商品，当且仅当 `G.category` 属于 `FOOD_GOODS_CATEGORIES = frozenset({"meal", "snack", "drink"})`，或 G 出现在 `catalog.food_states()` 中某个状态的 `reset_on_goods` 里。包 0001 的 22 种 `kit-dinner-*` 食材包（类别 `goods`）因此是食品商品；洗衣液（`item-detergent`）不是。
- **单元。** 地点内容（`location_kinds` 非空）所在的单元与 `location_units`（src/worldmock/simulation_engine/activities/content/location_activities.py:40-51）同一口径：不属于系列的内容自成一个单元；属于系列的内容，单元是 `catalog.contents_by_series[content.series.title]` 的全部各集。系列名在全部内容里唯一，各集共用同一个容器绑定（src/worldmock/resource_engine/registration/admission.py:238-253 的系列检查）。
- **食品单元。** 一个单元是食品单元，当且仅当其中某一集的 `completion_effects.food` 不为 None，或这个单元凭的状态（各集相同）是食品状态。整套做饭三集（包括只洗碗、不补能的那一集）都是同一个食品单元。

### 3. 一项活动的类别：规则 0–7

`activity_derivation(book, activity, goods_ids)` 对人物的一项活动给出 `Derivation(category, rule)`；`activity_category` 只取其中的类别。

**输入。**
- `activity` 是 02【E12】读法得到的活动快照：本人 `action_result` 事件携带的快照去掉 `content_title` 后按 `ActivityState` 读出，取序号不超过调用方窗口上界的最后一份。推导只用开始时就定下、此后不变的字段（`kind`、`started_at`、`location_id`、`destination_id`，`data` 的 `app_id`、`targets`、`order_ids` 与开始内容），所以同一项活动的任何一份快照得到同一类别。
- `goods_ids` 对购买活动是 `purchase_goods(activity, start_result)`：`start_result` 是这项活动开始请求（`action_id == activity.origin_action_id`）的第一条 `action_result` 事件；`placed_orders(start_result.data["data"])`（src/worldmock/recording_engine/projections/commerce/action_orders.py:29-32）里 `order_id` 属于 `activity.data["order_ids"]` 的各订单的 `goods_id`，按订单身份升序。购买活动开始时 `data["order_ids"]` 就是这次请求的全部订单（src/worldmock/simulation_engine/commerce/purchase/storefront_runtime.py:149-161），开始结果经 `activity_orders` 带着这些订单的快照（src/worldmock/simulation_engine/actions/start_group.py:299-300）。`activity.data["order_ids"]` 里有身份不在这些订单里时，写英文错误日志并抛 `IntegrityError("Purchase activity names an order that is not in its start result")`。对其余种类 `purchase_goods` 返回 `()`，`goods_ids` 不被读取。
- 推导不读 `WorldState`。

**判定时点与判定内容。**
- 时刻一律取活动开始的时刻 `activity.started_at`，日期 d 是 `date.fromisoformat(local_date(activity.started_at, book.timezone))`（`local_date` 给出 YYYY-MM-DD 串，src/worldmock/foundation/time/readable.py:35-37；`shift_on`、`duty_status` 要 `datetime.date`，src/worldmock/configuration/persona/work_schedule.py:34-65）。不看结束时刻，也不看与在岗时段重叠多少。
- 地点一律取 `activity.location_id`（活动开始时的地点；出行是出发地）和 `activity.destination_id`（出行的目的地）。
- interact 活动的「开始内容」由 `start_content_id(activity)` 给出：`activity.data["played"]` 非空时是其中第一项的 `content_id`，否则是 `activity.data["content_id"]`。一条内容放完才会接下一条（src/worldmock/simulation_engine/activities/content/interact.py:174-190），所以放完的第一条就是开始的那一条；没放完任何一条时 `content_id` 仍是开始的那一条。自动接续换了内容，类别不变。
- 当天的班次 `shift = work_schedule.shift_on(book.schedule, d)`（src/worldmock/configuration/persona/work_schedule.py:34-39），`book.schedule` 为 None 时 shift 为 None。shift 不为 None 的日子是「工作日」，与环境层 `duty` 和零点计划「上班安排」同一判定。

**规则。** 逐条按顺序判定，第一条命中即为结果。每条规则只对写明的活动种类适用；对其他种类不命中。

| # | 适用 | 命中条件 | 结果 |
| --- | --- | --- | --- |
| 0 | interact | 开始内容的 `memory_category` 不为 None | 该值 |
| 0 | interact，用内容 app（`data["app_id"]` 不为 None） | 开始内容没有 `memory_category`，而 `catalog.apps[data["app_id"]].memory_category` 不为 None | 该值 |
| 1 | sleep | 恒命中（包括在班次地点的午睡） | 生活 |
| 2 | purchase | 恒命中 | 这次购买各订单的商品按第 4 节 `goods_category`：商品是 `goods_ids` |
| 2 | query | 恒命中 | `data["targets"]` 按第 4 节 `query_category`；答案 `data["answer"]` 不参与 |
| 3 | interact，在地点做事（`data["app_id"]` 为 None） | 开始内容属于食品单元 | 饮食 |
| 4 | travel | 当天是工作日、`book.workplace_id` 不为 None，且 `{location_id, destination_id} == {book.home_id, book.workplace_id}` | 工作 |
| 5 | interact，在地点做事 | 当天是工作日，并且满足其一：`location_id == book.workplace_id`（不论几点）；或者 `location_id != book.home_id` 且 `duty_status(shift, d, started_at, ZoneInfo(book.timezone)) == STATUS_ON_DUTY`（work_schedule.py:56-65；「当班中」，不含未到上班时间、写明的休息时段和已下班） | 工作 |
| 6 | interact，用内容 app | 恒命中 | `book.workplace_kind` 不为 None 且开始内容的 `prerequisite` 不为 None、`book.workplace_kind in prerequisite.location_kinds` 时为工作，否则为生活 |
| 7 | 其余 | 恒命中 | 生活 |

- 规则 0 对内容 app 先看内容行，再看 app 行；对地点内容只看内容行。同一系列各集的 `memory_category` 由登记保证相同（第 9 节），所以地点单元的覆盖与从哪一集开始无关。
- 规则 0 只适用于 interact：睡眠与出行没有内容行或 app 行；购买与查询所在或下单的 app 行不参与（见「规格阶段的决定」）。
- 规则 5 先看地点再看时钟：`duty_status` 只看时钟（work_schedule.py:56-65），上班前在公司做的事按地点归工作。在家做的事不经规则 5 归工作。
- 购买活动的 `goods_ids` 为空时由第 4 节 `goods_category` 抛 `IntegrityError("Memory category needs at least one goods")`。

**边界情形**（由「测试」一节的用例逐一钉住）：

| 情形 | 结果 |
| --- | --- |
| 包 0001 的 `office-eat-lunch`（研发中心的「吃午饭」，没有食品信号，饭在收货时补能） | 没有覆盖时规则 5 → 工作；包 0001 给它加 `"memory_category":"饮食"`（08），规则 0 → 饮食 |
| 在班次地点午睡 | 规则 1 → 生活 |
| 休息时段在工位刷 app；在岗时段在工位刷 app | 规则 6 → 生活（规则 6 不看时段） |
| 钉钉「上班打卡」「下班打卡」（前提要求研发中心） | 规则 6 → 工作 |
| 食材包、咖啡（drink） | 规则 2 → 饮食 |
| 做菜视频（app 内容）、电话（app 内容） | 规则 6 → 生活 |
| 整套做饭（含洗碗那一集）、同事聚餐 | 规则 3 → 饮食 |
| 工作日从家去公司、从公司回家 | 规则 4 → 工作 |
| 休息日去公司；工作日从公司去午饭店 | 规则 7 → 生活 |
| 销售在岗时段在客户处做的事 | 规则 5 → 工作 |
| 在岗时段在家做的事 | 不经规则 5；食品单元里的事规则 3 → 饮食，其余规则 7 → 生活（除非覆盖） |
| 在班次地点、在岗时段做食品单元里的事 | 规则 3 先于规则 5 → 饮食 |
| 23:30 开始、跨过零点的活动 | 按开始那一刻判定，跨零点不改类别 |
| 没有工作安排的人物 | 规则 4、5 不可能命中（没有工作日，`workplace_id` 为 None）；规则 6 恒命中但只给出生活（`workplace_kind` 为 None）；只有规则 0 能给出工作 |

### 4. 商品、查询与个人事务的类别

- `goods_category(book, goods_ids)`：`goods_ids` 中任一身份在 `book.food_goods` 里为饮食，否则为生活。一次购买、一次送达或一次收货里既有食品又有非食品时归饮食。`goods_ids` 为空时写英文错误日志并抛 `IntegrityError("Memory category needs at least one goods")`。
- `query_category(book, targets)`：`targets` 是查询活动的 `data["targets"]`（docs/design/09-io-formats.md:40：商品是 `{kind: goods, goods_id}`，类别是 `{kind: category, category}`）。任一项是 `kind == "category"` 且 `category` 属于 `FOOD_GOODS_CATEGORIES`，或是 `kind == "goods"` 且 `goods_id` 在 `book.food_goods` 里时为饮食，否则为生活。查询的类别从开始就定下：「开始查询」那一条和「查完」那一条同一类别。
- 个人事务（办成、新增、更新、取消）：`goods_category(book, [errand["product_id"]])`，`errand` 是那条事务事件正文里的事务快照（`data["errand"]`，src/worldmock/simulation_engine/scheduling/plans/errands.py:527-534）。

### 5. 被拒请求的类别

`rejected_category(book, store, request)` 给一条没有开始任何活动的世界请求（`request` 是它的 `action_requested` 事件）定类别。下表的「请求」是 `request.data["tool_name"]`；`action`、`target`、`detail` 是 `request.data["request"]` 里的同名键（src/worldmock/simulation_engine/decisions/decision_coordinator.py:113-127）。「对上目录行」一律按名称经 `channel_key`（src/worldmock/resource_engine/catalog/models.py:35-37：去掉全部空白、不分大小写）相等比较。被拒的出行与地点做事的判定日期是 `date.fromisoformat(local_date(request.occurred_at, book.timezone))`。

| 请求 | 类别 |
| --- | --- |
| `revise_day_plan` | 生活 |
| `report_status`、`pause_activity`、`conclude_activity` | 生活（目标是活动名，不是目录行） |
| `start_main_activity`，`action == "sleep"` | 生活（规则 1） |
| `start_main_activity`，`action == "travel"` | 目的地：显示名与 `target` 对上的地点；对不上为生活。对上时，出发地是这次决策观察事件的 `data["location_id"]`：`store.read_decision_observations(request.agent_id, request.decision_id, request.occurred_at)`（src/worldmock/recording_engine/storage/store.py:509-519）必须恰好返回一个事件，否则写英文错误日志并抛 `IntegrityError("Rejected request has no unique decision observation")`。出发地、目的地和 `request.occurred_at` 按规则 4 判定，命中为工作，否则为生活 |
| `start_main_activity`，`action == "interact"` | 地点：显示名与 `target` 对上的地点；单元：`location_units(catalog, 地点.kind)` 中单元名与 `detail` 对上的那一个。任一对不上为生活。都对上时，以单元第一条内容、这个地点和 `request.occurred_at` 依次按规则 0、3、5 判定，都不命中为生活 |
| `start_side_activity`，`action == "interact"` | app：名称与 `target[0]` 对上的 app；对不上为生活。对上时取第 9 节的 app 行类别（规则 0，否则规则 6 的 app 行判定） |
| `start_side_activity`，`action` 为 `purchase` 或 `query` | 对 `target` 的每个名称逐个判定，任一个为饮食即饮食，否则生活。一个名称：与某个商品类别的中文名（`GOODS_CATEGORY_LABELS`，src/worldmock/exchange_engine/definitions/types.py:11-13）对上时，按那个类别是否属于 `FOOD_GOODS_CATEGORIES`；与某个商品名对上时，按那件商品是否在 `book.food_goods`；都对不上时按下面的文字规则 |

**文字规则。** 名称原文含 `FOOD_TEXT_MARKERS` 的任一项，或含 `book.food_names` 的任一项（子串包含）时为饮食，否则为生活：

```python
#: 被拒的购买与查询对不上目录行时，目标文字含其中之一即归饮食：三个食品类别的中文名，再加提案逐字列出的九个字词。
FOOD_TEXT_MARKERS: tuple[str, ...] = (*(GOODS_CATEGORY_LABELS[key] for key in ("meal", "snack", "drink")),
                                      "饭", "菜", "面", "餐", "食", "饮", "咖啡", "奶", "茶")
```

即 `("食品", "零食", "饮料", "饭", "菜", "面", "餐", "食", "饮", "咖啡", "奶", "茶")`。V 10-12 11:45 被拒的「查询盖浇饭」含「饭」，归饮食；V 里被拒的「一人份食材包」含「食」，归饮食。

### 6. 每一行、每一项的类别

今天条目的行和项由 02 生成。各行、各项的类别只由本节两张表规定，02「各行的类别」（02-today-entries.md:161）与「各项的类别」（:198）两句改为引用本节：个人事务变更行取第 4 节的个人事务规则；购买行取这一行所写已付款订单的商品类别，不是购买活动全部订单的类别。宿主为每一行、每一项各存一个类别（提案 :210），取法如下。表中「那项活动的 `activity_category`」一律是 `activity_category(book, 快照, purchase_goods(快照, 开始结果))`，快照与开始结果按第 3 节「输入」读取。

**核验行**（写法见 02）：

| 核验行 | 类别 |
| --- | --- |
| 活动结束句：按计划结束、其他终局、起止跨日、暂停区间、出行到达、系列与 app 标题、做饭用的食材包 | 那项活动的 `activity_category` |
| 购买行（一次购买一行） | `goods_category`，商品是这一行所写已付款各订单的商品（02【E12】：`placed_orders(data.data)` 里 `paid_at` 不为空的订单） |
| 送达行；待取行（「A已到取货点待取」） | `goods_category`，商品是这一行所写各订单的商品 |
| 查完〈app〉·〈范围〉（以及全文里「当时查到」的各行） | 那项查询活动的 `activity_category` |
| 办成「X」；新增、更新、取消个人事务「X」 | 第 4 节的个人事务规则 |
| 暂停〈短名〉 | 被暂停活动的 `activity_category` |
| 开始〈短名〉[、〈短名〉] | 这一行第一项活动的 `activity_category`；开始组取槽位 1 那一项（提案示例 08:34 与 08:54 的「类别」都只写「工作」） |
| 被拒：〈动作〉〈对象〉 | 这一行所写的第一个被拒请求的 `rejected_category` |
| 改了今日计划 | 生活 |
| HH:MM 开始〈短名〉，跨过零点（只在零点收尾条目里） | 那项活动的 `activity_category` |
| 播报〈短名〉、收尾〈短名〉（02 的兜底核验） | 这次请求第一项所点名活动的 `activity_category` |

**自述项**（前缀见 02）：

| 自述项 | 类别 |
| --- | --- |
| 「〈短名〉：」收尾或暂停的总结 | 这个控制项所点名的活动：受理结果 `data["data"]["activity_ids"]` 里与这一项同位的身份（src/worldmock/simulation_engine/actions/dispatcher.py:257-268，按请求各项的次序），取它的 `activity_category` |
| 「进展·〈短名〉：」 | 同上 |
| 「看法·〈对象或人〉：」，包括关系方面的看法 | 同一控制项所点名活动的类别；不另设「关系」 |
| 「记住：」和只进全文的 routine 记忆 | 同一控制项所点名活动的类别 |
| 「正文：」 | 本次决策主请求的类别（第 7 节） |

- 被拒的控制请求不产生总结、进展、看法与记住各项（02【E13】被拒时只写被拒行和正文）；这次决策的正文取主请求的类别，即这条请求的 `rejected_category`（第 5 节：生活）。
- 一行或一项所点名的活动按 02【E12】的读法读不到任何一份快照时，由 02 在读快照处写英文错误日志并抛 `IntegrityError("Activity has no snapshot within the entry window")`（02【E46】），本章的函数不另判；第 7 节 `request_category` 在 `activity_categories` 里找不到活动身份时，写英文错误日志并抛 `IntegrityError("Memory category names an activity that was not read")`。

### 7. 主请求与条目的「类别」串

- **主请求。** `primary_request(requests)`：`requests` 是本次决策全部世界请求的 `action_requested` 事件，按事件序号排列，至少一条。其中同时有 `start_main_activity` 与 `start_side_activity` 时（开始组），主请求是 `start_main_activity` 那一条；否则是第一条。
- **一条请求的类别。** `request_category(book, store, request, result, activity_categories)`，`result` 是这条请求的第一条 `action_result`；`activity_categories` 是调用方为本条目读到的每项活动算出的类别（活动身份 → 第 6 节开头的 `activity_category(book, 快照, purchase_goods(快照, 开始结果))`），不读 `WorldState`：

  | 请求 | 类别 |
  | --- | --- |
  | `revise_day_plan` | 生活 |
  | 两个开始工具 | `result.activity_id` 不为 None 时，取 `activity_categories[result.activity_id]`；为 None（被拒）时取 `rejected_category` |
  | 三个控制工具 | `result.data["status"] == "completed"` 时，取 `activity_categories[result.data["data"]["activity_ids"][0]]`；否则取 `rejected_category` |

  要取的活动身份不在 `activity_categories` 里时，写英文错误日志并抛 `IntegrityError("Memory category names an activity that was not read")`。

- **「类别」串。** `category_label(primary, parts)`：`primary` 是主请求的类别；`parts` 是这一条全文（02 的全文形状）里全部核验行的类别（按行序），接着是全部自述项的类别（按项序，含 routine 记忆）。结果是 `"、".join(dict.fromkeys([primary, *parts]))`：主请求的类别打头，其余按首次出现的次序。例：`"工作、饮食"`。
- 「类别」串在全文上算一次，渲染的条目和全文用同一串：规则 B 整项省略的项、不渲染的 routine 记忆，也计入。01 的兜底按「本地小时、类别串」合并，所以同一条目的类别串在当天不变。
- 零点收尾条目没有主请求：`category_label(None, parts)`，即它各行类别按首次出现的次序。
- `primary` 为 None 且 `parts` 为空时写英文错误日志并抛 `IntegrityError("Memory entry has no category")`。
- 宿主内部为每条条目保存「类别」串（以主请求的类别打头）、每一行和每一项的类别（02 的 `TodayEntry.category`、`EntryLine.category`、`SelfItem.category`）；按类别切分条目（04 的回顾输入、05 的按类别检索「今天」）只用这些逐行逐项的类别。

### 8. 工作类是否启用

- `book.categories` 是 `MEMORY_CATEGORIES`，除非人物没有工作安排（`work_schedule` 为 None）且目录里没有任何内容行或 app 行写 `"memory_category":"工作"`；那时是 `("饮食", "生活")`。
- 工作类不启用时，任何行和项都不可能归工作（第 3 节表末一行），「近三天」不出现「工作」键（第 13 节），04 不为它生成回顾。

### 9. 目录字段 `memory_category` 与目录格式 6

**字段。** `Content` 与 `App`（src/worldmock/resource_engine/catalog/models.py:225-249、:59-66）各加一个可选字段：

```python
    #: 记忆类别覆盖（docs/design/06-agent.md「记忆的类别」规则 0）；省略表示按推导规则。
    memory_category: MemoryCategory | None = None
```

- 合法值恰为「饮食」「工作」「生活」三个字符串。省略即 None。
- 显式写 `null` 由行模型拒绝：`field_validator("memory_category", mode="before")` 调 `require_declared_category`，它对 None 调 `reject_value("A declared memory category must be a category name.")`，与 `require_declared_state`（models.py:211-215）同一写法。
- 闭集以外的值（空串、「休息」、数字、数组）由 `Literal` 拒绝。两种失败都经 `parse_catalog_model`（src/worldmock/resource_engine/catalog/loader.py:98-113）报 `ENVIRONMENT_KIND_INVALID`，详情以 `content-catalog.jsonl:<行号>` 或 `app-catalog.jsonl:<行号>` 开头，含字段名 `memory_category`。
- 同一系列各集的 `memory_category` 必须相同。`_check_series`（admission.py:238-253）在比较 `consumes_state` 之后再比较 `memory_category`，不同时抛 `RegistrationError("CONTENT_SERIES_INVALID", f"{title} episodes declare different memory categories.")`；原因文字（src/worldmock/resource_engine/registration/errors.py:32-33）不变。
- 字段只被第 3、5、8、9 节读取：不进索引、准入、结算、名称解析和任何提示层。写了它的包，目录摘要 `catalog_content_sha256` 随行字节变化，与其他字段相同。
- 功能 app 行也可以写这个字段，登记不另加检查；它与内容 app 行一样计入第 8 节工作类是否启用和第 9 节静态推导表，在推导一条记录的类别时只在被拒请求点了这个 app 的名字时被读到。

**目录格式 6。** 写了 `memory_category` 的行到 f67c25e5 为止的构建都拒收（DeepFrozenModel 拒绝未知字段，src/worldmock/foundation/models.py:39-42），所以目录格式随本字段从 5 升到 6。格式号本身（`CatalogManifest.format_version` 为 `Literal[6]`、加载与校验的拒绝次序、仓内样例环境清单、相应测试与源码变异、五个包的重建与 LabelKit 通知）只在第 08 章「目录格式 6 与环境包」规定，本章不另写。

**静态推导表。** `row_categories(book)` 给目录的每一条内容行和 app 行一个 `RowCategory(kind, row_id, category, rule)`，供 09 的工具 tools/analysis/memory_categories.py 列给包作者，并由包 0001 的测试钉住。次序：内容行按 `content_id` 升序，然后 app 行按 `app_id` 升序。

| 行 | 判定（第一条命中） |
| --- | --- |
| 地点内容 | `memory_category` 不为 None → (该值, 0)；属于食品单元 → (饮食, 3)；`book.workplace_kind` 在它的 `location_kinds` 里 → (工作, 5)；否则 (生活, 7) |
| app 内容 | `memory_category` 不为 None → (该值, 0)；否则规则 6：前提的 `location_kinds` 含 `book.workplace_kind` → (工作, 6)，否则 (生活, 6) |
| app | `memory_category` 不为 None → (该值, 0)；否则规则 6 的 app 行判定：`catalog.contents_by_app[app_id]` 中有一条内容的前提 `location_kinds` 含 `book.workplace_kind` → (工作, 6)，否则 (生活, 6) |

静态表里地点内容的规则 5 表示「工作日在班次地点开始时归工作」；规则 5 的在岗时段一支依赖开始的时刻和地点，只在第 3 节的动态判定里出现。

### 10. 归日

**规则。** 一个事件按它的发生时刻 `occurred_at` 在场景时区的本地日期归日，零点整（00:00:00）算新的一天。日期由 `local_date`（readable.py:35-37）算出。全系统只用这一条：今天条目与零点收尾条目（02）、「近三天」与回顾的期次（本章、04）、检索的日期（05）、当日拒绝 `rejected_today`、零点计划的回顾时段都用它。

**一天的窗口。** `DayWindow(day, start, end, timezone)`：
- `start` 是 `day` 本地 00:00 的纪元秒，`int(datetime.combine(date.fromisoformat(day), time(0), ZoneInfo(timezone)).timestamp())`，与 work_schedule.py:44 构造当地零点的写法相同；
- 整天的窗口由 `day_window(day, timezone)` 给出，`end` 是下一个本地日的 `start`；
- `contains(epoch)` 恰为 `start <= epoch < end`：左闭右开。

**零点界。** D 日的零点界 B_D 是 D 日本地 00:00 之前最后一个事件的序号，`day_bound(store, day, timezone, now) = store.last_event_seq_before(day_window(day, timezone).start)`（store.py:340-355），没有这样的事件时为 0。全仓零点界只有这一个定义：02【E3】【E6】与 06【P1】的 B_D、`rejected_today` 的下界都调用它。
- 事件按发生时刻单调追加（store.py:342-344），所以序号落在 (B_D, B_{D+1}] 的事件恰是 D 日发生的事件。
- B_D 只有在时钟到达 D 日 00:00 之后才确定：`now < day_window(day, timezone).start` 时写英文错误日志并抛 `IntegrityError("Day bound is read before the day starts")`。
- 时钟推进以本地零点为边界（src/worldmock/simulation_engine/scheduling/clock/time_advance.py:409-412 的候选边界含场景本地零点），所以同一次提交的事件不会跨在零点两侧。

**归日的后果**（由 02 的条目和本章的测试落实）：
- 活动在哪天结束就记在哪天：23:30 → 07:30 的睡眠，结束句记在醒来那天；它的开始在开始那次决策的条目里；跨过零点仍在进行时，前一天的零点收尾条目另记一句「跨过零点」（02）。
- 00:10 的决策及其自述属于新的一天。
- 23:59:59 的事件属于前一天；00:00:00 的时点事件和恰在 00:00:00 结束的区间都属于新的一天。
- 退役的两条旧规则「按 session 结束日归」（src/worldmock/simulation_engine/observation/layers/memory_layer.py:105-107）与「睡醒日减一」的删除分别归 02 与 08。

**当日拒绝。** `rejected_today`（src/worldmock/simulation_engine/observation/layers/progress_layer.py:316-347）的下界（:321-322）改为 `day_bound(store, day, zone, context.state.current_time)`，其余不变。`local_day_start`（readable.py:40-42）随之没有调用方，删除。

### 11. 零点计划的回顾时段

`day_plan_inputs._review_window(now, timezone_name, midnight_boundary)`（src/worldmock/simulation_engine/scheduling/plans/day_plan_inputs.py:109-115）改为返回 `DayWindow`：

```python
def _review_window(now: int, timezone_name: str, midnight_boundary: bool) -> DayWindow:
    """@brief 回顾时段：零点边界回顾刚结束的前一日，运行开始、恢复与分支回顾生成时刻所在本地日中生成时刻之前的部分；
    都是左闭右开的本地日窗口（docs/design/04-simulation-engine.md「归日」），起点恰为零点时为空时段。"""
    day = local_date(now - 1 if midnight_boundary else now, timezone_name)
    return DayWindow(day, day_window(day, timezone_name).start, now, timezone_name)
```

- 零点边界时 `now` 恰是 D 日 00:00，窗口是 [D−1 日 00:00, D 日 00:00)，与 `day_window(D−1)` 相同；启动路径是 [当天 00:00, 生成时刻)。
- 删除 `_Window`（:37-60）及其 `covers`（闭区间，:45-47）和 `ends_within`（左开右闭，:49-51）。原来的五处判定一律改为 `window.contains(...)`：`_experience_sentences`（:169）、`_result_sentences` 的动作结果（:188）、送达（:195）与结束句（:206）、`_conclusion_records`（:233）。
- `_Window.clock` 与 `_Window.when` 改为模块私有函数 `_clock(window, epoch)`、`_when(window, epoch)`，按 `window.timezone` 写「HH:MM」和「M月D日 HH:MM」，文字与基线逐字相同；`plan_inputs`（:96-97）、`_conclusion_records`（:239）、`_pending_orders`（:265）照此调用。
- 「回顾时段」的文字仍是 `f"{_when(window, window.start)} 至 {_when(window, window.end)}"`；`_previous_goals` 仍按 `plan["day_end"] == window.end`（:285）找前一天计划的最后一份快照。
- 后果：恰在零点那一秒结算的结果、送达、结束、收尾和经历，从此只进新一天的计划，不再被前后两天各算一次；前一日 00:00:00 那一秒的经历从此进前一日的计划（原 `ends_within` 把它算给更前一天）。

### 12. 回顾事件在「近三天」里的读法

回顾事件 `memory_digest_generated` 的正文、读取与按 (store, agent) 的增量缓存归 04。本章只规定「近三天」从中读什么：

- 读取由 `day_digests(store, agent_id, cutoff)` 一处完成：经 04 的唯一读取入口 `registered_digests(store, agent_id)`（simulation_engine/observation/memory/digests.py）取这个人物 `tier == "day"`、`event_seq <= cutoff` 的回顾事件，每个事件对应一天 `period`（YYYY-MM-DD）；「近三天」的 `cutoff` 是本次观察时的 `store.last_event_seq`。
- 每一天读成 `DayDigest(texts, unsettled)`：`texts` 是有正文的类别（中文）→ 正文，即 `{MEMORY_CATEGORIES[i]: data["digests"][key]["text"] for i, key in enumerate(MEMORY_DIGEST_KEYS) if data["digests"][key] is not None}`；`unsettled` 是 `fallback` 里的类别（中文），即 `frozenset(MEMORY_CATEGORIES[MEMORY_DIGEST_KEYS.index(key)] for key in data["fallback"])`。`MEMORY_DIGEST_KEYS = ("food", "work", "life")` 由 04 定义在 state/model/memory.py，与 `MEMORY_CATEGORIES` 逐一对应。
- 一天一类恰有三种状态：
  - **有回顾**：这一类在 `texts` 里；
  - **未整理**：这一类在 `unsettled` 里（04 的校验重试用尽）；
  - **没有记录**：两者都不是，或那天没有回顾事件（04：那天三类都没有条目时不调用、不写事件）。
- 一类同时在 `texts` 与 `unsettled` 里时，`DayDigest` 构造即写英文错误日志并抛 `IntegrityError("Day digest marks a category both written and unsettled")`。
- 「有工作记录的日子」是工作类处于「有回顾」或「未整理」的日子。04 规定那天这一类没有任何条目时这一类为 null 且不进 `fallback`，所以二者等价于那天至少有一条工作类的行或项。

### 13. 近三天

**取哪几天。** 记今天（本次观察的本地日期）为 T。
- 饮食、生活：日历上的前三天 T−3、T−2、T−1（`recent_dates(T)`），不论那几天有没有记录。
- 工作：日期早于 T 的「有工作记录的日子」里最晚的三个（`recent_work_dates(digests, T)`），不足三个时有几个取几个。不设回看上限：长假之后仍能看到假前最后三个工作日。在 30 天的包运行（09-28 → 10-28）里，10-08 的工作格是 09-28、09-29、09-30，饮食和生活格是 10-05、10-06、10-07。
- 每类的格按日期从早到晚排列。

**格子。** 一天一类：
- 没有记录：这一格省略，不写任何占位；
- 有回顾：`{"日期":"2026-10-09","星期":"星期五","回顾":"……"}`；
- 未整理：`{"日期":"2026-10-09","星期":"星期五","回顾":null,"未整理":true}`。

省略与未整理是两种情形：前者表示那天这一类没有事，后者表示有事但回顾没整理出来（系统层 rules[1] 的说明归 07）。

键与写法：
- 「日期」是 YYYY-MM-DD；「星期」取 `WEEKDAY_NAMES[date.fromisoformat(日期).weekday()]`（work_schedule.py:24）。
- 工作格在「星期」与「回顾」之间另有「安排」：那天 `shift_on(schedule, date.fromisoformat(日期))` 不为 None 时写 `DAY_WORK`（「工作日」），否则写 `DAY_REST`（「休息日」，work_schedule.py:19-20）；人物没有工作安排（只因覆盖启用了工作类）时写「休息日」。饮食格和生活格没有「安排」。
- 键序即上面的书写次序：日期、星期、（安排、）回顾、（未整理）。
- 回顾正文原样放入，不裁剪（≤200 字由 04 在生成时校验）。

**整块。** `recent_block(digests, today, categories, schedule)` 返回一个对象：键是 `categories`（第 8 节）按 `MEMORY_CATEGORIES` 的次序，值是这一类的格子列表，没有格子时是 `[]`。工作类不启用时没有「工作」键。01 把它放在记忆层的「近三天」键下（01 的 `memory_view` 里 `recent = ...` 一行即调用本函数）。

**稳定性。** 「近三天」只依赖 T 和日期早于 T 的回顾事件。D 日的回顾事件在 D+1 日第一次决策之前就已登记（04 的零点任务次序与恢复补齐），当天不再新增日期早于当天的回顾事件，所以同一天里每次观察的「近三天」逐字节相同，只在零点变化。01 的递推依赖这一点。

**头三天。** 每次运行和每个分支在头三天只有部分格子：运行开始那天三类都是 `[]`。分支继承检查点之前的回顾事件；检查点之后经过的零点在分支里重新生成（04），所以分支的「近三天」与原运行在同一时刻相同，只要那几天的回顾相同。

## 接口与文件

### src/worldmock/state/model/memory.py（改）

导入行 `from typing import Any`（:6）改为 `from typing import Any, Literal, get_args`；在 `SUBJECTIVE_TYPES` 的注释（:22）之前新增：

```python
#: 记忆的类别闭集（docs/design/06-agent.md「记忆的类别」）；次序即「近三天」的键序、回顾与检索的类别次序。
MemoryCategory = Literal["饮食", "工作", "生活"]
MEMORY_CATEGORIES: tuple[MemoryCategory, ...] = get_args(MemoryCategory)
```

快照校验不改。

### src/worldmock/resource_engine/catalog/models.py（改）

- 新增导入 `from worldmock.state.model.memory import MemoryCategory`（`resource_engine` 引用 `worldmock.state` 不越界，test_import_graph.py:388-389）。
- `CatalogManifest.format_version`（:51）与 `Catalog.food_states()`、`Catalog.kit_states()` 由第 08 章改。
- 在 `CatalogManifest`（:49-56）之后、`App`（:59）之前新增（`App` 与 `Content`（:225）的类体都要用到它）：

```python
def require_declared_category(value):
    """@brief 显式记忆类别必须是类别名；不覆盖只有省略这一种写法。"""
    if value is None:
        reject_value("A declared memory category must be a category name.")
    return value
```

- `App`（:59-66）在 `travel_method_ids` 之后加字段 `memory_category: MemoryCategory | None = None` 和
  `_declared_category = field_validator("memory_category", mode="before")(require_declared_category)`，校验器前加注释
  `#: 显式记忆类别必须是类别名，缺省才表示按推导规则。`。
- `Content`（:225-249）在 `completion_effects` 之后加同样的字段和校验器。

### src/worldmock/resource_engine/registration/admission.py（改）

`_check_series`（:238-253）：`series` 的值从（容器绑定，内容前提，凭的状态）扩为（容器绑定，内容前提，凭的状态，记忆类别），`setdefault` 存入 `content.memory_category`；在凭的状态比较（:251-252）之后加

```python
    if first[3] != content.memory_category:
        raise RegistrationError("CONTENT_SERIES_INVALID", f"{title} episodes declare different memory categories.")
```

文档字符串改为「同一系列各集的容器绑定、内容前提、凭的状态与记忆类别必须完全相同，集号不得重复；完成效果各集各写。」，`:param series:` 一行同步写四项。

### 新文件 src/worldmock/simulation_engine/observation/memory/categories.py

包 `observation/memory/` 与它的 `__init__.py` 见 01。模块说明：

```python
"""@brief 记忆的类别：饮食、工作、生活三类由冻结目录、人物工作安排与已提交事件确定性推出，不由模型标注、不存状态。

规则见 docs/design/06-agent.md「记忆的类别」：一项活动按开始时刻与开始内容依规则 0–7 第一条命中即为结果；商品、查询与
个人事务按商品；被拒的请求按能对上的目录行，对不上的购买与查询按目标文字；条目的「类别」串以主请求的类别打头，其余按
首次出现的次序以「、」相连。本模块只读冻结输入与已提交事件，不读世界状态，不写任何东西。
"""
```

导入：`logging`、`dataclasses.dataclass`、`datetime.date`、`typing`（`Any`、`Iterable`、`Literal`、`Mapping`、`Sequence`）、
`weakref.WeakKeyDictionary`、`zoneinfo.ZoneInfo`；`worldmock.configuration.inputs.environment.EnvironmentInputs`；
`worldmock.configuration.persona.work_schedule`（`STATUS_ON_DUTY`、`duty_status`、`shift_on`）；
`worldmock.exchange_engine.definitions.types.GOODS_CATEGORY_LABELS`；`worldmock.foundation.errors.IntegrityError`；
`worldmock.foundation.time.readable.local_date`；`worldmock.recording_engine.events.models.Event`；
`worldmock.recording_engine.projections.commerce.action_orders.placed_orders`；`worldmock.recording_engine.storage.store.FootprintStore`；`worldmock.resource_engine.catalog.models`（`App`、`Catalog`、
`Content`、`channel_key`）；`worldmock.simulation_engine.activities.content.location_activities.location_units`；
`worldmock.state.interaction.actions.START_TOOLS`；`worldmock.state.interaction.control_items.CONTROL_TOOLS`（control_items.py:19，三个控制工具只在那里列一次）；`worldmock.state.model.memory`（`MEMORY_CATEGORIES`、`MemoryCategory`）；
`worldmock.state.world.activity.ActivityState`；`worldmock.state.world.kinds.ActivityKind`。不导入 `WorldState`。

常量与类型：

```python
#: 规则 2 的食品商品类别。
FOOD_GOODS_CATEGORIES = frozenset({"meal", "snack", "drink"})
#: 被拒的购买与查询对不上目录行时，目标文字含其中之一即归饮食：三个食品类别的中文名，再加提案逐字列出的九个字词。
FOOD_TEXT_MARKERS: tuple[str, ...] = (*(GOODS_CATEGORY_LABELS[key] for key in ("meal", "snack", "drink")),
                                      "饭", "菜", "面", "餐", "食", "饮", "咖啡", "奶", "茶")
_FOOD, _WORK, _LIFE = MEMORY_CATEGORIES


@dataclass(frozen=True, slots=True)
class Derivation:
    """@brief 一次推导的类别与命中的规则编号（0–7）。"""

    category: MemoryCategory
    rule: int


@dataclass(frozen=True, slots=True)
class RowCategory:
    """@brief 目录一行的静态推导：内容行或 app 行、行身份、类别与规则编号。"""

    kind: Literal["content", "app"]
    row_id: str
    category: MemoryCategory
    rule: int


@dataclass(frozen=True, slots=True)
class CategoryBook:
    """@brief 推导类别所需的冻结输入：目录、工作安排、时区、住处与班次地点、食品商品、食品单元与启用的类别。"""

    catalog: Catalog
    schedule: Any | None
    timezone: str
    home_id: str
    workplace_id: str | None
    workplace_kind: str | None
    food_goods: frozenset[str]
    food_names: tuple[str, ...]
    food_units: frozenset[str]
    categories: tuple[MemoryCategory, ...]


#: 存储对象 → （构建所用的环境输入，推导输入）；换成恢复后的新存储对象时重建。
_BOOKS: WeakKeyDictionary[FootprintStore, tuple[EnvironmentInputs, CategoryBook]] = WeakKeyDictionary()
```

公开函数（全部 ≤50 行、≤5 个参数，中文 `@brief`；私有辅助函数的拆分由实现决定，下列签名与语义固定）：

| 签名 | 语义 |
| --- | --- |
| `category_book(inputs: EnvironmentInputs) -> CategoryBook` | 按第 2、8 节算出全部字段；纯函数 |
| `memory_book(store: FootprintStore, inputs: EnvironmentInputs) -> CategoryBook` | 观察路径的入口：`_BOOKS` 里这个存储对象已有、且构建所用的 `inputs` 是同一对象时直接返回，否则调 `category_book` 并存入 |
| `start_content_id(activity: ActivityState) -> str` | 第 3 节「开始内容」 |
| `purchase_goods(activity: ActivityState, start_result: Event) -> tuple[str, ...]` | 第 3 节「输入」：购买活动的商品，按订单身份升序；其余种类为 `()` |
| `activity_derivation(book: CategoryBook, activity: ActivityState, goods_ids: Sequence[str]) -> Derivation` | 第 3 节规则 0–7。`activity` 是 02【E12】读法得到的活动快照；`goods_ids` 是 `purchase_goods(activity, start_result)`。不读 `WorldState` |
| `activity_category(book: CategoryBook, activity: ActivityState, goods_ids: Sequence[str]) -> MemoryCategory` | `activity_derivation(...).category` |
| `goods_category(book: CategoryBook, goods_ids: Sequence[str]) -> MemoryCategory` | 第 4 节 |
| `query_category(book: CategoryBook, targets: Sequence[Mapping[str, str]]) -> MemoryCategory` | 第 4 节 |
| `rejected_category(book: CategoryBook, store: FootprintStore, request: Event) -> MemoryCategory` | 第 5 节；只在被拒出行时读观察事件 |
| `request_category(book: CategoryBook, store: FootprintStore, request: Event, result: Event, activity_categories: Mapping[str, MemoryCategory]) -> MemoryCategory` | 第 7 节「一条请求的类别」；`activity_categories` 是调用方按 02【E12】读出的活动快照算出的类别（活动身份 → 类别） |
| `primary_request(requests: Sequence[Event]) -> Event` | 第 7 节「主请求」；`requests` 为空时写英文错误日志并抛 `IntegrityError("Decision has no world request")` |
| `category_label(primary: MemoryCategory \| None, parts: Iterable[MemoryCategory]) -> str` | 第 7 节「类别」串 |
| `row_categories(book: CategoryBook) -> tuple[RowCategory, ...]` | 第 9 节静态推导表 |

规则 0、3、5 在活动判定（第 3 节）与被拒的地点做事（第 5 节）之间共用一个私有函数：给定开始内容、地点身份和时刻，返回规则 0、3、5 中第一条命中的 `Derivation` 或 None。规则 4 在活动判定与被拒出行之间共用一个私有函数：给定出发地、目的地和时刻，返回是否命中。规则 6 的内容行判定与 app 行判定各一个私有函数，活动判定、被拒请求和 `row_categories` 共用。每条规则只在这些函数里写一次。

### 新文件 src/worldmock/simulation_engine/observation/memory/days.py

本章规定其中的归日、零点界与「近三天」部分；零点收尾条目归 02，月、年回顾的次数统计归 04，它们写进同一模块时保持本节的签名不变。模块说明：

```python
"""@brief 归日与「近三天」：事件按发生时刻的场景本地日期归日，零点整属于新的一天；一天的窗口左闭右开，零点界是本地零点
之前最后一个事件的序号；「近三天」饮食和生活取日历上的前三天，工作取最近三个有工作记录的日子。

规则见 docs/design/04-simulation-engine.md「归日」与 docs/design/06-agent.md「近三天」。记忆层、零点计划、当日拒绝与检索
都经这里归日，全仓只有这一条规则。
"""
```

本章这部分用到的导入：`logging`、`dataclasses.dataclass`、`datetime`（`date`、`datetime`、`time`、`timedelta`）、`typing`（`Any`、`Mapping`）、
`zoneinfo.ZoneInfo`；`worldmock.configuration.persona.work_schedule`（`DAY_REST`、`DAY_WORK`、`WEEKDAY_NAMES`、`shift_on`）；
`worldmock.foundation.errors.IntegrityError`；`worldmock.recording_engine.storage.store.FootprintStore`；
`worldmock.state.model.memory`（`MEMORY_CATEGORIES`、`MEMORY_DIGEST_KEYS`、`MemoryCategory`）；
`worldmock.simulation_engine.observation.memory.digests.registered_digests`。days.py 不得导入 progress_layer.py、memory_layer.py
或 day_plan_inputs.py。02 放进 days.py 的 `published_today`、`past_day_entries` 导入 entries.py，本章的 `day_digests` 导入
digests.py；entries.py、categories.py、digests.py 及它们的传递导入同样不得导入这三个模块，所以 day_plan_inputs.py 与
progress_layer.py 导入 days.py 不成环。在基线上，categories.py 的全部导入（含 location_activities.py 与 action_orders.py）的
传递闭包都不含这三个模块。

```python
_WORK = MEMORY_CATEGORIES[1]


@dataclass(frozen=True, slots=True)
class DayWindow:
    """@brief 一个本地日的时刻窗口 [start, end)：start 是这一天本地零点，end 是下一个本地零点或部分窗口的截止时刻。"""

    day: str
    start: int
    end: int
    timezone: str

    def contains(self, epoch: int) -> bool:
        """@brief 时刻是否落在窗口里（左闭右开：零点整属于新的一天）。"""
        return self.start <= epoch < self.end


@dataclass(frozen=True, slots=True)
class DayDigest:
    """@brief 一天的日回顾在「近三天」里用到的部分：有正文的类别 → 正文，以及「未整理」的类别。"""

    texts: Mapping[MemoryCategory, str]
    unsettled: frozenset[MemoryCategory]

    def __post_init__(self) -> None:
        """@brief 一类不能既有正文又未整理。"""
        if set(self.texts) & self.unsettled:
            logging.error("Day digest marks a category both written and unsettled")
            raise IntegrityError("Day digest marks a category both written and unsettled")
```

公开函数：

| 签名 | 语义 |
| --- | --- |
| `shift_date(day: str, days: int) -> str` | ISO 日期加减整天，`(date.fromisoformat(day) + timedelta(days=days)).isoformat()` |
| `day_window(day: str, timezone: str) -> DayWindow` | 整天窗口：`start` 按第 10 节；`end` 是 `shift_date(day, 1)` 按同一算法得到的本地零点 |
| `day_bound(store: FootprintStore, day: str, timezone: str, now: int) -> int` | 第 10 节零点界，含 `now` 早于当天零点时的完整性故障。全仓零点界只有这一个定义：02【E3】【E6】、06【P1】与 `rejected_today` 都调用本函数（`local_day_start` 删除）。02 的 `AgentEvents.day_bounds` 以日期串为键，存本函数的返回值 |
| `day_digests(store: FootprintStore, agent_id: int, cutoff: int) -> dict[str, DayDigest]` | 第 12 节。取 `registered_digests(store, agent_id)` 里 `tier == "day"` 且 `event_seq <= cutoff` 的事件；键是 `period`，值是第 12 节的 `DayDigest`；按 `period` 升序插入 |
| `recent_dates(today: str) -> tuple[str, str, str]` | `(T−3, T−2, T−1)` |
| `recent_work_dates(digests: Mapping[str, DayDigest], today: str) -> tuple[str, ...]` | 日期早于 today、工作类有回顾或未整理的日子中最晚的三个，按日期升序 |
| `recent_block(digests: Mapping[str, DayDigest], today: str, categories: tuple[MemoryCategory, ...], schedule: Any \| None) -> dict[str, list[dict]]` | 第 13 节整块；`digests` 是日期 → `DayDigest`，只含第 12 节读到的日子 |

01 的 `memory_view` 里「近三天」一行恰为：

```python
    book = memory_book(store, context.inputs)
    today = local_date(context.state.current_time, context.config.timezone)
    digests = day_digests(store, context.agent_id, store.last_event_seq)
    recent = recent_block(digests, today, book.categories, context.persona.work_schedule)
```

04 在恢复时补齐的候选日（日历上的 T−1…T−3，与最近三个工作类有条目的日子取并集，未登记的日子按条目判定）由 04 规定。本章的 `recent_dates` 与 `recent_work_dates` 只读已登记的日回顾，用于渲染「近三天」。

### src/worldmock/agents/prompt/prompt_schemas.py（改）

「近三天」格子的机器形状写在 `MEMORY_LAYER_SCHEMA` 之前（基线已导入 `DUTY_DAYS`、`WEEKDAY_NAMES`，:21）：

```python
def _recent_cells(work: bool) -> dict[str, Any]:
    """@brief 一类「近三天」的格子数组 Schema：至多三格；每格有日期、星期，工作格另有安排；回顾是 1–200 字的段落，
    没整理出来时回顾为 null 并带「未整理」。"""
    head = {"日期": _DATE, "星期": {"enum": list(WEEKDAY_NAMES)}, **({"安排": {"enum": list(DUTY_DAYS)}} if work else {})}
    return {"type": "array", "maxItems": 3, "items": {"oneOf": [
        _object({**head, "回顾": {"type": "string", "minLength": 1, "maxLength": 200}}),
        _object({**head, "回顾": {"type": "null"}, "未整理": {"const": True}})]}}


#: 「近三天」三类各自的格子数组。
_RECENT_CELLS = {name: _recent_cells(name == MEMORY_CATEGORIES[1]) for name in MEMORY_CATEGORIES}
```

`MEMORY_LAYER_SCHEMA` 的「近三天」写成 `_object(_RECENT_CELLS, optional=MEMORY_CATEGORIES)`，这一行由 01 写在整份 Schema 里（01-memory-layer.md:502）。「近三天」格子的 Schema 只在这里定义：01（:494、:881）与 07（:214、:299、:535）都引用本章的 `_recent_cells`、`_RECENT_CELLS`，不另写。

### src/worldmock/simulation_engine/scheduling/plans/day_plan_inputs.py（改）

- 新增导入 `from worldmock.foundation.time.readable import local_date` 与 `from worldmock.simulation_engine.observation.memory.days import DayWindow, day_window`。
- 删除 `_Window`（:37-60）；`_review_window` 按第 11 节返回 `DayWindow`；新增 `_clock(window: DayWindow, epoch: int) -> str` 与 `_when(window: DayWindow, epoch: int) -> str`，函数体即原 `_Window.clock`、`_Window.when`（:53-60），时区取 `window.timezone`。
- 五处判定改为 `window.contains`（第 11 节）；`_experience_sentences`、`_result_sentences`、`_conclusion_records`、`_pending_orders`、`_previous_goals` 的形参类型由 `_Window` 改为 `DayWindow`，签名其余不变。
- 模块说明（:1）末尾加「回顾时段按左闭右开的本地日窗口取（docs/design/04-simulation-engine.md「归日」）。」

### src/worldmock/simulation_engine/observation/layers/progress_layer.py（改，只改一处）

- 导入去掉 `local_day_start`（:28），新增 `from worldmock.simulation_engine.observation.memory.days import day_bound`。
- `rejected_today` 的 :322 改为 `after = day_bound(store, day, context.config.timezone, context.state.current_time)`。进度层的其余改动归 06。

### src/worldmock/foundation/time/readable.py（改）

删除 `local_day_start`（:40-42）。

## 设计文档

文档、实现和行为测试在同一次提交里改（docs/INDEX.md:18）。

**docs/design/06-agent.md**
- 「零点计划」表的「回顾时段」一行（:198）改为：零点时是前一天 00:00 起到这次零点为止，不含这次零点那一秒；运行开始或恢复时是当天 00:00 起到生成时刻为止，不含生成时刻那一秒；时刻落在哪个本地日见[模拟引擎](04-simulation-engine.md)「归日」。「动作结果」「收尾记录」「已核验经历」三行的「回顾时段里」不改。
- 「进度、待办与记忆」一节里新增小节 `<a name="agent-记忆的类别"></a>` `### 记忆的类别`，放在 :305「删除自然语言目标自动进展……」一句之后，其后紧接下一条的「近三天」小节，再后是第 04 章的「日、月、年回顾」（各小节的次序见第 10 章「设计文档」），写明：
  - 三类闭集与次序；类别由代码从冻结目录、人物声明和已提交事件推导，不由模型标注，不读世界状态；条目、行与项的类别不存进状态和事件正文（回顾事件按类别键存回顾，见「日、月、年回顾」）；不用 `intent_kind`；
  - 推导所用的冻结输入：食品商品、单元与食品单元的定义（第 2 节）；食品状态只链到[模拟引擎](04-simulation-engine.md)「出行、内容与查询」里第 08 章写的定义，不在这里另写；
  - 一项活动的规则 0–7 表，按活动快照里的开始时刻和开始内容判定，第一条命中即为结果；购买的商品取开始结果里这项活动的订单；规则 0 对内容 app 先内容行后 app 行；规则 3 先于规则 5；规则 5 先看地点后看在岗时段；
  - 第 3 节的边界情形表；
  - 商品、查询、个人事务的类别；被拒请求的类别表与文字规则（十二个字词逐字列出）；
  - 核验行与自述项的类别表；主请求；「类别」串的写法与「在全文上算一次」；零点收尾条目的类别；
  - 工作类的启用条件；
  - 目录覆盖字段 `memory_category` 的语义链到[资源引擎](03-resource-engine.md)「环境目录文件」。
- 同一节里新增小节 `<a name="agent-近三天"></a>` `### 近三天`，写明：取哪几天（饮食、生活按日历前三天，工作取最近三个有工作记录的日子，不设回看上限）；「有工作记录的日子」的判定；格子的三种情形与两种形状、键序、「星期」与「安排」的取值；整块的键与空列表；工作类不启用时没有「工作」键；同一天内逐字节不变、只在零点变；头三天只有部分格子。
- 这两个小节之外，同一节由 01、02、04、06 改写的部分不在本章。

**docs/design/04-simulation-engine.md**
- 在「唤醒、日计划与个人事务」（:681-724）之后、「回调事务」（:726）之前新增一节 `<a name="simulation-engine-归日"></a>` `## 归日`，写明：
  - 事件按发生时刻的场景本地日期归日，00:00:00 属于新的一天；全系统只有这一条规则，使用者列表（第 10 节）；
  - 一天的窗口左闭右开；零点界 B_D 的定义（全仓只经 `day_bound` 一处求出）、(B_D, B_{D+1}] 恰为 D 日的事件、B_D 只在时钟到达 D 日零点后读取；
  - 时钟推进以本地零点为边界，一次提交不跨零点；
  - 活动在哪天结束就记在哪天，跨零点未结束的事前一天只记开始（细节链到[人物](06-agent.md)「进度、待办与记忆」）；
  - 零点计划的回顾时段按同一个窗口取，左闭右开（第 11 节）。

**docs/design/09-io-formats.md**
- 「提示层的机器形状」的「记忆层」小节（01 建）里写近三天格子的一行：`近三天` 的键是启用的类别、按饮食、工作、生活的次序（只读空记忆层里为空对象），值是至多三格、按日期升序的数组；饮食格、生活格是封闭对象，键依次为 `日期`（YYYY-MM-DD）、`星期`（`WEEKDAY_NAMES` 之一）、`回顾`（1–200 字）；工作格在 `星期` 与 `回顾` 之间另有必有的 `安排`（工作日或休息日）；未整理的格 `回顾` 为 null，末尾带 `未整理: true`；那天这一类没有记录时不写这一格；Schema 是 `_RECENT_CELLS`。

**docs/design/03-resource-engine.md**
- 「环境目录文件」表：`app-catalog.jsonl` 一行（:32）末尾加「，可选的记忆类别覆盖 `memory_category`」；`content-catalog.jsonl` 一行（:35）末尾加「；任何内容都可以另写记忆类别覆盖 `memory_category`」。`manifest.json` 一行（:31）与 :40 的格式号由第 08 章改写。
- :40 之后新增一段：`memory_category` 取「饮食」「工作」「生活」之一，省略表示按推导规则，只用于记忆的类别（链到[人物](06-agent.md)「记忆的类别」规则 0），不参与索引、准入、结算、名称解析和任何提示层；写成 `null` 由行模型拒绝；同一系列各集必须相同。
- :47「未知字段不忽略、不翻译；旧格式直接拒绝。」不改。
- 拒绝码表 `ENVIRONMENT_KIND_INVALID` 一行加：`memory_category` 写成 `null`（详情带 `A declared memory category must be a category name.`）或闭集外的值；`CONTENT_SERIES_INVALID` 一行加：各集 `memory_category` 不同时详情为 `<series.title> episodes declare different memory categories.`。

**docs/manual/resources.md**
- :9 与 :169 的格式号由第 08 章改写。
- :77「不要添加内容分类、manner、……」改为「不要添加内容分类（记忆的类别只用下文的 memory_category 覆盖）、manner、……」。
- 在「凭状态与完成效果」一段（:117-150）之后新增一段，写给包作者：人物的记忆按饮食、工作、生活三类整理，类别由引擎推导；推导不合意时给内容行或 app 行写 `memory_category`；举 `office-eat-lunch` 写 `"memory_category":"饮食"` 的例子行；系列各集写同一个值；写 null 会被拒绝；推导结果用 tools/analysis/memory_categories.py（09）逐行查看。

## 测试

### 新增

**tests/unit/memory/__init__.py**（空）与 **tests/unit/memory/test_categories.py**。用例用 `copy_environment`、`grocery_environment`、`meal_environment`（tests/life/environment_inputs.py）造环境，`load_environment` 后 `category_book`，活动快照用 `ActivityState` 直接构造，`goods_ids` 直接传入；不构造 `WorldState`。下文「`c-walk`」指样例副本给 `park` 类型加的一条不补能、不凭状态的地点内容（`content_id` 为 `c-walk`，标题「公园散步」，600 秒，其余字段照 `c-chat`）。

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_memory_categories_are_food_work_life_in_this_order` | `MEMORY_CATEGORIES == ("饮食", "工作", "生活")` 且等于 `get_args(MemoryCategory)` | 第 1 节 |
| `test_food_goods_are_meal_snack_drink_and_resets_of_food_states` | `grocery_environment`（`groceries` 由「做饭」凭、写了 food，扑克牌重置它）：扑克牌、牛肉面、薯片、柠檬茶在 `food_goods`，纸巾、平台笔记本不在；`food_names` 按商品身份升序 | 第 2 节 |
| `test_a_state_consumed_without_a_food_effect_is_not_a_food_state` | 另登记 `detergent`（纸巾重置），由一条不补能的家中内容凭它：纸巾不在 `food_goods`，那条内容不在 `food_units` | 第 2 节 |
| `test_the_whole_dinner_series_is_one_food_unit` | `meal_environment`：「做晚饭」三集都在 `food_units`，包括没有 food、也不凭状态的第 3 集 | 第 2、3 节规则 3 |
| `test_sleep_is_life_even_at_the_workplace_during_duty` | 工作日 10:00 在公司开始的睡眠 → `Derivation("生活", 1)` | 规则 1 |
| `test_start_content_override_wins_over_the_location_rules` | 公司内容 `topic-sales` 加 `"memory_category":"饮食"`，工作日 10:00 在公司开始 → `("饮食", 0)` | 规则 0 |
| `test_app_override_applies_when_the_start_content_has_none_and_content_override_wins` | 哔哩哔哩 app 行写「工作」：开始内容没有覆盖时 → `("工作", 0)`；开始内容写「生活」时 → `("生活", 0)` | 规则 0 |
| `test_purchase_is_food_when_any_goods_is_food` | 购买快照配 `goods_ids=["tissue"]` → `("生活", 2)`；`["tissue", "lemon-tea"]` → `("饮食", 2)`；`[]` → `IntegrityError("Memory category needs at least one goods")` | 规则 2、第 4 节 |
| `test_purchase_goods_come_from_the_start_result_orders` | 开始结果事件的 `data["data"]["orders"]` 有订单 `o-2`（柠檬茶）、`o-1`（纸巾）和一张 `station_pickup`、`delivered` 的领取订单：`activity.data["order_ids"] == ["o-2", "o-1"]` 时 `purchase_goods` 为 `("tissue", "lemon-tea")`；`order_ids` 含领取订单的身份或不在结果里的身份 → `IntegrityError("Purchase activity names an order that is not in its start result")`；interact 快照 → `()` | 第 3 节「输入」 |
| `test_query_category_is_fixed_by_targets_not_by_the_answer` | 目标「商品」类别、`data["answer"]` 里全是食品 → 生活；目标「饮料」类别 → 饮食；目标是牛肉面 → 饮食 | 规则 2、第 4 节 |
| `test_commute_between_home_and_workplace_on_a_workday_is_work` | 周一家→公司、公司→家 → `("工作", 4)`；周六同样的出行 → `("生活", 7)`；周一公司→五里公园 → `("生活", 7)` | 规则 4 |
| `test_activity_at_the_workplace_on_a_workday_is_work_at_any_hour` | 周一 07:00、12:30（休息时段）、23:00 在公司开始的地点内容 → `("工作", 5)`；周六在公司 → `("生活", 7)` | 规则 5 |
| `test_away_from_home_during_duty_is_work_and_during_a_break_is_life` | 周一 10:00 在五里公园开始 `c-walk` → `("工作", 5)`；12:30 → `("生活", 7)`；周一 10:00 在家开始 `c-chat` → `("生活", 7)` | 规则 5 |
| `test_a_food_unit_at_the_workplace_on_duty_is_food_before_work` | 样例副本给 `topic-sales` 加 `"completion_effects": {"food": "meal"}`：周一 10:00 在公司开始 → `Derivation("饮食", 3)`；`row_categories` 中它是 `(饮食, 3)`；同一副本里被拒的「在公司做季度销售数据整理」、周一 10:00 → 饮食 | 规则 3 先于规则 5、第 5 节 |
| `test_a_rejected_location_request_uses_the_content_override` | `c-chat` 写 `"memory_category":"工作"`：被拒的「在家做测试闲聊」（`target` 为「家」、`detail` 为「测试闲聊」）→ 工作 | 第 5 节规则 0 |
| `test_app_content_requiring_the_workplace_kind_is_work_and_other_apps_are_life` | 一条哔哩哔哩内容前提 `location_kinds: ["office"]` → `("工作", 6)`；其余哔哩哔哩内容在公司、在岗时段 → `("生活", 6)` | 规则 6 |
| `test_category_follows_the_start_moment_and_the_start_content` | 一次 app 使用自动接到第二条（`played` 一项、`content_id` 换了）：类别按第一条；周一 08:50 在五里公园开始 `c-walk`、跨过 09:00 的活动 → `("生活", 7)` | 第 3 节判定时点 |
| `test_without_a_work_schedule_nothing_derives_work_and_work_is_disabled` | 去掉 `work_schedule`：通勤、在公司、要求 office 的 app 内容都不是工作；`categories == ("饮食", "生活")` | 第 3 节表末、第 8 节 |
| `test_a_work_override_enables_the_work_category_without_a_schedule` | 去掉 `work_schedule`，一条内容写「工作」：`categories == MEMORY_CATEGORIES` | 第 8 节 |
| `test_errand_lines_take_the_category_of_the_errand_goods` | `goods_category(book, ["lemon-tea"]) == "饮食"`，`["tissue"]` → 生活；`[]` → `IntegrityError("Memory category needs at least one goods")` | 第 4 节 |
| `test_rejected_purchase_and_query_use_rows_then_the_text_rule`，参数化 | 查询「盖浇饭」「一人份食材包」「奶茶」「饮料」→ 饮食；查询「商品」→ 生活；购买「纸巾」（对上商品）→ 生活；购买「洗衣液」（对不上，无字词）→ 生活；购买「纸巾」「冰美式」→ 生活；购买「纸巾」「柠檬茶」→ 饮食；名称只差空白与大小写也算对上 | 第 5 节 |
| `test_rejected_start_requests_resolve_catalog_rows` | 被拒的周一出行「公司」、观察的 `location_id` 是家 → 工作；目的地对不上 → 生活；被拒的在公司做 `topic-sales`、周一 10:00 → 工作；单元名对不上 → 生活；被拒的睡眠 → 生活；被拒的 app（app 行写「饮食」）→ 饮食；app 名对不上 → 生活；同一决策两个观察事件 → `IntegrityError("Rejected request has no unique decision observation")` | 第 5 节 |
| `test_rejected_control_requests_and_plan_revisions_are_life` | 三个控制工具与 `revise_day_plan` → 生活 | 第 5 节 |
| `test_primary_request_of_a_start_group_is_the_main_slot` | `[side, main]` → main；`[conclude]` → conclude；`[]` → `IntegrityError("Decision has no world request")` | 第 7 节 |
| `test_request_category_follows_the_started_or_named_activity` | `activity_categories` 给两项活动不同的类别：受理的开始 → `activity_categories[result.activity_id]`；被拒的开始（`activity_id` 为 None）→ `rejected_category`；受理的收尾 → `activity_ids[0]` 那项的类别；被拒的收尾 → 生活；`revise_day_plan` → 生活；活动身份不在 `activity_categories` 里 → `IntegrityError("Memory category names an activity that was not read")`；函数签名里没有 `WorldState` 形参 | 第 6、7 节 |
| `test_category_label_puts_the_primary_first_and_dedupes_in_order` | `("工作", ["饮食", "工作"]) → "工作、饮食"`；`("饮食", ["饮食"]) → "饮食"`；`(None, ["生活", "饮食", "生活"]) → "生活、饮食"`；`(None, [])` → `IntegrityError("Memory entry has no category")` | 第 7 节 |
| `test_row_categories_list_contents_then_apps_with_rules` | 样例环境加 `meal_environment` 的系列和一条 office 前提的哔哩哔哩内容：内容行按身份升序在前、app 行在后；`topic-sales` → (工作, 5)，「做晚饭」三集 → (饮食, 3)，`c-rest`（写了 food）→ (饮食, 3)，office 前提内容 → (工作, 6)，哔哩哔哩 app 行 → (工作, 6)，抖音 app 行 → (生活, 6)，`c-chat` → (生活, 7) | 第 9 节 |
| `test_memory_book_is_built_once_per_store_and_rebuilt_for_new_inputs` | 同一存储、同一 `inputs` 两次调用返回同一对象；换一个 `inputs` 对象时重建 | 第 2 节 |
| `test_line_and_item_categories_follow_the_two_tables`，参数化第 6 节两张表的每一行 | 在手写事件的存储上经 02 的 `agent_events`、`decision_commits` 与 `build_entry` 建条目，断言各 `EntryLine.category`、`SelfItem.category` 与 `TodayEntry.category`。必须包含三例：工作日在公司 report_status「季度销售数据整理」，窗口里送达柠檬茶 → 行类别 [饮食]，项类别 [工作]，类别串「工作、饮食」；收尾公司里的 `topic-sales` 时写对象为卡片人物「老周」的关系看法 → 这一项为工作；收尾在家的 `c-chat` 时写同一对象的看法 → 生活。另含：购买行只按已付款订单的商品；个人事务变更行按事务商品；被拒的控制请求只有被拒行与正文，二者都是生活 | 第 6、7 节，裁定 4 |

**tests/unit/catalog/test_memory_category_declarations.py**（照 tests/unit/catalog/test_location_content_declarations.py 的 `refused` 写法，经 `load_catalog` 与 `validate_catalog_files` 两个入口）：

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_contents_and_apps_may_declare_a_memory_category` | 一条内容写「饮食」、一个 app 写「工作」：加载后字段值相同；未写的行是 None | 第 9 节 |
| `test_memory_category_shape_is_refused_by_the_row_model`，参数化 | 内容行与 app 行各：`null`（详情含 `A declared memory category must be a category name.`）、`""`、`"休息"`、`1`、`["饮食"]`（详情含 `Input should be '饮食', '工作' or '生活'`）→ `ENVIRONMENT_KIND_INVALID`，详情以 `content-catalog.jsonl:<行号> ` 或 `app-catalog.jsonl:<行号> ` 开头并含 `memory_category` | 第 9 节 |
| `test_series_episodes_must_declare_the_same_memory_category` | 三集系列只有第 2 集写「饮食」→ `CONTENT_SERIES_INVALID`，详情 `做晚饭 episodes declare different memory categories.`；三集都写「饮食」→ 加载成功 | 第 9 节 |

**tests/unit/memory/test_days.py**

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_day_window_is_left_closed_right_open_at_local_midnight`，参数化 `Asia/Shanghai`、`America/New_York`、`UTC` | `day_window("2026-10-10", zone)`：`start` 是当地零点；`contains(start)`、`contains(start + 86399)` 为真；`contains(start - 1)`、`contains(end)` 为假；`end == day_window("2026-10-11", zone).start` | 第 10 节 |
| `test_shift_date_and_recent_dates` | `shift_date("2026-10-01", -1) == "2026-09-30"`；`recent_dates("2026-10-08") == ("2026-10-05", "2026-10-06", "2026-10-07")` | 第 10、13 节 |
| `test_day_bound_is_the_last_event_before_local_midnight` | 照 tests/unit/life/test_memory_progress_contract.py:268-295 的写法造存储，事件依次在 10-09 23:59:59、10-10 00:00:00、10-10 00:10:00：`day_bound(store, "2026-10-10", zone, now)` 等于 23:59:59 那条的序号；`day_bound(store, "2026-10-09", …)` 为 0；序号 (B_10, B_11] 恰是 00:00:00 与 00:10:00 两条 | 第 10 节，四个时点之 23:59:59、00:00:00、00:10 |
| `test_day_bound_refuses_a_day_that_has_not_started` | `now` 为 10-10 23:59:59 时读 10-11 的零点界 → `IntegrityError("Day bound is read before the day starts")` | 第 10 节 |
| `test_day_digests_read_day_events_up_to_the_cutoff` | 存储里依次登记 10-07 日回顾（`food` 有正文、`work` 为 null 且在 `fallback`、`life` 为 null 不在 `fallback`）、2026-09 月回顾、10-08 日回顾；`cutoff` 取 10-08 那条之前的序号：结果恰为 `{"2026-10-07": DayDigest({"饮食": 正文}, frozenset({"工作"}))}`，月回顾与序号大于截止的日回顾都不读；`cutoff` 取末序号时两天都在、键按日期升序 | 第 12 节 |
| `test_local_midnight_is_computed_only_by_day_window` | `readable` 模块没有 `local_day_start` 属性；`progress_layer` 模块源码不含 `local_day_start`、含 `day_bound(`；`day_plan_inputs` 模块源码不含 `replace(hour=0` | 第 10 节「当日拒绝」、第 11 节、「一条规则一处」 |

**tests/unit/memory/test_recent_days.py**（`DayDigest` 直接构造，不经存储）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_food_and_life_take_the_three_calendar_days_before_today` | 10-05 到 10-07 三天都有饮食、生活正文：两类各三格，日期升序；10-04 的正文不出现 | 第 13 节 |
| `test_work_takes_the_last_three_days_with_work_records_across_a_holiday` | 09-28、09-29、09-30 工作有正文，10-01 到 10-07 工作没有记录，今天 10-08：工作格是 09-28、09-29、09-30，饮食与生活格只取 10-05 到 10-07 | 第 13 节、裁定 10 |
| `test_an_unsettled_work_day_counts_as_a_work_record_day` | 09-30 工作在 `unsettled`：它算作有工作记录，格子是 `{"日期":"2026-09-30","星期":"星期三","安排":"工作日","回顾":null,"未整理":true}` | 第 12、13 节 |
| `test_an_omitted_cell_and_an_unsettled_cell_differ` | 同一天饮食没有记录、生活未整理：饮食列表里没有这一天，生活列表里是带 `"回顾":null,"未整理":true` 的格子；渲染（`json.dumps(…, ensure_ascii=False, separators=(",", ":"))`）逐字节等于预期 | 第 13 节、提案 :187 |
| `test_cells_have_the_fixed_keys_in_order` | 饮食格键序是 日期、星期、回顾；工作格是 日期、星期、安排、回顾；未整理格在最后多「未整理」；周六补班日 `安排` 为「工作日」，周一假日为「休息日」；没有工作安排时工作格的「安排」是「休息日」 | 第 13 节 |
| `test_the_work_key_is_absent_when_work_is_disabled` | `categories == ("饮食", "生活")` 时整块只有两个键 | 第 8、13 节 |
| `test_the_first_day_has_empty_lists` | 没有任何 `DayDigest`：`{"饮食": [], "工作": [], "生活": []}` | 第 13 节「头三天」 |
| `test_a_day_digest_cannot_be_written_and_unsettled` | `DayDigest({"饮食": "…"}, frozenset({"饮食"}))` → `IntegrityError("Day digest marks a category both written and unsettled")` | 第 12 节 |
| `test_recent_cell_schema_accepts_only_the_two_cell_shapes` | 经 `validate_layer` 校验整个记忆层（`assemble_layer("2026-10-01", 近三天, [])`），在「近三天」里放入所列各格：接受上述各格；拒绝（`ValueError`）：饮食格带「安排」、工作格缺「安排」、「回顾」201 字、「回顾」为 null 而没有「未整理」、「未整理」为 false、四格 | 第 13 节、prompt_schemas.py |

**tests/unit/life/test_day_plan_window.py**

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_review_window_is_yesterday_at_midnight_and_today_so_far_at_startup` | 10-10 00:00 零点边界：`DayWindow("2026-10-09", 10-09 00:00, 10-10 00:00, zone)`；10-10 08:00 启动：`DayWindow("2026-10-10", 10-10 00:00, 10-10 08:00, zone)`；启动在 00:00：空时段 | 第 11 节 |
| `test_conclusion_records_follow_the_half_open_day` | 用 `_AgentHistory` 直接构造的受理收尾请求在 10-09 00:00:00、10-09 23:59:59、10-10 00:00:00：10-10 零点的计划只取前两条 | 第 11 节，四个时点之 23:59:59、00:00:00 |
| `test_previous_goals_still_match_the_plan_ending_at_this_midnight` | `day_end` 等于 10-10 00:00 的计划快照被取到，`day_end` 等于 10-11 00:00 的不取 | 第 11 节 |
| `test_day_plan_inputs_keeps_one_window_predicate` | `day_plan_inputs` 模块没有 `_Window` 属性；`_review_window(...)` 是 `DayWindow` 实例；模块源码里没有 `covers(`、`ends_within(` | 第 11 节 |

**tests/integration/life/test_day_attribution.py**（引擎夹具 `engine_factory`，写法照 tests/integration/life/test_sleep_runtime.py:382-399）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_a_sleep_ending_exactly_at_midnight_is_left_out_of_that_midnights_plan` | 运行从 2026-01-05 20:00 开始，睡 240 分钟到 01-06 00:00：01-06 零点的 `plan_inputs(context, observations, True, 1)` 的「动作结果」和经历里都没有这段睡眠的结束句；`day_window("2026-01-06", zone).contains(结束时刻)` 为真 | 第 10、11 节，四个时点之「区间结束在 00:00:00」 |
| `test_an_overnight_sleep_ends_on_the_wake_day` | 23:30 开始、07:30 结束的睡眠：结束事件归醒来那天，`day_bound` 分出的前一天序号区间里没有它的结束事件 | 第 10 节「跨夜睡眠」 |
| `test_no_commit_spans_a_local_midnight` | 跑过一个零点的运行里，按 `(state_version_before, state_version_after)` 分组的每次推进版本的提交，组内事件的 `local_date(occurred_at)` 都相同 | 第 10 节 |
| `test_recent_days_and_searchable_stay_byte_identical_through_a_day` | 跨过一个零点后用 04 的 `settle_pending_memory_digests` 与测试替身 `StubMemoryDigestClient` 生成前一天的日回顾，新的一天里三次观察的 `render_layer_json(memory_layer["近三天"])` 与 `memory_layer["可查"]` 逐字节相同，且「近三天」带前一天的格子 | 第 13 节「稳定性」、提案 :827 |

**WorldMock-Dataset：tests/test_white_collar_package.py**（包 0001 的覆盖本身由 08 加入）

| 测试 | 断言 | 条款 |
| --- | --- | --- |
| `test_memory_categories_of_every_row_match_the_pinned_table` | `row_categories(category_book(inputs))` 写成 `{"contents": {content_id: [category, rule]}, "apps": {app_id: [category, rule]}}`，经 `canonical_json` 后逐字节等于 tests/fixtures/memory-categories-0001.json（新建，内容由 `row_categories` 在格式 6 的包 0001 上生成） | 第 9 节、提案 :369 |
| `test_office_lunch_override_and_the_rows_the_proposal_names` | `office-eat-lunch` → (饮食, 0)；`home-dinner-1-cook`、`home-dinner-2-eat`、`home-dinner-3-dishes` → (饮食, 3)；`restaurant-team-dinner` → (饮食, 3)；`work-feature-dev-selftest` → (工作, 5)；`dingtalk-clock-in`、`dingtalk-clock-out` → (工作, 6)；`home-laundry` → (生活, 7)；app 行 `dingtalk` → (工作, 6)，`douyin`、`phone` → (生活, 6)；`kit-dinner-chestnut-chicken`、`item-detergent` 分别在、不在 `food_goods` | 第 2、3、9 节 |

### 改动

| 测试 | 改动 | 条款 |
| --- | --- | --- |
| tests/unit/life/test_memory_progress_contract.py | 删去 :23 的 `local_day_start` 导入与 :260-265 的 `test_local_day_start_is_the_scenario_local_midnight`（由 `test_day_window_is_left_closed_right_open_at_local_midnight` 取代）；:268 的 `test_rejected_today_counts_only_evidence_from_local_midnight_to_now` 不改并照旧通过 | 第 10 节 |
| tests/integration/life/test_history_reads.py:109-125 | 不改；`_review_window` 返回的 `DayWindow` 照旧交给各内部函数，`planned == expected` 照旧成立 | 第 11 节 |

目录格式 6 的钉住（tests/unit/catalog/test_manifest_version.py、test_loader.py:84、test_identity.py:115、样例环境清单、tests/dataset_acceptance.py:291-294 的变异）由第 08 章改。

任何把零点那一秒的事件同时算进前后两天、或把前一日 00:00:00 的经历算给更前一天的既有断言，都按第 11 节改写；在基线上按 `covers`、`ends_within` 全仓检索测试目录，除上表外没有直接引用。

## 不在本章

- 今天条目的行与项怎样生成、窗口 C_j、核验短句、规则 B、全文、零点收尾条目、按类别切分条目：02 今天条目。
- 记忆层的形状、「可查」与 `earliest_date`、预算、兜底与层序：01 记忆层：形状、预算、兜底与层序。
- 回顾事件 `memory_digest_generated` 的正文、英文键与中文类别的对应关系、读取与增量缓存、生成、校验、幂等、恢复补齐、工作类不启用时的回顾：04 日、月、年回顾。
- 按日期、区间、月、年和「今天」检索：05 search_memory 与工具面。
- 进度层的已发布界（除 `rejected_today` 的下界一处）、待办层、同秒续接：06 进度层、待办层与同秒续接。
- 系统层 rules[1] 里讲类别、近三天和「未整理」的原文，以及 `MEMORY_LAYER_SCHEMA` 的其余部分：07 人设层与系统层规则。
- 包 0001 的 `office-eat-lunch` 覆盖与 home-dinner-1-cook 的摘要，五个包按格式 6 重建与 `environment_version` 升号，构建器的 `FORMAT_VERSION`，LabelKit 的 `CATALOG_FORMAT_VERSION` 5→6 与给对方会话的通知，「睡醒日减一」的退役：08 退役项、食材包句与环境包。
- tools/analysis/memory_categories.py 的输出格式和 memory_pilot.py：09 分析工具、运行比对与查看器。
- 版本、指纹、两候选重冻、门禁：10 实施：版本、文件、设计文档、测试与门禁。
- 为 0002–0005 包补覆盖（提案 P4）。

## 规格阶段的决定

| 问题 | 决定 | 依据 |
| --- | --- | --- |
| 「食品状态」提案没有定义 | 至少一条凭它的内容写了 `completion_effects.food` 的回调状态；这一定义只在第 08 章 `Catalog.food_states()` 实现，本章调用它（10 章跨章裁定） | 规则 3「消耗食品状态」举的做饭与同事聚餐都是这样；洗衣状态不是食品；研究目录 out3/entries/entries.py 的 `FOOD_STATES` 是同一定义 |
| interact 活动按哪条内容判定 | 开始内容：`played` 第一项，没有时取 `content_id`；地点内容的规则 3 按整个单元 | 「时刻一律取活动开始的时刻」；「整套做饭（包括洗碗那一集）」；自动接续会改 `content_id`（interact.py:206-221） |
| 同一系列各集的 `memory_category` 不同 | 登记以 `CONTENT_SERIES_INVALID` 拒绝 | 规则 0 要求单元只有一个覆盖值；与各集 `consumes_state` 必须相同的现行检查（admission.py:251-252）同一写法 |
| 显式 `"memory_category": null` | 行模型拒绝 | 目录现行写法：`consumes_state`、`completion_effects` 写 null 都拒绝，不覆盖只有省略一种写法（models.py:192-249） |
| 内容 app 的规则 0 有内容行与 app 行两处 | 先内容行，后 app 行 | 提案规则 0「目录行带 `memory_category` 时直接采用」不给两处的次序；规则 3、6 都按开始内容判定，规则 0 先查同一条开始内容的行与之一致 |
| 购买、查询所在或下单的 app 行是否按规则 0 覆盖 | 不覆盖；规则 0 只读 interact 的开始内容行与所用内容 app 行 | 提案规则 2 明写购买、收货、送达、查询按商品与目标判定，『查询的类别从开始起就定下』；购买活动的数据不带 app，一次购买的各订单可以各带不同的 `app_id`（09-io-formats.md「商品履约记录」），查询的 `app_id` 可空，没有唯一的一行可覆盖 |
| 规则 6 落到 app 行上（被拒的 app 请求、静态表）怎样判 | app 有任一内容的前提要求班次地点类型即工作 | 规则 6 原文「app 的某条内容要求班次地点类型」；被拒时没有选出内容 |
| 规则 5 的「在岗时段」 | `duty_status == STATUS_ON_DUTY` | 原文「在岗时段内（不含写明的休息时段）」；`duty_status` 的四个状态（work_schedule.py:27-31） |
| 被拒的控制请求 | 生活 | 被拒规则只按目录行判定，活动名不是目录行；原文「其余归生活」 |
| 「目标能对上目录行」怎样比较 | `channel_key` 相等 | 03-resource-engine.md「名称比较经 channel_key 去全部空白并忽略大小写」 |
| 被拒出行的出发地 | 这次决策观察事件的 `location_id` | 条目只依赖已提交事件；`read_decision_observations` 已有；被拒请求不改位置 |
| 被拒购买、查询里部分名称对上、部分对不上 | 逐个名称判定，任一为饮食即饮食；对上的名称不再看文字 | 原文按「对上」「对不上」分别给规则；一行只有一个类别 |
| 一次购买、送达或收货里有食品也有非食品 | 归饮食 | 一次购买一行，一行只有一个类别；研究目录 entries.py 同口径 |
| 个人事务变更（新增、更新、取消）的类别 | 按事务商品，与「办成」同一规则 2 | 个人事务只记商品（04-simulation-engine.md「唤醒、日计划与个人事务」）；提案只列了「办成」 |
| 开始组那一行的类别 | 槽位 1 那一项 | 提案示例 08:34、08:54 的「类别」只写「工作」 |
| 「类别」串在哪个范围上算 | 全文：全部核验行，再全部自述项（含 routine 与规则 B 省略的项）；渲染与全文同一串 | 01 的兜底按「同一类别串」合并，串当天不能变；提案的全文形状也带「类别」 |
| 主请求被拒或是控制请求时的类别 | 被拒取被拒规则；受理的控制请求取第一项所点名的活动 | 原文「正文继承本次决策第一个世界请求所指活动的类别」 |
| 「近三天」格子的键 | 日期、星期，工作格另有「安排」，回顾，未整理格另有「未整理」 | 提案 11:50 示例（:681-693，只有工作格带「安排」）与「九格连同日期、星期等键」（:163） |
| 没有工作安排而工作类因覆盖启用时，工作格的「安排」 | 写「休息日」 | 本章 `_recent_cells(True)` 规定工作格必有「安排」；没有班次即不当班（`shift_on` 为 None 时的写法，work_schedule.py:78-79） |
| 未整理格是否带星期、安排 | 带，只把回顾换成 null 并加「未整理」 | 同一格位、同一天；`_recent_cells` 的两种形状共用同一组日期、星期、安排键 |
| 某类当前没有格子 | 键在、值为 `[]`；工作类不启用时没有「工作」键 | 原文「不渲染工作类」只针对工作类不启用；01 的 Schema 允许空数组 |
| 「有工作记录的日子」怎样判 | 那天日回顾事件的工作类有正文或在 `fallback` 里 | 04：某一类那天没有条目时为 null 且不进 `fallback`；不必每次观察重读往日事件 |
| 「近三天」从回顾事件读什么 | 本章定义 `DayDigest`、`day_digests` 与 `recent_block`；`day_digests` 只经 04 的 `registered_digests` 读事件，回顾事件的读取与缓存归 04；05 的 `SearchDigest` 是检索一侧的同类转换 | 04:334 把回顾事件的唯一读取入口定为 `registered_digests`，「第 03 章……按自己的截止序号筛选」；01 的 `memory_view` 代码要一个有名有签名的读法 |
| 「可查」的最早日期由谁定义 | 01 的 `earliest_date`，本章不另设 | 01 已定义并测试；05 引用同一个函数 |
| 英文键 `food`、`work`、`life` 与中文类别的对应 | 用 04 定义的 `MEMORY_DIGEST_KEYS` 与 `MEMORY_CATEGORIES` 按下标对应，换算式与 05 的 `memory_sources` 相同 | 04:472「与 MEMORY_CATEGORIES 逐一对应」；05:331 的同一换算式 |
| 零点界在当天开始之前被读取 | 抛 `IntegrityError` | B_D 只有时钟到达 D 日零点后才确定，提前读会得到会变的值 |
| 零点计划启动路径的窗口 | [当天零点, 生成时刻) | 原文「统一成左闭右开」；生成时刻那一秒的事件归生成之后的部分；「回顾时段」的文字不变 |
| `local_day_start` 的去留 | 删除，`rejected_today` 改用 `day_bound` | 一条规则只在一处；删除后它没有调用方 |
| 零点界在 02、06 与本章曾有不同写法（起草时 02 写过 `day_bound(store, cached, day_start)`，02【E3】与 06【P1】写过 `local_day_start` 的式子；两章现已改为调用本章函数） | 只有本章的 `day_bound(store, day, timezone, now)`；02 的 `AgentEvents.day_bounds` 以日期串为键缓存它的返回值；02【E3】【E6】、06【P1】改为调用它 | 提案「归日规则」与设计原则第 2 条：全系统只用一条归日规则；本章删除 `local_day_start` |
| days.py 的导入边界 | 不导入 progress_layer.py、memory_layer.py、day_plan_inputs.py；可导入 entries.py（02）、categories.py 与 digests.py（04），它们的传递导入同样不含这三个模块 | 02 把 `published_today`、`past_day_entries` 放进 days.py 并导入 entries.py，entries.py 调用 categories.py；day_plan_inputs.py 与 progress_layer.py 导入 days.py |
| 类别推导读不读 `WorldState` | 不读：活动用 02【E12】的快照，购买商品用开始结果里的订单（`purchase_goods`），`request_category` 收调用方算出的 `activity_categories` | 提案 :317「条目文字只依赖序号 ≤C_k 的事件和冻结目录」，「类别」串是条目文字；02:99「条目构建……不读 `WorldState`」；05:331「不读 `context.state`」 |
| `request_category` 怎样得到控制请求点名活动的类别 | 调用方传 `activity_categories`（活动身份 → 类别），不传快照 | 控制结果只带 `activity_ids`（dispatcher.py:257-268），点名的若是购买活动，商品要从它自己的开始结果里取；参数上限 5 个 |
| 购买行的类别 | 这一行所写已付款订单的商品按 `goods_category` | 提案规则 2「购买……：商品属于 meal、snack、drink……归饮食」按商品判；02【E12】的购买行只写已付款的订单 |
| 恢复时补齐哪些日回顾 | 由 04 规定（日历上的 T−1…T−3 与最近三个工作类有条目的日子取并集）；本章的 `recent_dates`、`recent_work_dates` 只用于渲染 | `recent_work_dates` 只读已登记的回顾，认不出尚无回顾的日子；04:83-87、04:647 |
| 功能 app 行写 `memory_category` | 不另加登记检查 | 提案没有限制；被拒请求点了它的名字时照规则 0 读，并与内容 app 行一样计入第 8 节与第 9 节 |
| 「近三天」格子的 Schema | `_RECENT_CELLS` 按类别给数组 Schema，工作格必有「安排」；`MEMORY_LAYER_SCHEMA` 的「近三天」写成 `_object(_RECENT_CELLS, optional=MEMORY_CATEGORIES)` | 01（:494、:502、:881）与 07（:214、:299、:535）把 `_recent_cells`、`_RECENT_CELLS` 划给本章；提案 :686-688 只有工作格带「安排」 |
| 「近三天」回顾正文的 200 字在 Schema 里 | `maxLength: 200` | 与 04 的日回顾上限同值；Schema 只做逐串上限（07） |
