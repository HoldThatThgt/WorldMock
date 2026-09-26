# WorldMock 接口合同

> 状态：FROZEN
> 范围：LifeEnv 与 LifeAgent 适配接口、数字足迹事件与存储、语料内容目录、LabelKit 输入输出与交付格式、命令行与运行状态。
> 权威：记录引擎的事件、素材、存储与导出合同统一在[记录引擎设计](design/05-recording-engine.md)定义；其余接口签名、字段名、枚举值、Schema、文件格式与命令行参数在本文件定义。其他文档只引用所属合同，代码变更先更新对应合同。

## LifeEnv 状态字段

### 状态归属

| 状态 | 内容 | 修改权限 |
| --- | --- | --- |
| 世界 | `simulation_time`, `locations`, `weather`, `corpus_version`, `calendar`, `scheduled_events` | 环境规则 |
| 人物身体 | `available_energy_kcal`, `sleep_deficit_hours`, `entertainment_need`, `recent_activities`, `physiological_state` | 时间推进、进食、睡眠和活动结算 |
| 人物资产 | `account_balance`, `inventory`, `consumables_count`, `access_rights` | 交易、交付、使用与合同结算 |
| 人物活动 | `activity_type`, `start_time`, `progress`, `next_decision_time`, `occupied_resources`, `pause_reason`, `end_reason`, `same_moment_rounds`（本秒已进行的决策轮数）, `same_moment_stage`（受限轮阶段：`open`、`conclude_pending`、`start_or_resume`、`resume_or_conclude`、`conclude_only`、`wait_only`）；活动名额为常量 2（进行中、暂停中与待收尾的活动各占一个，`conclude_activity` 之后释放） | 人物申请，环境验证并执行；同刻计数与受限轮阶段由环境随决策轮与时钟推进维护 |
| 地点会话 | `sessions`：一段地点停留或两地之间的一段路（`kind` 取 `stay`、`transit`，`status` 取 `active`、`ended`），名称为地点或路段加开始时刻，例如「在家（1月5日 00:00 起）」「去张江办公室的路上（1月5日 14:17 起）」 | 环境随出发与到达开始和结束；人物不手动开始或结束会话 |
| 工作 | `task`, `materials`, `deadline`, `deliverable`, `review_result`, `contract`, `payment_status` | 任务来源、人物交付与独立验收规则 |
| 内容接触 | `visible_catalog`, `read_emails`, `played_segments`, `game_state`, `material_ranges` | 实际接触行为与内容提供过程 |
| 记忆 | AS2 人物记忆及其版本、`source_event`, `retrieval_results` | 人物记忆流程 |

人物身体与资产的准确数值属于世界事实，只进入足迹与评估。人物实际收到的是确定性规则算出的体感分级与自然语言场景描述；训练数据导出再按目标系统的信息范围裁剪，不默认线上设备能读到环境隐藏状态。

### 资源与需求字段

| 量 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- |
| `time` | UTC 整数秒 | seconds | 场景时区，同秒事件有严格序号 |
| `money` | 整数 | 最小货币单位 (分) | 避免浮点累计误差 |
| `available_energy` | 浮点 | kcal | 实验中的可用能量状态；非负；结算以 0 截断，不阻止任何活动 |
| `sleep_deficit` | 浮点 | hours | 与食品能量独立 |
| `entertainment_need` | 浮点 | 归一化 [0,1] | 随生活积累 |
| `work_need` | 复合对象 | - | `deadline`, `contract`, `task`, `economic_goal` |

### 生命周期

| 接口 | 签名 | 行为 |
| --- | --- | --- |
| `__init__` | `(scenario_path: str \| None = None)` | 仅保存配置路径和建立资源句柄，尚不创建世界 |
| `init` | `(start_datetime: datetime)` | 核验内容目录、规则、初始世界与时间；初始化数据库和事件计划 |
| `step` | `(tick: int, t: int)` | `tick` 为推进秒数，`t` 统一为结束时间；满足 `t = 上次已确认时刻 + tick` |
| `to_workspace` | `(workspace_path: str \| None = None)` | 遵循 AS2 签名；无参时使用已绑定工作区，持久化必要状态与检查点引用；错误必须传播 |
| `restore` | `(workspace_path: str)` | 读取明确指定的有效检查点，核对代码、规则、数据、时间和人物状态版本 |
| `close` | `()` | 关闭数据库等资源，保留运行终态与未完成活动 |

AS2 的环境扫描要求自定义环境类直接定义在 `custom/envs/*.py`；`LifeEnv` 在该文件定义，业务逻辑导入普通模块。正式实例通过现有 AS2 环境参数传入场景路径；`init` 缺少场景时明确失败，不生成默认世界继续运行。无参检查只用于注册与工具 Schema 发现，运行测试必须传入真实场景参数。

### 人物工具与管理接口

所有模型生活工具经 `submit_action(call)` 统一登记，宿主附加 `agent_id`、`decision_id`、`action_id`、
观察版本与本轮顺序；这些上下文由 runner 与路由绑定，不出现在模型可填写的参数中。
工具 Schema 由对应请求模型直接生成，全部字段必填，数组数量边界由本地校验器执行，不能静默截取。
模型只看人类可理解且能唯一定位的对象名称与动作短语；本轮私有 `interaction_bindings` 将它们绑定到内部身份、
标准动作、出行方式与冻结报价，绝不进入模型提示。LifeEnv 不为每个模型工具另建同名 RPC 方法。

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `observe` | `context` (宿主只读 RPC) | 当前可见状态、已到达通知和已完成的内容接触结果 |
| `start_activity` | `targets: list[str]`、`actions: list[str]`（等长一至二项，逐项为可读对象名称与动作短语） | 一次登记逐项启动活动；系统按冻结绑定决定领域请求及其结算参数，整组原子受理 |
| `recall_task_summary` | `task_name`（可读任务、当日目标、活动或会话名；会话名为地点加开始时刻） | 只读回看本人进度，不修改世界；结果在下一次观察的 `facts.progress.results[].details.recall` 中呈现 |
| `report_status` | `task: list[str]`, `summary: list[str]`（等长一至两项，逐项点名活动） | 逐项播报进行中活动的进展；点名暂停的活动即在其开始地点恢复它；关联当日目标同步记录各自自述 |
| `pause_activity` | `task: list[str]`, `summary: list[str]`（等长一至两项，逐项点名进行中活动） | 逐项写自述并暂停所选进行中活动；暂停仍占名额，路上的活动不能暂停；关联当日目标同步记录各自自述 |
| `conclude_activity` | `task: list[str]`, `summary: list[str]`, `memories: list[list[Memory]]`（三数组等长一至两项；`memories[i]` 是关于 `task[i]` 的零至多条记忆，合计不超过八条） | 逐项写总结与记忆并结束所选活动：进行中、暂停中或刚结束待收尾的活动；结束后释放名额，未选中活动保持原状 |
| `revise_day_plan` | `goals: List[{intent_kind, goal, remind_time}]`（替换全部未推进目标；`remind_time` 必填可为 null） | `status: "submitted"` |
| `submit_work` | `task_name`, `deliverable` | 有可见的未付款任务时开放；任务名称取本轮可见名称，未达标的提交以 `WORK_INCOMPLETE`、正在做的任务以 `WORK_TASK_IN_PROGRESS` 业务拒绝 |
| `send_message` | `recipient_name`, `text` | 发送模型撰写的消息，收件人名称取本轮可见名称 |

#### 交互菜单声明与有效菜单

场景根对象必须显式包含 `interaction_menus_by_location_kind`，键集合恰好等于场景中
场景选中地点经地图目录解析后的 `kind` 不同值集合；每个人物也必须显式包含 `interaction_menu`。人物值可以为
`null`，表示不在地点菜单之外增加限制；地点菜单不能为 `null`。解析后的地点 `kind` 是交互菜单地点类，
与候选资格使用的 `location_kinds` 不是同一个字段或词表。

```python
InteractionMenu = {
    "allowed_actions": list[str],
}
```

数组非空、无重复且只接受非空白字符串，菜单对象不接受未知字段。
`allowed_actions` 的闭集是 `START_ACTIVITY_ACTIONS`；其中 `wait`、`sleep` 必须存在，
保证人物在每个地点都能等待与睡觉。场景地点类缺项或多项、人物字段缺失、未知名称、
错误类型、重复、空数组或缺少保护动作均拒绝加载。

八个模型工具名由 `REQUEST_MODELS` 固定，地点菜单不剪裁工具，只收窄 `start_activity`
的可读动作选择。工作成果、消息正文和活动控制工具的工具资格由当前领域条件决定。

