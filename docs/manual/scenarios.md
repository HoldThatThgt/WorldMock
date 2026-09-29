# 生活场景

生活场景决定模拟谁、从什么时候到什么时候、有哪些生活机会，以及世界开始时的状态。
从[手册首页](README.md#创建自己的第一个场景)复制现有场景开始，按本页调整自己的条件。
人物具体怎么过一天仍由人物决定，场景用于提供环境和机会。

[返回手册](README.md) · [人物画像](personas.md) · [世界素材](resources.md)

## 选择起点与文件位置

| 现成场景 | 适合用来修改什么 |
| --- | --- |
| [白领十二周](../../scenarios/white-collar-12-weeks.json) | 单人物、工作合同、工资和生活支出；首页示例由它裁出一天 |
| [多人场景](../../scenarios/multi-person.json) | 多人物与按人分配工作 |
| [多人半天](../../scenarios/multi-person-half-day.json) | 短时段、多人、没有工作任务的生活观察 |

新场景放在仓库的 `scenarios/` 下。解析器用场景的上两级目录定位 `catalog/`，
场景中的 `corpus_root` 则相对场景所在目录解析。不要把场景移动到 `scenarios/custom/` 等更深目录而不调整整个项目布局。
`schema_version` 当前固定为 `3`；多余字段会被拒绝。

## 看懂场景的组成

```mermaid
flowchart TB
    SCENE[场景] --> PEOPLE[人物与身体初值]
    SCENE --> TIME[时间窗与日历]
    SCENE --> WORLD[地图、商品、供给和素材引用]
    SCENE --> EVENTS[任务、消息、工资、账单与供给变化]
    SCENE --> MENU[地点与人物的交互菜单]
    PEOPLE --> LOAD[校验身份、时间、单位和相互引用]
    TIME --> LOAD
    WORLD --> LOAD
    EVENTS --> LOAD
    MENU --> LOAD
```

以下字段均为顶层配置。表中“必填数组”即使没有条目也应保留 `[]`，不能直接删掉字段。

| 字段组 | 必填情况与用途 |
| --- | --- |
| `schema_version`、`scenario_id`、`timezone`、`start_at`、`end_at`、`random_seed` | 必填；格式版本、场景身份、时间与随机种子 |
| `activity_feedback_seconds`、`rotation_stale_seconds` | 必填；反馈间隔与长期未呈现阈值 |
| `corpus_root`、`corpus_manifest_sha256`、`catalog_version`、`map_dataset` | 必填；语料、目录版本和地图身份 |
| `accounts` | 必填非空数组；商家、雇主、出行服务和外部账户 |
| `body`、`agents`、`interaction_menus_by_location_kind` | 必填；身体初值、人物和每个地点类的菜单 |
| `locations`、`products`、`supplies` | 必填数组；选中的地点、商品与门店供给 |
| `tasks`、`messages`、`contents`、`games` | 必填数组；工作、通知与可接触素材 |
| `calendar` | 必填；特殊日期与生活情境 |
| `bills`、`payrolls`、`price_changes`、`product_releases` | 可省略，默认空数组；显式预排事件 |
| `recipe_output_bindings` | 可省略，默认 `{}`；有库存配方时把输出品类绑定到输出对象 |

## 修改日期与模拟节奏

下面是替换顶层同名字段的局部片段：

```json
{
  "scenario_id": "my-persona-day",
  "timezone": "Asia/Shanghai",
  "start_at": "2026-01-05T00:00:00+08:00",
  "end_at": "2026-01-06T00:00:00+08:00",
  "random_seed": 20260105,
  "activity_feedback_seconds": 900,
  "reconsideration_interval_seconds": 5400,
  "idle_reconsideration_interval_seconds": 10800,
  "max_same_moment_decisions": 3,
  "candidate_presentation_limit": 20,
  "candidate_pool_limit": 1000,
  "rotation_quota_ratio": 0.15,
  "rotation_stale_seconds": 604800,
  "retrieval_index_threshold": 1000
}
```

起止时刻必须带时区偏移，并与 `timezone` 在该日期的实际偏移一致。结束晚于开始，运行窗口左闭右开。
缩短场景时，任务、消息和预排事件也需要一起调整；首页脚本展示了完整做法。
扩大或移动时间窗时，还要检查地图、路线与报价的有效期，不能只换日期数字。

| 参数 | 单位与约束 | 如何理解 |
| --- | --- | --- |
| `activity_feedback_seconds` | 秒，60–86400 | 结算间隔，也是等待、浏览、交谈的计划时长；不是保证每隔这些秒都会重新决策 |
| `reconsideration_interval_seconds` | 秒，默认 5400，范围 1–86400 | 清醒且有在手承诺时的常规重估间隔（进行中的等待不算在手承诺）；其他更早事件仍能触发决策 |
| `idle_reconsideration_interval_seconds` | 秒，默认 10800，范围 1–86400，不能短于常规间隔 | 人物只在等待、且没有其他在手承诺时的空闲重估间隔；调大常规间隔时也要检查此值 |
| `max_same_moment_decisions` | 正整数，缺省 3 | 同一秒内的决策轮数上限；达到上限后另有至多三轮受限轮：有刚结束的活动时只开放收尾，否则只开放开始或恢复（等待始终可选），名额被暂停活动占满时只开放恢复或结束，其余动作在工具层标为不可用并写明原因；开始或恢复被拒后留在原阶段；受限轮用完仍没有进行中活动或仍有待收尾活动时，环境不替人物造活动，写一条预算耗尽事件，`activity_feedback_seconds` 之后再给决策；不标失败 |
| `progress_report_interval_seconds` | 秒，缺省 1800 | 进行中活动的播报间隔 |
| `candidate_presentation_limit` | 默认 20，范围 1–1000 | 单次候选呈现上限 |
| `candidate_pool_limit` | 默认 1000，范围 1–100000 | 候选池上限 |
| `rotation_quota_ratio` | 默认 0.15，严格在 0 与 1 之间 | 为轮换呈现预留的比例；呈现上限乘比例向下取整必须至少为 1 |
| `rotation_stale_seconds` | 必填，范围 1–31536000 秒 | 多久未出现的对象进入长期未呈现判定 |
| `retrieval_index_threshold` | 默认 1000，范围 1–100000 | 切换检索索引策略的规模阈值 |

呈现窗口只决定人物本轮看到哪些对象，不是资格上限：没有列出的生活活动、内容和商品，人物写出名称也能调用，受理照常核验。
窗口内先按口味权重乘注意力排序，再按相关度：生活活动的时间窗覆盖此刻 +2，对应当日计划里尚未推进的目标 +2，
条目名或品类出现在人物卡片的习惯里 +1；仍同分时按含日期的哈希决胜，所以顺序随日期变化，也不是名称的字母序。
生活活动窗口只列当前地点环境合适的活动；环境不合适的不列出，按名写它时以「这里不适合做这件事」拒绝。

不要把反馈间隔当作“人物每十五分钟行动一次”。决策本身不耗时：没有进行中活动的人物在同一秒接着决策，
只有人人都有事在做时，时间才根据活动完成、消息、任务、身体变化和其他事件推进。
在手承诺包括尚未推进的当日目标、可见的未完成工作任务、暂停的活动与进行中的活动（等待除外）。
当日目标由零点计划生成：运行开始时和每个场景本地零点（运行恰在零点结束时除外），系统为每个人物调用模型生成当天的
有序目标，同一人物同一天只生成一次；人物醒着时可以修订。目标不填写时间段，每条带模型写的大致时刻 `around_time`
（场景本地 `HH:MM`，可为空）：写了时，只有进行时段与它前后 90 分钟有交集的同类活动才算推进这条目标。
只有 `remind_time`（场景本地 `HH:MM`，可为空）会在那一刻提醒人物，每条提醒只触发一次，能叫醒睡着的人；睡眠中除了提醒，只有睡够等专用唤醒条件才会结束睡眠。
规则见[当日目标规格](../dev/SPEC-day-plan-goals.md)与[时间与能量规格](../dev/SPEC-time-and-energy.md)第四章。

## 修改人物、身体与账户

人物字段及联系人详见[人物画像](personas.md)。增加人物时，为它分配唯一正整数 `agent_id`，
同步修改属于它的任务受理人、邮件收件人、工资和账单引用。

身体初值是顶层 `body` 对象，当前所有人物共享这份初值配置；睡眠需要和时型可以逐人物声明。
下面是完整 `body` 对象的局部配置：

```json
{
  "body": {
    "mass_kg": 65,
    "initial_energy_kcal": 1600,
    "initial_sleep_debt_hours": 0,
    "initial_entertainment_need": 0
  }
}
```

体重为大于 0 且不超过 300 的千克数；能量为非负千卡，且不超过目录能量上界；
睡眠不足初值为非负小时数。`initial_entertainment_need` 接受 0–1，
但当前运行初始化仍固定为 0，非零设置尚未生效。不要用它构造不同娱乐需要的实验。
`body` 还可写 `entertainment_relief_preference_floor`：娱乐缓解的下限偏好 f，缺省 0.5，严格在 0 与 1 之间，越界加载拒绝。
观看与玩游戏的实际缓解乘有效偏好 f + (1 − f) × 口味权重：口味对不上（权重 0）的内容也按 f 缓解，权重 1 的不变；
候选排序仍按原始口味权重。取值是实验假设（敏感区间 0.3–0.7），见[实验设置](../EXPERIMENT.md#实验假设声明)。
饥饿与困意由身体和时刻计算，没有可直接设置的“饥饿值”或“疲劳值”。
起点困意也不另设：系统按人物作息反推起点的睡眠压力（假定自最近一次惯常起床时刻起一直清醒），
公式与起点落在惯常睡眠时段时的效果见[人物画像](personas.md#在场景中填写人物)。
能量不阻止任何活动，结算最低为 0；低能量只让人更饿、做事更慢。饥饿显示有两档低能量规则：
能量低于目录紧急阈值（`energy_kcal` 的 `EMERGENCY_ENERGY` 告警规则，正式目录为 400 千卡）时至少「饿得难受」，
低于代码常量 100 千卡（`APPETITE_STARVING_KCAL`）时为「饿得发慌」；两档都不受时刻与餐时窗口影响。

人物账户由 `agent_id` 自动形成，例如 `agent:1`；余额来自 `initial_balance_fen`。
不要在 `accounts` 中再声明人物账户。下面是一个服务账户的完整条目：

```json
{
  "account_id": "employer:zhangjiang",
  "money_fen": 10000000,
  "role": "employer"
}
```

金额为整数分，`role` 必须满足所引用目录规则的账户角色要求。
保留模板所需雇主、交通和商家账户；账户删改后，供给、任务和事件里的引用也必须一致。

## 限制人物可以做什么

地点菜单在 `interaction_menus_by_location_kind` 中填写；键恰好覆盖已选地点的所有 `kind`，
不允许缺项、多项或通配键。每个人物必须显式写 `interaction_menu`，不额外限制时写 `null`。

```mermaid
flowchart LR
    LOCATION[当前位置的地点菜单] --> BOTH[取共同允许的活动动作]
    PERSON[人物菜单；null 表示沿用地点菜单] --> BOTH
    BOTH --> STATE[列出菜单内全部动作，受理时按占用、目标、地点与出行方式核验]
    STATE --> ACTION[本轮实际能做的事情]
```

例如从菜单的 `allowed_actions` 中移除 `watch`，人物在该地点就不能发起观看活动；
工作等其他动作仍按各自的菜单准入判断。场景配置使用稳定的内部英文动作名；模型在本轮交互对象中看到的是
五个动词（`travel`、`interact`、`obtain`、`consume`、`sleep`）与方式（如「interact：接着看」），经本轮私有绑定映射回内部动作。

下面是完整的最小菜单对象，可作为一个地点类或一个人物的 `interaction_menu` 值。
它只保留等待、睡眠；不适合作为需要正常工作和购物的日常场景菜单。

```json
{
  "allowed_actions": ["wait", "sleep"]
}
```

`wait` 和 `sleep` 是每份菜单必须保留的自身动作。数组必须非空、无重复，名称只能来自动作闭集。
菜单只剪裁 `start_activity` 的可选动作；动作不因人物状态隐藏，条件未满足的照常列出并带说明，只有阶段与
同刻上限的受限轮照常收窄；其他工具由工具层按当前阶段与活动状态开放。
名称写错或找不到而被拒（名称类拒绝，例如 `SUPPLY_NOT_FOUND`、`TASK_NOT_FOUND`）时，每人每秒的前两次不计同刻轮数、
不影响情绪，也就不会用掉受限轮；第三次起与其他拒绝一样计轮。
一次最多选择两个交互对象，`targets[]`、`actions[]` 与 `ways[]` 按顺序一一对应，所选动作都须在有效菜单中；
每人两个名额，进行中、暂停与刚结束待收尾的活动各占一个，结束活动后才释放；
引擎按注册表占用与准入事实（目标在场与实际地点、出行方式、物品状态）整体核验，能量不参与。
菜单控制动作权限；画像偏好只影响选择和排序。

## 配置工作任务和消息

工作条目定义一个人物可以选择完成的合同。最稳妥的做法是复制模板中的 `tasks` 条目，
保留已有输入和验收材料，先修改分配、开放时间、截止时间与报酬。

| 任务字段 | 填写规则 |
| --- | --- |
| `task_id`、`assignee_agent_id` | 任务标识唯一；受理人写 `agent:1` 或 `1`，必须存在于人物数组 |
| `contract_kind` | `hourly`、`piece` 或 `package`，各自结算规则来自目录 |
| `available_at`、`due_at` | 带时区；开始 ≤ 开放 < 截止 ≤ 结束；开工与恢复都不越过截止，距截止不足 60 秒的恢复被拒绝；未付款的逾期任务保留到截止当日结束，逾期提交以 `WORK_SUBMISSION_LATE` 业务拒绝 |
| `maximum_duration_seconds` | 正整数秒，任务允许的最大执行时长 |
| `pay_fen` | 正整数分，具体付款语义由合同类型与交换规则决定 |
| `employer_account_id`、`required_location_id` | 已声明的雇主账户和地点 |
| `required_skill`、`minimum_skill` | 场景人物已声明的技能名称与 0–1 门槛 |
| `required_effective_seconds` | 正数秒，要求的有效工作量 |
| `input_path`、`input_sha256` | 相对语料根目录的工作材料路径及其真实 SHA-256 |
| `oracle_path`、`oracle_sha256` | 验收材料路径及其真实 SHA-256；材料格式见[世界素材](resources.md) |

不要只添加一个任务标题就期待它能执行。新材料需要满足当前工作提供器和验收器的格式。
现有技能名可以在对应人物的 `skill_levels` 中调整数值；新建技能名不会自动创造新的工作算法。

以下是一个完整消息条目，沿用白领场景当天任务：

```json
{
  "message_id": "my-work-reminder",
  "thread_id": "my-work-thread",
  "recipient_agent_id": 1,
  "sent_at": "2026-01-05T08:00:00+08:00",
  "subject": "产品评审准备",
  "body": "今天可以开始处理会议材料，请在任务截止前提交。",
  "related_task_id": "task-2026-01-05",
  "attachment_sha256": null
}
```

`sent_at` 必须早于场景结束且偏移匹配时区，可以早于场景开始；这样的消息在开始时已可见。
无关联任务时 `related_task_id` 可写 `null`，无附件时 `attachment_sha256` 可写 `null`。
任务被删除后，不要留下指向它的消息。
场景消息按邮件呈现：到达时人物只看到主题，正文通过阅读取得；人物之间与联系人发来的直接消息则以全文呈现给人物。

场景联系人可以填可空的 `receive_location_id`（地点标识）：直接配送给联系人或当面交给联系人的实物，以它为收货地点；
为空时联系人不能收实物。联系人的其余字段见[人物画像](personas.md)。

## 配置日历和预排事件

`calendar` 必须包含下面这些键：

| 字段 | 形状 |
| --- | --- |
| `spring_festival_dates`、`makeup_work_dates`、`overtime_opportunity_dates` | 日期数组，规范 `YYYY-MM-DD`，每个数组内部不重复，可为空 |
| `sick_leave_date`、`personal_leave_date`、`social_dinner_date` | 各一个规范日期字符串，不能用 `null` 或空字符串替代 |

日历日期定义情境和机会，不给人物写行动脚本。短场景可保留模板中落在窗口外的日期。

账单、工资、调价和补货属于明确预排的世界事件，`due_at` 必须严格晚于场景开始、严格早于场景结束。
下面是向既有场景增加一次工资和账单的局部片段；示例沿用模板账户：

```json
{
  "payrolls": [
    {
      "payroll_id": "my-salary",
      "employer_account_id": "employer:zhangjiang",
      "agent_id": 1,
      "amount_fen": 500000,
      "due_at": "2026-01-05T09:00:00+08:00"
    }
  ],
  "bills": [
    {
      "bill_id": "my-rent",
      "account_id": "agent:1",
      "payee_account_id": "external:scenario",
      "amount_fen": 450000,
      "due_at": "2026-01-05T10:00:00+08:00"
    }
  ]
}
```

事件标识各自唯一，金额为正整数分；账单付款与收款账户不能相同，所需账户必须存在。
调价 `price_changes` 使用 `change_id`、`supply_id`、`price_version`、`amount_fen`、`due_at`、`source`；
`source.kind` 必须为 `scenario`。补货 `product_releases` 使用 `release_id`、`supply_id`、`stock`、`due_at`、`source`，
同样要求 `source.kind` 为 `scenario`；
`stock` 是设置后的非负整数库存，不是本次增加量。
两者都引用门店供给 `supply_id`，不是商品 `product_id`；来源结构和商品供给见[世界素材](resources.md)。

## 校验自己的场景

先按[入门流程](README.md#创建自己的第一个场景)调用 `load_scenario` 做离线检查，
再用 `worldmock validate` 核对依赖、语料与人物卡片覆盖率。完整校验会调用模型。
`validate` 还逐条打印运行输入警告 `Input warning <警告码> (agent <编号>): <说明>`，同一组警告列在结果的 `input_warnings` 中，
新建运行时冻结进运行清单。警告不让校验或新建运行失败（覆盖率低于 80% 时 `validate` 照旧失败），
警告码见[接口合同](../CONTRACTS.md#运行输入警告)。

| 错误或现象 | 处理方法 |
| --- | --- |
| `Scenario validation failed` | 查看异常链中的具体字段；排除旧字段、类型错误与跨引用错误 |
| `Scenario corpus manifest identity is invalid` | 核对 `corpus_root` 与清单真实哈希；不要只改哈希掩盖素材损坏 |
| `work task is outside the scenario interval` | 同步调整任务开放和截止时间，或移除窗口外任务和有关消息 |
| `interaction menu location kinds must exactly match declared location kinds` | 根据已选地点的实际 `kind` 补齐或删除菜单键 |
| `Selected locations use kinds that are not place types` | 地点 `kind` 只能是 `home`、`office`、`merchant`、`restaurant`、`convenience_store`、`supermarket`、`wet_market`、`fitness`、`park` 之一；在地图中改正并同步菜单键，见[世界素材](resources.md#编写地点与营业时间) |
| `Selected locations share display names` | 同名地点在地图中写互不相同的 `branch_label` |
| `Agent <编号> would see non-unique object labels` | 同一人物会同时看到两个相同的名称，例如两项工作同名、两位联系人同称谓；改掉其中一个 |
| `candidate_presentation_limit and rotation_quota_ratio leave no rotation quota` | 调整二者，保证乘积向下取整至少为 1 |
| 旧场景含 `max_consecutive_rejections` 被拒（未知字段） | 删掉该键（同刻上限取缺省 3），或改写为 `max_same_moment_decisions` |
| 警告 `TASTE_GENRES_UNMATCHED`、`CONTENT_WEIGHTS_ALL_ZERO` | 卡片口味题材与内容 `genre` 按字面精确匹配，平台名不是题材；改画像正文的口味写法，或给内容补同一套题材词；没写 `background_story` 的人物没有口味权重，也报后者 |
| 警告 `CONTENT_EMPTY_WITH_MEDIA_TASTES` | 场景没有内容，卡片却有口味题材，或媒介、习惯里有看、刷、听；补 `contents`，或确认人物本就没有可看的内容 |
| 警告 `GAMES_WITHOUT_TASTE_WEIGHTS` | 场景有游戏，人物却没有口味权重（没写 `background_story`）；游戏缓解按下限偏好计 |
| 警告 `COVERAGE_BELOW_THRESHOLD`、`PERSONA_AGREEMENT_LOW` | 卡片的世界覆盖率低于 80%，或画像编译的三次一致性偏低；见[人物画像](personas.md#预览与校验) |
| 警告 `CARD_FIELD_OVERRIDDEN`、`CARD_FIELD_CONFLICT` | 场景显式写的作息字段覆盖了卡片抽取值，有意为之即可；冲突时保留卡片值，让场景窗口与卡片其余时刻按起床、出门、午餐、晚餐、就寝排序 |
| 警告 `DELIVERY_ROUTE_UNAVAILABLE`、`LOCATION_NAME_SUSPICIOUS` | 配送门店到声明目的地没有可达驾车路线，或地点显示名带坐标、标识形态；见[世界素材](resources.md#验证修改) |
| 修改配置后不能恢复旧运行 | 用新目录新建运行；恢复不是应用新配置的入口 |

字段的最终边界见[场景加载器](../../src/worldmock/life/scenario.py)、
[交互菜单合同](../CONTRACTS.md#交互菜单声明与有效菜单)和[运行配置](runtime.md)。
