<a name="scenarios-生活场景"></a>
# 生活场景

> 2026-10-01：[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)已冻结并已实现；本手册描述当前程序实际读取的配置。
> 2026-10-03：按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)同步个人事务与提醒（阶段 2）。
> 2026-10-03：按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)同步场景结构 5 的工作安排（阶段 3）。

生活场景决定模拟谁、从什么时候到什么时候、有哪些生活机会，以及世界开始时的状态。
从[手册首页](README.md#readme-创建自己的第一个场景)复制现有场景开始，按本页调整自己的条件。
人物具体怎么过一天仍由人物决定；场景提供环境和机会，也可以声明人物定下的工作安排——它是每轮告诉人物的事实，环境不因此拒绝任何行动。

[返回手册](README.md#readme-用户配置手册) · [人物画像](personas.md#personas-人物画像) · [世界素材](resources.md#resources-定义地点门店供给与世界素材)

<a name="scenarios-选择起点与文件位置"></a>
## 选择起点与文件位置

| 现成场景 | 适合用来修改什么 |
| --- | --- |
| [白领十二周](../../scenarios/white-collar-12-weeks.json) | 单人物、工作合同、工资和生活支出；首页示例由它裁出一天 |
| [多人场景](../../scenarios/multi-person.json) | 多人物与按人分配工作 |
| [多人半天](../../scenarios/multi-person-half-day.json) | 短时段、多人、没有工作任务的生活观察 |

新场景放在仓库的 `scenarios/` 下。解析器用场景的上两级目录定位 `catalog/`，
场景中的 `corpus_root` 则相对场景所在目录解析。不要把场景移动到 `scenarios/custom/` 等更深目录而不调整整个项目布局。
`schema_version` 当前为 5：每个人物须显式写 `work_schedule`（对象或 `null`）；多余字段和旧结构的输入都严格拒绝。

<a name="scenarios-看懂场景的组成"></a>
## 看懂场景的组成

```mermaid
flowchart TB
    SCENE[场景] --> PEOPLE[人物、工作安排与身体初值]
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
| `body`、`agents`、`interaction_menus_by_location_kind` | 必填；身体初值、人物和每个地点类的菜单；每个人物条目另须显式写 `work_schedule`，见[声明工作安排](scenarios.md#scenarios-声明工作安排) |
| `locations`、`products`、`supplies` | 必填数组；选中的地点、商品与门店供给 |
| `tasks`、`messages`、`contents`、`games` | 必填数组；工作、通知与可接触素材 |
| `calendar` | 必填；特殊日期与生活情境 |
| `bills`、`payrolls`、`price_changes`、`product_releases` | 可省略，默认空数组；显式预排事件 |
| `recipe_output_bindings` | 可省略，默认 `{}`；有库存配方时把输出品类绑定到输出对象 |
| `phone_simulation_config`、`phone_simulation_config_sha256` | 可省略，默认 `null`，两者同写或同省；语料根内手机配置的相对路径与 SHA-256，购买渠道与画像覆盖率的 App 名都取自它 |

购买渠道就是手机配置里的购买 App：称呼取 `app_display_names` 中该 App 的显示名，成员门店是 `purchase_apps_by_seller` 映射到它的
卖家有供给的所选地点。人物在 `from` 写 App 称呼（去掉空白、不分大小写比较）时只在成员门店里买，几家都能服务时按请求的 `prefer`
选定一家，订单记下 App 标识 `channel_id`；渠道没有自己的送达时长和服务范围，送达仍按选定门店的备货加路线，
见[世界素材](resources.md#resources-编写门店供给与商品)。
配置文件要登记进语料清单，场景钉定的 SHA-256 与清单一致；配置的 `scenario_id`、`timezone` 与场景相同，`agent_id` 是场景中的人物。
不钉定就没有渠道，`from` 写 App 名以 `SOURCE_NOT_FOUND` 拒绝；配置核验失败（身份、结构、场景身份或执行证据不符）时同样没有渠道：
加载照常并写一条 warning 日志，`worldmock validate` 则以同一原因失败。加载时另查渠道准入：称呼在渠道之间不重复，也不等于任何所选
地点的显示名；每个渠道至少一家成员门店，成员门店是所选地点并至少有一条供给；不满足即拒绝加载，报错见[校验自己的场景](scenarios.md#scenarios-校验自己的场景)。
现成场景都没有钉定手机配置。

<a name="scenarios-修改日期与模拟节奏"></a>
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
每轮工具层还附一份只列名称的可调用索引 `callable_index`：此地此刻可做的生活活动全部列名，内容与此刻从这里能得到的商品按品类给个数和
至多 3 个示例名；它不是候选，不带动作，不受 `candidate_presentation_limit` 限制，有效菜单不允许的部分为空。

不要把反馈间隔当作“人物每十五分钟行动一次”。决策本身不耗时：没有进行中活动的人物在同一秒接着决策，
只有人人都有事在做时，时间才根据活动完成、消息、任务、身体变化和其他事件推进。
在手承诺包括尚未推进的当日目标、可见的未完成工作任务、暂停的活动与进行中的活动（等待除外）。未完成的个人事务不算在手承诺，不改变两个重估间隔。
当日目标由零点计划生成：运行开始时和每个场景本地零点（运行恰在零点结束时除外），系统为每个人物调用模型生成当天的
有序目标，同一人物同一天只生成一次；人物醒着时可以修订。目标不填写时间段，每条带模型写的大致时刻 `around_time`
（场景本地 `HH:MM`，可为空）：写了时，只有进行时段与它前后 90 分钟有交集的同类活动才算推进这条目标。
会在某一刻提醒人物的有两种：当日目标的 `remind_time` 与个人事务的 `remind_time`（都是场景本地 `HH:MM`，可为空），每次设定只触发一次。
只有当日目标的提醒能叫醒睡着的人；个人事务的提醒在睡眠中不交付也不消耗，人物醒来的那一刻一并交付。
睡眠中除了当日目标的提醒，只有睡够等专用唤醒条件才会结束睡眠。
规则见[当日目标规格](../dev/SPEC-day-plan-goals.md#spec-day-plan-goals-当日目标特性开发规格)与[时间与能量规格](../dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)第四章。

个人事务由人物在运行中自己记下（播报、暂停或收尾时写在请求的 `errands` 里），场景不能预置。
它只表达能由结算确认的事：取得或吃掉某件具体商品若干份（1 至 9）；环境按真实结算确认办成，人物只能取消并写原因，没有截止与过期。
「取得」不看地点：到店买下的东西留在门店，事务照样办成，要在家里用的东西应远程下单送到家。
事务的提醒钟点不晚于写下它的时刻（新增或更新被受理时）时，指下一天的这个钟点。每人未完成事务至多 8 条，一次请求至多 4 条变更，
名称至多 30 字符，备注与取消原因各至多 120 字符；事务跨零点保留并一直显示在待办层，零点计划与修订计划都不删它。
这些上限是实验假设，见[实验设置](../EXPERIMENT.md#experiment-实验假设声明)；规则见[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)第二章。

<a name="scenarios-修改人物身体与账户"></a>
## 修改人物、身体与账户

人物字段及联系人详见[人物画像](personas.md#personas-人物画像)。增加人物时，为它分配唯一正整数 `agent_id`，
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
候选排序仍按原始口味权重。取值是实验假设（敏感区间 0.3–0.7），见[实验设置](../EXPERIMENT.md#experiment-实验假设声明)。
饥饿与困意由身体和时刻计算，没有可直接设置的“饥饿值”或“疲劳值”。
起点困意也不另设：系统按人物作息反推起点的睡眠压力（假定自最近一次惯常起床时刻起一直清醒），
公式与起点落在惯常睡眠时段时的效果见[人物画像](personas.md#personas-在场景中填写人物)。
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

<a name="scenarios-限制人物可以做什么"></a>
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
取得商品的「到店」「远程下单」与不列出的「购买」（由系统定到店或远程下单）都映射为 `purchase`，按名写的商品也计为它；
移除 `purchase`，人物在该地点既不能到店买，也不能远程下单。

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
目标工具接口一次最多选择两个交互对象，items每项填写target/way/from/to，方式推导的内部动作须在有效菜单中；
每人两个名额，进行中、暂停与刚结束待收尾的活动各占一个，结束活动后才释放；
引擎按注册表占用与准入事实（目标在场与实际地点、出行方式、物品状态）整体核验，能量不参与。
菜单控制动作权限；画像偏好只影响选择和排序。

<a name="scenarios-声明工作安排"></a>
## 声明工作安排

每个人物条目必须显式写 `work_schedule`：没有固定班次写 `null`，这个人物就没有任何在岗语义。
有固定班次时写一个对象。下面是 `agents[]` 条目里 `work_schedule` 一项的局部片段，示意形状：

```json
{
  "work_schedule": {
    "workplace_location_id": "office",
    "weekly": {
      "0": {"starts": 540, "ends": 1080, "breaks": [[720, 810]]},
      "1": {"starts": 540, "ends": 1080, "breaks": [[720, 810]]},
      "2": {"starts": 540, "ends": 1080, "breaks": [[720, 810]]},
      "3": {"starts": 540, "ends": 1080, "breaks": [[720, 810]]},
      "4": {"starts": 540, "ends": 1080, "breaks": [[720, 810]]},
      "5": null,
      "6": null
    },
    "exceptions": {"2026-02-14": {"starts": 540, "ends": 1080, "breaks": []}, "2026-02-17": null}
  }
}
```

`office` 换成场景实际选中的办公地点标识。示例只示意形状，原样放进白领十二周场景会被加载检查拒绝：
完整声明还要按下面的规则覆盖场景日历里落在时间窗内的全部特殊日（该场景还有补班日、春节其余工作日与病假、事假日）。

- 对象的三个键 `workplace_location_id`、`weekly`、`exceptions` 都必填，没有例外写 `{}`；班次的三个键 `starts`、`ends`、`breaks`
  也都必填，没有休息时段写 `[]`；多写的键被拒。
- `weekly` 七天全写，键 `"0"` 至 `"6"` 对应周一至周日，值为班次或 `null`（当日不当班）；`exceptions` 用规范的 `YYYY-MM-DD`
  覆盖当天，值同样是班次或 `null`，优先于 `weekly`。系统不从星期或任务分布推断班次。
- 时刻是当地零点起的分钟（`540` 是 09:00，`1440` 是 24:00），班次同日且 `starts < ends`；`breaks` 落在班次内、升序、互不重叠；
  区间一律半开。不写时区，用场景的 `timezone`。
- 加载时核对：工作地点是所选地点；落在场景时间窗内的日历日期里，补班日必须有班次，春节、病假、事假日必须不当班，
  加班机会日与聚餐日不要求班次；时间窗外的日期不核对，写了例外也不拒绝。日历是场景级的，病假、事假日对所有声明了班次的人物同时生效。
- 班次形状错误报 `Scenario validation failed`（明细在异常链）；地点与日历核对的错误是 `Work schedules are not admissible: …`，
  逐条写明人物、日期与字段。
- 现成三份场景的人物都写 `null`。

**效果。** 声明了工作安排的人物每轮在环境层看到 `duty`：当班日写「工作日」、班次钟点、工作地点名、休息时段与状态
（「未到上班时间」「当班中」「休息时段」「已下班」之一，只由时钟与班次决定，不看人物在不在场）；不当班的日子写「休息日」；
没有工作安排的人物为 `null`。零点计划的输入另有计划日期当天的上班安排（不含状态），零点计划不把目标安排在上班时间离开上班地点。
它是事实呈现，不是受理条件：出行照常列出、照常受理，零点计划、修订计划和交了手上的工作都不改变它；
班内离岗由行为评估的工作负荷与作息偏离指标报告（`scripts/inspect_run.py <运行目录> workday --scenario <场景文件>`）。
工作安排不改变任务的开放、开工、提交与迟交判定：班前到岗可以开工，班后可以继续做未到截止的任务。

<a name="scenarios-配置工作任务和消息"></a>
## 配置工作任务和消息

任务不写开放时间：开放时刻由关联通知或预约派生，见[工作通知与目标发布](scenarios.md#scenarios-工作通知与目标发布)。

工作条目定义一个人物可以选择完成的合同。最稳妥的做法是复制模板中的 `tasks` 条目，
保留已有输入和验收材料，先修改分配、通知或预约、截止时间与报酬。

| 任务字段 | 填写规则 |
| --- | --- |
| `task_id`、`assignee_agent_id` | 任务标识唯一；受理人写 `agent:1` 或 `1`，必须存在于人物数组 |
| `contract_kind` | `hourly`、`piece` 或 `package`，各自结算规则来自目录 |
| `appointment`、`due_at` | `appointment` 必填：普通工作写 `null`，真实预约写 `{starts_at, reason}`；`due_at` 带时区，晚于派生的开放时刻（最早本人关联通知的 `sent_at` 或预约 `starts_at`）、晚于场景开始且不晚于场景结束；开工与恢复都不越过截止，距截止不足 60 秒的恢复被拒绝；未付款的逾期任务保留到截止当日结束，逾期提交以 `WORK_SUBMISSION_LATE` 业务拒绝 |
| `maximum_duration_seconds` | 正整数秒，任务允许的最大执行时长 |
| `pay_fen` | 正整数分，具体付款语义由合同类型与交换规则决定 |
| `employer_account_id`、`required_location_id` | 已声明的雇主账户和地点 |
| `required_skill`、`minimum_skill` | 场景人物已声明的技能名称与 0–1 门槛 |
| `required_effective_seconds` | 正数秒，要求的有效工作量 |
| `input_path`、`input_sha256` | 相对语料根目录的工作材料路径及其真实 SHA-256 |
| `oracle_path`、`oracle_sha256` | 验收材料路径及其真实 SHA-256；材料格式见[世界素材](resources.md#resources-定义地点门店供给与世界素材) |

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
为空时联系人不能收实物。联系人的其余字段见[人物画像](personas.md#personas-人物画像)。

<a name="scenarios-配置日历和预排事件"></a>
## 配置日历和预排事件

`calendar` 必须包含下面这些键：

| 字段 | 形状 |
| --- | --- |
| `spring_festival_dates`、`makeup_work_dates`、`overtime_opportunity_dates` | 日期数组，规范 `YYYY-MM-DD`，每个数组内部不重复，可为空 |
| `sick_leave_date`、`personal_leave_date`、`social_dinner_date` | 各一个规范日期字符串，不能用 `null` 或空字符串替代 |

日历日期定义情境和机会，不给人物写行动脚本。声明了 `work_schedule` 的人物另与日历交叉核对，只核对落在场景时间窗内的日历日期：补班日必须有班次，春节、病假、事假日必须不当班，加班机会日不要求班次。短场景可保留模板中落在窗口外的日期。

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
两者都引用门店供给 `supply_id`，不是商品 `product_id`；来源结构和商品供给见[世界素材](resources.md#resources-定义地点门店供给与世界素材)。

<a name="scenarios-校验自己的场景"></a>
## 校验自己的场景

先按[入门流程](README.md#readme-创建自己的第一个场景)调用 `load_scenario` 做离线检查，
再用 `worldmock validate` 核对依赖、语料与人物卡片覆盖率。完整校验会调用模型。
`validate` 还逐条打印运行输入警告 `Input warning <警告码> (agent <编号>): <说明>`，同一组警告列在结果的 `input_warnings` 中，
新建运行时冻结进运行清单。警告不让校验或新建运行失败（覆盖率低于 80% 时 `validate` 照旧失败），
警告码见[接口合同](../CONTRACTS.md#contracts-运行输入警告)。

| 错误或现象 | 处理方法 |
| --- | --- |
| `Scenario validation failed` | 查看异常链中的具体字段；排除旧字段、类型错误与跨引用错误 |
| `Scenario corpus manifest identity is invalid` | 核对 `corpus_root` 与清单真实哈希；不要只改哈希掩盖素材损坏 |
| `work task is outside the scenario interval` | 同步调整任务通知、预约和截止时间，或移除窗口外任务和有关消息 |
| `interaction menu location kinds must exactly match declared location kinds` | 根据已选地点的实际 `kind` 补齐或删除菜单键 |
| `work_schedule` 被拒（缺字段、工作地点不是所选地点、班次或休息时段非法、时间窗内的日期与日历不一致；地点与日历两类以 `Work schedules are not admissible: …` 列出） | 每个人物都显式写 `work_schedule`，没有固定班次写 `null`；按报错里的人物、日期与字段改正，见[声明工作安排](scenarios.md#scenarios-声明工作安排) |
| `Selected locations use kinds that are not place types` | 地点 `kind` 只能是 `home`、`office`、`merchant`、`restaurant`、`convenience_store`、`supermarket`、`wet_market`、`fitness`、`park` 之一；在地图中改正并同步菜单键，见[世界素材](resources.md#resources-编写地点与营业时间) |
| `Selected locations share display names` | 同名地点在地图中写互不相同的 `branch_label` |
| `Agent <编号> would see non-unique object labels` | 同一人物会同时看到两个相同的名称，例如两项工作同名、两位联系人同称谓；改掉其中一个 |
| `Purchase channels are not admissible: …`，其后逐条列出 `name '<称呼>' is shared by channels <App 标识> and <App 标识>`、`channel <App 标识> name '<称呼>' equals location <地点标识>` 或 `channel <App 标识> has an empty name` | App 称呼在渠道之间去掉空白、不分大小写后重复，与所选地点的显示名相同，或为空；改手机配置 `app_display_names` 里的称呼并更新它在语料清单和场景里的 SHA-256，或改地点显示名 |
| `Purchase channels are not admissible: …`，其后逐条列出 `channel <App 标识> member store <地点标识> has no supply`、`channel <App 标识> member store <地点标识> is not a selected location` 或 `channel <App 标识> has no member store` | 成员门店必须是所选地点并至少有一条供给；核对 `purchase_apps_by_seller` 里的卖家账户在场景 `supplies` 中的门店 |
| 日志 `Phone simulation config failed verification; no purchase channels are derived: <原因>`；`worldmock validate` 以同一原因失败 | 手机配置核验失败，本次没有渠道：核对配置路径、语料清单登记与场景钉定的 SHA-256，配置的 `scenario_id`、`timezone`、`agent_id`，每条 App 映射都能执行（购买 App 的卖家有有货的供给，观看 App 要场景有内容，驾车 App 要有可达的驾车路线），每个 App 都有不重复的非空称呼 |
| `candidate_presentation_limit and rotation_quota_ratio leave no rotation quota` | 调整二者，保证乘积向下取整至少为 1 |
| 旧场景含 `max_consecutive_rejections` 被拒（未知字段） | 删掉该键（同刻上限取缺省 3），或改写为 `max_same_moment_decisions` |
| 警告 `TASTE_GENRES_UNMATCHED`、`CONTENT_WEIGHTS_ALL_ZERO` | 卡片口味题材与内容 `genre` 按字面精确匹配，平台名不是题材；改画像正文的口味写法，或给内容补同一套题材词；没写 `background_story` 的人物没有口味权重，也报后者 |
| 警告 `CONTENT_EMPTY_WITH_MEDIA_TASTES` | 场景没有内容，卡片却有口味题材，或媒介、习惯里有看、刷、听；补 `contents`，或确认人物本就没有可看的内容 |
| 警告 `GAMES_WITHOUT_TASTE_WEIGHTS` | 场景有游戏，人物却没有口味权重（没写 `background_story`）；游戏缓解按下限偏好计 |
| 警告 `COVERAGE_BELOW_THRESHOLD`、`PERSONA_AGREEMENT_LOW` | 卡片的世界覆盖率低于 80%，或画像编译留下了警告（三次一致性偏低，或下班、到家锚点因先后冲突降级为 null；原文在 `details.compiler_warning`）；见[人物画像](personas.md#personas-预览与校验) |
| 警告 `CARD_FIELD_OVERRIDDEN`、`CARD_FIELD_CONFLICT` | 场景显式写的作息字段覆盖了卡片抽取值，有意为之即可；冲突时保留卡片值，让场景窗口与卡片其余时刻按起床、出门、午餐、晚餐、就寝排序，`leave_work_window`、`arrive_home_window` 另须满足下班不早于出门、到家不早于下班（没有下班时不早于出门）且二者不晚于就寝；卡片在新槽为 null 时冲突就保留 null；卡片抽取的下班、到家时刻也参与核对，场景就寝窗口早于它们时把下班、到家窗口也写进场景 |
| 警告 `DELIVERY_ROUTE_UNAVAILABLE`、`LOCATION_NAME_SUSPICIOUS` | 配送门店到声明目的地没有可达驾车路线，或地点显示名带坐标、标识形态；见[世界素材](resources.md#resources-验证修改) |
| 修改配置后不能恢复旧运行 | 用新目录新建运行；恢复不是应用新配置的入口 |

字段的最终边界见[场景加载器](../../src/worldmock/life/scenario.py)、
[交互菜单合同](../CONTRACTS.md#contracts-交互菜单声明与有效菜单)和[运行配置](runtime.md#runtime-运行配置)。

<a name="scenarios-工作通知与目标发布"></a>
## 工作通知与目标发布

输入每项工作必填appointment=null（普通）或{starts_at,reason}（真实预约），删除available_at/availability_kind/appointment_reason；
运行时RuntimeWorkTask.available_at由普通任务最早本人关联通知sent_at或预约starts_at派生，不重复配置；
每项任务至少一封related_task_id通知发给受派人物，邮件到达即已知且普通工作开放。
预约starts_at带时区、reason非空说明真实预约依据；提前收件可见，约定时刻才开放。
地点和截止仍约束开工，任务标题带“会议分析”不代表本人参加预约会议。
启动前已收到的邮件与工作合同允许初始化，due_at必须晚于运行起点；不保留无通知旧行为。
正式products/supplies列表包含选中门店全部声明商品和生成供给，不沿用旧稀疏选择。
保持现有场景random_seed，按对应种子分别冻结供给发布；地图3、场景4、目录5.0分别在首次受影响阶段发布，后续目录内容变更另发版本，world8沿用。
其后个人事务与工作安排规格把世界结构升为 9、场景结构升为 5，当时目录仍为 5.3。
当前目录按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)发布为 5.4，只更正可供性声明的说明，业务数值与其它格式版本不变。