当前有效菜单按人物权威位置取得地点类菜单，再与人物菜单逐字段求交集并确定性排序；人物菜单为
`null` 时直接使用地点类菜单。它是现有状态、阶段、可见目标和回合预算门控之前的
准入约束，只能继续收窄动作选择，不能放宽既有规则。条件未满足的动作照常列出并附说明，不剪裁；
唯一的状态性收窄是同刻上限的受限轮。完整活动控制、下钻品类映射和恢复资格见
[交互菜单规格](dev/SPEC-interaction-menu.md#观察与人物工具)。菜单不得删除 `scene`、`facts`、
`affordance_contract` 或候选，也不得改变候选排除原因、呈现上限和轮转。

实际模型请求 Schema、本地决策校验和环境受理使用同一轮冻结菜单。环境在产生领域副作用前按权威
状态重验；违反动作准入的请求返回 `INTERACTION_MENU_DENIED`，
并沿用 `action_rejected` 与 `action_result` 的公开结果通道。拒绝不得扣款、消费、启动或恢复活动、
推进游戏、发送消息或调用工作验收。只读人物查询不进入生活观察与菜单，只开放原有记忆工具及只读
`finish`。完整声明、失败关闭和验收要求以[冻结特性规格](dev/SPEC-interaction-menu.md)为准。

| 管理接口 (不向模型暴露) | 参数 | 返回 |
| --- | --- | --- |
| `scheduling_state` | - | `decidable_agents: List[str]`, `next_event_time: int` |
| `commit_decision_round` | `round_id: str` | 按预定顺序结算请求 |
| `checkpoint` | `request` | 保存共同检查点 |

模型工具名是 LifeAgent 的原生请求合同。旧的分领域工具名直接删除，不维护别名；
领域执行函数继续负责实际结算。食物与耐用品的使用由 `start_activity` 的可读对象和动作定位，
再由引擎依据冻结绑定选择对应领域请求；模型不填写库存数量、物品内部身份或结算时长。
余额调整、天气设定、地点瞬移、验收强制通过和全世界读取不向人物暴露，也不提供通用代码执行接口。
`readonly=True` 表示调用不改变生活状态，观察调用本身仍需要留下证据。

### 当日目标请求

零点后台任务 `plan_day` 生成当日计划，人物以 `revise_day_plan` 修订。两者使用同一严格形状 `{goals: [...]}`：
零点计划的输出由 `PlanDayRequest` 校验，`revise_day_plan` 的请求模型与它同形。条目为
`PlanGoalInput`，只允许下表三个字段，禁止旧时段载荷和未知字段；工具 Schema 内联条目，
本地与环境共同校验文本长度、数量和意图闭集，不依赖提供商不支持的 Schema 关键字。
行为与验收见 [当日目标规格](dev/SPEC-day-plan-goals.md) 与[时间与能量规格](dev/SPEC-time-and-energy.md)第四章。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `intent_kind` | 下表十一种意图的闭集 | 同一清单最多出现一次 |
| `goal` | 非空白文本，最多三百字符 | 人物想推进什么，可写粗略时段，文字不驱动活动；实际家族活动不证明这句话已完成 |
| `remind_time` | `str \| null`，必填，可为 null | 场景本地 `HH:MM`（小时 00–23，分钟 00–59）；换算为当日绝对时刻，须晚于生成或修订时刻、早于次日零点；null 表示不设提醒。只有这个字段触发定时决策 |

清单零至八条，数组顺序就是优先级。同一人物同一本地日只生成一次（幂等键 `plan_submitted_dates`），可为空；
恢复与重放不重复生成。零点计划任务在调用模型之前按幂等键跳过当日已生成的人物，同日重复生成不以业务拒绝返回。
修订只提交替换后的全部未推进目标；已推进目标按原相对顺序保留前缀，合并后仍满足上限和唯一性。
`intent_kind`、`goal`、`remind_time` 三个字段完全相同的未推进条目保留登记时刻与提醒状态，不重校时刻，
所以原样重发已触发提醒的目标不被拒绝；只改 `remind_time` 的条目保留登记时刻并换成新提醒；
改变文字或删除记录为旧承诺丢弃，新条目重新登记；被替换或删除的目标，旧提醒取消。

`DayPlan` 持久形状是 `{day_end, goals}`。环境以场景本地次日零点填 `day_end`（场景本地零点指本地日期变更的那一秒：
夏令时在零点拨快、当日没有 00:00 时是该日的 01:00，零点回拨后重复出现的 00:00 不是零点）；
每条 `PlanGoal` 附加 `registered_at`、`progressed_at`、`progress_activity_id`、`remind_at`、`reminded_at`。
推进时刻与活动标识同时为 null 表示未推进，同时有值表示已有真实结算证据。
`remind_at`（`int | null`）是 `remind_time` 换算出的当日绝对时刻，未设提醒为 null；`reminded_at`（`int | null`）
是提醒触发的时刻，只有设了提醒的目标才有，且不早于 `remind_at`；提醒只触发一次。
这些字段由环境生成，人物请求不能写入。零点计划的登记写 `environment_event`（`visibility=system`，
`data.kind="day_plan_generated"`）：`data.day_plan` 与修订结果同形，目标的 `registered_at` 为该事件时刻，
`data.source_event_ids` 为输入来源事件。修订的动作完成结果 `data.day_plan` 和正时长结算事件
`data.day_plan` 同样保存完整对象；计划与这些事件都与世界状态在同一事务提交。

```mermaid
flowchart LR
    A[零点计划生成有序目标（可带提醒）] --> B[环境登记目标清单]
    B --> C[人物选择具体活动]
    C --> D[环境实际结算并记录目标推进]
    D --> E[人物修订尚未推进的目标]
    E --> C
```

| `intent_kind` | 活动家族闭集 |
| --- | --- |
| 睡眠 | `sleep` |
| 晨间例程 | `lifestyle:wellness`, `wait` |
| 通勤 | `travel` |
| 工作 | `work` |
| 用餐 | `consume` |
| 休息 | `wait`, `lifestyle:wellness` |
| 娱乐 | `watch`, `play_game`, `browse`, `lifestyle:hobby` |
| 社交 | `chat`, `call`, `lifestyle:social` |
| 家务 | `lifestyle:household`, `lifestyle:pet` |
| 运动 | `lifestyle:sport` |
| 外出 | `travel`, `lifestyle:outdoor` |

生活活动家族来自目录 `category`，活动快照原样保存该类别。同一活动在同一人物当日
最多推进一项目标，按未推进清单顺序匹配，不把重叠家族当成两份进展。

工具开放与生命周期：`plan_day` 不是人物工具。
存在当日计划即可开放 `revise_day_plan`，包括空清单后追加目标。计划只在本地日界清除，
不因就寝隐藏。环境不按清单执行活动，不从目标文字推导时刻；只有 `remind_time` 触发定时决策，
提醒可结束睡眠。

| 结算拒绝错误码 | 触发条件 |
| --- | --- |
| `PLAN_GOALS_INVALID` | 请求自身合法，但与保留的已推进目标合并后超过容量或违反领域约束 |
| `PLAN_GOAL_ALREADY_PROGRESSED` | 试图把已推进意图重新提交为待推进目标 |
| `PLAN_REMIND_TIME_INVALID` | 新登记或只改了提醒的条目，其 `remind_time` 换算为当日绝对时刻后不晚于修订时刻，或不早于次日零点；三个字段完全相同的条目不重校。零点计划的输出违反同一约束时不是业务拒绝，按输出不合法附回重试（见「生成参数」） |
| `PLAN_NOT_FOUND` | 没有当日计划时请求修订 |
| `ACTION_REQUEST_INVALID` | 请求未通过引擎边界严格校验，包括旧字段、超量或重复意图、非法文本、`remind_time` 格式错误或伪造状态字段 |

观察保留通用 `current_time` 与 `current_epoch`。原计划专用日首、日末、就寝换算锚点移除；
人物卡片作息与环境营业时刻继续在各自事实中表达。场景目标叙述只显示意图、已推进状态与提醒时刻，
完整目标文字与证据字段在 facts 中提供，不扩大场景叙述预算。

### 当日目标的配置与质量输出

场景沿用 `reconsideration_interval_seconds`（默认 5400 秒），并增加
`idle_reconsideration_interval_seconds`（默认 10800 秒）；两者为不超过 86400 的正整数，
空闲间隔不能短于常规间隔。进行中的等待不算在手承诺；人物只在等待、且没有未推进目标、可见未完成任务、
暂停活动或其他进行中活动时用空闲间隔，其余清醒情形用常规间隔。选择与威胁条件见 [目标决策节律](dev/SPEC-day-plan-goals.md#事件驱动的决策节律)。

决策节律场景参数：

| 场景键 | 取值 | 含义 |
| --- | --- | --- |
| `max_same_moment_decisions` | 正整数，缺省 3 | 同一人物同一模拟秒的决策轮数上限，时钟推进即归零；达到上限后进入受限轮，只开放一类动作（时间与能量规格第一章规则 3） |
| `activity_feedback_seconds` | 整数秒，60–86400 | 身体、进度与内容的结算间隔；等待的计划时长取它 |
| `progress_report_interval_seconds` | 正整数秒，缺省 1800 | 进行中活动的播报间隔 |

质量输出 `quality.json.day_plan_metrics` 按代码摘要分组，来源是同一认证截止的事件，
摘要从冻结 manifest 注入。每组包含 `habitual_consistency`、`external_constraints`、
`deliberate_goals` 和 `gates.external_constraint_block_count`；
字段、错误码闭集、分母、缺证据规则的唯一统计合同见
[度量输出合同](dev/SPEC-day-plan-goals.md#度量输出合同)，门禁地位见 [验证文档](VERIFICATION.md)。

### 请求字段与结算检查

| 模型请求 | 结算时的关键检查 |
| --- | --- |
| `start_activity` | 对象名称与动作必须在同一冻结观察中唯一匹配；按绑定解析内部身份、方式与报价，检查当前菜单、名额（进行中、暂停中与待收尾的活动各占一个，常量 2）、位置、资源及领域条件，能量不参与；多目标整体接受或拒绝 |
| `recall_task_summary` | 名称唯一对应本人可回看的任务、当日目标、活动或会话（会话名为地点加开始时刻）；只读，不修改世界 |
| `report_status`、`pause_activity`、`conclude_activity` | `task[]` 中每个名称唯一对应本人进行中、暂停中或待收尾的活动（活动名为「做什么＋对象＋开始时刻」），数组等长且一至两项（`conclude_activity` 的 `task`、`summary`、`memories` 三数组等长）；所有状态转换先验证再原子更新，自述另标未经核验 |
| `submit_work` | 任务名称唯一且可见，归属、进度、截止、素材与重复结算校验通过后单独评审；被提交的任务本身不在进行中（否则 `WORK_TASK_IN_PROGRESS`） |
| `send_message` | 收件人名称唯一且可见，渠道与文本符合规则 |
| `revise_day_plan` | 目标数量、文字、保留已推进目标与领域约束符合当日目标合同；`remind_time` 格式不合法报 `ACTION_REQUEST_INVALID`，新登记或改了提醒的条目换算后须晚于修订时刻、早于次日零点，否则报 `PLAN_REMIND_TIME_INVALID` |

出行、购买、消费、内容、社交和生活活动的内部领域请求仍按各自规则检查精确时长、数量、
报价、余额和占用；模型请求不含这些计算值。系统展示可比较的报价和预计时间，
结算使用当轮冻结报价与权威状态。报价变化或竞争导致的实际不可行性以具体业务拒绝返回，
不让模型根据提示中的取整文字回填精确秒数。

#### 活动受理拒绝码

活动受理与工作提交的业务拒绝以 `action_result` 登记，不终止运行。

| 错误码 | 触发条件 |
| --- | --- |
| `WORK_TASK_IN_PROGRESS` | `submit_work` 提交的任务本人正在做（有以它为目标的进行中工作活动）；评审准备与结算共用这一判定，不调用评审模型。做别的任务时提交已达标的任务照常评审与付款 |
| `SAME_MOMENT_ACTION_RESTRICTED` | 达到同刻上限后的受限轮中，请求不属于本轮开放的动作类；只在绕过本地校验时出现；计入同刻轮数 |
| `SESSION_LIMIT_EXCEEDED` | 名额超限：进行中、暂停中与待收尾的活动已占满两个名额（或本次开始的活动放不下），原因中点名占名额的活动：占满时提示先结束一项，只剩一个名额却要同时开始两项时说明只剩一个名额、不能同时开始两项；两个名额都被暂停活动占满时选择出行同样以此拒绝 |
| `ACTIVITY_PRECONDITION_UNMET` | 前置条件未满足，附 `unmet` 与 `suggested`：需要在某地；出发前仍有进行中活动（点名要先结束或暂停的活动）；暂停的活动在开始地点以外恢复（点名该地点，人在别处可以直接结束它） |
| `ACTIVITY_CONCLUSION_REQUIRED` | 有刚结束、待收尾的活动时做别的事（包括只结束其他活动）；原因为「先给刚结束的某事收尾」，点名待收尾活动 |
| `ACTIVITY_PAUSE_UNAVAILABLE` | 暂停路上的活动（回不到那段路）；可以直接结束。交谈、通话沿用 `SOCIAL_ACTIVITY_CANNOT_PAUSE` |

`start_activity` 的数组不合法、`targets` 与 `actions` 不等长或目标重复，报 `ACTION_REQUEST_INVALID`。

### 请求模型的闭集与字面边界

工具 Schema 由请求模型直接生成，全部字段必填。模型可填写的名称与动作短语必须非空白，
并在本轮可见投影中唯一；同名对象使用自然语言的地点或起始时间消歧，不生成索引编号。

| 请求 | 字段 | 闭集或边界 |
| --- | --- | --- |
| `start_activity` | `targets` | 一至两个名称；每个名称均在当轮交互对象中唯一，组合资源相容 |
| `start_activity` | `actions` | 与 `targets` 等长并逐项对应；每项属于对应对象当轮公开的可读动作闭集；出行方式由对应动作短语表达 |
| `recall_task_summary` | `task_name` | 当前冻结观察中唯一的可读任务、当日目标、活动或会话名称 |
| `submit_work` | `task_name` | 当前冻结观察中唯一的可读任务名称 |
| `report_status`、`pause_activity`、`conclude_activity` | `task` | 一至两个唯一的可读活动名称，不重复；活动名为「做什么＋对象＋开始时刻」（开始时刻用绝对日期），同一轮可见的名称重复时追加「（第二次）」这类序号 |
| `report_status`、`pause_activity`、`conclude_activity` | `summary` | 与 `task` 等长的逐项非空白模型自述；关联目标同步写对应进度 |
| `conclude_activity` | `memories` | 与 `task` 等长；`memories[i]` 是关于 `task[i]` 的零至多条记忆，合计不超过八条；每条为 `{type, text, importance, keywords}`，字段约束见「记忆字段」；超限由本地请求模型拒绝，按协议错误在本轮重试 |
| `revise_day_plan` | `goals[].remind_time` | 每条目标必填，可为 null；非 null 时为场景本地 `HH:MM`（两位小时 00–23、两位分钟 00–59），格式错误报 `ACTION_REQUEST_INVALID`；换算后的时刻约束见「当日目标请求」。零点计划 `plan_day` 不是人物工具，其输出条目按同一边界校验 |
| `send_message` | `recipient_name` | 当前冻结观察中的唯一可读人物名称 |
| `send_message` | `text` | 不超过 300 字符 |
| `submit_work` | `deliverable` | 会议纪要结构：`summary`、`decisions`、`action_items`，三者均不得为空白 |

工作任务的可读名称由场景必填的 `WorkTaskConfig.title` 提供；不得缺省并回退到内部 `task_id`。
引擎在冻结观察中保存标题到合同身份的私有绑定，模型只使用标题。

请求不能指定任意文件路径让环境读取；工作成果引用只允许已经登记且对人物可见的素材，
实际文本交付由宿主保存成新素材。请求校验失败返回明确业务错误；环境不替人物选择其他对象或动作。

### 动作状态与动作结果

```python
ActionStatus = Literal[
    "submitted",      # 已提交待结算
    "accepted",       # 已接受
    "in_progress",    # 进行中
    "completed",      # 完成
    "rejected",       # 拒绝
    "cancelled",      # 取消
    "failed",         # 失败
    "unknown"         # 结果未知
]
```

人物工具在决策阶段登记请求并返回已提交状态；资产检查和世界修改在收齐本轮提案后由环境执行。模型和游戏外部调用结果不明时，结果状态使用 `unknown`。登记成功不等于购买成功，也不等于活动完成：开始一个三十分钟的活动只代表它已开始，结果中不能立即包含三十分钟后的剧情或交付成果。已接受动作带活动标识、起始时间和下一次反馈时间；完成事件带实际执行区间、结果及可追溯的结算。正常业务拒绝回给人物继续选择；记录失败或程序错误终止运行，不能伪装成业务拒绝。人物记录的「已提交」必须引用实际登记的动作请求，决定想做、请求已登记与动作已完成分别表达。

生活决策开放「人物工具与管理接口」表中的人物工具与预算内的记忆查询，没有 `finish`。一轮决策恰好一个世界请求：世界请求必须是响应中唯一的调用，登记即结束本轮，受理或业务拒绝的结果在下一轮呈现；记忆查询是观察，可以在请求之前多次使用，不结束本轮。回合预算按去掉 `finish` 后的流程重算（见「回合预算」），预算耗尽仍无登记的世界请求即人物决策失败。有刚结束、待收尾的活动时做别的，以 `ACTIVITY_CONCLUSION_REQUIRED` 业务拒绝。显式只读人物查询保留只读 `finish`：它必须单独返回，不能与其他工具出现在同一批调用中。当前未开放的工具必须拒绝执行。冲突响应保存原文和协议错误，整批不执行，在现有 ReAct 剩余回合内把错误反馈给模型自行重新提交。每次非法响应消耗一个回合，预算耗尽才终止人物决策。

达到同刻上限（场景 `max_same_moment_decisions`）后，按观察私有证据 `same_moment_stage` 的受限轮阶段收窄世界请求工具与名称枚举，记忆查询照常；越界请求以 `SAME_MOMENT_ACTION_RESTRICTED` 业务拒绝并计入同刻轮数（[时间与能量规格](dev/SPEC-time-and-energy.md)第一章「实现约定」）。

### 领域枚举

活动类型与状态、活动变更、履约方式、出行方式与计酬方式在源码中以 `StrEnum` 声明，取值如下。

```python
ActivityKind = Literal[
    "wait", "sleep", "work", "watch", "play_game", "travel",
    "read_mail", "browse", "consume", "lifestyle", "chat", "call",
]

ActivityStatus = Literal[
    "active", "paused", "completed", "cancelled", "failed", "truncated",
]

ActivityChange = Literal["pause", "resume", "cancel", "change_speed"]

TravelMode = Literal["driving", "walking"]

FulfillmentKind = Literal["delivery", "pickup"]

ContractKind = Literal["package", "hourly", "piece"]
```

`lifestyle` 是生活活动子类型的统一活动类型，具体条目放 `target_id`，令牌形式为 `lifestyle:<activity_id>`；`chat` 与 `call` 是交谈与通话的独立活动类型，不在可供性注册表内。

| 接口 | 取值 |
| --- | --- |
| 订单状态 `DeliveryState.status` | `pending`、`ready`、`delivered`、`cancelled` |
| 决策轮状态 `DecisionRoundState.status` | `open`、`committed`、`aborted` |
| 世界状态结构版本 `WorldState.schema_version` | 7 |
| 活动边界 `ActivityState.boundary_type` | `planned_end`、`feedback`、`game_feedback`、`paused`、`cancelled`、`experiment_end`、`counterfactual_replay`、`route_ended`（路上开始的活动随到达或途中结束出行由世界结束）、`plan_reminder`（当日目标提醒到点结束睡眠；睡眠的计划结束与提醒同秒时记 `planned_end`） |
| 活动中断原因 `ActivityState.interruption_reason` | `agent_cancelled`（人物主动结束）、`experiment_end`、`LOCATION_ACCESS_DENIED`（到达准入失败）、`route_ended`、`plan_reminder`（提醒结束睡眠） |
| 地点会话 `SessionState.kind` / `SessionState.status` | `stay`、`transit` / `active`、`ended` |

环境自主到期事件（世界状态 `pending_events`）的类型闭集：

```python
ScheduledKind = Literal[
    "mail_arrival", "delivery_arrival", "work_available",
    "task_expired", "calendar_flag", "bill_due", "payroll",
    "price_change", "supply_quote_change", "product_release",
    "message_arrival",  # 联系人回复的预排送达（「联系人回复」）
]
```

| `ScheduledEvent` 字段 | 说明 |
| --- | --- |
| `scheduled_id`, `kind`, `due_at`, `sequence` | 事件身份、类型、到期模拟时刻与确定序号 |
| `agent_id`, `data` | 相关人物（可为空）与事件载荷 |

## 地图与门店供给合同

本节定义 [地图与门店供给规格](dev/SPEC-map-and-storefront.md) 的字段面。记录正文和恢复材料由
[记录引擎](design/05-recording-engine.md)定义；实现与验收状态独立记录。

### 冻结地图与场景引用

| 结构 | 字段 | 约束 |
| --- | --- | --- |
| `MapDatasetReference` | `dataset_id`、`version`、`path`、`sha256` | 稳定非空身份、整数版本、冻结文件位置及 SHA-256；文件身份与语料清单一致 |
| 场景 `locations` | 每项仅 `location_id` | 引用地图地点，不复制名称、坐标、营业或路线 |
| 场景 `products` | 每项仅 `product_id` | 引用商品定义，不携带卖方、报价或门店库存 |
| 场景 `supplies` | `supply_id`、可选 `initial_stock` | 引用冻结供给，可显式覆盖实例库存初值 |
| 运行时场景 | 完整 `map_dataset`、`locations`、`routes`、`products`、`supplies` | 只解析场景选中的范围；引用之外不得行动 |
| 人物声明 | `qualifications` | 显式资格集合；到达与购买分别验证，不能从画像推断 |

地图元数据固定 WGS84、时区、区域、来源时间、许可及通知。地点同时具有 `kind`、`business_kind`、
`location_kinds`，分别服务交互菜单、经营业态和活动环境；品牌可以缺失。供给以 `supply_id` 标识，
`(location_id, product_id, seller_account_id)` 唯一；多个卖方账户允许共享经营角色。
商品没有卖方、门店、购买报价、库存或配送处理时长；商品效果与取得后的持有仍按 `product_id`。
运行时 `ProductConfig` 继承 `InventoryItemConfig`，共享 `category_id`、`inventory_unit` 和使用语义；购买可供性由独立供给补充。

当前版本边界为地图文件结构 1、商品目录 major 4、品类契约版本 2、交换规则绑定版本 2、场景结构 3、世界状态结构 7。
这些版本分别约束不同输入，不能相互替代。场景结构保持 3；含旧键 `max_consecutive_rejections` 的场景按未知字段拒绝。旧商品门店字段、旧场景内嵌地点和旧世界状态直接拒绝，
不保留迁移脚本、兼容解析或回退数据源。

### 营业与报价声明

| 结构 | 字段 | 约束 |
| --- | --- | --- |
| `QuoteConfig` | `price_version`、`price_fen` | 非空报价版本与正整数分价格，取值由供给声明 |
| 时态声明共有字段 | `effective_from`、`effective_to`、`reviewed_on`、`still_valid_on`、`source` | 生效时刻带时区，结束可空；适用区间左闭右开，核对日期不得晚于可信截止日；来源保留事实或假设依据 |
| `OpeningCalendar` | 时态共有字段、`weekly`、`exceptions` | 周声明与日期例外使用同一时间口径 |
| `weekly` | 周一至周日的字符串键 `"0".."6"` 到开放区间映射，七天全部声明 | 每段以分钟 `opens`/`closes` 表示；跨午夜的 `closes` 大于 1440 |
| `exceptions` | 日期到开放区间映射 | 优先于每周声明；空区间明确闭店，不能继承前日跨夜营业 |
| 营业结果 | `open`、`closed`、`unknown` | 未生效、声明间隙与未知资料保持未知，不默认全天开放 |

同类生效声明不得重叠。所有营业消费者使用同一解释入口；运行例外覆盖和日历边界进入事件时钟。
报价未定义时不能购买，不使用零价或最后一次旧值。核对有效性在发布时校验，不把近期资料冒充实验日期事实。
`source` 严格使用 `DataSource`：`kind`、`url`、`retrieved_at`、`license`、`notice`、`fact_cutoff`、`applicability`。
当前报价状态恢复也校验该结构；空对象不能充当来源。显式调价和补货声明使用自身的场景假设来源。

### 地点发现与购买身份

人物通过 `start_activity` 选择可读地点发现对象与动作；以下筛选值是冻结观察对应的私有绑定，
不让模型填写内部身份、游标或精确时长。引擎的发现请求保存 `product_id: str | null`、
`business_kind: str | null`、`travel_mode: walking | driving`、`max_travel_seconds: int | null` 与
`cursor: str | null`，五个字段均必填。
分页以实际呈现条数为界，返回 `total/returned/returned_places/next_cursor`；游标绑定地图、选区、筛选与起点，
时间推进或世界版本更新不使同一查询失效。越界或篡改游标明确拒绝，不自动重启首屏。
道路范围使用分时路线预计秒数。已知超范围、不可达和资料未知分别表达，未知结果保留地点身份与原因。
路线状态为 `reachable/unreachable/unknown`；成功和已证不可达记录必须绑定原始响应及哈希。
每个所选有向端点与方式组合都必须有记录；`unknown` 可仅有来源解释且禁止出行。真正缺记录属于发布错误，
既不能伪造 `unreachable`，也不能用漏项隐式表达 `unknown`。

发现结果经 `action_result` 和下一次观察返回，包含实际筛选条件及有序地点/供给结果。
浏览是活动，结果随浏览结束或下一次观察回灌；没有进行中活动时同一秒再决策。
`discovery_result` 保留名称，当前无触发来源。
每行公开实际返回的路线、分时路程和费用、营业、资格、报价及版本、库存、履约和来源；不得注入未返回供给。
完整记录结构见[地图与门店供给证据](design/05-recording-engine.md#地图与门店供给证据)。

公开供给行保留原始 `price_fen`、`price_version` 与 `quote`，同时提供 `purchase_pairing_state`、`purchase_coefficient` 和 `purchase_price_fen`。
配对为 `defined` 时，系数来自冻结声明，`purchase_price_fen` 通过同一 `purchase_amount` 入口计算购买一份的实际金额；报价未知时金额为 `null`。
配对不是 `defined` 时保留原始报价和配对状态，系数及实际金额均为 `null`，`purchase_allowed=false`；动机跳过该供给，但不隐藏已持有物品。
人物的余额判断和价格排序也使用该入口计算的一份金额。多份请求必须用原始报价、数量和系数重新求整行金额，不能把已取整的一份金额乘以数量。
原始报价保持正整数分；明确的 `defined` 配对可以有零系数并产生零额付款，仍正常扣供给库存和履约，不能把 `no_effect` 当作零价购买。

购买的内部结算行使用 `supply_id`、数量和冻结 `price_version`；整单一至五项，
同一地点、同一卖方且无重复供给。模型只选择公开供给的可读名称和购买动作。
消费每行仍用 `product_id`、`quantity`。自提必须在所选门店，不要求配送路线；配送按供给支持、范围及路径验证。
每个订单行调用 `evaluate_purchase(quote.price_fen, quantity, coefficient)`，以 Decimal 对整行乘积向上取整分，形成该行的 `total_price_fen`；整单 `total_price_fen` 为各行金额之和。不得先对单价取整再乘数量。
报价身份和值保持原声明，付款账户取人物账户、收款账户取供给 `seller_account_id`；品类配对继续校验，禁止静态商品转账边替代。
错误字段继续使用既有 `error_code`；未知营业、闭店、资格不足、无报价、旧报价、库存不足、履约不支持、
路径不可达和资料未知必须可区分，不能统一归成“未找到商品”。英文错误与日志不得泄露凭据。

### 持久化当下事实

| 世界字段 | 身份与语义 |
| --- | --- |
| `supply_stock` | 供给身份到非负库存；替代按商品共用的门店库存 |
| `supply_quotes` | 供给身份到当前报价状态或 `null`；未知保留空值，不从初始目录补回 |
| `opening_exceptions` | 地点身份到日期开放区间的世界覆盖；与冻结定义共同求值 |
| `presentation_state` | 购买呈现按人物与供给身份记录，持有使用保留商品身份 |

世界结构版本为 `7`，旧购买请求与旧场景直接拒绝，旧检查点（含版本 6）以版本不符拒绝。人物的 `visible_supply_ids`、
`supply_browsed_at`、`supply_browsed_location` 分别保存实际可见供给集合及逐供给的呈现时刻、呈现地点映射。
三者的供给键集必须一致；只更新本次实际呈现行，不延长未呈现供给的可见期。资格、库存、
报价、营业例外和轮转状态随共同检查点往返；地图或代码身份改变后不能打开或恢复为同一运行。

### 供给结算与事件载荷

`price_change` 携带 `supply_id/price_version/amount_fen/source`，显式调价必须用新版本和自己的场景假设来源。
调价要求到期时存在有效报价，新的起点为 `due_at`，终点保留原报价到期或下一声明边界；无有效声明不能凭空生成永久价格。
`supply_quote_change` 在声明生效与失效边界完整替换报价，声明间隙写入 `null`；同一边界先切换声明，再执行该秒显式调价。
`product_release` 携带 `supply_id/stock/source`，仅把该供给库存设置为声明数量，允许明确置零；来源必须标为场景假设。

一次配送使用单个 `delivery_arrival` 事件，其 `order_ids` 列表引用整单全部项目。结果的 `orders` 保存各项实际交付状态，
`inventory_changes` 仅记录本次新增数量；重复到期不再增加库存且该列表为空。订单保留 `supply_id/product_id/location_id/`
`seller_account_id/price_version/route_id/source` 与金额，人物持有仍以目的地和商品身份合并。

## 社交接口

社交在 `LifeEnv` 内实现，复用现有消息通道与事件类型，不装配 AS2 社交空间。行为规则归 `docs/dev/SPEC-life-runtime.md`「社交最小可行版」，本节定义字段面、闭集与数值边界。

### 场景联系人

联系人按人物在场景中声明，允许缺省为空；同一人物内 `contact_id` 唯一。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `contact_id` | 文本 | 联系人身份，同一人物内唯一 |
| `salutation` | 文本 | 称谓 |
| `relation` | 文本 | 关系 |
| `closeness` | 整数，0–30 | 亲密度 |
| `channels` | 文本数组 | 可用联系渠道，元素非空；引擎据此约束可达性 |
| `reply_policy` | 枚举 | 回复策略，取下方闭集 |

```python
ReplyPolicy = Literal["none", "template", "model"]
```

### 消息与收件箱

```python
MessageKind = Literal["direct", "chat_invite"]
```

| 量 | 值 |
| --- | --- |
| 消息信封字段 | `message_id`, `kind`, `sender_id`, `recipient_id`, `text`, `created_at` |
| 消息正文上限 | 300 字符，按字符数计，超出即拒绝，不截断；直发消息与交谈邀请同一上限 |
| 交谈邀请文本 | 由引擎生成，携带发起方姓名与邀约时长 |
| 送达记录 | `environment_event` 的 `data.kind` 取 `message_arrival`，携带 `message_id`、`recipient_id`、`occurred_at` |

### 交谈与通话

| 量 | 值 |
| --- | --- |
| 交谈请求 | `kind=chat`，要求双方共同在场且对方可交谈（对方清醒且未在主动游戏） |
| 交谈时长 | 由场景 `activity_feedback_seconds` 派生后截取到 60–7200 秒，受理结果写明计划时长 |
| 通话请求 | `kind=call`，`partner_id` 取场景联系人身份 |
| 通话时长 | 由场景 `activity_feedback_seconds` 派生后截取到 600–3600 秒，受理结果写明计划时长 |
| 社交需要缓解速率 | 交谈被接受：双方各 0.5/小时；交谈被拒绝：发起方 0.2/小时；通话：0.4/小时 |
| 接受判定 | 发起人正有以对方为伙伴标识的进行中交谈活动时，对方对发起人选择交谈即接受，对方交谈的计划结束时刻对齐发起人；一方结束时，对方以其为伙伴的进行中交谈同秒结束（`partner_ended`） |
| 资源占用 | 交谈、通话占用 `voice`、`ears`（[时间与能量规格](dev/SPEC-time-and-energy.md#并行准入)）；交谈要求双方在场且都不在途中，通话可与驾车并行；不可暂停，取消任意时刻允许并保留已结算进度 |

### 联系人回复

| 量 | 值 |
| --- | --- |
| 策略行为 | `none` 不回复；`template` 落一条占位回复文本；`model` 触发一次独立模型调用 |
| 幂等 | 每条收到的消息最多回复一次，幂等键为收件消息的 `message_id` |
| `model` 回复请求字段 | `contact_id`, `reply_to_message_id`, `recipient_id`, `incoming_text`；`thinking` 固定 `disabled`，`max_chars` 固定 200 |
| 回复文本上限 | 200 字符 |
| 回复消息身份 | `reply-<被回复消息身份>` |
| 回复影响 | 只进入收件方收件箱，不改变任何资源或任务 |
| 送达时刻 | 回复在生成后登记为预排送达事件（世界状态 `pending_events`，类型 `message_arrival`），延迟为实验常量 `CONTACT_REPLY_DELAY_SECONDS`（缺省 300 秒）；到期写入收件箱并留 `message_arrival` 送达记录，收件人以 `message_arrival` 唤醒，睡眠中过滤 |
| 回复落库标记 | 域记录的 `source_kind` 为 `model_npc`；足迹事件的 `source_kind` 闭集取 `environment`、`model`、`scenario`、`external`，不含该值，落库映射为 `model`，原值保留在事件 `data.source_kind` |

| 回复事件 | 形状 |
| --- | --- |
| 待回复登记 | `environment_event`，`data.kind` 取 `model_npc_reply_pending`，携带 `request`（`model` 回复请求字段面） |
| 回复完成 | `environment_event`，`data.kind` 取 `model_npc_reply`，携带 `source_kind`、`reply_to_message_id`、`request`、`response`、`usage`、`reply`；在回复生成时写入，收件箱条目在预排送达到期时才出现 |

### 社交拒绝错误码

| 错误码 | 触发条件 |
| --- | --- |
| `RECIPIENT_NOT_FOUND` | 消息收件人既非同世界人物也非本人声明的联系人 |
| `MESSAGE_TEXT_TOO_LONG` | 消息文本超过 300 字符 |
| `CHAT_PARTNER_NOT_AVAILABLE` | 发起交谈时对方不在同一地点或不可交谈（睡眠/主动游戏中） |
| `SOCIAL_PARTNER_NOT_FOUND` | 交谈对象不在世界人物中 |
| `CONTACT_NOT_FOUND` | 通话对象不是本人声明的联系人 |
| `CALL_DURATION_OUT_OF_RANGE` | 通话时长超出 10–60 分钟 |
| `CONTACT_REPLY_NOT_MODEL` | 对非 `model` 策略的联系人登记模型回复 |
| `RESOURCE_CONFLICT` | 新建或恢复活动的个人独占资源与本人 ACTIVE 活动冲突，或组合内部冲突；占用按[时间与能量规格](dev/SPEC-time-and-energy.md#并行准入)重列 |
| `SOCIAL_ACTIVITY_CANNOT_PAUSE` | 对交谈或通话发起暂停 |
| `SOCIAL_ACTIVITY_OPERATION_UNKNOWN` | 交谈或通话的变更操作不在取消与恢复之内 |
| `ACTIVITY_TOO_SHORT` | 折算后的社交活动时长不足 60 秒 |

交互对象照常列出，不剪裁；使用与执行器相同的活动资源声明检查本人正在执行的活动，
条件未满足或占用冲突的动作附一句可读说明。
业务拒绝（含 `RESOURCE_CONFLICT`）后按[时间与能量规格](dev/SPEC-time-and-energy.md#规则)第一章
在同一秒再决策，受 `max_same_moment_decisions` 约束。

## 观察投影字段

引擎在每个观察边界装配一棵观察字典，供宿主渲染提示与生成工具身份枚举。它分三层：进入提示的两层投影 `scene` 与 `facts`、只进事件证据的 `body`、以及只供宿主工具面使用的行数据。三段投影的内容规则、上限与呈现细节归 `docs/dev/SPEC-life-runtime.md`「观察投影与提示合同」，本节定义字段面。

### 场景段落与事实清单

```mermaid
flowchart LR
    W[世界状态] --> S[scene 中文场景段落]
    W --> F[facts 事实清单]
    W --> B[body 体感分级与数值]
    W --> T[事件级工具支持行]
    S --> U[user 消息]
    F --> U
    B -->|仅分级| U
    B -->|数值| E[观察事件证据]
    T --> SCH[工具 Schema 身份枚举]
```

| 层 | 字段 | 说明 |
| --- | --- | --- |
| `scene` | `current_time`, `location`, `people_here`, `felt`, `current_activities`, `paused_activities`, `just_ended`, `day_plan`, `inbox_head`, `inbox_has_older_unread`, `todos_summary`, `reachable_places`, `inventory`, `local_food_summary`, `same_moment_note` | 中文场景渲染输入；`current_activities` 只列进行中活动（「正在做」只来自它）；`just_ended` 列刚结束、待收尾的活动名，渲染为「刚结束：<活动名>」；`day_plan` 按清单顺序显示意图与已推进状态，设了提醒的目标另带 `reminder`（「提醒 09:30」），渲染为「今天想推进：工作（未推进，提醒 09:30）」；`local_food_summary` 的含义见「已核验经历与当地餐食」（饥饿为「饿」及以上时是吃饭途径句）；`same_moment_note` 在同刻上限的受限轮中是一句可读说明、不写次数，其余轮次为空串；人物自述不进 scene；不复制完整执行结果 |
| `facts` | `current_time`, `people_here`, `contacts`, `inbox`, `tasks`, `routes`, `products`, `inventory`, `day_plan`, `felt`, `drive_summary`, `interactive_objects`, `progress`, `experiences` | 目标、交互对象和进度的结构化投影；任务、活动、会话与对象使用可读名称；`day_plan` 为完整当日目标对象或 null |

| `scene` 行 | 字段 |
| --- | --- |
| `reachable_places` | 地点名称及 `open/closed/unknown` 营业状态；路线分钟数只保留在 facts 与工具支持行，不把未知写为闭店；例外：`local_food_summary` 的吃饭途径句可带一条途径的人读时长或送达时刻 |
| `inbox_head` | 按 `created_at`、`id` 排序后最近 3 条收件头，展示保持时间先后 |
| `inbox_has_older_unread` | bool；未展示集合存在未读项时为 true |
| `todos_summary` | 待办一句话，无待办时为空串 |

| `facts` 行 | 字段 |
| --- | --- |
| `people_here` | 可读人物名称、正在做的事、是否可交谈；内部人物身份只在私有绑定和证据中 |
| `contacts` | 可读联系人名称、称呼、关系；亲密度、渠道与回复策略是引擎结算内部参数，不进提示 |
| `inbox` | 可读主题、发送人、时间、种类及已读状态；同名邮件以自然语言上下文消歧 |
| `tasks` | 唯一可读任务名称、截止时刻、剩余合同时间与新揭示材料范围 |
| `products` | 实际呈现的供给以可读商品和门店名称展示；系统单向提供报价、可购金额、库存、营业、资格、履约和来源；食品（`use_modes` 含 `eat`）另带 `energy`，写「每份约 N 千卡」，N 为每份热量按 5 进位取整到 10，不足 5 千卡写「几乎不含热量」，非食品不带；支持配送时以 `delivery_eta` 写人读送达时刻（例如「约 13 小时后送达，明天 11:16」），送达地不在可配送目的地或配送路线未知、不可达时写「暂时送不到这里」，不给原始秒数；内部供给、商品与报价版本身份只在私有绑定和证据中 |
| `routes` | 目的地名称、可读出行方式、预计时间、费用、费力 `effort` 与可行性；`effort` 按出行方式的代谢当量取「轻松」（MET < 2）、「一般」（2 ≤ MET < 3）或「费力」（MET ≥ 3），未核实的路线也给；`feasible` 只表示可达（`status` 为 `reachable`），不按能量计算；精确结算值由私有冻结绑定提供 |
| `inventory` | 地点名称、商品名称、正数数量；食品带 `energy`（与 `products` 同口径）；他处持有物带所在地点列出，并以 `note` 注明只能在当地使用，不算手边可用 |
| `day_plan` | 由零点计划生成、人物以 `revise_day_plan` 修订的当日目标；目标行按清单顺序给名称（「目标：<意图>：<文字>」）、意图、是否已推进，设了提醒的另带 `reminder`（「提醒 09:30」），人物写的 `progress_note` 标「你自己记下的，未经核验」；内部活动证据不进提示。当日计划生成之前为 null，空目标与就寝后的清单保持可见 |
| `drive_summary` | 由体感分级、今日工作缺口与待办拼成的一句中文，不含数字权重 |
| `interactive_objects` | 唯一可读对象名称、可读动作短语与前置条件；每个对象只列其合法动作；列表可以为空：空列表是合法状态，决策照常，`start_activity` 照常开放，工具 Schema 不绑定空枚举，所填名称不在本轮对象中时按协议错误在本轮重试 |
| `progress` | `sessions`、`results`、`alarms`；`sessions` 按会话（一段地点停留或一段路）分组、按开始时刻排序，每行为会话名加 `activities[]`；活动行为 `{name, phase, objective_progress, subjective_progress, subjective_status}`，阶段为自然语言（进行中、已暂停、刚结束、已收尾、已结束），客观进度从活动的已做与有效时长及任务累计写成人读时长（例如「已做约2小时，有效约1.5小时，还差约2小时」），有自述时标「你自己记下的，未经核验」；新结果按事件顺序完整回灌一次，期限提醒按任务名呈现；`alarms` 另含当日目标提醒的触发句「到了你给『<目标>』定的 09:30」，在提醒触发的那一秒写出，同一秒后续的同刻轮次中保留，直到人物有进行中活动 |
| `experiences` | 本人最近五条已核验经历写成的中文句子（例如「14:17 开车到了张江办公室」「13:57–14:17 吃了番茄炒蛋」），与私有 `data.verified_experiences` 同一窗口与顺序，用场景的地点、商品与内容名称，出行方式取自所属出行活动，不含任何内部身份 |

`facts.progress.results[]` 的公开字段为 `occurred_at`、`status`、`error_code`、`message`，
按动作可附 `details` 和 `work_review`。`details` 只包含可读对象或商品名称、份数、实际付款金额、
到达地点名称与时间、浏览条目名称、回看结果 `recall` 等语义值；`work_review` 只保留下述公开评审字段。
`recall` 按回看对象分四种：任务给 {名称, 截止, 最近自述}；目标给 {目标, 意图, 是否已推进}；活动给
{名称, 地点, 已做与有效时长, 状态, 自述}；会话给 {地点, 起止, 活动名列表}；自述都标「你自己记下的，未经核验」。
动作、事件、订单、商品、供给、会话、活动、路线、报价版本和账户身份不进入模型结果。

原始观察与结算证据保留完整供给身份、版本及比较行。模型投影中同一供给的当前事实与
本次结果避免重复呈现；发生报价或库存变化时明确告知新值，不要求模型解析内部差异引用。

事件级工具支持行必须包含 `interaction_menu`，字段形状就是当前人物有效的 `InteractionMenu`。
它与该观察一起冻结并进入观察事件证据，供实际模型请求 Schema、本地决策校验和宿主派发共用；
不把所有人物或地点的原始声明送入当前人物的 user 消息。地点变化只影响下一次观察。

`work_review` 仅包含公开汇总、必需事实判断和交付项判断；嵌套字段也按显式清单选择。
两类判断的 `rationale` 不得包含本次评审任一内部参考身份或 SHA-256 摘要。
解析阶段将此类输出作为协议错误，按既有评审预算纠错并保存原始响应；公开投影入口重复验证该边界。
合法理由完整保留，不截短、不生成替代理由。内部身份、摘要与完整评审证据只留在宿主侧。

### 活动控制行

观察顶层 `active_activities` 是稳定按 started_at、activity_id 排序的活动控制列表，无活动时为空列表。
删除 active_activity 与人物 current_activity_id，不接受单项/列表双形状。
WorldState.activities 是唯一活动事实源；人物另外持久保存 body_settled_at 及
已实际交付的当前威胁 delivered_warning_signatures。状态、资源及时间不变量见
[并行活动规格](dev/SPEC-dayrun-behavior.md#同一人物的活动集合)。

每项恰好八键，供 LifeAgent 冻结继续、暂停、取消和变速的目标身份；内部 data 不直接复制进提示。

| 字段 | 说明 |
| --- | --- |
| `activity_id` | 本人活动的不透明身份 |
| `kind` | ActivityKind 枚举值 |
| `status` | `active_activities` 一组恒为 active，仅从活动表实际 ACTIVE 项取得；`pending_conclusions` 一组为实际终态 |
| `title` | 关联内容或任务标题，无关联时为 null |
| `progress` | 中文进度短语，与 scene 活动列表同源 |
| `elapsed_seconds` | 已执行整数秒 |
| `planned_duration_seconds` | 计划整数秒 |
| `data` | 独立深拷贝；游戏保留真实 game_session_id，与同次观察会话一致 |

观察顶层 `pending_conclusions` 另列待收尾活动的控制行（状态为 `completed`、`failed` 或 `cancelled` 且
`concluded_at` 为空的活动；`truncated` 不收尾），字段同上八键，按同一顺序排序，只接受 `conclude_activity`。
`paused_activities` 仍提供暂停活动的完整记录及 remaining_duration_seconds；恢复不查能量。
每项按独立生命周期控制；
不从列表第一项猜测目标，不隐式暂停未点名活动，缺失或非法控制结构明确失败。
scene 的 `current_activities` 必须表达集合，每项为 title、progress、verb；
播客的 verb 为“听取”，其余为“进行”。不能把其中一项描述为人物的全部活动。
观察 `occupied_resources` 是这些 ACTIVE 显式资源声明的排序并集；不另存状态，
候选、人物工具 Schema、本地验证及环境准入均按同一注册表资源交集判定。

组合启动结果 data.activities 提供各独立活动记录，顶层 activity_id 为 null。
单项动作仍引用它自己的 activity_id。组合请求只在全部子活动终结后产生一次动作聚合终态，
各子活动此前的完成或取消留在自身 activity_progress；完整聚合规则见特性规格。

### 已核验经历与当地餐食

observation.data.verified_experiences 是本人在该观察事件之前已提交的最近五条已核验经历，
按 event_seq 正序；每项包含 kind、agent_id、activity_id、source_event_id、event_seq、
occurred_at 和种类载荷。这些字段保留在观察事件中作证据，模型只见由同一窗口写成的句子。
模型不能写入它，来源与事件正文由[记录引擎设计](design/05-recording-engine.md)维护。

饥饿低于「饿」时，scene.local_food_summary 是当前地点餐食的一句中文说明，依据真实本地供给及
当时购买条件。无可购餐食时如实说明；不能从有限窗口为空推出全店无食物，也不因该摘要授权未呈现供给。
饥饿为「饿」及以上时，同一字段改为吃饭途径句：按手边能吃的、在路上的、这里就能买的、能送到这里的、
附近能买的、都没有六档，取第一条成立的；只引用本轮观察的事实，不授权未呈现的供给；在途送达距现在
晚于 3600 秒时另补一条。六档的取数与例句见[时间与能量规格](dev/SPEC-time-and-energy.md#让人物趁早吃)。
正式餐馆覆盖与音频片段合同见 [一日行为规格](dev/SPEC-dayrun-behavior.md)。
内容品类增加 podcast_episode，沿用 watch 播放活动及内容进度；官方段落全部播放后才揭示文本。

### 体感与动机证据

`body` 分两组：`levels` 是进入提示的体感分级短语，`values` 是只进事件的数值，含 `appetite`、`sleepiness`、`entertainment_need`、`social_need`、`mood_valence`、`sleep_pressure`、`energy_kcal`、`sleep_deficit_hours`。动机评分器继续计算并写入 `motivation_internal`，只供评估与研究，不进提示。

人物身体常量 `chronotype_midsleep` 与 `sleep_need_hours` 属于人物状态与人物档案，只驱动世界层结算，不是观察投影字段；确定性口味权重 `taste_weights` 属于人物状态，由卡片口味与内容题材匹配得到，只供内部评分器与浏览排序使用，不进提示。

### 可见期与提示预算

| 量 | 值 |
| --- | --- |
| 门店供给可见期 | 只覆盖实际呈现的供给；逐供给记录后超过 1800 秒或离开呈现地点失效，人在门店也不能自动授权未呈现供给 |
| 工作材料单次揭示上限 | 每任务 2000 字符，只揭示自上次决策以来的新读区间 |
| 收件箱头展示条数 | 3 |
| 本轮新执行结果 | 全部在进度层回灌一次，不按条数截断 |
| 完整模型请求容量 | 每次发送前计数 system、工具定义、人物卡片、场景、事实、记忆、历史消息和预留输出 |
| 进度层浓缩阈值 | 完整请求达到所用模型上下文窗口的 85% |
| 人物卡片汉字上限 | 600 |
| 决策自述汉字上限 | 200 |

完整请求达到阈值时，先按会话合并 `facts.progress.sessions` 中较旧会话里已收尾的活动，
保留每组最新客观结论、自述和活动数；重算仍达阈值时，再合为一条总体历史摘要，
保留总活动数和最近结论、自述。进行中、暂停中与待收尾的活动完整保留；当前期限提醒、本轮新结果、
权威状态和事件不动，也不压缩其他层。浓缩后仍达到阈值或超过输入额度时记录分层 token 用量，
明确容量故障与英文错误日志。决策自述的空与超长属于可重试一次的协议错误；
收件头是展示选择，材料揭示是增量窗口，门店供给可见期是可见性条件，不属于容量故障。

工作公开评审只取 `public_work_review` 定义的字段：`accepted`、`required_fact_total`、
`required_fact_passed`、`required_fact_missing`、`missing_content_categories`、
`required_fact_assessments`、`delivery_assessments`。逐项判断只含公开内容品类、判断、
理由与经来源核实的原文片段，不含内部参考身份、答案、哈希、资源账户或完整活动快照。
具体时序与无损回灌行为见 [决策上下文规格](dev/SPEC-decision-context.md#登记确认与结算结果)。

### 候选与呈现合同

候选的生成、资格、排序与轮转是行为规则，归 `docs/dev/SPEC-life-runtime.md`「候选契约与呈现规则」；本节只定义该章的字段面、闭集、返回体结构、场景常量名与派生量字段。

### 候选对象与来源

```python
CandidateKind = Literal["product", "lifestyle", "content", "game", "task"]

CandidateSource = Literal["on_site", "browsed", "carried", "activated"]
```

`CandidateKind` 保持原闭集。购买候选 `kind=product` 的 `object_id` 取 `supply_id`，并保留商品引用 `product_id`；
持有与使用候选仍按商品身份。用途区分购买与使用，排序、游标、排除统计及轮转不能把不同门店供给合并。
浏览或发现只授予实际呈现供给的可见资格，不能把整份商品目录变成购买凭据。

| 来源 | 含义 |
| --- | --- |
| `on_site` | 当前地点的常驻对象 |
| `browsed` | 浏览或激活过、且仍在时效内的对象 |
| `carried` | 在当前位置手边持有的对象 |
| `activated` | 由画像激活集在本次场景中激活的对象 |

### 排除原因码

被排除项携带的原因码取下列闭集；原因码是稳定标识，不得按人类可读文案替代。

```python
ExclusionReason = Literal[
    "ELIGIBILITY_LOCATION_ABSENT",        # 人物不在该对象要求的商家或使用地点
    "ELIGIBILITY_LOCATION_KIND_MISMATCH",  # 地点类交集为空
    "ELIGIBILITY_CLOSED_HOURS",           # 不在营业时段
    "ELIGIBILITY_OPENING_UNKNOWN",        # 当前营业声明未知，不能当作闭店
    "ELIGIBILITY_OUT_OF_STOCK",           # 库存为零
    "ELIGIBILITY_DELISTED",               # 目录级下架（墓碑语义，保留可读）
    "ELIGIBILITY_RESOURCE_CONFLICT",      # 资源占用冲突
    "ELIGIBILITY_DURATION_OUT_OF_RANGE",  # 时长越界
    "ELIGIBILITY_DISABLED",               # 不在本人启用集内
    "ROTATION_DEFERRED",                  # 本次呈现名额已满
]
```

`ROTATION_DEFERRED` 在闭集内，计为一个排除项，能取到稳定的原因码；它与其他排除项的差别在于作用范围：只影响本次呈现的结果，不改变对象状态、不写目录。

### 排序键与游标

| 量 | 字段 | 说明 |
| --- | --- | --- |
| 排序键 | `taste_weight`（定点整数，四位小数口径与 `taste_weights` 一致）、`attention_prior`（定点整数）、`object_id`（决胜键） | 主键为口味 × 注意力先验；决胜键取对象标识，保证全序 |
| 游标 | `cursor` | 上一页返回的稳定游标；首屏显式为 `null`，后续原样传回，不解析内部结构 |
| 排序键版本 | `order_key_version` | 排序键口径的版本号，参与游标的身份 |

游标身份由（冻结目录摘要、世界状态版本、`order_key_version`、游标）四项决定。

### 下推返回体

图鉴与下推动作由 `start_activity` 的可读对象与动作发起；对应的 `action_result.data` 返回体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool` | 文本 | 工具名 |
| `category` | 文本 | 品类键 |
| `returned` | 整数 | 本次返回条数 |
| `total` | 整数 | 该分区过滤后的候选总数 |
| `next_cursor` | 文本或 `null` | 下一页游标，最后一页为 `null` |
| `items` | 数组 | 本次返回的条目行 |
| `excluded` | 数组 | 原始结果逐项保存 `{object_id, kind, reason}` 及存在的其他排除属性，`reason` 取 `ExclusionReason`；公开投影按全部非 `object_id` 属性分组，以 `object_ids` 保留各项身份 |

浏览游标解码后的排序键必须是两项整数与一项非空对象身份，偏移必须是非负整数；
布尔、浮点数、非有限值、缺项和 `null` 均为无效游标，不强制转换类型。
无效游标只拒绝当前动作并允许下一轮正常查询，不改变世界或呈现状态。

`returned` 取代此前的 `count` 告知值；`count` 不再作为返回体字段。
公开排除分组必须能够逐项还原原始排除行，不能只按原因码合并后丢失其他属性。
直接下钻和有时长浏览完成共用这条投影规则，原始事件仍保留逐项行。

### 场景常量

下列常量在场景层声明（不放 `catalog/manifest.json`，避免一次增量即触发目录版本 major 变更）：

| 常量 | 含义 | 默认值 |
| --- | --- | --- |
| `candidate_presentation_limit` | 单次呈现上限 | 20 |
| `candidate_pool_limit` | 单个检索分区池上限，只用于计数与下推 | 1000 |
| `rotation_quota_ratio` | 每次呈现留给长期未呈现对象的换新名额比例 | 0.15 |
| `rotation_stale_seconds` | "长期未呈现"的判定阈值 | 无缺省，必须声明 |
| `retrieval_index_threshold` | 单一品类池超过该规模时启用倒排与 facet | 1000 |

### 派生量字段

轮转判据与曝光留痕所需的派生量；它们是时刻或计数，不含偏好推断。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `last_presented_at` | 整数，UTC 纪元秒 | 按（人物、对象标识）记录的上次呈现时刻；键为人物与对象标识，值为时刻 |

`WorldState` 新增字段 `presentation_state`，承载 `last_presented_at`。轮转状态按对象逐个记录时刻，不按检索分区记游标：游标位置以后的对象优先与「哪些对象长期未被呈现」不是同一个判据，未访问分区里刚被呈现过的对象会被误当成长期未呈现。地图与门店供给特性再次更新 `WorldState.schema_version`，新增供给状态并严格拒绝旧结构；具体版本与状态字段见「地图与门店供给合同」。

### 地点类词表

地点类取生活活动地点类的单一封闭集，地点声明与条目声明同名同值：

```python
LocationKind = Literal["home", "office", "outdoor", "fitness", "venue", "anywhere"]
```

| 字段 | 位置 | 含义 |
| --- | --- | --- |
| `location_kinds` | 唯一地图地点目录，场景通过地点身份引用 | 该地点满足哪些活动环境，必填、非空且为实际环境闭集子集；不由经营业态推断，地点侧禁止 `anywhere` |
| `location_kinds` | 内容品类与生活活动条目声明 | 该条目需要哪些地点类；含 `anywhere` 表示恒可行 |
| `required_location_id` | 目录对象 | 具体地点实例要求；与地点类判定并列，取交集即可行 |

准入要求与品类覆盖要求归 `docs/dev/SPEC-life-realism.md`「世界素材品类要求」；交集判定的运行期行为归 `docs/dev/SPEC-life-runtime.md`「候选契约与呈现规则」。

### 禁止进入提示的字段

观察字典中不得进入提示的字面键：`world_state_version`、`end_time`、`affordance_contract`、
`interaction_menu`、`interaction_menus_by_location_kind`、`observation_event_id`、
`interaction_bindings`、`motivation_hints`、`game_catalog`、
完整活动快照（`activity_snapshot`、`activities`；`facts.progress.sessions[].activities` 是按会话分组的活动进度行，
不是完整快照，允许进入提示），内部身份键 `agent_id`、`activity_id`、`source_event_id`、`event_seq`、`product_id`、
`location_id`、`destination_id`、`content_id`（与[时间与能量规格](dev/SPEC-time-and-energy.md)第七章
「发给模型的内容不含内部身份」一致），以及嵌套的 `profile.motivation` 与
`profile.entertainment_preferences`。`routes` 条目只保留目的地、方式、分钟、票价、费力与是否可达，
发现结果只提供实际返回地点和供给的公开事实，不能复制全量地图或世界库存。
路线坐标与几何不得进入提示。事件级工具支持行（当前有效菜单、完整任务、路线、商品、门店供给、库存物品使用合同、
可供性合同、游戏目录、内容进度、工作进度、邮件头、日历与地点目录）只供宿主生成
私有绑定、可读对象名称与动作短语，不原样进入提示。`facts.interactive_objects[].actions` 是公开的
对象动作短语闭集，允许进入提示；它不授予读取完整可供性合同或内部状态的权限。

事件级 `inventory_items` 为当前地点正数持有物的使用合同，覆盖在售商品和独立库存物品；
每行只含 `product_id`、`name`、`use_modes`、`use_duration_seconds`、`required_location_id`。
持有数量仍以 `available_inventory` 和人物可见的 `facts.inventory` 为准，不复制到使用合同。
宿主据此约束 `consume` 的身份、使用方式、时长和地点，并在动态工具说明中关联物品身份与公开名称。
`purchase` 读取实际门店 `supplies` 的当前报价与供给库存；商品定义和 `inventory_items` 只提供规格与使用合同，不能为持有物虚构供给、报价或可购买性。
`consume.items[].quantity` 是请求份数，配方实际输入还要按冻结规则与配对系数缩放。
人物侧保持请求模型的数量范围与数组边界校验，不直接把份数与物品持有量比较；
实际库存是否足够由环境结算路径检查，拒绝结果在下一次观察返回。

结果投影同样递归禁止内部评审字段（`reference_id`、`reference_assessments`、`oracle`、`oracle_path`、
`input_path`、`expected`）、摘要字段、完整 `activity`、账户及资源变化字段，以及数值身体状态、
身体常量与 `taste_weights`。体感分级文字仍可见；不能把禁止的数值换一个嵌套位置重新送入提示。

## 人物决策提示与生成参数

每次提供决策机会时，人物先决策自述，再进入可多回合的行动循环；正常无查询无纠错需要自述与动作两次请求，
世界请求登记即结束本轮。
每次请求都经同一证据路径保存完整请求与响应。请求模板与生成参数在本节定义，
上下文与工具通道的行为归 [决策上下文规格](dev/SPEC-decision-context.md)，其他人物行为归 `docs/dev/SPEC-life-realism.md`。

### 请求组成

| 阶段 | 消息 | 工具 |
| --- | --- | --- |
| 决策自述 | system 身份前言、固定一节「怎样过一天」与一至三句第一人称中文自述的生成要求；user 依次为人物卡片、场景段落、记忆摘要 | 不带工具 |
| 行动 | system 身份前言与同一节「怎样过一天」；初始 user 同左，再追加事实清单与一句「你刚才心里想的是：<决策自述>」 | 八个人物工具（见「人物工具与管理接口」）与预算允许的记忆查询；世界请求登记即结束本轮，没有 `finish` |
| 零点计划 | system 为零点计划的中文模板；user 为一份 JSON 输入（见下文） | 唯一严格工具 `submit_day_plan`，`tool_choice=required` |

身份前言与只读前言是固定的中文模板。只读决策不启用决策自述阶段，其 user 消息由人物卡片、记忆摘要与外部只读问题组成。

身份前言之后空一行接固定一节「怎样过一天」：决策自述与行动两阶段收到同一段文字，每次决策都在；它不由事件触发，
不写进工具描述，只陈述世界规则与生活常识。只读问答的 system 不含这一节。全文如下，首行是标题，其后每段一行：

```text
怎样过一天：
你的一天由你自己安排。时间只在你做着某件事时流逝，所以每次想好之后都要落到一件具体的事上，哪怕只是歇一会儿或去睡觉。
一件事做完了，先给它收个尾，说说做得怎样、有什么想记住的，再开始下一件。
你手上最多同时挂两件事，暂停的也算；暂停的事只能回到原来的地方接着做，在别处可以直接收尾。
要出门，先把手上的事收尾或暂停；交谈和通话不能暂停，只能先结束。路上想听点什么、打个电话，出发时一并开始，到了再收尾，到了地方还可以接着听。
有的事一时做不了，世界会告诉你原因和该先做什么，照着调整就好。
困了就去睡。想在某个时刻起来，就在当天的目标里写上提醒时间；要到明天才起的，睡前收尾时说清几点起。睡着时只有提醒或睡够了才会醒。
工作要在要求的地方做，留意截止时间和已经做了多少；来不及做完时，想清楚先做哪部分。
你会饿、会困、会累。力气来自吃下去的东西，饿着或困着做事会慢，走路比坐车更费力。饿了就趁早去弄吃的，别拖到饿得发慌。
感到饿，或到了你的用餐时段，就把吃饭和手头的事一起考虑：先看手边能吃的和已经在路上的餐食；手边没有时，看看列出的餐食，需要时浏览商品。当前地点没有卖吃的，不代表不能送到这里。下单后要等送到，拿到手才能吃。
花钱量力而行；有人找你时，按你们的关系和当时的情况回应。
说打算做、说已经做过，都不等于真的做了，只有实际开始并完成的事才算数。你可以暂缓一件事，下次有机会时再随身体和处境重新权衡。
```

每次决策只向环境读取一次观察；自述与行动分别渲染所需内容，行动循环的初始 user 只构造一次并逐字节固定。
宿主 observations 随工具调用增长，
其中的完整审计记录不再整体序列化进提示；实际模型消息以原生 assistant/tool 配对承载交互历史。

零点计划是运行时的后台请求，调用方式同联系人回复：不在决策机会中发出，不调用人物，不读写人物工作区与记忆运行时，
不含记忆摘要。规格输入中的「人物卡片与场景的人物背景原文」按如下口径取：人物卡片取场景声明的人物档案（名字、背景原文、
健康习惯、社交风格与作息窗口）；编译后的卡片只存于人物工作区，零点计划不读工作区。
请求由中文模板（system）、一份 JSON 输入（user）与唯一严格工具 `submit_day_plan`
组成，生成参数见「生成参数」；模板、Schema 与参数是模块常量（`src/worldmock/life/day_planning.py`），由运行清单的
`code_sha256` 覆盖。user 输入的键如下：

| 键 | 内容 |
| --- | --- |
| `计划日期`、`生成时刻`、`回顾时段` | 场景本地日期（即写入 `plan_submitted_dates` 的值）、生成时刻（带星期）与回顾时段的起止 |
| `人物` | 人物卡片，取场景声明的人物档案：名字、背景原文 `background_story`、健康习惯、社交风格与作息窗口（午饭、晚饭、就寝时段）；未声明的项省略。编译后的卡片只存于人物工作区，零点计划不读工作区 |
| `已核验经历` | 回顾时段内本人已核验经历的确定性句子，与观察同一写法，按时间顺序 |
| `动作结果` | 回顾时段内本人已结算的公开动作结果写成的句子：消息送达、订单受理（带预计送达或取货地点）、配送送达与工作验收 |
| `收尾记录` | 回顾时段内已受理的 `conclude_activity` 请求记下的逐项 `{时间, 活动, 收尾说明, 想记住的, 说明}`（被拒与重放的请求不列）；`想记住的` 是该项 `memories` 的文字，`说明` 恒为「你自己记下的，未经核验」 |
| `待办任务`、`收件箱`、`在途订单`、`联系人` | 此刻可见、未付款且未过截止的任务（名称、截止、要求的地点）、收件箱（发件人、摘要、是否已读、时间）、本人待交付的订单（商品、数量、预计送达、送达地点）与联系人（称呼、关系） |
| `上一天的目标` | 零点边界上刚结束这一日计划的最后一份快照（含末段推进）：每条目标的意图、文字与是否已推进 |

回顾时段在零点边界是刚结束的本地日（前一日 00:00 至本次零点，结束于本次零点的经历区间属于刚结束的这一日），
在启动路径是生成时刻所在本地日中生成时刻之前的部分（起点恰为零点时为空）。输入不含身体数值、交互对象、私有绑定、未到期的预排事件、
决策自述、人物的主观记忆库与任何内部身份；来源由系统登记（`day_plan_generated.data.source_event_ids`），模型不填身份。
整次请求按 token 计数，达到所用模型窗口 85% 时先裁掉较早的经历句子，仍超限按容量故障处理（运行故障）。

零点计划的中文模板（system）：

```text
你要为一个人写出他这一天的当日目标清单。用户消息是一份 JSON，只含世界已经记下的事实：人物档案与背景原文、
回顾时段里已核验的经历、他本人已结算的动作结果、他收尾时自己记下的话（未经核验，只代表他的想法），
以及此刻他能看到的待办任务、收件箱、在途订单、联系人和上一天目标的推进情况。
按下面的规矩写：
一、写零到八条目标，按优先级从高到低排列；每条从给定的意图里选一个，同一意图只写一条。
二、goal 用一句中文写他打算做什么，可以写粗略的时段和要提前做的准备，不超过三百字；记录里没有的事不要编造。
三、remind_time 只在他需要在某个时刻被提醒或叫醒时填写，取当天本地时间 HH:MM，必须晚于生成时刻、早于当天结束；
不需要提醒就填 null。他收尾时写明了第二天几点起床或几点出门的，就给对应的目标定这个时刻的提醒。
四、只调用一次 submit_day_plan，提交完整清单。
```

输出 Schema：`submit_day_plan` 声明 `strict=true`，参数是 `PlanDayRequest` 的严格 Schema
`{goals: [{intent_kind, goal, remind_time}]}`，三键必填（`intent_kind` 取十一种意图闭集，`remind_time` 为 `HH:MM` 或 null），
没有记忆字段；条数零至八、意图唯一、文字长度与提醒时刻（晚于生成时刻、早于次日零点）由本地校验执行，同「当日目标请求」。

### 工具返回通道

| 返回语义 | 宿主证据 | 人物模型可见内容 |
| --- | --- | --- |
| 生活动作登记 | 完整 `ActionReceipt`，含请求身份与哈希 | 保留 `tool_call_id` 配对的固定登记确认，正文不含登记回执字段；不代表世界动作成功 |
| 即时记忆查询 | 实际查询输入与结果 | 当轮返回的记忆内容，每条只含 {type, text, time, epistemic_status}，不含条目身份 |
| 已结算世界结果 | 完整动作结算事件 | 下一次观察的公开结果投影；不在提交时提前回流 |

工作验收可在提交期间准备，仍只在批量结算后对人物可见。原生工具配对身份不属于内部世界身份，
不能为删除句柄而删除 `tool_call_id`。登记回执在 observation 与 data 两处均不得进入模型正文。

### 回合预算

行动阶段的 ReAct 请求在 system 消息尾部附加一段权威回合预算（阶段、总回合、已用回合、剩余回合与是否仍开放记忆工具）；剩余回合不超过 2 时先附加一句话低预算提示，预算 JSON 始终作为消息尾部的可解析块。

回合预算按去掉 `finish` 后的流程计算：生活决策的行动阶段（`before_action`）只为世界请求保留 1 个回合，剩余回合多于它时才开放记忆查询；决策自述前须剩余 2 个回合（自述与世界请求各一）。只读问答（`readonly`）为只读 `finish` 保留 1 个回合。剩余回合不足以完成本阶段时明确终止本次决策，不发注定失败的请求。

### 生成参数

| 阶段 | `thinking` | `temperature` | `max_tokens` | `tool_choice` | `tools` |
| --- | --- | --- | --- | --- | --- |
| 决策自述 | `disabled` | 1.0 | 8000 | 不发送 | 不发送 |
| 行动 | `enabled` | 不发送 | 8000 | `auto` | 原生生活工具 |
| 工作验收 | `disabled` | 不发送 | 不发送 | `required` | 单个验收工具 |
| 联系人回复 | `disabled` | 不发送 | 8000 | 不发送 | 不发送 |
| 零点计划 | `disabled` | 不发送 | 8000 | `required` | 单个 `submit_day_plan` 工具 |
| 画像抽取 | `disabled` | 0.3 | 8000 | 不发送 | 不发送 |

`thinking` 经请求的扩展体发送。决策自述、联系人回复、零点计划与画像抽取关闭思考；行动阶段开启思考，
使用 `tool_choice=auto`，模型未产出工具调用时由 ReAct 协议纠错回合处理。
工作验收与零点计划关闭思考，使用 `tool_choice=required`。各阶段不发送 OpenRouter 的 `reasoning` 扩展参数。

零点计划与联系人回复一样使用独立客户端：模型与端点取运行清单的模型身份并逐项核对，客户端 `max_retries=0`，
不流式输出。输出不合法（JSON、条数、意图唯一性、文字长度、`remind_time` 的格式或时刻有误）时，在原请求之后追加
一条 user 消息附回英文错误（JSON 键 `PREVIOUS_INVALID_RESPONSE`、`VALIDATION_ERROR`、`INSTRUCTION`）重试，
首次请求之外至多 3 次。这是零点计划自有的校验重试计数，数值与「模型身份」的 `transport_max_retries` 相同，
但分开计数，不借用传输预算；瞬时传输失败与联系人回复一样另用调度器的传输预算。校验重试或传输重试耗尽即运行故障，
保留请求、响应与错误证据，不用空计划或丢弃字段兜底。零点计划的参数不进运行清单的阶段键。

模型请求证据按 `data.purpose` 区分：工作验收为 `work_review`，零点计划为 `plan_day`，人物决策的请求不带 `purpose`。
交付窗口、决策 token 统计与行为指标的决策请求排除带 `purpose` 的请求，质量报告的 `model_usage` 仍计入它们。
人物请求之中，决策自述以 `temperature=1.0` 与 `max_tokens=8000` 作为形状标识：记忆巩固与反思的模型调用
既不带工具也不带这两个参数，因此可与决策自述请求区分。

### 模型身份

| 键 | 值 |
| --- | --- |
| `provider` | `openai_compatible` |
| `base_url` | `https://api.deepseek.com` |
| `requested_model` | `deepseek-flash` |
| `max_retries` | 0；非传输错误不在客户端内重试 |
| `transport_max_retries` | 3；仿真调度器独立的瞬时传输重试预算 |
| `max_react_turns` | 16；决策自述、工具纠错与世界请求共享的有限预算（回合预算按去掉 `finish` 后的流程重算，见「回合预算」） |
| 工具声明 | 每个函数声明 `strict=true` |
| `reasoning_effort` | 不发送 |

运行凭据来自唯一私密文件 `~/.config/worldmock/deepseek.env`，schema 仅允许 `DEEPSEEK_NODE_KEYS` 一个键：值为逗号分隔的至少一个非空密钥段，读取时逐段去除首尾空白、按出现顺序去重后以逗号连接传给仿真 LLM 与 CODER 角色；拒绝符号链接、未知键、空值与空段。多把密钥在 LiteLLM Router 中按 deployment 负载分摊。

仿真调度器按 LiteLLM 的连接、超时、内部服务错误与服务暂不可用异常类型识别瞬时传输失败；
每次逻辑请求最多额外尝试三次，依次退避 2、4、8 秒。传输预算与 `max_retries` 独立计数，
认证、无效参数、协议与业务错误不得借用剩余传输预算；任一对应预算耗尽立即报告真实总尝试次数。
LiteLLM Router 自身不重试，不启用备用模型；全部尝试保留同一逻辑请求的参数。
`transport_max_retries` 与 `max_react_turns` 冻结在两阶段运行身份内，缺失或篡改都拒绝。
恢复人物工作区时，实际 config.json 也必须显式包含整数 `max_react_turns=16`，
与清单一致；缺失、类型错误或错值均在恢复执行前拒绝，不使用 Agent 默认预算。
此传输策略适用于仿真 LLMClient；画像抽取使用独立 requests 客户端，当前仍使用凭据池首把，
不属于运行阶段 Router 的分摊与传输重试范围。LabelKit 的独立请求合同见下文。

阶段键为 `decision_statement`、`action`、`work_review`。逐阶段参数、重试次数、模型身份、提示指纹与工具 Schema 指纹一起冻结进运行清单，运行中不变更；`validate` 复核该身份一致，不一致即拒绝新运行。新建、打开既有运行、恢复及从工作区加载均拒绝旧阶段名称，不提供别名或迁移。显式无模型身份的引擎直驱路径不生成替代模型身份。

### 思考回传

思考模式下的 assistant 工具消息必须完整回传模型给出的 `reasoning_content`，与 `content`、`tool_calls` 一起进入后续带工具的请求。被拒绝的工具调用整批不执行，在现有 ReAct 剩余回合内把协议错误反馈给模型自行重新提交。

### 决策自述证据

决策自述文本写入 `decision_started.data.decision_statement`，并作为该决策的 `model_request` 与 `model_response` 事件保存。决策自述只允许 dreaming 任务读取，不进入动作与意图任务的可见输入，公开来源也不携带它。
质量计数使用 `decision_started_with_decision_statement` 和 `decision_started_missing_decision_statement`。

## 记录引擎接口导航

记录引擎的接口、行为和格式统一维护于[记录引擎设计](design/05-recording-engine.md)。
下列入口只用于跨模块定位，不重复定义合同。

### 数字足迹事件结构

见[事件字段与类型](design/05-recording-engine.md#数字足迹事件结构)、
[保存边界](design/05-recording-engine.md#保存边界)和[来源与可见性](design/05-recording-engine.md#来源与可见性)。

### 数字足迹存储接口

见[存储调用与行为](design/05-recording-engine.md#数字足迹存储接口)。

### 资金事件字段

见[资金守恒验证](design/05-recording-engine.md#资金守恒验证)。

### 共同检查点与恢复

见[保存共同检查点](design/05-recording-engine.md#保存共同检查点)与[恢复与反事实分支](design/05-recording-engine.md#恢复与反事实分支)。

### 持久化结构

见[运行目录与数据表](design/05-recording-engine.md#持久化结构)。

## 品类契约与目录字段

行为规则由 [品类契约规格](dev/SPEC-category-contract.md) 定义；本节固定文件字段与命令输入。
当前原生目录为 `4.0`，品类契约及交换规则的 `contract_version` 均为 `2`；格式版本与运行结构版本分别校验。
原始对象声明、解析后的运行视图与人物浏览使用的活动品类是不同层次，不能混用其字段。

| 文件 | 顶层字段 |
| --- | --- |
| `category-contract.json` | `format_version: 1`、正整数 `contract_version`、`categories`、`pairings`、`sources`、`review` |
| `exchange-rules.json` | `format_version: 1`、相同 `contract_version`、`rules`、`sources`、`review` |
| `objects.jsonl` 每行 | `object_id`、`name`、`category_id`、`values`、`provenance`、正整数 `version` |
| `manifest.json` | `version_major`、`version_minor`、`category_requirements` 和下方四个内容身份字段 |

| 声明 | 字段与闭集 |
| --- | --- |
| 品类节点 | `category_id`、`name`、`parent_id`、`kind`、`slots`、`values`；形态为 `entity`、`person`、`product`、`service`、`contract` |
| 属性槽 | 槽名映射到 `schema`（JSON Schema）、`required`、`unit`；子类与祖先约束同时成立 |
| 属性值 | `absolute`、`relative`、`proportional` 三个槽名映射；同层同槽只选一种 |
| 有向配对 | `actor_category_id`、`target_category_id`、`action`、`state`、`rule_id`、`coefficient`、`review` |
| 配对状态 | `defined` 必须有规则和显式系数；`no_effect` 必须有审核；`undefined` 表示未定义。后二者不携带生效规则或系数 |
| 来源 | `source_id`、`type`、`producer`、`source`、`artifact`、`method`、`paths`、`note`；`paths` 为绝对 JSON Pointer 数组 |
| 来源种类 | `fact`、`anchored`、`assumption`、`scenario`；生产者为 `human`、`data_source`、`model` |
| 审核 | `reviewer`、`reviewer_type`、`evidence`、`reason`、`authorization`；人工为 `human`，委托审查为 `delegated_agent`，两种身份都必须记录非空授权来源 |

动作闭集为 `purchase`、`consume:eat`、`consume:use`、`work:settle`、`travel:walking`、`travel:driving`。
规则共同字段为 `rule_id`、`category_id`、`action`、`operator`、`resource_id`、`unit`、`conservation`。
`conservation` 只取 `open` 或 `closed`，必须与资源定义一致。

| `operator` | 专属字段 |
| --- | --- |
| `purchase_transfer` | `from_role`、`to_role`；不接受 `price_slot` |
| `food_energy_source` | `energy_slot`、`duration_slot` |
| `durable_use` | 可选 `effect_slot` |
| `travel_fare` | `minimum_fen`、`fen_per_meter` |
| `work_hourly` | `rate_slot`、`maximum_duration_slot` |
| `work_piece` | `rate_slot` |
| `work_package` | `amount_slot` |
| `inventory_transform` | `input_category_id`、`output_category_id`、`input_quantity`、`output_quantity`、`inventory_unit` |

购买规则的 `from_role` 必须为 `buyer`，不允许声明另一付款人而仍从人物账户扣款。
`merchant` 是购买规则的收款角色，运行时绑定所选供给的 `seller_account_id`；不再从商品 `merchant_role` 或场景角色唯一性推断卖方。
购买规则编译为 `RuleEdge`，报价在结算时传入，商品不得编译静态购买 `TransferEdge`。
库存仍使用 `kind=level` 的资源定义，不增加第三种资源形态。
场景 `recipe_output_bindings` 为输出品类身份到输出对象身份的映射；没有配方的场景使用空对象。
库存配方按实际缩放数量检查库存，保留可表示的微量变化，不固定保留小数位，也不以库存容差放行不足输入。
正数量若下溢为零、超出有限数值范围或无法形成实际库存变化，以
`INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE` 拒绝，不扣加库存或推进配方活动；
受理拒绝仍正常记录 `action_rejected` 与 `action_result`。配方完成提交失败时，
库存、活动进度和该次足迹事务保持原值。
原始对象不得写 `edges`、公式或旧的 `object_kind` 字段。

目录和运行清单共享 `category_contract_version`、`category_contract_sha256`、
`exchange_rules_sha256`、`catalog_content_sha256`。前三者绑定契约版本及两个原始文件的字节；
最后一项绑定对象、资源、墓碑、契约、交换规则、可供性与规则文件，以及排除该摘要自身的规范化清单。
检查点与导出继续保存原始运行清单，字段的持久化责任见 [记录引擎](design/05-recording-engine.md)。

## 语料内容目录字段

| 字段 | 含义 |
| --- | --- |
| `item_id`, `kind` | 稳定内容标识与食品、节目、游戏、工作、邮件、商品、路线等类别 |
| `source` | 数据集、版本、原始记录标识与文件哈希 |
| `language`, `region`, `valid_time` | 语言、适用地区和数据时间 |
| `assets` | 原始文字、图像、音视频、任务附件等文件引用及哈希 |
| `segments` | 内容区间、媒体起止位置、顺序与是否具有可靠时间依据 |
| `attributes` | 营养、类别、技能要求等已核对属性及单位；原始语料可保留历史价格依据，当前成交报价属于门店供给 |
| `access` | 人物、地点、时间、持有权和前置事件等访问条件 |
| `evaluation_refs` | 参考答案或评价依据，仅验收端可用 |
| `usage_rights` | 可用于内部处理、再分发和训练的条件及核对结果 |

## LifeAgent 人格与记忆字段

### 人格与卡片字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `background_story` | str | 人物背景故事；正式人物的作息、消费与风险倾向写在各自的背景里并由卡片承载 |
| `PersonaCard` | 编译产物 | 由 `background_story` 确定性渲染出的结构化人物卡片 |
| 卡片长度 | - | 不超过 600 汉字的中文文本进入实际模型请求 |
| 删除字段 | - | `occupation`, `routine_preference`, `consumption_preference`, `risk_preference` 模板字段与 `entertainment_preferences` 随机浮点删除 |

| `PersonaCard` 字段 | 类型 | 说明 |
| --- | --- | --- |
| `identity` | `Identity` | 身份：`age` 取 16–100，`occupation` 不超过 20 字，`living_arrangement` 不超过 30 字 |
| `relationships` | `Relationship[]`，1–4 条 | 关系：`name_or_appellation` 不超过 12 字、`role` 不超过 12 字、`intimacy` 取 0–30 整数、`channels` 取 1–3 项非空文本 |
| `situation` | `Situation` | 当下处境：`busy_this_week`（本周在忙什么）与 `cares_about`（在意什么），各不超过 60 字 |
| `time_anchors` | `TimeAnchors` | 时间锚点：`wake`、`leave_home`、`lunch`、`dinner`、`bedtime` 五个时间窗口，按起点排列为起床 < 出门 < 午餐 < 晚餐 < 就寝；递增序列后的回绕小值构造时确定性 +24 归一为次日锚点（1.0→25.0，落在字段值里），归一后仍不单调的（同日乱序，如 8→7）拒绝 |
| `chronotype_midsleep` | 浮点，小时 | 时型（自由日睡眠中点），取 [0, 24)，吸附 0.25 小时格点 |
| `sleep_need_hours` | 浮点，小时 | 睡眠需要，取 7.0–9.0，吸附 0.25 小时格点，默认 7.5 |
| `habits` | `HabitSignature[]`，至多 5 条 | 习惯签名：`trigger` 不超过 30 字、`response` 不超过 40 字、`time_window` |
| `tastes` | `Tastes` | 口味：`content_genres` 至多 6 项、`food_preferences` 至多 6 项、`media` 至多 4 项，条目非空且去重；内容题材与内容条目 `genre[]` 确定性匹配得到 `taste_weights`，只供内部评分器与浏览排序使用 |
| `coping` | 文本 | 应对方式（压力时做什么），不超过 60 字 |

时间窗口（`TimeWindow`）取自午夜起的小时数，半开区间：`start_hour` 取 [0, 30)，`end_hour` 取 (0, 30]，两者吸附 0.5 小时格点，跨度不超过 12 小时。就寝窗口允许跨午夜：`end_hour` 可大于 24（如 23–25 表示到次日 01:00）；锚点回绕归一后 `start_hour` 亦可大于 24（25 表示次日 01:00），上限为次日 06:00 地平线（30 小时）。这些字段属于人物作息，不构成当日目标的时间计划。

编译产物（`CompiledPersona`）除卡片外还包含 `big_five`（大五五维各取 1–7 整数）、`world_dynamics`（大五到世界层动力学参数）、`source`（`background_story` 指纹、抽取模型、提示版本，以及三次一致性的运行次数与逐字段平均众数占比）、`schema_version` 与 `warnings`。抽取提示版本为 `card-v1`，卡片结构版本为 2。出现 `occupation`、`routine_preference`、`consumption_preference`、`risk_preference` 四个已删除模板字段的旧档案明确报错，不静默兼容。

| `CompiledPersona` 附属字段 | 字段面 |
| --- | --- |
| `schema_version` | 固定 2 |
| `big_five` | `openness`、`conscientiousness`、`extraversion`、`agreeableness`、`neuroticism`，各取 1–7 整数 |
| `source` | `background_story_sha256`（指纹，64 位小写十六进制）、`model`、`prompt_version`，以及 `consensus`（`runs` 不小于 1、`agreement` 取 0–1） |
| `warnings` | 文本数组，默认为空 |

| `world_dynamics` 参数 | 类型 | 说明 |
| --- | --- | --- |
| `mood_pulse_amplitude` | 浮点 [0, 1] | 情绪脉冲幅度 |
| `mood_halflife_minutes` | 浮点 [10, 180] | 情绪半衰期，分钟 |
| `social_need_growth_per_hour` | 浮点 (0, 0.5] | 社交需要每小时增长率 |
| `conversation_relief_per_hour` | 浮点 [0, 1] | 交谈每小时缓解量 |
| `diversity_weight` | 浮点 [0, 1] | 内部评分器多样性权重 |
| `invitation_acceptance` | 浮点 [0, 1] | 接受邀约倾向 |
| `reply_tendency` | 浮点 [0, 1] | 回复消息倾向 |
| `present_preference` | 浮点 [0, 1] | 内部评分器现时偏好 |

大五分数、九参数与任何权重不进入模型提示。

### 记忆字段

人物在决策中只在结束活动时写记忆：`conclude_activity.memories` 逐项对应 `task[]`（`memories[i]` 是关于
`task[i]` 的记忆），合计零至八条。此外只有就寝反思与阈值巩固。所有模型写入、反思及 memory_* 查询结果都是
人物自述，持久记录和结果固定 epistemic_status=subjective；系统登记的来源不赋予正文客观事实地位。

| 字段 | 类型及合同 |
| --- | --- |
| type | commitment、relationship、preference、goal、conflict、resolution、identity、routine、reflection |
| importance | 零至一浮点数 |
| keywords | 至多十二项文本关键词 |
| text | 非空、至多一千字符的人物语言，不包含八位以上十六进制身份 |
| source | 由系统按写入点固定为 `activity_conclusion`（结束活动）或 `reflection`（就寝反思），与 `memory_updated.data.trigger` 同一组取值；表示形成来源，不表示事实已核验，模型不填 |
| epistemic_status | 固定 subjective；由严格人物输入与持久运行时共同保证 |

记忆不带 `refs`，来源由系统登记：`conclude_activity` 的 `action_result.data.memory_sources` 为私有字段
`[{task, activity_id, session_id, verified_event_ids}]`，模型不填、不选任何身份。`conclude_activity` 登记后
结算被拒时，其记忆仍作为人物自述保留，来源标为该动作。

WorldMock 删除模型可写 event、observation 类型。主观自由文字不得进入已核验经历、
完成事实章节、客观指标或世界状态。memory_* 只查询主观 episodes，每条只返回 {type, text, time, epistemic_status}，
不含条目身份，并明确返回其性质；
近期实际经历来自本轮观察，不另建事实检索框架。

持久 episode 保留 id、agent_id、tick、tick_duration_sec、step_index、time、step_count、
merge_count。近二十四小时同类型 bigram Jaccard 不低于 0.6 时合并，更新计数与形成时刻，
不重复追加文本。记忆更新区分新增与合并，合法合并旧身份不是重复落库。
完整 MemoryUpdateEvidence 及 memory_updated 字段、主观快照和四个内容哈希由
[记录引擎设计](design/05-recording-engine.md#已核验经历与主观记忆)唯一维护。

主观巩固触发保持 importance 累计三、待巩固十条、入睡后的第一个边界三选一；结束活动写入的条目计入待巩固条数与
重要度累计，就寝反思的条目不计（由 WorldMock 记忆运行时自行判定，不改 AS2）。阈值巩固只把 {type, text, time} 交给模型。
主观归纳单独持久保存；模型不能直接重写完整 MEMORY.md。
WorldMock 每步确定性装配身份、已核验经历和人物自述三个区段；刷新视图不重置主观巩固计数。
当前提示的事实窗口只取当前观察，不再重复引用上一步 MEMORY.md 的旧事实窗口。

就寝反思至多五条，必须引用有效输入记忆（反思对输入记忆身份的引用属系统内部，不进人物决策提示），
type=reflection、importance=0.8、source=reflection、epistemic_status=subjective。这些新增身份进入同次更新证据。
空 memories 合法；无任何变化可以没有更新事件，实际摘要或反思变化必须记录。
写盘失败不报告成功。事件、哈希及来源截止的记录格式见
[记录引擎设计](design/05-recording-engine.md)，行为与恢复验收见
[一日行为规格](dev/SPEC-dayrun-behavior.md#已核验经历与人物自述)。

## LabelKit 输入结构与窗口编码

### 输入结构示例

下列为设计示例，不是已采集经历。

```json
{
  "sample_id": "string",
  "run_id": "string",
  "agent_id": "string",
  "source_event_ids": ["string"],
  "task": "action_sequence" | "intent" | "dreaming" | "rule_test",
  "evidence": {
    "window_start": "ISO8601",
    "window_end": "ISO8601",
    "boundary": "activity_completed" | "interrupted" | "time_cutoff",
    "events": [
      {
        "event_id": "string",
        "time": "ISO8601",
        "type": "EventType",
        "content": "string"
      }
    ]
  }
}
```

JSONL 实际每个对象一行；`input.text_field = "evidence"` 使用 LabelKit 已有的对象序列化能力，顶层来源字段配置透传。不使用 LabelKit 保留的 `_meta.event` 冒充其内部生成 stream。

### 窗口声明与字段字典编码

每个输入窗口声明起止、知识截止、活动身份与边界类型；上下文超限明确失败，不自动裁正文。
动作和意图窗口以同人物、同决策、动作前最后一次实际 `model_request` 的 `messages`、`tools` 和
模型可见请求参数作为 prompt 权威来源，并以原请求事件身份和 canonical SHA-256 绑定来源索引。
只提取实际发给生活人物模型的行动请求；工作评审、画像抽取等私有请求不得进入任何训练输入或目标。
窗口内其他事件只补实际可见事实与可引用来源，不得据结构化观察反推、替换或增加实际 prompt 中没有的
人物卡片、记忆或场景。动作窗口的 prompt 截止在目标动作前；意图窗口可以读取已发生的公开结果，
但目标事实仍与输入分开。观察的禁用键必须在字段级投影后才可进入窗口；私有响应不得混入。
`decision_prompt` 必须保留实际请求中的 assistant `reasoning_content`，因为工具调用后模型确实收到该字段；
窗口的其他事件和目标不得额外混入 `reasoning_content`。前置状态与 `events[0].data` 相同时只保存事件引用，
不复制整棵状态。
文字、动作和意图窗口按请求人物核对其 decision/action 组的可见来源；
同一不透明标识关联到其他人物时明确拒绝。

模型输入统一采用窗口内字段字典表示，普通窗口和真实重执行窗口经过同一编码入口。字典与表示版本始终显式存在，未共享的字段保留原值。仅同一窗口内重复出现的完整字段值可以共享；是否共享按 canonical JSON 的实际字节计算，只有重复原值的总字节大于全部指针与一份字典项的总字节时才选入，整体收益不足以抵消字典项与指针开销时清空字典、保留字段原值，不裁剪任何内容。

| 编码合同 | 规定 |
| --- | --- |
| 引用形式 | RFC 6901 JSON Pointer，在本记录内单跳取得完整字段值 |
| 字典项 | 保存字段名、完整 SHA-256 与原值 |
| 拒绝条件 | 外部或跨记录引用、字段不符、摘要不符、嵌套引用、未使用字典项 |
| 依赖 | `jsonpointer==3.0.0`，在根依赖中直接声明 |
| 身份 | 逻辑 `window_id` 绑定解码后的完整输入；LabelKit 记录身份绑定实际读取的编码行，来源索引另存模型输入摘要 |
| 隔离 | 未来目标及其他人物、窗口的数据不得成为共享来源 |

## LabelKit 训练记录与交付格式

### 交付格式

| 产物 | 最小字段 |
| --- | --- |
| 动作训练记录 | `task`, `input` (允许看到的历史), `target` (下一动作及参数), `provenance` (来源引用), `terminal` / `truncated` |
| 意图训练记录 | `input` (允许看到的足迹), `target` (意图标签), `evidence_events`, `annotation_source`, `ambiguity_state` |
| dreaming 训练记录 | `input` (截止时刻的经历), `target` (归纳或反思目标), `hypothesis`, `branch_source` |
| 规则测试夹具 | `initial_state`, `events` (有序输入), `expected` (预期命中或不命中), `rule_version`, `trigger_time`, `state_assertions`, `provenance` |
| 交付清单 | `input_hash`, `output_hash`, `schema_version`, `rule_version`, `model_version`, `count`, `rejection_reasons`, `source_groups`, `loader_result` |

标注 Schema 的完整候选只包含 `target`；`task`、`input`、`provenance` 和 `target.facts` 由 WorldMock 在语义评审通过后一次性组装，评审前不复制这些字段，也不向模型暴露原始真值。动作目标保留在原始行的独立 `target` 中，不能发送给模型充当动作前的历史。

### 数据集字段与划分

训练正式记录使用 `task`、`input`、`target`、`provenance`，其中 `provenance` 保存来源组、事件、运行和模型身份；目标加载器在保持这些事实字段的同时输出明确的 `prompt` / `completion` 消息结构。规则夹具使用 `initial_state`、`events`、`expected`、`provenance`，表达式、预期和输入分别冻结。

`input`、`target`、`provenance` 三列使用 Datasets 原生 `Json(decode=False)` 保存 JSON 文本，以标准 `json.loads` 逐记录核对原值，保留缺失与 `null` 的区别和浮点数；`prompt`、`completion` 使用已核验的 JSON 文本。所有来源连通组只能进入一个 split；只有一个连通组时允许全部进入同一 split。

### 反事实与重演字段

`deliver --replay-export` 接受实际完成的反事实分支导出。父检查点之前的事件必须逐字相同，分支清单、一次真实干预的摘要和父检查点内的可见来源必须匹配。余额条件干预使用 `set_balance_fen`，应用于子分支首次决策之前，随后由人物自主选择。

| 字段 | 取值与含义 |
| --- | --- |
| `world_kind` | `observed` 表示普通反事实输入，其事实只属于原世界；`resimulated` 只用于实际执行并认证过的环境分支 |
| `environment_replay` | 没有环境重演的普通反事实材料为 `null`，不能声称已经执行或观察到想象结果 |
| dreaming 记录绑定 | 声明假设条件与 `resimulated` 世界，绑定父运行、父检查点、干预事件、实际结果事件和最终检查点 |
| dreaming 材料字段 | 来源事件、知识截止时刻、生成模型与规则版本；任务可为经验归纳、反思或反事实推演 |

规则扰动单独使用 LabelKit 现有 sequence generation，覆盖缺失、乱序和超时；消费前验证其成功 manifest、运行身份、main/stream 行数与哈希，模型不生成夹具的预期结论。

### 运行报告

足迹导出的 `quality.json` 字段与核验规则统一见[记录引擎的质量报告](design/05-recording-engine.md#质量报告)。

最终交付清单绑定全部加工输入、输出、评审轨迹、日志、规则及实际 DatasetDict 文件；DatasetDict 必须经 `save_to_disk` 后重新 `load_from_disk` 验证行守恒。

## LabelKit 语义候选与标签值

| 字段 | 上限 |
| --- | --- |
| `claims` | 最多 32 条 |
| claim 正文 | 最多 512 字符 |
| claim 引用 | 最多 64 个引用，单个引用标识最多 64 字符 |
| `intent_candidates` | 最多 8 个且不重复 |
| `uncertainty` | 最多 1024 字符 |

三十二条是上限而非目标数量。数组超过 `maxItems` 时，错误包含字段路径、允许的最大数量和实际数量，候选全文只放在返工提示的原始输出中。每条只引用直接支持正文所需的真实事件，不为了完整保存原始材料穷举全部引用身份。

| 标签 | 取值与含义 |
| --- | --- |
| `fact` | 正文中的每个独立断言都由该 claim 所列来源直接支持；来源已明确给出的类别、持续性与因果关系仍可标为 `fact` |
| `inference` | 具有可追溯依据，明确区别于已证实结果 |
| `hypothesis` | 假设条件为未执行的动作与可见触发情境（含触发时刻的可见数值）；可能结果用推测措辞陈述且不得标为 `fact`，其 `evidence_event_ids` 只须支持假设条件 |
| `intent` | 可见证据不能支持具体意图时取 `unknown`；`basis=uncertain` 与 `intent=unknown` 是合法组合 |
| `uncertainty` | 只保留「假设结果未在观察世界发生、无法验证」的一般性声明，不复述 hypothesis claim 已回答的具体反事实问题 |

身份字段视为不透明标识，除非可见输入明确说明其组成语义，否则不得从数字或片段解释步骤、时间或结果。命名布尔字段的 `false` 只否定该字段表达的命题，不能据此推出未提供的上位类别或其反面。

## LabelKit 调用配置

动作、意图和 dreaming 使用 LabelKit 普通 process，固定输入文件且关闭去重、重新语义分段和分类。

| 配置项 | 值 |
| --- | --- |
| 生成与独立评审 `context_window` | `1000000` |
| 语义候选输出 | `thinking=disabled`，`max_tokens=131072` |
| 独立评审输出 | `thinking=enabled`，`max_tokens=65536`，使用单独 judge 角色 |
| 语义返工 | `verify.policy=repair`，最多三轮，每轮返工后重新独立评审 |
| 传输重试 | `max_retries=0`，每次实际 HTTP attempt 只发送一次请求 |
| 请求截止 | `timeout_s` 为总墙钟截止，持续收到响应字节不延长截止；同时保留 HTTPX 连接、读取、写入和连接池超时 |
| 失败归类 | 总截止归类为明确的 provider 请求失败 |
| 响应校验 | 2xx 响应先通过 JSON 顶层对象校验，再调用供应商字段解析器；非对象响应记录 `malformed provider response` 与类型，不记录响应原文或敏感内容，也不合成空结果 |

## 人物卡片覆盖率产出

人物卡片覆盖率校验把人物卡片的习惯反应与口味条目逐个映射到世界里实际可执行的目标或条目，防止卡片写了世界里没有的东西。

| 项 | 合同 |
| --- | --- |
| 产出文件 | `persona_coverage.json`，默认写在场景文件同目录，可由参数改写路径 |
| 触发命令 | `worldmock validate` 与 `worldmock profile validate` 都执行该校验 |
| 可执行比例阈值 | 0.8；任一人物低于阈值即校验失败并列出缺失项，此时不写出文件 |
| 文件结构版本 | 1 |
| 匹配规则 | 口味条目与目标品类集精确相等；习惯反应与目标关键词互为子串 |

下列为产出示例。文件只在全部人物通过时写出，因此 `passed` 为真、`missing` 为空。

```json
{
  "schema_version": 1,
  "scenario_id": "string",
  "threshold": 0.8,
  "agents": [
    {
      "agent_id": 1,
      "schema_version": 1,
      "total_items": 12,
      "covered_items": 12,
      "executable_ratio": 1.0,
      "passed": true,
      "missing": []
    }
  ]
}
```

| 逐人物报告字段 | 说明 |
| --- | --- |
| `agent_id` | 人物编号，与逐人物报告的其他字段平级展开 |
| `total_items` | 参与校验的条目数，即习惯反应与三类口味条目之和 |
| `covered_items` | 命中世界可执行目标的条目数 |
| `executable_ratio` | 可执行比例，保留四位小数；无条目时为 1.0 |
| `passed` | 比例是否达到阈值 |
| `missing` | 未命中的条目，每条含 `kind` 与 `value`；失败时随错误消息与返回值列出 |

`missing[].kind` 取 `habit_response`（习惯反应）、`content_genre`（内容题材）、`food_preference`（饮食喜好）或 `media`（媒介）。目标集由场景、目录与语料确定性派生：习惯反应关键词取生活活动目录条目名与场景地点名，内容题材取内容语料条目的题材闭集，饮食喜好取正餐语料名与目录中可进食商品名，媒介取世界实际可观看或可游玩的品类词。

## CLI 命令接口

对外命令统一使用 `worldmock`。命令失败返回非零状态并记录英文错误，不能创建看似成功的 manifest。

| 命令 | 参数 | 行为 |
| --- | --- | --- |
| `prepare-corpora` | - | 获取明确来源子集、核对文件、形成规范化目录与来源清单 |
| `validate` | `scenario_path` | 校验场景、语料、依赖、规则和目标数据合同，不启动人物 |
| `run` | `scenario_path`, `output_dir` | 从明确场景新建运行目录，经 AS2 执行到场景终点；已存在的运行目录不能被覆盖 |
| `resume` | `parent_checkpoint`, `output_dir` | 指定父运行和完整检查点，在新的运行目录恢复；不允许隐式新建 |
| `replay` | `parent_checkpoint`, `intervention`, `output_dir` | 指定父运行、共同检查点及一次余额条件干预，在新反事实分支实际执行 |
| `status` | `run_dir` | 只读显示已确认模拟时间、阶段、人物活动与调用统计 |
| `export` | `run_dir`, `cutoff_event`, `output_dir` | 固定截止事件和活动边界，形成完整证据 JSONL、来源索引与哈希 |
| `deliver` | `export_dir`, `labelkit_project`, `output_dir` | 执行 LabelKit、独立校验、训练加载与规则执行，最后发布成功清单 |
| `evaluate` | `baseline_dir`, `experiment_dir`, `output_dir` | 核对两套数据身份与可比范围，产出机器报告、图表和中文结论 |
| `profile compile` | `text`, `runs`, `out` | 编译指定文本的人物卡片、大五与世界层动力学，输出卡片、渲染文本与溯源 |
| `profile check` | `text`, `runs` | 重复编译并断言卡片一致性与大五众数稳定 |
| `profile validate` | `scenario_path`, `coverage_out` | 校验场景人物卡片的世界可执行覆盖并写 `persona_coverage.json` |
| `category check` | `--catalog`，可选 `--queries`、`--run-dir` | 严格加载目录，报告三态配对查询与真实规则触达次数 |
| `category refine` | `--catalog`、`--candidate`、`--parent-category`，可选 `--run-dir` | 列出对象归属建议、规则与配对变化；不改旧目录 |
| `category publish` | `--catalog`、`--candidate`、`--output`、`--assignments`、可重复的 `--scenario` | 检查完整候选与场景，将递增版本原子发布到新路径 |

`category check --queries` 读取 JSON 数组，每项恰含 `actor_category_id`、`target_category_id`、`action`。
`category publish --assignments` 读取对象身份到品类身份的 JSON 映射；没有改挂时显式传空对象。
维护命令成功向标准输出写一个 JSON 对象，发布命令至少要求一份真实场景。

### 运行状态枚举

运行生命周期由模拟引擎的运行编排维护；记录引擎只读呈现已确认的状态。

```python
RunStatus = Literal[
    "created",      # 已创建
    "running",      # 运行中
    "completed",    # 完成
    "failed",       # 失败
    "interrupted"   # 中断
]

RunPhase = Literal[
    "deciding",     # 决策中
    "resolving",    # 结算中
    "advancing",    # 推进中
    "checkpointing",# 检查点中
    "stopped"       # 已停止
]
```

运行状态使用 `created`、`running`、`completed`、`failed`、`interrupted`；运行阶段使用 `deciding`、`resolving`、`advancing`、`checkpointing`、`stopped`，正常睡眠属于活动状态。不得把已提交请求序号作为已完成活动或支付的证明。

### 运行诊断输出

运行宿主与各模块负责自身日志埋点，公共输出约定如下。

持久足迹是审计事实；运行日志帮助定位问题，两者不能互相充当完成证据。

| 输出 | 当前格式与用途 |
| --- | --- |
| 标准错误流 | Python 标准 logging 输出英文运行错误及诊断信息；日志级别用于排查问题 |
| `logs/ops-YYYYMMDD.jsonl` | 按场景时区的模拟日追加操作日志，字段为 `at`、`sim_time`、`component`、`level`、`code`、`detail` 及可用关联键 |
| `status.json` | 运行边界写入的 `status`、`phase`、`error_type`，不是世界状态快照 |
| `worldmock status` | 从已提交数据库汇总 `run_id`、`state_version`、`current_time`、`active_activities`、`event_counts`、`recent_checkpoints`、`runtime`、`token_stats`、`observed_model_usage`、`people` 与 `wall_time` |

操作日志的 `at` 为程序 UTC 时刻；模拟时间戳按场景时区显示，datetime 输入保留其自身时区。
现有组件常量为 `engine`、`agent_runtime`、`memory`、`review`、`commerce`、`travel`、`checkpoint`、`runtime`；
级别常量为 `info`、`warn`、`error`。当前 `OpsLogger` 不在运行时验证组件或事件码闭集，不能把示例码当成强制 Schema。
实际埋点包括 `DECISION_ROUND_COMMITTED`、`ACTION_ACCEPTED`、`ACTION_REJECTED`、`FUNDS_CHANGED`、
`MODEL_EXCHANGE`、`MEMORY_UPDATED` 与 `CHECKPOINT_COMMITTED`，它们和持久事件类型是不同字段。

操作日志逐条 flush，未作逐条 fsync；写入或关闭失败只报告到 stderr，属于当前诊断输出行为。
必要模型留证、记忆或数据库写入失败仍按[模拟引擎的失败处理](design/04-simulation-engine.md#失败处理)停止运行，不因诊断日志的处理方式而放宽。

## 版本表

以下仅为历史修订记录；旧时段合同已由当日目标规格替代，不构成现行接口。

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| 2026-09-18 | 2026-09-18 | 建立为接口唯一权威：涵盖 LifeEnv 状态与工具、动作状态与动作结果、数字足迹事件与存储、共同检查点与恢复、语料内容目录、LifeAgent 人格与记忆字段、LabelKit 输入窗口与交付字段、语义候选上限与标签值、调用配置和命令行接口 |
| 2026-09-18-revision-0 | 2026-09-18 | 按代码补齐接口面：修正 `plan_day` 与 `revise_day_plan` 的段字段并新增日程骨架请求节；补 `PersonaCard` 字段名、类型与取值；补记忆条数上限、字段面、类型闭集、合并阈值、巩固触发与反思；新增观察投影字段节、人物决策提示与生成参数节、人物卡片覆盖率产出节；命令行补画像编译三个子命令 |
| 2026-09-18-revision-1 | 2026-09-18 | 补入社交接口节（场景联系人字段与回复策略闭集、消息种类与字数上限、交谈与通话时长与缓解速率、联系人回复事件与拒绝错误码）、领域枚举节、请求模型的闭集与字面边界节、编译产物附属字段面；修正 `revise_day_plan` 的段数下限表述与存储调用签名（`commit`、`checkpoint`、`restore` 按源码为准），并补 `AssetStore.put_json`、`FootprintStore.read_agent_events` |
| 2026-09-18-revision-2 | 2026-09-18 | 按源码修正事件字段表的来源与可见性字段名、闭集与默认值，补 `artifact_refs` 字段组与存储分配字段的类型边界；新增决策节律增量字段节并登记唤醒原因冻结闭集；覆盖率产出节以「人物卡片覆盖率」统一概念词 |
| 2026-09-18-revision-3 | 2026-09-18 | 事件字段节补观察记录的字段形态合同：`observation` 事件的 `data` 即结构化观察投影本身，人物决策提示里的观察记录以 `turn`、`action`、`ok` 与 `data` 四个键承载，不另写入 JSON 字符串副本 |
| 2026-09-20-recording | 2026-09-20 | 记录引擎的事件、存储、检查点、运行状态与足迹质量报告归入单一设计文档，本文件保留接口导航 |
| 2026-09-20-boundaries | 2026-09-20 | 运行状态与公共日志合同由运行宿主管理，记录引擎仅提供已保存事实的查询与完整性诊断 |
| `2026-09-20-decision-context` | 2026-09-20 | 对齐决策上下文特性规格：统一决策自述命名，明确登记与结算结果的通道、场景信息和预算边界 |
| `2026-09-20-interaction-menu` | 2026-09-20 | 冻结地点类与人物交互菜单声明、观察证据、模型与宿主共同门控及环境拒绝合同 |
| 2026-09-20-category | 2026-09-20 | 冻结品类、对象增量、来源审核、闭集交换算子、库存输出绑定、维护命令与内容身份字段 |
| 2026-09-20-max-tokens | 2026-09-20 | 决策自述生成参数 `max_tokens` 400→2000：OpenRouter `deepseek-v4.1-flash` 每响应必带 reasoning 耗 token，400 预算下 content 必空（冒烟实证） |
| 2026-09-20-statement-budget | 2026-09-20 | 决策自述生成参数 `max_tokens` 2000→8000：抽取阶段实测 reasoning 峰值 4925-6916，2000 预算下复杂决策 content 被 reasoning 烧穿（一日模拟 07:30 中断实证）；联系人回复代码常量同步 8000（合同无独立条目） |
| 2026-09-20-reasoning-cap | 2026-09-20 | 三个有界生成调用点加 reasoning 上限（决策自述 1500、联系人回复 1500、画像抽取 3000，OpenRouter 扩展参数 `reasoning.max_tokens`）：temp 1.3 偶发 reasoning 思考螺旋烧穿全部 `max_tokens`（`finish=length`、content 为空，两次一日模拟 07:30 实测）；上限实测被尊重且只截断思考不伤内容；`reasoning.effort` 实测破坏输出，禁用。生成参数表新增 `reasoning` 上限列与联系人回复、画像抽取两行 |
| 2026-09-20-temp-1-0 | 2026-09-20 | 决策自述生成参数 `temperature` 1.3→1.0：模型更换后旧温度偶发采样脱轨（reasoning 螺旋烧穿/多语言乱码/超长输出，均发于一日模拟 07:30 最富上下文轮次，累计 12 发 5 发病态；乱码样本 reasoning 3587 超 1500 上限，证实 `reasoning.max_tokens` 为软限制、正文中「实测被尊重」表述同步修正）；同请求 temp 1.0 与 0.8 各 4 发全部正常（8/8）。联系人回复请求不带 `temperature`（表载「不发送」），无变更 |
| 2026-09-20-active-activity | 2026-09-20 | 观察投影字段节新增活动控制行合同：`active_activity` 控制行冻结为 7 键闭集（含 `status`，槽位不变量下恒 `"active"`），`paused_activities` 为完整 dump 加恢复可行性三键。迁移到极简控制行时丢 `status` 键曾致 LifeAgent 控制合同构建崩溃（runs/deepseek-day-openrouter 一日模拟 08:00 轮实证）；字段闭集防两侧再漂移；此历史合同已由上文八键 active_activities 集合及世界版本 5 取代 |
| `2026-09-20-killpoint-conversions` | 2026-09-20 | 模型可控输入的引擎严格路径全部转为业务拒绝：`note` 上限 300 字符进日程骨架字段表（请求模型与持久层同限）；社交拒绝错误码表补 `MESSAGE_TEXT_TOO_LONG` 与 `CHAT_PARTNER_NOT_AVAILABLE`（交谈共同在场/可交谈校验）；结算拒绝错误码表补通用兜底 `ACTION_REQUEST_INVALID`（引擎边界请求再验证）。此前四条路径以裸异常穿透 `commit_decision_round` 杀死整个 run（unit-15 审计 E1-E3 探针复现），agent 侧记忆工具幻觉 id 改为可重试失败反馈（E4） |
| `2026-09-20-plan-epoch-disclosure` | 2026-09-20 | 日程骨架单位披露合同：schema 一直要求 epoch 秒但提示只给 ISO 本地时间，模型猜成"当日分钟数"烧掉 33/83≈40% 模型调用直至 ReAct 预算耗尽（unit-11 审计 §6）。plan_day/revise_day_plan 工具描述与 `PlanSegmentInput.start/end` 字段描述写明绝对 Unix epoch 秒（UTC）；scene/facts 在 `current_time` 旁披露 `current_epoch` 与 `day_start_epoch`，时间句内并排渲染。工具 Schema 指纹随之重钉（`life_prompt_sha256` 输入不含工具描述，不变） |
| `2026-09-20-day-end-disclosure` | 2026-09-20 | 日程骨架披露第三个锚点 `day_end_epoch`（当日结束，即场景时区次日 00:00）：与 `day_end_seconds`/DayPlan 校验拒绝边界同源（段 `end` 越过即 `PLAN_SEGMENTS_INVALID`）。此前只披露 `current_epoch`/`day_start_epoch`，模型规划末段只能猜当日结束边界，猜过线即拒且拒绝消息不含边界值，run10 一日模拟 agent 2 连续 10 次被拒触发 fail-closed 死亡（unit-18）。scene/facts 键表、时间句、`PlanSegmentInput.end` 字段描述与 plan_day/revise_day_plan 工具描述硬规则同步；工具 Schema 指纹随之重钉（`life_prompt_sha256` 输入不含工具描述，不变） |
| `2026-09-20-bedtime-epoch-disclosure` | 2026-09-20 | 日程骨架披露第四个锚点 `bedtime_epoch`（当日就寝锚点，人物特有）：与 `bedtime_anchor`/DayPlan 校验就寝覆盖边界同源（最末段 `end` 低于它即 `PLAN_SEGMENTS_INVALID` "plan does not cover until bedtime"，修订同校验）。`day_end_epoch` 只救跨午夜钳制型；就寝不跨午夜的人物（陈曦型 bedtime 23.5 < day_end）在"最后一段规划到就寝"路径仍要猜就寝时刻（unit-18 残余风险）。per-agent 注入经 `AgentConfig.bedtime_window` 末端（与 `engine._plan_day` 同一取值链）；scene/facts 键表、时间句、`PlanSegmentInput.end` 字段描述与 plan_day/revise_day_plan 工具描述硬规则同步；工具 Schema 指纹随之重钉（`life_prompt_sha256` 输入不含工具描述，不变） |
| `2026-09-21-daily-goals` | 2026-09-21 | 补登 `2d97d3d`：「日程骨架请求」改为「当日目标请求」，`plan_day`、`revise_day_plan` 改为 `{goals: [{intent_kind, goal}]}`（零至八条有序目标），`DayPlan` 为 `{day_end, goals}` 并附登记与推进证据；错误码改为 `PLAN_GOALS_INVALID`、`PLAN_GOAL_ALREADY_PROGRESSED`，删段式错误码；新增「当日目标的配置与质量输出」；scene 与 facts 删日首、日末、就寝换算锚点，`day_plan` 改为目标清单；版本表加历史说明句 |
| `2026-09-21-daily-goals-merge` | 2026-09-21 | 补登 `db7d629`：合并时改写「人格与卡片字段」的 `TimeWindow` 段，删日程骨架就寝锚点句，改为作息字段不构成当日目标的时间计划 |
| `2026-09-21-dayrun-resilience` | 2026-09-21 | 补登 `fa150fb`：活动控制行改为八键（加 `data`，游戏保留真实 `game_session_id`）；「下推返回体」补浏览游标解码校验，无效游标只拒绝当前动作；「模型身份」加 `transport_max_retries`（3）与 `max_react_turns`（16），写明仿真调度器的瞬时传输重试（退避 2、4、8 秒，与 `max_retries` 分开计数） |
| `2026-09-21-concurrent-activities` | 2026-09-21 | 补登 `d998a31`：新增组合启动工具 `start_parallel_activities` 及其菜单与结算检查；`RESOURCE_CONFLICT` 改为新建或恢复活动的个人独占资源与本人 ACTIVE 活动冲突，或组合内部冲突；世界状态结构版本 4 改 5；scene 改 `current_activities` 并增 `local_food_summary`；活动控制行改为 `active_activities` 集合，补暂停活动与 `occupied_resources` 说明；新增「已核验经历与当地餐食」；「记忆字段」改为人物自述口径（`epistemic_status=subjective`，删模型可写的 `event`、`observation` 类型，增 `reflection`）；`2026-09-20-active-activity` 行注明已被取代 |
| `2026-09-21-world-schema-5` | 2026-09-21 | 补登 `aa22dc8`：「持久化当下事实」的世界结构版本 4 改 5，与领域枚举一致 |
| `2026-09-23-agent-interaction` | 2026-09-23 | 补登 `3880738`：删 `change_playback_speed`、`game_action` 工具行及其菜单保护项 |
| `2026-09-24-activity-execution` | 2026-09-24 | 补登 `2913546`：工具面收敛为九个按可读名称填写的工具（`start_activity(targets, actions)`、`recall_task_summary`、`report_status`、`pause_activity`、`conclude_activity`、`plan_day`、`revise_day_plan`、`submit_work`、`send_message`），私有 `interaction_bindings` 绑定内部身份；交互菜单改为 `allowed_actions`（闭集 `START_ACTIVITY_ACTIONS`，保护 `wait`、`sleep`）；请求检查、闭集与字面边界按新工具重写，工作任务标题必填；发现与购买的筛选值改为私有绑定；scene 与 facts 删 `current_epoch` 与内部身份（facts 删 `location_id`），facts 增 `interactive_objects`、`progress`，取代 `recent_action_results`；提示预算改为整次请求计数与 85% 进度浓缩；禁止进入提示的字段加 `interaction_bindings`；LabelKit 动作与意图窗口以实际 `model_request` 为 prompt 权威来源 |
| `2026-09-24-scenario-validation` | 2026-09-24 | 补登 `eee89fc`：交互对象的 `preconditions_met` 按执行器的资源声明检查进行中活动，`RESOURCE_CONFLICT` 后下一次决策排到最早活动结束或下一反馈间隔；生成参数删 `reasoning` 上限列与说明，行动阶段 `max_tokens` 为 8000，画像抽取关闭思考；模型身份 `base_url` 改 `https://api.deepseek.com`、`requested_model` 改 `deepseek-flash` |
| `2026-09-24-issue-1` | 2026-09-24 | 补登 `778beca`：`submit_work` 在有效工作量达到合同要求后开放；前置条件不满足或资源被占用的动作不列入交互对象；LabelKit `decision_prompt` 保留实际请求中的 assistant `reasoning_content` |
| `2026-09-24-time-and-energy` | 2026-09-24 | [时间与能量规格](dev/SPEC-time-and-energy.md)冻结，按其冻结作业改写：`available_energy` 非负，结算以 0 截断，不阻止任何活动；有效菜单删能量与日首计划门控，条件未满足的动作照常列出并附说明，唯一的状态性收窄是同刻上限的受限轮；当日计划由零点计划生成，`plan_day` 不是人物工具，只有 `remind_time` 触发定时决策；浏览是活动，删「下一秒」回灌，`discovery_result` 当前无触发来源；交谈、通话占用 `voice`、`ears`，时长由 `activity_feedback_seconds` 派生后截取，补交谈接受与对方同秒结束（`partner_ended`）；交互对象不剪裁，业务拒绝后在同一秒再决策，删按最早活动结束排期；已核验经历只以句子给模型，饥饿为「饿」及以上时 `local_food_summary` 为吃饭途径句；进度浓缩单位改为较旧会话中已收尾的活动。补登上列实施计划 C38 所列九个提交。工具面、错误码、枚举、结构版本与场景参数随各实施阶段落地，届时另追加版本行 |
| `2026-09-25-time-and-energy-stage-0` | 2026-09-25 | 时间与能量规格阶段 0（绿色基线，issue #1）：交互对象层可以为空（「场景段落与事实清单」）；世界状态结构版本改为 7（「领域枚举」「冻结地图与场景引用」「持久化当下事实」），旧检查点以版本不符拒绝；新增「活动受理拒绝码」并登记 `WORK_TASK_IN_PROGRESS`；`submit_work` 对全部可见未付款任务开放 |
| `2026-09-25-time-and-energy-stage-1` | 2026-09-25 | 时间与能量规格阶段 1：「状态归属」的人物活动加同刻计数 `same_moment_rounds` 与受限轮阶段 `same_moment_stage`（六个取值）；「当日目标的配置与质量输出」改为进行中的等待不算在手承诺，只在等待且没有其他在手承诺时用空闲间隔，并增「决策节律场景参数」：`max_same_moment_decisions`（缺省 3，时钟推进即归零），补登 `activity_feedback_seconds`（60–86400，等待时长取它）与 `progress_report_interval_seconds`（缺省 1800）；「活动受理拒绝码」增 `SAME_MOMENT_ACTION_RESTRICTED`；「冻结地图与场景引用」写明场景结构保持 3，含旧键的场景按未知字段拒绝；「动作状态与动作结果」写明达到同刻上限后按受限轮阶段收窄世界请求工具与名称枚举（记忆查询照常），越界请求以 `SAME_MOMENT_ACTION_RESTRICTED` 拒绝；「场景段落与事实清单」的 `scene` 增 `same_moment_note`（受限轮的一句说明，不写次数）；「联系人回复」增送达时刻：回复登记为 `CONTACT_REPLY_DELAY_SECONDS`（缺省 300 秒）后到期的预排送达，到期写入收件箱并以 `message_arrival` 唤醒、睡眠中过滤，「回复完成」注明模型回复证据在生成时写入；「领域枚举」的 `ScheduledKind` 增 `message_arrival` |
| `2026-09-25-time-and-energy-stage-2` | 2026-09-25 | 时间与能量规格阶段 2（身体与能量）：`start_activity` 的结算检查中能量不参与（「请求字段与结算检查」）；`local_food_summary` 的含义指向「已核验经历与当地餐食」；`facts.products` 的食品带 `energy`，配送以人读送达时刻 `delivery_eta` 取代原始秒数，`facts.routes` 带 `effort`、`feasible` 只表示可达，新增 `facts.inventory` 行（他处持有物注明只能在当地使用），`drive_summary` 行归位（「场景段落与事实清单」）；暂停控制行删 `minimum_energy_cost_kcal`、`resume_energy_feasible`（「活动控制行」）；排除原因码删 `ELIGIBILITY_ENERGY_INSUFFICIENT`（「排除原因码」）；禁止进入提示的字段删 `energy_feasibility`，`routes` 条目加费力，事件级工具支持行删能量可行性（「禁止进入提示的字段」） |
| `2026-09-25-time-and-energy-stage-3` | 2026-09-25 | 时间与能量规格阶段 3（活动、会话与记忆）：「状态归属」加活动名额（常量 2）与「地点会话」一行（`sessions` 由环境随出发与到达开始和结束）；「人物工具与管理接口」的回看、播报、暂停与结束改为点名活动，`conclude_activity` 加 `memories` 并可结束进行中、暂停或待收尾的活动，工具资格改称活动控制工具；「请求字段与结算检查」与「请求模型的闭集与字面边界」的名称指向活动（回看可取任务、当日目标、活动或会话名），加名额检查与 `memories` 行；「活动受理拒绝码」登记 `SESSION_LIMIT_EXCEEDED`（名额；原因按占满与只剩一个名额两种说法点名占名额的活动）、`ACTIVITY_PRECONDITION_UNMET`（出发前与别处恢复）、`ACTIVITY_CONCLUSION_REQUIRED`、`ACTIVITY_PAUSE_UNAVAILABLE`，形状错误报 `ACTION_REQUEST_INVALID`；「动作状态与动作结果」取消生活决策的 `finish`，一轮决策恰好一个世界请求、登记即结束，只读问答保留只读 `finish`；「领域枚举」登记 `boundary_type`、`interruption_reason`（加 `route_ended`）与会话的种类和状态；scene 加 `just_ended`、`current_activities` 只列进行中活动，facts 的 `progress` 按会话分组列活动行、增 `experiences` 句子，结果 `details` 加 `recall`；「活动控制行」另列待收尾一组 `pending_conclusions`；「禁止进入提示的字段」加内部身份键并放行进度层活动行；请求组成、工具返回通道、回合预算与模型身份按去掉 `finish` 改写，记忆查询不返回条目身份；「记忆字段」改为经 `conclude_activity.memories` 写入、来源由系统登记（删 `refs`），巩固只计结束活动写入的条目 |
| `2026-09-25-time-and-energy-stage-5` | 2026-09-25 | 时间与能量规格阶段 5（零点计划、计划提醒与常驻提示）：「人物工具与管理接口」删 `plan_day` 行，`revise_day_plan` 的条目加 `remind_time`；「交互菜单声明与有效菜单」的模型工具名改为八个；「当日目标请求」改为零点后台任务 `plan_day` 生成当日计划、人物以 `revise_day_plan` 修订，条目为三个字段（增必填可空的 `remind_time`：场景本地 `HH:MM`，换算后须晚于生成或修订时刻、早于次日零点），同一人物同一本地日只生成一次（幂等键 `plan_submitted_dates`，恢复与重放不重复），修订时三个字段完全相同的条目保留登记与提醒状态、只改提醒的换成新提醒、替换或删除的取消旧提醒，`PlanGoal` 加 `remind_at`、`reminded_at`，零点计划的登记写 `environment_event`（`data.kind="day_plan_generated"`，`visibility=system`，带 `data.day_plan` 与 `data.source_event_ids`）；错误表删 `PLAN_ALREADY_SUBMITTED`，增 `PLAN_REMIND_TIME_INVALID`，`remind_time` 格式错误归 `ACTION_REQUEST_INVALID`；「请求字段与结算检查」与「请求模型的闭集与字面边界」删 `plan_day`，加 `remind_time` 校验；「领域枚举」的 `boundary_type`、`interruption_reason` 加 `plan_reminder`；scene 与 facts 的 `day_plan` 目标行带「提醒 HH:MM」，facts 的 `day_plan` 行按实际投影改写，`progress.alarms` 加提醒触发句；「请求组成」的两阶段 system 加固定一节「怎样过一天」并登记全文（只读问答不含），新增零点计划的请求组成（JSON 输入键、回顾时段、容量裁剪、中文模板与 `submit_day_plan` 输出 Schema）；「生成参数」增零点计划行（`max_tokens` 8000，`tool_choice=required`，输出不合法时附回错误至多重试 3 次，与传输预算分开计数，不进清单阶段键），模型请求按 `data.purpose` 区分。复核补正：零点计划输入的 `人物` 是规格「人物卡片与场景的人物背景原文」的落地口径，人物卡片取场景声明的人物档案（名字、背景原文、健康习惯、社交风格与作息窗口），编译后的卡片只存于人物工作区，零点计划不读工作区；`收尾记录` 只列已受理的 `conclude_activity` 请求（被拒与重放的不列）；`DayPlan.day_end` 所说的场景本地零点按本地日期变更判定（夏令时在零点拨快的日子为 01:00） |
